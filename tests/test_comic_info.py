import xml.etree.ElementTree as ET

import pytest

from modules.qt.comic_info import (
    parse_comic_info_xml,
    _serialize_comic_xml,
    _update_trace_note,
    diff_comic_metadata,
    get_current_image_count,
    has_comic_info_entry,
    update_page_count_in_xml_data,
    sync_pages_in_xml_data,
    build_page_attrs_map,
    get_page_image_index,
    update_page_entries_in_xml_data,
)


MINIMAL_XML = (
    b'<?xml version="1.0"?>\r\n'
    b'<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\r\n'
    b"  <Title>Le titre</Title>\r\n"
    b"  <Series>La s\xc3\xa9rie</Series>\r\n"
    b"  <Number>1</Number>\r\n"
    b"  <Writer>Jean Dupont</Writer>\r\n"
    b"  <PageCount>2</PageCount>\r\n"
    b"  <Pages>\r\n"
    b'    <Page Image="0" ImageSize="1000" ImageWidth="800" ImageHeight="1200" />\r\n'
    b'    <Page Image="1" ImageSize="2000" ImageWidth="800" ImageHeight="1200" Type="FrontCover" />\r\n'
    b"  </Pages>\r\n"
    b"</ComicInfo>"
)


class FakeState:
    """Substitut minimal de l'objet state utilisé par comic_info.py (pas de Qt)."""

    def __init__(self, images_data, comic_metadata):
        self.images_data = images_data
        self.comic_metadata = comic_metadata
        self.modified = False
        self.original_page_count = None
        self._page_attrs_by_entry_id = {}


def make_xml_entry(xml_bytes):
    return {
        "orig_name": "ComicInfo.xml", "name": "ComicInfo.xml",
        "bytes": xml_bytes, "is_image": False, "is_dir": False,
        "extension": ".xml",
    }


def make_image_entry(name, bytes_len=100, width=None, height=None):
    entry = {
        "orig_name": name, "name": name, "bytes": b"x" * bytes_len,
        "is_image": True, "is_dir": False, "extension": ".jpg",
    }
    if width:
        entry["img_width"] = width
    if height:
        entry["img_height"] = height
    return entry


# ---------------------------------------------------------------------------
# parse_comic_info_xml
# ---------------------------------------------------------------------------

def test_parse_comic_info_xml_extracts_known_fields():
    meta = parse_comic_info_xml(MINIMAL_XML)
    assert meta["title"] == "Le titre"
    assert meta["series"] == "La série"
    assert meta["number"] == "1"
    assert meta["writer"] == "Jean Dupont"
    assert meta["page_count"] == "2"


def test_parse_comic_info_xml_extracts_pages():
    meta = parse_comic_info_xml(MINIMAL_XML)
    assert len(meta["pages"]) == 2
    assert meta["pages"][0]["Image"] == "0"
    assert meta["pages"][1]["Type"] == "FrontCover"


def test_parse_comic_info_xml_missing_field_is_empty_string():
    meta = parse_comic_info_xml(MINIMAL_XML)
    assert meta["genre"] == ""
    assert meta["summary"] == ""


def test_parse_comic_info_xml_no_pages_element_omits_key():
    xml = b'<?xml version="1.0"?>\r\n<ComicInfo>\r\n  <Title>T</Title>\r\n</ComicInfo>'
    meta = parse_comic_info_xml(xml)
    assert "pages" not in meta


def test_parse_comic_info_xml_invalid_xml_returns_none():
    assert parse_comic_info_xml(b"not xml at all <<<") is None


# ---------------------------------------------------------------------------
# _serialize_comic_xml — format exact (round-trip)
# ---------------------------------------------------------------------------

def test_serialize_roundtrip_preserves_declaration_and_crlf():
    root = ET.fromstring(MINIMAL_XML)
    out = _serialize_comic_xml(root, MINIMAL_XML)
    assert out.startswith(b'<?xml version="1.0"?>\r\n')
    assert b"\r\n" in out
    assert b"\n" not in out.replace(b"\r\n", b"")


def test_serialize_preserves_comicinfo_opening_tag_with_xmlns():
    root = ET.fromstring(MINIMAL_XML)
    out = _serialize_comic_xml(root, MINIMAL_XML)
    assert b'<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">' in out


def test_serialize_without_original_bytes_uses_bare_tag():
    root = ET.Element("ComicInfo")
    ET.SubElement(root, "Title").text = "T"
    out = _serialize_comic_xml(root, None)
    assert out.startswith(b'<?xml version="1.0"?>\r\n<ComicInfo>\r\n')


def test_serialize_page_attribute_order_is_canonical():
    root = ET.Element("ComicInfo")
    pages = ET.SubElement(root, "Pages")
    page = ET.SubElement(pages, "Page")
    # Ordre d'insertion volontairement inverse de l'ordre canonique attendu
    page.set("Type", "FrontCover")
    page.set("ImageHeight", "1200")
    page.set("ImageWidth", "800")
    page.set("ImageSize", "1000")
    page.set("Image", "0")
    out = _serialize_comic_xml(root, None)
    line = [l for l in out.decode("utf-8").split("\r\n") if l.strip().startswith("<Page ")][0]
    assert line == '    <Page Image="0" ImageSize="1000" ImageWidth="800" ImageHeight="1200" Type="FrontCover" />'


