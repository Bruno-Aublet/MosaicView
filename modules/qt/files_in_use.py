"""Fichiers que l'application lit en ce moment pour un document, hors du
fichier ouvert lui-même : archive ou PDF en cours d'ajout à un comic ouvert
(question de qualité comprise).

La fenêtre de choix de fichier (file_picker_qt._files_in_use) refuse de les
renommer ou de les mettre à la Corbeille : l'ajout échouerait en route. Les
autres fichiers attendus par un panneau (file d'attente, chargement en cours)
sont donnés par le panneau lui-même (PanelWidget._files_in_use).

Chaque lecture est inscrite par hold(chemin), qui rend un jeton, et retirée
par release(jeton) sur tous ses chemins de fin. Sans Qt.
"""
import threading

_lock = threading.Lock()
_held = {}   # jeton -> chemin


def hold(path):
    """Inscrit `path` comme lu ; rend le jeton à passer à release."""
    token = object()
    with _lock:
        _held[token] = path
    return token


def release(token):
    """Retire l'inscription de ce jeton (sans effet si déjà retirée)."""
    with _lock:
        _held.pop(token, None)


def held_paths():
    """Chemins inscrits, dans l'ordre d'inscription."""
    with _lock:
        return list(_held.values())
