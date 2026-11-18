"""
modules/qt/archive_members.py
Lecture des membres d'une archive (CBZ/EPUB, CBR, CB7, CBT), sans Qt.

Lecteurs communs à tous les chemins de lecture : ouverture et ajout d'une
archive (archive_loader, import_merge_qt), conversions et recompression par
lot, import de métadonnées en lot, Bibliothèque, ComicInfo.xml. Chacun
applique la limite de taille d'un membre (MAX_MEMBER_SIZE, archive_names) et
lit chaque homonyme à son tour.
"""

import os
import sys
import shutil
import tarfile
import zipfile
import zlib
import rarfile
import posixpath
import subprocess
import tempfile
import threading

from modules.qt.archive_names import MAX_ARCHIVE_MEMBERS, MAX_MEMBER_SIZE
from modules.qt.helper_processes import keep_rarfile_helpers_with_app, start_helper

# 7z et UnRAR, lancés ici (start_helper) ou par rarfile, ne survivent jamais à
# MosaicView : sans cela, une extraction en cours continuerait seule après sa
# fin, sans plus personne pour surveiller ce qu'elle écrit (helper_processes)
keep_rarfile_helpers_with_app()

# Plafonds de la liste des membres d'un CB7 (`7z l -slt`, environ 350 octets
# par membre plus son nom), lue au fil de l'eau : cette liste est compressée
# dans l'archive, et un CB7 de quelques Mo peut annoncer des millions de
# fichiers ou un nom démesuré. 256 Mo correspondent à plus de 600 000 membres ;
# un chemin Windows ne dépasse pas 32 767 caractères, soit au plus 96 Ko en
# UTF-8.
_LIST_7Z_MAX_BYTES = 256 * 1024 * 1024
_LIST_7Z_MAX_LINE = 128 * 1024


def _get_7z_exe():
    """Retourne le chemin vers 7z.exe embarqué (compatible PyInstaller).
    Hors exe compilé : dossier du projet, déduit de l'emplacement de ce module,
    jamais du dossier courant, où un 7z.exe étranger serait lancé."""
    try:
        base = sys._MEIPASS
    except AttributeError:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "7zip", "7z.exe")


