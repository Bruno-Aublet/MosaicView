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
   relecture (`verify_written_cbz`). L'écrivain commun des conversions
   (`converted_cbz.write_cbz`, et `_interruptible_zip_writer` des lots qui
   l'enveloppe) relit lui-même le CBZ avant de lui donner son nom, sauf
   `reread=False` / `verify=False` ; les appelants des lots s'appuient sur
   cette relecture ou contrôlent la structure du CBZ qui remplace l'original.
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

import ast_loader

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
    ("converted_cbz.py", "write_cbz"):
        "écrivain commun des conversions : temporaire du dossier, contrôlé puis renommé ; "
        "temporaire supprimé en cas d'échec",
}

GUARDED_WRITERS = ("_write_zip_with_progress",)
WRITE_MODES = ("w", "a", "x")

CHECK_CALLS = ("check_written_cbz_structure",)
VERIFY_CALLS = ("verify_written_cbz",)
REPLACE_CALLS = ("replace_file_atomically", "replace_file_retrying")

# Écrivain de ALLOWED_ZIP_WRITERS -> [(fichier, fonction qui doit contrôler, nature)]
# nature : "remplace" (contrôle de structure avant le remplacement) ou "neuf"
# (relecture complète avant suppression de la source). L'écrivain des lots,
# qui n'écrit plus lui-même de ZIP, a son test dédié, comme ses appelants.
WRITER_CHECKS = {
    ("file_operations_qt.py", "_write_zip_with_progress"):
        [("file_operations_qt.py", "_write_document_unlocked", "remplace")],
    ("library_window.py", "LibraryWindow._convert_file_to_cbz._on_load_finished._write"):
        [("library_window.py", "LibraryWindow._convert_file_to_cbz", "neuf")],
    ("library_window.py", "LibraryWindow._rewrite_comicinfo_in_cbz._write"):
        [("library_window.py", "LibraryWindow._rewrite_comicinfo_in_cbz", "remplace")],
    ("converted_cbz.py", "write_cbz"):
        [("converted_cbz.py", "write_cbz", "remplace"),
         ("converted_cbz.py", "write_cbz", "neuf")],
}
BATCH_WRITER = "_interruptible_zip_writer"
# Écrivains qui relisent le CBZ avant de lui donner son nom -> mot-clé qui
# désactive cette relecture
VERIFYING_WRITERS = {BATCH_WRITER: "verify", "write_cbz": "reread"}


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
        tree = ast_loader.module_tree(path)
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
    tree = ast_loader.module_tree(os.path.join(QT_DIR, fname))
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


def _is_verifying_writer(call):
    """Appel d'un écrivain qui relit le CBZ avant de lui donner son nom
    (relecture non désactivée)."""
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    name = getattr(func, "id", None) or getattr(func, "attr", None)
    off = VERIFYING_WRITERS.get(name)
    return off is not None and not any(
        k.arg == off and isinstance(k.value, ast.Constant) and k.value.value is False
        for k in call.keywords)


def _verifying_writer_lines(func):
    return sorted(n.lineno for n in ast.walk(func) if _is_verifying_writer(n))


def _reread_lines(func):
    """Relectures complètes : verify_written_cbz, écrivain qui relit, et
    converted_cbz.reread_then_delete_source, qui relit avant de supprimer
    (test_common_conversion_module_deletes_a_source_only_after_its_reread)."""
    return sorted(_call_lines(func, VERIFY_CALLS + (REREADING_DELETE,))
                  + _verifying_writer_lines(func))


def _structure_checking_writer_lines(func):
    """Appels d'un écrivain commun qui contrôle lui-même la structure du CBZ
    avant de lui donner son nom (check_structure=True)."""
    return sorted(n.lineno for n in ast.walk(func)
                  if isinstance(n, ast.Call) and _call_name(n) in VERIFYING_WRITERS
                  and any(k.arg == "check_structure" and isinstance(k.value, ast.Constant)
                          and k.value.value is True for k in n.keywords))


