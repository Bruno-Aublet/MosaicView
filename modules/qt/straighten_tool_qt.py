"""
modules/qt/straighten_tool_qt.py — Outil de redressement manuel (trait de
référence) de la barre d'outils flottante de la visionneuse principale
(image_viewer_qt.py).

Fusion progressive des visionneuses (idees.txt #3, 2e outil migré) : ce module
contient toute la logique propre à l'outil "straighten" manuel — état/
interactions du canvas (mixin StraightenCanvasMixin, hérité par
_ViewerCanvas), commit de la rotation dans l'historique du panneau (mixin
StraightenViewerMixin, hérité par ImageViewer), et le panneau flottant de la
spinbox d'angle (_StraightenAnglePanel). image_viewer_qt.py ne fait qu'hériter
de ces deux mixins et brancher l'icône de la barre d'outils — voir CLAUDE.md
règle "ne jamais migrer le code d'un outil dans image_viewer_qt.py".

Le redressement AUTOMATIQUE (deskew, icône bi-mode, clic droit pour basculer)
vit aussi ici (StraightenViewerMixin.perform_auto_straighten) puisqu'il
partage l'icône et le mode bi-mode avec le manuel — le traitement par lot
(deskew_selected_qt, plusieurs pages à la fois) reste dans deskew_qt.py,
inchangé, hors périmètre de ce module.

Le bouton "Valider" flottant du canvas reste PARTAGÉ avec le crop (un seul
widget, texte/action selon l'outil actif, voir image_viewer_qt.py::
_ViewerCanvas._show_validate_btn/_VALIDATE_KEYS) et n'est donc PAS dans ce
module — il resterait un point de couplage artificiel à extraire seul pour un
outil qui n'existe qu'à deux (crop+straighten). Idem pour la persistance par
page (_straighten_by_page), qui suit exactement le même pattern que
_crop_by_page et reste dans ImageViewer pour cette raison.
"""

from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QDoubleSpinBox
from PySide6.QtCore import Qt, QPoint

from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.straighten_geometry import line_to_correction as _line_to_correction


# ─────────────────────────────────────────────────────────────────────────────
# Panneau flottant de la spinbox d'angle
# ─────────────────────────────────────────────────────────────────────────────

