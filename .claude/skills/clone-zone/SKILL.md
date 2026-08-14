---
name: clone-zone
description: Localiser ou modifier le tampon de clonage (Ctrl+clic pour définir la zone source, peinture au clic maintenu pour dupliquer des pixels), outil "clone" de la barre d'outils flottante de la visionneuse principale. Utiliser dès qu'une tâche touche à clone_tool_qt.py, CloneCanvasMixin, CloneViewerMixin, _CloneOptionsPanel, ou au bouton/menu "Clonage de zone".
---

# Tampon de clonage — MosaicView

Outil de retouche classique (façon Photoshop/GIMP), intégré directement dans la **visionneuse principale de lecture** (`ImageViewer`, `modules/qt/image_viewer_qt.py`), pas une fenêtre séparée — l'utilisateur définit une **zone source** par Ctrl+clic, puis peint au clic gauche maintenu pour copier des pixels de cette zone source vers l'endroit peint, avec un pinceau circulaire de taille réglable. Usage typique : effacer un artefact de scan, une tache, un texte parasite, en le recouvrant avec une texture voisine plausible.

**Ancienne fenêtre dédiée `CloneZoneViewerDialog`/`clone_zone_viewer_qt.py` entièrement supprimée** (v1.7.3+, 3e outil migré dans la fusion progressive des visionneuses — voir skill `viewers`). Tout le mécanisme (calcul de décalage source/destination, tamponnage PIL, undo/redo par stroke) a été porté à l'identique dans le nouveau module.

Distinct de `page-straighten` (rotation) et `add-text-to-image` (texte superposé) — même chantier de fusion (crop/straighten/clone déjà migrés, texte pas encore) mais **aucun code partagé**, chaque outil ayant son propre module.

## Module dédié — `modules/qt/clone_tool_qt.py`

