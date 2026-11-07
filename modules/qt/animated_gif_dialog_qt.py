"""
modules/qt/animated_gif_dialog_qt.py — Création/édition de GIF animé.

Toutes les fenêtres supportent :
  - le thème courant (clair/sombre)
  - le changement de langue à la volée via language_signal
  - la police courante via get_current_font

Fonction publique :
  show_animated_gif_dialog(selected_entries, callbacks=None)
"""

import io
import os
import re

from PIL import Image

import threading

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QSpinBox, QCheckBox, QButtonGroup,
    QRadioButton, QTextEdit, QSizePolicy, QFrame,
)
from PySide6.QtCore import Qt, QMimeData, QPoint, QByteArray, QThread, QTimer, Signal
from PySide6.QtGui import QPixmap, QImage, QDrag, QCursor, QPainter, QColor, QPen, QPolygon, QValidator

from modules.qt import state as _state_module
from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.app_window_qt import AppWindowMixin, QuestionWindowMixin
from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.entries import ensure_image_loaded, free_image_memory, create_entry, page_size
from modules.qt.dialogs_qt import ConfirmDialog, ErrorDialog, MsgDialog
from modules.qt.gif_writer import (GifCancelled, STEP_FRAME, STEP_READING, build_gif, frame_delay,
                                   next_gif_name, pages_left_out)
from modules.qt.undo_redo import entry_key
from modules.qt.utils import (decimal_separator, decimal_text, dispose_qthread, register_cancel_on_close,
                              unregister_cancel_on_close)

# Plage du réglage « Délai », en millisecondes
_DELAY_MIN = 20
_DELAY_MAX = 5000
# Pas des flèches des champs de délai (vignettes et délai commun) : un GIF
# enregistre ses délais en centièmes de seconde. Pas des boutons - et +.
_DELAY_ARROW_STEP = 10
_DELAY_BUTTON_STEP = 50
# Délai tapé dans un champ : des secondes, au centième au plus, virgule ou point
_SECONDS_RE = re.compile(r"\d*([.,]\d{0,2})?")
# Pages nommées dans la question d'avant la création
_LEFT_OUT_LISTED = 10
# Zone de la liste, près de ses bords, où un glisser la fait défiler (pixels)
_AUTO_SCROLL_MARGIN = 40
_AUTO_SCROLL_STEP = 24
_AUTO_SCROLL_INTERVAL_MS = 40


def _auto_scroll_step(x, width):
    """Défilement de la liste pour un glisser à l'abscisse x de sa partie
    visible, de largeur `width` : la vignette à déplacer doit pouvoir atteindre
    une place hors de vue."""
    if x < _AUTO_SCROLL_MARGIN:
        return -_AUTO_SCROLL_STEP
    if x > width - _AUTO_SCROLL_MARGIN:
        return _AUTO_SCROLL_STEP
    return 0


def _delay_range(delay):
    """Plage du réglage d'une image de délai `delay`. Élargie à ce délai quand un
    GIF modifié en a un hors de la plage habituelle : ramené dans ses limites à
    l'affichage, il paraîtrait autre que celui qui sera écrit."""
    return min(_DELAY_MIN, delay), max(_DELAY_MAX, delay)


def _seconds_text(delay_ms, separator):
    """Délai en millisecondes, affiché en secondes au centième : un GIF
    enregistre ses délais en centièmes de seconde."""
    return decimal_text(delay_ms / 1000, 2, separator)


def _delay_from_text(text):
    """Délai en millisecondes d'un texte en secondes (virgule ou point), ou None
    s'il n'en est pas un."""
    if not _SECONDS_RE.fullmatch(text) or not any(c.isdigit() for c in text):
        return None
    return round(float(text.replace(",", ".")) * 100) * 10


def _average_delay(durations):
    """Délai commun proposé pour un GIF à modifier : moyenne de ses délais,
    arrondie au centième de seconde, seule précision qu'un GIF enregistre."""
    if not durations:
        return 100
    return round(sum(durations) / len(durations) / 10) * 10



def _delay_stats(delays):
    """(durée totale en secondes, images par seconde) d'une suite de délais."""
    total = sum(delays) / 1000.0
    return total, (len(delays) / total if total > 0 else 0)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers style
# ─────────────────────────────────────────────────────────────────────────────

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

class _NumberSpin(QSpinBox):
    """Champ numérique de la fenêtre. Entrée valide le nombre sans lancer la
    création (bouton par défaut de la fenêtre)."""

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.interpretText()
            self.selectAll()
            event.accept()
        else:
            super().keyPressEvent(event)


