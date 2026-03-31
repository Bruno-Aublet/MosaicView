# temp_files.py — Gestion des fichiers temporaires (version Qt, sans tkinter)

import os
import sys
import tempfile
import time
import shutil

from modules.qt.localization import _


def cleanup_stale_mei_dirs():
    """
    Supprime les dossiers _MEI* orphelins laissés par PyInstaller (mode --onefile)
    après un plantage. Le dossier de l'instance courante (sys._MEIPASS) est exclu.
    Les dossiers encore verrouillés par une autre instance active sont ignorés.
    Sans effet hors PyInstaller.
    """
    current_meipass = getattr(sys, "_MEIPASS", None)
    if current_meipass is None:
        return  # pas en mode PyInstaller onefile

    temp_base = tempfile.gettempdir()
    try:
        entries = os.listdir(temp_base)
    except Exception:
        return

    for name in entries:
        if not name.startswith("_MEI"):
            continue
        path = os.path.join(temp_base, name)
        if not os.path.isdir(path):
            continue
        if os.path.normcase(os.path.realpath(path)) == os.path.normcase(os.path.realpath(current_meipass)):
            continue  # dossier de l'instance courante
        # Teste si le dossier est verrouillé par une autre instance active
        try:
            os.rename(path, path)
        except (PermissionError, OSError):
            continue  # verrouillé → autre instance active, on laisse
        try:
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass


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


