---
name: save-export
description: Localiser ou modifier la sauvegarde/export CBZ de MosaicView (Enregistrer sous, sélection en CBZ, export vers dossier, apply_new_names, chaîne de validation avant écriture). Utiliser dès qu'une tâche touche à file_operations_qt.py ou save_as_cbz.
---

# Sauvegarde et export CBZ — MosaicView

Toutes les opérations qui écrivent un fichier CBZ (ou exportent des pages en dehors d'une archive) sont regroupées dans un seul fichier : `modules/qt/file_operations_qt.py` (1626 lignes). Six méthodes de sauvegarde, une chaîne de validation asynchrone partagée, et une dizaine de dialogues non-modaux dédiés.

## Les 5 fonctions publiques (6 méthodes historiques)

| Fonction | Déclenchée quand | Résultat |
|---|---|---|
| `save_as_cbz(parent, canvas, callbacks)` | Archive `.cbz` déjà ouverte, "Enregistrer sous" | Nouveau fichier CBZ, ancien optionnellement supprimé |
| `save_selection_as_cbz(parent, callbacks)` | Sélection non vide, "Enregistrer la sélection en CBZ" | Nouveau CBZ contenant uniquement les entrées sélectionnées |
| `save_selection_to_folder(parent, callbacks)` | Sélection non vide, "Exporter la sélection" | Fichier unique (1 sélection) ou dossier (plusieurs), écriture brute des bytes |
| `create_cbz_from_images(parent, canvas, callbacks)` | Pas d'archive ouverte, images chargées, "Créer une archive CBZ" | Nouveau CBZ à partir d'images isolées |
| `apply_new_names(parent, canvas, callbacks, on_complete)` | Bouton "Appliquer" après renommage/modification | Écrase le `.cbz` existant, **ou** convertit CBR/CB7/CBT/EPUB/PDF → nouveau `.cbz` (2 branches = les méthodes historiques 5 et 6) |

Tous les points d'entrée sont câblés dans `menubar_callbacks_qt.py:60-64` vers des méthodes de `MainWindow` (`mw._save_as_cbz` etc., dans `panel_widget.py`), elles-mêmes appelées depuis la barre de menu, la colonne d'icônes et/ou le menu contextuel selon la fonction (voir chaque skill dédié pour le détail des 3 points d'entrée — non recensés ici, ce fichier ne définit que la logique métier).

## Vérifications pré-sauvegarde communes (`_run_validation_chain`)

`save_as_cbz`, `create_cbz_from_images` et `apply_new_names` (première moitié) enchaînent la même série de validations **non modales**, chacune avec un callback `on_done(bool)` :

1. **`_validate_filenames_qt`** — cherche des caractères interdits (`<>:"|?*`), des caractères de contrôle, du path traversal (`..`) dans `orig_name`. Détecte automatiquement si l'archive a une **structure de sous-dossiers légitime** (`has_subdirectory_structure` — un `/` dans `orig_name` d'un fichier qui n'est pas une entrée `is_dir`) : si oui, `/` n'est pas traité comme un caractère interdit. Propose une correction automatique via `QuestionYNCDialog` (remplace les caractères invalides par `_`) — Oui/Non/Annuler, pas juste Oui/Non.
2. **`_check_animated_gifs_qt`** — avertit si la mosaïque contient un GIF animé (`entry["is_animated_gif"]`), car la sauvegarde CBZ fige chaque GIF sur sa frame courante (voir skill `animated-gif`). `ConfirmDialog` Oui/Non.
3. **`_handle_duplicate_filenames_qt`** — détecte les doublons de noms via `detect_duplicate_filenames_for_save` (`dialogs_qt.py`), propose `DuplicateFilenameDialog` avec 3 choix : **renuméroter** (appelle `callbacks['renumber_btn_action']`, voir skill `renumbering`), **ignorer et sauvegarder quand même**, ou **annuler**.

`_run_validation_chain(steps, on_all_passed)` exécute les steps en séquence (pas en parallèle) et s'arrête au premier échec — remplace une cascade de `if not X(): return` synchrones, impossible ici puisque chaque validation ouvre potentiellement une fenêtre non-modale et doit attendre la réponse de l'utilisateur avant de continuer (voir règle CLAUDE.md n°4, non-modalité obligatoire).

`_validate_filenames_qt`, `_check_animated_gifs_qt` (et `_has_animated_gifs` qu'elle appelle), `_handle_duplicate_filenames_qt` (et `detect_duplicate_filenames_for_save` dans `dialogs_qt.py` qu'elle appelle), ainsi que `_auto_update_page_count_qt`, acceptent toutes un paramètre `state=None` (fallback sur le singleton global `modules.qt.state.state` si omis). Chaque fonction publique (`save_as_cbz`, `save_selection_as_cbz`, `save_selection_to_folder`, `create_cbz_from_images`, `apply_new_names`) capture déjà `state = _state_module.state` en tête de fonction — **toujours propager ce `state` local aux lambdas de la chaîne de validation** (`lambda done: _validate_filenames_qt(parent, render_mosaic, done, state)`, etc.), jamais laisser une étape de la chaîne relire le global elle-même. Raison : le singleton est partagé entre panel1/panel2 (voir skill `mosaic-thumbnails`) — sans cette propagation, basculer sur l'autre panneau pendant qu'une boîte de dialogue de la chaîne (ex. `QuestionYNCDialog` de l'étape 1) reste ouverte ferait analyser le mauvais comic à l'étape suivante. **Toute nouvelle validation ajoutée à la chaîne doit suivre ce même pattern** dès sa création, pas seulement après coup.

`save_selection_as_cbz`/`save_selection_to_folder` ne passent que par l'étape 3 (doublons), pas les étapes 1/2 — cohérent, puisqu'exporter une sélection ne réécrit pas l'archive complète.

Avant les validations, `_check_no_ico`/`_check_no_video` bloquent purement et simplement la sauvegarde CBZ (pas de correction possible) si la mosaïque contient un `.ico` (voir skill `create-ico`) ou une vidéo (extensions listées dans `_VIDEO_EXTENSIONS`) — `MsgDialog`/`ErrorDialog` immédiat, la chaîne de validation n'est même pas lancée.

## Écriture effective du ZIP

- **`_write_zip_with_progress(filepath, images_data, overlay, compression_level)`** — utilisée par `save_as_cbz` et `create_cbz_from_images`. Écrit chaque entrée via `zf.writestr`, avec un cas particulier : si `entry["dpi"]` est défini et diffère du DPI déjà encodé dans les bytes, ré-ouvre l'image PIL et réencode via `save_image_to_bytes` pour forcer le bon DPI avant écriture (voir skill `page-resize`, DPI géré à la volée). Met à jour `_SavingOverlay` (overlay rouge "Sauvegarde X%" sur le canvas, classe locale à ce fichier — **pas** le mécanisme `canvas_overlay_qt.py` habituel, voir "Pièges" plus bas) après chaque entrée.
- **`save_selection_as_cbz`** écrit directement en ligne (pas via `_write_zip_with_progress`, pas d'overlay de progression) — cohérent avec le fait qu'une sélection est généralement petite.
- **`_write_apply_new_names`** (appelée par `apply_new_names`) a sa propre logique d'écriture selon l'extension d'origine :
  - **`.cbz`** — écrit dans un fichier temporaire (`tempfile.NamedTemporaryFile`, dans `get_mosaicview_temp_dir()`), vérifie l'espace disque disponible sur le disque de destination (`shutil.disk_usage`) avant de faire `shutil.move()` par-dessus l'original — évite d'avoir un CBZ à moitié écrit si le disque est plein en cours de route.
  - **`.cbr`/`.cb7`/`.cbt`/`.epub`** — demande un nouveau nom de fichier `.cbz` (conversion, l'ancien format ne peut pas être réécrit), propose ensuite de supprimer l'original via `SaveSuccessDialog`.
  - **`.pdf`** — même principe que ci-dessus mais avec ses propres clés de traduction (`cbz_converted_from_pdf.*`).

## `safe_join()` — protection Zip Slip (export multi-fichiers)

`save_selection_to_folder`, quand plusieurs fichiers sont exportés vers un dossier, utilise `safe_join(folder, entry["orig_name"])` (`utils.py:369`) plutôt qu'un simple `os.path.join`. Un `orig_name` piégé contenant `../` ou un chemin absolu produirait normalement une écriture hors du dossier choisi (vulnérabilité Zip Slip classique sur les archives non fiables) — `safe_join` renvoie `None` dans ce cas, et l'entrée est comptée dans `skipped_count` plutôt qu'écrite. Ce garde-fou ne s'applique **pas** à `_write_zip_with_progress`/`_write_apply_new_names`, qui écrivent dans un ZIP (où `zf.writestr(entry["orig_name"], ...)` ne peut pas s'échapper d'un dossier réel — le risque Zip Slip existe seulement à la **lecture** d'une archive, pas à l'écriture, voir skill `archive-image-loading` pour ce côté-là).

## `_get_save_filename()` — recentrage du dialogue natif Windows

`QFileDialog.getSaveFileName()` est un dialogue natif Windows, donc pas centré sur `parent` par le mécanisme Qt habituel (`_center_on_widget`/`position_dialog_on_parent`, voir règle CLAUDE.md n°5). Ce fichier contourne la limitation avec un thread `ctypes` séparé qui : énumère les fenêtres Windows (`EnumWindows`) à la recherche d'une classe `#32770` (dialogue Windows générique) dont le titre correspond exactement au `title` passé, attend qu'elle devienne visible, puis la repositionne via `SetWindowPos` au centre du `parent`. Fonctionne par **titre de fenêtre exact** — si un autre dialogue Windows partage le même titre au même moment, le mauvais pourrait être déplacé (scénario improbable mais à garder en tête si le recentrage semble aléatoirement rater).

## Dialogues Qt dédiés (tous non-modaux, tous dans ce fichier)

- **`InfoDialogClickablePath`** — info + lien cliquable vers l'emplacement du fichier sauvegardé (ouvre l'Explorateur, voir skill `explorer-select`... en réalité ici un `subprocess.Popen(["explorer", "/select,", ...])` direct dans `_open_file_location`, **pas** `_explorer_select()` — écart à signaler si on unifie un jour, voir "Pièges").
- **`SaveSuccessDialog`** — info + lien cliquable + question Oui/Non ("supprimer l'ancien fichier ?"), résultat via callback `on_done(bool)`.
- **`DuplicateNamesErrorDialog`** — erreur bloquante (OK seulement) pour des doublons de noms détectés tardivement dans `apply_new_names` (différent de `DuplicateFilenameDialog` : ici pas de choix, juste un blocage).
- **`DuplicateFilenameDialog`** — 3 boutons (renuméroter/ignorer/annuler), résultat via `Signal(object)` + `ask_async(on_result)`.
- **`FileSavedDialog`** / **`ThumbnailSavedDialog`** — confirmation avec lien vers un dossier (export multi-fichiers / export de vignettes — cette dernière semble utilisée ailleurs pour un export de vignettes non couvert par ce skill, à vérifier si une tâche future la concerne).

Tous suivent le pattern standard : `setModal(False)`, connexion/déconnexion de `language_signal.changed` dans `_retranslate`/`_on_close`, centrage via `_center_on_widget` ou `position_dialog_on_parent`.

## Comment modifier

- **Ajouter une nouvelle validation pré-sauvegarde** : écrire une fonction `_ma_validation_qt(parent, ..., on_done)` suivant le pattern `on_done(True/False)`, l'ajouter à la liste passée à `_run_validation_chain` dans `save_as_cbz`/`create_cbz_from_images`/`apply_new_names` (les 3 endroits, pas un seul, si la validation doit s'appliquer partout).
- **Changer le niveau de compression utilisé à la sauvegarde** : ne touche pas ce fichier — voir skill `zip-compression`, `zip_compression_kwargs(comp_level)` est déjà le point de passage central, appelé ici depuis `parent._zip_compression_config().get_zip_compression_level()`.
- **Changer le comportement de suppression de l'ancien fichier** : `_safe_delete()` (fin de fichier) — utilise `send2trash` si disponible (corbeille) sinon `os.remove` direct. Utilisé par `save_as_cbz`/`_write_apply_new_names`, jamais appelé sans confirmation préalable de l'utilisateur (toujours derrière un `SaveSuccessDialog`/`on_done(True)`).
- **Ajouter un nouveau format source pour `apply_new_names`** (ex. un futur format d'archive) : ajouter une branche `elif ext == ".xxx":` dans `_write_apply_new_names`, suivant le pattern CBR/CB7/CBT/EPUB (demande un nouveau nom `.cbz`, écrit, propose suppression de l'original via `SaveSuccessDialog`).

## Pièges connus

- **`_SavingOverlay` est une classe locale, pas le mécanisme `canvas_overlay_qt.py`** — contrairement à la quasi-totalité des indicateurs de progression du projet (voir skill `canvas-overlay-progress`, "réutilisé par au moins 11 fichiers"), la sauvegarde CBZ a sa propre implémentation d'overlay texte rouge (`QGraphicsTextItem` ajouté/retiré manuellement de la scène). Si une modification future doit toucher l'un des deux mécanismes, vérifier lequel est réellement utilisé ici avant de supposer qu'il s'agit du mécanisme central.
- **`_open_file_location` réimplémente l'ouverture Explorateur au lieu de réutiliser `_explorer_select()`** (skill `explorer-select`, qui dit explicitement "toujours réutiliser, jamais réimplémenter un appel explorer /select ad hoc") — cet écart existait déjà dans le code avant la création de ce skill ; à signaler/corriger si une tâche touche ce fichier, mais hors du périmètre d'une simple documentation.
- **Deux dialogues de "doublons" différents** — `DuplicateFilenameDialog` (3 choix, avant sauvegarde normale) et `DuplicateNamesErrorDialog` (blocage simple, dans `apply_new_names` seulement, pour des doublons détectés après application des noms modifiés depuis les `NameEdit`). Ne pas les confondre en cherchant "le" dialogue de doublons de noms.
- **`_finish_apply_new_names` est appelée à des moments différents selon la branche** — immédiatement pour `.cbz` (pas de conversion), mais **différée jusqu'à la réponse de `SaveSuccessDialog`** pour les conversions CBR/CB7/CBT/EPUB/PDF (commentaire explicite dans le code : appeler `_done(True)` trop tôt déclencherait une fermeture en cascade des fenêtres liées au comic avant que l'utilisateur ait répondu à la question de suppression).
- **`create_cbz_from_images`/`apply_new_names` appliquent les noms depuis `entry["name_entry"]` (widget Qt `NameEdit`) avant validation** — dupliqué dans les deux fonctions (pas de fonction commune), donc une modification du format de nom de fichier doit être répercutée aux deux endroits.
- **`safe_join` protège uniquement l'export vers un dossier réel**, pas l'écriture dans un ZIP — ne pas supposer qu'il s'agit d'une protection Zip Slip générale utilisée partout dans le fichier.

## Références croisées

- `zip-compression` — `zip_compression_kwargs()`, réglage par panneau utilisé à chaque écriture ZIP de ce fichier.
- `renumbering` — `renumber_btn_action`, appelé depuis `DuplicateFilenameDialog` quand l'utilisateur choisit "renuméroter".
- `create-ico` — `_check_no_ico`, blocage de sauvegarde tant qu'un `.ico` traîne dans la mosaïque.
- `animated-gif` — avertissement `_check_animated_gifs_qt` avant sauvegarde (fige les GIFs sur leur frame courante).
- `page-resize` — gestion du DPI à la volée dans `_write_zip_with_progress`.
- `explorer-select` — mécanisme central que `_open_file_location` devrait utiliser mais réimplémente (écart connu, voir Pièges).
- `canvas-overlay-progress` — mécanisme central de progression, **non utilisé ici** (`_SavingOverlay` est une implémentation séparée, voir Pièges) ; comparer les deux si on envisage d'unifier.
- `archive-image-loading` — le pendant lecture de la sécurité anti-évasion de chemin (Zip Slip à l'ouverture, `safe_join` couvrant ici seulement l'écriture vers un dossier).
- `temp-files` — fichier temporaire créé par `_write_apply_new_names` (`.cbz` en écrasement) dans `get_mosaicview_temp_dir()`.
- `mosaic-thumbnails` — piège daté 2026-08-14 sur le singleton `_state_module.state` partagé entre panneaux, à l'origine de la propagation explicite de `state` dans la chaîne de validation décrite plus haut.
