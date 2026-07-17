---
name: animated-gif
description: Localiser ou modifier la création/édition de GIF animé dans MosaicView (assemblage d'images en GIF, ré-édition par extraction des frames, réglages délai/boucle). Utiliser dès qu'une tâche touche à animated_gif_dialog_qt.py, is_animated_gif/get_gif_frame, ou au menu "Modifier le GIF animé".
---

# GIF animé — MosaicView

Fenêtre dédiée à deux usages qui se recouvrent dans une seule classe (`AnimatedGifDialog`, `modules/qt/animated_gif_dialog_qt.py`) : **créer** un nouveau GIF animé à partir de plusieurs images sélectionnées de la mosaïque, ou **ré-éditer** un GIF animé déjà présent (extraction de ses frames en images individuelles, modification, réassemblage). Les deux chemins convergent vers la même liste de travail interne (`self._gif_images`) et la même logique de création finale.

## Détection et lazy loading — `entries.py`, en amont de ce fichier

**Prérequis à comprendre avant de toucher à `animated_gif_dialog_qt.py`** : la détection d'un GIF multi-frame se fait dans `create_entry()` (skill `archive-image-loading`, `entries.py:207-233`), **pas** dans ce fichier. Au chargement d'un `.gif` avec `img.n_frames > 1` :

- `entry["is_animated_gif"] = True`, `entry["gif_frame_count"]` (nombre de frames, pas les frames elles-mêmes), `entry["gif_durations"]` (liste des durées en ms, une par frame), `entry["gif_loop"]`/`gif_disposal`/`gif_comment`/`gif_optimize` (métadonnées lues depuis `img.info`, `gif_optimize` étant une supposition faite par défaut à `True` plutôt qu'une valeur réellement détectée).
- **`entry["img"]` reste `None`** volontairement (lazy loading) — les frames individuelles ne sont chargées à la demande que via `get_gif_frame(entry, frame_idx)` (`entries.py:538`), qui vérifie `is_animated_gif`/`gif_frame_count`/`bytes` puis décode uniquement la frame demandée. Réutilisé aussi bien par la lecture (`ImageViewer`, skill `viewers`, mode lecture animée) que par ce dialogue d'édition.

Un GIF **statique** (une seule frame) n'a jamais `is_animated_gif = True` — traité comme n'importe quelle autre image du projet, hors périmètre de ce skill.

## Les deux chemins d'entrée dans `AnimatedGifDialog.__init__`

Selon la composition de `selected_entries` reçue à la construction, deux branches radicalement différentes s'exécutent avant même l'affichage de la fenêtre (`animated_gif_dialog_qt.py:344-378`) :

### 1. Édition d'un GIF animé existant

Condition : `len(selected_entries) == 1 and selected_entries[0].get("is_animated_gif")`. Dans ce cas :
- Les métadonnées d'origine (`gif_durations`/`gif_loop`/`gif_disposal`/`gif_comment`/`gif_optimize`) sont capturées dans `original_gif_metadata` **avant** que `selected_entries` soit vidé et reconstruit.
- Le GIF source est ouvert **une seule fois** (`Image.open` + `PIL.ImageSequence.Iterator`, pas une boucle `img.seek(i)` répétée) et chaque frame est extraite en une **nouvelle entrée PNG indépendante** (`{base_name}_frame_{i:03d}.png`, `is_image: True`, `is_animated_gif: False`, `from_gif_frame: True`, `gif_source: <nom du gif d'origine>`) — ce commentaire de code souligne explicitement que cette approche en itérateur séquentiel évite un coût en O(N²) qu'une boucle `seek()` répétée depuis le début du fichier aurait introduit.
- Le délai initial affiché dans le formulaire (`self._delay_ms`) est la **moyenne** des durées d'origine (`sum(durations) // len(durations)`), pas la durée de la première frame — un GIF aux durées hétérogènes par frame perd cette variation au ré-export, puisque `AnimatedGifDialog` n'expose qu'un seul champ de délai global, pas un délai par frame.

### 2. Création depuis plusieurs images sélectionnées

Sinon (plusieurs entrées sélectionnées, ou une seule qui n'est pas un GIF animé) : `selected_entries` est utilisé tel quel comme liste de frames de départ, valeurs par défaut (délai 100ms, boucle infinie `0`, disposal `2`, pas de commentaire, optimisation activée).

Dans les deux cas, `self._gif_images = [e.copy() for e in selected_entries]` — une **copie superficielle** de chaque dict d'entrée, indépendante de `state.images_data` : travailler dans cette fenêtre (réordonner, supprimer une frame) ne touche jamais aux entrées réelles de la mosaïque tant que "Créer" n'a pas été cliqué.

## Réorganisation des frames — glisser-déposer horizontal

`_ThumbWidget` (source de drag, une vignette 100×100 par frame avec son numéro et un bouton de suppression) et `_ThumbsContainer` (destination du drop, dessine un indicateur visuel d'insertion) :

- **Seuil anti-clic-accidentel** de 5px de déplacement (`manhattanLength() < 5`) avant de démarrer un vrai `QDrag` — cohérent avec les seuils similaires documentés dans `page-crop`/`create-ico`.
- **Indicateur d'insertion** (`_ThumbsContainer.paintEvent`) : ligne verticale rouge avec triangles haut/bas, positionnée dynamiquement pendant `dragMoveEvent` selon la position x de la souris (`_insert_index_at`, compare au centre de chaque vignette plutôt qu'à ses bords).
- **Correction d'index lors d'un déplacement vers la droite** (`dropEvent`, `if src_idx < insert_idx: insert_idx -= 1`) — piège classique de réordonnancement de liste : retirer un élément avant sa nouvelle position décale tous les index suivants d'un cran, sans cette correction la frame atterrirait un cran trop loin.
- **Cache de vignette par entrée** (`entry["_thumb_px"]`) — évite de redécoder l'image PIL à chaque `rebuild()` (appelé après toute suppression/déplacement) ; nettoyé explicitement dans `_free_gif_images()` à la fermeture.
- **Suppression** (`_delete_image`) : bloquée si une seule frame reste (`if len(self._gif_images) > 1`) — impossible de vider complètement la liste depuis l'UI.

## Réglages exposés

- **Délai inter-frames** (`QSpinBox`, 20 à 5000 ms, boutons +/- par pas de 50ms) — un seul délai **global**, appliqué à toutes les frames de sortie (`duration=delay_ms` dans les paramètres PIL de sauvegarde) ; pas de délai individualisé par frame même si la source en avait un différent par frame (voir remarque sur la moyenne, section édition).
- **Label FPS dynamique** (`_update_fps_label`) — affiche soit des FPS (`dialogs.gif_animated.fps_normal`, si délai `< 1000ms`) soit des secondes par frame (`fps_slow`, si délai `>= 1000ms`) ; formule dérivée (`1000.0 / delay_ms`), pas une valeur stockée séparément.
- **Boucles** (`QSpinBox`, 0 à 9999) — `0` signifie boucle infinie (convention GIF standard, pas une valeur spéciale gérée par du code custom ici, directement transmise à PIL en tant que `loop=0`).
- **Disposal** (4 `QRadioButton`, valeurs 0-3) — méthode de disposition GIF standard entre chaque frame (comment le rendu précédent est traité avant d'afficher le suivant : ne rien faire, ne pas disposer, restaurer en fond, restaurer au précédent) ; textes traduits via une clé dynamique par bouton (`rb._key = f"dialogs.gif_animated.disposal_{i}"`).
- **Optimize** (`QCheckBox`) — transmis tel quel à `Image.save(..., optimize=...)`, réduction de palette/taille de fichier gérée entièrement par Pillow.
- **Commentaire** (`QTextEdit`, encodé en UTF-8 à la sauvegarde si non vide) — métadonnée textuelle standard du format GIF, stockée dans le fichier final.
- **Panneau métadonnées en lecture seule** (`_update_metadata`) — dimensions (lues sur la première frame uniquement), nombre de frames, durée totale estimée, FPS, mention de palette, poids de fichier **estimé** (`num_frames * width * height * 0.5 / 1024`, approximation grossière basée sur 4 bits/pixel en moyenne pour une palette 256 couleurs compressée, pas une mesure réelle post-compression).

## Création du GIF final — `_create_animated_gif` (`animated_gif_dialog_qt.py:646`)

Pipeline en 5 étapes séquentielles, chacune affichant un message de progression (`_progress_lbl`) rafraîchi via `QApplication.processEvents()` — **pas de worker QThread**, tout se déroule sur le thread UI avec des appels manuels à `processEvents()` pour garder l'interface réactive pendant le traitement, contrairement au pattern `QThread` documenté dans `rotate-flip`/`page-resize`.

1. **Chargement** de toutes les images (via `entry["img"]` déjà présent ou `ensure_image_loaded`), calcul de la bounding box maximale (`max_w`/`max_h` = les plus grandes largeur/hauteur rencontrées parmi toutes les frames).
2. **Normalisation** : chaque frame est composée, centrée, sur un canevas RGB blanc de taille `max_w × max_h` — les frames plus petites que le maximum sont donc **letterboxées** (bordures blanches) plutôt que redimensionnées à l'échelle ; une image avec canal alpha est collée avec son propre canal comme masque (transparence fondue sur fond blanc), pas conservée comme transparence GIF.
3. **Quantization avec palette commune** : la première frame normalisée est quantifiée en 256 couleurs adaptatives (`Image.ADAPTIVE`) et sert de **référence de palette** ; toutes les frames suivantes sont quantifiées avec `palette=first_p` (même palette imposée) plutôt que chacune avec sa propre palette adaptative — garantit un rendu de couleurs cohérent entre frames (essentiel en GIF, format à palette unique par fichier), au prix d'une fidélité de couleur potentiellement dégradée sur les frames autres que la première si leurs couleurs dominantes diffèrent trop.
4. **Sauvegarde multi-frame Pillow** : `pil_images[0].save(..., format="GIF", save_all=True, append_images=pil_images[1:], duration=delay_ms, loop=loop, disposal=disposal, optimize=do_optimize, comment=...)` — un seul appel produit le fichier GIF animé complet.
5. **Nommage automatique** : `Animated_{N}.gif`, `N` calculé en comptant les entrées de `state.images_data` dont `orig_name` commence déjà par `"Animated_"` puis `+1` — motif de comptage simple (pas de recherche du maximum comme `create-ico`'s `ICO{NNN}_`), donc une collision reste possible si une entrée `Animated_N` a été renommée ou supprimée entre deux créations dans la même session.

**Nettoyage mémoire explicite à 3 endroits distincts** : les canevas RGB intermédiaires sont fermés et vidés dès la quantization terminée (avant même la sauvegarde), les images quantifiées après la sauvegarde, les frames sources (`entry["img"]` de `self._gif_images`) via `_free_gif_images()` après insertion de la nouvelle entrée — trois PIL Images distinctes par frame existent transitoirement pendant le pipeline, chacune libérée dès qu'elle n'est plus utile plutôt qu'attendre la fin complète de la fonction.

**Nouvelle entrée créée via `create_entry()`** (skill `archive-image-loading`, contrairement à `create-ico` qui construit son dict manuellement) — `new_entry["source_archive"] = "loose"` forcé après coup, insérée en fin de mosaïque (`state.images_data.append`, pas à une position relative à une source comme `create-ico`/`page-split`), suivie de `sync_pages_in_xml_data` (skill `comicinfo-metadata-editor`).

**Un seul `save_state()` après création, aucun avant** — même famille de pattern que `create-ico`/`page-split` : il s'agit d'un ajout de nouvelle entrée à la mosaïque, pas d'une modification en place d'une entrée existante, donc pas de "avant" à capturer au sens du skill `apply-image-operation`.

## Fermeture et libération mémoire — `closeEvent`

`_free_gif_images()` est appelée systématiquement à la fermeture (`closeEvent`, **y compris sur annulation**) — ferme chaque `entry["img"]` PIL encore ouvert dans `self._gif_images` et vide le cache `_thumb_px`. Nécessaire car la fenêtre peut avoir chargé un nombre arbitraire d'images complètes en mémoire (jusqu'à toutes les frames extraites d'un long GIF en mode édition) sans jamais les avoir validées dans la mosaïque.

## Points d'entrée UI

Deux chemins distincts, pas trois comme les skills d'édition d'image précédents :

1. **"Modifier le GIF animé..."** (`context_menu.image.edit_animated_gif`) — menu contextuel (`context_menus_qt.py:456`) et barre de menu (`menubar_qt.py:222`), visible/activé seulement si **exactement une** entrée sélectionnée et qu'elle est un GIF animé (`single_entry`, condition vérifiée côté appelant avant d'ajouter l'action, pas dans `AnimatedGifDialog` lui-même) — déclenche la branche "édition" décrite plus haut. **Pas de bouton dans la colonne d'icônes** pour ce point d'entrée.
2. **Dialogue de conversion de format** (`conversion_dialogs_qt.py:556`, hors périmètre de ce skill mais point d'entrée réel) — en choisissant "GIF animé (sélectionner plusieurs images)" (`dialogs.convert.format_gif_animated`) comme format cible pour plusieurs images sélectionnées, ce dialogue appelle directement `callbacks['show_animated_gif_dialog'](self._selected_entries)` — déclenche la branche "création" avec la sélection multiple d'origine.

Callbacks (`PanelWidget._animated_gif_callbacks()`, `panel_widget.py:1478`) : `save_state`, `render_mosaic`, `update_button_text` — pas de `state` explicite dans le dict (le dialogue utilise `_state_module.state` directement en solo, `animated_gif_dialog_qt.py:342`), pas de `canvas`.

## Traductions

`locales/fr.json`, section `gif_animated` (ligne 763) : `window_title` (`_wt()`, règle UI n°7), `images_title`/`params_title`, `frame_delay_label`/`fps_slow`/`fps_normal`, `loop_label`/`loop_info`, `disposal_label`/`disposal_0`-`disposal_3`, `optimize`, `metadata_title`/`metadata_dimensions`/`metadata_frames`/`metadata_duration`/`metadata_fps`/`metadata_palette`/`metadata_size`, `comment_label`, `creating_progress`/`creating_normalizing`/`creating_quantizing`/`creating_saving` (textes de progression). Clés séparées : `context_menu.image.edit_animated_gif`, `dialogs.convert.format_gif_animated` (dialogue de conversion), `messages.warnings.no_images_for_gif`/`no_valid_images`, `messages.errors.gif_creation_failed`, `buttons.create_gif`. Voir skill `add-translation`.

**Absent du mode d'emploi** (`user_guide_qt.py`) — même situation que `page-straighten`/`add-text-to-image`/`clone-zone` (skill `user-guide`).

## Comment étendre

- **Ajouter un délai par frame** (actuellement un seul délai global) : nécessiterait un champ éditable par `_ThumbWidget` plutôt qu'un unique `QSpinBox` global, et de transmettre une **liste** de durées à `duration=` plutôt qu'un entier unique (Pillow accepte les deux formes) — changement d'UI significatif, à valider avec l'utilisateur.
- **Changer l'algorithme de normalisation des tailles** (actuellement letterboxing centré sur fond blanc) : uniquement la boucle de composition dans `_create_animated_gif`, section "Normalise en RGB sur un canvas max_w×max_h".
- **Changer la stratégie de palette** (actuellement palette de la première frame imposée à toutes) : la section "Quantize avec palette commune" — une palette globale calculée sur l'ensemble des frames combinées donnerait un résultat plus fidèle mais coûterait significativement plus cher en calcul.
- **Corriger le risque de collision de nommage** (`Animated_{N}.gif` par comptage plutôt que par maximum) : aligner sur le pattern de `create-ico` (`_get_ico_name`, recherche du numéro maximum existant par regex) plutôt que le comptage actuel — changement mineur mais à confirmer avant de le faire, ce n'est peut-être pas jugé assez risqué pour justifier la modification.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour `AnimatedGifDialog` (non-modale déjà en place, `_wt()` pour le titre déjà en place).

## Pièges connus

- **Deux chemins d'entrée radicalement différents dans le même `__init__`** — toute modification du constructeur doit être testée dans les deux cas (édition d'un GIF existant vs création depuis une sélection multiple), pas un seul.
- **Le délai moyen remplace les délais individuels par frame** lors d'une ré-édition — une variation de rythme dans le GIF d'origine est aplatie au premier ré-export depuis cette fenêtre.
- **Palette imposée depuis la première frame** — un jeu de frames aux couleurs dominantes très différentes entre elles peut produire un résultat dégradé sur les frames autres que la première.
- **Pas de worker QThread, `QApplication.processEvents()` manuel** — contrairement à `rotate-flip`/`page-resize`, un traitement très long (beaucoup de frames haute résolution) bloque le thread UI par blocs plutôt que réellement en arrière-plan ; pas de bouton Annuler pendant la création.
- **Nommage par comptage, pas par maximum** — risque de collision de nom si une entrée `Animated_N` a été renommée/supprimée entre deux créations, contrairement à `create-ico`.
- **Un seul `save_state()`, aucun avant** — comme `create-ico`/`page-split`, cohérent avec le fait qu'il s'agit d'un ajout de nouvelle entrée.
- **`_free_gif_images()` doit être appelée à la fermeture, y compris annulation** — sans ça, les PIL Images chargées en mode édition (potentiellement toutes les frames d'un long GIF) resteraient en mémoire.
- **La suppression de frame est bloquée à 1 minimum**, pas 0 — ne pas supposer qu'une liste vide de frames est un état atteignable depuis l'UI.

## Références croisées

- `archive-image-loading` — détection `is_animated_gif`/`gif_frame_count`/`gif_durations` dans `create_entry()`, `get_gif_frame()` pour le lazy loading des frames, `create_entry()` réutilisé (contrairement à `create-ico`) pour la nouvelle entrée GIF créée.
- `viewers` — `ImageViewer` (visionneuse principale de lecture) a son propre mode de lecture animée qui consomme les mêmes `is_animated_gif`/`get_gif_frame`, mais pour l'affichage/lecture, pas l'édition ; aucun code partagé avec `AnimatedGifDialog`.
- `create-ico` — comparaison utile sur le pattern "ajout de nouvelle entrée, un seul `save_state` après" ; `create-ico` construit son entrée manuellement quand ce fichier réutilise `create_entry()`, et nomme par recherche du maximum plutôt que par comptage.
- `page-split` — autre exemple du pattern à un seul point undo pour un ajout de page(s).
- `apply-image-operation` — ce fichier ne suit pas le pattern documenté (pas de `save_state` avant, pas de modification de `entry['bytes']` existant) pour la même raison que `create-ico` : il s'agit d'un ajout, pas d'une modification en place.
- `comicinfo-metadata-editor` — `sync_pages_in_xml_data` après insertion de la nouvelle entrée GIF.
- `qt-context-menus` — entrée "Modifier le GIF animé..." du menu contextuel.
- `user-guide` — absence actuelle de section dédiée, à vérifier si une tâche touche à ce fichier.
- `nfo-editor` — autre créateur de nouvelle entrée du projet qui réutilise `create_entry()` (comme ce fichier, contrairement à `create-ico`), mais suit le pattern standard à deux `save_state` plutôt que le pattern à un seul suivi ici.
- `save-export` — `_check_animated_gifs_qt` avertit avant sauvegarde CBZ que les GIFs animés seront figés sur leur frame courante ; voir ce skill pour la chaîne de validation complète avant écriture.
