---
name: drag-and-drop
description: Localiser ou modifier le drag & drop de la mosaïque MosaicView — réordonnancement intra-panneau, déplacement inter-panneaux, drag-out vers l'Explorateur (CF_HDROP), drop entrant externe. Utiliser dès qu'une tâche touche à MosaicCanvas.dropEvent/_start_drag ou drop_handler_qt.py.
---

# Drag & drop — MosaicView

La grille de vignettes (`MosaicCanvas`, voir skill `mosaic-thumbnails`) a **quatre flux de drag & drop distincts**, tous gérés par la même paire `dragEnterEvent`/`dropEvent` mais discriminés par le contenu du `QMimeData`. Il existe aussi des D&D **locaux et indépendants** ailleurs dans l'appli (réordonnancement de la colonne d'icônes, frames d'un GIF animé, lignes de la Bibliothèque) — voir tout en bas, ne pas les confondre avec celui-ci.

## Fichier clé

**`modules/qt/mosaic_canvas.py`** — tout le D&D de la mosaïque vit dans cette seule classe `MosaicCanvas` :
- `_start_drag()` (~ligne 1549) — construit et lance le `QDrag` sortant.
- `dragEnterEvent` / `dragMoveEvent` / `dragLeaveEvent` / `dropEvent` (~ligne 1751-1921) — réception.
- `_calc_insert_visual()` / `_draw_drop_indicator()` / `_clear_drop_indicator()` — indicateur visuel (ligne rouge + triangles) de position d'insertion.
- `_reorder_items_after_drop()` (~ligne 1191) — replace les `ThumbnailItem` existants sans tout recréer (évite un freeze de `render_mosaic()`).

Les callbacks appelés par `MosaicCanvas` sont câblés depuis **`modules/qt/panel_widget.py`** juste après la création du canvas (~ligne 316-324) — c'est là qu'il faut regarder pour comprendre *ce que fait* un drop, `mosaic_canvas.py` ne contenant que le mécanisme Qt bas niveau + la logique de réordonnancement intra-panneau.

## Les quatre flux

### 1. Réordonnancement intra-panneau (drag interne)

Glisser une ou plusieurs vignettes **à l'intérieur du même panneau** pour changer leur ordre.

