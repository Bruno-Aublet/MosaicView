"""ComicInfo.xml abîmé ou refusé par le parseur durci : lu dans sa version
réparée (comic_info._comic_tree), jamais en exécutant une entité, et gardé
intact en annexe à sa première réécriture (replace_comic_info_bytes)."""
import sys
import types
import xml.etree.ElementTree as ET

import pytest

from modules.qt import comic_info
from modules.qt.comic_info import (
    comic_info_repaired,
    comic_info_strictly_readable,
    comic_info_unreadable,
    parse_comic_info_xml,
    replace_comic_info_bytes,
    sync_pages_in_xml_data,
    update_page_count_in_xml_data,
    write_comic_metadata_from_scraper,
)

# Déclaration d'entité : refusée par defusedxml, comme dans un fichier piégé
WITH_ENTITY = (
    b'<?xml version="1.0"?>\r\n'
    b'<!DOCTYPE x [<!ENTITY a "b">]>'
    b'<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema">\r\n'
    b"  <Title>Aaaa &a; Bbbb</Title>\r\n"
    b"  <Series>Cccc</Series>\r\n"
    b"  <Writer>Dddd</Writer>\r\n"
    b"  <OtherSoftware>Eeee</OtherSoftware>\r\n"
    b"  <PageCount>2</PageCount>\r\n"
    b"  <Pages>\r\n"
    b'    <Page Image="0" Type="FrontCover" />\r\n'
    b'    <Page Image="1" Bookmark="Ffff" />\r\n'
    b"  </Pages>\r\n"
    b"</ComicInfo>"
)

# Balise de fin manquante, texte coupé
TRUNCATED = (
    b'<?xml version="1.0"?>\r\n<ComicInfo>\r\n'
    b"  <Title>Aaaa</Title>\r\n  <Series>Cccc</Series>\r\n"
    b'  <Pages>\r\n    <Page Image="0" Type="FrontCover" />\r\n  </Pages>\r\n'
    b"  <Summary>Gggg"
)

READABLE = b'<?xml version="1.0"?>\r\n<ComicInfo>\r\n  <Title>Aaaa</Title>\r\n</ComicInfo>'


@pytest.fixture(autouse=True)
def backup_signal(monkeypatch):
    """Signaux de métadonnées sans Qt : émissions notées."""
    emitted = []
    fake = types.ModuleType("modules.qt.metadata_signal")
    fake.comic_info_backup_signal = types.SimpleNamespace(emit=emitted.append)
    fake.metadata_signal = types.SimpleNamespace(emit=lambda state=None: None)
    fake.metadata_pages_signal = types.SimpleNamespace(emit=lambda state=None: None)
    monkeypatch.setitem(sys.modules, "modules.qt.metadata_signal", fake)
    return emitted


class FakeState:
    def __init__(self, images_data, comic_metadata=None):
        self.images_data = images_data
        self.comic_metadata = comic_metadata
        self.modified = False
        self.original_page_count = None
        self._page_attrs_by_entry_id = {}


def xml_entry(data, name="ComicInfo.xml"):
    return {"orig_name": name, "name": name, "bytes": data,
            "is_image": False, "is_dir": False, "extension": ".xml"}


def page(name):
    return {"orig_name": name, "bytes": b"x" * 10, "is_image": True, "is_dir": False,
            "extension": ".jpg", "img_width": 10, "img_height": 20}


def state_for(data, pages=2):
    entries = [page(f"{i:03d}.jpg") for i in range(pages)] + [xml_entry(data)]
    st = FakeState(entries)
    comic_info.sync_comic_metadata(st, force=True)
    return st


# ── Lecture réparée ───────────────────────────────────────────────────────────

def test_entity_declaration_is_read_without_running_the_entity():
    meta = parse_comic_info_xml(WITH_ENTITY)
    assert meta is not None
    assert meta["series"] == "Cccc" and meta["writer"] == "Dddd"
    assert "b" not in meta["title"].replace("Bbbb", "")  # entité jamais remplacée
    assert meta["title"].startswith("Aaaa") and meta["title"].endswith("Bbbb")
    assert meta["pages"][0]["Type"] == "FrontCover"
    assert meta["pages"][1]["Bookmark"] == "Ffff"


