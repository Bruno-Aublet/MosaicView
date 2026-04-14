"""
modules/qt/merge_dialog_qt.py — Dialogue de fusion/jointure d'images (version PySide6)

Reproduit à l'identique le comportement de modules/merge_dialog.py (tkinter).
"""

import io

from PIL import Image
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QButtonGroup, QRadioButton, QScrollArea, QWidget, QSizePolicy,
)
from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtGui import QPixmap, QImage, QPainter, QPen, QColor

from modules.qt import state as _state_module
from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.undo_redo import save_state_data as _save_state_data
from modules.qt.entries import ensure_image_loaded, create_entry
from modules.qt.dialogs_qt import MsgDialog
from modules.qt.image_ops import merge_images_2d


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
# Dialogue de choix d'ajustement de taille
# ─────────────────────────────────────────────────────────────────────────────

class SizeAdjustmentDialog(QDialog):
    """Dialogue pour choisir le mode d'ajustement des tailles d'images."""

    def __init__(self, parent, dimension_type: str, dimensions_list: list):
        super().__init__(parent)
        self._dimension_type = dimension_type
        self._dimensions_list = dimensions_list
        self._result_mode = None
        self.setModal(True)
        self.setFixedSize(520, 400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        self._warn_lbl = QLabel()
        self._warn_lbl.setWordWrap(True)
        self._warn_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._warn_lbl)

        self._dim_lbl = QLabel()
        self._dim_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._dim_lbl)

        layout.addSpacing(8)

        self._btn_group = QButtonGroup(self)
        self._rb_keep   = QRadioButton()
        self._rb_enlarge = QRadioButton()
        self._rb_reduce  = QRadioButton()
        self._rb_enlarge.setChecked(True)

        self._desc_keep    = QLabel()
        self._desc_enlarge = QLabel()
        self._desc_reduce  = QLabel()
        for desc in (self._desc_keep, self._desc_enlarge, self._desc_reduce):
            desc.setWordWrap(False)
            desc.setAlignment(Qt.AlignCenter)
            desc.setContentsMargins(0, 0, 0, 4)

        for rb in (self._rb_keep, self._rb_enlarge, self._rb_reduce):
            self._btn_group.addButton(rb)

        for rb, desc in (
            (self._rb_keep,    self._desc_keep),
            (self._rb_enlarge, self._desc_enlarge),
            (self._rb_reduce,  self._desc_reduce),
        ):
            rb_row = QHBoxLayout()
            rb_row.addStretch()
            rb_row.addWidget(rb)
            rb_row.addStretch()
            layout.addLayout(rb_row)
            desc_row = QHBoxLayout()
            desc_row.addStretch()
            desc_row.addWidget(desc)
            desc_row.addStretch()
            layout.addLayout(desc_row)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok_btn = QPushButton()
        self._ok_btn.setFixedWidth(130)
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(self._ok_btn)
        self._cancel_btn = QPushButton()
        self._cancel_btn.setFixedWidth(130)
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._ok_btn.setFocus()
        self._center_parent = parent

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _retranslate(self):
        theme = get_current_theme()
        font_normal = _get_current_font(11)
        font_desc   = _get_current_font(9)
        font_desc.setItalic(True)

        self.setStyleSheet(
            f"QDialog     {{ background: {theme['bg']}; color: {theme['text']}; }}"
            f"QLabel       {{ color: {theme['text']}; }}"
            f"QRadioButton {{ background: {theme['bg']}; color: {theme['text']}; }}"
            f"QPushButton  {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }}"
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )

        self.setWindowTitle(_wt("dialogs.join.size_adjustment_title"))

        self._warn_lbl.setText(_("dialogs.join.size_adjustment_warning"))
        warn_font = _get_current_font(11, bold=True)
        self._warn_lbl.setFont(warn_font)
        self._warn_lbl.setStyleSheet("color: red;")

        dims = sorted(set(self._dimensions_list))
        dims_str = ", ".join(f"{d}px" for d in dims)
        if self._dimension_type == 'height':
            self._dim_lbl.setText(_("dialogs.join.heights_label") + " : " + dims_str)
        else:
            self._dim_lbl.setText(_("dialogs.join.widths_label") + " : " + dims_str)
        self._dim_lbl.setFont(_get_current_font(10))

        self._rb_keep.setText(_("dialogs.join.keep_original"))
        self._rb_keep.setFont(font_normal)
        self._desc_keep.setText(_("dialogs.join.keep_original_desc"))
        self._desc_keep.setFont(font_desc)

        self._rb_enlarge.setText(_("dialogs.join.enlarge_small"))
        self._rb_enlarge.setFont(font_normal)
        self._desc_enlarge.setText(_("dialogs.join.enlarge_small_desc"))
        self._desc_enlarge.setFont(font_desc)

        self._rb_reduce.setText(_("dialogs.join.reduce_large"))
        self._rb_reduce.setFont(font_normal)
        self._desc_reduce.setText(_("dialogs.join.reduce_large_desc"))
        self._desc_reduce.setFont(font_desc)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font_normal)
        self._cancel_btn.setText(_("dialogs.join.back_to_arrangement"))
        self._cancel_btn.setFont(font_normal)

    def _on_ok(self):
        if self._rb_keep.isChecked():
            self._result_mode = 'keep_original'
        elif self._rb_enlarge.isChecked():
            self._result_mode = 'enlarge_small'
        else:
            self._result_mode = 'reduce_large'
        self.accept()

    @property
    def result_mode(self):
        return self._result_mode


