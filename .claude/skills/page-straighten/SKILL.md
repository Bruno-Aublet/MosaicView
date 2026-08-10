---
name: page-straighten
description: Localiser ou modifier le redressement d'image, manuel (tracé d'une ligne de référence, straighten_viewer_qt.py) ou automatique (détection d'inclinaison par transformée de Hough, deskew.py/deskew_qt.py). Utiliser dès qu'une tâche touche à StraightenViewerDialog, deskew_entry_data, ou au bouton/menu "Redresser l'image".
---

# Redressement d'image — MosaicView

Deux mécanismes **complémentaires**, ni l'un ni l'autre ne remplace l'autre :

1. **Redressement manuel** — fenêtre plein-écran dédiée où l'utilisateur trace un **trait de référence** sur une image (bordure de case, ligne d'horizon...). Le code interprète l'orientation du trait, en déduit l'angle de correction, applique une rotation libre.
2. **Redressement automatique (deskew)** — détecte l'inclinaison **sans intervention** de l'utilisateur via la transformée de Hough (OpenCV), directement sur la sélection courante, sans fenêtre de prévisualisation.

Distinct de la rotation 90°/miroir de la mosaïque (voir skill `rotate-flip`) — **aucun code partagé**, malgré le mot "rotation" en commun. Le redressement (manuel ou auto) corrige une **inclinaison involontaire** avec un angle fin arbitraire ; `rotate-flip` fait pivoter une image **volontairement** de 90° exacts.

## Bascule manuel/automatique — icône unique, clic droit

Un seul bouton dans la colonne d'icônes (`id: "straighten"`), qui fonctionne en **bi-mode** exactement comme le bouton `renumber` (skill `icon-toolbar`) :
- **Clic gauche** : `PanelWidget._straighten_btn_action()` (`panel_widget.py:1391`) — route vers `show_straighten_viewer(...)` (mode manuel, `straighten_mode == 0`) ou `deskew_selected_qt(...)` (mode auto, `straighten_mode == 1`) selon `state.straighten_mode`.
- **Clic droit** : `PanelWidget._toggle_straighten_mode()` (`panel_widget.py:1402`) — bascule `state.straighten_mode` entre 0 et 1 (un simple `1 - current`, contrairement au cycle à 3 états de `renumber_mode`), persisté par panneau via `_renumber_config().set_straighten_mode(mode)` (même méthode `_renumber_config()` que pour `renumber_mode`, malgré son nom — elle retourne juste "le bon objet config panel1/panel2", pas spécifique à la renumérotation).
- **Tooltip dynamique** : `icon_toolbar_qt.py` (`IconLabel.enterEvent`, ~ligne 245) affiche `tooltip.straighten_mode_0`/`straighten_mode_1` selon le mode courant, toujours actif au survol même si le bouton est grisé (même dérogation que `renumber` — le clic droit doit rester accessible).
- **Persistance** : `config_manager.py` — `get_/set_straighten_mode()` (panel1), `get_/set_straighten_mode_panel2()` (panel2), redirigées par `Panel2Config.get_/set_straighten_mode()`. Valeur par défaut : `0` (manuel).
- **Reset aux valeurs par défaut** (`session_restore_qt.py::reset_to_defaults`) : remet `straighten_mode` à `0` pour tous les panneaux, dans le même bloc que la remise à zéro de `renumber_mode` — mentionné explicitement dans le tooltip/menu de reset (`context_menu.canvas.reset`, ligne "redressage automatique → manuel").

**Activation identique pour les deux modes** : `has_selected_images()` (`_ACTIVATION_RULES["straighten"]`, `icon_toolbar_qt.py:146`) — contrairement à l'ancien comportement du mode manuel seul (qui n'exigeait aucune sélection, `has_images()`), **les deux modes exigent désormais une sélection**. Changement de comportement volontaire lors de l'ajout du mode automatique, pas un oubli.

## Redressement manuel — `modules/qt/straighten_viewer_qt.py`

