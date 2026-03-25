"""
clipboard_qt.py — Copier/Couper/Coller depuis le presse-papiers système Windows.
Version Qt de modules/clipboard.py + modules/clipboard_ops.py.
"""

import os
import io
import struct
import time

from modules.qt import state as _state_module
from modules.qt.localization import _
from modules.qt.dialogs_qt import MsgDialog


# ─────────────────────────────────────────────────────────────────────────────
# Copie de l'archive courante vers le presse-papiers système
# ─────────────────────────────────────────────────────────────────────────────

def copy_archive_to_clipboard(parent):
    """Copie l'archive CBZ/CBR complète vers le presse-papiers Windows (CF_HDROP)."""
    state = _state_module.state
    if not state.current_file:
        return

    try:
        import win32clipboard
        import win32con
    except ImportError:
        from modules.qt.dialogs_qt import InfoDialog
        InfoDialog(
            parent,
            lambda: _("messages.info.pywin32_required.title"),
            lambda: _("messages.info.pywin32_required.message"),
        ).exec()
        return

    try:
        archive_path = os.path.normpath(state.current_file)
        offset = 20
        files_data = archive_path + '\0\0'
        files_bytes = files_data.encode('utf-16-le')
        dropfiles = struct.pack('IiiII', offset, 0, 0, 0, 1)
        data = dropfiles + files_bytes

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
        finally:
            win32clipboard.CloseClipboard()

        from modules.qt.dialogs_qt import InfoDialog
        InfoDialog(
            parent,
            lambda: _("messages.info.archive_copied.title"),
            lambda: _("messages.info.archive_copied.message"),
        ).exec()

    except Exception as e:
        import traceback
        traceback.print_exc()
        from modules.qt.dialogs_qt import ErrorDialog
        ErrorDialog(
            parent,
            lambda: _("messages.errors.copy_archive_failed.title"),
            lambda err=e: _("messages.errors.copy_archive_failed.message", error=err),
        ).exec()


# ─────────────────────────────────────────────────────────────────────────────
# Copie / Coupe vers le presse-papiers système
# ─────────────────────────────────────────────────────────────────────────────

def copy_to_system_clipboard(get_temp_dir_func):
    """Copie les fichiers sélectionnés vers le presse-papiers Windows (CF_HDROP)."""
    state = _state_module.state
    if not state.selected_indices:
        return

    try:
        import win32clipboard
        import win32con
    except ImportError as e:
        print(f"pywin32 non disponible : {e}")
        return

    try:
        mosaicview_temp = get_temp_dir_func()
        temp_dir = os.path.join(
            mosaicview_temp,
            f"clipboard_{id(state.selected_indices)}_{int(time.time())}"
        )
        os.makedirs(temp_dir, exist_ok=True)
        file_list = []

        for idx in sorted(state.selected_indices):
            if idx >= len(state.images_data):
                continue
            entry = state.images_data[idx]
            if entry["bytes"] is None or entry.get("is_dir"):
                continue

            temp_path = os.path.join(temp_dir, entry["orig_name"])
            temp_dir_path = os.path.dirname(temp_path)
            if temp_dir_path and temp_dir_path != temp_dir:
                os.makedirs(temp_dir_path, exist_ok=True)

            with open(temp_path, 'wb') as f:
                f.write(entry["bytes"])
            file_list.append(temp_path)

        if not file_list:
            return

        offset = 20
        files_data = '\0'.join(file_list) + '\0\0'
        files_bytes = files_data.encode('utf-16-le')
        dropfiles = struct.pack('IiiII', offset, 0, 0, 0, 1)
        data = dropfiles + files_bytes

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
        finally:
            win32clipboard.CloseClipboard()

    except Exception:
        import traceback
        traceback.print_exc()


def cut_selected(get_temp_dir_func, render_mosaic, save_state):
    """Coupe les images sélectionnées : copie dans presse-papiers puis supprime."""
    state = _state_module.state
    if not state.selected_indices:
        return

    copy_to_system_clipboard(get_temp_dir_func)

    save_state()
    for idx in sorted(state.selected_indices, reverse=True):
        if idx < len(state.images_data):
            state.images_data.pop(idx)
    state.selected_indices.clear()
    state.modified = True
    from modules.qt.comic_info import sync_pages_in_xml_data
    sync_pages_in_xml_data(state)
    render_mosaic()


# ─────────────────────────────────────────────────────────────────────────────
# Collage depuis le presse-papiers système
# ─────────────────────────────────────────────────────────────────────────────

def paste_from_system_clipboard(parent, load_files_callback, save_state, render_mosaic,
                                clear_selection, natural_sort_key):
    """Colle des fichiers ou une image bitmap depuis le presse-papiers Windows."""
    state = _state_module.state

    try:
        import win32clipboard
        import win32con
    except ImportError:
        MsgDialog(parent,
                  "messages.info.pywin32_required.title",
                  "messages.info.pywin32_required.message").exec()
        return

    try:
        win32clipboard.OpenClipboard()

        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
            files = win32clipboard.GetClipboardData(win32con.CF_HDROP)
            win32clipboard.CloseClipboard()
            if files:
                load_files_callback(list(files))

        elif win32clipboard.IsClipboardFormatAvailable(win32con.CF_DIB):
            win32clipboard.CloseClipboard()
            try:
                from PIL import ImageGrab
                from modules.qt.entries import create_entry

                IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
                              '.tiff', '.tif', '.ico', '.jfif', '.pjpeg', '.pjp')

                img = ImageGrab.grabclipboard()
                if img:
                    img_bytes_io = io.BytesIO()
                    img.save(img_bytes_io, format='PNG')
                    img_bytes = img_bytes_io.getvalue()

                    counter = 1
                    while any(e["orig_name"] == f"pasted_{counter}.png" for e in state.images_data):
                        counter += 1
                    filename = f"pasted_{counter}.png"

                    entry = create_entry(filename, img_bytes, IMAGE_EXTS)
                    if entry:
                        entry["source_archive"] = "loose"
                        save_state()
                        state.images_data.append(entry)
                        state.images_data.sort(key=lambda e: natural_sort_key(e["orig_name"]))
                        state.modified = True
                        from modules.qt.comic_info import sync_pages_in_xml_data
                        sync_pages_in_xml_data(state)
                        clear_selection()
                        render_mosaic()
            except ImportError:
                MsgDialog(parent,
                          "messages.warnings.pil_not_available.title",
                          "messages.warnings.pil_not_available.message").exec()
        else:
            win32clipboard.CloseClipboard()

    except Exception as e:
        import traceback
        traceback.print_exc()
        MsgDialog(parent,
                  "messages.errors.paste_failed.title",
                  "messages.errors.paste_failed.message",
                  {"error": str(e)}).exec()
