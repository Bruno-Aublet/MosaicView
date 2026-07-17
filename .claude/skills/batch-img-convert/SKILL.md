---
name: batch-img-convert
description: Localiser ou modifier la conversion par lot d'images isolées en CBZ (un CBZ par image ou toutes en un seul, rejet des ICO et multi-frames). Utiliser dès qu'une tâche touche à batch_convert_img_to_cbz ou au traitement "Conversion en lot d'images isolées en CBZ".
---

# Conversion par lot d'images isolées en CBZ — MosaicView

Un des 8 traitements par lot du projet (skill `batch-processing`, **à lire en premier** pour l'architecture commune : pattern confirm/progress/summary, contrat `batch_callbacks`, registre anti-GC `_active_batches`, deux points d'entrée menu/drop). Ce skill-ci détaille uniquement les spécificités du flux IMG — ne pas dupliquer ici ce qui est déjà couvert par le skill général.

**Seul des 4 flux de conversion classiques à proposer un choix de mode avant même la sélection du dossier**, et le seul dont la sortie peut être **N fichiers CBZ** plutôt qu'un CBZ par archive source (il n'y a pas d'"archive source" ici, seulement des images isolées). Dans `modules/qt/batch_dialogs_qt.py:2039-2573`.

## Choix du mode — `_ImgModeDialog` (`batch_dialogs_qt.py:2039`)

Affichée **avant** le sélecteur de dossier (`batch_convert_img_to_cbz`, `batch_dialogs_qt.py:2184`) — contrairement aux 3 autres flux classiques où le scan de dossier précède toute confirmation. Deux options radio, aucune présélectionnée par défaut sur la seconde (la première, `MODE_ONE_PER_IMAGE`, est cochée par défaut) :

- **`MODE_ONE_PER_IMAGE`** (défaut) — une image = un fichier `.cbz` distinct, nommé d'après l'image source (`{base_path}.cbz`). Route vers `batch_convert_img_to_cbz_confirm` → `_run_img_conversion`.
- **`MODE_ALL_IN_ONE`** — toutes les images trouvées dans le dossier (récursivement) regroupées en **un seul** `.cbz` multi-pages, nommé d'après le **dossier** scanné (`{nom_du_dossier}.cbz`, pas d'après une image). Route vers `batch_convert_imgs_to_single_cbz` → `_run_imgs_to_single_cbz`.

Annuler la fenêtre de mode (`chosen_mode = None`) interrompt tout le flux avant même d'atteindre le sélecteur de dossier — pas de repli sur un mode par défaut silencieux.

**Le scan récursif du dossier n'est fait qu'une seule fois**, après le choix du mode (`batch_convert_img_to_cbz`, filtré sur `image_exts` module-level — la liste complète du projet, contrairement à `image_exts` locale et incomplète de CBT/CB7) — le mode choisi détermine seulement la fonction de confirmation appelée ensuite avec la même liste `img_files`.

## Deux fonctions de traitement quasi jumelles

