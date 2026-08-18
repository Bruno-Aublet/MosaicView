"""
modules/qt/shapes_tool_qt.py — Outil "formes" (shapes) de la barre d'outils
flottante de la visionneuse principale (image_viewer_qt.py).

Fusion progressive des visionneuses (idees.txt #3, 12e outil migré) : ce
module contient toute la logique propre à l'outil "shapes" — état/
interactions du canvas (mixin ShapeCanvasMixin, hérité par _ViewerCanvas),
commit des formes dans l'historique du panneau (mixin ShapeViewerMixin,
hérité par ImageViewer), le panneau flottant de sélection de forme/couleurs/
épaisseur (_ShapeOptionsPanel), et les formes elles-mêmes (_Shape).
image_viewer_qt.py ne fait qu'hériter de ces deux mixins et brancher l'icône
de la barre d'outils — voir CLAUDE.md règle "ne jamais migrer le code d'un
outil dans image_viewer_qt.py".

5 types de formes (idees.txt, décision utilisateur) :
  ellipse | rectangle | rectangle à coins arrondis | ligne | flèche
Comme le texte, PLUSIEURS formes peuvent coexister sur une même page
(_shapes, liste). Contrairement au texte (overlay QTextEdit natif), une
forme est un objet géométrique pur (_Shape) dessiné directement dans
_ViewerCanvas.paintEvent — pas de widget Qt enfant par forme.

Tracé (décision utilisateur) : clic-glisser sur une zone vide crée une
nouvelle forme, qui devient sélectionnée (poignées visibles) dès le
relâchement de la souris — PAS aplatie dans les pixels à ce stade, "valider"
au sens de "la forme est posée et éditable" seulement. L'aplatissement réel
dans entry['bytes'] se fait au clic sur le bouton "Valider" flottant partagé
(comme crop/straighten/texte), au même titre que toutes les formes de la
page. Une fois posée, une forme sélectionnée peut être : redimensionnée par
ses poignées (8 zones façon crop pour ellipse/rectangle/rectangle arrondi, 2
poignées d'extrémité pour ligne/flèche), déplacée à la souris (glisser
l'intérieur) ou au clavier (flèches seules = 1px). Contrairement au texte
(déplacement câblé sur _RichTextOverlay.keyPressEvent, un widget Qt qui a le
focus), le déplacement clavier des formes est câblé sur les QShortcut
Left/Right/Up/Down au niveau ImageViewer (voir image_viewer_qt.py::
ImageViewer._shape_key_nav) — _ViewerCanvas a setFocusPolicy(Qt.NoFocus), une
forme sélectionnée n'est pas un widget Qt qui pourrait recevoir le focus
clavier lui-même.

Bouton "Valider" flottant partagé (_VALIDATE_KEYS["shapes"]) : contrairement
à crop/straighten/texte (masqué tant que rien à valider), CE bouton reste
TOUJOURS VISIBLE tant que l'outil "shapes" est actif, mais grisé/inactif tant
qu'aucune forme n'existe sur la page courante, vert/actif dès qu'au moins une
forme existe (décision explicite utilisateur — écart assumé par rapport aux
3 autres entrées de _VALIDATE_KEYS, qui restent en hide/show pur). Voir
image_viewer_qt.py::_ViewerCanvas._show_validate_btn pour l'implémentation
partagée exacte de cette variante.

Couleur UNIQUE (trait ET remplissage, pas deux couleurs séparées — retour
utilisateur explicite après un premier jet à deux boutons de couleur jugé
confus) : réutilisation telle quelle de _ColorPickerDialog (text_tool_qt.py)
— même sélecteur maison, un seul bouton couleur. Case à cocher "remplissage
activé" : une vraie QCheckBox (contrairement au reste des toggles de ce
panneau/de clone_tool_qt.py::_CloneOptionsPanel, qui utilisent des
QPushButton checkable) — testée en conditions réelles avec un style minimal
(background/color uniquement, jamais ::indicator), lisible malgré
WA_StyledBackground sur ce panneau. Grisée pour ligne/flèche (aucun
remplissage possible pour ces 2 formes).

Pipette couleur (sur la page affichée dans la visionneuse, pas l'écran) :
même mécanique que LevelsCanvasMixin.levels_pipette_click (conversion écran
→ image, lecture directe du pixel) + même curseur custom composé avec croix
de visée évidée que _LevelsOptionsPanel._build_pipette_cursor (repris tel
quel, pas réinventé) — mais lit la couleur RGBA complète du pixel au lieu
d'une luminance, et écrit dans l'unique couleur de la forme.

Rendu final (aplatissement) : PIL.ImageDraw sur une copie RGBA de l'image,
pattern apply-image-operation variante (A) complète, undo/redo unifié — UN
SEUL point d'historique à la validation (comme le texte), pas un par forme.
Persistance par page : _shapes_by_page (ImageViewer.__init__), même principe
que _text_blocks_by_page.
"""

import math

from PIL import Image, ImageDraw

from PySide6.QtWidgets import (
    QWidget, QLabel, QHBoxLayout, QFrame, QPushButton, QSlider, QSpinBox, QCheckBox,
)
from PySide6.QtCore import Qt, QPoint, QRect, QTimer, QSize
from PySide6.QtGui import QColor, QCursor, QPixmap, QPen, QPainter

from modules.qt import state as _state_module
from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.clone_tool_qt import floating_options_panel_style
from modules.qt.text_tool_qt import _ColorPickerDialog


# ─────────────────────────────────────────────────────────────────────────────
# Modèle géométrique d'une forme (coordonnées IMAGE, stables face au zoom/pan)
# ─────────────────────────────────────────────────────────────────────────────

SHAPE_TYPES = ("ellipse", "rectangle", "rounded_rectangle", "line", "arrow")
_LINE_LIKE = ("line", "arrow")

# Rayon des coins arrondis, en pixels IMAGE (indépendant du zoom, comme
# l'épaisseur de trait) — pas configurable par l'utilisateur (idees.txt,
# pas de contrôle dédié demandé), valeur fixe raisonnable.
_ROUNDED_RADIUS_IMG = 18
# Géométrie de la tête de flèche, en pixels IMAGE.
_ARROW_HEAD_LEN_IMG = 22
_ARROW_HEAD_ANGLE = math.radians(28)
# Croissance de la tête avec l'épaisseur du trait, PLAFONNÉE (bug vécu : à
# épaisseur max (40), une croissance linéaire sans plafond (+thickness*2)
# donnait une tête disproportionnée occupant presque toute la longueur
# tracée, rendant la flèche méconnaissable) — au-delà de _ARROW_HEAD_MAX_ADD,
# le trait épaissit toujours mais la tête n'grandit plus.
_ARROW_HEAD_MAX_ADD = 24


# Distance (pixels écran) entre le bord haut du rectangle englobant et la
# poignée de rotation — indépendante du zoom (fixe à l'écran, comme la
# tolérance de détection des autres poignées) pour rester cliquable à tout
# niveau de zoom.
_ROTATE_HANDLE_OFFSET = 24


def _arrow_head_len(thickness: int, zoom: float) -> int:
    """Longueur de la tête de flèche en pixels ÉCRAN pour ce thickness/zoom —
    fonction UNIQUE partagée entre le rendu écran (_paint_one_shape/
    _paint_arrow_head, où le corps du trait doit reculer exactement de cette
    même valeur) et le rendu final PIL (ShapeViewerMixin._draw_one_shape, en
    coordonnées image donc zoom=1.0) : un écart entre deux calculs séparés
    faisait déborder le corps du trait dans la tête d'un côté (bug vécu)."""
    return max(8, int(_ARROW_HEAD_LEN_IMG * zoom) + min(thickness, _ARROW_HEAD_MAX_ADD) * 2)


_ROTATE_CURSOR_CACHE: QCursor | None = None


