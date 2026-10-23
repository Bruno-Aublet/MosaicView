"""Réécriture du fichier ouvert alors qu'il a été modifié ailleurs (autre
panneau ouvert sur le même fichier, Bibliothèque, import de métadonnées en
lot, autre programme) : file_operations_qt._confirm_if_changed_on_disk.

Sans question, l'enregistrement remplacerait ces modifications. Vérifié :
- fichier inchangé depuis la lecture ou la dernière écriture du panneau
  (state.disk_signature) : écriture sans question ;
- fichier réécrit ailleurs : question, « Non » par défaut ; Oui écrit, Non
  abandonne ; une seule réponse prise en compte ; Oui après la fermeture du
  document abandonne (ses pages sont vidées) ;
- deux panneaux sur le même fichier : celui qui enregistre ne se pose pas la
  question, l'autre se la pose ensuite ;
- chaque écriture du document entier dans un fichier met à jour
  state.disk_signature (garde-fou AST), sinon l'enregistrement suivant
  poserait la question à tort, ou ne la poserait plus.

file_operations_qt.py importe PySide6 : la fonction est extraite par AST
(tests/ast_loader.py) ; la fenêtre de question est remplacée par une fausse.
"""
import ast
import os
import sys
import types

import pytest

from modules.qt.state import AppState
from modules.qt.utils import file_signature, replace_file_atomically

import ast_loader
from fakes import Signal as _Signal, tr as _tr


NS =ast_loader.load("file_operations_qt.py", {"_confirm_if_changed_on_disk"},
                     os=os, file_signature=file_signature, _=_tr, _wt=_tr)
confirm_if_changed_on_disk = NS["_confirm_if_changed_on_disk"]


class _Button:
    def __init__(self):
        self.default = None

    def setDefault(self, value):
        self.default = value

    def setFocus(self):
        pass


class FakeQuestion:
    shown = []

    def __init__(self, parent, title_fn, message_fn):
        self.message_fn = message_fn
        self._btn_yes, self._btn_no = _Button(), _Button()
        self.result_signal = _Signal()
        FakeQuestion.shown.append(self)

    def show(self):
        pass

    def raise_(self):
        pass

    def activateWindow(self):
        pass


@pytest.fixture(autouse=True)
def fake_dialogs(monkeypatch):
    FakeQuestion.shown = []
    module = types.ModuleType("modules.qt.dialogs_qt")
    module.ConfirmYNDialog = FakeQuestion
    module.position_dialog_on_parent = lambda dialog, parent: None
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", module)


def write(path, data):
    def _write(tmp):
        with open(tmp, "wb") as f:
            f.write(data)
    replace_file_atomically(str(path), _write)


def opened(path):
    """Panneau qui vient de lire `path`."""
    st = AppState()
    st.current_file = str(path)
    st.disk_signature = file_signature(str(path))
    return st


def check(st, path):
    calls = []
    confirm_if_changed_on_disk(None, st, str(path), lambda: calls.append("écrit"),
                               lambda: calls.append("abandonné"))
    return calls


# ---------------------------------------------------------------------------
# Décision
# ---------------------------------------------------------------------------

def test_unchanged_file_is_written_without_question(tmp_path):
    path = tmp_path / "Aaaa.cbz"
    write(path, b"version 1")
    assert check(opened(path), path) == ["écrit"]
    assert FakeQuestion.shown == []


def test_new_document_without_signature_is_written_without_question(tmp_path):
    path = tmp_path / "Aaaa.cbz"
    write(path, b"version 1")
    st = AppState()
    st.current_file = str(path)
    assert check(st, path) == ["écrit"]
    assert FakeQuestion.shown == []


def test_file_rewritten_elsewhere_asks_first_with_no_as_default(tmp_path):
    path = tmp_path / "Aaaa.cbz"
    write(path, b"version 1")
    st = opened(path)
    write(path, b"version 2, plus longue")
    assert check(st, path) == []                   # rien tant que la question est ouverte
    [question] = FakeQuestion.shown
    assert question._btn_no.default is True
    assert question._btn_yes.default is False
    assert "Aaaa.cbz" in question.message_fn()


