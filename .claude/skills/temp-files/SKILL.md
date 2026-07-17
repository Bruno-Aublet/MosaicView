---
name: temp-files
description: Localiser ou modifier les fichiers temporaires de MosaicView (extractions, dossiers clipboard, logs de batch, _MEI* PyInstaller) dans %TEMP%\MosaicViewTemp\. Utiliser dès qu'une tâche touche à temp_files.py ou qu'un traitement doit écrire des fichiers jetables.
---

# Fichiers temporaires — MosaicView

Fichiers jetables, recréés à la demande, effacés à chaque fermeture de l'application. Distinct de la configuration persistante (marque-pages, réglages, clé API...), qui vit ailleurs depuis la v1.6.2 — voir skill `config-storage`. Ne jamais confondre les deux dossiers.

## Emplacement — `%TEMP%\MosaicViewTemp\`

N'a **pas** bougé lors du déménagement de la config vers `%APPDATA%` (v1.6.2) — c'est l'emplacement correct pour ce type de contenu, Windows est censé pouvoir le purger sans conséquence. Avant la v1.6.2, la config vivait ici aussi (par erreur architecturale) ; ce n'est plus le cas.

## Fichier central — `modules/qt/temp_files.py`

Quatre fonctions, aucune classe :

| Fonction | Rôle | Ligne |
|---|---|---|
| `get_mosaicview_temp_dir()` | Retourne le chemin, le crée si absent | [temp_files.py:48](../../../modules/qt/temp_files.py#L48) |
| `cleanup_all_temp_files(keep_logs=False)` | Vide le dossier (avec exceptions) | [temp_files.py:57](../../../modules/qt/temp_files.py#L57) |
| `cleanup_stale_mei_dirs()` | Nettoie les dossiers `_MEI*` PyInstaller orphelins | [temp_files.py:12](../../../modules/qt/temp_files.py#L12) |
| `cleanup_legacy_root_clipboard_dirs()` | Nettoie les dossiers `clipboard_*` orphelins laissés à la racine `%TEMP%` par les versions antérieures au fix de placement (voir skill `clipboard`) | [temp_files.py](../../../modules/qt/temp_files.py) |

**`get_mosaicview_temp_dir()` est le point de passage obligé** pour obtenir ce chemin — ne jamais reconstruire `os.path.join(tempfile.gettempdir(), "MosaicViewTemp")` à la main dans un nouveau module ; importer et appeler cette fonction (ou passer par le callback `get_mosaicview_temp_dir` déjà injecté dans plusieurs dialogues batch, voir plus bas).

## Ce qui y vit, et pourquoi

- **Extractions "ouvrir avec l'application par défaut"** — `open_with_default_app_qt.py:128` : `entry["bytes"]` extrait sous son nom d'origine (`MosaicViewTemp/<orig_name>`) avant de le passer à l'application Windows associée.
- **Dossiers `clipboard_*`** — créés par `clipboard_qt.py` lors d'un copier/couper d'archive ou de pages. **Conservés 12h** (pas effacés immédiatement) par `cleanup_all_temp_files`, pour permettre un collage après fermeture/réouverture de l'application — voir section suivante.
- **Logs de batch** — `Log_pdftocbz_*.txt`, `Log_cbrtocbz_*.txt`, `Log_imgtocbz_*.txt` (voir skill `batch-processing`), écrits directement dans `batch_dialogs_qt.py`/`batch_metadata_dialog_qt.py` en cas d'erreur pendant une conversion en lot. Conservés indéfiniment (pas de purge par âge) jusqu'à un nettoyage manuel explicite.
- **Dossiers `drag_*`** — `mosaic_canvas.py:1593`, fichiers temporaires générés pendant un drag-out CF_HDROP vers l'Explorateur Windows (voir skill `drag-and-drop`).
- **Dossiers `printjob_*`** — `printing_qt.py:46`, pages exportées temporairement pour impression.
- **Dossiers `_MEI*`** — générés par PyInstaller en mode `--onefile` à chaque lancement (extraction de l'archive embarquée). `cleanup_stale_mei_dirs()` supprime ceux laissés par un plantage antérieur, en excluant le dossier de l'instance courante (`sys._MEIPASS`) et ceux encore verrouillés par une autre instance active (test par `os.rename` sur lui-même : échoue si verrouillé).

## Nettoyage de migration — dossiers `clipboard_*` orphelins à la racine `%TEMP%`

Avant son fix, `PanelWidget._get_temp_dir()` écrivait les dossiers `clipboard_*` directement à la racine `%TEMP%` au lieu de `%TEMP%\MosaicViewTemp\` (voir skill `clipboard`, section "Bug corrigé"). Tous les utilisateurs ayant utilisé copier/couper sur une version antérieure au fix ont potentiellement des dizaines de ces dossiers orphelins, jamais nettoyés puisque hors de portée de `cleanup_all_temp_files()`.

`cleanup_legacy_root_clipboard_dirs()` traite ce résidu : au lancement (appelée dans `MosaicView.py::main()` juste après `cleanup_stale_mei_dirs()`), elle balaie la racine `%TEMP%` (pas `MosaicViewTemp`) et supprime tout dossier dont le nom commence par `clipboard_`. Sans condition d'âge ni test de verrouillage — contrairement à `cleanup_stale_mei_dirs()`, ces dossiers ne peuvent plus être créés à cet endroit par aucune version corrigée, leur seule présence à la racine suffit à les qualifier d'orphelins.

## `cleanup_all_temp_files(keep_logs=False)` — ce qui est effacé, et ce qui ne l'est pas

Balaie tout le contenu de `MosaicViewTemp` et supprime chaque élément, **sauf** :
- Les logs de batch (`Log_pdftocbz_*`/`Log_cbrtocbz_*`/`Log_imgtocbz_*.txt`) si `keep_logs=True` (utilisé pendant l'exécution d'un batch, pour ne pas effacer son propre log en cours d'écriture).
- Les dossiers `clipboard_*` de moins de 12h (`clipboard_max_age = 12 * 60 * 60`).

**Depuis la v1.6.2**, cette fonction n'exclut plus `.mosaicview_config.json` de son balayage — cette exclusion a été retirée parce que le fichier de config ne vit structurellement plus dans ce dossier (déménagé vers `%APPDATA%`, voir skill `config-storage`). Si un jour ce fichier apparaît encore ici, ce n'est que le résidu d'une migration : `ConfigManager._migrate_from_temp()` le déplace au lancement suivant, avant que `cleanup_all_temp_files` ait la moindre chance de s'exécuter.

Appelée automatiquement à la fermeture de l'application, et manuellement via `PanelWidget._clear_temp_files_with_message()` (bouton "Effacer les fichiers temporaires").

## Points d'entrée UI

- **Effacer les fichiers temporaires** : `PanelWidget._clear_temp_files_with_message()` ([modules/qt/panel_widget.py:1184](../../../modules/qt/panel_widget.py#L1184)) — appelle `cleanup_all_temp_files()`, affiche un `_WarnDialog`.
- **Effacer le presse-papiers** : `PanelWidget._clear_clipboard_files()` ([modules/qt/panel_widget.py:1206](../../../modules/qt/panel_widget.py#L1206)) — supprime spécifiquement les dossiers `clipboard_*`, indépendamment de leur âge.
- **Ouvrir le dossier** : `PanelWidget._open_temp_folder()` ([modules/qt/panel_widget.py:1226](../../../modules/qt/panel_widget.py#L1226)) — `subprocess.Popen(["explorer", temp_dir])`.
- Tous les trois câblés dans `menubar_callbacks_qt.py`, consommés par la barre de menus et le menu contextuel (section "À propos") — regroupés ensemble, séparés par un `addSeparator()` des commandes de fichiers de configuration (voir skill `config-storage`).
- **Mode d'emploi** : section "Fichiers temporaires" (clé `help.config_files`/`help.config_files_content`), juste après "Fichiers de configuration". Builder : `UserGuideWindow._build_config_section()` ([modules/qt/user_guide_qt.py:814](../../../modules/qt/user_guide_qt.py#L814)). Contient aussi la note presse-papiers (`config_clipboard_note`) et la note logs de batch (`config_log_note`).

## Ajouter un nouveau type de fichier temporaire

1. Utiliser `get_mosaicview_temp_dir()` pour obtenir le dossier racine — jamais un chemin construit à la main.
2. Choisir un préfixe de nom cohérent avec l'existant (`clipboard_*`, `drag_*`, `printjob_*`, `Log_*`) pour que le fichier/dossier reste identifiable dans l'Explorateur.
3. Si le contenu doit survivre plus longtemps que la fermeture immédiate de l'app (comme `clipboard_*`), ajouter une exception dédiée dans `cleanup_all_temp_files()` avec une logique d'âge similaire — ne pas réutiliser `clipboard_max_age` telle quelle si la durée de rétention doit différer.
4. Si le traitement tourne dans un dialogue batch, le chemin est généralement déjà injecté via `callbacks['get_mosaicview_temp_dir']` plutôt qu'importé directement — suivre le pattern déjà en place dans `batch_dialogs_qt.py`/`batch_metadata_dialog_qt.py` pour rester cohérent avec le reste du fichier.

## Référencements croisés

- **`config-storage`** — l'autre moitié de l'ancienne section fusionnée du mode d'emploi ; la config persistante, qui ne vit plus ici.
- **`batch-processing`** — génère les logs `Log_*tocbz_*.txt` dans ce dossier.
- **`drag-and-drop`** — génère les dossiers `drag_*` pour le drag-out CF_HDROP.
- **`clipboard`** — génère les dossiers `clipboard_*` ; ancien bug de placement à la racine `%TEMP%` et son nettoyage de migration (`cleanup_legacy_root_clipboard_dirs()`) documentés ici et là-bas.
- **`user-guide`** — section "Fichiers temporaires" du mode d'emploi.
- **`single-instance`** — piège PyInstaller QtNetwork lié aux dossiers `_MEI*`.

## Pièges connus

- **Ne jamais coder en dur le chemin `%TEMP%\MosaicViewTemp`** — toujours `get_mosaicview_temp_dir()`, pour rester cohérent si l'emplacement devait un jour changer.
- **Ne pas confondre avec la config** — un nouveau réglage à persister durablement ne va jamais ici, voir skill `config-storage`.
- **`cleanup_stale_mei_dirs()` n'a d'effet qu'en mode PyInstaller `--onefile`** (`sys._MEIPASS` absent sinon) — ne pas s'attendre à un effet en environnement de développement (`python MosaicView.py`).
