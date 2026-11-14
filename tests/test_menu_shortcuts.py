"""Raccourcis clavier des menus : noms des touches traduits à l'affichage
(menubar_qt.shortcut_text) et règle unique des commandes du menu Fichier,
partagée par les entrées grisées et les raccourcis Ctrl+S, Ctrl+Maj+S,
Ctrl+W, Ctrl+P (menubar_qt.file_command_states, SAVE_COMMANDS).

menubar_qt.py importe Qt : fonctions extraites par AST."""
import ast
import os
import types
from pathlib import Path

import ast_loader

NAMES = ("_KEY_NAME_KEYS", "shortcut_text", "file_command_states", "SAVE_COMMANDS")
FRENCH = {"keys.ctrl": "Ctrl", "keys.shift": "Maj", "keys.delete": "Suppr",
          "keys.escape": "Échap"}

menubar = ast_loader.load("menubar_qt.py", set(NAMES), os=os,
                          _translate=lambda key: FRENCH[key])


def state(current_file=None, pages=0, selected=(), modified=False):
    return types.SimpleNamespace(current_file=current_file, images_data=[{}] * pages,
                                 selected_indices=set(selected), modified=modified)


def test_key_names_are_translated():
    text = menubar["shortcut_text"]
    assert text("Ctrl+Shift+S") == "Ctrl+Maj+S"
    assert text("Del") == "Suppr"
    assert text("Escape") == "Échap"
    assert text("F11") == "F11" and text("Ctrl+O") == "Ctrl+O"


def test_every_key_name_has_a_french_translation():
    import json
    root = Path(__file__).resolve().parent.parent
    keys = json.loads((root / "locales" / "fr.json").read_text(encoding="utf-8"))["keys"]
    assert {f"keys.{k}" for k in keys} == set(menubar["_KEY_NAME_KEYS"].values())


def save_command(st):
    states = menubar["file_command_states"](st, True)
    return next((name for name, _cb in menubar["SAVE_COMMANDS"] if states[name]), None)


def test_ctrl_s_does_the_only_possible_save():
    assert save_command(state("a.cbz", 2, modified=True)) == "apply_save_cbz"
    assert save_command(state("a.cbr", 2, modified=True)) == "apply_create_cbz"
    assert save_command(state(None, 2)) == "create_cbz"
    assert save_command(state("a.cbz", 2, modified=False)) is None   # rien à enregistrer
    assert save_command(state(None, 0)) is None


def test_at_most_one_save_command_is_possible():
    for st in (state("a.cbz", 2, modified=True), state("a.cbr", 2, modified=True),
               state(None, 2), state("a.cbz", 2), state(None, 0)):
        states = menubar["file_command_states"](st, True)
        assert sum(states[name] for name, _cb in menubar["SAVE_COMMANDS"]) <= 1


def test_other_file_commands():
    states = menubar["file_command_states"]
    assert states(state(None, 0), True)["close_file"] is False
    assert states(state(None, 1), True)["close_file"] is True
    assert states(state("a.cbz", 1), True)["save_as"] is True
    assert states(state(None, 1), True)["save_as"] is False
    assert states(state(None, 1), False)["print_all"] is False        # impression absente
    assert states(state(None, 1), True)["print_all"] is True


def test_file_shortcuts_call_the_menu_callbacks():
    """MainWindow._file_shortcut appelle les callbacks du menu (avec leurs
    blocages) et seulement ceux-là ; chaque raccourci affiché dans le menu
    Fichier a son QShortcut."""
    root = Path(__file__).resolve().parent.parent
    main = (root / "MosaicView.py").read_text(encoding="utf-8")
    for keys in ("Ctrl+S", "Ctrl+Shift+S", "Ctrl+W", "Ctrl+P", "Ctrl+Q", "F1", "Ctrl+O"):
        assert f'QKeySequence("{keys}")' in main, keys
    menu_source = (ast_loader.QT_DIR / "menubar_qt.py").read_text(encoding="utf-8")
    shown = {node.args[3].value for node in ast.walk(ast.parse(menu_source))
             if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_add_action"
             and len(node.args) > 3 and isinstance(node.args[3], ast.Constant)}
    for keys in shown:
        global_keys = {"Del", "Escape", "Ctrl+A", "Ctrl+I"}   # traitées par la mosaïque
        assert keys in global_keys or f'QKeySequence("{keys}")' in main, keys