def test_expanding_entities_never_fill_memory():
    """Entités en cascade (10 milliards de caractères une fois développées) :
    jamais développées. libxml2 arrête alors la lecture à la première
    référence (facteur d'amplification dépassé) : ce qui suit est abandonné,
    l'original reste gardé en annexe à la réécriture."""
    bomb = (b'<!DOCTYPE x [<!ENTITY a "aaaaaaaaaa">'
            + b"".join(b'<!ENTITY a%d "%s">' % (i + 1, (b"&a%d;" % i if i else b"&a;") * 10)
                       for i in range(9))
            + b']><ComicInfo><Title>&a9;</Title><Series>Cccc</Series></ComicInfo>')
    meta = parse_comic_info_xml(bomb)
    assert meta is None or len(meta["title"]) < 100
    clean = comic_info._repaired_comic_xml(bomb)
    assert clean is None or (len(clean) < 1000 and b"<!ENTITY" not in clean)


def test_external_entity_never_reads_a_file(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("Hhhh-secret", encoding="utf-8")
    data = (b'<!DOCTYPE x [<!ENTITY e SYSTEM "' + secret.as_uri().encode() + b'">]>'
            b"<ComicInfo><Title>&e;</Title><Series>Cccc</Series></ComicInfo>")
    meta = parse_comic_info_xml(data)
    assert meta is not None and meta["series"] == "Cccc"
    assert "Hhhh" not in meta["title"]


def test_truncated_file_keeps_what_is_still_readable():
    meta = parse_comic_info_xml(TRUNCATED)
    assert meta["title"] == "Aaaa" and meta["series"] == "Cccc"
    assert meta["pages"][0]["Type"] == "FrontCover"


@pytest.mark.parametrize("data", [b"", b"   ", b"not xml at all <<<", b"<Other><Title>Aaaa</Title></Other"])
def test_nothing_recoverable_stays_unreadable(data):
    assert parse_comic_info_xml(data) is None


def test_strictly_readable_only_for_files_read_as_they_are():
    assert comic_info_strictly_readable(READABLE)
    assert comic_info_strictly_readable(b"")
    assert not comic_info_strictly_readable(WITH_ENTITY)
    assert not comic_info_strictly_readable(TRUNCATED)
    assert not comic_info_strictly_readable(b"   ")


def test_repaired_and_unreadable_states():
    assert comic_info_repaired(state_for(WITH_ENTITY))
    assert not comic_info_repaired(state_for(READABLE))
    broken = state_for(b"garbage")
    assert comic_info_unreadable(broken) and not comic_info_repaired(broken)


def test_repaired_version_has_no_dtd_nor_entity():
    clean = comic_info._repaired_comic_xml(WITH_ENTITY)
    assert b"<!DOCTYPE" not in clean and b"<!ENTITY" not in clean and b"&a;" not in clean
    assert comic_info_strictly_readable(clean)


# ── Copie de l'original à la première réécriture ─────────────────────────────

def test_first_rewrite_keeps_the_original_next_to_it(backup_signal):
    st = state_for(WITH_ENTITY)
    xml = comic_info.comic_info_entry(st)
    update_page_count_in_xml_data(st, 5)
    names = [e["orig_name"] for e in st.images_data]
    assert names == ["000.jpg", "001.jpg", "ComicInfo.xml", "ComicInfo_0002.xml"]
    assert st.images_data[3]["bytes"] == WITH_ENTITY
    assert st.images_data[3]["is_image"] is False
    assert comic_info_strictly_readable(xml["bytes"])
    meta = parse_comic_info_xml(xml["bytes"])
    assert meta["page_count"] == "5" and meta["writer"] == "Dddd"
    assert meta["pages"][1]["Bookmark"] == "Ffff"
    assert b"<OtherSoftware>Eeee</OtherSoftware>" in xml["bytes"]
    assert st.comic_info_backup_name == "ComicInfo_0002.xml"
    assert backup_signal == [st] and st.modified


def test_backup_is_made_only_once(backup_signal):
    st = state_for(WITH_ENTITY)
    update_page_count_in_xml_data(st, 5)
    sync_pages_in_xml_data(st, emit_signal=False)
    update_page_count_in_xml_data(st, 7)
    assert sum(e["orig_name"].startswith("ComicInfo") for e in st.images_data) == 2
    assert len(backup_signal) == 1


def test_readable_file_never_gets_a_backup(backup_signal):
    st = state_for(READABLE)
    update_page_count_in_xml_data(st, 5)
    assert [e["orig_name"] for e in st.images_data][-1] == "ComicInfo.xml"
    assert len(st.images_data) == 3 and backup_signal == []


def test_backup_name_skips_names_already_taken():
    st = state_for(TRUNCATED)
    st.images_data.append(xml_entry(b"", "comicinfo_0002.xml"))
    st._archive_member_names = ["ComicInfo_0003.xml", "001.jpg"]
    xml = comic_info.comic_info_entry(st)
    assert replace_comic_info_bytes(st, xml, READABLE) == "ComicInfo_0004.xml"


def test_same_bytes_are_not_a_rewrite():
    st = state_for(WITH_ENTITY)
    xml = comic_info.comic_info_entry(st)
    assert replace_comic_info_bytes(st, xml, WITH_ENTITY) is None
    assert len(st.images_data) == 3


def test_comicvine_completes_the_repaired_content(backup_signal):
    st = state_for(WITH_ENTITY)
    write_comic_metadata_from_scraper(st, {"title": "Iiii", "publisher": "Jjjj"})
    meta = st.comic_metadata
    assert meta["title"] == "Iiii" and meta["publisher"] == "Jjjj"
    assert meta["series"] == "Cccc" and meta["writer"] == "Dddd"
    assert meta["pages"][0]["Type"] == "FrontCover"
    backup = next(e for e in st.images_data if e["orig_name"] == "ComicInfo_0002.xml")
    assert backup["bytes"] == WITH_ENTITY


def test_comicvine_on_an_unrecoverable_file_still_keeps_it():
    st = state_for(b"garbage <<<")
    write_comic_metadata_from_scraper(st, {"title": "Iiii"})
    assert st.comic_metadata["title"] == "Iiii"
    backup = next(e for e in st.images_data if e["orig_name"] == "ComicInfo_0002.xml")
    assert backup["bytes"] == b"garbage <<<"


def test_repair_on_open_replaces_the_file_and_keeps_the_original():
    st = state_for(WITH_ENTITY)
    seen = []
    sys.modules["modules.qt.metadata_signal"].comic_info_backup_signal = types.SimpleNamespace(
        emit=lambda s: seen.append(getattr(s, "comic_info_repaired_on_open", None)))
    assert comic_info.repair_comic_info_on_open(st) is True
    assert seen == [True]                       # texte « enregistrez pour garder »
    xml = comic_info.comic_info_entry(st)
    assert comic_info_strictly_readable(xml["bytes"])
    assert b"<OtherSoftware>Eeee</OtherSoftware>" in xml["bytes"]
    assert [e["orig_name"] for e in st.images_data][-1] == "ComicInfo_0002.xml"
    assert st.images_data[-1]["bytes"] == WITH_ENTITY
    assert st.modified and not comic_info_repaired(st)
    assert st.comic_metadata["writer"] == "Dddd"
    assert st.comic_metadata["pages"][1]["Bookmark"] == "Ffff"
    assert st.comic_info_repaired_on_open is False


def test_repair_on_open_does_nothing_for_a_sound_or_lost_file():
    for data in (READABLE, b"garbage <<<"):
        st = state_for(data)
        assert comic_info.repair_comic_info_on_open(st) is False
        assert len(st.images_data) == 3 and not st.modified


def test_panel_repairs_between_two_history_states():
    """PanelWidget._on_loading_finished pose un état d'historique avant la
    réparation à l'ouverture et un autre après : Annuler rend l'original."""
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "modules" / "qt" / "panel_widget.py").read_text(encoding="utf-8")
    func = next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef) and n.name == "_on_loading_finished")
    calls = [ast.unparse(n.func) for n in ast.walk(func) if isinstance(n, ast.Call)]
    lines = ast.unparse(func).splitlines()
    first_save = next(i for i, l in enumerate(lines) if "save_state_data(self._state)" in l)
    repair = next(i for i, l in enumerate(lines) if "repair_comic_info_on_open(self._state)" in l)
    assert first_save < repair
    assert "save_state_data(self._state)" in lines[repair + 1]
    assert calls.count("save_state_data") == 2


