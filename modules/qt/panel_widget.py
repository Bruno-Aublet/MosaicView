"""
modules/qt/panel_widget.py
Widget autonome représentant un panneau complet de MosaicView :
  colonne d'icônes gauche + menubar + onglets + canvas mosaïque + barre de statut.

Chaque panneau possède son propre AppState, son propre historique undo/redo,
ses propres loaders (archives, PDF) et ses propres composants UI.

Usage :
    panel = PanelWidget(
        app_ref        = app,
        main_window    = win,
        language_list  = win._language_list,
        loc            = win._loc,
        font_manager   = win._font_manager,
    )
"""

import os
import io
import re
import sys
import json
import shutil
import tempfile
import threading
import time
import zipfile
import subprocess
import configparser

import rarfile

from PySide6.QtWidgets import (
    QWidget, QStackedWidget,
    QVBoxLayout, QHBoxLayout, QSplitter,
    QFileDialog, QDialog, QLabel, QPushButton,
    QApplication, QFrame,
)
from PySide6.QtCore import QTimer, Qt, QThread, Signal
from PySide6.QtGui import QKeySequence, QShortcut

from modules.qt.config_manager import get_config_manager
from modules.qt.localization import _
from modules.qt import state as _state_module
from modules.qt.state import AppState
from modules.qt.entries import (
    get_icon_pil_for_entry, create_centered_thumbnail, create_entry,
    THUMB_SIZES,
)
from modules.qt.undo_redo import reset_history
from modules.qt.undo_redo_qt import (
    save_state_qt as _save_state_qt,
    undo_action_qt as _undo_action_qt,
    redo_action_qt as _redo_action_qt,
    rollback_to_current_state_qt as _rollback_to_current_state_qt,
)
import modules.qt.recent_files as _recent_files_module

from modules.qt.mosaic_canvas import MosaicCanvas
from modules.qt.archive_loader import ArchiveLoader
from modules.qt.menubar_qt import build_menubar
from modules.qt.tabs_qt import TabBar, MetadataTab
from modules.qt.status_bar_qt import StatusBar
from modules.qt.icon_toolbar_qt import build_icon_toolbar as _build_icon_toolbar_module
from modules.qt.file_operations_qt import (
    save_as_cbz as _qt_save_as_cbz,
    save_selection_as_cbz as _qt_save_selection_as_cbz,
    save_selection_to_folder as _qt_save_selection_to_folder,
    create_cbz_from_images as _qt_create_cbz_from_images,
    apply_new_names as _qt_apply_new_names,
)
from modules.qt.batch_dialogs_qt import (
    batch_convert_cbr_to_cbz as _qt_batch_cbr,
    batch_convert_cb7_to_cbz as _qt_batch_cb7,
    batch_convert_cbt_to_cbz as _qt_batch_cbt,
    batch_convert_pdf_to_cbz as _qt_batch_pdf,
    batch_convert_img_to_cbz as _qt_batch_img,
)
from modules.qt.dialogs_qt import MsgDialog as _WarnDialog
from modules.qt.image_viewer_qt import (
    open_image_viewer as _open_image_viewer_qt,
    update_image_viewer_if_open as _update_image_viewer_if_open,
)
from modules.qt.open_with_default_app_qt import open_file_with_default_app as _open_file_with_default_app
from modules.qt.menubar_callbacks_qt import build_menubar_callbacks as _build_menubar_callbacks_module

try:
    from send2trash import send2trash
    _SEND2TRASH_AVAILABLE = True
except ImportError:
    _SEND2TRASH_AVAILABLE = False


class _ImageLoadWorker(QThread):
    """Charge des fichiers image dans un thread séparé."""
    progress  = Signal(int)    # percent
    finished  = Signal(list)   # new_entries
    cancelled = Signal()

    def __init__(self, files_to_add: list, already_open: bool, image_exts: tuple):
        super().__init__()
        self._files      = files_to_add
        self._already_open = already_open
        self._image_exts = image_exts
        self._cancelled  = threading.Event()

    def run(self):
        from modules.qt.entries import create_entry_from_file, create_entries_from_tiff, FileTooLargeError
        new_entries  = []
        errors       = []
        total        = len(self._files)

        for idx, filepath in enumerate(self._files):
            if self._cancelled.is_set():
                self.cancelled.emit()
                return
            ext = os.path.splitext(filepath)[1].lower()
            if ext in ('.tiff', '.tif'):
                try:
                    entries = create_entries_from_tiff(
                        filepath, self._image_exts,
                        add_prefix=self._already_open,
                    )
                    for e in entries:
                        e["source_archive"] = "loose"
                    new_entries.extend(entries)
                except FileTooLargeError as e:
                    errors.append(('too_large', e.filename, e.size_str))
                except Exception:
                    pass
            else:
                try:
                    entry = create_entry_from_file(filepath, self._image_exts)
                    if entry:
                        if self._already_open:
                            entry["orig_name"] = "NEW-" + entry["orig_name"]
                        entry["source_archive"] = "loose"
                        new_entries.append(entry)
                except FileTooLargeError as e:
                    errors.append(('too_large', e.filename, e.size_str))
                except Exception:
                    pass
            pct = int((idx + 1) / total * 100)
            self.progress.emit(pct)

        self.finished.emit(new_entries)


