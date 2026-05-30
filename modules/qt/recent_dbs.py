# recent_dbs.py — Gestion des bases de données récentes

from modules.qt.config_manager import get_config_manager

MAX_RECENT_DBS = 10


def get_recent_dbs():
    return get_config_manager().get_recent_dbs()


def add_to_recent_dbs(filepath):
    try:
        get_config_manager().add_recent_db(filepath, max_files=MAX_RECENT_DBS)
    except Exception as e:
        print(f"Erreur lors de l'ajout de la DB recente : {e}")


def remove_from_recent_dbs(filepath):
    try:
        cfg = get_config_manager()
        recent = cfg.get_recent_dbs().copy()
        if filepath in recent:
            recent.remove(filepath)
            cfg.set_recent_dbs(recent)
    except Exception as e:
        print(f"Erreur lors de la suppression de la DB recente : {e}")


def clear_recent_dbs():
    try:
        get_config_manager().set_recent_dbs([])
    except Exception as e:
        print(f"Erreur lors de l'effacement des DB recentes : {e}")


def init_recent_dbs():
    try:
        get_config_manager().clean_recent_dbs()
    except Exception as e:
        print(f"Erreur lors du nettoyage des DB recentes : {e}")
