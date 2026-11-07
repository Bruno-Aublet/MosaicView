"""Commandes de la mosaïque tant qu'une visionneuse a du travail non validé.

La visionneuse est non modale. Une rotation, un miroir, un redressement, un
redimensionnement, une lecture de macro ou un Annuler lancé depuis la mosaïque
changerait une page sous un travail posé en pixels de la page d'avant : ces
commandes sont refusées, avec le message de la visionneuse, si l'une des pages
visées a un tel travail. Les autres pages restent libres.

Les modules concernés importent PySide6 : méthodes et fonctions extraites par
AST (tests/ast_loader), interface remplacée.
"""
import sys
import types

import pytest

from modules.qt import viewer_busy

import ast_loader

Base = ast_loader.load_methods(
    "rotation_tool_qt.py", "RotationViewerMixin",
    {"_current_page_has_pending_work", "_has_pending_page_work"})
RecordingBase = ast_loader.load_methods(
    "macro_tool_qt.py", "MacroViewerMixin", {"_macro_recording_on_page"},
    _viewer_state=lambda viewer: viewer._doc_state)
ViewerWork = ast_loader.load_methods(
    "image_viewer_qt.py", "ImageViewer", {"_has_unvalidated_work_on"})


class Store(set):
    """Travail rangé par page : `idx in store`, vrai s'il en reste."""


class Dialog:
    shown = []

    def __init__(self, parent, title, message):
        self.keys = (title, message)

    def show_nonmodal(self):
        Dialog.shown.append(self.keys)


class Viewer(Base, RecordingBase, ViewerWork):
    def __init__(self, state, current_idx=0, stored=None, recording=False, **canvas_work):
        canvas = dict(has_crop=False, _crop_start=None, has_line=False, _line_img_start=None,
                      has_text_blocks=False, has_shapes=False, has_pasted_images=False)
        canvas.update(canvas_work)
        self._canvas = types.SimpleNamespace(**canvas)
        self._doc_state = state
        self._closed = False
        self._macro_recording = recording
        self.current_idx = current_idx
        self._transp_work_img_by_page = Store()
        self._crop_by_page = Store()
        self._straighten_by_page = Store()
        self._text_blocks_by_page = Store()
        self._shapes_by_page = Store()
        self._pasted_images_by_page = Store()
        for store_name, idx in (stored or {}).items():
            getattr(self, store_name).add(idx)

    def _forget_outdated_transparency_work(self):
        pass


@pytest.fixture
def state(monkeypatch):
    Dialog.shown = []
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", types.SimpleNamespace(MsgDialog=Dialog))
    monkeypatch.setattr(viewer_busy, "image_viewer_refs", [])
    return types.SimpleNamespace(images_data=[{"n": 0}, {"n": 1}, {"n": 2}])


REFUSED = [("viewer.unvalidated_work_title", "viewer.rotation_unvalidated_work_message")]


@pytest.mark.parametrize("work", [{"has_crop": True}, {"_crop_start": object()}, {"has_line": True},
                                  {"_line_img_start": (1, 1)}, {"has_text_blocks": True},
                                  {"has_shapes": True}, {"has_pasted_images": True}])
def test_displayed_page_with_pending_work_is_refused(state, work):
    viewer_busy.image_viewer_refs.append(Viewer(state, current_idx=1, **work))
    assert viewer_busy.pending_page_work_refused(None, state, [state.images_data[1]]) is True
    assert Dialog.shown == REFUSED


@pytest.mark.parametrize("store", ["_crop_by_page", "_straighten_by_page", "_text_blocks_by_page",
                                   "_shapes_by_page", "_pasted_images_by_page"])
def test_page_left_with_pending_work_is_refused(state, store):
    viewer_busy.image_viewer_refs.append(Viewer(state, current_idx=0, stored={store: 2}))
    assert viewer_busy.pending_page_work_refused(None, state, [state.images_data[2]]) is True
    assert Dialog.shown == REFUSED


def test_other_pages_stay_free(state):
    viewer_busy.image_viewer_refs.append(
        Viewer(state, current_idx=1, stored={"_shapes_by_page": 2}, has_text_blocks=True))
    assert viewer_busy.pending_page_work_refused(None, state, [state.images_data[0]]) is False
    assert Dialog.shown == []


