"""
file_operations_qt.py — Opérations de sauvegarde/export.

Implémente les 6 méthodes de sauvegarde :
  1. save_as_cbz              — Enregistrer sous (nouveau fichier CBZ)
  2. save_selection_as_cbz    — Enregistrer la sélection en CBZ
  3. save_selection_to_folder — Exporter les pages sélectionnées (dossier ou fichier)
  4. create_cbz_from_images   — Créer une archive CBZ (mode images seules)
  5. apply_new_names          — Appliquer les modifications et sauvegarder le CBZ
  6. apply_new_names          — Appliquer les modifications et créer un CBZ (CBR/PDF → CBZ)
"""

import os
import io
import html
import sys
import shutil
import zipfile

from PIL import Image

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSizePolicy, QProgressDialog,
    QFileDialog, QApplication,
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QDesktopServices
from PySide6.QtCore import QUrl

from modules.qt import state as _state_module
from modules.qt.localization import _, _wt
from modules.qt.config_manager import get_config_manager
from modules.qt.entries import save_image_to_bytes, open_page_image
from modules.qt.archive_names import same_image_format, JPEG_EXTS
from modules.qt.image_ops import is_animated_entry, has_several_frames
from modules.qt.utils import (
    format_file_size, safe_join, is_same_path,
    windows_safe_component, windows_safe_relpath, unique_names,
    replace_file_atomically, estimate_cbz_size, file_signature,
    cbz_members, MissingPageDataError, verify_written_cbz, WrittenFileCheckError,
    check_written_cbz_structure,
    send_to_trash,
)
from modules.qt.undo_redo import (
    mark_history_saved, mark_history_unsaved, history_top, content_snapshot,
    content_unchanged,
)
from modules.qt.dialogs_qt import (
    detect_duplicate_filenames_for_save,
    ErrorDialog, InfoDialog, QuestionYNCDialog,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers communs
# ═══════════════════════════════════════════════════════════════════════════════

_VIDEO_EXTENSIONS = {
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".webm", ".flv", ".m4v",
    ".mpg", ".mpeg", ".ts", ".3gp", ".ogv", ".rm", ".rmvb", ".divx",
    ".vob", ".mts", ".m2ts", ".f4v", ".asf",
}

_JPEG_EXTS = frozenset(JPEG_EXTS)
# Extensions pour lesquelles save_image_to_bytes (entries.py) écrit le DPI.
_DPI_WRITABLE_EXTS = _JPEG_EXTS | {".png", ".tif", ".tiff"}


def jpeg_bytes_with_dpi(data, dpi):
    """Bytes JPEG portant la résolution `dpi` (entier), SANS ré-encoder l'image :
    seule la densité du segment JFIF (APP0) est écrite, en le créant juste après
    le marqueur de début s'il n'existe pas. Retourne None si `data` n'est pas un
    JPEG exploitable — l'appelant garde alors les bytes d'origine. Un ré-encodage
    pour inscrire la résolution coûterait une perte de qualité à chaque
    enregistrement."""
    if not data or data[:2] != b"\xff\xd8" or not dpi:
        return None
    value = max(1, min(int(round(dpi)), 0xFFFF))
    density = bytes([1]) + value.to_bytes(2, "big") * 2   # unité 1 = points par pouce
    if data[2:4] == b"\xff\xe0" and data[6:11] == b"JFIF\x00" and len(data) >= 18:
        return data[:13] + density + data[18:]
    app0 = (b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00" + b"\x01\x01"
            + density + b"\x00\x00")
    return data[:2] + app0 + data[2:]


def _check_no_ico(parent, state=None):
    if state is None:
        state = _state_module.state
    if any(e.get("orig_name", "").lower().endswith(".ico") for e in state.images_data):
        from modules.qt.dialogs_qt import MsgDialog
        MsgDialog(parent,
                  "dialogs.ico_creator.save_blocked_title",
                  "dialogs.ico_creator.save_blocked_message").show_nonmodal()
        return False
    return True


def _check_no_video(parent, state=None):
    if state is None:
        state = _state_module.state
    if any(os.path.splitext(e.get("orig_name", "").lower())[1] in _VIDEO_EXTENSIONS
           for e in state.images_data):
        ErrorDialog(parent,
                    lambda: _wt("dialogs.video_save_blocked.title"),
                    lambda: _("dialogs.video_save_blocked.message")).show()
        return False
    return True


def _open_file_location(filepath):
    """Ouvre l'explorateur Windows et sélectionne le fichier (skill
    explorer-select : toujours _explorer_select, jamais un appel
    explorer /select ad hoc)."""
    try:
        if os.name == "nt":
            from modules.qt.library_window import _explorer_select
            _explorer_select(os.path.abspath(filepath).replace('/', '\\'))
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(filepath)))
    except Exception:
        pass


def _open_folder(folder):
    """Ouvre le dossier dans l'explorateur."""
    try:
        if os.name == "nt":
            os.startfile(folder)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# Dialogs Qt — avec support changement de langue à la volée
# ═══════════════════════════════════════════════════════════════════════════════

def _apply_confirmation_theme(dialog, text_labels, link_label, link_href, link_text, buttons):
    """Thème courant des confirmations de sauvegarde : fond, textes, lien, boutons.

    La couleur d'un <a> dans un QLabel ne suit pas le stylesheet du label : elle
    doit être portée par la balise elle-même."""
    from modules.qt.state import get_current_theme
    theme = get_current_theme()
    bg  = theme['bg']
    fg  = theme['text']
    alt = theme.get('toolbar_bg', bg)
    sep = theme.get('separator', '#aaaaaa')
    dialog.setStyleSheet(f"QDialog {{ background: {bg}; }}")
    for lbl in text_labels:
        lbl.setStyleSheet(f"color: {fg}; background: {bg};")
    link_label.setStyleSheet(f"background: {bg};")
    link_label.setText(
        f'<a href="{link_href}" style="color:{theme["link"]};">'
        f'{html.escape(link_text)}</a>')
    for btn in buttons:
        btn.setStyleSheet(
            f"QPushButton {{ background: {alt}; color: {fg}; "
            f"border: 1px solid {sep}; padding: 4px 12px; border-radius: 3px; }} "
            f"QPushButton:hover {{ background: {sep}; }}"
        )


def _apply_confirmation_font(widgets):
    """Police courante (dont pIqaD/Tengwar) sur les textes traduits ; le lien,
    un chemin de fichier, garde la police par défaut."""
    from modules.qt.font_manager_qt import get_current_font
    font = get_current_font()
    for w in widgets:
        w.setFont(font)


