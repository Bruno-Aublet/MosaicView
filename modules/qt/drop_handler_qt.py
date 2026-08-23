"""
drop_handler_qt.py — Gestion du drop externe (fichiers / dossiers).
"""

import os

from modules.qt.localization import _, _wt
from modules.qt.dialogs_qt import ErrorDialog


def handle_dropped_paths(parent, paths: list, load_files_callback, batch_callbacks: dict,
                         from_drop: bool = False):
    """
    Point d'entrée unique pour tout drop externe (MainWindow ou canvas).

    parent              : QWidget parent pour les dialogues
    paths               : liste de chemins (fichiers ou dossiers)
    load_files_callback : callable(paths, from_drop) — charge des fichiers
    batch_callbacks     : dict renvoyé par MainWindow._get_batch_callbacks()
    """
    if not paths:
        return

    # Normalise les chemins (toLocalFile() produit des / sur Windows → chemins mixtes)
    paths = [os.path.normpath(p) for p in paths]

    dirs  = [p for p in paths if os.path.isdir(p)]
    files = [p for p in paths if not os.path.isdir(p)]

    if dirs and files:
        ErrorDialog(parent,
            lambda: _wt("dialogs.batch_drop.mixed_drop_title"),
            lambda: _("dialogs.batch_drop.mixed_drop_message")).show_nonmodal()
        return

    if dirs:
        _show_batch_drop_dialog(parent, dirs, batch_callbacks)
        return

    if files:
        load_files_callback(files, from_drop=from_drop)


# ─────────────────────────────────────────────────────────────────────────────

