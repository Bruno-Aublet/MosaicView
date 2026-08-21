"""
modules/qt/paste_image_tool_qt.py — Outil "Coller une image" (paste_image) de
la barre d'outils flottante de la visionneuse principale (image_viewer_qt.py).

Nouvel outil "Coller une image" de la visionneuse :
colle une image provenant du presse-papiers système (bitmap CF_DIB ou fichier
image unique CF_HDROP, voir modules.qt.clipboard_qt::clipboard_has_single_image/
get_clipboard_single_image, réutilisées telles quelles plutôt que réécrites)
sur la page actuellement affichée. L'image collée apparaît centrée, à une
taille initiale = un quart de la page ; l'utilisateur la déplace, la
redimensionne et la fait tourner librement via des poignées — COMPORTEMENT DE
MANIPULATION IDENTIQUE À L'OUTIL FORMES (shapes_tool_qt.py::ShapeCanvasMixin) :
8 poignées de redimensionnement + 1 poignée de rotation, mêmes conventions
d'angle (sens horaire écran/QPainter, dé-rotation du point cliqué avant
comparaison aux zones). Contrairement aux formes, l'objet posé (_PastedImage)
porte un BITMAP (image PIL source), pas une géométrie vectorielle pure — le
rendu écran dessine un QPixmap tourné/mis à l'échelle au lieu de tracer un
contour, et le rendu final PIL colle un calque RGBA tourné au lieu d'un
ImageDraw.

Pas de panneau d'options flottant dédié (décision explicite utilisateur :
"pas d'autres réglages que ceux déjà donnés") — contrairement
à shapes/text/clone/straighten, cet outil n'a ni couleur, ni épaisseur, ni
mode à régler : seules les poignées (redimensionnement/rotation/déplacement)
et les boutons "Valider"/"Annuler" flottants partagés pilotent le travail en
cours. Même cas que "crop" dans _VALIDATE_KEYS (aucun panneau associé dans
image_viewer_qt.py::_update_validate_btn_state).

Plusieurs images peuvent être collées et accumulées sur la même page avant
validation (comme les formes/le texte) — pas de limite de nombre. Aplatissement
en pixels sur la page à la validation (pattern apply-image-operation variante
A complète, undo/redo unifié, un seul point d'historique).

Point d'entrée UNIQUE pour l'instant : l'icône "Coller" de la barre (grisée/
dégrisée selon le contenu live du presse-papiers, voir _refresh_paste_image_
button_state / QClipboard.dataChanged). Piste secondaire envisagée
(glisser-déposer une page de la mosaïque vers la visionneuse) volontairement
PAS implémentée ici — mais le point d'entrée est isolé à dessein :
ShapeCanvasMixin/_add_pasted_image(pil_img) est le seul point qui crée et
active une nouvelle _PastedImage, indépendant de la provenance de l'image
(presse-papiers aujourd'hui, un futur drop de page demain n'aurait qu'à
appeler cette même méthode avec l'image de la page déposée) — aucune
retouche prévisible au mécanisme de manipulation/validation le jour où le
drag & drop sera ajouté.
"""

import math

from PIL import Image

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QPoint, QRect, QTimer
from PySide6.QtGui import QColor, QCursor, QPen, QPainter

from modules.qt import state as _state_module
from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.shapes_tool_qt import _build_rotate_cursor, _paint_out_of_page_bounds_overlay


# ─────────────────────────────────────────────────────────────────────────────
# Modèle d'une image collée (coordonnées IMAGE, stables face au zoom/pan)
# ─────────────────────────────────────────────────────────────────────────────

