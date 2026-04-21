"""
context_menus_qt.py — Menus contextuels (clic droit) de MosaicView Qt.

Reproduit fidèlement context_menus.py (tkinter) en PySide6.
"""

import os

from PySide6.QtWidgets import QMenu
from PySide6.QtGui import QAction

from modules.qt.localization import _
from modules.qt import state as _state_module


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_menu(parent) -> QMenu:
    from modules.qt.font_manager_qt import get_current_font as _get_current_font
    menu = QMenu(parent)
    font = _get_current_font(9)
    menu.setStyleSheet(f'QMenu {{ font-family: "{font.family()}"; font-size: {font.pointSize()}pt; }}')
    return menu


def _add_disabled(menu: QMenu, label: str) -> QAction:
    act = menu.addAction(label)
    act.setEnabled(False)
    return act


# ═══════════════════════════════════════════════════════════════════════════════
# Menu canvas — zone vide (reproduit show_canvas_context_menu)
# ═══════════════════════════════════════════════════════════════════════════════

def show_canvas_context_menu(global_pos, parent, callbacks: dict):
    """
    Menu clic droit sur une zone vide du canvas.
    Reproduit fidèlement context_menus.show_canvas_context_menu().
    """
    state = _state_module.state

    menu = _make_menu(parent)

    has_file      = bool(state.current_file)
    has_images    = bool(state.images_data)
    has_selection = bool(state.selected_indices)
    canvas_empty  = not has_file and not has_images
    print_available = bool(callbacks.get('PRINT_AVAILABLE'))

    # ── Afficher/masquer la colonne d'icônes ──────────────────────────────────

    toolbar_visible = callbacks.get('get_toolbar_visible', lambda: True)()
    sidebar_label = (_("context_menu.canvas_with_file.hide_sidebar") if toolbar_visible
                     else _("context_menu.canvas_with_file.show_sidebar"))
    menu.addAction(sidebar_label, callbacks['toggle_toolbar'])

    menu.addSeparator()

    # ── Section FICHIER ───────────────────────────────────────────────────────

    menu.addAction(_("menu.file_open"), callbacks['open_file'])

    # Fichiers récents (sous-menu)
    from modules.qt.recent_files import get_recent_files
    recent_files = get_recent_files()
    recent_submenu = _make_menu(menu)
    recent_submenu.setTitle(_("context_menu.canvas_no_file.recent_files"))
    if recent_files:
        for filepath in recent_files:
            filename = os.path.basename(filepath)
            recent_submenu.addAction(filename, lambda f=filepath: callbacks['open_recent_file'](f))
        recent_submenu.addSeparator()
        recent_submenu.addAction(
            _("context_menu.canvas_no_file.clear_history"),
            callbacks['clear_recent_files'],
        )
        menu.addMenu(recent_submenu)
    else:
        act = menu.addMenu(recent_submenu)
        act.setEnabled(False)

    if has_file:
        menu.addAction(_("menu.file_close"), callbacks['close_file'])
    else:
        _add_disabled(menu, _("menu.file_close"))

    menu.addSeparator()

    menu.addAction(_("web.import_web_button"), callbacks['show_web_import_dialog'])

    menu.addSeparator()

    if has_images:
        menu.addAction(_("nfo.menu_item"), callbacks['show_nfo_dialog'])
    else:
        _add_disabled(menu, _("nfo.menu_item"))

    menu.addSeparator()

    # Conversions en lot (sous-menu)
    batch_submenu = _make_menu(menu)
    batch_submenu.setTitle(_("menu.batch_convert"))
    batch_submenu.addAction(_("menu.batch_cbr_to_cbz"), callbacks['batch_convert_cbr_to_cbz'])
    batch_submenu.addAction(_("menu.batch_cb7_to_cbz"), callbacks['batch_convert_cb7_to_cbz'])
    batch_submenu.addAction(_("menu.batch_cbt_to_cbz"), callbacks['batch_convert_cbt_to_cbz'])
    batch_submenu.addAction(_("menu.batch_pdf_to_cbz"), callbacks['batch_convert_pdf_to_cbz'])
    batch_submenu.addAction(_("menu.batch_img_to_cbz"), callbacks['batch_convert_img_to_cbz'])
    batch_act = menu.addMenu(batch_submenu)
    if not canvas_empty:
        batch_act.setEnabled(False)

    menu.addSeparator()

    if has_file and not state.modified:
        menu.addAction(_("buttons.save_as"), callbacks['save_as_cbz'])
    else:
        _add_disabled(menu, _("buttons.save_as"))

    if not has_file and has_images:
        menu.addAction(_("buttons.create_cbz"), callbacks['create_cbz_from_images'])
    else:
        _add_disabled(menu, _("buttons.create_cbz"))

    current_ext = os.path.splitext(state.current_file or "")[1].lower() if state.current_file else ""
    can_save_cbz   = has_file and state.modified and current_ext == ".cbz"
    can_create_cbz = has_file and state.modified and current_ext != ".cbz"

    if can_save_cbz:
        menu.addAction(_("labels.apply_save_cbz"), callbacks['apply_new_names'])
    else:
        _add_disabled(menu, _("labels.apply_save_cbz"))

    if can_create_cbz:
        menu.addAction(_("labels.apply_create_cbz"), callbacks['apply_new_names'])
    else:
        _add_disabled(menu, _("labels.apply_create_cbz"))

    menu.addSeparator()

    if print_available and has_images:
        menu.addAction(_("buttons.print_all"), callbacks['print_all'])
    else:
        _add_disabled(menu, _("buttons.print_all"))

    menu.addSeparator()

    from modules.qt.config_manager import get_config_manager
    cfg = get_config_manager()
    has_bookmark_for_current = bool(
        state.current_file and cfg and cfg.get_bookmark(state.current_file) is not None
    )
    has_any_bookmark = bool(cfg and cfg.has_any_bookmark())
    if has_bookmark_for_current:
        menu.addAction(_("context_menu.canvas_with_file.delete_bookmark"),
                       callbacks.get('delete_bookmark', lambda: None))
    else:
        _add_disabled(menu, _("context_menu.canvas_with_file.delete_bookmark"))
    if has_any_bookmark:
        menu.addAction(_("context_menu.canvas_with_file.delete_all_bookmarks"),
                       callbacks.get('delete_all_bookmarks', lambda: None))
    else:
        _add_disabled(menu, _("context_menu.canvas_with_file.delete_all_bookmarks"))

    menu.addSeparator()

    menu.addAction(_("context_menu.canvas.quit"), callbacks['on_window_close'])

    menu.addSeparator()

    # ── Section EDITION ───────────────────────────────────────────────────────

    can_undo = callbacks.get('can_undo') and callbacks['can_undo']()
    can_redo = callbacks.get('can_redo') and callbacks['can_redo']()

    if can_undo:
        act = menu.addAction(_("menu.undo"), callbacks['undo_action'])
        act.setShortcutVisibleInContextMenu(True)
        act.setShortcut("Ctrl+Z")
    else:
        act = _add_disabled(menu, _("menu.undo"))
        act.setShortcut("Ctrl+Z")
        act.setShortcutVisibleInContextMenu(True)

    if can_redo:
        act = menu.addAction(_("menu.redo"), callbacks['redo_action'])
        act.setShortcutVisibleInContextMenu(True)
        act.setShortcut("Ctrl+Y")
    else:
        act = _add_disabled(menu, _("menu.redo"))
        act.setShortcut("Ctrl+Y")
        act.setShortcutVisibleInContextMenu(True)

    menu.addSeparator()

    if has_file:
        menu.addAction(_("context_menu.canvas_with_file.copy_archive"), callbacks['copy_archive_to_clipboard'])
    else:
        _add_disabled(menu, _("context_menu.canvas_with_file.copy_archive"))

    act = menu.addAction(_("menu.paste"), callbacks['paste_ctrl_v'])
    act.setShortcut("Ctrl+V")
    act.setShortcutVisibleInContextMenu(True)

    menu.addSeparator()

    if has_selection:
        act = menu.addAction(_("menu.invert_selection"), callbacks['invert_selection'])
        act.setShortcut("Ctrl+I")
        act.setShortcutVisibleInContextMenu(True)
    else:
        act = _add_disabled(menu, _("menu.invert_selection"))
        act.setShortcut("Ctrl+I")
        act.setShortcutVisibleInContextMenu(True)

    if has_images:
        act = menu.addAction(_("menu.select_all"), callbacks['select_all'])
        act.setShortcut("Ctrl+A")
        act.setShortcutVisibleInContextMenu(True)
    else:
        act = _add_disabled(menu, _("menu.select_all"))
        act.setShortcut("Ctrl+A")
        act.setShortcutVisibleInContextMenu(True)

    menu.addSeparator()

    if has_images:
        act = menu.addAction(_("menu.refresh_mosaic"), callbacks['render_mosaic'])
        act.setShortcut("F5")
        act.setShortcutVisibleInContextMenu(True)
    else:
        act = _add_disabled(menu, _("menu.refresh_mosaic"))
        act.setShortcut("F5")
        act.setShortcutVisibleInContextMenu(True)

    menu.addSeparator()

    # Tri (sous-menu)
    sort_submenu = _make_menu(menu)
    sort_submenu.setTitle(_("menu.sort"))
    sort_submenu.addAction(_("sort_menu.sort_name"),       lambda: callbacks['sort_images']("name"))
    sort_submenu.addAction(_("sort_menu.sort_type"),       lambda: callbacks['sort_images']("type"))
    sort_submenu.addAction(_("sort_menu.sort_size"),       lambda: callbacks['sort_images']("weight"))
    sort_submenu.addAction(_("sort_menu.sort_width"),      lambda: callbacks['sort_images']("width"))
    sort_submenu.addAction(_("sort_menu.sort_height"),     lambda: callbacks['sort_images']("height"))
    sort_submenu.addAction(_("sort_menu.sort_resolution"), lambda: callbacks['sort_images']("resolution"))
    sort_submenu.addAction(_("sort_menu.sort_dpi"),        lambda: callbacks['sort_images']("dpi"))
    sort_act = menu.addMenu(sort_submenu)
    if not has_images:
        sort_act.setEnabled(False)

    menu.addSeparator()

    has_subdir = has_images and callbacks['has_subdirectory_structure']()
    can_renumber = state.needs_renumbering and not has_subdir

    if can_renumber:
        menu.addAction(_("context_menu.image.renumber_auto"),   callbacks['renumber_pages_auto'])
        menu.addAction(_("context_menu.image.renumber_simple"), callbacks['renumber_pages'])
    else:
        _add_disabled(menu, _("context_menu.image.renumber_auto"))
        _add_disabled(menu, _("context_menu.image.renumber_simple"))

    menu.addSeparator()

    all_data = list(state.all_entries) if hasattr(state, 'all_entries') and state.all_entries else list(state.images_data)
    can_flatten = (
        any(e.get("is_dir") for e in all_data)
        or any('/' in e.get("orig_name", "") and not e.get("is_dir") for e in all_data)
    )
    if can_flatten:
        menu.addAction(_("context_menu.canvas_with_file.flatten"), callbacks['flatten_directories'])
    else:
        _add_disabled(menu, _("context_menu.canvas_with_file.flatten"))

    menu.addSeparator()

    # ── Métadonnées ComicVine (sous-menu) ─────────────────────────────────────
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtCore import QUrl
    meta_submenu = _make_menu(menu)
    meta_submenu.setTitle(_("comicvine.menu_label"))
    if has_file:
        meta_submenu.addAction(_("comicvine.tooltip"), callbacks['fetch_metadata'])
    else:
        _add_disabled(meta_submenu, _("comicvine.tooltip"))
    if canvas_empty:
        meta_submenu.addAction(_("buttons.batch_metadata"), callbacks['batch_metadata'])
    else:
        _add_disabled(meta_submenu, _("buttons.batch_metadata"))
    meta_submenu.addAction(_("comicvine.change_api_key"), callbacks['change_apikey'])
    meta_submenu.addSeparator()
    meta_submenu.addAction("ComicVine",
                           lambda: QDesktopServices.openUrl(QUrl("https://comicvine.gamespot.com/")))
    meta_submenu.addAction("ComicVine Scraper",
                           lambda: QDesktopServices.openUrl(QUrl("https://github.com/cbanack/comic-vine-scraper")))
    menu.addMenu(meta_submenu)

    menu.addSeparator()

    # ── Système (sous-menu) ───────────────────────────────────────────────────
    system_submenu = _build_system_submenu(menu, callbacks)
    menu.addMenu(system_submenu)

    # ── À propos (sous-menu) ──────────────────────────────────────────────────
    about_submenu = _build_about_submenu(menu, callbacks)
    menu.addMenu(about_submenu)

    menu.exec(global_pos)


