"""Visionneuse : zoom d'avant le webtoon rétabli à sa sortie
(image_viewer_qt.ImageViewer.set_page_mode).

Le webtoon a son propre ajustement (à la largeur). En y entrant, la
visionneuse retient le zoom du moment, s'il avait été choisi par
l'utilisateur, et le défilement vertical. En sortant par la touche D ou le
menu, un zoom choisi revient à l'identique et un ajustement automatique est
recalculé pour le nouveau mode. Un outil ou une macro qui impose la simple
page (keep_zoom) garde au contraire le zoom et le défilement du webtoon : le
tampon de flou et le clonage le font en plein geste, et convertissent la
position de la souris d'après zoom_level.

Vérifié par le comportement de set_page_mode, extraite par AST
(tests/ast_loader.load_methods : image_viewer_qt.py importe PySide6), et par
un scan AST : le mode n'est changé que par set_page_mode, et les outils et
macros le font avec keep_zoom=True.
"""
import ast
import types
from pathlib import Path

import ast_loader

QT_DIR = Path(__file__).resolve().parent.parent / "modules" / "qt"

Base = ast_loader.load_methods("image_viewer_qt.py", "ImageViewer", {"set_page_mode"})


class Viewer(Base):
    def __init__(self, mode="single", zoom=0.5, manual=False, pan_y=0):
        self.page_mode, self.zoom_level, self._manual_zoom = mode, zoom, manual
        self._zoom_initialized = True
        self._zoom_before_webtoon = None
        self._canvas = types.SimpleNamespace(pan_offset_y=pan_y, display_offset_y=0,
                                             display_height=0, height=lambda: 583)

    def show(self):
        """Ce que fait display_image pour choisir le zoom."""
        if not self._zoom_initialized:
            self.zoom_level = 1.6 if self.page_mode == "webtoon" else 0.4
            self._zoom_initialized = True


def test_automatic_fit_is_recomputed_after_webtoon():
    v = Viewer("continuous", zoom=0.5, manual=False)
    v.set_page_mode("webtoon")
    v._zoom_initialized = False
    v.show()
    assert v.zoom_level == 1.6
    v.set_page_mode("single")
    assert v._zoom_initialized is False and v._manual_zoom is False
    v.show()
    assert v.zoom_level == 0.4


def test_chosen_zoom_comes_back():
    v = Viewer("continuous", zoom=0.75, manual=True)
    v.set_page_mode("webtoon")
    v.zoom_level = 2.0                     # zoom changé pendant le webtoon
    v.set_page_mode("single")
    v.show()
    assert (v.zoom_level, v._manual_zoom) == (0.75, True)


def test_vertical_scroll_is_restored():
    v = Viewer("single", pan_y=-40)
    v.set_page_mode("webtoon")
    assert v._canvas.pan_offset_y == 0
    v._canvas.pan_offset_y = -900          # défilement dans le webtoon
    v.set_page_mode("single")
    assert v._canvas.pan_offset_y == -40


def test_a_tool_keeps_the_webtoon_zoom_and_the_page_in_place():
    v = Viewer("continuous", zoom=0.75, manual=True, pan_y=-40)
    v.set_page_mode("webtoon")
    # page défilée dans le webtoon : placée en haut de la zone, décalée du défilement
    c = v._canvas
    v.zoom_level, c.pan_offset_y = 2.0, -900
    c.display_offset_y, c.display_height = -900, 1161
    v.set_page_mode("single", keep_zoom=True)
    v.show()
    assert v.zoom_level == 2.0
    # placement de la simple page (centrée, puis décalée) : même place à l'écran
    assert (c.height() - c.display_height) // 2 + c.pan_offset_y == -900
    assert v._zoom_before_webtoon is None  # oublié : plus rien à rétablir
    v.set_page_mode("double")
    assert v.zoom_level == 2.0


def test_modes_other_than_webtoon_keep_the_zoom():
    v = Viewer("single", zoom=0.9, manual=True)
    v.set_page_mode("double")
    v.set_page_mode("continuous")
    assert (v.page_mode, v.zoom_level, v._zoom_before_webtoon) == ("continuous", 0.9, None)


def _assignments_and_calls():
    for path in sorted(QT_DIR.glob("*.py")):
        for node in ast.walk(ast_loader.module_tree(path)):
            if isinstance(node, ast.FunctionDef):
                yield path.name, node


def test_page_mode_is_only_changed_by_set_page_mode():
    wrong = []
    for fname, func in _assignments_and_calls():
        if func.name in ("set_page_mode", "__init__"):
            continue
        for sub in ast.walk(func):
            if isinstance(sub, ast.Assign) and any(
                    isinstance(t, ast.Attribute) and t.attr == "page_mode" for t in sub.targets):
                wrong.append(f"{fname}:{sub.lineno}")
    assert not wrong, ("changer le mode par set_page_mode (zoom d'avant le "
                       "webtoon rétabli à sa sortie) :\n" + "\n".join(wrong))


def test_tools_and_macros_keep_the_zoom():
    """Seul le changement de mode demandé par l'utilisateur (touche D, menu :
    toggle_double_page) rétablit le zoom d'avant le webtoon."""
    calls, wrong = 0, []
    for fname, func in _assignments_and_calls():
        for sub in ast.walk(func):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "set_page_mode"):
                calls += 1
                keep = any(k.arg == "keep_zoom" and getattr(k.value, "value", None) is True
                           for k in sub.keywords)
                if func.name != "toggle_double_page" and not keep:
                    wrong.append(f"{fname}:{sub.lineno} ({func.name})")
    assert calls >= 11
    assert not wrong, wrong
