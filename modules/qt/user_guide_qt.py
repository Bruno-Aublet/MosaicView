"""
user_guide_qt.py — Fenêtre de mode d'emploi (version PySide6)
Reproduit fidèlement modules/user_guide.py (tkinter).

Comportement identique :
  - Fenêtre modale centrée sur la fenêtre principale
  - Sections collapsibles (état persistant par session)
  - Scrollable avec molette souris
  - Texte sélectionnable, liens cliquables
  - Boutons d'action (exporter polices, vider fichiers temporaires, etc.)
  - Mise à jour à la volée si rouverte après changement de langue
"""

import os
import re
import shutil
import tempfile
import subprocess
import webbrowser

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QFrame, QSizePolicy, QTextBrowser,
    QFileDialog,
    QApplication,
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont, QDesktopServices, QIcon

from modules.qt.localization import _, _wt
from modules.qt.font_loader import resource_path, PIQAD_FONT_FILE, TENGWAR_FONT_FILES
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.dialogs_qt import ErrorDialog
from modules.qt.config_manager import get_config_manager


# ── Référence globale (singleton) ─────────────────────────────────────────────
_help_window_ref: QDialog | None = None
_help_parent_ref = None
_help_callbacks_ref = None
_help_lang_handler = None  # handler connecté au signal langue (un seul à la fois)
_help_debounce_timer = None  # QTimer pour debounce du changement de langue
_active_child_reopen: "callable | None" = None  # fonction pour rouvrir le dialogue enfant après recréation du guide


