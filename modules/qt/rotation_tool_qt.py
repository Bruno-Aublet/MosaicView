"""
modules/qt/rotation_tool_qt.py — Outil "rotation" de la barre d'outils
flottante de la visionneuse principale (image_viewer_qt.py).

Fusion progressive des visionneuses : ce module contient le panneau flottant
(4 boutons d'action instantanée) et le commit de chaque opération sur la page
courante — image_viewer_qt.py ne fait qu'hériter des deux mixins et brancher
l'icône de la barre d'outils, voir CLAUDE.md règle "ne jamais migrer le code
d'un outil dans image_viewer_qt.py".

Contrairement aux autres outils déjà migrés, AUCUNE nouvelle logique métier
n'est écrite ici : les 4 boutons (rotation gauche, rotation droite, miroir
horizontal, miroir vertical) appellent directement rotate_entry_data()/
flip_entry_data() (modules/qt/image_ops.py, skill rotate-flip), déjà utilisées
par la colonne d'icônes verticale/la barre de menus/le menu contextuel pour la
sélection multiple dans la mosaïque. Ici, l'opération s'applique uniquement à
la page COURANTE de la visionneuse (self.current_idx), pas à state.
selected_indices — pas de worker QThread ni de barre de progression (rotate_
selected_qt/flip_selected_qt, image_transforms_qt.py, sont pensées pour un lot
potentiellement volumineux ; une seule page se traite en synchrone, comme les
autres outils à commit immédiat de cette barre).

Profil "radios/boutons + commit immédiat", comme color_depth/effects/
image_mode (color_depth_tool_qt.py) : chaque clic est un commit COMPLET dans
entry['bytes'], devient sa propre entrée d'historique. Contrairement à ces
trois-là, AUCUN état de verrouillage/snapshot à mémoriser (state.
color_depth_original_bytes_by_page et équivalents) : il n'y a pas de notion
de "profondeur/effet/mode actuellement appliqué" à griser — on peut cliquer
les 4 boutons autant de fois qu'on veut, dans n'importe quel ordre, chaque
clic ajoute simplement une transformation de plus. Pas de bouton "Valider"/
"Annuler" flottant, pas de persistance de "travail non validé" par page — ne
contribue pas à ImageViewer._has_unvalidated_work(). Pas de bi-mode, icône
fixe (BTN_Rotation.png), pas de grisage conditionnel (une rotation/un miroir
s'applique quel que soit le format source, comme color_depth).

rotate_entry_data()/flip_entry_data() ne font qu'une invalidation PARTIELLE
des caches (large_thumb_pil/_hash, voir skill apply-image-operation variante
B) — contrairement à apply_image_adjustments() (image_processing_qt.py),
utilisée par les 9 modes d'ajustement de cette barre, qui fait l'invalidation
COMPLÈTE (variante A) et appelle render_mosaic() elle-même. Ici, comme ces
deux fonctions sont appelées directement (hors du worker _run_transform qui
s'en charge d'habitude pour la mosaïque), perform_rotate()/perform_flip()
complètent explicitement l'invalidation (qt_pixmap_large/qt_qimage_large/img/
_thumbnail) et appellent render_mosaic() eux-mêmes — même pattern que
ColorDepthViewerMixin.perform_color_depth().
"""

from PIL import Image

from PySide6.QtWidgets import QWidget, QPushButton, QHBoxLayout
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon

from modules.qt import state as _state_module
from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.clone_tool_qt import floating_options_panel_style


# ─────────────────────────────────────────────────────────────────────────────
# Panneau flottant des 4 boutons de rotation/miroir
# ─────────────────────────────────────────────────────────────────────────────

