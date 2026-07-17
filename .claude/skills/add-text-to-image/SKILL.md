---
name: add-text-to-image
description: Localiser ou modifier l'ajout de texte sur une image (blocs de texte riche superposés, positionnables, aplatis sur l'image PIL à l'application). Utiliser dès qu'une tâche touche à text_viewer_qt.py, TextViewerDialog, ou au bouton/menu "Insertion de texte".
---

# Ajout de texte sur une image — MosaicView

Fenêtre plein-écran dédiée où l'utilisateur clique sur l'image pour placer un ou plusieurs **blocs de texte riche** (police, taille, gras/italique/souligné, couleur avec alpha) superposés en transparence par-dessus l'image, déplaçables librement, puis "aplatis" (rendus définitivement) dans les pixels de l'image PIL au clic sur "Appliquer le texte". Usage typique : ajouter une bulle de traduction, un titre, une légende ou une correction de texte directement sur une page scannée.

Distinct des visionneuses de redressement (`page-straighten`) et de tampon de clonage (couvertes par le skill `viewers`) — architecture similaire (fenêtre page par page avec undo/redo interne) mais aucun code partagé, chaque fichier étant une implémentation autonome (voir skill `viewers`, avertissement général sur les 5 visionneuses).

## Fichier unique — `modules/qt/text_viewer_qt.py`

Tout le mécanisme tient dans un seul fichier (~1534 lignes, le plus long des 5 visionneuses en raison de la richesse de l'éditeur de texte) :

- **`_RichTextOverlay`** (`QTextEdit`) — un bloc de texte individuel, transparent, superposé sur l'image. Auto-redimensionné à son contenu (`_adjust_size`), bordure pointillée bleue (actif) ou grise (inactif) pour le distinguer visuellement, undo/redo **de frappe** natif Qt (`document().undo()/redo()`, indépendant des deux autres historiques du fichier — voir section undo/redo).
- **`_TextBlock`** — associe un `_RichTextOverlay` à sa position en coordonnées **image** (`img_pos`, pas coordonnées widget — reste correct quel que soit le zoom/pan courant). Expose `html()`/`plain_text()`/`is_empty()`.
- **`_TextImageWidget`** (`QWidget`) — affiche l'image sur fond damier (transparence) et héberge N blocs comme enfants Qt ; gère zoom/pan, placement au clic, drag & drop des blocs existants, activation (un seul bloc "actif" à la fois).
- **`TextViewerDialog`** (`QDialog`) — la fenêtre complète : toolbar navigation/zoom, barre d'options rich text (police/taille/gras/italique/souligné/couleur), zone image, barre du bas (Undo/Redo/Appliquer/Fermer).
- **`show_text_viewer(parent, callbacks)`** — point d'entrée public.

## Placer, déplacer, activer un bloc — `_TextImageWidget`

- **Nouveau bloc** : clic gauche sur une zone vide de l'image → `place_text` émis → `TextViewerDialog._on_place_text` crée le bloc via `add_block()`, lui applique le format courant (police/taille/couleur des contrôles de la barre d'options, voir `_apply_default_format_to_block`) et lui donne le focus clavier immédiatement.
- **Plusieurs blocs peuvent coexister simultanément** sur la même image (pas de limite) — chacun avec sa position, son contenu et son formatage propre.
- **Un seul bloc "actif" à la fois** (`_active_block`) : bordure bleue vs grise, seul celui-ci reçoit les changements de format des contrôles de la barre d'options (police/taille/gras/italique/souligné/couleur — voir `_active_overlay`). Cliquer sur un bloc existant l'active ; cliquer sur une zone vide en crée un nouveau qui devient actif automatiquement.
- **Déplacement d'un bloc existant** : glisser-déposer à la souris (seuil de 16 px² avant de basculer en mode drag, `mouseMoveEvent`, évite qu'un simple clic pour placer le curseur texte soit interprété comme un déplacement), ou **Ctrl+flèches** au clavier quand l'overlay a le focus — déplacement au pixel image près (`block_move` signal, `_on_block_move_signal`).
- **Détection du bloc sous la souris** (`_block_at`) : parcourt les blocs en ordre **inverse** (le dernier ajouté a priorité en cas de chevauchement), avec une marge de tolérance de 4px autour du rectangle réel de l'overlay.
- Les blocs ne sont **jamais sauvegardés séparément** — ce sont des widgets Qt éphémères qui n'existent que tant que la fenêtre de texte est ouverte sur cette page ; changer de page (`_prev_image`/`_next_image`) les détruit tous (`_clear_blocks` → `clear_blocks()` → `deleteLater()` sur chaque overlay).