def _build_rotate_cursor() -> QCursor:
    """Curseur custom de rotation (arc + tête de flèche, sens horaire) —
    remplace Qt.PointingHandCursor (retour utilisateur explicite : une main
    de pointage standard ne se distingue pas assez visuellement des autres
    curseurs pour signaler "ceci pivote"). Réutilisé À L'IDENTIQUE par
    paste_image_tool_qt.py (idees.txt #1, "les remarques sont valables aussi
    pour shapes" — mécanisme de poignées partagé entre les deux outils,
    donc UNE SEULE fonction de curseur plutôt que deux dessins séparés).
    Mis en cache au niveau module (dessiné une seule fois, jamais recalculé
    par outil/instance) — un QCursor est immuable une fois construit."""
    global _ROTATE_CURSOR_CACHE
    if _ROTATE_CURSOR_CACHE is not None:
        return _ROTATE_CURSOR_CACHE

    from PySide6.QtGui import QPolygonF
    from PySide6.QtCore import QPointF, QRectF

    size = 28
    canvas = QPixmap(size, size)
    canvas.fill(Qt.transparent)
    center = QPointF(size / 2, size / 2)
    radius = size / 2 - 5

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # Contour blanc large puis trait noir fin par-dessus (même principe de
    # lisibilité double-contour que la croix de visée de _build_pipette_cursor,
    # levels_tool_qt.py) — reste visible sur fond clair ET fond sombre.
    arc_rect = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
    span_deg = 270
    for pen in (QPen(QColor(255, 255, 255, 230), 4), QPen(QColor(0, 0, 0, 230), 2)):
        painter.setPen(pen)
        painter.drawArc(arc_rect, 90 * 16, span_deg * 16)

    # Tête de flèche au bout de l'arc (angle final = 90 - span, sens horaire
    # Qt standard pour drawArc en degrés*16 anti-horaire depuis 3h) —
    # positionnée tangente à l'arc pour indiquer le sens de rotation.
    end_angle_rad = math.radians(90 - span_deg)
    tip = QPointF(center.x() + radius * math.cos(end_angle_rad),
                   center.y() - radius * math.sin(end_angle_rad))
    tangent_rad = end_angle_rad - math.pi / 2
    head_len = 7
    head_angle = math.radians(28)
    p1 = QPointF(tip.x() - head_len * math.cos(tangent_rad - head_angle),
                 tip.y() + head_len * math.sin(tangent_rad - head_angle))
    p2 = QPointF(tip.x() - head_len * math.cos(tangent_rad + head_angle),
                 tip.y() + head_len * math.sin(tangent_rad + head_angle))
    head = QPolygonF([tip, p1, p2])
    painter.setPen(QPen(QColor(0, 0, 0, 230), 1))
    painter.setBrush(QColor(255, 255, 255, 230))
    painter.drawPolygon(head)
    painter.end()

    _ROTATE_CURSOR_CACHE = QCursor(canvas, size // 2, size // 2)
    return _ROTATE_CURSOR_CACHE


def _paint_out_of_page_bounds_overlay(painter, canvas, corners_widget: list) -> None:
    """Grise la portion d'un objet manipulable (forme OU image collée) qui
    dépasse des limites de la PAGE affichée (idees.txt #1, décision explicite
    utilisateur : "la partie hors de l'image principale doit être grisée
    afin de signifier à l'utilisateur qu'elle sera tronquée à l'aplatissage"
    — s'applique aussi à shapes). `corners_widget` = les 4 coins de l'objet
    en coordonnées ÉCRAN, DÉJÀ tournés (repère non tourné, indépendant de
    tout painter.rotate() actif ailleurs) — un QPainterPath exact plutôt
    qu'une simple bounding box axis-aligned, pour rester correct même avec
    une rotation non nulle (un rectangle englobant axis-aligned grise trop
    ou pas assez dès que l'objet est tourné). Le rectangle de la page vient
    directement de `canvas.display_offset_x/y` + `display_width/height`
    (mêmes coordonnées que le pixmap de page déjà dessiné dans paintEvent).
    No-op si `canvas.display_width/height` ne sont pas encore valides (page
    pas encore affichée)."""
    if canvas.display_width <= 0 or canvas.display_height <= 0:
        return
    from PySide6.QtGui import QPainterPath
    obj_path = QPainterPath()
    obj_path.moveTo(corners_widget[0])
    for pt in corners_widget[1:]:
        obj_path.lineTo(pt)
    obj_path.closeSubpath()

    page_path = QPainterPath()
    page_path.addRect(QRect(canvas.display_offset_x, canvas.display_offset_y,
                             canvas.display_width, canvas.display_height))

    out_of_bounds = obj_path.subtracted(page_path)
    if out_of_bounds.isEmpty():
        return
    painter.save()
    # Opacité relevée à 210/255 (précédemment 150 — retour utilisateur
    # explicite : "pas assez marqué", un gris à 59% restait peu contrasté sur
    # une image source colorée) + fine bordure pointillée rouge en plus du
    # remplissage gris — même couleur d'alerte que les poignées de sélection
    # (QColor("red")) déjà utilisée ailleurs dans cette barre, pour un signal
    # net même sur un fond déjà sombre.
    painter.setPen(QPen(QColor("red"), 1, Qt.PenStyle.DashLine))
    painter.setBrush(QColor(60, 60, 60, 210))
    painter.drawPath(out_of_bounds)
    painter.restore()


class _Shape:
    """Une forme posée sur la page — géométrie en coordonnées IMAGE (comme
    _TextBlock.img_pos/le rectangle de crop), reconvertie en coordonnées
    widget à chaque paintEvent (voir ShapeCanvasMixin.paint_shapes)."""

    def __init__(self, shape_type: str, ix1: int, iy1: int, ix2: int, iy2: int,
                 color: QColor, fill_enabled: bool, thickness: int, angle: float = 0.0):
        self.shape_type = shape_type
        # (ix1,iy1) / (ix2,iy2) : coin haut-gauche / bas-droit pour les formes
        # fermées, point de départ / point d'arrivée pour ligne/flèche (pas
        # normalisé dans ce cas — le sens du tracé compte pour la tête de
        # flèche).
        self.ix1, self.iy1 = ix1, iy1
        self.ix2, self.iy2 = ix2, iy2
        # UNE SEULE couleur, utilisée pour le trait ET le remplissage (retour
        # utilisateur explicite : deux couleurs séparées jugées confuses).
        self.color = QColor(color)
        self.fill_enabled = fill_enabled and shape_type not in _LINE_LIKE
        self.thickness = thickness
        # Rotation en degrés, sens horaire, autour du CENTRE du rectangle
        # englobant normalisé — uniquement pertinent pour les formes fermées
        # (ellipse/rectangle/rectangle arrondi) : tourner une ligne/flèche
        # revient à déplacer un de ses 2 points d'extrémité, déjà possible
        # sans notion d'angle séparée (is_line_like() garde ce champ à 0.0
        # pour elles, jamais lu).
        self.angle = angle if shape_type not in _LINE_LIKE else 0.0

    def is_line_like(self) -> bool:
        return self.shape_type in _LINE_LIKE

    def center_img(self) -> tuple:
        """Centre du rectangle englobant normalisé, en coordonnées image —
        pivot de la rotation."""
        x1, y1, x2, y2 = self.normalized_img_rect()
        return (x1 + x2) / 2, (y1 + y2) / 2

    def normalized_img_rect(self) -> tuple:
        """Rectangle englobant normalisé (x1<=x2, y1<=y2) en coordonnées
        image — utilisé pour les formes fermées (pas pour ligne/flèche, dont
        les 2 points gardent leur sens de tracé)."""
        return (min(self.ix1, self.ix2), min(self.iy1, self.iy2),
                max(self.ix1, self.ix2), max(self.iy1, self.iy2))


# ─────────────────────────────────────────────────────────────────────────────
# Panneau flottant : forme, remplissage, couleurs, épaisseur
# ─────────────────────────────────────────────────────────────────────────────

class _ShapeOptionsPanel(QWidget):
    """Panneau flottant sous la barre d'outils, visible uniquement quand
    l'outil "shapes" est actif — même principe de positionnement que
    _StraightenAnglePanel/_CloneOptionsPanel/_TextOptionsPanel.

    Ligne unique : 5 boutons de sélection de forme (checkable, exclusifs —
    même mécanisme que _CloneOptionsPanel._radio_fixed/_radio_relative, PAS
    de QRadioButton natif) | séparateur | réglette épaisseur | séparateur |
    bouton couleur de trait + pipette | bouton couleur de remplissage +
    pipette + case "remplissage activé" (bouton checkable, grisée pour
    ligne/flèche).
    """

    _THICKNESS_MIN, _THICKNESS_MAX = 1, 40

    def __init__(self, viewer: "ImageViewer"):
        super().__init__(viewer._canvas)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._viewer = viewer

        self.shape_type = "rectangle"
        self.thickness = 4
        # Couleur UNIQUE (trait ET remplissage) — retour utilisateur : deux
        # carrés de couleur séparés jugés confus/redondants.
        self.color = QColor(255, 0, 0, 255)
        self.fill_enabled = False
        # False | True — pipette armée ou non, même principe que
        # _LevelsOptionsPanel.active_pipette mais une seule couleur possible
        # ici (pas de choix trait/fill à faire pour l'armer).
        self.pipette_active = False
        self._cursor_pipette = self._build_pipette_cursor()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        self._SHAPE_ICONS = {
            "ellipse": "BTN_Shape_Ellipse.png",
            "rectangle": "BTN_Shape_Rectangle.png",
            "rounded_rectangle": "BTN_Shape_Rounded_Rectangle.png",
            "line": "BTN_Shape_Line.png",
            "arrow": "BTN_Shape_Arrow.png",
        }
        # Construits SANS connecter toggled ni cocher le bouton par défaut à
        # ce stade : _on_shape_type_toggled → _update_fill_controls_enabled
        # référence self._fill_enabled_cb, qui n'existe pas encore tant que
        # le reste du panneau n'est pas construit — setChecked(True) plus bas
        # dans la boucle émettrait toggled immédiatement et provoquerait un
        # AttributeError (bug vécu). Connexion + coche différées en fin de
        # __init__, une fois tous les widgets du panneau créés.
        self._shape_buttons: dict[str, QPushButton] = {}
        for shape_type in SHAPE_TYPES:
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setFixedSize(30, 30)
            btn.setIconSize(QSize(20, 20))
            layout.addWidget(btn)
            self._shape_buttons[shape_type] = btn
        self._load_shape_icons()

        self._sep1 = QFrame()
        self._sep1.setFrameShape(QFrame.Shape.VLine)
        layout.addWidget(self._sep1)

        self._lbl_thickness = QLabel()
        layout.addWidget(self._lbl_thickness)
        self._thickness_slider = QSlider(Qt.Orientation.Horizontal)
        self._thickness_slider.setMinimum(self._THICKNESS_MIN)
        self._thickness_slider.setMaximum(self._THICKNESS_MAX)
        self._thickness_slider.setValue(self.thickness)
        self._thickness_slider.setFixedWidth(100)
        self._thickness_slider.valueChanged.connect(self._on_thickness_changed)
        layout.addWidget(self._thickness_slider)
        self._thickness_spin = QSpinBox()
        self._thickness_spin.setRange(self._THICKNESS_MIN, self._THICKNESS_MAX)
        self._thickness_spin.setValue(self.thickness)
        self._thickness_spin.setFixedWidth(56)
        self._thickness_spin.valueChanged.connect(self._on_thickness_changed)
        layout.addWidget(self._thickness_spin)

        self._sep2 = QFrame()
        self._sep2.setFrameShape(QFrame.Shape.VLine)
        layout.addWidget(self._sep2)

        # Une SEULE couleur, utilisée pour le trait ET le remplissage (retour
        # utilisateur explicite : deux carrés de couleur séparés jugés
        # confus/redondants) — un seul bouton couleur + une seule pipette.
        self._lbl_color = QLabel()
        layout.addWidget(self._lbl_color)
        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(26, 26)
        self._color_btn.clicked.connect(self._pick_color)
        layout.addWidget(self._color_btn)
        self._pip_btn = QPushButton()
        self._pip_btn.setCheckable(True)
        self._pip_btn.setFixedSize(30, 30)
        self._pip_btn.setIconSize(QSize(24, 24))
        self._pip_btn.clicked.connect(self._toggle_pipette)
        layout.addWidget(self._pip_btn)
        self._load_pipette_icon()

        self._sep3 = QFrame()
        self._sep3.setFrameShape(QFrame.Shape.VLine)
        layout.addWidget(self._sep3)

        # Vraie QCheckBox (pas un bouton texte comme _CloneOptionsPanel) —
        # nécessite un style ::indicator EXPLICITE (posé dans _apply_theme)
        # pour rester visible sur ce panneau à fond stylé
        # (WA_StyledBackground) : un style ne posant que background/color
        # laissait l'indicateur totalement invisible (bug vécu), voir
        # _apply_theme pour le détail.
        self._fill_enabled_cb = QCheckBox()
        self._fill_enabled_cb.toggled.connect(self._on_fill_enabled_toggled)
        layout.addWidget(self._fill_enabled_cb)

        # Connexion + coche par défaut des 5 boutons de forme, différées
        # jusqu'ici (voir commentaire plus haut) : tout le panneau existe
        # maintenant, _on_shape_type_toggled peut résoudre self._fill_enabled_cb
        # sans crash.
        for shape_type, btn in self._shape_buttons.items():
            btn.toggled.connect(
                lambda checked, st=shape_type: self._on_shape_type_toggled(checked, st))
        self._shape_buttons["rectangle"].setChecked(True)

        self._update_fill_controls_enabled()
        self.hide()

    # ── Icônes des 5 boutons de forme ─────────────────────────────────────────

    def _load_shape_icons(self):
        """Icônes générées en noir plein fixe (PIL.ImageDraw, pas de fichier
        fourni) — mêmes conditions que BTN_Sharpness.png/BTN_Unsharp.png
        (viewer_toolbar_qt.py::_DARK_MODE_RECOLOR_ICONS) : quasi invisibles
        sur le fond sombre du panneau en mode sombre. Ce panneau n'est pas un
        _ToolButton de la barre principale (le mécanisme existant ne
        s'applique pas tel quel), donc recoloriage direct ici avec la même
        fonction (_recolor_for_dark, viewer_toolbar_qt.py), rappelée à
        chaque changement de thème (_apply_theme)."""
        from modules.qt.font_loader import resource_path
        from PySide6.QtGui import QIcon
        from modules.qt.viewer_toolbar_qt import _recolor_for_dark
        from modules.qt.image_viewer_qt import _pil_to_qpixmap
        theme = get_current_theme()
        dark = _state_module.state.dark_mode
        for shape_type, filename in self._SHAPE_ICONS.items():
            path = resource_path(f"icons/{filename}")
            pil_img = Image.open(path).convert("RGBA")
            if dark:
                pil_img = _recolor_for_dark(pil_img, theme['text'])
            pm = _pil_to_qpixmap(pil_img)
            if not pm.isNull():
                self._shape_buttons[shape_type].setIcon(QIcon(pm))

    def _load_pipette_icon(self):
        """Icône du bouton pipette (pipette_blanche.png, déjà en couleurs —
        pas de recoloriage mode sombre nécessaire, contrairement aux 5
        icônes de forme en noir plein)."""
        from modules.qt.font_loader import resource_path
        from PySide6.QtGui import QIcon
        path = resource_path("icons/pipette_blanche.png")
        pm = QPixmap(path)
        if not pm.isNull():
            self._pip_btn.setIcon(QIcon(pm))

    # ── Curseur pipette (repris de _LevelsOptionsPanel._build_pipette_cursor) ──

    @staticmethod
    def _build_pipette_cursor() -> QCursor:
        """Même géométrie que _LevelsOptionsPanel._build_pipette_cursor
        (icône 36×36 décalée en diagonale + croix de visée évidée, hotspot
        exact au centre du viseur) — réutilise pipette_noire.png comme icône
        neutre (cette pipette prélève une couleur, pas un point noir/blanc
        spécifique, mais l'icône générique reste visuellement cohérente avec
        celle déjà connue de l'outil niveaux)."""
        from modules.qt.font_loader import resource_path
        canvas_size = 56
        canvas = QPixmap(canvas_size, canvas_size)
        canvas.fill(Qt.transparent)
        margin = 20
        cross_x = margin
        cross_y = canvas_size - margin

        path = resource_path("icons/pipette_noire.png")
        icon_pixmap = QPixmap(path)
        painter = QPainter(canvas)
        if not icon_pixmap.isNull():
            icon_pixmap = icon_pixmap.scaled(
                36, 36, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            gap_to_icon = 4
            icon_x = cross_x + gap_to_icon
            icon_y = cross_y - gap_to_icon - icon_pixmap.height()
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

    # ── Thème / traduction ────────────────────────────────────────────────────

    def _apply_theme(self):
        theme = get_current_theme()
        self._load_shape_icons()
        self.setStyleSheet(floating_options_panel_style(theme, "_ShapeOptionsPanel"))
        for lbl in (self._lbl_thickness, self._lbl_color):
            lbl.setStyleSheet(f"color: {theme['text']}; background: transparent;")
        for sep in (self._sep1, self._sep2, self._sep3):
            sep.setStyleSheet(f"color: {theme['separator']};")
        accent = "#4a90d9"
        shape_btn_style = (
            f"QPushButton {{ background: {theme['bg']}; border: 1px solid #aaaaaa; }} "
            f"QPushButton:checked {{ background: {accent}; border: 1px solid {theme['text']}; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        for btn in self._shape_buttons.values():
            btn.setStyleSheet(shape_btn_style)
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
        self._thickness_slider.setStyleSheet(slider_style)
        self._thickness_spin.setStyleSheet(
            f"QSpinBox {{ background: {theme['bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 2px 4px; }} "
            f"QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}"
        )
        self._apply_color_btn_style(theme)
        # Plat, SANS cadre/fond au repos — icône seule, comme _ToolButton de
        # la barre principale plutôt qu'un carré vide avec bordure autour
        # d'un dessin déjà complet (icône pipette_blanche.png trop chargée
        # visuellement pour supporter un cadre en plus, retour utilisateur :
        # "pourquoi as-tu mis la pipette dans un putain de carré ?"). Léger
        # surlignage seulement au survol ou à l'état armé (checked).
        pip_style = (
            f"QPushButton {{ background: transparent; border: 1px solid transparent; "
            f"border-radius: 4px; }} "
            f"QPushButton:checked {{ background: {accent}; border: 1px solid {theme['text']}; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self._pip_btn.setStyleSheet(pip_style)
        # Indicateur ::indicator explicite (taille + bordure + coche image)
        # OBLIGATOIRE ici, contrairement à la QCheckBox "normale" de
        # resize_dialog_qt.py (une fenêtre QDialog sans WA_StyledBackground) :
        # sur CE panneau (WA_StyledBackground actif, voir __init__), un style
        # ne posant que background/color sur QCheckBox laisse l'indicateur
        # natif totalement invisible — même famille de piège que
        # _CloneOptionsPanel avec QRadioButton (voir docstring de module),
        # mais ici corrigée en stylant explicitement ::indicator au lieu de
        # remplacer le widget par un QPushButton checkable (une vraie case à
        # cocher reste la meilleure affordance visuelle pour "activer/
        # désactiver", demande explicite utilisateur). État coché = une vraie
        # coche (checkmark_white.png, générée en PIL) sur fond gris foncé FIXE
        # (ni la couleur d'accent bleue — retour utilisateur : un carré bleu
        # plein laissait croire à tort que le remplissage serait bleu — ni le
        # fond clair du thème, qui rendait la coche blanche quasi invisible
        # en thème clair, autre retour utilisateur) : un gris foncé neutre
        # reste lisible dans les deux thèmes sans évoquer une couleur de
        # remplissage réelle.
        from modules.qt.font_loader import resource_path
        checkmark_path = resource_path("icons/checkmark_white.png").replace("\\", "/")
        self._fill_enabled_cb.setStyleSheet(
            f"QCheckBox {{ background: transparent; color: {theme['text']}; spacing: 6px; }} "
            f"QCheckBox::indicator {{ width: 16px; height: 16px; "
            f"border: 1px solid {theme['text']}; border-radius: 3px; "
            f"background: {theme['bg']}; }} "
            f"QCheckBox::indicator:checked {{ background: #555555; "
            f"border: 1px solid {theme['text']}; "
            f"image: url({checkmark_path}); }} "
            f"QCheckBox::indicator:disabled {{ border: 1px solid {theme['separator']}; "
            f"background: {theme['separator']}; }}"
        )

    def _apply_color_btn_style(self, theme):
        self._color_btn.setStyleSheet(
            f"QPushButton {{ background: {self.color.name()}; "
            f"border: 2px solid {theme['text']}; border-radius: 3px; }}"
        )

    def retranslate(self):
        font = _get_current_font(11)
        tip = self._viewer._toolbar._overlay_tip
        for shape_type, btn in self._shape_buttons.items():
            btn.setFont(font)
            tip.track(btn, _(f"viewer.shape_type_{shape_type}"))
        self._lbl_thickness.setText(_("viewer.shape_thickness_label"))
        self._lbl_thickness.setFont(font)
        self._thickness_spin.setFont(font)
        self._lbl_color.setText(_("viewer.shape_color_label"))
        self._lbl_color.setFont(font)
        self._fill_enabled_cb.setText(_("viewer.shape_fill_enabled_label"))
        self._fill_enabled_cb.setFont(font)
        tip.track(self._color_btn, _("viewer.shape_color_tooltip"))
        tip.track(self._pip_btn, _("viewer.shape_pipette_tooltip"))

    # ── Visibilité ────────────────────────────────────────────────────────────

    def set_visible_for_tool(self, tool_id: str | None):
        if tool_id == "shapes":
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
        # Le curseur posé par shape_update_cursor (poignées de
        # redimensionnement/rotation) ou la pipette (idees.txt #4) est celui
        # du CANVAS, pas celui de ce panneau — sans ce reset, il restait
        # affiché par-dessus les contrôles du panneau, même piège corrigé sur
        # _TransparencyOptionsPanel/_LevelsOptionsPanel.
        self.setCursor(Qt.ArrowCursor)

    def leaveEvent(self, event):
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

    # ── Sélection de forme ───────────────────────────────────────────────────

    def _on_shape_type_toggled(self, checked: bool, shape_type: str):
        if checked:
            self.shape_type = shape_type
            self._update_fill_controls_enabled()
            # Cliquer sur un autre bouton du panneau doit IMPÉRATIVEMENT
            # désarmer une pipette restée active (retour utilisateur
            # explicite) — sinon le clic suivant sur l'image reste interprété
            # comme un prélèvement de couleur au lieu de tracer/sélectionner
            # une forme, incohérent avec le bouton visuellement relâché.
            self._deactivate_pipettes()

    def _update_fill_controls_enabled(self):
        enabled = self.shape_type not in _LINE_LIKE
        self._fill_enabled_cb.setEnabled(enabled)

    def _on_fill_enabled_toggled(self, checked: bool):
        self.fill_enabled = checked
        self._update_fill_controls_enabled()
        self._deactivate_pipettes()
        # Répercute sur la forme SÉLECTIONNÉE déjà tracée (pas encore
        # validée) — même principe que _on_thickness_changed/_set_color, qui
        # le faisaient déjà correctement. Manquait ici (bug vécu : cocher/
        # décocher "Remplissage" n'avait aucun effet visible tant qu'on ne
        # retraçait pas une nouvelle forme).
        active = self._viewer._canvas._shape_active
        if active is not None:
            active.fill_enabled = checked and active.shape_type not in _LINE_LIKE
            self._viewer._canvas.update()

    # ── Épaisseur ─────────────────────────────────────────────────────────────

    def _on_thickness_changed(self, value: int):
        self.thickness = value
        for widget in (self._thickness_slider, self._thickness_spin):
            if widget.value() != value:
                widget.blockSignals(True)
                widget.setValue(value)
                widget.blockSignals(False)
        active = self._viewer._canvas._shape_active
        if active is not None:
            active.thickness = value
            self._viewer._canvas.update()

    # ── Couleur ───────────────────────────────────────────────────────────────

    def _pick_color(self):
        self._deactivate_pipettes()
        dlg = _ColorPickerDialog(self._viewer, self.color)
        dlg.color_picked.connect(self._on_color_picked)
        dlg.adjustSize()
        from modules.qt.dialogs_qt import position_dialog_on_parent
        position_dialog_on_parent(dlg, self._viewer)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_color_picked(self, color: QColor):
        if not color.isValid():
            return
        self._set_color(color)

    def _set_color(self, color: QColor):
        theme = get_current_theme()
        self.color = QColor(color)
        self._apply_color_btn_style(theme)
        active = self._viewer._canvas._shape_active
        if active is not None:
            active.color = QColor(color)
            self._viewer._canvas.update()

    # ── Pipette ───────────────────────────────────────────────────────────────

    def _toggle_pipette(self):
        if self.pipette_active:
            self._deactivate_pipettes()
            return
        self.pipette_active = True
        self._pip_btn.setChecked(True)
        self._viewer._canvas.setCursor(self._cursor_pipette)

    def _deactivate_pipettes(self):
        self.pipette_active = False
        self._pip_btn.setChecked(False)
        self._viewer._canvas.setCursor(Qt.ArrowCursor)


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état et interactions souris de l'outil (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class ShapeCanvasMixin:
    """Hérité par _ViewerCanvas (image_viewer_qt.py) en plus de QLabel : ajoute
    l'état et les méthodes de l'outil "shapes" au canvas de la visionneuse,
    sans que leur code vive dans image_viewer_qt.py. Suppose que l'hôte a
    déjà self._viewer (ImageViewer) et les attributs habituels de
    _ViewerCanvas (display_offset_x/y, display_width/height).

    Formes = objets géométriques purs (_Shape), pas des widgets Qt enfants
    (contrairement au texte) — dessinées directement dans
    _ViewerCanvas.paintEvent via paint_shapes(painter)."""

    _CURSORS = {
        'tl': Qt.SizeFDiagCursor, 'br': Qt.SizeFDiagCursor,
        'tr': Qt.SizeBDiagCursor, 'bl': Qt.SizeBDiagCursor,
        'left': Qt.SizeHorCursor, 'right': Qt.SizeHorCursor,
        'top': Qt.SizeVerCursor,  'bottom': Qt.SizeVerCursor,
        'move': Qt.SizeAllCursor,
        'p1': Qt.CrossCursor, 'p2': Qt.CrossCursor,
        # PAS d'entrée 'rotate' ici : Qt n'a pas de curseur de rotation natif
        # cross-plateforme — voir _build_rotate_cursor() (module, curseur
        # custom dessiné en QPainter), résolu explicitement dans
        # shape_update_cursor plutôt que dans ce dict statique.
    }

    def _init_shape_state(self):
        self._shapes: list[_Shape] = []
        self._shape_active: _Shape | None = None
        # Tracé d'une NOUVELLE forme (clic sur zone vide, pas encore posée)
        self._shape_draw_start: QPoint | None = None
        self._shape_draw_end: QPoint | None = None
        # Redimensionnement/déplacement d'une forme EXISTANTE sélectionnée
        self._shape_resize_mode: str | None = None
        self._shape_resize_original: tuple | None = None
        self._shape_drag_start_widget: QPoint | None = None

    @property
    def has_shapes(self) -> bool:
        return bool(self._shapes)

    def clear_shapes(self):
        self._shapes.clear()
        self._shape_active = None
        self._shape_draw_start = None
        self._shape_draw_end = None
        self._shape_resize_mode = None
        self._shape_resize_original = None
        self.update()

    # ── Conversion widget <-> image ──────────────────────────────────────────

    def _shape_widget_to_image(self, pt: QPoint) -> tuple:
        zoom = self._viewer.zoom_level or 1.0
        ix = (pt.x() - self.display_offset_x) / zoom
        iy = (pt.y() - self.display_offset_y) / zoom
        return int(round(ix)), int(round(iy))

    def _shape_image_to_widget(self, ix: float, iy: float) -> QPoint:
        zoom = self._viewer.zoom_level or 1.0
        return QPoint(int(round(self.display_offset_x + ix * zoom)),
                       int(round(self.display_offset_y + iy * zoom)))

    def _shape_widget_rect(self, shape: _Shape) -> tuple:
        """(wx1,wy1,wx2,wy2) écran pour une forme, non normalisé (garde le
        sens de tracé — nécessaire pour la tête de flèche)."""
        p1 = self._shape_image_to_widget(shape.ix1, shape.iy1)
        p2 = self._shape_image_to_widget(shape.ix2, shape.iy2)
        return p1.x(), p1.y(), p2.x(), p2.y()

    def _shape_widget_center(self, shape: _Shape) -> QPoint:
        """Centre écran du rectangle englobant — pivot de rotation, stable
        quel que soit shape.angle (calculé sur la géométrie NON tournée)."""
        cix, ciy = shape.center_img()
        return self._shape_image_to_widget(cix, ciy)

    def _shape_rotate_point_around(self, pt: QPoint, center: QPoint, angle_deg: float) -> QPoint:
        """Fait pivoter pt autour de center de angle_deg (sens horaire, même
        convention que QPainter.rotate())."""
        if angle_deg == 0.0:
            return QPoint(pt.x(), pt.y())
        rad = math.radians(angle_deg)
        dx, dy = pt.x() - center.x(), pt.y() - center.y()
        rx = dx * math.cos(rad) - dy * math.sin(rad)
        ry = dx * math.sin(rad) + dy * math.cos(rad)
        return QPoint(int(round(center.x() + rx)), int(round(center.y() + ry)))

    # ── Rendu (appelé depuis _ViewerCanvas.paintEvent) ───────────────────────

    def paint_shapes(self, painter):
        """À appeler en fin de paintEvent, après l'image. Trait rouge (forme
        sélectionnée) / trait normal (forme posée non sélectionnée) — le
        contour utilise toujours la couleur de trait CHOISIE par
        l'utilisateur (pas un rouge de sélection à la crop/texte, une forme
        EST une couleur), la sélection se marque par les poignées + une fine
        surbrillance en pointillés autour du rectangle englobant."""
        shapes_active = self._viewer._toolbar.active_tool == "shapes"
        for shape in self._shapes:
            self._paint_one_shape(painter, shape)
            if shapes_active and shape is self._shape_active:
                self._paint_shape_handles(painter, shape)
        # Forme en cours de tracé (pas encore dans self._shapes)
        if self._shape_draw_start is not None and self._shape_draw_end is not None:
            panel = self._viewer._toolbar._shapes_panel
            preview = _Shape(
                panel.shape_type,
                *self._shape_widget_to_image(self._shape_draw_start),
                *self._shape_widget_to_image(self._shape_draw_end),
                panel.color, panel.fill_enabled, panel.thickness)
            self._paint_one_shape(painter, preview)

    def _paint_one_shape(self, painter, shape: _Shape):
        wx1, wy1, wx2, wy2 = self._shape_widget_rect(shape)
        pen = QPen(shape.color, max(1, shape.thickness))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if shape.fill_enabled:
            painter.setBrush(shape.color)
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)

        # Rotation (formes fermées uniquement, shape.angle toujours 0.0 pour
        # ligne/flèche — voir _Shape.__init__) : le contexte QPainter tourne
        # autour du centre écran de la forme, tout le dessin qui suit dans ce
        # bloc reste écrit en coordonnées NON tournées — Qt applique la
        # rotation automatiquement, aucun recalcul de géométrie nécessaire
        # (contrairement au rendu final PIL, voir ShapeViewerMixin.
        # _draw_one_shape, qui n'a pas cette facilité).
        rotated = shape.angle != 0.0
        if rotated:
            center = self._shape_widget_center(shape)
            painter.save()
            painter.translate(center)
            painter.rotate(shape.angle)
            painter.translate(-center.x(), -center.y())

        if shape.shape_type == "ellipse":
            rx1, ry1 = min(wx1, wx2), min(wy1, wy2)
            rx2, ry2 = max(wx1, wx2), max(wy1, wy2)
            painter.drawEllipse(QRect(QPoint(rx1, ry1), QPoint(rx2, ry2)))
        elif shape.shape_type == "rectangle":
            rx1, ry1 = min(wx1, wx2), min(wy1, wy2)
            rx2, ry2 = max(wx1, wx2), max(wy1, wy2)
            painter.drawRect(QRect(QPoint(rx1, ry1), QPoint(rx2, ry2)))
        elif shape.shape_type == "rounded_rectangle":
            rx1, ry1 = min(wx1, wx2), min(wy1, wy2)
            rx2, ry2 = max(wx1, wx2), max(wy1, wy2)
            zoom = self._viewer.zoom_level or 1.0
            radius = max(2, int(_ROUNDED_RADIUS_IMG * zoom))
            painter.drawRoundedRect(QRect(QPoint(rx1, ry1), QPoint(rx2, ry2)), radius, radius)
        elif shape.shape_type == "line":
            painter.drawLine(wx1, wy1, wx2, wy2)
        elif shape.shape_type == "arrow":
            zoom = self._viewer.zoom_level or 1.0
            head_len = _arrow_head_len(thickness=shape.thickness, zoom=zoom)
            angle = math.atan2(wy2 - wy1, wx2 - wx1)
            # Le corps du trait s'arrête AVANT la base du triangle de la
            # tête (pas jusqu'à la pointe x2,y2) — un trait épais dont le
            # bout rond/carré atteint la pointe déborde visuellement des
            # côtés du triangle et "avale" la tête, la rendant méconnaissable
            # dès que thickness dépasse la largeur de sa base (bug vécu :
            # pointe de flèche disparue en épaississant le trait). MÊME
            # head_len que _paint_arrow_head (fonction module partagée
            # _arrow_head_len, pas deux calculs séparés) — un écart entre les
            # deux faisait déborder le corps DANS la tête d'un côté,
            # décalant visuellement l'apparence de la pointe (bug vécu).
            body_end_x = wx2 - head_len * 0.85 * math.cos(angle)
            body_end_y = wy2 - head_len * 0.85 * math.sin(angle)
            painter.drawLine(int(wx1), int(wy1), int(body_end_x), int(body_end_y))
            self._paint_arrow_head(painter, wx1, wy1, wx2, wy2, shape.color, shape.thickness)

        if rotated:
            painter.restore()

        # Grisage de la portion hors des limites de la page (idees.txt #1,
        # décision explicite utilisateur, s'applique aussi à cet outil) —
        # uniquement pour les formes FERMÉES (ellipse/rectangle/rectangle
        # arrondi) : ligne/flèche n'ont pas de zone surfacique dont "la
        # partie hors de l'image principale" aurait un sens visuel comparable
        # (angle toujours 0.0 pour elles, voir _Shape.__init__ — rien à
        # tourner ici non plus). Coins en repère ÉCRAN NON tourné, indépendant
        # du save/translate/rotate déjà refermé ci-dessus.
        if not shape.is_line_like():
            rx1, ry1 = min(wx1, wx2), min(wy1, wy2)
            rx2, ry2 = max(wx1, wx2), max(wy1, wy2)
            if rotated:
                center = self._shape_widget_center(shape)
                corners = [self._shape_rotate_point_around(QPoint(x, y), center, shape.angle)
                           for (x, y) in ((rx1, ry1), (rx2, ry1), (rx2, ry2), (rx1, ry2))]
            else:
                corners = [QPoint(rx1, ry1), QPoint(rx2, ry1), QPoint(rx2, ry2), QPoint(rx1, ry2)]
            _paint_out_of_page_bounds_overlay(painter, self, corners)

    def _paint_arrow_head(self, painter, x1, y1, x2, y2, color, thickness):
        angle = math.atan2(y2 - y1, x2 - x1)
        zoom = self._viewer.zoom_level or 1.0
        head_len = _arrow_head_len(thickness=thickness, zoom=zoom)
        p1 = QPoint(
            int(x2 - head_len * math.cos(angle - _ARROW_HEAD_ANGLE)),
            int(y2 - head_len * math.sin(angle - _ARROW_HEAD_ANGLE)))
        p2 = QPoint(
            int(x2 - head_len * math.cos(angle + _ARROW_HEAD_ANGLE)),
            int(y2 - head_len * math.sin(angle + _ARROW_HEAD_ANGLE)))
        from PySide6.QtGui import QPolygon, QBrush
        head = QPolygon([QPoint(int(x2), int(y2)), p1, p2])
        painter.save()
        painter.setPen(QPen(color, 1))
        painter.setBrush(QBrush(color))
        painter.drawPolygon(head)
        painter.restore()

    def _paint_shape_handles(self, painter, shape: _Shape):
        wx1, wy1, wx2, wy2 = self._shape_widget_rect(shape)
        handle_pen = QPen(QColor("red"), 1)
        painter.save()
        painter.setPen(handle_pen)
        painter.setBrush(QColor(255, 255, 255, 220))
        size = 6
        if shape.is_line_like():
            for hx, hy in ((wx1, wy1), (wx2, wy2)):
                painter.drawRect(hx - size // 2, hy - size // 2, size, size)
        else:
            # Même rotation que _paint_one_shape (painter.rotate() autour du
            # centre) : les 8 poignées de redimensionnement + la poignée de
            # rotation sont dessinées en coordonnées NON tournées dans ce
            # bloc, Qt les tourne visuellement avec la forme — sinon elles
            # resteraient "fausses" (alignées sur les axes écran) dès que
            # shape.angle != 0, décrochées du contour visible de la forme.
            rotated = shape.angle != 0.0
            if rotated:
                center = self._shape_widget_center(shape)
                painter.translate(center)
                painter.rotate(shape.angle)
                painter.translate(-center.x(), -center.y())

            rx1, ry1 = min(wx1, wx2), min(wy1, wy2)
            rx2, ry2 = max(wx1, wx2), max(wy1, wy2)
            dash_pen = QPen(QColor("red"), 1, Qt.PenStyle.DashLine)
            painter.setPen(dash_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRect(QPoint(rx1, ry1), QPoint(rx2, ry2)))
            painter.setPen(handle_pen)
            painter.setBrush(QColor(255, 255, 255, 220))
            mx, my = (rx1 + rx2) // 2, (ry1 + ry2) // 2
            for hx, hy in ((rx1, ry1), (mx, ry1), (rx2, ry1),
                           (rx1, my), (rx2, my),
                           (rx1, ry2), (mx, ry2), (rx2, ry2)):
                painter.drawRect(hx - size // 2, hy - size // 2, size, size)

            # Poignée de rotation : petit CERCLE (pas un carré, pour la
            # distinguer visuellement des poignées de redimensionnement)
            # relié par un trait fin au milieu du bord haut, à
            # _ROTATE_HANDLE_OFFSET pixels au-dessus — convention standard
            # (PowerPoint/Illustrator/Figma), pas ambiguë avec le
            # redimensionnement puisque c'est une poignée séparée.
            handle_y = ry1 - _ROTATE_HANDLE_OFFSET
            painter.setPen(handle_pen)
            painter.drawLine(mx, ry1, mx, handle_y)
            painter.setBrush(QColor(255, 255, 255, 220))
            r = size // 2 + 1
            painter.drawEllipse(QPoint(mx, handle_y), r, r)
        painter.restore()

    # ── Détection de zone (poignées / intérieur) ─────────────────────────────

    def _shape_resize_mode_at(self, shape: _Shape, pos: QPoint, check_rotate: bool = True) -> str | None:
        """check_rotate=False désactive la détection de la poignée de
        rotation — la poignée n'est dessinée QUE sur la forme SÉLECTIONNÉE
        (_paint_shape_handles), donc _shape_at() (qui sert à repérer une
        forme à SÉLECTIONNER parmi celles pas encore actives) ne doit jamais
        matcher la poignée de rotation d'une forme dont elle n'est pas
        affichée. Les 8 poignées de coin/bord restent volontairement
        détectables même hors sélection : comportement préexistant, inchangé
        par cet ajout (même principe que crop/texte)."""
        wx1, wy1, wx2, wy2 = self._shape_widget_rect(shape)
        tolerance = 10
        x, y = pos.x(), pos.y()
        if shape.is_line_like():
            if (x - wx1) ** 2 + (y - wy1) ** 2 <= tolerance ** 2:
                return 'p1'
            if (x - wx2) ** 2 + (y - wy2) ** 2 <= tolerance ** 2:
                return 'p2'
            # Distance point-segment, pour permettre le déplacement de toute
            # la ligne en cliquant dessus (pas seulement ses 2 extrémités).
            if self._point_near_segment(x, y, wx1, wy1, wx2, wy2, tolerance + shape.thickness):
                return 'move'
            return None

        rx1, ry1 = min(wx1, wx2), min(wy1, wy2)
        rx2, ry2 = max(wx1, wx2), max(wy1, wy2)

        # Poignée de rotation : détectée AVANT dé-rotation du clic (sa
        # position affichée est déjà tournée avec la forme dans
        # _paint_shape_handles, donc on compare le clic BRUT à sa position
        # écran réelle une fois tournée elle aussi). check_rotate=False pour
        # une forme pas encore sélectionnée (voir docstring).
        mx = (rx1 + rx2) // 2
        if check_rotate:
            handle_center = self._shape_rotate_point_around(
                QPoint(mx, ry1 - _ROTATE_HANDLE_OFFSET),
                self._shape_widget_center(shape), shape.angle)
            if (x - handle_center.x()) ** 2 + (y - handle_center.y()) ** 2 <= tolerance ** 2:
                return 'rotate'

        # Dé-rotation du point cliqué (rotation INVERSE, -shape.angle, autour
        # du même centre) avant de comparer aux 8 zones de poignées/intérieur
        # définies en coordonnées NON tournées — sinon, dès que shape.angle
        # != 0, un clic sur le contour visible (tourné) ne matchait plus
        # aucune des zones (calculées sur le rectangle droit d'origine).
        if shape.angle != 0.0:
            center = self._shape_widget_center(shape)
            local = self._shape_rotate_point_around(pos, center, -shape.angle)
            x, y = local.x(), local.y()

        if abs(x - rx1) <= tolerance and abs(y - ry1) <= tolerance: return 'tl'
        if abs(x - rx2) <= tolerance and abs(y - ry1) <= tolerance: return 'tr'
        if abs(x - rx1) <= tolerance and abs(y - ry2) <= tolerance: return 'bl'
        if abs(x - rx2) <= tolerance and abs(y - ry2) <= tolerance: return 'br'
        if abs(x - rx1) <= tolerance and ry1 <= y <= ry2: return 'left'
        if abs(x - rx2) <= tolerance and ry1 <= y <= ry2: return 'right'
        if abs(y - ry1) <= tolerance and rx1 <= x <= rx2: return 'top'
        if abs(y - ry2) <= tolerance and rx1 <= x <= rx2: return 'bottom'
        if rx1 < x < rx2 and ry1 < y < ry2: return 'move'
        return None

    @staticmethod
    def _point_near_segment(px, py, x1, y1, x2, y2, tolerance) -> bool:
        dx, dy = x2 - x1, y2 - y1
        length_sq = dx * dx + dy * dy
        if length_sq == 0:
            dist = math.hypot(px - x1, py - y1)
        else:
            t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length_sq))
            proj_x, proj_y = x1 + t * dx, y1 + t * dy
            dist = math.hypot(px - proj_x, py - proj_y)
        return dist <= tolerance

    def _shape_at(self, pos: QPoint) -> "_Shape | None":
        for shape in reversed(self._shapes):
            # check_rotate=False : la poignée de rotation d'une forme qui
            # n'est pas encore sélectionnée n'est jamais dessinée
            # (_paint_shape_handles), donc jamais cliquable ici non plus.
            if self._shape_resize_mode_at(shape, pos, check_rotate=False) is not None:
                return shape
        return None

    # ── Événements souris (appelés depuis _ViewerCanvas.mousePress/Move/ReleaseEvent) ──

    def shape_mouse_press(self, event) -> bool:
        pos = event.position().toPoint()

        if self._shape_active is not None:
            mode = self._shape_resize_mode_at(self._shape_active, pos)
            if mode is not None:
                self._shape_resize_mode = mode
                self._shape_resize_original = (
                    self._shape_active.ix1, self._shape_active.iy1,
                    self._shape_active.ix2, self._shape_active.iy2,
                    self._shape_active.angle)
                self._shape_drag_start_widget = pos
                return True

        hit = self._shape_at(pos)
        if hit is not None:
            self._shape_active = hit
            panel = self._viewer._toolbar._shapes_panel
            # Resynchronise TOUS les contrôles du panneau sur la forme
            # cliquée (type, épaisseur, couleur, remplissage) — pas
            # seulement type/épaisseur : sans ça, sélectionner une forme
            # existante laissait les boutons couleur/remplissage affichant
            # encore les valeurs de la forme précédemment éditée, incohérent
            # avec ce qui est réellement modifiable (les setters _set_color/
            # _on_thickness_changed n'agissent que sur panel.color/thickness,
            # qui doivent donc déjà refléter la forme active avant toute
            # nouvelle modification).
            panel.shape_type = hit.shape_type
            panel._shape_buttons[hit.shape_type].setChecked(True)
            panel.thickness = hit.thickness
            panel._thickness_slider.blockSignals(True)
            panel._thickness_slider.setValue(hit.thickness)
            panel._thickness_slider.blockSignals(False)
            panel._thickness_spin.blockSignals(True)
            panel._thickness_spin.setValue(hit.thickness)
            panel._thickness_spin.blockSignals(False)
            panel.color = QColor(hit.color)
            panel.fill_enabled = hit.fill_enabled
            panel._fill_enabled_cb.blockSignals(True)
            panel._fill_enabled_cb.setChecked(hit.fill_enabled)
            panel._fill_enabled_cb.blockSignals(False)
            panel._update_fill_controls_enabled()
            theme = get_current_theme()
            panel._apply_color_btn_style(theme)
            self._viewer._on_shapes_content_changed()
            mode = self._shape_resize_mode_at(hit, pos)
            self._shape_resize_mode = mode
            self._shape_resize_original = (hit.ix1, hit.iy1, hit.ix2, hit.iy2, hit.angle)
            self._shape_drag_start_widget = pos
            self.update()
            return True

        # Zone vide : démarre le tracé d'une nouvelle forme, désélectionne
        # l'ancienne (poignées masquées).
        self._shape_active = None
        self._shape_draw_start = pos
        self._shape_draw_end = None
        self.update()
        return True

    def shape_mouse_move(self, event) -> bool:
        pos = event.position().toPoint()

        if self._shape_resize_mode and self._shape_active is not None and self._shape_resize_original:
            self._apply_shape_resize(pos)
            self.update()
            return True

        if self._shape_draw_start is not None:
            self._shape_draw_end = pos
            self.update()
            return True

        return False

    def _apply_shape_resize(self, pos: QPoint):
        shape = self._shape_active
        ox1, oy1, ox2, oy2, oangle = self._shape_resize_original
        rm = self._shape_resize_mode

        if rm == 'rotate':
            # Angle entre le centre ÉCRAN et la souris, moins l'angle
            # mesuré au moment du clic initial (self._shape_drag_start_widget)
            # — delta appliqué à l'angle D'ORIGINE (oangle) pour un drag
            # relatif fluide (la forme ne "saute" pas à l'angle absolu du
            # premier mouvement de souris, elle continue depuis où elle en
            # était). atan2 en coordonnées écran (y vers le bas) correspond
            # déjà au sens horaire de QPainter.rotate(), aucune inversion de
            # signe nécessaire.
            center = self._shape_widget_center(shape)
            start = self._shape_drag_start_widget
            start_angle = math.degrees(math.atan2(start.y() - center.y(), start.x() - center.x()))
            current_angle = math.degrees(math.atan2(pos.y() - center.y(), pos.x() - center.x()))
            shape.angle = (oangle + (current_angle - start_angle)) % 360.0
            return

        ix, iy = self._shape_widget_to_image(pos)

        if rm == 'move':
            start_ix, start_iy = self._shape_widget_to_image(self._shape_drag_start_widget)
            dx, dy = ix - start_ix, iy - start_iy
            shape.ix1, shape.iy1 = ox1 + dx, oy1 + dy
            shape.ix2, shape.iy2 = ox2 + dx, oy2 + dy
            return

        if shape.is_line_like():
            if rm == 'p1':
                shape.ix1, shape.iy1 = ix, iy
            elif rm == 'p2':
                shape.ix2, shape.iy2 = ix, iy
            return

        # Redimensionnement d'une forme tournée : le point souris (écran) est
        # d'abord DÉ-TOURNÉ (rotation inverse autour du centre écran) pour
        # revenir dans le référentiel non tourné où les coins/bords sont
        # définis — sans ça, tirer un coin d'une forme tournée déplacerait le
        # coin dans la mauvaise direction (celle de l'écran, pas celle,
        # tournée, de la forme).
        if shape.angle != 0.0:
            center = self._shape_widget_center(shape)
            local = self._shape_rotate_point_around(pos, center, -shape.angle)
            ix, iy = self._shape_widget_to_image(local)

        # Poignées de COIN : redimensionnement à PROPORTIONS CONSERVÉES
        # (idees.txt #1, décision explicite utilisateur, s'applique aussi à
        # cet outil formes) — seules les 4 poignées de BORD (milieu, 'left'/
        # 'right'/'top'/'bottom') redimensionnent librement en déformant.
        # Le coin opposé à celui tiré reste FIXE (pivot), le ratio largeur/
        # hauteur d'ORIGINE (avant ce drag, ox2-ox1 / oy2-oy1) est réappliqué
        # à chaque mouvement en ne conservant que la plus grande des deux
        # variations (largeur ou hauteur) demandées par la souris — évite un
        # comportement erratique où la forme se met à "rétrécir" dans un axe
        # dès que la souris s'approche du coin fixe.
        if rm in ('tl', 'tr', 'bl', 'br'):
            orig_w = abs(ox2 - ox1) or 1.0
            orig_h = abs(oy2 - oy1) or 1.0
            aspect = orig_w / orig_h
            # Coin FIXE = celui opposé à celui tiré (pivot du redimensionnement).
            fixed_x = ox1 if rm in ('tr', 'br') else ox2
            fixed_y = oy1 if rm in ('bl', 'br') else oy2
            # Direction (signe) de chaque axe déterminée par la position
            # SOURIS par rapport au coin fixe — la magnitude, elle, est
            # imposée par le ratio d'origine (seule la plus grande des deux
            # variations demandées par la souris est retenue, l'autre axe en
            # découle) : évite un comportement erratique où la forme se met
            # à "rétrécir" dans un axe dès que la souris s'approche du coin
            # fixe sur l'autre axe.
            raw_w, raw_h = ix - fixed_x, iy - fixed_y
            sign_x = -1 if rm in ('tl', 'bl') else 1
            sign_y = -1 if rm in ('tl', 'tr') else 1
            if abs(raw_w) >= abs(raw_h) * aspect:
                new_w = max(1.0, abs(raw_w))
                new_h = new_w / aspect
            else:
                new_h = max(1.0, abs(raw_h))
                new_w = new_h * aspect
            x1, y1, x2, y2 = ox1, oy1, ox2, oy2
            if rm == 'tl':   x1, y1 = fixed_x + sign_x * new_w, fixed_y + sign_y * new_h
            elif rm == 'tr': x2, y1 = fixed_x + sign_x * new_w, fixed_y + sign_y * new_h
            elif rm == 'bl': x1, y2 = fixed_x + sign_x * new_w, fixed_y + sign_y * new_h
            elif rm == 'br': x2, y2 = fixed_x + sign_x * new_w, fixed_y + sign_y * new_h
            shape.ix1, shape.iy1, shape.ix2, shape.iy2 = x1, y1, x2, y2
            return

        x1, y1, x2, y2 = ox1, oy1, ox2, oy2
        if rm == 'left':   x1 = ix
        elif rm == 'right':  x2 = ix
        elif rm == 'top':    y1 = iy
        elif rm == 'bottom': y2 = iy
        shape.ix1, shape.iy1, shape.ix2, shape.iy2 = x1, y1, x2, y2

    def shape_update_cursor(self, event):
        pos = event.position().toPoint()
        panel = self._viewer._toolbar._shapes_panel
        if panel.pipette_active:
            return
        mode = None
        if self._shape_active is not None:
            mode = self._shape_resize_mode_at(self._shape_active, pos)
        if mode is None:
            hit = self._shape_at(pos)
            if hit is not None:
                # check_rotate=False : hit n'est pas forcément la forme
                # active (ses poignées, dont la rotation, ne sont pas
                # dessinées) — même garde que _shape_at() lui-même.
                mode = self._shape_resize_mode_at(hit, pos, check_rotate=False)
        if mode == 'rotate':
            self.setCursor(_build_rotate_cursor())
        else:
            self.setCursor(QCursor(self._CURSORS.get(mode, Qt.ArrowCursor)))

    def shape_mouse_release(self, event) -> bool:
        handled = False

        if self._shape_resize_mode:
            self._shape_resize_mode = None
            self._shape_resize_original = None
            self._shape_drag_start_widget = None
            self._viewer._on_shapes_content_changed()
            handled = True

        elif self._shape_draw_start is not None:
            pos = event.position().toPoint()
            if self._shape_draw_end is None:
                self._shape_draw_end = pos
            wx1, wy1 = self._shape_draw_start.x(), self._shape_draw_start.y()
            wx2, wy2 = self._shape_draw_end.x(), self._shape_draw_end.y()
            distance = math.hypot(wx2 - wx1, wy2 - wy1)
            if distance >= 6:
                panel = self._viewer._toolbar._shapes_panel
                ix1, iy1 = self._shape_widget_to_image(self._shape_draw_start)
                ix2, iy2 = self._shape_widget_to_image(self._shape_draw_end)
                shape = _Shape(
                    panel.shape_type, ix1, iy1, ix2, iy2,
                    panel.color, panel.fill_enabled, panel.thickness)
                self._shapes.append(shape)
                self._shape_active = shape
                self._viewer._on_shapes_content_changed()
            self._shape_draw_start = None
            self._shape_draw_end = None
            handled = True
            self.update()

        return handled

    def shapes_pipette_click(self, event):
        """Prélève la couleur RGBA du pixel cliqué sur la page affichée dans
        la visionneuse (pas l'écran) et l'écrit dans la couleur (unique,
        trait+remplissage) — mêmes calculs écran→image que
        LevelsCanvasMixin.levels_pipette_click, réutilisés tels quels."""
        panel = self._viewer._toolbar._shapes_panel
        if not panel.pipette_active:
            return

        import io
        from modules.qt import state as _state_module
        state = self._viewer.callbacks.get('state') or _state_module.state
        entry = state.images_data[self._viewer.current_idx]
        if not entry.get('bytes'):
            return
        if self.display_width <= 0 or self.display_height <= 0:
            return

        pos = event.position().toPoint()
        try:
            img = Image.open(io.BytesIO(entry['bytes'])).convert("RGBA")
        except Exception:
            return
        img_w, img_h = img.size
        img_x = int((pos.x() - self.display_offset_x) * img_w / self.display_width)
        img_y = int((pos.y() - self.display_offset_y) * img_h / self.display_height)
        if img_x < 0 or img_y < 0 or img_x >= img_w or img_y >= img_h:
            return
        try:
            r, g, b, a = img.getpixel((img_x, img_y))
        except Exception:
            return
        color = QColor(r, g, b, 255)
        panel._set_color(color)


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — rendu / commit / persistance par page (hérité par ImageViewer)
# ─────────────────────────────────────────────────────────────────────────────

class ShapeViewerMixin:
    """Hérité par ImageViewer (image_viewer_qt.py) en plus de QDialog : ajoute
    la logique de rendu/validation/persistance de l'outil "shapes" au viewer,
    sans que son code vive dans image_viewer_qt.py. Suppose que l'hôte a déjà
    self._canvas (_ViewerCanvas, avec ShapeCanvasMixin), self.callbacks,
    self.current_idx, self._toolbar (avec _shapes_panel), et
    self._shapes_by_page (persistance par page, initialisée dans
    ImageViewer.__init__ comme _text_blocks_by_page)."""

    def _on_shapes_content_changed(self):
        """Recalcule seulement l'état actif/grisé du bouton "Valider" partagé
        (selon has_shapes) — ne touche JAMAIS à sa visibilité, pilotée
        uniquement par _ViewerToolbar.show_and_schedule_hide/_on_hide_timeout
        (mécanisme unique, voir image_viewer_qt.py::_update_validate_btn_state).
        Bouton "Annuler" jumeau rafraîchi juste à côté (2026-08-15)."""
        if self._toolbar.active_tool == "shapes":
            self._canvas._update_validate_btn_state()
            self._canvas._update_cancel_btn_state()

    # ── Rendu final — toutes les formes → PIL.ImageDraw ──────────────────────

    def _shapes_render_all(self, base_img: Image.Image) -> Image.Image:
        img = base_img.copy()
        for shape in self._canvas._shapes:
            if shape.angle != 0.0:
                # PIL.ImageDraw n'a pas d'équivalent de painter.rotate() : la
                # forme est dessinée NON tournée sur un calque RGBA
                # temporaire dédié (juste assez grand pour la contenir, marge
                # incluse pour ne pas rogner le contour aux bords), le calque
                # est pivoté (Image.rotate(expand=True), qui agrandit le
                # canvas pour ne rien couper), puis collé sur l'image finale
                # recentré sur le MÊME centre que la forme non tournée — la
                # rotation d'une image autour de son propre centre déplace
                # naturellement ce centre au milieu du nouveau canvas agrandi,
                # donc aligner les deux centres suffit à repositionner
                # correctement le résultat tourné (même principe déjà éprouvé
                # pour le rendu du texte riche, cf. text_tool_qt.py, mais PIL
                # pur ici plutôt que QImage/QPainter).
                self._draw_rotated_shape(img, shape)
            else:
                draw = ImageDraw.Draw(img, "RGBA")
                self._draw_one_shape(draw, shape)
        return img

    @staticmethod
    def _draw_rotated_shape(img: Image.Image, shape: _Shape):
        x1, y1, x2, y2 = shape.normalized_img_rect()
        cx, cy = shape.center_img()
        # Marge = épaisseur du trait (le contour déborde du rectangle
        # englobant de la moitié de son épaisseur de chaque côté) + un peu
        # de jeu pour l'anticrénelage — sans cette marge, le contour serait
        # rogné au bord du calque avant même la rotation.
        margin = max(4, shape.thickness)
        layer_w = int(x2 - x1) + margin * 2
        layer_h = int(y2 - y1) + margin * 2
        if layer_w <= 0 or layer_h <= 0:
            return
        layer = Image.new("RGBA", (layer_w, layer_h), (0, 0, 0, 0))
        layer_draw = ImageDraw.Draw(layer, "RGBA")
        # Forme redessinée en coordonnées LOCALES au calque (translation de
        # -x1+margin/-y1+margin) — un clone léger de shape avec des
        # coordonnées décalées, pour réutiliser _draw_one_shape tel quel sans
        # dupliquer sa logique de dessin par type de forme.
        local = _Shape(
            shape.shape_type,
            int(shape.ix1 - x1 + margin), int(shape.iy1 - y1 + margin),
            int(shape.ix2 - x1 + margin), int(shape.iy2 - y1 + margin),
            shape.color, shape.fill_enabled, shape.thickness)
        ShapeViewerMixin._draw_one_shape(layer_draw, local)

        # PIL.Image.rotate() tourne dans le sens ANTI-horaire pour un angle
        # positif — l'inverse de QPainter.rotate() (sens horaire, convention
        # utilisée pour shape.angle, voir ShapeCanvasMixin.
        # _shape_rotate_point_around) : angle signe opposé pour que le rendu
        # final tourne dans le MÊME sens que ce qui a été validé à l'écran.
        rotated = layer.rotate(-shape.angle, expand=True, resample=Image.BICUBIC)

        # Le centre du calque tourné (agrandi par expand=True) correspond au
        # même point image que (cx, cy) — recoller en alignant les deux
        # centres repositionne correctement le résultat, quel que soit
        # l'angle.
        paste_x = int(round(cx - rotated.width / 2))
        paste_y = int(round(cy - rotated.height / 2))
        img.paste(rotated, (paste_x, paste_y), rotated)

    @staticmethod
    def _draw_one_shape(draw: "ImageDraw.ImageDraw", shape: _Shape):
        thickness = max(1, shape.thickness)
        stroke_rgba = (shape.color.red(), shape.color.green(),
                       shape.color.blue(), shape.color.alpha())
        fill_rgba = stroke_rgba if shape.fill_enabled else None

        if shape.shape_type == "ellipse":
            x1, y1, x2, y2 = shape.normalized_img_rect()
            draw.ellipse((x1, y1, x2, y2), fill=fill_rgba, outline=stroke_rgba, width=thickness)
        elif shape.shape_type == "rectangle":
            x1, y1, x2, y2 = shape.normalized_img_rect()
            draw.rectangle((x1, y1, x2, y2), fill=fill_rgba, outline=stroke_rgba, width=thickness)
        elif shape.shape_type == "rounded_rectangle":
            x1, y1, x2, y2 = shape.normalized_img_rect()
            radius = max(1, min(_ROUNDED_RADIUS_IMG, (x2 - x1) // 2, (y2 - y1) // 2))
            draw.rounded_rectangle((x1, y1, x2, y2), radius=radius,
                                    fill=fill_rgba, outline=stroke_rgba, width=thickness)
        elif shape.shape_type == "line":
            draw.line((shape.ix1, shape.iy1, shape.ix2, shape.iy2),
                       fill=stroke_rgba, width=thickness)
        elif shape.shape_type == "arrow":
            angle = math.atan2(shape.iy2 - shape.iy1, shape.ix2 - shape.ix1)
            # head_len : MÊME fonction que le rendu écran (_arrow_head_len,
            # zoom=1.0 ici car ce rendu travaille déjà en coordonnées image)
            # — pour que le rendu final aplati corresponde exactement à ce
            # qui a été validé visuellement (un écart entre deux calculs
            # séparés faisait déborder le corps dans la tête, bug vécu).
            head_len = _arrow_head_len(thickness=thickness, zoom=1.0)
            body_end_x = shape.ix2 - head_len * 0.85 * math.cos(angle)
            body_end_y = shape.iy2 - head_len * 0.85 * math.sin(angle)
            draw.line((shape.ix1, shape.iy1, body_end_x, body_end_y),
                       fill=stroke_rgba, width=thickness)
            p1 = (shape.ix2 - head_len * math.cos(angle - _ARROW_HEAD_ANGLE),
                  shape.iy2 - head_len * math.sin(angle - _ARROW_HEAD_ANGLE))
            p2 = (shape.ix2 - head_len * math.cos(angle + _ARROW_HEAD_ANGLE),
                  shape.iy2 - head_len * math.sin(angle + _ARROW_HEAD_ANGLE))
            draw.polygon([(shape.ix2, shape.iy2), p1, p2], fill=stroke_rgba)

    def validate_shapes(self):
        from modules.qt.dialogs_qt import MsgDialog
        if not self._canvas.has_shapes:
            dlg = MsgDialog(
                self,
                "messages.warnings.no_shape.title",
                "messages.warnings.no_shape.message",
            )
            dlg.show_nonmodal()
            return
        self.perform_shapes()

    def perform_shapes(self):
        import io as _io
        from modules.qt import state as _state_module
        from modules.qt.entries import save_image_to_bytes
        from modules.qt.dialogs_qt import MsgDialog

        state = self.callbacks.get('state') or _state_module.state
        save_state    = self.callbacks.get("save_state")
        render_mosaic = self.callbacks.get("render_mosaic")
        update_btn    = self.callbacks.get("update_button_text")
        canvas_cb     = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]
            if not entry.get('bytes'):
                dlg = MsgDialog(self._center_parent, "messages.errors.shapes_failed.title",
                                "messages.errors.shapes_failed.title")
                dlg.show_nonmodal()
                return

            base_img_raw = Image.open(_io.BytesIO(entry['bytes']))
            if '_orig_mode' not in entry:
                entry['_orig_mode'] = base_img_raw.mode
            base_img = base_img_raw.convert('RGBA')

            if save_state:
                save_state()

            composed = self._shapes_render_all(base_img)

            orig_mode = entry.get('_orig_mode', 'RGBA')
            out_img = composed
            if orig_mode not in ('RGBA', 'LA', 'P') and \
                    entry.get('extension', '').lower() not in ('.png', '.webp', '.avif'):
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

            self._canvas.clear_shapes()
            self._shapes_by_page.pop(self.current_idx, None)
            self.display_image(keep_crop_rect=True)
            self._toolbar.refresh_undo_redo_state()
            # Recalcule l'état vert/actif ↔ gris/inactif du bouton "Valider"
            # partagé maintenant que has_shapes vient de repasser à False
            # (clear_shapes() juste au-dessus) — sans cet appel, le bouton
            # restait vert après validation alors qu'il n'y a plus rien à
            # valider (bug vécu : _update_validate_btn_state() n'est
            # normalement rappelé qu'au clic sur une forme/à un changement de
            # sélection, jamais après un clear_shapes() programmatique).
            self._on_shapes_content_changed()

        except Exception:
            dlg = MsgDialog(self._center_parent, "messages.errors.shapes_failed.title",
                            "messages.errors.shapes_failed.title")
            dlg.show_nonmodal()

    # ── Persistance par page ──────────────────────────────────────────────────

    def _save_shapes_for_current_page(self):
        shapes = self._canvas._shapes
        if shapes:
            self._shapes_by_page[self.current_idx] = [
                (s.shape_type, s.ix1, s.iy1, s.ix2, s.iy2,
                 s.color.name(QColor.NameFormat.HexArgb),
                 s.fill_enabled, s.thickness, s.angle)
                for s in shapes
            ]
        else:
            self._shapes_by_page.pop(self.current_idx, None)

    def _restore_shapes_for_page(self, idx: int):
        self._canvas.clear_shapes()
        saved = self._shapes_by_page.get(idx)
        if not saved:
            return
        for (shape_type, ix1, iy1, ix2, iy2, color_hex,
             fill_enabled, thickness, angle) in saved:
            shape = _Shape(
                shape_type, ix1, iy1, ix2, iy2,
                QColor(color_hex), fill_enabled, thickness, angle=angle)
            self._canvas._shapes.append(shape)
        self._canvas.update()
