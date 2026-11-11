"""
minimap_widget_qt.py — Panneau minimap : aperçu miniature de la position dans la
grille de la mosaïque (MosaicCanvas), avec rectangle de zone visible navigable
(comportement inspiré de la minimap de VS Code).

La taille des mini-vignettes est FIXE (dérivée de la largeur du panneau), pas
compressée pour faire tenir tout le contenu — sur un omnibus de 1500+ pages,
compresser verticalement rendrait les vignettes illisibles. À la place, la
minimap défile elle-même verticalement (offset interne, pas de QScrollBar
visible), indépendamment de la mosaïque : molette ou glisser hors du rectangle
de zone visible ne font défiler qu'elle, et le défilement de la mosaïque ne
déplace que le rectangle, qui peut sortir de la partie visible de la minimap.
Elle ne se recentre sur ce rectangle que quand la disposition change (autre
document, taille de la fenêtre ou du panneau, taille des vignettes).

Usage :
    minimap = MinimapWidget(canvas)   # canvas = MosaicCanvas du même panneau
    layout.addWidget(minimap)
"""

from PySide6.QtCore import Qt, QRectF, QTimer, QEvent, QPointF
from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtGui import QPainter, QColor, QPen

from modules.qt.state import get_current_theme
from modules.qt.mosaic_canvas import (
    ThumbnailItem, DirItem, PAD_X, PAD_Y, LABEL_H, _get_pixmap_for_size,
    _get_bookmark_pixmap, _get_duplicate_pixmap_wide_margin, SEL_OUTLINE,
    ALTERED_FRAME_COLOR, DELETED_PAGE_OPACITY, deleted_pages_skipped,
    paint_deleted_page_badge, _grayed_pixmap,
)
from modules.qt.entries import is_altered

_MINI_PAD = 4

# Couleur du rectangle de viewport — fixe, identique en thème clair et sombre (décidé par l'utilisateur)
_VIEWPORT_COLOR = QColor(0, 170, 0)

# Défilement automatique quand le rectangle vert est glissé au bord de la minimap
_EDGE_ZONE = 16          # hauteur (px) de la zone de bord qui déclenche le défilement
_AUTOSCROLL_MS = 30      # intervalle entre deux pas de défilement
_AUTOSCROLL_MAX = 40     # pas maximal (px minimap), atteint loin hors du widget

# Molette sur la minimap : pixels minimap par cran (120 unités d'angleDelta)
_WHEEL_STEP = 40

# Délai du recentrage après un redimensionnement : le canvas replace ses
# vignettes dans un QTimer.singleShot(0) posé après ce filtre, la minimap doit
# relire la scène une fois ce replacement fait.
_RELAYOUT_DELAY_MS = 30