## Formatage du texte

Barre d'options rich text (`_opt`, sous la toolbar navigation) : `QFontComboBox` (police), `QSpinBox` (taille, 6 à 500), 3 boutons bascule (gras/italique/souligné, avec police système utilisée pour l'aspect visuel des boutons eux-mêmes via `font-weight`/`font-style`/`text-decoration` dans leur stylesheet), bouton couleur (ouvre `QColorDialog` avec canal alpha activé — un texte peut donc être semi-transparent).

- **Appliquer un format** passe toujours par `apply_char_format(fmt)` sur l'overlay actif (`_RichTextOverlay`) — fusionne (`mergeCharFormat`) le nouveau format dans la sélection/position courante du curseur, ne remplace pas le format entier du bloc. Chaque changement de contrôle (police/taille/gras/etc.) redonne ensuite le focus à l'overlay (`ov.setFocus()`) pour que la frappe continue immédiatement après.
- **Synchronisation inverse** (`_sync_format_controls_from_block`) : quand le curseur se déplace dans un bloc ou qu'un bloc devient actif, les contrôles de la barre d'options se recalent sur le format du texte sous le curseur — protégée par le flag `_ignore_format_signals` pour éviter une boucle infinie (mettre à jour un contrôle déclenche normalement son signal, qui tenterait de réappliquer un format, qui redéclencherait une synchro...).
- **Piège sur un document vide** : `setCurrentCharFormat` seul ne suffit pas pour qu'un bloc tout juste créé (aucun caractère tapé) utilise la bonne police — il faut aussi `document().setDefaultFont(...)` (voir `_apply_default_format_to_block`, commentaire explicite dans le code sur ce point).

## Rendu final — `_render_all_blocks` (`text_viewer_qt.py:966`)

