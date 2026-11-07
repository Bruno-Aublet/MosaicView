---
name: batch-recompress
description: Localiser ou modifier la recompression ZIP en lot des CBZ (réécrit chaque CBZ au niveau de compression configuré, sans conversion ni suppression). Utiliser dès qu'une tâche touche à batch_recompress_cbz_confirm ou au traitement "Recompresser les CBZ au taux par défaut".
---

# Recompression ZIP en lot — MosaicView

Un des 8 traitements par lot du projet (skill `batch-processing`, **à lire en premier** pour l'architecture commune : pattern confirm/progress/summary, contrat `batch_callbacks`, registre anti-GC `_active_batches`, deux points d'entrée menu/drop). Ce skill-ci détaille les spécificités de la recompression — **le flux le plus différent des 4 conversions classiques après PDF**, puisqu'il ne convertit aucun format et ne supprime jamais de fichier.

Relit récursivement tous les CBZ d'un dossier et les réécrit avec le niveau de compression ZIP courant (skill `zip-compression`), dans `modules/qt/batch_dialogs_qt.py` (`batch_recompress_cbz_confirm` → `_run_recompress`). **Point d'entrée** : uniquement le dépôt d'un dossier (`drop_handler_qt._make_batch_recompress`), aucune entrée de menu. À ne pas confondre avec le clic sur l'indicateur ZIP de la barre de statut (`PanelWidget._zip_indicator_clicked`, skill `zip-compression`), qui réenregistre le comic ouvert par l'enregistrement d'un panneau : ses pages y sont réécrites à la date du jour, comme à tout enregistrement, choix de l'utilisateur.

## Ce que ce flux ne fait pas

Contrairement aux 5 autres flux de conversion :
- **Ne supprime jamais de fichier source** — pas de checkbox "suppression permanente" dans `_RecompressConfirmDialog` (`batch_dialogs_qt.py`, fenêtre dédiée, pas `_ConfirmDialog` générique réutilisé comme les autres flux — voir section dédiée).
- **Ne convertit aucun format** — l'entrée et la sortie sont toutes deux `.cbz`, seule la méthode de compression ZIP change.

## `_RecompressConfirmDialog` — fenêtre dédiée, pas `_ConfirmDialog`

**Seul flux à ne pas réutiliser `_ConfirmDialog`** (le composant partagé documenté dans le skill `batch-processing`) — `_RecompressConfirmDialog` est une classe séparée, plus simple (pas de checkbox, message différent qui inclut le niveau de compression cible : `confirm_message.format(count=..., directory=..., level=...)`). Raison probable : le contrat de `_ConfirmDialog` suppose toujours une checkbox de suppression, absente ici par nature du traitement — plutôt que de rendre `_ConfirmDialog` conditionnel, le projet a dupliqué une version allégée.

## Détection du vrai format — filtre plus strict que les autres flux

Comme les 3 autres flux d'archive (CBR/CB7/CBT), chaque fichier passe par `detect_archive_type(path)` — mais la logique diverge nettement :

```python
if real_type != "CBZ":
    ignored_entries.append(basename)
    continue
```

**Format inconnu, fichier disparu ou illisible** (`detect_archive_type` rend `None`) : une erreur du journal (`messages.errors.batch_unknown_format`), jamais « pas un CBZ », que rien ne prouve. **Vraie archive d'un autre format** : ignorée (`ignored_entries`, nommée dans le journal, pas une erreur ni un renommage vers son vrai format) — contrairement à CBR/CB7/CBT qui **redirigent** activement un fichier mal identifié vers le bon format. La recompression ne fait qu'un seul travail (réécrire un ZIP existant), elle ne cherche pas à corriger l'extension d'un fichier qui serait en réalité un RAR/7z/TAR déguisé en `.cbz` — ce cas serait mieux traité par le flux de conversion approprié, pas par la recompression.

## Renommage — seulement pour un `.cbz`-mal-nommé, distinct de `ignored_count`

Cas différent du précédent : un fichier dont le **contenu réel est bien ZIP** mais dont l'**extension** n'est pas `.cbz` (un ZIP nommé `.cbr`, `.cb7` ou `.cbt` : le dépôt d'un dossier ne recherche que `.cbz`, `.cbr`, `.cb7` et `.cbt`, jamais `.zip`) est renommé vers `.cbz` (`renamed_entries`, nom libre `" (N)"` réservé auprès des autres lots par `claims.reserve(free_name_candidates(...))`, comme les autres flux, skill `batch-processing`) puis **traité normalement** ensuite (recompressé si nécessaire) — `target_path` bascule sur le nouveau chemin pour la suite du traitement de ce fichier. Ce cas est orthogonal à `real_type != "CBZ"` : ici `real_type == "CBZ"` (contenu réellement ZIP), c'est seulement l'extension du fichier qui diffère de `.cbz`.

## « Déjà au taux par défaut » — `_all_members_stored`, `_members_compression`, `_AlreadyAtLevel`

Avant de recompresser, vérifie si le travail est nécessaire :

```python
if level <= 0 and _all_members_stored(target_path):
    already_optimal_count[0] += 1
    continue
```

**Taux 0** : `_all_members_stored` examine la méthode de **tous** les fichiers, dans le répertoire de l'archive, sans rien décompresser (mesuré sur 2000 pages : quelques dizaines de millisecondes, surtout l'ouverture). Un CBZ dont seul le premier fichier n'est pas compressé est recompressé. Archive illisible : `False`, l'erreur vient de la recompression qui suit.

