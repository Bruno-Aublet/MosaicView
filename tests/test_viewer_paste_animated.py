"""Coller une image sur une page qui en contient plusieurs (visionneuse).

Valider un collage n'en garderait que la première image. L'icône grisée ne
protège que le clic : Ctrl+V et le glisser-déposer sont refusés eux aussi, sur
une image animée (GIF, WebP, PNG, AVIF) et sur un TIFF de plusieurs pages, et
restent permis sur une page ordinaire.

Les modules de la visionneuse importent PySide6 : les méthodes sont extraites
par AST (tests/ast_loader.load_methods), l'interface est remplacée.
"""
import sys
import types

import pytest

import ast_loader

ViewerBase = ast_loader.load_methods("paste_image_tool_qt.py", "PasteImageViewerMixin",
                                     {"_paste_image_refused_on_current_page"},
                                     _state_module=None)
ToolbarBase = ast_loader.load_methods("viewer_toolbar_qt.py", "_ViewerToolbar",
                                      {"paste_image_from_clipboard"})
CanvasBase = ast_loader.load_methods("image_viewer_qt.py", "_ViewerCanvas",
                                     {"_drag_has_acceptable_image"})

PASTED = object()
REFUSED_PAGES = [{"is_animated_gif": True}, {"is_animated_image": True},
                 {"is_multipage_tiff": True}]


class Viewer(ViewerBase):
    def __init__(self, entry):
        self.busy = False
        self.callbacks = {"state": types.SimpleNamespace(images_data=[entry],
                                                         operation_running=lambda: self.busy)}
        self.current_idx = 0


class Canvas(CanvasBase):
    def __init__(self, viewer=None):
        self._viewer = viewer
        self.added = []

    def _add_pasted_image(self, img):
        self.added.append(img)


class Toolbar(ToolbarBase):
    def __init__(self, viewer):
        self._viewer = viewer
        self.active_tool = None

    def set_active_tool(self, tool):
        self.active_tool = tool


@pytest.fixture(autouse=True)
def clipboard(monkeypatch):
    fake = types.SimpleNamespace(get_clipboard_single_image=lambda: PASTED,
                                 IMAGE_EXTS={".png"})
    monkeypatch.setitem(sys.modules, "modules.qt.clipboard_qt", fake)


def ctrl_v(entry):
    viewer = Viewer(entry)
    viewer._canvas = Canvas()
    toolbar = Toolbar(viewer)
    toolbar.paste_image_from_clipboard()
    return viewer._canvas.added, toolbar.active_tool


def png_drag():
    url = types.SimpleNamespace(isLocalFile=lambda: True, toLocalFile=lambda: "c:/a.png")
    return types.SimpleNamespace(hasUrls=lambda: True, urls=lambda: [url])


@pytest.mark.parametrize("entry", REFUSED_PAGES)
def test_ctrl_v_does_nothing_on_a_page_with_several_images(entry):
    assert ctrl_v(entry) == ([], None)


def test_ctrl_v_pastes_on_an_ordinary_page():
    assert ctrl_v({}) == ([PASTED], "paste_image")


@pytest.mark.parametrize("entry", REFUSED_PAGES)
def test_drag_is_refused_on_a_page_with_several_images(entry):
    assert Canvas(Viewer(entry))._drag_has_acceptable_image(png_drag()) is False


def test_drag_is_accepted_on_an_ordinary_page():
    assert Canvas(Viewer({}))._drag_has_acceptable_image(png_drag()) is True


def test_paste_is_refused_during_an_operation():
    viewer = Viewer({})
    viewer.busy = True
    assert viewer._paste_image_refused_on_current_page() is True


def test_page_outside_the_document_is_refused():
    viewer = Viewer({})
    viewer.current_idx = 3
    assert viewer._paste_image_refused_on_current_page() is True
