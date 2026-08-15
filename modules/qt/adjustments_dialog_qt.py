"""
modules/qt/adjustments_dialog_qt.py — Ajustements d'images (version PySide6)

Reproduit le comportement de Modules_OLD/adjustments_dialog.py (tkinter).
Toutes les fenêtres supportent :
  - le thème courant (clair/sombre)
  - le changement de langue à la volée via language_signal
  - la police courante via get_current_font

Fonction publique :
  show_image_adjustments_dialog(callbacks=None)
"""

import io
import os

from PIL import Image, ImageOps

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QButtonGroup, QScrollArea, QWidget,
    QFrame, QGroupBox, QSizePolicy, QApplication,
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QImage, QIcon

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt import state as _state_module
from modules.qt.font_loader import resource_path
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.dialogs_qt import MsgDialog
from modules.qt.adjustments_processing_qt import (
    apply_adjustments      as _apply_adjustments,
    apply_image_adjustments as _apply_image_adjustments,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers langue / style
# ─────────────────────────────────────────────────────────────────────────────

def _connect_lang(dialog, handler):
    from modules.qt.language_signal import language_signal
    dialog._lang_handler = handler
    language_signal.changed.connect(dialog._lang_handler)
    dialog.finished.connect(lambda: _disconnect_lang(dialog))


def _disconnect_lang(dialog):
    from modules.qt.language_signal import language_signal
    try:
        language_signal.changed.disconnect(dialog._lang_handler)
    except RuntimeError:
        pass


def _btn_style(theme):
    return (
        f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
        f"border: 1px solid #aaaaaa; padding: 4px 12px; }} "
        f"QPushButton:hover {{ background: {theme['separator']}; }} "
        f"QPushButton:disabled {{ color: #888888; }}"
    )


def _groupbox_style(theme):
    return (
        f"QGroupBox {{ color: {theme['text']}; border: 1px solid {theme['separator']}; "
        f"border-radius: 4px; margin-top: 10px; margin-bottom: 4px; padding-top: 8px; padding-bottom: 6px; }} "
        f"QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}"
    )


def _set_groupbox_font(grp):
    """Applique la police courante en gras sur le titre d'un QGroupBox."""
    f = _get_current_font(10)
    f.setBold(True)
    grp.setFont(f)


def _radio_style(theme):
    disabled = theme.get('disabled', '#aaaaaa')
    return (f"QRadioButton {{ color: {theme['text']}; }} "
            f"QRadioButton:disabled {{ color: {disabled}; }}")




def _pil_to_qpixmap(img, max_size=300, is_bw=False):
    """Convertit une image PIL en QPixmap, redimensionnée à max_size en conservant le ratio."""
    img = img.copy()
    resample = Image.Resampling.NEAREST if is_bw else Image.Resampling.LANCZOS
    img.thumbnail((max_size, max_size), resample)
    buf = io.BytesIO()
    img.convert('RGBA').save(buf, format='PNG')
    buf.seek(0)
    pix = QPixmap()
    pix.loadFromData(buf.read())
    return pix


# ─────────────────────────────────────────────────────────────────────────────
# Dialogue principal
# ─────────────────────────────────────────────────────────────────────────────

class AdjustmentsDialog(QDialog):
    """Fenêtre d'ajustements d'images (profondeur de couleur, effets,
    mode d'image).

    La luminosité/contraste, la netteté (simple + adaptative), la saturation,
    la suppression des couleurs, la compression, les niveaux noir/blanc et la
    transparence ont été entièrement retirées de ce panneau (idees.txt #3,
    fusion progressive des visionneuses) : elles vivent désormais uniquement
    dans la barre d'outils flottante de la visionneuse principale — voir
    modules/qt/brightness_tool_qt.py, modules/qt/sharpness_tool_qt.py,
    modules/qt/saturation_tool_qt.py, modules/qt/remove_colors_tool_qt.py,
    modules/qt/compression_tool_qt.py, modules/qt/levels_tool_qt.py,
    modules/qt/transparency_tool_qt.py et skill viewers."""

    def __init__(self, parent, selected_entries, callbacks=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self._selected_entries = selected_entries
        self._callbacks = callbacks or {}
        self._preview_pixmap_ref = None   # anti-GC

        from modules.qt.overlay_tooltip_qt import OverlayTooltip
        self._overlay_tip = OverlayTooltip(self)

        ico_path = resource_path("icons/MosaicView.ico")
        if os.path.exists(ico_path):
            self.setWindowIcon(QIcon(ico_path))

        self.resize(1020, 900)

        # ── Image d'aperçu originale ──────────────────────────────────────────
        self._original_preview_img = None
        if selected_entries and selected_entries[0].get("bytes"):
            try:
                self._original_preview_img = Image.open(io.BytesIO(selected_entries[0]["bytes"]))
            except Exception:
                pass

        # ── Variables d'état (valeurs courantes des réglages) ─────────────────
        self._color_depth   = 'unchanged'
        self._effect        = 'none'
        self._image_mode       = 'unchanged'
        self._original_ext  = selected_entries[0].get('extension', '').lower() if selected_entries else ''

        # Flag d'annulation pendant le traitement multi-images
        self._cancel_requested = False

        # ── Construction UI ───────────────────────────────────────────────────
        self._build_ui()
        self._retranslate()
        self._disable_current_mode_radios()
        self._update_preview()

        _connect_lang(self, lambda _: self._retranslate())

        self._center_parent = parent

    # ─────────────────────────────────────────────────────────────────────────
    # Construction de l'UI
    # ─────────────────────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._preview_lbl = None   # garde contre les signaux prématurés des sliders

        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }} "
            f"QLabel {{ color: {theme['text']}; background: transparent; }} "
            f"QScrollArea {{ background: {theme['bg']}; border: none; }} "
            f"QWidget#scroll_content {{ background: {theme['bg']}; }}"
        )

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 8, 10, 8)
        root_layout.setSpacing(6)

        # Titre
        self._title_lbl = QLabel()
        font_title = _get_current_font(14)
        font_title.setBold(True)
        self._title_lbl.setFont(font_title)
        self._title_lbl.setAlignment(Qt.AlignCenter)
        root_layout.addWidget(self._title_lbl)

        # Zone scrollable contenant les 3 colonnes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: {theme['bg']}; }}")

        scroll_content = QWidget()
        scroll_content.setObjectName("scroll_content")
        cols_layout = QHBoxLayout(scroll_content)
        cols_layout.setContentsMargins(4, 4, 4, 4)
        cols_layout.setSpacing(10)
        cols_layout.setAlignment(Qt.AlignTop)

        # Les 3 colonnes
        left_col    = self._build_left_column(scroll_content)
        right_col   = self._build_right_column(scroll_content)
        preview_col = self._build_preview_column(scroll_content)

        cols_layout.addWidget(left_col,    stretch=1)
        cols_layout.addWidget(right_col,   stretch=1)
        cols_layout.addWidget(preview_col, stretch=0)

        scroll.setWidget(scroll_content)
        root_layout.addWidget(scroll, stretch=1)

        # Séparateur
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {get_current_theme()['separator']};")
        root_layout.addWidget(sep)

        # Label de progression
        self._progress_lbl = QLabel("")
        self._progress_lbl.setAlignment(Qt.AlignCenter)
        self._progress_lbl.setVisible(False)
        root_layout.addWidget(self._progress_lbl)

        # Boutons bas
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._btn_reset  = QPushButton()
        self._btn_apply  = QPushButton()
        self._btn_cancel = QPushButton()

        for btn in (self._btn_reset, self._btn_apply, self._btn_cancel):
            btn.setFont(_get_current_font(11))
            btn.setStyleSheet(_btn_style(get_current_theme()))
            btn.setFixedHeight(34)
            btn_row.addWidget(btn)

        btn_row.addStretch()
        root_layout.addLayout(btn_row)

        self._btn_reset.clicked.connect(self._on_reset)
        self._btn_apply.clicked.connect(self._on_apply)
        self._btn_cancel.clicked.connect(self._on_cancel)
        self._btn_apply.setDefault(True)

    # ── Colonne gauche ────────────────────────────────────────────────────────

    def _build_left_column(self, parent):
        theme = get_current_theme()
        w = QWidget(parent)
        w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(24)
        layout.setAlignment(Qt.AlignTop)

        # Section 1 : Profondeur de couleur
        self._grp_depth = QGroupBox()
        self._grp_depth.setStyleSheet(_groupbox_style(theme))
        depth_layout = QVBoxLayout(self._grp_depth)
        depth_layout.setContentsMargins(8, 12, 8, 8)
        depth_layout.setSpacing(4)

        self._depth_bg = QButtonGroup(self)
        self._depth_radios = {}
        for key in ('unchanged', '32', '24', '8', '1'):
            rb = QRadioButton()
            rb.setFont(_get_current_font(9))
            rb.setStyleSheet(_radio_style(theme))
            rb.setProperty('depth_key', key)
            self._depth_radios[key] = rb
            self._depth_bg.addButton(rb)
            depth_layout.addWidget(rb)
            rb.toggled.connect(lambda checked, k=key: self._on_depth_changed(k) if checked else None)
        self._depth_radios['unchanged'].setChecked(True)
        layout.addWidget(self._grp_depth)

        # Section 4 : Effets
        self._grp_effects = QGroupBox()
        self._grp_effects.setStyleSheet(_groupbox_style(theme))
        eff_layout = QVBoxLayout(self._grp_effects)
        eff_layout.setContentsMargins(8, 12, 8, 8)
        eff_layout.setSpacing(4)

        self._effect_bg = QButtonGroup(self)
        self._effect_radios = {}
        for key in ('none', 'grayscale', 'sepia', 'invert'):
            rb = QRadioButton()
            rb.setFont(_get_current_font(9))
            rb.setStyleSheet(_radio_style(theme))
            rb.setProperty('effect_key', key)
            self._effect_radios[key] = rb
            self._effect_bg.addButton(rb)
            eff_layout.addWidget(rb)
            rb.toggled.connect(lambda checked, k=key: self._on_effect_changed(k) if checked else None)
        self._effect_radios['none'].setChecked(True)
        # _grp_effects ajouté dans la colonne aperçu (3ème colonne)

        layout.addStretch()
        return w

    # ── Colonne droite ────────────────────────────────────────────────────────

    def _build_right_column(self, parent):
        theme = get_current_theme()
        w = QWidget(parent)
        w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(24)
        layout.setAlignment(Qt.AlignTop)

        # Section mode d'image (déplacée ici depuis colonne aperçu)
        self._grp_image_mode = QGroupBox()
        self._grp_image_mode.setStyleSheet(_groupbox_style(theme))
        mode_layout = QVBoxLayout(self._grp_image_mode)
        mode_layout.setContentsMargins(8, 12, 8, 8)
        mode_layout.setSpacing(4)

        self._mode_bg = QButtonGroup(self)
        self._mode_radios = {}
        for key in ('unchanged', 'RGB', 'RGBA', 'L', 'LA', 'CMYK', 'BW1', 'P'):
            rb = QRadioButton()
            rb.setFont(_get_current_font(9))
            rb.setStyleSheet(_radio_style(theme))
            rb.setProperty('mode_key', key)
            self._mode_radios[key] = rb
            self._mode_bg.addButton(rb)
            mode_layout.addWidget(rb)
            rb.toggled.connect(lambda checked, k=key: self._on_image_mode_changed(k) if checked else None)
        self._mode_radios['unchanged'].setChecked(True)
        layout.addWidget(self._grp_image_mode)

        layout.addStretch()
        return w

    # ── Colonne aperçu ────────────────────────────────────────────────────────

    def _build_preview_column(self, parent):
        theme = get_current_theme()
        w = QWidget(parent)
        w.setFixedWidth(330)
        w.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(24)
        layout.setAlignment(Qt.AlignTop)

        # Section aperçu
        self._grp_preview = QGroupBox()
        self._grp_preview.setStyleSheet(_groupbox_style(theme))
        prev_layout = QVBoxLayout(self._grp_preview)
        prev_layout.setContentsMargins(8, 12, 8, 8)
        prev_layout.setSpacing(4)

        self._preview_lbl = QLabel()
        self._preview_lbl.setFixedSize(300, 300)
        self._preview_lbl.setAlignment(Qt.AlignCenter)
        self._preview_lbl.setStyleSheet(
            f"background: {theme['canvas_bg']}; border: 1px solid {theme['separator']};")
        prev_layout.addWidget(self._preview_lbl, alignment=Qt.AlignCenter)

        self._preview_warn_lbl = QLabel()
        self._preview_warn_lbl.setFont(_get_current_font(8))
        self._preview_warn_lbl.setWordWrap(True)
        self._preview_warn_lbl.setAlignment(Qt.AlignCenter)
        self._preview_warn_lbl.setStyleSheet("color: #888888;")
        prev_layout.addWidget(self._preview_warn_lbl)
        layout.addWidget(self._grp_preview)

        # Section Effets (déplacée depuis colonne gauche)
        layout.addWidget(self._grp_effects)

        layout.addStretch()
        return w

    # ─────────────────────────────────────────────────────────────────────────
    # Traduction
    # ─────────────────────────────────────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        nb = len(self._selected_entries)
        word = _("dialogs.adjustments.word_image") if nb == 1 else _("dialogs.adjustments.word_images")
        self.setWindowTitle(_wt("dialogs.adjustments.window_title"))
        self._title_lbl.setText(_("dialogs.adjustments.title", count=nb, word=word))

        # Colonne gauche
        self._grp_depth.setTitle(_("dialogs.adjustments.color_depth_section"))
        depth_labels = {
            'unchanged': _("dialogs.adjustments.depth_unchanged"),
            '32':        _("dialogs.adjustments.depth_32bit"),
            '24':        _("dialogs.adjustments.depth_24bit"),
            '8':         _("dialogs.adjustments.depth_8bit"),
            '1':         _("dialogs.adjustments.depth_1bit"),
        }
        for key, rb in self._depth_radios.items():
            rb.setText(depth_labels[key])

        self._grp_effects.setTitle(_("dialogs.adjustments.effects_section"))
        effect_labels = {
            'none':      _("dialogs.adjustments.effect_none"),
            'grayscale': _("dialogs.adjustments.effect_grayscale"),
            'sepia':     _("dialogs.adjustments.effect_sepia"),
            'invert':    _("dialogs.adjustments.effect_invert"),
        }
        for key, rb in self._effect_radios.items():
            rb.setText(effect_labels[key])

        # Colonne aperçu
        self._grp_preview.setTitle(_("dialogs.adjustments.preview_section"))
        self._preview_warn_lbl.setText(_("dialogs.adjustments.preview_warning"))

        self._grp_image_mode.setTitle(_("dialogs.adjustments.image_mode_section"))
        mode_labels = {
            'unchanged': _("dialogs.adjustments.depth_unchanged"),
            'RGB':       _("dialogs.adjustments.image_mode_rgb"),
            'RGBA':      _("dialogs.adjustments.image_mode_rgba"),
            'L':         _("dialogs.adjustments.image_mode_l"),
            'LA':        _("dialogs.adjustments.image_mode_la"),
            'CMYK':      _("dialogs.adjustments.image_mode_cmyk"),
            'BW1':       _("dialogs.adjustments.image_mode_1"),
            'P':         _("dialogs.adjustments.image_mode_p"),
        }
        for key, rb in self._mode_radios.items():
            rb.setText(mode_labels[key])

        # Boutons bas
        self._btn_reset.setText(_("dialogs.adjustments.reset_button"))
        self._btn_apply.setText(_("dialogs.adjustments.apply_button"))
        self._btn_cancel.setText(_("buttons.cancel"))

        # Mise à jour des polices
        font_title = _get_current_font(14)
        font_title.setBold(True)
        self._title_lbl.setFont(font_title)
        font9 = _get_current_font(9)
        font8 = _get_current_font(8)
        font11 = _get_current_font(11)
        self._preview_warn_lbl.setFont(font8)
        for btn in (self._btn_reset, self._btn_apply, self._btn_cancel):
            btn.setFont(font11)
        self._progress_lbl.setFont(font11)
        self._progress_lbl.setStyleSheet("color: #cc0000; font-weight: bold;")
        for rb in list(self._depth_radios.values()) + list(self._effect_radios.values()) + list(self._mode_radios.values()):
            rb.setFont(font9)

        # Mise à jour des styles (thème peut avoir changé)
        self._apply_theme()

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }} "
            f"QLabel {{ color: {theme['text']}; background: transparent; }} "
            f"QScrollArea {{ background: {theme['bg']}; border: none; }} "
            f"QWidget#scroll_content {{ background: {theme['bg']}; }}"
        )
        grp_style = _groupbox_style(theme)
        for grp in (self._grp_depth,
                    self._grp_effects,
                    self._grp_preview, self._grp_image_mode):
            grp.setStyleSheet(grp_style)
            _set_groupbox_font(grp)
        btn_style = _btn_style(theme)
        for btn in (self._btn_reset, self._btn_apply,
                    self._btn_cancel):
            btn.setStyleSheet(btn_style)
        radio_style = _radio_style(theme)
        for rb in list(self._depth_radios.values()) + list(self._effect_radios.values()) + list(self._mode_radios.values()):
            rb.setStyleSheet(radio_style)
        self._preview_lbl.setStyleSheet(
            f"background: {theme['canvas_bg']}; border: 1px solid {theme['separator']};")
        self._overlay_tip.apply_theme()

    # ─────────────────────────────────────────────────────────────────────────
    # Handlers de changement de valeur
    # ─────────────────────────────────────────────────────────────────────────

    def _disable_current_mode_radios(self):
        """Désactive les radios correspondant au mode PIL actuel des images sélectionnées.
        Si toutes les images ont le même mode, le radio correspondant est désactivé."""
        # Correspondance mode PIL → clé depth et mode image
        PIL_TO_DEPTH = {'RGBA': '32', 'RGB': '24', 'L': '8', 'P': '8', '1': '1'}
        PIL_TO_MODE  = {'RGB': 'RGB', 'RGBA': 'RGBA', 'L': 'L', 'LA': 'LA',
                        'CMYK': 'CMYK', '1': 'BW1', 'P': 'P'}

        # Détecte les modes PIL de toutes les images sélectionnées
        modes = set()
        for entry in self._selected_entries:
            if not entry.get('bytes'):
                continue
            try:
                img = Image.open(io.BytesIO(entry['bytes']))
                modes.add(img.mode)
            except Exception:
                pass

        # Désactive uniquement si TOUTES les images ont le même mode
        if len(modes) == 1:
            pil_mode = next(iter(modes))
            depth_key = PIL_TO_DEPTH.get(pil_mode)
            mode_key  = PIL_TO_MODE.get(pil_mode)
            if depth_key and depth_key in self._depth_radios:
                self._depth_radios[depth_key].setEnabled(False)
            if mode_key and mode_key in self._mode_radios:
                self._mode_radios[mode_key].setEnabled(False)

    def _on_depth_changed(self, key):
        self._color_depth = key
        self._update_preview()

    def _on_effect_changed(self, key):
        self._effect = key
        self._update_preview()

    def _on_image_mode_changed(self, key):
        self._image_mode = key
        self._update_preview()

    # ─────────────────────────────────────────────────────────────────────────
    # Aperçu
    # ─────────────────────────────────────────────────────────────────────────

    def _get_settings(self):
        return {
            'color_depth':  self._color_depth,
            'effect':       self._effect,
            'image_mode':   self._image_mode,
            'original_ext': self._original_ext,
        }

    def _update_preview(self):
        if self._original_preview_img is None or self._preview_lbl is None:
            return
        try:
            settings = self._get_settings()
            result = _apply_adjustments(self._original_preview_img.copy(), settings, for_preview=True)
            original_is_bw = self._original_preview_img.mode == '1'
            is_bw = (settings.get('color_depth') == '1' or settings.get('image_mode') == 'BW1'
                     or (settings.get('color_depth') == 'unchanged' and settings.get('image_mode') == 'unchanged' and original_is_bw))
            original = self._original_preview_img
            original_has_alpha = (original.mode in ('RGBA', 'LA') or
                                  (original.mode == 'P' and 'transparency' in original.info))
            if original_has_alpha:
                from modules.qt.entries import _make_checkerboard_pil
                # Resize d'abord, damier ensuite (tile fixe indépendant de la taille source)
                resample = Image.Resampling.NEAREST if is_bw else Image.Resampling.LANCZOS
                if result.mode != 'RGBA':
                    alpha = original.convert('RGBA').split()[3]
                    rgba = result.convert('RGB').convert('RGBA')
                    rgba.putalpha(alpha)
                    result = rgba
                result.thumbnail((300, 300), resample)
                bg = _make_checkerboard_pil(result.width, result.height, tile=10)
                bg.paste(result, (0, 0), result)
                result = bg
            pix = _pil_to_qpixmap(result, max_size=300, is_bw=is_bw)
            self._preview_pixmap_ref = pix
            self._preview_lbl.setPixmap(pix)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────────────────────────────────────

    def _on_reset(self):
        """Réinitialise tous les contrôles à leurs valeurs par défaut."""
        self._color_depth        = 'unchanged'
        self._effect             = 'none'
        self._image_mode         = 'unchanged'

        self._depth_radios['unchanged'].setChecked(True)
        self._effect_radios['none'].setChecked(True)
        self._mode_radios['unchanged'].setChecked(True)

        self._retranslate()
        self._update_preview()

    def _on_cancel(self):
        """Annuler : si traitement en cours, demande l'arrêt ; sinon ferme normalement."""
        if self._cancel_requested is not None and self._cancel_requested is False and \
                self._progress_lbl.isVisible():
            # Traitement en cours → demande d'annulation
            self._cancel_requested = True
        else:
            self.close()

    def _on_apply(self):
        """Applique les ajustements aux images sélectionnées et ferme."""
        n = len(self._selected_entries)
        multi = n > 1

        self._cancel_requested = False
        self._progress_lbl.setText(_("labels.adjusting", current=1, total=n))
        self._progress_lbl.setVisible(True)
        QApplication.processEvents()

        # Callbacks sans save_state pour la boucle (on gère le undo manuellement)
        save_state = self._callbacks.get('save_state')
        render     = self._callbacks.get('render_mosaic')
        callbacks_no_save = dict(self._callbacks)
        callbacks_no_save.pop('save_state', None)
        callbacks_no_save.pop('render_mosaic', None)

        if multi:
            # Snapshot des bytes originaux pour restauration en cas d'annulation
            # (avant tout save_state, pour ne pas polluer le stack undo si annulé)
            orig_bytes    = {id(e): e.get('bytes') for e in self._selected_entries}
            orig_thumbs   = {id(e): {k: e.get(k) for k in
                             ('img', '_thumbnail', 'large_thumb_pil',
                              'qt_pixmap_large', 'qt_qimage_large')}
                             for e in self._selected_entries}
            processed = []

            for i, entry in enumerate(self._selected_entries, 1):
                if self._cancel_requested:
                    break
                self._progress_lbl.setText(_("labels.adjusting", current=i, total=n))
                QApplication.processEvents()
                _apply_image_adjustments([entry], self._get_settings(),
                                         callbacks=callbacks_no_save)
                processed.append(entry)

            if self._cancel_requested:
                # Restaurer les bytes et thumbnails des images déjà modifiées
                for entry in processed:
                    eid = id(entry)
                    ob = orig_bytes.get(eid)
                    if ob is not None:
                        entry['bytes'] = ob
                        entry['_hash'] = None
                    for k, v in orig_thumbs.get(eid, {}).items():
                        entry[k] = v
                if render:
                    render()
                self._progress_lbl.setVisible(False)
                self._cancel_requested = False
                return  # Reste ouvert — aucun save_state n'a été fait

            # Tout traité sans annulation → save undo+redo + render
            (self._callbacks.get('state') or _state_module.state).modified = True
            if save_state:
                save_state()
                save_state(force=True)
            if render:
                render()
        else:
            _apply_image_adjustments(self._selected_entries, self._get_settings(),
                                     callbacks=self._callbacks)

        self._progress_lbl.setVisible(False)
        self.close()


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée public
# ─────────────────────────────────────────────────────────────────────────────

def show_image_adjustments_dialog(parent=None, callbacks=None):
    """Ouvre le dialogue d'ajustements d'images.

    parent    : widget Qt parent (MainWindow)
    callbacks : dict avec save_state, render_mosaic, etc.
    """
    callbacks = callbacks or {}
    state = callbacks.get('state') or _state_module.state

    if not state.selected_indices:
        MsgDialog(
            parent,
            "messages.warnings.no_selection_adjust.title",
            "messages.warnings.no_selection_adjust.message",
        ).show_nonmodal()
        return

    selected_entries = [
        state.images_data[i]
        for i in sorted(state.selected_indices)
        if i < len(state.images_data) and state.images_data[i]["is_image"]
    ]

    if not selected_entries:
        MsgDialog(
            parent,
            "messages.warnings.invalid_selection_adjust.title",
            "messages.warnings.invalid_selection_adjust.message",
        ).show_nonmodal()
        return

    dlg = AdjustmentsDialog(parent, selected_entries, callbacks=callbacks)
    from modules.qt.dialogs_qt import position_dialog_on_parent
    position_dialog_on_parent(dlg, parent)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
