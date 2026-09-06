---
name: blur-stamp
description: Localiser ou modifier le tampon de flou (peinture au clic maintenu pour flouter des pixels, taille et puissance réglables séparément), outil "blur" de la barre d'outils flottante de la visionneuse principale. Utiliser dès qu'une tâche touche à blur_tool_qt.py, BlurCanvasMixin, BlurViewerMixin, _BlurOptionsPanel, ou au bouton/menu "Tampon de flou".
---

# Tampon de flou — MosaicView

Outil de retouche intégré directement dans la **visionneuse principale de lecture** (`ImageViewer`, `modules/qt/image_viewer_qt.py`), pas une fenêtre séparée — l'utilisateur peint au clic gauche maintenu avec un pinceau circulaire pour flouter (gaussien) la zone couverte, sans devoir définir de zone source (contrairement au clonage). Usage principal visé : rendre du texte ou tout autre contenu illisible de façon fiable et irréversible (censure), pas seulement un effet artistique — d'où deux choix de conception assumés : cercle plein sans dégradé sur le bord (un bord flouté partiellement laisserait une frange encore lisible), et flou qui s'accumule si on repasse plusieurs fois sur la même zone.

Tout le mécanisme (masque circulaire PIL, flou gaussien local, undo/redo par stroke) vit dans `modules/qt/blur_tool_qt.py`, sans fenêtre dédiée séparée.

Architecturalement calqué sur `clone-zone` (même famille de pattern : peinture continue, commit par stroke, pas de bouton "Valider") — chaque outil de la barre d'outils flottante a son propre module, **aucun code partagé** entre eux (voir skill `viewers` pour la règle architecturale).

## Module dédié — `modules/qt/blur_tool_qt.py`

