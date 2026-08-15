"""
modules/qt/undo_redo_qt.py — Système Annuler/Refaire (version PySide6)

Reproduit à l'identique le comportement de modules/undo_redo.py +
state_restore.py + les wrappers save_state/undo_action/redo_action
définis dans MosaicView.py (version tkinter).

Différences avec la version tkinter :
  - rename_only : les NameEdit Qt sont recréés à chaque render_mosaic(),
    donc pas d'optimisation possible — on appelle toujours render_mosaic()
  - update_flatten_button → refresh_toolbar_states (callback passé à
    restore_state_qt)
  - update_positions → non nécessaire en Qt (render recrée tout)

Fonctions publiques :
  save_state_qt(state, refresh_toolbar_cb, force=False)
  undo_action_qt(state, render_mosaic_cb, clear_selection_cb,
                 update_tabs_cb, refresh_toolbar_cb)
  redo_action_qt(state, render_mosaic_cb, clear_selection_cb,
                 update_tabs_cb, refresh_toolbar_cb)
  restore_state_qt(state, saved_state, render_mosaic_cb, clear_selection_cb,
                   update_tabs_cb, refresh_toolbar_cb)
"""

import io
from PIL import Image

from modules.qt.entries import create_centered_thumbnail, get_icon_pil_for_entry
from modules.qt.comic_info import has_comic_info_entry, parse_comic_info_xml
from modules.qt.undo_redo import (
    save_state_data,
    can_undo, can_redo,
    undo_data, redo_data,
    reset_history,           # noqa: F401  (réexporté pour panel_widget.py)
    pop_last_state,          # noqa: F401  (réexporté)
)


# ─────────────────────────────────────────────────────────────────────────────
# Reconstruction des thumbnails pour la version Qt
# ─────────────────────────────────────────────────────────────────────────────

def _reload_thumb_qt(entry: dict, tw: int, th: int):
    """Invalide les caches de vignettes pour une entrée — seront reconstruits depuis bytes au prochain paint().

    N'est appelé que si les bytes ont changé.

    qt_qimage_large DOIT être invalidé ici comme qt_pixmap_large : certains
    outils (ex. sharpness_tool_qt.py::perform_sharpness) appellent
    build_qimage_for_entry(entry) en synchrone juste après avoir écrit de
    nouveaux bytes, ce qui peuple qt_qimage_large avec le résultat de CETTE
    opération précise. Si un undo restaure ensuite des bytes plus anciens
    sans vider qt_qimage_large, _get_pixmap_for_size() (mosaic_canvas.py) le
    retrouve encore rempli et reconstruit qt_pixmap_large à partir de cette
    QImage périmée au lieu de relire les bytes restaurés — la vignette reste
    alors figée sur l'état d'avant le undo indéfiniment, aucun repaint
    (même forcé) ne peut corriger un cache dont la SOURCE est fausse.
    Diagnostiqué le 2026-08-14."""
    entry["qt_pixmap_large"] = None
    entry["qt_qimage_large"] = None
    entry["large_thumb_pil"] = None
    entry["_hash"] = None


def _build_new_entry_qt(entry_data: dict, tw: int, th: int) -> dict:
    """Crée une nouvelle entrée Qt à partir d'un snapshot (sans objet existant)."""
    entry = {
        "orig_name":        entry_data["orig_name"],
        "bytes":            entry_data["bytes"],      # référence partagée
        "extension":        entry_data["extension"],
        "is_image":         entry_data["is_image"],
        "is_dir":           entry_data.get("is_dir", False),
        "is_corrupted":     entry_data.get("is_corrupted", False),
        "corruption_reason": entry_data.get("corruption_reason"),
        # Champs Qt
        "qt_pixmap_large":  None,
        # Champs tkinter absents en Qt (mis à None pour compatibilité state_restore)
        "tk_img":           None,
        "name_entry":       None,
        "name_var":         None,
        "ext_label":        None,
        "img_id":           None,
        "text_id":          None,
        "img":              None,
    }
    _reload_thumb_qt(entry, tw, th)
    return entry


# ─────────────────────────────────────────────────────────────────────────────
# restore_state_qt — analogue Qt de state_restore.restore_state()
# ─────────────────────────────────────────────────────────────────────────────

