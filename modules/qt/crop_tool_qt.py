"""
modules/qt/crop_tool_qt.py — Outil de recadrage (rubber-band) de la barre
d'outils flottante de la visionneuse principale (image_viewer_qt.py).

Fusion progressive des visionneuses (idees.txt #3, 1er outil migré — déjà
intégré à ImageViewer avant même ce chantier, jamais eu de fenêtre dédiée
séparée). Ce module contient toute la logique propre à l'outil "crop" — état/
interactions du canvas (mixin CropCanvasMixin, hérité par _ViewerCanvas) et
commit du recadrage dans l'historique du panneau (mixin CropViewerMixin,
hérité par ImageViewer). image_viewer_qt.py ne fait qu'hériter de ces deux
mixins et brancher l'icône de la barre d'outils — voir CLAUDE.md règle
"ne jamais migrer le code d'un outil dans image_viewer_qt.py".

Restent dans image_viewer_qt.py (points de couplage transversaux, pas
spécifiques au seul crop) :
  - Le bouton "Valider" flottant du canvas, PARTAGÉ avec le redressement
    manuel (un seul widget, texte/action selon l'outil actif — voir
    _ViewerCanvas._show_validate_btn/_VALIDATE_KEYS).
  - _ignore_crop_events et mouseDoubleClickEvent, qui gèrent aussi le
    double-clic plein écran général (pas seulement la validation du crop).
"""

