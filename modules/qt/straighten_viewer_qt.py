"""
modules/qt/straighten_viewer_qt.py — Visionneuse de redressage d'images

L'utilisateur trace un trait rouge sur l'image avec le bouton gauche de la souris.
Le trait est interprété comme horizontal ou vertical selon son orientation,
et l'angle de correction est calculé puis appliqué à l'image par rotation PIL.

Classe publique :
  StraightenViewerDialog(parent, selected_entries, start_index, callbacks)

Fonction publique :
  show_straighten_viewer(parent, callbacks)
"""

import io
import math

from PIL import Image

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QFrame, QSizePolicy, QApplication,
)
from PySide6.QtCore import Qt, QPoint, QSize
from PySide6.QtGui import (
    QPixmap, QImage, QCursor, QKeySequence, QShortcut, QIcon,
    QPainter, QPen, QColor,
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


# ─────────────────────────────────────────────────────────────────────────────
# Widget image avec dessin de trait + pan clic-droit + zoom molette
# ─────────────────────────────────────────────────────────────────────────────

_HANDLE_RADIUS = 7      # rayon du cercle de contrôle (px)
_HANDLE_HIT    = 12     # rayon de détection du clic (px, > rayon pour faciliter le grab)


class _StraightenImageWidget(QWidget):
    """Affiche une image avec zoom, pan clic-droit, et tracé/édition de ligne clic-gauche.

    La ligne de référence est représentée par :
      - un trait rouge de 2px
      - deux cercles de contrôle rouges (contour blanc) aux extrémités
    Les cercles sont déplaçables au clic-gauche.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: black;")

        self._zoom = 1.0
        self._offset = QPoint(0, 0)
        self._pan_start = None
        self._pixmap = None

        # Trait de référence (coordonnées widget)
        self._line_start = None
        self._line_end   = None
        self._drawing    = False        # tracé initial en cours

        # Déplacement d'un point existant
        self._dragging_handle = None    # None | 'start' | 'end'

        # Callbacks
        self.on_zoom_changed = None     # callback(zoom_level)
        self.on_line_drawn   = None     # callback(img_x1, img_y1, img_x2, img_y2)

        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    # ── API publique ──────────────────────────────────────────────────────────

    def set_pixmap(self, pixmap, reset_offset=True):
        self._pixmap = pixmap
        self._line_start = None
        self._line_end   = None
        self._drawing    = False
        self._dragging_handle = None
        if reset_offset:
            self._offset = QPoint(0, 0)
        self.update()

    def clear_line(self):
        self._line_start = None
        self._line_end   = None
        self._drawing    = False
        self._dragging_handle = None
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

    # ── Rendu ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        if not self._pixmap:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = int(self._pixmap.width() * self._zoom)
        h = int(self._pixmap.height() * self._zoom)
        x = self._offset.x() + (self.width()  - w) // 2
        y = self._offset.y() + (self.height() - h) // 2

        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        painter.drawPixmap(x, y, w, h, self._pixmap)

        if self._line_start is not None and self._line_end is not None:
            # Trait rouge
            painter.setPen(QPen(QColor(255, 0, 0), 2, Qt.PenStyle.SolidLine))
            painter.drawLine(self._line_start, self._line_end)

            # Cercles de contrôle aux extrémités
            self._draw_handle(painter, self._line_start)
            self._draw_handle(painter, self._line_end)

    @staticmethod
    def _draw_handle(painter, pt):
        """Dessine un cercle plein rouge avec contour blanc au point pt."""
        r = _HANDLE_RADIUS
        from PySide6.QtCore import QRectF
        # Contour blanc (légèrement plus grand)
        painter.setPen(QPen(QColor(255, 255, 255), 1.5, Qt.PenStyle.SolidLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(pt.x() - r - 1, pt.y() - r - 1, (r + 1) * 2, (r + 1) * 2))
        # Disque rouge plein
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 0, 0))
        painter.drawEllipse(QRectF(pt.x() - r, pt.y() - r, r * 2, r * 2))

    # ── Détection des handles ─────────────────────────────────────────────────

    def _hit_handle(self, pos):
        """Retourne 'start', 'end' ou None selon la proximité du point pos."""
        if self._line_start is None or self._line_end is None:
            return None
        for name, pt in (('start', self._line_start), ('end', self._line_end)):
            dx = pos.x() - pt.x()
            dy = pos.y() - pt.y()
            if dx * dx + dy * dy <= _HANDLE_HIT * _HANDLE_HIT:
                return name
        return None

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
            hit = self._hit_handle(pos)
            if hit:
                # Déplace un point existant
                self._dragging_handle = hit
            else:
                # Nouveau tracé
                self._drawing    = True
                self._line_start = pos
                self._line_end   = pos
            self.update()
            event.accept()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._pan_start is not None:
            diff = pos - self._pan_start
            self._offset   += diff
            self._pan_start = pos
            self._clamp_offset()
            self.update()
        elif self._dragging_handle is not None:
            if self._dragging_handle == 'start':
                self._line_start = pos
            else:
                self._line_end = pos
            self.update()
        elif self._drawing:
            self._line_end = pos
            self.update()
        else:
            # Mise à jour du curseur selon la proximité des handles
            hit = self._hit_handle(pos)
            if hit:
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            else:
                self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        event.accept()

    def mouseReleaseEvent(self, event):
        pos = event.position().toPoint()
        if event.button() == Qt.MouseButton.RightButton:
            self._pan_start = None
            # Remet le curseur selon proximité
            hit = self._hit_handle(pos)
            self.setCursor(QCursor(
                Qt.CursorShape.SizeAllCursor if hit else Qt.CursorShape.CrossCursor))
            event.accept()
        elif event.button() == Qt.MouseButton.LeftButton:
            if self._dragging_handle is not None:
                # Fin du déplacement d'un handle — notifie le nouvel angle
                self._dragging_handle = None
                self._notify_line()
            elif self._drawing:
                self._drawing  = False
                self._line_end = pos
                self.update()
                self._notify_line()
            event.accept()

    def _notify_line(self):
        """Calcule les coordonnées image et appelle on_line_drawn si le trait est valide."""
        if (self.on_line_drawn
                and self._line_start is not None
                and self._line_end is not None
                and self._line_start != self._line_end):
            ix1, iy1 = self._widget_to_image(self._line_start)
            ix2, iy2 = self._widget_to_image(self._line_end)
            self.on_line_drawn(ix1, iy1, ix2, iy2)

    # ── Coordonnées ──────────────────────────────────────────────────────────

    def _widget_to_image(self, pt):
        if not self._pixmap:
            return 0, 0
        w  = int(self._pixmap.width()  * self._zoom)
        h  = int(self._pixmap.height() * self._zoom)
        ox = self._offset.x() + (self.width()  - w) // 2
        oy = self._offset.y() + (self.height() - h) // 2
        ix = int((pt.x() - ox) / self._zoom)
        iy = int((pt.y() - oy) / self._zoom)
        return ix, iy

    def _clamp_offset(self):
        if not self._pixmap:
            return
        w  = int(self._pixmap.width()  * self._zoom)
        h  = int(self._pixmap.height() * self._zoom)
        max_ox = max(0, (w - self.width())  // 2)
        max_oy = max(0, (h - self.height()) // 2)
        ox = max(-max_ox, min(max_ox, self._offset.x()))
        oy = max(-max_oy, min(max_oy, self._offset.y()))
        self._offset = QPoint(ox, oy)


# ─────────────────────────────────────────────────────────────────────────────
# Visionneuse principale
# ─────────────────────────────────────────────────────────────────────────────

class StraightenViewerDialog(QDialog):
    """Visionneuse de redressage d'images.

    Paramètres :
      selected_entries : liste des images (dicts avec 'bytes')
      start_index      : index dans selected_entries à afficher en premier
      callbacks        : dict MosaicView (save_state, render_mosaic, state)
    """

    def __init__(self, parent, selected_entries, start_index=0, callbacks=None):
        super().__init__(parent)
        self._selected_entries = selected_entries
        self._current_idx = max(0, min(start_index, len(selected_entries) - 1))
        self._callbacks = callbacks or {}

        # Historique par page : stocke les bytes AVANT chaque rotation
        # undo → restaure les bytes précédents
        # redo → réapplique la rotation depuis les bytes sauvegardés
        self._bytes_histories = {}   # dict idx → [bytes_before1, bytes_before2, ...]
        self._redo_stacks = {}       # dict idx → [(bytes_before, angle), ...]

        self._is_fullscreen = False

        theme = get_current_theme()
        self.setWindowTitle(_("dialogs.straighten_viewer.title"))
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

        # Séparateur
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {theme['separator']};")
        tb_layout.addWidget(sep)

        tb_layout.addStretch()

        # Message instruction — centré dans la toolbar
        self._instr_lbl = QLabel()
        self._instr_lbl.setFont(font_tb)
        self._instr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._instr_lbl.setWordWrap(False)
        tb_layout.addWidget(self._instr_lbl)

        tb_layout.addStretch()

        # Zoom − % +
        _icon_minus = _load_icon("BTN_-.png", 20)
        lbl_zoom_minus = QPushButton()
        lbl_zoom_minus.setFixedSize(28, 28)
        lbl_zoom_minus.setStyleSheet(_btn_style(theme))
        lbl_zoom_minus.clicked.connect(lambda: self._adjust_zoom(-0.15))
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

        _icon_plus = _load_icon("BTN_+.png", 20)
        lbl_zoom_plus = QPushButton()
        lbl_zoom_plus.setFixedSize(28, 28)
        lbl_zoom_plus.setStyleSheet(_btn_style(theme))
        lbl_zoom_plus.clicked.connect(lambda: self._adjust_zoom(0.15))
        if _icon_plus:
            lbl_zoom_plus.setIcon(_icon_plus)
            lbl_zoom_plus.setIconSize(QSize(20, 20))
        else:
            lbl_zoom_plus.setText("+")
            lbl_zoom_plus.setFont(font_tb)
        tb_layout.addWidget(lbl_zoom_plus)

        root.addWidget(tb)

        # ── Zone image ────────────────────────────────────────────────────────
        self._img_widget = _StraightenImageWidget()
        self._img_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._img_widget.on_zoom_changed = lambda z: self._zoom_lbl.setText(
            f"{int(z * 100)}%")
        self._img_widget.on_line_drawn = self._on_line_drawn
        root.addWidget(self._img_widget, stretch=1)

        # ── Barre du bas ──────────────────────────────────────────────────────
        bot = QWidget()
        bot.setStyleSheet(f"background: {theme['bg']}; color: {theme['text']};")
        bot_layout = QHBoxLayout(bot)
        bot_layout.setContentsMargins(10, 6, 10, 6)
        bot_layout.setSpacing(8)

        font_btn = _get_current_font(11)

        # Bouton Appliquer à cette page
        self._apply_btn = QPushButton()
        self._apply_btn.setFont(font_btn)
        self._apply_btn.setStyleSheet(_btn_style(theme))
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._apply_to_current)
        bot_layout.addWidget(self._apply_btn)

        # Séparateur
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet(f"color: {theme['separator']};")
        bot_layout.addWidget(sep2)

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

        # Bouton Annuler
        self._cancel_btn = QPushButton()
        self._cancel_btn.setFont(font_btn)
        self._cancel_btn.setStyleSheet(_btn_style(theme))
        self._cancel_btn.clicked.connect(self.reject)
        bot_layout.addWidget(self._cancel_btn)

        root.addWidget(bot)

        self._retranslate()

    # ── Navigation ────────────────────────────────────────────────────────────

    def _prev_image(self):
        if self._current_idx > 0:
            self._current_idx -= 1
            self._img_widget.clear_line()
            self._apply_btn.setEnabled(False)
            self._update_undo_redo_buttons()
            self._display_image(reset_offset=True)

    def _next_image(self):
        if self._current_idx < len(self._selected_entries) - 1:
            self._current_idx += 1
            self._img_widget.clear_line()
            self._apply_btn.setEnabled(False)
            self._update_undo_redo_buttons()
            self._display_image(reset_offset=True)

    # ── Affichage image ───────────────────────────────────────────────────────

    def _display_image(self, reset_offset=False):
        if not self._selected_entries or self._current_idx >= len(self._selected_entries):
            return
        entry = self._selected_entries[self._current_idx]
        if not entry.get('bytes'):
            return
        try:
            img = Image.open(io.BytesIO(entry['bytes']))
            has_alpha = (img.mode in ('RGBA', 'LA') or
                         (img.mode == 'P' and 'transparency' in img.info))
            if has_alpha:
                from modules.qt.adjustments_viewers_qt import AdjustmentViewerDialog
                img = AdjustmentViewerDialog.compose_checkerboard(img.convert('RGBA'))
            pixmap = self._pil_to_pixmap(img)
            self._img_widget.set_pixmap(pixmap, reset_offset=reset_offset)

            n = len(self._selected_entries)
            self._counter_lbl.setText(f"{self._current_idx + 1} / {n}")
            self._prev_btn.setEnabled(self._current_idx > 0)
            self._next_btn.setEnabled(self._current_idx < n - 1)
            self._zoom_lbl.setText(f"{int(self._img_widget.zoom_level() * 100)}%")
        except Exception as e:
            print(f"[straighten_viewer_qt] display_image : {e}")

    @staticmethod
    def _pil_to_pixmap(img):
        if img.mode not in ('RGB', 'RGBA'):
            img = img.convert('RGB')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        qimg = QImage()
        qimg.loadFromData(buf.read())
        return QPixmap.fromImage(qimg)

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

    # ── Tracé du trait → calcul de l'angle ───────────────────────────────────

    def _on_line_drawn(self, ix1, iy1, ix2, iy2):
        """Appelé quand l'utilisateur relâche le bouton gauche après avoir tracé un trait."""
        dx = ix2 - ix1
        dy = iy2 - iy1
        if dx == 0 and dy == 0:
            return

        # Calcule l'angle du trait par rapport à l'horizontale
        angle_rad = math.atan2(dy, dx)
        angle_deg = math.degrees(angle_rad)

        # Normalise dans [-90, 90]
        if angle_deg > 90:
            angle_deg -= 180
        elif angle_deg < -90:
            angle_deg += 180

        # Détermine si le trait est plutôt horizontal ou vertical
        # Note : PIL.rotate() tourne en anti-horaire pour les angles positifs,
        # mais l'axe Y écran est vers le bas (anti-mathématique) → on inverse le signe final.
        abs_angle = abs(angle_deg)
        if abs_angle <= 45:
            # Trait plutôt horizontal → correction = angle_deg (pas de signe -)
            correction = angle_deg
        else:
            # Trait plutôt vertical → angle par rapport à la verticale
            if angle_deg >= 0:
                correction = angle_deg - 90
            else:
                correction = angle_deg + 90

        # Stocke l'angle calculé et active le bouton Appliquer
        self._pending_angle = correction
        self._apply_btn.setEnabled(abs(correction) > 0.001)

    # ── Application ───────────────────────────────────────────────────────────

    def _apply_to_current(self):
        """Applique la rotation calculée à l'image courante (undo/redo interne à la visionneuse)."""
        angle = getattr(self, '_pending_angle', None)
        if angle is None or abs(angle) < 0.001:
            return

        entry = self._selected_entries[self._current_idx]
        if not entry.get('bytes'):
            return

        from modules.qt import state as _state_module
        state      = self._callbacks.get('state') or _state_module.state
        save_state = self._callbacks.get('save_state')
        render     = self._callbacks.get('render_mosaic')

        try:
            # Sauvegarde les bytes AVANT rotation pour l'undo
            bytes_before = entry['bytes']

            if save_state:
                save_state()

            img = Image.open(io.BytesIO(bytes_before))
            rotated = img.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)

            from modules.qt.entries import save_image_to_bytes
            entry['img'] = rotated
            entry['bytes'] = save_image_to_bytes(entry)
            entry['img'] = None
            entry['_thumbnail'] = None
            entry['large_thumb_pil'] = None
            entry['qt_pixmap_large'] = None
            entry['qt_qimage_large'] = None

            from modules.qt.comic_info import get_page_image_index, update_page_entries_in_xml_data
            pidx = get_page_image_index(state, entry)
            if pidx is not None:
                update_page_entries_in_xml_data(state, [(pidx, entry)])

            state.modified = True

            if save_state:
                save_state(force=True)

            # Enregistre bytes_before dans l'historique, vide le redo
            idx = self._current_idx
            if idx not in self._bytes_histories:
                self._bytes_histories[idx] = []
            self._bytes_histories[idx].append(bytes_before)
            if idx in self._redo_stacks:
                self._redo_stacks[idx].clear()

            self._update_undo_redo_buttons()

            # Rafraîchit l'image affichée et efface le trait
            self._img_widget.clear_line()
            self._apply_btn.setEnabled(False)
            self._pending_angle = None
            self._display_image()

            if render:
                render()

        except Exception as e:
            import traceback
            print(f"[straighten_viewer_qt] apply : {e}")
            traceback.print_exc()

    # ── Undo / Redo (historique interne de la visionneuse) ────────────────────

    def _undo(self):
        """Annule la dernière rotation : restaure les bytes d'avant la rotation."""
        idx = self._current_idx
        history = self._bytes_histories.get(idx, [])
        if not history:
            return

        entry = self._selected_entries[idx]
        if not entry.get('bytes'):
            return

        from modules.qt import state as _state_module
        state      = self._callbacks.get('state') or _state_module.state
        save_state = self._callbacks.get('save_state')
        render     = self._callbacks.get('render_mosaic')

        try:
            # Sauvegarde les bytes actuels (tournés) pour le redo
            bytes_current = entry['bytes']

            if save_state:
                save_state()

            # Restaure les bytes d'avant la rotation
            bytes_before = history.pop()
            entry['bytes'] = bytes_before
            entry['img'] = None
            entry['_thumbnail'] = None
            entry['large_thumb_pil'] = None
            entry['qt_pixmap_large'] = None
            entry['qt_qimage_large'] = None

            from modules.qt.comic_info import get_page_image_index, update_page_entries_in_xml_data
            pidx = get_page_image_index(state, entry)
            if pidx is not None:
                update_page_entries_in_xml_data(state, [(pidx, entry)])

            state.modified = True
            if save_state:
                save_state(force=True)

            if idx not in self._redo_stacks:
                self._redo_stacks[idx] = []
            self._redo_stacks[idx].append(bytes_current)

            self._update_undo_redo_buttons()
            self._img_widget.clear_line()
            self._apply_btn.setEnabled(False)
            self._pending_angle = None
            self._display_image()

            if render:
                render()

        except Exception as e:
            print(f"[straighten_viewer_qt] undo : {e}")

    def _redo(self):
        """Refait la dernière rotation annulée : restaure les bytes après rotation."""
        idx = self._current_idx
        redo_stack = self._redo_stacks.get(idx, [])
        if not redo_stack:
            return

        entry = self._selected_entries[idx]
        if not entry.get('bytes'):
            return

        from modules.qt import state as _state_module
        state      = self._callbacks.get('state') or _state_module.state
        save_state = self._callbacks.get('save_state')
        render     = self._callbacks.get('render_mosaic')

        try:
            # Sauvegarde les bytes actuels (avant-rotation) pour pouvoir re-undo
            bytes_current = entry['bytes']

            if save_state:
                save_state()

            # Restaure les bytes après rotation
            bytes_rotated = redo_stack.pop()
            entry['bytes'] = bytes_rotated
            entry['img'] = None
            entry['_thumbnail'] = None
            entry['large_thumb_pil'] = None
            entry['qt_pixmap_large'] = None
            entry['qt_qimage_large'] = None

            from modules.qt.comic_info import get_page_image_index, update_page_entries_in_xml_data
            pidx = get_page_image_index(state, entry)
            if pidx is not None:
                update_page_entries_in_xml_data(state, [(pidx, entry)])

            state.modified = True
            if save_state:
                save_state(force=True)

            if idx not in self._bytes_histories:
                self._bytes_histories[idx] = []
            self._bytes_histories[idx].append(bytes_current)

            self._update_undo_redo_buttons()
            self._img_widget.clear_line()
            self._apply_btn.setEnabled(False)
            self._pending_angle = None
            self._display_image()

            if render:
                render()

        except Exception as e:
            print(f"[straighten_viewer_qt] redo : {e}")

    def _update_undo_redo_buttons(self):
        idx = self._current_idx
        has_undo = bool(self._bytes_histories.get(idx))
        has_redo = bool(self._redo_stacks.get(idx))
        self._undo_btn.setEnabled(has_undo)
        self._redo_btn.setEnabled(has_redo)

    # ── Traduction ────────────────────────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(10)
        font_btn = _get_current_font(11)

        self.setWindowTitle(_("dialogs.straighten_viewer.title"))
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )

        self._instr_lbl.setText(_("dialogs.straighten_viewer.instruction"))
        self._instr_lbl.setFont(font)

        self._prev_btn.setFont(_get_current_font(12))
        self._next_btn.setFont(_get_current_font(12))
        self._counter_lbl.setFont(font)
        self._zoom_lbl.setFont(font)

        self._apply_btn.setFont(font_btn)
        self._apply_btn.setText(_("dialogs.levels_viewer.apply_current"))
        self._cancel_btn.setFont(font_btn)
        self._cancel_btn.setText(_("buttons.cancel"))

    # ── Keyboard ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Left:
            self._prev_image()
        elif key == Qt.Key.Key_Right:
            self._next_image()
        elif key == Qt.Key.Key_Escape:
            if self._is_fullscreen:
                self._toggle_fullscreen()
            else:
                self.reject()
        else:
            super().keyPressEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée public
