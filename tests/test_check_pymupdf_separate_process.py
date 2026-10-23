"""Garde-fou : PyMuPDF ne travaille jamais dans le process de l'application.

Un PDF malformé peut faire planter MuPDF en code natif, ce qui tue tout le
process qui l'exécute : dans l'application, ce serait la fermeture immédiate
des deux panneaux et la perte de leur travail non enregistré. Tout accès à un
PDF se fait donc dans un process séparé, qui a son propre interpréteur :
pdf_loading_qt._pdf_persistent_process (chargement, fusion, lots) et
pdf_probe._probe_process (lectures courtes, copie déverrouillée).

Scan AST de modules/qt/ : toute utilisation d'un attribut de PyMuPDF
(`fitz.open`, `fitz.Matrix`…) hors de ces fonctions est refusée. Un simple
`import pymupdf`, qui sert à savoir si la bibliothèque est installée, reste
permis.
"""
import ast
from pathlib import Path

import ast_loader

QT_DIR = Path(__file__).resolve().parent.parent / "modules" / "qt"
# Fonctions exécutées dans un process séparé
SEPARATE_PROCESS = {("pdf_loading_qt.py", "_pdf_persistent_process"),
                    ("pdf_probe.py", "_probe_process")}
# Noms sous lesquels le projet importe PyMuPDF (`import pymupdf as fitz`)
PYMUPDF_NAMES = {"fitz", "pymupdf"}


def _is_pymupdf_attribute(node):
    return isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
        and node.value.id in PYMUPDF_NAMES


def _uses_outside_separate_process(path):
    tree = ast_loader.module_tree(path)
    found = []

    def visit(node, in_process):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and (path.name, node.name) in SEPARATE_PROCESS:
            in_process = True
        if _is_pymupdf_attribute(node) and not in_process:
            found.append(f"{path.name}:{node.lineno} ({node.value.id}.{node.attr})")
        for child in ast.iter_child_nodes(node):
            visit(child, in_process)

    visit(tree, False)
    return found


def test_pymupdf_only_runs_in_a_separate_process():
    offenders = []
    for path in sorted(QT_DIR.glob("*.py")):
        offenders += _uses_outside_separate_process(path)
    assert not offenders, (
        "PyMuPDF utilisé dans le process de l'application (passer par "
        "pdf_loading_qt._spawn_pdf_process ou pdf_probe.PdfProbe) : "
        + ", ".join(offenders))


def test_the_check_sees_the_known_call_sites():
    """Le scan doit réellement trouver les appels existants : sinon il
    passerait sans rien vérifier (nom de module ou d'alias changé)."""
    calls = 0
    for path in sorted(QT_DIR.glob("*.py")):
        for node in ast.walk(ast_loader.module_tree(path)):
            if isinstance(node, ast.Call) and _is_pymupdf_attribute(node.func) \
                    and node.func.attr == "open":
                calls += 1
    assert calls >= 5
