"""Noms et chemins venus de l'extérieur (archives, ComicInfo.xml, dossiers).

- utils.windows_safe_component / windows_safe_relpath / unique_names : un nom
  de page écrit sur le disque (export, presse-papiers, glisser-déposer,
  ouverture avec l'application par défaut) est valide sous Windows et unique.
- open_with_default_app_qt.openable_temp_name : liste blanche jugée sur le nom
  réellement écrit (« evil.exe. » est écrit « evil.exe » par Windows).
- utils.walk_without_links : les lots n'entrent pas dans les jonctions NTFS.
- tabs_qt._page_attr_int : un attribut <Page> non entier ne casse pas le
  tableau Pages (tabs_qt importe des widgets : fonction extraite par AST).
"""
import os
import subprocess
import sys

import pytest

import ast_loader
from modules.qt.utils import (windows_safe_component, windows_safe_relpath, unique_names,
                              walk_without_links)
from modules.qt.open_with_default_app_qt import openable_temp_name


@pytest.mark.parametrize("name, expected", [
    ("page.jpg", "page.jpg"),
    ("a:b.jpg", "a_b.jpg"),
    ('w<h>a"t|?*.png', "w_h_a_t___.png"),
    ("evil.exe.", "evil.exe"),
    ("evil.exe . .", "evil.exe"),
    ("CON.jpg", "_CON.jpg"),
    ("nul", "_nul"),
    ("com1.txt", "_com1.txt"),
    ("console.jpg", "console.jpg"),
    ("tab\there.jpg", "tab_here.jpg"),
    ("...", "_"),
])
def test_windows_safe_component(name, expected):
    assert windows_safe_component(name) == expected


@pytest.mark.parametrize("name, expected", [
    ("chap1/p01.jpg", "chap1/p01.jpg"),
    ("../../Windows/x.jpg", "Windows/x.jpg"),
    ("..\\..\\x.jpg", "x.jpg"),
    ("/abs/x.jpg", "abs/x.jpg"),
    ("C:/Users/x.jpg", "C_/Users/x.jpg"),
    ("\\\\server\\share\\x.jpg", "server/share/x.jpg"),
    ("./a//b.jpg", "a/b.jpg"),
    ("..", "_"),
])
def test_windows_safe_relpath_never_leaves_the_folder(name, expected):
    assert windows_safe_relpath(name) == expected


@pytest.mark.parametrize("name, expected", [
    ("p01.jpg", "p01.jpg"),
    ("sub/p02.jpg", "sub/p02.jpg"),
    ("Fin...jpg", "Fin...jpg"),
    ("./p.jpg", "p.jpg"),
    ("../evil.jpg", "evil.jpg"),
    ("/abs.jpg", "abs.jpg"),
    ("C:/drive.jpg", "drive.jpg"),
    ("C:x.jpg", "x.jpg"),
    ("sub/../../up.jpg", "sub/up.jpg"),
    ("\\\\srv\\share\\x.jpg", "srv/share/x.jpg"),
    ("..", ""),
    ("page\n01.jpg", "page_01.jpg"),
    ("sub\r\n/p\t.jpg", "sub__/p_.jpg"),
    # Lettre de lecteur révélée par le retrait d'une première : jamais de
    # chemin absolu en tête
    ("C:C:/x.jpg", "x.jpg"),
    ("C:/D:/x.jpg", "x.jpg"),
    ("/c:", ""),
    ("./C:x.jpg", "x.jpg"),
    ("C:../x.jpg", "x.jpg"),
    ("sub/C:/x.jpg", "sub/C:/x.jpg"),
])
def test_contained_member_name(name, expected):
    """archive_names.contained_member_name : nom écrit dans le CBZ d'une
    conversion par l'import de métadonnées en lot."""
    from modules.qt.archive_names import contained_member_name
    assert contained_member_name(name) == expected


def test_unique_names_ignores_case():
    assert unique_names(["p.jpg", "P.JPG", "p.jpg", "q.jpg"]) == [
        "p.jpg", "P (2).JPG", "p (3).jpg", "q.jpg"]


