"""Conversion de format des pages sélectionnées : déroulement complet
(conversion_dialogs_qt.perform_conversion et _ConversionWorker._run).

Vérifié :
- pages converties insérées après la dernière page sélectionnée, dans
  l'ordre, au fil de leur arrivée ;
- seuls les originaux réellement convertis sont proposés à la suppression ;
  les échecs sont expliqués page par page ; rien de converti : aucune
  question, opération terminée ;
- l'opération reste en cours jusqu'à la réponse à « Supprimer les
  originaux ? », puis se termine (point d'historique) ;
- Annuler : pages déjà insérées retirées, page arrivée ensuite ignorée ;
  fichier fermé pendant la conversion ou pendant la question : rien n'est
  touché dans le document suivant, opération terminée ;
- le worker n'ajoute aux originaux convertis que les pages réellement
  converties (vraie conversion, page illisible comprise).

conversion_dialogs_qt.py importe PySide6 : les fonctions sont extraites par
AST (tests/ast_loader.py) ; worker, fenêtres et texte de progression sont
remplacés, la vraie conversion (image_ops.convert_image_data) est employée
par le worker.
"""
import io
import sys
import threading
import types

import pytest
from PIL import Image

from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import create_entry, free_image_memory
from modules.qt.image_ops import _ANIMATABLE_FORMATS, convert_image_data, is_animated_entry
from modules.qt.state import AppState
from modules.qt.utils import cancel_operations_on_close

import ast_loader
from fakes import Signal as _Signal, tr as _tr


class FakeWorker:
    created = []

    def __init__(self, target_format, quality, selected_entries, lossless=False):
        self.selected = selected_entries
        self._cancelled = threading.Event()
        self.converted_sources, self.failures = [], []
        for name in ("progress", "entry_ready", "finished", "cancelled"):
            setattr(self, name, _Signal())
        FakeWorker.created.append(self)

    def start(self):
        pass

    def wait(self):
        pass

    def deleteLater(self):
        pass


class Ui:
    """Fenêtres et overlay remplacés."""

    def __init__(self):
        self.errors, self.questions, self.cancel_button = [], [], None

    def show_cancel_item(self, canvas, text, holder, cancel_fn, anchor_lbl=None):
        self.cancel_button = cancel_fn

    def error_dialog(self, parent, title_fn, message_fn, **kwargs):
        self.errors.append(message_fn())
        return types.SimpleNamespace(show_nonmodal=lambda: None)

    def complete_dialog(self, parent, converted, target_format, sources, converted_entries,
                        callbacks, on_done=None, animation_lost=0):
        self.questions.append({"sources": sources, "converted": converted, "on_done": on_done,
                               "animation_lost": animation_lost})


@pytest.fixture
def ui(monkeypatch):
    FakeWorker.created = []
    u = Ui()
    web = types.ModuleType("modules.qt.web_import_qt")
    web._show_cancel_item = u.show_cancel_item
    dq = types.ModuleType("modules.qt.dialogs_qt")
    dq.ErrorDialog = u.error_dialog
    monkeypatch.setitem(sys.modules, "modules.qt.web_import_qt", web)
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", dq)
    u.ns = ast_loader.load(
        "conversion_dialogs_qt.py", {"perform_conversion", "_conversion_failures_message"},
        _=_tr, _wt=_tr, _state_module=types.SimpleNamespace(state=None),
        _show_canvas_text=lambda *a: None, _hide_canvas_text=lambda *a: None,
        _ConversionWorker=FakeWorker, show_conversion_complete_dialog=u.complete_dialog,
        _ANIMATABLE_FORMATS=_ANIMATABLE_FORMATS, is_animated_entry=is_animated_entry)
    return u


def png(name, color):
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(buf, "PNG")
    return create_entry(name, buf.getvalue(), IMAGE_EXTS)


class Document:
    def __init__(self):
        self.state = AppState()
        self.a, self.b, self.c = png("a.png", "red"), png("b.png", "green"), png("c.png", "blue")
        self.state.images_data = [self.a, self.b, self.c]
        self.state.selected_indices = {0, 1}
        self.saves = 0
        self.parent = types.SimpleNamespace(_canvas=types.SimpleNamespace())

    def callbacks(self):
        def save():
            self.saves += 1
        return {"state": self.state, "save_state": save, "render_mosaic": lambda: None,
                "update_button_text": lambda: None}

    def start(self, ui, fmt="WEBP"):
        ui.ns["perform_conversion"](self.parent, fmt, 90, [self.a, self.b], self.callbacks())
        [worker] = FakeWorker.created
        assert self.state.operation_running()
        return worker

    def names(self):
        return [e["orig_name"] for e in self.state.images_data]


def converted(name):
    return {"orig_name": name, "bytes": b"x", "extension": ".webp", "is_image": True}


