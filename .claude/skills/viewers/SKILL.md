---
name: viewers
description: Localiser et modifier une des 5 visionneuses plein-écran de MosaicView (lecture + 4 annexes d'édition — ajustements, clone, redressement, texte). Utiliser dès qu'une demande porte sur "la visionneuse", le zoom, le pan, ou un mode de la fenêtre d'ajustements.
---

# Visionneuses plein-écran — MosaicView

Cinq fenêtres plein-écran distinctes affichent une image en grand avec zoom/pan. Toutes héritent de `QDialog`, sont non-modales (règle UI n°4), et partagent un vocabulaire commun (`zoom_level`/`_zoom`, `adjust_zoom`, `reset_zoom`, `fit_to_window`) — mais **ce sont 5 implémentations séparées**, pas une classe commune : une correction ou un ajout dans l'une ne se propage jamais automatiquement aux autres (voir historique récent : le pan écrasé au zoom, le lag à fort zoom, Ctrl+0/Ctrl+1/Ctrl+Plus/Ctrl+Minus ont dû être portés fichier par fichier).

## Les 5 visionneuses

| Fichier | Classe | Rôle | Ouverte depuis |
|---|---|---|---|
| `modules/qt/image_viewer_qt.py` | `ImageViewer` | Visionneuse principale de lecture (pages simple/double/continue, GIF animés — lecture seule, voir skill `animated-gif` pour la création/édition, crop — voir skill `page-crop`, marque-page) | Double-clic sur une vignette de la mosaïque → `panel_widget.py::_open_image_viewer` → `open_image_viewer()` |
| `modules/qt/adjustments_viewers_qt.py` | `AdjustmentViewerDialog` | Aperçu/édition plein écran pour 8 modes de retouche (voir tableau ci-dessous) | Bouton "aperçu plein écran" de chaque onglet dans `adjustments_dialog_qt.py::_open_viewer(mode)` |
| `modules/qt/clone_zone_viewer_qt.py` | `CloneZoneViewerDialog` | Tampon de clonage (Ctrl+clic = source, clic gauche maintenu = peindre — voir skill `clone-zone`) | `show_clone_zone_viewer()` — barre d'icônes / menu / clic droit mosaïque |
| `modules/qt/straighten_viewer_qt.py` | `StraightenViewerDialog` | Redressement d'image (tracé d'une ligne de référence puis rotation à angle libre — voir skill `page-straighten`) | `show_straighten_viewer()` — mêmes points d'entrée |
| `modules/qt/text_viewer_qt.py` | `TextViewerDialog` | Visionneuse avec blocs de texte riche superposés, positionnables et formatables (OCR/traduction de bulles — voir skill `add-text-to-image`) | `show_text_viewer()` — mêmes points d'entrée |

## Les 8 modes de `AdjustmentViewerDialog` (une seule classe, pas 8 fichiers)

`AdjustmentViewerDialog(parent, selected_entries, settings, mode, on_close_callback=None, on_cancel_callback=None, callbacks=None)` — le paramètre `mode` (`str`) sélectionne le comportement, toujours dans le même fichier `adjustments_viewers_qt.py` :

| `mode` | Réglage |
|---|---|
| `sharpness` | Netteté, réglette -100..+100 |
| `brightness` | Luminosité + contraste, réglettes -100..+100 |
| `compression` | Qualité JPEG, réglette 1..100 |
| `remove_colors` | Intensité de suppression de couleurs, réglette 0..100 |
| `saturation` | Saturation, réglette -100..+100 |
| `unsharp` | Unsharp Mask, 3 réglettes (radius, percent, threshold) |
| `levels` | Point noir / point blanc via pipettes |
| `transparency` | Rendre transparent (flood fill ou global) au clic |

Chercher `if self._mode == '...'`/`elif self._mode == '...'` dans `adjustments_viewers_qt.py` pour trouver le code spécifique à un mode donné — la logique de zoom/pan/affichage (`_ImageScrollWidget`) est en revanche partagée par tous les modes.

