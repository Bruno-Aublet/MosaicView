---
name: temp-files
description: Localiser ou modifier les fichiers temporaires de MosaicView (extractions, dossiers clipboard, logs de batch, _MEI* PyInstaller) dans %TEMP%\MosaicViewTemp\. Utiliser dès qu'une tâche touche à temp_files.py ou qu'un traitement doit écrire des fichiers jetables.
---

# Fichiers temporaires — MosaicView

Fichiers jetables, recréés à la demande, effacés à chaque fermeture de l'application. Distinct de la configuration persistante (marque-pages, réglages, clé API...), qui vit dans `%APPDATA%` — voir skill `config-storage`. Ne jamais confondre les deux dossiers.

## Emplacement — `%TEMP%\MosaicViewTemp\`

C'est l'emplacement correct pour ce type de contenu, Windows est censé pouvoir le purger sans conséquence. La configuration persistante ne vit pas ici — voir skill `config-storage`.

## Fichier central — `modules/qt/temp_files.py`

Quatre fonctions, aucune classe :

Toutes dans [temp_files.py](../../../modules/qt/temp_files.py) :

| Fonction | Rôle |
|---|---|
| `get_mosaicview_temp_dir()` | Retourne le chemin, le crée si absent |
| `unique_log_path(directory, kind, now)` | Chemin libre du journal d'un lot : `Log_<kind>_AAAA_MM_JJ_HH_MM.txt`, suffixé `_2`, `_3`… si le nom existe déjà (testé par `tests/test_temp_files_logs.py`, comme la conservation des journaux à la fermeture) |
| `log_text(text)` | Texte d'une ligne de journal de lot : chaque caractère de contrôle écrit sous sa forme échappée (`\n`, `\x07`), pour qu'un nom de membre d'archive piégé ne crée pas de fausse ligne (testé par `tests/test_temp_files_logs.py` ; skill `batch-processing`, « Lignes des journaux ») |
| `cleanup_all_temp_files(keep_logs=False)` | Vide le dossier (avec exceptions) |
| `cleanup_stale_mei_dirs()` | Nettoie les dossiers `_MEI*` PyInstaller orphelins |
| `cleanup_legacy_root_clipboard_dirs()` | Nettoie les dossiers `clipboard_<nombre>_<nombre>` orphelins trouvés à la racine `%TEMP%` (hors de `MosaicViewTemp`, voir skill `clipboard`) |

**`get_mosaicview_temp_dir()` est le point de passage obligé** pour obtenir ce chemin — ne jamais reconstruire `os.path.join(tempfile.gettempdir(), "MosaicViewTemp")` à la main dans un nouveau module ; importer et appeler cette fonction (ou passer par le callback `get_mosaicview_temp_dir` déjà injecté dans plusieurs dialogues batch, voir plus bas).

## Ce qui y vit, et pourquoi

