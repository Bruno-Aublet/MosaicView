"""
modules/qt/adjustments_viewers_qt.py — Visionneuse plein-écran pour les ajustements d'images

Reproduit le comportement de la classe LevelsViewer de Modules_OLD/adjustments_dialog.py,
sans aucune référence à tkinter.

Modes supportés :
  'sharpness'      — réglette netteté -100..+100
  'brightness'     — réglettes luminosité + contraste -100..+100
  'compression'    — réglette qualité 1..100
  'remove_colors'  — réglette intensité 0..100
  'saturation'     — réglette saturation -100..+100
  'unsharp'        — 3 réglettes Unsharp Mask (radius, percent, threshold)
  'levels'         — pipettes point noir / point blanc
  'transparency'   — clic pour rendre transparent (flood fill ou global)

Classe publique :
  AdjustmentViewerDialog(parent, selected_entries, settings, mode,
                         on_close_callback=None, on_cancel_callback=None,
                         callbacks=None)
"""

import io

from PIL import Image

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QFrame, QScrollArea, QSizePolicy,
    QApplication, QSpinBox,
)
from PySide6.QtCore import Qt, QPoint, QSize
from PySide6.QtGui import QPixmap, QImage, QCursor, QKeySequence, QShortcut, QIcon

from modules.qt.localization import _
from modules.qt.utils import FocusSlider
from modules.qt.state import get_current_theme
from modules.qt.font_loader import resource_path
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.adjustments_processing_qt import (
    apply_adjustments      as _apply_adjustments,
    apply_image_adjustments as _apply_image_adjustments,
    compute_auto_levels    as _compute_auto_levels,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers langue
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


# ─────────────────────────────────────────────────────────────────────────────
# Styles
# ─────────────────────────────────────────────────────────────────────────────

def _btn_style(theme):
    return (
        f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
        f"border: 1px solid #aaaaaa; padding: 4px 12px; }} "
        f"QPushButton:hover {{ background: {theme['separator']}; }} "
        f"QPushButton:disabled {{ color: #888888; }}"
    )


def _pip_btn_style(theme):
    """Style bouton pipette avec état :checked visible."""
    return (
        f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
        f"border: 1px solid #aaaaaa; padding: 4px 12px; }} "
        f"QPushButton:hover {{ background: {theme['separator']}; }} "
        f"QPushButton:checked {{ background: #1a6fa8; color: white; border: 2px solid #4da6e0; }} "
        f"QPushButton:disabled {{ color: #888888; }}"
    )


def _slider_style(theme):
    return (
        f"QSlider::groove:horizontal {{ height: 6px; background: {theme['separator']}; "
        f"border-radius: 3px; }} "
        f"QSlider::handle:horizontal {{ background: {theme['text']}; width: 14px; height: 14px; "
        f"margin: -4px 0; border-radius: 7px; }} "
        f"QSlider::sub-page:horizontal {{ background: {theme['text']}; border-radius: 3px; }}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Widget de défilement d'image (canvas zoomable + scrollable + pan)
# ─────────────────────────────────────────────────────────────────────────────

class _ImageScrollWidget(QWidget):
    """Widget affichant une image avec zoom, scroll et pan clic-droit."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: black;")

        self._zoom = 1.0
        self._offset = QPoint(0, 0)
        self._pan_start = None
        self._is_panning = False
        self._pixmap = None
        self.on_zoom_changed = None  # callback(zoom_level)

        self.setMouseTracking(True)

    def set_pixmap(self, pixmap, reset_offset=True):
        self._pixmap = pixmap
        if reset_offset:
            self._offset = QPoint(0, 0)
        self.update()

    def zoom_level(self):
        return self._zoom

    def set_zoom(self, z):
        self._zoom = max(0.1, min(10.0, z))
        self.update()
        if self.on_zoom_changed:
            self.on_zoom_changed(self._zoom)

    def adjust_zoom(self, delta):
        self.set_zoom(self._zoom + delta)

    def reset_zoom(self):
        self._zoom = 1.0
        self._offset = QPoint(0, 0)
        self.update()
        if self.on_zoom_changed:
            self.on_zoom_changed(self._zoom)

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter
        if not self._pixmap:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w = int(self._pixmap.width() * self._zoom)
        h = int(self._pixmap.height() * self._zoom)
        x = self._offset.x() + (self.width() - w) // 2
        y = self._offset.y() + (self.height() - h) // 2
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        painter.drawPixmap(x, y, w, h, self._pixmap)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = 0.15 if event.angleDelta().y() > 0 else -0.15
            self.adjust_zoom(delta)
            event.accept()
        else:
            # Scroll vertical
            delta = -event.angleDelta().y() // 3
            self._offset += QPoint(0, -delta)
            self._clamp_offset()
            self.update()
            event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._pan_start = event.position().toPoint()
            self._is_panning = False
            event.accept()
        elif event.button() == Qt.MouseButton.LeftButton:
            # double-click géré dans mouseDoubleClickEvent
            event.accept()

    def mouseMoveEvent(self, event):
        if self._pan_start is not None:
            diff = event.position().toPoint() - self._pan_start
            if abs(diff.x()) > 3 or abs(diff.y()) > 3:
                self._is_panning = True
                self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
                self._offset += diff
                self._pan_start = event.position().toPoint()
                self._clamp_offset()
                self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._pan_start = None
            self._is_panning = False
            # Remet le curseur approprié selon le mode du dialog parent
            parent = self.parent()
            while parent:
                if isinstance(parent, AdjustmentViewerDialog):
                    if parent._mode == 'transparency':
                        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
                    else:
                        self.unsetCursor()
                    break
                parent = parent.parent()
            else:
                self.unsetCursor()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Signale au dialog parent de basculer le plein écran
            parent = self.parent()
            while parent:
                if hasattr(parent, 'toggle_fullscreen'):
                    parent.toggle_fullscreen()
                    break
                parent = parent.parent()
        event.accept()

    def _clamp_offset(self):
        if not self._pixmap:
            return
        w = int(self._pixmap.width() * self._zoom)
        h = int(self._pixmap.height() * self._zoom)
        max_ox = max(0, (w - self.width()) // 2)
        max_oy = max(0, (h - self.height()) // 2)
        ox = max(-max_ox, min(max_ox, self._offset.x()))
        oy = max(-max_oy, min(max_oy, self._offset.y()))
        self._offset = QPoint(ox, oy)


# ─────────────────────────────────────────────────────────────────────────────
# Visionneuse principale
# ─────────────────────────────────────────────────────────────────────────────

class AdjustmentViewerDialog(QDialog):
    """Visionneuse plein-écran pour les ajustements d'images.

    Paramètres :
      selected_entries  : liste des images sélectionnées (dicts avec 'bytes')
      settings          : dict des réglages courants (partagé avec le dialog parent)
      mode              : 'sharpness' | 'brightness' | 'compression' |
                          'remove_colors' | 'saturation' | 'levels'
      on_close_callback : appelé après Apply (pour rafraîchir le dialog parent)
      on_cancel_callback: appelé lors de Cancel/Escape (pour restaurer les valeurs)
      callbacks         : dict MosaicView (save_state, render_mosaic)
    """

    def __init__(self, parent, selected_entries, settings, mode,
                 on_close_callback=None, on_cancel_callback=None,
                 callbacks=None):
        super().__init__(parent)
        self._settings = settings           # dict partagé (sera mutated par les sliders)
        self._mode = mode
        self._on_close_callback = on_close_callback
        self._on_cancel_callback = on_cancel_callback
        self._callbacks = callbacks or {}

        # En mode transparency, filtrer les entrées non supportées (JPEG, BMP…)
        self._skipped_count = 0
        if mode == 'transparency':
            _SUPPORTED_EXTS = {'.png', '.webp', '.ico'}
            supported = [e for e in selected_entries
                         if e.get('extension', '').lower() in _SUPPORTED_EXTS]
            self._skipped_count = len(selected_entries) - len(supported)
            self._selected_entries = supported
        else:
            self._selected_entries = selected_entries

        self._current_idx = 0
        self._is_fullscreen = False

        # Variables spécifiques au mode levels (pipettes)
        self._pipette_mode = None       # None | 'black' | 'white'
        self._levels_history = []
        self._levels_redo_stack = []
        self._levels_histories = {}
        self._levels_redo_stacks = {}
        self._levels_values = {}

        # Variables spécifiques au mode transparency
        self._transp_work_img  = None   # PIL RGBA de la page courante
        self._transp_history   = []     # pile undo de la page courante
        self._transp_redo_stk  = []     # pile redo de la page courante
        self._transp_work_imgs = {}     # dict idx → PIL RGBA (toutes pages)
        self._transp_histories = {}     # dict idx → pile undo
        self._transp_redo_stks = {}     # dict idx → pile redo

        theme = get_current_theme()
        self.setWindowTitle(self._get_title())
        self.resize(900, 700)
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )

        self._build_ui(theme)
        _connect_lang(self, self._retranslate)

        # Raccourcis clavier
        QShortcut(QKeySequence("F11"), self).activated.connect(self.toggle_fullscreen)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self._img_widget.reset_zoom)
        if mode == 'levels':
            QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self._levels_undo)
            QShortcut(QKeySequence("Ctrl+Y"), self).activated.connect(self._levels_redo)
        elif mode == 'transparency':
            QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self._transp_undo)
            QShortcut(QKeySequence("Ctrl+Y"), self).activated.connect(self._transp_redo)

        self._display_image(reset_offset=True)

    # ── Construction de l'UI ──────────────────────────────────────────────────

    def _build_ui(self, theme):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Helper chargement icônes (utilisé dans la toolbar et la barre du bas)
        def _load_icon(name, size):
            try:
                from PIL import Image as _PilImg
                path = resource_path(f"icons/{name}")
                pil = _PilImg.open(path).resize((size, size), _PilImg.Resampling.LANCZOS)
                buf = io.BytesIO()
                pil.save(buf, format='PNG')
                buf.seek(0)
                pm = QPixmap()
                pm.loadFromData(buf.read())
                return QIcon(pm)
            except Exception:
                return None

        # ── Toolbar ──────────────────────────────────────────────────────────
        tb = QWidget()
        tb.setFixedHeight(50)
        tb.setStyleSheet(f"background: {theme['toolbar_bg']}; color: {theme['text']};")
        tb_layout = QHBoxLayout(tb)
        tb_layout.setContentsMargins(8, 4, 8, 4)
        tb_layout.setSpacing(6)

        font_tb = _get_current_font(10)

        # Navigation ◀ compteur ▶
        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFont(_get_current_font(12))
        self._prev_btn.setFixedWidth(36)
        self._prev_btn.setStyleSheet(_btn_style(theme))
        self._prev_btn.clicked.connect(self._prev_image)
        tb_layout.addWidget(self._prev_btn)

        self._counter_lbl = QLabel()
        self._counter_lbl.setFont(font_tb)
        self._counter_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._counter_lbl.setMinimumWidth(60)
        tb_layout.addWidget(self._counter_lbl)

        self._next_btn = QPushButton("▶")
        self._next_btn.setFont(_get_current_font(12))
        self._next_btn.setFixedWidth(36)
        self._next_btn.setStyleSheet(_btn_style(theme))
        self._next_btn.clicked.connect(self._next_image)
        tb_layout.addWidget(self._next_btn)

        # Séparateur vertical
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {theme['separator']};")
        tb_layout.addWidget(sep)

        # Contrôles spécifiques au mode
        self._mode_widgets = []
        self._build_mode_controls(tb_layout, tb, theme, font_tb)

        tb_layout.addStretch()

        # Zoom − % +
        lbl_zoom_minus = QPushButton()
        lbl_zoom_minus.setFixedSize(28, 28)
        lbl_zoom_minus.setStyleSheet(_btn_style(theme))
        lbl_zoom_minus.clicked.connect(lambda: self._adjust_zoom(-0.15))
        _icon_minus = _load_icon("BTN_-.png", 20)
        if _icon_minus:
            lbl_zoom_minus.setIcon(_icon_minus)
            lbl_zoom_minus.setIconSize(QSize(20, 20))
        else:
            lbl_zoom_minus.setText("−")
            lbl_zoom_minus.setFont(font_tb)
        tb_layout.addWidget(lbl_zoom_minus)

        self._zoom_lbl = QLabel("100%")
        self._zoom_lbl.setFont(font_tb)
        self._zoom_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_lbl.setMinimumWidth(48)
        tb_layout.addWidget(self._zoom_lbl)

        lbl_zoom_plus = QPushButton()
        lbl_zoom_plus.setFixedSize(28, 28)
        lbl_zoom_plus.setStyleSheet(_btn_style(theme))
        lbl_zoom_plus.clicked.connect(lambda: self._adjust_zoom(0.15))
        _icon_plus = _load_icon("BTN_+.png", 20)
        if _icon_plus:
            lbl_zoom_plus.setIcon(_icon_plus)
            lbl_zoom_plus.setIconSize(QSize(20, 20))
        else:
            lbl_zoom_plus.setText("+")
            lbl_zoom_plus.setFont(font_tb)
        tb_layout.addWidget(lbl_zoom_plus)

        root.addWidget(tb)

        # ── Bandeau d'avertissement (transparency : images ignorées) ──────────
        self._warning_lbl = QLabel()
        self._warning_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._warning_lbl.setWordWrap(True)
        self._warning_lbl.setStyleSheet(
            "background: #cc2222; color: white; padding: 4px 12px; font-weight: bold;")
        self._warning_lbl.setVisible(
            self._mode == 'transparency' and self._skipped_count > 0)
        root.addWidget(self._warning_lbl)

        # ── Zone image ───────────────────────────────────────────────────────
        self._img_widget = _ImageScrollWidget()
        self._img_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._img_widget.on_zoom_changed = lambda z: self._zoom_lbl.setText(f"{int(z * 100)}%")
        if self._mode == 'transparency':
            self._img_widget.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        root.addWidget(self._img_widget, stretch=1)

        # ── Barre du bas ─────────────────────────────────────────────────────
        bot = QWidget()
        bot.setStyleSheet(f"background: {theme['bg']}; color: {theme['text']};")
        bot_layout = QHBoxLayout(bot)
        bot_layout.setContentsMargins(10, 6, 10, 6)
        bot_layout.setSpacing(8)

        font_btn = _get_current_font(11)

        icon_undo  = _load_icon("BTN_Batch_Undo.png", 22)
        icon_redo  = _load_icon("BTN_Batch_Redo.png", 22)
        icon_black = _load_icon("pipette_noire.png",   20)
        icon_white = _load_icon("pipette_blanche.png", 20)

        # Curseurs custom pipette (24×24, hotspot en haut à gauche)
        self._cursor_black = None
        self._cursor_white = None
        for fname, attr in (("pipette_noire.png", "_cursor_black"),
                             ("pipette_blanche.png", "_cursor_white")):
            path = resource_path(f"icons/{fname}")
            pix = QPixmap(path)
            if not pix.isNull():
                setattr(self, attr, QCursor(pix.scaled(
                    24, 24, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation), 0, 0))

        if self._mode == 'levels':
            # En mode levels : bouton Appliquer + pipettes
            self._apply_current_btn = QPushButton()
            self._apply_current_btn.setFont(font_btn)
            self._apply_current_btn.setStyleSheet(_btn_style(theme))
            self._apply_current_btn.clicked.connect(self._apply_levels_all)
            bot_layout.addWidget(self._apply_current_btn)

            sep2 = QFrame()
            sep2.setFrameShape(QFrame.Shape.VLine)
            sep2.setStyleSheet(f"color: {theme['separator']};")
            bot_layout.addWidget(sep2)

            # Pipette noire
            self._black_pip_btn = QPushButton()
            self._black_pip_btn.setFont(font_btn)
            self._black_pip_btn.setStyleSheet(_pip_btn_style(theme))
            self._black_pip_btn.setCheckable(True)
            if icon_black:
                self._black_pip_btn.setIcon(icon_black)
                self._black_pip_btn.setIconSize(QSize(20, 20))
            self._black_pip_btn.clicked.connect(self._activate_black_pipette)
            bot_layout.addWidget(self._black_pip_btn)

            # Pipette blanche
            self._white_pip_btn = QPushButton()
            self._white_pip_btn.setFont(font_btn)
            self._white_pip_btn.setStyleSheet(_pip_btn_style(theme))
            self._white_pip_btn.setCheckable(True)
            if icon_white:
                self._white_pip_btn.setIcon(icon_white)
                self._white_pip_btn.setIconSize(QSize(20, 20))
            self._white_pip_btn.clicked.connect(self._activate_white_pipette)
            bot_layout.addWidget(self._white_pip_btn)

            # Undo / Redo pipettes
            self._levels_undo_btn = QPushButton()
            self._levels_undo_btn.setFixedSize(32, 32)
            self._levels_undo_btn.setStyleSheet(_btn_style(theme))
            self._levels_undo_btn.setEnabled(False)
            if icon_undo:
                self._levels_undo_btn.setIcon(icon_undo)
                self._levels_undo_btn.setIconSize(QSize(22, 22))
            else:
                self._levels_undo_btn.setText("↩")
                self._levels_undo_btn.setFont(font_btn)
            self._levels_undo_btn.clicked.connect(self._levels_undo)
            bot_layout.addWidget(self._levels_undo_btn)

            self._levels_redo_btn = QPushButton()
            self._levels_redo_btn.setFixedSize(32, 32)
            self._levels_redo_btn.setStyleSheet(_btn_style(theme))
            self._levels_redo_btn.setEnabled(False)
            if icon_redo:
                self._levels_redo_btn.setIcon(icon_redo)
                self._levels_redo_btn.setIconSize(QSize(22, 22))
            else:
                self._levels_redo_btn.setText("↪")
                self._levels_redo_btn.setFont(font_btn)
            self._levels_redo_btn.clicked.connect(self._levels_redo)
            bot_layout.addWidget(self._levels_redo_btn)

            sep3 = QFrame()
            sep3.setFrameShape(QFrame.Shape.VLine)
            sep3.setStyleSheet(f"color: {theme['separator']};")
            bot_layout.addWidget(sep3)

            self._auto_levels_btn = QPushButton()
            self._auto_levels_btn.setFont(font_btn)
            self._auto_levels_btn.setStyleSheet(_btn_style(theme))
            self._auto_levels_btn.clicked.connect(self._on_auto_levels)
            bot_layout.addWidget(self._auto_levels_btn)

            self._apply_all_btn = None

        elif self._mode == 'transparency':
            # Bouton Appliquer
            self._apply_current_btn = QPushButton()
            self._apply_current_btn.setFont(font_btn)
            self._apply_current_btn.setStyleSheet(_btn_style(theme))
            self._apply_current_btn.clicked.connect(self._apply_transparency)
            bot_layout.addWidget(self._apply_current_btn)

            # Séparateur
            sep_bt = QFrame()
            sep_bt.setFrameShape(QFrame.Shape.VLine)
            sep_bt.setStyleSheet(f"color: {theme['separator']};")
            bot_layout.addWidget(sep_bt)

            # Zone [slider] Global
            self._transp_flood_lbl = QLabel()
            self._transp_flood_lbl.setFont(font_btn)
            bot_layout.addWidget(self._transp_flood_lbl)

            self._transp_type_slider = FocusSlider(Qt.Orientation.Horizontal)
            self._transp_type_slider.setRange(0, 1)
            self._transp_type_slider.setValue(
                0 if self._settings.get('transparency_type', 'flood') == 'flood' else 1)
            self._transp_type_slider.setFixedWidth(50)
            self._transp_type_slider.setStyleSheet(_slider_style(theme))
            self._transp_type_slider.valueChanged.connect(self._on_transp_type_changed)
            bot_layout.addWidget(self._transp_type_slider)

            self._transp_global_lbl = QLabel()
            self._transp_global_lbl.setFont(font_btn)
            bot_layout.addWidget(self._transp_global_lbl)

            # Séparateur
            sep_bt2 = QFrame()
            sep_bt2.setFrameShape(QFrame.Shape.VLine)
            sep_bt2.setStyleSheet(f"color: {theme['separator']};")
            bot_layout.addWidget(sep_bt2)

            # Tolérance
            self._transp_tol_lbl = QLabel()
            self._transp_tol_lbl.setFont(font_btn)
            bot_layout.addWidget(self._transp_tol_lbl)

            self._transp_tol_slider = FocusSlider(Qt.Orientation.Horizontal)
            self._transp_tol_slider.setRange(0, 255)
            self._transp_tol_slider.setValue(self._settings.get('transparency_tolerance', 30))
            self._transp_tol_slider.setFixedWidth(180)
            self._transp_tol_slider.setStyleSheet(_slider_style(theme))
            self._transp_tol_slider.valueChanged.connect(self._on_transp_tol_changed)
            bot_layout.addWidget(self._transp_tol_slider)

            self._transp_tol_spin = QSpinBox()
            self._transp_tol_spin.setRange(0, 255)
            self._transp_tol_spin.setValue(self._settings.get('transparency_tolerance', 30))
            self._transp_tol_spin.setFixedWidth(58)
            self._transp_tol_spin.setFont(font_btn)
            self._transp_tol_spin.setStyleSheet(
                f"QSpinBox {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
                f"border: 1px solid #aaaaaa; padding: 2px 4px; }} "
                f"QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}"
            )
            self._transp_tol_spin.valueChanged.connect(self._on_transp_tol_spin_changed)
            bot_layout.addWidget(self._transp_tol_spin)

            # Espacement net avant undo/redo
            bot_layout.addSpacing(24)

            self._transp_undo_btn = QPushButton()
            self._transp_undo_btn.setFixedSize(32, 32)
            self._transp_undo_btn.setStyleSheet(_btn_style(theme))
            self._transp_undo_btn.setEnabled(False)
            if icon_undo:
                self._transp_undo_btn.setIcon(icon_undo)
                self._transp_undo_btn.setIconSize(QSize(22, 22))
            else:
                self._transp_undo_btn.setText("↩")
                self._transp_undo_btn.setFont(font_btn)
            self._transp_undo_btn.clicked.connect(self._transp_undo)
            bot_layout.addWidget(self._transp_undo_btn)

            self._transp_redo_btn = QPushButton()
            self._transp_redo_btn.setFixedSize(32, 32)
            self._transp_redo_btn.setStyleSheet(_btn_style(theme))
            self._transp_redo_btn.setEnabled(False)
            if icon_redo:
                self._transp_redo_btn.setIcon(icon_redo)
                self._transp_redo_btn.setIconSize(QSize(22, 22))
            else:
                self._transp_redo_btn.setText("↪")
                self._transp_redo_btn.setFont(font_btn)
            self._transp_redo_btn.clicked.connect(self._transp_redo)
            bot_layout.addWidget(self._transp_redo_btn)

            # Initialise les couleurs des labels type
            self._update_transp_type_labels()
            self._apply_all_btn = None

        else:
            # Bouton "Appliquer à cette image"
            self._apply_current_btn = QPushButton()
            self._apply_current_btn.setFont(font_btn)
            self._apply_current_btn.setStyleSheet(_btn_style(theme))
            self._apply_current_btn.clicked.connect(self._apply_to_current)
            bot_layout.addWidget(self._apply_current_btn)

            # Bouton "Appliquer à toutes" (si >1 image)
            if len(self._selected_entries) > 1:
                self._apply_all_btn = QPushButton()
                self._apply_all_btn.setFont(font_btn)
                self._apply_all_btn.setStyleSheet(_btn_style(theme))
                self._apply_all_btn.clicked.connect(self._apply_to_all)
                bot_layout.addWidget(self._apply_all_btn)
            else:
                self._apply_all_btn = None

        bot_layout.addStretch()

        # Bouton Annuler
        self._cancel_btn = QPushButton()
        self._cancel_btn.setFont(font_btn)
        self._cancel_btn.setStyleSheet(_btn_style(theme))
        self._cancel_btn.clicked.connect(self._cancel)
        bot_layout.addWidget(self._cancel_btn)

        root.addWidget(bot)

        # Textes initiaux
        self._retranslate()

    def _build_mode_controls(self, layout, parent_widget, theme, font_tb):
        """Ajoute les contrôles spécifiques au mode dans la toolbar."""

        if self._mode == 'sharpness':
            self._sharp_lbl = QLabel()
            self._sharp_lbl.setFont(font_tb)
            layout.addWidget(self._sharp_lbl)

            self._sharp_slider = FocusSlider(Qt.Orientation.Horizontal)
            self._sharp_slider.setRange(-100, 100)
            self._sharp_slider.setValue(self._settings.get('sharpness', 0))
            self._sharp_slider.setFixedWidth(300)
            self._sharp_slider.setStyleSheet(_slider_style(theme))
            self._sharp_slider.valueChanged.connect(self._on_sharpness_changed)
            layout.addWidget(self._sharp_slider)

        elif self._mode == 'brightness':
            self._bright_lbl = QLabel()
            self._bright_lbl.setFont(font_tb)
            layout.addWidget(self._bright_lbl)

            self._bright_slider = FocusSlider(Qt.Orientation.Horizontal)
            self._bright_slider.setRange(-100, 100)
            self._bright_slider.setValue(self._settings.get('brightness', 0))
            self._bright_slider.setFixedWidth(200)
            self._bright_slider.setStyleSheet(_slider_style(theme))
            self._bright_slider.valueChanged.connect(self._on_brightness_changed)
            layout.addWidget(self._bright_slider)

            self._contrast_lbl = QLabel()
            self._contrast_lbl.setFont(font_tb)
            layout.addWidget(self._contrast_lbl)

            self._contrast_slider = FocusSlider(Qt.Orientation.Horizontal)
            self._contrast_slider.setRange(-100, 100)
            self._contrast_slider.setValue(self._settings.get('contrast', 0))
            self._contrast_slider.setFixedWidth(200)
            self._contrast_slider.setStyleSheet(_slider_style(theme))
            self._contrast_slider.valueChanged.connect(self._on_contrast_changed)
            layout.addWidget(self._contrast_slider)

        elif self._mode == 'compression':
            self._comp_lbl = QLabel()
            self._comp_lbl.setFont(font_tb)
            layout.addWidget(self._comp_lbl)

            self._comp_slider = FocusSlider(Qt.Orientation.Horizontal)
            self._comp_slider.setRange(1, 100)
            self._comp_slider.setValue(self._settings.get('compression_quality', 100))
            self._comp_slider.setFixedWidth(300)
            self._comp_slider.setStyleSheet(_slider_style(theme))
            self._comp_slider.valueChanged.connect(self._on_compression_changed)
            layout.addWidget(self._comp_slider)

        elif self._mode == 'remove_colors':
            self._remove_lbl = QLabel()
            self._remove_lbl.setFont(font_tb)
            layout.addWidget(self._remove_lbl)

            self._remove_slider = FocusSlider(Qt.Orientation.Horizontal)
            self._remove_slider.setRange(0, 100)
            self._remove_slider.setValue(self._settings.get('remove_colors_intensity', 0))
            self._remove_slider.setFixedWidth(300)
            self._remove_slider.setStyleSheet(_slider_style(theme))
            self._remove_slider.valueChanged.connect(self._on_remove_colors_changed)
            layout.addWidget(self._remove_slider)

        elif self._mode == 'saturation':
            self._sat_lbl = QLabel()
            self._sat_lbl.setFont(font_tb)
            layout.addWidget(self._sat_lbl)

            self._sat_slider = FocusSlider(Qt.Orientation.Horizontal)
            self._sat_slider.setRange(-100, 100)
            self._sat_slider.setValue(self._settings.get('saturation', 0))
            self._sat_slider.setFixedWidth(300)
            self._sat_slider.setStyleSheet(_slider_style(theme))
            self._sat_slider.valueChanged.connect(self._on_saturation_changed)
            layout.addWidget(self._sat_slider)

        elif self._mode == 'unsharp':
            self._unsharp_radius_lbl = QLabel()
            self._unsharp_radius_lbl.setFont(font_tb)
            layout.addWidget(self._unsharp_radius_lbl)

            self._unsharp_radius_slider = FocusSlider(Qt.Orientation.Horizontal)
            self._unsharp_radius_slider.setRange(5, 50)
            self._unsharp_radius_slider.setValue(int(self._settings.get('unsharp_radius', 2.0) * 10))
            self._unsharp_radius_slider.setFixedWidth(120)
            self._unsharp_radius_slider.setStyleSheet(_slider_style(theme))
            self._unsharp_radius_slider.valueChanged.connect(self._on_unsharp_radius_changed)
            layout.addWidget(self._unsharp_radius_slider)

            self._unsharp_percent_lbl = QLabel()
            self._unsharp_percent_lbl.setFont(font_tb)
            layout.addWidget(self._unsharp_percent_lbl)

            self._unsharp_percent_slider = FocusSlider(Qt.Orientation.Horizontal)
            self._unsharp_percent_slider.setRange(0, 200)
            self._unsharp_percent_slider.setValue(self._settings.get('unsharp_percent', 0))
            self._unsharp_percent_slider.setFixedWidth(150)
            self._unsharp_percent_slider.setStyleSheet(_slider_style(theme))
            self._unsharp_percent_slider.valueChanged.connect(self._on_unsharp_percent_changed)
            layout.addWidget(self._unsharp_percent_slider)

            self._unsharp_threshold_lbl = QLabel()
            self._unsharp_threshold_lbl.setFont(font_tb)
            layout.addWidget(self._unsharp_threshold_lbl)

            self._unsharp_threshold_slider = FocusSlider(Qt.Orientation.Horizontal)
            self._unsharp_threshold_slider.setRange(0, 30)
            self._unsharp_threshold_slider.setValue(self._settings.get('unsharp_threshold', 3))
            self._unsharp_threshold_slider.setFixedWidth(100)
            self._unsharp_threshold_slider.setStyleSheet(_slider_style(theme))
            self._unsharp_threshold_slider.valueChanged.connect(self._on_unsharp_threshold_changed)
            layout.addWidget(self._unsharp_threshold_slider)

        elif self._mode == 'transparency':
            # Toolbar : instruction seulement
            self._transp_instr_lbl = QLabel()
            self._transp_instr_lbl.setFont(font_tb)
            layout.addWidget(self._transp_instr_lbl)

        # Pas de contrôles dans la toolbar pour le mode 'levels' (pipettes dans la barre du bas)

    # ── Callbacks sliders ─────────────────────────────────────────────────────

    def _on_sharpness_changed(self, val):
        self._settings['sharpness'] = val
        self._sharp_lbl.setText(
            _("dialogs.adjustments.sharpness_label", value=val))
        self._display_image()

    def _on_brightness_changed(self, val):
        self._settings['brightness'] = val
        self._bright_lbl.setText(
            _("dialogs.adjustments.brightness_label", value=val))
        self._display_image()

    def _on_contrast_changed(self, val):
        self._settings['contrast'] = val
        self._contrast_lbl.setText(
            _("dialogs.adjustments.contrast_label", value=val))
        self._display_image()

    def _on_compression_changed(self, val):
        self._settings['compression_quality'] = val
        self._comp_lbl.setText(
            _("dialogs.adjustments.compression_quality_label", value=val))
        self._display_image()

    def _on_remove_colors_changed(self, val):
        self._settings['remove_colors_intensity'] = val
        self._remove_lbl.setText(
            _("dialogs.adjustments.remove_colors_intensity_label", value=val))
        self._display_image()

    def _on_saturation_changed(self, val):
        self._settings['saturation'] = val
        self._sat_lbl.setText(
            _("dialogs.adjustments.saturation_label", value=val))
        self._display_image()

    def _on_unsharp_radius_changed(self, val):
        self._settings['unsharp_radius'] = round(val / 10.0, 1)
        self._unsharp_radius_lbl.setText(
            _("dialogs.adjustments.unsharp_radius_label", value=self._settings['unsharp_radius']))
        self._display_image()

    def _on_unsharp_percent_changed(self, val):
        self._settings['unsharp_percent'] = val
        self._unsharp_percent_lbl.setText(
            _("dialogs.adjustments.unsharp_percent_label", value=val))
        self._display_image()

    def _on_unsharp_threshold_changed(self, val):
        self._settings['unsharp_threshold'] = val
        self._unsharp_threshold_lbl.setText(
            _("dialogs.adjustments.unsharp_threshold_label", value=val))
        self._display_image()

    def _on_transp_type_changed(self, val):
        t = 'flood' if val == 0 else 'global'
        self._settings['transparency_type'] = t
        self._update_transp_type_labels()

    def _on_transp_tol_changed(self, val):
        self._settings['transparency_tolerance'] = val
        # Synchronise la spinbox sans boucle infinie
        if self._transp_tol_spin.value() != val:
            self._transp_tol_spin.blockSignals(True)
            self._transp_tol_spin.setValue(val)
            self._transp_tol_spin.blockSignals(False)

    def _on_transp_tol_spin_changed(self, val):
        # Synchronise le slider sans boucle infinie
        if self._transp_tol_slider.value() != val:
            self._transp_tol_slider.blockSignals(True)
            self._transp_tol_slider.setValue(val)
            self._transp_tol_slider.blockSignals(False)
        self._on_transp_tol_changed(val)

    def _update_transp_type_labels(self):
        """Grise le label du type inactif."""
        if not hasattr(self, '_transp_flood_lbl'):
            return
        theme = get_current_theme()
        active   = theme['text']
        inactive = theme.get('disabled', '#aaaaaa')
        t = self._settings.get('transparency_type', 'flood')
        self._transp_flood_lbl.setStyleSheet(
            f"color: {active};" if t == 'flood' else f"color: {inactive};")
        self._transp_global_lbl.setStyleSheet(
            f"color: {active};" if t == 'global' else f"color: {inactive};")

    # ── Navigation ────────────────────────────────────────────────────────────

    def _prev_image(self):
        if self._current_idx > 0:
            self._save_levels_state()
            self._save_transp_state()
            self._current_idx -= 1
            self._restore_levels_state()
            self._restore_transp_state()
            self._display_image(reset_offset=True)

    def _next_image(self):
        if self._current_idx < len(self._selected_entries) - 1:
            self._save_levels_state()
            self._save_transp_state()
            self._current_idx += 1
            self._restore_levels_state()
            self._restore_transp_state()
            self._display_image(reset_offset=True)

    # ── Affichage image ───────────────────────────────────────────────────────

    def _display_image(self, reset_offset=False):
        if not self._selected_entries or self._current_idx >= len(self._selected_entries):
            return
        entry = self._selected_entries[self._current_idx]
        if not entry.get('bytes'):
            return

        try:
            original = Image.open(io.BytesIO(entry['bytes']))
            if self._mode == 'levels':
                preview = self._apply_levels_preview(original.copy())
            elif self._mode == 'transparency':
                # Initialise l'image de travail si première visite de cette page
                if self._transp_work_img is None:
                    self._transp_work_img = original.convert('RGBA')
                preview = self._compose_checkerboard(self._transp_work_img)
            else:
                preview = _apply_adjustments(original.copy(), self._settings,
                                             for_preview=True)

            original_has_alpha = (original.mode in ('RGBA', 'LA') or
                                  (original.mode == 'P' and 'transparency' in original.info))
            if self._mode != 'transparency' and original_has_alpha:
                if preview.mode != 'RGBA':
                    # Le traitement a perdu l'alpha — le récupérer depuis l'original
                    alpha = original.convert('RGBA').split()[3]
                    rgba = preview.convert('RGB').convert('RGBA')
                    rgba.putalpha(alpha)
                    preview = self._compose_checkerboard(rgba)
                else:
                    preview = self._compose_checkerboard(preview)
            pixmap = self._pil_to_pixmap(preview)
            self._img_widget.set_pixmap(pixmap, reset_offset=reset_offset)

            # Mise à jour compteur et navigation
            n = len(self._selected_entries)
            self._counter_lbl.setText(f"{self._current_idx + 1} / {n}")
            self._prev_btn.setEnabled(self._current_idx > 0)
            self._next_btn.setEnabled(self._current_idx < n - 1)

            # Zoom label
            self._zoom_lbl.setText(f"{int(self._img_widget.zoom_level() * 100)}%")

        except Exception as e:
            print(f"[adjustments_viewers_qt] display_image : {e}")

    def _apply_levels_preview(self, img):
        """Applique uniquement les niveaux (pipettes) pour la prévisualisation."""
        black_pt = self._settings.get('black_point', 0)
        white_pt = self._settings.get('white_point', 255)
        gamma    = self._settings.get('gamma', 1.0)

        if black_pt == 0 and white_pt == 255 and gamma == 1.0:
            return img.convert('RGB') if img.mode not in ('RGB', 'RGBA', 'L') else img

        img = img.convert('RGB')
        lut = []
        for i in range(256):
            n = (i - black_pt) / (white_pt - black_pt) if white_pt != black_pt else 0
            lut.append(int(pow(max(0, min(1, n)), 1.0 / gamma) * 255))
        r, g, b = img.split()
        return Image.merge('RGB', (r.point(lut), g.point(lut), b.point(lut)))

    @staticmethod
    def _pil_to_pixmap(img):
        """Convertit une image PIL en QPixmap."""
        if img.mode not in ('RGB', 'RGBA'):
            img = img.convert('RGB')
        buf = io.BytesIO()
        fmt = 'PNG' if img.mode == 'RGBA' else 'PNG'
        img.save(buf, format=fmt)
        buf.seek(0)
        qimg = QImage()
        qimg.loadFromData(buf.read())
        return QPixmap.fromImage(qimg)

    # ── Zoom ──────────────────────────────────────────────────────────────────

    def _adjust_zoom(self, delta):
        self._img_widget.adjust_zoom(delta)
        self._zoom_lbl.setText(f"{int(self._img_widget.zoom_level() * 100)}%")

    # ── Plein écran ───────────────────────────────────────────────────────────

    def toggle_fullscreen(self):
        if self._is_fullscreen:
            self.showNormal()
            self._is_fullscreen = False
        else:
            self.showFullScreen()
            self._is_fullscreen = True

    # ── Mode pipette (levels) ─────────────────────────────────────────────────

    def _activate_black_pipette(self):
        if self._pipette_mode == 'black':
            self._pipette_mode = None
            self._black_pip_btn.setChecked(False)
            self._img_widget.unsetCursor()
        else:
            self._pipette_mode = 'black'
            self._black_pip_btn.setChecked(True)
            self._white_pip_btn.setChecked(False)
            cursor = self._cursor_black or QCursor(Qt.CursorShape.CrossCursor)
            self._img_widget.setCursor(cursor)

    def _activate_white_pipette(self):
        if self._pipette_mode == 'white':
            self._pipette_mode = None
            self._white_pip_btn.setChecked(False)
            self._img_widget.unsetCursor()
        else:
            self._pipette_mode = 'white'
            self._white_pip_btn.setChecked(True)
            self._black_pip_btn.setChecked(False)
            cursor = self._cursor_white or QCursor(Qt.CursorShape.CrossCursor)
            self._img_widget.setCursor(cursor)

    # ── Undo/Redo niveaux (pipettes) ──────────────────────────────────────────

    def _levels_push_history(self):
        snap = (
            self._settings.get('black_point', 0),
            self._settings.get('white_point', 255),
            self._settings.get('gamma', 1.0),
        )
        self._levels_history.append(snap)
        self._levels_redo_stack.clear()
        self._levels_undo_btn.setEnabled(True)
        self._levels_redo_btn.setEnabled(False)

    def _levels_undo(self):
        if not self._levels_history:
            return
        current = (
            self._settings.get('black_point', 0),
            self._settings.get('white_point', 255),
            self._settings.get('gamma', 1.0),
        )
        self._levels_redo_stack.append(current)
        bp, wp, g = self._levels_history.pop()
        self._settings['black_point'] = bp
        self._settings['white_point'] = wp
        self._settings['gamma'] = g
        self._levels_undo_btn.setEnabled(bool(self._levels_history))
        self._levels_redo_btn.setEnabled(True)
        self._display_image()

    def _levels_redo(self):
        if not self._levels_redo_stack:
            return
        current = (
            self._settings.get('black_point', 0),
            self._settings.get('white_point', 255),
            self._settings.get('gamma', 1.0),
        )
        self._levels_history.append(current)
        bp, wp, g = self._levels_redo_stack.pop()
        self._settings['black_point'] = bp
        self._settings['white_point'] = wp
        self._settings['gamma'] = g
        self._levels_undo_btn.setEnabled(True)
        self._levels_redo_btn.setEnabled(bool(self._levels_redo_stack))
        self._display_image()

    def _on_auto_levels(self):
        """Calcule les points noir/blanc via percentiles 1%/99% et les applique à la page courante."""
        entry = self._selected_entries[self._current_idx]
        if not entry.get('bytes'):
            return
        black_val, white_val = _compute_auto_levels(entry['bytes'])
        self._levels_push_history()
        self._settings['black_point'] = black_val
        self._settings['white_point'] = white_val
        self._display_image()

    def _save_levels_state(self):
        if self._mode == 'levels':
            idx = self._current_idx
            self._levels_histories[idx] = list(self._levels_history)
            self._levels_redo_stacks[idx] = list(self._levels_redo_stack)
            self._levels_values[idx] = (
                self._settings.get('black_point', 0),
                self._settings.get('white_point', 255),
                self._settings.get('gamma', 1.0),
            )

    def _restore_levels_state(self):
        if self._mode == 'levels':
            idx = self._current_idx
            self._levels_history = list(self._levels_histories.get(idx, []))
            self._levels_redo_stack = list(self._levels_redo_stacks.get(idx, []))
            if idx in self._levels_values:
                bp, wp, g = self._levels_values[idx]
                self._settings['black_point'] = bp
                self._settings['white_point'] = wp
                self._settings['gamma'] = g
            else:
                self._settings['black_point'] = 0
                self._settings['white_point'] = 255
                self._settings['gamma'] = 1.0
            if hasattr(self, '_levels_undo_btn'):
                self._levels_undo_btn.setEnabled(bool(self._levels_history))
                self._levels_redo_btn.setEnabled(bool(self._levels_redo_stack))

    # ── Transparence : état par page ─────────────────────────────────────────

    def _save_transp_state(self):
        if self._mode == 'transparency':
            idx = self._current_idx
            self._transp_work_imgs[idx] = self._transp_work_img
            self._transp_histories[idx] = list(self._transp_history)
            self._transp_redo_stks[idx] = list(self._transp_redo_stk)

    def _restore_transp_state(self):
        if self._mode == 'transparency':
            idx = self._current_idx
            self._transp_work_img = self._transp_work_imgs.get(idx, None)
            self._transp_history  = list(self._transp_histories.get(idx, []))
            self._transp_redo_stk = list(self._transp_redo_stks.get(idx, []))
            if hasattr(self, '_transp_undo_btn'):
                self._transp_undo_btn.setEnabled(bool(self._transp_history))
                self._transp_redo_btn.setEnabled(bool(self._transp_redo_stk))

    # ── Transparence : undo/redo ──────────────────────────────────────────────

    def _transp_undo(self):
        if not self._transp_history:
            return
        self._transp_redo_stk.append(self._transp_work_img.copy())
        self._transp_work_img = self._transp_history.pop()
        self._transp_undo_btn.setEnabled(bool(self._transp_history))
        self._transp_redo_btn.setEnabled(True)
        self._display_image()

    def _transp_redo(self):
        if not self._transp_redo_stk:
            return
        self._transp_history.append(self._transp_work_img.copy())
        self._transp_work_img = self._transp_redo_stk.pop()
        self._transp_undo_btn.setEnabled(True)
        self._transp_redo_btn.setEnabled(bool(self._transp_redo_stk))
        self._display_image()

    # ── Transparence : damier ─────────────────────────────────────────────────

    @staticmethod
    def compose_checkerboard(rgba_img):
        """Retourne une image RGB avec les zones transparentes remplacées par un damier."""
        w, h = rgba_img.size
        tile = 12
        light, dark = (204, 204, 204), (128, 128, 128)
        checker = Image.new('RGB', (w, h))
        px = checker.load()
        for y in range(h):
            for x in range(w):
                px[x, y] = light if ((x // tile) + (y // tile)) % 2 == 0 else dark
        checker.paste(rgba_img, mask=rgba_img.split()[3])
        return checker

    def _compose_checkerboard(self, rgba_img):
        return self.compose_checkerboard(rgba_img)

    # ── Transparence : clic ───────────────────────────────────────────────────

    def _apply_transparency_click(self, px, py):
        """Flood fill ou global selon le mode, sur l'image de travail."""
        if self._transp_work_img is None:
            return

        # Sauvegarde pour undo
        self._transp_history.append(self._transp_work_img.copy())
        self._transp_redo_stk.clear()
        self._transp_undo_btn.setEnabled(True)
        self._transp_redo_btn.setEnabled(False)

        img = self._transp_work_img
        tolerance  = self._settings.get('transparency_tolerance', 30)
        transp_type = self._settings.get('transparency_type', 'flood')

        ref = img.getpixel((px, py))
        ref_r, ref_g, ref_b = ref[0], ref[1], ref[2]

        # Si déjà transparent, rien à faire
        if ref[3] == 0:
            return

        pixels = img.load()
        w, h = img.size

        if transp_type == 'flood':
            stack   = [(px, py)]
            visited = {(px, py)}
            while stack:
                cx, cy = stack.pop()
                p = pixels[cx, cy]
                if p[3] == 0:
                    continue
                if max(abs(p[0]-ref_r), abs(p[1]-ref_g), abs(p[2]-ref_b)) > tolerance:
                    continue
                pixels[cx, cy] = (p[0], p[1], p[2], 0)
                for nx, ny in ((cx-1, cy), (cx+1, cy), (cx, cy-1), (cx, cy+1)):
                    if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in visited:
                        visited.add((nx, ny))
                        stack.append((nx, ny))
        else:
            for y in range(h):
                for x in range(w):
                    p = pixels[x, y]
                    if p[3] == 0:
                        continue
                    if max(abs(p[0]-ref_r), abs(p[1]-ref_g), abs(p[2]-ref_b)) <= tolerance:
                        pixels[x, y] = (p[0], p[1], p[2], 0)

        self._display_image()

    # ── Transparence : application ────────────────────────────────────────────

    def _apply_transparency(self):
        """Applique toutes les pages modifiées et ferme."""
        # Sauvegarde la page courante avant d'appliquer
        self._save_transp_state()

        from modules.qt import state as _state_module

        state      = self._callbacks.get('state') or _state_module.state
        save_state = self._callbacks.get('save_state')
        render     = self._callbacks.get('render_mosaic')

        # Applique toutes les pages qui ont une image de travail
        applied = False
        for idx, work_img in self._transp_work_imgs.items():
            if work_img is None or idx >= len(self._selected_entries):
                continue
            entry = self._selected_entries[idx]
            try:
                if save_state:
                    save_state()

                output = io.BytesIO()
                ext = entry.get('extension', '').lower()
                if ext == '.ico':
                    fmt = 'ICO'
                elif ext == '.webp':
                    fmt = 'WEBP'
                else:
                    fmt = 'PNG'
                img_to_save = work_img if work_img.mode == 'RGBA' else work_img.convert('RGBA')
                img_to_save.save(output, format=fmt)

                entry['bytes']           = output.getvalue()
                entry['img']             = None
                entry['_thumbnail']      = None
                entry['large_thumb_pil'] = None
                entry['qt_pixmap_large'] = None
                entry['qt_qimage_large'] = None

                from modules.qt.comic_info import get_page_image_index, update_page_entries_in_xml_data
                _pidx = get_page_image_index(state, entry)
                if _pidx is not None:
                    update_page_entries_in_xml_data(state, [(_pidx, entry)])

                applied = True
            except Exception as e:
                print(f"[adjustments_viewers_qt] transparence apply : {e}")

        if applied:
            state.modified = True
            if save_state:
                save_state(force=True)
            if render:
                render()

        if self._on_close_callback:
            self._on_close_callback()
        self.accept()

    # ── Clic sur l'image (pipette levels + transparency) ─────────────────────

    def mousePressEvent(self, event):
        """Intercepte les clics sur _img_widget pour les pipettes (mode levels)."""
        super().mousePressEvent(event)

    # Override pour intercepter les clics sur le widget image
    def _on_image_click(self, pos_in_widget):
        """Appelé par _ImageScrollWidget quand l'utilisateur clique."""
        if self._mode == 'transparency':
            self._handle_transp_click(pos_in_widget)
            return
        if self._mode != 'levels' or not self._pipette_mode:
            return
        entry = self._selected_entries[self._current_idx]
        if not entry.get('bytes'):
            return
        try:
            img = Image.open(io.BytesIO(entry['bytes']))
            w_img, h_img = img.size
            zoom = self._img_widget.zoom_level()
            iw = self._img_widget.width()
            ih = self._img_widget.height()
            disp_w = int(w_img * zoom)
            disp_h = int(h_img * zoom)
            ox = self._img_widget._offset.x() + (iw - disp_w) // 2
            oy = self._img_widget._offset.y() + (ih - disp_h) // 2
            img_x = int((pos_in_widget.x() - ox) / zoom)
            img_y = int((pos_in_widget.y() - oy) / zoom)
            if img_x < 0 or img_y < 0 or img_x >= w_img or img_y >= h_img:
                return
            pixel = img.getpixel((img_x, img_y))
            if isinstance(pixel, tuple):
                lum = int(sum(pixel[:3]) / 3)
            else:
                lum = int(pixel)
            self._levels_push_history()
            if self._pipette_mode == 'black':
                self._settings['black_point'] = lum
            else:
                self._settings['white_point'] = lum
            self._display_image()
        except Exception as e:
            print(f"[adjustments_viewers_qt] pipette click : {e}")

    def _handle_transp_click(self, pos_in_widget):
        """Convertit les coordonnées écran → image et applique la transparence."""
        if self._transp_work_img is None:
            return
        w_img, h_img = self._transp_work_img.size
        zoom  = self._img_widget.zoom_level()
        iw    = self._img_widget.width()
        ih    = self._img_widget.height()
        disp_w = int(w_img * zoom)
        disp_h = int(h_img * zoom)
        ox = self._img_widget._offset.x() + (iw - disp_w) // 2
        oy = self._img_widget._offset.y() + (ih - disp_h) // 2
        img_x = int((pos_in_widget.x() - ox) / zoom)
        img_y = int((pos_in_widget.y() - oy) / zoom)
        if img_x < 0 or img_y < 0 or img_x >= w_img or img_y >= h_img:
            return
        self._apply_transparency_click(img_x, img_y)

    # ── Application ───────────────────────────────────────────────────────────

    def _apply_to_current(self):
        entry = self._selected_entries[self._current_idx]
        _apply_image_adjustments([entry], self._settings, callbacks=self._callbacks)
        self._reset_mode_slider()
        if self._on_close_callback:
            self._on_close_callback()
        self.accept()

    def _apply_to_all(self):
        _apply_image_adjustments(self._selected_entries, self._settings,
                                 callbacks=self._callbacks)
        self._reset_mode_slider()
        if self._on_close_callback:
            self._on_close_callback()
        self.accept()

    def _apply_levels_all(self):
        """Mode levels : applique les valeurs de niveaux individuelles à chaque page."""
        # Sauvegarde les valeurs de la page courante
        self._save_levels_state()

        applied_any = False
        for idx, entry in enumerate(self._selected_entries):
            if idx in self._levels_values:
                bp, wp, g = self._levels_values[idx]
            elif idx == self._current_idx:
                bp = self._settings.get('black_point', 0)
                wp = self._settings.get('white_point', 255)
                g  = self._settings.get('gamma', 1.0)
            else:
                continue  # page non modifiée
            if bp == 0 and wp == 255 and g == 1.0:
                continue
            page_settings = dict(self._settings)
            page_settings['black_point'] = bp
            page_settings['white_point'] = wp
            page_settings['gamma'] = g
            _apply_image_adjustments([entry], page_settings, callbacks=self._callbacks)
            applied_any = True

        # Réinitialise les niveaux dans les settings partagés
        self._settings['black_point'] = 0
        self._settings['white_point'] = 255
        self._settings['gamma'] = 1.0

        if self._on_close_callback:
            self._on_close_callback()
        self.accept()

    def _reset_mode_slider(self):
        """Remet le curseur du mode à zéro après application."""
        if self._mode == 'sharpness':
            self._settings['sharpness'] = 0
        elif self._mode == 'brightness':
            self._settings['brightness'] = 0
            self._settings['contrast'] = 0
        elif self._mode == 'saturation':
            self._settings['saturation'] = 0
        elif self._mode == 'unsharp':
            self._settings['unsharp_percent'] = 0
        # compression et remove_colors : pas de remise à zéro

    def _cancel(self):
        if self._on_cancel_callback:
            self._on_cancel_callback()
        self.reject()

    # ── Traduction ────────────────────────────────────────────────────────────

    def _get_title(self):
        titles = {
            'sharpness':     "dialogs.sharpness_viewer.title",
            'brightness':    "dialogs.brightness_viewer.title",
            'compression':   "dialogs.compression_viewer.title",
            'remove_colors': "dialogs.adjustments.effect_remove_colors",
            'saturation':    "dialogs.adjustments.saturation_viewer_title",
            'unsharp':       "dialogs.adjustments.unsharp_viewer_title",
            'levels':        "dialogs.levels_viewer.title",
            'transparency':  "dialogs.adjustments.transparency_viewer_title",
        }
        return _(titles.get(self._mode, "dialogs.levels_viewer.title"))

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(10)

        self.setWindowTitle(self._get_title())
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )

        # Bandeau d'avertissement (transparency : images ignorées)
        if self._skipped_count > 0:
            self._warning_lbl.setText(
                _("dialogs.adjustments.transparency_skipped_warning",
                  count=self._skipped_count))
            self._warning_lbl.setFont(font)

        # Labels toolbar selon mode
        if self._mode == 'sharpness':
            val = self._settings.get('sharpness', 0)
            self._sharp_lbl.setText(
                _("dialogs.adjustments.sharpness_label", value=val))
            self._sharp_lbl.setFont(font)
        elif self._mode == 'brightness':
            self._bright_lbl.setText(
                _("dialogs.adjustments.brightness_label",
                  value=self._settings.get('brightness', 0)))
            self._bright_lbl.setFont(font)
            self._contrast_lbl.setText(
                _("dialogs.adjustments.contrast_label",
                  value=self._settings.get('contrast', 0)))
            self._contrast_lbl.setFont(font)
        elif self._mode == 'compression':
            self._comp_lbl.setText(
                _("dialogs.adjustments.compression_quality_label",
                  value=self._settings.get('compression_quality', 100)))
            self._comp_lbl.setFont(font)
        elif self._mode == 'remove_colors':
            self._remove_lbl.setText(
                _("dialogs.adjustments.remove_colors_intensity_label",
                  value=self._settings.get('remove_colors_intensity', 0)))
            self._remove_lbl.setFont(font)
        elif self._mode == 'saturation':
            self._sat_lbl.setText(
                _("dialogs.adjustments.saturation_label",
                  value=self._settings.get('saturation', 0)))
            self._sat_lbl.setFont(font)
        elif self._mode == 'unsharp':
            self._unsharp_radius_lbl.setText(
                _("dialogs.adjustments.unsharp_radius_label",
                  value=self._settings.get('unsharp_radius', 2.0)))
            self._unsharp_radius_lbl.setFont(font)
            self._unsharp_percent_lbl.setText(
                _("dialogs.adjustments.unsharp_percent_label",
                  value=self._settings.get('unsharp_percent', 0)))
            self._unsharp_percent_lbl.setFont(font)
            self._unsharp_threshold_lbl.setText(
                _("dialogs.adjustments.unsharp_threshold_label",
                  value=self._settings.get('unsharp_threshold', 3)))
            self._unsharp_threshold_lbl.setFont(font)

        # Boutons toolbar
        self._prev_btn.setFont(_get_current_font(12))
        self._next_btn.setFont(_get_current_font(12))
        self._counter_lbl.setFont(font)
        self._zoom_lbl.setFont(font)

        font_btn = _get_current_font(11)
        self._cancel_btn.setFont(font_btn)
        self._cancel_btn.setText(_("buttons.cancel"))

        if self._mode == 'levels':
            self._apply_current_btn.setFont(font_btn)
            self._apply_current_btn.setText(_("dialogs.adjustments.transparency_apply"))
            self._black_pip_btn.setFont(font_btn)
            self._black_pip_btn.setText(_("dialogs.levels_viewer.black_pipette"))
            self._white_pip_btn.setFont(font_btn)
            self._white_pip_btn.setText(_("dialogs.levels_viewer.white_pipette"))
            self._auto_levels_btn.setFont(font_btn)
            self._auto_levels_btn.setText(_("dialogs.adjustments.auto_levels_button"))
        elif self._mode == 'transparency':
            self._apply_current_btn.setFont(font_btn)
            self._apply_current_btn.setText(_("dialogs.adjustments.transparency_apply"))
            self._transp_instr_lbl.setFont(font)
            self._transp_instr_lbl.setText(_("dialogs.adjustments.transparency_click_instruction"))
            self._transp_flood_lbl.setFont(font)
            self._transp_flood_lbl.setText(_("dialogs.adjustments.transparency_type_flood"))
            self._transp_global_lbl.setFont(font)
            self._transp_global_lbl.setText(_("dialogs.adjustments.transparency_type_global"))
            self._transp_tol_lbl.setFont(font)
            self._transp_tol_lbl.setText(_("dialogs.adjustments.transparency_tolerance_label"))
            self._update_transp_type_labels()
        else:
            self._apply_current_btn.setFont(font_btn)
            self._apply_current_btn.setText(_("dialogs.levels_viewer.apply_current"))
            if self._apply_all_btn:
                self._apply_all_btn.setFont(font_btn)
                self._apply_all_btn.setText(_("dialogs.levels_viewer.apply_all"))

    # ── Keyboard ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Left:
            self._prev_image()
        elif key == Qt.Key.Key_Right:
            self._next_image()
        elif key == Qt.Key.Key_Escape:
            if self._is_fullscreen:
                self.toggle_fullscreen()
            else:
                self._cancel()
        else:
            super().keyPressEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# Patch _ImageScrollWidget pour relayer les clics au dialog parent (pipettes)
# ─────────────────────────────────────────────────────────────────────────────

_orig_press = _ImageScrollWidget.mousePressEvent


def _patched_press(self, event):
    if event.button() == Qt.MouseButton.LeftButton:
        parent = self.parent()
        while parent:
            if isinstance(parent, AdjustmentViewerDialog):
                parent._on_image_click(event.position().toPoint())
                break
            parent = parent.parent()
    _orig_press(self, event)


_ImageScrollWidget.mousePressEvent = _patched_press
