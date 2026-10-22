"""Import des métadonnées en lot : le ComicInfo.xml d'un CBZ est le fichier
nommé exactement ainsi, à la racine (Audit 7, C-5).

_BatchMetadataOrchestrator._load_state_for_file pré-remplit la fenêtre
ComicVine avec les métadonnées déjà présentes. Il prenait le premier fichier
dont le nom finissait par .xml et contenait « comicinfo » : un
ComicInfo_0002.xml, un dossier/ComicInfo.xml ou une copie de sauvegarde
placés avant le vrai devenaient les métadonnées de la B.D. Règle commune :
comic_info.is_comic_info_name.

batch_metadata_dialog_qt.py importe Qt : la méthode est extraite par AST,
archive_loader (Qt) remplacé par ses vraies fonctions de taille.
"""
import ast
import os
import sys
import types
import zipfile
from pathlib import Path

import pytest

from modules.qt.archive_names import open_zip

ROOT = Path(__file__).resolve().parent.parent


def _load():
    source = (ROOT / "modules" / "qt" / "batch_metadata_dialog_qt.py").read_text(encoding="utf-8")
    orchestrator = next(n for n in ast.parse(source).body
                        if isinstance(n, ast.ClassDef) and n.name == "_BatchMetadataOrchestrator")
    method = next(n for n in orchestrator.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_load_state_for_file")
    cls = ast.ClassDef(name="Orchestrator", bases=[], keywords=[], decorator_list=[], body=[method])
    ns = {"os": os, "open_zip": open_zip, "_natural_sort_key": lambda s: s.lower()}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                 "batch_metadata_dialog_qt.py", "exec"), ns)
    return ns["Orchestrator"]


@pytest.fixture(autouse=True)
def fake_archive_loader(monkeypatch):
    source = (ROOT / "modules" / "qt" / "archive_loader.py").read_text(encoding="utf-8")
    nodes = [n for n in ast.parse(source).body
             if isinstance(n, ast.FunctionDef) and n.name == "_check_member_size"]
    from modules.qt.archive_names import MAX_MEMBER_SIZE
    fake = types.ModuleType("modules.qt.archive_loader")
    fake.MAX_MEMBER_SIZE = MAX_MEMBER_SIZE
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "archive_loader.py", "exec"), fake.__dict__)
    monkeypatch.setitem(sys.modules, "modules.qt.archive_loader", fake)


def _xml(series):
    return f'<?xml version="1.0"?><ComicInfo><Series>{series}</Series></ComicInfo>'.encode()


def _cbz(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
    return str(path)


def test_annex_files_before_the_real_comicinfo_are_not_taken(tmp_path):
    path = _cbz(tmp_path / "a.cbz", [
        ("Bonus/ComicInfo.xml", _xml("Bonus")),
        ("ComicInfo_0002.xml", _xml("Seconde")),
        ("ancien_comicinfo.xml", _xml("Sauvegarde")),
        ("page1.jpg", b"jpg"),
        ("ComicInfo.xml", _xml("Vraie")),
    ])
    _cbz_path, state = _load()()._load_state_for_file(path)
    assert state.comic_metadata["series"] == "Vraie"
    xml_entries = [e["orig_name"] for e in state.images_data if e["orig_name"].endswith(".xml")]
    assert xml_entries == ["ComicInfo.xml"]


def test_cbz_with_only_annex_files_has_no_metadata(tmp_path):
    path = _cbz(tmp_path / "b.cbz", [
        ("page1.jpg", b"jpg"),
        ("Bonus/ComicInfo.xml", _xml("Bonus")),
        ("ComicInfo_0002.xml", _xml("Seconde")),
    ])
    _cbz_path, state = _load()()._load_state_for_file(path)
    assert state.comic_metadata == {}
    assert not [e for e in state.images_data if e["orig_name"].endswith(".xml")]


def test_comicinfo_name_case_is_ignored_like_comic_readers(tmp_path):
    path = _cbz(tmp_path / "c.cbz", [("page1.jpg", b"jpg"), ("comicinfo.XML", _xml("Casse"))])
    _cbz_path, state = _load()()._load_state_for_file(path)
    assert state.comic_metadata["series"] == "Casse"
