"""
modules/qt/image_transforms_qt.py — Rotation et miroir (version PySide6)

Reproduit à l'identique le comportement de modules/image_transforms.py
(fonctions rotate_selected et flip_selected) pour la version Qt.

Différences avec la version tkinter :
  - update_button_text → refresh_toolbar_states (callback Qt)

Fonctions publiques :
  rotate_selected_qt(angle, callbacks)
  flip_selected_qt(direction, callbacks)
"""

from modules.qt import state as _state_module
from modules.qt.image_ops import rotate_entry_data, flip_entry_data


def _regenerate_thumbnail_qt(entry: dict):
    """Invalide qt_pixmap_large et qt_qimage_large pour forcer la reconstruction au prochain paint()."""
    entry["qt_pixmap_large"] = None
    entry["qt_qimage_large"] = None


def rotate_selected_qt(angle, callbacks):
    """Fait pivoter les images sélectionnées de 90°.
    angle: -90 pour rotation droite (horaire), 90 pour rotation gauche (anti-horaire)."""
    state = callbacks.get('state') or _state_module.state
    if not state.selected_indices:
        return

    callbacks['save_state']()

    for idx in sorted(state.selected_indices):
        if idx >= len(state.images_data):
            continue
        entry = state.images_data[idx]
        if rotate_entry_data(entry, angle, state):
            _regenerate_thumbnail_qt(entry)

    state.modified = True
    callbacks['render_mosaic']()
    callbacks['update_button_text']()
    callbacks['save_state']()


def flip_selected_qt(direction, callbacks):
    """Retourne les images sélectionnées.
    direction: 'horizontal' ou 'vertical'."""
    state = callbacks.get('state') or _state_module.state
    if not state.selected_indices:
        return

    callbacks['save_state']()

    for idx in sorted(state.selected_indices):
        if idx >= len(state.images_data):
            continue
        entry = state.images_data[idx]
        if flip_entry_data(entry, direction, state):
            _regenerate_thumbnail_qt(entry)

    state.modified = True
    callbacks['render_mosaic']()
    callbacks['update_button_text']()
    callbacks['save_state']()
