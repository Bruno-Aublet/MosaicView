"""Une action = un seul Annuler : l'état d'avant, déjà au sommet de l'historique,
n'y est pas recopié. Forcé, il donnait un second Annuler sans aucun effet.

Vérifié sur les commandes qui posent un état avant d'agir : jointure de pages,
suppression depuis la fenêtre des doublons, remplacement d'une image abîmée,
fichier annexe modifié dans un autre programme. Dans chaque cas, l'historique
contient l'état de départ puis celui de l'action, et rien d'autre.

Ces modules importent PySide6 : les fonctions sont extraites par AST
(tests/ast_loader.py).
"""
import io
import os
import types

from PIL import Image

from modules.qt.state import AppState
from modules.qt.undo_redo import save_state_data

import ast_loader


def page(name):
    return {"orig_name": name, "bytes": name.encode(), "extension": ".jpg", "is_image": True}


def document(*entries):
    state = AppState()
    state.images_data = list(entries)
    state.current_file = "Aaaa.cbz"
    save_state_data(state, force=True)
    return state


def assert_single_step(state, before_names):
    assert len(state.history) == 2
    assert state.history_index == 1
    assert [e["orig_name"] for e in state.history[0]["entries"]] == before_names


# --- Jointure ------------------------------------------------------------------

class YesNoCancel:
    YES, NO, CANCEL = "yes", "no", "cancel"
    answer = []

    def __init__(self, parent, title, message):
        pass

    def ask_async(self, on_result):
        YesNoCancel.answer.append(on_result)


class Merge(ast_loader.load_methods(
        "merge_dialog_qt.py", "MergeDialog", {"_ask_delete_sources"},
        YesNoCancelDialog=YesNoCancel, _save_state_data=save_state_data)):

    def __init__(self, state, sources):
        self._state, self._source_entries, self._doc_generation = state, sources, state.doc_generation
        self._callbacks = {"render_mosaic": lambda: None, "update_button_text": lambda: None,
                           "clear_selection": lambda: None,
                           "renumber_no_save": lambda on_done: on_done()}


def test_join_needs_one_undo():
    state = document(page("01.jpg"), page("02.jpg"), page("03.jpg"))
    state.renumber_mode = 0
    Merge(state, state.images_data[:2])._ask_delete_sources(None, page("Collage_001.jpg"))
    YesNoCancel.answer[-1]("yes")
    assert [e["orig_name"] for e in state.images_data] == ["Collage_001.jpg", "03.jpg"]
    assert_single_step(state, ["01.jpg", "02.jpg", "03.jpg"])


# --- Doublons ------------------------------------------------------------------

def test_deleting_from_the_duplicates_window_needs_one_undo():
    delete = ast_loader.load("duplicate_detection_qt.py", {"delete_entries_by_index"})["delete_entries_by_index"]
    state = document(page("01.jpg"), page("02.jpg"))
    delete(state, [1], lambda force=False: save_state_data(state, force=force), lambda: None)
    assert [e["orig_name"] for e in state.images_data] == ["01.jpg"]
    assert_single_step(state, ["01.jpg", "02.jpg"])


# --- Image abîmée --------------------------------------------------------------

def test_replacing_a_damaged_image_needs_one_undo(tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2)).save(buffer, "PNG")
    replacement = tmp_path / "replacement.png"
    replacement.write_bytes(buffer.getvalue())

    # Fenêtre de choix : répond tout de suite par le fichier de remplacement
    class Panel(ast_loader.load_methods(
            "panel_widget.py", "PanelWidget",
            {"_replace_corrupted_image", "_replace_corrupted_image_with"},
            os=os, get_config_manager=None,
            ask_open_file=lambda parent, title, start, filters, on_result: on_result(str(replacement)),
            file_filter_text=lambda key, *patterns: None,
            _save_state_qt=lambda st, refresh, force=False: save_state_data(st, force=force))):
        _refresh_toolbar_states = staticmethod(lambda: None)
        _canvas = types.SimpleNamespace(render_mosaic=lambda: None)

    damaged = page("01.jpg")
    damaged.update(bytes=None, is_corrupted=True)
    panel = Panel()
    panel._state = document(damaged)
    panel._replace_corrupted_image(0)
    assert panel._state.images_data[0]["bytes"] == buffer.getvalue()
    assert_single_step(panel._state, ["01.jpg"])
    assert panel._state.history[0]["entries"][0]["bytes"] is None


# --- Fichier annexe ------------------------------------------------------------

def test_annex_modified_in_another_program_needs_one_undo():
    class Panel(ast_loader.load_methods("panel_widget.py", "PanelWidget",
                                        {"_apply_non_image_modification"})):
        _refresh_title = staticmethod(lambda: None)
        _update_status_bar = staticmethod(lambda: None)

        def save_state(self, force=False):
            save_state_data(self._state, force=force)

    annex = {"orig_name": "a.txt", "bytes": b"avant", "extension": ".txt", "is_image": False}
    panel = Panel()
    panel._state = document(annex)
    panel._apply_non_image_modification(annex, b"apres")
    assert annex["bytes"] == b"apres"
    assert_single_step(panel._state, ["a.txt"])
    assert panel._state.history[0]["entries"][0]["bytes"] == b"avant"
