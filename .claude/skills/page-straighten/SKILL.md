---
name: page-straighten
description: Localiser ou modifier le redressement d'image, manuel (tracé d'une ligne de référence, intégré à la visionneuse principale, seul point d'accès) ou automatique (détection d'inclinaison par transformée de Hough, deskew.py/deskew_qt.py — accessible en lot depuis la colonne d'icônes/menus, ou unitairement depuis la barre d'outils de la visionneuse via l'icône bi-mode "straighten", clic droit pour basculer manuel/auto). Utiliser dès qu'une tâche touche à _ViewerCanvas (outil "straighten"), ImageViewer.perform_straighten, ImageViewer.perform_auto_straighten, deskew_entry_data, ou au bouton/menu "Redresser l'image".
---

# Redressement d'image — MosaicView

Deux mécanismes **complémentaires**, ni l'un ni l'autre ne remplace l'autre :

1. **Redressement manuel** — l'utilisateur trace un **trait de référence** sur une image (bordure de case, ligne d'horizon...) directement dans la **visionneuse principale de lecture** (`ImageViewer`, `modules/qt/image_viewer_qt.py`), outil `"straighten"` de sa barre d'outils flottante. Le code interprète l'orientation du trait, en déduit l'angle de correction, applique une rotation libre. **La visionneuse est le SEUL point d'accès au mode manuel** : aucune commande dédiée dans le menu Images/menu contextuel, et l'icône de la colonne verticale n'est pas bi-mode (voir section dédiée plus bas) — le mode manuel ne se sélectionne que via l'icône straighten de la barre d'outils flottante de la visionneuse elle-même.
2. **Redressement automatique (deskew)** — détecte l'inclinaison **sans intervention** de l'utilisateur via la transformée de Hough (OpenCV). Reste fondamentalement une fonction batch séparée (`deskew_selected_qt`, sur la sélection courante de la mosaïque, sans fenêtre de prévisualisation), accessible depuis la colonne d'icônes (bouton mono-fonction), le menu Images, et le menu contextuel — **mais l'icône Redressage de la barre d'outils de la visionneuse est en plus bi-mode** (clic droit bascule manuel/auto) : en mode auto, un clic gauche sur l'icône lance `ImageViewer.perform_auto_straighten()`, qui applique le même algorithme de détection à la **seule page actuellement affichée** dans la visionneuse — une troisième voie d'accès, ni tout à fait "batch" ni "manuel avec trait", voir section dédiée plus bas.

Distinct de la rotation 90°/miroir de la mosaïque (voir skill `rotate-flip`) — **aucun code partagé**, malgré le mot "rotation" en commun. Le redressement (manuel ou auto) corrige une **inclinaison involontaire** avec un angle fin arbitraire ; `rotate-flip` fait pivoter une image **volontairement** de 90° exacts.

## Colonne d'icônes — mono-fonction automatique

**L'icône `straighten` de la colonne d'icônes n'est pas bi-mode** : elle ne déclenche que le redressement **automatique en lot** (`deskew_selected_qt(...)` sur la sélection courante de la mosaïque), au même titre que ses homologues du menu Images et du menu contextuel. Le clic droit sur cette icône n'a pas d'effet spécial (pas de bascule de mode) ; son tooltip est fixe (`tooltip.straighten` = "Redresser l'image automatiquement"). Le callback `"straighten"` de la colonne d'icônes (`icon_toolbar_qt.py`) pointe directement vers le même callback que `deskew_selected` du menu.

**`state.straighten_mode` reste utilisé, mais uniquement par l'icône bi-mode straighten interne à la barre d'outils de la visionneuse** (voir section dédiée plus bas) — `PanelWidget._set_straighten_mode()` persiste ce mode. La colonne d'icônes n'a pas de bi-mode équivalent.

**Activation** : `has_selected_images()` (`_ACTIVATION_RULES["straighten"]`, `icon_toolbar_qt.py`).

## Redressement manuel — module dédié `modules/qt/straighten_tool_qt.py`

Pas de fichier ni de classe dédiés à une fenêtre séparée : le trait de référence, son calcul d'angle, son application et sa persistance vivent dans `StraightenCanvasMixin`/`StraightenViewerMixin` (`straighten_tool_qt.py`), hérités par `_ViewerCanvas`/`ImageViewer` (`image_viewer_qt.py`) — exactement comme le recadrage (skill `page-crop`, `crop_tool_qt.py`). Voir skill `viewers`, section "Barre d'outils flottante" et "Architecture en modules séparés", pour l'architecture générale de la barre d'outils partagée entre crop/straighten/clone, et pour la règle CLAUDE.md imposant cette séparation en modules (le code de l'outil ne doit jamais vivre dans `image_viewer_qt.py`).

