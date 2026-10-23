"""Opération en arrière-plan sur un document : fin normale, annulation,
fermeture du fichier pendant le traitement, résultat qui arrive trop tard.

Exemple exécuté : la rotation et le miroir (image_transforms_qt._run_transform),
dont le worker modifie les pages pendant que l'interface reste utilisable.
Séquences jouées à la main, sans thread : le faux worker émet ses signaux
quand le test le décide, dans l'ordre voulu (y compris après une fermeture).

Vérifié :
- fin normale : point d'historique de fin, document modifié, opération terminée ;
- annulation : retour à l'état d'avant (rollback), aucun point de fin ;
- fermeture du fichier pendant le traitement (force_close_file appelle
  utils.cancel_operations_on_close, puis change doc_generation) : rien n'est
  touché dans le document suivant, ni rollback ni point de fin, opération
  terminée et worker libéré ;
- résultat d'un document remplacé sans annulation (annulation trop tardive) :
  ignoré grâce à doc_generation ;
- erreur dans la fin : l'opération est quand même terminée.
Et les aides de utils.py : inscription, annulation à la fermeture, retrait.

image_transforms_qt.py importe PySide6 : la fonction est extraite par AST
(tests/ast_loader.py) ; overlay, worker et visionneuse sont remplacés.
"""
import sys
import threading
import types

import pytest

from modules.qt.state import AppState
from modules.qt.utils import (cancel_operations_on_close, register_cancel_on_close,
                              unregister_cancel_on_close)

import ast_loader
from fakes import Signal as _Signal


class FakeWorker:
    """Worker dont le test émet lui-même les signaux."""
    created = []

    def __init__(self, entries, operation, state):
        self.entries, self.state = entries, state
        self.progress, self.done, self.cancelled = _Signal(), _Signal(), _Signal()
        self._cancelled = threading.Event()
        self.failures, self.failed_entries = [], []
        self.started = False
        FakeWorker.created.append(self)

    def start(self):
        self.started = True


class Canvas:
    def __init__(self):
        self.refreshed = []

    def refresh_thumbnail(self, idx):
        self.refreshed.append(idx)

    def refresh_duplicate_overlay(self):
        pass


@pytest.fixture
def run(monkeypatch):
    """(_run_transform, ensemble des workers libérés)."""
    FakeWorker.created = []
    disposed = []
    viewers = types.ModuleType("modules.qt.image_viewer_qt")
    viewers.refresh_image_viewers_after_external_undo_redo = lambda state: None
    web = types.ModuleType("modules.qt.web_import_qt")
    web._show_cancel_item = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "modules.qt.image_viewer_qt", viewers)
    monkeypatch.setitem(sys.modules, "modules.qt.web_import_qt", web)
    ns = ast_loader.load(
        "image_transforms_qt.py", {"_run_transform", "_active_workers"},
        _state_module=types.SimpleNamespace(state=None), _=lambda key, **kw: key,
        _show_canvas_text=lambda *a: None, _hide_canvas_text=lambda *a: None,
        dispose_qthread=disposed.append, register_cancel_on_close=register_cancel_on_close,
        unregister_cancel_on_close=unregister_cancel_on_close, _TransformWorker=FakeWorker)
    return ns["_run_transform"], disposed


def page(name):
    return {"orig_name": name, "bytes": b"x", "extension": ".png", "is_image": True}


class Document:
    """Document d'un panneau et ses rappels, comptés."""

    def __init__(self):
        self.state = AppState()
        self.state.images_data = [page("p01.png"), page("p02.png")]
        self.canvas = Canvas()
        self.saved = 0
        self.rollbacks = 0

    def callbacks(self):
        def save():
            self.saved += 1

        def rollback():
            self.rollbacks += 1
        return {"state": self.state, "canvas": self.canvas, "save_state": save,
                "rollback": rollback}

    def start(self, run_transform):
        run_transform(list(self.state.images_data), lambda e, st: True, "labels.rotating",
                      self.callbacks())
        [worker] = FakeWorker.created
        assert worker.started and self.state.operation_running()
        return worker

    def close_file(self):
        """Comme force_close_file : annulation des opérations inscrites,
        puis document vidé et changé."""
        cancel_operations_on_close(self.canvas)
        self.state.images_data = []
        self.state.doc_generation += 1

    def finished(self, disposed, worker):
        return (not self.state.operation_running() and disposed == [worker]
                and not getattr(self.canvas, "_cancel_on_close", []))


