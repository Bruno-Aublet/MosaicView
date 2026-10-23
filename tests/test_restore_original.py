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
- c'est bien l'original : une retouche faite ensuite par un autre outil
  disparaît aussi (comportement voulu, récupérable par Annuler) ;
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


@pytest.mark.parametrize("tool", TOOLS, ids=[t[0] for t in TOOLS])
def test_restore_returns_to_the_original_even_after_another_tool(tool):
    """« Restaurer l'original » rend la page d'avant toute modification par
    l'outil, pas un Annuler de plus : une retouche faite ensuite par un autre
    outil (rotation…) disparaît aussi. Elle reste récupérable par Annuler,
    la restauration étant une étape de l'historique."""
    st = edited_document(tool)
    st.images_data[1]["bytes"] = b"EDIT 2 + ROTATION"        # autre outil, après celui-ci
    save_state_data(st, force=True)
    _viewer, restore = make_viewer(tool, st, 1)
    assert restore() is True
    assert st.images_data[1]["bytes"] == b"ORIGINAL"
    assert [e["bytes"] for e in undo_data(st)["entries"]] == [b"P1", b"EDIT 2 + ROTATION"]


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
