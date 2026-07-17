---
name: minimap
description: Localiser ou modifier la minimap de MosaicView (panneau latéral droit, aperçu réduit de la grille avec rectangle de zone visible navigable, style VS Code). Utiliser dès qu'une tâche touche à minimap_widget_qt.py, MinimapWidget, ou au chevron minimap de la barre de menu.
---

# Minimap — MosaicView

Panneau optionnel accolé à droite de la mosaïque, affichant un aperçu miniature de **toute** la grille de vignettes du panneau, avec un rectangle vert représentant la zone actuellement visible dans la mosaïque. Comportement inspiré de la minimap de VS Code : clic pour sauter à un endroit, drag du rectangle pour naviguer en continu.

## Fichier central — `modules/qt/minimap_widget_qt.py`

Un seul fichier, une seule classe : **`class MinimapWidget(QWidget)`** (~320 lignes). Widget dessiné entièrement à la main via `QPainter` dans `paintEvent()` — ce n'est pas une vue Qt (pas de `QGraphicsView`/`QGraphicsScene`), juste un rendu manuel qui relit les items déjà construits par la mosaïque.

**Lecture seule sur le contenu** : la minimap ne modifie jamais `state.images_data` ni les items de la mosaïque. La seule action qu'elle déclenche en retour sur le canvas est un changement de position de scroll (`canvas.centerOn(...)`).

## Intégration dans le panneau — `modules/qt/panel_widget.py`

- **`PanelWidget._build_minimap_panel()`** (ligne ~2274) : construit le panneau conteneur (`QWidget` nommé `"minimapPanel"`, largeur fixe `_MINIMAP_WIDTH = 120`) et y place le `MinimapWidget(self._canvas, owner_panel=self)`.
- Positionné dans `content_row_layout` (ligne ~483-494), **à droite** de `self._content_stack` (qui contient le canvas), via un simple `QHBoxLayout` — **pas de `QSplitter`** : la largeur de 120px n'est jamais redimensionnable par l'utilisateur, contrairement à la colonne de gauche (icon-toolbar) qui utilise un splitter.
- **`PanelWidget._toggle_minimap()`** (ligne ~2286) : bascule `self._minimap_visible`, montre/cache `self._minimap_panel`, met à jour le chevron du menu, redonne le focus au canvas, et persiste l'état en config (`set_minimap_visible` pour le panneau 1, `set_minimap_visible_panel2` pour le panneau 2 en split-view — deux clés indépendantes, pas de synchro entre panneaux).
- Chaque panneau (panel1/panel2 en mode split) a **sa propre instance** de `MinimapWidget`, liée à son propre canvas — aucun état partagé entre les deux.

## Déclenchement — chevron de la barre de menu

- `modules/qt/menubar_qt.py` (ligne ~640-652) : une `QAction` chevron (`"«"`/`"»"`, sens inversé) tout à l'extrême droite de la barre de menu, symétrique du chevron de la colonne d'icônes à gauche. Callback câblé via `menubar_callbacks_qt.py:148-149` (`get_minimap_visible` / `toggle_minimap` → `mw._toggle_minimap`).
- Pas de raccourci clavier dédié ni d'entrée dans un menu contextuel — uniquement ce chevron.

## Persistance — `modules/qt/config_manager.py`

Deux clés indépendantes, cachées par défaut :
- `minimap_visible` (panneau 1) : `get_minimap_visible()` / `set_minimap_visible(visible)` (ligne ~257-262).
- `minimap_visible_panel2` (panneau 2) : `get_minimap_visible_panel2()` / `set_minimap_visible_panel2(visible)` (ligne ~264-268).

Restauration au démarrage : `session_restore_qt.py:39-40` (voir skill `session-restore`) appelle `win._panel._toggle_minimap()` **avant** `win.show()` si `cfg.get_minimap_visible()` est vrai (évite un flash de layout). `reset_to_defaults()` (ligne ~139-143) referme la minimap sur tous les panneaux avant de recalculer la taille par défaut de la fenêtre — sinon la largeur minimale imposée par le panneau minimap encore visible fausse le calcul du resize.

## Géométrie — taille FIXE, pas de compression

