"""
modules/qt/ico_creator_qt.py — Création de fichiers .ico multi-résolution (version PySide6)

Reproduit à l'identique le comportement de modules/ico_creator.py (tkinter).

Deux phases :
  Phase A — découpe carrée + validation (zoom Ctrl+Molette)
  Phase B — définition des zones de transparence par seuil (pipette flood-fill)
"""

import io
import os
import re

from PIL import Image

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QPoint, QRect, QSize
from PySide6.QtGui import QPixmap, QImage, QPainter, QPen, QColor, QCursor, QIcon

from modules.qt import state as _state_module
from modules.qt.utils import FocusSlider
from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.entries import ensure_image_loaded
from modules.qt.undo_redo_qt import save_state_qt


# Tailles standard ICO
_ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

_TOLERANCE_MIN = 0
_TOLERANCE_MAX = 255
_TOLERANCE_DEFAULT = 15


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_ICON_SIZE = 24  # taille des icônes undo/redo dans les barres de boutons


def _make_icon_btn(filename: str, tooltip: str = "") -> QPushButton:
    """Crée un QPushButton carré avec l'icône chargée depuis icons/."""
    from modules.qt.font_loader import resource_path
    btn = QPushButton()
    btn.setFixedSize(_ICON_SIZE + 8, _ICON_SIZE + 8)
    btn.setToolTip(tooltip)
    img_path = resource_path(os.path.join("icons", filename))
    if os.path.exists(img_path):
        try:
            img = Image.open(img_path).convert("RGBA").resize((_ICON_SIZE, _ICON_SIZE), Image.LANCZOS)
            data = img.tobytes("raw", "RGBA")
            qimg = QImage(data, img.width, img.height, QImage.Format_RGBA8888)
            btn.setIcon(QIcon(QPixmap.fromImage(qimg)))
            btn.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        except Exception:
            pass
    return btn


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


def _pil_to_qpixmap(img: Image.Image) -> QPixmap:
    """Convertit une PIL Image (RGBA) en QPixmap."""
    img_rgba = img.convert("RGBA")
    data = img_rgba.tobytes("raw", "RGBA")
    qimg = QImage(data, img_rgba.width, img_rgba.height, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qimg)


