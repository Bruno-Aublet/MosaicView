"""Garde-fous : une opération en arrière-plan s'arrête à la fermeture de son
fichier et de l'application, et son résultat tardif est abandonné.

1. Chaque fonction qui démarre une opération sur un document
   (`state.begin_operation`) inscrit son annulation pour la fermeture du
   fichier (`register_cancel_on_close`, retirée par `unregister_cancel_on_close`
   à la fin) et compare `doc_generation` : filet de sécurité si l'annulation
   arrive trop tard (règle CLAUDE.md, skill file-close). Le comportement
   lui-même est testé sur la rotation par tests/test_operation_lifecycle.py.
2. Chaque QThread de modules/qt/ expose l'une des conventions d'annulation
   que `utils.stop_running_qthreads` sait déclencher à la fermeture de
   l'application ; sinon il figure dans NON_INTERRUPTIBLE, avec la raison
   (appel réseau, WIA, process de PDF…) : encore actif après le délai, il
   fait terminer le process par os._exit à la fin de main().

Scan AST de modules/qt/, sans Qt.
"""
import ast
from pathlib import Path

import ast_loader

QT = Path(__file__).resolve().parent.parent / "modules" / "qt"

# (fichier, fonction) -> raison
NOT_CANCELLABLE_OPERATIONS = {
    ("file_operations_qt.py", "_write_document_with_progress"):
        "écriture d'un enregistrement, sur le thread de l'interface : une fermeture pendant "
        "l'écriture la fait abandonner (_DocumentChangedDuringSave, doc_generation lu par "
        "_write_document_unlocked)",
}

CANCEL_ATTRIBUTES = ("_cancelled", "_stop", "cancelled", "_cancel_flag")
CANCEL_METHODS = ("set_ext_result",)

_NETWORK = "appel réseau unique, sans point d'arrêt (délai de requests)"
_WIA = "numérisation WIA (COM) : un appel bloquant du pilote, sans point d'arrêt"
# (fichier, classe) -> raison
NON_INTERRUPTIBLE = {
    ("batch_metadata_dialog_qt.py", "_LoadWorker"):
        "lecture d'un seul fichier du lot ; le lot est arrêté entre deux fichiers",
    ("comicvine_dialog_qt.py", "_SearchWorker"): _NETWORK,
    ("comicvine_dialog_qt.py", "_IssuesWorker"): _NETWORK,
    ("comicvine_dialog_qt.py", "_FirstIssueWorker"): _NETWORK,
    ("comicvine_dialog_qt.py", "_ImageWorker"): _NETWORK,
    ("comicvine_dialog_qt.py", "_MetadataWorker"): _NETWORK,
    ("comicvine_update_check_qt.py", "_CheckUpdateWorker"): _NETWORK,
    ("comicvine_url_dialog_qt.py", "_UrlFetchWorker"): _NETWORK,
    ("web_import_qt.py", "_ResolveWorker"): _NETWORK,
    ("pdf_unlock_qt.py", "_UnlockWorker"):
        "attend la réponse d'un process PdfProbe, que shutdown_pdf_probes libère à la fermeture",
    ("printing_qt.py", "_PrintWorker"): "préparation du fichier à imprimer, courte",
    ("scan_dialog_qt.py", "_ScanListWorker"): _WIA,
    ("scan_dialog_qt.py", "_CapsWorker"): _WIA,
    ("scan_dialog_qt.py", "_EsclFallbackWorker"): _NETWORK,
    ("scan_dialog_qt.py", "_ScanWorker"): _WIA,
}


def _trees():
    return {p.name: ast_loader.module_tree(p) for p in sorted(QT.glob("*.py"))}


TREES = _trees()


def _operation_starters():
    """[(fichier, fonction de premier niveau ou méthode, nœud)] qui appellent
    .begin_operation(…)."""
    found = []
    for name, tree in TREES.items():
        scopes = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        scopes += [m for c in tree.body if isinstance(c, ast.ClassDef)
                   for m in c.body if isinstance(m, ast.FunctionDef)]
        for func in scopes:
            if any(isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "begin_operation"
                   for n in ast.walk(func)):
                found.append((name, func.name, func))
    return found