**Taux supérieur à 0** : un ZIP n'indique pas le taux employé, seul le résultat le dit. Le CBZ est donc recompressé, puis `_members_compression` (méthode et taille compressée de chaque fichier, dossiers exclus) compare le temporaire à l'original, relevé **avant** la boucle car l'écriture met à jour les `ZipInfo` de l'original. Identiques : `_AlreadyAtLevel`, levée dans le bloc d'écriture, jette le temporaire ; l'original garde sa date et compte dans `already_optimal_count`. Le temps de calcul reste dépensé à chaque passage. Le résultat peut être identique à deux taux différents (pages JPEG, peu compressibles) : l'original reste alors en place, sans perte puisqu'il a les mêmes tailles.

## Recompression réelle — fichier temporaire, jamais d'écriture en place

```python
with _interruptible_zip_writer(target_path, verify=False, check_structure=True,
                               **zip_compression_kwargs(level)) as zout:
    with open_zip(target_path) as zin:       # refermé avant la sortie du bloc d'écriture
        before = _members_compression(zin)
        for info in zin.infolist():
            _check_member_size(info.file_size)
            zout.writestr(info, zin.read(info), compress_type=…, compresslevel=…)
        if _members_compression(zout) == before:
            raise _AlreadyAtLevel()
        zout.comment = zin.comment
        zout.expected_count = ...            # fichiers de la source, dossiers exclus
# sortie du bloc : contrôle de structure, puis l'original est remplacé
```

L'écrivain des lots (enveloppe de `converted_cbz.write_cbz`, skill `batch-processing`, « Écrire, relire, supprimer ») écrit dans **un seul** temporaire du dossier et ne remplace l'original qu'à la sortie du bloc. `verify=False` : pas de relecture complète (aucune source n'est supprimée) ; `check_structure=True` : contrôle rapide de structure (`check_written_cbz_structure`, nombre de fichiers `expected_count` de la source, comme un enregistrement depuis un panneau, skill `save-export`) avant le remplacement. **Piège** : l'original doit être refermé **dans** le bloc d'écriture — ouvert dans un `with` englobant (ou sur la même ligne que l'écrivain), il serait encore ouvert au moment du remplacement, que Windows refuserait. Garde-fou `tests/test_check_atomic_file_writes.py` : la recompression doit relire ou demander ce contrôle de structure.

**Taille de chaque fichier de l'archive contrôlée avant lecture** (`archive_loader._check_member_size`, limite unique `MAX_MEMBER_SIZE` de 500 Mo, skill `archive-image-loading`) : `zin.read(info)` charge le membre entier en mémoire. Un membre au-delà lève `ArchiveRefusedError` : erreur consignée, temporaire supprimé, original intact. La limite vaut par fichier, jamais pour l'archive : un CBZ de plusieurs Go fait de pages ordinaires est recompressé normalement. La vignette de la fenêtre de progression lit la première image par son `ZipInfo` (jamais par nom), après le même contrôle. Testé par `tests/test_batch_loops.py`.

Le temporaire (nommé `.~mosaicview_*.tmp` par `utils.make_temp_path`, comme tous les temporaires écrits à côté d'un fichier de l'utilisateur, pour être nettoyé après un plantage par `remove_stale_temp_files`, skill `save-export`) est supprimé par l'écrivain sur tout échec, y compris sur `_BatchInterrupted` (BaseException, que le `except Exception` de la boucle ne voit pas), sauf s'il est devenu la seule copie (`is_kept_temp`).

Lit et réécrit **toutes** les entrées telles quelles (`zin.read(info)` → `zout.writestr(info, …)`). **Lecture par `ZipInfo`, jamais par nom** : dans un ZIP contenant deux membres de même nom, `zin.read(nom)` rend le contenu du dernier pour chacun, et la recompression remplacerait le premier par une copie du second (perte définitive). **Écriture par ce même `ZipInfo`**, avec `compress_type` et `compresslevel` passés à `writestr` : seule la compression change, le fichier garde sa date, son commentaire, ses données supplémentaires et ses attributs. **Piège** : `writestr(info.filename, …)` crée un fichier neuf, daté du jour et sans ces informations. Le fichier CBZ lui-même prend la date du jour dans l'Explorateur, comme tout fichier réécrit (choix de l'utilisateur, et le scan de la Bibliothèque se sert de cette date). Archive ouverte par `archive_names.open_zip` : un nom écrit sans le drapeau UTF-8 est réécrit sous son vrai nom (skill `archive-image-loading`), au lieu d'être fixé tel que Python l'a mal lu. — aucune conversion PIL, aucune normalisation de mode couleur (contrairement aux 3 flux de conversion classiques) : la recompression ne touche jamais aux pixels, seulement à la méthode de stockage ZIP. Écrit dans un fichier temporaire à côté de l'original puis remplacement **atomique** par-dessus l'original (`replace_file_retrying`, même dossier, donc même volume ; l'original garde sa date de création et ses attributs, skill `save-export`) seulement après succès complet — jamais `shutil.move`, qui sous Windows tronque l'original existant puis le recopie (`os.rename` y refuse une destination existante) — l'original n'est **jamais** corrompu par une recompression qui échoue en cours de route.

## Résumé — 3 compteurs de statut, pas de renommage à 3 formats

`_RecompressSummaryDialog` (`batch_dialogs_qt.py`) affiche `recompressed_count`/`already_optimal_count`/`ignored_count` (`summary_message`), `renamed_count` seulement s'il est supérieur à 0 (ligne `renamed_line`, annoncée aussi par `confirm_message`) et `errors_count`. `total` compte les fichiers réellement traités (somme des compteurs) : après Annuler, le fichier en cours n'en fait pas partie. À la fin du lot, `_update_open_comics_compression` met à jour l'état ZIP et la barre de statut des panneaux qui ont ouvert un fichier réécrit. Le texte et sa hauteur (`setMinimumHeight(heightForWidth)`, fenêtre à largeur fixe) sont posés dans `_retranslate`. Structure de comptage la plus riche des 8 traitements batch en nombre de catégories distinctes, mais **un seul type de renommage possible** (`.xxx → .cbz`, pas de redirection vers CBR/CB7/CBT comme les 3 autres flux d'archive), donc un seul compteur de renommage plutôt que 3.

