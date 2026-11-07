"""Création d'un GIF animé à partir de pages (gif_writer.build_gif, réglages lus
par animated_gif_dialog_qt.AnimatedGifDialog._gif_settings, ajout au document
par _add_created_gif).

Vérifié sur le GIF produit, relu :
- une image par page, dans l'ordre, sur une toile à la taille de la plus
  grande, plus petites centrées sur fond blanc ;
- délai propre de chaque image écrit, 0 compris ; réglage commun pour les images
  qui n'en ont pas ;
- couleurs d'un GIF modifié sans transparence identiques à l'original ;
- transparence gardée si une page en a ;
- nombre de lectures : non écrit (jouée une fois) quand le GIF d'origine
  n'en avait pas et que le réglage n'a pas changé, écrit sinon ;
- commentaire écrit, relu même en codage Windows ; GIF ajouté en fin de document
  (nom suivant le plus grand numéro), document modifié, entre deux états
  d'historique ;
- page illisible ou à plusieurs images annoncée ; arrêt à la demande ;
- opération en cours ou aucune page : rien n'est créé.

animated_gif_dialog_qt.py importe PySide6 : ses méthodes sont extraites par AST
(tests/ast_loader.load_methods), les réglages de la fenêtre sont remplacés.
"""
import io
import os
import types

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageSequence

from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import create_entry
from modules.qt.gif_writer import GifCancelled, build_gif, next_gif_name, pages_left_out
from modules.qt.state import AppState

import ast_loader


class Messages:
    shown = []

    def __init__(self, parent, title_key, message_key, *args, **kwargs):
        Messages.shown.append(title_key)
        self.kwargs = kwargs

    def show_nonmodal(self):
        pass

    def ask_async(self, on_result):
        Messages.answer = on_result


Base = ast_loader.load_methods(
    "animated_gif_dialog_qt.py", "AnimatedGifDialog",
    {"_gif_settings", "_add_created_gif", "_create_animated_gif"},
    MsgDialog=Messages, ConfirmDialog=Messages, pages_left_out=pages_left_out,
    _LEFT_OUT_LISTED=10)

extract_gif_frames = ast_loader.load("animated_gif_dialog_qt.py", {"_extract_gif_frames"},
                                     io=io, os=os, Image=Image)["_extract_gif_frames"]


def _widget(**values):
    ns = types.SimpleNamespace()
    ns.__dict__.update(values)
    return ns


class GifWindow(Base):
    def __init__(self, state, pages, delay=100, loop=0, loop_unset=False, comment=""):
        """loop_unset : GIF d'origine sans nombre de lectures (réglage proposé : 1)."""
        self._state, self._gif_images = state, pages
        self._loop_unset = loop_unset
        self._loop = 1 if loop_unset else loop
        self._worker = None
        self._comment_edit = _widget(toPlainText=lambda: comment)
        self._delay_spin = _widget(value=lambda: delay)
        self._loop_spin = _widget(value=lambda: loop)
        self._disposal_radios = [_widget(isChecked=lambda: True, _disposal_val=2)]
        self._optimize_check = _widget(isChecked=lambda: True)
        self.saves = []
        self._callbacks = {"save_state": lambda: self.saves.append(len(state.images_data))}
        self.started = 0

    def _refresh_from_pages(self):
        pass

    def _start_creation(self):
        self.started += 1

    def create(self):
        """Ce que fait le thread de création, puis la fin sur le thread Qt."""
        data, left_out = build_gif(self._gif_images, **self._gif_settings())
        entry = create_entry(next_gif_name(self._state.images_data), data, IMAGE_EXTS)
        entry["source_archive"] = "loose"
        self._add_created_gif(entry)
        return left_out


@pytest.fixture(autouse=True)
def reset():
    Messages.shown = []


def page(name, size, color, mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, "PNG")
    return create_entry(name, buf.getvalue(), IMAGE_EXTS)


def two_pages():
    return [page("a.png", (8, 8), (255, 0, 0)), page("b.png", (8, 8), (0, 255, 0))]


def created(state):
    entry = state.images_data[-1]
    return entry, Image.open(io.BytesIO(entry["bytes"]))


def frames_of(gif):
    return [frame.convert("RGBA").copy() for frame in ImageSequence.Iterator(gif)]