def _check_problem(func, nature):
    """Texte du manquement, None si la fonction contrôle comme il faut."""
    if nature == "neuf":
        return None if _reread_lines(func) else "aucune relecture (verify_written_cbz)"
    checks = sorted(_call_lines(func, CHECK_CALLS) + _structure_checking_writer_lines(func))
    replaces = _call_lines(func, REPLACE_CALLS)
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
    relit le CBZ neuf (relecture de l'écrivain, verify non désactivé) ou
    contrôle celui qui remplace l'original (check_written_cbz_structure, avant
    le remplacement)."""
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
    assert _check_problem(func("def f():\n    reread_then_delete_source(p, 2, src)\n"),
                          "neuf") is None
    # Écrivain qui contrôle la structure avant de donner le nom (recompression)
    assert _check_problem(func("def f():\n    with _interruptible_zip_writer(p, verify=False, "
                               "check_structure=True) as w:\n        pass\n"), "remplace") is None
    assert _check_problem(func("def f():\n    with _interruptible_zip_writer(p, verify=False) "
                               "as w:\n        pass\n"), "remplace")
    assert _check_problem(func("def f():\n    with _interruptible_zip_writer(p, verify=False, "
                               "check_structure=flag) as w:\n        pass\n"), "remplace")


# ---------------------------------------------------------------------------
# Règle 4 : relecture AVANT la suppression de la source d'une conversion
# ---------------------------------------------------------------------------
#
# La règle 3 exige qu'une relecture existe dans la fonction ; elle ne dit
# rien de sa place, ni des fins différées des conversions lancées depuis un
# panneau (réponse à « Supprimer l'original ? »). Ici : dans la fonction la
# plus proche qui supprime la source (Corbeille ou suppression définitive du
# même fichier), un appel de relecture doit précéder chaque suppression.
# Fichier -> nombre minimal de suppressions attendues (le scan doit les voir).
SOURCE_DELETERS = {
    # Enregistrer sous et conversion d'Appliquer : un reread_then_delete_source
    # commun (_delete_original_after_save)
    "file_operations_qt.py": 1,
    "library_window.py": 1,         # conversion depuis la Bibliothèque
    "batch_dialogs_qt.py": 6,       # six lots, un delete_source chacun
    "batch_metadata_dialog_qt.py": 1,   # un delete_source
}
TRASH_CALLS = ("_safe_delete", "safe_delete", "send_to_trash")
# Suppressions par converted_cbz : delete_source exige que son premier argument
# soit un écrivain qui relit, refermé avant l'appel ; reread_then_delete_source
# relit elle-même le CBZ juste avant la suppression.
COMMON_DELETE = "delete_source"
REREADING_DELETE = "reread_then_delete_source"
# Corbeille hors conversion : bases de la Bibliothèque supprimées à la demande
NOT_A_CONVERSION = {("library_window.py", "db_path_norm"), ("library_window.py", "old_path_norm")}


def _trash_target(call):
    """Argument d'un appel de mise à la Corbeille, None sinon."""
    func = call.func
    name = getattr(func, "id", None) or getattr(func, "attr", None)
    if name in TRASH_CALLS and call.args:
        return ast.unparse(call.args[0])
    # callbacks['safe_delete_file'](chemin) : Corbeille transmise par un panneau
    if (isinstance(func, ast.Subscript) and isinstance(func.slice, ast.Constant)
            and func.slice.value == "safe_delete_file" and call.args):
        return ast.unparse(call.args[0])
    return None


