"""Erreurs signalées à l'utilisateur, jamais avalées ni transformées (Audit 7).

- Lot PDF → CBZ (batch_dialogs_qt._run_pdf_conversion) : un process PDF mort
  (plantage natif de MuPDF : la lecture du canal lève EOFError) est signalé
  pour ce PDF, remplacé pour le suivant, et le lot va jusqu'à son résumé.
- Ouverture de plusieurs archives (archive_loader.LoadWorker.
  load_multiple_archives) : toutes illisibles → erreur affichée (pas une
  annulation muette) ; la première illisible à la lecture de ses pages → aucune
  page partielle, erreur nommant l'archive, document nommé d'après la première
  archive lue. Les messages ne sont traduits qu'à l'affichage
  (read_errors.TranslatedLine) : ils suivent un changement de langue.
- Enregistrement d'une macro (macro_tool_qt._MacroNameDialog._on_save_clicked) :
  une écriture impossible est affichée dans la fenêtre, qui reste ouverte.

Ces modules importent PySide6 au niveau module : les fonctions sont extraites
par AST et exécutées seules, sans Qt.
"""
import ast
import contextlib
import gc
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import types
import zipfile
from pathlib import Path

import pytest
import rarfile

import modules.qt.archive_names as archive_names
import modules.qt.read_errors as read_errors
from modules.qt.archive_type_detector import detect_archive_type
from modules.qt.batch_claims import BatchClaims
from modules.qt.entries import create_entry

ROOT = Path(__file__).resolve().parent.parent


def _tr(key, **kw):
    return key + (" " + repr(sorted(kw.items())) if kw else "")


def _nodes(module, names, class_name=None, class_attrs=()):
    tree = ast.parse((ROOT / "modules" / "qt" / module).read_text(encoding="utf-8"))
    body = tree.body
    if class_name:
        body = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name).body
    found = [n for n in body if (isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names)
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) in names | set(class_attrs)
                                                   for t in n.targets))]
    return found


# ---------------------------------------------------------------------------
# Lot PDF → CBZ : process PDF mort
# ---------------------------------------------------------------------------

class _DeadConnection:
    """Canal d'un process PDF mort pendant l'ouverture d'un PDF : poll() voit
    le canal fermé, recv() lève EOFError (comportement mesuré sous Windows)."""

    def poll(self, timeout=0):
        return True

    def recv(self):
        raise EOFError

    def close(self):
        pass


def _load_pdf_batch(spawned):
    names = {"_run_pdf_conversion", "_start_batch_thread", "_claim_file", "_raise_if_batch_stopped",
             "_BatchInterrupted", "_BatchCancelled", "_batch_shutdown", "_batch_local",
             "_batch_threads", "_batch_thread_parents", "log_line", "log_title"}
    fake_pdf = types.ModuleType("modules.qt.pdf_loading_qt")

    def spawn():
        spawned.append(True)
        return object(), types.SimpleNamespace(put=lambda msg: None), _DeadConnection()
    fake_pdf._spawn_pdf_process = spawn
    fake_pdf._kill_pdf_process = lambda *a: None
    # Vraie lecture commune du canal (pdf_loading_qt importe Qt : extraite par AST)
    exec(compile(ast.Module(body=_nodes("pdf_loading_qt.py",
                                        {"read_pdf_message", "_stopped", "PDF_STOPPED_MESSAGE"}),
                            type_ignores=[]), "pdf_loading_qt.py", "exec"), fake_pdf.__dict__)
    return fake_pdf, names


class _Signal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


class _Signals:
    def __init__(self):
        for name in ("update_filename", "update_progress", "update_page_bar",
                     "update_thumb", "conversion_done"):
            setattr(self, name, _Signal())


class _Progress:
    def __init__(self, *a, **k):
        self.cancel_event = threading.Event()
        self.closed = False

    def show(self): pass
    def set_filename(self, *a): pass
    def set_progress(self, *a): pass
    def set_page_progress(self, *a): pass
    def set_thumbnail(self, *a): pass
    def mark_done(self): pass

    def close(self):
        self.closed = True


