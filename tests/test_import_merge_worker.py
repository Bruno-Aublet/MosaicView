"""Ajout d'une archive à un comic ouvert : import_merge_qt.ImportMergeWorker.run
(lecture des pages, avant leur insertion dans le document).

Vérifié, sur de vraies archives ZIP et TAR :
- pages lues dans l'ordre naturel, préfixées (NEW01-…, dossier compris) pour
  ne jamais prendre le nom d'une page existante, archive d'origine notée ;
  ComicInfo.xml gardé sous son nom (il devient les métadonnées du comic s'il
  n'en a pas, sinon une annexe, au moment de l'insertion) ;
- un membre illisible n'arrête jamais l'ajout : page illisible (cadre rouge)
  avec sa raison, ou ligne de « Fichiers ignorés » pour un autre fichier ;
  page d'un ZIP restée sans données : emplacement de son membre gardé (pour
  la copier telle quelle) ;
- TIFF de plusieurs pages : une page du comic par page ;
- CBZ qui n'est pas un ZIP : signal « mal nommé », rien d'ajouté ;
- annulation : aucune page rendue ; archive illisible : erreur rendue.

import_merge_qt.py et archive_loader.py importent PySide6 : la méthode et
les lecteurs sont extraits par AST (tests/ast_loader.py), signaux remplacés.
"""
import io
import os
import re
import tarfile
import threading
import zipfile

import pytest
import rarfile
from PIL import Image

from modules.qt import archive_names
from modules.qt.archive_names import IMAGE_EXTS, epub_page_name, epub_reading_order, open_zip
from modules.qt.comic_info import is_comic_info_name
from modules.qt.entries import create_entry

import ast_loader
from fakes import Signal as _Signal

READERS = ast_loader.load(
    "archive_loader.py",
    {"_natural_sort_key", "_zip_member_reader", "_remember_unread_zip_member",
     "_check_member_size", "_tar_member_reader", "_tar_info_read", "create_page_entry",
     "AlteredMemberError", "_rar_read", "_expand_multipage_tiffs_with_thumbnails"},
    os=os, re=re, zipfile=zipfile, rarfile=rarfile, MAX_MEMBER_SIZE=archive_names.MAX_MEMBER_SIZE,
    create_entry=create_entry, IMAGE_EXTS=IMAGE_EXTS, build_qimage_for_entry=lambda entry: None,
    _rar_bytes_without_crc=lambda *a: (_ for _ in ()).throw(AssertionError("UnRAR non prévu")))


def make_worker():
    names = ("progress", "finished", "error", "cancelled", "bad_zip_file", "bad_rar_file")
    Base = ast_loader.load_methods(
        "import_merge_qt.py", "ImportMergeWorker", {"run"},
        os=os, zipfile=zipfile, tarfile=tarfile, rarfile=rarfile, IMAGE_EXTS=IMAGE_EXTS,
        create_entry=create_entry, open_zip=open_zip, epub_page_name=epub_page_name,
        epub_reading_order=epub_reading_order, is_comic_info_name=is_comic_info_name,
        _tar_entry_name=archive_names.tar_entry_name, _list_7z_files=None, _read_7z_file=None,
        **{k: READERS[k] for k in ("_natural_sort_key", "_zip_member_reader",
                                   "_remember_unread_zip_member", "_tar_member_reader",
                                   "_rar_read", "create_page_entry",
                                   "_expand_multipage_tiffs_with_thumbnails")})

    class Worker(Base):
        def __init__(self, filepath, prefix="NEW01-"):
            self._filepath, self._merge_prefix = filepath, prefix
            self._cancelled = threading.Event()
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


def test_folder_and_name_are_both_prefixed(tmp_path):
    src = make_tar(tmp_path / "Bbbb.cbt", [("Ch1/001.png", png("red"))])
    entries, _errors = finished(run(src))
    assert [e["orig_name"] for e in entries] == ["NEW01-Ch1/NEW01-001.png"]


def test_unreadable_members_never_stop_the_addition(tmp_path, monkeypatch):
    monkeypatch.setitem(READERS["_check_member_size"].__globals__, "MAX_MEMBER_SIZE", 500)
    src = make_zip(tmp_path / "Bbbb.cbz", [("p01.png", png("red")), ("p02.png", b"x" * 5000),
                                           ("info.nfo", b"y" * 5000)])
    entries, errors = finished(run(src))
    by_name = {e["orig_name"]: e for e in entries}
    unread = by_name["NEW01-p02.png"]
    assert unread["is_corrupted"] and unread["bytes"] is None
    assert unread["corruption_reason"]                  # raison sur la page (cadre rouge)
    assert unread["unread_member"]["path"] == os.path.abspath(src)
    assert by_name["NEW01-p01.png"]["is_corrupted"] is False
    assert len(errors) == 1 and errors[0].startswith("info.nfo: ")   # « Fichiers ignorés »


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
    assert worker.bad_zip_file.emitted == [()]
    assert worker.finished.emitted == []


def test_cancelled_addition_returns_no_page(tmp_path):
    src = make_zip(tmp_path / "Bbbb.cbz", [("p01.png", png("red"))])
    worker = Worker(src)
    worker._cancelled.set()
    worker.run()
    assert worker.cancelled.emitted == [()]
    assert worker.finished.emitted == []


def test_unreadable_archive_is_reported(tmp_path):
    src = tmp_path / "Bbbb.cbt"
    src.write_bytes(b"ni tar ni rien")
    worker = run(str(src))
    assert worker.finished.emitted == []
    [(message,)] = worker.error.emitted
    assert message
