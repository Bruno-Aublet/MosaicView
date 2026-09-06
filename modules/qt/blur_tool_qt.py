"""
modules/qt/blur_tool_qt.py — Outil de tampon de flou de la barre d'outils
flottante de la visionneuse principale (image_viewer_qt.py).

Fusion progressive des visionneuses : ce module contient toute la logique
propre à l'outil "blur" — état/interactions du canvas (mixin
BlurCanvasMixin, hérité par _ViewerCanvas), commit du stroke dans
l'historique du panneau (mixin BlurViewerMixin, hérité par ImageViewer), et
le panneau flottant de réglages (_BlurOptionsPanel). image_viewer_qt.py ne
fait qu'hériter de ces deux mixins et brancher l'icône de la barre d'outils
— voir CLAUDE.md règle "ne jamais migrer le code d'un outil dans
image_viewer_qt.py".

Même famille de comportement que le clonage (clone_tool_qt.py) : peint en
continu, chaque coup de tampon modifie déjà l'image et devient sa propre
entrée d'historique au relâchement — pas de bouton "Valider" pour cet
outil, pas de persistance de travail non validé par page, pas d'historique
local séparé (undo/redo unifié, celui du panneau). Contrairement au
clonage, aucune notion de source à définir (Ctrl+clic) : le pinceau floute
directement la zone sous le curseur, dans un disque plein (pas de dégradé
sur le bord — objectif assumé de rendre un contenu illisible de façon
fiable, un bord dégradé laisserait une frange encore lisible). Repasser
plusieurs fois sur la même zone accumule le flou (le calcul repart à
chaque frame de l'image de travail déjà floutée, jamais de l'image
d'origine).
"""

import io

from PIL import Image, ImageFilter, ImageDraw

from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QFrame, QSlider, QSpinBox
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QPixmap, QCursor, QPainter, QPen, QColor

from modules.qt import state as _state_module
from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.entries import save_image_to_bytes
from modules.qt.dialogs_qt import MsgDialog
from modules.qt.clone_tool_qt import floating_options_panel_style


# ─────────────────────────────────────────────────────────────────────────────
# Rendu pur (curseur en cercle)
# ─────────────────────────────────────────────────────────────────────────────

def make_blur_brush_cursor(r_screen: int) -> QCursor:
    """Curseur en cercle plein (contour seulement), rayon r_screen px —
    prévisualise la zone qui sera floutée, sans croix centrale (pas de
    notion de "point exact visé" ici, contrairement à la pipette des
    niveaux/de la transparence : tout le disque est traité identiquement)."""
    r = max(1, r_screen)
    margin = 4
    size = (r + margin) * 2 + 1
    cx = cy = size // 2

    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(255, 255, 255, 200), 2.5))
    painter.drawEllipse(QPoint(cx, cy), r + 1, r + 1)
    painter.setPen(QPen(QColor(40, 40, 40, 230), 1.5))
    painter.drawEllipse(QPoint(cx, cy), r, r)
    painter.end()

    return QCursor(pm, cx, cy)


# ─────────────────────────────────────────────────────────────────────────────
# Panneau flottant de réglages (taille du pinceau, puissance du flou)
# ─────────────────────────────────────────────────────────────────────────────

