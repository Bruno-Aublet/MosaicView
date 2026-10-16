"""Noms de pages refusés à l'enregistrement pour « sortie du dossier » :
seul un segment de chemin égal à `..` est dangereux (écrit dans le CBZ, il
ferait sortir le fichier du dossier d'extraction) ; des points dans un nom
(`Fin...jpg`) sont légitimes et ne doivent pas être signalés ni retirés.

file_operations_qt.py importe PySide6 : les fonctions sont extraites par AST.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAMES = {"_has_parent_dir_segment", "_without_parent_dir_segments"}


def _load():
    tree = ast.parse((ROOT / "modules" / "qt" / "file_operations_qt.py").read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in NAMES]
    assert {n.name for n in nodes} == NAMES
    ns = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "file_operations_qt.py", "exec"), ns)
    return ns


_NS = _load()
has_parent = _NS["_has_parent_dir_segment"]
without_parent = _NS["_without_parent_dir_segments"]


def test_dots_inside_a_name_are_allowed():
    for name in ("Fin...jpg", "Wait..jpg", "Chapitre 1..2.jpg", "a..b/c...d.png", "...jpg"):
        assert not has_parent(name), name


def test_parent_dir_segment_is_detected():
    for name in ("../page.jpg", "chap/../../page.jpg", "..\\page.jpg", "..", "chap/.."):
        assert has_parent(name), name


def test_cleaning_removes_only_parent_dir_segments():
    assert without_parent("../../page.jpg") == "page.jpg"
    assert without_parent("chap\\..\\page.jpg") == "chap/page.jpg"
    assert without_parent("Fin...jpg") == "Fin...jpg"
    assert without_parent("..") == ""