# ═══════════════════════════════════════════════════════════════════════════════
# Menu image — clic droit sur une vignette (reproduit show_context_menu)
# ═══════════════════════════════════════════════════════════════════════════════

def show_image_context_menu(global_pos, real_idx: int, parent, callbacks: dict):
    """
    Menu clic droit sur une vignette.
    Reproduit fidèlement context_menus.show_context_menu().
    """
    state = _state_module.state

    # Sélectionne l'élément si pas déjà sélectionné
    if real_idx not in state.selected_indices:
        callbacks['select_single'](real_idx)

    menu = _make_menu(parent)

    st = state
    has_image_selection = bool(st.selected_indices) and any(
        st.images_data[i].get("is_image", False)
        for i in st.selected_indices
        if i < len(st.images_data)
    )
    single_image_selection = (
        len(st.selected_indices) == 1
        and bool(st.selected_indices)
        and st.images_data[next(iter(st.selected_indices))].get("is_image", False)
        if st.selected_indices and next(iter(st.selected_indices)) < len(st.images_data)
        else False
    )
    multi_image_selection = has_image_selection and len(st.selected_indices) >= 2
    single_entry = st.images_data[next(iter(st.selected_indices))] if single_image_selection else None
    is_corrupted = single_image_selection and single_entry.get("is_corrupted", False)
    is_animated_gif = (
        single_image_selection
        and not is_corrupted
        and single_entry.get("extension", "").lower() == ".gif"
        and single_entry.get("is_animated_gif", False)
    )

    # ── Section IMAGES ────────────────────────────────────────────────────────

    # Rotation (sous-menu)
    rotation_submenu = _make_menu(menu)
    rotation_submenu.setTitle(_("menu.rotation"))
    rotation_submenu.addAction(_("context_menu.image.rotate_right"), callbacks['rotate_selected_right'])
    rotation_submenu.addAction(_("context_menu.image.rotate_left"),  callbacks['rotate_selected_left'])
    rotation_submenu.addAction(_("context_menu.image.mirror"),       callbacks['flip_selected_horizontal'])
    rotation_submenu.addAction(_("context_menu.image.mirror_vertical"), callbacks['flip_selected_vertical'])
    rot_act = menu.addMenu(rotation_submenu)
    if not has_image_selection:
        rot_act.setEnabled(False)

    if has_image_selection:
        menu.addAction(_("context_menu.image.reduce_size"), callbacks['reduce_selected_images_size'])
    else:
        _add_disabled(menu, _("context_menu.image.reduce_size"))

    if has_image_selection:
        menu.addAction(_("dialogs.adjustments.window_title"), callbacks['show_image_adjustments_dialog'])
    else:
        _add_disabled(menu, _("dialogs.adjustments.window_title"))

    if bool(st.images_data):
        menu.addAction(_("context_menu.image.straighten"), callbacks['show_straighten_viewer'])
    else:
        _add_disabled(menu, _("context_menu.image.straighten"))

    if bool(st.images_data):
        menu.addAction(_("context_menu.image.clone_zone"), callbacks['show_clone_zone_viewer'])
    else:
        _add_disabled(menu, _("context_menu.image.clone_zone"))

    if bool(st.images_data):
        menu.addAction(_("context_menu.image.text"), callbacks['show_text_viewer'])
    else:
        _add_disabled(menu, _("context_menu.image.text"))

    menu.addSeparator()

    if has_image_selection:
        menu.addAction(_("context_menu.image.convert"), callbacks['convert_selected_images'])
    else:
        _add_disabled(menu, _("context_menu.image.convert"))

    menu.addSeparator()

    if single_image_selection and not is_corrupted:
        menu.addAction(_("context_menu.image.crop"), callbacks['crop_selected_image'])
    else:
        _add_disabled(menu, _("context_menu.image.crop"))

    if multi_image_selection:
        menu.addAction(_("context_menu.image.join"), callbacks['open_merge_window'])
    else:
        _add_disabled(menu, _("context_menu.image.join"))

    if single_image_selection and not is_corrupted:
        menu.addAction(_("context_menu.image.split"), callbacks['split_page'])
    else:
        _add_disabled(menu, _("context_menu.image.split"))

    menu.addSeparator()

    if is_animated_gif:
        menu.addAction(_("context_menu.image.edit_animated_gif"),
            lambda: callbacks['show_animated_gif_dialog'](single_entry))
    else:
        _add_disabled(menu, _("context_menu.image.edit_animated_gif"))

    if single_image_selection and not is_corrupted:
        menu.addAction(_("context_menu.image.create_ico"), callbacks['create_ico_from_selected'])
    else:
        _add_disabled(menu, _("context_menu.image.create_ico"))

    menu.addSeparator()

    if is_corrupted:
        menu.addAction(_("context_menu.image.corrupted_replace"),
            lambda: callbacks['replace_corrupted_image'](next(iter(st.selected_indices))))
    else:
        _add_disabled(menu, _("context_menu.image.corrupted_replace"))

    if is_corrupted:
        menu.addAction(_("context_menu.image.corrupted_delete"), callbacks['delete_selected'])
    else:
        _add_disabled(menu, _("context_menu.image.corrupted_delete"))

    menu.addSeparator()

    # ── Section ARCHIVES ──────────────────────────────────────────────────────

    has_images = bool(st.images_data)

    menu.addSeparator()

    from modules.qt.config_manager import get_config_manager as _gcm
    _cfg = _gcm()
    _has_bm_current = bool(st.current_file and _cfg and _cfg.get_bookmark(st.current_file) is not None)
    _has_any_bm = bool(_cfg and _cfg.has_any_bookmark())
    if _has_bm_current:
        menu.addAction(_("context_menu.canvas_with_file.delete_bookmark"),
                       callbacks.get('delete_bookmark', lambda: None))
    else:
        _add_disabled(menu, _("context_menu.canvas_with_file.delete_bookmark"))
    if _has_any_bm:
        menu.addAction(_("context_menu.canvas_with_file.delete_all_bookmarks"),
                       callbacks.get('delete_all_bookmarks', lambda: None))
    else:
        _add_disabled(menu, _("context_menu.canvas_with_file.delete_all_bookmarks"))

    menu.addSeparator()

    # ── Section SÉLECTION ─────────────────────────────────────────────────────

    has_selection = bool(st.selected_indices)
    has_file = bool(st.current_file)
    print_available = bool(callbacks.get('PRINT_AVAILABLE'))

    if has_file and has_selection:
        menu.addAction(_("buttons.save_selection"), callbacks['save_selection_as_cbz'])
    else:
        _add_disabled(menu, _("buttons.save_selection"))

    if has_selection:
        menu.addAction(_("buttons.save_to_folder"), callbacks['save_selection_to_folder'])
    else:
        _add_disabled(menu, _("buttons.save_to_folder"))

    if print_available and has_selection:
        menu.addAction(_("buttons.print_selection"), callbacks['print_selection'])
    else:
        _add_disabled(menu, _("buttons.print_selection"))

    menu.addSeparator()

    if has_selection:
        act = menu.addAction(_("buttons.copy"), callbacks['copy_selected'])
        act.setShortcut("Ctrl+C")
        act.setShortcutVisibleInContextMenu(True)
    else:
        act = _add_disabled(menu, _("buttons.copy"))
        act.setShortcut("Ctrl+C")
        act.setShortcutVisibleInContextMenu(True)

    if has_selection:
        act = menu.addAction(_("buttons.cut"), callbacks['cut_selected'])
        act.setShortcut("Ctrl+X")
        act.setShortcutVisibleInContextMenu(True)
    else:
        act = _add_disabled(menu, _("buttons.cut"))
        act.setShortcut("Ctrl+X")
        act.setShortcutVisibleInContextMenu(True)

    if has_selection:
        act = menu.addAction(_("menu.delete"), callbacks['delete_selected'])
        act.setShortcut("Suppr")
        act.setShortcutVisibleInContextMenu(True)
    else:
        act = _add_disabled(menu, _("menu.delete"))
        act.setShortcut("Suppr")
        act.setShortcutVisibleInContextMenu(True)

    if has_selection:
        act = menu.addAction(_("menu.deselect"), callbacks['clear_selection'])
        act.setShortcut("Esc")
        act.setShortcutVisibleInContextMenu(True)
    else:
        act = _add_disabled(menu, _("menu.deselect"))
        act.setShortcut("Esc")
        act.setShortcutVisibleInContextMenu(True)

    menu.exec(global_pos)


