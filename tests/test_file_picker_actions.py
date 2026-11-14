"""Actions de la fenêtre de choix de fichier (file_picker_qt.FilePickerWindow) :
renommer (rename_item), mettre à la Corbeille (_delete_selected) et valider
un enregistrement sur un fichier existant (_accept_save, question
« Remplacer ? »).

Méthodes extraites par tests/ast_loader.py, sans Qt : le modèle de fichiers,
la liste et la fenêtre de confirmation sont remplacés. Les règles vérifiées :
- un nom que Windows n'écrirait pas tel quel est refusé avant tout
  renommage ; un fichier utilisé n'est ni renommé ni supprimé, y compris
  s'il l'est devenu pendant la saisie ou la question ;
- la Corbeille n'est utilisée qu'après confirmation, sur les éléments qui
  existent encore, et un échec s'affiche sans continuer en silence ;
- un fichier existant n'est remplacé qu'après « Oui », et une nouvelle
  validation pendant la question rend l'ancienne réponse caduque.
"""
import os
import sys
import types

import pytest

import ast_loader
from modules.qt.file_picker import resolve, with_extension
from modules.qt.utils import windows_safe_component


def _tr(key, **params):
    return (key, params)


_valid_name = ast_loader.load("file_picker_qt.py", {"_valid_name"},
                              windows_safe_component=windows_safe_component)["_valid_name"]
_norm = ast_loader.load("file_picker_qt.py", {"_norm"}, os=os)["_norm"]


class Used:
    """Chemins que _refuse_if_in_use refuse, remplis par chaque test."""
    paths = []


class Trash:
    sent = []
    failing = set()


def _send_to_trash(path):
    if path in Trash.failing:
        raise OSError("refus")
    Trash.sent.append(path)


Methods = ast_loader.load_methods(
    "file_picker_qt.py", "FilePickerWindow",
    {"rename_item", "_delete_selected", "_accept_save"},
    os=os, _=_tr, Qt=types.SimpleNamespace(EditRole=2),
    _valid_name=_valid_name, _norm=_norm, resolve=resolve, with_extension=with_extension,
    send_to_trash=_send_to_trash)


class Questions:
    """Fenêtres de confirmation ouvertes : (titre, message, réponse à donner)."""
    asked = []


class _Confirm:
    def __init__(self, parent, title_key, message, warning=None):
        self.title_key, self.message, self.warning = title_key, message, warning

    def ask_async(self, on_result):
        Questions.asked.append((self, on_result))


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    Used.paths, Trash.sent, Trash.failing, Questions.asked = [], [], set(), []
    dialogs = types.ModuleType("modules.qt.dialogs_qt")
    dialogs.ConfirmYNDialog = _Confirm
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", dialogs)
    recycle = types.ModuleType("modules.qt.recycle_bin")
    recycle.trash_problem = lambda path: None
    recycle.trash_problem_count = lambda paths: 0
    recycle.warning_text = lambda problem: ""
    monkeypatch.setitem(sys.modules, "modules.qt.recycle_bin", recycle)


class Picker(Methods):
    def __init__(self, folder, selected=(), patterns=("*.cbz",)):
        self._current = str(folder)
        self._selected = [str(p) for p in selected]
        self._renaming = ""
        self._answered = False
        self._overwrite_token = None
        self._filters = [types.SimpleNamespace(patterns=patterns)]
        self._filter_combo = types.SimpleNamespace(currentIndex=lambda: 0)
        self._name_edit = types.SimpleNamespace(clear=lambda: None)
        self.status = None
        self.answers = []
        self.visited = []

    # Interface de la fenêtre
    def _set_status(self, text_fn):
        self.status = text_fn() if text_fn else None

    def _refuse_if_in_use(self, paths):
        used = [p for p in paths if p in Used.paths]
        if used:
            self.status = ("in_use", used[0])
        return bool(used)

    def _selected_paths(self):
        return list(self._selected)

    def _answer(self, result):
        self.answers.append(result)

    def _go_to(self, path):
        self.visited.append(path)


# --- Renommer ------------------------------------------------------------------------

class _Index:
    def __init__(self, path):
        self.path = path


