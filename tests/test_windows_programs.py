"""
test_windows_programs.py — Les programmes lancés par MosaicView le sont par
leur chemin complet, jamais par leur seul nom.

Pour un nom sans chemin (`explorer`, `notepad.exe`), Windows cherche d'abord
dans le dossier de l'exe, puis dans le dossier courant (souvent celui de la BD
ouverte par double-clic) : un fichier de même nom venu avec des BD
téléchargées y serait lancé à la place du programme de Windows.

- utils.windows_program rend un chemin complet existant ;
- garde-fou : aucun appel de subprocess dans modules/qt/ ni MosaicView.py dont
  le programme est un nom écrit sans chemin ;
- archive_loader._get_7z_exe ne dépend pas du dossier courant.

Fait partie de la suite pytest normale :  python -m pytest tests/
"""
import ast
import os
import types

import ast_loader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QT_DIR = os.path.join(ROOT, "modules", "qt")
_SUBPROCESS_CALLS = {"Popen", "run", "call", "check_call", "check_output"}


def _windows_program():
    return ast_loader.load("utils.py", {"windows_program"}, os=os)["windows_program"]


def test_explorer_is_in_the_windows_folder():
    path = _windows_program()("explorer.exe")
    assert os.path.isabs(path)
    assert os.path.isfile(path)
    assert os.path.basename(os.path.dirname(path)).lower() != "system32"


def test_notepad_is_in_system32():
    path = _windows_program()("notepad.exe")
    assert os.path.isabs(path)
    assert os.path.isfile(path)
    assert os.path.basename(os.path.dirname(path)).lower() == "system32"


def test_path_does_not_depend_on_current_folder(tmp_path, monkeypatch):
    windows_program = _windows_program()
    before = windows_program("explorer.exe")
    monkeypatch.chdir(tmp_path)
    assert windows_program("explorer.exe") == before


def _bare_program_calls(tree, name):
    """Appels subprocess.X([...]) dont le premier élément est un nom de
    programme écrit sans dossier. Seuls les appels de `subprocess` comptent :
    une autre méthode nommée run (PdfProbe.run("count", …)) n'en est pas un."""
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
                and node.func.attr in _SUBPROCESS_CALLS and node.args):
            continue
        first = node.args[0]
        if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
            first = first.elts[0]
        if (isinstance(first, ast.Constant) and isinstance(first.value, str)
                and "\\" not in first.value and "/" not in first.value):
            found.append(f"{name}:{node.lineno} {first.value!r}")
    return found


def _offenders():
    found = []
    files = [os.path.join(QT_DIR, n) for n in sorted(os.listdir(QT_DIR)) if n.endswith(".py")]
    files.append(os.path.join(ROOT, "MosaicView.py"))
    for path in files:
        found += _bare_program_calls(ast_loader.module_tree(path), os.path.basename(path))
    return found


def test_scan_detects_a_bare_program_name():
    """Le scan doit voir ce qu'il interdit : sinon le test ci-dessous
    passerait sans rien vérifier."""
    tree = ast.parse(
        "import subprocess\n"
        "subprocess.Popen(['explorer', p])\n"
        "subprocess.run(('notepad.exe', p))\n"
        "subprocess.Popen([windows_program('explorer.exe'), p])\n"
        "subprocess.run([exe, 'l', p])\n"
        "probe.run('count', p)\n")
    assert _bare_program_calls(tree, "x.py") == ["x.py:2 'explorer'", "x.py:3 'notepad.exe'"]


def test_no_program_launched_by_its_bare_name():
    offenders = _offenders()
    assert not offenders, (
        "Programme lancé par son seul nom (cherché par Windows dans le dossier "
        "courant) ; utiliser utils.windows_program :\n" + "\n".join(offenders))


def test_7z_is_found_next_to_the_code_not_in_current_folder(tmp_path, monkeypatch):
    get_7z_exe = ast_loader.load(
        "archive_loader.py", {"_get_7z_exe"},
        os=os, sys=types.SimpleNamespace(),  # pas de _MEIPASS : mode développement
        __file__=os.path.join(QT_DIR, "archive_loader.py"))["_get_7z_exe"]
    monkeypatch.chdir(tmp_path)
    assert os.path.normcase(get_7z_exe()) == os.path.normcase(
        os.path.join(ROOT, "7zip", "7z.exe"))
