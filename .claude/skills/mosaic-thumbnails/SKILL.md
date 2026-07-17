---
name: mosaic-thumbnails
description: Localiser ou modifier la mosaïque (grille de vignettes) de MosaicView — rendu QGraphicsScene, 3 tailles de vignettes, cache pixmap. Utiliser dès qu'une tâche touche à render_mosaic, ThumbnailItem, THUMB_SIZES, ou à la réglette de taille des vignettes.
---

# Mosaïque et vignettes — MosaicView

La mosaïque est la grille principale (panneau central) affichant les pages d'une archive sous forme de vignettes. C'est une `QGraphicsScene`/`QGraphicsView`, pas un layout Qt classique — chaque vignette est un item positionné manuellement en coordonnées (x, y).

## Fichier central — `modules/qt/mosaic_canvas.py`

Tout le rendu de la grille vit dans ce seul fichier (~2000 lignes).

- **`class MosaicCanvas(QGraphicsView)`** (ligne ~858) : la vue. Un panneau (`PanelWidget`) en possède une (deux si split-view actif). Gère aussi sélection, drag & drop, navigation clavier, rubber band.
- **`class ThumbnailItem(QGraphicsItem)`** (ligne ~563) : une vignette d'image (page réelle de l'archive, liée à `state.images_data[real_idx]`). Dessine pixmap + cadres (sélection/focus/corruption) + nom éditable + badges (marque-page, doublon).
- **`class DirItem(QGraphicsItem)`** (ligne ~304) : une vignette de dossier virtuel ou de l'icône ".." (remonter), pour la navigation par sous-dossiers au sein d'une archive. Nom non éditable.
- **`render_mosaic()`** (ligne ~1035, méthode de `MosaicCanvas`) : reconstruit toute la scène depuis `state.images_data`. Appelle `_teardown_scene_items()` (destruction contrôlée item par item, **ne jamais remplacer par `scene.clear()` seul** — voir commentaire ligne ~999 sur la corruption shiboken), `get_visible_entries_qt(st)` (filtre par dossier courant + fabrique les entrées de dossiers virtuels à la volée), puis crée un `ThumbnailItem`/`DirItem` par entrée visible et les positionne en grille.
- **`get_visible_entries_qt(state)`** (ligne ~69) : équivalent Qt de l'ancien `canvas_rendering.get_visible_entries()` tkinter — ne retourne que les entrées du dossier courant (`state.current_directory`), regroupe les sous-dossiers en une seule entrée `is_dir`.

## Taille des vignettes — 3 paliers fixes

```python
# modules/qt/entries.py
THUMB_SIZES = {
    0: (100, 133),   # Petite (small)
    1: (150, 200),   # Moyenne (normal, défaut)
    2: (200, 267)    # Grande (large)
}
```

