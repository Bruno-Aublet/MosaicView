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
from modules.qt.utils import FocusSlider
from modules.qt.state import get_current_theme
from modules.qt import state as _state_module
from modules.qt.font_loader import resource_path
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.dialogs_qt import MsgDialog
from modules.qt.adjustments_processing_qt import (
    detect_jpeg_quality    as _detect_jpeg_quality,
    apply_adjustments      as _apply_adjustments,
    apply_image_adjustments as _apply_image_adjustments,
    compute_auto_levels    as _compute_auto_levels,
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


def _slider_style(theme):
    return (
        f"QSlider::groove:horizontal {{ height: 4px; background: {theme['separator']}; "
        f"border-radius: 2px; }} "
        f"QSlider::handle:horizontal {{ background: {theme['text']}; width: 12px; height: 12px; "
        f"border-radius: 6px; margin: -4px 0; }} "
        f"QSlider::handle:horizontal:disabled {{ background: #888888; }}"
    )


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
    """Fenêtre d'ajustements d'images (luminosité, contraste, niveaux, etc.)."""

    def __init__(self, parent, selected_entries, callbacks=None):
        super().__init__(parent)
        self.setModal(True)
        self._selected_entries = selected_entries
        self._callbacks = callbacks or {}
        self._preview_pixmap_ref = None   # anti-GC

        from modules.qt.overlay_tooltip_qt import OverlayTooltip
        self._overlay_tip = OverlayTooltip(self)

        ico_path = resource_path("icons/MosaicView.ico")
        if os.path.exists(ico_path):
            self.setWindowIcon(QIcon(ico_path))

        self.resize(1020, 900)

        # ── Détection qualité JPEG ────────────────────────────────────────────
        jpeg_qualities = []
        self._has_compressible = False
        self._has_transparent  = False
        for e in selected_entries:
            ext = e.get("extension", "").lower()
            if ext in (".jpg", ".jpeg", ".webp"):
                self._has_compressible = True
            if ext in (".png", ".webp", ".ico"):
                self._has_transparent = True
            if e.get("bytes"):
                q = _detect_jpeg_quality(e["bytes"])
                if q is not None:
                    jpeg_qualities.append(q)

        jpeg_qualities.sort()
        initial_quality = jpeg_qualities[len(jpeg_qualities) // 2] if jpeg_qualities else 85

        # ── Image d'aperçu originale ──────────────────────────────────────────
        self._original_preview_img = None
        if selected_entries and selected_entries[0].get("bytes"):
            try:
                self._original_preview_img = Image.open(io.BytesIO(selected_entries[0]["bytes"]))
            except Exception:
                pass

        # ── Variables d'état (valeurs courantes des réglages) ─────────────────
        self._color_depth   = 'unchanged'
        self._brightness    = 0
        self._contrast      = 0
        self._comp_quality  = initial_quality
        self._initial_quality = initial_quality
        self._effect        = 'none'
        self._sharpness     = 0
        self._threshold     = 128
        self._black_point   = 0
        self._gamma         = 1.0
        self._white_point   = 255
        self._remove_int       = 0
        self._saturation       = 0
        self._image_mode       = 'unchanged'
        self._unsharp_radius   = 2.0
        self._unsharp_percent  = 0
        self._unsharp_threshold = 3
        self._original_ext  = selected_entries[0].get('extension', '').lower() if selected_entries else ''

        # Sauvegarde avant "Auto" (pour que Annuler puisse restaurer)
        self._pre_auto_black_point = None
        self._pre_auto_white_point = None

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

        # Section 2 : Compression
        self._grp_comp = QGroupBox()
        self._grp_comp.setStyleSheet(_groupbox_style(theme))
        comp_layout = QVBoxLayout(self._grp_comp)
        comp_layout.setContentsMargins(8, 12, 8, 8)
        comp_layout.setSpacing(4)

        self._comp_info_lbl = QLabel()
        self._comp_info_lbl.setFont(_get_current_font(8))
        self._comp_info_lbl.setWordWrap(True)
        self._comp_info_lbl.setAlignment(Qt.AlignCenter)
        self._comp_info_lbl.setStyleSheet(f"color: #888888; font-style: italic;")
        comp_layout.addWidget(self._comp_info_lbl)

        self._comp_val_lbl = QLabel()
        self._comp_val_lbl.setFont(_get_current_font(9))
        comp_layout.addWidget(self._comp_val_lbl)

        self._comp_slider = FocusSlider(Qt.Horizontal)
        self._comp_slider.setRange(1, 100)
        self._comp_slider.setValue(self._comp_quality)
        self._comp_slider.setStyleSheet(_slider_style(theme))
        self._comp_slider.valueChanged.connect(self._on_comp_changed)
        comp_layout.addWidget(self._comp_slider)

        self._btn_comp_viewer = QPushButton()
        self._btn_comp_viewer.setFont(_get_current_font(9))
        self._btn_comp_viewer.setStyleSheet(_btn_style(theme))
        self._btn_comp_viewer.clicked.connect(lambda: self._open_viewer('compression'))
        comp_layout.addWidget(self._btn_comp_viewer, alignment=Qt.AlignCenter)

        if not self._has_compressible:
            self._comp_val_lbl.setEnabled(False)
            self._comp_slider.setEnabled(False)
            self._btn_comp_viewer.setEnabled(False)
            self._grp_comp.setStyleSheet(
                _groupbox_style(theme).replace(f"color: {theme['text']}", "color: #888888"))

        layout.addWidget(self._grp_comp)

        # Section 3 : Netteté
        self._grp_sharp = QGroupBox()
        self._grp_sharp.setStyleSheet(_groupbox_style(theme))
        sharp_layout = QVBoxLayout(self._grp_sharp)
        sharp_layout.setContentsMargins(8, 12, 8, 8)
        sharp_layout.setSpacing(4)

        self._sharp_val_lbl = QLabel()
        self._sharp_val_lbl.setFont(_get_current_font(9))
        sharp_layout.addWidget(self._sharp_val_lbl)

        self._sharp_slider = FocusSlider(Qt.Horizontal)
        self._sharp_slider.setRange(-100, 100)
        self._sharp_slider.setValue(0)
        self._sharp_slider.setStyleSheet(_slider_style(theme))
        self._sharp_slider.valueChanged.connect(self._on_sharp_changed)
        sharp_layout.addWidget(self._sharp_slider)

        self._btn_sharp_viewer = QPushButton()
        self._btn_sharp_viewer.setFont(_get_current_font(9))
        self._btn_sharp_viewer.setStyleSheet(_btn_style(theme))
        self._btn_sharp_viewer.clicked.connect(lambda: self._open_viewer('sharpness'))
        sharp_layout.addWidget(self._btn_sharp_viewer, alignment=Qt.AlignCenter)
        layout.addWidget(self._grp_sharp)

        # Section 3b : Netteté adaptative (Unsharp Mask)
        self._grp_unsharp = QGroupBox()
        self._grp_unsharp.setStyleSheet(_groupbox_style(theme))
        unsharp_layout = QVBoxLayout(self._grp_unsharp)
        unsharp_layout.setContentsMargins(8, 12, 8, 8)
        unsharp_layout.setSpacing(4)

        self._unsharp_radius_lbl = QLabel()
        self._unsharp_radius_lbl.setFont(_get_current_font(9))
        unsharp_layout.addWidget(self._unsharp_radius_lbl)

        self._unsharp_radius_slider = FocusSlider(Qt.Horizontal)
        self._unsharp_radius_slider.setRange(5, 50)   # ×0.1 → 0.5 à 5.0
        self._unsharp_radius_slider.setValue(20)       # défaut 2.0
        self._unsharp_radius_slider.setStyleSheet(_slider_style(theme))
        self._unsharp_radius_slider.valueChanged.connect(self._on_unsharp_radius_changed)
        unsharp_layout.addWidget(self._unsharp_radius_slider)

        self._unsharp_percent_lbl = QLabel()
        self._unsharp_percent_lbl.setFont(_get_current_font(9))
        unsharp_layout.addWidget(self._unsharp_percent_lbl)

        self._unsharp_percent_slider = FocusSlider(Qt.Horizontal)
        self._unsharp_percent_slider.setRange(0, 200)
        self._unsharp_percent_slider.setValue(0)
        self._unsharp_percent_slider.setStyleSheet(_slider_style(theme))
        self._unsharp_percent_slider.valueChanged.connect(self._on_unsharp_percent_changed)
        unsharp_layout.addWidget(self._unsharp_percent_slider)

        self._unsharp_threshold_lbl = QLabel()
        self._unsharp_threshold_lbl.setFont(_get_current_font(9))
        unsharp_layout.addWidget(self._unsharp_threshold_lbl)

        self._unsharp_threshold_slider = FocusSlider(Qt.Horizontal)
        self._unsharp_threshold_slider.setRange(0, 30)
        self._unsharp_threshold_slider.setValue(3)
        self._unsharp_threshold_slider.setStyleSheet(_slider_style(theme))
        self._unsharp_threshold_slider.valueChanged.connect(self._on_unsharp_threshold_changed)
        unsharp_layout.addWidget(self._unsharp_threshold_slider)

        self._btn_unsharp_viewer = QPushButton()
        self._btn_unsharp_viewer.setFont(_get_current_font(9))
        self._btn_unsharp_viewer.setStyleSheet(_btn_style(theme))
        self._btn_unsharp_viewer.clicked.connect(lambda: self._open_viewer('unsharp'))
        unsharp_layout.addWidget(self._btn_unsharp_viewer, alignment=Qt.AlignCenter)
        layout.addWidget(self._grp_unsharp)

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

        # Section 5 : Transparence
        self._grp_transp = QGroupBox()
        self._grp_transp.setStyleSheet(_groupbox_style(theme))
        transp_layout = QVBoxLayout(self._grp_transp)
        transp_layout.setContentsMargins(8, 12, 8, 8)
        transp_layout.setSpacing(4)

        self._transp_info_lbl = QLabel()
        self._transp_info_lbl.setFont(_get_current_font(8))
        self._transp_info_lbl.setWordWrap(True)
        self._transp_info_lbl.setAlignment(Qt.AlignCenter)
        self._transp_info_lbl.setStyleSheet("color: #888888; font-style: italic;")
        transp_layout.addWidget(self._transp_info_lbl)

        self._btn_transp_viewer = QPushButton()
        self._btn_transp_viewer.setFont(_get_current_font(9))
        self._btn_transp_viewer.setStyleSheet(_btn_style(theme))
        self._btn_transp_viewer.clicked.connect(lambda: self._open_viewer('transparency'))
        transp_layout.addWidget(self._btn_transp_viewer, alignment=Qt.AlignCenter)

        if not self._has_transparent:
            self._btn_transp_viewer.setEnabled(False)
            self._grp_transp.setStyleSheet(
                _groupbox_style(theme).replace(f"color: {theme['text']}", "color: #888888"))

        layout.addWidget(self._grp_transp)
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

        # Section 5 : Luminosité / Contraste
        self._grp_bc = QGroupBox()
        self._grp_bc.setStyleSheet(_groupbox_style(theme))
        bc_layout = QVBoxLayout(self._grp_bc)
        bc_layout.setContentsMargins(8, 12, 8, 8)
        bc_layout.setSpacing(4)

        self._bright_val_lbl = QLabel()
        self._bright_val_lbl.setFont(_get_current_font(9))
        bc_layout.addWidget(self._bright_val_lbl)

        self._bright_slider = FocusSlider(Qt.Horizontal)
        self._bright_slider.setRange(-100, 100)
        self._bright_slider.setValue(0)
        self._bright_slider.setStyleSheet(_slider_style(theme))
        self._bright_slider.valueChanged.connect(self._on_bright_changed)
        bc_layout.addWidget(self._bright_slider)

        self._contrast_val_lbl = QLabel()
        self._contrast_val_lbl.setFont(_get_current_font(9))
        bc_layout.addWidget(self._contrast_val_lbl)

        self._contrast_slider = FocusSlider(Qt.Horizontal)
        self._contrast_slider.setRange(-100, 100)
        self._contrast_slider.setValue(0)
        self._contrast_slider.setStyleSheet(_slider_style(theme))
        self._contrast_slider.valueChanged.connect(self._on_contrast_changed)
        bc_layout.addWidget(self._contrast_slider)

        self._btn_bright_viewer = QPushButton()
        self._btn_bright_viewer.setFont(_get_current_font(9))
        self._btn_bright_viewer.setStyleSheet(_btn_style(theme))
        self._btn_bright_viewer.clicked.connect(lambda: self._open_viewer('brightness'))
        bc_layout.addWidget(self._btn_bright_viewer, alignment=Qt.AlignCenter)
        layout.addWidget(self._grp_bc)

        # Section 6 : Niveaux
        self._grp_levels = QGroupBox()
        self._grp_levels.setStyleSheet(_groupbox_style(theme))
        lev_layout = QVBoxLayout(self._grp_levels)
        lev_layout.setContentsMargins(8, 12, 8, 10)
        lev_layout.setSpacing(6)

        # Seuil
        self._threshold_lbl = QLabel()
        self._threshold_lbl.setFont(_get_current_font(9))
        lev_layout.addWidget(self._threshold_lbl)

        self._threshold_slider = FocusSlider(Qt.Horizontal)
        self._threshold_slider.setRange(0, 255)
        self._threshold_slider.setValue(128)
        self._threshold_slider.setStyleSheet(_slider_style(theme))
        self._threshold_slider.valueChanged.connect(self._on_threshold_changed)
        lev_layout.addWidget(self._threshold_slider)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {theme['separator']};")
        lev_layout.addWidget(sep)

        # Point noir
        self._black_pt_lbl = QLabel()
        self._black_pt_lbl.setFont(_get_current_font(9))
        lev_layout.addWidget(self._black_pt_lbl)

        self._black_pt_slider = FocusSlider(Qt.Horizontal)
        self._black_pt_slider.setRange(0, 255)
        self._black_pt_slider.setValue(0)
        self._black_pt_slider.setStyleSheet(_slider_style(theme))
        self._black_pt_slider.valueChanged.connect(self._on_black_pt_changed)
        lev_layout.addWidget(self._black_pt_slider)
        lev_layout.addSpacing(6)

        # Gamma
        self._gamma_lbl = QLabel()
        self._gamma_lbl.setFont(_get_current_font(9))
        lev_layout.addWidget(self._gamma_lbl)

        self._gamma_slider = FocusSlider(Qt.Horizontal)
        self._gamma_slider.setRange(10, 300)   # ×0.01 → 0.10 à 3.00
        self._gamma_slider.setValue(100)
        self._gamma_slider.setStyleSheet(_slider_style(theme))
        self._gamma_slider.valueChanged.connect(self._on_gamma_changed)
        lev_layout.addWidget(self._gamma_slider)
        lev_layout.addSpacing(6)

        # Point blanc
        self._white_pt_lbl = QLabel()
        self._white_pt_lbl.setFont(_get_current_font(9))
        lev_layout.addWidget(self._white_pt_lbl)

        self._white_pt_slider = FocusSlider(Qt.Horizontal)
        self._white_pt_slider.setRange(0, 255)
        self._white_pt_slider.setValue(255)
        self._white_pt_slider.setStyleSheet(_slider_style(theme))
        self._white_pt_slider.valueChanged.connect(self._on_white_pt_changed)
        lev_layout.addWidget(self._white_pt_slider)

        self._btn_auto_levels = QPushButton()
        self._btn_auto_levels.setFont(_get_current_font(9))
        self._btn_auto_levels.setStyleSheet(_btn_style(theme))
        self._btn_auto_levels.clicked.connect(self._on_auto_levels)
        lev_layout.addWidget(self._btn_auto_levels, alignment=Qt.AlignCenter)
        self._overlay_tip.track(self._btn_auto_levels)

        self._btn_levels_viewer = QPushButton()
        self._btn_levels_viewer.setFont(_get_current_font(9))
        self._btn_levels_viewer.setStyleSheet(_btn_style(theme))
        self._btn_levels_viewer.clicked.connect(lambda: self._open_viewer('levels'))
        lev_layout.addWidget(self._btn_levels_viewer, alignment=Qt.AlignCenter)
        layout.addWidget(self._grp_levels)

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

        # Section suppression des couleurs
        self._grp_remove_colors = QGroupBox()
        self._grp_remove_colors.setStyleSheet(_groupbox_style(theme))
        rc_layout = QVBoxLayout(self._grp_remove_colors)
        rc_layout.setContentsMargins(8, 12, 8, 8)
        rc_layout.setSpacing(4)

        self._remove_colors_val_lbl = QLabel()
        self._remove_colors_val_lbl.setFont(_get_current_font(9))
        rc_layout.addWidget(self._remove_colors_val_lbl)

        self._remove_colors_slider = FocusSlider(Qt.Horizontal)
        self._remove_colors_slider.setRange(0, 100)
        self._remove_colors_slider.setValue(0)
        self._remove_colors_slider.setStyleSheet(_slider_style(theme))
        self._remove_colors_slider.valueChanged.connect(self._on_remove_colors_changed)
        rc_layout.addWidget(self._remove_colors_slider)

        self._btn_remove_colors_viewer = QPushButton()
        self._btn_remove_colors_viewer.setFont(_get_current_font(9))
        self._btn_remove_colors_viewer.setStyleSheet(_btn_style(theme))
        self._btn_remove_colors_viewer.clicked.connect(lambda: self._open_viewer('remove_colors'))
        rc_layout.addWidget(self._btn_remove_colors_viewer, alignment=Qt.AlignCenter)
        layout.addWidget(self._grp_remove_colors)

        # Section saturation (déplacée ici depuis colonne droite)
        self._grp_sat = QGroupBox()
        self._grp_sat.setStyleSheet(_groupbox_style(theme))
        sat_layout = QVBoxLayout(self._grp_sat)
        sat_layout.setContentsMargins(8, 12, 8, 8)
        sat_layout.setSpacing(4)

        self._sat_val_lbl = QLabel()
        self._sat_val_lbl.setFont(_get_current_font(9))
        sat_layout.addWidget(self._sat_val_lbl)

        self._sat_slider = FocusSlider(Qt.Horizontal)
        self._sat_slider.setRange(-100, 100)
        self._sat_slider.setValue(0)
        self._sat_slider.setStyleSheet(_slider_style(theme))
        self._sat_slider.valueChanged.connect(self._on_sat_changed)
        sat_layout.addWidget(self._sat_slider)

        self._btn_sat_viewer = QPushButton()
        self._btn_sat_viewer.setFont(_get_current_font(9))
        self._btn_sat_viewer.setStyleSheet(_btn_style(theme))
        self._btn_sat_viewer.clicked.connect(lambda: self._open_viewer('saturation'))
        sat_layout.addWidget(self._btn_sat_viewer, alignment=Qt.AlignCenter)
        layout.addWidget(self._grp_sat)

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

        self._grp_comp.setTitle(_("dialogs.adjustments.compression_section"))
        self._comp_info_lbl.setText(_("dialogs.adjustments.compression_info"))
        self._comp_val_lbl.setText(
            _("dialogs.adjustments.compression_quality_label", value=self._comp_quality))
        self._btn_comp_viewer.setText(_("dialogs.adjustments.open_viewer_button_simple"))

        self._grp_sharp.setTitle(_("dialogs.adjustments.sharpness_section"))
        self._sharp_val_lbl.setText(
            _("dialogs.adjustments.sharpness_label", value=self._sharpness))
        self._btn_sharp_viewer.setText(_("dialogs.adjustments.open_viewer_button_simple"))

        self._grp_unsharp.setTitle(_("dialogs.adjustments.unsharp_section"))
        self._unsharp_radius_lbl.setText(
            _("dialogs.adjustments.unsharp_radius_label", value=self._unsharp_radius))
        self._unsharp_percent_lbl.setText(
            _("dialogs.adjustments.unsharp_percent_label", value=self._unsharp_percent))
        self._unsharp_threshold_lbl.setText(
            _("dialogs.adjustments.unsharp_threshold_label", value=self._unsharp_threshold))
        self._btn_unsharp_viewer.setText(_("dialogs.adjustments.open_viewer_button_simple"))

        self._grp_effects.setTitle(_("dialogs.adjustments.effects_section"))
        effect_labels = {
            'none':      _("dialogs.adjustments.effect_none"),
            'grayscale': _("dialogs.adjustments.effect_grayscale"),
            'sepia':     _("dialogs.adjustments.effect_sepia"),
            'invert':    _("dialogs.adjustments.effect_invert"),
        }
        for key, rb in self._effect_radios.items():
            rb.setText(effect_labels[key])

        self._grp_transp.setTitle(_("dialogs.adjustments.transparency_section"))
        self._transp_info_lbl.setText(_("dialogs.adjustments.transparency_info"))
        self._btn_transp_viewer.setText(_("dialogs.adjustments.open_viewer_button_simple"))

        # Colonne droite
        self._grp_bc.setTitle(_("dialogs.adjustments.brightness_contrast_section"))
        self._bright_val_lbl.setText(
            _("dialogs.adjustments.brightness_label", value=self._brightness))
        self._contrast_val_lbl.setText(
            _("dialogs.adjustments.contrast_label", value=self._contrast))
        self._btn_bright_viewer.setText(_("dialogs.adjustments.open_viewer_button_simple"))

        self._grp_levels.setTitle(_("dialogs.adjustments.levels_section"))
        self._threshold_lbl.setText(
            _("dialogs.adjustments.levels_threshold") + f" : {self._threshold}")
        self._black_pt_lbl.setText(
            _("dialogs.adjustments.black_point_label", value=self._black_point))
        self._gamma_lbl.setText(
            _("dialogs.adjustments.gamma_label", value=round(self._gamma, 2)))
        self._white_pt_lbl.setText(
            _("dialogs.adjustments.white_point_label", value=self._white_point))
        self._btn_auto_levels.setText(_("dialogs.adjustments.auto_levels_button"))
        import html as _html
        tip_text = _("dialogs.adjustments.auto_levels_tooltip")
        tip_html = f'<table style="max-width:340px;white-space:normal;"><tr><td>{_html.escape(tip_text).replace(chr(10), "<br>")}</td></tr></table>'
        self._overlay_tip.set_tracked_html(tip_html, self._btn_auto_levels)
        self._btn_levels_viewer.setText(_("dialogs.adjustments.open_viewer_button"))

        self._grp_sat.setTitle(_("dialogs.adjustments.saturation_section"))
        self._sat_val_lbl.setText(
            _("dialogs.adjustments.saturation_label", value=self._saturation))
        self._btn_sat_viewer.setText(_("dialogs.adjustments.open_viewer_button_simple"))

        # Colonne aperçu
        self._grp_preview.setTitle(_("dialogs.adjustments.preview_section"))
        self._preview_warn_lbl.setText(_("dialogs.adjustments.preview_warning"))

        self._grp_remove_colors.setTitle(_("dialogs.adjustments.effect_remove_colors"))
        self._remove_colors_val_lbl.setText(
            _("dialogs.adjustments.remove_colors_intensity_label", value=self._remove_int))
        self._btn_remove_colors_viewer.setText(_("dialogs.adjustments.open_viewer_button_simple"))

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
        for lbl in (self._comp_val_lbl, self._sharp_val_lbl,
                    self._bright_val_lbl, self._contrast_val_lbl,
                    self._threshold_lbl, self._black_pt_lbl, self._gamma_lbl,
                    self._white_pt_lbl, self._sat_val_lbl,
                    self._remove_colors_val_lbl,
                    self._unsharp_radius_lbl, self._unsharp_percent_lbl,
                    self._unsharp_threshold_lbl):
            lbl.setFont(font9)
        for lbl in (self._comp_info_lbl, self._transp_info_lbl, self._preview_warn_lbl):
            lbl.setFont(font8)
        for btn in (self._btn_reset, self._btn_apply, self._btn_cancel):
            btn.setFont(font11)
        self._progress_lbl.setFont(font11)
        self._progress_lbl.setStyleSheet("color: #cc0000; font-weight: bold;")
        for btn in (self._btn_comp_viewer, self._btn_sharp_viewer, self._btn_unsharp_viewer,
                    self._btn_transp_viewer, self._btn_bright_viewer,
                    self._btn_auto_levels, self._btn_levels_viewer,
                    self._btn_sat_viewer, self._btn_remove_colors_viewer):
            btn.setFont(font9)
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
        for grp in (self._grp_depth, self._grp_comp, self._grp_sharp, self._grp_unsharp,
                    self._grp_effects, self._grp_transp, self._grp_bc,
                    self._grp_levels, self._grp_sat, self._grp_preview,
                    self._grp_remove_colors, self._grp_image_mode):
            grp.setStyleSheet(grp_style)
            _set_groupbox_font(grp)
        # Désactivés en gris
        if not self._has_compressible:
            self._grp_comp.setStyleSheet(grp_style.replace(
                f"color: {theme['text']}", "color: #888888"))
        if not self._has_transparent:
            self._grp_transp.setStyleSheet(grp_style.replace(
                f"color: {theme['text']}", "color: #888888"))
        slider_style = _slider_style(theme)
        for sl in (self._comp_slider, self._sharp_slider, self._bright_slider,
                   self._contrast_slider, self._threshold_slider,
                   self._black_pt_slider, self._gamma_slider, self._white_pt_slider,
                   self._sat_slider, self._remove_colors_slider,
                   self._unsharp_radius_slider, self._unsharp_percent_slider,
                   self._unsharp_threshold_slider):
            sl.setStyleSheet(slider_style)
        btn_style = _btn_style(theme)
        for btn in (self._btn_comp_viewer, self._btn_sharp_viewer, self._btn_unsharp_viewer,
                    self._btn_bright_viewer, self._btn_auto_levels, self._btn_levels_viewer,
                    self._btn_sat_viewer, self._btn_remove_colors_viewer,
                    self._btn_transp_viewer, self._btn_reset, self._btn_apply,
                    self._btn_cancel):
            btn.setStyleSheet(btn_style)
        radio_style = _radio_style(theme)
        for rb in list(self._depth_radios.values()) + list(self._effect_radios.values()) + list(self._mode_radios.values()):
            rb.setStyleSheet(radio_style)
        self._preview_lbl.setStyleSheet(
            f"background: {theme['canvas_bg']}; border: 1px solid {theme['separator']};")

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

    def _on_comp_changed(self, val):
        self._comp_quality = val
        self._comp_val_lbl.setText(
            _("dialogs.adjustments.compression_quality_label", value=val))
        self._update_preview()

    def _on_sharp_changed(self, val):
        self._sharpness = val
        self._sharp_val_lbl.setText(
            _("dialogs.adjustments.sharpness_label", value=val))
        self._update_preview()

    def _on_effect_changed(self, key):
        self._effect = key
        self._update_preview()

    def _on_bright_changed(self, val):
        self._brightness = val
        self._bright_val_lbl.setText(
            _("dialogs.adjustments.brightness_label", value=val))
        self._update_preview()

    def _on_contrast_changed(self, val):
        self._contrast = val
        self._contrast_val_lbl.setText(
            _("dialogs.adjustments.contrast_label", value=val))
        self._update_preview()

    def _on_threshold_changed(self, val):
        self._threshold = val
        self._threshold_lbl.setText(
            _("dialogs.adjustments.levels_threshold") + f" : {val}")
        self._update_preview()

    def _on_black_pt_changed(self, val):
        self._black_point = val
        self._black_pt_lbl.setText(
            _("dialogs.adjustments.black_point_label", value=val))
        self._update_preview()

    def _on_gamma_changed(self, val):
        self._gamma = round(val / 100.0, 2)
        self._gamma_lbl.setText(
            _("dialogs.adjustments.gamma_label", value=self._gamma))
        self._update_preview()

    def _on_white_pt_changed(self, val):
        self._white_point = val
        self._white_pt_lbl.setText(
            _("dialogs.adjustments.white_point_label", value=val))
        self._update_preview()

    def _on_sat_changed(self, val):
        self._saturation = val
        self._sat_val_lbl.setText(
            _("dialogs.adjustments.saturation_label", value=val))
        self._update_preview()

    def _on_remove_colors_changed(self, val):
        self._remove_int = val
        self._remove_colors_val_lbl.setText(
            _("dialogs.adjustments.remove_colors_intensity_label", value=val))
        self._update_preview()

    def _on_unsharp_radius_changed(self, val):
        self._unsharp_radius = round(val / 10.0, 1)
        self._unsharp_radius_lbl.setText(
            _("dialogs.adjustments.unsharp_radius_label", value=self._unsharp_radius))
        self._update_preview()

    def _on_unsharp_percent_changed(self, val):
        self._unsharp_percent = val
        self._unsharp_percent_lbl.setText(
            _("dialogs.adjustments.unsharp_percent_label", value=val))
        self._update_preview()

    def _on_unsharp_threshold_changed(self, val):
        self._unsharp_threshold = val
        self._unsharp_threshold_lbl.setText(
            _("dialogs.adjustments.unsharp_threshold_label", value=val))
        self._update_preview()

    def _on_image_mode_changed(self, key):
        self._image_mode = key
        self._update_preview()

    def _on_auto_levels(self):
        """Calcule les points noir/blanc sur la page d'aperçu et met à jour les sliders."""
        if self._original_preview_img is None:
            return
        # Sauvegarde pour restauration par "Annuler"
        self._pre_auto_black_point = self._black_point
        self._pre_auto_white_point = self._white_point

        buf = io.BytesIO()
        self._original_preview_img.save(buf, format='PNG')
        black_val, white_val = _compute_auto_levels(buf.getvalue())

        self._black_pt_slider.blockSignals(True)
        self._white_pt_slider.blockSignals(True)
        self._black_point = black_val
        self._white_point = white_val
        self._black_pt_slider.setValue(black_val)
        self._white_pt_slider.setValue(white_val)
        self._black_pt_slider.blockSignals(False)
        self._white_pt_slider.blockSignals(False)

        self._black_pt_lbl.setText(
            _("dialogs.adjustments.black_point_label", value=black_val))
        self._white_pt_lbl.setText(
            _("dialogs.adjustments.white_point_label", value=white_val))
        self._update_preview()

    def reject(self):
        """Annuler : restaure les niveaux si 'Auto' avait été cliqué."""
        if self._pre_auto_black_point is not None:
            self._black_pt_slider.blockSignals(True)
            self._white_pt_slider.blockSignals(True)
            self._black_point = self._pre_auto_black_point
            self._white_point = self._pre_auto_white_point
            self._black_pt_slider.setValue(self._pre_auto_black_point)
            self._white_pt_slider.setValue(self._pre_auto_white_point)
            self._black_pt_slider.blockSignals(False)
            self._white_pt_slider.blockSignals(False)
            self._pre_auto_black_point = None
            self._pre_auto_white_point = None
        super().reject()

    # ─────────────────────────────────────────────────────────────────────────
    # Aperçu
    # ─────────────────────────────────────────────────────────────────────────

    def _get_settings(self):
        return {
            'color_depth':            self._color_depth,
            'brightness':             self._brightness,
            'contrast':               self._contrast,
            'compression_quality':    self._comp_quality,
            'initial_quality':        self._initial_quality,
            'effect':                 self._effect,
            'sharpness':              self._sharpness,
            'threshold':              self._threshold,
            'black_point':            self._black_point,
            'gamma':                  self._gamma,
            'white_point':            self._white_point,
            'remove_colors_intensity': self._remove_int,
            'saturation':             self._saturation,
            'image_mode':             self._image_mode,
            'original_ext':           self._original_ext,
            'transparency_type':      'flood',
            'transparency_tolerance': 30,
            'unsharp_radius':         self._unsharp_radius,
            'unsharp_percent':        self._unsharp_percent,
            'unsharp_threshold':      self._unsharp_threshold,
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
        except Exception as e:
            print(f"[adjustments_dialog_qt] aperçu : {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────────────────────────────────────

    def _on_reset(self):
        """Réinitialise tous les contrôles à leurs valeurs par défaut."""
        # Bloque les signaux le temps de la réinitialisation (évite N rafraîchissements)
        for sl in (self._comp_slider, self._sharp_slider, self._bright_slider,
                   self._contrast_slider, self._threshold_slider,
                   self._black_pt_slider, self._gamma_slider, self._white_pt_slider,
                   self._sat_slider, self._remove_colors_slider,
                   self._unsharp_radius_slider, self._unsharp_percent_slider,
                   self._unsharp_threshold_slider):
            sl.blockSignals(True)

        self._color_depth        = 'unchanged'
        self._brightness         = 0
        self._contrast           = 0
        self._comp_quality       = self._initial_quality
        self._effect             = 'none'
        self._sharpness          = 0
        self._threshold          = 128
        self._black_point        = 0
        self._gamma              = 1.0
        self._white_point        = 255
        self._remove_int         = 0
        self._saturation         = 0
        self._image_mode         = 'unchanged'
        self._unsharp_radius     = 2.0
        self._unsharp_percent    = 0
        self._unsharp_threshold  = 3

        self._depth_radios['unchanged'].setChecked(True)
        self._effect_radios['none'].setChecked(True)
        self._mode_radios['unchanged'].setChecked(True)

        self._comp_slider.setValue(self._initial_quality)
        self._sharp_slider.setValue(0)
        self._bright_slider.setValue(0)
        self._contrast_slider.setValue(0)
        self._threshold_slider.setValue(128)
        self._black_pt_slider.setValue(0)
        self._gamma_slider.setValue(100)
        self._white_pt_slider.setValue(255)
        self._sat_slider.setValue(0)
        self._remove_colors_slider.setValue(0)
        self._unsharp_radius_slider.setValue(20)
        self._unsharp_percent_slider.setValue(0)
        self._unsharp_threshold_slider.setValue(3)
        for sl in (self._comp_slider, self._sharp_slider, self._bright_slider,
                   self._contrast_slider, self._threshold_slider,
                   self._black_pt_slider, self._gamma_slider, self._white_pt_slider,
                   self._sat_slider, self._remove_colors_slider,
                   self._unsharp_radius_slider, self._unsharp_percent_slider,
                   self._unsharp_threshold_slider):
            sl.blockSignals(False)

        self._retranslate()
        self._update_preview()

    def _on_cancel(self):
        """Annuler : si traitement en cours, demande l'arrêt ; sinon ferme normalement."""
        if self._cancel_requested is not None and self._cancel_requested is False and \
                self._progress_lbl.isVisible():
            # Traitement en cours → demande d'annulation
            self._cancel_requested = True
        else:
            self.reject()

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

            if self._pre_auto_black_point is not None:
                # Auto cliqué + plusieurs pages : calcul individuel par page
                base_settings = self._get_settings()
                for i, entry in enumerate(self._selected_entries, 1):
                    if self._cancel_requested:
                        break
                    if not entry.get('bytes'):
                        continue
                    self._progress_lbl.setText(_("labels.adjusting", current=i, total=n))
                    QApplication.processEvents()
                    page_settings = dict(base_settings)
                    bp, wp = _compute_auto_levels(entry['bytes'])
                    page_settings['black_point'] = bp
                    page_settings['white_point'] = wp
                    _apply_image_adjustments([entry], page_settings, callbacks=callbacks_no_save)
                    processed.append(entry)
            else:
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
        self.accept()

    def _open_viewer(self, mode):
        """Ouvre la visionneuse plein écran pour le mode donné."""
        from modules.qt.adjustments_viewers_qt import AdjustmentViewerDialog

        # Snapshot des settings courants (le viewer va les modifier)
        settings = self._get_settings()

        def on_close():
            """Synchronise les sliders du dialog avec les valeurs modifiées dans le viewer."""
            # Bloque les signaux pour éviter N rafraîchissements
            for sl in (self._comp_slider, self._sharp_slider, self._bright_slider,
                       self._contrast_slider, self._black_pt_slider,
                       self._gamma_slider, self._white_pt_slider,
                       self._sat_slider, self._remove_colors_slider,
                       self._unsharp_radius_slider, self._unsharp_percent_slider,
                       self._unsharp_threshold_slider):
                sl.blockSignals(True)

            self._sharpness         = settings.get('sharpness', 0)
            self._brightness        = settings.get('brightness', 0)
            self._contrast          = settings.get('contrast', 0)
            self._comp_quality      = settings.get('compression_quality', self._initial_quality)
            self._remove_int        = settings.get('remove_colors_intensity', 0)
            self._saturation        = settings.get('saturation', 0)
            self._black_point       = settings.get('black_point', 0)
            self._white_point       = settings.get('white_point', 255)
            self._gamma             = settings.get('gamma', 1.0)
            self._unsharp_radius    = settings.get('unsharp_radius', 2.0)
            self._unsharp_percent   = settings.get('unsharp_percent', 0)
            self._unsharp_threshold = settings.get('unsharp_threshold', 3)

            self._sharp_slider.setValue(self._sharpness)
            self._bright_slider.setValue(self._brightness)
            self._contrast_slider.setValue(self._contrast)
            self._comp_slider.setValue(self._comp_quality)
            self._sat_slider.setValue(self._saturation)
            self._remove_colors_slider.setValue(self._remove_int)
            self._black_pt_slider.setValue(self._black_point)
            self._white_pt_slider.setValue(self._white_point)
            self._gamma_slider.setValue(int(self._gamma * 100))
            self._unsharp_radius_slider.setValue(int(self._unsharp_radius * 10))
            self._unsharp_percent_slider.setValue(self._unsharp_percent)
            self._unsharp_threshold_slider.setValue(self._unsharp_threshold)

            for sl in (self._comp_slider, self._sharp_slider, self._bright_slider,
                       self._contrast_slider, self._black_pt_slider,
                       self._gamma_slider, self._white_pt_slider,
                       self._sat_slider, self._remove_colors_slider,
                       self._unsharp_radius_slider, self._unsharp_percent_slider,
                       self._unsharp_threshold_slider):
                sl.blockSignals(False)

            self._retranslate()
            self._update_preview()
            self.accept()

        def on_cancel():
            """Annulation : remet les settings au snapshot initial (sans modification)."""
            pass  # le viewer n'a pas encore appliqué → pas de rollback nécessaire

        viewer = AdjustmentViewerDialog(
            self, self._selected_entries, settings, mode,
            on_close_callback=on_close,
            on_cancel_callback=on_cancel,
            callbacks=self._callbacks,
        )
        viewer.exec()


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
        ).exec()
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
        ).exec()
        return

    dlg = AdjustmentsDialog(parent, selected_entries, callbacks=callbacks)
    dlg.exec()
