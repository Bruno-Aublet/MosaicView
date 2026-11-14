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
           "_apply_theme", "_retranslate", "show_nonmodal", "_show_as_tool_window",
           "keyPressEvent")

Qt = types.SimpleNamespace(NonModal="NonModal", WA_DeleteOnClose="WA_DeleteOnClose",
                           WA_Resized="WA_Resized",
                           WA_ShowWithoutActivating="WA_ShowWithoutActivating",
                           # Valeurs de Qt : type de fenêtre dans l'octet bas des drapeaux
                           WindowType_Mask=0xff, Window=0x01, Dialog=0x03, Tool=0x0b,
                           WindowStaysOnTopHint=0x40000)

# Fonctions de module appelées par show_nonmodal : la vraie règle de décision,
# et le panneau de chaque widget / le panneau utilisé, réglés par chaque test
shows_without_focus = ast_loader.load("app_window_qt.py",
                                      ["shows_without_focus"])["shows_without_focus"]
_WHERE = {"in_use": None}
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
        shows_without_focus=shows_without_focus,
        # Le centre de la fenêtre tient lieu de panneau source
        _panel_of=lambda widget: widget, _panel_in_use=lambda: _WHERE["in_use"],
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


def _shown(monkeypatch, resized, in_use=None, panel=None):
    fake = types.ModuleType("modules.qt.dialogs_qt")
    fake.position_dialog_on_parent = lambda dialog, parent: dialog.calls.append(
        ("position", parent))
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", fake)
    monkeypatch.setitem(_WHERE, "in_use", in_use)
    Dialog, _Widget_cls, _signal, _links = _classes()
    panel = panel if panel is not None else object()
    win = Dialog(parent=panel, resized=resized)
    win._setup_window()
    win.calls.clear()
    win.show_nonmodal()
    return win.calls, panel


ACTIVE = ("setAttribute", "WA_ShowWithoutActivating", False)
QUIET = ("setAttribute", "WA_ShowWithoutActivating", True)


def test_show_nonmodal_sizes_then_places_before_showing(monkeypatch):
    calls, panel = _shown(monkeypatch, resized=False)
    assert calls == [("ensurePolished",), ("adjustSize",), ("position", panel), ACTIVE,
                     ("show",), ("raise_",), ("activateWindow",)]


def test_show_nonmodal_keeps_a_size_given_by_the_code(monkeypatch):
    calls, panel = _shown(monkeypatch, resized=True)
    assert calls == [("ensurePolished",), ("position", panel), ACTIVE, ("show",),
                     ("raise_",), ("activateWindow",)]


def test_window_of_the_panel_in_use_takes_the_focus(monkeypatch):
    panel = object()
    calls, _panel = _shown(monkeypatch, resized=True, in_use=panel, panel=panel)
    assert calls == [("ensurePolished",), ("position", panel), ACTIVE, ("show",),
                     ("raise_",), ("activateWindow",)]


def test_window_of_the_other_panel_leaves_the_focus_where_it_is(monkeypatch):
    # Fin d'un travail dans un panneau pendant que l'utilisateur tape dans l'autre
    calls, panel = _shown(monkeypatch, resized=True, in_use=object())
    assert calls == [("ensurePolished",), ("position", panel), QUIET, ("show",)]


class _NeverShownDialog:
    """Fenêtre jamais affichée (FramedWindowMixin : _frame_installed faux),
    avec ses drapeaux Qt."""

    def __init__(self, *args, flags=0x03, **kwargs):
        super().__init__(*args, **kwargs)
        self._frame_installed = False
        self._flags = flags

    def windowFlags(self):
        return self._flags

    def windowType(self):
        return self._flags & Qt.WindowType_Mask

    def setWindowFlags(self, flags):
        self.calls.append(("setWindowFlags", flags))
        self._flags = flags


def _shown_quietly(monkeypatch, installed, flags=0x03):
    fake = types.ModuleType("modules.qt.dialogs_qt")
    fake.position_dialog_on_parent = lambda dialog, parent: None
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", fake)
    monkeypatch.setitem(_WHERE, "in_use", object())    # l'utilisateur est dans l'autre panneau
    Dialog, _Widget_cls, _signal, _links = _classes()

    class Shown(_NeverShownDialog, Dialog):
        pass

    win = Shown(parent=object(), resized=True, flags=flags)
    win._setup_window()
    win._frame_installed = installed
    win.calls.clear()
    win.show_nonmodal()
    return win


def test_window_shown_without_focus_is_a_tool_window(monkeypatch):
    # Qt ne ferme pas les menus ouverts à l'affichage d'une fenêtre Qt.Tool
    win = _shown_quietly(monkeypatch, installed=False,
                         flags=Qt.Dialog | Qt.WindowStaysOnTopHint)
    assert win.calls == [("ensurePolished",), QUIET,
                         ("setWindowFlags", Qt.Tool | Qt.WindowStaysOnTopHint), ("show",)]
    # Type d'origine gardé pour le choix des boutons de la barre de titre
    assert win._caption_type == Qt.Dialog


def test_window_already_shown_keeps_its_type(monkeypatch):
    # Fenêtre Windows déjà créée : un changement de type la recréerait
    win = _shown_quietly(monkeypatch, installed=True, flags=Qt.Window)
    assert win.calls == [("ensurePolished",), QUIET, ("show",)]
    assert not hasattr(win, "_caption_type")


def test_focus_rule_takes_the_focus_when_in_doubt():
    first, second = object(), object()
    assert shows_without_focus(first, second)
    assert not shows_without_focus(first, first)
    assert not shows_without_focus(None, second)    # fenêtre sans panneau (Bibliothèque)
    assert not shows_without_focus(first, None)     # application pas au premier plan


class _FakePanel:
    pass


class _Child:
    def __init__(self, parent):
        self._parent = parent

    def parentWidget(self):
        return self._parent


def _panel_functions(monkeypatch, active_window, focus):
    fake = types.ModuleType("modules.qt.panel_widget")
    fake.PanelWidget = _FakePanel
    monkeypatch.setitem(sys.modules, "modules.qt.panel_widget", fake)
    app = types.SimpleNamespace(activeWindow=lambda: active_window, focusWidget=lambda: focus)
    return ast_loader.load("app_window_qt.py", ["_panel_of", "_panel_in_use"],
                           QApplication=app)


def test_panel_of_goes_up_through_windows(monkeypatch):
    panel = _FakePanel()
    dialog = _Child(panel)
    field = _Child(_Child(dialog))
    ns = _panel_functions(monkeypatch, None, None)
    assert ns["_panel_of"](field) is panel
    assert ns["_panel_of"](_Child(None)) is None
    assert ns["_panel_of"](None) is None


def test_panel_in_use(monkeypatch):
    left, right = _FakePanel(), _FakePanel()
    main = types.SimpleNamespace(_active_panel=right)
    # Fenêtre principale au premier plan : le panneau actif, même si le
    # clavier est resté dans l'autre (clic sur la barre de menus d'un panneau)
    ns = _panel_functions(monkeypatch, main, _Child(left))
    assert ns["_panel_in_use"]() is right
    # Autre fenêtre au premier plan (visionneuse d'un panneau) : son panneau
    viewer = types.SimpleNamespace()
    ns = _panel_functions(monkeypatch, viewer, _Child(_Child(left)))
    assert ns["_panel_in_use"]() is left
    ns = _panel_functions(monkeypatch, viewer, _Child(None))
    assert ns["_panel_in_use"]() is None
    # Application pas au premier plan : inconnu
    ns = _panel_functions(monkeypatch, None, _Child(left))
    assert ns["_panel_in_use"]() is None


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
