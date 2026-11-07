"""Outil Formes : l'aperçu à l'écran correspond au résultat validé.

L'épaisseur et la tête de flèche sont en pixels de la page et suivent donc le
zoom ; le contour est dessiné vers l'intérieur, comme le rendu final PIL.

shapes_tool_qt.py importe PySide6 : le code est extrait par AST
(tests/ast_loader), l'interface est remplacée. Le rendu réel à l'écran se vérifie
par les tests manuels.
"""
import ast
import math
import types

import pytest
from PIL import Image, ImageDraw

import ast_loader

NS = ast_loader.load(
    "shapes_tool_qt.py",
    {"_arrow_head_len", "_shape_screen_outline", "_ARROW_HEAD_LEN_IMG", "_ARROW_HEAD_MAX_ADD"},
)
head_len, screen_outline = NS["_arrow_head_len"], NS["_shape_screen_outline"]


@pytest.mark.parametrize("zoom", [0.33, 1.0, 2.0])
def test_screen_thickness_follows_the_zoom(zoom):
    assert screen_outline(0, 0, 500, 500, 20, zoom)[0] == pytest.approx(20 * zoom)


def test_thin_stroke_stays_visible_at_a_small_zoom():
    assert screen_outline(0, 0, 500, 500, 1, 0.1)[0] == 1.0


def test_outline_is_drawn_inside_the_shape():
    pen, x1, y1, x2, y2 = screen_outline(100, 200, 300, 400, 20, 2.0)
    assert (x1, y1, x2, y2) == (120.0, 220.0, 280.0, 380.0)
    # Bord extérieur du trait, centré sur ce rectangle : le bord de la forme.
    assert x1 - pen / 2 == 100 and y2 + pen / 2 == 400


def test_outline_never_passes_the_center_of_a_narrow_shape():
    _pen, x1, _y1, x2, _y2 = screen_outline(100, 0, 110, 500, 40, 1.0)
    assert x1 == x2 == 105


@pytest.mark.parametrize("zoom", [0.33, 2.0])
def test_screen_arrow_head_is_the_final_one_at_the_zoom(zoom):
    assert head_len(thickness=10, zoom=zoom) == pytest.approx(head_len(thickness=10, zoom=1.0) * zoom)


def test_final_arrow_head_keeps_its_size():
    assert head_len(thickness=10, zoom=1.0) == 22 + 10 * 2
    assert head_len(thickness=40, zoom=1.0) == 22 + 24 * 2


class FakePen:
    def __init__(self, color, width):
        self.width = width
        self.cap = None
        self.join = None

    def setCapStyle(self, cap):
        self.cap = cap

    def setJoinStyle(self, join):
        self.join = join


class FakePainter:
    def __init__(self):
        self.pen = None
        self.calls = []

    def setPen(self, pen):
        self.pen = pen

    def setBrush(self, brush):
        pass

    def drawRect(self, rect):
        self.calls.append(("rect", rect))

    def drawLine(self, *points):
        self.calls.append(("line", points))


FakeQt = types.SimpleNamespace(
    PenCapStyle=types.SimpleNamespace(FlatCap="coupé"),
    PenJoinStyle=types.SimpleNamespace(MiterJoin="vif"),
    BrushStyle=types.SimpleNamespace(NoBrush="vide"),
    PenStyle=types.SimpleNamespace(NoPen="sans trait"),
)
CanvasBase = ast_loader.load_methods(
    "shapes_tool_qt.py", "ShapeCanvasMixin", {"_paint_one_shape"},
    QPen=FakePen, Qt=FakeQt, QPointF=lambda x, y: (x, y), QRectF=lambda a, b: (a, b),
    QPoint=lambda x, y: (x, y), math=math, _Shape=object,
    _shape_screen_outline=screen_outline, _arrow_head_len=head_len, _ROUNDED_RADIUS_IMG=18,
    _paint_out_of_page_bounds_overlay=lambda painter, canvas, corners: None,
)


