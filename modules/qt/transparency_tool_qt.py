"""
modules/qt/transparency_tool_qt.py — Outil "transparence" de la barre
d'outils flottante de la visionneuse principale (image_viewer_qt.py).

Fusion progressive des visionneuses (idees.txt #3, 13e outil migré, 8e et
DERNIER des 8 modes d'ajustement après sharpness/unsharp/brightness/
saturation/remove_colors/compression/levels) : ce module contient toute la
logique propre à l'outil "transparency" — état + geste souris de la pipette
(mixin TransparencyCanvasMixin, hérité par _ViewerCanvas), commit de
l'opération dans l'historique du panneau (mixin TransparencyViewerMixin,
hérité par ImageViewer), et le panneau flottant des contrôles
(_TransparencyOptionsPanel). image_viewer_qt.py ne fait qu'hériter de ces
deux mixins et brancher l'icône de la barre d'outils — voir CLAUDE.md règle
"ne jamais migrer le code d'un outil dans image_viewer_qt.py".

Une fois cet outil migré, AdjustmentViewerDialog/adjustments_viewers_qt.py
n'héberge plus aucun mode — voir skill viewers, "Le mode restant de
AdjustmentViewerDialog".

Contrairement aux 6 modes preview-slider purs (sharpness/brightness/
saturation/remove_colors/compression) : cet outil a un VRAI geste souris
comme crop/straighten/clone/texte/levels — une pipette qui capte un clic sur
l'image. Contrairement à levels (commit immédiat par clic, pas de bouton
"Valider") : cet outil ACCUMULE plusieurs clics dans une image de travail en
mémoire (comme l'ancien mode 'transparency' de AdjustmentViewerDialog, skill
adjust-transparency) avant validation explicite — décision utilisateur
2026-08-15, plus proche du pattern crop/straighten/text/shapes (bouton
"Valider" partagé, persistance du travail non validé par page) que du
pattern levels.

Panneau flottant à une seule ligne (idees.txt #3, décision 2026-08-15) :
  bascule flood/global | réglette+spin tolérance || bouton pipette
Réutilise à l'identique la logique PIL de _apply_transparency_click (skill
adjust-transparency, ancien AdjustmentViewerDialog) : flood fill 4-connexe en
pile (pas récursif) ou balayage global de toute l'image, tolérance testée en
distance de Chebyshev sur les 3 canaux RGB, seul le canal alpha est modifié
(RGB jamais touché).

Image de travail par page (self._transp_work_img_by_page, ImageViewer) : un
clic pipette mute une COPIE RGBA de l'image en mémoire, jamais entry['bytes']
directement — la page affichée montre cette image de travail tant que
l'outil est actif sur cette page (même principe que le preview PIL des
modes preview-slider, mais accumulatif au lieu d'être remplacé à chaque
geste). Validation ("Valider", bouton partagé crop/straighten/text/shapes/
transparency) : perform_transparency() écrit l'image de travail dans
entry['bytes'] (save_image_to_bytes, format selon l'extension d'origine —
PNG/WEBP/AVIF/ICO), fait save_state(), vide l'image de travail de cette page.

Échap/Suppr/Retour arrière (idees.txt #3, décision utilisateur 2026-08-15) :
annule TOUT le travail en attente d'un coup (retour à l'image d'origine),
PAS un undo clic par clic — pas de pile d'annulation locale comme l'ancien
_transp_history de AdjustmentViewerDialog.

Formats supportés — PNG/WEBP/ICO/AVIF uniquement (un canal alpha est requis) :
même filtrage que l'ancien mode, voir _SUPPORTED_EXTS ci-dessous (dupliqué
nulle part ailleurs, une seule constante, même nom que l'ancien
adjustments_viewers_qt.py pour rester repérable). Icône de la barre grisée/
désactivée sur une page d'un autre format — même mécanisme que compression
(seul autre outil migré grisable, voir compression_tool_qt.py::
is_compressible_entry et _ToolButton.set_enabled_state), pas un nouveau
mécanisme.
"""

import io

from PIL import Image

