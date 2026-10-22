---
name: config-storage
description: Localiser ou modifier la configuration persistante de MosaicView (fenêtre, thème, langue, récents, marque-pages, clé API chiffrée) dans %APPDATA%\MosaicView\.mosaicview_config.json, et le journal des erreurs (%APPDATA%\MosaicView\errors\errors.log, error_log.py, error_log_dialog_qt.py, sys.excepthook / threading.excepthook / faulthandler, fichiers de secours des plantages natifs, collect_crash_buffers, verrou entre process, localization.latin_texts, fenêtre « Journal d'erreurs », envoi par mail). Utiliser dès qu'une tâche touche à ConfigManager, config_manager.py, default_config_dir, au journal des erreurs, ou à un réglage à persister.
---

# Fichiers de configuration — MosaicView

Stockage persistant de tous les réglages applicatifs, distinct des vrais fichiers temporaires (voir skill `temp-files`). Ne jamais confondre les deux : la config est faite pour survivre indéfiniment entre les sessions, les fichiers temporaires sont jetables et effacés à chaque fermeture.

## Emplacement — `%APPDATA%\MosaicView\`

Deux fichiers y vivent :
- `.mosaicview_config.json` — la config principale (voir `ConfigManager.CONFIG_FILENAME`, [modules/qt/config_manager.py](../../../modules/qt/config_manager.py))
- `.mosaicview_icon_toolbar.json` — config séparée de la barre d'icônes, volontairement non effacée par "reset aux valeurs par défaut" (voir section dédiée plus bas)

Le sous-dossier `errors\` contient le journal des erreurs inattendues (section dédiée plus bas) ; il n'est pas touché par « Effacer le fichier de configuration ».

**Pourquoi pas `%TEMP%`** : Windows purge périodiquement `%TEMP%` (Nettoyage de disque, Storage Sense), ce qui effacerait silencieusement toute la config — y compris les marque-pages — sans avertissement. `%APPDATA%` est fait pour survivre indéfiniment, contrairement à `%TEMP%`.

## Fichier central — `modules/qt/config_manager.py`

Une seule classe, `ConfigManager`, instanciée une fois comme singleton global via `init_config_manager()` / récupérée partout via `get_config_manager()`. Un wrapper, `Panel2Config` (même fichier), redirige certaines méthodes vers des clés `*_panel2` pour ne pas écraser la config de panel1 — voir skill `panels`.

### Construction du chemin et migration — `ConfigManager.__init__`

```python
config_dir = default_config_dir()   # os.path.join(os.environ["APPDATA"], "MosaicView")
```

`default_config_dir()` (même module) est la seule définition de ce dossier : le journal des erreurs (`error_log.log_dir`) l'utilise aussi, sans instancier `ConfigManager`. Créé avec `os.makedirs(exist_ok=True)` si absent. **Avant** le premier `load_config()`, `_migrate_from_temp()` s'exécute :

- Ne fait rien si `.mosaicview_config.json` existe déjà dans `%APPDATA%\MosaicView\` (migration déjà faite, ou nouvelle installation).
- Sinon, **déplace** (`shutil.move`, pas copie) `.mosaicview_config.json` et `.mosaicview_icon_toolbar.json` depuis `%TEMP%\MosaicViewTemp\` s'ils y existent encore.
- Silencieuse (`except Exception: pass`) — une migration ratée ne doit jamais empêcher le démarrage de l'application ; dans le pire cas, l'utilisateur repart avec une config par défaut.

**Piège pour toute modification touchant ce chemin** : ne jamais coder en dur `os.path.join(tempfile.gettempdir(), "MosaicViewTemp")` pour la config — cet emplacement est réservé aux vrais fichiers temporaires (skill `temp-files`). Toujours passer par `get_config_manager().config_dir` / `get_config_manager().get_config_file_path()`, jamais reconstruire le chemin à la main dans un nouveau call site.

### Lecture/écriture

`load_config()` fusionne le JSON chargé avec `DEFAULT_CONFIG` (une clé absente du fichier existant retombe sur sa valeur par défaut, pas d'erreur — permet d'ajouter de nouvelles clés sans migration explicite). `save_config()` réécrit le fichier entier à chaque `set()`, par **`write_json_atomically(path, data)`** (fonction de module de `config_manager.py`, bibliothèque standard seule : ce module est chargé sans Qt) : le JSON complet va dans un temporaire `.~mosaicview_*.tmp` du même dossier, forcé sur le disque (`fsync`), qui remplace le fichier d'un coup (`os.replace`) ; en cas d'échec, temporaire supprimé, fichier intact, exception relancée (`save_config` retourne alors `False`, après avoir passé l'exception à **`ConfigManager.save_failed_handler`** s'il est posé). **Échec signalé à l'utilisateur** : `MainWindow.__init__` (`MosaicView.py`) pose ce handler, qui émet le signal `_ConfigSaveRelay.failed` (`save_config` peut être appelée depuis un thread de travail) ; `MainWindow._on_config_save_failed` affiche un seul `ErrorDialog` par session (`messages.errors.config_save_failed`, raison par `str(e)` ou le nom du type) — marque-pages, récents et réglages changés pendant la session seraient perdus à la fermeture, et `save_config` est appelée très souvent (taille de fenêtre…). Testé par `tests/test_audit7_p3.py`. **Ne jamais réécrire un fichier de configuration par `open(path, 'w')`** : l'ouverture le vide aussitôt, et un plantage, une coupure de courant ou un disque plein pendant l'écriture laisse un JSON illisible — `load_config` repart alors des valeurs par défaut et tout est perdu (récents, marque-pages, clé API). Coût mesuré : quelques millisecondes par écriture. Utilisée aussi par `_write_icon_toolbar_config` et `macro_engine.save_macro` (skill `macro-tool`). Testé par `tests/test_config_manager.py`. Tous les getters/setters typés (`get_dark_mode()`, `set_window_size()`, `get_bookmark()`, `set_comicvine_api_key()`...) passent par ce dict interne — **ne jamais lire/écrire `cfg.config['xxx']` directement** depuis un autre module, toujours utiliser un getter/setter dédié ou en ajouter un.

### Clé API ComicVine — chiffrement DPAPI

`get_comicvine_api_key()` / `set_comicvine_api_key()` chiffrent/déchiffrent via `win32crypt.CryptProtectData`/`CryptUnprotectData` (DPAPI Windows, liée au compte utilisateur — illisible sur une autre machine ou un autre compte). Migration automatique et silencieuse d'une éventuelle ancienne valeur en clair au premier `get()`. `set_comicvine_api_key` rend `False` sans rien modifier si le chiffrement échoue (`_dpapi_encrypt` rend `''`) — sinon une clé vide serait enregistrée à la place de la clé saisie —, et `False` aussi si l'écriture échoue ; la fenêtre de la clé (`comicvine_apikey_dialog_qt._ApiKeyDialog._on_validate`/`_on_clear`) lit ce retour et affiche alors `comicvine.api_key_dialog.error_save`, fenêtre ouverte (skill `comicvine-metadata-fetch`). Voir skill `comicvine-metadata-fetch` pour l'usage de cette clé.

### Config icon-toolbar — fichier séparé

`_icon_toolbar_config_file` (attribut de `ConfigManager`) vit dans le même dossier mais dans un fichier JSON à part, lu/écrit par `_read_icon_toolbar_config()`/`_write_icon_toolbar_config()`. Voir skill `icon-toolbar` pour ce qu'il contient et pourquoi il est séparé de `reset_to_defaults()`.

## Journal des erreurs — `%APPDATA%\MosaicView\errors\errors.log`

Garde la trace des erreurs : exception Python non interceptée (y compris dans un slot Qt ou un thread), erreur rattrapée et affichée par `ErrorDialog`, plantage natif (Qt, Pillow, 7-Zip, MuPDF). Actif dans l'exécutable comme en mode développement : l'exe n'a pas de console (`console=False` dans les `.spec`, `sys.stderr` vaut `None`), une telle erreur n'y laisserait aucune trace ; depuis les sources, la trace du terminal est noyée parmi les prints et perdue à la fermeture.

### Partie sans Qt — [modules/qt/error_log.py](../../../modules/qt/error_log.py)

- **Un seul fichier**, `log_path()` = `log_dir()` (`default_config_dir()` + `errors`) + `LOG_NAME` (`errors.log`). On n'y écrit que lorsqu'une erreur se produit : un démarrage sans erreur n'écrit rien. Chaque entrée commence par une ligne `===== <date> — MosaicView <version> — <processus N> — <nature> =====` (`_HEADER_RE`, qui sert aussi à découper le fichier : `split_entries`).
- **`append_entry(title, body, …)`** : écrit une entrée sous le verrou, la force sur le disque (`flush` + `os.fsync` : la fin du process peut être brutale) et fait le ménage si le fichier dépasse `MAX_BYTES`. Ne lève jamais.
- **Verrou entre process** (`_FileLock`) : `msvcrt.locking` sur `errors.lock`, plus un `threading.Lock` pour les threads du process. Deux MosaicView ouverts et les process PDF écrivent dans le même fichier ; toute lecture ou écriture se fait sous le verrou, et seul son détenteur ouvre le journal — ce qui permet au ménage de le remplacer d'un coup (temporaire puis `os.replace`) sans jamais le tronquer. Testé avec deux vrais process.
- **Limites** : `MAX_AGE_DAYS = 15`, `MAX_BYTES = 5 Mo`. `prune()` (au démarrage) retire les entrées trop vieilles ; au-delà de `MAX_BYTES`, les plus anciennes sont retirées jusqu'à `PRUNE_TARGET_BYTES` (80 %), marge qui évite un ménage à chaque écriture suivante.
- **Textes jamais en alphabet pIqaD ou tengwar** : le journal est lu dans le Bloc-notes ou un mail. En-têtes par `_text()` (`_wt()` ; clés `error_log.entry.*`) ; trace mise en forme sous `localization.latin_texts()`, qui fait de même pour un message d'exception traduit (`FileInUseError`…). Traces Python en anglais. Avant le chargement des traductions, un en-tête porte le nom de sa clé.
- **Crochets** (`make_excepthook`, `make_thread_excepthook`, `_Journal.write_error`) : écrivent la trace puis appellent le crochet précédent, donc la trace s'affiche toujours aussi dans le terminal en mode développement (choix assumé). `KeyboardInterrupt` et `SystemExit` ne sont pas journalisés.
- **Erreurs signalées à l'utilisateur** : `log_handled_error(exc)` écrit la pile d'une erreur rattrapée, sans rien faire tant que le journal n'est pas en place. `dialogs_qt.ErrorDialog.__init__` l'appelle avec `sys.exc_info()[1]` : ouverte dans un bloc `except`, une fenêtre d'erreur laisse sa pile au journal ; ouverte depuis le slot d'un worker (exception déjà terminée), rien n'est écrit.
- **Plantages natifs — fichiers de secours** : `faulthandler` exige un fichier ouvert à l'avance, où il écrit sans en-tête possible. Chaque process ouvre donc un fichier caché `.crash_<app|pdf>_<pid>_<version>.tmp` (`_open_crash_buffer`), supprimé à sa fermeture normale (`atexit`). `collect_crash_buffers()` verse la trace d'un fichier resté dans le journal, avec l'en-tête « plantage de l'application » ou « plantage du moteur PDF » (date du plantage = date du fichier), puis le supprime ; vide, il est seulement supprimé ; encore ouvert par un process vivant, Windows refuse sa suppression et il est laissé. Ramassage : par `MosaicView.main` juste après la création de la fenêtre principale (traductions chargées), par `pdf_loading_qt._stopped` dès qu'un process PDF est vu mort, et à la fermeture (`atexit`). Process PDF : `start_child_crash_log()` en tête de `pdf_loading_qt._pdf_persistent_process` et `pdf_probe._probe_process` ; tout nouveau process enfant doit l'appeler aussi. Sa version lui vient de la variable d'environnement `MOSAICVIEW_VERSION`, posée par `start()`.
- **`start(version)`** : appelée une seule fois par `MosaicView.main`, juste après `try_forward_to_running_instance` (une seconde instance, qui se ferme aussitôt, n'y touche pas). Supprime les fichiers `errors_*.log` (un journal par session : ils ne sont plus lus), fait le ménage, ouvre le fichier de secours, installe `sys.excepthook` et `threading.excepthook`. En cas d'échec, rend `None` et l'application démarre sans journal.
- **Lecture** : `read_log_text(folder, newest_first=True, since=None, max_bytes=None)` rend `(texte, nombre d'entrées omises)`, de l'erreur la plus récente à la plus ancienne ; `existing_log_path()` (le journal s'il contient une erreur, pour le sélectionner dans l'Explorateur).

### Fenêtre — [modules/qt/error_log_dialog_qt.py](../../../modules/qt/error_log_dialog_qt.py)

- **`show_error_log(parent)`** : ouvre `_ErrorLogDialog` (une seule à la fois, relue et ramenée au premier plan si elle est déjà ouverte), ou un `InfoDialog` « aucune erreur » (`error_log.no_errors`) si le journal est vide. Appelée par le callback `show_error_log` (`menubar_callbacks_qt.py`), consommé par le menu « À propos » de la barre de menus et du menu contextuel du canvas vide (skill `menu-bar`), et par le bouton « Ouvrir le journal d'erreurs » du mode d'emploi (skill `user-guide`).
- **Contenu** : `read_log_text()` en lecture seule dans un `QPlainTextEdit`, en Consolas (exception assumée à `get_current_font()` : trace technique, alignée en colonnes) ; au-dessus, le dossier du journal, cliquable (Explorateur, journal sélectionné) ; boutons Copier / Envoyer / Fermer.
- **`send_error_log_by_mail(parent)`** : copie `mail_content()` dans le presse-papiers puis ouvre `mailto:` avec la consigne de collage `error_log.mail_body_hint` (par `_wt()`), sur le modèle de `scan_dialog_qt._send_scan_log_by_mail` — un lien `mailto:` ne peut pas joindre de fichier. `mail_content()` ne garde que les erreurs des `MAIL_DAYS` (7) derniers jours, au plus `MAIL_MAX_BYTES` (500 Ko) — le journal peut atteindre 5 Mo, trop lourd à coller dans un mail — et, s'il en a laissé, commence par `error_log.mail_partial` (chemin du journal complet).
- **Mode d'emploi** : paragraphe `help.config_error_log_note` et ses deux boutons dans la section « Fichiers temporaires » (`_build_config_section`, `_error_log_note_html`, skill `user-guide`) ; le journal n'est pourtant pas un fichier temporaire (skill `temp-files`).

### Pièges

- **Ne jamais réassigner `sys.excepthook` / `threading.excepthook` ni rappeler `faulthandler.enable` après `start()`** : le journal ne recevrait plus rien. L'appel `faulthandler.enable()` vers le terminal au tout début de `main` est voulu : il couvre le démarrage avant l'ouverture du journal, et `start()` le redirige vers le fichier.
- **Ne pas lire `sys.stderr` pour décider d'activer le journal** : il vaut `None` dans l'exe, justement là où le journal est indispensable.
- **Signal de langue de la fenêtre débranché une seule fois** (`_disconnect_language`, qui retire le gestionnaire de `_lang_link`) : `closeEvent`, `finished` et `destroyed` y mènent tous, et un second `disconnect` fait afficher un `RuntimeWarning` par PySide (pas une exception, donc pas rattrapé par `try/except`).
- **Fenêtre détruite avec son parent** : ouverte depuis le mode d'emploi, elle est détruite à sa fermeture sans passer par `closeEvent` ni `finished` — d'où le branchement sur `destroyed` et le contrôle `shiboken6.isValid(_dialog)` dans `show_error_log`.

### Modifier

- Limites : les constantes en tête d'`error_log.py` (`MAX_AGE_DAYS`, `MAX_BYTES`, `PRUNE_TARGET_BYTES`) ; pour l'envoi, `MAIL_DAYS` et `MAIL_MAX_BYTES` d'`error_log_dialog_qt.py`.
- Nouvelle nature d'entrée : une clé `error_log.entry.<nom>`, passée par `_text()`.
- Contenu affiché : `read_log_text()` ; envoyé : `mail_content()`.
- Tests : `tests/test_error_log.py` (en-tête, crochets, ménage par âge et par taille, threads et vrais process en même temps, fichiers de secours, aucun caractère pIqaD dans le journal, contenu du mail) et `tests/test_audit7_p3.py` (erreur signalée, appels présents), sans Qt. Toute la suite tourne avec `APPDATA` redirigé vers un dossier temporaire (`tests/conftest.py`, process démarrés par les tests compris) : configuration et journal n'y touchent jamais le vrai `%APPDATA%\MosaicView` (garde-fou `tests/test_check_tests_isolated.py`) — un test ne contourne jamais cette redirection.

## Ajouter un nouveau réglage persistant

1. Ajouter une entrée dans `ConfigManager.DEFAULT_CONFIG` avec sa valeur par défaut.
2. Ajouter un getter/setter typé dédié (suivre le pattern existant, ex. `get_thumbnail_size()`/`set_thumbnail_size()`), pas un accès direct au dict.
3. Si le réglage doit être restauré au démarrage ou remis à zéro par « Réinitialiser l'application » : voir skill `session-restore`.
4. Si le réglage doit avoir un équivalent séparé pour panel2 : ajouter la clé `*_panel2` correspondante + son relais dans `Panel2Config` (voir skill `panels`). Exemple : `bookmarks_enabled` / `bookmarks_enabled_panel2`, lus par `PanelWidget._bookmarks_config()` (skill `bookmarks`).

## Effacer/ouvrir le fichier de config — points d'entrée UI

- **Effacer** : `PanelWidget._clear_config_file()` ([modules/qt/panel_widget.py](../../../modules/qt/panel_widget.py)) — met `get_config_manager().get_config_file_path()` à la Corbeille si présent ; succès ou échec affiché par `PanelWidget._show_clear_result` (échec : `help.config_config_not_cleared` avec la raison, jamais « effacé avec succès »). L'application garde sa configuration en mémoire et la réécrit au prochain réglage modifié et à la fermeture (comportement voulu). Lit le chemin dynamiquement, donc suit automatiquement tout changement d'emplacement futur sans modification de code.
- **Ouvrir le dossier** : `PanelWidget._open_config_folder()` — `subprocess.Popen(["explorer", get_config_manager().config_dir])`.
- Les deux sont câblés dans `menubar_callbacks_qt.py` (clés `clear_config_file`/`open_config_folder`), consommés par la barre de menus ([modules/qt/menubar_qt.py](../../../modules/qt/menubar_qt.py), section "À propos") et le menu contextuel ([modules/qt/context_menus_qt.py](../../../modules/qt/context_menus_qt.py)) — regroupés ensemble, séparés par un `addSeparator()` des commandes de fichiers temporaires.
- **Mode d'emploi** : section dédiée "Fichiers de configuration" (clé `help.config_dir`/`help.config_dir_content`), juste avant la section "Fichiers temporaires" (`help.config_files`). Builder : `_HelpDialog._build_config_dir_section()` ([modules/qt/user_guide_qt.py](../../../modules/qt/user_guide_qt.py)), retraduction : `_retranslate_config_dir_section()`. Cette section contient aussi les notes single-instance et registre Windows (`config_single_instance_note`/`config_registry_note`) : elles décrivent un comportement au démarrage lié à l'identité de l'application, pas des fichiers temporaires, et n'ont donc pas leur place dans la section "Fichiers temporaires".

## Référencements croisés

- **`session-restore`** — lit/écrit la config via `ConfigManager` pour restaurer la fenêtre au démarrage, et `reset_to_defaults` qui remet une partie des réglages à leur valeur par défaut.
- **`temp-files`** — les vrais fichiers temporaires, dans `%TEMP%\MosaicViewTemp\`.
- **`bookmarks`** — les marque-pages (`get_bookmark()`/`set_bookmark()`, dict commun aux deux panneaux) et le mode marque-pages on/off (`bookmarks_enabled` / `bookmarks_enabled_panel2`, un par panneau) sont stockés dans la config.
- **`comicvine-metadata-fetch`** — utilise `get_comicvine_api_key()`/`set_comicvine_api_key()`.
- **`recent-items`** — `recent_files.py`/`recent_dbs.py` sont de simples façades autour des getters/setters `get_recent_files()`/`get_recent_dbs()` etc. de `ConfigManager` ; toute la logique de nettoyage (`clean_recent_files`/`clean_recent_dbs`) vit ici, pas dans les deux modules façade.
- **`icon-toolbar`** — le fichier de config séparé de la barre d'icônes.
- **`panels`** — `Panel2Config`, réglages dédoublés par panneau.
- **`user-guide`** — la section "Fichiers de configuration" du mode d'emploi, et le paragraphe du journal des erreurs dans la section "Fichiers temporaires".
- **`menu-bar`** — l'entrée « Ouvrir le journal d'erreurs » du menu « À propos ».

## Pièges connus

- **Ne jamais coder en dur `%TEMP%\MosaicViewTemp` pour un besoin de config** — ce dossier est purgé par Windows ; toujours `get_config_manager().config_dir`.
- **`_migrate_from_temp()` déplace, ne copie pas** — après une migration réussie, l'ancien fichier dans `%TEMP%` n'existe plus. Ne pas s'étonner de son absence lors d'un debug.
- **Le nettoyage `cleanup_all_temp_files()`** (skill `temp-files`) ne contient aucune exclusion pour `.mosaicview_config.json` : le fichier n'est pas dans le dossier balayé. N'y en ajouter une que si la config revenait dans ce dossier, ce qui est à proscrire.
- **Réglage par panneau : ne jamais le lire via `get_config_manager()` en dur** — la méthode de `ConfigManager` ne renvoie que la valeur de panel1. Passer par l'objet config du panneau (`_renumber_config()`, `_zip_compression_config()`, `_bookmarks_config()`…).
