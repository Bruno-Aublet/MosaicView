"""
batch_dialogs_qt.py — Conversion par lot (CBR→CBZ, PDF→CBZ, IMG→CBZ) pour PySide6.
Reproduit exactement modules/batch_dialogs.py (tkinter), fenêtres comprises.
Toutes les fenêtres supportent le changement de langue à la volée via language_signal.
"""

import os
import io
import gc
import threading
import zipfile

from PIL import Image

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QProgressBar, QFrame, QSizePolicy, QFileDialog,
    QApplication, QRadioButton, QButtonGroup,
)
from PySide6.QtCore import Qt, QMetaObject, Q_ARG, QObject, Signal
from PySide6.QtGui import QPixmap, QImage, QDesktopServices, QCursor
from PySide6.QtCore import QUrl

from modules.qt.localization import _, _wt
from modules.qt.utils import format_file_size
from modules.qt.state import get_current_theme
from modules.qt.config_manager import get_config_manager
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.dialogs_qt import ErrorDialog, InfoDialog

# Import conditionnel rarfile
try:
    import rarfile
except ImportError:
    rarfile = None

# Import conditionnel PyMuPDF
try:
    import fitz
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

# Extensions d'images supportées
image_exts = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff', '.tif', '.ico', '.jfif', '.pjpeg', '.pjp')


# ═══════════════════════════════════════════════════════════════════════════════
# Signal thread → UI (pour mise à jour thread-safe)
# ═══════════════════════════════════════════════════════════════════════════════

class _ThreadSignals(QObject):
    update_filename   = Signal(str)
    update_progress   = Signal(str)
    update_page_bar   = Signal(float, str)   # valeur 0-100, texte
    update_thumb      = Signal(object)       # QPixmap ou None
    conversion_done   = Signal()

    # IMG n'a pas de barre de pages
    update_progress_img = Signal(str)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _open_path(path):
    try:
        if os.name == "nt":
            os.startfile(path)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    except Exception as e:
        print(f"Erreur ouverture : {e}")


def _pil_to_qpixmap(img, w, h, callbacks):
    """Crée un QPixmap vignette depuis une PIL Image."""
    try:
        thumb = callbacks["create_centered_thumbnail"](img, w, h)
        thumb = thumb.convert("RGBA")
        data  = thumb.tobytes("raw", "RGBA")
        qimg  = QImage(data, thumb.width, thumb.height, QImage.Format_RGBA8888)
        return QPixmap.fromImage(qimg)
    except Exception:
        return None


def _add_dir_links(layout, data):
    """Ajoute des liens cliquables vers les dossiers dans le layout."""
    dirs = data.get("directories") or [data.get("directory", "")]
    for d in dirs:
        display = d if len(d) <= 60 else "..." + d[-57:]
        lbl = QLabel(f'<a href="file:///{d}">{display}</a>')
        lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        lbl.setCursor(QCursor(Qt.PointingHandCursor))
        lbl.setStyleSheet("color: #4A9EFF;")
        lbl.setWordWrap(True)
        lbl.linkActivated.connect(lambda _, p=d: _open_path(p))
        lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl)


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


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de confirmation (commun aux 3 flux)
# ═══════════════════════════════════════════════════════════════════════════════

class _ConfirmDialog(QDialog):
    """
    Fenêtre de confirmation avant le lancement d'une conversion batch.
    Affiche : message (count/directory), taille totale, checkbox suppression permanente,
    boutons Démarrer / Annuler.
    """

    def __init__(self, parent, title_key, msg_key, count, directory,
                 total_size, checkbox_key, tooltip_key, start_key, callbacks):
        super().__init__(parent)
        self._title_key    = title_key
        self._msg_key      = msg_key
        self._count        = count
        self._directory    = directory
        self._total_size   = total_size
        self._checkbox_key = checkbox_key
        self._start_key    = start_key
        self._callbacks    = callbacks
        self.confirmed     = False

        self.setModal(True)
        self.setFixedWidth(500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        self._msg_lbl  = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg_lbl)

        self._size_lbl = QLabel()
        self._size_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._size_lbl)

        layout.addSpacing(6)

        self._chk = QCheckBox()
        layout.addWidget(self._chk, alignment=Qt.AlignCenter)

        layout.addSpacing(6)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._start_btn  = QPushButton()
        self._start_btn.setFixedWidth(110)
        self._start_btn.clicked.connect(self._on_start)
        self._start_btn.setDefault(True)
        btn_row.addWidget(self._start_btn)
        self._cancel_btn = QPushButton()
        self._cancel_btn.setFixedWidth(110)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.rejected.connect(self._on_cancel)
        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._start_btn.setFocus()

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        display_dir = self._directory if len(self._directory) <= 60 else "..." + self._directory[-57:]
        self.setWindowTitle(_wt(self._title_key))
        self._msg_lbl.setText(_(self._msg_key).format(count=self._count, directory=display_dir))
        self._msg_lbl.setFont(font)
        self._size_lbl.setText(f"({format_file_size(self._total_size)})")
        self._size_lbl.setFont(font)
        self._chk.setText(_(self._checkbox_key))
        self._chk.setFont(font)
        self._start_btn.setText(_(self._start_key))
        self._start_btn.setFont(font)
        self._start_btn.setStyleSheet(btn_style)
        self._cancel_btn.setText(_("buttons.cancel"))
        self._cancel_btn.setFont(font)
        self._cancel_btn.setStyleSheet(btn_style)

    @property
    def permanent_delete(self):
        return self._chk.isChecked()

    def _on_start(self):
        self.confirmed = True
        self.accept()

    def _on_cancel(self):
        self.confirmed = False
        self.accept()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de progression (commun aux 3 flux)
# ═══════════════════════════════════════════════════════════════════════════════

