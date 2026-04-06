"""
modules/qt/archive_loader.py
Chargement d'archives CBZ/CBR/EPUB pour la version PySide6.
Remplace modules/archive_loading.py (version tkinter).

Comportement reproduit à l'identique :
  - Texte rouge centré sur le canvas pendant le chargement (pas QProgressDialog)
  - Texte mis à jour avec le pourcentage en temps réel
  - testzip (intégrité ZIP)
  - comic_metadata (ComicInfo.xml)
  - detect_corrupted_images après chargement
  - Dialogue correction d'extension (CBZ↔CBR)
  - load_archive (1 fichier) et load_multiple_archives (plusieurs fichiers)
  - Tri naturel, préfixe "NEW-" pour les archives suivantes
"""

import os
import re
import sys
import time
import tarfile
import zipfile
import rarfile
import subprocess
import threading

from PySide6.QtCore import QThread, Signal, QObject, Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)
from PySide6.QtGui import QFont, QColor

from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font

def _get_7z_exe():
    """Retourne le chemin vers 7z.exe embarqué (compatible PyInstaller)."""
    try:
        base = sys._MEIPASS
    except AttributeError:
        base = os.path.abspath(".")
    return os.path.join(base, "7zip", "7z.exe")


def _to_short_path(path):
    """Convertit un chemin Unicode en chemin court 8.3 pour 7z.exe (Windows)."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024)
        return buf.value or path
    except Exception:
        return path


def _list_7z_files(archive_path):
    """Retourne la liste des noms de fichiers dans une archive 7z (fichiers uniquement, pas les dossiers).
    Lève une exception si 7z.exe signale une erreur fatale (returncode >= 2)."""
    exe = _get_7z_exe()
    short_path = _to_short_path(archive_path)
    result = subprocess.run(
        [exe, "l", "-ba", "-slt", short_path],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
    )
    if result.returncode >= 2:
        raise ValueError(f"7z returncode {result.returncode}: not a valid 7z archive")
    names = []
    current_path = None
    current_attr = None
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            # Fin d'un bloc : enregistrer si c'est un fichier (Attributes = A)
            if current_path is not None and current_attr == 'A':
                names.append(current_path.replace('\\', '/'))
            current_path = None
            current_attr = None
        elif line.startswith("Path = "):
            current_path = line[7:]
        elif line.startswith("Attributes = "):
            current_attr = line[13:]
    # Dernier bloc sans ligne vide finale
    if current_path is not None and current_attr == 'A':
        names.append(current_path.replace('\\', '/'))
    return names


def _read_7z_file(archive_path, member_name):
    """Extrait un fichier d'une archive 7z et retourne ses bytes via stdout."""
    exe = _get_7z_exe()
    short_path = _to_short_path(archive_path)
    # Passer le nom seul + -r pour recherche récursive dans les sous-dossiers
    filename = os.path.basename(member_name.replace('\\', '/'))
    result = subprocess.run(
        [exe, "e", "-so", "-r", short_path, filename],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


from modules.qt import state as _state_module
from modules.qt.entries import create_entry
from modules.qt.localization import _, _wt
from modules.qt.comic_info import read_comic_info
from modules.qt.canvas_overlay_qt import show_canvas_text as _show_canvas_text, hide_canvas_text as _hide_canvas_text
from modules.qt.mosaic_canvas import build_qimage_for_entry
from modules.qt.archive_type_detector import detect_archive_type


IMAGE_EXTS = (
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
    '.tiff', '.tif', '.ico', '.jfif', '.pjpeg', '.pjp',
)

_TYPE_TO_EXT = {
    "CBZ":  ".cbz",
    "EPUB": ".epub",
    "CBR":  ".cbr",
    "CB7":  ".cb7",
    "CBT":  ".cbt",
}


def _natural_sort_key(text):
    name = os.path.splitext(text)[0]
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', name)]


class _CorruptedImagesDialog(QDialog):
    """Dialogue d'avertissement images corrompues — thème, langue et police dynamiques."""

    def __init__(self, parent, corrupted_names: list, total: int):
        super().__init__(parent)
        self._corrupted_names = corrupted_names
        self._total = total
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(8)

        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignLeft)
        self._lbl.setWordWrap(True)
        layout.addWidget(self._lbl)

        self._btn_ok = QPushButton()
        self._btn_ok.clicked.connect(self.accept)
        layout.addWidget(self._btn_ok, alignment=Qt.AlignCenter)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    def _retranslate(self):
        from modules.qt.state import get_current_theme
        from modules.qt.font_manager_qt import get_current_font
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }} "
            f"QLabel  {{ color: {theme['text']}; }} "
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 12px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        font = get_current_font()
        self._lbl.setFont(font)
        self._btn_ok.setFont(font)

        count = len(self._corrupted_names)
        lines = [f"• {n}" for n in self._corrupted_names[:5]]
        if count > 5:
            lines.append(_("messages.warnings.corrupted_images.more", count=count - 5))
        msg = (
            _("messages.warnings.corrupted_images.summary", count=count, total=self._total) + "\n\n"
            + _("messages.warnings.corrupted_images.hint") + "\n\n"
            + "\n".join(lines)
        )
        self.setWindowTitle(_wt("messages.warnings.corrupted_images.title"))
        self._lbl.setText(msg)
        self._btn_ok.setText(_("buttons.ok"))

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass


def _detect_corrupted_qt(win):
    """
    Version Qt de detect_corrupted_images (sans tkinter).
    Affiche un dialogue si des images corrompues sont trouvées.
    """
    state = _state_module.state
    if not state:
        return

    corrupted = [e["orig_name"] for e in state.images_data if e.get("is_corrupted")]
    if corrupted:
        total = len([e for e in state.images_data if e.get("is_image")])
        _CorruptedImagesDialog(win, corrupted, total).exec()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialogue d'erreur/avertissement générique
# ═══════════════════════════════════════════════════════════════════════════════
class _MessageDialog(QDialog):
    """Remplace QMessageBox.critical/warning — respecte thème, police, langue."""
    def __init__(self, parent, title_key: str, msg: str):
        super().__init__(parent)
        self._title_key = title_key
        self._msg       = msg
        self.setModal(True)

        layout = QVBoxLayout(self)
        self._lbl = QLabel()
        self._lbl.setWordWrap(True)
        self._lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self._btn_ok = QPushButton()
        self._btn_ok.clicked.connect(self.accept)
        btn_layout.addWidget(self._btn_ok)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )
        font = _get_current_font(10)
        self.setWindowTitle(_wt(self._title_key))
        self._lbl.setText(self._msg)
        self._lbl.setFont(font)
        self._lbl.setStyleSheet(f"color: {theme['text']};")
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self._btn_ok.setText(_("buttons.ok"))
        self._btn_ok.setFont(font)
        self._btn_ok.setStyleSheet(btn_style)

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Dialogue correction d'extension
# ═══════════════════════════════════════════════════════════════════════════════
class ExtensionCorrectionDialog(QDialog):
    """
    Affiché quand l'extension d'un fichier ne correspond pas à son format réel.
    Résultat : 'rename', 'keep', ou None (annuler).
    """
    def __init__(self, parent, filepath: str, detected: str, declared: str):
        super().__init__(parent)
        self.result_choice = None
        self._filepath = filepath
        self._detected = detected
        self._declared = declared
        self._rename_ext = _TYPE_TO_EXT.get(detected.upper(), "." + detected.lower())
        self.setModal(True)

        layout = QVBoxLayout(self)
        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        layout.addWidget(self._lbl)

        btn_layout = QHBoxLayout()
        self._btn_rename = QPushButton()
        self._btn_keep   = QPushButton()
        self._btn_cancel = QPushButton()

        self._btn_rename.clicked.connect(self._rename)
        self._btn_keep.clicked.connect(self._keep)
        self._btn_cancel.clicked.connect(self._cancel)

        btn_layout.addWidget(self._btn_rename)
        btn_layout.addWidget(self._btn_keep)
        btn_layout.addWidget(self._btn_cancel)
        layout.addLayout(btn_layout)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self.setWindowTitle(_wt("messages.extension_mismatch_generic.title"))
        filename = os.path.basename(self._filepath)
        self._lbl.setText(_(
            "messages.extension_mismatch_generic.message",
            filename=filename,
            real=self._detected.upper(),
            declared=self._declared.upper(),
        ))
        self._lbl.setFont(font)
        self._lbl.setStyleSheet(f"color: {theme['text']};")
        for btn in (self._btn_rename, self._btn_keep, self._btn_cancel):
            btn.setFont(font)
            btn.setStyleSheet(btn_style)
        self._btn_rename.setText(_("buttons.rename_to_ext", ext=self._rename_ext))
        self._btn_keep.setText(_("buttons.open_without_rename"))
        self._btn_cancel.setText(_("buttons.cancel"))

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def _rename(self):
        self.result_choice = 'rename'
        self.accept()

    def _keep(self):
        self.result_choice = 'keep'
        self.accept()

    def _cancel(self):
        self.result_choice = None
        self.reject()


