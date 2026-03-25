# temp_files.py — Gestion des fichiers temporaires (version Qt, sans tkinter)

import os
import tempfile
import time
import shutil

from modules.qt.localization import _


def get_mosaicview_temp_dir():
    """Retourne le chemin du répertoire temporaire centralisé de MosaicView."""
    temp_base = tempfile.gettempdir()
    mosaicview_temp = os.path.join(temp_base, "MosaicViewTemp")
    if not os.path.exists(mosaicview_temp):
        os.makedirs(mosaicview_temp, exist_ok=True)
    return mosaicview_temp


def cleanup_all_temp_files(keep_logs=False):
    """Supprime le contenu du répertoire temporaire MosaicViewTemp."""
    try:
        temp_base = tempfile.gettempdir()
        mosaicview_temp = os.path.join(temp_base, "MosaicViewTemp")

        if os.path.exists(mosaicview_temp):
            config_filename = ".mosaicview_config.json"
            clipboard_max_age = 12 * 60 * 60
            current_time = time.time()

            for item in os.listdir(mosaicview_temp):
                if item == config_filename:
                    continue
                if keep_logs and (item.startswith("Log_pdftocbz_") or item.startswith("Log_cbrtocbz_") or item.startswith("Log_imgtocbz_")) and item.endswith(".txt"):
                    continue

                item_path = os.path.join(mosaicview_temp, item)

                if item.startswith("clipboard_") and os.path.isdir(item_path):
                    try:
                        age = current_time - os.path.getmtime(item_path)
                        if age < clipboard_max_age:
                            continue
                    except Exception:
                        pass

                try:
                    if os.path.isfile(item_path) or os.path.islink(item_path):
                        os.unlink(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                except Exception:
                    pass
    except Exception:
        pass


