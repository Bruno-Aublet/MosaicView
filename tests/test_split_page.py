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
- sélection vide ou multiple : message, rien d'ouvert ;
- question sur la page d'origine : gardée par défaut ; supprimée sur demande,
  les parties restant à sa place, avec un état d'historique de plus et ses
  attributs de <Page> repris (sauf « page double ») ; réponse tardive sans effet
  si une partie manque, si la page a disparu, si le document a changé ou si une
  opération tourne ; page rétablie par Annuler (nouvel objet) reconnue.

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
from modules.qt.undo_redo import adopt_entry_key, entry_key, save_state_data

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


class FakeOriginalQuestion:
    """Question sur la page d'origine : le test donne la réponse."""
    opened = []

    def __init__(self, parent, count):
        self.count, self.answer = count, None
        FakeOriginalQuestion.opened.append(self)

    def ask_async(self, on_result):
        self.answer = on_result


_split_ns = ast_loader.load(
    "split_dialog_qt.py", {"split_page", "_delete_split_original"},
    entry_key=entry_key, SplitOriginalDialog=FakeOriginalQuestion,
    io=io, os=os, IMAGE_EXTS=IMAGE_EXTS, MsgDialog=Messages, SplitDialog=FakeSplitDialog,
    _state_module=types.SimpleNamespace(state=None), _webp_is_lossless=_webp_is_lossless,
    create_entry=create_entry, ensure_image_loaded=ensure_image_loaded,
    free_image_memory=free_image_memory, image_dpi=image_dpi,
    metadata_save_kwargs=metadata_save_kwargs, page_icc_profile=page_icc_profile,
    save_image_to_bytes=save_image_to_bytes, transform_animated_gif=transform_animated_gif,
    transform_animated_image=transform_animated_image,
    transform_multipage_tiff=transform_multipage_tiff)
split_page = _split_ns["split_page"]


@pytest.fixture(autouse=True)
def reset():
    FakeSplitDialog.opened, Messages.shown = [], []
    FakeOriginalQuestion.opened = []


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


# --- Question sur la page d'origine -------------------------------------------

def split_and_question(st, callbacks, num_pages=2):
    split(st, callbacks, num_pages=num_pages)
    [question] = FakeOriginalQuestion.opened
    return question


def page_names(st):
    return [e["orig_name"] for e in st.images_data]


def test_question_is_asked_after_the_split_and_keeps_the_page_by_default():
    st, callbacks, _saves = document(create_entry("p01.png", encoded(two_halves(), "PNG"), IMAGE_EXTS))
    question = split_and_question(st, callbacks, num_pages=3)
    assert question.count == 3
    assert page_names(st) == ["p01.png", "p01_part01.png", "p01_part02.png", "p01_part03.png", "p02.png"]
    steps = len(st.history)
    question.answer(False)                                  # bouton Garder, croix ou Échap
    assert page_names(st)[0] == "p01.png" and len(st.history) == steps


def test_no_question_when_the_split_is_cancelled():
    st, callbacks, _saves = document(create_entry("p01.png", encoded(two_halves(), "PNG"), IMAGE_EXTS))
    split(st, callbacks, answer=False)
    assert FakeOriginalQuestion.opened == []


def test_deleting_the_original_leaves_the_parts_at_its_place_and_can_be_undone():
    st, callbacks, _saves = document(create_entry("p01.png", encoded(two_halves(), "PNG"), IMAGE_EXTS))
    question = split_and_question(st, callbacks)
    steps = len(st.history)
    question.answer(True)
    assert page_names(st) == ["p01_part01.png", "p01_part02.png", "p02.png"]
    assert not st.selected_indices and st.modified is True
    assert len(st.history) >= steps + 1
    previous = [e["orig_name"] for e in st.history[steps - 1]["entries"]]
    assert previous[0] == "p01.png"                         # Ctrl+Z rend l'originale


def test_first_part_takes_the_page_information_except_double_page():
    entry = create_entry("p01.png", encoded(two_halves(), "PNG"), IMAGE_EXTS)
    st, callbacks, _saves = document(entry)
    st._page_attrs_by_entry_id = {id(entry): {"Type": "FrontCover", "DoublePage": "True",
                                              "Bookmark": "Aaaa", "ImageWidth": "100"}}
    question = split_and_question(st, callbacks)
    question.answer(True)
    attrs = st._page_attrs_by_entry_id[id(st.images_data[0])]
    assert attrs["Type"] == "FrontCover" and attrs["Bookmark"] == "Aaaa"
    assert "DoublePage" not in attrs and attrs["ImageWidth"] == "50"


def test_late_answer_keeps_the_page_when_a_part_is_gone():
    """Annuler pendant la question retire les parties : supprimer l'originale la
    ferait disparaître sous toute forme."""
    st, callbacks, _saves = document(create_entry("p01.png", encoded(two_halves(), "PNG"), IMAGE_EXTS))
    question = split_and_question(st, callbacks)
    del st.images_data[1]
    question.answer(True)
    assert page_names(st)[0] == "p01.png"


def test_late_answer_does_nothing_when_the_page_is_gone():
    st, callbacks, _saves = document(create_entry("p01.png", encoded(two_halves(), "PNG"), IMAGE_EXTS))
    question = split_and_question(st, callbacks)
    del st.images_data[0]
    names = page_names(st)
    question.answer(True)
    assert page_names(st) == names


def test_late_answer_does_nothing_on_a_replaced_document_or_during_an_operation():
    st, callbacks, _saves = document(create_entry("p01.png", encoded(two_halves(), "PNG"), IMAGE_EXTS))
    question = split_and_question(st, callbacks)
    st.begin_operation("opération")
    question.answer(True)
    assert page_names(st)[0] == "p01.png"
    st.end_operation("opération")
    st.doc_generation += 1
    question.answer(True)
    assert page_names(st)[0] == "p01.png"


def test_page_brought_back_by_undo_is_recognized():
    """Une page rétablie par Annuler est un nouvel objet, avec le même jeton."""
    entry = create_entry("p01.png", encoded(two_halves(), "PNG"), IMAGE_EXTS)
    st, callbacks, _saves = document(entry)
    question = split_and_question(st, callbacks)
    restored = dict(entry)
    adopt_entry_key(restored, entry_key(entry))
    st.images_data[0] = restored
    question.answer(True)
    assert page_names(st) == ["p01_part01.png", "p01_part02.png", "p02.png"]
