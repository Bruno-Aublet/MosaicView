"""
modules/qt/clone_tool_qt.py — Outil de clonage de zone (tampon clone) de la
barre d'outils flottante de la visionneuse principale (image_viewer_qt.py).

Fusion progressive des visionneuses (idees.txt #3, 3e outil migré) : ce module
contient toute la logique propre à l'outil "clone" — état/interactions du
canvas (mixin CloneCanvasMixin, hérité par _ViewerCanvas), commit du stroke
dans l'historique du panneau (mixin CloneViewerMixin, hérité par ImageViewer),
le panneau flottant de réglages (_CloneOptionsPanel), et les deux fonctions de
rendu pures (damier, curseur en croix). image_viewer_qt.py ne fait qu'hériter
de ces deux mixins et brancher l'icône de la barre d'outils — voir CLAUDE.md
règle "ne jamais migrer le code d'un outil dans image_viewer_qt.py".

Contrairement au crop/straighten (une opération, validée une fois via un
bouton "Valider"), le clonage peint en continu : chaque coup de tampon modifie
déjà l'image et devient sa propre entrée d'historique au relâchement (accepté
explicitement par l'utilisateur que l'historique soit verbeux, "il correspond
à la réalité de ce que fait l'utilisateur"). Pas de bouton "Valider" pour cet
outil, pas de persistance de travail non validé par page (chaque stroke est
déjà commité), pas d'historique local séparé (undo/redo unifié, celui du
panneau).
"""

import io

from PIL import Image

from PySide6.QtWidgets import (
    QWidget, QLabel, QHBoxLayout, QFrame, QPushButton, QButtonGroup,
    QSlider, QSpinBox,
)
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QPixmap, QCursor

from modules.qt import state as _state_module
from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.entries import save_image_to_bytes
from modules.qt.dialogs_qt import MsgDialog


# ─────────────────────────────────────────────────────────────────────────────
# Rendu pur (damier d'aperçu, curseur en croix)
# ─────────────────────────────────────────────────────────────────────────────