def test_serialize_escapes_xml_special_chars_in_text():
    root = ET.Element("ComicInfo")
    ET.SubElement(root, "Summary").text = 'A & B <C> "D"'
    out = _serialize_comic_xml(root, None)
    assert b"<Summary>A &amp; B &lt;C&gt; &quot;D&quot;</Summary>" in out


def test_serialize_roundtrip_reparse_gives_same_metadata():
    meta_before = parse_comic_info_xml(MINIMAL_XML)
    root = ET.fromstring(MINIMAL_XML)
    out = _serialize_comic_xml(root, MINIMAL_XML)
    meta_after = parse_comic_info_xml(out)
    assert meta_before == meta_after


# ---------------------------------------------------------------------------
# _update_trace_note
# ---------------------------------------------------------------------------

def test_update_trace_note_empty_notes_creates_line():
    result = _update_trace_note("", "2026-07-17")
    assert result == "MosaicView: metadata retrieved on 2026-07-17."


def test_update_trace_note_appends_to_existing_user_text():
    result = _update_trace_note("Texte utilisateur", "2026-07-17")
    assert result == "Texte utilisateur\nMosaicView: metadata retrieved on 2026-07-17."


def test_update_trace_note_replaces_previous_mosaicview_line_only():
    existing = "Texte utilisateur\nMosaicView: metadata retrieved on 2026-01-01."
    result = _update_trace_note(existing, "2026-07-17")
    assert result == "Texte utilisateur\nMosaicView: metadata retrieved on 2026-07-17."


def test_update_trace_note_none_input_treated_as_empty():
    result = _update_trace_note(None, "2026-07-17")
    assert result == "MosaicView: metadata retrieved on 2026-07-17."


# ---------------------------------------------------------------------------
# diff_comic_metadata
# ---------------------------------------------------------------------------

def test_diff_comic_metadata_added_field():
    diffs = diff_comic_metadata({"writer": ""}, {"writer": "Jean Dupont"})
    assert ("writer", "added") in diffs


def test_diff_comic_metadata_modified_field():
    diffs = diff_comic_metadata({"writer": "Ancien"}, {"writer": "Nouveau"})
    assert ("writer", "modified") in diffs


def test_diff_comic_metadata_identical_field_not_reported():
    diffs = diff_comic_metadata({"writer": "Jean"}, {"writer": "Jean"})
    assert diffs == []


def test_diff_comic_metadata_remote_empty_never_reported():
    diffs = diff_comic_metadata({"writer": "Jean"}, {"writer": ""})
    assert diffs == []


def test_diff_comic_metadata_imprint_excluded_even_if_different():
    diffs = diff_comic_metadata({"imprint": "A"}, {"imprint": "B"})
    assert diffs == []


def test_diff_comic_metadata_none_inputs_do_not_crash():
    assert diff_comic_metadata(None, None) == []


# ---------------------------------------------------------------------------
# get_current_image_count / has_comic_info_entry
# ---------------------------------------------------------------------------

def test_get_current_image_count_excludes_dirs_and_comicinfo():
    images_data = [
        make_image_entry("page1.jpg"),
        make_image_entry("page2.jpg"),
        {"orig_name": "ComicInfo.xml", "is_image": False, "is_dir": False},
        {"orig_name": "sub", "is_image": False, "is_dir": True},
    ]
    state = FakeState(images_data, {})
    assert get_current_image_count(state) == 2


def test_has_comic_info_entry_true_and_false():
    state_with = FakeState([make_xml_entry(MINIMAL_XML)], {})
    state_without = FakeState([make_image_entry("page1.jpg")], {})
    assert has_comic_info_entry(state_with) is True
    assert has_comic_info_entry(state_without) is False


# ---------------------------------------------------------------------------
# update_page_count_in_xml_data
# ---------------------------------------------------------------------------

def test_update_page_count_updates_bytes_and_metadata():
    meta = parse_comic_info_xml(MINIMAL_XML)
    state = FakeState([make_xml_entry(MINIMAL_XML)], meta)
    ok = update_page_count_in_xml_data(state, 5)
    assert ok is True
    assert state.comic_metadata["page_count"] == "5"
    assert state.original_page_count == 5
    assert state.modified is True
    reparsed = parse_comic_info_xml(state.images_data[0]["bytes"])
    assert reparsed["page_count"] == "5"


def test_update_page_count_no_xml_entry_returns_false():
    state = FakeState([make_image_entry("page1.jpg")], {"title": "T"})
    assert update_page_count_in_xml_data(state, 5) is False


def test_update_page_count_no_comic_metadata_returns_false():
    state = FakeState([make_xml_entry(MINIMAL_XML)], None)
    assert update_page_count_in_xml_data(state, 5) is False


# ---------------------------------------------------------------------------
# sync_pages_in_xml_data
# ---------------------------------------------------------------------------

