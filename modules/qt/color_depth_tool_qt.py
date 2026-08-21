"""
modules/qt/color_depth_tool_qt.py — Outil "profondeur de couleur"
(color_depth) de la barre d'outils flottante de la visionneuse principale
(image_viewer_qt.py).

Fusion progressive des visionneuses : ce module contient la logique de
l'outil "profondeur de couleur" — profondeur de couleur, effets et mode
d'image forment un trio de fonctions apparentées (voir effects_tool_qt.py,
image_mode_tool_qt.py). Contrairement aux modes d'ajustement à réglette ou
geste souris (sharpness/unsharp/brightness/saturation/remove_colors/
compression/levels/transparency), c'est un groupe de QRadioButton (choix
mutuellement exclusifs), sans overlay ni geste souris — mais avec un
comportement de VERROUILLAGE propre à ce trio :

  - État initial (page affichée, aucun changement effectué) : radio
    "Restaurer l'original" grisé/inactif ; les 4 radios de profondeur (32/24/
    8/1 bits) actifs et cliquables ; aucun radio coché.
  - Clic sur une profondeur (ex. "24 bits") : commit IMMÉDIAT dans
    entry['bytes'] (perform_color_depth), nouvelle entrée undo/redo. Ce radio
    devient coché ET grisé (non re-cliquable) ; les 3 autres profondeurs
    restent cliquables (permet de changer directement vers une AUTRE
    profondeur, nouveau commit) ; "Restaurer l'original" devient actif.
  - Clic sur "Restaurer l'original" : restaure entry['bytes'] à son état
    D'AVANT LE TOUT PREMIER changement de cette session d'outil (pas un
    simple undo d'un cran — un enchaînement 32→24→8 bits revient à l'état
    d'avant le tout premier clic, en un seul commit). Redevient lui-même
    grisé ; les 4 radios de profondeur redeviennent tous actifs, aucun coché.
  - Le radio correspondant au mode PIL DÉJÀ COURANT de la page affichée est
    lui aussi grisé/inactif dès l'ouverture du panneau (pas seulement après
    un commit de CET outil) — ex. une page déjà en 8 bits affiche son radio
    "8 bits" grisé d'emblée ; il ne se réactive que si l'utilisateur clique
    sur un AUTRE choix (nouveau commit, qui change alors le mode réel).
  - Phrase d'info en italique sous les radios (couleur de texte du thème,
    jamais de couleur vive — CLAUDE.md "détails de style annexes"), TOUJOURS
    visible, ex. "Cette page est actuellement en 24 bits." : sans elle, un
    radio grisé n'explique pas pourquoi. Couvre TOUS les modes PIL
    rencontrables, pas seulement les 4 profondeurs de ce panneau — un mode
    sans radio équivalent (LA, CMYK) est quand même annoncé, avec son nom de
    mode PIL brut plutôt qu'un nombre de bits, et ne grise alors aucun radio
    de profondeur (voir _PIL_MODE_LABEL_KEYS vs _PIL_TO_DEPTH plus bas).

Contrairement aux 8 modes d'ajustement (dict state.*_value_by_history_index,
indexé par (page, history_index), RESYNCHRONISÉ à chaque changement de page/
undo-redo — voir sharpness_tool_qt.py), l'état ici n'est PAS dérivé de
l'historique undo/redo : c'est un snapshot "avant premier changement" par
page, qui doit SURVIVRE au changement de page ET à un Ctrl+Z/Ctrl+Y pendant
que l'outil est actif (sinon risque de confusion pour l'utilisateur). Stocké
sur state (state.color_depth_original_bytes_by_page : dict[int, bytes]),
PAS sur ImageViewer,
même raison que les dicts *_value_by_history_index (survivre à une fermeture/
réouverture de la visionneuse tant que le fichier reste ouvert) — mais ici
jamais vidé/réinitialisé au changement de page ni à un undo/redo, seulement
quand "Restaurer l'original" est cliqué pour cette page précise (ou à la
fermeture du fichier). L'état visuel du panneau (quel radio est verrouillé,
"Restaurer l'original" actif ou non) est entièrement dérivé de la présence
d'une entrée dans ce dict pour la page courante — jamais de l'historique.

Pas de bouton "Valider"/"Annuler" flottant (comme les niveaux) : chaque clic
est déjà un commit complet, il ne peut jamais y avoir de travail "en attente"
à valider plus tard. Ne contribue pas à ImageViewer._has_unvalidated_work().
Pas de bi-mode, icône fixe (BTN_Color_Depth.png), pas de grisage conditionnel
selon le format (contrairement à compression/transparency — la profondeur de
couleur s'applique quel que soit le format source).
"""

