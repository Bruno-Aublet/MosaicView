"""
modules/qt/keyboard_nav_qt.py
Navigation clavier TAB entre zones pour MosaicView Qt.

Usage dans MainWindow :
    from modules.qt.keyboard_nav_qt import ZoneTabNavigator
    self._tab_nav = ZoneTabNavigator(
        left_panel   = self._left_panel,
        menubar      = self._menubar,
        tab_bar      = self._tab_bar,
        canvas       = self._canvas,
        icon_toolbar = self._icon_toolbar,
        state        = self._state,
    )
    # Dans focusNextPrevChild :
    return self._tab_nav.focus_next_prev(next_)
    # Dans eventFilter (installé sur QApplication) :
    return self._tab_nav.key_filter(obj, event)
"""

from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import QApplication


def _is_descendant(widget, ancestor) -> bool:
    w = widget
    while w is not None:
        if w is ancestor:
            return True
        w = w.parent()
    return False


class ZoneTabNavigator:
    """Gère le cycle TAB entre les zones principales de la fenêtre."""

    def __init__(self, *, get_active_panel, get_other_panel=None, set_active_panel=None):
        self._get_active_panel = get_active_panel
        self._get_other_panel  = get_other_panel   # callable → PanelWidget | None
        self._set_active_panel = set_active_panel  # callable(panel)
        self._menubar_was_active = False  # True quand la menubar avait le focus

    def _panel(self):
        return self._get_active_panel()

    def _zones(self):
        p = self._panel()
        return [p._left_panel, p._menubar, p._tab_bar, p._canvas]

    def _current_zone_index(self, zones) -> int:
        """Retourne l'index de la zone qui a le focus, ou -1."""
        # Cas spécial : menubar activée via setActiveAction
        if self._panel()._menubar.activeAction() is not None:
            return zones.index(self._panel()._menubar)
        focused = QApplication.focusWidget()
        if focused is not None:
            for i, zone in enumerate(zones):
                if _is_descendant(focused, zone):
                    return i
        return -1

    def _focus_zone(self, zone) -> bool:
        """Tente de donner le focus à la zone. Retourne False si la zone est vide."""
        p = self._panel()
        if zone is p._left_panel:
            first = p._icon_toolbar.get_first_icon() if p._icon_toolbar else None
            (first or p._left_panel).setFocus()
            return True
        elif zone is p._menubar:
            actions = p._menubar.actions()
            if len(actions) > 1:
                from PySide6.QtCore import QTimer, QCoreApplication
                from PySide6.QtGui import QKeyEvent
                mb = p._menubar
                act = actions[1]
                mb.setFocus()
                mb.setActiveAction(act)
                def _close_menu(_act=act):
                    try:
                        from shiboken6 import isValid
                        if not isValid(_act):
                            return
                        menu = _act.menu()
                    except Exception:
                        return
                    if menu and menu.isVisible():
                        fake_esc = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_Escape, Qt.NoModifier)
                        QCoreApplication.sendEvent(menu, fake_esc)
                    mb.setFocus()
                QTimer.singleShot(0, _close_menu)
                return True
            elif actions:
                p._menubar.setFocus()
                p._menubar.setActiveAction(actions[0])
                return True
            return False
        elif zone is p._tab_bar:
            btn = p._tab_bar._btn_mosaic or p._tab_bar._btn_metadata
            if btn:
                btn.setFocus()
                return True
            return False
        elif zone is p._canvas:
            if p._state.images_data:
                p._canvas.setFocus()
                if p._canvas._focused_idx is None and p._canvas._items:
                    p._canvas._set_focus(0)
                    p._canvas._scroll_to(p._canvas._items[0])
                return True
            return False
        return False

    def focus_next_prev(self, next_: bool) -> bool:
        """
        Cycle TAB/Shift+TAB entre zones : icônes → menubar → onglets → canvas.
        Si un second panneau existe et qu'on boucle, bascule vers ce panneau.
        À appeler depuis focusNextPrevChild() de la MainWindow.
        """
        zones = self._zones()
        current = self._current_zone_index(zones)
        step = 1 if next_ else -1
        next_idx = (current + step) % len(zones)

        self._panel()._menubar.setActiveAction(None)

        # Cherche la prochaine zone focusable
        other_panel = self._get_other_panel() if self._get_other_panel else None
        for i in range(len(zones)):
            # Wrap-around : next_idx est passé "derrière" current (ou égal après avoir avancé)
            is_wrap = (next_ and next_idx <= current) or (not next_ and next_idx >= current)
            if is_wrap and other_panel is not None and self._set_active_panel:
                self._set_active_panel(other_panel)
                new_zones = self._zones()
                start_idx = 0 if next_ else len(new_zones) - 1
                for j in range(len(new_zones)):
                    idx = (start_idx + j * step) % len(new_zones)
                    if self._focus_zone(new_zones[idx]):
                        return True
                return True
            if self._focus_zone(zones[next_idx]):
                return True
            next_idx = (next_idx + step) % len(zones)

        return True

    def _is_in_menubar(self, obj) -> bool:
        """Retourne True si obj est la menubar du panneau actif ou un de ses QMenu."""
        from PySide6.QtWidgets import QMenu
        menubar = self._panel()._menubar
        if obj is menubar:
            return True
        if isinstance(obj, QMenu):
            w = obj
            while w is not None:
                if w is menubar:
                    return True
                w = w.parent()
        return False

    def key_filter(self, obj, event) -> bool:
        """
        Event filter à installer sur QApplication.
        Intercepte TAB/Shift+TAB dans la menubar et ses sous-menus.
        À appeler depuis eventFilter() de la MainWindow.
        """
        if event.type() == QEvent.KeyPress and self._is_in_menubar(obj):
            key = event.key()
            if key == Qt.Key_Tab:
                self.focus_next_prev(True)
                return True
            if key == Qt.Key_Backtab:
                self.focus_next_prev(False)
                return True
            from PySide6.QtWidgets import QMenu
            if key == Qt.Key_Space and isinstance(obj, QMenu):
                act = obj.activeAction()
                if act and act.isEnabled() and not act.menu():
                    act.trigger()
                    top = obj
                    while isinstance(top.parentWidget(), QMenu):
                        top = top.parentWidget()
                    top.close()
                    return True
        return False