def test_yes_writes_and_no_abandons(tmp_path):
    path = tmp_path / "Aaaa.cbz"
    write(path, b"version 1")
    st = opened(path)
    write(path, b"version 2, plus longue")
    calls = check(st, path)
    FakeQuestion.shown[0].result_signal.emit(True)
    assert calls == ["écrit"]
    calls = check(st, path)
    FakeQuestion.shown[1].result_signal.emit(False)
    assert calls == ["abandonné"]


def test_only_the_first_answer_counts(tmp_path):
    """Fenêtre cachée mais pas encore détruite, fermée ensuite avec le
    document : elle réémet False."""
    path = tmp_path / "Aaaa.cbz"
    write(path, b"version 1")
    st = opened(path)
    write(path, b"version 2, plus longue")
    calls = check(st, path)
    FakeQuestion.shown[0].result_signal.emit(True)
    FakeQuestion.shown[0].result_signal.emit(False)
    assert calls == ["écrit"]


def test_yes_after_the_document_was_closed_abandons(tmp_path):
    path = tmp_path / "Aaaa.cbz"
    write(path, b"version 1")
    st = opened(path)
    write(path, b"version 2, plus longue")
    calls = check(st, path)
    st.doc_generation += 1                          # force_close_file
    FakeQuestion.shown[0].result_signal.emit(True)
    assert calls == ["abandonné"]


# ---------------------------------------------------------------------------
# Deux panneaux sur le même fichier
# ---------------------------------------------------------------------------

def test_two_panels_on_the_same_file(tmp_path):
    path = tmp_path / "Aaaa.cbz"
    write(path, b"version 1")
    first, second = opened(path), opened(path)

    assert check(first, path) == ["écrit"]
    write(path, b"version 2 du premier panneau")
    first.disk_signature = file_signature(str(path))   # comme après tout enregistrement

    assert check(first, path) == ["écrit"]            # son propre fichier : pas de question
    assert FakeQuestion.shown == []
    assert check(second, path) == []                  # l'autre panneau : question
    assert len(FakeQuestion.shown) == 1


# ---------------------------------------------------------------------------
# Garde-fou : signature mise à jour après chaque écriture du document
# ---------------------------------------------------------------------------

WRITERS = ("_write_document_with_progress", "_write_cbz_with_overlay")


def _writer_calls_without_entries():
    """(fonction englobante, appel) des écritures du document entier
    (sans argument `entries`, réservé à Enregistrer la sélection)."""
    tree = ast.parse((ast_loader.QT_DIR / "file_operations_qt.py").read_text(encoding="utf-8"))
    found = []

    def walk(node, func):
        for child in ast.iter_child_nodes(node):
            inner = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            if (isinstance(child, ast.Call) and getattr(child.func, "id", None) in WRITERS
                    and func is not None and func.name not in WRITERS):
                if len(child.args) < 5 and not any(k.arg == "entries" for k in child.keywords):
                    found.append((func, child))
            walk(child, inner)

    walk(tree, None)
    return found


def _sets_signature_after(func, call):
    return any(isinstance(n, ast.Assign) and n.lineno > call.lineno
               and any(ast.unparse(t) == "state.disk_signature" for t in n.targets)
               for n in ast.walk(func))


def test_every_document_write_updates_the_disk_signature():
    calls = _writer_calls_without_entries()
    assert len(calls) >= 4, "le scan ne trouve plus les écritures du document"
    missing = [f"{func.name} (appel ligne {call.lineno})" for func, call in calls
               if not _sets_signature_after(func, call)]
    assert not missing, ("Écriture du document sans mise à jour de state.disk_signature "
                         "après elle :\n  " + "\n  ".join(missing))
