"""
test_check_no_modal.py — Garde-fou anti-régression : INTERDIT toute fenêtre modale.

Règle absolue du projet (architecture double-panneau) : AUCUNE fenêtre Qt ne doit
être modale, JAMAIS. Une modale gèle toute l'application, donc l'autre panneau.

Ce test scanne modules/qt/ et signale tout motif de modalité :
  - setModal(True)
  - setWindowModality(Qt.ApplicationModal | Qt.WindowModal)
  - appels .exec() / .exec_()  (boucle d'événements bloquante)

Fait partie de la suite pytest normale :  python -m pytest tests/
"""
import ast
import os

import pytest

QT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "modules", "qt")

# Liste blanche : (fichier_relatif, ligne_approx, motif) explicitement tolérés.
# Doit rester VIDE à terme. Chaque entrée doit être justifiée par un commentaire.
WHITELIST: set[tuple[str, str]] = set()


def scan_file(path: str, rel: str):
    findings = []
    with open(path, encoding="utf-8") as f:
        src = f.read()
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        findings.append((rel, e.lineno or 0, f"SyntaxError: {e.msg}"))
        return findings

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        name = func.attr

        # .exec() / .exec_() SANS argument = QDialog modal bloquant → interdit.
        # AVEC argument (menu.exec(pos), drag.exec(action)) = menu contextuel /
        # drag&drop : comportement Qt normal, NON modal au sens architectural → toléré.
        if name in ("exec", "exec_"):
            if not node.args and not node.keywords:
                findings.append((rel, node.lineno, f".{name}()"))

        # setModal(True)
        elif name == "setModal":
            if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value is True:
                findings.append((rel, node.lineno, "setModal(True)"))

        # setWindowModality(... ApplicationModal | WindowModal)
        elif name == "setWindowModality":
            if node.args:
                arg = node.args[0]
                txt = ast.unparse(arg) if hasattr(ast, "unparse") else ""
                if "ApplicationModal" in txt or "WindowModal" in txt:
                    findings.append((rel, node.lineno, f"setWindowModality({txt})"))

    return findings


def scan_modal_classes(path: str, rel: str):
    """Retourne les classes de dialogue qui se declarent modales (setModal(True)).
    C'est l'INDICATEUR PRINCIPAL : une classe modale est la cause ; ses .exec()
    sont les symptomes. Le vrai travail = ramener ce nombre a 0."""
    classes = []
    with open(path, encoding="utf-8") as f:
        src = f.read()
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError:
        return classes
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = []
        for b in node.bases:
            if isinstance(b, ast.Name):
                bases.append(b.id)
            elif isinstance(b, ast.Attribute):
                bases.append(b.attr)
        if not any("Dialog" in b or "QWidget" in b for b in bases):
            continue
        body = ast.get_source_segment(src, node) or ""
        if "setModal(True)" in body:
            classes.append((rel, node.name))
    return classes


def test_no_modal_windows_in_modules_qt():
    all_findings = []
    modal_classes = []
    for root, dirs, files in os.walk(QT_DIR):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, QT_DIR).replace("\\", "/")
            for f_rel, lineno, motif in scan_file(path, rel):
                if (f_rel, motif) in WHITELIST:
                    continue
                all_findings.append((f_rel, lineno, motif))
            modal_classes.extend(scan_modal_classes(path, rel))

    modal_classes.sort()
    all_findings.sort()

    if not all_findings and not modal_classes:
        return

    # RAPPEL : QDialog.exec() est MODAL PAR DEFAUT, meme SANS setModal(True). Le vrai
    # compteur de modalite = le nombre de .exec() sans argument. Compter seulement les
    # classes "setModal(True)" rate les QDialog.exec() implicitement modaux.
    lines = [f"{len(all_findings)} appel(s) modal(aux) .exec()/setModal détecté(s) dans modules/qt/ :"]
    for rel, lineno, motif in all_findings:
        lines.append(f"  {rel}:{lineno}  {motif}")
    if modal_classes:
        lines.append(f"(Dont {len(modal_classes)} classe(s) déclarant explicitement setModal(True) :)")
        for rel, cls in modal_classes:
            lines.append(f"  {rel}  ->  class {cls}")
    lines.append("La règle interdit TOUTE modalité (CLAUDE.md, règle UI n°4).")
    pytest.fail("\n".join(lines))
