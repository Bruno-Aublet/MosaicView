---
name: clone-zone
description: Localiser ou modifier le tampon de clonage (Ctrl+clic pour définir la zone source, peinture au clic maintenu pour dupliquer des pixels). Utiliser dès qu'une tâche touche à clone_zone_viewer_qt.py, CloneZoneViewerDialog, ou au bouton/menu "Clonage de zone".
---

# Tampon de clonage — MosaicView

Fenêtre plein-écran dédiée reproduisant un outil de retouche classique (façon Photoshop/GIMP) : l'utilisateur définit une **zone source** par Ctrl+clic, puis peint au clic gauche maintenu pour copier des pixels de cette zone source vers l'endroit peint, avec un pinceau circulaire de taille réglable. Usage typique : effacer un artefact de scan, une tache, un texte parasite, en le recouvrant avec une texture voisine plausible.

Distinct de `page-straighten` (rotation) et `add-text-to-image` (texte superposé) — architecture proche (fenêtre non-modale, undo/redo interne empilé sur l'historique global, skill `viewers`) mais **aucun code partagé**, chaque visionneuse étant une implémentation autonome (voir skill `viewers`, avertissement général).

## Fichier unique — `modules/qt/clone_zone_viewer_qt.py`

Tout le mécanisme (widget image + calcul de décalage source/destination + tamponnage PIL + fenêtre + undo/redo) tient dans un seul fichier (~1244 lignes) :

- **`_CloneImageWidget`** (`QWidget`) — affiche l'image, gère zoom/pan, détecte Ctrl+clic (définition de la source) et le glisser du clic gauche (peinture), dessine le marqueur visuel de la source (cible rouge).
- **`CloneZoneViewerDialog`** (`QDialog`) — la fenêtre complète : toolbar zoom, zone image, barre du bas (mode source, taille du pinceau, Undo/Redo/Fermer). Contient toute la logique de calcul du tamponnage (`_apply_stamp`) et de gestion du "stroke" (un coup de pinceau, du clic au relâchement).
- **`show_clone_zone_viewer(parent, callbacks)`** — point d'entrée public.

**Particularité structurelle par rapport aux deux autres visionneuses d'édition (`page-straighten`, `add-text-to-image`) : pas de navigation entre pages.** `CloneZoneViewerDialog.__init__(self, parent, entry, callbacks)` reçoit une **seule** entrée (`entry`, pas `selected_entries`/`start_index`) — aucune flèche ◀/▶, aucun compteur `n / total` dans la toolbar. Pour retoucher une autre page, l'utilisateur doit fermer la fenêtre et la rouvrir sur l'image suivante.

## Les deux modes de source — "fixe" vs "relative"

Réglables via deux `QRadioButton` dans la barre du bas (`_radio_fixed`/`_radio_relative`, `set_mode('fixed'|'relative')` sur le widget). Contrôlent le comportement de la source **entre deux strokes**, pas pendant un même stroke — pendant un stroke en cours, les deux modes utilisent exactement le même calcul de décalage constant (`_get_effective_source`, voir section suivante) :

- **Mode fixe** (défaut) : chaque nouveau stroke repart du **même point source** Ctrl+cliqué à l'origine, quel que soit le nombre de coups de pinceau donnés depuis. Utile pour dupliquer répétitivement le même motif à plusieurs endroits.
- **Mode relatif** : le point source **avance** d'un stroke à l'autre, du même déplacement que celui effectué par le stroke précédent (calculé en fin de stroke dans `_on_paint_end`, `ddx`/`ddy` = déplacement destination cumulé, appliqué à la source). Utile pour "étirer" une texture continue sur une plus grande zone sans que la source ne finisse par chevaucher une zone déjà modifiée par un stroke antérieur.

## Calcul du décalage source/destination — `_get_effective_source` (`clone_zone_viewer_qt.py:866`)

Le cœur géométrique du mécanisme, commun aux deux modes pendant un stroke : au premier point peint d'un stroke, `dx`/`dy` = source initiale moins destination initiale (un décalage constant). Chaque point suivant du même stroke applique ce même décalage à sa propre destination — la source "suit" le pinceau à distance fixe, comme un tampon physique qu'on traînerait sur la page.

## Application d'un coup de pinceau — `_apply_stamp` (`clone_zone_viewer_qt.py:876`)

Copie un disque de pixels du "snapshot" vers l'image de travail (`_work_img`), entièrement en PIL natif (crop/paste, pas de boucle Python pixel par pixel) :

1. `r = (brush_radius - 1) / 2` — le réglage utilisateur est un **diamètre** en pixels image, converti en demi-rayon flottant pour le calcul du disque.
2. Bounding box de destination clampée aux limites de l'image (`d_left`/`d_top`/`d_right`/`d_bottom`), bounding box source correspondante calculée par le même décalage, elle aussi clampée si elle déborderait de l'image (`sc_left`/`sc_top`/`sc_right`/`sc_bottom`) — un stroke proche du bord ne plante pas, il copie simplement moins de pixels que le disque complet.
3. **Masque circulaire** (`PIL.ImageDraw.ellipse`) appliqué au collage (`dst.paste(src_crop, ..., mask=mask)`) pour un pinceau rond plutôt qu'un carré — sauf cas particulier `diamètre == 1` (`r == 0.0`) où un seul pixel est collé sans masque, optimisation qui évite de construire un masque 1×1 inutile.
4. Un seul point du snapshot est lu par appel — l'**interpolation** entre deux positions de la souris (mouvement rapide) est gérée en amont, dans `_CloneImageWidget.mouseMoveEvent`, pas ici (voir section suivante).

## Le "snapshot" — pourquoi lire une image figée pendant le stroke

**Point le plus subtil du fichier.** Au tout premier point peint d'un stroke (`_on_paint_stroke`, `if not self._stroke_dirty`), le code décide d'où lire les pixels sources pendant tout le reste du stroke :

- **Mode fixe** : `self._stroke_snapshot = self._work_img.copy()` — une **copie figée** de l'image au tout début du stroke. Sans ce gel, peindre progressivement vers la source finirait par copier des pixels déjà modifiés par ce même stroke (un tampon qui "mange sa propre queue"), produisant un artefact de bavure. La source, en mode fixe, ne bouge jamais pendant un stroke donné, donc la figer une fois au début est correct et évite une copie à chaque point peint.
- **Mode relatif** : `self._stroke_snapshot = self._work_img` — une **référence directe**, pas de copie. Le commentaire du code explicite pourquoi c'est sûr ici : la source se déplace *avec* le pinceau à un décalage constant, elle ne peut donc jamais chevaucher la destination courante pendant le même stroke (sauf si l'utilisateur définit volontairement une source très proche de la destination).

