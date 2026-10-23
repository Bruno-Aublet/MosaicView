"""MsgDialog, ErrorDialog et InfoDialog s'affichent par show_nonmodal()
(règle UI n° 5 de CLAUDE.md ; Audit 7, P4-4).

Ces fenêtres ne se centrent sur leur panneau, dans showEvent, qu'au tick qui
suit l'affichage : un .show() nu les montre d'abord centrées sur la fenêtre
principale, puis les déplace (saut visible, surtout en double panneau).
show_nonmodal() les place avant de les afficher. Un .show() reste admis
quand position_dialog_on_parent est appelée juste avant, dans la même
fonction.

Scan AST de modules/qt/, sans Qt.
"""
import ast
from pathlib import Path

import ast_loader

QT = Path(__file__).resolve().parent.parent / "modules" / "qt"
DIALOGS = ("MsgDialog", "ErrorDialog", "InfoDialog")


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


def _bare_shows(tree):
    found = []
    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        nodes = _own_nodes(func)
        assigns = [(n.lineno, ast.unparse(t), getattr(n.value.func, "id", None))
                   for n in nodes if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                   for t in n.targets]
        positioned = [n.lineno for n in nodes if isinstance(n, ast.Call)
                      and getattr(n.func, "id", None) == "position_dialog_on_parent"]
        for n in nodes:
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "show" and not n.args):
                continue
            target = n.func.value
            if isinstance(target, ast.Call) and getattr(target.func, "id", None) in DIALOGS:
                found.append((n.lineno, target.func.id))
                continue
            name = ast.unparse(target)
            prev = [a for a in assigns if a[1] == name and a[0] <= n.lineno]
            if prev and prev[-1][2] in DIALOGS \
                    and not any(prev[-1][0] < line <= n.lineno for line in positioned):
                found.append((n.lineno, prev[-1][2]))
    return found


def test_message_dialogs_are_shown_with_show_nonmodal():
    bad = [f"{path.name}:{line} {cls}(...).show()"
           for path in sorted(QT.glob("*.py"))
           for line, cls in _bare_shows(ast_loader.module_tree(path))]
    assert not bad, ("utiliser show_nonmodal() (placement avant affichage, sans saut) :\n"
                     + "\n".join(bad))


def test_scan_detects_both_forms():
    tree = ast.parse(
        "def f(p):\n"
        "    ErrorDialog(p, 't', 'm').show()\n"
        "    dlg = InfoDialog(p, 't', 'm')\n"
        "    dlg.show()\n"
        "    ok = MsgDialog(p, 't', 'm')\n"
        "    position_dialog_on_parent(ok, p)\n"
        "    ok.show()\n"
        "    ErrorDialog(p, 't', 'm').show_nonmodal()\n")
    assert _bare_shows(tree) == [(2, "ErrorDialog"), (4, "InfoDialog")]
