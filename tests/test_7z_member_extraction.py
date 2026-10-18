"""Extraction d'un membre précis d'une archive 7z/CB7 via le 7z.exe embarqué.

archive_loader.py importe PySide6 au niveau module : les fonctions 7z sont extraites
par AST et exécutées seules, sans aucun import Qt.
"""
import ast
import io
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SEVEN_ZIP = ROOT / "7zip" / "7z.exe"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not SEVEN_ZIP.exists(),
    reason="7z.exe embarqué requis (Windows)",
)

_FUNCS = ("_to_short_path", "_is_7z_file_block", "_list_7z_files", "_read_7z_file",
          "_check_member_size")


def _is_max_member_size(node):
    return (isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "MAX_MEMBER_SIZE")


def _load_7z_functions():
    source = (ROOT / "modules" / "qt" / "archive_loader.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes = [n for n in tree.body
             if (isinstance(n, ast.FunctionDef) and n.name in _FUNCS) or _is_max_member_size(n)]
    assert {n.name for n in nodes if isinstance(n, ast.FunctionDef)} == set(_FUNCS)
    ns = {"os": os, "sys": sys, "subprocess": subprocess, "threading": threading,
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


def test_wildcard_characters_in_member_name_are_literal(tmp_path):
    """Un nom contenant * ou ? désigne ce seul membre, pas un joker : sinon
    7z rend les contenus de tous les membres qu'il désigne, mis bout à bout,
    et la page affiche (puis une conversion écrit) l'image d'un autre membre.
    Ces noms ne peuvent pas exister sur un disque Windows : les membres sont
    ajoutés depuis l'entrée standard (-si<nom>)."""
    archive = tmp_path / "jokers.cb7"
    pages = {"page1.jpg": b"AAAA", "page*.jpg": b"BBBB", "page?.jpg": b"CCCC"}
    for name, data in pages.items():
        subprocess.run([str(SEVEN_ZIP), "a", "-t7z", str(archive), f"-si{name}"],
                       input=data, check=True, capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    assert sorted(_list_7z_files(str(archive))) == sorted(pages)
    for name, data in pages.items():
        assert _read_7z_file(str(archive), name) == data


def test_member_larger_than_the_limit_is_refused(tmp_path):
    """L'extraction s'arrête dès que la sortie dépasse MAX_MEMBER_SIZE : une
    page qui se décompresse en plusieurs Go n'est jamais lue en entier."""
    archive = tmp_path / "big.cb7"
    for name, data in (("big.jpg", b"x" * 5000), ("ok.jpg", b"y" * 100)):
        subprocess.run([str(SEVEN_ZIP), "a", "-t7z", str(archive), f"-si{name}"],
                       input=data, check=True, capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    old = _ns["MAX_MEMBER_SIZE"]
    _ns["MAX_MEMBER_SIZE"] = 1000
    try:
        assert _read_7z_file(str(archive), "ok.jpg") == b"y" * 100
        with pytest.raises(ValueError):
            _read_7z_file(str(archive), "big.jpg")
    finally:
        _ns["MAX_MEMBER_SIZE"] = old


def test_read_error_is_reported(tmp_path):
    archive = tmp_path / "not7z.cb7"
    archive.write_bytes(b"not a 7z archive")
    with pytest.raises(RuntimeError):
        _read_7z_file(str(archive), "page.jpg")


def _attrib(path, *flags):
    subprocess.run(["attrib", *flags, str(path)], check=True, capture_output=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)


def test_list_keeps_read_only_hidden_and_non_archive_files(tmp_path):
    """Un fichier en lecture seule (RA), caché (HA) ou sans bit archive (N)
    est une page comme une autre : l'ignorer ferait supprimer l'archive
    d'origine par une conversion sans ces pages."""
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    pages = {"a.jpg": b"a", "b.jpg": b"b", "c.jpg": b"c", "d.jpg": b"d", "sub/e.jpg": b"e"}
    for name, data in pages.items():
        (src / name).write_bytes(data)
    _attrib(src / "b.jpg", "+R")
    _attrib(src / "c.jpg", "+H")
    _attrib(src / "d.jpg", "-A")
    archive = tmp_path / "attrs.cb7"
    try:
        subprocess.run([str(SEVEN_ZIP), "a", "-t7z", str(archive), "*"],
                       cwd=src, check=True, capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    finally:
        _attrib(src / "b.jpg", "-R")
        _attrib(src / "c.jpg", "-H")
    names = _list_7z_files(str(archive))
    assert sorted(names) == sorted(pages)
    for name in names:
        assert _read_7z_file(str(archive), name) == pages[name]


@pytest.mark.parametrize("path, attributes, expected", [
    ("a.jpg", "A", True),
    ("a.jpg", "RA", True),
    ("a.jpg", "HA", True),
    ("a.jpg", "N", True),
    ("a.jpg", "A_ -rw-r--r--", True),
    ("a.jpg", None, True),
    ("dir", "D", False),
    ("dir", "D_ drwxr-xr-x", False),
    ("dir/", None, False),
    (None, "A", False),
])
def test_file_block_detection(path, attributes, expected):
    assert _ns["_is_7z_file_block"](path, attributes) is expected