Une modification de ce fichier qui changerait la logique de décalage doit impérativement revalider cette hypothèse ("la source ne rattrape jamais la destination en mode relatif") avant de supprimer la copie du mode fixe ou de la réutiliser telle quelle pour le mode relatif.

## Interpolation pendant un mouvement rapide — `_CloneImageWidget.mouseMoveEvent`

Si la souris se déplace plus vite qu'un pas de `max(1, zoom * brush_radius * 0.5)` pixels widget entre deux événements de mouvement, le code interpole plusieurs points intermédiaires (`steps = dist / step`) et appelle `on_paint_stroke` pour chacun — sans ça, un mouvement rapide laisserait des trous non peints entre deux positions successives de la souris (le pinceau "sauterait" plutôt que de tracer un trait continu).

## Throttle d'affichage — `_display_timer`, ~30 fps

`_on_paint_stroke` ne rafraîchit le pixmap affiché (`_display_image`) que si au moins 33 ms se sont écoulées depuis le dernier rafraîchissement (`QElapsedTimer`) — l'image de travail PIL, elle, est modifiée à **chaque** point peint sans throttle (le tamponnage réel n'est jamais retardé, seul l'affichage l'est). `_on_paint_end` force un dernier rafraîchissement immédiat en fin de stroke pour rattraper un point que le throttle aurait sauté. Sans ce throttle, un stroke rapide avec un gros pinceau recomposerait le damier de transparence et reconvertirait toute l'image en `QPixmap` à chaque point peint individuel, bien plus vite que l'écran ne peut afficher — coûteux pour un gain visuel nul.

## Undo/redo — un stroke entier, pas un point peint

