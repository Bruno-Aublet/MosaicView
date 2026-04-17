"""
user_guide_qt.py — Fenêtre de mode d'emploi (version PySide6)

Comportement :
  - Non-modale, une instance par panneau
  - Sections collapsibles (état persistant par session)
  - Scrollable avec molette souris
  - Texte sélectionnable, liens cliquables
  - Boutons d'action (exporter polices, vider fichiers temporaires, etc.)
  - Mise à jour à la volée sans recréation si changement de langue
"""

import os
import re
import shutil
import tempfile
import subprocess

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QFrame, QSizePolicy, QTextBrowser,
    QFileDialog, QApplication,
)
from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QDesktopServices, QIcon

from modules.qt.localization import _, _wt
from modules.qt.font_loader import resource_path, PIQAD_FONT_FILE, TENGWAR_FONT_FILES
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.dialogs_qt import ErrorDialog
from modules.qt.config_manager import get_config_manager


# ── Registre des fenêtres ouvertes (une par panneau) ─────────────────────────
_help_windows: dict = {}  # id(panel) → _HelpDialog
_active_child_reopen = None


def register_child_reopen(reopen_func):
    global _active_child_reopen
    _active_child_reopen = reopen_func


def unregister_child_reopen():
    global _active_child_reopen
    _active_child_reopen = None


# ── État collapse/expand des sections (persistant par session) ────────────────
_section_collapsed_state: dict[int, bool] = {}


# ═══════════════════════════════════════════════════════════════════════════════
# Widgets helpers
# ═══════════════════════════════════════════════════════════════════════════════

class _SelectableText(QTextBrowser):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.anchorClicked.connect(lambda url: QDesktopServices.openUrl(url))
        self.setReadOnly(True)
        self.setFrameShape(QFrame.NoFrame)
        from modules.qt.utils import setup_text_browser_context_menu
        setup_text_browser_context_menu(self)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.document().setDocumentMargin(0)
        self.setContentsMargins(0, 0, 0, 0)
        self._apply_theme()
        self.setPlainText(text)
        self._adjust_height()
        self.document().contentsChanged.connect(self._adjust_height)

    def _apply_theme(self):
        theme = get_current_theme()
        link_color = theme.get("link", "#0066cc")
        self.setStyleSheet(
            f"QTextBrowser {{ background: transparent; color: {theme['text']}; "
            f"border: none; padding: 0px; }} "
            f"a {{ color: {link_color}; }}"
        )
        self.setFont(_get_current_font(10))

    def retranslate(self, text: str):
        self._apply_theme()
        self.setPlainText(text)

    def _adjust_height(self):
        doc_h = int(self.document().size().height()) + 4
        self.setFixedHeight(max(doc_h, 20))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_height()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self.verticalScrollBar().setValue(0)

    def wheelEvent(self, event):
        event.ignore()


class _LinkText(QTextBrowser):
    def __init__(self, html: str, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._on_anchor_clicked)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.NoFrame)
        from modules.qt.utils import setup_text_browser_context_menu
        setup_text_browser_context_menu(self)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.document().setDocumentMargin(0)
        self.setContentsMargins(0, 0, 0, 0)
        self._apply_theme()
        self.setHtml(html)
        self._adjust_height()
        self.document().contentsChanged.connect(self._adjust_height)

    def _apply_theme(self):
        theme = get_current_theme()
        link_color = theme.get("link", "#0066cc")
        self.setStyleSheet(
            f"QTextBrowser {{ background: transparent; color: {theme['text']}; "
            f"border: none; padding: 0px; }} "
            f"a {{ color: {link_color}; }}"
        )
        self.setFont(_get_current_font(10))

    def retranslate(self, html: str):
        self._apply_theme()
        self.setHtml(html)

    def _on_anchor_clicked(self, url: QUrl):
        if url.isLocalFile():
            path = os.path.realpath(url.toLocalFile())
            try:
                if os.path.isdir(path):
                    subprocess.Popen(["explorer", path])
                else:
                    subprocess.Popen(["explorer", "/select,", path])
            except Exception as e:
                print(f"Erreur ouverture explorateur : {e}")
        else:
            QDesktopServices.openUrl(url)

    def _adjust_height(self):
        doc_h = int(self.document().size().height()) + 4
        self.setFixedHeight(max(doc_h, 20))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_height()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self.verticalScrollBar().setValue(0)

    def wheelEvent(self, event):
        event.ignore()


