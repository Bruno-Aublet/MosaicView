---
name: minimap
description: Localiser ou modifier la minimap de MosaicView (panneau latéral droit, aperçu réduit de la grille avec rectangle de zone visible navigable, style VS Code ; mêmes badges et cadres que la mosaïque, pages « Supprimée » comprises : badge, grisé si la visionneuse les saute). Utiliser dès qu'une tâche touche à minimap_widget_qt.py, MinimapWidget, _paint_overlays, au chevron minimap de la barre de menu, ou à une nouvelle marque de vignette à reproduire dans la minimap.
---

# Minimap — MosaicView

Panneau optionnel accolé à droite de la mosaïque, affichant un aperçu miniature de **toute** la grille de vignettes du panneau, avec un rectangle vert représentant la zone actuellement visible dans la mosaïque. Comportement inspiré de la minimap de VS Code : clic pour sauter à un endroit, drag du rectangle pour naviguer en continu. **Défilement indépendant de la mosaïque** (décision utilisateur) : la minimap et la mosaïque défilent chacune de leur côté, seuls le clic simple et le glisser du rectangle vert font bouger la mosaïque.

## Fichier central — `modules/qt/minimap_widget_qt.py`

Un seul fichier, une seule classe : **`class MinimapWidget(QWidget)`** (~320 lignes). Widget dessiné entièrement à la main via `QPainter` dans `paintEvent()` — ce n'est pas une vue Qt (pas de `QGraphicsView`/`QGraphicsScene`), juste un rendu manuel qui relit les items déjà construits par la mosaïque.

**Lecture seule sur le contenu** : la minimap ne modifie jamais `state.images_data` ni les items de la mosaïque. La seule action qu'elle déclenche en retour sur le canvas est un changement de position de scroll (`canvas.centerOn(...)`).

## Intégration dans le panneau — `modules/qt/panel_widget.py`

- **`PanelWidget._build_minimap_panel()`** : construit le panneau conteneur (`QWidget` nommé `"minimapPanel"`, largeur fixe `_MINIMAP_WIDTH = 120`) et y place le `MinimapWidget(self._canvas, owner_panel=self)`.
- Positionné dans `content_row_layout` (`PanelWidget._build_center_panel`), **à droite** de `self._content_stack` (qui contient le canvas), via un simple `QHBoxLayout` — **pas de `QSplitter`** : la largeur de 120px n'est jamais redimensionnable par l'utilisateur, contrairement à la colonne de gauche (icon-toolbar) qui utilise un splitter.
- **`PanelWidget._toggle_minimap()`** : bascule `self._minimap_visible`, montre/cache `self._minimap_panel`, met à jour le chevron du menu, redonne le focus au canvas, et persiste l'état en config (`set_minimap_visible` pour le panneau 1, `set_minimap_visible_panel2` pour le panneau 2 en split-view — deux clés indépendantes, pas de synchro entre panneaux).
- Chaque panneau (panel1/panel2 en mode split) a **sa propre instance** de `MinimapWidget`, liée à son propre canvas — aucun état partagé entre les deux.

## Déclenchement — chevron de la barre de menu

- `modules/qt/menubar_qt.py` (`build_menubar`, `_on_minimap_toggle`) : une `QAction` chevron (`"«"`/`"»"`, sens inversé) tout à l'extrême droite de la barre de menu, symétrique du chevron de la colonne d'icônes à gauche. Callback câblé via `build_menubar_callbacks` (`menubar_callbacks_qt.py`, `get_minimap_visible` / `toggle_minimap` → `mw._toggle_minimap`).
- Pas de raccourci clavier dédié ni d'entrée dans un menu contextuel — uniquement ce chevron.

## Persistance — `modules/qt/config_manager.py`

Deux clés indépendantes, cachées par défaut :
- `minimap_visible` (panneau 1) : `get_minimap_visible()` / `set_minimap_visible(visible)`.
- `minimap_visible_panel2` (panneau 2) : `get_minimap_visible_panel2()` / `set_minimap_visible_panel2(visible)`.