import io

from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QVBoxLayout, QRadioButton, QButtonGroup
from PySide6.QtCore import Qt

from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.clone_tool_qt import floating_options_panel_style, BlockableRadioButton

# Correspondance mode PIL -> clé depth (skill adjust-color-depth). Ne couvre QUE les 4 modes
# ayant un radio de profondeur équivalent dans ce panneau (RGBA/RGB/L/P/'1') ;
# ne pilote QUE le grisage du radio correspondant, pas la phrase d'info
# ci-dessous (voir _PIL_MODE_LABEL_KEYS, qui couvre tous les modes PIL,
# y compris LA/CMYK qui n'ont pas d'équivalent parmi les 4 profondeurs).
_PIL_TO_DEPTH = {'RGBA': '32', 'RGB': '24', 'L': '8', 'P': '8', '1': '1'}

_DEPTH_KEYS = ('32', '24', '8', '1')
_DEPTH_LABEL_KEYS = {
    '32': 'dialogs.adjustments.depth_32bit',
    '24': 'dialogs.adjustments.depth_24bit',
    '8':  'dialogs.adjustments.depth_8bit',
    '1':  'dialogs.adjustments.depth_1bit',
}

# Profondeurs bloquées par extension d'origine : le format ne change jamais
# silencieusement à la sauvegarde, un choix incompatible reste impossible à
# sélectionner plutôt que dégradé après coup. Le libellé affiché dans le
# tooltip est dérivé de l'extension réelle du fichier (.jpg -> "JPG"), pas
# stocké ici — un fichier .jpg reste "JPG" à l'écran, jamais "JPEG".
_BLOCKED_DEPTH_KEYS_BY_EXT = {
    '.jpg':   {'32', '1'},
    '.jpeg':  {'32', '1'},
    '.jfif':  {'32', '1'},
    '.pjpeg': {'32', '1'},
    '.pjp':   {'32', '1'},
    '.gif':   {'32'},
    # BMP écrit bien un canal alpha 32-bit, mais Pillow (comme la plupart des
    # logiciels) ne le redétecte pas à la relecture — header BMP classique
    # ambigu sur la présence d'alpha, contrairement à BITMAPV4/V5HEADER avec
    # masques explicites. Transparence non fiable, donc bloquée.
    '.bmp':   {'32'},
}

# Libellé de la phrase d'info ("Cette page est actuellement en ...") pour
# TOUT mode PIL rencontrable (pas seulement les 4 profondeurs) : un mode
# sans radio équivalent (LA, CMYK) doit être affiché quand même, avec son nom
# de mode PIL brut plutôt qu'un nombre de bits, et ne grise alors aucun radio
# de profondeur (déjà le comportement de _PIL_TO_DEPTH ci-dessus, qui ne
# couvre pas ces modes). Réutilise les clés de traduction existantes
# (dialogs.adjustments.image_mode_*, skill adjust-image-mode) plutôt que
# d'en retraduire des neuves dans les 46 langues.
_PIL_MODE_LABEL_KEYS = {
    'RGB':  'dialogs.adjustments.image_mode_rgb',
    'RGBA': 'dialogs.adjustments.image_mode_rgba',
    'L':    'dialogs.adjustments.image_mode_l',
    'LA':   'dialogs.adjustments.image_mode_la',
    'CMYK': 'dialogs.adjustments.image_mode_cmyk',
    '1':    'dialogs.adjustments.image_mode_1',
    'P':    'dialogs.adjustments.image_mode_p',
}


# ─────────────────────────────────────────────────────────────────────────────
# Panneau flottant des radios de profondeur de couleur
# ─────────────────────────────────────────────────────────────────────────────

class _ColorDepthOptionsPanel(QWidget):
    """Panneau flottant avec les 5 radios (Restaurer l'original + 4
    profondeurs), affiché sous la barre d'outils uniquement quand l'outil
    "color_depth" est actif — même principe que _RemoveColorsOptionsPanel
    (jamais inséré dans le layout de ImageViewer, indépendant du timer
    d'auto-masquage de la barre).

    Contrairement aux panneaux à réglette(s), aucun état de valeur "en cours
    de réglage" : chaque radio de profondeur, une fois coché, est
    immédiatement grisé (commit déjà effectué, non re-sélectionnable) — voir
    docstring de module pour le détail du verrouillage."""

    def __init__(self, viewer: "ImageViewer"):
        super().__init__(viewer._canvas)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._viewer = viewer
        # Mode PIL réel de la page courante, mémorisé pour retraduire la
        # phrase d'info (_update_info_label) sans redemander l'info à
        # l'appelant — mis à jour uniquement par sync_to_page_state.
        self._current_pil_mode: str | None = None
        # Idem pour le tooltip des radios bloqués par format (retranslate()
        # n'a pas accès aux paramètres de sync_to_page_state).
        self._blocked_keys: set[str] = set()
        self._blocked_format_label: str = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(2)

        # 2 lignes de radios : les 5 libellés complets ("32 bits (RGBA -
        # couleurs + transparence)" etc.) rendraient une seule ligne bien
        # trop large — même principe de panneau multi-lignes que
        # _LevelsOptionsPanel (seul autre panneau de cette barre à en avoir
        # plusieurs), chaque ligne son propre QHBoxLayout empilé dans le
        # QVBoxLayout englobant. Répartition : ligne 1 = Restaurer l'original
        # + 32 bits + 24 bits, ligne 2 = 8 bits + 1 bit — pas de découpage
        # "logique" particulier au-delà d'équilibrer visuellement les 2
        # lignes. Lignes centrées via un stretch de chaque côté (même
        # principe que _LevelsOptionsPanel.pip_row) : sans ça, les 2 lignes
        # de longueurs différentes resteraient alignées à gauche,
        # visuellement décalées l'une par rapport à l'autre.
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

        # "Restaurer l'original" n'appartient PAS au même QButtonGroup que les
        # 4 profondeurs (setChecked(False)/setExclusive(False) temporaires ne
        # feraient que masquer le problème sans l'empêcher structurellement)
        # : ce n'est pas un choix
        # de PROFONDEUR parmi d'autres (il ne coexiste jamais visuellement
        # comme "sélectionné" avec un radio de profondeur, les deux ne
        # s'excluent pas au sens d'un vrai groupe de choix), c'est une action
        # "annuler tout le groupe" séparée. setAutoExclusive(False) dès la
        # création : jamais ajouté à self._group, jamais coché
        # automatiquement par Qt au premier addButton() d'un groupe exclusif
        # vide (cause racine du bug : le tout premier addButton() d'un
        # QButtonGroup exclusif coche automatiquement ce bouton). Son état
        # coché reste géré manuellement (toujours False juste après un clic,
        # voir _on_restore_toggled) — le radio ne sert que de bouton-poussoir
        # visuel, jamais de mémoire d'état "actif".
        self._restore_radio = QRadioButton()
        self._restore_radio.setAutoExclusive(False)
        self._restore_radio.setEnabled(False)
        self._restore_radio.toggled.connect(self._on_restore_toggled)
        row1.addWidget(self._restore_radio)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self._depth_radios: dict[str, BlockableRadioButton] = {}
        _ROW_FOR_KEY = {'32': row1, '24': row1, '8': row2, '1': row2}
        for key in _DEPTH_KEYS:
            radio = BlockableRadioButton()
            radio.toggled.connect(lambda checked, k=key: self._on_depth_toggled(k, checked))
            _ROW_FOR_KEY[key].addWidget(radio)
            self._group.addButton(radio)
            self._depth_radios[key] = radio
        row1.addStretch(1)
        row2.addStretch(1)

        # viewer._toolbar n'existe pas encore ici (ce panneau est construit
        # DEPUIS le constructeur de _ViewerToolbar) — le tracking se fait au
        # premier sync_to_page_state(), une fois la toolbar assignée.
        self._tooltip_tracked = False

        # Phrase d'info TOUJOURS visible : sans elle, un radio grisé
        # n'explique pas pourquoi — italique, couleur de texte du thème
        # (CLAUDE.md "détails de style
        # annexes", jamais de couleur vive pour une info non bloquante).
        self._info_label = QLabel()
        self._info_label.setAlignment(Qt.AlignCenter)
        outer.addWidget(self._info_label)

        self.hide()

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(floating_options_panel_style(theme, "_ColorDepthOptionsPanel"))
        # Indicateur ::indicator EXPLICITE obligatoire ici (piège déjà
        # documenté skill viewers/_CloneOptionsPanel et shapes_tool_qt.py) :
        # sur un panneau WA_StyledBackground, un style QRadioButton qui ne
        # pose que color/background laisse l'indicateur natif (la puce ronde)
        # totalement invisible — d'où des boutons radio invisibles sans ce
        # style explicite. Accent bleu (comme le reste de la
        # barre) pour l'état coché, gris atténué pour l'état désactivé (radio
        # déjà verrouillé après commit, ou "Restaurer l'original" avant tout
        # changement) — cohérent avec les autres accents de cette barre
        # (voir _RemoveColorsOptionsPanel::_apply_theme, accent = "#4a90d9").
        # Piège purement visuel, indépendant de l'état logique (isChecked()) :
        # QRadioButton::indicator:disabled qui remplirait l'indicateur avec
        # background: theme['separator'] même quand le radio n'est PAS coché
        # — un cercle rempli d'une couleur pleine (même grise/neutre) se lit
        # visuellement comme "coché" quel que soit l'état logique réel, d'où
        # une illusion de "Restaurer l'original" coché dès l'ouverture alors
        # qu'il ne l'est jamais. Fix : l'état désactivé-non-coché ne change
        # QUE la bordure (plus fine/grisée) et reste creux (background:
        # theme['bg'], identique à l'état activé-non-coché) — seul :checked
        # (avec ou sans :disabled) remplit l'indicateur d'une couleur pleine.
        accent = "#4a90d9"
        radio_style = (
            f"QRadioButton {{ color: {theme['text']}; background: transparent; spacing: 6px; }} "
            f"QRadioButton:disabled {{ color: {theme['separator']}; }} "
            f"QRadioButton[blocked=\"true\"] {{ color: {theme['separator']}; }} "
            f"QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px; "
            f"border: 1px solid {theme['text']}; background: {theme['bg']}; }} "
            f"QRadioButton::indicator:checked {{ background: {accent}; "
            f"border: 1px solid {theme['text']}; }} "
            f"QRadioButton::indicator:disabled {{ border: 1px solid {theme['separator']}; "
            f"background: {theme['bg']}; }} "
            f"QRadioButton[blocked=\"true\"]::indicator {{ border: 1px solid {theme['separator']}; "
            f"background: {theme['bg']}; }} "
            f"QRadioButton::indicator:checked:disabled {{ background: {accent}; "
            f"border: 1px solid {theme['separator']}; }}"
        )
        self._restore_radio.setStyleSheet(radio_style)
        for radio in self._depth_radios.values():
            radio.setStyleSheet(radio_style)
        self._info_label.setStyleSheet(f"color: {theme['text']}; background: transparent;")

    def retranslate(self):
        font = _get_current_font(11)
        self._restore_radio.setText(_("viewer.color_depth_panel_restore"))
        self._restore_radio.setFont(font)
        for key, radio in self._depth_radios.items():
            radio.setText(_(_DEPTH_LABEL_KEYS[key]))
            radio.setFont(font)
        italic_font = _get_current_font(10)
        italic_font.setItalic(True)
        self._info_label.setFont(italic_font)
        self._update_info_label()
        self._update_blocked_tooltips()

    def _update_info_label(self):
        """Reconstruit le texte de la phrase d'info à partir du mode PIL
        mémorisé — appelée par retranslate() (changement de langue/police) et
        par sync_to_page_state() (changement de page/mode réel)."""
        mode = self._current_pil_mode
        label_key = _PIL_MODE_LABEL_KEYS.get(mode) if mode else None
        if label_key:
            mode_text = _(label_key)
            self._info_label.setText(_("viewer.color_depth_panel_current_format", format=mode_text))
        else:
            self._info_label.setText("")

    def _update_blocked_tooltips(self):
        """Reconstruit le tooltip des radios bloqués par format à partir de
        self._blocked_keys/_blocked_format_label mémorisés — appelée par
        retranslate() (changement de langue) et par sync_to_page_state()."""
        overlay_tip = self._viewer._toolbar._overlay_tip
        if not self._tooltip_tracked:
            for radio in self._depth_radios.values():
                overlay_tip.track(radio, "")
            self._tooltip_tracked = True

        blocked_tip = (
            f'<table style="max-width:360px;white-space:normal;">'
            f'<tr><td>{_("viewer.color_depth_panel_blocked_format", format=self._blocked_format_label)}</td></tr>'
            f'</table>'
        )
        for key, radio in self._depth_radios.items():
            overlay_tip.set_tracked_html(blocked_tip if key in self._blocked_keys else "", radio)

    # ── Visibilité ────────────────────────────────────────────────────────────

    def set_visible_for_tool(self, tool_id: str | None):
        if tool_id == "color_depth":
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
                            pil_mode: str | None, blocked_keys: set[str] = frozenset(),
                            blocked_format_label: str = ""):
        """Positionne les 5 radios sans déclencher de commit (blockSignals) —
        reflète l'état de la page courante : has_original_saved pilote
        l'activation de "Restaurer l'original", locked_key pilote quel radio
        de profondeur est coché+grisé, pil_mode pilote la phrase d'info,
        blocked_keys grise en plus les profondeurs incompatibles avec le
        format d'origine (blocked_format_label = nom de ce format, pour le
        tooltip).

        Bloquer les signaux du QButtonGroup ne bloque PAS le signal toggled
        de chaque QRadioButton individuellement — sinon un setChecked() ici
        redéclencherait un commit/une restauration en pleine resynchronisation.

        Les radios bloqués par format restent setEnabled(True) (un widget
        désactivé ne reçoit plus Enter/MouseMove, le tooltip OverlayTooltip.
        track() ne se déclencherait jamais) : BlockableRadioButton.blocked
        rejette le clic dans mousePressEvent, la property Qt "blocked"
        pilote le style visuel (_apply_theme)."""
        all_radios = [self._restore_radio] + list(self._depth_radios.values())
        for radio in all_radios:
            radio.blockSignals(True)
        self._restore_radio.setEnabled(has_original_saved)
        self._restore_radio.setChecked(False)
        for key, radio in self._depth_radios.items():
            blocked = key in blocked_keys
            radio.blocked = blocked
            # setEnabled(True) tant que blocked : sinon Qt cesse d'envoyer
            # Enter/MouseMove et le tooltip ne se déclenche plus jamais.
            radio.setEnabled(blocked or key != locked_key)
            radio.setChecked(key == locked_key)
            radio.setCursor(Qt.ArrowCursor if blocked else Qt.PointingHandCursor)
            radio.setProperty("blocked", blocked)
            radio.style().unpolish(radio)
            radio.style().polish(radio)
        for radio in all_radios:
            radio.blockSignals(False)
        self._current_pil_mode = pil_mode
        self._update_info_label()

        self._blocked_keys = set(blocked_keys)
        self._blocked_format_label = blocked_format_label
        self._update_blocked_tooltips()

    def _on_depth_toggled(self, key: str, checked: bool):
        if checked:
            self._viewer.perform_color_depth(key)

    def _on_restore_toggled(self, checked: bool):
        if checked:
            self._viewer.perform_restore_color_depth()


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état de l'outil (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class ColorDepthCanvasMixin:
    """Hérité par _ViewerCanvas (image_viewer_qt.py) en plus de QLabel : ajoute
    l'état minimal de l'outil "color_depth" au canvas de la visionneuse, sans
    que son code vive dans image_viewer_qt.py.

    Volontairement vide : comme les 6 modes preview-slider purs (sharpness/
    brightness/saturation/remove_colors/compression) ou les niveaux, cet
    outil n'a aucun overlay dessiné sur le canvas — contrairement aux
    niveaux/transparence, il n'a même pas de geste souris à intercepter (les
    radios du panneau flottant suffisent, pas de pipette)."""

    def _init_color_depth_state(self):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — commit immédiat dans l'historique du panneau (hérité par ImageViewer)