class _RotationOptionsPanel(QWidget):
    """Panneau flottant avec les 4 boutons d'action instantanée (rotation
    gauche, rotation droite, miroir horizontal, miroir vertical, dans cet
    ordre), affiché sous la barre d'outils uniquement quand
    l'outil "rotation" est actif — même principe de positionnement que
    _ColorDepthOptionsPanel/_EffectsOptionsPanel.

    Boutons non checkable (contrairement aux radios de _ColorDepthOptions
    Panel) : il n'y a pas d'état "verrouillé" à représenter, chaque clic est
    une action instantanée indépendante, répétable sans limite — même
    principe que _ActionButton (undo/redo) de la barre principale, mais
    regroupés ici en QPushButton avec icône plutôt qu'en QLabel custom (même
    choix que _ShapeOptionsPanel pour ses 5 boutons de type de forme)."""

    def __init__(self, viewer: "ImageViewer"):
        super().__init__(viewer._canvas)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._viewer = viewer

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        self._rotate_left_btn = QPushButton()
        self._rotate_left_btn.setFixedSize(30, 30)
        self._rotate_left_btn.setIconSize(QSize(20, 20))
        self._rotate_left_btn.clicked.connect(lambda: self._viewer.perform_rotate(90))
        layout.addWidget(self._rotate_left_btn)

        self._rotate_right_btn = QPushButton()
        self._rotate_right_btn.setFixedSize(30, 30)
        self._rotate_right_btn.setIconSize(QSize(20, 20))
        self._rotate_right_btn.clicked.connect(lambda: self._viewer.perform_rotate(-90))
        layout.addWidget(self._rotate_right_btn)

        self._mirror_h_btn = QPushButton()
        self._mirror_h_btn.setFixedSize(30, 30)
        self._mirror_h_btn.setIconSize(QSize(20, 20))
        self._mirror_h_btn.clicked.connect(lambda: self._viewer.perform_flip('horizontal'))
        layout.addWidget(self._mirror_h_btn)

        self._mirror_v_btn = QPushButton()
        self._mirror_v_btn.setFixedSize(30, 30)
        self._mirror_v_btn.setIconSize(QSize(20, 20))
        self._mirror_v_btn.clicked.connect(lambda: self._viewer.perform_flip('vertical'))
        layout.addWidget(self._mirror_v_btn)

        self._load_icons()
        self.hide()

    # Icônes éclaircies en mode sombre (violet foncé sur fond gris foncé,
    # peu lisible) : contrairement à
    # BTN_Sharpness.png/BTN_Unsharp.png (noir plein, _DARK_MODE_RECOLOR_ICONS
    # de viewer_toolbar_qt.py, remplacement par une couleur unie), ces icônes
    # sont en deux tons d'une même teinte — un remplacement uni aplatirait le
    # dessin, donc éclaircissement proportionnel (_brighten_for_dark) plutôt
    # que recoloriage. BTN_Mirror_Horizontal/Vertical.png (rouge/vert vifs)
    # restent lisibles telles quelles, pas dans cette liste.
    _DARK_MODE_BRIGHTEN_ICONS = {"BTN_Rotate_Left.png", "BTN_Rotate_Right.png"}

    def _load_icons(self):
        """Icônes déjà utilisées telles quelles par la colonne d'icônes
        verticale (icon_toolbar_qt.py). Rappelée depuis _apply_theme() pour
        suivre un changement de thème en direct (même pattern que
        _ToolButton._load_icon() de la barre principale)."""
        from modules.qt.font_loader import resource_path
        from modules.qt.viewer_toolbar_qt import _brighten_for_dark
        dark = _state_module.state.dark_mode
        icon_map = {
            self._rotate_left_btn: "BTN_Rotate_Left.png",
            self._rotate_right_btn: "BTN_Rotate_Right.png",
            self._mirror_h_btn: "BTN_Mirror_Horizontal.png",
            self._mirror_v_btn: "BTN_Mirror_Vertical.png",
        }
        for btn, filename in icon_map.items():
            path = resource_path(f'icons/{filename}')
            if dark and filename in self._DARK_MODE_BRIGHTEN_ICONS:
                from modules.qt.image_viewer_qt import _pil_to_qpixmap
                pil_img = Image.open(path).convert("RGBA")
                pm = _pil_to_qpixmap(_brighten_for_dark(pil_img))
                btn.setIcon(QIcon(pm))
            else:
                btn.setIcon(QIcon(path))

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(floating_options_panel_style(theme, "_RotationOptionsPanel"))
        btn_style = (
            f"QPushButton {{ background: transparent; border: 1px solid transparent; "
            f"border-radius: 4px; }} "
            f"QPushButton:hover {{ background: {theme['icon_hover']}; }}"
        )
        for btn in (self._rotate_left_btn, self._rotate_right_btn,
                    self._mirror_h_btn, self._mirror_v_btn):
            btn.setStyleSheet(btn_style)
        # Recharge les icônes : nécessaire pour que rotate_left/right
        # basculent entre violet normal (clair) et éclairci (sombre) si le
        # thème change pendant que la visionneuse reste ouverte — voir
        # _DARK_MODE_BRIGHTEN_ICONS.
        self._load_icons()

    def retranslate(self):
        # Pas de libellé texte sur ces boutons (icônes seules, comme
        # _ShapeOptionsPanel) — retranslate() n'a donc rien à faire ici,
        # conservée pour l'appel symétrique depuis _ViewerToolbar.retranslate()
        # avec les autres panneaux.
        pass

    # ── Visibilité ────────────────────────────────────────────────────────────

    def set_visible_for_tool(self, tool_id: str | None):
        if tool_id == "rotation":
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
        # Même blindage anti-fuite de clic que tous les autres panneaux
        # flottants de cette barre (skill viewers) — un clic sur une zone
        # vide fuirait sinon vers _ViewerCanvas en dessous.
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


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état de l'outil (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class RotationCanvasMixin:
    """Hérité par _ViewerCanvas (image_viewer_qt.py) en plus de QLabel : ajoute
    l'état minimal de l'outil "rotation" au canvas de la visionneuse, sans
    que son code vive dans image_viewer_qt.py.

    Volontairement vide : comme color_depth/effects/image_mode, cet outil n'a
    aucun overlay dessiné sur le canvas et aucun geste souris à intercepter
    (les 4 boutons du panneau flottant suffisent)."""

    def _init_rotation_state(self):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — commit immédiat dans l'historique du panneau (hérité par ImageViewer)
