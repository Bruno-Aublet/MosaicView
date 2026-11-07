"""Coller des fichiers, et « Copier l'archive ».

- plusieurs PDF ou un PDF et une archive collés ensemble : tous sont traités. Dans
  un panneau vide, le premier s'ouvre et les autres s'y ajoutent une fois chargé ;
  dans un document ouvert, une question de résolution par PDF, l'une après l'autre ;
- un fichier d'un type non autorisé (.sfv, .dff…) est refusé avec un message, même
  copié par MosaicView : le collage ne fait aucune exception ;
- « Copier l'archive » copie le fichier du disque : le message le dit s'il y a des
  modifications non enregistrées, et un fichier absent est signalé sans rien copier.

panel_widget.py et clipboard_qt.py importent PySide6 : méthodes et fonctions
extraites par AST (tests/ast_loader), interface remplacée.
"""
import os
import struct
import sys
import tempfile
import types

import pytest

from modules.qt.state import AppState

import ast_loader

ROOT = os.path.join(tempfile.gettempdir(), "MosaicViewTemp")


class FakeDpi:
    shown = []

    def __init__(self, *args, **options):
        self.images_only = self.join_cut_images = False

    def ask_async(self, on_result):
        FakeDpi.shown.append(on_result)


class Warned:
    shown = []

    def __init__(self, parent, title, message, message_kwargs=None):
        Warned.shown.append(message_kwargs)

    def show_nonmodal(self):
        pass


Methods = ast_loader.load_methods(
    "panel_widget.py", "PanelWidget", {"_load_files", "_merge_pdfs", "_show_dpi_dialog_for_merge"},
    os=os, reset_history=lambda state: None, _WarnDialog=Warned)


class Panel(Methods):
    def __init__(self, opened=False):
        self._state = AppState()
        self._state.current_file = "ouvert.cbz" if opened else None
        self._canvas = None
        self._pending_load_paths = []
        self.merged_archives = []
        self.images = []
        self.opened = []
        self._pdf_loader = types.SimpleNamespace(load=lambda p: self.open(p))
        self._loader = types.SimpleNamespace(load=lambda p: self.open(p))

    def open(self, what):
        self._state.doc_generation += 1
        self.opened.append(what)

    def _replacing_load_in_progress(self):
        return False

    def _refresh_title(self):
        pass

    def _start_replacing_load(self, start):
        start()

    def _import_merge_archive(self, path):
        self.merged_archives.append(path)

    def _start_image_load(self, files, *args, **options):
        self.images.extend(files)


@pytest.fixture
def merged(monkeypatch):
    done = []
    FakeDpi.shown, Warned.shown = [], []
    monkeypatch.setitem(sys.modules, "modules.qt.pdf_loading_qt", types.SimpleNamespace(
        DpiDialog=FakeDpi,
        import_and_merge_pdf=lambda path, *args, **options: done.append(path)))
    monkeypatch.setitem(sys.modules, "modules.qt.archive_loader",
                        types.SimpleNamespace(_natural_sort_key=lambda name: name))
    return done


def test_two_pdfs_in_an_empty_panel_open_the_first_and_add_the_second(merged):
    panel = Panel()
    panel._load_files(["b.pdf", "a.pdf"])
    assert panel.opened == ["a.pdf"]
    assert panel._pending_load_paths == [
        (panel._state.doc_generation, ["b.pdf"], {"from_drop": False, "rename_collisions_as_copy": False})]


def test_pdf_and_archive_in_an_empty_panel_keep_the_archive_for_later(merged):
    panel = Panel()
    panel._load_files(["z.cbz", "a.pdf"])
    assert panel.opened == ["a.pdf"]
    assert panel._pending_load_paths[0][1] == ["z.cbz"]


def test_archives_alone_open_together(merged):
    panel = Panel()
    panel._load_files(["b.cbz", "a.cbz"])
    assert panel.opened == [["a.cbz", "b.cbz"]] and panel._pending_load_paths == []


