# recent_files.py — Gestion des fichiers récents

from modules.qt.config_manager import get_config_manager

MAX_RECENT_FILES = 10


def get_recent_files():
    """Retourne la liste des fichiers récents depuis la configuration (source unique)."""
    return get_config_manager().get_recent_files()


def add_to_recent_files(filepath):
    try:
        get_config_manager().add_recent_file(filepath, max_files=MAX_RECENT_FILES)
    except Exception:
        pass


def remove_from_recent_files(filepath):
    try:
        cfg = get_config_manager()
        recent = cfg.get_recent_files().copy()
        if filepath in recent:
            recent.remove(filepath)
            cfg.set_recent_files(recent)
    except Exception:
        pass


def clear_recent_files():
    try:
        get_config_manager().set_recent_files([])
    except Exception:
        pass


def init_recent_files():
    """Nettoie les fichiers récents inexistants, appelé au démarrage."""
    try:
        get_config_manager().clean_recent_files()
    except Exception:
        pass
