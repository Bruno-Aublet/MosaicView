"""
modules/qt/remove_colors_tool_qt.py — Outil "suppression des couleurs"
(remove_colors) de la barre d'outils flottante de la visionneuse principale
(image_viewer_qt.py).

Fusion progressive des visionneuses : ce module
contient toute la logique propre à l'outil "remove_colors" — état + preview
live (mixin RemoveColorsCanvasMixin, hérité par _ViewerCanvas), commit de
l'ajustement dans l'historique du panneau (mixin RemoveColorsViewerMixin,
hérité par ImageViewer), et le panneau flottant de la réglette
(_RemoveColorsOptionsPanel). image_viewer_qt.py ne fait qu'hériter de ces deux
mixins et brancher l'icône de la barre d'outils — voir CLAUDE.md règle "ne
jamais migrer le code d'un outil dans image_viewer_qt.py".

Même famille de pattern que brightness (brightness_tool_qt.py) : une seule
réglette, PAS de bi-mode, slider/spinbox reste sur la valeur commitée après
relâchement (ne revient PAS à 0, aligné sur brightness). Module dédié
séparé, pas ajouté dans sharpness_tool_qt.py. Seule différence de bornes :
réglette 0..100 (pas -100..+100 comme saturation/sharpness) — voir
apply_adjustments() dans image_processing_qt.py, seul moteur de calcul
restant, partagé (settings['remove_colors_intensity']).

Contrairement au crop/straighten/clone/texte, cet outil n'a AUCUN overlay
interactif ni geste souris sur le canvas : c'est une réglette avec preview
temps réel. RemoveColorsCanvasMixin reste donc volontairement minimal
(pas de mousePress/Move/Release à gérer, pas de paint_* à appeler depuis
paintEvent) — même raison que SharpnessCanvasMixin (sharpness),
BrightnessCanvasMixin et SaturationCanvasMixin.

PAS de bouton "Valider" pour cet outil (même principe que sharpness/unsharp/
brightness/saturation) : le preview PIL n'est
visible que PENDANT le déplacement du slider (valueChanged) ; au relâchement
du clic (sliderReleased), l'ajustement est commité automatiquement dans
entry['bytes'] (perform_remove_colors) et devient sa propre entrée
d'historique — pas de notion de "travail non validé" à conserver entre deux
affichages : il n'y a plus jamais d'état intermédiaire à committer plus tard,
contrairement au crop/straighten/texte. Conséquence : pas de contribution à
ImageViewer._has_unvalidated_work(), pas de _remove_colors_by_page, pas de
persistance/reset spécifique au changement de page au-delà de la remise à
zéro du slider lui-même.

self._sharpness_preview_img (ImageViewer, défini dans sharpness_tool_qt.py)
est RÉUTILISÉ tel quel comme champ de preview partagé pour ce mode aussi —
un seul outil actif à la fois dans la barre, donc jamais besoin d'un preview
sharpness/unsharp/brightness/saturation ET remove_colors simultané. Pas de
nouveau champ dédié.
"""

from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QSlider, QSpinBox

from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.clone_tool_qt import floating_options_panel_style


# ─────────────────────────────────────────────────────────────────────────────
# Panneau flottant de la réglette de suppression des couleurs
# ─────────────────────────────────────────────────────────────────────────────

