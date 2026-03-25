"""
modules/qt/canvas_overlay_qt.py — Texte rouge centré sur le canvas (overlay de progression)

Utilisé par : archive_loader.py, import_merge_qt.py, conversion_dialogs_qt.py, resize_dialog_qt.py

Fonctions publiques :
  show_canvas_text(canvas, text, item_holder)  — crée ou met à jour le texte
  hide_canvas_text(canvas, item_holder)        — supprime le texte de la scène
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextBlockFormat, QTextCursor
from shiboken6 import isValid

from modules.qt.font_manager_qt import get_current_font as _get_current_font


def show_canvas_text(canvas, text: str, item_holder: list) -> None:
    """Crée ou met à jour le texte rouge centré sur le canvas.

    Args:
        canvas:      QGraphicsView (le canvas mosaïque)
        text:        texte déjà traduit et formaté à afficher
        item_holder: liste à 1 élément [item | None] — persistance entre appels.
                     Passer une liste vide [] à la première création ; la fonction
                     y insère l'item créé.
    """
    scene = canvas.scene()

    item = item_holder[0] if item_holder else None
    if item is None or not isValid(item):
        item = scene.addText("")
        item.setDefaultTextColor(QColor(220, 0, 0))
        item.setZValue(1000)
        if item_holder:
            item_holder[0] = item
        else:
            item_holder.append(item)

    item.setFont(_get_current_font(24, bold=True))
    item.setPlainText(text)
    vr_width = canvas.viewport().rect().width()
    item.setTextWidth(max(vr_width, item.boundingRect().width()))

    # Centrage horizontal du texte dans son bloc
    doc = item.document()
    cursor = QTextCursor(doc)
    cursor.select(QTextCursor.Document)
    fmt = QTextBlockFormat()
    fmt.setAlignment(Qt.AlignHCenter)
    cursor.mergeBlockFormat(fmt)

    # Centrage de l'item dans la vue (coordonnées de scène)
    vr = canvas.viewport().rect()
    br = item.boundingRect()
    center_scene = canvas.mapToScene(vr.center())
    item.setPos(center_scene.x() - br.width() / 2, center_scene.y() - br.height() / 2)


def hide_canvas_text(canvas, item_holder: list) -> None:
    """Supprime le texte de la scène et remet item_holder[0] à None."""
    if not item_holder:
        return
    item = item_holder[0]
    if item is None:
        return
    try:
        if isValid(item) and item.scene() is canvas.scene():
            canvas.scene().removeItem(item)
    except Exception:
        pass
    item_holder[0] = None