def _to_short_path(path):
    """Convertit un chemin Unicode en chemin court 8.3 pour 7z.exe (Windows)."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024)
        return buf.value or path
    except Exception:
        return path


def _is_7z_file_block(path, attributes):
    """Un bloc de `7z l -slt` décrit un fichier (et non un dossier) : tout
    membre dont les attributs Windows ne contiennent pas D. Jamais une égalité
    avec "A" : un fichier en lecture seule (RA), caché (HA), sans bit archive
    (N) ou venant d'Unix (A_ -rw-r--r--) serait ignoré sans erreur, et une
    conversion supprimerait ensuite l'archive d'origine sans ces pages."""
    if path is None or path.endswith(("/", "\\")):
        return False
    if not attributes:
        return True
    return "D" not in attributes.split(" ")[0]


def _is_7z_dir_block(path, attributes):
    """Un bloc de `7z l -slt` décrit un dossier : attribut Windows D, ou
    chemin terminé par une barre."""
    if path is None:
        return False
    if path.endswith(("/", "\\")):
        return True
    return bool(attributes) and "D" in attributes.split(" ")[0]


def _drain(stream):
    """Lit et jette `stream` jusqu'à sa fin : sortie d'erreur d'un programme
    dont seul le code de sortie compte. Non lue, un tube plein le bloquerait ;
    lue d'un coup, elle n'aurait pas de limite de taille."""
    while stream.read(65536):
        pass


def _check_member_count(count):
    """Lève ArchiveListRefusedError si une archive a plus de
    MAX_ARCHIVE_MEMBERS membres (fichiers et dossiers). Appelée pendant la
    lecture de la liste quand elle se fait au fil de l'eau (7z, TAR), pour
    s'arrêter avant d'en avoir tout chargé."""
    if count > MAX_ARCHIVE_MEMBERS:
        from modules.qt.archive_names import ArchiveListRefusedError
        raise ArchiveListRefusedError(
            f"too many files in the archive: more than {MAX_ARCHIVE_MEMBERS}")


def _list_7z_members(archive_path):
    """(fichiers, dossiers) d'une archive 7z, chemins complets séparés par
    « / » ; chaque dossier, vide ou non, terminé par « / » (comme dans un ZIP
    ou un RAR). Une seule lecture de la liste par 7z.exe. Lève une exception
    pour tout code de sortie autre que 0, comme la lecture d'un membre
    (_read_7z_file) : 7-Zip n'en renvoie pas d'autre pour une liste complète,
    ses avertissements (données après la fin de l'archive, en-tête retrouvé)
    sortant en 0, et une conversion qui accepterait une liste douteuse
    supprimerait ensuite la source.

    La liste est lue ligne par ligne, jamais capturée d'un coup, et refusée
    (ArchiveListRefusedError, 7z arrêté) au-delà de _LIST_7Z_MAX_BYTES, d'une
    ligne de _LIST_7Z_MAX_LINE ou de MAX_ARCHIVE_MEMBERS membres : capturée
    entière puis découpée, la liste d'un CB7 piégé de quelques Mo occupait
    plusieurs Go de mémoire."""
    from modules.qt.archive_names import ArchiveListRefusedError, ArchiveRefusedError
    exe = _get_7z_exe()
    short_path = _to_short_path(archive_path)
    proc = start_helper(
        # -sccUTF-8 : sans lui, 7z écrit dans la page de code console (OEM) et les noms
        # non-ASCII décodés en UTF-8 deviennent des U+FFFD, introuvables ensuite à l'extraction
        [exe, "l", "-ba", "-slt", "-sccUTF-8", short_path],
        # Entrée standard vide : 7z y attendrait sinon le mot de passe d'une
        # archive aux noms chiffrés, et l'ouverture ne finirait jamais
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    stderr_reader = threading.Thread(target=_drain, args=(proc.stderr,), daemon=True)
    stderr_reader.start()
    names = []
    dirs = []

    def _end_of_block(path, attributes):
        if _is_7z_file_block(path, attributes):
            names.append(path.replace('\\', '/'))
        elif _is_7z_dir_block(path, attributes):
            folder = path.replace('\\', '/').strip('/')
            if folder:
                dirs.append(folder + '/')
        _check_member_count(len(names) + len(dirs))

    current_path = None
    current_attr = None
    total = 0
    try:
        while True:
            raw = proc.stdout.readline(_LIST_7Z_MAX_LINE + 1)
            if not raw:
                break
            total += len(raw)
            if len(raw) > _LIST_7Z_MAX_LINE:
                raise ArchiveListRefusedError(
                    f"name too long in the archive: more than {_LIST_7Z_MAX_LINE} bytes")
            if total > _LIST_7Z_MAX_BYTES:
                raise ArchiveListRefusedError(
                    f"too many files in the archive: list larger than {_LIST_7Z_MAX_BYTES} bytes")
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                # Fin d'un bloc : fichier ou dossier
                _end_of_block(current_path, current_attr)
                current_path = None
                current_attr = None
            elif line.startswith("Path = "):
                current_path = line[7:]
            elif line.startswith("Attributes = "):
                current_attr = line[13:]
    except BaseException:
        proc.kill()
        raise
    finally:
        proc.wait()
        stderr_reader.join()
        proc.stdout.close()
        proc.stderr.close()
    if proc.returncode != 0:
        raise ArchiveRefusedError(f"7z returncode {proc.returncode}: not a valid 7z archive")
    # Dernier bloc sans ligne vide finale
    _end_of_block(current_path, current_attr)
    return names, dirs


class AlteredMemberError(Exception):
    """Membre d'archive dont la somme de contrôle (CRC) ne correspond plus à
    ses octets : ils ont changé depuis la création de l'archive (disque
    défaillant, copie abîmée). Ses octets ont quand même été récupérés
    (`data`) ; le message est celui du lecteur.

    Hérite d'Exception : un appelant qui ne la traite pas (conversions par
    lot, Bibliothèque, import de métadonnées en lot) refuse le membre comme
    toute erreur de lecture. Seules l'ouverture et l'ajout d'une archive
    affichent la page si ses octets se décodent (create_page_entry)."""

    # Fichier abîmé, pas un défaut du programme : jamais écrite dans le
    # journal des erreurs (error_log.is_expected_error)
    expected_error = True

    def __init__(self, message, data):
        super().__init__(message)
        self.data = data


def _run_and_read(cmd):
    """Lance `cmd` et rend (code de sortie, sortie standard, sortie d'erreur
    en texte).

    La sortie est lue par blocs et le programme arrêté dès qu'elle dépasse
    MAX_MEMBER_SIZE (_check_member_size lève ValueError) : capturée d'un
    coup, une page de quelques Ko qui se décompresse en plusieurs Go
    épuiserait la mémoire. La sortie d'erreur est lue par un thread : un
    tube plein bloquerait le programme, et donc la lecture de la sortie.
    Entrée standard vide : lancé depuis un terminal, 7z y attendrait sinon le
    mot de passe d'une archive chiffrée, et la lecture ne finirait jamais."""
    proc = start_helper(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    stderr_chunks = []
    stderr_reader = threading.Thread(
        target=lambda: stderr_chunks.append(proc.stderr.read()), daemon=True)
    stderr_reader.start()
    chunks = []
    total = 0
    try:
        while True:
            chunk = proc.stdout.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            _check_member_size(total)
            chunks.append(chunk)
    except BaseException:
        proc.kill()
        raise
    finally:
        proc.wait()
        stderr_reader.join()
        proc.stdout.close()
        proc.stderr.close()
    stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
    return proc.returncode, b"".join(chunks), stderr


def _read_7z_file(archive_path, member_name):
    """Extrait un fichier d'une archive 7z et retourne ses bytes via stdout
    (_run_and_read : taille limitée à MAX_MEMBER_SIZE).

    « CRC Failed » : 7z a envoyé les octets du membre, puis constaté qu'ils ne
    correspondent plus à sa somme de contrôle (AlteredMemberError, octets
    joints). Toute autre erreur, dont « Data Error » (données compressées
    détruites, sortie incomplète), lève RuntimeError."""
    exe = _get_7z_exe()
    short_path = _to_short_path(archive_path)
    # Chemin complet, sans -r : avec un nom seul + -r, 7z extrait TOUS les membres
    # de même nom (A/p.jpg et B/p.jpg) concaténés sur stdout, et PIL décode alors
    # silencieusement le premier pour chacun. Sans -r, un nom seul ne matche que la racine.
    # -spd : un nom contenant * ou ? (archive créée hors de Windows, ou nom abîmé
    # par une erreur de codage) serait sinon un joker, et la page recevrait les
    # contenus de tous les membres qu'il désigne, mis bout à bout.
    member_path = member_name.replace('\\', '/')
    returncode, data, stderr = _run_and_read(
        [exe, "e", "-so", "-spd", short_path, "--", member_path])
    if returncode != 0:
        if "CRC Failed" in stderr and data:
            raise AlteredMemberError(stderr, data)
        from modules.qt.archive_names import ArchiveToolError
        raise ArchiveToolError(stderr)
    return data


def _zip_info_read(archive, info):
    """Octets du membre ZIP `info` (ZipInfo, jamais un nom : read(nom) rend le
    dernier homonyme), après contrôle de sa taille annoncée.

    Somme de contrôle fausse : le membre est relu sans ce contrôle et ses
    octets sont joints à AlteredMemberError."""
    _check_member_size(info.file_size)
    try:
        return archive.read(info)
    except zipfile.BadZipFile as e:
        # zipfile : « Bad CRC-32 for file … », levée une fois tous les
        # octets décompressés
        if "CRC" not in str(e):
            raise
        try:
            with archive.open(info) as src:
                src._expected_crc = None
                data = src.read()
        except Exception:
            raise e
        raise AlteredMemberError(str(e), data) from e


# Limite de taille d'un membre lu en mémoire : MAX_MEMBER_SIZE (archive_names,
# source unique). La taille reçue pendant l'extraction d'un CB7 est contrôlée
# par _read_7z_file. Un membre trop grand devient une page illisible (cadre
# rouge), que l'enregistrement refuse d'omettre.
def _check_member_size(size):
    """Lève MemberTooLargeError (ArchiveRefusedError, donc ValueError) si
    `size` (octets annoncés par l'archive) dépasse MAX_MEMBER_SIZE. Message
    sans mots (« 4,20 Go > 500,0 Mo ») ; sa raison brute, en octets, devient
    celle de la page illisible (create_page_entry)."""
    if size is not None and size > MAX_MEMBER_SIZE:
        from modules.qt.archive_names import MemberTooLargeError
        raise MemberTooLargeError(size, MAX_MEMBER_SIZE)


def _rar_read(archive, name):
    """archive.read(name) pour un RarFile, après contrôle de la taille annoncée.

    Somme de contrôle fausse : rarfile ne rend aucun octet (même avec
    crc_check=False) ; le membre est relu par UnRAR, qui les garde avec -kb
    (_rar_bytes_without_crc), et ses octets sont joints à AlteredMemberError.

    Nom porté par plusieurs fichiers de l'archive : jamais lu ici
    (ArchiveRefusedError). rarfile retrouve un membre par son nom, même
    quand on lui passe son RarInfo (CommonParser.getinfo relit
    member.filename), et UnRAR aussi : chacun recevrait le contenu du
    dernier, le premier serait perdu à l'enregistrement, et une conversion
    supprimerait ensuite le CBR. Le lecteur commun lit ces fichiers à part
    (ArchiveReader : _RarExtraction)."""
    if archive.namelist().count(name) > 1:
        from modules.qt.archive_names import ArchiveRefusedError
        raise ArchiveRefusedError(f"{name}: several files of the RAR archive have this name")
    _check_member_size(archive.getinfo(name).file_size)
    try:
        return archive.read(name)
    except (rarfile.BadRarFile, rarfile.RarCRCError) as e:
        if not isinstance(e, rarfile.RarCRCError) and "CRC" not in str(e):
            raise
        try:
            data = _rar_bytes_without_crc(archive.filename, name)
        except Exception:
            raise e
        raise AlteredMemberError(str(e), data) from e


def _rar_bytes_without_crc(archive_path, name):
    """Octets du membre `name` d'un RAR, extraits par UnRAR sans tenir compte
    de sa somme de contrôle (-kb : garder les fichiers abîmés), taille
    limitée (_run_and_read). Code de sortie 3 : somme de contrôle fausse.
    Un nom contenant * ou ? n'est pas relu : UnRAR le prendrait pour un
    joker et rendrait les contenus de plusieurs membres mis bout à bout."""
    from modules.qt.archive_names import ArchiveRefusedError, ArchiveToolError
    if any(c in name for c in "*?") or not archive_path:
        raise ArchiveRefusedError(name)
    returncode, data, stderr = _run_and_read(
        [rarfile.UNRAR_TOOL, "p", "-inul", "-kb", "--", archive_path, name])
    if returncode not in (0, 3) or not data:
        raise ArchiveToolError(stderr or f"UnRAR {returncode}")
    return data


def _tar_info_read(archive, member):
    """Octets du membre TAR `member` (TarInfo, jamais un nom :
    archive.getmember(nom) rend le dernier membre de ce nom, lien symbolique
    compris), après contrôle de sa taille."""
    _check_member_size(member.size)
    return archive.extractfile(member).read()


def first_page(archive_path):
    """(nom, octets) de la première page d'une archive : celle que montre son
    ouverture dans un panneau. Première image dans l'ordre des noms de
    l'ouverture (archive_names.natural_key) ; pour un EPUB, première image
    dans l'ordre de lecture du livre (epub_reading_order). Format d'après le
    contenu (detect_archive_type), jamais d'après l'extension. None si
    l'archive n'a aucune image ou n'est pas une archive reconnue.

    Couverture de la Bibliothèque, de l'import de métadonnées, vignette de la
    recompression : une règle à part y montrait une autre page que la
    première (« 10.jpg » avant « 2.jpg », dossier ignoré). Lue par les
    lecteurs communs : taille limitée à MAX_MEMBER_SIZE, premier membre d'un
    nom porté par plusieurs (celui que l'ouverture montre en premier), liens
    d'un TAR écartés. Une erreur de lecture remonte : à l'appelant de
    décider (vignette vide, fichier compté en erreur)."""
    from modules.qt.archive_type_detector import detect_archive_type
    kind = detect_archive_type(archive_path)
    if kind is None:
        return None
    with open_archive(archive_path, kind) as archive:
        pages = archive.epub_pages() if kind == "EPUB" else archive.images()
        if not pages:
            return None
        return pages[0].name, archive.read(pages[0])


# ═══════════════════════════════════════════════════════════════════════════════
# Lecteur d'archive commun : liste et lecture des membres, mêmes règles partout
# ═══════════════════════════════════════════════════════════════════════════════

ARCHIVE_KINDS = ("CBZ", "EPUB", "CBR", "CB7", "CBT")

_KIND_OF_EXTENSION = {".cbz": "CBZ", ".zip": "CBZ", ".epub": "EPUB", ".cbr": "CBR",
                      ".cb7": "CB7", ".cbt": "CBT"}


def _handle_comment(handle, kind):
    """Commentaire de l'archive ouverte `handle` (zipfile.ZipFile ou
    rarfile.RarFile) en octets ; b"" pour un autre format."""
    if handle is None:
        return b""
    if kind in ("CBZ", "EPUB"):
        return handle.comment or b""
    if kind == "CBR":
        # rarfile rend le commentaire en texte ; un commentaire de ZIP est en
        # octets, lus en UTF-8 par les logiciels qui y rangent des
        # métadonnées (ComicBookInfo)
        return (handle.comment or "").encode("utf-8")
    return b""


def archive_comment(path):
    """Commentaire d'archive du fichier `path`, en octets (b"" s'il n'y en a
    pas, si le format n'en a pas — 7z, TAR — ou si le fichier ne s'ouvre pas).
    Format d'après le contenu (detect_archive_type), jamais d'après
    l'extension.

    Ce texte libre porte parfois des métadonnées (ComicBookInfo, écrit par
    ComicRack ou ComicTagger, aussi dans un CBR) : toute réécriture d'un CBZ
    le recopie, et toute conversion d'un CBR en CBZ le reprend, sinon elles
    seraient perdues avec la source, supprimée ensuite."""
    from modules.qt.archive_type_detector import detect_archive_type
    try:
        kind = detect_archive_type(path)
        if kind in ("CBZ", "EPUB"):
            with zipfile.ZipFile(path) as handle:
                return _handle_comment(handle, kind)
        if kind == "CBR":
            with rarfile.RarFile(path, "r") as handle:
                return _handle_comment(handle, kind)
    except (OSError, zipfile.BadZipFile, rarfile.Error, ValueError, TypeError):
        return b""   # repli : archive illisible, sans commentaire à reprendre
    return b""


def kind_from_extension(path):
    """Format annoncé par l'extension de `path` (« CBZ », « EPUB », « CBR »,
    « CB7 », « CBT »), None sinon. Le format réel se lit dans le contenu
    (archive_type_detector.detect_archive_type)."""
    return _KIND_OF_EXTENSION.get(os.path.splitext(path)[1].lower())


class ArchiveMember:
    """Un fichier ou un dossier d'une archive, tel que tous les lecteurs le
    voient.

    name   : chemin dans l'archive, « / » comme séparateur ; sans le « ./ »
             initial ni le « / » initial d'un TAR (archive_names.tar_entry_name,
             nom relu dans son codage) ; un dossier se termine par « / ».
    is_dir : dossier, vide ou non (jamais lu).
    size   : taille annoncée par l'archive (None : inconnue avant lecture, CB7).
    ref    : ce qui désigne le membre pour sa lecture (ZipInfo, nom RAR,
             TarInfo, nom 7z) — jamais un nom pour un ZIP ou un TAR, qui
             rendrait le dernier homonyme."""

    __slots__ = ("name", "is_dir", "size", "ref")

    def __init__(self, name, is_dir, size, ref):
        self.name = name
        self.is_dir = is_dir
        self.size = size
        self.ref = ref

    def __repr__(self):
        return f"ArchiveMember({self.name!r})"


class ArchiveReader:
    """Archive ouverte (open_archive) : la liste de ses membres et leur
    lecture, avec les règles de tous les lecteurs (ouverture et ajout d'une
    BD, lots, import de métadonnées, Bibliothèque, ComicInfo.xml).

    members : fichiers et dossiers (vides compris), dans l'ordre de
    l'ouverture d'une BD (archive_names.natural_key du nom ; deux membres de
    même nom gardent l'ordre de l'archive, le premier est celui que
    l'ouverture montre en premier). Fichiers seuls pour un TAR (ni lien
    symbolique ni lien physique, qui désignent un autre membre ou un chemin
    hors de l'archive), racine « . » exclue.

    read(membre) : ses octets, par le lecteur du format — taille limitée à
    MAX_MEMBER_SIZE (annoncée avant lecture, reçue pour un CB7), somme de
    contrôle fausse signalée par AlteredMemberError (octets joints), nom
    7z ou RAR jamais pris pour un joker.

    Une archive illisible lève son exception à l'ouverture (zipfile.BadZipFile,
    rarfile.NotRarFile…, ArchiveRefusedError pour un 7z, ou pour une archive
    de plus de MAX_ARCHIVE_MEMBERS membres : _check_member_count), une page
    illisible à sa lecture : à l'appelant de décider (page à cadre rouge, archive refusée,
    lot en erreur)."""

    def __init__(self, path, kind):
        if kind not in ARCHIVE_KINDS:
            from modules.qt.archive_names import ArchiveRefusedError
            raise ArchiveRefusedError(f"unknown archive format: {kind}")
        self.path = path
        self.kind = kind
        self._handle = None
        # CBR dont un nom est porté par plusieurs fichiers : ces fichiers,
        # extraits à la première lecture de l'un d'eux (_RarExtraction), ou
        # l'erreur de cette extraction
        self._rar_extraction = None
        try:
            self.members = self._list(path, kind)
        except BaseException:
            # Archive refermée : sans quoi le fichier resterait ouvert, et
            # Windows refuserait de le renommer ou de le supprimer
            self.close()
            raise

    def _list(self, path, kind):
        members = []
        if kind in ("CBZ", "EPUB"):
            from modules.qt.archive_names import open_zip
            self._handle = open_zip(path)
            # Liste entière déjà chargée par zipfile, non compressée dans
            # l'archive : sa taille suit celle du fichier
            _check_member_count(len(self._handle.infolist()))
            for info in self._handle.infolist():
                members.append(ArchiveMember(info.filename, info.is_dir(), info.file_size, info))
        elif kind == "CBR":
            self._handle = rarfile.RarFile(path, "r")
            infos = self._handle.infolist()
            _check_member_count(len(infos))
            counts = {}
            for info in infos:
                if not info.is_dir():
                    counts[info.filename] = counts.get(info.filename, 0) + 1
            for info in infos:
                name = info.filename
                if info.is_dir() and not name.endswith("/"):
                    name += "/"
                # Nom porté par plusieurs fichiers : désigné par son RarInfo,
                # lu par l'extraction de l'archive (_read_rar_homonym) ; tout
                # autre fichier par son nom (_rar_read)
                homonym = not info.is_dir() and counts[info.filename] > 1
                members.append(ArchiveMember(name, info.is_dir(), info.file_size,
                                             info if homonym else info.filename))
        elif kind == "CB7":
            files, dirs = _list_7z_members(path)
            members.extend(ArchiveMember(n, False, None, n) for n in files)
            members.extend(ArchiveMember(n, True, None, None) for n in dirs)
        else:
            from modules.qt.archive_names import tar_entry_name
            self._handle = tarfile.open(path, "r:*")
            # Membre par membre, jamais getmembers() : les en-têtes d'un TAR
            # compressé le sont aussi, et des millions d'en-têtes vides
            # tiennent en quelques Ko. Arrêt dès que la limite est dépassée.
            for count, m in enumerate(self._handle, 1):
                _check_member_count(count)
                if m.isfile():
                    members.append(ArchiveMember(tar_entry_name(m.name), False, m.size, m))
                elif m.isdir():
                    folder = tar_entry_name(m.name).strip("/")
                    if folder not in ("", "."):
                        members.append(ArchiveMember(folder + "/", True, None, None))
        from modules.qt.archive_names import natural_key
        return sorted(members, key=lambda m: natural_key(m.name))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        handle, self._handle = self._handle, None
        if handle is not None:
            handle.close()
        extraction, self._rar_extraction = self._rar_extraction, None
        if isinstance(extraction, _RarExtraction):
            extraction.close()

    def comment(self):
        """Commentaire de l'archive (octets, b"" s'il n'y en a pas), comme
        archive_comment : ZIP/EPUB et RAR seulement, 7z et TAR n'en ont pas."""
        return _handle_comment(self._handle, self.kind)

    def files(self):
        """Fichiers (dossiers exclus), dans l'ordre de members."""
        return [m for m in self.members if not m.is_dir]

    def dirs(self):
        """Dossiers, vides compris, dans l'ordre de members."""
        return [m for m in self.members if m.is_dir]

    def images(self):
        """Fichiers d'image (archive_names.IMAGE_EXTS), dans l'ordre de members."""
        from modules.qt.archive_names import IMAGE_EXTS
        return [m for m in self.files() if os.path.splitext(m.name)[1].lower() in IMAGE_EXTS]

    def epub_pages(self):
        """Images d'un EPUB dans l'ordre de lecture du livre
        (archive_names.epub_reading_order) ; deux images de même nom dans
        l'ordre de l'archive."""
        from modules.qt.archive_names import epub_reading_order
        images = self.images()
        pools = {}
        for m in images:
            pools.setdefault(m.name, []).append(m)
        order = epub_reading_order(self._handle, [m.name for m in images])
        return [pools[name].pop(0) for name in order if pools.get(name)]

    def read(self, member):
        """Octets du fichier `member` (voir la classe)."""
        if member.is_dir:
            raise IsADirectoryError(member.name)
        if self.kind in ("CBZ", "EPUB"):
            return _zip_info_read(self._handle, member.ref)
        if self.kind == "CBR":
            if not isinstance(member.ref, str):
                return self._read_rar_homonym(member.ref)
            return _rar_read(self._handle, member.ref)
        if self.kind == "CB7":
            return _read_7z_file(self.path, member.ref)
        return _tar_info_read(self._handle, member.ref)


    def _read_rar_homonym(self, info):
        """Octets d'un fichier du CBR dont le nom est porté par plusieurs
        fichiers (`info` : son RarInfo). rarfile et UnRAR ne désignent un
        fichier que par son nom, et rendent le même contenu pour chacun :
        les fichiers de même nom sont extraits une fois (_RarExtraction, à
        la première lecture de l'un d'eux), et chacun y est retrouvé par son
        contenu. Une extraction impossible ou refusée (place, taille) rend
        ces fichiers illisibles (son erreur, à chaque lecture) ; les autres
        fichiers de l'archive sont lus normalement."""
        if self._rar_extraction is None:
            try:
                self._rar_extraction = _RarExtraction(self.path, self._handle.infolist())
            except Exception as e:
                self._rar_extraction = e
        if isinstance(self._rar_extraction, Exception):
            raise self._rar_extraction
        return self._rar_extraction.read(info)


class _RarExtraction:
    """Les fichiers de même nom d'un CBR, extraits par UnRAR dans un dossier
    temporaire vide (MosaicViewTemp, supprimé par close(), sinon au nettoyage
    des temporaires), pour les lire séparément. Les autres fichiers de
    l'archive ne sont pas extraits : ils sont lus un à un par _rar_read.

    Leurs chemins sont donnés à UnRAR par un fichier de liste (-n@, en
    UTF-16 : -scul), jamais en arguments : un nom passé en argument n'extrait
    que le premier des fichiers qui le portent, et un nom commençant par « @ »
    y serait pris pour un fichier de liste. Un chemin de la liste ne désigne
    que les fichiers de ce chemin exact, homonymes compris.

    UnRAR x -or renomme un fichier dont le nom est déjà pris (« p.jpg »,
    « p(1).jpg »…), mais selon les collisions qu'il rencontre : un vrai
    fichier « p(1).jpg » de l'archive deviendrait « p(1)(1).jpg ». Le nom
    produit ne dit donc pas de quel fichier il s'agit : chacun est retrouvé
    par son contenu (taille et somme de contrôle CRC32 de son RarInfo), dans
    le dossier de son chemin.

    Refusé avant toute écriture (ArchiveRefusedError) : un fichier annoncé à
    plus de MAX_MEMBER_SIZE, un nom qui sortirait du dossier d'extraction
    (chemin absolu, lettre de lecteur, segment « .. ») ou qu'UnRAR lirait
    comme un joker ou comme plusieurs lignes de la liste, et une extraction
    qui ne laisserait pas FREE_RESERVE de place libre sur le disque du
    dossier temporaire (_expected_size). Arrêtée pendant le travail d'UnRAR
    (_run_extraction) si elle dépasse la taille prévue ou entame cette
    réserve. UnRAR est lancé sans entrée standard et sans mot de passe
    (-p-) : une archive chiffrée échoue aussitôt. Seuls des fichiers
    ordinaires situés dans le dossier sont lus (ni lien, ni chemin qui en
    sortirait)."""

    # Place laissée libre sur le disque du dossier temporaire (en général
    # celui de Windows) : une archive piégée de quelques Ko peut annoncer des
    # centaines de Go, et un disque plein ferait échouer Windows et les
    # autres programmes.
    FREE_RESERVE = 256 * 1024 * 1024
    # Intervalle de contrôle de la taille extraite pendant le travail d'UnRAR
    POLL_SECONDS = 0.1

    def __init__(self, archive_path, infos):
        from modules.qt.archive_names import ArchiveRefusedError, ArchiveToolError
        counts = {}
        for info in infos:
            if not info.is_dir():
                counts[info.filename] = counts.get(info.filename, 0) + 1
        homonyms = [info for info in infos if not info.is_dir() and counts[info.filename] > 1]
        for info in homonyms:
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ":" in name or ".." in name.split("/"):
                raise ArchiveRefusedError(f"{info.filename}: unsafe name for extraction")
            if any(c in name for c in "*?\r\n"):
                raise ArchiveRefusedError(f"{info.filename}: name unusable in an UnRAR list")
        from modules.qt.temp_files import get_mosaicview_temp_dir
        temp_dir = get_mosaicview_temp_dir()
        expected = self._expected_size(homonyms, temp_dir)
        self.folder = tempfile.mkdtemp(prefix="rar_homonyms_", dir=temp_dir)
        self._crcs = {}
        list_path = None
        try:
            handle, list_path = tempfile.mkstemp(prefix="rar_homonyms_", suffix=".lst",
                                                 dir=temp_dir)
            paths = dict.fromkeys(info.filename for info in homonyms)
            with os.fdopen(handle, "wb") as f:
                f.write("﻿".encode("utf-16-le")
                        + "".join(p + "\r\n" for p in paths).encode("utf-16-le"))
            returncode = self._run_extraction(
                [rarfile.UNRAR_TOOL, "x", "-or", "-p-", "-inul", "-scul", "-n@" + list_path,
                 "--", archive_path, self.folder + os.sep], expected)
            if returncode != 0:
                raise ArchiveToolError(f"UnRAR {returncode}")
        except BaseException:
            self.close()
            raise
        finally:
            if list_path is not None:
                try:
                    os.remove(list_path)
                except OSError:
                    pass  # sans conséquence : temporaire supprimé au nettoyage de MosaicViewTemp

    @classmethod
    def _expected_size(cls, homonyms, temp_dir):
        """Taille totale que l'extraction de `homonyms` doit écrire. Chaque
        fichier est limité à MAX_MEMBER_SIZE (_check_member_size) ; un
        fichier RAR 5 marqué « taille inconnue » (archive écrite dans un
        flux) compte pour cette limite : sa taille annoncée n'engage à rien,
        rarfile la lit sans tenir compte de ce drapeau. Refusée
        (ArchiveRefusedError) si le disque de `temp_dir` n'a pas cette place
        en plus de FREE_RESERVE."""
        from modules.qt.archive_names import ArchiveRefusedError
        expected = 0
        for info in homonyms:
            if (getattr(info, "file_flags", None) or 0) & rarfile.RAR5_FILE_FLAG_UNKNOWN_SIZE:
                expected += MAX_MEMBER_SIZE
            else:
                _check_member_size(info.file_size)
                expected += info.file_size
        try:
            free = shutil.disk_usage(temp_dir).free
        except OSError:
            return expected      # place inconnue : _run_extraction limite encore la taille écrite
        if expected + cls.FREE_RESERVE > free:
            raise ArchiveRefusedError(
                f"not enough free space to extract: {expected} bytes needed, {free} free")
        return expected

    def _run_extraction(self, cmd, expected):
        """Lance UnRAR (`cmd`) et rend son code de sortie. Toutes les
        POLL_SECONDS, puis à sa fin, la taille des fichiers déjà écrits dans
        le dossier est comparée à `expected` : une taille annoncée fausse
        (RAR 5 « taille inconnue », en-tête piégé) est arrêtée là
        (ArchiveRefusedError, UnRAR tué), comme une place libre devenue
        inférieure à FREE_RESERVE (autre programme qui remplit le disque
        pendant ce temps)."""
        from modules.qt.archive_names import ArchiveRefusedError
        proc = start_helper(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            while True:
                try:
                    proc.wait(timeout=self.POLL_SECONDS)
                    finished = True
                except subprocess.TimeoutExpired:
                    finished = False
                written = self._written_size()
                if written > expected:
                    raise ArchiveRefusedError(
                        f"extraction larger than announced: more than {expected} bytes")
                if not finished:
                    try:
                        free = shutil.disk_usage(self.folder).free
                    except OSError:
                        free = None      # place inconnue : la taille écrite reste limitée
                    if free is not None and free < self.FREE_RESERVE:
                        raise ArchiveRefusedError(
                            f"extraction stopped: only {free} bytes left on the disk")
                if finished:
                    return proc.returncode
        except BaseException:
            proc.kill()
            proc.wait()
            raise

    def _written_size(self):
        """Taille des fichiers écrits dans le dossier d'extraction, fichier en
        cours d'écriture compris. Chaque taille se lit par os.lstat, qui ouvre
        le fichier : la taille rendue par os.scandir vient de l'entrée du
        dossier, que Windows ne met pas à jour tant qu'un fichier reste ouvert
        en écriture (0 jusqu'à la fin d'UnRAR : l'extraction ne serait
        arrêtée qu'une fois finie). Les liens et jonctions ne sont ni comptés ni
        suivis."""
        import stat
        total = 0
        folders = [self.folder]
        while folders:
            try:
                with os.scandir(folders.pop()) as entries:
                    for entry in entries:
                        try:
                            info = os.lstat(entry.path)
                        except OSError:
                            continue     # fichier retiré entre la liste et sa lecture
                        if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            folders.append(entry.path)
                        else:
                            total += info.st_size
            except OSError:
                continue                 # dossier pas encore créé ou retiré
        return total

    def read(self, info):
        """Octets du fichier `info` (RarInfo), retrouvé par sa taille et sa
        somme de contrôle parmi les fichiers extraits dans son dossier."""
        from modules.qt.archive_names import ArchiveRefusedError
        expected_crc = getattr(info, "CRC", None)
        if expected_crc is None:
            # RAR 5 protégé par BLAKE2 au lieu de CRC32 : aucun moyen de
            # reconnaître le fichier
            raise ArchiveRefusedError(f"{info.filename}: no CRC32 to identify the file")
        root = os.path.realpath(self.folder)
        folder = posixpath.dirname(info.filename.replace("\\", "/"))
        directory = os.path.join(self.folder, *folder.split("/")) if folder else self.folder
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            names = []
        for name in names:
            path = os.path.join(directory, name)
            if (os.path.islink(path) or not os.path.isfile(path)
                    or not os.path.realpath(path).startswith(root + os.sep)
                    or os.path.getsize(path) != info.file_size):
                continue
            if self._crcs.get(path, expected_crc & 0xFFFFFFFF) != expected_crc & 0xFFFFFFFF:
                continue                       # déjà reconnu comme un autre fichier
            _check_member_size(os.path.getsize(path))
            with open(path, "rb") as f:
                data = f.read()
            self._crcs[path] = zlib.crc32(data) & 0xFFFFFFFF
            if self._crcs[path] == expected_crc & 0xFFFFFFFF:
                return data
        raise ArchiveRefusedError(f"{info.filename}: not found after extraction")

    def close(self):
        shutil.rmtree(self.folder, ignore_errors=True)


def open_archive(path, kind):
    """ArchiveReader de l'archive `path`, au format `kind` (« CBZ », « EPUB »,
    « CBR », « CB7 », « CBT ») : format réel (detect_archive_type) ou annoncé
    par l'extension (kind_from_extension), selon l'appelant. À utiliser dans
    un bloc with (archive refermée à la sortie)."""
    return ArchiveReader(path, kind)
