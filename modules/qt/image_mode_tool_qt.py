"""
modules/qt/image_mode_tool_qt.py — Outil "mode d'image" (image_mode) de la
barre d'outils flottante de la visionneuse principale (image_viewer_qt.py).

Fusion progressive des visionneuses : ce module contient l'outil "mode
d'image", 3e d'un trio de fonctions apparentées avec color_depth_tool_qt.py
et effects_tool_qt.py (profondeur de couleur, effets, mode d'image). Cadré
sur le MÊME modèle que la profondeur de couleur (skill adjust-color-depth)
— contrairement à Effets, ce mode a un vrai équivalent PIL détectable, donc
le pattern de verrouillage EST dérivé du mode réel de l'image, exactement
comme color_depth_tool_qt.py :

  - État initial (page affichée, aucun changement effectué) : radio
    "Restaurer l'original" grisé/inactif ; le radio correspondant au mode PIL
    DÉJÀ COURANT de la page affichée est lui aussi grisé/inactif dès
    l'ouverture du panneau (pas seulement après un commit de CET outil) —
    ex. une page déjà en RGBA affiche son radio "RGBA" grisé d'emblée ; les
    6 autres radios de mode restent actifs et cliquables. PAS de radio
    "Ne pas modifier"/"unchanged" : un radio verrouillé sur le mode réel joue déjà ce rôle,
    exactement comme pour la profondeur de couleur — cliquer un mode DÉJÀ
    actif n'aurait aucun effet, il est donc simplement grisé plutôt que
    proposé comme un 8e choix redondant.
  - Clic sur un mode (ex. "CMYK") : commit IMMÉDIAT dans entry['bytes']
    (perform_image_mode), nouvelle entrée undo/redo. Ce radio devient coché ET
    grisé (non re-cliquable, le mode réel correspond désormais à lui) ; les
    autres restent cliquables (changer directement vers un AUTRE mode,
    nouveau commit) ; "Restaurer l'original" devient actif.
  - Clic sur "Restaurer l'original" : restaure entry['bytes'] à son état
    D'AVANT LE TOUT PREMIER changement de cette session d'outil (pas un simple
    undo d'un cran — un enchaînement RGB->CMYK->L revient à l'état d'avant le
    tout premier clic, en un seul commit). Redevient lui-même grisé ; le radio
    verrouillé redevient celui du mode PIL réel (redevenu l'original).

Contrairement aux 8 modes d'ajustement à réglette (dict state.
*_value_by_history_index, indexé par (page, history_index), RESYNCHRONISÉ à
chaque changement de page/undo-redo), l'état ici n'est PAS dérivé de
l'historique undo/redo : c'est un snapshot "avant premier changement" par
page, qui doit SURVIVRE au changement de page ET à un Ctrl+Z/Ctrl+Y pendant
que l'outil est actif (même raison que color_depth/effects : "sinon il y a un
risque de confusion pour l'utilisateur"). Stocké sur state (state.
image_mode_original_bytes_by_page : dict[int, bytes]), PAS sur ImageViewer,
jamais vidé/réinitialisé au changement de page ni à un undo/redo, seulement
quand "Restaurer l'original" est cliqué pour cette page précise (ou à la
fermeture du fichier).

Interaction avec la profondeur de couleur (skill adjust-color-depth) : le
bloc "Mode d'image" de apply_adjustments() (image_processing_qt.py)
s'exécute toujours AVANT celui de la profondeur de couleur — si les deux
outils sont utilisés sur la même page dans la visionneuse, la profondeur de
couleur garde le dernier mot sur le mode PIL final.

Pas de bouton "Valider"/"Annuler" flottant (comme la profondeur de couleur et
les effets) : chaque clic est déjà un commit complet, il ne peut jamais y
avoir de travail "en attente" à valider plus tard. Ne contribue pas à
ImageViewer._has_unvalidated_work(). Pas de bi-mode, icône fixe
(BTN_Image_Mode.png), pas de grisage conditionnel selon le format.
"""

import io

from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QVBoxLayout, QRadioButton, QButtonGroup
from PySide6.QtCore import Qt

from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.clone_tool_qt import floating_options_panel_style, BlockableRadioButton

