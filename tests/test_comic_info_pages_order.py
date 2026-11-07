"""<Pages> du ComicInfo.xml et ordre des pages : <Page Image="N"> désigne une
position, et la réouverture d'un CBZ trie les pages par nom.

Vérifié :
- après un tri ou une renumérotation, <Pages> suit l'ordre affiché, avant le
  point d'historique ;
- le fichier écrit range <Pages> dans l'ordre des noms, celui de la réouverture,
  même si l'ordre affiché en diffère, sans rien changer au document en mémoire ;
- un <Pages> déjà correct est écrit à l'identique ;
- une sélection enregistrée ne décrit que ses pages, sans toucher au document ;
- une page supprimée ne laisse aucune information dont hériterait une page
  arrivant à la même adresse mémoire.

file_operations_qt.py et archive_loader.py importent PySide6 : les fonctions
sont extraites par AST (tests/ast_loader.py), la vraie clé de tri de la
réouverture comprise ; le signal des métadonnées est remplacé.
"""
import io
import os
import re
import sys
import types
import zipfile

import pytest
from PIL import Image

from modules.qt.archive_names import JPEG_EXTS
from modules.qt.comic_info import build_page_attrs_map, parse_comic_info_xml, sync_pages_in_xml_data
from modules.qt.entries import open_page_image, save_image_to_bytes
from modules.qt.image_ops import has_several_frames
from modules.qt.renumbering import renumber_pages, renumber_pages_auto
from modules.qt.sorting import sort_images
from modules.qt.state import AppState
from modules.qt.undo_redo import history_top
from modules.qt.utils import (MissingPageDataError, cbz_members, check_written_cbz_structure,
                              replace_file_atomically)

import ast_loader
from fakes import Signal

NATURAL_KEY = ast_loader.load("archive_loader.py", {"_natural_sort_key"},
                              os=os, re=re)["_natural_sort_key"]

write_document = ast_loader.load(
    "file_operations_qt.py",
    {"_DocumentChangedDuringSave", "_write_document_with_progress", "_write_document_unlocked",
     "_comic_info_replacement", "_write_zip_with_progress", "_zip_comment",
     "_bytes_with_entry_dpi", "_DPI_WRITABLE_EXTS", "_JPEG_EXTS", "jpeg_bytes_with_dpi"},
    os=os, io=io, zipfile=zipfile, Image=Image, JPEG_EXTS=JPEG_EXTS,
    history_top=history_top, replace_file_atomically=replace_file_atomically,
    check_written_cbz_structure=check_written_cbz_structure, cbz_members=cbz_members,
    MissingPageDataError=MissingPageDataError, open_page_image=open_page_image,
    save_image_to_bytes=save_image_to_bytes,
    has_several_frames=has_several_frames)["_write_document_with_progress"]


@pytest.fixture(autouse=True)
def without_qt(monkeypatch):
    signals = types.ModuleType("modules.qt.metadata_signal")
    signals.metadata_signal = Signal()
    monkeypatch.setitem(sys.modules, "modules.qt.metadata_signal", signals)
    loader = types.ModuleType("modules.qt.archive_loader")
    loader._natural_sort_key = NATURAL_KEY
    monkeypatch.setitem(sys.modules, "modules.qt.archive_loader", loader)


XML = (b'<ComicInfo><PageCount>3</PageCount><Pages>'
       b'<Page Image="0" Type="FrontCover" ImageWidth="8" ImageHeight="6" ImageSize="1"/>'
       b'<Page Image="1" DoublePage="true" ImageWidth="8" ImageHeight="6" ImageSize="1"/>'
       b'<Page Image="2" Bookmark="Aaaa" ImageWidth="8" ImageHeight="6" ImageSize="1"/>'
       b'</Pages></ComicInfo>')


def page(name, extra=0):
    """Image de poids réglable (octets ajoutés après la fin du PNG, ignorés)."""
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), "white").save(buf, "PNG")
    return {"orig_name": name, "bytes": buf.getvalue() + b"\0" * extra,
            "extension": os.path.splitext(name)[1], "is_image": True, "is_dir": False}


