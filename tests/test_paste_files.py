"""Coller des fichiers, et « Copier l'archive ».

- plusieurs PDF ou un PDF et une archive collés ensemble : tous sont traités. Dans
  un panneau vide, le premier s'ouvre et les autres s'y ajoutent une fois chargé ;
  dans un document ouvert, une question de résolution par PDF, posée seulement
  une fois l'ajout du précédent terminé (jamais deux conversions à la fois),
  y compris pour des PDF ajoutés par une autre action pendant un ajout en cours
  (file d'attente du panneau, oubliée à la fermeture du fichier) ;
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
    """Question de qualité : shown[i](dpi) y répond (None : refusée)."""
    shown = []

    @staticmethod
    def ask(win, canvas, path, state, on_choice):
        def answer(dpi):
            on_choice(None if dpi is None else types.SimpleNamespace(
                dpi=dpi, images_only=False, join_cut_images=False, total_pages=3,
                pdf_process="process"))
        FakeDpi.shown.append(answer)


class Warned:
    shown = []

    def __init__(self, parent, title, message, message_kwargs=None):
        Warned.shown.append(message_kwargs)

    def show_nonmodal(self):
        pass


Methods = ast_loader.load_methods(
    "panel_widget.py", "PanelWidget",
    {"_load_files", "_merge_pdfs", "_merge_next_pdf", "_show_dpi_dialog_for_merge",
     "_shutdown_loading_on_close"},
    os=os, reset_history=lambda state: None, _WarnDialog=Warned)


class Panel(Methods):
    def __init__(self, opened=False):
        self._state = AppState()
        self._state.current_file = "ouvert.cbz" if opened else None
        self._canvas = None
        self._pending_load_paths = []
        self._pdf_merge_queue = []
        self._pdf_merge_current = None
        self.merged_archives = []
        self.images = []
        self.opened = []
        self._pdf_loader = types.SimpleNamespace(load=lambda p: self.open(p))
        self._loader = types.SimpleNamespace(load=lambda p: self.open(p), shutdown=lambda: None)

    def open(self, what):
        self._state.doc_generation += 1
        self.opened.append(what)

    def _replacing_load_in_progress(self):
        return False

    def _refresh_tabs(self):
        pass

    def _start_replacing_load(self, start):
        start()

    def _import_merge_archive(self, path):
        self.merged_archives.append(path)

    def _start_image_load(self, files, *args, **options):
        self.images.extend(files)

    def _stop_image_load(self):
        return None

    def close_file(self):
        """force_close_file : chargements arrêtés, puis nouveau document."""
        self._shutdown_loading_on_close()
        self._state.doc_generation += 1


class Merged(list):
    """PDF dont l'ajout a commencé ; ends[i]() termine le i-ème ajout."""

    def __init__(self):
        super().__init__()
        self.ends = []
        self.options = []

    def merge(self, path, *args, on_done=None, **options):
        self.append(path)
        self.ends.append(on_done)
        self.options.append(options)


@pytest.fixture
def merged(monkeypatch):
    done = Merged()
    FakeDpi.shown, Warned.shown = [], []
    # Lectures inscrites propres à chaque test : un ajout laissé en cours par
    # un test ne reste pas inscrit pour le suivant
    from modules.qt import files_in_use
    monkeypatch.setattr(files_in_use, "_held", {})
    monkeypatch.setitem(sys.modules, "modules.qt.pdf_loading_qt", types.SimpleNamespace(
        ask_pdf_merge_quality=FakeDpi.ask, import_and_merge_pdf=done.merge))
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
    assert merged == ["a.pdf"] and len(FakeDpi.shown) == 1
    merged.ends[0]()                                     # ajout de a.pdf terminé
    assert len(FakeDpi.shown) == 2
    FakeDpi.shown[1](150)
    assert merged == ["a.pdf", "b.pdf"]


def test_next_pdf_waits_for_the_end_of_the_previous_addition(merged):
    """Deux conversions en même temps : mémoire doublée, textes de progression
    superposés, pages ajoutées dans l'ordre de fin des conversions."""
    panel = Panel(opened=True)
    panel._load_files(["a.pdf", "b.pdf", "c.pdf"])
    FakeDpi.shown[0](150)
    assert len(FakeDpi.shown) == 1
    merged.ends[0]()
    FakeDpi.shown[1](300)
    assert len(FakeDpi.shown) == 2
    merged.ends[1]()
    assert len(FakeDpi.shown) == 3


def test_process_that_opened_the_pdf_is_handed_to_the_addition(merged):
    panel = Panel(opened=True)
    panel._load_files(["a.pdf"])
    FakeDpi.shown[0](150)
    assert merged.options[0]["pdf_process"] == "process"
    assert merged.options[0]["total_pages"] == 3


def test_closed_document_during_an_addition_stops_the_questions(merged):
    panel = Panel(opened=True)
    panel._load_files(["a.pdf", "b.pdf"])
    FakeDpi.shown[0](150)
    panel._state.doc_generation += 1
    merged.ends[0]()
    assert len(FakeDpi.shown) == 1


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