@pytest.mark.parametrize("name", [
    "evil.exe", "evil.exe.", "evil.exe ", "evil.bat::$DATA", "sub/evil.cmd.",
    "x.pyc", "x.rtf", "x.html", "x.svg", "x.lnk", "README", "x.docx",
])
def test_open_with_refuses_unlisted_types(name):
    assert openable_temp_name(name) is None


@pytest.mark.parametrize("name, expected", [
    ("note.txt", "note.txt"),
    ("note.txt.", "note.txt"),
    ("sub/read me.PDF", "sub/read me.PDF"),
    ("../../x.txt", "x.txt"),
    ("a:b.txt", "a_b.txt"),
])
def test_open_with_accepts_listed_types_under_their_written_name(name, expected):
    assert openable_temp_name(name) == expected


def test_open_with_warns_when_the_path_cannot_be_written(tmp_path, monkeypatch):
    """Chemin de milliers de dossiers (RecursionError dans os.makedirs) :
    avertissement, rien d'ouvert, au lieu d'une exception avalée par le
    double-clic."""
    from modules.qt import open_with_default_app_qt as owd
    warned = []
    monkeypatch.setattr(owd, "get_mosaicview_temp_dir", lambda: str(tmp_path))
    monkeypatch.setattr(owd, "_warn_unsafe_path", lambda parent: warned.append(parent))
    monkeypatch.setattr(owd.os, "startfile", lambda path: pytest.fail("fichier ouvert"), raising=False)
    owd.open_file_with_default_app({"orig_name": "d/" * 3000 + "x.txt", "bytes": b"x"}, parent="p")
    assert warned == ["p"]


def _startfile_failing(monkeypatch, tmp_path, winerror, openas_fails=False):
    """Remplace os.startfile : la première ouverture échoue avec `winerror`
    (le choix d'application « openas » aussi si `openas_fails`). Rend les
    appels reçus et les avertissements affichés."""
    from modules.qt import open_with_default_app_qt as owd
    calls, warned = [], []

    def startfile(path, operation=None):
        calls.append(operation)
        if operation is None or openas_fails:
            raise OSError(0, "refus", None, winerror)
    monkeypatch.setattr(owd, "get_mosaicview_temp_dir", lambda: str(tmp_path))
    monkeypatch.setattr(owd, "_warn_open_failed", lambda parent: warned.append(parent))
    monkeypatch.setattr(owd.os, "startfile", startfile, raising=False)
    owd.open_file_with_default_app({"orig_name": "note.sfv", "bytes": b"x"}, parent="p")
    return calls, warned


@pytest.mark.skipif(sys.platform != "win32", reason="code d'erreur Windows")
def test_open_with_offers_the_windows_app_choice_without_association(tmp_path, monkeypatch):
    calls, warned = _startfile_failing(monkeypatch, tmp_path, 1155)
    assert calls == [None, "openas"]
    assert warned == []


@pytest.mark.skipif(sys.platform != "win32", reason="code d'erreur Windows")
def test_open_with_warns_when_windows_cannot_open_the_file(tmp_path, monkeypatch):
    calls, warned = _startfile_failing(monkeypatch, tmp_path, 5)
    assert calls == [None]
    assert warned == ["p"]
    calls, warned = _startfile_failing(monkeypatch, tmp_path, 1155, openas_fails=True)
    assert calls == [None, "openas"]
    assert warned == ["p"]


@pytest.mark.skipif(sys.platform != "win32", reason="jonctions NTFS")
def test_walk_does_not_enter_junctions(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (root / "real").mkdir(parents=True)
    outside.mkdir()
    (root / "real" / "a.jpg").write_bytes(b"a")
    (outside / "secret.jpg").write_bytes(b"s")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "link"), str(outside)],
                   check=True, capture_output=True)
    try:
        found = {f for _d, _s, files in walk_without_links(str(root)) for f in files}
        assert found == {"a.jpg"}
    finally:
        os.rmdir(root / "link")   # retire la jonction seule, jamais sa cible


def _page_attr_int():
    return ast_loader.load("tabs_qt.py", {"_page_attr_int"})["_page_attr_int"]


@pytest.mark.parametrize("value, expected", [
    ("12", 12), ("", 0), (None, 0), ("abc", 0), ("1.5", 0), ("  7 ", 7),
])
def test_page_attribute_sort_value(value, expected):
    assert _page_attr_int()(value) == expected