# ═══════════════════════════════════════════════════════════════════════════════
# Menu dossier virtuel (clic droit sur icône de dossier)
# ═══════════════════════════════════════════════════════════════════════════════

def show_dir_context_menu(global_pos, parent, callbacks: dict):
    """
    Menu clic droit sur un dossier virtuel dans la mosaïque.
    """
    menu = _make_menu(parent)
    menu.addAction(_("context_menu.canvas_with_file.flatten"), callbacks['flatten_directories'])
    menu.exec(global_pos)


# ═══════════════════════════════════════════════════════════════════════════════
# Sous-menus Système et À propos (partagés)
# ═══════════════════════════════════════════════════════════════════════════════

def _add_action_ctx(menu, label, cb, enabled=True):
    from PySide6.QtGui import QAction
    act = menu.addAction(label)
    act.setEnabled(bool(enabled and cb))
    if cb and enabled:
        act.triggered.connect(cb)
    return act


def _build_system_submenu(parent, callbacks: dict) -> QMenu:
    """Reproduit exactement _populate_system_menu de menubar_qt.py."""
    submenu = _make_menu(parent)
    submenu.setTitle(_("menu.system"))

    is_fullscreen   = callbacks.get('get_is_fullscreen', lambda: False)()
    dark_mode       = callbacks.get('get_dark_mode', lambda: False)()
    thumb_size      = callbacks.get('get_thumb_size', lambda: 1)()
    font_offset     = callbacks.get('get_font_size_offset', lambda: 0)()
    min_font        = callbacks.get('MIN_FONT_SIZE_OFFSET', -3)
    max_font        = callbacks.get('MAX_FONT_SIZE_OFFSET', 5)

    # Langues (sous-menu)
    from modules.qt.menubar_qt import _populate_language_menu
    lang_menu = _make_menu(submenu)
    lang_menu.setTitle(_("menu.language"))
    _populate_language_menu(lang_menu, callbacks)
    submenu.addMenu(lang_menu)
    submenu.addSeparator()

    _add_action_ctx(submenu, _("context_menu.canvas_with_file.decrease_thumbs"),
                    callbacks.get('decrease_thumb_size'), enabled=thumb_size > 0)
    _add_action_ctx(submenu, _("context_menu.canvas_with_file.increase_thumbs"),
                    callbacks.get('increase_thumb_size'), enabled=thumb_size < 2)
    submenu.addSeparator()

    theme_label = (_("context_menu.canvas_with_file.theme_light") if dark_mode
                   else _("context_menu.canvas_with_file.theme_dark"))
    _add_action_ctx(submenu, theme_label, callbacks.get('toggle_theme'))
    submenu.addSeparator()

    _add_action_ctx(submenu, _("menu.decrease_font"), callbacks.get('decrease_font_size'),
                    enabled=font_offset > min_font)
    _add_action_ctx(submenu, _("menu.increase_font"), callbacks.get('increase_font_size'),
                    enabled=font_offset < max_font)
    submenu.addSeparator()

    _add_action_ctx(submenu, _("context_menu.canvas_with_file.help"), callbacks.get('show_user_guide'))
    submenu.addSeparator()

    fs_label = (_("context_menu.canvas_with_file.fullscreen_exit") if is_fullscreen
                else _("context_menu.canvas_with_file.fullscreen"))
    _add_action_ctx(submenu, fs_label, callbacks.get('toggle_fullscreen'))
    submenu.addSeparator()

    _add_action_ctx(submenu, _("context_menu.canvas.reset_label"), callbacks.get('reset_settings'))
    submenu.addSeparator()

    split_active = callbacks.get('get_split_active', lambda: False)()
    split_label = (_("buttons.unsplit_ui") if split_active else _("buttons.split_ui"))
    _add_action_ctx(submenu, split_label, callbacks.get('toggle_split_ui'))

    return submenu


