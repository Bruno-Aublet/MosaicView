"""
modules/qt/brightness_tool_qt.py — Outil "luminosité/contraste" (brightness)
de la barre d'outils flottante de la visionneuse principale (image_viewer_qt.py).

Fusion progressive des visionneuses : ce module contient toute la
logique propre à l'outil "brightness" — état + preview live (mixin
BrightnessCanvasMixin, hérité par _ViewerCanvas), commit de l'ajustement dans
l'historique du panneau (mixin BrightnessViewerMixin, hérité par ImageViewer),
et le panneau flottant des 2 réglettes (_BrightnessOptionsPanel).
image_viewer_qt.py ne fait qu'hériter de ces deux mixins et brancher l'icône
de la barre d'outils — voir CLAUDE.md règle "ne jamais migrer le code d'un
outil dans image_viewer_qt.py".

Même pattern que sharpness_tool_qt.py (sharpness/unsharp), avec 2
différences :
  - Une seule icône, PAS de bi-mode (clic droit sans effet) : luminosité et
    contraste sont 2 réglettes indépendantes dans le MÊME panneau flottant
    (les 2 réglages partagent une seule section UI).
  - Pas d'icône qui change (contrairement à sharpness/unsharp) : BTN_Brightness.png
    fixe.

Contrairement au crop/straighten/clone/texte, cet outil n'a AUCUN overlay
interactif ni geste souris sur le canvas : c'est 2 réglettes avec preview
temps réel. BrightnessCanvasMixin reste donc volontairement minimal (pas de mousePress/
Move/Release à gérer, pas de paint_* à appeler depuis paintEvent) — même
raison que SharpnessCanvasMixin (sharpness).

PAS de bouton "Valider" pour cet outil (même principe que sharpness/unsharp) :
le preview PIL n'est visible que PENDANT le
déplacement d'un slider (valueChanged) ; au relâchement du clic
(sliderReleased), l'ajustement est commité automatiquement dans
entry['bytes'] (perform_brightness) et devient sa propre entrée d'historique
— pas de notion de "travail non validé" à conserver entre deux affichages :
il n'y a plus jamais d'état intermédiaire à committer plus tard, contrairement
au crop/straighten/texte. Conséquence : pas de contribution à
ImageViewer._has_unvalidated_work(), pas de _brightness_by_page, pas de
persistance/reset spécifique au changement de page au-delà de la remise à
zéro des sliders eux-mêmes.

self._sharpness_preview_img (ImageViewer, défini dans sharpness_tool_qt.py)
est RÉUTILISÉ tel quel comme champ de preview partagé pour ce mode aussi —
un seul outil actif à la fois dans la barre, donc jamais besoin d'un preview
sharpness/unsharp ET brightness simultané. Pas de nouveau champ dédié.
"""

from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QSlider, QSpinBox

from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.clone_tool_qt import floating_options_panel_style


# ─────────────────────────────────────────────────────────────────────────────
# Panneau flottant des 2 réglettes luminosité/contraste
# ─────────────────────────────────────────────────────────────────────────────

