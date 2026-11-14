"""Tri des colonnes des tableaux ComicVine (comicvine_dialog_qt._NaturalSortItem).

Les nombres se comparent par leur valeur, décimaux compris (« 1.5 » entre
« 1 » et « 2 »). « ² » passe pour un chiffre pour str.isdigit() mais float()
le refuse : un titre ou un numéro qui en contenait faisait échouer le tri au
clic sur la colonne.

comicvine_dialog_qt.py importe PySide6 au niveau module : la méthode est
extraite par AST, la cellule Qt remplacée par une classe qui rend son texte.
"""
import re

import ast_loader

_SPLIT = ast_loader.load("comicvine_dialog_qt.py", {"_ISSUE_NUM_SPLIT_RE"}, re=re)
_Item = ast_loader.load_methods("comicvine_dialog_qt.py", "_NaturalSortItem", {"_sort_key"},
                                _ISSUE_NUM_SPLIT_RE=_SPLIT["_ISSUE_NUM_SPLIT_RE"])


class Cell(_Item):
    def __init__(self, text):
        self._text = text

    def text(self):
        return self._text


def _sorted(texts):
    return sorted(texts, key=lambda t: Cell(t)._sort_key())


def test_numbers_sort_by_value_decimals_included():
    assert _sorted(["10", "2", "1.5", "1", "Annual 1"]) == ["1", "1.5", "2", "10", "Annual 1"]


def test_superscript_digits_do_not_make_the_sort_fail():
    assert _sorted(["Aaaa²", "Aaaa", "²", "3"]) == ["3", "Aaaa", "Aaaa²", "²"]
