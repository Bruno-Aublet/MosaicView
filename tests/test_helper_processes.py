"""Programmes annexes (7z, UnRAR) jamais laissés en vie après MosaicView
(modules/qt/helper_processes.py).

Un programme lancé par start_helper, ou par rarfile une fois
keep_rarfile_helpers_with_app() appelée, est rattaché à un job Windows qui le
tue quand le processus qui l'a lancé se termine, même par os._exit ; un
programme lancé autrement (Explorateur, application par défaut) lui survit.
La fin du parent est vérifiée dans un processus séparé : celui des tests ne
peut pas se terminer pour la vérifier.
"""
import ast
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="objets job de Windows")

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


def _alive(pid):
    """Le programme `pid` tourne encore."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        k32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259                        # STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def _kill(pid):
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)


def _child_of_a_parent_that_exits(launch):
    """Pid d'un programme lancé par un processus parent qui se termine aussitôt
    par os._exit. launch : « helper » (start_helper) ou « plain »
    (subprocess.Popen, comme l'Explorateur ouvert pour l'utilisateur)."""
    script = (
        "import os, subprocess, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from modules.qt.helper_processes import start_helper\n"
        f"cmd = {SLEEPER!r}\n"
        f"start = start_helper if {launch!r} == 'helper' else subprocess.Popen\n"
        # Sorties vers DEVNULL : héritées, elles garderaient ouvert le tube lu
        # par subprocess.run tant que l'enfant vit
        "proc = start(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n"
        "             stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)\n"
        "print(proc.pid, flush=True)\n"
        "os._exit(0)\n")
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                         timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
    return int(out.stdout.strip())


def _wait_dead(pid, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return False


def test_helper_dies_with_the_process_that_started_it():
    """Parent terminé par os._exit (fermeture forcée, plantage) : son
    programme annexe meurt avec lui, au lieu de continuer seul."""
    pid = _child_of_a_parent_that_exits("helper")
    try:
        assert _wait_dead(pid)
    finally:
        _kill(pid)


def test_program_started_for_the_user_survives_the_application():
    """Un programme lancé sans start_helper (Explorateur, Bloc-notes) n'est
    jamais rattaché au job : il survit à l'application. Montre aussi que le
    test précédent mesure bien quelque chose."""
    pid = _child_of_a_parent_that_exits("plain")
    try:
        time.sleep(1)
        assert _alive(pid)
    finally:
        _kill(pid)


def _in_session_job(proc):
    import ctypes
    from ctypes import wintypes
    from modules.qt import helper_processes
    k32, job = helper_processes._kernel32_and_job()
    k32.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
    result = wintypes.BOOL()
    assert k32.IsProcessInJob(int(proc._handle), job, ctypes.byref(result))
    return bool(result.value)


def test_start_helper_attaches_the_program_to_the_session_job():
    from modules.qt.helper_processes import start_helper
    proc = start_helper(SLEEPER, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert _in_session_job(proc)
    finally:
        proc.kill()
        proc.wait()


def test_unrar_started_by_rarfile_is_attached_too():
    """rarfile lance UnRAR par sa fonction custom_popen : remplacée à l'import
    d'archive_members, elle rattache chaque programme au job ; un second
    appel ne la remplace pas une deuxième fois."""
    import rarfile
    from modules.qt import archive_members  # noqa: F401  (installe le remplacement)
    from modules.qt.helper_processes import keep_rarfile_helpers_with_app
    installed = rarfile.custom_popen
    assert installed._keeps_helpers_with_app
    keep_rarfile_helpers_with_app()
    assert rarfile.custom_popen is installed
    proc = rarfile.custom_popen(SLEEPER)
    try:
        assert _in_session_job(proc)
    finally:
        proc.kill()
        proc.wait()


def test_archive_readers_start_7z_and_unrar_only_through_start_helper():
    """Aucun subprocess.Popen/run direct dans archive_members.py : un 7z ou
    un UnRAR lancé autrement survivrait à MosaicView."""
    tree = ast.parse((ROOT / "modules" / "qt" / "archive_members.py").read_text(encoding="utf-8"))
    direct = [node.lineno for node in ast.walk(tree)
              if isinstance(node, ast.Attribute) and node.attr in ("Popen", "run", "call",
                                                                   "check_output")
              and isinstance(node.value, ast.Name) and node.value.id == "subprocess"]
    assert not direct, f"lancement direct ligne(s) {direct} : passer par start_helper"
