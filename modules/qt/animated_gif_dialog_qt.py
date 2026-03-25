"""
modules/qt/animated_gif_dialog_qt.py — Création/édition de GIF animé (version PySide6)

Reproduit à l'identique le comportement de modules/animated_gif_dialog.py (tkinter).
Toutes les fenêtres supportent :
  - le thème courant (clair/sombre)
  - le changement de langue à la volée via language_signal
  - la police courante via get_current_font

Fonction publique :
  show_animated_gif_dialog(selected_entries, callbacks=None)
"""

import io
import os

from PIL import Image

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QSpinBox, QCheckBox, QButtonGroup,
    QRadioButton, QTextEdit, QApplication, QSizePolicy, QFrame,
)
from PySide6.QtCore import Qt, QMimeData, QPoint, QByteArray
from PySide6.QtGui import QPixmap, QImage, QDrag, QCursor, QPainter, QColor, QPen, QPolygon

from modules.qt import state as _state_module
from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.entries import ensure_image_loaded, free_image_memory, get_gif_frame, create_entry
from modules.qt.dialogs_qt import MsgDialog


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
        f"QPushButton:hover {{ background: {theme['separator']}; }}"
    )


def _pil_to_qpixmap(pil_img: Image.Image, max_w: int, max_h: int) -> QPixmap:
    """Crée un QPixmap vignette depuis une PIL Image."""
    try:
        thumb = pil_img.copy()
        thumb.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
        thumb = thumb.convert("RGBA")
        data = thumb.tobytes("raw", "RGBA")
        qimg = QImage(data, thumb.width, thumb.height, QImage.Format_RGBA8888)
        return QPixmap.fromImage(qimg)
    except Exception:
        return QPixmap()


# ─────────────────────────────────────────────────────────────────────────────
# Widget vignette (source de drag uniquement)
# ─────────────────────────────────────────────────────────────────────────────

