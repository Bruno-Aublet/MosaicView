"""
modules/qt/misnamed_archives.py — Archives mal nommées : extension qui ne
correspond pas au format réel du fichier (ZIP nommé .cbr, TAR compressé
nommé .cb7…).

Le format réel se lit dans le contenu (archive_type_detector.detect_archive_type,
quelques octets) ; celui de l'extension par archive_members.kind_from_extension.
rename_to_real_format est la règle commune de renommage : jamais d'écrasement,
jamais un fichier ouvert ou utilisé par l'application ou par un autre lot.
Sans Qt : appelée depuis la fenêtre de la question et testable seule.
"""

import os
from typing import NamedTuple

# Extension donnée à une archive renommée selon son format réel
EXTENSION_OF_KIND = {"CBZ": ".cbz", "EPUB": ".epub", "CBR": ".cbr", "CB7": ".cb7", "CBT": ".cbt"}

# Essais de noms libres au plus (« X.cbz », « X (1).cbz »…) : borne une
# boucle que des noms tous refusés ne termineraient jamais
_MAX_NAME_TRIES = 10_000

# Lot « Vérifier les extensions » : extensions vérifiées, avec le format
# qu'elles annoncent, en deux familles. Un fichier mal nommé garde une
# extension de sa famille : un comic reste un comic (.cbr → .cbz), une
# archive reste une archive (.rar → .zip). Toutes les variantes de TAR
# (compressé ou non) annoncent le même format : detect_archive_type ne les
# distingue pas.
COMIC_EXTENSIONS = {".cbz": "CBZ", ".cbr": "CBR", ".cb7": "CB7", ".cbt": "CBT",
                    ".epub": "EPUB", ".pdf": "PDF"}
ARCHIVE_EXTENSIONS = {".zip": "CBZ", ".rar": "CBR", ".7z": "CB7", ".tar": "CBT",
                      ".tgz": "CBT", ".tar.gz": "CBT", ".tbz": "CBT", ".tbz2": "CBT",
                      ".tar.bz2": "CBT", ".txz": "CBT", ".tar.xz": "CBT"}
# Extension donnée selon le format réel, pour chaque famille. EPUB et PDF
# n'ont qu'une extension.
_COMIC_EXTENSION_OF_KIND = {"CBZ": ".cbz", "CBR": ".cbr", "CB7": ".cb7", "CBT": ".cbt",
                            "EPUB": ".epub", "PDF": ".pdf"}
_ARCHIVE_EXTENSION_OF_KIND = {"CBZ": ".zip", "CBR": ".rar", "CB7": ".7z", "CBT": ".tar",
                              "EPUB": ".epub", "PDF": ".pdf"}
# Extensions vérifiées, les plus longues d'abord : « x.tar.gz » est un
# « .tar.gz », pas un « .gz »
_CHECKED_EXTENSIONS = sorted({**COMIC_EXTENSIONS, **ARCHIVE_EXTENSIONS}, key=len, reverse=True)

# Un PDF peut commencer par quelques octets avant son en-tête « %PDF- » :
# les lecteurs le cherchent dans le premier Ko
_PDF_HEADER = b"%PDF-"
_PDF_HEADER_SEARCH = 1024


class MisnamedArchiveError(Exception):
    """Archive dont l'extension ne correspond pas au format réel (real_kind :
    « CBZ », « CBR »…). Cas normal, pas un défaut du programme : jamais écrite
    dans le journal des erreurs."""

    expected_error = True

    def __init__(self, path, real_kind):
        super().__init__(f"{os.path.basename(path)}: real format {real_kind}")
        self.path = path
        self.real_kind = real_kind


class RenameRefusedError(Exception):
    """Renommage refusé : fichier ouvert ou utilisé par l'application, ou
    traité par un autre lot. key : clé de traduction de la raison, avec le
    paramètre {name} ; le texte se résout à l'affichage (_ ou _wt)."""

    expected_error = True

    def __init__(self, key, name):
        super().__init__(f"{key}: {name}")
        self.key = key
        self.name = name


def misnamed_real_kind(path):
    """Format réel de `path` (« CBZ », « CBR »…) si son extension annonce
    une archive d'un autre format, None sinon : extension qui n'est pas celle
    d'une archive, archive bien nommée, format réel inconnu ou fichier
    illisible (la lecture le signalera). « .zip » est un CBZ bien nommé."""
    from modules.qt.archive_members import kind_from_extension
    declared = kind_from_extension(path)
    if declared is None:
        return None
    from modules.qt.archive_type_detector import detect_archive_type
    real = detect_archive_type(path)
    if real is None or real == declared:
        return None
    return real


def checked_extension(path):
    """Extension de `path` vérifiée par le lot « Vérifier les extensions »
    (en minuscules, « .tar.gz » compris), None pour un autre fichier. Un nom
    réduit à l'extension (« .zip ») n'en a pas, comme pour os.path.splitext."""
    name = os.path.basename(path).lower()
    for ext in _CHECKED_EXTENSIONS:
        if name.endswith(ext) and len(name) > len(ext):
            return ext
    return None


