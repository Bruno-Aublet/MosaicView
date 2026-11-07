"""Dans un sous-dossier de la mosaïque, le clic droit ne fait rien (aucun menu,
MosaicCanvas.mousePressEvent), et le double-clic sur un fichier non plus
(mouseDoubleClickEvent). mosaic_canvas.py importe PySide6 : les méthodes sont
extraites par AST, l'événement et la vignette sont remplacés."""
import types

import ast_loader

RIGHT, LEFT = "droit", "gauche"


class Thumb:
    real_idx = 0


Canvas = ast_loader.load_methods(
    "mosaic_canvas.py", "MosaicCanvas", {"mousePressEvent", "_in_subfolder"},
    Qt=types.SimpleNamespace(RightButton=RIGHT, LeftButton=LEFT),
    ThumbnailItem=Thumb, DirItem=type("DirItem", (), {}),
    QRect=object, QRubberBand=object, QSize=object)


class FakeCanvas(Canvas):
    def __init__(self, directory):
        self._state = types.SimpleNamespace(current_directory=directory)
        self._context_menu_open = False
        self.menus = []
        self._context_menu_callback = lambda pos, idx: self.menus.append("image")
        self._dir_context_menu_callback = lambda pos: self.menus.append("dossier")
        self._canvas_context_menu_callback = lambda pos: self.menus.append("fond")

    def mapToScene(self, point):
        return point

    def _item_at(self, scene_pos):
        return Thumb()


def right_click():
    point = types.SimpleNamespace(toPoint=lambda: (5, 5))
    return types.SimpleNamespace(button=lambda: RIGHT, position=lambda: point,
                                 globalPosition=lambda: point, pos=lambda: (5, 5))


def test_right_click_in_a_subfolder_opens_no_menu():
    canvas = FakeCanvas("Aaaa/")
    canvas.mousePressEvent(right_click())
    assert canvas.menus == []


def test_left_click_on_an_image_in_a_subfolder_selects_nothing():
    """Ni sélection, ni renommage, ni début de glisser."""
    canvas = FakeCanvas("Aaaa/")
    canvas.setFocus = lambda: None
    selected = []
    canvas._select_item = selected.append
    point = types.SimpleNamespace(toPoint=lambda: (5, 5))
    left = types.SimpleNamespace(button=lambda: LEFT, position=lambda: point)
    canvas.mousePressEvent(left)
    assert selected == [] and not hasattr(canvas, "_drag_start_pos")


def test_right_click_at_the_root_still_opens_the_menu():
    canvas = FakeCanvas("")
    canvas.mousePressEvent(right_click())
    assert canvas.menus == ["image"]


DirClass = type("DirItem", (), {})
DoubleClick = ast_loader.load_methods(
    "mosaic_canvas.py", "MosaicCanvas", {"mouseDoubleClickEvent", "_in_subfolder"},
    ThumbnailItem=Thumb, DirItem=DirClass, QGraphicsProxyWidget=type("Proxy", (), {}))


class QtBase:
    """Comportement d'origine de Qt, appelé par super()."""

    def mouseDoubleClickEvent(self, event):
        pass


class DoubleClickCanvas(DoubleClick, QtBase):
    def __init__(self, directory):
        self._state = types.SimpleNamespace(current_directory=directory,
                                            images_data=[{"is_image": True}])
        self._scene = types.SimpleNamespace(items=lambda pos: [])
        self.opened = []
        self._open_image_viewer_callback = self.opened.append
        self._open_non_image_callback = self.opened.append

    def mapToScene(self, point):
        return point

    def _item_at(self, scene_pos):
        return Thumb()


def test_double_click_on_an_image_in_a_subfolder_does_nothing():
    canvas = DoubleClickCanvas("Aaaa/")
    canvas.mouseDoubleClickEvent(right_click())
    assert canvas.opened == []


def test_double_click_on_a_folder_in_a_subfolder_still_enters_it():
    class Folder(DirClass):
        entry = {"orig_name": "Aaaa/Cccc/"}

    canvas = DoubleClickCanvas("Aaaa/")
    canvas._item_at = lambda scene_pos: Folder()
    entered = []
    canvas.navigate_to_directory = entered.append
    canvas.mouseDoubleClickEvent(right_click())
    assert entered == ["Aaaa/Cccc/"] and canvas.opened == []


Toolbar = ast_loader.load_methods(
    "icon_toolbar_qt.py", "IconToolbarQt", {"_is_active"},
    _BUSY_BLOCKED_ICONS=frozenset(), _ACTIVATION_RULES={})


class FakeToolbar(Toolbar):
    def __init__(self, in_subfolder):
        self._state_getters = {"in_subfolder": lambda: in_subfolder, "busy": lambda: False}


def test_in_a_subfolder_only_flatten_stays_active_in_the_icon_column():
    toolbar = FakeToolbar(True)
    assert toolbar._is_active("flatten_directories")
    assert not any(toolbar._is_active(i) for i in ("delete_selected", "rotate_left", "open_file",
                                                   "undo", "split_ui", "create_ico"))
    assert FakeToolbar(False)._is_active("delete_selected")


def test_double_click_on_an_image_at_the_root_opens_the_viewer():
    canvas = DoubleClickCanvas("")
    canvas.mouseDoubleClickEvent(right_click())
    assert canvas.opened == [0]
