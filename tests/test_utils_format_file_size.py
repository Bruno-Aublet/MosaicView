"""Taille d'un fichier écrite avec l'unité la plus adaptée (utils.format_file_size).

Unité traduite (clés units.*) et séparateur décimal de la langue : la
fonction est extraite par AST (tests/ast_loader.py), avec une traduction de
test qui rend l'unité française de la clé, et le séparateur choisi par le
test. Les traductions elles-mêmes sont vérifiées à part (toutes les langues
ont les cinq unités, avec {size}).
"""
import json
from pathlib import Path

import pytest

import ast_loader

UNITS = {"units.byte": "o", "units.kb": "Ko", "units.mb": "Mo", "units.gb": "Go", "units.tb": "To"}
LOCALES = Path(__file__).resolve().parent.parent / "locales"


def sizer(separator="."):
    return ast_loader.load(
        "utils.py", {"format_file_size", "_SIZE_UNITS", "decimal_text"},
        decimal_separator=lambda: separator,
        _units_text=lambda key, size: f"{size} {UNITS[key]}")["format_file_size"]


format_file_size = sizer()


def test_format_file_size_bytes():
    assert format_file_size(0) == "0 o"
    assert format_file_size(1) == "1 o"
    assert format_file_size(1023) == "1023 o"


def test_format_file_size_kilobytes():
    assert format_file_size(1024) == "1.0 Ko"
    assert format_file_size(1536) == "1.5 Ko"
    assert format_file_size(1024 * 1024 - 1) == "1024.0 Ko"


def test_format_file_size_megabytes():
    assert format_file_size(1024 * 1024) == "1.0 Mo"
    assert format_file_size(int(1.5 * 1024 * 1024)) == "1.5 Mo"


def test_format_file_size_gigabytes():
    assert format_file_size(1024 * 1024 * 1024) == "1.00 Go"
    assert format_file_size(int(2.5 * 1024 * 1024 * 1024)) == "2.50 Go"


def test_format_file_size_terabytes():
    assert format_file_size(1024 * 1024 * 1024 * 1024) == "1.00 To"
    assert format_file_size(int(3.25 * 1024**4)) == "3.25 To"
    # Pas d'unité au-delà : les très grandes tailles restent en To
    assert format_file_size(2048 * 1024**4) == "2048.00 To"


def test_format_file_size_boundary_just_below_next_unit():
    # Juste en dessous du seuil Mo : reste en Ko
    assert format_file_size(1024 * 1024 - 1) == "1024.0 Ko"
    # Juste en dessous du seuil Go : reste en Mo
    assert format_file_size(1024 * 1024 * 1024 - 1) == "1024.0 Mo"


def test_decimal_separator_of_the_language():
    with_comma = sizer(",")
    assert with_comma(1536) == "1,5 Ko"
    assert with_comma(int(2.5 * 1024**3)) == "2,50 Go"
    assert with_comma(1023) == "1023 o"


@pytest.mark.parametrize("path", sorted(p for p in LOCALES.glob("*.json")
                                        if p.name != "language_names.json"),
                         ids=lambda p: p.stem)
def test_every_language_has_the_five_units(path):
    units = json.loads(path.read_text(encoding="utf-8")).get("units", {})
    assert sorted(units) == sorted(key.split(".")[1] for key in UNITS), path.name
    assert all("{size}" in text for text in units.values()), path.name
