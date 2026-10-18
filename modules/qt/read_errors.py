"""
modules/qt/read_errors.py — Raison traduite d'une erreur de lecture.

Une page ou un fichier illisible garde le message technique brut de son
erreur (exception de zipfile, rarfile, tarfile, 7z, Pillow, ou de
entries.create_entry), le plus souvent en anglais : `corruption_reason` d'une
page, lignes « nom: message » des erreurs du chargement. Il n'est traduit
qu'à l'affichage (info-bulle, fenêtre « Fichiers ignorés »), pour suivre un
changement de langue.
"""
import os
import re

from modules.qt.localization import _
from modules.qt.archive_names import tar_entry_name, without_epub_rank

# Causes reconnues dans le message brut, dans l'ordre d'examen. Le premier
# motif qui correspond donne la clé de la raison (messages.read_errors.<clé>).
_TOO_LARGE_RE = re.compile(r"^\s*(?P<size>\d[\d.,]*\s*\S+)\s*>\s*(?P<max>\d[\d.,]*\s*\S+)\s*$")
_CAUSES = (
    ("crc", re.compile(r"crc", re.I)),
    ("encrypted", re.compile(r"encrypt|password", re.I)),
    ("unsupported_compression",
     re.compile(r"compression (method|type)|deflate64|unsupported method|not supported", re.I)),
    ("truncated", re.compile(r"truncat|unexpected end|end of (stream|data|file)", re.I)),
    ("unknown_format", re.compile(r"cannot identify image", re.I)),
    ("empty", re.compile(r"^Fichier image vide$", re.I)),
    ("invalid_dimensions", re.compile(r"^Dimensions invalides", re.I)),
)

# Longueur maximale du message technique cité par la raison générique
_MAX_DETAIL = 80


def read_error_text(raw):
    """Raison traduite d'une erreur de lecture, d'après son message brut :
    cause reconnue (données abîmées, mot de passe, compression non prise en
    charge, taille au-delà de la limite, fichier incomplet, format non
    reconnu…), sinon « illisible (message technique) »."""
    raw = (raw or "").strip()
    m = _TOO_LARGE_RE.match(raw)
    if m:
        return _("messages.read_errors.too_large", size=m.group("size"), max=m.group("max"))
    for key, pattern in _CAUSES:
        if pattern.search(raw):
            return _(f"messages.read_errors.{key}")
    detail = raw if len(raw) <= _MAX_DETAIL else raw[:_MAX_DETAIL - 1] + "…"
    return _("messages.read_errors.other", detail=detail)


def member_name_forms(member_name):
    """Noms sous lesquels la page d'un membre d'archive peut figurer dans le
    document : chemin tel quel, préfixe NEW- (archives 2..N d'une ouverture
    groupée), nom de base (page d'EPUB, sans son rang), nom TAR normalisé."""
    return {member_name, "NEW-" + member_name, os.path.basename(member_name),
            tar_entry_name(member_name)}


def unreadable_member_names(images_data):
    """Noms des entrées sans données (membres illisibles), sous toutes les
    formes comparables à un nom de membre (member_name_forms)."""
    names = set()
    for entry in images_data:
        if entry.get("bytes") is None and not entry.get("is_dir"):
            name = entry.get("orig_name", "")
            names.update((name, without_epub_rank(name)))
    return names


def files_ignored_line(error, member_names=None):
    """Ligne de la fenêtre « Fichiers ignorés » pour une erreur « nom: message ».

    member_names : noms des membres illisibles (unreadable_member_names) ;
    seules ces erreurs-là sont reformulées (nom + raison traduite). Les autres
    lignes (messages déjà traduits, erreurs d'archive entière) sont rendues
    telles quelles. None : toutes les erreurs sont des erreurs de membre."""
    name, sep, message = error.partition(": ")
    if not sep:
        return error
    if member_names is not None and not (member_name_forms(name) & member_names):
        return error
    return _("messages.read_errors.line", name=name, reason=read_error_text(message))
