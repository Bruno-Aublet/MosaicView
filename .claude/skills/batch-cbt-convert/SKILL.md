---
name: batch-cbt-convert
description: Localiser ou modifier la conversion par lot CBT→CBZ (tarfile stdlib, sans dépendance externe). Utiliser dès qu'une tâche touche à batch_convert_cbt_to_cbz ou au traitement "Conversion en lot de fichiers CBT en CBZ".
---

# Conversion par lot CBT→CBZ — MosaicView

Un des 8 traitements par lot du projet (skill `batch-processing`, **à lire en premier** pour l'architecture commune : pattern confirm/progress/summary, contrat `batch_callbacks`, registre anti-GC `_active_batches`, deux points d'entrée menu/drop). Ce skill-ci détaille uniquement les spécificités du flux CBT — ne pas dupliquer ici ce qui est déjà couvert par le skill général.

Convertit récursivement tous les fichiers `.cbt` d'un dossier en `.cbz`, dans `modules/qt/batch_dialogs_qt.py:1502-1763`.

**Flux le plus simple des 4 conversions classiques** (skill `batch-processing`, section "Comment ajouter un nouveau flux") — pas de dépendance externe (`rarfile` pour CBR) ni de binaire embarqué (`7z.exe` pour CB7) ni de process séparé (PDF) : uniquement `tarfile`, module de la bibliothèque standard Python. Recommandé comme modèle de copie pour un nouveau flux batch simple.

## Lecture de l'archive — `tarfile.open(cbt_path, 'r:*')`

`'r:*'` = mode lecture avec **détection automatique de la compression** (tar non compressé, gzip, bzip2, xz) — pas besoin de connaître à l'avance quelle variante de TAR le fichier `.cbt` utilise. Ouverture en `with`, deux fois indépendamment (comme CBR) :

1. **Vignette** : `arc.getmembers()` filtré sur `m.isfile() and m.name.lower().endswith(image_exts)`, trié par `natural_sort_key`, `arc.extractfile(img_members[0]).read()` pour le premier. Échec silencieux (`except Exception: signals.update_thumb.emit(None)`).
2. **Conversion réelle** : `archive.getmembers()` filtré sur `m.isfile()` seulement (tous les fichiers, pas seulement les images — mêmes raisons que CBR : préserver `.nfo`/`ComicInfo.xml`), triés, extraits un par un via `archive.extractfile(member).read()`.

`image_exts` local à cette fonction (`.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`, `.tiff`, `.tif`, `.gif`, `.avif`) — **sans `.ico`/`.jfif`/`.pjpeg`/`.pjp`**, contrairement à `image_exts` module-level utilisé par CBR (`batch_dialogs_qt.py:47`, la liste complète du projet) ; liste redéfinie localement dans `_run_cbt_conversion`, identique à celle de CB7. Une image `.ico` dans un CBT ne serait donc jamais choisie comme vignette d'aperçu (mais reste bien copiée dans le CBZ de sortie, le filtre ne s'applique qu'à la sélection de vignette, pas à la boucle de conversion qui traite `m.isfile()` sans filtre d'extension).

## Détection du vrai format et redirection

Même mécanique que les autres flux (`detect_archive_type`) — 3 formats de redirection possibles pour un `.cbt` mal nommé : **CBZ, CBR, CB7** (`renamed_cbz`/`renamed_cbr`/`renamed_cb7`), résumé à 3 compteurs conditionnels identique en structure à `batch-cbr-convert`/`batch-cb7-convert`.

## Écriture du CBZ, suppression, log, résumé

Strictement identiques aux deux autres flux "archive classique" (voir `batch-cbr-convert` pour le détail complet, transposable directement) : normalisation `CMYK`/`YCbCr`/`I`/`F` → `RGB`, fallback écriture brute si PIL échoue, `gc.collect()` tous les 20 pages, collision de nom `.cbz`, suppression via `is_permanent`/`safe_delete_file`, `Log_cbttocbz_{timestamp}.txt`, `_CbtSummaryDialog`.

## Comment étendre

- **Ce fichier est le modèle recommandé** pour créer un nouveau flux de conversion batch simple — voir la section "Comment ajouter un nouveau flux" du skill `batch-processing`, qui recommande explicitement CBT comme base à copier plutôt que CBR/CB7/PDF/IMG.
- **Unifier `image_exts` locale avec la liste module-level** (actuellement dupliquée, incomplète par rapport à celle de CBR) : changement mineur de cohérence, à confirmer avant de le faire — pourrait légèrement changer le choix de vignette pour un CBT contenant uniquement des `.ico` en première page (cas marginal).

## Pièges connus

- **`image_exts` locale à cette fonction, différente de la liste module-level** (`.ico`/`.jfif`/`.pjpeg`/`.pjp` absents) — n'affecte que le choix de la vignette d'aperçu pendant la conversion, pas la conversion elle-même (qui copie tout fichier, sans filtre d'extension).
- **`'r:*'` déduit la compression automatiquement** — ne pas fixer un mode explicite (`'r:gz'`, etc.) sans revalider que ça ne casse pas la lecture d'un `.cbt` non/différemment compressé.
- Voir aussi les pièges génériques déjà documentés dans `batch-cbr-convert` (converted_count incrémenté même si suppression échoue, collision de nom de log à la minute près) — identiques ici.

## Références croisées

- `batch-processing` — architecture commune (pattern confirm/progress/summary/thread, contrat de callbacks, registre anti-GC, points d'entrée menu/drop) ; à lire avant ce skill-ci ; recommande ce flux CBT comme modèle de copie pour un nouveau format.
- `batch-cbr-convert` — détail complet du pattern d'écriture CBZ/log/résumé à 3 compteurs, transposable directement ici.
- `batch-cb7-convert` — le troisième format de redirection possible pour un fichier `.cbt` mal nommé, et la même `image_exts` locale incomplète.
- `zip-compression` — réglage appliqué à l'écriture du CBZ de sortie.
- `temp-files` — emplacement du log d'erreurs/renommages.
