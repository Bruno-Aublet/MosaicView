"""Les actions différées de la Bibliothèque (suppression, renommage de la base,
confirmées dans un dialogue non modal) ne visent que la base pour laquelle
elles ont été demandées, jamais une autre base ouverte entre-temps.

library_window.py importe des widgets Qt : _same_db en est extraite par AST.
"""
import ast
import os

from modules.qt.library_db import LibraryDB


def _load_same_db():
    path = os.path.join(os.path.dirname(__file__), "..", "modules", "qt", "library_window.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_same_db"]
    ns = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "library_window.py", "exec"), ns)
    return ns["_same_db"]


_same_db = _load_same_db()


def make_db(tmp_path, name):
    master = str(tmp_path / f"comics_{name}")
    os.makedirs(master)
    return LibraryDB.create(str(tmp_path / f"{name}.mvdb"), master)


def test_same_object_is_the_same_db(tmp_path):
    a = make_db(tmp_path, "a")
    try:
        assert _same_db(a, a)
    finally:
        a.close()


def test_other_db_opened_meanwhile_is_refused(tmp_path):
    a = make_db(tmp_path, "a")
    b = make_db(tmp_path, "b")
    try:
        assert not _same_db(b, a)
    finally:
        a.close()
        b.close()


def test_same_file_reopened_is_the_same_db(tmp_path):
    a = make_db(tmp_path, "a")
    path = a.db_path
    a.close()
    first = LibraryDB.open(path)
    second = LibraryDB.open(path)
    try:
        assert _same_db(second, first)
    finally:
        first.close()
        second.close()


def test_no_db_open_is_refused(tmp_path):
    a = make_db(tmp_path, "a")
    try:
        assert not _same_db(None, a)
    finally:
        a.close()
