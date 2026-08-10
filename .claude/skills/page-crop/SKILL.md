---
name: page-crop
description: Localiser ou modifier le recadrage (crop) d'une page (rectangle rouge interactif dans la visionneuse principale, validation par bouton ou double-clic). Utiliser dès qu'une tâche touche à _ViewerCanvas, ImageViewer.perform_crop, ou au bouton/menu "Recadrer".
---

# Recadrage d'une page — MosaicView

Recadrage interactif d'une image par rectangle de sélection ("rubber-band") tracé directement dans la **visionneuse principale de lecture** (`ImageViewer`, `modules/qt/image_viewer_qt.py`) — pas une fenêtre dédiée séparée comme `page-straighten`/`add-text-to-image`/`clone-zone`. L'utilisateur trace, ajuste et valide un rectangle rouge par-dessus l'image affichée ; la validation recadre définitivement l'image aux pixels PIL.

**Distinct du crop indépendant de `ico_creator_qt.py`** (traductions `dialogs.ico_creator.btn_validate_crop`/`btn_validate_no_crop`) — mécanisme séparé pour la création de fichiers `.ico`, hors périmètre de ce skill, aucun code partagé.

## Un seul fichier, pas de fenêtre dédiée — `modules/qt/image_viewer_qt.py`

Le crop n'a pas son propre fichier ni sa propre classe `QDialog` : il est **intégré** au canvas de la visionneuse principale (`_ViewerCanvas`, une `QLabel`) et à la classe `ImageViewer` elle-même. Toute la logique tient dans deux zones du fichier :

- **`_ViewerCanvas`** (`image_viewer_qt.py:58`) — état du rectangle (`_crop_start`/`_crop_end` en coordonnées **widget**, `crop_rel_x1`/`crop_rel_y1`/`crop_rel_x2`/`crop_rel_y2` en coordonnées **relatives 0-1**, persistantes entre les zooms), dessin du rectangle rouge (`paintEvent`), détection des poignées de redimensionnement, gestion souris complète (tracé initial, redimensionnement par les bords/coins, déplacement, double-clic de validation).
- **`ImageViewer.validate_crop()`/`perform_crop()`** (`image_viewer_qt.py:1477`/`1488`) — validation et application réelle du recadrage en pixels PIL.

Voir skill `viewers` pour le tableau des 5 visionneuses plein-écran du projet et l'avertissement général (implémentations séparées, aucun code partagé entre elles).

## Tracer, ajuster, déplacer le rectangle — `_ViewerCanvas`

- **Nouveau rectangle** : clic gauche sur une zone sans rectangle existant → `_crop_start` fixé, le rectangle grandit avec la souris tant que le clic est maintenu (`mouseMoveEvent`) — mais seulement au-delà d'un seuil de **15 pixels de déplacement**, pour distinguer un vrai tracé d'un simple clic accidentel.
- **Poignées de redimensionnement** : 8 zones actives détectées par proximité (`_get_resize_mode`, tolérance de 10px) — 4 coins (`tl`/`tr`/`bl`/`br`, curseurs diagonaux), 4 bords (`left`/`right`/`top`/`bottom`, curseurs horizontaux/verticaux), plus l'intérieur du rectangle (`move`, curseur "déplacer tout"). Le curseur de la souris change dynamiquement selon la zone survolée même hors interaction (`mouseMoveEvent`, branche sans bouton enfoncé).
- **Redimensionnement/déplacement d'un rectangle existant** : reclic sur une poignée (`_resize_mode` fixé) plutôt que sur une zone vide — modifie `_crop_start`/`_crop_end` selon la poignée active, jamais les deux à la fois sauf en mode `move`.
- **Rectangle trop petit rejeté silencieusement** : si la largeur ou la hauteur finale est `< 10px`, le rectangle est annulé (`_crop_start`/`_crop_end` remis à `None`) sans message d'erreur — évite un crop qui produirait une image quasi nulle par une manipulation accidentelle.
- **Piège du double-clic contre un simple clic sur rectangle existant** : cliquer sur un rectangle déjà tracé (hors poignée) déclenche `_waiting_for_double_click = True` plutôt qu'un déplacement immédiat — le code attend de voir si un second clic rapide suit (double-clic = validation, voir plus bas) ou si la souris bouge significativement (glissement = déplacement du rectangle réinterprété après coup). Ce délai est nécessaire car Qt ne peut pas savoir à l'avance si un clic sera suivi d'un second avant que l'utilisateur n'agisse.
- **Persistance relative entre zooms** (`crop_rel_x1`...`crop_rel_x2`, calculés à chaque relâchement de clic) : le rectangle est stocké aussi en fractions 0-1 de l'image affichée, pas seulement en pixels widget — permet à `display_image(keep_crop_rect=True)` de replacer le rectangle au bon endroit après un changement de zoom/pan/redimensionnement de fenêtre sans perdre la sélection (voir `image_viewer_qt.py:1353-1359`, reconversion inverse relatif → widget à chaque réaffichage).
- **Pan clic-droit maintenu (`mouseMoveEvent`, branche `Qt.RightButton`)** : quand un rectangle est actif (`has_crop`), le pan appelle explicitement `self._viewer.display_image(keep_crop_rect=True)` à chaque déplacement, au lieu d'un simple `self.update()` — c'est ce qui déclenche la reconversion relatif → widget décrite ci-dessus et garde le rectangle collé à l'image pendant le pan. **Piège corrigé (2026-08, v1.7.2)** : avant ce correctif, le pan se contentait de translater `display_offset_x/y` et de repeindre, sans jamais recalculer `_crop_start`/`_crop_end` — le rectangle restait figé à sa position écran pendant que l'image glissait dessous, donnant l'impression que le cadre de découpe "ne suit pas" le déplacement. Le zoom, lui, appelait déjà `display_image(keep_crop_rect=True)` (voir `_on_wheel`) et n'a jamais eu ce problème. Sans rectangle actif (`not has_crop`), le pan reste un simple `self.update()` — pas de coût inutile tant qu'il n'y a rien à resynchroniser.

