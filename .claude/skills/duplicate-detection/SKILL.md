---
name: duplicate-detection
description: Localiser ou modifier la détection des pages en double d'une même archive (hash MD5, badge vignette, indicateur statusbar, pastille minimap). Utiliser dès qu'une tâche touche à entry["_hash"], recompute_duplicate_groups, ou à la fenêtre "Doublons".
---

# Détection des doublons de page — MosaicView

Détecte les pages **strictement identiques** (hash MD5 exact sur `entry["bytes"]`) au sein d'une même archive ouverte. Ne détecte pas les quasi-doublons (recadrage, recompression, résolution différente) — uniquement une correspondance binaire exacte.

## Cœur du mécanisme — `modules/qt/duplicate_detection_qt.py`

Toute la logique de calcul et la fenêtre de gestion vivent dans ce seul fichier.

- **`recompute_duplicate_groups(state)`** (ligne ~18) : parcourt `state.images_data`, hash paresseusement (seulement si `entry["_hash"] is None`) chaque entrée image non corrompue, puis regroupe par hash identique. Assigne sur chaque entrée :
  - `entry["_is_duplicate"]` (bool) — `True` si au moins 2 entrées partagent le même hash.
  - `entry["_duplicate_group"]` — le hash lui-même sert d'identifiant de groupe (stable entre deux recalculs tant que le contenu du groupe ne change pas), ou `None` si pas de doublon.
  - Les entrées non-image ou corrompues (`is_corrupted`) sont explicitement exclues (`_hash = None`, `_is_duplicate = False`) — voir skill `corrupted-images` pour la détection/l'affichage/le remplacement de ces entrées.
- **`get_duplicate_groups(state)`** (ligne ~58) : appelle `recompute_duplicate_groups` puis retourne une liste de groupes `[(real_idx, entry), ...]`, chaque groupe trié par `real_idx` croissant, groupes eux-mêmes triés par `real_idx` de leur première page. Utilisé pour peupler la fenêtre de gestion.
- **`has_any_duplicate(state)`** (ligne ~74) : recalcule puis retourne `True`/`False` — utilisé partout où on doit juste savoir s'il faut activer/afficher quelque chose (menus, indicateur statusbar), sans construire la liste complète.
- **`delete_entries_by_index(state, indices, save_state, render_mosaic, refresh_tabs=None)`** (ligne ~80) : suppression générique par liste d'indices explicite (indépendante de `state.selected_indices`), suit le pattern standard `save_state` avant/après + `sync_pages_in_xml_data` + `render_mosaic`. Utilisée par la fenêtre de doublons pour supprimer la sélection.

### Cache `_hash` — invalidation obligatoire à toute modification de `entry["bytes"]`

`_hash` est un **cache paresseux** : `recompute_duplicate_groups` ne le recalcule que si `entry["_hash"] is None`. Toute fonction qui modifie `entry["bytes"]` (crop, resize, rotation, flip, ajustements, remplacement d'image...) **doit** mettre `entry["_hash"] = None` pour forcer le rehash au prochain passage — sinon la page garde l'ancien statut de doublon (faux positif ou faux négatif) après modification.

C'est déjà fait dans tous les points d'écriture existants (`image_ops.py`, `image_transforms_qt.py`, `adjustments_processing_qt.py`, `adjustments_dialog_qt.py`, `adjustments_viewers_qt.py`, `clone_zone_viewer_qt.py`, `straighten_viewer_qt.py`, `text_viewer_qt.py`, `resize_dialog_qt.py`, `image_viewer_qt.py`, `file_operations_qt.py`, `undo_redo_qt.py`, `panel_widget.py`) — voir skill `apply-image-operation` pour le pattern général de modification d'image, qui inclut cette invalidation. **Si tu écris une nouvelle fonction qui touche à `entry["bytes"]`, ajoute `entry["_hash"] = None` dedans**, sinon la détection de doublons devient silencieusement fausse pour cette page.

`entries.py` (ligne ~262) initialise `_hash`/`_is_duplicate`/`_duplicate_group` à la création d'une entrée (chargement initial d'une archive, import de fichier).

## Affichage — trois surfaces indépendantes

### 1. Badge sur la vignette — `mosaic_canvas.py`

- `ThumbnailItem.paint()` (ligne ~752) : si `entry.get("_is_duplicate")`, dessine `_get_duplicate_pixmap()` (`icons/Duplicates 8.png`, marge 8%) en coin **supérieur gauche** de la vignette (opposé au ruban marque-page, coin supérieur droit), `setOpacity(0.85)`, taille `max(32, tw // 2)`.
- Tooltip de la vignette (`_format_tooltip`, ligne ~784) : préfixe `<b>{_('tooltip.duplicate_image')}</b>` si `_is_duplicate`.
- **`refresh_duplicate_overlay()`** (ligne ~974, méthode du canvas) : recalcule les groupes puis force un `item.update()` (repaint) sur tous les `ThumbnailItem` — ne reconstruit pas toute la scène. C'est la méthode à appeler après une opération qui peut changer le statut de doublon sans passer par un `render_mosaic()` complet. Appelée depuis `image_transforms_qt.py`, `image_viewer_qt.py`, `resize_dialog_qt.py`.
- `render_mosaic()` (ligne ~1035) appelle aussi `recompute_duplicate_groups(st)` (ligne ~1047) systématiquement à chaque reconstruction complète de la scène.
- Deux pixmaps globaux mis en cache (`_get_duplicate_pixmap` marge 8% pour la mosaïque, `_get_duplicate_pixmap_wide_margin` marge 20% pour minimap/statusbar) — icônes séparées car la même image lue à des tailles très différentes ne reste pas lisible avec la même marge.

