"""
modules/qt/menubar_qt.py
Barre de menus Qt pour MosaicView (remplace modules/menubar.py tkinter).

5 menus : Fichier, Édition, Images, Archives, Système.
Les items désactivés sont grisés. Les menus sont reconstruits à chaque ouverture
(aboutToShow) pour refléter l'état courant.
"""

import json
import os

from PySide6.QtWidgets import QMenuBar, QMenu
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt

from modules.qt.localization import _ as _translate
from modules.qt import state as _state_module
from modules.qt.font_manager_qt import get_current_font as _get_current_font


def _(key):
    """Wrapper : remplace les \\n par espace pour l'affichage en menu."""
    return _translate(key).replace("\n", " ")


def _load_language_names():
    try:
        import sys
        base = getattr(sys, "_MEIPASS", os.path.abspath("."))
        path = os.path.join(base, "locales", "language_names.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

_LANGUAGE_NAMES = _load_language_names()


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════
def _add_action(menu: QMenu, label: str, callback=None, shortcut: str = None,
                enabled: bool = True) -> QAction:
    action = QAction(label, menu)
    action.setFont(_get_current_font(9))
    if shortcut:
        action.setShortcut(shortcut)
    action.setEnabled(enabled)
    if callback and enabled:
        action.triggered.connect(callback)
    menu.addAction(action)
    return action


def _add_submenu(menu: QMenu, label: str, enabled: bool = True) -> QMenu:
    sub = QMenu(label, menu)
    sub.setFont(_get_current_font(9))
    sub.setEnabled(enabled)
    menu.addMenu(sub)
    return sub


# ═══════════════════════════════════════════════════════════════════════════════
# Construction des menus
# ═══════════════════════════════════════════════════════════════════════════════
def _populate_file_menu(menu: QMenu, callbacks: dict):
    menu.clear()
    st = _state_module.state

    _add_action(menu, _("menu.file_open"), callbacks.get("open_file"))

    # Fichiers récents
    recent_files = callbacks["get_recent_files"]() if "get_recent_files" in callbacks else []
    recent_menu = _add_submenu(menu, _("context_menu.canvas_no_file.recent_files"),
                               enabled=bool(recent_files))
    if recent_files:
        for fp in recent_files:
            _add_action(recent_menu, os.path.basename(fp),
                        lambda checked=False, f=fp: callbacks["open_recent_file"](f))
        recent_menu.addSeparator()
        _add_action(recent_menu, _("context_menu.canvas_no_file.clear_history"),
                    callbacks.get("clear_recent_files"))

    _add_action(menu, _("menu.file_close"), callbacks.get("close_file"))
    menu.addSeparator()

    _add_action(menu, _("web.import_web_button"), callbacks.get("show_web_import_dialog"))
    menu.addSeparator()

    # Conversion en lot — actif seulement si canvas vide
    canvas_empty = not st.current_file and not st.images_data
    batch_menu = _add_submenu(menu, _("menu.batch_convert"), enabled=canvas_empty)
    _add_action(batch_menu, _("menu.batch_cbr_to_cbz"), callbacks.get("batch_convert_cbr_to_cbz"))
    _add_action(batch_menu, _("menu.batch_pdf_to_cbz"), callbacks.get("batch_convert_pdf_to_cbz"))
    _add_action(batch_menu, _("menu.batch_img_to_cbz"), callbacks.get("batch_convert_img_to_cbz"))
    menu.addSeparator()

    has_file    = bool(st.current_file)
    has_images  = bool(st.images_data)
    has_sel     = bool(st.selected_indices)
    no_file_img = not st.current_file and has_images
    current_ext = os.path.splitext(st.current_file or "")[1].lower()
    can_save_cbz   = has_file and st.modified and current_ext == ".cbz"
    can_create_cbz = has_file and st.modified and current_ext != ".cbz"

    _add_action(menu, _("buttons.save_as"), callbacks.get("save_as_cbz"),
                enabled=has_file and not st.modified)
    _add_action(menu, _("buttons.save_selection"), callbacks.get("save_selection_as_cbz"),
                enabled=has_images and has_sel)
    _add_action(menu, _("buttons.save_to_folder"), callbacks.get("save_selection_to_folder"),
                enabled=has_images and has_sel)
    _add_action(menu, _("buttons.create_cbz"), callbacks.get("create_cbz_from_images"),
                enabled=no_file_img)
    _add_action(menu, _("labels.apply_save_cbz"), callbacks.get("apply_new_names"),
                enabled=can_save_cbz)
    _add_action(menu, _("labels.apply_create_cbz"), callbacks.get("apply_new_names"),
                enabled=can_create_cbz)
    menu.addSeparator()

    print_avail = callbacks.get("PRINT_AVAILABLE", False)
    _add_action(menu, _("buttons.print_all"), callbacks.get("print_all"),
                enabled=bool(print_avail and has_images))
    _add_action(menu, _("buttons.print_selection"), callbacks.get("print_selection"),
                enabled=bool(print_avail and has_sel))
    menu.addSeparator()

    _add_action(menu, _("context_menu.canvas.quit"), callbacks.get("on_window_close"))


def _populate_edit_menu(menu: QMenu, callbacks: dict):
    menu.clear()
    st = _state_module.state

    has_images = bool(st.images_data)
    has_sel    = bool(st.selected_indices)
    can_undo   = "can_undo" in callbacks and callbacks["can_undo"]()
    can_redo   = "can_redo" in callbacks and callbacks["can_redo"]()

    _add_action(menu, _("menu.undo"), callbacks.get("undo_action"), "Ctrl+Z", enabled=can_undo)
    _add_action(menu, _("menu.redo"), callbacks.get("redo_action"), "Ctrl+Y", enabled=can_redo)
    menu.addSeparator()

    _add_action(menu, _("buttons.copy"), callbacks.get("copy_selected"), "Ctrl+C", enabled=has_sel)
    _add_action(menu, _("buttons.cut"),  callbacks.get("cut_selected"),  "Ctrl+X", enabled=has_sel)
    _add_action(menu, _("context_menu.canvas_with_file.copy_archive"),
                callbacks.get("copy_archive_to_clipboard"), enabled=bool(st.current_file))
    _add_action(menu, _("menu.paste"), callbacks.get("paste_ctrl_v"), "Ctrl+V")
    menu.addSeparator()

    _add_action(menu, _("menu.delete"), callbacks.get("delete_selected"), "Del", enabled=has_sel)
    _add_action(menu, _("menu.invert_selection"), callbacks.get("invert_selection"), "Ctrl+I",
                enabled=has_sel)
    _add_action(menu, _("menu.select_all"), callbacks.get("select_all"), "Ctrl+A",
                enabled=has_images)
    _add_action(menu, _("menu.deselect"), callbacks.get("clear_selection"), "Escape",
                enabled=has_sel)
    menu.addSeparator()

    _add_action(menu, _("menu.refresh_mosaic") + "\tF5", callbacks.get("render_mosaic"),
                enabled=True)


def _populate_images_menu(menu: QMenu, callbacks: dict):
    menu.clear()
    st = _state_module.state

    has_img_sel = bool(st.selected_indices) and any(
        st.images_data[i].get("is_image", False)
        for i in st.selected_indices if i < len(st.images_data)
    )
    single_sel = (len(st.selected_indices) == 1 and has_img_sel)
    multi_sel  = has_img_sel and len(st.selected_indices) >= 2
    single_entry = (st.images_data[next(iter(st.selected_indices))]
                    if single_sel and next(iter(st.selected_indices)) < len(st.images_data)
                    else None)
    is_corrupted = bool(single_entry and single_entry.get("is_corrupted"))
    is_animated  = bool(single_entry and single_entry.get("is_animated_gif")
                        and single_entry.get("extension", "").lower() == ".gif")

    rot_menu = _add_submenu(menu, _("menu.rotation"), enabled=has_img_sel)
    _add_action(rot_menu, _("context_menu.image.rotate_right"),  callbacks.get("rotate_selected_right"))
    _add_action(rot_menu, _("context_menu.image.rotate_left"),   callbacks.get("rotate_selected_left"))
    _add_action(rot_menu, _("context_menu.image.mirror"),        callbacks.get("flip_selected_horizontal"))
    _add_action(rot_menu, _("context_menu.image.mirror_vertical"), callbacks.get("flip_selected_vertical"))

    _add_action(menu, _("context_menu.image.reduce_size"), callbacks.get("reduce_selected_images_size"),
                enabled=has_img_sel)
    _add_action(menu, _("dialogs.adjustments.window_title"), callbacks.get("show_image_adjustments_dialog"),
                enabled=has_img_sel)
    menu.addSeparator()

    _add_action(menu, _("context_menu.image.convert"), callbacks.get("convert_selected_images"),
                enabled=has_img_sel)
    menu.addSeparator()

    _add_action(menu, _("context_menu.image.crop"),  callbacks.get("crop_selected_image"),
                enabled=single_sel and not is_corrupted)
    _add_action(menu, _("context_menu.image.join"),  callbacks.get("open_merge_window"),
                enabled=multi_sel)
    _add_action(menu, _("context_menu.image.split"), callbacks.get("split_page"),
                enabled=single_sel and not is_corrupted)
    menu.addSeparator()

    _add_action(menu, _("context_menu.image.edit_animated_gif"),
                (lambda checked=False, e=single_entry: callbacks["show_animated_gif_dialog"](e))
                if is_animated else None,
                enabled=is_animated)
    _add_action(menu, _("context_menu.image.create_ico"), callbacks.get("create_ico_from_selected"),
                enabled=single_sel and not is_corrupted)
    menu.addSeparator()

    _add_action(menu, _("context_menu.image.corrupted_replace"),
                (lambda checked=False, idx=next(iter(st.selected_indices))
                 if st.selected_indices else 0:
                 callbacks["replace_corrupted_image"](idx))
                if is_corrupted else None,
                enabled=is_corrupted)
    _add_action(menu, _("context_menu.image.corrupted_delete"),
                callbacks.get("delete_selected"), enabled=is_corrupted)


def _populate_archives_menu(menu: QMenu, callbacks: dict):
    menu.clear()
    st = _state_module.state

    has_images = bool(st.images_data)
    has_subdir = has_images and "has_subdirectory_structure" in callbacks and callbacks["has_subdirectory_structure"]()
    can_renumber = st.needs_renumbering and not has_subdir

    _add_action(menu, _("context_menu.image.renumber_auto"),   callbacks.get("renumber_pages_auto"),   enabled=can_renumber)
    _add_action(menu, _("context_menu.image.renumber_simple"), callbacks.get("renumber_pages"),        enabled=can_renumber)
    menu.addSeparator()

    menu.addSeparator()

    all_data = list(st.all_entries) if hasattr(st, "all_entries") and st.all_entries else list(st.images_data)
    can_flatten = (any(e.get("is_dir") for e in all_data)
                   or any("/" in e.get("orig_name", "") and not e.get("is_dir") for e in all_data))
    _add_action(menu, _("context_menu.canvas_with_file.flatten"),
                callbacks.get("flatten_directories"), enabled=can_flatten)
    menu.addSeparator()

    sort_menu = _add_submenu(menu, _("menu.sort"), enabled=has_images)
    for key, label_key in [
        ("name",       "sort_menu.sort_name"),
        ("type",       "sort_menu.sort_type"),
        ("weight",     "sort_menu.sort_size"),
        ("width",      "sort_menu.sort_width"),
        ("height",     "sort_menu.sort_height"),
        ("resolution", "sort_menu.sort_resolution"),
        ("dpi",        "sort_menu.sort_dpi"),
    ]:
        _add_action(sort_menu, _(label_key),
                    lambda checked=False, k=key: callbacks["sort_images"](k)
                    if "sort_images" in callbacks else None)


def _populate_language_menu(menu: QMenu, callbacks: dict):
    """Reproduit _build_language_menu de menubar.py."""
    menu.clear()

    piqad_codes    = {'tlh-piqad'}
    tengwar_codes  = {'sjn-tengwar', 'qya-tengwar'}
    fictional_codes = {'tlh', 'tlh-piqad', 'sjn', 'sjn-tengwar', 'qya', 'qya-tengwar'}

    languages    = callbacks['get_languages']() if 'get_languages' in callbacks else []
    current_lang = callbacks['get_current_language']() if 'get_current_language' in callbacks else ''
    change_fn    = callbacks.get('change_language')

    fm = callbacks['get_font_manager']() if 'get_font_manager' in callbacks else None
    piqad_font   = fm.get_piqad_font(9)   if fm else None
    tengwar_font = fm.get_tengwar_font(9) if fm else None

    translated = _LANGUAGE_NAMES.get(current_lang, _LANGUAGE_NAMES.get("en", {}))

    # En-tête "Langues réelles" (non cliquable)
    hdr_real = QAction(_("menu.language_real"), menu)
    hdr_real.setFont(_get_current_font(9))
    hdr_real.setEnabled(False)
    menu.addAction(hdr_real)

    from PySide6.QtGui import QFont as _QFont

    def _make_lang_action(code, display, base_font, is_current, parent_menu):
        label = ("\u2713\u00a0" if is_current else "\u00a0\u00a0\u00a0") + display
        act = QAction(label, parent_menu)
        act.setFont(base_font)
        if change_fn and code:
            act.triggered.connect(lambda checked=False, c=code: change_fn(c))
        return act

    for code, name, _font_family in languages:
        if code not in fictional_codes:
            display = translated.get(code, name)
            menu.addAction(_make_lang_action(code, display, _get_current_font(9),
                                             code == current_lang, menu))

    menu.addSeparator()

    # En-tête "Langues fictives" (non cliquable)
    hdr_fic = QAction(_("menu.language_fictional"), menu)
    hdr_fic.setFont(_get_current_font(9))
    hdr_fic.setEnabled(False)
    menu.addAction(hdr_fic)

    for code, name, _font_family in languages:
        if code in fictional_codes:
            display = translated.get(code, name)
            if code in piqad_codes and piqad_font:
                base_font = piqad_font
            elif code in tengwar_codes and tengwar_font:
                base_font = tengwar_font
            else:
                base_font = _get_current_font(9)
            menu.addAction(_make_lang_action(code, display, base_font,
                                             code == current_lang, menu))


def _populate_system_menu(menu: QMenu, callbacks: dict):
    menu.clear()

    # Langues (sous-menu, reproduit _build_language_menu de menubar.py)
    lang_menu = _add_submenu(menu, _("menu.language"))
    _populate_language_menu(lang_menu, callbacks)
    menu.addSeparator()

    thumb_size = callbacks["get_thumb_size"]() if "get_thumb_size" in callbacks else 1
    min_font   = callbacks.get("MIN_FONT_SIZE_OFFSET", -3)
    max_font   = callbacks.get("MAX_FONT_SIZE_OFFSET", 5)
    font_off   = callbacks["get_font_size_offset"]() if "get_font_size_offset" in callbacks else 0
    is_fs      = callbacks["get_is_fullscreen"]() if "get_is_fullscreen" in callbacks else False
    dark_mode  = callbacks["get_dark_mode"]() if "get_dark_mode" in callbacks else False

    _add_action(menu, _("context_menu.canvas_with_file.decrease_thumbs"),
                callbacks.get("decrease_thumb_size"), enabled=thumb_size > 0)
    _add_action(menu, _("context_menu.canvas_with_file.increase_thumbs"),
                callbacks.get("increase_thumb_size"), enabled=thumb_size < 2)
    menu.addSeparator()

    theme_label = (_("context_menu.canvas_with_file.theme_light") if dark_mode
                   else _("context_menu.canvas_with_file.theme_dark"))
    _add_action(menu, theme_label, callbacks.get("toggle_theme"))
    menu.addSeparator()

    _add_action(menu, _("menu.decrease_font"), callbacks.get("decrease_font_size"),
                enabled=font_off > min_font)
    _add_action(menu, _("menu.increase_font"), callbacks.get("increase_font_size"),
                enabled=font_off < max_font)
    menu.addSeparator()

    _add_action(menu, _("context_menu.canvas_with_file.help"), callbacks.get("show_user_guide"))
    menu.addSeparator()

    fs_label = (_("context_menu.canvas_with_file.fullscreen_exit") if is_fs
                else _("context_menu.canvas_with_file.fullscreen"))
    _add_action(menu, fs_label, callbacks.get("toggle_fullscreen"), "F11")
    menu.addSeparator()

    _add_action(menu, _("context_menu.canvas.reset_label"), callbacks.get("reset_settings"))
    menu.addSeparator()

    split_active = callbacks.get("get_split_active", lambda: False)()
    split_label = (_("buttons.unsplit_ui") if split_active else _("buttons.split_ui"))
    _add_action(menu, split_label, callbacks.get("toggle_split_ui"))


def _populate_about_menu(menu: QMenu, callbacks: dict):
    menu.clear()

    _add_action(menu, _("donation.menu_label"),    callbacks.get("show_donation_dialog"))
    _add_action(menu, _("mail.menu_label"),         callbacks.get("open_mail"))
    _add_action(menu, _("mail.copy_address"),       callbacks.get("copy_mail_address"))
    menu.addSeparator()

    _add_action(menu, _("help.config_clear_temp"),      callbacks.get("clear_temp_files"))
    _add_action(menu, _("help.config_clear_recent"),    callbacks.get("clear_recent_files_about"))
    _add_action(menu, _("help.config_clear_config"),    callbacks.get("clear_config_file"))
    _add_action(menu, _("help.config_clear_clipboard"), callbacks.get("clear_clipboard_files"))
    _add_action(menu, _("help.config_open_temp_folder"),callbacks.get("open_temp_folder"))
    menu.addSeparator()

    _add_action(menu, _("menu.export_piqad_font"),    callbacks.get("export_piqad_font"))
    _add_action(menu, _("menu.export_tengwar_fonts"), callbacks.get("export_tengwar_fonts"))
    _add_action(menu, _("help.icons_save_all"),       callbacks.get("save_all_icons"))
    menu.addSeparator()

    _add_action(menu, "\u00a9 Bruno Aublet 2025-2026", callbacks.get("show_license_dialog"))

    lic_menu = _add_submenu(menu, _("labels.licenses_button"))
    _add_action(lic_menu, _("labels.license_gpl_full"),   callbacks.get("show_full_gpl_license"))
    lic_menu.addSeparator()
    _add_action(lic_menu, _("labels.license_unrar_full"), callbacks.get("show_full_unrar_license"))
    _add_action(lic_menu, _("labels.license_7zip_full"),  callbacks.get("show_full_7zip_license"))
    lic_menu.addSeparator()
    _add_action(lic_menu, _("labels.license_piqad_full"),   callbacks.get("show_full_piqad_license"))
    _add_action(lic_menu, _("labels.license_tengwar_full"), callbacks.get("show_full_tengwar_license"))


# ═══════════════════════════════════════════════════════════════════════════════
# Point d'entrée
# ═══════════════════════════════════════════════════════════════════════════════
def build_menubar(window, callbacks: dict, menubar: "QMenuBar | None" = None) -> "QMenuBar":
    """
    Peuple la QMenuBar fournie (ou window.menuBar() par défaut).
    Les menus se reconstruisent à chaque ouverture via aboutToShow.
    """
    mb = menubar if menubar is not None else window.menuBar()
    mb.clear()

    menus = [
        (_("menu.file"),     _populate_file_menu),
        (_("menu.edit"),     _populate_edit_menu),
        (_("menu.images"),   _populate_images_menu),
        (_("menu.archives"), _populate_archives_menu),
        (_("menu.system"),   _populate_system_menu),
        (_("menu.about"),    _populate_about_menu),
    ]

    font = _get_current_font(9)
    mb.setFont(font)
    mb.update()

    # ── Entrée chevron : rabat/déploie la colonne d'icônes ────────────────────
    toggle_toolbar = callbacks.get("toggle_toolbar")
    get_toolbar_visible = callbacks.get("get_toolbar_visible", lambda: True)
    sidebar_action = QAction("«" if get_toolbar_visible() else "»", mb)
    sidebar_action.setFont(font)
    def _on_sidebar_toggle():
        if toggle_toolbar:
            toggle_toolbar()
        sidebar_action.setText("«" if get_toolbar_visible() else "»")
        from PySide6.QtCore import QTimer
        def _restore_focus():
            mb.setFocus()
            mb.setActiveAction(sidebar_action)
        QTimer.singleShot(0, _restore_focus)
    sidebar_action.triggered.connect(_on_sidebar_toggle)
    mb.addAction(sidebar_action)
    # Expose un callable pour mettre à jour le chevron sans passer par triggered
    mb._update_sidebar_chevron = lambda: sidebar_action.setText("«" if get_toolbar_visible() else "»")

    for title, populate_fn in menus:
        menu = mb.addMenu(title)
        menu.setFont(font)
        menu.menuAction().setFont(font)
        def make_handler(m, fn):
            def handler():
                fn(m, callbacks)
            return handler
        menu.aboutToShow.connect(make_handler(menu, populate_fn))

    return mb
