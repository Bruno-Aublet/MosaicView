"""
modules/qt/merge_dialog_qt.py — Dialogue de fusion/jointure d'images.
"""

import io
import threading

from PIL import Image
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QButtonGroup, QRadioButton, QScrollArea, QWidget, QSizePolicy,
)
from PySide6.QtCore import Qt, QPoint, QRect, Signal, QTimer, QThread
from PySide6.QtGui import QPixmap, QImage, QPainter, QPen, QColor, QCursor

from modules.qt import state as _state_module
from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.undo_redo import save_state_data as _save_state_data
from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import ensure_image_loaded, create_entry, image_dpi, page_icc_profile
from modules.qt.dialogs_qt import MsgDialog
from modules.qt.image_ops import (
    merge_images_2d, merge_animated_2d, animations_joinable, is_animated_entry, _has_alpha,
)
from modules.qt.canvas_overlay_qt import show_canvas_text as _show_canvas_text, hide_canvas_text as _hide_canvas_text
from modules.qt.utils import dispose_qthread, register_cancel_on_close, unregister_cancel_on_close


_active_workers: list = []  # anti-GC : maintient les workers en vie jusqu'à leur destruction Qt


class _AnimatedJoinWorker(QThread):
    """Assemble frame par frame des images animées (merge_animated_2d), hors
    du thread UI : l'encodage de dizaines de frames en pleine résolution peut
    prendre plusieurs secondes."""
    progress  = Signal(int)
    done      = Signal(object)   # bytes de l'image animée, ou None en cas d'erreur
    cancelled = Signal()

    def __init__(self, sources, durations, adjustment_mode, save_format):
        super().__init__()
        self.setObjectName("AnimatedJoinWorker")
        self._sources = sources
        self._durations = durations
        self._adjustment_mode = adjustment_mode
        self._save_format = save_format
        self._cancelled = threading.Event()

    def run(self):
        try:
            data = merge_animated_2d(
                self._sources, self._durations, self._adjustment_mode, self._save_format,
                progress_cb=lambda d, t: self.progress.emit(int(d / t * 100)),
                is_cancelled=self._cancelled.is_set,
            )
        except Exception:
            data = None
        if self._cancelled.is_set():
            self.cancelled.emit()
            return
        self.done.emit(data)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers langue
# ─────────────────────────────────────────────────────────────────────────────

def _connect_lang(dialog, handler):
    from modules.qt.language_signal import language_signal
    dialog._lang_handler = handler
    language_signal.changed.connect(dialog._lang_handler)
    dialog.finished.connect(lambda: _disconnect_lang(dialog))
    # Fenêtre détruite avec sa fenêtre parente (SizeAdjustmentDialog quand
    # MergeDialog se ferme) : ni finished ni _finish, la traduction resterait
    # branchée sur un objet détruit
    dialog.destroyed.connect(lambda: _disconnect_lang(dialog))


def _disconnect_lang(dialog):
    from modules.qt.language_signal import language_signal
    handler = getattr(dialog, "_lang_handler", None)
    if handler is None:
        return
    # Une seule déconnexion : PySide6 avertit en console d'une déconnexion
    # d'un slot qui ne l'est plus
    dialog._lang_handler = None
    try:
        language_signal.changed.disconnect(handler)
    except RuntimeError:
        pass  # sans conséquence : déjà déconnecté


def _decoded_source(entry, cache):
    """Image décodée d'une page source, gardée par la fenêtre de jointure
    (`cache`, vidé à sa fermeture) et non sur la page : laissée dans
    entry["img"], elle resterait en mémoire jusqu'à la fermeture du fichier,
    et au-delà pour une page source supprimée par la jointure. Une image déjà
    décodée sur la page (visionneuse ouverte) est reprise telle quelle."""
    img = entry.get("img")
    if img is not None:
        return img
    key = id(entry)
    if key not in cache:
        cache[key] = ensure_image_loaded(entry)
        entry["img"] = None
    return cache[key]


# ─────────────────────────────────────────────────────────────────────────────
# Dialogue de choix d'ajustement de taille
# ─────────────────────────────────────────────────────────────────────────────

