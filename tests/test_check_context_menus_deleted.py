"""Menus contextuels détruits après leur affichage (audit 8, P3-2).

Scan AST de modules/qt/ : un QMenu créé avec un parent puis affiché par
exec() ou popup() est détruit dans la même fonction (deleteLater). Sans cela,
le parent le garde, avec ses actions et ce que leurs lambdas capturent : un
menu de plus à chaque clic droit, jusqu'à la destruction du parent — la fin de
la session pour la Bibliothèque ou un panneau.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QT_DIR = ROOT / "modules" / "qt"


def _shown_menus_without_delete(source, filename):
    wrong, shown = [], 0
    for func in ast.walk(ast.parse(source)):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = ast.unparse(func)
        for node in ast.walk(func):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and ast.unparse(node.value.func) == "QMenu" and node.value.args):
                name = ast.unparse(node.targets[0])
                if f"{name}.exec(" not in body and f"{name}.popup(" not in body:
                    continue
                shown += 1
                if f"{name}.deleteLater()" not in body:
                    wrong.append(f"{filename}:{node.lineno} {func.name}")
    return wrong, shown


def test_shown_context_menus_are_deleted():
    wrong, shown = [], 0
    for path in sorted(QT_DIR.glob("*.py")):
        file_wrong, file_shown = _shown_menus_without_delete(
            path.read_text(encoding="utf-8"), path.name)
        wrong += file_wrong
        shown += file_shown
    assert shown >= 20
    assert not wrong, wrong


def test_scan_detects_a_menu_left_behind():
    source = (
        "def show(self, pos):\n"
        "    menu = QMenu(self)\n"
        "    menu.exec(pos)\n"
    )
    wrong, shown = _shown_menus_without_delete(source, "x.py")
    assert shown == 1 and wrong == ["x.py:2 show"]