from PySide6.QtWidgets import (
    QWidget, QLabel, QHBoxLayout, QSlider, QSpinBox, QFrame,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor, QPixmap

from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.clone_tool_qt import floating_options_panel_style


# Formats supportés — un canal alpha est requis, mêmes extensions que
# l'ancien mode 'transparency' de AdjustmentViewerDialog (skill
# adjust-transparency, _SUPPORTED_EXTS).
_SUPPORTED_EXTS = {'.png', '.webp', '.ico', '.avif'}


def is_transparency_supported_entry(entry: dict) -> bool:
    """True si l'extension de l'entrée peut porter un canal alpha — même
    définition que l'ancien mode 'transparency' de AdjustmentViewerDialog."""
    ext = entry.get("extension", "").lower()
    return ext in _SUPPORTED_EXTS


# ─────────────────────────────────────────────────────────────────────────────
# Panneau flottant : bascule flood/global, réglette de tolérance, pipette
# ─────────────────────────────────────────────────────────────────────────────

class _ClickableLabel(QLabel):
    """QLabel qui déclenche un callback au clic gauche — utilisé pour les
    labels "Zone"/"Global" de la bascule flood/global (2026-08-15, retour
    utilisateur : viser la petite poignée d'un slider 2 positions de 40px
    était jugé "particulièrement dérangeant"). Le clic sur le label bascule
    directement, sans avoir à viser le slider lui-même."""

    def __init__(self, on_click):
        super().__init__()
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_click()
        event.accept()


class _TransparencyOptionsPanel(QWidget):
    """Panneau flottant à une seule ligne (idees.txt #3, décision 2026-08-15) :
    label+mini-slider bascule flood/global | label+slider+spin tolérance ||
    séparateur | bouton pipette. Plus simple que _LevelsOptionsPanel (une
    seule pipette, pas de triplet noir/gamma/blanc à gérer) — tient sur une
    seule ligne contrairement à lui.

    Même principe que les autres panneaux flottants (jamais inséré dans le
    layout de ImageViewer, indépendant du timer d'auto-masquage de la barre
    pour ne pas interrompre un réglage en cours).

    La bascule flood/global est un QSlider 2 positions (0=flood, 1=global),
    pas une checkbox — même widget que l'ancien _transp_type_slider de
    AdjustmentViewerDialog (skill adjust-transparency), avec le label de
    l'état inactif grisé (_update_type_labels).

    PAS de bouton pipette (contrairement à levels, qui en a 2 à choisir
    parmi plusieurs gestes possibles) : dans cet outil, le clic sur l'image
    EST le seul geste possible — la pipette est donc TOUJOURS active dès que
    l'outil "transparency" est sélectionné dans la barre, sans bouton à
    armer (curseur custom posé directement par set_visible_for_tool). Un
    clic sur l'image déclenche TransparencyCanvasMixin.
    transparency_pipette_click, qui mute l'image de travail de la page
    courante (accumulatif, PAS de commit immédiat contrairement à levels —
    voir docstring de module)."""

    _TOL_MIN, _TOL_MAX = 0, 255

    def __init__(self, viewer: "ImageViewer"):
        super().__init__(viewer._canvas)
        # Sans cet attribut, un QWidget nu n'applique jamais le "background"
        # d'une stylesheet (contrairement à QLabel/QPushButton).
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._viewer = viewer

        self.transparency_type = "flood"  # "flood" | "global"
        self.tolerance = 30

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        from modules.qt.font_loader import resource_path

        # Curseur pipette custom — même construction que
        # _LevelsOptionsPanel._build_pipette_cursor (croix de visée évidée,
        # hotspot au point de capture réel, voir sa docstring pour le détail
        # des pièges déjà corrigés qu'il ne faut pas réintroduire ici).
        self._cursor_pipette = None
        pipette_path = resource_path("icons/pipette_noire.png")
        pix = QPixmap(pipette_path)
        if not pix.isNull():
            icon_scaled = pix.scaled(
                36, 36, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            self._cursor_pipette = self._build_pipette_cursor(icon_scaled)

        # ── Bascule flood/global ────────────────────────────────────────────
        # Labels cliquables en plus du slider (2026-08-15, retour utilisateur
        # : cliquer précisément sur la petite poignée d'un slider 2 positions
        # de 40px était jugé "particulièrement dérangeant") — cliquer le
        # label du côté voulu bascule directement le slider sur cette valeur,
        # sans avoir à viser la poignée elle-même. Le slider par glisser reste
        # utilisable en plus (pas retiré).
        self._flood_label = _ClickableLabel(lambda: self._type_slider.setValue(0))
        layout.addWidget(self._flood_label)
        self._type_slider = QSlider(Qt.Orientation.Horizontal)
        self._type_slider.setMinimum(0)
        self._type_slider.setMaximum(1)
        self._type_slider.setValue(0)
        self._type_slider.setFixedWidth(40)
        self._type_slider.valueChanged.connect(self._on_type_changed)
        layout.addWidget(self._type_slider)
        self._global_label = _ClickableLabel(lambda: self._type_slider.setValue(1))
        layout.addWidget(self._global_label)

        self._separator1 = QFrame()
        self._separator1.setFrameShape(QFrame.VLine)
        layout.addWidget(self._separator1)

        # ── Tolérance ────────────────────────────────────────────────────────
        self._tolerance_label = QLabel()
        layout.addWidget(self._tolerance_label)
        self._tolerance_slider = QSlider(Qt.Orientation.Horizontal)
        self._tolerance_slider.setMinimum(self._TOL_MIN)
        self._tolerance_slider.setMaximum(self._TOL_MAX)
        self._tolerance_slider.setValue(30)
        self._tolerance_slider.setFixedWidth(100)
        self._tolerance_slider.valueChanged.connect(self._on_tolerance_slider_changed)
        self._tolerance_slider.sliderPressed.connect(self._on_slider_pressed)
        layout.addWidget(self._tolerance_slider)
        self._tolerance_spin = QSpinBox()
        self._tolerance_spin.setRange(self._TOL_MIN, self._TOL_MAX)
        self._tolerance_spin.setValue(30)
        self._tolerance_spin.setFixedWidth(62)
        self._tolerance_spin.valueChanged.connect(self._on_tolerance_spin_changed)
        layout.addWidget(self._tolerance_spin)

        self.hide()

    @staticmethod
    def _build_pipette_cursor(icon_pixmap: QPixmap) -> QCursor:
        """Même construction que _LevelsOptionsPanel._build_pipette_cursor
        (icons 36×36 composée sur une toile 56×56, croix de visée évidée,
        hotspot au point de capture réel) — voir sa docstring pour le détail
        des 2 pièges déjà vécus et corrigés (hotspot à (0,0) qui ratait le
        pixel visé, croix pleine qui masquait le pixel visé) : ne pas les
        réintroduire ici."""
        from PySide6.QtGui import QPainter, QPen, QColor

        canvas_size = 56
        canvas = QPixmap(canvas_size, canvas_size)
        canvas.fill(Qt.transparent)

        margin = 20
        cross_x = margin
        cross_y = canvas_size - margin

        gap_to_icon = 4
        icon_x = cross_x + gap_to_icon
        icon_y = cross_y - gap_to_icon - icon_pixmap.height()

        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.drawPixmap(icon_x, icon_y, icon_pixmap)

        arm = 9
        gap = 4
        pen_white = QPen(QColor(255, 255, 255, 230), 3)
        pen_black = QPen(QColor(0, 0, 0, 230), 1)
        for pen in (pen_white, pen_black):
            painter.setPen(pen)
            painter.drawLine(cross_x - arm, cross_y, cross_x - gap, cross_y)
            painter.drawLine(cross_x + gap, cross_y, cross_x + arm, cross_y)
            painter.drawLine(cross_x, cross_y - arm, cross_x, cross_y - gap)
            painter.drawLine(cross_x, cross_y + gap, cross_x, cross_y + arm)
        painter.end()

        return QCursor(canvas, cross_x, cross_y)

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(floating_options_panel_style(theme, "_TransparencyOptionsPanel"))
        for label in (self._flood_label, self._global_label, self._tolerance_label):
            label.setStyleSheet(f"color: {theme['text']}; background: transparent;")
        self._separator1.setStyleSheet(f"color: {theme['separator']};")
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
        self._type_slider.setStyleSheet(slider_style)
        self._tolerance_slider.setStyleSheet(slider_style)
        self._tolerance_spin.setStyleSheet(spin_style)
        self._update_type_labels()

    def retranslate(self):
        font = _get_current_font(11)
        self._flood_label.setText(_("viewer.transparency_flood_panel_label"))
        self._flood_label.setFont(font)
        self._global_label.setText(_("viewer.transparency_global_panel_label"))
        self._global_label.setFont(font)
        self._tolerance_label.setText(_("viewer.transparency_tolerance_panel_label"))
        self._tolerance_label.setFont(font)
        self._tolerance_spin.setFont(font)
        self._update_type_labels()

        # Tooltips (OverlayTooltip obligatoire, skill qt-tooltips — jamais
        # setToolTip() natif) : réutilise l'instance déjà créée par la barre
        # principale, même principe que les autres panneaux de cette barre.
        tip = self._viewer._toolbar._overlay_tip
        for widget in (self._flood_label, self._global_label, self._type_slider):
            tip.track(widget, _("viewer.transparency_type_slider_tooltip"))
        for widget in (self._tolerance_label, self._tolerance_slider, self._tolerance_spin):
            tip.track(widget, _("viewer.transparency_tolerance_slider_tooltip"))

    def _update_type_labels(self):
        theme = get_current_theme()
        active = f"color: {theme['text']}; background: transparent; font-weight: bold;"
        inactive = f"color: {theme['separator']}; background: transparent;"
        if self.transparency_type == "flood":
            self._flood_label.setStyleSheet(active)
            self._global_label.setStyleSheet(inactive)
        else:
            self._flood_label.setStyleSheet(inactive)
            self._global_label.setStyleSheet(active)

    # ── Visibilité ────────────────────────────────────────────────────────────

    def set_visible_for_tool(self, tool_id: str | None):
        if tool_id == "transparency":
            self.show()
            self.reposition()
            self.raise_()
            # Pas de bouton pipette : le clic sur l'image est le seul geste
            # possible de cet outil, donc le curseur pipette est posé
            # directement dès la sélection de l'outil, sans étape d'armement
            # intermédiaire (contrairement à levels, qui doit choisir entre
            # plusieurs gestes).
            self._viewer._canvas.setCursor(self._cursor_pipette or Qt.CrossCursor)
        else:
            self.hide()
            self._viewer._canvas.setCursor(Qt.ArrowCursor)

    def reposition(self):
        self.adjustSize()
        canvas = self._viewer._canvas
        x = (canvas.width() - self.width()) // 2
        y = 8 + self._viewer._toolbar.height() + 6
        self.move(max(0, x), y)

    def mousePressEvent(self, event):
        # Piège corrigé (2026-08-15) : sans ce blindage, un clic sur une zone
        # vide du panneau (marges entre les widgets, pas absorbée par un
        # QSlider/QSpinBox enfant) "fuit" vers _ViewerCanvas en dessous
        # (widget flottant enfant du canvas) et déclenchait un clic pipette
        # sur l'image affichée dessous — même piège déjà documenté pour
        # _ToolButton/_ActionButton/_ViewerToolbar (skill viewers), jamais
        # reproduit ici puisque c'est le seul panneau flottant de cette barre
        # posé directement au-dessus d'un outil qui capte un clic sur l'image.
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def enterEvent(self, event):
        self._viewer._toolbar.pause_hide()
        # Le curseur pipette est celui du CANVAS (posé par set_visible_for_tool
        # tant que l'outil est actif), pas celui de ce panneau — sans ce
        # reset, la pipette restait affichée par-dessus les contrôles du
        # panneau (slider de tolérance, bascule flood/global), donnant
        # l'impression trompeuse qu'un clic dessus rendrait aussi transparent.
        self.setCursor(Qt.ArrowCursor)

    def leaveEvent(self, event):
        # Voir _LevelsOptionsPanel.leaveEvent : Qt peut envoyer un Leave au
        # panneau parent en transitant entre deux widgets enfants même quand
        # la souris reste visuellement dedans — revérification différée à 0ms.
        QTimer.singleShot(0, self._check_really_left)

    def _check_really_left(self):
        really_left = not self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        if really_left:
            self._viewer._toolbar.resume_hide()
            # setCursor(ArrowCursor) posé dans enterEvent ne s'applique qu'à
            # ce panneau et ses enfants — aucun reset à faire ici pour le
            # canvas : Qt réaffiche de lui-même le curseur déjà posé dessus
            # (le curseur pipette, tant que l'outil "transparency" reste
            # actif) dès que la souris repasse physiquement au-dessus.
            self.unsetCursor()

    # ── Réglages ─────────────────────────────────────────────────────────────

    def reset(self):
        self._type_slider.blockSignals(True)
        self._type_slider.setValue(0)
        self._type_slider.blockSignals(False)
        self._tolerance_slider.blockSignals(True)
        self._tolerance_slider.setValue(30)
        self._tolerance_slider.blockSignals(False)
        self._tolerance_spin.blockSignals(True)
        self._tolerance_spin.setValue(30)
        self._tolerance_spin.blockSignals(False)
        self.transparency_type = "flood"
        self.tolerance = 30
        self._update_type_labels()

    def _on_type_changed(self, value: int):
        self.transparency_type = "flood" if value == 0 else "global"
        self._update_type_labels()

    def _on_tolerance_slider_changed(self, value: int):
        self.tolerance = value
        if self._tolerance_spin.value() != value:
            self._tolerance_spin.blockSignals(True)
            self._tolerance_spin.setValue(value)
            self._tolerance_spin.blockSignals(False)

    def _on_tolerance_spin_changed(self, value: int):
        self.tolerance = value
        if self._tolerance_slider.value() != value:
            self._tolerance_slider.blockSignals(True)
            self._tolerance_slider.setValue(value)
            self._tolerance_slider.blockSignals(False)

    def _on_slider_pressed(self):
        # QSlider capture la souris (grab) pendant tout le drag actif — même
        # piège que _LevelsOptionsPanel._on_slider_pressed, voir sa docstring.
        self._viewer._toolbar.pause_hide()


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état et geste souris de la pipette (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class TransparencyCanvasMixin:
    """Hérité par _ViewerCanvas (image_viewer_qt.py) en plus de QLabel : ajoute
    l'état et le geste souris de la pipette de l'outil "transparency" au
    canvas de la visionneuse, sans que leur code vive dans image_viewer_qt.py.

    Comme LevelsCanvasMixin : un clic à intercepter, mais aucun overlay à
    dessiner (le clic mute l'image de travail et ressort, rien à peindre
    dans paintEvent) — contrairement à crop/straighten/clone/texte/formes.
    """

    def _init_transparency_state(self):
        pass

    def transparency_pipette_click(self, event):
        """Appelé depuis _ViewerCanvas.mousePressEvent quand l'outil
        "transparency" est actif — pas de pipette à armer au préalable
        (contrairement à levels) : le clic sur l'image est le seul geste
        possible de cet outil, donc directement pris en compte. Convertit la
        position écran en coordonnées image (même calcul que
        levels_pipette_click), puis délègue la logique de flood fill /
        global à TransparencyViewerMixin.apply_transparency_click — la
        mutation de l'image de travail par page vit côté ImageViewer
        (self._transp_work_img_by_page), pas ici."""
        pos = event.position().toPoint()
        if self.display_width <= 0 or self.display_height <= 0:
            return
        img_x = int((pos.x() - self.display_offset_x) * self._viewer._transp_work_img_size()[0] / self.display_width)
        img_y = int((pos.y() - self.display_offset_y) * self._viewer._transp_work_img_size()[1] / self.display_height)
        self._viewer.apply_transparency_click(img_x, img_y)


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — image de travail par page + commit dans l'historique du panneau
# ─────────────────────────────────────────────────────────────────────────────

class TransparencyViewerMixin:
    """Hérité par ImageViewer (image_viewer_qt.py) en plus de QDialog : ajoute
    la logique de l'outil "transparency" au viewer, sans que son code vive
    dans image_viewer_qt.py. Suppose que l'hôte a déjà self._canvas
    (_ViewerCanvas, avec TransparencyCanvasMixin), self.callbacks,
    self.current_idx, self._toolbar (avec _transparency_panel), et
    self._transp_work_img_by_page (dict[int, Image.Image], persistance par
    page de l'image de travail RGBA accumulée — défini dans
    image_viewer_qt.py::ImageViewer.__init__, même famille que
    self._crop_by_page/self._shapes_by_page).

    Contrairement aux modes preview-slider et à levels : l'image de travail
    ACCUMULE plusieurs clics avant validation (bouton "Valider" partagé, voir
    _ALWAYS_VISIBLE_VALIDATE_TOOLS), pas un commit immédiat par geste — décision
    utilisateur 2026-08-15 (idees.txt #3), reproduisant le comportement de
    l'ancien mode 'transparency' de AdjustmentViewerDialog (skill
    adjust-transparency, image de travail + bouton "Appliquer").
    """

    def _transp_work_img_size(self) -> tuple[int, int]:
        """Dimensions de l'image de travail de la page courante — l'image de
        travail existante si un clic a déjà eu lieu sur cette page, sinon la
        taille de entry['bytes'] tel quel. Utilisé par
        TransparencyCanvasMixin.transparency_pipette_click pour la conversion
        écran → image (même rôle que original_img.size dans
        levels_pipette_click, mais l'image de référence peut déjà être la
        version RGBA de travail si des clics précédents existent)."""
        from modules.qt import state as _state_module
        work_img = self._transp_work_img_by_page.get(self.current_idx)
        if work_img is not None:
            return work_img.size
        state = self.callbacks.get('state') or _state_module.state
        entry = state.images_data[self.current_idx]
        img = Image.open(io.BytesIO(entry['bytes']))
        return img.size

    def _get_or_init_transp_work_img(self):
        """Retourne l'image de travail RGBA de la page courante, l'initialise
        depuis entry['bytes'] si elle n'existe pas encore (première mutation
        de cette page depuis l'ouverture de l'outil) — même principe que
        l'ancien _transp_work_img de AdjustmentViewerDialog (skill
        adjust-transparency, initialisation "une seule fois au premier
        affichage de chaque page")."""
        from modules.qt import state as _state_module
        work_img = self._transp_work_img_by_page.get(self.current_idx)
        if work_img is not None:
            return work_img
        state = self.callbacks.get('state') or _state_module.state
        entry = state.images_data[self.current_idx]
        original = Image.open(io.BytesIO(entry['bytes']))
        work_img = original.convert('RGBA')
        self._transp_work_img_by_page[self.current_idx] = work_img
        return work_img

    def apply_transparency_click(self, px: int, py: int):
        """Cœur de la logique — reprend à l'identique
        _apply_transparency_click de l'ancien AdjustmentViewerDialog (skill
        adjust-transparency) : flood fill 4-connexe en pile (pas récursif,
        évite un dépassement de pile Python sur de grandes zones) ou balayage
        global de toute l'image, tolérance testée en distance de Chebyshev
        (max des 3 deltas RGB), seul le canal alpha est modifié (RGB jamais
        touché — la couleur d'origine reste techniquement présente sous la
        transparence)."""
        work_img = self._get_or_init_transp_work_img()
        img_w, img_h = work_img.size
        if px < 0 or py < 0 or px >= img_w or py >= img_h:
            return

        pixels = work_img.load()
        ref = pixels[px, py]
        if ref[3] == 0:
            return

        panel = self._toolbar._transparency_panel
        tol = panel.tolerance

        def _in_tolerance(c):
            return (abs(c[0] - ref[0]) <= tol and
                    abs(c[1] - ref[1]) <= tol and
                    abs(c[2] - ref[2]) <= tol)

        if panel.transparency_type == "global":
            for y in range(img_h):
                for x in range(img_w):
                    c = pixels[x, y]
                    if c[3] != 0 and _in_tolerance(c):
                        pixels[x, y] = (c[0], c[1], c[2], 0)
        else:
            stack = [(px, py)]
            visited = set()
            while stack:
                cx, cy = stack.pop()
                if (cx, cy) in visited:
                    continue
                if cx < 0 or cy < 0 or cx >= img_w or cy >= img_h:
                    continue
                visited.add((cx, cy))
                c = pixels[cx, cy]
                if c[3] == 0 or not _in_tolerance(c):
                    continue
                pixels[cx, cy] = (c[0], c[1], c[2], 0)
                stack.extend([(cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)])

        self._update_transparency_preview()
        self._canvas._update_validate_btn_state()
        self._canvas._update_cancel_btn_state()

    def _update_transparency_preview(self):
        """Affiche l'image de travail courante (accumulée, pas encore
        commitée) — même rôle que _update_levels_preview mais sans recalcul
        PIL à partir de paramètres numériques : l'image de travail EST déjà
        le résultat visuel voulu, on l'affiche telle quelle."""
        self._sharpness_preview_img = self._transp_work_img_by_page.get(self.current_idx)
        self.display_image(keep_crop_rect=True)

    def validate_transparency(self):
        from modules.qt.dialogs_qt import MsgDialog
        if self.current_idx not in self._transp_work_img_by_page:
            dlg = MsgDialog(
                self,
                "messages.warnings.no_transparency_work.title",
                "messages.warnings.no_transparency_work.message",
            )
            dlg.show_nonmodal()
            return
        self.perform_transparency()

    def perform_transparency(self):
        """Bouton "Valider" : commit réel de l'image de travail dans
        entry['bytes'] (pattern skill apply-image-operation) — sauvegarde au
        format selon l'extension d'origine (ICO/WEBP/AVIF/PNG), même
        correspondance que l'ancien _apply_transparency (skill
        adjust-transparency). Devient sa propre entrée d'historique."""
        from modules.qt import state as _state_module
        from modules.qt.entries import save_image_to_bytes
        from modules.qt.dialogs_qt import MsgDialog

        work_img = self._transp_work_img_by_page.get(self.current_idx)
        if work_img is None:
            return

        state = self.callbacks.get('state') or _state_module.state
        save_state = self.callbacks.get("save_state")
        render_mosaic = self.callbacks.get("render_mosaic")
        update_btn = self.callbacks.get("update_button_text")
        canvas_cb = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]

            if save_state:
                save_state()

            entry["img"] = work_img.copy()
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

            self._transp_work_img_by_page.pop(self.current_idx, None)
            self._sharpness_preview_img = None
            self._canvas._update_validate_btn_state()
            self._canvas._update_cancel_btn_state()
            self.display_image(keep_crop_rect=True)
            self._toolbar.refresh_undo_redo_state()

        except Exception:
            dlg = MsgDialog(self, "messages.errors.transparency_failed.title",
                            "messages.errors.transparency_failed.message")
            dlg.show_nonmodal()

    def _clear_transparency_work(self):
        """Échap/Suppr/Retour arrière/bouton "Annuler" : annule TOUT le
        travail en attente d'un coup pour la page courante (idees.txt #3,
        décision explicite utilisateur 2026-08-15) — pas un undo clic par
        clic. Retour à l'image d'origine (entry['bytes'] inchangé, l'image
        de travail est simplement jetée)."""
        self._transp_work_img_by_page.pop(self.current_idx, None)
        self._sharpness_preview_img = None
        self._canvas._update_validate_btn_state()
        self._canvas._update_cancel_btn_state()
        self.display_image(keep_crop_rect=True)

    # ── Persistance par page ─────────────────────────────────────────────────

    def _save_transparency_for_current_page(self):
        """L'image de travail (self._transp_work_img_by_page) EST déjà
        indexée par page — rien à recopier au changement de page,
        contrairement à _save_crop_for_current_page (qui doit extraire l'état
        du canvas). No-op délibéré, gardé pour cohérence de signature avec
        les autres _save_*_for_current_page appelés depuis navigate()."""
        pass

    def _restore_transparency_for_page(self, idx: int):
        """Réaffiche l'image de travail mémorisée pour la page idx si elle
        existe — sinon aucun preview à afficher (retombe sur entry['bytes']
        normal via ensure_image_loaded, comme n'importe quelle page jamais
        touchée par cet outil)."""
        self._sharpness_preview_img = self._transp_work_img_by_page.get(idx)
