"""
modules/qt/effects_tool_qt.py — Outil "effets" (effects) de la barre d'outils
flottante de la visionneuse principale (image_viewer_qt.py).

Fusion progressive des visionneuses : ce module contient l'outil "effets",
2e d'un trio de fonctions apparentées avec color_depth_tool_qt.py et
image_mode_tool_qt.py (profondeur de couleur, effets, mode d'image). Cadré
sur le MÊME modèle que la profondeur de couleur (skill adjust-color-depth)
: groupe de QRadioButton, commit IMMÉDIAT au clic, pas de preview
live (l'aperçu se fait directement dans la visionneuse) :

  - État initial (page affichée, aucun changement effectué) : radio
    "Restaurer l'original" grisé/inactif ; les 3 radios d'effet (Noir et
    blanc/Sépia/Inversion) actifs et cliquables, aucun coché. PAS de radio
    "Aucun effet" : il ferait double emploi
    avec "Restaurer l'original", qui joue déjà ce rôle — contrairement à la
    profondeur de couleur, dont les 4 profondeurs sont des transformations
    différentes de l'état d'origine sans notion de "pas de choix", "Aucun
    effet" n'aurait été qu'un second bouton pour revenir au même résultat que
    "Restaurer l'original".
  - Clic sur un effet (ex. "Sépia") : commit IMMÉDIAT dans entry['bytes']
    (perform_effect), nouvelle entrée undo/redo. Ce radio devient coché ET
    grisé (non re-cliquable) ; les 2 autres restent cliquables (changer
    directement vers un AUTRE effet, nouveau commit) ; "Restaurer l'original"
    devient actif.
  - Clic sur "Restaurer l'original" : restaure entry['bytes'] à son état
    D'AVANT LE TOUT PREMIER changement de cette session d'outil (pas un simple
    undo d'un cran — un enchaînement grayscale->sepia revient à l'état d'avant
    le tout premier clic, en un seul commit). Redevient lui-même grisé ; les 3
    radios d'effet redeviennent tous actifs, aucun coché.

Écart assumé par rapport à la profondeur de couleur (aucun équivalent
possible, signalé en amont avec l'utilisateur) : PAS de verrouillage dérivé du
mode PIL réel de l'image affichée — contrairement à _PIL_TO_DEPTH
(color_depth_tool_qt.py), aucun des 3 effets ne laisse de trace détectable
dans le mode PIL (grayscale/sepia restent en RGB visuellement gris/teinté,
invert ne change pas le mode). Le radio verrouillé après un commit reflète
donc un état MÉMORISÉ (le dernier effet appliqué cette session), pas un état
recalculé depuis l'image — et PAS de phrase d'info "Cette page est
actuellement en ..." sous les radios (rien d'équivalent à annoncer).

Contrairement aux 8 modes d'ajustement à réglette (dict state.
*_value_by_history_index, indexé par (page, history_index), RESYNCHRONISÉ à
chaque changement de page/undo-redo), l'état ici n'est PAS dérivé de
l'historique undo/redo : c'est un snapshot "avant premier changement" par
page, qui doit SURVIVRE au changement de page ET à un Ctrl+Z/Ctrl+Y pendant
que l'outil est actif (même raison que color_depth : "sinon il y a un risque
de confusion pour l'utilisateur"). Stocké sur state (state.
effect_original_bytes_by_page : dict[int, bytes]), PAS sur ImageViewer, jamais
vidé/réinitialisé au changement de page ni à un undo/redo, seulement quand
"Restaurer l'original" est cliqué pour cette page précise (ou à la fermeture
du fichier). Le radio coché/verrouillé (quel effet, ou None si aucun changement
n'a encore été fait sur cette page) est lui aussi mémorisé par page dans ce
même mécanisme — voir state.effect_key_by_page ci-dessous.

Pas de bouton "Valider"/"Annuler" flottant (comme la profondeur de couleur) :
chaque clic est déjà un commit complet, il ne peut jamais y avoir de travail
"en attente" à valider plus tard. Ne contribue pas à
ImageViewer._has_unvalidated_work(). Pas de bi-mode, icône fixe
(BTN_Effects.png), pas de grisage conditionnel selon le format.
"""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QRadioButton, QButtonGroup
from PySide6.QtCore import Qt

from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.clone_tool_qt import floating_options_panel_style

_EFFECT_KEYS = ('grayscale', 'sepia', 'invert')
_EFFECT_LABEL_KEYS = {
    'grayscale': 'dialogs.adjustments.effect_grayscale',
    'sepia':     'dialogs.adjustments.effect_sepia',
    'invert':    'dialogs.adjustments.effect_invert',
}


