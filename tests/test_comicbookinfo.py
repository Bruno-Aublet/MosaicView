"""Métadonnées ComicBookInfo du commentaire d'archive : lues
(comic_info.comicbookinfo_meta), affichées dans l'onglet Métadonnées même sans
ComicInfo.xml (shows_metadata_tab, document_comicbookinfo), et transformées
en ComicInfo.xml sur demande (write_comic_metadata_from_comicbookinfo)."""
import json
import os
import sys
import types
import zipfile

import pytest

from modules.qt import comic_info
from modules.qt.comic_info import (
    comicbookinfo_meta,
    document_comicbookinfo,
    shows_metadata_tab,
    write_comic_metadata_from_comicbookinfo,
)


@pytest.fixture(autouse=True)
def signals(monkeypatch):
    """Signaux de métadonnées sans Qt : émissions notées."""
    emitted = []
    fake = types.ModuleType("modules.qt.metadata_signal")
    fake.metadata_signal = types.SimpleNamespace(emit=lambda state=None: emitted.append(state))
    fake.metadata_pages_signal = types.SimpleNamespace(emit=lambda state=None: None)
    fake.comic_info_backup_signal = types.SimpleNamespace(emit=lambda state=None: None)
    monkeypatch.setitem(sys.modules, "modules.qt.metadata_signal", fake)
    comic_info._COMICBOOKINFO_CACHE.clear()
    return emitted


def cbi_comment(**fields):
    return json.dumps({"appID": "Aaaa/1.0", "lastModified": "2020-01-01 00:00:00",
                       "ComicBookInfo/1.0": fields}).encode("utf-8")


FULL = cbi_comment(
    series="Aaaa", title="Bbbb", issue=3, numberOfIssues=12, volume=1,
    publisher="Cccc", publicationYear=1977, publicationMonth=5, genre="Dddd",
    language="English", country="Eeee", rating=4, tags=["Ffff", "Gggg"],
    comments="Hhhh & <Iiii>",
    credits=[
        {"person": "Jjjj", "role": "Writer", "primary": True},
        {"person": "Kkkk", "role": "Plotter"},
        {"person": "Llll", "role": "Penciler"},
        {"person": "Mmmm", "role": "Inker"},
        {"person": "Nnnn", "role": "Colourist"},
        {"person": "Oooo", "role": "Letterer"},
        {"person": "Pppp", "role": "Cover Artist"},
        {"person": "Qqqq", "role": "Editor"},
        {"person": "Rrrr", "role": "Translator"},     # rôle sans balise : ignoré
    ])


class FakeState:
    def __init__(self, images_data, current_file=None):
        self.images_data = images_data
        self.comic_metadata = None
        self.modified = False
        self.original_page_count = None
        self._page_attrs_by_entry_id = {}
        self.current_file = current_file


def page(name):
    return {"orig_name": name, "name": name, "bytes": b"x" * 10, "is_image": True,
            "is_dir": False, "extension": ".jpg", "img_width": 10, "img_height": 20}


# ── Lecture ───────────────────────────────────────────────────────────────────

def test_fields_are_mapped_to_comicinfo_keys():
    meta = comicbookinfo_meta(FULL)
    assert meta == {
        "series": "Aaaa", "title": "Bbbb", "number": "3", "count": "12", "volume": "1",
        "publisher": "Cccc", "year": "1977", "month": "5", "genre": "Dddd",
        "writer": "Jjjj, Kkkk", "penciller": "Llll", "inker": "Mmmm",
        "colorist": "Nnnn", "letterer": "Oooo", "cover_artist": "Pppp",
        "editor": "Qqqq", "tags": "Ffff, Gggg", "community_rating": "4",
        "summary": "Hhhh & <Iiii>",
    }


def test_every_key_has_a_metadata_label():
    """Chaque clé lue a son libellé metadata.<clé> (onglet Métadonnées) et sa
    balise ComicInfo.xml."""
    with open(os.path.join(os.path.dirname(__file__), "..", "locales", "fr.json"),
              encoding="utf-8") as f:
        labels = json.load(f)["metadata"]
    for key in comicbookinfo_meta(FULL):
        assert key in labels
        assert key in comic_info._COMICBOOKINFO_FIELD_MAP