def _show_batch_drop_dialog(parent, dirs: list, batch_callbacks: dict):
    # Normalise les chemins (toLocalFile() produit des / sur Windows → chemins mixtes)
    dirs = [os.path.normpath(d) for d in dirs]

    from modules.qt.batch_dialogs_qt import (
        batch_convert_cbr_to_cbz_confirm,
        batch_convert_cb7_to_cbz_confirm,
        batch_convert_cbt_to_cbz_confirm,
        batch_convert_pdf_to_cbz_confirm,
        batch_convert_img_to_cbz_confirm,
        batch_convert_imgs_to_single_cbz,
        _ImgModeDialog,
        PDF_AVAILABLE,
        image_exts as _image_exts,
    )
    from modules.qt.archive_loader import _natural_sort_key
    from modules.qt.batch_drop_dialog_qt import show_batch_drop_dialog

    def _make_batch_cbr():
        files = []
        for d in dirs:
            for dirpath, _subdirs, filenames in os.walk(d):
                for fn in filenames:
                    if fn.lower().endswith('.cbr'):
                        files.append(os.path.join(dirpath, fn))
        files.sort(key=lambda f: _natural_sort_key(os.path.basename(f).lower()))
        if not files:
            ErrorDialog(parent,
                lambda: _wt("dialogs.batch_cbr.no_cbr_title"),
                lambda d=dirs: _("dialogs.batch_cbr.no_cbr_message").format(directory=", ".join(d))).show_nonmodal()
            return
        batch_convert_cbr_to_cbz_confirm(parent, files, dirs[0], batch_callbacks, directories=dirs)

    def _make_batch_cb7():
        files = []
        for d in dirs:
            for dirpath, _subdirs, filenames in os.walk(d):
                for fn in filenames:
                    if fn.lower().endswith('.cb7'):
                        files.append(os.path.join(dirpath, fn))
        files.sort(key=lambda f: _natural_sort_key(os.path.basename(f).lower()))
        if not files:
            ErrorDialog(parent,
                lambda: _wt("dialogs.batch_cb7.no_cb7_title"),
                lambda d=dirs: _("dialogs.batch_cb7.no_cb7_message").format(directory=", ".join(d))).show_nonmodal()
            return
        batch_convert_cb7_to_cbz_confirm(parent, files, dirs[0], batch_callbacks, directories=dirs)

    def _make_batch_cbt():
        files = []
        for d in dirs:
            for dirpath, _subdirs, filenames in os.walk(d):
                for fn in filenames:
                    if fn.lower().endswith('.cbt'):
                        files.append(os.path.join(dirpath, fn))
        files.sort(key=lambda f: _natural_sort_key(os.path.basename(f).lower()))
        if not files:
            ErrorDialog(parent,
                lambda: _wt("dialogs.batch_cbt.no_cbt_title"),
                lambda d=dirs: _("dialogs.batch_cbt.no_cbt_message").format(directory=", ".join(d))).show_nonmodal()
            return
        batch_convert_cbt_to_cbz_confirm(parent, files, dirs[0], batch_callbacks, directories=dirs)

    def _make_batch_pdf():
        if not PDF_AVAILABLE:
            ErrorDialog(parent,
                lambda: _wt("dialogs.batch_pdf.pymupdf_required_title"),
                lambda: _("dialogs.batch_pdf.pymupdf_required_message")).show_nonmodal()
            return
        files = []
        for d in dirs:
            for dirpath, _subdirs, filenames in os.walk(d):
                for fn in filenames:
                    if fn.lower().endswith('.pdf'):
                        files.append(os.path.join(dirpath, fn))
        files.sort(key=lambda f: _natural_sort_key(os.path.basename(f).lower()))
        if not files:
            ErrorDialog(parent,
                lambda: _wt("dialogs.batch_pdf.no_pdf_title"),
                lambda d=dirs: _("dialogs.batch_pdf.no_pdf_message").format(directory=", ".join(d))).show_nonmodal()
            return
        batch_convert_pdf_to_cbz_confirm(parent, files, dirs[0], batch_callbacks, directories=dirs)

    def _make_batch_img():
        files = []
        for d in dirs:
            for dirpath, _subdirs, filenames in os.walk(d):
                for fn in filenames:
                    if fn.lower().endswith(_image_exts):
                        files.append(os.path.join(dirpath, fn))
        files.sort(key=lambda f: _natural_sort_key(os.path.basename(f).lower()))
        if not files:
            ErrorDialog(parent,
                lambda: _wt("dialogs.batch_img.no_img_title"),
                lambda d=dirs: _("dialogs.batch_img.no_img_message").format(directory=", ".join(d))).show_nonmodal()
            return
        mode_dlg = _ImgModeDialog(parent)

        def _on_mode_done():
            if mode_dlg.chosen_mode is None:
                return
            if mode_dlg.chosen_mode == _ImgModeDialog.MODE_ONE_PER_IMAGE:
                batch_convert_img_to_cbz_confirm(parent, files, dirs[0], batch_callbacks, directories=dirs)
            else:
                batch_convert_imgs_to_single_cbz(parent, files, dirs[0], batch_callbacks)

        mode_dlg.finished.connect(lambda _: _on_mode_done())
        mode_dlg.show()

    def _make_batch_metadata():
        files = []
        for d in dirs:
            for dirpath, _subdirs, filenames in os.walk(d):
                for fn in filenames:
                    if fn.lower().endswith(('.cbz', '.cbr', '.cb7', '.cbt', '.pdf')):
                        files.append(os.path.join(dirpath, fn))
        from modules.qt.archive_loader import _natural_sort_key
        files.sort(key=lambda f: _natural_sort_key(os.path.basename(f).lower()))
        if not files:
            ErrorDialog(parent,
                lambda: _wt("dialogs.batch_metadata.no_files_title"),
                lambda d=dirs: _("dialogs.batch_metadata.no_files_message").format(directory=", ".join(d))).show_nonmodal()
            return
        from modules.qt.batch_metadata_dialog_qt import show_batch_metadata_dialog
        show_batch_metadata_dialog(parent, files, dirs, batch_callbacks)

    def _make_batch_library():
        from modules.qt.archive_loader import _natural_sort_key
        sorted_dirs = sorted(dirs, key=lambda d: _natural_sort_key(os.path.basename(d).lower()))
        master_dir  = sorted_dirs[0]
        extra_dirs  = sorted_dirs[1:]
        from modules.qt.library_window import open_library_window
        lib_win = open_library_window(parent)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: _open_new_db_with_preset(lib_win, master_dir, extra_dirs))

    def _open_new_db_with_preset(lib_win, master_dir, extra_dirs):
        from modules.qt.library_dialogs import NewDbDialog
        dlg = NewDbDialog(parent=lib_win, preset_dir=master_dir)
        dlg.accepted.connect(lambda: lib_win._on_new_db_accepted(dlg, extra_dirs=extra_dirs))
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _make_batch_recompress():
        from modules.qt.batch_dialogs_qt import batch_recompress_cbz_confirm
        files = []
        for d in dirs:
            for dirpath, _subdirs, filenames in os.walk(d):
                for fn in filenames:
                    if fn.lower().endswith(('.cbz', '.cbr', '.cb7', '.cbt')):
                        files.append(os.path.join(dirpath, fn))
        files.sort(key=lambda f: _natural_sort_key(os.path.basename(f).lower()))
        if not files:
            ErrorDialog(parent,
                lambda: _wt("dialogs.batch_recompress.no_files_title"),
                lambda d=dirs: _("dialogs.batch_recompress.no_files_message").format(directory=", ".join(d))).show_nonmodal()
            return
        batch_recompress_cbz_confirm(parent, files, dirs[0], batch_callbacks, directories=dirs)

    callbacks = {
        'batch_cbr':        _make_batch_cbr,
        'batch_cb7':        _make_batch_cb7,
        'batch_cbt':        _make_batch_cbt,
        'batch_pdf':        _make_batch_pdf,
        'batch_img':        _make_batch_img,
        'batch_metadata':   _make_batch_metadata,
        'batch_library':    _make_batch_library,
        'batch_recompress': _make_batch_recompress,
    }
    show_batch_drop_dialog(parent, dirs, callbacks)
