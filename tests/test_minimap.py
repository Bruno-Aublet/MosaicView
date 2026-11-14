"""Minimap (modules/qt/minimap_widget_qt.py, MinimapWidget) : géométrie et
glisser, sans Qt.

- géométrie : taille fixe des mini-vignettes d'après les colonnes réelles de
  la mosaïque, conversions scène ↔ minimap avec le défilement interne,
  bornes du défilement, recentrage sur le rectangle vert seulement quand la
  disposition change, vitesse du défilement automatique au bord ;
- glisser du rectangle vert ou de la minimap : la mosaïque suit, puis tout
  s'arrête au relâchement — y compris quand le relâchement n'arrive jamais à
  la minimap (bouton relâché sur un menu ou une autre fenêtre, minimap
  masquée, fenêtre désactivée) : sinon la mosaïque continuait de suivre la
  souris, ou défilait toute seule ;
- défilement de la mosaïque : la minimap ne défile que pour garder le
  rectangle vert entier ;
- dessin (paintEvent) : chaque mini-vignette à la taille de la vignette
  réduite, décalée de la marge de sa case, sans jamais toucher ses voisines ;
  rectangle vert ; pages « Supprimée » grisées seulement si la visionneuse
  les saute.

Méthodes extraites par tests/ast_loader.py ; constantes reprises du code.
Les types Qt (point, rectangle, painter) sont remplacés par de petites classes.
"""
import types

import pytest

import ast_loader


class P:
    """QPointF réduit à ce qu'emploie la minimap."""

    def __init__(self, x=0.0, y=0.0):
        if isinstance(x, P):
            x, y = x.x(), x.y()
        self._x, self._y = float(x), float(y)

    def x(self):
        return self._x

    def y(self):
        return self._y

    def __sub__(self, other):
        return P(self._x - other.x(), self._y - other.y())

    def manhattanLength(self):
        return abs(self._x) + abs(self._y)


class R:
    """QRectF réduit à ce qu'emploie la minimap."""

    def __init__(self, x, y, w, h):
        self._x, self._y, self._w, self._h = float(x), float(y), float(w), float(h)

    def x(self):
        return self._x

    def y(self):
        return self._y

    def width(self):
        return self._w

    def height(self):
        return self._h

    def top(self):
        return self._y

    def bottom(self):
        return self._y + self._h

    def center(self):
        return P(self._x + self._w / 2, self._y + self._h / 2)

    def contains(self, p):
        return self._x <= p.x() <= self._x + self._w and self._y <= p.y() <= self._y + self._h

    def boundingRect(self):
        return self


LEFT, NO_BUTTON = 1, 0


class FakeApplication:
    buttons = LEFT

    @classmethod
    def mouseButtons(cls):
        return cls.buttons

    @staticmethod
    def startDragDistance():
        return 4


Qt = types.SimpleNamespace(LeftButton=LEFT, RightButton=2, OpenHandCursor="main",
                           ClosedHandCursor="main fermée", ArrowCursor="flèche")
QEvent = types.SimpleNamespace(ActivationChange="activation")

CONSTANT_NAMES = ("_MINI_PAD", "_EDGE_ZONE", "_AUTOSCROLL_MAX", "_WHEEL_STEP")
CONSTANTS = ast_loader.load("minimap_widget_qt.py", set(CONSTANT_NAMES))
LAYOUT = ast_loader.load("mosaic_canvas.py", {"PAD_X", "PAD_Y", "LABEL_H"})
MINI_PAD = CONSTANTS["_MINI_PAD"]
EDGE_ZONE = CONSTANTS["_EDGE_ZONE"]
AUTOSCROLL_MAX = CONSTANTS["_AUTOSCROLL_MAX"]

METHODS = {
    "_real_tw", "_real_th", "_real_cw", "_real_ch", "_real_cols", "_mini_cell_size", "_scale",
    "_content_height", "_clamp_scroll", "_center_scroll_on_viewport", "_scene_to_minimap_rect",
    "_minimap_to_scene_point", "_viewport_rect_minimap", "_current_layout_signature",
    "_on_canvas_changed", "_sync_after_relayout", "_on_canvas_scrolled",
    "_keep_viewport_visible", "_edge_speed", "_end_drag", "_autoscroll_tick",
    "_drag_canvas_to", "_scroll_canvas_to_scene_center", "_update_hover_cursor",
    "_notify_panel_focus", "mousePressEvent", "mouseMoveEvent", "mouseReleaseEvent",
    "hideEvent", "changeEvent", "wheelEvent",
}
Methods = ast_loader.load_methods(
    "minimap_widget_qt.py", "MinimapWidget", METHODS,
    QPointF=P, QRectF=R, Qt=Qt, QEvent=QEvent, QApplication=FakeApplication,
    PAD_X=LAYOUT["PAD_X"], PAD_Y=LAYOUT["PAD_Y"], LABEL_H=LAYOUT["LABEL_H"],
    **{name: CONSTANTS[name] for name in CONSTANT_NAMES})