def test_sync_pages_guard_no_pages_key_does_nothing():
    meta = {"title": "T"}  # pas de clé 'pages'
    xml_entry = make_xml_entry(b'<?xml version="1.0"?>\r\n<ComicInfo>\r\n  <Title>T</Title>\r\n</ComicInfo>')
    state = FakeState([xml_entry, make_image_entry("page1.jpg")], meta)
    original_bytes = xml_entry["bytes"]
    sync_pages_in_xml_data(state, emit_signal=False)
    assert xml_entry["bytes"] == original_bytes


def test_sync_pages_rebuilds_pages_for_current_entries():
    meta = parse_comic_info_xml(MINIMAL_XML)
    img1 = make_image_entry("page1.jpg", bytes_len=500, width=800, height=1200)
    img2 = make_image_entry("page2.jpg", bytes_len=600, width=800, height=1200)
    xml_entry = make_xml_entry(MINIMAL_XML)
    state = FakeState([xml_entry, img1, img2], meta)

    sync_pages_in_xml_data(state, emit_signal=False)

    assert len(state.comic_metadata["pages"]) == 2
    assert state.comic_metadata["pages"][0]["Image"] == "0"
    assert state.comic_metadata["pages"][1]["Image"] == "1"
    assert state.comic_metadata["page_count"] == "2"
    assert state.modified is True


def test_sync_pages_reflects_removed_entry():
    meta = parse_comic_info_xml(MINIMAL_XML)
    img1 = make_image_entry("page1.jpg", bytes_len=500, width=800, height=1200)
    xml_entry = make_xml_entry(MINIMAL_XML)
    # Une seule image réelle restante (page supprimée entre-temps)
    state = FakeState([xml_entry, img1], meta)

    sync_pages_in_xml_data(state, emit_signal=False)

    assert len(state.comic_metadata["pages"]) == 1
    assert state.comic_metadata["page_count"] == "1"


# ---------------------------------------------------------------------------
# build_page_attrs_map / get_page_image_index
# ---------------------------------------------------------------------------

def test_build_page_attrs_map_maps_entries_by_position():
    meta = parse_comic_info_xml(MINIMAL_XML)
    img1 = make_image_entry("page1.jpg")
    img2 = make_image_entry("page2.jpg")
    state = FakeState([make_xml_entry(MINIMAL_XML), img1, img2], meta)

    build_page_attrs_map(state)

    assert state._page_attrs_by_entry_id[id(img1)]["ImageWidth"] == "800"
    assert state._page_attrs_by_entry_id[id(img2)]["Type"] == "FrontCover"


def test_build_page_attrs_map_no_pages_key_leaves_empty_map():
    state = FakeState([make_image_entry("page1.jpg")], {"title": "T"})
    build_page_attrs_map(state)
    assert state._page_attrs_by_entry_id == {}


def test_get_page_image_index_skips_dirs_and_comicinfo():
    xml_entry = make_xml_entry(MINIMAL_XML)
    dir_entry = {"orig_name": "sub", "is_dir": True, "is_image": False}
    img1 = make_image_entry("page1.jpg")
    img2 = make_image_entry("page2.jpg")
    state = FakeState([xml_entry, dir_entry, img1, img2], {})

    assert get_page_image_index(state, img1) == 0
    assert get_page_image_index(state, img2) == 1


def test_get_page_image_index_unknown_entry_returns_none():
    state = FakeState([make_image_entry("page1.jpg")], {})
    assert get_page_image_index(state, {"orig_name": "not_in_list.jpg"}) is None


# ---------------------------------------------------------------------------
# update_page_entries_in_xml_data
# ---------------------------------------------------------------------------

def test_update_page_entries_updates_size_and_dimensions():
    from PIL import Image
    import io

    meta = parse_comic_info_xml(MINIMAL_XML)
    xml_entry = make_xml_entry(MINIMAL_XML)

    buf = io.BytesIO()
    Image.new("RGB", (640, 480), "white").save(buf, format="JPEG")
    new_bytes = buf.getvalue()
    img1 = {"orig_name": "page1.jpg", "is_image": True, "is_dir": False, "bytes": new_bytes}

    state = FakeState([xml_entry, img1], meta)

    update_page_entries_in_xml_data(state, [(0, img1)], emit_signal=False)

    reparsed = parse_comic_info_xml(xml_entry["bytes"])
    page0 = reparsed["pages"][0]
    assert page0["ImageWidth"] == "640"
    assert page0["ImageHeight"] == "480"
    assert page0["ImageSize"] == str(len(new_bytes))
    assert state.modified is True


def test_update_page_entries_guard_no_pages_key_does_nothing():
    xml_entry = make_xml_entry(b'<?xml version="1.0"?>\r\n<ComicInfo>\r\n  <Title>T</Title>\r\n</ComicInfo>')
    state = FakeState([xml_entry], {"title": "T"})
    original_bytes = xml_entry["bytes"]
    update_page_entries_in_xml_data(state, [(0, {"bytes": b"data"})], emit_signal=False)
    assert xml_entry["bytes"] == original_bytes
