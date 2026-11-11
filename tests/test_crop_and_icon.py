"""Recadrage d'une page (crop_tool_qt.CropViewerMixin.perform_crop) et création
d'une icône .ico (ico_creator_qt.IcoCreatorDialog, _CropCanvas).

Recadrage, vérifié sur la page produite, relue :
- rectangle donné en pixels (mode des macros) : pixels exacts, format gardé,
  point d'historique avant et après ;
- rectangle tracé à la souris : conversion de l'affichage (décalage, zoom)
  en pixels de l'image, rectangle ramené dans l'image, tracé effacé ensuite,
  étape de macro enregistrée en pixels absolus ;
- rectangle vide ou inversé : message, page inchangée ; rectangle hors de la
  page (macro) : refus sans message, jamais de recadrage silencieux.

Icône :
- carré choisi converti en pixels de l'image, gardé dans l'image ;
- recadrage carré, ou image entière complétée en carré transparent, puis
  ramenée à 256×256 ;
- pipette : pixels voisins de couleur proche rendus transparents (tolérance),
  pixel déjà transparent sans effet ;
- .ico à toutes les tailles, inséré juste après la page source (retrouvée
  même déplacée, position d'origine si elle a été supprimée), états
  d'historique avant et après, nom jamais déjà pris ; rien pendant une
  opération ;
- gris 16 bits ramené à 256 nuances ; refus tant que le document a des
  dossiers ; page retouchée suivie pendant la découpe, avertissement pendant
  la transparence ou si elle est supprimée ; clic sans déplacement du cadre
  sans étape d'annulation ; clic hors de l'image sans effet.

Ces modules importent PySide6 : les méthodes sont extraites par AST
(tests/ast_loader.load_methods), l'affichage est remplacé.
"""
import io
import math
import os
import re
import sys
import types

import pytest
from PIL import Image

from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import create_entry, ensure_image_loaded, to_display_mode
from modules.qt.state import AppState
from modules.qt.undo_redo import entry_key, save_state_data
from modules.qt.undo_redo_qt import save_state_qt

import ast_loader

unique_names = ast_loader.load("utils.py", {"unique_names"}, os=os)["unique_names"]


class Messages:
    shown = []

    def __init__(self, parent, title_key, *args, **kwargs):
        Messages.shown.append(title_key)

    def show_nonmodal(self):
        pass


@pytest.fixture(autouse=True)
def fake_dialogs(monkeypatch):
    Messages.shown = []
    module = types.ModuleType("modules.qt.dialogs_qt")
    module.MsgDialog = Messages

    def page_operation_failed(parent, title_key, message_key, entry=None):
        Messages.shown.append((title_key, message_key, entry, sys.exc_info()[1]))
    module.page_operation_failed = page_operation_failed
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", module)