**Ce skill ne couvre que le comportement générique du viewer (zoom/pan/plein écran/navigation) commun aux 8 modes.** Pour la formule PIL exacte, les bornes de valeur et les pièges propres à un mode donné, voir la skill `adjust-*` correspondante : `adjust-sharpness` (modes `sharpness`/`unsharp`), `adjust-brightness-contrast` (mode `brightness`), `adjust-compression` (mode `compression`), `adjust-remove-colors` (mode `remove_colors`), `adjust-saturation` (mode `saturation`), `adjust-levels` (mode `levels`, pipettes), `adjust-transparency` (mode `transparency`, flood fill/global). Pour la fenêtre parente qui ouvre ce viewer et récupère ses valeurs au retour, voir `adjustments-panel`.

## Les 4 modes de lecture de la visionneuse principale (`ImageViewer.page_mode`)

Distinct des 8 modes d'ajustement ci-dessus — propre à `image_viewer_qt.py`, aucun rapport avec `AdjustmentViewerDialog`. Attribut `self.page_mode`, une simple chaîne (`"single"` / `"double"` / `"continuous"` / `"webtoon"`), pas d'enum.

| `page_mode` | Comportement |
|---|---|
| `single` | Une page à la fois |
| `double` | Page 1 seule, puis paires 2-3, 4-5, etc. |
| `continuous` | Page 1 seule, puis paires glissantes 2-3, 3-4, 4-5, etc. (chaque page réapparaît dans la paire suivante) |
| `webtoon` | Ajouté 2026-07. Page simple (jamais de paire), pensé pour les images verticales (webcomics). Zoom initial calé sur `min(WEBTOON_MAX_WIDTH_PX=900, viewer_w) / img_w` au lieu du fit largeur+hauteur classique — l'image déborde donc verticalement de la fenêtre par construction. |