Convertit chaque bloc actif (non vide) en image PIL et le colle sur une copie de l'image de travail, **dans l'ordre où les blocs ont été ajoutés** à `_blocks` (pas d'ordre de superposition modifiable manuellement) :

1. Pour chaque bloc non vide : clone le `QTextDocument` de l'overlay (`document().clone()`), le rend dans un `QImage` ARGB32 transparent de la taille exacte du texte (`doc.drawContents`), convertit en PIL via `Image.frombytes('RGBA', ..., 'raw', 'BGRA')` — **ordre de canaux `BGRA`, pas `RGBA`**, spécifique au format mémoire natif de `QImage.Format_ARGB32` sur cette plateforme ; une erreur d'ordre de canaux ici inverserait rouge et bleu silencieusement.
2. Colle (`img.paste(text_pil, (px, py), text_pil)`, alpha du texte comme masque) sur l'image de travail à la position du bloc, **bornée à `[0, largeur-1]`/`[0, hauteur-1]`** (`px`/`py` clampés) — un bloc dont l'ancre a été déplacée hors des limites de l'image ne provoque pas d'erreur, mais peut être partiellement ou totalement hors cadre après collage.
3. Un bloc vide (`is_empty()` — seulement des espaces/rien tapé) est **silencieusement ignoré**, pas ajouté au rendu.

## Application — `_apply_text` (`text_viewer_qt.py:1206`)

Comme `page-straighten`, entièrement synchrone (pas de worker QThread, une seule image à la fois) :

1. `save_state()` (undo global, sans `force=True`) avant modification.
2. `composed = self._render_all_blocks()` puis `_commit_image(composed, entry, state)`.
3. **`_commit_image`** : gère la conversion de mode source — si le mode d'origine de l'image (`entry['_orig_mode']`, capturé à l'ouverture de la fenêtre, voir plus bas) n'a pas de canal alpha **et** que le format de fichier ne supporte pas la transparence (extension hors `.png`/`.webp`/`.avif`), aplatit le résultat RGBA sur un fond blanc opaque avant sauvegarde — évite qu'une image JPEG d'origine se retrouve avec un canal alpha invalide à l'export. Puis invalidation complète des caches (variante A du skill `apply-image-operation` : `img`/`_thumbnail`/`large_thumb_pil`/`qt_pixmap_large`/`qt_qimage_large`/`_hash`), synchronisation `ComicInfo.xml` (skill `comicinfo-metadata-editor`), `state.modified = True`.
4. Second `save_state(force=True)` après modification (redo).
5. Snapshot enregistré dans l'historique interne (voir section suivante), pile de redo interne vidée, tous les blocs de la page effacés (le texte est maintenant fusionné dans les pixels — il n'existe plus en tant que bloc éditable), image rechargée depuis les nouveaux bytes.

**`entry['_orig_mode']`** est calculé une seule fois à l'ouverture de la fenêtre pour toutes les pages (`show_text_viewer`, boucle sur `image_entries`, seulement si la clé n'existe pas encore) — pas recalculé après une première application de texte sur une page, puisque `_commit_image` ne le modifie jamais.

## Undo/redo — trois systèmes, pas deux

