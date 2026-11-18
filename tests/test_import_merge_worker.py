"""Ajout d'une archive à un comic ouvert : import_merge_qt.ImportMergeWorker.run
(lecture des pages, avant leur insertion dans le document).

Vérifié, sur de vraies archives ZIP et TAR :
- pages lues dans l'ordre naturel, préfixées (NEW01-…, dossier compris) pour
  ne jamais prendre le nom d'une page existante, archive d'origine notée ;
  ComicInfo.xml gardé sous son nom (il devient les métadonnées du comic s'il
  n'en a pas, sinon une annexe, au moment de l'insertion) ;
- un membre illisible n'arrête jamais l'ajout : page illisible (cadre rouge)
  avec sa raison, autre fichier gardé sans données avec sa raison ;
  page d'un ZIP restée sans données : emplacement de son membre gardé (pour
  la copier telle quelle) ;
- TIFF de plusieurs pages : une page du comic par page ;
- format réel lu dans le contenu, pour toutes les extensions : archive mal
  nommée signalée avec son format réel, rien d'ajouté, puis lue dans ce
  format si l'utilisateur la garde ; format inconnu signalé ; archive abîmée
  de son vrai format : erreur, jamais « mal nommée » ;
- annulation : aucune page rendue.

import_merge_qt.py et archive_loader.py importent PySide6 : la méthode et
les fonctions du chargeur sont extraites par AST (tests/ast_loader.py),
signaux remplacés ; le lecteur d'archive commun (archive_members, sans Qt)
est le vrai.
"""
import io
import os
import tarfile
import threading
import zipfile

import pytest
from PIL import Image

from modules.qt import archive_members, archive_names
from modules.qt.archive_members import AlteredMemberError, open_archive
from modules.qt.archive_type_detector import detect_archive_type
from modules.qt.archive_names import IMAGE_EXTS, epub_page_name
from modules.qt.comic_info import is_comic_info_name
from modules.qt.entries import create_entry, is_unreadable_file

import ast_loader
from fakes import Signal as _Signal

READERS = ast_loader.load(
    "archive_loader.py",
    {"_remember_unread_zip_member", "create_page_entry", "folder_entry",
     "_expand_multipage_tiffs_with_thumbnails"},
    os=os, create_entry=create_entry, IMAGE_EXTS=IMAGE_EXTS, AlteredMemberError=AlteredMemberError,
    build_qimage_for_entry=lambda entry: None)


# Formats d'après l'extension (LoadWorker._EXT_TO_TYPE, classe de archive_loader,
# qui importe Qt)
_LOAD_WORKER = ast_loader.load_methods("archive_loader.py", "LoadWorker", set(),
                                       class_attrs=("_EXT_TO_TYPE",))


def make_worker():
    names = ("progress", "finished", "error", "cancelled", "wrong_extension", "not_an_archive")
    Base = ast_loader.load_methods(
        "import_merge_qt.py", "ImportMergeWorker", {"run"},
        os=os, is_unreadable_file=is_unreadable_file,
        open_archive=open_archive, epub_page_name=epub_page_name,
        is_comic_info_name=is_comic_info_name, detect_archive_type=detect_archive_type,
        LoadWorker=_LOAD_WORKER,
        **{k: READERS[k] for k in ("folder_entry", "_remember_unread_zip_member",
                                   "create_page_entry", "_expand_multipage_tiffs_with_thumbnails")})

    class Worker(Base):
        def __init__(self, filepath, prefix="NEW01-", kind=None):
            self._filepath, self._merge_prefix, self._kind = filepath, prefix, kind
            self._cancelled = threading.Event()
            self.pages_read = []
            for name in names:
                setattr(self, name, _Signal())
    return Worker


Worker = make_worker()


def png(color):
    buf = io.BytesIO()
    Image.new("RGB", (6, 4), color).save(buf, "PNG")
    return buf.getvalue()


def make_zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
    return str(path)


def make_tar(path, members):
    with tarfile.open(path, "w") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return str(path)


def run(path, **kwargs):
    worker = Worker(path, **kwargs)
    worker.run()
    return worker


def finished(worker):
    [(entries, errors)] = worker.finished.emitted
    return entries, errors


def test_pages_are_read_in_order_and_prefixed(tmp_path):
    src = make_zip(tmp_path / "Bbbb.cbz", [("p10.png", png("blue")), ("p2.png", png("red")),
                                           ("ComicInfo.xml", b"<ComicInfo/>")])
    entries, errors = finished(run(src))
    assert [e["orig_name"] for e in entries] == ["ComicInfo.xml", "NEW01-p2.png", "NEW01-p10.png"]
    assert errors == []
    assert all(e["source_archive"] == "Bbbb.cbz" for e in entries)
    assert entries[1]["bytes"] == png("red")


def test_a_superscript_digit_in_a_name_does_not_refuse_the_archive(tmp_path):
    """« ² » passe pour un chiffre pour str.isdigit() mais int() le refuse :
    la clé de tri faisait échouer l'ajout de toute l'archive."""
    src = make_zip(tmp_path / "Bbbb.cbz", [("p1².png", png("blue")), ("p1.png", png("red"))])
    entries, errors = finished(run(src))
    assert [e["orig_name"] for e in entries] == ["NEW01-p1.png", "NEW01-p1².png"]
    assert errors == []


