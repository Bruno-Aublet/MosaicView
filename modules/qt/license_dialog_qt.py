# license_dialog_qt.py — Fenêtre de licence pour MosaicView Qt
# Reproduit fidèlement show_license_dialog() de modules/about_dialogs.py (tkinter)

import os
import re
import webbrowser

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTextBrowser, QPushButton,
    QScrollBar, QSizePolicy, QDialogButtonBox,
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont, QDesktopServices

from modules.qt.localization import _, _wt
from modules.qt.font_loader import resource_path
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font


def show_license_dialog_qt(parent):
    """Ouvre la fenêtre de licence (équivalent Qt de show_license_dialog)."""
    dlg = _LicenseDialog(parent)
    dlg.exec()


def show_full_license_window_qt(parent):
    """Ouvre le fichier LICENSE en lecture seule (équivalent Qt de show_full_license_window)."""
    dlg = _FullLicenseDialog(parent, resource_path("LICENSE"), "GNU General Public License v3.0")
    dlg.exec()


def show_full_unrar_license_window_qt(parent):
    """Ouvre unrar/license.txt en lecture seule."""
    import os
    dlg = _FullLicenseDialog(parent, resource_path(os.path.join("unrar", "license.txt")), "UnRAR License")
    dlg.exec()


def show_full_7zip_license_window_qt(parent):
    """Ouvre 7zip/license.txt en lecture seule."""
    import os
    dlg = _FullLicenseDialog(parent, resource_path(os.path.join("7zip", "license.txt")), "7-Zip License")
    dlg.exec()


def show_full_piqad_license_window_qt(parent):
    """Ouvre fonts/pIqaD-qolqoS-LICENSE.txt en lecture seule."""
    import os
    dlg = _FullLicenseDialog(parent, resource_path(os.path.join("fonts", "pIqaD-qolqoS-LICENSE.txt")), "pIqaD qolqoS — SIL Open Font License 1.1")
    dlg.exec()


def show_full_tengwar_license_window_qt(parent):
    """Ouvre fonts/AlcarinTengwar-LICENSE.txt en lecture seule."""
    import os
    dlg = _FullLicenseDialog(parent, resource_path(os.path.join("fonts", "AlcarinTengwar-LICENSE.txt")), "Alcarin Tengwar — SIL Open Font License 1.1")
    dlg.exec()


# ── Dialog résumé de licence ──────────────────────────────────────────────────

