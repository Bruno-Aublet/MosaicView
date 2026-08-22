"""
modules/qt/sharpness_tool_qt.py — Outil "netteté" (sharpness) de la barre
d'outils flottante de la visionneuse principale (image_viewer_qt.py).

Fusion progressive des visionneuses : ce module contient toute la logique propre à l'outil
"sharpness" — état + preview live (mixin SharpnessCanvasMixin, hérité par
_ViewerCanvas), commit de l'ajustement dans l'historique du panneau (mixin
SharpnessViewerMixin, hérité par ImageViewer), et le panneau flottant de la
réglette (_SharpnessOptionsPanel). image_viewer_qt.py ne fait qu'hériter de ces
deux mixins et brancher l'icône de la barre d'outils — voir CLAUDE.md règle
"ne jamais migrer le code d'un outil dans image_viewer_qt.py".

Contrairement au crop/straighten/clone/texte, cet outil n'a AUCUN overlay
interactif ni geste souris sur le canvas : c'est une réglette avec preview
temps réel. SharpnessCanvasMixin reste donc volontairement minimal (pas de mousePress/
Move/Release à gérer, pas de paint_* à appeler depuis paintEvent).

PAS de bouton "Valider" pour cet outil : le preview PIL n'est visible que PENDANT le
déplacement du slider (valueChanged) ; au relâchement du clic
(sliderReleased), la netteté est commitée automatiquement dans entry['bytes']
(perform_sharpness) et devient sa propre entrée d'historique — même principe
que le clonage (clone_tool_qt.py::CloneViewerMixin._on_clone_paint_end), pas
de notion de "travail non validé" à conserver entre deux affichages : il n'y
a plus jamais d'état intermédiaire à committer plus tard, contrairement au
crop/straighten/texte. Conséquence : pas de contribution à
ImageViewer._has_unvalidated_work(), pas de _sharpness_by_page, pas de
persistance/reset spécifique au changement de page au-delà de la remise à
zéro du slider lui-même.

Icône bi-mode sharpness/unsharp : clic
droit bascule state.sharpness_mode (0=sharpness, 1=unsharp), persisté comme
state.straighten_mode (voir config_manager.py::get_/set_sharpness_mode,
panel_widget.py::_set_sharpness_mode). Contrairement à l'icône straighten
(bi-mode mais icône visuellement fixe), L'ICÔNE ELLE-MÊME CHANGE ici
(BTN_Sharpness.png / BTN_Unsharp.png) — voir _ToolButton.set_icon_filename
dans viewer_toolbar_qt.py.

Le mode unsharp (1) est implémenté sur le même principe que sharpness :
_UnsharpOptionsPanel (3 réglettes radius/percent/threshold au lieu d'une
seule) + SharpnessViewerMixin._update_unsharp_preview()/perform_unsharp()/
_reset_unsharp_preview(), mêmes points de commit (sliderReleased/
editingFinished), même réutilisation de apply_adjustments()/
apply_image_adjustments() (unsharp_radius/unsharp_percent/unsharp_threshold
déjà supportés par ces fonctions, aucune formule PIL dupliquée ici — voir
skill adjust-sharpness). self._sharpness_preview_img (ImageViewer) reste le
SEUL champ de preview pour les deux modes : un seul outil actif à la fois,
donc jamais besoin d'un preview sharpness ET unsharp simultané.
"""

from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QFrame, QSlider, QSpinBox

from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.clone_tool_qt import floating_options_panel_style


# ─────────────────────────────────────────────────────────────────────────────
# Panneau flottant de la réglette de netteté
# ─────────────────────────────────────────────────────────────────────────────

