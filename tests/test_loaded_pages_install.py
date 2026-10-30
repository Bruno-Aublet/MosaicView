"""Fin d'un chargement qui remplace le document (archive, PDF) : les pages
ajoutées pendant le chargement (import web, numérisation, collage) ne sont
pas effacées, et l'historique ne garde aucun état pris pendant le chargement
(un Annuler le restaurerait à la place du fichier ouvert).

archive_loader.py importe PySide6 : la fonction en est extraite par AST.
"""
import ast_loader
from modules.qt.state import AppState
from modules.qt.undo_redo import can_undo, save_state_data

_install_loaded_pages = ast_loader.load("archive_loader.py",
                                        {"_install_loaded_pages"})["_install_loaded_pages"]


def make_entry(name):
    return {"orig_name": name, "bytes": name.encode(), "extension": ".jpg", "is_image": True}


def test_loaded_pages_become_the_document():
    st = AppState()
    _install_loaded_pages(st, [make_entry("p01.jpg"), make_entry("p02.jpg")])
    assert [e["orig_name"] for e in st.images_data] == ["p01.jpg", "p02.jpg"]
    assert not st.modified


def test_pages_added_during_loading_are_kept_after_the_loaded_ones():
    st = AppState()
    st.images_data.append(make_entry("scan_0001.jpg"))
    st.modified = True
    save_state_data(st)
    _install_loaded_pages(st, [make_entry("p01.jpg")])
    assert [e["orig_name"] for e in st.images_data] == ["p01.jpg", "scan_0001.jpg"]
    assert st.modified


def test_history_pushed_during_loading_is_dropped():
    st = AppState()
    st.images_data.append(make_entry("scan_0001.jpg"))
    save_state_data(st)
    _install_loaded_pages(st, [make_entry("p01.jpg")])
    save_state_data(st)   # état initial poussé en fin de chargement
    assert not can_undo(st)
