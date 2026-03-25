"""
modules/qt/import_merge_qt.py
Fusion d'une archive CBZ/CBR dans le comics courant — version PySide6.
Reproduit à l'identique import_merge.py (import_and_merge_archive).
"""

import zipfile
import rarfile
import os

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import QDialog

from modules.qt.localization import _
from modules.qt.canvas_overlay_qt import show_canvas_text as _show_canvas_text, hide_canvas_text as _hide_canvas_text
from modules.qt.entries import create_entry
from modules.qt.archive_loader import ExtensionCorrectionDialog, _natural_sort_key, _list_7z_files, _read_7z_file
from modules.qt.pdf_loading_qt import _MsgDialog

IMAGE_EXTS = (
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
    '.tiff', '.tif', '.ico', '.jfif', '.pjpeg', '.pjp',
)


# ═══════════════════════════════════════════════════════════════════════════════
# Worker thread — reproduit import_worker() de import_and_merge_archive
# ═══════════════════════════════════════════════════════════════════════════════
class ImportMergeWorker(QThread):
    progress     = Signal(int)    # percent
    finished     = Signal(list)   # new_entries
    error        = Signal(str)
    cancelled    = Signal()
    bad_zip_file = Signal()       # CBZ qui est en fait un RAR
    bad_rar_file = Signal()       # CBR qui est en fait un ZIP

    def __init__(self, filepath: str, merge_prefix: str):
        import threading
        super().__init__()
        self._filepath     = filepath
        self._merge_prefix = merge_prefix
        self._cancelled    = threading.Event()

    def run(self):
        filepath     = self._filepath
        merge_prefix = self._merge_prefix
        ext          = os.path.splitext(filepath)[1].lower()
        new_entries  = []

        def _add_prefix(entry):
            orig_name = entry["orig_name"]
            if '/' in orig_name:
                parts     = orig_name.rsplit('/', 1)
                path_part = parts[0]
                filename  = parts[1] if len(parts) > 1 else ""
                if filename:
                    entry["orig_name"] = merge_prefix + path_part + '/' + merge_prefix + filename
                else:
                    entry["orig_name"] = merge_prefix + orig_name
            else:
                entry["orig_name"] = merge_prefix + orig_name
            entry["source_archive"] = os.path.basename(filepath)

        def _load_zip(archive_path):
            with zipfile.ZipFile(archive_path, 'r') as archive:
                files_list = sorted(archive.namelist(), key=_natural_sort_key)
                if os.path.splitext(archive_path)[1].lower() == ".epub":
                    files_list = [f for f in files_list if any(f.lower().endswith(e) for e in IMAGE_EXTS)]
                total = len([f for f in files_list if not f.endswith("/")])
                count = 0
                for file in files_list:
                    if self._cancelled.is_set():
                        return False
                    data      = archive.read(file) if not file.endswith("/") else None
                    file_name = os.path.basename(file) if os.path.splitext(archive_path)[1].lower() == ".epub" else file
                    entry     = create_entry(file_name, data, IMAGE_EXTS)
                    _add_prefix(entry)
                    new_entries.append(entry)
                    if not file.endswith("/"):
                        count += 1
                        self.progress.emit(int(count / total * 100))
            return True

        def _load_rar(archive_path):
            with rarfile.RarFile(archive_path, 'r') as archive:
                files_list = sorted(archive.namelist(), key=_natural_sort_key)
                total = len([f for f in files_list if not f.endswith("/")])
                count = 0
                for file in files_list:
                    if self._cancelled.is_set():
                        return False
                    data  = archive.read(file) if not file.endswith("/") else None
                    entry = create_entry(file, data, IMAGE_EXTS)
                    _add_prefix(entry)
                    new_entries.append(entry)
                    if not file.endswith("/"):
                        count += 1
                        self.progress.emit(int(count / total * 100))
            return True

        def _load_7z(archive_path):
            files_list = sorted(_list_7z_files(archive_path), key=_natural_sort_key)
            total = len(files_list)
            for count, file in enumerate(files_list, start=1):
                if self._cancelled.is_set():
                    return False
                try:
                    data = _read_7z_file(archive_path, file)
                except Exception:
                    data = None
                entry = create_entry(file, data, IMAGE_EXTS)
                _add_prefix(entry)
                new_entries.append(entry)
                self.progress.emit(int(count / total * 100) if total else 0)
            return True

        try:
            ok = True
            if ext in (".cbz", ".epub"):
                try:
                    ok = _load_zip(filepath)
                except zipfile.BadZipFile:
                    self.bad_zip_file.emit()
                    return
            elif ext == ".cb7":
                ok = _load_7z(filepath)
            elif ext == ".cbr":
                try:
                    ok = _load_rar(filepath)
                except (rarfile.NotRarFile, rarfile.BadRarFile):
                    self.bad_rar_file.emit()
                    return

            if not ok:
                self.cancelled.emit()
                return
            self.finished.emit(new_entries)

        except Exception as e:
            self.error.emit(str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# import_and_merge_archive — reproduit import_and_merge_archive de import_merge.py
# ═══════════════════════════════════════════════════════════════════════════════
def import_and_merge_archive(filepath: str, win, canvas, state):
    """Importe une archive CBZ/CBR et fusionne ses pages dans le comics actuel."""

    state.merge_counter += 1
    merge_prefix = f"NEW{state.merge_counter:02d}-"

    # Label rouge sur le canvas (comme l'original tkinter)
    item_holder        = []
    cancel_item_holder = [None]
    _worker_ref        = [None]

    def _update_label(percent):
        if _worker_ref[0] is None:
            return
        _show_canvas_text(canvas, _("labels.import_progress", percent=percent), item_holder)
        from modules.qt.web_import_qt import _show_cancel_item
        _show_cancel_item(canvas, f"[ {_('buttons.cancel')} ]", cancel_item_holder, _cancel)

    def _remove_label():
        _hide_canvas_text(canvas, item_holder)
        _hide_canvas_text(canvas, cancel_item_holder)

    def _cancel():
        worker = _worker_ref[0]
        if worker is None:
            return
        worker._cancelled.set()
        try:
            worker.progress.disconnect(_update_label)
            worker.finished.disconnect(on_finished)
            worker.cancelled.disconnect(on_cancelled)
            worker.error.disconnect(on_error)
            worker.bad_zip_file.disconnect(on_bad_zip)
            worker.bad_rar_file.disconnect(on_bad_rar)
        except RuntimeError:
            pass
        _worker_ref[0] = None
        _remove_label()
        canvas.render_mosaic()

    _update_label(0)

    def _start_worker(actual_filepath):
        worker = ImportMergeWorker(actual_filepath, merge_prefix)
        _worker_ref[0] = worker

        def _cleanup():
            _worker_ref[0] = None
            worker.deleteLater()

        worker.progress.connect(_update_label)
        worker.finished.connect(lambda *_: _cleanup())
        worker.cancelled.connect(lambda: _cleanup())
        worker.error.connect(lambda *_: _cleanup())
        worker.bad_zip_file.connect(lambda: _cleanup())
        worker.bad_rar_file.connect(lambda: _cleanup())
        worker.finished.connect(on_finished)
        worker.cancelled.connect(on_cancelled)
        worker.error.connect(on_error)
        worker.bad_zip_file.connect(on_bad_zip)
        worker.bad_rar_file.connect(on_bad_rar)
        worker.start()

    def on_cancelled():
        _remove_label()
        canvas.render_mosaic()

    def on_finished(new_entries):
        _remove_label()
        if new_entries:
            state.images_data.extend(new_entries)
            state.images_data.sort(key=lambda e: _natural_sort_key(e["orig_name"]))
            state.all_entries = list(state.images_data)
            state.modified = True
            state.selected_indices.clear()
        canvas.render_mosaic()

    def on_error(msg):
        _remove_label()
        canvas.render_mosaic()
        _MsgDialog(win,
            "messages.errors.import_failed.title",
            "messages.errors.import_failed.message",
            {"error": msg},
        ).exec()

    def on_bad_zip():
        """CBZ qui est en fait un RAR — dialogue de correction d'extension."""
        _remove_label()
        dlg = ExtensionCorrectionDialog(win, filepath, "CBR", "CBZ")
        if dlg.exec() != QDialog.Accepted:
            return
        choice = dlg.result_choice
        actual = filepath
        if choice == 'rename':
            new_path = os.path.splitext(filepath)[0] + '.cbr'
            try:
                os.rename(filepath, new_path)
                actual = new_path
            except Exception as e:
                _MsgDialog(win,
                    "messages.errors.rename_failed.title",
                    "messages.errors.rename_failed.message",
                    {"error": str(e)[:100]},
                ).exec()
                return
        elif choice != 'keep':
            return
        _start_worker(actual)

    def on_bad_rar():
        """CBR qui est en fait un ZIP — dialogue de correction d'extension."""
        _remove_label()
        dlg = ExtensionCorrectionDialog(win, filepath, "CBZ", "CBR")
        if dlg.exec() != QDialog.Accepted:
            return
        choice = dlg.result_choice
        actual = filepath
        if choice == 'rename':
            new_path = os.path.splitext(filepath)[0] + '.cbz'
            try:
                os.rename(filepath, new_path)
                actual = new_path
            except Exception as e:
                _MsgDialog(win,
                    "messages.errors.rename_failed.title",
                    "messages.errors.rename_failed.message",
                    {"error": str(e)[:100]},
                ).exec()
                return
        elif choice != 'keep':
            return
        _start_worker(actual)

    _start_worker(filepath)
