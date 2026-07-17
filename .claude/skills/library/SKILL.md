---
name: library
description: Localiser ou modifier la Bibliothèque MosaicView (catalogage/recherche multi-répertoires, base .mvdb SQLite, scan incrémental). Utiliser dès qu'une tâche touche à library_db.py, library_window.py, virtual_library_panel.py, un fichier .mvdb, ou au menu "Bibliothèque".
---

# Bibliothèque — MosaicView

La Bibliothèque est un **catalogue** indépendant de la mosaïque : elle indexe en base SQLite tous les comics/ebooks/PDF/vidéos/etc. d'un ou plusieurs répertoires sur disque, permet de les rechercher par métadonnées ComicInfo, et ouvre le fichier choisi dans un panneau (`panel1`/`panel2`, voir skill `panels`). Elle ne touche jamais aux `images_data` d'un panneau réel tant que l'utilisateur n'a pas explicitement ouvert un fichier.

## Fichiers clés

- **`modules/qt/library_db.py`** — moteur SQLite pur (aucun Qt). Classe `LibraryDB` : création/ouverture de la base, scan incrémental du disque, recherche par critères, lecture/écriture `is_read`.
- **`modules/qt/library_window.py`** (~3965 lignes) — la fenêtre `LibraryWindow(QWidget)` : toute l'UI (toolbar, panneau de critères, prévisualisation, tableau de résultats), plus les workers Qt (`_ScanWorker`, `_PreviewWorker`).
- **`modules/qt/library_dialogs.py`** — dialogues annexes (ex. `NewDbDialog` pour créer une nouvelle base, accepte un paramètre `preset_dir` utilisé par le traitement batch de création de bibliothèque — voir skill `batch-library-create`).
- **`modules/qt/virtual_library_panel.py`** — `VirtualLibraryPanel`, le panneau logique (sans UI) que certaines opérations utilisent pour réutiliser le code panneau existant (ex. lecture ComicInfo) sans instancier un vrai `PanelWidget`. Voir skill `panels` section "La bibliothèque comme panneau virtuel".
- **`modules/qt/recent_dbs.py`** — liste des `.mvdb` récemment ouverts (persistée), utilisée par le sous-menu "Bases de données récentes" — voir skill `recent-items` pour ce module et son pendant `recent_files.py`.
- **`modules/qt/menubar_qt.py`** (~ligne 380-440) — construit le menu "Bibliothèque" : ouverture de la fenêtre, liste des DB récentes, sous-menu `build_db_menu` délégué à `LibraryWindow` — voir skill `menu-bar` pour la structure générale de la barre de menus.
- **`modules/qt/panel_widget.py`** (`_prewarm_library`, `_open_library`, `_open_library_db` ~ligne 886-899) — point d'entrée depuis un panneau réel.

## Le fichier `.mvdb`

Une base bibliothèque est un fichier SQLite unique avec l'extension `.mvdb`. Il n'y a pas de format propriétaire : `sqlite3` suffit pour l'inspecter directement. `LibraryDB.rename()` (ligne ~404) est la seule opération qui renomme ce fichier ; `_backup()` (ligne ~285) copie systématiquement l'ancien contenu vers `<db>.mvdb.old` avant toute écriture destructive (scan, set_read, set_master_dir, add_directory, rename).

### Schéma (3 tables)

- **`comics`** — une ligne par fichier indexé. Colonnes fixes (`relative_path` UNIQUE, `filename`, `file_extension`, `file_size`, `file_modified_at`, `indexed_at`, `has_comicinfo`, `can_have_comicinfo`, `is_read`, `page_count`) + tous les champs ComicInfo (`_COMICINFO_FIELDS`, ligne ~60 : `series`, `writer`, `summary`, `characters`, etc.) stockés en TEXT même pour des valeurs numériques (`number`, `volume`, `year`...).
- **`directories`** — les répertoires indexés. `is_master=1` marque le répertoire maître (racine utilisée pour calculer `relative_path` de tous les fichiers, y compris ceux des répertoires additionnels `is_master=0`). Une seule ligne peut avoir `is_master=1`.
- **`meta`** — clé/valeur libre, utilisée aujourd'hui uniquement pour `columns_config` (liste JSON des colonnes visibles du tableau, par base).

