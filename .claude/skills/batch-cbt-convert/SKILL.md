---
name: batch-cbt-convert
description: Localiser ou modifier la conversion par lot CBT→CBZ (tarfile stdlib, sans dépendance externe). Utiliser dès qu'une tâche touche à batch_convert_cbt_to_cbz ou au traitement "Conversion en lot de fichiers CBT en CBZ".
---

# Conversion par lot CBT→CBZ — MosaicView

Un des 8 traitements par lot du projet (skill `batch-processing`, **à lire en premier** pour l'architecture commune : pattern confirm/progress/summary, contrat `batch_callbacks`, registre anti-GC `_active_batches`, deux points d'entrée menu/drop). Ce skill-ci détaille uniquement les spécificités du flux CBT — ne pas dupliquer ici ce qui est déjà couvert par le skill général.

Convertit récursivement tous les fichiers `.cbt` d'un dossier en `.cbz`, dans `modules/qt/batch_dialogs_qt.py` (`batch_convert_cbt_to_cbz` → `batch_convert_cbt_to_cbz_confirm` → `_run_cbt_conversion`, résumé `_CbtSummaryDialog`).

**Flux le plus simple des 4 conversions classiques** (skill `batch-processing`, section "Comment ajouter un nouveau flux") — pas de dépendance externe (`rarfile` pour CBR) ni de binaire embarqué (`7z.exe` pour CB7) ni de process séparé (PDF) : uniquement `tarfile`, module de la bibliothèque standard Python. Recommandé comme modèle de copie pour un nouveau flux batch simple.

## Lecture de l'archive — `tarfile.open(cbt_path, 'r:*')`

`'r:*'` = mode lecture avec **détection automatique de la compression** (tar non compressé, gzip, bzip2, xz) — pas besoin de connaître à l'avance quelle variante de TAR le fichier `.cbt` utilise. Ouverture en `with`, deux fois indépendamment (comme CBR) :

1. **Vignette** : `arc.getmembers()` filtré sur `m.isfile() and m.name.lower().endswith(IMAGE_EXTS)`, trié par `natural_sort_key`, `arc.extractfile(img_members[0]).read()` pour le premier. Échec silencieux (`except Exception: signals.update_thumb.emit(None)`).
2. **Conversion réelle** : `archive.getmembers()` filtré sur `m.isfile()` seulement (tous les fichiers, pas seulement les images — mêmes raisons que CBR : préserver `.nfo`/`ComicInfo.xml`), triés, extraits un par un via `archive.extractfile(member).read()`.

`IMAGE_EXTS` : la liste unique des extensions d'image du projet (`archive_names.py`, skill `archive-image-loading`), comme pour tous les lots. Le filtre ne s'applique qu'à la sélection de la vignette, pas à la boucle de conversion, qui traite `m.isfile()` sans filtre d'extension.

## Détection du vrai format et redirection

Même mécanique que les autres flux (`detect_archive_type`) — 3 formats de redirection possibles pour un `.cbt` mal nommé : **CBZ, CBR, CB7** (`renamed_cbz`/`renamed_cbr`/`renamed_cb7`), résumé à 3 compteurs conditionnels identique en structure à `batch-cbr-convert`/`batch-cb7-convert`.

## Écriture du CBZ, suppression, log, résumé

Strictement identiques aux deux autres flux "archive classique" (voir `batch-cbr-convert` pour le détail complet, transposable directement) : normalisation `CMYK`/`YCbCr`/`I`/`F` par `_normalized_page_bytes`, fallback écriture brute si PIL échoue, CBZ aplati avec noms homonymes suffixés `_2`, `_3`… via `_unique_flat_name(member.name, written_names)` (skill `batch-processing`, section "Écriture du CBZ de sortie"), `gc.collect()` tous les 20 pages, nom `.cbz` libre réservé auprès des autres lots et lot refusé si un autre lot traite déjà l'un de ses fichiers (skill `batch-processing`, « Un seul lot à la fois sur les mêmes fichiers »), CBZ incomplet jeté sans prendre son nom (`_IncompleteCbz`), suppression via `is_permanent`/`safe_delete_file`, `Log_cbttocbz_{timestamp}.txt`, `_CbtSummaryDialog`. La boucle est la fonction commune `_copy_pages_to_cbz`, avec `lambda m: archive.extractfile(m).read()` comme lecteur et `lambda m: _tar_entry_name(m.name)` comme nom de page — `tar_entry_name` (`modules/qt/archive_names.py`, module sans Qt partagé par tous les lecteurs) retire le préfixe `./` ou `/` que certains outils ajoutent, comme à l'ouverture d'un CBT (skill `archive-image-loading`) : sans lui, le CBZ produit contenait un faux sous-dossier « . » à aplatir avant tout enregistrement ; un membre illisible (TAR tronqué…) empêche la suppression du CBT (skill `batch-processing`, "Suppression de la source — uniquement après une sortie complète").

## Comment étendre

- **Ce fichier est le modèle recommandé** pour créer un nouveau flux de conversion batch simple — voir la section "Comment ajouter un nouveau flux" du skill `batch-processing`, qui recommande explicitement CBT comme base à copier plutôt que CBR/CB7/PDF/IMG.

## Pièges connus

- **Jamais de liste d'extensions locale** : toujours `IMAGE_EXTS` importée d'`archive_names.py` ; une copie locale incomplète écartait les pages `.jfif`/`.pjpeg`/`.pjp`/`.ico` de la vignette d'aperçu.
- **`'r:*'` déduit la compression automatiquement** — ne pas fixer un mode explicite (`'r:gz'`, etc.) sans revalider que ça ne casse pas la lecture d'un `.cbt` non/différemment compressé.
- Voir aussi les pièges génériques déjà documentés dans `batch-cbr-convert` (converted_count incrémenté même si suppression échoue, collision de nom de log à la minute près) — identiques ici.

## Références croisées

- `batch-processing` — architecture commune (pattern confirm/progress/summary/thread, contrat de callbacks, registre anti-GC, points d'entrée menu/drop) ; à lire avant ce skill-ci ; recommande ce flux CBT comme modèle de copie pour un nouveau format.
- `batch-cbr-convert` — détail complet du pattern d'écriture CBZ/log/résumé à 3 compteurs, transposable directement ici.
- `batch-cb7-convert` — le troisième format de redirection possible pour un fichier `.cbt` mal nommé.
- `zip-compression` — réglage appliqué à l'écriture du CBZ de sortie.
- `temp-files` — emplacement du log d'erreurs/renommages.