Tout le mécanisme (état/interactions souris + commit dans l'historique + panneau de réglages + rendu pur) tient dans ce seul module, conformément à la règle CLAUDE.md "ne jamais migrer le code d'un outil dans `image_viewer_qt.py`" :

- **`BlurCanvasMixin`** (hérité par `_ViewerCanvas`, `image_viewer_qt.py`) — état du pinceau (`_blur_brush_radius`, `_blur_strength`), gestion souris (`blur_mouse_press`/`blur_mouse_move`/`blur_mouse_release`, délégation depuis les handlers réels de `_ViewerCanvas`), curseur en cercle (`blur_update_cursor`). **Aucun overlay dessiné** — contrairement au clonage (marqueur de source), rien à resynchroniser au pan/zoom/resize en dehors du curseur lui-même, qui suit nativement la souris.
- **`BlurViewerMixin`** (hérité par `ImageViewer`) — peinture effective (`_on_blur_paint_stroke`, `_blur_apply_stamp`), aperçu pendant le stroke (`_blur_refresh_display`), commit final (`_on_blur_paint_end`), rejeu depuis une macro (`perform_blur_step`).
- **`_BlurOptionsPanel`** (`QWidget`) — panneau flottant de réglages (taille du pinceau, puissance du flou), affiché sous la barre d'outils quand l'outil est actif.
- **`make_blur_brush_cursor`** — fonction de rendu pure (curseur en cercle simple, sans croix centrale contrairement au curseur cible du clonage — pas de notion de "point exact visé" ici, tout le disque est traité identiquement).
- **Ce qui reste dans `image_viewer_qt.py`** : rien de spécifique au flou (comme le clonage) — pas de bouton "Valider" partagé à câbler pour cet outil.

## Pas de zone source, contrairement au clonage

Différence structurelle principale avec `clone-zone` : aucun Ctrl+clic, aucun marqueur de source à définir ni à dessiner. Le pinceau agit directement là où on clique — `blur_mouse_press` démarre toujours un stroke au premier clic gauche, sans condition préalable (contrairement à `clone_mouse_press`, qui exige qu'une source ait déjà été définie).

## Pas de validation différée — même famille que le clonage

Comme le clonage (contrairement au crop/straighten), **le flou peint en continu** : chaque coup de tampon (stroke, du clic au relâchement) modifie déjà l'image et devient sa propre entrée d'historique dès le relâchement du clic. Conséquences directes :

- **Pas de bouton "Valider"** pour cet outil — `_VALIDATE_KEYS` (`image_viewer_qt.py`) n'a pas d'entrée `"blur"`.
- **Pas de persistance de travail non validé par page** — pas de `_blur_by_page` équivalent à `_crop_by_page`/`_straighten_by_page` : chaque stroke est déjà commité (bytes + `save_state()`) à son relâchement.
- **Pas de variante grise "conservée mais désélectionnée"** — rien à figer visuellement à la désélection de l'outil (pas de source à effacer non plus, contrairement au clonage).
- **Aucune contribution à `_has_unvalidated_work()`** — fermer la visionneuse ou l'application pendant qu'on est sur l'outil flou ne déclenche jamais l'avertissement "travail non validé".

## Deux réglages indépendants — taille et puissance

Réglables via deux couples slider/spinbox dans `_BlurOptionsPanel` (`set_blur_brush_radius`/`set_blur_strength` sur le canvas) :

- **Taille** (`_blur_brush_radius`, 1-400) — diamètre du disque couvert par le pinceau, mêmes bornes que le tampon de clonage.
- **Puissance** (`_blur_strength`, 1-30) — rayon du flou gaussien (`ImageFilter.GaussianBlur(radius=...)`) appliqué à l'intérieur de ce disque. Un rayon faible laisse deviner les formes, un rayon élevé produit une bouillie totalement illisible — réglage volontairement séparé de la taille pour pouvoir monter la puissance au maximum sans devoir élargir le pinceau (utile pour la censure de texte).

Le flou **s'accumule** si on repasse plusieurs fois sur la même zone (même stroke ou strokes successifs) : chaque application repart de l'image de travail déjà floutée par les applications précédentes, jamais de l'image d'origine.

## Application d'un coup de pinceau — `_blur_apply_stamp` (`BlurViewerMixin`)

Applique le flou gaussien à un carré local englobant le disque de destination, puis ne colle le résultat que dans le disque plein — entièrement en PIL natif :

1. `r = (brush_radius - 1) / 2` — même conversion diamètre → demi-rayon flottant que le clonage.
2. **Carré local, pas l'image entière** : la zone rognée (`crop`) avant flou est le disque élargi d'une marge égale à la puissance du flou (`margin = strength + 2`) — assez grande pour que le calcul du flou "voie" du contexte au-delà du bord du cercle, sans recalculer un flou gaussien sur l'image complète à chaque frame de peinture (coûteux sur une grande page, sans bénéfice visuel puisque seul l'intérieur du disque est finalement conservé).
3. **Masque circulaire plein, sans dégradé** (`PIL.ImageDraw.ellipse`, `fill=255`) appliqué au collage (`dst.paste(blurred, ..., mask=mask)`) — décision assumée (voir intro) : un dégradé sur le bord laisserait une frange du contenu original partiellement lisible, incompatible avec l'objectif de censure fiable.
4. Bounding box clampée aux limites de l'image, comme le clonage — un stroke proche du bord ne plante pas.

## `_blur_work_img` — image de travail séparée de `entry['bytes']`

Même principe que `clone_tool_qt.py::_clone_work_img` : une copie PIL RGBA (`self._blur_work_img`) qui existe **seulement pendant un stroke**, chargée depuis `entry['bytes']` au premier point peint, mutée en place à chaque point suivant, affichée directement via `_blur_refresh_display()` (sans repasser par `entry['bytes']`/`ensure_image_loaded`/`display_image()`), commitée dans `entry['bytes']` seulement au relâchement du clic (`_on_blur_paint_end`).

**`_orig_mode`** (mode PIL d'origine, pour aplatir correctement sur blanc en sortie si le format ne supporte pas l'alpha) posé une seule fois sur `entry` au premier stroke si absent — même mécanisme que le clonage, `.bmp` exclu pour la même raison (Pillow écrit un canal alpha 32-bit mais ne le redétecte pas à la relecture).

## Interpolation pendant un mouvement rapide — `BlurCanvasMixin.blur_mouse_move`

Même mécanisme que `clone_tool_qt.py::CloneCanvasMixin.clone_mouse_move` : si la souris se déplace plus vite qu'un pas de `max(1, zoom * brush_radius * 0.5)` pixels widget entre deux événements, le code interpole plusieurs points intermédiaires et applique le flou à chacun — sans ça, un mouvement rapide laisserait des trous non floutés dans le trait.

## Throttle d'affichage — `_blur_display_timer`, ~30 fps

Même mécanisme que le clonage (`_blur_display_timer`, `QElapsedTimer`) : le pixmap affiché n'est rafraîchi que si au moins 33 ms se sont écoulées depuis le dernier rafraîchissement — l'image de travail PIL, elle, est modifiée à **chaque** point peint sans throttle (seul l'affichage est retardé).

## Undo/redo — un stroke entier, unifié avec le panneau

Même mécanisme que le clonage : `_on_blur_paint_end()` fait `save_state()` avant de committer les bytes, puis `save_state(force=True)` après. L'unité d'undo est **le stroke entier**, pas chaque point peint individuellement. `self._toolbar.refresh_undo_redo_state()` est rappelée après le commit, comme pour tout autre outil de la barre.

## Macros

Supporté via `perform_blur_step(params)` (`BlurViewerMixin`) et le dispatcher `macro_engine.py::apply_step_to_entry` (`tool == "blur"`) — rejoue un stroke complet depuis un payload `{"brush_diam_px", "strength", "points_px"}` (coordonnées image absolues, pas mises à l'échelle), même principe que `clone_tool_qt.py::perform_clone_step`. Enregistré via `_macro_record_step("blur", ..., "macro.step_blur", {"stroke_points": len(points)})` en fin de `_on_blur_paint_end`, un point par frame réellement affichée (même throttle que l'affichage, pas chaque pixel du stroke).

## Points d'entrée UI

**Un seul point d'entrée** : directement dans la visionneuse principale déjà ouverte, en sélectionnant l'icône "Tampon de flou" dans la barre d'outils flottante (groupe "ajout de contenu", juste après le clonage). Pas de point d'entrée dédié depuis la mosaïque — pas de menu contextuel, pas d'entrée de barre de menu, pas de bouton dans la colonne d'icônes.

## Zoom, pan, plein écran

Vocabulaire commun aux visionneuses du projet (skill `viewers`) : `Ctrl++`/`Ctrl+-`, `Ctrl+0` (fit), `Ctrl+1` (reset 100%), `F11` (plein écran), molette, clic droit maintenu (pan) — toujours actif quel que soit l'outil sélectionné.

Le curseur en cercle (`make_blur_brush_cursor`) est reconstruit dynamiquement à chaque survol (`blur_update_cursor`), pas seulement à la première fois — le rayon écran dépend du zoom courant. Posé sur le **canvas** : `_BlurOptionsPanel.enterEvent`/`_check_really_left` le réinitialisent (`setCursor(Qt.ArrowCursor)`/`unsetCursor()`), sinon il resterait affiché par-dessus le panneau au survol — voir skill `viewers`, section "Piège transversal — le curseur spécifique d'un outil reste affiché par-dessus son propre panneau flottant".

## Traductions

`locales/fr.json` : `viewer.toolbar_blur_tooltip`/`toolbar_blur_instruction`, `viewer.blur_brush_size_label`/`blur_strength_label`, `messages.errors.blur_failed.title`/`.message`, `macro.step_blur` — propagées aux 46 fichiers de langue (40 naturelles + tlh/sjn/qya latin + 3 CSUR). Aucun mot existant pour "flou" dans les 3 lexiques de langues fictives au moment de l'ajout — néologismes construits sur des racines déjà attestées dans chaque fichier (tlh `boch`=trouble/sale + causatif `-moH`, sjn/qya racine `hui-`=brume/ombre) plutôt qu'improvisés sans méthode. Voir skill `add-translation`.

**Absent du mode d'emploi** (`user_guide_qt.py`) — même situation que `clone-zone`/`page-straighten`/`add-text-to-image`.

## Comment étendre

- **Changer la forme du pinceau** (actuellement toujours circulaire) : uniquement `_blur_apply_stamp` (`BlurViewerMixin`), remplacer le masque `ImageDraw.ellipse` par une autre forme.
- **Ajouter un dégradé sur le bord** : changement de philosophie contraire à la décision actée (censure fiable, pas d'effet artistique à bord doux) — à ne pas entreprendre sans confirmation explicite.
- **Ajuster la marge du carré local autour du disque** (actuellement `strength + 2`) : dans `_blur_apply_stamp`, revoir si un futur réglage de puissance dépasse largement la plage actuelle (1-30).
- **Ajouter une persistance de travail non validé par page** (n'existe pas aujourd'hui) : changement structurel qui contredirait la décision actée — à ne pas entreprendre sans confirmation explicite.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md — la barre d'outils/le panneau flottant suivent déjà thème dynamique, retraduction, `OverlayTooltip`.

## Pièges connus

- **Pas de fenêtre/classe dédiée** — vit dans `modules/qt/blur_tool_qt.py` (`BlurCanvasMixin`/`BlurViewerMixin`, hérités par `_ViewerCanvas`/`ImageViewer`).
- **`_blur_work_img` distinct de `entry['bytes']`** — ne pas appliquer chaque point directement sur `entry['bytes']`/`ensure_image_loaded` : recharger/réencoder à chaque point peint tuerait les performances.
- **Undo/redo au niveau du stroke entier, pas du point peint.**
- **Cercle plein sans dégradé — décision assumée**, ne pas "améliorer" en ajoutant un flou de bord sans confirmation explicite (voir intro).
- **Le flou s'accumule volontairement** — repasser sur une zone déjà floutée l'assombrit davantage ; ce n'est pas un bug à corriger.
- **`_VALIDATE_KEYS` n'a pas d'entrée `"blur"`** — ne pas en ajouter une par réflexe en copiant le pattern crop/straighten : cet outil n'a jamais besoin du bouton "Valider" flottant.
- **Aucune section dédiée dans le mode d'emploi.**
- **Pas de point d'entrée depuis la mosaïque.**

## Références croisées

- `clone-zone` — architecture jumelle (peinture continue, commit par stroke, pas de bouton "Valider", image de travail séparée, throttle d'affichage ~30fps) ; principale différence : pas de zone source à définir, masque circulaire sans dégradé assumé (censure), deux réglages indépendants taille/puissance plutôt qu'un seul.
- `viewers` — architecture générale de la barre d'outils flottante (règle des modules séparés, groupe "ajout de contenu").
- `apply-image-operation` — pattern général de modification de `entry['bytes']`.
- `undo-redo` — mécanique de l'historique global de l'appli.
- `macro-tool` — moteur d'enregistrement/lecture de macros, `perform_blur_step` en est un consommateur.
- `comicinfo-metadata-editor` — mise à jour des attributs de page dans `ComicInfo.xml` après un stroke.
- `add-translation` — procédure complète de traduction, néologismes "flou" construits pour tlh/sjn/qya.
- `user-guide` — absence actuelle de section dédiée, à vérifier si une tâche touche à ce fichier.
