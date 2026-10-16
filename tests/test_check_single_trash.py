"""
test_check_single_trash.py — Garde-fou : une seule implémentation de « mettre
à la Corbeille », utils.send_to_trash.

Plusieurs copies divergent : l'une se repliait sur une suppression définitive
quand send2trash manquait, une autre ignorait l'échec du repli. Tout module de
modules/qt/ autre qu'utils.py qui importe send2trash ou appelle
SHFileOperationW fait échouer ce test : il doit appeler send_to_trash.

Fait partie de la suite pytest normale :  python -m pytest tests/
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QT_DIR = os.path.join(ROOT, "modules", "qt")
ALLOWED = {"utils.py"}


def _offenders():
    found = []
    for name in sorted(os.listdir(QT_DIR)):
        if not name.endswith(".py") or name in ALLOWED:
            continue
        with open(os.path.join(QT_DIR, name), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name == "send2trash" for a in node.names):
                found.append(f"{name}:{node.lineno} import send2trash")
            elif isinstance(node, ast.ImportFrom) and node.module == "send2trash":
                found.append(f"{name}:{node.lineno} from send2trash import")
            elif isinstance(node, ast.Attribute) and node.attr == "SHFileOperationW":
                found.append(f"{name}:{node.lineno} SHFileOperationW")
    return found


def test_trash_goes_through_send_to_trash():
    offenders = _offenders()
    assert not offenders, (
        "Mise à la Corbeille hors de utils.send_to_trash :\n" + "\n".join(offenders))
