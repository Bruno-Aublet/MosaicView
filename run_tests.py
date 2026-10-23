"""Lance toute la suite de tests. Ouvrir ce fichier dans VSCode et cliquer sur ▶️ Run.

Lancée ainsi, la suite échoue si un test a été sauté (outil ou bibliothèque
absent : WinRAR, 7-Zip, PyMuPDF, tifffile), et liste les tests sautés avec
leur raison (-ra) : avant une publication, toute la suite doit avoir tourné.
`python -m pytest tests` garde le comportement habituel (sauts permis)."""
import os
import sys

import pytest

if __name__ == "__main__":
    os.environ["MOSAICVIEW_TESTS_NO_SKIP"] = "1"   # lu par tests/conftest.py
    sys.exit(pytest.main(["tests", "-v", "--tb=short", "-ra"]))

# Ce que ces tests vérifient, en résumé :
#
# Archives et pages
# - La reconnaissance du type d'une archive (CBZ, CBR, CB7, CBT...)
# - La lecture des fichiers d'une archive : homonymes, taille maximale, page illisible signalée
# - L'extraction des pages d'une archive CB7 avec 7-Zip
# - Les noms et l'ordre des pages (extension d'après le vrai format, ordre de lecture d'un EPUB)
# - Le tri des images de la mosaïque et des fichiers qui ne sont pas des images
# - Les pages chargées installées sans effacer celles ajoutées pendant le chargement
# - L'ouverture d'un TIFF de plusieurs pages, avec signalement des pages illisibles
# - La page trop grande (au-delà de la limite de pixels), avec ses dimensions
# - La raison d'une page illisible, traduite dans la langue de l'utilisateur
# - La lecture et l'écriture des métadonnées ComicInfo.xml (y compris depuis le fichier, comme à l'ouverture)
# - Un seul ComicInfo.xml par BD : celui de la racine, les autres renommés ComicInfo_0002.xml... (arrivée, aplatissement des dossiers)
#
# Images
# - Les opérations sur les images (rotation, miroir...) et la conservation de leur format
# - Ce qu'une page garde quand on la modifie (orientation, couleurs, qualité, transparence, 16 bits)
# - Les images à plusieurs frames (animations, TIFF de plusieurs pages) et les modes d'image peu courants
# - La modification d'un GIF animé : images lues jusqu'à une image illisible, arrêt signalé
# - Les causes d'échec d'une conversion de format (page trop grande, image illisible...)
# - La détection des pages doubles/multiples dans une image
# - La renumérotation des pages et la sélection des pages
# - Le travail en cours de la visionneuse, rangé page par page
# - Les pages abîmées, vides, minuscules ou au nom très long : jamais d'erreur, page illisible signalée
# - Le redressement automatique et manuel, la découpe d'une page, le redimensionnement, la conversion de format
# - La création d'un GIF animé (délais, transparence, nombre de lectures)
# - Le recadrage (souris et zoom convertis en pixels) et la création d'une icône (carré, pipette, tailles)
#
# PDF
# - Le mode « original » de l'ouverture d'un PDF (image intégrée reprise telle quelle seulement si elle est toute la page)
# - Les accès courts à un PDF dans un process séparé (nombre de pages, couverture, copie déverrouillée)
#
# Annuler/Refaire
# - L'historique, l'identité des pages, le point d'historique après un ajout de pages
# - Le blocage des commandes pendant une opération en cours (chaque commande classée bloquée ou libre)
# - La fin d'une opération : annulation, fermeture du fichier pendant le traitement, résultat arrivé trop tard
# - « Restaurer l'original » des outils Effets, Profondeur de couleur et Mode d'image
# - La visionneuse qui suit sa page ou se ferme quand la page est supprimée
# - Le déplacement de pages d'un panneau à l'autre
#
# Enregistrement et fichiers
# - L'écriture des fichiers sans jamais abîmer l'existant (remplacement atomique, fichier verrouillé)
# - La résolution (DPI) des pages écrites dans un CBZ, sans perte de qualité
# - L'enregistrement réel d'un panneau : pages, ordre, commentaire d'archive, document fermé pendant l'écriture
# - La chaîne de validation avant l'enregistrement, et le document marqué enregistré ou non à la fin
# - La question avant de réécrire un fichier modifié ailleurs (autre panneau, Bibliothèque, autre programme)
# - L'original d'une conversion supprimé seulement après relecture du CBZ produit
# - La détection d'un « Enregistrer sous » vers le fichier déjà ouvert
# - Les noms de pages refusés à l'enregistrement (segment « .. »)
# - La configuration sauvegardée de l'application
# - Le journal des erreurs inattendues (écriture, taille et nombre de fichiers limités, journaux affichés du plus récent au plus ancien)
# - Le calcul de la taille des fichiers affichée à l'utilisateur
# - L'export des fichiers de l'application (polices, son, icônes) : copies échouées signalées
#
# Traitements par lot et Bibliothèque
# - Les traitements par lot : copie des pages, noms de fichiers, CBZ jamais tronqué, import de métadonnées
# - Les lots complets (CBR, CB7, CBT, PDF, images, recompression) : source gardée après un échec, annulation, renommage
# - L'ajout d'une archive à un comic ouvert
# - Les écritures de la Bibliothèque : ComicInfo.xml réécrit, conversion en CBZ et suppression de l'original
# - Un seul traitement par lot à la fois sur les mêmes fichiers
# - Les journaux des traitements par lot et les fichiers temporaires
# - La base de données de la Bibliothèque, sa réindexation et ses actions différées (ComicInfo.xml trop grand non lu)
# - Les macros (lecture, limites des pages)
#
# Sécurité face aux fichiers et pages web hostiles
# - Les noms venus de l'extérieur (archives, métadonnées, dossiers) : noms Windows valides, jamais hors du dossier
# - Seuls certains types de fichiers ouverts avec l'application par défaut
# - Seuls les formats d'image gérés reconnus par Pillow (jamais un EPS déguisé)
# - Le nettoyage du HTML des descriptions ComicVine
# - Les requêtes ComicVine : erreurs réseau, nouvelles tentatives, clé API jamais affichée
# - Les limites de l'import web (taille, durée, nombre d'images, boucles JS)
#
# Garde-fous sur le code de l'application
# - L'isolation des deux panneaux en double affichage
# - Le respect de la règle "aucune fenêtre modale" dans toute l'application
# - L'existence de chaque nom importé d'un module de l'application (un import cassé rendrait une commande muette)
# - L'absence de process PDF partagé entre panneaux/traitements (un process dédié par chargement)
# - PyMuPDF jamais utilisé dans le process de l'application (un PDF malformé la fermerait)
# - L'absence d'opération différée qui lirait le panneau actif au lieu du sien
# - L'absence d'erreur avalée en silence dans une boucle (page perdue sans avertissement)
# - Toute erreur ignorée volontairement justifiée par un commentaire (« sans conséquence » ou « repli »)
# - Aucun worker qui modifie l'état du document depuis son thread
# - Chaque opération arrêtée à la fermeture du fichier, chaque worker arrêtable à la fermeture de l'application
# - Des garde-fous qui prouvent qu'ils détectent encore un cas fautif
# - Une seule façon de mettre un fichier à la Corbeille, des écritures qui ne tronquent jamais un fichier
# - Les textes venus d'un fichier externe toujours affichés en texte brut
