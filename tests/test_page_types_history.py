"""Type de page et Annuler/Refaire : le ComicInfo.xml et l'indicateur
« modifié » de l'état restauré sont exactement ceux de l'historique
(undo_redo_qt.restore_state_qt, comic_info.pages_section_numbers_each_page).

Un état d'historique garde son ComicInfo.xml tel qu'il était avec ses pages.
Le réécrire à la restauration (sync_pages_in_xml_data) le normalisait
(dimensions ajoutées, <Page> au-delà de la dernière page retirée) et forçait
`modified` : Annuler jusqu'à l'ouverture laissait le document « modifié », et
l'enregistrer écrivait un autre XML que celui du fichier. <Pages> n'est
resynchronisé que s'il ne numérote plus chaque page.

restore_state_qt est extraite par tests/ast_loader.py, avec les vraies
fonctions de l'historique et du ComicInfo.xml ; seuls les vignettes et le
signal des métadonnées (Qt) sont remplacés.
"""
import io
import os
import sys
import types

import pytest
from PIL import Image

import ast_loader
from modules.qt.comic_info import (build_page_attrs_map, has_comic_info_entry,
                                   pages_section_numbers_each_page, parse_comic_info_xml,
                                   set_page_marks, sync_comic_metadata)
from modules.qt.state import AppState
from modules.qt.undo_redo import (adopt_entry_key, entry_key, redo_data, save_state_data,
                                  undo_data)


class _Signal:
    def emit(self, *args):
        pass


@pytest.fixture(autouse=True)
def without_qt(monkeypatch):
    fake = types.ModuleType("modules.qt.metadata_signal")
    fake.metadata_signal = fake.metadata_pages_signal = fake.comic_info_backup_signal = _Signal()
    monkeypatch.setitem(sys.modules, "modules.qt.metadata_signal", fake)


restore_state_qt = ast_loader.load(
    "undo_redo_qt.py", {"restore_state_qt"},
    entry_key=entry_key, adopt_entry_key=adopt_entry_key,
    has_comic_info_entry=has_comic_info_entry, sync_comic_metadata=sync_comic_metadata,
    _reload_thumb_qt=lambda entry, tw, th: None,
    _build_new_entry_qt=lambda data, tw, th: pytest.fail("aucune page à recréer"),
)["restore_state_qt"]


# Tel qu'un autre logiciel l'écrit : pas de dimensions sur la page 2, une
# <Page> au-delà de la dernière page, sans <PageCount>
XML = (b'<?xml version="1.0" encoding="utf-8"?>\n<ComicInfo>\n  <Title>Aaaa</Title>\n'
       b'  <Pages>\n    <Page Image="0" Type="FrontCover" />\n    <Page Image="1" />\n'
       b'    <Page Image="7" Type="Story" />\n  </Pages>\n</ComicInfo>\n')


def page(name):
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), "white").save(buf, "PNG")
    return {"orig_name": name, "bytes": buf.getvalue(),
            "extension": os.path.splitext(name)[1], "is_image": True, "is_dir": False}


def opened_document(xml=XML):
    """Document tel qu'à la fin de l'ouverture : état initial de l'historique,
    non modifié."""
    st = AppState()
    info = {"orig_name": "ComicInfo.xml", "bytes": xml, "extension": ".xml",
            "is_image": False, "is_dir": False}
    st.images_data = [info, page("01.png"), page("02.png")]
    sync_comic_metadata(st, force=True)
    st.modified = False
    save_state_data(st)
    return st, info


def restore(st, saved):
    restore_state_qt(st, saved, lambda: None, lambda: None, lambda: None, lambda: None)


def mark_second_page(st):
    """PanelWidget._set_page_marks : état avant, type posé, état après."""
    save_state_data(st)
    assert set_page_marks(st, [st.images_data[2]], page_type="Letters")
    save_state_data(st)


