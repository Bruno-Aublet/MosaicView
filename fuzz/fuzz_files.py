"""Campagne de fuzzing des fichiers abîmés ou piégés : images, archives
CBZ/EPUB, CBT, CB7, CBR, ComicInfo.xml et PDF passés par les vrais lecteurs
de MosaicView.

Lancement, depuis la racine du projet :
    python fuzz/fuzz_files.py [--secondes N] [--graine G] [--cibles images,zip,tar,7z,rar,xml,pdf]

Sans --graine, la graine est tirée au hasard : chaque lancement essaie des
fichiers différents. Elle est affichée au départ ; --graine la rejoue (même
suite d'entrées, numérotées). --secondes : durée par cible (60 par défaut).

Chaque cible tourne dans un process de travail séparé, sous une limite de
mémoire Windows qui porte sur le total du process et des 7z.exe, UnRAR.exe et
process PDF qu'il lance, réunis : 1 Go au-dessus de leur départ (mesuré une
fois le process prêt et, pour la cible pdf, ses process PDF démarrés). Une
entrée qui bloque plus de 20 s, plante le process (erreur native) ou fait
monter cet ensemble de plus de 600 Mo au-dessus du départ est signalée, son
fichier gardé, et la campagne reprend à l'entrée suivante. Tout se fait dans un dossier temporaire neuf, supprimé à
la fin (APPDATA y est redirigé) ; aucun fichier de l'utilisateur n'est lu ni
écrit. Rapport et entrées en cause : %TEMP%\\MosaicView_fuzz_files_<graine>\\.

Pas de Qt : entries.py et image_ops.py sont importés avec un utils.py de
remplacement (format_file_size seul), les lecteurs d'archive_loader.py sont
extraits par tests/ast_loader. Mêmes réglages de Pillow qu'au démarrage de
MosaicView.py (limite de pixels, formats restreints).

Contrôles, entrée par entrée, comme le fait le chargeur (LoadWorker) :
- images : create_entry ne lève jamais ; une page acceptée s'affiche
  (ensure_image_loaded) et se réenregistre (save_image_to_bytes) en une
  image lisible ;
- archives : detect_archive_type ne lève jamais ; liste des noms puis
  lecture de chaque page par le lecteur du format (create_page_entry) ; une
  page lue n'a jamais plus d'octets que sa taille annoncée, ni plus que
  MAX_MEMBER_SIZE ; aucun 7z.exe ni UnRAR.exe ne reste en vie après la
  lecture ;
- ComicInfo.xml : parse_comic_info_xml ne lève jamais ; un fichier abîmé
  est lu dans sa version réparée (_comic_tree), sans DTD ni entité ; la
  réécriture (_serialize_comic_xml) ne lève jamais, se relit toujours et
  garde chaque balise, attribut et texte lus ;
- PDF : le vrai moteur (_pdf_persistent_process, dans un process séparé
  comme dans l'application, lu par read_pdf_message) et PdfProbe : chaque
  page rendue ou signalée en erreur, jamais manquante en silence ; une page
  rendue est lisible ; jamais plus de 15 s sans message ; un plantage natif
  de MuPDF est vu comme un process arrêté.
Une exception que error_log classerait « inattendue » (écrite dans le
journal des erreurs de l'utilisateur) est comptée à part.
"""
import argparse
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types
import zlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_LIMIT_MB = 1024
MEMORY_ALERT_MB = 600
HANG_SECONDS = 20
PDF_HANG_SECONDS = 50     # une entrée PDF attend jusqu'à 15 s le moteur, puis PdfProbe
PDF_SILENCE_SECONDS = 15
TARGETS = ("images", "zip", "tar", "7z", "rar", "xml", "pdf", "chaine")


# --- process de travail : limite de mémoire ------------------------------------

def _job_api():
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Io(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class Accounting(ctypes.Structure):
        _fields_ = [("a", ctypes.c_longlong), ("b", ctypes.c_longlong), ("c", ctypes.c_longlong),
                    ("d", ctypes.c_longlong), ("TotalPageFaultCount", wintypes.DWORD),
                    ("TotalProcesses", wintypes.DWORD), ("ActiveProcesses", wintypes.DWORD),
                    ("TotalTerminatedProcesses", wintypes.DWORD)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.GetCurrentProcess.argtypes = ()
    k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    k32.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                              ctypes.c_void_p)
    return ctypes, k32, Extended, Accounting


class MemoryJob:
    """Le process courant (et ses enfants) dans un objet job de Windows : au-delà
    de la limite, une allocation échoue (MemoryError) au lieu d'épuiser la
    mémoire de l'ordinateur ; les enfants sont tués avec le process.

    La limite porte sur le total du job (JOB_MEMORY, JobMemoryLimit), pas sur
    chaque process (PROCESS_MEMORY) : avec une limite par process, le process
    de travail et chacun de ses 7z.exe, UnRAR.exe ou process PDF pourraient
    prendre 1 Go chacun."""

    def __init__(self, limit_mb):
        self.ctypes, self.k32, self.Extended, self.Accounting = _job_api()
        self.job = self.k32.CreateJobObjectW(None, None)
        self.set_limit(limit_mb)
        c = self.ctypes
        if not self.k32.AssignProcessToJobObject(self.job, self.k32.GetCurrentProcess()):
            raise OSError(c.get_last_error(), "AssignProcessToJobObject")

    def set_limit(self, limit_mb):
        """Pose (ou déplace, le job déjà actif) la limite du total du job."""
        info = self.Extended()
        info.Basic.LimitFlags = 0x200 | 0x2000  # JOB_MEMORY | KILL_ON_JOB_CLOSE
        info.JobMemoryLimit = int(limit_mb * 1024 * 1024)
        c = self.ctypes
        if not self.k32.SetInformationJobObject(self.job, 9, c.byref(info), c.sizeof(info)):
            raise OSError(c.get_last_error(), "SetInformationJobObject")

    def peak_mb(self):
        """Pic de mémoire de l'ensemble du job (process de travail et enfants
        réunis)."""
        info = self.Extended()
        c = self.ctypes
        self.k32.QueryInformationJobObject(self.job, 9, c.byref(info), c.sizeof(info), None)
        return info.PeakJobMemoryUsed / (1024 * 1024)

    def active_processes(self):
        info = self.Accounting()
        c = self.ctypes
        self.k32.QueryInformationJobObject(self.job, 1, c.byref(info), c.sizeof(info), None)
        return info.ActiveProcesses

    def leftover_children(self, wait=3.0):
        """Process du job autres que celui-ci, encore en vie après `wait`
        secondes (Windows met un instant à compter un process terminé) :
        [(pid, nom)]."""
        end = time.monotonic() + wait
        while self.active_processes() > 1 and time.monotonic() < end:
            time.sleep(0.05)
        if self.active_processes() <= 1:
            return []
        c = self.ctypes

        class Pids(c.Structure):
            _fields_ = [("Assigned", c.c_ulong), ("InList", c.c_ulong), ("Ids", c.c_size_t * 64)]
        pids = Pids()
        self.k32.QueryInformationJobObject(self.job, 3, c.byref(pids), c.sizeof(pids), None)
        out = []
        for pid in pids.Ids[:pids.InList]:
            if pid != os.getpid():
                try:
                    name = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                          capture_output=True, text=True, timeout=10).stdout.split(",")[0]
                except Exception:  # noqa: BLE001
                    name = "?"
                out.append((pid, name.strip('"')))
        return out


# --- modules de MosaicView, sans Qt ---------------------------------------------

