"""
menubar_callbacks_qt.py
Construction du dict de callbacks pour la menubar et les menus contextuels.

Usage :
    from modules.qt.menubar_callbacks_qt import build_menubar_callbacks
    cb = build_menubar_callbacks(self)   # self = instance MainWindow
"""

import webbrowser as _webbrowser
from modules.qt.update_checker_qt import check_for_updates_qt as _check_for_updates_qt
from modules.qt.recent_files import get_recent_files as _get_recent_files
from modules.qt.undo_redo import can_undo, can_redo
from modules.qt.image_transforms_qt import (
    rotate_selected_qt as _rotate_selected_qt,
    flip_selected_qt   as _flip_selected_qt,
)
from modules.qt.split_dialog_qt        import split_page as _split_page_qt
from modules.qt.conversion_dialogs_qt  import convert_selected_images as _convert_selected_images_qt
from modules.qt.resize_dialog_qt       import reduce_selected_images_size_qt as _resize_qt
from modules.qt.merge_dialog_qt        import open_merge_window as _open_merge_window_qt
from modules.qt.ico_creator_qt         import create_ico_from_selected as _create_ico_qt
from modules.qt.adjustments_dialog_qt  import show_image_adjustments_dialog as show_image_adjustments_dialog_qt
from modules.qt.straighten_viewer_qt   import show_straighten_viewer as _show_straighten_viewer_qt
from modules.qt.clone_zone_viewer_qt   import show_clone_zone_viewer as _show_clone_zone_viewer_qt
from modules.qt.text_viewer_qt         import show_text_viewer as _show_text_viewer_qt
from modules.qt.web_import_qt          import show_web_import_dialog as _show_web_import_dialog
from modules.qt.printing_qt            import (
    PRINT_AVAILABLE,
    print_selection as _print_selection_qt,
    print_all       as _print_all_qt,
)