class Timer:
    def __init__(self):
        self.active = False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def isActive(self):
        return self.active


class Canvas:
    """MosaicCanvas : vignettes 150×200, zone visible `visible` (scène)."""

    def __init__(self, items=10, viewport_w=680, viewport_h=500):
        self._state = types.SimpleNamespace(thumb_w=150, thumb_h=200, doc_generation=1)
        self._items = [object()] * items
        self.viewport_w, self.viewport_h = viewport_w, viewport_h
        self.visible = R(0, 0, viewport_w, viewport_h)
        self.centered = []

    def viewport(self):
        return types.SimpleNamespace(width=lambda: self.viewport_w, height=lambda: self.viewport_h,
                                     rect=lambda: None)

    def mapToScene(self, rect):
        return self.visible

    def centerOn(self, point):
        self.centered.append((point.x(), point.y()))
        self.visible = R(point.x() - self.viewport_w / 2, point.y() - self.viewport_h / 2,
                         self.viewport_w, self.viewport_h)


class Base:
    """Méthodes de QWidget appelées par super()."""

    def hideEvent(self, event):
        pass

    def changeEvent(self, event):
        pass


class Minimap(Methods, Base):
    def __init__(self, canvas, width=120, height=100):
        self._canvas = canvas
        self._owner_panel = None
        self._w, self._h = width, height
        self._dragging_viewport = False
        self._drag_offset = P(0, 0)
        self._last_drag_pos = P(0, 0)
        self._scroll_y = 0.0
        self._press_pos = None
        self._press_scroll_y = 0.0
        self._panning = False
        self._layout_signature = None
        self._recenter_pending = False
        self._autoscroll_timer = Timer()
        self._relayout_timer = Timer()
        self.cursor = None
        self.active_window = True

    def width(self):
        return self._w

    def height(self):
        return self._h

    def update(self):
        pass

    def setCursor(self, cursor):
        self.cursor = cursor

    def isActiveWindow(self):
        return self.active_window


class Event:
    def __init__(self, x=0, y=0, button=LEFT, buttons=LEFT, delta=0, kind=None):
        self._pos, self._button, self._buttons, self._delta, self._kind = P(x, y), button, buttons, delta, kind

    def position(self):
        return self._pos

    def button(self):
        return self._button

    def buttons(self):
        return self._buttons

    def angleDelta(self):
        return types.SimpleNamespace(y=lambda: self._delta)

    def accept(self):
        pass

    def type(self):
        return self._kind


@pytest.fixture(autouse=True)
def button_held():
    FakeApplication.buttons = LEFT


# --- Géométrie ---------------------------------------------------------------------

def test_cell_size_follows_the_real_columns_of_the_mosaic():
    minimap = Minimap(Canvas(viewport_w=680))
    cell_w = 150 + LAYOUT["PAD_X"] * 2
    cols = 680 // cell_w
    mini_w, mini_h, real_cols = minimap._mini_cell_size()
    assert real_cols == cols
    assert mini_w == (120 - MINI_PAD * 2) // cols and mini_h == int(mini_w * 200 / 150)
    narrow = Minimap(Canvas(viewport_w=cell_w * 2))
    assert narrow._mini_cell_size()[2] == 2 and narrow._mini_cell_size()[0] > mini_w


def test_cells_are_never_compressed_to_fit_a_long_comic():
    short, long = Minimap(Canvas(items=8)), Minimap(Canvas(items=1500))
    assert short._mini_cell_size() == long._mini_cell_size()
    assert long._content_height() > long.height()


def test_scene_and_minimap_points_convert_both_ways_with_the_scroll():
    minimap = Minimap(Canvas(items=200))
    minimap._scroll_y = 37.0
    rect = minimap._scene_to_minimap_rect(R(85, 500, 10, 10))
    back = minimap._minimap_to_scene_point(P(rect.x(), rect.y()))
    assert back.x() == pytest.approx(85) and back.y() == pytest.approx(500)


