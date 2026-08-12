---
name: page-resize
description: Localiser ou modifier le redimensionnement des pages de MosaicView (dimensions personnalisées/pourcentages, détection des pages multiples par clustering, fenêtre des dimensions aberrantes). Utiliser dès qu'une tâche touche à resize_dialog_qt.py, ResizeDialog, ou OutlierDialog.
---

# Redimensionnement des pages — MosaicView

Fenêtre dédiée qui redimensionne les images **sélectionnées** de la mosaïque, soit vers des dimensions personnalisées (largeur/hauteur en pixels, avec conservation optionnelle du ratio), soit par un pourcentage de réduction/agrandissement prédéfini. Fonctionnalité la plus élaborée du projet en termes d'heuristique automatique : elle inclut une **détection automatique des pages multiples** (planches doubles/triples scannées en un seul fichier) qui adapte le facteur de redimensionnement page par page plutôt que d'appliquer une taille uniforme brutale.

## Deux fenêtres, un seul fichier — `modules/qt/resize_dialog_qt.py`

- **`ResizeDialog`** (`QDialog`) — la fenêtre principale : infos actuelles (poids total, dimensions si toutes identiques), section dimensions personnalisées (champs largeur/hauteur liés par le ratio, checkbox de détection multi-page), section pourcentages (deux colonnes réduction/agrandissement, radios avec poids estimé affiché par option).
- **`OutlierDialog`** (`QDialog`) — fenêtre **secondaire**, ouverte uniquement si la détection multi-page trouve des pages aux dimensions aberrantes ; demande à l'utilisateur de choisir un multiplicateur pour chacune, une par une, avec vignette.
- **`cluster_and_find_reference(dimensions, tolerance=0.10)`** — la logique pure de clustering, indépendante de Qt, portée directement depuis l'ancienne version tkinter du projet (`resize_dialog.py`, mentionné en commentaire de tête de fichier).
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

### L'algorithme — `cluster_and_find_reference` (`resize_dialog_qt.py:73`)

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

## Worker asynchrone — `_ResizeWorker` (`resize_dialog_qt.py:1204`)

Pattern proche de `rotate-flip` (`QThread`, overlay de progression + bouton Annuler sur le canvas, anti-GC implicite via `worker_ref`) :

