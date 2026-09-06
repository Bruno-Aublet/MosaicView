"""
modules/qt/viewer_toolbar_qt.py — Barre d'outils flottante de la visionneuse
principale (image_viewer_qt.py).

Fusion progressive des visionneuses : ce module contient le
composant transversal qui orchestre la sélection d'outil (crop/straighten/
clone) et l'undo/redo unifié — pas la logique propre à un outil donné, qui
vit dans son propre module (crop_tool_qt.py, straighten_tool_qt.py,
clone_tool_qt.py). image_viewer_qt.py instancie _ViewerToolbar et lui délègue
la sélection d'outil / le survol / l'auto-masquage — voir CLAUDE.md règle
"ne jamais migrer le code d'un outil dans image_viewer_qt.py" (cette barre
n'est pas un outil, mais suit le même principe de séparation).

Classes :
  _ToolButton    — icône cliquable d'un outil (état actif/inactif)
  _ActionButton  — icône cliquable d'action instantanée (undo/redo)
  _ViewerToolbar — la barre elle-même, widget flottant en surimpression du
                   canvas, jamais inséré dans le layout de ImageViewer.

Recolorisation mode sombre : BTN_Sharpness.png/BTN_Unsharp.png sont des
icônes en noir plein sans contour ni couleur (contrairement aux autres
icônes de cette barre, illustrées en couleur avec contour foncé), quasi
invisibles sur le fond sombre de la barre en mode sombre.
_recolor_for_dark(pil_img) les repasse en blanc (theme['text'] du thème
sombre) en conservant le canal alpha, appliqué uniquement à ces deux
fichiers et uniquement quand state.dark_mode est actif (voir _ToolButton.
_load_icon). Pas généralisé aux autres icônes de la barre : elles ont déjà un
contraste suffisant par leur propre couleur/contour.
"""

from PIL import Image, ImageEnhance

from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QFrame, QLabel
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QColor

from modules.qt import state as _state_module
from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.overlay_tooltip_qt import OverlayTooltip
from modules.qt.crop_tool_qt import _CropMaskPanel
from modules.qt.straighten_tool_qt import _StraightenAnglePanel
from modules.qt.rotation_tool_qt import _RotationOptionsPanel
from modules.qt.clone_tool_qt import _CloneOptionsPanel
from modules.qt.blur_tool_qt import _BlurOptionsPanel
from modules.qt.text_tool_qt import _TextOptionsPanel
from modules.qt.sharpness_tool_qt import _SharpnessOptionsPanel, _UnsharpOptionsPanel
from modules.qt.brightness_tool_qt import _BrightnessOptionsPanel
from modules.qt.saturation_tool_qt import _SaturationOptionsPanel
from modules.qt.remove_colors_tool_qt import _RemoveColorsOptionsPanel
from modules.qt.compression_tool_qt import _CompressionOptionsPanel, is_compressible_entry
from modules.qt.levels_tool_qt import _LevelsOptionsPanel
from modules.qt.shapes_tool_qt import _ShapeOptionsPanel
from modules.qt.clipboard_qt import clipboard_has_single_image
from modules.qt.transparency_tool_qt import _TransparencyOptionsPanel
from modules.qt.color_depth_tool_qt import _ColorDepthOptionsPanel
from modules.qt.effects_tool_qt import _EffectsOptionsPanel
from modules.qt.image_mode_tool_qt import _ImageModeOptionsPanel


# ─────────────────────────────────────────────────────────────────────────────
# Icônes
# ─────────────────────────────────────────────────────────────────────────────

# Icônes recolorisées en mode sombre (icônes noir plein sans contour/couleur
# propre, voir docstring de module) — clé = nom de fichier, valeur = couleur
# RGB de remplacement.
_DARK_MODE_RECOLOR_ICONS = {"BTN_Sharpness.png", "BTN_Unsharp.png"}


def _recolor_for_dark(pil_img: Image.Image, rgb_hex: str) -> Image.Image:
    """Remplace la couleur des pixels opaques par rgb_hex, canal alpha
    conservé tel quel — même principe que icon_toolbar_qt.py::_to_grayscale
    (recolorisation par canal, pas une teinte appliquée par-dessus)."""
    r, g, b, a = pil_img.convert("RGBA").split()
    color = QColor(rgb_hex)
    solid = Image.new("RGBA", pil_img.size, (color.red(), color.green(), color.blue(), 255))
    return Image.merge("RGBA", (*solid.split()[:3], a))


def _brighten_for_dark(pil_img: Image.Image, factor: float = 1.6) -> Image.Image:
    """Éclaircit les canaux RGB (ImageEnhance.Brightness, même mécanisme que
    icon_toolbar_qt.py::_to_grayscale) SANS désaturer ni remplacer la teinte —
    contrairement à _recolor_for_dark (icônes noir plein, une seule couleur de
    remplacement adaptée), utilisé pour des icônes en deux tons d'une même
    teinte (ex. BTN_Rotate_Left/Right.png, violet foncé sur fond sombre, peu
    lisibles en mode sombre) où un simple remplacement uni aplatirait le
    dessin. Canal alpha conservé tel quel."""
    r, g, b, a = pil_img.convert("RGBA").split()
    rgb = Image.merge("RGB", (r, g, b))
    brightened = ImageEnhance.Brightness(rgb).enhance(factor)
    return Image.merge("RGBA", (*brightened.split(), a))