### Extensions indexées

Le fichier définit des ensembles disjoints en tête (`_ARCHIVE_EXTS`, `_EBOOK_EXTS`, `_VIDEO_EXTS`, `_AUDIO_EXTS`, `_OTHER_SCAN_EXTS`) qui forment `_ALL_EXTS` (tout ce que le scan ramasse) et `_MEDIA_TYPE_*` (catégories du champ virtuel `media_type`, calculé à la volée depuis `file_extension`, jamais stocké). **Piège** : ajouter une extension à indexer nécessite de la mettre dans le bon set ET dans exactement une catégorie `_MEDIA_TYPE_*` (ou la laisser tomber dans `mt_other` par omission volontaire) — ne jamais dupliquer une extension dans deux catégories, elles doivent rester disjointes (commentaire explicite ligne ~47).

## Scan incrémental (`LibraryDB.scan`)

`scan(progress_callback, stop_event)` (ligne ~417) :
1. Parcourt tous les répertoires enregistrés (`os.walk`), construit `disk_files` (`relative_path → chemin absolu`) en ne gardant que les extensions de `_ALL_EXTS`.
2. Compare à `db_files` (ce qui est déjà en base) : un fichier absent de la DB → **nouveau** (`_index_file`) ; présent mais `file_modified_at` plus récent sur disque → **mis à jour** ; en DB mais absent du disque → **supprimé** (`DELETE`).
3. `_index_file()` : si l'extension est une archive (`_can_have_comicinfo`), tente de lire `ComicInfo.xml` à l'intérieur (`_read_comicinfo_from_archive`, gère `.cbz`/`.zip` via `zipfile`, `.cbt` via `tarfile`, `.cbr` via `rarfile` si installé) et parse via `modules.qt.comic_info.parse_comic_info_xml`. Compte aussi les pages réelles (`_count_pages`) plutôt que de faire confiance au `PageCount` du XML (plus fiable).
4. **`is_read` est préservé** lors d'une mise à jour (relu depuis la ligne existante avant l'`UPDATE`) — un rescan ne remet jamais une lecture à zéro.

Côté UI, `_ScanWorker(QThread)` dans `library_window.py` (ligne ~97) fait tourner `scan()` en arrière-plan et remonte des événements bruts non traduits `(kind, filename, pct)` par signal `progress` — la traduction se fait côté thread Qt principal dans `_on_scan_progress` pour pouvoir être rejouée si la langue change pendant le scan (voir `_disconnect_scan_lang_handler`).

`reindex_files(abs_paths)` (ligne ~575) réindexe une liste ciblée de fichiers sans scan complet — utilisé après modification de métadonnées (ex. édition ComicInfo, conversion vers CBZ) pour éviter un rescan intégral du répertoire maître.

## Recherche par critères

### Modèle de critère

