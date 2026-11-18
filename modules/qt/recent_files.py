# recent_files.py — Gestion des fichiers récents

import os

from modules.qt.config_manager import get_config_manager

MAX_RECENT_FILES = 10


def get_recent_files():
    """Retourne la liste des fichiers récents depuis la configuration (source unique)."""
    return get_config_manager().get_recent_files()


def add_to_recent_files(filepath):
    try:
        get_config_manager().add_recent_file(filepath, max_files=MAX_RECENT_FILES)
    except Exception:
        pass  # sans conséquence : seule la liste des fichiers récents n'est pas mise à jour


def remove_from_recent_files(filepath):
    try:
        cfg = get_config_manager()
        recent = cfg.get_recent_files().copy()
        if filepath in recent:
            recent.remove(filepath)
            cfg.set_recent_files(recent)
    except Exception:
        pass  # sans conséquence : seule la liste des fichiers récents n'est pas mise à jour


def rename_in_recent_files(old_path, new_path):
    """Fichier renommé hors d'un enregistrement (lot « Vérifier les
    extensions ») : son nouveau nom prend sa place dans la liste, au même
    rang."""
    try:
        cfg = get_config_manager()
        recent = cfg.get_recent_files().copy()
        old = os.path.normcase(os.path.abspath(old_path))
        changed = False
        for i, path in enumerate(recent):
            if os.path.normcase(os.path.abspath(path)) == old:
                recent[i] = os.path.abspath(new_path)
                changed = True
        if changed:
            cfg.set_recent_files(recent)
    except Exception:
        pass  # sans conséquence : seule la liste des fichiers récents n'est pas mise à jour


def clear_recent_files():
    try:
        get_config_manager().set_recent_files([])
    except Exception:
        pass  # sans conséquence : seule la liste des fichiers récents n'est pas mise à jour


def init_recent_files():
    """Nettoie les fichiers récents inexistants, appelé au démarrage."""
    try:
        get_config_manager().clean_recent_files()
    except Exception:
        pass  # sans conséquence : seule la liste des fichiers récents n'est pas mise à jour
