"""
modules/qt/compression_tool_qt.py — Outil "compression" (qualité JPEG) de la
barre d'outils flottante de la visionneuse principale (image_viewer_qt.py).

Fusion progressive des visionneuses (idees.txt #3, 10e outil migré, 6e des 8
modes d'ajustement après sharpness/unsharp/brightness/saturation/
remove_colors) : ce module contient toute la logique propre à l'outil
"compression" — état + preview live (mixin CompressionCanvasMixin, hérité par
_ViewerCanvas), commit de l'ajustement dans l'historique du panneau (mixin
CompressionViewerMixin, hérité par ImageViewer), et le panneau flottant de la
réglette (_CompressionOptionsPanel). image_viewer_qt.py ne fait qu'hériter de
ces deux mixins et brancher l'icône de la barre d'outils — voir CLAUDE.md
règle "ne jamais migrer le code d'un outil dans image_viewer_qt.py".

Même famille de pattern que sharpness/brightness (une réglette, PAS de
bi-mode, reste sur la valeur commitée après relâchement) — MAIS avec une
différence propre à cet outil, absente des 5 précédents :

**Grisage conditionnel de l'icône** (idees.txt #3, décision explicite) :
contrairement aux 5 modes déjà migrés (toujours applicables quelle que soit
l'image), la compression JPEG n'a de sens que si la page affichée est un
JPEG/WEBP/AVIF (mêmes extensions que _has_compressible du panneau Ajustements
classique, adjustments_dialog_qt.py). L'icône de la barre (_ToolButton) est
grisée/désactivée sur toute autre page — voir _ToolButton.set_enabled_state
(viewer_toolbar_qt.py) et ImageViewer._refresh_compression_button_state(),
appelée à chaque changement de page (navigate) et à l'ouverture de la
visionneuse. Le tooltip reste actif dans les deux états, avec un texte
différent (dispo/non dispo) — voir _update_compression_tooltip() dans
viewer_toolbar_qt.py.

**Le slider reste sur la valeur CIBLE appliquée après commit, PAS de
resynchronisation EXIF** (revenu sur un premier jet, piège vécu 2026-08-15) :
un premier jet tentait de resynchroniser le slider sur
detect_jpeg_quality(entry['bytes']) après chaque commit, dans l'idée
d'afficher la qualité "réelle" de l'image plutôt qu'une valeur cible qui peut
diverger après plusieurs recompressions successives. Mais detect_jpeg_quality
a un mapping à 5 paliers bien trop grossier pour ça (prévu uniquement pour
positionner le curseur au tout premier affichage, voir sa docstring dans le
skill adjust-compression) : une compression à qualité 1 retombe dans son
premier seuil et ressort à 95, donnant l'impression trompeuse que rien n'a
été appliqué et qu'on ne peut plus rien compresser. Revenu au même principe
que sharpness/brightness : le slider reste sur la valeur cible qui vient
d'être appliquée. Voir perform_compression() et _reset_compression_preview().
detect_jpeg_quality() n'est utilisée ici que comme valeur de DÉPART (première
ouverture de l'outil sur une page, aucun commit de CET outil encore fait à ce
point d'historique) — jamais pour vérifier après coup ce qui vient d'être
appliqué.

Contrairement au crop/straighten/clone/texte, cet outil n'a AUCUN overlay
interactif ni geste souris sur le canvas : c'est une réglette avec preview
temps réel (comme AdjustmentViewerDialog::_display_image en mode
'compression'). CompressionCanvasMixin reste donc volontairement minimal
(pas de mousePress/Move/Release à gérer, pas de paint_* à appeler depuis
paintEvent) — même raison que SharpnessCanvasMixin (sharpness).

PAS de bouton "Valider" pour cet outil (même principe que sharpness/unsharp/
brightness/saturation/remove_colors, décision actée idees.txt #3) : le
preview PIL n'est visible que PENDANT le déplacement du slider (valueChanged) ;
au relâchement du clic (sliderReleased), l'ajustement est commité
automatiquement dans entry['bytes'] (perform_compression) et devient sa
propre entrée d'historique — pas de notion de "travail non validé" à
conserver entre deux affichages : il n'y a plus jamais d'état intermédiaire à
committer plus tard, contrairement au crop/straighten/texte. Conséquence :
pas de contribution à ImageViewer._has_unvalidated_work(), pas de
_compression_by_page, pas de persistance/reset spécifique au changement de
page au-delà de la resynchronisation du slider lui-même.

self._sharpness_preview_img (ImageViewer, défini dans sharpness_tool_qt.py)
est RÉUTILISÉ tel quel comme champ de preview partagé pour ce mode aussi —
un seul outil actif à la fois dans la barre, donc jamais besoin d'un preview
sharpness/unsharp/brightness/saturation/remove_colors ET compression
simultané. Pas de nouveau champ dédié.
"""