# ─────────────────────────────────────────────────────────────────────────────
# Panneau flottant des radios d'effet
# ─────────────────────────────────────────────────────────────────────────────

class _EffectsOptionsPanel(QWidget):
    """Panneau flottant avec les 4 radios (Restaurer l'original + Noir et
    blanc/Sépia/Inversion), affiché sous la barre d'outils uniquement quand
    l'outil "effects" est actif — même principe que _ColorDepthOptionsPanel
    (jamais inséré dans le layout de ImageViewer, indépendant du timer
    d'auto-masquage de la barre).

    Pas de radio "Aucun effet" (écarté explicitement, voir docstring de
    module) : "Restaurer l'original" joue déjà ce rôle.

    Contrairement à _ColorDepthOptionsPanel, une seule ligne suffit (3 radios
    d'effet aux libellés courts + Restaurer l'original, pas de découpage sur 2
    lignes nécessaire)."""

    def __init__(self, viewer: "ImageViewer"):
        super().__init__(viewer._canvas)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._viewer = viewer

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(2)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        outer.addLayout(row)
        row.addStretch(1)

        # "Restaurer l'original" n'appartient PAS au même QButtonGroup que les
        # 3 effets — même raison et même fix que _ColorDepthOptionsPanel
        # (skill adjust-color-depth) : setAutoExclusive(False) permanent,
        # jamais ajouté à self._group, pour éviter que Qt ne le coche
        # automatiquement au premier addButton() d'un groupe exclusif vide.
        # Ce n'est pas un choix d'EFFET parmi d'autres, c'est une action
        # "annuler tout" séparée.
        self._restore_radio = QRadioButton()
        self._restore_radio.setAutoExclusive(False)
        self._restore_radio.setEnabled(False)
        self._restore_radio.toggled.connect(self._on_restore_toggled)
        row.addWidget(self._restore_radio)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self._effect_radios: dict[str, QRadioButton] = {}
        for key in _EFFECT_KEYS:
            radio = QRadioButton()
            radio.toggled.connect(lambda checked, k=key: self._on_effect_toggled(k, checked))
            row.addWidget(radio)
            self._group.addButton(radio)
            self._effect_radios[key] = radio
        row.addStretch(1)

        self.hide()

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(floating_options_panel_style(theme, "_EffectsOptionsPanel"))
        # Indicateur ::indicator EXPLICITE obligatoire ici, même piège/fix que
        # _ColorDepthOptionsPanel (skill adjust-color-depth) : sur un panneau
        # WA_StyledBackground, un style qui ne pose que color/background
        # laisse l'indicateur natif invisible ; l'état désactivé-non-coché
        # reste creux (background: theme['bg']), seul :checked remplit
        # l'indicateur d'une couleur pleine, pour ne jamais se lire comme
        # "coché" à tort.
        accent = "#4a90d9"
        radio_style = (
            f"QRadioButton {{ color: {theme['text']}; background: transparent; spacing: 6px; }} "
            f"QRadioButton:disabled {{ color: {theme['separator']}; }} "
            f"QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px; "
            f"border: 1px solid {theme['text']}; background: {theme['bg']}; }} "
            f"QRadioButton::indicator:checked {{ background: {accent}; "
            f"border: 1px solid {theme['text']}; }} "
            f"QRadioButton::indicator:disabled {{ border: 1px solid {theme['separator']}; "
            f"background: {theme['bg']}; }} "
            f"QRadioButton::indicator:checked:disabled {{ background: {accent}; "
            f"border: 1px solid {theme['separator']}; }}"
        )
        self._restore_radio.setStyleSheet(radio_style)
        for radio in self._effect_radios.values():
            radio.setStyleSheet(radio_style)

    def retranslate(self):
        font = _get_current_font(11)
        self._restore_radio.setText(_("viewer.effects_panel_restore"))
        self._restore_radio.setFont(font)
        for key, radio in self._effect_radios.items():
            radio.setText(_(_EFFECT_LABEL_KEYS[key]))
            radio.setFont(font)

    # ── Visibilité ────────────────────────────────────────────────────────────

    def set_visible_for_tool(self, tool_id: str | None):
        if tool_id == "effects":
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

    # ── Réglage ──────────────────────────────────────────────────────────────

    def sync_to_page_state(self, has_original_saved: bool, locked_key: str | None):
        """Positionne les 4 radios sans déclencher de commit (blockSignals) —
        reflète l'état mémorisé de la page courante : has_original_saved
        pilote l'activation de "Restaurer l'original", locked_key (dernier
        effet appliqué cette session pour cette page, None si aucun changement)
        pilote quel radio est coché+grisé (aucun si None). Appelé au
        changement de page, à la sélection de l'outil, et après un
        commit/une restauration/un undo-redo.

        Contrairement à _ColorDepthOptionsPanel, locked_key n'est jamais
        dérivé du mode PIL réel de l'image (aucun effet ne laisse de trace
        détectable dans le mode) — c'est une valeur mémorisée par page (voir
        state.effect_key_by_page dans EffectsViewerMixin._sync_effects_panel).

        Même piège que _ColorDepthOptionsPanel :
        bloquer les signaux du QButtonGroup NE bloque PAS le signal toggled
        de chaque QRadioButton individuellement — blockSignals doit être posé
        sur CHAQUE radio, sinon un setChecked() ici redéclenche
        perform_effect()/perform_restore_effect() en pleine resynchronisation
        silencieuse.

        PIÈGE SUPPLÉMENTAIRE, propre à ce panneau (contrairement à
        _ColorDepthOptionsPanel, qui a toujours un radio coché après un
        commit puisqu'un mode PIL réel correspond toujours à l'une des 4
        profondeurs) : quand locked_key redevient None (après "Restaurer
        l'original"), AUCUN des 3 radios ne doit rester coché. Un
        QButtonGroup exclusif refuse silencieusement de décocher un radio par
        setChecked(False) tant qu'aucun AUTRE bouton du groupe n'est coché à
        sa place — Qt n'autorise à changer l'état "coché" d'un groupe
        exclusif qu'en cochant un autre membre, jamais en décochant
        explicitement celui déjà coché. Sans setExclusive(False) temporaire
        ici, le radio du dernier effet appliqué resterait donc visuellement
        coché après une restauration, alors qu'aucun effet n'est plus en
        cours."""
        all_radios = [self._restore_radio] + list(self._effect_radios.values())
        for radio in all_radios:
            radio.blockSignals(True)
        self._restore_radio.setEnabled(has_original_saved)
        self._restore_radio.setChecked(False)
        self._group.setExclusive(False)
        for key, radio in self._effect_radios.items():
            radio.setEnabled(key != locked_key)
            radio.setChecked(key == locked_key)
        self._group.setExclusive(True)
        for radio in all_radios:
            radio.blockSignals(False)

    def _on_effect_toggled(self, key: str, checked: bool):
        if checked:
            self._viewer.perform_effect(key)

    def _on_restore_toggled(self, checked: bool):
        if checked:
            self._viewer.perform_restore_effect()


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état de l'outil (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class EffectsCanvasMixin:
    """Hérité par _ViewerCanvas (image_viewer_qt.py) en plus de QLabel : ajoute
    l'état minimal de l'outil "effects" au canvas de la visionneuse, sans que
    son code vive dans image_viewer_qt.py.

    Volontairement vide, comme ColorDepthCanvasMixin : aucun overlay dessiné
    sur le canvas, aucun geste souris à intercepter (les radios du panneau
    flottant suffisent)."""

    def _init_effects_state(self):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — commit immédiat dans l'historique du panneau (hérité par ImageViewer)
