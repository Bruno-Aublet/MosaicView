"""Outil Texte : le texte suit le zoom de la page, sans arrondi.

Tailles de police et largeur de retour à la ligne du document sont exprimées au
zoom display_scale. Vérifié :
- un changement de zoom remet les tailles à l'échelle, une seule fois, et le
  zoom devient le display_scale du bloc ;
- la largeur disponible reste proportionnelle au zoom, donc constante en pixels
  de la page ;
- changer la taille d'un morceau de texte ne touche pas au display_scale du bloc ;
- une taille d'écran n'est jamais arrondie.

text_tool_qt.py importe PySide6 : les méthodes sont extraites par AST
(tests/ast_loader.load_methods), l'interface est remplacée. scale_fonts, qui
travaille sur un vrai document Qt, se vérifie par les tests manuels.
"""
import types

import pytest

import ast_loader


class Point:
    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y


class FakeFormat:
    def __init__(self):
        self.size = None

    def setFontPointSize(self, size):
        self.size = size

    def setFontFamily(self, family):
        self.family = family

    def setForeground(self, color):
        self.color = color


class FakeFont:
    HintingPreference = types.SimpleNamespace(PreferNoHinting="sans ajustement")

    def __init__(self, family):
        self.family = family
        self.size = None
        self.hinting = None

    def setPointSizeF(self, size):
        self.size = size

    def setHintingPreference(self, hinting):
        self.hinting = hinting


class Ratios:
    """Mesure de _layout_ratio remplacée : Qt arrondit la taille des lettres,
    donc le rapport réel s'écarte un peu du zoom."""
    drift = 1.03

    def __init__(self):
        self.calls = 0

    def measure(self, doc, scale):
        self.calls += 1
        return scale * self.drift


ratios = Ratios()

CanvasBase = ast_loader.load_methods(
    "text_tool_qt.py", "TextCanvasMixin", {"_text_reposition_block", "_text_image_to_widget"},
    QPoint=Point, _layout_ratio=lambda doc, scale: ratios.measure(doc, scale),
)
PanelBase = ast_loader.load_methods(
    "text_tool_qt.py", "_TextOptionsPanel", {"_on_font_size_changed", "apply_default_format_to_block"},
    QTextCharFormat=FakeFormat, QFont=FakeFont,
)


class Overlay:
    def __init__(self):
        self.factors = []
        self.max_width = None
        self.text_revision = 1

    def document(self):
        return types.SimpleNamespace(revision=lambda: self.text_revision)

    def height(self):
        return 40

    def scale_fonts(self, factor):
        self.factors.append(factor)

    def move(self, target):
        pass

    def set_max_width(self, width):
        self.max_width = width


class Canvas(CanvasBase):
    def __init__(self, zoom):
        self._viewer = types.SimpleNamespace(zoom_level=zoom)
        self.display_offset_x = 0
        self.display_offset_y = 0
        self.display_width = 800 * zoom


def text_block(display_scale):
    return types.SimpleNamespace(overlay=Overlay(), img_pos=Point(200, 100),
                                 display_scale=display_scale, top_y_offset_img=20,
                                 page_wrap_width=None, layout_ratio=display_scale,
                                 layout_ratio_key=None)


def test_zoom_change_rescales_the_text_once():
    block = text_block(0.5)
    canvas = Canvas(2.0)
    canvas._text_reposition_block(block)
    assert block.overlay.factors == [pytest.approx(4.0)]
    assert block.display_scale == 2.0
    canvas._text_reposition_block(block)
    assert len(block.overlay.factors) == 1


def test_same_zoom_leaves_the_text_alone():
    block = text_block(0.68)
    Canvas(0.68)._text_reposition_block(block)
    assert block.overlay.factors == []


@pytest.mark.parametrize("zoom", [0.25, 0.68, 1.0, 3.0])
def test_available_width_is_the_same_in_page_pixels(zoom):
    block = text_block(zoom)
    Canvas(zoom)._text_reposition_block(block)
    assert block.page_wrap_width == pytest.approx(600)


def test_screen_width_follows_the_real_scale_of_the_letters():
    block = text_block(0.5)
    Canvas(0.5)._text_reposition_block(block)
    assert block.overlay.max_width == pytest.approx(600 * 0.5 * Ratios.drift)


def test_real_scale_is_measured_again_only_if_the_zoom_or_the_text_changed():
    block = text_block(1.0)
    canvas = Canvas(1.0)
    ratios.calls = 0
    canvas._text_reposition_block(block)
    canvas._text_reposition_block(block)
    assert ratios.calls == 1
    block.overlay.text_revision = 2
    canvas._text_reposition_block(block)
    assert ratios.calls == 2


OverlayBase = ast_loader.load_methods(
    "text_tool_qt.py", "_RichTextOverlay", {"set_max_width"},
    QTextEdit=types.SimpleNamespace(LineWrapMode=types.SimpleNamespace(FixedPixelWidth="fixe")),
)


class WrappingOverlay(OverlayBase):
    def __init__(self):
        self.modes = []
        self.widths = []
        self.adjusted = 0

    def setLineWrapMode(self, mode):
        self.modes.append(mode)

    def setLineWrapColumnOrWidth(self, width):
        self.widths.append(width)

    def _adjust_size(self):
        self.adjusted += 1


def test_document_gets_the_same_width_as_the_final_rendering():
    overlay = WrappingOverlay()
    overlay.set_max_width(599.6)
    assert overlay._max_width == 600
    assert overlay.modes == ["fixe"] and overlay.widths == [600] and overlay.adjusted == 1


def test_unknown_width_leaves_the_default_wrapping():
    overlay = WrappingOverlay()
    overlay.set_max_width(-1)
    assert overlay._max_width == -1 and overlay.modes == [] and overlay.adjusted == 1


class Panel(PanelBase):
    def __init__(self, block):
        self._ignore_format_signals = False
        self._viewer = types.SimpleNamespace(
            zoom_level=9.0, _canvas=types.SimpleNamespace(_text_active_block=lambda: block,
                                                          _text_reposition_block=lambda b: None))
        self._font_combo = types.SimpleNamespace(
            currentFont=lambda: types.SimpleNamespace(family=lambda: "Arial"))
        self._size_spin = types.SimpleNamespace(value=lambda: 24)
        self._text_color = "noir"


def edited_block(display_scale):
    applied = []
    default = []
    overlay = types.SimpleNamespace(
        apply_char_format=applied.append, setFocus=lambda: None,
        setCurrentCharFormat=applied.append,
        document=lambda: types.SimpleNamespace(setDefaultFont=default.append))
    block = types.SimpleNamespace(overlay=overlay, display_scale=display_scale)
    return block, applied, default


def test_size_change_keeps_the_scale_of_the_whole_block():
    block, applied, _unused = edited_block(0.5)
    Panel(block)._on_font_size_changed(30)
    assert block.display_scale == 0.5
    assert applied[0].size == 15.0


def test_screen_sizes_are_not_rounded():
    block, applied, default = edited_block(0.33)
    Panel(block).apply_default_format_to_block(block)
    assert applied[0].size == pytest.approx(24 * 0.33)
    assert default[0].size == pytest.approx(24 * 0.33)
    assert default[0].hinting == "sans ajustement"

    block, applied, _unused = edited_block(0.33)
    Panel(block)._on_font_size_changed(12)
    assert applied[0].size == pytest.approx(12 * 0.33)
