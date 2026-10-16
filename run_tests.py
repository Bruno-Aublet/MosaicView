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
# - Les opérations sur les images (rotation, miroir...) et la conservation de leur format
# - Les images à plusieurs frames (animations, TIFF de plusieurs pages) et les modes d'image peu courants
# - L'ouverture d'un TIFF de plusieurs pages, avec signalement des pages illisibles
# - Les causes d'échec d'une conversion de format (page trop grande, image illisible...)
# - Annuler/Refaire : historique, identité des pages, blocage pendant une opération en cours
# - Les macros (lecture, limites des pages)
# - L'isolation des deux panneaux en double affichage
# - L'écriture des fichiers sans jamais abîmer l'existant (remplacement atomique, fichier verrouillé)
# - La résolution (DPI) des pages écrites dans un CBZ, sans perte de qualité
# - La détection d'un « Enregistrer sous » vers le fichier déjà ouvert
# - Les traitements par lot : copie des pages, noms de fichiers, CBZ jamais tronqué, import de métadonnées
# - L'extraction des pages d'une archive CB7 avec 7-Zip
# - La base de données de la Bibliothèque et sa réindexation
# - Les journaux des traitements par lot et les fichiers temporaires
# - La configuration sauvegardée de l'application
# - Le calcul de la taille des fichiers affichée à l'utilisateur
# - Le respect de la règle "aucune fenêtre modale" dans toute l'application
# - L'absence de process PDF partagé entre panneaux/traitements (un process dédié par chargement)
# - L'absence d'opération différée qui lirait le panneau actif au lieu du sien
# - L'absence d'erreur avalée en silence dans une boucle (page perdue sans avertissement)