Encore plus stratifié que `page-straighten` (qui n'en a que deux) :

1. **Undo natif de frappe Qt**, interne à chaque `_RichTextOverlay` (`document().undo()/redo()`) — pile de `QTextDocument`, gère les modifications de caractères tant qu'un bloc n'a pas encore été "appliqué". `Ctrl+Z`/`Ctrl+Y` de la fenêtre les détournent **en priorité** vers ce niveau quand un overlay a actuellement le focus clavier (`_undo`/`_redo`, test `ov.hasFocus()` en tout premier) — l'utilisateur tapant dans un bloc s'attend à annuler sa frappe, pas une application précédente.
2. **Historique interne à la fenêtre**, par page (`self._histories`/`self._redo_stacks`, dict `idx → [(bytes_avant, snapshot_blocs), ...]`) — snapshot = liste de `(ix, iy, html)` pour reconstituer l'état des blocs (mais **note : le code ne restaure actuellement pas visuellement les blocs depuis ce snapshot** après un undo/redo, seuls les `bytes` de l'image sont restaurés et les blocs sont effacés — le `snapshot_before`/`snapshot_current` est capturé et stocké mais jamais relu ; à vérifier avant de supposer qu'annuler fait réapparaître les blocs éditables).
3. **Historique global de l'appli** (`callbacks['save_state']`, skill `undo-redo`) — comme dans `page-straighten`, chaque `_apply_text`/`_undo`/`_redo` interne pousse aussi un point dans cet historique global.

Ce triple empilement reprend exactement le pattern de `page-straighten` (deux niveaux) avec un niveau supplémentaire ajouté en amont (undo de frappe). Un bug de undo/redo signalé sur cette fenêtre doit être diagnostiqué en identifiant lequel des trois niveaux est concerné.

## Points d'entrée UI

Trois, identiques dans leur structure à ceux de `page-straighten`, conditionnés uniquement à la présence d'images (`has_images`/`bool(state.images_data)`) — pas besoin de sélection :

1. **Menu contextuel** (clic droit mosaïque, skill `qt-context-menus`) — `context_menus_qt.py:425`, clé `context_menu.image.text`.
2. **Barre de menu** — `menubar_qt.py:206`, même clé.
3. **Colonne d'icônes** (skill `icon-toolbar`) — bouton id `"text"` (`icon_toolbar_qt.py:67`, icône `BTN_Text.png`, activé si `has_images`), tooltip `tooltip.text` (skill `qt-tooltips`).

Callbacks (`PanelWidget._text_viewer_callbacks()`, `panel_widget.py:1631`) : `save_state`, `render_mosaic`, `update_button_text`, `state` — structure identique au dict de `page-straighten` (pas de `rollback`, pas de worker à annuler).

## Sélection de la page de départ

Même logique que `page-straighten` (`show_text_viewer`, `text_viewer_qt.py:1490`) : ouvre sur **toutes** les images valides de la mosaïque (navigables ◀/▶), démarre sur la première image sélectionnée si une sélection existe, sinon sur la première image de la mosaïque ; images corrompues exclues.

## Zoom, pan, plein écran

Vocabulaire commun aux 5 visionneuses (skill `viewers`) : `Ctrl++`/`Ctrl+-`, `Ctrl+0` (fit), `Ctrl+1` (reset 100%), `F11` (plein écran), molette, clic droit maintenu (pan). Particularité propre à ce fichier : **repositionner tous les blocs** (`reposition_all()`) doit être appelé après **tout** changement de zoom/pan/redimensionnement de fenêtre — sans ça les overlays resteraient à leurs anciennes coordonnées widget alors que l'image a bougé sous eux. Vérifier que tout nouveau code touchant au zoom/pan de `_TextImageWidget` appelle bien `reposition_all()`, comme le fait déjà chaque méthode existante (`set_zoom`, `reset_zoom`, `fit_to_window`, `resizeEvent`).

## Fond damier (transparence)

`_make_checker`/`_compose_on_checker` génèrent un damier gris clair/gris foncé (tuiles de 12px) et composent l'image RGBA dessus avant affichage — pattern similaire à `compose_checkerboard` de `AdjustmentViewerDialog` (skill `viewers`, `adjust-transparency`) mais **implémentation indépendante dans ce fichier**, pas un appel à la fonction partagée. Une modification de l'apparence du damier dans un fichier ne se propage pas à l'autre.

## Traductions

`locales/fr.json`, section `text_viewer` (ligne 1056) : `title` (`"Insertion de texte"`, résolu via `_wt()` pour le titre de fenêtre — règle UI n°7), `instruction` (`"Cliquez à l'endroit où vous souhaitez insérer le texte  •  Ctrl+flèches : déplacer le texte au pixel près"`), `size_label`/`color_label`, `bold_btn`/`italic_btn`/`underline_btn` (labels courts "G"/"I"/"S", pas d'icônes), `apply_btn`, `pick_color_title`. Clé séparée `context_menu.image.text` pour les menus, `tooltip.text` pour la colonne d'icônes. Voir skill `add-translation`.

**Absent du mode d'emploi** (`user_guide_qt.py`) — même situation que `page-straighten`, à signaler si une tâche touche à la documentation utilisateur (skill `user-guide`).

## Comment étendre

- **Changer l'ordre de superposition des blocs** (actuellement : ordre d'ajout, non modifiable) : `_render_all_blocks` boucle sur `self._img_widget.blocks()` dans l'ordre de la liste `_blocks` — un réordonnancement nécessiterait soit un contrôle UI dédié (glisser dans une liste, raccourci "envoyer au premier/dernier plan"), soit un tri par un critère explicite avant la boucle.
- **Restaurer visuellement les blocs après un undo/redo interne** (actuellement non fait, voir section undo/redo) : le snapshot `(ix, iy, html)` existe déjà dans `_histories`/`_redo_stacks`, il faudrait recréer des `_TextBlock`/`_RichTextOverlay` depuis ce snapshot dans `_undo`/`_redo` au lieu de `_clear_blocks()` — ne pas le faire sans confirmation explicite, ce silence pourrait être un choix délibéré (l'application "fige" le texte, pas de raison de le rendre à nouveau éditable après coup) plutôt qu'un oubli.
- **Ajouter un nouvel attribut de formatage** (ex. interlignage, alignement) : nouveau contrôle dans la barre d'options (`_build_ui`, section "barre d'options rich text"), nouveau handler `_on_xxx_changed` suivant le pattern des 3 boutons bascule existants (vérifie `_ignore_format_signals`, construit un `QTextCharFormat`/`QTextBlockFormat` selon l'attribut, appelle `apply_char_format` ou l'équivalent bloc, redonne le focus), et l'ajouter à `_sync_format_controls_from_block` pour la synchronisation inverse.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour `TextViewerDialog` (non-modale déjà en place, `_wt()` pour le titre déjà en place).

## Pièges connus

- **Trois systèmes d'undo/redo empilés** (frappe Qt native → historique interne fenêtre → historique global appli) — voir section dédiée ; diagnostiquer lequel est en cause avant de corriger un bug de undo signalé sur cette fenêtre.
- **Le snapshot de blocs stocké dans l'historique interne n'est jamais relu** — un undo restaure les `bytes` de l'image mais efface les blocs plutôt que de les recréer depuis le snapshot capturé ; ne pas supposer que l'undo "ramène" le texte éditable.
- **Ordre de canaux `BGRA`, pas `RGBA`**, dans la conversion `QImage` → PIL de `_render_all_blocks` — spécifique au format mémoire de `QImage.Format_ARGB32`, à reproduire exactement si ce bloc de code est dupliqué ailleurs.
- **Position d'un bloc non bornée avant collage, seulement au moment du collage** (`px`/`py` clampés dans `_render_all_blocks`, pas au moment du drag) — un bloc peut être déplacé visuellement hors de l'image dans l'éditeur sans avertissement, sa position réelle n'est corrigée qu'au rendu final.
- **`setCurrentCharFormat` seul ne suffit pas sur un document vide** — toujours accompagner de `document().setDefaultFont(...)` pour qu'un bloc fraîchement créé sans texte tapé utilise la bonne police dès la première frappe.
- **`reposition_all()` doit suivre tout changement de zoom/pan/taille** — omission facile si un nouveau contrôle de vue est ajouté sans repasser par les méthodes existantes de `_TextImageWidget`.
- **Fond damier dupliqué, pas partagé** avec `AdjustmentViewerDialog`/`compose_checkerboard` — une correction visuelle du damier faite dans un fichier ne s'applique pas automatiquement à l'autre.
- **Aucune section dédiée dans le mode d'emploi.**

## Références croisées

- `page-straighten` — architecture la plus proche dans le projet (fenêtre page par page, undo/redo interne empilé sur l'historique global, `BICUBIC`/rendu synchrone) ; comparer les deux pour les différences de complexité (un seul angle de rotation vs N blocs de texte riche indépendants) et le niveau d'historique supplémentaire ici (undo de frappe Qt natif).
- `clone-zone` — même famille de visionneuses d'édition (undo/redo interne + damier de transparence, chacun avec sa propre implémentation indépendante) ; sans navigation entre pages contrairement à celle-ci.
- `viewers` — la 5ᵉ visionneuse plein-écran du projet, vocabulaire zoom/pan/plein-écran commun mais implémentation non partagée ; section `AdjustmentViewerDialog`/`compose_checkerboard` pour l'autre implémentation (indépendante) du fond damier de transparence.
- `apply-image-operation` — pattern général suivi ici en variante (A) complète (comme `page-straighten`, contrairement à `rotate-flip`).
- `undo-redo` — mécanique de l'historique global de l'appli, le niveau le plus externe des trois empilés ici.
- `icon-toolbar` — bouton "text" de la colonne d'icônes.
- `qt-context-menus` — entrée du menu contextuel clic droit.
- `qt-tooltips` — tooltip du bouton colonne d'icônes.
- `comicinfo-metadata-editor` — mise à jour des dimensions/attributs de page dans `ComicInfo.xml` après application du texte.
- `user-guide` — absence actuelle de section dédiée, à vérifier si une tâche touche à ce fichier.
