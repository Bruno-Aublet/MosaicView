"""
modules/qt/canvas_overlay_qt.py — Texte rouge centré sur le canvas (overlay de progression)

Utilisé par : archive_loader.py, import_merge_qt.py, conversion_dialogs_qt.py, resize_dialog_qt.py

Fonctions publiques :
  show_canvas_text(canvas, text, item_holder)  — crée ou met à jour le texte
  hide_canvas_text(canvas, item_holder)        — supprime le texte de la scène
"""

from PySide6.QtWidgets import QLabel
from PySide6.QtCore import Qt

from modules.qt.font_manager_qt import get_current_font as _get_current_font


def show_canvas_text(canvas, text: str, item_holder: list) -> None:
    """Crée ou met à jour le label rouge centré sur le viewport du canvas.

    Le label est enfant du viewport (pas de la scène) : il reste fixe
    visuellement même quand la mosaïque défile.

    Args:
        canvas:      QGraphicsView (le canvas mosaïque)
        text:        texte déjà traduit et formaté à afficher
        item_holder: liste à 1 élément [label | None] — persistance entre appels.
    """
    viewport = canvas.viewport()

    lbl = item_holder[0] if item_holder else None
    if lbl is None or not isinstance(lbl, QLabel):
        lbl = QLabel(viewport)
        lbl.setStyleSheet("color: rgb(220, 0, 0); background: transparent;")
        lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setWordWrap(True)
        lbl.raise_()
        if item_holder:
            item_holder[0] = lbl
        else:
            item_holder.append(lbl)

    lbl.setFont(_get_current_font(24, bold=True))
    lbl.setText(text)

    vr = viewport.rect()
    lbl.setFixedWidth(vr.width())
    lbl.adjustSize()
    lbl.move(0, (vr.height() - lbl.height()) // 2)
    lbl.show()


def hide_canvas_text(canvas, item_holder: list) -> None:
    """Cache et détruit le label overlay."""
    if not item_holder:
        return
    lbl = item_holder[0]
    if lbl is None:
        return
    try:
        lbl.hide()
        lbl.deleteLater()
    except Exception:
        pass
    item_holder[0] = None
