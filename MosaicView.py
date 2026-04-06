"""
MosaicView — version PySide6
Point d'entrée principal (migration depuis tkinter)

Architecture :
  - MosaicView.py     : fenêtre principale, layout, wiring des modules
  - modules/qt/panel_widget.py : panneau autonome (toolbar + canvas + menubar + statusbar)
  - modules/qt/       : modules UI Qt (canvas, menubar, toolbar, dialogs…)
  - modules/          : modules logique métier inchangés (state, entries, localization…)
"""

__version__ = "1.1.1"

import sys
import os
import re

# ── Limite PIL ────────────────────────────────────────────────────────────────
from PIL import Image
Image.MAX_IMAGE_PIXELS = 500_000_000

# ── PySide6 ───────────────────────────────────────────────────────────────────
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QHBoxLayout, QSplitter, QSplitterHandle, QFrame, QVBoxLayout,
)
from PySide6.QtCore import QTimer, Qt, QObject, QEvent
from PySide6.QtGui import QIcon, QKeySequence, QShortcut, QPainter, QColor

# ── Modules logique métier ────────────────────────────────────────────────────
import zipfile, rarfile, io
import threading, time, json

# PyInstaller / UnRAR
if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
else:
    base_path = os.path.dirname(os.path.abspath(__file__))

unrar_path = os.path.join(base_path, 'unrar', 'UnRAR.exe')
if not os.path.exists(unrar_path):
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else base_path
    unrar_path = os.path.join(exe_dir, 'unrar', 'UnRAR.exe')
if os.path.exists(unrar_path):
    rarfile.UNRAR_TOOL = unrar_path
    try:
        rarfile.tool_setup(force=True)
    except rarfile.RarCannotExec:
        pass

# send2trash facultatif
try:
    from send2trash import send2trash
    SEND2TRASH_AVAILABLE = True
except ImportError:
    SEND2TRASH_AVAILABLE = False

from modules.qt.config_manager import init_config_manager, get_config_manager
from modules.qt.localization import init_localization, _
from modules.qt.font_loader import resource_path
from modules.qt.font_manager_qt import init_font_manager
from modules.qt.panel_widget import PanelWidget


# ═══════════════════════════════════════════════════════════════════════════════
class _EqualSplitterHandle(QSplitterHandle):
    """Double-clic sur le séparateur → remet les deux panneaux à 50/50."""
    def mouseDoubleClickEvent(self, event):
        splitter = self.splitter()
        total = sum(splitter.sizes())
        if total > 0:
            half = total // 2
            splitter.setSizes([half, total - half])
            splitter.splitterMoved.emit(half, 1)
        super().mouseDoubleClickEvent(event)


class _EqualSplitter(QSplitter):
    def createHandle(self):
        return _EqualSplitterHandle(self.orientation(), self)


# ═══════════════════════════════════════════════════════════════════════════════
class _PanelFrame(QFrame):
    """QFrame avec bordure dessinée via paintEvent — sans setStyleSheet,
    pour éviter la propagation coûteuse aux widgets enfants."""

    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.NoFrame)
        self._active = False
        self._color  = "#3a7bd5"

    def set_active(self, active: bool, color: str):
        self._active = active
        self._color  = color

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._active:
            painter = QPainter(self)
            painter.setPen(QColor(self._color))
            # 3px solid : dessine 3 rectangles emboîtés
            r = self.rect()
            for i in range(3):
                painter.drawRect(r.adjusted(i, i, -i - 1, -i - 1))


