---
name: rotate-flip
description: Localiser ou modifier la rotation 90° gauche/droite et le miroir horizontal/vertical des images sélectionnées de la mosaïque. Utiliser dès qu'une tâche touche à rotate_entry_data/flip_entry_data (image_ops.py) ou image_transforms_qt.py.
---

# Rotation et miroir — MosaicView

Quatre transformations géométriques appliquées aux images **sélectionnées** dans la mosaïque : **rotation gauche** (90° anti-horaire), **rotation droite** (90° horaire), **miroir horizontal** (gauche-droite) et **miroir vertical** (haut-bas). Les 4 partagent la même infrastructure (mêmes fichiers, même worker thread, même pattern d'invalidation), d'où un seul skill plutôt que quatre.

Distinct de la rotation libre du **redressement** (`straighten_tool_qt.py`, angle arbitraire calculé à partir d'une ligne tracée par l'utilisateur) — voir skill `page-straighten`. Aucun code n'est partagé entre les deux mécanismes.

## Les deux fonctions métier — `modules/qt/image_ops.py`

Pas de module séparé par opération ; les 4 transformations tiennent dans 2 fonctions qui suivent exactement le même squelette :

- **`rotate_entry_data(entry, angle, state=None)`** (`image_ops.py:10`) — `angle` : `-90` pour rotation droite (horaire), `90` pour rotation gauche (anti-horaire). Utilise `img.rotate(angle, expand=True)` (PIL) — `expand=True` est essentiel : sans lui, PIL conserverait le canevas d'origine et rognerait l'image pivotée si elle n'est pas carrée.
- **`flip_entry_data(entry, direction, state=None)`** (`image_ops.py:39`) — `direction` : `'horizontal'` → `Image.FLIP_LEFT_RIGHT`, tout le reste (en pratique `'vertical'`) → `Image.FLIP_TOP_BOTTOM`. Pas de validation stricte de la valeur — n'importe quoi d'autre que `'horizontal'` tombe silencieusement dans la branche verticale.

Les deux :
1. Retournent `False` immédiatement si `entry["is_image"]` est faux ou si `ensure_image_loaded(entry)` échoue (voir skill `archive-image-loading`) — pas de message d'erreur, l'appelant (`_TransformWorker`) avale l'exception dans un `try/except Exception: pass` et continue sur l'entrée suivante.
2. Ferment l'ancienne image PIL (`img.close()`) puis remplacent `entry["img"]` par le résultat transformé — **jamais de mutation en place**, un nouvel objet PIL à chaque fois (voir skill `undo-redo`, pourquoi `entry["bytes"]`/`entry["img"]` ne doivent jamais être mutés en place plutôt que réassignés).
3. Réencodent immédiatement en bytes via `save_image_to_bytes(entry)` et invalident `large_thumb_pil`/`_hash` (mais **pas** `qt_pixmap_large`/`qt_qimage_large` — voir section invalidation plus bas, c'est l'appelant qui s'en charge).
4. Si un `state` est fourni et que l'entrée fait partie d'un `ComicInfo.xml` chargé, appellent `update_page_entries_in_xml_data` (voir skill `comicinfo-metadata-editor`) pour que les dimensions `ImageWidth`/`ImageHeight` de la page suivent la rotation (une rotation 90° échange largeur et hauteur).
5. Terminent par `free_image_memory(entry)` (voir skill `archive-image-loading`, lazy loading).

## Orchestration — `modules/qt/image_transforms_qt.py`

Fichier dédié à l'exécution asynchrone des 4 opérations (le nom du fichier date d'avant la scission Qt/tkinter, mentionnée en tête de fichier comme portage de l'ancien `modules/image_transforms.py`).

### Points d'entrée publics

- **`rotate_selected_qt(angle, callbacks)`** (`image_transforms_qt.py:157`)
- **`flip_selected_qt(direction, callbacks)`** (`image_transforms_qt.py:183`)

Chacun : récupère `state.selected_indices`, filtre aux entrées qui sont des images (`is_image`), retourne silencieusement si la sélection est vide ou ne contient aucune image, tague chaque entrée avec `entry["_real_idx"]` (son index réel dans `images_data`, nécessaire plus tard pour rafraîchir la bonne vignette sans dépendre d'une sélection qui peut changer pendant le traitement), appelle **une seule fois** `callbacks['save_state']()` (undo, avant toute modification — **sans `force=True` explicite**, contrairement à ce que documente le skill `apply-image-operation` comme pattern recommandé ; à vérifier avant de copier ce fichier comme modèle pour une nouvelle fonction), puis délègue à `_run_transform()`.

### `_run_transform()` — le worker QThread

Toute la mécanique commune (rotation ET miroir) : lance un `_TransformWorker` (sous-classe `QThread`) qui applique l'opération à chaque entrée l'une après l'autre, avec :
- Un **overlay texte sur le canvas** (`show_canvas_text`/`hide_canvas_text`, skill `canvas-overlay-progress`) affichant la progression en pourcentage (clé `labels.rotating` ou `labels.flipping`) — utile pour une sélection multiple volumineuse.
- Un **bouton Annuler** cliquable pendant le traitement (`_show_cancel_item`, réutilisé depuis `web_import_qt.py`) — positionne `threading.Event()` (`w._cancelled`), le worker vérifie ce flag entre chaque entrée **et** après chaque opération individuelle (une rotation en cours n'est jamais interrompue à mi-chemin).
- **Annulation** (`_cancel()`) : appelle `callbacks['rollback']` (voir skill `undo-redo`, `rollback_to_current_state_qt`) — restaure l'état du sommet de l'historique **sans décrémenter `history_index`**, puisque le `save_state()` initial n'a rien poussé de nouveau (l'état était identique juste avant le lancement).
- **Fin normale** (`on_finished`) : `state.modified = True`, puis pour chaque entrée traitée `canvas.refresh_thumbnail(entry["_real_idx"])` (voir skill `mosaic-thumbnails`) — c'est **ici**, pas dans `rotate_entry_data`/`flip_entry_data`, que `qt_pixmap_large`/`qt_qimage_large` sont effectivement invalidés, via `_regenerate_thumbnail_qt(entry)` appelée par le worker après chaque opération réussie (`image_transforms_qt.py:22`). Puis `canvas.refresh_duplicate_overlay()` (skill `duplicate-detection`), `update_button_text()` (rafraîchit l'état grisé/actif des boutons undo/redo), `refresh_status_fn()` (skill `status-bar`), et **seulement à la fin** un second `save_state_fn()` (redo).
- **Anti-GC** : `_active_workers` (liste module-level) garde une référence Python vivante sur chaque worker tant qu'il tourne — un `QThread` sans référence externe peut être détruit prématurément par le garbage collector Python malgré son thread C++ toujours actif.

C'est donc une **variante particulière** du pattern documenté dans le skill `apply-image-operation` : un seul `save_state()` avant le lot entier (pas par image), invalidation des caches faite par le worker au fil du traitement plutôt que par la fonction métier elle-même, et un chemin d'annulation à mi-lot qui n'existe dans aucune autre fonction d'opération d'image du projet.

## Les 3 points d'entrée UI

Tous convergent vers `MainWindow._image_transforms_callbacks()` (`panel_widget.py:1552` — fournit `save_state`, `render_mosaic`, `update_button_text`, `refresh_status`, `canvas`, `state`, `rollback`) puis vers les 4 lambdas de `menubar_callbacks_qt.py:85-88` :

```python
"rotate_selected_right":    lambda: _rotate_selected_qt(-90, mw._image_transforms_callbacks()),
"rotate_selected_left":     lambda: _rotate_selected_qt(90,  mw._image_transforms_callbacks()),
"flip_selected_horizontal": lambda: _flip_selected_qt('horizontal', mw._image_transforms_callbacks()),
"flip_selected_vertical":   lambda: _flip_selected_qt('vertical',   mw._image_transforms_callbacks()),
```

1. **Menu contextuel** (clic droit sur la mosaïque, voir skill `qt-context-menus`) — sous-menu "Rotation" dans `context_menus_qt.py:396-397` (rotation seulement, pas de miroir dans ce sous-menu) et `rot_menu` équivalent dans `menubar_qt.py:193-194`.
2. **Barre de menu** — mêmes clés `context_menu.image.rotate_right`/`rotate_left`, menu Image.
3. **Colonne d'icônes** (voir skill `icon-toolbar`) — boutons `rotate_left`/`rotate_right`/`flip_horizontal`/`flip_vertical` (`icon_toolbar_qt.py`, icônes `BTN_Rotate_Left.png`/`BTN_Rotate_Right.png`/`BTN_Mirror_Horizontal.png`/`BTN_Mirror_Vertical.png`), activés seulement si `has_selected_images()` (`_ACTIVATION_RULES`), tooltips `tooltip.rotate_left`/`tooltip.rotate_right`/`tooltip.mirror_horizontal`/`tooltip.mirror_vertical` (skill `qt-tooltips`).

**Le miroir horizontal/vertical a bien un bouton dédié dans la colonne d'icônes**, au même titre que la rotation (`ICON_DEFINITIONS` liste bien les 4 : `rotate_left`, `rotate_right`, `flip_horizontal`, `flip_vertical`, chacun avec sa règle d'activation et son callback).

## 4e point d'entrée UI — outil de la visionneuse principale

Un 4e point d'entrée existe : un outil "rotation" dans la barre d'outils flottante de la visionneuse principale (`rotation_tool_qt.py`, icône `BTN_Rotation.png` entre redressage et clonage), avec un panneau flottant à 4 boutons (rotation gauche/droite, miroir horizontal/vertical) appliqués à la page **actuellement affichée** dans la visionneuse — pas à `state.selected_indices` comme les 3 points d'entrée ci-dessus.

**Ne réutilise PAS `rotate_selected_qt`/`flip_selected_qt`/`_run_transform`** (pensés pour un lot potentiellement volumineux, avec worker QThread/overlay de progression/bouton Annuler dédiés) : appelle directement `rotate_entry_data`/`flip_entry_data` de manière synchrone sur l'entrée de la page courante, puisqu'une seule page n'a pas besoin de cette machinerie de lot. Contrairement à `rotate_selected_qt`/`flip_selected_qt`, ce chemin fait l'invalidation de cache **complète** (variante A du skill `apply-image-operation`, pas la variante B partielle documentée plus haut) et appelle `render_mosaic()` lui-même, puisqu'il n'y a pas de worker pour s'en charger après coup. Voir skill `viewers`, section "Le cas de la rotation" (si présente) ou directement `rotation_tool_qt.py` pour le détail complet — ce skill-ci reste la référence pour `rotate_entry_data`/`flip_entry_data` elles-mêmes et les 3 points d'entrée orientés sélection multiple dans la mosaïque, pas pour ce 4e point d'entrée page-par-page.

**Cette coexistence est volontaire, pas une redondance à nettoyer** : contrairement aux 4 outils "macro" (crop/straighten/clone/texte) qui n'existent que dans la visionneuse, la rotation reste disponible aux deux endroits — elle garde un sens en sélection multiple depuis la mosaïque (icône colonne/menus), en plus du raccourci page par page depuis la visionneuse.

## Traductions

Clés dans `locales/fr.json` : `context_menu.image.rotate_right`/`rotate_left` (labels menu, avec flèches unicode `↻`/`↺` ailleurs en version courte), `tooltip.rotate_left`/`rotate_right` (`"Rotation à 90 degrés vers la gauche/droite"`), `labels.rotating`/`labels.flipping` (texte de l'overlay de progression, avec placeholder `{percent}`). Voir skill `add-translation` pour ajouter/modifier une clé dans les ~47 langues.

## Comment étendre

- **Ajouter une rotation 180°** : pas de nouvelle fonction métier nécessaire — `rotate_entry_data(entry, 180, state)` fonctionne déjà tel quel (PIL accepte n'importe quel angle). Il suffirait d'ajouter un point d'entrée UI (`rotate_selected_qt(180, callbacks)`) et ses clés de traduction/icône.
- **Ajouter un bouton miroir à la colonne d'icônes** : ajouter une entrée dans `ICON_DEFINITIONS` et `_ACTIVATION_RULES` (voir skill `icon-toolbar`) pointant vers `flip_selected_horizontal`/`flip_selected_vertical`, déjà présents dans le dict de callbacks — pas de nouvelle logique métier à écrire.
- **Changer le comportement d'annulation en cours de lot** : dans `_run_transform`/`_cancel()`, uniquement — ne pas toucher à `rotate_entry_data`/`flip_entry_data` qui n'ont aucune connaissance du worker ou de l'annulation.
- **Ajouter une nouvelle transformation géométrique proche** (ex. rotation 180° dédiée, symétrie diagonale) : suivre le squelette exact de `rotate_entry_data`/`flip_entry_data` (ouvrir → transformer → fermer l'ancien → réassigner → réencoder → invalider `large_thumb_pil`/`_hash` → sync ComicInfo → `free_image_memory`), puis brancher un nouveau point d'entrée `_qt` qui réutilise `_run_transform` tel quel plutôt que de dupliquer la logique de worker/overlay/annulation.

## Pièges connus

- **`expand=True` est obligatoire** dans `img.rotate()` — son omission tronquerait toute image non carrée après une rotation 90°.
- **Invalidation des caches Qt répartie entre deux fichiers** : `rotate_entry_data`/`flip_entry_data` (`image_ops.py`) n'invalident que `large_thumb_pil`/`_hash` ; `qt_pixmap_large`/`qt_qimage_large` ne le sont que plus tard, par le worker (`_regenerate_thumbnail_qt`, `image_transforms_qt.py`). Une fonction appelant `rotate_entry_data`/`flip_entry_data` **en dehors** de `_run_transform` (script, test, futur appel direct) laisserait la mosaïque afficher l'ancienne vignette tant que `_regenerate_thumbnail_qt` n'est pas appelée manuellement en plus — voir skill `apply-image-operation`, variante (B), qui documente déjà ce piège.
- **Un seul `save_state()` avant le lot, pas par image** — annuler restaure toutes les images du lot d'un coup à leur état d'avant, pas image par image. Ce n'est pas un bug : c'est voulu, cohérent avec l'affichage d'une seule barre de progression pour tout le lot.
- **Pas de `force=True`** sur le `save_state()` initial, contrairement au pattern recommandé par le skill `apply-image-operation` pour les opérations anticipatives — si un bug de undo/redo est signalé sur rotation/miroir dans un scénario où l'état sélectionné est déjà identique au dernier snapshot (sélection suivie d'une rotation sans modification intermédiaire), vérifier ce point en premier.
- **`flip_entry_data` ne valide pas `direction`** — toute valeur autre que `'horizontal'` tombe dans la branche verticale sans erreur ; un appelant qui passerait une valeur mal orthographiée échouerait silencieusement à faire ce qu'il pensait faire.

## Références croisées

- `apply-image-operation` — pattern général d'invalidation de cache/undo-redo pour toute fonction qui touche `entry['bytes']` ; rotation/miroir en est une variante particulière (voir section "Orchestration" ci-dessus pour les écarts précis). Le skill `apply-image-operation` référence déjà ce fichier comme exemple de la variante (B) d'invalidation partielle.
- `undo-redo` — mécanique interne d'historique/snapshot/rollback utilisée par `save_state`/`rollback` ; explique pourquoi `entry["img"]`/`entry["bytes"]` ne doivent jamais être mutés en place.
- `page-straighten` — la rotation **libre** (angle arbitraire) du redressement (`straighten_tool_qt.py`), un mécanisme totalement séparé qui ne partage aucun code avec ce skill malgré le mot "rotation" en commun ; comparer les deux sections "Application"/"Orchestration" pour les différences de `resample`/invalidation de cache/undo.
- `viewers` — le redressement manuel est un outil de la barre d'outils flottante de la visionneuse principale (plus de fenêtre dédiée) ; voir aussi skill `page-straighten` pour le mécanisme de calcul d'angle lui-même. La visionneuse a aussi son propre outil "rotation" (4e point d'entrée UI, voir section dédiée plus haut) — coexiste avec les 3 points d'entrée orientés mosaïque documentés dans ce skill-ci, ne les remplace pas.
- `icon-toolbar` — boutons `rotate_left`/`rotate_right`/`flip_horizontal`/`flip_vertical` de la colonne d'icônes, leur activation contextuelle et leurs tooltips.
- `qt-context-menus` — sous-menu "Rotation" du clic droit.
- `qt-tooltips` — tooltips des boutons de rotation/miroir dans la colonne d'icônes et de l'outil de la visionneuse.
- `mosaic-thumbnails` — `refresh_thumbnail`/`qt_pixmap_large` invalidés par le worker après chaque transformation.
- `duplicate-detection` — `refresh_duplicate_overlay()` appelé en fin de traitement puisque `_hash` a été invalidé pour chaque entrée modifiée.
- `comicinfo-metadata-editor` — mise à jour des dimensions de page dans `ComicInfo.xml` après une rotation (échange largeur/hauteur).
- `archive-image-loading` — `ensure_image_loaded`/`free_image_memory`, lazy loading des images PIL.
- `page-resize` — architecture de worker par lot la plus proche (overlay de progression + bouton Annuler sur le canvas) ; comparer les mécanismes d'annulation (rollback global ici via `rollback_to_current_state_qt` vs restauration manuelle des bytes là-bas).
- `canvas-overlay-progress` — détail complet du mécanisme d'overlay lui-même (`item_holder`, style non paramétrable, bouton Annuler associé), utilisé ici comme dans une dizaine d'autres fichiers du projet.