@pytest.mark.parametrize("comment", [
    b"", b"not json", b"\xff\xfe\x00", b"[1, 2]", b'"text"',
    json.dumps({"appID": "Aaaa"}).encode(),                       # pas de ComicBookInfo
    json.dumps({"ComicBookInfo/1.0": "Aaaa"}).encode(),           # pas un objet
    json.dumps({"ComicBookInfo/1.0": {}}).encode(),               # vide
])
def test_comments_without_comicbookinfo_give_nothing(comment):
    assert comicbookinfo_meta(comment) == {}


def test_values_that_are_not_simple_texts_are_ignored():
    meta = comicbookinfo_meta(cbi_comment(
        series=["Aaaa"], title={"x": 1}, issue=None, volume="  ", genre=True,
        publisher="Bbbb", tags="Cccc",
        credits=[{"person": ["Dddd"], "role": "Writer"}, "Eeee",
                 {"person": "Ffff", "role": None}, {"person": "Gggg", "role": "Inker"}]))
    assert meta == {"publisher": "Bbbb", "inker": "Gggg"}


# ── Onglet Métadonnées ────────────────────────────────────────────────────────

def make_cbz(path, comment, with_comic_info=False):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("001.jpg", b"x")
        if with_comic_info:
            zf.writestr("ComicInfo.xml", b"<ComicInfo><Title>Zzzz</Title></ComicInfo>")
        zf.comment = comment
    return str(path)


def test_the_tab_shows_for_a_file_with_comicbookinfo_only(tmp_path):
    st = FakeState([page("001.jpg")], make_cbz(tmp_path / "a.cbz", FULL))
    assert document_comicbookinfo(st)["series"] == "Aaaa"
    assert shows_metadata_tab(st)
    plain = FakeState([page("001.jpg")], make_cbz(tmp_path / "b.cbz", b""))
    assert document_comicbookinfo(plain) == {} and not shows_metadata_tab(plain)
    unsaved = FakeState([page("001.jpg")])
    assert document_comicbookinfo(unsaved) == {} and not shows_metadata_tab(unsaved)
    missing = FakeState([page("001.jpg")], str(tmp_path / "missing.cbz"))
    assert document_comicbookinfo(missing) == {}


def test_the_comment_is_read_again_only_when_the_file_changes(tmp_path, monkeypatch):
    path = make_cbz(tmp_path / "a.cbz", FULL)
    st = FakeState([page("001.jpg")], path)
    reads = []
    from modules.qt import archive_members
    real = archive_members.archive_comment
    monkeypatch.setattr(archive_members, "archive_comment",
                        lambda p: reads.append(p) or real(p))
    document_comicbookinfo(st)
    document_comicbookinfo(st)
    assert len(reads) == 1
    make_cbz(tmp_path / "a.cbz", cbi_comment(series="Ssss", title="Tttt"))
    os.utime(path, ns=(1, 1))                  # date changée à coup sûr
    assert document_comicbookinfo(st)["series"] == "Ssss"
    assert len(reads) == 2


# ── Création du ComicInfo.xml ─────────────────────────────────────────────────

def test_the_comicinfo_is_created_from_the_comicbookinfo(signals):
    st = FakeState([page("001.jpg"), page("002.jpg")])
    write_comic_metadata_from_comicbookinfo(st, comicbookinfo_meta(FULL))
    xml = comic_info.comic_info_entry(st)
    assert xml is not None and xml["orig_name"] == "ComicInfo.xml"
    meta = st.comic_metadata
    assert meta["series"] == "Aaaa" and meta["number"] == "3"
    assert meta["count"] == "12" and meta["community_rating"] == "4"
    assert meta["tags"] == "Ffff, Gggg" and meta["writer"] == "Jjjj, Kkkk"
    assert meta["summary"] == "Hhhh & <Iiii>"
    assert b"<Summary>Hhhh &amp; &lt;Iiii&gt;</Summary>" in xml["bytes"]
    assert len(meta["pages"]) == 2                # <Pages> construit
    assert st.modified and signals == [st]
