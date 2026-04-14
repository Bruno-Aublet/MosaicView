"""
modules/qt/pdf_unlock_qt.py
Déverrouillage de PDFs protégés (owner password) — version PySide6.
Reproduit à l'identique pdf_unlock.py (show_pdf_unlock_dialog + _save_unlocked).
"""

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.pdf_loading_qt import _MsgDialog


class _PdfUnlockedSuccessDialog(QDialog):
    """Dialogue de succès avec lien cliquable vers le PDF déverrouillé."""

    def __init__(self, parent, dest_path: str):
        super().__init__(parent)
        self._dest_path = dest_path
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(8)

        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        layout.addWidget(self._lbl)

        self._link = QLabel()
        self._link.setAlignment(Qt.AlignCenter)
        self._link.setOpenExternalLinks(False)
        self._link.linkActivated.connect(self._open_file)
        layout.addWidget(self._link)

        self._btn_ok = QPushButton()
        self._btn_ok.clicked.connect(self.accept)
        layout.addWidget(self._btn_ok, alignment=Qt.AlignCenter)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
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
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self.setWindowTitle(_wt("messages.info.pdf_unlocked.title"))
        msg = _("messages.info.pdf_unlocked.message", path="").split("\n")[0].strip()
        self._lbl.setText(msg)
        self._lbl.setFont(font)
        fname = os.path.basename(self._dest_path)
        self._link.setText(f'<a href="file">{fname}</a>')
        self._link.setFont(font)
        self._btn_ok.setText(_("buttons.ok"))
        self._btn_ok.setFont(font)
        self._btn_ok.setStyleSheet(btn_style)

    def _open_file(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self._dest_path)))

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Dialogue de proposition de déverrouillage — supporte le changement de langue
# ═══════════════════════════════════════════════════════════════════════════════
class PdfUnlockDialog(QDialog):
    """Propose d'enregistrer une copie déverrouillée du PDF (owner password)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 12)
        layout.setSpacing(12)

        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        layout.addWidget(self._lbl)

        from PySide6.QtWidgets import QHBoxLayout
        btn_layout = QHBoxLayout()
        self._btn_yes = QPushButton()
        self._btn_no  = QPushButton()
        self._btn_yes.clicked.connect(self.accept)
        self._btn_no.clicked.connect(self.reject)

        btn_layout.addWidget(self._btn_yes)
        btn_layout.addWidget(self._btn_no)
        layout.addLayout(btn_layout)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
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
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 6px 12px; min-height: 2em; "
            f"white-space: normal; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self.setWindowTitle(_wt("dialogs.pdf_unlock.title"))
        self._lbl.setText(_("dialogs.pdf_unlock.message"))
        self._lbl.setFont(font)
        self._btn_yes.setText(_("dialogs.pdf_unlock.yes"))
        self._btn_yes.setFont(font)
        self._btn_yes.setStyleSheet(btn_style)
        self._btn_no.setText(_("dialogs.pdf_unlock.no"))
        self._btn_no.setFont(font)
        self._btn_no.setStyleSheet(btn_style)

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Fonctions publiques — reproduit show_pdf_unlock_dialog + _save_unlocked
# ═══════════════════════════════════════════════════════════════════════════════
def show_pdf_unlock_dialog(filepath: str, parent):
    """Affiche la fenêtre de proposition de déverrouillage (owner password)."""
    try:
        import fitz
    except ImportError:
        return

    dlg = PdfUnlockDialog(parent)
    if dlg.exec() == QDialog.Accepted:
        _save_unlocked(filepath, parent)


def _save_unlocked(filepath: str, parent):
    """Enregistre une copie du PDF sans protection owner password."""
    try:
        import fitz
    except ImportError:
        return

    try:
        base, ext = os.path.splitext(filepath)
        dest_path = base + "_unlocked" + ext
        counter = 1
        while os.path.exists(dest_path):
            dest_path = base + f"_unlocked_{counter}" + ext
            counter += 1

        doc = fitz.open(filepath)
        doc.authenticate("")
        doc.save(dest_path, encryption=fitz.PDF_ENCRYPT_NONE)
        doc.close()

        _PdfUnlockedSuccessDialog(parent, dest_path).exec()

    except Exception as e:
        _MsgDialog(parent,
            "messages.errors.pdf_unlock_failed.title",
            "messages.errors.pdf_unlock_failed.message",
            {"error": str(e)},
        ).exec()