from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QSlider, QSpinBox

from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.clone_tool_qt import floating_options_panel_style

# Extensions considérées compressibles — mêmes valeurs que _has_compressible
# dans adjustments_dialog_qt.py, ne pas dupliquer une autre liste ailleurs.
COMPRESSIBLE_EXTENSIONS = (".jpg", ".jpeg", ".webp", ".avif")


def is_compressible_entry(entry: dict) -> bool:
    """True si l'extension de l'entrée est compatible avec la simulation de
    compression JPEG (apply_adjustments, image_processing_qt.py) —
    même définition que l'ancien panneau Ajustements classique (supprimé)."""
    ext = entry.get("extension", "").lower()
    return ext in COMPRESSIBLE_EXTENSIONS


# ─────────────────────────────────────────────────────────────────────────────
# Panneau flottant de la réglette de compression
# ─────────────────────────────────────────────────────────────────────────────

class _CompressionOptionsPanel(QWidget):
    """Panneau flottant avec la réglette de qualité de compression (1..100),
    affiché sous la barre d'outils uniquement quand l'outil "compression" est
    actif — même principe que _RemoveColorsOptionsPanel (jamais inséré dans
    le layout de ImageViewer, indépendant du timer d'auto-masquage de la
    barre pour ne pas interrompre un réglage en cours).

    Réglette ET spinbox synchronisées (même valeur, deux façons de la
    modifier). Pendant le déplacement du slider (valueChanged) ou la frappe
    dans la spinbox : preview PIL live sans toucher entry['bytes']. Commit
    réel via CompressionViewerMixin.perform_compression() au relâchement du
    slider (sliderReleased) OU à la perte de focus/validation de la spinbox
    (editingFinished) — puis les deux se resynchronisent sur la qualité JPEG
    RÉELLE de l'image après recompression (PAS une valeur fixe, voir
    docstring de module). Pas de bouton "Valider" pour cet outil.
    """

    _RANGE_MIN = 1
    _RANGE_MAX = 100

    def __init__(self, viewer: "ImageViewer"):
        super().__init__(viewer._canvas)
        # Sans cet attribut, un QWidget nu n'applique jamais le "background"
        # d'une stylesheet (contrairement à QLabel/QPushButton).
        from PySide6.QtCore import Qt
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._viewer = viewer
        self.value = self._RANGE_MAX

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        self._label = QLabel()
        layout.addWidget(self._label)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(self._RANGE_MIN)
        self._slider.setMaximum(self._RANGE_MAX)
        self._slider.setValue(self._RANGE_MAX)
        self._slider.setFixedWidth(160)
        self._slider.valueChanged.connect(self._on_slider_changed)
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        layout.addWidget(self._slider)

        self._spin = QSpinBox()
        self._spin.setRange(self._RANGE_MIN, self._RANGE_MAX)
        self._spin.setValue(self._RANGE_MAX)
        self._spin.setFixedWidth(62)
        self._spin.valueChanged.connect(self._on_spin_changed)
        self._spin.editingFinished.connect(self._on_spin_editing_finished)
        layout.addWidget(self._spin)

        self.hide()

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(floating_options_panel_style(theme, "_CompressionOptionsPanel"))
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
        self._label.setText(_("viewer.compression_panel_label"))
        self._label.setFont(font)
        self._spin.setFont(font)

    # ── Visibilité ────────────────────────────────────────────────────────────

    def set_visible_for_tool(self, tool_id: str | None):
        if tool_id == "compression":
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

    def set_value_silent(self, value: int):
        """Positionne slider+spinbox sur value sans redéclencher preview ni
        commit (blockSignals) — utilisé par la resynchronisation EXIF (voir
        docstring de module) et par la restauration après undo/redo
        (ImageViewer._refresh_after_undo_redo)."""
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
        self._viewer._update_compression_preview()

    def _on_slider_pressed(self):
        # QSlider capture la souris (grab) pendant tout le drag actif — même
        # piège que _SharpnessOptionsPanel._on_slider_pressed, voir sa docstring.
        self._viewer._toolbar.pause_hide()

    def _on_slider_released(self):
        self._viewer.perform_compression()

    def _on_spin_changed(self, value: int):
        self.value = value
        if self._slider.value() != value:
            self._slider.blockSignals(True)
            self._slider.setValue(value)
            self._slider.blockSignals(False)
        self._viewer._update_compression_preview()

    def _on_spin_editing_finished(self):
        self._viewer.perform_compression()


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état de l'outil (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class CompressionCanvasMixin:
    """Hérité par _ViewerCanvas (image_viewer_qt.py) en plus de QLabel : ajoute
    l'état minimal de l'outil "compression" au canvas de la visionneuse, sans
    que son code vive dans image_viewer_qt.py.

    Volontairement quasi vide : contrairement à crop/straighten/clone/texte,
    cet outil n'a aucun overlay dessiné sur le canvas ni aucun geste souris à
    intercepter — le réglage se fait entièrement via la réglette du panneau
    flottant (_CompressionOptionsPanel), le preview via le pixmap affiché
    normalement par ImageViewer.display_image() — même principe que
    SharpnessCanvasMixin (sharpness), BrightnessCanvasMixin, etc.
    """

    def _init_compression_state(self):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — preview live + commit dans l'historique du panneau (hérité par ImageViewer)