# ═══════════════════════════════════════════════════════════════════════════════
# Worker thread de chargement
# ═══════════════════════════════════════════════════════════════════════════════
class LoadWorker(QThread):
    """
    Charge une ou plusieurs archives en arrière-plan.
    Émet :
      progress(int)                    — pourcentage 0-100
      finished(list, list, str|None)   — (images_data, errors, first_filepath)
      error(str)                       — message d'erreur fatal
      need_ext_dialog(str, str, str)   — (filepath, detected, declared)
      cancelled()                      — utilisateur a annulé
    """
    progress        = Signal(int)
    load_finished   = Signal(list, list, str)   # images_data, errors, first_filepath
    error           = Signal(str)
    need_ext_dialog = Signal(str, str, str)
    cancelled       = Signal()

    def __init__(self, filepaths: list, multi: bool = False):
        super().__init__()
        self._filepaths  = filepaths
        self._multi      = multi
        self._ext_result = None
        self._ext_event  = threading.Event()
        self._cancelled  = threading.Event()

    def set_ext_result(self, result):
        self._ext_result = result
        self._ext_event.set()

    def _ask_ext(self, filepath, detected, declared):
        """Émet le signal, bloque jusqu'à réponse."""
        self._ext_event.clear()
        self._ext_result = None
        self.need_ext_dialog.emit(filepath, detected, declared)
        self._ext_event.wait()
        return self._ext_result

    # ── helpers détection / namelist ───────────────────────────────────────────

    _EXT_TO_TYPE = {
        ".cbz":  "CBZ",
        ".epub": "EPUB",
        ".cbr":  "CBR",
        ".cb7":  "CB7",
        ".cbt":  "CBT",
    }
    _ERROR_KEYS = {
        "CBZ":  "messages.errors.zip_invalid.message",
        "EPUB": "messages.errors.zip_invalid.message",
        "CBR":  "messages.errors.rar_invalid.message",
        "CB7":  "messages.errors.7z_invalid.message",
        "CBT":  "messages.errors.cbt_invalid.message",
    }

    def _get_namelist(self, filepath, real_type):
        """Retourne (files_list, error_str). error_str est None si succès."""
        try:
            if real_type in ("CBZ", "EPUB"):
                with zipfile.ZipFile(filepath, 'r') as archive:
                    nl = sorted(archive.namelist(), key=_natural_sort_key)
                if real_type == "EPUB":
                    nl = [f for f in nl if any(f.lower().endswith(e) for e in IMAGE_EXTS)]
                return nl, None
            elif real_type == "CBR":
                with rarfile.RarFile(filepath, 'r') as archive:
                    return sorted(archive.namelist(), key=_natural_sort_key), None
            elif real_type == "CB7":
                all_names = _list_7z_files(filepath)
                return sorted([f for f in all_names if not f.endswith('/')], key=_natural_sort_key), None
            elif real_type == "CBT":
                with tarfile.open(filepath, 'r:*') as archive:
                    return sorted(
                        [m.name for m in archive.getmembers() if m.isfile()
                         and any(m.name.lower().endswith(e) for e in IMAGE_EXTS)],
                        key=_natural_sort_key
                    ), None
        except Exception as e:
            return [], str(e)[:200]
        return [], "unknown format"

    def _resolve_filepath(self, filepath, declared_ext, real_type, actual_first_fp, is_multi_error_list=None):
        """
        Si real_type != declared type, demande renommage et renomme si besoin.
        Retourne le filepath final, ou None si annulé.
        is_multi_error_list : si non-None, les erreurs de renommage sont ajoutées à cette liste
                              plutôt qu'émises via self.error.
        """
        declared_type = self._EXT_TO_TYPE.get("." + declared_ext.lstrip("."))
        if real_type == declared_type:
            return filepath  # rien à faire

        choice = self._ask_ext(filepath, real_type, declared_ext.lstrip('.'))
        if choice is None:
            return None

        if choice == 'rename':
            new_ext = _TYPE_TO_EXT.get(real_type, declared_ext)
            new_filepath = os.path.splitext(filepath)[0] + new_ext
            try:
                os.rename(filepath, new_filepath)
                filepath = new_filepath
                if actual_first_fp is not None:
                    actual_first_fp[0] = filepath
            except Exception as e:
                msg = _("messages.errors.rename_failed.message", error=e)
                if is_multi_error_list is not None:
                    is_multi_error_list.append(msg)
                else:
                    self.error.emit(msg)
                return None

        return filepath

    def _read_entries(self, filepath, real_type, files_list, errors, add_prefix=False):
        """Lit les entrées d'une archive déjà ouverte et les ajoute à une liste retournée."""
        result = []
        ext = os.path.splitext(filepath)[1].lower()
        try:
            if real_type in ("CBZ", "EPUB"):
                with zipfile.ZipFile(filepath, 'r') as archive:
                    for file in files_list:
                        if self._cancelled.is_set():
                            return None
                        if file.endswith('/'):
                            continue
                        try:
                            data = archive.read(file)
                        except Exception as e:
                            data = None
                            errors.append(f"{file}: {str(e)[:50]}")
                        file_name = os.path.basename(file) if real_type == "EPUB" else file
                        entry = create_entry(file_name, data, IMAGE_EXTS)
                        if add_prefix:
                            entry["orig_name"] = "NEW-" + entry["orig_name"]
                            entry["source_archive"] = os.path.basename(filepath)
                        build_qimage_for_entry(entry)
                        result.append(entry)
            elif real_type == "CBR":
                with rarfile.RarFile(filepath, 'r') as archive:
                    for file in files_list:
                        if self._cancelled.is_set():
                            return None
                        if file.endswith('/'):
                            continue
                        try:
                            data = archive.read(file)
                        except Exception as e:
                            data = None
                            errors.append(f"{file}: {str(e)[:50]}")
                        entry = create_entry(file, data, IMAGE_EXTS)
                        if add_prefix:
                            entry["orig_name"] = "NEW-" + entry["orig_name"]
                            entry["source_archive"] = os.path.basename(filepath)
                        build_qimage_for_entry(entry)
                        result.append(entry)
            elif real_type == "CB7":
                for file in files_list:
                    if self._cancelled.is_set():
                        return None
                    try:
                        data = _read_7z_file(filepath, file)
                    except Exception as e:
                        data = None
                        errors.append(f"{file}: {str(e)[:50]}")
                    entry = create_entry(file, data, IMAGE_EXTS)
                    if add_prefix:
                        entry["orig_name"] = "NEW-" + entry["orig_name"]
                        entry["source_archive"] = os.path.basename(filepath)
                    build_qimage_for_entry(entry)
                    result.append(entry)
        except Exception as e:
            errors.append(str(e)[:200])
        return result

    # ── load_archive (single fichier) ──────────────────────────────────────────
    def load_archive(self, filepath, all_data, errors, actual_first_fp=None):
        """Charge un seul fichier CBZ/CBR/EPUB/CB7. Retourne False si annulé/erreur."""
        ext = os.path.splitext(filepath)[1].lower()

        real_type = detect_archive_type(filepath)
        if real_type is None:
            error_key = self._ERROR_KEYS.get(self._EXT_TO_TYPE.get(ext, "CBZ"), "messages.errors.zip_invalid.message")
            self.error.emit(_(error_key, file=os.path.basename(filepath)))
            return False

        filepath = self._resolve_filepath(filepath, ext, real_type, actual_first_fp)
        if filepath is None:
            self.cancelled.emit()
            return False

        files_list, err = self._get_namelist(filepath, real_type)
        if err:
            error_key = self._ERROR_KEYS.get(real_type, "messages.errors.zip_invalid.message")
            self.error.emit(_(error_key, file=os.path.basename(filepath)))
            return False
        if not files_list:
            return True

        total = len(files_list)
        entries = []
        if real_type in ("CBZ", "EPUB"):
            try:
                with zipfile.ZipFile(filepath, 'r') as archive:
                    for idx, file in enumerate(files_list, start=1):
                        if self._cancelled.is_set():
                            return False
                        if file.endswith('/'):
                            continue
                        try:
                            data = archive.read(file)
                        except Exception as e:
                            data = None
                            errors.append(f"{file}: {str(e)[:50]}")
                        file_name = os.path.basename(file) if real_type == "EPUB" else file
                        entry = create_entry(file_name, data, IMAGE_EXTS)
                        build_qimage_for_entry(entry)
                        all_data.append(entry)
                        self.progress.emit(int(idx / total * 100))
            except Exception as e:
                self.error.emit(str(e)[:200])
                return False
        elif real_type == "CBR":
            try:
                with rarfile.RarFile(filepath, 'r') as archive:
                    for idx, file in enumerate(files_list, start=1):
                        if self._cancelled.is_set():
                            return False
                        if file.endswith('/'):
                            continue
                        try:
                            data = archive.read(file)
                        except Exception as e:
                            data = None
                            errors.append(f"{file}: {str(e)[:50]}")
                        entry = create_entry(file, data, IMAGE_EXTS)
                        build_qimage_for_entry(entry)
                        all_data.append(entry)
                        self.progress.emit(int(idx / total * 100))
            except Exception as e:
                self.error.emit(str(e)[:200])
                return False
        elif real_type == "CB7":
            for idx, file in enumerate(files_list, start=1):
                if self._cancelled.is_set():
                    return False
                try:
                    data = _read_7z_file(filepath, file)
                except Exception as e:
                    data = None
                    errors.append(f"{file}: {str(e)[:50]}")
                entry = create_entry(file, data, IMAGE_EXTS)
                build_qimage_for_entry(entry)
                all_data.append(entry)
                self.progress.emit(int(idx / total * 100))
        elif real_type == "CBT":
            try:
                with tarfile.open(filepath, 'r:*') as archive:
                    for idx, file in enumerate(files_list, start=1):
                        if self._cancelled.is_set():
                            return False
                        try:
                            member = archive.getmember(file)
                            data = archive.extractfile(member).read()
                        except Exception as e:
                            data = None
                            errors.append(f"{file}: {str(e)[:50]}")
                        entry = create_entry(os.path.basename(file), data, IMAGE_EXTS)
                        build_qimage_for_entry(entry)
                        all_data.append(entry)
                        self.progress.emit(int(idx / total * 100))
            except Exception as e:
                self.error.emit(str(e)[:200])
                return False

        return True

    # ── load_multiple_archives ─────────────────────────────────────────────────
    def load_multiple_archives(self, filepaths, all_data, errors, actual_first_fp=None):
        """Charge plusieurs fichiers. Retourne False si annulé/erreur fatale."""
        _TYPE_TO_FORMAT = {"CBZ": "zip", "EPUB": "zip", "CBR": "rar", "CB7": "7z", "CBT": "tar"}

        # Phase 1 : détection + dialogues extension + namelists
        archive_namelists = []
        for orig_filepath in filepaths:
            ext = os.path.splitext(orig_filepath)[1].lower()
            current_filepath = orig_filepath

            real_type = detect_archive_type(current_filepath)
            if real_type is None:
                error_key = self._ERROR_KEYS.get(self._EXT_TO_TYPE.get(ext, "CBZ"), "messages.errors.zip_invalid.message")
                errors.append(_(error_key, file=os.path.basename(current_filepath)))
                continue

            current_filepath = self._resolve_filepath(
                current_filepath, ext, real_type, actual_first_fp,
                is_multi_error_list=errors
            )
            if current_filepath is None:
                continue

            files_list, err = self._get_namelist(current_filepath, real_type)
            if err or not files_list:
                if err:
                    error_key = self._ERROR_KEYS.get(real_type, "messages.errors.zip_invalid.message")
                    errors.append(_(error_key, file=os.path.basename(current_filepath)))
                continue

            archive_namelists.append((current_filepath, _TYPE_TO_FORMAT[real_type], real_type, files_list))

        if not archive_namelists:
            self.cancelled.emit()
            return False

        if actual_first_fp is not None:
            actual_first_fp[0] = archive_namelists[0][0]

        # Phase 2 : vraie lecture avec progression
        total_files = sum(
            len([f for f in files_list if not f.endswith("/")])
            for _, _, _, files_list in archive_namelists
        )
        processed_files = 0

        for archive_idx, (filepath, actual_format, real_type, files_list) in enumerate(archive_namelists):
            add_prefix = archive_idx > 0

            if actual_format == "zip":
                try:
                    with zipfile.ZipFile(filepath, 'r') as archive:
                        for file in files_list:
                            if file.endswith('/'):
                                continue
                            try:
                                data = archive.read(file)
                            except Exception as e:
                                errors.append(f"{file}: {str(e)[:50]}")
                                data = None
                            file_name = os.path.basename(file) if real_type == "EPUB" else file
                            entry = create_entry(file_name, data, IMAGE_EXTS)
                            if add_prefix:
                                entry["orig_name"] = "NEW-" + entry["orig_name"]
                                entry["source_archive"] = os.path.basename(filepath)
                            build_qimage_for_entry(entry)
                            all_data.append(entry)
                            processed_files += 1
                            self.progress.emit(int(processed_files / total_files * 100) if total_files > 0 else 0)
                except Exception as e:
                    errors.append(str(e)[:100])

            elif actual_format == "7z":
                for file in files_list:
                    try:
                        data = _read_7z_file(filepath, file)
                    except Exception as e:
                        errors.append(f"{file}: {str(e)[:50]}")
                        data = None
                    entry = create_entry(file, data, IMAGE_EXTS)
                    if add_prefix:
                        entry["orig_name"] = "NEW-" + entry["orig_name"]
                        entry["source_archive"] = os.path.basename(filepath)
                    build_qimage_for_entry(entry)
                    all_data.append(entry)
                    processed_files += 1
                    self.progress.emit(int(processed_files / total_files * 100) if total_files > 0 else 0)

            elif actual_format == "rar":
                try:
                    with rarfile.RarFile(filepath, 'r') as archive:
                        for file in files_list:
                            if file.endswith('/'):
                                continue
                            try:
                                data = archive.read(file)
                            except Exception as e:
                                errors.append(f"{file}: {str(e)[:50]}")
                                data = None
                            entry = create_entry(file, data, IMAGE_EXTS)
                            if add_prefix:
                                entry["orig_name"] = "NEW-" + entry["orig_name"]
                                entry["source_archive"] = os.path.basename(filepath)
                            build_qimage_for_entry(entry)
                            all_data.append(entry)
                            processed_files += 1
                            self.progress.emit(int(processed_files / total_files * 100) if total_files > 0 else 0)
                except Exception as e:
                    errors.append(str(e)[:100])

            elif actual_format == "tar":
                try:
                    with tarfile.open(filepath, 'r:*') as archive:
                        for file in files_list:
                            try:
                                member = archive.getmember(file)
                                data = archive.extractfile(member).read()
                            except Exception as e:
                                errors.append(f"{file}: {str(e)[:50]}")
                                data = None
                            entry = create_entry(os.path.basename(file), data, IMAGE_EXTS)
                            if add_prefix:
                                entry["orig_name"] = "NEW-" + entry["orig_name"]
                                entry["source_archive"] = os.path.basename(filepath)
                            build_qimage_for_entry(entry)
                            all_data.append(entry)
                            processed_files += 1
                            self.progress.emit(int(processed_files / total_files * 100) if total_files > 0 else 0)
                except Exception as e:
                    errors.append(str(e)[:100])

        return True

    # ── run ────────────────────────────────────────────────────────────────────
    def run(self):
        errors   = []
        all_data = []
        actual_first_fp = [self._filepaths[0] if self._filepaths else ""]

        if self._multi:
            ok = self.load_multiple_archives(self._filepaths, all_data, errors, actual_first_fp)
        else:
            ok = self.load_archive(self._filepaths[0], all_data, errors, actual_first_fp)

        if not ok:
            return

        if self._multi:
            all_data.sort(key=lambda e: _natural_sort_key(e["orig_name"]))

        self.load_finished.emit(all_data, errors, actual_first_fp[0])


