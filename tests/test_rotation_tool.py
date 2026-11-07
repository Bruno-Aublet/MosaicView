"""Rotation et miroir de la page affichée dans la visionneuse.

Refusés, avec un message, tant que la page a du travail non validé, qui ne
suivrait pas la page (la transparence, abandonnée quand la page change, n'en
fait pas partie) ; jamais refusés en lecture de macro. Si l'écriture échoue, la
fenêtre donne la cause et la page ne garde pas l'image non écrite.

rotation_tool_qt.py importe PySide6 : les méthodes sont extraites par AST
(tests/ast_loader.load_methods), l'interface est remplacée.
"""
import io
import sys
import types

import pytest
from PIL import Image

import modules.qt.image_ops as image_ops

import ast_loader

Base = ast_loader.load_methods(
    "rotation_tool_qt.py", "RotationViewerMixin",
    {"perform_rotate", "perform_flip", "_refuse_with_pending_page_work",
     "_current_page_has_pending_work", "_commit_rotation_op"},
)


class Dialog:
    shown = []

    def __init__(self, parent, title, message):
        self.keys = (title, message)

    def show_nonmodal(self):
        Dialog.shown.append(self.keys)


@pytest.fixture
def tool_modules(monkeypatch):
    failures = []
    Dialog.shown = []

    def write(entry, *args):
        return entry.setdefault("result", True)

    monkeypatch.setitem(sys.modules, "modules.qt.image_ops", types.SimpleNamespace(
        rotate_entry_data=write, flip_entry_data=write))
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", types.SimpleNamespace(
        MsgDialog=Dialog,
        page_operation_failed=lambda parent, title, message, entry=None:
            failures.append((message, entry))))
    return failures


class Viewer(Base):
    def __init__(self, entry=None, **work):
        canvas = dict(has_crop=False, _crop_start=None, has_line=False, _line_img_start=None,
                      has_text_blocks=False, has_shapes=False, has_pasted_images=False)
        canvas.update(work)
        self._canvas = types.SimpleNamespace(**canvas)
        self.entry = entry if entry is not None else {"bytes": b"page"}
        self.state = types.SimpleNamespace(images_data=[self.entry], modified=False)
        self.callbacks = {"state": self.state}
        self.current_idx = 0
        self._center_parent = None
        self.displayed = 0
        self.steps = []
        self._toolbar = types.SimpleNamespace(refresh_undo_redo_state=lambda: None)

    def display_image(self, keep_crop_rect=False):
        self.displayed += 1

    def _macro_record_step(self, *args):
        self.steps.append(args[0])


WORK = [{"has_crop": True}, {"_crop_start": object()}, {"has_line": True},
        {"_line_img_start": (1, 1)}, {"has_text_blocks": True}, {"has_shapes": True},
        {"has_pasted_images": True}]


@pytest.mark.parametrize("work", WORK)
def test_rotation_and_mirror_are_refused_with_pending_work(tool_modules, work):
    viewer = Viewer(**work)
    assert viewer.perform_rotate(90) is False
    assert viewer.perform_flip("horizontal") is False
    assert "result" not in viewer.entry and viewer.steps == []
    assert Dialog.shown == [("viewer.unvalidated_work_title",
                             "viewer.rotation_unvalidated_work_message")] * 2


def test_straightening_ignores_its_own_line_but_not_the_other_work(tool_modules):
    assert Viewer(has_line=True, _line_img_start=(1, 1))._refuse_with_pending_page_work(ignore_line=True) is False
    assert Dialog.shown == []
    assert Viewer(has_shapes=True)._refuse_with_pending_page_work(ignore_line=True) is True


def test_crop_ignores_its_own_rectangle_but_not_the_other_work(tool_modules):
    own = Viewer(has_crop=True, _crop_start=object())
    assert own._refuse_with_pending_page_work(ignore_crop=True) is False
    assert Dialog.shown == []
    for work in ({"has_shapes": True}, {"has_text_blocks": True}, {"has_pasted_images": True},
                 {"has_line": True}, {"_line_img_start": (1, 1)}):
        assert Viewer(**work)._refuse_with_pending_page_work(ignore_crop=True) is True


CropBase = ast_loader.load_methods("crop_tool_qt.py", "CropViewerMixin", {"perform_crop"})


class Cropper(CropBase):
    def __init__(self):
        self.calls = []
        self.callbacks = {"state": types.SimpleNamespace(images_data=[])}
        self.current_idx = 0
        self._center_parent = None
        self._canvas = types.SimpleNamespace(_crop_start=None, _crop_end=None)

    def _refuse_with_pending_page_work(self, ignore_crop=False):
        self.calls.append(ignore_crop)
        return True