class PanelWidget(QWidget):
    """
    Panneau autonome : colonne gauche (toolbar icônes) + zone centrale
    (menubar + onglets + canvas + statusbar).

    Expose les mêmes attributs publics que MainWindow en exposait auparavant,
    de sorte que tous les modules recevant ``mw`` en paramètre continuent
    de fonctionner sans modification.
    """

    def __init__(self, *, app_ref, main_window, language_list, loc, font_manager, is_primary=True):
        super().__init__()

        self._app_ref       = app_ref
        self._main_window   = main_window   # accès aux actions globales (thème, langue…)
        self._language_list = language_list
        self._loc           = loc
        self._font_manager  = font_manager
        self._is_primary    = is_primary    # False → ne sauvegarde pas ses prefs locales en config

        self._sidebar_visible = True

        # ── État applicatif propre à ce panneau ───────────────────────────────
        self._state = AppState()
        # Expose le state dans le singleton global (requis par les modules qui
        # accèdent à _state_module.state, ex. recent_files, undo_redo_qt…)
        _state_module.state = self._state

        # ── Fichiers récents ──────────────────────────────────────────────────
        _recent_files_module.init_recent_files()

        # ── Layout principal du panneau ───────────────────────────────────────
        h_layout = QHBoxLayout(self)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(0)

        # ── Colonne gauche ────────────────────────────────────────────────────
        self._left_panel = self._build_left_panel()

        # ── Zone centrale ─────────────────────────────────────────────────────
        self._center_panel = self._build_center_panel()

        # ── Splitter gauche/centre ────────────────────────────────────────────
        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.setObjectName("mainSplitter")
        self._splitter.setHandleWidth(3)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(self._left_panel)
        self._splitter.addWidget(self._center_panel)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.splitterMoved.connect(self._on_splitter_moved)
        h_layout.addWidget(self._splitter)

        # ── Chargeurs d'archives ──────────────────────────────────────────────
        self._loader = ArchiveLoader(self, self._canvas, self._state)
        self._loader.loading_finished.connect(self._on_loading_finished)

        from modules.qt.pdf_loading_qt import PdfLoader
        self._pdf_loader = PdfLoader(self, self._canvas, self._state)

        # Worker pour le chargement d'images individuelles
        self._image_worker        = None
        self._image_cancel_holder = [None]
        self._image_overlay_holder = [None]

        # ── Callbacks canvas ──────────────────────────────────────────────────
        self._canvas._load_callback                       = self._handle_dropped_paths
        self._canvas._has_subdirectory_structure_callback = self._has_subdirectory_structure
        self._canvas._warn_flatten_callback               = self._warn_flatten_required_renumber
        self._canvas._warn_flatten_dnd_callback           = self._warn_flatten_required_dnd
        self._canvas._save_state_callback                 = self.save_state
        self._canvas._renumber_after_drop_callback        = self._renumber_no_save
        self._canvas._delete_selected_callback            = self._delete_selected_qt
        self._canvas._web_import_callback                 = self._handle_dropped_web_urls
        self._canvas._inter_panel_drop_callback           = self._on_inter_panel_drop

        # ── Menus contextuels ─────────────────────────────────────────────────
        from modules.qt.context_menus_qt import show_canvas_context_menu, show_image_context_menu, show_dir_context_menu
        self._canvas._canvas_context_menu_callback = lambda pos: show_canvas_context_menu(
            pos, self, self._build_menubar_callbacks()
        )
        self._canvas._context_menu_callback = lambda pos, idx: show_image_context_menu(
            pos, idx, self, self._build_menubar_callbacks()
        )
        self._canvas._dir_context_menu_callback = lambda pos: show_dir_context_menu(
            pos, self, self._build_menubar_callbacks()
        )
        self._canvas._open_image_viewer_callback = self._open_image_viewer
        self._canvas._open_non_image_callback    = _open_file_with_default_app

        # ── Barre d'icônes ────────────────────────────────────────────────────
        self._build_icon_toolbar()

        # ── Barre de menus ────────────────────────────────────────────────────
        self._menubar_callbacks = self._build_menubar_callbacks()
        build_menubar(self, self._menubar_callbacks, self._menubar)
        self._refresh_title()

        # ── Drop entrant ──────────────────────────────────────────────────────
        self.setAcceptDrops(True)

        # ── Mise à jour initiale ──────────────────────────────────────────────
        self._update_status_bar()
        QTimer.singleShot(0, self._canvas.render_mosaic)


    # ──────────────────────────────────────────────────────────────────────────
    # Construction du layout interne
    # ──────────────────────────────────────────────────────────────────────────

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("leftPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._left_panel_layout = layout
        return panel

    def _build_icon_toolbar(self):
        self._icon_toolbar = _build_icon_toolbar_module(self, is_primary=self._is_primary)
        self._left_panel_layout.addWidget(self._icon_toolbar, stretch=1)
        self._init_thumb_size()
        from modules.qt.icon_toolbar_qt import ICON_SIZE_LEVELS, ICON_PAD
        size_idx = self._icon_toolbar._size_index
        self._update_splitter_constraints(size_idx)
        icon_sz, cols = ICON_SIZE_LEVELS[size_idx]
        initial_w = max(cols * (icon_sz + ICON_PAD) + 2 * ICON_PAD + 4, 210)
        self._splitter.setSizes([initial_w, self._splitter.width() - initial_w])

    def _update_splitter_constraints(self, size_index: int):
        from modules.qt.icon_toolbar_qt import ICON_SIZE_LEVELS, ICON_PAD
        icon_sz, cols = ICON_SIZE_LEVELS[size_index]
        cell_w = icon_sz + ICON_PAD
        min_w  = 1 * cell_w + 2 * ICON_PAD + 20
        max_w  = max(cols * cell_w + 2 * ICON_PAD + 4, 210)
        cur_w  = self._left_panel.width()
        self._left_panel.setMinimumWidth(min_w)
        self._left_panel.setMaximumWidth(max_w)
        if cur_w < min_w:
            total = self._splitter.width()
            self._splitter.setSizes([min_w, max(0, total - min_w)])
            cur_w = min_w
        elif cur_w > max_w:
            total = self._splitter.width()
            self._splitter.setSizes([max_w, max(0, total - max_w)])
            cur_w = max_w
        if hasattr(self, "_icon_toolbar"):
            self._icon_toolbar.adapt_cols_to_width(cur_w)

    def _on_splitter_moved(self, _pos: int, _index: int):
        self._icon_toolbar.adapt_cols_to_width(self._left_panel.width())

    def _build_center_panel(self) -> QWidget:
        from PySide6.QtWidgets import QMenuBar
        panel  = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._menubar = QMenuBar(panel)
        layout.addWidget(self._menubar)

        self._tab_bar = TabBar(tooltip_parent=panel)
        self._tab_bar._state = self._state  # lié au state du panneau dès la création
        self._tab_bar.tab_changed.connect(self._on_tab_changed)
        layout.addWidget(self._tab_bar)

        # Bandeau mise à jour (caché par défaut, inséré ici quand nécessaire)
        self._update_banner = None
        self._center_layout = layout  # référence pour insérer le bandeau

        self._content_stack = QStackedWidget()

        self._canvas = MosaicCanvas(self._state)
        self._canvas.status_changed.connect(self._update_status_bar)
        self._canvas.status_changed.connect(self._refresh_toolbar_states)
        self._content_stack.addWidget(self._canvas)       # index 0

        self._metadata_tab = MetadataTab()
        self._content_stack.addWidget(self._metadata_tab) # index 1

        self._content_stack.setCurrentIndex(0)
        layout.addWidget(self._content_stack, stretch=1)

        self._status_separator = QFrame()
        self._status_separator.setObjectName("statusSeparator")
        self._status_separator.setFixedHeight(1)
        self._status_separator.setStyleSheet("background: #d0d0d0;")
        layout.addWidget(self._status_separator)

        self._status_bar = StatusBar()
        layout.addWidget(self._status_bar)

        return panel

    def show_update_banner(self, latest: str) -> None:
        """Affiche le bandeau 'nouvelle version disponible' sous la tab bar."""
        if self._update_banner is not None:
            return  # déjà affiché

        from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
        import webbrowser
        from modules.qt.localization import _
        from modules.qt.state import get_current_theme
        from modules.qt.font_manager_qt import get_current_font as _gcf

        self._update_banner_latest = latest

        banner = QWidget()
        banner.setFixedHeight(36)
        row = QHBoxLayout(banner)
        row.setContentsMargins(12, 0, 8, 0)
        row.setSpacing(8)

        lbl = QLabel()
        row.addWidget(lbl)
        row.addStretch()

        dl_btn = QPushButton()
        dl_btn.setCursor(Qt.PointingHandCursor)
        dl_btn.clicked.connect(
            lambda: webbrowser.open("https://github.com/Bruno-Aublet/MosaicView/releases/latest")
        )
        row.addWidget(dl_btn)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self._close_update_banner)
        row.addWidget(close_btn)

        self._update_banner         = banner
        self._banner_lbl            = lbl
        self._banner_dl_btn         = dl_btn
        self._banner_close_btn      = close_btn

        # Insérer après la tab_bar (index 2 dans le layout)
        self._center_layout.insertWidget(2, banner)

        # Appliquer thème + police + langue
        self._retranslate_banner()

        # Langue à la volée
        from modules.qt.language_signal import language_signal
        self._banner_lang_handler = lambda _: self._retranslate_banner()
        language_signal.changed.connect(self._banner_lang_handler)

    def _retranslate_banner(self) -> None:
        """Met à jour thème, police et textes du bandeau."""
        if self._update_banner is None:
            return
        from modules.qt.localization import _
        from modules.qt.state import get_current_theme
        from modules.qt.font_manager_qt import get_current_font as _gcf

        theme  = get_current_theme()
        font   = _gcf(10)
        latest = self._update_banner_latest

        # Couleurs : vert foncé en mode clair, vert plus doux en mode sombre
        dark = getattr(self._state, "dark_mode", False)
        bg_color  = "#1a5c1a" if dark else "#2a7a2a"
        txt_color = "#ffffff"
        btn_bg    = "#ffffff" if not dark else "#e0ffe0"
        btn_fg    = "#1a5c1a" if dark else "#2a7a2a"
        btn_hover = "#ccffcc"

        self._update_banner.setStyleSheet(
            f"QWidget {{ background: {bg_color}; }}"
        )
        self._banner_lbl.setFont(font)
        self._banner_lbl.setStyleSheet(f"color: {txt_color}; background: transparent;")
        self._banner_lbl.setText(_("updates.banner_message").replace("{latest}", latest))

        self._banner_dl_btn.setFont(font)
        self._banner_dl_btn.setText(_("updates.download"))
        self._banner_dl_btn.setStyleSheet(
            f"QPushButton {{ background: {btn_bg}; color: {btn_fg}; border: none; "
            f"padding: 3px 12px; border-radius: 3px; }}"
            f"QPushButton:hover {{ background: {btn_hover}; }}"
        )

        self._banner_close_btn.setFont(font)
        self._banner_close_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {txt_color}; border: none; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,40); border-radius: 3px; }}"
        )

    def _close_update_banner(self) -> None:
        if self._update_banner is None:
            return
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._banner_lang_handler)
        except (RuntimeError, AttributeError):
            pass
        self._update_banner.deleteLater()
        self._update_banner = None

    # ──────────────────────────────────────────────────────────────────────────
    # Méthodes déléguées à MainWindow (globales)
    # ──────────────────────────────────────────────────────────────────────────

    def _get_config(self):
        return get_config_manager()

    def isFullScreen(self) -> bool:
        """Délègue à la MainWindow — le plein écran est global."""
        return self._main_window.isFullScreen()

    def close(self):
        """Délègue la fermeture de fenêtre à MainWindow."""
        self._main_window.close()

    # ──────────────────────────────────────────────────────────────────────────
    # Callbacks menubar
    # ──────────────────────────────────────────────────────────────────────────

    def _build_menubar_callbacks(self) -> dict:
        from modules.qt import state as _state_module
        raw = _build_menubar_callbacks_module(self)
        # Wrapper : chaque callable redirige le singleton vers self._state
        # avant exécution, et le restaure après.
        panel_state = self._state
        mw = self._main_window
        def _wrap(fn):
            def _wrapped(*a, **kw):
                _prev = _state_module.state
                _state_module.state = panel_state
                try:
                    return fn(*a, **kw)
                finally:
                    # Ne restaurer _prev que si c'est un état valide (panel actif).
                    # Si _prev appartient à un panel2 détruit, remettre panel_state.
                    try:
                        active_states = {id(p._state) for p in mw._all_panels()}
                    except AttributeError:
                        active_states = {id(panel_state)}
                    if id(_prev) in active_states:
                        _state_module.state = _prev
                    else:
                        _state_module.state = panel_state
            return _wrapped
        wrapped = {k: (_wrap(v) if callable(v) else v) for k, v in raw.items()}
        # Injecte la version disponible si une mise à jour a été détectée
        latest = getattr(self._main_window, "_update_latest", None)
        if latest:
            wrapped["_update_latest"] = latest
        return wrapped

    # ──────────────────────────────────────────────────────────────────────────
    # Opérations fichier
    # ──────────────────────────────────────────────────────────────────────────

    def _file_op_callbacks(self) -> dict:
        return {
            "render_mosaic":               self._canvas.render_mosaic,
            "update_button_text":          self._update_save_button,
            "update_tabs":                 self._refresh_tabs,
            "update_window_title":         self._refresh_title,
            "renumber_btn_action":         self._renumber_pages_auto,
            "safe_delete_file":            self._safe_delete_file,
            "get_mosaicview_temp_dir":     self._get_temp_dir,
        }

    def _safe_delete_file(self, filepath: str):
        try:
            from send2trash import send2trash
            send2trash(filepath)
        except ImportError:
            os.remove(filepath)

    def _get_temp_dir(self) -> str:
        return os.path.realpath(tempfile.gettempdir())

    def _update_save_button(self):
        pass

    def _refresh_tabs(self):
        if hasattr(self, "_tab_bar"):
            self._tab_bar.update(close_callback=self._close_file, state=self._state)

    def _save_as_cbz(self):
        _qt_save_as_cbz(self, self._canvas, self._file_op_callbacks())
        self._canvas.render_mosaic()

    def _save_selection_as_cbz(self):
        _qt_save_selection_as_cbz(self, self._file_op_callbacks())

    def _save_selection_to_folder(self):
        _qt_save_selection_to_folder(self, self._file_op_callbacks())

    def _create_cbz_from_images(self):
        _qt_create_cbz_from_images(self, self._canvas, self._file_op_callbacks())
        self._canvas.render_mosaic()

    def _apply_new_names(self):
        result = _qt_apply_new_names(self, self._canvas, self._file_op_callbacks())
        self._canvas.render_mosaic()
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Conversions par lot (batch)
    # ──────────────────────────────────────────────────────────────────────────

    def _get_batch_callbacks(self) -> dict:
        from modules.qt.archive_loader import _natural_sort_key
        from modules.qt.entries import create_centered_thumbnail
        from modules.qt import renumbering as _renumbering_module
        return {
            "natural_sort_key":          lambda s: _natural_sort_key(s),
            "create_centered_thumbnail": create_centered_thumbnail,
            "safe_delete_file":          self._safe_delete_file,
            "get_mosaicview_temp_dir":   self._get_temp_dir,
            "compute_auto_multipliers":  _renumbering_module.compute_auto_multipliers,
            "generate_auto_filenames":   _renumbering_module.generate_auto_filenames,
            "state":                     self._state,
        }

    def _batch_convert_cbr_to_cbz(self):
        _qt_batch_cbr(self, self._get_batch_callbacks())

    def _batch_convert_cb7_to_cbz(self):
        _qt_batch_cb7(self, self._get_batch_callbacks())

    def _batch_convert_cbt_to_cbz(self):
        _qt_batch_cbt(self, self._get_batch_callbacks())

    def _batch_convert_pdf_to_cbz(self):
        _qt_batch_pdf(self, self._get_batch_callbacks())

    def _batch_convert_img_to_cbz(self):
        _qt_batch_img(self, self._get_batch_callbacks())

    # ──────────────────────────────────────────────────────────────────────────
    # Langue / thème / police — délégués à MainWindow (globaux)
    # ──────────────────────────────────────────────────────────────────────────

    def _on_language_change(self, lang_code: str):
        self._main_window._on_language_change(lang_code)

    def _toggle_theme(self):
        self._main_window._toggle_theme()

    def _toggle_fullscreen(self):
        self._main_window._toggle_fullscreen()

    def _toggle_split_ui(self):
        self._main_window._toggle_split_ui()

    @property
    def _split_active(self):
        return getattr(self._main_window, "_split_active", False)

    def _decrease_font_size(self):
        self._main_window._decrease_font_size()

    def _increase_font_size(self):
        self._main_window._increase_font_size()

    def _reload_ui_fonts(self):
        """Reconstruit la menubar et re-render le canvas après changement de police."""
        build_menubar(self, self._build_menubar_callbacks(), self._menubar)
        self._canvas.render_mosaic()
        if hasattr(self._canvas, "_overlay_tip"):
            self._canvas._overlay_tip.update_font()
        if hasattr(self, "_icon_toolbar") and hasattr(self._icon_toolbar, "_overlay_tip"):
            self._icon_toolbar._overlay_tip.update_font()
        self._metadata_tab.apply_theme()

    def apply_separator_theme(self):
        from modules.qt.state import state
        dark = state.dark_mode if state and hasattr(state, "dark_mode") else False
        line = "#444444" if dark else "#d0d0d0"
        self._status_separator.setStyleSheet(f"background: {line};")

    def _reset_to_defaults(self):
        self._main_window._reset_to_defaults()

    def _show_user_guide(self):
        self._main_window._show_user_guide()

    def _show_license_dialog(self):
        self._main_window._show_license_dialog()

    def _show_full_gpl_license(self):
        self._main_window._show_full_gpl_license()

    def _show_full_unrar_license(self):
        self._main_window._show_full_unrar_license()

    def _show_full_7zip_license(self):
        self._main_window._show_full_7zip_license()

    def _show_full_piqad_license(self):
        self._main_window._show_full_piqad_license()

    def _show_full_tengwar_license(self):
        self._main_window._show_full_tengwar_license()

    def _show_donation_dialog(self):
        self._main_window._show_donation_dialog()

    def _copy_mail_address(self):
        self._main_window._copy_mail_address()

    # ──────────────────────────────────────────────────────────────────────────
    # Onglets
    # ──────────────────────────────────────────────────────────────────────────

    def _on_tab_changed(self, tab: str):
        if tab == "mosaic":
            self._content_stack.setCurrentIndex(0)
            if self._canvas._items:
                self._canvas.setFocus()
                if self._canvas._focused_idx is None:
                    self._canvas._set_focus(0)
                    self._canvas._scroll_to(self._canvas._items[0])
        elif tab == "info":
            if not self._metadata_tab._field_widgets and not self._metadata_tab._toggle_btn:
                self._metadata_tab.refresh()
            self._content_stack.setCurrentIndex(1)
            self._metadata_tab.setFocus()

    def _update_tabs(self):
        self._tab_bar.update(close_callback=self._close_file, state=self._state)

    # ──────────────────────────────────────────────────────────────────────────
    # Ouverture / fermeture de fichier
    # ──────────────────────────────────────────────────────────────────────────

    def _open_file_dialog(self):
        cfg = get_config_manager()
        st  = self._state
        if st.current_file:
            initial_dir = os.path.dirname(os.path.abspath(st.current_file))
        else:
            initial_dir = cfg.get('last_open_dir', "")
        paths, _filter = QFileDialog.getOpenFileNames(
            self,
            _("dialogs.open_file.title"),
            initial_dir,
            f"{_('dialogs.open_file.filter_archives')};;{_('dialogs.open_file.filter_all')}",
        )
        if paths:
            cfg.set('last_open_dir', os.path.dirname(os.path.abspath(paths[0])))
            self._load_files(paths)

    def _start_image_load(self, files: list, already_open: bool, image_exts: tuple, st):
        from modules.qt.archive_loader import _natural_sort_key
        from modules.qt.canvas_overlay_qt import show_canvas_text as _show_ct, hide_canvas_text as _hide_ct
        from modules.qt.web_import_qt import _show_cancel_item

        # Si un worker tourne déjà, on l'annule
        if self._image_worker is not None:
            self._image_worker._cancelled.set()
            self._image_worker = None

        first_image_dir = os.path.dirname(os.path.abspath(files[0])) if not st.images_data else None

        def _show(pct):
            if self._image_worker is None:
                return
            _show_ct(self._canvas, _("labels.loading", percent=pct), self._image_overlay_holder)
            main_item = self._image_overlay_holder[0]
            from shiboken6 import isValid
            offset_y = 40
            _show_cancel_item(self._canvas, f"[ {_('buttons.cancel')} ]",
                              self._image_cancel_holder, _cancel,
                              anchor_lbl=self._image_overlay_holder[0])

        def _hide():
            self._canvas._loading = False
            _hide_ct(self._canvas, self._image_overlay_holder)
            _hide_ct(self._canvas, self._image_cancel_holder)

        def _cancel():
            if self._image_worker is None:
                return
            self._image_worker._cancelled.set()
            self._image_worker = None
            _hide()
            self._canvas.render_mosaic()

        worker = _ImageLoadWorker(files, already_open, image_exts)
        self._image_worker = worker

        def on_progress(pct):
            _show(pct)

        def on_finished(new_entries):
            self._image_worker = None
            _hide()
            if new_entries:
                if first_image_dir and not st.images_data:
                    st.first_image_dir = first_image_dir
                st.images_data.extend(new_entries)
                st.images_data.sort(key=lambda e: _natural_sort_key(e["orig_name"]))
                st.all_entries = list(st.images_data)
                st.modified = True
                st.selected_indices.clear()
                from modules.qt.undo_redo import save_state_data
                save_state_data(st)
            self._canvas.render_mosaic()
            self._on_loading_finished()

        def on_cancelled():
            self._image_worker = None
            _hide()
            self._canvas.render_mosaic()

        def _cleanup():
            worker.deleteLater()

        worker.progress.connect(on_progress)
        worker.finished.connect(on_finished)
        worker.cancelled.connect(on_cancelled)
        worker.finished.connect(lambda _: _cleanup())
        worker.cancelled.connect(_cleanup)

        self._canvas._loading = True
        # Effacer le texte d'accueil qui est déjà dans la scène
        for it in self._canvas._empty_items:
            from shiboken6 import isValid
            if isValid(it) and it.scene() is self._canvas.scene():
                self._canvas.scene().removeItem(it)
        self._canvas._empty_items.clear()
        _show(0)
        worker.start()

    def _render_mosaic_invalidating(self):
        from modules.qt.mosaic_canvas import invalidate_pixmap_cache
        invalidate_pixmap_cache()
        self._canvas.render_mosaic()

    def _file_close_args(self) -> dict:
        return dict(
            canvas=self._canvas,
            create_cbz_cb=self._create_cbz_from_images,
            apply_new_names_cb=self._apply_new_names,
            refresh_title=self._refresh_title,
            refresh_toolbar=self._refresh_toolbar_states,
            refresh_tabs=lambda: (self._content_stack.setCurrentIndex(0), self._update_tabs()),
            refresh_status=self._update_status_bar,
            refresh_menubar=self._rebuild_menubar,
        )

    def _rebuild_menubar(self):
        build_menubar(self, self._build_menubar_callbacks(), self._menubar)

    def _close_file(self):
        from modules.qt.file_close_qt import close_file
        from modules.qt import state as _state_module
        _prev = _state_module.state
        _state_module.state = self._state
        close_file(self, **self._file_close_args())
        _state_module.state = _prev

    def _load_files(self, paths: list, from_drop: bool = False):
        from modules.qt.archive_loader import _natural_sort_key

        IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
                      '.tiff', '.tif', '.ico', '.jfif', '.pjpeg', '.pjp')
        ANNEX_EXTS = ('.nfo', '.txt', '.xml')

        st = self._state
        already_open = st.current_file is not None or bool(st.images_data)

        cbz_files   = [p for p in paths if os.path.splitext(p)[1].lower() in ('.cbz', '.cbr', '.cb7', '.cbt', '.epub')]
        pdf_files   = [p for p in paths if os.path.splitext(p)[1].lower() == '.pdf']
        image_files = [p for p in paths if os.path.splitext(p)[1].lower() in IMAGE_EXTS]
        annex_files = [p for p in paths if os.path.splitext(p)[1].lower() in ANNEX_EXTS]
        other_files = [p for p in paths if p not in cbz_files and p not in pdf_files
                       and p not in image_files and p not in annex_files]

        if other_files:
            names = "\n".join(os.path.basename(p) for p in other_files)
            _WarnDialog(
                self,
                "messages.errors.unsupported_files.title",
                "messages.errors.unsupported_files.message",
                message_kwargs={"files": names},
            ).exec()

        if pdf_files:
            if already_open:
                self._show_dpi_dialog_for_merge(pdf_files[0])
            else:
                reset_history(st)
                st.current_file = pdf_files[0]
                self._pdf_loader.load(pdf_files[0])

        elif cbz_files:
            if already_open:
                for fp in sorted(cbz_files, key=lambda f: _natural_sort_key(os.path.basename(f))):
                    self._import_merge_archive(fp)
            else:
                reset_history(st)
                cbz_sorted = sorted(cbz_files, key=lambda f: _natural_sort_key(os.path.basename(f)))
                st.current_file = cbz_sorted[0]
                self._loader.load(cbz_sorted)

        if image_files or annex_files:
            self._start_image_load(image_files + annex_files, already_open, IMAGE_EXTS, st)

    def _show_dpi_dialog_for_merge(self, filepath: str):
        from modules.qt.pdf_loading_qt import DpiDialog, import_and_merge_pdf
        dlg = DpiDialog(self, "dialogs.pdf.merge_quality_title", "dialogs.pdf.quality_merge")
        if dlg.exec() != DpiDialog.Accepted or dlg.selected_dpi is None:
            return
        import_and_merge_pdf(filepath, dlg.selected_dpi, self, self._canvas, self._state)

    def _import_merge_archive(self, filepath: str):
        from modules.qt.import_merge_qt import import_and_merge_archive
        import_and_merge_archive(filepath, self, self._canvas, self._state)

    def _open_recent_file(self, filepath: str):
        if os.path.exists(filepath):
            self._load_files([filepath])
        else:
            _recent_files_module.remove_from_recent_files(filepath)
            self._main_window._sync_recent_menus()
            _WarnDialog(
                self,
                "messages.errors.file_not_found.title",
                "messages.errors.file_not_found.message",
                message_kwargs={"path": filepath},
            ).exec()

    def _clear_recent_files(self):
        _recent_files_module.clear_recent_files()
        self._main_window._sync_recent_menus()
        _WarnDialog(
            self,
            "messages.info.history_cleared.title",
            "messages.info.history_cleared.message",
        ).exec()

    def _guide_or_self(self):
        from modules.qt import user_guide_qt as _guide_mod
        w = _guide_mod._help_window_ref
        if w is not None and w.isVisible():
            return w
        return self

    def _clear_temp_files_with_message(self):
        from modules.qt.temp_files import cleanup_all_temp_files
        cleanup_all_temp_files()
        _WarnDialog(
            self._guide_or_self(),
            "messages.info.history_cleared.title",
            "help.config_temp_cleared",
        ).exec()

    def _clear_config_file(self):
        try:
            config_path = get_config_manager().get_config_file_path()
            if os.path.exists(config_path):
                self._safe_delete_file(config_path)
        except Exception as e:
            print(f"Erreur suppression fichier de configuration : {e}")
        _WarnDialog(
            self._guide_or_self(),
            "messages.info.history_cleared.title",
            "help.config_config_cleared",
        ).exec()

    def _clear_clipboard_files(self):
        try:
            temp_dir = os.path.join(os.path.realpath(tempfile.gettempdir()), "MosaicViewTemp")
            if os.path.exists(temp_dir):
                for item in os.listdir(temp_dir):
                    if item.startswith("clipboard_"):
                        item_path = os.path.join(temp_dir, item)
                        try:
                            if os.path.isdir(item_path):
                                shutil.rmtree(item_path)
                        except Exception:
                            pass
        except Exception as e:
            print(f"Erreur suppression fichiers presse-papiers : {e}")
        _WarnDialog(
            self._guide_or_self(),
            "messages.info.history_cleared.title",
            "help.config_clipboard_cleared",
        ).exec()

    def _open_temp_folder(self):
        temp_dir = os.path.join(os.path.realpath(tempfile.gettempdir()), "MosaicViewTemp")
        try:
            subprocess.Popen(["explorer", temp_dir])
        except Exception as e:
            print(f"Erreur ouverture dossier temporaire : {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # Renumérotation
    # ──────────────────────────────────────────────────────────────────────────

    def _has_subdirectory_structure(self) -> bool:
        return any(
            '/' in e.get("orig_name", "") and not e.get("is_dir")
            for e in self._state.images_data
        )

    def _renumber_pages_auto(self):
        from modules.qt.renumbering_qt import renumber_pages_auto_qt
        from modules.qt import state as _state_module
        if self._has_subdirectory_structure():
            self._warn_flatten_required_renumber()
            return
        _prev = _state_module.state
        _state_module.state = self._state
        try:
            renumber_pages_auto_qt(self, self._canvas.render_mosaic, save_state_func=self.save_state)
        finally:
            _state_module.state = _prev
        self._canvas.status_changed.emit()

    def _renumber_pages(self):
        from modules.qt.renumbering_qt import renumber_pages_qt
        from modules.qt import state as _state_module
        if self._has_subdirectory_structure():
            self._warn_flatten_required_renumber()
            return
        _prev = _state_module.state
        _state_module.state = self._state
        try:
            renumber_pages_qt(self._canvas.render_mosaic, save_state_func=self.save_state)
        finally:
            _state_module.state = _prev
        self._canvas.status_changed.emit()

    def _renumber_no_save(self):
        from modules.qt import renumbering as _renumbering_module
        from modules.qt.renumbering_qt import show_first_page_dialog_qt
        from modules.qt import state as _state_module
        noop = lambda: None
        _prev = _state_module.state
        _state_module.state = self._state
        try:
            if getattr(self._state, "renumber_mode", 1) == 1:
                original = _renumbering_module.show_first_page_dialog
                def _qt_dialog(first_entry, first_mult, total_logical_pages, callbacks):
                    return show_first_page_dialog_qt(self, first_entry, first_mult, total_logical_pages)
                _renumbering_module.show_first_page_dialog = _qt_dialog
                try:
                    _renumbering_module.renumber_pages_auto({
                        "save_state":         noop,
                        "render_mosaic":      noop,
                        "update_button_text": noop,
                        "root":               self,
                    })
                finally:
                    _renumbering_module.show_first_page_dialog = original
            else:
                _renumbering_module.renumber_pages({
                    "save_state":         noop,
                    "render_mosaic":      noop,
                    "update_button_text": noop,
                })
        finally:
            _state_module.state = _prev

    def _renumber_btn_action(self):
        if getattr(self._state, "renumber_mode", 1) == 1:
            self._renumber_pages_auto()
        else:
            self._renumber_pages()

    def _toggle_renumber_mode(self):
        current = getattr(self._state, "renumber_mode", 1)
        self._state.renumber_mode = 2 if current == 1 else 1

    def _sort_images(self, sort_method: str):
        from modules.qt.sorting_qt import sort_images_qt
        sort_images_qt(sort_method, self.save_state, self._render_mosaic, self._refresh_toolbar_states, self._state)

    def _show_sort_menu(self, event=None):
        from modules.qt.sorting_qt import show_sort_menu_qt
        show_sort_menu_qt(self, self._sort_images)

    def _flatten_directories(self):
        from modules.qt.flatten_directories_qt import flatten_directories_qt
        from modules.qt import state as _state_module
        _prev = _state_module.state
        _state_module.state = self._state
        try:
            flatten_directories_qt(
                self,
                self._canvas.render_mosaic,
                self._icon_toolbar.refresh_states,
                self._canvas.status_changed,
                save_state_func=self.save_state,
            )
        finally:
            _state_module.state = _prev

    def _warn_flatten_required_renumber(self):
        _WarnDialog(
            self,
            "messages.warnings.renumber_disabled_in_subdirectory.title",
            "messages.warnings.renumber_disabled_in_subdirectory.message",
        ).exec()

    def _warn_flatten_required_dnd(self):
        _WarnDialog(
            self,
            "messages.warnings.drag_drop_disabled_in_subdirectory.title",
            "messages.warnings.drag_drop_disabled_in_subdirectory.message",
        ).exec()

    # ──────────────────────────────────────────────────────────────────────────
    # Undo / Redo
    # ──────────────────────────────────────────────────────────────────────────

    def _undo_redo_callbacks(self) -> tuple:
        return (
            self._canvas.render_mosaic,
            self._canvas._clear_selection,
            self._update_tabs,
            self._refresh_toolbar_states,
        )

    def save_state(self, force=False):
        _save_state_qt(self._state, self._refresh_toolbar_states, force=force)

    def _render_mosaic(self):
        """render_mosaic avec le singleton pointé sur self._state (pour _tw/_th etc.)."""
        from modules.qt import state as _state_module
        _prev = _state_module.state
        _state_module.state = self._state
        try:
            self._canvas.render_mosaic()
        finally:
            _state_module.state = _prev

    def _split_page_callbacks(self) -> dict:
        return {
            "save_state":         self.save_state,
            "render_mosaic":      self._render_mosaic,
            "update_button_text": self._refresh_toolbar_states,
            "state":              self._state,
        }

    def _ico_callbacks(self) -> dict:
        return {
            "render_mosaic":   self._render_mosaic,
            "refresh_toolbar": self._refresh_toolbar_states,
            "state":           self._state,
        }

    def _conversion_callbacks(self) -> dict:
        from modules.qt.conversion_dialogs_qt import (
            show_quality_dialog as _show_quality_dialog_qt,
            perform_conversion  as _perform_conversion,
        )
        from modules.qt.animated_gif_dialog_qt import show_animated_gif_dialog as _show_gif_dlg
        return {
            "perform_conversion":       lambda fmt, quality, entries: _perform_conversion(self, fmt, quality, entries, self._conversion_callbacks()),
            "show_quality_dialog":      lambda fmt, entries: _show_quality_dialog_qt(self, fmt, entries, self._conversion_callbacks()),
            "show_animated_gif_dialog": lambda entries: _show_gif_dlg(entries, self._animated_gif_callbacks()),
            "render_mosaic":            self._render_mosaic,
            "update_button_text":       self._refresh_toolbar_states,
            "save_state":               self.save_state,
            "state":                    self._state,
        }

    def _handle_dropped_web_urls(self, urls: list) -> None:
        from modules.qt.web_import_qt import _resolve_and_download
        for url in urls:
            _resolve_and_download(self._canvas, url, self._web_import_callbacks())

    def _web_import_callbacks(self) -> dict:
        return {
            "state":                    self._state,
            "save_state":               self.save_state,
            "render_mosaic":            self._render_mosaic,
            "update_button_text":       self._refresh_toolbar_states,
            "update_create_cbz_button": self._refresh_toolbar_states,
            "clear_selection":          self._canvas._clear_selection_and_emit,
        }

    def _animated_gif_callbacks(self) -> dict:
        return {
            "parent":             self,
            "save_state":         self.save_state,
            "render_mosaic":      self._render_mosaic,
            "update_button_text": self._refresh_toolbar_states,
        }

    def _image_viewer_callbacks(self) -> dict:
        return {
            "undo_action":        self._undo_action,
            "redo_action":        self._redo_action,
            "save_state":         self.save_state,
            "render_mosaic":      self._render_mosaic,
            "update_button_text": self._refresh_toolbar_states,
            "state":              self._state,
            "canvas":             self._canvas,
        }

    def _open_image_viewer(self, idx: int):
        _open_image_viewer_qt(self, idx, self._image_viewer_callbacks())

    def _crop_selected_image(self):
        state = self._state
        if not state.selected_indices:
            _WarnDialog(self, "messages.warnings.no_selection_crop.title",
                        "messages.warnings.no_selection_crop.message").exec()
            return
        if len(state.selected_indices) > 1:
            _WarnDialog(self, "messages.warnings.multi_selection_crop.title",
                        "messages.warnings.multi_selection_crop.message").exec()
            return
        idx = list(state.selected_indices)[0]
        entry = state.images_data[idx]
        if not entry["is_image"]:
            _WarnDialog(self, "messages.warnings.invalid_selection_crop.title",
                        "messages.warnings.invalid_selection_crop.message").exec()
            return
        self._open_image_viewer(idx)

    def _image_transforms_callbacks(self) -> dict:
        return {
            "save_state":         self.save_state,
            "render_mosaic":      self._render_mosaic,
            "update_button_text": self._refresh_toolbar_states,
            "refresh_status":     self._update_status_bar,
            "canvas":             self._canvas,
            "state":              self._state,
            "rollback":           lambda: _rollback_to_current_state_qt(self._state, *self._undo_redo_callbacks()),
        }

    def _resize_callbacks(self) -> dict:
        return {
            "save_state":         self.save_state,
            "render_mosaic":      self._render_mosaic,
            "update_button_text": self._refresh_toolbar_states,
            "refresh_status":     self._update_status_bar,
            "canvas":             self._canvas,
            "state":              self._state,
        }

    def _adjustments_callbacks(self) -> dict:
        from modules.qt import state as _state_module
        panel_state = self._state
        def _with_state(fn):
            def _wrapped(*a, **kw):
                _prev = _state_module.state
                _state_module.state = panel_state
                try:
                    return fn(*a, **kw)
                finally:
                    _state_module.state = _prev
            return _wrapped
        return {
            "save_state":         _with_state(self.save_state),
            "render_mosaic":      _with_state(self._canvas.render_mosaic),
            "update_button_text": self._refresh_toolbar_states,
            "canvas":             self._canvas,
            "state":              panel_state,
        }

    def _straighten_callbacks(self) -> dict:
        from modules.qt import state as _state_module
        panel_state = self._state
        def _with_state(fn):
            def _wrapped(*a, **kw):
                _prev = _state_module.state
                _state_module.state = panel_state
                try:
                    return fn(*a, **kw)
                finally:
                    _state_module.state = _prev
            return _wrapped
        return {
            "save_state":         _with_state(self.save_state),
            "render_mosaic":      _with_state(self._canvas.render_mosaic),
            "update_button_text": self._refresh_toolbar_states,
            "state":              panel_state,
        }

    def _clone_zone_callbacks(self) -> dict:
        from modules.qt import state as _state_module
        panel_state = self._state
        def _with_state(fn):
            def _wrapped(*a, **kw):
                _prev = _state_module.state
                _state_module.state = panel_state
                try:
                    return fn(*a, **kw)
                finally:
                    _state_module.state = _prev
            return _wrapped
        return {
            "save_state":         _with_state(self.save_state),
            "render_mosaic":      _with_state(self._canvas.render_mosaic),
            "update_button_text": self._refresh_toolbar_states,
            "state":              panel_state,
        }

    def _text_viewer_callbacks(self) -> dict:
        from modules.qt import state as _state_module
        panel_state = self._state
        def _with_state(fn):
            def _wrapped(*a, **kw):
                _prev = _state_module.state
                _state_module.state = panel_state
                try:
                    return fn(*a, **kw)
                finally:
                    _state_module.state = _prev
            return _wrapped
        return {
            "save_state":         _with_state(self.save_state),
            "render_mosaic":      _with_state(self._canvas.render_mosaic),
            "update_button_text": self._refresh_toolbar_states,
            "state":              panel_state,
        }

    def _merge_callbacks(self) -> dict:
        return {
            "save_state":         self.save_state,
            "render_mosaic":      self._render_mosaic,
            "update_button_text": self._refresh_toolbar_states,
            "clear_selection":    self._canvas._clear_selection_and_emit,
            "state":              self._state,
        }

    def _undo_action(self):
        _undo_action_qt(self._state, *self._undo_redo_callbacks())

    def _redo_action(self):
        _redo_action_qt(self._state, *self._undo_redo_callbacks())

    def _delete_selected_qt(self):
        from modules.qt.comic_info import has_comic_info_entry
        from modules.qt.utils import format_file_size
        st = self._state
        if not st or not st.selected_indices:
            return

        count = len(st.selected_indices)
        total_size = sum(
            len(st.images_data[i]["bytes"])
            for i in st.selected_indices
            if i < len(st.images_data) and st.images_data[i].get("bytes")
        )
        size_str = format_file_size(total_size) if total_size > 0 else ""

        from modules.qt.file_close_qt import DeleteConfirmDialog
        dlg = DeleteConfirmDialog(self, count, size_str)
        if dlg.exec() != QDialog.Accepted:
            return

        _save_state_qt(st, self._refresh_toolbar_states)
        for idx in sorted(st.selected_indices, reverse=True):
            if idx < len(st.images_data):
                st.images_data.pop(idx)
        st.selected_indices.clear()
        st.modified = True
        from modules.qt.comic_info import sync_pages_in_xml_data
        sync_pages_in_xml_data(st)

        image_count = sum(1 for e in st.images_data if e.get("is_image", False))
        if image_count == 0:
            st.needs_renumbering = False

        has_xml = has_comic_info_entry(st)
        if not has_xml and st.comic_metadata:
            st.comic_metadata = None
            self._refresh_tabs()

        _save_state_qt(st, self._refresh_toolbar_states)
        self._canvas.render_mosaic()
        self._refresh_toolbar_states()

    def _replace_corrupted_image(self, idx: int):
        st = self._state
        if idx >= len(st.images_data):
            return
        entry = st.images_data[idx]
        if not entry.get("is_corrupted"):
            return

        if st.current_file:
            initial_dir = os.path.dirname(os.path.abspath(st.current_file))
        else:
            initial_dir = get_config_manager().get('last_open_dir', "")
        filepath, _filter = QFileDialog.getOpenFileName(
            self,
            _("dialogs.replace_corrupted_image.title"),
            initial_dir,
            f"{_('dialogs.replace_corrupted_image.filter_images')}"
            f";;{_('dialogs.replace_corrupted_image.filter_all')}",
        )
        if not filepath:
            return

        try:
            with open(filepath, "rb") as f:
                data = f.read()

            from PIL import Image as _PILImage
            import io as _io
            img = _PILImage.open(_io.BytesIO(data))
            img.verify()
            img = _PILImage.open(_io.BytesIO(data))

            _save_state_qt(st, self._refresh_toolbar_states, force=True)

            entry["bytes"]             = data
            entry["is_corrupted"]      = False
            entry["corruption_reason"] = None
            entry.pop("qt_pixmap_large", None)
            entry.pop("qt_qimage_large", None)

            st.modified = True

            _save_state_qt(st, self._refresh_toolbar_states, force=True)
            self._canvas.render_mosaic()
            self._refresh_toolbar_states()

        except Exception as e:
            from modules.qt.dialogs_qt import MsgDialog
            MsgDialog(
                self,
                "messages.errors.load_image_failed.title",
                "messages.errors.load_image_failed.message",
                {"error": str(e)},
            ).exec()

    # ──────────────────────────────────────────────────────────────────────────
    # Presse-papiers
    # ──────────────────────────────────────────────────────────────────────────

    def _copy_selected(self):
        from modules.qt.clipboard_qt import copy_to_system_clipboard
        copy_to_system_clipboard(self._get_temp_dir)

    def _copy_archive_to_clipboard(self):
        from modules.qt.clipboard_qt import copy_archive_to_clipboard
        copy_archive_to_clipboard(self._main_window)

    def _cut_selected(self):
        from modules.qt.clipboard_qt import cut_selected
        cut_selected(
            get_temp_dir_func=self._get_temp_dir,
            render_mosaic=self._canvas.render_mosaic,
            save_state=self.save_state,
        )
        self._refresh_toolbar_states()

    def _paste_ctrl_v(self):
        from modules.qt.clipboard_qt import paste_from_system_clipboard
        from modules.qt.archive_loader import _natural_sort_key
        paste_from_system_clipboard(
            parent=self,
            load_files_callback=self._load_files,
            save_state=self.save_state,
            render_mosaic=self._canvas.render_mosaic,
            clear_selection=self._canvas._clear_selection_and_emit,
            natural_sort_key=_natural_sort_key,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Fin de chargement
    # ──────────────────────────────────────────────────────────────────────────

    def _on_loading_finished(self):
        if self._state.current_file:
            _recent_files_module.add_to_recent_files(self._state.current_file)
            self._main_window._sync_recent_menus()
        from modules.qt.undo_redo import save_state_data
        save_state_data(self._state)
        self._refresh_title()
        self._update_tabs()
        self._update_status_bar()
        if hasattr(self, "_icon_toolbar"):
            self._icon_toolbar.refresh_states()

    # ──────────────────────────────────────────────────────────────────────────
    # Barre de statut / toolbar
    # ──────────────────────────────────────────────────────────────────────────

    def _update_status_bar(self):
        self._status_bar.refresh(self._state)

    def _refresh_toolbar_states(self):
        if hasattr(self, "_icon_toolbar"):
            self._icon_toolbar.refresh_states()

    def cleanup(self):
        """Déconnecte les signaux globaux avant destruction du panneau."""
        if hasattr(self, "_tab_bar"):
            self._tab_bar.cleanup()
        if hasattr(self, "_metadata_tab"):
            self._metadata_tab.cleanup()

    # ──────────────────────────────────────────────────────────────────────────
    # Drag & drop inter-panneaux
    # ──────────────────────────────────────────────────────────────────────────

    def _on_inter_panel_drop(self, dragged_reals: list, insert_real: int, source_id: str):
        """Appelé par le canvas cible quand des pages sont droppées depuis un autre panneau.
        Supprime les entrées du panneau source, les insère dans ce panneau à insert_real."""
        mw = self._main_window
        # Trouve le canvas source via son id Python
        source_panel = None
        for p in getattr(mw, "_all_panels", lambda: [])():
            if str(id(p._canvas)) == source_id:
                source_panel = p
                break
        if source_panel is None:
            return

        src_st  = source_panel._state
        dst_st  = self._state

        # Récupère les entrées depuis le panneau source et en fait des copies
        # indépendantes : chaque panneau possède ses propres données, la fermeture
        # du panneau source ne peut pas affecter les bytes du panneau destination.
        _UI_KEYS = ("name_entry", "ext_label",
                    "img_id", "text_id", "qt_pixmap_large", "qt_qimage_large")
        def _copy_entry(e):
            c = dict(e)
            if c.get("bytes") is not None:
                c["bytes"] = bytes(c["bytes"])  # copie indépendante des données brutes
            for k in _UI_KEYS:
                c[k] = None  # widgets liés à l'ancien panneau
            c["img"] = None  # PIL Image sera rechargée à la demande
            return c

        dragged_entries = [_copy_entry(src_st.images_data[i]) for i in dragged_reals
                           if i < len(src_st.images_data)]
        if not dragged_entries:
            return

        # ── Supprime du source ────────────────────────────────────────────────
        kept = [e for i, e in enumerate(src_st.images_data) if i not in set(dragged_reals)]
        src_st.images_data = kept
        src_st.selected_indices.clear()
        # Si le panneau source est maintenant vide et sans archive, pas besoin de
        # le marquer modifié (évite de bloquer la fermeture de l'appli)
        src_st.modified = True if (kept or src_st.current_file) else False
        from modules.qt.comic_info import sync_pages_in_xml_data
        sync_pages_in_xml_data(src_st, emit_signal=False)
        has_images = any(e.get("is_image") for e in dragged_entries)
        if has_images:
            source_panel._renumber_no_save()
        source_panel.save_state()
        source_panel._canvas.render_mosaic()

        # ── Insère dans la cible (self) ───────────────────────────────────────
        insert_real = max(0, min(insert_real, len(dst_st.images_data)))
        for offset, entry in enumerate(dragged_entries):
            dst_st.images_data.insert(insert_real + offset, entry)
        dst_st.modified = True
        sync_pages_in_xml_data(dst_st, emit_signal=False)
        if has_images:
            self._renumber_no_save()
        # Recalcule selected_indices APRÈS renumérotation, par identité objet
        dragged_ids = {id(e) for e in dragged_entries}
        dst_st.selected_indices = {
            idx for idx, e in enumerate(dst_st.images_data)
            if id(e) in dragged_ids
        }
        self.save_state()
        self._canvas.render_mosaic()

        # Mise à jour incrémentale des onglets métadonnées (pas de signal global)
        if src_st.comic_metadata and 'pages' in src_st.comic_metadata:
            source_panel._metadata_tab.update_pages(src_st.comic_metadata['pages'])
        if dst_st.comic_metadata and 'pages' in dst_st.comic_metadata:
            self._metadata_tab.update_pages(dst_st.comic_metadata['pages'])

    # ──────────────────────────────────────────────────────────────────────────
    # Panneau actif (bordure colorée en mode split)
    # ──────────────────────────────────────────────────────────────────────────

    def _set_active(self, active: bool):
        """Délégué — la bordure est gérée par le QFrame wrapper dans MosaicView."""
        pass

    # ──────────────────────────────────────────────────────────────────────────
    # Drag & drop entrant
    # ──────────────────────────────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-mosaicview-indices"):
            return
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasFormat("application/x-mosaicview-indices"):
            return
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        if paths:
            self._handle_dropped_paths(paths, from_drop=True)

    def _handle_dropped_paths(self, paths: list, from_drop: bool = False):
        from modules.qt.drop_handler_qt import handle_dropped_paths
        from modules.qt.web_import_qt import _resolve_and_download

        regular_paths = []
        for p in paths:
            ext = os.path.splitext(p)[1].lower()
            if ext == '.url':
                try:
                    cfg = configparser.ConfigParser()
                    cfg.read(p, encoding='utf-8')
                    url = cfg.get('InternetShortcut', 'URL', fallback=None)
                    if url and url.startswith(('http://', 'https://')):
                        _resolve_and_download(self._canvas, url, self._web_import_callbacks())
                        continue
                except Exception:
                    pass
            elif ext == '.webloc':
                try:
                    import plistlib
                    with open(p, 'rb') as f:
                        data = plistlib.load(f)
                    url = data.get('URL', '')
                    if url.startswith(('http://', 'https://')):
                        _resolve_and_download(self._canvas, url, self._web_import_callbacks())
                        continue
                except Exception:
                    pass
            regular_paths.append(p)

        if regular_paths:
            handle_dropped_paths(self, regular_paths, self._load_files,
                                 self._get_batch_callbacks(), from_drop=from_drop)

    # ──────────────────────────────────────────────────────────────────────────
    # Titre de fenêtre
    # ──────────────────────────────────────────────────────────────────────────

    def _refresh_title(self):
        from modules.qt.window_title_qt import update_window_title
        update_window_title(self._main_window, self._state)

    # ──────────────────────────────────────────────────────────────────────────
    # Taille des vignettes
    # ──────────────────────────────────────────────────────────────────────────

    def _init_thumb_size(self):
        cfg = get_config_manager()
        saved = cfg.get_thumbnail_size()
        size_names_to_index = {'small': 0, 'normal': 1, 'large': 2}
        index = size_names_to_index.get(saved, 1)
        self._apply_thumb_size(index, save=False)
        self._icon_toolbar.set_thumb_size_index(index)

    def _on_thumb_size_change(self, index: int):
        self._apply_thumb_size(index, save=True)

    def _apply_thumb_size(self, index: int, save: bool = True):
        st = self._state
        st.current_thumb_size = index
        st.thumb_w, st.thumb_h = THUMB_SIZES[index]
        st.padding_x = 15 if index == 0 else 5
        if save:
            size_names = ['small', 'normal', 'large']
            get_config_manager().set_thumbnail_size(size_names[index])
        if not getattr(st, 'loading_label', None):
            self._canvas.render_mosaic()

    def _decrease_thumb_size(self):
        st = self._state
        if st.current_thumb_size > 0:
            new_idx = st.current_thumb_size - 1
            self._apply_thumb_size(new_idx)
            self._icon_toolbar.set_thumb_size_index(new_idx)

    def _increase_thumb_size(self):
        st = self._state
        if st.current_thumb_size < 2:
            new_idx = st.current_thumb_size + 1
            self._apply_thumb_size(new_idx)
            self._icon_toolbar.set_thumb_size_index(new_idx)

    # ──────────────────────────────────────────────────────────────────────────
    # Sidebar
    # ──────────────────────────────────────────────────────────────────────────

    def _toggle_sidebar(self):
        self._sidebar_visible = not self._sidebar_visible
        self._left_panel.setVisible(self._sidebar_visible)
        update = getattr(self._menubar, "_update_sidebar_chevron", None)
        if update:
            update()
        mw = self._main_window
        if not getattr(mw, "_split_active", False) or getattr(mw, "_panel", None) is self:
            from modules.qt.session_restore_qt import save_sidebar_state
            save_sidebar_state(not self._sidebar_visible)
        elif getattr(mw, "_panel2", None) is self:
            get_config_manager().set_sidebar_collapsed_panel2(not self._sidebar_visible)

    # ──────────────────────────────────────────────────────────────────────────
    # Escape
    # ──────────────────────────────────────────────────────────────────────────

    def _on_escape(self):
        if self.isFullScreen():
            self._toggle_fullscreen()
        else:
            self._canvas._clear_selection_and_emit()
