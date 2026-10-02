"""Nom d'entrée du CBZ aplati lors des conversions en lot CBR/CB7/CBT -> CBZ.

batch_dialogs_qt.py importe PySide6 au niveau module : la fonction est extraite
par AST et exécutée seule, sans aucun import Qt.
"""
import ast
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    source = (ROOT / "modules" / "qt" / "batch_dialogs_qt.py").read_text(encoding="utf-8")
    nodes = [n for n in ast.parse(source).body
             if isinstance(n, ast.FunctionDef) and n.name == "_unique_flat_name"]
    assert len(nodes) == 1
    ns = {"os": os}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "batch_dialogs_qt.py", "exec"), ns)
    return ns["_unique_flat_name"]


_unique_flat_name = _load()


def _flatten(paths):
    written = set()
    return [_unique_flat_name(p, written) for p in paths]


def _natural_sort_key(text):
    name = os.path.splitext(text)[0]
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', name)]


def test_unique_names_unchanged():
    paths = ["page01.jpg", "Chap1/page02.jpg", "a b/c d/page 03.png", "Été/café.png"]
    assert _flatten(paths) == ["page01.jpg", "page02.jpg", "page 03.png", "café.png"]


def test_same_basename_in_two_dirs_gets_suffix():
    assert _flatten(["A/page01.jpg", "B/page01.jpg"]) == ["page01.jpg", "page01_2.jpg"]


def test_three_homonyms_and_root():
    paths = ["page01.jpg", "A/page01.jpg", "X/Y/Z/page01.jpg"]
    assert _flatten(paths) == ["page01.jpg", "page01_2.jpg", "page01_3.jpg"]


def test_backslash_separator():
    assert _flatten(["A\\page01.jpg", "B\\page01.jpg"]) == ["page01.jpg", "page01_2.jpg"]


def test_collision_is_case_insensitive():
    assert _flatten(["A/Page01.JPG", "B/page01.jpg"]) == ["Page01.JPG", "page01_2.jpg"]


def test_suffix_skips_already_used_name():
    paths = ["page01_2.jpg", "A/page01.jpg", "B/page01.jpg"]
    assert _flatten(paths) == ["page01_2.jpg", "page01.jpg", "page01_3.jpg"]


def test_output_names_all_distinct():
    paths = ["A/p.jpg", "B/p.jpg", "C/p.jpg", "p.jpg", "D/p_2.jpg"]
    out = _flatten(paths)
    assert len({n.lower() for n in out}) == len(out)


def test_suffixed_name_sorts_right_after_original():
    out = _flatten(["A/page01.jpg", "B/page01.jpg", "page02.jpg"])
    assert sorted(out, key=_natural_sort_key) == ["page01.jpg", "page01_2.jpg", "page02.jpg"]