from PySide6.QtCore import Qt, QPoint


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état et interactions souris de l'outil (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class CropCanvasMixin:
    """Hérité par _ViewerCanvas (image_viewer_qt.py) en plus de QLabel : ajoute
    l'état et les méthodes de l'outil "crop" au canvas de la visionneuse, sans
    que leur code vive dans image_viewer_qt.py. Suppose que l'hôte a déjà
    self._viewer (ImageViewer), self._update_validate_btn_state()
    (bouton Valider partagé avec straighten/text/shapes, resté dans
    image_viewer_qt.py — visibilité pilotée uniquement par
    _ViewerToolbar.show_and_schedule_hide/_on_hide_timeout, jamais depuis ici),
    self._ignore_crop_events (partagé avec le double-clic plein écran général,
    resté dans image_viewer_qt.py), et les attributs habituels de
    _ViewerCanvas (display_offset_x/y, display_width/height).

    Rectangle rouge (actif) / gris (conservé mais outil désélectionné) avec
    poignées de redimensionnement sur les 4 coins/côtés, validation par
    double-clic à l'intérieur (géré dans image_viewer_qt.py::
    _ViewerCanvas.mouseDoubleClickEvent, transversal avec le plein écran) ou
    bouton "Valider" flottant.
    """

    _CURSORS = {
        'tl': Qt.SizeFDiagCursor, 'br': Qt.SizeFDiagCursor,
        'tr': Qt.SizeBDiagCursor, 'bl': Qt.SizeBDiagCursor,
        'left': Qt.SizeHorCursor, 'right': Qt.SizeHorCursor,
        'top': Qt.SizeVerCursor,  'bottom': Qt.SizeVerCursor,
        'move': Qt.SizeAllCursor,
    }

    def _init_crop_state(self):
        self._crop_start: QPoint | None = None
        self._crop_end:   QPoint | None = None
        self._rubber_band_active = False

        # Coordonnées relatives (0-1) persistantes entre zooms
        self.crop_rel_x1: float | None = None
        self.crop_rel_y1: float | None = None
        self.crop_rel_x2: float | None = None
        self.crop_rel_y2: float | None = None

        # Gestion double-clic (validation du crop)
        self._last_click_time = 0.0
        self._double_click_delay = 0.3
        self._waiting_for_double_click = False

        # Resize/move mode pour les bords/coins/intérieur du rectangle
        self._resize_mode: str | None = None
        self._resize_original_rect: tuple | None = None
        self._drag_start_pos: QPoint | None = None

    @property
    def has_crop(self) -> bool:
        return self._crop_start is not None and self._crop_end is not None

    def clear_crop(self):
        self._crop_start = None
        self._crop_end   = None
        self.crop_rel_x1 = None
        self.crop_rel_y1 = None
        self.crop_rel_x2 = None
        self.crop_rel_y2 = None
        self._resize_mode = None
        self._resize_original_rect = None
        self._drag_start_pos = None
        self.pan_offset_x = 0
        self.pan_offset_y = 0
        # Recalcule seulement l'ÉTAT du bouton "Valider" (repasse en gris,
        # has_crop redevient faux) — ne touche JAMAIS à sa visibilité, pilotée
        # uniquement par _ViewerToolbar.show_and_schedule_hide/_on_hide_timeout
        # (mécanisme unique, voir image_viewer_qt.py::_update_validate_btn_state).
        # Bouton "Annuler" jumeau rafraîchi juste à côté (2026-08-15).
        self._update_validate_btn_state()
        self._update_cancel_btn_state()
        self.update()

    def _get_resize_mode(self, pos: QPoint) -> str | None:
        if not self.has_crop:
            return None
        tolerance = 10
        x1 = min(self._crop_start.x(), self._crop_end.x())
        x2 = max(self._crop_start.x(), self._crop_end.x())
        y1 = min(self._crop_start.y(), self._crop_end.y())
        y2 = max(self._crop_start.y(), self._crop_end.y())
        x, y = pos.x(), pos.y()
        if abs(x - x1) <= tolerance and abs(y - y1) <= tolerance: return 'tl'
        if abs(x - x2) <= tolerance and abs(y - y1) <= tolerance: return 'tr'
        if abs(x - x1) <= tolerance and abs(y - y2) <= tolerance: return 'bl'
        if abs(x - x2) <= tolerance and abs(y - y2) <= tolerance: return 'br'
        if abs(x - x1) <= tolerance and y1 <= y <= y2: return 'left'
        if abs(x - x2) <= tolerance and y1 <= y <= y2: return 'right'
        if abs(y - y1) <= tolerance and x1 <= x <= x2: return 'top'
        if abs(y - y2) <= tolerance and x1 <= x <= x2: return 'bottom'
        if x1 < x < x2 and y1 < y < y2: return 'move'
        return None

    # ── Rendu (appelé depuis _ViewerCanvas.paintEvent) ───────────────────────

    def paint_crop_rect(self, painter):
        """À appeler en fin de paintEvent, après l'image. Rectangle rouge
        (actif) / gris (conservé mais outil désélectionné)."""
        from PySide6.QtGui import QPen, QColor
        if not self.has_crop:
            return
        crop_active = self._viewer._toolbar.active_tool == "crop"
        pen = QPen(QColor("red") if crop_active else QColor("#888888"), 2)
        painter.setPen(pen)
        x1 = int(min(self._crop_start.x(), self._crop_end.x()))
        y1 = int(min(self._crop_start.y(), self._crop_end.y()))
        x2 = int(max(self._crop_start.x(), self._crop_end.x()))
        y2 = int(max(self._crop_start.y(), self._crop_end.y()))
        painter.drawRect(x1, y1, x2 - x1, y2 - y1)

    # ── Événements souris (appelés depuis _ViewerCanvas.mousePress/Move/ReleaseEvent) ──

    def crop_mouse_press(self, event):
        import time
        pos = event.position().toPoint()
        current_time = time.time()
        time_since = current_time - self._last_click_time

        # Deuxième clic rapide → probablement double-clic, ignorer
        if 0.001 < time_since < self._double_click_delay:
            return

        self._last_click_time = current_time

        # Vérifie resize/move mode
        resize_mode = self._get_resize_mode(pos)
        if resize_mode:
            self._resize_mode = resize_mode
            self._resize_original_rect = (
                self._crop_start.x(), self._crop_start.y(),
                self._crop_end.x(),   self._crop_end.y()
            )
            self._drag_start_pos = pos
            self._waiting_for_double_click = False
            return

        # Rectangle complet existant → attendre double-clic
        if self.has_crop:
            self._waiting_for_double_click = True
            return

        # Nouveau rectangle — recalcule l'état du bouton (repasse en gris,
        # has_crop redevient faux tant que le tracé n'est pas terminé), voir
        # clear_crop() pour le détail de la règle.
        self._update_validate_btn_state()
        self._update_cancel_btn_state()
        self._crop_start = pos
        self._crop_end   = None
        self._resize_mode = None
        self._waiting_for_double_click = False
        self.update()

    def crop_update_cursor(self, event):
        """Curseur hors tracé (pas de bouton enfoncé), uniquement si l'outil
        crop est actif et qu'un rectangle existe déjà : curseur de
        redimensionnement selon la position sur le cadre."""
        mode = self._get_resize_mode(event.position().toPoint())
        from PySide6.QtGui import QCursor
        self.setCursor(QCursor(self._CURSORS.get(mode, Qt.ArrowCursor)))

    def crop_mouse_move(self, event):
        if self._crop_start is None:
            return

        pos = event.position().toPoint()
        distance = ((pos.x() - self._crop_start.x())**2 +
                    (pos.y() - self._crop_start.y())**2) ** 0.5

        # Drag en attente de double-clic
        if self._waiting_for_double_click:
            if distance >= 15:
                self._waiting_for_double_click = False
                self._update_validate_btn_state()
                self._update_cancel_btn_state()
            else:
                return

        if self._resize_mode and self._resize_original_rect:
            ox1, oy1, ox2, oy2 = self._resize_original_rect
            x1, y1, x2, y2 = ox1, oy1, ox2, oy2
            rm = self._resize_mode
            if rm == 'move' and self._drag_start_pos is not None:
                dx = pos.x() - self._drag_start_pos.x()
                dy = pos.y() - self._drag_start_pos.y()
                x1, y1, x2, y2 = ox1 + dx, oy1 + dy, ox2 + dx, oy2 + dy
            elif rm == 'tl':   x1, y1 = pos.x(), pos.y()
            elif rm == 'tr': x2, y1 = pos.x(), pos.y()
            elif rm == 'bl': x1, y2 = pos.x(), pos.y()
            elif rm == 'br': x2, y2 = pos.x(), pos.y()
            elif rm == 'left':   x1 = pos.x()
            elif rm == 'right':  x2 = pos.x()
            elif rm == 'top':    y1 = pos.y()
            elif rm == 'bottom': y2 = pos.y()
            self._crop_start = QPoint(x1, y1)
            self._crop_end   = QPoint(x2, y2)
        elif distance >= 15 or self._crop_end is not None:
            self._crop_end = pos

        self.update()

    def crop_mouse_release(self, event):
        pos = event.position().toPoint()

        if self._waiting_for_double_click:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(500, lambda: setattr(self, '_waiting_for_double_click', False))
            return

        if self._crop_start is None:
            return

        distance = ((pos.x() - self._crop_start.x())**2 +
                    (pos.y() - self._crop_start.y())**2) ** 0.5

        if self._resize_mode and self._resize_original_rect:
            ox1, oy1, ox2, oy2 = self._resize_original_rect
            x1, y1, x2, y2 = ox1, oy1, ox2, oy2
            rm = self._resize_mode
            if rm == 'move' and self._drag_start_pos is not None:
                dx = pos.x() - self._drag_start_pos.x()
                dy = pos.y() - self._drag_start_pos.y()
                x1, y1, x2, y2 = ox1 + dx, oy1 + dy, ox2 + dx, oy2 + dy
            elif rm == 'tl':   x1, y1 = pos.x(), pos.y()
            elif rm == 'tr': x2, y1 = pos.x(), pos.y()
            elif rm == 'bl': x1, y2 = pos.x(), pos.y()
            elif rm == 'br': x2, y2 = pos.x(), pos.y()
            elif rm == 'left':   x1 = pos.x()
            elif rm == 'right':  x2 = pos.x()
            elif rm == 'top':    y1 = pos.y()
            elif rm == 'bottom': y2 = pos.y()
            self._resize_mode = None
            self._resize_original_rect = None
            self._drag_start_pos = None
            self._crop_start = QPoint(min(x1, x2), min(y1, y2))
            self._crop_end   = QPoint(max(x1, x2), max(y1, y2))

        elif distance < 15 and self._crop_end is None:
            # Simple clic sans drag → rien à faire
            self._crop_start = None
            self.update()
            return
        else:
            if self._crop_end is None:
                self._crop_end = pos

        # Normalise
        x1 = min(self._crop_start.x(), self._crop_end.x())
        y1 = min(self._crop_start.y(), self._crop_end.y())
        x2 = max(self._crop_start.x(), self._crop_end.x())
        y2 = max(self._crop_start.y(), self._crop_end.y())

        if abs(x2 - x1) < 10 or abs(y2 - y1) < 10:
            self._crop_start = None
            self._crop_end   = None
            self.update()
            return

        self._crop_start = QPoint(x1, y1)
        self._crop_end   = QPoint(x2, y2)

        # Coordonnées relatives
        if self.display_width > 0 and self.display_height > 0:
            self.crop_rel_x1 = (x1 - self.display_offset_x) / self.display_width
            self.crop_rel_y1 = (y1 - self.display_offset_y) / self.display_height
            self.crop_rel_x2 = (x2 - self.display_offset_x) / self.display_width
            self.crop_rel_y2 = (y2 - self.display_offset_y) / self.display_height

        # En mode double page avec rectangle tracé → passe en simple page
        if self._viewer.page_mode != "single" and self.has_crop:
            if self._viewer.displayed_left_idx is not None and self._viewer.displayed_right_idx is not None:
                center_x = self.width() / 2
                rect_cx = (x1 + x2) / 2
                if rect_cx < center_x:
                    self._viewer.current_idx = self._viewer.displayed_left_idx
                else:
                    self._viewer.current_idx = self._viewer.displayed_right_idx
            self._viewer.page_mode = "single"
            self._viewer.display_image(keep_crop_rect=True)
            return

        self.update()
        self._update_validate_btn_state()
        self._update_cancel_btn_state()


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — commit du recadrage dans l'historique du panneau (hérité par ImageViewer)
# ─────────────────────────────────────────────────────────────────────────────

