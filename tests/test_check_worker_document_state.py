"""Garde-fou : un worker (`QThread`) ne modifie jamais l'état du document.

Règle « Workers asynchrones » de CLAUDE.md : un worker ne touche qu'aux
entrées qu'il traite (`entry["bytes"]`…), jamais au reste de l'état du
document (liste des pages, ComicInfo.xml et métadonnées, historique,
sélection, drapeau « modifié »…) ni aux signaux de métadonnées. Ce travail se
fait à la fin, sur le thread de l'interface : écrit depuis le thread du worker,
il croise les modifications faites en même temps par l'interface (pages
supprimées ou déplacées, Annuler) et en perd une partie.

Scan AST de modules/qt/ : pour chaque sous-classe de QThread, le code
exécuté par `run()` — ses propres méthodes et les fonctions du même module
qu'il appelle, de proche en proche — ne doit contenir :
- aucune affectation, suppression ou méthode de modification (append, update,
  clear…) visant un attribut d'état du document (DOCUMENT_STATE_ATTRS) d'un
  autre objet que le worker lui-même (`state.modified`, `self._state.images_data`) ;
- aucun appel aux fonctions qui modifient le document (DOCUMENT_STATE_FUNCS) ;
- aucune émission de metadata_signal / metadata_pages_signal.

Une exception légitime va dans ALLOWED, avec sa justification.
"""
import ast
from pathlib import Path

QT_DIR = Path(__file__).resolve().parent.parent / "modules" / "qt"

# Attributs d'AppState qui décrivent le document (pas l'affichage)
DOCUMENT_STATE_ATTRS = frozenset({
    "images_data", "comic_metadata", "modified", "history", "history_index",
    "_page_attrs_by_entry_id", "_selection", "selected_indices", "current_file",
    "disk_signature", "doc_generation", "pending_when_idle", "running_operations",
    "real_to_visual", "visual_to_real", "needs_renumbering", "original_page_count",
    "zip_compression_state",
})

# Fonctions qui modifient l'état du document ou son historique
DOCUMENT_STATE_FUNCS = frozenset({
    "update_page_entries_in_xml_data", "sync_pages_in_xml_data",
    "update_page_count_in_xml_data", "write_comic_metadata_from_scraper",
    "save_state", "save_state_qt", "mark_history_saved", "reset_history",
    "run_when_idle", "begin_operation", "end_operation",
})

METADATA_SIGNALS = frozenset({"metadata_signal", "metadata_pages_signal"})

MUTATING_METHODS = frozenset({
    "append", "extend", "insert", "pop", "remove", "clear", "update",
    "setdefault", "sort", "reverse", "popitem", "add", "discard",
})

# (fichier, classe) -> raison
ALLOWED = {}


def _chain(node):
    """Noms d'une expression `a.b[c].d(...)` → ['a', 'b', 'd']."""
    names = []
    while True:
        if isinstance(node, ast.Attribute):
            names.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Subscript):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        else:
            if isinstance(node, ast.Name):
                names.append(node.id)
            return list(reversed(names))


def _touches_document(chain):
    """True si la chaîne atteint un attribut d'état du document d'un autre
    objet que le worker : `self.modified` est un attribut du worker,
    `self._state.modified` ou `state.modified` celui du document."""
    owned = chain[2:] if chain[:1] == ["self"] else chain[1:]
    return any(name in DOCUMENT_STATE_ATTRS for name in owned)


def _is_qthread(cls):
    return any(getattr(b, "id", getattr(b, "attr", None)) == "QThread" for b in cls.bases)


def _violations_in_node(node):
    found = []
    for n in ast.walk(node):
        targets = []
        if isinstance(n, ast.Assign):
            targets = n.targets
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
            targets = [n.target]
        elif isinstance(n, ast.Delete):
            targets = n.targets
        for target in targets:
            for t in (target.elts if isinstance(target, ast.Tuple) else [target]):
                chain = _chain(t)
                if _touches_document(chain):
                    found.append((n.lineno, "écrit " + ".".join(chain)))
        if isinstance(n, ast.Call):
            func = n.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in DOCUMENT_STATE_FUNCS:
                found.append((n.lineno, f"appelle {name}"))
            elif isinstance(func, ast.Attribute) and name in MUTATING_METHODS \
                    and _touches_document(_chain(func.value)):
                found.append((n.lineno, f"modifie {'.'.join(_chain(func.value))} ({name})"))
            elif isinstance(func, ast.Attribute) and name == "emit":
                chain = _chain(func.value)
                if chain and chain[-1] in METADATA_SIGNALS:
                    found.append((n.lineno, f"émet {chain[-1]}"))
    return found


