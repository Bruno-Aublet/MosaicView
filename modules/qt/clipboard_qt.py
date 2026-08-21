"""
clipboard_qt.py — Copier/Couper/Coller depuis le presse-papiers système Windows.
Version Qt de modules/clipboard.py + modules/clipboard_ops.py.
"""

import os
import io
import struct
import time

from modules.qt import state as _state_module
from modules.qt.localization import _, _wt
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
            lambda: _wt("messages.info.pywin32_required.title"),
            lambda: _("messages.info.pywin32_required.message"),
        ).show()
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
            lambda: _wt("messages.info.archive_copied.title"),
            lambda: _("messages.info.archive_copied.message"),
        ).show()

    except Exception as e:
        from modules.qt.dialogs_qt import ErrorDialog
        ErrorDialog(
            parent,
            lambda: _wt("messages.errors.copy_archive_failed.title"),
            lambda err=e: _("messages.errors.copy_archive_failed.message", error=err),
            play_sound=True,
        ).show()


# ─────────────────────────────────────────────────────────────────────────────
# Copie / Coupe vers le presse-papiers système
# ─────────────────────────────────────────────────────────────────────────────

def copy_to_system_clipboard(get_temp_dir_func, parent=None):
    """Copie les fichiers sélectionnés vers le presse-papiers Windows (CF_HDROP).

    parent : widget source (panneau) pour centrer l'avertissement éventuel.
    """
    state = _state_module.state
    if not state.selected_indices:
        return
    _copy_entries_to_system_clipboard(
        [state.images_data[i] for i in sorted(state.selected_indices)
         if i < len(state.images_data)],
        get_temp_dir_func, parent)


def copy_single_entry_to_system_clipboard(entry: dict, get_temp_dir_func, parent=None):
    """Copie UNE SEULE entrée (pas une sélection de la mosaïque) vers le
    presse-papiers Windows (CF_HDROP) — utilisée par Ctrl+C dans la
    visionneuse principale (usage type : "je copie une page affichée, je
    vais plus loin, je colle la page dans une autre") : la page couramment
    affichée dans la visionneuse n'est pas forcément dans
    state.selected_indices (l'utilisateur peut naviguer dans la visionneuse
    sans toucher à la sélection de la mosaïque), donc copy_to_system_clipboard
    (dépendante de cette sélection) ne convient pas ici. Même mécanisme
    d'écriture/pose CF_HDROP, factorisé dans _copy_entries_to_system_clipboard
    plutôt que dupliqué."""
    _copy_entries_to_system_clipboard([entry], get_temp_dir_func, parent)


def _copy_entries_to_system_clipboard(entries: list, get_temp_dir_func, parent=None):
    """Cœur commun de copy_to_system_clipboard/copy_single_entry_to_system_clipboard
    — écrit chaque entrée sur disque dans un dossier temporaire dédié puis pose
    un CF_HDROP pointant vers ces fichiers (voir docstring de module : le
    système doit voir de vrais fichiers pour qu'un Ctrl+V dans l'Explorateur/
    une autre instance MosaicView fonctionne)."""
    if not entries:
        return

    try:
        import win32clipboard
        import win32con
    except ImportError:
        return

    try:
        from modules.qt.utils import safe_join
        mosaicview_temp = get_temp_dir_func()
        temp_dir = os.path.join(
            mosaicview_temp,
            f"clipboard_{id(entries)}_{int(time.time())}"
        )
        os.makedirs(temp_dir, exist_ok=True)
        file_list = []
        skipped = 0

        for entry in entries:
            if entry["bytes"] is None or entry.get("is_dir"):
                continue

            # Sécurité : empêche toute écriture hors du dossier temp
            # (nom d'entrée piégé "../" — Zip Slip). Entrée ignorée si évasion.
            temp_path = safe_join(temp_dir, entry["orig_name"])
            if temp_path is None:
                skipped += 1
                continue
            temp_dir_path = os.path.dirname(temp_path)
            if temp_dir_path and temp_dir_path != temp_dir:
                os.makedirs(temp_dir_path, exist_ok=True)

            with open(temp_path, 'wb') as f:
                f.write(entry["bytes"])
            file_list.append(temp_path)

        if not file_list:
            if skipped > 0:
                _warn_unsafe_paths_skipped(parent, skipped)
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

        if skipped > 0:
            _warn_unsafe_paths_skipped(parent, skipped)

    except Exception:
        pass