class SizeAdjustmentDialog(QDialog):
    """Dialogue pour choisir le mode d'ajustement des tailles d'images."""

    result_signal = Signal(object)   # mode (str) ou None si annulé

    def __init__(self, parent, dimension_type: str, dimensions_list: list):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self._dimension_type = dimension_type
        self._dimensions_list = dimensions_list
        self._result_mode = None
        self._emitted = False
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMinimumSize(520, 400)

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
        self._cancel_btn.clicked.connect(lambda: self._finish(None))
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
            mode = 'keep_original'
        elif self._rb_enlarge.isChecked():
            mode = 'enlarge_small'
        else:
            mode = 'reduce_large'
        self._finish(mode)

    def _finish(self, mode):
        if self._emitted:
            return
        self._emitted = True
        self._result_mode = mode
        _disconnect_lang(self)
        self.result_signal.emit(mode)
        self.hide()
        self.deleteLater()

    def reject(self):
        # Échap : même chemin que la croix (réponse « annulé », destruction).
        # QDialog.reject masquerait la fenêtre sans passer par closeEvent.
        self.close()

    def closeEvent(self, event):
        if not self._emitted:
            self._emitted = True
            _disconnect_lang(self)
            self.result_signal.emit(None)
        event.accept()

    def ask_async(self, on_result):
        """Affiche (NON modal) et appelle on_result(mode|None) à la réponse."""
        from modules.qt.dialogs_qt import position_dialog_on_parent
        self.result_signal.connect(on_result)
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

    @property
    def result_mode(self):
        return self._result_mode


# ─────────────────────────────────────────────────────────────────────────────
# Canvas de disposition (drag & drop 2D avec snap magnétique)
# ─────────────────────────────────────────────────────────────────────────────

THUMB_W = 80
THUMB_H = 106
SNAP_DIST = 15

AUTOSCROLL_MARGIN = 30   # px, zone proche du bord du viewport qui déclenche l'autoscroll
AUTOSCROLL_STEP = 12     # px par tick de timer
AUTOSCROLL_INTERVAL = 16  # ms entre chaque tick (~60 fps)

PREVIEW_SRC_MAX = 400   # px, résolution des images réduites utilisées pour l'aperçu du bas


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

    def __init__(self, parent, entries, decoded):
        super().__init__(parent)
        self.setMinimumSize(540, 400)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

        self._entries = entries
        self._decoded = decoded   # images décodées de la fenêtre (_decoded_source)
        self._thumbs = []      # list de {"x", "y", "w", "h", "pixmap", "entry"}
        self._snap_lines = []  # list de {"x1","y1","x2","y2","dashed"}

        self._drag_idx = None
        self._drag_offset = QPoint()
        self._last_drag_pos = QPoint()

        self._load_thumbs()
        self._init_positions()

        # Callback appelé quand les positions changent (pour maj prévisualisation)
        self.positions_changed = None

        # Autoscroll pendant le drag : renseigné par MergeDialog après construction
        self._scroll_area = None
        self._autoscroll_dx = 0
        self._autoscroll_dy = 0
        self._autoscroll_timer = QTimer(self)
        self._autoscroll_timer.setInterval(AUTOSCROLL_INTERVAL)
        self._autoscroll_timer.timeout.connect(self._on_autoscroll_tick)

    def _load_thumbs(self):
        self._thumbs = []
        for entry in self._entries:
            img = _decoded_source(entry, self._decoded)
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
        cols = 4
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
        self._last_drag_pos = pos
        self._apply_drag_position(pos)
        self._update_autoscroll(pos)

    def _apply_drag_position(self, pos):
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
            self._stop_autoscroll()
            self.update()

    # ── Autoscroll pendant le drag ────────────────────────────────────────────

    def _update_autoscroll(self, pos):
        """Détermine la vitesse d'autoscroll selon la proximité de pos aux bords
        du viewport visible du QScrollArea, et démarre/arrête le timer."""
        if self._scroll_area is None:
            return
        viewport = self._scroll_area.viewport()
        # pos est en coordonnées du canvas ; on le convertit en coordonnées viewport
        # en passant par les coordonnées globales (self.mapTo exigerait que self
        # soit un ancêtre de viewport, alors que c'est l'inverse ici).
        pos_in_viewport = viewport.mapFromGlobal(self.mapToGlobal(pos))
        rect = viewport.rect()

        dx = 0
        dy = 0
        if pos_in_viewport.x() < AUTOSCROLL_MARGIN:
            dx = -AUTOSCROLL_STEP
        elif pos_in_viewport.x() > rect.width() - AUTOSCROLL_MARGIN:
            dx = AUTOSCROLL_STEP
        if pos_in_viewport.y() < AUTOSCROLL_MARGIN:
            dy = -AUTOSCROLL_STEP
        elif pos_in_viewport.y() > rect.height() - AUTOSCROLL_MARGIN:
            dy = AUTOSCROLL_STEP

        self._autoscroll_dx = dx
        self._autoscroll_dy = dy

        if dx or dy:
            if not self._autoscroll_timer.isActive():
                self._autoscroll_timer.start()
        else:
            self._stop_autoscroll()

    def _stop_autoscroll(self):
        self._autoscroll_timer.stop()
        self._autoscroll_dx = 0
        self._autoscroll_dy = 0

    def _on_autoscroll_tick(self):
        if self._scroll_area is None or self._drag_idx is None:
            self._stop_autoscroll()
            return
        hbar = self._scroll_area.horizontalScrollBar()
        vbar = self._scroll_area.verticalScrollBar()
        if self._autoscroll_dx:
            hbar.setValue(hbar.value() + self._autoscroll_dx)
        if self._autoscroll_dy:
            vbar.setValue(vbar.value() + self._autoscroll_dy)

        # Le scroll a changé l'origine du canvas sous le curseur (resté immobile
        # à l'écran) : on relit sa vraie position système et on la remappe dans
        # le repère du canvas, pour rejouer EXACTEMENT le même calcul (snap
        # compris) que mouseMoveEvent, plutôt que de translater la vignette à la main.
        pos = self.mapFromGlobal(QCursor.pos())
        self._last_drag_pos = pos
        self._apply_drag_position(pos)