class InfoDialogClickablePath(QDialog):
    """Dialogue d'information avec chemin de fichier cliquable."""

    def __init__(self, parent, title_key: str, message_key: str, filepath: str, on_done=None):
        super().__init__(parent)
        self._title_key = title_key
        self._message_key = message_key
        self._on_done = on_done
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        self._msg = QLabel()
        self._msg.setWordWrap(True)
        self._msg.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg)

        # Chemin cliquable (statique — pas de clé de traduction)
        self._filepath = filepath
        self._path_lbl = QLabel()
        self._path_lbl.setWordWrap(True)
        self._path_lbl.setAlignment(Qt.AlignCenter)
        self._path_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._path_lbl.setCursor(QCursor(Qt.PointingHandCursor))
        self._path_lbl.linkActivated.connect(lambda _: _open_file_location(filepath))
        from modules.qt.utils import setup_path_label_context_menu
        setup_path_label_context_menu(self._path_lbl, lambda: filepath, lambda: _open_file_location(filepath))
        layout.addWidget(self._path_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok_btn = QPushButton()
        self._ok_btn.setFixedWidth(100)
        self._ok_btn.clicked.connect(self.accept)
        self._ok_btn.setDefault(True)
        btn_row.addWidget(self._ok_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()

        # Le changement de thème émet aussi language_signal : _retranslate
        # réapplique alors les couleurs.
        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
        if parent is not None:
            from modules.qt.dialogs_qt import _center_on_widget
            _center_on_widget(self, parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def _retranslate(self):
        _apply_confirmation_theme(self, [self._msg], self._path_lbl, "file:///",
                                  self._filepath, [self._ok_btn])
        self.setWindowTitle(_wt(self._title_key))
        self._msg.setText(_(self._message_key))
        self._ok_btn.setText(_("buttons.ok"))
        _apply_confirmation_font([self._msg, self._ok_btn])

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass
        cb = self._on_done
        self._on_done = None
        if cb is not None:
            cb()


class SaveSuccessDialog(QDialog):
    """
    Dialogue de sauvegarde réussie avec chemin cliquable et question de suppression.
    result = True si l'utilisateur choisit Oui (supprimer le fichier d'origine).
    """

    def __init__(self, parent, title_key: str, message_key: str,
                 filepath: str, question_key: str,
                 yes_key: str = "misc.yes", no_key: str = "misc.no",
                 on_done=None, question_kwargs: dict = None):
        super().__init__(parent)
        self.result = False
        self._title_key = title_key
        self._message_key = message_key
        self._question_key = question_key
        self._question_kwargs = question_kwargs or {}
        self._yes_key = yes_key
        self._no_key = no_key
        self._on_done = on_done
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        self._msg = QLabel()
        self._msg.setWordWrap(True)
        self._msg.setAlignment(Qt.AlignLeft)
        layout.addWidget(self._msg)

        # Chemin cliquable (statique)
        self._filepath = filepath
        self._path_lbl = QLabel()
        self._path_lbl.setWordWrap(True)
        self._path_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._path_lbl.setCursor(QCursor(Qt.PointingHandCursor))
        self._path_lbl.linkActivated.connect(lambda _: _open_file_location(filepath))
        from modules.qt.utils import setup_path_label_context_menu
        setup_path_label_context_menu(self._path_lbl, lambda: filepath, lambda: _open_file_location(filepath))
        layout.addWidget(self._path_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep)

        self._question = QLabel()
        self._question.setWordWrap(True)
        self._question.setAlignment(Qt.AlignLeft)
        layout.addWidget(self._question)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._yes_btn = QPushButton()
        self._yes_btn.setFixedWidth(100)
        self._yes_btn.clicked.connect(self._yes)
        btn_row.addWidget(self._yes_btn)
        self._no_btn = QPushButton()
        self._no_btn.setFixedWidth(100)
        self._no_btn.setDefault(True)
        self._no_btn.clicked.connect(self._no)
        btn_row.addWidget(self._no_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.rejected.connect(self._no)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
        self._no_btn.setFocus()
        if parent is not None:
            from modules.qt.dialogs_qt import _center_on_widget
            _center_on_widget(self, parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def _retranslate(self):
        _apply_confirmation_theme(self, [self._msg, self._question], self._path_lbl,
                                  "file:///", self._filepath, [self._yes_btn, self._no_btn])
        self.setWindowTitle(_wt(self._title_key))
        self._msg.setText(_(self._message_key))
        self._question.setText(_(self._question_key, **self._question_kwargs))
        self._yes_btn.setText(_(self._yes_key))
        self._no_btn.setText(_(self._no_key))
        _apply_confirmation_font([self._msg, self._question, self._yes_btn, self._no_btn])

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def _fire_done(self):
        cb = self._on_done
        self._on_done = None
        if cb is not None:
            cb(self.result)

    def _yes(self):
        self.result = True
        self._fire_done()
        self.close()

    def _no(self):
        self.result = False
        self._fire_done()
        self.close()

    def closeEvent(self, event):
        self._fire_done()
        super().closeEvent(event)


class DuplicateNamesErrorDialog(QDialog):
    """Dialogue d'erreur pour les noms de fichiers en double dans apply_new_names."""

    def __init__(self, parent, message_func, title_key: str):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self._title_key = title_key
        self._message_func = message_func
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self._title_lbl = QLabel()
        self._title_lbl.setStyleSheet("color: #d9534f; font-size: 13px; font-weight: bold;")
        self._title_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._title_lbl)

        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg_lbl)

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

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
        if parent is not None:
            from modules.qt.dialogs_qt import _center_on_widget
            _center_on_widget(self, parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def _retranslate(self):
        self.setWindowTitle(_wt(self._title_key))
        self._title_lbl.setText(_(self._title_key))
        self._msg_lbl.setText(self._message_func())
        self._ok_btn.setText(_("buttons.ok"))

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass


class DuplicateFilenameDialog(QDialog):
    """
    Dialogue pour les doublons de noms de fichiers lors d'une sauvegarde.
    result = "renumber" | "ignore" | None (annuler)
    """

    result_signal = Signal(object)   # "renumber" | "ignore" | None

    def __init__(self, parent, duplicate_names):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.result = None
        self._emitted = False
        self.setWindowTitle("")
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self._title_lbl = QLabel()
        self._title_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._title_lbl)

        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignLeft)
        layout.addWidget(self._msg_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._renumber_btn = QPushButton()
        self._renumber_btn.clicked.connect(self._renumber)
        btn_row.addWidget(self._renumber_btn)
        self._ignore_btn = QPushButton()
        self._ignore_btn.clicked.connect(self._ignore)
        btn_row.addWidget(self._ignore_btn)
        self._cancel_btn = QPushButton()
        self._cancel_btn.setDefault(True)
        self._cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
        self._renumber_btn.setFocus()
        self._center_parent = parent

    def _retranslate(self):
        # Appelée aussi par language_signal, émis aux changements de langue,
        # de thème et de taille de police : thème et police réappliqués ici.
        from modules.qt.state import get_current_theme
        from modules.qt.font_manager_qt import get_current_font
        theme = get_current_theme()
        bg, fg = theme['bg'], theme['text']
        alt = theme.get('toolbar_bg', bg)
        sep = theme.get('separator', '#aaaaaa')
        self.setStyleSheet(f"QDialog {{ background: {bg}; }}")
        # Couleur d'avertissement du titre conservée ; police par setFont,
        # jamais par le stylesheet (qui écraserait get_current_font).
        self._title_lbl.setStyleSheet(f"color: #ff9800; background: {bg};")
        self._msg_lbl.setStyleSheet(f"color: {fg}; background: {bg};")
        btn_style = (
            f"QPushButton {{ background: {alt}; color: {fg}; "
            f"border: 1px solid {sep}; padding: 4px 12px; border-radius: 3px; }} "
            f"QPushButton:hover {{ background: {sep}; }}"
        )

        self._title_lbl.setText(_("dialogs.duplicate_filenames.title"))
        self._msg_lbl.setText(_("dialogs.duplicate_filenames.message"))
        self._renumber_btn.setText(_("buttons.renumber_recommended"))
        self._ignore_btn.setText(_("buttons.ignore_and_save"))
        self._cancel_btn.setText(_("buttons.cancel"))

        self._title_lbl.setFont(get_current_font(11, bold=True))
        font = get_current_font()
        for w in (self._msg_lbl, self._renumber_btn, self._ignore_btn, self._cancel_btn):
            w.setFont(font)
        for btn in (self._renumber_btn, self._ignore_btn, self._cancel_btn):
            btn.setStyleSheet(btn_style)
        # Largeur fixe + retour à la ligne automatique : le sizeHint sous-estime
        # la hauteur d'un texte de plusieurs lignes (dernières lignes coupées).
        # Calculée après setFont, la hauteur dépendant de la police.
        margins = self.layout().contentsMargins()
        self._msg_lbl.setMinimumHeight(self._msg_lbl.heightForWidth(
            520 - margins.left() - margins.right()))

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def _finish(self, result):
        if self._emitted:
            return
        self._emitted = True
        self.result = result
        self._on_close()
        self.result_signal.emit(result)
        self.hide()
        self.deleteLater()

    def _renumber(self):
        self._finish("renumber")

    def _ignore(self):
        self._finish("ignore")

    def _cancel(self):
        self._finish(None)

    def closeEvent(self, event):
        if not self._emitted:
            self._emitted = True
            self._on_close()
            self.result_signal.emit(None)
        event.accept()

    def ask_async(self, on_result):
        """Affiche le dialogue (NON modal) et appelle on_result(result) à la réponse.
        result ∈ {'renumber','ignore', None}."""
        from modules.qt.dialogs_qt import position_dialog_on_parent
        self.result_signal.connect(on_result)
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()


class ExistingFilesDialog(QDialog):
    """
    Export de pages vers un dossier : des fichiers de même nom y existent déjà.
    Une seule question pour tous. NON modal, réponse via ask_async(on_result) :
    True (remplacer) ou False (annuler, rien n'est écrit).
    """

    result_signal = Signal(bool)
    _MAX_LISTED = 10
    _WIDTH = 520

    def __init__(self, parent, existing_names):
        super().__init__(parent)
        self._names = list(existing_names)
        self._emitted = False
        self._center_parent = parent
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedWidth(self._WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._replace_btn = QPushButton()
        self._replace_btn.clicked.connect(lambda: self._finish(True))
        btn_row.addWidget(self._replace_btn)
        self._cancel_btn = QPushButton()
        self._cancel_btn.setDefault(True)
        self._cancel_btn.clicked.connect(lambda: self._finish(False))
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()

        # Changement de langue, de thème et de taille de police : tous émettent
        # language_signal.
        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _code: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._disconnect_lang)
        self._cancel_btn.setFocus()

    def _retranslate(self):
        from modules.qt.state import get_current_theme
        from modules.qt.font_manager_qt import get_current_font
        theme = get_current_theme()
        bg, fg = theme['bg'], theme['text']
        alt = theme.get('toolbar_bg', bg)
        sep = theme.get('separator', '#aaaaaa')
        self.setStyleSheet(f"QDialog {{ background: {bg}; }}")
        self._msg_lbl.setStyleSheet(f"color: {fg}; background: {bg};")
        btn_style = (
            f"QPushButton {{ background: {alt}; color: {fg}; "
            f"border: 1px solid {sep}; padding: 4px 12px; border-radius: 3px; }} "
            f"QPushButton:hover {{ background: {sep}; }}"
        )

        self.setWindowTitle(_wt("dialogs.existing_files.title"))
        names = "\n".join(self._names[:self._MAX_LISTED])
        if len(self._names) > self._MAX_LISTED:
            names += "\n" + _("dialogs.existing_files.more",
                              more=len(self._names) - self._MAX_LISTED)
        self._msg_lbl.setText(_("dialogs.existing_files.message",
                                count=len(self._names), names=names))
        self._replace_btn.setText(_("dialogs.existing_files.replace"))
        self._cancel_btn.setText(_("buttons.cancel"))

        font = get_current_font()
        for w in (self._msg_lbl, self._replace_btn, self._cancel_btn):
            w.setFont(font)
        for btn in (self._replace_btn, self._cancel_btn):
            btn.setStyleSheet(btn_style)
        # QLabel WordWrap dans une fenêtre à largeur fixe : son sizeHint
        # sous-estime la hauteur des messages sur plusieurs lignes.
        margins = self.layout().contentsMargins()
        self._msg_lbl.setMinimumHeight(self._msg_lbl.heightForWidth(
            self._WIDTH - margins.left() - margins.right()))

    def _disconnect_lang(self, *_args):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except (RuntimeError, TypeError):
            pass

    def _finish(self, result):
        if self._emitted:
            return
        self._emitted = True
        # Fermeture par hide() + deleteLater() : finished n'est pas émis,
        # la déconnexion de language_signal doit être faite ici.
        self._disconnect_lang()
        self.result_signal.emit(result)
        self.hide()
        self.deleteLater()

    def closeEvent(self, event):
        if not self._emitted:
            self._emitted = True
            self._disconnect_lang()
            self.result_signal.emit(False)
        event.accept()

    def ask_async(self, on_result):
        """Affiche la question (NON modale) et appelle on_result(result)."""
        from modules.qt.dialogs_qt import position_dialog_on_parent
        self.result_signal.connect(on_result)
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()


# ═══════════════════════════════════════════════════════════════════════════════
# FileSavedDialog
# ═══════════════════════════════════════════════════════════════════════════════

class FileSavedDialog(QDialog):
    """Dialogue de confirmation avec lien cliquable vers le dossier."""

    def __init__(self, parent, count: int, folder: str, skipped: int = 0):
        super().__init__(parent)
        self._count = count
        self._folder = folder
        self._skipped = skipped
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        self._msg = QLabel()
        self._msg.setWordWrap(True)
        self._msg.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg)

        # Lien vers le dossier
        self._display = folder if len(folder) <= 60 else "..." + folder[-57:]
        self._link_lbl = QLabel()
        self._link_lbl.setAlignment(Qt.AlignCenter)
        self._link_lbl.setCursor(QCursor(Qt.PointingHandCursor))
        self._link_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._link_lbl.linkActivated.connect(lambda _: _open_folder(folder))
        from modules.qt.utils import setup_path_label_context_menu
        setup_path_label_context_menu(self._link_lbl, lambda: folder, lambda: _open_folder(folder))
        layout.addWidget(self._link_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._close_btn = QPushButton()
        self._close_btn.setFixedWidth(120)
        self._close_btn.setDefault(True)
        self._close_btn.clicked.connect(self.close)
        btn_row.addWidget(self._close_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
        if parent is not None:
            from modules.qt.dialogs_qt import _center_on_widget
            _center_on_widget(self, parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def _retranslate(self):
        _apply_confirmation_theme(self, [self._msg], self._link_lbl, "folder",
                                  self._display, [self._close_btn])
        self.setWindowTitle(_wt("messages.info.files_saved.title"))
        if self._skipped > 0:
            self._msg.setText(_("messages.info.files_saved.message_with_skipped",
                                count=self._count, folder=self._folder, skipped=self._skipped))
        else:
            self._msg.setText(_("messages.info.files_saved.message", count=self._count, folder=self._folder))
        self._close_btn.setText(_("buttons.close"))
        _apply_confirmation_font([self._msg, self._close_btn])

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass


class ThumbnailSavedDialog(QDialog):
    """Dialogue de confirmation après export des vignettes, avec lien cliquable vers le dossier."""

    def __init__(self, parent, count: int, folder: str):
        super().__init__(parent)
        self._count = count
        self._folder = folder
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        self._msg = QLabel()
        self._msg.setWordWrap(True)
        self._msg.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg)

        # Lien vers le dossier
        from modules.qt.state import get_current_theme
        link_color = get_current_theme().get("link", "#4A9EFF")
        display = folder if len(folder) <= 60 else "..." + folder[-57:]
        self._link_lbl = QLabel(f'<a href="folder" style="color:{link_color};">{display}</a>')
        self._link_lbl.setAlignment(Qt.AlignCenter)
        self._link_lbl.setCursor(QCursor(Qt.PointingHandCursor))
        self._link_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._link_lbl.linkActivated.connect(lambda _: _open_folder(folder))
        from modules.qt.utils import setup_path_label_context_menu
        setup_path_label_context_menu(self._link_lbl, lambda: folder, lambda: _open_folder(folder))
        layout.addWidget(self._link_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._close_btn = QPushButton()
        self._close_btn.setFixedWidth(120)
        self._close_btn.setDefault(True)
        self._close_btn.clicked.connect(self.close)
        btn_row.addWidget(self._close_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
        if parent is not None:
            from modules.qt.dialogs_qt import _center_on_widget
            _center_on_widget(self, parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def _retranslate(self):
        from modules.qt.font_manager_qt import get_current_font
        font = get_current_font()
        self.setWindowTitle(_wt("messages.info.thumbnails_saved.title"))
        self._msg.setText(_("messages.info.thumbnails_saved.message", count=self._count))
        self._msg.setFont(font)
        self._close_btn.setText(_("buttons.close"))
        self._close_btn.setFont(font)

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Overlay de progression sur le canvas Qt
# ═══════════════════════════════════════════════════════════════════════════════

class _SavingOverlay:
    """Texte rouge « Sauvegarde X% » pendant l'écriture du document, par le
    mécanisme commun des textes de progression (canvas_overlay_qt) : empilé
    avec les autres textes en cours (import web, numérisation…) au lieu de
    les recouvrir. Le label est un widget posé sur le canvas, pas un item de
    la scène : une reconstruction de la mosaïque pendant l'écriture (entrée
    dans un sous-dossier, taille des vignettes…) ne le détruit pas."""

    def __init__(self, canvas):
        self._canvas = canvas
        self._holder = [None]
        self.update(0, process_events=False)

    def update(self, percent: int, process_events: bool = True):
        from modules.qt.canvas_overlay_qt import show_canvas_text
        show_canvas_text(self._canvas, _("labels.saving_cbz", percent=percent), self._holder)
        if process_events:
            QApplication.processEvents()

    def remove(self):
        from modules.qt.canvas_overlay_qt import hide_canvas_text
        hide_canvas_text(self._canvas, self._holder)


# ═══════════════════════════════════════════════════════════════════════════════
# Vérifications pré-sauvegarde (portées de file_operations.py)
# ═══════════════════════════════════════════════════════════════════════════════

def _has_parent_dir_segment(filename):
    """True si un segment du chemin vaut `..` (« dossier parent ») : écrit dans
    le CBZ, ce nom ferait sortir le fichier du dossier d'extraction chez un
    lecteur ou un outil d'archive. Des points dans un nom (`Fin...jpg`,
    `Chapitre 1..2.jpg`) sont légitimes : seul le segment entier compte."""
    return any(part == ".." for part in filename.replace("\\", "/").split("/"))


def _without_parent_dir_segments(filename):
    """`filename` sans ses segments `..` (même découpage que
    _has_parent_dir_segment) ; "" s'il n'en reste rien."""
    return "/".join(part for part in filename.replace("\\", "/").split("/") if part != "..")


def _validate_filenames_qt(parent, render_mosaic, on_done, state=None):
    """
    Valide les noms de fichiers (caractères interdits, etc.). NON modal.
    Appelle on_done(True) si on peut continuer, on_done(False) si annulation.

    Détection des sous-répertoires : un nom contenant '/' (et qui n'est pas une
    entrée dossier) signifie une structure de sous-dossiers légitime — dans ce cas
    le '/' n'est PAS un caractère interdit. Détection canonique du projet, identique
    à has_subdirectory_structure (basée sur orig_name, pas sur la présence d'entrées
    is_dir).
    """
    if state is None:
        state = _state_module.state
    invalid_chars = '<>:"|?*'
    has_subdirs = any(
        '/' in e.get("orig_name", "") and not e.get("is_dir")
        for e in state.images_data
    )
    invalid_files = []

    for idx, entry in enumerate(state.images_data):
        if entry.get("is_dir"):
            continue
        filename = entry["orig_name"]
        parts = filename.replace("\\", "/").split("/")
        has_invalid = False
        empty_parts = [i for i, p in enumerate(parts) if not p]
        if empty_parts:
            has_invalid = True
        if not has_subdirs and len(parts) > 1:
            has_invalid = True
        for part in parts:
            if not part:
                continue
            if any(char in part for char in invalid_chars):
                has_invalid = True
                break
        has_control = any(ord(c) < 32 for c in filename)
        has_path_traversal = _has_parent_dir_segment(filename)
        if has_invalid or has_control or has_path_traversal:
            invalid_files.append({"index": idx, "original": filename, "entry": entry})

    if not invalid_files:
        on_done(True)
        return

    # Prépare les callables pour que le message se retraduit à la volée
    chars_str = invalid_chars if has_subdirs else (invalid_chars + " / \\")

    def _build_msg():
        m = _("messages.questions.invalid_filenames.intro", count=len(invalid_files)) + "\n\n"
        display_count = min(10, len(invalid_files))
        for i in range(display_count):
            m += _("messages.questions.invalid_filenames.item", name=invalid_files[i]['original']) + "\n"
        if len(invalid_files) > 10:
            m += _("messages.questions.invalid_filenames.more", more=len(invalid_files) - 10) + "\n"
        m += "\n" + _("messages.questions.invalid_filenames.forbidden_chars", chars=chars_str)
        m += "\n\n" + _("messages.questions.invalid_filenames.fix_question")
        return m

    def _on_reply(reply):
        if reply == "cancel":
            on_done(False)
            return

        if reply == "yes":
            for item in invalid_files:
                entry = item["entry"]
                filename = item["original"]
                cleaned = _without_parent_dir_segments(filename)
                if has_subdirs:
                    parts = [p for p in cleaned.split("/") if p]
                    cleaned = "/".join(parts)
                else:
                    cleaned = cleaned.replace("/", "_")
                for char in invalid_chars:
                    cleaned = cleaned.replace(char, "_")
                cleaned = "".join(c if ord(c) >= 32 else "_" for c in cleaned)
                if not cleaned:
                    cleaned = "fichier" + entry["extension"]
                entry["orig_name"] = cleaned
            render_mosaic()
            info_dlg = InfoDialog(
                parent,
                lambda: _wt("messages.info.correction_done.title"),
                lambda c=len(invalid_files): _("messages.info.correction_done.message", count=c),
            )
            info_dlg.finished.connect(lambda _r: on_done(True))
            info_dlg.show_nonmodal()
            return

        # Non — ne pas corriger
        ErrorDialog(
            parent,
            lambda: _wt("messages.warnings.save_cancelled.title"),
            lambda: _("messages.warnings.save_cancelled.message"),
        ).show()
        on_done(False)

    QuestionYNCDialog(
        parent,
        lambda: _wt("messages.questions.invalid_filenames.title"),
        _build_msg,
    ).ask_async(_on_reply)


def _auto_update_page_count_qt(state=None):
    """
    Met à jour silencieusement PageCount dans le ComicInfo.xml si le nombre de pages a changé.
    Appelé automatiquement avant chaque sauvegarde.
    Note : sync_pages_in_xml_data() gère déjà PageCount lors des ajouts/suppressions.
    Ceci couvre les cas résiduels (modifications sans changement de liste).
    """
    from modules.qt.comic_info import update_page_count_in_xml_data
    if state is None:
        state = _state_module.state
    if not state.comic_metadata or not state.original_page_count:
        return
    current_count = len([e for e in state.images_data if e.get("is_image") and not e.get("is_dir")])
    if current_count != state.original_page_count:
        update_page_count_in_xml_data(state, current_count)


def _apply_name_edits(state):
    """
    Recopie dans orig_name le nom affiché par le champ de chaque vignette
    (NameEdit), avant toute sauvegarde du document.

    Le champ met déjà orig_name à jour à chaque frappe ; cette recopie retire
    en plus les espaces en bord de nom. Un champ vidé garde le nom en mémoire :
    sans ce garde-fou, la page s'appellerait « .jpg » dans le CBZ.
    """
    for entry in state.images_data:
        name_entry = entry.get("name_entry")
        if name_entry is None:
            continue
        try:
            base = name_entry.toPlainText().strip()
        except AttributeError:
            base = str(name_entry).strip()
        except RuntimeError:
            continue  # champ détruit par une reconstruction de la mosaïque
        if not base:
            continue
        ext = entry["extension"]
        entry["orig_name"] = base if base.lower().endswith(ext.lower()) else base + ext


def _run_validation_chain(steps, on_all_passed, state=None):
    """
    Enchaîne des validations NON modales (callbacks) et n'exécute on_all_passed()
    que si toutes réussissent. Chaque step est un callable step(on_done) qui appelle
    on_done(True) pour continuer ou on_done(False) pour interrompre la chaîne.

    state : document enregistré. La chaîne s'arrête en silence s'il a été fermé
    ou remplacé entre deux étapes (AppState.doc_generation) : la fermeture du
    document ferme aussi la fenêtre d'une étape, et une fenêtre d'information
    fermée vaut « continuer » ; la suite enregistrerait alors l'état vidé
    (ou le document suivant) au lieu du document validé.
    """
    generation = getattr(state, "doc_generation", None)

    def _document_changed():
        return state is not None and state.doc_generation != generation

    def _run(index):
        if _document_changed():
            return
        if index >= len(steps):
            on_all_passed()
            return

        def _on_step_done(ok):
            if ok:
                _run(index + 1)
            # ok=False : on interrompt silencieusement (le dialogue a déjà informé)

        steps[index](_on_step_done)

    _run(0)


def _has_animated_gifs(state=None):
    """Retourne True si la liste d'images contient au moins une image animée
    (GIF, WebP, PNG ou AVIF) : les lecteurs de BD n'en affichent que la
    première frame."""
    if state is None:
        state = _state_module.state
    return any(is_animated_entry(entry) for entry in state.images_data)


def _check_animated_gifs_qt(parent, on_done, state=None):
    """
    Vérifie la présence d'images animées et affiche l'avertissement. NON modal.
    Appelle on_done(True) si on peut continuer, on_done(False) si annulation.
    """
    if not _has_animated_gifs(state):
        on_done(True)
        return
    from modules.qt.dialogs_qt import ConfirmDialog
    ConfirmDialog(
        parent,
        "messages.warnings.animated_gif_in_comic.title",
        "messages.warnings.animated_gif_in_comic.message",
    ).ask_async(on_done)


def _handle_duplicate_filenames_qt(parent, renumber_func, on_done, entries_to_check=None, state=None):
    """
    Vérifie les doublons et affiche le dialogue si nécessaire. NON modal.
    Appelle on_done(True) pour continuer, on_done(False) pour annuler.
    """
    has_duplicates, duplicate_names = detect_duplicate_filenames_for_save(entries_to_check, state)
    if not has_duplicates:
        on_done(True)
        return

    def _on_result(result):
        if result == "renumber":
            if renumber_func:
                renumber_func()
            on_done(True)
        elif result == "ignore":
            on_done(True)
        else:
            on_done(False)

    DuplicateFilenameDialog(parent, duplicate_names).ask_async(_on_result)


def _register_document_file(parent, callbacks, filepath):
    """Le document du panneau porte désormais `filepath` (création, Enregistrer
    sous, conversion) : ajout aux fichiers récents et titre de la fenêtre."""
    from modules.qt import recent_files as _recent_files_module
    _recent_files_module.add_to_recent_files(filepath)
    if hasattr(parent, "_main_window") and hasattr(parent._main_window, "_sync_recent_menus"):
        parent._main_window._sync_recent_menus()
    if callbacks.get("update_window_title"):
        callbacks["update_window_title"]()


def _claim_files_for_write(parent, paths):
    """Réserve les fichiers que l'écriture va lire, remplacer ou créer auprès
    des traitements par lot (batch_claims), le temps de l'écriture : un lot
    (conversion, import des métadonnées) qui traite l'un d'eux pourrait le
    réécrire ou le supprimer pendant cette écriture, ou la remplacer par une
    version lue avant elle. Rend le BatchClaims à libérer (release_all) après
    l'écriture, ou None, avec un message, si un lot détient l'un des fichiers."""
    from modules.qt.batch_claims import BatchClaims
    claims = BatchClaims()
    for path in paths:
        if not claims.claim(path):
            claims.release_all()
            ErrorDialog(parent,
                        lambda: _wt("messages.errors.save_failed.title"),
                        lambda name=os.path.basename(path):
                            _("messages.errors.file_in_batch", name=name)).show_nonmodal()
            return None
    return claims


def _document_still_writable(state):
    """Suite différée d'un enregistrement (réponse à une validation ou à une
    confirmation non modale) : False si une opération en arrière-plan
    (rotation, redimensionnement…) a démarré sur le document entre-temps.
    Écrit maintenant, le fichier contiendrait des pages à moitié traitées
    (panel_widget.BUSY_BLOCKED_COMMANDS). Sans effet : l'utilisateur
    relance l'enregistrement une fois l'opération terminée."""
    return not state.operation_running()


class _DocumentChangedDuringSave(Exception):
    """Le document du panneau a été fermé ou remplacé pendant son écriture."""


def _write_document_with_progress(filepath, state, overlay, compression_level, entries=None):
    """Écrit tout le document du panneau dans `filepath` (via un fichier
    temporaire), avec la progression rouge sur le canvas. `entries` : pages
    à écrire à la place du document entier (Enregistrer la sélection),
    sans le commentaire d'archive du fichier ouvert.

    Tout enregistrement d'un CBZ depuis un panneau passe par là : écrit d'un
    bloc, sans traiter les événements, un gros document figerait les deux
    panneaux jusqu'à la fin de l'écriture.

    _SavingOverlay.update traite les événements entre deux pages pour que
    l'application, dont l'autre panneau, reste utilisable : le document peut donc
    être fermé ou remplacé en cours d'écriture, ses pages restantes étant alors
    vidées. L'écriture est dans ce cas abandonnée (_DocumentChangedDuringSave,
    `filepath` intact) au lieu de produire un CBZ incomplet annoncé comme réussi.

    Retourne False si une action a été enregistrée dans l'historique pendant
    l'écriture : le fichier écrit ne reflète alors pas forcément l'état courant,
    qui ne doit pas être marqué sauvegardé.

    L'écriture est inscrite comme opération en cours du document
    (state.begin_operation) : pendant ces traitements d'événements, les
    commandes qui modifient ou enregistrent le document (BUSY_BLOCKED_COMMANDS
    de panel_widget, dont un second enregistrement) sont sans effet. Sans ce
    verrou, un second clic sur Enregistrer lancerait une écriture imbriquée
    du même fichier, et une suppression ou un déplacement de pages changerait
    la liste en cours d'écriture.

    Un ajout arrivé pendant l'écriture (numérisation, import web…) attend la
    fin du verrou (AppState.run_when_idle) et est appliqué par end_operation :
    absent du fichier écrit, il interdit de marquer le document sauvegardé."""
    token = object()
    state.begin_operation(token)
    try:
        unchanged = _write_document_unlocked(filepath, state, overlay, compression_level, entries)
    finally:
        waiting = bool(state.pending_when_idle)
        state.end_operation(token)
    applied_after_write = waiting and not state.pending_when_idle
    return unchanged and not applied_after_write


def _write_cbz_with_overlay(canvas, filepath, state, compression_level, entries=None):
    """_write_document_with_progress avec le texte « Sauvegarde X% » sur
    `canvas` (retiré à la fin). False, sans rien écrire ni message, si une
    opération a démarré sur le document depuis la validation (boîte
    d'enregistrement native) ou si le document a été fermé ou remplacé pendant
    l'écriture ; une erreur d'écriture est propagée à l'appelant."""
    if not _document_still_writable(state):
        return False
    overlay = _SavingOverlay(canvas) if canvas else None
    try:
        _write_document_with_progress(filepath, state, overlay, compression_level, entries)
    except _DocumentChangedDuringSave:
        return False
    finally:
        if overlay:
            overlay.remove()
    return True


def _write_document_unlocked(filepath, state, overlay, compression_level, entries=None):
    """Corps de _write_document_with_progress, appelé sous son verrou."""
    generation = state.doc_generation
    top_before = history_top(state)
    if entries is None:
        entries = state.images_data
        # Lu avant l'écriture : `filepath` peut être le fichier ouvert lui-même.
        comment = _zip_comment(state.current_file) if state.current_file else b""
    else:
        comment = b""

    def _write(tmp_path):
        try:
            written_count = _write_zip_with_progress(tmp_path, entries, overlay,
                                                     compression_level, comment=comment)
        except MissingPageDataError:
            # Pages vidées par force_close_file pendant l'écriture
            if state.doc_generation != generation:
                raise _DocumentChangedDuringSave()
            raise
        if state.doc_generation != generation:
            raise _DocumentChangedDuringSave()
        # Contrôlé avant de remplacer `filepath` : un CBZ incomplet ne prend
        # jamais la place du fichier existant, et l'appelant, qui reçoit
        # l'exception, ne marque pas le document comme enregistré.
        check_written_cbz_structure(tmp_path, written_count, shown_path=filepath)

    replace_file_atomically(filepath, _write)
    return history_top(state) is top_before


def _confirm_if_changed_on_disk(parent, state, filepath, proceed, cancel):
    """Avant de réécrire `filepath`, le fichier du document (state.current_file) :
    s'il a changé sur le disque depuis que ce panneau l'a lu ou écrit
    (state.disk_signature), il a été modifié ailleurs — autre panneau ouvert sur
    le même fichier, Bibliothèque, import de métadonnées en lot, autre programme —
    et l'enregistrer remplacerait ces modifications. Demande alors confirmation
    (NON modal, « Non » par défaut) ; sinon appelle proceed() directement.

    Abandonne (cancel()) si le document a été fermé ou remplacé avant la réponse :
    écrire ses pages, vidées par force_close_file, détruirait le fichier."""
    known = getattr(state, "disk_signature", None)
    current = file_signature(filepath)
    if known is None or current is None or current == known:
        proceed()
        return

    from modules.qt.dialogs_qt import ConfirmYNDialog, position_dialog_on_parent
    generation = state.doc_generation
    dlg = ConfirmYNDialog(
        parent,
        lambda: _wt("messages.questions.file_changed_on_disk.title"),
        lambda name=os.path.basename(filepath):
            _("messages.questions.file_changed_on_disk.message", file=name),
    )
    # Question destructive : Entrée ne doit pas écraser par réflexe.
    dlg._btn_yes.setDefault(False)
    dlg._btn_no.setDefault(True)
    dlg._btn_no.setFocus()

    answered = [False]

    def _on_answer(confirmed):
        # Une seule réponse : une fenêtre cachée mais pas encore détruite peut
        # recevoir un close() (fermeture du document) et réémettre False.
        if answered[0]:
            return
        answered[0] = True
        if confirmed and state.doc_generation == generation:
            proceed()
        else:
            cancel()

    dlg.result_signal.connect(_on_answer)
    position_dialog_on_parent(dlg, parent)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


def _check_disk_space(parent, dest, entries):
    """Vérifie, avant toute écriture, qu'il y a la place d'écrire le CBZ complet
    à côté de `dest` : le fichier temporaire et l'éventuel fichier existant
    coexistent jusqu'au remplacement. Affiche l'erreur et retourne False sinon."""
    needed = estimate_cbz_size(entries)
    try:
        free = shutil.disk_usage(os.path.dirname(os.path.abspath(dest))).free
    except OSError:
        return True  # dossier inaccessible : l'écriture échouera avec son propre message
    if needed <= free:
        return True
    _needed = format_file_size(needed)
    _free = format_file_size(free)
    ErrorDialog(parent,
                lambda: _wt("messages.errors.save_failed.title"),
                lambda n=_needed, fr=_free: _("messages.errors.disk_full.message",
                                              needed=n, free=fr),
                play_sound=True).show()
    return False


def _write_file_safely(filepath, data):
    """Écrit `data` dans `filepath` via un temporaire du même dossier
    (replace_file_atomically) : un échec en cours d'écriture ne tronque jamais
    un fichier existant de ce nom. Crée le dossier parent si besoin."""
    file_dir = os.path.dirname(filepath)
    if file_dir and not os.path.exists(file_dir):
        os.makedirs(file_dir, exist_ok=True)

    def _write(tmp_path):
        with open(tmp_path, "wb") as f:
            f.write(data)

    replace_file_atomically(filepath, _write)


def _check_pages_have_data(parent, entries):
    """Refuse l'enregistrement si une entrée n'a pas de données (fichier
    illisible à l'ouverture de l'archive) : l'écrire sans elle la perdrait.
    Affiche la liste des fichiers concernés et retourne False."""
    try:
        cbz_members(entries)
    except MissingPageDataError as e:
        ErrorDialog(parent,
                    lambda: _wt("messages.errors.save_failed.title"),
                    lambda err=e: str(err)).show_nonmodal()
        return False
    return True


def _zip_comment(path):
    """Commentaire d'archive du ZIP `path` (b"" si absent, si `path` n'est pas
    un ZIP ou n'existe pas). Ce champ de texte libre de l'archive porte parfois
    des métadonnées (ComicBookInfo, écrit par ComicRack ou ComicTagger) :
    toute réécriture du CBZ doit le recopier, sinon elles sont perdues."""
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.comment
    except (OSError, zipfile.BadZipFile, TypeError, ValueError):
        return b""


def _bytes_with_entry_dpi(entry):
    """Bytes à écrire dans le CBZ pour `entry`, portant sa résolution
    entry["dpi"] quand le fichier ne la contient pas (pages issues d'un PDF :
    dpi = résolution de rendu, absente des bytes). La page en mémoire n'est
    jamais modifiée (hors historique).

    Seulement pour les formats dont save_image_to_bytes écrit le DPI : pour
    WebP, AVIF, GIF, BMP, ICO, le DPI relu ne correspondrait jamais et l'image
    serait ré-encodée à chaque sauvegarde. Jamais pour une image animée :
    save_image_to_bytes n'en écrirait que la première frame. JPEG : densité
    écrite dans l'en-tête (jpeg_bytes_with_dpi), sans ré-encodage avec perte ;
    PNG/TIFF : ré-encodage sans perte."""
    data = entry["bytes"]
    ext = entry.get("extension", "").lower()
    dpi = entry.get("dpi")
    if isinstance(dpi, tuple):
        dpi = dpi[0]
    if not dpi or entry.get("img") is not None or ext not in _DPI_WRITABLE_EXTS \
            or has_several_frames(entry):
        return data
    try:
        with Image.open(io.BytesIO(data)) as tmp:
            existing = tmp.info.get("dpi")
        if isinstance(existing, tuple):
            existing = existing[0]
        if existing and abs(existing - dpi) < 1:
            return data
        if ext in _JPEG_EXTS:
            return jpeg_bytes_with_dpi(data, dpi) or data
        copy = dict(entry)
        # Dans le sens d'affichage : save_image_to_bytes réécrit l'EXIF sans
        # l'orientation
        copy["img"] = open_page_image(data)
        return save_image_to_bytes(copy)
    except Exception:
        return data


def _write_zip_with_progress(filepath, images_data, overlay, compression_level=0, comment=b""):
    """Écrit les entrées (hors dossiers) dans un nouveau ZIP, sous des noms
    uniques (cbz_members, qui lève MissingPageDataError pour une entrée sans
    données), avec la résolution de chaque page (_bytes_with_entry_dpi), et
    met à jour l'overlay. comment : commentaire d'archive à conserver
    (_zip_comment du CBZ d'origine). Retourne le nombre de fichiers écrits."""
    from modules.qt.utils import zip_compression_kwargs
    # Liste figée (cbz_members en fait une copie) : overlay.update traite les
    # événements, et une page supprimée entre-temps décalerait l'itération.
    members = cbz_members(images_data)
    total = len(members)
    processed = 0
    with zipfile.ZipFile(filepath, "w", **zip_compression_kwargs(compression_level)) as zf:
        zf.comment = comment
        for name, entry in members:
            if entry["bytes"] is None:
                # Vidée pendant l'écriture (fermeture du document) :
                # _write_document_with_progress abandonne l'écriture.
                raise MissingPageDataError([entry.get("orig_name", "")])
            zf.writestr(name, _bytes_with_entry_dpi(entry))
            processed += 1
            if overlay and total > 0:
                overlay.update(int(processed / total * 100))
    return total


def _get_save_filename(parent, title: str, path: str, filter_str: str) -> str:
    """
    Remplace QFileDialog.getSaveFileName() statique.
    Utilise le dialog natif Windows mais le recentre sur `parent` via Win32.
    Retourne le chemin choisi (str) ou "" si annulé.
    """
    import ctypes
    import ctypes.wintypes
    import threading
    import time
    from PySide6.QtCore import QPoint

    user32 = ctypes.windll.user32

    # Calcul de la zone cible (centre du widget parent) en coordonnées écran
    top_left = parent.mapToGlobal(QPoint(0, 0))
    target_cx = top_left.x() + parent.width() // 2
    target_cy = top_left.y() + parent.height() // 2

    stop_event = threading.Event()

    def _reposition_thread():
        buf_title = ctypes.create_unicode_buffer(512)
        buf_class = ctypes.create_unicode_buffer(64)
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)

        # 1. Trouver le hwnd du dialog
        found_hwnd = None
        for _ in range(100):
            if stop_event.is_set():
                return
            def _enum_cb(hwnd, _):
                nonlocal found_hwnd
                user32.GetClassNameW(hwnd, buf_class, 64)
                if buf_class.value != "#32770":
                    return True
                user32.GetWindowTextW(hwnd, buf_title, 512)
                if buf_title.value == title:
                    found_hwnd = hwnd
                    return False
                return True
            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
            user32.EnumWindows(EnumWindowsProc(_enum_cb), 0)
            if found_hwnd:
                break
            time.sleep(0.01)
        if not found_hwnd:
            return

        # 2. Attendre que la fenêtre soit visible, puis la déplacer
        rect = ctypes.wintypes.RECT()
        for _ in range(100):
            if stop_event.is_set():
                return
            if user32.IsWindowVisible(found_hwnd):
                break
            time.sleep(0.01)

        user32.GetWindowRect(found_hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w > 100 and h > 100:
            x = max(0, min(target_cx - w // 2, sw - w))
            y = max(0, min(target_cy - h // 2, sh - h))
            user32.SetWindowPos(found_hwnd, None, x, y, 0, 0, 0x0001 | 0x0004)

    t = threading.Thread(target=_reposition_thread, daemon=True)
    t.start()

    filepath, _filter = QFileDialog.getSaveFileName(parent, title, path, filter_str)

    stop_event.set()
    return filepath or ""


# ═══════════════════════════════════════════════════════════════════════════════
# 1. save_as_cbz — Enregistrer sous (nouveau fichier CBZ)
# ═══════════════════════════════════════════════════════════════════════════════

def save_as_cbz(parent, canvas, callbacks: dict):
    """
    Sauvegarde le document, avec ses modifications éventuelles, sous un
    nouveau fichier CBZ.
    Actif si : archive ouverte, modifiée ou non.
    """
    # callbacks["state"] (state explicite du panneau) plutôt que le singleton :
    # cette fonction peut être appelée en différé (réponse à un dialogue non
    # modal), alors que le singleton pointe déjà sur l'autre panneau.
    state = callbacks.get("state") or _state_module.state
    if not state.current_file or not state.images_data:
        return
    if state.operation_running():
        return

    if not _check_no_ico(parent, state):
        return
    if not _check_no_video(parent, state):
        return
    if not _check_pages_have_data(parent, state.images_data):
        return

    _auto_update_page_count_qt(state)
    _apply_name_edits(state)

    render_mosaic = callbacks.get("render_mosaic", lambda: None)
    renumber_func = callbacks.get("renumber_btn_action")

    def _on_validated():
        if not _document_still_writable(state):
            return
        initial_dir = os.path.dirname(os.path.abspath(state.current_file))
        if not initial_dir:
            initial_dir = get_config_manager().get('last_open_dir', "")
        initial_name = os.path.splitext(os.path.basename(state.current_file))[0] + ".cbz"
        filepath = _get_save_filename(
            parent,
            _wt("buttons.save_as"),
            os.path.join(initial_dir, initial_name),
            "Comic Book Archive (*.cbz)",
        )
        if not filepath:
            return
        get_config_manager().set('last_open_dir', os.path.dirname(os.path.abspath(filepath)))
        same_file = is_same_path(filepath, state.current_file)
        # Fichier ouvert qui n'est pas un CBZ (CBR, CB7, CBT, EPUB, PDF) et
        # choisi comme destination : il serait remplacé par un CBZ gardant son
        # extension, l'original étant perdu. Refusé comme dans apply_new_names.
        if os.path.splitext(state.current_file)[1].lower() != ".cbz" \
                and _is_conversion_source(parent, filepath, state):
            return
        if not _check_disk_space(parent, filepath, state.images_data):
            return

        def _write_and_finish():
            if not _document_still_writable(state):
                return
            claims = _claim_files_for_write(parent, [filepath, state.current_file])
            if claims is None:
                return
            overlay = _SavingOverlay(canvas) if canvas else None
            comp_level = parent._zip_compression_config().get_zip_compression_level() if hasattr(parent, "_zip_compression_config") else 0
            try:
                unchanged = _write_document_with_progress(filepath, state, overlay, comp_level)
            except _DocumentChangedDuringSave:
                return  # document fermé ou remplacé pendant l'écriture : rien n'est enregistré
            except Exception as e:
                if overlay:
                    overlay.remove()
                ErrorDialog(parent,
                            lambda: _wt("messages.errors.save_failed.title"),
                            lambda err=e: _("messages.errors.save_failed.message", error=err),
                            play_sound=True).show()
                return
            finally:
                if overlay:
                    overlay.remove()
                claims.release_all()

            # Nombre de fichiers écrits, contrôlé à la relecture avant de
            # supprimer l'ancien fichier (_after_save_as).
            written_count = len(cbz_members(state.images_data))
            written_generation = state.doc_generation
            old_file = state.current_file
            state.current_file = filepath
            state.disk_signature = file_signature(filepath)
            if unchanged:
                state.modified = False
                mark_history_saved(state)
            state.zip_compression_state = "stored" if comp_level <= 0 else "deflated"
            _register_document_file(parent, callbacks, filepath)
            if hasattr(parent, "_update_status_bar"):
                parent._update_status_bar()
            if callbacks.get("update_button_text"):
                callbacks["update_button_text"]()
            if callbacks.get("update_tabs"):
                callbacks["update_tabs"]()

            def _after_save_as(result):
                if result:
                    try:
                        if old_file and os.path.exists(old_file):
                            verify_written_cbz(filepath, written_count)
                            _safe_delete(old_file)
                    except WrittenFileCheckError as e:
                        _mark_save_unverified(parent, callbacks, state, written_generation)
                        ErrorDialog(parent,
                                    lambda: _wt("messages.errors.save_failed.title"),
                                    lambda err=e: str(err),
                                    play_sound=True).show()
                    except Exception as e:
                        ErrorDialog(parent,
                                    lambda: _wt("messages.errors.save_failed.title"),
                                    lambda err=e: _("messages.errors.delete_error", error=err),
                                    play_sound=True).show()

            if callbacks.get("on_file_saved"):
                callbacks["on_file_saved"](filepath)
            render_mosaic()
            if same_file:
                # Enregistré par-dessus le fichier ouvert : il n'y a pas d'ancien
                # fichier distinct, proposer sa suppression supprimerait celui
                # qu'on vient d'écrire.
                InfoDialogClickablePath(parent,
                                        "messages.info.cbz_saved.title",
                                        "messages.info.cbz_saved.message",
                                        filepath)
                return
            old_ext = os.path.splitext(old_file)[1].lstrip(".").upper() if old_file else ""
            SaveSuccessDialog(
                parent,
                "messages.info.new_cbz_saved.title",
                "messages.info.new_cbz_saved.message",
                filepath,
                "messages.info.new_cbz_saved.question",
                on_done=_after_save_as,
                question_kwargs={"ext": old_ext},
            )

        # Par-dessus le fichier ouvert : le remplacer peut détruire des
        # modifications faites ailleurs depuis son ouverture. Un autre fichier
        # existant a déjà fait l'objet de la confirmation d'écrasement native.
        if same_file:
            _confirm_if_changed_on_disk(parent, state, filepath, _write_and_finish, lambda: None)
        else:
            _write_and_finish()

    _run_validation_chain(
        [
            lambda done: _validate_filenames_qt(parent, render_mosaic, done, state),
            lambda done: _check_animated_gifs_qt(parent, done, state),
            lambda done: _handle_duplicate_filenames_qt(parent, renumber_func, done, state=state),
        ],
        on_all_passed=_on_validated,
        state=state,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. save_selection_as_cbz — Enregistrer la sélection en CBZ
# ═══════════════════════════════════════════════════════════════════════════════

def save_selection_as_cbz(parent, callbacks: dict, canvas=None):
    """
    Enregistre les pages sélectionnées dans un nouveau fichier CBZ.
    Actif si : images chargées ET sélection non vide.
    canvas : celui du panneau, pour la progression de l'écriture.
    """
    state = callbacks.get("state") or _state_module.state
    if not state.selected_indices or not state.images_data:
        ErrorDialog(parent,
                    lambda: _wt("messages.warnings.no_selection_save.title"),
                    lambda: _("messages.warnings.no_selection_save.message")).show()
        return

    if not _check_no_ico(parent, state):
        return
    if not _check_no_video(parent, state):
        return
    if not _check_pages_have_data(parent, [state.images_data[i] for i in state.selected_indices
                                           if i < len(state.images_data)]):
        return

    renumber_func = callbacks.get("renumber_btn_action")
    selected_entries = [state.images_data[i] for i in state.selected_indices
                        if i < len(state.images_data) and state.images_data[i]["is_image"]]
    def _on_validated():
        if not _document_still_writable(state):
            return
        initial_dir = None
        if state.current_file:
            initial_dir = os.path.dirname(os.path.abspath(state.current_file))
            base_name = os.path.splitext(os.path.basename(state.current_file))[0]
            initial_file = f"{base_name}_{_('misc.selection_suffix')}.cbz"
        else:
            initial_dir = getattr(state, "first_image_dir", None)
            initial_file = f"{_('misc.selection_suffix')}.cbz"
        if not initial_dir:
            initial_dir = get_config_manager().get('last_open_dir', "")

        start = os.path.join(initial_dir, initial_file) if initial_dir else initial_file
        filepath = _get_save_filename(
            parent,
            _wt("buttons.save_selection"),
            start,
            "Comic Book Archive (*.cbz)",
        )
        if not filepath:
            return
        get_config_manager().set('last_open_dir', os.path.dirname(os.path.abspath(filepath)))

        comp_level = parent._zip_compression_config().get_zip_compression_level() if hasattr(parent, "_zip_compression_config") else 0
        entries = [state.images_data[idx] for idx in sorted(state.selected_indices)
                   if idx < len(state.images_data)]
        if not _check_disk_space(parent, filepath, entries):
            return
        try:
            if not _write_cbz_with_overlay(canvas, filepath, state, comp_level, entries):
                return
            from modules.qt import recent_files as _recent_files_module
            _recent_files_module.add_to_recent_files(filepath)
            if hasattr(parent, "_main_window") and hasattr(parent._main_window, "_sync_recent_menus"):
                parent._main_window._sync_recent_menus()
            InfoDialogClickablePath(parent,
                                    "messages.info.selection_saved.title",
                                    "messages.info.selection_saved.message",
                                    filepath)
        except Exception as e:
            ErrorDialog(parent,
                        lambda: _wt("messages.errors.save_selection_failed.title"),
                        lambda err=e: _("messages.errors.save_selection_failed.message", error=err),
                        play_sound=True).show()

    _handle_duplicate_filenames_qt(
        parent, renumber_func,
        lambda ok: _on_validated() if ok else None,
        entries_to_check=selected_entries,
        state=state,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. save_selection_to_folder — Exporter les pages sélectionnées
# ═══════════════════════════════════════════════════════════════════════════════

def save_selection_to_folder(parent, callbacks: dict):
    """
    Enregistre les fichiers sélectionnés dans un dossier (ou un fichier unique).
    Actif si : images chargées ET sélection non vide.
    """
    state = callbacks.get("state") or _state_module.state
    if not state.selected_indices or not state.images_data:
        return

    renumber_func = callbacks.get("renumber_btn_action")
    selected_entries = [state.images_data[i] for i in state.selected_indices
                        if i < len(state.images_data) and state.images_data[i]["is_image"]]

    def _on_validated():
        if not _document_still_writable(state):
            return
        # Dernier dossier d'export s'il existe encore (réglage propre à
        # l'export, distinct de last_open_dir des fenêtres d'ouverture), sinon
        # dossier du comic, des premières images ou de la dernière ouverture.
        initial_dir = get_config_manager().get('last_export_dir', "")
        if not (initial_dir and os.path.isdir(initial_dir)):
            initial_dir = None
            if state.current_file:
                initial_dir = os.path.dirname(os.path.abspath(state.current_file))
            elif getattr(state, "first_image_dir", None):
                initial_dir = state.first_image_dir
            if not initial_dir:
                initial_dir = get_config_manager().get('last_open_dir', "")

        def _show_write_error(e):
            ErrorDialog(parent,
                        lambda: _wt("messages.errors.save_files_failed.title"),
                        lambda err=e: _("messages.errors.save_files_failed.message", error=str(err)),
                        play_sound=True).show()

        try:
            skipped_count = 0
            folder = None

            if len(state.selected_indices) == 1:
                idx = list(state.selected_indices)[0]
                entry = state.images_data[idx]
                # Nom de base seulement : un nom de page venu d'une archive
                # peut être un chemin absolu (« C:/… ») ou contenir « .. »,
                # et la boîte d'enregistrement proposerait alors d'écrire
                # dans ce dossier-là plutôt que dans le dossier d'export.
                initial_file = windows_safe_component(
                    entry["orig_name"].replace("\\", "/").rsplit("/", 1)[-1])
                file_ext = os.path.splitext(initial_file)[1].lower()
                all_files = f"{_wt('dialogs.file_filters.all_files')} (*.*)"
                filter_str = (f"{_wt('dialogs.file_filters.ext_files', ext=file_ext[1:].upper())}"
                              f" (*{file_ext});;{all_files}"
                              if file_ext else all_files)
                start = os.path.join(initial_dir, initial_file) if initial_dir else initial_file
                file_path = _get_save_filename(
                    parent,
                    _wt("dialogs.save_to_folder.title"),
                    start,
                    filter_str,
                )
                if not file_path:
                    return
                get_config_manager().set('last_export_dir', os.path.dirname(os.path.abspath(file_path)))
                # Extension tapée d'un autre format que la page (page.png
                # pour un JPEG) : l'image n'est pas convertie, la bonne
                # extension est ajoutée au nom tapé (page.png.jpg) — sinon le
                # fichier porterait une extension qui ment sur son contenu.
                # Convertir est le rôle de « Convertir ».
                page_ext = os.path.splitext(entry["orig_name"])[1]
                typed_ext = os.path.splitext(file_path)[1]
                completed = bool(page_ext) and not same_image_format(typed_ext, page_ext)
                if completed:
                    file_path += page_ext
                folder = os.path.dirname(file_path)
                data = (_bytes_with_entry_dpi(entry)   # résolution écrite, comme dans un CBZ
                        if entry["bytes"] is not None and not entry.get("is_dir") else None)

                def _write_single(path=file_path, data=data):
                    try:
                        if data is not None:
                            _write_file_safely(path, data)
                    except Exception as e:
                        _show_write_error(e)
                        return
                    FileSavedDialog(parent, 1 if data is not None else 0, folder, skipped_count)

                # Remplacement d'un fichier existant déjà confirmé par la
                # boîte native, sauf pour un nom complété : la boîte ne
                # portait pas sur ce nom-là, la question est posée ici.
                if completed and data is not None and os.path.exists(file_path):
                    ExistingFilesDialog(parent, [os.path.basename(file_path)]).ask_async(
                        lambda replace: _write_single() if replace else None)
                else:
                    _write_single()
                return

            folder = QFileDialog.getExistingDirectory(
                parent,
                _wt("dialogs.save_to_folder.title"),
                initial_dir or "",
            )
            if not folder:
                return
            get_config_manager().set('last_export_dir', folder)
            # Bytes capturés maintenant : la question sur les fichiers existants
            # est non modale, la sélection a pu changer avant la réponse.
            targets = []
            to_export = [state.images_data[idx] for idx in sorted(state.selected_indices)
                         if idx < len(state.images_data)
                         and state.images_data[idx]["bytes"] is not None
                         and not state.images_data[idx].get("is_dir")]
            # Noms valides sous Windows et distincts : un nom refusé par
            # Windows (« a:b.jpg », « CON.jpg ») arrêterait l'export au milieu,
            # deux pages devenues homonymes s'écraseraient.
            rel_names = unique_names([windows_safe_relpath(e["orig_name"]) for e in to_export])
            for entry, rel_name in zip(to_export, rel_names):
                # Sécurité : empêche toute écriture hors du dossier choisi
                # (nom d'entrée piégé "../" — Zip Slip). Entrée ignorée si évasion.
                file_path = safe_join(folder, rel_name)
                if file_path is None:
                    skipped_count += 1
                    continue
                # Résolution de la page écrite, comme dans un CBZ
                targets.append((file_path, _bytes_with_entry_dpi(entry)))
        except Exception as e:
            _show_write_error(e)
            return

        def _write_all():
            saved = 0
            try:
                for file_path, data in targets:
                    _write_file_safely(file_path, data)
                    saved += 1
            except Exception as e:
                _show_write_error(e)
                return
            FileSavedDialog(parent, saved, folder, skipped_count)

        # Un fichier du dossier portant le nom d'une page exportée n'est
        # jamais remplacé sans le demander (une seule question pour tous).
        # Tout ou rien : jamais d'export partiel, qui laisserait croire que
        # toutes les pages ont été exportées.
        existing = [os.path.relpath(p, folder) for p, _data in targets if os.path.exists(p)]
        if not existing:
            _write_all()
            return

        def _on_answer(replace):
            if replace:
                _write_all()

        ExistingFilesDialog(parent, existing).ask_async(_on_answer)

    _handle_duplicate_filenames_qt(
        parent, renumber_func,
        lambda ok: _on_validated() if ok else None,
        entries_to_check=selected_entries,
        state=state,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. create_cbz_from_images — Créer une archive CBZ (mode images seules)
# ═══════════════════════════════════════════════════════════════════════════════

def create_cbz_from_images(parent, canvas, callbacks: dict, on_complete=None):
    """
    Crée une archive CBZ à partir des images déposées (mode sans archive).
    Actif si : pas d'archive ouverte ET images présentes.

    on_complete(success: bool) : appelé avec True une fois la confirmation
    « CBZ créé » refermée par l'utilisateur, avec False en cas d'erreur ou
    d'annulation du choix de fichier. Pas appelé si une étape de validation est
    annulée (le dialogue concerné a déjà informé l'utilisateur).
    """
    state = callbacks.get("state") or _state_module.state

    def _done(success):
        if on_complete is not None:
            on_complete(success)

    if not state.images_data:
        return _done(False)
    # Appelée aussi directement par « Oui, créer un CBZ » de la fermeture,
    # hors des commandes bloquées pendant une opération.
    if state.operation_running():
        return _done(False)

    if not _check_no_ico(parent, state):
        return _done(False)
    if not _check_no_video(parent, state):
        return _done(False)
    if not _check_pages_have_data(parent, state.images_data):
        return _done(False)

    _auto_update_page_count_qt(state)
    _apply_name_edits(state)

    render_mosaic = callbacks.get("render_mosaic", lambda: None)
    renumber_func = callbacks.get("renumber_btn_action")

    def _on_validated():
        if not _document_still_writable(state):
            return _done(False)
        initial_dir = None
        if state.current_file:
            initial_dir = os.path.dirname(os.path.abspath(state.current_file))
        elif getattr(state, "first_image_dir", None):
            initial_dir = state.first_image_dir
        if not initial_dir:
            initial_dir = get_config_manager().get('last_open_dir', "")

        start = os.path.join(initial_dir, "nouveau_comics.cbz") if initial_dir else "nouveau_comics.cbz"
        filepath = _get_save_filename(
            parent,
            _wt("buttons.create_cbz"),
            start,
            "Comic Book Archive (*.cbz)",
        )
        if not filepath:
            return _done(False)
        get_config_manager().set('last_open_dir', os.path.dirname(os.path.abspath(filepath)))
        if not _check_disk_space(parent, filepath, state.images_data):
            return _done(False)
        claims = _claim_files_for_write(parent, [filepath])
        if claims is None:
            return _done(False)

        overlay = _SavingOverlay(canvas) if canvas else None
        comp_level = parent._zip_compression_config().get_zip_compression_level() if hasattr(parent, "_zip_compression_config") else 0
        try:
            unchanged = _write_document_with_progress(filepath, state, overlay, comp_level)
        except _DocumentChangedDuringSave:
            return _done(False)  # document fermé ou remplacé pendant l'écriture : rien n'est créé
        except Exception as e:
            if overlay:
                overlay.remove()
            ErrorDialog(parent,
                        lambda: _wt("messages.errors.create_archive_failed.title"),
                        lambda err=e: _("messages.errors.create_archive_failed.message").format(error=err),
                        play_sound=True).show()
            return _done(False)
        finally:
            if overlay:
                overlay.remove()
            claims.release_all()

        state.current_file = filepath
        state.disk_signature = file_signature(filepath)
        if unchanged:
            state.modified = False
            mark_history_saved(state)
        state.zip_compression_state = "stored" if comp_level <= 0 else "deflated"
        _register_document_file(parent, callbacks, filepath)
        if hasattr(parent, "_update_status_bar"):
            parent._update_status_bar()
        if callbacks.get("update_button_text"):
            callbacks["update_button_text"]()
        if callbacks.get("update_tabs"):
            callbacks["update_tabs"]()
        render_mosaic()
        # _done(True) seulement à la fermeture de la confirmation : appelé plus
        # tôt, la fermeture du document qui suit (« Oui, créer un CBZ » à la
        # fermeture) refermerait aussitôt cette confirmation avec lui.
        # La confirmation est non modale : le document a pu être modifié (ou
        # l'avoir été pendant l'écriture) avant qu'elle soit refermée. Il ne
        # correspond alors plus au CBZ écrit, _done(False) évite qu'une
        # fermeture enchaînée le détruise sans confirmation.
        written = content_snapshot(state)
        generation = state.doc_generation

        def _on_confirmation_closed():
            saved = (unchanged and state.doc_generation == generation
                     and content_unchanged(state, written))
            _done(saved)

        InfoDialogClickablePath(parent,
                            "messages.info.cbz_created.title",
                            "messages.info.cbz_created.message",
                            filepath,
                            on_done=_on_confirmation_closed)

    _run_validation_chain(
        [
            lambda done: _validate_filenames_qt(parent, render_mosaic, done, state),
            lambda done: _check_animated_gifs_qt(parent, done, state),
            lambda done: _handle_duplicate_filenames_qt(parent, renumber_func, done, state=state),
        ],
        on_all_passed=_on_validated,
        state=state,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 5 & 6. apply_new_names — Appliquer les modifications et sauvegarder/créer CBZ
# ═══════════════════════════════════════════════════════════════════════════════

def apply_new_names(parent, canvas, callbacks: dict, on_complete=None):
    """
    Applique les nouveaux noms et sauvegarde ou crée une archive CBZ. NON modal.

    - Si archive .cbz ouverte et modifiée → écrase le fichier CBZ existant
      (bouton « Appliquer et sauvegarder le CBZ »)
    - Si archive CBR/EPUB/PDF ouverte et modifiée → propose un nouveau fichier .cbz
      (bouton « Appliquer et créer un CBZ »)

    on_complete(success: bool) : appelé en fin de traitement (sauvegarde réussie =
    True, annulation/erreur = False). Remplace l'ancienne valeur de retour booléenne,
    car la validation est désormais asynchrone (dialogues non modaux).
    """
    state = callbacks.get("state") or _state_module.state

    def _done(success):
        if on_complete is not None:
            on_complete(success)

    if not state.current_file:
        return _done(False)
    # Appelée aussi directement par « Enregistrer et fermer » de la fermeture,
    # hors des commandes bloquées pendant une opération.
    if state.operation_running():
        return _done(False)

    if not _check_no_ico(parent, state):
        return _done(False)
    if not _check_no_video(parent, state):
        return _done(False)
    if not _check_pages_have_data(parent, state.images_data):
        return _done(False)

    _auto_update_page_count_qt(state)
    _apply_name_edits(state)

    # Vérifie les doublons (message d'erreur bloquant, pas de dialogue 3 boutons)
    duplicates = {}
    for entry in state.images_data:
        name = entry["orig_name"]
        duplicates[name] = duplicates.get(name, 0) + 1
    real_dups = {k: v for k, v in duplicates.items() if v > 1}
    if real_dups:
        def _make_msg(dups=real_dups):
            msg = _("messages.errors.duplicate_names.intro").format(count=len(dups))
            for idx, (name, times) in enumerate(dups.items()):
                if idx < 10:
                    msg += _("messages.errors.duplicate_names.item").format(name=name, times=times)
            if len(dups) > 10:
                msg += _("messages.errors.duplicate_names.more").format(more=len(dups) - 10)
            msg += _("messages.errors.duplicate_names.footer")
            return msg
        DuplicateNamesErrorDialog(parent, _make_msg, "messages.errors.duplicate_names.title")
        return _done(False)

    render_mosaic = callbacks.get("render_mosaic", lambda: None)

    def _on_validated():
        if not _document_still_writable(state):
            return _done(False)
        _write_apply_new_names(parent, canvas, callbacks, _done, state)

    _run_validation_chain(
        [
            lambda d: _validate_filenames_qt(parent, render_mosaic, d, state),
            lambda d: _check_animated_gifs_qt(parent, d, state),
        ],
        on_all_passed=_on_validated,
        state=state,
    )


def _write_apply_new_names(parent, canvas, callbacks, _done, state):
    """Écrit l'archive pour apply_new_names après validation. NON modal.
    Appelle _done(True) en cas de succès, _done(False) en cas d'annulation/erreur.

    state : celui capturé par apply_new_names au lancement — jamais relu depuis
    le singleton ici, la chaîne de validation qui précède passe par des
    dialogues non modaux pendant lesquels l'utilisateur peut changer de panneau."""
    ext = os.path.splitext(state.current_file)[1].lower()
    safe_delete = callbacks.get("safe_delete_file", _safe_delete)
    comp_level = parent._zip_compression_config().get_zip_compression_level() if hasattr(parent, "_zip_compression_config") else 0

    if ext == ".cbz":
        # Remplace le fichier CBZ existant d'un seul coup (fichier temporaire
        # dans le même dossier) : l'original reste intact en cas d'échec.
        if not _check_disk_space(parent, state.current_file, state.images_data):
            return _done(False)

        def _write_cbz():
            # Réponse différée à _confirm_if_changed_on_disk
            if not _document_still_writable(state):
                return _done(False)
            claims = _claim_files_for_write(parent, [state.current_file])
            if claims is None:
                return _done(False)
            try:
                written = (content_snapshot(state), state.doc_generation)
                try:
                    if not _write_cbz_with_overlay(canvas, state.current_file, state, comp_level):
                        return _done(False)
                finally:
                    claims.release_all()
                state.disk_signature = file_signature(state.current_file)
                state.zip_compression_state = "stored" if comp_level <= 0 else "deflated"
                if hasattr(parent, "_update_status_bar"):
                    parent._update_status_bar()
                InfoDialogClickablePath(
                    parent,
                    "messages.info.cbz_saved.title",
                    "messages.info.cbz_saved.message",
                    state.current_file,
                    on_done=lambda: _finish_apply_new_names(state, callbacks, _done, written),
                )
            except Exception as e:
                ErrorDialog(parent,
                            lambda: _wt("messages.errors.save_failed.title"),
                            lambda err=e: _("messages.errors.save_failed.message", error=err),
                            play_sound=True).show()
                _done(False)

        _confirm_if_changed_on_disk(parent, state, state.current_file,
                                    _write_cbz, lambda: _done(False))
        return

    elif ext in (".cbr", ".cb7", ".cbt", ".epub"):
        initial_dir = os.path.dirname(os.path.abspath(state.current_file))
        if not initial_dir:
            initial_dir = get_config_manager().get('last_open_dir', "")
        initial_name = os.path.splitext(os.path.basename(state.current_file))[0] + ".cbz"
        new_file = _get_save_filename(
            parent,
            _wt("labels.apply_create_cbz"),
            os.path.join(initial_dir, initial_name),
            "Comic Book Archive (*.cbz)",
        )
        if not new_file:
            return _done(False)
        if _is_conversion_source(parent, new_file, state):
            return _done(False)
        get_config_manager().set('last_open_dir', os.path.dirname(os.path.abspath(new_file)))
        if not _check_disk_space(parent, new_file, state.images_data):
            return _done(False)
        claims = _claim_files_for_write(parent, [new_file, state.current_file])
        if claims is None:
            return _done(False)
        try:
            written = (content_snapshot(state), state.doc_generation)
            try:
                if not _write_cbz_with_overlay(canvas, new_file, state, comp_level):
                    return _done(False)
            finally:
                claims.release_all()
            written_count = len(cbz_members(state.images_data))
            old_file_cbz = state.current_file
            state.current_file = new_file
            state.disk_signature = file_signature(new_file)
            state.zip_compression_state = "stored" if comp_level <= 0 else "deflated"
            _register_document_file(parent, callbacks, new_file)
            if hasattr(parent, "_update_status_bar"):
                parent._update_status_bar()

            def _after_cbz_converted(result):
                if result:
                    try:
                        if old_file_cbz and os.path.exists(old_file_cbz):
                            verify_written_cbz(new_file, written_count)
                            safe_delete(old_file_cbz)
                    except WrittenFileCheckError as e:
                        _mark_save_unverified(parent, callbacks, state, written[1])
                        ErrorDialog(parent,
                                    lambda: _wt("messages.errors.save_failed.title"),
                                    lambda err=e: str(err),
                                    play_sound=True).show()
                        return _done(False)
                    except Exception as e:
                        ErrorDialog(parent,
                                    lambda: _wt("messages.errors.save_failed.title"),
                                    lambda err=e: _("messages.errors.delete_error", error=err),
                                    play_sound=True).show()
                _finish_apply_new_names(state, callbacks, _done, written)

            SaveSuccessDialog(
                parent,
                "messages.info.cbz_converted.title",
                "messages.info.cbz_converted.message",
                new_file,
                "messages.info.cbz_converted.question",
                on_done=_after_cbz_converted,
                question_kwargs={"ext": ext.lstrip(".").upper()},
            )
            return
        except Exception as e:
            ErrorDialog(parent,
                        lambda: _wt("messages.errors.save_failed.title"),
                        lambda err=e: _("messages.errors.save_failed.message", error=err),
                        play_sound=True).show()
            return _done(False)

    elif ext == ".pdf":
        initial_dir = os.path.dirname(os.path.abspath(state.current_file))
        if not initial_dir:
            initial_dir = get_config_manager().get('last_open_dir', "")
        initial_name = os.path.splitext(os.path.basename(state.current_file))[0] + ".cbz"
        new_file = _get_save_filename(
            parent,
            _wt("labels.apply_create_cbz"),
            os.path.join(initial_dir, initial_name),
            "Comic Book Archive (*.cbz)",
        )
        if not new_file:
            return _done(False)
        if _is_conversion_source(parent, new_file, state):
            return _done(False)
        get_config_manager().set('last_open_dir', os.path.dirname(os.path.abspath(new_file)))
        if not _check_disk_space(parent, new_file, state.images_data):
            return _done(False)
        claims = _claim_files_for_write(parent, [new_file, state.current_file])
        if claims is None:
            return _done(False)
        try:
            written = (content_snapshot(state), state.doc_generation)
            try:
                if not _write_cbz_with_overlay(canvas, new_file, state, comp_level):
                    return _done(False)
            finally:
                claims.release_all()
            written_count = len(cbz_members(state.images_data))
            old_file_pdf = state.current_file
            state.current_file = new_file
            state.disk_signature = file_signature(new_file)
            state.zip_compression_state = "stored" if comp_level <= 0 else "deflated"
            _register_document_file(parent, callbacks, new_file)
            if hasattr(parent, "_update_status_bar"):
                parent._update_status_bar()

            def _after_pdf_converted(result):
                if result:
                    try:
                        if old_file_pdf and os.path.exists(old_file_pdf):
                            verify_written_cbz(new_file, written_count)
                            safe_delete(old_file_pdf)
                    except WrittenFileCheckError as e:
                        _mark_save_unverified(parent, callbacks, state, written[1])
                        ErrorDialog(parent,
                                    lambda: _wt("messages.errors.save_failed.title"),
                                    lambda err=e: str(err),
                                    play_sound=True).show()
                        return _done(False)
                    except Exception as e:
                        ErrorDialog(parent,
                                    lambda: _wt("messages.errors.save_failed.title"),
                                    lambda err=e: _("messages.errors.delete_error", error=err),
                                    play_sound=True).show()
                _finish_apply_new_names(state, callbacks, _done, written)

            SaveSuccessDialog(
                parent,
                "messages.info.cbz_converted_from_pdf.title",
                "messages.info.cbz_converted_from_pdf.message",
                new_file,
                "messages.info.cbz_converted_from_pdf.question",
                on_done=_after_pdf_converted,
            )
            return
        except Exception as e:
            ErrorDialog(parent,
                        lambda: _wt("messages.errors.save_failed.title"),
                        lambda err=e: _("messages.errors.save_failed.message", error=err),
                        play_sound=True).show()
            return _done(False)

    _finish_apply_new_names(state, callbacks, _done,
                            (content_snapshot(state), state.doc_generation))


def _is_conversion_source(parent, new_file, state):
    """Conversion CBR/CB7/CBT/EPUB/PDF → CBZ : True (et message) si le nom
    choisi est celui du fichier ouvert. Il serait écrasé par le CBZ, puis
    « Supprimer l'original ? » supprimerait le fichier qu'on vient d'écrire.
    Comparaison par is_same_path, jamais par ==."""
    if not is_same_path(new_file, state.current_file):
        return False
    ErrorDialog(parent,
                lambda: _wt("messages.errors.save_failed.title"),
                lambda: _("messages.errors.convert_target_is_source")).show_nonmodal()
    return True


def _mark_save_unverified(parent, callbacks, state, generation):
    """Le CBZ écrit ne se relit pas correctement (verify_written_cbz, avant la
    suppression de l'original, conservé) : le document repasse à « modifié »,
    sinon sa fermeture ne demanderait pas confirmation alors que ses
    modifications ne sont lisibles dans aucun fichier. current_file reste le
    CBZ écrit : un nouvel enregistrement le remplace, jamais l'original.
    Sans effet si le document a été fermé ou remplacé entre-temps."""
    if state.doc_generation != generation:
        return
    state.modified = True
    mark_history_unsaved(state)
    if hasattr(parent, "_update_status_bar"):
        parent._update_status_bar()
    for key in ("update_button_text", "update_tabs", "update_window_title"):
        if callbacks.get(key):
            callbacks[key]()


def _finish_apply_new_names(state, callbacks, _done, written):
    """Termine apply_new_names : marque non-modifié, rafraîchit l'UI, signale le succès.
    Différée jusqu'à la fermeture de la confirmation (InfoDialogClickablePath pour .cbz,
    réponse de SaveSuccessDialog pour une conversion cbr/cb7/cbt/epub/pdf), sans quoi
    _done(True) déclencherait la fermeture en cascade des fenêtres liées au comic avant
    que l'utilisateur ait lu la confirmation ou répondu à la question de suppression du
    fichier d'origine.

    written : (content_snapshot, doc_generation) capturés juste avant l'écriture. Ces
    confirmations sont non modales : si le document a été modifié, fermé ou remplacé
    avant leur fermeture, il ne correspond plus au fichier écrit. Il n'est alors pas
    marqué sauvegardé, et _done(False) empêche « Enregistrer et fermer » de le fermer
    sans confirmation."""
    snapshot, generation = written
    if state.doc_generation != generation:
        _done(False)
        return
    saved = content_unchanged(state, snapshot)
    if saved:
        state.modified = False
        mark_history_saved(state)
    if callbacks.get("render_mosaic"):
        callbacks["render_mosaic"]()
    if callbacks.get("update_button_text"):
        callbacks["update_button_text"]()
    if callbacks.get("update_tabs"):
        callbacks["update_tabs"]()
    if callbacks.get("on_file_saved"):
        callbacks["on_file_saved"](state.current_file)
    _done(saved)


# ═══════════════════════════════════════════════════════════════════════════════
# Mise à la Corbeille
# ═══════════════════════════════════════════════════════════════════════════════

def _safe_delete(filepath):
    """Met `filepath` à la Corbeille (utils.send_to_trash)."""
    send_to_trash(filepath)
