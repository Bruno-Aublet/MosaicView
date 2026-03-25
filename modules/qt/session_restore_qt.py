"""
modules/qt/session_restore_qt.py
Sauvegarde et restauration de l'état de session (géométrie, thème, sidebar).
"""

from PySide6.QtCore import QTimer

from modules.qt.config_manager import get_config_manager


def restore_session(win):
    """
    Restaure l'état de session depuis la config.
    À appeler après construction complète de la fenêtre (via QTimer.singleShot).

    win : MainWindow
    """
    def _restore():
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        cfg = get_config_manager()

        # Thème (appliqué avant show() pour éviter tout flash)
        from modules.qt.toggle_theme_qt import apply_theme
        if cfg.get_dark_mode():
            win._state.dark_mode = True
        apply_theme(app, win._canvas, win._left_panel, win._tab_bar, render=False)

        # Affichage : maximized, normal ou plein écran
        from PySide6.QtCore import Qt
        if cfg.get_maximized() and not win.isFullScreen():
            win.showMaximized()
        else:
            win.show()

        # Sidebar repliée
        if cfg.get_sidebar_collapsed():
            # _sidebar_visible démarre à True → _toggle_sidebar le passe à False
            win._toggle_sidebar()

        # Largeur de la colonne d'icônes
        saved_w = cfg.get_buttons_column_width()
        panel = win._panel
        if saved_w and hasattr(panel, "_splitter"):
            panel._update_splitter_constraints(panel._icon_toolbar._size_index)
            total = panel._splitter.width()
            panel._splitter.setSizes([saved_w, max(0, total - saved_w)])
            panel._icon_toolbar.adapt_cols_to_width(saved_w)

    QTimer.singleShot(50, _restore)


def save_session(win):
    """
    Sauvegarde la géométrie et l'état courant dans la config.
    À appeler dans closeEvent de MainWindow.

    win : MainWindow
    """
    cfg = get_config_manager()

    if win.isFullScreen():
        # En plein écran : on sauvegarde juste l'état fullscreen, pas la géométrie
        cfg.set_fullscreen(True)
    else:
        cfg.set_fullscreen(False)
        if win.isMaximized():
            cfg.set_maximized(True)
            geo = win.normalGeometry()
        else:
            cfg.set_maximized(False)
            geo = win.geometry()
        cfg.set_window_size(geo.width(), geo.height())
        cfg.set_window_position(geo.x(), geo.y())

    # Largeur de la colonne d'icônes — panneau 1
    panel = win._panel
    if hasattr(panel, "_splitter"):
        cfg.set_buttons_column_width(panel._splitter.sizes()[0])
    # Largeur de la colonne d'icônes — panneau 2
    panel2 = getattr(win, "_panel2", None)
    if panel2 is not None and hasattr(panel2, "_splitter"):
        cfg.set_buttons_column_width_panel2(panel2._splitter.sizes()[0])


def reset_to_defaults(win):
    """
    Réinitialise la fenêtre à ses paramètres par défaut.
    À appeler depuis MainWindow._reset_to_defaults().

    win : MainWindow
    """
    cfg = get_config_manager()

    # Quitter le plein écran si actif
    if win.isFullScreen():
        win.showNormal()
        cfg.set_fullscreen(False)

    # Taille et position par défaut
    default_width, default_height = 1240, 830
    screen = win.screen().availableGeometry()
    x = (screen.width() - default_width) // 2
    y = max(0, (screen.height() - default_height) // 2 - 40)
    win.resize(default_width, default_height)
    win.move(x, y)

    # Mode clair si mode sombre actif
    if win._state.dark_mode:
        win._toggle_theme()

    # Rabattre la colonne d'icônes si elle est visible — tous les panneaux
    for p in win._all_panels():
        if p._sidebar_visible:
            p._toggle_sidebar()

    # Taille des icônes et vignettes — tous les panneaux
    panels = [win._panel]
    if getattr(win, '_panel2', None) is not None:
        panels.append(win._panel2)
    for p in panels:
        if p._icon_toolbar._size_index != 0:
            p._icon_toolbar._size_index = 0
            p._icon_toolbar._apply_size_change()
        if p._state.current_thumb_size != 1:
            p._apply_thumb_size(1, save=False)
            p._icon_toolbar.set_thumb_size_index(1)

    # Taille de police à 0
    if cfg.get_font_size_offset() != 0:
        cfg.set_font_size_offset(0, save=False)
        for p in win._all_panels():
            p._reload_ui_fonts()

    # Langue : détection automatique (langue système)
    system_lang = win._loc.detect_system_language()
    if system_lang:
        win._on_language_change(system_lang)
        cfg.set_language(None, save=False)

    # Ratio split inter-panneaux : remettre à 50/50
    if getattr(win, '_split_active', False):
        total = win._panels_splitter.width()
        win._panels_splitter.setSizes([total // 2, total - total // 2])
        cfg.set_split_ratio(0.5)

    # Largeur colonne d'icônes : remettre à la valeur par défaut — tous les panneaux
    from modules.qt.icon_toolbar_qt import ICON_SIZE_LEVELS, ICON_PAD
    icon_sz, cols = ICON_SIZE_LEVELS[0]  # taille maximale des icônes = index 0
    default_col_w = max(cols * (icon_sz + ICON_PAD) + 2 * ICON_PAD + 4, 210)
    for p in win._all_panels():
        if hasattr(p, "_splitter"):
            p._update_splitter_constraints(0)
            total = p._splitter.width()
            p._splitter.setSizes([default_col_w, max(0, total - default_col_w)])
            p._icon_toolbar.adapt_cols_to_width(default_col_w)
    cfg.set_buttons_column_width(default_col_w, save=False)
    cfg.set_buttons_column_width_panel2(default_col_w, save=False)

    # Sauvegarder
    cfg.set_window_size(default_width, default_height, save=False)
    cfg.set_window_position(x, y, save=False)
    cfg.set_maximized(False, save=False)
    cfg.save_config()


def save_sidebar_state(collapsed: bool):
    """
    Sauvegarde l'état de la sidebar dans la config.
    À appeler depuis _toggle_sidebar après avoir mis à jour _sidebar_visible.

    collapsed : True si la sidebar est repliée (non visible)
    """
    get_config_manager().set_sidebar_collapsed(collapsed)