def build_menubar_callbacks(mw) -> dict:
    """Retourne le dict complet de callbacks pour la menubar.

    ``mw`` est l'instance de MainWindow ; aucune importation circulaire
    n'est introduite car on reçoit l'objet par paramètre.
    """
    st = mw._state
    return {
        # ── Fichier ───────────────────────────────────────────────────────────
        "open_file":                mw._open_file_dialog,
        "close_file":               mw._close_file,
        "get_recent_files":         _get_recent_files,
        "open_recent_file":         mw._open_recent_file,
        "clear_recent_files":       mw._clear_recent_files,
        "show_web_import_dialog":   lambda: _show_web_import_dialog(mw, mw._canvas, mw._web_import_callbacks()),
        "batch_convert_cbr_to_cbz": mw._batch_convert_cbr_to_cbz,
        "batch_convert_cb7_to_cbz": mw._batch_convert_cb7_to_cbz,
        "batch_convert_cbt_to_cbz": mw._batch_convert_cbt_to_cbz,
        "batch_convert_pdf_to_cbz": mw._batch_convert_pdf_to_cbz,
        "batch_convert_img_to_cbz": mw._batch_convert_img_to_cbz,
        "save_as_cbz":              mw._save_as_cbz,
        "save_selection_as_cbz":    mw._save_selection_as_cbz,
        "save_selection_to_folder": mw._save_selection_to_folder,
        "create_cbz_from_images":   mw._create_cbz_from_images,
        "apply_new_names":          mw._apply_new_names,
        "PRINT_AVAILABLE":          PRINT_AVAILABLE,
        "print_all":                lambda: _print_all_qt(mw, mw._canvas, st),
        "print_selection":          lambda: _print_selection_qt(mw, mw._canvas, st),
        "on_window_close":          mw.close,
        # ── Édition ───────────────────────────────────────────────────────────
        "can_undo":                 lambda: can_undo(st),
        "can_redo":                 lambda: can_redo(st),
        "undo_action":              mw._undo_action,
        "redo_action":              mw._redo_action,
        "copy_selected":            mw._copy_selected,
        "cut_selected":             mw._cut_selected,
        "copy_archive_to_clipboard": mw._copy_archive_to_clipboard,
        "paste_ctrl_v":             mw._paste_ctrl_v,
        "delete_selected":          mw._delete_selected_qt,
        "invert_selection":         mw._canvas._invert_selection,
        "select_all":               mw._canvas._select_all,
        "clear_selection":          mw._canvas._clear_selection_and_emit,
        "render_mosaic":            mw._canvas.render_mosaic,
        # ── Images ────────────────────────────────────────────────────────────
        "rotate_selected_right":    lambda: _rotate_selected_qt(-90, mw._image_transforms_callbacks()),
        "rotate_selected_left":     lambda: _rotate_selected_qt(90,  mw._image_transforms_callbacks()),
        "flip_selected_horizontal": lambda: _flip_selected_qt('horizontal', mw._image_transforms_callbacks()),
        "flip_selected_vertical":   lambda: _flip_selected_qt('vertical',   mw._image_transforms_callbacks()),
        "reduce_selected_images_size":   lambda: _resize_qt(mw, mw._resize_callbacks()),
        "show_image_adjustments_dialog": lambda: show_image_adjustments_dialog_qt(mw, mw._adjustments_callbacks()),
        "show_straighten_viewer":        lambda: _show_straighten_viewer_qt(mw, mw._straighten_callbacks()),
        "straighten":                    lambda: _show_straighten_viewer_qt(mw, mw._straighten_callbacks()),
        "show_clone_zone_viewer":        lambda: _show_clone_zone_viewer_qt(mw, mw._clone_zone_callbacks()),
        "clone_zone":                    lambda: _show_clone_zone_viewer_qt(mw, mw._clone_zone_callbacks()),
        "show_text_viewer":              lambda: _show_text_viewer_qt(mw, mw._text_viewer_callbacks()),
        "text":                          lambda: _show_text_viewer_qt(mw, mw._text_viewer_callbacks()),
        "convert_selected_images":  lambda: _convert_selected_images_qt(mw, mw._conversion_callbacks()),
        "crop_selected_image":      mw._crop_selected_image,
        "open_merge_window":        lambda: _open_merge_window_qt(mw, mw._merge_callbacks()),
        "split_page":               lambda: _split_page_qt(mw, mw._split_page_callbacks()),
        "show_animated_gif_dialog": lambda e: (
            __import__(
                'modules.qt.animated_gif_dialog_qt',
                fromlist=['show_animated_gif_dialog'],
            ).show_animated_gif_dialog(
                [e] if not isinstance(e, list) else e,
                mw._animated_gif_callbacks(),
            )
        ),
        "create_ico_from_selected": lambda: _create_ico_qt(mw, mw._ico_callbacks()),
        "replace_corrupted_image":  mw._replace_corrupted_image,
        # ── Archives ──────────────────────────────────────────────────────────
        "has_subdirectory_structure":    mw._has_subdirectory_structure,
        "renumber_pages_auto":           mw._renumber_pages_auto,
        "renumber_pages":                mw._renumber_pages,
        "get_current_image_count":       lambda: len(st.images_data),
        "flatten_directories":           mw._flatten_directories,
        "sort_images":                   mw._sort_images,
        # ── Système ───────────────────────────────────────────────────────────
        "get_font_manager":         lambda: mw._font_manager,
        "get_available_languages":  lambda: list(mw._loc.get_available_languages().keys()),
        "get_languages":            lambda: mw._language_list,
        "get_current_language":     lambda: mw._loc.get_current_language(),
        "set_language":             mw._on_language_change,
        "change_language":          mw._on_language_change,
        "get_thumb_size":           lambda: st.current_thumb_size,
        "get_font_size_offset":     lambda: mw._get_config().get_font_size_offset(),
        "MIN_FONT_SIZE_OFFSET":     -5,
        "MAX_FONT_SIZE_OFFSET":     10,
        "get_is_fullscreen":        lambda: mw.isFullScreen(),
        "get_dark_mode":            lambda: st.dark_mode,
        "toggle_theme":             mw._toggle_theme,
        "decrease_font_size":       mw._decrease_font_size,
        "increase_font_size":       mw._increase_font_size,
        "show_user_guide":          mw._show_user_guide,
        "toggle_fullscreen":        mw._toggle_fullscreen,
        "reset_settings":           mw._reset_to_defaults,
        "reset_to_defaults":        mw._reset_to_defaults,
        "toggle_split_ui":          mw._toggle_split_ui,
        "get_split_active":         lambda: mw._split_active,
        "get_toolbar_visible":      lambda: mw._sidebar_visible,
        "toggle_toolbar":           mw._toggle_sidebar,
        "decrease_thumb_size":      mw._decrease_thumb_size,
        "increase_thumb_size":      mw._increase_thumb_size,
        # ── À propos / maintenance ────────────────────────────────────────────
        "clear_temp_files":         mw._clear_temp_files_with_message,
        "clear_recent_files_about": mw._clear_recent_files,
        "clear_config_file":        mw._clear_config_file,
        "clear_clipboard_files":    mw._clear_clipboard_files,
        "open_temp_folder":         mw._open_temp_folder,
        "open_github":              lambda: _webbrowser.open("https://github.com/Bruno-Aublet/MosaicView"),
        "check_for_updates":        lambda: _check_for_updates_qt(mw),
        "show_donation_dialog":      mw._show_donation_dialog,
        "copy_mail_address":        mw._copy_mail_address,
        "show_license_dialog":      mw._show_license_dialog,
        "show_full_gpl_license":    mw._show_full_gpl_license,
        "show_full_unrar_license":  mw._show_full_unrar_license,
        "show_full_7zip_license":   mw._show_full_7zip_license,
        "show_full_piqad_license":   mw._show_full_piqad_license,
        "show_full_tengwar_license": mw._show_full_tengwar_license,
        # ── Sélection (contexte image) ────────────────────────────────────────
        "select_single": lambda idx: (
            mw._canvas._clear_selection(),
            mw._canvas._items[
                next((i for i, it in enumerate(mw._canvas._items) if it.real_idx == idx), -1)
            ].set_selected(True)
            if any(it.real_idx == idx for it in mw._canvas._items) else None,
            st.selected_indices.add(idx),
            mw._canvas.status_changed.emit(),
        ),
    }
