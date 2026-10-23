"""Découpe d'une page en plusieurs (split_dialog_qt.split_page).

Vérifié, sur de vraies images :
- morceaux de même taille, contenu exact, insérés juste après la page
  d'origine (gardée), nommés _part01, _part02… ; point d'historique avant et
  après ;
- chaque morceau garde le format de la page : JPEG (profil ICC et DPI
  gardés), WebP sans perte resté sans perte (pixels identiques), PNG ;
  GIF animé : chaque morceau garde toutes ses images ;
- fenêtre non modale : page supprimée, ou opération lancée, pendant qu'elle
  était ouverte : rien n'est découpé ; réponse « Annuler » : rien non plus ;
- sélection vide ou multiple : message, rien d'ouvert.

split_dialog_qt.py importe PySide6 : la fonction est extraite par AST
(tests/ast_loader.py), la fenêtre de réglage est remplacée.
"""
import io
import os
import types

import pytest
from PIL import Image, ImageCms

from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import (_webp_is_lossless, create_entry, ensure_image_loaded,
                                free_image_memory, image_dpi, metadata_save_kwargs,
                                page_icc_profile, save_image_to_bytes)
from modules.qt.image_ops import (transform_animated_gif, transform_animated_image,
                                  transform_multipage_tiff)
from modules.qt.state import AppState
from modules.qt.undo_redo import save_state_data

import ast_loader


class FakeSplitDialog:
    """Fenêtre de réglage : le test choisit les réglages et donne la réponse."""
    opened = []

    def __init__(self, parent, callbacks):
        self.num_pages, self.direction, self.answer = 2, "vertical", None
        FakeSplitDialog.opened.append(self)

    def ask_async(self, on_result):
        self.answer = on_result


class Messages:
    shown = []

    def __init__(self, parent, title_key, message_key, *args, **kwargs):
        Messages.shown.append(title_key)

    def show_nonmodal(self):
        pass


split_page = ast_loader.load(
    "split_dialog_qt.py", {"split_page"},
    io=io, os=os, IMAGE_EXTS=IMAGE_EXTS, MsgDialog=Messages, SplitDialog=FakeSplitDialog,
    _state_module=types.SimpleNamespace(state=None), _webp_is_lossless=_webp_is_lossless,
    create_entry=create_entry, ensure_image_loaded=ensure_image_loaded,
    free_image_memory=free_image_memory, image_dpi=image_dpi,
    metadata_save_kwargs=metadata_save_kwargs, page_icc_profile=page_icc_profile,
    save_image_to_bytes=save_image_to_bytes, transform_animated_gif=transform_animated_gif,
    transform_animated_image=transform_animated_image,
    transform_multipage_tiff=transform_multipage_tiff)["split_page"]


@pytest.fixture(autouse=True)
def reset():
    FakeSplitDialog.opened, Messages.shown = [], []