def test_dead_pdf_process_is_reported_and_the_batch_finishes(tmp_path, monkeypatch):
    spawned, summaries, progress = [], [], []
    fake_pdf, names = _load_pdf_batch(spawned)
    monkeypatch.setitem(sys.modules, "modules.qt.pdf_loading_qt", fake_pdf)
    # Process vu mort : ramassage des traces de plantage, jamais dans le vrai dossier
    from modules.qt import error_log
    monkeypatch.setattr(error_log, "collect_crash_buffers", lambda *a: None)
    pdfs = [str(tmp_path / "A.pdf"), str(tmp_path / "B.pdf")]
    for p in pdfs:
        Path(p).write_bytes(b"%PDF-1.4")

    def make_progress(*a, **k):
        progress.append(_Progress())
        return progress[-1]
    from modules.qt.temp_files import log_text, unique_log_path
    ns = {"os": os, "gc": gc, "io": io, "threading": threading, "contextlib": contextlib,
          "unique_log_path": unique_log_path, "log_text": log_text,
          "_": _tr, "_wt": _tr, "QApplication": types.SimpleNamespace(processEvents=lambda: None),
          "_start_batch_claims": lambda parent, files: BatchClaims.start(files),
          "_ProgressDialog": make_progress, "_ThreadSignals": _Signals,
          "_register_batch": lambda *a: None, "_unregister_batch": lambda *a: None,
          "show_batch_pdf_summary": lambda parent, data, cb: summaries.append(data)}
    exec(compile(ast.Module(body=_nodes("batch_dialogs_qt.py", names), type_ignores=[]),
                 "batch_dialogs_qt.py", "exec"), ns)
    errors = []
    monkeypatch.setattr(threading, "excepthook", lambda args: errors.append(args.exc_type))

    ns["_run_pdf_conversion"](None, pdfs, str(tmp_path), [str(tmp_path)],
                              {"get_mosaicview_temp_dir": lambda: str(tmp_path)}, False,
                              types.SimpleNamespace(renumber_mode=0))
    for thread in ns["_batch_threads"]:
        thread.join(10)

    assert errors == []
    [summary] = summaries
    assert summary["total"] == 2 and summary["converted_count"] == 0
    assert summary["errors_count"] == 2                 # les deux PDF ont été tentés
    assert len(spawned) == 3                            # process remplacé après chaque mort
    assert progress[0].closed                           # fenêtre de progression fermée
    log = Path(summary["log_path"]).read_text(encoding="utf-8")
    assert log.count("messages.errors.pdf_failure.process_stopped") == 2
    assert all(Path(p).exists() for p in pdfs)          # sources gardées
    assert BatchClaims.start(pdfs) is not None          # lot terminé


# ---------------------------------------------------------------------------
# Ouverture de plusieurs archives
# ---------------------------------------------------------------------------

def _load_worker_class(open_zip):
    funcs = {"_zip_member_reader", "_check_member_size", "_rar_read", "_tar_member_reader",
             "_natural_sort_key", "_list_7z_files", "_read_7z_file", "_is_7z_file_block", "_TYPE_TO_EXT",
             "_run_and_read", "AlteredMemberError", "_rar_bytes_without_crc", "create_page_entry",
             "_remember_unread_zip_member", "_archive_read_error"}
    methods = _nodes("archive_loader.py",
                     {"load_multiple_archives", "_get_namelist", "_resolve_filepath", "_emit_error"},
                     class_name="LoadWorker", class_attrs=("_EXT_TO_TYPE", "_ERROR_KEYS"))
    cls = ast.ClassDef(name="Worker", bases=[], keywords=[], decorator_list=[], body=methods)
    ns = dict(os=os, re=re, sys=sys, time=time, tarfile=tarfile, zipfile=zipfile, rarfile=rarfile,
              subprocess=subprocess, threading=threading, _=_tr,
              create_entry=create_entry, build_qimage_for_entry=lambda e: None,
              detect_archive_type=detect_archive_type, open_zip=open_zip,
              _tar_entry_name=archive_names.tar_entry_name,
              _without_epub_rank=archive_names.without_epub_rank,
              epub_page_name=archive_names.epub_page_name,
              epub_reading_order=archive_names.epub_reading_order,
              IMAGE_EXTS=archive_names.IMAGE_EXTS, MAX_MEMBER_SIZE=archive_names.MAX_MEMBER_SIZE,
              TranslatedLine=read_errors.TranslatedLine,
              files_ignored_line=read_errors.files_ignored_line)
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=_nodes("archive_loader.py", funcs) + [cls], type_ignores=[])),
        "archive_loader.py", "exec"), ns)
    worker = ns["Worker"]()
    worker._cancelled = threading.Event()
    worker.error_text_fn = None
    worker.emitted = []
    worker.progress = types.SimpleNamespace(emit=lambda *a: None)
    worker.error = types.SimpleNamespace(emit=lambda msg: worker.emitted.append(("error", msg)))
    worker.cancelled = types.SimpleNamespace(emit=lambda: worker.emitted.append(("cancelled",)))
    return worker


