"""
modules/qt/image_viewer_qt.py — Visionneuse d'images (version PySide6)

Reproduit à l'identique le comportement de modules/image_viewer.py (tkinter).
"""

import io

from PIL import Image

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QMenu, QFrame, QDoubleSpinBox,
)
from PySide6.QtCore import Qt, QTimer, QPoint, QElapsedTimer
from PySide6.QtGui import QPixmap, QImage, QKeySequence, QShortcut

from modules.qt import state as _state_module
from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.entries import (
    ensure_image_loaded, free_image_memory, get_gif_frame,
)
from modules.qt.dialogs_qt import MsgDialog, ConfirmYNDialog
from modules.qt.page_detection import compute_reference_ratio
# Outils de la barre d'outils flottante — code dans leur propre module,
# jamais dans ce fichier (CLAUDE.md, fusion des visionneuses idees.txt #3).
from modules.qt.crop_tool_qt import CropCanvasMixin, CropViewerMixin
from modules.qt.straighten_tool_qt import StraightenCanvasMixin, StraightenViewerMixin
from modules.qt.rotation_tool_qt import RotationCanvasMixin, RotationViewerMixin
from modules.qt.clone_tool_qt import CloneCanvasMixin, CloneViewerMixin
from modules.qt.text_tool_qt import TextCanvasMixin, TextViewerMixin
from modules.qt.sharpness_tool_qt import SharpnessCanvasMixin, SharpnessViewerMixin
from modules.qt.brightness_tool_qt import BrightnessCanvasMixin, BrightnessViewerMixin
from modules.qt.saturation_tool_qt import SaturationCanvasMixin, SaturationViewerMixin
from modules.qt.remove_colors_tool_qt import RemoveColorsCanvasMixin, RemoveColorsViewerMixin
from modules.qt.compression_tool_qt import (
    CompressionCanvasMixin, CompressionViewerMixin, is_compressible_entry,
)
from modules.qt.levels_tool_qt import LevelsCanvasMixin, LevelsViewerMixin
from modules.qt.shapes_tool_qt import ShapeCanvasMixin, ShapeViewerMixin
from modules.qt.paste_image_tool_qt import PasteImageCanvasMixin, PasteImageViewerMixin
from modules.qt.transparency_tool_qt import (
    TransparencyCanvasMixin, TransparencyViewerMixin, is_transparency_supported_entry,
)
from modules.qt.color_depth_tool_qt import ColorDepthCanvasMixin, ColorDepthViewerMixin
from modules.qt.effects_tool_qt import EffectsCanvasMixin, EffectsViewerMixin
from modules.qt.image_mode_tool_qt import ImageModeCanvasMixin, ImageModeViewerMixin
# La barre d'outils elle-même (composant transversal, pas un outil) — même
# principe de séparation, voir viewer_toolbar_qt.py.
from modules.qt.viewer_toolbar_qt import _ViewerToolbar

# Liste globale des visionneuses ouvertes (pour mise à jour de langue)
image_viewer_refs = []


# ─────────────────────────────────────────────────────────────────────────────
# Helpers PIL → QPixmap
# ─────────────────────────────────────────────────────────────────────────────

def _pil_to_qpixmap(img: Image.Image) -> QPixmap:
    """Convertit une PIL Image en QPixmap (sans perte)."""
    img_rgba = img.convert("RGBA")
    data = img_rgba.tobytes("raw", "RGBA")
    qimg = QImage(data, img_rgba.width, img_rgba.height, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qimg)


def _compose_on_checkerboard(img: Image.Image, tile: int = 16) -> Image.Image:
    """Compose une image RGBA sur un fond damier (gris clair/foncé)."""
    from modules.qt.entries import _make_checkerboard_pil
    bg = _make_checkerboard_pil(img.width, img.height, tile=tile)
    img_rgba = img.convert("RGBA")
    bg.paste(img_rgba, (0, 0), img_rgba)
    return bg


class _ValidateButton(QPushButton):
    """Bouton "Valider" flottant partagé (crop/straighten/text/shapes) — même
    pattern enterEvent/leaveEvent que les panneaux d'options flottants
    (ex. _ShapeOptionsPanel dans shapes_tool_qt.py) : survoler le bouton doit
    suspendre le décompte d'auto-masquage de la barre (idees.txt #3, demande
    explicite utilisateur 2026-08-15 — "il doit aussi être invalidé lorsqu'il
    est sur le bouton"), sinon la barre ET ce bouton pouvaient disparaître
    sous le curseur pendant qu'on visait le clic. leaveEvent différé à 0ms
    (QTimer.singleShot) pour absorber les faux `Leave` transitoires déjà
    documentés pour les panneaux (skill viewers, piège slider en drag actif)."""

    def __init__(self, viewer: "ImageViewer", parent=None):
        super().__init__(parent)
        self._viewer = viewer

    def enterEvent(self, event):
        self._viewer._toolbar.pause_hide()
        # Piège corrigé (2026-08-15, découvert sur l'outil transparency) : le
        # curseur pipette posé sur _ViewerCanvas (outils levels/transparency)
        # restait affiché par-dessus ce bouton, alors qu'il est un widget
        # flottant enfant du canvas — même correctif que les panneaux
        # d'options flottants (voir enterEvent de _TransparencyOptionsPanel/
        # _LevelsOptionsPanel).
        from PySide6.QtCore import Qt as _Qt
        self.setCursor(_Qt.ArrowCursor)
        super().enterEvent(event)

    def leaveEvent(self, event):
        from PySide6.QtCore import QTimer as _QTimer
        _QTimer.singleShot(0, self._check_really_left)
        super().leaveEvent(event)

    def _check_really_left(self):
        from PySide6.QtGui import QCursor as _QCursor
        really_left = not self.rect().contains(self.mapFromGlobal(_QCursor.pos()))
        if really_left:
            self._viewer._toolbar.resume_hide()
            # setCursor(ArrowCursor) posé dans enterEvent ne s'applique qu'à
            # ce bouton — aucun reset à faire ici pour le canvas : Qt
            # réaffiche de lui-même le curseur déjà posé dessus dès que la
            # souris repasse physiquement au-dessus.
            self.unsetCursor()


class _CancelButton(QPushButton):
    """Bouton "Annuler" flottant partagé (crop/straighten/text/shapes/
    transparency, 2026-08-15, demande explicite utilisateur) — jumeau du
    bouton "Valider" (_ValidateButton), placé à sa droite : même widget que
    lui à un geste près, annule TOUT le travail non validé de l'outil actif
    d'un coup (_ImageViewer._cancel_tool_work, même code que le bouton
    "Annuler" et la touche Échap). Même pattern enterEvent/leaveEvent
    (suspend le décompte d'auto-masquage de la barre au survol, reset du
    curseur pipette pour levels/transparency, leaveEvent différé à 0ms pour
    absorber les faux `Leave` transitoires) — voir _ValidateButton pour le
    détail de chaque piège déjà corrigé, reproduit ici à l'identique."""

    def __init__(self, viewer: "ImageViewer", parent=None):
        super().__init__(parent)
        self._viewer = viewer

    def enterEvent(self, event):
        self._viewer._toolbar.pause_hide()
        from PySide6.QtCore import Qt as _Qt
        self.setCursor(_Qt.ArrowCursor)
        super().enterEvent(event)

    def leaveEvent(self, event):
        from PySide6.QtCore import QTimer as _QTimer
        _QTimer.singleShot(0, self._check_really_left)
        super().leaveEvent(event)

    def _check_really_left(self):
        from PySide6.QtGui import QCursor as _QCursor
        really_left = not self.rect().contains(self.mapFromGlobal(_QCursor.pos()))
        if really_left:
            self._viewer._toolbar.resume_hide()
            self.unsetCursor()


# ─────────────────────────────────────────────────────────────────────────────
# Canvas de visionneuse (zone noire avec image centrée + rubber-band crop)
# ─────────────────────────────────────────────────────────────────────────────

