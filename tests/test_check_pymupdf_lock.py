"""Garde-fou : dans le process de l'application, PyMuPDF n'est jamais utilisé
hors du verrou PYMUPDF_LOCK (modules/qt/pymupdf_lock.py).

PyMuPDF ne peut pas servir à plusieurs threads en même temps : deux appels
simultanés (scan de la Bibliothèque et aperçu d'un PDF, par exemple) peuvent
faire planter toute l'application. Le process PDF dédié
(pdf_loading_qt._pdf_persistent_process) a son propre interpréteur : il n'est
pas concerné.

Scan AST de modules/qt/ : tout appel `fitz.open(...)` doit se trouver dans un
bloc `with PYMUPDF_LOCK:` ou dans le process dédié.
"""
import ast
from pathlib import Path

QT_DIR = Path(__file__).resolve().parent.parent / "modules" / "qt"
# Fonctions exécutées dans un process séparé, non concernées
SEPARATE_PROCESS = {("pdf_loading_qt.py", "_pdf_persistent_process")}


def _uses_lock(with_node):
    return any(isinstance(item.context_expr, ast.Name) and item.context_expr.id == "PYMUPDF_LOCK"
               for item in with_node.items)


def _unlocked_fitz_opens(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []

    def visit(node, locked, in_process):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and (path.name, node.name) in SEPARATE_PROCESS:
            in_process = True
        if isinstance(node, (ast.With, ast.AsyncWith)) and _uses_lock(node):
            locked = True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "open" and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "fitz" and not locked and not in_process:
            found.append(f"{path.name}:{node.lineno}")
        for child in ast.iter_child_nodes(node):
            visit(child, locked, in_process)

    visit(tree, False, False)
    return found


def test_every_in_process_pymupdf_open_is_locked():
    offenders = []
    for path in sorted(QT_DIR.glob("*.py")):
        offenders += _unlocked_fitz_opens(path)
    assert not offenders, (
        "fitz.open hors de `with PYMUPDF_LOCK:` (modules/qt/pymupdf_lock.py) : "
        + ", ".join(offenders))


def test_the_check_sees_the_known_call_sites():
    """Le scan doit réellement trouver les appels existants : sinon il
    passerait sans rien vérifier (nom de module ou d'alias changé)."""
    calls = 0
    for path in sorted(QT_DIR.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "open" and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == "fitz":
                calls += 1
    assert calls >= 5
