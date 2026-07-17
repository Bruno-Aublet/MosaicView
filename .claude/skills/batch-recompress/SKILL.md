---
name: batch-recompress
description: Localiser ou modifier la recompression ZIP en lot des CBZ (réécrit chaque CBZ au niveau de compression configuré, sans conversion ni suppression). Utiliser dès qu'une tâche touche à batch_recompress_cbz_confirm ou au traitement "Recompresser les CBZ au taux par défaut".
---

# Recompression ZIP en lot — MosaicView

Un des 8 traitements par lot du projet (skill `batch-processing`, **à lire en premier** pour l'architecture commune : pattern confirm/progress/summary, contrat `batch_callbacks`, registre anti-GC `_active_batches`, deux points d'entrée menu/drop). Ce skill-ci détaille les spécificités de la recompression — **le flux le plus différent des 4 conversions classiques après PDF**, puisqu'il ne convertit aucun format et ne supprime jamais de fichier.

Relit récursivement tous les CBZ d'un dossier et les réécrit avec le niveau de compression ZIP courant (skill `zip-compression`), dans `modules/qt/batch_dialogs_qt.py:2669-2844`.

## Ce que ce flux ne fait pas

Contrairement aux 5 autres flux de conversion :
- **Ne supprime jamais de fichier source** — pas de checkbox "suppression permanente" dans `_RecompressConfirmDialog` (`batch_dialogs_qt.py:2579`, fenêtre dédiée, pas `_ConfirmDialog` générique réutilisé comme les autres flux — voir section dédiée).
- **Ne convertit aucun format** — l'entrée et la sortie sont toutes deux `.cbz`, seule la méthode de compression ZIP change.

## `_RecompressConfirmDialog` — fenêtre dédiée, pas `_ConfirmDialog`

**Seul flux à ne pas réutiliser `_ConfirmDialog`** (le composant partagé documenté dans le skill `batch-processing`) — `_RecompressConfirmDialog` est une classe séparée, plus simple (pas de checkbox, message différent qui inclut le niveau de compression cible : `confirm_message.format(count=..., directory=..., level=...)`). Raison probable : le contrat de `_ConfirmDialog` suppose toujours une checkbox de suppression, absente ici par nature du traitement — plutôt que de rendre `_ConfirmDialog` conditionnel, le projet a dupliqué une version allégée.

## Détection du vrai format — filtre plus strict que les autres flux

Comme les 3 autres flux d'archive (CBR/CB7/CBT), chaque fichier passe par `detect_archive_type(path)` — mais la logique diverge nettement :

```python
if real_type != "CBZ":
    ignored_count[0] += 1
    continue
```

**Toute archive qui n'est pas réellement un ZIP est purement ignorée** (`ignored_count`, pas une erreur ni un renommage vers son vrai format) — contrairement à CBR/CB7/CBT qui **redirigent** activement un fichier mal identifié vers le bon format. La recompression ne fait qu'un seul travail (réécrire un ZIP existant), elle ne cherche pas à corriger l'extension d'un fichier qui serait en réalité un RAR/7z/TAR déguisé en `.cbz` — ce cas serait mieux traité par le flux de conversion approprié, pas par la recompression.

## Renommage — seulement pour un `.cbz`-mal-nommé, distinct de `ignored_count`

Cas différent du précédent : un fichier dont le **contenu réel est bien ZIP** mais dont l'**extension** n'est pas `.cbz` (ex. un `.zip` traînant, ou une extension incorrecte) est renommé vers `.cbz` (`renamed_entries`, même mécanisme de suffixe `" (N)"` que les autres flux) puis **traité normalement** ensuite (recompressé si nécessaire) — `target_path` bascule sur le nouveau chemin pour la suite du traitement de ce fichier. Ce cas est orthogonal à `real_type != "CBZ"` : ici `real_type == "CBZ"` (contenu réellement ZIP), c'est seulement l'extension du fichier qui diffère de `.cbz`.

## Skip "déjà optimal" — `_detect_zip_compression_state` (`archive_loader.py:131`)

Avant de recompresser, vérifie si le travail est nécessaire :

```python
state = _detect_zip_compression_state(target_path)   # 'stored' | 'deflated' | None
if level <= 0 and state == "stored":
    already_optimal_count[0] += 1
    continue
```

`_detect_zip_compression_state` lit le `compress_type` de la **première entrée fichier** de l'archive (pas toutes — suppose une cohérence interne, un CBZ n'ayant normalement jamais un mélange de méthodes de compression entre ses pages). **Piège de logique à connaître** : le skip ne s'applique que si `level <= 0` (niveau configuré = `ZIP_STORED`, pas de compression) **et** que l'archive est déjà `stored` — un CBZ déjà en `deflated` au niveau optimal configuré (ex. niveau 6) est **toujours recompressé**, même s'il n'y aurait objectivement rien à gagner, parce que ce cas de figure n'est pas détecté (`_detect_zip_compression_state` distingue `stored`/`deflated` mais pas le niveau de compression DEFLATE exact utilisé). Ce n'est pas un bug caché mais une limite assumée de l'optimisation — seul le cas `STORED→STORED` est reconnu comme travail inutile.