class _ToolButton(QLabel):
    """Icône cliquable de la barre d'outils : survol en surbrillance, état actif
    en surbrillance permanente (distincte du survol), tooltip via OverlayTooltip.

    Grisage optionnel (outil "compression") : contrairement aux outils dont
    la disponibilité ne dépend pas de la page affichée, la compression n'a de
    sens que sur une page JPEG/WEBP/AVIF — voir compression_tool_qt.py.
    set_enabled_state(False) grise
    l'icône (même algorithme PIL que _ActionButton, réutilisé tel quel) et la
    rend non cliquable ; le tooltip reste actif dans les deux états (texte
    différent selon activé/désactivé, géré par l'appelant)."""

    def __init__(self, toolbar: "_ViewerToolbar", icon_filename: str, tool_id: str = ""):
        super().__init__()
        self._toolbar = toolbar
        self._icon_filename = icon_filename
        self._tool_id = tool_id
        self._active = False
        self._enabled = True
        self.setFixedSize(36, 36)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self._load_icon()

    def _load_icon(self):
        from modules.qt.font_loader import resource_path
        from modules.qt.icon_toolbar_qt import _to_grayscale
        state = _state_module.state
        if self._icon_filename in _DARK_MODE_RECOLOR_ICONS and state.dark_mode:
            # Icône noir plein sans contour/couleur propre, invisible sur le
            # fond sombre de la barre — recolorisée en blanc (theme['text']
            # du thème sombre), voir docstring de module. Import différé de
            # _pil_to_qpixmap (image_viewer_qt.py) pour éviter le cycle
            # d'import déjà documenté sur _ActionButton._load_icon.
            from modules.qt.image_viewer_qt import _pil_to_qpixmap
            path = resource_path(f'icons/{self._icon_filename}')
            pil_img = Image.open(path).convert("RGBA")
            theme = get_current_theme()
            recolored = _recolor_for_dark(pil_img, theme['text'])
            pm = _pil_to_qpixmap(recolored)
        elif not self._enabled:
            # Même algorithme de grisage que _ActionButton (icon_toolbar_qt.
            # _to_grayscale), réutilisé tel quel plutôt qu'une simple opacité
            # Qt — voir docstring de classe.
            from modules.qt.image_viewer_qt import _pil_to_qpixmap
            path = resource_path(f'icons/{self._icon_filename}')
            pil_img = Image.open(path).convert("RGBA")
            pm = _pil_to_qpixmap(_to_grayscale(pil_img))
        else:
            path = resource_path(f'icons/{self._icon_filename}')
            pm = QPixmap(path)
        if not pm.isNull():
            pm = pm.scaled(22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.setPixmap(pm)

    def set_icon_filename(self, icon_filename: str):
        """Change l'icône affichée sans changer tool_id — utilisé par l'icône
        bi-mode sharpness/unsharp : contrairement à straighten (icône
        visuellement fixe, seul le comportement du clic gauche change selon
        le mode), ici l'icône elle-même change (BTN_Sharpness.png /
        BTN_Unsharp.png)."""
        if self._icon_filename != icon_filename:
            self._icon_filename = icon_filename
            self._load_icon()

    def set_active(self, active: bool):
        if self._active != active:
            self._active = active
            self._apply_style()

    def set_enabled_state(self, enabled: bool):
        """Grise/dégrise l'icône (voir docstring de classe). Désélectionne
        l'outil au passage si on le désactive alors qu'il était actif (ex.
        changement de page vers un format non compressible pendant que
        l'outil compression est sélectionné) — évite un outil actif mais
        non cliquable, incohérent visuellement."""
        if self._enabled == enabled:
            return
        self._enabled = enabled
        if not enabled and self._active:
            self._toolbar.set_active_tool(None)
        self._load_icon()
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
        self._apply_style()

    def _apply_style(self):
        theme = get_current_theme()
        if self._active:
            self.setStyleSheet(
                f"background: {theme['icon_hover']}; border: 2px solid {theme['text']}; "
                f"border-radius: 4px;"
            )
        else:
            self.setStyleSheet("background: transparent; border: 2px solid transparent; border-radius: 4px;")

    def enterEvent(self, event):
        if self._enabled and not self._active:
            theme = get_current_theme()
            self.setStyleSheet(
                f"background: {theme['icon_hover']}; border: 2px solid transparent; border-radius: 4px;"
            )

    def leaveEvent(self, event):
        self._apply_style()

    def mousePressEvent(self, event):
        if not self._enabled:
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            self._toolbar._on_tool_clicked(self)
        elif event.button() == Qt.RightButton:
            self._toolbar._on_tool_right_clicked(self)
        event.accept()

    def mouseReleaseEvent(self, event):
        # Sans ceci, le relâchement du clic droit (bouton/gauche comme droit)
        # "fuit" vers _ViewerCanvas en dessous (widget flottant enfant du
        # canvas) et déclenche son propre mouseReleaseEvent — pour le clic
        # droit, ça ouvrait le menu contextuel de la visionneuse en plus du
        # changement de mode manuel/auto voulu par le clic sur l'icône.
        event.accept()


class _ActionButton(QLabel):
    """Icône cliquable d'action instantanée (undo/redo) : pas de notion d'outil
    actif/inactif, seulement activée/désactivée (grisée, façon icon_toolbar_qt)
    selon la disponibilité de l'action au moment présent."""

    def __init__(self, icon_filename: str, on_click):
        super().__init__()
        self._icon_filename = icon_filename
        self._on_click = on_click
        self._enabled = True
        self._pm_normal: QPixmap | None = None
        self._pm_gray: QPixmap | None = None
        self.setFixedSize(36, 36)
        self.setAlignment(Qt.AlignCenter)
        self.setMouseTracking(True)
        self._load_icon()
        self._apply_enabled()

    def _load_icon(self):
        from modules.qt.font_loader import resource_path
        from modules.qt.icon_toolbar_qt import _to_grayscale
        # _pil_to_qpixmap importée en différé : évite un cycle d'import avec
        # image_viewer_qt.py, qui importe ce module-ci.
        from modules.qt.image_viewer_qt import _pil_to_qpixmap
        path = resource_path(f'icons/{self._icon_filename}')
        pil_img = Image.open(path).convert("RGBA")
        pil_img.thumbnail((22, 22), Image.LANCZOS)
        self._pm_normal = _pil_to_qpixmap(pil_img)
        self._pm_gray = _pil_to_qpixmap(_to_grayscale(pil_img))
        self.setPixmap(self._pm_normal)

    def set_enabled_state(self, enabled: bool):
        if self._enabled != enabled:
            self._enabled = enabled
            self._apply_enabled()

    def _apply_enabled(self):
        self.setPixmap(self._pm_normal if self._enabled else self._pm_gray)
        self.setCursor(Qt.PointingHandCursor if self._enabled else Qt.ArrowCursor)
        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet("background: transparent; border-radius: 4px;")

    def enterEvent(self, event):
        if self._enabled:
            theme = get_current_theme()
            self.setStyleSheet(f"background: {theme['icon_hover']}; border-radius: 4px;")

    def leaveEvent(self, event):
        self._apply_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._enabled:
            self._on_click()
        event.accept()

    def mouseReleaseEvent(self, event):
        # Voir _ToolButton.mouseReleaseEvent : sans ceci, le relâchement du
        # clic droit fuit vers _ViewerCanvas en dessous et ouvre son menu
        # contextuel — aucune icône de la barre d'outils ne doit déclencher
        # le menu contextuel de la visionneuse, quel que soit le bouton.
        event.accept()


# ─────────────────────────────────────────────────────────────────────────────
# Barre d'outils flottante
# ─────────────────────────────────────────────────────────────────────────────

class _ViewerToolbar(QWidget):
    """Barre d'outils flottante en surimpression du canvas de la visionneuse
    principale, ancrée en haut et centrée horizontalement — jamais insérée dans
    le layout (son apparition/disparition ne redimensionne jamais l'image).

    Auto-masquée après 3 secondes d'inactivité (état "aucun outil" ou outil actif :
    même règle), réapparaît au survol de la zone haute du canvas (voir
    _hover_zone_height). Un seul outil actif à la fois ; re-cliquer sur l'icône
    active désélectionne (retour à "aucun outil")."""

    HOVER_ZONE_RATIO = 0.10
    AUTO_HIDE_MS = 3000

    def __init__(self, viewer: "ImageViewer"):
        super().__init__(viewer._canvas)
        # Voir _StraightenAnglePanel.__init__ : sans cet attribut, un QWidget
        # nu n'applique jamais le "background" d'une stylesheet — cette barre
        # semblait s'en sortir visuellement (icônes contrastées par elles-mêmes)
        # mais reste exposée au même risque, posé ici par cohérence.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._viewer = viewer
        self.active_tool: str | None = None
        # True tant que la souris est dans une zone protégée (barre, panneau
        # d'options, ou la zone haute du canvas, voir _hover_zone_height) —
        # évite de redémarrer le timer à CHAQUE mouvement hors zone : resume_hide() ne doit
        # s'exécuter qu'une seule fois, à la sortie réelle, pas en continu
        # tant qu'on reste hors zone.
        self._in_protected_zone = False

        # Layout à lignes (voir _rewrap_groups) — lignes pré-créées, jamais
        # détruites, seulement masquées quand inutilisées.
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(6, 4, 6, 4)
        self._root_layout.setSpacing(4)
        self._row_spacing = 6

        self._buttons: dict[str, _ToolButton] = {}
        self._groups: list[QFrame] = []
        self._rows: list[QWidget] = []
        self._row_layouts: list[QHBoxLayout] = []

        def _new_group() -> tuple[QFrame, QHBoxLayout]:
            # Nom de groupe = repère interne, jamais affiché dans l'UI.
            frame = QFrame()
            frame_layout = QHBoxLayout(frame)
            frame_layout.setContentsMargins(3, 2, 3, 2)
            frame_layout.setSpacing(2)
            self._groups.append(frame)
            return frame, frame_layout

        def _add_tool(group_layout: QHBoxLayout, icon_filename: str, tool_id: str) -> _ToolButton:
            btn = _ToolButton(self, icon_filename, tool_id=tool_id)
            group_layout.addWidget(btn)
            self._buttons[tool_id] = btn
            return btn

        # Transformation de page.
        self._transform_group, transform_layout = _new_group()
        _add_tool(transform_layout, "BTN_Crop.png", "crop")
        _add_tool(transform_layout, "BTN_Straighten.png", "straighten")
        _add_tool(transform_layout, "BTN_Rotation.png", "rotation")

        # Ajout de contenu.
        self._content_group, content_layout = _new_group()
        _add_tool(content_layout, "BTN_Shapes.png", "shapes")
        _add_tool(content_layout, "BTN_PiP.png", "paste_image")
        _add_tool(content_layout, "BTN_Clone_Zone.png", "clone")
        _add_tool(content_layout, "BTN_Blur.png", "blur")
        _add_tool(content_layout, "BTN_Text.png", "text")

        # Retouche pixel (tonalité/couleur).
        self._pixel_group, pixel_layout = _new_group()
        _add_tool(pixel_layout, "BTN_Brightness.png", "brightness")
        _add_tool(pixel_layout, "BTN_Levels.png", "levels")
        _add_tool(pixel_layout, "BTN_Saturation.png", "saturation")
        _add_tool(pixel_layout, "BTN_Remove_Colors.png", "remove_colors")
        _add_tool(pixel_layout, "BTN_Effects.png", "effects")

        # Netteté / qualité technique.
        self._technical_group, technical_layout = _new_group()
        sharpness_btn = _add_tool(technical_layout, "BTN_Sharpness.png", "sharpness")
        _add_tool(technical_layout, "BTN_Transparency.png", "transparency")
        _add_tool(technical_layout, "BTN_Compression.png", "compression")

        # Structure de l'image.
        self._structure_group, structure_layout = _new_group()
        _add_tool(structure_layout, "BTN_Color_Depth.png", "color_depth")
        _add_tool(structure_layout, "BTN_Image_Mode.png", "image_mode")

        # Macros — groupe déjà cadré avant cette réorganisation.
        self._macro_group, macro_group_layout = _new_group()
        self._macro_record_btn = _ActionButton(
            "BTN_Macro_Record.png", self._on_macro_record_clicked)
        macro_group_layout.addWidget(self._macro_record_btn)
        self._macro_play_btn = _ActionButton(
            "BTN_Macro_Play.png", self._on_macro_play_clicked)
        macro_group_layout.addWidget(self._macro_play_btn)

        # Undo/redo — cadré désormais comme les autres groupes, le séparateur
        # QFrame.VLine précédent n'a plus lieu d'être.
        self._undo_redo_group, undo_redo_layout = _new_group()
        self._undo_btn = _ActionButton("BTN_Batch_Undo.png", self._on_undo_clicked)
        undo_redo_layout.addWidget(self._undo_btn)
        self._redo_btn = _ActionButton("BTN_Batch_Redo.png", self._on_redo_clicked)
        undo_redo_layout.addWidget(self._redo_btn)

        # Lignes pré-créées (voir _rewrap_groups), jamais détruites, montrées/
        # masquées selon le nombre réellement utilisé. Au plus un groupe par
        # ligne dans le pire des cas, donc autant de lignes que de groupes.
        for _i in range(len(self._groups)):
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(self._row_spacing)
            row_layout.addStretch(1)
            row_layout.addStretch(1)
            self._root_layout.addWidget(row_widget)
            row_widget.hide()
            self._rows.append(row_widget)
            self._row_layouts.append(row_layout)

        self._overlay_tip = OverlayTooltip(self.window())
        for _tid in (
            "crop", "straighten", "rotation", "shapes", "paste_image", "clone",
            "blur", "text", "brightness", "levels", "saturation", "remove_colors",
            "effects", "sharpness", "transparency", "compression",
            "color_depth", "image_mode",
        ):
            self._overlay_tip.track(self._buttons[_tid])
        self._overlay_tip.track(self._macro_record_btn)
        self._overlay_tip.track(self._macro_play_btn)
        self._overlay_tip.track(self._undo_btn)
        self._overlay_tip.track(self._redo_btn)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._on_hide_timeout)

        # Panneau flottant du mode "masque de découpe" (outil crop uniquement,
        # affiché seulement quand state.crop_mode == 1 — voir _CropMaskPanel).
        self._crop_mask_panel = _CropMaskPanel(viewer)

        self._angle_panel = _StraightenAnglePanel(viewer)
        self._rotation_panel = _RotationOptionsPanel(viewer)
        self._clone_panel = _CloneOptionsPanel(viewer)
        self._blur_panel = _BlurOptionsPanel(viewer)
        self._text_panel = _TextOptionsPanel(viewer)
        self._sharpness_panel = _SharpnessOptionsPanel(viewer)

        # Jamais visible en même temps que _sharpness_panel (set_visible_
        # for_tool de chacun se base sur state.sharpness_mode).
        self._unsharp_panel = _UnsharpOptionsPanel(viewer)

        self._brightness_panel = _BrightnessOptionsPanel(viewer)
        self._saturation_panel = _SaturationOptionsPanel(viewer)
        self._remove_colors_panel = _RemoveColorsOptionsPanel(viewer)
        self._compression_panel = _CompressionOptionsPanel(viewer)
        self._levels_panel = _LevelsOptionsPanel(viewer)
        self._shapes_panel = _ShapeOptionsPanel(viewer)
        self._transparency_panel = _TransparencyOptionsPanel(viewer)
        self._color_depth_panel = _ColorDepthOptionsPanel(viewer)
        self._effects_panel = _EffectsOptionsPanel(viewer)
        self._image_mode_panel = _ImageModeOptionsPanel(viewer)

        # Icône sharpness/unsharp synchronisée sur le mode persisté dès
        # l'ouverture (state.sharpness_mode restauré par PanelWidget.__init__
        # depuis la config avant la création de la visionneuse).
        if self._sharpness_mode() == 1:
            sharpness_btn.set_icon_filename("BTN_Unsharp.png")

        self._apply_theme()
        # Agencement calculé dès la construction (largeur de la fenêtre à
        # l'ouverture), pas seulement au premier resize.
        self.update_layout_for_width(viewer._canvas.width())
        self.hide()
        self.set_active_tool(None)
        self._update_sharpness_tooltip()
        self.refresh_undo_redo_state()
        self.refresh_macro_buttons_state()

        # Grisage LIVE de l'icône "Coller une image" selon le contenu du
        # presse-papiers système : écoute QClipboard.dataChanged plutôt
        # qu'une simple vérification au clic. Déconnecté dans
        # ImageViewer.closeEvent (voir _disconnect_paste_image_clipboard_watch),
        # même précaution que language_signal.changed (CLAUDE.md règle UI
        # n°2) : cette barre est détruite avec la visionneuse, un signal Qt
        # global (QApplication.clipboard()) qui reste connecté à un widget
        # supprimé provoquerait un RuntimeError au prochain changement de
        # presse-papiers.
        from PySide6.QtWidgets import QApplication
        self._clipboard = QApplication.clipboard()
        self._clipboard.dataChanged.connect(self._refresh_paste_image_button_state)
        self._refresh_paste_image_button_state()

    def _refresh_paste_image_button_state(self):
        """Grise/dégrise l'icône "paste_image" selon clipboard_has_single_image()
        (voir clipboard_qt.py, réutilisée telle quelle plutôt que réécrite) —
        appelée à la construction ET à chaque QClipboard.dataChanged, PAS
        seulement au moment du clic sur l'icône."""
        self._buttons["paste_image"].set_enabled_state(clipboard_has_single_image())
        self._update_paste_image_tooltip()

    def disconnect_paste_image_clipboard_watch(self):
        """Appelée UNIQUEMENT depuis ImageViewer.closeEvent — voir docstring
        du connect() ci-dessus."""
        try:
            self._clipboard.dataChanged.disconnect(self._refresh_paste_image_button_state)
        except (RuntimeError, TypeError):
            pass

    # ── Thème / traduction ───────────────────────────────────────────────────

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"_ViewerToolbar {{ background: {theme['toolbar_bg']}; border-radius: 6px; }}"
        )
        for btn in self._buttons.values():
            btn._apply_style()
            # Recharge l'icône : nécessaire pour que sharpness/unsharp
            # basculent entre noir (clair) et blanc (sombre) recolorisé
            # (_DARK_MODE_RECOLOR_ICONS) si le thème change pendant que la
            # visionneuse reste ouverte — no-op pour les autres icônes.
            btn._load_icon()
        # Même style sobre pour les 7 groupes, sans couleur différenciée.
        group_style = f"QFrame {{ border: 1px solid {theme['separator']}; border-radius: 5px; }}"
        for group in (
            self._transform_group, self._content_group, self._pixel_group,
            self._technical_group, self._structure_group, self._macro_group,
            self._undo_redo_group,
        ):
            group.setStyleSheet(group_style)
        self._macro_record_btn._apply_style()
        self._macro_play_btn._apply_style()
        self._undo_btn._apply_style()
        self._redo_btn._apply_style()
        self._crop_mask_panel._apply_theme()
        self._angle_panel._apply_theme()
        self._rotation_panel._apply_theme()
        self._clone_panel._apply_theme()
        self._blur_panel._apply_theme()
        self._text_panel._apply_theme()
        self._sharpness_panel._apply_theme()
        self._unsharp_panel._apply_theme()
        self._brightness_panel._apply_theme()
        self._saturation_panel._apply_theme()
        self._remove_colors_panel._apply_theme()
        self._compression_panel._apply_theme()
        self._levels_panel._apply_theme()
        self._shapes_panel._apply_theme()
        self._transparency_panel._apply_theme()
        self._color_depth_panel._apply_theme()
        self._effects_panel._apply_theme()
        self._image_mode_panel._apply_theme()
        self._overlay_tip.apply_theme()

    def retranslate(self):
        self._overlay_tip.track(self._buttons["crop"], "")
        self._update_crop_tooltip()
        self._crop_mask_panel.retranslate()
        self._overlay_tip.track(self._buttons["straighten"], "")
        self._update_straighten_tooltip()
        rotation_tip = (
            f"<b>{_('viewer.toolbar_rotation_tooltip')}</b><br>"
            f"{_('viewer.toolbar_rotation_instruction')}"
        )
        self._overlay_tip.track(self._buttons["rotation"], rotation_tip)
        clone_tip = (
            f"<b>{_('viewer.toolbar_clone_tooltip')}</b><br>"
            f"{_('dialogs.clone_zone_viewer.instruction')}"
        )
        self._overlay_tip.track(self._buttons["clone"], clone_tip)
        blur_tip = (
            f"<b>{_('viewer.toolbar_blur_tooltip')}</b><br>"
            f"{_('viewer.toolbar_blur_instruction')}"
        )
        self._overlay_tip.track(self._buttons["blur"], blur_tip)
        text_tip = (
            f"<b>{_('viewer.toolbar_text_tooltip')}</b><br>"
            f"{_('dialogs.text_viewer.instruction')}"
        )
        self._overlay_tip.track(self._buttons["text"], text_tip)
        self._overlay_tip.track(self._buttons["sharpness"], "")
        # Resynchronise l'icône affichée sur state.sharpness_mode : sans ça,
        # un changement de mode fait ailleurs (reset aux valeurs par défaut,
        # ou depuis l'autre panneau en split-view) laisserait l'icône figée
        # sur l'ancien mode dans une visionneuse déjà ouverte, alors que le
        # tooltip (mis à jour juste en dessous) serait lui à jour.
        icon = "BTN_Unsharp.png" if self._sharpness_mode() == 1 else "BTN_Sharpness.png"
        self._buttons["sharpness"].set_icon_filename(icon)
        self._update_sharpness_tooltip()
        brightness_tip = (
            f"<b>{_('viewer.toolbar_brightness_tooltip')}</b><br>"
            f"{_('viewer.toolbar_brightness_instruction')}"
        )
        self._overlay_tip.track(self._buttons["brightness"], brightness_tip)
        saturation_tip = (
            f"<b>{_('viewer.toolbar_saturation_tooltip')}</b><br>"
            f"{_('viewer.toolbar_saturation_instruction')}"
        )
        self._overlay_tip.track(self._buttons["saturation"], saturation_tip)
        remove_colors_tip = (
            f"<b>{_('viewer.toolbar_remove_colors_tooltip')}</b><br>"
            f"{_('viewer.toolbar_remove_colors_instruction')}"
        )
        self._overlay_tip.track(self._buttons["remove_colors"], remove_colors_tip)
        self._update_compression_tooltip()
        levels_tip = (
            f"<b>{_('viewer.toolbar_levels_tooltip')}</b><br>"
            f"{_('viewer.toolbar_levels_instruction')}"
        )
        self._overlay_tip.track(self._buttons["levels"], levels_tip)
        shapes_tip = (
            f"<b>{_('viewer.toolbar_shapes_tooltip')}</b><br>"
            f"{_('viewer.toolbar_shapes_instruction')}"
        )
        self._overlay_tip.track(self._buttons["shapes"], shapes_tip)
        self._update_transparency_tooltip()
        color_depth_tip = (
            f"<b>{_('viewer.toolbar_color_depth_tooltip')}</b><br>"
            f"{_('viewer.toolbar_color_depth_instruction')}"
        )
        self._overlay_tip.track(self._buttons["color_depth"], color_depth_tip)
        effects_tip = (
            f"<b>{_('viewer.toolbar_effects_tooltip')}</b><br>"
            f"{_('viewer.toolbar_effects_instruction')}"
        )
        self._overlay_tip.track(self._buttons["effects"], effects_tip)
        image_mode_tip = (
            f"<b>{_('viewer.toolbar_image_mode_tooltip')}</b><br>"
            f"{_('viewer.toolbar_image_mode_instruction')}"
        )
        self._overlay_tip.track(self._buttons["image_mode"], image_mode_tip)
        self._update_paste_image_tooltip()
        macro_record_tip = (
            f"<b>{_('viewer.toolbar_macro_record_tooltip')}</b><br>"
            f"{_('viewer.toolbar_macro_record_instruction')}"
        )
        self._overlay_tip.track(self._macro_record_btn, macro_record_tip)
        macro_play_tip = (
            f"<b>{_('viewer.toolbar_macro_play_tooltip')}</b><br>"
            f"{_('viewer.toolbar_macro_play_instruction')}"
        )
        self._overlay_tip.track(self._macro_play_btn, macro_play_tip)
        self._overlay_tip.track(self._undo_btn, _("viewer.toolbar_undo_tooltip"))
        self._overlay_tip.track(self._redo_btn, _("viewer.toolbar_redo_tooltip"))
        self._angle_panel.retranslate()
        self._rotation_panel.retranslate()
        self._clone_panel.retranslate()
        self._blur_panel.retranslate()
        self._text_panel.retranslate()
        self._sharpness_panel.retranslate()
        self._unsharp_panel.retranslate()
        self._brightness_panel.retranslate()
        self._saturation_panel.retranslate()
        self._remove_colors_panel.retranslate()
        self._compression_panel.retranslate()
        self._levels_panel.retranslate()
        self._shapes_panel.retranslate()
        self._transparency_panel.retranslate()
        self._color_depth_panel.retranslate()
        self._effects_panel.retranslate()
        self._image_mode_panel.retranslate()

    # ── Macros ────────────────────────────────────────────────────────────────

    def _on_macro_record_clicked(self):
        self._viewer.open_macro_record_dialog()

    def _on_macro_play_clicked(self):
        self._viewer.open_macro_read_dialog()

    def refresh_macro_buttons_state(self):
        """Grisage réciproque : impossible d'enregistrer ou de lire une macro
        dans ce panneau pendant qu'une lecture y est déjà en cours, et
        impossible de lire pendant qu'un enregistrement y est en cours. Rien
        n'empêche l'autre panneau (indépendant) de faire l'un ou l'autre en
        même temps."""
        recording = getattr(self._viewer, '_macro_recording', False)
        reading = getattr(self._viewer, '_macro_reading', False)
        self._macro_record_btn.set_enabled_state(not reading)
        self._macro_play_btn.set_enabled_state(not recording and not reading)

    # ── Undo/Redo ─────────────────────────────────────────────────────────────

    def _on_undo_clicked(self):
        self._viewer._undo_and_refresh()

    def _on_redo_clicked(self):
        self._viewer._redo_and_refresh()

    def refresh_undo_redo_state(self):
        from modules.qt.undo_redo import can_undo, can_redo
        state = self._viewer.callbacks.get('state') or _state_module.state
        self._undo_btn.set_enabled_state(can_undo(state))
        self._redo_btn.set_enabled_state(can_redo(state))

    # ── Sélection d'outil ─────────────────────────────────────────────────────

    def set_active_tool(self, tool_id: str | None):
        previous_tool = self.active_tool
        self.active_tool = tool_id
        for tid, btn in self._buttons.items():
            btn.set_active(tid == tool_id)
        # Le rectangle de crop / le trait de redressage tracés sur la page
        # courante ne sont pas effacés en désélectionnant l'outil (persistance
        # du travail non validé) — seule leur couleur
        # change (rouge actif / gris désélectionné, voir _ViewerCanvas.paintEvent)
        # pour rester visibles sans être trompeurs. Le bouton Valider flottant,
        # lui, ne doit être actionnable que si l'outil correspondant est
        # réellement sélectionné (sinon on pourrait valider un travail "en
        # pause" sans le vouloir).
        canvas = self._viewer._canvas
        # RÈGLE ABSOLUE, SANS AUCUNE EXCEPTION : sélectionner N'IMPORTE QUEL
        # outil de cette barre force IMMÉDIATEMENT le retour en mode simple
        # page, avant tout autre traitement — y compris pour les outils sans
        # problème connu aujourd'hui. Le mode double/continu affiche un
        # pixmap COMBINÉ de 2 pages, et tout outil qui lit une position de
        # clic ou une géométrie tracée risque de la calculer par rapport à
        # cette image combinée puis de l'appliquer (silencieusement, sans
        # erreur visible) à une seule page. La règle s'applique uniformément
        # à tous les outils plutôt que d'auditer au cas par cas lequel est
        # concerné, pour ne pas rater un outil qui deviendrait exposé après
        # une modification ultérieure.
        if tool_id is not None and self._viewer.page_mode != "single":
            self._viewer.page_mode = "single"
            self._viewer.display_image(keep_crop_rect=True)
        # Le bouton "Valider" partagé (crop/straighten/text/shapes) est
        # affiché/masqué tout à la fin de cette méthode, APRÈS que les
        # panneaux d'options des 4 outils (_angle_panel/_text_panel/
        # _shapes_panel — crop n'en a pas) aient été rendus visibles plus bas
        # — _reveal_validate_btn()/_update_validate_btn_state() calculent la
        # position à partir de panel.y()/panel.height(), qui ne sont fiables
        # qu'une fois le panneau effectivement repositionné ; l'appeler ici,
        # avant, utiliserait des dimensions pas encore à jour (bouton
        # invisible/mal placé au premier clic sur l'icône).
        # Panneau du mode masque de découpe : set_visible_for_tool gère
        # lui-même la condition supplémentaire state.crop_mode == 1, pas
        # seulement tool_id == "crop" (contrairement à _angle_panel qui n'a
        # qu'un seul mode straighten manuel à représenter).
        self._crop_mask_panel.set_visible_for_tool(tool_id)
        self._angle_panel.set_visible_for_tool(tool_id)
        self._rotation_panel.set_visible_for_tool(tool_id)
        self._clone_panel.set_visible_for_tool(tool_id)
        # Le clonage n'a pas de "travail en attente de validation" à conserver
        # visuellement en gris (chaque coup de tampon est déjà appliqué à
        # l'image) : la source Ctrl+cliquée est effacée dès qu'on quitte l'outil.
        if previous_tool == "clone" and tool_id != "clone":
            canvas.clear_clone_source()
        # Même principe que le clonage ci-dessus, mais rien à effacer à la
        # désélection : pas de source/marqueur pour cet outil.
        self._blur_panel.set_visible_for_tool(tool_id)
        # Les blocs de texte tracés sur la page courante ne sont pas effacés en
        # désélectionnant l'outil (comme le crop/straighten) — ils sont figés
        # (plus de focus/édition possible) et grisés (décision
        # explicite). Le panneau de formatage n'est visible que si l'outil est
        # actif ET qu'un bloc est actif (set_visible_for_tool gère ce 2e critère).
        canvas._text_set_frozen(tool_id != "text")
        if tool_id != "text":
            canvas._text_active_block_ref = None
        self._text_panel.set_visible_for_tool(tool_id)
        self._sharpness_panel.set_visible_for_tool(tool_id)
        self._unsharp_panel.set_visible_for_tool(tool_id)
        # Pas de bouton "Valider" ni de notion de travail non validé pour la
        # netteté : le relâchement du slider commit déjà tout
        # automatiquement. En quittant l'outil, il ne peut donc rester au pire
        # qu'un preview visuel abandonné en plein drag (avant relâchement) —
        # à annuler proprement, pas un réglage "en attente" à perdre. Couvre
        # les deux modes (sharpness ET unsharp) : les deux partagent le même
        # champ de preview (self._sharpness_preview_img), _reset_sharpness_
        # preview() suffit donc pour l'un comme pour l'autre.
        if previous_tool == "sharpness" and tool_id != "sharpness":
            self._viewer._reset_sharpness_preview()
        self._brightness_panel.set_visible_for_tool(tool_id)
        # Même principe que sharpness ci-dessus : pas de bouton "Valider" ni
        # de notion de travail non validé pour la luminosité/contraste.
        # En quittant l'outil, il ne peut donc rester au pire
        # qu'un preview visuel abandonné en plein drag — à annuler proprement.
        if previous_tool == "brightness" and tool_id != "brightness":
            self._viewer._reset_brightness_preview()
        self._saturation_panel.set_visible_for_tool(tool_id)
        # Même principe que sharpness/brightness ci-dessus : pas de bouton
        # "Valider" ni de notion de travail non validé pour la saturation.
        # En quittant l'outil, il ne peut donc rester au pire
        # qu'un preview visuel abandonné en plein drag — à annuler proprement.
        if previous_tool == "saturation" and tool_id != "saturation":
            self._viewer._reset_saturation_preview()
        self._remove_colors_panel.set_visible_for_tool(tool_id)
        # Même principe que sharpness/brightness/saturation ci-dessus : pas de
        # bouton "Valider" ni de notion de travail non validé pour la
        # suppression des couleurs. En quittant l'outil, il ne
        # peut donc rester au pire qu'un preview visuel abandonné en plein
        # drag — à annuler proprement.
        if previous_tool == "remove_colors" and tool_id != "remove_colors":
            self._viewer._reset_remove_colors_preview()
        self._compression_panel.set_visible_for_tool(tool_id)
        # Même principe que sharpness/brightness/saturation/remove_colors
        # ci-dessus : pas de bouton "Valider" ni de notion de travail non
        # validé pour la compression. En quittant l'outil, il
        # ne peut donc rester au pire qu'un preview visuel abandonné en plein
        # drag — à annuler proprement (resynchronise aussi le slider sur la
        # qualité JPEG réelle courante, voir compression_tool_qt.py).
        if previous_tool == "compression" and tool_id != "compression":
            self._viewer._reset_compression_preview()
        self._levels_panel.set_visible_for_tool(tool_id)
        # Même principe que sharpness/brightness/saturation/remove_colors/
        # compression ci-dessus : pas de bouton "Valider" ni de notion de
        # travail non validé pour les niveaux. En quittant
        # l'outil, il ne peut donc rester au pire qu'un preview visuel
        # abandonné en plein drag — à annuler proprement (désarme aussi une
        # pipette restée armée, voir _LevelsOptionsPanel.set_visible_for_tool
        # qui appelle déjà _deactivate_pipettes()).
        if previous_tool == "levels" and tool_id != "levels":
            self._viewer._reset_levels_preview()
        self._shapes_panel.set_visible_for_tool(tool_id)
        # Comme crop/straighten/texte : les formes déjà posées sur la page ne
        # sont pas effacées en désélectionnant l'outil (persistance du travail
        # non validé), mais deviennent non interactives (plus de sélection/
        # redimensionnement/déplacement possible tant que l'outil n'est pas
        # resélectionné) — un tracé EN COURS (pas encore posé), lui, est
        # annulé : il n'a pas de sens de le laisser "en l'air" une fois
        # l'outil désélectionné.
        if previous_tool == "shapes" and tool_id != "shapes":
            canvas._shape_draw_start = None
            canvas._shape_draw_end = None
            canvas._shape_active = None
            canvas._shape_resize_mode = None
        self._transparency_panel.set_visible_for_tool(tool_id)
        # Comme crop/straighten/texte/formes : l'image de travail accumulée
        # sur la page courante n'est PAS effacée en désélectionnant l'outil
        # (persistance du travail non validé par page) — set_visible_for_tool
        # désarme seulement une pipette restée armée (même principe que
        # _LevelsOptionsPanel).
        self._color_depth_panel.set_visible_for_tool(tool_id)
        if tool_id == "color_depth":
            # Resynchronise le panneau sur l'état RÉEL de la page courante à
            # chaque sélection de l'outil : le snapshot/verrouillage survit au
            # changement d'outil (pas de reset ici, contrairement aux
            # modes preview-slider), seul l'affichage doit être à jour.
            self._viewer._sync_color_depth_panel()
        self._effects_panel.set_visible_for_tool(tool_id)
        if tool_id == "effects":
            # Même principe que color_depth ci-dessus : le snapshot/état
            # mémorisé (state.effect_original_bytes_by_page/effect_key_by_page)
            # survit au changement d'outil, seul l'affichage doit être à jour.
            self._viewer._sync_effects_panel()
        self._image_mode_panel.set_visible_for_tool(tool_id)
        if tool_id == "image_mode":
            # Même principe que color_depth ci-dessus : le snapshot/
            # verrouillage survit au changement d'outil, seul l'affichage
            # doit être à jour.
            self._viewer._sync_image_mode_panel()
        # Bouton "Valider" partagé (crop/straighten/text/shapes/transparency) : affiché
        # (vert/actif ou gris/inactif selon _validate_tool_has_work) pour ces
        # outils, masqué pour tout autre — appelé ICI, tout à la fin, une
        # fois que TOUS les panneaux d'options pertinents (_angle_panel/
        # _text_panel/_shapes_panel) ont été rendus visibles juste au-dessus
        # (voir commentaire au point d'appel précédent pour la raison de cet ordre).
        # set_active_tool() n'est déclenchée que par un clic RÉEL sur une
        # icône (_on_tool_clicked) — la barre est donc nécessairement déjà
        # visible à cet instant (on ne peut pas cliquer une icône masquée) :
        # _reveal_validate_btn()/_conceal_validate_btn() sont donc légitimes
        # ici, contrairement à un simple rafraîchissement passif (display_image,
        # tracé en cours), qui utilise _update_validate_btn_state() sans
        # jamais changer la visibilité — voir sa docstring pour la règle
        # complète du mécanisme unique.
        if tool_id in canvas._ALWAYS_VISIBLE_VALIDATE_TOOLS:
            canvas._reveal_validate_btn()
            # Bouton "Annuler" jumeau, même condition/mêmes points d'appel
            # que le bouton "Valider" — voir _reveal_cancel_btn/_conceal_cancel_btn.
            canvas._reveal_cancel_btn()
        else:
            canvas._conceal_validate_btn()
            canvas._conceal_cancel_btn()
        canvas.update()

    def _on_tool_clicked(self, btn: "_ToolButton"):
        tool_id = btn._tool_id
        if not tool_id:
            return
        # Redressement en mode automatique (state.straighten_mode == 1, bascule
        # par clic droit — voir _on_tool_right_clicked) : le clic gauche ne
        # sélectionne pas un outil de tracé (rien à tracer en auto), il lance
        # directement le deskew sur la page actuellement affichée. L'icône
        # doit malgré tout devenir l'outil actif (comme pour le clic droit,
        # voir _on_tool_right_clicked) — sinon l'outil précédemment
        # sélectionné reste visuellement actif alors que straighten est
        # celui qui vient d'agir.
        if tool_id == "straighten" and self._straighten_mode() == 1:
            self.set_active_tool("straighten")
            self._viewer.perform_auto_straighten()
            return
        # "Coller une image" : voir paste_image_from_clipboard()
        # ci-dessous — factorisée pour être réutilisée à l'identique par le
        # raccourci Ctrl+V dédié de la visionneuse (voir ImageViewer.
        # _paste_image_shortcut, image_viewer_qt.py).
        if tool_id == "paste_image":
            self.paste_image_from_clipboard()
            return
        if self.active_tool == tool_id:
            self.set_active_tool(None)
        else:
            self.set_active_tool(tool_id)

    def paste_image_from_clipboard(self):
        """Colle immédiatement une NOUVELLE image du presse-papiers sur la
        page affichée et active l'outil "paste_image".
        Contrairement aux autres outils (sélectionner l'icône n'active que le
        mode de tracé), CHAQUE appel colle une image — plusieurs collages
        doivent pouvoir s'accumuler avant validation (comme les formes), donc
        rappeler cette méthode alors que l'outil est déjà actif ne le
        désélectionne JAMAIS (contrairement au comportement standard de
        _on_tool_clicked pour les autres icônes) : ce serait incohérent avec
        le fait de vouloir coller une 2e image sans quitter puis rerentrer
        dans l'outil. No-op silencieux si le presse-papiers ne contient pas
        EXACTEMENT une image (voir clipboard_qt.py::clipboard_has_single_image/
        get_clipboard_single_image, réutilisées telles quelles) — appelée
        aussi bien par le clic sur l'icône (déjà grisée dans ce cas, voir
        _ToolButton.mousePressEvent) que par le raccourci Ctrl+V dédié de la
        visionneuse (ImageViewer._paste_image_shortcut, sans garde
        équivalente à l'icône, donc le no-op silencieux est nécessaire ici)."""
        from modules.qt.clipboard_qt import get_clipboard_single_image
        img = get_clipboard_single_image()
        if img is None:
            return
        if self.active_tool != "paste_image":
            self.set_active_tool("paste_image")
        self._viewer._canvas._add_pasted_image(img)

    def _on_tool_right_clicked(self, btn: "_ToolButton"):
        """Un clic droit sur N'IMPORTE QUELLE icône de la barre la sélectionne
        (règle générale, sans exception) — d'abord la bascule de mode propre
        aux 3 icônes bi-mode (crop/straighten/sharpness) si applicable, puis
        set_active_tool(tool_id) dans tous les cas, y compris pour les icônes
        sans bi-mode. Pour straighten en mode auto, la sélection a lieu mais
        ne déclenche PAS le deskew automatique (contrairement au clic gauche,
        voir _on_tool_clicked) — le clic droit ne fait jamais que basculer le
        mode et sélectionner l'icône, jamais d'action supplémentaire."""
        if btn._tool_id == "crop":
            self._toggle_crop_mode()
        elif btn._tool_id == "straighten":
            self._toggle_straighten_mode()
        elif btn._tool_id == "sharpness":
            self._toggle_sharpness_mode()
        self.set_active_tool(btn._tool_id)

    def _toggle_crop_mode(self):
        """Bascule normal/masque de découpe — même mécanisme
        que _toggle_straighten_mode, à deux différences près : (1) le
        rectangle actuellement tracé/édité sur la page courante est CONSERVÉ
        tel quel et devient le rectangle du nouveau mode (décision explicite,
        pas d'effacement contrairement à la bascule straighten manuel→auto) ;
        (2) AUCUNE persistance sur disque (décision explicite : le mode
        masque ne survit pas à la fermeture de la visionneuse/de l'appli,
        contrairement à state.straighten_mode qui est sauvegardé en config
        via _renumber_config().set_straighten_mode — pas d'équivalent
        "set_crop_mode" à appeler ici, state.crop_mode reste une variable en
        mémoire simple, réinitialisée à 0 à chaque relance de l'appli). La
        sélection de l'outil (panneau/boutons Valider-Annuler rafraîchis) est
        gérée par _on_tool_right_clicked, qui appelle set_active_tool("crop")
        après cette bascule — pas de duplication ici."""
        from modules.qt import state as _state_module
        state = self._viewer.callbacks.get('state') or _state_module.state
        new_mode = 1 - self._crop_mode()
        state.crop_mode = new_mode
        self._update_crop_tooltip()

    def _crop_mode(self) -> int:
        from modules.qt import state as _state_module
        state = self._viewer.callbacks.get('state') or _state_module.state
        return getattr(state, "crop_mode", 0)

    def _update_crop_tooltip(self):
        import html as _html
        mode = self._crop_mode()
        text = _(f"tooltip.crop_mode_{mode}")
        tip = (
            f"<b>{_html.escape(_('viewer.toolbar_crop_tooltip'))}</b><br>"
            f"{_html.escape(text).replace(chr(10), '<br>')}"
        )
        self._overlay_tip.set_tracked_html(tip, self._buttons["crop"])
        self._overlay_tip.force_refresh_visible(self._buttons["crop"])

    def _toggle_straighten_mode(self):
        from modules.qt import state as _state_module
        state = self._viewer.callbacks.get('state') or _state_module.state
        new_mode = 1 - self._straighten_mode()
        state.straighten_mode = new_mode
        set_mode_cb = self._viewer.callbacks.get("set_straighten_mode")
        if set_mode_cb:
            set_mode_cb(new_mode)
        # Si le mode auto vient d'être activé et qu'un trait manuel était en
        # cours sur la page courante, on l'efface : les deux modes ne
        # cohabitent pas, un trait resté affiché en mode auto serait trompeur.
        # L'outil straighten reste sélectionné (voir _on_tool_right_clicked,
        # qui sélectionne systématiquement l'icône cliquée après la bascule
        # de mode, y compris en mode auto où il n'y a rien à tracer).
        if new_mode == 1:
            self._viewer._canvas.clear_line()
            self._viewer._straighten_by_page.pop(self._viewer.current_idx, None)
            self._angle_panel.reset()
        self._update_straighten_tooltip()

    def _straighten_mode(self) -> int:
        from modules.qt import state as _state_module
        state = self._viewer.callbacks.get('state') or _state_module.state
        return getattr(state, "straighten_mode", 0)

    def _update_straighten_tooltip(self):
        import html as _html
        mode = self._straighten_mode()
        text = _(f"tooltip.straighten_mode_{mode}")
        tip = (
            f"<b>{_html.escape(_('viewer.toolbar_straighten_tooltip'))}</b><br>"
            f"{_html.escape(text).replace(chr(10), '<br>')}"
        )
        self._overlay_tip.set_tracked_html(tip, self._buttons["straighten"])
        # Réaffiche immédiatement si le tooltip était déjà visible (clic droit
        # sans mouvement de souris ensuite) — sinon l'ancien texte reste
        # affiché jusqu'au prochain MouseMove.
        self._overlay_tip.force_refresh_visible(self._buttons["straighten"])

    def _toggle_sharpness_mode(self):
        from modules.qt import state as _state_module
        state = self._viewer.callbacks.get('state') or _state_module.state
        new_mode = 1 - self._sharpness_mode()
        state.sharpness_mode = new_mode
        set_mode_cb = self._viewer.callbacks.get("set_sharpness_mode")
        if set_mode_cb:
            set_mode_cb(new_mode)
        # Icône elle-même changée (contrairement à straighten) — voir
        # _ToolButton.set_icon_filename.
        icon = "BTN_Unsharp.png" if new_mode == 1 else "BTN_Sharpness.png"
        self._buttons["sharpness"].set_icon_filename(icon)
        # Les deux modes sont maintenant tous deux implémentés (sharpness ET
        # unsharp) : si l'outil sharpness était actif, on ne le désélectionne
        # plus — seul le panneau visible change (chaque set_visible_for_tool
        # checke déjà state.sharpness_mode). Un preview en cours (drag non
        # relâché) dans l'ancien mode n'a plus de sens dans le nouveau, donc
        # on l'efface au passage (_reset_sharpness_preview resynchronise
        # aussi les deux panneaux sur leur dernière valeur commitée).
        if self.active_tool == "sharpness":
            self._viewer._reset_sharpness_preview()
            self._sharpness_panel.set_visible_for_tool("sharpness")
            self._unsharp_panel.set_visible_for_tool("sharpness")
        self._update_sharpness_tooltip()

    def _sharpness_mode(self) -> int:
        from modules.qt import state as _state_module
        state = self._viewer.callbacks.get('state') or _state_module.state
        return getattr(state, "sharpness_mode", 0)

    def _update_sharpness_tooltip(self):
        import html as _html
        mode = self._sharpness_mode()
        # Contrairement à straighten (titre générique fixe quel que soit le
        # mode), le titre change lui aussi ici : "Netteté" en mode sharpness,
        # "Netteté adaptative" en mode unsharp — cohérent avec le fait que
        # l'icône elle-même change aussi (voir set_icon_filename).
        title_key = "viewer.toolbar_unsharp_tooltip" if mode == 1 else "viewer.toolbar_sharpness_tooltip"
        text = _(f"tooltip.sharpness_mode_{mode}")
        tip = (
            f"<b>{_html.escape(_(title_key))}</b><br>"
            f"{_html.escape(text).replace(chr(10), '<br>')}"
        )
        self._overlay_tip.set_tracked_html(tip, self._buttons["sharpness"])
        # Réaffiche immédiatement si le tooltip était déjà visible (clic droit
        # sans mouvement de souris ensuite) — sinon l'ancien texte reste
        # affiché jusqu'au prochain MouseMove.
        self._overlay_tip.force_refresh_visible(self._buttons["sharpness"])

    def _update_compression_tooltip(self):
        """Tooltip à deux états (outil "compression") : texte
        différent selon que l'icône est actuellement activée (page
        JPEG/WEBP/AVIF) ou grisée (tout autre format) — contrairement aux
        autres outils de la barre, toujours disponibles. Rappelé par
        ImageViewer._refresh_compression_button_state() à chaque changement
        de page, pas seulement à la construction/au changement de langue."""
        btn = self._buttons["compression"]
        if btn._enabled:
            tip = (
                f"<b>{_('viewer.toolbar_compression_tooltip')}</b><br>"
                f"{_('viewer.toolbar_compression_instruction')}"
            )
        else:
            tip = (
                f"<b>{_('viewer.toolbar_compression_tooltip')}</b><br>"
                f"{_('viewer.toolbar_compression_disabled')}"
            )
        self._overlay_tip.set_tracked_html(tip, btn)

    def _update_paste_image_tooltip(self):
        """Tooltip à deux états, même principe que
        _update_compression_tooltip/_update_transparency_tooltip : texte
        différent selon que l'icône est actuellement activée (le
        presse-papiers contient une image seule) ou grisée (tout autre
        contenu). Rappelé par _refresh_paste_image_button_state() à chaque
        changement du presse-papiers (QClipboard.dataChanged), pas seulement
        à la construction/au changement de langue comme les autres tooltips
        statiques."""
        btn = self._buttons["paste_image"]
        if btn._enabled:
            tip = (
                f"<b>{_('viewer.toolbar_paste_image_tooltip')}</b><br>"
                f"{_('viewer.toolbar_paste_image_instruction')}"
            )
        else:
            tip = (
                f"<b>{_('viewer.toolbar_paste_image_tooltip')}</b><br>"
                f"{_('viewer.toolbar_paste_image_disabled')}"
            )
        self._overlay_tip.set_tracked_html(tip, btn)

    def _update_transparency_tooltip(self):
        """Tooltip à deux états, même principe que
        _update_compression_tooltip (seul autre outil grisable de cette
        barre) : texte différent selon que l'icône est actuellement
        activée (page PNG/WEBP/ICO/AVIF) ou grisée (tout autre format).
        Rappelé par ImageViewer._refresh_transparency_button_state() à chaque
        changement de page, pas seulement à la construction/au changement de
        langue."""
        btn = self._buttons["transparency"]
        if btn._enabled:
            tip = (
                f"<b>{_('viewer.toolbar_transparency_tooltip')}</b><br>"
                f"{_('viewer.toolbar_transparency_instruction')}"
            )
        else:
            tip = (
                f"<b>{_('viewer.toolbar_transparency_tooltip')}</b><br>"
                f"{_('viewer.toolbar_transparency_disabled')}"
            )
        self._overlay_tip.set_tracked_html(tip, btn)

    # ── Positionnement / visibilité ──────────────────────────────────────────

    def _plan_rows(self, max_width: int) -> list[list[QFrame]]:
        """Répartit les groupes en lignes de largeur équilibrée (pas juste
        "remplir la ligne au maximum") : détermine le nombre de lignes
        nécessaires par remplissage glouton, puis redécoupe en visant une
        largeur cible égale par ligne — sans jamais couper un groupe."""
        spacing = self._row_spacing
        widths = [g.sizeHint().width() for g in self._groups]

        def row_width(ws: list[int]) -> int:
            return sum(ws) + spacing * (len(ws) - 1) if ws else 0

        n_rows = 1
        acc = 0
        for w in widths:
            new_acc = w if acc == 0 else acc + spacing + w
            if new_acc > max_width and acc > 0:
                n_rows += 1
                acc = w
            else:
                acc = new_acc

        if n_rows <= 1:
            return [list(self._groups)] if self._groups else []

        target = (sum(widths) + spacing * (len(widths) - 1)) / n_rows
        rows: list[list[QFrame]] = []
        remaining_groups = list(self._groups)
        remaining_widths = list(widths)
        row_num = 0
        while remaining_groups:
            # n_rows n'est qu'une ESTIMATION (calcul glouton ci-dessus) pour
            # fixer la cible d'équilibrage — pas une garantie. La dernière
            # ligne doit rester bornée par max_width comme les autres, sinon
            # un groupe de fin de liste plus large que la moyenne peut faire
            # déborder une "dernière ligne fourre-tout" au-delà de la fenêtre.
            rows_left_after = max(n_rows - row_num - 1, 0)
            cur_groups: list[QFrame] = []
            cur_widths: list[int] = []
            i = 0
            while i < len(remaining_widths):
                w = remaining_widths[i]
                if row_width(cur_widths + [w]) > max_width and cur_widths:
                    break
                cur_groups.append(remaining_groups[i])
                cur_widths.append(w)
                i += 1
                if (row_width(cur_widths) >= target and rows_left_after > 0
                        and len(remaining_widths) - i >= rows_left_after):
                    break
            if not cur_groups:
                # Un groupe seul dépasse déjà max_width : il reste seul sur
                # sa ligne et déborde tel quel (cas limite accepté).
                cur_groups = [remaining_groups[0]]
                i = 1
            rows.append(cur_groups)
            remaining_groups = remaining_groups[i:]
            remaining_widths = remaining_widths[i:]
            row_num += 1
        return rows

    def _rewrap_groups(self, max_width: int):
        """Répartit les groupes sur les lignes pré-créées — jamais de groupe
        coupé en deux entre lignes. removeWidget explicite avant setParent :
        sans lui, un groupe reste compté dans son ancienne ligne (sizeHint
        du layout faussé)."""
        for row_layout in self._row_layouts:
            for group in self._groups:
                row_layout.removeWidget(group)
        for group in self._groups:
            group.setParent(None)
        for row_widget in self._rows:
            row_widget.hide()

        if max_width <= 0:
            max_width = 1

        for row_index, row_groups in enumerate(self._plan_rows(max_width)):
            row_layout = self._row_layouts[row_index]
            self._rows[row_index].show()
            for group in row_groups:
                row_layout.insertWidget(row_layout.count() - 1, group)
                group.show()  # setParent(None) plus haut a caché le groupe
        for row_layout in self._row_layouts:
            row_layout.invalidate()
        self._root_layout.invalidate()
        self._root_layout.activate()

    def update_layout_for_width(self, canvas_width: int):
        self._rewrap_groups(canvas_width)
        self.reposition()

    def reposition(self):
        self.adjustSize()
        canvas = self._viewer._canvas
        x = (canvas.width() - self.width()) // 2
        y = 8
        self.move(max(0, x), y)
        self.raise_()

    def show_and_schedule_hide(self):
        self.reposition()
        self.show()
        self.raise_()
        # Bouton play/pause GIF : repositionné sous la barre qui vient de
        # réapparaître, pour ne jamais rester chevauché par elle (voir
        # image_viewer_qt.py::ImageViewer._reposition_gif_play_btn).
        if self._viewer.is_animated_gif:
            self._viewer._reposition_gif_play_btn()
        # Les panneaux d'options flottants (angle straighten, réglages clone,
        # formatage texte, réglette sharpness) réapparaissent avec la barre —
        # même zone de survol plutôt qu'une zone de survol dédiée à chacun.
        # Purement visuel : ne touche
        # à aucun état de réglage en cours (set_visible_for_tool sait déjà
        # si ce panneau doit être visible pour l'outil actif).
        self._crop_mask_panel.set_visible_for_tool(self.active_tool)
        self._angle_panel.set_visible_for_tool(self.active_tool)
        self._rotation_panel.set_visible_for_tool(self.active_tool)
        self._clone_panel.set_visible_for_tool(self.active_tool)
        self._blur_panel.set_visible_for_tool(self.active_tool)
        self._text_panel.set_visible_for_tool(self.active_tool)
        self._sharpness_panel.set_visible_for_tool(self.active_tool)
        self._unsharp_panel.set_visible_for_tool(self.active_tool)
        self._brightness_panel.set_visible_for_tool(self.active_tool)
        self._saturation_panel.set_visible_for_tool(self.active_tool)
        self._remove_colors_panel.set_visible_for_tool(self.active_tool)
        self._compression_panel.set_visible_for_tool(self.active_tool)
        self._levels_panel.set_visible_for_tool(self.active_tool)
        self._shapes_panel.set_visible_for_tool(self.active_tool)
        self._transparency_panel.set_visible_for_tool(self.active_tool)
        self._color_depth_panel.set_visible_for_tool(self.active_tool)
        self._effects_panel.set_visible_for_tool(self.active_tool)
        self._image_mode_panel.set_visible_for_tool(self.active_tool)
        # Le bouton "Valider" partagé (crop/straighten/text/shapes/transparency) réapparaît
        # ICI, et UNIQUEMENT ici (mécanisme unique de réapparition — voir
        # image_viewer_qt.py::_update_validate_btn_state pour la règle
        # complète) : _reveal_validate_btn() recalcule son état ET sa position
        # (sous la barre/son panneau d'options) avant de l'afficher.
        if self.active_tool in self._viewer._canvas._ALWAYS_VISIBLE_VALIDATE_TOOLS:
            self._viewer._canvas._reveal_validate_btn()
            # Bouton "Annuler" jumeau, même condition/mêmes points d'appel.
            self._viewer._canvas._reveal_cancel_btn()
        self._hide_timer.start(self.AUTO_HIDE_MS)

    def _on_hide_timeout(self):
        """Masque la barre ET les panneaux d'options flottants — purement
        visuel : ne touche à aucun état
        de réglage en cours, ils réapparaissent au prochain survol de la
        zone haute (voir show_and_schedule_hide)."""
        self.hide()
        self._crop_mask_panel.hide()
        self._angle_panel.hide()
        self._rotation_panel.hide()
        self._clone_panel.hide()
        self._blur_panel.hide()
        self._text_panel.hide()
        self._sharpness_panel.hide()
        self._unsharp_panel.hide()
        self._brightness_panel.hide()
        self._saturation_panel.hide()
        self._remove_colors_panel.hide()
        self._compression_panel.hide()
        self._levels_panel.hide()
        self._shapes_panel.hide()
        self._transparency_panel.hide()
        self._color_depth_panel.hide()
        self._effects_panel.hide()
        self._image_mode_panel.hide()
        # Bouton play/pause GIF : la barre vient de se masquer, il peut
        # reprendre sa position par défaut dans le coin (voir
        # _reposition_gif_play_btn — retombe sur y=10 quand la barre est
        # invisible).
        if self._viewer.is_animated_gif:
            self._viewer._reposition_gif_play_btn()
        # Le bouton "Valider" partagé disparaît ICI, et UNIQUEMENT ici
        # (mécanisme unique de masquage, symétrique de show_and_schedule_hide
        # — voir image_viewer_qt.py::_update_validate_btn_state pour la règle
        # complète) : sans ce point unique, il pouvait rester affiché à sa
        # dernière position, flottant seul sans la barre au-dessus.
        if self.active_tool in self._viewer._canvas._ALWAYS_VISIBLE_VALIDATE_TOOLS:
            self._viewer._canvas._conceal_validate_btn()
            self._viewer._canvas._conceal_cancel_btn()

    def pause_hide(self):
        """Suspend le décompte du masquage automatique tant que la souris
        reste dans une zone protégée (barre, un panneau d'options, ou la
        zone haute du canvas, voir _hover_zone_height) : le
        timer ne doit PAS continuer à courir/se redémarrer en boucle pendant
        qu'on reste dans ces zones, il doit être complètement arrêté tant
        qu'on y reste, et ne recommencer à décompter qu'à la SORTIE (voir
        resume_hide), pas à chaque micro-mouvement à l'intérieur. Idempotent :
        appelable à chaque mouvement dans la zone sans effet de bord (le
        timer reste simplement arrêté, pas redémarré)."""
        self._in_protected_zone = True
        if self.isVisible():
            self._hide_timer.stop()

    def resume_hide(self):
        """Relance le décompte du masquage automatique — à appeler quand la
        souris quitte une zone protégée (barre, panneau, zone haute du
        canvas). Ne redémarre le timer qu'à la transition réelle
        (_in_protected_zone True → False) : un appel répété pendant que la
        souris reste déjà hors zone ne doit PAS réinitialiser le délai à
        chaque fois, sinon le timer ne finirait jamais son décompte tant que
        la souris bouge n'importe où ailleurs dans le canvas (voir
        on_canvas_mouse_move)."""
        if not self._in_protected_zone:
            return
        self._in_protected_zone = False
        if self.isVisible():
            self._hide_timer.start(self.AUTO_HIDE_MS)

    def note_activity(self):
        """Conservé pour compatibilité — équivalent à pause_hide() suivi
        d'un futur resume_hide() au leaveEvent du widget appelant (voir
        pause_hide/resume_hide)."""
        self.pause_hide()

    def _hover_zone_height(self, canvas_height: int) -> int:
        """S'élargit au-delà de HOVER_ZONE_RATIO (10% du canvas) quand la
        barre grandit sur plusieurs lignes — sinon sa moitié basse resterait
        hors de sa propre zone de déclenchement au survol."""
        base = canvas_height * self.HOVER_ZONE_RATIO
        toolbar_h = self.sizeHint().height()
        return int(max(base, toolbar_h + 8))  # +8 = marge d'ancrage de reposition()

    def on_canvas_mouse_move(self, pos_y: int, canvas_height: int):
        if canvas_height <= 0:
            return
        # Le canvas continue de recevoir des mouseMoveEvent même quand la
        # souris survole un panneau d'options flottant qui lui est superposé
        # (widget enfant du canvas) — sans cette garde, une position Y basse
        # (sous la barre, sur un panneau) déclenchait resume_hide() ici et
        # écrasait le pause_hide() que le panneau venait de poser lui-même
        # via son propre enterEvent, faisant disparaître barre+panneau après
        # 3s même en restant dessus. Laisser le panneau gérer entièrement son
        # propre état pendant qu'il a la souris.
        if (self._crop_mask_panel.underMouse()
                or self._angle_panel.underMouse() or self._rotation_panel.underMouse()
                or self._clone_panel.underMouse() or self._blur_panel.underMouse()
                or self._text_panel.underMouse() or self._sharpness_panel.underMouse()
                or self._unsharp_panel.underMouse() or self._brightness_panel.underMouse()
                or self._saturation_panel.underMouse() or self._remove_colors_panel.underMouse()
                or self._compression_panel.underMouse() or self._levels_panel.underMouse()
                or self._shapes_panel.underMouse() or self._transparency_panel.underMouse()
                or self._color_depth_panel.underMouse() or self._effects_panel.underMouse()
                or self._image_mode_panel.underMouse()):
            return
        in_hover_zone = pos_y <= self._hover_zone_height(canvas_height)
        if in_hover_zone:
            if not self.isVisible():
                self.show_and_schedule_hide()
            self.pause_hide()
        elif self.isVisible():
            self.resume_hide()

    def mousePressEvent(self, event):
        # Une zone vide de la barre (marges entre les groupes) ne doit rien
        # déclencher côté canvas — même raison que _ToolButton/_ActionButton.
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def enterEvent(self, event):
        # La barre elle-même est une zone protégée : le timer doit être
        # suspendu tant que la souris reste dessus, pas seulement redémarré
        # à chaque mouvement.
        self.pause_hide()

    def leaveEvent(self, event):
        self.resume_hide()