- **Redimensionnement PIL** : toujours `Image.Resampling.LANCZOS`, quel que soit le mode (dimensions personnalisées ou pourcentage) — pas de choix de filtre exposé à l'utilisateur.
- **Préservation de la qualité JPEG d'origine** : `detect_jpeg_quality(entry["bytes"])` (skill `adjust-compression`) est relu avant redimensionnement et réappliqué à la sauvegarde — un resize ne dégrade pas davantage la compression déjà choisie pour cette image.
- **Préservation du DPI** : lu depuis `entry.get("dpi")` ou les métadonnées PIL d'origine (`img.info.get("dpi", (72, 72))`), réappliqué tel quel à la sauvegarde — les dimensions physiques déclarées de l'image suivent le changement de résolution en pixels.
- **Format de sortie toujours identique au format d'origine** (déduit de l'extension du nom de fichier) — pas de conversion de format pendant un resize, contrairement à `convert_image_data` (skill non couvert ici, voir `image_ops.py::convert_image_data`).
- Invalidation cache — variante intermédiaire, ni tout à fait (A) ni (B) du skill `apply-image-operation` : `img`/`_hash` remis à `None`, `large_thumb_pil` fermé et remis à `None`, `qt_pixmap_large`/`qt_qimage_large` retirés du dict (`pop`, pas juste `= None`), puis **`build_qimage_for_entry(entry)` est appelé explicitement dans le thread worker** pour précalculer la vignette Qt en arrière-plan avant même la fin du traitement — optimisation propre à ce fichier, absente des autres opérations d'image du projet, qui évite de reconstruire la vignette plus tard dans le thread UI au moment du premier `paintEvent`.
- **`update_page_entries_in_xml_data(..., emit_signal=False)`** — signal explicitement coupé pendant la boucle (contrairement à d'autres opérations qui laissent le signal par défaut), probablement pour éviter une rafale de rafraîchissements de l'onglet métadonnées à chaque image d'un lot potentiellement volumineux ; un seul `metadata_pages_signal.emit()` global est déclenché après coup dans `on_finished`.

### Annulation en cours de lot — restauration manuelle des bytes, pas un rollback global

Contrairement à `rotate-flip` qui utilise `rollback_to_current_state_qt` (skill `undo-redo`), l'annulation ici (`_cancel()`, `resize_dialog_qt.py:1399`) restaure **manuellement** les bytes d'origine de chaque entrée déjà modifiée depuis un dict `original_bytes` capturé **avant** le lancement du worker (`{id(e): e["bytes"] for e in selected_entries}`, dans `_finish_resize`), puis dépile explicitement le point undo poussé par le `save_state()` initial (`pop_last_state(state)`, skill `undo-redo`) et restaure `state.modified` à sa valeur d'avant. Une modification de ce pattern d'annulation doit rester cohérente avec ce choix explicite plutôt que de basculer vers `rollback_to_current_state_qt` sans vérifier que le comportement reste identique.

## Points d'entrée UI

Trois, tous nécessitant une sélection non vide (contrairement à `add-text-to-image`, ou aux outils crop/redressement/clonage de la visionneuse principale — skills `page-crop`/`page-straighten`/`clone-zone` — qui n'en ont pas besoin) :

1. **Menu contextuel** (clic droit mosaïque, skill `qt-context-menus`) — `context_menus_qt.py:405`, clé `context_menu.image.reduce_size`.
2. **Barre de menu** — `menubar_qt.py:198`, même clé.
3. **Colonne d'icônes** (skill `icon-toolbar`) — bouton id `"resize"` (`icon_toolbar_qt.py:62`, icône `BTN_Resize.png`, **pas de tooltip dédié** `tooltip_key: None` — utilise le libellé générique `buttons.reduce_size` à la place, voir `icon_toolbar_qt.py:2010`), activé si `has_selected_images()`.

Callbacks (`PanelWidget._resize_callbacks()`, `panel_widget.py:1563`) : `save_state`, `render_mosaic`, `update_button_text`, `refresh_status`, `canvas`, `state` — identique à celui de `rotate-flip` mais **sans `rollback`** (le pattern d'annulation ici est manuel, voir section dédiée, pas besoin du callback `rollback_to_current_state_qt`).

**Garde-fous avant ouverture** (`reduce_selected_images_size_qt`) : aucune sélection → `MsgDialog` `no_selection_reduce` ; sélection ne contenant aucune image valide → `invalid_selection_reduce`.

## Traductions

`locales/fr.json`, section `reduce_size` (ligne 893) : `window_title` (résolu via `_wt()`, règle UI n°7), tous les libellés de la fenêtre principale, `multi_page_width`/`multi_page_width_tooltip` pour la checkbox de détection automatique. Section `outliers` (ligne 929) séparée pour la fenêtre secondaire : `title`, `message`, `width`/`height`, `unusual`, `skip`/`keep`. Voir skill `add-translation`.

**Contrairement à `add-text-to-image`, cette fonctionnalité a bien une section dans le mode d'emploi** (`user_guide_qt.py:636`, clé `help.resize_pages`/`help.resize_pages_content`) — à maintenir à jour si le comportement de la détection multi-page ou de `OutlierDialog` change (skill `user-guide`).

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
- **`build_qimage_for_entry` appelé dans le thread worker**, pas dans le thread UI après coup — optimisation spécifique à ce fichier, absente des patterns d'invalidation de cache documentés ailleurs.
- **Le poids de fichier affiché à côté de chaque pourcentage est une estimation par extrapolation quadratique**, pas une mesure réelle post-compression — peut diverger significativement du poids final réel selon le contenu de l'image et le format.

## Références croisées

- `apply-image-operation` — pattern général d'invalidation de cache ; ce fichier suit une variante intermédiaire avec une optimisation propre (précalcul de la vignette Qt dans le worker).
- `rotate-flip` — architecture de worker par lot la plus proche (overlay de progression, bouton Annuler sur canvas) ; comparer les mécanismes d'annulation (rollback global ici vs manuel là) et les callbacks (`rollback` absent ici).
- `canvas-overlay-progress` — détail complet du mécanisme d'overlay (`item_holder`, style non paramétrable, bouton Annuler associé).
- `undo-redo` — `pop_last_state` utilisé pour dépiler le point undo en cas d'annulation manuelle, plutôt que `rollback_to_current_state_qt`.
- `adjust-compression` — `detect_jpeg_quality`, réutilisé ici pour préserver la qualité JPEG d'origine après redimensionnement.
- `icon-toolbar` — bouton "resize" de la colonne d'icônes (sans tooltip dédié, utilise un libellé générique).
- `qt-context-menus` — entrée du menu contextuel clic droit.
- `qt-tooltips` — tooltip de la checkbox de détection automatique multi-page (`OverlayTooltip`).
- `comicinfo-metadata-editor` — mise à jour des attributs de page dans `ComicInfo.xml` après redimensionnement, signal `emit_signal=False` pendant la boucle.
- `save-export` — `_write_zip_with_progress` régénère les bytes d'une entrée si son `entry["dpi"]` diffère du DPI déjà encodé, avant écriture du CBZ ; complète la gestion du DPI décrite ici côté redimensionnement.
- `user-guide` — section `help.resize_pages` existante, à maintenir à jour (contrairement aux 3 autres visionneuses d'édition qui n'en ont pas).
- `page-crop` — même optimisation `build_qimage_for_entry` avant `refresh_thumbnail`, exécutée en synchrone là où `page-resize` le fait dans un thread worker.