class _RemoveColorsOptionsPanel(QWidget):
    """Panneau flottant avec la réglette d'intensité de suppression des
    couleurs (0..100), affiché sous la barre d'outils uniquement quand
    l'outil "remove_colors" est actif — même principe que
    _SaturationOptionsPanel (jamais inséré dans le layout de ImageViewer,
    indépendant du timer d'auto-masquage de la barre pour ne pas interrompre
    un réglage en cours).

    Réglette ET spinbox synchronisées (même valeur, deux façons de la
    modifier). Pendant le déplacement du slider (valueChanged) ou la frappe
    dans la spinbox : preview PIL live sans toucher entry['bytes']. Commit
    réel via RemoveColorsViewerMixin.perform_remove_colors() au relâchement du
    slider (sliderReleased) OU à la perte de focus/validation de la spinbox
    (editingFinished) — puis les deux restent sur la valeur commitée (même
    comportement que brightness, pas sharpness). Pas de bouton "Valider" pour
    cet outil (voir docstring de module).
    """

    _RANGE_MIN = 0
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
        self.setStyleSheet(floating_options_panel_style(theme, "_RemoveColorsOptionsPanel"))
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
        self._label.setText(_("viewer.remove_colors_panel_label"))
        self._label.setFont(font)
        self._spin.setFont(font)

    # ── Visibilité ────────────────────────────────────────────────────────────

    def set_visible_for_tool(self, tool_id: str | None):
        if tool_id == "remove_colors":
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
        self._viewer._update_remove_colors_preview()

    def _on_slider_pressed(self):
        # QSlider capture la souris (grab) pendant tout le drag actif — même
        # piège que _SharpnessOptionsPanel._on_slider_pressed, voir sa docstring.
        self._viewer._toolbar.pause_hide()

    def _on_slider_released(self):
        self._viewer.perform_remove_colors()

    def _on_spin_changed(self, value: int):
        self.value = value
        if self._slider.value() != value:
            self._slider.blockSignals(True)
            self._slider.setValue(value)
            self._slider.blockSignals(False)
        self._viewer._update_remove_colors_preview()

    def _on_spin_editing_finished(self):
        self._viewer.perform_remove_colors()


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état de l'outil (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class RemoveColorsCanvasMixin:
    """Hérité par _ViewerCanvas (image_viewer_qt.py) en plus de QLabel : ajoute
    l'état minimal de l'outil "remove_colors" au canvas de la visionneuse,
    sans que son code vive dans image_viewer_qt.py.

    Volontairement quasi vide : contrairement à crop/straighten/clone/texte,
    cet outil n'a aucun overlay dessiné sur le canvas ni aucun geste souris à
    intercepter — le réglage se fait entièrement via la réglette du panneau
    flottant (_RemoveColorsOptionsPanel), le preview via le pixmap affiché
    normalement par ImageViewer.display_image() — même principe que
    SharpnessCanvasMixin (sharpness), BrightnessCanvasMixin et
    SaturationCanvasMixin.
    """

    def _init_remove_colors_state(self):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — preview live + commit dans l'historique du panneau (hérité par ImageViewer)
# ─────────────────────────────────────────────────────────────────────────────