def real_file_kind(path):
    """Format réel de `path`, lu dans son contenu : celui d'une archive
    (archive_type_detector.detect_archive_type : « CBZ », « EPUB », « CBR »,
    « CB7 », « CBT »), sinon « PDF » si son en-tête est dans le premier Ko,
    sinon None (format non reconnu, fichier illisible ou absent). Les
    archives sont reconnues d'abord : un TAR ou un ZIP qui contient un PDF
    non compressé porte « %PDF- » dans son premier Ko.

    Réservée au lot « Vérifier les extensions » : detect_archive_type ne
    reconnaît pas les PDF, et les autres lots ne savent renommer que vers une
    extension d'archive (EXTENSION_OF_KIND)."""
    from modules.qt.archive_type_detector import detect_archive_type
    kind = detect_archive_type(path)
    if kind is not None:
        return kind
    try:
        with open(path, "rb") as f:
            head = f.read(_PDF_HEADER_SEARCH)
    except OSError:
        return None
    return "PDF" if _PDF_HEADER in head else None


class ExtensionCheck(NamedTuple):
    """Résultat de check_extension pour un fichier.

    extension     : extension vérifiée, en minuscules (« .cbr », « .tar.gz »).
    real_kind     : format réel (real_file_kind), None s'il n'est pas reconnu.
    new_extension : extension à lui donner, None s'il est bien nommé ou si
                    son format n'est pas reconnu."""
    path: str
    extension: str
    real_kind: str | None
    new_extension: str | None


def check_extension(path):
    """Vérifie l'extension de `path` d'après son contenu (ExtensionCheck),
    None si cette extension n'est pas vérifiée (checked_extension). La
    nouvelle extension reste dans la famille de l'ancienne : comic
    (COMIC_EXTENSIONS) ou archive (ARCHIVE_EXTENSIONS)."""
    ext = checked_extension(path)
    if ext is None:
        return None
    real = real_file_kind(path)
    if ext in COMIC_EXTENSIONS:
        declared, targets = COMIC_EXTENSIONS[ext], _COMIC_EXTENSION_OF_KIND
    else:
        declared, targets = ARCHIVE_EXTENSIONS[ext], _ARCHIVE_EXTENSION_OF_KIND
    if real is None or real == declared:
        return ExtensionCheck(path, ext, real, None)
    return ExtensionCheck(path, ext, real, targets[real])


def rename_label(old_extension, new_extension):
    """Libellé d'un renommage dans un journal ou un résumé : ancienne
    extension → nouvelle, en majuscules (« CBR→CBZ », « TAR.GZ→ZIP »)."""
    return f"{old_extension.lstrip('.').upper()}→{new_extension.lstrip('.').upper()}"


def rename_to_real_format(path, real_kind, claims=None, open_files=(), files_in_use=()):
    """Renomme `path` avec l'extension de son format réel et rend le nouveau
    chemin : « X.cbz », ou le premier nom libre « X (1).cbz »… — jamais un
    fichier existant écrasé.

    claims : BatchClaims du lot qui renomme, None hors lot. Avec lui, le
    fichier doit être à ce lot (claim) et le nouveau nom est réservé contre
    les autres lots (reserve) ; sans lui, le fichier ne doit appartenir à
    aucun lot en cours.
    open_files : fichiers ouverts dans l'application (comics des panneaux,
    base de la Bibliothèque) ; files_in_use : fichiers qu'elle lit ou va
    lire. Les renommer laisserait l'application sur un fichier qui n'existe
    plus sous ce nom.

    Lève RenameRefusedError (raison traduisible) dans ces cas, OSError si
    Windows refuse le renommage."""
    return rename_with_extension(path, os.path.splitext(path)[1], EXTENSION_OF_KIND[real_kind],
                                 claims=claims, open_files=open_files,
                                 files_in_use=files_in_use)


def rename_with_extension(path, old_extension, new_extension, claims=None, open_files=(),
                          files_in_use=()):
    """Remplace l'extension `old_extension` de `path` (fin de son nom, en
    toute casse : « .tar.gz » compris) par `new_extension` et rend le nouveau
    chemin. Mêmes règles et mêmes refus que rename_to_real_format : premier
    nom libre « X.ext », « X (1).ext »…, jamais un fichier écrasé, jamais un
    fichier ouvert, utilisé ou pris par un autre lot."""
    from modules.qt.batch_claims import batch_file_under, free_name_candidates
    from modules.qt.file_picker import open_file_under
    name = os.path.basename(path)
    if open_file_under(path, open_files):
        raise RenameRefusedError("dialogs.file_picker.file_open", name)
    if open_file_under(path, files_in_use):
        raise RenameRefusedError("dialogs.file_picker.file_in_use", name)
    if claims is not None:
        if not claims.claim(path):
            raise RenameRefusedError("messages.errors.file_in_batch", name)
    elif batch_file_under(path):
        raise RenameRefusedError("messages.errors.file_in_batch", name)

    base = path[:len(path) - len(old_extension)] if old_extension else path
    candidates = free_name_candidates(base, new_extension)
    if claims is not None:
        # Nom libre sur le disque et pour les autres lots, réservé sous le
        # même verrou que le test
        new_path = claims.reserve(candidates)
        os.rename(path, new_path)
        return new_path
    for _try, candidate in zip(range(_MAX_NAME_TRIES), candidates):
        if os.path.exists(candidate):
            continue
        try:
            # Windows refuse de renommer vers un nom pris : un fichier créé
            # entre le test et le renommage n'est jamais écrasé
            os.rename(path, candidate)
            return candidate
        except FileExistsError:
            continue
    raise FileExistsError(path)