class _LicenseDialog(QDialog):

    def __init__(self, parent):
        super().__init__(parent)
        self.resize(650, 550)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 15)
        layout.setSpacing(8)

        # Zone de texte
        self._browser = QTextBrowser()
        self._browser.setOpenLinks(False)
        self._browser.anchorClicked.connect(self._on_link_clicked)
        from modules.qt.utils import setup_text_browser_context_menu
        setup_text_browser_context_menu(self._browser)
        layout.addWidget(self._browser, stretch=1)

        # Bouton "Voir la licence complète"
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        self._btn_full = QPushButton()
        self._btn_full.setCursor(Qt.PointingHandCursor)
        self._btn_full.clicked.connect(self._open_full_license)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_full)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
        self._center_parent = parent

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self.setWindowTitle(_wt("dialogs.license.window_title"))
        self._browser.setFont(font)
        self._btn_full.setText(_("labels.view_full_license"))
        self._btn_full.setFont(font)
        self._btn_full.setStyleSheet(btn_style)
        self._populate()

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def _populate(self):
        from modules.qt.state import get_current_theme
        theme = get_current_theme()
        link_color = theme.get("link", "#0066cc")
        font = _get_current_font(10)
        font_family = font.family()
        font_size = font.pointSize()
        license_text = _("labels.license_text")
        url_pattern = r'<(https?://[^>]+)>'
        paragraphs = license_text.split("\n\n")

        html_parts = [f'<div style="text-align:center; font-family:{font_family}; font-size:{font_size}pt;"><p style="margin:0; line-height:120%;">&nbsp;</p>']

        def is_heading(line):
            s = line.strip()
            return s == s.upper() and s and not s.startswith("http") and len(s) > 2

        for p_idx, paragraph in enumerate(paragraphs):
            lines = paragraph.split("\n")
            first_line = lines[0].strip()

            if is_heading(first_line):
                # Ligne vide de séparation avant chaque titre (sauf le tout premier paragraphe)
                if p_idx > 0:
                    html_parts.append('<p style="margin:0; line-height:120%;">&nbsp;</p>')
                # Double espacement uniquement avant le premier titre (LIBERTÉS ACCORDÉES)
                if not any(is_heading(p.split("\n")[0].strip()) for p in paragraphs[:p_idx]):
                    html_parts.append('<p style="margin:0; line-height:120%;">&nbsp;</p>')
                html_parts.append(
                    f'<p style="margin:0; font-weight:bold;">'
                    f'{_esc(first_line)}</p>'
                )
                for line in lines[1:]:
                    m = re.search(url_pattern, line)
                    if m:
                        url = m.group(1)
                        before = _esc(line[:m.start()])
                        after = _esc(line[m.end():])
                        html_parts.append(
                            f'<p style="margin:0">{before}'
                            f'<a href="{url}" style="color:{link_color};">{_esc(url)}</a>'
                            f'{after}</p>'
                        )
                    else:
                        html_parts.append(f'<p style="margin:0">{_esc(line)}</p>')
            else:
                full = "\n".join(lines)
                bold = "font-weight:bold;" if p_idx <= 1 else ""
                # Ligne vide avant les paragraphes normaux après les 2 premiers
                if p_idx > 1:
                    html_parts.append('<p style="margin:0; line-height:120%;">&nbsp;</p>')
                m = re.search(url_pattern, full)
                if m:
                    url = m.group(1)
                    before = _esc(full[:m.start()])
                    after = _esc(full[m.end():])
                    html_parts.append(
                        f'<p style="margin:0;{bold}">{before}'
                        f'<a href="{url}" style="color:{link_color};">{_esc(url)}</a>'
                        f'{after}</p>'
                    )
                else:
                    html_parts.append(f'<p style="margin:0;{bold}">{_esc(full)}</p>')

        html_parts.append('</div>')
        self._browser.setHtml("".join(html_parts))

    def _on_link_clicked(self, url: QUrl):
        QDesktopServices.openUrl(url)

    def _open_full_license(self):
        show_full_license_window_qt(self)



# ── Dialog licence complète (fichier LICENSE) ─────────────────────────────────

class _FullLicenseDialog(QDialog):

    def __init__(self, parent, license_path: str, window_title: str):
        super().__init__(parent)
        self._window_title = window_title
        self.resize(800, 600)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        self._browser = QTextBrowser()
        self._browser.setOpenLinks(False)
        from modules.qt.utils import setup_text_browser_context_menu
        setup_text_browser_context_menu(self._browser)
        layout.addWidget(self._browser, stretch=1)

        self._btn_close = QPushButton()
        self._btn_close.setCursor(Qt.PointingHandCursor)
        self._btn_close.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._btn_close)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        if os.path.exists(license_path):
            try:
                with open(license_path, "r", encoding="utf-8") as f:
                    self._browser.setPlainText(f.read())
            except Exception as e:
                self._browser.setPlainText(_("messages.errors.license_file_read_error.message", error=e))
        else:
            self._browser.setPlainText(_("messages.errors.license_file_not_found.message", path=license_path))

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
        self._center_parent = parent

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(9)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self.setWindowTitle(self._window_title)
        self._browser.setFont(font)
        self._btn_close.setText(_("buttons.ok"))
        self._btn_close.setFont(font)
        self._btn_close.setStyleSheet(btn_style)

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass


def _esc(text: str) -> str:
    """Échappe le HTML et convertit les sauts de ligne en <br>."""
    import html
    return html.escape(text).replace("\n", "<br>")