def _build_about_submenu(parent, callbacks: dict) -> QMenu:
    submenu = _make_menu(parent)
    submenu.setTitle(_("menu.about"))

    submenu.addAction(_("menu.view_on_github"),       callbacks.get("open_github"))

    latest = callbacks.get("_update_latest")
    if latest:
        from modules.qt.font_manager_qt import get_current_font as _gcf
        label = _("updates.menu_update_available").replace("{latest}", latest)
        action = submenu.addAction(label, callbacks.get("check_for_updates"))
        if action:
            font = _gcf(9, bold=True)
            action.setFont(font)
    else:
        submenu.addAction(_("menu.check_for_updates"), callbacks.get("check_for_updates"))
    submenu.addAction(_("menu.show_changelog"), callbacks.get("show_changelog"))
    submenu.addSeparator()
    submenu.addAction(_("donation.menu_label"),   callbacks.get("show_donation_dialog"))
    submenu.addAction(_("mail.menu_label"),        callbacks.get("open_mail"))
    submenu.addAction(_("mail.copy_address"),      callbacks.get("copy_mail_address"))
    submenu.addSeparator()
    submenu.addAction(_("help.config_clear_temp"),        callbacks['clear_temp_files'])
    submenu.addAction(_("help.config_clear_recent"),      callbacks['clear_recent_files_about'])
    submenu.addAction(_("help.config_clear_config"),      callbacks['clear_config_file'])
    submenu.addAction(_("help.config_clear_clipboard"),   callbacks['clear_clipboard_files'])
    submenu.addAction(_("help.config_open_temp_folder"),  callbacks['open_temp_folder'])
    submenu.addSeparator()
    submenu.addAction("© Bruno Aublet 2025-2026",         callbacks['show_license_dialog'])

    lic_menu = _make_menu(submenu)
    lic_menu.setTitle(_("labels.licenses_button"))
    lic_menu.addAction(_("labels.license_gpl_full"),   callbacks.get("show_full_gpl_license"))
    lic_menu.addSeparator()
    lic_menu.addAction(_("labels.license_unrar_full"), callbacks.get("show_full_unrar_license"))
    lic_menu.addAction(_("labels.license_7zip_full"),  callbacks.get("show_full_7zip_license"))
    lic_menu.addSeparator()
    lic_menu.addAction(_("labels.license_piqad_full"),   callbacks.get("show_full_piqad_license"))
    lic_menu.addAction(_("labels.license_tengwar_full"), callbacks.get("show_full_tengwar_license"))
    submenu.addMenu(lic_menu)

    return submenu