# ─────────────────────────────────────────────────────────────────────────────

class RotationViewerMixin:
    """Hérité par ImageViewer (image_viewer_qt.py) en plus de QDialog : ajoute
    la logique de l'outil "rotation" au viewer, sans que son code vive dans
    image_viewer_qt.py. Suppose que l'hôte a déjà self._canvas
    (_ViewerCanvas), self.callbacks, self.current_idx, self._toolbar."""

    def perform_rotate(self, angle: int, skip_history: bool = False):
        """Clic sur rotation gauche/droite : commit IMMÉDIAT dans
        entry['bytes'] via rotate_entry_data() (image_ops.py, skill
        rotate-flip), déjà utilisée par la colonne d'icônes/barre de menus/
        menu contextuel pour la sélection multiple. Ici, appliquée
        uniquement à la page courante — pas de worker QThread ni de barre de
        progression, une seule page se traite en synchrone.
        angle: 90 pour rotation gauche (anti-horaire), -90 pour rotation
        droite (horaire) — même convention que rotate_selected_qt().
        skip_history : voir _commit_rotation_op()."""
        from modules.qt import state as _state_module
        from modules.qt.image_ops import rotate_entry_data
        from modules.qt.dialogs_qt import MsgDialog

        state = self.callbacks.get('state') or _state_module.state
        ok = self._commit_rotation_op(
            lambda entry: rotate_entry_data(entry, angle, state),
            state,
            "messages.errors.rotation_failed",
            skip_history=skip_history,
        )
        if ok:
            self._macro_record_step(
                "rotate", {"angle": angle},
                "macro.step_rotate", {"angle": angle},
            )
        return ok

    def perform_flip(self, direction: str, skip_history: bool = False):
        """Clic sur miroir horizontal/vertical : commit IMMÉDIAT dans
        entry['bytes'] via flip_entry_data() (image_ops.py, skill
        rotate-flip), même principe que perform_rotate() ci-dessus.
        direction: 'horizontal' ou 'vertical'.
        skip_history : voir perform_rotate()."""
        from modules.qt import state as _state_module
        from modules.qt.image_ops import flip_entry_data
        from modules.qt.dialogs_qt import MsgDialog

        state = self.callbacks.get('state') or _state_module.state
        ok = self._commit_rotation_op(
            lambda entry: flip_entry_data(entry, direction, state),
            state,
            "messages.errors.rotation_failed",
            skip_history=skip_history,
        )
        if ok:
            self._macro_record_step(
                "flip", {"direction": direction},
                "macro.step_flip", {"direction": direction},
            )
        return ok

    def _commit_rotation_op(self, op, state, error_key: str, skip_history: bool = False) -> bool:
        """Squelette de commit partagé par perform_rotate()/perform_flip() :
        save_state (avant) -> opération -> invalidation complète des caches
        (skill apply-image-operation, variante A — rotate_entry_data/
        flip_entry_data ne font qu'une invalidation partielle, voir docstring
        de module) -> save_state(force=True) (après) -> render_mosaic ->
        rafraîchissement de l'affichage/undo-redo. Même structure que
        ColorDepthViewerMixin.perform_color_depth().
        skip_history=True saute les 2 save_state, laissés à l'appelant (macro_
        engine.py). Retourne True si le commit a réellement eu lieu."""
        from modules.qt.dialogs_qt import MsgDialog

        if not (0 <= self.current_idx < len(state.images_data)):
            return False
        entry = state.images_data[self.current_idx]
        if not entry.get('bytes'):
            return False

        save_state = self.callbacks.get("save_state")
        render = self.callbacks.get("render_mosaic")

        try:
            if save_state and not skip_history:
                save_state(force=True)

            if not op(entry):
                return False

            entry['_thumbnail'] = None
            entry['qt_pixmap_large'] = None
            entry['qt_qimage_large'] = None
            state.modified = True

            if save_state and not skip_history:
                save_state(force=True)
            if render:
                render()

            update_btn = self.callbacks.get("update_button_text")
            if update_btn:
                update_btn()

            self.display_image()
            self._toolbar.refresh_undo_redo_state()
            return True

        except Exception as e:
            dlg = MsgDialog(self._center_parent, "messages.errors.rotation_failed.title",
                            "messages.errors.rotation_failed.message",
                            message_kwargs={"error": str(e)})
            dlg.show_nonmodal()
            return False
