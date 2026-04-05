"""
drop_handler_qt.py — Gestion du drop externe (fichiers / dossiers) pour PySide6.
Reproduit exactement la logique de on_drop / _on_drop_impl / show_batch_drop_dialog
de MosaicView.py (tkinter).
"""

import os

from PySide6.QtWidgets import QMessageBox

from modules.qt.localization import _, _wt


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
        mb = QMessageBox(parent)
        mb.setWindowTitle(_wt("dialogs.batch_drop.mixed_drop_title"))
        mb.setText(_("dialogs.batch_drop.mixed_drop_message"))
        mb.setIcon(QMessageBox.Warning)
        mb.exec()
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
            for dirpath, _, filenames in os.walk(d):
                for fn in filenames:
                    if fn.lower().endswith('.cbr'):
                        files.append(os.path.join(dirpath, fn))
        files.sort(key=lambda f: _natural_sort_key(os.path.basename(f).lower()))
        if not files:
            mb = QMessageBox(parent)
            mb.setWindowTitle(_wt("dialogs.batch_cbr.no_cbr_title"))
            mb.setText(_("dialogs.batch_cbr.no_cbr_message").format(directory=", ".join(dirs)))
            mb.exec()
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
            mb = QMessageBox(parent)
            mb.setWindowTitle(_wt("dialogs.batch_cb7.no_cb7_title"))
            mb.setText(_("dialogs.batch_cb7.no_cb7_message").format(directory=", ".join(dirs)))
            mb.exec()
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
            mb = QMessageBox(parent)
            mb.setWindowTitle(_wt("dialogs.batch_cbt.no_cbt_title"))
            mb.setText(_("dialogs.batch_cbt.no_cbt_message").format(directory=", ".join(dirs)))
            mb.exec()
            return
        batch_convert_cbt_to_cbz_confirm(parent, files, dirs[0], batch_callbacks, directories=dirs)

    def _make_batch_pdf():
        if not PDF_AVAILABLE:
            mb = QMessageBox(parent)
            mb.setWindowTitle(_wt("dialogs.batch_pdf.pymupdf_required_title"))
            mb.setText(_("dialogs.batch_pdf.pymupdf_required_message"))
            mb.setIcon(QMessageBox.Warning)
            mb.exec()
            return
        files = []
        for d in dirs:
            for dirpath, _, filenames in os.walk(d):
                for fn in filenames:
                    if fn.lower().endswith('.pdf'):
                        files.append(os.path.join(dirpath, fn))
        files.sort(key=lambda f: _natural_sort_key(os.path.basename(f).lower()))
        if not files:
            mb = QMessageBox(parent)
            mb.setWindowTitle(_wt("dialogs.batch_pdf.no_pdf_title"))
            mb.setText(_("dialogs.batch_pdf.no_pdf_message").format(directory=", ".join(dirs)))
            mb.exec()
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
            mb = QMessageBox(parent)
            mb.setWindowTitle(_wt("dialogs.batch_img.no_img_title"))
            mb.setText(_("dialogs.batch_img.no_img_message").format(directory=", ".join(dirs)))
            mb.exec()
            return
        mode_dlg = _ImgModeDialog(parent)
        mode_dlg.exec()
        if mode_dlg.chosen_mode is None:
            return
        if mode_dlg.chosen_mode == _ImgModeDialog.MODE_ONE_PER_IMAGE:
            batch_convert_img_to_cbz_confirm(parent, files, dirs[0], batch_callbacks, directories=dirs)
        else:
            batch_convert_imgs_to_single_cbz(parent, files, dirs[0], batch_callbacks)

    callbacks = {
        'batch_cbr': _make_batch_cbr,
        'batch_cb7': _make_batch_cb7,
        'batch_cbt': _make_batch_cbt,
        'batch_pdf': _make_batch_pdf,
        'batch_img': _make_batch_img,
    }
    show_batch_drop_dialog(parent, dirs, callbacks)