Restauration au démarrage : `restore_session` (`session_restore_qt.py`, voir skill `session-restore`) appelle `win._panel._toggle_minimap()` **avant** `win.show()` si `cfg.get_minimap_visible()` est vrai (évite un flash de layout). `reset_to_defaults()` (même fichier) referme la minimap sur tous les panneaux avant de recalculer la taille par défaut de la fenêtre — sinon la largeur minimale imposée par le panneau minimap encore visible fausse le calcul du resize.

## Géométrie — taille FIXE, pas de compression

Point le plus important à comprendre avant de toucher ce fichier : **la taille d'une mini-vignette est fixe**, dérivée uniquement de la largeur du panneau (120px) divisée par le nombre de colonnes réel de la mosaïque (`_real_cols()`, miroir de `MosaicCanvas._cols()`). Elle n'est **jamais compressée** pour faire tenir tout le contenu verticalement — sur un omnibus de 1500+ pages, ça rendrait les mini-vignettes illisibles.

Conséquence : la minimap gère son **propre défilement vertical interne** (`self._scroll_y`, coordonnées minimap), sans `QScrollBar` visible, **indépendant de celui de la mosaïque** :
- Le défilement de la mosaïque ne touche pas `_scroll_y` (`_on_canvas_scrolled`, branché sur les deux scrollbars du canvas, ne fait que `update()`) : seul le rectangle vert bouge, et il peut sortir de la partie visible de la minimap. L'utilisateur le retrouve en faisant défiler la minimap (molette ou glisser).
- `_center_scroll_on_viewport()` recentre ce défilement sur le rectangle **uniquement quand la disposition change** : `_current_layout_signature()` (doc_generation du state du canvas — changée à l'ouverture et à la fermeture d'un document —, taille des vignettes, colonnes, taille du viewport du canvas et de la minimap) est comparée à `self._layout_signature`. Sélection, ajout ou suppression de pages ne recentrent pas.
- Comparaison faite par `_on_canvas_changed` (sur `status_changed`, fin d'un `render_mosaic`) et par `_sync_after_relayout` (timer `_relayout_timer`, `_RELAYOUT_DELAY_MS = 30`, relancé à chaque resize du canvas — filtre d'événements — ou de la minimap). **Piège** : le canvas replace ses vignettes dans un `QTimer.singleShot(0, self._relayout)` posé dans son `resizeEvent`, donc après le filtre de la minimap ; un recentrage immédiat ou par un `singleShot(0)` lirait l'ancienne disposition — d'où le délai. Quand `_on_canvas_changed` recentre, il pose aussi `_recenter_pending` et relance le timer : le recentrage est refait une fois que le canvas a éventuellement repositionné son défilement après le rendu.
- `_clamp_scroll()` borne ce défilement à `[0, content_height - panel_height]` (appelée aussi quand la disposition ne change pas, pour un contenu qui raccourcit).

### Chaîne de conversion de coordonnées

Trois systèmes de coordonnées coexistent — toujours passer par ces méthodes, ne jamais improviser un calcul de position :
- **Scène réelle** (coordonnées du `MosaicCanvas`/`QGraphicsScene`, en pixels de vignette réelle).
- **Minimap sans scroll** : scène réelle × `_scale()` (ratio `mini_w / real_cw`, identique en X et Y).
- **Minimap widget** (ce qui est réellement peint/cliqué) : minimap sans scroll − `_scroll_y`, + le padding fixe `_MINI_PAD = 4`.

Conversions dans les deux sens :
- `_scene_to_minimap_rect(scene_rect)` : scène → widget minimap (sert à placer les mini-vignettes et le rectangle de viewport).
- `_minimap_to_scene_point(pos)` : widget minimap → scène (sert à interpréter un clic/drag utilisateur).

## Ce qui est dessiné — `paintEvent()`

