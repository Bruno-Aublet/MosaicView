"""Type de page et double page (attributs Type et DoublePage de <Page> du
ComicInfo.xml), posés depuis le menu Images → Type de page ou le tableau Pages
de l'onglet Métadonnées (comic_info.set_page_marks, page_marks_summary).

Vérifié :
- seules les pages choisies changent, et seulement l'attribut demandé : les
  autres attributs (dimensions, signet, attributs d'autres logiciels) et les
  autres pages restent tels quels, couvertures comprises (plusieurs pages
  peuvent être des couvertures) ;
- un groupe de pages reçoit le même type en une fois ;
- la double page se pose et se retire ; l'effacement retire type et double page ;
- <Pages> est créé quand le ComicInfo.xml n'en a pas ;
- sans changement, rien n'est réécrit ;
- l'état d'un groupe (toutes / une partie / aucune) pour le menu.

Le signal des métadonnées est remplacé : aucun Qt.
"""
import io
import os
import sys
import types

import pytest
from PIL import Image

from modules.qt.comic_info import (PAGE_TYPES, build_page_attrs_map, page_entries,
                                   page_entries_at, page_marks_summary, page_types_editable,
                                   parse_comic_info_xml, selected_page_entries, set_page_marks)
from modules.qt.state import AppState


@pytest.fixture(autouse=True)
def without_qt(monkeypatch):
    monkeypatch.setitem(sys.modules, "modules.qt.metadata_signal",
                        types.ModuleType("modules.qt.metadata_signal"))


XML = (b'<ComicInfo><Title>Aaaa</Title><PageCount>3</PageCount><Pages>'
       b'<Page Image="0" Type="FrontCover" ImageWidth="8" ImageHeight="6" ImageSize="1"/>'
       b'<Page Image="1" DoublePage="true" ImageWidth="8" ImageHeight="6" ImageSize="1"/>'
       b'<Page Image="2" Bookmark="Bbbb" Key="Cccc" ImageWidth="8" ImageHeight="6" ImageSize="1"/>'
       b'</Pages></ComicInfo>')

XML_WITHOUT_PAGES = b'<ComicInfo><Title>Aaaa</Title></ComicInfo>'


def page(name):
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), "white").save(buf, "PNG")
    return {"orig_name": name, "bytes": buf.getvalue(),
            "extension": os.path.splitext(name)[1], "is_image": True, "is_dir": False}


class Document:
    def __init__(self, xml=XML):
        self.pages = [page("01.png"), page("02.png"), page("03.png")]
        self.info = {"orig_name": "ComicInfo.xml", "bytes": xml, "extension": ".xml",
                     "is_image": False, "is_dir": False}
        self.folder = {"orig_name": "Dddd/", "is_image": False, "is_dir": True}
        self.state = AppState()
        self.state.images_data = [self.info, self.folder] + self.pages
        self.state.comic_metadata = parse_comic_info_xml(xml)
        build_page_attrs_map(self.state)

    def written(self):
        """<Page> du ComicInfo.xml réécrit, par position."""
        return {int(p["Image"]): p for p in parse_comic_info_xml(self.info["bytes"])["pages"]}


def test_page_types_are_those_of_the_comicinfo_standard():
    assert sorted(PAGE_TYPES) == sorted((
        "FrontCover", "InnerCover", "Roundup", "Story", "Advertisement", "Editorial",
        "Letters", "Preview", "BackCover", "Other", "Deleted"))


def test_type_of_one_page_changes_only_that_attribute_of_that_page():
    doc = Document()
    assert set_page_marks(doc.state, [doc.pages[2]], page_type="Letters")
    pages = doc.written()
    assert pages[2] == {"Image": "2", "Type": "Letters", "Bookmark": "Bbbb", "Key": "Cccc",
                        "ImageWidth": "8", "ImageHeight": "6", "ImageSize": "1"}
    assert pages[0]["Type"] == "FrontCover"
    assert pages[1]["DoublePage"] == "true" and "Type" not in pages[1]
    assert doc.state.modified
    assert doc.state.comic_metadata["pages"][2]["Type"] == "Letters"
    assert parse_comic_info_xml(doc.info["bytes"])["title"] == "Aaaa"


def test_a_second_cover_leaves_the_first_one():
    doc = Document()
    set_page_marks(doc.state, [doc.pages[1]], page_type="FrontCover")
    pages = doc.written()
    assert pages[0]["Type"] == "FrontCover" and pages[1]["Type"] == "FrontCover"