def test_undo_restore_never_rewrites_a_damaged_original():
    """restore_state_qt ne resynchronise pas <Pages> d'un ComicInfo.xml lu
    réparé : Annuler referait sinon une copie en annexe."""
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "modules" / "qt" / "undo_redo_qt.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "restore_state_qt")
    guarded = False
    for node in ast.walk(func):
        if isinstance(node, ast.If) and "comic_info_repaired" in ast.unparse(node.test):
            guarded = any("sync_pages_in_xml_data" in ast.unparse(s) for s in node.body)
    assert guarded


# --- Codage d'un fichier qui ne déclare pas le sien ---------------------------

SUMMARY_XML = "<ComicInfo><Summary>{}</Summary><Notes>suite</Notes></ComicInfo>"


def repaired_summary(data):
    root = ET.fromstring(comic_info._repaired_comic_xml(data))
    assert root.findtext("Notes") == "suite"
    return root.findtext("Summary")


def test_windows_file_keeps_its_typographic_characters():
    data = SUMMARY_XML.format("L’été coûte 5 € – « fin »…").encode("cp1252")
    assert repaired_summary(data) == "L’été coûte 5 € – « fin »…"


def test_latin1_file_keeps_its_accents():
    assert repaired_summary(SUMMARY_XML.format("Été à Noël").encode("latin-1")) == "Été à Noël"


