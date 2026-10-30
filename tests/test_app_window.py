"""AppWindowMixin (app_window_qt) : ce que fait toute fenêtre de l'application.

Méthodes extraites par ast_loader et exécutées sur une fausse fenêtre, sans
Qt : non-modalité et destruction à la fermeture, branchement et débranchement
de la traduction, placement avant l'affichage, Échap par le chemin de la croix ;
QuestionWindowMixin : réponse envoyée une seule fois (boutons, croix, Échap).
"""
import sys
import types

import ast_loader

METHODS = ("_setup_window", "_connect_language", "_on_language_signal", "_disconnect_language",
           "_apply_theme", "_retranslate", "show_nonmodal", "keyPressEvent")

Qt = types.SimpleNamespace(NonModal="NonModal", WA_DeleteOnClose="WA_DeleteOnClose",
                           WA_Resized="WA_Resized")
QKeySequence = types.SimpleNamespace(Cancel="Cancel")


class _Signal:
    def __init__(self):
        self.slots = []
        self.disconnected = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.disconnected.append(slot)
        self.slots.remove(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


def _note(name):
    def method(self, *args):
        self.calls.append((name,) + args)
    return method


class _Widget:
    """Fausse fenêtre Qt : note chaque appel dans self.calls."""

    def __init__(self, parent=None, resized=False):
        self.calls = []
        self._parent = parent
        self._resized = resized
        self.finished = _Signal()

    _setup_title_bar = _note("_setup_title_bar")
    setModal = _note("setModal")
    setWindowModality = _note("setWindowModality")
    setAttribute = _note("setAttribute")
    ensurePolished = _note("ensurePolished")
    adjustSize = _note("adjustSize")
    show = _note("show")
    raise_ = _note("raise_")
    activateWindow = _note("activateWindow")
    close = _note("close")
    keyPressEvent = _note("base.keyPressEvent")

    def parentWidget(self):
        return self._parent

    def testAttribute(self, attribute):
        return attribute == Qt.WA_Resized and self._resized


class QDialog(_Widget):
    pass


class _Event:
    def __init__(self, sequence):
        self.sequence = sequence
        self.accepted = False

    def matches(self, sequence):
        return sequence == self.sequence

    def accept(self):
        self.accepted = True


def _mixin(language_signal, destroy_links):
    return ast_loader.load_methods(
        "app_window_qt.py", "AppWindowMixin", METHODS, Qt=Qt, QDialog=QDialog,
        QKeySequence=QKeySequence, language_signal=language_signal,
        disconnect_on_destroy=lambda widget, handler: destroy_links.append((widget, handler)))


def _classes():
    signal = types.SimpleNamespace(changed=_Signal())
    links = []
    mixin = _mixin(signal, links)

    class Dialog(mixin, QDialog):
        def _apply_theme(self):
            self.calls.append(("theme",))

        def _retranslate(self):
            self.calls.append(("texts",))

    class Widget(mixin, _Widget):
        pass

    return Dialog, Widget, signal, links


def test_setup_window_makes_a_non_modal_window_deleted_on_close():
    Dialog, _Widget_cls, _signal, _links = _classes()
    panel = object()
    win = Dialog(parent=panel)
    win._setup_window()
    assert win.calls == [("_setup_title_bar",), ("setModal", False),
                         ("setWindowModality", "NonModal"),
                         ("setAttribute", "WA_DeleteOnClose")]
    assert win._center_parent is panel


def test_setup_window_options():
    Dialog, Widget, _signal, _links = _classes()
    other = object()
    win = Dialog(parent=object())
    win._setup_window(delete_on_close=False, center_on=other)
    assert ("setAttribute", "WA_DeleteOnClose") not in win.calls
    assert win._center_parent is other
    # Fenêtre QWidget (Bibliothèque) : pas de setModal, propre à QDialog
    widget = Widget()
    widget._setup_window(delete_on_close=False)
    assert widget.calls == [("_setup_title_bar",), ("setWindowModality", "NonModal")]


def test_language_applied_then_replayed_then_disconnected_once():
    Dialog, _Widget_cls, signal, links = _classes()
    win = Dialog()
    win._setup_window()
    win.calls.clear()
    win._connect_language()
    assert win.calls == [("theme",), ("texts",)]
    handler = win._lang_handler
    assert signal.changed.slots == [handler]
    assert links == [(win, handler)]
    signal.changed.emit("tlh")
    assert win.calls == [("theme",), ("texts",), ("theme",), ("texts",)]
    # Fermeture : finished débranche la traduction, une seule fois
    win.finished.emit(0)
    win.finished.emit(0)
    assert signal.changed.disconnected == [handler]
    assert signal.changed.slots == []


def test_language_connected_without_replaying_texts_already_set():
    Dialog, _Widget_cls, signal, _links = _classes()
    win = Dialog()
    win._setup_window()
    win.calls.clear()
    win._connect_language(apply_now=False)
    assert win.calls == []
    signal.changed.emit("sjn")
    assert win.calls == [("theme",), ("texts",)]


def test_disconnect_without_connection_does_nothing():
    Dialog, _Widget_cls, signal, _links = _classes()
    win = Dialog()
    win._setup_window()
    win._disconnect_language()
    assert signal.changed.disconnected == []


def test_widget_window_stays_translated_while_hidden():
    _Dialog, Widget, signal, _links = _classes()
    widget = Widget()
    widget._setup_window(delete_on_close=False)
    widget._connect_language()
    assert widget.finished.slots == []
    assert signal.changed.slots == [widget._lang_handler]


def test_default_hooks_do_nothing():
    _Dialog, Widget, _signal, _links = _classes()
    widget = Widget()
    widget._setup_window()
    widget.calls.clear()
    widget._connect_language()
    assert widget.calls == []


def _shown(monkeypatch, resized):
    fake = types.ModuleType("modules.qt.dialogs_qt")
    fake.position_dialog_on_parent = lambda dialog, parent: dialog.calls.append(
        ("position", parent))
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", fake)
    Dialog, _Widget_cls, _signal, _links = _classes()
    panel = object()
    win = Dialog(parent=panel, resized=resized)
    win._setup_window()
    win.calls.clear()
    win.show_nonmodal()
    return win.calls, panel


def test_show_nonmodal_sizes_then_places_before_showing(monkeypatch):
    calls, panel = _shown(monkeypatch, resized=False)
    assert calls == [("ensurePolished",), ("adjustSize",), ("position", panel), ("show",),
                     ("raise_",), ("activateWindow",)]


def test_show_nonmodal_keeps_a_size_given_by_the_code(monkeypatch):
    calls, panel = _shown(monkeypatch, resized=True)
    assert calls == [("ensurePolished",), ("position", panel), ("show",), ("raise_",),
                     ("activateWindow",)]


def test_escape_takes_the_path_of_the_close_button():
    Dialog, Widget, _signal, _links = _classes()
    win = Dialog()
    escape = _Event("Cancel")
    win.keyPressEvent(escape)
    assert win.calls == [("close",)] and escape.accepted
    other = _Event("Enter")
    win.keyPressEvent(other)
    assert win.calls == [("close",), ("base.keyPressEvent", other)]
    # Fenêtre QWidget : Échap ne la fermait pas, il ne la ferme toujours pas
    widget = Widget()
    escape = _Event("Cancel")
    widget.keyPressEvent(escape)
    assert widget.calls == [("base.keyPressEvent", escape)] and not escape.accepted


# ── QuestionWindowMixin ──────────────────────────────────────────────────────

QUESTION_METHODS = ("_setup_window", "_answer", "closeEvent", "reject", "ask_async")


class _QuestionBase(QDialog):
    """Fausse fenêtre de question : la croix (close) passe par closeEvent."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result_signal = _Signal()
        self.answers = []
        self.result_signal.connect(self.answers.append)

    hide = _note("hide")
    deleteLater = _note("deleteLater")

    def close(self):
        self.calls.append(("close",))
        self.closeEvent(_Event(None))


def _question():
    signal = types.SimpleNamespace(changed=_Signal())
    mixin = _mixin(signal, [])
    question = ast_loader.load_methods("app_window_qt.py", "QuestionWindowMixin",
                                       QUESTION_METHODS)

    class Question(question, mixin, _QuestionBase):
        # Avant AppWindowMixin dans l'ordre des bases : son show_nonmodal
        # importerait le vrai dialogs_qt
        show_nonmodal = _note("show_nonmodal")

    win = Question(parent=object())
    win._setup_window(close_answer="cancel", delete_on_close=False)
    win._connect_language()
    win.setup_calls = list(win.calls)
    win.calls.clear()
    return win, signal


def test_question_setup_passes_the_window_options():
    win, _signal = _question()
    assert win._close_answer == "cancel"
    assert win.setup_calls == [("_setup_title_bar",), ("setModal", False),
                               ("setWindowModality", "NonModal")]


def test_button_answer_is_sent_once_then_the_window_is_deleted():
    win, signal = _question()
    win._answer("yes")
    win._answer("no")
    win.close()
    assert win.answers == ["yes"]
    assert win.calls == [("hide",), ("deleteLater",), ("close",)]
    assert signal.changed.slots == []


def test_close_button_and_escape_send_the_default_answer_once():
    win, signal = _question()
    win.keyPressEvent(_Event("Cancel"))
    win.reject()
    win._answer("yes")
    assert win.answers == ["cancel"]
    assert signal.changed.slots == []


def test_ask_async_connects_then_shows():
    win, _signal = _question()
    received = []
    win.ask_async(received.append)
    assert win.calls == [("show_nonmodal",)]
    win._answer("no")
    assert received == ["no"]