def test_normal_end_records_the_change(run):
    run_transform, disposed = run
    doc = Document()
    worker = doc.start(run_transform)
    worker.done.emit()
    assert doc.saved == 1 and doc.rollbacks == 0
    assert doc.state.modified is True
    assert doc.canvas.refreshed == [0, 1]
    assert doc.finished(disposed, worker)


def test_cancel_restores_the_document(run):
    run_transform, disposed = run
    doc = Document()
    worker = doc.start(run_transform)
    doc.canvas._cancel_on_close[0]()             # bouton Annuler
    assert worker._cancelled.is_set()
    worker.cancelled.emit()                      # le worker s'arrête ensuite
    assert doc.rollbacks == 1 and doc.saved == 0
    assert doc.finished(disposed, worker)


def test_cancel_after_the_last_page_still_restores(run):
    """Annulé pendant la dernière page : le worker émet done au lieu de cancelled."""
    run_transform, disposed = run
    doc = Document()
    worker = doc.start(run_transform)
    doc.canvas._cancel_on_close[0]()
    worker.done.emit()
    assert doc.rollbacks == 1 and doc.saved == 0
    assert doc.finished(disposed, worker)


def test_file_closed_during_the_operation_touches_nothing(run):
    run_transform, disposed = run
    doc = Document()
    worker = doc.start(run_transform)
    doc.close_file()
    assert worker._cancelled.is_set()            # arrêté par la fermeture
    worker.cancelled.emit()
    assert doc.rollbacks == 0 and doc.saved == 0  # l'historique est celui d'un autre document
    assert doc.state.modified is False
    assert doc.finished(disposed, worker)


def test_late_result_for_a_replaced_document_is_ignored(run):
    """Annulation impossible ou trop tardive : la fin arrive quand le panneau
    montre déjà un autre fichier ; doc_generation la fait abandonner."""
    run_transform, disposed = run
    doc = Document()
    worker = doc.start(run_transform)
    doc.state.images_data = [page("autre.png")]
    doc.state.doc_generation += 1
    worker.done.emit()
    assert doc.saved == 0 and doc.rollbacks == 0
    assert doc.state.modified is False
    assert doc.canvas.refreshed == []
    assert doc.finished(disposed, worker)


def test_error_in_the_end_still_ends_the_operation(run):
    run_transform, disposed = run
    doc = Document()
    worker = doc.start(run_transform)

    def broken(idx):
        raise RuntimeError("vignette")
    doc.canvas.refresh_thumbnail = broken
    with pytest.raises(RuntimeError):
        worker.done.emit()
    assert not doc.state.operation_running()     # le document reste modifiable
    assert disposed == [worker]


# ---------------------------------------------------------------------------
# utils : arrêt des opérations à la fermeture du fichier
# ---------------------------------------------------------------------------

def test_every_registered_operation_is_cancelled_once_on_close():
    canvas, called = types.SimpleNamespace(), []
    register_cancel_on_close(canvas, lambda: called.append("a"))
    register_cancel_on_close(canvas, lambda: called.append("b"))
    cancel_operations_on_close(canvas)
    cancel_operations_on_close(canvas)            # seconde fermeture : plus rien
    assert called == ["a", "b"]


def test_failing_cancel_does_not_prevent_the_others():
    canvas, called = types.SimpleNamespace(), []

    def broken():
        raise RuntimeError("annulation")
    register_cancel_on_close(canvas, broken)
    register_cancel_on_close(canvas, lambda: called.append("b"))
    cancel_operations_on_close(canvas)
    assert called == ["b"]