class CropViewerMixin:
    """Hérité par ImageViewer (image_viewer_qt.py) en plus de QDialog : ajoute
    la logique de recadrage au viewer, sans que son code vive dans
    image_viewer_qt.py. Suppose que l'hôte a déjà self._canvas (_ViewerCanvas,
    avec CropCanvasMixin), self.callbacks, self.current_idx, self.zoom_level,
    self._toolbar, et self._crop_by_page (persistance par page).
    """

    def _save_crop_for_current_page(self):
        """Mémorise le rectangle de crop de la page qu'on s'apprête à quitter
        (coordonnées relatives 0-1, indépendantes du zoom/pan) pour le
        restaurer si l'utilisateur revient sur cette page (idees.txt #3,
        partie B)."""
        c = self._canvas
        if c.crop_rel_x1 is not None:
            self._crop_by_page[self.current_idx] = (
                c.crop_rel_x1, c.crop_rel_y1, c.crop_rel_x2, c.crop_rel_y2
            )
        else:
            self._crop_by_page.pop(self.current_idx, None)

    def _restore_crop_for_page(self, idx: int):
        """Réapplique le rectangle de crop mémorisé pour la page idx, s'il existe."""
        rel = self._crop_by_page.get(idx)
        c = self._canvas
        if rel is None:
            c.clear_crop()
            return
        c.crop_rel_x1, c.crop_rel_y1, c.crop_rel_x2, c.crop_rel_y2 = rel

    def validate_crop(self):
        from modules.qt.dialogs_qt import MsgDialog
        if not self._canvas.has_crop:
            dlg = MsgDialog(
                self,
                "messages.warnings.no_crop_selection.title",
                "messages.warnings.no_crop_selection.message",
            )
            dlg.show_nonmodal()
            return
        self.perform_crop()

    def perform_crop(self):
        from modules.qt import state as _state_module
        from modules.qt.entries import ensure_image_loaded, save_image_to_bytes
        from modules.qt.dialogs_qt import MsgDialog

        state = self.callbacks.get('state') or _state_module.state
        save_state      = self.callbacks.get("save_state")
        render_mosaic   = self.callbacks.get("render_mosaic")
        update_btn      = self.callbacks.get("update_button_text")
        canvas          = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]
            original_img = ensure_image_loaded(entry)
            if original_img is None:
                dlg = MsgDialog(self, "messages.errors.crop_failed.title",
                                "messages.errors.crop_failed.title")
                dlg.show_nonmodal()
                return

            c = self._canvas
            crop_x1 = c._crop_start.x() - c.display_offset_x
            crop_y1 = c._crop_start.y() - c.display_offset_y
            crop_x2 = c._crop_end.x()   - c.display_offset_x
            crop_y2 = c._crop_end.y()   - c.display_offset_y

            crop_x1 = max(0, min(crop_x1, c.display_width))
            crop_y1 = max(0, min(crop_y1, c.display_height))
            crop_x2 = max(0, min(crop_x2, c.display_width))
            crop_y2 = max(0, min(crop_y2, c.display_height))

            img_w, img_h = original_img.size
            zoom_ratio = self.zoom_level

            orig_x1 = int(crop_x1 / zoom_ratio)
            orig_y1 = int(crop_y1 / zoom_ratio)
            orig_x2 = int(crop_x2 / zoom_ratio)
            orig_y2 = int(crop_y2 / zoom_ratio)

            if orig_x2 <= orig_x1 or orig_y2 <= orig_y1:
                dlg = MsgDialog(self, "messages.errors.crop_invalid.title",
                                "messages.errors.crop_invalid.message")
                dlg.show_nonmodal()
                return

            if save_state:
                save_state()

            cropped = original_img.crop((orig_x1, orig_y1, orig_x2, orig_y2))
            entry["img"]   = cropped
            entry["bytes"] = save_image_to_bytes(entry)
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
                save_state()
            real_idx = entry.get("_real_idx")
            if canvas is not None and real_idx is not None:
                from modules.qt.mosaic_canvas import build_qimage_for_entry
                build_qimage_for_entry(entry)
                canvas.refresh_thumbnail(real_idx)
                canvas.refresh_duplicate_overlay()
            elif render_mosaic:
                render_mosaic()
            if update_btn:
                update_btn()
            self._canvas.clear_crop()
            self._crop_by_page.pop(self.current_idx, None)
            self.display_image()
            self._toolbar.refresh_undo_redo_state()

        except Exception:
            dlg = MsgDialog(self, "messages.errors.crop_failed.title",
                            "messages.errors.crop_failed.title")
            dlg.show_nonmodal()
