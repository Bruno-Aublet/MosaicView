---
name: batch-processing
description: Localiser ou modifier les traitements par lot de MosaicView (conversions CBR/CB7/CBT/PDF/IMG→CBZ, import ComicVine en masse, recompression ZIP en masse). Utiliser dès qu'une tâche touche à batch_dialogs_qt.py, drop_handler_qt.py, ou à un nouveau traitement en lot.
---

# Traitements par lot (batch) — MosaicView

Sept traitements qui parcourent un **dossier entier** (récursivement) et agissent sur chaque fichier trouvé, avec confirmation → progression → résumé. Deux points d'entrée : le menu **Fichier > Batch**, et le **drag & drop d'un dossier** sur l'application.

## Les trois fichiers

| Fichier | Rôle |
|---|---|
| `modules/qt/batch_dialogs_qt.py` (~2940 lignes) | Conversions CBR/CB7/CBT/PDF/IMG → CBZ, et recompression ZIP en lot. Le plus gros fichier, très répétitif (5 flux de conversion quasi identiques). |
| `modules/qt/batch_metadata_dialog_qt.py` | Import de métadonnées ComicVine en lot (orchestre l'ouverture séquentielle de la fenêtre ComicVine pour chaque fichier). |
| `modules/qt/batch_drop_dialog_qt.py` | Fenêtre de choix du traitement (`BatchDropDialog`) affichée uniquement quand le déclencheur est un drop de dossier(s) — pas depuis le menu, où le type est déjà choisi par l'entrée de menu cliquée. |

Un huitième traitement (**Bibliothèque**) ne vit pas dans ces fichiers : il ouvre `LibraryWindow` avec un `NewDbDialog` pré-rempli — voir skill `library`.

## Les deux points d'entrée

### 1. Menu Fichier > Batch (`menubar_qt.py` ~ligne 99-103, 355)

Chaque entrée de menu appelle directement une méthode `PanelWidget._batch_convert_*` (`panel_widget.py:795-836`) qui demande le dossier (`QFileDialog.getExistingDirectory`) puis lance directement le bon flux — pas de passage par `BatchDropDialog`, le choix est déjà fait par l'entrée de menu cliquée.

```
menubar_qt.py → callbacks["batch_convert_cbr_to_cbz"] → mw._batch_convert_cbr_to_cbz()
              → panel_widget.py:795 → _qt_batch_cbr(self, self._get_batch_callbacks())
              → batch_dialogs_qt.batch_convert_cbr_to_cbz(parent, callbacks, directory=None)
```

`batch_metadata` (menu) appelle `PanelWidget._batch_metadata()` (`panel_widget.py:810`), qui gère lui-même la sélection de dossier avant d'appeler `show_batch_metadata_dialog`.

### 2. Drag & drop d'un dossier (`drop_handler_qt.py`)

Point d'entrée unique : `handle_dropped_paths(parent, paths, load_files_callback, batch_callbacks, from_drop)`.
- Sépare `paths` en `dirs`/`files`. Si mélange dossiers+fichiers → message d'erreur, rien ne se passe.
- Si un ou plusieurs dossiers → `_show_batch_drop_dialog(parent, dirs, batch_callbacks)`, qui affiche `BatchDropDialog` (choix radio parmi les 8 traitements) puis, une fois choisi, scanne `dirs` (tous les dossiers droppés en une passe, récursivement) et appelle le flux correspondant avec `directories=dirs` (pour l'affichage des liens dans le résumé — plusieurs dossiers possibles ici, contrairement au menu qui n'en a qu'un).
- Si des fichiers seuls (pas de dossier) → passe directement à `load_files_callback` (ouverture normale, pas de batch).

**Différence clé menu vs drop** : le menu ouvre un seul dossier choisi via dialogue (`directory` singulier, `directories=None`), le drop peut agrafer plusieurs dossiers déposés simultanément (`directories=[...]`, affichés comme liens multiples dans le résumé via `_add_dir_links`).

## Le dict `batch_callbacks` — contrat obligatoire

Toute fonction `batch_convert_*`/`batch_recompress_cbz_confirm`/`show_batch_metadata_dialog` reçoit un dict `callbacks` fourni par `PanelWidget._get_batch_callbacks()` (`panel_widget.py:781`) :

```python
{
    "natural_sort_key":          lambda s: ...,   # tri naturel des noms de fichiers
    "create_centered_thumbnail": ...,              # génère la vignette PIL affichée en progression
    "safe_delete_file":          self._safe_delete_file,   # suppression (corbeille sauf permanent_delete)
    "get_mosaicview_temp_dir":   self._get_temp_dir,        # dossier pour écrire les logs d'erreurs
    "compute_auto_multipliers":  ...,   # renumérotation auto (utilisé par le flux PDF, voir skill renumbering)
    "generate_auto_filenames":   ...,
    "state":                     self._state,   # AppState du panneau (utilisé par le flux PDF pour renumber_mode)
}
```

Si tu ajoutes un nouveau flux batch qui a besoin d'une info supplémentaire (ex. un nouveau callback), l'ajouter à ce dict central plutôt que de le passer en paramètre séparé — tous les appelants (menu ET drop) passent par cette même fonction.

## Anatomie d'un flux de conversion (CBR→CBZ pris comme référence, `batch_dialogs_qt.py:715-981`)

Chaque flux (CBR, CB7, CBT, PDF, IMG, recompress) suit exactement la même mécanique en 4 étapes. **Si tu modifies un flux, vérifie si le même bug/besoin existe dans les 5 autres** — c'est du code dupliqué volontairement (voir section "Pourquoi pas de factorisation").

1. **Point d'entrée public** `batch_convert_XXX_to_cbz(parent, callbacks, directory=None)` : si `directory` est `None`, ouvre `QFileDialog.getExistingDirectory` ; sinon utilise le paramètre (cas drop, dossier déjà connu). Scanne récursivement (`os.walk`) tous les fichiers de l'extension concernée, trie par `natural_sort_key`. Si aucun fichier trouvé → `InfoDialog`. Sinon → `_confirm`.

2. **`batch_convert_XXX_to_cbz_confirm(parent, files, directory, callbacks, directories=None)`** : affiche `_ConfirmDialog` (nombre de fichiers, taille totale, checkbox "suppression permanente" — sauf recompress qui n'en a pas puisqu'il ne supprime rien). Si confirmé → `_run_XXX_conversion`.

3. **`_run_XXX_conversion(...)`** : ouvre `_ProgressDialog` (vignette + nom fichier + barre de pages optionnelle + progression globale), lance un **`threading.Thread` daemon** qui fait le travail réel, communique avec l'UI via `_ThreadSignals` (QObject avec des `Signal`, jamais d'accès direct aux widgets depuis le thread). À la fin, `signals.conversion_done.emit()` déclenche `on_done()` (dans le thread UI) qui ferme la progression, écrit un log si erreurs/renommages, puis affiche le résumé.

4. **`_XXxSummaryDialog`** : affiche le compte de conversions réussies, les compteurs de renommage (voir ci-dessous), un lien cliquable vers le log si erreurs.

### Détection du vrai format avant conversion (`detect_archive_type`)

Avant de traiter un fichier `.cbr`/`.cb7`/`.cbt` comme "vraiment RAR/7z/TAR", chaque flux appelle `detect_archive_type(path)` (magic bytes, `archive_type_detector.py`) — un fichier mal nommé (ex. un vrai ZIP renommé `.cbr` par erreur) est **simplement renommé** vers la bonne extension (`.cbz`/`.cb7`/`.cbt`) au lieu d'être "converti", et compté séparément (`renamed_cbz`/`renamed_cb7`/`renamed_cbt` selon le flux). Le résumé affiche ces compteurs seulement s'ils sont > 0. Un format totalement inconnu → erreur loggée, fichier ignoré.

### Registre anti-GC (`_active_batches`)

`_register_batch(prog, signals)` / `_unregister_batch(prog)` (haut de `batch_dialogs_qt.py`) maintiennent une référence Python vivante sur `prog`/`signals` pendant que le thread tourne — sans ça, le garbage collector pourrait détruire ces objets avant la fin du thread (crash silencieux). **Ne jamais retirer cet enregistrement** dans un nouveau flux ; toujours appeler `_register_batch` juste après avoir créé `_ProgressDialog`+`_ThreadSignals`, et `_unregister_batch` dans `on_done()`.

### Écriture du CBZ de sortie

Tous les flux (sauf IMG qui utilise `ZIP_STORED` fixe, car une image seule ne bénéficie pas de la compression par lot) utilisent le réglage utilisateur via `zip_compression_kwargs(get_config_manager().get_zip_compression_level())` — voir skill `zip-compression`. Normalisation des modes couleur non standard (`CMYK`/`YCbCr`/`I`/`F` → `RGB`) avant écriture, avec `fmt_map` par extension. `gc.collect()` appelé tous les 20 pages pendant l'écriture pour limiter le pic mémoire sur les grosses archives.

### Collision de nom de sortie

Avant d'écrire le `.cbz` de destination, chaque flux vérifie `os.path.exists(cbz_path)` et ajoute un suffixe `" (N)"` incrémental jusqu'à trouver un nom libre — ne jamais écraser un fichier existant silencieusement.

## Spécificités par flux

Résumé condensé ci-dessous — chaque flux a désormais son propre skill détaillé (calcul exact, pièges, sections "comment étendre" dédiées) ; lien direct depuis chaque puce.

- **CBR→CBZ** (`batch_convert_cbr_to_cbz`, ligne 715) : nécessite `rarfile` (sinon `ErrorDialog` immédiat, pas de scan). Vignette lue via `rarfile.RarFile`. Détail complet : skill `batch-cbr-convert`.
- **CB7→CBZ** (ligne 1110) : vignette/lecture via `_list_7z_files`/`_read_7z_file` (`archive_loader.py`, wrapper autour du binaire `7zip/`). Détail complet : skill `batch-cb7-convert`.
- **CBT→CBZ** (ligne 1502) : utilise `tarfile` stdlib, pas de dépendance externe. Détail complet : skill `batch-cbt-convert`.
- **PDF→CBZ** (ligne 1774) : nécessite `PDF_AVAILABLE` (import `fitz`/PyMuPDF réussi). Ne travaille **pas** en thread `threading.Thread` classique comme les autres — communique avec un **process dédié au batch** (`modules/qt/pdf_loading_qt.py`, `_spawn_pdf_process`/`_kill_pdf_process`, protocole de messages `batch_open`/`batch_ready`/`batch_convert`/`batch_page`/`done`/`error`/`password_error`), créé une fois pour toute la durée du lot puis détruit à la fin — jamais partagé avec un panneau ni un autre batch, voir skill `pdf-loading` pour l'architecture PDF complète. Gère la renumérotation auto des noms de page selon `state.renumber_mode` (skill `renumbering`) et les PDF protégés par owner-password (propose un déverrouillage après le résumé via `pdf_unlock_qt.py`). Détail complet : skill `batch-pdf-convert`.
- **IMG→CBZ** (ligne 2184) : seul flux avec un choix préalable de **mode** (`_ImgModeDialog` : une image = un CBZ, ou toutes les images du dossier = un seul CBZ multi-pages). Rejette les ICO et les images multi-frames (GIF animé, etc. — `n_frames > 1`) avec une erreur explicite plutôt qu'une conversion silencieusement fausse. Détail complet : skill `batch-img-convert`.
- **Recompression CBZ** (`batch_recompress_cbz_confirm`, ligne 2669) : seul flux qui ne supprime ni ne convertit un format différent — relit chaque CBZ et le réécrit avec `zip_compression_kwargs(level)` courant. Pas de checkbox suppression (rien à supprimer). Skip les archives déjà au niveau optimal (`_detect_zip_compression_state`) pour éviter un travail inutile. Renomme aussi les fichiers mal étiquetés (extension ≠ `.cbz` mais contenu réellement ZIP), comme les autres flux. Détail complet : skill `batch-recompress`.
- **Import métadonnées ComicVine** (`batch_metadata_dialog_qt.py`) : seul flux qui n'écrit pas en arrière-plan silencieux — ouvre la **fenêtre ComicVine interactive** (`comicvine_dialog_qt.py`, voir skill `comicvine-metadata-fetch` pour le détail du scraping réseau/wizard) séquentiellement pour chaque fichier, avec caches de recherche partagés entre fichiers (`_search_cache`/`_issues_cache`) pour éviter de re-interroger l'API pour une série déjà vue dans le lot. `_BatchMetadataOrchestrator` (ligne 225) pilote la séquence : charge un `AppState` allégé du fichier courant dans un `QThread` (`_LoadWorker`, via `parse_comic_info_xml` — voir skill `comicinfo-metadata-editor`), affiche/actualise la fenêtre ComicVine (au lieu d'en recréer une à chaque fichier), sauvegarde en `QThread` (`_SaveWorker`, via `write_comic_metadata_from_scraper` — voir skill `comicinfo-metadata-editor`) à la validation, avance au suivant. Option "ignorer les fichiers ayant déjà un ComicInfo.xml" (`skip_existing`). Bouton Annuler pendant le traitement → `_CancelConfirmDialog` (continuer/arrêter), arrêt propre qui garde acquis les fichiers déjà traités. Détail complet : skill `batch-metadata-import`.
- **Bibliothèque** (`_make_batch_library`, `drop_handler_qt.py`) : le plus atypique des 8 — pas de scan, pas de confirmation/progression/résumé propres, ouvre simplement `LibraryWindow` + `NewDbDialog` pré-rempli (`preset_dir`) puis ajoute les dossiers supplémentaires après validation. Seul accessible par drop, aucune entrée de menu. Détail complet : skill `batch-library-create`.

## Comment ajouter un nouveau flux de conversion batch (ex. XYZ→CBZ)

1. Dans `batch_dialogs_qt.py`, copier intégralement la structure du flux le plus proche (CBT est le plus simple, pas de dépendance externe) :
   - `_XyzSummaryDialog` (classe résumé — copier `_CbtSummaryDialog`, adapter les clés de traduction `dialogs.batch_xyz.*` et les compteurs de renommage pertinents).
   - `batch_convert_xyz_to_cbz(parent, callbacks, directory=None)` — scan + garde "aucun fichier".
   - `batch_convert_xyz_to_cbz_confirm(parent, files, directory, callbacks, directories=None)` — `_ConfirmDialog` + branchement vers `_run_xyz_conversion`.
   - `_run_xyz_conversion(...)` — `_ProgressDialog` + `_ThreadSignals` + `_register_batch`/`_unregister_batch` + thread + écriture CBZ + log + résumé.
2. Ajouter toutes les clés de traduction `dialogs.batch_xyz.*` dans **toutes** les langues (`locales/*.json`) — voir skill `add-translation`. Clés minimales à couvrir (calquer sur `batch_cbt`) : `select_directory_title`, `no_xyz_title`/`no_xyz_message`, `confirm_title`/`confirm_message`, `checkbox_permanent_delete`, `start_button`, `converting_title`, `converting_progress`, `page_progress` (si barre de pages), `complete_title`, `complete_message`/`complete_message_errors`, `errors_count`.
3. Dans `panel_widget.py` : ajouter `_get_batch_callbacks()`-compatible méthode `_batch_convert_xyz_to_cbz(self)` sur le modèle de `_batch_convert_cbt_to_cbz` (ligne 801).
4. Dans `menubar_callbacks_qt.py` : ajouter l'entrée dans le dict de callbacks (`"batch_convert_xyz_to_cbz": mw._batch_convert_xyz_to_cbz`).
5. Dans `menubar_qt.py` : ajouter `_add_action(batch_menu, _("menu.batch_xyz_to_cbz"), callbacks.get("batch_convert_xyz_to_cbz"))` près des 5 autres (ligne ~99-103).
6. Dans `batch_drop_dialog_qt.py` (`BatchDropDialog`) : ajouter un `QRadioButton` (`self._radio_xyz`), l'enregistrer dans `self._btn_group.addButton(self._radio_xyz, N)` avec le bon index, l'ajouter au layout, au tuple de résolution dans `_on_ok` (`self.chosen = (...)[checked]`), et à `_retranslate()`.
7. Dans `drop_handler_qt.py` (`_show_batch_drop_dialog`) : ajouter une fonction `_make_batch_xyz()` sur le modèle de `_make_batch_cbt` (scan récursif sur `dirs`, garde "aucun fichier", appel à `batch_convert_xyz_to_cbz_confirm(parent, files, dirs[0], batch_callbacks, directories=dirs)`), puis l'ajouter au dict `callbacks` final avec la clé `'batch_xyz'` (doit correspondre à `"batch_" + dlg.chosen` résolu dans `show_batch_drop_dialog`).
8. Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour toute nouvelle fenêtre (`_XyzSummaryDialog` notamment) : non-modale, thème, langue à la volée, `_wt()` pour le titre, centrage sur le panneau source.

## Pourquoi pas de factorisation malgré la duplication massive

Les 5 flux de conversion (CBR/CB7/CB7/CBT/PDF/IMG) partagent ~80% de code identique (structure confirm/progress/summary/thread/registre anti-GC). C'est un choix assumé du projet existant, pas un oubli — ne pas lancer de refactoring de factorisation sans demande explicite de l'utilisateur (règle CLAUDE.md : ne jamais modifier hors du périmètre demandé). Si une demande porte sur "corriger un bug dans le flux CBR", corriger uniquement CBR sauf instruction contraire, même si le même bug existe ailleurs — le signaler et attendre l'accord.

## Pièges connus

- **Callback manquant dans `_get_batch_callbacks()`** : un nouveau flux qui a besoin d'une info non présente dans le dict actuel doit l'y ajouter en un seul endroit (`panel_widget.py:781`) — ne jamais créer un chemin d'appel parallèle qui contourne cette fonction, sinon le menu et le drop divergent silencieusement.
- **Oublier `_register_batch`/`_unregister_batch`** : crash aléatoire et difficile à reproduire (le thread accède à un `QObject` déjà détruit par le GC). Toujours les deux, jamais l'un sans l'autre.
- **Écrire dans l'UI depuis le thread `do_conversion`** : toute mise à jour visuelle doit passer par `signals.update_*.emit(...)`, jamais un appel direct à une méthode de `_ProgressDialog` depuis le thread — violerait le threading Qt (widgets non thread-safe).
- **`directory` (singulier) vs `directories` (liste)** : le menu passe toujours `directories=None` (un seul dossier choisi par dialogue) ; le drop passe `directories=dirs` (plusieurs dossiers déposés). `_add_dir_links` dans les fenêtres de résumé gère les deux cas — ne pas supposer qu'un seul dossier est toujours affiché.
- **PDF n'utilise pas `threading.Thread` de la même façon que les autres** — c'est un process séparé avec un protocole de messages, pas un thread avec accès direct au système de fichiers dans la boucle. Chaque appelant (panneau, batch, import métadonnées) doit créer et détruire son propre process via `_spawn_pdf_process`/`_kill_pdf_process` — ne jamais réutiliser le process d'un autre appelant, sous peine de "pipe broken" si deux chargements PDF tournent en même temps (voir skill `pdf-loading`). Ne pas copier le pattern PDF pour un nouveau flux simple ; copier plutôt CBT.
- **`_BatchMetadataOrchestrator` garde une référence module-level** (`_active_orchestrators`) le temps du batch — même piège anti-GC que `_register_batch`, mécanisme séparé car ce flux n'utilise pas `_ThreadSignals`/`_ProgressDialog`.

## Références croisées

Les 8 traitements ont chacun leur skill dédié, à consulter pour le détail exact (lignes de code, pièges propres, sections "comment étendre") plutôt que de dupliquer ici :

- `batch-cbr-convert` / `batch-cb7-convert` / `batch-cbt-convert` — les 3 flux "archive classique" quasi identiques en structure (résumé/log à 3 compteurs de renommage), qui ne diffèrent que par leur bibliothèque de lecture (`rarfile`/binaire `7z.exe`/`tarfile` stdlib). CBT est le modèle recommandé pour un nouveau flux simple.
- `batch-pdf-convert` — le flux le plus complexe, process séparé dédié au batch (voir skill `pdf-loading`), détection DPI intelligente, renumérotation auto, déverrouillage owner-password.
- `batch-img-convert` — seul flux avec un choix de mode préalable et une validation stricte par image (rejet ICO/multi-frame), seul à ne pas utiliser le réglage de compression utilisateur.
- `batch-recompress` — seul flux sans conversion de format ni suppression, fenêtre de confirmation dédiée (pas `_ConfirmDialog`).
- `batch-metadata-import` — seul flux interactif (pas de traitement silencieux), orchestrateur piloté par événements plutôt qu'une boucle synchrone, state allégé par fichier.
- `batch-library-create` — le plus atypique des 8, aucune des briques communes (scan/confirm/progress/résumé), accessible uniquement par drop.
- `pdf-loading` — architecture des process PDF dédiés (un par chargement, jamais partagé), utilisée par le flux PDF→CBZ ci-dessus et par `batch-metadata-import` pour écrire un CBZ à partir d'un PDF.
- `zip-compression` — réglage de compression utilisé par 6 des 8 flux (tous sauf IMG et Bibliothèque).
- `temp-files` — emplacement des logs d'erreurs/renommages écrits par la plupart des flux.
- `library` — `LibraryWindow`/`NewDbDialog`, réutilisées telles quelles par `batch-library-create`.