class RemoveColorsViewerMixin:
    """Hérité par ImageViewer (image_viewer_qt.py) en plus de QDialog : ajoute
    la logique de l'outil "remove_colors" au viewer, sans que son code vive
    dans image_viewer_qt.py. Suppose que l'hôte a déjà self._canvas
    (_ViewerCanvas, avec RemoveColorsCanvasMixin), self.callbacks,
    self.current_idx, self._toolbar (avec _remove_colors_panel), et
    self._sharpness_preview_img (champ de preview PARTAGÉ avec sharpness/
    unsharp/brightness/saturation, défini dans
    image_viewer_qt.py::ImageViewer.__init__ — un seul outil actif à la fois
    dans la barre, jamais besoin d'un preview simultané).
    """

    def _update_remove_colors_preview(self):
        """Régénère le pixmap affiché avec la valeur courante de la réglette,
        SANS toucher entry['bytes'] — même principe que
        SaturationViewerMixin._update_saturation_preview(), réutilise la même
        fonction de traitement (apply_adjustments) pour ne pas dupliquer la
        formule PIL (skill adjust-remove-colors).

        Le résultat est stocké dans self._sharpness_preview_img (champ
        partagé, voir docstring de classe) : _display_single_page
        (image_viewer_qt.py) l'utilise à la place de ensure_image_loaded(entry)
        quand il est défini pour la page courante."""
        from modules.qt import state as _state_module
        from modules.qt.image_processing_qt import apply_adjustments

        value = self._toolbar._remove_colors_panel.value
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
            original.copy(), {'remove_colors_intensity': value}, for_preview=True)
        self.display_image(keep_crop_rect=True)

    def perform_remove_colors(self):
        """Relâchement du slider ou validation de la spinbox : commit réel de
        la suppression des couleurs dans entry['bytes'] (pattern skill
        apply-image-operation, variante A complète) — réutilise
        apply_image_adjustments() (image_processing_qt.py), déjà utilisée
        par l'ancien panneau Ajustements pour "Appliquer à la page
        courante". Devient sa propre entrée d'historique, comme un commit de
        brightness (pas de bouton "Valider" séparé, voir docstring de module).

        Le slider/spinbox NE revient PAS à 0 après ce commit (même principe
        que perform_brightness()) : il reste sur la valeur qui vient d'être
        appliquée, pour rester une indication visuelle de l'ajustement en
        cours plutôt que de donner l'impression que le réglage a été perdu
        alors que l'image a bien changé. Un nouveau geste après ce commit
        applique un ajustement ADDITIONNEL par-dessus l'image déjà modifiée
        (le calcul repart de entry['bytes'] courant à chaque fois, pas d'un
        état "absolu" mémorisé)."""
        from modules.qt import state as _state_module
        from modules.qt.image_processing_qt import apply_image_adjustments
        from modules.qt.dialogs_qt import MsgDialog

        panel = self._toolbar._remove_colors_panel
        value = panel.value
        if value == 0:
            return

        state = self.callbacks.get('state') or _state_module.state
        canvas = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]
            apply_image_adjustments(
                [entry], {'remove_colors_intensity': value}, callbacks=self.callbacks)

            # apply_image_adjustments() vient de faire save_state(force=True)
            # en interne : state.history_index pointe maintenant sur CE
            # commit. Mémorisé sur state (PAS sur self/ImageViewer, voir
            # state.py) pour réafficher la bonne valeur sur le slider si un
            # undo/redo retombe pile sur ce point d'historique — même
            # principe que saturation_value_by_history_index.
            state.remove_colors_value_by_history_index[(self.current_idx, state.history_index)] = value

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
            # jamais pendant une suppression de couleurs), mais pour éviter
            # l'effet de bord de display_image() sans ce flag, qui appelle
            # inconditionnellement _canvas.clear_crop() — lequel remet aussi
            # pan_offset_x/y à 0 (crop_tool_qt.py::clear_crop, pensé pour
            # recentrer la vue quand on abandonne un crop). Sans ce flag, tout
            # commit après un zoom+pan recentrerait l'image sous les pieds de
            # l'utilisateur — même cause que dans
            # levels_tool_qt.py::perform_levels.
            self.display_image(keep_crop_rect=True)
            self._toolbar.refresh_undo_redo_state()
            # Le slider NE revient PAS à 0 après commit (voir docstring de
            # perform_remove_colors) — reste sur la valeur qui vient d'être
            # appliquée, même principe que perform_brightness().

        except Exception as e:
            dlg = MsgDialog(self._center_parent, "messages.errors.remove_colors_failed.title",
                            "messages.errors.remove_colors_failed.message",
                            message_kwargs={"error": str(e)})
            dlg.show_nonmodal()

    def _reset_remove_colors_preview(self):
        """Annule le preview visuel en cours (drag non relâché) et
        resynchronise le slider/spinbox sur la page COURANTE (self.current_idx,
        potentiellement déjà mise à jour par l'appelant en cas de changement
        de page) — appelé au changement de page, à la désélection de l'outil,
        et après undo/redo (_refresh_after_undo_redo). Même principe que
        _reset_brightness_preview().

        Il ne peut jamais y avoir de valeur "en attente" à committer (le
        relâchement du slider commit déjà tout, voir perform_remove_colors),
        mais le slider doit refléter la dernière valeur RÉELLEMENT commitée
        sur la page affichée, pas systématiquement 0 : state.
        remove_colors_value_by_history_index retrouve cette valeur si un
        commit remove_colors existe pour (page, history_index) courants,
        sinon 0 (page jamais ajustée à ce point d'historique)."""
        from modules.qt import state as _state_module
        self._sharpness_preview_img = None
        state = self.callbacks.get('state') or _state_module.state
        value = state.remove_colors_value_by_history_index.get(
            (self.current_idx, state.history_index))
        panel = self._toolbar._remove_colors_panel
        panel.set_value_silent(0 if value is None else value)