# ─────────────────────────────────────────────────────────────────────────────

class CompressionViewerMixin:
    """Hérité par ImageViewer (image_viewer_qt.py) en plus de QDialog : ajoute
    la logique de l'outil "compression" au viewer, sans que son code vive
    dans image_viewer_qt.py. Suppose que l'hôte a déjà self._canvas
    (_ViewerCanvas, avec CompressionCanvasMixin), self.callbacks,
    self.current_idx, self._toolbar (avec _compression_panel), et
    self._sharpness_preview_img (champ de preview PARTAGÉ avec sharpness/
    unsharp/brightness/saturation/remove_colors, défini dans
    image_viewer_qt.py::ImageViewer.__init__ — un seul outil actif à la fois
    dans la barre, jamais besoin d'un preview simultané).
    """

    def _update_compression_preview(self):
        """Régénère le pixmap affiché avec la valeur courante de la réglette,
        SANS toucher entry['bytes'] — même principe que
        RemoveColorsViewerMixin._update_remove_colors_preview(), réutilise la
        même fonction de traitement (apply_adjustments) pour ne pas dupliquer
        la logique de simulation JPEG (skill adjust-compression).

        Le résultat est stocké dans self._sharpness_preview_img (champ
        partagé, voir docstring de classe) : _display_single_page
        (image_viewer_qt.py) l'utilise à la place de ensure_image_loaded(entry)
        quand il est défini pour la page courante."""
        from modules.qt import state as _state_module
        from modules.qt.image_processing_qt import apply_adjustments

        value = self._toolbar._compression_panel.value
        state = self.callbacks.get('state') or _state_module.state
        entry = state.images_data[self.current_idx]
        if not entry.get('bytes') or not is_compressible_entry(entry):
            return
        if value >= 100:
            self._sharpness_preview_img = None
            self.display_image(keep_crop_rect=True)
            return

        import io
        from PIL import Image
        original = Image.open(io.BytesIO(entry['bytes']))
        self._sharpness_preview_img = apply_adjustments(
            original.copy(), {'compression_quality': value}, for_preview=True)
        self.display_image(keep_crop_rect=True)

    def perform_compression(self):
        """Relâchement du slider ou validation de la spinbox : commit réel de
        la compression dans entry['bytes'] (pattern skill
        apply-image-operation, variante A complète) — réutilise
        apply_image_adjustments() (image_processing_qt.py), déjà
        utilisée par l'ancien panneau Ajustements pour "Appliquer à la page
        courante". Devient sa propre entrée d'historique, comme un commit de
        remove_colors (pas de bouton "Valider" séparé, voir docstring de
        module).

        Le slider/spinbox NE revient PAS à une valeur fixe après ce commit
        (même principe que perform_sharpness()/perform_brightness()) : il
        reste sur la valeur CIBLE qui vient d'être appliquée, pour rester une
        indication visuelle fidèle du geste effectué. **Ne PAS resynchroniser
        sur detect_jpeg_quality(entry['bytes']) après un commit** (piège
        vécu, 2026-08-15) : cette fonction a un mapping à 5 paliers bien trop
        grossier pour être fiable en sortie de compression (ex. qualité 1
        réellement appliquée retombe dans son premier seuil et ressort à 95,
        laissant croire que rien n'a été appliqué) — sa docstring dans le
        skill adjust-compression prévient déjà qu'elle ne sert qu'à
        positionner le curseur au tout premier affichage, pas à vérifier une
        compression qui vient d'avoir lieu. Un nouveau geste après ce commit
        recompresse à nouveau l'image déjà modifiée (le calcul repart de
        entry['bytes'] courant, pas d'un état "absolu" mémorisé) — comportement
        accepté explicitement, cohérent avec sharpness/unsharp/brightness."""
        from modules.qt import state as _state_module
        from modules.qt.image_processing_qt import apply_image_adjustments
        from modules.qt.dialogs_qt import MsgDialog

        panel = self._toolbar._compression_panel
        value = panel.value

        state = self.callbacks.get('state') or _state_module.state
        canvas = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]
            if not is_compressible_entry(entry) or value >= 100:
                return
            apply_image_adjustments([entry], {'compression_quality': value}, callbacks=self.callbacks)

            # apply_image_adjustments() vient de faire save_state(force=True)
            # en interne : state.history_index pointe maintenant sur CE
            # commit. Mémorisé sur state (PAS sur self/ImageViewer, voir
            # state.py) pour réafficher la bonne valeur sur le slider si un
            # undo/redo retombe pile sur ce point d'historique — même
            # principe que sharpness_value_by_history_index. La valeur
            # mémorisée est la valeur CIBLE demandée (pas une redétection
            # EXIF, voir docstring ci-dessus).
            state.compression_value_by_history_index[(self.current_idx, state.history_index)] = value

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
            # jamais pendant une compression), mais pour éviter l'effet de
            # bord de display_image() sans ce flag, qui appelle
            # inconditionnellement _canvas.clear_crop() — lequel remet aussi
            # pan_offset_x/y à 0 (crop_tool_qt.py::clear_crop, pensé pour
            # recentrer la vue quand on abandonne un crop). Sans ce flag, tout
            # commit après un zoom+pan recentrait l'image sous les pieds de
            # l'utilisateur (bug diagnostiqué sur levels, 2026-08-15, même
            # cause ici — voir levels_tool_qt.py::perform_levels).
            self.display_image(keep_crop_rect=True)
            self._toolbar.refresh_undo_redo_state()
            # Le slider NE revient PAS à une valeur fixe après commit (voir
            # docstring ci-dessus) — reste sur la valeur qui vient d'être
            # appliquée (panel.value déjà à jour, rien à repositionner).

        except Exception as e:
            dlg = MsgDialog(self._center_parent, "messages.errors.compression_failed.title",
                            "messages.errors.compression_failed.message",
                            message_kwargs={"error": str(e)})
            dlg.show_nonmodal()

    def _reset_compression_preview(self):
        """Annule le preview visuel en cours (drag non relâché) et
        resynchronise le slider/spinbox sur la page COURANTE (self.
        current_idx, potentiellement déjà mise à jour par l'appelant en cas
        de changement de page) — appelé au changement de page, à la
        désélection de l'outil, à l'ouverture initiale de la visionneuse, et
        après undo/redo (_refresh_after_undo_redo). Même principe que
        _reset_sharpness_preview() : state.compression_value_by_history_index
        retrouve la valeur CIBLE commitée par CET outil sur (page,
        history_index) courants (pas une redétection EXIF, voir docstring de
        perform_compression — piège vécu 2026-08-15) ; à défaut (page jamais
        compressée par cet outil à ce point d'historique — première
        ouverture, ou page/point d'historique où seul un AUTRE mécanisme a pu
        modifier le fichier), retombe sur detect_jpeg_quality(entry['bytes'])
        de l'image telle qu'elle est actuellement, pour partir d'un point de
        départ sensé plutôt que d'un défaut arbitraire comme 100."""
        from modules.qt import state as _state_module
        from modules.qt.image_processing_qt import detect_jpeg_quality

        self._sharpness_preview_img = None
        state = self.callbacks.get('state') or _state_module.state
        panel = self._toolbar._compression_panel

        if not (0 <= self.current_idx < len(state.images_data)):
            return
        entry = state.images_data[self.current_idx]
        if not is_compressible_entry(entry) or not entry.get('bytes'):
            return

        value = state.compression_value_by_history_index.get((self.current_idx, state.history_index))
        if value is None:
            value = detect_jpeg_quality(entry['bytes']) or 100
        panel.set_value_silent(value)
