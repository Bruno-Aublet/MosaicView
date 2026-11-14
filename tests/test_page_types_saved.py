"""Type de page et double page, enregistrés puis rouverts : chaque marque
revient sur sa page.

Aller-retour complet sur un vrai fichier :
- ouverture par le lecteur commun (archive_members.open_archive, ordre de
  l'ouverture) et lecture du ComicInfo.xml (sync_comic_metadata) ;
- marques posées comme par le tableau Pages (comic_info.set_page_marks) ;
- enregistrement dans le fichier ouvert par l'écriture d'un panneau
  (file_operations_qt._write_document_with_progress, avec l'écrivain commun
  converted_cbz.write_cbz) ;
- réouverture du fichier écrit, de la même façon.

Vérifié : les marques posées, retirées, ou posées après un déplacement de
pages (le fichier est écrit dans l'ordre des noms, celui de la réouverture)
se retrouvent sur leur page ; le contenu de <Pages> qu'un autre logiciel y
avait mis (sous-balises d'une <Page>, <Page> sans page, autre balise) est
encore là après la réouverture.

file_operations_qt.py importe PySide6 : les fonctions sont extraites par AST
(tests/ast_loader.py) ; le signal des métadonnées est remplacé, et la clé de
tri de la réouverture est la vraie (archive_names.natural_key).
"""
import io
import os
import sys
import types
import zipfile

import pytest
from PIL import Image

import ast_loader
from modules.qt.archive_members import open_archive
from modules.qt.archive_names import IMAGE_EXTS, JPEG_EXTS, natural_key
from modules.qt.comic_info import (described_pages, page_entries, pages_section_numbers_each_page,
                                   parse_comic_info_xml, set_page_marks, sync_comic_metadata,
                                   sync_pages_in_xml_data)
from modules.qt.converted_cbz import write_cbz
from modules.qt.entries import open_page_image, save_image_to_bytes
from modules.qt.image_ops import has_several_frames
from modules.qt.state import AppState
from modules.qt.undo_redo import history_top
from modules.qt.utils import MissingPageDataError, cbz_members, estimate_cbz_size

write_document = ast_loader.load(
    "file_operations_qt.py",
    {"_DocumentChangedDuringSave", "_write_document_with_progress", "_write_document_unlocked",
     "_comic_info_replacement", "_write_zip_with_progress",
     "_bytes_with_entry_dpi", "_DPI_WRITABLE_EXTS", "_JPEG_EXTS", "jpeg_bytes_with_dpi"},
    os=os, io=io, zipfile=zipfile, Image=Image, JPEG_EXTS=JPEG_EXTS,
    history_top=history_top, write_cbz=write_cbz, estimate_cbz_size=estimate_cbz_size,
    cbz_members=cbz_members,
    MissingPageDataError=MissingPageDataError, open_page_image=open_page_image,
    save_image_to_bytes=save_image_to_bytes,
    has_several_frames=has_several_frames)["_write_document_with_progress"]


@pytest.fixture(autouse=True)
def without_qt(monkeypatch):
    signals = types.ModuleType("modules.qt.metadata_signal")
    signals.metadata_signal = signals.metadata_pages_signal = signals.comic_info_backup_signal = \
        types.SimpleNamespace(emit=lambda *args: None)
    monkeypatch.setitem(sys.modules, "modules.qt.metadata_signal", signals)
    loader = types.ModuleType("modules.qt.archive_loader")
    loader._natural_sort_key = natural_key
    monkeypatch.setitem(sys.modules, "modules.qt.archive_loader", loader)


XML = (b'<?xml version="1.0"?>\r\n<ComicInfo>\r\n  <Title>Aaaa</Title>\r\n  <Pages>\r\n'
       b'    <Page Image="0" Type="FrontCover" />\r\n    <Page Image="1" />\r\n'
       b'    <Page Image="2" />\r\n  </Pages>\r\n</ComicInfo>\r\n')

# Écrit par un autre logiciel : sous-balise dans une <Page>, <Page> au-delà de
# la dernière page, autre balise que <Page>
FOREIGN_XML = (b'<ComicInfo><Title>Aaaa</Title><Pages>'
               b'<Page Image="0" Type="FrontCover"/>'
               b'<Page Image="1"><Note>Bbbb</Note></Page>'
               b'<Page Image="2"/>'
               b'<Page Image="7" Type="Story"/>'
               b'<Other Cccc="1"/>'
               b'</Pages></ComicInfo>')

COLORS = {"01.png": "red", "02.png": "green", "03.png": "blue"}


def png(color):
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(buf, "PNG")
    return buf.getvalue()


def comic_file(path, xml=XML):
    with zipfile.ZipFile(path, "w") as zf:
        for name, color in COLORS.items():
            zf.writestr(name, png(color))
        zf.writestr("ComicInfo.xml", xml)
    return path