Une recherche est une liste de dicts `{field, op, value, link}` :
- `field` — nom de colonne, whitelist stricte `LibraryDB._SEARCHABLE` (ligne ~619) **plus** deux champs virtuels non stockés : `media_type` (catégorie calculée depuis `file_extension`, clauses dans `_MEDIA_TYPE_CLAUSES`) et `comicvine_format` (ancien/nouveau format d'URL ComicVine, clauses dans `_COMICVINE_FORMAT_CLAUSES`, même logique de reconnaissance de domaine que `comicvine_url_dialog_qt.py::_parse_comicvine_url`).
- `op` — clé de `_OP_MAP` (ligne ~641) : `contains`/`not_contains`/`is`/`empty`/`not_empty`/`eq`/`neq`/`gt`/`lt`/`gte`/`lte`/`between`/`true`/`false`/`before`/`after`.
- `value` — valeur(s) ; pour `between`, un tuple `(v1, v2)`.
- `link` — `'and'` ou `'or'`, relie ce critère au précédent **au sein du même champ** (les critères sur des champs différents sont toujours reliés par `AND` entre eux, jamais par `OR` — voir construction des `group_sqls` ligne ~793).

**Piège whitelist** : `field` et `op` sont strictement filtrés contre ces ensembles avant toute construction SQL (`if field not in self._SEARCHABLE: continue`) — c'est la protection anti-injection. Ne jamais construire une clause SQL avec un nom de champ qui n'a pas d'abord traversé cette whitelist.

**Champs numériques stockés en TEXT** (`_INT_CAST_FIELDS`, ligne ~634 : `number`, `volume`, `year`, `month`, `day`, `page_count`, `file_size`...) sont comparés via `CAST(col AS INTEGER)` pour éviter un tri/comparaison lexicographique (`"9" > "10"` en TEXT).

**Champs booléens texte ComicInfo** (`_YESNO_TEXT_FIELDS` : `black_and_white`, `manga`, `series_complete`) stockent `"Yes"/"No"/"Unknown"/""` et non `0`/`1` — les opérateurs `true`/`false` comparent donc à ces littéraux pour ces champs précis, pas à `1`/`0`.

`search(criteria, order_by, order_asc, progress_callback)` charge tout en mémoire (utilisé pour l'export) ; `search_cursor(...)` retourne `(total, cursor)` pour un chargement par lots sans tout garder en RAM — c'est cette seconde méthode qu'utilise `LibraryWindow._action_open_db`/`_do_search` pour peupler le tableau par paquets de 500 lignes via `QTimer.singleShot(0, ...)` (ne bloque jamais l'event loop Qt).

### Côté UI (`library_window.py`)

- **`_FieldRow`** (ligne ~991) — une ligne de critère dans le panneau de gauche, un par champ recherché possible (`_ALL_FIELDS`). Gère l'ajout de sous-critères liés (`_SubField`, ET/OU), les cases spéciales media_type (`_MediaTypeSubField`), et expose `to_criteria()` qui produit la liste de dicts attendue par `LibraryDB.search*`.
- **`_do_search()`** (ligne ~1871) — agrège `to_criteria()` de tous les `_FieldRow` actifs (`has_value()`) et appelle `search_cursor`.
- **Deux tableaux superposés** : `self._table` (résultats complets) et `self._filter_table` (résultats d'une recherche), un seul visible à la fois (`_filter_active`, propriété `_active_table`). Permet de revenir instantanément à la liste complète sans reconstruire quoi que ce soit.

## Cycle de vie de la fenêtre

`LibraryWindow` est un **singleton module-level** : `_library_window` (ligne ~66), créé une seule fois par `open_library_window(parent_panel=None, prewarm=False)`.

- **`prewarm=True`** — construit la fenêtre (35 widgets) sans l'afficher (`event.ignore()` dans `showEvent` tant que `_prewarmed`). Déclenché 2 secondes après le démarrage de chaque panel1 (`PanelWidget.__init__` → `QTimer.singleShot(2000, self._prewarm_library)`, **seulement si panel primaire**, voir skill `panels`) pour que la première ouverture réelle soit instantanée.
- **`closeEvent`** — ne détruit jamais la fenêtre : `event.ignore(); self.hide(); self._prewarmed = True`. Rouvrir rappelle juste `showMaximized()`. La DB ouverte (`self._db`) reste chargée en arrière-plan même fenêtre cachée — c'est pourquoi `menubar_qt.py` construit son sous-menu "Base de données" via `_library_window.build_db_menu()` (état réel) plutôt que de dépendre de la visibilité.
- **Maximisée par défaut** au premier affichage réel (`_on_first_show` → `showMaximized`), split 3 colonnes redimensionné ensuite (`_debug_sizes`, tailles `[280, 260, reste]`).
- **`_parent_panel`** — le `PanelWidget` (ou `None`) depuis lequel la fenêtre a été ouverte ; utilisé par `_open_in_mosaicview()` pour savoir où charger le fichier choisi.

## Ouvrir un comic depuis la Bibliothèque vers un panneau

`_open_in_mosaicview()` (ligne ~2567) : récupère le premier id sélectionné, résout son chemin absolu (`LibraryDB.get_absolute_path`, reconstruit `master_dir + relative_path` via `safe_join` — protection anti path-traversal), puis sur `self._parent_panel` : ferme le fichier courant s'il y en a un (`panel._close_file()`), charge le nouveau (`panel._load_files([abs_path])`), et mémorise `panel._library_window = self` (permet au panneau de savoir qu'il a été ouvert depuis la bibliothèque, ex. pour un futur retour). **Ne fait rien si `_parent_panel` est `None`** (fenêtre ouverte sans panneau associé, cas théorique).

Trois autres actions sur la sélection : `_open_in_explorer()` (réutilise `_explorer_select`, voir skill `explorer-select`), `_open_with_default()` (`os.startfile`), et double-clic/Entrée sur une ligne du tableau routent aussi vers `_open_in_mosaicview`.

## Ajouter un nouveau champ de recherche/colonne

1. Ajouter la colonne dans `_CREATE_COMICS` (`library_db.py`) si c'est un nouveau champ ComicInfo — l'ajouter aussi à `_COMICINFO_FIELDS` pour qu'`_index_file` le peuple depuis le XML.
2. L'ajouter à `LibraryDB._SEARCHABLE` pour qu'il soit accepté par `search`/`search_cursor` (whitelist anti-injection — une omission ici rend le champ silencieusement ignoré, pas une erreur).
3. Si le champ est numérique stocké en TEXT, l'ajouter à `_INT_CAST_FIELDS`. Si c'est un booléen texte façon ComicInfo (`Yes`/`No`), l'ajouter à `_YESNO_TEXT_FIELDS`.
4. Côté UI : ajouter l'entrée à `_ALL_FIELDS`/`_ALL_COLUMNS`/`_DEFAULT_COLUMNS` (constantes en tête de `library_window.py`) pour qu'une `_FieldRow` et une colonne de tableau soient générées automatiquement.
5. Toute base `.mvdb` existante ne verra la nouvelle colonne remplie qu'après un rescan (`_action_scan`) — `CREATE TABLE IF NOT EXISTS` ne rajoute pas de colonne à une table déjà créée avec un schéma plus ancien (pas de migration `ALTER TABLE` en place aujourd'hui). Si une migration de schéma s'avère nécessaire, le signaler avant de l'implémenter plutôt que de supposer que `IF NOT EXISTS` suffit.

