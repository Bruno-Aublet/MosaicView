"""Corrections faites par un enregistrement (file_operations_qt) : chacune a son
état d'historique, et renoncer à enregistrer ne change rien de plus.

Vérifié :
- PageCount faux : corrigé seulement juste avant l'écriture (jamais au clic sur
  Enregistrer), avec un état d'historique, et l'onglet Métadonnées est prévenu ;
- nom interdit corrigé après « Oui » : état d'historique, document modifié ;
  « Non » et « Annuler » ne changent rien ;
- espaces au bord d'un nom : état d'historique si un nom change, sinon rien.

file_operations_qt.py importe PySide6 : fonctions extraites par AST
(tests/ast_loader.py), fenêtres remplacées.
"""
import ast
import sys
import types
from pathlib import Path

import pytest

from modules.qt.state import AppState
from modules.qt.undo_redo import save_state_data

import ast_loader
from fakes import Signal, tr

ROOT = Path(__file__).resolve().parent.parent


def document(names):
    """Document de pages à ces noms, son premier état d'historique posé."""
    state = AppState()
    state.images_data = [{"orig_name": name, "bytes": b"x", "extension": ".png", "is_image": True}
                         for name in names]
    save_state_data(state, force=True)
    return state


def saver(state):
    return lambda: save_state_data(state)


def history_names(state, position):
    return [e["orig_name"] for e in state.history[position]["entries"]]


# --- Espaces au bord d'un nom -------------------------------------------------

apply_name_edits = ast_loader.load("file_operations_qt.py", {"_apply_name_edits"})["_apply_name_edits"]


def test_trimmed_name_has_its_own_history_state():
    state = document([" p01.png", "p02.png"])
    state.images_data[0]["name_entry"] = " p01.png "
    apply_name_edits(state, saver(state))
    assert state.images_data[0]["orig_name"] == "p01.png" and state.modified is True
    assert len(state.history) == 2
    assert history_names(state, 0)[0] == " p01.png" and history_names(state, 1)[0] == "p01.png"
    assert state.history[1]["modified"] is True


def test_unchanged_names_touch_neither_the_document_nor_the_history():
    state = document(["p01.png"])
    state.images_data[0]["name_entry"] = "p01.png"
    apply_name_edits(state, saver(state))
    assert len(state.history) == 1 and state.modified is False


def test_emptied_name_field_keeps_the_name():
    state = document(["p01.png"])
    state.images_data[0]["name_entry"] = "   "
    apply_name_edits(state, saver(state))
    assert state.images_data[0]["orig_name"] == "p01.png"
    assert len(state.history) == 1 and state.modified is False


# --- Noms interdits -----------------------------------------------------------

class FakeQuestion:
    answer = None

    def __init__(self, *args):
        pass

    def ask_async(self, on_reply):
        on_reply(FakeQuestion.answer)


class FakeWindow:
    def __init__(self, *args, **options):
        self.finished = Signal()

    def show_nonmodal(self):
        self.finished.emit(0)


validate = ast_loader.load(
    "file_operations_qt.py",
    {"_validate_filenames_qt", "_has_parent_dir_segment", "_without_parent_dir_segments"},
    _=tr, _state_module=types.SimpleNamespace(state=None), QuestionYNCDialog=FakeQuestion,
    InfoDialog=FakeWindow, ErrorDialog=FakeWindow)["_validate_filenames_qt"]


def answer_invalid_name(state, answer):
    FakeQuestion.answer = answer
    results, rendered = [], []
    validate(None, lambda: rendered.append(True), results.append, state, saver(state))
    return results, rendered


def test_accepted_name_correction_has_its_own_history_state():
    state = document(["p01.png", "p02?bad.png"])
    results, rendered = answer_invalid_name(state, "yes")
    assert results == [True] and rendered
    assert state.images_data[1]["orig_name"] == "p02_bad.png" and state.modified is True
    assert len(state.history) == 2
    assert history_names(state, 0)[1] == "p02?bad.png"     # Ctrl+Z rend le nom d'avant
    assert history_names(state, 1)[1] == "p02_bad.png"     # Ctrl+Y rend la correction


