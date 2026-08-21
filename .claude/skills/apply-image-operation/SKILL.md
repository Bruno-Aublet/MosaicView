---
name: apply-image-operation
description: Écrire ou modifier une fonction qui change les bytes d'une image de la mosaïque (crop, resize, rotation, conversion, ajustements...). Utiliser dès qu'une tâche touche à entry['bytes'] dans images_data, pour ne pas casser l'undo/redo ni les caches de vignettes.
---

# Appliquer une opération image (mosaïque + undo/redo) — MosaicView

Quand une fonction modifie `entry['bytes']` dans `images_data`, il faut impérativement suivre ce pattern en 5 étapes. Voir skill `undo-redo` pour le fonctionnement interne du mécanisme d'historique lui-même (snapshots, `_original_id`, pourquoi `entry["bytes"]` ne doit jamais être muté en place) — ce skill-ci se concentre sur le contrat à respecter côté appelant.

## 1. Sauvegarder l'état AVANT modification (undo)
```python
if save_state:
    save_state(force=True)
```
`force=True` est obligatoire — sans ça, si l'état courant est déjà dans l'historique (mêmes bytes), `save_state_data` retourne `False` et ne sauvegarde rien.

## 2. Modifier les bytes ET invalider tous les caches vignettes

**Il n'existe pas de fonction utilitaire centralisée** — chaque module invalide les caches inline. Deux variantes existent dans le code actuel, préférer la variante complète (A) pour toute nouvelle fonction :

**A. Invalidation complète** (`image_processing_qt.py`, `crop_tool_qt.py`, `straighten_tool_qt.py`, `clone_tool_qt.py`, `text_tool_qt.py`) :
```python
entry['bytes'] = <nouveaux bytes>
entry['img'] = None
entry['_thumbnail'] = None
entry['large_thumb_pil'] = None   # cache PIL
entry['qt_pixmap_large'] = None   # cache Qt — CRITIQUE pour render_mosaic
entry['qt_qimage_large'] = None   # cache Qt intermédiaire
entry['_hash'] = None             # cache détection de doublons
```

**B. Invalidation partielle** (`image_transforms_qt.py::_regenerate_thumbnail_qt(entry)`, utilisée après rotation/flip) : n'invalide que `qt_pixmap_large`, `qt_qimage_large`, `_hash` — `entry['img']` y est réassigné directement à la nouvelle image (pas mis à `None`) par l'appelant (`rotate_entry_data`/`flip_entry_data` dans `image_ops.py`), et `_thumbnail` n'y est pas touché. Ne pas copier cette variante pour une nouvelle fonction sans vérifier qu'elle correspond bien à ton cas — préférer (A), plus sûre.

Sans invalider `qt_pixmap_large`, `render_mosaic` réaffiche l'ancienne vignette.

Si une fonctionnalité ajoute un autre cache dérivé de `entry['bytes']`, l'invalider ici aussi dans la même liste.

## 3. Marquer le fichier comme modifié
```python
state.modified = True
```
Sans ça, fermer le fichier ne déclenche pas le message d'avertissement.

## 4. Sauvegarder l'état APRÈS modification (redo)
```python
if save_state:
    save_state(force=True)
```
Sans ce deuxième appel, le redo ne peut pas restaurer l'état modifié.

## 5. Appeler render_mosaic
```python
if render:
    render()
```

## Callbacks attendus
- `save_state` : `MainWindow.save_state(force=False)` — supporte `force=True`.
- `render_mosaic` : `MosaicCanvas.render_mosaic()`.

## Pourquoi ce pattern exact

Ce pattern exact évite plusieurs bugs récurrents : mosaïque ne se mettant pas à jour (`qt_pixmap_large` non invalidé), undo/redo impossible (`save_state` sans `force=True` ignoré), message de fermeture absent (`state.modified` non mis à `True`).

**Toute nouvelle fonction qui modifie des bytes d'images (crop, resize, flip, conversion, etc.) doit suivre ce pattern exact — ne pas improviser une variante.**

## Références croisées

- `viewers` — appelant principal de la variante (A) via `apply_image_adjustments()` (`image_processing_qt.py`), utilisée par chaque outil d'ajustement de la barre d'outils de la visionneuse (sharpness, brightness, saturation, remove_colors, compression, levels, color_depth, effects, image_mode) sur la page affichée, un commit par geste.
- `adjust-transparency` — seul outil de la barre à appliquer ses propres bytes en dehors de `apply_image_adjustments()` (chemin dédié `perform_transparency()`), mais qui suit le même pattern d'invalidation de caches.
- `rotate-flip` — exemple de la variante (B) d'invalidation partielle : `rotate_entry_data`/`flip_entry_data` (`image_ops.py`) n'invalident que `large_thumb_pil`/`_hash`, `qt_pixmap_large`/`qt_qimage_large` étant invalidés séparément par le worker qui orchestre le traitement par lot ; pas de `force=True` explicite sur son `save_state()` initial non plus, contrairement au pattern recommandé ici.
- `page-straighten` — exemple de la variante (A) complète (contrairement à `rotate-flip`), avec en plus un second historique undo/redo interne à la fenêtre qui s'empile par-dessus le `save_state`/`force=True` standard documenté ici.
- `add-text-to-image` — même variante (A) complète, avec un troisième niveau d'historique (undo de frappe Qt natif par bloc) en plus des deux niveaux de `page-straighten`.
- `page-resize` — variante intermédiaire propre à ce fichier : invalidation de cache proche de (B) mais avec un précalcul explicite de la vignette Qt dans le thread worker (`build_qimage_for_entry`), et une annulation en cours de lot qui restaure les bytes manuellement plutôt que via `rollback_to_current_state_qt`.
- `page-crop` — variante (A) complète, avec la même optimisation `build_qimage_for_entry` que `page-resize` mais exécutée en synchrone (pas de worker QThread) ; les deux appels à `save_state` omettent `force=True`, contrairement au pattern recommandé ici.
- `clone-zone` — le clonage (3e outil migré dans la visionneuse principale) suit un pattern distinct : pas de bouton "Valider", chaque coup de pinceau (stroke) relâché est directement `save_state()`/`save_state(force=True)` et devient sa propre entrée d'historique — pas d'unité d'undo "par point peint" ni d'historique interne séparé comme l'avait l'ancienne fenêtre dédiée (supprimée).
- `create-ico` — ne suit pas ce pattern du tout : aucun `save_state` avant modification, entrée construite manuellement plutôt que via `create_entry()`, car il s'agit d'un **ajout** de nouvelle entrée (le `.ico` généré) plutôt que d'une modification de `entry['bytes']` existant.
- `animated-gif` — même famille de cas hors pattern : ajout d'une nouvelle entrée (le GIF assemblé), un seul `save_state` après, mais réutilise `create_entry()` contrairement à `create-ico`.
- `nfo-editor` — hors périmètre direct (pas d'image, pas de cache vignette) mais utile en contraste : la mutation en place de `entry['bytes']` y est correcte pour du texte, contrairement à la règle stricte documentée ici pour les images.
