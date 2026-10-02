---
name: page-resize
description: Localiser ou modifier le redimensionnement des pages de MosaicView (dimensions personnalisées/pourcentages, détection des pages multiples par clustering, fenêtre des dimensions aberrantes). Utiliser dès qu'une tâche touche à resize_dialog_qt.py, ResizeDialog, ou OutlierDialog.
---

# Redimensionnement des pages — MosaicView

Fenêtre dédiée qui redimensionne les images **sélectionnées** de la mosaïque, soit vers des dimensions personnalisées (largeur/hauteur en pixels, avec conservation optionnelle du ratio), soit par un pourcentage de réduction/agrandissement prédéfini. Fonctionnalité la plus élaborée du projet en termes d'heuristique automatique : elle inclut une **détection automatique des pages multiples** (planches doubles/triples scannées en un seul fichier) qui adapte le facteur de redimensionnement page par page plutôt que d'appliquer une taille uniforme brutale.

## Deux fenêtres, un seul fichier — `modules/qt/resize_dialog_qt.py`

- **`ResizeDialog`** (`QDialog`) — la fenêtre principale : infos actuelles (poids total, dimensions si toutes identiques), section dimensions personnalisées (champs largeur/hauteur liés par le ratio, checkbox de détection multi-page), section pourcentages (deux colonnes réduction/agrandissement, radios avec poids estimé affiché par option).
- **`OutlierDialog`** (`QDialog`) — fenêtre **secondaire**, ouverte uniquement si la détection multi-page trouve des pages aux dimensions aberrantes ; demande à l'utilisateur de choisir un multiplicateur pour chacune, une par une, avec vignette.
- **`cluster_and_find_reference(dimensions, tolerance=0.10)`** — la logique pure de clustering, indépendante de Qt.
- **`reduce_selected_images_size_qt(parent, callbacks)`** — point d'entrée public.

## Les deux modes de redimensionnement

**Mutuellement exclusifs** — saisir quoi que ce soit dans les champs largeur/hauteur décoche automatiquement les radios de pourcentage (`_on_width_changed`/`_on_height_changed`), et inversement sélectionner un pourcentage vide les champs (`_on_pct_selected`).

### 1. Dimensions personnalisées

Deux `QLineEdit` (largeur/hauteur, `QIntValidator(1, 99999)`). **Seulement si toutes les images sélectionnées ont exactement les mêmes dimensions d'origine** (`self._same_dim`), les deux champs sont liés par le ratio d'aspect : modifier l'un recalcule l'autre automatiquement (`new_h = int(new_w / self._aspect)`) pour ne jamais déformer l'image. Si les dimensions diffèrent entre les images sélectionnées, les champs restent indépendants (aucun ratio de référence unique n'existe) et un avertissement rouge s'affiche (`different_dimensions_warning`) tant que la détection multi-page n'est pas activée.

Il est possible de ne renseigner **qu'un seul** des deux champs (largeur seule ou hauteur seule) — le worker recalcule alors l'autre dimension au ratio de l'image traitée, page par page (voir section worker).

### 2. Pourcentages prédéfinis