def test_a_selection_is_refused_if_one_of_its_pages_has_work(state):
    viewer_busy.image_viewer_refs.append(Viewer(state, current_idx=1, has_shapes=True))
    assert viewer_busy.pending_page_work_refused(None, state, state.images_data) is True


def test_whole_document_check_sees_work_on_any_page(state):
    viewer_busy.image_viewer_refs.append(Viewer(state, stored={"_shapes_by_page": 2}))
    assert viewer_busy.pending_page_work_refused(None, state) is True


def test_whole_document_check_without_work(state):
    viewer_busy.image_viewer_refs.append(Viewer(state))
    assert viewer_busy.pending_page_work_refused(None, state) is False
    assert Dialog.shown == []


def test_viewer_of_another_document_or_closed_is_ignored(state):
    other = types.SimpleNamespace(images_data=state.images_data)
    closed = Viewer(state, has_shapes=True)
    closed._closed = True
    viewer_busy.image_viewer_refs.extend([Viewer(other, has_shapes=True), closed])
    assert viewer_busy.pending_page_work_refused(None, state, state.images_data) is False


def test_destroyed_viewer_does_not_block(state):
    class Destroyed:
        _closed = False
        _doc_state = state

        def _has_pending_page_work(self, entries):
            raise RuntimeError("objet détruit")

    viewer_busy.image_viewer_refs.append(Destroyed())
    assert viewer_busy.pending_page_work_refused(None, state, state.images_data) is False


def test_no_open_viewer(state):
    assert viewer_busy.pending_page_work_refused(None, state) is False


# --- Enregistrement d'une macro -------------------------------------------------

def test_page_being_recorded_is_refused(state):
    viewer_busy.image_viewer_refs.append(Viewer(state, current_idx=1, recording=True))
    assert viewer_busy.pending_page_work_refused(None, state, [state.images_data[1]]) is True
    assert Dialog.shown == REFUSED


def test_other_pages_stay_free_during_a_recording(state):
    viewer_busy.image_viewer_refs.append(Viewer(state, current_idx=1, recording=True))
    assert viewer_busy.pending_page_work_refused(None, state, [state.images_data[0]]) is False


def test_undo_stays_free_during_a_recording(state):
    viewer_busy.image_viewer_refs.append(Viewer(state, current_idx=1, recording=True))
    assert viewer_busy.pending_page_work_refused(None, state) is False


# --- Enregistrer le comic avec du travail non validé ----------------------------

class Answer:
    shown = []
    answer = True

    def __init__(self, parent, title, message):
        Answer.shown.append((title, message()))

    def ask_async(self, on_result):
        on_result(Answer.answer)


CONFIRM = ast_loader.load("file_operations_qt.py", {"_confirm_viewer_work_left_out_qt"},
                          _=lambda key: key)["_confirm_viewer_work_left_out_qt"]


@pytest.fixture
def question(monkeypatch):
    Answer.shown, Answer.answer = [], True
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt",
                        types.SimpleNamespace(ConfirmYNDialog=Answer, MsgDialog=Dialog))


def ask(state, entries=None):
    results = []
    CONFIRM(None, results.append, state, entries)
    return results


QUESTION = [("viewer.unvalidated_work_title", "viewer.save_with_unvalidated_work_message")]


def test_save_without_a_viewer_asks_nothing(state, question):
    assert ask(state) == [True] and Answer.shown == []


def test_save_without_work_asks_nothing(state, question):
    viewer = Viewer(state)
    viewer._has_unvalidated_work = lambda: False
    viewer_busy.image_viewer_refs.append(viewer)
    assert ask(state) == [True] and Answer.shown == []


@pytest.mark.parametrize("answer", [True, False])
def test_save_with_work_follows_the_answer(state, question, answer):
    viewer = Viewer(state)
    viewer._has_unvalidated_work = lambda: True
    viewer_busy.image_viewer_refs.append(viewer)
    Answer.answer = answer
    assert ask(state) == [answer] and Answer.shown == QUESTION