def test_byte_undefined_in_cp1252_does_not_lose_the_file():
    data = SUMMARY_XML.format("été").encode("latin-1").replace(b"\xe9t", b"\xe9\x81t")
    assert repaired_summary(data).startswith("é")


def test_sound_utf8_file_is_unchanged():
    assert repaired_summary(SUMMARY_XML.format("L’été 5 €").encode("utf-8")) == "L’été 5 €"


def test_cut_utf8_file_keeps_its_accents():
    data = "<ComicInfo><Notes>suite</Notes><Summary>L’été à".encode("utf-8") + "é".encode("utf-8")[:1]
    root = ET.fromstring(comic_info._repaired_comic_xml(data))
    assert root.findtext("Summary").startswith("L’été à")


def test_damaged_utf8_file_keeps_its_accents_except_the_bad_byte():
    data = SUMMARY_XML.format("été@à fin").encode("utf-8").replace(b"@", b"\xff")
    assert repaired_summary(data) == "été�à fin"


def test_declared_encoding_is_respected():
    data = ('<?xml version="1.0" encoding="windows-1252"?>' + SUMMARY_XML.format("L’été 5 €")).encode("cp1252")
    assert repaired_summary(data) == "L’été 5 €"


def test_utf16_file_is_respected():
    assert repaired_summary(SUMMARY_XML.format("L’été").encode("utf-16")) == "L’été"


def test_comicinfo_bytes_are_replaced_only_by_replace_comic_info_bytes():
    """Garde-fou : dans comic_info.py, seule replace_comic_info_bytes écrit
    les octets d'une entrée (x["bytes"] = …). Une écriture directe perdrait
    la partie illisible d'un ComicInfo.xml abîmé, sans copie en annexe."""
    import ast
    from pathlib import Path
    tree = ast.parse(Path(comic_info.__file__).read_text(encoding="utf-8"))
    offenders = []
    for func in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        if func.name == "replace_comic_info_bytes":
            continue
        for node in ast.walk(func):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                            and target.slice.value == "bytes"):
                        offenders.append(f"{func.name}:{node.lineno}")
    assert not offenders, offenders