# Correspondance mode PIL -> clé image_mode (skill adjust-image-mode). Ne couvre QUE les 7 modes
# ayant un radio équivalent dans ce panneau ; pilote UNIQUEMENT le grisage du
# radio correspondant (pas de phrase d'info séparée ici, contrairement à
# color_depth : les 7 radios couvrent déjà tous les modes PIL rencontrables
# dans ce contexte, il n'y a pas de mode "sans équivalent" à annoncer à part).
_PIL_TO_MODE = {'RGB': 'RGB', 'RGBA': 'RGBA', 'L': 'L', 'LA': 'LA',
                 'CMYK': 'CMYK', '1': 'BW1', 'P': 'P'}

_MODE_KEYS = ('RGB', 'RGBA', 'L', 'LA', 'CMYK', 'BW1', 'P')
_MODE_LABEL_KEYS = {
    'RGB':  'dialogs.adjustments.image_mode_rgb',
    'RGBA': 'dialogs.adjustments.image_mode_rgba',
    'L':    'dialogs.adjustments.image_mode_l',
    'LA':   'dialogs.adjustments.image_mode_la',
    'CMYK': 'dialogs.adjustments.image_mode_cmyk',
    'BW1':  'dialogs.adjustments.image_mode_1',
    'P':    'dialogs.adjustments.image_mode_p',
}

# Modes bloqués par extension d'origine : le format ne change jamais
# silencieusement à la sauvegarde, un choix incompatible reste impossible à
# sélectionner plutôt que dégradé après coup. Le libellé affiché dans le
# tooltip est dérivé de l'extension réelle du fichier (.jpg -> "JPG"), pas
# stocké ici — un fichier .jpg reste "JPG" à l'écran, jamais "JPEG".
_BLOCKED_MODE_KEYS_BY_EXT = {
    '.jpg':   {'RGBA', 'LA', 'P', 'BW1'},
    '.jpeg':  {'RGBA', 'LA', 'P', 'BW1'},
    '.jfif':  {'RGBA', 'LA', 'P', 'BW1'},
    '.pjpeg': {'RGBA', 'LA', 'P', 'BW1'},
    '.pjp':   {'RGBA', 'LA', 'P', 'BW1'},
    '.gif':   {'RGBA', 'LA', 'CMYK'},
    # BMP écrit bien un canal alpha 32-bit, mais Pillow (comme la plupart des
    # logiciels) ne le redétecte pas à la relecture — header BMP classique
    # ambigu sur la présence d'alpha, contrairement à BITMAPV4/V5HEADER avec
    # masques explicites. Transparence non fiable, donc bloquée.
    '.bmp':   {'RGBA', 'LA'},
}


# ─────────────────────────────────────────────────────────────────────────────
# Panneau flottant des radios de mode d'image
# ─────────────────────────────────────────────────────────────────────────────