class _Model:
    """Modèle de la liste (proxy) et modèle de fichiers à la fois : le nom
    est renommé sur le disque, comme QFileSystemModel.setData."""
    def __init__(self, fails=False):
        self.fails = fails
        self.renamed = []

    def data(self, index, role):
        return os.path.basename(index.path)

    def setData(self, index, name, role):
        if self.fails:
            return False
        new = os.path.join(os.path.dirname(index.path), name)
        os.rename(index.path, new)
        self.renamed.append(new)
        return True

    def filePath(self, index):
        return index.path

    def mapToSource(self, index):
        return index


def _renaming_picker(tmp_path, name="Aaaa.cbz", fails=False):
    path = tmp_path / name
    path.write_bytes(b"x")
    picker = Picker(tmp_path)
    picker._model = picker._proxy = model = _Model(fails)
    picker._renaming = _norm(str(path))
    return picker, model, _Index(str(path))


def test_rename_writes_the_new_name_and_follows_it(tmp_path):
    picker, model, index = _renaming_picker(tmp_path)
    picker.rename_item("  Bbbb.cbz ", model, index)
    assert (tmp_path / "Bbbb.cbz").exists() and not (tmp_path / "Aaaa.cbz").exists()
    assert picker._renaming == _norm(str(tmp_path / "Bbbb.cbz"))
    assert picker.status is None


def test_rename_to_the_same_or_an_empty_name_does_nothing(tmp_path):
    picker, model, index = _renaming_picker(tmp_path)
    for text in ("Aaaa.cbz", "   ", ""):
        picker.rename_item(text, model, index)
    assert model.renamed == [] and picker.status is None


@pytest.mark.parametrize("name", ["Bb:bb.cbz", "Bbbb.", "Bbbb. ", "CON", "Bb<bb>.cbz",
                                  "Aa\\Bbbb.cbz", "Aa/Bbbb.cbz"])
def test_rename_refuses_a_name_windows_would_change(tmp_path, name):
    """Flux caché (a:b), point final retiré, nom réservé, caractère interdit,
    sous-dossier : refusé avant d'écrire (les espaces autour du nom saisi
    sont retirés d'abord)."""
    picker, model, index = _renaming_picker(tmp_path)
    picker.rename_item(name, model, index)
    assert model.renamed == []
    assert picker.status == ("dialogs.file_picker.invalid_name", {"name": name.strip()})
    assert (tmp_path / "Aaaa.cbz").exists()


def test_rename_of_a_file_used_meanwhile_is_refused(tmp_path):
    """Un lot ou un chargement a pu commencer pendant la saisie."""
    picker, model, index = _renaming_picker(tmp_path)
    Used.paths = [index.path]
    picker.rename_item("Bbbb.cbz", model, index)
    assert model.renamed == [] and picker.status == ("in_use", index.path)


def test_rename_failure_is_shown(tmp_path):
    picker, model, index = _renaming_picker(tmp_path, fails=True)
    picker.rename_item("Bbbb.cbz", model, index)
    assert picker.status == ("dialogs.file_picker.rename_failed", {"name": "Bbbb.cbz"})
    assert picker._renaming == _norm(index.path)


# --- Supprimer (Corbeille) -----------------------------------------------------------

def _files(tmp_path, *names):
    paths = []
    for name in names:
        (tmp_path / name).write_bytes(b"x")
        paths.append(str(tmp_path / name))
    return paths


def _reply(answer):
    dialog, on_result = Questions.asked[-1]
    on_result(answer)
    return dialog


def test_nothing_goes_to_the_trash_before_confirmation(tmp_path):
    paths = _files(tmp_path, "Aaaa.cbz")
    picker = Picker(tmp_path, paths)
    picker._delete_selected()
    assert Trash.sent == []
    dialog = _reply(True)
    assert Trash.sent == paths
    assert dialog.title_key == "dialogs.file_picker.delete_title"
    assert dialog.message() == ("dialogs.file_picker.delete_one", {"name": "Aaaa.cbz"})


def test_no_answer_keeps_the_files(tmp_path):
    paths = _files(tmp_path, "Aaaa.cbz", "Bbbb.cbz")
    picker = Picker(tmp_path, paths)
    picker._delete_selected()
    dialog = _reply(False)
    assert Trash.sent == []
    assert dialog.message() == ("dialogs.file_picker.delete_many", {"count": 2})


def test_used_file_is_refused_without_question(tmp_path):
    paths = _files(tmp_path, "Aaaa.cbz")
    Used.paths = list(paths)
    picker = Picker(tmp_path, paths)
    picker._delete_selected()
    assert Questions.asked == [] and Trash.sent == []


