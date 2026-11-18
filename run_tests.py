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
# - La reconnaissance du type d'une archive (CBZ, CBR, CB7, CBT, EPUB...)
# - Un seul lecteur pour toutes les archives : homonymes lus chacun avec son contenu, taille maximale, page illisible signalée
# - L'extraction des pages d'une archive CB7 avec 7-Zip ; 7-Zip et UnRAR jamais laissés en vie après MosaicView
# - Une image qui annonce une taille impossible refusée avant d'être décodée
# - Les pages altérées (somme de contrôle fausse mais image lisible) : affichées, signalées, confirmation avant de les enregistrer
# - Les dossiers d'une archive, vides ou non, gardés jusqu'à l'aplatissement ; l'aplatissement des dossiers
# - Les noms et l'ordre des pages (extension d'après le vrai format, ordre de lecture d'un EPUB, noms en codage Windows)
# - La première page d'une archive (couverture de la Bibliothèque, de l'import de métadonnées, vignette de la recompression)
# - Le tri des images de la mosaïque et des fichiers qui ne sont pas des images
# - Les pages chargées installées sans effacer celles ajoutées pendant le chargement
# - L'ouverture d'un TIFF de plusieurs pages, avec signalement des pages illisibles
# - La page trop grande (au-delà de la limite de pixels), avec ses dimensions
# - La raison d'une page illisible, traduite dans la langue de l'utilisateur
# - Copier, couper, coller et glisser des pages, y compris une page illisible ; temporaires du glisser supprimés
# - L'état de compression d'un CBZ, l'indicateur ZIP et la question de recompression
#
# Métadonnées
# - La lecture et l'écriture des métadonnées ComicInfo.xml (y compris depuis le fichier, comme à l'ouverture)
# - Un seul ComicInfo.xml par BD : celui de la racine ou du dossier des pages, les autres renommés ComicInfo_0002.xml...
# - Un ComicInfo.xml abîmé lu dans sa version réparée, l'original gardé en annexe
# - La section <Pages> qui suit l'ordre des pages ; le type de page et la double page (pose, annulation, enregistrement)
# - Les métadonnées ComicBookInfo du commentaire d'archive
# - ComicVine : requêtes (erreurs réseau, nouvelles tentatives, clé API jamais affichée), recherche par nom de fichier,
#   tri des tableaux, couverture décodée, worker remplacé mis à l'écart
#
# Images
# - Les opérations sur les images (rotation, miroir...) et la conservation de leur format
# - Une seule façon d'encoder une page ; ce qu'une page garde quand on la modifie (orientation, couleurs, qualité, transparence, 16 bits)
# - Les images à plusieurs frames (animations, TIFF de plusieurs pages) et les modes d'image peu courants
# - La création et la modification d'un GIF animé (délais, transparence, images lues jusqu'à une image illisible)
# - Les causes d'échec d'une conversion de format (page trop grande, image illisible...) et son déroulement complet
# - La détection des pages doubles/multiples dans une image, la fenêtre des doublons
# - La renumérotation des pages et la sélection des pages
# - Les pages abîmées, vides, minuscules ou au nom très long : jamais d'erreur, page illisible signalée
# - Le redressement automatique et manuel, la découpe d'une page, la jointure, le redimensionnement
# - Le recadrage (souris et zoom convertis en pixels) et la création d'une icône (carré, pipette, tailles)
#
# Visionneuse et ses outils
# - Une seule visionneuse par panneau, qui suit son document, sa page, ou se ferme quand la page est supprimée
# - Le travail non validé rangé page par page, et les commandes de la mosaïque tant qu'il en reste
# - Les outils de dessin, Formes, Texte, Transparence, Clonage, Tampon de flou, Coller une image : aperçu fidèle au résultat
# - « Restaurer l'original » des outils Effets, Profondeur de couleur et Mode d'image
# - La rotation, le mode de lecture, le zoom du webtoon, le rythme des images animées, les pages « Supprimée » sautées
# - Les messages d'échec d'un recadrage, d'un redressement ou d'un outil de dessin
#
# PDF
# - Le mode « Pas de modification des images » : image intégrée reprise seulement si elle est toute la page,
#   sinon page rendue à la résolution de ses images, sans qu'un petit logo très fin la rende énorme
# - Le mode « images brutes » : images extraites telles qu'enregistrées, recollage des images coupées entre deux pages
# - Les images abîmées signalées, jamais rendues blanches ; le PDF libéré dès que ses images sont lues
# - La lecture du canal d'un process PDF, la question de qualité avant l'ajout d'un PDF
# - Les accès courts à un PDF dans un process séparé (nombre de pages, couverture, copie déverrouillée)
#
# Annuler/Refaire
# - L'historique, l'identité des pages, une action = un seul Annuler, le point d'historique après un ajout de pages
# - Le blocage des commandes pendant une opération en cours (chaque commande classée bloquée ou libre)
# - La fin d'une opération : annulation, fermeture du fichier pendant le traitement, résultat arrivé trop tard
# - Les résultats périmés ignorés (ouverture d'archive ou de PDF, ajout d'une archive, images déposées,
#   vérification des mises à jour ComicVine, aperçu de la Bibliothèque) : jamais appliqués au mauvais document
# - Le déplacement de pages d'un panneau à l'autre
#
# Enregistrement et fichiers
# - L'écriture des fichiers sans jamais abîmer l'existant (remplacement atomique, place libre, fichier verrouillé)
# - La résolution (DPI) des pages écrites dans un CBZ, sans perte de qualité
# - L'enregistrement réel d'un panneau : pages, ordre, commentaire d'archive, document fermé pendant l'écriture
# - La chaîne de validation avant l'enregistrement, ses corrections dans l'historique, le marqueur « modifié »
# - La question avant de réécrire un fichier modifié ailleurs (autre panneau, Bibliothèque, autre programme)
# - L'original d'une conversion supprimé seulement après relecture du CBZ produit ; la Corbeille annoncée quand
#   Windows supprimerait définitivement
# - La détection d'un « Enregistrer sous » vers le fichier déjà ouvert
# - Les noms de pages refusés à l'enregistrement (segment « .. »)
# - L'export de la sélection vers un dossier, l'export des vignettes, le fichier NFO
# - La fenêtre de choix de fichier : règles, renommer, Corbeille, fichiers en cours d'utilisation refusés,
#   réponse qui vaut pour ce qui était visé au lancement de la commande
# - La configuration sauvegardée de l'application
# - Le journal des erreurs inattendues (écriture, taille limitée, pile des erreurs rattrapées, envoi par mail)
#   et les erreurs signalées à l'utilisateur, jamais avalées
# - Le calcul de la taille des fichiers affichée à l'utilisateur, l'indicateur de mémoire
# - L'export des fichiers de l'application (polices, son, icônes) : copies échouées signalées
#
# Traitements par lot et Bibliothèque
# - Les traitements par lot : copie des pages, noms de fichiers, CBZ jamais tronqué
# - Les lots complets (CBR, CB7, CBT, PDF, images, recompression) : source gardée après un échec, annulation, renommage,
#   source supprimée ou modifiée par un autre programme pendant sa conversion
# - L'import de métadonnées en lot : écriture, annulation, ComicInfo.xml reconnu, jamais deux imports sur les mêmes fichiers
# - L'ajout d'une archive à un comic ouvert
# - Les écritures de la Bibliothèque : ComicInfo.xml réécrit, conversion en CBZ (un ou plusieurs fichiers), suppression de l'original
# - Un seul traitement par lot à la fois sur les mêmes fichiers
# - Les journaux des traitements par lot et les fichiers temporaires
# - La base de données de la Bibliothèque, sa réindexation, ses actions différées, son export Excel
# - Les macros (lecture, limites des pages)
#
# Interface
# - Le code commun des fenêtres, la barre de titre dessinée par Qt, le cadre Windows, les titres de la fenêtre principale
# - La minimap, l'onglet Métadonnées, les raccourcis des menus, les menus du clic droit construits par Qt remplacés
# - Les sous-dossiers de la mosaïque (commandes limitées, clic droit sans effet)
# - Un seul MosaicView par compte Windows reçoit les fichiers ouverts
#
# Sécurité face aux fichiers et pages web hostiles
# - Les noms venus de l'extérieur (archives, métadonnées, dossiers) : noms Windows valides, jamais hors du dossier
# - Des noms et des fichiers abîmés ou piégés passés aux vrais lecteurs (versions courtes du fuzzing, graine fixe)
# - Seuls certains types de fichiers ouverts avec l'application par défaut
# - Seuls les formats d'image gérés reconnus par Pillow (jamais un EPS déguisé)
# - Le nettoyage du HTML des descriptions ComicVine, le texte mis en forme jamais lu tel quel
# - Les limites de l'import web (taille, durée, nombre d'images, boucles JS)
# - Les programmes de Windows et les outils embarqués lancés par leur chemin complet
#
# Garde-fous sur le code de l'application
# - L'isolation des deux panneaux en double affichage
# - Le respect de la règle "aucune fenêtre modale" dans toute l'application ; fenêtres affichées par le code commun,
#   détruites à leur fermeture, avec leur barre de titre dessinée par Qt ; menus détruits après affichage
# - L'existence de chaque nom importé d'un module de l'application (un import cassé rendrait une commande muette)
# - L'absence de process PDF partagé entre panneaux/traitements (un process dédié par chargement)
# - PyMuPDF jamais utilisé dans le process de l'application (un PDF malformé la fermerait)
# - L'absence d'opération différée qui lirait le panneau actif au lieu du sien
# - L'absence d'erreur avalée en silence dans une boucle (page perdue sans avertissement)
# - Toute erreur ignorée volontairement justifiée par un commentaire (« sans conséquence » ou « repli »)
# - Aucun worker qui modifie l'état du document depuis son thread ; chaque opération qui se termine toujours
# - Chaque opération arrêtée à la fermeture du fichier, chaque worker arrêtable à la fermeture de l'application
# - Chaque modification de la liste des pages encadrée par un état d'historique avant et après elle
# - Des garde-fous qui prouvent qu'ils détectent encore un cas fautif
# - Une seule façon de mettre un fichier à la Corbeille, des écritures qui ne tronquent jamais un fichier
# - Les pages réécrites par le seul encodeur commun
# - Les textes venus d'un fichier externe toujours affichés en texte brut
# - Le code extrait pour les tests seulement par l'outil commun ; la suite qui n'écrit jamais dans le vrai %APPDATA%
#   et ne laisse aucun fichier derrière elle