class _ImageModeOptionsPanel(QWidget):
    """Panneau flottant avec les 8 radios (Restaurer l'original + les 7
    modes), affiché sous la barre d'outils uniquement quand l'outil
    "image_mode" est actif — même principe que _ColorDepthOptionsPanel
    (jamais inséré dans le layout de ImageViewer, indépendant du timer
    d'auto-masquage de la barre).

    Contrairement aux panneaux à réglette(s), aucun état de valeur "en cours
    de réglage" : chaque radio de mode, une fois coché, est immédiatement
    grisé (commit déjà effectué, non re-sélectionnable) — voir docstring de
    module pour le détail du verrouillage."""

    def __init__(self, viewer: "ImageViewer"):
        super().__init__(viewer._canvas)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._viewer = viewer
        # Mémorisés pour retraduire le tooltip des radios bloqués par format
        # (retranslate() n'a pas accès aux paramètres de sync_to_page_state).
        self._blocked_keys: set[str] = set()
        self._blocked_format_label: str = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(2)

        # 3 lignes de radios (7 modes aux libellés assez longs, ex. "RGBA
        # (couleurs + transparence)" — même raison que _ColorDepthOptionsPanel
        # : une seule ligne serait bien trop large).
        # Répartition : ligne 1 = Restaurer l'original + RGB + RGBA, ligne 2 =
        # L + LA + CMYK, ligne 3 = 1 bit + P — pas de découpage "logique"
        # particulier au-delà d'équilibrer visuellement les 3 lignes. Chaque
        # ligne centrée via un stretch de chaque côté (même principe que
        # _ColorDepthOptionsPanel/_LevelsOptionsPanel).
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(10)
        outer.addLayout(row1)
        row1.addStretch(1)

        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(10)
        outer.addLayout(row2)
        row2.addStretch(1)

        row3 = QHBoxLayout()
        row3.setContentsMargins(0, 0, 0, 0)
        row3.setSpacing(10)
        outer.addLayout(row3)
        row3.addStretch(1)

        # "Restaurer l'original" n'appartient PAS au même QButtonGroup que les
        # 7 modes — même raison et même fix que _ColorDepthOptionsPanel
        # (skill adjust-color-depth) : setAutoExclusive(False) permanent,
        # jamais ajouté à self._group, pour éviter que Qt ne le coche
        # automatiquement au premier addButton() d'un groupe exclusif vide.
        # Ce n'est pas un choix de MODE parmi d'autres, c'est une action
        # "annuler tout" séparée.
        self._restore_radio = QRadioButton()
        self._restore_radio.setAutoExclusive(False)
        self._restore_radio.setEnabled(False)
        self._restore_radio.toggled.connect(self._on_restore_toggled)
        row1.addWidget(self._restore_radio)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self._mode_radios: dict[str, BlockableRadioButton] = {}
        _ROW_FOR_KEY = {'RGB': row1, 'RGBA': row1,
                        'L': row2, 'LA': row2, 'CMYK': row2,
                        'BW1': row3, 'P': row3}
        for key in _MODE_KEYS:
            radio = BlockableRadioButton()
            radio.toggled.connect(lambda checked, k=key: self._on_mode_toggled(k, checked))
            _ROW_FOR_KEY[key].addWidget(radio)
            self._group.addButton(radio)
            self._mode_radios[key] = radio
        row1.addStretch(1)
        row2.addStretch(1)
        row3.addStretch(1)

        # viewer._toolbar n'existe pas encore ici (ce panneau est construit
        # DEPUIS le constructeur de _ViewerToolbar) — le tracking se fait au
        # premier sync_to_page_state(), une fois la toolbar assignée.
        self._tooltip_tracked = False

        self.hide()

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(floating_options_panel_style(theme, "_ImageModeOptionsPanel"))
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
            f"QRadioButton:disabled, QRadioButton[blocked=\"true\"] {{ color: {theme['separator']}; }} "
            f"QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px; "
            f"border: 1px solid {theme['text']}; background: {theme['bg']}; }} "
            f"QRadioButton::indicator:checked {{ background: {accent}; "
            f"border: 1px solid {theme['text']}; }} "
            f"QRadioButton::indicator:disabled, QRadioButton[blocked=\"true\"]::indicator {{ "
            f"border: 1px solid {theme['separator']}; background: {theme['bg']}; }} "
            f"QRadioButton::indicator:checked:disabled {{ background: {accent}; "
            f"border: 1px solid {theme['separator']}; }}"
        )
        self._restore_radio.setStyleSheet(radio_style)
        for radio in self._mode_radios.values():
            radio.setStyleSheet(radio_style)

    def retranslate(self):
        font = _get_current_font(11)
        self._restore_radio.setText(_("viewer.image_mode_panel_restore"))
        self._restore_radio.setFont(font)
        for key, radio in self._mode_radios.items():
            radio.setText(_(_MODE_LABEL_KEYS[key]))
            radio.setFont(font)
        self._update_blocked_tooltips()

    def _update_blocked_tooltips(self):
        """Reconstruit le tooltip des radios bloqués par format à partir de
        self._blocked_keys/_blocked_format_label mémorisés — appelée par
        retranslate() (changement de langue) et par sync_to_page_state()."""
        overlay_tip = self._viewer._toolbar._overlay_tip
        if not self._tooltip_tracked:
            for radio in self._mode_radios.values():
                overlay_tip.track(radio, "")
            self._tooltip_tracked = True

        blocked_tip = (
            f'<table style="max-width:360px;white-space:normal;">'
            f'<tr><td>{_("viewer.image_mode_panel_blocked_format", format=self._blocked_format_label)}</td></tr>'
            f'</table>'
        )
        for key, radio in self._mode_radios.items():
            overlay_tip.set_tracked_html(blocked_tip if key in self._blocked_keys else "", radio)

    # ── Visibilité ────────────────────────────────────────────────────────────

    def set_visible_for_tool(self, tool_id: str | None):
        if tool_id == "image_mode":
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

    def sync_to_page_state(self, has_original_saved: bool, locked_key: str | None,
                            blocked_keys: set[str] = frozenset(),
                            blocked_format_label: str = ""):
        """Positionne les 8 radios sans déclencher de commit (blockSignals) —
        has_original_saved pilote "Restaurer l'original", locked_key pilote
        quel radio de mode est coché+grisé, blocked_keys grise en plus les
        modes incompatibles avec le format d'origine (blocked_format_label =
        nom de ce format, pour le tooltip).

        Bloquer les signaux du QButtonGroup ne bloque PAS le signal toggled
        de chaque QRadioButton individuellement — sinon un setChecked() ici
        redéclencherait un commit/une restauration en pleine resynchronisation.
        setExclusive(False) temporaire : un QButtonGroup exclusif refuse
        silencieusement de décocher un radio par setChecked(False) tant
        qu'aucun autre n'est coché à sa place (cas locked_key=None).

        Les radios bloqués par format restent setEnabled(True) (sinon Qt
        cesse d'envoyer Enter/MouseMove et le tooltip ne se déclenche plus) :
        BlockableRadioButton.blocked rejette le clic dans mousePressEvent, la
        property Qt "blocked" pilote le style visuel (_apply_theme)."""
        all_radios = [self._restore_radio] + list(self._mode_radios.values())
        for radio in all_radios:
            radio.blockSignals(True)
        self._restore_radio.setEnabled(has_original_saved)
        self._restore_radio.setChecked(False)
        self._group.setExclusive(False)
        for key, radio in self._mode_radios.items():
            blocked = key in blocked_keys
            radio.blocked = blocked
            radio.setEnabled(blocked or key != locked_key)
            radio.setChecked(key == locked_key)
            radio.setCursor(Qt.ArrowCursor if blocked else Qt.PointingHandCursor)
            radio.setProperty("blocked", blocked)
            radio.style().unpolish(radio)
            radio.style().polish(radio)
        self._group.setExclusive(True)
        for radio in all_radios:
            radio.blockSignals(False)

        self._blocked_keys = set(blocked_keys)
        self._blocked_format_label = blocked_format_label
        self._update_blocked_tooltips()

    def _on_mode_toggled(self, key: str, checked: bool):
        if checked:
            self._viewer.perform_image_mode(key)

    def _on_restore_toggled(self, checked: bool):
        if checked:
            self._viewer.perform_restore_image_mode()


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état de l'outil (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class ImageModeCanvasMixin:
    """Hérité par _ViewerCanvas (image_viewer_qt.py) en plus de QLabel : ajoute
    l'état minimal de l'outil "image_mode" au canvas de la visionneuse, sans
    que son code vive dans image_viewer_qt.py.

    Volontairement vide : comme color_depth/effects, cet outil n'a aucun
    overlay dessiné sur le canvas, aucun geste souris à intercepter (les
    radios du panneau flottant suffisent)."""

    def _init_image_mode_state(self):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — commit immédiat dans l'historique du panneau (hérité par ImageViewer)
