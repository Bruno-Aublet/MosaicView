"""
test_check_no_shared_pdf_process.py — Garde-fou anti-régression : INTERDIT tout
process PDF partagé entre panneaux/traitements.

Règle absolue du projet (architecture double-panneau) : chaque chargement/merge/
batch PDF doit créer et détruire son propre process (modules/qt/pdf_loading_qt.py
:: _spawn_pdf_process/_kill_pdf_process), jamais un singleton module-level réutilisé
par plusieurs appelants. Un singleton partagé provoque une erreur "pipe broken" (et
la perte du chargement d'un panneau innocent) dès que deux chargements PDF tournent
en même temps — voir skill pdf-loading.

Ce test scanne modules/qt/ et signale :
  - toute réapparition des anciens noms de globals singleton (symptôme direct
    d'un retour en arrière vers un process partagé) ;
  - tout appel à _pdf_persistent_process ailleurs que dans sa seule fabrique
    autorisée (_spawn_pdf_process) — un second point d'appel direct signifierait
    qu'un nouveau chemin de code démarre son propre process "maison" au lieu de
    passer par la fabrique commune.

Fait partie de la suite pytest normale :  python -m pytest tests/
"""
import ast
import os

import pytest

QT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "modules", "qt")

# Noms de l'ancien mécanisme à singleton (process PDF partagé entre panneaux),
# interdits partout dans modules/qt/ — leur réapparition signale un retour en
# arrière vers le bug "pipe broken" corrigé par l'architecture "un process par
# chargement" (_spawn_pdf_process/_kill_pdf_process).
FORBIDDEN_NAMES = {
    "_warm_process", "_warm_in_q", "_warm_out_conn", "_warm_out_q",
    "_merge_process", "_merge_in_q", "_merge_out_conn",
    "_ensure_warm_process", "_ensure_merge_process", "warmup_pdf_process",
}

# Seul fichier autorisé à appeler _pdf_persistent_process directement (sa propre
# fabrique) ; tout autre appelant doit passer par _spawn_pdf_process/_kill_pdf_process.
PDF_PROCESS_TARGET = "_pdf_persistent_process"
ALLOWED_TARGET_FILE = "pdf_loading_qt.py"


def scan_forbidden_names(path: str, rel: str):
    findings = []
    with open(path, encoding="utf-8") as f:
        src = f.read()
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        findings.append((rel, e.lineno or 0, f"SyntaxError: {e.msg}"))
        return findings

    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.Global):
            for n in node.names:
                if n in FORBIDDEN_NAMES:
                    findings.append((rel, node.lineno, f"global {n}"))
            continue

        if name in FORBIDDEN_NAMES:
            findings.append((rel, node.lineno, name))

    return findings


def scan_process_target(path: str, rel: str):
    """Retourne les lignes où _pdf_persistent_process est utilisé comme cible
    d'un process (target=... ou positionnel), hors définition de la fonction elle-même."""
    findings = []
    with open(path, encoding="utf-8") as f:
        src = f.read()
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError:
        return findings

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == PDF_PROCESS_TARGET:
            continue  # la définition elle-même n'est pas un point d'appel
        if not isinstance(node, ast.Name):
            continue
        if node.id != PDF_PROCESS_TARGET:
            continue
        if isinstance(node.ctx, ast.Load):
            findings.append((rel, node.lineno))

    return findings


def test_no_shared_pdf_process_globals():
    all_findings = []
    for root, dirs, files in os.walk(QT_DIR):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, QT_DIR).replace("\\", "/")
            all_findings.extend(scan_forbidden_names(path, rel))

    if not all_findings:
        return

    all_findings.sort()
    lines = [f"{len(all_findings)} référence(s) à l'ancien mécanisme de process PDF partagé détectée(s) dans modules/qt/ :"]
    for rel, lineno, motif in all_findings:
        lines.append(f"  {rel}:{lineno}  {motif}")
    lines.append("Chaque chargement/merge/batch PDF doit créer et détruire son propre process via")
    lines.append("_spawn_pdf_process()/_kill_pdf_process() (pdf_loading_qt.py) — jamais un singleton")
    lines.append("module-level partagé entre panneaux ou traitements (voir skill pdf-loading).")
    pytest.fail("\n".join(lines))


def test_pdf_persistent_process_started_only_via_factory():
    all_findings = []
    for root, dirs, files in os.walk(QT_DIR):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, QT_DIR).replace("\\", "/")
            findings = scan_process_target(path, rel)
            if rel == ALLOWED_TARGET_FILE:
                # Dans son propre fichier, seul l'appel fait par _spawn_pdf_process
                # (target=_pdf_persistent_process) est légitime ; on tolère ce fichier
                # dans son ensemble puisque c'est l'implémentation de la fabrique.
                continue
            all_findings.extend(findings)

    if not all_findings:
        return

    all_findings.sort()
    lines = [f"{len(all_findings)} point(s) d'appel direct à {PDF_PROCESS_TARGET} détecté(s) hors {ALLOWED_TARGET_FILE} :"]
    for rel, lineno in all_findings:
        lines.append(f"  {rel}:{lineno}")
    lines.append(f"Tout nouveau code qui a besoin d'un process PDF doit passer par")
    lines.append(f"_spawn_pdf_process()/_kill_pdf_process() (pdf_loading_qt.py), jamais démarrer")
    lines.append(f"son propre process {PDF_PROCESS_TARGET} directement (voir skill pdf-loading).")
    pytest.fail("\n".join(lines))