def _source_deletions(fname, tree=None):
    """[(fonction la plus proche, appel de suppression)] de la source d'une
    conversion : delete_source et reread_then_delete_source, mises à la
    Corbeille, et os.remove du même chemin. `tree` : arbre à scanner à la
    place du fichier (auto-contrôle)."""
    if tree is None:
        tree = ast_loader.module_tree(os.path.join(QT_DIR, fname))
    by_func = {}

    def walk(node, func):
        for child in ast.iter_child_nodes(node):
            inner = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            if isinstance(child, ast.Call) and func is not None:
                by_func.setdefault(func, []).append(child)
            walk(child, inner)

    walk(tree, None)
    found = []
    for func, calls in by_func.items():
        targets = {_trash_target(c) for c in calls} - {None}
        targets = {t for t in targets if (fname, t) not in NOT_A_CONVERSION}
        for call in calls:
            if _call_name(call) in (COMMON_DELETE, REREADING_DELETE):
                found.append((func, call))
                continue
            target = _trash_target(call)
            if target is None and ast.unparse(call.func) == "os.remove" and call.args:
                target = ast.unparse(call.args[0])
                if target not in targets:
                    continue          # temporaire ou CBZ rejeté, pas la source
            if target in targets:
                found.append((func, call))
    return found


def _call_name(call):
    return getattr(call.func, "id", None) or getattr(call.func, "attr", None)


def _verified_before(func, call):
    name = _call_name(call)
    if name == REREADING_DELETE:
        return True
    if name == COMMON_DELETE:
        # delete_source(w, …) : `w` est la cible d'un `with` d'écrivain qui
        # relit, refermé avant l'appel (CBZ relu puis nommé)
        if not call.args or not isinstance(call.args[0], ast.Name):
            return False
        return any(isinstance(node, ast.With) and node.end_lineno < call.lineno
                   and any(_is_verifying_writer(item.context_expr)
                           and isinstance(item.optional_vars, ast.Name)
                           and item.optional_vars.id == call.args[0].id
                           for item in node.items)
                   for node in ast.walk(func))
    return any(line < call.lineno for line in _reread_lines(func))


def test_batch_writer_is_the_common_writer_with_its_reread():
    """L'écrivain des lots passe par converted_cbz.write_cbz, qui relit le CBZ
    avant de lui donner son nom (test dédié plus haut) : un CBZ relu après
    avoir pris son nom devrait être supprimé par ce nom s'il est abîmé, alors
    qu'un autre programme ou un enregistrement depuis un panneau peut y avoir
    écrit entre-temps (Audit 10, constat B). Sa relecture n'est désactivée
    que par son propre `verify`, et il ne supprime rien lui-même."""
    func = _functions_by_qualname("batch_dialogs_qt.py")[BATCH_WRITER]
    calls = [n for n in ast.walk(func) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "write_cbz"]
    assert len(calls) == 1
    keywords = {k.arg: ast.unparse(k.value) for k in calls[0].keywords if k.arg}
    assert keywords.get("reread") == "verify"
    assert keywords.get("check_structure") == "check_structure"
    assert keywords.get("before_write") == "_raise_if_batch_stopped"
    assert not [n for n in ast.walk(func) if isinstance(n, ast.Call)
                and ast.unparse(n.func) in ("os.remove", "zipfile.ZipFile")]


def test_common_conversion_module_deletes_a_source_only_after_its_reread():
    """converted_cbz.py, seule implémentation de « écrire, relire, supprimer » :
    write_cbz relit avant de donner son nom et n'autorise la suppression
    qu'après ; la suppression (_remove_source) n'est appelée que par
    delete_source, après le contrôle de cette autorisation, et par
    reread_then_delete_source, après sa relecture ; aucune autre fonction du
    module ne supprime ni ne met à la Corbeille."""
    funcs = _functions_by_qualname("converted_cbz.py")
    write = funcs["write_cbz"]
    verify = _call_lines(write, VERIFY_CALLS)
    rename = _call_lines(write, ("replace_file_retrying",))
    allowed = [n.lineno for n in ast.walk(write) if isinstance(n, ast.Assign)
               and any(ast.unparse(t) == "writer.source_may_be_deleted" for t in n.targets)]
    assert verify and rename and allowed and max(verify) < min(rename) < min(allowed)
    removals = [n for n in ast.walk(write) if isinstance(n, ast.Call)
                and ast.unparse(n.func) == "os.remove"]
    assert all(ast.unparse(n.args[0]) == "tmp_path" for n in removals)

    callers = {q for q, f in funcs.items() if q != "_remove_source"
               and _call_lines(f, ("_remove_source",))}
    assert callers == {"delete_source", "reread_then_delete_source"}, callers
    delete = funcs["delete_source"]
    guard = [n.lineno for n in ast.walk(delete) if isinstance(n, ast.Raise)]
    assert guard and min(guard) < min(_call_lines(delete, ("_remove_source",)))
    reread = funcs["reread_then_delete_source"]
    assert _call_lines(reread, VERIFY_CALLS)[0] < _call_lines(reread, ("_remove_source",))[0]

    deleting = {q for q, f in funcs.items()
                if any(isinstance(n, ast.Call) and _trash_target(n) is not None for n in ast.walk(f))
                or any(isinstance(n, ast.Call) and ast.unparse(n.func) == "os.remove"
                       and ast.unparse(n.args[0]) != "tmp_path" for n in ast.walk(f))}
    assert deleting == {"_remove_source"}, deleting