class Document:
    """Couverture 01 (la plus lourde), double page 02, signet 03."""

    def __init__(self, xml=XML):
        self.cover, self.double, self.marked = page("01.png", 300), page("02.png", 200), page("03.png", 100)
        self.info = {"orig_name": "ComicInfo.xml", "bytes": xml, "extension": ".xml",
                     "is_image": False, "is_dir": False}
        self.state = AppState()
        self.state.images_data = [self.info, self.cover, self.double, self.marked]
        self.state.comic_metadata = parse_comic_info_xml(xml)
        build_page_attrs_map(self.state)
        self.xml_at_save = []

    def callbacks(self):
        return {"state": self.state, "natural_sort_key": NATURAL_KEY,
                "get_image_metadata": lambda entry: None,
                "save_state": lambda: self.xml_at_save.append(self.info["bytes"]),
                "render_mosaic": lambda: None, "update_button_text": lambda: None}

    def sort_lightest_first(self):
        sort_images("weight", self.callbacks())

    def written(self, path, **options):
        write_document(str(path), self.state, None, 0, **options)
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            xml = zf.read("ComicInfo.xml") if "ComicInfo.xml" in names else None
            return {name: zf.read(name) for name in names}, xml


def by_position(xml):
    return {int(p["Image"]): p for p in parse_comic_info_xml(xml)["pages"]}


def in_reopening_order(contents):
    """Octets des pages, dans l'ordre de la réouverture."""
    names = sorted((n for n in contents if n != "ComicInfo.xml"), key=NATURAL_KEY)
    return [contents[n] for n in names]


# --- Tri et renumérotation : l'ordre affiché -----------------------------------

def test_sort_moves_page_information_with_the_pages():
    doc = Document()
    doc.sort_lightest_first()
    pages = by_position(doc.info["bytes"])
    assert pages[0]["Bookmark"] == "Aaaa" and pages[2]["Type"] == "FrontCover"
    assert doc.state.comic_metadata["pages"][2]["Type"] == "FrontCover"


def test_sort_records_the_new_pages_in_the_history_state():
    doc = Document()
    doc.sort_lightest_first()
    assert doc.xml_at_save == [doc.info["bytes"]]


@pytest.mark.parametrize("renumber", [renumber_pages, renumber_pages_auto])
def test_renumbering_keeps_each_information_on_its_page(renumber):
    doc = Document()
    doc.state.images_data = [doc.info, doc.marked, doc.double, doc.cover]   # sans resynchronisation
    renumber(doc.callbacks(), state=doc.state)
    pages = by_position(doc.info["bytes"])
    assert pages[0]["Bookmark"] == "Aaaa" and pages[2]["Type"] == "FrontCover"
    assert doc.xml_at_save[-1] == doc.info["bytes"]


def test_sort_without_pages_list_writes_nothing():
    doc = Document(b"<ComicInfo><Title>Aaaa</Title></ComicInfo>")
    doc.sort_lightest_first()
    assert doc.info["bytes"] == b"<ComicInfo><Title>Aaaa</Title></ComicInfo>"


# --- Fichier écrit : l'ordre de la réouverture ---------------------------------

def test_sort_renumber_save_reopen_keeps_each_information_on_its_page(tmp_path):
    doc = Document()
    doc.sort_lightest_first()
    renumber_pages(doc.callbacks(), state=doc.state)
    contents, xml = doc.written(tmp_path / "Aaaa.cbz")
    pages = by_position(xml)
    reopened = in_reopening_order(contents)
    assert reopened == [doc.marked["bytes"], doc.double["bytes"], doc.cover["bytes"]]
    assert pages[0]["Bookmark"] == "Aaaa" and pages[2]["Type"] == "FrontCover"


def test_sorted_pages_keeping_their_names_are_described_in_name_order(tmp_path):
    doc = Document()
    doc.sort_lightest_first()                                 # renumérotation désactivée
    contents, xml = doc.written(tmp_path / "Aaaa.cbz")
    pages = by_position(xml)
    assert in_reopening_order(contents)[0] == doc.cover["bytes"]
    assert pages[0]["Type"] == "FrontCover" and pages[2]["Bookmark"] == "Aaaa"


def test_writing_changes_nothing_in_the_document(tmp_path):
    doc = Document()
    doc.sort_lightest_first()
    xml_before, pages_before = doc.info["bytes"], list(doc.state.comic_metadata["pages"])
    doc.written(tmp_path / "Aaaa.cbz")
    assert doc.info["bytes"] == xml_before
    assert doc.state.comic_metadata["pages"] == pages_before
    assert by_position(doc.info["bytes"])[0]["Bookmark"] == "Aaaa"     # ordre affiché


def test_order_changed_without_any_resynchronisation_is_still_right(tmp_path):
    doc = Document()
    doc.state.images_data = [doc.info, doc.marked, doc.double, doc.cover]
    _contents, xml = doc.written(tmp_path / "Aaaa.cbz")
    assert by_position(xml)[0]["Type"] == "FrontCover"


def test_right_pages_list_is_written_unchanged(tmp_path):
    doc = Document()
    _contents, xml = doc.written(tmp_path / "Aaaa.cbz")
    assert xml == XML


