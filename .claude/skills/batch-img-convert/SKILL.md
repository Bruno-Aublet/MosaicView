---
name: batch-img-convert
description: Localiser ou modifier la conversion par lot d'images isolées en CBZ (un CBZ par image ou toutes en un seul, rejet des ICO et multi-frames, photos MPO acceptées). Utiliser dès qu'une tâche touche à batch_convert_img_to_cbz ou au traitement "Conversion en lot d'images isolées en CBZ".
---

# Conversion par lot d'images isolées en CBZ — MosaicView

Un des 8 traitements par lot du projet (skill `batch-processing`, **à lire en premier** pour l'architecture commune : pattern confirm/progress/summary, contrat `batch_callbacks`, registre anti-GC `_active_batches`, deux points d'entrée menu/drop). Ce skill-ci détaille uniquement les spécificités du flux IMG — ne pas dupliquer ici ce qui est déjà couvert par le skill général.

**Seul des 4 flux de conversion classiques à proposer un choix de mode avant même la sélection du dossier**, et le seul dont la sortie peut être **N fichiers CBZ** plutôt qu'un CBZ par archive source (il n'y a pas d'"archive source" ici, seulement des images isolées). Dans `modules/qt/batch_dialogs_qt.py`, de `_ImgModeDialog` à `_run_imgs_to_single_cbz`.

## Choix du mode — `_ImgModeDialog` (`batch_dialogs_qt.py`)

Affichée **avant** le sélecteur de dossier (`batch_convert_img_to_cbz`) — contrairement aux 3 autres flux classiques où le scan de dossier précède toute confirmation. Deux options radio, aucune présélectionnée par défaut sur la seconde (la première, `MODE_ONE_PER_IMAGE`, est cochée par défaut) :

- **`MODE_ONE_PER_IMAGE`** (défaut) — une image = un fichier `.cbz` distinct, nommé d'après l'image source (`{base_path}.cbz`). Route vers `batch_convert_img_to_cbz_confirm` → `_run_img_conversion`.
- **`MODE_ALL_IN_ONE`** — toutes les images trouvées dans le dossier (récursivement) regroupées en **un seul** `.cbz` multi-pages, nommé d'après le **dossier** scanné (`{nom_du_dossier}.cbz`, pas d'après une image). Route vers `batch_convert_imgs_to_single_cbz` → `_run_imgs_to_single_cbz`.

Annuler la fenêtre de mode (`chosen_mode = None`) interrompt tout le flux avant même d'atteindre le sélecteur de dossier — pas de repli sur un mode par défaut silencieux.

**Le scan récursif du dossier n'est fait qu'une seule fois**, après le choix du mode (`batch_convert_img_to_cbz`, filtré sur `IMAGE_EXTS`, la liste unique du projet, `archive_names.py`) — le mode choisi détermine seulement la fonction de confirmation appelée ensuite avec la même liste `img_files`.

## Deux fonctions de traitement quasi jumelles