## Ce qui n'est PAS géré ici

- L'ouverture d'un `.mvdb` **double-cliqué dans l'Explorateur Windows** (association de fichier) : routée par le mécanisme single instance (`MosaicView.py::main()` → `_open_associated_path` → `open_library_window` + `_action_open_db`), y compris quand une instance MosaicView tourne déjà — voir skill `single-instance`. Côté bibliothèque, le point d'entrée reste `_action_open_db(filepath)`.
- L'édition des métadonnées ComicInfo d'un comic (`_action_edit_comicinfo`/`_open_comicinfo_editor`, ligne ~3479) — voir skill `comicinfo-metadata-editor` — et le scraping ComicVine (`_action_fetch_metadata`) — voir skill `comicvine-metadata-fetch` — délèguent à des modules dédiés déjà existants — pas un sujet propre à ce skill sauf pour le point de réindexation après écriture (`_write_comicinfo_and_reindex`, appelle `LibraryDB.reindex_files`).
- La conversion CBR/CB7→CBZ (`_convert_file_to_cbz`) réutilise le pipeline de conversion existant de l'appli ; le point d'intégration bibliothèque est seulement `_refresh_row_after_convert` (met à jour la ligne DB + `remove_by_id` de l'ancien id si le chemin change).
- Toutes les règles UI Qt obligatoires (non-modale, thème, langue à la volée, `_wt()` pour le titre, tooltips via `OverlayTooltip`) s'appliquent intégralement à `LibraryWindow` et ses dialogues (`NewDbDialog`, etc.) — voir CLAUDE.md, pas un mécanisme spécifique à la bibliothèque.