class _BlurOptionsPanel(QWidget):
    """Panneau flottant avec les réglages du tampon de flou (taille du
    pinceau, puissance du flou), affiché sous la barre d'outils uniquement
    quand l'outil "blur" est actif — même principe que _CloneOptionsPanel.

    Deux réglettes indépendantes : la taille couvre la zone traitée (diamètre
    du disque, mêmes bornes que le pinceau de clonage), la puissance couvre
    le rayon du flou gaussien appliqué à l'intérieur de ce disque — utile
    pour rendre du texte illisible sans devoir agrandir le pinceau."""

    _BRUSH_SIZE_MIN = 1
    _BRUSH_SIZE_MAX = 400
    _BRUSH_SIZE_DEFAULT = 40

    _STRENGTH_MIN = 1
    _STRENGTH_MAX = 30
    _STRENGTH_DEFAULT = 8

    def __init__(self, viewer: "ImageViewer"):
        super().__init__(viewer._canvas)
        # Sans cet attribut, un QWidget nu n'applique jamais le "background"
        # d'une stylesheet (contrairement à QLabel/QPushButton).
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._viewer = viewer

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        self._lbl_brush = QLabel()
        layout.addWidget(self._lbl_brush)

        self._brush_slider = QSlider(Qt.Orientation.Horizontal)
        self._brush_slider.setMinimum(self._BRUSH_SIZE_MIN)
        self._brush_slider.setMaximum(self._BRUSH_SIZE_MAX)
        self._brush_slider.setValue(self._BRUSH_SIZE_DEFAULT)
        self._brush_slider.setFixedWidth(120)
        self._brush_slider.valueChanged.connect(self._on_brush_slider_changed)
        layout.addWidget(self._brush_slider)

        self._brush_spin = QSpinBox()
        self._brush_spin.setRange(self._BRUSH_SIZE_MIN, self._BRUSH_SIZE_MAX)
        self._brush_spin.setValue(self._BRUSH_SIZE_DEFAULT)
        self._brush_spin.setFixedWidth(62)
        self._brush_spin.valueChanged.connect(self._on_brush_spin_changed)
        layout.addWidget(self._brush_spin)

        self._sep = QFrame()
        self._sep.setFrameShape(QFrame.Shape.VLine)
        layout.addWidget(self._sep)

        self._lbl_strength = QLabel()
        layout.addWidget(self._lbl_strength)

        self._strength_slider = QSlider(Qt.Orientation.Horizontal)
        self._strength_slider.setMinimum(self._STRENGTH_MIN)
        self._strength_slider.setMaximum(self._STRENGTH_MAX)
        self._strength_slider.setValue(self._STRENGTH_DEFAULT)
        self._strength_slider.setFixedWidth(120)
        self._strength_slider.valueChanged.connect(self._on_strength_slider_changed)
        layout.addWidget(self._strength_slider)

        self._strength_spin = QSpinBox()
        self._strength_spin.setRange(self._STRENGTH_MIN, self._STRENGTH_MAX)
        self._strength_spin.setValue(self._STRENGTH_DEFAULT)
        self._strength_spin.setFixedWidth(62)
        self._strength_spin.valueChanged.connect(self._on_strength_spin_changed)
        layout.addWidget(self._strength_spin)

        self.hide()

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(floating_options_panel_style(theme, "_BlurOptionsPanel"))
        self._lbl_brush.setStyleSheet(f"color: {theme['text']}; background: transparent;")
        self._lbl_strength.setStyleSheet(f"color: {theme['text']}; background: transparent;")
        self._sep.setStyleSheet(f"color: {theme['separator']};")
        self._brush_spin.setStyleSheet(
            f"QSpinBox {{ background: {theme['bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 2px 4px; }} "
            f"QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}"
        )
        self._strength_spin.setStyleSheet(
            f"QSpinBox {{ background: {theme['bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 2px 4px; }} "
            f"QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}"
        )
        # Style explicite de la piste/du curseur : le rendu natif disparaît
        # sur ce fond stylé (WA_StyledBackground) — même piège que
        # _CloneOptionsPanel, sub-page/add-page DOIVENT être stylés dès que
        # groove/handle le sont.
        accent = "#4a90d9"
        slider_style = (
            f"QSlider {{ background: transparent; }} "
            f"QSlider::groove:horizontal {{ height: 4px; background: {theme['separator']}; "
            f"border-radius: 2px; }} "
            f"QSlider::sub-page:horizontal {{ height: 4px; background: {accent}; "
            f"border-radius: 2px; }} "
            f"QSlider::add-page:horizontal {{ height: 4px; background: {theme['separator']}; "
            f"border-radius: 2px; }} "
            f"QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0; "
            f"background: {accent}; border: 1px solid {theme['text']}; border-radius: 7px; }}"
        )
        self._brush_slider.setStyleSheet(slider_style)
        self._strength_slider.setStyleSheet(slider_style)

    def retranslate(self):
        font = _get_current_font(11)
        self._lbl_brush.setText(_("viewer.blur_brush_size_label"))
        self._lbl_brush.setFont(font)
        self._lbl_strength.setText(_("viewer.blur_strength_label"))
        self._lbl_strength.setFont(font)

    # ── Visibilité ────────────────────────────────────────────────────────────

    def set_visible_for_tool(self, tool_id: str | None):
        if tool_id == "blur":
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

    def mousePressEvent(self, event):
        # Sans ce blindage, un clic sur une zone vide du panneau "fuit" vers
        # _ViewerCanvas en dessous — même piège déjà documenté pour
        # _ToolButton/_ActionButton/_ViewerToolbar (skill viewers), appliqué
        # par cohérence à tous les panneaux flottants.
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def enterEvent(self, event):
        # Suspend le timer d'auto-masquage de la barre tant que la souris
        # reste sur ce panneau (voir _ViewerToolbar.pause_hide).
        self._viewer._toolbar.pause_hide()
        # Le curseur en cercle (blur_update_cursor) est celui du CANVAS, pas
        # celui de ce panneau — sans ce reset, il resterait affiché
        # par-dessus les contrôles du panneau, même piège que
        # _CloneOptionsPanel.
        self.setCursor(Qt.ArrowCursor)

    def leaveEvent(self, event):
        # Revérification différée à 0ms : Qt peut envoyer un Leave en
        # transitant entre deux widgets enfants même quand la souris reste
        # visuellement sur le panneau (même piège que _CloneOptionsPanel).
        from PySide6.QtCore import QTimer as _QTimer
        _QTimer.singleShot(0, self._check_really_left)

    def _check_really_left(self):
        really_left = not self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        if really_left:
            self._viewer._toolbar.resume_hide()
            self.unsetCursor()

    # ── Réglages ─────────────────────────────────────────────────────────────

    def _on_brush_slider_changed(self, value: int):
        if self._brush_spin.value() != value:
            self._brush_spin.blockSignals(True)
            self._brush_spin.setValue(value)
            self._brush_spin.blockSignals(False)
        self._viewer._canvas.set_blur_brush_radius(value)

    def _on_brush_spin_changed(self, value: int):
        if self._brush_slider.value() != value:
            self._brush_slider.blockSignals(True)
            self._brush_slider.setValue(value)
            self._brush_slider.blockSignals(False)
        self._viewer._canvas.set_blur_brush_radius(value)

    def _on_strength_slider_changed(self, value: int):
        if self._strength_spin.value() != value:
            self._strength_spin.blockSignals(True)
            self._strength_spin.setValue(value)
            self._strength_spin.blockSignals(False)
        self._viewer._canvas.set_blur_strength(value)

    def _on_strength_spin_changed(self, value: int):
        if self._strength_slider.value() != value:
            self._strength_slider.blockSignals(True)
            self._strength_slider.setValue(value)
            self._strength_slider.blockSignals(False)
        self._viewer._canvas.set_blur_strength(value)


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état et interactions souris de l'outil (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class BlurCanvasMixin:
    """Hérité par _ViewerCanvas (image_viewer_qt.py) en plus de QLabel : ajoute
    l'état et les méthodes de l'outil "blur" au canvas de la visionneuse,
    sans que leur code vive dans image_viewer_qt.py. Suppose que l'hôte a
    déjà self._viewer (ImageViewer) et les attributs habituels de
    _ViewerCanvas (display_offset_x/y).

    Aucun overlay à resynchroniser au pan/zoom/resize (contrairement à
    crop/straighten/clone) : rien n'est dessiné en dehors du curseur du
    pinceau lui-même, qui suit nativement la souris."""

    def _init_blur_state(self):
        self._blur_painting = False
        self._blur_paint_last: QPoint | None = None
        self._blur_brush_radius = _BlurOptionsPanel._BRUSH_SIZE_DEFAULT
        self._blur_strength = _BlurOptionsPanel._STRENGTH_DEFAULT
        self._blur_brush_cursor = None

    def set_blur_brush_radius(self, r: int):
        self._blur_brush_radius = r
        self._rebuild_blur_cursor()

    def set_blur_strength(self, s: int):
        self._blur_strength = s

    def _rebuild_blur_cursor(self):
        zoom = self._viewer.zoom_level or 1.0
        r_screen = max(1, int(self._blur_brush_radius * zoom / 2))
        self._blur_brush_cursor = make_blur_brush_cursor(r_screen)

    def _blur_widget_to_image(self, pt: QPoint) -> tuple:
        zoom = self._viewer.zoom_level or 1.0
        ix = (pt.x() - self.display_offset_x) / zoom
        iy = (pt.y() - self.display_offset_y) / zoom
        return ix, iy

    # ── Événements souris (appelés depuis _ViewerCanvas.mousePress/Move/ReleaseEvent) ──

    def blur_mouse_press(self, event) -> bool:
        pos = event.position().toPoint()
        self._blur_painting = True
        self._blur_paint_last = pos
        ix, iy = self._blur_widget_to_image(pos)
        self._viewer._on_blur_paint_stroke(ix, iy)
        return True

    def blur_update_cursor(self, event):
        # Reconstruit à chaque survol (pas seulement la première fois) : le
        # rayon écran dépend du zoom courant, qui peut avoir changé (molette,
        # Ctrl+0/1/+/-) depuis la dernière fois que ce curseur a été construit.
        self._rebuild_blur_cursor()
        self.setCursor(self._blur_brush_cursor)

    def blur_mouse_move(self, event) -> bool:
        """Retourne True si géré (bouton gauche enfoncé, outil blur actif)."""
        if not self._blur_painting:
            return False
        pos = event.position().toPoint()
        # Interpolation si déplacement rapide, même principe que le clonage
        # (clone_tool_qt.py::CloneCanvasMixin.clone_mouse_move) — sans ça, un
        # déplacement rapide de la souris laisse des trous non floutés dans
        # le trait.
        if self._blur_paint_last is not None:
            dx = pos.x() - self._blur_paint_last.x()
            dy = pos.y() - self._blur_paint_last.y()
            dist = (dx * dx + dy * dy) ** 0.5
            zoom = self._viewer.zoom_level or 1.0
            step = max(1, int(zoom * self._blur_brush_radius * 0.5))
            if dist >= step:
                steps = max(1, int(dist / step))
                for i in range(1, steps + 1):
                    t = i / steps
                    ix = self._blur_paint_last.x() + int(dx * t)
                    iy = self._blur_paint_last.y() + int(dy * t)
                    iix2, iiy2 = self._blur_widget_to_image(QPoint(ix, iy))
                    self._viewer._on_blur_paint_stroke(iix2, iiy2)
                self._blur_paint_last = pos
        else:
            iix, iiy = self._blur_widget_to_image(pos)
            self._viewer._on_blur_paint_stroke(iix, iiy)
            self._blur_paint_last = pos
        return True

    def blur_mouse_release(self, event) -> bool:
        """Retourne True si géré (un stroke était en cours)."""
        if not self._blur_painting:
            return False
        self._blur_painting = False
        self._blur_paint_last = None
        self._viewer._on_blur_paint_end()
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — commit du stroke dans l'historique du panneau (hérité par ImageViewer)
# ─────────────────────────────────────────────────────────────────────────────