def _cbz(path, names):
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, b"data")


@pytest.fixture
def translation(monkeypatch):
    """Traduction de read_errors remplaçable pendant le test : `set(f)`
    simule un changement de langue."""
    current = {"fn": _tr}
    monkeypatch.setattr(read_errors, "_", lambda key, **kw: current["fn"](key, **kw))
    return types.SimpleNamespace(set=lambda fn: current.update(fn=fn))


def test_all_invalid_archives_are_reported_not_cancelled(tmp_path, translation):
    files = [tmp_path / "A.cbz", tmp_path / "B.cbz"]
    for f in files:
        f.write_bytes(b"pas une archive")
    worker = _load_worker_class(archive_names.open_zip)
    assert worker.load_multiple_archives([str(f) for f in files], [], [], [str(files[0])]) is False
    [(kind, message)] = worker.emitted
    assert kind == "error"
    assert "A.cbz" in message and "B.cbz" in message
    assert worker.error_text_fn() == message           # texte recalculé à l'affichage
    # Changement de langue pendant que la fenêtre est affichée : le texte suit
    translation.set(lambda key, **kw: "AUTRE LANGUE " + _tr(key, **kw))
    lines = worker.error_text_fn().splitlines()
    assert len(lines) == 2 and all(line.startswith("AUTRE LANGUE ") for line in lines)


def test_first_archive_unreadable_when_read_gives_no_partial_pages(tmp_path, translation):
    a, b = tmp_path / "A.cbz", tmp_path / "B.cbz"
    _cbz(a, ["a1.txt", "a2.txt"])
    _cbz(b, ["b1.txt", "b2.txt"])
    opened = {"A.cbz": 0}

    def open_zip(path):
        # Liste des noms : normale ; lecture des pages : A devenue illisible
        if os.path.basename(path) == "A.cbz":
            opened["A.cbz"] += 1
            if opened["A.cbz"] >= 2:
                raise OSError(22, "Argument non valide")
        return archive_names.open_zip(path)
    worker = _load_worker_class(open_zip)
    all_data, errors, first = [], [], [str(a)]
    assert worker.load_multiple_archives([str(a), str(b)], all_data, errors, first) is True
    assert os.path.basename(first[0]) == "B.cbz"
    assert [e["orig_name"] for e in all_data] == ["b1.txt", "b2.txt"]   # sans préfixe NEW-
    [error] = errors
    # Nom de l'archive et raison brute, traduite à l'affichage
    assert isinstance(error, read_errors.TranslatedLine)
    assert error.params == {"name": "A.cbz"} and error.raw_reason == "[Errno 22] Argument non valide"
    assert read_errors.files_ignored_line(error).startswith("messages.read_errors.line")


# ---------------------------------------------------------------------------
# Enregistrement d'une macro
# ---------------------------------------------------------------------------

def test_macro_save_failure_is_shown_and_the_window_stays_open(monkeypatch):
    import modules.qt.macro_engine as macro_engine
    method = _nodes("macro_tool_qt.py", {"_on_save_clicked"}, class_name="_MacroNameDialog")
    cls = ast.ClassDef(name="Dialog", bases=[], keywords=[], decorator_list=[], body=method)
    ns = {"_": _tr}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                 "macro_tool_qt.py", "exec"), ns)

    def refuse(macro):
        raise PermissionError(13, "Accès refusé")
    monkeypatch.setattr(macro_engine, "save_macro", refuse)
    monkeypatch.setattr(macro_engine, "list_macro_names", lambda: set())
    dialog = ns["Dialog"]()
    dialog._edit_name = types.SimpleNamespace(text=lambda: "Ma macro")
    dialog._edit_desc = types.SimpleNamespace(text=lambda: "")
    dialog._existing_name, dialog._rename, dialog._steps = None, False, []
    dialog._saved, dialog._on_saved, dialog.statuses, dialog.closed = False, None, [], False
    dialog._set_status = lambda fn: dialog.statuses.append(fn())
    dialog.close = lambda: setattr(dialog, "closed", True)

    dialog._on_save_clicked()

    assert dialog.statuses == ["dialogs.macro_name_error.save_failed [('error', 'Accès refusé')]"]
    assert not dialog._saved and not dialog.closed