class _SharpnessOptionsPanel(QWidget):
    """Panneau flottant avec la réglette de netteté (-100..+100), affiché sous
    la barre d'outils uniquement quand l'outil "sharpness" est actif — même
    principe que _StraightenAnglePanel/_CloneOptionsPanel (jamais inséré dans
    le layout de ImageViewer, indépendant du timer d'auto-masquage de la barre
    pour ne pas interrompre un réglage en cours).

    Réglette ET spinbox synchronisées (même valeur, deux façons de la
    modifier — même principe que _CloneOptionsPanel._brush_slider/_brush_spin)
    pour permettre un réglage fin ou une saisie manuelle au clavier. Pendant
    le déplacement du slider (valueChanged) ou la frappe dans la spinbox :
    preview PIL live sans toucher entry['bytes']. Commit réel via
    SharpnessViewerMixin.perform_sharpness() au relâchement du slider
    (sliderReleased) OU à la perte de focus/validation de la spinbox
    (editingFinished) — puis les deux reviennent à 0. Pas de bouton "Valider"
    pour cet outil (voir docstring de module).
    """

    _RANGE_MIN = -100
    _RANGE_MAX = 100

    def __init__(self, viewer: "ImageViewer"):
        super().__init__(viewer._canvas)
        # Sans cet attribut, un QWidget nu n'applique jamais le "background"
        # d'une stylesheet (contrairement à QLabel/QPushButton).
        from PySide6.QtCore import Qt
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._viewer = viewer
        self.value = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        self._label = QLabel()
        layout.addWidget(self._label)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(self._RANGE_MIN)
        self._slider.setMaximum(self._RANGE_MAX)
        self._slider.setValue(0)
        self._slider.setFixedWidth(160)
        self._slider.valueChanged.connect(self._on_slider_changed)
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        layout.addWidget(self._slider)

        self._spin = QSpinBox()
        self._spin.setRange(self._RANGE_MIN, self._RANGE_MAX)
        self._spin.setValue(0)
        self._spin.setFixedWidth(62)
        self._spin.valueChanged.connect(self._on_spin_changed)
        self._spin.editingFinished.connect(self._on_spin_editing_finished)
        layout.addWidget(self._spin)

        self.hide()

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(floating_options_panel_style(theme, "_SharpnessOptionsPanel"))
        self._label.setStyleSheet(f"color: {theme['text']}; background: transparent;")
        accent = "#4a90d9"
        self._slider.setStyleSheet(
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
        self._spin.setStyleSheet(
            f"QSpinBox {{ background: {theme['bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 2px 4px; }} "
            f"QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}"
        )

    def retranslate(self):
        font = _get_current_font(11)
        self._label.setText(_("viewer.sharpness_panel_label"))
        self._label.setFont(font)
        self._spin.setFont(font)

    # ── Visibilité ────────────────────────────────────────────────────────────

    def set_visible_for_tool(self, tool_id: str | None):
        # Restreint au mode 0 (sharpness simple) : en mode 1 (unsharp),
        # c'est _UnsharpOptionsPanel qui doit être visible à la place — voir
        # _ViewerToolbar.set_active_tool, qui appelle les deux en séquence.
        if tool_id == "sharpness" and self._viewer._toolbar._sharpness_mode() == 0:
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
        self._viewer._toolbar.pause_hide()

    def leaveEvent(self, event):
        # Qt peut envoyer un Leave au panneau parent en transitant entre deux
        # widgets enfants (slider, spinbox, label) selon la plateforme/le
        # style, même quand la souris reste visuellement DANS le panneau —
        # pas seulement pendant le grab de souris d'un QSlider en drag actif
        # (déjà géré séparément par _on_slider_pressed). Un leaveEvent isolé
        # ne prouve donc pas une vraie sortie. Revérification différée à 0ms
        # (après que Qt ait fini de traiter l'éventuel enterEvent immédiat du
        # widget enfant survolé) : on ne relance le timer que si la souris
        # est RÉELLEMENT hors du rectangle du panneau à ce moment-là.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._check_really_left)

    def _check_really_left(self):
        from PySide6.QtGui import QCursor
        really_left = not self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        if really_left:
            self._viewer._toolbar.resume_hide()

    # ── Réglage ──────────────────────────────────────────────────────────────

    def reset(self):
        self.set_value_silent(0)

    def set_value_silent(self, value: int):
        """Positionne slider+spinbox sur value sans redéclencher preview ni
        commit (blockSignals) — utilisé par reset() et par la restauration
        après undo/redo (ImageViewer._refresh_after_undo_redo)."""
        self._slider.blockSignals(True)
        self._slider.setValue(value)
        self._slider.blockSignals(False)
        self._spin.blockSignals(True)
        self._spin.setValue(value)
        self._spin.blockSignals(False)
        self.value = value

    def _on_slider_changed(self, value: int):
        self.value = value
        if self._spin.value() != value:
            self._spin.blockSignals(True)
            self._spin.setValue(value)
            self._spin.blockSignals(False)
        self._viewer._update_sharpness_preview()

    def _on_slider_pressed(self):
        # QSlider capture la souris (grab) pendant tout le drag actif — Qt
        # envoie alors un leaveEvent au panneau parent même si le curseur
        # reste visuellement sur le slider (underMouse()=False au leaveEvent
        # malgré un drag en cours). Sans cet appel explicite, la barre/le
        # panneau pourraient disparaître sous les doigts de l'utilisateur en
        # pleine manipulation du slider.
        self._viewer._toolbar.pause_hide()

    def _on_slider_released(self):
        self._viewer.perform_sharpness()
        # Le grab de souris du slider se termine ici : si le curseur est
        # encore visuellement sur le panneau, un enterEvent naturel a déjà dû
        # se redéclencher entre-temps (ou le pause_hide() de _on_slider_pressed
        # tient toujours) ; sinon, laisser le prochain leaveEvent réel relancer
        # le timer normalement.

    def _on_spin_changed(self, value: int):
        self.value = value
        if self._slider.value() != value:
            self._slider.blockSignals(True)
            self._slider.setValue(value)
            self._slider.blockSignals(False)
        self._viewer._update_sharpness_preview()

    def _on_spin_editing_finished(self):
        self._viewer.perform_sharpness()


# ─────────────────────────────────────────────────────────────────────────────
# Panneau flottant des 3 réglettes de netteté adaptative (Unsharp Mask)
# ─────────────────────────────────────────────────────────────────────────────

class _UnsharpOptionsPanel(QWidget):
    """Panneau flottant avec les 3 réglettes de l'Unsharp Mask (rayon,
    intensité, seuil), affiché sous la barre d'outils uniquement quand
    l'outil "sharpness" est actif ET state.sharpness_mode == 1 — même
    principe que _SharpnessOptionsPanel (jamais inséré dans le layout de
    ImageViewer, indépendant du timer d'auto-masquage de la barre), mais 3
    groupes label/slider/spin côte à côte séparés par des QFrame VLine,
    disposition horizontale reprise de _CloneOptionsPanel (mode + taille du
    tampon) plutôt qu'un empilement vertical.

    Bornes (skill adjust-sharpness) : radius stocké en dixièmes (slider int 5..50, exposé
    via la propriété radius = valeur/10.0 => 0.5..5.0, même pattern que
    gamma), percent 0..200, threshold 0..30. Réglette ET spinbox synchronisées
    pour chacun des 3, même principe que _SharpnessOptionsPanel. Chaque
    _on_*_changed régénère un seul preview PIL global via _update_unsharp_
    preview() (les 3 valeurs sont relues ensemble à chaque fois) : pas de
    preview partiel par réglette.
    """

    _RADIUS_MIN, _RADIUS_MAX, _RADIUS_DEFAULT = 5, 50, 20    # /10.0 => 0.5..5.0, défaut 2.0
    _PERCENT_MIN, _PERCENT_MAX, _PERCENT_DEFAULT = 0, 200, 0
    _THRESHOLD_MIN, _THRESHOLD_MAX, _THRESHOLD_DEFAULT = 0, 30, 3

    def __init__(self, viewer: "ImageViewer"):
        super().__init__(viewer._canvas)
        from PySide6.QtCore import Qt
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._viewer = viewer

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        self._lbl_radius = QLabel()
        layout.addWidget(self._lbl_radius)
        self._radius_slider = QSlider(Qt.Orientation.Horizontal)
        self._radius_slider.setMinimum(self._RADIUS_MIN)
        self._radius_slider.setMaximum(self._RADIUS_MAX)
        self._radius_slider.setValue(self._RADIUS_DEFAULT)
        self._radius_slider.setFixedWidth(100)
        self._radius_slider.valueChanged.connect(self._on_radius_slider_changed)
        self._radius_slider.sliderPressed.connect(self._on_slider_pressed)
        self._radius_slider.sliderReleased.connect(self._on_slider_released)
        layout.addWidget(self._radius_slider)
        self._radius_spin = QSpinBox()
        self._radius_spin.setRange(self._RADIUS_MIN, self._RADIUS_MAX)
        self._radius_spin.setValue(self._RADIUS_DEFAULT)
        self._radius_spin.setFixedWidth(50)
        self._radius_spin.valueChanged.connect(self._on_radius_spin_changed)
        self._radius_spin.editingFinished.connect(self._on_editing_finished)
        layout.addWidget(self._radius_spin)

        self._sep1 = QFrame()
        self._sep1.setFrameShape(QFrame.Shape.VLine)
        layout.addWidget(self._sep1)

        self._lbl_percent = QLabel()
        layout.addWidget(self._lbl_percent)
        self._percent_slider = QSlider(Qt.Orientation.Horizontal)
        self._percent_slider.setMinimum(self._PERCENT_MIN)
        self._percent_slider.setMaximum(self._PERCENT_MAX)
        self._percent_slider.setValue(self._PERCENT_DEFAULT)
        self._percent_slider.setFixedWidth(100)
        self._percent_slider.valueChanged.connect(self._on_percent_slider_changed)
        self._percent_slider.sliderPressed.connect(self._on_slider_pressed)
        self._percent_slider.sliderReleased.connect(self._on_slider_released)
        layout.addWidget(self._percent_slider)
        self._percent_spin = QSpinBox()
        self._percent_spin.setRange(self._PERCENT_MIN, self._PERCENT_MAX)
        self._percent_spin.setValue(self._PERCENT_DEFAULT)
        self._percent_spin.setFixedWidth(58)
        self._percent_spin.valueChanged.connect(self._on_percent_spin_changed)
        self._percent_spin.editingFinished.connect(self._on_editing_finished)
        layout.addWidget(self._percent_spin)

        self._sep2 = QFrame()
        self._sep2.setFrameShape(QFrame.Shape.VLine)
        layout.addWidget(self._sep2)

        self._lbl_threshold = QLabel()
        layout.addWidget(self._lbl_threshold)
        self._threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self._threshold_slider.setMinimum(self._THRESHOLD_MIN)
        self._threshold_slider.setMaximum(self._THRESHOLD_MAX)
        self._threshold_slider.setValue(self._THRESHOLD_DEFAULT)
        self._threshold_slider.setFixedWidth(100)
        self._threshold_slider.valueChanged.connect(self._on_threshold_slider_changed)
        self._threshold_slider.sliderPressed.connect(self._on_slider_pressed)
        self._threshold_slider.sliderReleased.connect(self._on_slider_released)
        layout.addWidget(self._threshold_slider)
        self._threshold_spin = QSpinBox()
        self._threshold_spin.setRange(self._THRESHOLD_MIN, self._THRESHOLD_MAX)
        self._threshold_spin.setValue(self._THRESHOLD_DEFAULT)
        self._threshold_spin.setFixedWidth(50)
        self._threshold_spin.valueChanged.connect(self._on_threshold_spin_changed)
        self._threshold_spin.editingFinished.connect(self._on_editing_finished)
        layout.addWidget(self._threshold_spin)

        self.hide()

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(floating_options_panel_style(theme, "_UnsharpOptionsPanel"))
        for lbl in (self._lbl_radius, self._lbl_percent, self._lbl_threshold):
            lbl.setStyleSheet(f"color: {theme['text']}; background: transparent;")
        for sep in (self._sep1, self._sep2):
            sep.setStyleSheet(f"color: {theme['separator']};")
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
        spin_style = (
            f"QSpinBox {{ background: {theme['bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 2px 4px; }} "
            f"QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}"
        )
        for slider in (self._radius_slider, self._percent_slider, self._threshold_slider):
            slider.setStyleSheet(slider_style)
        for spin in (self._radius_spin, self._percent_spin, self._threshold_spin):
            spin.setStyleSheet(spin_style)

    def retranslate(self):
        font = _get_current_font(11)
        self._lbl_radius.setText(_("viewer.unsharp_radius_label"))
        self._lbl_radius.setFont(font)
        self._lbl_percent.setText(_("viewer.unsharp_percent_label"))
        self._lbl_percent.setFont(font)
        self._lbl_threshold.setText(_("viewer.unsharp_threshold_label"))
        self._lbl_threshold.setFont(font)
        for spin in (self._radius_spin, self._percent_spin, self._threshold_spin):
            spin.setFont(font)

    # ── Visibilité ────────────────────────────────────────────────────────────

    def set_visible_for_tool(self, tool_id: str | None):
        if tool_id == "sharpness" and self._viewer._toolbar._sharpness_mode() == 1:
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
        self._viewer._toolbar.pause_hide()

    def leaveEvent(self, event):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._check_really_left)

    def _check_really_left(self):
        from PySide6.QtGui import QCursor
        really_left = not self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        if really_left:
            self._viewer._toolbar.resume_hide()

    # ── Valeurs ──────────────────────────────────────────────────────────────

    @property
    def radius(self) -> float:
        return round(self._radius_slider.value() / 10.0, 1)

    @property
    def percent(self) -> int:
        return self._percent_slider.value()

    @property
    def threshold(self) -> int:
        return self._threshold_slider.value()

    def reset(self):
        self.set_values_silent(self._RADIUS_DEFAULT / 10.0, self._PERCENT_DEFAULT, self._THRESHOLD_DEFAULT)

    def set_values_silent(self, radius: float, percent: int, threshold: int):
        """Positionne les 3 réglettes+spinboxes sans redéclencher preview ni
        commit (blockSignals) — utilisé par reset() et par la restauration
        après undo/redo (ImageViewer._refresh_after_undo_redo)."""
        radius_slider_val = int(round(radius * 10))
        for widget, value in (
            (self._radius_slider, radius_slider_val), (self._radius_spin, radius_slider_val),
            (self._percent_slider, percent), (self._percent_spin, percent),
            (self._threshold_slider, threshold), (self._threshold_spin, threshold),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)

    def _on_radius_slider_changed(self, value: int):
        if self._radius_spin.value() != value:
            self._radius_spin.blockSignals(True)
            self._radius_spin.setValue(value)
            self._radius_spin.blockSignals(False)
        self._viewer._update_unsharp_preview()

    def _on_radius_spin_changed(self, value: int):
        if self._radius_slider.value() != value:
            self._radius_slider.blockSignals(True)
            self._radius_slider.setValue(value)
            self._radius_slider.blockSignals(False)
        self._viewer._update_unsharp_preview()

    def _on_percent_slider_changed(self, value: int):
        if self._percent_spin.value() != value:
            self._percent_spin.blockSignals(True)
            self._percent_spin.setValue(value)
            self._percent_spin.blockSignals(False)
        self._viewer._update_unsharp_preview()

    def _on_percent_spin_changed(self, value: int):
        if self._percent_slider.value() != value:
            self._percent_slider.blockSignals(True)
            self._percent_slider.setValue(value)
            self._percent_slider.blockSignals(False)
        self._viewer._update_unsharp_preview()

    def _on_threshold_slider_changed(self, value: int):
        if self._threshold_spin.value() != value:
            self._threshold_spin.blockSignals(True)
            self._threshold_spin.setValue(value)
            self._threshold_spin.blockSignals(False)
        self._viewer._update_unsharp_preview()

    def _on_threshold_spin_changed(self, value: int):
        if self._threshold_slider.value() != value:
            self._threshold_slider.blockSignals(True)
            self._threshold_slider.setValue(value)
            self._threshold_slider.blockSignals(False)
        self._viewer._update_unsharp_preview()

    def _on_slider_pressed(self):
        # Même piège que _SharpnessOptionsPanel._on_slider_pressed (grab de
        # souris pendant le drag actif d'un QSlider) — voir sa docstring.
        self._viewer._toolbar.pause_hide()

    def _on_slider_released(self):
        self._viewer.perform_unsharp()

    def _on_editing_finished(self):
        self._viewer.perform_unsharp()


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état de l'outil (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class SharpnessCanvasMixin:
    """Hérité par _ViewerCanvas (image_viewer_qt.py) en plus de QLabel : ajoute
    l'état minimal de l'outil "sharpness" au canvas de la visionneuse, sans
    que son code vive dans image_viewer_qt.py.

    Volontairement quasi vide : contrairement à crop/straighten/clone/texte,
    cet outil n'a aucun overlay dessiné sur le canvas ni aucun geste souris à
    intercepter (mousePress/Move/ReleaseEvent de _ViewerCanvas n'ont donc
    aucune branche "sharpness" à ajouter) — le réglage se fait entièrement via
    la réglette du panneau flottant (_SharpnessOptionsPanel), le preview via
    le pixmap affiché normalement par ImageViewer.display_image().
    """

    def _init_adjustments_state(self):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — preview live + commit dans l'historique du panneau (hérité par ImageViewer)