class _PastedImage:
    """Une image collée posée sur la page — porte le BITMAP source (PIL RGBA,
    JAMAIS redimensionné en PIL au fil des manipulations : seul le rectangle
    englobant (ix1,iy1,ix2,iy2) et l'angle changent, le pixmap source garde sa
    résolution native jusqu'au rendu final, comme le pixmap de page affichée
    lui-même — voir _ViewerCanvas.paintEvent, même principe de mise à l'échelle
    différée à l'affichage). Coordonnées reconverties en widget à chaque
    paintEvent (voir PasteImageCanvasMixin.paint_pasted_images)."""

    def __init__(self, pil_img: Image.Image, ix1: float, iy1: float,
                 ix2: float, iy2: float, angle: float = 0.0):
        self.pil_img = pil_img.convert("RGBA")
        # (ix1,iy1) / (ix2,iy2) : coin haut-gauche / bas-droit du rectangle
        # englobant, PAS normalisé en interne (redimensionner peut inverser
        # un coin par-dessus l'autre pendant le drag) — normalized_img_rect()
        # ci-dessous renvoie la version normalisée.
        self.ix1, self.iy1 = ix1, iy1
        self.ix2, self.iy2 = ix2, iy2
        # Rotation en degrés, sens horaire, autour du centre du rectangle
        # englobant normalisé — même convention que _Shape.angle
        # (shapes_tool_qt.py), réutilisée à l'identique.
        self.angle = angle

    def center_img(self) -> tuple:
        x1, y1, x2, y2 = self.normalized_img_rect()
        return (x1 + x2) / 2, (y1 + y2) / 2

    def normalized_img_rect(self) -> tuple:
        return (min(self.ix1, self.ix2), min(self.iy1, self.iy2),
                max(self.ix1, self.ix2), max(self.iy1, self.iy2))


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état et interactions souris de l'outil (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class PasteImageCanvasMixin:
    """Hérité par _ViewerCanvas (image_viewer_qt.py) en plus de QLabel — même
    principe que ShapeCanvasMixin (shapes_tool_qt.py), dont la mécanique de
    poignées/rotation/redimensionnement est réutilisée à l'identique (mêmes
    noms de mode 'tl'/'tr'/.../'rotate'/'move', même tolérance de détection),
    appliquée ici à un rectangle englobant de bitmap plutôt qu'à une forme
    vectorielle. Pas de tracé au clic-glisser (contrairement aux formes) :
    une _PastedImage naît déjà posée, centrée sur la page, via
    _add_pasted_image() — seul son redimensionnement/déplacement/rotation
    est un geste utilisateur."""

    _CURSORS = {
        'tl': Qt.SizeFDiagCursor, 'br': Qt.SizeFDiagCursor,
        'tr': Qt.SizeBDiagCursor, 'bl': Qt.SizeBDiagCursor,
        'left': Qt.SizeHorCursor, 'right': Qt.SizeHorCursor,
        'top': Qt.SizeVerCursor,  'bottom': Qt.SizeVerCursor,
        'move': Qt.SizeAllCursor,
        # PAS d'entrée 'rotate' ici : voir shapes_tool_qt.py::
        # _build_rotate_cursor (curseur custom partagé entre les deux
        # outils), résolu explicitement dans paste_image_update_cursor.
    }

    _ROTATE_HANDLE_OFFSET = 24

    def _init_paste_image_state(self):
        self._pasted_images: list[_PastedImage] = []
        self._pasted_image_active: _PastedImage | None = None
        self._paste_resize_mode: str | None = None
        self._paste_resize_original: tuple | None = None
        self._paste_drag_start_widget: QPoint | None = None

    @property
    def has_pasted_images(self) -> bool:
        return bool(self._pasted_images)

    def clear_pasted_images(self):
        self._pasted_images.clear()
        self._pasted_image_active = None
        self._paste_resize_mode = None
        self._paste_resize_original = None
        self.update()

    def _add_pasted_image(self, pil_img: Image.Image):
        """Point d'entrée UNIQUE pour poser une nouvelle image collée sur la
        page — voir docstring de module pour la raison de cette isolation
        (futur drag & drop de page, piste secondaire). Centrée
        sur la page affichée, taille initiale = un quart de la page (moitié
        de chaque dimension), ratio d'aspect de l'image source préservé."""
        state = self._viewer.callbacks.get('state') or _state_module.state
        entry = state.images_data[self._viewer.current_idx]
        if not entry.get('bytes'):
            return
        import io
        try:
            page_img = Image.open(io.BytesIO(entry['bytes']))
            page_w, page_h = page_img.size
        except Exception:
            return

        target_w = page_w / 2
        target_h = page_h / 2
        src_w, src_h = pil_img.size
        if src_w <= 0 or src_h <= 0:
            return
        scale = min(target_w / src_w, target_h / src_h)
        half_w = (src_w * scale) / 2
        half_h = (src_h * scale) / 2
        cx, cy = page_w / 2, page_h / 2

        pasted = _PastedImage(pil_img, cx - half_w, cy - half_h, cx + half_w, cy + half_h)
        self._pasted_images.append(pasted)
        self._pasted_image_active = pasted
        self._viewer._on_paste_image_content_changed()
        self.update()

    # ── Conversion widget <-> image (identique à ShapeCanvasMixin) ───────────

    def _paste_widget_to_image(self, pt: QPoint) -> tuple:
        zoom = self._viewer.zoom_level or 1.0
        ix = (pt.x() - self.display_offset_x) / zoom
        iy = (pt.y() - self.display_offset_y) / zoom
        return ix, iy

    def _paste_image_to_widget(self, ix: float, iy: float) -> QPoint:
        zoom = self._viewer.zoom_level or 1.0
        return QPoint(int(round(self.display_offset_x + ix * zoom)),
                       int(round(self.display_offset_y + iy * zoom)))

    def _paste_widget_rect(self, pasted: _PastedImage) -> tuple:
        x1, y1, x2, y2 = pasted.normalized_img_rect()
        p1 = self._paste_image_to_widget(x1, y1)
        p2 = self._paste_image_to_widget(x2, y2)
        return p1.x(), p1.y(), p2.x(), p2.y()

    def _paste_widget_center(self, pasted: _PastedImage) -> QPoint:
        cix, ciy = pasted.center_img()
        return self._paste_image_to_widget(cix, ciy)

    def _paste_rotate_point_around(self, pt: QPoint, center: QPoint, angle_deg: float) -> QPoint:
        if angle_deg == 0.0:
            return QPoint(pt.x(), pt.y())
        rad = math.radians(angle_deg)
        dx, dy = pt.x() - center.x(), pt.y() - center.y()
        rx = dx * math.cos(rad) - dy * math.sin(rad)
        ry = dx * math.sin(rad) + dy * math.cos(rad)
        return QPoint(int(round(center.x() + rx)), int(round(center.y() + ry)))

    # ── Rendu (appelé depuis _ViewerCanvas.paintEvent) ───────────────────────

    def paint_pasted_images(self, painter):
        from modules.qt.image_viewer_qt import _pil_to_qpixmap
        paste_active = self._viewer._toolbar.active_tool == "paste_image"
        for pasted in self._pasted_images:
            self._paint_one_pasted_image(painter, pasted, _pil_to_qpixmap)
            if paste_active and pasted is self._pasted_image_active:
                self._paint_paste_handles(painter, pasted)

    def _paint_one_pasted_image(self, painter, pasted: _PastedImage, to_qpixmap):
        wx1, wy1, wx2, wy2 = self._paste_widget_rect(pasted)
        w, h = wx2 - wx1, wy2 - wy1
        if w <= 0 or h <= 0:
            return
        pm = to_qpixmap(pasted.pil_img)
        rotated = pasted.angle != 0.0
        if rotated:
            center = self._paste_widget_center(pasted)
            painter.save()
            painter.translate(center)
            painter.rotate(pasted.angle)
            painter.translate(-center.x(), -center.y())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(QRect(wx1, wy1, w, h), pm, pm.rect())
        if rotated:
            painter.restore()

        # Grisage de la portion hors des limites de la page (décision
        # explicite utilisateur — signale ce qui sera tronqué à
        # l'aplatissage). Coins calculés en repère ÉCRAN NON tourné (indépendant
        # du save/translate/rotate déjà refermé ci-dessus) — même convention
        # d'angle que _shape_rotate_point_around (sens horaire QPainter).
        if rotated:
            center = self._paste_widget_center(pasted)
            corners = [self._paste_rotate_point_around(QPoint(x, y), center, pasted.angle)
                       for (x, y) in ((wx1, wy1), (wx2, wy1), (wx2, wy2), (wx1, wy2))]
        else:
            corners = [QPoint(wx1, wy1), QPoint(wx2, wy1), QPoint(wx2, wy2), QPoint(wx1, wy2)]
        _paint_out_of_page_bounds_overlay(painter, self, corners)

    def _paint_paste_handles(self, painter, pasted: _PastedImage):
        wx1, wy1, wx2, wy2 = self._paste_widget_rect(pasted)
        handle_pen = QPen(QColor("red"), 1)
        painter.save()
        painter.setPen(handle_pen)
        painter.setBrush(QColor(255, 255, 255, 220))
        size = 6

        rotated = pasted.angle != 0.0
        if rotated:
            center = self._paste_widget_center(pasted)
            painter.translate(center)
            painter.rotate(pasted.angle)
            painter.translate(-center.x(), -center.y())

        dash_pen = QPen(QColor("red"), 1, Qt.PenStyle.DashLine)
        painter.setPen(dash_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRect(QPoint(wx1, wy1), QPoint(wx2, wy2)))
        painter.setPen(handle_pen)
        painter.setBrush(QColor(255, 255, 255, 220))
        mx, my = (wx1 + wx2) // 2, (wy1 + wy2) // 2
        for hx, hy in ((wx1, wy1), (mx, wy1), (wx2, wy1),
                       (wx1, my), (wx2, my),
                       (wx1, wy2), (mx, wy2), (wx2, wy2)):
            painter.drawRect(hx - size // 2, hy - size // 2, size, size)

        handle_y = wy1 - self._ROTATE_HANDLE_OFFSET
        painter.setPen(handle_pen)
        painter.drawLine(mx, wy1, mx, handle_y)
        painter.setBrush(QColor(255, 255, 255, 220))
        r = size // 2 + 1
        painter.drawEllipse(QPoint(mx, handle_y), r, r)
        painter.restore()

    # ── Détection de zone (poignées / intérieur) — identique à ShapeCanvasMixin ──

    def _paste_resize_mode_at(self, pasted: _PastedImage, pos: QPoint, check_rotate: bool = True) -> str | None:
        wx1, wy1, wx2, wy2 = self._paste_widget_rect(pasted)
        tolerance = 10
        x, y = pos.x(), pos.y()

        mx = (wx1 + wx2) // 2
        if check_rotate:
            handle_center = self._paste_rotate_point_around(
                QPoint(mx, wy1 - self._ROTATE_HANDLE_OFFSET),
                self._paste_widget_center(pasted), pasted.angle)
            if (x - handle_center.x()) ** 2 + (y - handle_center.y()) ** 2 <= tolerance ** 2:
                return 'rotate'

        if pasted.angle != 0.0:
            center = self._paste_widget_center(pasted)
            local = self._paste_rotate_point_around(pos, center, -pasted.angle)
            x, y = local.x(), local.y()

        if abs(x - wx1) <= tolerance and abs(y - wy1) <= tolerance: return 'tl'
        if abs(x - wx2) <= tolerance and abs(y - wy1) <= tolerance: return 'tr'
        if abs(x - wx1) <= tolerance and abs(y - wy2) <= tolerance: return 'bl'
        if abs(x - wx2) <= tolerance and abs(y - wy2) <= tolerance: return 'br'
        if abs(x - wx1) <= tolerance and wy1 <= y <= wy2: return 'left'
        if abs(x - wx2) <= tolerance and wy1 <= y <= wy2: return 'right'
        if abs(y - wy1) <= tolerance and wx1 <= x <= wx2: return 'top'
        if abs(y - wy2) <= tolerance and wx1 <= x <= wx2: return 'bottom'
        if wx1 < x < wx2 and wy1 < y < wy2: return 'move'
        return None

    def _pasted_image_at(self, pos: QPoint) -> "_PastedImage | None":
        for pasted in reversed(self._pasted_images):
            if self._paste_resize_mode_at(pasted, pos, check_rotate=False) is not None:
                return pasted
        return None

    # ── Événements souris (appelés depuis _ViewerCanvas.mousePress/Move/ReleaseEvent) ──

    def paste_image_mouse_press(self, event) -> bool:
        pos = event.position().toPoint()

        if self._pasted_image_active is not None:
            mode = self._paste_resize_mode_at(self._pasted_image_active, pos)
            if mode is not None:
                self._paste_resize_mode = mode
                self._paste_resize_original = (
                    self._pasted_image_active.ix1, self._pasted_image_active.iy1,
                    self._pasted_image_active.ix2, self._pasted_image_active.iy2,
                    self._pasted_image_active.angle)
                self._paste_drag_start_widget = pos
                return True

        hit = self._pasted_image_at(pos)
        if hit is not None:
            self._pasted_image_active = hit
            mode = self._paste_resize_mode_at(hit, pos)
            self._paste_resize_mode = mode
            self._paste_resize_original = (hit.ix1, hit.iy1, hit.ix2, hit.iy2, hit.angle)
            self._paste_drag_start_widget = pos
            self.update()
            return True

        # Zone vide : désélectionne l'image active — pas de nouveau tracé
        # possible ici (contrairement aux formes), une image collée ne naît
        # que via _add_pasted_image().
        self._pasted_image_active = None
        self.update()
        return True

    def paste_image_mouse_move(self, event) -> bool:
        pos = event.position().toPoint()
        if self._paste_resize_mode and self._pasted_image_active is not None \
                and self._paste_resize_original:
            self._apply_paste_resize(pos)
            self.update()
            return True
        return False

    def _apply_paste_resize(self, pos: QPoint):
        pasted = self._pasted_image_active
        ox1, oy1, ox2, oy2, oangle = self._paste_resize_original
        rm = self._paste_resize_mode

        if rm == 'rotate':
            center = self._paste_widget_center(pasted)
            start = self._paste_drag_start_widget
            start_angle = math.degrees(math.atan2(start.y() - center.y(), start.x() - center.x()))
            current_angle = math.degrees(math.atan2(pos.y() - center.y(), pos.x() - center.x()))
            pasted.angle = (oangle + (current_angle - start_angle)) % 360.0
            return

        ix, iy = self._paste_widget_to_image(pos)

        if rm == 'move':
            start_ix, start_iy = self._paste_widget_to_image(self._paste_drag_start_widget)
            dx, dy = ix - start_ix, iy - start_iy
            pasted.ix1, pasted.iy1 = ox1 + dx, oy1 + dy
            pasted.ix2, pasted.iy2 = ox2 + dx, oy2 + dy
            return

        if pasted.angle != 0.0:
            center = self._paste_widget_center(pasted)
            local = self._paste_rotate_point_around(pos, center, -pasted.angle)
            ix, iy = self._paste_widget_to_image(local)

        # Poignées de COIN : redimensionnement à PROPORTIONS CONSERVÉES
        # (décision explicite utilisateur) — seules les 4
        # poignées de BORD (milieu) redimensionnent librement en déformant.
        # Même logique que ShapeCanvasMixin._apply_shape_resize
        # (shapes_tool_qt.py), dupliquée ici plutôt que partagée : les deux
        # mixins n'ont pas de classe de base commune pour porter une méthode
        # utilitaire sans introduire un couplage artificiel entre les deux
        # outils.
        if rm in ('tl', 'tr', 'bl', 'br'):
            orig_w = abs(ox2 - ox1) or 1.0
            orig_h = abs(oy2 - oy1) or 1.0
            aspect = orig_w / orig_h
            fixed_x = ox1 if rm in ('tr', 'br') else ox2
            fixed_y = oy1 if rm in ('bl', 'br') else oy2
            raw_w, raw_h = ix - fixed_x, iy - fixed_y
            sign_x = -1 if rm in ('tl', 'bl') else 1
            sign_y = -1 if rm in ('tl', 'tr') else 1
            if abs(raw_w) >= abs(raw_h) * aspect:
                new_w = max(1.0, abs(raw_w))
                new_h = new_w / aspect
            else:
                new_h = max(1.0, abs(raw_h))
                new_w = new_h * aspect
            x1, y1, x2, y2 = ox1, oy1, ox2, oy2
            if rm == 'tl':   x1, y1 = fixed_x + sign_x * new_w, fixed_y + sign_y * new_h
            elif rm == 'tr': x2, y1 = fixed_x + sign_x * new_w, fixed_y + sign_y * new_h
            elif rm == 'bl': x1, y2 = fixed_x + sign_x * new_w, fixed_y + sign_y * new_h
            elif rm == 'br': x2, y2 = fixed_x + sign_x * new_w, fixed_y + sign_y * new_h
            pasted.ix1, pasted.iy1, pasted.ix2, pasted.iy2 = x1, y1, x2, y2
            return

        x1, y1, x2, y2 = ox1, oy1, ox2, oy2
        if rm == 'left':   x1 = ix
        elif rm == 'right':  x2 = ix
        elif rm == 'top':    y1 = iy
        elif rm == 'bottom': y2 = iy
        pasted.ix1, pasted.iy1, pasted.ix2, pasted.iy2 = x1, y1, x2, y2

    def paste_image_update_cursor(self, event):
        pos = event.position().toPoint()
        mode = None
        if self._pasted_image_active is not None:
            mode = self._paste_resize_mode_at(self._pasted_image_active, pos)
        if mode is None:
            hit = self._pasted_image_at(pos)
            if hit is not None:
                mode = self._paste_resize_mode_at(hit, pos, check_rotate=False)
        if mode == 'rotate':
            self.setCursor(_build_rotate_cursor())
        else:
            self.setCursor(QCursor(self._CURSORS.get(mode, Qt.ArrowCursor)))

    def paste_image_mouse_release(self, event) -> bool:
        if self._paste_resize_mode:
            self._paste_resize_mode = None
            self._paste_resize_original = None
            self._paste_drag_start_widget = None
            self._viewer._on_paste_image_content_changed()
            return True
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — rendu / commit / persistance par page (hérité par ImageViewer)
# ─────────────────────────────────────────────────────────────────────────────

class PasteImageViewerMixin:
    """Hérité par ImageViewer (image_viewer_qt.py) en plus de QDialog — même
    principe que ShapeViewerMixin. Suppose que l'hôte a déjà self._canvas
    (_ViewerCanvas, avec PasteImageCanvasMixin), self.callbacks,
    self.current_idx, et self._pasted_images_by_page (persistance par page,
    initialisée dans ImageViewer.__init__ comme _shapes_by_page)."""

    def _on_paste_image_content_changed(self):
        if self._toolbar.active_tool == "paste_image":
            self._canvas._update_validate_btn_state()
            self._canvas._update_cancel_btn_state()

    # ── Rendu final — toutes les images collées → PIL paste ──────────────────

    def _pasted_images_render_all(self, base_img: Image.Image) -> Image.Image:
        img = base_img.copy()
        for pasted in self._canvas._pasted_images:
            self._paste_one_pasted_image(img, pasted)
        return img

    @staticmethod
    def _paste_one_pasted_image(img: Image.Image, pasted: "_PastedImage"):
        x1, y1, x2, y2 = pasted.normalized_img_rect()
        w = max(1, int(round(x2 - x1)))
        h = max(1, int(round(y2 - y1)))
        resized = pasted.pil_img.resize((w, h), Image.LANCZOS)

        if pasted.angle == 0.0:
            img.paste(resized, (int(round(x1)), int(round(y1))), resized)
            return

        # Même principe que ShapeViewerMixin._draw_rotated_shape
        # (shapes_tool_qt.py) : PIL n'a pas d'équivalent de painter.rotate(),
        # l'image redimensionnée (non tournée) est pivotée sur elle-même
        # (expand=True agrandit le canvas pour ne rien couper) puis collée en
        # alignant son centre sur le même centre (cx, cy) que le rectangle
        # englobant d'origine.
        cx, cy = pasted.center_img()
        # Signe opposé à pasted.angle (sens horaire écran/QPainter) — PIL
        # tourne anti-horaire pour un angle positif, même piège/même fix que
        # _draw_rotated_shape.
        rotated = resized.rotate(-pasted.angle, expand=True, resample=Image.BICUBIC)
        paste_x = int(round(cx - rotated.width / 2))
        paste_y = int(round(cy - rotated.height / 2))
        img.paste(rotated, (paste_x, paste_y), rotated)

    def validate_paste_image(self):
        from modules.qt.dialogs_qt import MsgDialog
        if not self._canvas.has_pasted_images:
            dlg = MsgDialog(
                self,
                "messages.warnings.no_pasted_image.title",
                "messages.warnings.no_pasted_image.message",
            )
            dlg.show_nonmodal()
            return
        self.perform_paste_image()

    def perform_paste_image(self):
        import io as _io
        from modules.qt import state as _state_module
        from modules.qt.entries import save_image_to_bytes
        from modules.qt.dialogs_qt import MsgDialog

        state = self.callbacks.get('state') or _state_module.state
        save_state    = self.callbacks.get("save_state")
        render_mosaic = self.callbacks.get("render_mosaic")
        update_btn    = self.callbacks.get("update_button_text")
        canvas_cb     = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]
            if not entry.get('bytes'):
                dlg = MsgDialog(self._center_parent, "messages.errors.paste_image_failed.title",
                                "messages.errors.paste_image_failed.title")
                dlg.show_nonmodal()
                return

            base_img_raw = Image.open(_io.BytesIO(entry['bytes']))
            if '_orig_mode' not in entry:
                entry['_orig_mode'] = base_img_raw.mode
            base_img = base_img_raw.convert('RGBA')

            if save_state:
                save_state()

            composed = self._pasted_images_render_all(base_img)

            orig_mode = entry.get('_orig_mode', 'RGBA')
            out_img = composed
            # .bmp exclu : Pillow écrit bien un canal alpha 32-bit, mais ne le
            # redétecte pas à la relecture (header BMP classique ambigu sur la
            # présence d'alpha) — transparence non fiable, voir color_depth_tool_qt.py.
            if orig_mode not in ('RGBA', 'LA', 'P') and \
                    entry.get('extension', '').lower() not in (
                        '.png', '.webp', '.avif', '.tiff', '.tif', '.ico'
                    ):
                bg = Image.new('RGB', out_img.size, (255, 255, 255))
                bg.paste(out_img, mask=out_img.split()[3])
                out_img = bg

            entry["img"]   = out_img.copy()
            entry["bytes"] = save_image_to_bytes(entry)
            entry["img"] = None
            entry["_thumbnail"] = None
            entry["large_thumb_pil"] = None
            entry["qt_pixmap_large"] = None
            entry["qt_qimage_large"] = None
            entry["_hash"] = None
            state.modified = True

            from modules.qt.comic_info import get_page_image_index, update_page_entries_in_xml_data
            _pidx = get_page_image_index(state, entry)
            if _pidx is not None:
                update_page_entries_in_xml_data(state, [(_pidx, entry)])
            if save_state:
                save_state(force=True)

            real_idx = entry.get("_real_idx")
            if canvas_cb is not None and real_idx is not None:
                from modules.qt.mosaic_canvas import build_qimage_for_entry
                build_qimage_for_entry(entry)
                canvas_cb.refresh_thumbnail(real_idx)
                canvas_cb.refresh_duplicate_overlay()
            elif render_mosaic:
                render_mosaic()
            if update_btn:
                update_btn()

            self._canvas.clear_pasted_images()
            self._pasted_images_by_page.pop(self.current_idx, None)
            self.display_image(keep_crop_rect=True)
            self._toolbar.refresh_undo_redo_state()
            self._on_paste_image_content_changed()

        except Exception:
            dlg = MsgDialog(self._center_parent, "messages.errors.paste_image_failed.title",
                            "messages.errors.paste_image_failed.title")
            dlg.show_nonmodal()

    # ── Persistance par page ──────────────────────────────────────────────────
    # Contrairement aux formes (tuples légers), une image collée porte un
    # bitmap : sérialisée en PNG (bytes) pour la persistance par page, comme
    # l'image de travail de transparency (_transp_work_img_by_page) est
    # gardée en objet PIL direct — ici en bytes PNG + géométrie, reconstruite
    # en PIL uniquement à la restauration.

    def _save_paste_image_for_current_page(self):
        pasted_list = self._canvas._pasted_images
        if pasted_list:
            import io as _io
            saved = []
            for p in pasted_list:
                buf = _io.BytesIO()
                p.pil_img.save(buf, format="PNG")
                saved.append((buf.getvalue(), p.ix1, p.iy1, p.ix2, p.iy2, p.angle))
            self._pasted_images_by_page[self.current_idx] = saved
        else:
            self._pasted_images_by_page.pop(self.current_idx, None)

    def _restore_paste_image_for_page(self, idx: int):
        self._canvas.clear_pasted_images()
        saved = self._pasted_images_by_page.get(idx)
        if not saved:
            return
        import io as _io
        for (png_bytes, ix1, iy1, ix2, iy2, angle) in saved:
            pil_img = Image.open(_io.BytesIO(png_bytes)).convert("RGBA")
            pasted = _PastedImage(pil_img, ix1, iy1, ix2, iy2, angle=angle)
            self._canvas._pasted_images.append(pasted)
        self._canvas.update()