def restore_state_qt(state,
                     saved_state: dict,
                     render_mosaic_cb,
                     clear_selection_cb,
                     update_tabs_cb,
                     refresh_toolbar_cb):
    """Restaure un état depuis l'historique undo/redo.

    Reproduit à l'identique la logique de state_restore.restore_state(),
    adaptée pour Qt (pas de ImageTk, pas de tk.Text name_entry).
    """
    tw = state.thumb_w
    th = state.thumb_h

    target_entries = saved_state["entries"]

    # Dictionnaire des entrées actuelles par id Python
    entries_by_id = {id(e): e for e in state.images_data}

    new_images_data = []

    # Entrées dont les bytes changent réellement pendant cette restauration —
    # pour forcer leur repaint immédiat après render_mosaic_cb() (voir plus
    # bas), sans attendre le prochain repaint naturel de la vue (peut être
    # différé si la fenêtre principale n'est pas active, ex. undo/redo
    # déclenché depuis la visionneuse — diagnostiqué 2026-08-14).
    changed_entries = []

    for entry_data in target_entries:
        if not isinstance(entry_data, dict):
            # Ancien format tuple — ne devrait pas arriver en Qt, mais on gère
            if len(entry_data) == 2:
                entry_id, saved_name = entry_data
                saved_bytes = None
            else:
                entry_id, saved_name, saved_bytes = entry_data
            if entry_id in entries_by_id:
                entry = entries_by_id[entry_id]
                entry["orig_name"] = saved_name
                if saved_bytes is not None and entry["is_image"]:
                    entry["bytes"] = saved_bytes[:]
                    _reload_thumb_qt(entry, tw, th)
                    changed_entries.append(entry)
                new_images_data.append(entry)
                del entries_by_id[entry_id]
            continue

        # Format normal (dict)
        original_id = entry_data.get("_original_id")

        if original_id and original_id in entries_by_id:
            # Réutilise l'entrée existante
            entry = entries_by_id[original_id]
            entry["orig_name"]        = entry_data["orig_name"]
            entry["is_corrupted"]     = entry_data.get("is_corrupted", False)
            entry["corruption_reason"] = entry_data.get("corruption_reason")

            if entry_data["bytes"] is not None:
                bytes_changed = entry["bytes"] is not entry_data["bytes"]
                entry["bytes"] = entry_data["bytes"]  # référence partagée

                if bytes_changed:
                    _reload_thumb_qt(entry, tw, th)
                    changed_entries.append(entry)

            del entries_by_id[original_id]
        else:
            # Crée une nouvelle entrée
            entry = _build_new_entry_qt(entry_data, tw, th)

        new_images_data.append(entry)

    # Les entrées orphelines : invalider les pixmaps Qt (libère la mémoire)
    for orphan in entries_by_id.values():
        orphan["qt_pixmap_large"] = None
        orphan["qt_qimage_large"] = None

    # Met à jour images_data en place
    state.images_data[:] = new_images_data
    state.modified            = saved_state["modified"]
    state.needs_renumbering   = saved_state["needs_renumbering"]
    state.current_sort_method = saved_state.get("current_sort_method")
    state.current_sort_order  = saved_state.get("current_sort_order", "asc")

    # Restaure all_entries et current_directory (aplatissement de répertoires)
    saved_all_entries = saved_state.get("all_entries")
    if saved_all_entries is not None:
        restored_all = []
        all_by_id = {id(e): e for e in state.images_data}
        for entry_data in saved_all_entries:
            original_id = entry_data.get("_original_id")
            if original_id and original_id in all_by_id:
                restored_all.append(all_by_id[original_id])
            else:
                restored_all.append(_build_new_entry_qt(entry_data, tw, th))
        state.all_entries = restored_all
    else:
        state.all_entries = list(state.images_data)
    state.current_directory = saved_state.get("current_directory", "")

    # Vérifie et restaure les métadonnées ComicInfo.xml
    has_xml = has_comic_info_entry(state)
    if has_xml:
        for entry in state.images_data:
            if entry.get("orig_name", "").lower().endswith("comicinfo.xml"):
                xml_content = entry.get("bytes")
                if xml_content:
                    state.comic_metadata = parse_comic_info_xml(xml_content)
                    if state.comic_metadata:
                        from modules.qt.comic_info import build_page_attrs_map, sync_pages_in_xml_data
                        from modules.qt.metadata_signal import metadata_signal
                        build_page_attrs_map(state)
                        sync_pages_in_xml_data(state, emit_signal=False)
                        metadata_signal.emit()
                    break
        update_tabs_cb()
    elif state.comic_metadata:
        state.comic_metadata = None
        state._page_attrs_by_entry_id = {}
        update_tabs_cb()

    # Restaure la sélection : noms sauvegardés dans l'état historique
    selected_names = saved_state.get("selected_names", set())
    if selected_names:
        restored = {i for i, e in enumerate(state.images_data)
                    if e['orig_name'] in selected_names}
        if restored:
            state.selected_indices = restored
        else:
            clear_selection_cb()
    else:
        clear_selection_cb()

    # En Qt, render_mosaic() recrée tous les items (pas d'optimisation rename_only)
    render_mosaic_cb()

    # Force le repaint immédiat des vignettes dont les bytes ont réellement
    # changé pendant cette restauration (ex. undo/redo d'un ajustement
    # sharpness) — sans ça, quand la fenêtre principale n'est pas la fenêtre
    # active (visionneuse ouverte par-dessus), Qt peut différer le repaint
    # naturel de la mosaïque. entry["_real_idx"] n'est fiable qu'APRÈS
    # render_mosaic_cb() (assigné par ThumbnailItem.__init__), d'où la lecture
    # ici plutôt que pendant la boucle de restauration plus haut. Cible
    # uniquement ces items précis (pas toute la mosaïque), y compris pour
    # plusieurs pages modifiées en une seule opération d'historique (ex. un
    # futur traitement par lot). Voir mosaic_canvas.py::refresh_thumbnails_visual.
    if changed_entries:
        real_indices = [e["_real_idx"] for e in changed_entries if e.get("_real_idx") is not None]
        if real_indices and hasattr(render_mosaic_cb, "__self__"):
            canvas = render_mosaic_cb.__self__
            if hasattr(canvas, "refresh_thumbnails_visual"):
                canvas.refresh_thumbnails_visual(real_indices)

    refresh_toolbar_cb()


# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# rollback_to_current_state_qt — restaure le sommet sans décrémenter
# ─────────────────────────────────────────────────────────────────────────────

def rollback_to_current_state_qt(state, render_mosaic_cb, clear_selection_cb,
                                  update_tabs_cb, refresh_toolbar_cb):
    """Restaure l'état au sommet de l'historique (history_index) sans le décrémenter.

    Utilisé pour annuler une opération en cours (ex. rotation annulée) quand
    save_state() n'a rien sauvegardé avant le lancement (état identique au précédent) :
    les modifications en place sont défaites en relisant le snapshot existant.
    """
    if state.history_index < 0 or not state.history:
        return
    saved_state = state.history[state.history_index]
    restore_state_qt(state, saved_state, render_mosaic_cb, clear_selection_cb,
                     update_tabs_cb, refresh_toolbar_cb)


# ─────────────────────────────────────────────────────────────────────────────
# Wrappers publics — save_state_qt / undo_action_qt / redo_action_qt
# ─────────────────────────────────────────────────────────────────────────────

def save_state_qt(state, refresh_toolbar_cb, force=False):
    """Sauvegarde l'état courant dans l'historique.

    Analogue à save_state() dans MosaicView.py tkinter.
    Appelle save_state_data() (logique data commune), puis refresh_toolbar
    pour mettre à jour les boutons Annuler/Refaire.
    Si force=True, sauvegarde même si l'état semble identique (ex. bytes=None).
    """
    if save_state_data(state, force=force):
        refresh_toolbar_cb()


def undo_action_qt(state, render_mosaic_cb, clear_selection_cb,
                   update_tabs_cb, refresh_toolbar_cb):
    """Annule la dernière action.

    Analogue à undo_action() dans MosaicView.py tkinter.
    """
    saved_state = undo_data(state)
    if saved_state is None:
        return
    restore_state_qt(state, saved_state, render_mosaic_cb, clear_selection_cb,
                     update_tabs_cb, refresh_toolbar_cb)
    refresh_toolbar_cb()


def redo_action_qt(state, render_mosaic_cb, clear_selection_cb,
                   update_tabs_cb, refresh_toolbar_cb):
    """Rétablit l'action annulée.

    Analogue à redo_action() dans MosaicView.py tkinter.
    """
    saved_state = redo_data(state)
    if saved_state is None:
        return
    restore_state_qt(state, saved_state, render_mosaic_cb, clear_selection_cb,
                     update_tabs_cb, refresh_toolbar_cb)
    refresh_toolbar_cb()