def _unchecked_common_writer_calls(tree):
    """Appels de write_cbz qui ne relisent pas toujours le CBZ (`reread`
    autre que True) sans demander le contrôle de sa structure
    (check_structure=True) : le CBZ prendrait son nom sans aucun contrôle."""
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _call_name(node) == "write_cbz"):
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        rereads = "reread" not in kw or (isinstance(kw["reread"], ast.Constant)
                                         and kw["reread"].value is True)
        checks = isinstance(kw.get("check_structure"), ast.Constant) \
            and kw["check_structure"].value is True
        if not (rereads or checks):
            bad.append(node.lineno)
    return bad


def test_every_common_writer_call_checks_what_it_writes():
    # L'écrivain des lots transmet son propre `verify` : ses appelants sont
    # contrôlés par test_batch_writer_callers_check_what_they_write
    wrapper = _functions_by_qualname("batch_dialogs_qt.py")[BATCH_WRITER]
    exempt = ("batch_dialogs_qt.py", range(wrapper.lineno, wrapper.end_lineno + 1))
    bad = [f"  {fname}:{line}" for fname, path in _scanned_files()
           for line in _unchecked_common_writer_calls(ast_loader.module_tree(path))
           if not (fname == exempt[0] and line in exempt[1])]
    assert not bad, ("write_cbz sans relecture ni contrôle de structure "
                     "(check_structure=True) :\n" + "\n".join(bad))
    tree = ast_loader.module_tree(os.path.join(QT_DIR, "batch_metadata_dialog_qt.py"))
    assert any(isinstance(n, ast.Call) and _call_name(n) == "write_cbz" for n in ast.walk(tree)), \
        "le scan ne voit plus l'écriture de l'import de métadonnées"


def test_common_writer_rule_detects_an_unchecked_call():
    def bad(src):
        return _unchecked_common_writer_calls(ast.parse(src))
    assert bad("write_cbz(p, reread=False)")
    assert bad("write_cbz(p, reread=is_conversion)")
    assert bad("write_cbz(p, reread=False, check_structure=flag)")
    assert not bad("write_cbz(p)")
    assert not bad("write_cbz(p, reread=True)")
    assert not bad("write_cbz(p, reread=is_conversion, check_structure=True)")


def test_source_of_a_conversion_is_deleted_only_after_the_new_cbz_is_reread():
    bad, counts = [], {}
    for fname, minimum in SOURCE_DELETERS.items():
        deletions = _source_deletions(fname)
        counts[fname] = len(deletions)
        bad += [f"  {fname} ({func.name}, {ast.unparse(call)})"
                for func, call in deletions if not _verified_before(func, call)]
    short = {f: n for f, n in counts.items() if n < SOURCE_DELETERS[f]}
    assert not short, f"Le scan ne voit plus toutes les suppressions de source : {short}"
    assert not bad, ("Source supprimée sans relecture préalable du CBZ produit "
                     "(verify_written_cbz, ou écrivain des lots qui relit) :\n" + "\n".join(bad))


