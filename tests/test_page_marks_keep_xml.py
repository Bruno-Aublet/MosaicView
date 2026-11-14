"""Type de page posé (comic_info.set_page_marks) : rien d'autre ne change
dans la section <Pages> du ComicInfo.xml.

Un ComicInfo.xml écrit par un autre logiciel peut contenir dans <Pages> :
- une <Page> qui ne désigne aucune page du comic (au-delà de la dernière
  page, ou numéro illisible) ;
- des sous-balises dans une <Page> ;
- une autre balise que <Page>.
Poser le type d'une page les garde tels quels ; avant, toute la section était
recréée à partir des seules pages, et ils disparaissaient (une autre balise
était même renommée <Page>). La réécriture du fichier (_serialize_comic_xml)
les écrit fidèlement, et deux réécritures donnent les mêmes octets.

Sans Qt : le signal des métadonnées est remplacé.
"""
import io
import os
import sys
import types

import pytest
from PIL import Image

from modules.qt.comic_info import (_comic_fromstring, _serialize_comic_xml, described_pages,
                                   pages_section_numbers_each_page, parse_comic_info_xml,
                                   set_page_marks, sync_comic_metadata)
from modules.qt.state import AppState


@pytest.fixture(autouse=True)
def without_qt(monkeypatch):
    fake = types.ModuleType("modules.qt.metadata_signal")
    fake.metadata_signal = fake.metadata_pages_signal = fake.comic_info_backup_signal = \
        types.SimpleNamespace(emit=lambda *args: None)
    monkeypatch.setitem(sys.modules, "modules.qt.metadata_signal", fake)


def page(name):
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), "white").save(buf, "PNG")
    return {"orig_name": name, "bytes": buf.getvalue(), "extension": os.path.splitext(name)[1],
            "is_image": True, "is_dir": False}


def document(pages_xml):
    st = AppState()
    xml = b"<ComicInfo><Title>Aaaa</Title><Pages>" + pages_xml + b"</Pages></ComicInfo>"
    info = {"orig_name": "ComicInfo.xml", "bytes": xml, "extension": ".xml",
            "is_image": False, "is_dir": False}
    st.images_data = [info, page("01.png"), page("02.png")]
    sync_comic_metadata(st, force=True)
    return st, info


def pages_after(info):
    root = _comic_fromstring(info["bytes"])
    return list(root.find("Pages"))


def test_page_beyond_the_last_page_is_kept():
    st, info = document(b'<Page Image="0"/><Page Image="1"/><Page Image="7" Type="Story"/>')
    assert set_page_marks(st, [st.images_data[1]], page_type="FrontCover")
    elems = pages_after(info)
    assert [e.get("Image") for e in elems] == ["0", "1", "7"]
    assert elems[0].get("Type") == "FrontCover" and elems[2].get("Type") == "Story"


def test_page_with_an_unreadable_number_is_kept():
    st, info = document(b'<Page Image="0"/><Page Image="1"/><Page Image="x" Key="Bbbb"/>')
    assert set_page_marks(st, [st.images_data[2]], page_type="BackCover")
    assert [e.get("Image") for e in pages_after(info)] == ["0", "1", "x"]


def test_sub_elements_of_a_page_are_kept():
    st, info = document(b'<Page Image="0"><Note lang="aa">Bbbb</Note></Page><Page Image="1"/>')
    assert set_page_marks(st, [st.images_data[1]], page_type="FrontCover")
    first = pages_after(info)[0]
    assert first.get("Type") == "FrontCover"
    assert [(c.tag, c.get("lang"), c.text) for c in first] == [("Note", "aa", "Bbbb")]


def test_sub_elements_of_another_page_are_kept():
    st, info = document(b'<Page Image="0"/><Page Image="1"><Note>Bbbb</Note></Page>')
    assert set_page_marks(st, [st.images_data[1]], page_type="FrontCover")
    assert [c.text for c in pages_after(info)[1]] == ["Bbbb"]


def test_other_tag_in_pages_is_kept_and_never_renamed():
    st, info = document(b'<Page Image="0"/><Page Image="1"/><Extra a="1">Cccc</Extra>')
    assert set_page_marks(st, [st.images_data[1]], double_page=True)
    elems = pages_after(info)
    assert [e.tag for e in elems] == ["Page", "Page", "Extra"]
    assert elems[2].get("a") == "1" and elems[2].text == "Cccc"


def test_other_tag_in_pages_is_not_read_as_a_page():
    """Le tableau Pages de l'onglet Métadonnées affichait une ligne vide pour
    elle, et la comptait dans le nombre de pages de la section."""
    meta = parse_comic_info_xml(b'<ComicInfo><Pages><Page Image="0"/><Page Image="7" Type="Story"/>'
                                b'<Extra a="1">Cccc</Extra></Pages></ComicInfo>')
    assert meta["pages"] == [{"Image": "0"}, {"Image": "7", "Type": "Story"}]


def test_page_list_in_memory_follows_the_rewritten_file():
    """comic_metadata['pages'] est, après le type posé, ce que donnerait la
    relecture du fichier, <Page> gardée comprise."""
    st, info = document(b'<Page Image="0"/><Page Image="1"/><Page Image="7" Type="Story"/>')
    set_page_marks(st, [st.images_data[1]], page_type="FrontCover")
    assert [p["Image"] for p in st.comic_metadata["pages"]] == \
        [p["Image"] for p in parse_comic_info_xml(info["bytes"])["pages"]]


def test_page_list_of_the_tab_shows_only_the_pages_of_the_comic():
    """Tableau Pages (described_pages) : une ligne par page du comic, jamais
    une <Page> au-delà de la dernière page ni de numéro illisible."""
    st, _info = document(b'<Page Image="0"/><Page Image="1"/><Page Image="7" Type="Story"/>'
                         b'<Page Image="x"/>')
    assert [p["Image"] for p in described_pages(st)] == ["0", "1"]


def test_kept_elements_do_not_make_the_pages_out_of_step():
    """Annuler/Refaire (undo_redo_qt.restore_state_qt) ne resynchronise
    <Pages> que s'il ne numérote plus chaque page : une <Page> gardée hors
    limites ou de numéro illisible n'y compte pas."""
    st, _info = document(b'<Page Image="0"/><Page Image="1"/><Page Image="x"/><Page Image="7"/>')
    assert pages_section_numbers_each_page(st)


def test_rewriting_twice_gives_the_same_bytes():
    xml = (b'<ComicInfo><Pages><Page Image="0" Type="Story"><Note lang="aa">Bbbb</Note>'
           b'<Sub><Deep>Cccc</Deep></Sub></Page><Page Image="1">Dddd <b>Eeee</b></Page>'
           b'<Extra a="1"/></Pages></ComicInfo>')
    once = _serialize_comic_xml(_comic_fromstring(xml), xml)
    twice = _serialize_comic_xml(_comic_fromstring(once), once)
    assert once == twice
    pages = list(_comic_fromstring(once).find("Pages"))
    assert pages[0].find("Sub/Deep").text == "Cccc"
    assert "".join(pages[1].itertext()) == "Dddd Eeee"
    assert pages[2].tag == "Extra"


def test_empty_page_stays_self_closing():
    xml = b'<ComicInfo><Pages><Page ImageWidth="8" Image="0"/></Pages></ComicInfo>'
    out = _serialize_comic_xml(_comic_fromstring(xml), xml)
    assert b'    <Page Image="0" ImageWidth="8" />' in out
