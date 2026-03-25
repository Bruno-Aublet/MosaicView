"""
sorting_qt.py — Tri des images (version PySide6).
Reproduit fidèlement le comportement de show_sort_menu / sort_images de MosaicView.py (tkinter).
"""

from PySide6.QtWidgets import QMenu
from PySide6.QtGui import QCursor

from modules.qt.localization import _
from modules.qt.sorting import sort_images as _sort_images_func
from modules.qt.archive_loader import _natural_sort_key
from modules.qt.entries import get_image_metadata


def sort_images_qt(sort_method: str, save_state_cb, render_mosaic_cb, refresh_toolbar_cb, state=None):
    """Trie les images selon la méthode spécifiée."""
    _sort_images_func(sort_method, {
        "save_state":         save_state_cb,
        "render_mosaic":      render_mosaic_cb,
        "update_button_text": refresh_toolbar_cb,
        "natural_sort_key":   _natural_sort_key,
        "get_image_metadata": get_image_metadata,
        "state":              state,
    })


def show_sort_menu_qt(parent, sort_method_cb):
    """
    Affiche un menu popup avec les options de tri.
    sort_method_cb(method: str) est appelé quand l'utilisateur choisit une option.
    """
    menu = QMenu(parent)
    menu.addAction(_("sort_menu.sort_name"),       lambda: sort_method_cb("name"))
    menu.addAction(_("sort_menu.sort_type"),       lambda: sort_method_cb("type"))
    menu.addAction(_("sort_menu.sort_size"),       lambda: sort_method_cb("weight"))
    menu.addAction(_("sort_menu.sort_width"),      lambda: sort_method_cb("width"))
    menu.addAction(_("sort_menu.sort_height"),     lambda: sort_method_cb("height"))
    menu.addAction(_("sort_menu.sort_resolution"), lambda: sort_method_cb("resolution"))
    menu.addAction(_("sort_menu.sort_dpi"),        lambda: sort_method_cb("dpi"))
    menu.exec(QCursor.pos())