def _calls(func, name):
    return any(isinstance(n, ast.Call) and (getattr(n.func, "id", None) == name
                                            or getattr(n.func, "attr", None) == name)
               for n in ast.walk(func))


def _reads_generation(func):
    """state.doc_generation, ou getattr(state, 'doc_generation', …)."""
    return any((isinstance(n, ast.Attribute) and n.attr == "doc_generation")
               or (isinstance(n, ast.Call) and getattr(n.func, "id", None) == "getattr"
                   and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant)
                   and n.args[1].value == "doc_generation")
               for n in ast.walk(func))


def test_every_operation_stops_at_close_and_drops_late_results():
    starters = _operation_starters()
    assert len(starters) >= 6, "le scan ne voit plus les opérations"
    bad = []
    for name, func_name, func in starters:
        if (name, func_name) in NOT_CANCELLABLE_OPERATIONS:
            continue
        missing = [what for what, ok in (
            ("register_cancel_on_close", _calls(func, "register_cancel_on_close")),
            ("unregister_cancel_on_close", _calls(func, "unregister_cancel_on_close")),
            ("comparaison de doc_generation", _reads_generation(func))) if not ok]
        if missing:
            bad.append(f"  {name} ({func_name}) : {', '.join(missing)}")
    assert not bad, ("Opération sur un document sans arrêt à la fermeture du fichier "
                     "ou sans abandon d'un résultat tardif :\n" + "\n".join(bad))


def test_not_cancellable_operations_are_still_there():
    used = {(name, func_name) for name, func_name, _func in _operation_starters()}
    stale = [k for k in NOT_CANCELLABLE_OPERATIONS if k not in used]
    assert not stale, f"Exceptions sans opération correspondante : {stale}"


def _qthreads():
    for name, tree in TREES.items():
        for cls in ast.walk(tree):
            if isinstance(cls, ast.ClassDef) and any(
                    getattr(b, "id", None) == "QThread" or getattr(b, "attr", None) == "QThread"
                    for b in cls.bases):
                yield name, cls


def _cancellable(cls):
    stored = {n.attr for n in ast.walk(cls) if isinstance(n, ast.Attribute)
              and isinstance(n.ctx, ast.Store) and getattr(n.value, "id", None) == "self"}
    methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    return bool(stored & set(CANCEL_ATTRIBUTES)) or bool(methods & set(CANCEL_METHODS))


def test_every_qthread_can_be_stopped_when_the_application_closes():
    threads = list(_qthreads())
    assert len(threads) >= 25, "le scan ne voit plus les QThread"
    bad = [f"  {name} ({cls.name})" for name, cls in threads
           if not _cancellable(cls) and (name, cls.name) not in NON_INTERRUPTIBLE]
    assert not bad, (
        "QThread sans convention d'annulation reconnue par utils.stop_running_qthreads "
        f"({', '.join(CANCEL_ATTRIBUTES + CANCEL_METHODS)}) : en ajouter une, ou le déclarer "
        "dans NON_INTERRUPTIBLE avec la raison :\n" + "\n".join(bad))


def test_non_interruptible_list_is_not_stale():
    threads = {(name, cls.name): cls for name, cls in _qthreads()}
    stale = [k for k in NON_INTERRUPTIBLE if k not in threads]
    became = [k for k in NON_INTERRUPTIBLE if k in threads and _cancellable(threads[k])]
    assert not stale, f"QThread disparus : {stale}"
    assert not became, f"QThread devenus interruptibles, à retirer de la liste : {became}"


def test_the_conventions_are_those_stop_running_qthreads_triggers():
    func = next(n for n in TREES["utils.py"].body
                if isinstance(n, ast.FunctionDef) and n.name == "stop_running_qthreads")
    source = ast.unparse(func)
    for convention in CANCEL_ATTRIBUTES + CANCEL_METHODS:
        assert f"'{convention}'" in source or f'"{convention}"' in source \
            or f".{convention}" in source, convention