def test_finished_operation_is_no_longer_cancelled():
    canvas, called = types.SimpleNamespace(), []
    cancel = lambda: called.append("a")  # noqa: E731
    register_cancel_on_close(canvas, cancel)
    unregister_cancel_on_close(canvas, cancel)
    unregister_cancel_on_close(canvas, cancel)    # sans effet la seconde fois
    cancel_operations_on_close(canvas)
    assert called == []


def test_canvas_without_operation_closes_quietly():
    cancel_operations_on_close(types.SimpleNamespace())


# ---------------------------------------------------------------------------
# Redressement automatique (deskew_qt._run_deskew) : même déroulement que la
# rotation, fin résumée dans une fenêtre
# ---------------------------------------------------------------------------

class FakeDeskewWorker:
    created = []

    def __init__(self, entries, state):
        self.progress, self.done, self.cancelled = _Signal(), _Signal(), _Signal()
        self._cancelled = threading.Event()
        self.straightened, self.success_count, self.failed_names = [], 0, []
        self.started = False
        FakeDeskewWorker.created.append(self)

    def start(self):
        self.started = True


@pytest.fixture
def run_deskew(monkeypatch):
    """(_run_deskew, workers libérés, résumés affichés)."""
    FakeDeskewWorker.created = []
    disposed, summaries = [], []
    viewers = types.ModuleType("modules.qt.image_viewer_qt")
    viewers.refresh_image_viewers_after_external_undo_redo = lambda state: None
    web = types.ModuleType("modules.qt.web_import_qt")
    web._show_cancel_item = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "modules.qt.image_viewer_qt", viewers)
    monkeypatch.setitem(sys.modules, "modules.qt.web_import_qt", web)

    class Summary:
        def __init__(self, parent, success, failed):
            summaries.append((success, list(failed)))

        def show_nonmodal(self):
            pass
    ns = ast_loader.load(
        "deskew_qt.py", {"_run_deskew", "_active_workers"},
        _state_module=types.SimpleNamespace(state=None), _=lambda key, **kw: key,
        _show_canvas_text=lambda *a: None, _hide_canvas_text=lambda *a: None,
        dispose_qthread=disposed.append, register_cancel_on_close=register_cancel_on_close,
        unregister_cancel_on_close=unregister_cancel_on_close, _DeskewWorker=FakeDeskewWorker,
        _DeskewSummaryDialog=Summary)
    return ns["_run_deskew"], disposed, summaries


def start_deskew(doc, run):
    run(list(doc.state.images_data), doc.callbacks())
    [worker] = FakeDeskewWorker.created
    assert worker.started and doc.state.operation_running()
    return worker


def test_deskew_normal_end_records_the_change_and_shows_the_summary(run_deskew):
    run, disposed, summaries = run_deskew
    doc = Document()
    worker = start_deskew(doc, run)
    worker.straightened = list(doc.state.images_data)
    worker.success_count, worker.failed_names = 1, ["p02.png"]
    worker.done.emit()
    assert doc.saved == 1 and doc.rollbacks == 0 and doc.state.modified is True
    assert summaries == [(1, ["p02.png"])]
    assert doc.finished(disposed, worker)


def test_deskew_cancel_restores_the_document(run_deskew):
    run, disposed, summaries = run_deskew
    doc = Document()
    worker = start_deskew(doc, run)
    doc.canvas._cancel_on_close[0]()                 # bouton Annuler
    worker.cancelled.emit()
    assert doc.rollbacks == 1 and doc.saved == 0 and summaries == []
    assert doc.finished(disposed, worker)


def test_deskew_file_closed_during_the_operation_touches_nothing(run_deskew):
    run, disposed, summaries = run_deskew
    doc = Document()
    worker = start_deskew(doc, run)
    doc.close_file()
    assert worker._cancelled.is_set()
    worker.cancelled.emit()
    assert doc.rollbacks == 0 and doc.saved == 0 and summaries == []
    assert doc.finished(disposed, worker)


def test_deskew_late_result_for_a_replaced_document_is_ignored(run_deskew):
    run, disposed, summaries = run_deskew
    doc = Document()
    worker = start_deskew(doc, run)
    doc.state.doc_generation += 1
    worker.done.emit()
    assert doc.saved == 0 and doc.state.modified is False and summaries == []
    assert doc.finished(disposed, worker)


