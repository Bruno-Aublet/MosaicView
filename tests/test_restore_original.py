"""« Restaurer l'original » des outils Effets, Profondeur de couleur et Mode
d'image de la visionneuse (perform_restore_effect, perform_restore_color_depth,
perform_restore_image_mode).

Chaque outil capture les octets d'une page avant son premier changement sur
cette page (state.*_original_bytes_by_page, rangé par page_key, jamais par
position). Vérifié pour les trois :
- la page retrouve les octets d'avant le premier changement, même après
  plusieurs changements ;
- c'est une nouvelle étape de l'historique, pas un Annuler : Annuler revient
  à la version modifiée ;
- page changée depuis le dernier passage de l'outil (rotation…) : la
  restauration n'est plus proposée, elle effacerait ce changement ; Annuler
  jusqu'au résultat de l'outil la rend de nouveau possible, et un nouveau
  passage de l'outil repart de la page telle qu'elle est ;
- l'original est oublié ensuite (une seconde restauration ne fait rien) ;
- la restauration vise la bonne page après l'insertion d'une page avant elle ;
- lecture de macro (skip_history) : aucune étape d'historique ;
- sans original capturé : rien ne change.

Les mixins de la visionneuse importent PySide6 : les méthodes sont extraites
par AST (tests/ast_loader.load_methods), l'objet visionneuse est remplacé.
"""
import sys
import types

import pytest

from modules.qt.state import AppState
from modules.qt.undo_redo import save_state_data, undo_data

import ast_loader

TOOLS = [
    # (module, classe, méthode de restauration, originaux, méthode de synchronisation)
    ("effects_tool_qt.py", "EffectsViewerMixin", "perform_restore_effect",
     "effect_original_bytes_by_page", "_sync_effects_panel"),
    ("color_depth_tool_qt.py", "ColorDepthViewerMixin", "perform_restore_color_depth",
     "color_depth_original_bytes_by_page", "_sync_color_depth_panel"),
    ("image_mode_tool_qt.py", "ImageModeViewerMixin", "perform_restore_image_mode",
     "image_mode_original_bytes_by_page", "_sync_image_mode_panel"),
]


@pytest.fixture(autouse=True)
def fake_dialogs(monkeypatch):
    module = types.ModuleType("modules.qt.dialogs_qt")
    module.MsgDialog = lambda *a, **k: types.SimpleNamespace(show_nonmodal=lambda: None)
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", module)


def page(name, data):
    return {"orig_name": name, "bytes": data, "extension": ".png", "is_image": True}


def make_viewer(tool, state, index):
    module, cls_name, restore, _originals, sync = tool
    base = ast_loader.load_methods(module, cls_name, {restore})

    class Viewer(base):
        def __init__(self):
            def save_state(force=False):
                save_state_data(state, force=force)
            self.callbacks = {"state": state, "save_state": save_state}
            self.current_idx = index
            self._center_parent = None
            self._toolbar = types.SimpleNamespace(refresh_undo_redo_state=lambda: None)
            self.recorded = []

        def display_image(self, keep_crop_rect=False):
            pass

        def _macro_record_step(self, *args):
            self.recorded.append(args[0])

    setattr(Viewer, sync, lambda self: None)
    viewer = Viewer()
    return viewer, getattr(viewer, restore)


def edited_document(tool):
    """Document de deux pages ; la seconde modifiée deux fois par l'outil,
    son original capturé avant la première modification."""
    originals = tool[3]
    st = AppState()
    st.images_data = [page("p01.png", b"P1"), page("p02.png", b"ORIGINAL")]
    save_state_data(st, force=True)
    getattr(st, originals)[st.page_key(1)] = st.images_data[1]["bytes"]
    for version in (b"EDIT 1", b"EDIT 2"):
        st.images_data[1]["bytes"] = version
        save_state_data(st, force=True)
    return st


@pytest.mark.parametrize("tool", TOOLS, ids=[t[0] for t in TOOLS])
def test_restore_gives_back_the_bytes_before_the_first_change(tool):
    st = edited_document(tool)
    steps = len(st.history)
    viewer, restore = make_viewer(tool, st, 1)
    assert restore() is True
    assert st.images_data[1]["bytes"] == b"ORIGINAL"
    assert st.images_data[0]["bytes"] == b"P1"
    assert len(st.history) == steps + 1                 # une étape de plus, pas un Annuler
    assert [e["bytes"] for e in undo_data(st)["entries"]] == [b"P1", b"EDIT 2"]
    assert st.page_key(1) not in getattr(st, tool[3])   # original oublié
    assert viewer.recorded                              # étape de macro enregistrée


NAMES = {"effects_tool_qt.py": "effect", "color_depth_tool_qt.py": "color_depth",
         "image_mode_tool_qt.py": "image_mode"}


@pytest.mark.parametrize("tool", TOOLS, ids=[t[0] for t in TOOLS])
def test_restore_is_refused_after_a_change_made_by_something_else(tool):
    """La rotation (ou tout autre changement) serait effacée sans qu'on l'ait
    demandé : la page reste telle quelle, sans étape d'historique."""
    st = edited_document(tool)
    st.remember_tool_result(NAMES[tool[0]], st.page_key(1), b"EDIT 2")
    st.images_data[1]["bytes"] = b"EDIT 2 + ROTATION"
    save_state_data(st, force=True)
    steps = len(st.history)
    _viewer, restore = make_viewer(tool, st, 1)
    assert restore() is False
    assert st.images_data[1]["bytes"] == b"EDIT 2 + ROTATION"
    assert len(st.history) == steps


