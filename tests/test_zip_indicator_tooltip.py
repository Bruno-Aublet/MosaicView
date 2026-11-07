"""Info-bulle de l'indicateur ZIP sans comic ouvert (status_bar_qt._zip_no_comic_tooltip) :
la ligne du clic droit, reprise de l'info-bulle existante, dans toutes les langues.
Fonction extraite par AST, status_bar_qt.py important Qt."""
import json
from pathlib import Path

import ast_loader

LOCALES = Path(__file__).resolve().parent.parent / "locales"
OTHER_TOOLTIPS = ("zip_indicator_stored_action", "zip_indicator_deflated", "zip_indicator_na")


def _locales():
    for path in sorted(LOCALES.glob("*.json")):
        if path.stem != "language_names":
            yield path.stem, json.loads(path.read_text(encoding="utf-8"))


def _tooltip_in(data):
    def translate(key):
        node = data
        for part in key.split("."):
            node = node[part]
        return node
    return ast_loader.load("status_bar_qt.py", {"_zip_no_comic_tooltip"},
                           _=translate)["_zip_no_comic_tooltip"]()


def test_tooltip_is_only_the_right_click_line():
    data = {"tooltip": {"zip_indicator_stored_noop": "Pas compressé.\nClic droit."}}
    assert _tooltip_in(data) == "Clic droit."


def test_right_click_line_is_alone_and_complete_in_every_language():
    """Dernière ligne d'un texte de deux lignes : ni la phrase sur le fichier, qui serait
    fausse sans comic, ni paramètre laissé sans valeur."""
    for code, data in _locales():
        text = _tooltip_in(data)
        assert data["tooltip"]["zip_indicator_stored_noop"].count("\n") == 1, code
        assert text.strip() and "{" not in text, code


def test_every_zip_tooltip_ends_with_a_right_click_line():
    """Même position partout : la ligne reprise est bien celle du clic droit."""
    for code, data in _locales():
        for key in OTHER_TOOLTIPS:
            lines = data["tooltip"][key].split("\n")
            assert len(lines) == 3 and "{" not in lines[-1], (code, key)