class Canvas(CanvasBase):
    def __init__(self, zoom):
        self._viewer = types.SimpleNamespace(zoom_level=zoom)

    def _shape_widget_rect(self, shape):
        return shape.rect


def shape(kind, rect):
    return types.SimpleNamespace(shape_type=kind, rect=rect, color="rouge", thickness=20,
                                 fill_enabled=False, angle=0.0, is_line_like=lambda: kind == "line")


def test_rectangle_is_painted_at_the_page_scale():
    painter = FakePainter()
    Canvas(0.5)._paint_one_shape(painter, shape("rectangle", (100, 100, 300, 300)))
    assert painter.pen.width == 10
    assert painter.pen.join == "vif"
    assert painter.calls == [("rect", ((105.0, 105.0), (295.0, 295.0)))]


def test_filled_rectangle_is_painted_without_a_stroke_over_its_color():
    painter = FakePainter()
    filled = shape("rectangle", (100, 100, 300, 300))
    filled.fill_enabled = True
    Canvas(0.5)._paint_one_shape(painter, filled)
    assert painter.pen == "sans trait"
    assert painter.calls == [("rect", ((100, 100), (300, 300)))]


class FinalShape:
    def __init__(self, shape_type, ix1, iy1, ix2, iy2, color, fill_enabled, thickness, angle=0.0):
        self.shape_type = shape_type
        self.ix1, self.iy1, self.ix2, self.iy2 = ix1, iy1, ix2, iy2
        self.color, self.fill_enabled, self.thickness = color, fill_enabled, thickness
        self.angle = angle

    def normalized_img_rect(self):
        return (min(self.ix1, self.ix2), min(self.iy1, self.iy2),
                max(self.ix1, self.ix2), max(self.iy1, self.iy2))

    def center_img(self):
        x1, y1, x2, y2 = self.normalized_img_rect()
        return (x1 + x2) / 2, (y1 + y2) / 2


SEMI_RED = types.SimpleNamespace(red=lambda: 255, green=lambda: 0, blue=lambda: 0, alpha=lambda: 128)
RenderBase = ast_loader.load_methods(
    "shapes_tool_qt.py", "ShapeViewerMixin",
    {"_shapes_render_all", "_draw_one_shape", "_draw_rotated_shape"},
    Image=Image, ImageDraw=ImageDraw, math=math, _Shape=FinalShape,
    _arrow_head_len=head_len, _ROUNDED_RADIUS_IMG=18, _ARROW_HEAD_ANGLE=math.radians(28),
)


class Renderer(RenderBase):
    def __init__(self, shapes):
        self._canvas = types.SimpleNamespace(_shapes=shapes)


@pytest.mark.parametrize("angle", [0.0, 30.0])
def test_semi_transparent_shape_blends_with_the_page(angle):
    page = Image.new("RGBA", (60, 60), (0, 0, 255, 255))
    filled = FinalShape("rectangle", 10, 10, 50, 50, SEMI_RED, True, 2, angle)
    result = Renderer([filled])._shapes_render_all(page)
    assert result.getpixel((30, 30)) == (128, 0, 127, 255)
    assert result.getpixel((1, 1)) == (0, 0, 255, 255)


def test_macro_keeps_the_transparency_of_a_shape_color():
    tree = ast_loader.module_tree(ast_loader.QT_DIR / "shapes_tool_qt.py")
    calls = [ast.unparse(n) for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "name" and ast.unparse(n.func).startswith("s.color")]
    assert calls
    assert all("HexArgb" in call for call in calls)


def test_line_ends_are_cut_like_the_final_rendering():
    painter = FakePainter()
    Canvas(2.0)._paint_one_shape(painter, shape("line", (0, 0, 100, 0)))
    assert painter.pen.width == 40 and painter.pen.cap == "coupé"
    assert painter.calls == [("line", (0, 0, 100, 0))]