`_run_img_conversion` (un CBZ par image) et `_run_imgs_to_single_cbz` (un seul CBZ) partagent une boucle de validation/conversion **par image strictement identique** — dupliquée entre les deux fonctions plutôt que factorisée (cohérent avec le choix assumé de duplication documenté dans le skill `batch-processing` pour l'ensemble des flux batch).

### Validation par image — rejets explicites, pas de conversion silencieusement fausse

Avant d'écrire quoi que ce soit, chaque image passe 3 vérifications strictes qui **lèvent une exception** (capturée et loggée comme erreur pour ce fichier, le lot continue avec les suivants) :

1. `Image.open(...)` + `.load()` — échec → `"invalid or corrupted image: {err}"`.
2. **`.ico` explicitement rejeté** (`tmp.format == 'ICO' or ext_lower == '.ico'`) → `"unsupported format: ICO files cannot be converted"` — un `.ico` n'a pas de sens comme page de comic, contrairement aux autres formats image du projet.
3. **Image multi-frame rejetée** (`getattr(tmp, 'n_frames', 1) > 1`, typiquement un GIF animé, skill `animated-gif`) → `"unsupported multi-frame image ({fmt}, {n_frames} frames)"` — évite qu'un GIF animé ne finisse comme une seule frame statique silencieusement fausse dans le CBZ de sortie ; la vérification lit `n_frames` directement sur l'objet PIL, pas une détection maison.

**Seul flux batch avec ce niveau explicite de rejet par validation de contenu** — les 3 autres flux classiques (CBR/CB7/CBT) n'ont pas de notion de "type de fichier interdit" puisqu'ils travaillent sur des archives entières où chaque membre est traité tel quel (voir `batch-cbr-convert`, "une image non décodable par PIL traverse la conversion sans modification" — comportement opposé ici, où toute image qui échoue une validation est explicitement rejetée plutôt que copiée telle quelle).

### Normalisation de mode couleur

Identique aux 3 autres flux classiques (`CMYK`/`YCbCr`/`I`/`F` → `RGB`, `fmt_map` par extension) — voir `batch-cbr-convert` pour le détail.

### Compression ZIP — `ZIP_STORED` fixe, pas le réglage utilisateur

**Seul flux batch qui n'utilise pas `zip_compression_kwargs`/le niveau configuré par l'utilisateur** (skill `zip-compression`) : `zipfile.ZipFile(cbz_path, 'w', compression=zipfile.ZIP_STORED)`, en dur. Rationnel documenté dans le skill `batch-processing` : une image seule (mode `ONE_PER_IMAGE`) ne bénéficie pas de la compression par lot puisqu'elle est déjà dans un format compressé (JPEG/PNG/WebP) — compresser à nouveau le conteneur ZIP n'apporterait qu'un gain négligeable pour un coût CPU inutile. Ce choix `ZIP_STORED` fixe s'applique **aux deux modes**, y compris `ALL_IN_ONE` où plusieurs images sont regroupées — à vérifier si une demande future porte sur l'harmonisation avec le réglage utilisateur pour ce second mode spécifiquement (actuellement identique aux deux, pas de distinction).

## Compteurs par extension — `converted_by_ext`

Dictionnaire `{ext_lower: count}` accumulé pendant la boucle, transmis dans `summary_data` — seul flux batch dont le résumé (`_ImgSummaryDialog`) affiche une ligne **par extension convertie** (`dialogs.batch_img.converted_by_ext`, format `"{ext}: {count}"`), plutôt qu'un simple compteur total. Les labels sont reconstruits dynamiquement dans `_retranslate` (`self._ext_labels`, supprimés puis recréés à chaque appel — nécessaire puisque le nombre de lignes dépend des données, pas fixe comme les autres résumés).

## Collision de nom de sortie — suffixe différent des autres flux

`_c:02d` (`"_01"`, `"_02"`...) plutôt que `" (N)"` utilisé par CBR/CB7/CBT/PDF — `f"{base_path}_{c:02d}.cbz"` en mode un-par-image, `f"{base}_{c:02d}.cbz"` en mode tout-en-un. Incohérence de convention à connaître si une tâche touche à l'un ou l'autre des deux styles de suffixe — ne pas les harmoniser sans consigne explicite.

## Log et résumé

`Log_imgtocbz_{timestamp}.txt` — identique en structure aux autres logs (pas de section "renamed", comme PDF, puisque IMG n'a pas de mécanisme de détection de format mal nommé). Le mode `ALL_IN_ONE` ajoute une ligne `Output: {cbz_path_out}` supplémentaire au log (le fichier de sortie n'est pas déductible du nom d'une image source individuelle, contrairement au mode un-par-image).

`_ImgSummaryDialog` (`batch_dialogs_qt.py:599`) — structure de base identique à `_CbrSummaryDialog` (message + liens dossiers + erreurs), sans les compteurs de renommage (n'existent pas pour ce flux) mais avec les compteurs par extension en plus.

## Comment étendre

- **Ajouter un format explicitement rejeté** (ex. un futur format non pertinent comme page de comic) : dupliquer la vérification `.ico` dans les **deux** fonctions (`_run_img_conversion` et `_run_imgs_to_single_cbz`) — ne pas oublier l'une des deux copies.
- **Appliquer le réglage de compression utilisateur en mode `ALL_IN_ONE`** (actuellement `ZIP_STORED` fixe comme le mode un-par-image) : changerait le comportement documenté dans le skill `batch-processing` — à valider avec l'utilisateur avant de le faire, ce n'est peut-être pas jugé pertinent même pour le regroupement multi-pages.
- **Harmoniser le suffixe de collision** (`_NN` ici vs `(N)` ailleurs) : cohérence visuelle mineure, à confirmer avant de changer un comportement de nommage de fichier existant.

## Pièges connus

- **Choix de mode avant le scan de dossier**, contrairement aux 3 autres flux classiques (scan avant confirmation) — ne pas supposer le même ordre d'opérations en copiant ce flux comme modèle.
- **`.ico` et images multi-frames explicitement rejetés**, pas simplement ignorés silencieusement ni copiés tels quels — comportement opposé à CBR/CB7/CBT où un fichier non-image traverse la conversion intact.
- **`ZIP_STORED` fixe, pas le réglage utilisateur** — seul flux batch dans ce cas ; ne pas supposer que `zip_compression_kwargs` s'applique ici comme dans les 3 autres flux classiques.
- **Suffixe de collision `_NN`, pas `" (N)"`** — incohérence de convention avec les 3 autres flux, propre à ce fichier.
- **Deux fonctions de traitement dupliquées** (`_run_img_conversion`/`_run_imgs_to_single_cbz`) — toute correction de bug dans la logique de validation/conversion par image doit être appliquée aux deux, elles ne partagent aucun code commun malgré leur quasi-identité.

## Références croisées

- `batch-processing` — architecture commune (pattern confirm/progress/summary/thread, contrat de callbacks, registre anti-GC, points d'entrée menu/drop) ; à lire avant ce skill-ci ; documente déjà le rationnel du `ZIP_STORED` fixe.
- `batch-cbr-convert` — comparaison utile pour la normalisation de mode couleur (identique) et le traitement des fichiers non-image (opposé : copiés tels quels là-bas, rejetés explicitement ici).
- `animated-gif` — notion de `n_frames > 1`/GIF animé, explicitement rejetée par ce flux plutôt que traitée.
- `zip-compression` — seul flux batch qui **n'utilise pas** ce mécanisme, compression `ZIP_STORED` fixe à la place.
- `temp-files` — emplacement du log d'erreurs.
