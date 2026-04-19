# changelog_dialog_qt.py — Fenêtre de changelog pour MosaicView Qt

import os
import re

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTextBrowser, QPushButton
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices

from modules.qt.localization import _, _wt
from modules.qt.font_loader import resource_path
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font


def show_changelog_dialog_qt(parent):
    """Ouvre la fenêtre de changelog."""
    dlg = _ChangelogDialog(parent)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


class _ChangelogDialog(QDialog):

    def __init__(self, parent):
        super().__init__(parent)
        self.resize(750, 600)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        self._browser = QTextBrowser()
        self._browser.setOpenLinks(False)
        self._browser.anchorClicked.connect(self._on_link_clicked)
        from modules.qt.utils import setup_text_browser_context_menu
        setup_text_browser_context_menu(self._browser)
        layout.addWidget(self._browser, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        self._btn_close = QPushButton()
        self._btn_close.setCursor(Qt.PointingHandCursor)
        self._btn_close.clicked.connect(self.accept)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_close)
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
            from PySide6.QtCore import QTimer, QPoint
            from PySide6.QtWidgets import QApplication
            p = self._center_parent
            def _do_center():
                top_left = p.mapToGlobal(QPoint(0, 0))
                x = top_left.x() + (p.width()  - self.width())  // 2
                y = top_left.y() + (p.height() - self.height()) // 2
                screen = QApplication.screenAt(top_left) or QApplication.primaryScreen()
                if screen:
                    sa = screen.availableGeometry()
                    x = max(sa.left(), min(x, sa.right()  - self.width()))
                    y = max(sa.top(),  min(y, sa.bottom() - self.height()))
                self.move(x, y)
            QTimer.singleShot(0, _do_center)

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(9)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self.setWindowTitle(_wt("menu.show_changelog"))
        self._browser.setFont(font)
        self._btn_close.setText(_("buttons.ok"))
        self._btn_close.setFont(font)
        self._btn_close.setStyleSheet(btn_style)
        self._populate()

    def _populate(self):
        theme = get_current_theme()
        link_color = theme.get("link", "#0066cc")
        font = _get_current_font(9)
        font_family = font.family()
        font_size = font.pointSize()

        changelog_path = resource_path("CHANGELOG.md")
        if not os.path.exists(changelog_path):
            self._browser.setPlainText("CHANGELOG.md not found.")
            return

        try:
            with open(changelog_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            self._browser.setPlainText(str(e))
            return

        html = _markdown_to_html(content, font_family, font_size, link_color)
        self._browser.setHtml(html)

    def _on_link_clicked(self, url: QUrl):
        QDesktopServices.openUrl(url)

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass


def _markdown_to_html(md: str, font_family: str, font_size: int, link_color: str) -> str:
    import html as _html

    lines = md.splitlines()
    parts = [
        f'<div style="font-family:{font_family}; font-size:{font_size}pt; padding:4px;">'
    ]

    for line in lines:
        # Titres ## [1.x.y] - date
        m = re.match(r'^## (.+)$', line)
        if m:
            text = _esc(m.group(1))
            parts.append(
                f'<p style="margin:12px 0 2px 0; font-weight:bold; font-size:{font_size + 1}pt;">'
                f'{text}</p>'
            )
            continue

        # Titre principal #
        m = re.match(r'^# (.+)$', line)
        if m:
            text = _esc(m.group(1))
            parts.append(
                f'<p style="margin:0 0 8px 0; font-weight:bold; font-size:{font_size + 2}pt;">'
                f'{text}</p>'
            )
            continue

        # Ligne vide
        if line.strip() == "":
            parts.append('<p style="margin:0; line-height:50%;">&nbsp;</p>')
            continue

        # Puce -
        m = re.match(r'^- (.+)$', line)
        if m:
            inner = _inline(m.group(1), link_color)
            parts.append(
                f'<p style="margin:1px 0 1px 16px;">• {inner}</p>'
            )
            continue

        # Paragraphe normal
        parts.append(f'<p style="margin:2px 0;">{_inline(line, link_color)}</p>')

    parts.append('</div>')
    return "".join(parts)


def _inline(text: str, link_color: str) -> str:
    import html as _html

    # Découper le texte en segments : marqueurs inline vs texte brut.
    # On traite séquentiellement pour éviter de re-échapper du HTML généré.
    pattern = re.compile(
        r'\[([^\]]+)\]\(([^)]+)\)'   # lien [label](url)
        r'|`([^`]+)`'                # code `...`
        r'|\*\*([^*]+)\*\*'          # gras **...**
    )

    result = []
    last = 0
    for m in pattern.finditer(text):
        # texte brut avant ce marqueur
        result.append(_html.escape(text[last:m.start()]))
        last = m.end()

        if m.group(1) is not None:   # lien
            label = _html.escape(m.group(1))
            url   = m.group(2)
            result.append(f'<a href="{url}" style="color:{link_color};">{label}</a>')
        elif m.group(3) is not None:  # code
            result.append(f'<code>{_html.escape(m.group(3))}</code>')
        elif m.group(4) is not None:  # gras
            result.append(f'<b>{_html.escape(m.group(4))}</b>')

    result.append(_html.escape(text[last:]))
    return "".join(result)


def _esc(text: str) -> str:
    import html
    return html.escape(text)

