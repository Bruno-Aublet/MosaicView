"""
modules/qt/canvas_overlay_qt.py — Texte rouge centré sur le canvas (overlay de progression)

Mécanisme unique des textes de progression (chargement, rotation, conversion,
import web, sauvegarde…) et de leur lien « Annuler » (web_import_qt._show_cancel_item).

Fonctions publiques :
  show_canvas_text(canvas, text, item_holder)  — crée ou met à jour le texte
  hide_canvas_text(canvas, item_holder)        — supprime le texte
  attach_overlay_label(canvas, lbl, anchor)    — inscrit un label annexe (lien
                                                 Annuler) sous le texte `anchor`

Plusieurs opérations peuvent tourner en même temps sur un même widget (deux
imports web, une numérisation et un chargement, une sauvegarde et un import…) :
leurs textes sont empilés les uns sous les autres, l'ensemble restant centré
verticalement (_layout_overlays), au lieu d'être tous centrés au même endroit,
où ils se superposeraient.
"""

from PySide6.QtWidgets import QLabel
from PySide6.QtCore import Qt

from modules.qt.font_manager_qt import get_current_font as _get_current_font

# Espace vertical entre deux textes empilés (lien Annuler compris)
_OVERLAY_GAP = 12


def _alive(lbl) -> bool:
    if lbl is None:
        return False
    import shiboken6
    return shiboken6.isValid(lbl)


def _overlay_list(canvas) -> list:
    """Textes de progression affichés sur `canvas`, dans l'ordre d'apparition
    (attribut dynamique du widget, chaque widget a sa propre pile)."""
    labels = getattr(canvas, '_mv_overlay_labels', None)
    if labels is None:
        labels = canvas._mv_overlay_labels = []
    return labels


def _layout_overlays(canvas) -> None:
    """Empile les textes de progression de `canvas`, chacun suivi de son lien
    Annuler éventuel, et centre l'ensemble verticalement. Les textes déjà
    détruits sont retirés de la pile au passage."""
    labels = [lbl for lbl in _overlay_list(canvas) if _alive(lbl)]
    canvas._mv_overlay_labels = labels
    if not labels:
        return

    def _annex(lbl):
        annex = getattr(lbl, '_mv_annex', None)
        return annex if _alive(annex) and annex.isVisible() else None

    heights = []
    for lbl in labels:
        annex = _annex(lbl)
        heights.append(lbl.height() + (annex.height() if annex else 0))
    total = sum(heights) + _OVERLAY_GAP * (len(labels) - 1)
    y = max(0, (canvas.rect().height() - total) // 2)
    for lbl, h in zip(labels, heights):
        lbl.move(0, y)
        annex = _annex(lbl)
        if annex:
            annex.move(0, y + lbl.height())
        y += h + _OVERLAY_GAP


def show_canvas_text(canvas, text: str, item_holder: list) -> None:
    """Crée ou met à jour le label rouge sur le viewport du canvas, empilé
    avec les autres textes de progression du même widget.

    Le label est enfant du viewport (pas de la scène) : il reste fixe
    visuellement même quand la mosaïque défile, et survit à une
    reconstruction de la mosaïque.

    Args:
        canvas:      QGraphicsView (le canvas mosaïque) ou tout autre widget
        text:        texte déjà traduit et formaté à afficher
        item_holder: liste à 1 élément [label | None] — persistance entre appels.
    """
    lbl = item_holder[0] if item_holder else None
    if lbl is None or not isinstance(lbl, QLabel) or not _alive(lbl):
        lbl = QLabel(canvas)
        lbl.setStyleSheet("color: rgb(220, 0, 0); background: transparent;")
        lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setWordWrap(True)
        if item_holder:
            item_holder[0] = lbl
        else:
            item_holder.append(lbl)
        _overlay_list(canvas).append(lbl)

    lbl.setFont(_get_current_font(24, bold=True))
    lbl.setText(text)

    vr = canvas.rect()
    lbl.setFixedWidth(vr.width())
    lbl.adjustSize()
    lbl.show()
    lbl.raise_()
    _layout_overlays(canvas)


def attach_overlay_label(canvas, lbl, anchor=None) -> None:
    """Inscrit `lbl` (déjà dimensionné, ex. le lien Annuler) dans la pile de
    `canvas` : placé juste sous le texte `anchor` s'il est donné et encore
    affiché, sinon empilé comme un texte à part entière."""
    if _alive(anchor) and anchor in _overlay_list(canvas):
        anchor._mv_annex = lbl
        # Un même label n'est jamais à la fois annexe et texte de la pile
        labels = _overlay_list(canvas)
        if lbl in labels:
            labels.remove(lbl)
    elif lbl not in _overlay_list(canvas):
        _overlay_list(canvas).append(lbl)
    _layout_overlays(canvas)


def hide_canvas_text(canvas, item_holder: list) -> None:
    """Cache et détruit le label (texte de progression ou lien Annuler), puis
    réempile les textes restants du widget."""
    if not item_holder:
        return
    lbl = item_holder[0]
    if lbl is None:
        return
    item_holder[0] = None
    labels = _overlay_list(canvas)
    if lbl in labels:
        labels.remove(lbl)
    try:
        lbl.hide()
        lbl.deleteLater()
    except Exception:
        pass
    _layout_overlays(canvas)
