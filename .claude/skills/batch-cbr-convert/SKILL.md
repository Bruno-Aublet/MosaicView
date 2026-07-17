---
name: batch-cbr-convert
description: Localiser ou modifier la conversion par lot CBR→CBZ (dépendance rarfile). Utiliser dès qu'une tâche touche à batch_convert_cbr_to_cbz ou au traitement "Conversion en lot de fichiers CBR en CBZ".
---

# Conversion par lot CBR→CBZ — MosaicView

Un des 8 traitements par lot du projet (skill `batch-processing`, **à lire en premier** pour l'architecture commune : pattern confirm/progress/summary, contrat `batch_callbacks`, registre anti-GC `_active_batches`, deux points d'entrée menu/drop). Ce skill-ci détaille uniquement les spécificités du flux CBR — ne pas dupliquer ici ce qui est déjà couvert par le skill général.

Convertit récursivement tous les fichiers `.cbr` d'un dossier en `.cbz`, dans `modules/qt/batch_dialogs_qt.py:715-981`.

## Dépendance bloquante — `rarfile`

**Seul flux à bloquer avant même le scan du dossier** si sa dépendance est absente : `batch_convert_cbr_to_cbz` (`batch_dialogs_qt.py:715`) vérifie `if rarfile is None` en tout premier, avant même d'ouvrir le sélecteur de dossier — `ErrorDialog` immédiat (`dialogs.batch_cbr.no_cbr_title`/`rarfile_unavailable`) si le module `rarfile` n'a pas pu être importé (`try: import rarfile except ImportError: rarfile = None`, en tête de `batch_dialogs_qt.py`). Contrairement à CB7 (dépend d'un binaire externe `7zip/`, jamais absent car embarqué) ou CBT (`tarfile` stdlib, toujours disponible).

## Détection du vrai format — `detect_archive_type`

Avant tout traitement, chaque fichier passe par `detect_archive_type(cbr_path)` (magic bytes, `archive_type_detector.py`) — un `.cbr` mal nommé qui est en réalité un ZIP/7z/TAR est **simplement renommé** vers la bonne extension plutôt que "converti" :

```python
if real_type in ("CBZ", "CB7", "CBT"):
    ext_map = {"CBZ": ".cbz", "CB7": ".cb7", "CBT": ".cbt"}
    ...
    os.rename(cbr_path, new_path)
```

**Particularité propre à CBR** parmi les 4 flux de conversion classiques : c'est le seul point d'entrée qui peut détecter et rediriger vers **3 formats de sortie différents** (CBZ, CB7, **et** CBT) plutôt qu'un seul — cohérent avec le fait qu'un CBR est l'archive la plus susceptible d'être mal identifiée historiquement (RAR étant un format propriétaire longtemps confondu avec d'autres par des outils de scan approximatifs). Trois compteurs séparés (`renamed_cbz`/`renamed_cb7`/`renamed_cbt`), chacun affiché dans le résumé seulement si `> 0`. Un format totalement inconnu (`real_type is None`) → erreur loggée (`"{basename}: format inconnu"`), fichier ignoré, pas de tentative de conversion en aveugle.

## Lecture de l'archive RAR — `rarfile.RarFile`

1. **Vignette** (avant la conversion réelle) : liste les fichiers de `arc.namelist()` filtrés par extension image (`image_exts`, la même liste que le reste du projet), triés par `natural_sort_key` (skill `sort-images`, même fonction que la mosaïque), lit le **premier** en tant qu'aperçu. Échec silencieux (`except Exception: signals.update_thumb.emit(None)`) — une vignette manquante n'interrompt jamais la conversion.
2. **Conversion réelle** : relit l'archive une seconde fois (nouvel objet `RarFile`, pas de réutilisation du premier), liste **tous** les fichiers (pas seulement les images — un CBR peut contenir des `.nfo`/`ComicInfo.xml` à préserver), triés de la même façon. Archive vide (`total_pages == 0`) → erreur, fichier ignoré (aucun CBZ vide n'est créé).

## Écriture du CBZ — normalisation de mode couleur

Pour chaque fichier de l'archive source, tente de l'ouvrir en PIL et convertit les modes non-standard (`CMYK`/`YCbCr`/`I`/`F`) vers `RGB` avant réécriture — `fmt_map` associe l'extension d'origine au format de sauvegarde PIL (`.jpg`/`.jpeg` → `JPEG` avec `quality=100, optimize=True`, sinon format déduit de l'extension). **Si l'ouverture PIL échoue** (fichier non-image, `.nfo`, `ComicInfo.xml`, image dans un format que PIL ne sait pas décoder), le `raw` original est écrit **tel quel sans modification** (`except Exception: pass`, le bloc `try` englobe seulement la tentative de conversion, pas l'écriture) — un fichier non-image traverse donc la conversion intact, jamais perdu ni corrompu par une tentative de traitement image qui ne le concerne pas.

`gc.collect()` tous les 20 pages pendant l'écriture (limite le pic mémoire sur une grosse archive), et un second `gc.collect()` après la fermeture du ZIP.

## Suppression de la source — `is_permanent`

Après écriture réussie du CBZ : `os.remove(cbr_path)` si la checkbox "suppression permanente" de `_ConfirmDialog` est cochée, sinon `callbacks['safe_delete_file'](cbr_path)` (corbeille Windows). Un échec de suppression (`del_err`) est loggé séparément (`"{basename} (suppression): {del_err}"`) mais **`converted_count` est déjà incrémenté avant** ce bloc — la conversion est comptée comme réussie même si le fichier source n'a pas pu être supprimé, cohérent avec le fait que le vrai résultat (le CBZ) existe bien à ce stade.

## Résumé — `_CbrSummaryDialog` (`batch_dialogs_qt.py:356`)

Le plus riche des dialogues de résumé du projet parmi les flux de conversion classiques, en raison des 3 compteurs de renommage possibles :
- Message principal : `complete_message` (pas d'erreur/renommage) ou `complete_message_errors` (avec `count`/`total`) — bascule dès qu'il y a **soit** des erreurs **soit** au moins un renommage (`has_renamed = any(data.get(k, 0) > 0 for k in (...))`).
- Un `QLabel` par compteur de renommage non nul (`renamed_cbz_count`/`renamed_cb7_count`/`renamed_cbt_count`), créés dynamiquement dans `__init__` (pas dans `_retranslate`, donc figés si le dialogue reste ouvert — cohérent puisque les compteurs eux-mêmes ne changent jamais après la fin du traitement, seul le **texte** de ces labels suit `_retranslate()`).
- Lien cliquable vers le log si présent (erreurs et/ou renommages) — `_open_path` (`os.startfile` sur Windows), menu contextuel dédié (`setup_path_label_context_menu`, skill `qt-context-menus`).

## Log d'erreurs — `Log_cbrtocbz_{timestamp}.txt`

Écrit dans `get_mosaicview_temp_dir()` (skill `temp-files`) uniquement si `conversion_errors` **ou** `renamed_entries` est non vide — contenu : compteurs globaux, puis section "Renamed files" (liste `{ancien} → {nouveau}`) si des renommages, puis section "Error details" si des erreurs. Le nom de fichier inclut un timestamp à la minute (`%Y_%m_%d_%H_%M`) — deux lots lancés dans la même minute écraseraient le même fichier log (limite théorique, jamais un vrai problème en usage normal).

## Comment étendre

- **Ajouter une 4e redirection de renommage** (ex. détecter un futur format supplémentaire) : `ext_map`/`label_map`/`counter_map` dans `do_conversion`, plus un nouveau compteur `renamed_xxx = [0]` et son label correspondant dans `_CbrSummaryDialog.__init__`/`_retranslate`.
- **Changer la politique de conversion de mode couleur** : uniquement le bloc `try` autour de `tmp = Image.open(...)` dans `do_conversion` — `fmt_map` et les kwargs JPEG.
- Voir aussi la section "Comment ajouter un nouveau flux" du skill `batch-processing` — ce fichier CBR n'est **pas** le modèle recommandé pour copier un nouveau flux (le skill général recommande CBT, plus simple, sans les 3 redirections de renommage).

## Pièges connus

- **Le blocage `rarfile is None` intervient avant le scan de dossier**, pas après — un flux copié depuis CBR pour un format sans dépendance externe ne doit pas reproduire ce garde-fou en tête de fonction.
- **3 compteurs de renommage possibles, pas 1** — CBR est le seul flux où le résumé doit afficher jusqu'à 3 lignes de renommage distinctes selon le format réel détecté.
- **Une image non décodable par PIL traverse la conversion sans modification** — ne pas supposer que toutes les entrées d'un CBZ de sortie sont passées par une normalisation de mode couleur.
- **`converted_count` incrémenté même si la suppression de la source échoue** — la réussite de la conversion et la réussite de la suppression sont deux états indépendants dans le comptage.
- **Timestamp de log à la minute près** — collision de nom de fichier log théoriquement possible entre deux lots très rapprochés.

## Références croisées

- `batch-processing` — architecture commune (pattern confirm/progress/summary/thread, contrat de callbacks, registre anti-GC, points d'entrée menu/drop) ; à lire avant ce skill-ci.
- `batch-cb7-convert` / `batch-cbt-convert` — les deux autres formats vers lesquels CBR peut rediriger un fichier mal nommé.
- `zip-compression` — réglage `zip_compression_kwargs`/niveau utilisateur appliqué à l'écriture du CBZ de sortie.
- `sort-images` — `natural_sort_key`, réutilisé ici pour trier les fichiers de l'archive comme pour la mosaïque.
- `temp-files` — `get_mosaicview_temp_dir()`, emplacement du log d'erreurs/renommages.
- `qt-context-menus` — menu contextuel du lien vers le log dans le résumé.
