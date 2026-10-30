"""Les actions différées de la Bibliothèque (suppression, renommage de la base,
confirmées dans un dialogue non modal) ne visent que la base pour laquelle
elles ont été demandées, jamais une autre base ouverte entre-temps.

library_window.py importe des widgets Qt : _same_db et les deux suites de
confirmation en sont extraites par AST.
"""
import os

import pytest

import ast_loader
from modules.qt.library_db import LibraryDB

_same_db = ast_loader.load("library_window.py", {"_same_db"})["_same_db"]


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


# ---------------------------------------------------------------------------
# Les vraies suites des confirmations (LibraryWindow._on_delete_accepted,
# _on_rename_accepted), sur de vraies bases : la confirmation est demandée
# pour la base A, la base B est ouverte avant la réponse.
# ---------------------------------------------------------------------------

_Methods = ast_loader.load_methods(
    "library_window.py", "LibraryWindow", {"_on_delete_accepted", "_on_rename_accepted"},
    _same_db=_same_db, os=os)


class _Table:
    def setVisible(self, visible): pass
    def setRowCount(self, count): pass


class Library(_Methods):
    def __init__(self, db):
        self._db = db
        self._filter_table, self._table = _Table(), _Table()
        self.errors = []

    def _close_db_dialogs(self): pass
    def _stop_scan(self, wait=False): pass
    def _retranslate(self): pass

    def _show_error(self, text):
        self.errors.append(text)


class Answer:
    def __init__(self, result_name=None):
        self.result_name = result_name


@pytest.fixture
def trash(monkeypatch):
    from modules.qt import utils
    trashed = []

    def to_trash(path):
        trashed.append(os.path.basename(path))
        os.remove(path)
    monkeypatch.setattr(utils, "send_to_trash", to_trash)
    return trashed


def files(tmp_path):
    return sorted(n for n in os.listdir(tmp_path) if n.endswith(".mvdb"))


def test_delete_confirmed_for_the_open_db_deletes_it(tmp_path, trash):
    """Témoin : la base ouverte, celle de la confirmation, va à la Corbeille."""
    a, b = make_db(tmp_path, "a"), make_db(tmp_path, "b")
    b.close()
    window = Library(a)
    window._on_delete_accepted(Answer(), a)
    assert trash == ["a.mvdb"] and files(tmp_path) == ["b.mvdb"]
    assert window._db is None and window.errors == []


def test_delete_confirmed_after_another_db_was_opened_deletes_nothing(tmp_path, trash):
    """Confirmation de suppression de A validée après l'ouverture de B :
    aucune base supprimée — ni A, annoncée mais plus ouverte, ni B, ouverte
    au moment du clic — et B reste ouverte."""
    a, b = make_db(tmp_path, "a"), make_db(tmp_path, "b")
    a.close()
    window = Library(b)
    try:
        window._on_delete_accepted(Answer(), a)
        assert trash == [] and files(tmp_path) == ["a.mvdb", "b.mvdb"]
        assert window._db is b
    finally:
        b.close()


def test_rename_confirmed_after_another_db_was_opened_renames_nothing(tmp_path):
    a, b = make_db(tmp_path, "a"), make_db(tmp_path, "b")
    a.close()
    window = Library(b)
    try:
        window._on_rename_accepted(Answer("cccc"), a)
        assert files(tmp_path) == ["a.mvdb", "b.mvdb"]
        assert window._db is b and b.name == "b"
    finally:
        b.close()


def test_rename_confirmed_for_the_open_db_renames_it(tmp_path):
    a = make_db(tmp_path, "a")
    window = Library(a)
    try:
        window._on_rename_accepted(Answer("cccc"), a)
        assert files(tmp_path) == ["cccc.mvdb"] and window.errors == []
    finally:
        a.close()