def test_incomplete_pages_list_already_right_is_written_unchanged(tmp_path):
    partial = (b'<ComicInfo><Pages><Page Image="0" Type="FrontCover"/>'
               b'<Page Image="9" Type="Story"/></Pages></ComicInfo>')
    doc = Document(partial)
    _contents, xml = doc.written(tmp_path / "Aaaa.cbz")
    assert xml == partial


def test_copy_with_the_same_name_stays_after_its_original(tmp_path):
    """Noms égaux sans leur extension : la réouverture garde l'ordre écrit."""
    doc = Document()
    copy = page("01.jpg", 0)
    doc.state.images_data = [doc.info, doc.cover, copy, doc.double, doc.marked]
    doc.state._page_attrs_by_entry_id[id(copy)] = {"ImageWidth": "8"}
    _contents, xml = doc.written(tmp_path / "Aaaa.cbz")
    pages = by_position(xml)
    assert pages[0]["Type"] == "FrontCover" and pages[1] == {"Image": "1", "ImageWidth": "8",
                                                             "ImageSize": str(len(copy["bytes"]))}
    assert pages[2]["DoublePage"] == "true"


def test_saved_selection_describes_only_its_pages(tmp_path):
    doc = Document()
    _contents, xml = doc.written(tmp_path / "Aaaa.cbz", entries=[doc.info, doc.marked])
    pages = by_position(xml)
    assert parse_comic_info_xml(xml)["page_count"] == "1"
    assert list(pages) == [0] and pages[0]["Bookmark"] == "Aaaa"
    assert b"FrontCover" not in xml                        # la couverture n'est pas enregistrée


def test_saved_selection_keeps_each_page_information_in_name_order(tmp_path):
    doc = Document()
    _contents, xml = doc.written(tmp_path / "Aaaa.cbz", entries=[doc.info, doc.marked, doc.cover])
    pages = by_position(xml)
    assert pages[0]["Type"] == "FrontCover" and pages[1]["Bookmark"] == "Aaaa"


def test_saved_selection_keeps_the_other_fields_and_leaves_the_document_alone(tmp_path):
    xml_in = XML.replace(b"<PageCount>", b"<Title>Bbbb</Title><PageCount>")
    doc = Document(xml_in)
    _contents, xml = doc.written(tmp_path / "Aaaa.cbz", entries=[doc.info, doc.marked])
    assert parse_comic_info_xml(xml)["title"] == "Bbbb"
    assert doc.info["bytes"] == xml_in
    assert doc.state.comic_metadata["page_count"] == "3" and doc.state.modified is False
    assert len(doc.state.comic_metadata["pages"]) == 3


def test_saved_selection_without_a_pages_section_corrects_its_page_count(tmp_path):
    doc = Document(b"<ComicInfo><Title>Bbbb</Title><PageCount>3</PageCount></ComicInfo>")
    _contents, xml = doc.written(tmp_path / "Aaaa.cbz", entries=[doc.info, doc.cover, doc.double])
    assert parse_comic_info_xml(xml)["page_count"] == "2"


def test_renumbered_selection_keeps_the_information_of_each_page(tmp_path):
    """Pages renumérotées dans le nouveau fichier : ce sont des copies, reliées à
    leur page d'origine par _selection_source_id."""
    doc = Document()
    copy = dict(doc.marked, orig_name="01.png", _selection_source_id=id(doc.marked))
    contents, xml = doc.written(tmp_path / "Aaaa.cbz", entries=[doc.info, copy])
    assert "01.png" in contents
    assert by_position(xml)[0]["Bookmark"] == "Aaaa"


def test_selection_without_comic_info_writes_none(tmp_path):
    doc = Document()
    _contents, xml = doc.written(tmp_path / "Aaaa.cbz", entries=[doc.marked])
    assert xml is None


def test_removed_page_leaves_no_page_information():
    """Les attributs sont rangés par id(page) : une nouvelle page peut recevoir
    l'adresse d'une page supprimée. Remettre le même objet, donc la même adresse,
    montre qu'elle n'hérite de rien."""
    doc = Document()
    doc.state.images_data.remove(doc.marked)
    sync_pages_in_xml_data(doc.state)
    assert set(doc.state._page_attrs_by_entry_id) == {id(doc.cover), id(doc.double)}

    doc.state.images_data.append(doc.marked)
    sync_pages_in_xml_data(doc.state)
    assert "Bookmark" not in doc.state._page_attrs_by_entry_id[id(doc.marked)]
    assert b"Bookmark" not in doc.info["bytes"]