def test_undo_to_the_opening_gives_back_the_file_xml_unmodified():
    st, info = opened_document()
    mark_second_page(st)
    assert info["bytes"] != XML and st.modified
    restore(st, undo_data(st))
    assert info["bytes"] == XML
    assert not st.modified
    assert st.comic_metadata["pages"][0]["Type"] == "FrontCover"
    assert "Type" not in st.comic_metadata["pages"][1]


def test_redo_gives_back_the_marked_xml_and_the_modified_document():
    st, info = opened_document()
    mark_second_page(st)
    marked = info["bytes"]
    restore(st, undo_data(st))
    restore(st, redo_data(st))
    assert info["bytes"] == marked
    assert st.modified
    assert st.comic_metadata["pages"][1]["Type"] == "Letters"


def test_group_marks_undo_and_redo():
    st, info = opened_document()
    save_state_data(st)
    assert set_page_marks(st, st.images_data[1:], page_type="Advertisement", double_page=True)
    save_state_data(st)
    marked = info["bytes"]
    restore(st, undo_data(st))
    assert info["bytes"] == XML and not st.modified
    restore(st, redo_data(st))
    assert info["bytes"] == marked
    # Les deux pages du comic ; la <Page Image="7"> du fichier, hors limites,
    # est gardée telle quelle (set_page_marks)
    pages = parse_comic_info_xml(info["bytes"])["pages"]
    assert [p["Image"] for p in pages] == ["0", "1", "7"]
    assert all(p["Type"] == "Advertisement" and p["DoublePage"] == "true" for p in pages[:2])
    assert pages[2] == {"Image": "7", "Type": "Story"}


def test_restored_state_is_not_a_new_history_point():
    """Le XML restauré est celui de l'état : l'action suivante ne voit aucun
    changement à enregistrer avant elle."""
    st, _info = opened_document()
    mark_second_page(st)
    restore(st, undo_data(st))
    assert save_state_data(st) is False


def test_pages_numbering_each_page():
    st, _info = opened_document(b'<ComicInfo><Pages><Page Image="0"/><Page Image="1"/>'
                                b'</Pages></ComicInfo>')
    assert pages_section_numbers_each_page(st)


def test_page_beyond_the_last_page_does_not_count():
    """Le fichier ouvert pouvait en avoir une : elle ne désigne aucune page."""
    st, _info = opened_document()
    assert pages_section_numbers_each_page(st)


def test_pages_out_of_step_with_the_pages():
    for xml in (b'<ComicInfo><Pages><Page Image="0"/></Pages></ComicInfo>',
                b'<ComicInfo><Pages><Page Image="1"/><Page Image="0"/></Pages></ComicInfo>',
                b'<ComicInfo><Pages><Page Image="0"/><Page Image="x"/></Pages></ComicInfo>'):
        st, _info = opened_document(xml)
        assert not pages_section_numbers_each_page(st), xml


def test_without_pages_section_there_is_nothing_to_resynchronise():
    st, _info = opened_document(b'<ComicInfo><Title>Aaaa</Title></ComicInfo>')
    assert pages_section_numbers_each_page(st)


def test_restored_pages_out_of_step_are_resynchronised():
    """Un état dont <Pages> ne décrit qu'une partie des pages est encore
    resynchronisé à la restauration : chaque page y reçoit sa <Page>."""
    st, info = opened_document(b'<ComicInfo><Pages><Page Image="0" Type="FrontCover"/>'
                               b'</Pages></ComicInfo>')
    saved = st.history[st.history_index]
    restore(st, saved)
    pages = parse_comic_info_xml(info["bytes"])["pages"]
    assert [p["Image"] for p in pages] == ["0", "1"]
    assert pages[0]["Type"] == "FrontCover"
    assert st.modified


def test_build_page_attrs_map_after_restore_follows_the_restored_xml():
    st, _info = opened_document()
    mark_second_page(st)
    restore(st, undo_data(st))
    build_page_attrs_map(st)
    assert st._page_attrs_by_entry_id[id(st.images_data[1])]["Type"] == "FrontCover"