class BlurViewerMixin:
    """Hérité par ImageViewer (image_viewer_qt.py) en plus de QDialog : ajoute
    la logique de peinture/commit de l'outil "blur" au viewer, sans que son
    code vive dans image_viewer_qt.py. Suppose que l'hôte a déjà self._canvas
    (_ViewerCanvas, avec BlurCanvasMixin), self.callbacks, self.current_idx,
    self.zoom_level, self.page_mode, self._toolbar.
    """

    def _init_blur_viewer_state(self):
        self._blur_work_img = None       # copie PIL RGBA de travail, existe seulement pendant un stroke
        self._blur_stroke_dirty = False
        from PySide6.QtCore import QElapsedTimer
        self._blur_display_timer = QElapsedTimer()
        self._blur_display_timer.start()

    def _on_blur_paint_stroke(self, ix: float, iy: float):
        """Clic gauche maintenu : applique le flou gaussien dans le disque
        centré sur (ix, iy), coordonnées image. Repasser plusieurs fois sur
        la même zone accumule le flou : chaque application repart de l'image
        de travail déjà floutée par les applications précédentes (jamais de
        l'image d'origine)."""
        if not self._blur_stroke_dirty:
            # Première application de ce stroke : charge l'image de travail
            # depuis l'entrée actuelle, même principe que
            # clone_tool_qt.py::CloneViewerMixin._on_clone_paint_stroke.
            entry = (self.callbacks.get('state') or _state_module.state).images_data[self.current_idx]
            if not entry.get('bytes'):
                return
            try:
                img = Image.open(io.BytesIO(entry['bytes']))
                if '_orig_mode' not in entry:
                    entry['_orig_mode'] = img.mode
                self._blur_work_img = img.convert('RGBA')
            except Exception:
                return
            self._blur_stroke_dirty = True
            self._macro_blur_points = [(ix, iy)]
            # En mode double page/continu/webtoon, un stroke effectif force le
            # retour en simple page — même règle que crop/straighten/clone.
            if self.page_mode != "single":
                self.page_mode = "single"

        self._blur_apply_stamp(ix, iy)

        if getattr(self, '_macro_recording', False):
            self._macro_blur_points.append((ix, iy))

        if self._blur_display_timer.elapsed() >= 33:
            self._blur_refresh_display()
            self._blur_display_timer.restart()

    def _blur_apply_stamp(self, dest_x: float, dest_y: float):
        """Floute un carré local englobant le disque de destination (marge =
        rayon du flou gaussien, pour que le calcul du flou "voie" assez de
        contexte autour du bord du disque) puis ne colle le résultat que dans
        le disque plein — pas de dégradé sur le bord (censure fiable, voir
        docstring de module)."""
        dst = self._blur_work_img
        w, h = dst.size
        r = (self._canvas._blur_brush_radius - 1) / 2
        strength = self._canvas._blur_strength
        margin = int(strength) + 2

        left   = max(0, int(dest_x - r - margin))
        top    = max(0, int(dest_y - r - margin))
        right  = min(w, int(dest_x + r + margin) + 1)
        bottom = min(h, int(dest_y + r + margin) + 1)
        if left >= right or top >= bottom:
            return

        region = dst.crop((left, top, right, bottom))
        blurred = region.filter(ImageFilter.GaussianBlur(radius=strength))

        mask = Image.new('L', region.size, 0)
        cx = dest_x - left
        cy = dest_y - top
        draw = ImageDraw.Draw(mask)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
        dst.paste(blurred, (left, top), mask=mask)

    def _blur_refresh_display(self):
        """Affiche directement l'image de travail pendant le stroke, sans
        repasser par entry['bytes']/ensure_image_loaded/display_image() —
        même raison que clone_tool_qt.py::CloneViewerMixin._clone_refresh_display
        (recharger/réencoder à chaque frame serait trop coûteux)."""
        if self._blur_work_img is None:
            return
        from modules.qt.image_viewer_qt import _pil_to_qpixmap
        pixmap = _pil_to_qpixmap(self._blur_work_img.convert('RGB'))
        canvas = self._canvas
        final_w = max(1, int(self._blur_work_img.width * self.zoom_level))
        final_h = max(1, int(self._blur_work_img.height * self.zoom_level))
        cw = canvas.width() or 1
        ch = canvas.height() or 1
        offset_x = (cw - final_w) // 2 + canvas.pan_offset_x
        offset_y = (ch - final_h) // 2 + canvas.pan_offset_y
        canvas.set_pixmap_and_geometry(pixmap, offset_x, offset_y, final_w, final_h)

    def _on_blur_paint_end(self, skip_history: bool = False) -> bool:
        """Relâchement du clic gauche : commit du stroke dans entry['bytes']
        et dans l'historique unique du panneau (save_state avant + après,
        même pattern que perform_crop/perform_straighten/CloneViewerMixin.
        _on_clone_paint_end).

        skip_history : saute les 2 save_state, laissés à l'appelant."""
        if not self._blur_stroke_dirty:
            return False

        state = self.callbacks.get('state') or _state_module.state
        save_state    = self.callbacks.get("save_state")
        render_mosaic = self.callbacks.get("render_mosaic")
        update_btn    = self.callbacks.get("update_button_text")
        canvas_cb     = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]

            if save_state and not skip_history:
                save_state()

            self._blur_refresh_display()

            out_img = self._blur_work_img
            orig_mode = entry.get('_orig_mode', 'RGBA')
            # .bmp exclu : voir clone_tool_qt.py::_on_clone_paint_end (Pillow
            # écrit un canal alpha 32-bit mais ne le redétecte pas à la
            # relecture).
            if orig_mode not in ('RGBA', 'LA', 'P') and entry.get('extension', '').lower() not in (
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
            if save_state and not skip_history:
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

            self._toolbar.refresh_undo_redo_state()

            if not skip_history:
                # Payload en pixels absolus — points peints (throttle
                # d'affichage, pas chaque pixel) + taille/puissance du
                # pinceau. Chaque stroke = sa propre étape.
                points = getattr(self, '_macro_blur_points', [])
                if points:
                    self._macro_record_step(
                        "blur",
                        {
                            "brush_diam_px": self._canvas._blur_brush_radius,
                            "strength": self._canvas._blur_strength,
                            "points_px": [[p[0], p[1]] for p in points],
                        },
                        "macro.step_blur",
                        {"stroke_points": len(points)},
                    )

            self._blur_work_img = None
            self._blur_stroke_dirty = False

            # Reprend l'affichage normal depuis les bytes committés (invalide
            # le pixmap "aperçu direct" utilisé pendant le stroke).
            self.display_image()
            return True

        except Exception:
            if skip_history:
                return False
            dlg = MsgDialog(self._center_parent, "messages.errors.blur_failed.title",
                            "messages.errors.blur_failed.message")
            dlg.show_nonmodal()
            return False

    def perform_blur_step(self, params: dict) -> bool:
        """Rejoue un stroke complet depuis un payload de macro (points_px en
        pixels absolus) : pose l'état du canvas puis appelle
        _on_blur_paint_stroke() point par point, sans passer par un
        événement souris réel — même principe que
        clone_tool_qt.py::CloneViewerMixin.perform_clone_step."""
        canvas = self._canvas
        points_px = params.get("points_px")
        if not points_px:
            return False

        canvas.set_blur_brush_radius(params.get("brush_diam_px", 40))
        canvas.set_blur_strength(params.get("strength", 8))
        self._blur_stroke_dirty = False

        for px, py in points_px:
            self._on_blur_paint_stroke(px, py)
            if not self._blur_stroke_dirty:
                return False

        canvas._blur_painting = False
        return self._on_blur_paint_end(skip_history=True)