def test_scroll_stays_within_the_content():
    minimap = Minimap(Canvas(items=200))
    minimap._scroll_y = -50
    minimap._clamp_scroll()
    assert minimap._scroll_y == 0
    minimap._scroll_y = 1e9
    minimap._clamp_scroll()
    assert minimap._scroll_y == pytest.approx(
        minimap._content_height() - (minimap.height() - MINI_PAD * 2))
    short = Minimap(Canvas(items=2))
    short._scroll_y = 30
    short._clamp_scroll()
    assert short._scroll_y == 0


def test_recentred_on_the_green_rectangle():
    canvas = Canvas(items=400)
    canvas.visible = R(0, 5000, 680, 500)
    minimap = Minimap(canvas)
    minimap._center_scroll_on_viewport()
    green = minimap._viewport_rect_minimap()
    middle = green.y() + green.height() / 2
    assert middle == pytest.approx(MINI_PAD + (minimap.height() - MINI_PAD * 2) / 2)


def test_recentred_only_when_the_layout_changes():
    canvas = Canvas(items=400)
    canvas.visible = R(0, 5000, 680, 500)
    minimap = Minimap(canvas)
    minimap._on_canvas_changed()                       # première disposition : recentrée
    minimap._scroll_y = 0.0                            # l'utilisateur fait défiler la minimap
    minimap._on_canvas_changed()                       # sélection, ajout… : position gardée
    assert minimap._scroll_y == 0.0
    canvas._state.doc_generation += 1                  # autre document : recentrée
    minimap._on_canvas_changed()
    assert minimap._scroll_y > 0


def _green_inside(minimap):
    green = minimap._viewport_rect_minimap()
    return (green.y() >= MINI_PAD - 1e-6
            and green.y() + green.height() <= minimap.height() - MINI_PAD + 1e-6)


def test_mosaic_scroll_inside_the_minimap_leaves_it_still():
    canvas = Canvas(items=400)
    minimap = Minimap(canvas)
    canvas.visible = R(0, 50, 680, 500)
    minimap._on_canvas_scrolled()
    assert minimap._scroll_y == 0.0 and _green_inside(minimap)


def test_green_rectangle_leaving_the_minimap_brings_it_along():
    """Juste assez pour le garder entier, vers le bas comme vers le haut."""
    canvas = Canvas(items=400)
    minimap = Minimap(canvas)
    canvas.visible = R(0, 5000, 680, 500)
    minimap._on_canvas_scrolled()
    assert _green_inside(minimap)
    assert minimap._viewport_rect_minimap().y() + minimap._viewport_rect_minimap().height() \
        == pytest.approx(minimap.height() - MINI_PAD)
    canvas.visible = R(0, 1000, 680, 500)
    minimap._on_canvas_scrolled()
    assert minimap._viewport_rect_minimap().y() == pytest.approx(MINI_PAD)


def test_bottom_of_the_mosaic_shows_the_last_row_of_the_minimap():
    canvas = Canvas(items=400)
    minimap = Minimap(canvas)
    rows = 400 // minimap._real_cols()
    end = rows * minimap._real_ch()
    canvas.visible = R(0, end - 500, 680, 500)
    minimap._on_canvas_scrolled()
    assert minimap._scroll_y == pytest.approx(
        minimap._content_height() - (minimap.height() - MINI_PAD * 2))


def test_dragging_the_rectangle_keeps_its_own_minimap_scroll():
    canvas = Canvas(items=400)
    minimap = Minimap(canvas)
    minimap._dragging_viewport = True
    canvas.visible = R(0, 5000, 680, 500)
    minimap._on_canvas_scrolled()
    assert minimap._scroll_y == 0.0


# --- Dessin (paintEvent) -----------------------------------------------------------

class Painter:
    """QPainter : garde la trace de ce qui est dessiné."""
    SmoothPixmapTransform = "smooth"
    last = None

    def __init__(self, widget):
        self.pixmaps, self.rects, self.opacity = [], [], []
        Painter.last = self

    def drawPixmap(self, x, y, pixmap):
        self.pixmaps.append((x, y, pixmap))

    def drawRect(self, rect):
        self.rects.append(rect)

    def setOpacity(self, value):
        self.opacity.append(value)

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class Color:
    def __init__(self, *args):
        self.args = args

    def red(self):
        return 0

    def green(self):
        return 170

    def blue(self):
        return 0


class Deleted:
    skipped = False


