"""
file_operations_qt.py — Opérations de sauvegarde/export pour la version PySide6.

Implémente les 6 méthodes de sauvegarde de l'appli originale :
  1. save_as_cbz              — Enregistrer sous (nouveau fichier CBZ)
  2. save_selection_as_cbz    — Enregistrer la sélection en CBZ
  3. save_selection_to_folder — Exporter les pages sélectionnées (dossier ou fichier)
  4. create_cbz_from_images   — Créer une archive CBZ (mode images seules)
  5. apply_new_names          — Appliquer les modifications et sauvegarder le CBZ
  6. apply_new_names          — Appliquer les modifications et créer un CBZ (CBR/PDF → CBZ)

Les dialogs Qt remplacent les Toplevel tkinter :
  - InfoDialogClickablePath  ↔  InfoDialogWithClickablePath
  - SaveSuccessDialog        ↔  SaveSuccessDialog
  - DuplicateFilenameDialog  ↔  DuplicateFilenameDialog
  - DuplicateNamesErrorDialog↔  DuplicateNamesErrorDialog
"""

import os
import io
import sys
import subprocess
import tempfile
import shutil
import zipfile

from PIL import Image

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSizePolicy, QProgressDialog,
    QFileDialog, QApplication,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor, QDesktopServices
from PySide6.QtCore import QUrl

from modules.qt import state as _state_module
from modules.qt.localization import _, _wt
from modules.qt.config_manager import get_config_manager
from modules.qt.entries import save_image_to_bytes
from modules.qt.utils import format_file_size
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


def _check_no_ico(parent):
    state = _state_module.state
    if any(e.get("orig_name", "").lower().endswith(".ico") for e in state.images_data):
        from modules.qt.dialogs_qt import MsgDialog
        MsgDialog(parent,
                  "dialogs.ico_creator.save_blocked_title",
                  "dialogs.ico_creator.save_blocked_message").exec()
        return False
    return True


def _check_no_video(parent):
    state = _state_module.state
    if any(os.path.splitext(e.get("orig_name", "").lower())[1] in _VIDEO_EXTENSIONS
           for e in state.images_data):
        ErrorDialog(parent,
                    _("dialogs.video_save_blocked.title"),
                    _("dialogs.video_save_blocked.message")).exec()
        return False
    return True


def _open_file_location(filepath):
    """Ouvre l'explorateur Windows et sélectionne le fichier."""
    try:
        if os.name == "nt":
            subprocess.Popen(f'explorer /select,"{os.path.abspath(filepath)}"')
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(filepath)))
    except Exception as e:
        print(f"Erreur ouverture explorateur : {e}")


def _open_folder(folder):
    """Ouvre le dossier dans l'explorateur."""
    try:
        if os.name == "nt":
            os.startfile(folder)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
    except Exception as e:
        print(f"Erreur ouverture dossier : {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# Dialogs Qt — avec support changement de langue à la volée
# ═══════════════════════════════════════════════════════════════════════════════

class InfoDialogClickablePath(QDialog):
    """
    Dialogue d'information avec chemin de fichier cliquable.
    Équivalent Qt de InfoDialogWithClickablePath (tkinter).
    """

    def __init__(self, parent, title_key: str, message_key: str, filepath: str):
        super().__init__(parent)
        self._title_key = title_key
        self._message_key = message_key
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setFixedWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        self._msg = QLabel()
        self._msg.setWordWrap(True)
        self._msg.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg)

        # Chemin cliquable (statique — pas de clé de traduction)
        path_lbl = QLabel(f'<a href="file:///">{filepath}</a>')
        path_lbl.setWordWrap(True)
        path_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        path_lbl.setCursor(QCursor(Qt.PointingHandCursor))
        path_lbl.setStyleSheet("color: #4A9EFF;")
        path_lbl.linkActivated.connect(lambda _: _open_file_location(filepath))
        layout.addWidget(path_lbl)

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
        self._msg.setText(_(self._message_key))
        self._ok_btn.setText(_("buttons.ok"))

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass


