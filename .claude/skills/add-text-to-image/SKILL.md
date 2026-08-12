---
name: add-text-to-image
description: Localiser ou modifier l'ajout de texte sur une image (blocs de texte riche superposés, positionnables, aplatis sur l'image PIL à l'application). Utiliser dès qu'une tâche touche à text_tool_qt.py, l'outil "text" de la barre d'outils flottante de la visionneuse principale, ou le bouton/menu "Insertion de texte".
---

# Ajout de texte sur une image — MosaicView

4e outil migré (v1.7.3) dans la barre d'outils flottante de la visionneuse principale (`ImageViewer`, `image_viewer_qt.py`), après crop/straighten/clone — voir skill `viewers` (section "Fusion progressive") pour l'architecture générale en mixins et les décisions transversales. L'utilisateur clique sur l'image pour placer un ou plusieurs **blocs de texte riche** (police, taille, gras/italique/souligné, couleur avec alpha) superposés en transparence par-dessus l'image, déplaçables librement, puis "aplatis" (rendus définitivement) dans les pixels de l'image PIL au clic sur "Valider". Usage typique : ajouter une bulle de traduction, un titre, une légende ou une correction de texte directement sur une page scannée.

**L'ancienne fenêtre dédiée `TextViewerDialog`/`text_viewer_qt.py` a été entièrement supprimée** (v1.7.3, après migration complète et confirmation utilisateur) — plus aucune référence, plus d'historique interne séparé par fenêtre. Tout vit désormais dans `modules/qt/text_tool_qt.py`.

## Fichier unique — `modules/qt/text_tool_qt.py`