Point le plus important à comprendre avant de toucher ce fichier : **la taille d'une mini-vignette est fixe**, dérivée uniquement de la largeur du panneau (120px) divisée par le nombre de colonnes réel de la mosaïque (`_real_cols()`, miroir de `MosaicCanvas._cols()`). Elle n'est **jamais compressée** pour faire tenir tout le contenu verticalement — sur un omnibus de 1500+ pages, ça rendrait les mini-vignettes illisibles.

Conséquence : la minimap gère son **propre défilement vertical interne** (`self._scroll_y`, coordonnées minimap), sans `QScrollBar` visible :
- `_center_scroll_on_viewport()` (ligne ~118) recentre automatiquement ce défilement sur le rectangle de zone visible à chaque changement (scroll du canvas, resize, changement de contenu) — la minimap suit donc toujours la position de lecture, jamais besoin de scroller la minimap elle-même pour retrouver le rectangle.
- `_clamp_scroll()` (ligne ~114) borne ce défilement à `[0, content_height - panel_height]`.

### Chaîne de conversion de coordonnées

Trois systèmes de coordonnées coexistent — toujours passer par ces méthodes, ne jamais improviser un calcul de position :
- **Scène réelle** (coordonnées du `MosaicCanvas`/`QGraphicsScene`, en pixels de vignette réelle).
- **Minimap sans scroll** : scène réelle × `_scale()` (ligne ~98, ratio `mini_w / real_cw`, identique en X et Y).
- **Minimap widget** (ce qui est réellement peint/cliqué) : minimap sans scroll − `_scroll_y`, + le padding fixe `_MINI_PAD = 4`.

Conversions dans les deux sens :
- `_scene_to_minimap_rect(scene_rect)` (ligne ~128) : scène → widget minimap (sert à placer les mini-vignettes et le rectangle de viewport).
- `_minimap_to_scene_point(pos)` (ligne ~137) : widget minimap → scène (sert à interpréter un clic/drag utilisateur).

## Ce qui est dessiné — `paintEvent()` (ligne ~162)

Pour chaque item de `self._canvas._items` (liste déjà construite par `render_mosaic()` de la mosaïque — la minimap ne la reconstruit pas, elle la relit) :
1. **Le pixmap réduit** de la vignette, via `_mini_pixmap_for_item()` → `_get_pixmap_for_size(entry, mini_w, mini_h)` (même fonction de cache que la mosaïque principale, voir skill `mosaic-thumbnails` — pas de cache séparé pour la minimap, juste un rescale à la volée de `qt_pixmap_large`).
2. **Les mêmes overlays qu'une `ThumbnailItem`**, redessinés à l'échelle réduite par `_paint_overlays()` (ligne ~223) :
   - Cadre de sélection bleu (`SEL_OUTLINE`) si `item._selected`.
   - Cadre rouge (image corrompue) si `entry["is_corrupted"]` — voir skill `corrupted-images`.
   - Badge marque-page (coin haut-droit) si `entry["_is_bookmarked"]` — voir skill `bookmarks`.
   - Badge doublon (coin haut-gauche) si `entry["_is_duplicate"]` — voir skill `duplicate-detection`.
3. **Le rectangle de zone visible** (`_viewport_rect_minimap()`, ligne ~146) : contour vert fixe `_VIEWPORT_COLOR = QColor(0, 170, 0)` (**identique en thème clair et sombre, décision utilisateur assumée** — ne pas le faire dépendre de `get_current_theme()`), remplissage vert semi-transparent (alpha 40).

