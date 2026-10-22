"""Une opération en arrière-plan se termine toujours (Audit 7, P4-5).

Pendant une opération (AppState.begin_operation), toute commande qui modifie
ou enregistre le document est sans effet. Si la fin n'appelle jamais
end_operation, le document reste impossible à modifier ou à enregistrer
jusqu'à sa fermeture. Deux causes, gardées ici :

1. un worker dont run() lève une exception imprévue n'émet aucun signal de
   fin : chaque QThread d'un module qui appelle begin_operation enveloppe son
   travail (`run` → `self._run()`) dans un try qui émet un signal puis relance ;
2. une exception dans la fin, sur le thread Qt, saute end_operation : les fins
   de la rotation, du redressement et de la conversion le font dans un finally.

Scan AST de modules/qt/, sans Qt ; les run() sont aussi exécutés seuls.
"""
import ast
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
QT = ROOT / "modules" / "qt"


def _tree(name):
    return ast.parse((QT / name).read_text(encoding="utf-8"))


def _modules_with_operations():
    return sorted(p.name for p in QT.glob("*.py")
                  if ".begin_operation(" in p.read_text(encoding="utf-8"))


def _qthread_classes(tree):
    return [n for n in tree.body if isinstance(n, ast.ClassDef)
            and any(getattr(b, "id", None) == "QThread" for b in n.bases)]


def _method(cls, name):
    return next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name), None)


def _functions_named(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]


def test_operation_modules_are_found():
    assert {"image_transforms_qt.py", "deskew_qt.py", "resize_dialog_qt.py",
            "conversion_dialogs_qt.py"} <= set(_modules_with_operations())


def test_every_begin_operation_has_a_matching_end_operation():
    bad = []
    for name in _modules_with_operations():
        calls = {"begin_operation": set(), "end_operation": set()}
        for node in ast.walk(_tree(name)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in calls and node.args):
                calls[node.func.attr].add(ast.unparse(node.args[0]))
        bad += [f"{name}: begin_operation({t}) sans end_operation({t})"
                for t in calls["begin_operation"] - calls["end_operation"]]
    assert not bad, "\n".join(bad)


def test_worker_run_always_emits_an_end_signal():
    bad = []
    for name in _modules_with_operations():
        for cls in _qthread_classes(_tree(name)):
            run = _method(cls, "run")
            ok = (run is not None and len(run.body) >= 1 and isinstance(run.body[-1], ast.Try)
                  and any(ast.unparse(h.type) == "Exception"
                          and any(isinstance(s, ast.Raise) and s.exc is None for s in h.body)
                          and ".emit(" in ast.unparse(h)
                          for h in run.body[-1].handlers))
            if not ok:
                bad.append(f"{name}: {cls.name}.run")
    assert not bad, ("run() sans signal de fin sur erreur imprévue "
                     "(try: self._run() / except Exception: <signal>.emit(); raise) :\n"
                     + "\n".join(bad))


@pytest.mark.parametrize("module, cls_name", [
    ("image_transforms_qt.py", "_TransformWorker"),
    ("deskew_qt.py", "_DeskewWorker"),
    ("resize_dialog_qt.py", "_ResizeWorker"),
    ("conversion_dialogs_qt.py", "_ConversionWorker"),
])
def test_unexpected_error_in_worker_emits_cancelled_then_reraises(module, cls_name):
    cls = next(c for c in _qthread_classes(_tree(module)) if c.name == cls_name)
    ns = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[
        ast.ClassDef(name="W", bases=[], keywords=[], decorator_list=[], body=[_method(cls, "run")])],
        type_ignores=[])), module, "exec"), ns)
    worker = ns["W"]()
    emitted = []
    worker.cancelled = types.SimpleNamespace(emit=lambda *a: emitted.append(a))

    def broken():
        raise RuntimeError("page introuvable")
    worker._run = broken
    with pytest.raises(RuntimeError):
        worker.run()
    assert emitted == [()]


def _finally_calls(func, callee):
    return any(isinstance(n, ast.Try) and callee in "".join(ast.unparse(s) for s in n.finalbody)
               for n in ast.walk(func))


@pytest.mark.parametrize("module, func, callee", [
    ("image_transforms_qt.py", "on_finished", "_release_worker()"),
    ("image_transforms_qt.py", "_finish_cancel", "_release_worker()"),
    ("deskew_qt.py", "on_finished", "_release_worker()"),
    ("deskew_qt.py", "_finish_cancel", "_release_worker()"),
    ("conversion_dialogs_qt.py", "_after_complete", "end_operation(worker)"),
])
def test_end_of_operation_survives_an_exception(module, func, callee):
    funcs = _functions_named(_tree(module), func)
    assert funcs, f"{module}: {func} introuvable"
    assert all(_finally_calls(f, callee) for f in funcs), f"{module}: {func} sans finally → {callee}"


def test_conversion_question_failure_lets_cleanup_end_the_operation():
    tree = _tree("conversion_dialogs_qt.py")
    on_finished = next(f for f in _functions_named(tree, "on_finished")
                       if "awaiting_choice[0] = True" in ast.unparse(f))
    handlers = [h for n in ast.walk(on_finished) if isinstance(n, ast.Try) for h in n.handlers]
    assert any("awaiting_choice[0] = False" in ast.unparse(h) for h in handlers)


def test_release_worker_runs_only_once():
    for module in ("image_transforms_qt.py", "deskew_qt.py"):
        for func in _functions_named(_tree(module), "_release_worker"):
            assert "if released[0]:" in ast.unparse(func), module