def _warn_unsafe_paths_skipped(parent, skipped):
    """Avertit (fenêtre non-modale) que des entrées ont été ignorées car
    leur nom tentait d'écrire hors du dossier cible (chemin non valide)."""
    from modules.qt.dialogs_qt import InfoDialog
    dlg = InfoDialog(
        parent,
        lambda: _wt("messages.warnings.unsafe_path_skipped.title"),
        lambda n=skipped: _("messages.warnings.unsafe_path_skipped.message", count=n),
    )
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


def cut_selected(get_temp_dir_func, render_mosaic, save_state, parent=None):
    """Coupe les images sélectionnées : copie dans presse-papiers puis supprime."""
    state = _state_module.state
    if not state.selected_indices:
        return

    copy_to_system_clipboard(get_temp_dir_func, parent=parent)

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

# Extensions reconnues comme "image" pour un CF_HDROP à un seul fichier —
# partagée entre paste_from_system_clipboard() et clipboard_has_single_image()
# (outil "Coller une image" de la visionneuse, voir paste_image_tool_qt.py).
IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
              '.tiff', '.tif', '.ico', '.jfif', '.pjpeg', '.pjp', '.avif')


def clipboard_has_single_image() -> bool:
    """True si le presse-papiers contient EXCLUSIVEMENT une image — soit un
    bitmap (CF_DIB, ex. capture d'écran ou page copiée depuis l'appli
    elle-même), soit un CF_HDROP pointant vers UN SEUL fichier dont
    l'extension est reconnue comme image (ex. copié depuis l'Explorateur).
    Si le presse-papiers contient une image ET autre chose (plusieurs
    fichiers, un seul fichier non-image, une image accompagnée de texte),
    retourne False — pas de tentative de n'utiliser que la partie image
    (décision explicite utilisateur). Lecture SEULE (n'ouvre le
    presse-papiers que pour tester les formats disponibles, ne consomme/copie
    rien) — pensée pour être appelée en boucle depuis un handler
    QClipboard.dataChanged, contrairement à paste_from_system_clipboard qui
    effectue l'extraction complète.

    Ne réécrit pas la logique de détection : mêmes tests que la branche
    CF_HDROP/CF_DIB de paste_from_system_clipboard ci-dessous, simplement
    sans l'étape d'extraction/chargement PIL."""
    try:
        import win32clipboard
        import win32con
    except ImportError:
        return False

    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                files = win32clipboard.GetClipboardData(win32con.CF_HDROP)
                if not files or len(files) != 1:
                    return False
                ext = os.path.splitext(files[0])[1].lower()
                return ext in IMAGE_EXTS
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_DIB):
                return True
            return False
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return False


def get_clipboard_single_image():
    """Retourne l'image PIL du presse-papiers si clipboard_has_single_image()
    serait vrai (bitmap CF_DIB ou fichier image unique CF_HDROP), sinon None.
    Ne modifie ni ne consomme le presse-papiers. Utilisée par l'outil "Coller
    une image" de la visionneuse (paste_image_tool_qt.py) pour récupérer
    l'image réelle à poser sur la page, une fois l'icône activée."""
    if not clipboard_has_single_image():
        return None
    try:
        from PIL import ImageGrab
        img = ImageGrab.grabclipboard()
        if img is not None:
            return img.convert("RGBA")
    except Exception:
        pass

    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                files = win32clipboard.GetClipboardData(win32con.CF_HDROP)
            else:
                files = None
        finally:
            win32clipboard.CloseClipboard()
        if files and len(files) == 1:
            from PIL import Image
            return Image.open(files[0]).convert("RGBA")
    except Exception:
        pass
    return None


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
                  "messages.info.pywin32_required.message").show_nonmodal()
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
                          "messages.warnings.pil_not_available.message").show_nonmodal()
        else:
            win32clipboard.CloseClipboard()

    except Exception as e:
        MsgDialog(parent,
                  "messages.errors.paste_failed.title",
                  "messages.errors.paste_failed.message",
                  {"error": str(e)}).show_nonmodal()