class _ViewerCanvas(CropCanvasMixin, StraightenCanvasMixin, RotationCanvasMixin, CloneCanvasMixin,
                     TextCanvasMixin, SharpnessCanvasMixin, BrightnessCanvasMixin, SaturationCanvasMixin,
                     RemoveColorsCanvasMixin, CompressionCanvasMixin, LevelsCanvasMixin,
                     ShapeCanvasMixin, TransparencyCanvasMixin, ColorDepthCanvasMixin,
                     EffectsCanvasMixin, ImageModeCanvasMixin, PasteImageCanvasMixin, QLabel):
    """
    QLabel utilisé comme zone d'affichage de l'image.
    Gère :
      - affichage d'un QPixmap centré sur fond noir
      - rubber-band (rectangle rouge) pour le recadrage
      - pan clic-droit
      - double-clic plein écran / validation crop
    """

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(0, 0)

    def minimumSizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(0, 0)

    def __init__(self, viewer: "ImageViewer"):
        super().__init__()
        self._viewer = viewer
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background: black;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(1, 1)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)
        # Piste secondaire "Coller une image" (idees.txt #1) : glisser une
        # page depuis n'importe quelle mosaïque (panel1/panel2) OU un fichier
        # image depuis l'Explorateur Windows déclenche le même comportement
        # qu'un collage — voir dragEnterEvent/dropEvent plus bas et
        # paste_image_tool_qt.py::PasteImageCanvasMixin._add_pasted_image
        # (point d'entrée déjà isolé à dessein pour ce jour-là, voir sa
        # docstring de module).
        self.setAcceptDrops(True)

        # Infos image affichée (pour conversion coordonnées → image originale)
        self.display_offset_x = 0
        self.display_offset_y = 0
        self.display_width    = 0
        self.display_height   = 0

        # Pan clic-droit
        self._pan_start: QPoint | None = None
        self._is_panning = False

        # Décalage de pan persistant (relatif au centrage), conservé entre les zooms
        self.pan_offset_x = 0
        self.pan_offset_y = 0

        # Ignoré transitoirement après un double-clic (validation crop OU
        # bascule plein écran général — voir mouseDoubleClickEvent) pour
        # éviter qu'un clic résiduel ne déclenche un nouveau geste.
        self._ignore_crop_events = False

        # État de l'outil "crop" (voir crop_tool_qt.py::CropCanvasMixin,
        # hérité par cette classe — CLAUDE.md : ne jamais migrer le code d'un
        # outil dans image_viewer_qt.py).
        self._init_crop_state()

        # Bouton Valider (flottant, partagé crop + straighten — texte selon l'outil actif)
        self._validate_btn: QPushButton | None = None
        self._validate_btn_visible = False
        self._validate_btn_connected_tool: str | None = None

        # Bouton Annuler (flottant, partagé crop/straighten/text/shapes/
        # transparency — même pattern que le bouton Valider, placé à sa
        # droite, voir _ensure_cancel_btn/_update_cancel_btn_state).
        self._cancel_btn: QPushButton | None = None
        self._cancel_btn_visible = False
        self._cancel_btn_connected_tool: str | None = None

        # État de l'outil "straighten" manuel (voir straighten_tool_qt.py::
        # StraightenCanvasMixin, hérité par cette classe — CLAUDE.md : ne
        # jamais migrer le code d'un outil dans image_viewer_qt.py).
        self._init_straighten_state()

        # État de l'outil "rotation" (voir rotation_tool_qt.py::
        # RotationCanvasMixin, hérité par cette classe — CLAUDE.md : ne
        # jamais migrer le code d'un outil dans image_viewer_qt.py).
        self._init_rotation_state()

        # État de l'outil "clone" (voir clone_tool_qt.py::CloneCanvasMixin,
        # hérité par cette classe — CLAUDE.md : ne jamais migrer le code d'un
        # outil dans image_viewer_qt.py).
        self._init_clone_state()

        # État de l'outil "text" (voir text_tool_qt.py::TextCanvasMixin,
        # hérité par cette classe — CLAUDE.md : ne jamais migrer le code d'un
        # outil dans image_viewer_qt.py).
        self._init_text_state()

        # État de l'outil "sharpness" (voir sharpness_tool_qt.py::
        # SharpnessCanvasMixin, hérité par cette classe — CLAUDE.md : ne
        # jamais migrer le code d'un outil dans image_viewer_qt.py).
        self._init_adjustments_state()

        # État de l'outil "brightness" (voir brightness_tool_qt.py::
        # BrightnessCanvasMixin, hérité par cette classe — CLAUDE.md : ne
        # jamais migrer le code d'un outil dans image_viewer_qt.py).
        self._init_brightness_state()

        # État de l'outil "saturation" (voir saturation_tool_qt.py::
        # SaturationCanvasMixin, hérité par cette classe — CLAUDE.md : ne
        # jamais migrer le code d'un outil dans image_viewer_qt.py).
        self._init_saturation_state()

        # État de l'outil "remove_colors" (voir remove_colors_tool_qt.py::
        # RemoveColorsCanvasMixin, hérité par cette classe — CLAUDE.md : ne
        # jamais migrer le code d'un outil dans image_viewer_qt.py).
        self._init_remove_colors_state()

        # État de l'outil "compression" (voir compression_tool_qt.py::
        # CompressionCanvasMixin, hérité par cette classe — CLAUDE.md : ne
        # jamais migrer le code d'un outil dans image_viewer_qt.py).
        self._init_compression_state()

        # État de l'outil "levels" (voir levels_tool_qt.py::LevelsCanvasMixin,
        # hérité par cette classe — CLAUDE.md : ne jamais migrer le code d'un
        # outil dans image_viewer_qt.py).
        self._init_levels_state()

        # État de l'outil "shapes" (voir shapes_tool_qt.py::ShapeCanvasMixin,
        # hérité par cette classe — CLAUDE.md : ne jamais migrer le code d'un
        # outil dans image_viewer_qt.py).
        self._init_shape_state()

        # État de l'outil "transparency" (voir transparency_tool_qt.py::
        # TransparencyCanvasMixin, hérité par cette classe — CLAUDE.md : ne
        # jamais migrer le code d'un outil dans image_viewer_qt.py).
        self._init_transparency_state()

        # État de l'outil "color_depth" (voir color_depth_tool_qt.py::
        # ColorDepthCanvasMixin, hérité par cette classe — CLAUDE.md : ne
        # jamais migrer le code d'un outil dans image_viewer_qt.py).
        self._init_color_depth_state()

        # État de l'outil "effects" (voir effects_tool_qt.py::
        # EffectsCanvasMixin, hérité par cette classe — CLAUDE.md : ne jamais
        # migrer le code d'un outil dans image_viewer_qt.py).
        self._init_effects_state()

        # État de l'outil "image_mode" (voir image_mode_tool_qt.py::
        # ImageModeCanvasMixin, hérité par cette classe — CLAUDE.md : ne
        # jamais migrer le code d'un outil dans image_viewer_qt.py).
        self._init_image_mode_state()

        # État de l'outil "paste_image" (idees.txt #1, voir
        # paste_image_tool_qt.py::PasteImageCanvasMixin, hérité par cette
        # classe — CLAUDE.md : ne jamais migrer le code d'un outil dans
        # image_viewer_qt.py).
        self._init_paste_image_state()

    # Méthodes de l'outil "crop" (has_crop, clear_crop, _get_resize_mode...)
    # fournies par CropCanvasMixin (crop_tool_qt.py), de l'outil "straighten"
    # manuel (has_line, clear_line, set_line_end_from_angle...) fournies par
    # StraightenCanvasMixin (straighten_tool_qt.py), et de l'outil "clone"
    # (clear_clone_source, set_clone_mode, set_clone_brush_radius...) fournies
    # par CloneCanvasMixin (clone_tool_qt.py), héritées par cette classe.

    # ── Bouton Valider (partagé crop + straighten) ───────────────────────────
    # Le texte et l'action dépendent de l'outil actif au moment de l'affichage
    # (self._viewer._toolbar.active_tool) — un seul bouton flottant réutilisé
    # par les deux outils plutôt qu'un par outil.

    _VALIDATE_KEYS = {
        "crop": "buttons.validate_crop",
        "straighten": "buttons.validate_straighten",
        "text": "buttons.validate_text",
        "shapes": "buttons.validate_shapes",
        "transparency": "buttons.validate_transparency",
        "paste_image": "buttons.validate_paste_image",
    }

    # Bouton "Annuler" partagé (2026-08-15, demande explicite utilisateur) :
    # même liste d'outils que le bouton "Valider" — un outil ne peut avoir
    # l'un sans l'autre, les deux sont strictement jumeaux. Écart de largeur
    # entre les deux boutons flottants (px), voir _update_validate_btn_state.
    _CANCEL_KEYS = {
        "crop": "buttons.cancel_crop",
        "straighten": "buttons.cancel_straighten",
        "text": "buttons.cancel_text",
        "shapes": "buttons.cancel_shapes",
        "transparency": "buttons.cancel_transparency",
        "paste_image": "buttons.cancel_paste_image",
    }
    _CANCEL_BTN_GAP = 10

    # Outils dont le bouton "Valider" reste TOUJOURS VISIBLE tant qu'ils sont
    # actifs (grisé/inactif tant que rien à valider, vert/actif sinon) —
    # généralisé depuis le comportement introduit pour "shapes" seul (décision
    # explicite utilisateur, 2026-08-15) ; "transparency" rejoint ce groupe
    # (13e outil migré, seul autre outil migré à accumuler un travail non
    # validé sur plusieurs gestes avant validation, comme crop/straighten/
    # text/shapes — contrairement à levels, voir transparency_tool_qt.py).
    _ALWAYS_VISIBLE_VALIDATE_TOOLS = {"crop", "straighten", "text", "shapes", "transparency", "paste_image"}

    def _validate_tool_has_work(self, tool: str) -> bool:
        """True s'il y a quelque chose à valider pour cet outil sur la page
        courante — pilote l'état vert/actif vs gris/inactif du bouton
        "Valider" partagé (voir _show_validate_btn)."""
        if tool == "crop":
            return self.has_crop
        if tool == "straighten":
            return self.has_line
        if tool == "text":
            return self.has_text_blocks
        if tool == "shapes":
            return self.has_shapes
        if tool == "transparency":
            return self._viewer.current_idx in self._viewer._transp_work_img_by_page
        if tool == "paste_image":
            return self.has_pasted_images
        return True

    def _ensure_validate_btn(self):
        if self._validate_btn is None:
            theme = get_current_theme()
            font = _get_current_font(12, bold=True)
            self._validate_btn = _ValidateButton(self._viewer, self)
            self._validate_btn.setFont(font)
            self._validate_btn_style_normal = (
                f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
                f"border: 1px solid #aaaaaa; padding: 6px 12px; }}"
                f"QPushButton:hover {{ background: {theme['separator']}; }}"
            )
            self._validate_btn_style_green = (
                f"QPushButton {{ background: #2e7d32; color: #ffffff; "
                f"border: 1px solid #1b5e20; padding: 6px 12px; }}"
                f"QPushButton:hover {{ background: #388e3c; }}"
            )
            self._validate_btn_style_disabled = (
                f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['separator']}; "
                f"border: 1px solid {theme['separator']}; padding: 6px 12px; }}"
            )
            self._validate_btn.setStyleSheet(self._validate_btn_style_normal)
            self._validate_btn.setFixedWidth(200)
            # Un QWidget nouvellement créé sans .move() explicite reste à la
            # géométrie par défaut (0,0) — position illégitime, superposée au
            # _mode_label, tant qu'aucun outil (qui déclencherait le calcul de
            # position réel dans _update_validate_btn_state) n'a encore été
            # sélectionné. Positionné ici sous la barre d'outils dès la
            # construction pour qu'il n'existe jamais à une position fausse,
            # en plus du .hide() (seul _reveal_/_conceal_validate_btn pilotent
            # sa visibilité par la suite).
            toolbar = self._viewer._toolbar
            self._validate_btn.move(
                max(0, (self.width() - self._validate_btn.width()) // 2),
                toolbar.y() + toolbar.height() + 6)
            self._validate_btn.hide()

    def _update_validate_btn_state(self):
        """Recalcule TOUT l'état du bouton "Valider" partagé — texte,
        connexion du clic, police, couleur vert/gris, position — SANS jamais
        toucher à sa visibilité (`.show()`/`.hide()`). Appelable sans risque
        depuis n'importe quel point du code qui modifie l'outil actif ou le
        travail en attente (display_image, set_active_tool, les handlers
        souris de crop/straighten/text/shapes...), que la barre soit
        actuellement visible ou masquée : c'est une simple mise à jour de
        données, jamais un déclencheur d'affichage.

        RÈGLE ARCHITECTURALE ABSOLUE (2026-08-15, décision explicite et
        répétée de l'utilisateur) : il ne doit y avoir qu'UN SEUL mécanisme
        qui décide de la visibilité de ce bouton — `_ViewerToolbar.
        show_and_schedule_hide()` (le fait apparaître) et `_on_hide_timeout()`
        (le fait disparaître), tous deux dans viewer_toolbar_qt.py. Aucune
        autre fonction de ce fichier n'appelle jamais `.show()`/`.hide()` sur
        ce bouton — seuls ces deux points du mécanisme d'auto-masquage de la
        barre le font, via les méthodes dédiées `_reveal_validate_btn()`/
        `_conceal_validate_btn()` plus bas. Avant cette séparation, une seule
        fonction (`_show_validate_btn`) mélangeait mise à jour d'état ET
        affichage, appelée depuis 15+ endroits différents — chacun de ces
        appels était un point de réapparition potentiel indépendant, ce qui a
        provoqué plusieurs bugs vécus (bouton flottant seul, sans la barre
        au-dessus, après l'auto-masquage). Ne JAMAIS réintroduire un appel à
        `.show()` sur ce bouton en dehors de `_reveal_validate_btn()`."""
        self._ensure_validate_btn()
        w = self._validate_btn
        tool = self._viewer._toolbar.active_tool
        key = self._VALIDATE_KEYS.get(tool)
        if key is None:
            return
        if self._validate_btn_connected_tool != tool:
            if self._validate_btn_connected_tool is not None:
                w.clicked.disconnect()
            if tool == "crop":
                w.clicked.connect(self._viewer.validate_crop)
            elif tool == "text":
                w.clicked.connect(self._viewer.validate_text)
            elif tool == "shapes":
                w.clicked.connect(self._viewer.validate_shapes)
            elif tool == "transparency":
                w.clicked.connect(self._viewer.validate_transparency)
            elif tool == "paste_image":
                w.clicked.connect(self._viewer.validate_paste_image)
            else:
                w.clicked.connect(self._viewer.validate_straighten)
            self._validate_btn_connected_tool = tool
        w.setText(_(key))
        # Réappliquer la police à CHAQUE mise à jour (pas seulement à la
        # création dans _ensure_validate_btn) — piège CLAUDE.md "retraduction
        # dynamique" : un setText() seul garde l'ancienne police (latine),
        # illisible pour les langues CSUR (pIqaD/Tengwar).
        w.setFont(_get_current_font(12, bold=True))

        if tool in self._ALWAYS_VISIBLE_VALIDATE_TOOLS:
            has_work = self._validate_tool_has_work(tool)
            # setEnabled(False) plutôt qu'un simple style "grisé" ferait
            # perdre à Qt tout enterEvent/leaveEvent sur ce bouton (un widget
            # désactivé ne reçoit plus les événements souris, ils remontent
            # au parent) — piège corrigé (2026-08-16, signalé utilisateur) :
            # le curseur restait celui de l'outil actif (pipette de levels/
            # transparency, ou simplement le curseur du canvas) même survolé
            # au-dessus du bouton. Le bouton reste donc TOUJOURS setEnabled
            # (True) et cliquable même "gris" — chaque validate_* gère déjà
            # le cas "rien à valider" avec son propre MsgDialog
            # d'avertissement (voir crop_tool_qt.py::validate_crop et
            # équivalents), donc un clic sur un bouton gris n'est jamais
            # dangereux, seulement redondant avec ce garde-fou déjà en place.
            w.setEnabled(True)
            w.setStyleSheet(
                self._validate_btn_style_green if has_work
                else self._validate_btn_style_disabled)
        else:
            w.setEnabled(True)
            w.setStyleSheet(self._validate_btn_style_normal)

        bw = w.sizeHint().width()
        bh = w.sizeHint().height()
        # Positionné SOUS la barre d'outils + son panneau d'options s'il en a
        # un (retour utilisateur explicite, généralisé aux outils à bouton
        # "Valider" partagé le 2026-08-15 — initialement introduit pour
        # "shapes" seul, puis "transparency" à son tour) : plus de position
        # "bas d'écran" pour aucun de ces outils. Crop n'a pas de panneau
        # d'options flottant dédié (rien à régler à part le rectangle
        # lui-même) — retombe directement sur la barre elle-même, comme le
        # ferait n'importe quel outil dont le panneau n'est pas visible.
        toolbar = self._viewer._toolbar
        panel = {
            "crop": None,
            "straighten": toolbar._angle_panel,
            "text": toolbar._text_panel,
            "shapes": toolbar._shapes_panel,
            "transparency": toolbar._transparency_panel,
            "paste_image": None,
        }.get(tool)
        if panel is not None and panel.isVisible():
            panel_bottom = panel.y() + panel.height()
        else:
            panel_bottom = toolbar.y() + toolbar.height()
        y = panel_bottom + 6
        # Centré avec le bouton "Annuler" placé juste à sa droite (2026-08-15,
        # demande explicite utilisateur) : x calculé sur la largeur du COUPLE
        # des deux boutons (avec un espacement _CANCEL_BTN_GAP entre eux), pas
        # sur ce seul bouton — sinon le couple ne serait plus centré sous la
        # barre. cw/ch = 0 si le bouton Annuler n'existe pas encore (avant sa
        # toute première construction) ou si l'outil actif n'en a pas besoin.
        cbw = self._cancel_btn.sizeHint().width() if self._cancel_btn is not None else 0
        gap = self._CANCEL_BTN_GAP if cbw else 0
        total_w = bw + gap + cbw
        x = (self.width() - total_w) // 2
        w.setGeometry(x, y, bw, bh)

    def _reveal_validate_btn(self):
        """SEUL point du code autorisé à afficher ce bouton — appelé
        UNIQUEMENT par _ViewerToolbar.show_and_schedule_hide() (mécanisme
        unique de réapparition, voir docstring de _update_validate_btn_state).
        Recalcule l'état à jour puis affiche, si l'outil actif en a besoin."""
        tool = self._viewer._toolbar.active_tool
        if self._VALIDATE_KEYS.get(tool) is None:
            return
        self._update_validate_btn_state()
        w = self._validate_btn
        w.show()
        w.raise_()
        self._validate_btn_visible = True

    def _conceal_validate_btn(self):
        """SEUL point du code autorisé à masquer ce bouton — appelé
        UNIQUEMENT par _ViewerToolbar._on_hide_timeout() (mécanisme unique de
        masquage, symétrique de _reveal_validate_btn)."""
        if self._validate_btn is not None:
            self._validate_btn.hide()
        self._validate_btn_visible = False

    def retranslate_validate_btn(self):
        if self._validate_btn is not None and self._validate_btn_visible:
            tool = self._viewer._toolbar.active_tool
            key = self._VALIDATE_KEYS.get(tool)
            if key is not None:
                self._validate_btn.setText(_(key))
            font = _get_current_font(12, bold=True)
            self._validate_btn.setFont(font)

    # ── Bouton Annuler (partagé crop/straighten/text/shapes/transparency) ────
    # Jumeau du bouton "Valider" ci-dessus, placé à sa droite — même
    # mécanisme unique de réapparition/masquage (_reveal_cancel_btn/
    # _conceal_cancel_btn, appelés UNIQUEMENT par _ViewerToolbar.
    # show_and_schedule_hide()/_on_hide_timeout(), jamais ailleurs — voir la
    # docstring de _update_validate_btn_state pour la règle complète, elle
    # s'applique ici de façon strictement identique). Rouge/actif dès qu'il y
    # a quelque chose à annuler (_validate_tool_has_work, même test que le
    # bouton Valider — ce qui peut être validé peut être annulé, rien de
    # plus), gris/inactif sinon.

    def _ensure_cancel_btn(self):
        if self._cancel_btn is None:
            theme = get_current_theme()
            font = _get_current_font(12, bold=True)
            self._cancel_btn = _CancelButton(self._viewer, self)
            self._cancel_btn.setFont(font)
            self._cancel_btn_style_red = (
                f"QPushButton {{ background: #c62828; color: #ffffff; "
                f"border: 1px solid #8e0000; padding: 6px 12px; }}"
                f"QPushButton:hover {{ background: #d32f2f; }}"
            )
            self._cancel_btn_style_disabled = (
                f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['separator']}; "
                f"border: 1px solid {theme['separator']}; padding: 6px 12px; }}"
            )
            self._cancel_btn.setStyleSheet(self._cancel_btn_style_disabled)
            self._cancel_btn.clicked.connect(
                lambda: self._viewer._cancel_tool_work(self._viewer._toolbar.active_tool))
            # Même raison que _ensure_validate_btn : sans .move() explicite,
            # ce bouton nouvellement créé reste à la géométrie par défaut
            # (0,0), position illégitime superposée au _mode_label. Positionné
            # ici juste à droite de l'emplacement du bouton "Valider" (même
            # calcul que _update_cancel_btn_state), en plus du .hide().
            self._ensure_validate_btn()
            v = self._validate_btn
            toolbar = self._viewer._toolbar
            self._cancel_btn.move(
                v.x() + v.width() + self._CANCEL_BTN_GAP,
                toolbar.y() + toolbar.height() + 6)
            self._cancel_btn.hide()

    def _update_cancel_btn_state(self):
        """Jumeau de _update_validate_btn_state ci-dessus — mêmes garanties
        (ne touche jamais à .show()/.hide(), appelable sans risque depuis
        n'importe quel point de mise à jour). Le clic est connecté UNE SEULE
        FOIS dans _ensure_cancel_btn (pas reconnecté à chaque outil comme le
        bouton Valider) : contrairement à validate_crop/validate_straighten/
        etc. (une méthode dédiée par outil), _cancel_tool_work(tool) est déjà
        une seule fonction paramétrée par l'outil actif — rien à reconnecter
        au changement d'outil."""
        self._ensure_cancel_btn()
        self._ensure_validate_btn()
        w = self._cancel_btn
        tool = self._viewer._toolbar.active_tool
        key = self._CANCEL_KEYS.get(tool)
        if key is None:
            return
        w.setText(_(key))
        w.setFont(_get_current_font(12, bold=True))

        has_work = self._validate_tool_has_work(tool)
        # setEnabled(False) ferait perdre enterEvent/leaveEvent — même piège
        # et même correctif que le bouton "Valider" (2026-08-16, voir
        # _update_validate_btn_state). Le clic sur un bouton "gris" retombe
        # simplement sur _cancel_tool_work(tool), un no-op naturel puisqu'il
        # n'y a alors rien à effacer (clear_crop() sur un crop déjà vide,
        # etc.) — pas besoin d'un garde-fou supplémentaire comme pour Valider.
        w.setEnabled(True)
        w.setStyleSheet(
            self._cancel_btn_style_red if has_work
            else self._cancel_btn_style_disabled)

        # Positionné à droite du bouton "Valider", même ligne — recalcule
        # aussi la position du bouton "Valider" au passage (son x dépend de
        # la largeur du couple des deux boutons, voir
        # _update_validate_btn_state) pour rester centré maintenant que ce
        # bouton existe.
        self._update_validate_btn_state()
        v = self._validate_btn
        cbw = w.sizeHint().width()
        cbh = w.sizeHint().height()
        x = v.x() + v.width() + self._CANCEL_BTN_GAP
        y = v.y()
        w.setGeometry(x, y, cbw, cbh)

    def _reveal_cancel_btn(self):
        """SEUL point du code autorisé à afficher ce bouton — appelé
        UNIQUEMENT par _ViewerToolbar.show_and_schedule_hide(), jumeau exact
        de _reveal_validate_btn."""
        tool = self._viewer._toolbar.active_tool
        if self._CANCEL_KEYS.get(tool) is None:
            return
        self._update_cancel_btn_state()
        w = self._cancel_btn
        w.show()
        w.raise_()
        self._cancel_btn_visible = True

    def _conceal_cancel_btn(self):
        """SEUL point du code autorisé à masquer ce bouton — appelé
        UNIQUEMENT par _ViewerToolbar._on_hide_timeout(), jumeau exact de
        _conceal_validate_btn."""
        if self._cancel_btn is not None:
            self._cancel_btn.hide()
        self._cancel_btn_visible = False

    def retranslate_cancel_btn(self):
        if self._cancel_btn is not None and self._cancel_btn_visible:
            tool = self._viewer._toolbar.active_tool
            key = self._CANCEL_KEYS.get(tool)
            if key is not None:
                self._cancel_btn.setText(_(key))
            font = _get_current_font(12, bold=True)
            self._cancel_btn.setFont(font)

    # ── Affichage du pixmap ──────────────────────────────────────────────────

    def set_pixmap_and_geometry(self, pixmap: QPixmap,
                                offset_x: int, offset_y: int,
                                disp_w: int, disp_h: int):
        # Stocke sans appeler setPixmap() pour ne pas déclencher updateGeometry()
        self._current_pixmap  = pixmap
        self.display_offset_x = offset_x
        self.display_offset_y = offset_y
        self.display_width    = disp_w
        self.display_height   = disp_h
        self.update()

    # ── Dessin (image + rubber-band) ─────────────────────────────────────────

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QPen, QColor
        from PySide6.QtCore import QRectF
        painter = QPainter(self)

        # Fond noir
        painter.fillRect(self.rect(), QColor("black"))

        # Image centrée : le pixmap est stocké à sa résolution source (jamais
        # redimensionné en PIL pour le zoom) — c'est Qt qui l'étire à l'affichage
        # via drawPixmap(rect cible), beaucoup plus rapide qu'un resize() PIL
        # répété à chaque cran de molette.
        pm = getattr(self, '_current_pixmap', None)
        if pm and not pm.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            target = QRectF(self.display_offset_x, self.display_offset_y,
                             self.display_width, self.display_height)
            painter.drawPixmap(target, pm, QRectF(pm.rect()))

        # Rubber-band de recadrage (outil "crop", voir crop_tool_qt.py::
        # CropCanvasMixin.paint_crop_rect).
        self.paint_crop_rect(painter)

        # Trait de redressage manuel (outil "straighten", voir
        # straighten_tool_qt.py::StraightenCanvasMixin.paint_straighten_line).
        self.paint_straighten_line(painter)

        # Marqueur de source du tampon de clonage (outil "clone", voir
        # clone_tool_qt.py::CloneCanvasMixin.paint_clone_marker).
        self.paint_clone_marker(painter)

        # Formes (outil "shapes", voir shapes_tool_qt.py::ShapeCanvasMixin.paint_shapes).
        self.paint_shapes(painter)

        # Images collées (outil "paste_image", idees.txt #1, voir
        # paste_image_tool_qt.py::PasteImageCanvasMixin.paint_pasted_images).
        self.paint_pasted_images(painter)

        # Barre de progression verticale (mode Webtoon uniquement) : indique la
        # position de scroll dans une page qui dépasse de la fenêtre en hauteur.
        if self._viewer.page_mode == "webtoon" and self.display_height > self.height():
            track_w = 4
            track_x = self.width() - track_w - 4
            track_h = self.height()
            painter.fillRect(track_x, 0, track_w, track_h, QColor(255, 255, 255, 40))
            visible_ratio = min(1.0, self.height() / self.display_height)
            thumb_h = max(20, int(track_h * visible_ratio))
            scroll_range = self.display_height - self.height()
            scroll_pos = -self.display_offset_y
            progress = 0.0 if scroll_range <= 0 else max(0.0, min(1.0, scroll_pos / scroll_range))
            thumb_y = int((track_h - thumb_h) * progress)
            painter.fillRect(track_x, thumb_y, track_w, thumb_h, QColor(255, 255, 255, 160))

        painter.end()

    # ── Gestion resize mode ──────────────────────────────────────────────────

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

    # ── Événements souris ────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        # Vole explicitement le focus clavier à tout widget flottant de la
        # barre d'outils actuellement en édition (ex. QSpinBox de netteté) :
        # ce canvas a setFocusPolicy(Qt.NoFocus) (raccourcis clavier gérés
        # par QShortcut au niveau fenêtre, pas par le focus du canvas), donc
        # sans cet appel explicite, aucun clic dans la zone de lecture ne
        # peut jamais faire perdre le focus à un widget flottant — celui-ci
        # le garde indéfiniment jusqu'à ce que la fenêtre entière perde
        # l'activation (diagnostiqué 2026-08-14 : QSpinBox.hasFocus() restait
        # True après un clic canvas, seul un Alt+Tab vers une autre appli
        # déclenchait le FocusOut). setFocus() sur un widget NoFocus reste
        # possible par appel programmatique explicite (contrairement à un
        # focus par clic natif, bloqué par la policy).
        self.setFocus(Qt.MouseFocusReason)
        if event.button() == Qt.RightButton:
            self._pan_start = event.position().toPoint()
            self._is_panning = False
            return

        if event.button() != Qt.LeftButton:
            return

        if self._ignore_crop_events:
            return

        active_tool = self._viewer._toolbar.active_tool
        if active_tool == "straighten":
            self.straighten_mouse_press(event)
            return

        if active_tool == "clone":
            self.clone_mouse_press(event)
            return

        if active_tool == "text":
            self.text_mouse_press(event)
            return

        if active_tool == "levels":
            self.levels_pipette_click(event)
            return

        if active_tool == "shapes":
            shapes_panel = self._viewer._toolbar._shapes_panel
            if shapes_panel.pipette_active:
                self.shapes_pipette_click(event)
            else:
                self.shape_mouse_press(event)
            return

        if active_tool == "transparency":
            self.transparency_pipette_click(event)
            return

        if active_tool == "paste_image":
            self.paste_image_mouse_press(event)
            return

        if active_tool != "crop":
            return

        self.crop_mouse_press(event)

    def mouseMoveEvent(self, event):
        self._viewer._toolbar.on_canvas_mouse_move(int(event.position().y()), self.height())

        # Pan clic-droit
        if event.buttons() & Qt.RightButton and self._pan_start is not None:
            delta = event.position().toPoint() - self._pan_start
            if abs(delta.x()) > 5 or abs(delta.y()) > 5:
                self._is_panning = True
                self.setCursor(Qt.SizeAllCursor)
                self.pan_offset_x += delta.x()
                self.pan_offset_y += delta.y()
                self._pan_start = event.position().toPoint()
                if self.has_crop or self.has_text_blocks or self.has_shapes or self.has_pasted_images:
                    self._viewer.display_image(keep_crop_rect=True)
                else:
                    self.display_offset_x += delta.x()
                    self.display_offset_y += delta.y()
                    self.update()
            return

        active_tool = self._viewer._toolbar.active_tool

        if not (event.buttons() & Qt.LeftButton):
            # Mise à jour du curseur selon la position sur le cadre (uniquement si
            # l'outil crop est actif : un rectangle conservé mais désélectionné
            # n'est ni redimensionnable ni déplaçable, voir set_active_tool)
            if self.has_crop and active_tool == "crop":
                self.crop_update_cursor(event)
            elif self.has_line and active_tool == "straighten":
                self.straighten_update_cursor(event)
            elif active_tool == "clone":
                self.clone_update_cursor(event)
            elif active_tool == "text":
                self.text_update_cursor(event)
            elif active_tool == "levels":
                # Curseur pipette déjà posé par _LevelsOptionsPanel au clic sur
                # le bouton pipette (voir _on_black/white_pipette_clicked) —
                # ne pas l'écraser ici, contrairement aux autres outils qui
                # recalculent leur curseur à chaque mouvement.
                pass
            elif active_tool == "transparency":
                # Même principe que levels ci-dessus : curseur pipette déjà
                # posé par _TransparencyOptionsPanel au clic sur son bouton
                # pipette (voir _on_pipette_clicked) — ne pas l'écraser ici.
                pass
            elif active_tool == "shapes":
                self.shape_update_cursor(event)
            elif active_tool == "paste_image":
                self.paste_image_update_cursor(event)
            else:
                self.setCursor(Qt.ArrowCursor)
            return

        if active_tool == "straighten":
            if self._ignore_crop_events:
                return
            self.straighten_mouse_move(event)
            return

        if active_tool == "clone":
            if self._ignore_crop_events:
                return
            self.clone_mouse_move(event)
            return

        if active_tool == "text":
            if self._ignore_crop_events:
                return
            self.text_mouse_move(event)
            return

        if active_tool == "shapes":
            if self._ignore_crop_events:
                return
            self.shape_mouse_move(event)
            return

        if active_tool == "paste_image":
            if self._ignore_crop_events:
                return
            self.paste_image_mouse_move(event)
            return

        if self._ignore_crop_events:
            return
        if active_tool != "crop":
            return

        self.crop_mouse_move(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            # Restaure le curseur pipette si l'outil "levels" est actif avec
            # une pipette armée (bug vécu 2026-08-15) : sans ce cas, un pan
            # (clic droit maintenu) pendant qu'une pipette est armée écrasait
            # inconditionnellement le curseur avec ArrowCursor au relâchement,
            # faisant "disparaître" visuellement la pipette alors que
            # panel.active_pipette restait bien armé côté état (le clic
            # suivant sur l'image continuait de fonctionner comme un clic
            # pipette, seul le curseur affiché était trompeur).
            levels_panel = self._viewer._toolbar._levels_panel
            if (self._viewer._toolbar.active_tool == "levels"
                    and levels_panel.active_pipette == "black"):
                self.setCursor(levels_panel._cursor_black or Qt.CrossCursor)
            elif (self._viewer._toolbar.active_tool == "levels"
                    and levels_panel.active_pipette == "white"):
                self.setCursor(levels_panel._cursor_white or Qt.CrossCursor)
            elif self._viewer._toolbar.active_tool == "transparency":
                # Même piège que la pipette de niveaux ci-dessus, corrigé de
                # la même façon (2026-08-15) — pas de notion d'"armement" ici
                # (pas de bouton pipette, contrairement à levels) : le
                # curseur pipette est simplement celui de l'outil actif.
                panel = self._viewer._toolbar._transparency_panel
                self.setCursor(panel._cursor_pipette or Qt.CrossCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            if not self._is_panning:
                self._viewer._show_context_menu(event.globalPosition().toPoint())
            self._pan_start  = None
            self._is_panning = False
            return

        if event.button() != Qt.LeftButton:
            return
        if self._ignore_crop_events:
            return

        if self._viewer._toolbar.active_tool == "straighten":
            self.straighten_mouse_release(event)
            return

        if self._viewer._toolbar.active_tool == "clone":
            self.clone_mouse_release(event)
            return

        if self._viewer._toolbar.active_tool == "text":
            self.text_mouse_release(event)
            return

        if self._viewer._toolbar.active_tool == "shapes":
            self.shape_mouse_release(event)
            return

        if self._viewer._toolbar.active_tool == "paste_image":
            self.paste_image_mouse_release(event)
            return

        self.crop_mouse_release(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self._ignore_crop_events = True
        self._waiting_for_double_click = False

        pos = event.position().toPoint()

        if self.has_crop and self._viewer._toolbar.active_tool == "crop":
            x1 = min(self._crop_start.x(), self._crop_end.x())
            y1 = min(self._crop_start.y(), self._crop_end.y())
            x2 = max(self._crop_start.x(), self._crop_end.x())
            y2 = max(self._crop_start.y(), self._crop_end.y())
            if x1 <= pos.x() <= x2 and y1 <= pos.y() <= y2:
                self._viewer._validating_crop = True
                self._viewer.validate_crop()
                self._viewer._validating_crop = False
                QTimer.singleShot(100, lambda: setattr(self, '_ignore_crop_events', False))
                return

        if not self._viewer._validating_crop:
            if self._crop_start is not None and self._crop_end is None:
                self._crop_start = None
                self.update()
            self._viewer.toggle_fullscreen()

        QTimer.singleShot(1000, lambda: setattr(self, '_ignore_crop_events', False))

    def wheelEvent(self, event):
        self._viewer._on_wheel(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._validate_btn_visible and self._validate_btn is not None:
            # Le bouton est déjà visible — recalcule seulement sa position
            # (la fenêtre vient de changer de taille), ne touche pas à sa
            # visibilité (mécanisme unique, voir _update_validate_btn_state).
            self._update_validate_btn_state()
        if self._cancel_btn_visible and self._cancel_btn is not None:
            self._update_cancel_btn_state()
        if self._viewer._toolbar.isVisible():
            self._viewer._toolbar.reposition()
        angle_panel = self._viewer._toolbar._angle_panel
        if angle_panel.isVisible():
            angle_panel.reposition()
        rotation_panel = self._viewer._toolbar._rotation_panel
        if rotation_panel.isVisible():
            rotation_panel.reposition()
        clone_panel = self._viewer._toolbar._clone_panel
        if clone_panel.isVisible():
            clone_panel.reposition()
        text_panel = self._viewer._toolbar._text_panel
        if text_panel.isVisible():
            text_panel.reposition()
        sharpness_panel = self._viewer._toolbar._sharpness_panel
        if sharpness_panel.isVisible():
            sharpness_panel.reposition()
        unsharp_panel = self._viewer._toolbar._unsharp_panel
        if unsharp_panel.isVisible():
            unsharp_panel.reposition()
        brightness_panel = self._viewer._toolbar._brightness_panel
        if brightness_panel.isVisible():
            brightness_panel.reposition()
        saturation_panel = self._viewer._toolbar._saturation_panel
        if saturation_panel.isVisible():
            saturation_panel.reposition()
        remove_colors_panel = self._viewer._toolbar._remove_colors_panel
        if remove_colors_panel.isVisible():
            remove_colors_panel.reposition()
        compression_panel = self._viewer._toolbar._compression_panel
        if compression_panel.isVisible():
            compression_panel.reposition()
        levels_panel = self._viewer._toolbar._levels_panel
        if levels_panel.isVisible():
            levels_panel.reposition()
        shapes_panel = self._viewer._toolbar._shapes_panel
        if shapes_panel.isVisible():
            shapes_panel.reposition()
        transparency_panel = self._viewer._toolbar._transparency_panel
        if transparency_panel.isVisible():
            transparency_panel.reposition()
        color_depth_panel = self._viewer._toolbar._color_depth_panel
        if color_depth_panel.isVisible():
            color_depth_panel.reposition()
        effects_panel = self._viewer._toolbar._effects_panel
        if effects_panel.isVisible():
            effects_panel.reposition()
        image_mode_panel = self._viewer._toolbar._image_mode_panel
        if image_mode_panel.isVisible():
            image_mode_panel.reposition()
        if self.has_text_blocks:
            self.reposition_text_blocks()

    # ── Drag & drop entrant (idees.txt #1, piste secondaire "Coller une
    # image") : glisser une page depuis n'importe quelle mosaïque (panel1/
    # panel2) OU un fichier image depuis l'Explorateur Windows déclenche le
    # même comportement qu'un collage — même point d'entrée unique
    # (_add_pasted_image) que Ctrl+V/l'icône "paste_image", voir sa docstring
    # de module (paste_image_tool_qt.py) pour la raison de cette isolation. ──

    def _drag_has_acceptable_image(self, mime) -> bool:
        """True si ce QMimeData représente EXACTEMENT une image utilisable —
        même exigence stricte que le presse-papiers (idees.txt #1 : "l'icône
        n'est active QUE si le presse-papier contient EXCLUSIVEMENT une
        image"), appliquée ici au drag plutôt qu'au Ctrl+V. Un drag INTERNE
        depuis une mosaïque (mime "application/x-mosaicview-indices"/
        "-panel") pose TOUJOURS aussi des URLs CF_HDROP en parallèle (voir
        mosaic_canvas.py::_start_drag — chaque entrée sélectionnée est déjà
        écrite sur disque pour permettre le drag-out vers l'Explorateur),
        donc un seul chemin de code (mime.hasUrls()) couvre les deux
        provenances (mosaïque ET Explorateur) sans avoir à distinguer les
        deux mimes internes ni à lire state.images_data directement — on ne
        lit jamais que le fichier réellement écrit sur disque, exactement
        comme pour un drop externe classique. N'accepte qu'un SEUL fichier
        image reconnu (même liste IMAGE_EXTS que le presse-papiers, voir
        clipboard_qt.py) — plusieurs fichiers, ou un seul fichier non-image,
        sont refusés."""
        if not mime.hasUrls():
            return False
        urls = mime.urls()
        if len(urls) != 1 or not urls[0].isLocalFile():
            return False
        import os
        from modules.qt.clipboard_qt import IMAGE_EXTS
        ext = os.path.splitext(urls[0].toLocalFile())[1].lower()
        return ext in IMAGE_EXTS

    def dragEnterEvent(self, event):
        # Qt.CopyAction forcé (pas acceptProposedAction) — voir dropEvent :
        # ce drag doit toujours se présenter comme un collage, jamais un
        # déplacement, dès le survol.
        if self._drag_has_acceptable_image(event.mimeData()):
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._drag_has_acceptable_image(event.mimeData()):
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        mime = event.mimeData()
        if not self._drag_has_acceptable_image(mime):
            event.ignore()
            return
        path = mime.urls()[0].toLocalFile()
        try:
            from PIL import Image
            img = Image.open(path).convert("RGBA")
        except Exception:
            event.ignore()
            return
        # Qt.CopyAction forcé explicitement (pas acceptProposedAction) : ce
        # drop doit toujours se comporter comme un COLLAGE, jamais comme un
        # déplacement — même si le SO/modificateur clavier suggérait
        # MoveAction, la page source ne doit jamais disparaître de sa
        # mosaïque d'origine. mosaic_canvas.py::_start_drag ne supprime la
        # page source que si son PROPRE dropEvent (réordonnancement/
        # inter-panneaux) marque _drop_was_internal — jamais déclenché ici,
        # cette fenêtre est une QDialog séparée de toute mosaïque.
        event.setDropAction(Qt.CopyAction)
        event.accept()
        if self._viewer._toolbar.active_tool != "paste_image":
            self._viewer._toolbar.set_active_tool("paste_image")
        self._add_pasted_image(img)


# ─────────────────────────────────────────────────────────────────────────────
# Barre d'outils flottante (fusion progressive des visionneuses — idees.txt #3)
# ─────────────────────────────────────────────────────────────────────────────

def _floating_options_panel_style(theme, class_name: str) -> str:
    """Style de fond commun aux panneaux flottants d'options (angle de
    redressage, réglages du tampon de clonage) : contrairement à
    _ViewerToolbar (dont les icônes se détachent d'elles-mêmes par leur
    contraste propre), ces panneaux n'ont que du texte/contrôles fins — sans
    bordure marquée, ils se fondent visuellement dans une image de fond claire
    ou blanche (signalé par l'utilisateur en conditions réelles). Fond franc
    dédié (pas toolbar_bg, trop proche d'un fond de page clair/blanc typique
    d'une BD) + bordure nette dans la couleur de texte du thème."""
    panel_bg = "#3a3a3a" if _state_module.state.dark_mode else "#f0f0f0"
    return (
        f"{class_name} {{ background: {panel_bg}; border: 1px solid {theme['text']}; "
        f"border-radius: 6px; }}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Visionneuse principale
# ─────────────────────────────────────────────────────────────────────────────

class ImageViewer(CropViewerMixin, StraightenViewerMixin, RotationViewerMixin, CloneViewerMixin,
                   TextViewerMixin, SharpnessViewerMixin, BrightnessViewerMixin, SaturationViewerMixin,
                   RemoveColorsViewerMixin, CompressionViewerMixin, LevelsViewerMixin,
                   ShapeViewerMixin, TransparencyViewerMixin, ColorDepthViewerMixin,
                   EffectsViewerMixin, ImageModeViewerMixin, PasteImageViewerMixin, QDialog):
    """
    Visionneuse d'images Qt.
    Reproduit à l'identique ImageViewer (tkinter) :
      - Navigation ← → / molette
      - Zoom Ctrl+Molette / Ctrl+Plus / Ctrl+Moins / Ctrl+0
      - Mode lecture : simple / double / continu (touche D)
      - Plein écran F11 / double-clic
      - Recadrage rubber-band + validation
      - GIF animé (bouton Play/Pause)
      - Pan clic-droit
      - Menu contextuel clic-droit
      - Undo/Redo Ctrl+Z / Ctrl+Y
    """

    def __init__(self, parent, start_idx: int, callbacks: dict | None = None,
                 initial_tool: str | None = None):
        super().__init__(parent)
        self.callbacks = callbacks or {}
        state = self.callbacks.get('state') or _state_module.state
        self._initial_tool = initial_tool

        self.setWindowTitle(_wt("viewer.title"))
        self.resize(800, 600)
        self.setWindowFlags(Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)

        # ── Appliquer l'icône de la fenêtre ──
        from modules.qt.font_loader import resource_path
        import os
        ico_path = resource_path('icons/MosaicView.ico')
        if os.path.exists(ico_path):
            from PySide6.QtGui import QIcon
            self.setWindowIcon(QIcon(ico_path))

        self.current_idx   = start_idx
        state.active_viewers += 1
        image_viewer_refs.append(self)

        # Rectangles de crop en cours, conservés par page (idees.txt #3, partie B :
        # un travail d'outil non validé survit à un changement de page). Clé = index
        # de page, valeur = (crop_rel_x1, crop_rel_y1, crop_rel_x2, crop_rel_y2).
        # Volatile : jamais persisté sur disque, perdu à la fermeture de la visionneuse.
        self._crop_by_page: dict[int, tuple[float, float, float, float]] = {}

        # Traits de redressage en cours, conservés par page — même principe que
        # _crop_by_page. Clé = index de page, valeur = ((ix1, iy1), (ix2, iy2))
        # en coordonnées image (stables, indépendantes du zoom/pan).
        self._straighten_by_page: dict[int, tuple[tuple[float, float], tuple[float, float]]] = {}

        # État de l'outil "clone" — PAS de dict par page comme crop/straighten :
        # contrairement à eux, un coup de tampon est déjà appliqué et commité
        # (bytes + save_state) dès son relâchement, il n'y a donc rien "en
        # attente de validation" à faire survivre à un changement de page (voir
        # idees.txt #3, discussion du 3e outil migré). Seule la position de la
        # source Ctrl+cliquée est un état d'outil volatile, réinitialisée à
        # chaque changement de page (voir navigate()). Voir clone_tool_qt.py::
        # CloneViewerMixin, hérité par cette classe.
        self._init_clone_viewer_state()

        # Blocs de texte en cours, conservés par page — même principe que
        # _crop_by_page/_straighten_by_page, mais valeur = liste de blocs
        # (ix, iy, html) au lieu d'une seule géométrie. Volatile : jamais
        # persisté sur disque, perdu à la fermeture de la visionneuse.
        self._text_blocks_by_page: dict[int, list[tuple[int, int, str]]] = {}

        # Formes en cours, conservées par page — même principe que
        # _text_blocks_by_page (liste de N formes par page, pas une seule
        # géométrie). Voir shapes_tool_qt.py::ShapeViewerMixin. Volatile :
        # jamais persisté sur disque, perdu à la fermeture de la visionneuse.
        self._shapes_by_page: dict[int, list[tuple]] = {}

        # Images collées en cours, conservées par page (idees.txt #1, outil
        # "paste_image") — même principe que _shapes_by_page, mais valeur =
        # liste de (png_bytes, ix1, iy1, ix2, iy2, angle) par page (bitmap
        # sérialisé en PNG, pas juste une géométrie — voir
        # paste_image_tool_qt.py::PasteImageViewerMixin). Volatile : jamais
        # persisté sur disque, perdu à la fermeture de la visionneuse.
        self._pasted_images_by_page: dict[int, list[tuple]] = {}

        # Images de travail RGBA en cours pour l'outil "transparency"
        # (13e outil migré, 8e et dernier mode d'ajustement), conservées par
        # page — même principe que _shapes_by_page/_text_blocks_by_page, mais
        # valeur = image PIL RGBA accumulée (pas une géométrie) : contrairement
        # à levels (commit immédiat par clic), plusieurs clics pipette
        # s'accumulent avant validation explicite (bouton "Valider" partagé),
        # décision utilisateur 2026-08-15. Voir transparency_tool_qt.py::
        # TransparencyViewerMixin. Volatile : jamais persisté sur disque,
        # perdu à la fermeture de la visionneuse.
        self._transp_work_img_by_page: dict[int, "Image.Image"] = {}

        # Preview PIL des outils "sharpness"/"unsharp" (5e outil migré) ET
        # "brightness" (6e outil migré), PARTAGÉ entre les trois (un seul
        # outil actif à la fois dans la barre, voir sharpness_tool_qt.py::
        # _update_unsharp_preview et brightness_tool_qt.py::
        # _update_brightness_preview) — PAS de dict par page comme
        # crop/straighten/texte : contrairement à eux, un réglage non validé
        # ne survit pas à un changement de page (idees.txt #3, décision
        # explicite du 2026-08-14). Consommé par _display_single_page à la
        # place de ensure_image_loaded(entry) quand non None. Voir
        # sharpness_tool_qt.py::SharpnessViewerMixin et
        # brightness_tool_qt.py::BrightnessViewerMixin. La correspondance
        # valeur commitée <-> point d'historique vit sur state (pas ici), voir
        # state.py::sharpness_value_by_history_index /
        # brightness_value_by_history_index — elle doit survivre à la
        # fermeture de cette fenêtre, contrairement au preview lui-même.
        self._sharpness_preview_img = None

        # ── État ──────────────────────────────────────────────────────────────
        # zoom_level : échelle réelle par rapport à la taille native de l'image
        # (1.0 = 100% = 1 pixel image = 1 pixel écran). Initialisé au premier
        # affichage pour correspondre à l'ajustement automatique à la fenêtre.
        self.zoom_level      = 1.0
        self._zoom_initialized = False
        self._manual_zoom = False
        self._webtoon_bound_scrolls = 0
        self.page_mode       = "double"
        self.is_fullscreen   = False
        self._validating_crop = False

        self.displayed_left_idx  = None
        self.displayed_right_idx = None

        # GIF
        self.is_animated_gif   = False
        self.gif_is_playing    = False
        self.gif_current_frame = 0
        self._gif_timer        = QTimer(self)
        self._gif_timer.timeout.connect(self._animate_gif_frame)
        self.gif_durations     = []

        # Resize debounce
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._on_resize_debounced)
        self._last_w = 0
        self._last_h = 0

        # Nom masquage plein écran
        self._name_hide_timer = QTimer(self)
        self._name_hide_timer.setSingleShot(True)
        self._name_hide_timer.timeout.connect(self._hide_name_label)

        # ── Layout ───────────────────────────────────────────────────────────
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Canvas de visionneuse
        self._canvas = _ViewerCanvas(self)
        root_layout.addWidget(self._canvas, stretch=1)

        # Barre d'outils flottante (fusion progressive des visionneuses, idees.txt #3)
        self._toolbar = _ViewerToolbar(self)
        if self._initial_tool in ("crop", "straighten", "clone", "text"):
            self._toolbar.set_active_tool(self._initial_tool)
        # Synchronise le slider/spinbox de netteté sur la valeur déjà commitée
        # pour cette page à ce point d'historique, si applicable (ex. fermer
        # la visionneuse après un ajustement puis la rouvrir sur la même page
        # doit réafficher la valeur, pas repartir de 0 — voir state.py::
        # sharpness_value_by_history_index et _reset_sharpness_preview).
        self._reset_sharpness_preview()
        # Idem pour la luminosité/contraste (voir state.py::
        # brightness_value_by_history_index et _reset_brightness_preview).
        self._reset_brightness_preview()
        # Idem pour la saturation (voir saturation_tool_qt.py::
        # _reset_saturation_preview) — remet toujours le slider à 0, pas de
        # resynchronisation sur une valeur commitée (voir sa docstring).
        self._reset_saturation_preview()
        # Idem pour la suppression des couleurs (voir remove_colors_tool_qt.py::
        # _reset_remove_colors_preview) — remet toujours le slider à 0, même
        # principe que saturation.
        self._reset_remove_colors_preview()
        # Idem pour la compression (voir compression_tool_qt.py::
        # _reset_compression_preview) — resynchronise sur la qualité JPEG
        # RÉELLE de la page affichée (detect_jpeg_quality), pas une valeur
        # fixe. Grise aussi l'icône si la page n'est pas JPEG/WEBP/AVIF (voir
        # _refresh_compression_button_state, propre à cet outil).
        self._reset_compression_preview()
        self._refresh_compression_button_state()
        # Idem pour les niveaux (voir levels_tool_qt.py::
        # _reset_levels_preview) — resynchronise sur les 4 valeurs commitées
        # pour cette page à ce point d'historique, ou les valeurs neutres.
        self._reset_levels_preview()
        # Idem pour la transparence (13e outil migré, voir
        # transparency_tool_qt.py::TransparencyViewerMixin) — resynchronise
        # sur l'image de travail déjà en cours pour cette page, s'il y en a
        # une. Grise aussi l'icône si la page n'est pas PNG/WEBP/ICO/AVIF
        # (voir _refresh_transparency_button_state, même principe que
        # compression).
        self._restore_transparency_for_page(self.current_idx)
        self._refresh_transparency_button_state()
        # Idem pour la profondeur de couleur (14e outil migré, voir
        # color_depth_tool_qt.py::ColorDepthViewerMixin) — resynchronise le
        # panneau (radio verrouillé + activation de "Restaurer l'original")
        # sur l'état déjà accumulé pour cette page, s'il y en a un.
        self._sync_color_depth_panel()
        # Idem pour les effets (15e outil migré, voir effects_tool_qt.py::
        # EffectsViewerMixin) — même principe, sauf que le radio verrouillé
        # est mémorisé par page plutôt que dérivé du mode PIL réel.
        self._sync_effects_panel()
        # Idem pour le mode d'image (16e et DERNIER outil migré, voir
        # image_mode_tool_qt.py::ImageModeViewerMixin) — même principe que
        # color_depth (radio verrouillé dérivé du mode PIL réel).
        self._sync_image_mode_panel()

        # Label du nom en bas
        self._name_label = QLabel()
        self._name_label.setAlignment(Qt.AlignCenter)
        self._name_label.setStyleSheet("background: transparent;")
        root_layout.addWidget(self._name_label)

        # Label du zoom (superposé en haut à droite)
        self._zoom_label = QLabel("100%", self)
        self._zoom_label.setStyleSheet("color: #666666; background: transparent;")
        self._zoom_label.adjustSize()

        # Bouton Play/Pause GIF (superposé en haut à gauche)
        self._play_pause_btn = QPushButton("▶", self)
        self._play_pause_btn.setFixedSize(40, 40)
        self._play_pause_btn.clicked.connect(self.toggle_gif_playback)
        self._play_pause_btn.hide()

        # Label du mode de lecture (superposé en haut à gauche)
        self._mode_label = QLabel("", self)
        self._mode_label.setStyleSheet("color: #666666; background: transparent;")
        self._mode_label.adjustSize()

        # ── Raccourcis clavier ────────────────────────────────────────────────
        # Left/Right naviguent de page — mais un bloc de texte en édition (outil
        # "text", idees.txt #3) utilise aussi les flèches simples pour déplacer
        # le curseur dans le texte : sans ce garde, ces deux QShortcut (contexte
        # par défaut Qt.WindowShortcut, actif même si un QTextEdit enfant a le
        # focus) intercepteraient la flèche avant qu'elle n'atteigne l'overlay.
        # Même principe pour l'outil "shapes" : une forme sélectionnée se
        # déplace au clavier (flèches seules, pas de widget de saisie en
        # concurrence — voir shapes_tool_qt.py::ShapeCanvasMixin.shape_key_press),
        # priorité sur la navigation de page tant qu'une forme est active.
        QShortcut(QKeySequence(Qt.Key_Left),       self).activated.connect(
            lambda: None if self._shape_key_nav(Qt.Key_Left) or self._text_block_has_focus()
            else self.navigate(-1))
        QShortcut(QKeySequence(Qt.Key_Right),      self).activated.connect(
            lambda: None if self._shape_key_nav(Qt.Key_Right) or self._text_block_has_focus()
            else self.navigate(1))
        QShortcut(QKeySequence(Qt.Key_Up),         self).activated.connect(
            lambda: self._shape_key_nav(Qt.Key_Up))
        QShortcut(QKeySequence(Qt.Key_Down),       self).activated.connect(
            lambda: self._shape_key_nav(Qt.Key_Down))
        QShortcut(QKeySequence(Qt.Key_Escape),     self).activated.connect(self._on_escape)
        # Suppr/Del : efface le tracé de forme en cours (pas encore posé) s'il
        # y en a un, sinon la forme SÉLECTIONNÉE (pas toutes, contrairement à
        # Échap sans tracé en cours — voir _on_escape) — comportement standard
        # attendu de cette touche dans un éditeur graphique, demande explicite
        # utilisateur ("suppr ou del doit en faire autant" qu'Échap pour le
        # tracé en cours).
        QShortcut(QKeySequence(Qt.Key_Delete),     self).activated.connect(self._on_shape_delete_key)
        QShortcut(QKeySequence(Qt.Key_Backspace),  self).activated.connect(self._on_shape_delete_key)
        QShortcut(QKeySequence(Qt.Key_F11),        self).activated.connect(self.toggle_fullscreen)

        QShortcut(QKeySequence("Ctrl+Z"),          self).activated.connect(self._undo_and_refresh)
        QShortcut(QKeySequence("Ctrl+Shift+Z"),    self).activated.connect(self._redo_and_refresh)
        QShortcut(QKeySequence("Ctrl+Y"),          self).activated.connect(self._redo_and_refresh)
        QShortcut(QKeySequence("Ctrl++"),          self).activated.connect(lambda: self.adjust_zoom(0.1))
        QShortcut(QKeySequence("Ctrl+-"),          self).activated.connect(lambda: self.adjust_zoom(-0.1))
        QShortcut(QKeySequence("Ctrl+0"),          self).activated.connect(self.fit_zoom_to_window)
        QShortcut(QKeySequence("Ctrl+1"),          self).activated.connect(self.reset_zoom)
        # Ctrl+C / Ctrl+V (idees.txt #1 : "je copie une page [affichée dans la
        # visionneuse], je vais plus loin, je colle la page dans une autre") :
        # cette fenêtre est une QDialog séparée de la mosaïque, les
        # raccourcis globaux (PanelWidget._copy_selected/_paste_ctrl_v,
        # MosaicView.py, skill clipboard) ne l'atteignent jamais tant qu'elle
        # a le focus. Ctrl+C copie la page COURAMMENT AFFICHÉE (self.current_idx),
        # pas state.selected_indices (qui peut diverger — l'utilisateur
        # navigue dans la visionneuse sans toucher à la sélection de la
        # mosaïque) — voir _copy_current_page_shortcut.
        QShortcut(QKeySequence("Ctrl+C"),          self).activated.connect(self._copy_current_page_shortcut)
        QShortcut(QKeySequence("Ctrl+V"),          self).activated.connect(self._paste_image_shortcut)

        # ── Signal langue ─────────────────────────────────────────────────────
        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self._closed = False
        self._close_confirmed = False

        self._center_parent = parent
        self._retranslate()
        self.display_image()
        # Le canvas n'a pas encore sa taille réelle à la construction (valeur par
        # défaut Qt) : le fit calculé ci-dessus est donc approximatif. On force un
        # recalcul unique une fois la fenêtre affichée à sa taille définitive.
        from PySide6.QtCore import QTimer as _QTimer
        _QTimer.singleShot(0, self._recompute_initial_fit)

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    # ── Traduction à la volée ─────────────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(12)
        font_zoom = _get_current_font(10)

        self.setWindowTitle(_wt("viewer.title"))
        self.setStyleSheet(f"QDialog {{ background: black; }}")

        self._name_label.setFont(font)
        self._name_label.setStyleSheet(
            f"color: {theme['text']}; background: {theme['bg']};"
        )

        warn_color = "#666666" if not (self.callbacks.get('state') or _state_module.state).dark_mode else "#999999"
        self._zoom_label.setFont(font_zoom)
        self._zoom_label.setStyleSheet(f"color: {warn_color}; background: transparent;")
        self._zoom_label.adjustSize()
        self._zoom_label.move(self.width() - self._zoom_label.width() - 10, 10)

        self._mode_label.setFont(font_zoom)
        self._mode_label.setStyleSheet(f"color: {warn_color}; background: transparent;")
        self._update_mode_label()

        self._play_pause_btn.setFont(_get_current_font(16))
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: none; }}"
        )
        self._play_pause_btn.setStyleSheet(btn_style)

        self._canvas.retranslate_validate_btn()
        self._canvas.retranslate_cancel_btn()

        self._toolbar._apply_theme()
        self._toolbar.retranslate()

    # ── Redisplay avec pan ────────────────────────────────────────────────────

    def _redisplay_with_pan(self):
        """Redessine l'image en tenant compte du décalage de pan."""
        self.display_image(keep_crop_rect=True)

    def _recompute_initial_fit(self):
        """Le zoom initial calculé dans __init__ utilise une taille de canvas
        provisoire (la fenêtre n'a pas encore sa taille réelle à la construction).
        Recalcule une seule fois le fit avec la taille définitive, sauf si
        l'utilisateur a déjà zoomé manuellement entre-temps."""
        if self._manual_zoom:
            return
        self._zoom_initialized = False
        self.display_image(keep_crop_rect=True)
        self._zoom_label.setText(f"{int(self.zoom_level * 100)}%")
        self._zoom_label.adjustSize()
        self._zoom_label.move(self.width() - self._zoom_label.width() - 10, 10)

    # ── Resize de la fenêtre ─────────────────────────────────────────────────

    def _on_resize_debounced(self):
        """Tant qu'aucun zoom manuel n'a été fait, le fit-to-window doit rester
        valable quelle que soit la taille de la fenêtre (plein écran, maximisée,
        etc.) : on force le recalcul du zoom avant de redessiner."""
        if not self._manual_zoom:
            self._zoom_initialized = False
        self.display_image(keep_crop_rect=True)
        self._zoom_label.setText(f"{int(self.zoom_level * 100)}%")
        self._zoom_label.adjustSize()
        self._zoom_label.move(self.width() - self._zoom_label.width() - 10, 10)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        if w != self._last_w or h != self._last_h:
            self._last_w, self._last_h = w, h
            self._resize_timer.start(150)
        # Repositionne le zoom label
        self._zoom_label.adjustSize()
        self._zoom_label.move(w - self._zoom_label.width() - 10, 10)

    # ── Fermeture ─────────────────────────────────────────────────────────────

    def _has_unvalidated_work(self) -> bool:
        """Travail d'outil non validé sur au moins une page (idees.txt #3, partie B) :
        le rectangle/trait/blocs de la page actuellement affichée ne sont pas
        encore dans _crop_by_page/_straighten_by_page/_text_blocks_by_page (ils
        n'y entrent qu'au moment de changer de page), donc toutes les sources
        doivent être vérifiées."""
        if self._canvas.crop_rel_x1 is not None:
            return True
        if self._crop_by_page:
            return True
        if self._canvas._line_img_start is not None:
            return True
        if self._straighten_by_page:
            return True
        if self._canvas.has_text_blocks:
            return True
        if self._text_blocks_by_page:
            return True
        if self._canvas.has_shapes:
            return True
        if self._shapes_by_page:
            return True
        if self._canvas.has_pasted_images:
            return True
        if self._pasted_images_by_page:
            return True
        # Contrairement au crop/straighten/texte/formes (état vivant dans le
        # canvas de la page COURANTE, pas encore transféré dans son dict par
        # page), l'image de travail de transparency est déjà indexée par
        # page dès le premier clic pipette (voir transparency_tool_qt.py) —
        # une seule vérification suffit, pas de doublon canvas+dict à tester.
        return bool(self._transp_work_img_by_page)

    def closeEvent(self, event):
        if self._closed:
            super().closeEvent(event)
            return

        if self._has_unvalidated_work() and not self._close_confirmed:
            event.ignore()
            dlg = ConfirmYNDialog(
                self,
                lambda: _wt("viewer.unvalidated_work_title"),
                lambda: _("viewer.unvalidated_work_message"),
            )
            dlg.result_signal.connect(self._on_close_confirmed)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            return

        self._closed = True

        state = self.callbacks.get('state') or _state_module.state
        self._gif_timer.stop()
        self._name_hide_timer.stop()
        self._resize_timer.stop()
        # Déconnexion du QClipboard.dataChanged global câblé pour griser/
        # dégriser l'icône "Coller une image" en direct (idees.txt #1) —
        # même précaution que language_signal.changed (CLAUDE.md règle UI
        # n°2) : un signal Qt global qui reste connecté à cette barre après
        # sa destruction provoquerait un RuntimeError au prochain changement
        # de presse-papiers.
        self._toolbar.disconnect_paste_image_clipboard_watch()

        self._save_bookmark(state)

        for entry in state.images_data:
            if entry.get("is_image"):
                free_image_memory(entry)

        state.active_viewers -= 1
        if self in image_viewer_refs:
            image_viewer_refs.remove(self)

        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except Exception:
            pass

        super().closeEvent(event)

    def _on_close_confirmed(self, confirmed: bool):
        if confirmed:
            self._close_confirmed = True
            self.close()

    def _save_bookmark(self, state):
        """Sauvegarde la page courante comme marque-page (sauf page 0 et dernière page)."""
        filepath = getattr(state, 'current_file', None)
        if not filepath:
            return
        img_indices = self.get_image_indices()
        if not img_indices:
            return
        try:
            current_pos = img_indices.index(self.current_idx)
        except ValueError:
            return
        last_pos = len(img_indices) - 1
        if current_pos == 0 or current_pos >= last_pos:
            return
        from modules.qt.config_manager import get_config_manager
        cfg = get_config_manager()
        if cfg:
            cfg.set_bookmark(filepath, current_pos)
            on_bm = self.callbacks.get("on_bookmark_changed")
            if on_bm:
                on_bm(img_indices[current_pos])

    # ── Navigation ────────────────────────────────────────────────────────────

    def get_image_indices(self) -> list[int]:
        state = self.callbacks.get('state') or _state_module.state
        return [i for i, e in enumerate(state.images_data) if e["is_image"]]

    def is_wide_image(self, idx: int) -> bool:
        state = self.callbacks.get('state') or _state_module.state
        if idx >= len(state.images_data):
            return False
        entry = state.images_data[idx]
        if not entry["is_image"]:
            return False
        img = ensure_image_loaded(entry)
        if img is None:
            return False
        w, h = img.size
        return (w / h if h > 0 else 0) > 1.5

    def _get_all_ratios(self):
        """Retourne les ratios largeur/hauteur de toutes les images (pour la détection de pages multiples)."""
        state = self.callbacks.get('state') or _state_module.state
        ratios = []
        for entry in state.images_data:
            if not entry["is_image"]:
                continue
            w = entry.get("img_width")
            h = entry.get("img_height")
            if w and h and h > 0:
                ratios.append(w / h)
            else:
                img = ensure_image_loaded(entry)
                if img is not None:
                    ratios.append(img.width / img.height if img.height > 0 else 0)
                else:
                    ratios.append(0)
        return ratios

    def is_multiple_page(self, idx: int) -> bool:
        """Détecte si une page est une page multiple (double, triple…) selon la logique de renumérotation.
        Utilise le ratio relatif à la médiane des pages portrait : mult >= 2 → page multiple."""
        state = self.callbacks.get('state') or _state_module.state
        if idx >= len(state.images_data):
            return False
        entry = state.images_data[idx]
        if not entry["is_image"]:
            return False
        w = entry.get("img_width")
        h = entry.get("img_height")
        if w and h and h > 0:
            ratio = w / h
        else:
            img = ensure_image_loaded(entry)
            if img is None:
                return False
            ratio = img.width / img.height if img.height > 0 else 0
        if ratio <= 0:
            return False
        reference_ratio = compute_reference_ratio(self._get_all_ratios())
        if reference_ratio <= 0:
            return False
        mult = max(1, round(ratio / reference_ratio))
        return mult >= 2

    def _double_page_pair_start(self, img_indices, pos):
        """Retourne True si pos est le début d'une paire en mode page double.
        La parité est recalculée dynamiquement en tenant compte des pages multiples précédentes :
        on simule le défilement depuis le début pour savoir si pos est début ou fin de paire."""
        if pos == 0:
            return False  # pos 0 est toujours affiché seul
        p = 1  # on commence à simuler depuis pos 1
        while p < pos:
            if self.is_multiple_page(img_indices[p]):
                p += 1  # page multiple : seule, avance de 1
            else:
                if p == pos:
                    break
                # paire normale : deux pages
                p += 2
        return p == pos

    def navigate(self, delta: int):
        img_indices = self.get_image_indices()
        if not img_indices:
            return
        try:
            current_pos = img_indices.index(self.current_idx)
            if self.page_mode == "double":
                is_pair_start = self._double_page_pair_start(img_indices, current_pos)
                if delta > 0:
                    if current_pos == 0:
                        new_pos = 1
                    elif self.is_multiple_page(img_indices[current_pos]):
                        new_pos = current_pos + 1
                    elif is_pair_start:
                        if current_pos + 1 < len(img_indices) and self.is_multiple_page(img_indices[current_pos + 1]):
                            new_pos = current_pos + 1
                        else:
                            new_pos = current_pos + 2
                    else:
                        new_pos = current_pos + 1
                else:
                    if current_pos <= 1:
                        new_pos = 0
                    elif self.is_multiple_page(img_indices[current_pos]):
                        new_pos = current_pos - 1
                    elif is_pair_start:
                        new_pos = max(1, current_pos - 2)
                    else:
                        new_pos = current_pos - 1
            else:
                new_pos = current_pos + delta
            new_pos = max(0, min(new_pos, len(img_indices) - 1))
            self._save_crop_for_current_page()
            self._save_straighten_for_current_page()
            self._save_text_for_current_page()
            self._save_shapes_for_current_page()
            self._save_paste_image_for_current_page()
            self.current_idx = img_indices[new_pos]
            if self.page_mode == "webtoon":
                self._canvas.pan_offset_y = 0
            self._check_clear_bookmark_on_last_page(img_indices, new_pos)
            self._restore_crop_for_page(self.current_idx)
            self._restore_straighten_for_page(self.current_idx)
            self._restore_text_for_page(self.current_idx)
            self._restore_shapes_for_page(self.current_idx)
            self._restore_paste_image_for_page(self.current_idx)
            self._restore_transparency_for_page(self.current_idx)
            # Pas de persistance par page pour le clonage (voir idees.txt #3,
            # discussion du 3e outil migré) : la source Ctrl+cliquée est
            # simplement effacée, aucun travail en attente à restaurer.
            self._canvas.clear_clone_source()
            # Idem pour la netteté (5e outil migré, idees.txt #3) et la
            # luminosité/contraste (6e outil migré) : pas de persistance par
            # page — le relâchement du slider commit déjà tout, donc changer
            # de page ne peut qu'annuler un preview visuel abandonné en plein
            # drag (avant relâchement).
            self._reset_sharpness_preview()
            self._reset_brightness_preview()
            self._reset_saturation_preview()
            self._reset_remove_colors_preview()
            self._reset_compression_preview()
            self._refresh_compression_button_state()
            self._reset_levels_preview()
            self._refresh_transparency_button_state()
            self._sync_color_depth_panel()
            self._sync_effects_panel()
            self._sync_image_mode_panel()
            self.display_image(keep_crop_rect=True)
        except ValueError:
            self._save_crop_for_current_page()
            self._save_straighten_for_current_page()
            self._save_text_for_current_page()
            self._save_shapes_for_current_page()
            self._save_paste_image_for_current_page()
            self.current_idx = img_indices[0]
            self._restore_crop_for_page(self.current_idx)
            self._restore_straighten_for_page(self.current_idx)
            self._restore_text_for_page(self.current_idx)
            self._restore_shapes_for_page(self.current_idx)
            self._restore_paste_image_for_page(self.current_idx)
            self._restore_transparency_for_page(self.current_idx)
            self._reset_sharpness_preview()
            self._reset_brightness_preview()
            self._reset_saturation_preview()
            self._reset_remove_colors_preview()
            self._reset_compression_preview()
            self._refresh_compression_button_state()
            self._reset_levels_preview()
            self._refresh_transparency_button_state()
            self._sync_color_depth_panel()
            self._sync_effects_panel()
            self._sync_image_mode_panel()
            self._canvas.clear_clone_source()
            self.display_image(keep_crop_rect=True)

    def _check_clear_bookmark_on_last_page(self, img_indices, current_pos):
        """Efface le marque-page si la dernière page est maintenant visible."""
        last_pos = len(img_indices) - 1
        is_last = False
        if self.page_mode == "double":
            is_last = (current_pos >= last_pos or current_pos + 1 >= last_pos)
        else:
            is_last = (current_pos >= last_pos)
        if not is_last:
            return
        state = self.callbacks.get('state') or _state_module.state
        filepath = getattr(state, 'current_file', None)
        if not filepath:
            return
        from modules.qt.config_manager import get_config_manager
        cfg = get_config_manager()
        if cfg and cfg.get_bookmark(filepath) is not None:
            cfg.remove_bookmark(filepath)
            on_bm = self.callbacks.get("on_bookmark_changed")
            if on_bm:
                on_bm(None)

    WEBTOON_PAGE_TURN_THRESHOLD = 360  # ~3 crans de molette standard (120 par cran)

    def _on_wheel(self, event):
        mods = event.modifiers()
        delta = event.angleDelta().y()
        if mods & Qt.ControlModifier:
            self.adjust_zoom(0.1 if delta > 0 else -0.1)
        elif self.page_mode == "webtoon":
            self._on_webtoon_wheel(delta)
        else:
            step = 2 if self.page_mode == "double" else 1
            self.navigate(-step if delta > 0 else step)

    def _on_webtoon_wheel(self, delta: int):
        """Molette en mode webtoon : scroll vertical dans la page ; une fois en
        butée (haut ou bas), accumule l'intensité des crans de molette
        supplémentaires dans le même sens jusqu'à un seuil, puis change de page
        (évite un changement de page accidentel). L'accumulation se fait sur la
        magnitude du delta (pas le nombre d'événements) car une molette à
        défilement fin envoie beaucoup plus d'événements qu'une molette
        standard pour un même geste physique.

        Un scroll rapide peut envoyer un seul événement avec un delta énorme
        (plusieurs dizaines de crans agrégés) qui dépasse d'un coup la distance
        restante jusqu'à la butée : la partie du delta qui "déborde" au-delà de
        la butée est reportée dans l'accumulation dès ce même événement, sinon
        ce geste ne compte pour rien et l'utilisateur doit recommencer 3 crans
        supplémentaires une fois déjà en butée (ressenti de blocage)."""
        ch = self._canvas.height()
        min_offset = min(0, ch - self._canvas.display_height)
        max_offset = 0
        pan_y = self._canvas.pan_offset_y
        new_offset = pan_y + delta

        if delta > 0:
            overflow = max(0, new_offset - max_offset)
        else:
            overflow = max(0, min_offset - new_offset)

        if overflow > 0:
            self._webtoon_bound_scrolls += overflow
            if self._webtoon_bound_scrolls >= self.WEBTOON_PAGE_TURN_THRESHOLD:
                self._webtoon_bound_scrolls = 0
                self.navigate(-1 if delta > 0 else 1)
                return
        else:
            self._webtoon_bound_scrolls = 0

        clamped = max(min_offset, min(max_offset, new_offset))
        moved = clamped - pan_y
        self._canvas.pan_offset_y = clamped
        self._canvas.display_offset_y += moved
        self._canvas.update()

    # ── Zoom ──────────────────────────────────────────────────────────────────

    def adjust_zoom(self, delta: float):
        self._manual_zoom = True
        self.zoom_level = max(0.1, min(10.0, self.zoom_level + delta))
        self._zoom_label.setText(f"{int(self.zoom_level * 100)}%")
        self._zoom_label.adjustSize()
        self._zoom_label.move(self.width() - self._zoom_label.width() - 10, 10)
        self.display_image(keep_crop_rect=True)

    def reset_zoom(self):
        """Ctrl+1 : zoom à 100% (taille réelle des pixels de l'image)."""
        self._manual_zoom = True
        self.zoom_level = 1.0
        self._zoom_label.setText("100%")
        self._zoom_label.adjustSize()
        self._zoom_label.move(self.width() - self._zoom_label.width() - 10, 10)
        self.display_image(keep_crop_rect=True)

    def fit_zoom_to_window(self):
        """Ctrl+0 : recalcule le zoom pour ajuster la page actuelle à la fenêtre.
        Repasse en mode auto-fit (contrairement aux vrais zooms manuels) : un
        redimensionnement ultérieur de la fenêtre continuera à réajuster le zoom."""
        self._manual_zoom = False
        self._zoom_initialized = False
        self.display_image(keep_crop_rect=True)
        self._zoom_label.setText(f"{int(self.zoom_level * 100)}%")
        self._zoom_label.adjustSize()
        self._zoom_label.move(self.width() - self._zoom_label.width() - 10, 10)

    # ── Plein écran ───────────────────────────────────────────────────────────

    def toggle_fullscreen(self):
        if self._validating_crop:
            return
        if self.is_fullscreen:
            self.showNormal()
            self.is_fullscreen = False
            self._name_label.show()
            self._name_label.setStyleSheet(
                f"color: {get_current_theme()['text']}; background: {get_current_theme()['bg']};"
            )
            self._name_hide_timer.stop()
        else:
            self.showFullScreen()
            self.is_fullscreen = True
            self._name_label.setStyleSheet("color: white; background: black;")
        if not self._manual_zoom:
            self._zoom_initialized = False
        self.display_image(keep_crop_rect=True)

    # ── Mode de lecture ───────────────────────────────────────────────────────

    def _update_mode_label(self):
        if self.is_animated_gif:
            self._mode_label.hide()
            return
        if self.page_mode == "single":
            text = _("viewer.mode_single")
        elif self.page_mode == "double":
            text = _("viewer.mode_double")
        elif self.page_mode == "continuous":
            text = _("viewer.mode_continuous")
        else:
            text = _("viewer.mode_webtoon")
        self._mode_label.setText(text)
        self._mode_label.adjustSize()
        self._mode_label.move(10, 10)
        self._mode_label.show()

    def toggle_double_page(self):
        if self.page_mode == "single":
            self.page_mode = "double"
        elif self.page_mode == "double":
            self.page_mode = "continuous"
        elif self.page_mode == "continuous":
            self.page_mode = "webtoon"
        else:
            self.page_mode = "single"
        if self.page_mode == "webtoon":
            self._canvas.pan_offset_y = 0
            self._webtoon_bound_scrolls = 0
            if not self._manual_zoom:
                self._zoom_initialized = False
        self._update_mode_label()
        self.display_image(keep_crop_rect=True)

    # ── Undo/Redo ─────────────────────────────────────────────────────────────

    def _undo_and_refresh(self):
        if self._block_undo_redo_for_unvalidated_work():
            return
        fn = self.callbacks.get("undo_action")
        if fn:
            fn()
        self._refresh_after_undo_redo()

    def _redo_and_refresh(self):
        if self._block_undo_redo_for_unvalidated_work():
            return
        fn = self.callbacks.get("redo_action")
        if fn:
            fn()
        self._refresh_after_undo_redo()

    def _block_undo_redo_for_unvalidated_work(self) -> bool:
        """True si Ctrl+Z/Ctrl+Y (et les boutons undo/redo de la barre,
        _on_undo_clicked/_on_redo_clicked dans viewer_toolbar_qt.py, qui
        passent tous les deux par _undo_and_refresh/_redo_and_refresh) doivent
        être bloqués — trou de conception découvert le 2026-08-15 (transparency,
        13e outil migré) : l'historique undo/redo unique de l'appli
        (state.history) n'a jamais eu connaissance du travail non validé
        propre à un outil de cette barre (crop/straighten/text/shapes/
        transparency, voir _has_unvalidated_work) — un Ctrl+Z pendant qu'un
        tel travail existe restaure entry['bytes'] à un état antérieur SOUS
        un travail qui, lui, référence encore l'ancien entry['bytes'] (le
        plus dangereux : l'image de travail RGBA de transparency, capturée
        une seule fois au premier clic pipette et jamais réactualisée) —
        valider ensuite écraserait le undo avec une donnée périmée,
        silencieusement. No-op silencieux (pas de MsgDialog) tant qu'il reste
        du travail non validé — même principe qu'un Ctrl+Z sans rien à
        annuler ailleurs dans l'appli : aucune action possible, donc aucun
        effet, sans avertissement."""
        return self._has_unvalidated_work()

    def _refresh_after_undo_redo(self):
        """Rafraîchit la visionneuse après undo/redo en invalidant le cache image."""
        state = self.callbacks.get('state') or _state_module.state
        # Invalide le cache PIL de l'image courante pour forcer le rechargement depuis bytes
        if 0 <= self.current_idx < len(state.images_data):
            entry = state.images_data[self.current_idx]
            entry["img"] = None
        # Le slider/spinbox de netteté reste sur la dernière valeur appliquée
        # après un commit (voir perform_sharpness), donc un undo/redo doit le
        # resynchroniser explicitement sur (page courante, history_index
        # courant) — voir _reset_sharpness_preview, qui retrouve la valeur
        # dans state.sharpness_value_by_history_index (survit même à une
        # fermeture/réouverture de la visionneuse, voir state.py) ou remet à
        # 0 si aucun commit sharpness ne correspond à ce point précis. Même
        # principe pour la luminosité/contraste (state.
        # brightness_value_by_history_index, _reset_brightness_preview).
        self._reset_sharpness_preview()
        self._reset_brightness_preview()
        self._reset_saturation_preview()
        self._reset_remove_colors_preview()
        self._reset_compression_preview()
        self._refresh_compression_button_state()
        self._reset_levels_preview()
        # L'image de travail de transparency, elle, ne doit PAS être annulée
        # par un undo/redo (contrairement aux previews des autres modes) :
        # c'est un travail non validé, indépendant de l'historique undo/redo
        # (même principe que _crop_by_page/_shapes_by_page, qui survivent
        # eux aussi à un undo/redo) — seul le bouton grisable est à
        # rafraîchir ici, le format de la page ayant pu changer.
        self._refresh_transparency_button_state()
        # Le snapshot "avant premier changement" de color_depth (state.
        # color_depth_original_bytes_by_page), lui, ne doit PAS être vidé par
        # un undo/redo (décision explicite utilisateur, 2026-08-16 — "sinon
        # il y a un risque de confusion") : seul le panneau (radio verrouillé/
        # activation de "Restaurer l'original") est à resynchroniser ici,
        # dérivé du mode PIL réel de l'image après le undo/redo.
        self._sync_color_depth_panel()
        # Même principe pour les effets (state.effect_original_bytes_by_page/
        # effect_key_by_page) : ni l'un ni l'autre n'est vidé par un
        # undo/redo, seul le panneau est resynchronisé ici.
        self._sync_effects_panel()
        # Même principe pour le mode d'image (state.
        # image_mode_original_bytes_by_page) : pas vidé par un undo/redo,
        # seul le panneau (radio verrouillé dérivé du mode PIL réel) est
        # resynchronisé ici.
        self._sync_image_mode_panel()
        self.display_image()
        self._toolbar.refresh_undo_redo_state()

    def _refresh_compression_button_state(self):
        """Grise/dégrise l'icône "compression" de la barre selon le format de
        la page COURANTE (idees.txt #3, seul outil migré à ce jour dont la
        disponibilité dépend du format de fichier) — appelé à l'ouverture de
        la visionneuse, à chaque changement de page (navigate) et après
        undo/redo, jamais à la construction seule (le format peut changer
        d'une page à l'autre). Rafraîchit aussi le tooltip (texte différent
        activé/désactivé, voir viewer_toolbar_qt.py::
        _update_compression_tooltip)."""
        state = self.callbacks.get('state') or _state_module.state
        if not (0 <= self.current_idx < len(state.images_data)):
            return
        entry = state.images_data[self.current_idx]
        self._toolbar._buttons["compression"].set_enabled_state(is_compressible_entry(entry))
        self._toolbar._update_compression_tooltip()

    def _refresh_transparency_button_state(self):
        """Grise/dégrise l'icône "transparency" de la barre selon le format
        de la page COURANTE (13e outil migré, 2e outil migré dont la
        disponibilité dépend du format de fichier après compression) —
        appelé à l'ouverture de la visionneuse, à chaque changement de page
        (navigate) et après undo/redo, jamais à la construction seule (le
        format peut changer d'une page à l'autre). Rafraîchit aussi le
        tooltip (texte différent activé/désactivé, même principe que
        compression, voir viewer_toolbar_qt.py::
        _update_transparency_tooltip)."""
        state = self.callbacks.get('state') or _state_module.state
        if not (0 <= self.current_idx < len(state.images_data)):
            return
        entry = state.images_data[self.current_idx]
        self._toolbar._buttons["transparency"].set_enabled_state(
            is_transparency_supported_entry(entry))
        self._toolbar._update_transparency_tooltip()

    # ── Touches clavier ───────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_D:
            self.toggle_double_page()
        else:
            super().keyPressEvent(event)

    # ── Outil texte : focus clavier ──────────────────────────────────────────

    def _text_block_has_focus(self) -> bool:
        """True si un bloc de texte a actuellement le focus clavier — les
        raccourcis Left/Right de navigation doivent alors céder la priorité au
        déplacement du curseur natif de QTextEdit (voir __init__)."""
        block = self._canvas._text_active_block()
        return block is not None and block.overlay.isVisible() and block.overlay.hasFocus()

    # ── Outil "paste_image" : raccourcis clavier dédiés ─────────────────────────

    def _copy_current_page_shortcut(self):
        """Ctrl+C (idees.txt #1) — copie la page COURAMMENT AFFICHÉE dans la
        visionneuse vers le presse-papiers système (CF_HDROP), pour qu'un
        Ctrl+V ultérieur (ici ou ailleurs dans l'appli) la retrouve. Voir
        clipboard_qt.py::copy_single_entry_to_system_clipboard, variante de
        copy_to_system_clipboard qui ne dépend pas de state.selected_indices
        (la page affichée dans la visionneuse peut diverger de la sélection
        de la mosaïque).

        Refuse (avertissement non-modal, PAS de copie) en mode "double" ou
        "continuous" — décision explicite utilisateur : self.current_idx en
        mode double/continuous correspond soit à la page GAUCHE soit à la
        page DROITE de la paire combinée selon comment on y est arrivé (voir
        display_image), ambigu pour l'utilisateur qui copie en pensant
        obtenir une page précise visible à l'écran. Message renvoie vers la
        touche D (bascule de mode, voir keyPressEvent) plutôt que de
        deviner/forcer un mode à sa place."""
        if self.page_mode in ("double", "continuous"):
            from modules.qt.dialogs_qt import MsgDialog
            dlg = MsgDialog(
                self,
                "messages.warnings.copy_page_requires_single_mode.title",
                "messages.warnings.copy_page_requires_single_mode.message",
            )
            dlg.show_nonmodal()
            return
        from modules.qt import state as _state_module
        from modules.qt.clipboard_qt import copy_single_entry_to_system_clipboard
        from modules.qt.temp_files import get_mosaicview_temp_dir
        state = self.callbacks.get('state') or _state_module.state
        if not (0 <= self.current_idx < len(state.images_data)):
            return
        entry = state.images_data[self.current_idx]
        copy_single_entry_to_system_clipboard(entry, get_mosaicview_temp_dir, self)

    def _paste_image_shortcut(self):
        """Ctrl+V (idees.txt #1) — voir _ViewerToolbar.paste_image_from_clipboard
        (viewer_toolbar_qt.py), réutilisée telle quelle : cette QDialog est
        séparée de la mosaïque, le Ctrl+V global (PanelWidget._paste_ctrl_v,
        skill clipboard) ne l'atteint jamais tant qu'elle a le focus."""
        self._toolbar.paste_image_from_clipboard()

    # ── Outil formes : déplacement clavier ────────────────────────────────────

    def _shape_key_nav(self, key) -> bool:
        """Déplace d'1px la forme sélectionnée si l'outil "shapes" est actif
        et qu'une forme est sélectionnée, OU l'image collée sélectionnée si
        l'outil "paste_image" est actif (idees.txt #1 : "l'utilisateur la
        place" — même mécanisme de déplacement clavier fin réutilisé tel
        quel, les deux objets partagent les mêmes champs ix1/iy1/ix2/iy2) —
        retourne True si la touche a été consommée par ce déplacement (les
        QShortcut Left/Right cèdent alors la priorité à la navigation de
        page, voir __init__)."""
        active_tool = self._toolbar.active_tool
        if active_tool == "shapes" and self._canvas._shape_active is not None:
            obj = self._canvas._shape_active
            on_changed = self._on_shapes_content_changed
        elif active_tool == "paste_image" and self._canvas._pasted_image_active is not None:
            obj = self._canvas._pasted_image_active
            on_changed = self._on_paste_image_content_changed
        else:
            return False
        dx = dy = 0
        if key == Qt.Key_Left:    dx = -1
        elif key == Qt.Key_Right: dx = 1
        elif key == Qt.Key_Up:    dy = -1
        elif key == Qt.Key_Down:  dy = 1
        else:
            return False
        obj.ix1 += dx; obj.iy1 += dy
        obj.ix2 += dx; obj.iy2 += dy
        self._canvas.update()
        on_changed()
        return True

    # ── Bouton "Annuler" partagé (crop/straighten/text/shapes/transparency) ────

    def _cancel_tool_work(self, tool: str):
        """Annule TOUT le travail non validé de l'outil `tool` d'un coup —
        même geste que le clic sur le bouton "Annuler" flottant (2026-08-15,
        décision explicite utilisateur : bouton partagé, symétrique du
        bouton "Valider", pour les 5 outils qui en ont un). Réutilise le
        même code que les branches correspondantes de _on_escape, mais
        indexé directement par `tool` (l'outil réellement actif) plutôt que
        par une détection en cascade — _on_escape doit rester tel quel car
        Échap a une logique de priorité entre outils (crop avant straighten
        avant clone...) qui n'a pas de sens ici : ce bouton n'agit que sur
        l'outil actuellement sélectionné dans la barre, jamais un autre."""
        if tool == "crop":
            self._canvas.clear_crop()
            self._crop_by_page.pop(self.current_idx, None)
        elif tool == "straighten":
            self._canvas.clear_line()
            self._toolbar._angle_panel.reset()
            self._straighten_by_page.pop(self.current_idx, None)
        elif tool == "text":
            self._canvas.clear_text_blocks()
            self._toolbar._text_panel.set_visible_for_tool(self._toolbar.active_tool)
            self._text_blocks_by_page.pop(self.current_idx, None)
        elif tool == "shapes":
            self._canvas.clear_shapes()
            self._shapes_by_page.pop(self.current_idx, None)
            self._on_shapes_content_changed()
        elif tool == "transparency":
            self._clear_transparency_work()  # rafraîchit déjà _update_validate_btn_state()
        elif tool == "paste_image":
            self._canvas.clear_pasted_images()
            self._pasted_images_by_page.pop(self.current_idx, None)
            self._on_paste_image_content_changed()
        self._canvas._update_validate_btn_state()
        self._canvas._update_cancel_btn_state()

    # ── Échap ─────────────────────────────────────────────────────────────────

    def _on_escape(self):
        if self._canvas.has_crop or self._canvas._crop_start is not None:
            self._canvas.clear_crop()
            self._crop_by_page.pop(self.current_idx, None)
            self._canvas._update_cancel_btn_state()
        elif self._canvas.has_line or self._canvas._line_start is not None:
            self._canvas.clear_line()
            self._toolbar._angle_panel.reset()
            self._straighten_by_page.pop(self.current_idx, None)
            self._canvas._update_cancel_btn_state()
        elif self._canvas._clone_source_img is not None:
            # Efface la source Ctrl+cliquée — rien d'autre à annuler pour cet
            # outil (voir idees.txt #3, le clonage n'a pas de "travail en
            # attente de validation" : chaque coup de tampon est déjà commité).
            self._canvas.clear_clone_source()
        elif self._canvas.has_text_blocks:
            self._canvas.clear_text_blocks()
            self._toolbar._text_panel.set_visible_for_tool(self._toolbar.active_tool)
            # Recalcule seulement l'ÉTAT du bouton (repasse en gris,
            # has_text_blocks vient de redevenir faux) — ne touche jamais à sa
            # visibilité (mécanisme unique, voir _update_validate_btn_state).
            self._canvas._update_validate_btn_state()
            self._canvas._update_cancel_btn_state()
            self._text_blocks_by_page.pop(self.current_idx, None)
        elif self._canvas._shape_draw_start is not None:
            # Tracé de forme en cours (pas encore posée) : annule seulement
            # ce tracé, ne touche pas aux formes déjà posées sur la page.
            self._canvas._shape_draw_start = None
            self._canvas._shape_draw_end = None
            self._canvas.update()
        elif self._canvas.has_shapes:
            self._canvas.clear_shapes()
            self._shapes_by_page.pop(self.current_idx, None)
            self._on_shapes_content_changed()
            self._canvas._update_cancel_btn_state()
        elif self._canvas.has_pasted_images:
            # Même principe que "shapes" ci-dessus (idees.txt #1) : Échap
            # annule TOUTES les images collées de la page courante d'un coup,
            # pas une seule à la fois.
            self._canvas.clear_pasted_images()
            self._pasted_images_by_page.pop(self.current_idx, None)
            self._on_paste_image_content_changed()
            self._canvas._update_cancel_btn_state()
        elif self.current_idx in self._transp_work_img_by_page:
            # Travail de transparency en attente sur la page courante — testé
            # AVANT le bloc self._sharpness_preview_img ci-dessous : ce champ
            # de preview partagé est aussi utilisé par transparency (voir
            # _update_transparency_preview), donc sans cette priorité le
            # travail accumulé serait ignoré par le reset générique (qui ne
            # touche pas à _transp_work_img_by_page) au lieu d'être
            # entièrement annulé (idees.txt #3, décision explicite 2026-08-15 :
            # Échap/Suppr annule TOUT le travail en attente d'un coup, pas un
            # undo clic par clic — voir _clear_transparency_work).
            self._clear_transparency_work()
            self._canvas._update_cancel_btn_state()
        elif self._sharpness_preview_img is not None:
            # Champ de preview PARTAGÉ entre sharpness/unsharp/brightness/
            # saturation/remove_colors/compression/levels (un seul outil actif
            # à la fois) — un seul des reset a un effet réel selon l'outil
            # actuellement actif dans la barre, les autres sont des no-op
            # silencieux (retrouvent (0,0)/0/valeur détectée et redéfinissent
            # déjà self._sharpness_preview_img à None, sans dégât).
            self._reset_sharpness_preview()
            self._reset_brightness_preview()
            self._reset_saturation_preview()
            self._reset_remove_colors_preview()
            self._reset_compression_preview()
            self._reset_levels_preview()
        elif self._toolbar._levels_panel.active_pipette is not None:
            # Pipette armée mais aucun clic effectué (pas de preview en cours
            # au sens des sliders) — Échap la désarme simplement, comme les
            # autres outils annulent leur geste en cours.
            self._toolbar._levels_panel._deactivate_pipettes()
        elif self.is_fullscreen:
            self.toggle_fullscreen()
        else:
            self.close()

    def _on_shape_delete_key(self):
        """Suppr/Del : sur l'outil "shapes", efface le tracé en cours s'il y
        en a un (même geste qu'Échap dans ce cas précis), sinon UNIQUEMENT la
        forme actuellement sélectionnée (pas toutes les formes de la page,
        contrairement à Échap sans tracé en cours — voir _on_escape) :
        comportement standard d'une touche Suppr dans un éditeur graphique.
        Sur l'outil "transparency" (idees.txt #3, décision explicite
        2026-08-15) : annule TOUT le travail en attente d'un coup, même
        geste qu'Échap pour cet outil (pas de notion de "sélection" à
        effacer individuellement comme pour les formes). No-op si aucun de
        ces deux outils n'est actif ou si rien n'est à effacer, pour ne
        jamais interférer avec un Suppr destiné à un autre widget (ex. une
        spinbox en cours de frappe)."""
        if self._toolbar.active_tool == "transparency":
            if self.current_idx in self._transp_work_img_by_page:
                self._clear_transparency_work()
            return
        if self._toolbar.active_tool == "paste_image":
            # Même principe que "shapes" ci-dessous : efface UNIQUEMENT
            # l'image collée actuellement sélectionnée (pas toutes celles de
            # la page), comportement standard d'un éditeur graphique.
            canvas = self._canvas
            active = canvas._pasted_image_active
            if active is not None and active in canvas._pasted_images:
                canvas._pasted_images.remove(active)
                canvas._pasted_image_active = None
                canvas.update()
                self._on_paste_image_content_changed()
            return
        if self._toolbar.active_tool != "shapes":
            return
        canvas = self._canvas
        if canvas._shape_draw_start is not None:
            canvas._shape_draw_start = None
            canvas._shape_draw_end = None
            canvas.update()
            return
        active = canvas._shape_active
        if active is not None and active in canvas._shapes:
            canvas._shapes.remove(active)
            canvas._shape_active = None
            canvas.update()
            self._on_shapes_content_changed()

    # ── Marque-page ───────────────────────────────────────────────────────────

    def _delete_current_bookmark(self):
        state = self.callbacks.get('state') or _state_module.state
        filepath = getattr(state, 'current_file', None)
        if not filepath:
            return
        from modules.qt.config_manager import get_config_manager
        cfg = get_config_manager()
        if cfg:
            cfg.remove_bookmark(filepath)
            on_bm = self.callbacks.get("on_bookmark_changed")
            if on_bm:
                on_bm(None)

    # ── Menu contextuel ───────────────────────────────────────────────────────

    def _show_context_menu(self, global_pos: QPoint):
        menu = QMenu(self)
        font = _get_current_font(9)
        menu.setFont(font)

        theme = get_current_theme()
        menu.setStyleSheet(
            f"QMenu {{ background: {theme['toolbar_bg']}; color: {theme['text']}; }}"
            f"QMenu::item:selected {{ background: {theme['separator']}; }}"
        )

        menu.addAction(_("context_menu.viewer.prev_page"),  lambda: self.navigate(-1))
        menu.addAction(_("context_menu.viewer.next_page"),  lambda: self.navigate(1))
        menu.addSeparator()
        menu.addAction(_("context_menu.viewer.zoom_in"),    lambda: self.adjust_zoom(0.1))
        menu.addAction(_("context_menu.viewer.zoom_out"),   lambda: self.adjust_zoom(-0.1))
        menu.addAction(_("context_menu.viewer.zoom_reset"), self.fit_zoom_to_window)
        menu.addAction(_("context_menu.viewer.zoom_100"),   self.reset_zoom)
        menu.addSeparator()

        if self.page_mode == "single":
            mode_label = _("context_menu.viewer.reading_mode_double")
        elif self.page_mode == "double":
            mode_label = _("context_menu.viewer.reading_mode_continuous")
        elif self.page_mode == "continuous":
            mode_label = _("context_menu.viewer.reading_mode_webtoon")
        else:
            mode_label = _("context_menu.viewer.reading_mode_single")
        menu.addAction(mode_label, self.toggle_double_page)
        menu.addSeparator()

        fs_label = (_("context_menu.viewer.fullscreen_exit") if self.is_fullscreen
                    else _("context_menu.viewer.fullscreen"))
        menu.addAction(fs_label, self.toggle_fullscreen)
        menu.addSeparator()
        menu.addAction(_("context_menu.viewer.close_viewer"), self.close)
        menu.addSeparator()

        state = self.callbacks.get('state') or _state_module.state
        filepath = getattr(state, 'current_file', None)
        from modules.qt.config_manager import get_config_manager
        cfg = get_config_manager()
        has_bookmark = bool(filepath and cfg and cfg.get_bookmark(filepath) is not None)
        act_del_bm = menu.addAction(_("context_menu.viewer.delete_bookmark"), self._delete_current_bookmark)
        act_del_bm.setEnabled(has_bookmark)

        menu.exec(global_pos)

    # ── GIF animé ─────────────────────────────────────────────────────────────

    def toggle_gif_playback(self):
        if not self.is_animated_gif:
            return
        if self.gif_is_playing:
            self._gif_timer.stop()
            self.gif_is_playing = False
            self._play_pause_btn.setText("▶")
        else:
            self.gif_is_playing = True
            self._play_pause_btn.setText("⏸")
            self._schedule_gif_frame()

    def _schedule_gif_frame(self):
        if not self.gif_is_playing or not self.is_animated_gif:
            return
        duration = self.gif_durations[self.gif_current_frame] if self.gif_current_frame < len(self.gif_durations) else 100
        self._gif_timer.start(duration)

    def _animate_gif_frame(self):
        self._gif_timer.stop()
        state = self.callbacks.get('state') or _state_module.state
        if not self.gif_is_playing or not self.is_animated_gif:
            return
        entry = state.images_data[self.current_idx]
        frame_count = entry.get("gif_frame_count", 0)
        if frame_count == 0:
            return
        frame = get_gif_frame(entry, self.gif_current_frame)
        if frame is None:
            return
        # Redimensionne la frame
        cw = self._canvas.width()
        ch = self._canvas.height() - 40
        if cw <= 1: cw = 800
        if ch <= 1: ch = 540
        fw, fh = frame.size
        final_w = max(1, int(fw * self.zoom_level))
        final_h = max(1, int(fh * self.zoom_level))
        # Pixmap stocké à la résolution native de la frame, étiré par Qt à l'affichage.
        frame_has_alpha = (frame.mode in ('RGBA', 'LA') or
                           (frame.mode == 'P' and 'transparency' in frame.info))
        if frame_has_alpha:
            frame = _compose_on_checkerboard(frame)
        pixmap = _pil_to_qpixmap(frame)
        offset_x = (cw - final_w) // 2 + self._canvas.pan_offset_x
        if self.page_mode == "webtoon":
            offset_y = self._canvas.pan_offset_y
        else:
            offset_y = (ch - final_h) // 2 + self._canvas.pan_offset_y
        self._canvas.set_pixmap_and_geometry(pixmap, offset_x, offset_y, final_w, final_h)
        self.gif_current_frame = (self.gif_current_frame + 1) % frame_count
        self._schedule_gif_frame()

    def _stop_gif(self):
        self._gif_timer.stop()
        self.gif_is_playing = False

    # ── Affichage ─────────────────────────────────────────────────────────────

    def display_image(self, keep_crop_rect: bool = False):
        state = self.callbacks.get('state') or _state_module.state
        if self.current_idx >= len(state.images_data):
            return

        if not keep_crop_rect:
            self._canvas.clear_crop()
            self._canvas.clear_line()
            self._toolbar._angle_panel.reset()

        img_indices = self.get_image_indices()
        if not img_indices:
            return
        try:
            current_pos = img_indices.index(self.current_idx)
        except ValueError:
            return

        cw = self._canvas.width()
        ch = self._canvas.height()
        if cw <= 1: cw = 780
        if ch <= 1: ch = 540
        viewer_w = cw - 20
        viewer_h = ch - 20

        if self.page_mode == "double":
            if self.is_multiple_page(self.current_idx):
                self._display_single_page(self.current_idx, viewer_w, viewer_h)
            elif current_pos == 0:
                self._display_single_page(img_indices[0], viewer_w, viewer_h)
            else:
                is_pair_start = self._double_page_pair_start(img_indices, current_pos)
                if is_pair_start:
                    left_idx  = img_indices[current_pos]
                    right_idx = img_indices[current_pos + 1] if current_pos + 1 < len(img_indices) else None
                    if self.is_multiple_page(left_idx) or (right_idx and self.is_multiple_page(right_idx)):
                        self._display_single_page(left_idx, viewer_w, viewer_h)
                    else:
                        self._display_double_page(left_idx, right_idx, viewer_w, viewer_h)
                else:
                    left_idx  = img_indices[current_pos - 1] if current_pos > 0 else None
                    right_idx = img_indices[current_pos]
                    if self.is_multiple_page(right_idx):
                        self._display_single_page(right_idx, viewer_w, viewer_h)
                    else:
                        self._display_double_page(left_idx, right_idx, viewer_w, viewer_h)

        elif self.page_mode == "continuous":
            if self.is_wide_image(self.current_idx):
                self._display_single_page(self.current_idx, viewer_w, viewer_h)
            elif current_pos == 0:
                self._display_single_page(img_indices[0], viewer_w, viewer_h)
            else:
                left_idx  = img_indices[current_pos]
                right_idx = img_indices[current_pos + 1] if current_pos + 1 < len(img_indices) else None
                if self.is_wide_image(left_idx) or (right_idx and self.is_wide_image(right_idx)):
                    self._display_single_page(left_idx, viewer_w, viewer_h)
                else:
                    self._display_double_page(left_idx, right_idx, viewer_w, viewer_h)
        else:
            self._display_single_page(self.current_idx, viewer_w, viewer_h)

    WEBTOON_MAX_WIDTH_PX = 900

    def _display_single_page(self, idx: int, viewer_w: int, viewer_h: int):
        state = self.callbacks.get('state') or _state_module.state
        entry = state.images_data[idx]
        if not entry["is_image"]:
            return

        # Preview live des outils "sharpness"/"unsharp"/"brightness"/
        # "saturation"/"remove_colors"/"compression"/"levels" (non appliqué à
        # entry['bytes'], champ partagé — un seul actif à la fois) :
        # uniquement pour la page réellement affichée, voir
        # sharpness_tool_qt.py::SharpnessViewerMixin._update_sharpness_preview,
        # brightness_tool_qt.py::BrightnessViewerMixin._update_brightness_preview,
        # saturation_tool_qt.py::SaturationViewerMixin._update_saturation_preview,
        # remove_colors_tool_qt.py::RemoveColorsViewerMixin._update_remove_colors_preview,
        # compression_tool_qt.py::CompressionViewerMixin._update_compression_preview
        # et levels_tool_qt.py::LevelsViewerMixin._update_levels_preview.
        if self._sharpness_preview_img is not None and idx == self.current_idx:
            img = self._sharpness_preview_img
        else:
            img = ensure_image_loaded(entry)
        if img is None:
            return

        self.displayed_left_idx  = None
        self.displayed_right_idx = None
        self._stop_gif()

        self.is_animated_gif = entry.get("is_animated_gif", False)
        if self.is_animated_gif:
            self.gif_durations = entry.get("gif_durations", [])
            self.gif_current_frame = 0
            self._play_pause_btn.setText("▶")
            self._play_pause_btn.move(10, 10)
            self._play_pause_btn.show()
            self._play_pause_btn.raise_()
        else:
            self._play_pause_btn.hide()
            self.gif_durations = []
        self._update_mode_label()

        img = img.copy()
        has_alpha = (img.mode in ('RGBA', 'LA') or
                     (img.mode == 'P' and 'transparency' in img.info))
        img_w, img_h = img.size
        if not self._zoom_initialized:
            if self.page_mode == "webtoon":
                self.zoom_level = min(self.WEBTOON_MAX_WIDTH_PX, viewer_w) / img_w
            else:
                self.zoom_level = min(viewer_w / img_w, viewer_h / img_h)
            self._zoom_initialized = True
        final_w = max(1, int(img_w * self.zoom_level))
        final_h = max(1, int(img_h * self.zoom_level))
        # Le pixmap est stocké à la résolution source : c'est Qt qui l'étire à
        # l'affichage (voir _ViewerCanvas.paintEvent), pas un resize() PIL ici.
        if has_alpha:
            img = _compose_on_checkerboard(img)
        pixmap = _pil_to_qpixmap(img)

        cw = self._canvas.width()
        ch = self._canvas.height()
        if cw <= 1: cw = viewer_w
        if ch <= 1: ch = viewer_h
        offset_x = (cw - final_w) // 2 + self._canvas.pan_offset_x
        if self.page_mode == "webtoon":
            offset_y = self._canvas.pan_offset_y
        else:
            offset_y = (ch - final_h) // 2 + self._canvas.pan_offset_y

        self._canvas.set_pixmap_and_geometry(pixmap, offset_x, offset_y, final_w, final_h)

        # Redessine le rubber-band si coordonnées relatives disponibles
        if self._canvas.crop_rel_x1 is not None:
            x1 = int(offset_x + self._canvas.crop_rel_x1 * final_w)
            y1 = int(offset_y + self._canvas.crop_rel_y1 * final_h)
            x2 = int(offset_x + self._canvas.crop_rel_x2 * final_w)
            y2 = int(offset_y + self._canvas.crop_rel_y2 * final_h)
            self._canvas._crop_start = QPoint(x1, y1)
            self._canvas._crop_end   = QPoint(x2, y2)
            self._canvas.update()
            # Recalcule seulement l'ÉTAT du bouton (texte/couleur/position),
            # jamais sa visibilité — voir _update_validate_btn_state, seul
            # mécanisme de visibilité = _ViewerToolbar.show_and_schedule_hide/
            # _on_hide_timeout. Si l'outil actif n'est pas crop,
            # _update_validate_btn_state() elle-même ne fait rien (key is None) :
            # pas besoin d'un appel "hide" séparé ici. Bouton "Annuler" jumeau
            # rafraîchi juste à côté (même condition, même raisonnement).
            self._canvas._update_validate_btn_state()
            self._canvas._update_cancel_btn_state()

        # Redessine le trait de redressage si un trait est mémorisé pour cette page
        if self._canvas.has_line:
            self._canvas._sync_line_from_image()
            self._canvas.update()
            self._canvas._update_validate_btn_state()
            self._canvas._update_cancel_btn_state()
            self._toolbar._angle_panel.set_visible_for_tool(
                "straighten" if self._toolbar.active_tool == "straighten" else None)

        # Repositionne les blocs de texte de cette page (widgets Qt enfants du
        # canvas, pas un simple dessin — doivent suivre offset_x/y/zoom comme
        # le fait paint_crop_rect/paint_straighten_line pour leurs overlays).
        if self._canvas.has_text_blocks:
            self._canvas.reposition_text_blocks()
            self._canvas._update_validate_btn_state()
            self._canvas._update_cancel_btn_state()

        # Nom de fichier
        img_indices = self.get_image_indices()
        pos = img_indices.index(idx) + 1
        total = len(img_indices)
        self._name_label.setText(f"{entry['orig_name']} ({pos}/{total})")

        self._schedule_name_hide()

    def _display_double_page(self, left_idx, right_idx, viewer_w: int, viewer_h: int):
        state = self.callbacks.get('state') or _state_module.state
        self._stop_gif()
        self._play_pause_btn.hide()
        self.is_animated_gif = False

        self.displayed_left_idx  = left_idx
        self.displayed_right_idx = right_idx
        self._update_mode_label()

        left_img = right_img = None
        if left_idx is not None and left_idx < len(state.images_data):
            e = state.images_data[left_idx]
            if e["is_image"]:
                loaded = ensure_image_loaded(e)
                if loaded:
                    left_img = loaded.copy()
        if right_idx is not None and right_idx < len(state.images_data):
            e = state.images_data[right_idx]
            if e["is_image"]:
                loaded = ensure_image_loaded(e)
                if loaded:
                    right_img = loaded.copy()

        if not left_img and not right_img:
            return
        if not right_img:
            self._display_single_page(left_idx, viewer_w, viewer_h)
            return

        # Normalise les hauteurs
        lw, lh = left_img.size
        rw, rh = right_img.size
        max_h = max(lh, rh)
        if lh != max_h:
            left_img = left_img.resize((int(lw * max_h / lh), max_h), Image.Resampling.LANCZOS)
            lw = left_img.size[0]
        if rh != max_h:
            right_img = right_img.resize((int(rw * max_h / rh), max_h), Image.Resampling.LANCZOS)
            rw = right_img.size[0]

        combined_w = lw + rw
        left_has_alpha  = left_img.mode  in ('RGBA', 'LA') or (left_img.mode  == 'P' and 'transparency' in left_img.info)
        right_has_alpha = right_img.mode in ('RGBA', 'LA') or (right_img.mode == 'P' and 'transparency' in right_img.info)
        has_alpha = left_has_alpha or right_has_alpha
        if has_alpha:
            combined = Image.new('RGBA', (combined_w, max_h), (0, 0, 0, 0))
            left_rgba  = left_img.convert('RGBA')
            right_rgba = right_img.convert('RGBA')
            combined.paste(left_rgba,  (0,  0), left_rgba)
            combined.paste(right_rgba, (lw, 0), right_rgba)
        else:
            combined = Image.new('RGB', (combined_w, max_h), 'black')
            combined.paste(left_img,  (0,  0))
            combined.paste(right_img, (lw, 0))

        if not self._zoom_initialized:
            self.zoom_level = min(viewer_w / combined_w, viewer_h / max_h)
            self._zoom_initialized = True
        final_w = max(1, int(combined_w * self.zoom_level))
        final_h = max(1, int(max_h     * self.zoom_level))

        # Pixmap stocké à la résolution combinée native, étiré par Qt à l'affichage.
        if has_alpha:
            combined = _compose_on_checkerboard(combined)
        pixmap = _pil_to_qpixmap(combined)

        cw = self._canvas.width()
        ch = self._canvas.height()
        if cw <= 1: cw = viewer_w
        if ch <= 1: ch = viewer_h
        offset_x = (cw - final_w) // 2 + self._canvas.pan_offset_x
        offset_y = (ch - final_h) // 2 + self._canvas.pan_offset_y

        self._canvas.set_pixmap_and_geometry(pixmap, offset_x, offset_y, final_w, final_h)

        # Nom
        img_indices = self.get_image_indices()
        left_entry  = state.images_data[left_idx]
        right_entry = state.images_data[right_idx] if right_idx else None
        lpos = img_indices.index(left_idx) + 1
        rpos = img_indices.index(right_idx) + 1 if right_idx and right_idx in img_indices else None
        total = len(img_indices)
        if rpos:
            self._name_label.setText(
                f"{left_entry['orig_name']} | {right_entry['orig_name']} ({lpos}-{rpos}/{total})"
            )
        else:
            self._name_label.setText(f"{left_entry['orig_name']} ({lpos}/{total})")

        self._schedule_name_hide()

    # ── Nom en plein écran ────────────────────────────────────────────────────

    def _schedule_name_hide(self):
        self._name_hide_timer.stop()
        if self.is_fullscreen:
            self._name_label.show()
            self._name_hide_timer.start(2000)

    def _hide_name_label(self):
        if self.is_fullscreen:
            self._name_label.hide()


# ─────────────────────────────────────────────────────────────────────────────
# Fonction publique d'ouverture
# ─────────────────────────────────────────────────────────────────────────────

def open_image_viewer(parent, idx: int, callbacks: dict, initial_tool: str | None = None):
    """Ouvre la visionneuse sur l'image d'index idx.

    initial_tool : "crop" ou "straighten" pour présélectionner un outil de la
    barre d'outils flottante à l'ouverture (ex. commande "Recadrer" du menu
    contextuel/barre de menus/colonne d'icônes) ; None (défaut) = aucun outil
    présélectionné."""
    state = (callbacks or {}).get('state') or _state_module.state
    if not state.images_data[idx]["is_image"] or state.images_data[idx].get("is_corrupted"):
        return
    viewer = ImageViewer(parent, idx, callbacks=callbacks, initial_tool=initial_tool)
    viewer.show()


def update_image_viewer_if_open():
    """Met à jour le titre des visionneuses ouvertes si elles existent."""
    for viewer in image_viewer_refs[:]:
        try:
            if viewer and viewer.isVisible():
                viewer.setWindowTitle(_wt("viewer.title"))
                viewer._retranslate()
        except Exception:
            if viewer in image_viewer_refs:
                image_viewer_refs.remove(viewer)


def refresh_image_viewers_after_external_undo_redo(state):
    """Rafraîchit l'affichage de toute visionneuse ouverte sur `state` après un
    undo/redo déclenché HORS de la visionneuse (icône Undo/Redo de la colonne
    verticale, barre de menus, Ctrl+Z/Ctrl+Y du panneau) — un seul historique
    partagé (state.history, undo/redo unifié, voir idees.txt #3), mais sans cet
    appel la visionneuse continue d'afficher l'ancienne image : son propre
    ImageViewer._undo_and_refresh()/_refresh_after_undo_redo() n'est jamais
    déclenché puisque le clic n'a pas eu lieu sur SA propre icône Undo/Redo."""
    for viewer in image_viewer_refs[:]:
        try:
            if viewer and viewer.isVisible() and viewer.callbacks.get('state') is state:
                viewer._refresh_after_undo_redo()
        except Exception:
            if viewer in image_viewer_refs:
                image_viewer_refs.remove(viewer)
