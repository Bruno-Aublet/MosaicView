"""Textes en codage Windows : archive_names.windows_text, et ses lecteurs hors de
ComicInfo.xml (éditeur de fichiers annexes, pages de l'import web).

nfo_dialog_qt.py importe PySide6 : la fonction est extraite par AST
(tests/ast_loader.py).
"""
import pytest

from modules.qt.archive_names import tar_entry_name, windows_text
from modules.qt.web_import_qt import _page_text

import ast_loader

annex_text = ast_loader.load("nfo_dialog_qt.py", {"_annex_text"},
                             windows_text=windows_text)["_annex_text"]

SAMPLE = "L’été coûte 5 € – « fin »…"


def test_typographic_characters_are_kept():
    assert windows_text(SAMPLE.encode("cp1252")) == SAMPLE


def test_byte_undefined_in_cp1252_falls_back_on_latin1():
    assert windows_text(b"\xe9\x81") == "é\x81"


def test_tar_name_still_uses_it():
    assert tar_entry_name(b"\xe9t\xe9\x92.jpg".decode("utf-8", "surrogateescape")) == "été’.jpg"


# --- Fichier annexe (.nfo) -----------------------------------------------------

def test_utf8_annex_is_read_as_is():
    assert annex_text(SAMPLE.encode("utf-8")) == SAMPLE


def test_windows_annex_keeps_its_characters():
    assert annex_text(SAMPLE.encode("cp1252")) == SAMPLE


def test_empty_annex():
    assert annex_text(b"") == ""


# --- Page web ------------------------------------------------------------------

PAGE = '<img src="été.jpg">'


def test_server_announced_encoding_is_used():
    assert _page_text(PAGE.encode("iso-8859-1"), "text/html; charset=ISO-8859-1") == PAGE


def test_page_announced_encoding_is_used():
    page = f'<meta charset="windows-1252">{PAGE}'
    assert _page_text(page.encode("cp1252"), "text/html") == page


def test_server_announcement_wins_over_the_page():
    page = f'<meta charset="iso-8859-1">{PAGE}'
    assert _page_text(page.encode("utf-8"), "text/html; charset=utf-8") == page


def test_utf8_page_without_announcement():
    assert _page_text(PAGE.encode("utf-8"), "text/html") == PAGE


def test_old_page_without_announcement_keeps_its_accented_names():
    assert _page_text(PAGE.encode("cp1252"), "text/html") == PAGE


@pytest.mark.parametrize("announced", ["nothing-known", "zlib", "hex", "utf-16"])
def test_unknown_or_wrong_announcement_is_ignored(announced):
    data = PAGE.encode("cp1252")
    text = _page_text(data, f"text/html; charset={announced}")
    assert isinstance(text, str)
    if announced != "utf-16":
        assert text == PAGE


def test_empty_page():
    assert _page_text(b"", "") == ""
