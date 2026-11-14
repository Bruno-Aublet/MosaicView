"""Garde-fou : toute modification de la liste des pages d'un document est
encadrée par un point d'historique AVANT et APRÈS elle (CLAUDE.md, « Toute
modification du document pose un état d'historique avant et après elle »).

Sans le point d'après, Annuler défait l'action en même temps que la
précédente, et Refaire ne la rend jamais (une page numérisée a déjà été
perdue ainsi). Sans le point d'avant, une modification restée sans point
d'historique est défaite en même temps que l'action.

Scan AST de modules/qt/ : une fonction qui modifie `<objet>.images_data`
(affectation, `[:] =`, `del`, append, insert, extend, pop, remove, sort…)
doit appeler une fonction de point d'historique (nom contenant `save_state`,
ou `callbacks["save_state"]`) avant la première modification et après la
dernière. Les points posés dans une fonction imbriquée comptent comme
« après » : c'est la suite de l'action (fin d'une renumérotation…).
Une fonction qui ne le fait pas elle-même est en règle si chacun de ses
appels, dans le même module, est encadré de la même façon par son appelant
(de proche en proche). Une exception justifiée va dans ALLOWED, avec sa
raison.
"""
import ast
from pathlib import Path

QT_DIR = Path(__file__).resolve().parent.parent / "modules" / "qt"

MUTATING_METHODS = frozenset({
    "append", "insert", "extend", "pop", "remove", "sort", "clear", "reverse",
})

# (fichier, fonction) -> raison
ALLOWED = {
    ("archive_loader.py", "ArchiveLoader.load"):
        "nouveau document : historique remis à zéro, premier point posé à la fin du chargement",
    ("archive_loader.py", "_install_loaded_pages"):
        "fin d'un chargement qui remplace le document : reset_history puis premier point",
    ("batch_metadata_dialog_qt.py", "_BatchMetadataOrchestrator._load_state_for_file"):
        "AppState temporaire d'un fichier de lot, sans historique ni panneau",
    ("comic_info.py", "_write_comic_fields"):
        "ajout du ComicInfo.xml : appelée par write_comic_metadata_from_scraper / "
        "_from_comicbookinfo, dont les appelants (autres modules) posent les points",
    ("comic_info.py", "replace_comic_info_bytes"):
        "annexe de l'original abîmé : partie d'une réécriture du ComicInfo.xml dont "
        "l'appelant (autres modules) pose les points",
    ("conversion_dialogs_qt.py", "perform_conversion.<_cancel>"):
        "Annuler de la conversion : retire les pages insérées, le document revient "
        "à l'état du point posé au lancement",
    ("conversion_dialogs_qt.py", "perform_conversion.<on_entry_ready>"):
        "page convertie insérée pendant le worker : point avant au lancement "
        "(perform_conversion), point après dans on_finished",
    ("conversion_dialogs_qt.py", "show_conversion_complete_dialog.<_handle_action>"):
        "réponse de la fenêtre de fin : point avant dans on_finished, point après "
        "dans _after_complete (on_done)",
    ("file_close_qt.py", "force_close_file"):
        "fermeture du document : historique remis à zéro",
    ("state.py", "AppState.__init__"):
        "construction de l'état vide",
    ("undo_redo_qt.py", "restore_state_qt"):
        "Annuler / Refaire lui-même : restaure un point de l'historique",
    ("virtual_library_panel.py", "VirtualLibraryPanel.open_from_file"):
        "panneau virtuel de la Bibliothèque : liste lue d'un fichier, sans historique",
}


def _is_pages(node):
    while isinstance(node, ast.Subscript):
        node = node.value
    return isinstance(node, ast.Attribute) and node.attr == "images_data"


def _is_undo_point(call):
    func = call.func
    if isinstance(func, ast.Subscript):
        return isinstance(func.slice, ast.Constant) and func.slice.value == "save_state"
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return "save_state" in name and name != "_save_state_for_file"