def two_halves(size=(100, 60), mode="RGB"):
    """Moitié gauche rouge, moitié droite bleue."""
    img = Image.new(mode, size, (255, 0, 0))
    img.paste((0, 0, 255), (size[0] // 2, 0, size[0], size[1]))
    return img


def encoded(img, fmt, **save):
    buf = io.BytesIO()
    img.save(buf, fmt, **save)
    return buf.getvalue()


def document(entry, other=True):
    st = AppState()
    st.images_data = [entry] + ([create_entry("p02.png", encoded(two_halves(), "PNG"),
                                              IMAGE_EXTS)] if other else [])
    st.selected_indices = {0}
    save_state_data(st, force=True)
    saves = []

    def save_state(force=False):
        saves.append(force)
        save_state_data(st, force=True)
    callbacks = {"state": st, "save_state": save_state, "render_mosaic": lambda: None,
                 "update_button_text": lambda: None}
    return st, callbacks, saves


def split(st, callbacks, num_pages=2, direction="vertical", answer=True):
    split_page(None, callbacks)
    [dialog] = FakeSplitDialog.opened
    dialog.num_pages, dialog.direction = num_pages, direction
    dialog.answer(answer)


def parts(st):
    return [e for e in st.images_data if "_part" in e["orig_name"]]


def test_page_is_cut_into_equal_parts_inserted_after_it():
    entry = create_entry("p01.png", encoded(two_halves(), "PNG"), IMAGE_EXTS)
    st, callbacks, saves = document(entry)
    split(st, callbacks)
    assert [e["orig_name"] for e in st.images_data] == ["p01.png", "p01_part01.png",
                                                        "p01_part02.png", "p02.png"]
    left, right = (Image.open(io.BytesIO(e["bytes"])) for e in parts(st))
    assert left.size == right.size == (50, 60)
    assert left.getpixel((10, 10)) == (255, 0, 0) and right.getpixel((10, 10)) == (0, 0, 255)
    assert len(saves) == 2                               # avant et après la découpe
    assert st.modified is True


def test_horizontal_cut_in_three():
    entry = create_entry("p01.png", encoded(two_halves((60, 90)), "PNG"), IMAGE_EXTS)
    st, callbacks, _saves = document(entry)
    split(st, callbacks, num_pages=3, direction="horizontal")
    assert [Image.open(io.BytesIO(e["bytes"])).size for e in parts(st)] == [(60, 30)] * 3


def test_jpeg_parts_keep_the_profile_and_resolution():
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    entry = create_entry("p01.jpg", encoded(two_halves(), "JPEG", quality=95, icc_profile=profile,
                                            dpi=(300, 300)), IMAGE_EXTS)
    st, callbacks, _saves = document(entry)
    split(st, callbacks)
    for part in parts(st):
        img = Image.open(io.BytesIO(part["bytes"]))
        assert img.format == "JPEG"
        assert img.info.get("icc_profile") == profile
        assert round(img.info["dpi"][0]) == 300


def test_lossless_webp_parts_stay_lossless():
    source = two_halves()
    entry = create_entry("p01.webp", encoded(source, "WEBP", lossless=True), IMAGE_EXTS)
    st, callbacks, _saves = document(entry)
    split(st, callbacks)
    left = parts(st)[0]
    assert _webp_is_lossless(left["bytes"])
    assert Image.open(io.BytesIO(left["bytes"])).convert("RGB").tobytes() == \
        source.crop((0, 0, 50, 60)).tobytes()


def test_animated_gif_parts_keep_every_frame():
    frames = [two_halves().convert("P"), two_halves().transpose(Image.Transpose.FLIP_LEFT_RIGHT).convert("P")]
    entry = create_entry("p01.gif", encoded(frames[0], "GIF", save_all=True,
                                            append_images=frames[1:], duration=120, loop=0),
                         IMAGE_EXTS)
    st, callbacks, _saves = document(entry)
    split(st, callbacks)
    for part in parts(st):
        img = Image.open(io.BytesIO(part["bytes"]))
        assert img.n_frames == 2 and img.size == (50, 60)


def test_page_deleted_while_the_window_was_open_is_not_cut():
    entry = create_entry("p01.png", encoded(two_halves(), "PNG"), IMAGE_EXTS)
    st, callbacks, saves = document(entry)
    split_page(None, callbacks)
    st.images_data.remove(entry)
    FakeSplitDialog.opened[0].answer(True)
    assert parts(st) == [] and saves == []


def test_operation_started_while_the_window_was_open_cuts_nothing():
    entry = create_entry("p01.png", encoded(two_halves(), "PNG"), IMAGE_EXTS)
    st, callbacks, saves = document(entry)
    split_page(None, callbacks)
    st.begin_operation(object())
    FakeSplitDialog.opened[0].answer(True)
    assert parts(st) == [] and saves == []


def test_cancelled_window_cuts_nothing():
    entry = create_entry("p01.png", encoded(two_halves(), "PNG"), IMAGE_EXTS)
    st, callbacks, saves = document(entry)
    split(st, callbacks, answer=False)
    assert parts(st) == [] and saves == []


@pytest.mark.parametrize("selection", [set(), {0, 1}])
def test_empty_or_multiple_selection_shows_a_message(selection):
    entry = create_entry("p01.png", encoded(two_halves(), "PNG"), IMAGE_EXTS)
    st, callbacks, _saves = document(entry)
    st.selected_indices = selection
    split_page(None, callbacks)
    assert FakeSplitDialog.opened == [] and len(Messages.shown) == 1
