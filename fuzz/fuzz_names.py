"""Campagne de fuzzing des fonctions de noms et de chemins, et des fonctions
qui reçoivent un texte venu d'un fichier ou du web (sans Qt).

Lancement, depuis la racine du projet :
    python fuzz/fuzz_names.py [--secondes N] [--graine G]

Sans --graine, la graine est tirée au hasard : chaque lancement essaie des
noms différents. Elle est affichée au départ ; la redonner par --graine
rejoue exactement la même campagne (reproduire un échec). --secondes : durée
par cible (15 par défaut, 12 cibles).

Outil d'exploration, hors de la suite de tests : la version courte et à
graine fixe de ces contrôles est tests/test_fuzz_names.py. Toute écriture se
fait dans un dossier temporaire neuf, supprimé à la fin ; aucun fichier de
l'utilisateur n'est lu ni écrit. Les échecs sont écrits dans
%TEMP%\\MosaicView_fuzz_<graine>.txt (entrées en ascii()).

Les fonctions des modules qui importent Qt sont extraites par tests/ast_loader.
"""
import argparse
import io
import os
import posixpath
import random
import re
import shutil
import sys
import tarfile
import tempfile
import time
import zipfile
from urllib.parse import urljoin

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))
import ast_loader  # noqa: E402
from modules.qt import archive_names, comic_info, temp_files  # noqa: E402
from modules.qt.comicvine_scraper import _strip_html  # noqa: E402
from modules.qt.open_with_default_app_qt import _OPENABLE_EXTS, openable_temp_name  # noqa: E402

U = ast_loader.load("utils.py", ["safe_join", "windows_safe_component", "windows_safe_relpath",
                                  "unique_names", "_WINDOWS_RESERVED_NAMES",
                                  "_WINDOWS_FORBIDDEN_CHARS", "cbz_members", "MissingPageDataError"],
                    os=os, Exception=Exception)
B = ast_loader.load("batch_dialogs_qt.py", ["_flat_names", "_unique_flat_name"], os=os)
_page_attr_int = ast_loader.load("tabs_qt.py", ["_page_attr_int"])["_page_attr_int"]
WEB = ast_loader.load("web_import_qt.py", [
    "extract_images_from_js_loops", "_resolve_js_number", "_find_matching_brace",
    "_JS_CONST_NUM_RE", "_JS_CONST_STR_RE", "_JS_PAD_FN_RE", "_JS_FOR_LOOP_RE",
    "_JS_RECURSIVE_LOOP_RE", "_JS_CONCAT_TERM_RE", "_MAX_EXTRACTED_URLS", "_MAX_JS_LOOP_SPAN",
    "_MAX_JS_LOOPS", "_MAX_JS_EXPR_LENGTH", "_MAX_JS_LOOP_BODY", "_MAX_JS_EVALUATIONS"],
    _re=re, urljoin=urljoin)

# Pièces des textes générés pour les quatre dernières cibles
ATTR_PIECES = ["0", "1", "9", "12", "-", "+", " ", "\t", "\n", "_", ".", "5", "e3", "abc", "0x1f",
               "١", "٢", "¹", "²", "Ⅻ", "①", "\x00", "inf", "nan", "9" * 5000, "﻿", "　"]
JS_PIECES = ["for (let i = ", "for (var j = ", "0", "1", "N", "5000", "4999", "1000000000000", "-3",
             "; i <= ", "; i < ", "; j < ", "; i++) {", "; j++) {", "{", "}", " el.src = ", ".src=",
             "'a.jpg'", "\"b/\"", "'x' + i + '.png'", "BASE + pad(i) + '.jpg'", "pad(i + 1)",
             "pad(N)", "pad(order[i])", " + ", "i", "j", ";", "const N = 40;", "let BASE = 'img/';",
             "var K = 7;", "function pad(n){return String(n).padStart(3,'0')}",
             "let k = 0; function step() { if (k >= N) {return;} ", "k++; setTimeout(step, 10); }",
             "//", "\n", "'", "\"", "(", ")", "'http://h/'", "'javascript:x'", "'\\\\\\\\srv\\\\x'"]