- **`StraightenCanvasMixin`** (`straighten_tool_qt.py`, hérité par `_ViewerCanvas`) — état du trait (`_line_start`/`_line_end` en coordonnées **widget**, `_line_img_start`/`_line_img_end` en coordonnées **image** stables), dessin (`paint_straighten_line`, appelée depuis `_ViewerCanvas.paintEvent`), détection des poignées, gestion souris (`straighten_mouse_press`/`straighten_mouse_move`/`straighten_mouse_release`, délégation depuis les handlers réels de `_ViewerCanvas`), gatée par `active_tool == "straighten"`.
- **`StraightenViewerMixin`** (`straighten_tool_qt.py`, hérité par `ImageViewer`) — `validate_straighten()`/`perform_straighten()` (validation et application réelle de la rotation en pixels PIL), `perform_auto_straighten()`, `_save_straighten_for_current_page()`/`_restore_straighten_for_page()`.
- **`_StraightenAnglePanel`** (`straighten_tool_qt.py`) — panneau flottant contenant la spinbox d'angle, visible sous la barre d'outils (`viewer_toolbar_qt.py::_ViewerToolbar`) quand l'outil est actif et qu'un trait existe.
- **`modules/qt/straighten_geometry.py`** — module module-level séparé, sans dépendance Qt, contenant `line_to_correction()` (calcul d'angle), importé par `straighten_tool_qt.py`.
- **Ce qui reste dans `image_viewer_qt.py`** : le bouton "Valider" flottant (partagé avec crop/texte/formes/transparency, `_VALIDATE_KEYS`, TOUJOURS VISIBLE tant que l'outil straighten est actif — voir skill `viewers`), `_straighten_by_page` (dict de persistance, défini dans `ImageViewer.__init__`) — voir skill `viewers` pour la liste complète des points de couplage transversaux qui ne peuvent pas appartenir à un seul module d'outil.

### Le canvas — tracé et édition du trait

Le trait de référence est stocké à **deux niveaux** (`_ViewerCanvas._sync_line_from_image`) :
- `_line_img_start`/`_line_img_end` — coordonnées **image**, stables, figées à chaque fin de tracé/déplacement de poignée dans `_notify_line_drawn()` via `_line_widget_to_image`. C'est la source de vérité persistante.
- `_line_start`/`_line_end` — coordonnées **widget** (`QPoint`), dérivées des précédentes via `_line_image_to_widget` (l'inverse), utilisées uniquement pour le dessin (`paintEvent`) et la détection de poignée (`_hit_line_handle`). Recalculées par `_sync_line_from_image()`, appelée en tête de `paintEvent` (donc à chaque pan, zoom, **et** redimensionnement de la fenêtre puisque Qt réinvoque `paintEvent` dans les trois cas) — **sauf** pendant un tracé ou un drag de poignée en cours (`if not self._line_drawing and self._line_dragging_handle is None:`), sinon la position "live" pilotée par la souris serait écrasée par l'ancienne position figée avant même le dessin.

Conversion image↔widget partagée avec le calcul du crop (`self._viewer.zoom_level`, `self.display_offset_x/y`) — cohérent avec le fait que le trait est dessiné sur le même canvas que l'image affichée en lecture, pas dans un widget d'aperçu séparé.

- **Premier tracé** : clic-gauche (avec `active_tool == "straighten"`) + glisser dessine un trait rouge de 2px entre le point de départ et le point courant. Gaté symétriquement au crop dans `mousePressEvent`/`mouseMoveEvent`/`mouseReleaseEvent` (branche `active_tool == "straighten"` en tête de chaque handler, avant la branche crop).
- **Poignées de réglage** : chaque extrémité du trait affiche un cercle rouge à contour blanc (`_LINE_HANDLE_RADIUS = 7`, zone de clic élargie `_LINE_HANDLE_HIT = 12`). Un clic sur une poignée existante permet de la redéplacer (`_line_dragging_handle`) au lieu de retracer un nouveau trait.
- À chaque relâchement de clic gauche (fin de tracé **ou** fin de déplacement d'une poignée), `_notify_line_drawn()` fige `_line_img_start`/`_line_img_end`, force le retour en mode simple page si un mode double/continu/webtoon était actif (même règle que le crop), puis appelle `ImageViewer._on_straighten_line_drawn()` qui calcule l'angle et alimente le panneau flottant.
- **Pendant** un tracé initial ou un drag de poignée (avant relâchement), `mouseMoveEvent` appelle `_notify_line_live()` à chaque mouvement — calcule l'angle à la volée depuis les coordonnées widget courantes (sans toucher `_line_img_*`) et appelle `ImageViewer._on_straighten_line_live()`, qui alimente la spinbox du panneau flottant en continu.
- Changer de page sans trait sauvegardé (`display_image()` sans `keep_crop_rect=True`) appelle `clear_line()` **et** `_StraightenAnglePanel.reset()` — le trait ne survit à un changement de page que via le mécanisme de persistance dédié (voir plus bas), jamais par accident.

### Calcul de l'angle — `line_to_correction()` (`modules/qt/straighten_geometry.py`, fonction module-level)

Fonction pure (aucun import Qt), importée par `straighten_tool_qt.py` (utilisée par `_StraightenAnglePanel` et, sous l'alias `_line_to_correction`, dans `perform_straighten`). Retourne `(correction_deg, category, vertical_sign)` — `category` vaut `'h'` ou `'v'`, `vertical_sign` (+1/-1) n'a de sens que si `category == 'v'` (mémorise quelle branche de la réduction verticale a été prise, nécessaire pour l'inverser proprement depuis la spinbox — voir plus bas).

1. `dx`/`dy` = vecteur du trait en coordonnées image (`ix2-ix1`, `iy2-iy1`). Retourne `(None, None, None)` si le trait est nul (`dx == dy == 0`).
2. `angle_deg = math.degrees(math.atan2(dy, dx))`, normalisé dans `[-90, 90]` (un trait et son symétrique à 180° doivent donner la même correction).
3. **Décision horizontale vs verticale** : `abs_angle <= 45` → le trait est interprété comme approximativement **horizontal**, correction = `angle_deg` tel quel (`category='h'`, `vertical_sign=None`). Sinon → interprété comme approximativement **vertical**, correction = `angle_deg - 90` (si `angle_deg >= 0`, `vertical_sign=1`) ou `angle_deg + 90` (sinon, `vertical_sign=-1`) — ramène l'angle à "l'écart par rapport à la verticale" au lieu de "l'écart par rapport à l'horizontale". **Même convention réutilisée telle quelle dans `deskew.py`** pour normaliser les angles de segments détectés par Hough — voir section dédiée plus bas.
4. **Piège de signe** : `PIL.rotate()` tourne en anti-horaire pour un angle positif, mais l'axe Y à l'écran pointe vers le bas (sens anti-mathématique) — la formule ci-dessus compense déjà ce décalage, ne **pas** ajouter un signe `-` supplémentaire par réflexe sans retester au clavier/souris que le sens de rotation reste correct après modification.
5. Le viewer stocke le résultat dans `_StraightenAnglePanel.pending_angle` (pas appliqué tout de suite) plus `_angle_category`/`_angle_vertical_sign` (pour la spinbox) ; le bouton "Valider" flottant n'est actionnable que si un trait existe (voir `perform_straighten`, qui refuse tout angle `abs(angle) < 0.001`).

**Aucun garde-fou sur un trait quasi vertical/horizontal à exactement 45°** — la bascule horizontale/verticale est une frontière dure (`<=45` vs `>45`), pas de zone tampon ; comportement existant, pas un bug à corriger sans consigne explicite.

### Panneau flottant de la spinbox d'angle — `_StraightenAnglePanel`

Positionné sous `_ViewerToolbar` (`y = 8 + toolbar.height() + 6`), un `QLabel` (clé `dialogs.straighten_viewer.angle_label`) + un `QDoubleSpinBox` (plage `[-90, 90]`, 2 décimales, pas `0.1°`, suffixe `°`). Visible uniquement quand `active_tool == "straighten"` **et** qu'un trait existe (`set_visible_for_tool`) — **indépendant du timer d'auto-masquage de `_ViewerToolbar`**, il ne se masque pas quand la barre du haut se masque après 3s d'inactivité, pour ne pas interrompre un réglage en cours.

- **Affichage en continu** : `on_line_live()` (appelée depuis `ImageViewer._on_straighten_line_live`, elle-même déclenchée par `_ViewerCanvas._notify_line_live` à chaque `mouseMoveEvent` pendant tracé/drag) met à jour la spinbox via `_set_spin_value()`, qui encadre `setValue` d'un `blockSignals(True)/(False)` pour ne pas redéclencher `_on_spin_changed` en boucle. `on_line_drawn()` (relâchement) fait de même.
- **Édition manuelle** (clavier ou boutons +/-) : `_on_spin_changed(value)` lit `_angle_category`/`_angle_vertical_sign` (mémorisés au dernier tracé/drag) et appelle `_ViewerCanvas.set_line_end_from_angle(value, category, vertical_sign)` — **le premier point (`_line_start`) reste fixe, seul le second (`_line_end`) est redéplacé** autour de lui, à longueur de trait inchangée. La formule inverse la réduction horizontale/verticale de `line_to_correction` : `angle_deg = correction_deg` si `category == 'h'`, sinon `correction_deg + 90` ou `correction_deg - 90` selon `vertical_sign`. **`set_line_end_from_angle` fige directement `_line_img_start`/`_line_img_end` SANS repasser par `_notify_line_drawn()`** — pendant l'édition via la spinbox, celle-ci est la seule source de vérité de sa propre valeur ; repasser par le recalcul géométrique (arrondi aux pixels) la ferait osciller autour de la valeur saisie.
- **Réinitialisation** : `reset()` (vide `_angle_category`/`_angle_vertical_sign`/`pending_angle`, remet la spinbox à `0.0`, masque le panneau) appelée partout où `clear_line()` est appelé côté canvas — changement de page sans restauration, après `perform_straighten()`, `_on_escape`.
- **Style `QDoubleSpinBox`** : `QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 16px; }` explicite dans le stylesheet (sinon boutons +/- invisibles bien que fonctionnels — voir `split_dialog_qt.py` comme référence du pattern correct).
- **Curseur** : `straighten_update_cursor` pose un curseur spécifique sur le **canvas** (`SizeAllCursor` sur une poignée, `CrossCursor` sinon). `enterEvent`/`_check_really_left` du panneau le réinitialisent (`setCursor(Qt.ArrowCursor)`/`unsetCursor()`), sinon il resterait affiché par-dessus la spinbox au survol — voir skill `viewers`, section "Piège transversal — le curseur spécifique d'un outil reste affiché par-dessus son propre panneau flottant".

Traduction : `dialogs.straighten_viewer.angle_label`/`dialogs.straighten_viewer.instruction` réutilisées en tooltip enrichi de l'icône Redressage de la barre d'outils (`viewer.toolbar_straighten_tooltip` en gras + instruction sur une seconde ligne, via `OverlayTooltip`) — voir skill `viewers`.

### Application — `ImageViewer.perform_straighten()` (`straighten_tool_qt.py::StraightenViewerMixin`)

Pas de worker QThread : tout se fait en synchrone dans le thread UI (l'opération est rapide, une seule image à la fois, pas de traitement par lot).

1. `save_state()` (undo global de l'appli, **sans** `force=True`) avant modification — aligné sur le pattern de `perform_crop()`.
2. `img.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)` — **`BICUBIC`**, contrairement à `rotate_entry_data` (skill `rotate-flip`) qui n'impose aucun `resample` explicite (défaut PIL = `NEAREST` pour les angles non multiples de 90°). **Pas de `fillcolor`** ici (contrairement au deskew automatique, voir plus bas) — les coins vides après rotation restent transparents/noirs selon le mode de l'image.
3. Invalidation complète des caches — **variante (A)** du skill `apply-image-operation` (`entry['img'] = None` après réassignation, plus `_thumbnail`/`large_thumb_pil`/`qt_pixmap_large`/`qt_qimage_large`/`_hash`), contrairement à l'invalidation partielle de `rotate_entry_data`/`flip_entry_data`.
4. Synchronisation `ComicInfo.xml` (`update_page_entries_in_xml_data`, skill `comicinfo-metadata-editor`) — les dimensions changent après une rotation à angle libre avec `expand=True`.
5. `state.modified = True`, puis **second `save_state()`, également sans `force=True`** — symétrique au premier appel.
6. Précalcul explicite de la vignette Qt (`build_qimage_for_entry`) avant `canvas.refresh_thumbnail(real_idx)`, comme `perform_crop()`.
7. Le trait est effacé (`clear_line()`), le panneau d'angle réinitialisé, `self._straighten_by_page.pop(self.current_idx, None)`, l'image réaffichée, `self._toolbar.refresh_undo_redo_state()` rafraîchi.

### Undo/redo — unifié avec le panneau, pas d'historique interne séparé

Un seul mécanisme d'historique, celui du panneau (`callbacks['save_state']`, voir skill `undo-redo`), piloté par les boutons Undo/Redo de `_ViewerToolbar` (partagés avec le crop). **Aucun historique local séparé** — un bug de undo/redo signalé sur le redressement se diagnostique exactement comme pour n'importe quelle autre opération d'édition (skill `undo-redo`), pas de mécanisme à part à soupçonner.

### Persistance du trait entre pages — `ImageViewer._straighten_by_page`

`dict[int, tuple[tuple[float,float], tuple[float,float]]]`, clé = index de page, valeur = `(_line_img_start, _line_img_end)` en coordonnées **image absolues** (pas relatives 0-1 comme le crop — différence à noter si on compare les deux mécanismes). `navigate()` appelle `_save_straighten_for_current_page()` avant de changer `current_idx` puis `_restore_straighten_for_page(idx)` juste après. L'entrée est retirée du dict quand le trait est validé (`perform_straighten()`) ou annulé (`_on_escape`). Même limitation que le crop : un trait restauré sur une page qui s'affiche en mode double page n'est pas redessiné (`_display_double_page` ne gère aucun overlay).

### Sélection de la page de départ

Le mode manuel n'a pas de point d'entrée dédié qui ouvrirait la visionneuse directement sur l'outil straighten : on l'atteint en ouvrant la visionneuse normalement (double-clic sur une vignette) puis en sélectionnant l'icône `straighten` dans sa barre d'outils. C'est simplement la page affichée au moment où l'outil est activé. La navigation entre pages reste celle, commune, de la visionneuse principale (flèches/molette), sur toutes les pages de la mosaïque (y compris non-images, simplement ignorées par `get_image_indices()`).

### Zoom, pan, plein écran

Vocabulaire commun aux visionneuses du projet (voir skill `viewers`) : `Ctrl++`/`Ctrl+-` (zoom), `Ctrl+0` (fit to window), `Ctrl+1` (reset 100%), `F11` (plein écran), molette (zoom), clic droit maintenu (pan) — toujours actif quel que soit l'outil sélectionné dans la barre, y compris pendant un tracé de trait.

## Icône Redressage de la barre d'outils — bi-mode manuel/auto

L'icône `"straighten"` de `_ViewerToolbar` (`modules/qt/viewer_toolbar_qt.py`) n'est pas un simple `_ToolButton` mono-fonction : elle pilote `state.straighten_mode` (0=manuel/1=auto) en interne à la visionneuse. **C'est le seul bi-mode straighten de l'application** — la colonne d'icônes, elle, est mono-fonction (voir plus haut) et ne fait que déclencher l'automatique en lot, sans lire ni écrire `state.straighten_mode` autrement que pour rester synchronisée en affichage (tooltip fixe, pas de bascule).

- **Clic droit** (`_ViewerToolbar._on_tool_right_clicked`, gaté sur `btn._tool_id == "straighten"`) : bascule `state.straighten_mode` directement sur l'objet `state` (lu via `self._viewer.callbacks.get('state')`), puis appelle le callback `set_straighten_mode` (voir plus bas) pour la persistance disque. Si le nouveau mode est auto (1) et qu'un trait manuel était en cours sur la page affichée, il est effacé (`clear_line()`, `_straighten_by_page.pop(...)`, `_angle_panel.reset()`). **Le clic droit sélectionne ensuite systématiquement l'icône straighten** (`set_active_tool("straighten")`, appelé par `_on_tool_right_clicked` après la bascule, règle générale de la barre — voir skill `viewers`) — y compris en mode auto, où l'icône devient active sans qu'aucun geste de tracé ne soit possible, et sans déclencher `perform_auto_straighten()` (contrairement au clic gauche, voir ci-dessous).
- **Clic gauche** (`_ViewerToolbar._on_tool_clicked`) : en mode manuel (0), comportement inchangé (sélectionne/désélectionne l'outil de tracé). **En mode auto (1)**, ne bascule pas en outil de tracé (rien à tracer en auto) — sélectionne d'abord l'icône straighten (`set_active_tool("straighten")`, même effet visuel que le clic droit ci-dessus, pour qu'elle apparaisse comme l'outil actif au lieu de laisser l'outil précédemment sélectionné le paraître) puis appelle directement `ImageViewer.perform_auto_straighten()` sur la page actuellement affichée.
- **`ImageViewer.perform_auto_straighten()`** (`straighten_tool_qt.py::StraightenViewerMixin`) : appelle `detect_skew_angle(entry)` (`deskew.py`, la même fonction pure que le batch — voir plus bas) **avant** tout `save_state()` ; si `None`/angle `< 0.001`, affiche `MsgDialog` avec `messages.warnings.no_skew_detected` sans créer de point d'historique. Sinon `save_state()`, `deskew_entry_data(entry, state)` (même fonction unitaire que le worker batch, réutilisée telle quelle), `save_state()` à nouveau (symétrique, sans `force=True`, même pattern que `perform_straighten`/`perform_crop`), rafraîchit la vignette et `self._toolbar.refresh_undo_redo_state()`. Synchrone (une seule image, pas de worker QThread) — à la différence du batch qui a son propre `_DeskewWorker`.
- **Tooltip dynamique** (`_ViewerToolbar._update_straighten_tooltip`) : préfixe systématiquement `<b>Redressage</b>` (clé `viewer.toolbar_straighten_tooltip`) en gras, suivi de `tooltip.straighten_mode_0`/`straighten_mode_1` sur une seconde ligne — sans ce préfixe, rien n'indique quelle fonction appelle l'icône tant qu'on n'a pas cliqué. Recalculé à chaque `retranslate()` (construction, changement de langue/thème) et à chaque bascule de mode.
- **Callback `set_straighten_mode`** (`PanelWidget._set_straighten_mode`, `panel_widget.py`) : persiste le mode sans le re-basculer (déjà fait côté `_ViewerToolbar` avant l'appel) — `_renumber_config().set_straighten_mode(mode)` + `_refresh_toolbar_states()`. Seule méthode de persistance pour `state.straighten_mode`.

### Piège transversal — clic droit sur une icône de la barre d'outils ne doit jamais ouvrir le menu contextuel du canvas

`_ToolButton`/`_ActionButton`/`_ViewerToolbar` sont des widgets flottants enfants de `_ViewerCanvas`. Leurs `mousePressEvent`/`mouseReleaseEvent` doivent tous deux `event.accept()` : sans ça, le `mouseReleaseEvent` "fuit" vers `_ViewerCanvas` en dessous — pour un clic droit, ça ouvre en plus le menu contextuel de la visionneuse (`_show_context_menu`) à chaque changement de mode straighten. **Règle à respecter pour tout futur widget ajouté à la barre d'outils** : accepter systématiquement les deux événements souris, quel que soit le bouton, sinon un clic droit dessus ouvrira le menu contextuel par accident.

## Redressement automatique (deskew) — `modules/qt/deskew.py` + `modules/qt/deskew_qt.py`

Séparé en deux fichiers façon `rotate-flip` : logique métier pure (`deskew.py`, sans aucun import Qt) et orchestration UI (`deskew_qt.py`, worker + overlay + fenêtre de résumé). `deskew_entry_data`/`detect_skew_angle` sont aussi appelées directement (hors worker, une seule image) par `ImageViewer.perform_auto_straighten()` — voir section dédiée juste au-dessus.

### Détection de l'angle — `detect_skew_angle(entry)` (`deskew.py`)

1. Image convertie en niveaux de gris (`numpy`), détection de contours `cv2.Canny(gray, 150, 300, apertureSize=3)` — seuils volontairement élevés (au lieu des 50/150 usuels) : un scan de bande dessinée porte souvent un grain de papier/trame d'impression dense qui, avec des seuils plus permissifs, produit un nombre de faux contours largement supérieur aux vrais traits du dessin.
2. `cv2.HoughLinesP(edges, 1, np.pi/180, threshold=100, minLineLength=min(w,h)/2, maxLineGap=20)` détecte les segments de droite dominants. Sur une image bruitée, ce bruit de contour peut être "cousu" par `HoughLinesP` en fausses lignes longues, majoritairement à 45° (angle le plus favorisé par une grille de pixels carrée) — voir point 7 pour le garde-fou correspondant.
3. **Piège de format selon la version d'OpenCV** : `HoughLinesP` retourne `(N, 4)` sur OpenCV 5.x (chaque `line` est déjà `[x1,y1,x2,y2]`), mais `(N, 1, 4)` sur les anciennes versions 4.x (`line` = `[[x1,y1,x2,y2]]`). Un simple `x1,y1,x2,y2 = line[0]` plante avec `TypeError: cannot unpack non-iterable numpy.int32 object` sur 5.x. Fix : `x1, y1, x2, y2 = np.asarray(line).reshape(-1)`, compatible avec les deux formats — ne jamais revenir à l'indexation `[0]` nue.
4. Pour chaque segment : angle via `atan2`, normalisé `[-90,90]`, puis ramené à l'écart par rapport à l'axe le plus proche — **exactement la même convention à 45° que le calcul manuel** (`line_to_correction`, voir section dédiée plus haut), dupliquée ici plutôt que partagée en fonction commune (aucun code partagé entre les deux fichiers malgré cette convention identique).
5. **Filtrage en deux temps, dans cet ordre précis** (l'ordre inverse casse la détection) :
   - Calcule d'abord la **médiane brute** de tous les angles.
   - Élimine les segments dont l'angle s'écarte de plus de `_MAX_ANGLE_STD_DEG` (2.0°) de cette médiane brute — un seul segment aberrant (ex. un bord quasi horizontal sur une page par ailleurs nettement inclinée) ne doit pas, à lui seul, faire échouer tout le calcul.
   - **Seulement après ce filtrage**, mesure l'écart-type du groupe restant (`inliers`) contre `_MAX_ANGLE_STD_DEG` — mesurer l'écart-type sur les données brutes **avant** filtrage fait qu'un seul outlier parmi un fort consensus fait rejeter tout le lot alors que la médiane brute était déjà fiable.
6. Deux seuils numériques distincts, à ne pas confondre :
   - `_MIN_SEGMENTS = 5` — nombre minimum de segments **bruts** détectés par Hough pour tenter un consensus.
   - `_MIN_INLIERS = 3` — nombre minimum de segments **retenus après filtrage des aberrants**, volontairement plus bas que `_MIN_SEGMENTS` : un fort consensus (écart-type quasi nul) reste fiable même avec peu de segments une fois les faux-positifs écartés.
7. **Garde-fou anti-motif décoratif** : si la médiane retenue tombe à 45° (ou -45°) pile, à `_SUSPECT_ANGLE_TOLERANCE_DEG` (0.1°) près, elle est rejetée plutôt qu'appliquée. Un motif graphique aligné sur la grille de pixels (hachures, trame de fond en diagonale — fréquent sur un scan de bande dessinée) peut produire un consensus parfait (écart-type quasi nul) à cet angle exact, indiscernable statistiquement d'une vraie inclinaison — sauf qu'une vraie inclinaison de scan tombe presque toujours sur un angle quelconque, jamais pile 45°. Le durcissement des seuils Canny (point 1) réduit déjà ce risque en amont ; ce garde-fou reste nécessaire pour les pages où le motif à 45° est malgré tout dominant.
8. Retourne la **médiane** du groupe filtré (`inliers`), ou `None` si aucun consensus fiable trouvé à n'importe quelle étape (moins de `_MIN_SEGMENTS` bruts, moins de `_MIN_INLIERS` après filtrage, écart-type final trop élevé, ou médiane rejetée par le garde-fou du point 7).

Ces seuils (`_MIN_SEGMENTS`, `_MIN_INLIERS`, `_MAX_ANGLE_STD_DEG`, les seuils Canny, `_SUSPECT_ANGLE_TOLERANCE_DEG`) sont **empiriques**, validés sur des scans réels — à ajuster si un nouveau cas d'usage montre un faux négatif/positif systématique, pas à changer par réflexe.

### Application — `deskew_entry_data(entry, state=None)` (`deskew.py`)

1. Appelle `detect_skew_angle`, retourne `False` (échec) si `None` ou angle `< 0.001°`.
2. `img.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC, fillcolor="white")` — **`fillcolor="white"` explicite**, contrairement au manuel qui n'en a pas : les coins vides après rotation sont comblés en blanc plutôt que laissés transparents/noirs, décision explicite de l'utilisateur ("l'utilisateur fera ensuite son crop comme il l'entend" — pas de rognage automatique).
3. Invalidation complète des caches — même variante (A) complète que le manuel (`_thumbnail`/`large_thumb_pil`/`qt_pixmap_large`/`qt_qimage_large`/`_hash`).
4. Sync `ComicInfo.xml` identique au manuel.
5. Retourne `True` si succès.

### Orchestration — `deskew_selected_qt(callbacks)` (`deskew_qt.py`)

Calquée sur `rotate_selected_qt`/`_run_transform` du skill `rotate-flip` (même squelette worker QThread + overlay + bouton Annuler + rollback), avec deux différences :

- **Le worker (`_DeskewWorker`) trace succès ET échecs** (`self.success_count`, `self.failed_names`), contrairement à `_TransformWorker` qui ignore silencieusement un échec individuel — nécessaire pour la fenêtre de résumé final.
- **Un seul `save_state()` avant le lot entier**, un seul `save_state(force=True)` après — undo/redo groupé pour toute la sélection traitée en une fois, identique au comportement de `rotate-flip`. Si l'utilisateur relance l'automatique sur une autre sélection plus tard, ça fait un nouveau point séparé.
- Filtre `not entry.get("is_corrupted")` en plus de `is_image` lors de la construction de la liste des entrées traitées (`deskew_selected_qt`) — une entrée corrompue ne peut de toute façon pas être redressée.

À la fin du traitement (`on_finished`, dans `_run_deskew`), affiche `_DeskewSummaryDialog(parent, worker.success_count, worker.failed_names).show_nonmodal()` — **toujours affichée**, même en cas de succès total (pas seulement en cas d'échec).

### Fenêtre de résumé — `_DeskewSummaryDialog` (`deskew_qt.py`)

Non-modale, centrée via `position_dialog_on_parent()` avant `show()` (pas de flash), titre via `_wt()`. Message centré (`Qt.AlignCenter`) sur deux lignes séparées si échecs (`dialogs.deskew_summary.message_errors`, un `\n` entre le compte de succès et le compte d'échecs). Liste des noms de fichiers en échec dans un `QScrollArea` (labels centrés eux aussi), hauteur plafonnée à 220px.

**Doit figurer dans la liste `apply_app_theme()` de `toggle_theme_qt.py`** (`isinstance(widget, (..., _DeskewSummaryDialog))`) — sans cet ajout, la fenêtre resterait figée dans l'ancien thème si elle reste ouverte pendant un bascule clair/sombre déclenché ailleurs. **Vérifier ce point pour toute future fenêtre de résumé de ce type**, ne pas supposer qu'une autre fenêtre similaire du projet suit forcément déjà ce pattern.

## Points d'entrée UI

**Automatique (en lot) — trois points d'entrée**, tous conditionnés à `has_selected_images()` :

1. **Menu contextuel** (clic droit mosaïque, skill `qt-context-menus`) — `context_menus_qt.py`, `context_menu.image.straighten_auto` → `callbacks['deskew_selected']`.
2. **Barre de menu** — `menubar_qt.py`, même clé/callback.
3. **Colonne d'icônes** — bouton `straighten`, mono-fonction, pointe directement vers le même callback `deskew_selected`.

Callbacks (`PanelWidget._deskew_callbacks()`) : `parent`, `save_state`, `render_mosaic`, `update_button_text`, `refresh_status`, `canvas`, `state`, `rollback` — même forme que `_image_transforms_callbacks()` du skill `rotate-flip` (worker + overlay + annulation), plus `parent` pour le centrage de la fenêtre de résumé.

**Manuel (unitaire) — un seul point d'entrée** : l'icône bi-mode `straighten` **de la barre d'outils de la visionneuse principale** (section "Icône Redressage de la barre d'outils" plus haut), une fois la visionneuse déjà ouverte par un autre moyen (double-clic sur une vignette). Il n'existe pas de point d'entrée dédié depuis la mosaïque ou les menus qui ouvrirait directement la visionneuse sur cet outil.

**Cette distinction est définitive** : même si d'autres commandes rejoignent un jour la barre d'outils de la visionneuse, l'icône bi-mode de la colonne d'icônes et ses 2 entrées de commande (menu contextuel, barre de menus) doivent SURVIVRE, précisément parce qu'ils restent le seul accès au traitement par lot — `perform_auto_straighten()` ne traite jamais qu'une seule page (celle affichée dans la visionneuse), jamais la sélection de la mosaïque ; ne pas confondre avec `deskew_selected_qt` qui reste le seul moyen de traiter un lot.

## Traductions

`locales/fr.json` (et 45 autres langues, dont les 3 variantes CSUR régénérées par script) :
- `context_menu.image.straighten_auto`.
- `tooltip.straighten` (nom du bouton mono-fonction de la colonne d'icônes/fenêtre de config des icônes, "Redresser l'image automatiquement") + `tooltip.straighten_mode_0`/`straighten_mode_1` (tooltip dynamique au survol de l'icône bi-mode **de la barre d'outils de la visionneuse uniquement**, selon le mode courant).
- `dialogs.straighten_viewer.instruction` + `angle_label` — utilisées par le panneau d'angle (voir plus haut).
- `viewer.toolbar_straighten_tooltip` (icône de la barre d'outils, préfixe en gras du tooltip bi-mode) + `viewer.toolbar_crop_instruction` (même traitement pour le crop) + `buttons.validate_straighten` (bouton "Valider" flottant, partagé avec `buttons.validate_crop`) + `messages.warnings.no_straighten_line.title`/`.message` + `messages.errors.straighten_failed.title`/`.message` — propagées aux 45 langues + 3 CSUR.
- `messages.warnings.no_skew_detected.title`/`.message` (message du redressement automatique unitaire déclenché depuis la barre d'outils quand aucune inclinaison fiable n'est trouvée sur la page affichée) — propagée aux 45 langues + 3 CSUR. **Piège de vérification tlh/sjn/qya** : un mot fictif "calqué" sur une clé existante peut malgré tout ne pas être un mot réellement construit — un vrai mot se convertit intégralement en CSUR par les scripts de conversion, tandis qu'un néologisme non reconnu reste partiellement visible en latin dans le résultat régénéré. Toujours vérifier chaque mot d'une phrase fictive par grep dans le fichier source avant exécution, même quand la phrase est calquée sur une clé existante.
- `labels.deskewing` (overlay de progression, placeholder `{percent}`).
- `dialogs.deskew_summary.title`/`message`/`message_errors` (fenêtre de résumé, placeholders `{success}`/`{failed}`).
- `context_menu.canvas.reset` — la ligne "renumérotation → auto" a une ligne sœur juste après, "redressage automatique → manuel".

Vocabulaire fictif réutilisé pour "redresser" (jamais réinventé) : sindarin `Trenarna`/`Trenarn`, quenya `Lempina`, klingon `nIt chenmoH mIllogh`/`nIt chenmoH`. Pour "angle" : klingon `jom`, sindarin `Naith` (attesté chez Tolkien), quenya `Rembë`. Voir skill `add-translation` pour la procédure complète (fr d'abord, jamais enchaîner sans autorisation explicite, lexiques de référence tlh/sjn/qya).

**Toujours absent du mode d'emploi** (`user_guide_qt.py`) — vrai pour le manuel comme pour l'automatique. Voir skill `user-guide`.

## Comment étendre

- **Ajuster la frontière horizontale/verticale du manuel** (actuellement 45° pile) : uniquement `if abs_angle <= 45:` dans `modules/qt/straighten_geometry.py::line_to_correction` — la même convention est dupliquée dans `deskew.py::detect_skew_angle`, à modifier en parallèle si on change l'une sans l'autre volontairement.
- **Ajuster les seuils de fiabilité du deskew automatique** (`_MIN_SEGMENTS`, `_MIN_INLIERS`, `_MAX_ANGLE_STD_DEG`, les seuils Canny, `_SUSPECT_ANGLE_TOLERANCE_DEG` dans `deskew.py`) : valeurs empiriques, voir section dédiée — avant tout changement, valider avec des `print()` de diagnostic sur un cas réel (voir piège CLAUDE.md "toujours diagnostiquer avec des prints avant d'appliquer un fix"), jamais deviner un nouveau seuil à l'aveugle.
- **Changer l'algorithme de rotation** : `resample=Image.Resampling.BICUBIC` dans `straighten_tool_qt.py::perform_straighten` et `deskew.py` (une occurrence chacun) ; `fillcolor="white"` uniquement dans `deskew.py`.
- **Appliquer le redressement manuel à plusieurs pages d'un coup** : n'existe pas (le manuel reste strictement page par page) — si demandé un jour, s'inspirer directement du pattern déjà en place pour l'automatique (`_run_deskew`/`_DeskewWorker`) plutôt que de le réinventer.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour toute nouvelle fenêtre des deux mécanismes — voir en particulier le piège du thème dynamique documenté dans la section `_DeskewSummaryDialog` ci-dessus (et le piège transversal `OverlayTooltip`/thème dynamique documenté dans le skill `viewers`).

## Pièges connus

### Manuel
- **Pas de fichier/classe dédiés** — le trait vit dans `_ViewerCanvas`/`ImageViewer`, partagé avec toute la logique d'affichage/zoom/pan/pagination/crop de la visionneuse principale ; une modification imprudente peut affecter la lecture normale des pages ou le crop, pas seulement le redressement.
- **Undo/redo unifié avec le panneau** — pas d'historique local séparé ; un bug de undo/redo signalé sur le redressement se diagnostique exactement comme pour n'importe quelle autre opération d'édition (skill `undo-redo`).
- **Trait stocké à deux niveaux (image stable + widget dérivé)** — toute modification qui réintroduirait un stockage widget-only sans passer par `_sync_line_from_image()` romprait la synchronisation au pan/zoom/resize.
- **`paintEvent` ne doit PAS resynchroniser depuis `_line_img_*` pendant un tracé/drag en cours** (`_line_drawing`/`_line_dragging_handle`) — sinon le trait resterait visuellement figé jusqu'au relâchement du clic malgré le mouvement de la souris.
- **La spinbox d'angle ne doit jamais repasser par `_notify_line_drawn()`/`on_line_drawn` pendant sa propre édition** (`set_line_end_from_angle`) — sous peine de boucle de réécriture angle→pixels arrondis→angle qui donne l'impression que les boutons +/- ne répondent plus.
- **`BICUBIC` explicite, sans `fillcolor`** — contrairement à `rotate_entry_data` (skill `rotate-flip`, aucun `resample`) et contrairement au deskew automatique (`fillcolor="white"`).
- **Deux `save_state()` sans `force=True`** — aligné sur `perform_crop()`.
- **Le trait ne survit à un changement de page que via `_straighten_by_page`** — `clear_line()` et `_StraightenAnglePanel.reset()` doivent toujours être appelés ensemble en dehors de ce mécanisme de persistance.
- **Pas de garde-fou sur un trait à exactement 45°** — comportement existant à ne pas changer sans consigne explicite.
- **Style `QDoubleSpinBox` custom sans `::up-button`/`::down-button` explicites** → boutons +/- invisibles bien que fonctionnels, pas un problème de police malgré l'apparence — voir `split_dialog_qt.py` comme référence du pattern correct.
- **Curseur du panneau flottant** — `_StraightenAnglePanel` doit réinitialiser le curseur posé par `straighten_update_cursor` sur le canvas (`enterEvent`/`_check_really_left`), sinon il reste affiché par-dessus la spinbox au survol.
- **Persistance en coordonnées image absolues, pas relatives** (`_straighten_by_page`) — contrairement au crop qui utilise des fractions 0-1 (`crop_rel_*`) ; ne pas supposer que les deux mécanismes de persistance par page suivent la même convention de coordonnées si on les compare.
- **Limitation connue, partagée avec le crop, non corrigée** : un trait restauré sur une page qui s'affiche en mode double page n'est pas redessiné (seul `_display_single_page` gère les overlays).

### Automatique
- **Format `HoughLinesP` variable selon la version d'OpenCV** — `(N,4)` sur 5.x, `(N,1,4)` sur 4.x ; toujours `np.asarray(line).reshape(-1)`, jamais `line[0]` nu.
- **Écart-type à mesurer APRÈS filtrage des aberrants, jamais avant** — un seul outlier parmi un fort consensus ne doit pas faire échouer tout le calcul ; voir section détection d'angle pour l'ordre exact des étapes.
- **Deux seuils distincts** (`_MIN_SEGMENTS` sur le brut, `_MIN_INLIERS` sur le filtré, ce dernier plus permissif) — ne pas les fusionner en un seul par souci de simplicité, un test réel a montré qu'ils doivent rester différents.
- **Un motif graphique en diagonale à 45° peut produire un faux consensus parfait** — sur un scan de bande dessinée avec beaucoup de grain/trame ou de hachures denses, `HoughLinesP` peut "coudre" du bruit ou capter un motif décoratif en de nombreux segments tous alignés pile à 45° (ou -45°), avec un écart-type quasi nul, indiscernable statistiquement d'une vraie inclinaison. Double protection : seuils Canny durcis (`150, 300` au lieu de `50, 150`, réduit le bruit en amont) et rejet explicite de toute médiane retenue à 45°/-45° pile (`_SUSPECT_ANGLE_TOLERANCE_DEG`, une vraie inclinaison de scan tombe presque toujours sur un angle quelconque). Voir section "Détection de l'angle" ci-dessus, points 1 et 7.
- **`fillcolor="white"` explicite** — choix délibéré de l'utilisateur (pas de rognage automatique des coins vides, "l'utilisateur fera ensuite son crop comme il l'entend").
- **Résumé toujours affiché**, même sans aucun échec — ne pas le conditionner à `if failed_names:` par souci de "moins de fenêtres inutiles", c'est un choix explicite de l'utilisateur — **ne s'applique qu'au batch** (`_DeskewSummaryDialog`) : `perform_auto_straighten()` (unitaire, depuis la barre d'outils) n'affiche un message que sur ÉCHEC (`no_skew_detected`), jamais de confirmation de succès (l'image se redresse visiblement sous les yeux de l'utilisateur, contrairement au batch où rien n'est visible pendant le traitement) — ne pas harmoniser les deux comportements par souci de cohérence sans consigne explicite, ce sont deux contextes différents.
- **`perform_auto_straighten()` ne traite jamais qu'une seule page** (celle affichée dans la visionneuse), jamais la sélection de la mosaïque — voir icône bi-mode de la barre d'outils plus haut ; ne pas confondre avec `deskew_selected_qt` qui reste le seul moyen de traiter un lot.

### Commun aux deux
- **Invalidation de cache complète (variante A)** dans les deux mécanismes — voir skill `apply-image-operation` pour la distinction exacte entre variante (A) complète et (B) partielle (cette dernière utilisée par `rotate-flip`).
- **Activation identique pour les deux modes** (`has_selected_images`).
- **Aucune section dédiée dans le mode d'emploi**, ni pour le manuel ni pour l'automatique.

## Références croisées

- `rotate-flip` — l'autre mécanisme de rotation de MosaicView (90°/miroir) ; le deskew automatique en reprend directement le squelette worker/overlay/rollback (`_run_deskew` ≈ `_run_transform`), comparer les deux pour toute évolution du pattern commun.
- `apply-image-operation` — pattern général suivi en variante (A) complète par le manuel ET l'automatique.
- `undo-redo` — mécanique de l'historique global de l'appli, utilisée directement par le manuel (pas d'historique local séparé) et un seul point pour tout le lot côté automatique.
- `viewers` — la visionneuse principale (`ImageViewer`) contient le redressement manuel comme outil de sa barre d'outils flottante ; le deskew automatique n'a pas de fenêtre de prévisualisation. Voir en particulier sa section "Barre d'outils flottante" pour l'architecture complète partagée avec le crop, et son piège transversal `OverlayTooltip`/thème dynamique.
- `page-crop` — l'autre outil de la même barre d'outils ; les deux se gatent mutuellement sur le clic-gauche du canvas et partagent le même bouton "Valider" flottant.
- `icon-toolbar` — bouton bi-mode `straighten`, son pattern de bascule clic droit calqué sur `renumber`.
- `qt-context-menus` — les deux entrées du menu contextuel clic droit.
- `add-translation` — procédure complète de traduction, lexiques de référence tlh/sjn/qya, vocabulaire "redresser"/"angle" déjà établi et réutilisé.
- `comicinfo-metadata-editor` — mise à jour des dimensions de page dans `ComicInfo.xml` après redressement, manuel comme automatique.
- `user-guide` — absence actuelle de section dédiée pour les deux mécanismes.
- `qt-tooltips` — le tooltip enrichi de l'icône Redressage (titre + instruction) suit le pattern `OverlayTooltip` standard du projet.