# ─────────────────────────────────────────────────────────────────────────────

def show_straighten_viewer(parent=None, callbacks=None):
    """Ouvre la visionneuse de redressage.

    Si des images sont sélectionnées, ouvre sur la première sélectionnée.
    Sinon ouvre sur la première image de la mosaïque.
    """
    callbacks = callbacks or {}
    from modules.qt import state as _state_module
    state = callbacks.get('state') or _state_module.state

    if not state.images_data:
        return

    # Récupère toutes les entrées image (non corrompues)
    image_entries = [
        (i, e) for i, e in enumerate(state.images_data)
        if e.get('is_image') and not e.get('is_corrupted')
    ]
    if not image_entries:
        return

    # Détermine l'index de départ
    if state.selected_indices:
        # Première image sélectionnée parmi les images valides
        selected_real_indices = {
            i for i in state.selected_indices
            if i < len(state.images_data)
            and state.images_data[i].get('is_image')
            and not state.images_data[i].get('is_corrupted')
        }
        if selected_real_indices:
            first_sel = min(selected_real_indices)
            # Trouve l'index dans image_entries
            start_index = next(
                (j for j, (i, _) in enumerate(image_entries) if i == first_sel), 0
            )
        else:
            start_index = 0
    else:
        start_index = 0

    selected_entries = [e for _, e in image_entries]

    dlg = StraightenViewerDialog(parent, selected_entries, start_index,
                                 callbacks=callbacks)
    dlg.exec()