Deux colonnes de radios : réduction (10/20/25/33/50/75/90 %) et agrandissement (10/20/25/33/50/75/100 %), plus une option "0 % (aucune modification)" cochée par défaut qui désactive le bouton OK. Chaque radio affiche un **poids de fichier estimé** (`pct_est`, calculé par `(facteur)² × poids_total_actuel_Mo` — approximation basée sur le fait que le poids d'une image scale approximativement au carré du facteur de redimensionnement linéaire, pas une mesure réelle après compression).

## Détection automatique des pages multiples — la checkbox `multi_page_width`

**Le cœur de ce skill.** Visible uniquement s'il y a plus d'une image sélectionnée (`self._nb_files > 1`), cochée par défaut et **son état est mémorisé globalement entre deux ouvertures de la fenêtre** dans la session (`_multi_page_checkbox_state`, variable module-level, pas persistée sur disque — remise à `True` par défaut à chaque redémarrage de l'application). Tooltip explicatif (`multi_page_width_tooltip`, affiché via `OverlayTooltip` — voir skill `qt-tooltips`) : *"Active la détection automatique des pages multiples : double page, triple page, etc. Le coefficient multiplicateur est calculé automatiquement selon le ratio de chaque page, qu'elle soit en largeur (pages côte à côte) ou en hauteur (pages empilées)."*

**Ne s'applique qu'en mode dimensions personnalisées avec plus d'une image** (`use_custom_dim and multi_page and self._nb_files > 1`) — sans effet en mode pourcentage, où chaque image est simplement mise à l'échelle par le même facteur quelle que soit sa taille d'origine.

### L'algorithme — `cluster_and_find_reference` (`resize_dialog_qt.py`)

Reçoit la liste de toutes les largeurs (puis, séparément, toutes les hauteurs) des images sélectionnées, et :

1. **Regroupe** les dimensions similaires à ±10 % (`tolerance=0.10`) en clusters — parcourt les dimensions triées, rattache chaque valeur au premier cluster existant dont elle est à moins de 10 % de la moyenne, sinon crée un nouveau cluster.
2. **Identifie le cluster principal** (`main_cluster`) : celui qui regroupe le plus grand nombre total de pages (`cluster_total_count`, pondéré par `Counter` — une dimension qui revient 5 fois compte 5 fois, pas 1). Sa moyenne devient la **dimension de référence** (`reference`).
3. **Détecte les outliers** : tout cluster dont le ratio par rapport à la référence est `< 0.75` ou `> 2.5` est considéré comme aberrant — trop loin d'un multiple entier simple pour qu'un multiplicateur automatique soit fiable. Ces dimensions sont exclues du mapping automatique et redirigées vers `OutlierDialog` pour un choix manuel.
4. **Calcule un multiplicateur entier par cluster non-aberrant** (`round(raw_ratio)`) — typiquement `1` pour une page simple, `2` pour une planche double, `4`/`8` pour des cas extrêmes. Le résultat `dim_to_multiplier` mappe chaque dimension exacte rencontrée vers son multiplicateur.

**Exécuté séparément pour les largeurs et pour les hauteurs** (`reference_width`/`width_mapping`/`width_outliers` et leurs équivalents hauteur) — une image peut être un outlier en largeur sans l'être en hauteur, ou inversement, gérés indépendamment.

### Application du multiplicateur — dans `_ResizeWorker.run()`

Pour chaque image, si la détection multi-page est active : le multiplicateur de cette image (`width_mapping.get(img.width, 1.0)`, ou le choix manuel de l'utilisateur pour un outlier — voir section suivante) est appliqué à la dimension cible commune : `new_w = int(target_width * width_multiplier)`. Une planche double détectée (multiplicateur 2) reçoit donc le double de la largeur cible saisie par l'utilisateur pour une page simple — le but étant que toutes les pages, une fois redimensionnées, affichent le même "grain" par page individuelle malgré des tailles de fichier source différentes.

**Cas `width_multiplier is None`** (utilisateur a choisi "Ne pas redimensionner" pour un outlier dans `OutlierDialog`, voir section suivante) : `img.close(); continue` — cette image est laissée totalement intacte, sautée de la boucle sans modification.

## Fenêtre secondaire — `OutlierDialog`

Ouverte automatiquement (`_on_ok` → `if outlier_pages: OutlierDialog(...).ask_async(...)`) seulement si la détection multi-page est active **et** qu'au moins une page a été classée comme aberrante par `cluster_and_find_reference`. Non-modale (`ask_async`, callback plutôt que valeur de retour synchrone — règle UI n°4), affiche une carte par page aberrante avec :

- Le nom du fichier et sa vignette (150×150, réutilise `entry["large_thumb_pil"]` si déjà en cache, sinon redécode depuis les bytes).
- Une liste de `QRadioButton` par dimension aberrante (largeur et/ou hauteur séparément) proposant des multiplicateurs plausibles (`[1, 2]`, plus `4` si le ratio dépasse 2.5, plus `8` si il dépasse 5 — voir `possible_mults`), plus toujours une option "Ne pas redimensionner (garder Npx)".
- Le bouton "Continuer" ne s'active (`_check_all_selected`) que lorsque **toutes** les pages aberrantes ont un choix fait sur **chacune** de leurs dimensions concernées — impossible de valider un choix partiel.
- Annuler (`_on_cancel`) referme `OutlierDialog` **sans** lancer le redimensionnement — l'utilisateur reste dans `ResizeDialog`, rien n'est appliqué, aucun état undo n'est poussé à ce stade (le `save_state()` n'intervient que dans `_finish_resize`, appelé seulement après un choix validé ou en l'absence d'outliers).

Les choix (`{page_name: {"width_mult": int|0|None, "height_mult": ...}}`, `0` signifiant "ne pas redimensionner" côté UI, transformé en `None` côté worker) sont transmis à `_finish_resize` puis au worker, qui les consulte **prioritairement** sur le mapping automatique (`if user_choice and user_choice.get("width_mult") is not None: ... else: width_mapping.get(...)`).

## Worker asynchrone — `_ResizeWorker` (`resize_dialog_qt.py`)

Pattern proche de `rotate-flip` (`QThread`, overlay de progression + bouton Annuler sur le canvas, anti-GC implicite via `worker_ref`) :

- **Redimensionnement PIL** : toujours `Image.Resampling.LANCZOS`, quel que soit le mode (dimensions personnalisées ou pourcentage) — pas de choix de filtre exposé à l'utilisateur.
- **Enregistrement par `save_image_to_bytes`** (`entries.py`, skill `apply-image-operation`) : `_ResizeWorker.run` pose `entry["img"] = img_resized` puis appelle `save_image_to_bytes(entry)`, comme toutes les autres opérations — format choisi d'après l'extension (JPEG/JFIF/PJPEG/PJP, PNG, WebP, AVIF, GIF, BMP, TIFF, ICO avec ses tailles d'origine), DPI repris de `entry["dpi"]` ou de `img.info` (conservé par `resize`), dimensions mémorisées mises à jour. **Ne jamais réintroduire un `save()` local avec un format déduit du nom** : une extension absente d'une liste locale retombait sur JPEG (`.tif`/`.avif`/`.ico` devenaient du JPEG sous leur extension d'origine, ou la page n'était pas traitée en silence quand l'image avait de la transparence).
- **GIF animé** : `transform_animated_gif` (`image_ops.py`, skill `rotate-flip`) redimensionne chaque frame à la taille calculée sur la première.
- **Préservation de la qualité JPEG d'origine** : `save_image_to_bytes` relit `detect_jpeg_quality(entry["bytes"])` (version `entries.py`, moindres carrés) avant que les bytes ne soient remplacés, et la réapplique à la sauvegarde — un resize ne dégrade pas davantage la compression déjà choisie pour cette image.
- **Préservation du DPI** : lu par `save_image_to_bytes` depuis `entry.get("dpi")` ou les métadonnées PIL de l'image (aucun DPI n'est inventé si la page n'en a pas), réappliqué tel quel à la sauvegarde — les dimensions physiques déclarées de l'image suivent le changement de résolution en pixels.
- **Format de sortie toujours identique au format d'origine** (déduit de l'extension du nom de fichier) — pas de conversion de format pendant un resize, contrairement à `convert_image_data` (skill non couvert ici, voir `image_ops.py::convert_image_data`).
- Invalidation cache — variante intermédiaire, ni tout à fait (A) ni (B) du skill `apply-image-operation` : `img`/`_hash` remis à `None`, `large_thumb_pil` fermé et remis à `None`, `qt_pixmap_large`/`qt_qimage_large` retirés du dict (`pop`, pas juste `= None`), puis **`build_qimage_for_entry(entry)` est appelé explicitement dans le thread worker** pour précalculer la vignette Qt en arrière-plan avant même la fin du traitement — optimisation propre à ce fichier, absente des autres opérations d'image du projet, qui évite de reconstruire la vignette plus tard dans le thread UI au moment du premier `paintEvent`.
- **`update_page_entries_in_xml_data(..., emit_signal=False)`** — signal explicitement coupé pendant la boucle (contrairement à d'autres opérations qui laissent le signal par défaut), probablement pour éviter une rafale de rafraîchissements de l'onglet métadonnées à chaque image d'un lot potentiellement volumineux ; un seul `metadata_pages_signal.emit(state)` est déclenché après coup dans `on_finished` (le state limite le rafraîchissement à l'onglet Métadonnées de ce panneau, voir skill `tabs`).

### Annulation en cours de lot — restauration manuelle des bytes, pas un rollback global

Contrairement à `rotate-flip` qui utilise `rollback_to_current_state_qt` (skill `undo-redo`), l'annulation ici restaure **manuellement** les bytes d'origine de chaque entrée déjà modifiée depuis un dict `original_bytes` capturé **avant** le lancement du worker (`{id(e): e["bytes"] for e in selected_entries}`, dans `_finish_resize`), puis dépile le point undo poussé par le `save_state()` initial (`pop_last_state(state)`, skill `undo-redo`) **seulement s'il a réellement été poussé** — `_finish_resize` compare par identité `history_top(state)` (`undo_redo.py`) avant et après ce `save_state()` et transmet le résultat à `_start_resize_worker` (`pushed_before`). Dans le cas normal, le sommet de l'historique décrit déjà l'état courant et ce `save_state()` n'ajoute rien : dépiler retirerait alors la dernière action de l'utilisateur de l'historique (piège détaillé dans le skill `undo-redo`). Restaure enfin `state.modified` à sa valeur d'avant.

Elle se fait **en deux temps**, comme dans `rotate-flip` :
- `_cancel()` (clic sur Annuler, ou fermeture du fichier) pose le flag du worker et `cancel_requested[0]`, oublie le worker (`worker_ref[0] = None`) et masque l'overlay, **sans rien restaurer** ;
- `_restore_after_cancel()` fait la restauration une fois le worker sorti de sa boucle : appelée par `on_cancelled`, et par `on_finished` quand `cancel_requested[0]` est posé (annulation pendant la dernière page : le worker émet `finished` au lieu de `cancelled`).

**Piège — `on_finished` est appelé deux fois.** `_ResizeWorker` déclare `finished = Signal()`, exactement la même signature que le `QThread.finished` natif : le slot connecté à `worker.finished` reçoit l'émission du worker (dernière ligne de `run()`) **puis** le signal natif à la fin réelle du thread ; `on_cancelled` peut lui aussi être suivi de ce `finished` natif. D'où l'indicateur `settled` : la première fin reçue (`on_finished` ou `on_cancelled`) est la seule traitée, les appels suivants sont ignorés — et `_cleanup` (lui aussi connecté aux deux signaux) ne libère le worker qu'une fois (`cleaned`). **Ne jamais déduire l'annulation de `worker_ref[0] is None`** : `on_finished` remet lui-même `worker_ref[0]` à `None`, donc son second appel prendrait un redimensionnement réussi pour une annulation et le déferait (bytes d'origine restaurés, point d'undo dépilé). Seul `cancel_requested` dit qu'une annulation a été demandée. C'est le seul worker du projet dont le `finished` custom a la signature du natif (les autres ont des arguments, ou signalent leur fin par `done`) : renommer ce signal supprimerait le double appel, mais toute modification doit garder `settled`/`cancel_requested` tant qu'il porte ce nom.

**Document fermé ou remplacé pendant le traitement** (`_document_changed()`, sur `state.doc_generation`, voir skill `file-close`) : `on_finished` n'applique rien et `_restore_after_cancel` ne restaure rien — ni bytes (entrées de l'ancien document), ni surtout `pop_last_state`, qui dépilerait un point d'historique du document suivant. Le worker est libéré par `dispose_qthread` (`utils.py`) dans le slot de son signal de fin.

**Fermeture du fichier pendant le traitement** : `_cancel` est inscrit par `register_cancel_on_close(canvas, _cancel)` avant la création du worker (retiré dans `_cleanup`) ; `force_close_file` l'appelle comme le bouton Annuler, puis `_restore_after_cancel` saute la restauration grâce à la garde ci-dessus (voir skill `file-close`, « Arrêt des opérations à la fermeture »).

Une modification de ce pattern d'annulation doit rester cohérente avec ce choix explicite plutôt que de basculer vers `rollback_to_current_state_qt` sans vérifier que le comportement reste identique.

## Points d'entrée UI

Trois, tous nécessitant une sélection non vide (contrairement à `add-text-to-image`, ou aux outils crop/redressement/clonage de la visionneuse principale — skills `page-crop`/`page-straighten`/`clone-zone` — qui n'en ont pas besoin) :

1. **Menu contextuel** (clic droit mosaïque, skill `qt-context-menus`) — `show_image_context_menu` (`context_menus_qt.py`), clé `context_menu.image.reduce_size`.
2. **Barre de menu** — `_populate_images_menu` (`menubar_qt.py`), même clé.
3. **Colonne d'icônes** (skill `icon-toolbar`) — bouton id `"resize"` (`ICON_DEFINITIONS`, `icon_toolbar_qt.py`, icône `BTN_Resize.png`, **pas de tooltip dédié** `tooltip_key: None` — utilise le libellé générique `buttons.reduce_size` à la place, voir `IconToolbarQt._LABEL_KEYS`), activé si `has_selected_images()`.

Callbacks (`PanelWidget._resize_callbacks()`, `panel_widget.py`) : `save_state`, `render_mosaic`, `update_button_text`, `refresh_status`, `canvas`, `state` — identique à celui de `rotate-flip` mais **sans `rollback`** (le pattern d'annulation ici est manuel, voir section dédiée, pas besoin du callback `rollback_to_current_state_qt`).

**Garde-fous avant ouverture** (`reduce_selected_images_size_qt`) : aucune sélection → `MsgDialog` `no_selection_reduce` ; sélection ne contenant aucune image valide → `invalid_selection_reduce`.

## Traductions

`locales/fr.json`, section `reduce_size` : `window_title` (résolu via `_wt()`, règle UI n°7), tous les libellés de la fenêtre principale, `multi_page_width`/`multi_page_width_tooltip` pour la checkbox de détection automatique. Section `outliers` séparée pour la fenêtre secondaire : `title`, `message`, `width`/`height`, `unusual`, `skip`/`keep`. Voir skill `add-translation`.

**Contrairement à `add-text-to-image`, cette fonctionnalité a bien une section dans le mode d'emploi** (liste des sections de `_HelpDialog._build_ui`, `user_guide_qt.py`, clé `help.resize_pages`/`help.resize_pages_content`) — à maintenir à jour si le comportement de la détection multi-page ou de `OutlierDialog` change (skill `user-guide`).

## Comment étendre

- **Ajuster la tolérance de clustering** (actuellement ±10 %, `tolerance=0.10`) ou les seuils d'outlier (`ratio < 0.75 or ratio > 2.5`) : uniquement dans `cluster_and_find_reference` — fonction pure, testable indépendamment de l'UI.
- **Ajouter un multiplicateur candidat supplémentaire dans `OutlierDialog`** (au-delà de `×1`/`×2`/`×4`/`×8`) : `possible_mults` dans `_build_page_widgets`, dupliqué pour la largeur et la hauteur — les deux blocs doivent rester synchronisés si le principe change.
- **Changer le filtre de rééchantillonnage** (actuellement toujours `LANCZOS`) : une seule ligne dans `_ResizeWorker.run()`, `img.resize((new_w, new_h), Image.Resampling.LANCZOS)`.
- **Persister l'état de la checkbox multi-page entre sessions** (actuellement en mémoire seulement, `_multi_page_checkbox_state` réinitialisé à chaque lancement) : migrerait vers `ConfigManager` (skill `config-storage`) — changement de comportement notable, à ne pas faire sans confirmation explicite.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour `ResizeDialog`/`OutlierDialog` (non-modales déjà en place, `_wt()` pour les titres déjà en place).

## Pièges connus

- **Les deux modes (dimensions personnalisées / pourcentage) sont mutuellement exclusifs via un nettoyage croisé des contrôles** — toute nouvelle option de saisie doit décocher/vider l'autre mode pour ne pas laisser un état ambigu où les deux semblent actifs.
- **La détection multi-page ne s'applique qu'en mode dimensions personnalisées, avec plus d'une image sélectionnée** — sans effet silencieux en mode pourcentage ou sur une image seule ; ne pas supposer qu'elle influence le calcul dans ces cas.
- **`OutlierDialog` peut être annulé sans effet** — contrairement à un simple "annuler" qui interromprait un traitement en cours, ici rien n'a encore été appliqué ni sauvegardé (`save_state`) à ce stade ; annuler ramène proprement à `ResizeDialog`.
- **État de la checkbox multi-page mémorisé en mémoire process, pas persisté sur disque** — se réinitialise à `True` à chaque redémarrage de l'application, contrairement à d'autres réglages par panneau qui survivent via `ConfigManager`.
- **Annulation en cours de worker restaure les bytes manuellement**, pas via `rollback_to_current_state_qt` — pattern différent de `rotate-flip`, à ne pas mélanger si ce fichier est utilisé comme modèle pour une nouvelle fonction avec annulation.
- **Ne jamais restaurer les bytes dans `_cancel()` lui-même** : au moment du clic, le worker peut être en train d'encoder une page (plusieurs secondes sur une grande image) et ne revérifie l'annulation qu'au début de la page suivante. Son écriture de `entry["bytes"]` (et de `qt_qimage_large` via `build_qimage_for_entry`), postérieure à une restauration immédiate, laisserait cette page redimensionnée après l'annulation, sans point undo. La restauration n'est sûre qu'une fois `cancelled`/`finished` reçu.
- **`build_qimage_for_entry` appelé dans le thread worker**, pas dans le thread UI après coup — optimisation spécifique à ce fichier, absente des patterns d'invalidation de cache documentés ailleurs.
- **Le poids de fichier affiché à côté de chaque pourcentage est une estimation par extrapolation quadratique**, pas une mesure réelle post-compression — peut diverger significativement du poids final réel selon le contenu de l'image et le format.

## Références croisées

- `apply-image-operation` — pattern général d'invalidation de cache ; ce fichier suit une variante intermédiaire avec une optimisation propre (précalcul de la vignette Qt dans le worker).
- `rotate-flip` — architecture de worker par lot la plus proche (overlay de progression, bouton Annuler sur canvas) ; comparer les mécanismes d'annulation (restauration manuelle ici vs rollback global là, mais même découpage en deux temps `_cancel`/restauration différée) et les callbacks (`rollback` absent ici).
- `canvas-overlay-progress` — détail complet du mécanisme d'overlay (`item_holder`, style non paramétrable, bouton Annuler associé).
- `undo-redo` — `pop_last_state` utilisé pour dépiler le point undo en cas d'annulation manuelle, plutôt que `rollback_to_current_state_qt`, conditionné par `history_top` (ne dépiler que ce qui a été poussé).
- `adjust-compression` — `detect_jpeg_quality`, réutilisé ici pour préserver la qualité JPEG d'origine après redimensionnement.
- `icon-toolbar` — bouton "resize" de la colonne d'icônes (sans tooltip dédié, utilise un libellé générique).
- `qt-context-menus` — entrée du menu contextuel clic droit.
- `qt-tooltips` — tooltip de la checkbox de détection automatique multi-page (`OverlayTooltip`).
- `comicinfo-metadata-editor` — mise à jour des attributs de page dans `ComicInfo.xml` après redimensionnement, signal `emit_signal=False` pendant la boucle.
- `save-export` — `_write_zip_with_progress` régénère les bytes d'une entrée JPEG/PNG/TIFF si son `entry["dpi"]` diffère du DPI déjà encodé, avant écriture du CBZ ; complète la gestion du DPI décrite ici côté redimensionnement.
- `user-guide` — section `help.resize_pages` existante, à maintenir à jour (contrairement aux 3 autres visionneuses d'édition qui n'en ont pas).
- `page-crop` — même optimisation `build_qimage_for_entry` avant `refresh_thumbnail`, exécutée en synchrone là où `page-resize` le fait dans un thread worker.
