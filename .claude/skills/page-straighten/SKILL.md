---
name: page-straighten
description: Localiser ou modifier le redressement d'image (tracé d'une ligne de référence, calcul de l'angle de correction, rotation libre appliquée). Utiliser dès qu'une tâche touche à straighten_viewer_qt.py, StraightenViewerDialog, ou au bouton/menu "Redresser l'image".
---

# Redressement d'image — MosaicView

Fenêtre plein-écran dédiée où l'utilisateur trace un **trait de référence** sur une image (typiquement le long d'une bordure de case, d'une ligne d'horizon, ou de tout élément censé être parfaitement horizontal ou vertical sur la page scannée). Le code interprète l'orientation approximative de ce trait, en déduit l'angle de correction nécessaire, et applique une **rotation libre** (angle quelconque, pas seulement des multiples de 90°) pour redresser l'image.

Distinct de la rotation 90°/miroir de la mosaïque (voir skill `rotate-flip`) — **aucun code partagé**, malgré le mot "rotation" en commun dans les deux. Le redressement corrige une **inclinaison involontaire** (scan de travers) avec un angle fin arbitraire ; `rotate-flip` fait pivoter une image **volontairement** de 90° exacts (page dans le mauvais sens).

## Fichier unique — `modules/qt/straighten_viewer_qt.py`