`show_batch_recompress_summary(parent, data)` — **signature sans `callbacks`**, contrairement aux autres `show_batch_*_summary` — ce flux n'a besoin d'aucun callback dans son résumé (pas de bouton d'action secondaire comme le déverrouillage PDF).

## Log

`Log_recompress_{timestamp}.txt`, écrit s'il y a des erreurs, des renommages **ou** des fichiers ignorés — inclut les compteurs de statut, le nombre de fichiers trouvés et, par section, les fichiers renommés, les fichiers ignorés (titre : la ligne `ignored_not_cbz`) et les erreurs.

## Comment étendre

- **Ajouter une checkbox optionnelle** (ex. ignorer certains sous-dossiers) : dans `_RecompressConfirmDialog`, fenêtre dédiée déjà séparée de `_ConfirmDialog` — pas de contrainte de compatibilité avec les autres flux à respecter ici.

## Pièges connus

- **`_RecompressConfirmDialog` n'est pas `_ConfirmDialog`** — ne pas supposer que ce flux réutilise le composant de confirmation générique documenté dans le skill `batch-processing` ; c'est le seul flux à en avoir une version dédiée.
- **Fichier réellement d'un autre format → ignoré (compteur, et son nom au journal)**, pas une redirection vers son vrai format — contrairement à CBR/CB7/CBT qui renomment activement. Ne pas confondre `ignored_count` (vrai non-CBZ) et `renamed_count` (CBZ valide mais mal étiqueté).
- **« Déjà au taux » au-dessus de 0 ne fait pas gagner de temps** — un CBZ déjà au bon niveau est recompressé pour le savoir, seule sa réécriture est évitée.
- **Aucune conversion de pixels/mode couleur** — contrairement aux 3 flux de conversion classiques, la recompression est un pur remplacement de méthode de stockage ZIP.
- **`show_batch_recompress_summary` n'a pas de paramètre `callbacks`** — signature différente des autres `show_batch_*_summary`, à ne pas copier par erreur pour un nouveau flux qui en aurait besoin.

## Références croisées

- `batch-processing` — architecture commune (pattern confirm/progress/summary/thread, contrat de callbacks, registre anti-GC, points d'entrée menu/drop) ; à lire avant ce skill-ci ; documente déjà pourquoi ce flux n'a pas de checkbox de suppression.
- `zip-compression` — `zip_compression_kwargs`/niveau utilisateur (réglage central appliqué ici), état `zip_compression_state` des panneaux mis à jour à la fin du lot.
- `batch-cbr-convert` — comparaison utile sur la détection de format et le mécanisme de renommage, structurellement différents ici (ignoré vs redirigé).
- `temp-files` — emplacement du log d'erreurs/renommages.
