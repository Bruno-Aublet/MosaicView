"""Annuler/Refaire bloqués pendant une opération en arrière-plan sur le document.

Sans ce blocage, un Annuler pendant une rotation, un redressement, un
redimensionnement ou une conversion restaurerait un snapshot que le worker
écraserait ensuite, et son save_state de fin effacerait la branche Refaire.
"""
from modules.qt.state import AppState
from modules.qt.undo_redo import save_state_data
from modules.qt.undo_redo_qt import redo_action_qt, undo_action_qt


def make_entry(data, name):
    return {"orig_name": name, "bytes": data, "extension": ".bmp", "is_image": True}


def state_with_two_steps():
    st = AppState()
    st.images_data = [make_entry(b"A", "p01.bmp")]
    save_state_data(st, force=True)
    st.images_data.append(make_entry(b"B", "p02.bmp"))
    save_state_data(st, force=True)
    return st


def noop(*_args, **_kwargs):
    pass


def test_begin_and_end_operation():
    st = AppState()
    token = object()
    assert not st.operation_running()
    st.begin_operation(token)
    assert st.operation_running()
    st.end_operation(token)
    assert not st.operation_running()


def test_end_operation_removes_only_its_token():
    st = AppState()
    first, second = object(), object()
    st.begin_operation(first)
    st.begin_operation(second)
    st.end_operation(first)
    assert st.operation_running()
    st.end_operation(second)
    assert not st.operation_running()


def test_undo_is_ignored_during_operation():
    st = state_with_two_steps()
    st.begin_operation(object())
    undo_action_qt(st, noop, noop, noop, noop)
    assert st.history_index == 1
    assert len(st.images_data) == 2


def test_redo_is_ignored_during_operation():
    st = state_with_two_steps()
    st.history_index = 0
    st.begin_operation(object())
    redo_action_qt(st, noop, noop, noop, noop)
    assert st.history_index == 0


def test_undo_works_again_after_operation():
    st = state_with_two_steps()
    token = object()
    st.begin_operation(token)
    st.end_operation(token)
    undo_action_qt(st, noop, noop, noop, noop)
    assert st.history_index == 0


# ---------------------------------------------------------------------------
# Commandes bloquées pendant une opération en arrière-plan
# (panel_widget.BUSY_BLOCKED_COMMANDS, icon_toolbar_qt._BUSY_BLOCKED_ICONS)
# ---------------------------------------------------------------------------

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _frozenset_constant(module, name):
    tree = ast.parse((ROOT / "modules" / "qt" / module).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return set(ast.literal_eval(node.value.args[0]))
    raise AssertionError(f"{name} introuvable dans {module}")


def _dict_keys_in(module, function):
    tree = ast.parse((ROOT / "modules" / "qt" / module).read_text(encoding="utf-8"))
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Dict):
                    keys.update(k.value for k in sub.keys if isinstance(k, ast.Constant))
    return keys


def test_blocked_commands_exist():
    """Une clé mal orthographiée ne bloquerait rien, sans aucune erreur."""
    blocked = _frozenset_constant("panel_widget.py", "BUSY_BLOCKED_COMMANDS")
    commands = _dict_keys_in("menubar_callbacks_qt.py", "build_menubar_callbacks")
    assert blocked <= commands, sorted(blocked - commands)


def test_blocked_icons_exist():
    blocked = _frozenset_constant("icon_toolbar_qt.py", "_BUSY_BLOCKED_ICONS")
    icons = _dict_keys_in("icon_toolbar_qt.py", "build_icon_toolbar")
    assert blocked <= icons, sorted(blocked - icons)


def _document_writer(body):
    """_write_document_with_progress (file_operations_qt.py, qui importe
    PySide6) extraite par AST et exécutée avec `body` à la place de
    l'écriture réelle."""
    tree = ast.parse((ROOT / "modules" / "qt" / "file_operations_qt.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "_write_document_with_progress")
    ns = {"_write_document_unlocked": body}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "file_operations_qt.py", "exec"), ns)
    return ns["_write_document_with_progress"]


def test_save_blocks_document_commands_while_writing():
    """Pendant l'écriture (qui traite les événements), un second
    enregistrement ou une modification des pages est sans effet."""
    st = AppState()
    seen = []
    write = _document_writer(lambda *_args: seen.append(st.operation_running()) or True)
    assert write("x.cbz", st, None, 0) is True
    assert seen == [True]
    assert not st.operation_running()


def test_addition_during_write_is_not_marked_saved():
    """Une page numérisée arrivée pendant l'écriture n'est pas dans le
    fichier : le document ne doit pas être déclaré sauvegardé."""
    st = AppState()
    st.images_data = [make_entry(b"A", "p01.bmp")]

    def body(*_args):
        st.run_when_idle(lambda: st.images_data.append(make_entry(b"S", "scan.bmp")))
        return True

    write = _document_writer(body)
    assert write("x.cbz", st, None, 0) is False
    assert len(st.images_data) == 2


# ---------------------------------------------------------------------------
# Ajouts asynchrones pendant une opération (AppState.run_when_idle)
# ---------------------------------------------------------------------------

def test_addition_runs_immediately_when_idle():
    st = AppState()
    done = []
    st.run_when_idle(lambda: done.append(1))
    assert done == [1]


def test_addition_waits_for_the_end_of_the_operation():
    """Appliqué pendant une rotation, l'ajout serait effacé par son annulation
    (retour au sommet de l'historique pris avant elle)."""
    st = AppState()
    token = object()
    done = []
    st.begin_operation(token)
    st.run_when_idle(lambda: done.append(1))
    assert done == []
    st.end_operation(token)
    assert done == [1]
    assert st.pending_when_idle == []


def test_addition_waits_for_the_last_operation():
    st = AppState()
    first, second = object(), object()
    done = []
    st.begin_operation(first)
    st.begin_operation(second)
    st.run_when_idle(lambda: done.append(1))
    st.end_operation(first)
    assert done == []
    st.end_operation(second)
    assert done == [1]


def test_failed_addition_does_not_drop_the_next_ones(capsys):
    """Un ajout en attente qui lève une exception ne fait perdre ni les ajouts
    suivants (une page numérisée), ni la fin de l'opération qui les applique."""
    st = AppState()
    token = object()
    done = []

    def broken():
        raise RuntimeError("ajout en échec")

    st.begin_operation(token)
    st.run_when_idle(broken)
    st.run_when_idle(lambda: done.append("page numérisée"))
    st.end_operation(token)
    assert done == ["page numérisée"]
    assert not st.operation_running()
    assert "ajout en échec" in capsys.readouterr().err


def test_addition_for_a_closed_document_is_dropped():
    st = AppState()
    token = object()
    done = []
    st.begin_operation(token)
    st.run_when_idle(lambda: done.append(1))
    st.doc_generation += 1
    st.end_operation(token)
    assert done == []


def test_failed_save_releases_the_lock():
    st = AppState()

    def fail(*_args):
        raise OSError(28, "No space left on device")

    write = _document_writer(fail)
    try:
        write("x.cbz", st, None, 0)
    except OSError:
        pass
    assert not st.operation_running()
