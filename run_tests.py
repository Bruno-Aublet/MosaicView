"""Lance toute la suite de tests. Ouvrir ce fichier dans VSCode et cliquer sur ▶️ Run."""
import sys

import pytest

if __name__ == "__main__":
    sys.exit(pytest.main(["tests", "-v", "--tb=short"]))

# Ce que ces tests vérifient, en résumé :
# - La lecture et l'écriture des métadonnées ComicInfo.xml
# - La détection des pages doubles/multiples dans une image
# - La renumérotation des pages
# - La reconnaissance du type d'une archive (CBZ, CBR, CB7, CBT...)
# - Le tri des images de la mosaïque
# - Le tri des fichiers qui ne sont pas des images
# - Les opérations sur les images (rotation, miroir...)
# - La base de données de la Bibliothèque
# - La configuration sauvegardée de l'application
# - Le calcul de la taille des fichiers affichée à l'utilisateur
# - Le respect de la règle "aucune fenêtre modale" dans toute l'application
# - L'absence de process PDF partagé entre panneaux/traitements (un process dédié par chargement)
