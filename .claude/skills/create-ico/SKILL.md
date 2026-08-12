---
name: create-ico
description: Localiser ou modifier la création de fichiers .ico multi-résolution dans MosaicView (fenêtre 2 phases découpe/transparence, blocage de sauvegarde CBZ tant qu'un .ico traîne dans la mosaïque). Utiliser dès qu'une tâche touche à ico_creator_qt.py, IcoCreatorDialog, ou _check_no_ico.
---

# Création de fichier .ico — MosaicView

Fenêtre dédiée à 2 phases qui transforme une image **sélectionnée** de la mosaïque en un fichier `.ico` Windows multi-résolution (16/32/48/64/128/256 px), inséré comme nouvelle entrée juste après l'image source. Usage typique : générer une icône d'application ou de raccourci à partir d'une image existante, sans quitter MosaicView.

**Fichier unique — `modules/qt/ico_creator_qt.py`** (~1180 lignes), une seule classe `QDialog` (`IcoCreatorDialog`) qui reconstruit entièrement son contenu entre les deux phases (`_build_phase_a()`/`_build_phase_b()`, widget de phase précédent détruit et remplacé, pas deux fenêtres séparées).

## Phase A — découpe carrée

Objectif : réduire l'image source à une zone **carrée** (les `.ico` sont toujours carrés), via un cadre rouge interactif très proche de celui de `page-crop` — mêmes principes de poignées de coin/bord/déplacement, mêmes curseurs, mais implémentation **indépendante** (`_CropCanvas`, pas `_ViewerCanvas` de `image_viewer_qt.py`, aucun code partagé) avec une contrainte supplémentaire : le cadre reste **toujours carré**, jamais rectangulaire.

- **Contrainte carrée systématique** : `_constrain_square_from_corner`/`_constrain_square_from_edge` recalculent toujours un côté égal en largeur et hauteur, quelle que soit la poignée déplacée — glisser un coin agrandit dans la diagonale (le côté opposé au point d'ancrage bouge à la même distance en x et y, `side = max(abs(dx), abs(dy))`), glisser un bord recentre le carré autour de son centre précédent (`_constrain_square_from_edge`, garde `cx`/`cy` fixes et ajuste le demi-côté symétriquement) plutôt que de simplement translater un seul côté.
- **Zoom molette+Ctrl** (`Ctrl+Molette`, `Ctrl++`/`Ctrl+-`/`Ctrl+0`) et **pan clic droit** — mêmes raccourcis que les autres visionneuses (skill `viewers`), mais implémentation locale à ce fichier.
- **Coordonnées persistantes relatives** (`rect_rel_x1`/`rect_rel_y1`/`rect_rel_size`, 0-1) — même principe que `page-crop` (`crop_rel_x1`...) pour survivre aux changements de zoom/pan/redimensionnement de fenêtre.
- **Deux façons de valider** : "Valider la découpe" (`_on_validate_crop`, garde le cadre carré tracé) ou "Valider sans découper" (`_on_validate_no_crop`, ignore le cadre et fait un **letterboxing** — l'image entière est centrée sur un canevas carré transparent de côté `max(largeur, hauteur)`, sans perte de contenu ni recadrage réel). Double-clic à l'intérieur du cadre (`on_double_click`) équivaut au premier bouton.
- **Undo/redo local à la phase A** (`_undo_stack_a`/`_redo_stack_a`) : capture `(rect_rel_x1, rect_rel_y1, rect_rel_size)` **avant** chaque début de glissement du cadre (`on_drag_start` → `_undo_push_a`, appelé dès `mousePressEvent`, pas seulement à la fin du geste) — indépendant de l'historique global de l'application (`save_state`), aucune interaction avec le undo/redo de la mosaïque à ce stade puisque rien n'est encore appliqué à `entry['bytes']`.

## Transition A → B — `_prepare_phase_b`

Que la validation vienne du crop ou du "sans découper", le résultat est systématiquement redimensionné à **256×256** (`Image.LANCZOS`) et stocké dans `self._ico_img_rgba` — c'est cette image 256×256 qui sera travaillée en phase B, puis réduite aux 6 résolutions standard (`_ICO_SIZES`) uniquement à la sauvegarde finale, pas avant. Le nom de fichier final (`self._ico_name`) est calculé à cet instant (voir section nommage), les piles undo/redo de la phase B sont vidées, les raccourcis clavier de la phase A désactivés.

## Phase B — transparence par pipette flood-fill

Objectif : rendre transparentes les zones de fond de l'image 256×256 (typiquement un fond uni autour du sujet), affichée sur un damier gris pour visualiser l'alpha en temps réel.

- **Pipette** (`_btn_pipette`, bouton bascule) : active `_TransparencyCanvas.pipette_active`, change le curseur en croix. Un clic gauche dans cet état déclenche `_on_pipette_click(img_x, img_y)`.
- **Algorithme flood-fill 4-connexe** — `_apply_transparency(px, py, tolerance)` (`ico_creator_qt.py:1085`) : lit la couleur RGB du pixel cliqué comme référence, puis propage par une pile explicite (pas de récursion, évite tout risque de dépassement de pile Python sur une grande zone à 256×256 = jusqu'à 65536 pixels) aux 4 voisins directs (haut/bas/gauche/droite, pas les diagonales) tant que leur écart de couleur (`max(abs(dr), abs(dg), abs(db))`, distance de Chebyshev, pas euclidienne) reste sous la **tolérance** réglée par curseur. Chaque pixel accepté voit son canal alpha mis à `0` directement via `pixels[cx, cy] = (pr, pg, pb, 0)` (mutation en place du buffer PIL `.load()`, pas de `putpixel` un par un — plus rapide sur une grande zone).
- **Piège** : un pixel déjà transparent (`ref[3] == 0`) au point cliqué fait sortir immédiatement de la fonction sans rien faire — cliquer une seconde fois sur une zone déjà rendue transparente est un no-op silencieux, pas une erreur.
- **Tolérance** (`FocusSlider`, 0 à 255, défaut 15) — réglable en direct avant chaque clic pipette, pas de prévisualisation en overlay pendant l'ajustement du slider (l'effet n'apparaît qu'au clic suivant).
- **Undo/redo local à la phase B** (`_undo_stack_b`/`_redo_stack_b`) : contrairement à la phase A qui ne stocke que 3 flottants, ici chaque entrée est une **copie complète** de l'image PIL 256×256 (`self._ico_img_rgba.copy()`) — capturée avant chaque application de pipette (`_on_pipette_click` → `_undo_push_b`). Coût mémoire plus élevé mais nécessaire puisqu'un flood-fill n'est pas décrit par une poignée de paramètres réutilisables comme un simple rectangle.
- **"Retour à la découpe"** (`_on_back_to_crop`) : revient à la phase A en réinitialisant le zoom, **perd tout le travail de transparence de la phase B** (pas de sauvegarde croisée entre phases) — un aller-retour reconstruit `_ico_img_rgba` depuis zéro à la prochaine validation de la phase A.

## Undo/redo partagé — un seul jeu de boutons, deux piles distinctes

Les boutons Undo/Redo de la barre du bas sont **physiquement différents par phase** (`_btn_undo_a`/`_btn_redo_a` vs `_btn_undo_b`/`_btn_redo_b`, reconstruits à chaque `_build_phase_a`/`_build_phase_b`) mais `_undo()`/`_redo()` sont des méthodes **uniques** sur `IcoCreatorDialog` qui aiguillent selon `self._phase` (`'a'` ou `'b'`) vers la pile appropriée — pas de confusion possible entre les deux car les piles ne sont jamais consultées hors de la phase à laquelle elles appartiennent.

## Nommage automatique — `_get_ico_name` (`ico_creator_qt.py:550`)

Motif `ICO{NNN}_{nom_original}.ico`, `NNN` sur 3 chiffres. Le numéro est calculé en scannant **toutes** les entrées déjà présentes dans `images_data` à la recherche du motif `^ICO(\d+)_` (regex insensible à la casse) et en prenant le maximum existant + 1 — garantit l'absence de collision même après plusieurs créations successives ou une suppression partielle, sans compteur persistant séparé à synchroniser.

## Validation finale — `_on_validate_final` (`ico_creator_qt.py:1119`)

1. `self._ico_img_rgba.save(buf, format="ICO", sizes=_ICO_SIZES)` — **Pillow génère lui-même les 6 résolutions** (16/32/48/64/128/256) à partir de la seule image 256×256 travaillée, un seul appel `save()` produit le fichier `.ico` multi-résolution complet ; pas de redimensionnement manuel par résolution dans ce fichier.
2. Construction d'un **nouvel objet entrée** (`new_entry`, dict minimal : `orig_name`/`extension`/`bytes`/`img`/`is_image`/`thumb`/`img_id`/`qt_pixmap_large`) — **pas** via `create_entry()` (skill `archive-image-loading`), contrairement au point de passage habituel du projet pour créer une entrée ; champs volontairement réduits au strict nécessaire pour l'affichage immédiat dans la mosaïque.
3. **Insertion juste après l'image source** (`state.images_data.insert(self._idx + 1, new_entry)`) — l'image d'origine n'est jamais retirée ni modifiée, comme `page-split` (résultat ajouté, source conservée) plutôt que comme `page-crop`/`rotate-flip` (source remplacée en place).
4. `sync_pages_in_xml_data(state)` (skill `comicinfo-metadata-editor`) pour intégrer la nouvelle page au `ComicInfo.xml` si présent.
5. **Un seul `save_state_qt(...)` après insertion, aucun avant** — contrairement au pattern à deux appels documenté dans le skill `undo-redo` et suivi par la quasi-totalité des autres opérations d'image du projet ; cohérent avec `page-split` qui a la même particularité (un seul point undo, pas deux) puisqu'il s'agit ici aussi d'un **ajout** de page plutôt que d'une modification en place.
6. `MsgDialog` de succès (`success_title`/`success_message`) centré sur `self.parent()` (le panneau source), **pas sur `self`** — commentaire explicite dans le code : `self.close()` est appelé juste après, centrer sur `IcoCreatorDialog` donnerait une géométrie invalide au moment où le centrage différé s'exécuterait (piège déjà documenté en général dans CLAUDE.md, règle UI n°5, "second dialogue déclenché par un premier qui se ferme aussitôt").

## Blocage de sauvegarde tant qu'un `.ico` traîne dans la mosaïque — `_check_no_ico`

**Mécanisme central à connaître, situé hors de ce fichier** : `modules/qt/file_operations_qt.py::_check_no_ico(parent)` (ligne 60) vérifie si `state.images_data` contient une entrée dont `orig_name` se termine par `.ico`, et bloque l'opération avec un `MsgDialog` (`dialogs.ico_creator.save_blocked_title`/`save_blocked_message`) si c'est le cas. Appelé à **4 points de sauvegarde CBZ distincts** dans `file_operations_qt.py` (lignes 994, 1097, 1267, 1381 — sauvegarde directe, "enregistrer sous", création de CBZ, export de sélection ; grep `_check_no_ico` pour la liste exhaustive à jour plutôt que de supposer que ces 4 emplacements suffisent après une modification de ce fichier).

**Raison** : un `.ico` n'est pas une page de bande dessinée normale — le laisser dans un CBZ produirait une archive avec un fichier hors format que les lecteurs de comics ne sauraient pas afficher correctement. Le blocage force l'utilisateur à retirer (Suppr) ou déplacer (Ctrl+X, skill `clipboard`) le `.ico` avant de pouvoir sauvegarder, plutôt que de le laisser silencieusement polluer l'archive.

Ce n'est **pas** un mécanisme de undo/redo ni une validation dans `IcoCreatorDialog` lui-même — c'est un garde-fou global, découplé, qui s'applique à n'importe quel `.ico` présent dans la mosaïque quelle que soit son origine (créé par ce dialogue, importé depuis le web, glissé-déposé, etc.).

## Traitement du `.ico` ailleurs dans le projet

Une fois inséré dans la mosaïque, un `.ico` reste une entrée `is_image: True` comme les autres et peut en théorie subir les mêmes opérations (rotation, ajustements...) — avec un cas particulier notable dans `AdjustmentViewerDialog` (skill `viewers`/`adjustments-panel`) : `adjustments_viewers_qt.py:1401`, l'export après ajustement de transparence détecte `ext == '.ico'` et force `fmt = 'ICO'` pour la sauvegarde plutôt que `PNG` — sans ce cas particulier, un `.ico` retravaillé perdrait son format multi-résolution au profit d'un PNG simple.

## Points d'entrée UI

Trois, tous nécessitant **exactement une seule** image sélectionnée (`create_ico_from_selected`, `ico_creator_qt.py:1163` — retourne silencieusement, sans message d'erreur, si la sélection est vide, multiple, invalide ou corrompue ; contrairement à `page-resize`/`page-crop` qui affichent des `MsgDialog` dédiés pour ces cas) :

1. **Menu contextuel** (clic droit mosaïque, skill `qt-context-menus`) — `context_menus_qt.py:462`, clé `context_menu.image.create_ico`.
2. **Barre de menu** — `menubar_qt.py:226`, même clé.
3. **Colonne d'icônes** (skill `icon-toolbar`) — bouton id `"create_ico"` (`icon_toolbar_qt.py:68`, icône `BTN_ICO.png`, **pas de tooltip dédié** `tooltip_key: None`, comme `page-resize`/`page-crop` — utilise le libellé générique `context_menu.image.create_ico`), activé si `single_image_selected()`.

Callbacks (`PanelWidget._ico_callbacks()`, `panel_widget.py:1466`) : seulement `render_mosaic`, `refresh_toolbar`, `state` — **pas de `save_state`** dans le dict transmis (l'appel se fait directement via `save_state_qt(state, ...)` importé en tête de fichier, pas via un callback injecté comme dans les autres skills d'édition d'image).

## Traductions

`locales/fr.json`, section `ico_creator` (ligne 1067) : `title` (résolu via `_wt()`, règle UI n°7), tous les libellés des deux phases (`btn_validate_crop`/`btn_validate_no_crop`/`btn_cancel`/`btn_transparency`/`btn_back_to_crop`/`btn_validate_final`/`tolerance_label`/`info_message`), `success_title`/`success_message`, et **`save_blocked_title`/`save_blocked_message`** pour le garde-fou de `_check_no_ico` (logiquement rattachées à cette section malgré leur usage dans un fichier différent). `overwrite_title`/`overwrite_message` existent aussi dans cette section mais ne sont référencées nulle part dans `ico_creator_qt.py` ni `file_operations_qt.py` au moment de la rédaction de ce skill — à vérifier avant de les considérer comme mortes ou comme appelées depuis un chemin non trouvé par la recherche. Voir skill `add-translation`.

**A une section dans le mode d'emploi** (`user_guide_qt.py:640`, clé `help.create_icon`/`help.create_icon_content`) — comme `page-resize`/`page-crop`, contrairement à `add-text-to-image` (skill `user-guide`).

## Comment étendre

- **Ajouter/retirer une résolution générée** (actuellement `_ICO_SIZES = [(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)]`) : une seule constante en tête de fichier, transmise telle quelle à `Image.save(..., sizes=...)`.
- **Changer l'algorithme de détection de couleur similaire** (actuellement distance de Chebyshev max des 3 canaux RGB) : `_apply_transparency`, la ligne `if max(abs(pr - r), abs(pg - g), abs(pb - b)) > tolerance`.
- **Passer le flood-fill en 8-connexe** (inclure les diagonales) : ajouter les 4 voisins diagonaux à la liste dans la boucle `for nx, ny in (...)`.
- **Autoriser un cadre rectangulaire en phase A** (actuellement toujours contraint carré) : changement de nature du fichier, `.ico` exige des icônes carrées par spécification Windows — ne pas faire sans confirmation explicite, cette contrainte n'est probablement pas négociable.
- **Faire persister le travail de transparence à un retour en phase A** (actuellement perdu, voir section phase B) : nécessiterait de conserver `_ico_img_rgba` avant reconstruction plutôt que de le régénérer entièrement à la prochaine validation de phase A — changement de comportement notable, à valider avec l'utilisateur avant de l'implémenter.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour `IcoCreatorDialog` (non-modale déjà en place, `_wt()` pour le titre déjà en place).

## Pièges connus

- **Contrainte carrée systématique en phase A** — toute poignée de redimensionnement recalcule un côté égal, ne jamais introduire de logique qui permettrait un rectangle.
- **Un seul `save_state_qt()` après insertion, aucun avant** — comme `page-split`, pas le pattern standard à deux appels ; cohérent puisqu'il s'agit d'un ajout de page, pas d'une modification en place.
- **Nouvelle entrée construite manuellement, pas via `create_entry()`** — champs volontairement réduits ; si un champ attendu ailleurs dans le projet manque sur une entrée `.ico` fraîchement créée, vérifier ici en premier plutôt que dans `entries.py`.
- **Retour "à la découpe" perd la phase B sans confirmation** — pas de message d'avertissement avant de perdre le travail de transparence en cours.
- **Clic pipette sur pixel déjà transparent = no-op silencieux**, pas une erreur.
- **`_check_no_ico` est un garde-fou global découplé**, pas une validation locale à `IcoCreatorDialog` — un `.ico` bloque la sauvegarde CBZ quelle que soit son origine, pas seulement ceux créés par ce dialogue.
- **`overwrite_title`/`overwrite_message` potentiellement des clés mortes** — non retrouvées dans le code au moment de la rédaction de ce skill, à vérifier avant de s'appuyer dessus.
- **Pas de callback `save_state` dans `_ico_callbacks`** — contrairement au pattern des autres skills d'édition d'image, l'appel undo/redo se fait via un import direct de `save_state_qt`.

## Références croisées

- `page-crop` — cadre rouge interactif très similaire en interaction (poignées, curseurs, coordonnées relatives persistantes) mais implémentation totalement indépendante (`_CropCanvas` ici vs `_ViewerCanvas` là-bas) et avec une contrainte supplémentaire (carré forcé).
- `page-split` — seule autre fonctionnalité du projet avec le même pattern "un seul `save_state`, la source reste en place, le résultat est inséré juste après" plutôt que le pattern standard à deux appels.
- `viewers` — cas particulier de sauvegarde `.ico` dans `AdjustmentViewerDialog` (`adjustments_viewers_qt.py:1401`) pour ne pas perdre le format multi-résolution après un ajustement de transparence sur une entrée `.ico`.
- `apply-image-operation` — ce fichier ne suit pas le pattern documenté (pas de `save_state` avant modification, entrée construite manuellement) car il s'agit d'un **ajout** de nouvelle entrée, pas d'une modification de `entry['bytes']` existant.
- `archive-image-loading` — `create_entry()`, le point de passage habituel du projet pour créer une entrée, volontairement contourné ici.
- `undo-redo` — `save_state_qt` appelé directement plutôt que via le callback `save_state` injecté habituel.
- `comicinfo-metadata-editor` — `sync_pages_in_xml_data` après insertion de la nouvelle entrée `.ico`.
- `icon-toolbar` — bouton "create_ico" de la colonne d'icônes (sans tooltip dédié, comme "resize"/"crop").
- `qt-context-menus` — entrée du menu contextuel clic droit.
- `save-export` — `_check_no_ico` bloque toute sauvegarde CBZ tant qu'une entrée `.ico` traîne dans la mosaïque ; voir ce skill pour le détail des 6 méthodes de sauvegarde concernées.
- `clipboard` — Ctrl+X pour déplacer un `.ico` bloquant la sauvegarde vers un autre panneau plutôt que le supprimer.
- `user-guide` — section `help.create_icon` existante, à maintenir à jour.
- `animated-gif` — même famille de pattern "ajout de nouvelle entrée, un seul `save_state` après" ; `animated-gif` réutilise `create_entry()` (contrairement à ce fichier qui construit son dict manuellement) et nomme par comptage plutôt que par recherche du maximum.
- `nfo-editor` — la création d'un `.nfo` suit au contraire le pattern standard à deux `save_state`, contrairement à ce fichier et à `animated-gif`/`page-split` — à noter si une cohérence inter-créateurs d'entrée est un jour recherchée.
