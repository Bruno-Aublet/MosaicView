"""Fenêtres Qt détruites à leur fermeture (audit 8, P2-1 et P3-3).

Scan AST de modules/qt/ :
- toute sous-classe de QDialog pose WA_DeleteOnClose, elle-même ou par
  AppWindowMixin._setup_window (app_window_qt), sans delete_on_close=False :
  fermée sans lui, une fenêtre non modale n'est que masquée, et reste enfant
  de son parent (panneau, fenêtre principale, Bibliothèque) jusqu'à la fin de
  la session, avec tout ce qu'elle référence ;
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
    assert len(names) >= 78
    assert {"MergeDialog", "ScanDialog", "DpiDialog", "ImageViewer", "_HelpDialog"} <= names


def _deleted_by_app_window(node):
    """Vrai si la fenêtre hérite d'AppWindowMixin (ou de QuestionWindowMixin,
    bâti sur lui) et appelle _setup_window sans delete_on_close=False."""
    if not {"AppWindowMixin", "QuestionWindowMixin"} & {ast.unparse(b) for b in node.bases}:
        return False
    calls = [c for c in ast.walk(node) if isinstance(c, ast.Call)
             and isinstance(c.func, ast.Attribute) and c.func.attr == "_setup_window"]
    return bool(calls) and all(
        not (k.arg == "delete_on_close" and not (isinstance(k.value, ast.Constant) and k.value.value))
        for c in calls for k in c.keywords)


def _deletes_on_close(node, seg):
    return "setAttribute(Qt.WA_DeleteOnClose)" in seg or _deleted_by_app_window(node)


def test_every_dialog_is_deleted_on_close():
    missing = [f"{f}:{node.name}" for f, node, seg in _dialog_classes()
               if not _deletes_on_close(node, seg)]
    assert not missing, missing


def test_app_window_mixin_deletes_on_close():
    """_setup_window pose WA_DeleteOnClose, sauf delete_on_close=False."""
    tree = ast_loader.module_tree(QT_DIR / "app_window_qt.py")
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AppWindowMixin")
    setup = _methods(cls)["_setup_window"]
    assert ast.unparse(setup.args.defaults[0]) == "True"
    branch = next(n for n in ast.walk(setup) if isinstance(n, ast.If)
                  and ast.unparse(n.test) == "delete_on_close")
    assert "self.setAttribute(Qt.WA_DeleteOnClose)" in ast.unparse(branch.body)


def test_scan_sees_app_window_deletion():
    tree = ast.parse(
        "class Kept(AppWindowMixin, QDialog):\n"
        "    def __init__(self, p):\n"
        "        self._setup_window(delete_on_close=False)\n"
        "class Deleted(AppWindowMixin, QDialog):\n"
        "    def __init__(self, p):\n"
        "        self._setup_window(center_on=p)\n"
        "class NoSetup(AppWindowMixin, QDialog):\n"
        "    pass\n"
        "class Plain(QDialog):\n"
        "    def __init__(self, p):\n"
        "        self._setup_window()\n"
        "class Question(QuestionWindowMixin, QDialog):\n"
        "    def __init__(self, p):\n"
        "        self._setup_window(close_answer=False)\n")
    found = {n.name: _deletes_on_close(n, ast.unparse(n)) for n in tree.body}
    assert found == {"Kept": False, "Deleted": True, "NoSetup": False, "Plain": False,
                     "Question": True}


# closeEvent redéfini sans reject, Échap ne pouvant pas en sauter le travail
ESC_HANDLED_ELSEWHERE = {
    # Échap est pris par son raccourci (_on_escape) ; son keyPressEvent
    # l'absorbe quand le raccourci est désactivé (lecture de macro)
    ("image_viewer_qt.py", "ImageViewer"),
}


def _escape_closes(node):
    """Vrai si Échap passe par close() : fenêtre à AppWindowMixin (ou
    QuestionWindowMixin) dont le keyPressEvent n'est pas redéfini."""
    bases = {ast.unparse(b) for b in node.bases}
    return (bool({"AppWindowMixin", "QuestionWindowMixin"} & bases)
            and "keyPressEvent" not in _methods(node))


def test_escape_does_what_close_event_does():
    """Toute fenêtre qui redéfinit closeEvent redéfinit reject : Échap passe
    par QDialog.reject, qui ne déclenche pas closeEvent (réponse jamais
    envoyée, opération jamais terminée, nettoyage sauté). Sauf une fenêtre à
    AppWindowMixin, où Échap appelle close(), comme la croix."""
    wrong = []
    for f, node, _seg in _dialog_classes():
        methods = _methods(node)
        if ("closeEvent" in methods and "reject" not in methods
                and not _escape_closes(node)
                and (f, node.name) not in ESC_HANDLED_ELSEWHERE):
            wrong.append(f"{f}:{node.name}")
    assert not wrong, wrong


def test_app_window_escape_calls_close():
    tree = ast_loader.module_tree(QT_DIR / "app_window_qt.py")
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AppWindowMixin")
    assert "self.close()" in ast.unparse(_methods(cls)["keyPressEvent"])
    tree = ast.parse(
        "class Closes(AppWindowMixin, QDialog):\n"
        "    def closeEvent(self, e): pass\n"
        "class OwnKeys(AppWindowMixin, QDialog):\n"
        "    def closeEvent(self, e): pass\n"
        "    def keyPressEvent(self, e): pass\n"
        "class Plain(QDialog):\n"
        "    def closeEvent(self, e): pass\n")
    assert [n.name for n in tree.body if _escape_closes(n)] == ["Closes"]


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


def test_app_window_mixin_disconnects_language_on_destroy():
    """Les fenêtres à AppWindowMixin branchent la traduction par
    _connect_language : c'est lui qui la débranche à la destruction."""
    tree = ast_loader.module_tree(QT_DIR / "app_window_qt.py")
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AppWindowMixin")
    body = ast.unparse(_methods(cls)["_connect_language"])
    assert "language_signal.changed.connect(self._lang_handler)" in body
    assert "disconnect_on_destroy(self, self._lang_handler)" in body
    assert "self.finished.connect(self._disconnect_language)" in body


def test_module_language_helpers_disconnect_on_destroy():
    """Une fonction _connect_lang de module (branchement de la traduction
    recopié avant AppWindowMixin) débranche aussi à la destruction."""
    wrong = []
    for path in sorted(QT_DIR.glob("*.py")):
        for node in ast_loader.module_tree(path).body:
            if isinstance(node, ast.FunctionDef) and node.name == "_connect_lang":
                body = ast.unparse(node)
                if "disconnect_on_destroy(" not in body and "destroyed.connect(" not in body:
                    wrong.append(path.name)
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
    changed = _FakeChanged()
    ns = ast_loader.load("language_signal.py", {"_disconnect_quietly"}, warnings=warnings,
                         language_signal=types.SimpleNamespace(changed=changed))
    handler = object()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        ns["_disconnect_quietly"](handler)      # fermeture
        ns["_disconnect_quietly"](handler)      # destruction : ni exception ni avertissement
    assert changed.calls == [handler, handler]