def test_one_frame_per_page_centered_on_white():
    st = AppState()
    pages = [page("a.png", (40, 30), (255, 0, 0)), page("b.png", (20, 10), (0, 0, 255))]
    window = GifWindow(st, pages, delay=150)
    window.create()
    entry, gif = created(st)
    assert entry["orig_name"] == "Animated_1.gif" and entry["is_animated_gif"]
    assert gif.size == (40, 30) and gif.n_frames == 2
    first, second = frames_of(gif)
    assert first.getpixel((5, 5))[:3] == (255, 0, 0)
    assert second.getpixel((20, 15))[:3] == (0, 0, 255)      # petite image centrée
    assert second.getpixel((2, 2))[:3] == (255, 255, 255)    # fond blanc autour
    assert entry["gif_durations"] == [150, 150]
    assert st.modified is True
    assert window.saves == [0, 1]                            # avant et après l'ajout


def test_frames_of_an_edited_gif_keep_their_own_delays():
    st = AppState()
    pages = two_pages()
    pages[0]["gif_duration"], pages[1]["gif_duration"] = 80, 300
    GifWindow(st, pages, delay=100).create()
    assert created(st)[0]["gif_durations"] == [80, 300]


def test_a_frame_with_a_zero_delay_keeps_it():
    st = AppState()
    pages = two_pages() + [page("c.png", (8, 8), (0, 0, 255))]
    for p, delay in zip(pages, (500, 0, 500)):
        p["gif_duration"] = delay
    GifWindow(st, pages, delay=333).create()
    assert created(st)[0]["gif_durations"] == [500, 0, 500]


def test_a_frame_without_its_own_delay_takes_the_common_one():
    st = AppState()
    pages = two_pages()
    pages[1]["gif_duration"] = 300
    GifWindow(st, pages, delay=200).create()
    assert created(st)[0]["gif_durations"] == [200, 300]


def test_edited_gif_without_transparency_keeps_its_colors_exactly():
    """Sans rien changer : écart nul. En RGBA, il atteignait 23 sur 255."""
    images = []
    for shift in range(3):
        im = Image.new("RGB", (64, 64))
        draw = ImageDraw.Draw(im)
        for x in range(0, 64, 4):
            for y in range(0, 64, 4):
                draw.rectangle((x, y, x + 3, y + 3), fill=(x * 4, y * 4, (x + y + shift * 30) % 256))
        images.append(im)
    buf = io.BytesIO()
    images[0].save(buf, "GIF", save_all=True, append_images=images[1:], duration=100, loop=0)
    source = create_entry("a.gif", buf.getvalue(), IMAGE_EXTS)
    frames, _stopped = extract_gif_frames(source)
    st = AppState()
    GifWindow(st, frames).create()
    for before, after in zip(frames_of(Image.open(io.BytesIO(source["bytes"]))), frames_of(created(st)[1])):
        assert ImageChops.difference(before.convert("RGB"), after.convert("RGB")).getbbox() is None


def test_transparency_is_kept():
    st = AppState()
    pages = [page("a.png", (8, 8), (255, 0, 0, 0), mode="RGBA"),
             page("b.png", (8, 8), (0, 0, 255, 255), mode="RGBA")]
    GifWindow(st, pages).create()
    first, second = frames_of(created(st)[1])
    assert first.getpixel((4, 4))[3] == 0                     # transparente
    assert second.getpixel((4, 4)) == (0, 0, 255, 255)


def test_gif_played_once_stays_played_once():
    st = AppState()
    GifWindow(st, two_pages(), loop=1, loop_unset=True).create()
    assert "loop" not in created(st)[1].info


def test_loop_setting_is_written():
    st = AppState()
    GifWindow(st, two_pages(), loop=0).create()
    assert created(st)[1].info.get("loop") == 0


def test_comment_is_written_and_names_follow_each_other():
    st = AppState()
    pages = two_pages()
    GifWindow(st, pages, comment="Aaaa").create()
    GifWindow(st, pages).create()
    assert [e["orig_name"] for e in st.images_data] == ["Animated_1.gif", "Animated_2.gif"]
    assert Image.open(io.BytesIO(st.images_data[0]["bytes"])).info.get("comment") == b"Aaaa"