# --- File d'attente des PDF du panneau (PanelWidget._merge_pdfs) -------------------
# Des PDF ajoutés par une autre action (second dépôt, menu Ajouter) pendant un
# ajout en cours attendent leur tour : une seule question de qualité et une
# seule conversion à la fois dans le panneau.

def test_pdf_dropped_during_an_addition_waits_for_its_end(merged):
    panel = Panel(opened=True)
    panel._load_files(["a.pdf"])
    FakeDpi.shown[0](150)
    panel._load_files(["b.pdf"])                         # second dépôt
    assert len(FakeDpi.shown) == 1 and merged == ["a.pdf"]
    merged.ends[0]()
    assert len(FakeDpi.shown) == 2
    FakeDpi.shown[1](150)
    assert merged == ["a.pdf", "b.pdf"]


def test_pdf_dropped_during_the_quality_question_waits(merged):
    panel = Panel(opened=True)
    panel._load_files(["a.pdf"])
    panel._load_files(["b.pdf"])
    assert len(FakeDpi.shown) == 1
    FakeDpi.shown[0](None)                               # question de a.pdf refusée
    assert len(FakeDpi.shown) == 2 and merged == []


def test_cancelled_addition_still_asks_for_the_next_pdf(merged):
    """Annuler n'abandonne que le PDF en cours d'ajout : import_and_merge_pdf
    appelle alors on_done, comme à la fin normale."""
    panel = Panel(opened=True)
    panel._load_files(["a.pdf"])
    FakeDpi.shown[0](150)
    panel._load_files(["b.pdf", "c.pdf"])
    merged.ends[0]()                                     # Annuler sur a.pdf
    assert len(FakeDpi.shown) == 2
    FakeDpi.shown[1](None)
    assert len(FakeDpi.shown) == 3


def test_drops_are_asked_in_their_order(merged):
    panel = Panel(opened=True)
    panel._load_files(["a.pdf"])
    panel._load_files(["c.pdf"])
    panel._load_files(["b.pdf"])
    for answer in range(3):
        FakeDpi.shown[answer](150)
        merged.ends[answer]()
    assert merged == ["a.pdf", "c.pdf", "b.pdf"]


def test_queued_pdfs_are_forgotten_when_the_file_closes(merged):
    panel = Panel(opened=True)
    panel._load_files(["a.pdf"])
    FakeDpi.shown[0](150)
    panel._load_files(["b.pdf"])
    panel.close_file()
    assert panel._pdf_merge_queue == []
    merged.ends[0]()                                     # fin de l'ajout arrêté
    assert len(FakeDpi.shown) == 1


def test_queued_pdfs_of_a_replaced_document_are_not_asked(merged):
    panel = Panel(opened=True)
    panel._load_files(["a.pdf"])
    FakeDpi.shown[0](150)
    panel._load_files(["b.pdf"])
    panel._state.doc_generation += 1
    merged.ends[0]()
    assert len(FakeDpi.shown) == 1 and panel._pdf_merge_current is None


def test_next_document_is_not_blocked_by_the_stopped_addition(merged):
    """L'ajout arrêté par la fermeture peut finir plus tard : le document
    suivant n'attend pas sa fin, et cette fin n'enchaîne rien."""
    panel = Panel(opened=True)
    panel._load_files(["a.pdf"])
    FakeDpi.shown[0](150)
    panel.close_file()
    panel._state.current_file = "suivant.cbz"
    panel._load_files(["c.pdf", "d.pdf"])
    assert len(FakeDpi.shown) == 2                       # c.pdf demandé tout de suite
    FakeDpi.shown[1](150)
    merged.ends[0]()                                     # fin tardive de a.pdf
    assert len(FakeDpi.shown) == 2                       # d.pdf attend la fin de c.pdf
    merged.ends[1]()
    assert len(FakeDpi.shown) == 3


def test_pdf_being_added_is_held_until_its_end(merged):
    """Inscrit dans files_in_use (la fenêtre de choix de fichier refuse de
    le renommer ou de le supprimer) de la question à la fin de l'ajout, même
    détaché par la fermeture du fichier."""
    from modules.qt import files_in_use
    panel = Panel(opened=True)
    panel._load_files(["a.pdf", "b.pdf"])
    assert "a.pdf" in files_in_use.held_paths()
    FakeDpi.shown[0](150)
    assert "a.pdf" in files_in_use.held_paths()
    merged.ends[0]()
    assert "a.pdf" not in files_in_use.held_paths() and "b.pdf" in files_in_use.held_paths()
    panel.close_file()
    FakeDpi.shown[1](None)                               # question fermée par la fermeture
    assert "b.pdf" not in files_in_use.held_paths()


def test_new_drop_after_the_queue_is_empty_starts_at_once(merged):
    panel = Panel(opened=True)
    panel._load_files(["a.pdf"])
    FakeDpi.shown[0](150)
    merged.ends[0]()
    assert panel._pdf_merge_current is None
    panel._load_files(["b.pdf"])
    assert len(FakeDpi.shown) == 2


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