- Mime type : `application/x-mosaicview-indices` (liste d'index réels séparés par des virgules).
- Toujours accompagné de `application/x-mosaicview-panel` (id Python du canvas source, `str(id(self))`) — sert à distinguer drop intra-panneau vs inter-panneaux dans `dropEvent`.
- **Bloqué si sous-dossiers présents** (`_has_subdirectory_structure_callback()` → `True`, c'est-à-dire `orig_name` contient `/`) : le mime `-indices` n'est alors *pas* ajouté au `QMimeData`, seul `-panel` l'est. Résultat : pas d'indicateur rouge, pas de réordonnancement ; si l'utilisateur droppe quand même à l'intérieur du même panneau, `_warn_flatten_dnd_callback()` affiche un avertissement (`_WarnDialog`, clé `messages.warnings.drag_drop_disabled_in_subdirectory`).
- Le drop appelle `dropEvent` → branche `is_inter_panel == False` (~ligne 1852) : retire les entrées de `st.images_data`, recalcule la position d'insertion dans la liste restante, réinsère, `sync_pages_in_xml_data`, déclenche `_renumber_after_drop_callback` (→ `PanelWidget._renumber_no_save`, voir skill `renumbering`) qui renumérote ou non selon `renumber_mode` du panneau (0=OFF ne fait rien, 1=auto, 2=simple — mêmes trois modes que le bouton dédié de la statusbar), recalcule `selected_indices` par **identité d'objet** (`id(e)`, pas par nom — la renumérotation a pu renommer les fichiers), `save_state_callback`, puis `_reorder_items_after_drop()` pour l'affichage.
- Les entrées non-image (fichiers non gérés en tant que page) ne participent jamais au réordonnancement — filtrées avant traitement.

### 2. Déplacement inter-panneaux (panel1 ↔ panel2)

Glisser des vignettes d'un panneau vers l'autre en split-view (voir skill `panels`).

- Détecté dans `dropEvent` en comparant l'id du mime `application/x-mosaicview-panel` reçu à `str(id(self))` du canvas cible (~ligne 1799-1803).
- Nécessite `self._inter_panel_drop_callback` non `None` (câblé uniquement côté `PanelWidget`, jamais sur une mosaïque isolée).
- **Sous-dossiers = rejet dans les deux sens** :
  - Si la source a des sous-dossiers → pas de mime `-indices` → `dropEvent` cible rejette avec `_warn_flatten_dnd_callback()` et signale à la source (`src_canvas._inter_panel_warn_shown = True`) de ne pas *aussi* afficher son propre avertissement en fin de `_start_drag()`.
  - Si la cible a des sous-dossiers (`_has_subdirectory_structure_callback()` de la cible) → même rejet, même signalisation à la source.
- Sinon, appelle `PanelWidget._on_inter_panel_drop(dragged_reals, insert_real, source_id)` (`panel_widget.py` ~ligne 1971) :
  1. Retrouve le `PanelWidget` source via `mw._all_panels()` en comparant `id(p._canvas)` à `source_id`.
  2. Copie chaque entrée déplacée (`_copy_entry`) — **copie indépendante des `bytes`**, widgets UI liés à l'ancien panneau remis à `None` (`name_entry`, `img_id`, `qt_pixmap_large`, etc.), `img` (PIL Image) remis à `None` pour être rechargée à la demande. **Pourquoi** : chaque panneau doit posséder ses propres données ; fermer le panneau source ne doit jamais affecter les bytes désormais dans la cible.
  3. Retire les entrées du panneau source, renumérote côté source (`source_panel._renumber_no_save`), sauvegarde.
  4. Insère les copies dans le panneau cible à `insert_real`, renumérote côté cible, sauvegarde.
  5. Les deux renumérotations passent par `_renumber_no_save`, dont le comportement dépend de `renumber_mode` (0=OFF/1=auto/2=simple, voir skill `renumbering`) : en mode OFF `finish()` est appelé directement sans rien renuméroter, en mode simple c'est synchrone, en mode auto ça peut devenir **asynchrone** (dialogue non-modal "1ère page multiple" si la 1ère page déplacée a un ratio large/haut suspect). La finalisation (`_finalize_source`/`_finalize_target`) passe donc toujours par `on_done=`, jamais exécutée en séquence directe — ça doit marcher que la renumérotation ait été synchrone, asynchrone, ou sautée.
- Le canvas source marque `_drop_was_internal = True` pour que `_start_drag()` sache nettoyer son dossier temporaire CF_HDROP (voir flux 3) au lieu de le laisser pour un drop externe.

### 3. Drag-out vers l'Explorateur Windows (CF_HDROP)

Glisser des vignettes **hors de MosaicView**, vers l'Explorateur ou une autre application qui accepte des fichiers.

- Construit dans `_start_drag()` (~ligne 1587-1615) : chaque entrée sélectionnée est écrite dans un dossier temporaire (`get_mosaicview_temp_dir()/drag_<uuid>/`), les `QUrl.fromLocalFile(...)` résultants sont posés sur `mime.setUrls(urls)`.
- **Piège chemin court Windows** : `os.makedirs` peut renvoyer un chemin 8.3 (`PROPRI~1`) selon le profil utilisateur ; corrigé explicitement via `GetLongPathNameW` avant de construire les URLs (sinon l'appli cible reçoit un chemin qui peut ne pas résoudre proprement).
- `drag.exec(Qt.CopyAction | Qt.MoveAction)` — toujours autorisé même s'il y a des sous-dossiers ou des non-images dans la sélection (seul le mime `-indices`, donc le réordonnancement *interne*, est bloqué dans ce cas).
- Nettoyage : si le drop a atterri **à l'intérieur de MosaicView** (`_drop_was_internal`), le dossier temporaire est supprimé aussitôt (les fichiers CF_HDROP ne servaient à rien). Sinon (drop vers l'extérieur), le dossier est laissé intact — la cible externe lit le fichier *après* le retour de `drag.exec()`, il ne faut donc jamais le supprimer immédiatement ; il sera nettoyé au prochain démarrage de l'appli.
- Vignettes grisées pendant le drag (feedback visuel façon ancien tkinter) : pixmap converti en niveaux de gris le temps du `drag.exec()`, restauré après — avec un `try/except RuntimeError` car les `ThumbnailItem` peuvent avoir été détruits entre-temps si `render_mosaic()` a tourné pendant le drag (cas inter-panneaux).

### 4. Drop entrant de fichiers/dossiers/URLs externes

Glisser depuis l'Explorateur (fichiers, dossiers, `.mvdb`, `.url`, `.webloc`) ou depuis un navigateur (image, lien HTTP/HTTPS) **vers** la mosaïque.

- Branche `elif mime.hasUrls() and event.source() is not self` (~ligne 1893) — le test `event.source() is not self` exclut explicitement le cas où on droppe une image *depuis MosaicView vers MosaicView* en passant par le mécanisme CF_HDROP (ça, c'est un drag-out suivi d'un drop interne, déjà couvert par les flux 1/2).
- Sépare `local_paths` (fichiers/dossiers locaux) et `web_urls` (`http://`/`https://`).
- **Fichiers/dossiers locaux** → `self._load_callback(local_paths, from_drop=True)` = `PanelWidget._handle_dropped_paths` (`panel_widget.py` ~ligne 2082) :
  - `.mvdb` → ouvre la Bibliothèque sur cette base (voir skill `library`).
  - `.url` / `.webloc` → extrait l'URL interne, si HTTP(S) part sur le chemin `_resolve_and_download` (import web, voir plus bas).
  - Le reste → délégué à `handle_dropped_paths()` dans `modules/qt/drop_handler_qt.py` :
    - Mélange dossiers + fichiers → message d'avertissement, rien n'est chargé.
    - Uniquement des dossiers → `_show_batch_drop_dialog` (voir skill `batch-processing` : conversion CBR/CB7/CBT/PDF/IMG en masse, import métadonnées, recompression ZIP, création de bibliothèque).
    - Uniquement des fichiers → `load_files_callback(files, from_drop=True)`, le chargeur normal d'archive/image.
- **URLs web** → `self._web_import_callback` = `PanelWidget._handle_dropped_web_urls`. Si le mime contient aussi `text/html` (cas fréquent d'un drop depuis navigateur), tente d'abord d'extraire le `<img src="...">` réellement droppé via `_extract_single_img_src` (`web_import_qt.py`) plutôt que l'URL de la page entière. Voir skill `web-import` pour la résolution/le téléchargement qui suit (`_resolve_and_download`), et pour les deux autres façons d'importer depuis le web (saisie manuelle d'URL, drop d'un fichier `.url`/`.webloc`).
- Ce même flux 4 existe **en double, en plus simple**, à deux autres niveaux (mêmes mime types, mêmes callbacks finaux) :
  - `PanelWidget.dragEnterEvent`/`dropEvent` (`panel_widget.py` ~ligne 2069-2081) — drop n'importe où sur le panneau (pas seulement sur le canvas).
  - `MainWindow.dragEnterEvent`/`dropEvent` (`MosaicView.py` ~ligne 744-755) — drop n'importe où sur la fenêtre, routé vers `self._active_panel._handle_dropped_paths`.
  - Dans les trois cas, `event.mimeData().hasFormat("application/x-mosaicview-indices")` fait `return` sans rien faire — évite qu'un drag interne raté à la position exacte du canvas ne remonte jusqu'à ces gestionnaires plus larges.

## Indicateur visuel de position d'insertion

Pendant un drag interne (`dragMoveEvent`, flux 1/2 uniquement — pas dessiné pour un drop de fichiers externes) :
- `_calc_insert_visual(scene_pos)` convertit la position souris en index d'insertion (`visual_idx`), en tenant compte de la colonne/ligne et de la moitié gauche/droite de la cellule survolée.
- `_draw_drop_indicator()` dessine une ligne rouge verticale avec deux triangles (haut/bas) à la position calculée — couleur `DROP_COLOR`, épaisseur/taille en dur (`s = 9`, correspond à `_ARROW_SIZE` historique de l'ancien `drag_drop.py` tkinter).
- Auto-scroll : si la souris approche du haut/bas du viewport pendant le drag (`_SCROLL_ZONE = 60` px), un `QTimer` (`_drag_scroll_timer`) scrolle automatiquement, vitesse proportionnelle à la proximité du bord (`_SCROLL_MAX = 50` px/tick).
- Tout est nettoyé dans `dragLeaveEvent` et en fin de `dropEvent`/`_start_drag`.

## Modifier ou étendre ce mécanisme

- **Ajouter un nouveau type de fichier accepté en drop externe** : modifier `PanelWidget._handle_dropped_paths` (extension → traitement spécifique) ou `handle_dropped_paths()`/`_show_batch_drop_dialog()` dans `drop_handler_qt.py` si c'est un traitement par lot de dossier.
- **Changer ce qui est autorisé/bloqué en présence de sous-dossiers** : toute la logique passe par `_has_subdirectory_structure_callback` (défini par `PanelWidget._has_subdirectory_structure`, teste `'/' in orig_name`) — un seul point à modifier pour changer la condition, mais bien vérifier les **trois** endroits qui la consultent dans `mosaic_canvas.py` (drag interne au départ, drop intra-panneau, drop inter-panneaux côté cible).
- **Changer le comportement d'insertion inter-panneaux** (copie profonde, clés UI réinitialisées) : `PanelWidget._on_inter_panel_drop`, fonction `_copy_entry` — si un nouveau champ d'`entry` référence un widget/objet Qt lié au panneau (comme `name_entry`, `img_id`...), l'ajouter à `_UI_KEYS` pour qu'il soit remis à `None` lors de la copie, sinon risque de crash (wrapper Qt du mauvais panneau).
- **Toucher à la renumérotation post-drop** : ne pas dupliquer la logique ici, voir skill `renumbering` — `_renumber_after_drop_callback`/`_renumber_no_save` est le point d'entrée commun aux flux 1 et 2, potentiellement asynchrone (dialogue non-modal).
- **Ajouter un avertissement/dialogue lié au D&D** : suivre le pattern `_WarnDialog(...).show_nonmodal()` déjà utilisé par `_warn_flatten_required_dnd` — respecter les 8 règles UI Qt obligatoires du `CLAUDE.md` racine (non-modal, thème, langue, police, `_wt()` pour le titre...).
- **Débogage** : la règle CLAUDE.md "toujours diagnostiquer avec des prints avant d'appliquer un fix" s'applique particulièrement bien ici — un bug de D&D implique presque toujours un flux d'événements Qt (`dragEnterEvent`→`dragMoveEvent`→`dropEvent`, ou `mousePressEvent`→`mouseMoveEvent`→`_start_drag`) où l'ordre exact des event handlers déclenchés doit être observé avant de corriger.

## D&D locaux, indépendants — ne pas confondre

Trois autres endroits de l'appli ont leur propre petit mécanisme de D&D, sans rapport avec celui de la mosaïque (pas de mime `-indices`/`-panel`, pas de callbacks `PanelWidget`) :

- **Colonne d'icônes** (`modules/qt/icon_toolbar_qt.py`, `IconLabel`) — réordonnancement des boutons par glisser-déposer, mime type `IconLabel.MIME_TYPE` propre. Voir skill `icon-toolbar`.
- **Fenêtre GIF animé** (`modules/qt/animated_gif_dialog_qt.py`) — réordonnancement des frames, même pattern local (`self.MIME`).
- **Bibliothèque** (`modules/qt/library_window.py`) :
  - Drag-*out* d'un ou plusieurs comics sélectionnés dans la table vers l'extérieur (CF_HDROP, `_do_drag`) — permet par exemple de glisser un comic de la Bibliothèque vers un panneau MosaicView (traité côté panneau comme un drop de fichier externe normal, flux 4 ci-dessus).
  - Drop-*in* limité aux fichiers `.mvdb` (ouvre cette base). Voir skill `library`.
