"""
easter_eggs_qt.py — Easter eggs sonores de MosaicView.

Cri de Wilhelm (Wilhelm_Scream.wav, domaine public / CC0, Wikimedia Commons) :
joué uniquement sur des erreurs système rares et graves (échec d'écriture
disque, erreur réseau imprévue, ressource interne manquante...), jamais sur
une simple validation utilisateur (champ vide, annulation, confirmation de
fermeture) — pour rester un clin d'œil occasionnel et non un agacement.
"""

import os
import sys
import winsound


def _resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


_WILHELM_SCREAM_PATH = _resource_path(os.path.join('Sound', 'Wilhelm_Scream.wav'))


def play_wilhelm_scream():
    """Joue le cri de Wilhelm de façon asynchrone (ne bloque pas l'UI).

    Échoue silencieusement si le fichier est absent ou si le son ne peut
    pas être joué (pas de périphérique audio, etc.) : un easter egg ne
    doit jamais faire planter ni perturber l'affichage de l'erreur réelle.
    """
    try:
        winsound.PlaySound(_WILHELM_SCREAM_PATH,
                           winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass
