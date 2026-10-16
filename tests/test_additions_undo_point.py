"""Un ajout de pages (numérisation, import web) pose un point d'historique
APRÈS lui : sans ce point, Annuler retirerait les pages ajoutées en défaisant
aussi l'action précédente, et Refaire ne les rendrait jamais — une page
numérisée était alors perdue.

scan_dialog_qt.py et web_import_qt.py importent des widgets Qt : les
fonctions d'ajout en sont extraites par AST.
"""
import ast
import types
from pathlib import Path

from modules.qt.state import AppState
from modules.qt.undo_redo import (_create_entries_snapshot, _is_state_identical,
                                  history_top, save_state_data, undo_data, redo_data)

ROOT = Path(__file__).resolve().parent.parent


def _load(module, name):
    tree = ast.parse((ROOT / "modules" / "qt" / module).read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {"_state_module": types.SimpleNamespace(state=None)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), module, "exec"), ns)
    return ns[name]


def make_entry(data, name):
    return {"orig_name": name, "bytes": data, "extension": ".jpg", "is_image": True}


def state_after_one_action():
    """Document ouvert (état initial), puis une action (une page supprimée)."""
    st = AppState()
    st.images_data = [make_entry(b"A", "p01.jpg"), make_entry(b"B", "p02.jpg")]
    save_state_data(st)
    st.images_data.pop()
    save_state_data(st)
    return st


def callbacks_for(st):
    noop = lambda *a, **k: None
    return {"state": st, "save_state": lambda: save_state_data(st),
            "render_mosaic": noop, "update_button_text": noop,
            "update_create_cbz_button": noop, "clear_selection": noop}


def top_describes_document(st):
    return _is_state_identical(history_top(st)["entries"], _create_entries_snapshot(st))


def test_scanned_page_has_its_own_undo_point():
    st = state_after_one_action()
    add = _load("scan_dialog_qt.py", "_add_entry_to_mosaic")
    add(make_entry(b"S", "scan_0002.jpg"), callbacks_for(st))
    assert top_describes_document(st)


def test_redo_gives_back_the_scanned_page():
    st = state_after_one_action()
    add = _load("scan_dialog_qt.py", "_add_entry_to_mosaic")
    add(make_entry(b"S", "scan_0002.jpg"), callbacks_for(st))
    undone = undo_data(st)
    assert [e["orig_name"] for e in undone["entries"]] == ["p01.jpg"]
    redone = redo_data(st)
    assert [e["orig_name"] for e in redone["entries"]] == ["p01.jpg", "scan_0002.jpg"]


def test_web_images_have_their_own_undo_point():
    st = state_after_one_action()
    append = _load("web_import_qt.py", "_append_entries")
    append(st, [make_entry(b"W", "image.jpg")], callbacks_for(st))
    assert top_describes_document(st)
