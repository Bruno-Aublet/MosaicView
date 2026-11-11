"""Question de qualité avant l'ajout d'un PDF au comic ouvert :
pdf_loading_qt.ask_pdf_merge_quality.

Le PDF est d'abord ouvert dans son process, la question n'est posée qu'ensuite :
- PDF protégé par mot de passe, abîmé, sans page, ou process arrêté : aucune
  question, seulement le message ; process arrêté, ajout abandonné ;
- PDF ouvert : la question est posée, avec le nom du PDF ; acceptée, le
  process qui a ouvert le PDF et son nombre de pages sont confiés à l'ajout ;
  refusée, process arrêté, aucun message ;
- document fermé ou remplacé pendant l'ouverture du PDF : aucune question.

pdf_loading_qt.py importe PySide6 : la fonction est extraite par AST
(tests/ast_loader.py), fenêtre, canal et minuterie remplacés.
"""
import os
import types

import pytest

from modules.qt.state import AppState

import ast_loader

STOPPED = ("process_stopped",)
PATH = os.path.join("C:\\", "Aaaa", "Bbbb.pdf")


class FakeDpi:
    def __init__(self, parent, title_key, label_key, offer_original_images=False, file_name=None):
        self.file_name = file_name
        self.images_only = self.join_cut_images = False
        self.on_result = None

    def ask_async(self, on_result):
        self.on_result = on_result


class FakeOpening:
    """« Ouverture du fichier PDF... » : visible ou non."""

    def __init__(self, canvas):
        self.visible = False

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


class Ctx:
    def __init__(self):
        self.state = AppState()
        self.opening = None
        self.messages = []          # réponses du process, dans l'ordre
        self.timers = []
        self.killed = []
        self.failures = []
        self.choices = []
        self.sent = []
        self.dialogs = []

    def read(self, conn, process, timeout):
        return self.messages.pop(0) if self.messages else None

    def tick(self):
        timers, self.timers = self.timers, []
        for fn in timers:
            fn()


@pytest.fixture
def ctx():
    c = Ctx()

    def make_dialog(*args, **kwargs):
        c.dialogs.append(FakeDpi(*args, **kwargs))
        return c.dialogs[-1]

    def make_opening(canvas):
        c.opening = FakeOpening(canvas)
        return c.opening

    fn = ast_loader.load(
        "pdf_loading_qt.py", {"ask_pdf_merge_quality"},
        PDF_AVAILABLE=True, os=os, DpiDialog=make_dialog, OpeningText=make_opening,
        _MsgDialog=None,
        _spawn_pdf_process=lambda: ("process", types.SimpleNamespace(put=c.sent.append), "conn"),
        read_pdf_message=c.read, PDF_STOPPED_MESSAGE=STOPPED, PDF_PROCESS_STOPPED="arrêté",
        QTimer=types.SimpleNamespace(singleShot=lambda ms, f: c.timers.append(f)),
        _kill_pdf_process=lambda *p: c.killed.append(p),
        _show_pdf_merge_failure=lambda win, msg: c.failures.append(msg),
    )["ask_pdf_merge_quality"]
    c.ask = lambda: fn(None, "canvas", PATH, c.state, c.choices.append)
    return c


def test_question_waits_until_the_pdf_is_opened(ctx):
    ctx.ask()
    assert ctx.sent == [("preopen", PATH)]
    ctx.tick()
    assert ctx.dialogs == [] and ctx.choices == []
    assert ctx.opening.visible                          # « Ouverture du fichier PDF... »
    ctx.messages.append(("preopen_ok", 80, False))
    ctx.tick()
    [dialog] = ctx.dialogs
    assert dialog.file_name == "Bbbb.pdf"               # nom du PDF dans la question
    assert not ctx.opening.visible


@pytest.mark.parametrize("message, failure", [
    (("password_error",), ("password_error",)),
    (("error", "abîmé"), ("error", "abîmé")),
    (("preopen_ok", 0, False), ("empty_pdf",)),
    (STOPPED, ("error", "arrêté")),
], ids=["mot de passe", "abîmé", "sans page", "process arrêté"])
def test_pdf_that_does_not_open_shows_only_its_message(ctx, message, failure):
    ctx.ask()
    ctx.messages.append(message)
    ctx.tick()
    assert ctx.dialogs == []                            # aucune question sans objet
    assert not ctx.opening.visible
    assert ctx.failures == [failure]
    assert len(ctx.killed) == 1 and ctx.killed[0][0] == "process"
    assert ctx.choices == [None]


def test_accepted_question_hands_over_the_process_and_page_count(ctx):
    ctx.messages.append(("preopen_ok", 80, False))
    ctx.ask()
    ctx.dialogs[0].on_result(150)
    [choice] = ctx.choices
    assert choice.dpi == 150 and choice.total_pages == 80
    assert choice.pdf_process[0] == "process" and choice.pdf_process[2] == "conn"
    assert ctx.killed == [] and ctx.failures == []


def test_refused_question_stops_the_process_without_message(ctx):
    ctx.messages.append(("preopen_ok", 80, False))
    ctx.ask()
    ctx.dialogs[0].on_result(None)
    assert len(ctx.killed) == 1 and ctx.failures == [] and ctx.choices == [None]


def test_document_closed_while_the_pdf_opens_asks_nothing(ctx):
    ctx.ask()
    ctx.state.doc_generation += 1
    ctx.messages.append(("preopen_ok", 80, False))
    ctx.tick()
    assert ctx.dialogs == [] and ctx.failures == [] and not ctx.opening.visible
    assert len(ctx.killed) == 1 and ctx.choices == [None]