def test_name_is_never_one_already_taken():
    """Après la suppression de Animated_1.gif, un simple comptage redonnait
    Animated_2.gif, déjà présent."""
    st = AppState()
    pages = two_pages()
    GifWindow(st, pages).create()
    GifWindow(st, pages).create()
    del st.images_data[0]
    GifWindow(st, pages).create()
    assert [e["orig_name"] for e in st.images_data] == ["Animated_2.gif", "Animated_3.gif"]


def test_comment_in_windows_encoding_is_read_correctly():
    buf = io.BytesIO()
    frames = [Image.new("RGB", (8, 8), (255, 0, 0)), Image.new("RGB", (8, 8), (0, 255, 0))]
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100,
                   comment="caf\xe9 €".encode("cp1252"))
    assert create_entry("a.gif", buf.getvalue(), IMAGE_EXTS)["gif_comment"] == "caf\xe9 €"


def test_unreadable_page_is_announced_before_creating():
    st = AppState()
    bad = create_entry("x.jpg", b"Aaaa", IMAGE_EXTS)
    window = GifWindow(st, [page("a.png", (8, 8), (255, 0, 0)), bad])
    window._create_animated_gif()
    assert Messages.shown == ["dialogs.gif_animated.left_out_title"] and window.started == 0
    Messages.answer(False)
    assert window.started == 0
    Messages.answer(True)
    assert window.started == 1


def test_page_with_several_images_is_announced():
    buf = io.BytesIO()
    frames = [Image.new("RGB", (8, 8), (255, 0, 0)), Image.new("RGB", (8, 8), (0, 255, 0))]
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    animated = create_entry("b.gif", buf.getvalue(), IMAGE_EXTS)
    plain = page("a.png", (8, 8), (255, 0, 0))
    assert pages_left_out([plain, animated]) == ([], [animated])


def test_healthy_pages_start_at_once():
    window = GifWindow(AppState(), two_pages())
    window._create_animated_gif()
    assert window.started == 1 and Messages.shown == []


def test_page_that_fails_to_decode_is_left_out_with_the_right_delays():
    """Lisible à l'en-tête, illisible au décodage : recommencé sans elle, sans
    décaler les délais des suivantes."""
    good = two_pages()
    buf = io.BytesIO()
    Image.effect_noise((64, 64), 50).save(buf, "PNG")
    broken = {"orig_name": "x.png", "bytes": buf.getvalue()[:len(buf.getvalue()) // 2], "is_corrupted": False}
    for p, delay in zip((good[0], broken, good[1]), (100, 200, 300)):
        p["gif_duration"] = delay
    data, left_out = build_gif([good[0], broken, good[1]], 50, 0, 2, True, "")
    assert left_out == [broken]
    entry = create_entry("a.gif", data, IMAGE_EXTS)
    assert entry["gif_frame_count"] == 2 and entry["gif_durations"] == [100, 300]


def test_no_readable_page_gives_nothing():
    bad = create_entry("x.jpg", b"Aaaa", IMAGE_EXTS)
    assert build_gif([bad], 100, 0, 2, True, "") == (None, [bad])


def test_stop_requested_during_creation():
    asked = []

    def stop():
        asked.append(True)
        return len(asked) > 3

    with pytest.raises(GifCancelled):
        build_gif(two_pages(), 100, 0, 2, True, "", should_stop=stop)


def test_pages_are_not_modified_and_steps_are_reported():
    """La fenêtre lit ces pages pendant que le thread travaille."""
    pages = two_pages()
    before = [dict(p) for p in pages]
    steps = []
    build_gif(pages, 100, 0, 2, True, "", on_step=lambda *a: steps.append(a))
    assert pages == before
    assert ("frame", 2, 2) in steps and steps[-1][0] == "saving"


def test_nothing_is_created_during_an_operation_or_without_pages():
    st = AppState()
    token = object()
    st.begin_operation(token)
    window = GifWindow(st, [page("a.png", (8, 8), (255, 0, 0))])
    window._create_animated_gif()
    assert window.started == 0
    st.end_operation(token)
    window = GifWindow(st, [])
    window._create_animated_gif()
    assert window.started == 0 and len(Messages.shown) == 1