# ─────────────────────────────────────────────────────────────────────────────
# Canvas de disposition (drag & drop 2D avec snap magnétique)
# ─────────────────────────────────────────────────────────────────────────────

THUMB_W = 80
THUMB_H = 106
SNAP_DIST = 15


def _pil_to_qpixmap(img: Image.Image) -> QPixmap:
    """Convertit une image PIL en QPixmap."""
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    data = img.tobytes("raw", img.mode)
    qfmt = QImage.Format.Format_RGB888 if img.mode == "RGB" else QImage.Format.Format_RGBA8888
    qimg = QImage(data, img.width, img.height, img.width * len(img.mode), qfmt)
    return QPixmap.fromImage(qimg)


class MiniMosaicCanvas(QWidget):
    """Widget canvas pour la disposition libre 2D des miniatures avant fusion."""

    def __init__(self, parent, entries):
        super().__init__(parent)
        self.setMinimumSize(540, 400)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

        self._entries = entries
        self._thumbs = []      # list de {"x", "y", "w", "h", "pixmap", "entry"}
        self._snap_lines = []  # list de {"x1","y1","x2","y2","dashed"}

        self._drag_idx = None
        self._drag_offset = QPoint()

        self._load_thumbs()
        self._init_positions()

        # Callback appelé quand les positions changent (pour maj prévisualisation)
        self.positions_changed = None

    def _load_thumbs(self):
        self._thumbs = []
        for entry in self._entries:
            img = ensure_image_loaded(entry)
            if img is None:
                continue
            t = img.copy()
            t.thumbnail((THUMB_W, THUMB_H))
            self._thumbs.append({
                "x": 0, "y": 0,
                "w": t.width, "h": t.height,
                "pixmap": _pil_to_qpixmap(t),
                "entry": entry,
            })

    def _init_positions(self):
        cols = 3
        spacing_x = THUMB_W + 40
        spacing_y = THUMB_H + 40
        start_x = 60
        start_y = 60
        for i, th in enumerate(self._thumbs):
            col = i % cols
            row = i // cols
            th["x"] = start_x + col * spacing_x + th["w"] // 2
            th["y"] = start_y + row * spacing_y + th["h"] // 2
        self._update_min_size()

    def _update_min_size(self):
        if not self._thumbs:
            return
        max_x = max(th["x"] + th["w"] // 2 + 20 for th in self._thumbs)
        max_y = max(th["y"] + th["h"] // 2 + 30 for th in self._thumbs)
        self.setMinimumSize(max(540, max_x), max(400, max_y))

    def get_positions_data(self):
        """Retourne la liste des dicts {"entry", "x", "y"} pour merge_images_2d."""
        return [{"entry": th["entry"], "x": th["x"], "y": th["y"]} for th in self._thumbs]

    # ── Dessin ───────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        theme = get_current_theme()
        painter.fillRect(self.rect(), QColor(theme["canvas_bg"]))

        # Snap lines
        for line in self._snap_lines:
            pen = QPen(QColor("red"), 2)
            if line.get("dashed"):
                pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.drawLine(line["x1"], line["y1"], line["x2"], line["y2"])

        # Thumbnails
        font = _get_current_font(9)
        painter.setFont(font)
        for i, th in enumerate(self._thumbs):
            cx, cy = int(th["x"]), int(th["y"])
            hw, hh = th["w"] // 2, th["h"] // 2
            # Rectangle fond
            painter.setPen(QPen(QColor("#000000"), 2))
            painter.fillRect(cx - hw - 3, cy - hh - 3, th["w"] + 6, th["h"] + 6, QColor("#d0d0d0"))
            painter.drawRect(cx - hw - 3, cy - hh - 3, th["w"] + 6, th["h"] + 6)
            # Image
            painter.drawPixmap(cx - hw, cy - hh, th["pixmap"])
            # Numéro
            painter.setPen(QPen(QColor(theme["text"])))
            painter.drawText(
                QRect(cx - hw, cy + hh + 2, th["w"], 16),
                Qt.AlignCenter,
                str(i + 1),
            )

    # ── Interactions souris ───────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        for i, th in enumerate(self._thumbs):
            cx, cy = int(th["x"]), int(th["y"])
            hw, hh = th["w"] // 2, th["h"] // 2
            if abs(pos.x() - cx) <= hw + 3 and abs(pos.y() - cy) <= hh + 3:
                self._drag_idx = i
                self._drag_offset = QPoint(pos.x() - cx, pos.y() - cy)
                return

    def mouseMoveEvent(self, event):
        if self._drag_idx is None:
            return
        pos = event.position().toPoint()
        idx = self._drag_idx
        th = self._thumbs[idx]

        new_x = pos.x() - self._drag_offset.x()
        new_y = pos.y() - self._drag_offset.y()
        drag_hw = th["w"] // 2
        drag_hh = th["h"] // 2

        dragged_left   = new_x - drag_hw
        dragged_right  = new_x + drag_hw
        dragged_top    = new_y - drag_hh
        dragged_bottom = new_y + drag_hh

        snap_candidates = []
        for i, other in enumerate(self._thumbs):
            if i == idx:
                continue
            ox, oy   = other["x"], other["y"]
            ohw, ohh = other["w"] // 2, other["h"] // 2
            o_left   = ox - ohw
            o_right  = ox + ohw
            o_top    = oy - ohh
            o_bottom = oy + ohh

            if abs(dragged_right - o_left) < SNAP_DIST:
                snap_candidates.append({
                    "distance": abs(dragged_right - o_left),
                    "snap_x": o_left - drag_hw, "snap_y": oy,
                    "type": "h_left",
                    "o": other,
                })
            if abs(dragged_left - o_right) < SNAP_DIST:
                snap_candidates.append({
                    "distance": abs(dragged_left - o_right),
                    "snap_x": o_right + drag_hw, "snap_y": oy,
                    "type": "h_right",
                    "o": other,
                })
            if abs(dragged_bottom - o_top) < SNAP_DIST:
                snap_candidates.append({
                    "distance": abs(dragged_bottom - o_top),
                    "snap_x": ox, "snap_y": o_top - drag_hh,
                    "type": "v_top",
                    "o": other,
                })
            if abs(dragged_top - o_bottom) < SNAP_DIST:
                snap_candidates.append({
                    "distance": abs(dragged_top - o_bottom),
                    "snap_x": ox, "snap_y": o_bottom + drag_hh,
                    "type": "v_bottom",
                    "o": other,
                })

        def would_overlap(sx, sy):
            for i, other in enumerate(self._thumbs):
                if i == idx:
                    continue
                if (abs(sx - other["x"]) < drag_hw + other["w"] // 2 and
                        abs(sy - other["y"]) < drag_hh + other["h"] // 2):
                    return True
            return False

        self._snap_lines = []

        if snap_candidates:
            h_snaps = [s for s in snap_candidates if s["type"] in ("h_left", "h_right")]
            v_snaps = [s for s in snap_candidates if s["type"] in ("v_top", "v_bottom")]
            best_h = min(h_snaps, key=lambda s: s["distance"]) if h_snaps else None
            best_v = min(v_snaps, key=lambda s: abs(s["o"]["x"] - new_x)) if v_snaps else None

            best = (best_h if (best_h and best_v and best_h["distance"] <= best_v["distance"])
                    else best_h or best_v)

            if best and not would_overlap(best["snap_x"], best["snap_y"]):
                new_x = best["snap_x"]
                new_y = best["snap_y"]

            # Guides visuels
            if best:
                o = best["o"]
                ox, oy   = o["x"], o["y"]
                ohw, ohh = o["w"] // 2, o["h"] // 2
                o_left   = ox - ohw
                o_right  = ox + ohw
                o_top    = oy - ohh
                o_bottom = oy + ohh
                t = best["type"]
                if t == "h_left":
                    self._snap_lines = [
                        {"x1": o_left, "y1": o_top - 10,    "x2": o_left, "y2": o_bottom + 10},
                        {"x1": o_left - THUMB_W - 10, "y1": oy, "x2": o_left + 10, "y2": oy, "dashed": True},
                    ]
                elif t == "h_right":
                    self._snap_lines = [
                        {"x1": o_right, "y1": o_top - 10,    "x2": o_right, "y2": o_bottom + 10},
                        {"x1": o_right - 10, "y1": oy, "x2": o_right + THUMB_W + 10, "y2": oy, "dashed": True},
                    ]
                elif t == "v_top":
                    self._snap_lines = [
                        {"x1": o_left - 10,  "y1": o_top, "x2": o_right + 10,  "y2": o_top},
                        {"x1": ox, "y1": o_top - THUMB_H - 10, "x2": ox, "y2": o_top + 10, "dashed": True},
                    ]
                elif t == "v_bottom":
                    self._snap_lines = [
                        {"x1": o_left - 10,  "y1": o_bottom, "x2": o_right + 10,  "y2": o_bottom},
                        {"x1": ox, "y1": o_bottom - 10, "x2": ox, "y2": o_bottom + THUMB_H + 10, "dashed": True},
                    ]

        th["x"] = new_x
        th["y"] = new_y
        self._update_min_size()
        self.update()

        if self.positions_changed:
            self.positions_changed()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_idx = None
            self._snap_lines = []
            self.update()


# ─────────────────────────────────────────────────────────────────────────────
# Dialogue de oui/non/annuler (supprimer fichiers sources ?)
# ─────────────────────────────────────────────────────────────────────────────

class YesNoCancelDialog(QDialog):
    """Dialogue Oui / Non / Annuler."""

    # Résultats possibles
    YES    = 1
    NO     = 2
    CANCEL = 0

    def __init__(self, parent, title_key: str, message_key: str):
        super().__init__(parent)
        self._title_key   = title_key
        self._message_key = message_key
        self._result_val  = self.CANCEL
        self.setModal(True)
        self.setFixedSize(420, 160)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(12)

        self._lbl = QLabel()
        self._lbl.setWordWrap(True)
        self._lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_yes    = QPushButton()
        self._btn_no     = QPushButton()
        self._btn_cancel = QPushButton()
        self._btn_yes.setDefault(True)
        self._btn_yes.clicked.connect(self._on_yes)
        self._btn_no.clicked.connect(self._on_no)
        self._btn_cancel.clicked.connect(self._on_cancel)
        for btn in (self._btn_yes, self._btn_no, self._btn_cancel):
            btn.setFixedWidth(100)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._btn_yes.setFocus()
        self._center_parent = parent

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(11)
        self.setStyleSheet(
            f"QDialog    {{ background: {theme['bg']}; color: {theme['text']}; }}"
            f"QLabel      {{ color: {theme['text']}; }}"
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }}"
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self.setWindowTitle(_wt(self._title_key))
        self._lbl.setText(_(self._message_key))
        self._lbl.setFont(font)
        self._btn_yes.setText(_("buttons.yes"))
        self._btn_yes.setFont(font)
        self._btn_no.setText(_("buttons.no"))
        self._btn_no.setFont(font)
        self._btn_cancel.setText(_("buttons.cancel"))
        self._btn_cancel.setFont(font)

    def _on_yes(self):
        self._result_val = self.YES
        self.accept()

    def _on_no(self):
        self._result_val = self.NO
        self.accept()

    def _on_cancel(self):
        self._result_val = self.CANCEL
        self.reject()

    @property
    def result_value(self):
        return self._result_val


# ─────────────────────────────────────────────────────────────────────────────
# Dialogue principal de fusion
# ─────────────────────────────────────────────────────────────────────────────

def _get_next_collage_number(state=None):
    state = state or _state_module.state
    prefixes = ["Collage", "Merged", "Fusionado", "Zusammengeführt"]
    max_num = 0
    for entry in state.images_data:
        name = entry.get("orig_name", "")
        for prefix in prefixes:
            if name.startswith(f"{prefix}_"):
                try:
                    num = int(name.split("_")[1].split(".")[0])
                    max_num = max(max_num, num)
                except (IndexError, ValueError):
                    continue
    return max_num + 1


class MergeDialog(QDialog):
    """Fenêtre de disposition 2D + prévisualisation avant fusion d'images."""

    def __init__(self, parent, callbacks):
        super().__init__(parent)
        self._callbacks = callbacks
        self._state = callbacks.get('state') or _state_module.state
        self.setModal(True)
        self.setMinimumSize(600, 700)
        self.resize(620, 740)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # Instructions
        self._instr_lbl = QLabel()
        self._instr_lbl.setWordWrap(True)
        self._instr_lbl.setStyleSheet("color: red;")
        layout.addWidget(self._instr_lbl)

        # Canvas dans un QScrollArea
        selected_entries = [
            self._state.images_data[idx]
            for idx in sorted(self._state.selected_indices)
            if idx < len(self._state.images_data) and self._state.images_data[idx]["is_image"]
        ]
        self._canvas = MiniMosaicCanvas(self, selected_entries)
        self._canvas.positions_changed = self._update_preview

        scroll = QScrollArea()
        scroll.setWidget(self._canvas)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, stretch=3)

        # Séparateur + prévisualisation
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #888888;")
        layout.addWidget(sep)

        self._preview_title = QLabel()
        self._preview_title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._preview_title)

        self._preview_lbl = QLabel()
        self._preview_lbl.setAlignment(Qt.AlignCenter)
        self._preview_lbl.setMinimumHeight(120)
        layout.addWidget(self._preview_lbl)

        # Boutons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._join_btn   = QPushButton()
        self._join_btn.setFixedWidth(130)
        self._join_btn.setDefault(True)
        self._join_btn.clicked.connect(self._on_join)
        btn_row.addWidget(self._join_btn)
        self._cancel_btn = QPushButton()
        self._cancel_btn.setFixedWidth(130)
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._join_btn.setFocus()
        self._update_preview()
        self._center_parent = parent

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(11)
        font_bold = _get_current_font(11, bold=True)

        self.setStyleSheet(
            f"QDialog    {{ background: {theme['bg']}; color: {theme['text']}; }}"
            f"QLabel      {{ color: {theme['text']}; background: {theme['bg']}; }}"
            f"QScrollArea {{ background: {theme['canvas_bg']}; }}"
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }}"
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )

        self.setWindowTitle(_wt("dialogs.join.window_title"))

        self._instr_lbl.setText(_("dialogs.join.instructions"))
        self._instr_lbl.setFont(font_bold)

        self._preview_title.setText(_("dialogs.adjustments.preview_section"))
        self._preview_title.setFont(font_bold)

        self._join_btn.setText(_("buttons.join_button"))
        self._join_btn.setFont(font)
        self._cancel_btn.setText(_("buttons.cancel"))
        self._cancel_btn.setFont(font)

    def _update_preview(self):
        """Compose une prévisualisation basse résolution depuis les positions courantes."""
        positions = self._canvas.get_positions_data()
        if not positions:
            return

        thumbs = []
        for pos in positions:
            img = ensure_image_loaded(pos["entry"])
            if img is None:
                return
            t = img.copy()
            t.thumbnail((THUMB_W, THUMB_H))
            thumbs.append({"img": t, "x": pos["x"], "y": pos["y"],
                           "w": t.width, "h": t.height})

        min_x = int(min(d["x"] - d["w"] // 2 for d in thumbs))
        min_y = int(min(d["y"] - d["h"] // 2 for d in thumbs))
        max_x = int(max(d["x"] + d["w"] // 2 for d in thumbs))
        max_y = int(max(d["y"] + d["h"] // 2 for d in thumbs))
        total_w = max_x - min_x
        total_h = max_y - min_y
        if total_w <= 0 or total_h <= 0:
            return

        preview = Image.new("RGB", (total_w, total_h), (200, 200, 200))
        for d in thumbs:
            px = int(d["x"]) - d["w"] // 2 - min_x
            py = int(d["y"]) - d["h"] // 2 - min_y
            preview.paste(d["img"], (px, py))

        preview.thumbnail((300, 200))
        pixmap = _pil_to_qpixmap(preview)
        self._preview_lbl.setPixmap(pixmap)

    def _ask_size_adjustment(self, dimension_type, dimensions_list):
        """Ouvre le dialogue de choix d'ajustement ; retourne le mode ou None si annulé."""
        dlg = SizeAdjustmentDialog(self, dimension_type, dimensions_list)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dlg.result_mode

    def _on_join(self):
        positions = self._canvas.get_positions_data()
        state = self._state

        # Fusion PIL
        merged_img = merge_images_2d(
            positions,
            ask_adjustment_func=self._ask_size_adjustment,
        )
        if merged_img is None:
            # Annulé par l'utilisateur dans le dialogue d'ajustement → reste ouvert
            return

        # Détermine le format de sortie
        source_extensions = {pos["entry"].get("extension", "").lower() for pos in positions}
        ext_to_format = {
            ".jpg": ("JPEG", ".jpg"), ".jpeg": ("JPEG", ".jpg"),
            ".png": ("PNG", ".png"),
            ".webp": ("WEBP", ".webp"),
            ".bmp": ("BMP", ".bmp"),
            ".tiff": ("TIFF", ".tiff"), ".tif": ("TIFF", ".tiff"),
        }
        if len(source_extensions) == 1:
            save_format, out_ext = ext_to_format.get(source_extensions.pop(), ("PNG", ".png"))
        else:
            save_format, out_ext = "PNG", ".png"

        collage_num  = _get_next_collage_number(self._state)
        collage_name = f"{_('dialogs.join.merged_filename_prefix')}_{collage_num:02d}{out_ext}"

        # DPI source
        source_dpi = None
        for pos in positions:
            img = pos["entry"].get("img")
            if img:
                dpi = img.info.get("dpi")
                if dpi:
                    source_dpi = dpi
                    break

        img_bytes = io.BytesIO()
        save_kwargs = {}
        if save_format in ("JPEG", "WEBP"):
            save_kwargs["quality"] = 95
        if source_dpi:
            save_kwargs["dpi"] = source_dpi
        merged_img.save(img_bytes, format=save_format, **save_kwargs)

        image_exts = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff", ".tif"}
        new_entry = create_entry(collage_name, img_bytes.getvalue(), image_exts)
        new_entry["img"] = merged_img

        # Ferme le dialogue de disposition
        self.accept()

        # Demande si on supprime les fichiers sources
        ync = YesNoCancelDialog(
            self.parent(),
            "messages.questions.delete_source_files.title",
            "messages.questions.delete_source_files.message",
        )
        ync.exec()

        if ync.result_value == YesNoCancelDialog.CANCEL:
            # Rien à défaire — on n'a pas encore modifié state
            self._callbacks["render_mosaic"]()
            self._callbacks["update_button_text"]()
            return

        # Sauvegarde l'état AVANT modification (point de retour pour undo)
        _save_state_data(state, force=True)

        # Insère en tête de mosaïque
        state.images_data.insert(0, new_entry)
        state.modified = True
        state.needs_renumbering = True

        if ync.result_value == YesNoCancelDialog.YES:
            indices_to_remove = sorted(state.selected_indices, reverse=True)
            for idx in indices_to_remove:
                adjusted = idx + 1
                if adjusted < len(state.images_data):
                    state.images_data.pop(adjusted)

        from modules.qt.comic_info import sync_pages_in_xml_data
        sync_pages_in_xml_data(state)

        # Déselectionne tout et sélectionne la nouvelle image
        self._callbacks["clear_selection"]()
        state.selected_indices.add(0)

        # Sauvegarde l'état APRÈS modification (point de retour pour redo)
        _save_state_data(state, force=True)

        self._callbacks["render_mosaic"]()
        self._callbacks["update_button_text"]()


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée public
# ─────────────────────────────────────────────────────────────────────────────

def open_merge_window(parent, callbacks):
    """Ouvre la fenêtre de jointure d'images.

    callbacks: dict avec 'save_state', 'render_mosaic', 'update_button_text', 'clear_selection'
    """
    state = callbacks.get('state') or _state_module.state

    if not state.selected_indices or len(state.selected_indices) < 2:
        dlg = MsgDialog(
            parent,
            "messages.warnings.insufficient_selection_join.title",
            "messages.warnings.insufficient_selection_join.message",
        )
        dlg.exec()
        return

    selected_entries = [
        state.images_data[idx]
        for idx in sorted(state.selected_indices)
        if idx < len(state.images_data)
    ]
    if not all(e["is_image"] for e in selected_entries):
        dlg = MsgDialog(
            parent,
            "messages.warnings.invalid_selection_join.title",
            "messages.warnings.invalid_selection_join.message",
        )
        dlg.exec()
        return

    dlg = MergeDialog(parent, callbacks)
    dlg.exec()
