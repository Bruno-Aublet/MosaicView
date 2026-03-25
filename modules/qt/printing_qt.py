"""
modules/qt/printing_qt.py — Impression Windows (version PySide6)

Utilise le PhotoPrintingWizard de Windows Shell via IDropTarget COM.
Les images sont extraites du TIFF multi-pages en JPEG dans un dossier temporaire dédié,
puis passées au wizard via SHCreateDataObject + CoCreateInstance.

Fonctions publiques :
  print_selection(parent, canvas, state)  — imprime la sélection courante
  print_all(parent, canvas, state)        — imprime toutes les images valides
"""

import io
import os
import time

from PIL import Image

from modules.qt.localization import _
from modules.qt.canvas_overlay_qt import (
    show_canvas_text as _show_canvas_text,
    hide_canvas_text as _hide_canvas_text,
)
from modules.qt.dialogs_qt import ErrorDialog
from modules.qt.temp_files import get_mosaicview_temp_dir

PRINT_AVAILABLE = True  # Utilise uniquement ctypes + shell32/ole32, toujours disponible


# ─────────────────────────────────────────────────────────────────────────────
# Dialogue d'impression Windows Shell (PhotoPrintingWizard)
# ─────────────────────────────────────────────────────────────────────────────

def _open_print_dialog(tiff_path, parent):
    """Ouvre le dialogue 'Imprimer les images' de Windows (PhotoPrintingWizard)
    via IDropTarget COM avec la liste des JPEG extraits du TIFF multi-pages.
    """
    import ctypes
    import ctypes.wintypes

    try:
        ole32   = ctypes.windll.ole32
        shell32 = ctypes.windll.shell32

        # Extraire les pages du TIFF en JPEG dans un sous-dossier dédié
        job_dir = os.path.join(get_mosaicview_temp_dir(), f"printjob_{int(time.time())}")
        os.makedirs(job_dir, exist_ok=True)

        tiff_img = Image.open(tiff_path)
        n_pages  = getattr(tiff_img, 'n_frames', 1)

        for i in range(n_pages):
            tiff_img.seek(i)
            img = tiff_img.copy().convert('RGB')
            img.save(os.path.join(job_dir, f"{i:04d}.jpg"), 'JPEG', quality=95)

        # PIDL du dossier parent
        parent_pidl = ctypes.c_void_p()
        hr = shell32.SHParseDisplayName(job_dir, None, ctypes.byref(parent_pidl), 0, None)
        if hr != 0:
            raise RuntimeError(f"SHParseDisplayName dossier échoué hr={hr:#x}")

        # IShellFolder du dossier parent
        IID_IShellFolder = (ctypes.c_byte * 16)(
            0xE6, 0x14, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
            0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46)
        folder = ctypes.c_void_p()
        hr = shell32.SHBindToObject(None, parent_pidl, None,
                                     ctypes.byref(IID_IShellFolder),
                                     ctypes.byref(folder))
        if hr != 0:
            raise RuntimeError(f"SHBindToObject échoué hr={hr:#x}")

        # PIDLs relatifs via IShellFolder::ParseDisplayName
        vt_folder = ctypes.cast(folder, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
        ParseDisplayName = ctypes.WINFUNCTYPE(
            ctypes.HRESULT, ctypes.c_void_p, ctypes.wintypes.HWND,
            ctypes.c_void_p, ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_ulong))
        pdn = ParseDisplayName(vt_folder[0][3])

        rel_pidls = []
        for i in range(n_pages):
            rpidl = ctypes.c_void_p()
            eaten = ctypes.c_ulong(0)
            attr  = ctypes.c_ulong(0)
            hr = pdn(folder, 0, None, f"{i:04d}.jpg",
                     ctypes.byref(eaten), ctypes.byref(rpidl), ctypes.byref(attr))
            if hr == 0 and rpidl.value:
                rel_pidls.append(rpidl.value)

        # IDataObject via SHCreateDataObject
        IID_IDataObject = (ctypes.c_byte * 16)(
            0x0E, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46)
        rel_array = (ctypes.c_void_p * len(rel_pidls))(*rel_pidls)
        data_obj  = ctypes.c_void_p()
        hr = shell32.SHCreateDataObject(parent_pidl, len(rel_pidls), rel_array,
                                         None, ctypes.byref(IID_IDataObject),
                                         ctypes.byref(data_obj))
        if hr != 0:
            raise RuntimeError(f"SHCreateDataObject échoué hr={hr:#x}")

        # CoCreateInstance du PhotoPrintingWizard {60fd46de-f830-4894-a628-6fa81bc0190d}
        CLSID_PPW = (ctypes.c_byte * 16)(
            0xDE, 0x46, 0xFD, 0x60, 0x30, 0xF8, 0x94, 0x48,
            0xA6, 0x28, 0x6F, 0xA8, 0x1B, 0xC0, 0x19, 0x0D)
        IID_IDropTarget = (ctypes.c_byte * 16)(
            0x22, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46)
        drop_target = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(CLSID_PPW), None, 1,
                                     ctypes.byref(IID_IDropTarget),
                                     ctypes.byref(drop_target))
        if hr != 0:
            raise RuntimeError(f"CoCreateInstance PPW échoué hr={hr:#x}")

        # DragEnter + Drop via vtable IDropTarget
        # vtable: 0=QI, 1=AddRef, 2=Release, 3=DragEnter, 4=DragOver, 5=DragLeave, 6=Drop
        vt2       = ctypes.cast(drop_target, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
        pt_struct = (ctypes.c_long * 2)(0, 0)
        effect    = ctypes.c_ulong(1)  # DROPEFFECT_COPY

        DragEnterFn = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p,
                                          ctypes.c_ulong, ctypes.c_long * 2,
                                          ctypes.POINTER(ctypes.c_ulong))
        DragEnterFn(vt2[0][3])(drop_target, data_obj, 0, pt_struct, ctypes.byref(effect))

        DropFn = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_ulong, ctypes.c_long * 2,
                                     ctypes.POINTER(ctypes.c_ulong))
        DropFn(vt2[0][6])(drop_target, data_obj, 0, pt_struct, ctypes.byref(effect))

    except Exception as e:
        ErrorDialog(
            parent,
            lambda: _("messages.errors.print_error.title"),
            lambda err=str(e): _("messages.errors.print_error.message", error=err),
        ).exec()


