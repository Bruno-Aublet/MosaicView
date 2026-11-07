"""Une seule visionneuse par panneau.

Deux fenêtres sur le même document écriraient les mêmes pages chacune de leur
côté. Toute demande d'ouverture sur un panneau dont la visionneuse est déjà
ouverte la ramène au premier plan sur la page demandée, son travail non validé
restant rangé par page. Les deux panneaux gardent chacun la leur.

image_viewer_qt.py et macro_tool_qt.py importent PySide6 : fonctions et méthodes
extraites par AST (tests/ast_loader), interface remplacée.
"""
import types

import pytest

from modules.qt import viewer_busy

import ast_loader


class FakeViewer:
    """Visionneuse ouverte, ou construite par le code testé."""
    built = []

    def __init__(self, parent=None, idx=None, callbacks=None, initial_tool=None):
        self._doc_state = (callbacks or {}).get("state")
        self._closed = False
        self.idx, self.initial_tool = idx, initial_tool
        self.calls = []
        FakeViewer.built.append(self)

    def show_nonmodal(self):
        self.calls.append("show_nonmodal")

    def show_page(self, idx, tool=None):
        self.calls.append(("show_page", idx, tool))


def document(**first_page):
    page = {"is_image": True}
    page.update(first_page)
    return types.SimpleNamespace(images_data=[page, {"is_image": True}], selected_indices={0})


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(viewer_busy, "image_viewer_refs", [])
    FakeViewer.built = []


def opened_on(state):
    viewer = FakeViewer(callbacks={"state": state})
    viewer_busy.image_viewer_refs.append(viewer)
    FakeViewer.built = []
    return viewer


def test_open_viewer_on_finds_the_viewer_of_this_document():
    state = document()
    opened_on(document())
    viewer = opened_on(state)
    assert viewer_busy.open_viewer_on(state) is viewer


def test_open_viewer_on_ignores_a_closed_viewer():
    state = document()
    opened_on(state)._closed = True
    assert viewer_busy.open_viewer_on(state) is None


open_image_viewer = ast_loader.load(
    "image_viewer_qt.py", {"open_image_viewer"},
    _state_module=types.SimpleNamespace(state=None), ImageViewer=FakeViewer,
    open_viewer_on=lambda state: viewer_busy.open_viewer_on(state))["open_image_viewer"]


def test_first_opening_creates_the_viewer():
    state = document()
    open_image_viewer(None, 1, {"state": state}, initial_tool="crop")
    assert len(FakeViewer.built) == 1
    assert (FakeViewer.built[0].idx, FakeViewer.built[0].initial_tool) == (1, "crop")
    assert FakeViewer.built[0].calls == ["show_nonmodal"]


def test_second_opening_reuses_the_open_viewer():
    state = document()
    viewer = opened_on(state)
    open_image_viewer(None, 1, {"state": state}, initial_tool="text")
    assert FakeViewer.built == []
    assert viewer.calls == [("show_page", 1, "text")]


def test_each_panel_keeps_its_own_viewer():
    other_panel = opened_on(document())
    state = document()
    open_image_viewer(None, 1, {"state": state})
    assert len(FakeViewer.built) == 1 and other_panel.calls == []


def test_corrupted_page_does_not_open_or_move_the_viewer():
    state = document(is_corrupted=True)
    viewer = opened_on(state)
    open_image_viewer(None, 0, {"state": state})
    assert FakeViewer.built == [] and viewer.calls == []


ShowPage = ast_loader.load_methods("image_viewer_qt.py", "ImageViewer", {"show_page"},
                                   INITIAL_TOOLS=("crop", "straighten", "clone", "text"))


class Shown(ShowPage):
    def __init__(self, current_idx=0, active_tool=None, stroke=False, reading=False, recording=False):
        self.current_idx = current_idx
        self._macro_reading, self._macro_recording = reading, recording
        self._stroke = stroke
        self.front = 0
        self.positions = []
        self.selected = []
        self._toolbar = types.SimpleNamespace(active_tool=active_tool, set_active_tool=self.selected.append)

    def bring_to_front(self):
        self.front += 1

    def _paint_stroke_blocks_command(self):
        return self._stroke

    def get_image_indices(self):
        return [0, 2, 5]

    def _show_image_position(self, img_indices, position):
        self.positions.append((img_indices, position))


def test_show_page_goes_to_the_requested_page():
    viewer = Shown()
    viewer.show_page(5)
    assert viewer.front == 1 and viewer.positions == [([0, 2, 5], 2)] and viewer.selected == []


def test_show_page_on_the_displayed_page_only_comes_to_the_front():
    viewer = Shown(current_idx=2)
    viewer.show_page(2)
    assert viewer.front == 1 and viewer.positions == []


@pytest.mark.parametrize("busy", [{"stroke": True}, {"reading": True}, {"recording": True}],
                         ids=["coup de pinceau", "lecture de macro", "enregistrement de macro"])
def test_show_page_never_changes_page_while_it_is_forbidden(busy):
    viewer = Shown(**busy)
    viewer.show_page(5, "crop")
    assert viewer.front == 1 and viewer.positions == [] and viewer.selected == []


def test_show_page_selects_the_requested_tool():
    viewer = Shown()
    viewer.show_page(0, "crop")
    assert viewer.selected == ["crop"]


def test_show_page_does_not_reselect_the_active_tool():
    viewer = Shown(active_tool="crop")
    viewer.show_page(0, "crop")
    assert viewer.selected == []


def make_viewer(state, parent=None, callbacks=None):
    return ast_loader.load_inner(
        "macro_tool_qt.py", ("read_macro_on_selection_qt",), {"_make_viewer"},
        state=state, entries=[state.images_data[0]], parent=parent, callbacks=callbacks,
        ImageViewer=FakeViewer,
        open_viewer_on=lambda st: viewer_busy.open_viewer_on(st))["_make_viewer"]


def test_macro_from_the_mosaic_uses_the_open_viewer_and_leaves_it_open():
    state = document()
    viewer = opened_on(state)
    assert make_viewer(state)() is viewer
    assert FakeViewer.built == [] and viewer.calls == [("show_page", 0, None)]
    assert not getattr(viewer, "_macro_read_transient_viewer", False)


def test_macro_from_the_mosaic_creates_a_transient_viewer_otherwise():
    state = document()
    viewer = make_viewer(state, callbacks={"state": state})()
    assert FakeViewer.built == [viewer] and viewer._macro_read_transient_viewer is True
    assert viewer.calls == ["show_nonmodal"]