class _SecondsSpin(_NumberSpin):
    """Champ de délai : sa valeur reste en millisecondes, seuls l'affichage et la
    saisie sont en secondes, au centième, avec le séparateur décimal de la
    langue (virgule ou point acceptés à la saisie). Les millisecondes laissaient
    taper un délai qu'un GIF ne sait pas enregistrer (125 ms écrit 120)."""

    def _number_part(self, text):
        text = text.strip()
        suffix = self.suffix().strip()
        if suffix and text.endswith(suffix):
            text = text[:-len(suffix)]
        return text.strip()

    def textFromValue(self, value):
        return _seconds_text(value, decimal_separator())

    def valueFromText(self, text):
        delay = _delay_from_text(self._number_part(text))
        return self.value() if delay is None else delay

    def validate(self, text, pos):
        number = self._number_part(text)
        if not _SECONDS_RE.fullmatch(number):
            return QValidator.Invalid
        delay = _delay_from_text(number)
        if delay is None or not self.minimum() <= delay <= self.maximum():
            # Saisie en cours ; laissée hors plage, le champ reprend sa valeur
            return QValidator.Intermediate
        return QValidator.Acceptable


class _DelaySpin(_SecondsSpin):
    """Délai d'une image, sur sa vignette. La molette ne le change qu'après un clic
    dedans : sinon, en parcourant la liste, elle changerait le délai de l'image
    passant sous la souris."""

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


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
        # Largeur du champ du délai et de ses boutons - et +
        self.setFixedWidth(self.THUMB_SIZE + 60)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # Image
        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignCenter)
        self._img_lbl.setFixedSize(self.THUMB_SIZE, self.THUMB_SIZE)
        layout.addWidget(self._img_lbl, alignment=Qt.AlignHCenter)

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

        # Délai de cette image, entre ses boutons - et + (comme le délai commun)
        delay_row = QHBoxLayout()
        delay_row.setSpacing(2)
        self._delay_minus = QPushButton("-")
        self._delay_minus.setFixedWidth(22)
        self._delay_minus.clicked.connect(lambda: self._step_delay(-_DELAY_BUTTON_STEP))
        delay_row.addWidget(self._delay_minus)
        self._delay_spin = _DelaySpin()
        self._delay_spin.setSingleStep(_DELAY_ARROW_STEP)
        self.show_delay()
        self._delay_spin.valueChanged.connect(self._on_delay_changed)
        delay_row.addWidget(self._delay_spin)
        self._delay_plus = QPushButton("+")
        self._delay_plus.setFixedWidth(22)
        self._delay_plus.clicked.connect(lambda: self._step_delay(_DELAY_BUTTON_STEP))
        delay_row.addWidget(self._delay_plus)
        layout.addLayout(delay_row)

        self._load_thumb()
        self._apply_theme()

    def show_delay(self):
        """Affiche le délai de l'image, sans le considérer comme un choix de
        l'utilisateur."""
        delay = frame_delay(self._entry, self._dialog._delay_spin.value())
        self._delay_spin.blockSignals(True)
        self._delay_spin.setRange(*_delay_range(delay))
        self._delay_spin.setValue(delay)
        self._delay_spin.blockSignals(False)

    def _step_delay(self, step):
        # setValue garde la valeur dans la plage du champ
        self._delay_spin.setValue(self._delay_spin.value() + step)

    def _on_delay_changed(self, value):
        self._entry["gif_duration"] = value
        self._dialog._update_metadata()

    def _load_thumb(self):
        entry = self._entry
        # Utilise le pixmap mis en cache pour éviter de redécoder l'image à chaque rebuild
        px = entry.get("_thumb_px")
        if px is None:
            img = entry.get("img") or ensure_image_loaded(entry)
            if img:
                px = _pil_to_qpixmap(img, self.THUMB_SIZE, self.THUMB_SIZE)
                entry["_thumb_px"] = px
                # Image relue depuis ses bytes à la création : gardée, chaque
                # page sélectionnée resterait décodée tant que la fenêtre est
                # ouverte. Une image extraite d'un GIF n'existe qu'en mémoire.
                if entry.get("bytes"):
                    free_image_memory(entry)
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
            f"QPushButton:hover {{ background: {theme['separator']}; }} "
            f"QSpinBox {{ background: {theme['entry_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 2px 4px; }} "
            f"QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}"
        )
        font = _get_current_font(9)
        self._num_lbl.setFont(font)
        self._del_btn.setFont(font)
        # Comme le délai commun : en police 9, le champ n'a que 23 pixels de haut,
        # et ses flèches sont trop petites pour se voir
        self._delay_spin.setFont(_get_current_font(11))
        self._delay_minus.setFont(_get_current_font(11))
        self._delay_plus.setFont(_get_current_font(11))
        self._delay_spin.setSuffix(_("dialogs.gif_animated.seconds_suffix"))

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

        self._scroll_step = 0
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(_AUTO_SCROLL_INTERVAL_MS)
        self._scroll_timer.timeout.connect(self._auto_scroll)

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

    def refresh_delays(self):
        for thumb in self._thumb_widgets():
            thumb.show_delay()

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

    def _own_drag(self, event):
        """Vignette de cette fenêtre : le numéro d'une vignette d'une autre
        fenêtre GIF désignerait ici une autre image, ou aucune."""
        source = event.source()
        return (event.mimeData().hasFormat(self.MIME) and isinstance(source, _ThumbWidget)
                and source._dialog is self._dialog)

    def _set_drop_pos(self, insert_idx):
        if insert_idx != self._drop_pos:
            self._drop_pos = insert_idx
            self.update()

    def _follow_drag_with_scroll(self, x_local):
        viewport = self._dialog._scroll_area.viewport()
        x_visible = self.mapTo(viewport, QPoint(x_local, 0)).x()
        self._scroll_step = _auto_scroll_step(x_visible, viewport.width())
        if self._scroll_step and not self._scroll_timer.isActive():
            self._scroll_timer.start()
        elif not self._scroll_step:
            self._scroll_timer.stop()

    def _auto_scroll(self):
        bar = self._dialog._scroll_area.horizontalScrollBar()
        bar.setValue(bar.value() + self._scroll_step)
        self._set_drop_pos(self._insert_index_at(self.mapFromGlobal(QCursor.pos()).x()))

    def dragEnterEvent(self, event):
        if self._own_drag(event):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if not self._own_drag(event):
            return
        x_local = event.position().toPoint().x()
        self._set_drop_pos(self._insert_index_at(x_local))
        self._follow_drag_with_scroll(x_local)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._scroll_timer.stop()
        self._drop_pos = -1
        self.update()

    def dropEvent(self, event):
        self._scroll_timer.stop()
        self._drop_pos = -1
        self.update()
        if not self._own_drag(event):
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
# Création en arrière-plan
# ─────────────────────────────────────────────────────────────────────────────

