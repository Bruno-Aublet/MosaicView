"""Dossiers d'une archive, vides ou non : présents dans l'archive, ils restent
dans le document et visibles dans la mosaïque jusqu'à ce que l'utilisateur
les aplatisse, quel que soit le format.

- archive_loader.folder_entry : entrée d'un dossier, jamais lue ; un nom qui
  ne désigne aucun vrai dossier (vide, « . », « .. ») n'en donne pas ;
- mosaic_canvas.get_visible_entries_qt : un dossier vide apparaît, à la racine
  comme dans un autre dossier, et l'entrée d'un dossier n'est jamais montrée
  comme un fichier ;
- MosaicCanvas._invert_selection : ne sélectionne jamais un dossier (il ne se
  supprime, ne se copie ni ne se déplace : seul Aplatir le retire) ;
- utils.cbz_folders : dossiers écrits à l'enregistrement, une fois chacun ;
- PanelWidget._has_subdirectory_structure : un dossier vide est un dossier
  (renumérotation, déplacement des pages… attendent l'aplatissement).

archive_loader.py, mosaic_canvas.py et panel_widget.py importent PySide6 : les
fonctions sont extraites par AST (tests/ast_loader.py).
"""
import types

import pytest

import ast_loader
from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import create_entry
from modules.qt.state import AppState
from modules.qt.utils import cbz_folders

folder_entry = ast_loader.load("archive_loader.py", {"folder_entry"},
                               create_entry=create_entry, IMAGE_EXTS=IMAGE_EXTS)["folder_entry"]
visible_entries = ast_loader.load("mosaic_canvas.py",
                                  {"get_visible_entries_qt"})["get_visible_entries_qt"]


class _Thumbnail:
    pass


Canvas = ast_loader.load_methods("mosaic_canvas.py", "MosaicCanvas", {"_invert_selection"},
                                 ThumbnailItem=_Thumbnail)
Panel = ast_loader.load_methods("panel_widget.py", "PanelWidget", {"_has_subdirectory_structure"})


def page(name):
    return {"orig_name": name, "bytes": b"x", "is_image": name.endswith(".jpg"), "is_dir": False}


def folder(name):
    return folder_entry(name)


# --- folder_entry --------------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("Vide/", "Vide/"), ("A/B/", "A/B/"), ("A\\B\\", "A/B/"), ("Vide", "Vide/"),
])
def test_folder_entry_is_a_folder_never_read(name, expected):
    entry = folder_entry(name)
    assert entry["orig_name"] == expected and entry["is_dir"] is True
    assert entry["bytes"] is None and entry["is_image"] is False and not entry["is_corrupted"]


@pytest.mark.parametrize("name", ["/", "", "../", "./x/", "x/../", "a//b/"])
def test_a_name_that_is_not_a_real_folder_gives_no_entry(name):
    assert folder_entry(name) is None


# --- Mosaïque ------------------------------------------------------------------

def state_with(*entries, current=""):
    state = AppState()
    state.images_data = list(entries)
    state.current_directory = current
    return state


def names(state):
    return [e["orig_name"] for e in visible_entries(state)]


def test_empty_folder_is_shown_at_the_root():
    state = state_with(page("p.jpg"), folder("Vide/"), folder("A/B/"), page("C/x.jpg"))
    assert names(state) == ["p.jpg", "Vide/", "A/", "C/"]
    assert all(e["is_dir"] for e in visible_entries(state)[1:])


def test_empty_folder_is_shown_inside_its_parent_and_is_empty_itself():
    state = state_with(folder("A/"), folder("A/B/"), page("A/x.jpg"), current="A/")
    assert names(state) == ["..", "A/B/", "A/x.jpg"]
    assert names(state_with(folder("A/"), folder("A/B/"), current="A/B/")) == [".."]


def test_folder_with_pages_appears_once():
    state = state_with(folder("Ch1/"), page("Ch1/001.jpg"), page("Ch1/002.jpg"))
    assert names(state) == ["Ch1/"]


# --- Inverser la sélection -------------------------------------------------------

def test_invert_selection_never_selects_a_folder():
    canvas = Canvas()
    canvas._state = state_with(folder("Vide/"), page("p1.jpg"), page("p2.jpg"))
    canvas._state.selected_indices = {1}
    canvas._items = []
    canvas.status_changed = types.SimpleNamespace(emit=lambda: None)
    canvas._invert_selection()
    assert canvas._state.selected_indices == {2}


# --- Enregistrement et blocages --------------------------------------------------

def test_folders_to_write_once_each_in_document_order():
    entries = [folder("Vide/"), page("p.jpg"), folder("A/B/"), folder("Vide/"),
               {"orig_name": "/", "is_dir": True}]
    assert cbz_folders(entries) == ["Vide/", "A/B/"]


@pytest.mark.parametrize("entries, expected", [
    ([page("p.jpg")], False),
    ([page("p.jpg"), folder("Vide/")], True),           # un dossier vide est un dossier
    ([page("Ch1/p.jpg")], True),
])
def test_an_empty_folder_counts_as_a_folder(entries, expected):
    panel = Panel()
    panel._state = state_with(*entries)
    assert panel._has_subdirectory_structure() is expected