def test_crop_is_refused_with_other_pending_work(tool_modules):
    viewer = Cropper()
    assert viewer.perform_crop() is False
    assert viewer.calls == [True] and tool_modules == []


def test_macro_crop_is_not_refused(tool_modules):
    viewer = Cropper()
    assert viewer.perform_crop(override_px=(0, 0, 1, 1)) is False   # document vide, sans fenêtre
    assert viewer.calls == [] and tool_modules == []


StraightenBase = ast_loader.load_methods(
    "straighten_tool_qt.py", "StraightenViewerMixin", {"perform_straighten", "perform_auto_straighten"})


class Straightener(StraightenBase):
    def __init__(self):
        self.calls = []
        self.callbacks = {"state": types.SimpleNamespace(images_data=[])}
        self.current_idx = 0
        self._center_parent = None
        self._toolbar = types.SimpleNamespace(_angle_panel=types.SimpleNamespace(pending_angle=5.0))

    def _refuse_with_pending_page_work(self, ignore_line=False):
        self.calls.append(ignore_line)
        return True


def test_manual_and_automatic_straightening_are_refused_with_pending_work(tool_modules):
    viewer = Straightener()
    assert viewer.perform_straighten() is False
    assert viewer.perform_auto_straighten() is False
    assert viewer.calls == [True, True]


def test_macro_straightening_is_not_refused(tool_modules):
    viewer = Straightener()
    viewer.display_image = lambda keep_crop_rect=False: None
    viewer.perform_straighten(skip_history=True, override_angle=5.0)
    viewer.perform_auto_straighten(skip_history=True)
    assert viewer.calls == []


def test_rotation_and_mirror_apply_without_pending_work(tool_modules):
    viewer = Viewer()
    assert viewer.perform_rotate(90) is True
    assert viewer.perform_flip("vertical") is True
    assert viewer.steps == ["rotate", "flip"] and Dialog.shown == []


@pytest.mark.parametrize("operate", [lambda v: v.perform_rotate(90),
                                     lambda v: v.perform_flip("horizontal")],
                         ids=["rotation", "miroir"])
def test_no_identical_history_step_is_created_before_the_operation(tool_modules, operate):
    viewer = Viewer()
    calls = []
    viewer.callbacks["save_state"] = lambda force=False: calls.append(force)
    assert operate(viewer) is True
    assert calls == [False, True]


def test_macro_reading_is_not_refused(tool_modules):
    viewer = Viewer(has_shapes=True)
    assert viewer.perform_rotate(90, skip_history=True) is True
    assert Dialog.shown == []


def test_unreadable_page_is_named_in_the_message(tool_modules):
    entry = {"bytes": b"page", "result": False}
    assert Viewer(entry).perform_rotate(90) is False
    assert tool_modules == [("messages.errors.rotation_failed.message", entry)]


def test_failed_write_gives_the_cause_and_shows_the_page_again(tool_modules, monkeypatch):
    def fail(entry, *args):
        raise OSError("disque plein")

    monkeypatch.setitem(sys.modules, "modules.qt.image_ops", types.SimpleNamespace(
        rotate_entry_data=fail, flip_entry_data=fail))
    viewer = Viewer()
    assert viewer.perform_flip("horizontal") is False
    assert tool_modules == [("messages.errors.rotation_failed.message", None)]
    assert viewer.displayed == 1


def test_macro_reading_shows_no_failure_window(tool_modules):
    assert Viewer({"bytes": b"page", "result": False}).perform_rotate(90, skip_history=True) is False
    assert tool_modules == []


@pytest.mark.parametrize("operation", [
    lambda entry: image_ops.rotate_entry_data(entry, 90),
    lambda entry: image_ops.flip_entry_data(entry, "horizontal"),
], ids=["rotation", "miroir"])
def test_failed_write_leaves_no_unsaved_image_in_the_page(monkeypatch, operation):
    buf = io.BytesIO()
    Image.new("RGB", (4, 2), "white").save(buf, format="PNG")
    entry = {"bytes": buf.getvalue(), "is_image": True, "extension": ".png", "orig_name": "a.png"}

    def fail(page):
        raise OSError("disque plein")

    monkeypatch.setattr(image_ops, "save_image_to_bytes", fail)
    with pytest.raises(OSError):
        operation(entry)
    assert entry["img"] is None and entry["bytes"] == buf.getvalue()