`_run_img_conversion` (un CBZ par image) et `_run_imgs_to_single_cbz` (un seul CBZ) partagent une boucle de validation/conversion **par image strictement identique** — dupliquée entre les deux fonctions plutôt que factorisée (cohérent avec le choix assumé de duplication documenté dans le skill `batch-processing` pour l'ensemble des flux batch).

### Validation par image — rejets explicites, pas de conversion silencieusement fausse

Avant d'écrire quoi que ce soit, chaque image passe 3 vérifications strictes qui **lèvent une exception** (capturée et loggée comme erreur pour ce fichier, le lot continue avec les suivants) :

1. `Image.open(...)` + `.load()` — échec → `"invalid or corrupted image: {err}"`.
2. **`.ico` explicitement rejeté** (`tmp.format == 'ICO' or ext_lower == '.ico'`) → `"unsupported format: ICO files cannot be converted"` — un `.ico` n'a pas de sens comme page de comic, contrairement aux autres formats image du projet.
3. **Image multi-frame rejetée** (`getattr(tmp, 'n_frames', 1) > 1`, typiquement un GIF animé, skill `animated-gif`) → `"unsupported multi-frame image ({fmt}, {n_frames} frames)"` — évite qu'un GIF animé ne finisse comme une seule frame statique silencieusement fausse dans le CBZ de sortie ; la vérification lit `n_frames` directement sur l'objet PIL, pas une détection maison. **Exception : un JPEG MPO** (`tmp.format == 'MPO'`, photo de téléphone ou d'appareil photo avec une image annexe — aperçu, profondeur) compte pour une seule image : c'est une photo ordinaire, que tous les lecteurs affichent par sa première image. Sans cette exception, toute photo de téléphone était refusée comme « multi-frame ».

**Nom de la page d'après son vrai format** : `page_name = name_for_real_format(nom, tmp.format)` (`archive_names.py`, skill `archive-image-loading`) — un PNG nommé `.jpg` est écrit `.png` dans le CBZ, dans les deux modes.

**Seul flux batch avec ce niveau explicite de rejet par validation de contenu** — les 3 autres flux classiques (CBR/CB7/CBT) n'ont pas de notion de "type de fichier interdit" puisqu'ils travaillent sur des archives entières où chaque membre est traité tel quel (voir `batch-cbr-convert`, "une image non décodable par PIL traverse la conversion sans modification" — comportement opposé ici, où toute image qui échoue une validation est explicitement rejetée plutôt que copiée telle quelle).

### Suppression des images sources

- **Relecture avant suppression** : dans les deux modes, le CBZ produit est relu par `_cbz_verified` (skill `batch-processing`) avant toute suppression d'image — 1 fichier attendu par CBZ en mode un par image, `len(written_images)` en mode un seul CBZ. S'il ne se relit pas correctement, il est supprimé et aucune image n'est supprimée.
- **Un CBZ par image** : l'image est supprimée après la sortie du `with` de son CBZ, donc une fois celui-ci fermé.
- **Un seul CBZ** (`_run_imgs_to_single_cbz`) : chaque image écrite est ajoutée à `written_images` ; elles ne sont supprimées (et comptées dans `converted_count`/`converted_by_ext`) qu'**après la fermeture réussie du ZIP**. Tant que son répertoire central n'est pas écrit, le CBZ est illisible : supprimer les images au fil de l'eau les perdait toutes en cas d'interruption à la fermeture de l'application (`_interruptible_zip_writer` supprime alors le CBZ) ou d'échec de fermeture du ZIP. Voir skill `batch-processing`, "Suppression de la source — uniquement après une sortie complète".
- **Ordre et noms des pages du CBZ unique** : `_run_imgs_to_single_cbz` retrie les images par chemin relatif au dossier commun (dossier puis nom), puis nomme chaque page par `_flat_names` + `_unique_flat_name` (skill `batch-processing`) : `Ch1/001.jpg` → `Ch1_001.jpg`. Le dossier est parcouru récursivement par `utils.walk_without_links` (ni liens symboliques ni jonctions NTFS, skill `batch-processing`) : trié sur le seul nom de fichier, les sous-dossiers s'entrelaceraient, et deux images de même nom (`Ch1/001.jpg`, `Ch2/001.jpg`) donneraient deux entrées ZIP homonymes, dont une seule lisible, alors que toutes les images sources sont supprimées.

### Normalisation de mode couleur

Identique aux 3 autres flux classiques (`_normalized_page_bytes` pour `CMYK`/`YCbCr`/`I`/`F`, dans les deux modes du lot) — voir `batch-cbr-convert` pour le détail.

### Compression ZIP — réglage de l'utilisateur

Comme tous les autres lots, les deux modes écrivent le CBZ avec le niveau de compression ZIP de l'utilisateur : `_interruptible_zip_writer(cbz_path, **zip_compression_kwargs(_gcm().get_zip_compression_level()))` (skill `zip-compression`). Le niveau par défaut (0) écrit sans compression, ce qui convient aux images déjà compressées (JPEG, PNG, WebP).

## Compteurs par extension — `converted_by_ext`

Dictionnaire `{ext_lower: count}` accumulé pendant la boucle, transmis dans `summary_data` — seul flux batch dont le résumé (`_ImgSummaryDialog`) affiche une ligne **par extension convertie** (`dialogs.batch_img.converted_by_ext`, format `"{ext}: {count}"`), plutôt qu'un simple compteur total. Les labels sont reconstruits dynamiquement dans `_retranslate` (`self._ext_labels`, supprimés puis recréés à chaque appel — nécessaire puisque le nombre de lignes dépend des données, pas fixe comme les autres résumés).

## Collision de nom de sortie — suffixe différent des autres flux

`_01`, `_02`... plutôt que `" (N)"` utilisé par CBR/CB7/CBT/PDF : format `"{base}_{n:02d}{ext}"` passé à `free_name_candidates` (`batch_claims.py`), dans `claims.reserve(...)` — par image en mode un-par-image (`base_path` de l'image), une fois au lancement du lot en mode tout-en-un (nom du dossier, réservé pour toute la durée du lot). Le nom n'existe ni sur le disque ni dans les réservations d'un autre lot (skill `batch-processing`, « Un seul lot à la fois sur les mêmes fichiers »). Incohérence de convention à connaître si une tâche touche à l'un ou l'autre des deux styles de suffixe — ne pas les harmoniser sans consigne explicite.

**Lot refusé** si un autre lot traite déjà l'une des images (`_refuse_if_batch_running` dans les deux confirmations, `_start_batch_claims` au démarrage). En mode tout-en-un, `do_conversion` réserve aussi chaque image (`free_files`) et laisse de côté celles réservées par un autre lot, consignées dans le journal.

## Log et résumé

`Log_imgtocbz_{timestamp}.txt` — identique en structure aux autres logs (pas de section "renamed", comme PDF : une page mal nommée est écrite sous son vrai format dans le CBZ, sans être consignée). Le mode `ALL_IN_ONE` ajoute une ligne `Output: {cbz_path_out}` supplémentaire au log (le fichier de sortie n'est pas déductible du nom d'une image source individuelle, contrairement au mode un-par-image).

`_ImgSummaryDialog` (`batch_dialogs_qt.py`) — structure de base identique à `_CbrSummaryDialog` (message + liens dossiers + erreurs), sans les compteurs de renommage (n'existent pas pour ce flux) mais avec les compteurs par extension en plus.

## Comment étendre

- **Ajouter un format explicitement rejeté** (ex. un futur format non pertinent comme page de comic) : dupliquer la vérification `.ico` dans les **deux** fonctions (`_run_img_conversion` et `_run_imgs_to_single_cbz`) — ne pas oublier l'une des deux copies.
- **Harmoniser le suffixe de collision** (`_NN` ici vs `(N)` ailleurs) : cohérence visuelle mineure, à confirmer avant de changer un comportement de nommage de fichier existant.

## Pièges connus

- **Choix de mode avant le scan de dossier**, contrairement aux 3 autres flux classiques (scan avant confirmation) — ne pas supposer le même ordre d'opérations en copiant ce flux comme modèle.
- **`.ico` et images multi-frames explicitement rejetés**, pas simplement ignorés silencieusement ni copiés tels quels — comportement opposé à CBR/CB7/CBT où un fichier non-image traverse la conversion intact. Un JPEG MPO n'est pas une image multi-frame pour ce contrôle : l'exception est écrite dans les deux fonctions.
- **Suffixe de collision `_NN`, pas `" (N)"`** — incohérence de convention avec les 3 autres flux, propre à ce fichier.
- **Deux fonctions de traitement dupliquées** (`_run_img_conversion`/`_run_imgs_to_single_cbz`) — toute correction de bug dans la logique de validation/conversion par image doit être appliquée aux deux, elles ne partagent aucun code commun malgré leur quasi-identité.

## Références croisées

- `batch-processing` — architecture commune (pattern confirm/progress/summary/thread, contrat de callbacks, registre anti-GC, points d'entrée menu/drop) ; à lire avant ce skill-ci.
- `batch-cbr-convert` — comparaison utile pour la normalisation de mode couleur (identique) et le traitement des fichiers non-image (opposé : copiés tels quels là-bas, rejetés explicitement ici).
- `animated-gif` — notion de `n_frames > 1`/GIF animé, explicitement rejetée par ce flux plutôt que traitée.
- `zip-compression` — niveau de compression ZIP de l'utilisateur, appliqué aux deux modes.
- `archive-image-loading` — `name_for_real_format` (extension d'après le vrai format de l'image).
- `temp-files` — emplacement du log d'erreurs.
