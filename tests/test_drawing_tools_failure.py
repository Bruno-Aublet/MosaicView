"""Enregistrement impossible avec un outil de dessin de la visionneuse.

La fenêtre d'échec donne la cause (dialogs_qt.page_operation_failed, message de
l'outil avec {error}), l'image non écrite est retirée de la page, le travail non
validé reste pour réessayer, et une lecture de macro ne montre aucune fenêtre.

Les modules importent PySide6 : les méthodes sont extraites par AST
(tests/ast_loader.load_methods), l'interface est remplacée. Les messages sont lus
dans les vrais fichiers de langue.
"""
import glob
import io
import json
import os
import sys
import types

import pytest
from PIL import Image

import modules.qt.state  # noqa: F401  (chargé avant le remplacement des modules ci-dessous)

import ast_loader

TOOLS = [
    ("text_tool_qt.py", "TextViewerMixin", "perform_text", "text"),
    ("shapes_tool_qt.py", "ShapeViewerMixin", "perform_shapes", "shapes"),
    ("paste_image_tool_qt.py", "PasteImageViewerMixin", "perform_paste_image", "paste_image"),
]


def png():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def failures(monkeypatch):
    calls = []

    def failed_write(entry):
        raise OSError("disque plein")

    def forget(images_data, index):
        images_data[index]["img"] = None

    monkeypatch.setitem(sys.modules, "modules.qt.entries", types.SimpleNamespace(
        save_image_to_bytes=failed_write, forget_unsaved_page_image=forget,
        drawing_base_image=lambda data: Image.new("RGBA", (4, 4), "white"),
        drawn_page_image=lambda data, img: img))
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", types.SimpleNamespace(
        page_operation_failed=lambda parent, title, message, entry=None:
            calls.append((title, message, entry))))
    return calls


def viewer_for(module, class_name, method, entry):
    base = ast_loader.load_methods(module, class_name, {method}, Image=Image, QColor=None)

    class Viewer(base):
        def __init__(self):
            self.state = types.SimpleNamespace(images_data=[entry])
            self.callbacks = {"state": self.state}
            self.current_idx = 0
            self.displayed = 0
            self.rendered_work = []
            self._center_parent = None

        def display_image(self, keep_crop_rect=False):
            self.displayed += 1

        def _text_render_all_blocks(self, img):
            return img

        _shapes_render_all = _pasted_images_render_all = _text_render_all_blocks

    return Viewer()


@pytest.mark.parametrize("module, class_name, method, key", TOOLS)
def test_failed_save_gives_the_cause_and_forgets_the_image(failures, module, class_name, method, key):
    entry = {"bytes": png(), "extension": ".png"}
    viewer = viewer_for(module, class_name, method, entry)
    assert getattr(viewer, method)() is False
    assert failures == [(f"messages.errors.{key}_failed.title",
                         f"messages.errors.{key}_failed.message", None)]
    assert entry["img"] is None and viewer.displayed == 1


@pytest.mark.parametrize("module, class_name, method, key", TOOLS)
def test_page_without_data_is_named_in_the_message(failures, module, class_name, method, key):
    entry = {"bytes": None, "extension": ".png"}
    viewer = viewer_for(module, class_name, method, entry)
    assert getattr(viewer, method)() is False
    assert failures == [(f"messages.errors.{key}_failed.title",
                         f"messages.errors.{key}_failed.message", entry)]


@pytest.mark.parametrize("module, class_name, method, key", TOOLS)
def test_macro_reading_shows_no_window(failures, module, class_name, method, key):
    entry = {"bytes": png(), "extension": ".png"}
    viewer = viewer_for(module, class_name, method, entry)
    assert getattr(viewer, method)(skip_history=True) is False
    assert failures == [] and entry["img"] is None


def test_transparency_failure_gives_the_cause_and_keeps_the_page(failures):
    base = ast_loader.load_methods(
        "transparency_tool_qt.py", "TransparencyViewerMixin", {"perform_transparency"})
    entry = {"bytes": b"page", "extension": ".png"}

    class Viewer(base):
        def __init__(self):
            self.callbacks = {"state": types.SimpleNamespace(images_data=[entry])}
            self.current_idx = 0
            self.displayed = 0
            self._center_parent = None
            self._transp_work_img_by_page = {0: Image.new("RGBA", (4, 4))}

        def _drop_transparency_work_if_page_changed(self, idx):
            return False

        def display_image(self, keep_crop_rect=False):
            self.displayed += 1

    viewer = Viewer()
    assert viewer.perform_transparency() is False
    assert failures[0][1] == "messages.errors.transparency_failed.message"
    assert entry["img"] is None and entry["bytes"] == b"page"
    assert 0 in viewer._transp_work_img_by_page and viewer.displayed == 1


@pytest.mark.parametrize("key", ["blur_failed", "transparency_failed", "clone_failed",
                                 "text_failed", "shapes_failed", "paste_image_failed"])
def test_every_language_has_room_for_the_cause(key):
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "locales")
    for path in glob.glob(os.path.join(root, "*.json")):
        if os.path.basename(path) == "language_names.json":
            continue
        with open(path, encoding="utf-8") as f:
            message = json.load(f)["messages"]["errors"][key]["message"]
        assert "{error}" in message, os.path.basename(path)
