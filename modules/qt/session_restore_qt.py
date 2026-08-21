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
        from modules.qt.toggle_theme_qt import apply_app_theme, apply_theme
        if cfg.get_dark_mode():
            win._state.dark_mode = True
        apply_app_theme(app)
        apply_theme(app, win._canvas, win._left_panel, win._tab_bar, render=False)
        win._active_panel._update_status_bar()

        # Sidebar repliée et minimap affichée — appliqué AVANT show() : sinon
        # le premier calcul de layout au show() se base sur la colonne
        # d'icônes encore ouverte par défaut, imposant un minimumSize plus
        # large que la géométrie sauvegardée et forçant la fenêtre à
        # s'agrandir au-delà de la taille demandée juste en dessous.
        if cfg.get_sidebar_collapsed():
            # _sidebar_visible démarre à True → _toggle_sidebar le passe à False
            win._toggle_sidebar()
        if cfg.get_minimap_visible():
            win._panel._toggle_minimap()

        # Affichage : maximized, normal ou plein écran
        from PySide6.QtCore import Qt
        if cfg.get_maximized() and not win.isFullScreen():
            win.showMaximized()
        else:
            win.show()

        # Barre de titre Windows — après show(), WM_NCACTIVATE force le repeint sans vider l'écran
        if cfg.get_dark_mode():
            from modules.qt.toggle_theme_qt import _set_titlebar_dark
            QTimer.singleShot(200, lambda: _set_titlebar_dark(win, True, force_repaint=True))

        # Largeur de la colonne d'icônes
        saved_w = cfg.get_buttons_column_width()
        panel = win._panel
        if hasattr(panel, "_splitter"):
            panel._update_splitter_constraints(panel._icon_toolbar._size_index)
            if saved_w:
                if panel._sidebar_visible:
                    # Clamper à min/max courants : une largeur sauvegardée avec
                    # une taille d'icône différente peut dépasser le maximum
                    # actuel — sans ça, le splitter place son séparateur à
                    # saved_w alors que _left_panel refuse de dépasser
                    # maximumWidth(), laissant un espace vide entre la colonne
                    # et le séparateur.
                    saved_w = max(panel._left_panel.minimumWidth(),
                                  min(saved_w, panel._left_panel.maximumWidth()))
                    total = panel._splitter.width()
                    panel._splitter.setSizes([saved_w, max(0, total - saved_w)])
                else:
                    # Colonne rabattue au démarrage : ne pas toucher au splitter
                    # (widget caché), mais semer la largeur mémorisée pour que
                    # la prochaine réouverture la restaure.
                    panel._saved_sidebar_width = saved_w
            # Ne pas adapter la grille d'icônes si la colonne est cachée : la
            # largeur lue serait périmée et la grille serait peuplée avec un
            # mauvais nombre de colonnes, dont le minimum (icônes à taille
            # fixe) fausserait ensuite la largeur de réouverture de la colonne.
            if panel._sidebar_visible:
                panel._icon_toolbar.adapt_cols_to_width(panel._left_panel.width())

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

    # Largeur de la colonne d'icônes — tous les panneaux. Si la colonne est
    # rabattue au moment de la fermeture, sa largeur physique vaut 0 : on
    # sauvegarde alors la largeur mémorisée au rabattement (_saved_sidebar_width),
    # sinon la largeur choisie par l'utilisateur serait perdue d'une session à
    # l'autre et la colonne rouvrirait à sa largeur par défaut.
    panel = win._panel
    if hasattr(panel, "_splitter"):
        w1 = panel._splitter.sizes()[0]
        if not panel._sidebar_visible:
            w1 = getattr(panel, "_saved_sidebar_width", 0) or 0
        cfg.set_buttons_column_width(w1)
    panel2 = getattr(win, "_panel2", None)
    if panel2 is not None and hasattr(panel2, "_splitter"):
        w2 = panel2._splitter.sizes()[0]
        if not panel2._sidebar_visible:
            w2 = getattr(panel2, "_saved_sidebar_width", 0) or 0
        cfg.set_buttons_column_width_panel2(w2)


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

    # Rabattre la colonne d'icônes et cacher la minimap si visibles — tous les
    # panneaux. Fait AVANT le resize de la fenêtre plus bas : ces deux éléments
    # imposent une largeur minimale à leur panel qui remonte jusqu'à la
    # MainWindow et bride silencieusement le resize() à cette largeur minimale
    # au lieu de default_width, ce qui fausserait ensuite le calcul du ratio 50/50.
    for p in win._all_panels():
        if p._sidebar_visible:
            p._toggle_sidebar()
        if p._minimap_visible:
            p._toggle_minimap()

    # Taille et position par défaut
    default_width, default_height = 1240, 830
    screen = win.screen().availableGeometry()
    x = (screen.width() - default_width) // 2
    y = max(0, (screen.height() - default_height) // 2 - 40)
    # Libère toute contrainte de largeur minimale héritée (colonnes d'icônes /
    # minimap encore prises en compte dans le minimumSizeHint mis en cache par
    # Qt malgré leur masquage juste au-dessus) — sinon resize() ci-dessous est
    # silencieusement bridé à cette largeur minimale au lieu de default_width.
    win.setMinimumSize(0, 0)
    win.resize(default_width, default_height)
    win.move(x, y)

    # Mode clair si mode sombre actif
    if win._state.dark_mode:
        win._toggle_theme()

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
        p._thumb_size_config().set_thumbnail_size('normal', save=False)

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

    # Largeur colonne d'icônes : remettre à la valeur par défaut — tous les
    # panneaux. La colonne reste repliée (rabattue plus haut) ; sa largeur par
    # défaut ne sera visible qu'à la prochaine réouverture.
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

    # Mode de renumérotation, mode de redressement et taux de compression ZIP
    # par défaut — tous les panneaux
    for p in win._all_panels():
        p._state.renumber_mode = 1
        p._renumber_config().set_renumber_mode(1)
        p._state.straighten_mode = 0
        p._renumber_config().set_straighten_mode(0)
        p._state.sharpness_mode = 0
        p._renumber_config().set_sharpness_mode(0)
        p._zip_compression_config().set_zip_compression_level(0)
        p._update_status_bar()
        p._refresh_toolbar_states()
        # _refresh_toolbar_states() ne rafraîchit que la colonne d'icônes
        # verticale — sans cet appel, l'icône bi-mode straighten/sharpness
        # d'une visionneuse principale déjà ouverte (tooltip + icône affichée
        # pour sharpness) resterait figée sur l'ancien mode malgré le reset
        # de state.straighten_mode/sharpness_mode ci-dessus.
        p._refresh_open_image_viewers()

    # Ratio split inter-panneaux : remettre à 50/50.
    # QSplitter.setSizes() seul se révèle ignoré à ce stade du reset (le
    # splitter garde ses proportions précédentes) ; un vrai double-clic
    # utilisateur sur le séparateur, lui, fonctionne toujours. On simule donc
    # cet événement souris sur le handle du splitter, en différé pour laisser
    # le reste du reset (icônes, sidebar) terminer son propre layout d'abord.
    # cfg.set_split_ratio(0.5) est écrit APRÈS ce double-clic simulé, pas avant :
    # celui-ci déplace le splitter et émet splitterMoved → _save_split_ratio,
    # qui réécrirait sinon la config avec une valeur recalculée depuis les
    # tailles réelles (potentiellement 0.5 ± un arrondi entier) juste après.
    if getattr(win, '_split_active', False):
        def _center_split():
            splitter = win._panels_splitter
            handle = splitter.handle(1)
            from PySide6.QtGui import QMouseEvent
            from PySide6.QtCore import QPointF, Qt as _Qt
            from PySide6.QtWidgets import QApplication as _QApp
            ev = QMouseEvent(QMouseEvent.Type.MouseButtonDblClick, QPointF(handle.rect().center()),
                              _Qt.MouseButton.LeftButton, _Qt.MouseButton.LeftButton, _Qt.KeyboardModifier.NoModifier)
            _QApp.sendEvent(handle, ev)
            cfg.set_split_ratio(0.5)
        QTimer.singleShot(50, _center_split)

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
