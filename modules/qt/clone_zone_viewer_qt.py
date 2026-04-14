"""
modules/qt/clone_zone_viewer_qt.py — Visionneuse de clonage de zone (tampon clone)

L'utilisateur définit une zone source avec Ctrl+clic, puis peint la zone clonée
avec le bouton gauche de la souris (maintenu enfoncé).

Deux modes de source :
  - "fixe"    : la source reste ancrée au point Ctrl+cliqué à chaque coup de pinceau
  - "relative" : la source se déplace avec le curseur (décalage constant)

Classe publique :
  CloneZoneViewerDialog(parent, entry, callbacks)

Fonction publique :
  show_clone_zone_viewer(parent, callbacks)
"""

import io

from PIL import Image

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QFrame, QSizePolicy, QButtonGroup, QRadioButton, QSlider,
)
from PySide6.QtCore import Qt, QPoint, QSize
from PySide6.QtGui import (
    QPixmap, QImage, QCursor, QKeySequence, QShortcut, QIcon,
    QPainter, QPen, QColor, QBitmap,
)

from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_loader import resource_path
from modules.qt.font_manager_qt import get_current_font as _get_current_font


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


def _radio_style(theme):
    return (
        f"QRadioButton {{ color: {theme['text']}; }} "
        f"QRadioButton:disabled {{ color: #888888; }}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Curseur "cible" pour la sélection de la source (Ctrl enfoncé)
# ─────────────────────────────────────────────────────────────────────────────

def _make_crosshair_cursor(r_screen):
    """Retourne un QCursor en forme de cible avec croix, rayon r_screen px.
    Le pixmap est dimensionné dynamiquement selon le rayon."""
    r = max(1, r_screen)
    margin = 8          # espace autour du cercle pour la croix
    r_inner = max(1, r // 4)   # petit cercle central (visée fine)
    size = (r + margin) * 2 + 1
    cx = cy = size // 2

    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    pen_white = QPen(QColor(255, 255, 255, 200), 2.5)
    pen_black = QPen(QColor(0, 0, 0, 230), 1.5)

    # Cercle extérieur (taille brosse)
    painter.setPen(pen_white)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QPoint(cx, cy), r + 1, r + 1)
    painter.setPen(pen_black)
    painter.drawEllipse(QPoint(cx, cy), r, r)

    # Petit cercle central (point de visée)
    if r_inner > 1:
        painter.setPen(pen_white)
        painter.drawEllipse(QPoint(cx, cy), r_inner + 1, r_inner + 1)
        painter.setPen(pen_black)
        painter.drawEllipse(QPoint(cx, cy), r_inner, r_inner)

    # Croix (entre le cercle central et le cercle extérieur, + petit dépassement)
    gap = r_inner + 2
    ext = r + margin - 2

    def _hline(pen, y_off=0):
        painter.setPen(pen)
        painter.drawLine(cx - ext, cy + y_off, cx - gap, cy + y_off)
        painter.drawLine(cx + gap, cy + y_off, cx + ext, cy + y_off)

    def _vline(pen, x_off=0):
        painter.setPen(pen)
        painter.drawLine(cx + x_off, cy - ext, cx + x_off, cy - gap)
        painter.drawLine(cx + x_off, cy + gap, cx + x_off, cy + ext)

    _hline(pen_white, y_off=-1)
    _hline(pen_black)
    _vline(pen_white, x_off=-1)
    _vline(pen_black)

    painter.end()

    return QCursor(pm, cx, cy)


# ─────────────────────────────────────────────────────────────────────────────
# Constante : rayon du tampon de clonage (px image)
# ─────────────────────────────────────────────────────────────────────────────

_BRUSH_RADIUS_DEFAULT = 20
_BRUSH_RADIUS_MIN     = 1
_BRUSH_RADIUS_MAX     = 200


# ─────────────────────────────────────────────────────────────────────────────
# Widget image avec interactions de clonage
# ─────────────────────────────────────────────────────────────────────────────

class _CloneImageWidget(QWidget):
    """Affiche une image avec zoom, pan clic-droit, et tampon clone clic-gauche.

    Ctrl+clic gauche : définir la source.
    Clic gauche (maintenu) : appliquer le tampon sur l'image de travail.
    Clic droit (maintenu) : pan.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: black;")

        self._zoom = 1.0
        self._offset = QPoint(0, 0)
        self._pan_start = None
        self._pixmap = None         # Pixmap affiché (mis à jour à chaque coup de tampon)

        # Source du clonage (coordonnées IMAGE)
        self._source_pt = None      # QPoint image (origine de clonage)
        self._painting = False      # Peinture en cours (clic maintenu)
        self._paint_last = None     # Dernier point widget peint (pour interpolation)

        # Indicateur visuel de la source (coordonnées WIDGET, recalculées)
        self._source_widget_pt = None
        # Position live du marqueur pendant le stroke (suit la source effective)
        self._live_source_widget_pt = None

        # Mode : 'fixed' ou 'relative'
        self._mode = 'fixed'

        # Rayon du tampon (mis à jour par le dialog via set_brush_radius)
        self._brush_radius = _BRUSH_RADIUS_DEFAULT

        # Callbacks
        self.on_zoom_changed = None         # callback(zoom_level)
        self.on_source_defined = None       # callback(img_x, img_y)
        self.on_paint_stroke = None         # callback(img_x, img_y) → appelé pendant le drag
        self.on_paint_move = None           # callback(img_x, img_y) → mouvement sans peindre (suivi marqueur)
        self.on_paint_end = None            # callback() → fin du stroke (mouseRelease)

        self._crosshair_cursor = _make_crosshair_cursor(_BRUSH_RADIUS_DEFAULT)
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    # ── API publique ──────────────────────────────────────────────────────────

    def set_pixmap(self, pixmap, reset_offset=True):
        self._pixmap = pixmap
        if reset_offset:
            self._offset = QPoint(0, 0)
        self._source_pt = None
        self._source_widget_pt = None
        self._painting = False
        self._paint_last = None
        self.update()

    def update_pixmap(self, pixmap):
        """Met à jour le pixmap sans réinitialiser l'offset ni la source."""
        self._pixmap = pixmap
        self._recalc_source_widget_pt()
        self.update()

    def zoom_level(self):
        return self._zoom

    def set_zoom(self, z):
        self._zoom = max(0.1, min(10.0, z))
        self._recalc_source_widget_pt()
        self._rebuild_crosshair_cursor()
        self.update()
        if self.on_zoom_changed:
            self.on_zoom_changed(self._zoom)

    def adjust_zoom(self, delta):
        self.set_zoom(self._zoom + delta)

    def reset_zoom(self):
        self._zoom = 1.0
        self._offset = QPoint(0, 0)
        self._recalc_source_widget_pt()
        self.update()
        if self.on_zoom_changed:
            self.on_zoom_changed(self._zoom)

    def set_mode(self, mode):
        """'fixed' ou 'relative'."""
        self._mode = mode

    def set_brush_radius(self, r):
        self._brush_radius = r
        self._rebuild_crosshair_cursor()

    def _rebuild_crosshair_cursor(self):
        r_screen = max(1, int(self._brush_radius * self._zoom))
        self._crosshair_cursor = _make_crosshair_cursor(r_screen)

    def clear_source(self):
        self._source_pt = None
        self._source_widget_pt = None
        self.update()

    # ── Rendu ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        if not self._pixmap:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = int(self._pixmap.width() * self._zoom)
        h = int(self._pixmap.height() * self._zoom)
        x = self._offset.x() + (self.width() - w) // 2
        y = self._offset.y() + (self.height() - h) // 2

        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        painter.drawPixmap(x, y, w, h, self._pixmap)

        # Marqueur de source : live pendant le stroke, sinon position Ctrl+clic
        marker = self._live_source_widget_pt if self._painting else self._source_widget_pt
        if marker is not None:
            r_screen = max(1, int(self._brush_radius * self._zoom))
            self._draw_source_marker(painter, marker, r_screen)

    @staticmethod
    def _draw_source_marker(painter, pt, r):
        """Dessine une cible (cercle + croix) sur la zone source, rayon r en pixels écran."""
        from PySide6.QtCore import QRectF
        # Halo blanc
        painter.setPen(QPen(QColor(255, 255, 255, 180), 2.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(pt.x() - r - 1, pt.y() - r - 1, (r + 1) * 2, (r + 1) * 2))
        # Cercle rouge
        painter.setPen(QPen(QColor(255, 80, 80), 2))
        painter.drawEllipse(QRectF(pt.x() - r, pt.y() - r, r * 2, r * 2))
        # Croix rouge (petite, indépendante du rayon de brosse)
        c = 6
        painter.drawLine(QPoint(pt.x() - r - c, pt.y()), QPoint(pt.x() + r + c, pt.y()))
        painter.drawLine(QPoint(pt.x(), pt.y() - r - c), QPoint(pt.x(), pt.y() + r + c))

    # ── Coordonnées ──────────────────────────────────────────────────────────

    def _img_origin(self):
        """Retourne (ox, oy) : coordonnées widget du coin supérieur gauche de l'image."""
        if not self._pixmap:
            return 0, 0
        w = int(self._pixmap.width() * self._zoom)
        h = int(self._pixmap.height() * self._zoom)
        return (self._offset.x() + (self.width() - w) // 2,
                self._offset.y() + (self.height() - h) // 2)

    def _widget_to_image(self, pt):
        ox, oy = self._img_origin()
        ix = int((pt.x() - ox) / self._zoom)
        iy = int((pt.y() - oy) / self._zoom)
        return ix, iy

    def _image_to_widget(self, ix, iy):
        ox, oy = self._img_origin()
        wx = int(ix * self._zoom + ox)
        wy = int(iy * self._zoom + oy)
        return QPoint(wx, wy)

    def _recalc_source_widget_pt(self):
        if self._source_pt is not None:
            self._source_widget_pt = self._image_to_widget(
                self._source_pt.x(), self._source_pt.y())
        else:
            self._source_widget_pt = None

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

    # ── Événements souris ─────────────────────────────────────────────────────

    def wheelEvent(self, event):
        delta = 0.15 if event.angleDelta().y() > 0 else -0.15
        self.adjust_zoom(delta)
        event.accept()

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        if event.button() == Qt.MouseButton.RightButton:
            self._pan_start = pos
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            event.accept()
        elif event.button() == Qt.MouseButton.LeftButton:
            mods = event.modifiers()
            if mods & Qt.KeyboardModifier.ControlModifier:
                # Définit la zone source
                ix, iy = self._widget_to_image(pos)
                self._source_pt = QPoint(ix, iy)
                self._source_widget_pt = pos
                if self.on_source_defined:
                    self.on_source_defined(ix, iy)
                self.update()
            else:
                # Début du stroke de peinture
                if self._source_pt is not None:
                    self._painting = True
                    self._paint_last = pos
                    ix, iy = self._widget_to_image(pos)
                    if self.on_paint_stroke:
                        self.on_paint_stroke(ix, iy)
            event.accept()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._pan_start is not None:
            diff = pos - self._pan_start
            self._offset += diff
            self._pan_start = pos
            self._clamp_offset()
            self._recalc_source_widget_pt()
            self.update()
        elif self._painting:
            iix, iiy = self._widget_to_image(pos)
            # Notifie le dialog pour mettre à jour le marqueur source live
            if self.on_paint_move:
                self.on_paint_move(iix, iiy)
            # Interpolation si déplacement rapide
            if self._paint_last is not None:
                dx = pos.x() - self._paint_last.x()
                dy = pos.y() - self._paint_last.y()
                dist = (dx * dx + dy * dy) ** 0.5
                step = max(1, int(self._zoom * self._brush_radius * 0.5))
                if dist >= step:
                    steps = max(1, int(dist / step))
                    for i in range(1, steps + 1):
                        t = i / steps
                        ix = self._paint_last.x() + int(dx * t)
                        iy = self._paint_last.y() + int(dy * t)
                        iix2, iiy2 = self._widget_to_image(QPoint(ix, iy))
                        if self.on_paint_stroke:
                            self.on_paint_stroke(iix2, iiy2)
                    self._paint_last = pos
            else:
                if self.on_paint_stroke:
                    self.on_paint_stroke(iix, iiy)
                self._paint_last = pos
        else:
            # Mise à jour du curseur selon Ctrl enfoncé
            mods = event.modifiers()
            if mods & Qt.KeyboardModifier.ControlModifier:
                self.setCursor(self._crosshair_cursor)
            else:
                self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._pan_start = None
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            event.accept()
        elif event.button() == Qt.MouseButton.LeftButton:
            if self._painting:
                self._painting = False
                self._paint_last = None
                if self.on_paint_end:
                    self.on_paint_end()
            event.accept()

    def keyPressEvent(self, event):
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            self.setCursor(self._crosshair_cursor)
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        mods = event.modifiers()
        if not (mods & Qt.KeyboardModifier.ControlModifier):
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        super().keyReleaseEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# Visionneuse principale
# ─────────────────────────────────────────────────────────────────────────────

class CloneZoneViewerDialog(QDialog):
    """Visionneuse de clonage de zone.

    Paramètres :
      entry     : dict image (avec 'bytes')
      callbacks : dict MosaicView (save_state, render_mosaic, state)
    """

    def __init__(self, parent, entry, callbacks=None):
        super().__init__(parent)
        self._entry = entry
        self._callbacks = callbacks or {}

        # Historique undo/redo (stocke bytes AVANT chaque stroke)
        self._bytes_history = []    # [bytes_before_stroke1, ...]
        self._redo_stack = []       # [bytes_to_redo1, ...]

        # Image de travail PIL (RGBA, modifiée en place)
        self._work_img = None
        self._checker_bg = None   # Damier RGB précalculé (même taille que _work_img)

        # Source de clonage (coordonnées IMAGE PIL)
        self._source_pt = None      # (ix, iy)
        # Point de départ du stroke courant (pour le mode relatif)
        self._stroke_start_dest = None  # (ix, iy) premier point du stroke courant
        self._stroke_start_src = None   # (ix, iy) source au début du stroke

        # Taille du tampon (rayon en px image)
        self._brush_radius = 20

        # Sauvegarde avant chaque stroke (pour undo)
        self._bytes_before_stroke = None
        self._stroke_dirty = False        # Le stroke courant a-t-il modifié l'image ?
        self._stroke_snapshot = None      # Copie PIL de l'image au début du stroke (lecture seule)
        self._stroke_last_dest = None     # Dernier point destination du stroke (pour mode relatif)

        self._is_fullscreen = False

        theme = get_current_theme()
        self.setWindowTitle(_("dialogs.clone_zone_viewer.title"))
        self.resize(900, 700)
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )

        self._build_ui(theme)
        _connect_lang(self, self._retranslate)
        self._center_parent = parent

        QShortcut(QKeySequence("F11"), self).activated.connect(self._toggle_fullscreen)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self._img_widget.reset_zoom)
        QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self._undo)
        QShortcut(QKeySequence("Ctrl+Y"), self).activated.connect(self._redo)

        self._load_work_image()
        self._display_image(reset_offset=True)

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    # ── Construction UI ───────────────────────────────────────────────────────

    def _build_ui(self, theme):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

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

        # ── Toolbar ───────────────────────────────────────────────────────────
        tb = QWidget()
        tb.setFixedHeight(50)
        tb.setStyleSheet(f"background: {theme['toolbar_bg']}; color: {theme['text']};")
        tb_layout = QHBoxLayout(tb)
        tb_layout.setContentsMargins(8, 4, 8, 4)
        tb_layout.setSpacing(6)

        font_tb = _get_current_font(10)

        # Message instruction — centré dans la toolbar
        self._instr_lbl = QLabel()
        self._instr_lbl.setFont(font_tb)
        self._instr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._instr_lbl.setWordWrap(False)
        tb_layout.addWidget(self._instr_lbl, stretch=1)

        # Séparateur
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {theme['separator']};")
        tb_layout.addWidget(sep)

        # Zoom − % +
        _icon_minus = _load_icon("BTN_-.png", 20)
        self._zoom_minus_btn = QPushButton()
        self._zoom_minus_btn.setFixedSize(28, 28)
        self._zoom_minus_btn.setStyleSheet(_btn_style(theme))
        self._zoom_minus_btn.clicked.connect(lambda: self._adjust_zoom(-0.15))
        if _icon_minus:
            self._zoom_minus_btn.setIcon(_icon_minus)
            self._zoom_minus_btn.setIconSize(QSize(20, 20))
        else:
            self._zoom_minus_btn.setText("−")
            self._zoom_minus_btn.setFont(font_tb)
        tb_layout.addWidget(self._zoom_minus_btn)

        self._zoom_lbl = QLabel("100%")
        self._zoom_lbl.setFont(font_tb)
        self._zoom_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_lbl.setMinimumWidth(48)
        tb_layout.addWidget(self._zoom_lbl)

        _icon_plus = _load_icon("BTN_+.png", 20)
        self._zoom_plus_btn = QPushButton()
        self._zoom_plus_btn.setFixedSize(28, 28)
        self._zoom_plus_btn.setStyleSheet(_btn_style(theme))
        self._zoom_plus_btn.clicked.connect(lambda: self._adjust_zoom(0.15))
        if _icon_plus:
            self._zoom_plus_btn.setIcon(_icon_plus)
            self._zoom_plus_btn.setIconSize(QSize(20, 20))
        else:
            self._zoom_plus_btn.setText("+")
            self._zoom_plus_btn.setFont(font_tb)
        tb_layout.addWidget(self._zoom_plus_btn)

        root.addWidget(tb)

        # ── Zone image ────────────────────────────────────────────────────────
        self._img_widget = _CloneImageWidget()
        self._img_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._img_widget.on_zoom_changed = lambda z: self._zoom_lbl.setText(
            f"{int(z * 100)}%")
        self._img_widget.on_source_defined = self._on_source_defined
        self._img_widget.on_paint_stroke = self._on_paint_stroke
        self._img_widget.on_paint_move = self._on_paint_move
        self._img_widget.on_paint_end = self._on_paint_end
        root.addWidget(self._img_widget, stretch=1)

        # ── Barre du bas ──────────────────────────────────────────────────────
        bot = QWidget()
        bot.setStyleSheet(f"background: {theme['bg']}; color: {theme['text']};")
        bot_layout = QHBoxLayout(bot)
        bot_layout.setContentsMargins(10, 6, 10, 6)
        bot_layout.setSpacing(8)

        font_btn = _get_current_font(11)
        font_radio = _get_current_font(10)

        # Mode source : radios
        self._lbl_mode = QLabel()
        self._lbl_mode.setFont(font_btn)
        bot_layout.addWidget(self._lbl_mode)

        self._radio_fixed = QRadioButton()
        self._radio_fixed.setFont(font_radio)
        self._radio_fixed.setStyleSheet(_radio_style(theme))
        self._radio_fixed.setChecked(True)
        self._radio_fixed.toggled.connect(self._on_mode_changed)
        bot_layout.addWidget(self._radio_fixed)

        self._radio_relative = QRadioButton()
        self._radio_relative.setFont(font_radio)
        self._radio_relative.setStyleSheet(_radio_style(theme))
        bot_layout.addWidget(self._radio_relative)

        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._radio_fixed)
        self._mode_group.addButton(self._radio_relative)

        # Séparateur
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet(f"color: {theme['separator']};")
        bot_layout.addWidget(sep2)

        # Taille du tampon
        self._lbl_brush = QLabel()
        self._lbl_brush.setFont(font_btn)
        bot_layout.addWidget(self._lbl_brush)

        self._brush_slider = QSlider(Qt.Orientation.Horizontal)
        self._brush_slider.setMinimum(_BRUSH_RADIUS_MIN)
        self._brush_slider.setMaximum(_BRUSH_RADIUS_MAX)
        self._brush_slider.setValue(_BRUSH_RADIUS_DEFAULT)
        self._brush_slider.setFixedWidth(120)
        self._brush_slider.valueChanged.connect(self._on_brush_size_changed)
        bot_layout.addWidget(self._brush_slider)

        self._lbl_brush_val = QLabel(str(_BRUSH_RADIUS_DEFAULT))
        self._lbl_brush_val.setFont(font_btn)
        self._lbl_brush_val.setMinimumWidth(30)
        bot_layout.addWidget(self._lbl_brush_val)

        # Séparateur
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.VLine)
        sep3.setStyleSheet(f"color: {theme['separator']};")
        bot_layout.addWidget(sep3)

        # Undo
        icon_undo = _load_icon("BTN_Batch_Undo.png", 22)
        self._undo_btn = QPushButton()
        self._undo_btn.setFixedSize(32, 32)
        self._undo_btn.setStyleSheet(_btn_style(theme))
        self._undo_btn.setEnabled(False)
        if icon_undo:
            self._undo_btn.setIcon(icon_undo)
            self._undo_btn.setIconSize(QSize(22, 22))
        else:
            self._undo_btn.setText("↩")
            self._undo_btn.setFont(font_btn)
        self._undo_btn.clicked.connect(self._undo)
        bot_layout.addWidget(self._undo_btn)

        # Redo
        icon_redo = _load_icon("BTN_Batch_Redo.png", 22)
        self._redo_btn = QPushButton()
        self._redo_btn.setFixedSize(32, 32)
        self._redo_btn.setStyleSheet(_btn_style(theme))
        self._redo_btn.setEnabled(False)
        if icon_redo:
            self._redo_btn.setIcon(icon_redo)
            self._redo_btn.setIconSize(QSize(22, 22))
        else:
            self._redo_btn.setText("↪")
            self._redo_btn.setFont(font_btn)
        self._redo_btn.clicked.connect(self._redo)
        bot_layout.addWidget(self._redo_btn)

        bot_layout.addStretch()

        # Bouton Fermer
        self._close_btn = QPushButton()
        self._close_btn.setFont(font_btn)
        self._close_btn.setStyleSheet(_btn_style(theme))
        self._close_btn.clicked.connect(self.reject)
        bot_layout.addWidget(self._close_btn)

        root.addWidget(bot)

        self._retranslate()

    # ── Chargement de l'image de travail ──────────────────────────────────────

    def _load_work_image(self):
        """Charge l'image de l'entrée en PIL RGBA comme image de travail et précalcule le damier."""
        if not self._entry.get('bytes'):
            return
        try:
            img = Image.open(io.BytesIO(self._entry['bytes']))
            self._work_img = img.convert('RGBA')
            self._checker_bg = self._make_checker(self._work_img.width, self._work_img.height)
        except Exception as e:
            print(f"[clone_zone_viewer_qt] load_work_image : {e}")

    @staticmethod
    def _make_checker(w, h, tile=12):
        """Génère un fond damier RGB de taille (w, h) sans boucle Python."""
        import array as _array
        light_val, dark_val = 204, 128
        row_light = bytes(_array.array('B', [
            light_val if (x // tile) % 2 == 0 else dark_val for x in range(w)
        ]))
        row_dark = bytes(_array.array('B', [
            dark_val if (x // tile) % 2 == 0 else light_val for x in range(w)
        ]))
        rows = []
        for y in range(h):
            rows.append(row_light if (y // tile) % 2 == 0 else row_dark)
        checker_l = Image.frombytes('L', (w, h), b''.join(rows))
        return checker_l.convert('RGB')

    # ── Affichage image ───────────────────────────────────────────────────────

    def _display_image(self, reset_offset=False):
        """Rafraîchit le pixmap depuis self._work_img."""
        if self._work_img is None:
            return
        try:
            pixmap = self._work_img_to_pixmap(self._work_img, self._checker_bg)
            if reset_offset:
                self._img_widget.set_pixmap(pixmap, reset_offset=True)
            else:
                self._img_widget.update_pixmap(pixmap)
            self._zoom_lbl.setText(f"{int(self._img_widget.zoom_level() * 100)}%")
        except Exception as e:
            print(f"[clone_zone_viewer_qt] display_image : {e}")

    @staticmethod
    def _work_img_to_pixmap(rgba_img, checker_bg):
        """Compose rgba_img sur checker_bg et retourne un QPixmap.
        Utilise PIL paste (C natif) + tobytes raw — pas de PNG, pas de boucle Python."""
        w, h = rgba_img.size
        display = checker_bg.copy()
        display.paste(rgba_img.convert('RGB'), mask=rgba_img.split()[3])
        raw = display.tobytes('raw', 'RGB')
        qimg = QImage(raw, w, h, w * 3, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimg.copy())

    # ── Zoom ──────────────────────────────────────────────────────────────────

    def _adjust_zoom(self, delta):
        self._img_widget.adjust_zoom(delta)
        self._zoom_lbl.setText(f"{int(self._img_widget.zoom_level() * 100)}%")

    # ── Plein écran ───────────────────────────────────────────────────────────

    def _toggle_fullscreen(self):
        if self._is_fullscreen:
            self.showNormal()
            self._is_fullscreen = False
        else:
            self.showFullScreen()
            self._is_fullscreen = True

    # ── Mode source ──────────────────────────────────────────────────────────

    def _on_brush_size_changed(self, value):
        self._brush_radius = value
        self._img_widget.set_brush_radius(value)
        self._lbl_brush_val.setText(str(value))

    def _on_mode_changed(self):
        if self._radio_fixed.isChecked():
            self._img_widget.set_mode('fixed')
        else:
            self._img_widget.set_mode('relative')

    # ── Callbacks de clonage ─────────────────────────────────────────────────

    def _on_source_defined(self, ix, iy):
        """Ctrl+clic : définit le point source."""
        self._source_pt = (ix, iy)

    def _on_paint_move(self, dest_x, dest_y):
        """Mouvement pendant le stroke : met à jour le marqueur source live."""
        src_x, src_y = self._get_effective_source(dest_x, dest_y)
        self._img_widget._live_source_widget_pt = self._img_widget._image_to_widget(src_x, src_y)
        self._img_widget.update()

    def _on_paint_stroke(self, ix, iy):
        """Clic gauche maintenu : applique le tampon à la position (ix, iy) de destination."""
        if self._source_pt is None or self._work_img is None:
            return

        # Première application du stroke : sauvegarde pour undo + snapshot de lecture (mode fixe)
        if not self._stroke_dirty:
            self._bytes_before_stroke = self._entry['bytes']
            self._stroke_start_dest = (ix, iy)
            self._stroke_start_src = self._source_pt
            # Mode fixe : snapshot figé au début du stroke (la source ne change pas)
            # Mode relatif : pas de snapshot — on lit dans _work_img directement
            #   (la source se déplace avec le pinceau, donc elle ne chevauche jamais la destination)
            if self._img_widget._mode == 'fixed':
                self._stroke_snapshot = self._work_img.copy()
            else:
                self._stroke_snapshot = self._work_img  # référence directe, pas de copie
            self._stroke_dirty = True

        src_x, src_y = self._get_effective_source(ix, iy)
        self._apply_stamp(ix, iy, src_x, src_y)
        self._stroke_last_dest = (ix, iy)
        self._display_image(reset_offset=False)

    def _get_effective_source(self, dest_x, dest_y):
        """Calcule la source effective : décalage constant (source initiale - dest initiale) appliqué à dest courante.
        Les deux modes utilisent le même calcul pendant le stroke.
        La différence est entre les strokes : fixe repart du même point, relatif avance."""
        if self._stroke_start_dest is None:
            return self._source_pt
        dx = self._stroke_start_src[0] - self._stroke_start_dest[0]
        dy = self._stroke_start_src[1] - self._stroke_start_dest[1]
        return (dest_x + dx, dest_y + dy)

    def _apply_stamp(self, dest_x, dest_y, src_x, src_y):
        """Copie un disque de rayon _BRUSH_RADIUS depuis le snapshot vers _work_img.
        Utilise crop/paste PIL (C natif) avec un masque circulaire."""
        snap = self._stroke_snapshot
        dst = self._work_img
        w, h = dst.size
        r = self._brush_radius

        # Bounding box de destination (clampée à l'image)
        d_left  = max(0, dest_x - r)
        d_top   = max(0, dest_y - r)
        d_right = min(w, dest_x + r + 1)
        d_bottom = min(h, dest_y + r + 1)
        if d_left >= d_right or d_top >= d_bottom:
            return

        # Bounding box source correspondante (même décalage)
        s_left  = src_x - (dest_x - d_left)
        s_top   = src_y - (dest_y - d_top)
        s_right  = s_left + (d_right - d_left)
        s_bottom = s_top  + (d_bottom - d_top)

        bw = d_right - d_left
        bh = d_bottom - d_top

        # Crop source depuis le snapshot (avec clamping si hors image)
        sc_left   = max(0, s_left)
        sc_top    = max(0, s_top)
        sc_right  = min(w, s_right)
        sc_bottom = min(h, s_bottom)
        if sc_left >= sc_right or sc_top >= sc_bottom:
            return

        src_crop = snap.crop((sc_left, sc_top, sc_right, sc_bottom))

        # Offset de collage si la source a été clampée
        paste_x = d_left + (sc_left - s_left)
        paste_y = d_top  + (sc_top  - s_top)

        pw = sc_right - sc_left
        ph = sc_bottom - sc_top

        # Masque circulaire pour la zone de collage
        mask = Image.new('L', (pw, ph), 0)
        import math
        cx = dest_x - paste_x
        cy = dest_y - paste_y
        from PIL import ImageDraw
        draw = ImageDraw.Draw(mask)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)

        dst.paste(src_crop, (paste_x, paste_y), mask=mask)

    def _on_paint_end(self):
        """Fin du stroke : commit dans l'entrée + undo/redo."""
        if not self._stroke_dirty:
            return

        from modules.qt import state as _state_module
        state = self._callbacks.get('state') or _state_module.state
        save_state = self._callbacks.get('save_state')
        render = self._callbacks.get('render_mosaic')

        try:
            if save_state:
                save_state()

            # Sauvegarde l'image de travail dans l'entrée
            self._commit_work_image(state)

            if save_state:
                save_state(force=True)

            # Empile pour undo
            self._bytes_history.append(self._bytes_before_stroke)
            self._redo_stack.clear()
            self._update_undo_redo_buttons()

            if render:
                render()

        except Exception as e:
            import traceback
            print(f"[clone_zone_viewer_qt] paint_end : {e}")
            traceback.print_exc()

        self._bytes_before_stroke = None
        self._stroke_dirty = False
        self._stroke_snapshot = None

        if self._img_widget._mode == 'fixed':
            # Mode fixe : le prochain stroke repart exactement du même point source
            self._stroke_start_dest = None
            self._stroke_start_src = None
        else:
            # Mode relatif : avance le point source du déplacement effectué pendant ce stroke
            if (self._stroke_start_dest is not None and
                    self._stroke_last_dest is not None and
                    self._stroke_start_src is not None):
                ddx = self._stroke_last_dest[0] - self._stroke_start_dest[0]
                ddy = self._stroke_last_dest[1] - self._stroke_start_dest[1]
                self._source_pt = (self._stroke_start_src[0] + ddx,
                                   self._stroke_start_src[1] + ddy)
                self._img_widget._source_pt = QPoint(*self._source_pt)
                self._img_widget._recalc_source_widget_pt()
            self._stroke_start_dest = None
            self._stroke_start_src = None

        self._stroke_last_dest = None
        self._img_widget._live_source_widget_pt = None
        self._img_widget.update()

    def _commit_work_image(self, state):
        """Encode self._work_img → entry['bytes'] en conservant le format original."""
        from modules.qt.entries import save_image_to_bytes
        from modules.qt.comic_info import get_page_image_index, update_page_entries_in_xml_data

        # Convertit l'image de travail dans le mode du format d'origine
        orig_mode = self._entry.get('_orig_mode', 'RGBA')
        out_img = self._work_img
        if orig_mode not in ('RGBA', 'LA', 'P') and not self._entry.get('extension', '').lower() in ('.png', '.webp'):
            # Format sans alpha : aplatit sur blanc
            bg = Image.new('RGB', out_img.size, (255, 255, 255))
            bg.paste(out_img, mask=out_img.split()[3])
            out_img = bg

        self._entry['img'] = out_img.copy()
        self._entry['bytes'] = save_image_to_bytes(self._entry)
        self._entry['img'] = None
        self._entry['_thumbnail'] = None
        self._entry['large_thumb_pil'] = None
        self._entry['qt_pixmap_large'] = None
        self._entry['qt_qimage_large'] = None

        pidx = get_page_image_index(state, self._entry)
        if pidx is not None:
            update_page_entries_in_xml_data(state, [(pidx, self._entry)])

        state.modified = True

    # ── Undo / Redo ──────────────────────────────────────────────────────────

    def _undo(self):
        if not self._bytes_history:
            return

        from modules.qt import state as _state_module
        state = self._callbacks.get('state') or _state_module.state
        save_state = self._callbacks.get('save_state')
        render = self._callbacks.get('render_mosaic')

        try:
            bytes_current = self._entry['bytes']

            if save_state:
                save_state()

            bytes_before = self._bytes_history.pop()
            self._entry['bytes'] = bytes_before
            self._entry['img'] = None
            self._entry['_thumbnail'] = None
            self._entry['large_thumb_pil'] = None
            self._entry['qt_pixmap_large'] = None
            self._entry['qt_qimage_large'] = None

            from modules.qt.comic_info import get_page_image_index, update_page_entries_in_xml_data
            pidx = get_page_image_index(state, self._entry)
            if pidx is not None:
                update_page_entries_in_xml_data(state, [(pidx, self._entry)])

            state.modified = True
            if save_state:
                save_state(force=True)

            self._redo_stack.append(bytes_current)
            self._update_undo_redo_buttons()

            # Recharge l'image de travail depuis les bytes restaurés
            self._load_work_image()
            self._img_widget.clear_source()
            self._source_pt = None
            self._display_image()

            if render:
                render()

        except Exception as e:
            print(f"[clone_zone_viewer_qt] undo : {e}")

    def _redo(self):
        if not self._redo_stack:
            return

        from modules.qt import state as _state_module
        state = self._callbacks.get('state') or _state_module.state
        save_state = self._callbacks.get('save_state')
        render = self._callbacks.get('render_mosaic')

        try:
            bytes_current = self._entry['bytes']

            if save_state:
                save_state()

            bytes_redo = self._redo_stack.pop()
            self._entry['bytes'] = bytes_redo
            self._entry['img'] = None
            self._entry['_thumbnail'] = None
            self._entry['large_thumb_pil'] = None
            self._entry['qt_pixmap_large'] = None
            self._entry['qt_qimage_large'] = None

            from modules.qt.comic_info import get_page_image_index, update_page_entries_in_xml_data
            pidx = get_page_image_index(state, self._entry)
            if pidx is not None:
                update_page_entries_in_xml_data(state, [(pidx, self._entry)])

            state.modified = True
            if save_state:
                save_state(force=True)

            self._bytes_history.append(bytes_current)
            self._update_undo_redo_buttons()

            self._load_work_image()
            self._img_widget.clear_source()
            self._source_pt = None
            self._display_image()

            if render:
                render()

        except Exception as e:
            print(f"[clone_zone_viewer_qt] redo : {e}")

    def _update_undo_redo_buttons(self):
        self._undo_btn.setEnabled(bool(self._bytes_history))
        self._redo_btn.setEnabled(bool(self._redo_stack))

    # ── Traduction ────────────────────────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(10)
        font_btn = _get_current_font(11)
        font_radio = _get_current_font(10)

        self.setWindowTitle(_("dialogs.clone_zone_viewer.title"))
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )

        self._instr_lbl.setText(_("dialogs.clone_zone_viewer.instruction"))
        self._instr_lbl.setFont(font)

        self._zoom_lbl.setFont(font)

        self._lbl_brush.setText(_("dialogs.clone_zone_viewer.brush_size_label"))
        self._lbl_brush.setFont(font_btn)
        self._lbl_brush_val.setFont(font_btn)

        self._lbl_mode.setText(_("dialogs.clone_zone_viewer.mode_label"))
        self._lbl_mode.setFont(font_btn)

        self._radio_fixed.setText(_("dialogs.clone_zone_viewer.mode_fixed"))
        self._radio_fixed.setFont(font_radio)
        self._radio_fixed.setStyleSheet(_radio_style(theme))

        self._radio_relative.setText(_("dialogs.clone_zone_viewer.mode_relative"))
        self._radio_relative.setFont(font_radio)
        self._radio_relative.setStyleSheet(_radio_style(theme))

        self._close_btn.setText(_("buttons.close"))
        self._close_btn.setFont(font_btn)
        self._close_btn.setStyleSheet(_btn_style(theme))

        self._zoom_minus_btn.setStyleSheet(_btn_style(theme))
        self._zoom_plus_btn.setStyleSheet(_btn_style(theme))
        self._undo_btn.setStyleSheet(_btn_style(theme))
        self._redo_btn.setStyleSheet(_btn_style(theme))

    # ── Keyboard ─────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            if self._is_fullscreen:
                self._toggle_fullscreen()
            else:
                self.reject()
        else:
            super().keyPressEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée public
# ─────────────────────────────────────────────────────────────────────────────

def show_clone_zone_viewer(parent=None, callbacks=None):
    """Ouvre la visionneuse de clonage de zone.

    Si une image est sélectionnée, ouvre sur cette image.
    Sinon ouvre sur la première image de la mosaïque.
    Ne fait rien s'il n'y a pas d'images.
    """
    callbacks = callbacks or {}
    from modules.qt import state as _state_module
    state = callbacks.get('state') or _state_module.state

    if not state.images_data:
        return

    # Images valides uniquement
    image_entries = [
        e for e in state.images_data
        if e.get('is_image') and not e.get('is_corrupted')
    ]
    if not image_entries:
        return

    # Détermine l'entrée à ouvrir
    entry = None
    if state.selected_indices:
        for i in state.selected_indices:
            if i < len(state.images_data):
                e = state.images_data[i]
                if e.get('is_image') and not e.get('is_corrupted'):
                    entry = e
                    break
    if entry is None:
        entry = image_entries[0]

    # Sauvegarde le mode original (pour la reconversion en sortie)
    if entry.get('bytes'):
        try:
            img = Image.open(io.BytesIO(entry['bytes']))
            entry['_orig_mode'] = img.mode
        except Exception:
            entry['_orig_mode'] = 'RGB'

    dlg = CloneZoneViewerDialog(parent, entry, callbacks=callbacks)
    dlg.exec()
