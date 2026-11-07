"""Onglet Métadonnées : les lignes restent tassées vers le haut, quel que soit le
nombre de champs et que le ComicInfo.xml ait ou non une section <Pages>.

Sans ressort en bas du contenu, Qt répartit la hauteur libre entre les lignes :
avec peu de champs, elles s'écartent sur toute la page.

tabs_qt.py importe PySide6 : lecture de l'arbre syntaxique, sans Qt.
"""
import ast
from pathlib import Path

TABS = Path(__file__).resolve().parent.parent / "modules" / "qt" / "tabs_qt.py"


def _method(name):
    tree = ast.parse(TABS.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MetadataTab")
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _adds_stretch(node):
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "addStretch"
               and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "_vlay"
               for n in ast.walk(node))


def test_rows_stay_at_the_top_with_a_pages_section():
    assert _adds_stretch(_method("_build_pages_section"))


def test_rows_stay_at_the_top_without_a_pages_section():
    refresh = _method("refresh")
    last_if = [n for n in refresh.body if isinstance(n, ast.If)][-1]
    assert any("_build_pages_section" in ast.unparse(s) for s in last_if.body)
    assert any(_adds_stretch(s) for s in last_if.orelse)
