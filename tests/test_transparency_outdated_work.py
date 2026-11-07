"""Travail de transparence en attente sur une page modifiée entre-temps.

L'image de travail est une copie de la page au premier clic : la valider après
un changement (rotation depuis la mosaïque, autre outil…) l'effacerait. Le
travail est donc abandonné, sans message, dès que la page a changé, et la page
garde son changement. Sur une page inchangée, la validation écrit bien le travail.

transparency_tool_qt.py importe PySide6 : les méthodes sont extraites par AST
(tests/ast_loader.load_methods), l'interface est remplacée.
"""
import io
import sys
import types

import pytest
from PIL import Image

import modules.qt.state  # noqa: F401  (chargé avant le remplacement des modules ci-dessous)

import ast_loader

Base = ast_loader.load_methods(
    "transparency_tool_qt.py", "TransparencyViewerMixin",
    {"_get_or_init_transp_work_img", "_drop_transparency_work_if_page_changed",
     "_forget_outdated_transparency_work", "perform_transparency", "_clear_transparency_work"},
)


def png(color):
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buf, format="PNG")
    return buf.getvalue()


RED, BLUE, WRITTEN = png("red"), png("blue"), b"valide"


@pytest.fixture(autouse=True)
def fake_modules(monkeypatch):
    monkeypatch.setitem(sys.modules, "modules.qt.entries", types.SimpleNamespace(
        save_image_to_bytes=lambda entry: WRITTEN,
        forget_unsaved_page_image=lambda images_data, index: None,
        drawing_base_image=lambda data: Image.open(io.BytesIO(data)).convert("RGBA")))
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt",
                        types.SimpleNamespace(page_operation_failed=None))
    monkeypatch.setitem(sys.modules, "modules.qt.comic_info", types.SimpleNamespace(
        get_page_image_index=lambda state, entry: None,
        update_page_entries_in_xml_data=lambda state, pairs: None))


class Viewer(Base):
    def __init__(self):
        self.state = types.SimpleNamespace(
            images_data=[{"bytes": RED, "is_image": True, "orig_name": "a"},
                         {"bytes": BLUE, "is_image": True, "orig_name": "b"}],
            modified=False, comic_metadata=None)
        self.saved = []
        self.callbacks = {"state": self.state, "save_state": lambda force=False: self.saved.append(force)}
        self.current_idx = 0
        self._transp_work_img_by_page = {}
        self._transp_base_bytes_by_page = {}
        self._macro_transparency_clicks = []
        self._sharpness_preview_img = None
        self.displayed = 0
        self.buttons = 0
        self._canvas = types.SimpleNamespace(_update_validate_btn_state=self._count_button,
                                             _update_cancel_btn_state=self._count_button)
        self._toolbar = types.SimpleNamespace(refresh_undo_redo_state=lambda: None)

    def _count_button(self):
        self.buttons += 1

    def display_image(self, keep_crop_rect=False):
        self.displayed += 1

    def first_click(self):
        work = self._get_or_init_transp_work_img()
        self._sharpness_preview_img = work
        self._macro_transparency_clicks = [{"type": "zone"}]
        return work


def test_work_on_an_unchanged_page_is_kept_and_validated():
    viewer = Viewer()
    viewer.first_click()
    assert viewer._drop_transparency_work_if_page_changed(0) is False
    assert viewer.perform_transparency(skip_history=True) is True
    assert viewer.state.images_data[0]["bytes"] == WRITTEN
    assert viewer._transp_base_bytes_by_page == {}


def test_same_content_in_new_bytes_is_not_a_change():
    viewer = Viewer()
    viewer.first_click()
    viewer.state.images_data[0]["bytes"] = bytes(bytearray(RED))
    assert viewer._drop_transparency_work_if_page_changed(0) is False
    assert 0 in viewer._transp_work_img_by_page


def test_work_is_dropped_when_the_page_has_changed():
    viewer = Viewer()
    viewer.first_click()
    viewer.state.images_data[0]["bytes"] = BLUE
    assert viewer._drop_transparency_work_if_page_changed(0) is True
    assert viewer._transp_work_img_by_page == {} and viewer._transp_base_bytes_by_page == {}
    assert viewer._sharpness_preview_img is None
    assert viewer._macro_transparency_clicks == []
    assert viewer.buttons == 2                 # Valider et Annuler repassent en gris


def test_validation_after_a_change_keeps_the_page_and_writes_nothing():
    viewer = Viewer()
    viewer.first_click()
    viewer.state.images_data[0]["bytes"] = BLUE
    assert viewer.perform_transparency() is False
    assert viewer.state.images_data[0]["bytes"] == BLUE
    assert viewer.saved == [] and viewer.state.modified is False
    assert viewer.displayed == 1


def test_outdated_work_of_another_page_is_forgotten():
    viewer = Viewer()
    viewer.first_click()
    viewer.current_idx = 1
    kept_preview = object()
    viewer._sharpness_preview_img = kept_preview
    viewer.first_click()
    viewer._sharpness_preview_img = kept_preview
    viewer.state.images_data[0]["bytes"] = WRITTEN
    viewer._forget_outdated_transparency_work()
    assert 0 not in viewer._transp_work_img_by_page
    assert 1 in viewer._transp_work_img_by_page
    assert viewer._macro_transparency_clicks != []   # clics de la page affichée
    assert viewer._sharpness_preview_img is kept_preview


def test_cancelling_forgets_the_page_version_too():
    viewer = Viewer()
    viewer.first_click()
    viewer._clear_transparency_work()
    assert viewer._transp_work_img_by_page == {} and viewer._transp_base_bytes_by_page == {}


def test_page_version_is_noted_at_the_first_click_only():
    viewer = Viewer()
    viewer._get_or_init_transp_work_img()
    viewer.state.images_data[0]["bytes"] = BLUE
    viewer._get_or_init_transp_work_img()
    assert viewer._transp_base_bytes_by_page[0] == RED