- **Extractions "ouvrir avec l'application par défaut"** — `open_file_with_default_app` (`open_with_default_app_qt.py`) : `entry["bytes"]` extrait sous son nom rendu valide sous Windows (`MosaicViewTemp/<windows_safe_relpath(orig_name)>`, liste blanche de types, skill `open-with-default-app`) avant de le passer à l'application Windows associée.
- **Dossiers `clipboard_*`** — créés par `clipboard_qt.py` lors d'un copier/couper d'archive ou de pages. **Conservés 12h** (pas effacés immédiatement) par `cleanup_all_temp_files`, pour permettre un collage après fermeture/réouverture de l'application — voir section suivante.
- **Logs de batch** — `Log_<kind>_*.txt` (`cbrtocbz`, `cb7tocbz`, `cbttocbz`, `pdftocbz`, `imgtocbz`, `recompress`, `metadata` ; voir skill `batch-processing`), écrits dans `batch_dialogs_qt.py`/`batch_metadata_dialog_qt.py` à la fin d'un lot avec erreurs (ou renommages). Chemin donné par `unique_log_path` : **jamais construire le nom à la main**. **Piège** : le nom n'est daté qu'à la minute ; sans le suffixe `_2`, `_3`… deux lots du même type terminés dans la même minute écriraient dans le même fichier, et le résumé du premier renverrait au journal du second. Conservés indéfiniment (pas de purge par âge, épargnés à la fermeture) jusqu'à un nettoyage manuel explicite.
- **Dossiers `drag_*`** — `MosaicCanvas._start_drag` (`mosaic_canvas.py`), fichiers temporaires générés pendant un drag-out CF_HDROP vers l'Explorateur Windows (voir skill `drag-and-drop`).
- **Dossiers `printjob_*`** — `_open_print_dialog` (`printing_qt.py`), pages exportées temporairement pour impression.
- **Dossiers `_MEI*`** — générés par PyInstaller en mode `--onefile` à chaque lancement (extraction de l'archive embarquée). `cleanup_stale_mei_dirs()` supprime ceux laissés par un plantage antérieur, en excluant le dossier de l'instance courante (`sys._MEIPASS`) et ceux encore verrouillés par une autre instance active (test par `os.rename` sur lui-même : échoue si verrouillé).

## Nettoyage — dossiers `clipboard_*` orphelins à la racine `%TEMP%`

Un dossier `clipboard_*` ne doit jamais légitimement se trouver ailleurs qu'à l'intérieur de `%TEMP%\MosaicViewTemp\` (voir skill `clipboard`) — s'en trouver un directement à la racine `%TEMP%` le qualifie d'orphelin.

`cleanup_legacy_root_clipboard_dirs()` traite ce cas : au lancement (appelée dans `MosaicView.py::main()` juste après `cleanup_stale_mei_dirs()`), elle balaie la racine `%TEMP%` (pas `MosaicViewTemp`) et supprime tout dossier nommé exactement `clipboard_<nombre>_<nombre>` (`re.fullmatch`), le nom que MosaicView donne à ces dossiers. Sans condition d'âge ni test de verrouillage — contrairement à `cleanup_stale_mei_dirs()`, leur seule présence à la racine suffit à les qualifier d'orphelins. **Piège** : jamais un simple préfixe `clipboard_` — `%TEMP%` est partagé par tous les logiciels, dont les dossiers seraient effacés. Testé par `tests/test_temp_files_logs.py` (`test_legacy_cleanup_removes_only_mosaicview_clipboard_dirs`).

## `cleanup_all_temp_files(keep_logs=False)` — ce qui est effacé, et ce qui ne l'est pas

Balaie tout le contenu de `MosaicViewTemp` et supprime chaque élément, **sauf** :
- Les journaux de moins de `LOG_MAX_AGE_DAYS` jours (30, constante de `temp_files.py`, âge d'après la date de modification) — tout fichier `Log_*.txt` (logs de batch et `Log_scan.txt`, skill `scan`) — si `keep_logs=True`, ce qui est le cas à la fermeture de l'application (`cleanup_all_temp_files(keep_logs=True)`, `MosaicView.py`) : le résumé d'un lot indique le chemin de son journal, qui doit rester lisible après la fermeture. Les plus anciens sont supprimés : un fichier par lot s'accumulerait sinon sans limite. Âge illisible : journal gardé. Testé par `tests/test_temp_files_logs.py`. Le test porte sur le préfixe commun `Log_`, jamais sur une liste de types : un nouveau lot qui nomme son journal par `unique_log_path` est conservé sans rien déclarer ici. Le bouton « Effacer les fichiers temporaires » (`keep_logs=False`) les supprime.
- Les dossiers `clipboard_*` de moins de 12h (`clipboard_max_age = 12 * 60 * 60`).

Cette fonction n'exclut pas `.mosaicview_config.json` de son balayage — le fichier de config vit dans `%APPDATA%`, pas ici (voir skill `config-storage`). Si ce fichier apparaît malgré tout dans `MosaicViewTemp` (résidu d'une ancienne installation), `ConfigManager._migrate_from_temp()` le déplace au lancement suivant, avant que `cleanup_all_temp_files` ait la moindre chance de s'exécuter.

Appelée automatiquement à la fermeture de l'application, et manuellement via `PanelWidget._clear_temp_files_with_message()` (bouton "Effacer les fichiers temporaires"). Elle rend les éléments non effacés, `[(nom, message brut)]` (élément encore utilisé, droits) — un dossier `clipboard_*` récent, laissé exprès, n'en fait pas partie. La fermeture ignore ce retour (repris au nettoyage suivant) ; le bouton le signale. **Piège** : avalés, ces échecs faisaient annoncer « effacés avec succès » alors qu'une partie restait.

## Points d'entrée UI

- **Effacer les fichiers temporaires** : `PanelWidget._clear_temp_files_with_message()` ([modules/qt/panel_widget.py](../../../modules/qt/panel_widget.py)) — appelle `cleanup_all_temp_files()` et passe ses échecs à `PanelWidget._show_clear_result`.
- **Effacer le presse-papiers** : `PanelWidget._clear_clipboard_files()` — supprime spécifiquement les dossiers `clipboard_*`, indépendamment de leur âge, en relevant chaque dossier non effacé.
- **Message de fin** (les deux boutons, et l'effacement de la configuration, skill `config-storage`) : `PanelWidget._show_clear_result(échecs, clé_succès, clé_échec)` — `_WarnDialog` de succès, ou, s'il reste des éléments, titre `help.config_clear_failed_title` et message `help.config_temp_not_cleared` / `config_clipboard_not_cleared` (`{count}`, `{error}` = première raison). Jamais un succès annoncé alors qu'une partie est restée.
- **Ouvrir le dossier** : `PanelWidget._open_temp_folder()` — `subprocess.Popen(["explorer", temp_dir])`.
- Tous les trois câblés dans `menubar_callbacks_qt.py`, consommés par la barre de menus et le menu contextuel (section "À propos") — regroupés ensemble, séparés par un `addSeparator()` des commandes de fichiers de configuration (voir skill `config-storage`).
- **Mode d'emploi** : section "Fichiers temporaires" (clé `help.config_files`/`help.config_files_content`), juste après "Fichiers de configuration". Builder : `_HelpDialog._build_config_section()` ([modules/qt/user_guide_qt.py](../../../modules/qt/user_guide_qt.py)). Contient aussi la note presse-papiers (`config_clipboard_note`) et la note logs de batch (`config_log_note`). Le journal des erreurs inattendues, présenté juste après (`config_error_log_note`), n'est pas un fichier temporaire : il vit dans `%APPDATA%\MosaicView\errors` (skill `config-storage`) et « Effacer les fichiers temporaires » n'y touche pas.

## Ajouter un nouveau type de fichier temporaire

1. Utiliser `get_mosaicview_temp_dir()` pour obtenir le dossier racine — jamais un chemin construit à la main.
2. Choisir un préfixe de nom cohérent avec l'existant (`clipboard_*`, `drag_*`, `printjob_*`, `Log_*`) pour que le fichier/dossier reste identifiable dans l'Explorateur.
3. Si le contenu doit survivre plus longtemps que la fermeture immédiate de l'app (comme `clipboard_*`), ajouter une exception dédiée dans `cleanup_all_temp_files()` avec une logique d'âge similaire — ne pas réutiliser `clipboard_max_age` telle quelle si la durée de rétention doit différer.
4. Si le traitement tourne dans un dialogue batch, le chemin est généralement déjà injecté via `callbacks['get_mosaicview_temp_dir']` plutôt qu'importé directement — suivre le pattern déjà en place dans `batch_dialogs_qt.py`/`batch_metadata_dialog_qt.py` pour rester cohérent avec le reste du fichier.

## Hors de ce dossier — temporaires d'écriture à côté des fichiers de l'utilisateur

Un fichier qui **remplace ou crée un fichier de l'utilisateur** (CBZ enregistré, converti, recompressé) n'est jamais écrit dans `MosaicViewTemp` : autre volume (le remplacement deviendrait une copie non atomique) et `cleanup_all_temp_files` l'effacerait à la fermeture. Son temporaire est créé **dans le dossier de destination** par `utils.make_temp_path(dossier)` (nom `.~mosaicview_*.tmp`, constantes `TEMP_FILE_PREFIX`/`TEMP_FILE_SUFFIX`), libéré par `release_temp_path`. Un plantage ou une coupure de courant pendant l'écriture en laisse un à côté des comics : `make_temp_path` appelle d'abord `remove_stale_temp_files(dossier)`, qui supprime ceux qu'aucune écriture en cours de ce processus ne détient et qui n'ont pas été modifiés depuis `STALE_TEMP_AGE` (600 s). Le ménage n'a donc lieu qu'à la prochaine écriture **dans ce même dossier**. Détail et utilisateurs : skill `save-export` (`replace_file_atomically`). La configuration et les macros (`%APPDATA%`) ont leur propre écriture atomique, `config_manager.write_json_atomically` (skill `config-storage`).

## Référencements croisés

- **`save-export`** — temporaires d'écriture `.~mosaicview_*.tmp` à côté des fichiers de l'utilisateur et leur nettoyage après un plantage (voir section précédente).
- **`config-storage`** — la configuration persistante, qui ne vit pas ici mais dans `%APPDATA%`.
- **`batch-processing`** — génère les logs `Log_*tocbz_*.txt` dans ce dossier.
- **`drag-and-drop`** — génère les dossiers `drag_*` pour le drag-out CF_HDROP.
- **`clipboard`** — génère les dossiers `clipboard_*` ; nettoyage des orphelins à la racine `%TEMP%` (`cleanup_legacy_root_clipboard_dirs()`) documenté ici et là-bas.
- **`user-guide`** — section "Fichiers temporaires" du mode d'emploi.
- **`single-instance`** — piège PyInstaller QtNetwork lié aux dossiers `_MEI*`.

## Pièges connus

- **Ne jamais coder en dur le chemin `%TEMP%\MosaicViewTemp`** — toujours `get_mosaicview_temp_dir()`, pour rester cohérent si l'emplacement devait un jour changer.
- **Ne pas confondre avec la config** — un nouveau réglage à persister durablement ne va jamais ici, voir skill `config-storage`.
- **`cleanup_stale_mei_dirs()` n'a d'effet qu'en mode PyInstaller `--onefile`** (`sys._MEIPASS` absent sinon) — ne pas s'attendre à un effet en environnement de développement (`python MosaicView.py`).