Tout le mécanisme (widget image custom + fenêtre + logique de calcul d'angle + undo/redo interne) tient dans un seul fichier (~965 lignes), à la différence de la rotation 90°/miroir qui sépare logique métier (`image_ops.py`) et orchestration UI (`image_transforms_qt.py`).

- **`_StraightenImageWidget`** (`QWidget`) — affiche l'image avec zoom (molette) et pan (clic droit maintenu), gère le tracé et l'édition du trait de référence.
- **`StraightenViewerDialog`** (`QDialog`) — la fenêtre complète : toolbar de navigation, zone image, barre du bas (Appliquer/Undo/Redo/Annuler).
- **`show_straighten_viewer(parent, callbacks)`** — point d'entrée public.

### Le widget image — tracé et édition du trait

Le trait de référence est stocké à **deux niveaux** (`_sync_line_from_image`, `straighten_viewer_qt.py`) :
- `_line_img_start`/`_line_img_end` — coordonnées **image**, stables, figées à chaque fin de tracé/déplacement de poignée dans `_notify_line()` via `_widget_to_image`. C'est la source de vérité persistante.
- `_line_start`/`_line_end` — coordonnées **widget** (`QPoint`), dérivées des précédentes via `_image_to_widget` (l'inverse de `_widget_to_image`), utilisées uniquement pour le dessin (`paintEvent`) et la détection de poignée (`_hit_handle`). Recalculées par `_sync_line_from_image()`, appelée systématiquement en tête de `paintEvent` (donc à chaque pan, zoom, **et** redimensionnement de la fenêtre puisque Qt réinvoque `paintEvent` dans les trois cas), plus explicitement dans `mouseMoveEvent` (pan) et `set_zoom`/`reset_zoom`/`fit_to_window` (redondant avec l'appel dans `paintEvent` mais inoffensif, idempotent).

**Piège corrigé (2026-08, v1.7.2)** : avant ce mécanisme à deux niveaux, `_line_start`/`_line_end` étaient les seules coordonnées stockées (en widget uniquement), jamais recalculées après un pan ou un zoom — seul `mousePressEvent`/`mouseMoveEvent`/`mouseReleaseEvent` les modifiait. Résultat : paner (clic droit maintenu) ou zoomer (molette) déplaçait l'image affichée sans jamais retoucher le trait, qui restait visuellement figé à l'ancien endroit — trait et image se désynchronisaient au premier pan/zoom suivant un tracé. Voir aussi le même piège corrigé la même session dans `page-crop` (rectangle de recadrage) et `clone-zone` (marqueur de source, cas du redimensionnement uniquement).

- **Premier tracé** : clic-gauche + glisser dessine un trait rouge de 2px entre le point de départ et le point courant (`mousePressEvent`/`mouseMoveEvent`/`mouseReleaseEvent`).
- **Poignées de réglage** : chaque extrémité du trait affiche un cercle rouge à contour blanc (`_HANDLE_RADIUS = 7`, zone de clic élargie `_HANDLE_HIT = 12` pour faciliter la prise). Un clic sur une poignée existante permet de la redéplacer (`_dragging_handle`) au lieu de retracer un nouveau trait — utile pour affiner la précision après un premier tracé approximatif.
- À chaque relâchement de clic gauche (fin de tracé **ou** fin de déplacement d'une poignée), `_notify_line()` fige `_line_img_start`/`_line_img_end` puis recalcule l'angle et invoque le callback `on_line_drawn` — l'angle est donc **toujours recalculé en direct** après un ajustement de poignée, pas seulement après le tout premier tracé.
- Changer de page (`_prev_image`/`_next_image`) appelle `clear_line()` — le trait (widget **et** image) ne survit jamais au changement d'image affichée, chaque page repart d'un tracé vide.

### Calcul de l'angle — `_on_line_drawn` (`straighten_viewer_qt.py:630`)

1. `dx`/`dy` = vecteur du trait en coordonnées image (`ix2-ix1`, `iy2-iy1`).
2. `angle_deg = math.degrees(math.atan2(dy, dx))`, normalisé dans `[-90, 90]` (un trait et son symétrique à 180° doivent donner la même correction).
3. **Décision horizontale vs verticale** : `abs_angle <= 45` → le trait est interprété comme approximativement **horizontal**, correction = `angle_deg` tel quel. Sinon → interprété comme approximativement **vertical**, correction = `angle_deg - 90` (si `angle_deg >= 0`) ou `angle_deg + 90` (sinon) — ramène l'angle à "l'écart par rapport à la verticale" au lieu de "l'écart par rapport à l'horizontale". **Même convention réutilisée telle quelle dans `deskew.py`** pour normaliser les angles de segments détectés par Hough — voir section dédiée plus bas.
4. **Piège de signe documenté en commentaire dans le code** (ligne 648-649) : `PIL.rotate()` tourne en anti-horaire pour un angle positif, mais l'axe Y à l'écran pointe vers le bas (sens anti-mathématique) — la formule ci-dessus compense déjà ce décalage, ne **pas** ajouter un signe `-` supplémentaire par réflexe sans retester au clavier/souris que le sens de rotation reste correct après modification.
5. Le résultat est stocké dans `self._pending_angle` (pas appliqué tout de suite) et le bouton "Appliquer à cette page" est activé seulement si `abs(correction) > 0.001` — un trait quasi parfaitement droit n'active pas le bouton, il n'y a rien à corriger.

**Aucun garde-fou sur un trait quasi vertical/horizontal à exactement 45°** — la bascule horizontale/verticale est une frontière dure (`<=45` vs `>45`), pas de zone tampon ; comportement existant, pas un bug à corriger sans consigne explicite.

### Application — `_apply_to_current` (`straighten_viewer_qt.py:667`)

Contrairement à la rotation 90°/miroir (skill `rotate-flip`), **pas de worker QThread** : tout se fait en synchrone dans le thread UI (l'opération est rapide, une seule image à la fois, pas de traitement par lot).

1. `save_state()` (undo global de l'appli, **sans** `force=True` à ce stade) avant modification.
2. `img.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)` — **`BICUBIC`**, contrairement à `rotate_entry_data` (skill `rotate-flip`) qui n'impose aucun `resample` explicite (défaut PIL = `NEAREST` pour les angles non multiples de 90°). **Pas de `fillcolor`** ici (contrairement au deskew automatique, voir plus bas) — les coins vides après rotation restent transparents/noirs selon le mode de l'image.
3. Invalidation complète des caches — **variante (A)** du skill `apply-image-operation` (`entry['img'] = None` après réassignation, plus `_thumbnail`/`large_thumb_pil`/`qt_pixmap_large`/`qt_qimage_large`/`_hash`), contrairement à l'invalidation partielle de `rotate_entry_data`/`flip_entry_data`.
4. Synchronisation `ComicInfo.xml` (`update_page_entries_in_xml_data`, skill `comicinfo-metadata-editor`) — même mécanisme que pour rotation/miroir, les dimensions changent après une rotation à angle libre avec `expand=True`.
5. `state.modified = True`, puis **second** `save_state(force=True)` après modification — ici `force=True` **est bien présent** contrairement au premier appel (cohérent avec le skill `undo-redo` : le second `save_state` doit forcer un nouveau point d'historique même si aucune autre modification n'a eu lieu entre-temps).
6. Le trait est effacé (`clear_line()`), le bouton Appliquer redésactivé, l'image réaffichée.

### Undo/redo — deux systèmes empilés, pas un seul

Point le plus piégeux du fichier : **deux mécanismes d'historique coexistent et s'empilent** à chaque `_apply_to_current`/`_undo`/`_redo` :

1. **L'historique global de l'appli** (`callbacks['save_state']`, voir skill `undo-redo`) — un point avant et un point après chaque opération, exactement comme n'importe quelle autre fonction d'édition d'image. Permet d'annuler un redressement depuis les boutons Undo/Redo **globaux** de la fenêtre principale, même après avoir fermé la visionneuse de redressement.
2. **Un historique interne à la visionneuse elle-même**, indépendant du premier : `self._bytes_histories` (dict `idx → [bytes_before1, bytes_before2, ...]`) et `self._redo_stacks` (dict `idx → [bytes_after1, ...]`), **un par page affichée** (indexés par position dans `selected_entries`, pas par identité d'objet). Les boutons Undo/Redo **de la fenêtre de redressement** (`Ctrl+Z`/`Ctrl+Y` ou icônes `BTN_Batch_Undo.png`/`BTN_Batch_Redo.png`) pilotent cet historique interne, pas l'historique global.

**Conséquence pratique** : cliquer sur Undo dans la visionneuse (`_undo`, ligne 736) fait *deux* choses à la fois — ça pousse un nouveau point dans l'historique global (`save_state()` puis `save_state(force=True)`, comme n'importe quelle édition) **et** ça dépile l'historique interne (`history.pop()`). Le undo interne ne "voyage" donc pas dans l'historique global en marche arrière — il ajoute un nouveau point qui *se trouve* représenter l'état d'avant la dernière rotation. Si un bug de undo/redo est signalé spécifiquement sur cette fenêtre, vérifier lequel des deux systèmes est en cause avant de supposer que c'est le mécanisme global documenté dans le skill `undo-redo`.

**`_redo_stacks[idx].clear()`** est appelé après chaque nouvelle rotation appliquée (`_apply_to_current`, ligne 718) — comportement standard (une nouvelle action efface la pile de redo), mais seulement sur l'historique **interne** ; l'historique global suit sa propre règle indépendante (voir skill `undo-redo`).

### Sélection de la page de départ — `show_straighten_viewer` (`straighten_viewer_qt.py:917`)

Ouvre sur **toutes** les images valides de la mosaïque (`is_image` et pas `is_corrupted`), navigables ensuite via ◀/▶ ou les flèches clavier — pas seulement l'image sélectionnée :
- Si une sélection existe, ouvre sur la **première image sélectionnée valide** (au sens de l'ordre dans `images_data`).
- Sinon, ouvre sur la première image de la mosaïque (comportement du code interne inchangé — mais l'icône/menu qui y donnent accès exigent désormais une sélection, voir section bascule plus haut ; ce cas "sans sélection" ne peut donc plus être atteint via les points d'entrée UI standards, uniquement si `show_straighten_viewer` était appelée par un autre chemin).
- Les images corrompues sont exclues de la liste navigable entièrement (ni affichées, ni comptées dans le compteur `n / total`).

### Zoom, pan, plein écran

Vocabulaire commun aux 5 visionneuses du projet (voir skill `viewers`) : `Ctrl++`/`Ctrl+-` (zoom), `Ctrl+0` (fit to window), `Ctrl+1` (reset 100%), `F11` (plein écran), molette (zoom), clic droit maintenu (pan). Implémentation propre à ce fichier (`_StraightenImageWidget`), pas de classe partagée avec les 4 autres visionneuses.

## Redressement automatique (deskew) — `modules/qt/deskew.py` + `modules/qt/deskew_qt.py`

Contrairement au manuel, séparé en deux fichiers façon `rotate-flip` : logique métier pure (`deskew.py`, sans aucun import Qt) et orchestration UI (`deskew_qt.py`, worker + overlay + fenêtre de résumé).

### Détection de l'angle — `detect_skew_angle(entry)` (`deskew.py`)

1. Image convertie en niveaux de gris (`numpy`), détection de contours `cv2.Canny(gray, 50, 150, apertureSize=3)`.
2. `cv2.HoughLinesP(edges, 1, np.pi/180, threshold=100, minLineLength=min(w,h)/2, maxLineGap=20)` détecte les segments de droite dominants.
3. **Piège de format selon la version d'OpenCV, déjà rencontré** : `HoughLinesP` retourne `(N, 4)` sur OpenCV 5.x (chaque `line` est déjà `[x1,y1,x2,y2]`), mais `(N, 1, 4)` sur les anciennes versions 4.x (`line` = `[[x1,y1,x2,y2]]`). Un simple `x1,y1,x2,y2 = line[0]` plante avec `TypeError: cannot unpack non-iterable numpy.int32 object` sur 5.x. Fix : `x1, y1, x2, y2 = np.asarray(line).reshape(-1)`, compatible avec les deux formats — ne jamais revenir à l'indexation `[0]` nue.
4. Pour chaque segment : angle via `atan2`, normalisé `[-90,90]`, puis ramené à l'écart par rapport à l'axe le plus proche — **exactement la même convention à 45° que `_on_line_drawn`** du manuel (voir section dédiée plus haut), dupliquée ici plutôt que partagée en fonction commune (aucun code partagé entre les deux fichiers malgré cette convention identique).
5. **Filtrage en deux temps, dans cet ordre précis** (piège déjà rencontré — l'ordre inverse casse la détection) :
   - Calcule d'abord la **médiane brute** de tous les angles.
   - Élimine les segments dont l'angle s'écarte de plus de `_MAX_ANGLE_STD_DEG` (2.0°) de cette médiane brute — un seul segment aberrant (ex. un bord quasi horizontal sur une page par ailleurs nettement inclinée) ne doit pas, à lui seul, faire échouer tout le calcul.
   - **Seulement après ce filtrage**, mesure l'écart-type du groupe restant (`inliers`) contre `_MAX_ANGLE_STD_DEG` — mesurer l'écart-type sur les données brutes **avant** filtrage (bug initial de cette fonction) fait qu'un seul outlier parmi un fort consensus fait rejeter tout le lot alors que la médiane brute était déjà fiable.
6. Deux seuils numériques distincts, à ne pas confondre :
   - `_MIN_SEGMENTS = 5` — nombre minimum de segments **bruts** détectés par Hough pour tenter un consensus.
   - `_MIN_INLIERS = 3` — nombre minimum de segments **retenus après filtrage des aberrants**, volontairement plus bas que `_MIN_SEGMENTS` : un fort consensus (écart-type quasi nul) reste fiable même avec peu de segments une fois les faux-positifs écartés.
7. Retourne la **médiane** du groupe filtré (`inliers`), ou `None` si aucun consensus fiable trouvé à n'importe quelle étape (moins de `_MIN_SEGMENTS` bruts, moins de `_MIN_INLIERS` après filtrage, ou écart-type final encore trop élevé).

Ces seuils sont **empiriques**, validés sur un scan réel (couverture de livre photographiée, ~9° d'inclinaison, bords nets) — à ajuster si un nouveau cas d'usage montre un faux négatif/positif systématique, pas à changer par réflexe.

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

Non-modale, centrée via `position_dialog_on_parent()` avant `show()` (pas de flash), titre via `_wt()`. Message centré (`Qt.AlignCenter`) sur deux lignes séparées si échecs (`dialogs.deskew_summary.message_errors`, un `\n` entre le compte de succès et le compte d'échecs — demande explicite de l'utilisateur, la version à une seule ligne était jugée illisible). Liste des noms de fichiers en échec dans un `QScrollArea` (labels centrés eux aussi), hauteur plafonnée à 220px.

**Ajoutée à la liste `apply_app_theme()` de `toggle_theme_qt.py`** (`isinstance(widget, (..., _DeskewSummaryDialog))`) — sans cet ajout, la fenêtre resterait figée dans l'ancien thème si elle reste ouverte pendant un bascule clair/sombre déclenché ailleurs (piège identifié et corrigé après une première vérification incomplète des 8 règles UI Qt qui avait conclu à tort "conforme" en se basant sur le fait qu'aucune autre fenêtre de résumé du projet, ex. `_CbtSummaryDialog`, n'a ce mécanisme — ce n'était pas une excuse valable, juste une dette préexistante ailleurs).

## Points d'entrée UI (communs aux deux modes)

Trois points d'entrée, tous conditionnés à `has_selected_images()` (voir section bascule plus haut) :

1. **Menu contextuel** (clic droit mosaïque, skill `qt-context-menus`) — `context_menus_qt.py:424-428`, deux entrées désormais séparées : `context_menu.image.straighten_manual` → `callbacks['show_straighten_viewer']`, `context_menu.image.straighten_auto` → `callbacks['deskew_selected']`.
2. **Barre de menu** — `menubar_qt.py:207-210`, mêmes clés/callbacks.
3. **Colonne d'icônes** — bouton unique bi-mode `straighten` (voir section bascule).

Callbacks manuel (`PanelWidget._straighten_callbacks()`, `panel_widget.py:1605`) : `save_state`, `render_mosaic`, `update_button_text`, `state` — pas de `rollback` (pas de worker à annuler en cours de route).
Callbacks automatique (`PanelWidget._deskew_callbacks()`, `panel_widget.py:1643`) : `parent`, `save_state`, `render_mosaic`, `update_button_text`, `refresh_status`, `canvas`, `state`, `rollback` — même forme que `_image_transforms_callbacks()` du skill `rotate-flip` (worker + overlay + annulation), plus `parent` pour le centrage de la fenêtre de résumé.

## Traductions

`locales/fr.json` (et 45 autres langues, dont les 3 variantes CSUR régénérées par script) :
- `context_menu.image.straighten_manual`/`straighten_auto` (remplacent l'ancienne clé unique `context_menu.image.straighten`, supprimée).
- `tooltip.straighten` (nom générique du bouton, fenêtre de config des icônes uniquement) + `tooltip.straighten_mode_0`/`straighten_mode_1` (tooltip dynamique au survol, selon le mode courant).
- `dialogs.straighten_viewer.title`/`instruction` (fenêtre manuelle, inchangées).
- `labels.deskewing` (overlay de progression, placeholder `{percent}`).
- `dialogs.deskew_summary.title`/`message`/`message_errors` (fenêtre de résumé, placeholders `{success}`/`{failed}`).
- `context_menu.canvas.reset` — la ligne "renumérotation → auto" a une nouvelle ligne sœur juste après, "redressage automatique → manuel" (position vérifiée identique — avant-dernière ligne du bloc, juste avant la ligne compression ZIP — dans les 39 langues naturelles avant l'insertion par script).

Vocabulaire fictif réutilisé pour "redresser" (jamais réinventé) : sindarin `Trenarna`/`Trenarn`, quenya `Lempina`, klingon `nIt chenmoH mIllogh`/`nIt chenmoH` — déjà établis par le manuel, repris tels quels pour l'automatique. Voir skill `add-translation` pour la procédure complète (fr d'abord, jamais enchaîner sans autorisation explicite, lexiques de référence tlh/sjn/qya).

**Toujours absent du mode d'emploi** (`user_guide_qt.py`) — vrai pour le manuel comme pour l'automatique. Voir skill `user-guide`.

## Comment étendre

- **Ajuster la frontière horizontale/verticale du manuel** (actuellement 45° pile) : uniquement `if abs_angle <= 45:` dans `straighten_viewer_qt.py::_on_line_drawn` — la même convention est dupliquée dans `deskew.py::detect_skew_angle`, à modifier en parallèle si on change l'une sans l'autre volontairement.
- **Ajuster les seuils de fiabilité du deskew automatique** (`_MIN_SEGMENTS`, `_MIN_INLIERS`, `_MAX_ANGLE_STD_DEG` dans `deskew.py`) : valeurs empiriques, voir section dédiée — avant tout changement, valider avec des `print()` de diagnostic sur un cas réel (voir piège CLAUDE.md "toujours diagnostiquer avec des prints avant d'appliquer un fix"), jamais deviner un nouveau seuil à l'aveugle.
- **Changer l'algorithme de rotation** : `resample=Image.Resampling.BICUBIC` dans les deux fichiers (une occurrence chacun) ; `fillcolor="white"` uniquement dans `deskew.py`.
- **Appliquer le redressement manuel à plusieurs pages d'un coup** : n'existe toujours pas (le manuel reste strictement page par page) — si demandé un jour, s'inspirer directement du pattern déjà en place pour l'automatique (`_run_deskew`/`_DeskewWorker`) plutôt que de le réinventer.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour toute nouvelle fenêtre des deux mécanismes — voir en particulier le piège du thème dynamique documenté dans la section `_DeskewSummaryDialog` ci-dessus, facile à rater en se fiant à un pattern déjà bancal ailleurs dans le projet.

## Pièges connus

### Manuel
- **Deux systèmes d'undo/redo empilés** (global de l'appli + interne à la fenêtre) — voir section dédiée ; un bug de undo/redo signalé sur cette fenêtre spécifiquement doit être diagnostiqué en tenant compte des deux avant de supposer qu'un seul est en cause.
- **Trait stocké à deux niveaux (image stable + widget dérivé)** depuis le correctif 2026-08 (v1.7.2) — voir section "Le widget image" ; toute modification qui réintroduirait un stockage widget-only sans passer par `_sync_line_from_image()` romprait la synchronisation au pan/zoom/resize.
- **`BICUBIC` explicite, sans `fillcolor`** — contrairement à `rotate_entry_data` (skill `rotate-flip`, aucun `resample`) et contrairement au deskew automatique (`fillcolor="white"`).
- **Premier `save_state()` sans `force=True`, second avec** — ordre inverse de ce qu'on pourrait supposer par symétrie.
- **Le trait ne survit jamais à un changement de page** — comportement voulu.
- **Pas de garde-fou sur un trait à exactement 45°** — comportement existant à ne pas changer sans consigne explicite.

### Automatique
- **Format `HoughLinesP` variable selon la version d'OpenCV** — `(N,4)` sur 5.x, `(N,1,4)` sur 4.x ; toujours `np.asarray(line).reshape(-1)`, jamais `line[0]` nu.
- **Écart-type à mesurer APRÈS filtrage des aberrants, jamais avant** — un seul outlier parmi un fort consensus ne doit pas faire échouer tout le calcul ; voir section détection d'angle pour l'ordre exact des étapes.
- **Deux seuils distincts** (`_MIN_SEGMENTS` sur le brut, `_MIN_INLIERS` sur le filtré, ce dernier plus permissif) — ne pas les fusionner en un seul par souci de simplicité, un test réel a montré qu'ils doivent rester différents.
- **`fillcolor="white"` explicite** — choix délibéré de l'utilisateur (pas de rognage automatique des coins vides, "l'utilisateur fera ensuite son crop comme il l'entend").
- **Résumé toujours affiché**, même sans aucun échec — ne pas le conditionner à `if failed_names:` par souci de "moins de fenêtres inutiles", c'est un choix explicite de l'utilisateur.

### Commun aux deux
- **Invalidation de cache complète (variante A)** dans les deux fichiers — voir skill `apply-image-operation` pour la distinction exacte entre variante (A) complète et (B) partielle (cette dernière utilisée par `rotate-flip`).
- **Activation désormais identique pour les deux modes** (`has_selected_images`) — changement de comportement du manuel (qui n'exigeait auparavant aucune sélection), volontaire, ne pas revenir en arrière sans consigne explicite.
- **Aucune section dédiée dans le mode d'emploi**, ni pour le manuel ni pour l'automatique.

## Références croisées

- `rotate-flip` — l'autre mécanisme de rotation de MosaicView (90°/miroir) ; le deskew automatique en reprend directement le squelette worker/overlay/rollback (`_run_deskew` ≈ `_run_transform`), comparer les deux pour toute évolution du pattern commun.
- `apply-image-operation` — pattern général suivi en variante (A) complète par le manuel ET l'automatique.
- `undo-redo` — mécanique de l'historique global de l'appli, utilisée par les deux mécanismes (un seul point pour tout le lot côté automatique, deux systèmes empilés côté manuel).
- `viewers` — la visionneuse manuelle est l'une des 5 visionneuses plein-écran du projet ; l'automatique n'en a pas (pas de fenêtre de prévisualisation).
- `icon-toolbar` — bouton bi-mode `straighten`, son pattern de bascule clic droit calqué sur `renumber`.
- `qt-context-menus` — les deux entrées du menu contextuel clic droit.
- `add-translation` — procédure complète de traduction, lexiques de référence tlh/sjn/qya, vocabulaire "redresser" déjà établi et réutilisé par l'automatique.
- `comicinfo-metadata-editor` — mise à jour des dimensions de page dans `ComicInfo.xml` après redressement, manuel comme automatique.
- `user-guide` — absence actuelle de section dédiée pour les deux mécanismes.
