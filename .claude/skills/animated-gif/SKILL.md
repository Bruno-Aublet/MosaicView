---
name: animated-gif
description: Localiser ou modifier la création/édition de GIF animé dans MosaicView (assemblage d'images en GIF, ré-édition par extraction des frames, réglages délai/boucle). Utiliser dès qu'une tâche touche à animated_gif_dialog_qt.py, is_animated_gif/get_gif_frame, aux menus "Créer un GIF animé" / "Modifier le GIF animé", ou à l'icône GIF animé de la colonne d'icônes.
---

# GIF animé — MosaicView

Fenêtre dédiée à deux usages qui se recouvrent dans une seule classe (`AnimatedGifDialog`, `modules/qt/animated_gif_dialog_qt.py`) : **créer** un nouveau GIF animé à partir de plusieurs images sélectionnées de la mosaïque, ou **ré-éditer** un GIF animé déjà présent (extraction de ses frames en images individuelles, modification, réassemblage). Les deux chemins convergent vers la même liste de travail interne (`self._gif_images`) et la même logique de création finale.

## Détection et lazy loading — `entries.py`, en amont de ce fichier

**Prérequis à comprendre avant de toucher à `animated_gif_dialog_qt.py`** : la détection d'un GIF multi-frame se fait dans `create_entry()` (skill `archive-image-loading`, `entries.py`), **pas** dans ce fichier. Au chargement d'un `.gif` avec `img.n_frames > 1` :

- `entry["is_animated_gif"] = True`, `entry["gif_frame_count"]` (nombre de frames, pas les frames elles-mêmes), `entry["gif_durations"]` (liste des durées en ms, une par frame), `entry["gif_loop"]`/`gif_disposal`/`gif_comment`/`gif_optimize` (métadonnées lues depuis `img.info`, `gif_optimize` étant une supposition faite par défaut à `True` plutôt qu'une valeur réellement détectée).
- **`entry["img"]` reste `None`** volontairement (lazy loading) — les frames individuelles ne sont chargées à la demande que via `get_gif_frame(entry, frame_idx)` (`entries.py`), qui vérifie `is_animated_gif`/`gif_frame_count`/`bytes` puis décode uniquement la frame demandée. Réutilisé aussi bien par la lecture (`ImageViewer`, skill `viewers`, mode lecture animée) que par ce dialogue d'édition.

Un GIF **statique** (une seule frame) n'a jamais `is_animated_gif = True` — traité comme n'importe quelle autre image du projet, hors périmètre de ce skill.

## Un GIF animé hors de cette fenêtre

Cette fenêtre reste le seul endroit où modifier le contenu d'un GIF animé. Ailleurs dans l'application :
Les WebP/PNG/AVIF animés (`is_animated_image`) suivent exactement les mêmes règles que les GIF animés ci-dessous (`image_ops.is_animated_entry`) ; seule cette fenêtre leur est fermée.
- **Conservent toutes les frames** (via `image_ops.transform_animated_entry`, skill `rotate-flip`) : rotation et miroir (mosaïque, visionneuse, macros), redimensionnement (skill `page-resize`), redressement automatique depuis la mosaïque (skill `page-straighten`), découpe (skill `page-split`), conversion vers WebP/PNG/AVIF (skill `image-format-conversion`). Les mêmes opérations conservent toutes les pages d'un TIFF de plusieurs pages (`is_multipage_tiff`, `image_ops.has_several_frames`, skill `apply-image-operation`), qui n'est pas pour autant traité en image animée.
- **Jointure de pages** (skill `page-merge`) : possible seulement entre images animées de même nombre de frames et de mêmes durées (`animations_joinable`, tous formats animés confondus), jamais avec une image fixe ; le résultat est animé, assemblé frame par frame (`merge_animated_2d`). Commandes grisées sinon.
- **Refusés** : les outils de la visionneuse qui ne traitent que la première frame, grisés avec une explication (skill `viewers`, `_ANIMATED_GIF_BLOCKED_TOOLS`) et refusés en lecture de macro (skill `macro-tool`).
- **Première frame seulement, par nature du format cible** : conversion vers JPEG/BMP/TIFF, et "GIF statique".
- Lecture animée : la visionneuse affiche toujours une image animée seule en mode double/continu, et la joue le nombre de fois prévu par le fichier (`entries.animation_play_count`, skill `viewers`).

## Les deux chemins d'entrée dans `AnimatedGifDialog.__init__`

Selon la composition de `selected_entries` reçue à la construction, deux branches radicalement différentes s'exécutent avant même l'affichage de la fenêtre (`AnimatedGifDialog.__init__`, bloc "Extraction des frames si GIF animé unique") :

### 1. Édition d'un GIF animé existant

Condition : `_is_single_animated_gif(selected_entries)` (une seule entrée, `is_animated_gif`). Dans ce cas :
- Les métadonnées d'origine (`gif_durations`/`gif_loop`/`gif_disposal`/`gif_comment`/`gif_optimize`) sont capturées dans `original_gif_metadata`, puis `selected_entries` est remplacé par les images extraites.
- **Extraction** : fonction de module `_extract_gif_frames(gif_entry)` → `(entrées, arrêtée)`. Le GIF source est ouvert **une seule fois** (`Image.open` + `PIL.ImageSequence.Iterator`, pas une boucle `img.seek(i)` répétée, qui coûterait O(N²)) et chaque frame est extraite en une **nouvelle entrée PNG indépendante** (`{base_name}_frame_{i:03d}.png`, RGBA, `is_image: True`, `is_animated_gif: False`, `from_gif_frame: True`, `gif_source: <nom du gif d'origine>`). `show_animated_gif_dialog` l'appelle **avant** de construire la fenêtre et lui passe le résultat (`AnimatedGifDialog(..., gif_extraction)` ; sans lui, `__init__` extrait lui-même).
- **Lecture arrêtée avant la fin** (`arrêtée` vrai : image qui ne se décode pas, ou mémoire insuffisante pour tout convertir en RGBA — un GIF tronqué, lui, est déjà refusé au chargement, page à cadre rouge, et ne propose pas « Modifier ») : les images lues sont gardées. Aucune image lue → la fenêtre ne s'ouvre pas, `MsgDialog` `dialogs.gif_animated.read_error_none` ; sinon elle s'ouvre, et un `MsgDialog` (parent : la fenêtre GIF, qui reste ouverte) affiche `read_error_partial` (`{read}` images lues sur `{total}` = `gif_frame_count`). **Pourquoi pas sauter l'image fautive** : Pillow lit un GIF dans l'ordre et ne peut pas atteindre les images suivantes (même `seek()` direct), et chaque image ne contient souvent que ce qui change par rapport à la précédente — les suivantes seraient fausses. **Piège** : avalée, l'erreur ouvrait la fenêtre avec une partie des images, et le GIF recréé était tronqué sans que l'utilisateur le sache. Testé par `tests/test_gif_frame_extraction.py` (fonction extraite par AST).
- Chaque entrée de frame porte sa durée d'origine (`gif_duration`), qui la suit si la liste est réordonnée. Le délai initial affiché dans le formulaire (`self._delay_ms`) est la **moyenne** des durées d'origine ; **tant qu'il n'est pas modifié**, le GIF créé garde la durée propre à chaque image (une pause sur la dernière image est conservée) ; s'il l'est, sa valeur vaut pour toutes les images (pas de réglage par image dans la fenêtre).
- **Boucle** : `gif_loop` (`entries.create_entry`) vaut `None` pour un GIF sans bloc de répétition, joué une seule fois — jamais remplacé par `0` (boucle infinie). Le réglage « Nombre de répétitions » (`0` = infini) ne sait pas représenter ce cas : il affiche `1`, et tant qu'il n'est pas modifié (`_loop_unset`), le GIF créé reste sans bloc de répétition (`loop` absent des paramètres de `save()`).

### 2. Création depuis plusieurs images sélectionnées

Sinon (plusieurs entrées sélectionnées, ou une seule qui n'est pas un GIF animé) : `selected_entries` est utilisé tel quel comme liste de frames de départ, valeurs par défaut (délai 100ms, boucle infinie `0`, disposal `2`, pas de commentaire, optimisation activée).

Dans les deux cas, `self._gif_images` contient une **copie superficielle** (`e.copy()`) de chaque dict d'entrée, indépendante de `state.images_data` : travailler dans cette fenêtre (réordonner, supprimer une frame) ne touche jamais aux entrées réelles de la mosaïque tant que "Créer" n'a pas été cliqué. Une copie qui a des `bytes` (page de la mosaïque) reçoit `img = None` et décode sa propre image. **Piège** : une page déjà décodée (visionneuse ouverte) partagerait sinon son objet PIL avec sa copie, que la fenêtre ferme (`_free_gif_images`, `_update_metadata`) : la page garderait une image fermée (`ValueError: Operation on closed image` dans la visionneuse). Les images extraites d'un GIF à modifier n'ont pas de `bytes` et gardent leur image.

## Réorganisation des frames — glisser-déposer horizontal

`_ThumbWidget` (source de drag, une vignette 100×100 par frame avec son numéro et un bouton de suppression) et `_ThumbsContainer` (destination du drop, dessine un indicateur visuel d'insertion) :

- **Seuil anti-clic-accidentel** de 5px de déplacement (`manhattanLength() < 5`) avant de démarrer un vrai `QDrag` — cohérent avec les seuils similaires documentés dans `page-crop`/`create-ico`.
- **Indicateur d'insertion** (`_ThumbsContainer.paintEvent`) : ligne verticale rouge avec triangles haut/bas, positionnée dynamiquement pendant `dragMoveEvent` selon la position x de la souris (`_insert_index_at`, compare au centre de chaque vignette plutôt qu'à ses bords).
- **Correction d'index lors d'un déplacement vers la droite** (`dropEvent`, `if src_idx < insert_idx: insert_idx -= 1`) — piège classique de réordonnancement de liste : retirer un élément avant sa nouvelle position décale tous les index suivants d'un cran, sans cette correction la frame atterrirait un cran trop loin.
- **Cache de vignette par entrée** (`entry["_thumb_px"]`) — évite de redécoder l'image PIL à chaque `rebuild()` (appelé après toute suppression/déplacement) ; nettoyé explicitement dans `_free_gif_images()` à la fermeture.
- **Suppression** (`_delete_image`) : bloquée si une seule frame reste (`if len(self._gif_images) > 1`) — impossible de vider complètement la liste depuis l'UI.

## Réglages exposés

- **Délai inter-frames** (`QSpinBox`, 20 à 5000 ms, boutons +/- par pas de 50ms) — un seul délai **global** ; les durées propres à chaque image d'un GIF modifié sont gardées tant qu'il n'est pas changé (voir section édition).
- **Label FPS dynamique** (`_update_fps_label`) — affiche soit des FPS (`dialogs.gif_animated.fps_normal`, si délai `< 1000ms`) soit des secondes par frame (`fps_slow`, si délai `>= 1000ms`) ; formule dérivée (`1000.0 / delay_ms`), pas une valeur stockée séparément.
- **Boucles** (`QSpinBox`, 0 à 9999) — `0` signifie boucle infinie (convention GIF standard, transmise à PIL en tant que `loop=0`) ; cas du GIF joué une fois : voir section édition.
- **Disposal** (4 `QRadioButton`, valeurs 0-3) — méthode de disposition GIF standard entre chaque frame (comment le rendu précédent est traité avant d'afficher le suivant : ne rien faire, ne pas disposer, restaurer en fond, restaurer au précédent) ; textes traduits via une clé dynamique par bouton (`rb._key = f"dialogs.gif_animated.disposal_{i}"`).
- **Optimize** (`QCheckBox`) — transmis à `Image.save(..., optimize=...)`, réduction de palette/taille de fichier gérée par Pillow. **Ignoré quand des images sont transparentes** : avec `optimize`, Pillow rend opaques (blanches) les zones transparentes des images qui suivent la première.
- **Commentaire** (`QTextEdit`, encodé en UTF-8 à la sauvegarde si non vide) — métadonnée textuelle standard du format GIF, stockée dans le fichier final.
- **Panneau métadonnées en lecture seule** (`_update_metadata`) — dimensions (lues sur la première frame uniquement), nombre de frames, durée totale estimée, FPS, mention de palette, poids de fichier **estimé** (`num_frames * width * height * 0.5 / 1024`, approximation grossière basée sur 4 bits/pixel en moyenne pour une palette 256 couleurs compressée, pas une mesure réelle post-compression). **Piège** : après avoir lu la première frame, la mémoire n'en est libérée que si elle peut être relue depuis ses bytes (`first.get("bytes")`). Les frames extraites d'un GIF modifié n'existent qu'en mémoire (`entry["img"]`, sans bytes) : libérée, la première disparaissait du GIF créé.

## Création du GIF final — `AnimatedGifDialog._create_animated_gif`

Sans effet (fenêtre ouverte) si une opération en arrière-plan (rotation…) tourne sur le document du panneau (`self._state.operation_running()`, skill `undo-redo`) : la fenêtre est non modale.

Pipeline en étapes séquentielles, chacune affichant un message de progression (`_progress_lbl`) rafraîchi via `QApplication.processEvents()` — **pas de worker QThread**, tout se déroule sur le thread UI avec des appels manuels à `processEvents()` pour garder l'interface réactive pendant le traitement, contrairement au pattern `QThread` documenté dans `rotate-flip`/`page-resize`.

1. **Chargement** de toutes les images (via `entry["img"]` déjà présent ou `ensure_image_loaded`), 16 bits ramenés à 256 nuances (`to_display_mode`), durée propre de chaque image relevée (`gif_duration`), calcul de la bounding box maximale (`max_w`/`max_h`).
2. **Normalisation** : chaque frame est composée, centrée, sur un canevas blanc de taille `max_w × max_h` — les frames plus petites que le maximum sont **letterboxées** (bordures blanches) plutôt que redimensionnées. Si une image a de la transparence (`keep_alpha`), le canevas est RGBA et chaque image y est collée **sans masque** : ses zones transparentes restent transparentes, seul le fond qu'aucune image ne couvre est blanc. Sinon canevas RGB.
3. **Une palette par image** : les frames RGB/RGBA sont transmises telles quelles à `save()`, et Pillow quantifie chacune avec sa propre palette, transparence comprise. Une palette commune imposée (celle de la première image) dégraderait les couleurs des suivantes.
4. **Sauvegarde multi-frame Pillow** : `pil_images[0].save(..., format="GIF", save_all=True, append_images=pil_images[1:], duration=durations, disposal=disposal, optimize=do_optimize and not keep_alpha, comment=...)`, plus `loop` sauf pour un GIF joué une fois resté inchangé (section édition) — un seul appel produit le fichier GIF animé complet.
5. **Nommage automatique** : `Animated_{N}.gif`, `N` calculé en comptant les entrées de `state.images_data` dont `orig_name` commence déjà par `"Animated_"` puis `+1` — motif de comptage simple (pas de recherche du maximum comme `create-ico`'s `ICO{NNN}_`), donc une collision reste possible si une entrée `Animated_N` a été renommée ou supprimée entre deux créations dans la même session.

**Nettoyage mémoire explicite** : les images assemblées sont fermées après la sauvegarde, les frames sources (`entry["img"]` de `self._gif_images`) via `_free_gif_images()` après insertion de la nouvelle entrée.

**Nouvelle entrée créée via `create_entry()`** (skill `archive-image-loading`, contrairement à `create-ico` qui construit son dict manuellement) — `new_entry["source_archive"] = "loose"` forcé après coup, insérée en fin de mosaïque (`state.images_data.append`, pas à une position relative à une source comme `create-ico`/`page-split`), suivie de `sync_pages_in_xml_data` (skill `comicinfo-metadata-editor`).

**Un seul `save_state()` après création, aucun avant** — même famille de pattern que `create-ico`/`page-split` : il s'agit d'un ajout de nouvelle entrée à la mosaïque, pas d'une modification en place d'une entrée existante, donc pas de "avant" à capturer au sens du skill `apply-image-operation`.

## Fermeture et libération mémoire — `closeEvent`

`_free_gif_images()` est appelée systématiquement à la fermeture (`closeEvent` pour la croix et Annuler, `reject` pour Échap, qui ne passe pas par `closeEvent`) — ferme chaque `entry["img"]` PIL encore ouvert dans `self._gif_images` et vide le cache `_thumb_px`. Nécessaire car la fenêtre peut avoir chargé un nombre arbitraire d'images complètes en mémoire (jusqu'à toutes les frames extraites d'un long GIF en mode édition) sans jamais les avoir validées dans la mosaïque.

## Points d'entrée UI

Quatre chemins, tous via `callbacks['show_animated_gif_dialog'](entries)`. Les conditions d'activation sont vérifiées côté appelant (au moment de construire le menu ou de rafraîchir l'état de l'icône), jamais dans `AnimatedGifDialog` lui-même.

1. **"Créer un GIF animé"** (`context_menu.image.create_animated_gif`) — menu contextuel (`show_image_context_menu`, `context_menus_qt.py`) et menu Images de la barre de menu (`_populate_images_menu`, `menubar_qt.py`), juste au-dessus de "Modifier le GIF animé". Actif si la sélection contient **au moins 2 entrées `is_image`** (`gif_entries`/`can_create_gif`, les entrées non-images de la sélection sont ignorées et ne comptent pas). Transmet ces entrées images, dans l'ordre de la mosaïque (`sorted(selected_indices)`) → branche "création".
2. **"Modifier le GIF animé"** (`context_menu.image.edit_animated_gif`) — mêmes deux menus, actif seulement si **exactement une** image sélectionnée et qu'elle est un GIF animé (`single_entry` + `is_animated_gif` + extension `.gif` ; le menu contextuel exclut en plus une entrée corrompue) → branche "édition".
3. **Dialogue de conversion de format** (`_ConvertFormatDialog._on_convert`, `conversion_dialogs_qt.py`, skill `image-format-conversion`) — choix "GIF animé (sélectionner plusieurs images)" (`dialogs.convert.format_gif_animated`), radio désactivé si une seule image → branche "création" avec les entrées images de la sélection.
4. **Icône `animated_gif` de la colonne d'icônes** (`icon_toolbar_qt.py`, skill `icon-toolbar`, `BTN_Animated_GIF.png`, hors `DEFAULT_LAYOUT` donc proposée parmi les icônes masquées de la fenêtre ⚙) — icône **bi-mode** qui couvre les deux commandes des menus : un seul GIF animé sélectionné (non corrompu) → branche "édition", sinon au moins 2 entrées `is_image` → branche "création", sinon grisée. La logique vit dans `build_icon_toolbar` : `_selected_animated_gif()` et `_gif_create_entries()` (exposées en `state_getters` `selected_animated_gif`/`gif_create_entries`, consommées par la règle `animated_gif` de `_ACTIVATION_RULES`) et `_animated_gif_action()` (callback du clic, teste l'édition en premier). Tooltip dynamique selon le mode : `tooltip_key` (`create_animated_gif`) / `tooltip_key_alt` (`edit_animated_gif`), résolu dans `IconLabel.enterEvent`.

**Conditions d'activation dupliquées sans source commune** : "au moins 2 images" à 4 endroits (menu contextuel, barre de menu, radio `GIF_ANIMATED` du dialogue de conversion, `_gif_create_entries` de la colonne d'icônes), "un seul GIF animé" à 3 endroits (menu contextuel, barre de menu, `_selected_animated_gif` de la colonne d'icônes) — toute modification d'une règle doit être reportée partout. Écart existant : la barre de menu n'exclut pas un GIF corrompu pour "Modifier le GIF animé", alors que le menu contextuel et l'icône l'excluent.

Le callback `show_animated_gif_dialog` du dict de `build_menubar_callbacks` (`menubar_callbacks_qt.py`, partagé par la barre de menu, le menu contextuel et la colonne d'icônes) accepte une entrée seule ou une liste (`[e] if not isinstance(e, list) else e`).

Callbacks (`PanelWidget._animated_gif_callbacks()`, `panel_widget.py`) : `parent`, `state`, `save_state`, `render_mosaic`, `update_button_text`, pas de `canvas`. `AnimatedGifDialog.__init__` mémorise `self._state = callbacks.get("state") or _state_module.state` : c'est dans ce state que le GIF créé est ajouté (`images_data.append`, comptage `Animated_N`, `sync_pages_in_xml_data`). La fenêtre étant non modale, l'utilisateur peut cliquer dans l'autre panneau avant "Créer" — ce qui fait basculer le singleton global (skill `panels`) — sans que le GIF change de destination. Tout nouvel appelant doit passer le `state` de son panneau, le singleton n'est qu'un repli.

## Traductions

`locales/fr.json`, section `dialogs.gif_animated` : `window_title` (`_wt()`, règle UI n°7), `images_title`/`params_title`, `frame_delay_label`/`fps_slow`/`fps_normal`, `loop_label`/`loop_info`, `disposal_label`/`disposal_0`-`disposal_3`, `optimize`, `metadata_title`/`metadata_dimensions`/`metadata_frames`/`metadata_duration`/`metadata_fps`/`metadata_palette`/`metadata_size`, `comment_label`, `creating_progress`/`creating_normalizing`/`creating_saving` (textes de progression). Clés séparées : `context_menu.image.create_animated_gif`, `context_menu.image.edit_animated_gif`, `dialogs.convert.format_gif_animated` (dialogue de conversion), `messages.warnings.no_images_for_gif`/`no_valid_images`, `messages.errors.gif_creation_failed`, `buttons.create_gif`. Voir skill `add-translation`.

**Absent du mode d'emploi** (`user_guide_qt.py`) — même situation que `add-text-to-image` (skill `user-guide`).

## Comment étendre

- **Ajouter un délai par frame** (actuellement un seul délai global) : nécessiterait un champ éditable par `_ThumbWidget` plutôt qu'un unique `QSpinBox` global, et de transmettre une **liste** de durées à `duration=` plutôt qu'un entier unique (Pillow accepte les deux formes) — changement d'UI significatif, à valider avec l'utilisateur.
- **Changer l'algorithme de normalisation des tailles** (actuellement letterboxing centré sur fond blanc) : uniquement la boucle de composition dans `_create_animated_gif`, section "Normalise en RGB sur un canvas max_w×max_h".
- **Changer la stratégie de palette** (actuellement une palette par image, calculée par Pillow à l'enregistrement) : étape 3 de `_create_animated_gif`. Une quantification faite par MosaicView doit garder la transparence (une image RGBA quantifiée par `convert("P", ADAPTIVE)` la perd).
- **Corriger le risque de collision de nommage** (`Animated_{N}.gif` par comptage plutôt que par maximum) : aligner sur le pattern de `create-ico` (`_get_ico_name`, recherche du numéro maximum existant par regex) plutôt que le comptage actuel — changement mineur mais à confirmer avant de le faire, ce n'est peut-être pas jugé assez risqué pour justifier la modification.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour `AnimatedGifDialog` (non-modale déjà en place, `_wt()` pour le titre déjà en place).

## Tests

`tests/test_animated_gif_creation.py` exécute `AnimatedGifDialog._create_animated_gif` (extraite par `tests/ast_loader.load_methods` ; les réglages de la fenêtre — délai, nombre de lectures, commentaire, élimination, optimisation — sont des objets simples du test) et relit le GIF produit : toile et centrage sur blanc, délai commun ou délai propre à chaque image tant que le réglage n'a pas changé, transparence, nombre de lectures non écrit pour un GIF joué une fois, commentaire, noms `Animated_N`, rien pendant une opération ni sans page. `_extract_gif_frames` (ré-édition) est testée par `tests/test_gif_frame_extraction.py`.

## Pièges connus

- **Deux chemins d'entrée radicalement différents dans le même `__init__`** — toute modification du constructeur doit être testée dans les deux cas (édition d'un GIF existant vs création depuis une sélection multiple), pas un seul.
- **Le délai moyen remplace les délais individuels par frame** lors d'une ré-édition — une variation de rythme dans le GIF d'origine est aplatie au premier ré-export depuis cette fenêtre.
- **Palette imposée depuis la première frame** — un jeu de frames aux couleurs dominantes très différentes entre elles peut produire un résultat dégradé sur les frames autres que la première.
- **Pas de worker QThread, `QApplication.processEvents()` manuel** — contrairement à `rotate-flip`/`page-resize`, un traitement très long (beaucoup de frames haute résolution) bloque le thread UI par blocs plutôt que réellement en arrière-plan ; pas de bouton Annuler pendant la création.
- **Nommage par comptage, pas par maximum** — risque de collision de nom si une entrée `Animated_N` a été renommée/supprimée entre deux créations, contrairement à `create-ico`.
- **Un seul `save_state()`, aucun avant** — comme `create-ico`/`page-split`, cohérent avec le fait qu'il s'agit d'un ajout de nouvelle entrée.
- **`_free_gif_images()` doit être appelée à la fermeture, y compris annulation et Échap** (`closeEvent` et `reject`) — sans ça, les PIL Images chargées en mode édition (potentiellement toutes les frames d'un long GIF) resteraient en mémoire. La fenêtre est en `WA_DeleteOnClose` : détruite à sa fermeture. Son `reject` ne doit pas appeler `close()` (`QDialog.closeEvent` rappelle `reject`) : il libère puis appelle `super().reject()`.
- **La suppression de frame est bloquée à 1 minimum**, pas 0 — ne pas supposer qu'une liste vide de frames est un état atteignable depuis l'UI.

## Références croisées

- `archive-image-loading` — détection `is_animated_gif`/`gif_frame_count`/`gif_durations` dans `create_entry()`, `get_gif_frame()` pour le lazy loading des frames, `create_entry()` réutilisé (contrairement à `create-ico`) pour la nouvelle entrée GIF créée.
- `viewers` — `ImageViewer` (visionneuse principale de lecture) a son propre mode de lecture animée qui consomme les mêmes `is_animated_gif`/`get_gif_frame`, mais pour l'affichage/lecture, pas l'édition ; aucun code partagé avec `AnimatedGifDialog`.
- `create-ico` — comparaison utile sur le pattern "ajout de nouvelle entrée, un seul `save_state` après" ; `create-ico` construit son entrée manuellement quand ce fichier réutilise `create_entry()`, et nomme par recherche du maximum plutôt que par comptage.
- `page-split` — autre exemple du pattern à un seul point undo pour un ajout de page(s).
- `apply-image-operation` — ce fichier ne suit pas le pattern documenté (pas de `save_state` avant, pas de modification de `entry['bytes']` existant) pour la même raison que `create-ico` : il s'agit d'un ajout, pas d'une modification en place.
- `comicinfo-metadata-editor` — `sync_pages_in_xml_data` après insertion de la nouvelle entrée GIF.
- `qt-context-menus` / `menu-bar` — entrées "Créer un GIF animé" et "Modifier le GIF animé" du menu contextuel et du menu Images.
- `image-format-conversion` — radio `GIF_ANIMATED`, troisième point d'entrée, même condition "au moins 2 images".
- `icon-toolbar` — icône bi-mode `animated_gif`, quatrième point d'entrée (création ou édition selon la sélection).
- `user-guide` — absence actuelle de section dédiée, à vérifier si une tâche touche à ce fichier.
- `nfo-editor` — autre créateur de nouvelle entrée du projet qui réutilise `create_entry()` (comme ce fichier, contrairement à `create-ico`), mais suit le pattern standard à deux `save_state` plutôt que le pattern à un seul suivi ici.
- `save-export` — `_check_animated_gifs_qt` avertit avant sauvegarde CBZ que les GIFs animés seront figés sur leur frame courante ; voir ce skill pour la chaîne de validation complète avant écriture.
