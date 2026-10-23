"""Annulation de l'import de métadonnées en lot pendant l'écriture d'un
fichier (_BatchMetadataOrchestrator.cancel).

L'écriture n'est arrêtée qu'avant l'écriture d'un fichier du CBZ : passé le
dernier, elle va au bout (remplacement, relecture, suppression de
l'original). Le résumé attend donc sa fin réelle, le fichier est compté s'il a
été écrit, et les fichiers du lot restent réservés jusque-là.

batch_metadata_dialog_qt.py importe PySide6 au niveau module : les méthodes de
l'orchestrateur sont extraites par AST dans une classe sans Qt. Le worker
d'écriture est un faux, dont la fin est déclenchée par le test.
"""
import ast
import os
import threading
import types
from pathlib import Path

from modules.qt.batch_claims import BatchClaims

ROOT = Path(__file__).resolve().parent.parent
METHODS = {"cancel", "_finish_cancel", "_retire_save_worker", "_retire_current_worker",
           "_show_summary", "_on_save_done", "_on_next", "_on_dialog_closed"}


class _Signal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def disconnect(self, *args):
        self._slots.clear()

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


class FakeSaveWorker:
    """_SaveWorker : done/error personnalisés, finished natif de QThread."""

    def __init__(self):
        self.done, self.error, self.finished = _Signal(), _Signal(), _Signal()
        self._cancelled = threading.Event()
        self.running = True

    def isRunning(self):
        return self.running

    def end(self, written):
        """Fin réelle de run() : done si l'écriture est allée au bout, rien
        si elle a été arrêtée (_SaveCancelled), puis finished."""
        self.running = False
        if written:
            self.done.emit("")
        self.finished.emit()


def _load():
    source = (ROOT / "modules" / "qt" / "batch_metadata_dialog_qt.py").read_text(encoding="utf-8")
    orchestrator = next(n for n in ast.parse(source).body
                        if isinstance(n, ast.ClassDef) and n.name == "_BatchMetadataOrchestrator")
    body = [n for n in orchestrator.body if isinstance(n, ast.FunctionDef) and n.name in METHODS]
    assert {n.name for n in body} == METHODS
    cls = ast.ClassDef(name="Orchestrator", bases=[], keywords=[], decorator_list=[], body=body)
    timers = []
    ns = {"os": os, "_": lambda key, **kw: key,
          "QTimer": types.SimpleNamespace(singleShot=lambda ms, fn: timers.append(fn))}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                 "batch_metadata_dialog_qt.py", "exec"), ns)
    return ns, timers


_NS, TIMERS = _load()


def make_orchestrator(tmp_path):
    """Orchestrateur pendant l'écriture du premier fichier d'un lot de deux."""
    files = [str(tmp_path / "a.cbr"), str(tmp_path / "b.cbr")]
    summaries, loads = [], []
    _NS["_show_metadata_summary"] = lambda parent, data: summaries.append(dict(data))
    o = _NS["Orchestrator"]()
    o._claims = BatchClaims.start(files)
    o._claims.claim(files[0])
    o._files, o._index = files, 0
    o._done_count, o._skipped_count, o._errors = 0, 0, []
    o._ended, o._cancelling = False, False
    o._pending_timer, o._worker, o._current_dlg = None, None, None
    o._orphan_workers, o._parent, o._callbacks = [], None, {}
    o._stop_pdf_probe = lambda: None
    o._show_converting_overlay = lambda *a: None
    o._lock_nav_buttons = o._reset_dialog = lambda *a: None
    o._load_and_open = lambda *a, **k: loads.append(True)
    worker = FakeSaveWorker()
    o._save_worker = worker
    worker.done.connect(lambda error, orig=files[0]: o._on_save_done(error, orig))
    return o, worker, files, summaries, loads


def test_summary_waits_for_the_write_and_counts_the_written_file(tmp_path):
    o, worker, files, summaries, loads = make_orchestrator(tmp_path)
    o.cancel()
    assert worker._cancelled.is_set()
    assert summaries == []
    assert BatchClaims.start(files) is None          # fichiers toujours réservés
    o._on_dialog_closed()                            # fermeture de la fenêtre ComicVine
    worker.end(written=True)
    assert summaries == [{"total": 2, "done": 1, "skipped": 0, "errors": 0, "log_path": None,
                          "log_error": None}]
    assert loads == []                               # aucun fichier suivant chargé
    released = BatchClaims.start(files)              # lot terminé : fichiers libres
    assert released is not None
    released.finish()


def test_stopped_write_is_not_counted(tmp_path):
    o, worker, files, summaries, loads = make_orchestrator(tmp_path)
    o.cancel()
    worker.end(written=False)
    assert summaries == [{"total": 2, "done": 0, "skipped": 0, "errors": 0, "log_path": None,
                          "log_error": None}]
    assert loads == []


def test_write_ending_while_cancel_connects_is_not_missed(tmp_path):
    """Écriture finie entre le test isRunning() et le branchement sur son
    finished : le résumé est quand même affiché, une seule fois."""
    o, worker, files, summaries, loads = make_orchestrator(tmp_path)
    connect = worker.finished.connect

    def connect_after_end(slot):
        worker.running = False
        connect(slot)
    worker.finished.connect = connect_after_end
    TIMERS.clear()
    o.cancel()
    worker.done.emit("")
    for fn in TIMERS:
        fn()
    worker.finished.emit()
    assert summaries == [{"total": 2, "done": 1, "skipped": 0, "errors": 0, "log_path": None,
                          "log_error": None}]


def test_cancel_without_write_in_progress_shows_the_summary_at_once(tmp_path):
    o, worker, files, summaries, loads = make_orchestrator(tmp_path)
    worker.running = False
    o.cancel()
    assert summaries == [{"total": 2, "done": 0, "skipped": 0, "errors": 0, "log_path": None,
                          "log_error": None}]
