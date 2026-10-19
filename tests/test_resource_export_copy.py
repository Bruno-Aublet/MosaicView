"""Export des fichiers de l'application depuis le mode d'emploi (polices
tengwar, cri de Wilhelm, icônes) : _copy_resource_files.

Une copie qui échoue est rendue avec sa raison, jamais avalée : la fenêtre de
succès annonçait sinon tous les fichiers, et un échec complet passait pour
« aucun fichier trouvé ».

user_guide_qt.py importe PySide6 au niveau module : la fonction est extraite
par AST et exécutée seule, sans aucun import Qt.
"""
import ast
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    source = (ROOT / "modules" / "qt" / "user_guide_qt.py").read_text(encoding="utf-8")
    nodes = [n for n in ast.parse(source).body
             if isinstance(n, ast.FunctionDef) and n.name == "_copy_resource_files"]
    assert len(nodes) == 1
    ns = {"os": os, "shutil": shutil}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "user_guide_qt.py", "exec"), ns)
    return ns["_copy_resource_files"]


copy_resource_files = _load()


def test_copies_existing_sources(tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"A")
    out = tmp_path / "out"
    out.mkdir()
    copied, failures = copy_resource_files([(str(src), str(out / "a.png"))])
    assert copied == [str(out / "a.png")] and failures == []
    assert (out / "a.png").read_bytes() == b"A"


def test_failed_copy_is_reported_with_its_reason(tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"A")
    missing_dir = tmp_path / "missing" / "a.png"
    copied, failures = copy_resource_files([(str(src), str(missing_dir))])
    assert copied == []
    assert len(failures) == 1 and failures[0][0] == "a.png" and failures[0][1]


def test_missing_source_is_neither_copied_nor_a_failure(tmp_path):
    copied, failures = copy_resource_files([(str(tmp_path / "absent.png"), str(tmp_path / "x.png"))])
    assert copied == [] and failures == []
