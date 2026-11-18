"""
modules/qt/read_errors.py — Raison traduite d'une erreur de lecture.

Une page ou un fichier illisible garde le message technique brut de son
erreur (exception de zipfile, rarfile, tarfile, 7z, Pillow, ou de
entries.create_entry), le plus souvent en anglais : `corruption_reason` d'une
page, lignes « nom: message » des erreurs du chargement. Il n'est traduit
qu'à l'affichage (info-bulle, fenêtre « Fichiers ignorés »), pour suivre un
changement de langue. Les autres lignes de ces listes (archive refusée,
renommage impossible) sont des TranslatedLine, traduites elles aussi à
l'affichage.
"""
import os
import re

from modules.qt.localization import _
from modules.qt.archive_names import tar_entry_name, without_epub_rank
from modules.qt.utils import format_file_size

# Causes reconnues dans le message brut, dans l'ordre d'examen. Le premier
# motif qui correspond donne la clé de la raison (messages.read_errors.<clé>).
# Taille refusée : en octets (archive_names.MemberTooLargeError.raw_reason,
# mise en forme ici dans la langue du moment), ou déjà écrite avec son unité
_TOO_LARGE_BYTES_RE = re.compile(r"^\s*(?P<size>\d+)\s*>\s*(?P<max>\d+)\s*$")
_TOO_LARGE_RE = re.compile(r"^\s*(?P<size>\d[\d.,]*\s*\S+)\s*>\s*(?P<max>\d[\d.,]*\s*\S+)\s*$")
_CAUSES = (
    # Extraction des fichiers de même nom d'un CBR (archive_members._RarExtraction) :
    # refusée faute de place sur le disque du dossier temporaire, ou arrêtée
    # parce qu'elle écrit plus que la taille annoncée par l'archive
    ("disk_space", re.compile(r"not enough free space|bytes left on the disk", re.I)),
    ("larger_than_announced", re.compile(r"larger than announced", re.I)),
    # Archive refusée par le lecteur commun (archive_members) : plus de
    # MAX_ARCHIVE_MEMBERS membres, ou liste d'un 7z démesurée
    ("too_many_files", re.compile(r"too many files in the archive", re.I)),
    ("name_too_long", re.compile(r"name too long", re.I)),
    # Données abîmées : somme de contrôle fausse, ou données compressées
    # que zlib (« Error -3 while decompressing data »), bz2 (« Invalid data
    # stream ») ou lzma (« Corrupt input data ») ne savent pas décompresser
    ("crc", re.compile(r"crc|while decompressing|invalid data stream|corrupt input data", re.I)),
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
    m = _TOO_LARGE_BYTES_RE.match(raw)
    if m:
        return _("messages.read_errors.too_large",
                 size=format_file_size(int(m.group("size"))), max=format_file_size(int(m.group("max"))))
    m = _TOO_LARGE_RE.match(raw)
    if m:
        return _("messages.read_errors.too_large", size=m.group("size"), max=m.group("max"))
    for key, pattern in _CAUSES:
        if pattern.search(raw):
            return _(f"messages.read_errors.{key}")
    detail = raw if len(raw) <= _MAX_DETAIL else raw[:_MAX_DETAIL - 1] + "…"
    return _("messages.read_errors.other", detail=detail)


class ReadErrorReason:
    """Raison brute d'une erreur de lecture, traduite (read_error_text) chaque
    fois qu'elle est mise en forme. Passée comme paramètre d'un message que sa
    fenêtre recalcule à chaque changement de langue (localization : str.format
    appelle str() sur chaque paramètre), elle suit la langue du moment, là où
    un texte traduit d'avance resterait dans l'ancienne."""

    def __init__(self, raw):
        self.raw = raw

    def __str__(self):
        return read_error_text(self.raw)


class TranslatedLine:
    """Ligne d'une liste d'erreurs du chargement dont le texte n'est traduit
    qu'à l'affichage : une clé et ses paramètres, plus, éventuellement, la
    raison brute d'une erreur de lecture (`raw_reason`, traduite par
    read_error_text et passée comme paramètre `reason`).

    Les fenêtres rappellent leur texte à chaque changement de langue : une
    phrase traduite au moment de l'erreur resterait dans l'ancienne langue.
    Rangée dans la même liste que les lignes brutes « nom: message »
    (files_ignored_line sait afficher les deux)."""

    def __init__(self, key, raw_reason=None, **params):
        self.key = key
        self.raw_reason = raw_reason
        self.params = params

    def __call__(self):
        params = dict(self.params)
        if self.raw_reason is not None:
            params["reason"] = read_error_text(self.raw_reason)
        return _(self.key, **params)

    __str__ = __call__


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
    lignes sont rendues telles quelles. None : toutes les erreurs sont des
    erreurs de membre. Une TranslatedLine (archive refusée, renommage
    impossible, archive illisible) est traduite dans la langue courante."""
    if isinstance(error, TranslatedLine):
        return error()
    name, sep, message = error.partition(": ")
    if not sep:
        return error
    if member_names is not None and not (member_name_forms(name) & member_names):
        return error
    return _("messages.read_errors.line", name=name, reason=read_error_text(message))