HTML_PIECES = ["<", ">", "&lt;", "&gt;", "&amp;", "&amp;lt;", "&amp;gt;", "b", "img", "IMG",
               " src=x", "/", "!", "--", "?", "&nbsp;", "<br>", "<p>", "<b>", "</b>", " ", "a",
               "1", "=", "\"", "'", "\n", "&quot;", "list of covers", "<!--", "-->", "&lt;!--"]
# Extensions qu'un fichier venu d'une archive ne doit jamais recevoir à
# l'ouverture (quelques types exécutés ou interprétés par Windows), mêlées à
# celles de la liste blanche
EXT_PIECES = [".exe", ".bat", ".cmd", ".com", ".scr", ".pif", ".lnk", ".url", ".vbs", ".js", ".hta",
              ".ps1", ".msi", ".jar", ".py", ".pyc", ".reg", ".html", ".svg", ".rtf", ".cpl",
              ".chm", ".wsf", ".library-ms", ".settingcontent-ms", ".dll",
              ".txt", ".TXT", ".pdf", ".mp4", ".xml", ".nfo", ".jpg"]
TAG_RE = re.compile(r"<(?:/?[A-Za-z]|[!?])[^<>]*>")  # balise, commentaire, <!DOCTYPE>, <?…?>

PIECES = ["..", ".", "/", "\\", "C:", "c:", "Z:", "\\\\srv\\share\\", "//srv/share/", ":", "*", "?",
          "\"", "<", ">", "|", "\x00", "\n", "\r", "\t", "\x1b", "\x7f", " ", "  ", "CON", "nul",
          "Com1", "LPT9", "aux.", "a", "B", "z9", "é", "É", "ſ", "ı", "K", "‮", "﻿",
          "　", "．", "∕", "%2e%2e", "::$DATA", "~1", ".jpg", ".JPG", ".png", ".jpeg",
          ".cbz", "ComicInfo.xml", "comicinfo.XML", "_2", " (2)", "-", "_", "\U0001F600", "\ud800"]

WORK = None
failures = []


def gen_name(rng, max_pieces=8):
    return "".join(rng.choice(PIECES) for _i in range(rng.randint(0, max_pieces)))


def record(target, inp, msg):
    failures.append(f"[{target}] {msg} :: {ascii(inp)}")


def _drive_first(name):
    return len(name) >= 2 and name[1] == ":" and name[0].isascii() and name[0].isalpha()


# --- cibles -----------------------------------------------------------------

def t_contained(rng):
    name = gen_name(rng)
    try:
        out = archive_names.contained_member_name(name)
    except Exception as e:  # noqa: BLE001
        record("contained_member_name", name, f"exception {type(e).__name__}: {e}")
        return
    if out == "":
        return
    if "\\" in out or any(s in ("", ".", "..") for s in out.split("/")) or any(ord(c) < 32 for c in out):
        record("contained_member_name", name, f"segment interdit -> {ascii(out)}")
    if _drive_first(out):
        record("contained_member_name", name, f"lettre de lecteur -> {ascii(out)}")
    if archive_names.contained_member_name(out) != out:
        record("contained_member_name", name, "non idempotent")


def t_safe_join(rng):
    name = gen_name(rng)
    base = os.path.join(WORK, "safe")
    try:
        out = U["safe_join"](base, name)
    except ValueError:
        return  # caractère NUL : os.path refuse, l'appelant ne reçoit jamais de chemin
    except OSError:
        # Windows refuse certains noms bruts (« : » suivi d'un flux et de
        # caractères Unicode, WinError 1113) : aucun chemin rendu. Les appelants
        # passent un nom de windows_safe_relpath, sans « : »
        return
    except Exception as e:  # noqa: BLE001
        record("safe_join", name, f"exception {type(e).__name__}: {e}")
        return
    if out is None:
        return
    real_base = os.path.realpath(base)
    if os.path.commonpath([real_base, out]) != real_base:
        record("safe_join", name, f"hors du dossier -> {ascii(out)}")


