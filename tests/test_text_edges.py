"""Outil Texte : un bloc qui dépasse de la page est coupé, jamais déplacé.

Le texte validé est collé exactement à la position de l'aperçu, y compris
au-dessus ou à gauche de la page. Le rendu Qt du bloc est remplacé par un
rectangle noir de 10 × 30 pixels : seul le collage sur la page est vérifié.
"""
import types

import pytest
from PIL import Image

import ast_loader

TEXT_W, TEXT_H = 10, 30
BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)


class FakeImage:
    Format = types.SimpleNamespace(Format_ARGB32="ARGB32")

    def __init__(self, width, height, _format):
        self.width, self.height = width, height

    def fill(self, color):
        pass

    def constBits(self):
        return bytes((0, 0, 0, 255)) * (self.width * self.height)


class FakePainter:
    RenderHint = types.SimpleNamespace(Antialiasing=1, TextAntialiasing=2)

    def __init__(self, image):
        pass

    def setRenderHint(self, hint):
        pass

    def end(self):
        pass


Base = ast_loader.load_methods(
    "text_tool_qt.py", "TextViewerMixin", {"_text_render_all_blocks"},
    Image=Image, QImage=FakeImage, QPainter=FakePainter, QRectF=lambda *args: args,
    Qt=types.SimpleNamespace(GlobalColor=types.SimpleNamespace(transparent=0)),
    _scale_document_fonts=lambda doc, factor: None,
)


def block_at(x, y):
    doc = types.SimpleNamespace(
        setTextWidth=lambda width: None,
        size=lambda: types.SimpleNamespace(width=lambda: TEXT_W, height=lambda: TEXT_H),
        drawContents=lambda painter, rect: None)
    position = types.SimpleNamespace(x=lambda: x, y=lambda: y)
    return types.SimpleNamespace(
        is_empty=lambda: False, img_pos=position, top_y_offset_img=15,
        display_scale=1.0, page_wrap_width=100,
        overlay=types.SimpleNamespace(document=lambda: types.SimpleNamespace(clone=lambda: doc)))


class Viewer(Base):
    def __init__(self, block):
        self._canvas = types.SimpleNamespace(_text_blocks=[block])


def rendered(x, y):
    page = Image.new("RGBA", (100, 100), WHITE)
    return Viewer(block_at(x, y))._text_render_all_blocks(page)


def black_rows(page, x):
    return [y for y in range(page.height) if page.getpixel((x, y)) == BLACK]


def black_columns(page, y):
    return [x for x in range(page.width) if page.getpixel((x, y)) == BLACK]


def test_block_inside_the_page_is_written_at_its_place():
    page = rendered(20, 50)          # haut du bloc : 50 - 15
    assert black_rows(page, 25) == list(range(35, 65))
    assert black_columns(page, 40) == list(range(20, 30))


def test_block_above_the_page_is_cut_and_not_moved_down():
    page = rendered(20, 0)           # haut du bloc : -15
    assert black_rows(page, 25) == list(range(0, 15))


def test_block_left_of_the_page_is_cut_and_not_moved_right():
    page = rendered(-5, 50)
    assert black_columns(page, 40) == list(range(0, 5))


def test_block_past_the_bottom_right_corner_is_cut():
    page = rendered(95, 90)          # haut du bloc : 75
    assert black_rows(page, 97) == list(range(75, 100))
    assert black_columns(page, 80) == list(range(95, 100))


@pytest.mark.parametrize("x, y", [(-50, 50), (200, 50), (20, -100), (20, 200)])
def test_block_entirely_outside_the_page_writes_nothing(x, y):
    page = rendered(x, y)
    assert page.getcolors() == [(10000, WHITE)]