- **Cycle** : touche `D` → `toggle_double_page()` fait tourner `single → double → continuous → webtoon → single`, dans cet ordre fixe et circulaire — pas de va-et-vient. Aussi accessible via le menu contextuel (clic droit → l'entrée affiche le mode *suivant* dans le cycle, pas le mode actuel : `context_menu.viewer.reading_mode_double/continuous/webtoon/single`).
- **Label affiché** : `_update_mode_label()` — traductions `viewer.mode_single`/`viewer.mode_double`/`viewer.mode_continuous`/`viewer.mode_webtoon` dans `locales/*.json`. Masqué automatiquement pour un GIF animé.
- **Routage vers l'affichage** : `display_image()` contient un `if self.page_mode == "double": ... elif self.page_mode == "continuous": ... else: ...` qui décide, pour chaque mode, quelles pages envoyer à `_display_single_page()` (une page — webtoon y tombe toujours via le `else`) ou `_display_double_page(left_idx, right_idx, ...)` (deux pages assemblées côte à côte, jamais utilisé par webtoon). La logique de pagination par paire diffère entre `double` (paires fixes 2-3/4-5) et `continuous` (paires glissantes) — voir `_double_page_pair_start()` pour le calcul des débuts de paire en mode `double`.
- **Cas particuliers déjà gérés, à ne pas casser en ajoutant un mode** : une page "multiple" (`is_multiple_page`, ex. une planche triple) s'affiche toujours seule même en mode `double`/`continuous` ; une "image large" (`is_wide_image`) idem en mode `continuous` ; la position 0 (première page) s'affiche toujours seule, jamais en paire.

**Pour ajouter un 5e mode de lecture** : ajouter la valeur à la chaîne de `toggle_double_page()`, un cas dans `_update_mode_label()` (+ nouvelle clé de traduction), et un bloc `elif self.page_mode == "nouveau_mode":` dans `display_image()` qui décide de la pagination — probablement une nouvelle méthode `_double_page_pair_start`-like si la logique de paire diffère des modes existants. Vérifier aussi le menu contextuel (`_show_context_menu`) et l'entrée `help.viewer_content` du mode d'emploi (skill `user-guide`) qui énumèrent explicitement les modes actuels. Pour l'ajout de clés de traduction dans les 3 langues fictives (tlh/sjn/qya) et leurs variantes CSUR, ne pas oublier `PRESERVE_RE` dans `conversion_tools/convert_piqad_csur.py`/`convert_tengwar_csur.py` si le nom du mode est un terme technique/nom propre à préserver tel quel (voir skill `add-translation`).

### Mode `webtoon` : molette = scroll vertical + changement de page en butée

Seul mode où la molette (sans Ctrl) ne navigue pas directement entre pages : `_on_wheel()` route vers `_on_webtoon_wheel()` qui scrolle `self._canvas.pan_offset_y` verticalement dans la page. Une fois en butée (haut ou bas), le **débordement** du delta au-delà de la limite (`overflow`, pas un simple compteur d'événements) s'accumule dans `self._webtoon_bound_scrolls` jusqu'à `WEBTOON_PAGE_TURN_THRESHOLD` (360, ≈3 crans de molette standard), puis déclenche `navigate()`. **Piège déjà corrigé** : compter le nombre d'événements plutôt que la magnitude du delta casse le mécanisme dès qu'un scroll rapide envoie un seul événement avec un delta énorme (plusieurs dizaines de crans agrégés par le driver) — ce gros événement dépassait la butée sans jamais être comptabilisé, forçant l'utilisateur à recommencer l'accumulation à zéro avec de petits crans. D'où la nécessité de calculer un `overflow` (portion du delta qui dépasse la limite) reporté dans l'accumulation dès le même événement, plutôt qu'un test binaire `at_bound`.

Offset vertical calé en haut de page (`offset_y = pan_offset_y`, jamais centré comme les autres modes) dans les 3 chemins d'affichage concernés : `_display_single_page`, `_animate_gif_frame` (GIF vertical). `_display_double_page` n'est jamais emprunté par ce mode. Le pan vertical est remis à zéro à chaque changement de page et à l'entrée dans le mode (sinon le pan de la page précédente reste appliqué à la nouvelle image).

Une fine barre de progression verticale (piste + curseur semi-transparents, non cliquable, collée au bord droit du canvas) est dessinée dans `_ViewerCanvas.paintEvent`, visible uniquement en mode `webtoon` et seulement si `display_height > height()` — volontairement pas une vraie scrollbar Qt (casserait l'esthétique plein-écran et interférerait avec le pan clic-droit).

## Architecture d'affichage : deux familles différentes

**Famille A — les 4 visionneuses annexes** (`adjustments_viewers_qt.py`, `clone_zone_viewer_qt.py`, `straighten_viewer_qt.py`, `text_viewer_qt.py`) : chacune a un petit widget interne (`_ImageScrollWidget` ou équivalent local au fichier, ex. `_CloneImageWidget`) dont le `paintEvent` fait :
```python
w = int(self._pixmap.width() * self._zoom)
h = int(self._pixmap.height() * self._zoom)
painter.drawPixmap(x, y, w, h, self._pixmap)
```
Le pixmap est stocké **une seule fois à sa résolution native** (`set_pixmap`) ; c'est Qt (`SmoothPixmapTransform`) qui fait l'étirement à l'affichage, à chaque frame, quel que soit le niveau de zoom. Rapide par construction — jamais de `resize()` PIL répété.

**Famille B — la visionneuse principale** (`image_viewer_qt.py`) : a été alignée sur ce même principe (2026-07, voir `CHANGELOG.md` v1.5.10) après avoir eu un mécanisme différent et plus coûteux (un `Image.resize()` PIL complet à chaque cran de zoom). Le canvas `_ViewerCanvas.paintEvent` fait désormais :
```python
target = QRectF(self.display_offset_x, self.display_offset_y, self.display_width, self.display_height)
painter.drawPixmap(target, pm, QRectF(pm.rect()))
```
avec `pm` stocké à résolution source (page simple, ou image combinée pour le mode double page). **Si un futur bug de lag réapparaît dans une de ces 5 fenêtres, vérifier en premier qu'aucun `.resize()` PIL n'a été réintroduit dans le chemin de zoom** — c'est la régression la plus probable.

## Pan : toujours additionné au centrage, jamais recalculé à part

Dans les 5 visionneuses, le pan (clic droit + glisser) est un offset stocké séparément (`self._offset` dans les 4 annexes, `pan_offset_x/y` dans la visionneuse principale) et **additionné** au centrage géométrique à chaque redessin :
```python
x = self._offset.x() + (self.width() - w) // 2
```
**Piège déjà rencontré** (visionneuse principale uniquement, avant le fix de 2026-07) : si le calcul d'offset ignore le pan et repart du centrage pur à chaque zoom, l'image se recentre à chaque cran de molette. Vérifier ce pattern avant de toucher au calcul d'offset dans n'importe laquelle des 5 fenêtres.

## Raccourcis clavier zoom — communs aux 5, à garder synchronisés

Chaque fenêtre câble ces raccourcis indépendamment (pas de table de raccourcis partagée) :

- `Ctrl+Plus` / `Ctrl+Minus` : `adjust_zoom(+0.1)` / `adjust_zoom(-0.1)` dans la visionneuse principale (pas de bouton +/- à l'écran, molette Ctrl+molette aussi disponible) ; `adjust_zoom(+0.15)` / `adjust_zoom(-0.15)` dans les 4 annexes (même pas que leurs boutons +/- à l'écran — toujours reprendre cette valeur, ne pas en improviser une autre).
- `Ctrl+0` : ajuste le zoom à la fenêtre (`fit_zoom_to_window()` / `fit_to_window()`).
- `Ctrl+1` : zoom à 100% = taille réelle des pixels de l'image (`reset_zoom()`).
- Molette seule : navigation entre pages (visionneuse principale uniquement) ou scroll vertical (les 4 annexes).

**Piège vérifié** : dans certaines visionneuses annexes, les boutons +/- à l'écran passent par une méthode wrapper du dialog (`self._adjust_zoom(delta)` dans `adjustments_viewers_qt.py`) qui met aussi à jour le label de pourcentage — un raccourci clavier branché directement sur `self._img_widget.adjust_zoom()` sans passer par ce wrapper laisserait le label figé. Dans d'autres (clone/straighten/text), le widget expose `on_zoom_changed` (callback) déjà câblé sur la mise à jour du label, donc brancher directement sur le widget suffit. **Toujours vérifier lequel des deux mécanismes est utilisé dans le fichier concerné avant d'ajouter un raccourci**, plutôt que de copier le pattern d'un autre fichier au hasard.

## Sémantique du zoom : 100% = taille réelle des pixels

Dans les 5 visionneuses, `zoom_level = 1.0` signifie 1 pixel image = 1 pixel écran — jamais un multiple de la taille "ajustée à la fenêtre". Le fit-to-window est un calcul ponctuel (au premier affichage, ou sur demande via `fit_to_window()`/`fit_zoom_to_window()`), pas la référence du pourcentage affiché. Ne jamais réintroduire un calcul du type `final_w = img_w * ratio_fit * zoom_level` (upscale caché au-delà de la résolution native, source de lag et de confusion sur ce que "100%" veut dire).

## Piège performance — `_make_checkerboard_pil` (fond damier transparence)

`modules/qt/entries.py::_make_checkerboard_pil(w, h, tile)` génère le fond en damier affiché derrière toute image avec canal alpha (`_compose_on_checkerboard`, appelé dans `_display_single_page` de la visionneuse principale, dans les miniatures de la mosaïque, et dans l'aperçu de la fenêtre d'ajustements). **Pas spécifique à une visionneuse ni à un mode** — touche les 3 points d'appel dès qu'une image transparente de grande taille est affichée.

Historique (2026-07) : implémentée à l'origine par une double boucle Python pixel par pixel (`for y: for x: pixels[x,y] = ...`), invisible sur une page de comics classique ou une miniature (peu de pixels), mais provoquant un gel UI de ~3,4 secondes sur une page verticale de webtoon (940×11075 ≈ 10,4M pixels) — découvert via le mode `webtoon` (seul cas ayant poussé la taille d'image assez haut pour rendre le coût perceptible), mais le correctif bénéficie à tous les appelants. Corrigée par un tuilage par blocs (motif de base 2×2 cases construit une fois, puis répété via `paste()`), ramenant le cas à ~240ms (gain ≈14×). **Si un lag réapparaît sur l'affichage d'une image transparente de grande taille (quelle que soit la fenêtre), vérifier en premier qu'aucune boucle pixel par pixel n'a été réintroduite dans `_make_checkerboard_pil` ou une fonction similaire.**

## Avant de modifier une de ces fenêtres

1. Identifier la bonne fenêtre parmi les 5 (et le bon `mode` si c'est `AdjustmentViewerDialog`) — ne pas supposer qu'elles partagent du code, grep le nom de la classe/fichier pour confirmer.
2. Vérifier si le changement doit être répliqué dans les 4 autres visionneuses ou seulement celle-ci — demander à l'utilisateur si ambigu (comme pour le zoom, propagé sur demande explicite après correction dans la principale).
3. Respecter les règles UI générales du projet (`CLAUDE.md`) : non-modale, thème dynamique, retraduction à la volée, `_wt()` pour le titre de fenêtre, tooltips via `OverlayTooltip`.

## Références croisées

- `adjustments-panel` — le panneau "Ajustements d'image" qui ouvre `AdjustmentViewerDialog` depuis chaque bouton "Ajuster avec la visionneuse", et récupère les valeurs modifiées à la fermeture.
- `adjust-color-depth`, `adjust-compression`, `adjust-sharpness`, `adjust-brightness-contrast`, `adjust-levels`, `adjust-transparency`, `adjust-image-mode`, `adjust-remove-colors`, `adjust-saturation`, `adjust-effects` — logique PIL propre à chaque mode/section (`adjust-color-depth`, `adjust-image-mode` et `adjust-effects` n'ont pas de mode viewer dédié, uniquement une réglette dans le panneau).
- `apply-image-operation` — pattern d'invalidation de caches suivi par `_apply_to_current`/`_apply_to_all`/`_apply_levels_all` de ce viewer.
- `rotate-flip` — la rotation 90°/miroir de la mosaïque (hors visionneuse), un mécanisme entièrement séparé de la rotation libre du redressement malgré le mot "rotation" en commun ; ne partage aucun code avec `StraightenViewerDialog`.
- `page-straighten` — détail complet du mécanisme de redressement (calcul d'angle à partir du trait tracé, undo/redo interne à la fenêtre en plus de l'historique global).
- `add-text-to-image` — détail complet de l'ajout de texte riche (placement/formatage/rendu des blocs, triple empilement d'historique undo/redo).
- `clone-zone` — détail complet du tampon de clonage (modes fixe/relatif, calcul de décalage source/destination, snapshot figé vs référence directe) ; seule des 3 visionneuses d'édition sans navigation entre pages.
- `page-crop` — détail complet du recadrage (rubber-band interactif, poignées de redimensionnement, validation par bouton flottant ou double-clic) ; contrairement aux 3 skills ci-dessus, intégré à la visionneuse principale de lecture plutôt qu'une fenêtre séparée.
- `create-ico` — le cas particulier de sauvegarde `.ico` dans `AdjustmentViewerDialog` (`adjustments_viewers_qt.py:1401`, `fmt = 'ICO'` forcé pour ne pas perdre le format multi-résolution après un ajustement de transparence).
- `animated-gif` — création/édition de GIF animé (`AnimatedGifDialog`), qui consomme les mêmes `is_animated_gif`/`get_gif_frame` que le mode de lecture animée de la visionneuse principale mais sans code partagé.
