"""Fenêtre NFO (nfo_dialog_qt) : nom de fichier refusé s'il ne peut pas être
écrit sur le disque, doublon reconnu, « Enregistrer » grisé tant que rien n'a
changé. Fichier NFO ajouté au document : extensions d'image communes.

nfo_dialog_qt.py et panel_widget.py importent PySide6 : fonctions et méthodes
extraites par AST.
"""
import ast
import os

import ast_loader

MODULE = "nfo_dialog_qt.py"
_utils = ast_loader.load("utils.py", {"windows_safe_component", "_WINDOWS_RESERVED_NAMES",
                                      "_WINDOWS_FORBIDDEN_CHARS"}, os=os)
name_error_key = ast_loader.load(MODULE, {"_name_error_key"},
                                 windows_safe_component=_utils["windows_safe_component"])["_name_error_key"]

INVALID = "dialogs.macro_name_error.invalid_chars"
RESERVED = "dialogs.macro_name_error.reserved_name"


def test_valid_names_are_accepted():
    for name in ("Aaaa.nfo", "Aaaa Bbbb (2).nfo", "Aaaa.Bbbb.nfo", "Éééé.nfo"):
        assert name_error_key(name) is None, name


def test_name_that_would_make_a_folder_or_be_altered_by_windows_is_refused():
    """« / » créait un dossier dans l'archive ; « : » ou « ? » ne peuvent pas
    être écrits sur le disque."""
    for name in ("Aaaa/Bbbb.nfo", "Aaaa\\Bbbb.nfo", "Aaaa:Bbbb.nfo", "Aaaa?.nfo",
                 'Aaaa"Bbbb.nfo', "Aaaa\tBbbb.nfo"):
        assert name_error_key(name) == INVALID, name


def test_reserved_windows_name_is_refused():
    for name in ("CON.nfo", "nul.nfo", "COM1.nfo"):
        assert name_error_key(name) == RESERVED, name


class _Field:
    def __init__(self, text):
        self.value = text

    def text(self):
        return self.value

    toPlainText = text


class _Button:
    enabled = True

    def setEnabled(self, value):
        self.enabled = value


Dialog = ast_loader.load_methods(MODULE, "_NfoDialog", {"_update_save_button", "_name_taken"})


def _dialog(name, text, entry=None, images=()):
    d = Dialog()
    d._edit_filename, d._text_edit, d._btn_create = _Field(name), _Field(text), _Button()
    d._initial = (name, text)
    d._entry, d._edit_mode = entry, entry is not None
    d._state = type("State", (), {"images_data": list(images)})()
    return d


def test_save_button_is_greyed_until_something_changes():
    """Enregistrer sans modification marquait le document modifié et
    réécrivait le fichier en UTF-8."""
    d = _dialog("Aaaa", "Bbbb")
    d._update_save_button()
    assert d._btn_create.enabled is False
    d._text_edit.value = "Bbbb Cccc"
    d._update_save_button()
    assert d._btn_create.enabled is True
    d._text_edit.value = "Bbbb"
    d._update_save_button()
    assert d._btn_create.enabled is False
    d._edit_filename.value = "Cccc"
    d._update_save_button()
    assert d._btn_create.enabled is True


def test_duplicate_name_ignores_case_and_the_edited_file_itself():
    edited = {"orig_name": "Aaaa.nfo"}
    other = {"orig_name": "Bbbb.nfo"}
    d = _dialog("Aaaa", "", entry=edited, images=[edited, other])
    assert not d._name_taken("AAAA.nfo")
    assert d._name_taken("bbbb.NFO")
    creating = _dialog("", "", images=[edited, other])
    assert creating._name_taken("aaaa.nfo") and not creating._name_taken("Cccc.nfo")


def test_annex_file_uses_the_common_image_extensions():
    """Une liste locale d'extensions finit par diverger d'IMAGE_EXTS."""
    tree = ast_loader.module_tree(ast_loader.QT_DIR / "panel_widget.py")
    method = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_append_annex_entry")
    call = next(n for n in ast.walk(method)
                if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "create_entry")
    assert isinstance(call.args[2], ast.Name) and call.args[2].id == "IMAGE_EXTS"