def opened(path):
    """Document tel qu'à l'ouverture : membres dans l'ordre du lecteur commun,
    métadonnées lues, non modifié."""
    st = AppState()
    with open_archive(str(path), "CBZ") as archive:
        members = [(m.name, archive.read(m)) for m in archive.members if not m.is_dir]
    st.images_data = [{"orig_name": name, "bytes": data, "extension": os.path.splitext(name)[1],
                       "is_image": os.path.splitext(name)[1].lower() in IMAGE_EXTS,
                       "is_dir": False}
                      for name, data in members]
    st.current_file = str(path)
    sync_comic_metadata(st, force=True)
    st.modified = False
    return st


def saved_and_reopened(st):
    assert write_document(st.current_file, st, None, 0) is True
    return opened(st.current_file)


def page(st, name):
    return next(e for e in page_entries(st) if e["orig_name"] == name)


def marks(st):
    """{nom de page: Type et DoublePage lus pour elle}."""
    attrs = st._page_attrs_by_entry_id
    return {e["orig_name"]: {k: v for k, v in attrs.get(id(e), {}).items()
                             if k in ("Type", "DoublePage")}
            for e in page_entries(st)}


def comic_info_bytes(st):
    return next(e["bytes"] for e in st.images_data if e["orig_name"] == "ComicInfo.xml")


def test_marks_come_back_on_their_pages(tmp_path):
    st = opened(comic_file(tmp_path / "Aaaa.cbz"))
    assert set_page_marks(st, [page(st, "02.png")], page_type="Letters")
    assert set_page_marks(st, [page(st, "02.png"), page(st, "03.png")], double_page=True)
    reopened = saved_and_reopened(st)
    assert marks(reopened) == {"01.png": {"Type": "FrontCover"},
                               "02.png": {"Type": "Letters", "DoublePage": "true"},
                               "03.png": {"DoublePage": "true"}}
    assert [e["bytes"] for e in page_entries(reopened)] == [png(c) for c in COLORS.values()]
    assert pages_section_numbers_each_page(reopened)


def test_removed_marks_stay_removed(tmp_path):
    st = opened(comic_file(tmp_path / "Aaaa.cbz"))
    set_page_marks(st, [page(st, "03.png")], double_page=True)
    reopened = saved_and_reopened(st)
    assert set_page_marks(reopened, [page(reopened, "01.png")], page_type=None)
    assert set_page_marks(reopened, [page(reopened, "03.png")], double_page=False)
    again = saved_and_reopened(reopened)
    assert marks(again) == {"01.png": {}, "02.png": {}, "03.png": {}}


def test_marks_follow_pages_moved_before_saving(tmp_path):
    """Page 03 glissée en tête sans renumérotation, puis marquée : le fichier
    décrit les pages dans l'ordre des noms, que la réouverture rétablit."""
    st = opened(comic_file(tmp_path / "Aaaa.cbz"))
    info = next(e for e in st.images_data if e["orig_name"] == "ComicInfo.xml")
    first, second, third = page_entries(st)
    st.images_data = [info, third, first, second]
    sync_pages_in_xml_data(st, emit_signal=False)
    assert set_page_marks(st, [third], page_type="Letters")
    assert set_page_marks(st, [first], double_page=True)
    reopened = saved_and_reopened(st)
    assert [e["orig_name"] for e in page_entries(reopened)] == ["01.png", "02.png", "03.png"]
    assert marks(reopened) == {"01.png": {"Type": "FrontCover", "DoublePage": "true"},
                               "02.png": {},
                               "03.png": {"Type": "Letters"}}


def test_other_software_content_of_pages_survives_save_and_reopen(tmp_path):
    st = opened(comic_file(tmp_path / "Aaaa.cbz", FOREIGN_XML))
    assert set_page_marks(st, [page(st, "03.png")], page_type="Letters")
    reopened = saved_and_reopened(st)
    xml = comic_info_bytes(reopened)
    assert b"<Note>Bbbb</Note>" in xml
    assert b'<Other Cccc="1"' in xml
    assert {"Image": "7", "Type": "Story"} in parse_comic_info_xml(xml)["pages"]
    assert marks(reopened) == {"01.png": {"Type": "FrontCover"}, "02.png": {},
                               "03.png": {"Type": "Letters"}}
    # La <Page> sans page est gardée dans le fichier, jamais montrée comme une page
    assert [p["Image"] for p in described_pages(reopened)] == ["0", "1", "2"]


def test_saving_again_without_change_writes_the_same_comic_info(tmp_path):
    st = opened(comic_file(tmp_path / "Aaaa.cbz", FOREIGN_XML))
    set_page_marks(st, [page(st, "02.png")], double_page=True)
    reopened = saved_and_reopened(st)
    first = comic_info_bytes(reopened)
    assert comic_info_bytes(saved_and_reopened(reopened)) == first
