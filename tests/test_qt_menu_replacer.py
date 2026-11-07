"""Menus du clic droit construits par Qt, en anglais (ascenseurs, champs
numériques) : remplacés dans toute l'application par utils._QtMenuReplacer,
posé au démarrage par MosaicView.main.

utils.py importe PySide6 : la méthode est extraite par AST, les classes de Qt
sont remplacées.
"""
import ast
import pathlib
import types

import pytest

import ast_loader

CONTEXT_MENU, OTHER_EVENT = "context", "other"
CUSTOM, DEFAULT = "custom", "default"


class FakeWidget:
    def __init__(self, parent=None, policy=DEFAULT):
        self._parent, self._policy = parent, policy

    def parent(self):
        return self._parent

    def contextMenuPolicy(self):
        return self._policy


class ScrollBar(FakeWidget):
    pass


class LineEdit(FakeWidget):
    pass


class SpinBox(FakeWidget):
    def __init__(self, policy=DEFAULT, edit_policy=DEFAULT):
        super().__init__(policy=policy)
        self.edit = LineEdit(self, edit_policy)

    def findChild(self, cls):
        return self.edit if cls is LineEdit else None


shown = []

Replacer = ast_loader.load_methods(
    "utils.py", "_QtMenuReplacer", {"eventFilter"},
    QEvent=types.SimpleNamespace(ContextMenu=CONTEXT_MENU),
    Qt=types.SimpleNamespace(CustomContextMenu=CUSTOM),
    QScrollBar=ScrollBar, QLineEdit=LineEdit, QAbstractSpinBox=SpinBox,
    _show_lineedit_menu=lambda edit, pos: shown.append((edit, pos)))


def event(kind=CONTEXT_MENU):
    return types.SimpleNamespace(type=lambda: kind, globalPos=lambda: (10, 20))


@pytest.fixture(autouse=True)
def reset():
    shown.clear()


def test_scrollbar_gets_no_menu():
    assert Replacer().eventFilter(ScrollBar(), event()) is True
    assert shown == []


@pytest.mark.parametrize("clicked", ["edit", "arrows"])
def test_number_field_gets_the_translated_menu(clicked):
    spin = SpinBox()
    target = spin.edit if clicked == "edit" else spin
    assert Replacer().eventFilter(target, event()) is True
    assert shown == [(spin.edit, (10, 20))]


@pytest.mark.parametrize("own", ["field", "edit"])
def test_number_field_with_its_own_menu_keeps_it(own):
    spin = SpinBox(policy=CUSTOM) if own == "field" else SpinBox(edit_policy=CUSTOM)
    assert Replacer().eventFilter(spin.edit, event()) is False
    assert shown == []


def test_other_widgets_and_events_are_left_alone():
    assert Replacer().eventFilter(FakeWidget(), event()) is False
    assert Replacer().eventFilter(LineEdit(), event()) is False
    assert Replacer().eventFilter(ScrollBar(), event(OTHER_EVENT)) is False
    assert shown == []


def test_installed_at_startup():
    source = (pathlib.Path(__file__).resolve().parent.parent / "MosaicView.py").read_text(encoding="utf-8")
    main = next(node for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    calls = [node.func.id for node in ast.walk(main)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    assert "install_qt_menu_replacer" in calls
