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
   fonctions de ALLOWED_ZIP_WRITERS, chacune justifiée. L'écrivain de
   file_operations_qt.py (`_write_zip_with_progress`) ne doit lui-même être
   appelé que depuis une fonction ou un lambda passé à
   `replace_file_atomically`, qui lui fournit un fichier temporaire.

3. Le CBZ produit est contrôlé : chaque écrivain de ALLOWED_ZIP_WRITERS
   déclare dans WRITER_CHECKS ce qu'il fait. Remplacer un fichier existant
   exige `check_written_cbz_structure` dans la fonction qui remplace, appelé
   avant le remplacement (`replace_file_atomically` / `replace_file_retrying`) ;
   créer un fichier neuf, dont la source peut être supprimée, exige sa
   relecture (`verify_written_cbz` ou `_cbz_verified`). Les appelants de
   l'écrivain des lots (`_interruptible_zip_writer`) font l'un ou l'autre.
   Sans cette règle, un contrôle ajouté à un seul écrivain laissait les
   autres remplacer l'original sans contrôle (Audit 7, P3-2 / P4-3). Contrôle
   statique : il attrape l'oubli, pas un appel mal placé dans un autre chemin.

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

GUARDED_WRITERS = ("_write_zip_with_progress",)
WRITE_MODES = ("w", "a", "x")

CHECK_CALLS = ("check_written_cbz_structure",)
VERIFY_CALLS = ("verify_written_cbz", "_cbz_verified")
REPLACE_CALLS = ("replace_file_atomically", "replace_file_retrying")

# Écrivain de ALLOWED_ZIP_WRITERS -> [(fichier, fonction qui doit contrôler, nature)]
# nature : "remplace" (contrôle de structure avant le remplacement) ou "neuf"
# (relecture complète avant suppression de la source). Pour l'écrivain des
# lots, ce sont ses appelants qui contrôlent (voir test dédié).
WRITER_CHECKS = {
    ("file_operations_qt.py", "_write_zip_with_progress"):
        [("file_operations_qt.py", "_write_document_unlocked", "remplace")],
    ("library_window.py", "LibraryWindow._convert_file_to_cbz._on_load_finished._write"):
        [("library_window.py", "LibraryWindow._convert_file_to_cbz", "neuf")],
    ("library_window.py", "LibraryWindow._rewrite_comicinfo_in_cbz._write"):
        [("library_window.py", "LibraryWindow._rewrite_comicinfo_in_cbz", "remplace")],
    ("batch_metadata_dialog_qt.py", "_BatchMetadataOrchestrator._save_state_for_file"):
        [("batch_metadata_dialog_qt.py", "_BatchMetadataOrchestrator._save_state_for_file", "remplace"),
         ("batch_metadata_dialog_qt.py", "_BatchMetadataOrchestrator._save_state_for_file", "neuf")],
    ("batch_dialogs_qt.py", "_interruptible_zip_writer"): "appelants",
}
BATCH_WRITER = "_interruptible_zip_writer"


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
        "_write_zip_with_progress doit être appelé depuis une fonction ou un "
        "lambda passé à replace_file_atomically :\n"
        + "\n".join(f"  {f}:{line} ({q})" for f, q, line in bad))


def test_allowed_lists_are_not_stale():
    """Une exception qui ne correspond plus à aucun appel doit être retirée."""
    used_moves = {(f, q) for f, q, _line in MOVES}
    used_writers = {(f, q) for f, q, _line in ZIP_WRITES}
    stale = [k for k in ALLOWED_MOVES if k not in used_moves]
    stale += [k for k in ALLOWED_ZIP_WRITERS if k not in used_writers]
    assert not stale, f"Exceptions sans appel correspondant : {stale}"


def _functions_by_qualname(fname):
    """{nom qualifié: nœud} des fonctions et méthodes du fichier."""
    path = os.path.join(QT_DIR, fname)
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    out = {}

    def walk(node, qual):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = qual + [child.name]
                if not isinstance(child, ast.ClassDef):
                    out[".".join(q)] = child
                walk(child, q)
            else:
                walk(child, qual)

    walk(tree, [])
    return out