class _GifWorker(QThread):
    """Assemble le GIF hors du thread de l'interface : un GIF de grandes pages
    figerait sinon les deux panneaux pendant de longues secondes. Ne touche pas
    au document : la nouvelle page est ajoutée par la fenêtre, à la fin."""

    step      = Signal(str, int, int)   # étape (gif_writer.STEP_*), numéro, total
    created   = Signal(object, list)    # nouvelle entrée (None : aucune page lisible), pages écartées
    failed    = Signal(str)             # texte de l'erreur
    cancelled = Signal()

    def __init__(self, pages, settings, name):
        super().__init__()
        self.setObjectName("GifWorker")
        self._pages = pages
        self._settings = settings
        self._name = name
        self._cancelled = threading.Event()

    def run(self):
        # Erreur imprévue : un signal de fin est toujours émis, sinon
        # l'opération ne serait jamais terminée (end_operation) et le document
        # resterait impossible à modifier. Relancée pour le journal des erreurs.
        try:
            self._run()
        except Exception:
            self.cancelled.emit()
            raise

    def _run(self):
        try:
            data, left_out = build_gif(self._pages, **self._settings, on_step=self.step.emit,
                                       should_stop=self._cancelled.is_set)
            entry = None
            if data is not None:
                entry = create_entry(self._name, data, IMAGE_EXTS)
                entry["source_archive"] = "loose"
        except GifCancelled:
            self.cancelled.emit()
            return
        except Exception as e:
            from modules.qt.error_log import log_if_unexpected
            from modules.qt.temp_files import exception_text
            log_if_unexpected(e)
            self.failed.emit(exception_text(e))
            return
        self.created.emit(entry, left_out)


# Workers en cours : sans référence Python, un QThread actif serait détruit.
_active_workers = []


# ─────────────────────────────────────────────────────────────────────────────
# Question après la modification d'un GIF animé
# ─────────────────────────────────────────────────────────────────────────────

