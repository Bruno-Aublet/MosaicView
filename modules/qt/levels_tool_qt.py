"""
modules/qt/levels_tool_qt.py — Outil "niveaux noir/blanc" (levels) de la
barre d'outils flottante de la visionneuse principale (image_viewer_qt.py).

Fusion progressive des visionneuses : ce module contient toute la logique
propre à l'outil "levels" — état + geste souris des pipettes (mixin
LevelsCanvasMixin,
hérité par _ViewerCanvas), commit de l'ajustement dans l'historique du
panneau (mixin LevelsViewerMixin, hérité par ImageViewer), et le panneau
flottant des 7 contrôles (_LevelsOptionsPanel). image_viewer_qt.py ne fait
qu'hériter de ces deux mixins et brancher l'icône de la barre d'outils — voir
CLAUDE.md règle "ne jamais migrer le code d'un outil dans image_viewer_qt.py".

Contrairement à sharpness/brightness/saturation/remove_colors/compression
(réglette(s) pure(s), aucun geste souris sur le canvas), cet outil a un VRAI
geste souris comme crop/straighten/clone/texte : les 2 pipettes (noire/
blanche) captent la luminance du pixel cliqué sur l'image pour positionner
respectivement le point noir et le point blanc — voir skill adjust-levels
pour le comportement de référence.

Panneau flottant à 7 contrôles, dans cet ordre (logique de lecture
d'histogramme gauche→droite, noir avant blanc — convention universelle des
logiciels de retouche) :
  pipette noire | slider+spin point noir | slider+spin gamma |
  slider+spin point blanc | pipette blanche || slider+spin seuil | bouton Auto
Le séparateur avant seuil marque que la binarisation (threshold) est un
traitement PIL INDÉPENDANT du triplet noir/gamma/blanc (2 blocs `if` séparés
dans apply_adjustments(), voir skill adjust-levels) — pas une même LUT.
Gamma n'a volontairement PAS de pipette : aucun pixel ne "possède" une valeur
de gamma (courbe de répartition entre les 2 points déjà fixés, pas un
prélèvement ponctuel) — convention universelle des logiciels de retouche
(Photoshop/GIMP/Lightroom/Darktable), pas une lacune de MosaicView.

Comme sharpness/brightness (pas comme remove_colors/saturation) : les 4
sliders restent sur la valeur commitée après relâchement/editingFinished, ne
reviennent PAS à une valeur neutre — cohérent avec "l'image a réellement
changé, le contrôle doit continuer à en témoigner".

Preview live + commit automatique pour les 4 SLIDERS (threshold, black_point,
gamma, white_point) : même pattern que brightness/compression, pas de bouton
"Valider". Pour les 2 PIPETTES : un clic sur l'image est déjà un geste complet
(pas de drag à relâcher) — positionne le slider correspondant PUIS commit
immédiat dans la foulée (pas d'attente d'un relâchement qui n'existe pas pour
ce geste). Le bouton "Auto" (compute_auto_levels, skill adjust-levels) suit le
même principe : recalcule noir/blanc, met à jour les 2 sliders, commit
immédiat.

state.levels_value_by_history_index (state.py) mémorise le tuple
(threshold, black_point, gamma, white_point) COMMITÉ, indexé par
(image_idx, state.history_index) — même principe et même durée de vie que
state.brightness_value_by_history_index (survit à une fermeture/réouverture
de la visionneuse tant que l'historique n'a pas bougé). Resynchronise les 4
sliders au changement de page/désélection de l'outil/undo-redo (voir
_reset_levels_preview) — valeurs neutres (128, 0, 1.0, 255) si aucun commit
levels ne correspond à ce point d'historique.

Pas de bouton "Valider", pas de persistance de "travail non validé" par page
(pas de _levels_by_page, contrairement à crop/straighten/texte) : chaque
geste (relâchement de slider, clic pipette, clic Auto) commit déjà tout dans
la foulée — il ne peut jamais y avoir de valeur "en attente" à committer plus
tard. Ne contribue pas à ImageViewer._has_unvalidated_work().

self._sharpness_preview_img (ImageViewer, défini dans sharpness_tool_qt.py)
est RÉUTILISÉ tel quel comme champ de preview partagé pour ce mode aussi —
un seul outil actif à la fois dans la barre, jamais besoin d'un preview
simultané avec un autre mode d'ajustement.
"""

import io

from PIL import Image

from PySide6.QtWidgets import (
    QWidget, QLabel, QHBoxLayout, QVBoxLayout, QSlider, QSpinBox, QDoubleSpinBox,
    QPushButton, QFrame,
)
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QCursor, QPixmap

from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.clone_tool_qt import floating_options_panel_style


# ─────────────────────────────────────────────────────────────────────────────
# Panneau flottant des 7 contrôles (pipettes + sliders + Auto)
# ─────────────────────────────────────────────────────────────────────────────