# ─────────────────────────────────────────────────────────────────────────────

class SharpnessViewerMixin:
    """Hérité par ImageViewer (image_viewer_qt.py) en plus de QDialog : ajoute
    la logique de l'outil "sharpness" au viewer, sans que son code vive dans
    image_viewer_qt.py. Suppose que l'hôte a déjà self._canvas
    (_ViewerCanvas, avec SharpnessCanvasMixin), self.callbacks,
    self.current_idx, self._toolbar (avec _sharpness_panel).
    """

    def _update_sharpness_preview(self):
        """Régénère le pixmap affiché avec la valeur courante de la réglette,
        SANS toucher entry['bytes'] — réutilise apply_adjustments() pour ne
        pas dupliquer la formule PIL (asymétrie ImageEnhance.Sharpness/
        GaussianBlur documentée dans le skill adjust-sharpness).

        Le résultat est stocké dans self._sharpness_preview_img : _display_
        single_page (image_viewer_qt.py) l'utilise à la place de
        ensure_image_loaded(entry) quand il est défini pour la page courante
        — évite de dupliquer ici tout le calcul de zoom/offset/checkerboard
        déjà fait par _display_single_page. Purement visuel pendant le drag :
        perform_sharpness() (appelée au relâchement) recharge de toute façon
        entry['bytes'] depuis zéro, donc ce cache n'a pas besoin d'être
        cohérent au-delà de l'aperçu affiché."""
        from modules.qt import state as _state_module
        from modules.qt.image_processing_qt import apply_adjustments

        value = self._toolbar._sharpness_panel.value
        if value == 0:
            self._sharpness_preview_img = None
            self.display_image(keep_crop_rect=True)
            return

        state = self.callbacks.get('state') or _state_module.state
        entry = state.images_data[self.current_idx]
        if not entry.get('bytes'):
            return

        import io
        from PIL import Image
        original = Image.open(io.BytesIO(entry['bytes']))
        self._sharpness_preview_img = apply_adjustments(
            original.copy(), {'sharpness': value}, for_preview=True)
        self.display_image(keep_crop_rect=True)

    def perform_sharpness(self, skip_history: bool = False):
        """skip_history : propagé à apply_image_adjustments().

        Relâchement du slider : commit réel de la netteté dans
        entry['bytes'] (pattern skill apply-image-operation, variante A
        complète) — réutilise apply_image_adjustments() (image_processing_qt.py).
        Devient sa propre entrée d'historique,
        comme un coup de tampon de clonage (pas de bouton "Valider" séparé,
        voir docstring de module).

        Le slider/spinbox NE revient PAS à 0 après ce commit : il reste sur
        la valeur qui vient d'être appliquée, pour rester une indication
        visuelle de l'ajustement en cours plutôt que de donner l'impression
        que le réglage a été perdu alors que l'image a bien changé. Un
        nouveau geste sur le slider après ce commit applique un ajustement
        ADDITIONNEL par-dessus l'image déjà modifiée (le calcul PIL repart de
        entry['bytes'] courant à chaque fois, pas d'un état "absolu"
        mémorisé) — cohérent avec le fait qu'aucune valeur "totale"
        fiable n'existe une fois les pixels réellement modifiés."""
        from modules.qt import state as _state_module
        from modules.qt.image_processing_qt import apply_image_adjustments
        from modules.qt.dialogs_qt import MsgDialog

        panel = self._toolbar._sharpness_panel
        value = panel.value
        if value == 0:
            return

        state = self.callbacks.get('state') or _state_module.state
        canvas = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]
            apply_image_adjustments([entry], {'sharpness': value}, callbacks=self.callbacks,
                                     skip_history=skip_history)

            # apply_image_adjustments() vient de faire save_state(force=True)
            # en interne : state.history_index pointe maintenant sur CE
            # commit. Mémorisé sur state (PAS sur self/ImageViewer, voir
            # state.py) pour réafficher la bonne valeur sur le slider si un
            # undo/redo retombe pile sur ce point d'historique — y compris
            # après une fermeture/réouverture de la visionneuse, puisque
            # l'historique lui-même survit à la fenêtre (voir
            # ImageViewer._refresh_after_undo_redo).
            state.sharpness_value_by_history_index[(self.current_idx, state.history_index)] = value

            real_idx = entry.get("_real_idx")
            if canvas is not None and real_idx is not None:
                from modules.qt.mosaic_canvas import build_qimage_for_entry
                build_qimage_for_entry(entry)
                canvas.refresh_thumbnail(real_idx)
                canvas.refresh_duplicate_overlay()
            update_btn = self.callbacks.get("update_button_text")
            if update_btn:
                update_btn()

            self._sharpness_preview_img = None
            # keep_crop_rect=True : pas pour préserver un crop (il n'y en a
            # jamais pendant un ajustement de netteté), mais pour éviter
            # l'effet de bord de display_image() sans ce flag, qui appelle
            # inconditionnellement _canvas.clear_crop() — lequel remet aussi
            # pan_offset_x/y à 0 (crop_tool_qt.py::clear_crop, pensé pour
            # recentrer la vue quand on abandonne un crop). Sans ce flag, tout
            # commit après un zoom+pan recentrerait l'image sous les pieds de
            # l'utilisateur — même cause que dans
            # levels_tool_qt.py::perform_levels.
            self.display_image(keep_crop_rect=True)
            self._toolbar.refresh_undo_redo_state()
            self._macro_record_step(
                "sharpness", {"value": value},
                "macro.step_sharpness", {"value": value},
            )
            return True

        except Exception as e:
            dlg = MsgDialog(self._center_parent, "messages.errors.sharpness_failed.title",
                            "messages.errors.sharpness_failed.message",
                            message_kwargs={"error": str(e)})
            dlg.show_nonmodal()
            return False

    def _reset_sharpness_preview(self):
        """Annule le preview visuel en cours (drag non relâché) et
        resynchronise le slider/spinbox sur la page COURANTE (self.
        current_idx, potentiellement déjà mise à jour par l'appelant en cas
        de changement de page) — appelé au changement de page, à la
        désélection de l'outil, et après undo/redo (_refresh_after_undo_redo).

        Il ne peut jamais y avoir de valeur "en attente" à committer (le
        relâchement du slider commit déjà tout, voir perform_sharpness), mais
        le slider doit refléter la dernière valeur RÉELLEMENT commitée sur la
        page affichée, pas systématiquement 0 : state.
        sharpness_value_by_history_index (voir state.py) retrouve cette
        valeur si un commit sharpness existe pour (page, history_index)
        courants, sinon 0 (page jamais ajustée à ce point d'historique).

        Resynchronise aussi le panneau unsharp (_reset_unsharp_preview) dans
        le même mouvement : les deux modes partagent ce seul point d'entrée
        (tous les appelants existants — changement de page, désélection
        d'outil, undo/redo — doivent resynchroniser les deux, pas seulement
        le mode actif au moment de l'appel, sinon l'autre panneau resterait
        périmé au prochain basculement de mode)."""
        from modules.qt import state as _state_module
        self._sharpness_preview_img = None
        state = self.callbacks.get('state') or _state_module.state
        value = state.sharpness_value_by_history_index.get(
            (self.current_idx, state.history_index), 0)
        self._toolbar._sharpness_panel.set_value_silent(value)
        self._reset_unsharp_preview()

    # ── Netteté adaptative (Unsharp Mask) — même pattern que sharpness ──────────

    def _update_unsharp_preview(self):
        """Équivalent de _update_sharpness_preview() pour les 3 réglettes
        unsharp — même self._sharpness_preview_img (un seul champ de preview,
        un seul outil actif à la fois, voir docstring de module). Le filtre
        Unsharp Mask ne s'active que si percent > 0 (voir apply_adjustments,
        skill adjust-sharpness) : à percent == 0, pas d'effet visible même si
        radius/threshold ont bougé, comportement PIL attendu, pas un bug."""
        from modules.qt import state as _state_module
        from modules.qt.image_processing_qt import apply_adjustments

        panel = self._toolbar._unsharp_panel
        if panel.percent == 0:
            self._sharpness_preview_img = None
            self.display_image(keep_crop_rect=True)
            return

        state = self.callbacks.get('state') or _state_module.state
        entry = state.images_data[self.current_idx]
        if not entry.get('bytes'):
            return

        import io
        from PIL import Image
        original = Image.open(io.BytesIO(entry['bytes']))
        self._sharpness_preview_img = apply_adjustments(
            original.copy(),
            {
                'unsharp_radius': panel.radius,
                'unsharp_percent': panel.percent,
                'unsharp_threshold': panel.threshold,
            },
            for_preview=True)
        self.display_image(keep_crop_rect=True)

    def perform_unsharp(self, skip_history: bool = False):
        """skip_history : propagé à apply_image_adjustments().

        Équivalent de perform_sharpness() pour les 3 réglettes unsharp :
        commit réel dans entry['bytes'] au relâchement d'un slider ou à la
        validation d'une spinbox, réutilise apply_image_adjustments().
        Les réglettes ne reviennent PAS à leurs valeurs par défaut après ce
        commit, même raison que perform_sharpness()."""
        from modules.qt import state as _state_module
        from modules.qt.image_processing_qt import apply_image_adjustments
        from modules.qt.dialogs_qt import MsgDialog

        panel = self._toolbar._unsharp_panel
        radius, percent, threshold = panel.radius, panel.percent, panel.threshold
        if percent == 0:
            return

        state = self.callbacks.get('state') or _state_module.state
        canvas = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]
            apply_image_adjustments([entry], {
                'unsharp_radius': radius,
                'unsharp_percent': percent,
                'unsharp_threshold': threshold,
            }, callbacks=self.callbacks, skip_history=skip_history)

            # Même principe que perform_sharpness() : mémorisé sur state (PAS
            # sur self/ImageViewer) pour survivre à une fermeture/réouverture
            # de la visionneuse — voir state.py::unsharp_value_by_history_index.
            state.unsharp_value_by_history_index[(self.current_idx, state.history_index)] = (
                radius, percent, threshold)

            real_idx = entry.get("_real_idx")
            if canvas is not None and real_idx is not None:
                from modules.qt.mosaic_canvas import build_qimage_for_entry
                build_qimage_for_entry(entry)
                canvas.refresh_thumbnail(real_idx)
                canvas.refresh_duplicate_overlay()
            update_btn = self.callbacks.get("update_button_text")
            if update_btn:
                update_btn()

            self._sharpness_preview_img = None
            # keep_crop_rect=True : pas pour préserver un crop (il n'y en a
            # jamais pendant un ajustement de netteté adaptative), mais pour
            # éviter l'effet de bord de display_image() sans ce flag, qui
            # appelle inconditionnellement _canvas.clear_crop() — lequel remet
            # aussi pan_offset_x/y à 0 (crop_tool_qt.py::clear_crop, pensé
            # pour recentrer la vue quand on abandonne un crop). Sans ce flag,
            # tout commit après un zoom+pan recentrerait l'image sous les
            # pieds de l'utilisateur — même cause que dans
            # levels_tool_qt.py::perform_levels.
            self.display_image(keep_crop_rect=True)
            self._toolbar.refresh_undo_redo_state()
            self._macro_record_step(
                "unsharp", {"radius": radius, "percent": percent, "threshold": threshold},
                "macro.step_unsharp",
                {"radius": radius, "percent": percent, "threshold": threshold},
            )
            return True

        except Exception as e:
            dlg = MsgDialog(self._center_parent, "messages.errors.unsharp_failed.title",
                            "messages.errors.unsharp_failed.message",
                            message_kwargs={"error": str(e)})
            dlg.show_nonmodal()
            return False

    def _reset_unsharp_preview(self):
        """Équivalent de _reset_sharpness_preview() pour les 3 réglettes
        unsharp — resynchronise sur la dernière valeur RÉELLEMENT commitée
        pour (page, history_index) courants via state.
        unsharp_value_by_history_index, sinon les valeurs par défaut (0.5..
        5.0 défaut 2.0 / 0 / 3, aucun commit unsharp à ce point d'historique)."""
        from modules.qt import state as _state_module
        self._sharpness_preview_img = None
        state = self.callbacks.get('state') or _state_module.state
        values = state.unsharp_value_by_history_index.get(
            (self.current_idx, state.history_index))
        panel = self._toolbar._unsharp_panel
        if values is None:
            panel.reset()
        else:
            radius, percent, threshold = values
            panel.set_values_silent(radius, percent, threshold)