Tout le mécanisme (état/interactions souris + commit dans l'historique + panneau de réglages + rendu pur) tient dans ce seul module, conformément à la règle CLAUDE.md "ne jamais migrer le code d'un outil dans `image_viewer_qt.py`" :

- **`CloneCanvasMixin`** (hérité par `_ViewerCanvas`, `image_viewer_qt.py`) — état du tampon (`_clone_source_img` en coordonnées **image**, `_clone_marker_widget`/`_clone_live_marker_widget` dérivées pour le dessin), dessin du marqueur cible (`paint_clone_marker`, appelée depuis `_ViewerCanvas.paintEvent`), gestion souris (`clone_mouse_press`/`clone_mouse_move`/`clone_mouse_release`, délégation depuis les handlers réels de `_ViewerCanvas`), curseur en croix (`clone_update_cursor`).
- **`CloneViewerMixin`** (hérité par `ImageViewer`) — peinture effective (`_on_clone_paint_stroke`, `_clone_apply_stamp`), aperçu pendant le stroke (`_clone_refresh_display`), commit final (`_on_clone_paint_end`).
- **`_CloneOptionsPanel`** (`QWidget`) — panneau flottant de réglages (mode source, taille du tampon), affiché sous la barre d'outils quand l'outil est actif.
- **`make_clone_checker`/`make_clone_crosshair_cursor`** — fonctions de rendu pures (damier d'aperçu, curseur en forme de cible), reprises telles quelles de l'ancienne fenêtre.
- **Ce qui reste dans `image_viewer_qt.py`** : rien de spécifique au clonage (contrairement au crop/straighten qui partagent le bouton "Valider" flottant) — voir section suivante, cet outil n'a justement pas besoin de ce bouton.

**Particularité structurelle héritée de l'ancienne fenêtre : pas de navigation entre pages dédiée.** Comme avant, le clonage s'applique à la page actuellement affichée dans la visionneuse — la navigation se fait via les flèches/molette habituelles de `ImageViewer`, pas une liste filtrée séparée.

## Différence fondamentale avec crop/straighten : pas de validation différée

Contrairement au crop/straighten (l'utilisateur trace, ajuste, puis valide une seule fois via un bouton "Valider" ou un double-clic), **le clonage peint en continu** : chaque coup de tampon (stroke, du clic au relâchement) modifie déjà l'image et devient sa propre entrée d'historique dès le relâchement du clic. Accepté explicitement par l'utilisateur que l'historique soit verbeux — *"il correspond à la réalité de ce que fait l'utilisateur"*. Conséquences directes :

- **Pas de bouton "Valider"** pour cet outil — `_VALIDATE_KEYS` (`image_viewer_qt.py`, partagé crop/straighten) n'a pas d'entrée `"clone"`.
- **Pas de persistance de travail non validé par page** — contrairement à `_crop_by_page`/`_straighten_by_page` (dicts définis dans `ImageViewer.__init__`), il n'existe pas de `_clone_by_page` équivalent : chaque stroke est déjà commité (bytes + `save_state()`) à son relâchement, il n'y a donc rien "en attente" à faire survivre à un changement de page. Seule la position de la source Ctrl+cliquée est réinitialisée à chaque changement de page (`navigate()` appelle `canvas.clear_clone_source()`).
- **Pas de variante grise "conservée mais désélectionnée"** (contrairement au rectangle de crop ou au trait de redressage, qui restent affichés en gris quand l'outil est désélectionné) — désélectionner l'outil clone efface simplement la source (`_ViewerToolbar.set_active_tool` appelle `canvas.clear_clone_source()` en quittant l'outil).
- **Aucune contribution à `_has_unvalidated_work()`** (`image_viewer_qt.py`) — fermer la visionneuse ou l'application pendant qu'on est sur l'outil clone ne déclenche jamais l'avertissement "travail non validé", précisément parce qu'il n'y a jamais de travail en attente pour cet outil.
- **Touche Échap** : efface juste la source Ctrl+cliquée (`_on_escape`, `image_viewer_qt.py`) — rien d'autre à annuler.

## Les deux modes de source — "fixe" vs "relative"

Réglables via deux boutons texte checkable dans `_CloneOptionsPanel` (`_radio_fixed`/`_radio_relative`, `set_clone_mode('fixed'|'relative')` sur le canvas). Contrôlent le comportement de la source **entre deux strokes**, pas pendant un même stroke — pendant un stroke en cours, les deux modes utilisent exactement le même calcul de décalage constant (`_get_effective_clone_source`, voir section suivante) :

- **Mode fixe** (défaut) : chaque nouveau stroke repart du **même point source** Ctrl+cliqué à l'origine, quel que soit le nombre de coups de pinceau donnés depuis. Utile pour dupliquer répétitivement le même motif à plusieurs endroits.
- **Mode relatif** : le point source **avance** d'un stroke à l'autre, du même déplacement que celui effectué par le stroke précédent (calculé en fin de stroke dans `_on_clone_paint_end`, `ddx`/`ddy` = déplacement destination cumulé, appliqué à la source). Utile pour "étirer" une texture continue sur une plus grande zone sans que la source ne finisse par chevaucher une zone déjà modifiée par un stroke antérieur.

## Calcul du décalage source/destination — `_get_effective_clone_source` (`CloneCanvasMixin`)

Le cœur géométrique du mécanisme, commun aux deux modes pendant un stroke : au premier point peint d'un stroke, `dx`/`dy` = source initiale moins destination initiale (un décalage constant). Chaque point suivant du même stroke applique ce même décalage à sa propre destination — la source "suit" le pinceau à distance fixe, comme un tampon physique qu'on traînerait sur la page.

## Application d'un coup de pinceau — `_clone_apply_stamp` (`CloneViewerMixin`)

Copie un disque de pixels du "snapshot" vers l'image de travail (`_clone_work_img`), entièrement en PIL natif (crop/paste, pas de boucle Python pixel par pixel) :

1. `r = (brush_radius - 1) / 2` — le réglage utilisateur est un **diamètre** en pixels image, converti en demi-rayon flottant pour le calcul du disque.
2. Bounding box de destination clampée aux limites de l'image (`d_left`/`d_top`/`d_right`/`d_bottom`), bounding box source correspondante calculée par le même décalage, elle aussi clampée si elle déborderait de l'image (`sc_left`/`sc_top`/`sc_right`/`sc_bottom`) — un stroke proche du bord ne plante pas, il copie simplement moins de pixels que le disque complet.
3. **Masque circulaire** (`PIL.ImageDraw.ellipse`) appliqué au collage (`dst.paste(src_crop, ..., mask=mask)`) pour un pinceau rond plutôt qu'un carré — sauf cas particulier `diamètre == 1` (`r == 0.0`) où un seul pixel est collé sans masque, optimisation qui évite de construire un masque 1×1 inutile.
4. Un seul point du snapshot est lu par appel — l'**interpolation** entre deux positions de la souris (mouvement rapide) est gérée en amont, dans `CloneCanvasMixin.clone_mouse_move`, pas ici (voir section suivante).
5. **Piège de type corrigé lors de la migration** : `dest_x`/`dest_y`/`src_x`/`src_y` peuvent être des `float` (coordonnées image dérivées d'une division par le zoom, `_clone_widget_to_image`) — contrairement à l'ancienne `clone_zone_viewer_qt.py` où `_widget_to_image` castait déjà en `int` à la source. `PIL.Image.new`/`crop`/`paste` exigent des entiers ; le cast (`int(round(...))`) se fait au bord de la géométrie source (`s_left`/`s_top`), pas plus tôt — `dest_x`/`dest_y` non arrondis restent utiles pour le calcul du masque circulaire, qui doit rester précis au pixel près. Sans ce cast explicite, `Image.new('L', (pw, ph), 0)` lève `TypeError: 'float' object cannot be interpreted as an integer`.

## Le "snapshot" — pourquoi lire une image figée pendant le stroke

**Point le plus subtil du fichier.** Au tout premier point peint d'un stroke (`_on_clone_paint_stroke`, `if not self._clone_stroke_dirty`), le code décide d'où lire les pixels sources pendant tout le reste du stroke :

- **Mode fixe** : `self._clone_stroke_snapshot = self._clone_work_img.copy()` — une **copie figée** de l'image au tout début du stroke. Sans ce gel, peindre progressivement vers la source finirait par copier des pixels déjà modifiés par ce même stroke (un tampon qui "mange sa propre queue"), produisant un artefact de bavure. La source, en mode fixe, ne bouge jamais pendant un stroke donné, donc la figer une fois au début est correct et évite une copie à chaque point peint.
- **Mode relatif** : `self._clone_stroke_snapshot = self._clone_work_img` — une **référence directe**, pas de copie. La source se déplace *avec* le pinceau à un décalage constant, elle ne peut donc jamais chevaucher la destination courante pendant le même stroke (sauf si l'utilisateur définit volontairement une source très proche de la destination).

Une modification de ce fichier qui changerait la logique de décalage doit impérativement revalider cette hypothèse ("la source ne rattrape jamais la destination en mode relatif") avant de supprimer la copie du mode fixe ou de la réutiliser telle quelle pour le mode relatif.

## `_clone_work_img` — image de travail séparée de `entry['bytes']`

Contrairement au crop/straighten (qui lisent `ensure_image_loaded(entry)` et appliquent leur opération en une fois), le clonage a besoin d'une image de travail intermédiaire (`self._clone_work_img`, copie PIL RGBA) qui existe **seulement pendant un stroke** :
- Chargée depuis `entry['bytes']` au tout premier point du stroke (`_on_clone_paint_stroke`).
- Modifiée en place à chaque point peint (`_clone_apply_stamp`).
- Affichée directement via `_clone_refresh_display()` — **sans repasser par `entry['bytes']`/`ensure_image_loaded`/`display_image()`**, recharger et réencoder l'image à chaque frame serait beaucoup trop coûteux pour un simple aperçu.
- Commitée dans `entry['bytes']` (via `save_image_to_bytes`) seulement au relâchement du clic (`_on_clone_paint_end`), puis remise à `None`.

**`_orig_mode`** (mode PIL d'origine avant conversion RGBA, pour aplatir correctement sur blanc en sortie si le format ne supporte pas l'alpha) posé une seule fois sur `entry` au premier stroke si absent — ne jamais utiliser un défaut `'RGBA'` arbitraire, une image sans alpha d'origine (JPEG) doit rester aplatie en sortie.

## Interpolation pendant un mouvement rapide — `CloneCanvasMixin.clone_mouse_move`

Si la souris se déplace plus vite qu'un pas de `max(1, zoom * brush_radius * 0.5)` pixels widget entre deux événements de mouvement, le code interpole plusieurs points intermédiaires (`steps = dist / step`) et appelle `_on_clone_paint_stroke` pour chacun — sans ça, un mouvement rapide laisserait des trous non peints entre deux positions successives de la souris (le pinceau "sauterait" plutôt que de tracer un trait continu).

## Throttle d'affichage — `_clone_display_timer`, ~30 fps

`_on_clone_paint_stroke` ne rafraîchit le pixmap affiché (`_clone_refresh_display`) que si au moins 33 ms se sont écoulées depuis le dernier rafraîchissement (`QElapsedTimer`) — l'image de travail PIL, elle, est modifiée à **chaque** point peint sans throttle (le tamponnage réel n'est jamais retardé, seul l'affichage l'est). `_on_clone_paint_end` appelle `_clone_refresh_display()` une dernière fois en début de commit pour rattraper un point que le throttle aurait sauté. Sans ce throttle, un stroke rapide avec un gros pinceau recomposerait le damier de transparence et reconvertirait toute l'image en `QPixmap` à chaque point peint individuel, bien plus vite que l'écran ne peut afficher — coûteux pour un gain visuel nul.

## Undo/redo — un stroke entier, unifié avec le panneau

**Changement majeur par rapport à l'ancienne fenêtre** : il n'y a plus qu'**un seul** mécanisme d'historique, celui du panneau (`callbacks['save_state']`, skill `undo-redo`), piloté par les boutons Undo/Redo de `_ViewerToolbar` (partagés avec crop et straighten). `_on_clone_paint_end()` fait `save_state()` avant de committer les bytes, puis `save_state(force=True)` après — même pattern que `perform_crop()`/`perform_straighten()`. L'ancienne `CloneZoneViewerDialog` empilait deux systèmes (historique global de l'appli + `self._bytes_history`/`self._redo_stack` internes à la fenêtre) — ce second système a disparu avec la fenêtre elle-même.

L'unité d'undo reste **le stroke entier** (tout un coup de pinceau, du clic au relâchement), pas chaque point peint individuellement — `_clone_stroke_dirty` n'est posé qu'une fois par stroke, et `_on_clone_paint_end` ne committe qu'au relâchement.

**Piège pour toute nouvelle opération de cet outil** : `self._toolbar.refresh_undo_redo_state()` doit être rappelée après tout nouveau `save_state()`, sinon les icônes Undo/Redo de la barre restent dans un état obsolète (déjà fait dans `_on_clone_paint_end`).

## Points d'entrée UI

**Un seul point d'entrée depuis le 2026-08-14** : directement dans la visionneuse principale déjà ouverte, en sélectionnant l'icône Clonage dans la barre d'outils flottante. Il n'existe plus de point d'entrée dédié depuis la mosaïque qui ouvrirait directement la visionneuse avec cet outil présélectionné.

**Nettoyage du 2026-08-14** (`idees.txt` #3, "NETTOYAGE DES COMMANDES REDONDANTES") : le menu contextuel (`context_menu.image.clone_zone`), l'entrée équivalente de la barre de menu, le bouton `"clone_zone"` de la colonne d'icônes (`icon_toolbar_qt.py`) et son tooltip `tooltip.clone_zone`, ainsi que la méthode `PanelWidget._clone_selected_image()` (`panel_widget.py`, callback `"show_clone_zone_viewer"`) qui les orchestrait — tous supprimés. Ce nettoyage est intervenu après celui de `page-crop`/`page-straighten` (même mécanique, mêmes trois points d'entrée retirés).

## Zoom, pan, plein écran

Vocabulaire commun aux visionneuses du projet (skill `viewers`) : `Ctrl++`/`Ctrl+-`, `Ctrl+0` (fit), `Ctrl+1` (reset 100%), `F11` (plein écran), molette, clic droit maintenu (pan) — toujours actif quel que soit l'outil sélectionné, **y compris pendant l'utilisation du clonage** : le clic droit reste réservé au pan, jamais réquisitionné par l'outil (contrairement à Ctrl+clic, qui définit la source — ce n'est pas un clic droit, donc pas de conflit).

Particularité propre à cet outil : le curseur en forme de **cible** (`make_clone_crosshair_cursor`) est reconstruit dynamiquement à chaque survol avec Ctrl enfoncé (`clone_update_cursor`), pas seulement à la première fois — le rayon écran dépend du zoom courant, qui peut avoir changé (molette, Ctrl+0/1/+/-) depuis la dernière construction. Un curseur non reconstruit après un changement de zoom afficherait une taille de pinceau trompeuse par rapport à la zone réellement peinte.

Le marqueur visuel de la source (`_clone_marker_widget`, dérivé de `_clone_source_img` en coordonnées image via `_clone_image_to_widget`) est resynchronisé systématiquement en tête de `paint_clone_marker` (appelée depuis `_ViewerCanvas.paintEvent`, donc à chaque pan/zoom/redimensionnement puisque Qt réinvoque `paintEvent` dans les trois cas) — voir skill `viewers`, section "Piège transversal — overlays interactifs qui se désynchronisent de l'image au pan/zoom/resize".

## Fond damier (transparence)

`make_clone_checker`/`_clone_refresh_display` — implémentation **indépendante** (encore une copie du même algorithme après celle de `AdjustmentViewerDialog`) ; aucune des deux n'appelle une fonction partagée. Une correction visuelle du damier faite dans un fichier ne se propage pas à l'autre. Distinct de `_compose_on_checkerboard`/`entries.py::_make_checkerboard_pil` (affichage normal hors stroke) — tuile plus fine (12px) et composition RGB directe sans repasser par le pipeline d'affichage standard.

## Traductions

`locales/fr.json`, section `clone_zone_viewer` : `title` (non résolue nulle part depuis la suppression de l'ancienne `QDialog` — orpheline mais pas retirée, à vérifier si elle est encore utilisée avant de la supprimer), `instruction` (réutilisée en tooltip enrichi de l'icône Clonage de la barre d'outils, voir skill `viewers`), `mode_label`/`mode_fixed`/`mode_relative`, `brush_size_label` — toutes réutilisées telles quelles par `_CloneOptionsPanel`. Clés v1.7.3+ : `viewer.toolbar_clone_tooltip`, `messages.errors.clone_failed.title`/`.message` — propagées aux 45 langues (39 naturelles + tlh/sjn/qya latin + 3 CSUR), calquées sur le vocabulaire déjà attesté pour "clonage" dans `dialogs.clone_zone_viewer` de chaque fichier fictif (tlh `tIngmeH`, sjn `Glawar`, qya `Lúmequenta`) plutôt qu'improvisées. Voir skill `add-translation`.

**Clés mortes retirées le 2026-08-14** (ancien point d'entrée mosaïque supprimé) : `context_menu.image.clone_zone`, `tooltip.clone_zone`.

**Absent du mode d'emploi** (`user_guide_qt.py`) — même situation que `page-straighten` et `add-text-to-image`, ces visionneuses/outils d'édition d'image partagent ce manque (skill `user-guide`).

## Comment étendre

- **Changer la forme du pinceau** (actuellement toujours circulaire) : uniquement `_clone_apply_stamp` (`CloneViewerMixin`), remplacer le masque `ImageDraw.ellipse` par une autre forme — attention à conserver le cas particulier `diamètre == 1` qui contourne le masque.
- **Ajouter un troisième mode de source** : suivre le pattern de `_get_effective_clone_source`/la bascule `if canvas._clone_mode == 'fixed': ... else: ...` dans `_on_clone_paint_end` — les deux points d'extension sont `_get_effective_clone_source` (calcul pendant un stroke) et la fin de `_on_clone_paint_end` (mise à jour de la source entre deux strokes).
- **Ajuster la fréquence du throttle d'affichage** (actuellement ~30 fps) : la constante `33` (ms) est en dur dans `_on_clone_paint_stroke`, à extraire en attribut de classe si elle doit devenir configurable.
- **Ajouter une persistance de travail non validé par page** (n'existe pas aujourd'hui, contrairement au crop/straighten) : changement structurel qui contredirait la décision actée ("chaque stroke est déjà commité, rien à faire survivre") — à ne pas entreprendre sans confirmation explicite, ce serait un changement de philosophie de l'outil, pas juste une extension.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md — la barre d'outils/le panneau flottant suivent déjà thème dynamique, retraduction, `OverlayTooltip`.

## Pièges connus

- **Pas de fenêtre/classe dédiée** — contrairement à `add-text-to-image`, le clonage vit dans `modules/qt/clone_tool_qt.py` (`CloneCanvasMixin`/`CloneViewerMixin`, hérités par `_ViewerCanvas`/`ImageViewer`), partagé avec toute la logique d'affichage/zoom/pan/pagination de la visionneuse principale.
- **Coordonnées `float` non castées avant construction du masque PIL** — bug rencontré et corrigé lors de la migration (`TypeError: 'float' object cannot be interpreted as an integer` dans `Image.new`), voir section `_clone_apply_stamp` ; caster `s_left`/`s_top` en `int(round(...))`, jamais plus tôt (`dest_x`/`dest_y` doivent rester précis pour le masque circulaire).
- **Snapshot figé en mode fixe, référence directe en mode relatif** — une modification de la logique de décalage doit revalider que "la source ne rattrape jamais la destination en mode relatif" avant de réutiliser la référence directe ailleurs ; voir section dédiée.
- **`_clone_work_img` distinct de `entry['bytes']`** — ne pas essayer de "simplifier" en appliquant chaque point directement sur `entry['bytes']`/`ensure_image_loaded` : recharger/réencoder à chaque point peint tuerait les performances (voir section dédiée).
- **Undo/redo au niveau du stroke entier, pas du point peint** — un stroke long (glisser la souris longtemps sans relâcher) ne peut être annulé qu'en un seul bloc, jamais point par point.
- **Fond damier dupliqué** — pas partagé avec `AdjustmentViewerDialog`.
- **Curseur cible à reconstruire à chaque survol Ctrl enfoncé** — pas seulement à la première fois, le rayon écran dépend du zoom courant.
- **Marqueur de source à resynchroniser après pan, zoom, ET redimensionnement** — géré en tête de `paint_clone_marker`, appelée automatiquement par Qt via `paintEvent` dans les trois cas ; ne pas dupliquer l'appel dans chaque handler séparément.
- **`_VALIDATE_KEYS` n'a pas d'entrée `"clone"`** — ne pas en ajouter une par réflexe en copiant le pattern crop/straighten : cet outil n'a jamais besoin du bouton "Valider" flottant, l'application est immédiate.
- **Aucune section dédiée dans le mode d'emploi.**
- **Plus de point d'entrée depuis la mosaïque** (menu contextuel, barre de menu, colonne d'icônes) depuis le 2026-08-14 — ne pas chercher `PanelWidget._clone_selected_image()`, elle a été supprimée avec ses 3 points d'appel ; le clonage ne s'atteint plus que depuis l'intérieur de la visionneuse déjà ouverte.

## Références croisées

- `page-straighten` — architecture la plus proche dans le projet (outil migré dans son propre module, undo/redo unifié avec le panneau, plus de fenêtre dédiée) ; comparer les deux pour la différence de philosophie de validation (une opération validée une fois vs peinture continue commitée en continu).
- `page-crop` — même famille d'outils migrés, partage le bouton "Valider" flottant avec `page-straighten` mais pas avec le clonage (qui n'en a pas besoin) ; même nettoyage des points d'entrée mosaïque le 2026-08-14.
- `add-text-to-image` — dernier outil migré ; troisième implémentation indépendante du fond damier de transparence après celle-ci et celle de `AdjustmentViewerDialog`.
- `viewers` — architecture générale de la fusion progressive des visionneuses (barre d'outils, règle des modules séparés, sections spécifiques au clonage) ; vocabulaire zoom/pan/plein-écran commun mais implémentation non partagée.
- `apply-image-operation` — pattern général de modification de `entry['bytes']`, suivi ici en variante (A) complète.
- `undo-redo` — mécanique de l'historique global de l'appli, unique niveau depuis la migration (plus d'historique interne séparé).
- `comicinfo-metadata-editor` — mise à jour des attributs de page dans `ComicInfo.xml` après un stroke.
- `add-translation` — procédure complète de traduction, vocabulaire fictif "clonage" déjà établi (tlh `tIngmeH`, sjn `Glawar`, qya `Lúmequenta`) et réutilisé pour les nouvelles clés de la barre d'outils.
- `user-guide` — absence actuelle de section dédiée, à vérifier si une tâche touche à ce fichier.