class _BrightnessOptionsPanel(QWidget):
    """Panneau flottant avec les 2 réglettes luminosité/contraste (-100..+100
    chacune), affiché sous la barre d'outils uniquement quand l'outil
    "brightness" est actif — même principe que _SharpnessOptionsPanel (jamais
    inséré dans le layout de ImageViewer, indépendant du timer d'auto-masquage
    de la barre pour ne pas interrompre un réglage en cours), mais 2 lignes
    label/slider/spin empilées verticalement (une par réglette) au lieu d'une
    seule ligne.

    Réglette ET spinbox synchronisées pour chacune des 2 (même valeur, deux
    façons de la modifier — même principe que _SharpnessOptionsPanel). Pendant
    le déplacement d'un slider (valueChanged) ou la frappe dans une spinbox :
    preview PIL live combinant les 2 valeurs courantes, sans toucher
    entry['bytes']. Commit réel via BrightnessViewerMixin.perform_brightness()
    au relâchement d'un slider (sliderReleased) OU à la perte de focus/
    validation d'une spinbox (editingFinished) — puis les deux réglettes NE
    reviennent PAS à 0 (voir perform_brightness). Pas de bouton "Valider" pour
    cet outil (voir docstring de module).
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
        self.brightness_value = 0
        self.contrast_value = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(4)

        bright_row = QHBoxLayout()
        bright_row.setSpacing(8)
        self._bright_label = QLabel()
        bright_row.addWidget(self._bright_label)
        self._bright_slider = QSlider(Qt.Orientation.Horizontal)
        self._bright_slider.setMinimum(self._RANGE_MIN)
        self._bright_slider.setMaximum(self._RANGE_MAX)
        self._bright_slider.setValue(0)
        self._bright_slider.setFixedWidth(160)
        self._bright_slider.valueChanged.connect(self._on_bright_slider_changed)
        self._bright_slider.sliderPressed.connect(self._on_slider_pressed)
        self._bright_slider.sliderReleased.connect(self._on_slider_released)
        bright_row.addWidget(self._bright_slider)
        self._bright_spin = QSpinBox()
        self._bright_spin.setRange(self._RANGE_MIN, self._RANGE_MAX)
        self._bright_spin.setValue(0)
        self._bright_spin.setFixedWidth(62)
        self._bright_spin.valueChanged.connect(self._on_bright_spin_changed)
        self._bright_spin.editingFinished.connect(self._on_editing_finished)
        bright_row.addWidget(self._bright_spin)
        outer.addLayout(bright_row)

        contrast_row = QHBoxLayout()
        contrast_row.setSpacing(8)
        self._contrast_label = QLabel()
        contrast_row.addWidget(self._contrast_label)
        self._contrast_slider = QSlider(Qt.Orientation.Horizontal)
        self._contrast_slider.setMinimum(self._RANGE_MIN)
        self._contrast_slider.setMaximum(self._RANGE_MAX)
        self._contrast_slider.setValue(0)
        self._contrast_slider.setFixedWidth(160)
        self._contrast_slider.valueChanged.connect(self._on_contrast_slider_changed)
        self._contrast_slider.sliderPressed.connect(self._on_slider_pressed)
        self._contrast_slider.sliderReleased.connect(self._on_slider_released)
        contrast_row.addWidget(self._contrast_slider)
        self._contrast_spin = QSpinBox()
        self._contrast_spin.setRange(self._RANGE_MIN, self._RANGE_MAX)
        self._contrast_spin.setValue(0)
        self._contrast_spin.setFixedWidth(62)
        self._contrast_spin.valueChanged.connect(self._on_contrast_spin_changed)
        self._contrast_spin.editingFinished.connect(self._on_editing_finished)
        contrast_row.addWidget(self._contrast_spin)
        outer.addLayout(contrast_row)

        self.hide()

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(floating_options_panel_style(theme, "_BrightnessOptionsPanel"))
        self._bright_label.setStyleSheet(f"color: {theme['text']}; background: transparent;")
        self._contrast_label.setStyleSheet(f"color: {theme['text']}; background: transparent;")
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
        self._bright_slider.setStyleSheet(slider_style)
        self._contrast_slider.setStyleSheet(slider_style)
        self._bright_spin.setStyleSheet(spin_style)
        self._contrast_spin.setStyleSheet(spin_style)

    def retranslate(self):
        font = _get_current_font(11)
        self._bright_label.setText(_("viewer.brightness_panel_label"))
        self._bright_label.setFont(font)
        self._bright_spin.setFont(font)
        self._contrast_label.setText(_("viewer.contrast_panel_label"))
        self._contrast_label.setFont(font)
        self._contrast_spin.setFont(font)

    # ── Visibilité ────────────────────────────────────────────────────────────

    def set_visible_for_tool(self, tool_id: str | None):
        if tool_id == "brightness":
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
        # Sans ce blindage, un clic sur une zone vide du panneau (marges
        # entre les widgets, pas absorbée par un QSlider/QSpinBox enfant)
        # "fuit" vers _ViewerCanvas en dessous (widget flottant enfant du
        # canvas) — même piège déjà documenté pour
        # _ToolButton/_ActionButton/_ViewerToolbar (skill viewers), appliqué
        # par cohérence à tous les panneaux flottants de cette barre.
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def enterEvent(self, event):
        self._viewer._toolbar.pause_hide()

    def leaveEvent(self, event):
        # Voir _SharpnessOptionsPanel.leaveEvent : Qt peut envoyer un Leave au
        # panneau parent en transitant entre deux widgets enfants même quand
        # la souris reste visuellement dedans — revérification différée à 0ms.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._check_really_left)

    def _check_really_left(self):
        from PySide6.QtGui import QCursor
        really_left = not self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        if really_left:
            self._viewer._toolbar.resume_hide()

    # ── Réglage ──────────────────────────────────────────────────────────────

    def reset(self):
        self.set_values_silent(0, 0)

    def set_values_silent(self, brightness: int, contrast: int):
        """Positionne les 2 réglettes+spinboxes sans redéclencher preview ni
        commit (blockSignals) — utilisé par reset() et par la restauration
        après undo/redo (ImageViewer._refresh_after_undo_redo)."""
        for widget, value in (
            (self._bright_slider, brightness), (self._bright_spin, brightness),
            (self._contrast_slider, contrast), (self._contrast_spin, contrast),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self.brightness_value = brightness
        self.contrast_value = contrast

    def _on_bright_slider_changed(self, value: int):
        self.brightness_value = value
        if self._bright_spin.value() != value:
            self._bright_spin.blockSignals(True)
            self._bright_spin.setValue(value)
            self._bright_spin.blockSignals(False)
        self._viewer._update_brightness_preview()

    def _on_bright_spin_changed(self, value: int):
        self.brightness_value = value
        if self._bright_slider.value() != value:
            self._bright_slider.blockSignals(True)
            self._bright_slider.setValue(value)
            self._bright_slider.blockSignals(False)
        self._viewer._update_brightness_preview()

    def _on_contrast_slider_changed(self, value: int):
        self.contrast_value = value
        if self._contrast_spin.value() != value:
            self._contrast_spin.blockSignals(True)
            self._contrast_spin.setValue(value)
            self._contrast_spin.blockSignals(False)
        self._viewer._update_brightness_preview()

    def _on_contrast_spin_changed(self, value: int):
        self.contrast_value = value
        if self._contrast_slider.value() != value:
            self._contrast_slider.blockSignals(True)
            self._contrast_slider.setValue(value)
            self._contrast_slider.blockSignals(False)
        self._viewer._update_brightness_preview()

    def _on_slider_pressed(self):
        # QSlider capture la souris (grab) pendant tout le drag actif — même
        # piège que _SharpnessOptionsPanel._on_slider_pressed, voir sa docstring.
        self._viewer._toolbar.pause_hide()

    def _on_slider_released(self):
        self._viewer.perform_brightness()

    def _on_editing_finished(self):
        self._viewer.perform_brightness()


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état de l'outil (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class BrightnessCanvasMixin:
    """Hérité par _ViewerCanvas (image_viewer_qt.py) en plus de QLabel : ajoute
    l'état minimal de l'outil "brightness" au canvas de la visionneuse, sans
    que son code vive dans image_viewer_qt.py.

    Volontairement quasi vide : contrairement à crop/straighten/clone/texte,
    cet outil n'a aucun overlay dessiné sur le canvas ni aucun geste souris à
    intercepter — le réglage se fait entièrement via les 2 réglettes du
    panneau flottant (_BrightnessOptionsPanel), le preview via le pixmap
    affiché normalement par ImageViewer.display_image() — même principe que
    SharpnessCanvasMixin (sharpness).
    """

    def _init_brightness_state(self):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — preview live + commit dans l'historique du panneau (hérité par ImageViewer)
# ─────────────────────────────────────────────────────────────────────────────

class BrightnessViewerMixin:
    """Hérité par ImageViewer (image_viewer_qt.py) en plus de QDialog : ajoute
    la logique de l'outil "brightness" au viewer, sans que son code vive dans
    image_viewer_qt.py. Suppose que l'hôte a déjà self._canvas
    (_ViewerCanvas, avec BrightnessCanvasMixin), self.callbacks,
    self.current_idx, self._toolbar (avec _brightness_panel), et
    self._sharpness_preview_img (champ de preview PARTAGÉ avec sharpness/
    unsharp, défini dans image_viewer_qt.py::ImageViewer.__init__ — un seul
    outil actif à la fois dans la barre, jamais besoin d'un preview simultané).
    """

    def _update_brightness_preview(self):
        """Régénère le pixmap affiché avec les valeurs courantes des 2
        réglettes, SANS toucher entry['bytes'] — même principe que
        SharpnessViewerMixin._update_sharpness_preview(), réutilise la même
        fonction de traitement (apply_adjustments) pour ne pas dupliquer la
        formule PIL (ImageEnhance.Brightness/Contrast, skill
        adjust-brightness-contrast).

        Le résultat est stocké dans self._sharpness_preview_img (champ
        partagé, voir docstring de classe) : _display_single_page
        (image_viewer_qt.py) l'utilise à la place de ensure_image_loaded(entry)
        quand il est défini pour la page courante."""
        from modules.qt import state as _state_module
        from modules.qt.image_processing_qt import apply_adjustments

        panel = self._toolbar._brightness_panel
        brightness, contrast = panel.brightness_value, panel.contrast_value
        if brightness == 0 and contrast == 0:
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
            {'brightness': brightness, 'contrast': contrast},
            for_preview=True)
        self.display_image(keep_crop_rect=True)

    def perform_brightness(self):
        """Relâchement d'un slider ou validation d'une spinbox : commit réel
        de la luminosité/contraste dans entry['bytes'] (pattern skill
        apply-image-operation, variante A complète) — réutilise
        apply_image_adjustments() (image_processing_qt.py), déjà
        utilisée par le panneau Ajustements pour "Appliquer à la page
        courante". Devient sa propre entrée d'historique, comme un commit de
        netteté (pas de bouton "Valider" séparé, voir docstring de module).

        Les 2 réglettes NE reviennent PAS à 0 après ce commit (même principe
        que perform_sharpness()) : elles restent sur les valeurs qui viennent
        d'être appliquées, pour rester une indication visuelle de l'ajustement
        en cours plutôt que de donner l'impression que le réglage a été perdu
        alors que l'image a bien changé. Un nouveau geste sur une réglette
        après ce commit applique un ajustement ADDITIONNEL par-dessus l'image
        déjà modifiée (le calcul PIL repart de entry['bytes'] courant à chaque
        fois, pas d'un état "absolu" mémorisé) — comportement accepté
        explicitement, cohérent avec sharpness/unsharp."""
        from modules.qt import state as _state_module
        from modules.qt.image_processing_qt import apply_image_adjustments
        from modules.qt.dialogs_qt import MsgDialog

        panel = self._toolbar._brightness_panel
        brightness, contrast = panel.brightness_value, panel.contrast_value
        if brightness == 0 and contrast == 0:
            return

        state = self.callbacks.get('state') or _state_module.state
        canvas = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]
            apply_image_adjustments(
                [entry], {'brightness': brightness, 'contrast': contrast},
                callbacks=self.callbacks)

            # apply_image_adjustments() vient de faire save_state(force=True)
            # en interne : state.history_index pointe maintenant sur CE
            # commit. Mémorisé sur state (PAS sur self/ImageViewer, voir
            # state.py) pour réafficher les bonnes valeurs sur les réglettes
            # si un undo/redo retombe pile sur ce point d'historique — même
            # principe que sharpness_value_by_history_index.
            state.brightness_value_by_history_index[(self.current_idx, state.history_index)] = (
                brightness, contrast)

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
            # jamais pendant un ajustement de luminosité/contraste), mais
            # pour éviter l'effet de bord de display_image() sans ce flag,
            # qui appelle inconditionnellement _canvas.clear_crop() — lequel
            # remet aussi pan_offset_x/y à 0 (crop_tool_qt.py::clear_crop,
            # pensé pour recentrer la vue quand on abandonne un crop). Sans ce
            # flag, tout commit après un zoom+pan recentrerait l'image sous
            # les pieds de l'utilisateur — même cause que dans
            # levels_tool_qt.py::perform_levels.
            self.display_image(keep_crop_rect=True)
            self._toolbar.refresh_undo_redo_state()

        except Exception as e:
            dlg = MsgDialog(self._center_parent, "messages.errors.brightness_failed.title",
                            "messages.errors.brightness_failed.message",
                            message_kwargs={"error": str(e)})
            dlg.show_nonmodal()

    def _reset_brightness_preview(self):
        """Annule le preview visuel en cours (drag non relâché) et
        resynchronise les 2 réglettes sur la page COURANTE (self.current_idx,
        potentiellement déjà mise à jour par l'appelant en cas de changement
        de page) — appelé au changement de page, à la désélection de l'outil,
        et après undo/redo (_refresh_after_undo_redo). Même principe que
        _reset_sharpness_preview().

        Il ne peut jamais y avoir de valeur "en attente" à committer (le
        relâchement d'un slider commit déjà tout, voir perform_brightness),
        mais les réglettes doivent refléter les dernières valeurs RÉELLEMENT
        commitées sur la page affichée, pas systématiquement 0 : state.
        brightness_value_by_history_index retrouve ce couple si un commit
        brightness existe pour (page, history_index) courants, sinon (0, 0)
        (page jamais ajustée à ce point d'historique)."""
        from modules.qt import state as _state_module
        self._sharpness_preview_img = None
        state = self.callbacks.get('state') or _state_module.state
        values = state.brightness_value_by_history_index.get(
            (self.current_idx, state.history_index))
        panel = self._toolbar._brightness_panel
        if values is None:
            panel.set_values_silent(0, 0)
        else:
            brightness, contrast = values
            panel.set_values_silent(brightness, contrast)