### 2. Indicateur de la statusbar — `status_bar_qt.py`

Pour le mécanisme générique de la barre (layout, `refresh()`, `OverlayTooltip`) → skill `status-bar`. Spécifique aux doublons :

- Icône grisée si aucun doublon (`_get_duplicate_indicator_pixmap(grayed=not active)`, dérivé de `_get_duplicate_pixmap_wide_margin`), curseur `PointingHandCursor` uniquement si actif (`_set_duplicate_indicator_state`, ligne ~242).
- **Clic gauche sur l'indicateur** : ouvre la fenêtre de gestion. Callback branché via `set_duplicate_click_callback()` (ligne ~147), câblé dans `panel_widget.py:510` vers `self._show_duplicates_window`. N'a d'effet que si `active` (doublons présents).
- Tooltip dynamique (`tooltip.duplicate_indicator` / `tooltip.duplicate_indicator_none`), vide si aucun fichier ouvert.

### 3. Pastille sur la minimap — `minimap_widget_qt.py`

Ligne ~244-245 : pour chaque entrée avec `_is_duplicate`, dessine `_get_duplicate_pixmap_wide_margin()` (même pixmap que l'indicateur statusbar, cohérence visuelle à petite taille).

## Fenêtre de gestion — `_DuplicatesWindow` dans `duplicate_detection_qt.py`

- Ouverte via `show_duplicates_window(parent, state, save_state, render_mosaic, refresh_tabs=None)` (ligne ~114), non-modale (règle CLAUDE.md), centrée sur le panneau source.
- Accessible depuis trois endroits (actifs seulement si `has_any_duplicate`) : menu Fichier (`menubar_qt.py:170`), menu contextuel canvas (`context_menus_qt.py:250`), menu contextuel vignette (`context_menus_qt.py:468`) — plus le clic sur l'indicateur statusbar.
- Structure : un groupe = un `QFrame` avec titre (`dialogs.duplicates.group_title`, numéro + nombre de membres), boutons "tout sélectionner"/"tout désélectionner" du groupe, puis une ligne par page (vignette 45×60 + checkbox avec nom, chemin affiché seulement si l'archive a une structure de sous-dossiers via `_has_subdirectory_structure`).
- **Cases cochées mémorisées par `id(entry)` (identité d'objet Python), jamais par `real_idx`** (`_rebuild_list`, ligne ~194) : après une suppression, les `real_idx` se décalent — mémoriser par position ferait cocher à tort une entrée qui a hérité de la position d'une autre, avec risque de suppression non voulue.
- Boutons de groupe (`_on_group_select_clicked`/`_on_group_deselect_clicked`, ligne ~280) retrouvent leur `QFrame` via `self.sender().parent()`, jamais via une lambda capturant le frame — une lambda retenue par la connexion C++ d'un bouton enfant créerait un cycle Python qui retarderait la destruction du frame détaché jusqu'au GC (voir aussi le commentaire équivalent dans `qt-context-menus` pour le même piège).
- Suppression (`_on_delete_clicked`, ligne ~374) : passe par `DeleteConfirmDialog` (non-modal, `file_close_qt.py`) avant d'appeler `delete_entries_by_index`. Après suppression, si des doublons subsistent, `_rebuild_list()` (rafraîchit sur place) ; sinon `self.close()`.
- `refresh()` (ligne ~408) reconstruit la liste depuis l'état courant — pas de branchement automatique sur un signal `status_changed` dans ce fichier ; à appeler explicitement si un appelant externe modifie `images_data` pendant que la fenêtre est ouverte.

## Comment étendre

- **Changer le critère de doublon** (ex. tolérer une différence de nom mais pas de contenu — déjà le cas ; ou aller vers un hash perceptif/quasi-doublon) : modifier uniquement `recompute_duplicate_groups`. Le hash MD5 exact sur les bytes est le seul point de comparaison ; toute la UI en aval (badge, statusbar, minimap, fenêtre) consomme uniquement `_is_duplicate`/`_duplicate_group` et n'a pas à changer.
- **Ajouter une nouvelle surface d'affichage** (ex. un badge dans un autre widget) : lire `entry.get("_is_duplicate")` après un appel à `recompute_duplicate_groups(state)` (ou `has_any_duplicate`/`get_duplicate_groups` qui l'appellent déjà) — ne jamais lire `_is_duplicate` sans être sûr qu'un recalcul a eu lieu depuis la dernière modification de `bytes`.
- **Icônes** : `icons/Duplicates 8.png` (marge réduite, mosaïque) et `icons/Duplicates 20.png` (marge large, minimap/statusbar) — respecter cette convention de nommage si une variante supplémentaire est ajoutée.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour toute modification de `_DuplicatesWindow` (non-modale déjà en place, ne pas régresser).

## Pièges connus

- **Oublier d'invalider `_hash`** après une nouvelle fonction de modification d'image → doublons obsolètes affichés indéfiniment (voir section cache ci-dessus).
- **Confondre `real_idx` et position dans le groupe** : les indices retournés par `get_duplicate_groups` sont des `real_idx` (index brut dans `images_data`), pas des positions parmi les images — cohérent avec le reste de la codebase (`selected_indices` etc.) mais à vérifier si on croise avec du code utilisant une numérotation différente (ex. `page_idx` des bookmarks, qui lui est une position parmi les images).
- **`refresh_duplicate_overlay()` vs `render_mosaic()`** : le premier ne fait qu'un repaint ciblé (pas de reconstruction de scène) — à préférer après une opération qui ne change ni le nombre de pages ni leur ordre. Un `render_mosaic()` complet recalcule de toute façon les doublons donc pas besoin d'appeler les deux.