def two_halves(size=(100, 60)):
    img = Image.new("RGB", size, (255, 0, 0))
    img.paste((0, 0, 255), (size[0] // 2, 0, size[0], size[1]))
    return img


def png_entry(img, name="p01.png", fmt="PNG"):
    buf = io.BytesIO()
    img.save(buf, fmt)
    return create_entry(name, buf.getvalue(), IMAGE_EXTS)


def decoded(entry):
    return Image.open(io.BytesIO(entry["bytes"]))


# ---------------------------------------------------------------------------
# Recadrage
# ---------------------------------------------------------------------------

CropBase = ast_loader.load_methods("crop_tool_qt.py", "CropViewerMixin", {"perform_crop"})


class _Point:
    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y


class Viewer(CropBase):
    """Visionneuse : page affichée à `zoom`, décalée de (10, 10) dans la zone."""

    def __init__(self, state, index=0, zoom=2.0):
        self._state = state
        self.saves = 0

        def save_state():
            self.saves += 1
            save_state_data(state, force=True)
        self.callbacks = {"state": state, "save_state": save_state}
        self.current_idx, self.zoom_level = index, zoom
        self._center_parent = None
        img_w, img_h = state.images_data[index]["img_width"], state.images_data[index]["img_height"]
        self.cleared = []
        self._canvas = types.SimpleNamespace(
            display_offset_x=10, display_offset_y=10,
            display_width=int(img_w * zoom), display_height=int(img_h * zoom),
            _crop_start=None, _crop_end=None, clear_crop=lambda: self.cleared.append(True))
        self._toolbar = types.SimpleNamespace(_crop_mode=lambda: 0,
                                              refresh_undo_redo_state=lambda: None)
        self._crop_by_page = {index: "tracé en cours"}
        self.recorded = []

    def display_image(self):
        pass

    def _refuse_with_pending_page_work(self, ignore_crop=False):
        return False

    def _macro_record_step(self, tool, params, *args):
        self.recorded.append((tool, params))

    def draw(self, start, end):
        self._canvas._crop_start, self._canvas._crop_end = _Point(*start), _Point(*end)


def page_document(entry):
    st = AppState()
    st.images_data = [entry]
    save_state_data(st, force=True)
    return st


def test_crop_in_pixels_keeps_exact_pixels_and_records_history():
    source = two_halves()
    st = page_document(png_entry(source))
    viewer = Viewer(st)
    assert viewer.perform_crop(override_px=(10, 5, 60, 45)) is True
    out = decoded(st.images_data[0])
    assert out.format == "PNG" and out.size == (50, 40)
    assert out.convert("RGB").tobytes() == source.crop((10, 5, 60, 45)).tobytes()
    assert viewer.saves == 2 and st.modified is True
    assert viewer.recorded == []                         # macro rejouée : pas de nouvelle étape


def test_crop_drawn_with_the_mouse_is_converted_to_image_pixels():
    st = page_document(png_entry(two_halves()))
    viewer = Viewer(st, zoom=2.0)
    viewer.draw((30, 20), (130, 70))                     # zone affichée : décalage 10, zoom 2
    assert viewer.perform_crop() is True
    assert decoded(st.images_data[0]).size == (50, 25)
    assert viewer.recorded == [("crop", {"px": [10, 5, 60, 30]})]
    assert viewer.cleared == [True] and 0 not in viewer._crop_by_page


def test_rectangle_drawn_past_the_page_is_kept_inside_it():
    st = page_document(png_entry(two_halves()))
    viewer = Viewer(st, zoom=2.0)
    viewer.draw((110, 70), (900, 900))
    assert viewer.perform_crop() is True
    assert viewer.recorded == [("crop", {"px": [50, 30, 100, 60]})]


def test_empty_rectangle_shows_a_message_and_changes_nothing():
    st = page_document(png_entry(two_halves()))
    before = st.images_data[0]["bytes"]
    viewer = Viewer(st)
    viewer.draw((60, 60), (40, 40))                      # tracé à l'envers
    assert viewer.perform_crop() is False
    assert st.images_data[0]["bytes"] is before and viewer.saves == 0
    assert Messages.shown == ["messages.errors.crop_invalid.title"]


def test_macro_rectangle_outside_the_page_is_refused_without_message():
    st = page_document(png_entry(two_halves()))
    before = st.images_data[0]["bytes"]
    assert Viewer(st).perform_crop(override_px=(10, 10, 500, 50)) is False
    assert st.images_data[0]["bytes"] is before and Messages.shown == []


def test_cropped_jpeg_stays_a_jpeg():
    st = page_document(png_entry(two_halves(), name="p01.jpg", fmt="JPEG"))
    Viewer(st).perform_crop(override_px=(0, 0, 40, 30))
    assert decoded(st.images_data[0]).format == "JPEG"


# ---------------------------------------------------------------------------
# Icône
# ---------------------------------------------------------------------------

CanvasBase = ast_loader.load_methods("ico_creator_qt.py", "_CropCanvas",
                                     {"get_crop_in_image_coords", "_clamp_rect_to_image"})
_ICO_SIZES = ast_loader.load("ico_creator_qt.py", {"_ICO_SIZES"})["_ICO_SIZES"]
ico_helpers = ast_loader.load(
    "ico_creator_qt.py", {"_page_image", "_has_folders", "_rect_moved", "_RECT_UNCHANGED"},
    ensure_image_loaded=ensure_image_loaded, to_display_mode=to_display_mode)
IcoBase = ast_loader.load_methods(
    "ico_creator_qt.py", "IcoCreatorDialog",
    {"_on_validate_crop", "_on_validate_no_crop", "_prepare_phase_b", "_apply_transparency",
     "_on_validate_final", "_find_page", "_get_ico_name", "_on_document_changed",
     "_set_source_changed"},
    Image=Image, io=io, os=os, re=re, _ICO_SIZES=_ICO_SIZES, save_state_qt=save_state_qt,
    unique_names=unique_names, entry_key=entry_key, _page_image=ico_helpers["_page_image"],
    _pil_to_qpixmap=lambda img: ("pixmap", img.size),
    _state_module=types.SimpleNamespace(state=None))


class SquareCanvas(CanvasBase):
    """Image de 200×100 affichée à moitié, décalée de (5, 5)."""

    def __init__(self, rect):
        self._img_w, self._img_h = 200, 100
        self.display_offset_x = self.display_offset_y = 5
        self.display_width, self.display_height = 100, 50
        self.rect_x1, self.rect_y1, self.rect_x2, self.rect_y2 = rect


def test_chosen_square_is_converted_to_image_pixels():
    assert SquareCanvas((15, 10, 45, 40)).get_crop_in_image_coords() == (20, 10, 80, 70)


def test_square_is_kept_inside_the_image():
    canvas = SquareCanvas((90, 40, 120, 70))             # déborde à droite et en bas
    canvas._clamp_rect_to_image()
    assert (canvas.rect_x1, canvas.rect_y1, canvas.rect_x2, canvas.rect_y2) == (75, 25, 105, 55)


def test_image_not_displayed_gives_an_empty_square():
    canvas = SquareCanvas((15, 10, 45, 40))
    canvas.display_width = 0
    assert canvas.get_crop_in_image_coords() == (0, 0, 0, 0)


class IconWindow(IcoBase):
    def __init__(self, state, entry, idx, square=(0, 0, 100, 100)):
        self._callbacks = {"state": state, "render_mosaic": lambda: None,
                           "refresh_toolbar": lambda: None}
        self._entry, self._idx = entry, idx
        self._page_key, self._page_bytes = entry_key(entry), entry["bytes"]
        self._source_changed = False
        self._phase = "a"
        self._original_img = Image.open(io.BytesIO(entry["bytes"])).convert("RGBA")
        self._crop_canvas = types.SimpleNamespace(get_crop_in_image_coords=lambda: square)
        self._undo_stack_b, self._redo_stack_b = ["ancien"], ["ancien"]
        self.warning_shown = []
        self._warn_label = types.SimpleNamespace(setVisible=self.warning_shown.append)
        self.redisplayed = 0
        self.closed = False

    def _display_image_a(self, init=False):
        self.redisplayed += 1

    def _build_phase_b(self):
        pass

    def close(self):
        self.closed = True


def icon_document(img):
    st = AppState()
    entry = png_entry(img)
    st.images_data = [entry, png_entry(two_halves(), name="p02.png")]
    save_state_data(st, force=True)
    return st, entry


def test_square_crop_becomes_a_256_pixel_icon():
    st, entry = icon_document(two_halves((200, 100)))
    window = IconWindow(st, entry, 0, square=(0, 0, 100, 60))   # carré : 60×60
    window._on_validate_crop()
    assert window._ico_img_rgba.size == (256, 256)
    assert window._ico_img_rgba.getpixel((128, 128))[:3] == (255, 0, 0)
    assert window._undo_stack_b == [] and window._redo_stack_b == []


def test_whole_image_is_completed_into_a_transparent_square():
    st, entry = icon_document(two_halves((200, 100)))
    window = IconWindow(st, entry, 0)
    window._on_validate_no_crop()
    icon = window._ico_img_rgba
    assert icon.size == (256, 256)
    assert icon.getpixel((128, 10))[3] == 0                     # bande ajoutée : transparente
    assert icon.getpixel((10, 128))[:3] == (255, 0, 0) and icon.getpixel((10, 128))[3] == 255


def white_and_black():
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 255))
    img.paste((255, 255, 255, 255), (0, 0, 128, 256))
    img.putpixel((10, 10), (250, 250, 250, 255))                 # presque blanc
    return img