def test_group_of_pages_gets_the_same_type():
    doc = Document()
    set_page_marks(doc.state, doc.pages[1:], page_type="Advertisement")
    pages = doc.written()
    assert pages[1]["Type"] == pages[2]["Type"] == "Advertisement"
    assert pages[0]["Type"] == "FrontCover"
    types_count, _doubles = page_marks_summary(doc.state, doc.pages[1:])
    assert types_count == {"Advertisement": 2}


def test_new_type_replaces_the_old_one():
    doc = Document()
    set_page_marks(doc.state, [doc.pages[0]], page_type="BackCover")
    assert doc.written()[0]["Type"] == "BackCover"


def test_empty_type_removes_the_type_only():
    doc = Document()
    set_page_marks(doc.state, [doc.pages[0]], page_type="")
    assert doc.written()[0] == {"Image": "0", "ImageWidth": "8", "ImageHeight": "6",
                                "ImageSize": "1"}


def test_double_page_is_set_and_removed():
    doc = Document()
    set_page_marks(doc.state, doc.pages, double_page=True)
    assert all(p["DoublePage"] == "true" for p in doc.written().values())
    set_page_marks(doc.state, doc.pages[:2], double_page=False)
    pages = doc.written()
    assert "DoublePage" not in pages[0] and "DoublePage" not in pages[1]
    assert pages[2]["DoublePage"] == "true"


def test_clearing_removes_type_and_double_page_and_keeps_the_rest():
    doc = Document()
    set_page_marks(doc.state, [doc.pages[2]], page_type="Other", double_page=True)
    set_page_marks(doc.state, doc.pages, page_type="", double_page=False)
    pages = doc.written()
    assert all("Type" not in p and "DoublePage" not in p for p in pages.values())
    assert pages[2]["Bookmark"] == "Bbbb" and pages[2]["Key"] == "Cccc"


def test_nothing_to_change_rewrites_nothing():
    doc = Document()
    doc.state.modified = False
    assert not set_page_marks(doc.state, [doc.pages[0]], page_type="FrontCover")
    assert not set_page_marks(doc.state, [doc.pages[1]], double_page=True)
    assert doc.info["bytes"] == XML and not doc.state.modified


def test_pages_section_is_created_when_missing():
    doc = Document(XML_WITHOUT_PAGES)
    assert "pages" not in doc.state.comic_metadata
    assert set_page_marks(doc.state, [doc.pages[0]], page_type="FrontCover")
    pages = doc.written()
    assert sorted(pages) == [0, 1, 2]
    assert pages[0]["Type"] == "FrontCover" and pages[0]["ImageWidth"] == "8"
    assert "Type" not in pages[1] and pages[1]["ImageHeight"] == "6"
    assert parse_comic_info_xml(doc.info["bytes"])["page_count"] == "3"


def test_without_comicinfo_nothing_happens():
    doc = Document()
    doc.state.images_data.remove(doc.info)
    doc.state.comic_metadata = {}
    assert not page_types_editable(doc.state)
    assert not set_page_marks(doc.state, doc.pages, page_type="Story")


def test_summary_of_a_mixed_group():
    doc = Document()
    types_count, doubles = page_marks_summary(doc.state, doc.pages)
    assert types_count == {"FrontCover": 1} and doubles == 1
    types_count, doubles = page_marks_summary(doc.state, [doc.pages[2]])
    assert types_count == {} and doubles == 0


def test_menu_label_says_how_many_pages_have_the_mark():
    """Toutes les pages : coche. Une partie : « (2 sur 5) », en clair (un
    symbole comme ◐ est illisible à la taille des menus). Aucune : rien."""
    import ast_loader
    label = ast_loader.load(
        "menubar_qt.py", {"_page_mark_label", "_ALL_PAGES_MARK", "_NO_MARK"},
        _translate=lambda key, **kw: f"{kw['label']} ({kw['count']}/{kw['total']})")["_page_mark_label"]
    assert label("Aaaa", 5, 5) == "✓ Aaaa"
    assert label("Aaaa", 2, 5) == "   Aaaa (2/5)"
    assert label("Aaaa", 0, 5) == "   Aaaa"


def test_pages_are_the_images_outside_folders_and_comicinfo():
    doc = Document()
    assert page_entries(doc.state) == doc.pages
    assert page_entries_at(doc.state, [2, 0, 7]) == [doc.pages[0], doc.pages[2]]
    doc.state.selected_indices = {0, 1, 3}
    assert selected_page_entries(doc.state) == [doc.pages[1]]