class _ProgressDialog(QDialog):
    """
    Fenêtre de progression pendant la conversion.
    Affiche vignette, nom du fichier, barre de pages (optionnelle), progression globale.
    """

    def __init__(self, parent, title_key, has_page_bar=True):
        super().__init__(parent)
        self._title_key   = title_key
        self._has_page_bar = has_page_bar
        self._running     = True

        self.setModal(True)
        self.setFixedWidth(450)
        self.setFixedHeight(400 if has_page_bar else 350)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(8)

        # Vignette
        self._thumb_lbl = QLabel()
        self._thumb_lbl.setFixedSize(150, 210)
        self._thumb_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._thumb_lbl, alignment=Qt.AlignCenter)

        # Nom du fichier
        self._filename_lbl = QLabel("")
        self._filename_lbl.setWordWrap(True)
        self._filename_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._filename_lbl)

        # Barre progression pages (CBR + PDF seulement)
        if has_page_bar:
            self._page_bar = QProgressBar()
            self._page_bar.setRange(0, 100)
            self._page_bar.setValue(0)
            self._page_bar.setFixedWidth(400)
            layout.addWidget(self._page_bar, alignment=Qt.AlignCenter)

            self._page_lbl = QLabel("")
            self._page_lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(self._page_lbl)

        # Progression globale
        self._progress_lbl = QLabel("")
        self._progress_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._progress_lbl)

        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        self._filename_lbl.setFont(font)
        self._progress_lbl.setFont(font)
        self.setWindowTitle(_wt(self._title_key))

    def set_filename(self, name):
        self._filename_lbl.setText(name)

    def set_progress(self, text):
        self._progress_lbl.setText(text)

    def set_page_progress(self, value, text):
        if self._has_page_bar:
            self._page_bar.setValue(int(value))
            self._page_lbl.setText(text)

    def set_thumbnail(self, pixmap):
        if pixmap:
            self._thumb_lbl.setPixmap(pixmap.scaled(150, 210, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self._thumb_lbl.clear()

    def mark_done(self):
        self._running = False

    def closeEvent(self, event):
        if self._running:
            event.ignore()
        else:
            event.accept()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de résumé CBR
# ═══════════════════════════════════════════════════════════════════════════════

class _CbrSummaryDialog(QDialog):
    def __init__(self, parent, data):
        super().__init__(parent)
        self._data = data
        self.setModal(True)
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        top = QVBoxLayout()
        top.setSpacing(0)
        top.setContentsMargins(0, 0, 0, 0)
        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        top.addWidget(self._msg_lbl)
        _add_dir_links(top, data)
        layout.addLayout(top)

        if data.get("renamed_count", 0) > 0:
            self._renamed_lbl = QLabel()
            self._renamed_lbl.setWordWrap(True)
            self._renamed_lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(self._renamed_lbl)
        else:
            self._renamed_lbl = None

        # Lien erreurs
        self._error_lbl = None
        if data.get("has_errors"):
            log_path = data.get("log_path")
            if log_path:
                self._error_lbl = QLabel()
                self._error_lbl.setWordWrap(True)
                self._error_lbl.setAlignment(Qt.AlignCenter)
                self._error_lbl.setStyleSheet("color: #4A9EFF;")
                self._error_lbl.setCursor(QCursor(Qt.PointingHandCursor))
                self._error_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
                self._error_lbl.linkActivated.connect(lambda _, p=log_path: _open_path(p))
                layout.addWidget(self._error_lbl)
            else:
                self._error_lbl = QLabel()
                self._error_lbl.setWordWrap(True)
                self._error_lbl.setAlignment(Qt.AlignCenter)
                self._error_lbl.setStyleSheet("color: #4A9EFF;")
                layout.addWidget(self._error_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok_btn = QPushButton()
        self._ok_btn.setFixedWidth(100)
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._ok_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._ok_btn.setFocus()

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        data = self._data
        self.setWindowTitle(_wt("dialogs.batch_cbr.complete_title"))
        if data.get("has_errors") or data.get("renamed_count", 0) > 0:
            self._msg_lbl.setText(_("dialogs.batch_cbr.complete_message_errors").format(
                count=data["converted_count"], total=data["total"]))
        else:
            self._msg_lbl.setText(_("dialogs.batch_cbr.complete_message").format(
                count=data["converted_count"]))
        self._msg_lbl.setFont(font)

        if self._renamed_lbl is not None:
            self._renamed_lbl.setText(_("dialogs.batch_cbr.renamed_count").format(
                count=data["renamed_count"]))
            self._renamed_lbl.setFont(font)

        if self._error_lbl is not None:
            log_path = data.get("log_path")
            error_text = _("dialogs.batch_cbr.errors_count").format(count=data["errors_count"])
            if log_path:
                see_log = _("dialogs.see_log")
                self._error_lbl.setText(f'<a href="file:///{log_path}">{error_text} — {see_log}</a>')
            else:
                self._error_lbl.setText(error_text)
            self._error_lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de résumé PDF
# ═══════════════════════════════════════════════════════════════════════════════

class _PdfSummaryDialog(QDialog):
    def __init__(self, parent, data, callbacks):
        super().__init__(parent)
        self._data      = data
        self._callbacks = callbacks
        self.setModal(True)
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        top = QVBoxLayout()
        top.setSpacing(0)
        top.setContentsMargins(0, 0, 0, 0)
        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        top.addWidget(self._msg_lbl)
        _add_dir_links(top, data)
        layout.addLayout(top)

        self._error_lbl = None
        if data.get("has_errors"):
            log_path = data.get("log_path")
            self._error_lbl = QLabel()
            self._error_lbl.setWordWrap(True)
            self._error_lbl.setAlignment(Qt.AlignCenter)
            self._error_lbl.setStyleSheet("color: #4A9EFF;")
            if log_path:
                self._error_lbl.setCursor(QCursor(Qt.PointingHandCursor))
                self._error_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
                self._error_lbl.linkActivated.connect(lambda _, p=log_path: _open_path(p))
            layout.addWidget(self._error_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok_btn = QPushButton()
        self._ok_btn.setFixedWidth(100)
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(self._ok_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._ok_btn.setFocus()

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        data = self._data
        self.setWindowTitle(_wt("dialogs.batch_pdf.complete_title"))
        if data.get("has_errors"):
            self._msg_lbl.setText(_("dialogs.batch_pdf.complete_message_errors").format(
                count=data["converted_count"], total=data["total"]))
        else:
            self._msg_lbl.setText(_("dialogs.batch_pdf.complete_message").format(
                count=data["converted_count"]))
        self._msg_lbl.setFont(font)

        if self._error_lbl is not None:
            log_path = data.get("log_path")
            error_text = _("dialogs.batch_pdf.errors_count").format(count=data["errors_count"])
            if log_path:
                see_log = _("dialogs.see_log")
                self._error_lbl.setText(f'<a href="file:///{log_path}">{error_text} — {see_log}</a>')
            else:
                self._error_lbl.setText(error_text)
            self._error_lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)

    def _on_ok(self):
        self.accept()
        # Après fermeture : proposer déverrouillage si owner-protected PDFs
        owner_protected = self._data.get("owner_protected_pdfs", [])
        if owner_protected:
            try:
                from modules.qt.pdf_unlock_qt import show_batch_pdf_unlock_dialog
                show_batch_pdf_unlock_dialog(
                    owner_protected, self.parent(),
                    is_permanent=self._data.get("is_permanent", False),
                    safe_delete_file=self._data.get("safe_delete_file"),
                )
            except Exception as e:
                print(f"Erreur pdf_unlock_qt : {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de résumé IMG
# ═══════════════════════════════════════════════════════════════════════════════

class _ImgSummaryDialog(QDialog):
    def __init__(self, parent, data):
        super().__init__(parent)
        self._data = data
        self.setModal(True)
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        top = QVBoxLayout()
        top.setSpacing(0)
        top.setContentsMargins(0, 0, 0, 0)
        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        top.addWidget(self._msg_lbl)
        _add_dir_links(top, data)
        layout.addLayout(top)

        # Labels par extension (statiques — on recrée si langue change)
        self._ext_frame = QVBoxLayout()
        layout.addLayout(self._ext_frame)
        self._ext_labels = []

        self._error_lbl = None
        if data.get("has_errors"):
            log_path = data.get("log_path")
            self._error_lbl = QLabel()
            self._error_lbl.setWordWrap(True)
            self._error_lbl.setAlignment(Qt.AlignCenter)
            self._error_lbl.setStyleSheet("color: #4A9EFF;")
            if log_path:
                self._error_lbl.setCursor(QCursor(Qt.PointingHandCursor))
                self._error_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
                self._error_lbl.linkActivated.connect(lambda _, p=log_path: _open_path(p))
            layout.addWidget(self._error_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok_btn = QPushButton()
        self._ok_btn.setFixedWidth(100)
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._ok_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._ok_btn.setFocus()

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        data = self._data
        self.setWindowTitle(_wt("dialogs.batch_img.complete_title"))
        if data.get("has_errors"):
            self._msg_lbl.setText(_("dialogs.batch_img.complete_message_errors").format(
                count=data["converted_count"], total=data["total"]))
        else:
            self._msg_lbl.setText(_("dialogs.batch_img.complete_message").format(
                count=data["converted_count"]))
        self._msg_lbl.setFont(font)

        # Mise à jour labels par extension
        converted_by_ext = data.get("converted_by_ext", {})
        for lbl in self._ext_labels:
            lbl.deleteLater()
        self._ext_labels.clear()
        for ext_key in sorted(converted_by_ext.keys()):
            ext_display = ext_key.lstrip('.').upper()
            line = _("dialogs.batch_img.converted_by_ext").format(ext=ext_display, count=converted_by_ext[ext_key])
            lbl = QLabel(line)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setFont(font)
            self._ext_frame.addWidget(lbl, alignment=Qt.AlignCenter)
            self._ext_labels.append(lbl)

        if self._error_lbl is not None:
            log_path = data.get("log_path")
            error_text = _("dialogs.batch_img.errors_count").format(count=data["errors_count"])
            if log_path:
                see_log = _("dialogs.see_log")
                self._error_lbl.setText(f'<a href="file:///{log_path}">{error_text} — {see_log}</a>')
            else:
                self._error_lbl.setText(error_text)
            self._error_lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)


# ═══════════════════════════════════════════════════════════════════════════════
# Fonctions publiques — CBR→CBZ
# ═══════════════════════════════════════════════════════════════════════════════

def batch_convert_cbr_to_cbz(parent, callbacks, directory=None):
    """Lance la conversion batch CBR→CBZ. Équivalent de batch_dialogs.batch_convert_cbr_to_cbz."""
    if rarfile is None:
        ErrorDialog(parent, _("dialogs.batch_cbr.no_cbr_title"),
                    _("dialogs.batch_cbr.rarfile_unavailable")).exec()
        return

    if directory is None:
        cfg = get_config_manager()
        directory = QFileDialog.getExistingDirectory(
            parent, _("dialogs.batch_cbr.select_directory_title"),
            cfg.get('last_open_dir', ""))
        if directory:
            cfg.set('last_open_dir', directory)
    if not directory:
        return

    cbr_files = []
    for dirpath, _subdirs, filenames in os.walk(directory):
        for fn in filenames:
            if fn.lower().endswith('.cbr'):
                cbr_files.append(os.path.join(dirpath, fn))
    cbr_files.sort(key=lambda f: callbacks['natural_sort_key'](os.path.basename(f).lower()))

    if not cbr_files:
        InfoDialog(parent, _("dialogs.batch_cbr.no_cbr_title"),
                   _("dialogs.batch_cbr.no_cbr_message").format(directory=directory)).exec()
        return

    batch_convert_cbr_to_cbz_confirm(parent, cbr_files, directory, callbacks)


def batch_convert_cbr_to_cbz_confirm(parent, cbr_files, directory, callbacks, directories=None):
    """Fenêtre de confirmation + progression + résumé CBR→CBZ."""
    cbr_files  = [os.path.normpath(f) for f in cbr_files]
    directory  = os.path.normpath(directory) if directory else directory
    if directories:
        directories = [os.path.normpath(d) for d in directories]
    total_size = sum(os.path.getsize(f) for f in cbr_files)

    dlg = _ConfirmDialog(
        parent,
        title_key    = "dialogs.batch_cbr.confirm_title",
        msg_key      = "dialogs.batch_cbr.confirm_message",
        count        = len(cbr_files),
        directory    = directory,
        total_size   = total_size,
        checkbox_key = "dialogs.batch_cbr.checkbox_permanent_delete",
        tooltip_key  = "tooltip.batch_cbr_permanent_delete",
        start_key    = "dialogs.batch_cbr.start_button",
        callbacks    = callbacks,
    )
    dlg.exec()
    if not dlg.confirmed:
        return

    is_permanent = dlg.permanent_delete

    prog = _ProgressDialog(parent, "dialogs.batch_cbr.converting_title", has_page_bar=True)
    prog.show()
    QApplication.processEvents()

    signals          = _ThreadSignals()
    conversion_errors = []
    converted_count  = [0]
    renamed_count    = [0]

    signals.update_filename.connect(prog.set_filename)
    signals.update_progress.connect(prog.set_progress)
    signals.update_page_bar.connect(lambda v, t: prog.set_page_progress(v, t))
    signals.update_thumb.connect(prog.set_thumbnail)

    def do_conversion():
        total = len(cbr_files)
        for idx, cbr_path in enumerate(cbr_files):
            basename = os.path.basename(cbr_path)
            signals.update_filename.emit(basename)
            signals.update_progress.emit(
                _("dialogs.batch_cbr.converting_progress").format(current=idx + 1, total=total))
            signals.update_page_bar.emit(0.0, "")

            # Vignette
            try:
                with rarfile.RarFile(cbr_path, 'r') as arc:
                    names = sorted(
                        [f for f in arc.namelist() if not f.endswith('/') and f.lower().endswith(image_exts)],
                        key=lambda f: callbacks['natural_sort_key'](os.path.basename(f).lower())
                    )
                    if names:
                        data = arc.read(names[0])
                        img  = Image.open(io.BytesIO(data))
                        px   = _pil_to_qpixmap(img, 150, 210, callbacks)
                        signals.update_thumb.emit(px)
                        img = None
            except Exception:
                signals.update_thumb.emit(None)

            try:
                with rarfile.RarFile(cbr_path, 'r') as archive:
                    all_files   = sorted(
                        [f for f in archive.namelist() if not f.endswith('/')],
                        key=lambda f: callbacks['natural_sort_key'](os.path.basename(f).lower())
                    )
                    total_pages = len(all_files)
                    if total_pages == 0:
                        conversion_errors.append(f"{basename}: archive vide")
                        continue

                    base_path, _ext = os.path.splitext(cbr_path)
                    cbz_path = base_path + ".cbz"
                    if os.path.exists(cbz_path):
                        c = 1
                        while os.path.exists(f"{base_path} ({c}).cbz"):
                            c += 1
                        cbz_path = f"{base_path} ({c}).cbz"

                    with zipfile.ZipFile(cbz_path, 'w') as cbz:
                        for page_num, file_name in enumerate(all_files):
                            try:
                                raw = archive.read(file_name)
                                try:
                                    tmp = Image.open(io.BytesIO(raw))
                                    if tmp.mode in ("CMYK", "YCbCr", "I", "F"):
                                        tmp = tmp.convert("RGB")
                                        buf = io.BytesIO()
                                        ext_l = os.path.splitext(file_name)[1].lower()
                                        fmt_map = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG",
                                                   ".webp": "WEBP", ".bmp": "BMP", ".tiff": "TIFF",
                                                   ".tif": "TIFF", ".gif": "GIF"}
                                        out_fmt = fmt_map.get(ext_l, "JPEG")
                                        tmp.save(buf, format=out_fmt,
                                                 **({"quality": 100, "optimize": True} if out_fmt == "JPEG" else {}))
                                        raw = buf.getvalue()
                                    tmp = None
                                except Exception:
                                    pass
                                cbz.writestr(os.path.basename(file_name), raw)
                                raw = None
                                if (page_num + 1) % 20 == 0:
                                    gc.collect()
                                pct = (page_num + 1) / total_pages * 100
                                signals.update_page_bar.emit(
                                    pct,
                                    _("dialogs.batch_cbr.page_progress").format(
                                        current=page_num + 1, total=total_pages))
                            except Exception:
                                continue

                gc.collect()
                try:
                    if is_permanent:
                        os.remove(cbr_path)
                    else:
                        callbacks['safe_delete_file'](cbr_path)
                except Exception as del_err:
                    conversion_errors.append(f"{basename} (suppression): {del_err}")
                converted_count[0] += 1

            except (rarfile.BadRarFile, rarfile.NotRarFile):
                try:
                    with zipfile.ZipFile(cbr_path, 'r') as arc:
                        if len([f for f in arc.namelist() if not f.endswith('/')]) == 0:
                            conversion_errors.append(f"{basename}: archive vide")
                            continue
                    base_path, _ext = os.path.splitext(cbr_path)
                    cbz_path = base_path + ".cbz"
                    if os.path.exists(cbz_path):
                        c = 1
                        while os.path.exists(f"{base_path} ({c}).cbz"):
                            c += 1
                        cbz_path = f"{base_path} ({c}).cbz"
                    os.rename(cbr_path, cbz_path)
                    signals.update_page_bar.emit(100.0, "ZIP → CBZ")
                    renamed_count[0] += 1
                except zipfile.BadZipFile:
                    conversion_errors.append(f"{basename}: ni RAR ni ZIP valide")
                except Exception as e:
                    conversion_errors.append(f"{basename}: {e}")
            except Exception as e:
                conversion_errors.append(f"{basename}: {e}")

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.accept()

        log_path = None
        if conversion_errors:
            try:
                from datetime import datetime
                now = datetime.now()
                log_filename = f"Log_cbrtocbz_{now.strftime('%Y_%m_%d_%H_%M')}.txt"
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = os.path.join(mosaicview_temp, log_filename)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write("MosaicView - CBR to CBZ Batch Conversion Log\n")
                    lf.write(f"Date: {now.strftime('%Y-%m-%d %H:%M')}\n")
                    lf.write(f"Directory: {directory}\n")
                    lf.write(f"Total files: {len(cbr_files)}\n")
                    lf.write(f"Converted: {converted_count[0]}\n")
                    lf.write(f"Renamed (ZIP→CBZ): {renamed_count[0]}\n")
                    lf.write(f"Errors: {len(conversion_errors)}\n")
                    lf.write(f"\n{'='*60}\n")
                    lf.write("Error details:\n")
                    lf.write(f"{'='*60}\n\n")
                    for err in conversion_errors:
                        lf.write(f"  - {err}\n")
            except Exception as log_err:
                print(f"Erreur log : {log_err}")
                log_path = None

        summary_data = {
            "converted_count": converted_count[0],
            "total":           len(cbr_files),
            "renamed_count":   renamed_count[0],
            "errors_count":    len(conversion_errors),
            "has_errors":      bool(conversion_errors),
            "directory":       directory,
            "directories":     directories,
            "log_path":        log_path,
        }
        show_batch_cbr_summary(parent, summary_data, callbacks)

    signals.conversion_done.connect(on_done)
    thread = threading.Thread(target=do_conversion, daemon=True)
    thread.start()
    prog.exec()


def show_batch_cbr_summary(parent, data, callbacks):
    dlg = _CbrSummaryDialog(parent, data)
    dlg.exec()


# ═══════════════════════════════════════════════════════════════════════════════
# Fonctions publiques — PDF→CBZ
# ═══════════════════════════════════════════════════════════════════════════════

def batch_convert_pdf_to_cbz(parent, callbacks, directory=None):
    """Lance la conversion batch PDF→CBZ."""
    if not PDF_AVAILABLE:
        ErrorDialog(parent,
                    _("dialogs.batch_pdf.pymupdf_required_title"),
                    _("dialogs.batch_pdf.pymupdf_required_message")).exec()
        return

    if directory is None:
        cfg = get_config_manager()
        directory = QFileDialog.getExistingDirectory(
            parent, _("dialogs.batch_pdf.select_directory_title"),
            cfg.get('last_open_dir', ""))
        if directory:
            cfg.set('last_open_dir', directory)
    if not directory:
        return

    pdf_files = []
    for dirpath, _subdirs, filenames in os.walk(directory):
        for fn in filenames:
            if fn.lower().endswith('.pdf'):
                pdf_files.append(os.path.join(dirpath, fn))
    pdf_files.sort(key=lambda f: callbacks['natural_sort_key'](os.path.basename(f).lower()))

    if not pdf_files:
        InfoDialog(parent, _("dialogs.batch_pdf.no_pdf_title"),
                   _("dialogs.batch_pdf.no_pdf_message").format(directory=directory)).exec()
        return

    batch_convert_pdf_to_cbz_confirm(parent, pdf_files, directory, callbacks)


def batch_convert_pdf_to_cbz_confirm(parent, pdf_files, directory, callbacks, directories=None):
    """Fenêtre de confirmation + progression + résumé PDF→CBZ."""
    pdf_files  = [os.path.normpath(f) for f in pdf_files]
    directory  = os.path.normpath(directory) if directory else directory
    if directories:
        directories = [os.path.normpath(d) for d in directories]
    from modules.qt import state as _state_module
    state = callbacks.get('state') or _state_module.state

    total_size = sum(os.path.getsize(f) for f in pdf_files)
    dlg = _ConfirmDialog(
        parent,
        title_key    = "dialogs.batch_pdf.confirm_title",
        msg_key      = "dialogs.batch_pdf.confirm_message",
        count        = len(pdf_files),
        directory    = directory,
        total_size   = total_size,
        checkbox_key = "dialogs.batch_pdf.checkbox_permanent_delete",
        tooltip_key  = "tooltip.batch_pdf_permanent_delete",
        start_key    = "dialogs.batch_pdf.start_button",
        callbacks    = callbacks,
    )
    dlg.exec()
    if not dlg.confirmed:
        return

    is_permanent = dlg.permanent_delete

    prog = _ProgressDialog(parent, "dialogs.batch_pdf.converting_title", has_page_bar=True)
    prog.show()
    QApplication.processEvents()

    signals           = _ThreadSignals()
    conversion_errors = []
    converted_count   = [0]
    owner_protected   = []

    signals.update_filename.connect(prog.set_filename)
    signals.update_progress.connect(prog.set_progress)
    signals.update_page_bar.connect(lambda v, t: prog.set_page_progress(v, t))
    signals.update_thumb.connect(prog.set_thumbnail)

    def do_conversion():
        import modules.qt.pdf_loading_qt as _pdfmod

        def _send(msg):
            _pdfmod._merge_in_q.put(msg)

        def _recv(timeout=60):
            """Attend un message du process, retourne None si timeout."""
            if _pdfmod._merge_out_conn.poll(timeout):
                return _pdfmod._merge_out_conn.recv()
            return None

        total = len(pdf_files)
        for idx, pdf_path in enumerate(pdf_files):
            basename = os.path.basename(pdf_path)
            signals.update_filename.emit(basename)
            signals.update_progress.emit(
                _("dialogs.batch_pdf.converting_progress").format(current=idx + 1, total=total))
            signals.update_page_bar.emit(0.0, "")

            # Assure que le process est vivant, envoie batch_open
            _pdfmod._ensure_merge_process()
            _send(('batch_open', pdf_path))

            # Attend batch_ready
            batch_ready = None
            while True:
                msg = _recv(timeout=30)
                if msg is None:
                    break
                if msg[0] == '_debug':
                    continue
                if msg[0] in ('batch_ready', 'error'):
                    batch_ready = msg
                    break

            if batch_ready is None or batch_ready[0] == 'error':
                err = batch_ready[1] if batch_ready else 'timeout'
                conversion_errors.append(f"{basename}: {err}")
                continue

            if batch_ready[1] is None:
                # needs_pass
                conversion_errors.append(
                    _("messages.errors.pdf_encrypted_skipped").format(filename=basename))
                continue

            _kind, total_pages, ratios, thumb_bytes, is_owner = batch_ready

            if is_owner:
                owner_protected.append(pdf_path)

            # Vignette
            if thumb_bytes:
                try:
                    img = Image.open(io.BytesIO(thumb_bytes))
                    px  = _pil_to_qpixmap(img, 150, 210, callbacks)
                    signals.update_thumb.emit(px)
                except Exception:
                    signals.update_thumb.emit(None)
            else:
                signals.update_thumb.emit(None)

            # Noms de fichiers
            ext_out = ".jpg"
            if state.renumber_mode == 1:
                multipliers    = callbacks['compute_auto_multipliers'](ratios)
                filenames_list = callbacks['generate_auto_filenames'](multipliers, ext_out)
            else:
                digits = max(2, len(str(total_pages)))
                filenames_list = [str(i + 1).zfill(digits) + ext_out for i in range(total_pages)]

            # CBZ path
            base_path, _ext = os.path.splitext(pdf_path)
            cbz_path = base_path + ".cbz"
            if os.path.exists(cbz_path):
                c = 1
                while os.path.exists(f"{base_path} ({c}).cbz"):
                    c += 1
                cbz_path = f"{base_path} ({c}).cbz"

            # Lance la conversion
            _send(('batch_convert', filenames_list))

            # Reçoit les pages et les écrit dans le CBZ
            try:
                with zipfile.ZipFile(cbz_path, 'w') as cbz:
                    while True:
                        msg = _recv(timeout=120)
                        if msg is None:
                            conversion_errors.append(f"{basename}: timeout")
                            break
                        if msg[0] == '_debug':
                            continue
                        if msg[0] == 'batch_page':
                            _kind2, filename, img_bytes, pct, cur, tot = msg
                            cbz.writestr(filename, img_bytes)
                            signals.update_page_bar.emit(
                                pct,
                                _("dialogs.batch_pdf.page_progress").format(
                                    current=cur, total=tot))
                        elif msg[0] == 'done':
                            break
                        elif msg[0] == 'error':
                            conversion_errors.append(f"{basename}: {msg[1]}")
                            break
            except Exception as e:
                conversion_errors.append(f"{basename}: {e}")
                continue

            gc.collect()

            if pdf_path not in owner_protected:
                try:
                    if is_permanent:
                        os.remove(pdf_path)
                    else:
                        callbacks['safe_delete_file'](pdf_path)
                except Exception as del_err:
                    conversion_errors.append(f"{basename} (suppression): {del_err}")

            converted_count[0] += 1

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.accept()

        log_path = None
        if conversion_errors:
            try:
                from datetime import datetime
                now = datetime.now()
                log_filename = f"Log_pdftocbz_{now.strftime('%Y_%m_%d_%H_%M')}.txt"
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = os.path.join(mosaicview_temp, log_filename)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write("MosaicView - PDF to CBZ Batch Conversion Log\n")
                    lf.write(f"Date: {now.strftime('%Y-%m-%d %H:%M')}\n")
                    lf.write(f"Directory: {directory}\n")
                    lf.write(f"Total files: {len(pdf_files)}\n")
                    lf.write(f"Converted: {converted_count[0]}\n")
                    lf.write(f"Errors: {len(conversion_errors)}\n")
                    lf.write(f"\n{'='*60}\n")
                    lf.write("Error details:\n")
                    lf.write(f"{'='*60}\n\n")
                    for err in conversion_errors:
                        lf.write(f"  - {err}\n")
            except Exception as log_err:
                print(f"Erreur log : {log_err}")
                log_path = None

        summary_data = {
            "converted_count":     converted_count[0],
            "total":               len(pdf_files),
            "errors_count":        len(conversion_errors),
            "has_errors":          bool(conversion_errors),
            "directory":           directory,
            "directories":         directories,
            "log_path":            log_path,
            "owner_protected_pdfs": owner_protected,
            "is_permanent":        is_permanent,
            "safe_delete_file":    callbacks['safe_delete_file'],
        }
        show_batch_pdf_summary(parent, summary_data, callbacks)

    signals.conversion_done.connect(on_done)
    thread = threading.Thread(target=do_conversion, daemon=True)
    thread.start()
    prog.exec()


def show_batch_pdf_summary(parent, data, callbacks):
    dlg = _PdfSummaryDialog(parent, data, callbacks)
    dlg.exec()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog choix de mode IMG→CBZ
# ═══════════════════════════════════════════════════════════════════════════════

class _ImgModeDialog(QDialog):
    """Demande à l'utilisateur le mode de regroupement : une image/CBZ ou tout en un."""

    MODE_ONE_PER_IMAGE = "one_per_image"
    MODE_ALL_IN_ONE    = "all_in_one"

    def __init__(self, parent):
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumWidth(480)
        self.chosen_mode = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self._title_lbl = QLabel()
        self._title_lbl.setAlignment(Qt.AlignCenter)
        self._title_lbl.setWordWrap(True)
        layout.addWidget(self._title_lbl)

        self._intro_lbl = QLabel()
        self._intro_lbl.setAlignment(Qt.AlignCenter)
        self._intro_lbl.setWordWrap(True)
        layout.addWidget(self._intro_lbl)

        layout.addSpacing(6)

        self._group = QButtonGroup(self)

        # Option 1 — une image par CBZ
        opt1_layout = QVBoxLayout()
        opt1_layout.setSpacing(0)
        opt1_layout.setContentsMargins(0, 0, 0, 0)
        self._radio1 = QRadioButton()
        self._radio1.setChecked(True)
        self._group.addButton(self._radio1)
        opt1_layout.addWidget(self._radio1, alignment=Qt.AlignCenter)
        self._sub1 = QLabel()
        self._sub1.setAlignment(Qt.AlignCenter)
        opt1_layout.addWidget(self._sub1, alignment=Qt.AlignCenter)
        layout.addLayout(opt1_layout)

        layout.addSpacing(8)

        # Option 2 — toutes les images en un seul CBZ
        opt2_layout = QVBoxLayout()
        opt2_layout.setSpacing(0)
        opt2_layout.setContentsMargins(0, 0, 0, 0)
        self._radio2 = QRadioButton()
        self._group.addButton(self._radio2)
        opt2_layout.addWidget(self._radio2, alignment=Qt.AlignCenter)
        self._sub2 = QLabel()
        self._sub2.setAlignment(Qt.AlignCenter)
        self._sub2.setWordWrap(True)
        opt2_layout.addWidget(self._sub2)
        layout.addLayout(opt2_layout)

        layout.addSpacing(12)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok_btn = QPushButton()
        self._ok_btn.setFixedWidth(110)
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(self._ok_btn)
        self._cancel_btn = QPushButton()
        self._cancel_btn.setFixedWidth(110)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.rejected.connect(self._on_cancel)
        self._retranslate()
        _connect_lang(self, lambda _lang: self._retranslate())
        self._ok_btn.setFocus()

        # Centrer sur le parent
        self.adjustSize()
        self.setFixedHeight(self.sizeHint().height() + 30)
        if parent:
            pg = parent.geometry()
            self.move(
                pg.x() + (pg.width()  - self.width())  // 2,
                pg.y() + (pg.height() - self.height()) // 2,
            )

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )
        font      = _get_current_font(10)
        font_bold = _get_current_font(11)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        radio_style = f"QRadioButton {{ color: {theme['text']}; }}"

        self.setWindowTitle(_wt("dialogs.batch_img.mode_title"))
        self._title_lbl.setText(_("dialogs.batch_img.mode_title"))
        self._title_lbl.setFont(font_bold)
        self._intro_lbl.setText(_("dialogs.batch_img.mode_intro"))
        self._intro_lbl.setFont(font)

        self._radio1.setText(_("dialogs.batch_img.mode_one_per_image"))
        self._radio1.setFont(font)
        self._radio1.setStyleSheet(radio_style)
        self._sub1.setText(_("dialogs.batch_img.mode_one_per_image_sub"))
        self._sub1.setFont(_get_current_font(9))
        self._sub1.setStyleSheet(f"color: {theme.get('disabled', '#888888')};")

        self._radio2.setText(_("dialogs.batch_img.mode_all_in_one"))
        self._radio2.setFont(font)
        self._radio2.setStyleSheet(radio_style)
        self._sub2.setText(_("dialogs.batch_img.mode_all_in_one_sub"))
        self._sub2.setFont(_get_current_font(9))
        self._sub2.setStyleSheet(f"color: {theme.get('disabled', '#888888')};")

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)
        self._cancel_btn.setText(_("buttons.cancel"))
        self._cancel_btn.setFont(font)
        self._cancel_btn.setStyleSheet(btn_style)

    def _on_ok(self):
        self.chosen_mode = (
            self.MODE_ONE_PER_IMAGE if self._radio1.isChecked()
            else self.MODE_ALL_IN_ONE
        )
        self.accept()

    def _on_cancel(self):
        self.chosen_mode = None
        self.accept()


# ═══════════════════════════════════════════════════════════════════════════════
# Fonctions publiques — IMG→CBZ
# ═══════════════════════════════════════════════════════════════════════════════

def batch_convert_img_to_cbz(parent, callbacks, directory=None):
    """Lance la conversion batch images→CBZ."""
    # Choix du mode avant la sélection du répertoire
    mode_dlg = _ImgModeDialog(parent)
    mode_dlg.exec()
    if mode_dlg.chosen_mode is None:
        return

    if directory is None:
        cfg = get_config_manager()
        directory = QFileDialog.getExistingDirectory(
            parent, _("dialogs.batch_img.select_directory_title"),
            cfg.get('last_open_dir', ""))
        if directory:
            cfg.set('last_open_dir', directory)
    if not directory:
        return

    img_files = []
    for dirpath, _subdirs, filenames in os.walk(directory):
        for fn in filenames:
            if fn.lower().endswith(image_exts):
                img_files.append(os.path.join(dirpath, fn))
    img_files.sort(key=lambda f: callbacks['natural_sort_key'](os.path.basename(f).lower()))

    if not img_files:
        InfoDialog(parent, _("dialogs.batch_img.no_img_title"),
                   _("dialogs.batch_img.no_img_message").format(directory=directory)).exec()
        return

    if mode_dlg.chosen_mode == _ImgModeDialog.MODE_ONE_PER_IMAGE:
        batch_convert_img_to_cbz_confirm(parent, img_files, directory, callbacks)
    else:
        batch_convert_imgs_to_single_cbz(parent, img_files, directory, callbacks)


def batch_convert_img_to_cbz_confirm(parent, img_files, directory, callbacks, directories=None):
    """Fenêtre de confirmation + progression + résumé IMG→CBZ."""
    img_files  = [os.path.normpath(f) for f in img_files]
    directory  = os.path.normpath(directory) if directory else directory
    if directories:
        directories = [os.path.normpath(d) for d in directories]
    total_size = sum(os.path.getsize(f) for f in img_files)

    dlg = _ConfirmDialog(
        parent,
        title_key    = "dialogs.batch_img.confirm_title",
        msg_key      = "dialogs.batch_img.confirm_message",
        count        = len(img_files),
        directory    = directory,
        total_size   = total_size,
        checkbox_key = "dialogs.batch_img.checkbox_permanent_delete",
        tooltip_key  = "tooltip.batch_img_permanent_delete",
        start_key    = "dialogs.batch_img.start_button",
        callbacks    = callbacks,
    )
    dlg.exec()
    if not dlg.confirmed:
        return

    is_permanent = dlg.permanent_delete

    prog = _ProgressDialog(parent, "dialogs.batch_img.converting_title", has_page_bar=False)
    prog.show()
    QApplication.processEvents()

    signals           = _ThreadSignals()
    conversion_errors = []
    converted_count   = [0]
    converted_by_ext  = {}

    signals.update_filename.connect(prog.set_filename)
    signals.update_progress.connect(prog.set_progress)
    signals.update_thumb.connect(prog.set_thumbnail)

    def do_conversion():
        total = len(img_files)
        for idx, img_path in enumerate(img_files):
            basename  = os.path.basename(img_path)
            base_path, ext = os.path.splitext(img_path)
            ext_lower = ext.lower()

            signals.update_filename.emit(basename)
            signals.update_progress.emit(
                _("dialogs.batch_img.converting_progress").format(current=idx + 1, total=total))

            # Vignette
            try:
                img = Image.open(img_path)
                px  = _pil_to_qpixmap(img, 150, 210, callbacks)
                signals.update_thumb.emit(px)
                img = None
            except Exception:
                signals.update_thumb.emit(None)

            try:
                with open(img_path, 'rb') as f:
                    img_data = f.read()

                try:
                    tmp = Image.open(io.BytesIO(img_data))
                    tmp.load()
                except Exception as val_err:
                    raise Exception(f"invalid or corrupted image: {val_err}")

                if tmp.format == 'ICO' or ext_lower == '.ico':
                    raise Exception("unsupported format: ICO files cannot be converted")

                n_frames = getattr(tmp, 'n_frames', 1)
                if n_frames > 1:
                    fmt = tmp.format or ext_lower.lstrip('.').upper()
                    raise Exception(f"unsupported multi-frame image ({fmt}, {n_frames} frames)")

                if tmp.mode in ("CMYK", "YCbCr", "I", "F"):
                    tmp = tmp.convert("RGB")
                    buf = io.BytesIO()
                    fmt_map = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP",
                               ".bmp": "BMP", ".tiff": "TIFF", ".tif": "TIFF", ".gif": "GIF"}
                    out_fmt = fmt_map.get(ext_lower, "JPEG")
                    tmp.save(buf, format=out_fmt,
                             **({"quality": 100, "optimize": True} if out_fmt == "JPEG" else {}))
                    img_data = buf.getvalue()
                    buf = None
                tmp = None

                cbz_path = base_path + ".cbz"
                if os.path.exists(cbz_path):
                    c = 1
                    while os.path.exists(f"{base_path}_{c:02d}.cbz"):
                        c += 1
                    cbz_path = f"{base_path}_{c:02d}.cbz"

                with zipfile.ZipFile(cbz_path, 'w', compression=zipfile.ZIP_STORED) as cbz:
                    cbz.writestr(basename, img_data)

                img_data = None
                gc.collect()

                try:
                    if is_permanent:
                        os.remove(img_path)
                    else:
                        callbacks['safe_delete_file'](img_path)
                except Exception as del_err:
                    conversion_errors.append(f"{basename} (suppression): {del_err}")

                converted_count[0] += 1
                converted_by_ext[ext_lower] = converted_by_ext.get(ext_lower, 0) + 1

            except Exception as e:
                conversion_errors.append(f"{basename}: {e}")

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.accept()

        log_path = None
        if conversion_errors:
            try:
                from datetime import datetime
                now = datetime.now()
                log_filename = f"Log_imgtocbz_{now.strftime('%Y_%m_%d_%H_%M')}.txt"
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = os.path.join(mosaicview_temp, log_filename)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write("MosaicView - Image to CBZ Batch Conversion Log\n")
                    lf.write(f"Date: {now.strftime('%Y-%m-%d %H:%M')}\n")
                    lf.write(f"Directory: {directory}\n")
                    lf.write(f"Total files: {len(img_files)}\n")
                    lf.write(f"Converted: {converted_count[0]}\n")
                    lf.write(f"Errors: {len(conversion_errors)}\n")
                    lf.write(f"\n{'='*60}\n")
                    lf.write("Error details:\n")
                    lf.write(f"{'='*60}\n\n")
                    for err in conversion_errors:
                        lf.write(f"  - {err}\n")
            except Exception as log_err:
                print(f"Erreur log : {log_err}")
                log_path = None

        summary_data = {
            "converted_count":  converted_count[0],
            "total":            len(img_files),
            "errors_count":     len(conversion_errors),
            "has_errors":       bool(conversion_errors),
            "directory":        directory,
            "directories":      directories,
            "log_path":         log_path,
            "converted_by_ext": dict(converted_by_ext),
        }
        show_batch_img_summary(parent, summary_data, callbacks)

    signals.conversion_done.connect(on_done)
    thread = threading.Thread(target=do_conversion, daemon=True)
    thread.start()
    prog.exec()


def show_batch_img_summary(parent, data, callbacks):
    dlg = _ImgSummaryDialog(parent, data)
    dlg.exec()


def batch_convert_imgs_to_single_cbz(parent, img_files, directory, callbacks):
    """Regroupe toutes les images du répertoire en un seul fichier CBZ."""
    img_files  = [os.path.normpath(f) for f in img_files]
    directory  = os.path.normpath(directory)
    total_size = sum(os.path.getsize(f) for f in img_files)

    dlg = _ConfirmDialog(
        parent,
        title_key    = "dialogs.batch_img.confirm_title",
        msg_key      = "dialogs.batch_img.confirm_message",
        count        = len(img_files),
        directory    = directory,
        total_size   = total_size,
        checkbox_key = "dialogs.batch_img.checkbox_permanent_delete",
        tooltip_key  = "tooltip.batch_img_permanent_delete",
        start_key    = "dialogs.batch_img.start_button",
        callbacks    = callbacks,
    )
    dlg.exec()
    if not dlg.confirmed:
        return

    is_permanent = dlg.permanent_delete

    prog = _ProgressDialog(parent, "dialogs.batch_img.converting_title", has_page_bar=False)
    prog.show()
    QApplication.processEvents()

    signals           = _ThreadSignals()
    conversion_errors = []
    converted_count   = [0]
    converted_by_ext  = {}

    signals.update_filename.connect(prog.set_filename)
    signals.update_progress.connect(prog.set_progress)
    signals.update_thumb.connect(prog.set_thumbnail)

    cbz_name     = os.path.basename(directory) + ".cbz"
    cbz_path_out = os.path.join(directory, cbz_name)
    # Éviter collision si le CBZ existe déjà
    if os.path.exists(cbz_path_out):
        base = os.path.join(directory, os.path.basename(directory))
        c = 1
        while os.path.exists(f"{base}_{c:02d}.cbz"):
            c += 1
        cbz_path_out = f"{base}_{c:02d}.cbz"

    def do_conversion():
        total = len(img_files)
        try:
            with zipfile.ZipFile(cbz_path_out, 'w', compression=zipfile.ZIP_STORED) as cbz:
                for idx, img_path in enumerate(img_files):
                    basename  = os.path.basename(img_path)
                    ext_lower = os.path.splitext(img_path)[1].lower()

                    signals.update_filename.emit(basename)
                    signals.update_progress.emit(
                        _("dialogs.batch_img.converting_progress").format(current=idx + 1, total=total))

                    # Vignette
                    try:
                        img = Image.open(img_path)
                        px  = _pil_to_qpixmap(img, 150, 210, callbacks)
                        signals.update_thumb.emit(px)
                        img = None
                    except Exception:
                        signals.update_thumb.emit(None)

                    try:
                        with open(img_path, 'rb') as f:
                            img_data = f.read()

                        try:
                            tmp = Image.open(io.BytesIO(img_data))
                            tmp.load()
                        except Exception as val_err:
                            raise Exception(f"invalid or corrupted image: {val_err}")

                        if tmp.format == 'ICO' or ext_lower == '.ico':
                            raise Exception("unsupported format: ICO files cannot be converted")

                        n_frames = getattr(tmp, 'n_frames', 1)
                        if n_frames > 1:
                            fmt = tmp.format or ext_lower.lstrip('.').upper()
                            raise Exception(f"unsupported multi-frame image ({fmt}, {n_frames} frames)")

                        if tmp.mode in ("CMYK", "YCbCr", "I", "F"):
                            tmp = tmp.convert("RGB")
                            buf = io.BytesIO()
                            fmt_map = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP",
                                       ".bmp": "BMP", ".tiff": "TIFF", ".tif": "TIFF", ".gif": "GIF"}
                            out_fmt = fmt_map.get(ext_lower, "JPEG")
                            tmp.save(buf, format=out_fmt,
                                     **({"quality": 100, "optimize": True} if out_fmt == "JPEG" else {}))
                            img_data = buf.getvalue()
                            buf = None
                        tmp = None

                        cbz.writestr(basename, img_data)
                        img_data = None
                        gc.collect()

                        try:
                            if is_permanent:
                                os.remove(img_path)
                            else:
                                callbacks['safe_delete_file'](img_path)
                        except Exception as del_err:
                            conversion_errors.append(f"{basename} (suppression): {del_err}")

                        converted_count[0] += 1
                        converted_by_ext[ext_lower] = converted_by_ext.get(ext_lower, 0) + 1

                    except Exception as e:
                        conversion_errors.append(f"{basename}: {e}")

        except Exception as e:
            conversion_errors.append(f"CBZ creation failed: {e}")

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.accept()

        log_path = None
        if conversion_errors:
            try:
                from datetime import datetime
                now = datetime.now()
                log_filename = f"Log_imgtocbz_{now.strftime('%Y_%m_%d_%H_%M')}.txt"
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = os.path.join(mosaicview_temp, log_filename)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write("MosaicView - Images to single CBZ Batch Conversion Log\n")
                    lf.write(f"Date: {now.strftime('%Y-%m-%d %H:%M')}\n")
                    lf.write(f"Directory: {directory}\n")
                    lf.write(f"Output: {cbz_path_out}\n")
                    lf.write(f"Total files: {len(img_files)}\n")
                    lf.write(f"Converted: {converted_count[0]}\n")
                    lf.write(f"Errors: {len(conversion_errors)}\n")
                    lf.write(f"\n{'='*60}\n")
                    lf.write("Error details:\n")
                    lf.write(f"{'='*60}\n\n")
                    for err in conversion_errors:
                        lf.write(f"  - {err}\n")
            except Exception as log_err:
                print(f"Erreur log : {log_err}")
                log_path = None

        summary_data = {
            "converted_count":  converted_count[0],
            "total":            len(img_files),
            "errors_count":     len(conversion_errors),
            "has_errors":       bool(conversion_errors),
            "directory":        directory,
            "directories":      None,
            "log_path":         log_path,
            "converted_by_ext": dict(converted_by_ext),
        }
        show_batch_img_summary(parent, summary_data, callbacks)

    signals.conversion_done.connect(on_done)
    thread = threading.Thread(target=do_conversion, daemon=True)
    thread.start()
    prog.exec()
