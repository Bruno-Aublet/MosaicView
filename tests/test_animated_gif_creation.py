"""Création d'un GIF animé à partir de pages
(animated_gif_dialog_qt.AnimatedGifDialog._create_animated_gif).

Vérifié sur le GIF produit, relu :
- une image par page, dans l'ordre, sur une toile à la taille de la plus
  grande, plus petites centrées sur fond blanc ;
- délai réglé appliqué à toutes les images ; GIF modifié : délai propre à
  chaque image gardé tant que le réglage n'a pas été changé ;
- transparence gardée si une page en a ;
- nombre de lectures : non écrit (jouée une fois) quand le GIF d'origine
  n'en avait pas et que le réglage n'a pas changé, écrit sinon ;
- commentaire écrit ; GIF ajouté en fin de document (Animated_1.gif,
  Animated_2.gif…), document modifié, point d'historique ;
- opération en cours ou aucune page : rien n'est créé.

animated_gif_dialog_qt.py importe PySide6 : la méthode est extraite par AST
(tests/ast_loader.load_methods), les réglages de la fenêtre sont remplacés.
"""
import io
import types

import pytest
from PIL import Image, ImageSequence

from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import create_entry, ensure_image_loaded, to_display_mode
from modules.qt.state import AppState

import ast_loader


class Messages:
    shown = []

    def __init__(self, parent, title_key, message_key, *args, **kwargs):
        Messages.shown.append(title_key)

    def show_nonmodal(self):
        pass


Base = ast_loader.load_methods(
    "animated_gif_dialog_qt.py", "AnimatedGifDialog", {"_create_animated_gif"},
    io=io, Image=Image, MsgDialog=Messages, _=lambda key, **kw: key, create_entry=create_entry,
    ensure_image_loaded=ensure_image_loaded, to_display_mode=to_display_mode,
    QApplication=types.SimpleNamespace(processEvents=lambda: None))


def _widget(**values):
    ns = types.SimpleNamespace(setEnabled=lambda v: None, setVisible=lambda v: None,
                               setText=lambda t: None)
    ns.__dict__.update(values)
    return ns


class GifWindow(Base):
    IMAGE_EXTS = IMAGE_EXTS

    def __init__(self, state, pages, delay=100, loop=0, loop_unset=False, default_delay=100,
                 comment=""):
        """loop_unset : GIF d'origine sans nombre de lectures (réglage proposé : 1)."""
        self._state, self._gif_images = state, pages
        self._delay_ms, self._loop_unset = default_delay, loop_unset
        self._loop = 1 if loop_unset else loop
        self._comment_edit = _widget(toPlainText=lambda: comment)
        self._delay_spin = _widget(value=lambda: delay)
        self._loop_spin = _widget(value=lambda: loop)
        self._disposal_radios = [_widget(isChecked=lambda: True, _disposal_val=2)]
        self._optimize_check = _widget(isChecked=lambda: True)
        self._create_btn = self._cancel_btn = self._progress_lbl = _widget()
        self.saves = []
        self._callbacks = {"save_state": lambda: self.saves.append(True)}
        self.closed = False

    def _free_gif_images(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    """Messages remis à zéro ; fenêtre d'erreur de la création remplacée
    (importée par la méthode en cas d'échec)."""
    import sys
    Messages.shown = []
    module = types.ModuleType("modules.qt.dialogs_qt")
    module.MsgDialog = Messages
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", module)


def page(name, size, color, mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, "PNG")
    return create_entry(name, buf.getvalue(), IMAGE_EXTS)


def created(state):
    entry = state.images_data[-1]
    return entry, Image.open(io.BytesIO(entry["bytes"]))


def frames_of(gif):
    return [frame.convert("RGBA").copy() for frame in ImageSequence.Iterator(gif)]


def test_one_frame_per_page_centered_on_white():
    st = AppState()
    pages = [page("a.png", (40, 30), (255, 0, 0)), page("b.png", (20, 10), (0, 0, 255))]
    window = GifWindow(st, pages, delay=150)
    window._create_animated_gif()
    entry, gif = created(st)
    assert entry["orig_name"] == "Animated_1.gif" and entry["is_animated_gif"]
    assert gif.size == (40, 30) and gif.n_frames == 2
    first, second = frames_of(gif)
    assert first.getpixel((5, 5))[:3] == (255, 0, 0)
    assert second.getpixel((20, 15))[:3] == (0, 0, 255)      # petite image centrée
    assert second.getpixel((2, 2))[:3] == (255, 255, 255)    # fond blanc autour
    assert entry["gif_durations"] == [150, 150]
    assert st.modified is True and window.saves == [True] and window.closed


def test_frames_of_an_edited_gif_keep_their_own_delays():
    st = AppState()
    pages = [page("a.png", (8, 8), (255, 0, 0)), page("b.png", (8, 8), (0, 255, 0))]
    pages[0]["gif_duration"], pages[1]["gif_duration"] = 80, 300
    GifWindow(st, pages, delay=100, default_delay=100)._create_animated_gif()
    assert created(st)[0]["gif_durations"] == [80, 300]


def test_changed_delay_applies_to_every_frame():
    st = AppState()
    pages = [page("a.png", (8, 8), (255, 0, 0)), page("b.png", (8, 8), (0, 255, 0))]
    pages[0]["gif_duration"], pages[1]["gif_duration"] = 80, 300
    GifWindow(st, pages, delay=200, default_delay=100)._create_animated_gif()
    assert created(st)[0]["gif_durations"] == [200, 200]


def test_transparency_is_kept():
    st = AppState()
    pages = [page("a.png", (8, 8), (255, 0, 0, 0), mode="RGBA"),
             page("b.png", (8, 8), (0, 0, 255, 255), mode="RGBA")]
    GifWindow(st, pages)._create_animated_gif()
    first, second = frames_of(created(st)[1])
    assert first.getpixel((4, 4))[3] == 0                     # transparente
    assert second.getpixel((4, 4)) == (0, 0, 255, 255)


def test_gif_played_once_stays_played_once():
    st = AppState()
    pages = [page("a.png", (8, 8), (255, 0, 0)), page("b.png", (8, 8), (0, 255, 0))]
    GifWindow(st, pages, loop=1, loop_unset=True)._create_animated_gif()
    assert "loop" not in created(st)[1].info


def test_loop_setting_is_written():
    st = AppState()
    pages = [page("a.png", (8, 8), (255, 0, 0)), page("b.png", (8, 8), (0, 255, 0))]
    GifWindow(st, pages, loop=0)._create_animated_gif()
    assert created(st)[1].info.get("loop") == 0


def test_comment_is_written_and_names_follow_each_other():
    st = AppState()
    pages = [page("a.png", (8, 8), (255, 0, 0)), page("b.png", (8, 8), (0, 255, 0))]
    GifWindow(st, pages, comment="Aaaa")._create_animated_gif()
    GifWindow(st, pages)._create_animated_gif()
    assert [e["orig_name"] for e in st.images_data] == ["Animated_1.gif", "Animated_2.gif"]
    assert Image.open(io.BytesIO(st.images_data[0]["bytes"])).info.get("comment") == b"Aaaa"


def test_nothing_is_created_during_an_operation_or_without_pages():
    st = AppState()
    token = object()
    st.begin_operation(token)
    GifWindow(st, [page("a.png", (8, 8), (255, 0, 0))])._create_animated_gif()
    assert st.images_data == []
    st.end_operation(token)
    window = GifWindow(st, [])
    window._create_animated_gif()
    assert st.images_data == [] and len(Messages.shown) == 1 and not window.closed
