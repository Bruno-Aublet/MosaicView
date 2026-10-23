"""Glisser-déposer : la copie des pages écrite dans les temporaires au début
d'un glisser est supprimée quand elle ne sert pas (audit 8, P3-5).

MosaicCanvas._start_drag écrit la sélection dans un dossier drag_* de
MosaicViewTemp (nécessaire pour un dépôt dans l'Explorateur). Un dépôt interne
ou vers l'autre panneau, et un glisser abandonné (Échap, cible qui refuse :
IgnoreAction), ne s'en servent pas : sans suppression, une copie de la
sélection par glisser restait jusqu'à la fermeture de l'application.

mosaic_canvas.py importe PySide6 et _start_drag exige un vrai glisser Qt : le
contrôle porte sur le code de la fonction.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _start_drag_source():
    source = (ROOT / "modules" / "qt" / "mosaic_canvas.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "_start_drag":
            return ast.unparse(node)
    raise AssertionError("_start_drag introuvable")


def test_abandoned_drag_removes_its_temp_copy():
    body = _start_drag_source()
    assert "result_action == Qt.IgnoreAction" in body
    cleanup = next(line for line in body.splitlines() if "rmtree(tmp_dir" in line)
    condition = body[:body.index(cleanup)].rsplit("if ", 1)[1]
    assert "_drop_was_internal" in condition and "dropped_nowhere" in condition