PaintMethods = ast_loader.load_methods(
    "minimap_widget_qt.py", "MinimapWidget", METHODS | {"paintEvent"},
    QPointF=P, QRectF=R, Qt=Qt, QEvent=QEvent, QApplication=FakeApplication,
    PAD_X=LAYOUT["PAD_X"], PAD_Y=LAYOUT["PAD_Y"], LABEL_H=LAYOUT["LABEL_H"],
    QPainter=Painter, QColor=Color, QPen=lambda *args: args, _VIEWPORT_COLOR=Color(),
    get_current_theme=lambda: {"toolbar_bg": "#fff", "separator": "#888", "disabled": "#ccc"},
    deleted_pages_skipped=lambda: Deleted.skipped, DELETED_PAGE_OPACITY=0.35,
    _grayed_pixmap=lambda pixmap: ("gris", pixmap),
    **{name: CONSTANTS[name] for name in CONSTANT_NAMES})


class PaintedMinimap(PaintMethods, Minimap):
    """Minimap dont les vignettes sont des (largeur, hauteur) demandées."""
    deleted = ()

    def rect(self):
        return R(0, 0, self._w, self._h)

    def _mini_pixmap_for_item(self, item, mini_w, mini_h):
        return (mini_w, mini_h)

    def _paint_overlays(self, painter, item, x, y, mini_w, mini_h):
        pass

    def _is_deleted_page(self, item):
        return item.visual_idx in self.deleted


def painted(items=8, viewport_w=680, **attrs):
    canvas = Canvas(items=items, viewport_w=viewport_w)
    canvas._items = [types.SimpleNamespace(visual_idx=i) for i in range(items)]
    minimap = PaintedMinimap(canvas, height=2000)       # toutes les lignes dessinées
    for name, value in attrs.items():
        setattr(minimap, name, value)
    minimap.paintEvent(None)
    return minimap, Painter.last


@pytest.fixture
def no_deleted_skip():
    Deleted.skipped = False


def test_mini_thumbnails_sit_in_their_cell_with_its_margin(no_deleted_skip):
    """Taille de la vignette réduite, décalée de la marge de sa case, comme
    dans la mosaïque."""
    minimap, painter = painted()
    scale = minimap._scale()
    cols = minimap._real_cols()
    size = (int(150 * scale), int(200 * scale))
    for idx, (x, y, pixmap) in enumerate(painter.pixmaps):
        col, row = idx % cols, idx // cols
        assert pixmap == size
        assert x == int(MINI_PAD + col * (minimap._real_cw() * scale) + LAYOUT["PAD_X"] * scale)
        assert y == int(MINI_PAD + row * (minimap._real_ch() * scale) + LAYOUT["PAD_Y"] * scale)


def test_neighbouring_mini_thumbnails_never_touch(no_deleted_skip):
    """Pages aux proportions exactes des vignettes : sans la marge, deux
    colonnes se touchaient et formaient un seul bloc."""
    for viewport_w in (340, 510, 680, 1200):
        minimap, painter = painted(items=12, viewport_w=viewport_w)
        cols = minimap._real_cols()
        drawn = painter.pixmaps
        for idx in range(len(drawn) - 1):
            x, y, (w, h) = drawn[idx]
            if (idx + 1) % cols:
                assert drawn[idx + 1][0] > x + w, viewport_w
            if idx + cols < len(drawn):
                assert drawn[idx + cols][1] > y + h, viewport_w


def test_green_rectangle_is_the_visible_part_of_the_mosaic(no_deleted_skip):
    minimap, painter = painted(_scroll_y=12.0)
    [green] = painter.rects
    expected = minimap._viewport_rect_minimap()
    assert (green.x(), green.y(), green.width(), green.height()) == \
        (expected.x(), expected.y(), expected.width(), expected.height())


def test_deleted_pages_are_grayed_only_when_the_viewer_skips_them():
    Deleted.skipped = False
    _minimap, painter = painted(deleted=(2,))
    assert not any(isinstance(p, tuple) and p[0] == "gris" for _x, _y, p in painter.pixmaps)
    Deleted.skipped = True
    try:
        _minimap, painter = painted(deleted=(2,))
        assert painter.pixmaps[2][2][0] == "gris"
        assert painter.opacity == [0.35, 1.0]
    finally:
        Deleted.skipped = False


def test_edge_speed():
    minimap = Minimap(Canvas())
    assert minimap._edge_speed(P(10, 50)) == 0
    assert minimap._edge_speed(P(10, 0)) < 0
    assert minimap._edge_speed(P(10, minimap.height())) > 0
    assert minimap._edge_speed(P(10, -10_000)) == -AUTOSCROLL_MAX
    assert minimap._edge_speed(P(10, 10_000)) == AUTOSCROLL_MAX
    assert abs(minimap._edge_speed(P(10, EDGE_ZONE - 1))) < abs(minimap._edge_speed(P(10, 0)))


# --- Glisser -----------------------------------------------------------------------

