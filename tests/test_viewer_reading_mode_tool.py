"""Mode de lecture de la visionneuse pendant qu'un outil est sélectionné.

Un outil travaille sur une seule page : tant qu'il est sélectionné, la touche D
ne change pas de mode et l'entrée du menu contextuel est grisée. Sans outil, le
cycle des modes reste inchangé.

image_viewer_qt.py importe PySide6 : les méthodes sont extraites par AST
(tests/ast_loader.load_methods), l'interface est remplacée.
"""
import ast
import types

import pytest

import ast_loader

Base = ast_loader.load_methods("image_viewer_qt.py", "ImageViewer",
                               {"toggle_double_page", "_can_change_reading_mode"})


class Viewer(Base):
    def __init__(self, tool):
        self._toolbar = types.SimpleNamespace(active_tool=tool)
        self._macro_recording = False
        self._macro_reading = False
        self._manual_zoom = True
        self.page_mode = "single"
        self.zoom_level = 1.0
        self.redrawn = 0
        self._zoom_label = types.SimpleNamespace(
            setText=lambda text: None, adjustSize=lambda: None,
            move=lambda x, y: None, width=lambda: 10)

    def _paint_stroke_blocks_command(self):
        return False

    def set_page_mode(self, mode):
        self.page_mode = mode

    def _update_mode_label(self):
        pass

    def display_image(self, keep_crop_rect=False):
        self.redrawn += 1

    def width(self):
        return 100

    def _overlay_top(self):
        return 0


@pytest.mark.parametrize("tool", ["crop", "shapes", "text", "paste_image", "blur"])
def test_key_d_does_nothing_while_a_tool_is_selected(tool):
    viewer = Viewer(tool)
    viewer.toggle_double_page()
    assert viewer.page_mode == "single" and viewer.redrawn == 0


def test_modes_cycle_without_a_tool():
    viewer = Viewer(None)
    seen = []
    for _i in range(4):
        viewer.toggle_double_page()
        seen.append(viewer.page_mode)
    assert seen == ["double", "continuous", "webtoon", "single"]


def test_context_menu_entry_follows_the_same_rule():
    tree = ast_loader.module_tree(ast_loader.QT_DIR / "image_viewer_qt.py")
    viewer_class = next(n for n in tree.body
                        if isinstance(n, ast.ClassDef) and n.name == "ImageViewer")
    menu = next(n for n in viewer_class.body
                if isinstance(n, ast.FunctionDef) and n.name == "_show_context_menu")
    enabled = [ast.unparse(n.args[0]) for n in ast.walk(menu)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "setEnabled"]
    assert "self._can_change_reading_mode()" in enabled
