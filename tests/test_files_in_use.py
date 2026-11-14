"""Fichiers que l'application utilise : la fenêtre de choix de fichier refuse
de les renommer ou de les mettre à la Corbeille
(file_picker_qt.FilePickerWindow._refuse_if_in_use).

- fichiers ouverts (comic d'un panneau, base de la Bibliothèque) : message
  « fermez-le d'abord » (dialogs.file_picker.file_open) ;
- fichiers lus ou à lire : archives d'un chargement en cours, fichiers en
  attente d'ajout, archive d'où une page illisible serait relue
  (PanelWidget._files_in_use), archive ou PDF en cours d'ajout
  (modules/qt/files_in_use.py) : message « en cours d'utilisation »
  (dialogs.file_picker.file_in_use) — l'opération échouerait en route ;
- fichiers d'un traitement par lot en cours.

Méthodes extraites par tests/ast_loader.py, sans Qt.
"""
import os
import sys
import types

import pytest

import ast_loader
from modules.qt import files_in_use
from modules.qt.file_picker import open_file_under
from modules.qt.state import AppState


# --- Registre des lectures en cours ------------------------------------------------

def test_held_path_is_listed_until_released():
    token = files_in_use.hold(r"C:\Aaaa\Bbbb.cbz")
    assert r"C:\Aaaa\Bbbb.cbz" in files_in_use.held_paths()
    files_in_use.release(token)
    assert r"C:\Aaaa\Bbbb.cbz" not in files_in_use.held_paths()


def test_release_twice_is_harmless():
    token = files_in_use.hold(r"C:\Aaaa\Cccc.pdf")
    files_in_use.release(token)
    files_in_use.release(token)
    assert r"C:\Aaaa\Cccc.pdf" not in files_in_use.held_paths()


def test_same_file_held_twice_stays_until_both_are_released():
    first = files_in_use.hold(r"C:\Aaaa\Dddd.cbr")
    second = files_in_use.hold(r"C:\Aaaa\Dddd.cbr")
    files_in_use.release(first)
    assert r"C:\Aaaa\Dddd.cbr" in files_in_use.held_paths()
    files_in_use.release(second)
    assert r"C:\Aaaa\Dddd.cbr" not in files_in_use.held_paths()


# --- Fichiers d'un panneau ---------------------------------------------------------

PanelMethods = ast_loader.load_methods("panel_widget.py", "PanelWidget", {"_files_in_use"})


class Panel(PanelMethods):
    def __init__(self):
        self._state = AppState()
        self._loader = types.SimpleNamespace(_worker=None)
        self._image_worker = None
        self._pending_load_paths = []
        self._queued_image_loads = []
        self._pdf_merge_queue = []


def test_empty_panel_uses_nothing():
    assert Panel()._files_in_use() == []


def test_panel_lists_every_file_it_reads_or_will_read():
    panel = Panel()
    st = panel._state
    st.current_file = r"C:\Aaaa\ouvert.cbz"
    panel._loader._worker = types.SimpleNamespace(_filepaths=[r"C:\Aaaa\ouvert.cbz", r"C:\Aaaa\b.cbz"])
    panel._image_worker = types.SimpleNamespace(_files=[r"C:\Aaaa\c.jpg"])
    panel._pending_load_paths = [(1, [r"C:\Aaaa\d.cbz"], {})]
    panel._queued_image_loads = [(1, [r"C:\Aaaa\e.png"], False)]
    panel._pdf_merge_queue = [(1, r"C:\Aaaa\f.pdf")]
    st.images_data = [{"orig_name": "01.jpg", "bytes": b"x"},
                      {"orig_name": "02.jpg", "bytes": None,
                       "unread_member": {"path": r"C:\Aaaa\g.cbz", "offset": 0, "signature": None}}]
    files = panel._files_in_use()
    for name in ("ouvert.cbz", "b.cbz", "c.jpg", "d.cbz", "e.png", "f.pdf", "g.cbz"):
        assert os.path.join(r"C:\Aaaa", name) in files, name