Tout le mécanisme (widget image custom + fenêtre + logique de calcul d'angle + undo/redo interne) tient dans un seul fichier (~965 lignes), à la différence de la rotation 90°/miroir qui sépare logique métier (`image_ops.py`) et orchestration UI (`image_transforms_qt.py`).

- **`_StraightenImageWidget`** (`QWidget`) — affiche l'image avec zoom (molette) et pan (clic droit maintenu), gère le tracé et l'édition du trait de référence.
- **`StraightenViewerDialog`** (`QDialog`) — la fenêtre complète : toolbar de navigation, zone image, barre du bas (Appliquer/Undo/Redo/Annuler).
- **`show_straighten_viewer(parent, callbacks)`** — point d'entrée public.

## Le widget image — tracé et édition du trait

Le trait de référence est stocké en coordonnées **widget** (`_line_start`/`_line_end`, des `QPoint`), converti en coordonnées **image** seulement au moment de la notification (`_widget_to_image`, tient compte du zoom et du centrage).

- **Premier tracé** : clic-gauche + glisser dessine un trait rouge de 2px entre le point de départ et le point courant (`mousePressEvent`/`mouseMoveEvent`/`mouseReleaseEvent`).
- **Poignées de réglage** : chaque extrémité du trait affiche un cercle rouge à contour blanc (`_HANDLE_RADIUS = 7`, zone de clic élargie `_HANDLE_HIT = 12` pour faciliter la prise). Un clic sur une poignée existante permet de la redéplacer (`_dragging_handle`) au lieu de retracer un nouveau trait — utile pour affiner la précision après un premier tracé approximatif.
- À chaque relâchement de clic gauche (fin de tracé **ou** fin de déplacement d'une poignée), `_notify_line()` recalcule l'angle et invoque le callback `on_line_drawn` — l'angle est donc **toujours recalculé en direct** après un ajustement de poignée, pas seulement après le tout premier tracé.
- Changer de page (`_prev_image`/`_next_image`) appelle `clear_line()` — le trait ne survit jamais au changement d'image affichée, chaque page repart d'un tracé vide.

## Calcul de l'angle — `_on_line_drawn` (`straighten_viewer_qt.py:630`)

C'est le cœur du skill — la partie la plus facile à casser par une modification maladroite :

1. `dx`/`dy` = vecteur du trait en coordonnées image (`ix2-ix1`, `iy2-iy1`).
2. `angle_deg = math.degrees(math.atan2(dy, dx))`, normalisé dans `[-90, 90]` (un trait et son symétrique à 180° doivent donner la même correction).
3. **Décision horizontale vs verticale** : `abs_angle <= 45` → le trait est interprété comme approximativement **horizontal**, correction = `angle_deg` tel quel. Sinon → interprété comme approximativement **vertical**, correction = `angle_deg - 90` (si `angle_deg >= 0`) ou `angle_deg + 90` (sinon) — ramène l'angle à "l'écart par rapport à la verticale" au lieu de "l'écart par rapport à l'horizontale".
4. **Piège de signe documenté en commentaire dans le code** (ligne 648-649) : `PIL.rotate()` tourne en anti-horaire pour un angle positif, mais l'axe Y à l'écran pointe vers le bas (sens anti-mathématique) — la formule ci-dessus compense déjà ce décalage, ne **pas** ajouter un signe `-` supplémentaire par réflexe sans retester au clavier/souris que le sens de rotation reste correct après modification.
5. Le résultat est stocké dans `self._pending_angle` (pas appliqué tout de suite) et le bouton "Appliquer à cette page" est activé seulement si `abs(correction) > 0.001` — un trait quasi parfaitement droit n'active pas le bouton, il n'y a rien à corriger.

**Aucun garde-fou sur un trait quasi vertical/horizontal à exactement 45°** — la bascule horizontale/verticale est une frontière dure (`<=45` vs `>45`), pas de zone tampon ; un trait tracé à 44,9° et un autre à 45,1° peuvent donner deux corrections très différentes en apparence si l'intention de l'utilisateur était ambiguë. Comportement existant, pas un bug à corriger sans consigne explicite.

## Application — `_apply_to_current` (`straighten_viewer_qt.py:667`)

Contrairement à la rotation 90°/miroir (skill `rotate-flip`), **pas de worker QThread** : tout se fait en synchrone dans le thread UI (l'opération est rapide, une seule image à la fois, pas de traitement par lot).

1. `save_state()` (undo global de l'appli, **sans** `force=True` à ce stade) avant modification.
2. `img.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)` — **`BICUBIC`**, contrairement à `rotate_entry_data` (skill `rotate-flip`) qui n'impose aucun `resample` explicite (défaut PIL = `NEAREST` pour les angles non multiples de 90°). Ce choix est cohérent avec la nature de l'opération : un angle libre laisse forcément apparaître des pixels interpolés sur les bords de l'image tournée, `BICUBIC` limite le crénelage visible par rapport au défaut.
3. Invalidation complète des caches — **variante (A)** du skill `apply-image-operation` (`entry['img'] = None` après réassignation, plus `_thumbnail`/`large_thumb_pil`/`qt_pixmap_large`/`qt_qimage_large`/`_hash`), contrairement à l'invalidation partielle de `rotate_entry_data`/`flip_entry_data`.
4. Synchronisation `ComicInfo.xml` (`update_page_entries_in_xml_data`, skill `comicinfo-metadata-editor`) — même mécanisme que pour rotation/miroir, les dimensions changent après une rotation à angle libre avec `expand=True`.
5. `state.modified = True`, puis **second** `save_state(force=True)` après modification — ici `force=True` **est bien présent** contrairement au premier appel (cohérent avec le skill `undo-redo` : le second `save_state` doit forcer un nouveau point d'historique même si aucune autre modification n'a eu lieu entre-temps).
6. Le trait est effacé (`clear_line()`), le bouton Appliquer redésactivé, l'image réaffichée.

## Undo/redo — deux systèmes empilés, pas un seul

Point le plus piégeux du fichier : **deux mécanismes d'historique coexistent et s'empilent** à chaque `_apply_to_current`/`_undo`/`_redo` :

1. **L'historique global de l'appli** (`callbacks['save_state']`, voir skill `undo-redo`) — un point avant et un point après chaque opération, exactement comme n'importe quelle autre fonction d'édition d'image. Permet d'annuler un redressement depuis les boutons Undo/Redo **globaux** de la fenêtre principale, même après avoir fermé la visionneuse de redressement.
2. **Un historique interne à la visionneuse elle-même**, indépendant du premier : `self._bytes_histories` (dict `idx → [bytes_before1, bytes_before2, ...]`) et `self._redo_stacks` (dict `idx → [bytes_after1, ...]`), **un par page affichée** (indexés par position dans `selected_entries`, pas par identité d'objet). Les boutons Undo/Redo **de la fenêtre de redressement** (`Ctrl+Z`/`Ctrl+Y` ou icônes `BTN_Batch_Undo.png`/`BTN_Batch_Redo.png`) pilotent cet historique interne, pas l'historique global.

**Conséquence pratique** : cliquer sur Undo dans la visionneuse (`_undo`, ligne 736) fait *deux* choses à la fois — ça pousse un nouveau point dans l'historique global (`save_state()` puis `save_state(force=True)`, comme n'importe quelle édition) **et** ça dépile l'historique interne (`history.pop()`). Le undo interne ne "voyage" donc pas dans l'historique global en marche arrière — il ajoute un nouveau point qui *se trouve* représenter l'état d'avant la dernière rotation. Si un bug de undo/redo est signalé spécifiquement sur cette fenêtre, vérifier lequel des deux systèmes est en cause avant de supposer que c'est le mécanisme global documenté dans le skill `undo-redo`.

**`_redo_stacks[idx].clear()`** est appelé après chaque nouvelle rotation appliquée (`_apply_to_current`, ligne 718) — comportement standard (une nouvelle action efface la pile de redo), mais seulement sur l'historique **interne** ; l'historique global suit sa propre règle indépendante (voir skill `undo-redo`).

## Points d'entrée UI

Deux, tous deux conditionnés uniquement à `bool(state.images_data)` — **pas besoin de sélection** contrairement à beaucoup d'autres opérations d'image du projet, puisque `show_straighten_viewer` gère lui-même le choix de la page de départ (voir section suivante) :

1. **Menu contextuel** (clic droit mosaïque, voir skill `qt-context-menus`) — `context_menus_qt.py:415`, clé `context_menu.image.straighten`.
2. **Barre de menu** — `menubar_qt.py:202`, même clé.

Callbacks (`PanelWidget._straighten_callbacks()`, `panel_widget.py:1593`) : `save_state`, `render_mosaic`, `update_button_text`, `state` — pas de `rollback` (pas de worker à annuler en cours de route, contrairement à `rotate-flip`).

**Pas de bouton dans la colonne d'icônes malgré une entrée dans `ICON_DEFINITIONS`** (`icon_toolbar_qt.py:65`, id `"straighten"`, activé si `has_images`, tooltip `tooltip.straighten`) — le bouton existe bel et bien dans la colonne (contrairement au miroir de `rotate-flip` qui n'en a aucun) ; câblé vers `cb["show_straighten_viewer"]` (`icon_toolbar_qt.py:2167`). Donc en réalité **3** points d'entrée UI : menu contextuel, barre de menu, colonne d'icônes.

## Sélection de la page de départ — `show_straighten_viewer` (`straighten_viewer_qt.py:917`)

Ouvre sur **toutes** les images valides de la mosaïque (`is_image` et pas `is_corrupted`), navigables ensuite via ◀/▶ ou les flèches clavier — pas seulement l'image sélectionnée, contrairement à d'autres fenêtres du projet qui n'opèrent que sur la sélection courante :
- Si une sélection existe, ouvre sur la **première image sélectionnée valide** (au sens de l'ordre dans `images_data`).
- Sinon, ouvre sur la première image de la mosaïque.
- Les images corrompues sont exclues de la liste navigable entièrement (ni affichées, ni comptées dans le compteur `n / total`).

## Zoom, pan, plein écran

Vocabulaire commun aux 5 visionneuses du projet (voir skill `viewers`) : `Ctrl++`/`Ctrl+-` (zoom), `Ctrl+0` (fit to window), `Ctrl+1` (reset 100%), `F11` (plein écran), molette (zoom), clic droit maintenu (pan). Implémentation propre à ce fichier (`_StraightenImageWidget`), pas de classe partagée avec les 4 autres visionneuses — un correctif de zoom/pan dans l'une ne se propage jamais automatiquement ici.

## Traductions

`locales/fr.json` : `context_menu.image.straighten` (`"Redresser l'image..."`, ligne 133), `dialogs.straighten_viewer.title` (`"Redresser"`, résolu via `_wt()` pour le titre de fenêtre — règle UI n°7), `dialogs.straighten_viewer.instruction` (`"Tracez une ligne droite sur ce qui doit être horizontal (ou vertical) dans l'image"`), `tooltip.straighten` (tooltip du bouton colonne d'icônes). Voir skill `add-translation`.

**Absent du mode d'emploi** (`user_guide_qt.py`, aucune occurrence de "straighten") — contrairement à d'autres fonctionnalités comparables en complexité. À signaler si une tâche touche à la documentation utilisateur ; pourrait être un oubli plutôt qu'un choix délibéré (voir skill `user-guide`).

## Comment étendre

- **Ajuster la frontière horizontale/verticale** (actuellement 45° pile) : uniquement `if abs_angle <= 45:` dans `_on_line_drawn` — pas de zone tampon aujourd'hui, en ajouter une changerait le comportement pour les traits proches de la diagonale.
- **Changer l'algorithme de rotation** (ex. un autre filtre de rééchantillonnage) : `resample=Image.Resampling.BICUBIC` dans `_apply_to_current`, une seule occurrence.
- **Appliquer le redressement à plusieurs pages d'un coup** (traitement par lot façon `rotate-flip`) : n'existe pas aujourd'hui — la fenêtre est strictement page par page (`_apply_to_current` n'agit que sur `self._current_idx`). Introduire un tel mode nécessiterait de répliquer soit le pattern worker de `image_transforms_qt.py` (skill `rotate-flip`), soit de recalculer un angle par image (pas de sens géométrique évident si les pages n'ont pas la même inclinaison) — vérifier l'intention exacte avec l'utilisateur avant d'improviser.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour `StraightenViewerDialog` (non-modale déjà en place, `_wt()` pour le titre déjà en place, déconnexion `language_signal` déjà câblée via `_connect_lang`/`_disconnect_lang` sur `finished`).

## Pièges connus

- **Deux systèmes d'undo/redo empilés** (global de l'appli + interne à la fenêtre) — voir section dédiée ci-dessus ; un bug de undo/redo signalé sur cette fenêtre spécifiquement doit être diagnostiqué en tenant compte des deux avant de supposer qu'un seul est en cause.
- **`BICUBIC` explicite ici, contrairement à `rotate_entry_data`** (skill `rotate-flip`) qui n'impose aucun `resample` — différence volontaire et cohérente (angle libre vs multiples de 90° exacts), mais à ne pas harmoniser par erreur en copiant l'un vers l'autre.
- **Invalidation de cache complète (variante A)**, contrairement à `rotate_entry_data`/`flip_entry_data` (variante B partielle) — voir skill `apply-image-operation` pour la distinction exacte entre les deux variantes.
- **Premier `save_state()` sans `force=True`, second avec** — ordre inverse de ce qu'on pourrait supposer par symétrie ; suivre l'ordre exact du code plutôt que de deviner.
- **Le trait ne survit jamais à un changement de page** — `clear_line()` systématique sur navigation ◀/▶, comportement voulu (chaque page a sa propre inclinaison, pas de sens à réutiliser un trait d'une page à l'autre).
- **Aucune section dédiée dans le mode d'emploi** — à vérifier/ajouter si une tâche touche à la documentation utilisateur.
- **Pas de garde-fou sur un trait à exactement 45°** — frontière dure entre interprétation horizontale et verticale, comportement existant à ne pas changer sans consigne explicite.

## Références croisées

- `rotate-flip` — l'autre mécanisme de rotation de MosaicView (90°/miroir), qui ne partage aucun code avec ce skill malgré le mot "rotation" en commun ; comparer les deux sections "Application" pour les différences de `resample`/invalidation de cache/undo.
- `apply-image-operation` — pattern général suivi ici en variante (A) complète, contrairement à la variante (B) partielle de `rotate-flip`.
- `undo-redo` — mécanique de l'historique global de l'appli (`save_state`/`force=True`) utilisée en parallèle du second historique interne propre à ce fichier.
- `viewers` — la 5ᵉ visionneuse plein-écran du projet, vocabulaire zoom/pan/plein-écran commun aux 5 mais implémentation non partagée.
- `add-text-to-image` — architecture la plus proche de celle-ci dans le projet (fenêtre page par page, undo/redo interne empilé sur l'historique global, application synchrone sans worker) ; elle ajoute un troisième niveau d'historique (undo de frappe Qt natif par bloc de texte) absent ici.
- `clone-zone` — même famille de visionneuses d'édition, mais sans navigation entre pages (une seule entrée par ouverture de fenêtre) contrairement à celle-ci.
- `icon-toolbar` — bouton "straighten" de la colonne d'icônes, son activation (`has_images`) et son tooltip.
- `qt-context-menus` — entrée du menu contextuel clic droit.
- `comicinfo-metadata-editor` — mise à jour des dimensions de page dans `ComicInfo.xml` après redressement.
- `user-guide` — absence actuelle de section dédiée, à vérifier si une tâche touche à ce fichier.