# Fenêtre principale
# ═══════════════════════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setVisible(False)

        self._app_ref       = None   # assigné par main() après construction
        self._update_latest = None   # version disponible si mise à jour détectée

        # ── Config & localisation ─────────────────────────────────────────────
        init_config_manager()
        self._loc          = init_localization()
        self._font_manager = init_font_manager()

        # ── Liste des langues ─────────────────────────────────────────────────
        _fictional = {'tlh', 'tlh-piqad', 'sjn', 'sjn-tengwar', 'qya', 'qya-tengwar'}
        _fictional_order = ['tlh', 'tlh-piqad', 'sjn', 'sjn-tengwar', 'qya', 'qya-tengwar']
        _lang_code_to_name = self._loc.get_available_languages()
        _real_languages = {k: v for k, v in _lang_code_to_name.items() if k not in _fictional}
        self._language_list = [(code, name, None) for code, name in _real_languages.items()]
        for code in _fictional_order:
            if code in _lang_code_to_name:
                if code == 'tlh-piqad':
                    font_name = self._font_manager.piqad_font_name
                elif code in ('sjn-tengwar', 'qya-tengwar'):
                    font_name = self._font_manager.tengwar_font_name
                else:
                    font_name = None
                self._language_list.append((code, _lang_code_to_name[code], font_name))

        # ── Titre + icône ─────────────────────────────────────────────────────
        self.setWindowTitle("MosaicView")
        cfg = get_config_manager()
        win_size = cfg.get_window_size()
        win_pos  = cfg.get_window_position()
        if win_pos:
            from PySide6.QtCore import QRect
            self.setGeometry(QRect(win_pos['x'], win_pos['y'], win_size['width'], win_size['height']))
        else:
            from PySide6.QtWidgets import QApplication as _QApp
            from PySide6.QtCore import QRect
            screen = _QApp.primaryScreen().availableGeometry()
            x = (screen.width()  - win_size['width'])  // 2
            y = max(0, (screen.height() - win_size['height']) // 2 - 40)
            self.setGeometry(QRect(x, y, win_size['width'], win_size['height']))
        ico_path = resource_path('icons/MosaicView.ico')
        if os.path.isfile(ico_path):
            self.setWindowIcon(QIcon(ico_path))

        # ── Widget central ────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        self._main_layout = QHBoxLayout(central)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        # ── QSplitter inter-panneaux ──────────────────────────────────────────
        self._panels_splitter = _EqualSplitter(Qt.Horizontal)
        self._panels_splitter.setChildrenCollapsible(False)
        self._panels_splitter.setHandleWidth(8)
        self._panels_splitter.setStyleSheet("""
            QSplitter::handle {
                background: #999999;
                border-left: 1px solid #666666;
                border-right: 1px solid #666666;
            }
            QSplitter::handle:hover {
                background: #3a7bd5;
            }
            QSplitter::handle:pressed {
                background: #2a5bb5;
            }
        """)
        self._main_layout.addWidget(self._panels_splitter)

        # ── Panneau 1 (toujours présent) ──────────────────────────────────────
        self._panel = PanelWidget(
            app_ref      = None,  # sera assigné après construction dans main()
            main_window  = self,
            language_list = self._language_list,
            loc          = self._loc,
            font_manager = self._font_manager,
        )
        self._frame1 = self._wrap_in_frame(self._panel)
        self._panels_splitter.addWidget(self._frame1)

        # ── Panneau 2 (null tant que split inactif) ───────────────────────────
        self._panel2: PanelWidget | None = None
        self._frame2: QFrame | None = None
        self._split_active = False
        self._active_panel = self._panel

        # ── Raccourcis clavier globaux ────────────────────────────────────────
        from PySide6.QtCore import Qt as _Qt
        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(lambda: self._active_panel._open_file_dialog())
        QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(lambda: self._active_panel._undo_action())
        QShortcut(QKeySequence("Ctrl+Y"), self).activated.connect(lambda: self._active_panel._redo_action())
        QShortcut(QKeySequence("Ctrl+C"), self).activated.connect(lambda: self._active_panel._copy_selected())
        QShortcut(QKeySequence("Ctrl+X"), self).activated.connect(lambda: self._active_panel._cut_selected())
        QShortcut(QKeySequence("Ctrl+V"), self).activated.connect(lambda: self._active_panel._paste_ctrl_v())
        f5 = QShortcut(QKeySequence("F5"), self)
        f5.setContext(_Qt.ApplicationShortcut)
        f5.activated.connect(lambda: self._active_panel._canvas.render_mosaic())
        QShortcut(QKeySequence("F11"),    self).activated.connect(self._toggle_fullscreen)
        QShortcut(QKeySequence("Escape"), self).activated.connect(lambda: self._active_panel._on_escape())

        # ── Désactive la native QMainWindow menubar ───────────────────────────
        self.menuBar().setVisible(False)

        # ── Drag & drop entrant ───────────────────────────────────────────────
        self.setAcceptDrops(True)

        # ── Restauration plein écran depuis config ────────────────────────────
        if get_config_manager().get_fullscreen():
            QTimer.singleShot(0, self._toggle_fullscreen)

        # ── Navigation TAB entre zones ────────────────────────────────────────
        from modules.qt.keyboard_nav_qt import ZoneTabNavigator
        self._tab_nav = ZoneTabNavigator(
            get_active_panel = lambda: self._active_panel,
            get_other_panel  = lambda: (self._panel2 if self._active_panel is self._panel else self._panel)
                                       if self._split_active else None,
            set_active_panel = self._set_active_panel,
        )
        QApplication.instance().installEventFilter(self)
        self._panel._menubar.installEventFilter(self)
        self.installEventFilter(self)

        # ── Restauration session (maximized / dark_mode / sidebar) ────────────
        from modules.qt.session_restore_qt import restore_session
        restore_session(self)

        # ── Restauration split depuis config ──────────────────────────────────
        if get_config_manager().get_split_active():
            QTimer.singleShot(0, self._open_split)

    # ──────────────────────────────────────────────────────────────────────────
    # Raccourcis vers le panneau actif (pour session_restore_qt et autres)
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def _state(self):
        return self._active_panel._state

    @property
    def _canvas(self):
        return self._active_panel._canvas

    @property
    def _left_panel(self):
        return self._active_panel._left_panel

    @property
    def _tab_bar(self):
        return self._active_panel._tab_bar

    @property
    def _icon_toolbar(self):
        return self._active_panel._icon_toolbar

    @property
    def _sidebar_visible(self):
        return self._active_panel._sidebar_visible

    @_sidebar_visible.setter
    def _sidebar_visible(self, value):
        self._active_panel._sidebar_visible = value

    @property
    def _splitter(self):
        return self._active_panel._splitter

    # ──────────────────────────────────────────────────────────────────────────
    # Actions globales (thème, langue, police, plein écran)
    # ──────────────────────────────────────────────────────────────────────────

    def _save_split_ratio(self):
        try:
            sizes = self._panels_splitter.sizes()
            if sum(sizes) > 0:
                get_config_manager().set_split_ratio(sizes[0] / sum(sizes))
        except Exception:
            pass

    def _all_panels(self):
        """Retourne la liste de tous les panneaux actifs."""
        panels = [self._panel]
        if self._panel2 is not None:
            panels.append(self._panel2)
        return panels

    def _sync_recent_menus(self):
        """Reconstruit la menubar de tous les panneaux après un changement de fichiers récents."""
        from modules.qt.menubar_qt import build_menubar
        for p in self._all_panels():
            build_menubar(p, p._build_menubar_callbacks(), p._menubar)

    def _on_language_change(self, lang_code: str):
        """Change la langue et met à jour l'UI de tous les panneaux."""
        if not self._loc.change_language(lang_code):
            return
        from modules.qt.menubar_qt import build_menubar
        from modules.qt.toggle_theme_qt import apply_theme
        for p in self._all_panels():
            build_menubar(p, p._build_menubar_callbacks(), p._menubar)
            p._refresh_title()
            p._update_status_bar()
            p._icon_toolbar.update_language()
            p._icon_toolbar.update_language_combo()
            apply_theme(self._app_ref, p._canvas, p._left_panel, p._tab_bar, render=False)
            p._metadata_tab.apply_theme()
            if not p._state.images_data:
                p._canvas.render_mosaic()
            else:
                p._canvas.update_name_fonts()
        from modules.qt import state as _state_module
        _state_module.state = self._active_panel._state
        from modules.qt.language_signal import language_signal
        language_signal.emit(lang_code)

    def _toggle_theme(self):
        from modules.qt import state as _state_module
        from modules.qt.toggle_theme_qt import toggle_theme, apply_theme
        # Bascule le thème une seule fois (via le panneau actif)
        toggle_theme(self._app_ref, self._active_panel._canvas,
                     self._active_panel._left_panel, self._active_panel._tab_bar)
        self._active_panel._metadata_tab.apply_theme()
        # Synchronise dark_mode sur tous les autres panneaux et applique le thème
        new_dark = self._active_panel._state.dark_mode
        for p in self._all_panels():
            if p is self._active_panel:
                continue
            p._state.dark_mode = new_dark
            _state_module.state = p._state
            apply_theme(self._app_ref, p._canvas, p._left_panel, p._tab_bar)
            p._metadata_tab.apply_theme()
        _state_module.state = self._active_panel._state
        # Met à jour le bandeau de mise à jour si affiché
        for p in self._all_panels():
            p._retranslate_banner()
        # Met à jour la couleur de bordure après changement de thème
        if self._split_active:
            self._set_frame_active(self._frame1, self._active_panel is self._panel)
            if self._frame2:
                self._set_frame_active(self._frame2, self._active_panel is self._panel2)

    def _toggle_fullscreen(self):
        cfg = get_config_manager()
        if self.isFullScreen():
            self.showNormal()
            cfg.set_fullscreen(False)
        else:
            self.showFullScreen()
            cfg.set_fullscreen(True)

    def _toggle_sidebar(self):
        self._active_panel._toggle_sidebar()

    def _decrease_font_size(self):
        from modules.qt.state import MIN_FONT_SIZE_OFFSET
        cfg = get_config_manager()
        current = cfg.get_font_size_offset()
        if current <= MIN_FONT_SIZE_OFFSET:
            return
        cfg.set_font_size_offset(current - 1)
        for p in self._all_panels():
            p._reload_ui_fonts()
            p._retranslate_banner()

    def _increase_font_size(self):
        from modules.qt.state import MAX_FONT_SIZE_OFFSET
        cfg = get_config_manager()
        current = cfg.get_font_size_offset()
        if current >= MAX_FONT_SIZE_OFFSET:
            return
        cfg.set_font_size_offset(current + 1)
        for p in self._all_panels():
            p._reload_ui_fonts()
            p._retranslate_banner()

    def _update_splitter_constraints(self, size_index: int):
        for p in self._all_panels():
            p._update_splitter_constraints(size_index)

    def _reset_to_defaults(self):
        from modules.qt.session_restore_qt import reset_to_defaults
        reset_to_defaults(self)

    # ──────────────────────────────────────────────────────────────────────────
    # Split de l'interface
    # ──────────────────────────────────────────────────────────────────────────

    def _wrap_in_frame(self, panel: "PanelWidget") -> "_PanelFrame":
        """Enroule un PanelWidget dans un _PanelFrame pour gérer la bordure active."""
        frame = _PanelFrame()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(0)
        layout.addWidget(panel)
        return frame

    def _set_frame_active(self, frame: "_PanelFrame", active: bool):
        """Applique ou retire la bordure colorée sur le frame d'un panneau."""
        dark = getattr(self._active_panel._state, "dark_mode", False)
        color = "#5a9bf5" if dark else "#3a7bd5"
        frame.set_active(active, color)
        frame.update()

    def _toggle_split_ui(self):
        if self._split_active:
            self._close_split()
        else:
            self._open_split()

    def _open_split(self):
        if self._split_active:
            return
        self._panel2 = PanelWidget(
            app_ref      = self._app_ref,
            main_window  = self,
            language_list = self._language_list,
            loc          = self._loc,
            font_manager = self._font_manager,
            is_primary   = False,
        )
        self._frame2 = self._wrap_in_frame(self._panel2)
        self._panels_splitter.addWidget(self._frame2)
        self._panels_splitter.setStretchFactor(0, 1)
        self._panels_splitter.setStretchFactor(1, 1)

        # Synchronise dark_mode du nouveau panneau depuis la config (source de vérité au démarrage)
        self._panel2._state.dark_mode = get_config_manager().get_dark_mode()
        from modules.qt.toggle_theme_qt import apply_theme
        from modules.qt import state as _state_module
        _state_module.state = self._panel2._state
        apply_theme(self._app_ref, self._panel2._canvas, self._panel2._left_panel, self._panel2._tab_bar)
        _state_module.state = self._panel._state

        # Restaure le ratio depuis la config — différé pour que la fenêtre ait sa taille finale
        cfg = get_config_manager()
        ratio = cfg.get_split_ratio()
        saved_w2 = cfg.get_buttons_column_width_panel2()
        def _apply_ratio():
            total = self._panels_splitter.width()
            if total > 0:
                s1 = int(total * ratio)
                self._panels_splitter.setSizes([s1, total - s1])
            if saved_w2:
                p2 = self._panel2
                p2._update_splitter_constraints(p2._icon_toolbar._size_index)
                total2 = p2._splitter.width()
                p2._splitter.setSizes([saved_w2, max(0, total2 - saved_w2)])
                p2._icon_toolbar.adapt_cols_to_width(saved_w2)
        QTimer.singleShot(50, _apply_ratio)

        # Sauvegarde le ratio à chaque déplacement du séparateur
        self._panels_splitter.splitterMoved.connect(self._save_split_ratio)

        self._split_active = True
        self._active_panel = self._panel
        self._set_frame_active(self._frame1, True)
        self._set_frame_active(self._frame2, False)

        # Clic sur l'un ou l'autre frame → désigne le panneau actif
        self._frame1.mousePressEvent = lambda _e: self._set_active_panel(self._panel)
        self._frame2.mousePressEvent = lambda _e: self._set_active_panel(self._panel2)

        # Disposition des icônes panel2 : restaurer depuis config panel2 si elle existe,
        # sinon hériter de panel1 (première ouverture)
        tb1 = self._panel._icon_toolbar
        tb2 = self._panel2._icon_toolbar
        if cfg.get_icon_toolbar_layout_panel2() is None:
            tb2.apply_layout(tb1._layout, tb1._show_thumb_slider, tb1._show_lang_combo)
        # Taille : hériter de panel1 seulement si panel2 n'a jamais été configuré
        # (icon_size_index_panel2 == 0 par défaut, ambigu → on hérite si panel1 ≠ 0)
        if cfg.get_icon_size_index_panel2() == 0 and tb1._size_index != 0:
            tb2.set_size_index(tb1._size_index)

        # Sidebar panel2 : restaurer état depuis config
        if cfg.get_sidebar_collapsed_panel2():
            self._panel2._sidebar_visible = False
            self._panel2._left_panel.setVisible(False)

        cfg.set_split_active(True)
        self._panel._refresh_toolbar_states()
        self._panel2._refresh_toolbar_states()
        self._panel._refresh_title()

    def _close_split(self):
        if not self._split_active or self._panel2 is None:
            return

        # Confirmation si panel2 a une archive ouverte
        st2 = self._panel2._state
        if st2.images_data or st2.modified:
            from modules.qt import state as _state_module
            from modules.qt.file_close_qt import close_file
            _prev_state = _state_module.state
            _state_module.state = st2
            closed = close_file(self, **self._panel2._file_close_args())
            _state_module.state = _prev_state
            if not closed:
                return  # L'utilisateur a annulé

        # Sauvegarde le ratio
        self._save_split_ratio()

        # Annule le chargement en cours dans panel2 (déconnecte les signaux)
        self._panel2._loader.cancel()

        # Redirige le wheel hook vers le combo de panel1 avant de détruire panel2
        from modules.qt.icon_toolbar_qt import _wheel_hook_singleton
        if _wheel_hook_singleton is not None:
            try:
                combo1 = self._panel._icon_toolbar._lang_combo._combo
                _wheel_hook_singleton._target = combo1
            except AttributeError:
                pass

        # Déconnecte les signaux globaux de panel2 avant destruction
        self._panel2.cleanup()

        # Supprime frame2 + panel2
        self._frame2.setParent(None)
        self._frame2.deleteLater()
        self._frame2 = None
        self._panel2 = None
        self._split_active = False
        self._active_panel = self._panel
        self._set_frame_active(self._frame1, False)

        # Retire les overrides mousePressEvent du frame1
        try:
            del self._frame1.mousePressEvent
        except AttributeError:
            pass

        get_config_manager().set_split_active(False)
        self._panel._refresh_toolbar_states()
        self._panel._refresh_title()

    def _set_active_panel(self, panel: "PanelWidget"):
        if self._active_panel is panel:
            return
        from modules.qt import state as _state_module
        self._active_panel = panel
        _state_module.state = panel._state
        self._update_active_frames(panel)

    # ──────────────────────────────────────────────────────────────────────────
    # Fenêtres globales (guide, licences)
    # ──────────────────────────────────────────────────────────────────────────

    def _show_license_dialog(self):
        from modules.qt.license_dialog_qt import show_license_dialog_qt
        show_license_dialog_qt(self)

    def show_update_banner(self, latest: str) -> None:
        """Affiche le bandeau de mise à jour sur tous les panneaux actifs."""
        self._update_latest = latest
        for p in self._all_panels():
            p.show_update_banner(latest)

    def set_update_available_in_menu(self, latest: str) -> None:
        """Stocke la version disponible et reconstruit la menubar."""
        self._update_latest = latest
        from modules.qt.menubar_qt import build_menubar
        for p in self._all_panels():
            build_menubar(p, p._build_menubar_callbacks(), p._menubar)

    def _all_panels(self):
        panels = [self._panel]
        if self._panel2 is not None:
            panels.append(self._panel2)
        return panels

    def _show_donation_dialog(self):
        from modules.qt.donation_dialog_qt import show_donation_dialog_qt
        show_donation_dialog_qt(self)

    def _copy_mail_address(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText("mosaicview1969@gmail.com")

    def _show_full_gpl_license(self):
        from modules.qt.license_dialog_qt import show_full_license_window_qt
        show_full_license_window_qt(self)

    def _show_full_unrar_license(self):
        from modules.qt.license_dialog_qt import show_full_unrar_license_window_qt
        show_full_unrar_license_window_qt(self)

    def _show_full_7zip_license(self):
        from modules.qt.license_dialog_qt import show_full_7zip_license_window_qt
        show_full_7zip_license_window_qt(self)

    def _show_full_piqad_license(self):
        from modules.qt.license_dialog_qt import show_full_piqad_license_window_qt
        show_full_piqad_license_window_qt(self)

    def _show_full_tengwar_license(self):
        from modules.qt.license_dialog_qt import show_full_tengwar_license_window_qt
        show_full_tengwar_license_window_qt(self)

    def _show_user_guide(self):
        from modules.qt.user_guide_qt import (
            show_user_guide, export_piqad_font, export_tengwar_fonts, save_all_icons,
        )
        show_user_guide(self, {
            "export_piqad_font":              lambda: export_piqad_font(self),
            "export_tengwar_fonts":           lambda: export_tengwar_fonts(self),
            "clear_temp_files_with_message":  self._active_panel._clear_temp_files_with_message,
            "clear_recent_files":             self._active_panel._clear_recent_files,
            "clear_config_file":              self._active_panel._clear_config_file,
            "clear_clipboard_files":          self._active_panel._clear_clipboard_files,
            "save_all_icons":                 lambda: save_all_icons(self),
        })

    # ──────────────────────────────────────────────────────────────────────────
    # Drag & drop entrant (au niveau fenêtre)
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
            self._active_panel._handle_dropped_paths(paths, from_drop=True)

    # ──────────────────────────────────────────────────────────────────────────
    # Fermeture — sauvegarde session
    # ──────────────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if getattr(self, '_close_event_handled', False):
            event.accept()
            return
        self._close_event_handled = True

        from modules.qt.file_close_qt import on_window_close
        from modules.qt.session_restore_qt import save_session
        from modules.qt.temp_files import cleanup_all_temp_files

        # Sauvegarde le ratio du splitter inter-panneaux si split actif
        if self._split_active:
            self._save_split_ratio()

        from modules.qt import state as _state_module

        # Si panel2 est ouvert, annule son loader avant tout
        if self._split_active and self._panel2 is not None:
            self._panel2._loader.cancel()

        # Si panel2 est ouvert et a un fichier → le fermer en priorité
        if self._split_active and self._panel2 is not None:
            st2 = self._panel2._state
            # modified seul sans images ni archive = rien à sauvegarder (ex: toutes les
            # images ont été draguées vers panel1) → on skip panel2 directement
            _panel2_needs_close = bool(st2.images_data) or (st2.modified and st2.current_file)
            if _panel2_needs_close:
                _prev = _state_module.state
                _state_module.state = st2
                try:
                    on_window_close(
                        main_window=self,
                        save_session_cb=lambda: None,
                        cleanup_temp_cb=lambda: None,
                        **self._panel2._file_close_args(),
                    )
                finally:
                    _state_module.state = _prev
                # closed=False signifie soit annulé soit fichier fermé → dans les 2 cas on ignore
                self._close_event_handled = False  # permet au prochain clic de retraiter
                event.ignore()
                return

        _state_module.state = self._panel._state
        can_close = on_window_close(
            main_window=self,
            save_session_cb=lambda: save_session(self),
            cleanup_temp_cb=lambda: cleanup_all_temp_files(keep_logs=True),
            **self._panel._file_close_args(),
        )
        if can_close:
            event.accept()
        else:
            self._close_event_handled = False
            event.ignore()

    # ──────────────────────────────────────────────────────────────────────────
    # Navigation TAB entre zones
    # ──────────────────────────────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent, QTimer
        if (self._split_active
                and event.type() == QEvent.MouseButtonPress):
            # Détermine quel panneau contient le widget qui reçoit le clic
            widget = obj if hasattr(obj, 'isWidgetType') and obj.isWidgetType() else None
            if widget is not None:
                if self._frame2 and self._frame2.isAncestorOf(widget):
                    target = self._panel2
                elif self._frame1.isAncestorOf(widget):
                    target = self._panel
                else:
                    target = None
                if target is not None and target is not self._active_panel:
                    # Met à jour le singleton immédiatement (pour que le callback
                    # déclenché sur mouseRelease trouve le bon state)
                    from modules.qt import state as _state_module
                    self._active_panel = target
                    _state_module.state = target._state
                    # Diffère la mise à jour visuelle (bordures) pour ne pas
                    # perturber la livraison du mouseReleaseEvent
                    QTimer.singleShot(0, lambda t=target: self._update_active_frames(t))
        return self._tab_nav.key_filter(obj, event)

    def _update_active_frames(self, panel):
        self._set_frame_active(self._frame1, panel is self._panel)
        if self._frame2:
            self._set_frame_active(self._frame2, panel is self._panel2)

    def focusNextPrevChild(self, next_: bool) -> bool:
        return self._tab_nav.focus_next_prev(next_)


# ═══════════════════════════════════════════════════════════════════════════════
# Point d'entrée
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    import os
    os.environ["QT_LOGGING_RULES"] = "qt.text.font.db=false"

    app = QApplication(sys.argv)
    app.setApplicationName("MosaicView")
    app.setStyle("Fusion")

    app.setStyleSheet("""
        QMainWindow { background: #f5f5f5; }
        #leftPanel  { background: #e0e0e0; border-right: 1px solid #c0c0c0; }
        QToolTip {
            background-color: #ffffe0;
            color: #000000;
            border: 1px solid #aaaaaa;
            padding: 3px 5px;
            font-size: 9pt;
        }
        QMenu {
            background-color: #e0e0e0;
            color: #000000;
            border: 1px solid #aaaaaa;
        }
        QMenu::item:selected {
            background-color: #3a7bd5;
            color: #ffffff;
        }
        QMenu::item:disabled {
            color: #999999;
        }
        QMenu::separator {
            height: 1px;
            background: #aaaaaa;
            margin: 2px 4px;
        }
        QPushButton:focus {
            border: 2px solid #888888;
        }
        QCheckBox:focus {
            border: 1px solid #888888;
        }
        QRadioButton:focus {
            border: 1px solid #888888;
        }
        QLineEdit:focus {
            border: 2px solid #888888;
        }
        QComboBox:focus {
            border: 2px solid #888888;
        }
        QSpinBox:focus {
            border: 2px solid #888888;
        }
        QSlider:focus {
            border: 1px solid #888888;
        }
    """)

    _splash = None
    try:
        import pyi_splash  # type: ignore[import]
        pyi_splash.close()
    except Exception:
        from PySide6.QtWidgets import QSplashScreen
        from PySide6.QtGui import QPixmap
        _splash_path = os.path.join(os.path.dirname(__file__), 'icons', 'splash.png')
        _splash_px   = QPixmap(_splash_path)
        if not _splash_px.isNull():
            _splash = QSplashScreen(_splash_px, Qt.WindowStaysOnTopHint)
            _splash.show()
            app.processEvents()

    from modules.qt.temp_files import cleanup_stale_mei_dirs
    cleanup_stale_mei_dirs()

    win = MainWindow()
    win._app_ref        = app
    win._panel._app_ref = app

    if _splash is not None:
        _splash.finish(win)

    # Vérification des mises à jour en arrière-plan
    from modules.qt.update_checker_qt import check_for_updates_on_startup
    check_for_updates_on_startup(win)

    # Préchauffage du process fitz en arrière-plan — élimine le délai au 1er PDF
    from modules.qt.pdf_loading_qt import warmup_pdf_process, shutdown_pdf_process
    warmup_pdf_process()
    app.aboutToQuit.connect(shutdown_pdf_process)

    # ── Focus initial ──────────────────────────────────────────────────────
    if win._panel._sidebar_visible:
        first = win._panel._icon_toolbar.get_first_icon() if win._panel._icon_toolbar else None
        if first:
            first.setFocus()
    else:
        actions = win._panel._menubar.actions()
        if len(actions) > 1:
            from PySide6.QtCore import QCoreApplication
            from PySide6.QtGui import QKeyEvent
            win._panel._menubar.setFocus()
            win._panel._menubar.setActiveAction(actions[1])
            mb  = win._panel._menubar
            act = actions[1]
            def _close_menu():
                fake_esc = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_Escape, Qt.NoModifier)
                QCoreApplication.sendEvent(mb, fake_esc)
                mb.setFocus()
                mb.setActiveAction(act)
                QCoreApplication.sendEvent(mb, fake_esc)
            QTimer.singleShot(0, _close_menu)

    sys.exit(app.exec())


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()  # requis pour PyInstaller + spawn
    main()