def load_app():
    sys.path.insert(0, REPO)
    sys.path.insert(0, os.path.join(REPO, "tests"))
    import ast_loader
    from PIL import Image
    import zipfile
    stub = types.ModuleType("modules.qt.utils")
    # Fonctions de utils.py (qui importe Qt) dont ont besoin les lecteurs et
    # l'écrivain commun des CBZ (converted_cbz.write_cbz), extraites par AST
    stub.__dict__.update(ast_loader.load(
        "utils.py",
        ["format_file_size", "check_written_cbz_structure", "fsync_file", "is_kept_temp", "make_temp_path",
         "release_temp_path", "replace_file_retrying", "verify_written_cbz", "cbz_members",
         "MissingPageDataError", "unique_names", "windows_safe_relpath", "windows_safe_component",
         "_WINDOWS_RESERVED_NAMES", "_WINDOWS_FORBIDDEN_CHARS", "FileInUseError", "ReadOnlyFileError",
         "TEMP_FILE_PREFIX", "TEMP_FILE_SUFFIX", "WrittenFileCheckError", "_active_temp_files",
         "_active_temp_lock", "_kept_temp_files", "_replace_keeping_identity", "remove_stale_temp_files",
         "ReplacementPendingError", "STALE_TEMP_AGE", "_ERROR_UNABLE_TO_MOVE_REPLACEMENT",
         "_ERROR_UNABLE_TO_MOVE_REPLACEMENT_2", "_REPLACEFILE_IGNORE_MERGE_ERRORS"],
        os=os, threading=threading, zipfile=zipfile, time=time))
    sys.modules["modules.qt.utils"] = stub
    from modules.qt import archive_names, entries, error_log
    from modules.qt.archive_type_detector import detect_archive_type
    Image.MAX_IMAGE_PIXELS = 500_000_000  # comme MosaicView.py
    archive_names.restrict_pillow_formats()

    unexpected = []
    error_log.log_if_unexpected = (
        lambda exc, *a, **k: unexpected.append(exc) if exc is not None and not error_log.is_expected_error(exc)
        else None)
    import rarfile
    rarfile.UNRAR_TOOL = os.path.join(REPO, "unrar", "UnRAR.exe")
    import tarfile
    import zipfile
    seven = os.path.join(REPO, "7zip", "7z.exe")
    ns = ast_loader.load(
        "archive_loader.py",
        {"_check_member_size", "AlteredMemberError", "_zip_member_reader", "create_page_entry",
         "_tar_info_read", "_tar_member_reader", "_rar_read", "_rar_bytes_without_crc", "_run_and_read",
         "_read_7z_file", "_list_7z_files", "_to_short_path", "_is_7z_file_block"},
        os=os, sys=sys, zipfile=zipfile, tarfile=tarfile, rarfile=rarfile, subprocess=subprocess,
        threading=threading, MAX_MEMBER_SIZE=archive_names.MAX_MEMBER_SIZE,
        create_entry=entries.create_entry, IMAGE_EXTS=archive_names.IMAGE_EXTS, _get_7z_exe=lambda: seven)
    ns.update(ast_loader.load("pdf_loading_qt.py",
                              {"read_pdf_message", "_stopped", "PDF_STOPPED_MESSAGE", "pdf_image_ext"}))
    app = types.SimpleNamespace(**ns)
    app.entries, app.names, app.detect = entries, archive_names, detect_archive_type
    app.unexpected, app.seven = unexpected, seven
    return app


# --- graines et mutations ----------------------------------------------------------

def image_seeds():
    from PIL import Image, ImageCms, ImageDraw, features
    base = Image.new("RGB", (24, 16), (200, 30, 30))
    ImageDraw.Draw(base).rectangle((4, 3, 15, 11), fill=(20, 160, 40))
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    exif = Image.Exif()
    exif[274] = 6
    seeds = []

    def add(ext, img, fmt, **kw):
        buf = io.BytesIO()
        img.save(buf, fmt, **kw)
        seeds.append((ext, buf.getvalue()))

    frames = [base, base.rotate(90, expand=False), base.convert("L").convert("RGB")]
    add(".jpg", base, "JPEG", quality=80)
    add(".jpg", base, "JPEG", progressive=True, exif=exif.tobytes(), icc_profile=srgb)
    add(".jpg", base.convert("L"), "JPEG")
    add(".jpg", base.convert("CMYK"), "JPEG")
    add(".png", base, "PNG")
    add(".png", base.convert("RGBA"), "PNG", icc_profile=srgb, exif=exif.tobytes())
    add(".png", base.convert("P"), "PNG", transparency=0)
    add(".png", base.convert("1"), "PNG")
    add(".png", base.convert("I;16"), "PNG")
    add(".png", frames[0], "PNG", save_all=True, append_images=frames[1:], duration=50)
    add(".gif", base, "GIF")
    add(".gif", frames[0], "GIF", save_all=True, append_images=frames[1:], duration=[40, 60, 80], loop=0)
    add(".webp", base, "WEBP", quality=70)
    add(".webp", base.convert("RGBA"), "WEBP", lossless=True)
    add(".webp", frames[0], "WEBP", save_all=True, append_images=frames[1:], duration=50)
    add(".tif", base, "TIFF")
    add(".tif", base, "TIFF", compression="tiff_lzw", dpi=(300, 300))
    add(".tif", base.convert("CMYK"), "TIFF", compression="tiff_deflate")
    add(".tif", base.convert("I;16"), "TIFF")
    add(".tif", base.convert("1"), "TIFF", compression="group4")
    add(".tif", frames[0], "TIFF", save_all=True, append_images=frames[1:])
    add(".bmp", base, "BMP")
    add(".bmp", base.convert("P"), "BMP")
    add(".ico", base.resize((32, 32)), "ICO", sizes=[(16, 16), (32, 32)])
    if features.check("avif"):
        add(".avif", base, "AVIF", quality=60)
    return seeds


INTERESTING = [0, 1, 0x7F, 0x80, 0xFF, 0x7FFF, 0x8000, 0xFFFF, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0x10000]


