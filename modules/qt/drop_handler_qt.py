"""
drop_handler_qt.py — Gestion du drop externe (fichiers / dossiers).
"""

import os

from modules.qt.localization import _, _wt
from modules.qt.dialogs_qt import ErrorDialog
from modules.qt.utils import walk_without_links


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
            "dialogs.batch_drop.mixed_drop_title",
            lambda: _("dialogs.batch_drop.mixed_drop_message")).show_nonmodal()
        return

    if dirs:
        show_batch_choice(parent, dirs, batch_callbacks)
        return

    if files:
        load_files_callback(files, from_drop=from_drop)


# ─────────────────────────────────────────────────────────────────────────────

def show_batch_choice(parent, dirs: list, batch_callbacks: dict, from_menu=False):
    """Fenêtre de choix du traitement par lot des dossiers `dirs`
    (BatchDropDialog), puis le traitement choisi. Appelée au dépôt de
    dossiers et par le menu Fichier > Batch > « Conversions et compression
    des BD » (from_menu, un dossier choisi)."""
    # Normalise les chemins (toLocalFile() produit des / sur Windows → chemins mixtes)
    dirs = [os.path.normpath(d) for d in dirs]

    from modules.qt.batch_dialogs_qt import (
        count_ignored_files,
        batch_convert_cbr_to_cbz_confirm,
        batch_convert_cb7_to_cbz_confirm,
        batch_convert_cbt_to_cbz_confirm,
        batch_convert_pdf_to_cbz_confirm,
        batch_convert_img_to_cbz_confirm,
        batch_convert_imgs_to_single_cbz,
        _ImgModeDialog,
        PDF_AVAILABLE,
    )
    from modules.qt.archive_names import IMAGE_EXTS
    from modules.qt.archive_loader import _natural_sort_key
    from modules.qt.batch_drop_dialog_qt import show_batch_drop_dialog

    def _make_batch_cbr():
        files = []
        for d in dirs:
            for dirpath, _subdirs, filenames in walk_without_links(d):
                for fn in filenames:
                    if fn.lower().endswith('.cbr'):
                        files.append(os.path.join(dirpath, fn))
        files.sort(key=lambda f: _natural_sort_key(os.path.basename(f).lower()))
        if not files:
            ErrorDialog(parent,
                "dialogs.batch_cbr.no_cbr_title",
                lambda d=dirs: _("dialogs.batch_cbr.no_cbr_message").format(directory=", ".join(d))).show_nonmodal()
            return
        batch_convert_cbr_to_cbz_confirm(parent, files, dirs[0], batch_callbacks, directories=dirs,
                                         ignored_count=count_ignored_files(dirs, files))

    def _make_batch_cb7():
        files = []
        for d in dirs:
            for dirpath, _subdirs, filenames in walk_without_links(d):
                for fn in filenames:
                    if fn.lower().endswith('.cb7'):
                        files.append(os.path.join(dirpath, fn))
        files.sort(key=lambda f: _natural_sort_key(os.path.basename(f).lower()))
        if not files:
            ErrorDialog(parent,
                "dialogs.batch_cb7.no_cb7_title",
                lambda d=dirs: _("dialogs.batch_cb7.no_cb7_message").format(directory=", ".join(d))).show_nonmodal()
            return
        batch_convert_cb7_to_cbz_confirm(parent, files, dirs[0], batch_callbacks, directories=dirs,
                                         ignored_count=count_ignored_files(dirs, files))

    def _make_batch_cbt():
        files = []
        for d in dirs:
            for dirpath, _subdirs, filenames in walk_without_links(d):
                for fn in filenames:
                    if fn.lower().endswith('.cbt'):
                        files.append(os.path.join(dirpath, fn))
        files.sort(key=lambda f: _natural_sort_key(os.path.basename(f).lower()))
        if not files:
            ErrorDialog(parent,
                "dialogs.batch_cbt.no_cbt_title",
                lambda d=dirs: _("dialogs.batch_cbt.no_cbt_message").format(directory=", ".join(d))).show_nonmodal()
            return
        batch_convert_cbt_to_cbz_confirm(parent, files, dirs[0], batch_callbacks, directories=dirs,
                                         ignored_count=count_ignored_files(dirs, files))

    def _make_batch_pdf():
        if not PDF_AVAILABLE:
            ErrorDialog(parent,
                "dialogs.batch_pdf.pymupdf_required_title",
                lambda: _("dialogs.batch_pdf.pymupdf_required_message")).show_nonmodal()
            return
        files = []
        for d in dirs:
            for dirpath, _subdirs, filenames in walk_without_links(d):
                for fn in filenames:
                    if fn.lower().endswith('.pdf'):
                        files.append(os.path.join(dirpath, fn))
        files.sort(key=lambda f: _natural_sort_key(os.path.basename(f).lower()))
        if not files:
            ErrorDialog(parent,
                "dialogs.batch_pdf.no_pdf_title",
                lambda d=dirs: _("dialogs.batch_pdf.no_pdf_message").format(directory=", ".join(d))).show_nonmodal()
            return
        batch_convert_pdf_to_cbz_confirm(parent, files, dirs[0], batch_callbacks, directories=dirs,
                                         ignored_count=count_ignored_files(dirs, files))

    def _make_batch_img():
        files = []
        for d in dirs:
            for dirpath, _subdirs, filenames in walk_without_links(d):
                for fn in filenames:
                    if fn.lower().endswith(IMAGE_EXTS):
                        files.append(os.path.join(dirpath, fn))
        files.sort(key=lambda f: _natural_sort_key(os.path.basename(f).lower()))
        if not files:
            ErrorDialog(parent,
                "dialogs.batch_img.no_img_title",
                lambda d=dirs: _("dialogs.batch_img.no_img_message").format(directory=", ".join(d))).show_nonmodal()
            return
        mode_dlg = _ImgModeDialog(parent)

        def _on_mode_done():
            if mode_dlg.chosen_mode is None:
                return
            ignored = count_ignored_files(dirs, files)
            if mode_dlg.chosen_mode == _ImgModeDialog.MODE_ONE_PER_IMAGE:
                batch_convert_img_to_cbz_confirm(parent, files, dirs[0], batch_callbacks, directories=dirs,
                                                 ignored_count=ignored)
            else:
                batch_convert_imgs_to_single_cbz(parent, files, dirs[0], batch_callbacks,
                                                 ignored_count=ignored)

        mode_dlg.finished.connect(lambda _: _on_mode_done())
        mode_dlg.show_nonmodal()

    def _make_batch_metadata():
        files = []
        for d in dirs:
            for dirpath, _subdirs, filenames in walk_without_links(d):
                for fn in filenames:
                    if fn.lower().endswith(('.cbz', '.cbr', '.cb7', '.cbt', '.pdf')):
                        files.append(os.path.join(dirpath, fn))
        from modules.qt.archive_loader import _natural_sort_key
        files.sort(key=lambda f: _natural_sort_key(os.path.basename(f).lower()))
        if not files:
            ErrorDialog(parent,
                "dialogs.batch_metadata.no_files_title",
                lambda d=dirs: _("dialogs.batch_metadata.no_files_message").format(directory=", ".join(d))).show_nonmodal()
            return
        from modules.qt.batch_metadata_dialog_qt import show_batch_metadata_dialog
        show_batch_metadata_dialog(parent, files, dirs, batch_callbacks,
                                   ignored_count=count_ignored_files(dirs, files))

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
        dlg.show_nonmodal()

    def _make_batch_recompress():
        from modules.qt.batch_dialogs_qt import batch_recompress_cbz_confirm, recompress_files
        files = recompress_files(dirs, _natural_sort_key)
        if not files:
            ErrorDialog(parent,
                "dialogs.batch_recompress.no_files_title",
                lambda d=dirs: _("dialogs.batch_recompress.no_files_message").format(directory=", ".join(d))).show_nonmodal()
            return
        batch_recompress_cbz_confirm(parent, files, dirs[0], batch_callbacks, directories=dirs,
                                     ignored_count=count_ignored_files(dirs, files))

    def _make_batch_extensions():
        from modules.qt.batch_dialogs_qt import extension_check_files, run_extension_check
        files = extension_check_files(dirs)
        if not files:
            ErrorDialog(parent,
                "dialogs.batch_extensions.no_files_title",
                lambda d=dirs: _("dialogs.batch_extensions.no_files_message",
                                 directory=", ".join(d))).show_nonmodal()
            return
        run_extension_check(parent, files, dirs[0], batch_callbacks, directories=dirs,
                            ignored_count=count_ignored_files(dirs, files))

    # Traitement du groupe coché seul : lancé seul, avec sa propre fenêtre
    # de confirmation, son résumé et son journal
    single = {
        'cbr':        _make_batch_cbr,
        'cb7':        _make_batch_cb7,
        'cbt':        _make_batch_cbt,
        'pdf':        _make_batch_pdf,
        'img':        _make_batch_img,
        'extensions': _make_batch_extensions,
        'recompress': _make_batch_recompress,
    }

    def _make_batch_all(steps):
        """Traitements cochés du groupe : un seul est lancé seul, plusieurs
        sont enchaînés par la conversion complète."""
        if len(steps) == 1:
            single[steps[0]]()
            return
        from modules.qt.batch_dialogs_qt import convert_all_to_cbz
        convert_all_to_cbz(parent, dirs, batch_callbacks, directories=dirs, steps=steps)

    callbacks = {
        'batch_all':      _make_batch_all,
        'batch_metadata': _make_batch_metadata,
        'batch_library':  _make_batch_library,
    }
    show_batch_drop_dialog(parent, dirs, callbacks, from_menu=from_menu)