# ─────────────────────────────────────────────────────────────────────────────
# Dialogue de oui/non/annuler (supprimer fichiers sources ?)
# ─────────────────────────────────────────────────────────────────────────────

class YesNoCancelDialog(QDialog):
    """Dialogue Oui / Non / Annuler."""

    # Résultats possibles
    YES    = 1
    NO     = 2
    CANCEL = 0

    result_signal = Signal(int)   # YES | NO | CANCEL

    def __init__(self, parent, title_key: str, message_key: str):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self._title_key   = title_key
        self._message_key = message_key
        self._result_val  = self.CANCEL
        self._emitted = False
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)
        # Largeur fixe (design), hauteur libre : le message fait 3 lignes + boutons,
        # 160px etait trop juste (3e ligne coupee). Qt calcule la hauteur reelle.
        self.setFixedWidth(420)
        self.setMinimumHeight(160)

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
        # Le sizeHint d'un QLabel WordWrap sous-estime la hauteur (il suppose le
        # texte sur une seule ligne large). On impose la hauteur reelle pour la
        # largeur contrainte (420 - marges 20*2 = 380px), sinon la derniere ligne
        # du message multi-lignes est coupee.
        self._lbl.setMinimumHeight(self._lbl.heightForWidth(380))
        self._btn_yes.setText(_("buttons.yes"))
        self._btn_yes.setFont(font)
        self._btn_no.setText(_("buttons.no"))
        self._btn_no.setFont(font)
        self._btn_cancel.setText(_("buttons.cancel"))
        self._btn_cancel.setFont(font)

    def _on_yes(self):
        self._finish(self.YES)

    def _on_no(self):
        self._finish(self.NO)

    def _on_cancel(self):
        self._finish(self.CANCEL)

    def _finish(self, result):
        if self._emitted:
            return
        self._emitted = True
        self._result_val = result
        _disconnect_lang(self)
        self.result_signal.emit(result)
        self.hide()
        self.deleteLater()

    def reject(self):
        # Échap : même chemin que la croix (réponse « annulé », destruction).
        # QDialog.reject masquerait la fenêtre sans passer par closeEvent.
        self.close()

    def closeEvent(self, event):
        if not self._emitted:
            self._emitted = True
            self._result_val = self.CANCEL
            _disconnect_lang(self)
            self.result_signal.emit(self.CANCEL)
        event.accept()

    def ask_async(self, on_result):
        """Affiche (NON modal) et appelle on_result(int) à la réponse."""
        from modules.qt.dialogs_qt import position_dialog_on_parent
        self.result_signal.connect(on_result)
        # Hauteur libre : adjustSize() ajuste la hauteur au contenu (message 3 lignes)
        # AVANT le centrage. La largeur reste fixée par setFixedWidth(420).
        self.adjustSize()
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

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
        self.setWindowFlags(Qt.Window)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        # Détruite à la fermeture : masquée seulement, elle resterait enfant du
        # panneau jusqu'à la fin de la session, avec les pages sources (celles
        # que la jointure supprime comprises) et leurs images décodées.
        # _ask_delete_sources, appelé après la fermeture (et après la fin de la
        # jointure animée), ne lit que ses attributs Python.
        self.setAttribute(Qt.WA_DeleteOnClose)
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
        self._source_entries = selected_entries
        self._doc_generation = self._state.doc_generation
        # Images décodées des pages sources (_decoded_source), libérées à la
        # fermeture (_release_images)
        self._decoded = {}
        self.finished.connect(self._release_images)
        self._canvas = MiniMosaicCanvas(self, selected_entries, self._decoded)
        self._canvas.positions_changed = self._schedule_preview_update

        # Debounce : la recomposition de preview redimensionne les images sources
        # en pleine résolution (coûteux), donc on ne la relance qu'une fois le
        # déplacement stabilisé plutôt qu'à chaque pixel de mouseMoveEvent.
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(150)
        self._preview_timer.timeout.connect(self._update_preview)

        scroll = QScrollArea()
        scroll.setWidget(self._canvas)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, stretch=3)
        self._canvas._scroll_area = scroll

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
        self._cancel_btn.clicked.connect(self.close)
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

    def _schedule_preview_update(self):
        self._preview_timer.start()

    def _release_images(self):
        """Fermeture (croix, Annuler, Échap, jointure lancée) : libère les
        images décodées par la fenêtre. Elles ne servent plus — la planche est
        déjà assemblée, la jointure animée lit une copie des bytes — et la
        fenêtre peut rester référencée jusqu'à la réponse à la question sur
        les pages sources."""
        self._preview_timer.stop()
        self._decoded.clear()

    def _update_preview(self):
        """Compose une prévisualisation en réutilisant le vrai algorithme d'assemblage
        (merge_images_2d) sur des images réduites, pour que les proportions entre
        images de tailles très différentes (ex. un join précédent) restent fidèles
        au résultat final, contrairement au placement libre du canvas du haut."""
        positions = self._canvas.get_positions_data()
        if not positions:
            return

        imgs = []
        for pos in positions:
            img = _decoded_source(pos["entry"], self._decoded)
            if img is None:
                return
            imgs.append(img)

        # Un seul facteur d'échelle commun à toutes les images, basé sur la plus
        # grande dimension parmi toutes les sources : un thumbnail() individuel
        # par image casserait la cohérence des largeurs/hauteurs relatives entre
        # elles dès que leurs ratios diffèrent (ex. une page issue d'un join
        # précédent, bien plus haute que large que les pages normales).
        largest_dim = max(max(img.size) for img in imgs)
        scale = min(1.0, PREVIEW_SRC_MAX / largest_dim)

        preview_positions = []
        for pos, img in zip(positions, imgs):
            t = img.resize(
                (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                Image.Resampling.LANCZOS,
            )
            preview_positions.append({
                "entry": {"img": t},
                "x": pos["x"],
                "y": pos["y"],
            })

        preview = merge_images_2d(preview_positions, ask_adjustment_func=None)
        if preview is None:
            return

        preview.thumbnail((300, 200))
        pixmap = _pil_to_qpixmap(preview)
        self._preview_lbl.setPixmap(pixmap)

    def _image_positions(self, positions):
        """`positions` (get_positions_data) avec, à la place de chaque page,
        son image décodée par la fenêtre : detect_merge_adjustment et
        merge_images_2d lisent entry["img"], qui reste vide sur les pages
        (_decoded_source)."""
        return [{"entry": {"img": _decoded_source(pos["entry"], self._decoded)},
                 "x": pos["x"], "y": pos["y"]}
                for pos in positions]

    def _on_join(self):
        positions = self._canvas.get_positions_data()

        # Étape 1 : détecter SI un ajustement de taille est nécessaire (calcul pur,
        # pas d'UI). Si oui → demander à l'utilisateur via SizeAdjustmentDialog NON
        # modal, puis poursuivre dans le callback ; sinon → poursuivre directement.
        from modules.qt.image_ops import detect_merge_adjustment
        need, dimension_type, dimensions_list = detect_merge_adjustment(
            self._image_positions(positions))

        if need:
            dlg = SizeAdjustmentDialog(self, dimension_type, dimensions_list)

            def _after_size(mode):
                if mode is None:
                    return   # annulé → MergeDialog reste ouvert
                self._finish_join(positions, mode)

            dlg.ask_async(_after_size)
        else:
            self._finish_join(positions, 'keep_original')

    def _finish_join(self, positions, adjustment_mode):
        """Étape 2 : fusionne avec le mode déjà choisi, crée l'entrée, ferme la
        fenêtre, puis demande la suppression des sources (NON modal)."""
        # Document fermé ou remplacé pendant que la fenêtre était ouverte : ses
        # pages n'appartiennent plus au panneau.
        if self._state.doc_generation != self._doc_generation:
            self.close()
            return
        # Opération (rotation…) lancée pendant que la fenêtre était ouverte :
        # sans effet tant qu'elle tourne, la fenêtre reste ouverte
        # (panel_widget.BUSY_BLOCKED_COMMANDS).
        if self._state.operation_running():
            return

        # animations_joinable garantit que toutes les sources sont animées, ou
        # aucune.
        if is_animated_entry(positions[0]["entry"]):
            self._start_animated_join(positions, adjustment_mode)
            return

        state = self._state

        # Détermine le format de sortie (avant la fusion : il décide si la
        # transparence des pages peut être gardée)
        source_extensions = {pos["entry"].get("extension", "").lower() for pos in positions}
        ext_to_format = {
            ".jpg": ("JPEG", ".jpg"), ".jpeg": ("JPEG", ".jpg"),
            ".jfif": ("JPEG", ".jpg"), ".pjpeg": ("JPEG", ".jpg"), ".pjp": ("JPEG", ".jpg"),
            ".gif": ("GIF", ".gif"),
            ".png": ("PNG", ".png"),
            ".webp": ("WEBP", ".webp"),
            ".bmp": ("BMP", ".bmp"),
            ".tiff": ("TIFF", ".tiff"), ".tif": ("TIFF", ".tiff"),
            ".avif": ("AVIF", ".avif"),
        }
        if len(source_extensions) == 1:
            save_format, out_ext = ext_to_format.get(source_extensions.pop(), ("PNG", ".png"))
        else:
            save_format, out_ext = "PNG", ".png"

        # Transparence des pages gardée quand le format de sortie la stocke
        # (GIF : en tout ou rien) ; les zones de la planche couvertes par
        # aucune page restent blanches.
        image_positions = self._image_positions(positions)
        loaded = [p["entry"]["img"] for p in image_positions]
        keep_alpha = save_format in ("PNG", "WEBP", "AVIF", "TIFF", "GIF") and any(
            img is not None and _has_alpha(img) for img in loaded)

        # Fusion PIL avec le mode déjà décidé (ask_adjustment_func retourne ce mode,
        # plus aucun dialogue n'est ouvert ici).
        merged_img = merge_images_2d(
            image_positions,
            ask_adjustment_func=lambda dt, dl: adjustment_mode,
            keep_alpha=keep_alpha,
        )
        if merged_img is None:
            return

        collage_num  = _get_next_collage_number(self._state)
        collage_name = f"{_('dialogs.join.merged_filename_prefix')}_{collage_num:02d}{out_ext}"

        # DPI source
        source_dpi = None
        for img in loaded:
            if img:
                dpi = image_dpi(img)
                if dpi:
                    source_dpi = dpi
                    break

        img_bytes = io.BytesIO()
        save_kwargs = {}
        if save_format in ("JPEG", "WEBP"):
            save_kwargs["quality"] = 95
        if source_dpi:
            save_kwargs["dpi"] = source_dpi
        # Profil ICC repris seulement s'il est le même pour toutes les pages :
        # des pages aux profils différents n'ont pas de profil commun juste
        if save_format in ("JPEG", "WEBP", "PNG", "TIFF", "AVIF"):
            profiles = {page_icc_profile(merged_img, pos["entry"].get("bytes"))
                        for pos in positions}
            if len(profiles) == 1:
                save_kwargs["icc_profile"] = profiles.pop()
        merged_img.save(img_bytes, format=save_format, **save_kwargs)

        # Sans image décodée : une retouche suivante relira la planche depuis
        # ses bytes, tels qu'ils seront enregistrés
        new_entry = create_entry(collage_name, img_bytes.getvalue(), IMAGE_EXTS)

        merge_parent = self.parent()
        self.close()
        self._ask_delete_sources(merge_parent, new_entry)

    def _start_animated_join(self, positions, adjustment_mode):
        """Jointure d'images animées : assemblage frame par frame dans un worker,
        avec le texte rouge de progression et Annuler sur la mosaïque du
        panneau. La fenêtre se ferme dès le lancement ; à la fin, la suite est
        la même que pour une jointure fixe (_ask_delete_sources)."""
        from modules.qt.web_import_qt import _show_cancel_item

        state = self._state
        canvas = self._callbacks.get("canvas")
        merge_parent = self.parent()

        # Même format que les sources si elles le partagent, sinon GIF.
        ext_to_format = {".gif": ("GIF", ".gif"), ".webp": ("WEBP", ".webp"),
                         ".png": ("PNG", ".png"), ".avif": ("AVIF", ".avif")}
        source_extensions = {pos["entry"].get("extension", "").lower() for pos in positions}
        if len(source_extensions) == 1:
            save_format, out_ext = ext_to_format.get(source_extensions.pop(), ("GIF", ".gif"))
        else:
            save_format, out_ext = "GIF", ".gif"

        # Copie des données sur le thread UI : le worker ne lit jamais les
        # entrées, que le panneau peut modifier pendant le traitement.
        sources = [{"bytes": pos["entry"]["bytes"], "x": pos["x"], "y": pos["y"]}
                   for pos in positions]
        durations = list(positions[0]["entry"].get("gif_durations") or [])
        doc_generation = self._doc_generation

        item_holder   = [None]
        cancel_holder = [None]
        worker_ref    = [None]

        def _show(pct):
            if worker_ref[0] is None or canvas is None:
                return
            _show_canvas_text(canvas, _("labels.joining", percent=pct), item_holder)
            _show_cancel_item(canvas, f"[ {_('buttons.cancel')} ]", cancel_holder, _cancel,
                              anchor_lbl=item_holder[0])

        def _hide():
            if canvas is None:
                return
            _hide_canvas_text(canvas, item_holder)
            _hide_canvas_text(canvas, cancel_holder)

        def _cancel():
            # Rien n'a été écrit dans state : arrêter le worker suffit.
            w = worker_ref[0]
            if w is None:
                return
            w._cancelled.set()
            worker_ref[0] = None
            _hide()

        def _release_worker():
            if canvas is not None:
                unregister_cancel_on_close(canvas, _cancel)
            if worker in _active_workers:
                _active_workers.remove(worker)
            dispose_qthread(worker)

        def on_done(data):
            cancelled = worker_ref[0] is None
            worker_ref[0] = None
            _hide()
            _release_worker()
            # Document fermé ou remplacé pendant le traitement : le résultat
            # appartient à un autre document (voir AppState.doc_generation).
            if cancelled or state.doc_generation != doc_generation:
                return
            if data is None:
                from modules.qt.dialogs_qt import ErrorDialog
                ErrorDialog(
                    merge_parent,
                    lambda: _wt("messages.errors.join_failed.title"),
                    lambda: _("messages.errors.join_failed.message"),
                ).show_nonmodal()
                return
            collage_num  = _get_next_collage_number(state)
            collage_name = f"{_('dialogs.join.merged_filename_prefix')}_{collage_num:02d}{out_ext}"
            new_entry = create_entry(collage_name, data, IMAGE_EXTS)
            self._ask_delete_sources(merge_parent, new_entry)

        def on_cancelled():
            _release_worker()

        worker = _AnimatedJoinWorker(sources, durations, adjustment_mode, save_format)
        worker_ref[0] = worker
        _active_workers.append(worker)
        if canvas is not None:
            # Fermeture du fichier pendant le traitement : même effet qu'Annuler.
            register_cancel_on_close(canvas, _cancel)
        worker.progress.connect(_show)
        worker.done.connect(on_done)
        worker.cancelled.connect(on_cancelled)

        self.close()
        _show(0)
        worker.start()

    def _ask_delete_sources(self, merge_parent, new_entry):
        """Étape 3 : demande si on supprime les fichiers sources (NON modal),
        puis insère new_entry dans la mosaïque."""
        state = self._state
        source_entries = self._source_entries
        doc_generation = self._doc_generation

        # Étape 3 : demande si on supprime les fichiers sources (NON modal)
        ync = YesNoCancelDialog(
            merge_parent,
            "messages.questions.delete_source_files.title",
            "messages.questions.delete_source_files.message",
        )

        renumber_mode = getattr(state, "renumber_mode", 1)

        def _after_delete_choice(result):
            # Document fermé ou remplacé pendant que la question était affichée.
            if state.doc_generation != doc_generation:
                return
            # Opération (rotation…) lancée pendant que la question était
            # affichée : la planche est insérée à sa fin, sinon son annulation
            # l'effacerait (AppState.run_when_idle).
            if state.operation_running():
                state.run_when_idle(lambda: _after_delete_choice(result))
                return
            if result == YesNoCancelDialog.CANCEL:
                # Rien à défaire — on n'a pas encore modifié state
                self._callbacks["render_mosaic"]()
                self._callbacks["update_button_text"]()
                return

            # Pages sources retrouvées par identité au moment de la réponse, pas
            # par la sélection ni des indices figés : la question n'est pas
            # modale (et une jointure animée tourne dans un worker), l'ordre des
            # pages et la sélection ont pu changer depuis le clic sur Joindre.
            source_indices = sorted(
                i for i, e in enumerate(state.images_data)
                if any(e is s for s in source_entries)
            )
            first_join_idx = source_indices[0] if source_indices else len(state.images_data)

            # Sauvegarde l'état AVANT modification (point de retour pour undo)
            _save_state_data(state, force=True)

            if renumber_mode == 0:
                # Renumérotation désactivée : la page jointe rejoint la tête de
                # mosaïque sous son nom Collage_xxx. « Oui, supprimer les
                # sources » s'applique comme avec la renumérotation active.
                if result == YesNoCancelDialog.YES:
                    for idx in reversed(source_indices):
                        state.images_data.pop(idx)
                insert_idx = 0
                state.images_data.insert(insert_idx, new_entry)
            elif result == YesNoCancelDialog.YES and source_indices:
                # Sources supprimées : le join prend l'emplacement de la 1ère
                # page du join une fois les autres pages sources retirées.
                for idx in reversed(source_indices[1:]):
                    state.images_data.pop(idx)
                insert_idx = first_join_idx
                state.images_data[insert_idx] = new_entry
            else:
                # Sources conservées : le join s'intercale juste avant la 1ère
                # page du join, qui reste inchangée.
                insert_idx = first_join_idx
                state.images_data.insert(insert_idx, new_entry)

            state.modified = True
            state.needs_renumbering = True

            from modules.qt.comic_info import sync_pages_in_xml_data
            sync_pages_in_xml_data(state)

            # Déselectionne tout ; la nouvelle image sera sélectionnée dans _finish,
            # une fois la renumérotation (si déclenchée) terminée.
            self._callbacks["clear_selection"]()

            def _finish():
                # Recherchée par identité d'objet (pas par insert_idx figé) car la
                # renumérotation ne réordonne pas la liste aujourd'hui, mais pourrait le faire.
                for i, e in enumerate(state.images_data):
                    if e is new_entry:
                        state.selected_indices.add(i)
                        break
                # Sauvegarde l'état APRÈS modification (point de retour pour redo)
                _save_state_data(state, force=True)
                self._callbacks["render_mosaic"]()
                self._callbacks["update_button_text"]()

            if renumber_mode != 0:
                self._callbacks["renumber_no_save"](on_done=_finish)
            else:
                _finish()

        ync.ask_async(_after_delete_choice)


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
        dlg.show_nonmodal()
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
        dlg.show_nonmodal()
        return

    # Filet de sécurité : les commandes de jointure sont déjà grisées pour une
    # sélection qui mélange images animées et fixes, ou des animations aux
    # frames/durées différentes.
    if not animations_joinable(selected_entries):
        return

    dlg = MergeDialog(parent, callbacks)
    from modules.qt.dialogs_qt import position_dialog_on_parent
    position_dialog_on_parent(dlg, parent)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