# ─────────────────────────────────────────────────────────────────────────────
# Worker QThread
# ─────────────────────────────────────────────────────────────────────────────

from PySide6.QtCore import QThread, Signal as _Signal


class _PrintWorker(QThread):
    """Prépare le TIFF multi-pages dans un thread séparé."""
    ready     = _Signal(str)  # chemin du TIFF prêt
    no_images = _Signal()     # aucune image valide
    error     = _Signal(str)  # message d'erreur

    def __init__(self, images_to_print):
        super().__init__()
        self._images = images_to_print

    def run(self):
        try:
            mosaicview_temp = get_mosaicview_temp_dir()
            temp_tiff_path  = os.path.join(mosaicview_temp, f"print_{int(time.time())}.tiff")

            images = []
            for entry in self._images:
                image_bytes = entry.get("bytes")
                if not image_bytes:
                    continue
                try:
                    img = Image.open(io.BytesIO(image_bytes))
                    if img.mode in ('RGBA', 'LA', 'P'):
                        background = Image.new('RGB', img.size, (255, 255, 255))
                        if img.mode == 'P':
                            img = img.convert('RGBA')
                        background.paste(
                            img,
                            mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None,
                        )
                        img = background
                    elif img.mode != 'RGB':
                        img = img.convert('RGB')
                    images.append(img)
                except Exception:
                    continue

            if not images:
                self.no_images.emit()
                return

            images[0].save(
                temp_tiff_path,
                save_all=True,
                append_images=images[1:] if len(images) > 1 else [],
                compression="tiff_deflate",
            )
            self.ready.emit(temp_tiff_path)

        except Exception as e:
            self.error.emit(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Impression des images (worker thread + overlay)
# ─────────────────────────────────────────────────────────────────────────────

def _print_images(images_to_print, parent, canvas):
    """Imprime les images originales via Windows.

    Args:
        images_to_print : liste d'entrées `images_data` à imprimer
        parent          : QWidget parent (pour les dialogues)
        canvas          : QGraphicsView (pour l'overlay de progression)
    """
    # ── Overlay de progression ───────────────────────────────────────────────
    _item_holder = []

    def _show_overlay():
        _show_canvas_text(canvas, _("labels.print_preparing"), _item_holder)

    def _hide_overlay():
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(_lang_handler)
        except RuntimeError:
            pass
        _hide_canvas_text(canvas, _item_holder)

    _lang_handler = lambda _: _show_overlay()
    from modules.qt.language_signal import language_signal
    language_signal.changed.connect(_lang_handler)

    _show_overlay()

    # ── Worker ───────────────────────────────────────────────────────────────
    worker = _PrintWorker(images_to_print)

    def _on_ready(tiff_path):
        _hide_overlay()
        worker.deleteLater()
        _open_print_dialog(tiff_path, parent)

    def _on_no_images():
        _hide_overlay()
        worker.deleteLater()
        ErrorDialog(
            parent,
            lambda: _("messages.warnings.no_valid_image_print.title"),
            lambda: _("messages.warnings.no_valid_image_print.message"),
        ).exec()

    def _on_error(msg):
        _hide_overlay()
        worker.deleteLater()
        ErrorDialog(
            parent,
            lambda: _("messages.errors.print_error.title"),
            lambda m=msg: _("messages.errors.print_error.message", error=m),
        ).exec()

    worker.ready.connect(_on_ready)
    worker.no_images.connect(_on_no_images)
    worker.error.connect(_on_error)
    worker.start()


# ─────────────────────────────────────────────────────────────────────────────
# API publique
# ─────────────────────────────────────────────────────────────────────────────

def print_selection(parent, canvas, state):
    """Imprime la sélection en utilisant le dialogue d'impression Windows."""
    if not PRINT_AVAILABLE:
        ErrorDialog(
            parent,
            lambda: _("messages.errors.print_not_available.title"),
            lambda: _("messages.errors.print_not_available.message"),
        ).exec()
        return

    if not state.selected_indices:
        ErrorDialog(
            parent,
            lambda: _("messages.warnings.no_selection_print.title"),
            lambda: _("messages.warnings.no_selection_print.message"),
        ).exec()
        return

    images = [
        state.images_data[i]
        for i in sorted(state.selected_indices)
        if i < len(state.images_data)
        and state.images_data[i].get("is_image")
        and not state.images_data[i].get("is_corrupted")
    ]

    if not images:
        ErrorDialog(
            parent,
            lambda: _("messages.warnings.no_valid_selection_print.title"),
            lambda: _("messages.warnings.no_valid_selection_print.message"),
        ).exec()
        return

    _print_images(images, parent, canvas)


def print_all(parent, canvas, state):
    """Imprime toute la BD en utilisant le dialogue d'impression Windows."""
    if not PRINT_AVAILABLE:
        ErrorDialog(
            parent,
            lambda: _("messages.errors.print_not_available.title"),
            lambda: _("messages.errors.print_not_available.message"),
        ).exec()
        return

    if not state.images_data:
        ErrorDialog(
            parent,
            lambda: _("messages.warnings.no_image_print.title"),
            lambda: _("messages.warnings.no_image_print.message"),
        ).exec()
        return

    images = [
        e for e in state.images_data
        if e.get("is_image") and not e.get("is_corrupted")
    ]

    if not images:
        ErrorDialog(
            parent,
            lambda: _("messages.warnings.no_valid_image_print.title"),
            lambda: _("messages.warnings.no_valid_image_print.message"),
        ).exec()
        return

    _print_images(images, parent, canvas)