def test_pipette_makes_the_connected_similar_color_transparent():
    st, entry = icon_document(two_halves())
    window = IconWindow(st, entry, 0)
    window._ico_img_rgba = white_and_black()
    window._apply_transparency(50, 50, 10)
    assert window._ico_img_rgba.getpixel((50, 50))[3] == 0
    assert window._ico_img_rgba.getpixel((10, 10))[3] == 0      # dans la tolérance
    assert window._ico_img_rgba.getpixel((200, 50))[3] == 255   # autre couleur : gardée


def test_pipette_tolerance_and_already_transparent_pixel():
    st, entry = icon_document(two_halves())
    window = IconWindow(st, entry, 0)
    window._ico_img_rgba = white_and_black()
    window._apply_transparency(50, 50, 2)
    assert window._ico_img_rgba.getpixel((10, 10))[3] == 255    # hors tolérance
    before = window._ico_img_rgba.tobytes()
    window._apply_transparency(50, 50, 255)                      # pixel déjà transparent
    assert window._ico_img_rgba.tobytes() == before


def test_icon_has_every_size_and_is_inserted_after_its_page():
    st, entry = icon_document(two_halves())
    window = IconWindow(st, entry, 0)
    window._on_validate_no_crop()
    st.images_data.insert(0, png_entry(two_halves(), name="p00.png"))   # page déplacée
    save_state_data(st)                                          # avec son état, comme dans l'appli
    steps = len(st.history)
    window._on_validate_final()
    assert [e["orig_name"] for e in st.images_data] == [
        "p00.png", "p01.png", "ICO001_p01.ico", "p02.png"]
    icon = Image.open(io.BytesIO(st.images_data[2]["bytes"]))
    assert icon.format == "ICO" and set(icon.info["sizes"]) == set(_ICO_SIZES)
    assert st.modified is True and len(st.history) == steps + 1 and window.closed