def worker_violations(source, filename="<source>"):
    """[(fichier, classe, ligne, description)] pour chaque QThread de `source`.
    Suit les méthodes du worker et les fonctions du module appelées depuis
    run(), de proche en proche."""
    tree = ast.parse(source)
    module_funcs = {f.name: f for f in tree.body if isinstance(f, ast.FunctionDef)}
    out = []
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and _is_qthread(n)):
        methods = {m.name: m for m in cls.body if isinstance(m, ast.FunctionDef)}
        todo, seen = [("method", "run")], set()
        while todo:
            key = todo.pop()
            if key in seen:
                continue
            seen.add(key)
            kind, name = key
            node = methods.get(name) if kind == "method" else module_funcs.get(name)
            if node is None:
                continue
            for line, what in _violations_in_node(node):
                out.append((filename, cls.name, line, what))
            for n in ast.walk(node):
                if not isinstance(n, ast.Call):
                    continue
                func = n.func
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
                        and func.value.id == "self":
                    todo.append(("method", func.attr))
                elif isinstance(func, ast.Name) and func.id in module_funcs:
                    todo.append(("function", func.id))
    return out


def _scan():
    violations, workers = [], set()
    for path in sorted(QT_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        workers |= {(path.name, n.name) for n in ast.walk(tree)
                    if isinstance(n, ast.ClassDef) and _is_qthread(n)}
        violations += worker_violations(source, path.name)
    return violations, workers


VIOLATIONS, WORKERS = _scan()


def test_no_worker_modifies_document_state():
    bad = [v for v in VIOLATIONS if (v[0], v[1]) not in ALLOWED]
    assert not bad, (
        "Un worker modifie l'état du document depuis son thread : rendre le "
        "résultat au thread de l'interface (signal de fin) et faire la "
        "modification dans le slot (CLAUDE.md, « Workers asynchrones ») :\n"
        + "\n".join(f"  {f}:{line} {c}.run → {what}" for f, c, line, what in bad))


def test_allowed_list_is_not_stale():
    stale = [k for k in ALLOWED if k not in {(f, c) for f, c, _l, _w in VIOLATIONS}]
    assert not stale, f"Exceptions sans écriture correspondante : {stale}"


def test_scan_sees_the_workers():
    """Le scan doit trouver les workers existants : sinon le test principal
    passerait sans rien vérifier (classe de base renommée…)."""
    assert len(WORKERS) >= 25
    assert ("resize_dialog_qt.py", "_ResizeWorker") in WORKERS


def test_scan_detects_writes_and_ignores_the_worker_own_data():
    source = '''
from PySide6.QtCore import QThread

def _helper(state):
    state._page_attrs_by_entry_id[1] = {}

class _Worker(QThread):
    def run(self):
        entry = self._entries[0]
        entry["bytes"] = b"x"                  # entrée traitée : permis
        self.modified = True                   # attribut du worker : permis
        done = []
        done.append(entry)                     # liste locale : permis
        self._state.modified = True
        self._state.images_data.append(entry)
        update_page_entries_in_xml_data(self._state, [])
        metadata_pages_signal.emit(self._state)
        self._finish()
        _helper(self._state)

    def _finish(self):
        state = self._state
        state.comic_metadata["pages"] = []
'''
    found = sorted(what for _f, _c, _l, what in worker_violations(source))
    assert found == sorted([
        "écrit self._state.modified",
        "modifie self._state.images_data (append)",
        "appelle update_page_entries_in_xml_data",
        "émet metadata_pages_signal",
        "écrit state.comic_metadata",
        "écrit state._page_attrs_by_entry_id",
    ])
