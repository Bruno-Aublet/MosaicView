"""is_same_path : détection d'un « Enregistrer sous » vers le fichier déjà ouvert."""
import os
import sys

import pytest

from modules.qt.utils import is_same_path


@pytest.fixture
def cbz(tmp_path):
    path = tmp_path / "comic.cbz"
    path.write_bytes(b"PK")
    return path


def test_same_path(cbz):
    assert is_same_path(str(cbz), str(cbz))


def test_relative_and_absolute(cbz, monkeypatch):
    monkeypatch.chdir(cbz.parent)
    assert is_same_path("comic.cbz", str(cbz))


def test_dot_dot_component(cbz):
    other = cbz.parent / "sub" / ".." / "comic.cbz"
    assert is_same_path(str(other), str(cbz))


@pytest.mark.skipif(sys.platform != "win32", reason="système de fichiers insensible à la casse")
def test_different_case(cbz):
    assert is_same_path(str(cbz).upper(), str(cbz))


def test_different_files(cbz):
    other = cbz.parent / "other.cbz"
    other.write_bytes(b"PK")
    assert not is_same_path(str(other), str(cbz))


def test_new_file_not_yet_written(cbz):
    assert not is_same_path(str(cbz.parent / "new.cbz"), str(cbz))


@pytest.mark.parametrize("a, b", [("", "x.cbz"), ("x.cbz", None), (None, None)])
def test_empty_paths(a, b):
    assert not is_same_path(a, b)