def _make_action_button(parent, text: str, callback) -> QPushButton:
    theme = get_current_theme()
    btn = QPushButton(text, parent)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFont(_get_current_font(10))
    btn.setStyleSheet(
        f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
        f"border: 1px solid #aaaaaa; padding: 3px 10px; }} "
        f"QPushButton:hover {{ background: {theme['separator']}; }}"
    )
    btn.clicked.connect(callback)
    return btn


# ═══════════════════════════════════════════════════════════════════════════════
# Section collapsible
# ═══════════════════════════════════════════════════════════════════════════════

class _CollapsibleSection(QWidget):
    def __init__(self, title_key: str, section_idx: int, parent=None):
        super().__init__(parent)
        self._idx = section_idx
        self._title_key = title_key
        self._collapsed = _section_collapsed_state.get(section_idx, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(0)

        self._btn = QPushButton()
        self._btn.setFlat(True)
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.clicked.connect(self._toggle)
        layout.addWidget(self._btn)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(4)
        layout.addWidget(self._content)

        self._content.setVisible(not self._collapsed)
        self._apply_theme()

    def _apply_theme(self):
        theme = get_current_theme()
        self._btn.setStyleSheet(
            f"QPushButton {{ background: {theme['bg']}; color: {theme['text']}; "
            f"text-align: left; padding: 4px 10px; font-weight: bold; }} "
            f"QPushButton:hover {{ background: {theme['toolbar_bg']}; }}"
        )
        font = _get_current_font(12)
        font.setBold(True)
        self._btn.setFont(font)
        self._update_btn_text()

    def _update_btn_text(self):
        arrow = "\u25ba" if self._collapsed else "\u25bc"
        self._btn.setText(f"{arrow}  {_(self._title_key)}")

    def retranslate(self):
        self._apply_theme()

    def _toggle(self):
        self._collapsed = not self._collapsed
        _section_collapsed_state[self._idx] = self._collapsed
        self._content.setVisible(not self._collapsed)
        self._update_btn_text()

    def add_widget(self, widget: QWidget):
        self._content_layout.addWidget(widget)

    def add_button(self, text: str, callback) -> QPushButton:
        btn = _make_action_button(self._content, text, callback)
        self._content_layout.addWidget(btn, alignment=Qt.AlignLeft)
        self._content_layout.setContentsMargins(20, 0, 20, 10)
        return btn

    def add_buttons_row(self, buttons: list) -> QWidget:
        row = QWidget(self._content)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(20, 0, 20, 10)
        row_layout.setSpacing(6)
        row_layout.setAlignment(Qt.AlignLeft)
        for text, cb in buttons:
            btn = _make_action_button(row, text, cb)
            row_layout.addWidget(btn)
        self._content_layout.addWidget(row)
        return row


# ═══════════════════════════════════════════════════════════════════════════════
# Fonctions d'export (polices, icônes)
# ═══════════════════════════════════════════════════════════════════════════════

def _show_success_dialog(parent, title: str, message: str, folder_path: str, first_file: str):
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.resize(500, 150)

    ico_path = resource_path("icons/MosaicView.ico")
    if os.path.exists(ico_path):
        dlg.setWindowIcon(QIcon(ico_path))

    theme = get_current_theme()
    dlg.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(20, 15, 20, 15)
    layout.setSpacing(8)

    msg_lbl = QLabel(message)
    msg_lbl.setStyleSheet(f"color: {theme['text']};")
    msg_lbl.setWordWrap(True)
    msg_lbl.setAlignment(Qt.AlignCenter)
    layout.addWidget(msg_lbl)

    folder_norm = os.path.abspath(folder_path)
    link_lbl = QLabel(f'<a href="explorer://{first_file}" style="color:#0000cc;">{folder_norm}</a>')
    link_lbl.setOpenExternalLinks(False)
    link_lbl.setStyleSheet("color: #0000cc; text-decoration: underline;")
    link_lbl.setCursor(Qt.PointingHandCursor)
    link_lbl.setAlignment(Qt.AlignCenter)

    def _open_explorer():
        try:
            subprocess.run(f'explorer /select,"{os.path.abspath(first_file)}"', shell=True)
        except Exception as ex:
            print(f"Erreur ouverture explorateur: {ex}")

    link_lbl.mousePressEvent = lambda e: _open_explorer()
    layout.addWidget(link_lbl)

    btn_ok = QPushButton(_("buttons.ok"))
    btn_ok.setCursor(Qt.PointingHandCursor)
    btn_ok.clicked.connect(dlg.accept)
    layout.addWidget(btn_ok, alignment=Qt.AlignHCenter)

    if parent:
        pg = parent.geometry()
        dlg.move(
            pg.x() + (pg.width()  - dlg.width())  // 2,
            pg.y() + (pg.height() - dlg.height()) // 2,
        )
    dlg.exec()


def export_piqad_font(parent_widget):
    cfg = get_config_manager()
    initial = os.path.join(cfg.get('last_open_dir', ""), PIQAD_FONT_FILE)
    save_path, _filt = QFileDialog.getSaveFileName(
        parent_widget,
        _("help.language_export_piqad"),
        initial,
        "TrueType Font (*.ttf);;All files (*.*)",
    )
    if not save_path:
        return
    cfg.set('last_open_dir', os.path.dirname(os.path.abspath(save_path)))
    font_source = resource_path(os.path.join("fonts", PIQAD_FONT_FILE))
    if not os.path.exists(font_source):
        ErrorDialog(parent_widget, _("messages.errors.file_not_found.title"),
                    _("messages.errors.font_source_not_found", path=font_source)).exec()
        return
    try:
        shutil.copy2(font_source, save_path)
        filename = os.path.basename(save_path)
        _show_success_dialog(
            parent_widget,
            _("help.language_export_piqad"),
            _("help.language_export_piqad_success").format(filename=filename),
            os.path.dirname(save_path),
            save_path,
        )
    except Exception as e:
        ErrorDialog(parent_widget, _("messages.errors.file_not_found.title"),
                    _("messages.errors.export_error", error=e)).exec()


def export_tengwar_fonts(parent_widget):
    cfg = get_config_manager()
    initial = os.path.join(cfg.get('last_open_dir', ""), TENGWAR_FONT_FILES[0])
    save_path, _filt = QFileDialog.getSaveFileName(
        parent_widget,
        _("help.language_export_tengwar"),
        initial,
        "TrueType Font (*.ttf);;All files (*.*)",
    )
    if not save_path:
        return
    cfg.set('last_open_dir', os.path.dirname(os.path.abspath(save_path)))
    save_dir = os.path.dirname(save_path)
    copied_count = 0
    first_file = None
    for font_file in TENGWAR_FONT_FILES:
        font_source = resource_path(os.path.join("fonts", font_file))
        if os.path.exists(font_source):
            dest = os.path.join(save_dir, font_file)
            try:
                shutil.copy2(font_source, dest)
                if copied_count == 0:
                    first_file = dest
                copied_count += 1
            except Exception as e:
                print(f"Erreur copie {font_file}: {e}")
    if copied_count > 0:
        _show_success_dialog(
            parent_widget,
            _("help.language_export_tengwar"),
            _("help.language_export_tengwar_success").format(count=copied_count),
            save_dir,
            first_file,
        )
    else:
        ErrorDialog(parent_widget, _("messages.errors.file_not_found.title"),
                    _("messages.errors.no_tengwar_font")).exec()


def save_all_icons(parent_widget):
    cfg = get_config_manager()
    save_dir = QFileDialog.getExistingDirectory(
        parent_widget,
        _("help.icons_save_all"),
        cfg.get('last_open_dir', ""),
    )
    if not save_dir:
        return
    cfg.set('last_open_dir', save_dir)
    icons_dir = resource_path("icons")
    icon_files = []
    if os.path.exists(icons_dir):
        for f in os.listdir(icons_dir):
            if f.lower().endswith(".png"):
                icon_files.append(("icons", f))
    ico_path = resource_path("icons/MosaicView.ico")
    if os.path.exists(ico_path):
        icon_files.append(("icons", "MosaicView.ico"))

    copied_count = 0
    first_file = None
    for folder, filename in icon_files:
        source = resource_path(os.path.join(folder, filename))
        if os.path.exists(source):
            dest = os.path.join(save_dir, filename)
            try:
                shutil.copy2(source, dest)
                if copied_count == 0:
                    first_file = dest
                copied_count += 1
            except Exception as e:
                print(f"Erreur copie {filename}: {e}")
    if copied_count > 0:
        _show_success_dialog(
            parent_widget,
            _("help.icons_save_all"),
            _("help.icons_saved_success").format(count=copied_count),
            save_dir,
            first_file,
        )
    else:
        ErrorDialog(parent_widget, _("messages.errors.file_not_found.title"),
                    _("messages.errors.no_icons_found")).exec()


# ═══════════════════════════════════════════════════════════════════════════════
# Fenêtre principale du guide
# ═══════════════════════════════════════════════════════════════════════════════

class _HelpDialog(QDialog):

    def __init__(self, parent_widget, callbacks: dict):
        super().__init__(parent_widget)
        self._parent_widget = parent_widget
        self._callbacks = callbacks

        self.setWindowTitle(_wt("help.title"))
        self.resize(680, 600)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setWindowFlags(self.windowFlags() | Qt.Window)

        ico_path = resource_path("icons/MosaicView.ico")
        if os.path.exists(ico_path):
            self.setWindowIcon(QIcon(ico_path))

        self._build_ui()
        self._apply_theme()

        # Langue
        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    # ── Construction de l'UI (une seule fois) ─────────────────────────────────

    def _build_ui(self):
        theme = get_current_theme()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(6)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._scroll_content = QWidget()
        self._content_layout = QVBoxLayout(self._scroll_content)
        self._content_layout.setContentsMargins(10, 0, 10, 10)
        self._content_layout.setSpacing(0)
        self._content_layout.setAlignment(Qt.AlignTop)

        self._scroll.setWidget(self._scroll_content)
        main_layout.addWidget(self._scroll, stretch=1)

        # Titre
        self._title_lbl = QLabel()
        title_font = _get_current_font(16)
        title_font.setBold(True)
        self._title_lbl.setFont(title_font)
        self._title_lbl.setAlignment(Qt.AlignCenter)
        self._content_layout.addWidget(self._title_lbl)

        # Liste des sections : (title_key_or_"", content_key_or_SPECIAL)
        self._SECTIONS = [
            ("",                          "help.intro"),
            ("help.open_files",           "help.open_files_content"),
            ("help.mosaic",               "help.mosaic_content"),
            ("help.icon_toolbar",         "help.icon_toolbar_content"),
            ("help.manipulation",         "help.manipulation_content"),
            ("help.renumber",             "help.renumber_content"),
            ("help.flatten",              "help.flatten_content"),
            ("help.viewer",               "help.viewer_content"),
            ("help.crop",                 "help.crop_content"),
            ("help.resize_pages",         "help.resize_pages_content"),
            ("help.join_pages",           "help.join_pages_content"),
            ("help.split_pages",          "help.split_pages_content"),
            ("help.adjust_images",        "help.adjust_images_content"),
            ("help.create_icon",          "help.create_icon_content"),
            ("help.batch_convert",        "help.batch_convert_content"),
            ("help.shortcuts",            "help.shortcuts_content"),
            ("help.dark_mode",            "help.dark_mode_content"),
            ("help.sort",                 "help.sort_content"),
            ("help.save",                 "help.save_content"),
            ("help.other",                "help.other_content"),
            ("help.nfo_editor",           "help.nfo_editor_content"),
            ("help.language",             "LANGUAGE_SECTION"),
            ("help.config_files",         "CONFIG_SECTION"),
            ("help.icons",                "ICONS_SECTION"),
            ("help.split_ui",             "help.split_ui_content"),
            ("help.license_gpl",          "LICENSE_GPL_SECTION"),
            ("help.license_unrar",        "LICENSE_UNRAR_SECTION"),
            ("help.license_7zip",         "LICENSE_7ZIP_SECTION"),
            ("help.credits",              "help.credits_content"),
        ]

        # Widgets à mettre à jour lors du retranslate
        self._intro_widget: _SelectableText | None = None
        self._sections: list[_CollapsibleSection] = []
        # Par section spéciale : références aux sous-widgets
        self._section_widgets: dict = {}  # title_key → dict de widgets

        for idx, (title_key, content_key) in enumerate(self._SECTIONS):
            if not title_key:
                # Intro
                w = _SelectableText(_(content_key))
                w.setContentsMargins(20, 0, 20, 10)
                self._content_layout.addWidget(w)
                self._intro_widget = (w, content_key)
                continue

            section = _CollapsibleSection(title_key, idx)
            self._content_layout.addWidget(section)
            self._sections.append(section)
            sw = {}

            if content_key == "LANGUAGE_SECTION":
                sw = self._build_language_section(section)
            elif content_key == "CONFIG_SECTION":
                sw = self._build_config_section(section)
            elif content_key == "ICONS_SECTION":
                sw = self._build_icons_section(section)
            elif content_key == "LICENSE_GPL_SECTION":
                sw = self._build_license_section(section, "labels.license_text",
                                                  "labels.view_full_license",
                                                  self._open_full_gpl)
            elif content_key == "LICENSE_UNRAR_SECTION":
                sw = self._build_license_section(section, "labels.license_unrar_text",
                                                  "labels.view_full_unrar_license",
                                                  self._open_full_unrar)
            elif content_key == "LICENSE_7ZIP_SECTION":
                sw = self._build_license_section(section, "labels.license_7zip_text",
                                                  "labels.view_full_7zip_license",
                                                  self._open_full_7zip)
            else:
                pady = (5, 10) if title_key == "help.credits" else (0, 10)
                w = _SelectableText(_(content_key))
                w.setContentsMargins(20, pady[0], 20, pady[1])
                section.add_widget(w)
                sw = {"text": (w, content_key)}

            self._section_widgets[title_key] = sw

        # Bouton Fermer
        self._close_btn = QPushButton()
        self._close_btn.setCursor(Qt.PointingHandCursor)
        close_font = _get_current_font(10)
        close_font.setBold(True)
        self._close_btn.setFont(close_font)
        self._close_btn.clicked.connect(self.close)
        self._content_layout.addWidget(self._close_btn, alignment=Qt.AlignHCenter)

    # ── Builders de sections spéciales ────────────────────────────────────────

    def _build_language_section(self, section: _CollapsibleSection) -> dict:
        url_piqad   = "https://github.com/dadap/pIqaD-fonts"
        url_tengwar = "https://www.dafont.com/fr/tengwar-annatar.font"
        regular_w = italic_w = fonts_w = None
        buttons_row = None

        content_text = _("help.language_content")
        full = content_text.replace("{url_piqad}", url_piqad).replace("{url_tengwar}", url_tengwar)
        paragraphs = full.split("\n\n")
        regular, fonts_parts, italic_parts = [], [], []
        for para in paragraphs:
            if url_piqad in para or url_tengwar in para:
                fonts_parts.append(para)
            elif "Claude" in para:
                italic_parts.append(para)
            else:
                regular.append(para)

        if regular:
            regular_w = _SelectableText("\n\n".join(regular))
            regular_w.setContentsMargins(20, 0, 20, 5)
            section.add_widget(regular_w)

        if fonts_parts:
            html = _text_with_links_html("\n\n".join(fonts_parts), [url_piqad, url_tengwar])
            fonts_w = _LinkText(html)
            fonts_w.setContentsMargins(20, 0, 20, 5)
            section.add_widget(fonts_w)
            buttons_row = section.add_buttons_row([
                (_("help.language_export_piqad"),   self._callbacks.get("export_piqad_font",   lambda: None)),
                (_("help.language_export_tengwar"), self._callbacks.get("export_tengwar_fonts", lambda: None)),
            ])

        if italic_parts:
            italic_w = _SelectableText("\n\n".join(italic_parts))
            italic_w.setContentsMargins(20, 0, 20, 10)
            f = italic_w.font()
            f.setItalic(True)
            italic_w.setFont(f)
            section.add_widget(italic_w)

        return {
            "regular_w": regular_w,
            "fonts_w": fonts_w,
            "italic_w": italic_w,
            "buttons_row": buttons_row,
            "url_piqad": url_piqad,
            "url_tengwar": url_tengwar,
        }

    def _build_config_section(self, section: _CollapsibleSection) -> dict:
        temp_dir     = os.path.join(os.path.realpath(tempfile.gettempdir()), "MosaicViewTemp")
        config_fname = ".mosaicview_config.json"
        config_path  = os.path.join(temp_dir, config_fname)

        config_text = _("help.config_files_content").replace("{temp_dir}", temp_dir)
        html = _text_with_explorer_links_html(
            config_text,
            [(temp_dir, temp_dir), (config_fname, config_path)],
            None,
        )
        lw = _LinkText(html)
        lw.setContentsMargins(20, 0, 20, 5)
        section.add_widget(lw)

        btns_row = section.add_buttons_row([
            (_("help.config_clear_temp"),   self._callbacks.get("clear_temp_files_with_message", lambda: None)),
            (_("help.config_clear_recent"), self._callbacks.get("clear_recent_files",            lambda: None)),
            (_("help.config_clear_config"), self._callbacks.get("clear_config_file",             lambda: None)),
        ])

        clip_note = _SelectableText(_("help.config_clipboard_note"))
        clip_note.setContentsMargins(20, 10, 20, 0)
        section.add_widget(clip_note)

        clip_btn = section.add_button(
            _("help.config_clear_clipboard"),
            self._callbacks.get("clear_clipboard_files", lambda: None),
        )

        log_note = _SelectableText(_("help.config_log_note"))
        log_note.setContentsMargins(20, 10, 20, 10)
        section.add_widget(log_note)

        return {
            "lw": lw,
            "btns_row": btns_row,
            "clip_note": clip_note,
            "clip_btn": clip_btn,
            "log_note": log_note,
            "temp_dir": temp_dir,
            "config_fname": config_fname,
            "config_path": config_path,
        }

    def _build_icons_section(self, section: _CollapsibleSection) -> dict:
        url_icons = "https://www.freepik.com/author/juicy-fish/icons/juicy-fish-sketchy_908#from_element=resource_detail"
        html = _text_with_links_html(_("help.icons_content"), [url_icons])
        lw = _LinkText(html)
        lw.setContentsMargins(20, 0, 20, 5)
        section.add_widget(lw)
        btn = section.add_button(
            _("help.icons_save_all"),
            self._callbacks.get("save_all_icons", lambda: None),
        )
        return {"lw": lw, "btn": btn, "url_icons": url_icons}

    def _build_license_section(self, section, text_key, btn_key, open_func) -> dict:
        html = _text_with_angle_bracket_links_html(_(text_key))
        lw = _LinkText(html)
        lw.setContentsMargins(20, 0, 20, 5)
        section.add_widget(lw)

        btn = _make_action_button(section._content, _(btn_key), open_func)
        btn_font = btn.font()
        btn_font.setBold(True)
        btn.setFont(btn_font)
        section._content_layout.addWidget(btn, alignment=Qt.AlignLeft)
        section._content_layout.setContentsMargins(20, 0, 20, 10)
        return {"lw": lw, "btn": btn, "text_key": text_key, "btn_key": btn_key}

    # ── Callbacks licences ────────────────────────────────────────────────────

    def _open_full_gpl(self):
        from modules.qt.license_dialog_qt import show_full_license_window_qt
        register_child_reopen(self._open_full_gpl)
        show_full_license_window_qt(self)
        unregister_child_reopen()

    def _open_full_unrar(self):
        from modules.qt.license_dialog_qt import show_full_unrar_license_window_qt
        register_child_reopen(self._open_full_unrar)
        show_full_unrar_license_window_qt(self)
        unregister_child_reopen()

    def _open_full_7zip(self):
        from modules.qt.license_dialog_qt import show_full_7zip_license_window_qt
        register_child_reopen(self._open_full_7zip)
        show_full_7zip_license_window_qt(self)
        unregister_child_reopen()

    # ── Thème ─────────────────────────────────────────────────────────────────

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        self._scroll_content.setStyleSheet(f"background: {theme['bg']};")
        self._title_lbl.setStyleSheet(f"color: {theme['text']}; padding-bottom: 10px;")

        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 5px 20px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self._close_btn.setStyleSheet(btn_style)

    # ── Retranslate (sans recréer la fenêtre) ─────────────────────────────────

    def _retranslate(self):
        self.setWindowTitle(_wt("help.title"))
        self._apply_theme()

        # Titre
        title_font = _get_current_font(16)
        title_font.setBold(True)
        self._title_lbl.setFont(title_font)
        self._title_lbl.setText(_("help.title"))

        # Bouton fermer
        close_font = _get_current_font(10)
        close_font.setBold(True)
        self._close_btn.setFont(close_font)
        self._close_btn.setText(_("buttons.close"))

        # Intro
        if self._intro_widget:
            w, key = self._intro_widget
            w.retranslate(_(key))

        # Sections
        for section in self._sections:
            section.retranslate()
            title_key = section._title_key
            sw = self._section_widgets.get(title_key, {})

            if title_key == "help.language":
                self._retranslate_language_section(sw)
            elif title_key == "help.config_files":
                self._retranslate_config_section(sw)
            elif title_key == "help.icons":
                self._retranslate_icons_section(sw)
            elif title_key in ("help.license_gpl", "help.license_unrar", "help.license_7zip"):
                self._retranslate_license_section(sw)
            else:
                if "text" in sw:
                    w, key = sw["text"]
                    w.retranslate(_(key))

    def _retranslate_language_section(self, sw: dict):
        url_piqad   = sw["url_piqad"]
        url_tengwar = sw["url_tengwar"]
        content_text = _("help.language_content")
        full = content_text.replace("{url_piqad}", url_piqad).replace("{url_tengwar}", url_tengwar)
        paragraphs = full.split("\n\n")
        regular, fonts_parts, italic_parts = [], [], []
        for para in paragraphs:
            if url_piqad in para or url_tengwar in para:
                fonts_parts.append(para)
            elif "Claude" in para:
                italic_parts.append(para)
            else:
                regular.append(para)

        if sw.get("regular_w") and regular:
            sw["regular_w"].retranslate("\n\n".join(regular))
        if sw.get("fonts_w") and fonts_parts:
            html = _text_with_links_html("\n\n".join(fonts_parts), [url_piqad, url_tengwar])
            sw["fonts_w"].retranslate(html)
        if sw.get("italic_w") and italic_parts:
            sw["italic_w"].retranslate("\n\n".join(italic_parts))
            f = sw["italic_w"].font()
            f.setItalic(True)
            sw["italic_w"].setFont(f)
        # Boutons de la ligne export
        if sw.get("buttons_row"):
            row = sw["buttons_row"]
            btns = [b for b in row.findChildren(QPushButton)]
            keys = ["help.language_export_piqad", "help.language_export_tengwar"]
            theme = get_current_theme()
            btn_style = (
                f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
                f"border: 1px solid #aaaaaa; padding: 3px 10px; }} "
                f"QPushButton:hover {{ background: {theme['separator']}; }}"
            )
            for btn, key in zip(btns, keys):
                btn.setText(_(key))
                btn.setFont(_get_current_font(10))
                btn.setStyleSheet(btn_style)

    def _retranslate_config_section(self, sw: dict):
        temp_dir     = sw["temp_dir"]
        config_fname = sw["config_fname"]
        config_path  = sw["config_path"]
        config_text  = _("help.config_files_content").replace("{temp_dir}", temp_dir)
        html = _text_with_explorer_links_html(
            config_text,
            [(temp_dir, temp_dir), (config_fname, config_path)],
            None,
        )
        sw["lw"].retranslate(html)
        sw["clip_note"].retranslate(_("help.config_clipboard_note"))
        sw["log_note"].retranslate(_("help.config_log_note"))
        # Boutons de la ligne config
        theme = get_current_theme()
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 3px 10px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        btns = [b for b in sw["btns_row"].findChildren(QPushButton)]
        keys = ["help.config_clear_temp", "help.config_clear_recent", "help.config_clear_config"]
        for btn, key in zip(btns, keys):
            btn.setText(_(key))
            btn.setFont(_get_current_font(10))
            btn.setStyleSheet(btn_style)
        sw["clip_btn"].setText(_("help.config_clear_clipboard"))
        sw["clip_btn"].setFont(_get_current_font(10))
        sw["clip_btn"].setStyleSheet(btn_style)

    def _retranslate_icons_section(self, sw: dict):
        url_icons = sw["url_icons"]
        html = _text_with_links_html(_("help.icons_content"), [url_icons])
        sw["lw"].retranslate(html)
        theme = get_current_theme()
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 3px 10px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        sw["btn"].setText(_("help.icons_save_all"))
        sw["btn"].setFont(_get_current_font(10))
        sw["btn"].setStyleSheet(btn_style)

    def _retranslate_license_section(self, sw: dict):
        html = _text_with_angle_bracket_links_html(_(sw["text_key"]))
        sw["lw"].retranslate(html)
        theme = get_current_theme()
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 3px 10px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        btn_font = _get_current_font(10)
        btn_font.setBold(True)
        sw["btn"].setText(_(sw["btn_key"]))
        sw["btn"].setFont(btn_font)
        sw["btn"].setStyleSheet(btn_style)

    # ── Centrage à l'affichage ────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        if not event.spontaneous():
            QTimer.singleShot(0, self._center_on_parent)

    def _center_on_parent(self):
        p = self._parent_widget
        if p is None:
            return
        from PySide6.QtCore import QPoint
        top_left = p.mapToGlobal(QPoint(0, 0))
        x = top_left.x() + (p.width()  - self.width())  // 2
        y = top_left.y() + (p.height() - self.height()) // 2
        screen = QApplication.screenAt(top_left)
        if screen:
            sg = screen.availableGeometry()
            x = max(sg.x(), min(x, sg.x() + sg.width()  - self.width()))
            y = max(sg.y(), min(y, sg.y() + sg.height() - self.height()))
        self.move(x, y)

    # ── Nettoyage ─────────────────────────────────────────────────────────────

    def _on_close(self):
        panel_key = id(self._parent_widget)
        _help_windows.pop(panel_key, None)
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Point d'entrée public
# ═══════════════════════════════════════════════════════════════════════════════

def show_user_guide(parent_widget, callbacks: dict):
    panel_key = id(parent_widget)

    entry = _help_windows.get(panel_key)
    if entry is not None and entry.isVisible():
        entry.raise_()
        entry.activateWindow()
        return

    dlg = _HelpDialog(parent_widget, callbacks)
    _help_windows[panel_key] = dlg
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


def update_help_window_if_open(reopen_func=None):
    """Maintenu pour compatibilité — met à jour toutes les fenêtres ouvertes."""
    for dlg in list(_help_windows.values()):
        if dlg.isVisible():
            dlg._retranslate()


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers HTML
# ═══════════════════════════════════════════════════════════════════════════════

def _escape_html(text: str) -> str:
    import html
    return html.escape(text).replace("\n", "<br>")


def _text_with_links_html(text: str, urls: list) -> str:
    import html as _html
    theme = get_current_theme()
    link_color = theme.get("link", "#0066cc")

    result = []
    pos = 0
    pending = [(text.find(u, 0), u) for u in urls if text.find(u, 0) != -1]
    pending.sort()

    for url_pos, url in pending:
        if url_pos < pos:
            continue
        result.append(_html.escape(text[pos:url_pos]).replace("\n", "<br>"))
        result.append(f'<a href="{_html.escape(url)}" style="color:{link_color};">{_html.escape(url)}</a>')
        pos = url_pos + len(url)

    result.append(_html.escape(text[pos:]).replace("\n", "<br>"))
    return "".join(result)


def _text_with_angle_bracket_links_html(text: str) -> str:
    import html as _html
    theme = get_current_theme()
    link_color = theme.get("link", "#0066cc")

    def _replace(m):
        url = m.group(1)
        return f'<a href="{_html.escape(url)}" style="color:{link_color};">{_html.escape(url)}</a>'

    escaped = _html.escape(text).replace("\n", "<br>")
    return re.sub(r"&lt;(https?://[^&]+)&gt;", _replace, escaped)


def _text_with_explorer_links_html(text: str, replacements: list, _open_explorer_func) -> str:
    import html as _html
    theme = get_current_theme()
    link_color = theme.get("link", "#0066cc")

    result = []
    pos = 0

    candidates = []
    for needle, path in replacements:
        idx = text.find(needle, 0)
        while idx != -1:
            candidates.append((idx, needle, path))
            idx = text.find(needle, idx + 1)
    candidates.sort()

    seen_ranges = []
    filtered = []
    for idx, needle, path in candidates:
        end = idx + len(needle)
        overlap = any(s <= idx < e or s < end <= e for s, e in seen_ranges)
        if not overlap:
            filtered.append((idx, needle, path))
            seen_ranges.append((idx, end))

    for url_pos, needle, path in filtered:
        if url_pos < pos:
            continue
        result.append(_html.escape(text[pos:url_pos]).replace("\n", "<br>"))
        href = QUrl.fromLocalFile(path).toString()
        result.append(
            f'<a href="{_html.escape(href)}" style="color:{link_color};">'
            f'{_html.escape(needle)}</a>'
        )
        pos = url_pos + len(needle)

    result.append(_html.escape(text[pos:]).replace("\n", "<br>"))
    return "".join(result)