class GifOriginalDialog(QuestionWindowMixin, QDialog):
    """Question posée après « Modifier le GIF animé » : garder le GIF d'origine
    ou le remplacer par le GIF modifié. Réponse True = remplacer. La croix et
    Échap le gardent. NON modale."""

    result_signal = Signal(bool)
    _WIDTH = 480

    def __init__(self, parent):
        super().__init__(parent)
        self._setup_window(close_answer=False)
        self.set_window_title("dialogs.gif_animated.original_title")
        self.setFixedWidth(self._WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(12)

        self._msg_lbl = QLabel()
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        self._msg_lbl.setWordWrap(True)
        layout.addWidget(self._msg_lbl)

        btn_row = QHBoxLayout()
        self._keep_btn = QPushButton()
        self._keep_btn.setDefault(True)
        self._keep_btn.clicked.connect(lambda: self._answer(False))
        btn_row.addWidget(self._keep_btn)
        self._replace_btn = QPushButton()
        self._replace_btn.clicked.connect(lambda: self._answer(True))
        btn_row.addWidget(self._replace_btn)
        layout.addLayout(btn_row)

        self._connect_language()
        self._keep_btn.setFocus()

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(11)
        self._msg_lbl.setText(_("dialogs.gif_animated.original_message"))
        self._msg_lbl.setFont(font)
        self._msg_lbl.setStyleSheet(f"color: {theme['text']};")
        # Largeur fixe : sizeHint d'un QLabel à retour à la ligne sous-estime sa
        # hauteur, les dernières lignes seraient coupées
        self._msg_lbl.setMinimumHeight(self._msg_lbl.heightForWidth(self._WIDTH - 40))
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 6px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        for button, key in ((self._keep_btn, "dialogs.gif_animated.keep_original"),
                            (self._replace_btn, "dialogs.gif_animated.replace_original")):
            button.setText(_(key))
            button.setFont(font)
            button.setStyleSheet(btn_style)


def _replace_edited_gif(state, callbacks, original_key, new_key, generation):
    """Réponse « remplacer » à GifOriginalDialog : le GIF modifié prend la place
    et le nom du GIF d'origine, et reprend ses attributs de <Page>. La question
    est non modale : le document a pu changer. Pages retrouvées par leur jeton
    (une page rétablie par Annuler est un nouvel objet). Sans effet si l'une des
    deux manque : le GIF d'origine n'existerait plus sous aucune forme. Rend True
    si le GIF a été remplacé."""
    if state.doc_generation != generation or state.operation_running():
        return False
    pages = {entry_key(e): e for e in state.images_data}
    original, new = pages.get(original_key), pages.get(new_key)
    if original is None or new is None:
        return False

    callbacks["save_state"]()
    from modules.qt.comic_info import sync_pages_in_xml_data, transfer_page_attrs
    transfer_page_attrs(state, original, new)
    new["orig_name"] = original["orig_name"]
    if "source_archive" in original:
        new["source_archive"] = original["source_archive"]
    pages_left = [e for e in state.images_data if e is not new]
    state.images_data[:] = [new if e is original else e for e in pages_left]
    state.selected_indices.clear()
    state.modified = True
    sync_pages_in_xml_data(state)
    callbacks["save_state"]()
    callbacks["render_mosaic"]()
    callbacks["update_button_text"]()
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Dialogue principal
# ─────────────────────────────────────────────────────────────────────────────

class AnimatedGifDialog(AppWindowMixin, QDialog):
    """Fenêtre de création/édition d'un GIF animé."""

    def __init__(self, parent, selected_entries: list, callbacks: dict,
                 gif_extraction=None):
        """gif_extraction : résultat de _extract_gif_frames déjà obtenu pour
        un GIF animé seul (show_animated_gif_dialog), extrait ici sinon."""
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("dialogs.gif_animated.window_title")
        self._callbacks = callbacks or {}
        self._state     = self._callbacks.get("state") or _state_module.state
        self._worker    = None
        self._close_when_stopped = False
        self._status_fn = None

        # ── Extraction des frames si GIF animé unique ─────────────────────
        original_gif_metadata = None
        # GIF modifié : jeton du GIF d'origine (question après la création)
        self._edited_key = None
        self._edited_generation = self._state.doc_generation
        if _is_single_animated_gif(selected_entries):
            gif_entry = selected_entries[0]
            self._edited_key = entry_key(gif_entry)
            original_gif_metadata = {
                "durations": gif_entry.get("gif_durations", []),
                "loop":      gif_entry.get("gif_loop"),
                "disposal":  gif_entry.get("gif_disposal", 2),
                "comment":   gif_entry.get("gif_comment", ""),
                "optimize":  gif_entry.get("gif_optimize", True),
            }
            if gif_extraction is None:
                gif_extraction = _extract_gif_frames(gif_entry)
            selected_entries = gif_extraction[0]

        # Copies des pages. Une page du document qui a déjà son image décodée
        # (visionneuse ouverte) la partagerait avec sa copie, que la fenêtre
        # ferme (_free_gif_images, _update_metadata) : la page garderait une
        # image fermée (« Operation on closed image » dans la visionneuse).
        # La copie décode donc la sienne depuis les bytes. Les images extraites
        # d'un GIF à modifier n'ont pas de bytes : elles gardent leur image.
        # _frames : toutes les images de la fenêtre ; _gif_images : celles qui
        # entreront dans le GIF. Une page supprimée du document quitte la
        # seconde et y revient si Annuler la rétablit (_refresh_from_pages).
        self._frames = []
        # Jeton de la page pris sur la page elle-même : une copie reçoit le sien
        for e in selected_entries:
            copy = e.copy()
            copy["_page_key"] = None if self._edited_key else entry_key(e)
            if copy.get("bytes") is not None:
                copy["img"] = None
            self._frames.append(copy)
        self._gif_images = list(self._frames)

        # ── Valeurs initiales ─────────────────────────────────────────────
        # Un GIF sans bloc de répétition n'est joué qu'une fois, ce que le
        # réglage « Nombre de répétitions » (0 = boucle infinie) ne sait pas
        # représenter : il affiche 1, et tant qu'il n'est pas modifié, le GIF
        # créé reste sans bloc de répétition (_loop_unset).
        self._loop_unset = False
        if original_gif_metadata:
            durations = original_gif_metadata["durations"]
            # Moyenne affichée par le réglage commun : chaque image garde son
            # délai (gif_duration) tant que ce réglage n'est pas modifié
            self._delay_ms  = _average_delay(durations)
            self._loop      = original_gif_metadata["loop"]
            if self._loop is None:
                self._loop_unset = True
                self._loop = 1
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
        self.setWindowFlags(Qt.Window)
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
        self._scroll_area.setFixedHeight(210)
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
        self._delay_spin = _SecondsSpin()
        self._delay_spin.setRange(_DELAY_MIN, _DELAY_MAX)
        self._delay_spin.setSingleStep(_DELAY_ARROW_STEP)
        self._delay_spin.setValue(self._delay_ms)
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
        self._loop_spin = _NumberSpin()
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
        from modules.qt.utils import setup_textedit_context_menu
        setup_textedit_context_menu(self._comment_edit)
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
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        root_layout.addLayout(btn_row)

        # ── Init affichage ────────────────────────────────────────────────
        self._render_thumbs()
        self._connect_language()
        self._create_btn.setFocus()

        # Fenêtre non modale : les pages peuvent changer tant qu'elle est ouverte
        if not self._edited_key:
            listener, state = self._refresh_from_pages, self._state
            state.add_document_listener(listener)
            self.finished.connect(lambda *_args: state.remove_document_listener(listener))
            self.destroyed.connect(lambda *_args: state.remove_document_listener(listener))

    # ── Nettoyage mémoire ─────────────────────────────────────────────────────

    def _free_gif_images(self):
        """Libère les PIL Images et QPixmaps mis en cache dans _frames."""
        for entry in self._frames:
            img = entry.get("img")
            if img is not None:
                try:
                    img.close()
                except Exception:
                    pass  # sans conséquence : la référence est retirée juste après
                entry["img"] = None
            entry.pop("_thumb_px", None)

    def closeEvent(self, event):
        """Libère la mémoire PIL quand la fenêtre est fermée (croix, Échap,
        annulation). Pendant une création, le thread lit ces images : il est
        arrêté d'abord, et la fenêtre se ferme à sa fin réelle."""
        if self._worker is not None:
            self._close_when_stopped = True
            self._worker._cancelled.set()
            event.ignore()
            return
        self._free_gif_images()
        super().closeEvent(event)

    def _on_cancel_clicked(self):
        """Annuler pendant une création l'arrête, la fenêtre reste ouverte."""
        if self._worker is not None:
            self._worker._cancelled.set()
            self._cancel_btn.setEnabled(False)
        else:
            self.close()

    # ── Rendu vignettes ───────────────────────────────────────────────────────

    def _render_thumbs(self):
        """Reconstruit la zone de vignettes depuis self._gif_images."""
        self._thumbs_container.rebuild(self._gif_images, self)
        self._update_metadata()

    # ── Suivi des pages ───────────────────────────────────────────────────────

    def _refresh_from_pages(self):
        """Le document a changé (fenêtre créant un GIF depuis des pages) : une
        page supprimée ou déplacée vers l'autre panneau sort du GIF, une page
        rétablie par Annuler y revient à sa place, une page retouchée entre
        telle qu'elle est maintenant. Sans effet pendant une création : le
        thread lit ces images, et le document ne peut plus changer."""
        if self._edited_key or self._worker is not None:
            return
        pages = {entry_key(e): e for e in self._state.images_data}
        visible, changed = [], False
        for frame in self._frames:
            page = pages.get(frame["_page_key"])
            if page is None:
                continue
            if page.get("bytes") is not frame.get("bytes"):
                self._reload_frame(frame, page)
                changed = True
            visible.append(frame)
        if changed or len(visible) != len(self._gif_images) or any(
                a is not b for a, b in zip(visible, self._gif_images)):
            self._gif_images = visible
            self._create_btn.setEnabled(bool(visible))
            self._render_thumbs()

    @staticmethod
    def _reload_frame(frame, page):
        img = frame.get("img")
        if img is not None:
            img.close()
        key, delay = frame["_page_key"], frame.get("gif_duration")
        frame.clear()
        frame.update(page)
        frame["_page_key"] = key
        frame["gif_duration"] = delay
        frame["img"] = None

    # ── Gestion des images ────────────────────────────────────────────────────

    def _delete_image(self, idx: int):
        if len(self._gif_images) > 1:
            item = self._gif_images.pop(idx)
            self._frames = [f for f in self._frames if f is not item]
            self._render_thumbs()

    def _move_image(self, src: int, dst: int):
        item = self._gif_images.pop(src)
        self._gif_images.insert(dst, item)
        # Même place parmi toutes les images : une page absente du document
        # retrouvera la sienne si Annuler la rétablit
        self._frames = [f for f in self._frames if f is not item]
        following = self._gif_images[dst + 1] if dst + 1 < len(self._gif_images) else None
        position = (next(i for i, f in enumerate(self._frames) if f is following)
                    if following is not None else len(self._frames))
        self._frames.insert(position, item)
        self._render_thumbs()

    # ── Délai / FPS ───────────────────────────────────────────────────────────

    def _decrease_delay(self):
        self._delay_spin.setValue(max(_DELAY_MIN, self._delay_spin.value() - _DELAY_BUTTON_STEP))

    def _increase_delay(self):
        self._delay_spin.setValue(min(_DELAY_MAX, self._delay_spin.value() + _DELAY_BUTTON_STEP))

    def _on_delay_changed(self, val: int):
        """Réglage commun modifié : sa valeur vaut pour toutes les images, y
        compris une page absente du document que Annuler rétablirait."""
        for frame in self._frames:
            frame["gif_duration"] = None
        self._thumbs_container.refresh_delays()
        self._update_fps_label(val)
        self._update_metadata()

    def _update_fps_label(self, delay_ms: int):
        separator = decimal_separator()
        if delay_ms >= 1000:
            seconds = _seconds_text(delay_ms, separator)
            self._fps_lbl.setText(_("dialogs.gif_animated.fps_slow", seconds=seconds))
        else:
            fps = 1000.0 / delay_ms if delay_ms > 0 else 0
            self._fps_lbl.setText(_("dialogs.gif_animated.fps_normal",
                                    fps=decimal_text(fps, 2, separator)))

    def _update_metadata(self):
        if not self._gif_images:
            self._meta_lbl.setText(_("dialogs.gif_animated.no_images"))
            return
        width, height = self._first_image_size()
        num_frames  = len(self._gif_images)
        total_dur, fps = _delay_stats(
            [frame_delay(frame, self._delay_spin.value()) for frame in self._gif_images])
        est_size    =num_frames * width * height * 0.5 / 1024
        separator   = decimal_separator()
        meta  = _("dialogs.gif_animated.metadata_dimensions", width=width, height=height) + "\n"
        meta += _("dialogs.gif_animated.metadata_frames",    count=num_frames) + "\n"
        meta += _("dialogs.gif_animated.metadata_duration",
                  duration=decimal_text(total_dur, 2, separator)) + "\n"
        meta += _("dialogs.gif_animated.metadata_fps",       fps=decimal_text(fps, 2, separator)) + "\n"
        meta += _("dialogs.gif_animated.metadata_palette") + "\n"
        meta += _("dialogs.gif_animated.metadata_size",      size=decimal_text(est_size, 1, separator))
        self._meta_lbl.setText(meta)

    def _first_image_size(self):
        """(largeur, hauteur) de la première image, sans la décoder ni la
        fermer : le thread de création peut la lire au même moment."""
        first = self._gif_images[0]
        img = first.get("img")
        if img is not None:
            return img.size
        try:
            return page_size(first["bytes"]) if first.get("bytes") else (0, 0)
        except Exception:
            return 0, 0  # repli : page illisible, signalée avant la création

    # ── Création du GIF ───────────────────────────────────────────────────────

    def _gif_settings(self):
        """Réglages de gif_writer.build_gif, lus dans la fenêtre."""
        loop = self._loop_spin.value()
        return {
            "delay": self._delay_spin.value(),
            "loop": None if self._loop_unset and loop == self._loop else loop,
            "disposal": next((rb._disposal_val for rb in self._disposal_radios if rb.isChecked()), 2),
            "optimize": self._optimize_check.isChecked(),
            "comment": self._comment_edit.toPlainText().strip(),
        }

    def _create_animated_gif(self):
        # Opération (rotation…) lancée pendant que la fenêtre était ouverte :
        # sans effet tant qu'elle tourne, la fenêtre reste ouverte
        # (panel_widget.BUSY_BLOCKED_COMMANDS).
        if self._worker is not None or self._state.operation_running():
            return
        self._refresh_from_pages()
        if not self._gif_images:
            dlg = MsgDialog(self,
                            "messages.warnings.no_images_for_gif.title",
                            "messages.warnings.no_images_for_gif.message")
            dlg.show_nonmodal()
            return
        unreadable, several = pages_left_out(self._gif_images)
        if not unreadable and not several:
            self._start_creation()
            return

        def listed(pages):
            names = [p.get("orig_name", "") for p in pages[:_LEFT_OUT_LISTED]]
            return "\n".join(names + (["…"] if len(pages) > _LEFT_OUT_LISTED else []))

        base = "dialogs.gif_animated."
        if unreadable and several:
            key, kwargs = "left_out_both", {"unreadable": listed(unreadable), "several": listed(several)}
        elif unreadable:
            key, kwargs = "left_out_unreadable", {"names": listed(unreadable)}
        else:
            key, kwargs = "left_out_several", {"names": listed(several)}
        # Noms de pages venus de l'archive : texte brut
        ConfirmDialog(self, base + "left_out_title", base + key, message_kwargs=kwargs,
                      ok_key=base + "create_anyway", plain_text=True).ask_async(
            lambda ok: self._start_creation() if ok else None)

    def _set_creating(self, creating):
        """Fenêtre figée pendant une création, Annuler excepté : le thread lit
        les images et les réglages sont déjà pris."""
        widgets = [self._scroll_area, self._delay_spin, self._minus_btn, self._plus_btn,
                   self._loop_spin, self._optimize_check, self._comment_edit,
                   *self._disposal_radios]
        for widget in widgets:
            widget.setEnabled(not creating)
        self._create_btn.setEnabled(not creating and bool(self._gif_images))
        self._cancel_btn.setEnabled(True)
        self._progress_lbl.setVisible(creating)
        if not creating:
            self._status_fn = None

    def _on_gif_step(self, step, number, total):
        if step == STEP_READING:
            self._status_fn = lambda: _("dialogs.gif_animated.creating_progress",
                                        current=number, total=total)
        elif step == STEP_FRAME:
            self._status_fn = lambda: _("dialogs.gif_animated.creating_normalizing",
                                        current=number, total=total)
        else:
            self._status_fn = lambda: _("dialogs.gif_animated.creating_saving")
        self._progress_lbl.setText(self._status_fn())

    def _start_creation(self):
        """Lance la création en arrière-plan. Les commandes qui modifient le
        document sont grisées pendant ce temps (begin_operation) ; à la
        fermeture du fichier, elle s'arrête, et un résultat tardif est abandonné."""
        state = self._state
        if self._worker is not None or state.operation_running():
            return
        update_button_text = self._callbacks.get("update_button_text", lambda: None)
        canvas = self._callbacks.get("canvas")
        generation = state.doc_generation
        worker = _GifWorker(list(self._gif_images), self._gif_settings(),
                            next_gif_name(state.images_data))
        released = [False]

        def _cancel():
            worker._cancelled.set()

        def _release_worker():
            # Une seule fois, y compris depuis le finally d'une fin interrompue
            if released[0]:
                return
            released[0] = True
            if canvas is not None:
                unregister_cancel_on_close(canvas, _cancel)
            state.end_operation(worker)
            self._worker = None
            if worker in _active_workers:
                _active_workers.remove(worker)
            dispose_qthread(worker)
            update_button_text()

        def _after_stop():
            """Création terminée sans page ajoutée : la fenêtre reste ouverte
            pour un autre essai, sauf si sa fermeture était demandée."""
            self._set_creating(False)
            if self._close_when_stopped:
                self.close()
            else:
                self._refresh_from_pages()

        def on_created(entry, left_out):
            # Arrêt demandé juste après la dernière image, ou document fermé ou
            # remplacé : ce GIF ne doit pas arriver dans le document
            dropped = worker._cancelled.is_set() or state.doc_generation != generation
            try:
                if entry is not None and not dropped:
                    self._add_created_gif(entry)
            finally:
                _release_worker()
            if dropped:
                _after_stop()
            elif entry is None:
                MsgDialog(self,
                          "messages.warnings.no_valid_images.title",
                          "messages.warnings.no_valid_images.message").show_nonmodal()
                _after_stop()
            else:
                self._after_gif_added(entry, left_out)

        def on_failed(message):
            _release_worker()
            if state.doc_generation == generation:
                # Texte recalculé à l'affichage (changement de langue)
                ErrorDialog(self, "messages.errors.gif_creation_failed.title",
                            lambda m=message: _("messages.errors.gif_creation_failed.message", error=m),
                            ).show_nonmodal()
            _after_stop()

        def on_cancelled():
            _release_worker()
            _after_stop()

        self._worker = worker
        _active_workers.append(worker)
        if canvas is not None:
            register_cancel_on_close(canvas, _cancel)
        state.begin_operation(worker)
        update_button_text()
        self._set_creating(True)
        worker.step.connect(self._on_gif_step)
        worker.created.connect(on_created)
        worker.failed.connect(on_failed)
        worker.cancelled.connect(on_cancelled)
        worker.start()

    def _add_created_gif(self, entry):
        """Nouvelle page à la fin du document, entre deux états d'historique."""
        save_state = self._callbacks.get("save_state", lambda: None)
        save_state()
        self._state.images_data.append(entry)
        self._state.modified = True
        from modules.qt.comic_info import sync_pages_in_xml_data
        sync_pages_in_xml_data(self._state)
        save_state()
        self._callbacks.get("render_mosaic", lambda: None)()
        self._callbacks.get("update_button_text", lambda: None)()

    def _after_gif_added(self, entry, left_out):
        """Fenêtre fermée ; la suite s'affiche sur le panneau, jamais sur
        elle : elle disparaîtrait avec la fenêtre."""
        panel = self._callbacks.get("parent")
        state, callbacks = self._state, self._callbacks
        edited_key, generation = self._edited_key, self._edited_generation
        self._set_creating(False)
        self.close()
        if left_out:
            from modules.qt.archive_loader import _MessageDialog
            names = [p.get("orig_name", "") for p in left_out[:20]]
            _MessageDialog(panel, "messages.warnings.files_ignored.title",
                           lambda n=names: "\n".join(
                               _("dialogs.gif_animated.page_left_out", name=x) for x in n),
                           ).show_nonmodal()
        if edited_key:
            new_key = entry_key(entry)
            GifOriginalDialog(panel).ask_async(
                lambda replace: _replace_edited_gif(state, callbacks, edited_key, new_key, generation)
                if replace else None)

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
            f"             border: 1px solid #aaaaaa; padding: 2px 4px; }} "
            f"QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }} "
            f"QTextEdit {{ background: {theme['entry_bg']}; color: {theme['text']}; "
            f"             border: 1px solid #aaaaaa; }} "
            f"QScrollArea {{ background: {theme['toolbar_bg']}; border: 1px solid {theme['separator']}; }}"
        )

        self._images_title.setText(_("dialogs.gif_animated.images_title"))
        self._images_title.setFont(font_lrg)

        self._params_title.setText(_("dialogs.gif_animated.params_title"))
        self._params_title.setFont(font_lrg)

        self._delay_title.setText(_("dialogs.gif_animated.frame_delay_all_label"))
        self._delay_title.setFont(_get_current_font(11, bold=True))
        self._delay_spin.setFont(font)
        self._delay_spin.setSuffix(_("dialogs.gif_animated.seconds_suffix"))
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
        if self._status_fn is not None:
            self._progress_lbl.setText(self._status_fn())

        self._create_btn.setText(_("buttons.create_gif"))
        self._create_btn.setFont(font)
        self._create_btn.setStyleSheet(btn_sty)

        self._cancel_btn.setText(_("buttons.cancel"))
        self._cancel_btn.setFont(font)
        self._cancel_btn.setStyleSheet(btn_sty)

        self._thumbs_container.apply_theme_to_thumbs()


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée public
# ─────────────────────────────────────────────────────────────────────────────