def test_file_used_during_the_question_is_kept(tmp_path):
    paths = _files(tmp_path, "Aaaa.cbz", "Bbbb.cbz")
    picker = Picker(tmp_path, paths)
    picker._delete_selected()
    Used.paths = [paths[1]]
    _reply(True)
    assert Trash.sent == []
    assert picker.status == ("in_use", paths[1])


def test_vanished_file_is_skipped(tmp_path):
    paths = _files(tmp_path, "Aaaa.cbz", "Bbbb.cbz")
    picker = Picker(tmp_path, paths)
    picker._delete_selected()
    os.remove(paths[0])
    _reply(True)
    assert Trash.sent == [paths[1]]


def test_answer_after_the_window_answered_does_nothing(tmp_path):
    paths = _files(tmp_path, "Aaaa.cbz")
    picker = Picker(tmp_path, paths)
    picker._delete_selected()
    picker._answered = True
    _reply(True)
    assert Trash.sent == []


def test_trash_failure_is_shown_and_stops(tmp_path):
    paths = _files(tmp_path, "Aaaa.cbz", "Bbbb.cbz", "Cccc.cbz")
    Trash.failing = {paths[1]}
    picker = Picker(tmp_path, paths)
    picker._delete_selected()
    _reply(True)
    assert Trash.sent == [paths[0]]
    assert picker.status == ("dialogs.file_picker.delete_failed",
                             {"name": "Bbbb.cbz", "error": "refus"})


# --- Enregistrer : « Remplacer ? » ----------------------------------------------------

def test_new_file_is_answered_with_its_extension(tmp_path):
    picker = Picker(tmp_path)
    picker._accept_save("Aaaa")
    assert picker.answers == [str(tmp_path / "Aaaa.cbz")]
    assert Questions.asked == []


def test_existing_file_is_replaced_only_after_yes(tmp_path):
    _files(tmp_path, "Aaaa.cbz")
    picker = Picker(tmp_path)
    picker._accept_save("Aaaa")
    assert picker.answers == []
    dialog, on_result = Questions.asked[-1]
    assert dialog.title_key == "dialogs.file_picker.replace_title"
    assert dialog.message() == ("dialogs.file_picker.replace_message", {"name": "Aaaa.cbz"})
    on_result(False)
    assert picker.answers == []
    picker._accept_save("Aaaa.cbz")
    _reply(True)
    assert picker.answers == [str(tmp_path / "Aaaa.cbz")]


def test_new_validation_makes_the_previous_question_stale(tmp_path):
    _files(tmp_path, "Aaaa.cbz", "Bbbb.cbz")
    picker = Picker(tmp_path)
    picker._accept_save("Aaaa.cbz")
    first = Questions.asked[-1][1]
    picker._accept_save("Bbbb.cbz")
    first(True)
    assert picker.answers == []
    _reply(True)
    assert picker.answers == [str(tmp_path / "Bbbb.cbz")]


def test_yes_after_the_window_answered_does_nothing(tmp_path):
    _files(tmp_path, "Aaaa.cbz")
    picker = Picker(tmp_path)
    picker._accept_save("Aaaa.cbz")
    picker._answered = True
    _reply(True)
    assert picker.answers == []


def test_folder_name_opens_the_folder(tmp_path):
    (tmp_path / "Aaaa").mkdir()
    picker = Picker(tmp_path)
    picker._accept_save("Aaaa")
    assert picker.visited == [str(tmp_path / "Aaaa")] and picker.answers == []


def test_folder_named_like_the_file_is_refused(tmp_path):
    """« Aaaa » devient « Aaaa.cbz », qui est un dossier : jamais remplacé."""
    (tmp_path / "Aaaa.cbz").mkdir()
    picker = Picker(tmp_path)
    picker._accept_save("Aaaa")
    assert picker.answers == [] and Questions.asked == []
    assert picker.status == ("dialogs.file_picker.invalid_name", {"name": "Aaaa.cbz"})


def test_invalid_or_misplaced_names_are_refused(tmp_path):
    picker = Picker(tmp_path)
    picker._accept_save("Aa:aa")
    assert picker.status == ("dialogs.file_picker.invalid_name", {"name": "Aa:aa"})
    picker._accept_save(str(tmp_path / "Absent" / "Aaaa"))
    assert picker.status == ("dialogs.file_picker.not_found", {"name": str(tmp_path / "Absent")})
    assert picker.answers == [] and Questions.asked == []