# ---------------------------------------------------------------------------
# Redimensionnement (resize_dialog_qt._start_resize_worker) : l'annulation
# remet les octets d'origine des pages ; la fin peut arriver deux fois
# (signal `finished` du worker, puis celui de la fin réelle du thread)
# ---------------------------------------------------------------------------

class FakeResizeWorker:
    created = []

    def __init__(self, entries, state, *settings):
        self.entries = entries
        self.progress, self.finished, self.cancelled = _Signal(), _Signal(), _Signal()
        self._cancelled = threading.Event()
        self.resized, self.failures = [], []
        self.started = False
        FakeResizeWorker.created.append(self)

    def start(self):
        self.started = True

    def wait(self):                                   # utils.dispose_qthread
        pass

    def deleteLater(self):
        pass


@pytest.fixture
def run_resize(monkeypatch):
    """Fonction de lancement ; fenêtres « Fichiers ignorés » affichées."""
    FakeResizeWorker.created = []
    warnings = []
    modules = {
        "modules.qt.canvas_overlay_qt": {"show_canvas_text": lambda *a: None,
                                         "hide_canvas_text": lambda *a: None},
        "modules.qt.web_import_qt": {"_show_cancel_item": lambda *a, **k: None},
        "modules.qt.image_viewer_qt": {
            "refresh_image_viewers_after_external_undo_redo": lambda state: None},
        "modules.qt.metadata_signal": {
            "metadata_pages_signal": types.SimpleNamespace(emit=lambda state: None)},
        "modules.qt.archive_loader": {
            "_MessageDialog": lambda *a: types.SimpleNamespace(
                show_nonmodal=lambda: warnings.append(a[2]()))},
    }
    for name, attrs in modules.items():
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    from modules.qt.entries import free_image_memory
    start = ast_loader.load("resize_dialog_qt.py", {"_start_resize_worker"},
                            _=lambda key, **kw: key + repr(sorted(kw.items())),
                            _ResizeWorker=FakeResizeWorker,
                            free_image_memory=free_image_memory)["_start_resize_worker"]
    return start, warnings


class ResizeDocument:
    def __init__(self):
        from modules.qt.undo_redo import save_state_data
        self.state = AppState()
        self.pages = [page("p01.png"), page("p02.png")]
        self.state.images_data = list(self.pages)
        save_state_data(self.state, force=True)
        self.original = {id(p): p["bytes"] for p in self.pages}
        save_state_data(self.state, force=True)          # état « avant », poussé au lancement
        self.canvas = Canvas()
        self.saved = 0

    def start(self, start_fn, pushed_before=True):
        def save():
            self.saved += 1
        start_fn(self.canvas, self.pages, self.state,
                 False, None, None, 0.5, False, 2, False, False, None, None, {}, {}, {},
                 self.original, save, lambda: None, lambda: None, lambda: None, pushed_before)
        [worker] = FakeResizeWorker.created
        assert worker.started and self.state.operation_running()
        return worker

    def resize_pages(self):
        """Ce que le worker fait aux pages pendant le traitement."""
        for p in self.pages:
            p["bytes"] = b"redimensionnee"


def test_resize_normal_end_is_handled_once_although_finished_comes_twice(run_resize):
    start, warnings = run_resize
    doc = ResizeDocument()
    worker = doc.start(start)
    doc.resize_pages()
    worker.resized = list(doc.pages)
    worker.finished.emit()
    worker.finished.emit()                            # fin réelle du thread
    assert doc.saved == 1 and doc.state.modified is True
    assert all(p["bytes"] == b"redimensionnee" for p in doc.pages)   # jamais défait
    assert not doc.state.operation_running()


def test_resize_cancel_gives_back_the_original_bytes_and_the_history(run_resize):
    start, _warnings = run_resize
    doc = ResizeDocument()
    steps = len(doc.state.history)
    worker = doc.start(start)
    doc.resize_pages()
    doc.canvas._cancel_on_close[0]()                 # bouton Annuler
    worker.cancelled.emit()
    worker.finished.emit()                            # fin réelle du thread : ignorée
    assert [p["bytes"] for p in doc.pages] == [b"x", b"x"]
    assert len(doc.state.history) == steps - 1       # état « avant » retiré
    assert doc.state.modified is False and doc.saved == 0
    assert not doc.state.operation_running()


