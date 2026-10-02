"""Extraction d'un membre précis d'une archive 7z/CB7 via le 7z.exe embarqué.

archive_loader.py importe PySide6 au niveau module : les fonctions 7z sont extraites
par AST et exécutées seules, sans aucun import Qt.
"""
import ast
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SEVEN_ZIP = ROOT / "7zip" / "7z.exe"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not SEVEN_ZIP.exists(),
    reason="7z.exe embarqué requis (Windows)",
)

_FUNCS = ("_to_short_path", "_list_7z_files", "_read_7z_file")


def _load_7z_functions():
    source = (ROOT / "modules" / "qt" / "archive_loader.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in _FUNCS]
    assert {n.name for n in nodes} == set(_FUNCS)
    ns = {"os": os, "sys": sys, "subprocess": subprocess,
          "_get_7z_exe": lambda: str(SEVEN_ZIP)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "archive_loader.py", "exec"), ns)
    return ns


_ns = _load_7z_functions()
_list_7z_files = _ns["_list_7z_files"]
_read_7z_file = _ns["_read_7z_file"]

# Chemin dans l'archive -> couleur unie (images distinctes, décodables par PIL)
MEMBERS = {
    "root.jpg": (0, 255, 0),
    "A/page01.jpg": (255, 0, 0),
    "B/page01.jpg": (0, 0, 255),
    "X/root.jpg": (200, 200, 0),
    "X/Y/Z/deep.png": (10, 20, 30),
    "dir with space/my page.png": (40, 50, 60),
    "Été/café.png": (70, 80, 90),
}


@pytest.fixture(scope="module")
def cb7(tmp_path_factory):
    base = tmp_path_factory.mktemp("cb7")
    src = base / "src"
    expected = {}
    for name, color in MEMBERS.items():
        path = src / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), color).save(path)
        expected[name] = path.read_bytes()
    archive = base / "test.cb7"
    subprocess.run(
        [str(SEVEN_ZIP), "a", "-t7z", str(archive), "*"],
        cwd=src, check=True, capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return str(archive), expected


@pytest.mark.parametrize("member", list(MEMBERS))
def test_read_returns_exact_member_bytes(cb7, member):
    archive, expected = cb7
    assert _read_7z_file(archive, member) == expected[member]


def test_same_basename_in_two_dirs_gives_two_different_images(cb7):
    archive, _unused = cb7
    a = _read_7z_file(archive, "A/page01.jpg")
    b = _read_7z_file(archive, "B/page01.jpg")
    assert a != b
    assert Image.open(io.BytesIO(a)).convert("RGB").getpixel((4, 4))[0] > 200
    assert Image.open(io.BytesIO(b)).convert("RGB").getpixel((4, 4))[2] > 200


def test_root_file_not_mixed_with_subdir_homonym(cb7):
    archive, expected = cb7
    assert _read_7z_file(archive, "root.jpg") == expected["root.jpg"]
    assert _read_7z_file(archive, "X/root.jpg") == expected["X/root.jpg"]


def test_backslash_member_name_accepted(cb7):
    archive, expected = cb7
    assert _read_7z_file(archive, "X\\Y\\Z\\deep.png") == expected["X/Y/Z/deep.png"]


def test_list_returns_non_ascii_names_intact(cb7):
    archive, _unused = cb7
    assert "Été/café.png" in _list_7z_files(archive)


def test_list_then_read_roundtrip(cb7):
    archive, expected = cb7
    names = _list_7z_files(archive)
    assert sorted(names) == sorted(MEMBERS)
    for name in names:
        assert _read_7z_file(archive, name) == expected[name]
