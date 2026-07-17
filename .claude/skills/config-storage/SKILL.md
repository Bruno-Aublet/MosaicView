---
name: config-storage
description: Localiser ou modifier la configuration persistante de MosaicView (fenêtre, thème, langue, récents, marque-pages, clé API chiffrée) dans %APPDATA%\MosaicView\.mosaicview_config.json. Utiliser dès qu'une tâche touche à ConfigManager, config_manager.py, ou à un réglage à persister.
---

# Fichiers de configuration — MosaicView

Stockage persistant de tous les réglages applicatifs, distinct des vrais fichiers temporaires (voir skill `temp-files`). Ne jamais confondre les deux : la config est faite pour survivre indéfiniment entre les sessions, les fichiers temporaires sont jetables et effacés à chaque fermeture.

## Emplacement — `%APPDATA%\MosaicView\`

Depuis la v1.6.2. Deux fichiers y vivent :
- `.mosaicview_config.json` — la config principale (voir `ConfigManager.CONFIG_FILENAME`, [modules/qt/config_manager.py:43](../../../modules/qt/config_manager.py#L43))
- `.mosaicview_icon_toolbar.json` — config séparée de la barre d'icônes, volontairement non effacée par "reset aux valeurs par défaut" (voir section dédiée plus bas)

**Avant la v1.6.2**, ces deux fichiers vivaient dans `%TEMP%\MosaicViewTemp\`, au même endroit que les vrais fichiers temporaires. Ce choix posait un problème réel : Windows purge périodiquement `%TEMP%` (Nettoyage de disque, Storage Sense), ce qui pouvait effacer silencieusement toute la config — y compris les marque-pages — sans avertissement. D'où le déménagement.

## Fichier central — `modules/qt/config_manager.py`

Une seule classe, `ConfigManager` ([modules/qt/config_manager.py:39](../../../modules/qt/config_manager.py#L39)), instanciée une fois comme singleton global via `init_config_manager()` / récupérée partout via `get_config_manager()`. Un wrapper, `Panel2Config` ([modules/qt/config_manager.py:685](../../../modules/qt/config_manager.py#L685)), redirige certaines méthodes vers des clés `*_panel2` pour ne pas écraser la config de panel1 — voir skill `panels`.

### Construction du chemin et migration — `__init__` (ligne ~66)

```python
config_dir = os.path.join(os.environ["APPDATA"], "MosaicView")
```

Créé avec `os.makedirs(exist_ok=True)` si absent. **Avant** le premier `load_config()`, `_migrate_from_temp()` ([modules/qt/config_manager.py:99](../../../modules/qt/config_manager.py#L99)) s'exécute :

- Ne fait rien si `.mosaicview_config.json` existe déjà dans `%APPDATA%\MosaicView\` (migration déjà faite, ou nouvelle installation).
- Sinon, **déplace** (`shutil.move`, pas copie) `.mosaicview_config.json` et `.mosaicview_icon_toolbar.json` depuis `%TEMP%\MosaicViewTemp\` s'ils y existent encore.
- Silencieuse (`except Exception: pass`) — une migration ratée ne doit jamais empêcher le démarrage de l'application ; dans le pire cas, l'utilisateur repart avec une config par défaut.

**Piège pour toute modification touchant ce chemin** : ne jamais coder en dur `os.path.join(tempfile.gettempdir(), "MosaicViewTemp")` pour la config — c'est l'ancien emplacement, réservé aux vrais fichiers temporaires (skill `temp-files`). Toujours passer par `get_config_manager().config_dir` / `get_config_manager().get_config_file_path()`, jamais reconstruire le chemin à la main dans un nouveau call site (voir le piège historique documenté dans le mode d'emploi, section suivante).

### Lecture/écriture

`load_config()` fusionne le JSON chargé avec `DEFAULT_CONFIG` (une clé absente du fichier existant retombe sur sa valeur par défaut, pas d'erreur — permet d'ajouter de nouvelles clés sans migration explicite). `save_config()` réécrit le fichier entier à chaque `set()`. Tous les getters/setters typés (`get_dark_mode()`, `set_window_size()`, `get_bookmark()`, `set_comicvine_api_key()`...) passent par ce dict interne — **ne jamais lire/écrire `cfg.config['xxx']` directement** depuis un autre module, toujours utiliser un getter/setter dédié ou en ajouter un.

### Clé API ComicVine — chiffrement DPAPI

`get_comicvine_api_key()` / `set_comicvine_api_key()` ([modules/qt/config_manager.py:493-508](../../../modules/qt/config_manager.py#L493-L508)) chiffrent/déchiffrent via `win32crypt.CryptProtectData`/`CryptUnprotectData` (DPAPI Windows, liée au compte utilisateur — illisible sur une autre machine ou un autre compte). Migration automatique et silencieuse d'une éventuelle ancienne valeur en clair au premier `get()`. Voir skill `comicvine-metadata-fetch` pour l'usage de cette clé.

### Config icon-toolbar — fichier séparé

`_icon_toolbar_config_file` ([modules/qt/config_manager.py:534](../../../modules/qt/config_manager.py#L534)) vit dans le même dossier mais dans un fichier JSON à part, lu/écrit par `_read_icon_toolbar_config()`/`_write_icon_toolbar_config()`. Voir skill `icon-toolbar` pour ce qu'il contient et pourquoi il est séparé de `reset_to_defaults()`.

## Ajouter un nouveau réglage persistant

1. Ajouter une entrée dans `DEFAULT_CONFIG` ([modules/qt/config_manager.py:45](../../../modules/qt/config_manager.py#L45)) avec sa valeur par défaut.
2. Ajouter un getter/setter typé dédié (suivre le pattern existant, ex. `get_thumbnail_size()`/`set_thumbnail_size()`), pas un accès direct au dict.
3. Si le réglage doit être restauré au démarrage : voir skill `session-restore` (**skill à vérifier avant de s'y fier** — elle affirme encore que la config vit dans `%TEMP%`, obsolète depuis ce changement, voir note en bas).
4. Si le réglage doit avoir un équivalent séparé pour panel2 : ajouter la clé `*_panel2` correspondante + son relais dans `Panel2Config` (voir skill `panels`).

## Effacer/ouvrir le fichier de config — points d'entrée UI

- **Effacer** : `PanelWidget._clear_config_file()` ([modules/qt/panel_widget.py:1193](../../../modules/qt/panel_widget.py#L1193)) — supprime `get_config_manager().get_config_file_path()` si présent, affiche un `_WarnDialog`. Lit le chemin dynamiquement, donc suit automatiquement tout changement d'emplacement futur sans modification de code.
- **Ouvrir le dossier** : `PanelWidget._open_config_folder()` ([modules/qt/panel_widget.py:1233](../../../modules/qt/panel_widget.py#L1233)) — `subprocess.Popen(["explorer", get_config_manager().config_dir])`.
- Les deux sont câblés dans `menubar_callbacks_qt.py` (clés `clear_config_file`/`open_config_folder`), consommés par la barre de menus ([modules/qt/menubar_qt.py](../../../modules/qt/menubar_qt.py), section "À propos") et le menu contextuel ([modules/qt/context_menus_qt.py](../../../modules/qt/context_menus_qt.py)) — regroupés ensemble, séparés par un `addSeparator()` des commandes de fichiers temporaires.
- **Mode d'emploi** : section dédiée "Fichiers de configuration" (clé `help.config_dir`/`help.config_dir_content`), juste avant la section "Fichiers temporaires" (`help.config_files`). Builder : `UserGuideWindow._build_config_dir_section()` ([modules/qt/user_guide_qt.py:786](../../../modules/qt/user_guide_qt.py#L786)), retraduction : `_retranslate_config_dir_section()`. Cette section contient aussi les notes single-instance et registre Windows (`config_single_instance_note`/`config_registry_note`) — elles ont été déplacées ici depuis l'ancienne section fusionnée "Fichiers de configuration et fichiers temporaires" car elles décrivent un comportement au démarrage lié à l'identité de l'application, pas des fichiers temporaires à proprement parler.

## Référencements croisés

- **`session-restore`** — lit/écrit la config via `ConfigManager` pour restaurer la fenêtre au démarrage. **Ce skill contient des affirmations obsolètes** (emplacement `%TEMP%/MosaicViewTemp`) à corriger lors d'une prochaine intervention dessus.
- **`temp-files`** — les vrais fichiers temporaires, qui restent dans `%TEMP%\MosaicViewTemp\` et ne bougent pas.
- **`bookmarks`** — les marque-pages sont stockés dans la config (`get_bookmark()`/`set_bookmark()`), donc soumis à la même politique de persistance/migration que le reste.
- **`comicvine-metadata-fetch`** — utilise `get_comicvine_api_key()`/`set_comicvine_api_key()`.
- **`recent-items`** — `recent_files.py`/`recent_dbs.py` sont de simples façades autour des getters/setters `get_recent_files()`/`get_recent_dbs()` etc. de `ConfigManager` ; toute la logique de nettoyage (`clean_recent_files`/`clean_recent_dbs`) vit ici, pas dans les deux modules façade.
- **`icon-toolbar`** — le fichier de config séparé de la barre d'icônes.
- **`panels`** — `Panel2Config`, réglages dédoublés par panneau.
- **`user-guide`** — la section "Fichiers de configuration" du mode d'emploi.

## Pièges connus

- **Ne jamais coder en dur `%TEMP%\MosaicViewTemp` pour un besoin de config** — c'est l'erreur qui a motivé le déménagement ; toujours `get_config_manager().config_dir`.
- **`_migrate_from_temp()` déplace, ne copie pas** — après une migration réussie, l'ancien fichier dans `%TEMP%` n'existe plus. Ne pas s'étonner de son absence lors d'un futur debug.
- **Le nettoyage `cleanup_all_temp_files()`** (skill `temp-files`) n'a plus besoin d'exclure `.mosaicview_config.json` de son balayage — l'exclusion a été retirée en même temps que ce déménagement, puisque le fichier n'est structurellement plus dans le dossier balayé.