Pas de zoom continu : uniquement ces 3 valeurs (index 0/1/2). Stocké sur `state` :
- `state.current_thumb_size` : index courant (0, 1 ou 2).
- `state.thumb_w`, `state.thumb_h` : dimensions actives en pixels, lues dynamiquement par les accesseurs `_tw()`/`_th()` (ligne ~142-148 de `mosaic_canvas.py`) — **toujours passer par `_tw()`/`_th()` dans ce fichier, jamais lire `THUMB_SIZES` en dur**, sinon le rendu ne suit pas un changement de taille en cours de session.
- `state.padding_x` : `15` en mode Petite, `5` sinon (compense visuellement l'espacement plus dense).

Persisté en config sous forme de string (`'small'`/`'normal'`/`'large'`, pas l'index) via `ConfigManager.get_thumbnail_size()`/`set_thumbnail_size()` dans `modules/qt/config_manager.py` (ligne ~179 et ~337).

### Comment le changement de taille se propage — `panel_widget.py:2131-2179`

Point d'entrée unique : **`PanelWidget._apply_thumb_size(index, save=True)`** (ligne ~2142). Fait 4 choses dans l'ordre :
1. `st.current_thumb_size = index` + `st.thumb_w, st.thumb_h = THUMB_SIZES[index]` + ajuste `padding_x`.
2. Sauvegarde en config si `save=True` (`get_config_manager().set_thumbnail_size(...)`).
3. Appelle `self._canvas.render_mosaic()` — reconstruction complète de la scène (nouvelle taille de pixmap pour chaque vignette).
4. `self._canvas.viewport().repaint()`.

Cinq déclencheurs appellent `_apply_thumb_size` (directement ou via `_decrease_thumb_size`/`_increase_thumb_size`/`_on_thumb_size_wheel`) :
- **Réglette** (`ThumbSizeSlider` dans `icon_toolbar_qt.py`, ligne ~639) — widget de la colonne de gauche, 3 crans, touche Espace pour cycler 0→1→2→0. `valueChanged` → `PanelWidget._on_thumb_size_change`.
- **Molette + Ctrl** sur le canvas — `MosaicCanvas.eventFilter` (ligne ~1926-1936) intercepte `QEvent.Wheel` avec `Qt.ControlModifier`, appelle `self._thumb_size_wheel_callback(delta)`, câblé sur `PanelWidget._on_thumb_size_wheel` (`panel_widget.py:325`).
- Raccourcis clavier / menu (`menubar_callbacks_qt.py:150-151`) → `_decrease_thumb_size`/`_increase_thumb_size`.
- `_init_thumb_size()` (ligne ~2131) — au chargement du panneau, relit la config et applique sans sauvegarder (`save=False`) ni forcer de render si aucun fichier n'est encore chargé.

**Chaque panneau a son propre `current_thumb_size`** (pas de couplage entre panneau 1 et panneau 2 en split-view) mais les deux lisent/écrivent la même clé de config globale `thumbnail_size` — donc un changement dans un panneau devient la valeur par défaut du prochain panneau ouvert, sans changer la taille d'un panneau déjà ouvert avec une autre valeur.

### Ajouter un 4e palier ou changer les dimensions

- Modifier `THUMB_SIZES` dans `entries.py` (garder les clés entières 0..N contiguës — tout le code qui fait `min`/`max`/`+1`/`-1` sur l'index suppose une plage contiguë commençant à 0).
- `ConfigManager.set_thumbnail_size` valide contre une liste en dur `['small', 'normal', 'large']` (ligne ~348) — à étendre en parallèle avec un nouveau nom si on ajoute un palier.
- `_apply_thumb_size` a aussi une liste en dur `size_names = ['small', 'normal', 'large']` (`panel_widget.py:2149`) — même chose.
- `_decrease_thumb_size`/`_increase_thumb_size`/`_on_thumb_size_wheel` bornent avec `0`/`2` en dur (`panel_widget.py:2162,2169,2176`) — à ajuster si la borne haute change.
- `ThumbSizeSlider.setMaximum(2)` (`icon_toolbar_qt.py:671`) — idem.

## Disposition en grille — calcul de position

- `_cw()`/`_ch()` (ligne ~150-154) : largeur/hauteur totale d'une "case" de grille = vignette + padding + zone nom (`LABEL_H = 30`).
- `MosaicCanvas._cols()` (ligne ~1187) : nombre de colonnes = `viewport_width // _cw()`, recalculé à chaque `render_mosaic()` — la grille est donc **responsive**, se réorganise automatiquement au redimensionnement de la fenêtre/du panneau (voir le `resizeEvent` du canvas qui déclenche un nouveau `render_mosaic()`).
- Position de chaque item : `col = visual_idx % cols`, `row = visual_idx // cols`, `x = col * _cw() + PAD_X`, `y = row * _ch() + PAD_Y` (constantes `PAD_X = PAD_Y = 10`, ligne ~44-46, distinctes de `state.padding_x/padding_y` qui ne servent qu'à l'ajustement visuel par taille de vignette mentionné plus haut).
- Après un drag & drop interne (réordonnancement), `_reorder_items_after_drop()` (ligne ~1191) repositionne les `ThumbnailItem` existants **sans** passer par `render_mosaic()` complet (évite un freeze sur une grosse archive) — à utiliser comme modèle si une future opération doit juste réordonner sans changer le nombre/la taille des vignettes.

## Rafraîchissement manuel — touche F5

`render_mosaic()` est directement exposé à l'utilisateur comme commande de rafraîchissement manuel, sans logique intermédiaire propre — 3 points d'entrée, tous appelant `render_mosaic()` tel quel :
- **Raccourci global F5** (`MosaicView.py:210-212`) : `QShortcut(QKeySequence("F5"), self)` au niveau de `MainWindow`, contexte `Qt.ApplicationShortcut` (actif quelle que soit la fenêtre/le widget qui a le focus dans l'appli), connecté à `lambda: self._active_panel._canvas.render_mosaic()` — agit donc toujours sur le **panneau actif**, jamais sur le panneau inactif en split-view.
- **Menu contextuel** (clic droit mosaïque, skill `qt-context-menus`) — `context_menus_qt.py:242`, clé `menu.refresh_mosaic`, `QAction.setShortcut("F5")` pour afficher le raccourci dans le menu (purement visuel, la touche est déjà captée globalement par le `QShortcut` de `MainWindow` — les deux ne rentrent pas en conflit puisqu'ils appellent la même fonction). Désactivé si la mosaïque est vide (`_add_disabled`).
- **Barre de menu** — `menubar_qt.py:167`, même clé, suffixe `"\tF5"` ajouté manuellement au libellé (tabulation Qt pour aligner le raccourci à droite dans le menu) plutôt que `setShortcut()`.

Usage typique : forcer une reconstruction de la grille après une modification externe au fichier courant (édition manuelle sur disque, désynchronisation visuelle rare) — dans l'immense majorité des cas, les fonctions du projet appellent déjà `render_mosaic()` elles-mêmes après leur propre modification (voir skill `apply-image-operation`), F5 est donc surtout un filet de secours manuel plutôt qu'un mécanisme activement nécessaire au fonctionnement normal.

## Cache des pixmaps — deux niveaux

Chaque `entry` (dict de `state.images_data`) porte deux clés de cache non persistées :

1. **`entry["qt_qimage_large"]`** : `QImage` pré-calculée à la résolution `THUMB_SIZES[2]` (la plus grande, 200×267), construite dans un **thread background** au chargement de l'archive (`build_qimage_for_entry`, ligne ~217, appelée par tous les workers de chargement — voir skill `archive-image-loading` pour `create_entry()` et le worker qui l'invoque) — `QImage` est thread-safe contrairement à `QPixmap`, d'où ce choix pour ne pas toucher Qt hors du thread UI pendant le chargement.
2. **`entry["qt_pixmap_large"]`** : `QPixmap` convertie depuis `qt_qimage_large` **à la première demande côté thread UI** (`_get_pixmap_for_size`, ligne ~247) — dès que la conversion a eu lieu, `qt_qimage_large` est libérée (`entry["qt_qimage_large"] = None`) pour ne pas garder les deux représentations en mémoire.

`_get_pixmap_for_size(entry, tw, th)` (ligne ~247) est le point d'entrée unique pour obtenir une vignette à une taille donnée :
- Si `qt_pixmap_large` existe déjà à exactement `(tw, th)`, la retourne telle quelle.
- Sinon, la re-scale depuis `qt_pixmap_large` (opération Qt rapide en mémoire, pas de PIL) — **c'est pourquoi changer de palier de taille ne redécode jamais l'image depuis les bytes**, juste un rescale de pixmap déjà en RAM.
- Fallback complet si aucun cache n'existe (entrée ajoutée par drop après le chargement initial) : décode `entry["bytes"]` via PIL, `create_centered_thumbnail()` (centre l'image dans le cadre `tw×th` en conservant le ratio, fond transparent ou damier si alpha), conversion PIL→QImage→QPixmap.
- Dernier filet : rectangle gris uni si tout échoue.

### Invalidation du cache — `invalidate_pixmap_cache()` (ligne ~292)

Vide `qt_pixmap_large`/`qt_qimage_large` sur toutes les entrées de `state.images_data`. **Obligatoire après toute modification de `entry["bytes"]`** (crop, resize, rotation, flip, ajustements...), sinon la vignette continue d'afficher l'ancienne image — voir skill `apply-image-operation` pour le pattern complet (undo/redo + invalidation de **tous** les caches liés à `bytes`, dont celui-ci et celui de `duplicate-detection`).

## `create_centered_thumbnail()` — `modules/qt/entries.py:128`

Fonction PIL pure (pas de dépendance Qt) qui produit l'image de la vignette :
- Redimensionne en conservant le ratio (`img.thumbnail((thumb_w, thumb_h), LANCZOS)`), donc l'image ne remplit pas forcément tout le cadre `thumb_w × thumb_h`.
- Centre le résultat sur un fond `RGBA` transparent (`(0,0,0,0)`) par défaut, ou un damier gris (`_make_checkerboard_pil`) si `checkerboard=True` — utilisé quand l'image source a un canal alpha, pour rendre la transparence visible dans la vignette.
- Retourne toujours une image de taille exacte `thumb_w × thumb_h` (fond inclus), jamais la taille réelle de l'image source.

## Comment étendre / pièges connus

- **Ne jamais lire `state.thumb_w`/`thumb_h` directement dans `mosaic_canvas.py`** — utiliser `_tw()`/`_th()`, qui gèrent le cas `state is None`/attribut absent (fallback 150×200).
- **Un changement de taille ne redécode pas les images** : c'est un rescale de `qt_pixmap_large` (résolution fixe 200×267, la plus grande des 3). Si un jour une taille au-delà de `THUMB_SIZES[2]` est ajoutée, il faudra revoir `build_qimage_for_entry` qui code en dur `THUMB_SIZES[2]` comme résolution de référence (ligne ~222) — sinon l'agrandissement au-delà de 200×267 upscale un pixmap déjà réduit (perte de netteté).
- **Toute nouvelle fonction qui modifie `entry["bytes"]`** doit appeler `invalidate_pixmap_cache()` (ou au minimum vider `qt_pixmap_large`/`qt_qimage_large` sur l'entrée concernée) **et** invalider `entry["_hash"]` (skill `duplicate-detection`) — les deux caches sont indépendants et doivent être invalidés séparément.
- **`_teardown_scene_items()` est obligatoire avant toute reconstruction de la scène** — ne jamais appeler `self._scene.clear()` seul sur une scène peuplée (voir commentaire détaillé ligne ~998-1010 sur la corruption de la table shiboken).
- **Grille responsive** : ne pas supposer un nombre de colonnes fixe — toujours recalculer via `_cols()` si du code positionne des items manuellement en dehors de `render_mosaic()`/`_reorder_items_after_drop()`.
- Les badges superposés sur une vignette (marque-page coin haut-droit, doublon coin haut-gauche) sont dessinés dans `ThumbnailItem.paint()` (ligne ~714) — toute nouvelle pastille doit choisir un coin libre ou composer avec l'existant, pas les superposer. Le cadre rouge d'une entrée corrompue (`entry["is_corrupted"]`) est dessiné juste avant ces badges, à la même méthode — voir skill `corrupted-images` pour la détection et le remplacement.