@pytest.mark.parametrize("tool", TOOLS, ids=[t[0] for t in TOOLS])
def test_restore_is_available_again_once_the_change_is_undone(tool):
    st = edited_document(tool)
    st.remember_tool_result(NAMES[tool[0]], st.page_key(1), b"EDIT 2")
    st.images_data[1]["bytes"] = b"EDIT 2 + ROTATION"
    st.images_data[1]["bytes"] = bytes(b"EDIT 2")          # autre objet, mêmes octets
    _viewer, restore = make_viewer(tool, st, 1)
    assert restore() is True
    assert st.images_data[1]["bytes"] == b"ORIGINAL"
    assert (NAMES[tool[0]], st.page_key(1)) not in st.tool_result_bytes_by_page


def test_availability_needs_an_original():
    st = AppState()
    assert st.restore_is_available({}, "effect", 1, b"X") is False


def test_new_original_after_a_change_keeps_that_change():
    st, originals = AppState(), {1: b"ORIGINAL"}
    st.remember_tool_result("effect", 1, b"EDIT")
    st.keep_tool_original(originals, "effect", 1, b"EDIT + ROTATION")
    assert originals == {1: b"EDIT + ROTATION"}


def test_original_is_kept_through_a_chain_of_settings():
    st, originals = AppState(), {}
    st.keep_tool_original(originals, "effect", 1, b"ORIGINAL")
    st.remember_tool_result("effect", 1, b"EDIT 1")
    st.keep_tool_original(originals, "effect", 1, b"EDIT 1")
    assert originals == {1: b"ORIGINAL"}


COMMITS = [
    ("effects_tool_qt.py", "EffectsViewerMixin", "perform_effect", "effect_original_bytes_by_page",
     "_sync_effects_panel"),
    ("color_depth_tool_qt.py", "ColorDepthViewerMixin", "perform_color_depth",
     "color_depth_original_bytes_by_page", "_sync_color_depth_panel"),
    ("image_mode_tool_qt.py", "ImageModeViewerMixin", "perform_image_mode",
     "image_mode_original_bytes_by_page", "_sync_image_mode_panel"),
]


@pytest.mark.parametrize("tool", COMMITS, ids=[t[0] for t in COMMITS])
def test_tool_passage_after_a_change_restores_up_to_that_change(tool, monkeypatch):
    """Effet, rotation, autre effet, restauration : la rotation reste."""
    def apply(entries, settings, **options):
        entries[0]["bytes"] = entries[0]["bytes"] + b" + OUTIL"

    module = types.ModuleType("modules.qt.image_processing_qt")
    module.apply_image_adjustments = apply
    monkeypatch.setitem(sys.modules, "modules.qt.image_processing_qt", module)
    st = AppState()
    st.images_data = [page("p01.png", b"PAGE")]
    save_state_data(st, force=True)
    viewer, perform = make_viewer(tool, st, 0)
    assert perform("cle") is True
    original = getattr(st, tool[3])
    assert original[st.page_key(0)] == b"PAGE"
    st.images_data[0]["bytes"] = b"PAGE TOURNEE"
    assert perform("cle") is True
    assert original[st.page_key(0)] == b"PAGE TOURNEE"
    assert st.images_data[0]["bytes"] == b"PAGE TOURNEE + OUTIL"


@pytest.mark.parametrize("tool", TOOLS, ids=[t[0] for t in TOOLS])
def test_second_restore_does_nothing(tool):
    st = edited_document(tool)
    _viewer, restore = make_viewer(tool, st, 1)
    restore()
    steps = len(st.history)
    assert restore() is False
    assert len(st.history) == steps


@pytest.mark.parametrize("tool", TOOLS, ids=[t[0] for t in TOOLS])
def test_restore_follows_the_page_after_an_insertion_before_it(tool):
    st = edited_document(tool)
    st.images_data.insert(0, page("p00.png", b"P0"))   # la page modifiée est maintenant la 3e
    save_state_data(st, force=True)
    _viewer, restore = make_viewer(tool, st, 2)
    assert restore() is True
    assert [e["bytes"] for e in st.images_data] == [b"P0", b"P1", b"ORIGINAL"]


@pytest.mark.parametrize("tool", TOOLS, ids=[t[0] for t in TOOLS])
def test_page_now_at_the_old_position_is_not_restored(tool):
    """L'original est rangé par page, jamais par position : la page qui a pris
    la place de la page modifiée n'a rien à restaurer."""
    st = edited_document(tool)
    st.images_data.insert(1, page("p01b.png", b"AUTRE"))
    _viewer, restore = make_viewer(tool, st, 1)
    assert restore() is False
    assert st.images_data[1]["bytes"] == b"AUTRE"


@pytest.mark.parametrize("tool", TOOLS, ids=[t[0] for t in TOOLS])
def test_macro_playback_adds_no_history_step(tool):
    st = edited_document(tool)
    steps = len(st.history)
    viewer, restore = make_viewer(tool, st, 1)
    assert restore(skip_history=True, _skip_macro_capture=True) is True
    assert st.images_data[1]["bytes"] == b"ORIGINAL"
    assert len(st.history) == steps
    assert viewer.recorded == []


@pytest.mark.parametrize("tool", TOOLS, ids=[t[0] for t in TOOLS])
def test_nothing_captured_changes_nothing(tool):
    st = AppState()
    st.images_data = [page("p01.png", b"P1")]
    save_state_data(st, force=True)
    _viewer, restore = make_viewer(tool, st, 0)
    assert restore() is False
    assert st.images_data[0]["bytes"] == b"P1"
    assert len(st.history) == 1


def test_restored_effect_forgets_the_chosen_effect():
    tool = TOOLS[0]
    st = edited_document(tool)
    st.effect_key_by_page[st.page_key(1)] = "sepia"
    _viewer, restore = make_viewer(tool, st, 1)
    restore()
    assert st.page_key(1) not in st.effect_key_by_page
