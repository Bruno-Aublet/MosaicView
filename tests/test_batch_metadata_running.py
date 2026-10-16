"""Un import des métadonnées en lot n'est pas lancé sur des fichiers qu'un
autre import non terminé traite déjà : ils seraient convertis deux fois
(« X.cbz » puis « X (1).cbz ») et leur original supprimé deux fois.

batch_metadata_dialog_qt.py importe PySide6 : la fonction en est extraite
par AST, avec une liste d'imports en cours simulée.
"""
import ast
import os
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(active, pending=()):
    tree = ast.parse((ROOT / "modules" / "qt" / "batch_metadata_dialog_qt.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "_files_in_running_batch")
    ns = {"os": os, "_active_orchestrators": active, "_pending_confirmations": list(pending)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "batch_metadata_dialog_qt.py", "exec"), ns)
    return ns["_files_in_running_batch"]


def orchestrator(files, ended=False):
    return types.SimpleNamespace(_files=files, _ended=ended)


def test_no_running_batch(tmp_path):
    check = _load([])
    assert not check([str(tmp_path / "a.cbr")])


def test_same_file_in_a_running_batch_is_refused(tmp_path):
    a = str(tmp_path / "a.cbr")
    check = _load([orchestrator([a, str(tmp_path / "b.cbr")])])
    assert check([a, str(tmp_path / "c.cbr")])


def test_other_files_are_allowed(tmp_path):
    check = _load([orchestrator([str(tmp_path / "a.cbr")])])
    assert not check([str(tmp_path / "c.cbr")])


def test_finished_batch_does_not_block(tmp_path):
    a = str(tmp_path / "a.cbr")
    check = _load([orchestrator([a], ended=True)])
    assert not check([a])


def test_confirmation_still_displayed_blocks_a_second_one(tmp_path):
    """Deux confirmations ouvertes sur le même dossier : la seconde est
    refusée, sans attendre que la première ait démarré l'import."""
    a = str(tmp_path / "a.cbr")
    check = _load([], pending=[[a]])
    assert check([a])


def test_path_spelling_does_not_matter(tmp_path):
    a = str(tmp_path / "Dir" / "a.cbr")
    other_spelling = str(tmp_path / "Dir" / "." / "A.CBR")
    check = _load([orchestrator([a])])
    assert check([other_spelling])