def test_resize_cancelled_during_the_last_page_is_still_restored(run_resize):
    start, _warnings = run_resize
    doc = ResizeDocument()
    worker = doc.start(start)
    doc.resize_pages()
    doc.canvas._cancel_on_close[0]()
    worker.finished.emit()                            # finished au lieu de cancelled
    assert [p["bytes"] for p in doc.pages] == [b"x", b"x"]
    assert doc.saved == 0


def test_resize_cancel_keeps_a_change_recorded_meanwhile(run_resize):
    """Une autre action enregistrée pendant le traitement : ni son état
    d'historique ni son « modifié » ne sont défaits par l'annulation."""
    from modules.qt.undo_redo import save_state_data
    start, _warnings = run_resize
    doc = ResizeDocument()
    worker = doc.start(start)
    doc.state.images_data.append(page("p03.png"))
    save_state_data(doc.state, force=True)
    doc.state.modified = True
    steps = len(doc.state.history)
    doc.canvas._cancel_on_close[0]()
    worker.cancelled.emit()
    assert len(doc.state.history) == steps and doc.state.modified is True


def test_resize_file_closed_during_the_operation_touches_nothing(run_resize):
    start, _warnings = run_resize
    doc = ResizeDocument()
    worker = doc.start(start)
    doc.resize_pages()
    cancel_operations_on_close(doc.canvas)
    doc.state.doc_generation += 1
    worker.cancelled.emit()
    assert all(p["bytes"] == b"redimensionnee" for p in doc.pages)   # pages d'un document fermé
    assert doc.saved == 0 and not doc.state.operation_running()


def test_resize_late_result_for_a_replaced_document_is_ignored(run_resize):
    start, _warnings = run_resize
    doc = ResizeDocument()
    worker = doc.start(start)
    doc.state.doc_generation += 1
    worker.finished.emit()
    assert doc.saved == 0 and doc.state.modified is False
    assert not doc.state.operation_running()


def test_resize_failed_pages_are_listed(run_resize):
    start, warnings = run_resize
    doc = ResizeDocument()
    worker = doc.start(start)
    worker.failures = ["p02.png"]
    worker.finished.emit()
    assert len(warnings) == 1 and "p02.png" in warnings[0]


# ---------------------------------------------------------------------------
# Jointure d'images animées (merge_dialog_qt.MergeDialog._start_animated_join) :
# le worker ne touche pas au document ; la fin crée la page jointe et propose
# de supprimer les sources
# ---------------------------------------------------------------------------

class FakeJoinWorker:
    created = []

    def __init__(self, sources, durations, adjustment_mode, save_format):
        self.save_format = save_format
        self.progress, self.done, self.cancelled = _Signal(), _Signal(), _Signal()
        self._cancelled = threading.Event()
        self.started = False
        FakeJoinWorker.created.append(self)

    def start(self):
        self.started = True


def animated_gif():
    import io
    from PIL import Image
    frames = [Image.new("P", (8, 8), c) for c in (1, 2)]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=50, loop=0)
    return buf.getvalue()


