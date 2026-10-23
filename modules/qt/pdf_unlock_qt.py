"""
modules/qt/pdf_unlock_qt.py
Déverrouillage de PDFs protégés (owner password).
"""

import os

from PySide6.QtCore import Qt, QThread, QUrl, Signal
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
        self.setWindowFlags(Qt.Window)
        self._dest_path = dest_path
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)

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
        from modules.qt.utils import setup_path_label_context_menu
        setup_path_label_context_menu(self._link, lambda: self._dest_path, self._open_file)
        layout.addWidget(self._link)

        self._btn_ok = QPushButton()
        self._btn_ok.clicked.connect(self.accept)
        layout.addWidget(self._btn_ok, alignment=Qt.AlignCenter)

        self._retranslate()

        from modules.qt.language_signal import language_signal, disconnect_on_destroy
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        disconnect_on_destroy(self, self._lang_handler)
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
    """Propose d'enregistrer une copie déverrouillée du PDF (owner password). NON modal."""

    result_signal = Signal(bool)   # True = Oui, False = Non/fermeture

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self._emitted = False
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)
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
        self._btn_yes.clicked.connect(lambda: self._finish(True))
        self._btn_no.clicked.connect(lambda: self._finish(False))

        btn_layout.addWidget(self._btn_yes)
        btn_layout.addWidget(self._btn_no)
        layout.addLayout(btn_layout)

        self._retranslate()

        from modules.qt.language_signal import language_signal, disconnect_on_destroy
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        disconnect_on_destroy(self, self._lang_handler)
        self._center_parent = parent

    def _finish(self, result: bool):
        if self._emitted:
            return
        self._emitted = True
        self._on_close()
        self.result_signal.emit(result)
        self.hide()
        self.deleteLater()

    def reject(self):
        # Échap : même chemin que la croix (réponse « non », destruction).
        # QDialog.reject masquerait la fenêtre sans passer par closeEvent.
        self.close()

    def closeEvent(self, event):
        if not self._emitted:
            self._emitted = True
            self._on_close()
            self.result_signal.emit(False)
        event.accept()

    def ask_async(self, on_result):
        """Affiche (NON modal) et appelle on_result(bool) à la réponse."""
        from modules.qt.dialogs_qt import position_dialog_on_parent
        self.result_signal.connect(on_result)
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

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
# Fonctions publiques
# ═══════════════════════════════════════════════════════════════════════════════
def show_pdf_unlock_dialog(filepath: str, parent):
    """Affiche la fenêtre de proposition de déverrouillage (owner password)."""
    try:
        import pymupdf as fitz
    except ImportError:
        return

    dlg = PdfUnlockDialog(parent)
    dlg.ask_async(lambda yes: _save_unlocked(filepath, parent) if yes else None)


# Erreur « process arrêté » transmise par _UnlockWorker.done : traduite à
# l'affichage par _MsgDialog (clé), pour suivre un changement de langue
_PROCESS_STOPPED = "\0process_stopped"


class _ProbeFailed(RuntimeError):
    """Échec rapporté par le process PDF : la pile d'une erreur inattendue y a
    déjà été écrite dans le journal des erreurs, celle-ci n'apprendrait rien."""

    expected_error = True


class _UnlockWorker(QThread):
    """Écrit la copie déverrouillée hors du thread de l'interface : un gros
    PDF figerait sinon les deux panneaux pendant tout l'enregistrement.

    PyMuPDF travaille dans un process séparé (pdf_probe) : un PDF qui le fait
    planter ne ferme pas l'application. La copie est écrite dans un temporaire
    qui ne remplace la destination qu'une fois complète
    (replace_file_atomically) : un process mort en pleine écriture ne laisse
    pas de copie tronquée. Pas de délai (un gros PDF peut être long à
    réécrire) ; à la fermeture de l'application, shutdown_pdf_probes arrête
    le process et la demande échoue aussitôt."""

    done = Signal(str, str)   # chemin écrit, message d'erreur ("" si succès)

    def __init__(self, filepath, dest_path):
        super().__init__()
        self._filepath = filepath
        self._dest_path = dest_path

    def run(self):
        from modules.qt import pdf_probe
        from modules.qt.utils import replace_file_atomically
        probe = pdf_probe.PdfProbe()

        def _write(tmp_path):
            status, result = probe.call(('unlock', self._filepath, tmp_path), timeout=None)
            if status == pdf_probe.ERROR:
                raise _ProbeFailed(result)
            if status != pdf_probe.OK:
                raise _ProbeFailed(_PROCESS_STOPPED)

        try:
            replace_file_atomically(self._dest_path, _write)
        except Exception as e:
            from modules.qt.error_log import log_if_unexpected
            log_if_unexpected(e)
            self.done.emit(self._dest_path, str(e) or type(e).__name__)
            return
        finally:
            probe.shutdown()
        self.done.emit(self._dest_path, "")


# Workers en cours : sans référence Python, un QThread actif serait détruit.
_running_unlock_workers = []


def _save_unlocked(filepath: str, parent):
    """Enregistre une copie du PDF sans protection owner password."""
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        return

    base, ext = os.path.splitext(filepath)
    dest_path = base + "_unlocked" + ext
    counter = 1
    while os.path.exists(dest_path):
        dest_path = base + f"_unlocked_{counter}" + ext
        counter += 1

    worker = _UnlockWorker(filepath, dest_path)
    _running_unlock_workers.append(worker)

    def _on_done(path, error):
        from modules.qt.utils import dispose_qthread
        if worker in _running_unlock_workers:
            _running_unlock_workers.remove(worker)
        dispose_qthread(worker)
        try:
            if error == _PROCESS_STOPPED:
                _MsgDialog(parent,
                    "messages.errors.pdf_unlock_failed.title",
                    "messages.errors.pdf_failure.process_stopped",
                ).show_nonmodal()
                return
            if error:
                _MsgDialog(parent,
                    "messages.errors.pdf_unlock_failed.title",
                    "messages.errors.pdf_unlock_failed.message",
                    {"error": error},
                ).show_nonmodal()
                return
            dlg = _PdfUnlockedSuccessDialog(parent, path)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
        except RuntimeError:
            pass  # panneau détruit pendant l'enregistrement

    worker.done.connect(_on_done, Qt.QueuedConnection)
    worker.start()