class _StraightenAnglePanel(QWidget):
    """Panneau flottant avec la spinbox d'angle de correction, affiché sous la
    barre d'outils uniquement quand l'outil "straighten" est actif ET qu'un
    trait de référence existe sur la page courante. Même principe que le
    bouton "Valider" flottant : jamais inséré dans le layout de ImageViewer.

    Reprend le calcul d'angle et le comportement de la spinbox déjà en place
    dans l'ancienne straighten_viewer_qt.py (voir skill page-straighten) — la
    spinbox est la source de vérité de sa propre valeur pendant l'édition
    manuelle.
    """

    def __init__(self, viewer: "ImageViewer"):
        super().__init__(viewer._canvas)
        # Sans cet attribut, un QWidget nu n'applique jamais le "background"
        # d'une stylesheet (contrairement à QLabel/QPushButton) — le panneau
        # restait invisible malgré le style posé dans _apply_theme (signalé
        # par l'utilisateur en conditions réelles).
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._viewer = viewer
        self._angle_category: str | None = None
        self._angle_vertical_sign = 1
        self.pending_angle: float | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        self._label = QLabel()
        layout.addWidget(self._label)

        self._spin = QDoubleSpinBox()
        self._spin.setRange(-90.0, 90.0)
        self._spin.setDecimals(2)
        self._spin.setSingleStep(0.1)
        self._spin.setSuffix("°")
        self._spin.setValue(0.0)
        self._spin.valueChanged.connect(self._on_spin_changed)
        layout.addWidget(self._spin)

        self.hide()

    def _apply_theme(self):
        from modules.qt.image_viewer_qt import _floating_options_panel_style
        theme = get_current_theme()
        self.setStyleSheet(_floating_options_panel_style(theme, "_StraightenAnglePanel"))
        self._label.setStyleSheet(f"color: {theme['text']}; background: transparent;")
        self._spin.setStyleSheet(
            f"QDoubleSpinBox {{ background: {theme['bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 2px 4px; }} "
            f"QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 16px; }}"
        )

    def retranslate(self):
        font = _get_current_font(11)
        self._label.setText(_("dialogs.straighten_viewer.angle_label"))
        self._label.setFont(font)
        self._spin.setFont(font)

    # ── Visibilité ────────────────────────────────────────────────────────────

    def set_visible_for_tool(self, tool_id: str | None):
        canvas = self._viewer._canvas
        if tool_id == "straighten" and canvas.has_line:
            self.show()
            self.reposition()
            self.raise_()
        else:
            self.hide()

    def reposition(self):
        self.adjustSize()
        canvas = self._viewer._canvas
        x = (canvas.width() - self.width()) // 2
        y = 8 + self._viewer._toolbar.height() + 6
        self.move(max(0, x), y)

    def enterEvent(self, event):
        # Suspend le timer d'auto-masquage de la barre (dont ce panneau suit
        # désormais la visibilité, idees.txt #3 décision 2026-08-14) tant que
        # la souris reste sur ce panneau — pas seulement redémarré à chaque
        # mouvement, complètement arrêté (voir _ViewerToolbar.pause_hide).
        self._viewer._toolbar.pause_hide()

    def leaveEvent(self, event):
        self._viewer._toolbar.resume_hide()

    # ── Ligne tracée / éditée ────────────────────────────────────────────────

    def reset(self):
        """Aucun trait courant : vide et désactive la spinbox, masque le panneau."""
        self._angle_category = None
        self._angle_vertical_sign = 1
        self.pending_angle = None
        self._set_spin_value(0.0)
        self.hide()

    def on_line_live(self, ix1, iy1, ix2, iy2):
        correction, category, vertical_sign = _line_to_correction(ix1, iy1, ix2, iy2)
        if correction is None:
            return
        self._angle_category = category
        self._angle_vertical_sign = vertical_sign
        self._set_spin_value(correction)
        self.show()
        self.reposition()
        self.raise_()

    def on_line_drawn(self, ix1, iy1, ix2, iy2):
        correction, category, vertical_sign = _line_to_correction(ix1, iy1, ix2, iy2)
        if correction is None:
            return
        self.pending_angle = correction
        self._angle_category = category
        self._angle_vertical_sign = vertical_sign
        self._set_spin_value(correction)
        self.show()
        self.reposition()
        self.raise_()

    def _set_spin_value(self, value: float):
        self._spin.blockSignals(True)
        self._spin.setValue(value)
        self._spin.blockSignals(False)

    def _on_spin_changed(self, value: float):
        if self._angle_category is None:
            return
        canvas = self._viewer._canvas
        canvas.set_line_end_from_angle(value, self._angle_category, self._angle_vertical_sign)
        self.pending_angle = value


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état et interactions souris de l'outil (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class StraightenCanvasMixin:
    """Hérité par _ViewerCanvas (image_viewer_qt.py) en plus de QLabel : ajoute
    l'état et les méthodes de l'outil "straighten" manuel au canvas de la
    visionneuse, sans que leur code vive dans image_viewer_qt.py. Suppose que
    l'hôte a déjà self._viewer (ImageViewer), self._hide_validate_btn()
    (bouton Valider partagé avec le crop, resté dans image_viewer_qt.py), et
    les attributs habituels de _ViewerCanvas (display_offset_x/y).

    Coordonnées widget dérivées pour le dessin/l'interaction, coordonnées
    image stables comme source de vérité (survivent au pan/zoom/resize) —
    même principe que le rectangle de crop.
    """

    _LINE_HANDLE_RADIUS = 7
    _LINE_HANDLE_HIT = 12

    def _init_straighten_state(self):
        self._line_start: QPoint | None = None
        self._line_end:   QPoint | None = None
        self._line_img_start: tuple | None = None
        self._line_img_end:   tuple | None = None
        self._line_drawing = False
        self._line_dragging_handle: str | None = None

    @property
    def has_line(self) -> bool:
        return self._line_start is not None and self._line_end is not None

    def clear_line(self):
        self._line_start = None
        self._line_end = None
        self._line_img_start = None
        self._line_img_end = None
        self._line_drawing = False
        self._line_dragging_handle = None
        self._hide_validate_btn()
        self.update()

    def _line_widget_to_image(self, pt: QPoint) -> tuple:
        zoom = self._viewer.zoom_level or 1.0
        ix = (pt.x() - self.display_offset_x) / zoom
        iy = (pt.y() - self.display_offset_y) / zoom
        return ix, iy

    def _line_image_to_widget(self, ix: float, iy: float) -> QPoint:
        zoom = self._viewer.zoom_level or 1.0
        return QPoint(int(self.display_offset_x + ix * zoom),
                       int(self.display_offset_y + iy * zoom))

    def _sync_line_from_image(self):
        """Recalcule _line_start/_line_end (widget) depuis les coordonnées image
        stables, après un pan, un zoom, ou un redimensionnement — même principe
        que l'ancienne straighten_viewer_qt.py::_StraightenImageWidget._sync_line_from_image."""
        if self._line_img_start is None or self._line_img_end is None:
            return
        self._line_start = self._line_image_to_widget(*self._line_img_start)
        self._line_end = self._line_image_to_widget(*self._line_img_end)

    def _hit_line_handle(self, pos: QPoint) -> str | None:
        if self._line_start is None or self._line_end is None:
            return None
        for name, pt in (('start', self._line_start), ('end', self._line_end)):
            dx = pos.x() - pt.x()
            dy = pos.y() - pt.y()
            if dx * dx + dy * dy <= self._LINE_HANDLE_HIT * self._LINE_HANDLE_HIT:
                return name
        return None

    def _notify_line_drawn(self):
        """Fige les coordonnées image du trait (relâchement de la souris) et
        prévient le viewer pour qu'il calcule l'angle et active le bouton Valider."""
        if self._line_start is None or self._line_end is None:
            return
        self._line_img_start = self._line_widget_to_image(self._line_start)
        self._line_img_end = self._line_widget_to_image(self._line_end)
        if self._line_start != self._line_end:
            # En mode double page/continu/webtoon avec un trait tracé → passe en
            # simple page, même règle que le crop.
            if self._viewer.page_mode != "single":
                self._viewer.page_mode = "single"
                self._viewer.display_image(keep_crop_rect=True)
            ix1, iy1 = self._line_img_start
            ix2, iy2 = self._line_img_end
            self._viewer._on_straighten_line_drawn(ix1, iy1, ix2, iy2)

    def _notify_line_live(self):
        """Pendant le tracé initial ou le drag d'une poignée : n'écrit pas dans
        les coordonnées image figées (réservé au relâchement), juste un aperçu
        de l'angle en direct pour la spinbox."""
        if self._line_start is None or self._line_end is None or self._line_start == self._line_end:
            return
        ix1, iy1 = self._line_widget_to_image(self._line_start)
        ix2, iy2 = self._line_widget_to_image(self._line_end)
        self._viewer._on_straighten_line_live(ix1, iy1, ix2, iy2)

    def set_line_end_from_angle(self, correction_deg, category, vertical_sign):
        """Redéplace le 2e point du trait pour obtenir l'angle de correction voulu
        (premier point et longueur inchangés) — appelé depuis la spinbox d'angle.
        Fige directement les coordonnées image SANS repasser par _notify_line_drawn :
        pendant l'édition via la spinbox, celle-ci est la source de vérité de sa
        propre valeur (voir piège documenté dans le skill page-straighten)."""
        if self._line_start is None or self._line_end is None:
            return
        import math
        dx0 = self._line_end.x() - self._line_start.x()
        dy0 = self._line_end.y() - self._line_start.y()
        length = math.hypot(dx0, dy0)
        if length < 1e-6:
            return
        if category == 'h':
            angle_deg = correction_deg
        elif vertical_sign >= 0:
            angle_deg = correction_deg + 90
        else:
            angle_deg = correction_deg - 90
        angle_rad = math.radians(angle_deg)
        new_dx = length * math.cos(angle_rad)
        new_dy = length * math.sin(angle_rad)
        self._line_end = QPoint(
            int(round(self._line_start.x() + new_dx)),
            int(round(self._line_start.y() + new_dy)))
        self._line_img_start = self._line_widget_to_image(self._line_start)
        self._line_img_end = self._line_widget_to_image(self._line_end)
        self.update()

    @staticmethod
    def _draw_line_handle(painter, pt):
        from PySide6.QtGui import QPen, QColor
        from PySide6.QtCore import QRectF
        r = StraightenCanvasMixin._LINE_HANDLE_RADIUS
        painter.setPen(QPen(QColor(255, 255, 255), 1.5, Qt.PenStyle.SolidLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(pt.x() - r - 1, pt.y() - r - 1, (r + 1) * 2, (r + 1) * 2))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 0, 0))
        painter.drawEllipse(QRectF(pt.x() - r, pt.y() - r, r * 2, r * 2))

    # ── Rendu (appelé depuis _ViewerCanvas.paintEvent) ───────────────────────

    def paint_straighten_line(self, painter):
        """À appeler en fin de paintEvent, après l'image. Trait rouge (actif) /
        gris (conservé mais outil désélectionné) — même principe que le
        rectangle de crop."""
        from PySide6.QtGui import QPen, QColor
        if not self.has_line:
            return
        if not self._line_drawing and self._line_dragging_handle is None:
            self._sync_line_from_image()
        straighten_active = self._viewer._toolbar.active_tool == "straighten"
        pen = QPen(QColor("red") if straighten_active else QColor("#888888"), 2)
        painter.setPen(pen)
        painter.drawLine(self._line_start, self._line_end)
        self._draw_line_handle(painter, self._line_start)
        self._draw_line_handle(painter, self._line_end)

    # ── Événements souris (appelés depuis _ViewerCanvas.mousePress/Move/ReleaseEvent) ──

    def straighten_mouse_press(self, event):
        pos = event.position().toPoint()
        hit = self._hit_line_handle(pos)
        if hit:
            self._line_dragging_handle = hit
        else:
            self._hide_validate_btn()
            self._line_drawing = True
            self._line_start = pos
            self._line_end = pos
        self.update()

    def straighten_update_cursor(self, event):
        """Curseur hors tracé (pas de bouton enfoncé), uniquement si l'outil
        straighten est actif : croix simple, ou curseur de déplacement sur une
        poignée du trait."""
        hit = self._hit_line_handle(event.position().toPoint())
        self.setCursor(Qt.SizeAllCursor if hit else Qt.CrossCursor)

    def straighten_mouse_move(self, event):
        if self._line_dragging_handle is not None:
            pos = event.position().toPoint()
            if self._line_dragging_handle == 'start':
                self._line_start = pos
            else:
                self._line_end = pos
            self.update()
            self._notify_line_live()
        elif self._line_drawing:
            self._line_end = event.position().toPoint()
            self.update()
            self._notify_line_live()

    def straighten_mouse_release(self, event):
        if self._line_dragging_handle is not None:
            self._line_dragging_handle = None
            self._notify_line_drawn()
        elif self._line_drawing:
            self._line_drawing = False
            self._line_end = event.position().toPoint()
            self.update()
            self._notify_line_drawn()


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — commit de la rotation dans l'historique du panneau (hérité par ImageViewer)
# ─────────────────────────────────────────────────────────────────────────────

