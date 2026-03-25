# recent_files.py — Gestion des fichiers récents

from modules.qt.config_manager import get_config_manager

MAX_RECENT_FILES = 10


def get_recent_files():
    """Retourne la liste des fichiers récents depuis la configuration (source unique)."""
    return get_config_manager().get_recent_files()


def add_to_recent_files(filepath):
    """Ajoute un fichier à la liste des fichiers récents."""
    try:
        get_config_manager().add_recent_file(filepath, max_files=MAX_RECENT_FILES)
    except Exception as e:
        print(f"Erreur lors de l'ajout du fichier récent : {e}")


def remove_from_recent_files(filepath):
    """Supprime un fichier de la liste des fichiers récents."""
    try:
        cfg = get_config_manager()
        recent = cfg.get_recent_files().copy()
        if filepath in recent:
            recent.remove(filepath)
            cfg.set_recent_files(recent)
    except Exception as e:
        print(f"Erreur lors de la suppression du fichier récent : {e}")


def clear_recent_files():
    """Efface l'historique des fichiers récents."""
    try:
        get_config_manager().set_recent_files([])
    except Exception as e:
        print(f"Erreur lors de l'effacement des fichiers récents : {e}")


def init_recent_files():
    """Nettoie les fichiers récents inexistants au démarrage."""
    try:
        get_config_manager().clean_recent_files()
    except Exception as e:
        print(f"Erreur lors du nettoyage des fichiers récents : {e}")