def test_empty_folders_of_a_zip_and_a_tar_are_added(tmp_path):
    """Un dossier présent dans l'archive, vide ou non, est ajouté avec ses
    pages (entrée « dossier », jamais lue), préfixé comme elles ; un nom de
    dossier qui sort de l'archive (« .. ») n'est pas un dossier."""
    cbz = make_zip(tmp_path / "Bbbb.cbz", [("Ch1/001.png", png("red")), ("Vide/", b""),
                                           ("../", b"")])
    entries, _errors = finished(run(cbz))
    assert [(e["orig_name"], e["is_dir"]) for e in entries] == [
        ("NEW01-Ch1/NEW01-001.png", False), ("NEW01-Vide/", True)]
    assert entries[1]["bytes"] is None

    cbt = tmp_path / "Bbbb.cbt"
    with tarfile.open(cbt, "w") as tf:
        for name in (".", "./Vide", "./Ch1"):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            tf.addfile(info)
        info = tarfile.TarInfo("./Ch1/001.png")
        data = png("red")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    entries, _errors = finished(run(str(cbt)))
    assert [(e["orig_name"], e["is_dir"]) for e in entries] == [
        ("NEW01-Ch1/", True), ("NEW01-Ch1/NEW01-001.png", False), ("NEW01-Vide/", True)]


def test_folder_and_name_are_both_prefixed(tmp_path):
    src = make_tar(tmp_path / "Bbbb.cbt", [("Ch1/001.png", png("red"))])
    entries, _errors = finished(run(src))
    assert [e["orig_name"] for e in entries] == ["NEW01-Ch1/NEW01-001.png"]


def test_unreadable_members_never_stop_the_addition(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_members, "MAX_MEMBER_SIZE", 500)
    src = make_zip(tmp_path / "Bbbb.cbz", [("p01.png", png("red")), ("p02.png", b"x" * 5000),
                                           ("info.nfo", b"y" * 5000)])
    entries, errors = finished(run(src))
    by_name = {e["orig_name"]: e for e in entries}
    unread = by_name["NEW01-p02.png"]
    assert unread["is_corrupted"] and unread["bytes"] is None
    assert unread["corruption_reason"]                  # raison sur la page (cadre rouge)
    assert unread["unread_member"]["path"] == os.path.abspath(src)
    assert by_name["NEW01-p01.png"]["is_corrupted"] is False
    # Autre fichier : présent dans le document, sans données, avec sa raison
    # (cadre rouge), et non dans « Fichiers ignorés », qui le dirait absent
    nfo = by_name["NEW01-info.nfo"]
    assert nfo["bytes"] is None and nfo["corruption_reason"] and not nfo["is_corrupted"]
    assert errors == []


def test_multipage_tiff_becomes_one_page_per_page(tmp_path):
    frames = [Image.new("RGB", (6, 4), c) for c in ("red", "blue")]
    buf = io.BytesIO()
    frames[0].save(buf, "TIFF", save_all=True, append_images=frames[1:])
    src = make_zip(tmp_path / "Bbbb.cbz", [("p01.tif", buf.getvalue())])
    entries, _errors = finished(run(src))
    assert len(entries) == 2
    assert all(e["orig_name"].endswith(".tif") for e in entries)


def test_cbz_that_is_not_a_zip_is_reported_as_misnamed(tmp_path):
    src = tmp_path / "Bbbb.cbz"
    src.write_bytes(b"Rar!\x1a\x07\x00 pas un zip")
    worker = run(str(src))
    assert worker.wrong_extension.emitted == [("CBR",)]
    assert worker.finished.emitted == []


def compressed_tar_named_cb7(tmp_path):
    path = tmp_path / "Bbbb.cb7"
    with tarfile.open(path, "w:gz") as tf:
        for name, data in (("p01.png", png("red")), ("p02.png", png("blue"))):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return str(path)


def test_every_misnamed_format_is_detected_from_its_content(tmp_path):
    """Pas seulement CBZ et CBR : un TAR compressé nommé .cb7 (que 7-Zip
    montrerait comme un seul fichier, le TAR) est signalé mal nommé, avec son
    format réel, et rien n'est lu."""
    worker = run(compressed_tar_named_cb7(tmp_path))
    assert worker.wrong_extension.emitted == [("CBT",)]
    assert worker.finished.emitted == []


def test_misnamed_archive_kept_is_read_in_its_real_format(tmp_path):
    """« Ajouter sans renommer » : l'ajout reprend dans le format réel."""
    entries, errors = finished(run(compressed_tar_named_cb7(tmp_path), kind="CBT"))
    assert [e["orig_name"] for e in entries] == ["NEW01-p01.png", "NEW01-p02.png"]
    assert errors == []


def test_damaged_archive_of_the_right_format_is_an_error_not_a_misnamed_file(tmp_path):
    """Un vrai ZIP abîmé n'est pas pris pour un RAR mal nommé : la question
    de renommage proposerait de lui donner une extension fausse."""
    src = tmp_path / "Bbbb.cbz"
    src.write_bytes(b"PK\x03\x04" + b"\x00" * 60)
    worker = run(str(src))
    assert worker.wrong_extension.emitted == []
    [(message,)] = worker.error.emitted
    assert message


def test_cancelled_addition_returns_no_page(tmp_path):
    src = make_zip(tmp_path / "Bbbb.cbz", [("p01.png", png("red"))])
    worker = Worker(src)
    worker._cancelled.set()
    worker.run()
    assert worker.cancelled.emitted == [()]
    assert worker.finished.emitted == []


def test_file_of_unknown_format_is_reported(tmp_path):
    src = tmp_path / "Bbbb.cbt"
    src.write_bytes(b"ni tar ni rien")
    worker = run(str(src))
    assert worker.finished.emitted == []
    assert worker.not_an_archive.emitted == [()]
