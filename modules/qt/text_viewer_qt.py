"""
modules/qt/text_viewer_qt.py — Visionneuse d'ajout de texte rich text sur image

L'utilisateur clique sur l'image pour placer des blocs QTextEdit transparents superposés.
Plusieurs blocs peuvent coexister simultanément.
"Appliquer" aplatit tous les blocs dans l'ordre sur l'image PIL.

Classe publique :
  TextViewerDialog(parent, selected_entries, start_index, callbacks)

Fonction publique :
  show_text_viewer(parent, callbacks)
"""

import io
from PIL import Image

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QFrame, QSizePolicy, QSpinBox,
    QFontComboBox, QColorDialog, QTextEdit,
)
from PySide6.QtCore import Qt, QPoint, QSize, QRect, QRectF, Signal
from PySide6.QtGui import (
    QPixmap, QImage, QCursor, QKeySequence, QShortcut, QIcon,
    QPainter, QPen, QColor, QFont,
    QTextCharFormat, QTextOption,
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

def _btn_toggle_style(theme, checked, font_style=""):
    bg = theme['separator'] if checked else theme['toolbar_bg']
    extra = f" {font_style}" if font_style else ""
    return (
        f"QPushButton {{ background: {bg}; color: {theme['text']}; "
        f"border: 1px solid {'#4488cc' if checked else '#aaaaaa'}; "
        f"padding: 4px 8px;{extra} }} "
        f"QPushButton:hover {{ background: {theme['separator']}; }}"
    )

def _combo_style(theme):
    return (
        f"QComboBox {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
        f"border: 1px solid #aaaaaa; padding: 2px 6px; }} "
        f"QComboBox QAbstractItemView {{ background: {theme['bg']}; color: {theme['text']}; "
        f"selection-background-color: {theme['separator']}; }}"
    )

def _spinbox_style(theme):
    return (
        f"QSpinBox {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
        f"border: 1px solid #aaaaaa; padding: 2px 4px; }}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Damier (transparence)
# ─────────────────────────────────────────────────────────────────────────────

def _make_checker(w, h, tile=12):
    import array as _array
    light_val, dark_val = 204, 128
    row_light = bytes(_array.array('B', [
        light_val if (x // tile) % 2 == 0 else dark_val for x in range(w)
    ]))
    row_dark = bytes(_array.array('B', [
        dark_val if (x // tile) % 2 == 0 else light_val for x in range(w)
    ]))
    rows = [row_light if (y // tile) % 2 == 0 else row_dark for y in range(h)]
    checker_l = Image.frombytes('L', (w, h), b''.join(rows))
    return checker_l.convert('RGB')


def _compose_on_checker(rgba_img, checker_bg):
    w, h = rgba_img.size
    display = checker_bg.copy()
    display.paste(rgba_img.convert('RGB'), mask=rgba_img.split()[3])
    raw = display.tobytes('raw', 'RGB')
    qimg = QImage(raw, w, h, w * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


# ─────────────────────────────────────────────────────────────────────────────
# Curseur I-beam
# ─────────────────────────────────────────────────────────────────────────────

def _make_text_cursor():
    size = 32
    cx = cy = size // 2
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen_white = QPen(QColor(255, 255, 255, 220), 2.0)
    pen_black = QPen(QColor(0, 0, 0, 240), 1.2)
    for pen in (pen_white, pen_black):
        painter.setPen(pen)
        painter.drawLine(cx, cy - 10, cx, cy + 10)
        painter.drawLine(cx - 4, cy - 10, cx + 4, cy - 10)
        painter.drawLine(cx - 4, cy + 10, cx + 4, cy + 10)
    vx, vy = cx + 8, cy - 8
    r = 4
    for pen in (pen_white, pen_black):
        painter.setPen(pen)
        painter.drawLine(vx - r, vy, vx + r, vy)
        painter.drawLine(vx, vy - r, vx, vy + r)
    painter.end()
    return QCursor(pm, cx, cy)

_TEXT_CURSOR = None

def _get_text_cursor():
    global _TEXT_CURSOR
    if _TEXT_CURSOR is None:
        _TEXT_CURSOR = _make_text_cursor()
    return _TEXT_CURSOR


# ─────────────────────────────────────────────────────────────────────────────
# Overlay rich text (QTextEdit transparent)
# ─────────────────────────────────────────────────────────────────────────────

class _RichTextOverlay(QTextEdit):
    """QTextEdit transparent positionné en overlay sur _TextImageWidget.

    Signaux :
      content_changed()   — texte ou format modifié
      block_move(dx, dy)  — déplacement Ctrl+flèche (pixels image)
      activated()         — l'overlay a reçu le focus (clic dessus)
    """

    content_changed = Signal()
    block_move      = Signal(int, int)
    activated       = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setStyleSheet(
            "QTextEdit { background: rgba(0,0,0,0); border: 1px dashed rgba(0,120,255,180); }"
            "QTextEdit QAbstractScrollArea { background: rgba(0,0,0,0); }"
        )

        self.setFrameStyle(QFrame.Shape.NoFrame)
        self.setAcceptRichText(True)
        self.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.document().contentsChanged.connect(self._on_contents_changed)
        self.cursorPositionChanged.connect(self._on_cursor_moved)

        self.hide()

    # ── Taille auto ───────────────────────────────────────────────────────────

    def _on_contents_changed(self):
        self._adjust_size()
        self.content_changed.emit()

    def _adjust_size(self):
        doc = self.document()
        doc.setTextWidth(-1)
        sz = doc.size()
        w = max(int(sz.width()) + 20, 60)
        h = max(int(sz.height()) + 8, 24)
        self.resize(w, h)

    def _on_cursor_moved(self):
        # Le curseur ne change pas le contenu à rendre — pas d'émission de content_changed.
        pass

    # ── Format ────────────────────────────────────────────────────────────────

    def apply_char_format(self, fmt: QTextCharFormat):
        cursor = self.textCursor()
        cursor.mergeCharFormat(fmt)
        self.setTextCursor(cursor)
        self.content_changed.emit()

    # ── Clavier ───────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key  = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)

        if ctrl and key in (Qt.Key.Key_Left, Qt.Key.Key_Right,
                             Qt.Key.Key_Up, Qt.Key.Key_Down):
            dx, dy = 0, 0
            if key == Qt.Key.Key_Left:    dx = -1
            elif key == Qt.Key.Key_Right: dx =  1
            elif key == Qt.Key.Key_Up:    dy = -1
            elif key == Qt.Key.Key_Down:  dy =  1
            self.block_move.emit(dx, dy)
            event.accept()
            return

        if ctrl and key == Qt.Key.Key_Z:
            self.document().undo()
            event.accept()
            return
        if ctrl and key == Qt.Key.Key_Y:
            self.document().redo()
            event.accept()
            return

        super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.activated.emit()

    # ── Utilitaires ───────────────────────────────────────────────────────────

    def set_active_style(self, active: bool):
        """Bordure bleue (actif) ou grise (inactif)."""
        if active:
            self.setStyleSheet(
                "QTextEdit { background: rgba(0,0,0,0); border: 1px dashed rgba(0,120,255,180); }"
                "QTextEdit QAbstractScrollArea { background: rgba(0,0,0,0); }"
            )
        else:
            self.setStyleSheet(
                "QTextEdit { background: rgba(0,0,0,0); border: 1px dashed rgba(160,160,160,140); }"
                "QTextEdit QAbstractScrollArea { background: rgba(0,0,0,0); }"
            )

    def safe_clear(self):
        """Vide le document sans déclencher les signaux."""
        self.document().blockSignals(True)
        self.blockSignals(True)
        self.setPlainText("")
        self.blockSignals(False)
        self.document().blockSignals(False)


# ─────────────────────────────────────────────────────────────────────────────
# Bloc texte (overlay + position image)
# ─────────────────────────────────────────────────────────────────────────────

class _TextBlock:
    """Regroupe un _RichTextOverlay et sa position en coordonnées image."""

    def __init__(self, overlay: _RichTextOverlay, img_x: int, img_y: int):
        self.overlay = overlay
        self.img_pos = QPoint(img_x, img_y)   # coordonnées image

    def html(self) -> str:
        return self.overlay.toHtml()

    def plain_text(self) -> str:
        return self.overlay.toPlainText()

    def is_empty(self) -> bool:
        return not self.plain_text().strip()


# ─────────────────────────────────────────────────────────────────────────────
# Widget image — héberge N blocs
# ─────────────────────────────────────────────────────────────────────────────

class _TextImageWidget(QWidget):
    """Affiche l'image + héberge plusieurs _RichTextOverlay en enfants."""

    # Signaux vers le dialog
    place_text   = Signal(int, int)          # nouveau bloc demandé
    block_moved  = Signal(object, int, int)  # (_TextBlock, new_ix, new_iy)
    block_activated = Signal(object)         # (_TextBlock) devient actif

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: black;")

        self._zoom    = 1.0
        self._offset  = QPoint(0, 0)
        self._pan_start = None
        self._pixmap  = None

        # Liste des blocs actifs
        self._blocks: list[_TextBlock] = []
        self._active_block: _TextBlock | None = None

        # Drag
        self._drag_block: _TextBlock | None = None
        self._drag_pending  = False
        self._dragging_text = False
        self._drag_start_widget  = None
        self._drag_start_img_pos = None

        # Callback zoom
        self.on_zoom_changed = None

        self.setMouseTracking(True)
        self.setCursor(_get_text_cursor())

    # ── API publique ──────────────────────────────────────────────────────────

    def add_block(self, img_x: int, img_y: int,
                  on_content_changed, on_block_move) -> _TextBlock:
        """Crée un nouveau bloc, le positionne et le rend actif."""
        overlay = _RichTextOverlay(self)
        overlay.content_changed.connect(on_content_changed)
        overlay.block_move.connect(lambda dx, dy, b=None: self._on_block_move_signal(dx, dy))
        overlay.activated.connect(lambda b=None: None)

        block = _TextBlock(overlay, img_x, img_y)
        overlay.activated.connect(lambda bl=block: self._activate_block(bl))

        self._blocks.append(block)
        self._activate_block(block)

        wpt = self._image_to_widget(img_x, img_y)
        overlay.move(wpt)
        overlay.show()
        overlay.setFocus()

        return block

    def _activate_block(self, block: _TextBlock):
        """Rend un bloc actif, met les autres en inactif."""
        if self._active_block is block:
            return
        if self._active_block is not None:
            self._active_block.overlay.set_active_style(False)
            cursor = self._active_block.overlay.textCursor()
            cursor.clearSelection()
            self._active_block.overlay.setTextCursor(cursor)
        self._active_block = block
        block.overlay.set_active_style(True)
        self.block_activated.emit(block)

    def active_block(self) -> '_TextBlock | None':
        return self._active_block

    def clear_blocks(self):
        """Supprime tous les blocs."""
        for b in self._blocks:
            b.overlay.hide()
            b.overlay.deleteLater()
        self._blocks.clear()
        self._active_block = None

    def blocks(self) -> list:
        return list(self._blocks)

    def move_active_block_to(self, img_x: int, img_y: int):
        if self._active_block is None:
            return
        self._active_block.img_pos = QPoint(img_x, img_y)
        self._reposition_block(self._active_block)

    def reposition_all(self):
        for b in self._blocks:
            self._reposition_block(b)

    def set_pixmap(self, pixmap, reset_offset=True):
        self._pixmap = pixmap
        if reset_offset:
            self._offset = QPoint(0, 0)
        self.reposition_all()
        self.update()

    def update_pixmap(self, pixmap):
        self._pixmap = pixmap
        self.update()

    def zoom_level(self):
        return self._zoom

    def set_zoom(self, z):
        self._zoom = max(0.1, min(10.0, z))
        self.reposition_all()
        self.update()
        if self.on_zoom_changed:
            self.on_zoom_changed(self._zoom)

    def adjust_zoom(self, delta):
        self.set_zoom(self._zoom + delta)

    def reset_zoom(self):
        self._zoom = 1.0
        self._offset = QPoint(0, 0)
        self.reposition_all()
        self.update()
        if self.on_zoom_changed:
            self.on_zoom_changed(self._zoom)

    # ── Coordonnées ──────────────────────────────────────────────────────────

    def _img_origin(self):
        if not self._pixmap:
            return 0, 0
        w = int(self._pixmap.width()  * self._zoom)
        h = int(self._pixmap.height() * self._zoom)
        return (self._offset.x() + (self.width()  - w) // 2,
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

    def _reposition_block(self, block: _TextBlock):
        wpt = self._image_to_widget(block.img_pos.x(), block.img_pos.y())
        block.overlay.move(wpt)

    def _block_at(self, pos) -> '_TextBlock | None':
        """Retourne le bloc sous pos (widget coords), ou None."""
        # Parcours en sens inverse pour priorité au dernier ajouté
        for b in reversed(self._blocks):
            if not b.overlay.isVisible():
                continue
            r = QRect(b.overlay.pos(), b.overlay.size())
            if r.adjusted(-4, -4, 4, 4).contains(pos):
                return b
        return None

    def _clamp_offset(self):
        if not self._pixmap:
            return
        w = int(self._pixmap.width()  * self._zoom)
        h = int(self._pixmap.height() * self._zoom)
        max_ox = max(0, (w - self.width())  // 2)
        max_oy = max(0, (h - self.height()) // 2)
        ox = max(-max_ox, min(max_ox, self._offset.x()))
        oy = max(-max_oy, min(max_oy, self._offset.y()))
        self._offset = QPoint(ox, oy)

    # ── Rendu ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        if not self._pixmap:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w = int(self._pixmap.width()  * self._zoom)
        h = int(self._pixmap.height() * self._zoom)
        x = self._offset.x() + (self.width()  - w) // 2
        y = self._offset.y() + (self.height() - h) // 2
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        painter.drawPixmap(x, y, w, h, self._pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reposition_all()

    # ── Souris ───────────────────────────────────────────────────────────────

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
            hit = self._block_at(pos)
            if hit is not None:
                # Clic sur un bloc existant → activer + préparer drag
                self._activate_block(hit)
                self._drag_block   = hit
                self._drag_pending = True
                self._drag_start_widget  = pos
                self._drag_start_img_pos = QPoint(*self._widget_to_image(pos))
            else:
                # Clic sur zone vide → nouveau bloc
                ix, iy = self._widget_to_image(pos)
                self.place_text.emit(ix, iy)
            event.accept()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._pan_start is not None:
            diff = pos - self._pan_start
            self._offset += diff
            self._pan_start = pos
            self._clamp_offset()
            self.reposition_all()
            self.update()
        elif self._drag_pending and self._drag_start_widget is not None:
            diff = pos - self._drag_start_widget
            if diff.x() ** 2 + diff.y() ** 2 >= 16:
                self._drag_pending  = False
                self._dragging_text = True
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        elif self._dragging_text and self._drag_block is not None:
            cur_img = QPoint(*self._widget_to_image(pos))
            dx = cur_img.x() - self._drag_start_img_pos.x()
            dy = cur_img.y() - self._drag_start_img_pos.y()
            new_pos = QPoint(
                self._drag_start_img_pos.x() + dx,
                self._drag_start_img_pos.y() + dy,
            )
            self._drag_block.img_pos = new_pos
            self._drag_start_img_pos = cur_img
            self._reposition_block(self._drag_block)
            self.update()
        else:
            if self._block_at(pos) is not None:
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            else:
                self.setCursor(_get_text_cursor())
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._pan_start = None
            pos = event.position().toPoint()
            self.setCursor(
                QCursor(Qt.CursorShape.SizeAllCursor)
                if self._block_at(pos) else _get_text_cursor()
            )
            event.accept()
        elif event.button() == Qt.MouseButton.LeftButton:
            if self._dragging_text:
                self._dragging_text = False
                if self._drag_block is not None:
                    b = self._drag_block
                    self.block_moved.emit(b, b.img_pos.x(), b.img_pos.y())
                pos = event.position().toPoint()
                self.setCursor(
                    QCursor(Qt.CursorShape.SizeAllCursor)
                    if self._block_at(pos) else _get_text_cursor()
                )
            elif self._drag_pending:
                self._drag_pending = False
                # Simple clic → focus sur l'overlay du bloc
                if self._drag_block is not None:
                    self._drag_block.overlay.setFocus()
            self._drag_block = None
            self._drag_start_widget  = None
            self._drag_start_img_pos = None
            event.accept()

    def _on_block_move_signal(self, dx: int, dy: int):
        """Ctrl+flèche depuis un overlay actif."""
        if self._active_block is None:
            return
        b = self._active_block
        b.img_pos = QPoint(b.img_pos.x() + dx, b.img_pos.y() + dy)
        self._reposition_block(b)
        self.block_moved.emit(b, b.img_pos.x(), b.img_pos.y())


# ─────────────────────────────────────────────────────────────────────────────
# Visionneuse principale
# ─────────────────────────────────────────────────────────────────────────────

class TextViewerDialog(QDialog):

    def __init__(self, parent, selected_entries, start_index=0, callbacks=None):
        super().__init__(parent)
        self._selected_entries = selected_entries
        self._current_idx = max(0, min(start_index, len(selected_entries) - 1))
        self._callbacks = callbacks or {}

        # Historique undo/redo par page : liste de (bytes_avant, snapshot_blocs)
        # snapshot_blocs = liste de (ix, iy, html)
        self._histories   = {}
        self._redo_stacks = {}

        self._work_img    = None
        self._checker_bg  = None

        self._text_color = QColor(0, 0, 0, 255)
        self._is_fullscreen = False
        self._ignore_format_signals = False

        theme = get_current_theme()
        self.setWindowTitle(_("dialogs.text_viewer.title"))
        self.resize(960, 750)
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )

        self._build_ui(theme)
        _connect_lang(self, self._retranslate)
        self._center_parent = parent

        QShortcut(QKeySequence("F11"),    self).activated.connect(self._toggle_fullscreen)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self._img_widget.reset_zoom)
        QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self._undo)
        QShortcut(QKeySequence("Ctrl+Y"), self).activated.connect(self._redo)

        # Connexions du widget image
        self._img_widget.place_text.connect(self._on_place_text)
        self._img_widget.block_moved.connect(self._on_block_moved)
        self._img_widget.block_activated.connect(self._on_block_activated)

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

        # ── Toolbar navigation + zoom ──────────────────────────────────────────
        tb = QWidget()
        tb.setFixedHeight(50)
        tb.setStyleSheet(f"background: {theme['toolbar_bg']}; color: {theme['text']};")
        tb_layout = QHBoxLayout(tb)
        tb_layout.setContentsMargins(8, 4, 8, 4)
        tb_layout.setSpacing(6)

        font_tb = _get_current_font(10)

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

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {theme['separator']};")
        tb_layout.addWidget(sep)

        tb_layout.addStretch()

        self._instr_lbl = QLabel()
        self._instr_lbl.setFont(font_tb)
        self._instr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tb_layout.addWidget(self._instr_lbl)

        tb_layout.addStretch()

        _icon_minus = _load_icon("BTN_-.png", 20)
        self._zoom_minus_btn = QPushButton()
        self._zoom_minus_btn.setFixedSize(28, 28)
        self._zoom_minus_btn.setStyleSheet(_btn_style(theme))
        self._zoom_minus_btn.clicked.connect(lambda: self._adjust_zoom(-0.15))
        if _icon_minus:
            self._zoom_minus_btn.setIcon(_icon_minus)
            self._zoom_minus_btn.setIconSize(QSize(20, 20))
        else:
            self._zoom_minus_btn.setText("−"); self._zoom_minus_btn.setFont(font_tb)
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
            self._zoom_plus_btn.setText("+"); self._zoom_plus_btn.setFont(font_tb)
        tb_layout.addWidget(self._zoom_plus_btn)

        root.addWidget(tb)

        # ── Barre d'options rich text ──────────────────────────────────────────
        opt = QWidget()
        opt.setStyleSheet(f"background: {theme['toolbar_bg']}; color: {theme['text']};")
        opt_layout = QHBoxLayout(opt)
        opt_layout.setContentsMargins(8, 4, 8, 4)
        opt_layout.setSpacing(6)

        font_opt = _get_current_font(10)

        self._font_combo = QFontComboBox()
        self._font_combo.setEditable(False)
        self._font_combo.setFont(font_opt)
        self._font_combo.setStyleSheet(_combo_style(theme))
        self._font_combo.setMinimumWidth(160)
        self._font_combo.setMaximumWidth(220)
        self._font_combo.currentFontChanged.connect(self._on_font_family_changed)
        opt_layout.addWidget(self._font_combo)

        self._lbl_size = QLabel()
        self._lbl_size.setFont(font_opt)
        opt_layout.addWidget(self._lbl_size)

        self._size_spin = QSpinBox()
        self._size_spin.setFont(font_opt)
        self._size_spin.setStyleSheet(_spinbox_style(theme))
        self._size_spin.setMinimum(6)
        self._size_spin.setMaximum(500)
        self._size_spin.setValue(24)
        self._size_spin.setFixedWidth(60)
        self._size_spin.valueChanged.connect(self._on_font_size_changed)
        opt_layout.addWidget(self._size_spin)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet(f"color: {theme['separator']};")
        opt_layout.addWidget(sep2)

        self._bold_btn = QPushButton(_("dialogs.text_viewer.bold_btn"))
        self._bold_btn.setFixedSize(28, 28)
        self._bold_btn.setCheckable(True)
        self._bold_btn.setStyleSheet(_btn_toggle_style(theme, False, "font-weight: bold;"))
        self._bold_btn.clicked.connect(self._on_bold_clicked)
        opt_layout.addWidget(self._bold_btn)

        self._italic_btn = QPushButton(_("dialogs.text_viewer.italic_btn"))
        self._italic_btn.setFixedSize(28, 28)
        self._italic_btn.setCheckable(True)
        self._italic_btn.setStyleSheet(_btn_toggle_style(theme, False, "font-style: italic;"))
        self._italic_btn.clicked.connect(self._on_italic_clicked)
        opt_layout.addWidget(self._italic_btn)

        self._underline_btn = QPushButton(_("dialogs.text_viewer.underline_btn"))
        self._underline_btn.setFixedSize(28, 28)
        self._underline_btn.setCheckable(True)
        self._underline_btn.setStyleSheet(_btn_toggle_style(theme, False, "text-decoration: underline;"))
        self._underline_btn.clicked.connect(self._on_underline_clicked)
        opt_layout.addWidget(self._underline_btn)

        sep3 = QFrame(); sep3.setFrameShape(QFrame.Shape.VLine)
        sep3.setStyleSheet(f"color: {theme['separator']};")
        opt_layout.addWidget(sep3)

        self._lbl_color = QLabel()
        self._lbl_color.setFont(font_opt)
        opt_layout.addWidget(self._lbl_color)

        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(28, 28)
        self._color_btn.setStyleSheet(
            f"QPushButton {{ background: {self._text_color.name()}; border: 1px solid #aaaaaa; }}"
        )
        self._color_btn.clicked.connect(self._pick_color)
        opt_layout.addWidget(self._color_btn)

        opt_layout.addStretch()
        root.addWidget(opt)

        hline = QFrame(); hline.setFrameShape(QFrame.Shape.HLine)
        hline.setStyleSheet(f"color: {theme['separator']};")
        root.addWidget(hline)

        # ── Zone image ────────────────────────────────────────────────────────
        self._img_widget = _TextImageWidget()
        self._img_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._img_widget.on_zoom_changed = lambda z: self._zoom_lbl.setText(
            f"{int(z * 100)}%")
        root.addWidget(self._img_widget, stretch=1)

        # ── Barre du bas ──────────────────────────────────────────────────────
        bot = QWidget()
        bot.setStyleSheet(f"background: {theme['bg']}; color: {theme['text']};")
        bot_layout = QHBoxLayout(bot)
        bot_layout.setContentsMargins(10, 6, 10, 6)
        bot_layout.setSpacing(8)

        font_btn = _get_current_font(11)

        icon_undo = _load_icon("BTN_Batch_Undo.png", 22)
        self._undo_btn = QPushButton()
        self._undo_btn.setFixedSize(32, 32)
        self._undo_btn.setStyleSheet(_btn_style(theme))
        self._undo_btn.setEnabled(False)
        if icon_undo:
            self._undo_btn.setIcon(icon_undo)
            self._undo_btn.setIconSize(QSize(22, 22))
        else:
            self._undo_btn.setText("↩"); self._undo_btn.setFont(font_btn)
        self._undo_btn.clicked.connect(self._undo)
        bot_layout.addWidget(self._undo_btn)

        icon_redo = _load_icon("BTN_Batch_Redo.png", 22)
        self._redo_btn = QPushButton()
        self._redo_btn.setFixedSize(32, 32)
        self._redo_btn.setStyleSheet(_btn_style(theme))
        self._redo_btn.setEnabled(False)
        if icon_redo:
            self._redo_btn.setIcon(icon_redo)
            self._redo_btn.setIconSize(QSize(22, 22))
        else:
            self._redo_btn.setText("↪"); self._redo_btn.setFont(font_btn)
        self._redo_btn.clicked.connect(self._redo)
        bot_layout.addWidget(self._redo_btn)

        bot_layout.addStretch()

        self._apply_btn = QPushButton()
        self._apply_btn.setFont(font_btn)
        self._apply_btn.setStyleSheet(_btn_style(theme))
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._apply_text)
        bot_layout.addWidget(self._apply_btn)

        self._close_btn = QPushButton()
        self._close_btn.setFont(font_btn)
        self._close_btn.setStyleSheet(_btn_style(theme))
        self._close_btn.clicked.connect(self.reject)
        bot_layout.addWidget(self._close_btn)

        root.addWidget(bot)
        self._retranslate()

    # ── Navigation ────────────────────────────────────────────────────────────

    def _prev_image(self):
        if self._current_idx > 0:
            self._current_idx -= 1
            self._clear_blocks()
            self._update_undo_redo_buttons()
            self._load_work_image()
            self._display_image(reset_offset=True)

    def _next_image(self):
        if self._current_idx < len(self._selected_entries) - 1:
            self._current_idx += 1
            self._clear_blocks()
            self._update_undo_redo_buttons()
            self._load_work_image()
            self._display_image(reset_offset=True)

    def _clear_blocks(self):
        self._img_widget.clear_blocks()
        self._apply_btn.setEnabled(False)

    # ── Chargement et affichage ───────────────────────────────────────────────

    def _load_work_image(self):
        entry = self._selected_entries[self._current_idx]
        if not entry.get('bytes'):
            self._work_img   = None
            self._checker_bg = None
            return
        try:
            img = Image.open(io.BytesIO(entry['bytes']))
            self._work_img   = img.convert('RGBA')
            self._checker_bg = _make_checker(self._work_img.width, self._work_img.height)
        except Exception as e:
            print(f"[text_viewer_qt] load_work_image : {e}")
            self._work_img   = None
            self._checker_bg = None

    def _display_image(self, reset_offset=False):
        if self._work_img is None:
            return
        try:
            pixmap = _compose_on_checker(self._work_img, self._checker_bg)
            if reset_offset:
                self._img_widget.set_pixmap(pixmap, reset_offset=True)
            else:
                self._img_widget.update_pixmap(pixmap)
            n = len(self._selected_entries)
            self._counter_lbl.setText(f"{self._current_idx + 1} / {n}")
            self._prev_btn.setEnabled(self._current_idx > 0)
            self._next_btn.setEnabled(self._current_idx < n - 1)
            self._zoom_lbl.setText(f"{int(self._img_widget.zoom_level() * 100)}%")
        except Exception as e:
            print(f"[text_viewer_qt] display_image : {e}")

    # ── Rendu final — tous les blocs → PIL ───────────────────────────────────

    def _render_all_blocks(self) -> Image.Image:
        """Rend tous les blocs dans l'ordre sur l'image de travail → PIL RGBA."""
        img = self._work_img.copy()
        iw, ih = img.size

        for block in self._img_widget.blocks():
            if block.is_empty():
                continue
            doc = block.overlay.document().clone()
            doc.setTextWidth(-1)
            sz  = doc.size()
            tw  = max(int(sz.width()), 1)
            th  = max(int(sz.height()), 1)

            text_img = QImage(tw, th, QImage.Format.Format_ARGB32)
            text_img.fill(Qt.GlobalColor.transparent)
            painter = QPainter(text_img)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            doc.drawContents(painter, QRectF(0, 0, tw, th))
            painter.end()

            ptr = text_img.constBits()
            arr = bytes(ptr)
            text_pil = Image.frombytes('RGBA', (tw, th), arr, 'raw', 'BGRA')

            x  = block.img_pos.x()
            y  = block.img_pos.y()
            px = max(0, min(x, iw - 1))
            py = max(0, min(y, ih - 1))
            img.paste(text_pil, (px, py), text_pil)

        return img

    # ── Zoom / Plein écran ────────────────────────────────────────────────────

    def _adjust_zoom(self, delta):
        self._img_widget.adjust_zoom(delta)
        self._zoom_lbl.setText(f"{int(self._img_widget.zoom_level() * 100)}%")

    def _toggle_fullscreen(self):
        if self._is_fullscreen:
            self.showNormal()
            self._is_fullscreen = False
        else:
            self.showFullScreen()
            self._is_fullscreen = True

    # ── Callbacks blocs ───────────────────────────────────────────────────────

    def _on_place_text(self, ix: int, iy: int):
        """Nouveau bloc demandé au clic sur zone vide."""
        block = self._img_widget.add_block(
            ix, iy,
            on_content_changed=self._on_overlay_changed,
            on_block_move=lambda dx, dy: None,   # géré via signal block_moved
        )
        # Synchro des contrôles format sur déplacement du curseur (sans passer par content_changed)
        block.overlay.cursorPositionChanged.connect(
            lambda bl=block: self._on_cursor_moved_in_block(bl)
        )
        self._apply_default_format_to_block(block)
        self._update_apply_btn()

    def _on_block_moved(self, block, ix: int, iy: int):
        pass  # position déjà mise à jour dans le widget

    def _on_block_activated(self, block):
        """Un bloc vient de devenir actif → met à jour les contrôles."""
        if block is None or block.overlay is None:
            return
        if not block.overlay.isVisible():
            return
        self._sync_format_controls_from_block(block)

    def _on_overlay_changed(self):
        """Contenu d'un overlay modifié (texte seulement, pas curseur)."""
        self._update_apply_btn()

    def _on_cursor_moved_in_block(self, block):
        """Curseur déplacé dans un bloc → synchro des contrôles si bloc actif."""
        if block is self._img_widget.active_block():
            self._sync_format_controls_from_block(block)

    def _update_apply_btn(self):
        has_text = any(not b.is_empty() for b in self._img_widget.blocks())
        self._apply_btn.setEnabled(has_text)

    # ── Synchronisation contrôles ─────────────────────────────────────────────

    def _sync_format_controls_from_block(self, block: _TextBlock):
        if self._ignore_format_signals:
            return
        try:
            fmt = block.overlay.currentCharFormat()
        except Exception:
            return
        self._ignore_format_signals = True
        try:
            self._bold_btn.blockSignals(True)
            self._bold_btn.setChecked(fmt.fontWeight() >= QFont.Weight.Bold)
            self._bold_btn.blockSignals(False)
            self._italic_btn.blockSignals(True)
            self._italic_btn.setChecked(fmt.fontItalic())
            self._italic_btn.blockSignals(False)
            self._underline_btn.blockSignals(True)
            self._underline_btn.setChecked(fmt.fontUnderline())
            self._underline_btn.blockSignals(False)
            theme = get_current_theme()
            self._bold_btn.setStyleSheet(
                _btn_toggle_style(theme, self._bold_btn.isChecked(), "font-weight: bold;"))
            self._italic_btn.setStyleSheet(
                _btn_toggle_style(theme, self._italic_btn.isChecked(), "font-style: italic;"))
            self._underline_btn.setStyleSheet(
                _btn_toggle_style(theme, self._underline_btn.isChecked(), "text-decoration: underline;"))
            family = fmt.fontFamily()
            if family:
                try:
                    self._font_combo.blockSignals(True)
                    idx = self._font_combo.findText(family)
                    if idx >= 0:
                        self._font_combo.setCurrentIndex(idx)
                    self._font_combo.blockSignals(False)
                except Exception:
                    self._font_combo.blockSignals(False)
            pt = fmt.fontPointSize()
            if pt > 0:
                self._size_spin.blockSignals(True)
                self._size_spin.setValue(int(pt))
                self._size_spin.blockSignals(False)
            fg = fmt.foreground().color()
            if fg.isValid() and fg != QColor(0, 0, 0, 0):
                self._text_color = fg
                self._color_btn.setStyleSheet(
                    f"QPushButton {{ background: {fg.name()}; border: 1px solid #aaaaaa; }}"
                )
        finally:
            self._ignore_format_signals = False

    # ── Format appliqué au bloc actif ────────────────────────────────────────

    def _active_overlay(self) -> '_RichTextOverlay | None':
        ab = self._img_widget.active_block()
        return ab.overlay if ab else None

    def _apply_default_format_to_block(self, block: _TextBlock):
        family = self._font_combo.currentFont().family()
        # setCurrentCharFormat seul ne suffit pas sur document vide —
        # on applique aussi la police via le format par défaut du document
        fmt = QTextCharFormat()
        fmt.setFontFamily(family)
        fmt.setFontPointSize(self._size_spin.value())
        fmt.setForeground(self._text_color)
        ov = block.overlay
        # setDefaultFont : garantit que même un doc vide utilisera cette police
        ov.document().setDefaultFont(QFont(family, int(self._size_spin.value())))
        ov.setCurrentCharFormat(fmt)

    def _on_font_family_changed(self, font):
        if self._ignore_format_signals:
            return
        ov = self._active_overlay()
        if ov is None:
            return
        fmt = QTextCharFormat()
        fmt.setFontFamily(font.family())
        ov.apply_char_format(fmt)
        ov.setFocus()

    def _on_font_size_changed(self, value):
        if self._ignore_format_signals:
            return
        ov = self._active_overlay()
        if ov is None:
            return
        fmt = QTextCharFormat()
        fmt.setFontPointSize(value)
        ov.apply_char_format(fmt)
        ov.setFocus()

    def _on_bold_clicked(self, checked):
        if self._ignore_format_signals:
            return
        ov = self._active_overlay()
        if ov is None:
            return
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Bold if checked else QFont.Weight.Normal)
        ov.apply_char_format(fmt)
        theme = get_current_theme()
        self._bold_btn.setStyleSheet(_btn_toggle_style(theme, checked, "font-weight: bold;"))
        ov.setFocus()

    def _on_italic_clicked(self, checked):
        if self._ignore_format_signals:
            return
        ov = self._active_overlay()
        if ov is None:
            return
        fmt = QTextCharFormat()
        fmt.setFontItalic(checked)
        ov.apply_char_format(fmt)
        theme = get_current_theme()
        self._italic_btn.setStyleSheet(_btn_toggle_style(theme, checked, "font-style: italic;"))
        ov.setFocus()

    def _on_underline_clicked(self, checked):
        if self._ignore_format_signals:
            return
        ov = self._active_overlay()
        if ov is None:
            return
        fmt = QTextCharFormat()
        fmt.setFontUnderline(checked)
        ov.apply_char_format(fmt)
        theme = get_current_theme()
        self._underline_btn.setStyleSheet(_btn_toggle_style(theme, checked, "text-decoration: underline;"))
        ov.setFocus()

    def _pick_color(self):
        color = QColorDialog.getColor(
            self._text_color, self,
            _("dialogs.text_viewer.pick_color_title"),
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if not color.isValid():
            return
        self._text_color = color
        self._color_btn.setStyleSheet(
            f"QPushButton {{ background: {color.name()}; border: 1px solid #aaaaaa; }}"
        )
        ov = self._active_overlay()
        if ov is not None:
            fmt = QTextCharFormat()
            fmt.setForeground(color)
            ov.apply_char_format(fmt)
            ov.setFocus()

    # ── Application du texte ──────────────────────────────────────────────────

    def _apply_text(self):
        blocks = self._img_widget.blocks()
        if not any(not b.is_empty() for b in blocks):
            return
        entry = self._selected_entries[self._current_idx]
        if not entry.get('bytes'):
            return

        from modules.qt import state as _state_module
        state      = self._callbacks.get('state') or _state_module.state
        save_state = self._callbacks.get('save_state')
        render     = self._callbacks.get('render_mosaic')

        try:
            bytes_before    = entry['bytes']
            snapshot_before = [(b.img_pos.x(), b.img_pos.y(), b.html()) for b in blocks]

            if save_state:
                save_state()

            composed = self._render_all_blocks()
            self._commit_image(composed, entry, state)

            if save_state:
                save_state(force=True)

            idx = self._current_idx
            if idx not in self._histories:
                self._histories[idx] = []
            self._histories[idx].append((bytes_before, snapshot_before))
            if idx in self._redo_stacks:
                self._redo_stacks[idx].clear()

            self._update_undo_redo_buttons()
            self._clear_blocks()
            self._load_work_image()
            self._display_image(reset_offset=False)

            if render:
                render()

        except Exception as e:
            import traceback
            print(f"[text_viewer_qt] apply_text : {e}")
            traceback.print_exc()

    def _commit_image(self, pil_img, entry, state):
        from modules.qt.entries import save_image_to_bytes
        from modules.qt.comic_info import get_page_image_index, update_page_entries_in_xml_data

        orig_mode = entry.get('_orig_mode', 'RGBA')
        out_img   = pil_img
        if orig_mode not in ('RGBA', 'LA', 'P') and \
                entry.get('extension', '').lower() not in ('.png', '.webp'):
            bg = Image.new('RGB', out_img.size, (255, 255, 255))
            bg.paste(out_img, mask=out_img.split()[3])
            out_img = bg

        entry['img']              = out_img.copy()
        entry['bytes']            = save_image_to_bytes(entry)
        entry['img']              = None
        entry['_thumbnail']       = None
        entry['large_thumb_pil']  = None
        entry['qt_pixmap_large']  = None
        entry['qt_qimage_large']  = None

        pidx = get_page_image_index(state, entry)
        if pidx is not None:
            update_page_entries_in_xml_data(state, [(pidx, entry)])
        state.modified = True

    # ── Undo / Redo ──────────────────────────────────────────────────────────

    def _undo(self):
        # Si un overlay actif a le focus, son undo interne d'abord
        ov = self._active_overlay()
        if ov is not None and ov.isVisible() and ov.hasFocus():
            ov.document().undo()
            return

        idx = self._current_idx
        if not self._histories.get(idx):
            return
        entry = self._selected_entries[idx]
        if not entry.get('bytes'):
            return

        from modules.qt import state as _state_module
        state      = self._callbacks.get('state') or _state_module.state
        save_state = self._callbacks.get('save_state')
        render     = self._callbacks.get('render_mosaic')

        try:
            bytes_current    = entry['bytes']
            snapshot_current = [
                (b.img_pos.x(), b.img_pos.y(), b.html())
                for b in self._img_widget.blocks()
            ]

            if save_state:
                save_state()

            bytes_before, snapshot_before = self._histories[idx].pop()
            entry['bytes']           = bytes_before
            entry['img']             = None
            entry['_thumbnail']      = None
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
            self._redo_stacks[idx].append((bytes_current, snapshot_current))

            self._update_undo_redo_buttons()
            self._clear_blocks()
            self._load_work_image()
            self._display_image(reset_offset=False)

            if render:
                render()
        except Exception as e:
            print(f"[text_viewer_qt] undo : {e}")

    def _redo(self):
        ov = self._active_overlay()
        if ov is not None and ov.isVisible() and ov.hasFocus():
            ov.document().redo()
            return

        idx = self._current_idx
        if not self._redo_stacks.get(idx):
            return
        entry = self._selected_entries[idx]
        if not entry.get('bytes'):
            return

        from modules.qt import state as _state_module
        state      = self._callbacks.get('state') or _state_module.state
        save_state = self._callbacks.get('save_state')
        render     = self._callbacks.get('render_mosaic')

        try:
            bytes_current    = entry['bytes']
            snapshot_current = [
                (b.img_pos.x(), b.img_pos.y(), b.html())
                for b in self._img_widget.blocks()
            ]

            if save_state:
                save_state()

            bytes_redo, snapshot_redo = self._redo_stacks[idx].pop()
            entry['bytes']           = bytes_redo
            entry['img']             = None
            entry['_thumbnail']      = None
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

            if idx not in self._histories:
                self._histories[idx] = []
            self._histories[idx].append((bytes_current, snapshot_current))

            self._update_undo_redo_buttons()
            self._clear_blocks()
            self._load_work_image()
            self._display_image(reset_offset=False)

            if render:
                render()
        except Exception as e:
            print(f"[text_viewer_qt] redo : {e}")

    def _update_undo_redo_buttons(self):
        idx = self._current_idx
        self._undo_btn.setEnabled(bool(self._histories.get(idx)))
        self._redo_btn.setEnabled(bool(self._redo_stacks.get(idx)))

    # ── Traduction ────────────────────────────────────────────────────────────

    def _retranslate(self):
        theme    = get_current_theme()
        font     = _get_current_font(10)
        font_btn = _get_current_font(11)

        self.setWindowTitle(_("dialogs.text_viewer.title"))
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )
        self._instr_lbl.setText(_("dialogs.text_viewer.instruction"))
        self._instr_lbl.setFont(font)

        self._prev_btn.setFont(_get_current_font(12))
        self._next_btn.setFont(_get_current_font(12))
        self._counter_lbl.setFont(font)
        self._zoom_lbl.setFont(font)

        self._lbl_size.setText(_("dialogs.text_viewer.size_label"))
        self._lbl_size.setFont(font)
        self._lbl_color.setText(_("dialogs.text_viewer.color_label"))
        self._lbl_color.setFont(font)
        font_format_btn = _get_current_font(11)
        self._bold_btn.setText(_("dialogs.text_viewer.bold_btn"))
        self._bold_btn.setFont(font_format_btn)
        self._italic_btn.setText(_("dialogs.text_viewer.italic_btn"))
        self._italic_btn.setFont(font_format_btn)
        self._underline_btn.setText(_("dialogs.text_viewer.underline_btn"))
        self._underline_btn.setFont(font_format_btn)

        self._apply_btn.setText(_("dialogs.text_viewer.apply_btn"))
        self._apply_btn.setFont(font_btn)
        self._close_btn.setText(_("buttons.close"))
        self._close_btn.setFont(font_btn)

        self._zoom_minus_btn.setStyleSheet(_btn_style(theme))
        self._zoom_plus_btn.setStyleSheet(_btn_style(theme))
        self._undo_btn.setStyleSheet(_btn_style(theme))
        self._redo_btn.setStyleSheet(_btn_style(theme))
        self._apply_btn.setStyleSheet(_btn_style(theme))
        self._close_btn.setStyleSheet(_btn_style(theme))
        self._font_combo.setStyleSheet(_combo_style(theme))
        self._size_spin.setStyleSheet(_spinbox_style(theme))
        self._bold_btn.setStyleSheet(
            _btn_toggle_style(theme, self._bold_btn.isChecked(), "font-weight: bold;"))
        self._italic_btn.setStyleSheet(
            _btn_toggle_style(theme, self._italic_btn.isChecked(), "font-style: italic;"))
        self._underline_btn.setStyleSheet(
            _btn_toggle_style(theme, self._underline_btn.isChecked(), "text-decoration: underline;"))

    # ── Clavier ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key  = event.key()
        ov   = self._active_overlay()
        if ov is not None and ov.isVisible() and ov.hasFocus():
            super().keyPressEvent(event)
            return

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

def show_text_viewer(parent=None, callbacks=None):
    callbacks = callbacks or {}
    from modules.qt import state as _state_module
    state = callbacks.get('state') or _state_module.state

    if not state.images_data:
        return

    image_entries = [
        (i, e) for i, e in enumerate(state.images_data)
        if e.get('is_image') and not e.get('is_corrupted')
    ]
    if not image_entries:
        return

    start_index = 0
    if state.selected_indices:
        selected_real_indices = {
            i for i in state.selected_indices
            if i < len(state.images_data)
            and state.images_data[i].get('is_image')
            and not state.images_data[i].get('is_corrupted')
        }
        if selected_real_indices:
            first_sel = min(selected_real_indices)
            start_index = next(
                (j for j, (i, _) in enumerate(image_entries) if i == first_sel), 0
            )

    for _, entry in image_entries:
        if entry.get('bytes') and '_orig_mode' not in entry:
            try:
                img = Image.open(io.BytesIO(entry['bytes']))
                entry['_orig_mode'] = img.mode
            except Exception:
                entry['_orig_mode'] = 'RGB'

    selected_entries = [e for _, e in image_entries]
    dlg = TextViewerDialog(parent, selected_entries, start_index, callbacks=callbacks)
    dlg.exec()