def _is_single_animated_gif(selected_entries):
    """Modification d'un GIF animé existant (une seule entrée, GIF animé) :
    ses images sont extraites pour être éditées."""
    return len(selected_entries) == 1 and bool(selected_entries[0].get("is_animated_gif"))


def _extract_gif_frames(gif_entry):
    """(entrées des images du GIF animé, True si la lecture s'est arrêtée
    avant la dernière image). Un GIF se lit image par image, dans l'ordre :
    une image qui ne se décode pas (données abîmées, mémoire insuffisante
    pour tout convertir en RGBA) arrête la lecture, et les suivantes ne
    peuvent pas être atteintes — chacune ne contient souvent que ce qui
    change par rapport à la précédente. Les images lues sont gardées ; l'arrêt
    est signalé par show_animated_gif_dialog, jamais avalé : le GIF créé
    serait sinon tronqué sans que l'utilisateur le sache.

    Images en RGB si aucune n'a de transparence : Pillow réduit les couleurs
    d'une image RGBA moins fidèlement, et le GIF recréé aurait perdu des
    couleurs sans que l'utilisateur ait rien changé."""
    from PIL import ImageSequence as _ImageSequence
    durations = gif_entry.get("gif_durations", [])
    base_name = os.path.splitext(gif_entry["orig_name"])[0]
    frames = []

    def _image(raw_frame):
        rgba = raw_frame.copy().convert("RGBA")
        return rgba if rgba.getchannel("A").getextrema()[0] < 255 else rgba.convert("RGB")

    def _same_mode_for_all():
        if any(f["img"].mode == "RGBA" for f in frames):
            for f in frames:
                f["img"] = f["img"].convert("RGBA")

    try:
        # Ouvre le GIF une seule fois et itère les frames en séquence (évite O(N²))
        with Image.open(io.BytesIO(gif_entry["bytes"])) as gif_img:
            for i, raw_frame in enumerate(_ImageSequence.Iterator(gif_img)):
                frames.append({
                    # Délai propre à cette image : il la suit si la liste
                    # est réordonnée
                    "gif_duration":     durations[i] if i < len(durations) else None,
                    "orig_name":         f"{base_name}_frame_{i:03d}.png",
                    "img":               _image(raw_frame),
                    "extension":         ".png",
                    "is_image":          True,
                    "is_dir":            False,
                    "is_corrupted":      False,
                    "corruption_reason": None,
                    "is_animated_gif":   False,
                    "from_gif_frame":    True,
                    "gif_source":        gif_entry["orig_name"],
                })
    except Exception:
        _same_mode_for_all()
        return frames, True
    _same_mode_for_all()
    return frames, False