class SaveSuccessDialog(QDialog):
    """
    Dialogue de sauvegarde réussie avec chemin cliquable et question de suppression.
    Équivalent Qt de SaveSuccessDialog (tkinter).
    result = True si l'utilisateur choisit Oui (supprimer le fichier d'origine).
    """

    def __init__(self, parent, title_key: str, message_key: str,
                 filepath: str, question_key: str,
                 yes_key: str = "misc.yes", no_key: str = "misc.no",
                 on_done=None):
        super().__init__(parent)
        self.result = False
        self._title_key = title_key
        self._message_key = message_key
        self._question_key = question_key
        self._yes_key = yes_key
        self._no_key = no_key
        self._on_done = on_done
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setFixedWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        self._msg = QLabel()
        self._msg.setWordWrap(True)
        self._msg.setAlignment(Qt.AlignLeft)
        layout.addWidget(self._msg)

        # Chemin cliquable (statique)
        path_lbl = QLabel(f'<a href="file:///">{filepath}</a>')
        path_lbl.setWordWrap(True)
        path_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        path_lbl.setCursor(QCursor(Qt.PointingHandCursor))
        path_lbl.setStyleSheet("color: #4A9EFF;")
        path_lbl.linkActivated.connect(lambda _: _open_file_location(filepath))
        layout.addWidget(path_lbl)

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
        self.setWindowTitle(_wt(self._title_key))
        self._msg.setText(_(self._message_key))
        self._question.setText(_(self._question_key))
        self._yes_btn.setText(_(self._yes_key))
        self._no_btn.setText(_(self._no_key))

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
    """
    Dialogue d'erreur pour les noms de fichiers en double dans apply_new_names.
    Équivalent Qt de DuplicateNamesErrorDialog (tkinter).
    """

    def __init__(self, parent, message_func, title_key: str):
        super().__init__(parent)
        self._title_key = title_key
        self._message_func = message_func
        self.setModal(True)
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
        self.exec()

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
    Équivalent Qt de DuplicateFilenameDialog (tkinter).
    result = "renumber" | "ignore" | None (annuler)
    """

    def __init__(self, parent, duplicate_names):
        super().__init__(parent)
        self.result = None
        self.setWindowTitle("")
        self.setModal(True)
        self.setFixedWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self._title_lbl = QLabel()
        self._title_lbl.setStyleSheet("color: #ff9800; font-size: 13px; font-weight: bold;")
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
        if parent is not None:
            from modules.qt.dialogs_qt import _center_on_widget
            _center_on_widget(self, parent)
        self.exec()

    def _retranslate(self):
        self._title_lbl.setText(_("dialogs.duplicate_filenames.title"))
        self._msg_lbl.setText(_("dialogs.duplicate_filenames.message"))
        self._renumber_btn.setText(_("buttons.renumber_recommended"))
        self._ignore_btn.setText(_("buttons.ignore_and_save"))
        self._cancel_btn.setText(_("buttons.cancel"))

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def _renumber(self):
        self.result = "renumber"
        self.accept()

    def _ignore(self):
        self.result = "ignore"
        self.accept()

    def _cancel(self):
        self.result = None
        self.accept()


# ═══════════════════════════════════════════════════════════════════════════════
# FileSavedDialog (équivalent show_files_saved_dialog tkinter)
# ═══════════════════════════════════════════════════════════════════════════════

class FileSavedDialog(QDialog):
    """Dialogue de confirmation avec lien cliquable vers le dossier."""

    def __init__(self, parent, count: int, folder: str):
        super().__init__(parent)
        self._count = count
        self._folder = folder
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setFixedWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        self._msg = QLabel()
        self._msg.setWordWrap(True)
        layout.addWidget(self._msg)

        # Lien vers le dossier
        display = folder if len(folder) <= 60 else "..." + folder[-57:]
        link_lbl = QLabel(f'<a href="folder">{display}</a>')
        link_lbl.setStyleSheet("color: #4A9EFF;")
        link_lbl.setCursor(QCursor(Qt.PointingHandCursor))
        link_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        link_lbl.linkActivated.connect(lambda _: _open_folder(folder))
        layout.addWidget(link_lbl)

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
        self.setWindowTitle(_wt("messages.info.files_saved.title"))
        self._msg.setText(_("messages.info.files_saved.message", count=self._count, folder=self._folder))
        self._close_btn.setText(_("buttons.close"))

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
    """Overlay texte rouge « Sauvegarde X% » affiché par-dessus le canvas Qt."""

    def __init__(self, canvas):
        from PySide6.QtWidgets import QGraphicsTextItem
        from PySide6.QtGui import QColor
        from modules.qt.font_manager_qt import get_current_font as _get_current_font
        self._canvas = canvas
        self._scene = canvas.scene()
        self._item = QGraphicsTextItem(_("labels.saving_cbz", percent=0))
        font = _get_current_font(24)
        font.setBold(True)
        self._item.setFont(font)
        self._item.setDefaultTextColor(QColor("red"))
        self._item.setZValue(9999)
        self._scene.addItem(self._item)
        self._reposition()

    def _reposition(self):
        vp = self._canvas.viewport()
        vr = self._canvas.mapToScene(vp.rect()).boundingRect()
        br = self._item.boundingRect()
        self._item.setPos(vr.center().x() - br.width() / 2,
                          vr.center().y() - br.height() / 2)

    def update(self, percent: int):
        from modules.qt.localization import _
        self._item.setPlainText(_("labels.saving_cbz", percent=percent))
        self._reposition()
        QApplication.processEvents()

    def remove(self):
        try:
            self._scene.removeItem(self._item)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Vérifications pré-sauvegarde (portées de file_operations.py)
# ═══════════════════════════════════════════════════════════════════════════════

def _validate_filenames_qt(parent, render_mosaic):
    """
    Valide les noms de fichiers (caractères interdits, etc.).
    Version Qt de file_validation.validate_filenames.
    Retourne True si OK, False si l'utilisateur annule.
    """
    state = _state_module.state
    invalid_chars = '<>:"|?*'
    has_subdirs = any(e.get("is_dir") for e in state.images_data)
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
        has_path_traversal = ".." in filename
        if has_invalid or has_control or has_path_traversal:
            invalid_files.append({"index": idx, "original": filename, "entry": entry})

    if not invalid_files:
        return True

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

    reply = QuestionYNCDialog(
        parent,
        lambda: _("messages.questions.invalid_filenames.title"),
        _build_msg,
    ).ask()

    if reply == "cancel":
        return False

    if reply == "yes":
        for item in invalid_files:
            entry = item["entry"]
            filename = item["original"]
            cleaned = filename
            if has_subdirs:
                parts = cleaned.replace("\\", "/").split("/")
                parts = [p for p in parts if p]
                cleaned = "/".join(parts) if parts else "fichier" + entry["extension"]
            else:
                cleaned = cleaned.replace("/", "_").replace("\\", "_")
            for char in invalid_chars:
                cleaned = cleaned.replace(char, "_")
            cleaned = "".join(c if ord(c) >= 32 else "_" for c in cleaned)
            cleaned = cleaned.replace("..", "")
            entry["orig_name"] = cleaned
        render_mosaic()
        InfoDialog(
            parent,
            _("messages.info.correction_done.title"),
            _("messages.info.correction_done.message", count=len(invalid_files)),
        ).exec()
        return True

    # Non — ne pas corriger
    ErrorDialog(
        parent,
        _("messages.warnings.save_cancelled.title"),
        _("messages.warnings.save_cancelled.message"),
    ).exec()
    return False


def _auto_update_page_count_qt():
    """
    Met à jour silencieusement PageCount dans le ComicInfo.xml si le nombre de pages a changé.
    Appelé automatiquement avant chaque sauvegarde.
    Note : sync_pages_in_xml_data() gère déjà PageCount lors des ajouts/suppressions.
    Ceci couvre les cas résiduels (modifications sans changement de liste).
    """
    from modules.qt.comic_info import update_page_count_in_xml_data
    state = _state_module.state
    if not state.comic_metadata or not state.original_page_count:
        return
    current_count = len([e for e in state.images_data if e.get("is_image") and not e.get("is_dir")])
    if current_count != state.original_page_count:
        update_page_count_in_xml_data(state, current_count)


def _has_animated_gifs():
    """Retourne True si la liste d'images contient au moins un GIF animé."""
    for entry in _state_module.state.images_data:
        if entry.get("extension", "").lower() == ".gif" and entry.get("is_animated_gif"):
            return True
    return False


