"""Export des vignettes (thumbnail_export_qt, raccourci caché Ctrl+Alt+T) : liste
des vignettes, écriture par un temporaire, JPEG décodé directement en petit,
thread qui signale toujours sa fin.

thumbnail_export_qt.py importe PySide6 : les fonctions sont extraites par AST.
"""
import io
import os
import threading
import types

from PIL import Image

import ast_loader
from modules.qt.entries import oriented

MODULE = "thumbnail_export_qt.py"
_utils = ast_loader.load("utils.py", {"windows_safe_relpath", "windows_safe_component",
                                      "_WINDOWS_RESERVED_NAMES", "_WINDOWS_FORBIDDEN_CHARS"}, os=os)


def _replace_file_atomically(dest, write_to):
    """Même contrat que utils.replace_file_atomically : écrit par un temporaire."""
    tmp = dest + ".tmp"
    write_to(tmp)
    os.replace(tmp, dest)


helpers = ast_loader.load(MODULE, {"thumbnail_items", "write_thumbnail", "_THUMB_EXTS"},
                          os=os, io=io, Image=Image, oriented=oriented)


def jpeg(size=(1200, 1800)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "JPEG")
    return buf.getvalue()


def test_items_flatten_folders_and_give_icons(monkeypatch):
    import sys
    fake_utils = types.ModuleType("modules.qt.utils")
    fake_utils.windows_safe_relpath = _utils["windows_safe_relpath"]
    monkeypatch.setitem(sys.modules, "modules.qt.utils", fake_utils)
    data = [{"orig_name": "Aaaa/001.jpg", "is_image": True, "bytes": b"x"},
            {"orig_name": "Aaaa/002.tif", "is_image": True, "bytes": b"y"},
            {"orig_name": "ComicInfo.xml", "is_image": False, "bytes": b"<x/>"},
            {"orig_name": "003.jpg", "is_image": True, "is_corrupted": True, "bytes": None}]
    names = [name for _entry, _raw, name in helpers["thumbnail_items"](data)]
    assert names == ["parent_dir.png", "Aaaa.png", "001.jpg", "002.png",
                     "ComicInfo.xml.png", "003.jpg.png"]


def test_thumbnail_is_written_small_upright_and_complete(monkeypatch, tmp_path):
    import sys
    fake_utils = types.ModuleType("modules.qt.utils")
    fake_utils.replace_file_atomically = _replace_file_atomically
    monkeypatch.setitem(sys.modules, "modules.qt.utils", fake_utils)
    dest = str(tmp_path / "001.jpg")
    helpers["write_thumbnail"](jpeg(), dest, (100, 150))
    with Image.open(dest) as written:
        assert written.format == "JPEG" and max(written.size) <= 150
    assert sorted(os.listdir(tmp_path)) == ["001.jpg"]          # aucun temporaire laissé


Worker = ast_loader.load_methods(MODULE, "_ThumbnailWorker", {"run", "_run"},
                                 write_thumbnail=lambda source, dest, size: None)


class FakeSignal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


def _refused(source, dest, size):
    if dest == "y.jpg":
        raise PermissionError(13, "Accès refusé", dest)


FailingWorker = ast_loader.load_methods(MODULE, "_ThumbnailWorker", {"run", "_run"},
                                        write_thumbnail=_refused)


def worker(jobs, cls=Worker):
    w = cls()
    w._jobs, w._size, w._written = jobs, (100, 150), 0
    w._failed, w._first_error = 0, ""
    w._cancelled = threading.Event()
    w.progress, w.done = FakeSignal(), FakeSignal()
    return w


def test_worker_reports_progress_and_its_end():
    w = worker([(b"a", "x.jpg"), (b"b", "y.jpg")])
    w.run()
    assert w.progress.emitted == [(1, 2), (2, 2)] and w.done.emitted == [(2, 0, "")]


def test_thumbnail_that_cannot_be_written_is_counted_with_its_reason():
    """Sautée sans rien dire, le résumé ne donnait que le nombre de fichiers écrits."""
    w = worker([(b"a", "x.jpg"), (b"b", "y.jpg"), (b"c", "z.jpg")], FailingWorker)
    w.run()
    [(written, failed, reason)] = w.done.emitted
    assert (written, failed) == (2, 1) and "Accès refusé" in reason
    assert w.progress.emitted[-1] == (3, 3)


def test_cancelled_worker_stops_and_still_reports_its_end():
    w = worker([(b"a", "x.jpg"), (b"b", "y.jpg")])
    w._cancelled.set()
    w.run()
    assert w.progress.emitted == [] and w.done.emitted == [(0, 0, "")]


def test_export_runs_in_a_thread_with_cancel_and_stops_on_close():
    """Et se souvient du dernier dossier choisi (réglage propre à cet export)."""
    source = (ast_loader.QT_DIR / MODULE).read_text(encoding="utf-8")
    for needed in ('config.get("last_thumbnail_export_dir"', 'config.set("last_thumbnail_export_dir", folder)',
                   "worker.start()", "register_cancel_on_close(canvas, _cancel)",
                   "unregister_cancel_on_close(canvas, _cancel)", "_show_cancel_item(",
                   "dispose_qthread(worker)", "state.doc_generation != generation"):
        assert needed in source, needed