Comme `page-straighten`, deux niveaux empilés (historique interne + historique global de l'appli, skill `undo-redo`) — mais l'unité d'undo est **le stroke entier** (tout un coup de pinceau, du clic au relâchement), pas chaque point peint individuellement :

- `_bytes_before_stroke` est capturé une seule fois, à la **première** application du stroke (`if not self._stroke_dirty`) — tous les points peints ensuite dans le même stroke ne créent aucun point d'historique intermédiaire.
- `_on_paint_end` (relâchement du clic) commit l'image finale (`_commit_work_image`) et empile `_bytes_before_stroke` dans `self._bytes_history` — c'est **le seul moment** où un point undo est réellement créé pour ce stroke.
- Contrairement à `page-straighten`/`add-text-to-image`, pas de tuple `(bytes, snapshot_blocs)` à restaurer en plus des bytes — un undo/redo ici recharge simplement `_work_img` depuis les `bytes` restaurés (`_load_work_image`) et **restaure le marqueur visuel de la source** (`_img_widget._source_pt`) puisque celui-ci n'est pas capturé dans l'historique et pourrait sinon désynchroniser l'affichage de la cible rouge par rapport à ce que l'utilisateur avait défini.

## Points d'entrée UI

Trois, identiques dans leur structure à `page-straighten`/`add-text-to-image`, conditionnés uniquement à la présence d'images (`has_images`) — pas besoin de sélection, `show_clone_zone_viewer` gère le choix de la page :

1. **Menu contextuel** (clic droit mosaïque, skill `qt-context-menus`) — `context_menus_qt.py:420`, clé `context_menu.image.clone_zone`.
2. **Barre de menu** — `menubar_qt.py:204`, même clé.
3. **Colonne d'icônes** (skill `icon-toolbar`) — bouton id `"clone_zone"` (`icon_toolbar_qt.py:66`, icône `BTN_Clone_Zone.png`, activé si `has_images`), tooltip `tooltip.clone_zone` (skill `qt-tooltips`).

Callbacks (`PanelWidget._clone_zone_callbacks()`, `panel_widget.py:1612`) : `save_state`, `render_mosaic`, `update_button_text`, `state` — même structure que `page-straighten`.

## Sélection de la page — pas de liste navigable

Différence par rapport aux deux autres visionneuses d'édition : `show_clone_zone_viewer` (`clone_zone_viewer_qt.py:1196`) choisit **une seule** entrée avant d'ouvrir la fenêtre (pas une liste complète navigable) — la première image sélectionnée valide s'il y en a une, sinon la première image de la mosaïque. Comme les autres, exclut les images corrompues et capture `entry['_orig_mode']` une seule fois pour la reconversion de sortie.

## Zoom, pan, plein écran

Vocabulaire commun aux 5 visionneuses (skill `viewers`) : `Ctrl++`/`Ctrl+-`, `Ctrl+0` (fit), `Ctrl+1` (reset 100%), `F11` (plein écran), molette, clic droit maintenu (pan). Particularité propre à ce fichier : le curseur en forme de **cible** (`_make_crosshair_cursor`) est reconstruit dynamiquement à chaque changement de zoom **et** de taille de pinceau (`_rebuild_crosshair_cursor`), puisque son rayon visuel à l'écran dépend des deux (`brush_radius * zoom / 2`) — un curseur non reconstruit après un changement de zoom afficherait une taille de pinceau trompeuse par rapport à la zone réellement peinte.

Le marqueur visuel de la source (`_source_widget_pt`, dérivé de `_source_pt` en coordonnées image via `_image_to_widget`) est resynchronisé par `_recalc_source_widget_pt()`, appelée après pan (`mouseMoveEvent`), après chaque zoom (`set_zoom`/`reset_zoom`/`fit_to_window`), et — depuis le correctif 2026-08 (v1.7.2) — après un redimensionnement de la fenêtre (`resizeEvent`, ajouté à `_CloneImageWidget` juste avant `wheelEvent`). **Avant ce correctif**, seuls pan et zoom étaient couverts : redimensionner la fenêtre du tampon de clonage laissait la cible rouge/blanche affichée au mauvais endroit par rapport à l'image, sans affecter la position réelle de `_source_pt` (donc le clonage restait correct au pixel près, seul l'indicateur visuel dérivait). Voir le même correctif appliqué la même session à `page-crop` (pan) et `page-straighten` (pan + zoom + resize, qui n'avait aucun mécanisme de coordonnées image avant).

## Fond damier (transparence)

`_make_checker`/`_work_img_to_pixmap` — implémentation **encore une fois indépendante** (troisième copie du même algorithme après celles de `AdjustmentViewerDialog` et `text_viewer_qt.py`, voir skill `add-text-to-image`) ; aucune des trois n'appelle une fonction partagée. Une correction visuelle du damier faite dans un fichier ne se propage à aucun des deux autres.

## Traductions

`locales/fr.json`, section `clone_zone_viewer` (ligne 1048) : `title` (`"Clonage de Zone"`, résolu via `_wt()` — règle UI n°7), `instruction` (`"Ctrl+clic : définir la source  •  Clic gauche : peindre la zone clonée"`), `mode_label`/`mode_fixed`/`mode_relative`, `brush_size_label`. Clé séparée `context_menu.image.clone_zone` pour les menus, `tooltip.clone_zone` pour la colonne d'icônes. Voir skill `add-translation`.

**Absent du mode d'emploi** (`user_guide_qt.py`) — même situation que `page-straighten` et `add-text-to-image`, les 3 visionneuses d'édition d'image partagent ce manque (skill `user-guide`).

## Comment étendre

- **Changer la forme du pinceau** (actuellement toujours circulaire) : uniquement `_apply_stamp`, remplacer le masque `ImageDraw.ellipse` par une autre forme — attention à conserver le cas particulier `diamètre == 1` qui contourne le masque.
- **Ajouter un troisième mode de source** : suivre le pattern de `_get_effective_source`/la bascule `if self._img_widget._mode == 'fixed': ... else: ...` dans `_on_paint_end` — les deux points d'extension sont `_get_effective_source` (calcul pendant un stroke) et la fin de `_on_paint_end` (mise à jour de la source entre deux strokes).
- **Ajuster la fréquence du throttle d'affichage** (actuellement ~30 fps, `_display_interval_ms = 33`) : une seule constante dans `__init__`.
- **Ajouter la navigation entre pages** (absente aujourd'hui, contrairement aux deux autres visionneuses d'édition) : nécessiterait de répliquer le pattern `selected_entries`/`_current_idx`/`_prev_image`/`_next_image` de `page-straighten`/`add-text-to-image` — changement structurel notable, à ne pas entreprendre sans confirmation explicite (l'absence de navigation ici pourrait être un choix délibéré, le clonage étant une opération plus longue/minutieuse par page que redresser ou ajouter un texte).
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour `CloneZoneViewerDialog` (non-modale déjà en place, `_wt()` pour le titre déjà en place).

## Pièges connus

- **Pas de navigation entre pages** — contrairement à `page-straighten`/`add-text-to-image`, cette fenêtre ne traite qu'une seule entrée `entry` passée à la construction ; ne pas supposer l'existence d'un `_current_idx`/`selected_entries` en copiant du code depuis les deux autres visionneuses.
- **Snapshot figé en mode fixe, référence directe en mode relatif** — une modification de la logique de décalage doit revalider que "la source ne rattrape jamais la destination en mode relatif" avant de réutiliser la référence directe ailleurs ; voir section dédiée.
- **Le marqueur de source n'est pas dans l'historique undo/redo** — restauré manuellement après chaque `_undo`/`_redo` (`_img_widget._source_pt` réassigné), pas capturé dans les bytes ni dans un snapshot séparé comme le sont les blocs de texte de `add-text-to-image`.
- **Undo/redo au niveau du stroke entier, pas du point peint** — un stroke long (glisser la souris longtemps sans relâcher) ne peut être annulé qu'en un seul bloc, jamais point par point.
- **Fond damier dupliqué une troisième fois** — ni partagé avec `AdjustmentViewerDialog` ni avec `text_viewer_qt.py`.
- **Curseur cible à reconstruire à chaque changement de zoom ET de taille de pinceau** — omission facile si un nouveau contrôle de vue ou de taille de pinceau est ajouté sans repasser par `_rebuild_crosshair_cursor`.
- **Marqueur de source à resynchroniser après pan, zoom, ET redimensionnement** — les trois appellent `_recalc_source_widget_pt()` (le troisième cas, `resizeEvent`, a été ajouté en 2026-08/v1.7.2 ; absent avant, voir section "Zoom, pan, plein écran"). Tout nouveau chemin qui modifie `_offset`/`_zoom`/la taille du widget sans repasser par cette méthode réintroduirait une désynchronisation du même ordre.
- **Aucune section dédiée dans le mode d'emploi.**

## Références croisées

- `page-straighten` — architecture la plus proche (fenêtre d'édition non-modale, undo/redo interne empilé sur l'historique global, invalidation complète des caches) ; principale différence structurelle : pas de navigation entre pages ici.
- `add-text-to-image` — même famille de visionneuses ; troisième implémentation indépendante du fond damier de transparence après celle-ci et celle de `AdjustmentViewerDialog`.
- `viewers` — la 5ᵉ visionneuse plein-écran du projet, vocabulaire zoom/pan/plein-écran commun mais implémentation non partagée.
- `apply-image-operation` — pattern général suivi ici en variante (A) complète (comme `page-straighten`/`add-text-to-image`).
- `undo-redo` — mécanique de l'historique global de l'appli, niveau externe de l'empilement à deux niveaux ici (pas trois, contrairement à `add-text-to-image`).
- `icon-toolbar` — bouton "clone_zone" de la colonne d'icônes.
- `qt-context-menus` — entrée du menu contextuel clic droit.
- `qt-tooltips` — tooltip du bouton colonne d'icônes.
- `comicinfo-metadata-editor` — mise à jour des attributs de page dans `ComicInfo.xml` après un stroke.
- `user-guide` — absence actuelle de section dédiée, à vérifier si une tâche touche à ce fichier.
