"""Recherche ComicVine par nom de fichier.

- comicvine_dialog_qt.series_part_of_terms : partie « série » d'un terme tiré
  d'un nom de fichier, à laquelle la sélection automatique compare aussi les
  séries trouvées — comparées au seul terme entier, le titre de l'épisode
  peut faire gagner une série sans rapport.
- l'import de métadonnées en lot nettoie le nom de fichier comme la fenêtre
  ComicVine ouverte seule.

comicvine_dialog_qt.py importe PySide6 au niveau module : la fonction est
extraite par AST et exécutée seule.
"""
import ast
import difflib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
QT_DIR = ROOT / "modules" / "qt"


def _extract():
    """series_part_of_terms, _ComicVineDialog._clean_filename_for_search et
    les règles de module qu'elles utilisent (_LEADING_DATE, _NAMING_PREFIX)."""
    source = (QT_DIR / "comicvine_dialog_qt.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes = [n for n in tree.body
             if (isinstance(n, ast.Assign) and any(getattr(t, "id", "").startswith(("_DATE_", "_LEADING_DATE", "_NAMING_PREFIX"))
                                                    for t in n.targets))
             or (isinstance(n, ast.FunctionDef) and n.name == "series_part_of_terms")]
    dialog = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_ComicVineDialog")
    clean = next(n for n in dialog.body
                 if isinstance(n, ast.FunctionDef) and n.name == "_clean_filename_for_search")
    clean.decorator_list = []
    ns = {"re": re}
    exec(compile(ast.Module(body=nodes + [clean], type_ignores=[]), "comicvine_dialog_qt.py", "exec"), ns)
    return ns["series_part_of_terms"], ns["_clean_filename_for_search"]


series_part_of_terms, clean_filename_for_search = _extract()


@pytest.mark.parametrize("terms, expected", [
    ("Aaaa #001 - Bbbb - Cccc", "Aaaa"),
    ("Aaaa Bbbb 125", "Aaaa Bbbb"),
    ("Aaaa v1 002", "Aaaa"),
    ("Aaaa v1 188 - Cccc", "Aaaa"),
    ("Aaaa #12 (2011)", "Aaaa"),
    ("Aaaa 3 - Bbbb", "Aaaa"),
    # Nombre dans le nom de la série : seul le dernier nombre isolé est le
    # numéro d'épisode
    ("Aaaa 2099 001", "Aaaa 2099"),
    ("Aaaa 2099 v1 #3", "Aaaa 2099"),
    # Lettre après le numéro (épisode en plusieurs parties)
    ("Aaaa v1 007B - Bbbb", "Aaaa"),
    ("Aaaa 177b - Bbbb", "Aaaa"),
    # Sans numéro d'épisode : terme rendu tel quel
    ("Aaaa", "Aaaa"),
    ("52", "52"),
    ("Aaaa320b", "Aaaa320b"),
])
def test_series_part(terms, expected):
    assert series_part_of_terms(terms) == expected


@pytest.mark.parametrize("stem, expected", [
    # Date en tête, toutes formes : retirée
    ("2014-04-15 Aaaa 012", "Aaaa 012"),
    ("2014-04-15 - Aaaa 012", "Aaaa 012"),
    ("2014 04 15 Aaaa 012", "Aaaa 012"),
    ("20140415 Aaaa 012", "Aaaa 012"),
    ("2014_04_15 Aaaa 012", "Aaaa 012"),
    ("2014.04.15 Aaaa 012", "Aaaa 012"),
    ("2014-0412 Aaaa 012", "Aaaa 012"),
    ("2014-04 Aaaa 012", "Aaaa 012"),
    ("2014.04 Aaaa 012", "Aaaa 012"),
    ("15-04-2014 Aaaa 012", "Aaaa 012"),
    ("15.04.2014 Aaaa 012", "Aaaa 012"),
    ("15_04_2014 Aaaa 012", "Aaaa 012"),
    ("(2014-04-15) Aaaa 012", "Aaaa 012"),
    ("[15.04.2014] - Aaaa 012", "Aaaa 012"),
    # Préfixe de numérotation (zéro en tête, ou 5 chiffres et plus) : retiré,
    # après une date éventuelle
    ("06164 Aaaa 012", "Aaaa 012"),
    ("12345_Aaaa 012", "Aaaa 012"),
    ("2014-04-15_06164_Aaaa 012", "Aaaa 012"),
    # Nombre qui fait partie du nom, fausse date : gardés
    ("2001 - Aaaa v1 002", "2001 - Aaaa v1 002"),
    ("1978 Aaaa", "1978 Aaaa"),
    ("1234 Aaaa", "1234 Aaaa"),
    ("52 Aaaa", "52 Aaaa"),
    ("2014 Aaaa", "2014 Aaaa"),
    ("2014-04-32 Aaaa", "2014-04-32 Aaaa"),
    ("1990-13-01 Aaaa", "1990-13-01 Aaaa"),
    ("Aaaa 2014-04-15", "Aaaa 2014-04-15"),
])
def test_clean_filename_for_search(stem, expected):
    assert clean_filename_for_search(stem) == expected


def test_series_part_ranks_the_right_series_first():
    """Sur le terme entier, la série dont le nom reprend le titre de l'épisode
    gagne ; comparée aussi à la partie « série », la bonne l'emporte."""
    terms = "Aaaa #001 - Cccccccc Dddd - Eeeee"
    query, series_query = terms.lower(), series_part_of_terms(terms).lower()

    def ratio(a, b):
        return difflib.SequenceMatcher(None, a, b.lower()).ratio()

    found = ["Fff: Cccccccc Dddd", "Aaaa", "Cccccccc"]
    assert max(found, key=lambda name: ratio(query, name)) == "Fff: Cccccccc Dddd"
    assert max(found, key=lambda name: max(ratio(query, name),
                                           ratio(series_query, name))) == "Aaaa"


def test_page1_ranks_with_the_series_part():
    source = (QT_DIR / "comicvine_dialog_qt.py").read_text(encoding="utf-8")
    page1 = next(n for n in ast.parse(source).body
                 if isinstance(n, ast.ClassDef) and n.name == "_Page1Series")
    populate = next(n for n in page1.body
                    if isinstance(n, ast.FunctionDef) and n.name == "populate_series")
    assert "series_part_of_terms(terms)" in ast.unparse(populate)


def test_batch_import_cleans_the_file_name():
    source = (QT_DIR / "batch_metadata_dialog_qt.py").read_text(encoding="utf-8")
    orchestrator = next(n for n in ast.parse(source).body
                        if isinstance(n, ast.ClassDef) and n.name == "_BatchMetadataOrchestrator")
    update = next(n for n in orchestrator.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_update_dialog")
    assert "_clean_filename_for_search(" in ast.unparse(update)
