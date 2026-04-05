"""
modules/qt/archive_type_detector.py
Détection du format réel d'une archive par magic bytes.

Formats supportés : CBZ (ZIP), EPUB (ZIP+mimetype), CBR (RAR), CB7 (7z)
Retourne : "CBZ", "EPUB", "CBR", "CB7", ou None si format inconnu.
"""

import tarfile
import zipfile


# Magic bytes des formats supportés
_MAGIC_ZIP   = b'PK\x03\x04'
_MAGIC_RAR4  = b'Rar!\x1a\x07\x00'
_MAGIC_RAR5  = b'Rar!\x1a\x07\x01\x00'
_MAGIC_7Z    = b'7z\xbc\xaf\x27\x1c'


def detect_archive_type(filepath: str) -> str | None:
    """
    Détecte le format réel d'une archive par magic bytes.
    Retourne "CBZ", "EPUB", "CBR", "CB7", "CBT", ou None.

    - CBZ  : ZIP sans fichier mimetype EPUB
    - EPUB : ZIP contenant mimetype = "application/epub+zip"
    - CBR  : RAR (v4 ou v5)
    - CB7  : 7z
    - CBT  : TAR (nu, gzip, bzip2) — détecté en dernier recours car pas de magic bytes fiables
    """
    try:
        with open(filepath, 'rb') as f:
            magic = f.read(8)
    except OSError:
        return None

    if magic[:4] == _MAGIC_ZIP:
        # ZIP — distinguer CBZ et EPUB
        try:
            with zipfile.ZipFile(filepath, 'r') as zf:
                if 'mimetype' in zf.namelist():
                    mime = zf.read('mimetype').strip()
                    if mime == b'application/epub+zip':
                        return "EPUB"
        except Exception:
            pass
        return "CBZ"

    if magic[:7] == _MAGIC_RAR4 or magic[:8] == _MAGIC_RAR5:
        return "CBR"

    if magic[:6] == _MAGIC_7Z:
        return "CB7"

    # TAR — pas de magic bytes fixes en tête, on utilise tarfile.is_tarfile()
    try:
        if tarfile.is_tarfile(filepath):
            return "CBT"
    except Exception:
        pass

    return None