def test_icon_of_a_deleted_page_goes_to_its_former_position():
    st, entry = icon_document(two_halves())
    window = IconWindow(st, entry, 0)
    window._on_validate_no_crop()
    st.images_data.remove(entry)
    window._on_validate_final()
    assert [e["orig_name"] for e in st.images_data] == ["p02.png", "ICO001_p01.ico"]


def test_change_left_without_history_state_is_not_undone_with_the_icon():
    """Un seul état, après l'ajout : Annuler l'icône annulait aussi la
    modification d'avant restée sans le sien."""
    st, entry = icon_document(two_halves())
    window = IconWindow(st, entry, 0)
    window._on_validate_no_crop()
    st.images_data[1]["orig_name"] = "p09.png"                   # sans état d'historique
    steps = len(st.history)
    window._on_validate_final()
    assert len(st.history) == steps + 2                          # avant et après l'ajout


def test_icon_name_is_chosen_at_validation_and_never_taken():
    """Deux fenêtres ouvertes sur la même page donnaient deux icônes du même nom."""
    st, entry = icon_document(two_halves())
    first, second = IconWindow(st, entry, 0), IconWindow(st, entry, 0)
    first._on_validate_no_crop()
    second._on_validate_no_crop()
    first._on_validate_final()
    second._on_validate_final()
    names = [e["orig_name"] for e in st.images_data]
    assert "ICO001_p01.ico" in names and "ICO002_p01.ico" in names


def test_16_bit_gray_page_is_not_turned_white():
    gray = Image.new("I;16", (8, 8), 30000)                      # gris moyen
    entry = png_entry(gray, name="g.png")
    img = ico_helpers["_page_image"](entry)
    assert img.mode == "RGBA" and 100 < img.getpixel((1, 1))[0] < 140
    assert entry["img"] is None                                  # pas gardée sur la page


def test_icon_refused_while_the_document_has_folders():
    has_folders = ico_helpers["_has_folders"]
    assert has_folders([{"orig_name": "Aaaa/001.jpg"}])
    assert not has_folders([{"orig_name": "001.jpg"}, {"orig_name": "Aaaa/", "is_dir": True}])
    opened = []
    create = ast_loader.load(
        "ico_creator_qt.py", {"create_ico_from_selected", "_has_folders"},
        _state_module=types.SimpleNamespace(state=None),
        ErrorDialog=Messages, IcoCreatorDialog=lambda *a: opened.append(a),
        _=lambda key, **kw: key)["create_ico_from_selected"]
    st = AppState()
    st.images_data = [png_entry(two_halves(), name="Aaaa/001.png")]
    st.selected_indices = {0}
    create(None, {"state": st})
    assert opened == [] and Messages.shown == []                # commande grisée : rien