# ─────────────────────────────────────────────────────────────────────────────

class EffectsViewerMixin:
    """Hérité par ImageViewer (image_viewer_qt.py) en plus de QDialog : ajoute
    la logique de l'outil "effects" au viewer, sans que son code vive dans
    image_viewer_qt.py. Suppose que l'hôte a déjà self._canvas
    (_ViewerCanvas), self.callbacks, self.current_idx, self._toolbar (avec
    _effects_panel)."""

    def perform_effect(self, key: str):
        """Clic sur un radio d'effet : commit IMMÉDIAT dans entry['bytes']
        (pattern skill apply-image-operation, variante A complète) —
        réutilise apply_image_adjustments() (image_processing_qt.py). Devient
        sa propre entrée d'historique.

        Avant ce commit, si aucun snapshot "avant premier changement" n'existe
        encore pour CETTE page (state.effect_original_bytes_by_page), on le
        capture maintenant — c'est ce que "Restaurer l'original" restaurera
        plus tard, quel que soit le nombre de clics d'effet intermédiaires
        (voir docstring de module). Mémorise aussi la clé d'effet choisie
        (state.effect_key_by_page) : contrairement à la profondeur de
        couleur, ce choix n'est pas déductible du mode PIL de l'image."""
        from modules.qt import state as _state_module
        from modules.qt.image_processing_qt import apply_image_adjustments
        from modules.qt.dialogs_qt import MsgDialog

        state = self.callbacks.get('state') or _state_module.state
        canvas = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]
            if not entry.get('bytes'):
                return

            if self.current_idx not in state.effect_original_bytes_by_page:
                state.effect_original_bytes_by_page[self.current_idx] = entry['bytes']

            apply_image_adjustments([entry], {'effect': key}, callbacks=self.callbacks)
            state.effect_key_by_page[self.current_idx] = key

            real_idx = entry.get("_real_idx")
            if canvas is not None and real_idx is not None:
                from modules.qt.mosaic_canvas import build_qimage_for_entry
                build_qimage_for_entry(entry)
                canvas.refresh_thumbnail(real_idx)
                canvas.refresh_duplicate_overlay()
            update_btn = self.callbacks.get("update_button_text")
            if update_btn:
                update_btn()

            self.display_image(keep_crop_rect=True)
            self._toolbar.refresh_undo_redo_state()
            self._sync_effects_panel()

        except Exception as e:
            dlg = MsgDialog(self._center_parent, "messages.errors.effect_failed.title",
                            "messages.errors.effect_failed.message",
                            message_kwargs={"error": str(e)})
            dlg.show_nonmodal()

    def perform_restore_effect(self):
        """Clic sur "Restaurer l'original" : restaure entry['bytes'] au
        snapshot capturé avant le tout premier changement d'effet de cette
        session d'outil sur cette page — un NOUVEAU commit (ajoute une entrée
        undo/redo, ne dépile pas l'historique), pas un undo. Retire ensuite
        l'entrée du dict pour cette page (et la clé d'effet mémorisée) : un
        nouveau clic sur un effet recommencera un nouveau snapshot."""
        from modules.qt import state as _state_module
        from modules.qt.dialogs_qt import MsgDialog

        state = self.callbacks.get('state') or _state_module.state
        canvas = self.callbacks.get("canvas")

        original_bytes = state.effect_original_bytes_by_page.get(self.current_idx)
        if original_bytes is None:
            return

        try:
            entry = state.images_data[self.current_idx]
            save_state = self.callbacks.get("save_state")
            if save_state:
                save_state()

            entry['bytes'] = original_bytes
            entry['img'] = None
            entry['qt_pixmap_large'] = None
            entry['qt_qimage_large'] = None

            if save_state:
                save_state(force=True)

            del state.effect_original_bytes_by_page[self.current_idx]
            state.effect_key_by_page.pop(self.current_idx, None)

            real_idx = entry.get("_real_idx")
            if canvas is not None and real_idx is not None:
                from modules.qt.mosaic_canvas import build_qimage_for_entry
                build_qimage_for_entry(entry)
                canvas.refresh_thumbnail(real_idx)
                canvas.refresh_duplicate_overlay()
            update_btn = self.callbacks.get("update_button_text")
            if update_btn:
                update_btn()

            self.display_image(keep_crop_rect=True)
            self._toolbar.refresh_undo_redo_state()
            self._sync_effects_panel()

        except Exception as e:
            dlg = MsgDialog(self._center_parent, "messages.errors.effect_failed.title",
                            "messages.errors.effect_failed.message",
                            message_kwargs={"error": str(e)})
            dlg.show_nonmodal()

    def _sync_effects_panel(self):
        """Resynchronise les 4 radios sur l'état MÉMORISÉ de la page
        COURANTE — appelé au changement de page, à la sélection de l'outil,
        après chaque commit/restauration, et après un undo/redo externe
        (Ctrl+Z ne vide PAS le snapshot ni la clé mémorisée, voir docstring de
        module : l'état visuel reste dérivé de la présence d'une entrée dans
        les dicts, jamais de l'historique).

        Contrairement à _sync_color_depth_panel, locked_key n'est PAS
        retrouvé depuis le mode PIL réel de l'image (aucun effet ne laisse de
        trace détectable dans le mode) — c'est state.effect_key_by_page, None
        par défaut si aucun changement n'a encore été fait sur cette page (ne
        coche alors aucun des 3 radios d'effet)."""
        from modules.qt import state as _state_module
        state = self.callbacks.get('state') or _state_module.state
        panel = self._toolbar._effects_panel

        has_original_saved = self.current_idx in state.effect_original_bytes_by_page
        locked_key = state.effect_key_by_page.get(self.current_idx)

        panel.sync_to_page_state(has_original_saved, locked_key)
