"""
overlay_tooltip_qt.py — Tooltip overlay réutilisable (QLabel enfant d'un QWidget).

Usage :
    tip = OverlayTooltip(parent_widget)
    tip.update_font()                  # à appeler au changement de police
    tip.show_tooltip(html)             # affiche près du curseur (suit la souris automatiquement)
    tip.hide_tooltip()                 # cache

    # Pour suivre la souris sur un widget enfant (ex. QCheckBox dans un QDialog) :
    tip.track(widget)   # installe le suivi automatique Enter/MouseMove/Leave
    tip.untrack(widget) # retire le suivi
"""

from PySide6.QtWidgets import QLabel, QApplication
from PySide6.QtCore import Qt, QObject, QEvent
from PySide6.QtGui import QCursor


class _MouseTracker(QObject):
    """EventFilter interne qui repositionne le tooltip à chaque MouseMove.
    Chaque widget tracké peut avoir son propre texte HTML.
    """

    def __init__(self, overlay):
        super().__init__()
        self._overlay = overlay
        self._html_map: dict = {}   # widget → html
        self._default_html = ""     # utilisé quand un seul widget est tracké (compat)

    def set_html(self, html: str):
        """Définit le texte par défaut (un seul widget tracké)."""
        self._default_html = html
        # Propage aussi à tous les widgets déjà enregistrés sans texte propre
        for w in list(self._html_map):
            if not self._html_map[w]:
                self._html_map[w] = html

    def set_widget_html(self, widget, html: str):
        """Associe un texte HTML à un widget précis."""
        self._html_map[widget] = html

    def remove_widget(self, widget):
        self._html_map.pop(widget, None)

    def eventFilter(self, obj, event):
        t = event.type()
        html = self._html_map.get(obj, self._default_html)
        if t == QEvent.Enter:
            if html:
                self._overlay.show_tooltip(html)
        elif t == QEvent.MouseMove:
            if html and self._overlay._label.isVisible():
                self._overlay._reposition()
        elif t == QEvent.Leave:
            self._overlay.hide_tooltip()
        return False  # ne pas consommer l'événement


class OverlayTooltip:
    """
    Tooltip overlay sous forme de QLabel enfant d'un QWidget parent.
    Transparent aux événements souris, positionné près du curseur.
    """

    MAX_WIDTH = 340

    def __init__(self, parent_widget):
        """
        parent_widget : le widget dans lequel le tooltip sera affiché
                        (viewport() pour un QGraphicsView, le widget lui-même sinon).
        """
        self._parent = parent_widget
        self._label = QLabel(parent_widget)
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(self.MAX_WIDTH)
        self._label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._label.hide()
        self._apply_style()
        self.update_font()
        self._tracker = _MouseTracker(self)
        self._tracked_widgets: list = []

    def _apply_style(self):
        try:
            from modules.qt import state as _st
            dark = getattr(getattr(_st, "state", None), "dark_mode", False)
        except Exception:
            dark = False
        if dark:
            bg, fg, bd = "#3a3a3a", "#eeeeee", "#666666"
        else:
            bg, fg, bd = "#ffffe0", "#000000", "#b0b000"
        self._label.setStyleSheet(
            f"QLabel {{ background: {bg}; color: {fg}; border: 1px solid {bd};"
            f" padding: 4px 6px; border-radius: 3px; }}"
        )

    def apply_theme(self, dark: bool = False):
        """Conservé pour compatibilité — le thème est désormais lu depuis state."""
        self._apply_style()
        self.update_font()

    def update_font(self):
        """Met à jour la police avec get_current_font(). Doit être appelé après _apply_style."""
        try:
            from modules.qt.font_manager_qt import get_current_font
            self._label.setFont(get_current_font())
        except Exception:
            pass

    def track(self, widget, html: str = ""):
        """
        Installe le suivi automatique Enter/MouseMove/Leave sur widget.
        Chaque widget peut avoir son propre texte HTML.
        """
        self._tracker.set_widget_html(widget, html)
        widget.installEventFilter(self._tracker)
        if widget not in self._tracked_widgets:
            self._tracked_widgets.append(widget)

    def set_tracked_html(self, html: str, widget=None):
        """Met à jour le texte d'un widget précis, ou de tous si widget=None."""
        if widget is not None:
            self._tracker.set_widget_html(widget, html)
        else:
            self._tracker.set_html(html)

    def untrack(self, widget):
        """Retire le suivi sur widget."""
        widget.removeEventFilter(self._tracker)
        self._tracker.remove_widget(widget)
        if widget in self._tracked_widgets:
            self._tracked_widgets.remove(widget)

    def _reposition(self):
        """Repositionne le tooltip près du curseur (sans changer le texte)."""
        parent = self._parent
        cp = parent.mapFromGlobal(QCursor.pos())
        offset_x, offset_y = 16, 16
        x = cp.x() + offset_x
        y = cp.y() + offset_y
        w = self._label.width()
        h = self._label.height()
        if x + w > parent.width():
            x = cp.x() - w - 4
        if y + h > parent.height():
            y = cp.y() - h - 4
        self._label.move(max(0, x), max(0, y))

    def show_tooltip(self, html: str):
        """Affiche le tooltip avec le contenu HTML donné, près du curseur."""
        if not html:
            self._label.hide()
            return
        self._label.setText(html)
        # setStyleSheet réinitialise la police — on la réapplique systématiquement
        try:
            from modules.qt.font_manager_qt import get_current_font
            self._label.setFont(get_current_font())
        except Exception:
            pass
        self._label.adjustSize()
        self._reposition()
        self._label.raise_()
        self._label.show()

    def hide_tooltip(self):
        """Cache le tooltip."""
        self._label.hide()
