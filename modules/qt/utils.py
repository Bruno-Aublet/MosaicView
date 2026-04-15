from PySide6.QtWidgets import QSlider, QMenu
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtCore import Qt


class FocusSlider(QSlider):
    """QSlider avec bordure de focus visible."""

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.hasFocus():
            painter = QPainter(self)
            painter.setPen(QPen(QColor("#888888"), 2))
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))


def setup_text_browser_context_menu(browser):
    """
    Remplace le menu contextuel natif (anglais) d'un QTextBrowser
    par un menu traduit avec Copier / Tout sélectionner.
    """
    browser.setContextMenuPolicy(Qt.CustomContextMenu)

    def _show_menu(pos):
        from modules.qt.localization import _
        from modules.qt.font_manager_qt import get_current_font
        font = get_current_font(9)
        menu = QMenu(browser)
        menu.setFont(font)
        menu.setStyleSheet(
            f'QMenu {{ font-family: "{font.family()}"; font-size: {font.pointSize()}pt; }}'
        )
        act_copy = menu.addAction(_("buttons.copy"))
        act_copy.setEnabled(browser.textCursor().hasSelection())
        act_copy.triggered.connect(browser.copy)
        menu.addSeparator()
        act_select_all = menu.addAction(_("menu.select_all"))
        act_select_all.triggered.connect(browser.selectAll)
        menu.exec(browser.mapToGlobal(pos))

    browser.customContextMenuRequested.connect(_show_menu)


def format_file_size(size_bytes):
    """
    Convertit une taille en octets en format lisible (o, Ko, Mo, Go, To).

    Args:
        size_bytes: Taille en octets (int)

    Returns:
        str: Taille formatée (ex: "1.5 Mo")
    """
    if size_bytes < 1024:
        return f"{size_bytes} o"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} Ko"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} Mo"
    elif size_bytes < 1024 * 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} Go"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024 * 1024):.2f} To"