def test_create_icon_command_is_greyed_while_the_document_has_folders():
    """Menus et colonne d'icônes : même règle que la renumérotation. Le clic
    droit sur une vignette reprend le menu Images de la barre."""
    source = {name: (ast_loader.QT_DIR / name).read_text(encoding="utf-8")
              for name in ("context_menus_qt.py", "menubar_qt.py", "icon_toolbar_qt.py")}
    assert "_populate_images_menu(menu, callbacks)" in source["context_menus_qt.py"]
    assert "enabled=single_sel and not is_corrupted and not has_subdir)" in source["menubar_qt.py"]
    assert ('"create_ico":          lambda sg: sg["single_image_selected"]() '
            'and not sg["has_subdirs"](),') in source["icon_toolbar_qt.py"]


def test_page_retouched_during_the_crop_is_reloaded():
    st, entry = icon_document(two_halves())
    window = IconWindow(st, entry, 0)
    entry["bytes"] = png_entry(Image.new("RGB", (30, 50), (0, 255, 0)))["bytes"]
    window._on_document_changed()
    assert window._original_img.size == (30, 50) and window.redisplayed == 1
    assert window._source_changed is False


def test_page_retouched_during_transparency_keeps_the_work_with_a_warning():
    st, entry = icon_document(two_halves())
    window = IconWindow(st, entry, 0)
    window._on_validate_no_crop()
    window._phase = "b"
    work = window._ico_img_rgba
    entry["bytes"] = png_entry(Image.new("RGB", (30, 50), (0, 255, 0)))["bytes"]
    window._on_document_changed()
    assert window._ico_img_rgba is work and window.warning_shown == [True]


def test_deleted_page_shows_the_warning_until_undo_brings_it_back():
    st, entry = icon_document(two_halves())
    window = IconWindow(st, entry, 0)
    st.images_data.remove(entry)
    window._on_document_changed()
    st.images_data.insert(0, entry)
    window._on_document_changed()
    assert window.warning_shown == [True, False] and window.redisplayed == 0


CropRelease = ast_loader.load_methods(
    "ico_creator_qt.py", "_CropCanvas", {"mouseReleaseEvent", "_canvas_to_rel"},
    Qt=types.SimpleNamespace(LeftButton="gauche", RightButton="droit", ArrowCursor="flèche"),
    QCursor=lambda shape: shape, _rect_moved=ico_helpers["_rect_moved"])


class ReleasedCanvas(CropRelease):
    def __init__(self, moved_by):
        self._img_w, self._img_h = 200, 100
        self.display_offset_x = self.display_offset_y = 0
        self.display_width, self.display_height = 200, 100
        self.rect_x1, self.rect_y1, self.rect_x2, self.rect_y2 = 10 + moved_by, 10, 60 + moved_by, 60
        self._rel_before = (10 / 200, 10 / 100, 50 / 200)
        self._drag_mode = self._drag_rect_orig = None
        self.on_rect_changed = None
        self.committed = []
        self.on_rect_committed = self.committed.append


@pytest.mark.parametrize("moved_by, steps", [(0, 0), (5, 1)])
def test_only_a_moved_frame_adds_an_undo_step(moved_by, steps):
    canvas = ReleasedCanvas(moved_by)
    canvas.mouseReleaseEvent(types.SimpleNamespace(button=lambda: "gauche"))
    assert len(canvas.committed) == steps


def test_click_just_outside_the_left_edge_does_nothing():
    """int() ramenait un clic juste à gauche de l'image sur son bord."""
    Press = ast_loader.load_methods(
        "ico_creator_qt.py", "_TransparencyCanvas", {"mousePressEvent"},
        math=math, Qt=types.SimpleNamespace(LeftButton="gauche", RightButton="droit"),
        QCursor=lambda shape: shape, QPoint=lambda x, y: (x, y))

    class Canvas(Press):
        _off_x, _off_y, _zoom = 100, 100, 4.0
        pipette_active = True

        def __init__(self):
            self.clicked = []
            self.on_pipette_click = lambda x, y: self.clicked.append((x, y))

    def click(x):
        return types.SimpleNamespace(button=lambda: "gauche",
                                     position=lambda: types.SimpleNamespace(x=lambda: x, y=lambda: 150))

    canvas = Canvas()
    canvas.mousePressEvent(click(99))                            # 1 pixel d'écran à gauche
    canvas.mousePressEvent(click(101))
    assert canvas.clicked == [(0, 12)]


def test_no_icon_is_created_during_an_operation():
    st, entry = icon_document(two_halves())
    window = IconWindow(st, entry, 0)
    window._on_validate_no_crop()
    st.begin_operation(object())
    window._on_validate_final()
    assert len(st.images_data) == 2 and not window.closed