def t_flat(rng):
    # Les lots excluent les noms finissant par « / » (dossiers)
    paths = [p for p in (gen_name(rng, 6) for _i in range(rng.randint(1, 6))) if not p.endswith("/")]
    if not paths:
        return
    try:
        flat = B["_flat_names"](paths)
        written = set()
        outs = [B["_unique_flat_name"](flat[p], written) for p in paths]
    except Exception as e:  # noqa: BLE001
        record("_flat_names", paths, f"exception {type(e).__name__}: {e}")
        return
    for p, o in zip(paths, outs):
        if o in ("", ".", "..") or "/" in o or "\\" in o or any(ord(c) < 32 for c in o):
            record("_flat_names", paths, f"nom de membre inutilisable {ascii(p)} -> {ascii(o)}")
    if len({o.lower() for o in outs}) != len(outs):
        record("_flat_names", paths, f"homonymes {ascii(outs)}")


def t_disk_export(rng):
    """windows_safe_relpath + unique_names + safe_join, puis écriture réelle sur
    le disque : autant de fichiers que de pages, chacun sous son nom, dans le
    dossier."""
    names = [gen_name(rng, 5) for _i in range(rng.randint(1, 6))]
    rels = U["unique_names"]([U["windows_safe_relpath"](n) for n in names])
    # Page « a » et page « a/b.jpg » : conflit signalé par l'export lui-même
    dirs = {"/".join(r.lower().split("/")[:k]) for r in rels for k in range(1, r.count("/") + 1)}
    if any(r.lower() in dirs for r in rels):
        return
    base = os.path.join(WORK, "export")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base)
    written = []
    for n, rel in zip(names, rels):
        if any(len(s) > 200 for s in rel.split("/")):
            continue
        dest = U["safe_join"](base, rel)
        if dest is None:
            record("export", names, f"safe_join refuse {ascii(rel)}")
            continue
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "xb") as f:  # x : un écrasement serait une collision
                f.write(n.encode("utf-8", "surrogatepass"))
        except FileExistsError:
            record("export", names, f"collision sur le disque {ascii(rel)} (tous : {ascii(rels)})")
            continue
        except OSError as e:
            record("export", names, f"écriture refusée {ascii(rel)} : {e}")
            continue
        written.append(dest)
    real_base = os.path.realpath(base)
    for dest in written:
        parent, leaf = os.path.split(dest)
        if leaf not in os.listdir(parent):
            record("export", names, f"nom transformé par Windows {ascii(leaf)} : {ascii(os.listdir(parent))}")
        if os.path.commonpath([real_base, os.path.realpath(dest)]) != real_base:
            record("export", names, f"hors du dossier {ascii(dest)}")


def t_cbz_members(rng):
    # Exclus, risques connus : NUL (zipfile coupe le nom, absent des noms de
    # fichiers Windows) et demi-caractère isolé (refusé par zipfile avec un
    # message d'erreur, aucune perte). Un nom de page n'a que des « / ».
    entries = [{"orig_name": gen_name(rng, 4).replace("\x00", "").replace("\ud800", "")
                .replace("\\", "/") or "x",
                "bytes": bytes([i])} for i in range(rng.randint(1, 6))]
    try:
        members = U["cbz_members"](entries)
    except Exception as e:  # noqa: BLE001
        record("cbz_members", [e_["orig_name"] for e_ in entries], f"exception {type(e).__name__}: {e}")
        return
    names = [m for m, _e in members]
    if len(set(names)) != len(names):
        record("cbz_members", names, "homonymes")
        return
    buf = io.BytesIO()
    try:
        with zipfile.ZipFile(buf, "w") as zf:
            for m, e in members:
                zf.writestr(m, e["bytes"])
    except Exception as e:  # noqa: BLE001
        record("cbz_members", names, f"zipfile refuse : {type(e).__name__}: {e}")
        return
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        got = [zf.read(i) for i in zf.infolist()]
    if got != [e["bytes"] for _m, e in members]:
        record("cbz_members", names, "contenu relu différent")


def t_zip_member_name(rng):
    name = bytes(rng.randrange(256) for _i in range(rng.randint(0, 12))).decode("cp437")
    try:
        out = archive_names.zip_member_name(name)
    except Exception as e:  # noqa: BLE001
        record("zip_member_name", name, f"exception {type(e).__name__}: {e}")
        return
    # Les caractères ASCII (dont « / », « \\ », « . ») ne changent jamais
    if [c for c in name if c.isascii()] != [c for c in out if c.isascii()]:
        record("zip_member_name", name, f"caractères ASCII modifiés -> {ascii(out)}")


