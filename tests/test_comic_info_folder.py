"""ComicInfo.xml d'une BD rangé dans le dossier de ses pages (archive faite
en compressant un dossier) : c'est le sien quand il n'y en a pas à la racine,
qu'il est seul et que toutes les pages sont dans ce dossier
(comic_info.comic_info_member_index). Sinon il reste un fichier annexe."""
import os
import sys
import types
import zipfile

import pytest

import ast_loader
from modules.qt import comic_info
from modules.qt.archive_names import open_zip
from modules.qt.comic_info import (
    comic_info_entry,
    comic_info_entry_in,
    comic_info_member_index,
    name_incoming_comic_infos,
    read_comic_info,
    replace_comic_info_bytes,
)


def _xml(series):
    return f'<?xml version="1.0"?><ComicInfo><Series>{series}</Series></ComicInfo>'.encode()


@pytest.mark.parametrize("names, expected", [
    (["ComicInfo.xml", "p01.jpg"], 0),
    (["Aaaa/p01.jpg", "Aaaa/ComicInfo.xml", "Aaaa/p02.jpg"], 1),
    (["Aaaa/", "Aaaa/p01.jpg", "Aaaa/ComicInfo.xml"], 2),
    (["Aaaa/Bbbb/p01.jpg", "Aaaa/ComicInfo.xml"], 1),            # pages d'un sous-dossier
    (["Aaaa\\p01.jpg", "Aaaa\\comicinfo.XML"], 1),                # séparateur et casse
    (["Aaaa/ComicInfo.xml", "Aaaa/p01.jpg", "ComicInfo.xml"], 2),  # la racine l'emporte
    (["Aaaa/ComicInfo.xml", "Aaaa/p01.jpg", "__MACOSX/Aaaa/._p01.jpg"], 0),
])
def test_comic_info_of_the_comic(names, expected):
    assert comic_info_member_index(names) == expected


@pytest.mark.parametrize("names", [
    ["p01.jpg", "Aaaa/ComicInfo.xml"],                               # page hors du dossier
    ["Aaaa/p01.jpg", "Bbbb/p02.jpg", "Aaaa/ComicInfo.xml"],          # pages dans deux dossiers
    ["Aaaa/p01.jpg", "Aaaa/ComicInfo.xml", "Bbbb/ComicInfo.xml"],    # deux candidats
    ["Aaaa/ComicInfo.xml", "Aaaa/notes.txt"],                        # aucune page
    ["Aaaa/p01.jpg", "Aaaa/ComicInfo_0002.xml", "NEW01-ComicInfo.xml"],
    ["Aaaa/p01.jpg", "aaaa/ComicInfo.xml"],                          # dossier d'une autre casse
    [],
])
def test_annex_files_are_not_the_comic_info(names):
    assert comic_info_member_index(names) is None


def _entry(name, data=b"", is_dir=False):
    return {"orig_name": name, "bytes": data, "is_dir": is_dir,
            "is_image": os.path.splitext(name)[1] == ".jpg", "extension": os.path.splitext(name)[1]}


def test_document_comic_info_in_the_pages_folder():
    xml = _entry("Aaaa/ComicInfo.xml", _xml("Cccc"))
    entries = [_entry("Aaaa/", is_dir=True), _entry("Aaaa/p01.jpg"), xml]
    assert comic_info_entry_in(entries) is xml
    st = types.SimpleNamespace(images_data=entries, comic_metadata=None, original_page_count=None,
                               _page_attrs_by_entry_id={})
    assert comic_info_entry(st) is xml
    comic_info.sync_comic_metadata(st, force=True)
    assert st.comic_metadata["series"] == "Cccc"


def test_incoming_root_comic_info_never_replaces_the_folder_one():
    existing = [_entry("Aaaa/p01.jpg"), _entry("Aaaa/ComicInfo.xml", _xml("Cccc"))]
    incoming = _entry("ComicInfo.xml", _xml("Dddd"))
    name_incoming_comic_infos([incoming], existing)
    assert incoming["orig_name"] == "ComicInfo_0002.xml"


def test_backup_of_a_damaged_folder_comic_info_stays_in_its_folder(monkeypatch):
    fake = types.ModuleType("modules.qt.metadata_signal")
    fake.comic_info_backup_signal = types.SimpleNamespace(emit=lambda state: None)
    monkeypatch.setitem(sys.modules, "modules.qt.metadata_signal", fake)
    damaged = b"<ComicInfo><Series>Cccc</Series>"
    xml = _entry("Aaaa/ComicInfo.xml", damaged)
    st = types.SimpleNamespace(images_data=[_entry("Aaaa/p01.jpg"), xml], modified=False)
    assert replace_comic_info_bytes(st, xml, _xml("Cccc")) == "Aaaa/ComicInfo_0002.xml"
    assert st.images_data[2]["bytes"] == damaged
    assert comic_info_entry_in(st.images_data) is xml


def test_library_reads_the_folder_comic_info(tmp_path):
    path = tmp_path / "Aaaa.cbz"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Aaaa/p01.jpg", b"jpg")
        zf.writestr("Aaaa/ComicInfo.xml", _xml("Cccc"))
    assert read_comic_info(str(path))["series"] == "Cccc"


# ── Import des métadonnées en lot ────────────────────────────────────────────

@pytest.fixture
def batch_loader(monkeypatch):
    from modules.qt.archive_names import MAX_MEMBER_SIZE
    fake = types.ModuleType("modules.qt.archive_loader")
    fake.MAX_MEMBER_SIZE = MAX_MEMBER_SIZE
    fake._check_member_size = ast_loader.load("archive_loader.py", {"_check_member_size"},
                                              MAX_MEMBER_SIZE=MAX_MEMBER_SIZE)["_check_member_size"]
    monkeypatch.setitem(sys.modules, "modules.qt.archive_loader", fake)
    return ast_loader.load_methods("batch_metadata_dialog_qt.py", "_BatchMetadataOrchestrator",
                                   {"_load_state_for_file"},
                                   os=os, open_zip=open_zip,
                                   _natural_sort_key=lambda s: s.lower())


def test_batch_prefills_from_the_folder_comic_info(tmp_path, batch_loader):
    path = tmp_path / "Aaaa.cbz"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Aaaa/p01.jpg", b"jpg")
        zf.writestr("Aaaa/ComicInfo.xml", _xml("Cccc"))
    _cbz_path, state = batch_loader()._load_state_for_file(str(path))
    assert state.comic_metadata["series"] == "Cccc"
    assert [e["orig_name"] for e in state.images_data if e["orig_name"].endswith(".xml")] == \
        ["Aaaa/ComicInfo.xml"]
    assert comic_info_entry(state)["orig_name"] == "Aaaa/ComicInfo.xml"
