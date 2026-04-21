"""
toggle_theme_qt.py — Bascule entre thème clair et sombre (version PySide6).
Analogue à modules/toggle_theme.py (tkinter).

Usage :
    from modules.qt.toggle_theme_qt import toggle_theme, apply_theme
    toggle_theme(app, state, config, canvas, left_panel)
    apply_theme(app, state, canvas, left_panel)
"""

from PySide6.QtGui import QColor, QPalette

from modules.qt import state as _state_module
from modules.qt.state import get_current_theme
from modules.qt.config_manager import get_config_manager


def toggle_theme(app, canvas, left_panel, tab_bar=None):
    """Bascule dark_mode, sauvegarde, et applique le thème."""
    state = _state_module.state
    state.dark_mode = not state.dark_mode
    get_config_manager().set_dark_mode(state.dark_mode)
    apply_theme(app, canvas, left_panel, tab_bar)


def apply_theme(app, canvas, left_panel, tab_bar=None, render=True):
    """Applique le thème courant (depuis state.dark_mode) à l'application Qt."""
    theme   = get_current_theme()
    bg         = theme["bg"]
    toolbar    = theme["toolbar_bg"]
    text       = theme["text"]
    sep        = theme["separator"]
    tip_bg     = theme["tooltip_bg"]
    tip_fg     = theme["tooltip_fg"]
    entry      = theme["entry_bg"]
    icon_hover = theme["icon_hover"]
    sel_bg     = "#3a7bd5"
    from modules.qt.font_manager_qt import get_current_font
    cur_font   = get_current_font(9)
    tip_font   = cur_font.family()
    menu_font  = cur_font.family()

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

    # Palette Qt — contrôle les couleurs natives de Fusion (boutons radio, checkboxes, etc.)
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
        canvas.render_mosaic()

    # Met à jour toutes les fenêtres ouvertes qui gèrent le thème
    from modules.qt.license_dialog_qt import _LicenseDialog, _FullLicenseDialog
    from modules.qt.changelog_dialog_qt import _ChangelogDialog
    from modules.qt.batch_metadata_dialog_qt import _MetadataConfirmDialog, _MetadataSummaryDialog
    from modules.qt.batch_drop_dialog_qt import BatchDropDialog
    from modules.qt.comicvine_dialog_qt import _ComicVineDialog
    from modules.qt.comicvine_apikey_dialog_qt import _ApiKeyDialog
    from modules.qt.nfo_dialog_qt import _NfoDialog
    from modules.qt.update_checker_qt import _UpdateDialog
    from modules.qt.user_guide_qt import _HelpDialog
    from modules.qt.donation_dialog_qt import _DonationDialog
    from modules.qt.icon_toolbar_qt import _IconConfigDialog
    from modules.qt.panel_widget import _BookmarkPopup
    from PySide6.QtWidgets import QApplication
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, (_LicenseDialog, _FullLicenseDialog, _ChangelogDialog,
                                _MetadataConfirmDialog, _MetadataSummaryDialog, BatchDropDialog,
                                _ComicVineDialog, _ApiKeyDialog,
                                _NfoDialog, _UpdateDialog, _HelpDialog,
                                _DonationDialog, _IconConfigDialog, _BookmarkPopup)):
            widget._retranslate()