Comme `crop_tool_qt.py`/`straighten_tool_qt.py`/`clone_tool_qt.py`, deux mixins hérités par les deux classes centrales de `image_viewer_qt.py` (`class _ViewerCanvas(CropCanvasMixin, StraightenCanvasMixin, CloneCanvasMixin, TextCanvasMixin, QLabel)` / `class ImageViewer(CropViewerMixin, StraightenViewerMixin, CloneViewerMixin, TextViewerMixin, QDialog)`) — voir skill `viewers` pour la règle architecturale absolue (jamais de code d'outil dans `image_viewer_qt.py`).

- **`_ColorPickerDialog`** (`QDialog`) + `_ColorSwatch`/`_HueSatSquare`/`_ValueSlider` — sélecteur de couleur maison, voir section dédiée plus bas.
- **`_RichTextOverlay`** (`QTextEdit`) — un bloc de texte individuel, transparent, superposé sur `_ViewerCanvas`. Auto-redimensionné à son contenu (`_adjust_size`), bordure pointillée bleue (actif/éditable) ou grise (figé, outil désélectionné). Signaux `content_changed`/`block_move(dx,dy)`/`activated`.
- **`_TextBlock`** — associe un `_RichTextOverlay` à sa position en coordonnées **image** (`img_pos`, stable indépendamment du zoom/pan), plus `display_scale` (zoom au moment de l'édition) et `top_y_offset_img` (décalage vertical figé une seule fois — voir section positionnement).
- **`_TextOptionsPanel`** (`QWidget`) — panneau flottant de formatage (police/taille/gras/italique/souligné/couleur), même mécanisme que `_StraightenAnglePanel`/`_CloneOptionsPanel` (skill `viewers`), instancié dans `_ViewerToolbar.__init__` (`viewer_toolbar_qt.py`) et accessible via `self._toolbar._text_panel`.
- **`TextCanvasMixin`** — état + interactions souris, hérité par `_ViewerCanvas`.
- **`TextViewerMixin`** — rendu final, validation, persistance par page, hérité par `ImageViewer`.

## Placer, déplacer, activer un bloc — `TextCanvasMixin`

- **Nouveau bloc** : `text_mouse_press` (appelé depuis `_ViewerCanvas.mousePressEvent` quand `active_tool == "text"`) — clic sur une zone vide → `add_text_block(ix, iy)` crée l'overlay, lui applique le format par défaut des contrôles courants (`_TextOptionsPanel.apply_default_format_to_block`) puis l'active et lui donne le focus. **Ordre d'appel critique** : format appliqué + `overlay.show()` **avant** `_text_activate_block()` — l'inverse provoquait un access violation natif (currentCharFormat() sur un QTextEdit encore caché).
- **Plusieurs blocs peuvent coexister** sur la même page (pas de limite), chacun avec sa position/contenu/formatage propre, stockés dans `_ViewerCanvas._text_blocks` (liste).
- **Un seul bloc "actif" à la fois** (`_text_active_block_ref`) : bordure bleue vs grise, seul lui reçoit les changements de format de `_TextOptionsPanel`. Cliquer sur un bloc existant l'active ; cliquer sur une zone vide en crée un nouveau qui devient actif.
- **Bloc vide abandonné retiré automatiquement** : si un clic (sur un bloc existant ou une zone vide) survient pendant qu'un bloc actif est resté vide (`is_empty()`), il est supprimé via `_remove_block()` avant de traiter le nouveau clic — évite l'accumulation de `_RichTextOverlay` fantômes qui déstabilisait Qt (cause racine des access violations lors d'un changement rapide de zone de frappe, voir section pièges).
- **Déplacement d'un bloc existant** : glisser-déposer à la souris (seuil de 16 px² avant bascule en mode drag, `text_mouse_move`/`text_mouse_press`), ou **Ctrl+flèches** au clavier quand l'overlay a le focus (`_RichTextOverlay.keyPressEvent` → signal `block_move` → `_on_text_block_move_signal`).
- **Détection du bloc sous la souris** (`_text_block_at`) : parcourt `_text_blocks` en ordre **inverse** (le dernier ajouté a priorité en cas de chevauchement), marge de tolérance 4px.
- **Clic hors image** : coordonnées bornées via `_clamp_to_image` avant `add_text_block` — un bloc n'est jamais créé à une position aberrante hors des limites de l'image.
- **Désélection de l'outil "text"** (icône re-cliquée, ou un autre outil activé) : les blocs **ne sont pas effacés** (même principe que le rectangle de crop conservé en gris) — `_text_set_frozen(True)` les fige (`overlay.setReadOnly(True)`, plus de focus possible, bordure grise). Resélectionner l'outil les rend à nouveau éditables.

## Formatage — `_TextOptionsPanel`

Panneau flottant sous `_ViewerToolbar`, visible **uniquement** quand `active_tool == "text"` **et** qu'un bloc est actif (`set_visible_for_tool`, appelé depuis `_ViewerToolbar.set_active_tool` et `_on_text_block_activated`) — un outil sélectionné sans bloc actif n'affiche pas la barre de formatage. Contrôles : `QFontComboBox`, `QSpinBox` (taille 6-500), 3 `QPushButton` checkable (gras/italique/souligné), bouton couleur carré ouvrant `_ColorPickerDialog`.

- **Appliquer un format** passe par `_RichTextOverlay.apply_char_format(fmt)` (`mergeCharFormat` sur la sélection/position courante), puis redonne le focus à l'overlay pour que la frappe continue.
- **Synchronisation inverse** (`sync_from_block`) : différée via un **timer unique coalescé** (`request_sync`/`_run_pending_sync`, `QTimer(singleShot=True)`) plutôt que des `QTimer.singleShot()` empilés — `content_changed`/`activated` sont émis en pleine réentrance d'un événement Qt natif (frappe, changement de focus), et une resynchronisation immédiate ou des callbacks empilés sur un état périmé provoquaient des access violations natifs (voir section pièges). Garde `hasFocus()` avant `fmt.fontFamily()` — le seul appel identifié comme cause de crash en cas de changement de focus pendant que le callback était en vol.
- **Taille de police — deux échelles** : la spinbox affiche une taille **logique** (voulue en pixels sur l'image finale) ; le document Qt affiché à l'écran utilise cette taille × `block.display_scale` (zoom courant, pour rester lisible pendant l'édition à n'importe quel niveau de zoom). `_text_render_all_blocks` divise par ce même facteur au rendu final. **`display_scale` est reposé au zoom courant à chaque changement de taille** (`_on_font_size_changed`), pas figé à la création — seul `top_y_offset_img` (position) est figé une fois pour toutes, pas `display_scale` (taille).

## Sélecteur de couleur maison — `_ColorPickerDialog`

**`QColorDialog` a été abandonné** après plusieurs tentatives infructueuses de le rendre lisible en mode sombre (ses widgets internes sont peints nativement par l'OS ou cassés par le moteur `QStyleSheetStyle` du stylesheet global de l'appli) — remplacé par une fenêtre 100% maison qui suit `get_current_theme()` comme n'importe quelle autre fenêtre du projet. Non-modale (`setModal(False)`, résultat via signal `color_picked`).

- **`_HueSatSquare`** : nuancier 2D (teinte 0-359° en abscisse, saturation 0-255 en ordonnée), dégradé peint à pleine luminosité, curseur = cercle blanc/noir.
- **`_ValueSlider`** : barre verticale de luminosité (V du HSV), dégradé noir→teinte pleine.
- **Grille de 16 couleurs de base** (`_BASIC_COLORS`, `_ColorSwatch`), champ hexadécimal, 4 curseurs+spinbox R/V/B/Alpha.
- **Piège corrigé — clic sur le nuancier restait noir** : la couleur initiale (`#000000`, texte par défaut) a `value()=0` en HSV ; `_ValueSlider._val` héritait de 0, donc `QColor.fromHsv(h, s, 0)` restait toujours noir quelle que soit la teinte cliquée. `_on_hue_sat_changed` force `val=255` si la valeur courante est `<= 0`.
- **Piège corrigé — bandes noires derrière les labels** : dès qu'un stylesheet est actif sur le `QDialog` parent, un `QLabel` dont le style ne pose que `color` (pas de fond) retombe sur un fond opaque par défaut du moteur `QStyleSheetStyle` — chaque label doit explicitement poser `background: transparent;` dans son propre stylesheet.
- **Piège corrigé — spinbox R/V/B/Alpha restaient noires malgré un style posé** : un style `QLineEdit { ... }` ne cible que les vrais `QLineEdit` (comme le champ hexadécimal) — un `QSpinBox` est une classe différente même s'il héberge un `QLineEdit` interne. Style `QSpinBox { ... }` séparé et explicite nécessaire.
- **Piège corrigé — carré de couleur active empiétait sur le texte du dessous** : `QLabel` de taille fixe (`setFixedSize`) avec seulement `background` en stylesheet peut voir sa taille ignorée en présence d'un stylesheet actif sur un ancêtre — nécessite `setAttribute(Qt.WA_StyledBackground, True)`.
- **Piège corrigé — fenêtre qui flashe à une position puis se déplace, mal centrée** : `adjustSize()` doit être appelé **avant** `position_dialog_on_parent(dlg, self._viewer)` (pas dans `showEvent`) — sans ça `dialog.height()` vaut encore une hauteur par défaut minime au moment du calcul de centrage. Centrée sur `self._viewer` (`ImageViewer`, la visionneuse), pas sur `self` (`_TextOptionsPanel`, petit panneau flottant qui donnerait une position peu pertinente).
- Traductions : `dialogs.text_viewer.pick_color_title` (titre, via `_wt()`), section `dialogs.color_picker.*` (`basic_colors_label`/`hex_label`/`red_label`/`green_label`/`blue_label`/`alpha_label`), propagées aux 45 langues.

## Positionnement — point cliqué = début horizontal, centre vertical figé

Décision explicite de l'utilisateur après plusieurs itérations ratées (centrage horizontal essayé puis abandonné — dérive pendant la frappe, largeur de wrap mal calculée) :

- **Horizontal** : `block.img_pos` est le bord **gauche** du texte (le point cliqué = début de la ligne, comme dans n'importe quel éditeur), jamais recentré.
- **Vertical** : le point cliqué reste le **centre vertical** du bloc, mais **tel qu'il était au moment du placement initial** — `_TextBlock.top_y_offset_img`, calculé une seule fois (`(overlay.height() / 2) / display_scale_à_la_création`, en pixels **image**) et **jamais recalculé** ensuite, ni à l'écran (`_text_reposition_block`) ni au rendu final (`_text_render_all_blocks`). Recalculer ce décalage avec la hauteur courante (après plusieurs lignes tapées) faisait dériver le bloc de plus en plus loin du point cliqué à mesure que le texte s'allongeait — bug corrigé en figeant la valeur une fois pour toutes et en la persistant (`_save_text_for_current_page`/`_restore_text_for_page`).
- **Largeur de wrap** (`_RichTextOverlay.set_max_width`) : recalculée à chaque repositionnement (`_text_reposition_block`) comme la distance entre le point cliqué et le **bord droit réel de l'image** (`display_offset_x + display_width`), pas du canvas — le texte revient à la ligne en l'atteignant.
- **`WrapAnywhere`** (pas `WordWrap`) : un mot unique très long (texte de test sans espaces) continuait sinon indéfiniment sur une seule ligne sans jamais revenir à la ligne.
- **Calcul de largeur** : `doc.idealWidth()` (largeur réellement nécessaire au contenu), **pas** `doc.documentLayout().documentSize().width()` qui retourne toujours `textWidth` une fois celui-ci fixé, pas la largeur du contenu — utiliser l'un à la place de l'autre inversait le sens du wrap automatique.

## Rendu final — `TextViewerMixin._text_render_all_blocks`

Convertit chaque bloc non vide en image PIL et le colle sur une copie de l'image de travail, dans l'ordre de `_ViewerCanvas._text_blocks` (ordre d'ajout, pas modifiable manuellement) :

1. Clone le `QTextDocument` de l'overlay, repose la même largeur de wrap que celle vue à l'écran (`block.overlay._max_width`) — sinon le rendu ignorerait le retour à la ligne automatique et produirait un texte débordant incohérent avec ce qui a été validé visuellement.
2. Dézoome les tailles de police (`scale = 1.0 / block.display_scale`) pour retrouver la taille logique voulue par l'utilisateur, indépendamment du zoom courant au moment de l'édition.
3. Rend dans un `QImage` ARGB32 transparent puis convertit en PIL via `Image.frombytes('RGBA', ..., 'raw', 'BGRA')` — **ordre de canaux `BGRA`, pas `RGBA`**, spécifique au format mémoire natif de `QImage.Format_ARGB32` ; une erreur d'ordre ici inverserait rouge et bleu silencieusement.
4. Position = `img_pos.x()` / `img_pos.y() - top_y_offset_img` (valeur **figée**, jamais recalculée ici), bornée `[0, largeur-1]`/`[0, hauteur-1]` avant `img.paste(text_pil, (px, py), text_pil)`.
5. Un bloc vide (`is_empty()`) est silencieusement ignoré.

## Validation — `TextViewerMixin.perform_text`/`validate_text`

Entièrement synchrone (pas de worker QThread), suit le pattern (A) complet du skill `apply-image-operation`, undo/redo **unifié** avec l'historique global du panneau (pas d'historique interne séparé, contrairement à l'ancienne fenêtre) :

1. `validate_text()` (branché sur le bouton "Valider" flottant partagé, `_VALIDATE_KEYS["text"] = "buttons.validate_text"`, skill `viewers`) — si aucun bloc non vide, `MsgDialog` d'avertissement (`messages.warnings.no_text_block`) et arrêt ; sinon `perform_text()`.
2. `save_state()` (undo global, sans `force=True`) avant modification.
3. `composed = self._text_render_all_blocks(base_img)` puis conversion de mode source si nécessaire (`entry['_orig_mode']`, capturé une seule fois à la première application de texte sur la page — pas à l'ouverture de la visionneuse) : si le mode d'origine n'a pas de canal alpha **et** que le format de fichier ne le supporte pas (hors `.png`/`.webp`/`.avif`), aplatit sur fond blanc opaque avant sauvegarde.
4. Invalidation complète des caches (variante A du skill `apply-image-operation`), synchronisation `ComicInfo.xml` (skill `comicinfo-metadata-editor`), `state.modified = True`, `save_state(force=True)` après modification.
5. `self._canvas.clear_text_blocks()` (tous les blocs de la page détruits, le texte est maintenant fusionné dans les pixels), `_text_blocks_by_page.pop(current_idx)`, `display_image()`, `self._toolbar.refresh_undo_redo_state()`.

## Undo/redo — unifié, pas de système séparé

Contrairement à l'ancienne `text_viewer_qt.py` (trois systèmes empilés), l'outil migré n'a que **deux** niveaux :

1. **Undo natif de frappe Qt**, local à chaque `_RichTextOverlay` tant qu'il a le focus (`document().undo()/redo()`, détourné via `Ctrl+Z`/`Ctrl+Y` dans `_RichTextOverlay.keyPressEvent`) — comportement standard de `QTextEdit`, pas un système ajouté. Concerne uniquement la frappe non encore validée.
2. **Historique global de l'appli** (`callbacks['save_state']`, skill `undo-redo`) — un seul point créé à la validation (clic sur "Valider"), comme crop/straighten/clone. Décision explicite (`idees.txt` #3, discussion de conception) : pas de point d'historique à chaque frappe.

Plus d'historique interne par page à la `ImageViewer` — l'ancienne `text_viewer_qt.py` en avait un (snapshot jamais relu de toute façon). La persistance du travail **non validé** (blocs en cours d'édition, changement de page) passe entièrement par `_text_blocks_by_page`, pas par un niveau d'undo.

## Persistance par page — `_text_blocks_by_page`

`ImageViewer._text_blocks_by_page: dict[int, list[tuple]]`, défini dans `__init__` comme `_crop_by_page`/`_straighten_by_page` (skill `viewers`), mais liste de N blocs par page au lieu d'une seule géométrie :

- **`_save_text_for_current_page()`** (`TextViewerMixin`) : appelée dans `navigate()` avant de changer `current_idx` — mémorise pour chaque bloc `(img_x, img_y, html, display_scale, top_y_offset_img)`. `display_scale` et `top_y_offset_img` doivent être conservés tels quels (pas recalculés) : le HTML sérialisé contient des tailles de police déjà mises à l'échelle de ce facteur, et le décalage vertical doit rester la valeur figée au tout premier placement.
- **`_restore_text_for_page(idx)`** : `clear_text_blocks()` puis recrée un `_TextBlock` par entrée sauvegardée via `add_text_block()`, **écrase ensuite** `display_scale`/`top_y_offset_img` avec les valeurs sauvegardées (`add_text_block` les repose par défaut au zoom courant / à la hauteur du widget encore vide — il faut les remplacer par les vraies valeurs d'origine), `overlay.setHtml(html)`, puis repositionne. Les blocs restaurés sont figés (`_text_set_frozen`) si l'outil "text" n'est pas l'outil actif au moment du retour sur la page.
- Contribue à `_has_unvalidated_work()` (skill `viewers`, confirmation de fermeture) au même titre que `_crop_by_page`/`_straighten_by_page`.
- **Limitation connue partagée avec crop/straighten** (skill `viewers`) : pas redessiné en mode double page (`_display_double_page` ne redessine aucun overlay).

## Pièges connus (access violations natifs, tous corrigés)

Trois crashs distincts rencontrés et corrigés en conditions réelles, tous diagnostiqués par prints avant correction (voir historique du chantier) :

1. **Premier clic de placement** : `_text_activate_block()` appelée avant que l'overlay soit formaté/affiché → `currentCharFormat()` sur un `QTextEdit` encore caché plantait Qt nativement. Fix : format + `show()` **avant** activation (voir `add_text_block`).
2. **Pendant la frappe** : `content_changed` émis synchrone depuis `keyPressEvent`, resynchroniser `_TextOptionsPanel` immédiatement en pleine réentrance plantait. Fix : différé via `request_sync`/timer unique coalescé.
3. **Changement rapide de zone de frappe** : cause racine = accumulation de blocs vides jamais nettoyés, chacun avec son focus/timer en vol — terrain instable pour Qt. Fix réel : `_remove_block()` du bloc actif vide avant tout nouveau clic (`text_mouse_press`), **pas** seulement la garde `hasFocus()` sur `fmt.fontFamily()` (qui protège un symptôme différent, plus mineur, du même type de crash).

**Si un nouveau crash Qt natif (access violation, pas d'exception Python) apparaît sur cet outil** : vérifier en premier si des blocs vides s'accumulent, puis si un callback différé peut s'exécuter sur un `QTextCharFormat`/overlay déjà périmé — ce sont les deux causes déjà rencontrées ici, avant d'explorer une piste nouvelle.

## Points d'entrée UI

Trois, recâblés vers le nouvel outil migré, conditionnés à la présence d'images (`has_images`) :

1. **Menu contextuel** (clic droit mosaïque, skill `qt-context-menus`) — clé `context_menu.image.text`.
2. **Barre de menu** — même clé, callback `"show_text_viewer"`/`"text"` dans `menubar_callbacks_qt.py`, tous deux pointés vers `mw._text_selected_image`.
3. **Colonne d'icônes** (skill `icon-toolbar`) — bouton id `"text"`, icône `BTN_Text.png`, tooltip `tooltip.text`/`viewer.toolbar_text_tooltip` (skill `qt-tooltips`).

`PanelWidget._text_selected_image()` (`panel_widget.py`) : ouvre `ImageViewer(..., initial_tool="text")` — première image sélectionnée si une sélection valide existe, sinon première image de la mosaïque, images corrompues exclues (même logique que `_straighten_selected_image`/`_clone_selected_image`). Remplace l'ancienne `_text_viewer_callbacks()`, retirée (devenue morte).

4e icône de `_ViewerToolbar` (`viewer_toolbar_qt.py`) : `BTN_Text.png`, `tool_id="text"`, tooltip enrichi (`viewer.toolbar_text_tooltip` + `dialogs.text_viewer.instruction` sur une seconde ligne).

## Traductions

`locales/*.json` : section `dialogs.text_viewer` — `instruction`, `size_label`/`color_label`, `bold_btn`/`italic_btn`/`underline_btn` (labels courts "G"/"I"/"S"), `pick_color_title` (via `_wt()`). Section `dialogs.color_picker` (6 clés, ajoutées v1.7.3 pour `_ColorPickerDialog`) : `basic_colors_label`/`hex_label`/`red_label`/`green_label`/`blue_label`/`alpha_label`. `buttons.validate_text` (bouton flottant). `viewer.toolbar_text_tooltip` (icône barre d'outils). `context_menu.image.text`/`tooltip.text` (menu contextuel/colonne d'icônes, inchangées). `messages.warnings.no_text_block`/`messages.errors.text_failed` (validation). Toutes propagées aux 45 langues (39 naturelles + tlh/sjn/qya latin + 3 CSUR) — voir skill `add-translation`.

**Clés mortes retirées** (ancienne fenêtre supprimée) : `dialogs.text_viewer.title`, `dialogs.text_viewer.apply_btn`.

**Absent du mode d'emploi** (`user_guide_qt.py`) — même situation que les 3 autres outils migrés, à signaler si une tâche touche à la documentation utilisateur (skill `user-guide`).

## Comment étendre

- **Changer l'ordre de superposition des blocs** (actuellement : ordre d'ajout) : `_text_render_all_blocks` boucle sur `self._canvas._text_blocks` dans l'ordre de la liste — nécessiterait un contrôle UI dédié ou un tri explicite avant la boucle.
- **Ajouter un nouvel attribut de formatage** (ex. interlignage, alignement) : nouveau contrôle dans `_TextOptionsPanel.__init__`, nouveau handler `_on_xxx_changed` suivant le pattern des 3 boutons bascule existants (garde `_ignore_format_signals`, construit un `QTextCharFormat`/`QTextBlockFormat`, appelle `apply_char_format` ou l'équivalent bloc, redonne le focus), l'ajouter à `sync_from_block` pour la synchronisation inverse et à `apply_default_format_to_block` pour le format initial d'un bloc neuf.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour `_ColorPickerDialog` (non-modale, thème, retraduction, `_wt()` pour le titre — déjà en place).

## Références croisées

- `viewers` — architecture générale de la fusion progressive (mixins CanvasMixin/ViewerMixin, `_ViewerToolbar`, bouton "Valider" partagé, undo/redo unifié, persistance par page, piège overlays interactifs pan/zoom/resize) ; ce skill-ci ne documente que ce qui est spécifique à l'outil texte.
- `page-crop`, `page-straighten`, `clone-zone` — les 3 autres outils migrés, même pattern de mixins, à comparer pour la complexité relative (crop/straighten = une seule géométrie par page, clone = pas de persistance, texte = N blocs persistés).
- `apply-image-operation` — pattern général d'invalidation de caches suivi ici en variante (A) complète.
- `undo-redo` — mécanique de l'historique global de l'appli, seul niveau externe restant (plus d'historique interne séparé).
- `icon-toolbar` — bouton "text" de la colonne d'icônes.
- `qt-context-menus` — entrée du menu contextuel clic droit.
- `qt-tooltips` — tooltips de l'icône colonne d'icônes et de l'icône de la barre d'outils flottante (`OverlayTooltip` uniquement).
- `comicinfo-metadata-editor` — mise à jour des dimensions/attributs de page dans `ComicInfo.xml` après validation.
- `add-translation` — méthode de propagation des clés `dialogs.color_picker.*` aux 45 langues.
- `user-guide` — absence actuelle de section dédiée, à vérifier si une tâche touche à ce fichier.