def register_child_reopen(reopen_func):
    """Enregistre une fonction pour rouvrir le dialogue enfant après recréation du guide."""
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
    """
    Texte sélectionnable en lecture seule.
    Reproduit _make_selectable_text (tkinter).
    Liens cliquables via anchorClicked.
    """
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.anchorClicked.connect(lambda url: QDesktopServices.openUrl(url))
        self.setReadOnly(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.document().setDocumentMargin(0)
        self.setContentsMargins(0, 0, 0, 0)

        theme = get_current_theme()
        link_color = theme.get("link", "#0066cc")
        self.setStyleSheet(
            f"QTextBrowser {{ background: transparent; color: {theme['text']}; "
            f"border: none; padding: 0px; }} "
            f"a {{ color: {link_color}; }}"
        )
        font = _get_current_font(10)
        self.setFont(font)
        self.setPlainText(text)
        self._adjust_height()
        self.document().contentsChanged.connect(self._adjust_height)

    def _adjust_height(self):
        doc_h = int(self.document().size().height()) + 4
        self.setFixedHeight(max(doc_h, 20))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_height()

    def scrollContentsBy(self, dx, dy):
        # Toujours maintenir le viewport en haut (le widget a hauteur fixe = tout son contenu)
        super().scrollContentsBy(dx, dy)
        self.verticalScrollBar().setValue(0)

    def wheelEvent(self, event):
        # Transmet la molette au QScrollArea parent au lieu de scroller le texte
        event.ignore()


class _LinkText(QTextBrowser):
    """
    Texte avec liens HTML cliquables (pour URLs, chemins cliquables).
    """
    def __init__(self, html: str, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._on_anchor_clicked)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.document().setDocumentMargin(0)
        self.setContentsMargins(0, 0, 0, 0)

        theme = get_current_theme()
        link_color = theme.get("link", "#0066cc")
        self.setStyleSheet(
            f"QTextBrowser {{ background: transparent; color: {theme['text']}; "
            f"border: none; padding: 0px; }} "
            f"a {{ color: {link_color}; }}"
        )
        font = _get_current_font(10)
        self.setFont(font)
        self.setHtml(html)
        self._adjust_height()
        self.document().contentsChanged.connect(self._adjust_height)

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
        # Toujours maintenir le viewport en haut (le widget a hauteur fixe = tout son contenu)
        super().scrollContentsBy(dx, dy)
        self.verticalScrollBar().setValue(0)

    def wheelEvent(self, event):
        # Transmet la molette au QScrollArea parent au lieu de scroller le texte
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
    def __init__(self, title: str, section_idx: int, parent=None):
        super().__init__(parent)
        self._idx = section_idx
        self._collapsed = _section_collapsed_state.get(section_idx, True)

        theme = get_current_theme()
        self._theme = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(0)

        # Bouton titre
        self._btn = QPushButton()
        self._btn.setFlat(True)
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.setStyleSheet(
            f"QPushButton {{ background: {theme['bg']}; color: {theme['text']}; "
            f"text-align: left; padding: 4px 10px; font-weight: bold; }} "
            f"QPushButton:hover {{ background: {theme['toolbar_bg']}; }}"
        )
        font = _get_current_font(12)
        font.setBold(True)
        self._btn.setFont(font)
        self._btn.clicked.connect(self._toggle)
        layout.addWidget(self._btn)

        # Contenu
        self._content = QWidget()
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(4)
        self._content_layout = content_layout
        layout.addWidget(self._content)

        self._title = title
        self._update_btn()
        self._content.setVisible(not self._collapsed)

    def _update_btn(self):
        arrow = "\u25ba" if self._collapsed else "\u25bc"
        self._btn.setText(f"{arrow}  {self._title}")

    def _toggle(self):
        self._collapsed = not self._collapsed
        _section_collapsed_state[self._idx] = self._collapsed
        self._content.setVisible(not self._collapsed)
        self._update_btn()

    def add_widget(self, widget: QWidget):
        self._content_layout.addWidget(widget)

    def add_button(self, text: str, callback) -> QPushButton:
        btn = _make_action_button(self._content, text, callback)
        self._content_layout.addWidget(btn, alignment=Qt.AlignLeft)
        self._content_layout.setContentsMargins(20, 0, 20, 10)
        return btn

    def add_buttons_row(self, buttons: list[tuple[str, object]]) -> QWidget:
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
    """Fenêtre de confirmation post-export avec lien cliquable vers le dossier."""
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

    # Centrer sur le parent
    if parent:
        pg = parent.geometry()
        dlg.move(
            pg.x() + (pg.width()  - dlg.width())  // 2,
            pg.y() + (pg.height() - dlg.height()) // 2,
        )
    dlg.exec()


def export_piqad_font(parent_widget):
    """Exporte la police pIqaD vers un fichier choisi par l'utilisateur."""
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
    """Exporte toutes les polices Tengwar vers un dossier choisi par l'utilisateur."""
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
    """Enregistre toutes les icônes PNG dans un dossier choisi par l'utilisateur."""
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

def show_user_guide(parent_widget, callbacks: dict):
    """
    Affiche la fenêtre de mode d'emploi.

    Args:
        parent_widget : QWidget parent (MainWindow)
        callbacks     : dict avec les clés :
            'export_piqad_font'
            'export_tengwar_fonts'
            'clear_temp_files_with_message'
            'clear_recent_files'
            'clear_config_file'
            'clear_clipboard_files'
            'save_all_icons'
    """
    global _help_window_ref, _help_parent_ref, _help_callbacks_ref

    # Singleton : si déjà ouverte, mettre au premier plan
    if _help_window_ref is not None and _help_window_ref.isVisible():
        _help_window_ref.raise_()
        _help_window_ref.activateWindow()
        return

    _help_parent_ref   = parent_widget
    _help_callbacks_ref = callbacks

    dlg = QDialog(parent_widget)
    _help_window_ref = dlg
    dlg.setWindowTitle(_wt("help.title"))
    dlg.resize(780, 600)
    dlg.setModal(True)

    # Icône
    ico_path = resource_path("icons/MosaicView.ico")
    if os.path.exists(ico_path):
        from PySide6.QtGui import QIcon
        dlg.setWindowIcon(QIcon(ico_path))

    theme = get_current_theme()
    dlg.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")

    def on_close():
        global _help_window_ref, _help_lang_handler
        _help_window_ref = None
        if _help_lang_handler is not None:
            from modules.qt.language_signal import language_signal
            try:
                language_signal.changed.disconnect(_help_lang_handler)
            except RuntimeError:
                pass
            _help_lang_handler = None
        dlg.accept()

    dlg.rejected.connect(on_close)

    # ── Layout principal ──────────────────────────────────────────────────────
    main_layout = QVBoxLayout(dlg)
    main_layout.setContentsMargins(10, 10, 10, 10)
    main_layout.setSpacing(6)

    # Zone scrollable
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    scroll_content = QWidget()
    scroll_content.setStyleSheet(f"background: {theme['bg']};")
    content_layout = QVBoxLayout(scroll_content)
    content_layout.setContentsMargins(10, 0, 10, 10)
    content_layout.setSpacing(0)
    content_layout.setAlignment(Qt.AlignTop)

    scroll.setWidget(scroll_content)
    main_layout.addWidget(scroll, stretch=1)

    # ── Titre ─────────────────────────────────────────────────────────────────
    title_lbl = QLabel(_("help.title"))
    title_font = _get_current_font(16)
    title_font.setBold(True)
    title_lbl.setFont(title_font)
    title_lbl.setAlignment(Qt.AlignCenter)
    title_lbl.setStyleSheet(f"color: {theme['text']}; padding-bottom: 10px;")
    content_layout.addWidget(title_lbl)

    # ── Contenu du guide ──────────────────────────────────────────────────────
    guide_content = [
        ("",                            _("help.intro")),
        (_("help.open_files"),          _("help.open_files_content")),
        (_("help.mosaic"),              _("help.mosaic_content")),
        (_("help.icon_toolbar"),        _("help.icon_toolbar_content")),
        (_("help.manipulation"),        _("help.manipulation_content")),
        (_("help.renumber"),            _("help.renumber_content")),
        (_("help.flatten"),             _("help.flatten_content")),
        (_("help.viewer"),              _("help.viewer_content")),
        (_("help.crop"),                _("help.crop_content")),
        (_("help.resize_pages"),        _("help.resize_pages_content")),
        (_("help.join_pages"),          _("help.join_pages_content")),
        (_("help.split_pages"),         _("help.split_pages_content")),
        (_("help.adjust_images"),       _("help.adjust_images_content")),
        (_("help.create_icon"),         _("help.create_icon_content")),
        (_("help.batch_convert"),       _("help.batch_convert_content")),
        (_("help.shortcuts"),           _("help.shortcuts_content")),
        (_("help.dark_mode"),           _("help.dark_mode_content")),
        (_("help.sort"),                _("help.sort_content")),
        (_("help.save"),                _("help.save_content")),
        (_("help.other"),               _("help.other_content")),
        (_("help.language"),            _("help.language_content")),
        (_("help.config_files"),        "CONFIG_SECTION"),
        (_("help.icons"),               "ICONS_SECTION"),
        (_("help.split_ui"),            _("help.split_ui_content")),
        (_("help.license_gpl"),         "LICENSE_GPL_SECTION"),
        (_("help.license_unrar"),       "LICENSE_UNRAR_SECTION"),
        (_("help.license_7zip"),        "LICENSE_7ZIP_SECTION"),
        (_("help.credits"),             _("help.credits_content")),
    ]

    for idx, (title_text, content_text) in enumerate(guide_content):

        # ── Intro : toujours visible, non collapsible ─────────────────────────
        if not title_text:
            w = _SelectableText(content_text)
            w.setContentsMargins(20, 0, 20, 10)
            content_layout.addWidget(w)
            continue

        # ── Section collapsible ───────────────────────────────────────────────
        section = _CollapsibleSection(title_text, idx)
        content_layout.addWidget(section)

        # ── Langue ────────────────────────────────────────────────────────────
        if title_text == _("help.language"):
            url_piqad   = "https://github.com/dadap/pIqaD-fonts"
            url_tengwar = "https://www.dafont.com/fr/tengwar-annatar.font"
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
                w = _SelectableText("\n\n".join(regular))
                w.setContentsMargins(20, 0, 20, 5)
                section.add_widget(w)

            if fonts_parts:
                text = "\n\n".join(fonts_parts)
                # Convertit les URLs en liens HTML
                html = _text_with_links_html(text, [url_piqad, url_tengwar])
                lw = _LinkText(html)
                lw.setContentsMargins(20, 0, 20, 5)
                section.add_widget(lw)
                section.add_buttons_row([
                    (_("help.language_export_piqad"),   callbacks.get("export_piqad_font",   lambda: None)),
                    (_("help.language_export_tengwar"), callbacks.get("export_tengwar_fonts", lambda: None)),
                ])

            if italic_parts:
                w = _SelectableText("\n\n".join(italic_parts))
                w.setContentsMargins(20, 0, 20, 10)
                # Italique via stylesheet
                f = w.font()
                f.setItalic(True)
                w.setFont(f)
                section.add_widget(w)

        # ── Configuration ─────────────────────────────────────────────────────
        elif content_text == "CONFIG_SECTION":
            temp_dir      = os.path.join(os.path.realpath(tempfile.gettempdir()), "MosaicViewTemp")
            config_fname  = ".mosaicview_config.json"
            config_path   = os.path.join(temp_dir, config_fname)
            config_text   = _("help.config_files_content").replace("{temp_dir}", temp_dir)

            def _open_explorer(path):
                try:
                    subprocess.Popen(["explorer", "/select,", path])
                except Exception as e:
                    print(f"Erreur ouverture explorateur : {e}")

            # Liens cliquables : temp_dir → ouvre le dossier, config_fname → sélectionne le fichier
            html = _text_with_explorer_links_html(
                config_text,
                [(temp_dir, temp_dir), (config_fname, config_path)],
                _open_explorer,
            )
            lw = _LinkText(html)
            lw.setContentsMargins(20, 0, 20, 5)
            section.add_widget(lw)

            section.add_buttons_row([
                (_("help.config_clear_temp"),   callbacks.get("clear_temp_files_with_message", lambda: None)),
                (_("help.config_clear_recent"), callbacks.get("clear_recent_files",            lambda: None)),
                (_("help.config_clear_config"), callbacks.get("clear_config_file",             lambda: None)),
            ])

            clip_note = _SelectableText(_("help.config_clipboard_note"))
            clip_note.setContentsMargins(20, 10, 20, 0)
            section.add_widget(clip_note)

            section.add_button(
                _("help.config_clear_clipboard"),
                callbacks.get("clear_clipboard_files", lambda: None),
            )

            log_note = _SelectableText(_("help.config_log_note"))
            log_note.setContentsMargins(20, 10, 20, 10)
            section.add_widget(log_note)

        # ── Icônes ────────────────────────────────────────────────────────────
        elif content_text == "ICONS_SECTION":
            url_icons = "https://www.freepik.com/author/juicy-fish/icons/juicy-fish-sketchy_908#from_element=resource_detail"
            icons_text = _("help.icons_content")
            html = _text_with_links_html(icons_text, [url_icons])
            lw = _LinkText(html)
            lw.setContentsMargins(20, 0, 20, 5)
            section.add_widget(lw)
            section.add_button(
                _("help.icons_save_all"),
                callbacks.get("save_all_icons", lambda: None),
            )

        # ── Licence GPL ───────────────────────────────────────────────────────
        elif content_text == "LICENSE_GPL_SECTION":
            license_text = _("labels.license_text")
            html = _text_with_angle_bracket_links_html(license_text)
            lw = _LinkText(html)
            lw.setContentsMargins(20, 0, 20, 5)
            section.add_widget(lw)

            def _open_full_gpl():
                from modules.qt.license_dialog_qt import show_full_license_window_qt
                register_child_reopen(_open_full_gpl)
                show_full_license_window_qt(dlg)
                unregister_child_reopen()

            btn = _make_action_button(section._content, _("labels.view_full_license"), _open_full_gpl)
            btn_font = btn.font()
            btn_font.setBold(True)
            btn.setFont(btn_font)
            section._content_layout.addWidget(btn, alignment=Qt.AlignLeft)
            section._content_layout.setContentsMargins(20, 0, 20, 10)

        # ── Licence UnRAR ──────────────────────────────────────────────────────
        elif content_text == "LICENSE_UNRAR_SECTION":
            unrar_text = _("labels.license_unrar_text")
            html = _text_with_angle_bracket_links_html(unrar_text)
            lw = _LinkText(html)
            lw.setContentsMargins(20, 0, 20, 5)
            section.add_widget(lw)

            def _open_full_unrar():
                from modules.qt.license_dialog_qt import show_full_unrar_license_window_qt
                register_child_reopen(_open_full_unrar)
                show_full_unrar_license_window_qt(dlg)
                unregister_child_reopen()

            btn = _make_action_button(section._content, _("labels.view_full_unrar_license"), _open_full_unrar)
            btn_font2 = btn.font()
            btn_font2.setBold(True)
            btn.setFont(btn_font2)
            section._content_layout.addWidget(btn, alignment=Qt.AlignLeft)
            section._content_layout.setContentsMargins(20, 0, 20, 10)

        # ── Licence 7-Zip ──────────────────────────────────────────────────────
        elif content_text == "LICENSE_7ZIP_SECTION":
            sevenzip_text = _("labels.license_7zip_text")
            html = _text_with_angle_bracket_links_html(sevenzip_text)
            lw = _LinkText(html)
            lw.setContentsMargins(20, 0, 20, 5)
            section.add_widget(lw)

            def _open_full_7zip():
                from modules.qt.license_dialog_qt import show_full_7zip_license_window_qt
                register_child_reopen(_open_full_7zip)
                show_full_7zip_license_window_qt(dlg)
                unregister_child_reopen()

            btn = _make_action_button(section._content, _("labels.view_full_7zip_license"), _open_full_7zip)
            btn_font3 = btn.font()
            btn_font3.setBold(True)
            btn.setFont(btn_font3)
            section._content_layout.addWidget(btn, alignment=Qt.AlignLeft)
            section._content_layout.setContentsMargins(20, 0, 20, 10)

        # ── Sections standard ─────────────────────────────────────────────────
        else:
            pady = (5, 10) if title_text == _("help.credits") else (0, 10)
            w = _SelectableText(content_text)
            w.setContentsMargins(20, pady[0], 20, pady[1])
            section.add_widget(w)

    # ── Bouton Fermer ─────────────────────────────────────────────────────────
    close_btn = QPushButton(_("buttons.close"))
    close_btn.setCursor(Qt.PointingHandCursor)
    close_font = _get_current_font(10)
    close_font.setBold(True)
    close_btn.setFont(close_font)
    close_btn.setStyleSheet(
        f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
        f"border: 1px solid #aaaaaa; padding: 5px 20px; }} "
        f"QPushButton:hover {{ background: {theme['separator']}; }}"
    )
    close_btn.clicked.connect(on_close)
    content_layout.addWidget(close_btn, alignment=Qt.AlignHCenter)

    # ── Centrage sur la fenêtre principale ────────────────────────────────────
    if parent_widget:
        pg = parent_widget.geometry()
        x = pg.x() + (pg.width()  - dlg.width())  // 2
        y = pg.y() + (pg.height() - dlg.height()) // 2
        dlg.move(max(x, 0), max(y, 0))

    # Mise à jour à la volée lors d'un changement de langue (un seul handler à la fois)
    global _help_lang_handler, _help_debounce_timer
    from modules.qt.language_signal import language_signal
    if _help_lang_handler is not None:
        try:
            language_signal.changed.disconnect(_help_lang_handler)
        except RuntimeError:
            pass
    if _help_debounce_timer is None:
        from PySide6.QtCore import QTimer
        _help_debounce_timer = QTimer()
        _help_debounce_timer.setSingleShot(True)
        _help_debounce_timer.timeout.connect(
            lambda: update_help_window_if_open(lambda: show_user_guide(_help_parent_ref, _help_callbacks_ref))
        )
    def _on_lang_changed(lang_code):
        _help_debounce_timer.start(150)
    _help_lang_handler = _on_lang_changed
    language_signal.changed.connect(_help_lang_handler)

    dlg.show()


def update_help_window_if_open(reopen_func):
    """Met à jour la fenêtre d'aide si elle est ouverte (changement de langue).
    Ne recrée pas le guide si un dialogue enfant modal est actif devant lui."""
    global _help_window_ref
    if _help_window_ref is None or not _help_window_ref.isVisible():
        return
    # Si un dialogue modal enfant est ouvert : le fermer, recréer le guide, puis le rouvrir
    active = QApplication.activeModalWidget()
    if active is not None and active is not _help_window_ref:
        active.close()
    geom = _help_window_ref.geometry()
    _help_window_ref.close()
    _help_window_ref = None
    reopen_func()
    if _help_window_ref is not None and _help_window_ref.isVisible():
        _help_window_ref.setGeometry(geom)
    # Rouvrir le dialogue enfant par-dessus si enregistré
    if _active_child_reopen is not None:
        _active_child_reopen()


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers HTML
# ═══════════════════════════════════════════════════════════════════════════════

def _escape_html(text: str) -> str:
    import html
    return html.escape(text).replace("\n", "<br>")


def _text_with_links_html(text: str, urls: list[str]) -> str:
    """Convertit un texte brut en HTML avec les URLs rendues cliquables."""
    import html as _html
    theme = get_current_theme()
    link_color = theme.get("link", "#0066cc")

    result = []
    pos = 0
    # Trouve chaque URL dans l'ordre d'apparition
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
    """Convertit <https://...> en liens cliquables HTML."""
    import html as _html
    theme = get_current_theme()
    link_color = theme.get("link", "#0066cc")

    def _replace(m):
        url = m.group(1)
        return f'<a href="{_html.escape(url)}" style="color:{link_color};">{_html.escape(url)}</a>'

    escaped = _html.escape(text).replace("\n", "<br>")
    # Les < et > sont échappés → &lt;https://...&gt;
    return re.sub(r"&lt;(https?://[^&]+)&gt;", _replace, escaped)


def _text_with_explorer_links_html(
    text: str,
    replacements: list[tuple[str, str]],
    _open_explorer_func,
) -> str:
    """
    Convertit les occurrences de textes en liens qui ouvrent l'explorateur.
    replacements : liste de (texte_à_chercher, chemin_à_ouvrir)
    Note : comme QTextBrowser ouvre les liens via anchorClicked, on utilise
    un scheme custom "explorer://" — intercepté dans _LinkText via anchorClicked.
    """
    import html as _html
    theme = get_current_theme()
    link_color = theme.get("link", "#0066cc")

    result = []
    pos = 0

    # Tri par ordre d'apparition dans le texte
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
    html_str = "".join(result)
    return html_str