def test_converted_pages_arrive_after_the_selection_in_order(ui):
    doc = Document()
    worker = doc.start(ui)
    worker.entry_ready.emit(converted("a.webp"))
    worker.entry_ready.emit(converted("b.webp"))
    assert doc.names() == ["a.png", "b.png", "a.webp", "b.webp", "c.png"]


def test_only_converted_originals_are_offered_for_deletion(ui):
    doc = Document()
    worker = doc.start(ui)
    worker.entry_ready.emit(converted("a.webp"))
    worker.converted_sources = [doc.a]
    worker.failures = [{"kind": "too_large", "name": "b.png", "width": 20000, "height": 20000,
                        "max": 16383}]
    worker.finished.emit([], 1)
    [question] = ui.questions
    assert question["sources"] == [doc.a]
    assert len(ui.errors) == 1 and "b.png" in ui.errors[0]   # cause expliquée
    assert doc.state.operation_running()                      # jusqu'à la réponse
    question["on_done"]("keep_all")
    assert not doc.state.operation_running()
    assert doc.saves == 2                                     # avant, puis après la réponse


def test_nothing_converted_ends_without_question(ui):
    doc = Document()
    worker = doc.start(ui)
    worker.failures = [{"kind": "unreadable", "name": "a.png"}]
    worker.finished.emit([], 0)
    assert ui.questions == [] and len(ui.errors) == 1
    assert not doc.state.operation_running()


def test_cancel_removes_inserted_pages_and_ignores_late_ones(ui):
    doc = Document()
    worker = doc.start(ui)
    worker.entry_ready.emit(converted("a.webp"))
    ui.cancel_button()                                        # bouton Annuler
    assert worker._cancelled.is_set()
    assert doc.names() == ["a.png", "b.png", "c.png"]
    worker.entry_ready.emit(converted("b.webp"))              # arrivée après l'annulation
    worker.cancelled.emit()
    assert doc.names() == ["a.png", "b.png", "c.png"]
    assert not doc.state.operation_running()


def close_file(doc):
    """Comme force_close_file : opérations annulées, document vidé et changé."""
    cancel_operations_on_close(doc.parent._canvas)
    doc.state.images_data = []
    doc.state.running_operations = []
    doc.state.doc_generation += 1


def test_file_closed_during_the_conversion_touches_nothing(ui):
    doc = Document()
    worker = doc.start(ui)
    close_file(doc)
    assert worker._cancelled.is_set()
    worker.entry_ready.emit(converted("a.webp"))
    worker.finished.emit([], 1)
    assert doc.state.images_data == [] and ui.questions == []
    assert not doc.state.operation_running()


def test_file_closed_during_the_question_touches_nothing(ui):
    doc = Document()
    worker = doc.start(ui)
    worker.entry_ready.emit(converted("a.webp"))
    worker.converted_sources = [doc.a]
    worker.finished.emit([], 1)
    saves = doc.saves
    close_file(doc)
    ui.questions[0]["on_done"]("delete_orig")
    assert doc.saves == saves and doc.state.images_data == []


def test_operation_started_meanwhile_prevents_the_conversion(ui):
    doc = Document()
    doc.state.begin_operation(object())
    ui.ns["perform_conversion"](doc.parent, "WEBP", 90, [doc.a], doc.callbacks())
    assert FakeWorker.created == [] and doc.saves == 0


# ---------------------------------------------------------------------------
# _ConversionWorker._run : vraie conversion
# ---------------------------------------------------------------------------

def test_worker_lists_only_the_pages_really_converted(monkeypatch):
    mosaic = types.ModuleType("modules.qt.mosaic_canvas")
    mosaic.build_qimage_for_entry = lambda entry: None
    monkeypatch.setitem(sys.modules, "modules.qt.mosaic_canvas", mosaic)
    Base = ast_loader.load_methods("conversion_dialogs_qt.py", "_ConversionWorker", {"_run"},
                                   convert_image_data=convert_image_data,
                                   free_image_memory=free_image_memory)

    class Worker(Base):
        def __init__(self, entries):
            self._target_format, self._quality, self._lossless = "WEBP", 90, False
            self._selected_entries = entries
            self._cancelled = threading.Event()
            self.converted_sources, self.failures = [], []
            for name in ("progress", "entry_ready", "finished", "cancelled"):
                setattr(self, name, _Signal())

    good, bad = png("a.png", "red"), create_entry("b.png", b"illisible", IMAGE_EXTS)
    ready = []
    worker = Worker([good, bad])
    worker.entry_ready.connect(ready.append)
    worker._run()
    assert worker.converted_sources == [good]
    assert [f["name"] for f in worker.failures] == ["b.png"]
    [new] = ready
    assert Image.open(io.BytesIO(new["bytes"])).format == "WEBP"