## Bouton "Valider" flottant

`_show_validate_btn()`/`_hide_validate_btn()` — un `QPushButton` flottant positionné en bas au centre du canvas (pas dans la barre d'outils de la visionneuse), affiché uniquement lorsqu'un rectangle valide existe. Repositionné à chaque `resizeEvent` du canvas tant qu'il est visible. Cliquer dessus appelle `ImageViewer.validate_crop()`.

## Validation par double-clic — raccourci alternatif au bouton

`mouseDoubleClickEvent` : un double-clic **à l'intérieur** du rectangle existant déclenche directement `validate_crop()` (équivalent au bouton). Un double-clic **ailleurs** (pas de rectangle, ou double-clic hors rectangle) bascule le plein écran de la visionneuse à la place (`toggle_fullscreen()`) — le même geste a donc deux significations différentes selon le contexte. `_ignore_crop_events` bloque temporairement (100ms après validation, 1000ms après bascule plein écran) toute nouvelle interaction de crop pour absorber les événements souris résiduels du double-clic sans les réinterpréter comme un nouveau tracé.

## Basculement automatique double page → simple page

**Piège de comportement à connaître** : si un rectangle est tracé alors que la visionneuse est en mode double page (`page_mode != "single"`, voir skill `viewers` pour les 4 modes de lecture), `mouseReleaseEvent` (`image_viewer_qt.py:470`) bascule automatiquement en mode simple page dès qu'un rectangle valide est terminé — et sélectionne comme page courante celle des deux pages affichées dont le centre du rectangle est le plus proche (`rect_cx < center_x` → page gauche, sinon page droite). Le crop ne s'applique jamais à un couple de pages double-affichées simultanément ; il est toujours résolu à une seule page avant validation.

## Application réelle — `perform_crop()` (`image_viewer_qt.py:1488`)

1. Conversion des coordonnées widget du rectangle vers les coordonnées de l'image **source** (pas affichée) : soustrait l'offset d'affichage (`display_offset_x`/`display_offset_y`), clampe aux limites du canvas affiché, puis divise par `zoom_level` pour revenir à l'échelle réelle des pixels de l'image d'origine.
2. Garde-fou `orig_x2 <= orig_x1 or orig_y2 <= orig_y1` → `MsgDialog` `crop_invalid`, annule sans modifier l'image.
3. `save_state()` **avant** modification — **sans `force=True`**, contrairement au pattern généralement recommandé par le skill `apply-image-operation` pour les opérations anticipatives (voir aussi `page-straighten`/`page-resize` pour d'autres écarts déjà documentés à ce même pattern).
4. `original_img.crop((orig_x1, orig_y1, orig_x2, orig_y2))` — un simple appel PIL, pas de traitement supplémentaire (pas de redimensionnement après coup, l'image recadrée garde sa résolution native moins la zone retirée).
5. Invalidation cache — variante (A) du skill `apply-image-operation` (`bytes`/`large_thumb_pil`/`qt_pixmap_large`/`qt_qimage_large`/`_hash`, `state.modified = True`), synchronisation `ComicInfo.xml` (skill `comicinfo-metadata-editor`).
6. **Second `save_state()`, également sans `force=True`** — même remarque que pour le premier appel.
7. **`build_qimage_for_entry(entry)` appelé explicitement** avant `canvas.refresh_thumbnail(real_idx)` — même optimisation que celle documentée dans `page-resize` (précalcul de la vignette Qt hors du thread UI), ici exécutée en synchrone puisque le crop n'a pas de worker QThread dédié (une seule image, opération rapide).
8. `self._canvas.clear_crop()` puis `self.display_image()` — le rectangle disparaît après validation, l'image rechargée depuis les nouveaux bytes recadrés.

**Piège de code existant à connaître, pas à corriger sans consigne explicite** : le bloc `except Exception` de `perform_crop()` et le garde-fou `original_img is None` affichent tous deux `MsgDialog(self, "messages.errors.crop_failed.title", "messages.errors.crop_failed.title")` — la clé de **titre** est passée deux fois, la clé de **message** (`messages.errors.crop_failed.message`) n'est jamais utilisée à cet endroit. Vérifier ce point avant de supposer que le message affiché à l'utilisateur en cas d'échec est celui défini dans `locales/fr.json`.

## Points d'entrée UI

Deux façons d'atteindre le crop, structurellement différentes des autres skills d'édition d'image :

1. **`crop_selected_image`** (`PanelWidget._crop_selected_image()`, `panel_widget.py:1534`) — accessible depuis le menu contextuel (`context_menus_qt.py:439`, clé `context_menu.image.crop`), la barre de menu (`menubar_qt.py:214`) et la colonne d'icônes (`icon_toolbar_qt.py:64`, bouton id `"crop"`, icône `BTN_Crop.png`, **pas de tooltip dédié** comme `page-resize` — utilise le libellé `context_menu.image.crop`, activé seulement si `single_image_selected()`). Ce callback **ne fait pas le crop lui-même** : il valide la sélection (exactement une image, garde-fous `no_selection_crop`/`multi_selection_crop`/`invalid_selection_crop`) puis **ouvre simplement la visionneuse principale** sur cette image (`self._open_image_viewer(idx)`) — l'utilisateur doit ensuite tracer le rectangle lui-même.
2. **Directement dans la visionneuse déjà ouverte** — aucun bouton ni menu n'est nécessaire une fois `ImageViewer` affichée : tracer un rectangle avec la souris est le seul déclencheur, qu'on ait ouvert la visionneuse via le crop, un double-clic sur une vignette, ou tout autre chemin (skill `viewers`).

Callbacks utilisés par `perform_crop` (`self.callbacks`, transmis à la construction de `ImageViewer`) : `state`, `save_state`, `render_mosaic`, `update_button_text`, `canvas` — pas de `refresh_status` ni `rollback`, contrairement à `rotate-flip`/`page-resize`.

## Traductions

`locales/fr.json` : `buttons.validate_crop` (`"Valider la découpe"`, texte du bouton flottant, réutilisé nulle part ailleurs — distinct de `dialogs.ico_creator.btn_validate_crop` qui sert un mécanisme différent), `context_menu.image.crop` (`"Recadrer"`, menus + tooltip icône), messages d'erreur `messages.warnings.no_selection_crop`/`multi_selection_crop`/`invalid_selection_crop`/`no_crop_selection`, `messages.errors.crop_invalid`/`crop_failed`. Voir skill `add-translation`.

**A une section dans le mode d'emploi** (`user_guide_qt.py:635`, clé `help.crop`/`help.crop_content`) — comme `page-resize`, contrairement à `page-straighten`/`add-text-to-image`/`clone-zone` (skill `user-guide`).

## Comment étendre

- **Changer le seuil de déclenchement du tracé** (actuellement 15px de déplacement avant de considérer qu'un clic devient un tracé) : deux occurrences distinctes à garder synchronisées — `mouseMoveEvent` (`distance >= 15`) et `mouseReleaseEvent` (`distance < 15`).
- **Changer la tolérance de détection des poignées** (actuellement 10px) : `_get_resize_mode`, une seule constante `tolerance = 10`.
- **Changer la taille minimale d'un rectangle valide** (actuellement 10px de large/haut) : `mouseReleaseEvent`, `if abs(x2 - x1) < 10 or abs(y2 - y1) < 10`.
- **Permettre un crop en mode double page sans forcer le retour en simple page** (comportement actuel décrit plus haut) : changement de comportement notable dans `mouseReleaseEvent` (`image_viewer_qt.py:470`) — ne pas modifier sans confirmation explicite, ce basculement automatique pourrait être un choix délibéré (éviter l'ambiguïté de savoir à quelle des deux pages affichées le crop devrait s'appliquer).
- **Corriger la clé de message d'erreur dupliquée** (`crop_failed.title` utilisé deux fois au lieu de `.message`, voir section dédiée) — signalé ici comme piège connu, à corriger seulement sur demande explicite (règle CLAUDE.md : ne jamais modifier hors du périmètre exact demandé).
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md — `ImageViewer` est déjà conforme (non-modale, `_wt()` pour le titre) puisque partagée avec le reste de la visionneuse (skill `viewers`).

## Pièges connus

- **Pas de fenêtre/classe dédiée** — contrairement à `page-straighten`/`add-text-to-image`/`clone-zone`, le crop vit entièrement à l'intérieur de `_ViewerCanvas`/`ImageViewer`, partagé avec toute la logique d'affichage/zoom/pan/pagination de la visionneuse principale ; une modification imprudente peut affecter la lecture normale des pages, pas seulement le crop.
- **`save_state()` sans `force=True`** aux deux appels de `perform_crop` — écart par rapport au pattern recommandé du skill `apply-image-operation`, à rapprocher des écarts déjà documentés dans `page-straighten`/`page-resize`.
- **Basculement automatique en mode simple page** dès qu'un rectangle est validé en mode double page — le crop ne s'applique jamais à deux pages simultanément, toujours résolu à une seule.
- **Le double-clic a un double sens contextuel** (valider le crop à l'intérieur du rectangle, basculer le plein écran ailleurs) — `_ignore_crop_events` protège contre une réinterprétation des événements souris résiduels, mais toute modification de cette zone doit revalider les deux comportements.
- **Clé de message d'erreur dupliquée** (`crop_failed.title` au lieu de `.message`) dans `perform_crop` — bug préexistant, ne pas le reproduire ailleurs par copier-coller.
- **`crop_selected_image` n'exécute pas le crop** — il ouvre seulement la visionneuse ; ne pas chercher la logique de recadrage à cet endroit du code.
- **Ne pas confondre avec le crop de `ico_creator_qt.py`** — mécanisme entièrement séparé pour la création de fichiers `.ico`, traductions `dialogs.ico_creator.btn_validate_crop`/`btn_validate_no_crop`/`btn_back_to_crop`, hors périmètre de ce skill.

## Références croisées

- `viewers` — `ImageViewer` est la visionneuse principale de lecture, l'une des 5 visionneuses plein-écran du projet ; le crop y est une fonctionnalité intégrée plutôt qu'une visionneuse séparée comme les 4 autres.
- `apply-image-operation` — pattern général d'invalidation de cache ; `perform_crop` suit la variante (A) complète mais s'écarte du pattern `force=True` recommandé aux deux appels de `save_state`.
- `page-straighten` / `page-resize` — autres exemples documentés d'écarts au pattern `force=True` standard de `save_state`.
- `page-resize` — même optimisation de précalcul explicite de la vignette Qt (`build_qimage_for_entry`) avant `refresh_thumbnail`.
- `icon-toolbar` — bouton "crop" de la colonne d'icônes (sans tooltip dédié, comme "resize").
- `qt-context-menus` — entrée du menu contextuel clic droit.
- `comicinfo-metadata-editor` — mise à jour des attributs de page dans `ComicInfo.xml` après recadrage.
- `user-guide` — section `help.crop` existante, à maintenir à jour.
- `create-ico` — cadre rouge interactif très similaire en interaction (poignées, curseurs, coordonnées relatives persistantes) mais implémentation indépendante (`_CropCanvas`, phase A de `IcoCreatorDialog`), avec une contrainte supplémentaire : le cadre y est toujours forcé carré.