Pour chaque item de `self._canvas._items` (liste déjà construite par `render_mosaic()` de la mosaïque — la minimap ne la reconstruit pas, elle la relit) :
1. **Le pixmap réduit** de la vignette, via `_mini_pixmap_for_item()` → `_get_pixmap_for_size(entry, mini_w, mini_h)` (même fonction de cache que la mosaïque principale, voir skill `mosaic-thumbnails` — pas de cache séparé pour la minimap, juste un rescale à la volée de `qt_pixmap_large`).
2. **Les mêmes overlays qu'une `ThumbnailItem`**, redessinés à l'échelle réduite par `_paint_overlays()` :
   - Cadre de sélection bleu (`SEL_OUTLINE`) si `item._selected`.
   - Cadre rouge (image corrompue) si `entry["is_corrupted"]`, sinon cadre orange (`ALTERED_FRAME_COLOR`, importé de `mosaic_canvas`) si la page est altérée (`entries.is_altered`) — voir skill `corrupted-images`.
   - Badge marque-page (coin haut-droit) si `entry["_is_bookmarked"]` — voir skill `bookmarks`.
   - Badge doublon (coin haut-gauche) si `entry["_is_duplicate"]` — voir skill `duplicate-detection`.
   - Badge page « Supprimée » (coin bas-droit, `paint_deleted_page_badge` de `mosaic_canvas`) si `MinimapWidget._is_deleted_page(item)` (délègue à `ThumbnailItem._is_deleted_page`). Le grisé de la page, lui, est appliqué au dessin du pixmap dans `paintEvent` (`_grayed_pixmap`, opacité `DELETED_PAGE_OPACITY`), seulement si `deleted_pages_skipped()` — réglage lu une fois par dessin. Mêmes règles que la mosaïque (skill `mosaic-thumbnails`) ; redessin par `PanelWidget._repaint_deleted_page_marks()`.
3. **Le rectangle de zone visible** (`_viewport_rect_minimap()`) : contour vert fixe `_VIEWPORT_COLOR = QColor(0, 170, 0)` (**identique en thème clair et sombre, décision utilisateur assumée** — ne pas le faire dépendre de `get_current_theme()`), remplissage vert semi-transparent (alpha 40).