@pytest.fixture
def run_join(monkeypatch):
    """(fenêtre de jointure, workers libérés, fenêtres d'erreur affichées)."""
    FakeJoinWorker.created = []
    disposed, errors = [], []
    web = types.ModuleType("modules.qt.web_import_qt")
    web._show_cancel_item = lambda *a, **k: None
    dialogs = types.ModuleType("modules.qt.dialogs_qt")
    dialogs.ErrorDialog = lambda parent, title, message: types.SimpleNamespace(
        show_nonmodal=lambda: errors.append(message()))
    monkeypatch.setitem(sys.modules, "modules.qt.web_import_qt", web)
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", dialogs)
    from modules.qt.archive_names import IMAGE_EXTS
    from modules.qt.entries import create_entry
    active = []
    Base = ast_loader.load_methods(
        "merge_dialog_qt.py", "MergeDialog", {"_start_animated_join"},
        _=lambda key, **kw: key, _wt=lambda key: key,
        _show_canvas_text=lambda *a: None, _hide_canvas_text=lambda *a: None,
        _active_workers=active, dispose_qthread=disposed.append,
        register_cancel_on_close=register_cancel_on_close,
        unregister_cancel_on_close=unregister_cancel_on_close,
        _AnimatedJoinWorker=FakeJoinWorker, _get_next_collage_number=lambda state: 1,
        create_entry=create_entry, IMAGE_EXTS=IMAGE_EXTS)

    class JoinDialog(Base):
        def __init__(self, extensions=(".gif", ".gif")):
            self._state = AppState()
            self._state.images_data = [dict(page(f"p0{i}{ext}"), extension=ext)
                                       for i, ext in enumerate(extensions)]
            self._doc_generation = self._state.doc_generation
            self.canvas = Canvas()
            self._callbacks = {"canvas": self.canvas}
            self.panel = object()
            self.closed, self.asked = False, []

        def parent(self):
            return self.panel

        def close(self):
            self.closed = True

        def _ask_delete_sources(self, parent, new_entry):
            self.asked.append((parent, new_entry))

        def start(self):
            positions = [{"entry": e, "x": 0, "y": 0} for e in self._state.images_data]
            self._start_animated_join(positions, "keep")
            [worker] = FakeJoinWorker.created
            assert worker.started and self.closed and active[-1] is worker
            return worker

        def released(self, worker):
            return (disposed == [worker] and active == []
                    and not getattr(self.canvas, "_cancel_on_close", None))
    return JoinDialog, errors


def test_animated_join_end_adds_the_page_and_asks_about_the_sources(run_join):
    JoinDialog, errors = run_join
    dlg = JoinDialog()
    worker = dlg.start()
    assert worker.save_format == "GIF"
    data = animated_gif()
    worker.done.emit(data)
    [(parent, entry)] = dlg.asked
    assert parent is dlg.panel                        # la fenêtre de jointure est fermée
    assert entry["orig_name"] == "dialogs.join.merged_filename_prefix_01.gif"
    assert entry["bytes"] == data and entry["is_image"]
    assert errors == [] and dlg.released(worker)


def test_animated_join_keeps_the_common_format_of_the_sources(run_join):
    JoinDialog, _errors = run_join
    worker = JoinDialog((".webp", ".webp")).start()
    assert worker.save_format == "WEBP"
    FakeJoinWorker.created = []
    worker = JoinDialog((".webp", ".png")).start()
    assert worker.save_format == "GIF"                # formats différents


def test_animated_join_cancel_adds_nothing(run_join):
    JoinDialog, errors = run_join
    dlg = JoinDialog()
    worker = dlg.start()
    dlg.canvas._cancel_on_close[0]()                 # bouton Annuler
    assert worker._cancelled.is_set()
    worker.done.emit(animated_gif())                  # résultat déjà parti : ignoré
    assert dlg.asked == [] and errors == [] and dlg.released(worker)


def test_animated_join_file_closed_during_the_operation_adds_nothing(run_join):
    JoinDialog, errors = run_join
    dlg = JoinDialog()
    worker = dlg.start()
    cancel_operations_on_close(dlg.canvas)
    dlg._state.doc_generation += 1
    worker.cancelled.emit()
    assert worker._cancelled.is_set()
    assert dlg.asked == [] and errors == [] and dlg.released(worker)


def test_animated_join_late_result_for_a_replaced_document_is_ignored(run_join):
    JoinDialog, errors = run_join
    dlg = JoinDialog()
    worker = dlg.start()
    dlg._state.doc_generation += 1
    worker.done.emit(animated_gif())
    assert dlg.asked == [] and errors == [] and dlg.released(worker)


def test_animated_join_failure_is_reported(run_join):
    JoinDialog, errors = run_join
    dlg = JoinDialog()
    worker = dlg.start()
    worker.done.emit(None)
    assert errors == ["messages.errors.join_failed.message"]
    assert dlg.asked == [] and dlg.released(worker)
