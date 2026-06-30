import os

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


def zip_compression_kwargs(level: int) -> dict:
    """
    Convertit un niveau de compression 0-9 (réglage utilisateur) en kwargs
    pour zipfile.ZipFile(..., **kwargs).
    0 → ZIP_STORED (pas de compression). 1-9 → ZIP_DEFLATED avec compresslevel.
    """
    import zipfile
    if level <= 0:
        return {"compression": zipfile.ZIP_STORED}
    return {"compression": zipfile.ZIP_DEFLATED, "compresslevel": level}


def safe_join(base, name):
    """
    Joint `name` à `base` en garantissant que le résultat reste à l'intérieur
    de `base`. Préserve les sous-dossiers légitimes (ex. "chapitre1/page01.jpg").

    Protège contre la traversée de répertoire (Zip Slip) : un nom contenant
    "../", un chemin absolu ou une autre lettre de lecteur produit un chemin
    hors de `base`.

    Returns:
        str: le chemin absolu sûr si `name` reste sous `base`.
        None: si `name` tente de sortir de `base` (l'appelant doit ignorer l'entrée).
    """
    base_real = os.path.realpath(base)
    dest = os.path.realpath(os.path.join(base_real, name))
    if dest != base_real and os.path.commonpath([base_real, dest]) != base_real:
        return None
    return dest