def show_animated_gif_dialog(selected_entries: list, callbacks: dict = None):
    """Affiche la fenêtre de paramétrage pour créer un GIF animé.

    Modification d'un GIF animé : aucune image lisible → la fenêtre ne
    s'ouvre pas (message) ; lecture arrêtée avant la fin → elle s'ouvre avec
    les images lues, et un message précise lesquelles manquent.

    callbacks : dict avec les clés :
        - save_state          : callable
        - render_mosaic       : callable
        - update_button_text  : callable
        - state               : AppState du panneau appelant (optionnel)
        - parent              : QWidget parent (optionnel)
    """
    parent = (callbacks or {}).get("parent")
    gif_extraction = None
    if _is_single_animated_gif(selected_entries):
        gif_extraction = _extract_gif_frames(selected_entries[0])
        if not gif_extraction[0]:
            MsgDialog(parent,
                      "dialogs.gif_animated.read_error_title",
                      "dialogs.gif_animated.read_error_none").show_nonmodal()
            return
    dialog = AnimatedGifDialog(parent, selected_entries, callbacks or {}, gif_extraction)
    dialog.show_nonmodal()
    if gif_extraction and gif_extraction[1]:
        total = selected_entries[0].get("gif_frame_count") or "?"
        MsgDialog(dialog,
                  "dialogs.gif_animated.read_error_title",
                  "dialogs.gif_animated.read_error_partial",
                  {"read": len(gif_extraction[0]), "total": total}).show_nonmodal()