# ─────────────────────────────────────────────────────────────────────────────

class ImageModeViewerMixin:
    """Hérité par ImageViewer (image_viewer_qt.py) en plus de QDialog : ajoute
    la logique de l'outil "image_mode" au viewer, sans que son code vive dans
    image_viewer_qt.py. Suppose que l'hôte a déjà self._canvas
    (_ViewerCanvas), self.callbacks, self.current_idx, self._toolbar (avec
    _image_mode_panel)."""

    def perform_image_mode(self, key: str):
        """Clic sur un radio de mode : commit IMMÉDIAT dans entry['bytes']
        (pattern skill apply-image-operation, variante A complète) —
        réutilise apply_image_adjustments() (image_processing_qt.py). Devient
        sa propre entrée d'historique.

        Avant ce commit, si aucun snapshot "avant premier changement" n'existe
        encore pour CETTE page (state.image_mode_original_bytes_by_page), on
        le capture maintenant — c'est ce que "Restaurer l'original"
        restaurera plus tard, quel que soit le nombre de clics de mode
        intermédiaires (voir docstring de module)."""
        from modules.qt import state as _state_module
        from modules.qt.image_processing_qt import apply_image_adjustments
        from modules.qt.dialogs_qt import MsgDialog

        state = self.callbacks.get('state') or _state_module.state
        canvas = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]
            if not entry.get('bytes'):
                return

            if self.current_idx not in state.image_mode_original_bytes_by_page:
                state.image_mode_original_bytes_by_page[self.current_idx] = entry['bytes']

            apply_image_adjustments([entry], {'image_mode': key}, callbacks=self.callbacks)

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
            self._sync_image_mode_panel()

        except Exception as e:
            dlg = MsgDialog(self._center_parent, "messages.errors.image_mode_failed.title",
                            "messages.errors.image_mode_failed.message",
                            message_kwargs={"error": str(e)})
            dlg.show_nonmodal()

    def perform_restore_image_mode(self):
        """Clic sur "Restaurer l'original" : restaure entry['bytes'] au
        snapshot capturé avant le tout premier changement de mode de cette
        session d'outil sur cette page — un NOUVEAU commit (ajoute une entrée
        undo/redo, ne dépile pas l'historique), pas un undo. Retire ensuite
        l'entrée du dict pour cette page : un nouveau clic sur un mode
        recommencera un nouveau snapshot."""
        from modules.qt import state as _state_module
        from modules.qt.dialogs_qt import MsgDialog

        state = self.callbacks.get('state') or _state_module.state
        canvas = self.callbacks.get("canvas")

        original_bytes = state.image_mode_original_bytes_by_page.get(self.current_idx)
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

            del state.image_mode_original_bytes_by_page[self.current_idx]

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
            self._sync_image_mode_panel()

        except Exception as e:
            dlg = MsgDialog(self._center_parent, "messages.errors.image_mode_failed.title",
                            "messages.errors.image_mode_failed.message",
                            message_kwargs={"error": str(e)})
            dlg.show_nonmodal()

    def _sync_image_mode_panel(self):
        """Resynchronise les 8 radios sur l'état de la page COURANTE — appelé
        au changement de page, à la sélection de l'outil, après chaque
        commit/restauration, et après un undo/redo externe (Ctrl+Z ne vide
        PAS le snapshot, voir docstring de module : l'état visuel reste
        dérivé de la présence d'une entrée dans le dict, jamais de
        l'historique).

        locked_key (quel radio de mode apparaît coché+grisé) est retrouvé
        depuis le mode PIL RÉEL de l'image affichée (pas mémorisé
        séparément), exactement comme color_depth — après un commit ou un
        undo/redo, c'est toujours le mode PIL courant qui fait foi de ce qui
        est "déjà appliqué"."""
        from modules.qt import state as _state_module
        state = self.callbacks.get('state') or _state_module.state
        panel = self._toolbar._image_mode_panel

        has_original_saved = self.current_idx in state.image_mode_original_bytes_by_page

        locked_key = None
        blocked_keys: set[str] = set()
        blocked_format_label = ""
        if 0 <= self.current_idx < len(state.images_data):
            entry = state.images_data[self.current_idx]
            if entry.get('bytes'):
                try:
                    from PIL import Image
                    img = Image.open(io.BytesIO(entry['bytes']))
                    locked_key = _PIL_TO_MODE.get(img.mode)
                except Exception:
                    pass
            ext = entry.get('extension', '')
            blocked = _BLOCKED_MODE_KEYS_BY_EXT.get(ext.lower())
            if blocked:
                blocked_keys = blocked
                blocked_format_label = ext.lstrip('.').upper()

        panel.sync_to_page_state(has_original_saved, locked_key, blocked_keys, blocked_format_label)