## Recompression réelle — fichier temporaire, jamais d'écriture en place

```python
tmp_path = target_path + ".~recompress.tmp"
with zipfile.ZipFile(target_path, "r") as zin, \
     zipfile.ZipFile(tmp_path, "w", **zip_compression_kwargs(level)) as zout:
    for name in zin.namelist():
        zout.writestr(name, zin.read(name))
shutil.move(tmp_path, target_path)
```

Lit et réécrit **toutes** les entrées telles quelles (`zin.read(name)` → `zout.writestr(name, ...)`) — aucune conversion PIL, aucune normalisation de mode couleur (contrairement aux 3 flux de conversion classiques) : la recompression ne touche jamais aux pixels, seulement à la méthode de stockage ZIP. Écrit dans un fichier temporaire à côté de l'original (suffixe `.~recompress.tmp`) puis `shutil.move` **atomique-ish** par-dessus l'original seulement après succès complet — en cas d'exception pendant l'écriture, le fichier temporaire orphelin est nettoyé explicitement (`if os.path.exists(tmp_path): os.remove(tmp_path)`), l'original n'est **jamais** corrompu par une recompression qui échoue en cours de route.

## Résumé — 3 compteurs de statut, pas de renommage à 3 formats

`_RecompressSummaryDialog` (`batch_dialogs_qt.py:2846`) affiche `recompressed_count`/`already_optimal_count`/`ignored_count`/`renamed_count`/`errors_count` — structure de comptage la plus riche des 8 traitements batch en nombre de catégories distinctes, mais **un seul type de renommage possible** (`.xxx → .cbz`, pas de redirection vers CBR/CB7/CBT comme les 3 autres flux d'archive), donc un seul compteur de renommage plutôt que 3.

`show_batch_recompress_summary(parent, data)` — **signature sans `callbacks`**, contrairement aux autres `show_batch_*_summary` — ce flux n'a besoin d'aucun callback dans son résumé (pas de bouton d'action secondaire comme le déverrouillage PDF).

## Log

`Log_recompress_{timestamp}.txt`, écrit si erreurs **ou** renommages (comme CBR/CB7/CBT) — inclut les 3 compteurs de statut en plus du nombre total et des erreurs.

## Comment étendre

- **Détecter aussi un niveau DEFLATE déjà optimal** (actuellement seul `STORED→STORED` est reconnu comme travail inutile) : nécessiterait d'étendre `_detect_zip_compression_state` (skill `zip-compression`) pour retourner le niveau de compression réel, pas seulement `stored`/`deflated` — changement partagé avec d'autres consommateurs de cette fonction, à valider avant de le faire.
- **Ajouter une checkbox optionnelle** (ex. ignorer certains sous-dossiers) : dans `_RecompressConfirmDialog`, fenêtre dédiée déjà séparée de `_ConfirmDialog` — pas de contrainte de compatibilité avec les autres flux à respecter ici.

## Pièges connus

- **`_RecompressConfirmDialog` n'est pas `_ConfirmDialog`** — ne pas supposer que ce flux réutilise le composant de confirmation générique documenté dans le skill `batch-processing` ; c'est le seul flux à en avoir une version dédiée.
- **Fichier réellement non-ZIP → ignoré silencieusement (compteur seulement)**, pas une redirection vers son vrai format — contrairement à CBR/CB7/CBT qui renomment activement. Ne pas confondre `ignored_count` (vrai non-CBZ) et `renamed_count` (CBZ valide mais mal étiqueté).
- **Skip "déjà optimal" limité au cas `STORED→STORED`** — un CBZ déjà en DEFLATE au bon niveau est toujours retraité, coût CPU non optimisé pour ce cas.
- **Aucune conversion de pixels/mode couleur** — contrairement aux 3 flux de conversion classiques, la recompression est un pur remplacement de méthode de stockage ZIP.
- **`show_batch_recompress_summary` n'a pas de paramètre `callbacks`** — signature différente des autres `show_batch_*_summary`, à ne pas copier par erreur pour un nouveau flux qui en aurait besoin.

## Références croisées

- `batch-processing` — architecture commune (pattern confirm/progress/summary/thread, contrat de callbacks, registre anti-GC, points d'entrée menu/drop) ; à lire avant ce skill-ci ; documente déjà pourquoi ce flux n'a pas de checkbox de suppression.
- `zip-compression` — `zip_compression_kwargs`/niveau utilisateur (réglage central appliqué ici), `_detect_zip_compression_state` (critère de skip "déjà optimal").
- `batch-cbr-convert` — comparaison utile sur la détection de format et le mécanisme de renommage, structurellement différents ici (ignoré vs redirigé).
- `temp-files` — emplacement du log d'erreurs/renommages.
