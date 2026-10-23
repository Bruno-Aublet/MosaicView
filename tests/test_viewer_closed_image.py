"""Visionneuse : image d'une page fermée par un traitement en arrière-plan
(image_viewer_qt.ImageViewer._display_single_page).

Un traitement lancé depuis la mosaïque (rotation, redimensionnement…) ferme
l'image décodée d'une page dans son thread. Si la visionneuse affiche cette
page au même instant, la copie de l'image échoue (« Operation on closed
image ») : la visionneuse décode alors une image à elle depuis les bytes de la
page, sans jamais écrire dans la page, que le traitement est peut-être en
train de remplir.

Vérifié par le comportement : image fermée → page affichée quand même, à
partir de ses bytes, et page inchangée ; page sans bytes → rien d'affiché,
sans erreur ; image ordinaire → affichée. image_viewer_qt.py importe PySide6 :
la méthode est extraite par AST (tests/ast_loader.load_methods) ; la
conversion en image Qt et la zone d'affichage sont remplacées.
"""
import io
import types

from PIL import Image

from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import animation_play_count, create_entry, ensure_image_loaded, open_page_image
from modules.qt.state import AppState

import ast_loader

SHOWN = []

Base = ast_loader.load_methods(
    "image_viewer_qt.py", "ImageViewer", {"_display_single_page"},
    _state_module=types.SimpleNamespace(state=None), ensure_image_loaded=ensure_image_loaded,
    animation_play_count=animation_play_count, open_page_image=open_page_image,
    _compose_on_checkerboard=lambda img: img, _pil_to_qpixmap=lambda img: SHOWN.append(img) or img,
    QPoint=lambda x, y: (x, y))


class _Button:
    def __getattr__(self, name):
        return lambda *a: None


class Viewer(Base):
    WEBTOON_MAX_WIDTH_PX = 800

    def __init__(self, state):
        self.callbacks = {"state": state}
        self._sharpness_preview_img = None
        self.current_idx, self.page_mode = 0, "single"
        self._zoom_initialized, self.zoom_level = False, 1.0
        self._play_pause_btn = _Button()
        self.placed = []
        self._canvas = types.SimpleNamespace(
            width=lambda: 400, height=lambda: 300, pan_offset_x=0, pan_offset_y=0,
            crop_rel_x1=None, has_line=False, has_text_blocks=False,
            set_pixmap_and_geometry=lambda pixmap, x, y, w, h: self.placed.append((w, h)))

    def _stop_gif(self):
        pass

    def _set_name_label_pages(self, entry):
        pass

    def _schedule_name_hide(self):
        pass

    def _update_mode_label(self):
        pass

    def _reposition_gif_play_btn(self):
        pass


def page_with_image(color="red"):
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buf, "PNG")
    entry = create_entry("p01.png", buf.getvalue(), IMAGE_EXTS)
    ensure_image_loaded(entry)
    st = AppState()
    st.images_data = [entry]
    return st, entry


def setup_function():
    SHOWN.clear()


def test_page_shown_from_its_bytes_when_its_image_was_closed():
    st, entry = page_with_image()
    closed = entry["img"]
    closed.close()                                      # fermée par un traitement en cours
    before = dict(entry)
    viewer = Viewer(st)
    viewer._display_single_page(0, 400, 300)
    [shown] = SHOWN
    assert shown.size == (40, 30) and shown.convert("RGB").getpixel((5, 5)) == (255, 0, 0)
    assert viewer.placed == [(400, 300)]                # zoom ajusté à la zone (40×30 → 400×300)
    assert entry == before and entry["img"] is closed   # page jamais modifiée


def test_page_without_bytes_shows_nothing_without_error():
    st, entry = page_with_image()
    entry["img"].close()
    entry["bytes"] = None
    viewer = Viewer(st)
    viewer._display_single_page(0, 400, 300)
    assert SHOWN == [] and viewer.placed == []


def test_ordinary_page_is_shown():
    st, _entry = page_with_image("blue")
    viewer = Viewer(st)
    viewer._display_single_page(0, 400, 300)
    [shown] = SHOWN
    assert shown.convert("RGB").getpixel((5, 5)) == (0, 0, 255)