def _call_lines(func, names):
    return sorted(n.lineno for n in ast.walk(func) if isinstance(n, ast.Call)
                  and (getattr(n.func, "id", None) in names or getattr(n.func, "attr", None) in names))


def _check_problem(func, nature):
    """Texte du manquement, None si la fonction contrôle comme il faut."""
    if nature == "neuf":
        return None if _call_lines(func, VERIFY_CALLS) else "aucune relecture (verify_written_cbz)"
    checks, replaces = _call_lines(func, CHECK_CALLS), _call_lines(func, REPLACE_CALLS)
    if not checks:
        return "aucun check_written_cbz_structure avant le remplacement"
    if replaces and min(checks) > max(replaces):
        return "check_written_cbz_structure placé après le remplacement"
    return None


def test_every_zip_writer_declares_its_check():
    missing = [k for k in ALLOWED_ZIP_WRITERS if k not in WRITER_CHECKS]
    stale = [k for k in WRITER_CHECKS if k not in ALLOWED_ZIP_WRITERS]
    assert not missing, ("Écrivain ZIP sans contrôle déclaré dans WRITER_CHECKS "
                         "(remplace : check_written_cbz_structure ; neuf : verify_written_cbz) :\n"
                         + "\n".join(f"  {f} ({q})" for f, q in missing))
    assert not stale, f"WRITER_CHECKS sans écrivain correspondant : {stale}"


def test_written_cbz_is_checked_before_it_replaces_or_deletes_anything():
    bad = []
    for writer, scopes in WRITER_CHECKS.items():
        if scopes == "appelants":
            continue
        for fname, qual, nature in scopes:
            func = _functions_by_qualname(fname).get(qual)
            if func is None:
                bad.append(f"  {fname} ({qual}) : fonction introuvable")
                continue
            problem = _check_problem(func, nature)
            if problem:
                bad.append(f"  {fname} ({qual}, {nature}) : {problem}")
    assert not bad, "CBZ produit non contrôlé :\n" + "\n".join(bad)


def test_batch_writer_callers_check_what_they_write():
    """Chaque fonction de premier niveau qui appelle _interruptible_zip_writer
    relit le CBZ neuf (_cbz_verified) ou contrôle celui qui remplace l'original
    (check_written_cbz_structure, avant le remplacement)."""
    funcs = _functions_by_qualname("batch_dialogs_qt.py")
    callers = {q.split(".")[0] for q, f in funcs.items()
               if q.split(".")[0] != BATCH_WRITER and _call_lines(f, (BATCH_WRITER,))}
    assert len(callers) >= 7, callers
    bad = []
    for name in sorted(callers):
        func = funcs[name]
        problems = [_check_problem(func, nature) for nature in ("neuf", "remplace")]
        if all(problems):
            bad.append(f"  batch_dialogs_qt.py ({name}) : ni relecture ni contrôle de structure")
    assert not bad, "CBZ des lots non contrôlé :\n" + "\n".join(bad)


def test_check_rule_detects_missing_and_misplaced_checks():
    def func(src):
        return ast.parse(src).body[0]
    assert _check_problem(func("def f():\n    replace_file_retrying(t, d)\n"), "remplace")
    assert _check_problem(func("def f():\n    replace_file_retrying(t, d)\n"
                               "    check_written_cbz_structure(t, 1)\n"), "remplace")
    assert _check_problem(func("def f():\n    check_written_cbz_structure(t, 1)\n"
                               "    replace_file_retrying(t, d)\n"), "remplace") is None
    assert _check_problem(func("def f():\n    send_to_trash(src)\n"), "neuf")
    assert _check_problem(func("def f():\n    verify_written_cbz(p)\n"), "neuf") is None


def test_scan_finds_the_known_writers():
    """Le scan doit voir les écritures qu'il est censé surveiller : sinon les
    autres tests passeraient sans rien vérifier."""
    assert ("file_operations_qt.py", "_write_zip_with_progress") in {(f, q) for f, q, _l in ZIP_WRITES}
    assert len(GUARDED_CALLS) >= 1
