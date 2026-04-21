"""
modules/qt/image_viewer_qt.py — Visionneuse d'images (version PySide6)

Reproduit à l'identique le comportement de modules/image_viewer.py (tkinter).
"""

import io
import time

from PIL import Image

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QMenu,
)
from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QPixmap, QImage, QKeySequence, QShortcut, QCursor

from modules.qt import state as _state_module
from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.entries import (
    ensure_image_loaded, free_image_memory, get_gif_frame, save_image_to_bytes,
)
from modules.qt.dialogs_qt import MsgDialog
from modules.qt.page_detection import compute_reference_ratio

# Liste globale des visionneuses ouvertes (pour mise à jour de langue)
image_viewer_refs = []


# ─────────────────────────────────────────────────────────────────────────────
# Helpers PIL → QPixmap
# ─────────────────────────────────────────────────────────────────────────────

def _pil_to_qpixmap(img: Image.Image) -> QPixmap:
    """Convertit une PIL Image en QPixmap (sans perte)."""
    img_rgba = img.convert("RGBA")
    data = img_rgba.tobytes("raw", "RGBA")
    qimg = QImage(data, img_rgba.width, img_rgba.height, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qimg)


def _compose_on_checkerboard(img: Image.Image, tile: int = 16) -> Image.Image:
    """Compose une image RGBA sur un fond damier (gris clair/foncé)."""
    from modules.qt.entries import _make_checkerboard_pil
    bg = _make_checkerboard_pil(img.width, img.height, tile=tile)
    img_rgba = img.convert("RGBA")
    bg.paste(img_rgba, (0, 0), img_rgba)
    return bg


# ─────────────────────────────────────────────────────────────────────────────
# Canvas de visionneuse (zone noire avec image centrée + rubber-band crop)
# ─────────────────────────────────────────────────────────────────────────────