Les `DirItem` (dossiers virtuels) sont dessinés comme les `ThumbnailItem` via `_mini_pixmap_for_item()` (même chemin, `isinstance(item, (ThumbnailItem, DirItem))`), mais **sans** les overlays marque-page/doublon (qui n'ont de sens que pour une page réelle).

Items hors de la zone verticale visible du panneau minimap (`y + mini_ch < 0 or y > self.height()`) : skippés, pas de coût de rendu inutile même sur une très longue liste.

## Interactions souris

| Geste | Effet |
|---|---|
| Molette sur la minimap | défile la minimap seule (`_WHEEL_STEP` px minimap par cran) |
| Glisser le rectangle vert | la mosaïque suit en continu ; au bord, la minimap défile toute seule |
| Glisser ailleurs | défile la minimap seule, la mosaïque ne bouge pas |
| Clic simple ailleurs | la mosaïque saute à l'endroit cliqué, au relâchement |

- **`mousePressEvent`** : si le clic tombe **dans** le rectangle de viewport → démarre un drag (`_dragging_viewport = True`, curseur main fermée). Sinon → mémorise `_press_pos` et `_press_scroll_y` : saut ou défilement, décidé selon que la souris bouge avant le relâchement.
- **`mouseMoveEvent`** :
  - pendant le drag du rectangle, `_drag_canvas_to(pos)` recentre le canvas sur la position de la souris (moins l'offset de saisie initial, pour ne pas faire sauter le rectangle sous le curseur). Si la souris est dans la zone de bord (`_EDGE_ZONE` px en haut ou en bas) ou au-delà, `_autoscroll_timer` (`_AUTOSCROLL_MS`) appelle `_autoscroll_tick`, qui décale `_scroll_y` du pas de `_edge_speed` (croissant avec la distance au bord, plafonné à `_AUTOSCROLL_MAX`) puis rappelle `_drag_canvas_to(_last_drag_pos)` : la même position de souris désigne un autre endroit de la scène une fois la minimap décalée, la mosaïque suit donc même souris immobile ;
  - après un appui hors du rectangle, au-delà de `QApplication.startDragDistance()` → `_panning` : `_scroll_y = _press_scroll_y - déplacement vertical` (le contenu suit la souris) ;
  - sinon, change juste le curseur (`_update_hover_cursor` : main ouverte sur le rectangle, flèche ailleurs).
- **`mouseReleaseEvent`** : termine le drag (arrête l'autoscroll) ; après un appui hors du rectangle sans `_panning`, saut de la mosaïque sur le point d'appui (`centerOn`).
- **`wheelEvent`** : défile la minimap seule, **jamais** la mosaïque.
- **Focus en mode split** : `_notify_panel_focus()` — cliquer/draguer sur la minimap d'un panneau non-actif l'active, même comportement qu'un clic direct sur son canvas (via `mw._set_active_panel(panel)`).
- Pas de drag & drop de pages depuis/vers la minimap — volontairement absent (voir docstring en tête de fichier).

## Synchronisation avec le canvas

La minimap se repeint automatiquement sur ces événements (pas de polling) :
- `canvas.status_changed` (signal générique de la mosaïque — rechargement, réordonnancement, changement de sélection...) → `_on_canvas_changed` (recentrage si la disposition a changé, voir « Géométrie »).
- `canvas.horizontalScrollBar().valueChanged` / `verticalScrollBar().valueChanged` → `_on_canvas_scrolled`, simple `update()` : le rectangle bouge, pas la minimap.
- `QEvent.Resize` du canvas, intercepté via `installEventFilter(self)` (dans `MinimapWidget.__init__`, traité par `eventFilter`) — **nécessaire car `render_mosaic()` n'émet pas `status_changed` sur un simple resize** (juste un `_relayout()` interne) ; relance `_relayout_timer`.
- `resizeEvent()` du widget minimap lui-même (hauteur de la fenêtre, affichage de la minimap) → relance `_relayout_timer`.

## Piège déjà rencontré — lire `self._canvas._state`, jamais le singleton global

`_real_tw()`/`_real_th()`/`_real_cols()` lisent explicitement **`self._canvas._state`**, jamais `modules.qt.state.state` (le singleton global). Raison documentée en commentaire dans le code, au-dessus de ces méthodes : pendant certaines opérations qui basculent temporairement le singleton global vers un autre state (ex. `_apply_thumb_size` en mode split), un `update()` différé de la minimap (via `QTimer.singleShot` ou un signal en file d'attente) pourrait repeindre avec un state déjà restauré/périmé si elle lisait le singleton au lieu de l'état propre à son canvas. Toute nouvelle méthode de géométrie ajoutée à `MinimapWidget` doit suivre le même principe.

## Comment étendre / pièges connus

- **Ne jamais ajouter de `QScrollBar` visible à la minimap** — le défilement interne (`_scroll_y`, molette ou glisser) est un choix délibéré, pas un oubli.
- **Ne jamais relier à nouveau le défilement de la minimap à celui de la mosaïque** (molette qui ferait défiler la mosaïque, recentrage à chaque défilement du canvas) : leur indépendance est une décision utilisateur. Un nouvel événement qui doit recentrer la minimap entre dans `_current_layout_signature()`, jamais dans `_on_canvas_scrolled`.
- **Ne jamais compresser les mini-vignettes pour tout faire tenir** — la taille fixe dérivée de `_real_cols()` est le point central de la conception (voir docstring en tête de fichier).
- **Toute nouvelle donnée à afficher en overlay** (nouveau badge, nouvel indicateur) doit être ajoutée dans `_paint_overlays()`, à l'échelle `mini_w`/`mini_h` — ne pas dupliquer la logique de `ThumbnailItem.paint()` telle quelle, la reproduire à l'échelle réduite (voir le pattern `bm_size = max(6, mini_w // 2)` pour un minimum de lisibilité même sur de très petites mini-vignettes).
- **Respecter les 9 règles UI Qt obligatoires** (CLAUDE.md) pour tout ajout de widget/dialogue lié à la minimap (ex. un futur menu contextuel ou une future fenêtre de config) — thème, langue à la volée, police, non-modale, centrage, etc. Le `MinimapWidget` actuel n'a ni tooltip, ni menu contextuel, ni texte traduit (rendu 100% graphique) donc ces règles ne s'y appliquent pas directement aujourd'hui, mais s'appliqueraient à toute extension qui ajouterait du texte/dialogue.
- **La couleur du rectangle de viewport est volontairement fixe** (pas de dérivation depuis `get_current_theme()`) — ne pas "corriger" ça vers une couleur thémée sans demande explicite.
- Si un jour la minimap doit devenir redimensionnable par l'utilisateur, il faudra remplacer le `QHBoxLayout` simple par un `QSplitter` comme pour la colonne d'icônes (voir skill `icon-toolbar`) — actuellement, `_MINIMAP_WIDTH = 120` est une constante en dur dans `panel_widget.py`.