def _check_animated_gifs_qt(parent):
    """
    Vérifie la présence de GIFs animés et affiche l'avertissement.
    Retourne True si on peut continuer, False si l'utilisateur annule.
    """
    if not _has_animated_gifs():
        return True
    from modules.qt.dialogs_qt import ConfirmDialog
    return ConfirmDialog(
        parent,
        "messages.warnings.animated_gif_in_comic.title",
        "messages.warnings.animated_gif_in_comic.message",
    ).ask()


def _handle_duplicate_filenames_qt(parent, renumber_func, entries_to_check=None):
    """
    Vérifie les doublons et affiche le dialogue si nécessaire.
    Retourne True pour continuer, False pour annuler.
    """
    has_duplicates, duplicate_names = detect_duplicate_filenames_for_save(entries_to_check)
    if not has_duplicates:
        return True

    dlg = DuplicateFilenameDialog(parent, duplicate_names)
    if dlg.result == "renumber":
        if renumber_func:
            renumber_func()
        return True
    elif dlg.result == "ignore":
        return True
    return False


def _write_zip_with_progress(filepath, images_data, overlay):
    """Écrit le fichier ZIP et met à jour l'overlay. Retourne True si OK."""
    total = len([e for e in images_data if e["bytes"] is not None and not e.get("is_dir")])
    processed = 0
    with zipfile.ZipFile(filepath, "w") as zf:
        for entry in images_data:
            if entry["bytes"] is None or entry.get("is_dir"):
                continue
            # Si DPI défini, vérifie/régénère les bytes
            if entry.get("dpi") and entry.get("img") is None:
                try:
                    tmp = Image.open(io.BytesIO(entry["bytes"]))
                    existing_dpi = tmp.info.get("dpi")
                    target_dpi = entry["dpi"][0] if isinstance(entry["dpi"], tuple) else entry["dpi"]
                    existing_val = (existing_dpi[0] if isinstance(existing_dpi, tuple)
                                   else existing_dpi if existing_dpi else None)
                    has_correct = existing_val and abs(existing_val - target_dpi) < 1
                    tmp.close()
                    if not has_correct:
                        entry["img"] = Image.open(io.BytesIO(entry["bytes"]))
                        entry["bytes"] = save_image_to_bytes(entry)
                        entry["img"] = None
                except Exception:
                    try:
                        entry["img"] = Image.open(io.BytesIO(entry["bytes"]))
                        entry["bytes"] = save_image_to_bytes(entry)
                        entry["img"] = None
                    except Exception:
                        pass
            zf.writestr(entry["orig_name"], entry["bytes"])
            processed += 1
            if overlay and total > 0:
                overlay.update(int(processed / total * 100))


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
    Sauvegarde l'archive modifiée sous un nouveau fichier CBZ.
    Logique identique à file_operations.save_as_cbz.
    Actif si : archive ouverte ET non modifiée (ou modifiée — le menu le permet aussi).
    """
    state = _state_module.state
    if not state.current_file or not state.images_data:
        return

    if not _check_no_ico(parent):
        return
    if not _check_no_video(parent):
        return

    _auto_update_page_count_qt()

    render_mosaic = callbacks.get("render_mosaic", lambda: None)
    renumber_func = callbacks.get("renumber_btn_action")

    if not _validate_filenames_qt(parent, render_mosaic):
        return

    if not _check_animated_gifs_qt(parent):
        return

    if not _handle_duplicate_filenames_qt(parent, renumber_func):
        return

    initial_dir = os.path.dirname(os.path.abspath(state.current_file))
    if not initial_dir:
        initial_dir = get_config_manager().get('last_open_dir', "")
    initial_name = os.path.splitext(os.path.basename(state.current_file))[0] + ".cbz"
    filepath = _get_save_filename(
        parent,
        _("buttons.save_as"),
        os.path.join(initial_dir, initial_name),
        "Comic Book Archive (*.cbz)",
    )
    if not filepath:
        return
    get_config_manager().set('last_open_dir', os.path.dirname(os.path.abspath(filepath)))

    overlay = _SavingOverlay(canvas) if canvas else None
    try:
        _write_zip_with_progress(filepath, state.images_data, overlay)
    except Exception as e:
        if overlay:
            overlay.remove()
        ErrorDialog(parent,
                    _("messages.errors.save_failed.title"),
                    _("messages.errors.save_failed.message", error=e)).exec()
        return
    finally:
        if overlay:
            overlay.remove()

    old_file = state.current_file
    state.current_file = filepath
    state.modified = False
    if callbacks.get("update_button_text"):
        callbacks["update_button_text"]()
    if callbacks.get("update_tabs"):
        callbacks["update_tabs"]()

    def _after_save_as(result):
        if result:
            try:
                if old_file and os.path.exists(old_file):
                    _safe_delete(old_file)
            except Exception as e:
                ErrorDialog(parent,
                            _("messages.errors.save_failed.title"),
                            _("messages.errors.delete_error", error=e)).exec()

    render_mosaic()
    SaveSuccessDialog(
        parent,
        "messages.info.new_cbz_saved.title",
        "messages.info.new_cbz_saved.message",
        filepath,
        "messages.info.new_cbz_saved.question",
        on_done=_after_save_as,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. save_selection_as_cbz — Enregistrer la sélection en CBZ
# ═══════════════════════════════════════════════════════════════════════════════

def save_selection_as_cbz(parent, callbacks: dict):
    """
    Enregistre les pages sélectionnées dans un nouveau fichier CBZ.
    Actif si : images chargées ET sélection non vide.
    """
    state = _state_module.state
    if not state.selected_indices or not state.images_data:
        ErrorDialog(parent,
                    _("messages.warnings.no_selection_save.title"),
                    _("messages.warnings.no_selection_save.message")).exec()
        return

    if not _check_no_ico(parent):
        return
    if not _check_no_video(parent):
        return

    renumber_func = callbacks.get("renumber_btn_action")
    selected_entries = [state.images_data[i] for i in state.selected_indices
                        if i < len(state.images_data) and state.images_data[i]["is_image"]]
    if not _handle_duplicate_filenames_qt(parent, renumber_func, selected_entries):
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
        _("buttons.save_selection"),
        start,
        "Comic Book Archive (*.cbz)",
    )
    if not filepath:
        return
    get_config_manager().set('last_open_dir', os.path.dirname(os.path.abspath(filepath)))

    try:
        with zipfile.ZipFile(filepath, "w") as zf:
            for idx in sorted(state.selected_indices):
                if idx < len(state.images_data):
                    entry = state.images_data[idx]
                    if entry["bytes"] is not None and not entry.get("is_dir"):
                        zf.writestr(entry["orig_name"], entry["bytes"])
        InfoDialogClickablePath(parent,
                                "messages.info.selection_saved.title",
                                "messages.info.selection_saved.message",
                                filepath)
    except Exception as e:
        ErrorDialog(parent,
                    _("messages.errors.save_selection_failed.title"),
                    _("messages.errors.save_selection_failed.message", error=e)).exec()


# ═══════════════════════════════════════════════════════════════════════════════
# 3. save_selection_to_folder — Exporter les pages sélectionnées
# ═══════════════════════════════════════════════════════════════════════════════

def save_selection_to_folder(parent, callbacks: dict):
    """
    Enregistre les fichiers sélectionnés dans un dossier (ou un fichier unique).
    Actif si : images chargées ET sélection non vide.
    """
    state = _state_module.state
    if not state.selected_indices or not state.images_data:
        return

    renumber_func = callbacks.get("renumber_btn_action")
    selected_entries = [state.images_data[i] for i in state.selected_indices
                        if i < len(state.images_data) and state.images_data[i]["is_image"]]
    if not _handle_duplicate_filenames_qt(parent, renumber_func, selected_entries):
        return

    initial_dir = None
    if state.current_file:
        initial_dir = os.path.dirname(os.path.abspath(state.current_file))
    elif getattr(state, "first_image_dir", None):
        initial_dir = state.first_image_dir
    if not initial_dir:
        initial_dir = get_config_manager().get('last_open_dir', "")

    try:
        saved_count = 0
        folder = None

        if len(state.selected_indices) == 1:
            idx = list(state.selected_indices)[0]
            entry = state.images_data[idx]
            initial_file = entry["orig_name"]
            file_ext = os.path.splitext(initial_file)[1].lower()
            filter_str = (f"Fichiers {file_ext} (*{file_ext});;Tous les fichiers (*.*)"
                          if file_ext else "Tous les fichiers (*.*)")
            start = os.path.join(initial_dir, initial_file) if initial_dir else initial_file
            file_path = _get_save_filename(
                parent,
                _("dialogs.save_to_folder.title"),
                start,
                filter_str,
            )
            if not file_path:
                return
            get_config_manager().set('last_open_dir', os.path.dirname(os.path.abspath(file_path)))
            if entry["bytes"] is not None and not entry.get("is_dir"):
                file_dir = os.path.dirname(file_path)
                if file_dir and not os.path.exists(file_dir):
                    os.makedirs(file_dir, exist_ok=True)
                with open(file_path, "wb") as f:
                    f.write(entry["bytes"])
                saved_count = 1
            folder = os.path.dirname(file_path)
        else:
            folder = QFileDialog.getExistingDirectory(
                parent,
                _("dialogs.save_to_folder.title"),
                initial_dir or "",
            )
            if not folder:
                return
            get_config_manager().set('last_open_dir', folder)
            for idx in sorted(state.selected_indices):
                if idx < len(state.images_data):
                    entry = state.images_data[idx]
                    if entry["bytes"] is not None and not entry.get("is_dir"):
                        file_path = os.path.join(folder, entry["orig_name"])
                        file_dir = os.path.dirname(file_path)
                        if file_dir and not os.path.exists(file_dir):
                            os.makedirs(file_dir, exist_ok=True)
                        with open(file_path, "wb") as f:
                            f.write(entry["bytes"])
                        saved_count += 1

        FileSavedDialog(parent, saved_count, folder)
    except Exception as e:
        ErrorDialog(parent,
                    _("messages.errors.save_files_failed.title"),
                    _("messages.errors.save_files_failed.message", error=str(e))).exec()


# ═══════════════════════════════════════════════════════════════════════════════
# 4. create_cbz_from_images — Créer une archive CBZ (mode images seules)
# ═══════════════════════════════════════════════════════════════════════════════

def create_cbz_from_images(parent, canvas, callbacks: dict):
    """
    Crée une archive CBZ à partir des images déposées (mode sans archive).
    Actif si : pas d'archive ouverte ET images présentes.
    """
    state = _state_module.state
    if not state.images_data:
        return

    if not _check_no_ico(parent):
        return
    if not _check_no_video(parent):
        return

    _auto_update_page_count_qt()

    # Applique les noms modifiés (depuis les NameEdit Qt)
    for entry in state.images_data:
        name_entry = entry.get("name_entry")
        if name_entry is not None:
            try:
                base = name_entry.toPlainText().strip()
            except AttributeError:
                base = str(name_entry)
            ext = entry["extension"]
            entry["orig_name"] = base if base.lower().endswith(ext.lower()) else base + ext

    render_mosaic = callbacks.get("render_mosaic", lambda: None)
    renumber_func = callbacks.get("renumber_btn_action")

    if not _validate_filenames_qt(parent, render_mosaic):
        return

    if not _check_animated_gifs_qt(parent):
        return

    if not _handle_duplicate_filenames_qt(parent, renumber_func):
        return

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
        _("buttons.create_cbz"),
        start,
        "Comic Book Archive (*.cbz)",
    )
    if not filepath:
        return
    get_config_manager().set('last_open_dir', os.path.dirname(os.path.abspath(filepath)))

    overlay = _SavingOverlay(canvas) if canvas else None
    try:
        _write_zip_with_progress(filepath, state.images_data, overlay)
    except Exception as e:
        if overlay:
            overlay.remove()
        ErrorDialog(parent,
                    _("messages.errors.create_archive_failed.title"),
                    _("messages.errors.create_archive_failed.message").format(error=e)).exec()
        return
    finally:
        if overlay:
            overlay.remove()

    state.current_file = filepath
    state.modified = False
    if callbacks.get("update_button_text"):
        callbacks["update_button_text"]()
    if callbacks.get("update_window_title"):
        callbacks["update_window_title"]()
    if callbacks.get("update_tabs"):
        callbacks["update_tabs"]()
    render_mosaic()
    InfoDialogClickablePath(parent,
                            "messages.info.cbz_created.title",
                            "messages.info.cbz_created.message",
                            filepath)


# ═══════════════════════════════════════════════════════════════════════════════
# 5 & 6. apply_new_names — Appliquer les modifications et sauvegarder/créer CBZ
# ═══════════════════════════════════════════════════════════════════════════════

def apply_new_names(parent, canvas, callbacks: dict):
    """
    Applique les nouveaux noms et sauvegarde ou crée une archive CBZ.

    - Si archive .cbz ouverte et modifiée → écrase le fichier CBZ existant
      (bouton « Appliquer et sauvegarder le CBZ »)
    - Si archive CBR/EPUB/PDF ouverte et modifiée → propose un nouveau fichier .cbz
      (bouton « Appliquer et créer un CBZ »)
    Logique identique à file_operations.apply_new_names.
    Retourne False si la sauvegarde n'a pas eu lieu (erreur ou annulation), True si succès.
    """
    state = _state_module.state
    if not state.current_file:
        return False

    if not _check_no_ico(parent):
        return False
    if not _check_no_video(parent):
        return False

    _auto_update_page_count_qt()

    # Applique les noms depuis les NameEdit Qt
    for entry in state.images_data:
        name_entry = entry.get("name_entry")
        if name_entry is not None:
            try:
                base = name_entry.toPlainText().strip()
            except AttributeError:
                base = str(name_entry)
            ext = entry["extension"]
            entry["orig_name"] = base if base.lower().endswith(ext.lower()) else base + ext

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
        return False

    render_mosaic = callbacks.get("render_mosaic", lambda: None)
    if not _validate_filenames_qt(parent, render_mosaic):
        return False

    if not _check_animated_gifs_qt(parent):
        return False

    ext = os.path.splitext(state.current_file)[1].lower()
    safe_delete = callbacks.get("safe_delete_file", _safe_delete)
    get_temp_dir = callbacks.get("get_mosaicview_temp_dir", tempfile.gettempdir)

    if ext == ".cbz":
        # Écrase le fichier CBZ existant via un fichier temporaire
        try:
            temp_file = tempfile.NamedTemporaryFile(
                delete=False, suffix=".cbz", dir=get_temp_dir()
            ).name
            with zipfile.ZipFile(temp_file, "w") as zf:
                for entry in state.images_data:
                    if entry["bytes"] is not None and not entry.get("is_dir"):
                        zf.writestr(entry["orig_name"], entry["bytes"])
            # Vérifie la place disponible sur le disque de destination avant le move
            temp_size = os.path.getsize(temp_file)
            dest_dir = os.path.dirname(os.path.abspath(state.current_file))
            free_space = shutil.disk_usage(dest_dir).free
            if temp_size > free_space:
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
                _needed = format_file_size(temp_size)
                _free   = format_file_size(free_space)
                ErrorDialog(parent,
                            lambda: _("messages.errors.save_failed.title"),
                            lambda: _("messages.errors.disk_full.message",
                                      needed=_needed, free=_free)).exec()
                return False
            shutil.move(temp_file, state.current_file)
            InfoDialogClickablePath(parent,
                                    "messages.info.cbz_saved.title",
                                    "messages.info.cbz_saved.message",
                                    state.current_file)
        except Exception as e:
            ErrorDialog(parent,
                        _("messages.errors.save_failed.title"),
                        _("messages.errors.save_failed.message", error=e)).exec()
            return False

    elif ext in (".cbr", ".cbt", ".epub"):
        initial_dir = os.path.dirname(os.path.abspath(state.current_file))
        if not initial_dir:
            initial_dir = get_config_manager().get('last_open_dir', "")
        initial_name = os.path.splitext(os.path.basename(state.current_file))[0] + ".cbz"
        new_file = _get_save_filename(
            parent,
            _("labels.apply_create_cbz"),
            os.path.join(initial_dir, initial_name),
            "Comic Book Archive (*.cbz)",
        )
        if not new_file:
            return False
        get_config_manager().set('last_open_dir', os.path.dirname(os.path.abspath(new_file)))
        try:
            with zipfile.ZipFile(new_file, "w") as zf:
                for entry in state.images_data:
                    if entry["bytes"] is not None and not entry.get("is_dir"):
                        zf.writestr(entry["orig_name"], entry["bytes"])
            old_file_cbz = state.current_file
            state.current_file = new_file

            def _after_cbz_converted(result):
                if result:
                    try:
                        if old_file_cbz and os.path.exists(old_file_cbz):
                            safe_delete(old_file_cbz)
                    except Exception as e:
                        ErrorDialog(parent,
                                    _("messages.errors.save_failed.title"),
                                    _("messages.errors.delete_error", error=e)).exec()

            SaveSuccessDialog(
                parent,
                "messages.info.cbz_converted.title",
                "messages.info.cbz_converted.message",
                new_file,
                "messages.info.cbz_converted.question",
                on_done=_after_cbz_converted,
            )
        except Exception as e:
            ErrorDialog(parent,
                        _("messages.errors.save_failed.title"),
                        _("messages.errors.save_failed.message", error=e)).exec()
            return False

    elif ext == ".pdf":
        initial_dir = os.path.dirname(os.path.abspath(state.current_file))
        if not initial_dir:
            initial_dir = get_config_manager().get('last_open_dir', "")
        initial_name = os.path.splitext(os.path.basename(state.current_file))[0] + ".cbz"
        new_file = _get_save_filename(
            parent,
            _("labels.apply_create_cbz"),
            os.path.join(initial_dir, initial_name),
            "Comic Book Archive (*.cbz)",
        )
        if not new_file:
            return False
        get_config_manager().set('last_open_dir', os.path.dirname(os.path.abspath(new_file)))
        try:
            with zipfile.ZipFile(new_file, "w") as zf:
                for entry in state.images_data:
                    if entry["bytes"] is not None and not entry.get("is_dir"):
                        zf.writestr(entry["orig_name"], entry["bytes"])
            old_file_pdf = state.current_file
            state.current_file = new_file

            def _after_pdf_converted(result):
                if result:
                    try:
                        if old_file_pdf and os.path.exists(old_file_pdf):
                            safe_delete(old_file_pdf)
                    except Exception as e:
                        ErrorDialog(parent,
                                    _("messages.errors.save_failed.title"),
                                    _("messages.errors.delete_error", error=e)).exec()

            SaveSuccessDialog(
                parent,
                "messages.info.cbz_converted_from_pdf.title",
                "messages.info.cbz_converted_from_pdf.message",
                new_file,
                "messages.info.cbz_converted_from_pdf.question",
                on_done=_after_pdf_converted,
            )
        except Exception as e:
            ErrorDialog(parent,
                        _("messages.errors.save_failed.title"),
                        _("messages.errors.save_failed.message", error=e)).exec()
            return False

    state.modified = False
    if callbacks.get("render_mosaic"):
        callbacks["render_mosaic"]()
    if callbacks.get("update_button_text"):
        callbacks["update_button_text"]()
    if callbacks.get("update_tabs"):
        callbacks["update_tabs"]()
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# Helper suppression sécurisée (fallback si send2trash absent)
# ═══════════════════════════════════════════════════════════════════════════════

def _safe_delete(filepath):
    try:
        from send2trash import send2trash
        send2trash(filepath)
    except ImportError:
        os.remove(filepath)