def _called_name(call):
    """Nom d'une fonction du module appelée par `nom(...)` ou `self.nom(...)`."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
            and func.value.id == "self":
        return func.attr
    return None


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _own_nodes(fn):
    """Nœuds du corps de `fn`, hors fonctions, lambdas et classes imbriquées."""
    stack = [n for n in fn.body if not isinstance(n, _SCOPES)]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(c for c in ast.iter_child_nodes(node) if not isinstance(c, _SCOPES))


def _functions(tree):
    """[(nom qualifié, nœud)] : `Classe.méthode`, `fonction.<imbriquée>`."""
    out = []

    def visit(node, prefix, in_function):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.", False)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}<{child.name}>" if in_function else f"{prefix}{child.name}"
                out.append((name, child))
                visit(child, f"{name}.", True)
            else:
                visit(child, prefix, in_function)

    visit(tree, "", False)
    return out


def _mutation_lines(fn):
    lines = []
    for n in _own_nodes(fn):
        targets = []
        if isinstance(n, ast.Assign):
            targets = n.targets
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
            targets = [n.target]
        elif isinstance(n, ast.Delete):
            targets = n.targets
        for target in targets:
            for t in (target.elts if isinstance(target, ast.Tuple) else [target]):
                if _is_pages(t):
                    lines.append(n.lineno)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr in MUTATING_METHODS and _is_pages(n.func.value):
            lines.append(n.lineno)
    return lines


def _undo_lines(fn):
    """(points du corps, points des fonctions imbriquées)."""
    own = [n.lineno for n in _own_nodes(fn) if isinstance(n, ast.Call) and _is_undo_point(n)]
    nested = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and _is_undo_point(n)]
    return own, nested


def _bracketed(fn, lines):
    own, nested = _undo_lines(fn)
    return (any(u < min(lines) for u in own)
            and any(u > max(lines) for u in own + nested))


def module_violations(source):
    """[(fonction, lignes modifiées)] des fonctions de `source` dont les
    modifications de la liste des pages ne sont pas encadrées."""
    tree = ast.parse(source)
    functions = _functions(tree)
    short = {}
    for qualname, fn in functions:
        short.setdefault(fn.name, []).append(fn)
    calls = {}  # fonction appelée -> [(appelant, ligne)]
    for _q, caller in functions:
        for n in _own_nodes(caller):
            if isinstance(n, ast.Call):
                name = _called_name(n)
                if name in short:
                    calls.setdefault(name, []).append((caller, n.lineno))

    def covered(fn, lines, seen):
        if _bracketed(fn, lines):
            return True
        if fn in seen:
            return False
        sites = calls.get(fn.name, [])
        return bool(sites) and all(covered(caller, [line], seen | {fn})
                                   for caller, line in sites)

    out = []
    for qualname, fn in functions:
        lines = _mutation_lines(fn)
        if lines and not covered(fn, lines, frozenset()):
            out.append((qualname, lines))
    return out


def _scan():
    found = {}
    for path in sorted(QT_DIR.glob("*.py")):
        for qualname, lines in module_violations(path.read_text(encoding="utf-8")):
            found[(path.name, qualname)] = lines
    return found


VIOLATIONS = _scan()


def test_every_change_of_the_pages_has_undo_points_before_and_after():
    bad = {k: v for k, v in VIOLATIONS.items() if k not in ALLOWED}
    assert not bad, (
        "Modification de la liste des pages sans point d'historique avant ET "
        "après (save_state) — Annuler défairait deux actions d'un coup ou ne "
        "rendrait pas une page. Poser les deux points, ou justifier l'exception "
        "dans ALLOWED :\n"
        + "\n".join(f"  {f} {name} (lignes {lines})" for (f, name), lines in sorted(bad.items())))


def test_allowed_list_is_not_stale():
    stale = [k for k in ALLOWED if k not in VIOLATIONS]
    assert not stale, f"Exceptions sans modification correspondante : {stale}"


def test_scan_sees_the_known_page_changes():
    """Le scan voit bien les modifications existantes : sinon le test principal
    passerait sans rien vérifier (attribut renommé…)."""
    assert ("undo_redo_qt.py", "restore_state_qt") in VIOLATIONS
    assert len(ALLOWED) >= 10


def test_scan_detects_missing_points_and_accepts_covered_helpers():
    source = '''
def no_point(state):
    state.images_data.append(1)

def only_after(state, callbacks):
    state.images_data.sort()
    callbacks["save_state"]()

def only_before(state):
    save_state_qt(state)
    del state.images_data[0]

def both(state):
    save_state_data(state)
    state.images_data[:] = []
    save_state_data(state)

def after_in_continuation(self, st):
    self.save_state()
    st.images_data.insert(0, 1)
    def _finish():
        self.save_state()
    self._renumber(on_done=_finish)

def _helper(state):
    state.images_data.pop()

def caller_of_helper(self, state):
    self.save_state()
    _helper(state)
    self.save_state()

def _helper_badly_called(state):
    state.images_data.remove(1)

def bad_caller(state):
    _helper_badly_called(state)
    save_state_data(state)

def local_list_is_ignored():
    images_data = []
    images_data.append(1)

def nested_outer(state):
    def inner():
        state.images_data.clear()
    return inner
'''
    found = sorted(name for name, _lines in module_violations(source))
    assert found == sorted(["no_point", "only_after", "only_before",
                            "_helper_badly_called", "nested_outer.<inner>"])