class _ThumbWidget(QFrame):
    """Vignette d'une frame du GIF. Source de drag. Le drop est géré par _ThumbsContainer."""

    MIME       = "application/x-mosaicview-gif-idx"
    THUMB_SIZE = 100

    def __init__(self, idx: int, entry: dict, dialog: "AnimatedGifDialog"):
        super().__init__()
        self._idx    = idx
        self._entry  = entry
        self._dialog = dialog

        self.setFrameShape(QFrame.Box)
        self.setLineWidth(2)
        self.setFixedWidth(self.THUMB_SIZE + 20)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # Image
        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignCenter)
        self._img_lbl.setFixedSize(self.THUMB_SIZE, self.THUMB_SIZE)
        layout.addWidget(self._img_lbl)

        # Numéro + bouton supprimer
        row = QHBoxLayout()
        row.setSpacing(2)
        self._num_lbl = QLabel(f"#{idx + 1}")
        self._num_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        row.addWidget(self._num_lbl)
        self._del_btn = QPushButton("✕")
        self._del_btn.setFixedSize(22, 22)
        self._del_btn.clicked.connect(lambda: dialog._delete_image(idx))
        row.addWidget(self._del_btn)
        layout.addLayout(row)

        self._load_thumb()
        self._apply_theme()

    def _load_thumb(self):
        entry = self._entry
        # Utilise le pixmap mis en cache pour éviter de redécoder l'image à chaque rebuild
        px = entry.get("_thumb_px")
        if px is None:
            img = entry.get("img") or ensure_image_loaded(entry)
            if img:
                px = _pil_to_qpixmap(img, self.THUMB_SIZE, self.THUMB_SIZE)
                entry["_thumb_px"] = px
            else:
                px = None
        if px and not px.isNull():
            self._img_lbl.setPixmap(px)
        else:
            self._img_lbl.setText("[?]")

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"QFrame {{ background: {theme['toolbar_bg']}; border: 2px solid {theme['separator']}; }} "
            f"QLabel {{ color: {theme['text']}; border: none; }} "
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 1px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        font = _get_current_font(9)
        self._num_lbl.setFont(font)
        self._del_btn.setFont(font)

    # ── Drag (source) ─────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton):
            return
        if (event.position().toPoint() - self._drag_start_pos).manhattanLength() < 5:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(self.MIME, QByteArray(str(self._idx).encode()))
        drag.setMimeData(mime)
        px = self._img_lbl.pixmap()
        if px and not px.isNull():
            drag.setPixmap(px)
            drag.setHotSpot(QPoint(px.width() // 2, px.height() // 2))
        drag.exec(Qt.MoveAction)


# ─────────────────────────────────────────────────────────────────────────────
# Conteneur des vignettes — gère le drop et dessine l'indicateur de position
# ─────────────────────────────────────────────────────────────────────────────

class _ThumbsContainer(QWidget):
    """
    Conteneur horizontal des vignettes.
    Reçoit les drops et affiche une ligne rouge + triangles à la position d'insertion.
    """

    MIME            = _ThumbWidget.MIME
    INDICATOR_COLOR = QColor(220, 0, 0)   # rouge vif
    TRI_SIZE        = 8                   # demi-base des triangles

    def __init__(self, dialog: "AnimatedGifDialog"):
        super().__init__()
        self._dialog   = dialog
        self._drop_pos = -1   # index d'insertion courant (-1 = aucun indicateur)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(8)
        self._layout.addStretch()

        self.setAcceptDrops(True)

    # ── Gestion des widgets ───────────────────────────────────────────────────

    def rebuild(self, entries: list, dialog: "AnimatedGifDialog"):
        """Reconstruit les vignettes depuis la liste entries."""
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for idx, entry in enumerate(entries):
            thumb = _ThumbWidget(idx, entry, dialog)
            self._layout.insertWidget(idx, thumb)

    def apply_theme_to_thumbs(self):
        for i in range(self._layout.count()):
            w = self._layout.itemAt(i).widget()
            if isinstance(w, _ThumbWidget):
                w._apply_theme()

    # ── Vignettes accessibles ─────────────────────────────────────────────────

    def _thumb_widgets(self) -> list:
        result = []
        for i in range(self._layout.count()):
            w = self._layout.itemAt(i).widget()
            if isinstance(w, _ThumbWidget):
                result.append(w)
        return result

    # ── Calcul de l'index d'insertion à partir du x de la souris ─────────────

    def _insert_index_at(self, x_local: int) -> int:
        """Retourne l'index d'insertion (0…N) correspondant à la position x."""
        thumbs = self._thumb_widgets()
        if not thumbs:
            return 0
        for i, tw in enumerate(thumbs):
            center = tw.x() + tw.width() // 2
            if x_local < center:
                return i
        return len(thumbs)

    # ── Coordonnée x de la ligne d'insertion ─────────────────────────────────

    def _x_for_insert_index(self, idx: int) -> int:
        thumbs = self._thumb_widgets()
        if not thumbs:
            return self._layout.contentsMargins().left()
        if idx == 0:
            return thumbs[0].x() - self._layout.spacing() // 2
        if idx >= len(thumbs):
            last = thumbs[-1]
            return last.x() + last.width() + self._layout.spacing() // 2
        left  = thumbs[idx - 1]
        right = thumbs[idx]
        return (left.x() + left.width() + right.x()) // 2

    # ── Dessin de l'indicateur ────────────────────────────────────────────────

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_pos < 0:
            return

        x = self._x_for_insert_index(self._drop_pos)
        t = self.TRI_SIZE

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self.INDICATOR_COLOR, 2)
        painter.setPen(pen)
        painter.setBrush(self.INDICATOR_COLOR)

        # Ligne verticale
        painter.drawLine(x, t * 2, x, self.height() - t * 2)

        # Triangle haut (pointe vers le bas, collé au bord supérieur)
        top_tri = QPolygon([
            QPoint(x - t, 2),
            QPoint(x + t, 2),
            QPoint(x,     2 + t * 2),
        ])
        painter.drawPolygon(top_tri)

        # Triangle bas (pointe vers le haut, collé au bord inférieur)
        h = self.height()
        bot_tri = QPolygon([
            QPoint(x - t, h - 2),
            QPoint(x + t, h - 2),
            QPoint(x,     h - 2 - t * 2),
        ])
        painter.drawPolygon(bot_tri)

        painter.end()

    # ── Drag & Drop (destination) ─────────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(self.MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if not event.mimeData().hasFormat(self.MIME):
            return
        insert_idx = self._insert_index_at(event.position().toPoint().x())
        if insert_idx != self._drop_pos:
            self._drop_pos = insert_idx
            self.update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._drop_pos = -1
        self.update()

    def dropEvent(self, event):
        self._drop_pos = -1
        self.update()
        if not event.mimeData().hasFormat(self.MIME):
            return
        src_idx    = int(event.mimeData().data(self.MIME).toStdString())
        insert_idx = self._insert_index_at(event.position().toPoint().x())

        # Si on déplace vers la droite, l'index effectif est décalé de -1
        if src_idx < insert_idx:
            insert_idx -= 1

        if src_idx != insert_idx:
            self._dialog._move_image(src_idx, insert_idx)
        event.acceptProposedAction()


# ─────────────────────────────────────────────────────────────────────────────
# Dialogue principal
# ─────────────────────────────────────────────────────────────────────────────

class AnimatedGifDialog(QDialog):
    """Fenêtre de création/édition d'un GIF animé."""

    IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
                  '.tiff', '.tif', '.ico', '.jfif', '.pjpeg', '.pjp')

    def __init__(self, parent, selected_entries: list, callbacks: dict):
        super().__init__(parent)
        self._callbacks = callbacks or {}
        self._state     = _state_module.state

        # ── Extraction des frames si GIF animé unique ─────────────────────
        original_gif_metadata = None
        if len(selected_entries) == 1 and selected_entries[0].get("is_animated_gif"):
            gif_entry = selected_entries[0]
            original_gif_metadata = {
                "durations": gif_entry.get("gif_durations", []),
                "loop":      gif_entry.get("gif_loop", 0),
                "disposal":  gif_entry.get("gif_disposal", 2),
                "comment":   gif_entry.get("gif_comment", ""),
                "optimize":  gif_entry.get("gif_optimize", True),
            }
            selected_entries = []
            base_name = os.path.splitext(gif_entry["orig_name"])[0]
            # Ouvre le GIF une seule fois et itère les frames en séquence (évite O(N²))
            try:
                from PIL import ImageSequence as _ImageSequence
                gif_img = Image.open(io.BytesIO(gif_entry["bytes"]))
                for i, raw_frame in enumerate(_ImageSequence.Iterator(gif_img)):
                    frame = raw_frame.copy().convert("RGBA")
                    frame_entry = {
                        "orig_name":         f"{base_name}_frame_{i:03d}.png",
                        "img":               frame,
                        "extension":         ".png",
                        "is_image":          True,
                        "is_dir":            False,
                        "is_corrupted":      False,
                        "corruption_reason": None,
                        "is_animated_gif":   False,
                        "from_gif_frame":    True,
                        "gif_source":        gif_entry["orig_name"],
                    }
                    selected_entries.append(frame_entry)
                gif_img.close()
            except Exception:
                pass

        self._gif_images = [e.copy() for e in selected_entries]

        # ── Valeurs initiales ─────────────────────────────────────────────
        if original_gif_metadata:
            durations = original_gif_metadata["durations"]
            self._delay_ms  = sum(durations) // len(durations) if durations else 100
            self._loop      = original_gif_metadata["loop"]
            self._disposal  = original_gif_metadata["disposal"]
            self._comment   = original_gif_metadata["comment"]
            self._optimize  = original_gif_metadata["optimize"]
        else:
            self._delay_ms = 100
            self._loop     = 0
            self._disposal = 2
            self._comment  = ""
            self._optimize = True

        # ── Fenêtre ───────────────────────────────────────────────────────
        self.setModal(True)
        self.setMinimumSize(800, 600)
        self.resize(1000, 700)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(6)

        # ── Zone IMAGES (haut) ────────────────────────────────────────────
        self._images_title = QLabel()
        self._images_title.setAlignment(Qt.AlignCenter)
        root_layout.addWidget(self._images_title)

        self._scroll_area = QScrollArea()
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setFixedHeight(170)
        self._scroll_area.setWidgetResizable(True)

        self._thumbs_container = _ThumbsContainer(self)
        self._scroll_area.setWidget(self._thumbs_container)
        root_layout.addWidget(self._scroll_area)

        # ── Séparateur ────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        root_layout.addWidget(sep)

        # ── Zone PARAMÈTRES (bas) ─────────────────────────────────────────
        self._params_title = QLabel()
        self._params_title.setAlignment(Qt.AlignCenter)
        root_layout.addWidget(self._params_title)

        params_row = QHBoxLayout()
        params_row.setSpacing(20)

        # ── Colonne gauche ────────────────────────────────────────────────
        left_col = QVBoxLayout()
        left_col.setSpacing(8)

        # Délai
        self._delay_title = QLabel()
        left_col.addWidget(self._delay_title)

        delay_row = QHBoxLayout()
        delay_row.setSpacing(4)
        self._minus_btn = QPushButton("-")
        self._minus_btn.setFixedWidth(36)
        self._minus_btn.clicked.connect(self._decrease_delay)
        delay_row.addWidget(self._minus_btn)
        self._delay_spin = QSpinBox()
        self._delay_spin.setRange(20, 5000)
        self._delay_spin.setValue(self._delay_ms)
        self._delay_spin.setSuffix(" ms")
        self._delay_spin.setFixedWidth(100)
        self._delay_spin.valueChanged.connect(self._on_delay_changed)
        delay_row.addWidget(self._delay_spin)
        self._plus_btn = QPushButton("+")
        self._plus_btn.setFixedWidth(36)
        self._plus_btn.clicked.connect(self._increase_delay)
        delay_row.addWidget(self._plus_btn)
        delay_row.addStretch()
        left_col.addLayout(delay_row)

        self._fps_lbl = QLabel()
        left_col.addWidget(self._fps_lbl)

        # Loop
        self._loop_title = QLabel()
        left_col.addWidget(self._loop_title)
        self._loop_spin = QSpinBox()
        self._loop_spin.setRange(0, 9999)
        self._loop_spin.setValue(self._loop)
        self._loop_spin.setFixedWidth(100)
        left_col.addWidget(self._loop_spin)
        self._loop_info = QLabel()
        self._loop_info.setWordWrap(True)
        left_col.addWidget(self._loop_info)

        # Disposal
        self._disposal_title = QLabel()
        left_col.addWidget(self._disposal_title)
        self._disposal_group  = QButtonGroup(self)
        self._disposal_radios = []
        for i in range(4):
            rb = QRadioButton()
            rb.setChecked(i == self._disposal)
            rb._disposal_val = i
            rb._key = f"dialogs.gif_animated.disposal_{i}"
            self._disposal_group.addButton(rb, i)
            self._disposal_radios.append(rb)
            left_col.addWidget(rb)

        # Optimize
        self._optimize_check = QCheckBox()
        self._optimize_check.setChecked(self._optimize)
        left_col.addWidget(self._optimize_check)

        left_col.addStretch()

        # ── Colonne droite ────────────────────────────────────────────────
        right_col = QVBoxLayout()
        right_col.setSpacing(8)

        # Métadonnées
        self._meta_title = QLabel()
        right_col.addWidget(self._meta_title)
        self._meta_lbl = QLabel()
        self._meta_lbl.setWordWrap(True)
        self._meta_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        right_col.addWidget(self._meta_lbl)

        # Commentaire
        self._comment_lbl = QLabel()
        right_col.addWidget(self._comment_lbl)
        self._comment_edit = QTextEdit()
        self._comment_edit.setFixedHeight(80)
        self._comment_edit.setPlainText(self._comment)
        right_col.addWidget(self._comment_edit)

        right_col.addStretch()

        params_row.addLayout(left_col)
        params_row.addLayout(right_col)
        root_layout.addLayout(params_row)

        # ── Label de progression ───────────────────────────────────────────
        self._progress_lbl = QLabel("")
        self._progress_lbl.setAlignment(Qt.AlignCenter)
        self._progress_lbl.setVisible(False)
        root_layout.addWidget(self._progress_lbl)

        # ── Boutons ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._create_btn = QPushButton()
        self._create_btn.setFixedWidth(140)
        self._create_btn.setDefault(True)
        self._create_btn.clicked.connect(self._create_animated_gif)
        btn_row.addWidget(self._create_btn)
        self._cancel_btn = QPushButton()
        self._cancel_btn.setFixedWidth(140)
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        root_layout.addLayout(btn_row)

        # ── Init affichage ────────────────────────────────────────────────
        self._render_thumbs()
        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._create_btn.setFocus()

    # ── Nettoyage mémoire ─────────────────────────────────────────────────────

    def _free_gif_images(self):
        """Libère les PIL Images et QPixmaps mis en cache dans _gif_images."""
        for entry in self._gif_images:
            img = entry.get("img")
            if img is not None:
                try:
                    img.close()
                except Exception:
                    pass
                entry["img"] = None
            entry.pop("_thumb_px", None)

    def closeEvent(self, event):
        """Libère la mémoire PIL quand la fenêtre est fermée (y compris annulation)."""
        self._free_gif_images()
        super().closeEvent(event)

    # ── Rendu vignettes ───────────────────────────────────────────────────────

    def _render_thumbs(self):
        """Reconstruit la zone de vignettes depuis self._gif_images."""
        self._thumbs_container.rebuild(self._gif_images, self)
        self._update_metadata()

    # ── Gestion des images ────────────────────────────────────────────────────

    def _delete_image(self, idx: int):
        if len(self._gif_images) > 1:
            del self._gif_images[idx]
            self._render_thumbs()

    def _move_image(self, src: int, dst: int):
        item = self._gif_images.pop(src)
        self._gif_images.insert(dst, item)
        self._render_thumbs()

    # ── Délai / FPS ───────────────────────────────────────────────────────────

    def _decrease_delay(self):
        self._delay_spin.setValue(max(20, self._delay_spin.value() - 50))

    def _increase_delay(self):
        self._delay_spin.setValue(min(5000, self._delay_spin.value() + 50))

    def _on_delay_changed(self, val: int):
        self._update_fps_label(val)
        self._update_metadata()

    def _update_fps_label(self, delay_ms: int):
        if delay_ms >= 1000:
            seconds = delay_ms / 1000.0
            self._fps_lbl.setText(_("dialogs.gif_animated.fps_slow", seconds=f"{seconds:.2f}"))
        else:
            fps = 1000.0 / delay_ms if delay_ms > 0 else 0
            self._fps_lbl.setText(_("dialogs.gif_animated.fps_normal", fps=f"{fps:.2f}"))

    def _update_metadata(self):
        if not self._gif_images:
            self._meta_lbl.setText(_("dialogs.gif_animated.no_images"))
            return
        first = self._gif_images[0]
        img = first.get("img") or ensure_image_loaded(first)
        width, height = (img.size if img else (0, 0))
        if img:
            free_image_memory(first)
        num_frames  = len(self._gif_images)
        delay_ms    = self._delay_spin.value()
        total_dur   = (num_frames * delay_ms) / 1000.0
        fps         = 1000.0 / delay_ms if delay_ms > 0 else 0
        est_size    = num_frames * width * height * 0.5 / 1024
        meta  = _("dialogs.gif_animated.metadata_dimensions", width=width, height=height) + "\n"
        meta += _("dialogs.gif_animated.metadata_frames",    count=num_frames) + "\n"
        meta += _("dialogs.gif_animated.metadata_duration",  duration=f"{total_dur:.2f}") + "\n"
        meta += _("dialogs.gif_animated.metadata_fps",       fps=f"{fps:.2f}") + "\n"
        meta += _("dialogs.gif_animated.metadata_palette") + "\n"
        meta += _("dialogs.gif_animated.metadata_size",      size=f"{est_size:.1f}")
        self._meta_lbl.setText(meta)

    # ── Création du GIF ───────────────────────────────────────────────────────

    def _create_animated_gif(self):
        if not self._gif_images:
            dlg = MsgDialog(self,
                            "messages.warnings.no_images_for_gif.title",
                            "messages.warnings.no_images_for_gif.message")
            dlg.exec()
            return

        try:
            comment     = self._comment_edit.toPlainText().strip()
            delay_ms    = self._delay_spin.value()
            loop        = self._loop_spin.value()
            disposal    = next((rb._disposal_val for rb in self._disposal_radios if rb.isChecked()), 2)
            do_optimize = self._optimize_check.isChecked()
            total       = len(self._gif_images)

            # Désactive les boutons et affiche la progression
            self._create_btn.setEnabled(False)
            self._cancel_btn.setEnabled(False)
            self._progress_lbl.setVisible(True)

            # Charge toutes les images et calcule la taille max
            max_w, max_h = 0, 0
            temp_images  = []
            for i, entry in enumerate(self._gif_images):
                self._progress_lbl.setText(
                    _("dialogs.gif_animated.creating_progress", current=i + 1, total=total)
                )
                QApplication.processEvents()
                img = entry.get("img") or ensure_image_loaded(entry)
                if img:
                    temp_images.append(img)
                    max_w = max(max_w, img.width)
                    max_h = max(max_h, img.height)

            # Normalise en RGB sur un canvas max_w×max_h
            rgb_images = []
            for i, img in enumerate(temp_images):
                self._progress_lbl.setText(
                    _("dialogs.gif_animated.creating_normalizing", current=i + 1, total=total)
                )
                QApplication.processEvents()
                canvas = Image.new("RGB", (max_w, max_h), (255, 255, 255))
                x_off  = (max_w - img.width)  // 2
                y_off  = (max_h - img.height) // 2
                if img.mode == "RGBA":
                    canvas.paste(img, (x_off, y_off), mask=img.split()[3])
                elif img.mode != "RGB":
                    canvas.paste(img.convert("RGB"), (x_off, y_off))
                else:
                    canvas.paste(img, (x_off, y_off))
                rgb_images.append(canvas)

            # Quantize avec palette commune
            pil_images = []
            if rgb_images:
                self._progress_lbl.setText(
                    _("dialogs.gif_animated.creating_quantizing", current=1, total=total)
                )
                QApplication.processEvents()
                first_p = rgb_images[0].quantize(colors=256, method=Image.ADAPTIVE)
                pil_images.append(first_p)
                for i, img in enumerate(rgb_images[1:], start=2):
                    self._progress_lbl.setText(
                        _("dialogs.gif_animated.creating_quantizing", current=i, total=total)
                    )
                    QApplication.processEvents()
                    pil_images.append(img.quantize(palette=first_p))

            # Libère les canvases RGB intermédiaires dès que la quantize est faite
            for img in rgb_images:
                try:
                    img.close()
                except Exception:
                    pass
            rgb_images.clear()

            if not pil_images:
                self._create_btn.setEnabled(True)
                self._cancel_btn.setEnabled(True)
                self._progress_lbl.setVisible(False)
                dlg = MsgDialog(self,
                                "messages.warnings.no_valid_images.title",
                                "messages.warnings.no_valid_images.message")
                dlg.exec()
                return

            self._progress_lbl.setText(_("dialogs.gif_animated.creating_saving"))
            QApplication.processEvents()

            img_bytes   = io.BytesIO()
            save_params = {
                "format":        "GIF",
                "save_all":      True,
                "append_images": pil_images[1:],
                "duration":      delay_ms,
                "loop":          loop,
                "disposal":      disposal,
                "optimize":      do_optimize,
            }
            if comment:
                save_params["comment"] = comment.encode("utf-8")
            pil_images[0].save(img_bytes, **save_params)
            gif_bytes = img_bytes.getvalue()
            img_bytes.close()

            # Nommage
            existing  = sum(1 for e in self._state.images_data
                            if e["orig_name"].startswith("Animated_"))
            gif_name  = f"Animated_{existing + 1}.gif"
            new_entry = create_entry(gif_name, gif_bytes, self.IMAGE_EXTS)
            new_entry["source_archive"] = "loose"
            self._state.images_data.append(new_entry)
            self._state.modified = True
            from modules.qt.comic_info import sync_pages_in_xml_data
            sync_pages_in_xml_data(self._state)

            # Libère les images PIL quantizées
            for img in pil_images:
                try:
                    img.close()
                except Exception:
                    pass

            # Libère les frames PIL sources (entry["img"]) — plus nécessaires après création
            self._free_gif_images()

            # Callbacks
            if self._callbacks.get("save_state"):
                self._callbacks["save_state"]()
            if self._callbacks.get("render_mosaic"):
                self._callbacks["render_mosaic"]()
            if self._callbacks.get("update_button_text"):
                self._callbacks["update_button_text"]()

            self.accept()

        except Exception as e:
            self._create_btn.setEnabled(True)
            self._cancel_btn.setEnabled(True)
            self._progress_lbl.setVisible(False)
            from modules.qt.dialogs_qt import MsgDialog as _MD
            dlg = _MD(self,
                      "messages.errors.gif_creation_failed.title",
                      "messages.errors.gif_creation_failed.message",
                      {"error": str(e)})
            dlg.exec()

    # ── Retranslate / restyle ─────────────────────────────────────────────────

    def _retranslate(self):
        theme    = get_current_theme()
        font_lrg = _get_current_font(14, bold=True)
        font     = _get_current_font(11)
        font_sm  = _get_current_font(9)
        btn_sty  = _btn_style(theme)
        state    = self._state
        note_color = "#666666" if not state.dark_mode else "#999999"

        self.setStyleSheet(
            f"QDialog   {{ background: {theme['bg']}; color: {theme['text']}; }} "
            f"QLabel    {{ color: {theme['text']}; }} "
            f"QCheckBox {{ color: {theme['text']}; }} "
            f"QSpinBox  {{ background: {theme['entry_bg']}; color: {theme['text']}; "
            f"             border: 1px solid #aaaaaa; }} "
            f"QTextEdit {{ background: {theme['entry_bg']}; color: {theme['text']}; "
            f"             border: 1px solid #aaaaaa; }} "
            f"QScrollArea {{ background: {theme['toolbar_bg']}; border: 1px solid {theme['separator']}; }}"
        )

        self.setWindowTitle(_wt("dialogs.gif_animated.window_title"))

        self._images_title.setText(_("dialogs.gif_animated.images_title"))
        self._images_title.setFont(font_lrg)

        self._params_title.setText(_("dialogs.gif_animated.params_title"))
        self._params_title.setFont(font_lrg)

        self._delay_title.setText(_("dialogs.gif_animated.frame_delay_label"))
        self._delay_title.setFont(_get_current_font(11, bold=True))
        self._delay_spin.setFont(font)
        self._minus_btn.setFont(font)
        self._minus_btn.setStyleSheet(btn_sty)
        self._plus_btn.setFont(font)
        self._plus_btn.setStyleSheet(btn_sty)
        self._update_fps_label(self._delay_spin.value())
        self._fps_lbl.setFont(font_sm)
        self._fps_lbl.setStyleSheet(f"color: {note_color};")

        self._loop_title.setText(_("dialogs.gif_animated.loop_label"))
        self._loop_title.setFont(_get_current_font(11, bold=True))
        self._loop_spin.setFont(font)
        self._loop_info.setText(_("dialogs.gif_animated.loop_info"))
        self._loop_info.setFont(font_sm)
        self._loop_info.setStyleSheet(f"color: {note_color};")

        self._disposal_title.setText(_("dialogs.gif_animated.disposal_label"))
        self._disposal_title.setFont(_get_current_font(11, bold=True))
        radio_sty = f"QRadioButton {{ background: {theme['bg']}; color: {theme['text']}; }}"
        for rb in self._disposal_radios:
            rb.setText(_(rb._key))
            rb.setFont(font)
            rb.setStyleSheet(radio_sty)

        self._optimize_check.setText(_("dialogs.gif_animated.optimize"))
        self._optimize_check.setFont(font)

        self._meta_title.setText(_("dialogs.gif_animated.metadata_title"))
        self._meta_title.setFont(_get_current_font(11, bold=True))
        self._meta_lbl.setFont(font)
        self._update_metadata()

        self._comment_lbl.setText(_("dialogs.gif_animated.comment_label"))
        self._comment_lbl.setFont(font)
        self._comment_edit.setFont(font)

        self._progress_lbl.setFont(font)
        self._progress_lbl.setStyleSheet("color: #cc0000; font-weight: bold;")

        self._create_btn.setText(_("buttons.create_gif"))
        self._create_btn.setFont(font)
        self._create_btn.setStyleSheet(btn_sty)

        self._cancel_btn.setText(_("buttons.cancel"))
        self._cancel_btn.setFont(font)
        self._cancel_btn.setStyleSheet(btn_sty)

        # Rafraîchit le style des vignettes
        self._thumbs_container.apply_theme_to_thumbs()


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée public
# ─────────────────────────────────────────────────────────────────────────────

def show_animated_gif_dialog(selected_entries: list, callbacks: dict = None):
    """Affiche la fenêtre de paramétrage pour créer un GIF animé.

    callbacks : dict avec les clés :
        - save_state          : callable
        - render_mosaic       : callable
        - update_button_text  : callable
        - parent              : QWidget parent (optionnel)
    """
    parent = (callbacks or {}).get("parent")
    dialog = AnimatedGifDialog(parent, selected_entries, callbacks or {})
    dialog.exec()