Les `DirItem` (dossiers virtuels) sont dessinés comme les `ThumbnailItem` via `_mini_pixmap_for_item()` (même chemin, `isinstance(item, (ThumbnailItem, DirItem))`), mais **sans** les overlays marque-page/doublon (qui n'ont de sens que pour une page réelle).

Items hors de la zone verticale visible du panneau minimap (`y + mini_ch < 0 or y > self.height()`) : skippés, pas de coût de rendu inutile même sur une très longue liste.

## Interactions souris

- **`mousePressEvent`** (ligne ~269) : si le clic tombe **dans** le rectangle de viewport → démarre un drag (`_dragging_viewport = True`, curseur main fermée). Sinon → saut direct, la mosaïque se recentre instantanément sur le point cliqué (`centerOn`).
- **`mouseMoveEvent`** (ligne ~285) : pendant un drag, recentre le canvas en continu sur la position de la souris (moins l'offset de saisie initial, pour ne pas faire sauter le rectangle sous le curseur). Hors drag, change juste le curseur (main ouverte si survol du rectangle, flèche sinon).
- **`mouseReleaseEvent`** : termine le drag.
- **`wheelEvent`** (ligne ~318) : la molette sur la minimap fait défiler **le canvas de la mosaïque** (pas la minimap elle-même — son propre défilement interne suit automatiquement via `_on_canvas_changed`, câblé sur les scrollbars du canvas).
- **Focus en mode split** : `_notify_panel_focus()` (ligne ~257) — cliquer/draguer sur la minimap d'un panneau non-actif l'active, même comportement qu'un clic direct sur son canvas (via `mw._set_active_panel(panel)`).
- Pas de drag & drop de pages depuis/vers la minimap — volontairement absent (voir docstring en tête de fichier).

## Synchronisation avec le canvas

La minimap se repeint automatiquement sur ces événements (pas de polling) :
- `canvas.status_changed` (signal générique de la mosaïque — rechargement, réordonnancement, changement de sélection...).
- `canvas.horizontalScrollBar().valueChanged` / `verticalScrollBar().valueChanged`.
- `QEvent.Resize` du canvas, intercepté via `installEventFilter(self)` (ligne ~54-61) — **nécessaire car `render_mosaic()` n'émet pas `status_changed` sur un simple resize** (juste un `_relayout()` interne), donc sans cet event filter la minimap resterait périmée après un redimensionnement de fenêtre sans changement de contenu.
- `resizeEvent()` du widget minimap lui-même (changement de largeur du panneau, cas rare vu la largeur fixe, mais couvre un futur changement de `_MINIMAP_WIDTH`).

## Piège déjà rencontré — lire `self._canvas._state`, jamais le singleton global

`_real_tw()`/`_real_th()`/`_real_cols()` lisent explicitement **`self._canvas._state`**, jamais `modules.qt.state.state` (le singleton global). Raison documentée en commentaire (ligne ~64-67) : pendant certaines opérations qui basculent temporairement le singleton global vers un autre state (ex. `_apply_thumb_size` en mode split), un `update()` différé de la minimap (via `QTimer.singleShot` ou un signal en file d'attente) pourrait repeindre avec un state déjà restauré/périmé si elle lisait le singleton au lieu de l'état propre à son canvas. Toute nouvelle méthode de géométrie ajoutée à `MinimapWidget` doit suivre le même principe.

## Comment étendre / pièges connus

- **Ne jamais ajouter de `QScrollBar` visible à la minimap** — le défilement interne centré automatiquement (`_scroll_y`) est un choix delibéré, pas un oubli.
- **Ne jamais compresser les mini-vignettes pour tout faire tenir** — la taille fixe dérivée de `_real_cols()` est le point central de la conception (voir docstring en tête de fichier).
- **Toute nouvelle donnée à afficher en overlay** (nouveau badge, nouvel indicateur) doit être ajoutée dans `_paint_overlays()`, à l'échelle `mini_w`/`mini_h` — ne pas dupliquer la logique de `ThumbnailItem.paint()` telle quelle, la reproduire à l'échelle réduite (voir le pattern `bm_size = max(6, mini_w // 2)` pour un minimum de lisibilité même sur de très petites mini-vignettes).
- **Respecter les 8 règles UI Qt obligatoires** (CLAUDE.md) pour tout ajout de widget/dialogue lié à la minimap (ex. un futur menu contextuel ou une future fenêtre de config) — thème, langue à la volée, police, non-modale, centrage, etc. Le `MinimapWidget` actuel n'a ni tooltip, ni menu contextuel, ni texte traduit (rendu 100% graphique) donc ces règles ne s'y appliquent pas directement aujourd'hui, mais s'appliqueraient à toute extension qui ajouterait du texte/dialogue.
- **La couleur du rectangle de viewport est volontairement fixe** (pas de dérivation depuis `get_current_theme()`) — ne pas "corriger" ça vers une couleur thémée sans demande explicite.
- Si un jour la minimap doit devenir redimensionnable par l'utilisateur, il faudra remplacer le `QHBoxLayout` simple par un `QSplitter` comme pour la colonne d'icônes (voir skill `icon-toolbar`) — actuellement, `_MINIMAP_WIDTH = 120` est une constante en dur dans `panel_widget.py`.
