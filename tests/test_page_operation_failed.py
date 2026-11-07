"""Échec d'un recadrage ou d'un redressement : la fenêtre dit ce qui s'est passé.

Vérifié :
- page_operation_failed (dialogs_qt) : le message cite l'exception, ou le nom
  de la page illisible, et suit la langue ; le titre n'est jamais repris comme
  message ;
- perform_crop, perform_straighten, perform_auto_straighten : chaque échec
  passe par cette fenêtre avec la clé de son message ; en lecture de macro,
  l'échec reste sans fenêtre.

Ces modules importent PySide6 : fonctions extraites par AST
(tests/ast_loader.py), fenêtres remplacées.
"""
import sys
import types

import pytest

from modules.qt.state import AppState

import ast_loader

language = {"prefix": "fr"}


def translate(key, **params):
    return f"{language['prefix']}:{key}" + "".join(f"[{k}={v}]" for k, v in sorted(params.items()))


class FakeErrorDialog:
    shown = []

    def __init__(self, parent, title_key, message):
        FakeErrorDialog.shown.append((title_key, message))

    def show_nonmodal(self):
        pass


failed = ast_loader.load("dialogs_qt.py", {"page_operation_failed"}, sys=sys, _=translate,
                         ErrorDialog=FakeErrorDialog)["page_operation_failed"]


@pytest.fixture(autouse=True)
def clean_windows():
    FakeErrorDialog.shown = []
    language["prefix"] = "fr"


def test_failure_in_an_except_block_cites_the_exception():
    try:
        raise ValueError("cause précise")
    except ValueError:
        failed(None, "title.key", "message.key")
    [(title, message)] = FakeErrorDialog.shown
    assert title == "title.key"
    assert message() == "fr:message.key[error=cause précise]"


def test_exception_without_text_gives_its_type_name():
    try:
        raise MemoryError()
    except MemoryError:
        failed(None, "title.key", "message.key")
    assert FakeErrorDialog.shown[0][1]() == "fr:message.key[error=MemoryError]"


def test_unreadable_page_is_named():
    failed(None, "title.key", "message.key", {"orig_name": "p01.png"})
    text = FakeErrorDialog.shown[0][1]()
    assert text.startswith("fr:message.key[error=fr:messages.errors.batch_image_unreadable")
    assert "p01.png" in text


def test_message_follows_a_language_change():
    failed(None, "title.key", "message.key", {"orig_name": "p01.png"})
    message = FakeErrorDialog.shown[0][1]
    language["prefix"] = "en"
    assert message().startswith("en:message.key")


# --- Recadrage et redressement ------------------------------------------------

@pytest.fixture
def recorded(monkeypatch):
    """Appels à page_operation_failed : (titre, message, page, exception)."""
    calls = []
    module = types.ModuleType("modules.qt.dialogs_qt")
    module.MsgDialog = None

    def page_operation_failed(parent, title_key, message_key, entry=None):
        calls.append((title_key, message_key, entry, sys.exc_info()[1]))
    module.page_operation_failed = page_operation_failed
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", module)
    return calls


CropViewer = ast_loader.load_methods("crop_tool_qt.py", "CropViewerMixin", {"perform_crop"})
StraightenViewer = ast_loader.load_methods(
    "straighten_tool_qt.py", "StraightenViewerMixin",
    {"perform_straighten", "perform_auto_straighten"})


def make_viewer(cls, pages):
    state = AppState()
    state.images_data = pages
    viewer = cls()
    viewer.callbacks = {"state": state}
    viewer.current_idx = 0
    viewer._center_parent = None
    viewer._canvas = types.SimpleNamespace(_crop_start=None, _crop_end=None)
    viewer._toolbar = types.SimpleNamespace(_angle_panel=types.SimpleNamespace(pending_angle=5.0))
    viewer._refuse_with_pending_page_work = lambda **ignored: False
    viewer.display_image = lambda keep_crop_rect=False: None
    return viewer


def unreadable_page():
    return {"orig_name": "p01.png", "bytes": b"pas une image", "extension": ".png",
            "is_image": True, "is_corrupted": True}


CROP = ("messages.errors.crop_failed.title", "messages.errors.crop_failed.message")
STRAIGHTEN = ("messages.errors.straighten_failed.title", "messages.errors.straighten_failed.message")


def test_crop_of_an_unreadable_page_names_it(recorded):
    page = unreadable_page()
    assert make_viewer(CropViewer, [page]).perform_crop() is False
    assert recorded == [(*CROP, page, None)]


def test_crop_failure_cites_the_exception(recorded):
    assert not make_viewer(CropViewer, []).perform_crop()
    [(title, message, page, error)] = recorded
    assert (title, message, page) == (*CROP, None) and isinstance(error, IndexError)


def test_crop_failure_during_a_macro_shows_no_window(recorded):
    assert make_viewer(CropViewer, []).perform_crop(override_px=(0, 0, 1, 1)) is False
    assert make_viewer(CropViewer, [unreadable_page()]).perform_crop(override_px=(0, 0, 1, 1)) is False
    assert recorded == []


def test_straighten_of_an_unreadable_page_names_it(recorded):
    page = unreadable_page()
    assert make_viewer(StraightenViewer, [page]).perform_straighten() is False
    assert recorded == [(*STRAIGHTEN, page, None)]


def test_straighten_failure_cites_the_exception(recorded):
    assert not make_viewer(StraightenViewer, []).perform_straighten()
    [(title, message, page, error)] = recorded
    assert (title, message, page) == (*STRAIGHTEN, None) and isinstance(error, IndexError)


def test_straighten_failure_during_a_macro_shows_no_window(recorded):
    assert make_viewer(StraightenViewer, []).perform_straighten(override_angle=5.0) is False
    assert make_viewer(StraightenViewer, [unreadable_page()]).perform_straighten(override_angle=5.0) is False
    assert recorded == []


def test_auto_straighten_failure_cites_the_exception(recorded):
    assert make_viewer(StraightenViewer, []).perform_auto_straighten() is False
    [(title, message, page, error)] = recorded
    assert (title, message, page) == (*STRAIGHTEN, None) and isinstance(error, IndexError)


def test_auto_straighten_failure_during_a_macro_shows_no_window(recorded):
    assert make_viewer(StraightenViewer, []).perform_auto_straighten(skip_history=True) is False
    assert recorded == []