class _LevelsOptionsPanel(QWidget):
    """Panneau flottant avec les 7 contrôles de l'outil "niveaux", répartis
    sur 2 lignes (une seule ligne serait trop large et laisserait plusieurs
    sliders sans aucun label visible) :
      ligne 1 (réglages) : label+slider+spin point noir | label+slider+spin
        gamma | label+slider+spin point blanc | séparateur | label+slider+spin
        seuil | bouton Auto
      ligne 2 (pipettes) : pipette noire | pipette blanche
    Même principe que _BrightnessOptionsPanel (jamais inséré dans le layout
    de ImageViewer, indépendant du timer d'auto-masquage de la barre pour ne
    pas interrompre un réglage en cours) — seule la disposition change,
    toujours un layout maison plutôt qu'une vraie QToolBar.

    Chaque slider a désormais un label explicite (point noir/gamma/point
    blanc/seuil) — la 1re version ne labellisait que gamma et seuil, laissant
    les 2 sliders point noir/point blanc sans aucune indication visuelle de
    ce qu'ils réglaient, à côté de leur pipette respective.

    Chaque slider a sa spinbox synchronisée (même valeur, deux façons de la
    modifier). Pendant le déplacement d'un slider (valueChanged) ou la frappe
    dans une spinbox : preview PIL live combinant les 4 valeurs courantes,
    sans toucher entry['bytes']. Commit réel via
    LevelsViewerMixin.perform_levels() au relâchement d'un slider
    (sliderReleased) OU à la perte de focus/validation d'une spinbox
    (editingFinished) — puis les 4 valeurs NE reviennent PAS à leur défaut
    (voir docstring de module).

    Les pipettes n'ont pas de valeur propre : armer une pipette (clic sur son
    bouton) puis cliquer sur l'image écrit directement dans black_point/
    white_point via set_black_point()/set_white_point() (appelées par
    LevelsCanvasMixin.levels_pipette_click, ImageViewer sait déjà retrouver
    ce panneau via self._toolbar._levels_panel) et déclenche un commit
    immédiat — pas de sliderReleased à attendre pour ce geste.
    """

    _PT_MIN, _PT_MAX = 0, 255
    _GAMMA_MIN, _GAMMA_MAX = 10, 300  # ×0.01 → 0.10..3.00, défaut 100=1.0
    _THRESHOLD_MIN, _THRESHOLD_MAX = 0, 255

    def __init__(self, viewer: "ImageViewer"):
        super().__init__(viewer._canvas)
        # Sans cet attribut, un QWidget nu n'applique jamais le "background"
        # d'une stylesheet (contrairement à QLabel/QPushButton).
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._viewer = viewer

        self.black_point = 0
        self.gamma = 1.0
        self.white_point = 255
        self.threshold = 128
        self.active_pipette: str | None = None  # None | "black" | "white"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(4)

        # Ligne 1 : les 4 réglettes (point noir, gamma, point blanc, seuil) + Auto
        layout = QHBoxLayout()
        layout.setSpacing(6)
        outer.addLayout(layout)

        from modules.qt.font_loader import resource_path

        def _load_icon(fname, size):
            path = resource_path(f'icons/{fname}')
            pm = QPixmap(path)
            if pm.isNull():
                return None
            return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        # Curseurs custom pipette (icône 36×36, assez grande pour rester
        # lisible/utilisable comme curseur). Hotspot en bas à gauche de
        # l'icône — la pointe du dessin, l'endroit qui touche réellement le
        # pixel visé, PAS (0, 0)/coin haut-gauche du bitmap qui ne correspond
        # à rien de visuel sur cette icône : voir icons/pipette_noire.png,
        # pointe clairement dans le coin bas-gauche du dessin. Avec un
        # hotspot à (0,0), le clic serait routé plusieurs dizaines de pixels
        # au-dessus/à gauche de ce que l'utilisateur vise visuellement, assez
        # pour rater un bouton de 26px de haut situé juste au-dessus de
        # l'image et retomber sur le canvas en dessous.
        #
        # Croix de visée ÉVIDÉE au centre (gap autour du hotspot, PAS une
        # croix pleine qui se rejoint au milieu) dessinée par-dessus l'icône,
        # centrée EXACTEMENT sur ce hotspot : une croix pleine masquerait
        # justement le pixel qu'on cherche à viser, rendant toute sélection
        # fine impossible. Convention Photoshop/GIMP : le point de capture
        # doit rester visible, entouré par la croix, pas recouvert par elle.
        # Voir _build_pipette_cursor pour la géométrie exacte.
        self._cursor_black = None
        self._cursor_white = None
        for fname, attr in (("pipette_noire.png", "_cursor_black"),
                             ("pipette_blanche.png", "_cursor_white")):
            path = resource_path(f"icons/{fname}")
            pix = QPixmap(path)
            if not pix.isNull():
                icon_scaled = pix.scaled(
                    36, 36, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                setattr(self, attr, self._build_pipette_cursor(icon_scaled))

        font_btn = _get_current_font(11)

        # ── Point noir ───────────────────────────────────────────────────────
        # Label : sans lui, ce slider n'aurait aucune indication visuelle de
        # ce qu'il règle, seulement sa pipette adjacente.
        self._black_label = QLabel()
        layout.addWidget(self._black_label)
        self._black_slider = QSlider(Qt.Orientation.Horizontal)
        self._black_slider.setMinimum(self._PT_MIN)
        self._black_slider.setMaximum(self._PT_MAX)
        self._black_slider.setValue(0)
        self._black_slider.setFixedWidth(80)
        self._black_slider.valueChanged.connect(self._on_black_slider_changed)
        self._black_slider.sliderPressed.connect(self._on_slider_pressed)
        self._black_slider.sliderReleased.connect(self._on_slider_released)
        layout.addWidget(self._black_slider)
        self._black_spin = QSpinBox()
        self._black_spin.setRange(self._PT_MIN, self._PT_MAX)
        self._black_spin.setValue(0)
        self._black_spin.setFixedWidth(62)
        self._black_spin.valueChanged.connect(self._on_black_spin_changed)
        self._black_spin.editingFinished.connect(self._on_editing_finished)
        layout.addWidget(self._black_spin)

        # ── Gamma ────────────────────────────────────────────────────────────
        self._gamma_label = QLabel()
        layout.addWidget(self._gamma_label)
        self._gamma_slider = QSlider(Qt.Orientation.Horizontal)
        self._gamma_slider.setMinimum(self._GAMMA_MIN)
        self._gamma_slider.setMaximum(self._GAMMA_MAX)
        self._gamma_slider.setValue(100)
        self._gamma_slider.setFixedWidth(80)
        self._gamma_slider.valueChanged.connect(self._on_gamma_slider_changed)
        self._gamma_slider.sliderPressed.connect(self._on_slider_pressed)
        self._gamma_slider.sliderReleased.connect(self._on_slider_released)
        layout.addWidget(self._gamma_slider)
        self._gamma_spin = QDoubleSpinBox()
        self._gamma_spin.setRange(self._GAMMA_MIN / 100.0, self._GAMMA_MAX / 100.0)
        self._gamma_spin.setSingleStep(0.01)
        self._gamma_spin.setDecimals(2)
        self._gamma_spin.setValue(1.0)
        self._gamma_spin.setFixedWidth(62)
        self._gamma_spin.valueChanged.connect(self._on_gamma_spin_changed)
        self._gamma_spin.editingFinished.connect(self._on_editing_finished)
        layout.addWidget(self._gamma_spin)

        # ── Point blanc ──────────────────────────────────────────────────────
        # Label : même raison que point noir ci-dessus.
        self._white_label = QLabel()
        layout.addWidget(self._white_label)
        self._white_slider = QSlider(Qt.Orientation.Horizontal)
        self._white_slider.setMinimum(self._PT_MIN)
        self._white_slider.setMaximum(self._PT_MAX)
        self._white_slider.setValue(255)
        self._white_slider.setFixedWidth(80)
        self._white_slider.valueChanged.connect(self._on_white_slider_changed)
        self._white_slider.sliderPressed.connect(self._on_slider_pressed)
        self._white_slider.sliderReleased.connect(self._on_slider_released)
        layout.addWidget(self._white_slider)
        self._white_spin = QSpinBox()
        self._white_spin.setRange(self._PT_MIN, self._PT_MAX)
        self._white_spin.setValue(255)
        self._white_spin.setFixedWidth(62)
        self._white_spin.valueChanged.connect(self._on_white_spin_changed)
        self._white_spin.editingFinished.connect(self._on_editing_finished)
        layout.addWidget(self._white_spin)

        # Ligne 2 : les 2 pipettes + bouton Auto, puis séparateur + Seuil,
        # centrés. Seuil isolé par un séparateur en fin de ligne, cohérent
        # avec le fait qu'il agit sur un traitement PIL INDÉPENDANT du
        # triplet noir/gamma/blanc. Ligne entière centrée via un stretch de
        # chaque côté plutôt qu'alignée à gauche.
        pip_row = QHBoxLayout()
        pip_row.setSpacing(10)
        outer.addLayout(pip_row)
        pip_row.addStretch(1)

        self._black_pip_btn = QPushButton()
        self._black_pip_btn.setFont(font_btn)
        self._black_pip_btn.setCheckable(True)
        icon_black = _load_icon("pipette_noire.png", 18)
        if icon_black is not None:
            self._black_pip_btn.setIcon(icon_black)
            self._black_pip_btn.setIconSize(QSize(18, 18))
        self._black_pip_btn.clicked.connect(self._on_black_pipette_clicked)
        pip_row.addWidget(self._black_pip_btn)

        self._white_pip_btn = QPushButton()
        self._white_pip_btn.setFont(font_btn)
        self._white_pip_btn.setCheckable(True)
        icon_white = _load_icon("pipette_blanche.png", 18)
        if icon_white is not None:
            self._white_pip_btn.setIcon(icon_white)
            self._white_pip_btn.setIconSize(QSize(18, 18))
        self._white_pip_btn.clicked.connect(self._on_white_pipette_clicked)
        pip_row.addWidget(self._white_pip_btn)

        # ── Auto ─────────────────────────────────────────────────────────────
        self._auto_btn = QPushButton()
        self._auto_btn.setFont(font_btn)
        self._auto_btn.clicked.connect(self._on_auto_clicked)
        pip_row.addWidget(self._auto_btn)

        # ── Séparateur (seuil = traitement indépendant, voir docstring) ─────
        self._separator = QFrame()
        self._separator.setFrameShape(QFrame.VLine)
        pip_row.addWidget(self._separator)

        # ── Seuil ────────────────────────────────────────────────────────────
        self._threshold_label = QLabel()
        pip_row.addWidget(self._threshold_label)
        self._threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self._threshold_slider.setMinimum(self._THRESHOLD_MIN)
        self._threshold_slider.setMaximum(self._THRESHOLD_MAX)
        self._threshold_slider.setValue(128)
        self._threshold_slider.setFixedWidth(80)
        self._threshold_slider.valueChanged.connect(self._on_threshold_slider_changed)
        self._threshold_slider.sliderPressed.connect(self._on_slider_pressed)
        self._threshold_slider.sliderReleased.connect(self._on_slider_released)
        pip_row.addWidget(self._threshold_slider)
        self._threshold_spin = QSpinBox()
        self._threshold_spin.setRange(self._THRESHOLD_MIN, self._THRESHOLD_MAX)
        self._threshold_spin.setValue(128)
        self._threshold_spin.setFixedWidth(62)
        self._threshold_spin.valueChanged.connect(self._on_threshold_spin_changed)
        self._threshold_spin.editingFinished.connect(self._on_editing_finished)
        pip_row.addWidget(self._threshold_spin)

        pip_row.addStretch(1)

        self.hide()

    @staticmethod
    def _build_pipette_cursor(icon_pixmap: QPixmap) -> QCursor:
        """Compose l'icône pipette (36×36) sur une toile 56×56 transparente,
        avec une croix de visée ÉVIDÉE (gap au centre, PAS de traits qui se
        rejoignent) dessinée au point de capture réel (le coin bas-gauche du
        dessin, où se trouve la pointe) : une croix pleine, bras collés au
        hotspot, masquerait justement le pixel qu'on cherche à viser. Le
        hotspot du QCursor pointe exactement sur le centre du gap, pas un
        pixel arbitraire.

        L'icône est décalée en diagonale par rapport au hotspot (pas collée
        dans le coin de la toile) pour que son propre dessin ne mange pas
        dans la zone du viseur : le hotspot dispose ainsi d'un anneau dégagé
        tout autour, y compris du côté de la pipette."""
        from PySide6.QtGui import QPainter, QPen, QColor

        canvas_size = 56
        canvas = QPixmap(canvas_size, canvas_size)
        canvas.fill(Qt.transparent)

        # Hotspot posé avec une marge fixe par rapport au bord bas-gauche de
        # la toile, pour laisser la place au viseur des 4 côtés.
        margin = 20
        cross_x = margin
        cross_y = canvas_size - margin

        # Icône positionnée pour que sa pointe (coin bas-gauche du dessin)
        # tombe à quelques pixels en diagonale AU-DESSUS-À-DROITE du hotspot
        # (gap_to_icon), pas directement dessus — sans ce décalage le dessin
        # de la pipette empiète sur le quadrant haut-droit du viseur.
        gap_to_icon = 4
        icon_x = cross_x + gap_to_icon
        icon_y = cross_y - gap_to_icon - icon_pixmap.height()

        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.drawPixmap(icon_x, icon_y, icon_pixmap)

        arm = 9       # longueur totale de chaque bras, hotspot exclu
        gap = 4        # rayon du trou central, laisse le pixel visé visible
        # Trait blanc plus épais en dessous du trait noir pour rester visible
        # quel que soit le fond (image sombre ou claire) — même principe que
        # la croix de visée du tampon de clonage (clone_tool_qt.py).
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
        self.setStyleSheet(floating_options_panel_style(theme, "_LevelsOptionsPanel"))
        for label in (self._black_label, self._gamma_label, self._white_label,
                      self._threshold_label):
            label.setStyleSheet(f"color: {theme['text']}; background: transparent;")
        self._separator.setStyleSheet(f"color: {theme['separator']};")
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
            f"QSpinBox, QDoubleSpinBox {{ background: {theme['bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 2px 4px; }} "
            f"QSpinBox::up-button, QSpinBox::down-button, "
            f"QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 16px; }}"
        )
        pip_style = (
            f"QPushButton {{ background: {theme['bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 3px 6px; }} "
            f"QPushButton:checked {{ background: {accent}; border: 1px solid {theme['text']}; }}"
        )
        btn_style = (
            f"QPushButton {{ background: {theme['bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 3px 10px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        for slider in (self._black_slider, self._gamma_slider, self._white_slider,
                       self._threshold_slider):
            slider.setStyleSheet(slider_style)
        for spin in (self._black_spin, self._gamma_spin, self._white_spin,
                     self._threshold_spin):
            spin.setStyleSheet(spin_style)
        self._black_pip_btn.setStyleSheet(pip_style)
        self._white_pip_btn.setStyleSheet(pip_style)
        self._auto_btn.setStyleSheet(btn_style)

    def retranslate(self):
        font = _get_current_font(11)
        self._black_label.setText(_("viewer.levels_black_panel_label"))
        self._black_label.setFont(font)
        self._gamma_label.setText(_("viewer.levels_gamma_panel_label"))
        self._gamma_label.setFont(font)
        self._white_label.setText(_("viewer.levels_white_panel_label"))
        self._white_label.setFont(font)
        self._threshold_label.setText(_("viewer.levels_threshold_panel_label"))
        self._threshold_label.setFont(font)
        self._black_pip_btn.setText(_("dialogs.levels_viewer.black_pipette"))
        self._black_pip_btn.setFont(font)
        self._white_pip_btn.setText(_("dialogs.levels_viewer.white_pipette"))
        self._white_pip_btn.setFont(font)
        self._auto_btn.setText(_("dialogs.adjustments.auto_levels_button"))
        self._auto_btn.setFont(font)
        for spin in (self._black_spin, self._gamma_spin, self._white_spin,
                     self._threshold_spin):
            spin.setFont(font)

        # Tooltips (OverlayTooltip obligatoire, skill qt-tooltips — jamais
        # setToolTip() natif) : réutilise l'instance déjà créée par la barre
        # principale (self._viewer._toolbar._overlay_tip) plutôt que d'en
        # instancier une seconde sur ce panneau.
        tip = self._viewer._toolbar._overlay_tip
        tip.track(self._black_pip_btn, _("dialogs.levels_viewer.black_pipette_tooltip"))
        tip.track(self._white_pip_btn, _("dialogs.levels_viewer.white_pipette_tooltip"))
        # Sliders : le label texte à côté suffit à identifier le contrôle,
        # le tooltip explique ce qu'il FAIT (pas juste son nom) — même
        # widget que le label pour rester simple (survoler le slider OU son
        # label affiche le même tooltip).
        for widget in (self._black_label, self._black_slider, self._black_spin):
            tip.track(widget, _("viewer.levels_black_slider_tooltip"))
        for widget in (self._gamma_label, self._gamma_slider, self._gamma_spin):
            tip.track(widget, _("viewer.levels_gamma_slider_tooltip"))
        for widget in (self._white_label, self._white_slider, self._white_spin):
            tip.track(widget, _("viewer.levels_white_slider_tooltip"))
        for widget in (self._threshold_label, self._threshold_slider, self._threshold_spin):
            tip.track(widget, _("viewer.levels_threshold_slider_tooltip"))
        tip.track(self._auto_btn, _("viewer.levels_auto_tooltip"))

    # ── Visibilité ────────────────────────────────────────────────────────────

    def set_visible_for_tool(self, tool_id: str | None):
        if tool_id == "levels":
            self.show()
            self.reposition()
            self.raise_()
        else:
            self.hide()
            self._deactivate_pipettes()

    def reposition(self):
        self.adjustSize()
        canvas = self._viewer._canvas
        x = (canvas.width() - self.width()) // 2
        y = 8 + self._viewer._toolbar.height() + 6
        self.move(max(0, x), y)

    def mousePressEvent(self, event):
        # Sans ce blindage, un clic sur une zone vide du panneau (marges
        # entre les widgets, pas absorbée par un QSlider/QSpinBox/QPushButton
        # enfant) "fuit" vers _ViewerCanvas en dessous (widget flottant
        # enfant du canvas) et pourrait déclencher un clic pipette sur
        # l'image affichée dessous — même piège déjà
        # documenté pour _ToolButton/_ActionButton/_ViewerToolbar (skill
        # viewers). Ce panneau est concerné au même titre que celui de
        # transparency : seul autre panneau de la barre avec un vrai geste de
        # clic sur le canvas (les 2 pipettes) à proximité immédiate.
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def enterEvent(self, event):
        self._viewer._toolbar.pause_hide()
        # Le curseur pipette est celui du CANVAS (posé au clic sur un bouton
        # pipette), pas celui de ce panneau — sans ce reset, la pipette
        # restait affichée par-dessus les contrôles du panneau lui-même.
        self.setCursor(Qt.ArrowCursor)

    def leaveEvent(self, event):
        # Voir _SharpnessOptionsPanel.leaveEvent : Qt peut envoyer un Leave au
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
            # dès que la souris repasse physiquement au-dessus.
            self.unsetCursor()

    # ── Pipettes ─────────────────────────────────────────────────────────────

    def _on_black_pipette_clicked(self):
        if self.active_pipette == "black":
            self._deactivate_pipettes()
        else:
            self.active_pipette = "black"
            self._black_pip_btn.setChecked(True)
            self._white_pip_btn.setChecked(False)
            self._viewer._canvas.setCursor(self._cursor_black or Qt.CrossCursor)

    def _on_white_pipette_clicked(self):
        if self.active_pipette == "white":
            self._deactivate_pipettes()
        else:
            self.active_pipette = "white"
            self._white_pip_btn.setChecked(True)
            self._black_pip_btn.setChecked(False)
            self._viewer._canvas.setCursor(self._cursor_white or Qt.CrossCursor)

    def _deactivate_pipettes(self):
        self.active_pipette = None
        self._black_pip_btn.setChecked(False)
        self._white_pip_btn.setChecked(False)
        self._viewer._canvas.setCursor(Qt.ArrowCursor)

    # ── Réglage ──────────────────────────────────────────────────────────────

    def reset(self):
        self.set_values_silent(128, 0, 1.0, 255)

    def set_values_silent(self, threshold: int, black_point: int, gamma: float, white_point: int):
        """Positionne les 4 sliders+spinboxes sans redéclencher preview ni
        commit (blockSignals) — utilisé par reset() et par la restauration
        après undo/redo (ImageViewer._refresh_after_undo_redo)."""
        for widget, value in (
            (self._threshold_slider, threshold), (self._threshold_spin, threshold),
            (self._black_slider, black_point), (self._black_spin, black_point),
            (self._white_slider, white_point), (self._white_spin, white_point),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        for widget, value in (
            (self._gamma_slider, round(gamma * 100)), (self._gamma_spin, gamma),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self.threshold = threshold
        self.black_point = black_point
        self.gamma = gamma
        self.white_point = white_point

    def set_black_point(self, value: int):
        """Appelé par LevelsCanvasMixin.levels_pipette_click : positionne le
        slider point noir depuis la luminance du pixel cliqué — le commit
        (perform_levels) est déclenché juste après par l'appelant, pas ici."""
        self.black_point = value
        self._black_slider.blockSignals(True)
        self._black_slider.setValue(value)
        self._black_slider.blockSignals(False)
        self._black_spin.blockSignals(True)
        self._black_spin.setValue(value)
        self._black_spin.blockSignals(False)

    def set_white_point(self, value: int):
        """Équivalent de set_black_point() pour le point blanc."""
        self.white_point = value
        self._white_slider.blockSignals(True)
        self._white_slider.setValue(value)
        self._white_slider.blockSignals(False)
        self._white_spin.blockSignals(True)
        self._white_spin.setValue(value)
        self._white_spin.blockSignals(False)

    def _on_black_slider_changed(self, value: int):
        self.black_point = value
        if self._black_spin.value() != value:
            self._black_spin.blockSignals(True)
            self._black_spin.setValue(value)
            self._black_spin.blockSignals(False)
        self._viewer._update_levels_preview()

    def _on_black_spin_changed(self, value: int):
        self.black_point = value
        if self._black_slider.value() != value:
            self._black_slider.blockSignals(True)
            self._black_slider.setValue(value)
            self._black_slider.blockSignals(False)
        self._viewer._update_levels_preview()

    def _on_gamma_slider_changed(self, value: int):
        self.gamma = round(value / 100.0, 2)
        if round(self._gamma_spin.value() * 100) != value:
            self._gamma_spin.blockSignals(True)
            self._gamma_spin.setValue(self.gamma)
            self._gamma_spin.blockSignals(False)
        self._viewer._update_levels_preview()

    def _on_gamma_spin_changed(self, value: float):
        self.gamma = round(value, 2)
        slider_val = round(self.gamma * 100)
        if self._gamma_slider.value() != slider_val:
            self._gamma_slider.blockSignals(True)
            self._gamma_slider.setValue(slider_val)
            self._gamma_slider.blockSignals(False)
        self._viewer._update_levels_preview()

    def _on_white_slider_changed(self, value: int):
        self.white_point = value
        if self._white_spin.value() != value:
            self._white_spin.blockSignals(True)
            self._white_spin.setValue(value)
            self._white_spin.blockSignals(False)
        self._viewer._update_levels_preview()

    def _on_white_spin_changed(self, value: int):
        self.white_point = value
        if self._white_slider.value() != value:
            self._white_slider.blockSignals(True)
            self._white_slider.setValue(value)
            self._white_slider.blockSignals(False)
        self._viewer._update_levels_preview()

    def _on_threshold_slider_changed(self, value: int):
        self.threshold = value
        if self._threshold_spin.value() != value:
            self._threshold_spin.blockSignals(True)
            self._threshold_spin.setValue(value)
            self._threshold_spin.blockSignals(False)
        self._viewer._update_levels_preview()

    def _on_threshold_spin_changed(self, value: int):
        self.threshold = value
        if self._threshold_slider.value() != value:
            self._threshold_slider.blockSignals(True)
            self._threshold_slider.setValue(value)
            self._threshold_slider.blockSignals(False)
        self._viewer._update_levels_preview()

    def _on_slider_pressed(self):
        # QSlider capture la souris (grab) pendant tout le drag actif — même
        # piège que _SharpnessOptionsPanel._on_slider_pressed, voir sa docstring.
        self._viewer._toolbar.pause_hide()

    def _on_slider_released(self):
        self._viewer.perform_levels()

    def _on_editing_finished(self):
        self._viewer.perform_levels()

    def _on_auto_clicked(self):
        self._viewer.perform_auto_levels()


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état et geste souris des pipettes (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class LevelsCanvasMixin:
    """Hérité par _ViewerCanvas (image_viewer_qt.py) en plus de QLabel : ajoute
    l'état et le geste souris des pipettes de l'outil "levels" au canvas de la
    visionneuse, sans que leur code vive dans image_viewer_qt.py.

    Contrairement à sharpness/brightness/saturation/remove_colors/compression
    (aucun geste souris), cet outil a un clic à intercepter — mais
    contrairement à crop/straighten/clone (rubber-band, trait, tampon), il
    n'y a aucun overlay à dessiner : le clic capte juste un pixel et ressort
    immédiatement, rien à peindre dans paintEvent.
    """

    def _init_levels_state(self):
        pass

    def levels_pipette_click(self, event):
        """Appelé depuis _ViewerCanvas.mousePressEvent quand l'outil "levels"
        est actif ET qu'une pipette est armée (panel.active_pipette non None).
        Convertit la position écran en coordonnées image (en tenant compte du
        zoom/pan courants, skill adjust-levels), lit la luminance du
        pixel, met à jour le slider correspondant, puis commit immédiatement
        (pas de relâchement à attendre pour ce geste, contrairement aux 4
        sliders)."""
        panel = self._viewer._toolbar._levels_panel
        if panel.active_pipette is None:
            return

        from modules.qt import state as _state_module
        state = self._viewer.callbacks.get('state') or _state_module.state
        entry = state.images_data[self._viewer.current_idx]
        if not entry.get('bytes'):
            return

        pos = event.position().toPoint()
        # display_offset_x/y et display_width/height décrivent le pixmap
        # actuellement affiché (résolution source, étiré par Qt à l'affichage
        # — voir paintEvent) : conversion écran → image identique au calcul
        # de perform_crop() (crop_tool_qt.py).
        if self.display_width <= 0 or self.display_height <= 0:
            return
        try:
            img = Image.open(io.BytesIO(entry['bytes']))
        except Exception:
            return
        img_w, img_h = img.size
        img_x = int((pos.x() - self.display_offset_x) * img_w / self.display_width)
        img_y = int((pos.y() - self.display_offset_y) * img_h / self.display_height)
        if img_x < 0 or img_y < 0 or img_x >= img_w or img_y >= img_h:
            return
        try:
            pixel = img.getpixel((img_x, img_y))
        except Exception:
            return
        if isinstance(pixel, tuple):
            lum = int(sum(pixel[:3]) / 3)
        else:
            lum = int(pixel)

        if panel.active_pipette == "black":
            panel.set_black_point(lum)
        else:
            panel.set_white_point(lum)
        self._viewer.perform_levels()


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — preview live + commit dans l'historique du panneau (hérité par ImageViewer)
# ─────────────────────────────────────────────────────────────────────────────

class LevelsViewerMixin:
    """Hérité par ImageViewer (image_viewer_qt.py) en plus de QDialog : ajoute
    la logique de l'outil "levels" au viewer, sans que son code vive dans
    image_viewer_qt.py. Suppose que l'hôte a déjà self._canvas (_ViewerCanvas,
    avec LevelsCanvasMixin), self.callbacks, self.current_idx, self._toolbar
    (avec _levels_panel), et self._sharpness_preview_img (champ de preview
    PARTAGÉ avec les autres modes d'ajustement, défini dans
    image_viewer_qt.py::ImageViewer.__init__ — un seul outil actif à la fois
    dans la barre, jamais besoin d'un preview simultané).
    """

    def _levels_is_neutral(self, threshold, black_point, gamma, white_point) -> bool:
        return threshold == 128 and black_point == 0 and gamma == 1.0 and white_point == 255

    def _update_levels_preview(self):
        """Régénère le pixmap affiché avec les valeurs courantes des 4
        sliders, SANS toucher entry['bytes'] — même principe que
        BrightnessViewerMixin._update_brightness_preview(), réutilise la même
        fonction de traitement (apply_adjustments) pour ne pas dupliquer la
        formule PIL (LUT point noir/gamma/point blanc + binarisation seuil,
        skill adjust-levels).

        Le résultat est stocké dans self._sharpness_preview_img (champ
        partagé, voir docstring de classe) : _display_single_page
        (image_viewer_qt.py) l'utilise à la place de ensure_image_loaded(entry)
        quand il est défini pour la page courante."""
        from modules.qt import state as _state_module
        from modules.qt.image_processing_qt import apply_adjustments

        panel = self._toolbar._levels_panel
        threshold, black_point = panel.threshold, panel.black_point
        gamma, white_point = panel.gamma, panel.white_point
        if self._levels_is_neutral(threshold, black_point, gamma, white_point):
            self._sharpness_preview_img = None
            self.display_image(keep_crop_rect=True)
            return

        state = self.callbacks.get('state') or _state_module.state
        entry = state.images_data[self.current_idx]
        if not entry.get('bytes'):
            return

        original = Image.open(io.BytesIO(entry['bytes']))
        self._sharpness_preview_img = apply_adjustments(
            original.copy(),
            {'threshold': threshold, 'black_point': black_point,
             'gamma': gamma, 'white_point': white_point},
            for_preview=True)
        self.display_image(keep_crop_rect=True)

    def perform_levels(self, skip_history: bool = False):
        """Relâchement d'un slider, validation d'une spinbox, OU clic pipette
        (voir LevelsCanvasMixin.levels_pipette_click) : commit réel des
        niveaux dans entry['bytes'] (pattern skill apply-image-operation,
        variante A complète) — réutilise apply_image_adjustments()
        (image_processing_qt.py). Devient sa propre
        entrée d'historique, comme un commit de brightness (pas de bouton
        "Valider" séparé, voir docstring de module).

        Les 4 contrôles NE reviennent PAS à leur valeur neutre après ce
        commit (même principe que perform_brightness()) : ils restent sur les
        valeurs qui viennent d'être appliquées. Un nouveau geste après ce
        commit applique un ajustement ADDITIONNEL par-dessus l'image déjà
        modifiée (le calcul PIL repart de entry['bytes'] courant à chaque
        fois, pas d'un état "absolu" mémorisé) — comportement accepté
        explicitement, cohérent avec brightness/sharpness.

        skip_history : propagé à apply_image_adjustments()."""
        from modules.qt import state as _state_module
        from modules.qt.image_processing_qt import apply_image_adjustments
        from modules.qt.dialogs_qt import MsgDialog

        panel = self._toolbar._levels_panel
        threshold, black_point = panel.threshold, panel.black_point
        gamma, white_point = panel.gamma, panel.white_point
        if self._levels_is_neutral(threshold, black_point, gamma, white_point):
            return

        state = self.callbacks.get('state') or _state_module.state
        canvas = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]
            apply_image_adjustments(
                [entry],
                {'threshold': threshold, 'black_point': black_point,
                 'gamma': gamma, 'white_point': white_point},
                callbacks=self.callbacks, skip_history=skip_history)

            # apply_image_adjustments() vient de faire save_state(force=True)
            # en interne : state.history_index pointe maintenant sur CE
            # commit. Mémorisé sur state (PAS sur self/ImageViewer, voir
            # state.py) pour réafficher les bonnes valeurs sur les contrôles
            # si un undo/redo retombe pile sur ce point d'historique — même
            # principe que brightness_value_by_history_index.
            state.levels_value_by_history_index[(self.current_idx, state.history_index)] = (
                threshold, black_point, gamma, white_point)

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
            # keep_crop_rect=True : PAS pour préserver un crop (il n'y en a
            # jamais pendant un ajustement de niveaux), mais pour éviter
            # l'effet de bord de display_image() sans ce flag, qui appelle
            # inconditionnellement _canvas.clear_crop() — lequel remet aussi
            # pan_offset_x/y à 0 (crop_tool_qt.py::clear_crop, pensé pour
            # recentrer la vue quand on abandonne un crop). Sans ce flag, tout
            # commit levels après un zoom+pan recentrerait l'image sous les
            # pieds de l'utilisateur. Même bug latent sur brightness/
            # saturation/etc. (display_image() sans keep_crop_rect aussi) —
            # non corrigé, hors périmètre de ce module.
            self.display_image(keep_crop_rect=True)
            self._toolbar.refresh_undo_redo_state()
            self._macro_record_step(
                "levels",
                {"threshold": threshold, "black_point": black_point,
                 "gamma": gamma, "white_point": white_point},
                "macro.step_levels",
                {"threshold": threshold, "black_point": black_point,
                 "gamma": gamma, "white_point": white_point},
            )
            return True

        except Exception as e:
            dlg = MsgDialog(self._center_parent, "messages.errors.levels_failed.title",
                            "messages.errors.levels_failed.message",
                            message_kwargs={"error": str(e)})
            dlg.show_nonmodal()
            return False

    def perform_auto_levels(self):
        """Bouton "Auto" du panneau : calcule les points noir/blanc via
        compute_auto_levels (percentiles 1%/99%, skill adjust-levels) sur
        l'image RÉELLEMENT affichée (entry['bytes'] courant, pas un aperçu
        figé), met à jour les 2 sliders correspondants, puis commit
        immédiatement — même principe que le clic pipette (un geste complet,
        pas de relâchement à attendre)."""
        from modules.qt import state as _state_module
        from modules.qt.image_processing_qt import compute_auto_levels
        from modules.qt.dialogs_qt import MsgDialog

        state = self.callbacks.get('state') or _state_module.state
        try:
            entry = state.images_data[self.current_idx]
            if not entry.get('bytes'):
                return
            black_val, white_val = compute_auto_levels(entry['bytes'])
            panel = self._toolbar._levels_panel
            panel.set_black_point(black_val)
            panel.set_white_point(white_val)
            self.perform_levels()
        except Exception as e:
            dlg = MsgDialog(self._center_parent, "messages.errors.levels_failed.title",
                            "messages.errors.levels_failed.message",
                            message_kwargs={"error": str(e)})
            dlg.show_nonmodal()

    def _reset_levels_preview(self):
        """Annule le preview visuel en cours (drag non relâché) et
        resynchronise les 4 contrôles sur la page COURANTE (self.current_idx,
        potentiellement déjà mise à jour par l'appelant en cas de changement
        de page) — appelé au changement de page, à la désélection de l'outil,
        et après undo/redo (_refresh_after_undo_redo). Même principe que
        _reset_brightness_preview().

        Il ne peut jamais y avoir de valeur "en attente" à committer (chaque
        geste commit déjà tout, voir perform_levels/perform_auto_levels),
        mais les contrôles doivent refléter les dernières valeurs RÉELLEMENT
        commitées sur la page affichée, pas systématiquement les valeurs
        neutres : state.levels_value_by_history_index retrouve ce quadruplet
        si un commit levels existe pour (page, history_index) courants, sinon
        (128, 0, 1.0, 255) (page jamais ajustée à ce point d'historique).
        Désarme aussi une pipette restée armée (curseur remis à la normale)."""
        from modules.qt import state as _state_module
        self._sharpness_preview_img = None
        state = self.callbacks.get('state') or _state_module.state
        values = state.levels_value_by_history_index.get(
            (self.current_idx, state.history_index))
        panel = self._toolbar._levels_panel
        panel._deactivate_pipettes()
        if values is None:
            panel.reset()
        else:
            threshold, black_point, gamma, white_point = values
            panel.set_values_silent(threshold, black_point, gamma, white_point)