class _ViewerCanvas(QLabel):
    """
    QLabel utilisé comme zone d'affichage de l'image.
    Gère :
      - affichage d'un QPixmap centré sur fond noir
      - rubber-band (rectangle rouge) pour le recadrage
      - pan clic-droit
      - double-clic plein écran / validation crop
    """

    _CURSORS = {
        'tl': Qt.SizeFDiagCursor, 'br': Qt.SizeFDiagCursor,
        'tr': Qt.SizeBDiagCursor, 'bl': Qt.SizeBDiagCursor,
        'left': Qt.SizeHorCursor, 'right': Qt.SizeHorCursor,
        'top': Qt.SizeVerCursor,  'bottom': Qt.SizeVerCursor,
        'move': Qt.SizeAllCursor,
    }

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(0, 0)

    def minimumSizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(0, 0)

    def __init__(self, viewer: "ImageViewer"):
        super().__init__()
        self._viewer = viewer
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background: black;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(1, 1)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)

        # État rubber-band
        self._crop_start: QPoint | None = None
        self._crop_end:   QPoint | None = None
        self._rubber_band_active = False

        # Infos image affichée (pour conversion coordonnées → image originale)
        self.display_offset_x = 0
        self.display_offset_y = 0
        self.display_width    = 0
        self.display_height   = 0

        # Coordonnées relatives (0-1) persistantes entre zooms
        self.crop_rel_x1: float | None = None
        self.crop_rel_y1: float | None = None
        self.crop_rel_x2: float | None = None
        self.crop_rel_y2: float | None = None

        # Pan clic-droit
        self._pan_start: QPoint | None = None
        self._is_panning = False

        # Gestion double-clic
        self._last_click_time = 0.0
        self._double_click_delay = 0.3
        self._waiting_for_double_click = False
        self._ignore_crop_events = False

        # Resize/move mode pour les bords/coins/intérieur du rectangle
        self._resize_mode: str | None = None
        self._resize_original_rect: tuple | None = None
        self._drag_start_pos: QPoint | None = None

        # Bouton Valider (flottant)
        self._validate_btn: QPushButton | None = None
        self._validate_btn_visible = False

    # ── Propriétés crop ──────────────────────────────────────────────────────

    @property
    def has_crop(self) -> bool:
        return self._crop_start is not None and self._crop_end is not None

    def clear_crop(self):
        self._crop_start = None
        self._crop_end   = None
        self.crop_rel_x1 = None
        self.crop_rel_y1 = None
        self.crop_rel_x2 = None
        self.crop_rel_y2 = None
        self._resize_mode = None
        self._resize_original_rect = None
        self._drag_start_pos = None
        self._hide_validate_btn()
        self.update()

    # ── Bouton Valider ───────────────────────────────────────────────────────

    def _ensure_validate_btn(self):
        if self._validate_btn is None:
            theme = get_current_theme()
            font = _get_current_font(12, bold=True)
            self._validate_btn = QPushButton(_("buttons.validate_crop"), self)
            self._validate_btn.setFont(font)
            self._validate_btn.setStyleSheet(
                f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
                f"border: 1px solid #aaaaaa; padding: 6px 12px; }}"
                f"QPushButton:hover {{ background: {theme['separator']}; }}"
            )
            self._validate_btn.clicked.connect(self._viewer.validate_crop)
            self._validate_btn.setFixedWidth(200)

    def _show_validate_btn(self):
        self._ensure_validate_btn()
        w = self._validate_btn
        # Positionné en bas au centre
        bw = w.sizeHint().width()
        bh = w.sizeHint().height()
        x = (self.width() - bw) // 2
        y = int(self.height() * 0.92) - bh // 2
        w.setGeometry(x, y, bw, bh)
        w.show()
        w.raise_()
        self._validate_btn_visible = True

    def _hide_validate_btn(self):
        if self._validate_btn is not None:
            self._validate_btn.hide()
        self._validate_btn_visible = False

    def retranslate_validate_btn(self):
        if self._validate_btn is not None:
            self._validate_btn.setText(_("buttons.validate_crop"))
            font = _get_current_font(12, bold=True)
            self._validate_btn.setFont(font)

    # ── Affichage du pixmap ──────────────────────────────────────────────────

    def set_pixmap_and_geometry(self, pixmap: QPixmap,
                                offset_x: int, offset_y: int,
                                disp_w: int, disp_h: int):
        # Stocke sans appeler setPixmap() pour ne pas déclencher updateGeometry()
        self._current_pixmap  = pixmap
        self.display_offset_x = offset_x
        self.display_offset_y = offset_y
        self.display_width    = disp_w
        self.display_height   = disp_h
        self.update()

    # ── Dessin (image + rubber-band) ─────────────────────────────────────────

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QPen, QColor
        painter = QPainter(self)

        # Fond noir
        painter.fillRect(self.rect(), QColor("black"))

        # Image centrée
        pm = getattr(self, '_current_pixmap', None)
        if pm and not pm.isNull():
            painter.drawPixmap(self.display_offset_x, self.display_offset_y, pm)

        # Rubber-band rouge
        if self.has_crop:
            pen = QPen(QColor("red"), 2)
            painter.setPen(pen)
            x1 = int(min(self._crop_start.x(), self._crop_end.x()))
            y1 = int(min(self._crop_start.y(), self._crop_end.y()))
            x2 = int(max(self._crop_start.x(), self._crop_end.x()))
            y2 = int(max(self._crop_start.y(), self._crop_end.y()))
            painter.drawRect(x1, y1, x2 - x1, y2 - y1)

        painter.end()

    # ── Gestion resize mode ──────────────────────────────────────────────────

    def _get_resize_mode(self, pos: QPoint) -> str | None:
        if not self.has_crop:
            return None
        tolerance = 10
        x1 = min(self._crop_start.x(), self._crop_end.x())
        x2 = max(self._crop_start.x(), self._crop_end.x())
        y1 = min(self._crop_start.y(), self._crop_end.y())
        y2 = max(self._crop_start.y(), self._crop_end.y())
        x, y = pos.x(), pos.y()
        if abs(x - x1) <= tolerance and abs(y - y1) <= tolerance: return 'tl'
        if abs(x - x2) <= tolerance and abs(y - y1) <= tolerance: return 'tr'
        if abs(x - x1) <= tolerance and abs(y - y2) <= tolerance: return 'bl'
        if abs(x - x2) <= tolerance and abs(y - y2) <= tolerance: return 'br'
        if abs(x - x1) <= tolerance and y1 <= y <= y2: return 'left'
        if abs(x - x2) <= tolerance and y1 <= y <= y2: return 'right'
        if abs(y - y1) <= tolerance and x1 <= x <= x2: return 'top'
        if abs(y - y2) <= tolerance and x1 <= x <= x2: return 'bottom'
        if x1 < x < x2 and y1 < y < y2: return 'move'
        return None

    # ── Événements souris ────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self._pan_start = event.position().toPoint()
            self._is_panning = False
            return

        if event.button() != Qt.LeftButton:
            return

        if self._ignore_crop_events:
            return

        pos = event.position().toPoint()
        current_time = time.time()
        time_since = current_time - self._last_click_time

        # Deuxième clic rapide → probablement double-clic, ignorer
        if 0.001 < time_since < self._double_click_delay:
            return

        self._last_click_time = current_time

        # Vérifie resize/move mode
        resize_mode = self._get_resize_mode(pos)
        if resize_mode:
            self._resize_mode = resize_mode
            self._resize_original_rect = (
                self._crop_start.x(), self._crop_start.y(),
                self._crop_end.x(),   self._crop_end.y()
            )
            self._drag_start_pos = pos
            self._waiting_for_double_click = False
            return

        # Rectangle complet existant → attendre double-clic
        if self.has_crop:
            self._waiting_for_double_click = True
            return

        # Nouveau rectangle
        self._hide_validate_btn()
        self._crop_start = pos
        self._crop_end   = None
        self._resize_mode = None
        self._waiting_for_double_click = False
        self.update()

    def mouseMoveEvent(self, event):
        # Pan clic-droit
        if event.buttons() & Qt.RightButton and self._pan_start is not None:
            delta = event.position().toPoint() - self._pan_start
            if abs(delta.x()) > 5 or abs(delta.y()) > 5:
                self._is_panning = True
                self.setCursor(Qt.SizeAllCursor)
                self.display_offset_x += delta.x()
                self.display_offset_y += delta.y()
                self._pan_start = event.position().toPoint()
                self.update()
            return

        if not (event.buttons() & Qt.LeftButton):
            # Mise à jour du curseur selon la position sur le cadre
            if self.has_crop:
                mode = self._get_resize_mode(event.position().toPoint())
                self.setCursor(QCursor(self._CURSORS.get(mode, Qt.ArrowCursor)))
            else:
                self.setCursor(Qt.ArrowCursor)
            return
        if self._ignore_crop_events:
            return
        if self._crop_start is None:
            return

        pos = event.position().toPoint()
        distance = ((pos.x() - self._crop_start.x())**2 +
                    (pos.y() - self._crop_start.y())**2) ** 0.5

        # Drag en attente de double-clic
        if self._waiting_for_double_click:
            if distance >= 15:
                self._waiting_for_double_click = False
                self._hide_validate_btn()
            else:
                return

        if self._resize_mode and self._resize_original_rect:
            ox1, oy1, ox2, oy2 = self._resize_original_rect
            x1, y1, x2, y2 = ox1, oy1, ox2, oy2
            rm = self._resize_mode
            if rm == 'move' and self._drag_start_pos is not None:
                dx = pos.x() - self._drag_start_pos.x()
                dy = pos.y() - self._drag_start_pos.y()
                x1, y1, x2, y2 = ox1 + dx, oy1 + dy, ox2 + dx, oy2 + dy
            elif rm == 'tl':   x1, y1 = pos.x(), pos.y()
            elif rm == 'tr': x2, y1 = pos.x(), pos.y()
            elif rm == 'bl': x1, y2 = pos.x(), pos.y()
            elif rm == 'br': x2, y2 = pos.x(), pos.y()
            elif rm == 'left':   x1 = pos.x()
            elif rm == 'right':  x2 = pos.x()
            elif rm == 'top':    y1 = pos.y()
            elif rm == 'bottom': y2 = pos.y()
            self._crop_start = QPoint(x1, y1)
            self._crop_end   = QPoint(x2, y2)
        elif distance >= 15 or self._crop_end is not None:
            self._crop_end = pos

        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self.setCursor(Qt.ArrowCursor)
            if not self._is_panning:
                self._viewer._show_context_menu(event.globalPosition().toPoint())
            self._pan_start  = None
            self._is_panning = False
            return

        if event.button() != Qt.LeftButton:
            return
        if self._ignore_crop_events:
            return

        pos = event.position().toPoint()

        if self._waiting_for_double_click:
            QTimer.singleShot(500, lambda: setattr(self, '_waiting_for_double_click', False))
            return

        if self._crop_start is None:
            return

        distance = ((pos.x() - self._crop_start.x())**2 +
                    (pos.y() - self._crop_start.y())**2) ** 0.5

        if self._resize_mode and self._resize_original_rect:
            ox1, oy1, ox2, oy2 = self._resize_original_rect
            x1, y1, x2, y2 = ox1, oy1, ox2, oy2
            rm = self._resize_mode
            if rm == 'move' and self._drag_start_pos is not None:
                dx = pos.x() - self._drag_start_pos.x()
                dy = pos.y() - self._drag_start_pos.y()
                x1, y1, x2, y2 = ox1 + dx, oy1 + dy, ox2 + dx, oy2 + dy
            elif rm == 'tl':   x1, y1 = pos.x(), pos.y()
            elif rm == 'tr': x2, y1 = pos.x(), pos.y()
            elif rm == 'bl': x1, y2 = pos.x(), pos.y()
            elif rm == 'br': x2, y2 = pos.x(), pos.y()
            elif rm == 'left':   x1 = pos.x()
            elif rm == 'right':  x2 = pos.x()
            elif rm == 'top':    y1 = pos.y()
            elif rm == 'bottom': y2 = pos.y()
            self._resize_mode = None
            self._resize_original_rect = None
            self._drag_start_pos = None
            self._crop_start = QPoint(min(x1, x2), min(y1, y2))
            self._crop_end   = QPoint(max(x1, x2), max(y1, y2))

        elif distance < 15 and self._crop_end is None:
            # Simple clic sans drag → rien à faire
            self._crop_start = None
            self.update()
            return
        else:
            if self._crop_end is None:
                self._crop_end = pos

        # Normalise
        x1 = min(self._crop_start.x(), self._crop_end.x())
        y1 = min(self._crop_start.y(), self._crop_end.y())
        x2 = max(self._crop_start.x(), self._crop_end.x())
        y2 = max(self._crop_start.y(), self._crop_end.y())

        if abs(x2 - x1) < 10 or abs(y2 - y1) < 10:
            self._crop_start = None
            self._crop_end   = None
            self.update()
            return

        self._crop_start = QPoint(x1, y1)
        self._crop_end   = QPoint(x2, y2)

        # Coordonnées relatives
        if self.display_width > 0 and self.display_height > 0:
            self.crop_rel_x1 = (x1 - self.display_offset_x) / self.display_width
            self.crop_rel_y1 = (y1 - self.display_offset_y) / self.display_height
            self.crop_rel_x2 = (x2 - self.display_offset_x) / self.display_width
            self.crop_rel_y2 = (y2 - self.display_offset_y) / self.display_height

        # En mode double page avec rectangle tracé → passe en simple page
        if self._viewer.page_mode != "single" and self.has_crop:
            if self._viewer.displayed_left_idx is not None and self._viewer.displayed_right_idx is not None:
                center_x = self.width() / 2
                rect_cx = (x1 + x2) / 2
                if rect_cx < center_x:
                    self._viewer.current_idx = self._viewer.displayed_left_idx
                else:
                    self._viewer.current_idx = self._viewer.displayed_right_idx
            self._viewer.page_mode = "single"
            self._viewer.display_image(keep_crop_rect=True)
            return

        self.update()
        self._show_validate_btn()

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self._ignore_crop_events = True
        self._waiting_for_double_click = False

        pos = event.position().toPoint()

        if self.has_crop:
            x1 = min(self._crop_start.x(), self._crop_end.x())
            y1 = min(self._crop_start.y(), self._crop_end.y())
            x2 = max(self._crop_start.x(), self._crop_end.x())
            y2 = max(self._crop_start.y(), self._crop_end.y())
            if x1 <= pos.x() <= x2 and y1 <= pos.y() <= y2:
                self._viewer._validating_crop = True
                self._viewer.validate_crop()
                self._viewer._validating_crop = False
                QTimer.singleShot(100, lambda: setattr(self, '_ignore_crop_events', False))
                return

        if not self._viewer._validating_crop:
            if self._crop_start is not None and self._crop_end is None:
                self._crop_start = None
                self.update()
            self._viewer.toggle_fullscreen()

        QTimer.singleShot(1000, lambda: setattr(self, '_ignore_crop_events', False))

    def wheelEvent(self, event):
        self._viewer._on_wheel(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._validate_btn_visible and self._validate_btn is not None:
            self._show_validate_btn()


# ─────────────────────────────────────────────────────────────────────────────
# Visionneuse principale
# ─────────────────────────────────────────────────────────────────────────────

class ImageViewer(QDialog):
    """
    Visionneuse d'images Qt.
    Reproduit à l'identique ImageViewer (tkinter) :
      - Navigation ← → / molette
      - Zoom Ctrl+Molette / Ctrl+Plus / Ctrl+Moins / Ctrl+0
      - Mode lecture : simple / double / continu (touche D)
      - Plein écran F11 / double-clic
      - Recadrage rubber-band + validation
      - GIF animé (bouton Play/Pause)
      - Pan clic-droit
      - Menu contextuel clic-droit
      - Undo/Redo Ctrl+Z / Ctrl+Y
    """

    def __init__(self, parent, start_idx: int, callbacks: dict | None = None):
        super().__init__(parent)
        self.callbacks = callbacks or {}
        state = self.callbacks.get('state') or _state_module.state

        self.setWindowTitle(_wt("viewer.title"))
        self.resize(800, 600)
        self.setWindowFlags(Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)

        # ── Appliquer l'icône de la fenêtre ──
        from modules.qt.font_loader import resource_path
        import os
        ico_path = resource_path('icons/MosaicView.ico')
        if os.path.exists(ico_path):
            from PySide6.QtGui import QIcon
            self.setWindowIcon(QIcon(ico_path))

        self.current_idx   = start_idx
        state.active_viewers += 1
        image_viewer_refs.append(self)

        # ── État ──────────────────────────────────────────────────────────────
        self.zoom_level      = 1.0
        self.page_mode       = "double"
        self.is_fullscreen   = False
        self._validating_crop = False

        self.displayed_left_idx  = None
        self.displayed_right_idx = None

        # GIF
        self.is_animated_gif   = False
        self.gif_is_playing    = False
        self.gif_current_frame = 0
        self._gif_timer        = QTimer(self)
        self._gif_timer.timeout.connect(self._animate_gif_frame)
        self.gif_durations     = []

        # Resize debounce
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(lambda: self.display_image(keep_crop_rect=True))
        self._last_w = 0
        self._last_h = 0

        # Nom masquage plein écran
        self._name_hide_timer = QTimer(self)
        self._name_hide_timer.setSingleShot(True)
        self._name_hide_timer.timeout.connect(self._hide_name_label)

        # ── Layout ───────────────────────────────────────────────────────────
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Canvas de visionneuse
        self._canvas = _ViewerCanvas(self)
        root_layout.addWidget(self._canvas, stretch=1)

        # Label du nom en bas
        self._name_label = QLabel()
        self._name_label.setAlignment(Qt.AlignCenter)
        self._name_label.setStyleSheet("background: transparent;")
        root_layout.addWidget(self._name_label)

        # Label du zoom (superposé en haut à droite)
        self._zoom_label = QLabel("100%", self)
        self._zoom_label.setStyleSheet("color: #666666; background: transparent;")
        self._zoom_label.adjustSize()

        # Bouton Play/Pause GIF (superposé en haut à gauche)
        self._play_pause_btn = QPushButton("▶", self)
        self._play_pause_btn.setFixedSize(40, 40)
        self._play_pause_btn.clicked.connect(self.toggle_gif_playback)
        self._play_pause_btn.hide()

        # Label du mode de lecture (superposé en haut à gauche)
        self._mode_label = QLabel("", self)
        self._mode_label.setStyleSheet("color: #666666; background: transparent;")
        self._mode_label.adjustSize()

        # ── Raccourcis clavier ────────────────────────────────────────────────
        QShortcut(QKeySequence(Qt.Key_Left),       self).activated.connect(lambda: self.navigate(-1))
        QShortcut(QKeySequence(Qt.Key_Right),      self).activated.connect(lambda: self.navigate(1))
        QShortcut(QKeySequence(Qt.Key_Escape),     self).activated.connect(self._on_escape)
        QShortcut(QKeySequence(Qt.Key_F11),        self).activated.connect(self.toggle_fullscreen)

        QShortcut(QKeySequence("Ctrl+Z"),          self).activated.connect(self._undo_and_refresh)
        QShortcut(QKeySequence("Ctrl+Shift+Z"),    self).activated.connect(self._redo_and_refresh)
        QShortcut(QKeySequence("Ctrl+Y"),          self).activated.connect(self._redo_and_refresh)
        QShortcut(QKeySequence("Ctrl++"),          self).activated.connect(lambda: self.adjust_zoom(0.1))
        QShortcut(QKeySequence("Ctrl+-"),          self).activated.connect(lambda: self.adjust_zoom(-0.1))
        QShortcut(QKeySequence("Ctrl+0"),          self).activated.connect(self.reset_zoom)

        # ── Signal langue ─────────────────────────────────────────────────────
        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self._closed = False

        self._center_parent = parent
        self._retranslate()
        self.display_image()

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    # ── Traduction à la volée ─────────────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(12)
        font_zoom = _get_current_font(10)

        self.setWindowTitle(_wt("viewer.title"))
        self.setStyleSheet(f"QDialog {{ background: black; }}")

        self._name_label.setFont(font)
        self._name_label.setStyleSheet(
            f"color: {theme['text']}; background: {theme['bg']};"
        )

        warn_color = "#666666" if not (self.callbacks.get('state') or _state_module.state).dark_mode else "#999999"
        self._zoom_label.setFont(font_zoom)
        self._zoom_label.setStyleSheet(f"color: {warn_color}; background: transparent;")
        self._zoom_label.adjustSize()
        self._zoom_label.move(self.width() - self._zoom_label.width() - 10, 10)

        self._mode_label.setFont(font_zoom)
        self._mode_label.setStyleSheet(f"color: {warn_color}; background: transparent;")
        self._update_mode_label()

        self._play_pause_btn.setFont(_get_current_font(16))
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: none; }}"
        )
        self._play_pause_btn.setStyleSheet(btn_style)

        self._canvas.retranslate_validate_btn()

    # ── Redisplay avec pan ────────────────────────────────────────────────────

    def _redisplay_with_pan(self):
        """Redessine l'image en tenant compte du décalage de pan."""
        self.display_image(keep_crop_rect=True)

    # ── Resize de la fenêtre ─────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        if w != self._last_w or h != self._last_h:
            self._last_w, self._last_h = w, h
            self._resize_timer.start(150)
        # Repositionne le zoom label
        self._zoom_label.adjustSize()
        self._zoom_label.move(w - self._zoom_label.width() - 10, 10)

    # ── Fermeture ─────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._closed:
            super().closeEvent(event)
            return
        self._closed = True

        state = self.callbacks.get('state') or _state_module.state
        self._gif_timer.stop()
        self._name_hide_timer.stop()
        self._resize_timer.stop()

        self._save_bookmark(state)

        for entry in state.images_data:
            if entry.get("is_image"):
                free_image_memory(entry)

        state.active_viewers -= 1
        if self in image_viewer_refs:
            image_viewer_refs.remove(self)

        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except Exception:
            pass

        super().closeEvent(event)

    def _save_bookmark(self, state):
        """Sauvegarde la page courante comme marque-page (sauf page 0 et dernière page)."""
        filepath = getattr(state, 'current_file', None)
        if not filepath:
            return
        img_indices = self.get_image_indices()
        if not img_indices:
            return
        try:
            current_pos = img_indices.index(self.current_idx)
        except ValueError:
            return
        last_pos = len(img_indices) - 1
        if current_pos == 0 or current_pos >= last_pos:
            return
        from modules.qt.config_manager import get_config_manager
        cfg = get_config_manager()
        if cfg:
            cfg.set_bookmark(filepath, current_pos)

    # ── Navigation ────────────────────────────────────────────────────────────

    def get_image_indices(self) -> list[int]:
        state = self.callbacks.get('state') or _state_module.state
        return [i for i, e in enumerate(state.images_data) if e["is_image"]]

    def is_wide_image(self, idx: int) -> bool:
        state = self.callbacks.get('state') or _state_module.state
        if idx >= len(state.images_data):
            return False
        entry = state.images_data[idx]
        if not entry["is_image"]:
            return False
        img = ensure_image_loaded(entry)
        if img is None:
            return False
        w, h = img.size
        return (w / h if h > 0 else 0) > 1.5

    def _get_all_ratios(self):
        """Retourne les ratios largeur/hauteur de toutes les images (pour la détection de pages multiples)."""
        state = self.callbacks.get('state') or _state_module.state
        ratios = []
        for entry in state.images_data:
            if not entry["is_image"]:
                continue
            w = entry.get("img_width")
            h = entry.get("img_height")
            if w and h and h > 0:
                ratios.append(w / h)
            else:
                img = ensure_image_loaded(entry)
                if img is not None:
                    ratios.append(img.width / img.height if img.height > 0 else 0)
                else:
                    ratios.append(0)
        return ratios

    def is_multiple_page(self, idx: int) -> bool:
        """Détecte si une page est une page multiple (double, triple…) selon la logique de renumérotation.
        Utilise le ratio relatif à la médiane des pages portrait : mult >= 2 → page multiple."""
        state = self.callbacks.get('state') or _state_module.state
        if idx >= len(state.images_data):
            return False
        entry = state.images_data[idx]
        if not entry["is_image"]:
            return False
        w = entry.get("img_width")
        h = entry.get("img_height")
        if w and h and h > 0:
            ratio = w / h
        else:
            img = ensure_image_loaded(entry)
            if img is None:
                return False
            ratio = img.width / img.height if img.height > 0 else 0
        if ratio <= 0:
            return False
        reference_ratio = compute_reference_ratio(self._get_all_ratios())
        if reference_ratio <= 0:
            return False
        mult = max(1, round(ratio / reference_ratio))
        return mult >= 2

    def _double_page_pair_start(self, img_indices, pos):
        """Retourne True si pos est le début d'une paire en mode page double.
        La parité est recalculée dynamiquement en tenant compte des pages multiples précédentes :
        on simule le défilement depuis le début pour savoir si pos est début ou fin de paire."""
        if pos == 0:
            return False  # pos 0 est toujours affiché seul
        p = 1  # on commence à simuler depuis pos 1
        while p < pos:
            if self.is_multiple_page(img_indices[p]):
                p += 1  # page multiple : seule, avance de 1
            else:
                if p == pos:
                    break
                # paire normale : deux pages
                p += 2
        return p == pos

    def navigate(self, delta: int):
        img_indices = self.get_image_indices()
        if not img_indices:
            return
        try:
            current_pos = img_indices.index(self.current_idx)
            if self.page_mode == "double":
                is_pair_start = self._double_page_pair_start(img_indices, current_pos)
                if delta > 0:
                    if current_pos == 0:
                        new_pos = 1
                    elif self.is_multiple_page(img_indices[current_pos]):
                        new_pos = current_pos + 1
                    elif is_pair_start:
                        if current_pos + 1 < len(img_indices) and self.is_multiple_page(img_indices[current_pos + 1]):
                            new_pos = current_pos + 1
                        else:
                            new_pos = current_pos + 2
                    else:
                        new_pos = current_pos + 1
                else:
                    if current_pos <= 1:
                        new_pos = 0
                    elif self.is_multiple_page(img_indices[current_pos]):
                        new_pos = current_pos - 1
                    elif is_pair_start:
                        new_pos = max(1, current_pos - 2)
                    else:
                        new_pos = current_pos - 1
            else:
                new_pos = current_pos + delta
            new_pos = max(0, min(new_pos, len(img_indices) - 1))
            self.current_idx = img_indices[new_pos]
            self._check_clear_bookmark_on_last_page(img_indices, new_pos)
            self.display_image()
        except ValueError:
            self.current_idx = img_indices[0]
            self.display_image()

    def _check_clear_bookmark_on_last_page(self, img_indices, current_pos):
        """Efface le marque-page si la dernière page est maintenant visible."""
        last_pos = len(img_indices) - 1
        is_last = False
        if self.page_mode == "double":
            is_last = (current_pos >= last_pos or current_pos + 1 >= last_pos)
        else:
            is_last = (current_pos >= last_pos)
        if not is_last:
            return
        state = self.callbacks.get('state') or _state_module.state
        filepath = getattr(state, 'current_file', None)
        if not filepath:
            return
        from modules.qt.config_manager import get_config_manager
        cfg = get_config_manager()
        if cfg and cfg.get_bookmark(filepath) is not None:
            cfg.remove_bookmark(filepath)

    def _on_wheel(self, event):
        mods = event.modifiers()
        delta = event.angleDelta().y()
        if mods & Qt.ControlModifier:
            self.adjust_zoom(0.1 if delta > 0 else -0.1)
        else:
            step = 2 if self.page_mode == "double" else 1
            self.navigate(-step if delta > 0 else step)

    # ── Zoom ──────────────────────────────────────────────────────────────────

    def adjust_zoom(self, delta: float):
        self.zoom_level = max(0.1, min(5.0, self.zoom_level + delta))
        self._zoom_label.setText(f"{int(self.zoom_level * 100)}%")
        self._zoom_label.adjustSize()
        self._zoom_label.move(self.width() - self._zoom_label.width() - 10, 10)
        self.display_image(keep_crop_rect=True)

    def reset_zoom(self):
        self.zoom_level = 1.0
        self._zoom_label.setText("100%")
        self._zoom_label.adjustSize()
        self._zoom_label.move(self.width() - self._zoom_label.width() - 10, 10)
        self.display_image(keep_crop_rect=True)

    # ── Plein écran ───────────────────────────────────────────────────────────

    def toggle_fullscreen(self):
        if self._validating_crop:
            return
        if self.is_fullscreen:
            self.showNormal()
            self.is_fullscreen = False
            self._name_label.show()
            self._name_label.setStyleSheet(
                f"color: {get_current_theme()['text']}; background: {get_current_theme()['bg']};"
            )
            self._name_hide_timer.stop()
        else:
            self.showFullScreen()
            self.is_fullscreen = True
            self._name_label.setStyleSheet("color: white; background: black;")
        self.display_image(keep_crop_rect=True)

    # ── Mode de lecture ───────────────────────────────────────────────────────

    def _update_mode_label(self):
        if self.is_animated_gif:
            self._mode_label.hide()
            return
        if self.page_mode == "single":
            text = _("viewer.mode_single")
        elif self.page_mode == "double":
            text = _("viewer.mode_double")
        else:
            text = _("viewer.mode_continuous")
        self._mode_label.setText(text)
        self._mode_label.adjustSize()
        self._mode_label.move(10, 10)
        self._mode_label.show()

    def toggle_double_page(self):
        if self.page_mode == "single":
            self.page_mode = "double"
        elif self.page_mode == "double":
            self.page_mode = "continuous"
        else:
            self.page_mode = "single"
        self._update_mode_label()
        self.display_image(keep_crop_rect=True)

    # ── Undo/Redo ─────────────────────────────────────────────────────────────

    def _undo_and_refresh(self):
        fn = self.callbacks.get("undo_action")
        if fn:
            fn()
        self._refresh_after_undo_redo()

    def _redo_and_refresh(self):
        fn = self.callbacks.get("redo_action")
        if fn:
            fn()
        self._refresh_after_undo_redo()

    def _refresh_after_undo_redo(self):
        """Rafraîchit la visionneuse après undo/redo en invalidant le cache image."""
        state = self.callbacks.get('state') or _state_module.state
        # Invalide le cache PIL de l'image courante pour forcer le rechargement depuis bytes
        if 0 <= self.current_idx < len(state.images_data):
            entry = state.images_data[self.current_idx]
            entry["img"] = None
        self.display_image()

    # ── Touches clavier ───────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_D:
            self.toggle_double_page()
        else:
            super().keyPressEvent(event)

    # ── Échap ─────────────────────────────────────────────────────────────────

    def _on_escape(self):
        if self._canvas.has_crop or self._canvas._crop_start is not None:
            self._canvas.clear_crop()
        elif self.is_fullscreen:
            self.toggle_fullscreen()
        else:
            self.close()

    # ── Marque-page ───────────────────────────────────────────────────────────

    def _delete_current_bookmark(self):
        state = self.callbacks.get('state') or _state_module.state
        filepath = getattr(state, 'current_file', None)
        if not filepath:
            return
        from modules.qt.config_manager import get_config_manager
        cfg = get_config_manager()
        if cfg:
            cfg.remove_bookmark(filepath)

    # ── Menu contextuel ───────────────────────────────────────────────────────

    def _show_context_menu(self, global_pos: QPoint):
        menu = QMenu(self)
        font = _get_current_font(9)
        menu.setFont(font)

        theme = get_current_theme()
        menu.setStyleSheet(
            f"QMenu {{ background: {theme['toolbar_bg']}; color: {theme['text']}; }}"
            f"QMenu::item:selected {{ background: {theme['separator']}; }}"
        )

        menu.addAction(_("context_menu.viewer.prev_page"),  lambda: self.navigate(-1))
        menu.addAction(_("context_menu.viewer.next_page"),  lambda: self.navigate(1))
        menu.addSeparator()
        menu.addAction(_("context_menu.viewer.zoom_in"),    lambda: self.adjust_zoom(0.1))
        menu.addAction(_("context_menu.viewer.zoom_out"),   lambda: self.adjust_zoom(-0.1))
        menu.addAction(_("context_menu.viewer.zoom_reset"), self.reset_zoom)
        menu.addSeparator()

        if self.page_mode == "single":
            mode_label = _("context_menu.viewer.reading_mode_double")
        elif self.page_mode == "double":
            mode_label = _("context_menu.viewer.reading_mode_continuous")
        else:
            mode_label = _("context_menu.viewer.reading_mode_single")
        menu.addAction(mode_label, self.toggle_double_page)
        menu.addSeparator()

        fs_label = (_("context_menu.viewer.fullscreen_exit") if self.is_fullscreen
                    else _("context_menu.viewer.fullscreen"))
        menu.addAction(fs_label, self.toggle_fullscreen)
        menu.addSeparator()
        menu.addAction(_("context_menu.viewer.close_viewer"), self.close)
        menu.addSeparator()

        state = self.callbacks.get('state') or _state_module.state
        filepath = getattr(state, 'current_file', None)
        from modules.qt.config_manager import get_config_manager
        cfg = get_config_manager()
        has_bookmark = bool(filepath and cfg and cfg.get_bookmark(filepath) is not None)
        act_del_bm = menu.addAction(_("context_menu.viewer.delete_bookmark"), self._delete_current_bookmark)
        act_del_bm.setEnabled(has_bookmark)

        menu.exec(global_pos)

    # ── GIF animé ─────────────────────────────────────────────────────────────

    def toggle_gif_playback(self):
        if not self.is_animated_gif:
            return
        if self.gif_is_playing:
            self._gif_timer.stop()
            self.gif_is_playing = False
            self._play_pause_btn.setText("▶")
        else:
            self.gif_is_playing = True
            self._play_pause_btn.setText("⏸")
            self._schedule_gif_frame()

    def _schedule_gif_frame(self):
        if not self.gif_is_playing or not self.is_animated_gif:
            return
        duration = self.gif_durations[self.gif_current_frame] if self.gif_current_frame < len(self.gif_durations) else 100
        self._gif_timer.start(duration)

    def _animate_gif_frame(self):
        self._gif_timer.stop()
        state = self.callbacks.get('state') or _state_module.state
        if not self.gif_is_playing or not self.is_animated_gif:
            return
        entry = state.images_data[self.current_idx]
        frame_count = entry.get("gif_frame_count", 0)
        if frame_count == 0:
            return
        frame = get_gif_frame(entry, self.gif_current_frame)
        if frame is None:
            return
        # Redimensionne la frame
        cw = self._canvas.width()
        ch = self._canvas.height() - 40
        if cw <= 1: cw = 800
        if ch <= 1: ch = 540
        fw, fh = frame.size
        ratio = min(cw / fw, ch / fh)
        final_w = int(fw * ratio * self.zoom_level)
        final_h = int(fh * ratio * self.zoom_level)
        frame = frame.resize((final_w, final_h), Image.Resampling.LANCZOS)
        frame_has_alpha = (frame.mode in ('RGBA', 'LA') or
                           (frame.mode == 'P' and 'transparency' in frame.info))
        if frame_has_alpha:
            frame = _compose_on_checkerboard(frame)
        pixmap = _pil_to_qpixmap(frame)
        offset_x = (cw - final_w) // 2
        offset_y = (ch - final_h) // 2
        self._canvas.set_pixmap_and_geometry(pixmap, offset_x, offset_y, final_w, final_h)
        self.gif_current_frame = (self.gif_current_frame + 1) % frame_count
        self._schedule_gif_frame()

    def _stop_gif(self):
        self._gif_timer.stop()
        self.gif_is_playing = False

    # ── Affichage ─────────────────────────────────────────────────────────────

    def display_image(self, keep_crop_rect: bool = False):
        state = self.callbacks.get('state') or _state_module.state
        if self.current_idx >= len(state.images_data):
            return

        if not keep_crop_rect:
            self._canvas.clear_crop()

        img_indices = self.get_image_indices()
        if not img_indices:
            return
        try:
            current_pos = img_indices.index(self.current_idx)
        except ValueError:
            return

        cw = self._canvas.width()
        ch = self._canvas.height()
        if cw <= 1: cw = 780
        if ch <= 1: ch = 540
        viewer_w = cw - 20
        viewer_h = ch - 20

        if self.page_mode == "double":
            if self.is_multiple_page(self.current_idx):
                self._display_single_page(self.current_idx, viewer_w, viewer_h)
            elif current_pos == 0:
                self._display_single_page(img_indices[0], viewer_w, viewer_h)
            else:
                is_pair_start = self._double_page_pair_start(img_indices, current_pos)
                if is_pair_start:
                    left_idx  = img_indices[current_pos]
                    right_idx = img_indices[current_pos + 1] if current_pos + 1 < len(img_indices) else None
                    if self.is_multiple_page(left_idx) or (right_idx and self.is_multiple_page(right_idx)):
                        self._display_single_page(left_idx, viewer_w, viewer_h)
                    else:
                        self._display_double_page(left_idx, right_idx, viewer_w, viewer_h)
                else:
                    left_idx  = img_indices[current_pos - 1] if current_pos > 0 else None
                    right_idx = img_indices[current_pos]
                    if self.is_multiple_page(right_idx):
                        self._display_single_page(right_idx, viewer_w, viewer_h)
                    else:
                        self._display_double_page(left_idx, right_idx, viewer_w, viewer_h)

        elif self.page_mode == "continuous":
            if self.is_wide_image(self.current_idx):
                self._display_single_page(self.current_idx, viewer_w, viewer_h)
            elif current_pos == 0:
                self._display_single_page(img_indices[0], viewer_w, viewer_h)
            else:
                left_idx  = img_indices[current_pos]
                right_idx = img_indices[current_pos + 1] if current_pos + 1 < len(img_indices) else None
                if self.is_wide_image(left_idx) or (right_idx and self.is_wide_image(right_idx)):
                    self._display_single_page(left_idx, viewer_w, viewer_h)
                else:
                    self._display_double_page(left_idx, right_idx, viewer_w, viewer_h)
        else:
            self._display_single_page(self.current_idx, viewer_w, viewer_h)

    def _display_single_page(self, idx: int, viewer_w: int, viewer_h: int):
        state = self.callbacks.get('state') or _state_module.state
        entry = state.images_data[idx]
        if not entry["is_image"]:
            return

        img = ensure_image_loaded(entry)
        if img is None:
            return

        self.displayed_left_idx  = None
        self.displayed_right_idx = None
        self._stop_gif()

        self.is_animated_gif = entry.get("is_animated_gif", False)
        if self.is_animated_gif:
            self.gif_durations = entry.get("gif_durations", [])
            self.gif_current_frame = 0
            self._play_pause_btn.setText("▶")
            self._play_pause_btn.move(10, 10)
            self._play_pause_btn.show()
            self._play_pause_btn.raise_()
        else:
            self._play_pause_btn.hide()
            self.gif_durations = []
        self._update_mode_label()

        img = img.copy()
        has_alpha = (img.mode in ('RGBA', 'LA') or
                     (img.mode == 'P' and 'transparency' in img.info))
        img_w, img_h = img.size
        ratio = min(viewer_w / img_w, viewer_h / img_h)
        final_w = int(img_w * ratio * self.zoom_level)
        final_h = int(img_h * ratio * self.zoom_level)
        img = img.resize((final_w, final_h), Image.Resampling.LANCZOS)
        if has_alpha:
            img = _compose_on_checkerboard(img)
        pixmap = _pil_to_qpixmap(img)

        cw = self._canvas.width()
        ch = self._canvas.height()
        if cw <= 1: cw = viewer_w
        if ch <= 1: ch = viewer_h
        offset_x = (cw - final_w) // 2
        offset_y = (ch - final_h) // 2

        self._canvas.set_pixmap_and_geometry(pixmap, offset_x, offset_y, final_w, final_h)

        # Redessine le rubber-band si coordonnées relatives disponibles
        if self._canvas.crop_rel_x1 is not None:
            x1 = int(offset_x + self._canvas.crop_rel_x1 * final_w)
            y1 = int(offset_y + self._canvas.crop_rel_y1 * final_h)
            x2 = int(offset_x + self._canvas.crop_rel_x2 * final_w)
            y2 = int(offset_y + self._canvas.crop_rel_y2 * final_h)
            self._canvas._crop_start = QPoint(x1, y1)
            self._canvas._crop_end   = QPoint(x2, y2)
            self._canvas.update()
            self._canvas._show_validate_btn()

        # Nom de fichier
        img_indices = self.get_image_indices()
        pos = img_indices.index(idx) + 1
        total = len(img_indices)
        self._name_label.setText(f"{entry['orig_name']} ({pos}/{total})")

        self._schedule_name_hide()

    def _display_double_page(self, left_idx, right_idx, viewer_w: int, viewer_h: int):
        state = self.callbacks.get('state') or _state_module.state
        self._stop_gif()
        self._play_pause_btn.hide()
        self.is_animated_gif = False

        self.displayed_left_idx  = left_idx
        self.displayed_right_idx = right_idx
        self._update_mode_label()

        left_img = right_img = None
        if left_idx is not None and left_idx < len(state.images_data):
            e = state.images_data[left_idx]
            if e["is_image"]:
                loaded = ensure_image_loaded(e)
                if loaded:
                    left_img = loaded.copy()
        if right_idx is not None and right_idx < len(state.images_data):
            e = state.images_data[right_idx]
            if e["is_image"]:
                loaded = ensure_image_loaded(e)
                if loaded:
                    right_img = loaded.copy()

        if not left_img and not right_img:
            return
        if not right_img:
            self._display_single_page(left_idx, viewer_w, viewer_h)
            return

        # Normalise les hauteurs
        lw, lh = left_img.size
        rw, rh = right_img.size
        max_h = max(lh, rh)
        if lh != max_h:
            left_img = left_img.resize((int(lw * max_h / lh), max_h), Image.Resampling.LANCZOS)
            lw = left_img.size[0]
        if rh != max_h:
            right_img = right_img.resize((int(rw * max_h / rh), max_h), Image.Resampling.LANCZOS)
            rw = right_img.size[0]

        combined_w = lw + rw
        left_has_alpha  = left_img.mode  in ('RGBA', 'LA') or (left_img.mode  == 'P' and 'transparency' in left_img.info)
        right_has_alpha = right_img.mode in ('RGBA', 'LA') or (right_img.mode == 'P' and 'transparency' in right_img.info)
        has_alpha = left_has_alpha or right_has_alpha
        if has_alpha:
            combined = Image.new('RGBA', (combined_w, max_h), (0, 0, 0, 0))
            left_rgba  = left_img.convert('RGBA')
            right_rgba = right_img.convert('RGBA')
            combined.paste(left_rgba,  (0,  0), left_rgba)
            combined.paste(right_rgba, (lw, 0), right_rgba)
        else:
            combined = Image.new('RGB', (combined_w, max_h), 'black')
            combined.paste(left_img,  (0,  0))
            combined.paste(right_img, (lw, 0))

        ratio   = min(viewer_w / combined_w, viewer_h / max_h)
        final_w = int(combined_w * ratio * self.zoom_level)
        final_h = int(max_h     * ratio * self.zoom_level)

        combined = combined.resize((final_w, final_h), Image.Resampling.LANCZOS)
        if has_alpha:
            combined = _compose_on_checkerboard(combined)
        pixmap = _pil_to_qpixmap(combined)

        cw = self._canvas.width()
        ch = self._canvas.height()
        if cw <= 1: cw = viewer_w
        if ch <= 1: ch = viewer_h
        offset_x = (cw - final_w) // 2
        offset_y = (ch - final_h) // 2

        self._canvas.set_pixmap_and_geometry(pixmap, offset_x, offset_y, final_w, final_h)

        # Nom
        img_indices = self.get_image_indices()
        left_entry  = state.images_data[left_idx]
        right_entry = state.images_data[right_idx] if right_idx else None
        lpos = img_indices.index(left_idx) + 1
        rpos = img_indices.index(right_idx) + 1 if right_idx and right_idx in img_indices else None
        total = len(img_indices)
        if rpos:
            self._name_label.setText(
                f"{left_entry['orig_name']} | {right_entry['orig_name']} ({lpos}-{rpos}/{total})"
            )
        else:
            self._name_label.setText(f"{left_entry['orig_name']} ({lpos}/{total})")

        self._schedule_name_hide()

    # ── Nom en plein écran ────────────────────────────────────────────────────

    def _schedule_name_hide(self):
        self._name_hide_timer.stop()
        if self.is_fullscreen:
            self._name_label.show()
            self._name_hide_timer.start(2000)

    def _hide_name_label(self):
        if self.is_fullscreen:
            self._name_label.hide()

    # ── Recadrage ─────────────────────────────────────────────────────────────

    def validate_crop(self):
        if not self._canvas.has_crop:
            dlg = MsgDialog(
                self,
                "messages.warnings.no_crop_selection.title",
                "messages.warnings.no_crop_selection.message",
            )
            dlg.exec()
            return
        self.perform_crop()

    def perform_crop(self):
        state = self.callbacks.get('state') or _state_module.state
        save_state      = self.callbacks.get("save_state")
        render_mosaic   = self.callbacks.get("render_mosaic")
        update_btn      = self.callbacks.get("update_button_text")
        canvas          = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]
            original_img = ensure_image_loaded(entry)
            if original_img is None:
                dlg = MsgDialog(self, "messages.errors.crop_failed.title",
                                "messages.errors.crop_failed.title")
                dlg.exec()
                return

            cw = self._canvas.width()
            ch = self._canvas.height()
            viewer_w = cw - 20
            viewer_h = ch - 20
            if viewer_w <= 1: viewer_w = 780
            if viewer_h <= 1: viewer_h = 540

            c = self._canvas
            crop_x1 = c._crop_start.x() - c.display_offset_x
            crop_y1 = c._crop_start.y() - c.display_offset_y
            crop_x2 = c._crop_end.x()   - c.display_offset_x
            crop_y2 = c._crop_end.y()   - c.display_offset_y

            crop_x1 = max(0, min(crop_x1, c.display_width))
            crop_y1 = max(0, min(crop_y1, c.display_height))
            crop_x2 = max(0, min(crop_x2, c.display_width))
            crop_y2 = max(0, min(crop_y2, c.display_height))

            img_w, img_h = original_img.size
            ratio = min(viewer_w / img_w, viewer_h / img_h)
            zoom_ratio = ratio * self.zoom_level

            orig_x1 = int(crop_x1 / zoom_ratio)
            orig_y1 = int(crop_y1 / zoom_ratio)
            orig_x2 = int(crop_x2 / zoom_ratio)
            orig_y2 = int(crop_y2 / zoom_ratio)

            if orig_x2 <= orig_x1 or orig_y2 <= orig_y1:
                dlg = MsgDialog(self, "messages.errors.crop_invalid.title",
                                "messages.errors.crop_invalid.message")
                dlg.exec()
                return

            if save_state:
                save_state()

            cropped = original_img.crop((orig_x1, orig_y1, orig_x2, orig_y2))
            entry["img"]   = cropped
            entry["bytes"] = save_image_to_bytes(entry)
            entry["large_thumb_pil"] = None
            entry["qt_pixmap_large"] = None
            entry["qt_qimage_large"] = None
            state.modified = True

            from modules.qt.comic_info import get_page_image_index, update_page_entries_in_xml_data
            _pidx = get_page_image_index(state, entry)
            if _pidx is not None:
                update_page_entries_in_xml_data(state, [(_pidx, entry)])
            if save_state:
                save_state()
            real_idx = entry.get("_real_idx")
            if canvas is not None and real_idx is not None:
                from modules.qt.mosaic_canvas import build_qimage_for_entry
                build_qimage_for_entry(entry)
                canvas.refresh_thumbnail(real_idx)
            elif render_mosaic:
                render_mosaic()
            if update_btn:
                update_btn()
            self._canvas.clear_crop()
            self.display_image()

        except Exception as e:
            import traceback; traceback.print_exc()
            dlg = MsgDialog(self, "messages.errors.crop_failed.title",
                            "messages.errors.crop_failed.title")
            dlg.exec()


# ─────────────────────────────────────────────────────────────────────────────
# Fonction publique d'ouverture
# ─────────────────────────────────────────────────────────────────────────────

def open_image_viewer(parent, idx: int, callbacks: dict):
    """Ouvre la visionneuse sur l'image d'index idx."""
    state = (callbacks or {}).get('state') or _state_module.state
    if not state.images_data[idx]["is_image"] or state.images_data[idx].get("is_corrupted"):
        return
    viewer = ImageViewer(parent, idx, callbacks=callbacks)
    viewer.show()


def update_image_viewer_if_open():
    """Met à jour le titre des visionneuses ouvertes si elles existent."""
    for viewer in image_viewer_refs[:]:
        try:
            if viewer and viewer.isVisible():
                viewer.setWindowTitle(_wt("viewer.title"))
                viewer._retranslate()
        except Exception:
            if viewer in image_viewer_refs:
                image_viewer_refs.remove(viewer)