def make_clone_checker(w: int, h: int, tile: int = 12) -> Image.Image:
    """Génère un fond damier RGB de taille (w, h) sans boucle Python — repris
    tel quel de l'ancienne clone_zone_viewer_qt.py::CloneZoneViewerDialog._make_checker
    (fenêtre dédiée supprimée après migration de son outil dans cette barre).
    Utilisé pour l'aperçu direct pendant un stroke de clonage (voir
    CloneViewerMixin._clone_refresh_display), distinct de _compose_on_checkerboard
    (affichage normal, entries.py::_make_checkerboard_pil) — tuile plus fine
    (12px) et affichage RGB sans le canal alpha composé de la même façon."""
    import array as _array
    light_val, dark_val = 204, 128
    row_light = bytes(_array.array('B', [
        light_val if (x // tile) % 2 == 0 else dark_val for x in range(w)
    ]))
    row_dark = bytes(_array.array('B', [
        dark_val if (x // tile) % 2 == 0 else light_val for x in range(w)
    ]))
    rows = []
    for y in range(h):
        rows.append(row_light if (y // tile) % 2 == 0 else row_dark)
    checker_l = Image.frombytes('L', (w, h), b''.join(rows))
    return checker_l.convert('RGB')


def make_clone_crosshair_cursor(r_screen: int) -> QCursor:
    """Curseur en forme de cible (cercle + croix), rayon r_screen px — repris
    tel quel de l'ancienne clone_zone_viewer_qt.py::_make_crosshair_cursor."""
    from PySide6.QtGui import QPainter, QPen, QColor
    r = max(1, r_screen)
    margin = 8
    r_inner = max(1, r // 4)
    size = (r + margin) * 2 + 1
    cx = cy = size // 2

    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    pen_white = QPen(QColor(255, 255, 255, 200), 2.5)
    pen_black = QPen(QColor(0, 0, 0, 230), 1.5)

    painter.setPen(pen_white)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QPoint(cx, cy), r + 1, r + 1)
    painter.setPen(pen_black)
    painter.drawEllipse(QPoint(cx, cy), r, r)

    if r_inner > 1:
        painter.setPen(pen_white)
        painter.drawEllipse(QPoint(cx, cy), r_inner + 1, r_inner + 1)
        painter.setPen(pen_black)
        painter.drawEllipse(QPoint(cx, cy), r_inner, r_inner)

    gap = r_inner + 2
    ext = r + margin - 2

    def _hline(pen, y_off=0):
        painter.setPen(pen)
        painter.drawLine(cx - ext, cy + y_off, cx - gap, cy + y_off)
        painter.drawLine(cx + gap, cy + y_off, cx + ext, cy + y_off)

    def _vline(pen, x_off=0):
        painter.setPen(pen)
        painter.drawLine(cx + x_off, cy - ext, cx + x_off, cy - gap)
        painter.drawLine(cx + x_off, cy + gap, cx + x_off, cy + ext)

    _hline(pen_white, y_off=-1)
    _hline(pen_black)
    _vline(pen_white, x_off=-1)
    _vline(pen_black)

    painter.end()

    return QCursor(pm, cx, cy)


def floating_options_panel_style(theme, class_name: str) -> str:
    """Style de fond commun aux panneaux flottants d'options de la barre
    d'outils (angle de redressage, réglages du tampon de clonage) : sans
    bordure marquée, un panneau flottant transparent se fond visuellement
    dans une image de fond claire ou blanche (signalé par l'utilisateur en
    conditions réelles). Fond franc dédié (pas toolbar_bg, trop proche d'un
    fond de page clair/blanc typique d'une BD) + bordure nette dans la
    couleur de texte du thème."""
    panel_bg = "#3a3a3a" if _state_module.state.dark_mode else "#f0f0f0"
    return (
        f"{class_name} {{ background: {panel_bg}; border: 1px solid {theme['text']}; "
        f"border-radius: 6px; }}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Panneau flottant de réglages (mode source, taille du tampon)
# ─────────────────────────────────────────────────────────────────────────────

class _CloneOptionsPanel(QWidget):
    """Panneau flottant avec les réglages du tampon de clonage (mode source
    fixe/relatif, taille du tampon), affiché sous la barre d'outils uniquement
    quand l'outil "clone" est actif — même principe que _StraightenAnglePanel
    de image_viewer_qt.py (jamais inséré dans le layout de ImageViewer,
    indépendant du timer d'auto-masquage de la barre pour ne pas interrompre
    un réglage en cours).

    Reprend les réglages déjà en place dans l'ancienne clone_zone_viewer_qt.py
    (voir skill clone-zone) — pas de source de vérité propre : chaque
    changement pousse directement l'état sur le canvas
    (set_clone_mode/set_clone_brush_radius, voir CloneCanvasMixin).
    """

    _BRUSH_SIZE_MIN = 1
    _BRUSH_SIZE_MAX = 400
    _BRUSH_SIZE_DEFAULT = 20

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

        self._lbl_mode = QLabel()
        layout.addWidget(self._lbl_mode)

        # Boutons texte checkable (bouton entier surligné à l'état actif),
        # plutôt que QRadioButton/QCheckBox : le rendu natif de ::indicator
        # (cercle, puis carré) ne se dessinait pas correctement sur ce panneau
        # à fond stylé (WA_StyledBackground) et les styles custom successifs
        # sont restés peu lisibles (signalé par l'utilisateur en conditions
        # réelles, plusieurs itérations). Un QPushButton checkable évite tout
        # indicateur séparé à styler : c'est le fond du bouton entier qui
        # marque l'état, même mécanisme déjà éprouvé que _ToolButton de la
        # barre d'outils (set_active/bordure). setAutoExclusive(True) +
        # QButtonGroup reproduisent le comportement radio (un seul actif à la
        # fois).
        self._radio_fixed = QPushButton()
        self._radio_fixed.setCheckable(True)
        self._radio_fixed.setAutoExclusive(True)
        self._radio_fixed.setChecked(True)
        self._radio_fixed.toggled.connect(self._on_mode_changed)
        layout.addWidget(self._radio_fixed)

        self._radio_relative = QPushButton()
        self._radio_relative.setCheckable(True)
        self._radio_relative.setAutoExclusive(True)
        layout.addWidget(self._radio_relative)

        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._radio_fixed)
        self._mode_group.addButton(self._radio_relative)

        self._sep = QFrame()
        self._sep.setFrameShape(QFrame.Shape.VLine)
        layout.addWidget(self._sep)

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

        self.hide()

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(floating_options_panel_style(theme, "_CloneOptionsPanel"))
        self._lbl_mode.setStyleSheet(f"color: {theme['text']}; background: transparent;")
        self._lbl_brush.setStyleSheet(f"color: {theme['text']}; background: transparent;")
        self._sep.setStyleSheet(f"color: {theme['separator']};")
        # Pas d'indicateur séparé à styler (voir __init__) : c'est le fond du
        # bouton entier qui marque l'état actif — non coché = discret
        # (bordure fine, fond transparent), coché = fond plein en couleur
        # d'accent + texte blanc, sans ambiguïté possible.
        accent = "#4a90d9"
        radio_style = (
            f"QPushButton {{ color: {theme['text']}; background: transparent; "
            f"border: 1px solid {theme['separator']}; border-radius: 4px; padding: 3px 10px; }} "
            f"QPushButton:checked {{ background: {accent}; color: #ffffff; "
            f"border: 1px solid {accent}; }}"
        )
        self._radio_fixed.setStyleSheet(radio_style)
        self._radio_relative.setStyleSheet(radio_style)
        self._brush_spin.setStyleSheet(
            f"QSpinBox {{ background: {theme['bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 2px 4px; }} "
            f"QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}"
        )
        # Style explicite de la piste/du curseur : même raison que les radios
        # ci-dessus, le rendu natif de la piste disparaît sur ce fond stylé.
        # sub-page/add-page DOIVENT être stylés explicitement dès que
        # groove/handle le sont — sinon Qt applique un rendu par défaut
        # incohérent avec le reste (barre remplie de façon sombre et uniforme
        # sur toute sa longueur, signalé par l'utilisateur en conditions
        # réelles), au lieu de deux segments piste/parcouru distincts. Fond du
        # widget lui-même explicitement transparent : un QSlider nu peut
        # afficher un pavé de fond sombre hérité du style natif sinon (signalé
        # par l'utilisateur en mode clair).
        self._brush_slider.setStyleSheet(
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

    def retranslate(self):
        font = _get_current_font(11)
        self._lbl_mode.setText(_("dialogs.clone_zone_viewer.mode_label"))
        self._lbl_mode.setFont(font)
        self._radio_fixed.setText(_("dialogs.clone_zone_viewer.mode_fixed"))
        self._radio_fixed.setFont(font)
        self._radio_relative.setText(_("dialogs.clone_zone_viewer.mode_relative"))
        self._radio_relative.setFont(font)
        self._lbl_brush.setText(_("dialogs.clone_zone_viewer.brush_size_label"))
        self._lbl_brush.setFont(font)
        self._brush_spin.setFont(font)

    # ── Visibilité ────────────────────────────────────────────────────────────

    def set_visible_for_tool(self, tool_id: str | None):
        if tool_id == "clone":
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
        # Piège corrigé (2026-08-15, découvert sur le panneau de
        # transparency_tool_qt.py) : sans ce blindage, un clic sur une zone
        # vide du panneau "fuit" vers _ViewerCanvas en dessous — même piège
        # déjà documenté pour _ToolButton/_ActionButton/_ViewerToolbar (skill
        # viewers), appliqué par cohérence à tous les panneaux flottants.
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def enterEvent(self, event):
        # Suspend le timer d'auto-masquage de la barre (dont ce panneau suit
        # désormais la visibilité, idees.txt #3 décision 2026-08-14) tant que
        # la souris reste sur ce panneau — pas seulement redémarré à chaque
        # mouvement, complètement arrêté (voir _ViewerToolbar.pause_hide).
        self._viewer._toolbar.pause_hide()
        # Le curseur en croix (clone_update_cursor) est celui du CANVAS, pas
        # celui de ce panneau — sans ce reset, il restait affiché par-dessus
        # les contrôles du panneau (idees.txt #4), voir même piège corrigé sur
        # _TransparencyOptionsPanel/_LevelsOptionsPanel.
        self.setCursor(Qt.ArrowCursor)

    def leaveEvent(self, event):
        # Revérification différée à 0ms : Qt peut envoyer un Leave en
        # transitant entre deux widgets enfants même quand la souris reste
        # visuellement sur le panneau (même piège que _LevelsOptionsPanel).
        from PySide6.QtCore import QTimer as _QTimer
        _QTimer.singleShot(0, self._check_really_left)

    def _check_really_left(self):
        really_left = not self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        if really_left:
            self._viewer._toolbar.resume_hide()
            # setCursor(ArrowCursor) posé dans enterEvent ne s'applique qu'à
            # ce panneau et ses enfants — aucun reset à faire ici pour le
            # canvas : Qt réaffiche de lui-même le curseur déjà posé dessus
            # (le curseur en croix, tant que l'outil "clone" reste actif) dès
            # que la souris repasse physiquement au-dessus.
            self.unsetCursor()

    # ── Réglages ─────────────────────────────────────────────────────────────

    def _on_mode_changed(self):
        mode = 'fixed' if self._radio_fixed.isChecked() else 'relative'
        self._viewer._canvas.set_clone_mode(mode)

    def _on_brush_slider_changed(self, value: int):
        if self._brush_spin.value() != value:
            self._brush_spin.blockSignals(True)
            self._brush_spin.setValue(value)
            self._brush_spin.blockSignals(False)
        self._viewer._canvas.set_clone_brush_radius(value)

    def _on_brush_spin_changed(self, value: int):
        if self._brush_slider.value() != value:
            self._brush_slider.blockSignals(True)
            self._brush_slider.setValue(value)
            self._brush_slider.blockSignals(False)
        self._viewer._canvas.set_clone_brush_radius(value)


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état et interactions souris de l'outil (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class CloneCanvasMixin:
    """Hérité par _ViewerCanvas (image_viewer_qt.py) en plus de QLabel : ajoute
    l'état et les méthodes de l'outil "clone" au canvas de la visionneuse,
    sans que leur code vive dans image_viewer_qt.py. Suppose que l'hôte a déjà
    self._viewer (ImageViewer) et les attributs habituels de _ViewerCanvas
    (display_offset_x/y, pan_offset_x/y).

    Pas de coordonnées widget dérivées à resynchroniser comme crop/straighten :
    le marqueur de source est redessiné directement depuis les coordonnées
    image stables à chaque paintEvent (voir _sync_clone_marker_from_image),
    aucun état "en cours non validé" à conserver entre deux affichages puisque
    chaque coup de tampon est déjà appliqué à l'image de travail.
    """

    def _init_clone_state(self):
        self._clone_source_img: tuple | None = None   # (ix, iy) point Ctrl+cliqué
        self._clone_marker_widget: QPoint | None = None
        self._clone_live_marker_widget: QPoint | None = None  # position pendant un stroke (source effective)
        self._clone_painting = False
        self._clone_paint_last: QPoint | None = None
        self._clone_stroke_start_dest: tuple | None = None
        self._clone_stroke_start_src: tuple | None = None
        self._clone_stroke_last_dest: tuple | None = None
        self._clone_mode = 'fixed'          # 'fixed' ou 'relative'
        self._clone_brush_radius = 20
        self._clone_crosshair_cursor = None

    def clear_clone_source(self):
        self._clone_source_img = None
        self._clone_marker_widget = None
        self.update()

    def _get_effective_clone_source(self, dest_x: float, dest_y: float) -> tuple:
        """Calcule la source effective : décalage constant (source initiale -
        dest initiale) appliqué à la dest courante — même calcul que l'ancien
        clone_zone_viewer_qt.py::_get_effective_source. Les deux modes
        utilisent ce même calcul pendant un stroke ; la différence entre
        strokes se joue dans CloneViewerMixin._on_clone_paint_end (mode fixe :
        repart du même point ; mode relatif : avance)."""
        if self._clone_stroke_start_dest is None:
            return self._clone_source_img
        dx = self._clone_stroke_start_src[0] - self._clone_stroke_start_dest[0]
        dy = self._clone_stroke_start_src[1] - self._clone_stroke_start_dest[1]
        return (dest_x + dx, dest_y + dy)

    def set_clone_mode(self, mode: str):
        self._clone_mode = mode

    def set_clone_brush_radius(self, r: int):
        self._clone_brush_radius = r
        self._rebuild_clone_cursor()

    def _rebuild_clone_cursor(self):
        zoom = self._viewer.zoom_level or 1.0
        r_screen = max(1, int(self._clone_brush_radius * zoom / 2))
        self._clone_crosshair_cursor = make_clone_crosshair_cursor(r_screen)

    def _clone_widget_to_image(self, pt: QPoint) -> tuple:
        zoom = self._viewer.zoom_level or 1.0
        ix = (pt.x() - self.display_offset_x) / zoom
        iy = (pt.y() - self.display_offset_y) / zoom
        return ix, iy

    def _clone_image_to_widget(self, ix: float, iy: float) -> QPoint:
        zoom = self._viewer.zoom_level or 1.0
        return QPoint(int(self.display_offset_x + ix * zoom),
                       int(self.display_offset_y + iy * zoom))

    def _sync_clone_marker_from_image(self):
        """Recalcule le marqueur (widget) depuis la source (coordonnées image
        stables), après un pan, un zoom, ou un redimensionnement — même
        principe que _sync_line_from_image pour le redressage."""
        if self._clone_source_img is not None:
            self._clone_marker_widget = self._clone_image_to_widget(*self._clone_source_img)
        else:
            self._clone_marker_widget = None

    @staticmethod
    def _draw_clone_marker(painter, pt, r):
        """Dessine une cible (cercle + croix) sur la zone source, rayon r en pixels écran."""
        from PySide6.QtGui import QPen, QColor
        from PySide6.QtCore import QRectF
        painter.setPen(QPen(QColor(255, 255, 255, 180), 2.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(pt.x() - r - 1, pt.y() - r - 1, (r + 1) * 2, (r + 1) * 2))
        painter.setPen(QPen(QColor(255, 80, 80), 2))
        painter.drawEllipse(QRectF(pt.x() - r, pt.y() - r, r * 2, r * 2))
        c = 6
        painter.drawLine(QPoint(pt.x() - r - c, pt.y()), QPoint(pt.x() + r + c, pt.y()))
        painter.drawLine(QPoint(pt.x(), pt.y() - r - c), QPoint(pt.x(), pt.y() + r + c))

    # ── Rendu (appelé depuis _ViewerCanvas.paintEvent) ───────────────────────

    def paint_clone_marker(self, painter):
        """À appeler en fin de paintEvent, après l'image et les autres
        overlays. Position live pendant un stroke (suit la source effective,
        qui bouge en mode "relative"), sinon dernière position Ctrl+cliquée.
        Toujours resynchronisé depuis les coordonnées image stables
        (pan/zoom/resize), même principe que le trait de redressage. Pas de
        variante grise "conservée" : la source n'est pas un travail en
        attente de validation."""
        if not self._clone_painting:
            self._sync_clone_marker_from_image()
        clone_marker = self._clone_live_marker_widget if self._clone_painting else self._clone_marker_widget
        if clone_marker is not None:
            r_screen = max(1, int(self._clone_brush_radius * (self._viewer.zoom_level or 1.0) / 2))
            self._draw_clone_marker(painter, clone_marker, r_screen)

    # ── Événements souris (appelés depuis _ViewerCanvas.mousePress/Move/ReleaseEvent) ──

    def clone_mouse_press(self, event) -> bool:
        """Retourne True si l'événement a été géré ici (l'appelant doit alors
        arrêter son propre traitement, comme les branches "straighten"/"crop"
        existantes)."""
        pos = event.position().toPoint()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Ctrl+clic gauche : définit la zone source (pas le clic droit,
            # qui reste toujours réservé au pan quel que soit l'outil actif).
            ix, iy = self._clone_widget_to_image(pos)
            self._clone_source_img = (ix, iy)
            self._clone_marker_widget = pos
            self.update()
        elif self._clone_source_img is not None:
            self._clone_painting = True
            self._clone_paint_last = pos
            ix, iy = self._clone_widget_to_image(pos)
            src_x, src_y = self._get_effective_clone_source(ix, iy)
            self._clone_live_marker_widget = self._clone_image_to_widget(src_x, src_y)
            self._viewer._on_clone_paint_stroke(ix, iy)
        return True

    def clone_update_cursor(self, event):
        """Curseur hors tracé (pas de bouton enfoncé) : croix simple, ou
        curseur en croix (rayon du tampon) si Ctrl est enfoncé."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Reconstruit à chaque survol (pas seulement à la première fois) :
            # le rayon écran dépend du zoom courant, qui peut avoir changé
            # (molette, Ctrl+0/1/+/-) depuis la dernière fois que ce curseur a
            # été construit.
            self._rebuild_clone_cursor()
            self.setCursor(self._clone_crosshair_cursor)
        else:
            self.setCursor(Qt.CrossCursor)

    def clone_mouse_move(self, event) -> bool:
        """Retourne True si géré (bouton gauche enfoncé, outil clone actif)."""
        if not self._clone_painting:
            return False
        pos = event.position().toPoint()
        src_x, src_y = self._get_effective_clone_source(*self._clone_widget_to_image(pos))
        self._clone_live_marker_widget = self._clone_image_to_widget(src_x, src_y)
        self.update()
        # Interpolation si déplacement rapide, même principe que l'ancienne
        # clone_zone_viewer_qt.py::_CloneImageWidget.mouseMoveEvent — sans ça,
        # un déplacement rapide de la souris laisse des trous dans le trait.
        if self._clone_paint_last is not None:
            dx = pos.x() - self._clone_paint_last.x()
            dy = pos.y() - self._clone_paint_last.y()
            dist = (dx * dx + dy * dy) ** 0.5
            zoom = self._viewer.zoom_level or 1.0
            step = max(1, int(zoom * self._clone_brush_radius * 0.5))
            if dist >= step:
                steps = max(1, int(dist / step))
                for i in range(1, steps + 1):
                    t = i / steps
                    ix = self._clone_paint_last.x() + int(dx * t)
                    iy = self._clone_paint_last.y() + int(dy * t)
                    iix2, iiy2 = self._clone_widget_to_image(QPoint(ix, iy))
                    self._viewer._on_clone_paint_stroke(iix2, iiy2)
                self._clone_paint_last = pos
        else:
            iix, iiy = self._clone_widget_to_image(pos)
            self._viewer._on_clone_paint_stroke(iix, iiy)
            self._clone_paint_last = pos
        return True

    def clone_mouse_release(self, event) -> bool:
        """Retourne True si géré (un stroke était en cours)."""
        if not self._clone_painting:
            return False
        self._clone_painting = False
        self._clone_paint_last = None
        self._clone_live_marker_widget = None
        self._viewer._on_clone_paint_end()
        self.update()
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — commit du stroke dans l'historique du panneau (hérité par ImageViewer)
# ─────────────────────────────────────────────────────────────────────────────

class CloneViewerMixin:
    """Hérité par ImageViewer (image_viewer_qt.py) en plus de QDialog : ajoute
    la logique de peinture/commit de l'outil "clone" au viewer, sans que son
    code vive dans image_viewer_qt.py. Suppose que l'hôte a déjà self._canvas
    (_ViewerCanvas, avec CloneCanvasMixin), self.callbacks, self.current_idx,
    self.zoom_level, self.page_mode, self._toolbar.
    """

    def _init_clone_viewer_state(self):
        self._clone_work_img = None          # copie PIL RGBA de travail, existe seulement pendant un stroke
        self._clone_checker_bg = None        # damier RGB précalculé pour l'affichage pendant le stroke
        self._clone_stroke_snapshot = None   # snapshot figé (mode fixe) ou référence directe (mode relatif)
        self._clone_bytes_before_stroke = None
        self._clone_stroke_dirty = False
        from PySide6.QtCore import QElapsedTimer
        self._clone_display_timer = QElapsedTimer()
        self._clone_display_timer.start()

    def _on_clone_paint_stroke(self, ix: float, iy: float):
        """Clic gauche maintenu (ou Ctrl+clic suivi de mouvement) : applique le
        tampon à la position (ix, iy) de destination, en coordonnées image."""
        canvas = self._canvas
        if canvas._clone_source_img is None:
            return

        if not self._clone_stroke_dirty:
            # Première application de ce stroke : charge l'image de travail
            # depuis l'entrée actuelle (bytes committés du stroke précédent ou
            # de l'ouverture de la visionneuse), même principe que l'ancienne
            # clone_zone_viewer_qt.py::_load_work_image.
            entry = (self.callbacks.get('state') or _state_module.state).images_data[self.current_idx]
            if not entry.get('bytes'):
                return
            try:
                img = Image.open(io.BytesIO(entry['bytes']))
                # Mode d'origine (pour la reconversion en sortie, voir plus bas
                # dans _on_clone_paint_end) — posé une seule fois, comme le fait
                # text_viewer_qt.py::show_text_viewer avant l'ouverture. Ne pas
                # utiliser un défaut 'RGBA' arbitraire : une image sans alpha
                # d'origine (ex. JPEG) doit rester aplatie sur blanc à la sortie.
                if '_orig_mode' not in entry:
                    entry['_orig_mode'] = img.mode
                self._clone_work_img = img.convert('RGBA')
            except Exception:
                return
            self._clone_checker_bg = make_clone_checker(
                self._clone_work_img.width, self._clone_work_img.height)
            self._clone_bytes_before_stroke = entry['bytes']
            canvas._clone_stroke_start_dest = (ix, iy)
            canvas._clone_stroke_start_src = canvas._clone_source_img
            if canvas._clone_mode == 'fixed':
                self._clone_stroke_snapshot = self._clone_work_img.copy()
            else:
                self._clone_stroke_snapshot = self._clone_work_img
            self._clone_stroke_dirty = True
            # En mode double page/continu/webtoon, un stroke effectif force le
            # retour en simple page — même règle que le crop/straighten
            # (déclenchée seulement à la fin d'un geste effectif, pas à la
            # simple sélection de l'icône, cohérence avec les 2 outils migrés).
            if self.page_mode != "single":
                self.page_mode = "single"

        src_x, src_y = canvas._get_effective_clone_source(ix, iy)
        self._clone_apply_stamp(ix, iy, src_x, src_y)
        canvas._clone_stroke_last_dest = (ix, iy)

        if self._clone_display_timer.elapsed() >= 33:
            self._clone_refresh_display()
            self._clone_display_timer.restart()

    def _clone_apply_stamp(self, dest_x: float, dest_y: float, src_x: float, src_y: float):
        """Copie un disque de diamètre canvas._clone_brush_radius px depuis le
        snapshot vers l'image de travail — repris tel quel de l'ancienne
        clone_zone_viewer_qt.py::CloneZoneViewerDialog._apply_stamp."""
        from PIL import ImageDraw
        snap = self._clone_stroke_snapshot
        dst = self._clone_work_img
        w, h = dst.size
        r = (self._canvas._clone_brush_radius - 1) / 2

        d_left   = max(0, int(dest_x - r))
        d_top    = max(0, int(dest_y - r))
        d_right  = min(w, int(dest_x + r) + 1)
        d_bottom = min(h, int(dest_y + r) + 1)
        if d_left >= d_right or d_top >= d_bottom:
            return

        # src_x/src_y/dest_x/dest_y peuvent être des float (coordonnées image
        # dérivées d'une division par le zoom, voir _clone_widget_to_image) —
        # contrairement à l'ancienne clone_zone_viewer_qt.py où _widget_to_image
        # castait déjà en int à la source. PIL (crop/paste/Image.new) exige des
        # entiers : caster ici, au bord de la géométrie source, pas plus tôt
        # (dest_x/dest_y non arrondis restent utiles pour le calcul du masque
        # circulaire ci-dessous, qui doit rester précis au pixel près).
        s_left   = int(round(src_x - (dest_x - d_left)))
        s_top    = int(round(src_y - (dest_y - d_top)))
        s_right  = s_left + (d_right - d_left)
        s_bottom = s_top  + (d_bottom - d_top)

        sc_left   = max(0, s_left)
        sc_top    = max(0, s_top)
        sc_right  = min(w, s_right)
        sc_bottom = min(h, s_bottom)
        if sc_left >= sc_right or sc_top >= sc_bottom:
            return

        src_crop = snap.crop((sc_left, sc_top, sc_right, sc_bottom))
        paste_x = d_left + (sc_left - s_left)
        paste_y = d_top  + (sc_top  - s_top)
        pw = sc_right - sc_left
        ph = sc_bottom - sc_top

        if r == 0.0:
            dst.paste(src_crop, (paste_x, paste_y))
            return
        mask = Image.new('L', (pw, ph), 0)
        cx = dest_x - paste_x
        cy = dest_y - paste_y
        draw = ImageDraw.Draw(mask)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
        dst.paste(src_crop, (paste_x, paste_y), mask=mask)

    def _clone_refresh_display(self):
        """Affiche directement l'image de travail pendant le stroke, sans
        repasser par entry['bytes']/ensure_image_loaded/display_image() —
        recharger et réencoder à chaque frame serait beaucoup trop coûteux
        pour un simple aperçu (contrairement au crop/straighten qui n'agissent
        qu'une fois à la validation)."""
        if self._clone_work_img is None:
            return
        from modules.qt.image_viewer_qt import _pil_to_qpixmap
        display = self._clone_checker_bg.copy()
        display.paste(self._clone_work_img.convert('RGB'), mask=self._clone_work_img.split()[3])
        pixmap = _pil_to_qpixmap(display)
        canvas = self._canvas
        final_w = max(1, int(display.width * self.zoom_level))
        final_h = max(1, int(display.height * self.zoom_level))
        cw = canvas.width() or 1
        ch = canvas.height() or 1
        offset_x = (cw - final_w) // 2 + canvas.pan_offset_x
        offset_y = (ch - final_h) // 2 + canvas.pan_offset_y
        canvas.set_pixmap_and_geometry(pixmap, offset_x, offset_y, final_w, final_h)

    def _on_clone_paint_end(self):
        """Relâchement du clic gauche : commit du stroke dans entry['bytes']
        et dans l'historique unique du panneau (save_state avant + après,
        même pattern que perform_crop/perform_straighten — pas d'historique
        local séparé comme dans l'ancienne CloneZoneViewerDialog)."""
        if not self._clone_stroke_dirty:
            return

        state = self.callbacks.get('state') or _state_module.state
        save_state    = self.callbacks.get("save_state")
        render_mosaic = self.callbacks.get("render_mosaic")
        update_btn    = self.callbacks.get("update_button_text")
        canvas_cb     = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]

            if save_state:
                save_state()

            self._clone_refresh_display()

            out_img = self._clone_work_img
            orig_mode = entry.get('_orig_mode', 'RGBA')
            if orig_mode not in ('RGBA', 'LA', 'P') and entry.get('extension', '').lower() not in ('.png', '.webp', '.avif'):
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

            self._toolbar.refresh_undo_redo_state()

        except Exception:
            dlg = MsgDialog(self._center_parent, "messages.errors.clone_failed.title",
                            "messages.errors.clone_failed.title")
            dlg.show_nonmodal()

        # Prépare le prochain stroke : mode fixe repart du même point source,
        # mode relatif avance le point source du déplacement effectué — même
        # principe que l'ancienne clone_zone_viewer_qt.py::_on_paint_end.
        canvas = self._canvas
        if canvas._clone_mode != 'fixed':
            if (canvas._clone_stroke_start_dest is not None and
                    canvas._clone_stroke_last_dest is not None and
                    canvas._clone_stroke_start_src is not None):
                ddx = canvas._clone_stroke_last_dest[0] - canvas._clone_stroke_start_dest[0]
                ddy = canvas._clone_stroke_last_dest[1] - canvas._clone_stroke_start_dest[1]
                canvas._clone_source_img = (
                    canvas._clone_stroke_start_src[0] + ddx,
                    canvas._clone_stroke_start_src[1] + ddy,
                )

        canvas._clone_stroke_start_dest = None
        canvas._clone_stroke_start_src = None
        canvas._clone_stroke_last_dest = None
        self._clone_work_img = None
        self._clone_checker_bg = None
        self._clone_stroke_snapshot = None
        self._clone_bytes_before_stroke = None
        self._clone_stroke_dirty = False

        # Reprend l'affichage normal depuis les bytes committés (invalide le
        # pixmap "aperçu direct" utilisé pendant le stroke).
        self.display_image()
