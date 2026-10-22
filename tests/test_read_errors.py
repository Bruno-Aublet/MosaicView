"""Raisons traduites d'une erreur de lecture (modules/qt/read_errors.py).

Le message brut d'une erreur (zipfile, rarfile, tarfile, 7z, Pillow,
entries.create_entry) est stocké tel quel et traduit à l'affichage. Ces tests
vérifient la clé de traduction choisie pour chaque message, pas le texte d'une
langue : la fonction de traduction est remplacée par une fonction qui rend la
clé et ses paramètres.
"""
import pytest

from modules.qt import read_errors
from modules.qt.read_errors import (files_ignored_line, member_name_forms,
                                    read_error_text, unreadable_member_names)


@pytest.fixture(autouse=True)
def keys_instead_of_text(monkeypatch):
    def fake(key, **kwargs):
        params = ",".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
        return f"[{key.rsplit('.', 1)[-1]}{':' + params if params else ''}]"
    monkeypatch.setattr(read_errors, "_", fake)


@pytest.mark.parametrize("raw, expected", [
    ("Bad CRC-32 for file 'notes.txt'", "[crc]"),
    ("CRC error in member p.jpg", "[crc]"),
    ("File page.jpg is encrypted, password required for extraction", "[encrypted]"),
    ("Wrong password ?", "[encrypted]"),
    ("That compression method is not supported", "[unsupported_compression]"),
    ("compression type 9 (deflate64)", "[unsupported_compression]"),
    ("image file is truncated (12 bytes not processed)", "[truncated]"),
    ("Unexpected end of data", "[truncated]"),
    ("cannot identify image file <_io.BytesIO object at 0x0>", "[unknown_format]"),
    ("Fichier image vide", "[empty]"),
    ("Dimensions invalides: 0x0", "[invalid_dimensions]"),
    ("4.20 Go > 500.0 Mo", "[too_large:max=500.0 Mo,size=4.20 Go]"),
    ("1.0 Mo > 976.6 Ko", "[too_large:max=976.6 Ko,size=1.0 Mo]"),
])
def test_known_causes_are_recognised(raw, expected):
    assert read_error_text(raw) == expected


def test_unknown_cause_keeps_the_technical_message():
    assert read_error_text("Error -3 while decompressing data") == \
        "[other:detail=Error -3 while decompressing data]"


def test_long_technical_message_is_shortened():
    text = read_error_text("x" * 300)
    detail = text[len("[other:detail="):-1]
    assert len(detail) == read_errors._MAX_DETAIL and detail.endswith("…")


def test_empty_message_does_not_crash():
    assert read_error_text(None) == "[other:detail=]"


def test_member_name_forms():
    assert member_name_forms("./sub/p.jpg") >= {"./sub/p.jpg", "NEW-./sub/p.jpg", "p.jpg", "sub/p.jpg"}


def test_unreadable_member_names_keep_only_entries_without_data():
    entries = [
        {"orig_name": "notes.txt", "bytes": None},
        {"orig_name": "0002_b.jpg", "bytes": None},
        {"orig_name": "ok.jpg", "bytes": b"x"},
        {"orig_name": "sub/", "bytes": None, "is_dir": True},
    ]
    names = unreadable_member_names(entries)
    assert {"notes.txt", "0002_b.jpg", "b.jpg"} <= names
    assert "ok.jpg" not in names and "sub/" not in names


def test_member_error_line_is_rewritten():
    names = unreadable_member_names([{"orig_name": "notes.txt", "bytes": None}])
    line = files_ignored_line("notes.txt: Bad CRC-32 for file 'notes.txt'", names)
    assert line == "[line:name=notes.txt,reason=[crc]]"


def test_epub_member_error_matches_its_ranked_page():
    names = unreadable_member_names([{"orig_name": "0002_b.jpg", "bytes": None}])
    assert files_ignored_line("OEBPS/img/b.jpg: Bad CRC-32", names).startswith("[line:")


def test_other_lines_are_left_untouched():
    names = unreadable_member_names([{"orig_name": "notes.txt", "bytes": None}])
    translated = "Le fichier « x.cbz » n'est pas une archive ZIP valide : abîmé"
    assert files_ignored_line(translated, names) == translated
    assert files_ignored_line("File is not a zip file", names) == "File is not a zip file"


def test_without_member_names_every_line_is_a_member_error():
    assert files_ignored_line("a/b.txt: Bad CRC-32") == "[line:name=a/b.txt,reason=[crc]]"


def test_translated_line_is_translated_when_shown(monkeypatch):
    """Archive refusée, archive illisible : traduites à chaque affichage, dans
    la langue du moment (fenêtres de l'ouverture groupée)."""
    invalid = read_errors.TranslatedLine("messages.errors.zip_invalid.message", file="x.cbz")
    unread = read_errors.TranslatedLine("messages.read_errors.line", name="y.cbz",
                                        raw_reason="Bad CRC-32 for file 'p.jpg'")
    names = unreadable_member_names([{"orig_name": "notes.txt", "bytes": None}])
    assert files_ignored_line(invalid, names) == "[message:file=x.cbz]"
    assert files_ignored_line(unread) == "[line:name=y.cbz,reason=[crc]]"
    # Changement de langue : le texte suit
    monkeypatch.setattr(read_errors, "_", lambda key, **kw: "autre " + key.rsplit(".", 1)[-1])
    assert files_ignored_line(invalid) == "autre message"