def mutate(rng, data, keep=0):
    """Mutations aveugles : bits inversés, octets remplacés par des valeurs
    limites, champs de 2 ou 4 octets forcés (dimensions, tailles, positions),
    coupe, ajout, duplication ou suppression d'une tranche. Les `keep` premiers
    octets (signature) sont gardés."""
    b = bytearray(data)
    for _i in range(rng.choice((1, 1, 1, 2, 3, 6))):
        if len(b) <= keep + 4:
            break
        pos = rng.randrange(keep, len(b) - 4)
        op = rng.randrange(9)
        if op == 0:
            b[pos] ^= 1 << rng.randrange(8)
        elif op == 1:
            b[pos] = rng.choice((0, 0xFF, 0x7F, 0x80, rng.randrange(256)))
        elif op in (2, 3):
            width = 2 if op == 2 else 4
            value = rng.choice(INTERESTING) & ((1 << (8 * width)) - 1)
            b[pos:pos + width] = value.to_bytes(width, rng.choice(("little", "big")))
        elif op == 4:
            del b[rng.randrange(max(keep + 1, len(b) // 4), len(b)):]
        elif op == 5:
            b[pos:pos] = bytes(rng.randrange(256) for _j in range(rng.randint(1, 64)))
        elif op == 6:
            end = min(len(b), pos + rng.randint(1, 256))
            b[pos:pos] = b[pos:end] * rng.randint(1, 4)
        elif op == 7:
            del b[pos:pos + rng.randint(1, 64)]
        else:
            b[pos] = (b[pos] + rng.choice((-1, 1))) & 0xFF
    return bytes(b)


def _page(rng, seeds):
    return rng.choice(seeds)[1]


def zip_seed(rng, seeds, epub=False):
    import zipfile
    buf = io.BytesIO()
    methods = [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA]
    with zipfile.ZipFile(buf, "w") as zf:
        if epub:
            zf.writestr("mimetype", "application/epub+zip")
            zf.writestr("META-INF/container.xml",
                        '<container><rootfiles><rootfile full-path="OEBPS/c.opf"/></rootfiles></container>')
            zf.writestr("OEBPS/c.opf", '<package><manifest><item id="d1" href="t1.xhtml"/>'
                                       '<item id="i1" href="img/b.jpg" properties="cover-image"/></manifest>'
                                       '<spine><itemref idref="d1"/></spine></package>')
            zf.writestr("OEBPS/t1.xhtml", '<html><body><img src="img/a.png"/><img src="img/b.jpg"/></body></html>')
            prefix = "OEBPS/img/"
            names = ["a.png", "b.jpg"]
        else:
            prefix = rng.choice(("", "Aaaa/"))
            names = ["001.jpg", "002.png", "Bbbb/003.webp", "004.gif"]
            zf.writestr("ComicInfo.xml", "<ComicInfo><Title>Aaaa</Title></ComicInfo>",
                        compress_type=rng.choice(methods))
        for name in names:
            zf.writestr(prefix + name, _page(rng, seeds), compress_type=rng.choice(methods))
    return buf.getvalue()


def mutate_zip(rng, data):
    """Champs des en-têtes ZIP (méthode, drapeaux dont chiffrement, CRC,
    tailles, longueur du nom, position de l'en-tête local), puis mutations
    aveugles."""
    b = bytearray(data)
    spots = [(i, b"PK\x01\x02") for i in range(len(b)) if b[i:i + 4] == b"PK\x01\x02"]
    spots += [(i, b"PK\x03\x04") for i in range(len(b)) if b[i:i + 4] == b"PK\x03\x04"]
    fields_cd = [(8, 2), (10, 2), (16, 4), (20, 4), (24, 4), (28, 2), (42, 4)]
    fields_lh = [(6, 2), (8, 2), (14, 4), (18, 4), (22, 4), (26, 2)]
    for _i in range(rng.randint(0, 3)):
        if not spots:
            break
        pos, sig = rng.choice(spots)
        off, width = rng.choice(fields_cd if sig == b"PK\x01\x02" else fields_lh)
        if pos + off + width > len(b):
            continue
        if off in (8, 10) and width == 2 and rng.random() < 0.5:
            value = rng.choice((0, 1, 8, 9, 12, 14, 93, 98, 99, 0x0001, 0x0008, 0x0800, 0x0041))
        else:
            value = rng.choice(INTERESTING) & ((1 << (8 * width)) - 1)
        b[pos + off:pos + off + width] = value.to_bytes(width, "little")
    data = bytes(b)
    return mutate(rng, data, keep=4) if rng.random() < 0.6 else data


def tar_seed(rng, seeds):
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=rng.choice((tarfile.GNU_FORMAT, tarfile.PAX_FORMAT,
                                                                 tarfile.USTAR_FORMAT))) as tf:
        for name in ("./001.jpg", "Aaaa/002.png", "Aaaa/", "ComicInfo.xml", "003.gif", "lien.jpg", "dur.jpg"):
            info = tarfile.TarInfo(name)
            if name.endswith("/"):
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            elif name == "lien.jpg":
                info.type, info.linkname = tarfile.SYMTYPE, "../../hors.jpg"
                tf.addfile(info)
            elif name == "dur.jpg":
                info.type, info.linkname = tarfile.LNKTYPE, "003.gif"
                tf.addfile(info)
            else:
                content = b"<ComicInfo/>" if name.endswith(".xml") else _page(rng, seeds)
                info.size = len(content)
                tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def mutate_tar(rng, data):
    import bz2
    import gzip
    kind = rng.choice(("tar", "tar", "gz", "bz2"))
    if kind == "tar" or rng.random() < 0.7:
        data = mutate(rng, data)
    if kind == "gz":
        data = gzip.compress(data, mtime=0)
    elif kind == "bz2":
        data = bz2.compress(data)
    if kind != "tar" and rng.random() < 0.3:
        data = mutate(rng, data, keep=3)
    return data


def seven_seed(app, rng, seeds, work):
    src = os.path.join(work, "src7")
    shutil.rmtree(src, ignore_errors=True)
    os.makedirs(os.path.join(src, "Aaaa"))
    for name in ("001.jpg", "Aaaa/002.png", "ComicInfo.xml", "Aaaa/003.tif"):
        with open(os.path.join(src, name), "wb") as f:
            f.write(b"<ComicInfo/>" if name.endswith(".xml") else _page(rng, seeds))
    out = os.path.join(work, f"seed{rng.randrange(10 ** 6)}.cb7")
    level = rng.choice(("-mx0", "-mx1", "-mx5"))
    solid = rng.choice(("-ms=on", "-ms=off"))
    subprocess.run([app.seven, "a", "-t7z", level, solid, out, "*"], cwd=src, stdin=subprocess.DEVNULL,
                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    with open(out, "rb") as f:
        data = f.read()
    os.remove(out)
    return data


def rar4_stored(files):
    """Archive RAR 4 dont chaque fichier est « stocké » (sans compression) :
    rarfile la lit sans programme externe."""
    import struct

    def block(htype, flags, body, add=b""):
        size = 7 + len(body)
        head = struct.pack("<BHH", htype, flags, size) + body
        return struct.pack("<H", zlib.crc32(head) & 0xFFFF) + head + add

    out = b"Rar!\x1a\x07\x00" + block(0x73, 0, b"\x00\x00\x00\x00\x00\x00")
    for name, data in files:
        raw = name.replace("/", "\\").encode("ascii")
        body = struct.pack("<IIBIIBBHI", len(data), len(data), 2, zlib.crc32(data), 0x5A000000, 20, 0x30,
                           len(raw), 0x20) + raw
        out += block(0x74, 0x8000, body, data)
    return out + block(0x7B, 0x4000, b"")


def rar_seed(rng, seeds):
    return rar4_stored([("001.jpg", _page(rng, seeds)), ("Aaaa/002.png", _page(rng, seeds)),
                        ("ComicInfo.xml", b"<ComicInfo/>"), ("Aaaa/002.png", _page(rng, seeds))])


# --- cibles -----------------------------------------------------------------------

class Finding(Exception):
    pass


def check_image(app, rng, seeds, work):
    ext, data = rng.choice(seeds)
    data = mutate(rng, data) if rng.random() < 0.95 else data
    name = "Aaaa/p" + (ext if rng.random() < 0.8 else rng.choice(app.names.IMAGE_EXTS))
    _write(work, ".bin", data)
    try:
        entry = app.entries.create_entry(name, data, app.names.IMAGE_EXTS)
    except BaseException as e:  # noqa: BLE001
        raise Finding(f"create_entry lève {type(e).__name__}: {e}") from e
    if entry["is_corrupted"]:
        return data, "illisible"
    if not entry.get("img_width") or not entry.get("img_height"):
        raise Finding(f"page acceptée sans dimensions {entry.get('img_width')}x{entry.get('img_height')}")
    img = app.entries.ensure_image_loaded(entry)
    if img is None:
        raise Finding("page acceptée à l'ouverture, refusée à l'affichage (ensure_image_loaded)")
    try:
        from modules.qt.gif_writer import build_gif
        gif, _left_out = build_gif([entry, entry], 100, 0, 2, False, "")
    except MemoryError:
        gif = None
    except Exception as e:  # noqa: BLE001
        raise Finding(f"build_gif lève {type(e).__name__}: {e}") from e
    if gif is not None and app.entries.create_entry("a.gif", gif, app.names.IMAGE_EXTS)["is_corrupted"]:
        raise Finding("GIF créé illisible")
    several = entry.get("is_animated_gif") or entry.get("is_animated_image") or entry.get("is_multipage_tiff")
    if several:
        return data, "plusieurs images"
    try:
        out = app.entries.save_image_to_bytes(entry)
    except Exception as e:  # noqa: BLE001
        raise Finding(f"save_image_to_bytes lève {type(e).__name__}: {e}") from e
    again = app.entries.create_entry(entry["orig_name"], out, app.names.IMAGE_EXTS)
    if again["is_corrupted"]:
        raise Finding(f"page réenregistrée illisible : {again['corruption_reason']}")
    return data, "lisible"


def _write(work, ext, data):
    """L'entrée sur le disque, avant sa lecture : gardée par le superviseur si
    elle bloque, plante le process ou dépasse la mémoire."""
    for old in os.listdir(work):
        if old.startswith("entree"):
            os.remove(os.path.join(work, old))
    path = os.path.join(work, "entree" + ext)
    with open(path, "wb") as f:
        f.write(data)
    return path


def _detect(app, path):
    try:
        return app.detect(path)
    except BaseException as e:  # noqa: BLE001
        raise Finding(f"detect_archive_type lève {type(e).__name__}: {e}") from e


def _read_pages(app, names, read_of, size_of):
    """La boucle de lecture des pages de LoadWorker.load_archive : chaque page
    par create_page_entry, qui ne doit jamais lever."""
    count = 0
    for name in names:
        if name.endswith("/"):
            continue
        try:
            entry, _msg = app.create_page_entry(name, read_of(name))
        except Exception as e:  # noqa: BLE001
            # Rattrapée par le chargeur (archive entière refusée), mais
            # create_page_entry doit rattraper toute erreur de lecture
            raise Finding(f"create_page_entry lève {type(e).__name__}: {e}") from e
        data = entry.get("bytes")
        if data is not None:
            count += 1
            if len(data) > app.names.MAX_MEMBER_SIZE:
                raise Finding(f"page de {len(data)} octets, au-delà de MAX_MEMBER_SIZE")
            # Moins que la taille annoncée, CRC juste : taille annoncée fausse,
            # octets complets (membre stocké, zipfile lit la taille compressée)
            expected = size_of(name)
            if expected is not None and len(data) > expected:
                raise Finding(f"page {name!a} : {len(data)} octets lus, {expected} annoncés")
    return count


def check_zip(app, rng, seeds, work):
    epub = rng.random() < 0.3
    data = mutate_zip(rng, zip_seed(rng, seeds, epub))
    path = _write(work, ".epub" if epub else ".cbz", data)
    kind = _detect(app, path)
    if kind not in ("CBZ", "EPUB"):
        return data, f"type {kind}"
    try:
        with app.names.open_zip(path) as archive:
            names = sorted(archive.namelist())
            if kind == "EPUB":
                names = app.names.epub_reading_order(
                    archive, [n for n in names if n.lower().endswith(app.names.IMAGE_EXTS)])
    except Exception:  # noqa: BLE001
        return data, "liste refusée"  # LoadWorker._get_namelist : message « archive invalide »
    try:
        with app.names.open_zip(path) as archive:
            read_member = app._zip_member_reader(archive)
            infos = {}
            for info in archive.infolist():
                infos.setdefault(info.filename, []).append(info.file_size)
            sizes = {n: v[0] if len(set(v)) == 1 else None for n, v in infos.items()}
            count = _read_pages(app, names, lambda n: (lambda: read_member(n)), sizes.get)
    except Finding:
        raise
    except Exception as e:  # noqa: BLE001
        # LoadWorker._emit_read_error : journal des erreurs si inattendue
        if not _expected(e):
            app.unexpected.append(e)
        return data, "archive refusée"
    return data, f"{count} pages lues"


def check_tar(app, rng, seeds, work):
    import tarfile
    data = mutate_tar(rng, tar_seed(rng, seeds))
    path = _write(work, ".cbt", data)
    kind = _detect(app, path)
    if kind != "CBT":
        return data, f"type {kind}"
    try:
        with tarfile.open(path, "r:*") as archive:
            names = sorted(m.name for m in archive.getmembers() if m.isfile())
    except Exception:  # noqa: BLE001
        return data, "liste refusée"
    try:
        with tarfile.open(path, "r:*") as archive:
            read_member = app._tar_member_reader(archive)
            sizes = {}
            for m in archive.getmembers():
                if m.isfile():
                    sizes[m.name] = m.size if m.name not in sizes else None
            count = 0
            for name in names:
                try:
                    page = read_member(name)
                except Exception as e:  # noqa: BLE001
                    if not _expected(e):
                        app.unexpected.append(e)
                    continue
                count += 1
                if sizes.get(name) is not None and len(page) > sizes[name]:
                    raise Finding(f"page {name!a} : {len(page)} octets lus, {sizes[name]} annoncés")
                app.entries.create_entry(app.names.tar_entry_name(name), page, app.names.IMAGE_EXTS)
    except Finding:
        raise
    except Exception as e:  # noqa: BLE001
        if not _expected(e):
            app.unexpected.append(e)
        return data, "archive refusée"
    return data, f"{count} pages lues"


def _expected(exc):
    from modules.qt import error_log
    return error_log.is_expected_error(exc)


def check_7z(app, rng, seeds, work, job, cache):
    if not cache or rng.random() < 0.05:
        cache.append(seven_seed(app, rng, seeds, work))
        del cache[:-6]
    data = mutate(rng, rng.choice(cache), keep=6 if rng.random() < 0.85 else 0)
    path = _write(work, ".cb7", data)
    kind = _detect(app, path)
    if kind != "CB7":
        return data, f"type {kind}"
    try:
        names = sorted(app._list_7z_files(path))
    except Exception as e:  # noqa: BLE001
        if not _expected(e):
            app.unexpected.append(e)
        return data, "liste refusée"
    count = _read_pages(app, names, lambda n: (lambda: app._read_7z_file(path, n)), lambda n: None)
    left = job.leftover_children()
    if left:
        raise Finding(f"programmes encore en vie 3 s après la lecture : {left}")
    return data, f"{count} pages lues"


def check_rar(app, rng, seeds, work, job):
    import rarfile
    data = rar_seed(rng, seeds)
    data = mutate(rng, data, keep=7 if rng.random() < 0.85 else 0)
    path = _write(work, ".cbr", data)
    kind = _detect(app, path)
    if kind != "CBR":
        return data, f"type {kind}"
    try:
        with rarfile.RarFile(path, "r") as archive:
            names = sorted(archive.namelist())
    except Exception as e:  # noqa: BLE001
        if not _expected(e):
            app.unexpected.append(e)
        return data, "liste refusée"
    try:
        with rarfile.RarFile(path, "r") as archive:
            count = _read_pages(app, names, lambda n: (lambda: app._rar_read(archive, n)), lambda n: None)
    except Finding:
        raise
    except Exception as e:  # noqa: BLE001
        if not _expected(e):
            app.unexpected.append(e)
        return data, "archive refusée"
    left = job.leftover_children()
    if left:
        raise Finding(f"programmes encore en vie 3 s après la lecture : {left}")
    return data, f"{count} pages lues"


# --- Enchaînement ouverture -> enregistrement -> relecture ---------------------------

def check_chain(app, rng, seeds, work):
    """Archive abîmée lue comme à l'ouverture (create_page_entry, noms TAR
    par tar_entry_name), puis enregistrée comme un document
    (utils.cbz_members, converted_cbz.write_cbz avec relecture complète), et
    rouverte : une page sans données n'est jamais omise en silence
    (MissingPageDataError), le CBZ écrit rend les mêmes noms et les mêmes
    octets, aucun temporaire ne reste à côté."""
    import tarfile
    from modules.qt import converted_cbz
    from modules.qt.utils import MissingPageDataError, cbz_members
    use_tar = rng.random() < 0.4
    if use_tar:
        data = mutate_tar(rng, tar_seed(rng, seeds))
    else:
        data = mutate_zip(rng, zip_seed(rng, seeds))
    path = _write(work, ".cbt" if use_tar else ".cbz", data)
    entries = []
    try:
        if use_tar:
            with tarfile.open(path, "r:*") as archive:
                read_member = app._tar_member_reader(archive)
                for name in sorted(m.name for m in archive.getmembers() if m.isfile()):
                    entry, _msg = app.create_page_entry(app.names.tar_entry_name(name),
                                                        lambda n=name: read_member(n))
                    entries.append(entry)
        else:
            with app.names.open_zip(path) as archive:
                read_member = app._zip_member_reader(archive)
                for name in sorted(archive.namelist()):
                    if not name.endswith("/"):
                        entry, _msg = app.create_page_entry(name, lambda n=name: read_member(n))
                        entries.append(entry)
    except Exception:  # noqa: BLE001
        return data, "archive refusée"
    if not entries:
        return data, "aucune page"
    out = os.path.join(work, "sortie.cbz")
    if os.path.exists(out):
        os.remove(out)
    before = set(os.listdir(work))
    try:
        members = cbz_members(entries)
    except MissingPageDataError:
        if all(e.get("bytes") is not None for e in entries):
            raise Finding("MissingPageDataError alors que toutes les pages ont des données")
        return data, "enregistrement refusé (page sans données)"
    if any(e.get("bytes") is None for e in entries):
        raise Finding("page sans données acceptée à l'enregistrement")
    try:
        with converted_cbz.write_cbz(out, reread=True) as cbz:
            for name, entry in members:
                cbz.writestr(name, entry["bytes"])
            cbz.expected_count = len(members)
    except Exception as e:  # noqa: BLE001
        raise Finding(f"enregistrement lève {type(e).__name__}: {e} ; noms {[n for n, _e in members]!a}") from e
    left = set(os.listdir(work)) - before - {"sortie.cbz"}
    if left:
        raise Finding(f"temporaire resté à côté du CBZ : {left!a}")
    with app.names.open_zip(out) as written:
        got = [(info.filename, written.read(info)) for info in written.infolist()]
    expected = [(name, entry["bytes"]) for name, entry in members]
    if got != expected:
        diff = [(a[0], b[0], a[1] == b[1]) for a, b in zip(expected, got) if a != b][:5]
        raise Finding(f"CBZ relu différent : {len(got)} membres pour {len(expected)}, {diff!a}")
    return data, f"{len(members)} fichiers enregistrés"


# --- ComicInfo.xml ------------------------------------------------------------------

_XML_TEXTS = ["Aaaa", "", "  ", "Bbbb Cccc", "é à ç", "\U0001F600", "‮Aaaa", "é", "a&amp;b",
              "&lt;x&gt;", "&#233;", "&#x1F600;", "<![CDATA[<b>&]]>", "x]]&gt;y", "1.5", "-3",
              "ligne\nligne", "tab\there", "　", "&quot;'", "&#13;", "https://aaaa.example/?a=1&amp;b=2"]
_XML_TAGS = ["Title", "Series", "Number", "Summary", "Writer", "Web", "Notes", "PageCount", "Year",
             "Genre", "Tags", "LanguageISO", "Manga", "Inconnu", "x:Autre", "Aaaa_Bbbb"]
_XML_ATTRS = [('xmlns:xsi', 'http://www.w3.org/2001/XMLSchema-instance'),
              ('xmlns:xsd', 'http://www.w3.org/2001/XMLSchema'), ('xmlns', 'http://aaaa.example/ci'),
              ('note', 'a&gt;b'), ('note2', "c'd"), ('autre', '&lt;&amp;&quot;'), ('saut', 'x&#10;y'),
              ('uni', 'é\U0001F600'), ('xsi:nil', 'true')]


def _xml_attrs(rng, pool, count):
    picked = rng.sample(pool, min(count, len(pool)))
    quote = rng.choice(('"', "'"))
    escaped = "&quot;" if quote == '"' else "&apos;"
    return "".join(f" {k}={quote}{v.replace(quote, escaped)}{quote}" for k, v in picked)


def _xml_element(rng, depth):
    tag = rng.choice(_XML_TAGS)
    if tag.startswith("x:"):
        attrs = ' xmlns:x="http://aaaa.example/x"'
    else:
        attrs = _xml_attrs(rng, [("a", "1"), ("b", "&lt;2&gt;"), ("c", "é")], rng.randint(0, 2))
    if depth < 2 and rng.random() < 0.25:
        inner = "".join(_xml_element(rng, depth + 1) + rng.choice(("", " ", "\n", "queue"))
                        for _i in range(rng.randint(1, 3)))
        return f"<{tag}{attrs}>{rng.choice(('', 'texte', ' '))}{inner}</{tag}>"
    if rng.random() < 0.1:
        return f"<{tag}{attrs}/>"
    return f"<{tag}{attrs}>{rng.choice(_XML_TEXTS)}</{tag}>"


def xml_seed(rng):
    parts = []
    if rng.random() < 0.3:
        parts.append(f"<!-- <ComicInfo ancien='1'> {rng.choice(_XML_TEXTS)} -->")
    if rng.random() < 0.2:
        parts.append("<?outil version='1'?>")
    children = []
    for _i in range(rng.randint(0, 6)):
        children.append(_xml_element(rng, 0))
        if rng.random() < 0.15:
            children.append("<!-- note -->")
    if rng.random() < 0.5:
        extras = ("", ' Type="FrontCover"', ' DoublePage="True"', ' Bookmark="é&amp;"')
        pages = "".join(f'<Page Image="{i}" ImageSize="{rng.randrange(10 ** 6)}"{rng.choice(extras)}/>'
                        for i in range(rng.randint(0, 4)))
        children.append(f"<Pages>{pages}</Pages>")
    if rng.random() < 0.2:
        children.append('<MosaicViewTrace date="2026-01-01" url="https://aaaa.example/?a=1&amp;b=2"/>')
    sep = rng.choice(("", "\n", "\r\n", "\n  "))
    root_attrs = _xml_attrs(rng, _XML_ATTRS, rng.randint(0, 3))
    if not children and rng.random() < 0.5:
        root = f"<ComicInfo{root_attrs}/>"
    else:
        root = f"<ComicInfo{root_attrs}>{sep}{sep.join(children)}{sep}</ComicInfo>"
    text = "".join(parts) + root
    encoding = rng.choice(("utf-8", "utf-8", "utf-8-sig", "utf-16", "iso-8859-1", None))
    if encoding is None:
        return text.encode("utf-8")
    declared = {"utf-8-sig": "utf-8"}.get(encoding, encoding)
    try:
        return (f'<?xml version="1.0" encoding="{declared}"?>' + text).encode(encoding)
    except UnicodeEncodeError:
        return ('<?xml version="1.0" encoding="utf-8"?>' + text).encode("utf-8")


def _canonical(elem, top=False):
    """Contenu d'un élément tel que la réécriture doit le garder : balise,
    attributs, texte d'une feuille (fins de ligne normalisées comme le fait
    tout lecteur XML), texte non blanc d'un élément à sous-éléments, et
    sous-éléments avec leur texte de queue non blanc. Ne sont pas gardés
    (choix de la réécriture) : commentaires et instructions de traitement,
    texte direct de <ComicInfo> et queue de ses enfants, texte de <Pages> et
    de <MosaicViewTrace> (balises de MosaicView, réécrites à leur façon)."""
    def norm(text):
        return (text or "").replace("\r\n", "\n").replace("\r", "\n")
    children = [c for c in elem if isinstance(c.tag, str)]
    if elem.tag == "Pages":
        # Liste des pages, régénérée par MosaicView : seuls comptent les
        # attributs de chaque page (toute balise y est réécrite en <Page>)
        return ("Pages", sorted(elem.attrib.items()), [sorted(c.attrib.items()) for c in children])
    if top or elem.tag == "MosaicViewTrace":
        text = None
    elif children:
        text = norm(elem.text).strip()
    else:
        text = norm(elem.text)
    # Racine : toujours réécrite en <ComicInfo>, quel que soit son nom
    return ("ComicInfo" if top else elem.tag, sorted(elem.attrib.items()), text,
            [(_canonical(c), None if top else norm(c.tail).strip()) for c in children])


def check_xml(app, rng, work):
    from modules.qt import comic_info
    data = xml_seed(rng)
    if rng.random() < 0.25:
        data = mutate(rng, data)
    _write(work, ".xml", data)
    try:
        comic_info.parse_comic_info_xml(data)
    except BaseException as e:  # noqa: BLE001
        raise Finding(f"parse_comic_info_xml lève {type(e).__name__}: {e}") from e
    try:
        # Comme l'application : version réparée si le parseur durci le
        # refuse, et balise <ComicInfo> reprise des bytes réellement lus
        root, source = comic_info._comic_tree(data)
    except Exception:  # noqa: BLE001
        return data, "illisible"
    repaired = source is not comic_info._as_bytes(data)
    if repaired:
        bad = _repaired_xml_problem(source)
        if bad:
            raise Finding(f"version réparée dangereuse ({bad}) : {source[:300]!a}")
    try:
        out = comic_info._serialize_comic_xml(root, source)
    except Exception as e:  # noqa: BLE001
        raise Finding(f"réécriture lève {type(e).__name__}: {e}") from e
    try:
        back = comic_info._comic_fromstring(out)
    except Exception as e:  # noqa: BLE001
        raise Finding(f"ComicInfo.xml réécrit illisible ({e}) : {out[:300]!a}") from e
    if root.tag != "ComicInfo":
        # Racine d'un autre nom : réécrite en <ComicInfo> sans ses attributs
        # (_comic_info_open_tag ne reprend que ceux d'une vraie <ComicInfo>)
        root.attrib.clear()
    # Noms qu'UTF-8 ne peut pas porter (comic_info._writable_name) : omis
    for elem in list(root.iter()):
        for key in [k for k in elem.attrib if not comic_info._writable_name(k)]:
            del elem.attrib[key]
        for child in [c for c in elem if isinstance(c.tag, str) and not comic_info._writable_name(c.tag)]:
            elem.remove(child)
    if _canonical(back, top=True) != _canonical(root, top=True):
        raise Finding(f"contenu changé à la réécriture : {_canonical(root, top=True)!a} -> "
                      f"{_canonical(back, top=True)!a}")
    if comic_info._serialize_comic_xml(back, out) != out:
        raise Finding("seconde réécriture différente de la première")
    return data, "réparé puis réécrit" if repaired else "réécrit"


def _repaired_xml_problem(clean):
    """Ce que la version réparée d'un ComicInfo.xml (comic_info._repaired_comic_xml)
    ne doit jamais contenir : une DTD ou une déclaration d'entité (refusées
    par le parseur durci), une référence d'entité non prédéfinie."""
    if b"<!DOCTYPE" in clean or b"<!ENTITY" in clean:
        return "DTD"
    import re as _re
    for ref in _re.findall(rb"&([A-Za-z_][\w.-]*);", clean):
        if ref not in (b"amp", b"lt", b"gt", b"quot", b"apos"):
            return f"entité {ref!a}"
    return None


# --- PDF ----------------------------------------------------------------------------

def pdf_engine(in_q, out_send):
    """Process du moteur PDF : la vraie fonction _pdf_persistent_process de
    pdf_loading_qt.py (qui importe Qt), extraite par ast_loader, avec les
    réglages de Pillow de MosaicView.py, que le process de l'application
    reçoit aussi (spawn)."""
    sys.path[:0] = [REPO, os.path.join(REPO, "tests")]
    import ast_loader
    from PIL import Image
    from modules.qt import archive_names
    from modules.qt.error_log import logged_child_process
    from modules.qt.pdf_page_images import image_damage, original_images, page_resolution, raw_page_image
    Image.MAX_IMAGE_PIXELS = 500_000_000
    archive_names.restrict_pillow_formats()
    ns = ast_loader.load(
        "pdf_loading_qt.py",
        {"_pdf_persistent_process", "pdf_image_ext", "_send_page_error", "_send_request_error",
         "_CHANNEL_CLOSED", "_JPEG_TOO_LARGE_MARK"},
        logged_child_process=logged_child_process, page_resolution=page_resolution,
        image_damage=image_damage,
        original_images=original_images, raw_page_image=raw_page_image)
    ns["_pdf_persistent_process"](in_q, out_send)


def pdf_seed(rng, seeds):
    import pymupdf as fitz
    doc = fitz.open()
    pictures = [d for ext, d in seeds if ext in (".jpg", ".png")]
    for _i in range(rng.randint(1, 3)):
        width, height = rng.choice(((595, 842), (300, 500), (842, 595), (100, 100)))
        page = doc.new_page(width=width, height=height)
        kind = rng.randrange(4)
        if kind in (0, 2):
            page.insert_text((20, 40), "Aaaa Bbbb", fontsize=rng.choice((8, 12, 30)))
        if kind in (1, 2, 3):
            rect = page.rect if kind == 1 else fitz.Rect(10, 10, width / 2, height / 2)
            page.insert_image(rect, stream=rng.choice(pictures))
        if rng.random() < 0.2:
            page.set_rotation(rng.choice((90, 180, 270)))
    kwargs = {"garbage": rng.choice((0, 1, 3)), "deflate": rng.random() < 0.5}
    if rng.random() < 0.15:
        kwargs.update(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="Aaaa",
                      user_pw=rng.choice(("", "", "Bbbb")))
    data = doc.tobytes(**kwargs)
    doc.close()
    return data


class PdfEngine:
    """Process du moteur, lancé comme _spawn_pdf_process et gardé tant qu'il
    vit, comme le process préchauffé de l'application."""

    def __init__(self, app):
        self.app, self.proc = app, None

    def ensure(self):
        if self.proc is not None and self.proc.is_alive():
            return
        self.stop()
        import multiprocessing
        ctx = multiprocessing.get_context("spawn")
        self.in_q = ctx.Queue()
        self.recv, send = ctx.Pipe(duplex=False)
        self.proc = ctx.Process(target=pdf_engine, args=(self.in_q, send), daemon=True)
        self.proc.start()
        send.close()

    def read(self):
        return self.app.read_pdf_message(self.recv, self.proc, PDF_SILENCE_SECONDS)

    def stop(self):
        if self.proc is not None:
            if self.proc.is_alive():
                self.proc.kill()
            self.proc.join(5)
        self.proc = None


def _pdf_run(app, engine, request, complete):
    """Envoie `request` et lit la réponse jusqu'à son dernier message. Rend
    l'issue. complete : chaque page 0..n-1 doit être rendue ou signalée."""
    engine.ensure()
    engine.in_q.put(request)
    first = engine.read()
    if first is None:
        engine.stop()
        raise Finding(f"moteur PDF muet {PDF_SILENCE_SECONDS} s après {request[0]}")
    if first[0] == "process_stopped":
        return "moteur arrêté (plantage natif)"
    if first[0] in ("error", "password_error", "empty_pdf", "no_images"):
        return first[0]
    if first[0] == "batch_ready":
        return first
    if first[0] != "total":
        raise Finding(f"premier message inattendu {first[0]!a}")
    total, seen = first[1], []
    while True:
        msg = engine.read()
        if msg is None:
            engine.stop()
            raise Finding(f"moteur PDF muet {PDF_SILENCE_SECONDS} s au milieu de {request[0]}")
        kind = msg[0]
        if kind == "process_stopped":
            return "moteur arrêté (plantage natif)"
        if kind in ("page", "page_error", "merge_page"):
            seen.append(msg)
            if kind != "page_error" and len(msg) > 2 and isinstance(msg[2], bytes):
                ext = app.pdf_image_ext(msg[2])
                entry = app.entries.create_entry("p" + ext, msg[2], app.names.IMAGE_EXTS)
                rendered = request[0] in ("run", "run_merge") and len(request) > 2 and request[2] > 0
                if entry["is_corrupted"] and rendered:
                    raise Finding(f"page rendue illisible : {entry['corruption_reason']}")
        elif kind in ("progress", "scan_progress"):
            continue
        elif kind in ("done", "no_images", "error"):
            if complete and kind == "done":
                # Numéro de page : celui du message, ou celui du nom de fichier
                # d'une page de fusion (« NEW-page_0001.jpg » : page 0)
                pages = sorted(m[1] if m[0] != "merge_page" else int(m[1].rsplit("_", 1)[1][:4]) - 1
                               for m in seen)
                if pages != list(range(total)):
                    raise Finding(f"pages reçues {pages} pour {total} pages, sans erreur signalée")
            return f"{kind} ({len(seen)} messages de page)"
        else:
            raise Finding(f"message inattendu {kind!a}")


def check_pdf(app, rng, seeds, work, engine, probe):
    from modules.qt import pdf_probe
    data = pdf_seed(rng, seeds)
    if rng.random() < 0.9:
        data = mutate(rng, data, keep=5 if rng.random() < 0.85 else 0)
    path = _write(work, ".pdf", data)
    # Accès courts (Bibliothèque, import de métadonnées en lot)
    for request in (("count", path), ("cover", path)):
        status, _result = probe.call(request, timeout=PDF_SILENCE_SECONDS)
        if status == pdf_probe.TIMEOUT:
            raise Finding(f"PdfProbe sans réponse en {PDF_SILENCE_SECONDS} s ({request[0]})")
    mode = rng.randrange(5)
    if mode == 0:
        outcome = _pdf_run(app, engine, ("run", path, rng.choice((0, 0, 72, 150))), True)
    elif mode == 1:
        outcome = _pdf_run(app, engine, ("run_images", path, rng.random() < 0.5), False)
    elif mode == 2:
        outcome = _pdf_run(app, engine, ("run_merge", path, rng.choice((0, 72)), "NEW-"), True)
    elif mode == 3:
        ready = _pdf_run(app, engine, ("batch_open", path), False)
        if isinstance(ready, tuple) and ready[1] is not None:
            names = [f"{i + 1:04d}.jpg" for i in range(ready[1])]
            engine.in_q.put(("batch_convert", names))
            total = ready[1]
            pages = []
            while True:
                msg = engine.read()
                if msg is None:
                    engine.stop()
                    raise Finding(f"moteur PDF muet {PDF_SILENCE_SECONDS} s pendant batch_convert")
                if msg[0] in ("batch_page", "page_error"):
                    pages.append(msg)
                elif msg[0] in ("done", "error", "process_stopped"):
                    if msg[0] == "done" and len(pages) != total:
                        raise Finding(f"{len(pages)} pages reçues pour {total} pages en lot")
                    outcome = msg[0]
                    break
        else:
            outcome = f"batch_open : {ready if isinstance(ready, str) else 'mot de passe'}"
    else:
        engine.ensure()
        engine.in_q.put(("preopen", path))
        first = engine.read()
        if first is None:
            engine.stop()
            raise Finding("moteur PDF muet après preopen")
        if first[0] == "preopen_ok":
            outcome = _pdf_run(app, engine, ("run_opened", rng.choice((0, 72))), True)
        else:
            outcome = f"preopen : {first[0]}"
    return data, outcome if isinstance(outcome, str) else str(outcome)[:40]


# --- process de travail -------------------------------------------------------------

def _warm_up_pdf(app, seeds, work, engine, probe, seed):
    """Démarre PdfProbe et le moteur PDF sur un PDF sain, rendu à la plus haute
    résolution des entrées, avant la mesure du départ de mémoire."""
    path = _write(work, ".pdf", pdf_seed(random.Random(f"{seed}:pdf:depart"), seeds))
    for request in (("count", path), ("cover", path)):
        probe.call(request, timeout=PDF_SILENCE_SECONDS)
    _pdf_run(app, engine, ("run", path, 150), True)


def worker(target, seed, start, seconds, outdir):
    job = MemoryJob(MEMORY_LIMIT_MB)
    app = load_app()
    # Dossier fixe, jamais vidé par le process de travail : l'entrée en cours
    # y reste quand il bloque ou plante, le superviseur la recopie
    work = os.path.join(outdir, f"w_{target}")
    os.makedirs(work, exist_ok=True)
    seeds = image_seeds()
    cache = []
    engine = probe = None
    if target == "pdf":
        from modules.qt.pdf_probe import PdfProbe
        engine, probe = PdfEngine(app), PdfProbe(timeout=PDF_SILENCE_SECONDS)
        _warm_up_pdf(app, seeds, work, engine, probe, seed)
    # Limite et alerte comptées au-dessus de ce départ : les process PDF
    # démarrés occupent à eux seuls environ 600 Mo, qu'une limite de 1 Go au
    # total ne laisserait presque plus aux entrées
    baseline = job.peak_mb()
    job.set_limit(baseline + MEMORY_LIMIT_MB)
    end = time.monotonic() + seconds
    index = start
    while time.monotonic() < end:
        rng = random.Random(f"{seed}:{target}:{index}")
        print(f"I {index}", flush=True)
        t0 = time.monotonic()
        del app.unexpected[:]
        try:
            if target == "images":
                _data, outcome = check_image(app, rng, seeds, work)
            elif target == "zip":
                _data, outcome = check_zip(app, rng, seeds, work)
            elif target == "tar":
                _data, outcome = check_tar(app, rng, seeds, work)
            elif target == "7z":
                _data, outcome = check_7z(app, rng, seeds, work, job, cache)
            elif target == "rar":
                _data, outcome = check_rar(app, rng, seeds, work, job)
            elif target == "xml":
                _data, outcome = check_xml(app, rng, work)
            elif target == "chaine":
                _data, outcome = check_chain(app, rng, seeds, work)
            else:
                _data, outcome = check_pdf(app, rng, seeds, work, engine, probe)
        except Finding as f:
            # Copie faite ici : l'entrée suivante remplacera celle-ci avant
            # que le superviseur lise ce constat
            kept = _keep_input(outdir, target, index)
            print("F " + json.dumps({"i": index, "msg": str(f), "tb": traceback.format_exc(limit=8),
                                     "file": kept}), flush=True)
            outcome = "échec"
        elapsed = time.monotonic() - t0
        print("R " + json.dumps({"i": index, "outcome": outcome, "s": round(elapsed, 2),
                                 "unexpected": [f"{type(e).__module__}.{type(e).__name__}: {str(e)[:120]}"
                                                for e in app.unexpected]}), flush=True)
        peak = job.peak_mb()
        if peak - baseline > MEMORY_ALERT_MB:
            print("M " + json.dumps({"i": index, "mb": round(peak - baseline), "base": round(baseline)}),
                  flush=True)
            return 3
        index += 1
    if engine is not None:
        engine.stop()
        probe.shutdown()
    return 0


# --- campagne -------------------------------------------------------------------------

def _keep_input(outdir, target, index):
    """Copie de l'entrée en cours du process de travail, sous son numéro."""
    work = os.path.join(outdir, f"w_{target}")
    for name in os.listdir(work) if os.path.isdir(work) else ():
        if name.startswith("entree"):
            kept = os.path.join(outdir, f"{target}_{index}{os.path.splitext(name)[1]}")
            shutil.copyfile(os.path.join(work, name), kept)
            return kept
    return None


def campaign(targets, seed, seconds):
    outdir = os.path.join(tempfile.gettempdir(), f"MosaicView_fuzz_files_{seed}")
    os.makedirs(outdir, exist_ok=True)
    env = dict(os.environ, APPDATA=os.path.join(outdir, "appdata"), PYTHONIOENCODING="utf-8")
    findings, totals = [], {}
    for target in targets:
        hang = PDF_HANG_SECONDS if target == "pdf" else HANG_SECONDS
        # Entrée lente : au-delà de ce délai (PDF : lancement du moteur compris)
        slow = 10 if target == "pdf" else 5
        start, deadline = 0, time.monotonic() + seconds
        stats = {"entrées": 0, "issues": {}, "inattendues": {}, "lentes": 0, "max_s": 0.0}
        totals[target] = stats
        while time.monotonic() < deadline - 1:
            cmd = [sys.executable, "-I", os.path.abspath(__file__), "--travail", target, str(seed), str(start),
                   str(deadline - time.monotonic()), outdir]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            lines = []
            stderr_tail = []
            threading.Thread(target=lambda: stderr_tail.extend(proc.stderr.read().decode("utf-8", "replace")
                                                               .splitlines()[-15:]), daemon=True).start()

            def pump():
                for raw in proc.stdout:
                    lines.append(raw.decode("utf-8", "replace").rstrip("\n"))
            reader = threading.Thread(target=pump, daemon=True)
            reader.start()
            current, seen, last_change = start, 0, time.monotonic()
            while proc.poll() is None:
                time.sleep(0.2)
                if len(lines) != seen:
                    seen, last_change = len(lines), time.monotonic()
                if time.monotonic() - last_change > hang:
                    proc.kill()
                    break
            proc.wait()
            reader.join(5)
            mem_alert = False
            for line in lines:
                kind, _sep, rest = line.partition(" ")
                if kind == "I":
                    current = int(rest)
                elif kind == "R":
                    r = json.loads(rest)
                    stats["entrées"] += 1
                    stats["issues"][r["outcome"]] = stats["issues"].get(r["outcome"], 0) + 1
                    for u in r["unexpected"]:
                        stats["inattendues"].setdefault(u.split(":")[0], (r["i"], u))
                    stats["max_s"] = max(stats["max_s"], r["s"])
                    if r["s"] > slow:
                        stats["lentes"] += 1
                        findings.append((target, r["i"], f"lente : {r['s']} s", None))
                    current = r["i"] + 1
                elif kind == "F":
                    f = json.loads(rest)
                    findings.append((target, f["i"], f["msg"] + "\n" + f["tb"], f["file"]))
                elif kind == "M":
                    m = json.loads(rest)
                    mem_alert = True
                    findings.append((target, m["i"], f"mémoire : pic de {m['mb']} Mo au-dessus du "
                                                     f"départ ({m['base']} Mo)",
                                     _keep_input(outdir, target, m["i"])))
            if proc.returncode == 0:
                break
            if not any(line.startswith("I ") for line in lines):
                print(f"le process de travail ne démarre pas : {' | '.join(stderr_tail)[-800:]}")
                return 2
            if not mem_alert:
                reason = f"bloquée plus de {hang} s" if time.monotonic() - last_change > hang else \
                    f"process arrêté (code {proc.returncode}) : {' | '.join(stderr_tail[-3:])[:300]}"
                findings.append((target, current, reason, _keep_input(outdir, target, current)))
            start = current + 1
        shutil.rmtree(os.path.join(outdir, f"w_{target}"), ignore_errors=True)
        print(f"{target} : {stats['entrées']} entrées, issues {stats['issues']}, "
              f"le plus long {stats['max_s']} s", flush=True)
        for kind, (i, text) in stats["inattendues"].items():
            print(f"  exception inattendue (journal des erreurs) {kind}, entrée {i} : {ascii(text)[:200]}")
    report = os.path.join(outdir, "rapport.json")
    with open(report, "w", encoding="utf-8") as f:
        json.dump({"graine": seed, "secondes": seconds, "totaux": totals,
                   "constats": [{"cible": t, "entrée": i, "constat": m, "fichier": k} for t, i, m, k in findings]},
                  f, ensure_ascii=False, indent=1)
    shutil.rmtree(os.path.join(outdir, "appdata"), ignore_errors=True)
    for t, i, m, k in findings[:60]:
        print(f"[{t} #{i}] {ascii(m)[:300]}" + (f"  -> {k}" if k else ""))
    print(f"{len(findings)} constats ; rapport : {report}")
    return 1 if findings else 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--travail":
        target, seed, start, seconds, outdir = sys.argv[2:7]
        return worker(target, int(seed), int(start), float(seconds), outdir)
    parser = argparse.ArgumentParser(description="Fuzzing des images et archives abîmées de MosaicView")
    parser.add_argument("--secondes", type=float, default=60.0, help="durée par cible (60 par défaut)")
    parser.add_argument("--graine", type=int, default=None, help="rejouer une campagne (graine affichée)")
    parser.add_argument("--cibles", default=",".join(TARGETS), help="parmi " + ",".join(TARGETS))
    args = parser.parse_args()
    seed = args.graine if args.graine is not None else random.SystemRandom().randrange(10 ** 9)
    targets = [t for t in args.cibles.split(",") if t in TARGETS]
    print(f"graine {seed} (pour rejouer : --graine {seed})", flush=True)
    return campaign(targets, seed, args.secondes)


if __name__ == "__main__":
    sys.exit(main())
