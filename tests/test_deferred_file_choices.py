"""Réponse différée de la fenêtre de choix (non modale) : elle vaut pour ce
qui était visé quand la commande a été lancée.

- « Enregistrer la sélection dans un dossier »
  (file_operations_qt.save_selection_to_folder) : les pages exportées sont
  celles sélectionnées au lancement, pas la sélection du moment où le
  dossier est choisi ; une page supprimée entre-temps n'est pas exportée.
- « Ouvrir » dans un panneau (PanelWidget._open_file_dialog) : les fichiers
  choisis ne s'ajoutent pas à un autre comic ouvert dans le panneau pendant
  le choix ; panneau vidé entre-temps, ils y sont ouverts.

Fonctions extraites par tests/ast_loader.py, interface remplacée.
"""
import os
import types

import ast_loader
from modules.qt.state import AppState


def page(name):
    return {"orig_name": name, "bytes": name.encode(), "is_image": True, "is_dir": False}


class Config:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


# --- Enregistrer la sélection dans un dossier --------------------------------------

class Export:
    """Remplace l'interface : le test répond lui-même au choix du dossier."""

    def __init__(self):
        self.on_folder = None
        self.written = []
        self.config = Config()

    def deps(self):
        return dict(
            os=os, _=lambda key, **params: key,
            _state_module=types.SimpleNamespace(state=None),
            _run_validation_chain=lambda steps, on_all_passed, state: on_all_passed(),
            _confirm_viewer_work_left_out_qt=None, _confirm_altered_pages_qt=None,
            _handle_duplicate_filenames_qt=None,
            _document_still_writable=lambda state: not state.operation_running(),
            _choice_still_valid=lambda state, generation: state.doc_generation == generation,
            get_config_manager=lambda: self.config,
            ask_directory=lambda parent, title, start, on_folder: setattr(self, "on_folder", on_folder),
            unique_names=lambda names: names, windows_safe_relpath=lambda name: name,
            safe_join=os.path.join, _bytes_with_entry_dpi=lambda entry: entry["bytes"],
            _write_file_safely=lambda path, data: self.written.append(os.path.basename(path)),
            FileSavedDialog=lambda *args: None, ExistingFilesDialog=None, ErrorDialog=None,
        )


def export_document():
    state = AppState()
    state.images_data = [page("01.jpg"), page("02.jpg"), page("03.jpg")]
    return state


def start_export(state):
    export = Export()
    save = ast_loader.load("file_operations_qt.py", {"save_selection_to_folder"},
                           **export.deps())["save_selection_to_folder"]
    save(None, {"state": state})
    return export


def test_selected_pages_are_exported(tmp_path):
    state = export_document()
    state.selected_indices.replace([0, 2])
    export = start_export(state)
    export.on_folder(str(tmp_path))
    assert export.written == ["01.jpg", "03.jpg"]


def test_selection_changed_during_the_choice_is_not_what_is_exported(tmp_path):
    state = export_document()
    state.selected_indices.replace([0])
    export = start_export(state)
    state.selected_indices.replace([1, 2])             # sélection changée pendant le choix
    export.on_folder(str(tmp_path))
    assert export.written == ["01.jpg"]


def test_page_deleted_during_the_choice_is_not_exported(tmp_path):
    state = export_document()
    state.selected_indices.replace([0, 1])
    export = start_export(state)
    del state.images_data[0]
    export.on_folder(str(tmp_path))
    assert export.written == ["02.jpg"]


def test_pages_moved_during_the_choice_follow_the_document_order(tmp_path):
    state = export_document()
    state.selected_indices.replace([0, 2])
    export = start_export(state)
    state.images_data.reverse()
    export.on_folder(str(tmp_path))
    assert export.written == ["03.jpg", "01.jpg"]


# --- Ouvrir dans un panneau --------------------------------------------------------

class Opening:
    on_chosen = None


Methods = ast_loader.load_methods(
    "panel_widget.py", "PanelWidget", {"_open_file_dialog"},
    os=os, get_config_manager=Config,
    ask_open_files=lambda parent, title, start, filters, on_chosen: setattr(Opening, "on_chosen", on_chosen),
    file_filter_text=lambda key, *patterns: key)


class Panel(Methods):
    def __init__(self, current_file):
        self._state = AppState()
        self._state.current_file = current_file
        self.loaded = []

    def _load_files(self, paths):
        self.loaded.append(paths)


def test_chosen_files_are_added_to_the_open_comic():
    panel = Panel(r"C:\Aaaa\a.cbz")
    panel._open_file_dialog()
    Opening.on_chosen([r"C:\Aaaa\b.cbz"])
    assert panel.loaded == [[r"C:\Aaaa\b.cbz"]]


def test_chosen_files_are_not_added_to_a_comic_opened_during_the_choice():
    panel = Panel(r"C:\Aaaa\a.cbz")
    panel._open_file_dialog()
    panel._state.doc_generation += 1                   # autre comic ouvert pendant le choix
    panel._state.current_file = r"C:\Aaaa\c.cbz"
    Opening.on_chosen([r"C:\Aaaa\b.cbz"])
    assert panel.loaded == []


def test_chosen_files_open_in_a_panel_emptied_during_the_choice():
    panel = Panel(r"C:\Aaaa\a.cbz")
    panel._open_file_dialog()
    panel._state.doc_generation += 1                   # comic fermé pendant le choix
    panel._state.current_file = None
    Opening.on_chosen([r"C:\Aaaa\b.cbz"])
    assert panel.loaded == [[r"C:\Aaaa\b.cbz"]]


def test_cancelled_choice_loads_nothing():
    panel = Panel(r"C:\Aaaa\a.cbz")
    panel._open_file_dialog()
    Opening.on_chosen([])
    assert panel.loaded == []
