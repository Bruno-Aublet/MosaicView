"""
modules/qt/flatten_directories_qt.py
Aplatissement de l'arborescence des répertoires — version PySide6.
Reproduit à l'identique modules/flatten_directories.py.
"""

import os

from modules.qt import state as _state_module
from modules.qt.undo_redo import save_state_data as _save_state_data


# ═══════════════════════════════════════════════════════════════════════════════
# Fonction principale
# ═══════════════════════════════════════════════════════════════════════════════

def flatten_directories_qt(parent, render_mosaic, refresh_states, status_changed,
                           save_state_func=None):
    """
    Aplatit l'arborescence des répertoires.
    Reproduit flatten_directories() de modules/flatten_directories.py.

    parent          : QWidget parent pour le dialog
    render_mosaic   : callable() → redessine la mosaïque
    refresh_states  : callable() → met à jour la toolbar
    status_changed  : Signal Qt → émet pour mettre à jour la barre de statut
    save_state_func : callable() → sauvegarde undo avant ET après modification (None = noop)
    """
    state = _state_module.state
    all_data = state.all_entries if hasattr(state, 'all_entries') and state.all_entries else state.images_data

    dirs = [e for e in all_data if e.get("is_dir")]
    has_files_with_paths = any('/' in e.get("orig_name", "") and not e.get("is_dir") for e in all_data)

    if not dirs and not has_files_with_paths:
        return

    # Sauvegarde l'état avant modification (undo/redo) — force=True car l'état
    # peut être identique au snapshot initial (mêmes noms, mêmes bytes)
    _save_state_data(state, force=True)

    # Logique métier identique à flatten_directories.py
    seen_names: set[str] = set()
    new_images_data = []
    for entry in all_data:
        if entry.get("is_dir"):
            continue
        orig_name = entry["orig_name"]
        if '/' in orig_name:
            parts = orig_name.split('/')
            first_part = parts[0]
            filename = parts[-1]
            if first_part.startswith("NEW-"):
                orig_name = "NEW-" + filename
            elif first_part.startswith("OLD-"):
                orig_name = "OLD-" + filename
            else:
                orig_name = filename
        base, ext = os.path.splitext(orig_name)
        counter = 1
        new_name = orig_name
        while new_name in seen_names:
            new_name = f"{base}_{counter}{ext}"
            counter += 1
        entry["orig_name"] = new_name
        seen_names.add(new_name)
        new_images_data.append(entry)

    state.images_data[:] = new_images_data
    state.all_entries = list(state.images_data)
    state.current_directory = ""
    state.modified = True
    from modules.qt.comic_info import sync_pages_in_xml_data
    sync_pages_in_xml_data(state, emit_signal=False)
    # Sauvegarde l'état APRÈS l'aplatissement — identique à flatten_directories.py tkinter
    if save_state_func:
        save_state_func()
    render_mosaic()
    refresh_states()
    status_changed.emit()
    from PySide6.QtCore import QTimer
    from modules.qt.metadata_signal import metadata_signal
    QTimer.singleShot(0, metadata_signal.emit)
