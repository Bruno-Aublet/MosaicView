"""Fenêtres Qt détruites à leur fermeture (audit 8, P2-1 et P3-3).

Scan AST de modules/qt/ :
- toute sous-classe de QDialog pose WA_DeleteOnClose : fermée sans lui, une
  fenêtre non modale n'est que masquée, et reste enfant de son parent (panneau,
  fenêtre principale, Bibliothèque) jusqu'à la fin de la session, avec tout ce
  qu'elle référence ;
- une fenêtre qui redéfinit closeEvent sans appeler QDialog.closeEvent
  redéfinit aussi reject : Échap (reject) masquerait sinon la fenêtre sans
  passer par closeEvent — sans réponse à l'appelant, et détruite pendant
  qu'un lot la met encore à jour pour la fenêtre de progression ;
- toute fenêtre qui branche la traduction (language_signal) la débranche aussi
  à sa destruction (disconnect_on_destroy ou destroyed) : détruite avec sa
  fenêtre parente, elle ne passe ni par finished ni par sa méthode de
  fermeture ;
- language_signal.disconnect_on_destroy ne déconnecte qu'en silence.
"""
import ast
import types
import warnings
from pathlib import Path

import ast_loader

ROOT = Path(__file__).resolve().parent.parent
QT_DIR = ROOT / "modules" / "qt"

def _dialog_classes():
    for path in sorted(QT_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for node in ast_loader.module_tree(path).body:
            if isinstance(node, ast.ClassDef) and any("QDialog" in ast.unparse(b) for b in node.bases):
                yield path.name, node, ast.get_source_segment(source, node)


def _methods(node):
    return {m.name: m for m in node.body if isinstance(m, ast.FunctionDef)}


def test_scan_finds_the_dialogs():
    names = {node.name for _f, node, _s in _dialog_classes()}
    assert len(names) >= 80
    assert {"MergeDialog", "ScanDialog", "DpiDialog", "ImageViewer", "_HelpDialog"} <= names


def test_every_dialog_is_deleted_on_close():
    missing = [f"{f}:{node.name}" for f, node, seg in _dialog_classes()
               if "setAttribute(Qt.WA_DeleteOnClose)" not in seg]
    assert not missing, missing


# closeEvent redéfini sans reject, Échap ne pouvant pas en sauter le travail
ESC_HANDLED_ELSEWHERE = {
    # Échap est pris par son raccourci (_on_escape), jamais par reject
    ("image_viewer_qt.py", "ImageViewer"),
    # Échap → finished → _on_close : le même nettoyage que closeEvent
    ("error_log_dialog_qt.py", "_ErrorLogDialog"),
}


def test_escape_does_what_close_event_does():
    """Toute fenêtre qui redéfinit closeEvent redéfinit reject : Échap passe
    par QDialog.reject, qui ne déclenche pas closeEvent (réponse jamais
    envoyée, opération jamais terminée, nettoyage sauté)."""
    wrong = []
    for f, node, _seg in _dialog_classes():
        methods = _methods(node)
        if ("closeEvent" in methods and "reject" not in methods
                and (f, node.name) not in ESC_HANDLED_ELSEWHERE):
            wrong.append(f"{f}:{node.name}")
    assert not wrong, wrong


def test_reject_does_not_call_close_when_close_event_calls_super():
    """QDialog.closeEvent rappelle reject : un reject qui ferait close() y
    rebouclerait, et la fermeture serait refusée (fenêtre encore visible).
    Il fait alors son travail puis super().reject()."""
    wrong = []
    for f, node, _seg in _dialog_classes():
        methods = _methods(node)
        close_event, reject = methods.get("closeEvent"), methods.get("reject")
        if close_event is None or reject is None:
            continue
        if "super().closeEvent" in ast.unparse(close_event) and "self.close()" in ast.unparse(reject):
            wrong.append(f"{f}:{node.name}")
    assert not wrong, wrong


def test_escape_exceptions_are_still_needed():
    names = {(f, node.name): _methods(node) for f, node, _seg in _dialog_classes()}
    for key in ESC_HANDLED_ELSEWHERE:
        assert key in names, key
        assert "closeEvent" in names[key] and "reject" not in names[key], key


def test_language_is_disconnected_on_destroy():
    wrong = [f"{f}:{node.name}" for f, node, seg in _dialog_classes()
             if "language_signal.changed.connect(" in seg
             and "disconnect_on_destroy(" not in seg and "destroyed.connect(" not in seg]
    assert not wrong, wrong


def test_module_language_helpers_disconnect_on_destroy():
    helpers, wrong = 0, []
    for path in sorted(QT_DIR.glob("*.py")):
        for node in ast_loader.module_tree(path).body:
            if isinstance(node, ast.FunctionDef) and node.name == "_connect_lang":
                helpers += 1
                body = ast.unparse(node)
                if "disconnect_on_destroy(" not in body and "destroyed.connect(" not in body:
                    wrong.append(path.name)
    assert helpers >= 13
    assert not wrong, wrong


class _FakeChanged:
    def __init__(self):
        self.calls = []

    def disconnect(self, handler):
        self.calls.append(handler)
        if len(self.calls) > 1:
            warnings.warn("Failed to disconnect", RuntimeWarning)
            raise RuntimeError("not connected")


def test_quiet_disconnect_tolerates_an_already_disconnected_handler():
    source = (QT_DIR / "language_signal.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.FunctionDef) and n.name == "_disconnect_quietly")
    changed = _FakeChanged()
    ns = {"warnings": warnings, "language_signal": types.SimpleNamespace(changed=changed)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "language_signal.py", "exec"), ns)
    handler = object()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        ns["_disconnect_quietly"](handler)      # fermeture
        ns["_disconnect_quietly"](handler)      # destruction : ni exception ni avertissement
    assert changed.calls == [handler, handler]