@pytest.mark.parametrize("answer", ["no", "cancel"])
def test_refused_name_correction_changes_nothing(answer):
    state = document(["p02?bad.png"])
    results, _rendered = answer_invalid_name(state, answer)
    assert results == [False]
    assert state.images_data[0]["orig_name"] == "p02?bad.png"
    assert len(state.history) == 1 and state.modified is False


def test_valid_names_ask_nothing_and_change_nothing():
    state = document(["p01.png"])
    results, _rendered = answer_invalid_name(state, "yes")
    assert results == [True] and len(state.history) == 1 and state.modified is False


# --- PageCount ----------------------------------------------------------------

correct_page_count = ast_loader.load(
    "file_operations_qt.py", {"_correct_page_count_qt"})["_correct_page_count_qt"]


@pytest.fixture
def page_count_document(monkeypatch):
    """Document de 2 pages dont le ComicInfo.xml en annonce 99 ; envois du signal."""
    emitted = []
    module = types.ModuleType("modules.qt.metadata_signal")
    module.metadata_signal = types.SimpleNamespace(emit=emitted.append)
    monkeypatch.setitem(sys.modules, "modules.qt.metadata_signal", module)
    state = document(["p01.png", "p02.png"])
    xml = b"<ComicInfo><Title>Aaaa</Title><PageCount>99</PageCount></ComicInfo>"
    state.images_data.insert(0, {"orig_name": "ComicInfo.xml", "bytes": xml,
                                 "extension": ".xml", "is_image": False})
    state.comic_metadata = {"title": "Aaaa", "page_count": "99"}
    state.original_page_count = 99
    save_state_data(state, force=True)
    return state, emitted


def test_wrong_page_count_is_corrected_with_a_history_state(page_count_document):
    state, emitted = page_count_document
    steps = len(state.history)
    correct_page_count(state, saver(state))
    assert b"<PageCount>2</PageCount>" in state.images_data[0]["bytes"]
    assert state.comic_metadata["page_count"] == "2" and state.modified is True
    assert len(state.history) == steps + 1
    assert b"99" in state.history[-2]["entries"][0]["bytes"]
    assert emitted == [state]                               # onglet Métadonnées prévenu


def test_right_page_count_touches_nothing(page_count_document):
    state, emitted = page_count_document
    state.original_page_count = 2
    steps = len(state.history)
    before = state.images_data[0]["bytes"]
    correct_page_count(state, saver(state))
    assert state.images_data[0]["bytes"] is before
    assert len(state.history) == steps and state.modified is False and emitted == []


def test_document_without_metadata_touches_nothing():
    state = document(["p01.png"])
    correct_page_count(state, saver(state))
    assert len(state.history) == 1 and state.modified is False


# --- Moment de la correction du PageCount -------------------------------------

def _functions():
    tree = ast.parse((ROOT / "modules" / "qt" / "file_operations_qt.py").read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _calls(node, name, enter_inner):
    """Appels à `name` dans `node`, fonctions internes comprises ou non."""
    count = 0
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.Lambda)) and not enter_inner:
            continue
        if isinstance(child, ast.Call) and getattr(child.func, "id", None) == name:
            count += 1
        count += _calls(child, name, enter_inner)
    return count


@pytest.mark.parametrize("function", ["save_as_cbz", "create_cbz_from_images", "apply_new_names"])
def test_page_count_is_not_corrected_when_the_save_command_starts(function):
    assert _calls(_functions()[function], "_correct_page_count_qt", enter_inner=False) == 0


@pytest.mark.parametrize("function, expected", [
    ("save_as_cbz", 1), ("create_cbz_from_images", 1), ("_write_apply_new_names", 2),
])
def test_every_document_write_corrects_the_page_count_first(function, expected):
    assert _calls(_functions()[function], "_correct_page_count_qt", enter_inner=True) == expected