def grab_green_rectangle(minimap):
    green = minimap._viewport_rect_minimap()
    centre = green.center()
    minimap.mousePressEvent(Event(centre.x(), centre.y()))
    assert minimap._dragging_viewport
    return centre


def test_dragging_the_green_rectangle_moves_the_mosaic_until_release():
    canvas = Canvas(items=400)
    minimap = Minimap(canvas)
    centre = grab_green_rectangle(minimap)
    minimap.mouseMoveEvent(Event(centre.x(), centre.y() + 20))
    assert len(canvas.centered) == 1 and canvas.centered[0][1] > canvas.viewport_h / 2
    minimap.mouseReleaseEvent(Event(centre.x(), centre.y() + 20))
    assert not minimap._dragging_viewport and not minimap._autoscroll_timer.isActive()


def test_release_missed_ends_the_drag_at_the_next_move():
    """Bouton relâché sur un menu ou une autre fenêtre : le mouvement suivant
    arrive sans bouton, et la mosaïque ne suit plus la souris."""
    canvas = Canvas(items=400)
    minimap = Minimap(canvas)
    centre = grab_green_rectangle(minimap)
    minimap.mouseMoveEvent(Event(centre.x(), minimap.height() - 1))   # au bord
    assert minimap._autoscroll_timer.isActive()
    moved = len(canvas.centered)
    minimap.mouseMoveEvent(Event(centre.x(), 50, buttons=NO_BUTTON))
    assert not minimap._dragging_viewport and not minimap._autoscroll_timer.isActive()
    assert len(canvas.centered) == moved


def test_autoscroll_stops_when_the_button_was_released_elsewhere():
    canvas = Canvas(items=400)
    minimap = Minimap(canvas)
    centre = grab_green_rectangle(minimap)
    minimap.mouseMoveEvent(Event(centre.x(), minimap.height() + 30))
    minimap._autoscroll_tick()
    moved, scroll = len(canvas.centered), minimap._scroll_y
    FakeApplication.buttons = NO_BUTTON
    minimap._autoscroll_tick()
    assert not minimap._autoscroll_timer.isActive() and not minimap._dragging_viewport
    assert len(canvas.centered) == moved and minimap._scroll_y == scroll


def test_hiding_the_minimap_ends_the_drag():
    minimap = Minimap(Canvas(items=400))
    centre = grab_green_rectangle(minimap)
    minimap.mouseMoveEvent(Event(centre.x(), minimap.height() + 30))
    minimap.hideEvent(Event())
    assert not minimap._dragging_viewport and not minimap._autoscroll_timer.isActive()


def test_window_deactivated_ends_the_drag():
    minimap = Minimap(Canvas(items=400))
    grab_green_rectangle(minimap)
    minimap.active_window = False
    minimap.changeEvent(Event(kind=QEvent.ActivationChange))
    assert not minimap._dragging_viewport


def test_other_change_keeps_the_drag():
    minimap = Minimap(Canvas(items=400))
    grab_green_rectangle(minimap)
    minimap.changeEvent(Event(kind="autre"))
    assert minimap._dragging_viewport


def test_click_outside_the_rectangle_jumps_on_release():
    canvas = Canvas(items=400)
    minimap = Minimap(canvas)
    minimap.mousePressEvent(Event(60, 90))
    minimap.mouseReleaseEvent(Event(60, 90))
    assert len(canvas.centered) == 1 and minimap._press_pos is None


def test_panning_the_minimap_does_not_move_the_mosaic():
    canvas = Canvas(items=400)
    minimap = Minimap(canvas)
    minimap._scroll_y = 60.0
    minimap.mousePressEvent(Event(60, 90))
    minimap.mouseMoveEvent(Event(60, 60))
    assert minimap._panning and minimap._scroll_y == pytest.approx(90.0)
    minimap.mouseReleaseEvent(Event(60, 60))
    assert canvas.centered == [] and not minimap._panning


def test_panning_ends_when_the_release_was_missed():
    canvas = Canvas(items=400)
    minimap = Minimap(canvas)
    minimap.mousePressEvent(Event(60, 90))
    minimap.mouseMoveEvent(Event(60, 60))
    minimap.mouseMoveEvent(Event(60, 20, buttons=NO_BUTTON))
    assert minimap._press_pos is None and not minimap._panning and canvas.centered == []


def test_wheel_scrolls_the_minimap_only():
    canvas = Canvas(items=400)
    minimap = Minimap(canvas)
    minimap.wheelEvent(Event(delta=-120))
    assert minimap._scroll_y == CONSTANTS["_WHEEL_STEP"] and canvas.centered == []
