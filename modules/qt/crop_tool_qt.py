"""
modules/qt/crop_tool_qt.py — Outil de recadrage (rubber-band) de la barre
d'outils flottante de la visionneuse principale (image_viewer_qt.py).

Fusion progressive des visionneuses : ce module contient toute la logique propre à l'outil "crop" — état/
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

Icône bi-mode (normal/masque de découpe) — même mécanisme que straighten
manuel/auto (state.crop_mode, bascule par clic droit sur l'icône, voir
viewer_toolbar_qt.py::_toggle_crop_mode) : le mode "masque de découpe" ne
change RIEN au tracé/à la validation du rectangle lui-même (perform_crop()
reste strictement identique, qu'il s'applique à un rectangle tracé à la main
ou rappelé via le panneau _CropMaskPanel), il ajoute uniquement la capacité
de mémoriser le rectangle courant et de le rappeler sur les pages suivantes,
une par une — traitement strictement page par page, pas de lot sur une
sélection multiple.
"""

from PySide6.QtWidgets import QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout
from PySide6.QtCore import Qt, QPoint

from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font


# ─────────────────────────────────────────────────────────────────────────────
# Panneau flottant du mode "masque de découpe"
# ─────────────────────────────────────────────────────────────────────────────

class _CropMaskPanel(QWidget):
    """Panneau flottant avec les 3 boutons du mode masque de découpe
    (Enregistrer / Rappeler / Oublier), affiché sous la barre d'outils
    uniquement quand l'outil "crop" est actif ET que state.crop_mode == 1
    (bascule par clic droit sur l'icône crop, voir viewer_toolbar_qt.py::
    _toggle_crop_mode). Même principe de panneau que _StraightenAnglePanel/
    _RotationOptionsPanel — jamais inséré dans le layout de ImageViewer.

    "Enregistrer" grisé tant qu'aucun rectangle valide n'existe sur la page
    courante (rien à mémoriser). "Rappeler"/"Oublier" grisés tant qu'aucun
    masque n'est en mémoire — "Rappeler" est en plus coloré en vert dès qu'un
    masque existe (indicateur visuel de l'état mémorisé, décision explicite :
    pas de label/pastille séparés, c'est le bouton lui-même qui porte
    l'info, même mécanique que le bouton "Valider" flottant vert/gris)."""

    def __init__(self, viewer: "ImageViewer"):
        super().__init__(viewer._canvas)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._viewer = viewer

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        self._label = QLabel()
        layout.addWidget(self._label, alignment=Qt.AlignCenter)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._save_btn = QPushButton()
        self._save_btn.clicked.connect(self._on_save_clicked)
        btn_row.addWidget(self._save_btn)

        self._recall_btn = QPushButton()
        self._recall_btn.clicked.connect(self._on_recall_clicked)
        btn_row.addWidget(self._recall_btn)

        self._forget_btn = QPushButton()
        self._forget_btn.clicked.connect(self._on_forget_clicked)
        btn_row.addWidget(self._forget_btn)

        layout.addLayout(btn_row)

        self.hide()

    def _apply_theme(self):
        from modules.qt.image_viewer_qt import _floating_options_panel_style
        theme = get_current_theme()
        self.setStyleSheet(_floating_options_panel_style(theme, "_CropMaskPanel"))
        self._label.setStyleSheet(f"color: {theme['text']}; background: transparent;")
        self._normal_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 10px; }}"
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
            f"QPushButton:disabled {{ background: {theme['toolbar_bg']}; color: {theme['separator']}; "
            f"border: 1px solid {theme['separator']}; }}"
        )
        self._green_style = (
            f"QPushButton {{ background: #2e7d32; color: #ffffff; "
            f"border: 1px solid #1b5e20; padding: 4px 10px; }}"
            f"QPushButton:hover {{ background: #388e3c; }}"
        )
        # Même rouge que le bouton "Annuler" flottant partagé (_cancel_btn_
        # style_red, image_viewer_qt.py) — cohérence de palette entre les
        # deux mécanismes de la barre d'outils.
        self._red_style = (
            f"QPushButton {{ background: #c62828; color: #ffffff; "
            f"border: 1px solid #8e0000; padding: 4px 10px; }}"
            f"QPushButton:hover {{ background: #d32f2f; }}"
        )
        self._update_buttons_state()

    def retranslate(self):
        font = _get_current_font(11)
        self._label.setText(_("dialogs.crop_mask.label"))
        self._label.setFont(font)
        self._save_btn.setText(_("buttons.save_crop_mask"))
        self._save_btn.setFont(font)
        self._recall_btn.setText(_("buttons.recall_crop_mask"))
        self._recall_btn.setFont(font)
        self._forget_btn.setText(_("buttons.forget_crop_mask"))
        self._forget_btn.setFont(font)

    # ── État des 3 boutons ───────────────────────────────────────────────────

    def _update_buttons_state(self):
        """Recalcule le grisage des 3 boutons + la couleur de chacun selon
        son état actif — jamais la visibilité du panneau lui-même (pilotée
        uniquement par set_visible_for_tool, même règle que les boutons
        Valider/Annuler). "Enregistrer" vert quand il y a un rectangle à
        mémoriser, "Rappeler" vert quand un masque existe, "Oublier" rouge
        quand un masque existe (même rouge que le bouton "Annuler" flottant,
        pour signaler une action destructive sur la mémoire)."""
        canvas = self._viewer._canvas
        has_crop = canvas.has_crop
        has_mask = self._viewer._get_crop_mask_px() is not None
        self._save_btn.setEnabled(has_crop)
        self._recall_btn.setEnabled(has_mask)
        self._forget_btn.setEnabled(has_mask)
        self._save_btn.setStyleSheet(self._green_style if has_crop else self._normal_style)
        self._recall_btn.setStyleSheet(self._green_style if has_mask else self._normal_style)
        self._forget_btn.setStyleSheet(self._red_style if has_mask else self._normal_style)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _on_save_clicked(self):
        self._viewer.save_crop_mask()
        self._update_buttons_state()

    def _on_recall_clicked(self):
        self._viewer.recall_crop_mask()
        self._update_buttons_state()

    def _on_forget_clicked(self):
        self._viewer.forget_crop_mask()
        self._update_buttons_state()

    # ── Visibilité ────────────────────────────────────────────────────────────

    def set_visible_for_tool(self, tool_id: str | None):
        canvas = self._viewer._canvas
        if tool_id == "crop" and self._viewer._toolbar._crop_mode() == 1:
            self._update_buttons_state()
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
        # Même blindage anti-fuite de clic que tous les autres panneaux
        # flottants de cette barre (skill viewers) — un clic sur une zone
        # vide fuirait sinon vers _ViewerCanvas en dessous.
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def enterEvent(self, event):
        self._viewer._toolbar.pause_hide()
        # Ce panneau n'a pas de curseur custom posé par crop_update_cursor
        # (contrairement à _StraightenAnglePanel/_LevelsOptionsPanel) — pas
        # de setCursor(Qt.ArrowCursor) nécessaire ici, mais laissé en
        # commentaire pour cohérence si un curseur custom devait être ajouté
        # un jour au mode masque.

    def leaveEvent(self, event):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._check_really_left)

    def _check_really_left(self):
        from PySide6.QtGui import QCursor
        really_left = not self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        if really_left:
            self._viewer._toolbar.resume_hide()


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
        # Bouton "Annuler" jumeau rafraîchi juste à côté.
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
        restaurer si l'utilisateur revient sur cette page."""
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

    # ── Mode "masque de découpe" ─────────────────────────────────────────────
    # Page par page uniquement à ce stade — le traitement en lot sur une
    # sélection multiple n'est pas couvert ici.

    def _get_crop_mask_px(self):
        """Masque de découpe mémorisé — coordonnées en PIXELS ABSOLUS de
        l'image source (pas relatives 0-1, contrairement à crop_rel_x1...x2) :
        (x1, y1, largeur, hauteur). Choix délibéré : des coordonnées
        relatives à la page d'enregistrement n'auraient plus de sens
        rappelées sur une page de dimensions/ratio différents (ex. une page déjà recadrée puis un
        masque rappelé sur une page pas encore recadrée) — la même TAILLE en
        pixels, ancrée au même coin haut-gauche, reste seule cohérente d'une
        page à l'autre. Stocké sur `state` (attribut dynamique, jamais
        déclaré dans state.py — comme state.crop_mode), PAS sur
        _ViewerCanvas : une nouvelle instance de _ViewerCanvas est créée à
        chaque ouverture de la visionneuse, donc un stockage côté canvas ne
        survivrait pas à sa fermeture/réouverture, contrairement à `state`
        qui vit pour toute la durée du process — le masque doit survivre à
        la fermeture de la visionneuse, mais jamais à la fermeture de
        l'application (décision explicite, aucune écriture en config/disque)."""
        from modules.qt import state as _state_module
        state = self.callbacks.get('state') or _state_module.state
        return getattr(state, "crop_mask_px", None)

    def save_crop_mask(self):
        """Mémorise le rectangle actuellement tracé/affiché sur la page
        courante comme masque de découpe, converti en pixels absolus de
        l'image source (même conversion widget → image que perform_crop()).
        No-op silencieux si aucun rectangle valide n'existe (le bouton
        "Enregistrer" est de toute façon grisé dans ce cas, voir
        _CropMaskPanel)."""
        from modules.qt import state as _state_module
        state = self.callbacks.get('state') or _state_module.state
        c = self._canvas
        if c.crop_rel_x1 is None or c._crop_start is None or c._crop_end is None:
            return
        zoom = self.zoom_level or 1.0
        x1 = (c._crop_start.x() - c.display_offset_x) / zoom
        y1 = (c._crop_start.y() - c.display_offset_y) / zoom
        x2 = (c._crop_end.x()   - c.display_offset_x) / zoom
        y2 = (c._crop_end.y()   - c.display_offset_y) / zoom
        px_x1, px_y1 = min(x1, x2), min(y1, y2)
        width, height = abs(x2 - x1), abs(y2 - y1)
        state.crop_mask_px = (px_x1, px_y1, width, height)

    def recall_crop_mask(self):
        """Rappelle le masque mémorisé sur la page courante — même TAILLE en
        pixels image, ancrée au même coin haut-gauche, clampée si elle
        dépasse les dimensions de l'image courante (page plus petite que la
        page d'enregistrement). Écrase silencieusement un rectangle en cours
        de tracé/édition (pas de confirmation, décision explicite). No-op si
        aucun masque n'est en mémoire (bouton grisé dans ce cas), ou si
        l'image de la page courante n'est pas chargeable."""
        from modules.qt import state as _state_module
        from modules.qt.entries import ensure_image_loaded

        mask = self._get_crop_mask_px()
        if mask is None:
            return
        state = self.callbacks.get('state') or _state_module.state
        if not (0 <= self.current_idx < len(state.images_data)):
            return
        entry = state.images_data[self.current_idx]
        img = ensure_image_loaded(entry)
        if img is None:
            return
        img_w, img_h = img.size

        px_x1, px_y1, width, height = mask
        px_x1 = max(0, min(px_x1, img_w))
        px_y1 = max(0, min(px_y1, img_h))
        px_x2 = max(0, min(px_x1 + width, img_w))
        px_y2 = max(0, min(px_y1 + height, img_h))
        if px_x2 <= px_x1 or px_y2 <= px_y1:
            return

        c = self._canvas
        zoom = self.zoom_level or 1.0
        c.crop_rel_x1 = px_x1 / img_w
        c.crop_rel_y1 = px_y1 / img_h
        c.crop_rel_x2 = px_x2 / img_w
        c.crop_rel_y2 = px_y2 / img_h
        c._crop_start = QPoint(
            int(c.display_offset_x + px_x1 * zoom),
            int(c.display_offset_y + px_y1 * zoom))
        c._crop_end = QPoint(
            int(c.display_offset_x + px_x2 * zoom),
            int(c.display_offset_y + px_y2 * zoom))
        c.update()
        c._update_validate_btn_state()
        c._update_cancel_btn_state()

    def forget_crop_mask(self):
        """Vide le masque mémorisé — n'affecte PAS le rectangle actuellement
        affiché/tracé sur la page courante, qui reste éditable/validable
        normalement (décision explicite : "Oublier" agit sur la mémoire, pas
        sur l'écran)."""
        from modules.qt import state as _state_module
        state = self.callbacks.get('state') or _state_module.state
        state.crop_mask_px = None

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

    def perform_crop(self, skip_history: bool = False, override_px=None) -> bool:
        """skip_history : saute les 2 save_state, laissés à l'appelant.
        override_px=(x1,y1,x2,y2) : utilise ces pixels directement au lieu de
        c._crop_start/c._crop_end (lecture headless d'une macro, aucun tracé
        souris). Retourne True si le crop a réellement eu lieu."""
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
                if override_px is None:
                    dlg = MsgDialog(self._center_parent, "messages.errors.crop_failed.title",
                                    "messages.errors.crop_failed.title")
                    dlg.show_nonmodal()
                return False

            img_w, img_h = original_img.size

            if override_px is not None:
                orig_x1, orig_y1, orig_x2, orig_y2 = override_px
            else:
                c = self._canvas
                crop_x1 = c._crop_start.x() - c.display_offset_x
                crop_y1 = c._crop_start.y() - c.display_offset_y
                crop_x2 = c._crop_end.x()   - c.display_offset_x
                crop_y2 = c._crop_end.y()   - c.display_offset_y

                crop_x1 = max(0, min(crop_x1, c.display_width))
                crop_y1 = max(0, min(crop_y1, c.display_height))
                crop_x2 = max(0, min(crop_x2, c.display_width))
                crop_y2 = max(0, min(crop_y2, c.display_height))

                zoom_ratio = self.zoom_level

                orig_x1 = int(crop_x1 / zoom_ratio)
                orig_y1 = int(crop_y1 / zoom_ratio)
                orig_x2 = int(crop_x2 / zoom_ratio)
                orig_y2 = int(crop_y2 / zoom_ratio)

            if orig_x2 <= orig_x1 or orig_y2 <= orig_y1:
                if override_px is None:
                    dlg = MsgDialog(self._center_parent, "messages.errors.crop_invalid.title",
                                    "messages.errors.crop_invalid.message")
                    dlg.show_nonmodal()
                return False

            # Rectangle hors des dimensions de la page cible = échec pour
            # cette page, jamais de clamp silencieux.
            if override_px is not None and (orig_x2 > img_w or orig_y2 > img_h):
                return False

            if save_state and not skip_history:
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
            if save_state and not skip_history:
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

            if override_px is None:
                # Mode masque : valider enregistre aussi le masque.
                if self._toolbar._crop_mode() == 1:
                    self.save_crop_mask()
                self._canvas.clear_crop()
                self._crop_by_page.pop(self.current_idx, None)
            self.display_image()
            self._toolbar.refresh_undo_redo_state()
            if override_px is None:
                # Pixels absolus, PAS crop_rel_* (relatif, utilisé hors macro).
                self._macro_record_step(
                    "crop", {"px": [orig_x1, orig_y1, orig_x2, orig_y2]},
                    "macro.step_crop", {},
                )
            return True

        except Exception:
            if override_px is not None:
                return False
            dlg = MsgDialog(self._center_parent, "messages.errors.crop_failed.title",
                            "messages.errors.crop_failed.title")
            dlg.show_nonmodal()