# --- Refus de la fenêtre de choix --------------------------------------------------

class Opened:
    """Fichiers ouverts, lus et de lots, que chaque test remplit."""
    opened, in_use, batch = [], [], []


def _batch_file_under(path):
    return open_file_under(path, Opened.batch)


PickerMethods = ast_loader.load_methods(
    "file_picker_qt.py", "FilePickerWindow", {"_refuse_if_in_use"},
    os=os, _=lambda key, **params: (key, params),
    _open_documents=lambda: list(Opened.opened), _files_in_use=lambda: list(Opened.in_use),
    open_file_under=open_file_under, batch_file_under=_batch_file_under)


class Picker(PickerMethods):
    def __init__(self):
        self.status = None

    def _set_status(self, text_fn):
        self.status = text_fn() if text_fn else None


@pytest.fixture
def picker(tmp_path):
    Opened.opened, Opened.in_use, Opened.batch = [], [], []
    return Picker()


def test_open_comic_is_refused_with_close_it_first(picker, tmp_path):
    Opened.opened = [str(tmp_path / "Aaaa.cbz")]
    assert picker._refuse_if_in_use([str(tmp_path / "Aaaa.cbz")])
    assert picker.status == ("dialogs.file_picker.file_open", {"name": "Aaaa.cbz"})


def test_file_being_added_is_refused_as_in_use(picker, tmp_path):
    Opened.in_use = [str(tmp_path / "Aaaa" / "Bbbb.pdf")]
    assert picker._refuse_if_in_use([str(tmp_path / "Aaaa")])
    assert picker.status == ("dialogs.file_picker.file_in_use", {"name": "Bbbb.pdf"})


def test_file_held_by_an_addition_is_refused(picker, tmp_path):
    """Archive ou PDF en cours d'ajout, inscrit par files_in_use.hold."""
    path = str(tmp_path / "Cccc.cbz")
    token = files_in_use.hold(path)
    try:
        Opened.in_use = files_in_use.held_paths()
        assert picker._refuse_if_in_use([path])
    finally:
        files_in_use.release(token)


def test_batch_file_is_refused(picker, tmp_path):
    Opened.batch = [str(tmp_path / "Dddd.cbr")]
    assert picker._refuse_if_in_use([str(tmp_path / "Dddd.cbr")])
    assert picker.status[0] == "messages.errors.file_in_batch"


def test_unused_file_is_allowed(picker, tmp_path):
    Opened.opened = [str(tmp_path / "Aaaa.cbz")]
    Opened.in_use = [str(tmp_path / "Bbbb.pdf")]
    assert not picker._refuse_if_in_use([str(tmp_path / "Eeee.cbz")])
    assert picker.status is None


# --- Collecte de la fenêtre : panneaux et lectures inscrites -----------------------

def test_window_collects_panels_and_held_readings(monkeypatch):
    """Fenêtres de premier niveau remplacées : seule la fenêtre principale
    (_all_panels) donne des fichiers."""
    panel = types.SimpleNamespace(_files_in_use=lambda: [r"C:\Aaaa\a.cbz"])
    window = types.SimpleNamespace(_all_panels=lambda: [panel])
    qtwidgets = types.ModuleType("PySide6.QtWidgets")
    qtwidgets.QApplication = types.SimpleNamespace(
        topLevelWidgets=lambda: [window, types.SimpleNamespace()])
    pyside = types.ModuleType("PySide6")
    pyside.QtWidgets = qtwidgets
    monkeypatch.setitem(sys.modules, "PySide6", pyside)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", qtwidgets)
    collect = ast_loader.load("file_picker_qt.py", {"_files_in_use"})["_files_in_use"]
    token = files_in_use.hold(r"C:\Aaaa\b.pdf")
    try:
        assert collect() == [r"C:\Aaaa\a.cbz", r"C:\Aaaa\b.pdf"]
    finally:
        files_in_use.release(token)
