"""
modules/qt/viewer_toolbar_qt.py — Barre d'outils flottante de la visionneuse
principale (image_viewer_qt.py).

Fusion progressive des visionneuses (idees.txt #3) : ce module contient le
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
"""

from PIL import Image

from PySide6.QtWidgets import QWidget, QHBoxLayout, QFrame, QLabel
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap

from modules.qt import state as _state_module
from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.overlay_tooltip_qt import OverlayTooltip
from modules.qt.straighten_tool_qt import _StraightenAnglePanel
from modules.qt.clone_tool_qt import _CloneOptionsPanel
from modules.qt.text_tool_qt import _TextOptionsPanel


# ─────────────────────────────────────────────────────────────────────────────
# Icônes
# ─────────────────────────────────────────────────────────────────────────────

class _ToolButton(QLabel):
    """Icône cliquable de la barre d'outils : survol en surbrillance, état actif
    en surbrillance permanente (distincte du survol), tooltip via OverlayTooltip."""

    def __init__(self, toolbar: "_ViewerToolbar", icon_filename: str, tool_id: str = ""):
        super().__init__()
        self._toolbar = toolbar
        self._icon_filename = icon_filename
        self._tool_id = tool_id
        self._active = False
        self.setFixedSize(36, 36)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self._load_icon()

    def _load_icon(self):
        from modules.qt.font_loader import resource_path
        path = resource_path(f'icons/{self._icon_filename}')
        pm = QPixmap(path)
        if not pm.isNull():
            pm = pm.scaled(22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.setPixmap(pm)

    def set_active(self, active: bool):
        if self._active != active:
            self._active = active
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
        if not self._active:
            theme = get_current_theme()
            self.setStyleSheet(
                f"background: {theme['icon_hover']}; border: 2px solid transparent; border-radius: 4px;"
            )

    def leaveEvent(self, event):
        self._apply_style()

    def mousePressEvent(self, event):
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
    même règle), réapparaît au survol des 10% supérieurs du canvas. Un seul outil
    actif à la fois ; re-cliquer sur l'icône active désélectionne (retour à
    "aucun outil"). Voir idees.txt #3 pour le design complet.
    """

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

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._buttons: dict[str, _ToolButton] = {}
        crop_btn = _ToolButton(self, "BTN_Crop.png", tool_id="crop")
        layout.addWidget(crop_btn)
        self._buttons["crop"] = crop_btn

        straighten_btn = _ToolButton(self, "BTN_Straighten.png", tool_id="straighten")
        layout.addWidget(straighten_btn)
        self._buttons["straighten"] = straighten_btn

        clone_btn = _ToolButton(self, "BTN_Clone_Zone.png", tool_id="clone")
        layout.addWidget(clone_btn)
        self._buttons["clone"] = clone_btn

        text_btn = _ToolButton(self, "BTN_Text.png", tool_id="text")
        layout.addWidget(text_btn)
        self._buttons["text"] = text_btn

        self._separator = QFrame()
        self._separator.setFrameShape(QFrame.VLine)
        layout.addWidget(self._separator)

        self._undo_btn = _ActionButton("BTN_Batch_Undo.png", self._on_undo_clicked)
        layout.addWidget(self._undo_btn)
        self._redo_btn = _ActionButton("BTN_Batch_Redo.png", self._on_redo_clicked)
        layout.addWidget(self._redo_btn)

        self._overlay_tip = OverlayTooltip(self.window())
        self._overlay_tip.track(crop_btn)
        self._overlay_tip.track(straighten_btn)
        self._overlay_tip.track(clone_btn)
        self._overlay_tip.track(text_btn)
        self._overlay_tip.track(self._undo_btn)
        self._overlay_tip.track(self._redo_btn)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        # Panneau flottant de la spinbox d'angle (outil straighten uniquement,
        # affiché seulement quand un trait existe — voir _StraightenAnglePanel).
        self._angle_panel = _StraightenAnglePanel(viewer)

        # Panneau flottant des réglages du tampon (outil clone uniquement,
        # voir _CloneOptionsPanel).
        self._clone_panel = _CloneOptionsPanel(viewer)

        # Panneau flottant de formatage rich text (outil text uniquement,
        # visible seulement quand un bloc est actif — voir _TextOptionsPanel).
        self._text_panel = _TextOptionsPanel(viewer)

        self._apply_theme()
        self.hide()
        self.set_active_tool(None)
        self.refresh_undo_redo_state()

    # ── Thème / traduction ───────────────────────────────────────────────────

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"_ViewerToolbar {{ background: {theme['toolbar_bg']}; border-radius: 6px; }}"
        )
        self._separator.setStyleSheet(f"color: {theme['separator']};")
        for btn in self._buttons.values():
            btn._apply_style()
        self._undo_btn._apply_style()
        self._redo_btn._apply_style()
        self._angle_panel._apply_theme()
        self._clone_panel._apply_theme()
        self._text_panel._apply_theme()
        self._overlay_tip.apply_theme()

    def retranslate(self):
        crop_tip = (
            f"<b>{_('viewer.toolbar_crop_tooltip')}</b><br>"
            f"{_('viewer.toolbar_crop_instruction')}"
        )
        self._overlay_tip.track(self._buttons["crop"], crop_tip)
        self._overlay_tip.track(self._buttons["straighten"], "")
        self._update_straighten_tooltip()
        clone_tip = (
            f"<b>{_('viewer.toolbar_clone_tooltip')}</b><br>"
            f"{_('dialogs.clone_zone_viewer.instruction')}"
        )
        self._overlay_tip.track(self._buttons["clone"], clone_tip)
        text_tip = (
            f"<b>{_('viewer.toolbar_text_tooltip')}</b><br>"
            f"{_('dialogs.text_viewer.instruction')}"
        )
        self._overlay_tip.track(self._buttons["text"], text_tip)
        self._overlay_tip.track(self._undo_btn, _("viewer.toolbar_undo_tooltip"))
        self._overlay_tip.track(self._redo_btn, _("viewer.toolbar_redo_tooltip"))
        self._angle_panel.retranslate()
        self._clone_panel.retranslate()
        self._text_panel.retranslate()

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
        # courante ne sont pas effacés en désélectionnant l'outil (voir
        # idees.txt #3, persistance du travail non validé) — seule leur couleur
        # change (rouge actif / gris désélectionné, voir _ViewerCanvas.paintEvent)
        # pour rester visibles sans être trompeurs. Le bouton Valider flottant,
        # lui, ne doit être actionnable que si l'outil correspondant est
        # réellement sélectionné (sinon on pourrait valider un travail "en
        # pause" sans le vouloir).
        canvas = self._viewer._canvas
        if tool_id == "crop" and canvas.has_crop:
            canvas._show_validate_btn()
        elif tool_id == "straighten" and canvas.has_line:
            canvas._show_validate_btn()
        elif tool_id == "text" and canvas.has_text_blocks:
            canvas._show_validate_btn()
        else:
            canvas._hide_validate_btn()
        self._angle_panel.set_visible_for_tool(tool_id)
        self._clone_panel.set_visible_for_tool(tool_id)
        # Le clonage n'a pas de "travail en attente de validation" à conserver
        # visuellement en gris (chaque coup de tampon est déjà appliqué à
        # l'image) : la source Ctrl+cliquée est effacée dès qu'on quitte l'outil.
        if previous_tool == "clone" and tool_id != "clone":
            canvas.clear_clone_source()
        # Les blocs de texte tracés sur la page courante ne sont pas effacés en
        # désélectionnant l'outil (comme le crop/straighten) — ils sont figés
        # (plus de focus/édition possible) et grisés (idees.txt #3, décision
        # explicite). Le panneau de formatage n'est visible que si l'outil est
        # actif ET qu'un bloc est actif (set_visible_for_tool gère ce 2e critère).
        canvas._text_set_frozen(tool_id != "text")
        if tool_id != "text":
            canvas._text_active_block_ref = None
        self._text_panel.set_visible_for_tool(tool_id)
        canvas.update()

    def _on_tool_clicked(self, btn: "_ToolButton"):
        tool_id = btn._tool_id
        if not tool_id:
            return
        # Redressement en mode automatique (state.straighten_mode == 1, bascule
        # par clic droit — voir _on_tool_right_clicked) : le clic gauche ne
        # sélectionne pas un outil de tracé (rien à tracer en auto), il lance
        # directement le deskew sur la page actuellement affichée.
        if tool_id == "straighten" and self._straighten_mode() == 1:
            self._viewer.perform_auto_straighten()
            return
        if self.active_tool == tool_id:
            self.set_active_tool(None)
        else:
            self.set_active_tool(tool_id)

    def _on_tool_right_clicked(self, btn: "_ToolButton"):
        if btn._tool_id != "straighten":
            return
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
        # Ne désélectionne l'outil straighten de la barre que s'il était
        # effectivement l'outil actif (ne doit pas toucher au crop).
        if new_mode == 1:
            if self.active_tool == "straighten":
                self.set_active_tool(None)
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

    # ── Positionnement / visibilité ──────────────────────────────────────────

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
        self._hide_timer.start(self.AUTO_HIDE_MS)

    def note_activity(self):
        """À appeler à chaque mouvement de souris au-dessus de la barre elle-même,
        pour ne pas la masquer pendant qu'on l'utilise."""
        if self.isVisible():
            self._hide_timer.start(self.AUTO_HIDE_MS)

    def on_canvas_mouse_move(self, pos_y: int, canvas_height: int):
        if canvas_height <= 0:
            return
        if pos_y <= canvas_height * self.HOVER_ZONE_RATIO:
            self.show_and_schedule_hide()
        elif self.isVisible():
            self._hide_timer.start(self.AUTO_HIDE_MS)

    def mousePressEvent(self, event):
        # Une zone vide de la barre (marges, séparateur) ne doit rien
        # déclencher côté canvas — même raison que _ToolButton/_ActionButton.
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()