# Référence globale aux workers orphelins (annulés mais encore en cours)
# pour éviter que Qt les détruise pendant que le thread tourne.
_orphan_workers: list = []


# ═══════════════════════════════════════════════════════════════════════════════
# Gestionnaire de chargement (interface avec MainWindow)
# ═══════════════════════════════════════════════════════════════════════════════
class ArchiveLoader(QObject):
    """
    Orchestre le chargement d'une ou plusieurs archives.
    Pendant le chargement, affiche un texte rouge centré sur le canvas
    (même comportement que la version tkinter).

    Usage :
        loader = ArchiveLoader(parent_window, canvas, state)
        loader.load([filepath])
        loader.load([fp1, fp2, ...])

    Signal :
        loading_finished  — émis quand le rendu est terminé
    """
    loading_started  = Signal()
    loading_finished = Signal()

    def __init__(self, parent_window, canvas, state):
        super().__init__(parent_window)
        self._win    = parent_window
        self._canvas = canvas
        self._state  = state
        self._worker = None
        # QGraphicsTextItem rouge centré (affiché pendant le chargement)
        self._loading_item = None
        self._loading_item_holder = [None]
        # Texte cliquable Annuler (comme web_import_qt)
        self._cancel_item_holder = [None]

    # ──────────────────────────────────────────────────────────────────────────
    # Démarrage du chargement
    # ──────────────────────────────────────────────────────────────────────────
    def load(self, filepaths: list):
        if not filepaths:
            return

        multi = len(filepaths) > 1

        # Réinitialise l'état (identique à load_archive original)
        st = self._state
        st.images_data       = []
        st.modified          = False
        st.selected_indices.clear()
        st.current_file      = filepaths[0]  # premier fichier (comme l'original)
        st.comic_metadata    = None
        st.original_page_count = None
        st.needs_renumbering = True
        st.merge_counter     = 0
        st.first_image_dir   = None
        st.all_entries       = []
        st.current_directory = ""
        st.focused_index     = None

        # Vide la scène (supprime les 3 lignes du canvas vide) avant d'afficher le texte rouge
        self._canvas._empty_items.clear()
        self._canvas._items.clear()
        self._canvas._drop_indicator_items.clear()
        self._canvas.scene().clear()

        # Affiche le texte rouge centré sur le canvas (comme l'original)
        self._show_loading_text(0)

        # Libère l'ancien worker s'il existe encore (chargement consécutifs)
        if self._worker is not None:
            try:
                self._worker.progress.disconnect(self._on_progress)
                self._worker.load_finished.disconnect(self._on_finished)
                self._worker.error.disconnect(self._on_error)
                self._worker.cancelled.disconnect(self._on_cancelled)
                self._worker.need_ext_dialog.disconnect(self._on_need_ext_dialog)
            except RuntimeError:
                pass
            self._worker.deleteLater()
            self._worker = None

        self._worker = LoadWorker(filepaths, multi=multi)
        self._worker.progress.connect(self._on_progress)
        self._worker.load_finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.need_ext_dialog.connect(self._on_need_ext_dialog)

        self.loading_started.emit()
        self._worker.start()

    # ──────────────────────────────────────────────────────────────────────────
    # Texte rouge centré sur le canvas
    # ──────────────────────────────────────────────────────────────────────────
    def _show_loading_text(self, percent: int):
        if self._worker is None:
            return
        _show_canvas_text(self._canvas, _("labels.loading", percent=percent), self._loading_item_holder)
        self._loading_item = self._loading_item_holder[0]
        self._show_cancel_button()

    def _hide_loading_text(self):
        _hide_canvas_text(self._canvas, self._loading_item_holder)
        self._loading_item = None
        self._hide_cancel_button()

    # ──────────────────────────────────────────────────────────────────────────
    # Texte cliquable Annuler (comme web_import_qt._CancelTextItem)
    # ──────────────────────────────────────────────────────────────────────────
    def _show_cancel_button(self):
        from modules.qt.web_import_qt import _show_cancel_item
        cancel_text = f"[ {_('buttons.cancel')} ]"
        _show_cancel_item(self._canvas, cancel_text, self._cancel_item_holder, self.cancel,
                          anchor_lbl=self._loading_item_holder[0])

    def _hide_cancel_button(self):
        _hide_canvas_text(self._canvas, self._cancel_item_holder)

    # ──────────────────────────────────────────────────────────────────────────
    # Slots signaux worker
    # ──────────────────────────────────────────────────────────────────────────
    def cancel(self):
        """Annule le chargement en cours et déconnecte les signaux du worker."""
        if self._worker is not None:
            self._worker._cancelled.set()
            try:
                self._worker.progress.disconnect(self._on_progress)
                self._worker.load_finished.disconnect(self._on_finished)
                self._worker.error.disconnect(self._on_error)
                self._worker.cancelled.disconnect(self._on_cancelled)
                self._worker.need_ext_dialog.disconnect(self._on_need_ext_dialog)
            except RuntimeError:
                pass
            # Garde une référence globale jusqu'à la fin du thread (évite la destruction prématurée)
            worker = self._worker
            self._worker = None
            worker.setParent(None)
            _orphan_workers.append(worker)
            def _on_thread_done(w=worker):
                try:
                    _orphan_workers.remove(w)
                except ValueError:
                    pass
                w.deleteLater()
            worker.finished.connect(_on_thread_done)  # QThread::finished
            self._hide_loading_text()
            self._state.current_file = None
            self._canvas.render_mosaic()
            self.loading_finished.emit()

    def _cleanup_worker(self):
        """Libère le worker Qt après la fin du thread."""
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _on_progress(self, pct: int):
        self._show_loading_text(pct)

    def _on_finished(self, images_data: list, errors: list, first_filepath: str):
        self._hide_loading_text()
        self._cleanup_worker()

        st = self._state
        st.images_data       = images_data
        st.all_entries       = list(images_data)
        st.current_directory = ""
        st.needs_renumbering = True
        st.focused_index     = None
        if first_filepath:
            st.current_file = first_filepath

        # comic_metadata (comme l'original via file_operations.py)
        if first_filepath:
            st.comic_metadata = None
            st.original_page_count = None
            try:
                st.comic_metadata = read_comic_info(first_filepath)
                if st.comic_metadata and st.comic_metadata.get('page_count'):
                    try:
                        st.original_page_count = int(st.comic_metadata['page_count'])
                    except (ValueError, TypeError):
                        pass
                from modules.qt.comic_info import build_page_attrs_map
                build_page_attrs_map(st)
            except Exception:
                pass

        self._canvas.render_mosaic()
        self.loading_finished.emit()

        # Détecte les images corrompues (comme l'original)
        _detect_corrupted_qt(self._win)

        if errors:
            dlg = _MessageDialog(self._win, "messages.warnings.files_ignored.title", "\n".join(errors[:20]))
            dlg.exec()

    def _on_error(self, msg: str):
        self._hide_loading_text()
        self._cleanup_worker()
        self._state.current_file = None
        self._canvas.render_mosaic()
        self.loading_finished.emit()
        dlg = _MessageDialog(self._win, "messages.warnings.cannot_open_file.title", msg)
        dlg.exec()

    def _on_cancelled(self):
        self._hide_loading_text()
        self._cleanup_worker()
        self._state.current_file = None
        self._canvas.render_mosaic()
        self.loading_finished.emit()

    def _on_need_ext_dialog(self, filepath: str, detected: str, declared: str):
        """Affiché dans le thread principal, réponse transmise au worker."""
        dlg = ExtensionCorrectionDialog(self._win, filepath, detected, declared)
        dlg.exec()
        # Réaffiche le texte rouge après fermeture du dialogue modal
        self._show_loading_text(0)
        self._worker.set_ext_result(dlg.result_choice)