def test_reread_rule_detects_a_missing_or_late_check():
    def func(src):
        return ast.parse(src).body[0]
    late = func("def f():\n    safe_delete(src)\n    verify_written_cbz(new, 2)\n")
    early = func("def f():\n    verify_written_cbz(new, 2)\n    safe_delete(src)\n")
    delete_late = next(n for n in ast.walk(late) if isinstance(n, ast.Call)
                       and _trash_target(n) is not None)
    delete_early = next(n for n in ast.walk(early) if isinstance(n, ast.Call)
                        and _trash_target(n) is not None)
    assert not _verified_before(late, delete_late)
    assert _verified_before(early, delete_early)
    lot = ast.parse("callbacks['safe_delete_file'](p)").body[0].value
    assert _trash_target(lot) == "p"


def _direct_deletions(fname, tree=None):
    """Suppressions de source qui ne passent pas par converted_cbz."""
    return [(func, call) for func, call in _source_deletions(fname, tree)
            if _call_name(call) not in (COMMON_DELETE, REREADING_DELETE)]


def test_conversions_delete_their_source_only_through_the_common_module():
    """« Écrire, relire, supprimer » n'est écrit qu'une fois (converted_cbz) :
    une conversion qui mettrait elle-même sa source à la Corbeille, ou la
    supprimerait par os.remove, recréerait une copie de cette séquence."""
    bad = [f"  {fname} ({func.name}, {ast.unparse(call)})"
           for fname in SOURCE_DELETERS for func, call in _direct_deletions(fname)]
    assert not bad, ("Source d'une conversion supprimée hors de converted_cbz "
                     "(delete_source / reread_then_delete_source) :\n" + "\n".join(bad))


def test_direct_deletion_rule_detects_a_copy_of_the_sequence():
    def direct(src):
        return _direct_deletions("test.py", ast.parse(src))
    assert direct("def f():\n    verify_written_cbz(new, 2)\n    send_to_trash(src)\n")
    assert direct("def f():\n    if permanent:\n        os.remove(src)\n"
                  "    else:\n        callbacks['safe_delete_file'](src)\n")
    assert not direct("def f():\n    with write_cbz(p) as w:\n        pass\n"
                      "    delete_source(w, src)\n")
    assert not direct("def f():\n    reread_then_delete_source(new, 2, src)\n")
    assert not direct("def f():\n    os.remove(tmp_path)\n")     # temporaire, pas une source


def test_reread_rule_detects_a_delete_source_without_a_reread_writer():
    def verdict(body):
        f = ast.parse("def f():\n" + body).body[0]
        call = next(n for n in ast.walk(f) if isinstance(n, ast.Call)
                    and _call_name(n) == COMMON_DELETE)
        return _verified_before(f, call)
    after_writer = ("    with write_cbz(p) as w:\n        w.writestr('a', b'')\n"
                    "    delete_source(w, src)\n")
    after_batch_writer = ("    with _interruptible_zip_writer(p) as w:\n        pass\n"
                          "    delete_source(w, src, permanent=True)\n")
    assert verdict(after_writer) and verdict(after_batch_writer)
    assert not verdict("    with write_cbz(p, reread=False) as w:\n        pass\n"
                       "    delete_source(w, src)\n")
    assert not verdict("    with _interruptible_zip_writer(p, verify=False) as w:\n"
                       "        pass\n    delete_source(w, src)\n")
    assert not verdict("    with write_cbz(p) as w:\n        delete_source(w, src)\n")
    assert not verdict("    w = object()\n    delete_source(w, src)\n")
    assert not verdict("    with write_cbz(p) as w:\n        pass\n"
                       "    delete_source(other, src)\n")
    assert not verdict("    delete_source(w, src)\n    with write_cbz(p) as w:\n"
                       "        pass\n")


def test_scan_finds_the_known_writers():
    """Le scan doit voir les écritures qu'il est censé surveiller : sinon les
    autres tests passeraient sans rien vérifier."""
    assert ("file_operations_qt.py", "_write_zip_with_progress") in {(f, q) for f, q, _l in ZIP_WRITES}
    assert len(GUARDED_CALLS) >= 1