def t_tar_bytes(rng):
    raw = bytes(rng.randrange(32, 256) for _i in range(rng.randint(1, 12)))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT, encoding="latin-1") as tf:
        tf.addfile(tarfile.TarInfo("p_" + raw.decode("latin-1")), io.BytesIO(b""))
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r") as tf:
        name = tf.getmembers()[0].name
    out = archive_names.tar_entry_name(name)
    try:
        zipfile.ZipFile(io.BytesIO(), "w").writestr(out or "_", b"")
    except Exception as e:  # noqa: BLE001
        record("tar_bytes", raw, f"{type(e).__name__}: {e}")


def t_misc(rng):
    name = gen_name(rng)
    out = temp_files.log_text(name)
    if any(ord(c) < 32 for c in out):
        record("log_text", name, f"contrôle restant {ascii(out)}")
    t = archive_names.tar_entry_name(name)
    if t.startswith(("/", "./")) or archive_names.tar_entry_name(t) != t:
        record("tar_entry_name", name, f"préfixe restant ou non idempotent {ascii(t)}")
    fmt = rng.choice(["JPEG", "PNG", "WEBP", "GIF", "TIFF", "BMP", "AVIF", None, "EPS"])
    # Un nom de page n'a que des « / » (normalisés par les lecteurs d'archives)
    page = name.replace("\\", "/")
    r = archive_names.name_for_real_format(page, fmt)
    if posixpath.dirname(r) != posixpath.dirname(page):
        record("name_for_real_format", (page, fmt), f"dossier modifié {ascii(r)}")
    # Un seul ComicInfo.xml après l'arrivée de fichiers
    new = [{"orig_name": rng.choice(["ComicInfo.xml", "comicinfo.xml", "COMICINFO.XML",
                                     "ComicInfo_0002.xml", "a.jpg"])} for _i in range(rng.randint(0, 5))]
    old = [{"orig_name": rng.choice(["ComicInfo.xml", "ComicInfo_0002.xml", "b.jpg"])}
           for _i in range(rng.randint(0, 3))]
    comic_info.name_incoming_comic_infos(new, old)
    before = sum(comic_info.is_comic_info_entry(e) for e in old)
    if sum(comic_info.is_comic_info_entry(e) for e in new + old) > max(1, before):
        record("name_incoming_comic_infos", (new, old), "plusieurs ComicInfo.xml")


def gen_text(rng, pieces, max_pieces):
    """Assemblage de pièces ; une fois sur trois, une pièce répétée jusqu'à
    3000 fois (page ou description démesurée)."""
    parts = [rng.choice(pieces) for _i in range(rng.randint(0, max_pieces))]
    if parts and rng.random() < 0.3:
        k = rng.randrange(len(parts))
        parts[k] = parts[k] * rng.randint(1, 3000)
    return "".join(parts)


def t_page_attr_int(rng):
    value = None if rng.random() < 0.05 else gen_text(rng, ATTR_PIECES, 6)
    try:
        out = _page_attr_int(value)
    except Exception as e:  # noqa: BLE001
        record("_page_attr_int", value, f"exception {type(e).__name__}: {e}")
        return
    if type(out) is not int:
        record("_page_attr_int", value, f"valeur non entière {ascii(out)}")


def t_js_loops(rng):
    """Durée bornée, adresses http(s) distinctes, jamais plus que la limite."""
    page = gen_text(rng, JS_PIECES, 40)
    start = time.monotonic()
    try:
        urls = WEB["extract_images_from_js_loops"]("https://ex.com/p/", page)
    except Exception as e:  # noqa: BLE001
        record("extract_images_from_js_loops", page[:2000], f"exception {type(e).__name__}: {e}")
        return
    elapsed = time.monotonic() - start
    if elapsed > 3:
        record("extract_images_from_js_loops", page[:2000], f"durée {elapsed:.1f} s ({len(page)} caractères)")
    if len(urls) > WEB["_MAX_EXTRACTED_URLS"]:
        record("extract_images_from_js_loops", page[:2000], f"{len(urls)} adresses")
    if len(set(urls)) != len(urls) or not all(u.startswith(("http://", "https://")) for u in urls):
        record("extract_images_from_js_loops", page[:2000], f"adresses en double ou hors http(s) {ascii(urls[:5])}")


