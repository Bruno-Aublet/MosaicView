"""Instance unique : le chemin d'un fichier n'est transmis qu'à un MosaicView
du même compte Windows (RAPPORT_AUDITS.md, Audit 6, P4-5).

Le canal nommé par lequel un second lancement passe son fichier à la fenêtre
déjà ouverte est visible de tous les comptes du PC ; un autre compte connecté
au même moment pourrait le créer le premier et recevoir les chemins.

Vérifié :
- process_owner.process_belongs_to_current_user : vrai pour le process
  courant et ses enfants ; faux pour un process d'un autre compte (Système,
  services), y compris quand Windows laisse lire son propriétaire ; faux pour
  un process introuvable ;
- single_instance_qt.try_forward_to_running_instance : la vérification a
  lieu avant tout envoi, et un refus fait raccrocher et démarrer normalement.

single_instance_qt.py importe PySide6 : sa fonction est lue par AST.
"""
import ast
import getpass
import os
import subprocess
import sys

import psutil

import ast_loader
from modules.qt import process_owner
from modules.qt.process_owner import process_belongs_to_current_user


def test_current_process_belongs_to_the_current_user():
    assert process_belongs_to_current_user(os.getpid())


def test_child_process_belongs_to_the_current_user():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        assert process_belongs_to_current_user(child.pid)
    finally:
        child.kill()
        child.wait()


def test_system_process_does_not_belong_to_the_current_user():
    assert not process_belongs_to_current_user(4)          # « System »


def test_missing_process_is_not_ours():
    assert not process_belongs_to_current_user(0)
    assert not process_belongs_to_current_user(None)
    assert not process_belongs_to_current_user(4_000_000)


def test_readable_process_of_another_account_is_not_ours():
    """Le refus ne repose pas seulement sur un accès refusé : quand Windows
    laisse lire le propriétaire d'un process d'un autre compte (services,
    Système), la comparaison des comptes répond « non »."""
    me = getpass.getuser().lower()
    found = 0
    for proc in psutil.process_iter(["username"]):
        user = (proc.info["username"] or "").split("\\")[-1].lower()
        if not user or user == me:
            continue
        handle = process_owner._kernel32.OpenProcess(
            process_owner._PROCESS_QUERY_LIMITED_INFORMATION, False, proc.pid)
        if not handle:
            continue
        readable = process_owner._token_user_sid(handle) is not None
        process_owner._kernel32.CloseHandle(handle)
        if readable:
            found += 1
            assert not process_belongs_to_current_user(proc.pid), proc.pid
        if found >= 5:
            break
    assert found, "aucun process d'un autre compte lisible sur ce poste"


def _forward_function():
    tree = ast_loader.module_tree(os.path.join(str(ast_loader.QT_DIR), "single_instance_qt.py"))
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "try_forward_to_running_instance")


def _calls(node, name):
    return sorted(c.lineno for c in ast.walk(node) if isinstance(c, ast.Call)
                  and (getattr(c.func, "id", None) == name or getattr(c.func, "attr", None) == name))


def test_path_is_sent_only_after_the_owner_check():
    func = _forward_function()
    checks = _calls(func, "process_belongs_to_current_user")
    writes = _calls(func, "write")
    assert checks and writes and max(checks) < min(writes)


def test_refused_owner_hangs_up_and_starts_normally():
    """Process d'un autre compte : abort() puis return False, comme sans
    bannière — l'appelant démarre une instance normale."""
    func = _forward_function()
    check = next(n for n in ast.walk(func) if isinstance(n, ast.If)
                 and "process_belongs_to_current_user" in ast.unparse(n.test))
    body = "\n".join(ast.unparse(s) for s in check.body)
    assert "sock.abort()" in body and "return False" in body