class StraightenViewerMixin:
    """Hérité par ImageViewer (image_viewer_qt.py) en plus de QDialog : ajoute
    la logique de redressement manuel + automatique au viewer, sans que son
    code vive dans image_viewer_qt.py. Suppose que l'hôte a déjà self._canvas
    (_ViewerCanvas, avec StraightenCanvasMixin), self.callbacks,
    self.current_idx, self._toolbar (avec _angle_panel), et
    self._straighten_by_page (persistance par page — reste dans ImageViewer,
    voir la note du docstring de module).
    """

    def _on_straighten_line_drawn(self, ix1, iy1, ix2, iy2):
        """Trait figé (relâchement de la souris) : calcule l'angle, alimente le
        panneau flottant, active le bouton Valider."""
        self._toolbar._angle_panel.on_line_drawn(ix1, iy1, ix2, iy2)
        self._toolbar._angle_panel.set_visible_for_tool(self._toolbar.active_tool)
        self._canvas._show_validate_btn()

    def _on_straighten_line_live(self, ix1, iy1, ix2, iy2):
        """Pendant le tracé initial ou le drag d'une poignée : aperçu en direct
        de l'angle dans le panneau flottant, sans toucher au bouton Valider."""
        self._toolbar._angle_panel.on_line_live(ix1, iy1, ix2, iy2)

    def validate_straighten(self):
        from modules.qt.dialogs_qt import MsgDialog
        if not self._canvas.has_line:
            dlg = MsgDialog(
                self,
                "messages.warnings.no_straighten_line.title",
                "messages.warnings.no_straighten_line.message",
            )
            dlg.show_nonmodal()
            return
        self.perform_straighten()

    def perform_straighten(self):
        from PIL import Image
        from modules.qt import state as _state_module
        from modules.qt.entries import ensure_image_loaded, save_image_to_bytes
        from modules.qt.dialogs_qt import MsgDialog

        angle = self._toolbar._angle_panel.pending_angle
        if angle is None or abs(angle) < 0.001:
            return

        state = self.callbacks.get('state') or _state_module.state
        save_state    = self.callbacks.get("save_state")
        render_mosaic = self.callbacks.get("render_mosaic")
        update_btn    = self.callbacks.get("update_button_text")
        canvas        = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]
            original_img = ensure_image_loaded(entry)
            if original_img is None:
                dlg = MsgDialog(self, "messages.errors.straighten_failed.title",
                                "messages.errors.straighten_failed.title")
                dlg.show_nonmodal()
                return

            if save_state:
                save_state()

            rotated = original_img.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
            entry["img"]   = rotated
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
            self._canvas.clear_line()
            self._toolbar._angle_panel.reset()
            self._straighten_by_page.pop(self.current_idx, None)
            self.display_image()
            self._toolbar.refresh_undo_redo_state()

        except Exception:
            dlg = MsgDialog(self, "messages.errors.straighten_failed.title",
                            "messages.errors.straighten_failed.title")
            dlg.show_nonmodal()

    def perform_auto_straighten(self):
        """Redressement automatique (deskew) de la page actuellement affichée,
        déclenché par un clic gauche sur l'icône Redressage de la barre
        d'outils quand state.straighten_mode == 1 (bascule par clic droit).
        Contrairement au traitement par lot de deskew_selected_qt (skill
        page-straighten), s'applique uniquement à cette page, pas à la
        sélection de la mosaïque, et reste synchrone (une seule image)."""
        from modules.qt import state as _state_module
        from modules.qt.dialogs_qt import MsgDialog

        state = self.callbacks.get('state') or _state_module.state
        save_state    = self.callbacks.get("save_state")
        render_mosaic = self.callbacks.get("render_mosaic")
        update_btn    = self.callbacks.get("update_button_text")
        canvas        = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]
            if not entry.get("is_image") or entry.get("is_corrupted"):
                return

            from modules.qt.deskew import detect_skew_angle, deskew_entry_data

            angle = detect_skew_angle(entry)
            if angle is None or abs(angle) < 0.001:
                dlg = MsgDialog(self, "messages.warnings.no_skew_detected.title",
                                "messages.warnings.no_skew_detected.message")
                dlg.show_nonmodal()
                return

            if save_state:
                save_state()

            ok = deskew_entry_data(entry, state)
            if not ok:
                # detect_skew_angle vient de réussir juste au-dessus : cette
                # branche ne devrait pas se produire en pratique (aucun état
                # à annuler puisque rien n'a encore été modifié), gardée par
                # cohérence avec le contrat de deskew_entry_data.
                return

            state.modified = True
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
            self.display_image()
            self._toolbar.refresh_undo_redo_state()

        except Exception:
            dlg = MsgDialog(self, "messages.errors.straighten_failed.title",
                            "messages.errors.straighten_failed.title")
            dlg.show_nonmodal()

    def _save_straighten_for_current_page(self):
        """Mémorise le trait de redressage de la page qu'on s'apprête à quitter
        (coordonnées image, indépendantes du zoom/pan) pour le restaurer si
        l'utilisateur revient sur cette page — même principe que le crop
        (idees.txt #3, partie B)."""
        c = self._canvas
        if c._line_img_start is not None and c._line_img_end is not None:
            self._straighten_by_page[self.current_idx] = (c._line_img_start, c._line_img_end)
        else:
            self._straighten_by_page.pop(self.current_idx, None)

    def _restore_straighten_for_page(self, idx: int):
        """Réapplique le trait de redressage mémorisé pour la page idx, s'il existe."""
        saved = self._straighten_by_page.get(idx)
        c = self._canvas
        if saved is None:
            c.clear_line()
            self._toolbar._angle_panel.reset()
            return
        c._line_img_start, c._line_img_end = saved
        c._sync_line_from_image()
        ix1, iy1 = c._line_img_start
        ix2, iy2 = c._line_img_end
        self._toolbar._angle_panel.on_line_drawn(ix1, iy1, ix2, iy2)