def t_strip_html(rng):
    text = gen_text(rng, HTML_PIECES, 30)
    start = time.monotonic()
    try:
        out = _strip_html(text)
    except Exception as e:  # noqa: BLE001
        record("_strip_html", text[:2000], f"exception {type(e).__name__}: {e}")
        return
    if time.monotonic() - start > 1:
        record("_strip_html", text[:2000], f"durée {time.monotonic() - start:.1f} s")
    m = TAG_RE.search(out)
    if m:
        record("_strip_html", text[:2000], f"balise en sortie {ascii(m.group(0))}")


def t_openable(rng):
    """Aucun nom accepté ne reçoit une autre extension que celles de la liste
    blanche, ni à la lecture du nom, ni une fois écrit sur le disque (Windows
    retire un point ou un espace final, lit « : » comme un flux)."""
    name = gen_text(rng, PIECES + EXT_PIECES * 3, 8)
    try:
        rel = openable_temp_name(name)
    except Exception as e:  # noqa: BLE001
        record("openable_temp_name", name, f"exception {type(e).__name__}: {e}")
        return
    if rel is None:
        return
    if os.path.splitext(rel)[1].lower() not in _OPENABLE_EXTS:
        record("openable_temp_name", name, f"extension refusée acceptée -> {ascii(rel)}")
        return
    base = os.path.join(WORK, "open")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base)
    dest = U["safe_join"](base, rel)
    if dest is None:
        record("openable_temp_name", name, f"safe_join refuse {ascii(rel)}")
        return
    if len(dest) > 240:
        return  # chemin trop long pour Windows : écriture refusée, rien d'ouvert
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(b"x")
    except OSError as e:
        record("openable_temp_name", name, f"écriture refusée {ascii(rel)} : {e}")
        return
    parent, leaf = os.path.split(dest)
    written = os.listdir(parent)
    if leaf not in written:
        record("openable_temp_name", name, f"nom transformé par Windows {ascii(leaf)} : {ascii(written)}")
    elif any(os.path.splitext(w)[1].lower() not in _OPENABLE_EXTS for w in written):
        record("openable_temp_name", name, f"fichier écrit hors liste blanche {ascii(written)}")


TARGETS = [("contained_member_name", t_contained), ("safe_join", t_safe_join),
           ("_flat_names + _unique_flat_name", t_flat), ("export sur le disque", t_disk_export),
           ("cbz_members", t_cbz_members), ("zip_member_name", t_zip_member_name),
           ("log_text / tar_entry_name / format / ComicInfo", t_misc), ("noms TAR en octets", t_tar_bytes),
           ("_page_attr_int", t_page_attr_int), ("extract_images_from_js_loops", t_js_loops),
           ("_strip_html", t_strip_html), ("openable_temp_name", t_openable)]


def main():
    global WORK
    parser = argparse.ArgumentParser(description="Fuzzing des fonctions de noms et de chemins de MosaicView")
    parser.add_argument("--secondes", type=float, default=15.0, help="durée par cible (15 par défaut)")
    parser.add_argument("--graine", type=int, default=None, help="rejouer une campagne (graine affichée)")
    args = parser.parse_args()
    seed = args.graine if args.graine is not None else random.SystemRandom().randrange(10 ** 9)
    print(f"graine {seed} (pour rejouer : --graine {seed})")
    WORK = tempfile.mkdtemp(prefix="MosaicView_fuzz_")
    start = time.monotonic()
    try:
        for i, (label, fn) in enumerate(TARGETS):
            rng = random.Random(seed * 100 + i)
            before = len(failures)
            count = 0
            end = time.monotonic() + args.secondes
            while time.monotonic() < end:
                fn(rng)
                count += 1
            print(f"{label} : {count} entrées, {len(failures) - before} échecs")
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    print(f"total {time.monotonic() - start:.1f} s")
    if not failures:
        print("aucun échec")
        return 0
    report = os.path.join(tempfile.gettempdir(), f"MosaicView_fuzz_{seed}.txt")
    with open(report, "w", encoding="utf-8") as out:
        out.write("\n".join(failures))
    seen = {}
    for f in failures:
        seen.setdefault(f.split(" :: ")[0][:90], f)
    for f in list(seen.values())[:40]:
        print(ascii(f)[:400])
    print(f"{len(failures)} échecs, détail dans {report}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