def test_selection_with_work_on_a_selected_page_asks(state, question):
    viewer_busy.image_viewer_refs.append(Viewer(state, stored={"_shapes_by_page": 2}))
    assert ask(state, [state.images_data[2]]) == [True] and Answer.shown == QUESTION


def test_selection_with_pending_transparency_on_a_selected_page_asks(state, question):
    viewer_busy.image_viewer_refs.append(Viewer(state, stored={"_transp_work_img_by_page": 2}))
    assert ask(state, [state.images_data[2]]) == [True] and Answer.shown == QUESTION


def test_selection_without_work_on_its_pages_asks_nothing(state, question):
    viewer_busy.image_viewer_refs.append(Viewer(state, stored={"_shapes_by_page": 2}))
    assert ask(state, [state.images_data[0]]) == [True] and Answer.shown == []


def test_recording_a_macro_is_not_unvalidated_work(state, question):
    viewer_busy.image_viewer_refs.append(Viewer(state, recording=True))
    assert ask(state, [state.images_data[0]]) == [True] and Answer.shown == []


# --- Chaque commande de la mosaïque fait ce contrôle avant de rien modifier ---

def selection_callbacks(state, calls):
    state.selected_indices = {0}
    state.images_data[0]["is_image"] = True
    return {"state": state, "canvas": None, "save_state": lambda: calls.append("save_state")}


TRANSFORMS = ast_loader.load(
    "image_transforms_qt.py", {"rotate_selected_qt", "flip_selected_qt"},
    _state_module=types.SimpleNamespace(state=None),
    rotate_entry_data=None, flip_entry_data=None,
    pending_page_work_refused=lambda *args: True,
    _run_transform=lambda *args: pytest.fail("opération lancée malgré le refus"))

DESKEW = ast_loader.load(
    "deskew_qt.py", {"deskew_selected_qt"},
    _state_module=types.SimpleNamespace(state=None),
    pending_page_work_refused=lambda *args: True,
    _run_deskew=lambda *args: pytest.fail("opération lancée malgré le refus"))


@pytest.mark.parametrize("launch", [
    lambda callbacks: TRANSFORMS["rotate_selected_qt"](90, callbacks),
    lambda callbacks: TRANSFORMS["flip_selected_qt"]("horizontal", callbacks),
    lambda callbacks: DESKEW["deskew_selected_qt"](callbacks),
], ids=["rotation", "miroir", "redressement"])
def test_refused_command_changes_nothing(state, launch):
    calls = []
    launch(selection_callbacks(state, calls))
    assert calls == []          # pas même un état d'historique


def panel_command(name, state):
    Panel = ast_loader.load_methods(
        "panel_widget.py", "PanelWidget", {name},
        _undo_action_qt=lambda *args: pytest.fail("Annuler exécuté malgré le refus"),
        _redo_action_qt=lambda *args: pytest.fail("Refaire exécuté malgré le refus"))
    panel = Panel()
    panel._state = state
    panel._undo_redo_callbacks = lambda: ()
    panel._refresh_open_image_viewers = lambda: None
    return getattr(panel, name)


@pytest.mark.parametrize("name", ["_undo_action", "_redo_action"])
def test_undo_redo_from_the_mosaic_are_refused(state, name):
    viewer_busy.image_viewer_refs.append(Viewer(state, has_shapes=True))
    panel_command(name, state)()
    assert Dialog.shown == REFUSED


def test_macro_reading_from_the_mosaic_is_refused(state):
    Reader = ast_loader.load_methods("macro_tool_qt.py", "_MacroReadDialog", {"_on_play_clicked"})
    open_viewer = Viewer(state)
    open_viewer._has_unvalidated_work = lambda: True
    viewer_busy.image_viewer_refs.append(open_viewer)
    reader = Reader()
    reader._current_macro = lambda: {"steps": []}
    reader._refuse_if_busy = lambda: False
    reader._viewer = None
    reader._center_parent = None
    reader._state = state
    reader._entries = [state.images_data[0]]
    reader.close = lambda: pytest.fail("lecture lancée malgré le refus")
    reader._on_play_clicked()
    assert Dialog.shown == [("viewer.unvalidated_work_title",
                             "viewer.macro_read_unvalidated_work_message")]
