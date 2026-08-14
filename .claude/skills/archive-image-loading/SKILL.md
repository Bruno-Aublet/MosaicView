---
name: archive-image-loading
description: Localiser ou modifier le chargement d'archives (CBZ/CBR/CB7/CBT/EPUB) et d'images isolées dans MosaicView. Utiliser dès qu'une tâche touche à archive_loader.py, entries.py (create_entry), PanelWidget._load_files, ou import_merge_qt.py.
---

# Chargement d'archives et d'images isolées — MosaicView

Point d'entrée de **toute** donnée qui finit dans `state.images_data` (donc dans la mosaïque) : ouverture d'une archive CBZ/CBR/CB7/CBT/EPUB, ajout d'images isolées, fusion d'une archive dans une session déjà ouverte. Ne couvre pas les formats qui ont leur propre pipeline séparé (PDF — voir skill `pdf-loading`, seul format jamais ouvert dans le process Qt principal — et l'import ComicVine/batch qui réutilise ces mécanismes sans les redéfinir).

## Fichiers clés

- **`modules/qt/archive_type_detector.py`** — `detect_archive_type(filepath)` : détection du **format réel** par magic bytes (pas par extension). Fonction pure, ~60 lignes, aucune dépendance Qt.
- **`modules/qt/archive_loader.py`** (~1120 lignes) — cœur du chargement d'archives : `LoadWorker` (QThread), `ArchiveLoader` (orchestrateur QObject côté UI), dialogues associés (`ExtensionCorrectionDialog`, `_CorruptedImagesDialog`, `_MessageDialog`). Contient aussi les wrappers du binaire 7-Zip embarqué (`_get_7z_exe`, `_list_7z_files`, `_read_7z_file`) puisque `zipfile`/`rarfile`/`tarfile` stdlib ne couvrent pas le 7z.
- **`modules/qt/entries.py`** — `create_entry()` : construit une **entrée** de `images_data` à partir de bytes bruts (décodage PIL, détection GIF animé, hash MD5, dimensions...) — le point de passage obligé, qu'on charge depuis une archive, un fichier isolé, ou un TIFF multi-pages. Aussi : `create_entry_from_file()`, `create_entries_from_tiff()`, et le lazy-loading (`ensure_image_loaded`/`free_image_memory`/`get_gif_frame`).
- **`modules/qt/panel_widget.py`** — `_ImageLoadWorker` (QThread pour les images isolées, classe locale à ce fichier), `PanelWidget._load_files()` (routeur central : dispatch par extension vers archive/PDF/image), `_start_image_load()` (câblage UI du worker d'images).
- **`modules/qt/import_merge_qt.py`** — `ImportMergeWorker`/`import_and_merge_archive()` : fusionne une **deuxième archive** dans la session déjà ouverte (pas un nouveau chargement qui réinitialise `images_data`) — réutilise `create_entry`, `_list_7z_files`/`_read_7z_file` du loader.
- **`modules/qt/mosaic_canvas.py`** — `build_qimage_for_entry()` : appelé juste après `create_entry()` par tous les chemins de chargement pour préconstruire le cache vignette en arrière-plan (voir skill `mosaic-thumbnails`).
- **`modules/qt/comic_info.py`** — `read_comic_info()` : lit `ComicInfo.xml` directement depuis le fichier archive sur disque, appelé par `ArchiveLoader._on_finished` juste après le chargement (voir skill `comicinfo-metadata-editor`).

## Vue d'ensemble — trois familles de chargement

| Famille | Déclencheur | Réinitialise `images_data` ? | Classe worker |
|---|---|---|---|
| **Ouverture d'archive(s)** | Premier fichier CBZ/CBR/CB7/CBT/EPUB ouvert dans un panneau vide | Oui | `LoadWorker` (`archive_loader.py`) |
| **Fusion d'archive** | Glisser/ouvrir une archive alors qu'un comic est déjà affiché | Non — ajoute à la suite | `ImportMergeWorker` (`import_merge_qt.py`) |
| **Ajout d'image(s) isolée(s)** | Fichiers image/`.nfo`/`.txt`/`.xml` ouverts ou droppés (panneau vide **ou** déjà ouvert) | Non si déjà ouvert, sinon initialise | `_ImageLoadWorker` (`panel_widget.py`) |

Le **PDF** est une quatrième famille, avec son propre process séparé préchauffé — hors périmètre de ce skill, voir mémoire `project_pdf_support.md` et skill `batch-processing` (section flux PDF→CBZ) pour l'architecture complète.

## Le routeur central — `PanelWidget._load_files()` (`panel_widget.py:1079`)

Point d'entrée unique quel que soit l'appelant (ouverture menu, drag & drop, fichier récent, Bibliothèque) :

1. Sépare `paths` par extension : `cbz_files` (`.cbz`/`.cbr`/`.cb7`/`.cbt`/`.epub`), `pdf_files` (`.pdf`), `image_files` (liste `IMAGE_EXTS`), `annex_files` (`.nfo`/`.txt`/`.xml`), `other_files` (reste → avertissement "fichiers non supportés", rien n'est chargé pour eux).
2. `already_open = st.current_file is not None or bool(st.images_data)` — **la variable qui décide tout le comportement** : premier chargement (réinitialise) vs ajout dans une session existante (fusionne).
3. PDF présent : si déjà ouvert → dialogue de choix de DPI puis `import_and_merge_pdf` ; sinon → `reset_history` + `self._pdf_loader.load(...)`.
4. Archives présentes : si déjà ouvert → **une fusion par fichier**, dans l'ordre de tri naturel, via `_import_merge_archive()` (donc `N` fusions séquentielles si `N` archives droppées en même temps) ; sinon → `reset_history` + `self._loader.load(cbz_sorted)` (un seul chargement multi-archives, voir `load_multiple_archives`).
5. Images/annexes présentes (peuvent coexister avec une archive/un PDF dans le même appel) : `_start_image_load(...)`.

**Piège** : PDF, CBZ et images peuvent tous être présents dans le même appel à `_load_files` (ex. drop mixte) — les trois branches s'exécutent, pas de `elif` entre PDF et CBZ pour les images (`if image_files or annex_files:` est un bloc séparé, toujours évalué). Vérifier ce comportement avant de le changer si une tâche touche à l'ordre de priorité entre types de fichiers.

## Chargement d'archive — `ArchiveLoader` / `LoadWorker`

### `ArchiveLoader` (QObject, un par panneau, construit dans `PanelWidget.__init__`)

Orchestre le worker et l'affichage pendant le chargement :
- `load(filepaths)` : **réinitialise entièrement `state`** (`images_data=[]`, `selected_indices.clear()`, `comic_metadata=None`, `merge_counter=0`, etc.) avant même de lancer le thread — un appel à `ArchiveLoader.load()` n'est **jamais** une fusion, contrairement à `import_merge_qt`.
- Affiche un texte rouge centré sur le canvas avec pourcentage (`_show_loading_text`, réutilise `canvas_overlay_qt.show_canvas_text` — pas de `QProgressDialog`, comportement hérité de la version tkinter ; voir skill `canvas-overlay-progress` pour le détail complet de ce mécanisme central) + un lien "Annuler" cliquable (réutilise `web_import_qt._show_cancel_item`).
- `cancel()` : marque `_cancelled` sur le worker, détache les slots, **garde une référence dans `_orphan_workers`** (liste module-level) jusqu'à ce que le thread se termine réellement — même famille de piège que `_park_running_worker` documenté dans le skill `comicvine-metadata-fetch` (ne jamais détruire un `QThread` encore actif).
- `_on_finished(images_data, errors, first_filepath)` : assigne `images_data`/`all_entries`, détecte l'état de compression ZIP (`_detect_zip_compression_state`), lit `ComicInfo.xml` (`read_comic_info` + `build_page_attrs_map`, voir skill `comicinfo-metadata-editor`), déclenche `render_mosaic()`, `loading_finished.emit()`, puis la détection d'images corrompues (`_detect_corrupted_qt(self._win, self._state)`, appel avec `self._state` en paramètre depuis le 2026-08-14 — ne jamais l'appeler sans, voir "Pièges connus") et un résumé d'erreurs non bloquant s'il y en a.

### `LoadWorker` (QThread)

Fait le vrai travail I/O, jamais d'accès direct aux widgets Qt depuis `run()` (uniquement des `Signal`) :

- **`detect_archive_type(filepath)`** appelé en premier, **avant** de faire confiance à l'extension — un fichier mal nommé (ZIP renommé `.cbr` par erreur) est détecté ici.
- Si le type détecté diffère de l'extension déclarée (`_resolve_filepath`) : `need_ext_dialog` émis, le thread se **bloque** sur un `threading.Event` (`_ask_ext`) jusqu'à ce que `ExtensionCorrectionDialog` (affiché non-modal côté thread UI) rappelle `set_ext_result()` — le thread worker attend, mais **le thread UI n'est jamais bloqué** : l'utilisateur peut continuer à agir sur l'autre panneau pendant ce temps. Trois choix : `rename` (renomme le fichier sur disque vers la bonne extension), `keep` (charge quand même sous le nom déclaré), `None`/annuler (abandonne tout le chargement).
- **`load_archive()`** (un seul fichier) vs **`load_multiple_archives()`** (plusieurs fichiers droppés/ouverts ensemble) : la version multi-fichiers fait tout en 2 phases (détection+namelists puis lecture réelle avec une seule barre de progression globale sur le total de pages tous fichiers confondus), et **préfixe `NEW-`** les noms de toutes les archives sauf la première (`add_prefix = archive_idx > 0`) + `entry["source_archive"]` — pour distinguer visuellement/techniquement quelle archive d'origine a fourni quelle page quand plusieurs sont ouvertes d'un coup.
- Chaque format d'archive (ZIP/EPUB, RAR, 7z, TAR) a sa propre branche de lecture dans `_read_entries`/`load_archive`/`load_multiple_archives` — **code dupliqué volontairement entre ces 3 méthodes**, comme documenté pour les flux batch (skill `batch-processing`) : ne pas factoriser sans demande explicite.
- `build_qimage_for_entry(entry)` appelé pour **chaque** entrée juste après `create_entry()` — précalcule le cache vignette (`qt_qimage_large`) en arrière-plan pendant le chargement, avant même que la mosaïque soit rendue (voir skill `mosaic-thumbnails`, section cache pixmap).

## Formats d'archive supportés — détection et lecture

| Format | Détection (magic bytes) | Lecture | Dépendance |
|---|---|---|---|
| CBZ | `PK\x03\x04`/`PK\x05\x06` (ZIP), sans mimetype EPUB | `zipfile` stdlib | aucune |
| EPUB | ZIP + `mimetype` = `application/epub+zip` | `zipfile`, filtré aux seules images (`IMAGE_EXTS`) | aucune |
| CBR | `Rar!\x1a\x07\x00`/`...\x01\x00` (RAR4/RAR5) | `rarfile` | paquet `rarfile` (+ UnRAR externe) |
| CB7 | `7z\xbc\xaf\x27\x1c` | binaire `7zip/7z.exe` embarqué via `subprocess` (`_list_7z_files`/`_read_7z_file`) | binaire embarqué, pas de lib Python |
| CBT | pas de magic bytes fixes — `tarfile.is_tarfile()` en dernier recours | `tarfile` stdlib (`r:*`, gère nu/gzip/bzip2) | aucune |

**EPUB n'est pas listé comme un "vrai" format cible du projet** (pas d'icône ni de mention dans le nom "CBZ/CBR/CB7/PDF" du CLAUDE.md) mais **est bien supporté en lecture** — traité comme un ZIP filtré aux images, hérité du scraper `comic-vine-scraper` d'origine. Vérifier ce point avant de supposer qu'EPUB est absent du projet.

**CB7 est le seul format sans bibliothèque Python** : toute la lecture passe par un appel process (`subprocess.run`, `CREATE_NO_WINDOW`) au binaire `7zip/7z.exe` embarqué — voir skill `check-embedded-versions` pour la maintenance de ce binaire. `_to_short_path()` convertit le chemin en 8.3 avant l'appel (contournement d'un problème d'unicode avec l'exécutable 7z sur certains profils Windows).

## `create_entry()` — le point de passage obligé (`entries.py:154`)

Toute donnée binaire qui devient une entrée de `images_data`, quelle que soit sa provenance (archive, fichier isolé, TIFF multi-page, collage/merge), passe par cette fonction :

1. Détermine `is_image` (extension dans `image_exts`) et `is_dir` (`file.endswith("/")`).
2. Si image : décodage PIL en 2 passes (`img.verify()` puis réouverture + `img.load()`, nécessaire car `verify()` invalide l'objet pour un usage ultérieur) — capture `DecompressionBombError` séparément (image bombe : marque `is_too_large=True`, `is_corrupted=True`, garde les dimensions détectées) d'une corruption normale (`is_corrupted=True`, `is_too_large=False`, `corruption_reason=str(e)`). Voir skill `corrupted-images` pour le signalement visuel, le tooltip dédié et le mécanisme de remplacement, ainsi que le second point de détection tardif dans `ensure_image_loaded()`.
3. Stocke `img_width`/`img_height`/`dpi` immédiatement (évite de rouvrir l'image plus tard, ex. pour la renumérotation — voir skill `renumbering`, `page_detection.py` qui lit ces mêmes clés).
4. **GIF animé** : détecté avant de garder l'image en mémoire (`n_frames > 1`), stocke uniquement les métadonnées légères (durées par frame, loop, disposal) — **pas** toutes les frames décodées (lazy loading, voir `get_gif_frame()` pour le chargement à la demande d'une frame). Voir skill `animated-gif` pour la création/édition d'un GIF animé (consomme ces mêmes métadonnées) et skill `viewers` pour la lecture animée dans la visionneuse principale.
5. **`entry["img"]` reste `None` après `create_entry`** (lazy loading systématique) — `ensure_image_loaded(entry)` le décode à la demande plus tard, `free_image_memory(entry)` le libère après usage tout en gardant `entry["bytes"]`. Ne jamais supposer que `entry["img"]` est peuplé juste après un chargement.
6. Calcule `entry["_hash"]` (MD5) si image non corrompue — voir skill `duplicate-detection`, ce hash est le même que celui consommé par `recompute_duplicate_groups`.

**`create_entry_from_file(filepath, image_exts)`** — variante pour un fichier **sur disque** (pas depuis une archive ouverte) : limite 500 Mo (`FileTooLargeError`), lit tous les bytes puis délègue à `create_entry`. Utilisée par `_ImageLoadWorker` pour les images isolées.

**`create_entries_from_tiff(filepath, image_exts, add_prefix=False)`** — cas particulier : un seul fichier TIFF peut contenir **plusieurs pages**, chacune devenant une entrée séparée (`_page_0001.jpg`, etc., toujours reconverties en JPEG). Double implémentation : `tifffile` (si installé, gère aussi les SubIFDs) en priorité, sinon fallback PIL (`TiffImagePlugin` puis `ImageSequence.Iterator` en dernier recours). Un TIFF à une seule page réelle est renommé sans le suffixe `_page_0001`.

## Chargement d'images isolées — `_ImageLoadWorker` / `_start_image_load` (`panel_widget.py`)

Distinct du chargement d'archive : pas de réinitialisation systématique de `images_data`, peut s'ajouter à une session déjà ouverte.

- `_ImageLoadWorker.run()` : pour chaque fichier, si `.tiff`/`.tif` → `create_entries_from_tiff` (peut produire plusieurs entrées) ; sinon → `create_entry_from_file` (une entrée). Toutes reçoivent `entry["source_archive"] = "loose"` (pas de fichier archive d'origine) et, si `already_open`, un préfixe `NEW-` sur le nom (même convention que les archives fusionnées après la première) — **sauf** si le worker a été construit avec `existing_names` (set de noms déjà présents, passé uniquement par le coller Ctrl+V, voir skill `clipboard`) : dans ce cas, une vraie collision de nom avec `existing_names` est résolue par un suffixe `-COPY`/`-COPYn` au lieu du préfixe `NEW-`, et une absence de collision ne change pas le nom du tout (pas de préfixe ajouté). `_start_image_load()` accepte ce même paramètre `existing_names` et le transmet tel quel au worker ; `PanelWidget._load_files()` ne le calcule que si appelé avec `rename_collisions_as_copy=True`.
- `_start_image_load()` (câblage UI, `panel_widget.py:973`) : annule un éventuel worker précédent encore actif avant d'en lancer un nouveau (pas de `_park_running_worker` ici — la simple annulation suffit car un seul `_ImageLoadWorker` par panneau à la fois, contrairement au cas ComicVine avec plusieurs workers concurrents possibles). Même pattern d'overlay canvas + bouton Annuler que `ArchiveLoader`.
- À la fin (`on_finished`) : les nouvelles entrées sont ajoutées à `images_data`, retriées par tri naturel (`_natural_sort_key`), `save_state_data` (point undo, voir skill `apply-image-operation` pour le pattern général) — puis `render_mosaic()`.
- **`st.first_image_dir`** n'est renseigné que si le panneau était vide avant l'ajout (`not st.images_data` au moment du calcul) — sert à mémoriser le dossier d'origine pour un futur enregistrement en CBZ sans archive source.

## Fusion d'une archive existante — `import_merge_qt.py`

Distinct de `ArchiveLoader.load()` : ajoute le contenu d'une **deuxième** archive à la suite du comic déjà affiché, sans toucher aux pages existantes.

- `import_and_merge_archive(filepath, parent, canvas, state)` calcule un `merge_prefix` unique (`state.merge_counter`, incrémenté à chaque fusion) et lance `ImportMergeWorker`.
- Chaque entrée importée est préfixée par ce `merge_prefix` sur **le nom de fichier ET le chemin de sous-dossier** (`_add_prefix`, gère le cas `path/file.ext` en préfixant les deux segments séparément) — pas seulement `NEW-` comme pour les images isolées ou les archives multiples au premier chargement.
- Réutilise directement `create_entry`, `_list_7z_files`/`_read_7z_file`, `ExtensionCorrectionDialog` du loader — mêmes formats supportés, même détection de type, même dialogue de correction d'extension.
- Après fusion, la **renumérotation automatique** se déclenche selon `state.renumber_mode` (voir skill `renumbering`) — le préfixe `merge_prefix` garantit que les pages fusionnées ne collisionnent pas avec les noms existants avant que la renumérotation, si activée, ne les renomme proprement.

## Comment ça entre dans la mosaïque et les panneaux

Aucun de ces mécanismes ne connaît directement `MosaicCanvas` au-delà de l'appel final `render_mosaic()` — le contrat est entièrement au niveau de `state.images_data` (liste de dicts d'entrées) :

1. Un loader (quel qu'il soit) peuple/complète `state.images_data`.
2. Il appelle `self._canvas.render_mosaic()` (voir skill `mosaic-thumbnails`) qui reconstruit la scène depuis `images_data` — c'est le seul point de contact avec le rendu visuel.
3. Chaque `PanelWidget` (panel1/panel2 en split-view, voir skill `panels`) a sa **propre** instance d'`ArchiveLoader`/`_pdf_loader`, construite avec son propre `self._state` et son propre `self._canvas` — deux panneaux ne partagent jamais un chargement, un drop sur le panneau 2 ne touche jamais `images_data` du panneau 1.
4. La **Bibliothèque** (voir skill `library`) n'utilise **aucun** de ces workers pour son scan (elle lit directement via `read_comic_info`/`parse_comic_info_xml` sans construire d'`images_data`) mais délègue à `PanelWidget._load_files()` (donc à ce mécanisme) au moment où l'utilisateur choisit d'ouvrir un comic indexé dans un panneau réel.

## Comment étendre

- **Ajouter un nouveau format d'archive lisible** : (a) ajouter la détection magic bytes dans `archive_type_detector.py` (`detect_archive_type`), (b) ajouter l'extension à `_EXT_TO_TYPE`/`_TYPE_TO_EXT`/`_ERROR_KEYS` dans `archive_loader.py`, (c) ajouter une branche de lecture dans `_get_namelist`/`_read_entries`/`load_archive`/`load_multiple_archives` (4 endroits, suivre le pattern CBT — le plus simple, aucune dépendance externe), (d) ajouter l'extension à la liste `cbz_files` dans `PanelWidget._load_files()`, (e) si la fusion doit aussi le supporter, ajouter la branche correspondante dans `import_merge_qt.py`.
- **Changer la limite de taille de fichier image isolée** (500 Mo) : `FileTooLargeError`/constante `max_size` dans `entries.py`, dupliquée dans `create_entry_from_file` et `create_entries_from_tiff` — mettre à jour les deux.
- **Changer le comportement de correction d'extension** : `ExtensionCorrectionDialog` (`archive_loader.py`) est réutilisée telle quelle par `import_merge_qt.py` — toute modification de ses 3 choix (`rename`/`keep`/annuler) impacte les deux chemins.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour les dialogues de ce module (`ExtensionCorrectionDialog`, `_CorruptedImagesDialog`, `_MessageDialog` — déjà non-modaux, `_wt()` pour les titres déjà en place).

## Pièges connus

- **`ArchiveLoader.load()` réinitialise toujours `state`** — ne jamais l'utiliser pour un besoin de fusion ; c'est `import_merge_qt.py` qu'il faut appeler dans ce cas. `PanelWidget._load_files()` fait déjà cette distinction via `already_open`, ne pas la contourner dans un nouveau call-site.
- **Le thread `LoadWorker` peut se bloquer volontairement** sur `_ask_ext` en attendant une réponse UI — ce n'est pas un bug de threading, c'est le mécanisme voulu pour le dialogue de correction d'extension. Ne jamais court-circuiter ce blocage sans comprendre que le thread UI, lui, reste réactif.
- **`_orphan_workers`/`_cleanup_worker` doivent être respectés à l'identique pour tout nouveau worker de chargement** — un `QThread` détruit pendant qu'il tourne encore crashe silencieusement ou plus tard (même classe de bug que documenté dans `project_qthread_lifecycle.md`, mémoire projet).
- **`entry["img"]` est `None` après `create_entry`/tout chargement** — toujours passer par `ensure_image_loaded()` avant de lire une image PIL complète depuis une entrée fraîchement chargée, ne jamais supposer qu'elle est déjà décodée.
- **Le préfixe de nommage diffère selon le chemin** : `NEW-` (images isolées ajoutées à une session ouverte, ou archives 2..N d'un chargement multi-fichiers) vs `merge_prefix` unique incrémental (fusion explicite d'une archive via `import_merge_qt.py`) vs suffixe `-COPY`/`-COPYn` (uniquement le coller Ctrl+V d'une page, en cas de collision réelle avec un nom déjà présent — voir skill `clipboard`) — ne pas mélanger ces conventions en copiant un pattern de l'une vers l'autre sans vérifier laquelle s'applique.
- **CB7 dépend d'un binaire externe, pas d'une lib Python** — un changement de version de `7zip/7z.exe` peut changer le format de sortie de `7z l -ba -slt` parsé par `_list_7z_files`, à revérifier après une mise à jour du binaire (voir skill `check-embedded-versions`).
- **Piège corrigé (2026-08-14) — `_detect_corrupted_qt(win)` lisait le singleton global `_state_module.state` au lieu du state du panneau qui vient de charger l'archive** : `_on_finished` est le callback de fin de `LoadWorker` (QThread) — en split-view, si l'utilisateur interagit avec l'autre panneau pendant le chargement en arrière-plan, le singleton global (voir piège daté du même jour dans le skill `mosaic-thumbnails`) peut pointer sur le mauvais panneau au moment où le thread termine, et le dialogue "images corrompues" analyserait alors `images_data` du mauvais comic. Fix : `_detect_corrupted_qt(win, state=None)` accepte désormais un `state` explicite (fallback sur le global si omis), et son unique appelant (`_on_finished`, ligne ~1088) lui passe `self._state` — le state capturé par l'instance `ArchiveLoader` à sa construction, correct quel que soit le panneau actif au moment où le thread se termine.
