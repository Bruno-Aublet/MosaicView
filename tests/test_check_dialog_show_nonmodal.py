"""Les fenêtres à AppWindowMixin (app_window_qt) s'affichent par
show_nonmodal() ou ask_async() (règle UI n° 5 de CLAUDE.md ; Audit 7, P4-4).

show_nonmodal() donne à la fenêtre sa taille, puis la place sur son panneau,
avant de l'afficher. Un .show() nu la montre à sa position par défaut, et
position_dialog_on_parent suivi de .show() la centre d'après sa taille par
défaut quand le code ne lui en a donné aucune : décalée, sans recentrage
ensuite.

Scan AST de modules/qt/ et de MosaicView.py, sans Qt.
"""
import ast
from pathlib import Path

import ast_loader

ROOT = Path(__file__).resolve().parent.parent
FILES = sorted((ROOT / "modules" / "qt").glob("*.py")) + [ROOT / "MosaicView.py"]
MIXINS = {"AppWindowMixin", "QuestionWindowMixin"}


def _app_windows(trees):
    """Noms des classes qui héritent, directement ou non, d'AppWindowMixin."""
    bases = {}
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases.setdefault(node.name, set()).update(
                    ast.unparse(b).split(".")[-1] for b in node.bases)
    found = set(MIXINS)
    grew = True
    while grew:
        new = {name for name, parents in bases.items() if parents & found} - found
        found |= new
        grew = bool(new)
    return found - MIXINS


def _own_nodes(func):
    out = []

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.ClassDef)):
                continue
            out.append(child)
            walk(child)
    walk(func)
    return out


def _bare_shows(tree, dialogs):
    found = []
    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        nodes = _own_nodes(func)
        assigns = [(n.lineno, ast.unparse(t), getattr(n.value.func, "id", None))
                   for n in nodes if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                   for t in n.targets]
        for n in nodes:
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "show" and not n.args):
                continue
            target = n.func.value
            if isinstance(target, ast.Call) and getattr(target.func, "id", None) in dialogs:
                found.append((n.lineno, target.func.id))
                continue
            name = ast.unparse(target)
            prev = [a for a in assigns if a[1] == name and a[0] <= n.lineno]
            if prev and prev[-1][2] in dialogs:
                found.append((n.lineno, prev[-1][2]))
    return found


def test_scan_finds_the_app_windows():
    windows = _app_windows([ast_loader.module_tree(p) for p in FILES])
    assert {"MsgDialog", "ErrorDialog", "InfoDialog", "ConfirmDialog", "QuestionYNCDialog",
            "ConfirmYNDialog"} <= windows


def test_app_windows_are_shown_with_show_nonmodal():
    trees = {path: ast_loader.module_tree(path) for path in FILES}
    dialogs = _app_windows(trees.values())
    bad = [f"{path.name}:{line} {cls}(...).show()"
           for path, tree in trees.items()
           for line, cls in _bare_shows(tree, dialogs)]
    assert not bad, ("utiliser show_nonmodal() ou ask_async() (taille puis placement "
                     "avant affichage, sans saut) :\n" + "\n".join(bad))


def test_scan_detects_every_form():
    tree = ast.parse(
        "class AppWindowMixin: pass\n"
        "class QuestionWindowMixin(AppWindowMixin): pass\n"
        "class Base(AppWindowMixin, QDialog): pass\n"
        "class Child(Base): pass\n"
        "class Asked(QuestionWindowMixin, QDialog): pass\n"
        "class Other(QDialog): pass\n"
        "def f(p):\n"
        "    Base(p).show()\n"
        "    dlg = Child(p)\n"
        "    dlg.show()\n"
        "    placed = Asked(p)\n"
        "    position_dialog_on_parent(placed, p)\n"
        "    placed.show()\n"
        "    Base(p).show_nonmodal()\n"
        "    dlg = Other(p)\n"
        "    dlg.show()\n")
    dialogs = _app_windows([tree])
    assert dialogs == {"Base", "Child", "Asked"}
    assert _bare_shows(tree, dialogs) == [(8, "Base"), (10, "Child"), (13, "Asked")]
