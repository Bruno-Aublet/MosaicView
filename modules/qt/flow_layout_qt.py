"""
modules/qt/flow_layout_qt.py — Disposition « en flux » : des widgets placés de
gauche à droite, en rangées centrées, qui passent à la rangée suivante quand
la largeur manque (ligne de boutons de la Bibliothèque : ses libellés
s'allongent selon la langue et la taille de police).

Qt n'en fournit pas : QHBoxLayout garde tout sur une ligne et impose sa
largeur minimale à la fenêtre. Calcul des rangées : flow_rows, sans Qt.
"""

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout


def flow_rows(widths, available, spacing):
    """Indices des éléments de largeurs `widths`, groupés par rangée de
    largeur au plus `available`, `spacing` entre deux éléments. Un élément
    plus large que la place disponible forme seul sa rangée (jamais coupé
    ni perdu)."""
    rows, row, used = [], [], 0
    for index, width in enumerate(widths):
        needed = width if not row else used + spacing + width
        if row and needed > available:
            rows.append(row)
            row, used = [index], width
        else:
            row.append(index)
            used = needed
    if row:
        rows.append(row)
    return rows


class FlowLayout(QLayout):
    """Rangées centrées de widgets, à la largeur de leur sizeHint, hauteur
    suivant le nombre de rangées (heightForWidth). Un widget masqué ne prend
    pas de place : la ligne de boutons change selon qu'une base est ouverte.
    Mise en page refaite par Qt à chaque redimensionnement, et quand un
    libellé change (langue, police) puisque la taille de son bouton change.
    Fixe la hauteur minimale du widget qui la porte (_follow_row_count) :
    à poser sur un widget dont elle règle seule la hauteur minimale."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        # Nombre de rangées de la dernière mise en page (_follow_row_count)
        self._row_count = None
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        # Toute la largeur offerte, où les rangées sont centrées (placée dans
        # une rangée à côté d'autres widgets, elle prend la place restante)
        return Qt.Orientation.Horizontal

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)
        widths = [item.sizeHint().width() for item in self._visible_items()]
        row_count = len(flow_rows(widths, rect.width(), self._spacing))
        if row_count == self._row_count:
            return
        self._row_count = row_count
        self._follow_row_count()

    def _follow_row_count(self):
        """Nombre de rangées changé : le widget qui porte la disposition
        (barre de boutons) reçoit pour hauteur minimale celle qu'il réclame à
        sa largeur actuelle, et sa taille préférée est recalculée. Pendant un
        redimensionnement à la souris, la fenêtre se fie sinon à la taille
        préférée calculée une fois, sans redemander la hauteur pour la
        nouvelle largeur : les rangées en plus débordaient sous les widgets
        suivants. Appelée seulement à un changement du nombre de rangées :
        jamais de recalcul à chaque pixel, ni de boucle de mise en page."""
        widget = self.parentWidget()
        if widget is None:
            return
        needed = widget.heightForWidth(widget.width())
        if needed > 0:
            widget.setMinimumHeight(needed)
        widget.updateGeometry()

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        """Le plus large des widgets : la fenêtre peut se rétrécir jusqu'à
        lui, les autres passant à la ligne."""
        size = QSize()
        for item in self._visible_items():
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _visible_items(self):
        # isEmpty : vrai pour un widget masqué
        return [item for item in self._items if not item.isEmpty()]

    def _do_layout(self, rect, apply):
        """Place les widgets dans `rect` (apply) et rend la hauteur occupée."""
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        items = self._visible_items()
        hints = [item.sizeHint() for item in items]
        rows = flow_rows([hint.width() for hint in hints], area.width(), self._spacing)
        y = area.y()
        for row in rows:
            row_width = sum(hints[i].width() for i in row) + self._spacing * (len(row) - 1)
            row_height = max(hints[i].height() for i in row)
            x = area.x() + max(0, (area.width() - row_width) // 2)
            for i in row:
                if apply:
                    offset = (row_height - hints[i].height()) // 2
                    items[i].setGeometry(QRect(QPoint(x, y + offset), hints[i]))
                x += hints[i].width() + self._spacing
            y += row_height + self._spacing
        used = y - area.y() - (self._spacing if rows else 0)
        return used + margins.top() + margins.bottom()
