"""
test_check_atomic_file_writes.py — Garde-fou anti-régression : un fichier de
l'utilisateur (CBZ) ne doit jamais pouvoir être tronqué ou détruit par une
écriture qui échoue en cours de route.

Deux règles, vérifiées par scan AST de modules/qt/*.py et MosaicView.py :

1. Pas de `shutil.move` (alias compris) : sous Windows, os.rename refuse une
   destination existante et shutil.move bascule alors sur une copie qui tronque
   la destination avant de la réécrire. Remplacer un fichier se fait par
   `replace_file_atomically` (utils.py) ou par un temporaire du même dossier
   suivi d'`os.replace`. Seule exception : ALLOWED_MOVES.

2. Pas d'écriture ZIP (`zipfile.ZipFile(..., "w"/"a"/"x")`) en dehors des
   fonctions de ALLOWED_ZIP_WRITERS, chacune justifiée. Les deux écrivains de
   file_operations_qt.py (`_write_cbz_entries`, `_write_zip_with_progress`) ne
   doivent eux-mêmes être appelés que depuis une fonction ou un lambda passé à
   `replace_file_atomically`, qui leur fournit un fichier temporaire.

Une nouvelle écriture qui fait échouer ce test doit passer par
replace_file_atomically (skill save-export) ; ne l'ajouter à une liste
d'exceptions qu'avec une justification équivalente.

Fait partie de la suite pytest normale :  python -m pytest tests/
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QT_DIR = os.path.join(ROOT, "modules", "qt")

# (fichier, fonction qualifiée) -> raison
ALLOWED_MOVES = {
    ("config_manager.py", "ConfigManager._migrate_from_temp"):
        "migration unique de la configuration vers un emplacement où elle n'existe pas encore",
}

ALLOWED_ZIP_WRITERS = {
    ("file_operations_qt.py", "_write_cbz_entries"):
        "écrit dans le temporaire fourni par replace_file_atomically (règle 2, 2e partie)",
    ("file_operations_qt.py", "_write_zip_with_progress"):
        "écrit dans le temporaire fourni par replace_file_atomically (règle 2, 2e partie)",
    ("library_window.py", "LibraryWindow._convert_file_to_cbz._on_load_finished._write"):
        "fonction passée à replace_file_atomically",
    ("library_window.py", "LibraryWindow._rewrite_comicinfo_in_cbz._write"):
        "fonction passée à replace_file_atomically",
    ("batch_dialogs_qt.py", "_interruptible_zip_writer"):
        "lots : nouveau CBZ au nom libre (jamais d'écrasement) ou temporaire de la "
        "recompression suivi d'os.replace ; fichier incomplet supprimé à l'interruption",
    ("batch_metadata_dialog_qt.py", "_BatchMetadataOrchestrator._save_state_for_file"):
        "temporaire à côté du fichier, puis os.replace ; temporaire supprimé en cas d'échec",
}

GUARDED_WRITERS = ("_write_cbz_entries", "_write_zip_with_progress")
WRITE_MODES = ("w", "a", "x")


def _scanned_files():
    for name in sorted(os.listdir(QT_DIR)):
        if name.endswith(".py"):
            yield name, os.path.join(QT_DIR, name)
    yield "MosaicView.py", os.path.join(ROOT, "MosaicView.py")


def _module_aliases(tree, module):
    """Noms locaux désignant `module` (import x / import x as y), et noms importés
    directement depuis lui (from x import a as b) -> {nom local: nom d'origine}."""
    modules, members = set(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module:
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == module:
            for alias in node.names:
                members[alias.asname or alias.name] = alias.name
    return modules, members


def _calls_with_scope(tree):
    """(appel, nom qualifié de la fonction englobante, chaîne des nœuds englobants)."""
    out = []

    def walk(node, qual, chain):
        for child in ast.iter_child_nodes(node):
            q = qual
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = qual + [child.name]
            if isinstance(child, ast.Call):
                out.append((child, ".".join(q), chain))
            walk(child, q, chain + [child])

    walk(tree, [], [])
    return out


def _is_call_to(call, modules, members, attr):
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr == attr and isinstance(func.value, ast.Name) and func.value.id in modules
    if isinstance(func, ast.Name):
        return members.get(func.id) == attr
    return False


def _zip_mode(call):
    mode = None
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        mode = call.args[1].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return mode


def _scan():
    moves, zip_writes, guarded_calls = [], [], []
    for fname, path in _scanned_files():
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        shutil_mods, shutil_members = _module_aliases(tree, "shutil")
        zip_mods, zip_members = _module_aliases(tree, "zipfile")

        # Lambdas et noms de fonctions passés à replace_file_atomically
        atomic_lambdas, atomic_names = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name == "replace_file_atomically":
                    for arg in node.args:
                        if isinstance(arg, ast.Lambda):
                            atomic_lambdas.add(id(arg))
                        elif isinstance(arg, ast.Name):
                            atomic_names.add(arg.id)

        for call, qual, chain in _calls_with_scope(tree):
            where = (fname, qual or "<module>", call.lineno)
            if _is_call_to(call, shutil_mods, shutil_members, "move"):
                moves.append(where)
            elif _is_call_to(call, zip_mods, zip_members, "ZipFile") and _zip_mode(call) in WRITE_MODES:
                zip_writes.append(where)
            elif getattr(call.func, "id", None) in GUARDED_WRITERS:
                enclosing = next((n for n in reversed(chain)
                                  if isinstance(n, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef))),
                                 None)
                ok = enclosing is not None and (
                    (isinstance(enclosing, ast.Lambda) and id(enclosing) in atomic_lambdas)
                    or (not isinstance(enclosing, ast.Lambda) and enclosing.name in atomic_names))
                guarded_calls.append((where, ok))
    return moves, zip_writes, guarded_calls


MOVES, ZIP_WRITES, GUARDED_CALLS = _scan()


def test_no_shutil_move():
    bad = [w for w in MOVES if (w[0], w[1]) not in ALLOWED_MOVES]
    assert not bad, (
        "shutil.move tronque la destination existante sous Windows ; utiliser "
        "replace_file_atomically (utils.py) :\n"
        + "\n".join(f"  {f}:{line} ({q})" for f, q, line in bad))


def test_no_zip_write_outside_allowed_writers():
    bad = [w for w in ZIP_WRITES if (w[0], w[1]) not in ALLOWED_ZIP_WRITERS]
    assert not bad, (
        "Écriture ZIP hors des écrivains autorisés : écrire via "
        "replace_file_atomically (utils.py) :\n"
        + "\n".join(f"  {f}:{line} ({q})" for f, q, line in bad))


def test_guarded_writers_only_write_temp_files():
    bad = [w for w, ok in GUARDED_CALLS if not ok]
    assert not bad, (
        "_write_cbz_entries / _write_zip_with_progress doivent être appelés depuis "
        "une fonction ou un lambda passé à replace_file_atomically :\n"
        + "\n".join(f"  {f}:{line} ({q})" for f, q, line in bad))


def test_allowed_lists_are_not_stale():
    """Une exception qui ne correspond plus à aucun appel doit être retirée."""
    used_moves = {(f, q) for f, q, _line in MOVES}
    used_writers = {(f, q) for f, q, _line in ZIP_WRITES}
    stale = [k for k in ALLOWED_MOVES if k not in used_moves]
    stale += [k for k in ALLOWED_ZIP_WRITERS if k not in used_writers]
    assert not stale, f"Exceptions sans appel correspondant : {stale}"


def test_scan_finds_the_known_writers():
    """Le scan doit voir les écritures qu'il est censé surveiller : sinon les
    autres tests passeraient sans rien vérifier."""
    assert ("file_operations_qt.py", "_write_cbz_entries") in {(f, q) for f, q, _l in ZIP_WRITES}
    assert len(GUARDED_CALLS) >= 5
