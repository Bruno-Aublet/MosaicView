"""
toggle_theme_qt.py — Bascule entre thème clair et sombre.

Usage :
    from modules.qt.toggle_theme_qt import toggle_theme, apply_theme
    toggle_theme(app, state, config, canvas, left_panel)
    apply_theme(app, state, canvas, left_panel)
"""

import ctypes
import sys

from PySide6.QtGui import QColor, QPalette

from modules.qt import state as _state_module
from modules.qt.state import get_current_theme
from modules.qt.config_manager import get_config_manager


def _set_titlebar_dark(window, dark: bool, force_repaint: bool = False) -> None:
    """Demande à Windows de peindre la barre de titre en mode sombre ou clair.
    force_repaint=True : envoie WM_NCACTIVATE pour forcer le repeint de la barre (Windows 10)."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(value), ctypes.sizeof(value),
        )
        if force_repaint:
            WM_NCACTIVATE = 0x0086
            ctypes.windll.user32.SendMessageW(hwnd, WM_NCACTIVATE, 1, 0)
            ctypes.windll.user32.SendMessageW(hwnd, WM_NCACTIVATE, 0, 0)
            ctypes.windll.user32.SendMessageW(hwnd, WM_NCACTIVATE, 1, 0)
    except Exception:
        pass  # sans conséquence : seule la barre de titre Windows garde ses couleurs


def toggle_theme(app, canvas, left_panel, tab_bar=None):
    """Bascule dark_mode, sauvegarde, et applique le thème (app + panneau actif)."""
    state = _state_module.state
    state.dark_mode = not state.dark_mode
    get_config_manager().set_dark_mode(state.dark_mode)
    apply_app_theme(app)
    apply_theme(app, canvas, left_panel, tab_bar, rebuild=False)


def apply_app_theme(app):
    """Applique la partie du thème commune à toute l'application (stylesheet
    global QApplication, palette, barre de titre Windows). À appeler UNE SEULE FOIS par changement de thème,
    quel que soit le nombre de panneaux — contrairement à apply_theme() qui
    est spécifique à chaque panneau."""
    theme   = get_current_theme()
    bg         = theme["bg"]
    toolbar    = theme["toolbar_bg"]
    text       = theme["text"]
    sep        = theme["separator"]
    tip_bg     = theme["tooltip_bg"]
    tip_fg     = theme["tooltip_fg"]
    sel_bg     = "#3a7bd5"
    from modules.qt.font_manager_qt import get_current_font
    cur_font   = get_current_font(9)
    tip_font   = cur_font.family()
    menu_font  = cur_font.family()

    # Palette Qt — contrôle les couleurs natives de Fusion (boutons radio, checkboxes, etc.).
    # Posée AVANT la feuille de style : setStyleSheet fige la palette alors
    # courante sur les widgets sans couleur dans la feuille (bouton « … » de
    # la barre de menus), qui garderaient sinon les couleurs du thème précédent.
    entry = theme["entry_bg"]
    palette = QPalette()
    palette.setColor(QPalette.Window,          QColor(bg))
    palette.setColor(QPalette.WindowText,      QColor(text))
    palette.setColor(QPalette.Base,            QColor(entry))
    palette.setColor(QPalette.AlternateBase,   QColor(toolbar))
    palette.setColor(QPalette.Text,            QColor(text))
    palette.setColor(QPalette.ButtonText,      QColor(text))
    palette.setColor(QPalette.Button,          QColor(toolbar))
    palette.setColor(QPalette.Highlight,       QColor(sel_bg))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    app.setStyleSheet(f"""
        QMainWindow {{ background: {bg}; }}
        #leftPanel  {{ background: {toolbar}; }}
        #mainSplitter::handle {{ background: {sep}; width: 3px; }}
        #mainSplitter::handle:hover {{ background: {theme["link"]}; }}
        QMenuBar {{
            background-color: {toolbar};
            color: {text};
            font-family: "{menu_font}";
            font-size: 9pt;
        }}
        QMenuBar::item:selected {{
            background-color: {sel_bg};
            color: #ffffff;
        }}
        QToolTip {{
            background-color: {tip_bg};
            color: {tip_fg};
            border: 1px solid {sep};
            padding: 3px 5px;
            font-family: "{tip_font}";
            font-size: 9pt;
        }}
        QMenu {{
            background-color: {toolbar};
            color: {text};
            border: 1px solid {sep};
            font-family: "{menu_font}";
            font-size: 9pt;
        }}
        QMenu::item:selected {{
            background-color: {sel_bg};
            color: #ffffff;
        }}
        QMenu::item:disabled {{
            color: {theme["disabled"]};
        }}
        QMenu::separator {{
            height: 1px;
            background: {sep};
            margin: 2px 4px;
        }}
        QDialog {{
            background-color: {bg};
            color: {text};
        }}
        QDialog QLabel {{
            color: {text};
        }}
        QDialog QPushButton {{
            background-color: {toolbar};
            color: {text};
        }}
        QTextBrowser {{
            background-color: {bg};
            color: {text};
            border: none;
        }}
        QTextBrowser a {{
            color: {theme["link"]};
        }}
        QRadioButton {{
            color: {text};
        }}
        QScrollBar:vertical {{
            background: {bg};
            width: 14px;
            margin: 0px;
        }}
        QScrollBar::handle:vertical {{
            background: {sep};
            min-height: 20px;
            border-radius: 3px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {theme["disabled"]};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QScrollBar:horizontal {{
            background: {bg};
            height: 14px;
            margin: 0px;
        }}
        QScrollBar::handle:horizontal {{
            background: {sep};
            min-width: 20px;
            border-radius: 3px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {theme["disabled"]};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0px;
        }}
    """)

    # Les fenêtres ouvertes suivent le thème par language_signal, émis par
    # MainWindow._toggle_theme (AppWindowMixin._connect_language)
    from PySide6.QtWidgets import QApplication

    # Barre de titre Windows — DWM immédiat, WM_NCACTIVATE après le prochain cycle événementiel
    from PySide6.QtCore import QTimer
    dark = _state_module.state.dark_mode
    visible = [w for w in QApplication.topLevelWidgets() if w.isVisible()]
    for w in visible:
        _set_titlebar_dark(w, dark, force_repaint=False)
    for w in visible:
        QTimer.singleShot(0, lambda w=w: _set_titlebar_dark(w, dark, force_repaint=True))


def apply_theme(app, canvas, left_panel, tab_bar=None, render=True, rebuild=True):
    """Applique la partie du thème spécifique à UN panneau (canvas, left_panel,
    tab_bar). La partie globale à l'application (stylesheet, palette, dialogs
    secondaires) est gérée séparément par apply_app_theme(), à appeler une
    seule fois même en mode split (2 panneaux).
    rebuild=False (bascule du thème) : la mosaïque déjà affichée est
    seulement recolorée (refresh_theme_colors), pas reconstruite."""
    theme      = get_current_theme()
    toolbar    = theme["toolbar_bg"]
    icon_hover = theme["icon_hover"]

    canvas._apply_theme_bg()
    left_panel.setStyleSheet(f"background: {toolbar};")
    # Propage la couleur de survol aux icônes
    from modules.qt.icon_toolbar_qt import IconToolbarQt
    dark = _state_module.state.dark_mode
    theme_name = "dark" if dark else "light"
    for child in left_panel.findChildren(IconToolbarQt):
        child.set_hover_color(icon_hover)
        child.set_slider_theme(theme_name)
    if tab_bar is not None:
        tab_bar.apply_theme()
    if render:
        if rebuild:
            canvas.render_mosaic()
        else:
            canvas.refresh_theme_colors()