def test_pdfs_added_to_an_open_comic_are_asked_one_after_the_other(merged):
    panel = Panel(opened=True)
    panel._load_files(["a.pdf", "b.pdf"])
    assert len(FakeDpi.shown) == 1 and merged == []
    FakeDpi.shown[0](150)
    assert merged == ["a.pdf"] and len(FakeDpi.shown) == 2
    FakeDpi.shown[1](150)
    assert merged == ["a.pdf", "b.pdf"]


def test_refusing_a_pdf_still_asks_for_the_next_one(merged):
    panel = Panel(opened=True)
    panel._load_files(["a.pdf", "b.pdf"])
    FakeDpi.shown[0](None)
    assert merged == [] and len(FakeDpi.shown) == 2


def test_closed_document_stops_the_questions(merged):
    panel = Panel(opened=True)
    panel._load_files(["a.pdf", "b.pdf"])
    panel._state.doc_generation += 1
    FakeDpi.shown[0](None)
    assert len(FakeDpi.shown) == 1


def test_pdf_and_archive_added_to_an_open_comic_are_both_taken(merged):
    panel = Panel(opened=True)
    panel._load_files(["z.cbz", "a.pdf"])
    assert panel.merged_archives == ["z.cbz"] and len(FakeDpi.shown) == 1


@pytest.mark.parametrize("path", [
    os.path.join("C:\\", "a.sfv"),
    os.path.join(ROOT, "clipboard_1_2", "a.sfv"),
], ids=["ailleurs", "copié par MosaicView"])
def test_file_of_an_unsupported_type_is_always_refused(merged, path):
    panel = Panel(opened=True)
    panel._load_files([path], rename_collisions_as_copy=True)
    assert panel.images == [] and Warned.shown == [{"files": "a.sfv"}]


# --- Copier l'archive -------------------------------------------------------------

class Shown:
    log = []

    def __init__(self, parent, title, message, **options):
        self.text = message()

    def show_nonmodal(self):
        Shown.log.append(self.text)


@pytest.fixture
def clipboard(monkeypatch):
    calls = []
    Shown.log = []
    monkeypatch.setitem(sys.modules, "win32con", types.SimpleNamespace(CF_HDROP=15))
    monkeypatch.setitem(sys.modules, "win32clipboard", types.SimpleNamespace(
        OpenClipboard=lambda: None, CloseClipboard=lambda: None, EmptyClipboard=lambda: None,
        SetClipboardData=lambda fmt, data: calls.append(data)))
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt",
                        types.SimpleNamespace(InfoDialog=Shown, ErrorDialog=Shown))
    return calls


def copy_archive(path, modified):
    state = types.SimpleNamespace(current_file=path, modified=modified)
    ast_loader.load(
        "clipboard_qt.py", {"copy_archive_to_clipboard"},
        _state_module=types.SimpleNamespace(state=state), os=os, struct=struct,
        _=lambda key, **kwargs: key)["copy_archive_to_clipboard"](None)


def test_saved_archive_is_copied_with_the_usual_message(clipboard, tmp_path):
    path = tmp_path / "a.cbz"
    path.write_bytes(b"x")
    copy_archive(str(path), False)
    assert len(clipboard) == 1 and Shown.log == ["messages.info.archive_copied.message"]


def test_unsaved_changes_are_announced_as_left_out(clipboard, tmp_path):
    path = tmp_path / "a.cbz"
    path.write_bytes(b"x")
    copy_archive(str(path), True)
    assert len(clipboard) == 1 and Shown.log == ["messages.info.archive_copied.message_unsaved"]


def test_missing_archive_copies_nothing_and_says_so(clipboard, tmp_path):
    copy_archive(str(tmp_path / "absent.cbz"), False)
    assert clipboard == [] and Shown.log == ["messages.errors.file_not_found.message"]