def _make_checkerboard_pixmap(w: int, h: int, tile: int = 8) -> QPixmap:
    """Génère un QPixmap damier (gris clair / gris foncé) de taille w×h."""
    light = QColor(200, 200, 200)
    dark  = QColor(160, 160, 160)
    pixmap = QPixmap(w, h)
    painter = QPainter(pixmap)
    for row in range(0, h, tile):
        for col in range(0, w, tile):
            color = light if ((row // tile) + (col // tile)) % 2 == 0 else dark
            painter.fillRect(col, row, min(tile, w - col), min(tile, h - row), color)
    painter.end()
    return pixmap


# ─────────────────────────────────────────────────────────────────────────────
# Canvas Phase A — découpe carrée
# ─────────────────────────────────────────────────────────────────────────────

class _CropCanvas(QWidget):
    """
    Zone d'affichage de la phase A.
    Affiche l'image avec un cadre rouge carré déplaçable/redimensionnable.
    """

    _TOLERANCE = 10
    _CURSORS = {
        'tl': Qt.SizeFDiagCursor, 'br': Qt.SizeFDiagCursor,
        'tr': Qt.SizeBDiagCursor, 'bl': Qt.SizeBDiagCursor,
        'left': Qt.SizeHorCursor, 'right': Qt.SizeHorCursor,
        'top': Qt.SizeVerCursor,  'bottom': Qt.SizeVerCursor,
        'move': Qt.SizeAllCursor,
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: black;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(100, 100)
        self.setMouseTracking(True)

        self._pixmap: QPixmap | None = None

        # Géométrie de l'image affichée (coords widget)
        self.display_offset_x = 0
        self.display_offset_y = 0
        self.display_width = 0
        self.display_height = 0

        # Cadre carré en coords widget
        self.rect_x1 = 0.0
        self.rect_y1 = 0.0
        self.rect_x2 = 0.0
        self.rect_y2 = 0.0

        # Coords relatives (0-1) persistantes entre zooms
        self.rect_rel_x1: float | None = None
        self.rect_rel_y1: float | None = None
        self.rect_rel_size: float | None = None

        # Drag cadre (clic gauche)
        self._drag_mode: str | None = None
        self._drag_start = QPoint()
        self._drag_rect_orig: tuple | None = None

        # Pan image (clic droit)
        self._pan_start: QPoint | None = None
        self._pan_orig_off: tuple | None = None  # (offset_x, offset_y) au début du pan

        # Callbacks
        self.on_rect_changed = None    # appelé après chaque modification du cadre
        self.on_pan_changed = None     # appelé après un pan (arg: (offset_x, offset_y))
        self.on_double_click = None    # appelé sur double-clic gauche
        self.on_drag_start = None      # appelé au début d'un drag du cadre (pour undo)

    def set_image(self, pixmap: QPixmap, img_w: int, img_h: int,
                  offset_x: int, offset_y: int, disp_w: int, disp_h: int,
                  init: bool):
        self._img_w = img_w
        self._img_h = img_h
        self._pixmap = pixmap
        self.display_offset_x = offset_x
        self.display_offset_y = offset_y
        self.display_width = disp_w
        self.display_height = disp_h

        if init or self.rect_rel_x1 is None:
            side_img_px = min(img_w, img_h)
            self.rect_rel_x1 = (img_w // 2 - side_img_px // 2) / img_w
            self.rect_rel_y1 = (img_h // 2 - side_img_px // 2) / img_h
            self.rect_rel_size = side_img_px / img_w

        self._rel_to_canvas()
        self.update()

    def _rel_to_canvas(self):
        img_w = self._img_w
        img_h = self._img_h
        side_img = self.rect_rel_size * img_w
        x1_img = self.rect_rel_x1 * img_w
        y1_img = self.rect_rel_y1 * img_h
        x2_img = x1_img + side_img
        y2_img = y1_img + side_img
        self.rect_x1 = self.display_offset_x + x1_img * self.display_width / img_w
        self.rect_y1 = self.display_offset_y + y1_img * self.display_height / img_h
        self.rect_x2 = self.display_offset_x + x2_img * self.display_width / img_w
        self.rect_y2 = self.display_offset_y + y2_img * self.display_height / img_h

    def _canvas_to_rel(self):
        img_w = self._img_w
        img_h = self._img_h
        if self.display_width == 0 or self.display_height == 0:
            return
        x1_img = (self.rect_x1 - self.display_offset_x) * img_w / self.display_width
        y1_img = (self.rect_y1 - self.display_offset_y) * img_h / self.display_height
        side_img = (self.rect_x2 - self.rect_x1) * img_w / self.display_width
        self.rect_rel_x1 = x1_img / img_w
        self.rect_rel_y1 = y1_img / img_h
        self.rect_rel_size = side_img / img_w

    def _get_handle(self, x, y) -> str | None:
        x1, y1, x2, y2 = self.rect_x1, self.rect_y1, self.rect_x2, self.rect_y2
        left, right = min(x1, x2), max(x1, x2)
        top, bottom = min(y1, y2), max(y1, y2)
        t = self._TOLERANCE

        if abs(x - left) <= t and abs(y - top) <= t:    return 'tl'
        if abs(x - right) <= t and abs(y - top) <= t:   return 'tr'
        if abs(x - left) <= t and abs(y - bottom) <= t: return 'bl'
        if abs(x - right) <= t and abs(y - bottom) <= t:return 'br'
        if abs(x - left) <= t and top <= y <= bottom:   return 'left'
        if abs(x - right) <= t and top <= y <= bottom:  return 'right'
        if abs(y - top) <= t and left <= x <= right:    return 'top'
        if abs(y - bottom) <= t and left <= x <= right: return 'bottom'
        if left < x < right and top < y < bottom:       return 'move'
        return None

    def _constrain_square_from_corner(self, ax, ay, mx, my):
        dx = mx - ax
        dy = my - ay
        side = max(abs(dx), abs(dy))
        sx = 1 if dx >= 0 else -1
        sy = 1 if dy >= 0 else -1
        return ax, ay, ax + sx * side, ay + sy * side

    def _constrain_square_from_edge(self, x1, y1, x2, y2, edge, delta):
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        if edge == 'left':
            nx1 = x1 + delta; side = x2 - nx1; half = side / 2
            return nx1, cy - half, x2, cy + half
        if edge == 'right':
            nx2 = x2 + delta; side = nx2 - x1; half = side / 2
            return x1, cy - half, nx2, cy + half
        if edge == 'top':
            ny1 = y1 + delta; side = y2 - ny1; half = side / 2
            return cx - half, ny1, cx + half, y2
        if edge == 'bottom':
            ny2 = y2 + delta; side = ny2 - y1; half = side / 2
            return cx - half, y1, cx + half, ny2
        return x1, y1, x2, y2

    def _clamp_rect_to_image(self):
        il = self.display_offset_x
        it = self.display_offset_y
        ir = self.display_offset_x + self.display_width
        ib = self.display_offset_y + self.display_height
        x1, y1, x2, y2 = self.rect_x1, self.rect_y1, self.rect_x2, self.rect_y2
        side = min(abs(x2 - x1), abs(y2 - y1))
        x1 = max(il, min(x1, ir - side))
        y1 = max(it, min(y1, ib - side))
        x2 = x1 + side
        y2 = y1 + side
        if x2 > ir: x2 = ir; x1 = x2 - side
        if y2 > ib: y2 = ib; y1 = y2 - side
        self.rect_x1, self.rect_y1, self.rect_x2, self.rect_y2 = x1, y1, x2, y2

    def get_crop_in_image_coords(self):
        """Retourne (ox1, oy1, ox2, oy2) dans les coords de l'image originale."""
        img_w = self._img_w
        img_h = self._img_h
        if self.display_width == 0 or self.display_height == 0:
            return 0, 0, 0, 0
        rel_x1 = (self.rect_x1 - self.display_offset_x) / self.display_width
        rel_y1 = (self.rect_y1 - self.display_offset_y) / self.display_height
        rel_x2 = (self.rect_x2 - self.display_offset_x) / self.display_width
        rel_y2 = (self.rect_y2 - self.display_offset_y) / self.display_height
        ox1 = int(max(0, rel_x1 * img_w))
        oy1 = int(max(0, rel_y1 * img_h))
        ox2 = int(min(img_w, rel_x2 * img_w))
        oy2 = int(min(img_h, rel_y2 * img_h))
        return ox1, oy1, ox2, oy2

    def get_crop_side_px(self) -> int:
        if self.rect_rel_size is not None:
            return int(round(self.rect_rel_size * self._img_w))
        return 0

    # ── Events ──────────────────────────────────────────────────────────────

    def mouseMoveEvent(self, event):
        # Pan clic droit
        if self._pan_start is not None:
            dx = event.position().x() - self._pan_start.x()
            dy = event.position().y() - self._pan_start.y()
            self.display_offset_x = self._pan_orig_off[0] + dx
            self.display_offset_y = self._pan_orig_off[1] + dy
            self._rel_to_canvas()
            self.update()
            return

        # Drag cadre clic gauche
        if self._drag_mode is not None:
            ox1, oy1, ox2, oy2 = self._drag_rect_orig
            dx = event.position().x() - self._drag_start.x()
            dy = event.position().y() - self._drag_start.y()
            mode = self._drag_mode
            if mode == 'move':
                side = ox2 - ox1
                self.rect_x1 = ox1 + dx
                self.rect_y1 = oy1 + dy
                self.rect_x2 = self.rect_x1 + side
                self.rect_y2 = self.rect_y1 + side
            elif mode in ('tl', 'tr', 'bl', 'br'):
                ax, ay = {
                    'tl': (ox2, oy2), 'tr': (ox1, oy2),
                    'bl': (ox2, oy1), 'br': (ox1, oy1),
                }[mode]
                nx1, ny1, nx2, ny2 = self._constrain_square_from_corner(
                    ax, ay, event.position().x(), event.position().y()
                )
                self.rect_x1, self.rect_y1, self.rect_x2, self.rect_y2 = nx1, ny1, nx2, ny2
            elif mode == 'left':
                nx1, ny1, nx2, ny2 = self._constrain_square_from_edge(ox1, oy1, ox2, oy2, 'left', dx)
                self.rect_x1, self.rect_y1, self.rect_x2, self.rect_y2 = nx1, ny1, nx2, ny2
            elif mode == 'right':
                nx1, ny1, nx2, ny2 = self._constrain_square_from_edge(ox1, oy1, ox2, oy2, 'right', dx)
                self.rect_x1, self.rect_y1, self.rect_x2, self.rect_y2 = nx1, ny1, nx2, ny2
            elif mode == 'top':
                nx1, ny1, nx2, ny2 = self._constrain_square_from_edge(ox1, oy1, ox2, oy2, 'top', dy)
                self.rect_x1, self.rect_y1, self.rect_x2, self.rect_y2 = nx1, ny1, nx2, ny2
            elif mode == 'bottom':
                nx1, ny1, nx2, ny2 = self._constrain_square_from_edge(ox1, oy1, ox2, oy2, 'bottom', dy)
                self.rect_x1, self.rect_y1, self.rect_x2, self.rect_y2 = nx1, ny1, nx2, ny2

            # Normalise
            self.rect_x1, self.rect_x2 = min(self.rect_x1, self.rect_x2), max(self.rect_x1, self.rect_x2)
            self.rect_y1, self.rect_y2 = min(self.rect_y1, self.rect_y2), max(self.rect_y1, self.rect_y2)
            if self.rect_x2 - self.rect_x1 < 20: self.rect_x2 = self.rect_x1 + 20
            if self.rect_y2 - self.rect_y1 < 20: self.rect_y2 = self.rect_y1 + 20
            self._clamp_rect_to_image()
            self.update()
        else:
            handle = self._get_handle(event.position().x(), event.position().y())
            cursor = self._CURSORS.get(handle, Qt.ArrowCursor)
            self.setCursor(QCursor(cursor))

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self.on_double_click:
            self.on_double_click()

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self._pan_start = QPoint(int(event.position().x()), int(event.position().y()))
            self._pan_orig_off = (self.display_offset_x, self.display_offset_y)
            self.setCursor(QCursor(Qt.ClosedHandCursor))
        elif event.button() == Qt.LeftButton:
            x, y = event.position().x(), event.position().y()
            self._drag_mode = self._get_handle(x, y)
            self._drag_start = QPoint(int(x), int(y))
            self._drag_rect_orig = (self.rect_x1, self.rect_y1, self.rect_x2, self.rect_y2)
            if self.on_drag_start:
                self.on_drag_start()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self._pan_start = None
            self._pan_orig_off = None
            self._canvas_to_rel()
            self.setCursor(QCursor(Qt.ArrowCursor))
            if self.on_pan_changed:
                self.on_pan_changed((self.display_offset_x, self.display_offset_y))
            if self.on_rect_changed:
                self.on_rect_changed()
        elif event.button() == Qt.LeftButton:
            self._drag_mode = None
            self._drag_rect_orig = None
            self._canvas_to_rel()
            if self.on_rect_changed:
                self.on_rect_changed()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("black"))
        if self._pixmap:
            # Le pixmap est déjà à la bonne taille (disp_w × disp_h) — pas de rescaling
            painter.drawPixmap(self.display_offset_x, self.display_offset_y, self._pixmap)
        # Cadre rouge
        pen = QPen(QColor("red"), 2)
        painter.setPen(pen)
        x1 = int(self.rect_x1)
        y1 = int(self.rect_y1)
        w  = int(self.rect_x2 - self.rect_x1)
        h  = int(self.rect_y2 - self.rect_y1)
        painter.drawRect(x1, y1, w, h)
        painter.end()


# ─────────────────────────────────────────────────────────────────────────────
# Canvas Phase B — transparence
# ─────────────────────────────────────────────────────────────────────────────

class _TransparencyCanvas(QWidget):
    """
    Zone d'affichage de la phase B.
    Affiche l'image 256×256 sur un damier, gère la pipette flood-fill.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: #606060;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(100, 100)
        self.setMouseTracking(True)

        self._composed: QPixmap | None = None

        self._off_x = 0
        self._off_y = 0
        self._zoom  = 1.0

        # Pan clic droit
        self._pan_start: QPoint | None = None
        self._pan_orig_off: tuple | None = None

        self.pipette_active = False
        self.on_pipette_click = None   # callable(img_x, img_y)
        self.on_pan_changed = None     # callable(off_x, off_y)

    def set_composed(self, pixmap: QPixmap, off_x: int, off_y: int, zoom: float):
        self._composed = pixmap
        self._off_x = off_x
        self._off_y = off_y
        self._zoom  = zoom
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0x60, 0x60, 0x60))
        if self._composed:
            painter.drawPixmap(self._off_x, self._off_y, self._composed)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self._pan_start = QPoint(int(event.position().x()), int(event.position().y()))
            self._pan_orig_off = (self._off_x, self._off_y)
            self.setCursor(QCursor(Qt.ClosedHandCursor))
        elif event.button() == Qt.LeftButton and self.pipette_active:
            x = event.position().x()
            y = event.position().y()
            img_x = int((x - self._off_x) / self._zoom)
            img_y = int((y - self._off_y) / self._zoom)
            if 0 <= img_x < 256 and 0 <= img_y < 256:
                if self.on_pipette_click:
                    self.on_pipette_click(img_x, img_y)

    def mouseMoveEvent(self, event):
        if self._pan_start is not None:
            dx = event.position().x() - self._pan_start.x()
            dy = event.position().y() - self._pan_start.y()
            self._off_x = self._pan_orig_off[0] + dx
            self._off_y = self._pan_orig_off[1] + dy
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton and self._pan_start is not None:
            self._pan_start = None
            self._pan_orig_off = None
            self.setCursor(QCursor(Qt.CrossCursor if self.pipette_active else Qt.ArrowCursor))
            if self.on_pan_changed:
                self.on_pan_changed(self._off_x, self._off_y)

    def set_pipette(self, active: bool, theme: dict):
        self.pipette_active = active
        if self._pan_start is None:
            self.setCursor(QCursor(Qt.CrossCursor if active else Qt.ArrowCursor))


# ─────────────────────────────────────────────────────────────────────────────
# Dialogue principal IcoCreatorDialog
# ─────────────────────────────────────────────────────────────────────────────

class IcoCreatorDialog(QDialog):
    """Fenêtre de création d'un fichier .ico à partir d'une image de la mosaïque."""

    def __init__(self, parent, idx: int, callbacks: dict):
        super().__init__(parent)
        self._idx = idx
        self._callbacks = callbacks

        state = callbacks.get('state') or _state_module.state
        entry = state.images_data[idx]
        self._entry = entry

        original_img = ensure_image_loaded(entry)
        if original_img is None:
            self.reject()
            return
        self._original_img = original_img.convert("RGBA")

        # Zoom phase A
        self._zoom_level_a = 1.0
        # Offset de pan phase A (décalage utilisateur par rapport au centrage)
        self._pan_offset_a = (0, 0)

        # Zoom phase B
        self._zoom_level_b = 1.0
        self._pan_offset_b = (0, 0)

        # Image travaillée (256×256 RGBA) — initialisée en phase B
        self._ico_img_rgba: Image.Image | None = None
        self._ico_name: str = ""

        # Undo/redo local — phase A : tuples (rel_x1, rel_y1, rel_size)
        self._undo_stack_a: list = []
        self._redo_stack_a: list = []
        # Undo/redo local — phase B : copies PIL de _ico_img_rgba
        self._undo_stack_b: list = []
        self._redo_stack_b: list = []

        self.setModal(True)
        self.resize(900, 700)
        self.setMinimumSize(600, 500)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        # Phase courante : 'a' ou 'b'
        self._phase: str = 'a'
        self._phase_widget: QWidget | None = None

        self._build_phase_a()

        _connect_lang(self, lambda _: self._retranslate())
        self._center_parent = parent

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers nommage
    # ─────────────────────────────────────────────────────────────────────────

    def _get_ico_name(self) -> str:
        state = self._callbacks.get('state') or _state_module.state
        orig_name = self._entry.get("orig_name", "image")
        base_name = os.path.splitext(orig_name)[0]
        pattern = re.compile(r'^ICO(\d+)_', re.IGNORECASE)
        max_num = 0
        for e in state.images_data:
            m = pattern.match(e.get("orig_name", ""))
            if m:
                max_num = max(max_num, int(m.group(1)))
        return f"ICO{max_num + 1:03d}_{base_name}.ico"

    # ─────────────────────────────────────────────────────────────────────────
    # Phase A — découpe
    # ─────────────────────────────────────────────────────────────────────────

    def _build_phase_a(self):
        self._phase = 'a'
        if self._phase_widget:
            self._layout.removeWidget(self._phase_widget)
            self._phase_widget.deleteLater()

        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        # Label info
        self._info_label = QLabel()
        self._info_label.setAlignment(Qt.AlignCenter)
        self._info_label.setWordWrap(True)
        vbox.addWidget(self._info_label)

        # Canvas
        self._crop_canvas = _CropCanvas()
        self._crop_canvas.on_rect_changed = self._update_info_label_a
        self._crop_canvas.on_pan_changed = lambda off: setattr(self, '_pan_offset_a', off)
        self._crop_canvas.on_double_click = self._on_validate_crop
        self._crop_canvas.on_drag_start = self._undo_push_a
        vbox.addWidget(self._crop_canvas, stretch=1)

        # Barre de boutons
        self._btn_bar_a = QWidget()
        btn_row = QHBoxLayout(self._btn_bar_a)
        btn_row.setContentsMargins(10, 8, 10, 8)

        self._btn_undo_a = _make_icon_btn("BTN_Batch_Undo.png")
        self._btn_redo_a = _make_icon_btn("BTN_Batch_Redo.png")
        self._btn_undo_a.setEnabled(False)
        self._btn_redo_a.setEnabled(False)
        self._btn_undo_a.clicked.connect(self._undo)
        self._btn_redo_a.clicked.connect(self._redo)
        btn_row.addWidget(self._btn_undo_a)
        btn_row.addWidget(self._btn_redo_a)

        btn_row.addStretch()

        self._btn_validate_crop    = QPushButton()
        self._btn_validate_no_crop = QPushButton()
        self._btn_cancel_a         = QPushButton()
        for btn in (self._btn_validate_crop, self._btn_validate_no_crop, self._btn_cancel_a):
            btn.setFixedWidth(200)
            btn_row.addWidget(btn)
        btn_row.addStretch()

        self._btn_validate_crop.clicked.connect(self._on_validate_crop)
        self._btn_validate_no_crop.clicked.connect(self._on_validate_no_crop)
        self._btn_cancel_a.clicked.connect(self.reject)

        vbox.addWidget(self._btn_bar_a)

        self._phase_widget = container
        self._layout.addWidget(container)

        # Zoom clavier
        from PySide6.QtGui import QShortcut, QKeySequence
        self._shortcuts_a = [
            QShortcut(QKeySequence("Ctrl++"), self, lambda: self._adjust_zoom_a(0.1)),
            QShortcut(QKeySequence("Ctrl+-"), self, lambda: self._adjust_zoom_a(-0.1)),
            QShortcut(QKeySequence("Ctrl+0"), self, lambda: self._reset_zoom_a()),
            QShortcut(QKeySequence("Ctrl+Z"), self, self._undo),
            QShortcut(QKeySequence("Ctrl+Y"), self, self._redo),
        ]

        self._retranslate()
        self._first_show_a = True  # centrage différé au premier showEvent

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, '_first_show_a', False):
            self._first_show_a = False
            self._display_image_a(init=True)

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(11)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )
        self.setWindowTitle(_wt("dialogs.ico_creator.title"))

        if self._phase == 'a':
            self._btn_bar_a.setStyleSheet(f"background: {theme['bg']};")
            self._info_label.setFont(_get_current_font(9))
            self._info_label.setStyleSheet(
                f"color: {theme['text']}; background: {theme['bg']}; "
                f"padding: 6px; qproperty-alignment: AlignCenter;"
            )
            self._update_info_label_a()

            self._btn_validate_crop.setText(_("dialogs.ico_creator.btn_validate_crop"))
            self._btn_validate_crop.setFont(font)
            self._btn_validate_crop.setStyleSheet(btn_style)

            self._btn_validate_no_crop.setText(_("dialogs.ico_creator.btn_validate_no_crop"))
            self._btn_validate_no_crop.setFont(font)
            self._btn_validate_no_crop.setStyleSheet(btn_style)

            self._btn_cancel_a.setText(_("dialogs.ico_creator.btn_cancel"))
            self._btn_cancel_a.setFont(font)
            self._btn_cancel_a.setStyleSheet(btn_style)

            icon_btn_style = (
                f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
                f"border: 1px solid #aaaaaa; padding: 2px; }} "
                f"QPushButton:hover {{ background: {theme['separator']}; }}"
                f"QPushButton:disabled {{ opacity: 0.4; }}"
            )
            self._btn_undo_a.setStyleSheet(icon_btn_style)
            self._btn_redo_a.setStyleSheet(icon_btn_style)

        elif self._phase == 'b':
            self._retranslate_b()

    def _update_info_label_a(self):
        if self._phase != 'a':
            return
        side_px = self._crop_canvas.get_crop_side_px()
        self._info_label.setText(_("dialogs.ico_creator.info_message", w=side_px, h=side_px))

    # Zoom phase A

    def wheelEvent(self, event):
        if self._phase == 'a':
            if event.modifiers() & Qt.ControlModifier:
                delta = 0.1 if event.angleDelta().y() > 0 else -0.1
                self._adjust_zoom_a(delta)
                event.accept()
                return
        elif self._phase == 'b':
            if event.modifiers() & Qt.ControlModifier:
                delta = 0.1 if event.angleDelta().y() > 0 else -0.1
                self._adjust_zoom_b(delta)
                event.accept()
                return
        super().wheelEvent(event)

    def _adjust_zoom_a(self, delta):
        new_zoom = max(0.1, min(10.0, self._zoom_level_a + delta))
        if new_zoom != self._zoom_level_a:
            self._zoom_level_a = new_zoom
            self._display_image_a(init=False)

    def _reset_zoom_a(self):
        if self._zoom_level_a != 1.0:
            self._zoom_level_a = 1.0
            self._display_image_a(init=False)

    def _display_image_a(self, init=False):
        canvas = self._crop_canvas
        cw = canvas.width()
        ch = canvas.height()
        if cw < 2 or ch < 2:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(50, lambda: self._display_image_a(init=init))
            return

        img_w, img_h = self._original_img.size
        fit_ratio = min(cw / img_w, ch / img_h)
        effective = fit_ratio * self._zoom_level_a

        disp_w = int(img_w * effective)
        disp_h = int(img_h * effective)

        if init:
            # Recentre l'image et réinitialise le pan
            off_x = (cw - disp_w) // 2
            off_y = (ch - disp_h) // 2
            self._pan_offset_a = (off_x, off_y)
        else:
            # Conserve l'offset de pan existant
            off_x, off_y = self._pan_offset_a

        resized = self._original_img.resize((disp_w, disp_h), Image.LANCZOS)
        checker = _make_checkerboard_pixmap(disp_w, disp_h, tile=8)
        img_px = _pil_to_qpixmap(resized)
        painter = QPainter(checker)
        painter.drawPixmap(0, 0, img_px)
        painter.end()
        pixmap = checker

        canvas.set_image(pixmap, img_w, img_h, off_x, off_y, disp_w, disp_h, init)
        self._update_info_label_a()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._phase == 'a':
            self._recenter_on_resize_a()
        elif self._phase == 'b':
            self._display_image_b(recenter=True)

    def _recenter_on_resize_a(self):
        """Au resize, recalcule le centrage en conservant le delta de pan utilisateur."""
        canvas = self._crop_canvas
        cw = canvas.width()
        ch = canvas.height()
        if cw < 2 or ch < 2:
            return
        img_w, img_h = self._original_img.size
        fit_ratio = min(cw / img_w, ch / img_h)
        effective = fit_ratio * self._zoom_level_a
        disp_w = int(img_w * effective)
        disp_h = int(img_h * effective)
        # Nouveau centrage théorique
        new_center_x = (cw - disp_w) // 2
        new_center_y = (ch - disp_h) // 2
        self._pan_offset_a = (new_center_x, new_center_y)
        self._display_image_a(init=False)

    # Validation phase A

    # ─────────────────────────────────────────────────────────────────────────
    # Undo / Redo local
    # ─────────────────────────────────────────────────────────────────────────

    def _update_undo_redo_btns(self):
        if self._phase == 'a':
            self._btn_undo_a.setEnabled(bool(self._undo_stack_a))
            self._btn_redo_a.setEnabled(bool(self._redo_stack_a))
        elif self._phase == 'b':
            self._btn_undo_b.setEnabled(bool(self._undo_stack_b))
            self._btn_redo_b.setEnabled(bool(self._redo_stack_b))

    def _undo_push_a(self):
        """Sauvegarde l'état du rectangle avant une modification (phase A)."""
        cc = self._crop_canvas
        self._undo_stack_a.append((cc.rect_rel_x1, cc.rect_rel_y1, cc.rect_rel_size))
        self._redo_stack_a.clear()
        self._update_undo_redo_btns()

    def _undo_push_b(self):
        """Sauvegarde l'état de l'image avant une modification (phase B)."""
        self._undo_stack_b.append(self._ico_img_rgba.copy())
        self._redo_stack_b.clear()
        self._update_undo_redo_btns()

    def _undo(self):
        if self._phase == 'a' and self._undo_stack_a:
            cc = self._crop_canvas
            self._redo_stack_a.append((cc.rect_rel_x1, cc.rect_rel_y1, cc.rect_rel_size))
            x1, y1, sz = self._undo_stack_a.pop()
            cc.rect_rel_x1 = x1
            cc.rect_rel_y1 = y1
            cc.rect_rel_size = sz
            cc._rel_to_canvas()
            cc.update()
        elif self._phase == 'b' and self._undo_stack_b:
            self._redo_stack_b.append(self._ico_img_rgba.copy())
            self._ico_img_rgba = self._undo_stack_b.pop()
            self._display_image_b()
        self._update_undo_redo_btns()

    def _redo(self):
        if self._phase == 'a' and self._redo_stack_a:
            cc = self._crop_canvas
            self._undo_stack_a.append((cc.rect_rel_x1, cc.rect_rel_y1, cc.rect_rel_size))
            x1, y1, sz = self._redo_stack_a.pop()
            cc.rect_rel_x1 = x1
            cc.rect_rel_y1 = y1
            cc.rect_rel_size = sz
            cc._rel_to_canvas()
            cc.update()
        elif self._phase == 'b' and self._redo_stack_b:
            self._undo_stack_b.append(self._ico_img_rgba.copy())
            self._ico_img_rgba = self._redo_stack_b.pop()
            self._display_image_b()
        self._update_undo_redo_btns()

    def _on_validate_crop(self):
        ox1, oy1, ox2, oy2 = self._crop_canvas.get_crop_in_image_coords()
        side = min(ox2 - ox1, oy2 - oy1)
        if side <= 0:
            return
        cropped = self._original_img.crop((ox1, oy1, ox1 + side, oy1 + side))
        self._prepare_phase_b(cropped)

    def _on_validate_no_crop(self):
        img_w, img_h = self._original_img.size
        side = max(img_w, img_h)
        letterboxed = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        offset_x = (side - img_w) // 2
        offset_y = (side - img_h) // 2
        letterboxed.paste(self._original_img, (offset_x, offset_y))
        self._prepare_phase_b(letterboxed)

    def _prepare_phase_b(self, src_img: Image.Image):
        self._ico_img_rgba = src_img.resize((256, 256), Image.LANCZOS)
        self._ico_name = self._get_ico_name()
        # Réinitialise les piles undo/redo de la phase B
        self._undo_stack_b.clear()
        self._redo_stack_b.clear()
        # Supprime les shortcuts de la phase A
        for sc in getattr(self, '_shortcuts_a', []):
            sc.setEnabled(False)
        self._build_phase_b()

    # ─────────────────────────────────────────────────────────────────────────
    # Phase B — transparence
    # ─────────────────────────────────────────────────────────────────────────

    def _build_phase_b(self):
        self._phase = 'b'
        if self._phase_widget:
            self._layout.removeWidget(self._phase_widget)
            self._phase_widget.deleteLater()

        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        # Canvas transparence
        self._transp_canvas = _TransparencyCanvas()
        self._transp_canvas.on_pipette_click = self._on_pipette_click
        self._transp_canvas.on_pan_changed = lambda ox, oy: setattr(self, '_pan_offset_b', (ox, oy))
        vbox.addWidget(self._transp_canvas, stretch=1)

        # Barre du bas
        self._btn_bar_b = QWidget()
        btn_row = QHBoxLayout(self._btn_bar_b)
        btn_row.setContentsMargins(10, 8, 10, 8)

        self._btn_undo_b = _make_icon_btn("BTN_Batch_Undo.png")
        self._btn_redo_b = _make_icon_btn("BTN_Batch_Redo.png")
        self._btn_undo_b.setEnabled(False)
        self._btn_redo_b.setEnabled(False)
        self._btn_undo_b.clicked.connect(self._undo)
        self._btn_redo_b.clicked.connect(self._redo)
        btn_row.addWidget(self._btn_undo_b)
        btn_row.addWidget(self._btn_redo_b)

        btn_row.addStretch()

        self._btn_pipette      = QPushButton()
        self._btn_pipette.setCheckable(True)

        # Tolérance : label texte + (valeur chiffrée au-dessus du slider)
        self._tol_label  = QLabel()

        tol_widget = QWidget()
        tol_vbox = QVBoxLayout(tol_widget)
        tol_vbox.setContentsMargins(0, 0, 0, 0)
        tol_vbox.setSpacing(0)

        self._tol_value_label = QLabel(str(_TOLERANCE_DEFAULT))
        self._tol_value_label.setAlignment(Qt.AlignCenter)

        self._tol_slider = FocusSlider(Qt.Horizontal)
        self._tol_slider.setRange(_TOLERANCE_MIN, _TOLERANCE_MAX)
        self._tol_slider.setValue(_TOLERANCE_DEFAULT)
        self._tol_slider.setFixedWidth(150)

        tol_vbox.addWidget(self._tol_value_label)
        tol_vbox.addWidget(self._tol_slider)

        self._btn_validate_b   = QPushButton()
        self._btn_back_to_crop = QPushButton()
        self._btn_cancel_b     = QPushButton()

        for w in (self._btn_pipette, self._tol_label, tol_widget,
                  self._btn_validate_b, self._btn_back_to_crop, self._btn_cancel_b):
            btn_row.addWidget(w)

        self._tol_slider.valueChanged.connect(
            lambda v: self._tol_value_label.setText(str(v))
        )

        btn_row.addStretch()
        vbox.addWidget(self._btn_bar_b)

        self._btn_pipette.clicked.connect(self._toggle_pipette)
        self._btn_validate_b.clicked.connect(self._on_validate_final)
        self._btn_back_to_crop.clicked.connect(self._on_back_to_crop)
        self._btn_cancel_b.clicked.connect(self.reject)

        self._phase_widget = container
        self._layout.addWidget(container)

        # Shortcuts phase B
        from PySide6.QtGui import QShortcut, QKeySequence
        self._shortcuts_b = [
            QShortcut(QKeySequence("Ctrl++"), self, lambda: self._adjust_zoom_b(0.1)),
            QShortcut(QKeySequence("Ctrl+-"), self, lambda: self._adjust_zoom_b(-0.1)),
            QShortcut(QKeySequence("Ctrl+0"), self, lambda: self._reset_zoom_b()),
            QShortcut(QKeySequence("Ctrl+Z"), self, self._undo),
            QShortcut(QKeySequence("Ctrl+Y"), self, self._redo),
        ]

        self._retranslate()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._display_image_b)

    def _retranslate_b(self):
        theme = get_current_theme()
        font = _get_current_font(11)
        self._btn_bar_b.setStyleSheet(f"background: {theme['bg']};")
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        btn_checked_style = (
            f"QPushButton {{ background: {theme['separator']}; color: {theme['text']}; "
            f"border: 2px solid {theme['text']}; padding: 4px 8px; }}"
        )
        slider_style = (
            f"QSlider::groove:horizontal {{ height: 4px; background: {theme['separator']}; }} "
            f"QSlider::handle:horizontal {{ background: {theme['text']}; width: 12px; height: 12px; "
            f"margin: -4px 0; border-radius: 6px; }}"
        )

        self._tol_label.setText(_("dialogs.ico_creator.tolerance_label"))
        self._tol_label.setFont(font)
        self._tol_label.setStyleSheet(f"color: {theme['text']}; background: transparent;")

        self._tol_value_label.setFont(font)
        self._tol_value_label.setStyleSheet(f"color: {theme['text']}; background: transparent;")

        self._tol_slider.setStyleSheet(slider_style)

        self._btn_pipette.setText(_("dialogs.ico_creator.btn_transparency"))
        self._btn_pipette.setFont(font)
        self._btn_pipette.setStyleSheet(
            btn_checked_style if self._btn_pipette.isChecked() else btn_style
        )

        self._btn_validate_b.setText(_("dialogs.ico_creator.btn_validate_final"))
        self._btn_validate_b.setFont(font)
        self._btn_validate_b.setStyleSheet(btn_style)

        self._btn_back_to_crop.setText(_("dialogs.ico_creator.btn_back_to_crop"))
        self._btn_back_to_crop.setFont(font)
        self._btn_back_to_crop.setStyleSheet(btn_style)

        self._btn_cancel_b.setText(_("dialogs.ico_creator.btn_cancel"))
        self._btn_cancel_b.setFont(font)
        self._btn_cancel_b.setStyleSheet(btn_style)

        icon_btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 2px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
            f"QPushButton:disabled {{ opacity: 0.4; }}"
        )
        self._btn_undo_b.setStyleSheet(icon_btn_style)
        self._btn_redo_b.setStyleSheet(icon_btn_style)

    # Zoom phase B

    def _adjust_zoom_b(self, delta):
        new_zoom = max(0.1, min(10.0, self._zoom_level_b + delta))
        if new_zoom != self._zoom_level_b:
            self._zoom_level_b = new_zoom
            self._display_image_b()

    def _reset_zoom_b(self):
        if self._zoom_level_b != 1.0:
            self._zoom_level_b = 1.0
            self._display_image_b()

    def _compose_with_checkerboard(self) -> QPixmap:
        checker = _make_checkerboard_pixmap(256, 256, tile=8)
        img_px = _pil_to_qpixmap(self._ico_img_rgba)
        painter = QPainter(checker)
        painter.drawPixmap(0, 0, img_px)
        painter.end()
        return checker

    def _display_image_b(self, recenter=False):
        if self._ico_img_rgba is None:
            return
        canvas = self._transp_canvas
        cw = canvas.width()
        ch = canvas.height()
        if cw < 2 or ch < 2:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(50, self._display_image_b)
            return

        composed = self._compose_with_checkerboard()

        fit = min(cw / 256, ch / 256)
        effective = fit * self._zoom_level_b
        disp_w = int(256 * effective)
        disp_h = int(256 * effective)

        if recenter or self._pan_offset_b == (0, 0):
            off_x = (cw - disp_w) // 2
            off_y = (ch - disp_h) // 2
            self._pan_offset_b = (off_x, off_y)
        else:
            off_x, off_y = self._pan_offset_b

        scaled = composed.scaled(disp_w, disp_h, Qt.KeepAspectRatio, Qt.FastTransformation)
        canvas.set_composed(scaled, off_x, off_y, effective)

    # Pipette

    def _toggle_pipette(self):
        theme = get_current_theme()
        active = self._btn_pipette.isChecked()
        self._transp_canvas.set_pipette(active, theme)
        self._retranslate_b()

    def _on_pipette_click(self, img_x: int, img_y: int):
        tolerance = self._tol_slider.value()
        self._undo_push_b()
        self._apply_transparency(img_x, img_y, tolerance)
        self._display_image_b()

    def _apply_transparency(self, px: int, py: int, tolerance: int):
        """Flood fill 4-connexe : rend transparents les pixels similaires connectés."""
        ref = self._ico_img_rgba.getpixel((px, py))
        r, g, b = ref[0], ref[1], ref[2]
        if ref[3] == 0:
            return
        pixels = self._ico_img_rgba.load()
        stack = [(px, py)]
        visited = {(px, py)}
        while stack:
            cx, cy = stack.pop()
            p = pixels[cx, cy]
            pr, pg, pb = p[0], p[1], p[2]
            if max(abs(pr - r), abs(pg - g), abs(pb - b)) > tolerance:
                continue
            pixels[cx, cy] = (pr, pg, pb, 0)
            for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                if 0 <= nx < 256 and 0 <= ny < 256 and (nx, ny) not in visited:
                    visited.add((nx, ny))
                    stack.append((nx, ny))

    # Retour découpe

    def _on_back_to_crop(self):
        for sc in getattr(self, '_shortcuts_b', []):
            sc.setEnabled(False)
        self._zoom_level_a = 1.0
        self._build_phase_a()
        # showEvent ne se redéclenche pas (dialog déjà visible) → afficher l'image directement
        self._first_show_a = False
        self._display_image_a(init=True)

    # Validation finale

    def _on_validate_final(self):
        buf = io.BytesIO()
        self._ico_img_rgba.save(buf, format="ICO", sizes=_ICO_SIZES)
        ico_bytes = buf.getvalue()

        new_entry = {
            "orig_name":  self._ico_name,
            "extension":  ".ico",
            "bytes":      ico_bytes,
            "img":        None,
            "is_image":   True,
            "thumb":      None,
            "img_id":     None,
            "qt_pixmap_large": None,
        }

        state = self._callbacks.get('state') or _state_module.state
        insert_pos = self._idx + 1
        state.images_data.insert(insert_pos, new_entry)
        state.modified = True
        from modules.qt.comic_info import sync_pages_in_xml_data
        sync_pages_in_xml_data(state)

        save_state_qt(state, self._callbacks.get("refresh_toolbar"))

        self._callbacks["render_mosaic"]()
        self._callbacks["refresh_toolbar"]()

        from modules.qt.dialogs_qt import MsgDialog
        dlg = MsgDialog(self, "dialogs.ico_creator.success_title",
                        "dialogs.ico_creator.success_message",
                        message_kwargs={"filename": self._ico_name})
        dlg.exec()

        self.accept()


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée public
# ─────────────────────────────────────────────────────────────────────────────

def create_ico_from_selected(parent, callbacks: dict):
    """Ouvre la fenêtre de création .ico pour l'image sélectionnée."""
    state = callbacks.get('state') or _state_module.state
    if not state.selected_indices or len(state.selected_indices) != 1:
        return
    idx = list(state.selected_indices)[0]
    if idx >= len(state.images_data):
        return
    entry = state.images_data[idx]
    if not entry.get("is_image") or entry.get("is_corrupted"):
        return
    dlg = IcoCreatorDialog(parent, idx, callbacks)
    dlg.exec()