# ─────────────────────────────────────────────────────────────────────────────

class ColorDepthViewerMixin:
    """Hérité par ImageViewer (image_viewer_qt.py) en plus de QDialog : ajoute
    la logique de l'outil "color_depth" au viewer, sans que son code vive
    dans image_viewer_qt.py. Suppose que l'hôte a déjà self._canvas
    (_ViewerCanvas), self.callbacks, self.current_idx, self._toolbar (avec
    _color_depth_panel)."""

    def perform_color_depth(self, key: str):
        """Clic sur un radio de profondeur : commit IMMÉDIAT dans
        entry['bytes'] (pattern skill apply-image-operation, variante A
        complète) — réutilise apply_image_adjustments()
        (image_processing_qt.py). Devient sa propre entrée d'historique.

        Avant ce commit, si aucun snapshot "avant premier changement" n'existe
        encore pour CETTE page (state.color_depth_original_bytes_by_page),
        on le capture maintenant — c'est ce que "Restaurer l'original"
        restaurera plus tard, quel que soit le nombre de clics de profondeur
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

            if self.current_idx not in state.color_depth_original_bytes_by_page:
                state.color_depth_original_bytes_by_page[self.current_idx] = entry['bytes']

            apply_image_adjustments([entry], {'color_depth': key}, callbacks=self.callbacks)

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
            self._sync_color_depth_panel()

        except Exception as e:
            dlg = MsgDialog(self._center_parent, "messages.errors.color_depth_failed.title",
                            "messages.errors.color_depth_failed.message",
                            message_kwargs={"error": str(e)})
            dlg.show_nonmodal()

    def perform_restore_color_depth(self):
        """Clic sur "Restaurer l'original" : restaure entry['bytes'] au
        snapshot capturé avant le tout premier changement de profondeur de
        cette session d'outil sur cette page — un NOUVEAU commit (ajoute une
        entrée undo/redo, ne dépile pas l'historique), pas un undo. Retire
        ensuite l'entrée du dict pour cette page : un nouveau clic sur une
        profondeur recommencera un nouveau snapshot."""
        from modules.qt import state as _state_module
        from modules.qt.dialogs_qt import MsgDialog

        state = self.callbacks.get('state') or _state_module.state
        canvas = self.callbacks.get("canvas")

        original_bytes = state.color_depth_original_bytes_by_page.get(self.current_idx)
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

            del state.color_depth_original_bytes_by_page[self.current_idx]

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
            self._sync_color_depth_panel()

        except Exception as e:
            dlg = MsgDialog(self._center_parent, "messages.errors.color_depth_failed.title",
                            "messages.errors.color_depth_failed.message",
                            message_kwargs={"error": str(e)})
            dlg.show_nonmodal()

    def _sync_color_depth_panel(self):
        """Resynchronise les 5 radios sur l'état de la page COURANTE — appelé
        au changement de page, à la sélection de l'outil, après chaque commit/
        restauration, et après un undo/redo externe (Ctrl+Z ne vide PAS le
        snapshot, voir docstring de module : l'état visuel reste dérivé de la
        présence d'une entrée dans le dict, jamais de l'historique).

        locked_key (quel radio de profondeur apparaît coché+grisé) et
        pil_mode (mode PIL brut, pour la phrase d'info) sont tous deux
        retrouvés depuis le mode PIL RÉEL de l'image affichée (pas mémorisés
        séparément) : après un commit ou un undo/redo, c'est toujours le mode
        PIL courant qui fait foi de ce qui est "déjà appliqué"."""
        from modules.qt import state as _state_module
        state = self.callbacks.get('state') or _state_module.state
        panel = self._toolbar._color_depth_panel

        has_original_saved = self.current_idx in state.color_depth_original_bytes_by_page

        pil_mode = None
        locked_key = None
        blocked_keys: set[str] = set()
        blocked_format_label = ""
        if 0 <= self.current_idx < len(state.images_data):
            entry = state.images_data[self.current_idx]
            if entry.get('bytes'):
                try:
                    from PIL import Image
                    img = Image.open(io.BytesIO(entry['bytes']))
                    pil_mode = img.mode
                    locked_key = _PIL_TO_DEPTH.get(img.mode)
                except Exception:
                    pil_mode = None
                    locked_key = None
            ext = entry.get('extension', '')
            blocked = _BLOCKED_DEPTH_KEYS_BY_EXT.get(ext.lower())
            if blocked:
                blocked_keys = blocked
                blocked_format_label = ext.lstrip('.').upper()

        panel.sync_to_page_state(has_original_saved, locked_key, pil_mode,
                                  blocked_keys, blocked_format_label)