class MinimapWidget(QWidget):
    """Widget dessiné à la main (QPainter) affichant un aperçu réduit de la grille
    du MosaicCanvas fourni, avec un rectangle indiquant la zone actuellement visible.
    Ne modifie jamais le contenu de la mosaïque — lecture seule des items déjà
    construits par render_mosaic() ; seule la position de scroll du canvas est modifiée
    en réaction aux interactions utilisateur sur la minimap (jamais de D&D de pages)."""

    def __init__(self, canvas, parent=None, owner_panel=None):
        super().__init__(parent)
        self._canvas = canvas
        self._owner_panel = owner_panel  # PanelWidget propriétaire — pour le focus en mode split
        self.setMinimumHeight(0)
        self.setMouseTracking(True)

        self._dragging_viewport = False
        self._drag_offset = QPointF(0, 0)
        self._last_drag_pos = QPointF(0, 0)
        self._scroll_y = 0.0  # offset de défilement interne à la minimap (coords minimap)

        # Clic hors du rectangle : saut de la mosaïque au relâchement si la
        # souris n'a pas bougé, sinon défilement de la minimap seule
        self._press_pos = None
        self._press_scroll_y = 0.0
        self._panning = False

        # Disposition à laquelle le défilement interne a été recentré
        self._layout_signature = None
        self._recenter_pending = False

        self._autoscroll_timer = QTimer(self)
        self._autoscroll_timer.setInterval(_AUTOSCROLL_MS)
        self._autoscroll_timer.timeout.connect(self._autoscroll_tick)

        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(_RELAYOUT_DELAY_MS)
        self._relayout_timer.timeout.connect(self._sync_after_relayout)

        canvas.status_changed.connect(self._on_canvas_changed)
        # Le canvas ne réémet pas status_changed sur un simple resize (_relayout()) —
        # on intercepte donc directement ses Resize events pour rester synchro.
        canvas.installEventFilter(self)
        canvas.horizontalScrollBar().valueChanged.connect(self._on_canvas_scrolled)
        canvas.verticalScrollBar().valueChanged.connect(self._on_canvas_scrolled)

    def eventFilter(self, obj, event):
        if obj is self._canvas and event.type() == QEvent.Resize:
            self._relayout_timer.start()
        return False

    # ── Géométrie des mini-cellules (taille FIXE, pas de compression) ────────
    # NB : on lit self._canvas._state directement (jamais le singleton global
    # _state_module.state) — pendant les opérations qui basculent temporairement
    # le singleton vers un autre state (ex. _apply_thumb_size), un update() différé
    # de la minimap pourrait sinon repeindre avec un state déjà restauré/périmé.
    def _real_tw(self):
        st = self._canvas._state
        return st.thumb_w if st and hasattr(st, 'thumb_w') else 150

    def _real_th(self):
        st = self._canvas._state
        return st.thumb_h if st and hasattr(st, 'thumb_h') else 200

    def _real_cw(self):
        return self._real_tw() + PAD_X * 2

    def _real_ch(self):
        return self._real_th() + PAD_Y * 2 + LABEL_H

    def _real_cols(self):
        """Équivalent de MosaicCanvas._cols() mais basé sur self._canvas._state
        (jamais le singleton global — cf. note plus haut sur le state périmé)."""
        w = self._canvas.viewport().width()
        return max(1, w // self._real_cw())

    def _mini_cell_size(self):
        """Retourne (mini_w, mini_h, cols) — taille fixe d'une mini-vignette,
        dérivée uniquement de la largeur disponible / du nombre de colonnes réel."""
        tw, th = self._real_tw(), self._real_th()
        avail_w = max(1, self.width() - _MINI_PAD * 2)
        real_cols = self._real_cols()
        mini_w = max(4, avail_w // real_cols)
        mini_h = max(4, int(mini_w * (th / tw))) if tw else mini_w
        return mini_w, mini_h, real_cols

    def _scale(self):
        """Facteur d'échelle scène réelle → minimap (identique en X et Y par construction)."""
        mini_w, _, _ = self._mini_cell_size()
        real_cw = self._real_cw()
        return mini_w / real_cw if real_cw else 1.0

    def _content_height(self):
        """Hauteur totale (coords minimap) de toute la grille, défilement inclus."""
        items = getattr(self._canvas, "_items", None) or []
        if not items:
            return 0.0
        _, _, cols = self._mini_cell_size()
        rows = -(-len(items) // cols)  # ceil division
        scale = self._scale()
        return rows * self._real_ch() * scale

    def _clamp_scroll(self):
        max_scroll = max(0.0, self._content_height() - (self.height() - _MINI_PAD * 2))
        self._scroll_y = max(0.0, min(self._scroll_y, max_scroll))

    def _center_scroll_on_viewport(self):
        """Recentre le défilement interne de la minimap sur le rectangle de zone visible."""
        scale = self._scale()
        vp = self._canvas.viewport().rect()
        scene_visible = self._canvas.mapToScene(vp).boundingRect()
        vp_center_y = scene_visible.center().y() * scale
        avail_h = max(1, self.height() - _MINI_PAD * 2)
        self._scroll_y = vp_center_y - avail_h / 2
        self._clamp_scroll()

    def _scene_to_minimap_rect(self, scene_rect: QRectF) -> QRectF:
        scale = self._scale()
        return QRectF(
            _MINI_PAD + scene_rect.x() * scale,
            _MINI_PAD + scene_rect.y() * scale - self._scroll_y,
            scene_rect.width() * scale,
            scene_rect.height() * scale,
        )

    def _minimap_to_scene_point(self, pos: QPointF) -> QPointF:
        scale = self._scale()
        if scale == 0:
            return QPointF(0, 0)
        return QPointF(
            (pos.x() - _MINI_PAD) / scale,
            (pos.y() - _MINI_PAD + self._scroll_y) / scale,
        )

    def _viewport_rect_minimap(self) -> QRectF | None:
        """Rectangle (coords widget minimap) représentant la zone visible du canvas."""
        vp = self._canvas.viewport().rect()
        scene_visible = self._canvas.mapToScene(vp).boundingRect()
        return self._scene_to_minimap_rect(scene_visible)

    def _current_layout_signature(self):
        """Ce qui, en changeant, rend caduque la position de défilement de la
        minimap : document (doc_generation, changée à l'ouverture et à la
        fermeture), taille des vignettes, colonnes, taille du canvas et de la
        minimap. Lu sur l'état du canvas, jamais sur le singleton."""
        st = self._canvas._state
        vp = self._canvas.viewport()
        return (getattr(st, "doc_generation", None), self._real_tw(), self._real_th(),
                self._real_cols(), vp.width(), vp.height(), self.width(), self.height())

    def _on_canvas_changed(self):
        """Fin d'un rendu de la mosaïque (ou changement de sélection) : recentre
        seulement si la disposition a changé, sinon garde la position choisie
        par l'utilisateur. Le recentrage est refait après coup, une fois que le
        canvas a éventuellement repositionné son défilement."""
        signature = self._current_layout_signature()
        if signature != self._layout_signature:
            self._layout_signature = signature
            self._center_scroll_on_viewport()
            self._recenter_pending = True
            self._relayout_timer.start()
        else:
            self._clamp_scroll()
        self.update()

    def _sync_after_relayout(self):
        signature = self._current_layout_signature()
        if signature != self._layout_signature or self._recenter_pending:
            self._layout_signature = signature
            self._recenter_pending = False
            self._center_scroll_on_viewport()
        else:
            self._clamp_scroll()
        self.update()

    def _on_canvas_scrolled(self, _value=None):
        # Le défilement de la mosaïque ne déplace que le rectangle vert
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_timer.start()
        self.update()

    # ── Rendu ──────────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        theme = get_current_theme()
        painter.fillRect(self.rect(), QColor(theme["toolbar_bg"]))

        # Bordure gauche statique (jamais interactive — ne pas confondre avec une poignée
        # de splitter redimensionnable, la minimap a une largeur fixe)
        painter.setPen(QPen(QColor(theme["separator"]), 1))
        painter.drawLine(0, 0, 0, self.height())

        items = getattr(self._canvas, "_items", None)
        if not items:
            painter.end()
            return

        mini_w, mini_h, cols = self._mini_cell_size()
        scale = self._scale()
        mini_cw = self._real_cw() * scale
        mini_ch = self._real_ch() * scale

        painter.setClipRect(1, 0, self.width() - 1, self.height())
        # Pages « Supprimée » grisées comme dans la mosaïque, si la visionneuse
        # les saute (lu une fois par dessin)
        gray_deleted = deleted_pages_skipped()

        for item in items:
            visual_idx = item.visual_idx
            col = visual_idx % cols
            row = visual_idx // cols
            x = _MINI_PAD + col * mini_cw
            y = _MINI_PAD + row * mini_ch - self._scroll_y

            if y + mini_ch < 0 or y > self.height():
                continue  # hors de la zone visible de la minimap — pas la peine de dessiner

            pixmap = self._mini_pixmap_for_item(item, mini_w, mini_h)
            if pixmap is not None and gray_deleted and self._is_deleted_page(item):
                painter.setOpacity(DELETED_PAGE_OPACITY)
                painter.drawPixmap(int(x), int(y), _grayed_pixmap(pixmap))
                painter.setOpacity(1.0)
            elif pixmap is not None:
                painter.drawPixmap(int(x), int(y), pixmap)
            else:
                painter.fillRect(QRectF(x, y, mini_w, mini_h), QColor(theme["disabled"]))

            self._paint_overlays(painter, item, x, y, mini_w, mini_h)

        # Rectangle de la zone actuellement visible dans la mosaïque
        vp_rect = self._viewport_rect_minimap()
        if vp_rect is not None:
            painter.setPen(QPen(_VIEWPORT_COLOR, 2))
            painter.setBrush(QColor(_VIEWPORT_COLOR.red(), _VIEWPORT_COLOR.green(),
                                     _VIEWPORT_COLOR.blue(), 40))
            painter.drawRect(vp_rect)

        painter.end()

    def _mini_pixmap_for_item(self, item, mini_w, mini_h):
        if isinstance(item, (ThumbnailItem, DirItem)):
            entry = item.entry
        else:
            return None
        try:
            return _get_pixmap_for_size(entry, mini_w, mini_h)
        except Exception:
            return None

    def _paint_overlays(self, painter, item, x, y, mini_w, mini_h):
        """Dessine, en surimpression réduite, le cadre de sélection ainsi que les
        badges de doublon/marque-page/page « Supprimée » — mêmes conditions que ThumbnailItem.paint(),
        à l'échelle de la mini-vignette."""
        if isinstance(item, ThumbnailItem) and getattr(item, "_selected", False):
            painter.setPen(QPen(SEL_OUTLINE, 2))
            painter.drawRect(QRectF(x - 1, y - 1, mini_w + 2, mini_h + 2))

        entry = getattr(item, "entry", None)
        if not entry:
            return

        if entry.get("is_corrupted"):
            painter.setPen(QPen(QColor(200, 0, 0), 2))
            painter.drawRect(QRectF(x - 1, y - 1, mini_w + 2, mini_h + 2))
        elif is_altered(entry):
            painter.setPen(QPen(ALTERED_FRAME_COLOR, 2))
            painter.drawRect(QRectF(x - 1, y - 1, mini_w + 2, mini_h + 2))

        if entry.get("_is_bookmarked"):
            bm_px = _get_bookmark_pixmap()
            if bm_px and not bm_px.isNull():
                bm_size = max(6, mini_w // 2)
                scaled = bm_px.scaled(bm_size, bm_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                painter.setOpacity(0.85)
                painter.drawPixmap(int(x + mini_w - scaled.width()), int(y), scaled)
                painter.setOpacity(1.0)

        if entry.get("_is_duplicate"):
            dup_px = _get_duplicate_pixmap_wide_margin()
            if dup_px and not dup_px.isNull():
                dup_size = max(6, mini_w // 2)
                scaled_dup = dup_px.scaled(dup_size, dup_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                painter.setOpacity(0.85)
                painter.drawPixmap(int(x), int(y), scaled_dup)
                painter.setOpacity(1.0)

        if self._is_deleted_page(item):
            paint_deleted_page_badge(painter, x + mini_w, y + mini_h, max(6, mini_w // 3))

    @staticmethod
    def _is_deleted_page(item) -> bool:
        """Page de type « Supprimée » (ThumbnailItem._is_deleted_page : document
        du panneau, jamais le singleton)."""
        return isinstance(item, ThumbnailItem) and item._is_deleted_page()

    # ── Navigation : clic / drag sur le rectangle de viewport ────────────────
    def _scroll_canvas_to_scene_center(self, scene_pos: QPointF):
        self._canvas.centerOn(scene_pos)

    def _notify_panel_focus(self):
        """En mode split, cliquer/draguer sur la minimap d'un panneau non-actif
        le rend actif — même comportement qu'un clic direct sur son canvas."""
        panel = self._owner_panel
        if panel is None:
            return
        mw = getattr(panel, "_main_window", None)
        if mw is not None and getattr(mw, "_split_active", False):
            set_active = getattr(mw, "_set_active_panel", None)
            if set_active:
                set_active(panel)

    def _drag_canvas_to(self, pos: QPointF):
        """Pendant le glisser du rectangle vert : centre la mosaïque sous la
        souris, moins le décalage de saisie (le rectangle ne saute pas)."""
        scene_pos = self._minimap_to_scene_point(pos - self._drag_offset)
        self._scroll_canvas_to_scene_center(scene_pos)

    def _edge_speed(self, pos: QPointF) -> float:
        """Pas de défilement automatique (px minimap, signé) quand la souris est
        dans la zone de bord haute ou basse, ou au-delà ; 0 ailleurs."""
        y = pos.y()
        if y < _EDGE_ZONE:
            return -min(_AUTOSCROLL_MAX, 2 + (_EDGE_ZONE - y) / 2)
        bottom = self.height() - _EDGE_ZONE
        if y > bottom:
            return min(_AUTOSCROLL_MAX, 2 + (y - bottom) / 2)
        return 0.0

    def _autoscroll_tick(self):
        if not self._dragging_viewport:
            self._autoscroll_timer.stop()
            return
        speed = self._edge_speed(self._last_drag_pos)
        if speed == 0:
            self._autoscroll_timer.stop()
            return
        self._scroll_y += speed
        self._clamp_scroll()
        # La même position de souris désigne un autre endroit de la scène
        # une fois la minimap décalée : la mosaïque suit
        self._drag_canvas_to(self._last_drag_pos)
        self.update()

    def _update_hover_cursor(self, pos: QPointF):
        vp_rect = self._viewport_rect_minimap()
        if vp_rect is not None and vp_rect.contains(pos):
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self._notify_panel_focus()
        vp_rect = self._viewport_rect_minimap()
        pos = event.position() if hasattr(event, "position") else event.pos()
        pos = QPointF(pos)
        if vp_rect is not None and vp_rect.contains(pos):
            self._dragging_viewport = True
            self._drag_offset = pos - vp_rect.center()
            self._last_drag_pos = pos
            self.setCursor(Qt.ClosedHandCursor)
        else:
            # Clic hors du rectangle : saut ou défilement de la minimap, décidé
            # selon que la souris bouge avant le relâchement
            self._press_pos = pos
            self._press_scroll_y = self._scroll_y
            self._panning = False

    def mouseMoveEvent(self, event):
        pos = event.position() if hasattr(event, "position") else event.pos()
        pos = QPointF(pos)
        if self._dragging_viewport:
            self._last_drag_pos = pos
            self._drag_canvas_to(pos)
            if self._edge_speed(pos) != 0:
                if not self._autoscroll_timer.isActive():
                    self._autoscroll_timer.start()
            else:
                self._autoscroll_timer.stop()
            return
        if self._press_pos is not None:
            if not self._panning:
                moved = (pos - self._press_pos).manhattanLength()
                if moved < QApplication.startDragDistance():
                    return
                self._panning = True
                self.setCursor(Qt.ClosedHandCursor)
            # Défilement de la minimap seule : le contenu suit la souris
            self._scroll_y = self._press_scroll_y - (pos.y() - self._press_pos.y())
            self._clamp_scroll()
            self.update()
            return
        self._update_hover_cursor(pos)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position() if hasattr(event, "position") else event.pos()
        pos = QPointF(pos)
        if self._dragging_viewport:
            self._dragging_viewport = False
            self._autoscroll_timer.stop()
        elif self._press_pos is not None:
            if not self._panning:
                # Clic simple : la mosaïque saute à l'endroit cliqué
                scene_pos = self._minimap_to_scene_point(self._press_pos)
                self._scroll_canvas_to_scene_center(scene_pos)
            self._press_pos = None
            self._panning = False
        self._update_hover_cursor(pos)

    def leaveEvent(self, event):
        if not self._dragging_viewport and not self._panning:
            self.setCursor(Qt.ArrowCursor)
        super().leaveEvent(event)

    # ── Molette : défile la minimap seule, jamais la mosaïque ───────────────
    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self._scroll_y -= delta / 120 * _WHEEL_STEP
            self._clamp_scroll()
            self.update()
        event.accept()
