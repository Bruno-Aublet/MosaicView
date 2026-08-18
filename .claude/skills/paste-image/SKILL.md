---
name: paste-image
description: Localiser ou modifier l'outil "Coller une image" de la barre d'outils flottante de la visionneuse principale — collage depuis le presse-papiers système, glisser-déposer depuis une mosaïque ou l'Explorateur Windows, Ctrl+C/Ctrl+V dédiés à la visionneuse. Utiliser dès qu'une tâche touche à paste_image_tool_qt.py, à l'icône "paste_image" de la barre, ou au grisage live selon le contenu du presse-papiers.
---

# Coller une image — MosaicView

17e outil de la barre d'outils flottante de la visionneuse principale (`ImageViewer`, `image_viewer_qt.py`), premier outil entièrement neuf depuis les formes — voir skill `viewers` (section "Architecture en modules séparés") pour l'architecture générale en mixins et les décisions transversales, et section "Le cas de coller une image" pour l'intégration dans la barre. L'utilisateur colle une image provenant du presse-papiers système (bitmap ou fichier image unique), ou la glisse depuis n'importe quel panneau de la mosaïque ou l'Explorateur Windows, directement sur la page affichée. L'image apparaît centrée, taille initiale = un quart de la page ; poignées de redimensionnement/rotation identiques à l'outil formes. Plusieurs images peuvent être posées et accumulées avant validation. Usage typique : incruster un logo, un tampon, un correctif visuel sur une page.

## Fichier unique — `modules/qt/paste_image_tool_qt.py`

Deux mixins hérités par les deux classes centrales de `image_viewer_qt.py` (`class _ViewerCanvas(..., PasteImageCanvasMixin, QLabel)` / `class ImageViewer(..., PasteImageViewerMixin, QDialog)`) — voir skill `viewers` pour la règle architecturale absolue (jamais de code d'outil dans `image_viewer_qt.py`).

- **`_PastedImage`** — modèle d'une image posée : porte le **bitmap PIL RGBA source** (jamais redimensionné en PIL au fil des manipulations, seul le rectangle englobant `(ix1,iy1)`/`(ix2,iy2)` en coordonnées **image** et `angle` changent, même principe que le pixmap de page affichée lui-même — mise à l'échelle différée à l'affichage). `center_img()`/`normalized_img_rect()` identiques dans leur rôle à `_Shape` (skill `draw-shapes`).
- **`PasteImageCanvasMixin`** — état + interactions souris/clavier, hérité par `_ViewerCanvas`.
- **`PasteImageViewerMixin`** — rendu final, validation, persistance par page, hérité par `ImageViewer`.
- **AUCUN panneau d'options flottant** (`_PasteImageOptionsPanel` n'existe pas) — décision explicite utilisateur, aucun réglage à part les poignées et les boutons "Valider"/"Annuler" partagés. Le bouton "Valider" retombe donc directement sous la barre (`panel: None` dans `_update_validate_btn_state()`), même cas que `crop`.

## Poignées, rotation, grisage hors-limites — mécanique PARTAGÉE avec `draw-shapes`

Toute la mécanique de manipulation est dupliquée depuis `shapes_tool_qt.py` (mêmes noms de mode `'tl'`/`'tr'`/`'bl'`/`'br'`/`'left'`/`'right'`/`'top'`/`'bottom'`/`'rotate'`/`'move'`, même tolérance de détection 10px, même convention d'angle horaire/`QPainter.rotate()`) — voir skill `draw-shapes` (sections "Poignées, déplacement", "Rotation — mécanisme complet") pour le détail complet des calculs, valable ici à l'identique :

- **Poignées de COIN conservent les proportions d'origine** (coin opposé = pivot fixe, magnitude du redimensionnement dérivée du ratio largeur/hauteur d'origine, signe dérivé de la position souris) ; **poignées de BORD (milieu) déforment librement** — `PasteImageCanvasMixin._apply_paste_resize`.
- **Curseur de rotation dédié** : `_build_rotate_cursor()` (fonction module, `shapes_tool_qt.py`, importée telle quelle) — un curseur custom (arc + tête de flèche, `QPainter`, mis en cache au niveau module) remplace `Qt.PointingHandCursor`, Qt n'ayant pas de curseur de rotation cross-plateforme. **Une seule source de vérité pour ce curseur** entre les deux outils — toute modification de son apparence se fait dans `shapes_tool_qt.py` uniquement.
- **Grisage de la portion hors des limites de la page** : `_paint_out_of_page_bounds_overlay()` (fonction module, `shapes_tool_qt.py`, importée telle quelle) — assombrit (remplissage gris ~82% d'opacité + contour pointillé rouge) tout ce qui dépasse du rectangle de la page affichée, signalant que cette partie sera tronquée à l'aplatissage. Calcul exact via `QPainterPath.subtracted()` (reste correct même avec une rotation non nulle). Coins de l'image collée fournis en repère **écran non tourné** (indépendant de tout `painter.rotate()` déjà refermé) : `PasteImageCanvasMixin._paint_one_pasted_image` calcule les 4 coins tournés via `_paste_rotate_point_around` avant l'appel.
- **Déplacement clavier** : `ImageViewer._shape_key_nav` (`image_viewer_qt.py`) gère **les deux outils** dans une seule méthode partagée (déplace `_shape_active` si `active_tool == "shapes"`, ou `_pasted_image_active` si `active_tool == "paste_image"` — les deux objets exposent les mêmes champs `ix1/iy1/ix2/iy2`) ; câblé sur `QShortcut(Qt.Key_Left/Right/Up/Down)` au niveau `ImageViewer.__init__`, pas sur le canvas (`setFocusPolicy(Qt.NoFocus)`).
- **Suppr/Backspace** (`ImageViewer._on_shape_delete_key`, même méthode que pour "shapes") : efface UNIQUEMENT l'image collée **sélectionnée** (`canvas._pasted_image_active`), pas toutes.
- **Échap** (`ImageViewer._on_escape`) : sans tracé/sélection particulière à annuler, efface TOUTES les images collées de la page courante d'un coup (`clear_pasted_images()`), même principe radical que "shapes".

**Piège si le comportement des poignées ou du curseur de rotation doit changer** : modifier `shapes_tool_qt.py` (source unique) répercute automatiquement le changement sur les deux outils via l'import — ne jamais dupliquer `_build_rotate_cursor()`/`_paint_out_of_page_bounds_overlay()` dans `paste_image_tool_qt.py`. La logique de redimensionnement par coin/bord, elle, EST dupliquée (`_apply_paste_resize` vs `_apply_shape_resize`) faute de classe de base commune entre les deux mixins — un changement de règle doit être répliqué manuellement dans les deux fichiers.

## Grisage LIVE de l'icône selon le contenu du presse-papiers

Seul outil de la barre grisé selon un état **externe changeant en direct**, pas selon le format de la page affichée (contrairement à `compression`/`transparency`) :

- **`clipboard_qt.py::clipboard_has_single_image()`** — teste que le presse-papiers contient EXCLUSIVEMENT une image : soit un bitmap (`CF_DIB`), soit un `CF_HDROP` pointant vers UN SEUL fichier dont l'extension figure dans `IMAGE_EXTS` (constante module, réutilisée aussi par `paste_from_system_clipboard`). Lecture seule (ouvre/ferme le presse-papiers juste pour tester les formats disponibles, sans extraction) — pensée pour être appelée en boucle. Si le presse-papiers contient une image ET autre chose (plusieurs fichiers, un fichier non-image, une image + du texte), retourne `False` — pas de tentative de n'utiliser que la partie image.
- **`clipboard_qt.py::get_clipboard_single_image()`** — retourne l'objet PIL réel si `clipboard_has_single_image()` est vrai, sinon `None`. Utilisée au moment de coller réellement (pas seulement tester).
- **`QClipboard.dataChanged`** connecté dans `_ViewerToolbar.__init__` (`self._clipboard = QApplication.clipboard(); self._clipboard.dataChanged.connect(self._refresh_paste_image_button_state)`) — **déconnecté explicitement dans `ImageViewer.closeEvent`** via `self._toolbar.disconnect_paste_image_clipboard_watch()`, avant tout autre nettoyage de la fenêtre. **Piège** : un signal Qt global (`QApplication.clipboard()`) laissé connecté à une barre détruite provoquerait un `RuntimeError` au prochain changement de presse-papiers, même après la fermeture de la visionneuse.
- **`_refresh_paste_image_button_state()`** (`_ViewerToolbar`) — grise/dégrise via `_ToolButton.set_enabled_state()` (même mécanisme PIL que `compression`/`transparency`) et rafraîchit le tooltip à deux états (`_update_paste_image_tooltip()`, texte différent activé/désactivé, comme `_update_compression_tooltip`).

## Trois points d'entrée, un seul mécanisme de collage

`_ViewerToolbar.paste_image_from_clipboard()` — factorisée, appelée par les trois chemins ci-dessous. **Chaque appel colle TOUJOURS une nouvelle image**, contrairement au comportement standard des autres icônes de la barre (`_on_tool_clicked` : re-cliquer sur l'icône déjà active la désélectionne) — re-coller pendant que l'outil est déjà actif ne le désélectionne jamais, pour permettre d'accumuler plusieurs images sans quitter puis rerentrer dans l'outil.

1. **Clic sur l'icône** (`_ViewerToolbar._on_tool_clicked`, branche `tool_id == "paste_image"`) — déjà grisée si `clipboard_has_single_image()` est faux, `_ToolButton.mousePressEvent` ignore alors le clic.
2. **Ctrl+V** (`ImageViewer._paste_image_shortcut`, `QShortcut("Ctrl+V")` câblé dans `ImageViewer.__init__`) — nécessaire car `ImageViewer` est une `QDialog` séparée de la mosaïque, hors de portée du Ctrl+V global (`PanelWidget._paste_ctrl_v`, skill `clipboard`). Pas de garde d'activation équivalente à l'icône : `paste_image_from_clipboard()` est un no-op silencieux si le presse-papiers ne contient pas une image seule.
3. **Drag & drop entrant sur `_ViewerCanvas`** (`dragEnterEvent`/`dragMoveEvent`/`dropEvent`, `setAcceptDrops(True)`) — voir section dédiée ci-dessous, chemin différent (pas de passage par `paste_image_from_clipboard()`, appelle `_add_pasted_image()` directement).

**`PasteImageCanvasMixin._add_pasted_image(pil_img)`** — point d'entrée UNIQUE pour poser une nouvelle image sur la page, quelle que soit sa provenance (presse-papiers ou drop). Centre l'image sur la page affichée, taille initiale = un quart de la page (ratio d'aspect source préservé), l'ajoute à `_pasted_images`, la sélectionne, déclenche `_on_paste_image_content_changed()`. **Isolation volontaire** : aucune logique de manipulation/validation n'a besoin d'être retouchée pour ajouter un futur point d'entrée — un nouveau chemin n'a qu'à appeler cette même méthode avec l'image obtenue.

## Ctrl+C — copie la page affichée, pas la sélection de la mosaïque

`ImageViewer._copy_current_page_shortcut()` (`Ctrl+C`, `QShortcut` câblé dans `__init__`) copie `state.images_data[self.current_idx]` — **pas** `state.selected_indices` (qui peut diverger de la page réellement affichée dans la visionneuse, l'utilisateur pouvant naviguer dans la visionneuse sans toucher à la sélection de la mosaïque). Réutilise `clipboard_qt.py::copy_single_entry_to_system_clipboard(entry, get_mosaicview_temp_dir, self)`, variante à une seule entrée factorisée depuis `copy_to_system_clipboard` (cœur commun : `_copy_entries_to_system_clipboard`, écriture sur disque + pose `CF_HDROP`) — voir skill `clipboard`.

**Refusé en mode `double`/`continuous`** (`MsgDialog`, `messages.warnings.copy_page_requires_single_mode`, non-modal) : `self.current_idx` dans ces deux modes correspond soit à la page GAUCHE soit à la page DROITE de la paire combinée selon comment on y est arrivé (voir `display_image()`, skill `viewers`), ambigu pour l'utilisateur qui copie en pensant obtenir une page précise visible à l'écran. Le message renvoie vers la touche `D` (bascule de mode) plutôt que de deviner/forcer un mode à sa place. Fonctionne normalement en mode `single` et `webtoon` (une seule page affichée dans les deux cas).

## Drag & drop entrant — mosaïque (n'importe quel panneau) OU Explorateur Windows

`_ViewerCanvas.setAcceptDrops(True)`, `dragEnterEvent`/`dragMoveEvent`/`dropEvent` (`image_viewer_qt.py`) :

- **`_drag_has_acceptable_image(mime)`** — vrai si le `QMimeData` porte EXACTEMENT une URL de fichier local dont l'extension figure dans `IMAGE_EXTS` (même liste que le presse-papiers, `clipboard_qt.py`). Plusieurs fichiers, ou un seul fichier non-image, sont refusés.
- **Un seul test couvre les deux provenances** (mosaïque ET Explorateur), sans distinguer les mimes internes `application/x-mosaicview-indices`/`application/x-mosaicview-panel` : `mosaic_canvas.py::_start_drag()` écrit **toujours** chaque page sélectionnée sur disque et pose des URLs `CF_HDROP` en parallèle des mimes internes, même pour un drag purement interne à l'appli (pour permettre le drag-out vers l'Explorateur, voir skill `drag-and-drop`) — donc `mime.hasUrls()` seul suffit à lire le fichier réellement écrit sur disque, peu importe si le geste a commencé sur panel1, panel2, ou l'Explorateur.
- **`Qt.CopyAction` forcé explicitement** (`event.setDropAction(Qt.CopyAction)`, pas `acceptProposedAction()`) dans les trois handlers — la page source ne doit **jamais** être supprimée de sa mosaïque d'origine. `mosaic_canvas.py::_start_drag()` ne supprime la page source que si le `dropEvent` de `MosaicCanvas` lui-même (réordonnancement/inter-panneaux) marque `_drop_was_internal = True` — jamais déclenché par le drop sur la visionneuse, une `QDialog` distincte de toute mosaïque.
- **`dropEvent`** charge l'image via `PIL.Image.open(path).convert("RGBA")`, active l'outil `paste_image` si besoin (`set_active_tool("paste_image")`), puis appelle `_add_pasted_image(img)` — même point d'entrée unique que le presse-papiers, aucune logique dupliquée.

## Rendu final (aplatissement)

`PasteImageViewerMixin._pasted_images_render_all()`/`_paste_one_pasted_image()` — pattern `apply-image-operation` variante (A) complète, undo/redo unifié, un seul point d'historique à la validation (comme le texte/les formes, pas un par image) :

1. `validate_paste_image()` — si `not self._canvas.has_pasted_images`, `MsgDialog` (`messages.warnings.no_pasted_image`) et arrêt.
2. `save_state()` avant modification.
3. Pour chaque `_PastedImage` : redimensionne le bitmap source à la taille du rectangle englobant courant (`Image.resize(..., Image.LANCZOS)`), puis si `angle == 0.0` colle directement (`img.paste(resized, (x1,y1), resized)`), sinon pivote le bitmap redimensionné (`Image.rotate(-angle, expand=True, resample=Image.BICUBIC)` — signe opposé à `angle`, même piège de convention horaire écran/QPainter vs anti-horaire PIL que `draw-shapes`) puis colle en alignant le centre du calque tourné sur le centre `(cx, cy)` du rectangle englobant d'origine.
4. Conversion de mode source si nécessaire (`entry['_orig_mode']`), aplatissement sur fond blanc si le format cible ne supporte pas l'alpha — même logique que `text_tool_qt.py::perform_text`/`shapes_tool_qt.py::perform_shapes`.
5. Invalidation complète des caches (variante A), sync `ComicInfo.xml`, `state.modified = True`, `save_state(force=True)`.
6. `self._canvas.clear_pasted_images()`, `_pasted_images_by_page.pop(...)`, `display_image(keep_crop_rect=True)`, `refresh_undo_redo_state()`, puis `_on_paste_image_content_changed()`.

## Persistance par page — `_pasted_images_by_page`

`ImageViewer._pasted_images_by_page: dict[int, list[tuple]]`, même principe que `_shapes_by_page` (liste de N images collées par page, pas un seul objet) — mais chaque tuple porte les **bytes PNG** du bitmap (`p.pil_img.save(buf, format="PNG")`) en plus de la géométrie `(png_bytes, ix1, iy1, ix2, iy2, angle)`, contrairement à `_Shape` qui n'a qu'une géométrie vectorielle à sérialiser. `_save_paste_image_for_current_page()`/`_restore_paste_image_for_page()` appelées depuis `navigate()`, comme les autres dicts `_by_page`. Contribue à `ImageViewer._has_unvalidated_work()`.

## Points d'entrée UI

Trois points d'entrée (icône barre, Ctrl+V, drag & drop) — voir sections dédiées ci-dessus. Aucune commande dans le menu/la colonne d'icônes, aucun ancien point d'entrée mosaïque à nettoyer (outil entièrement neuf, comme "shapes").

## Traductions

`locales/*.json` : `buttons.validate_paste_image`/`cancel_paste_image`, `viewer.toolbar_paste_image_tooltip`/`toolbar_paste_image_instruction`/`toolbar_paste_image_disabled`, `messages.warnings.no_pasted_image.title`/`message`, `messages.warnings.copy_page_requires_single_mode.title`/`message`, `messages.errors.paste_image_failed.title`/`message` — propagées aux 46 langues (voir skill `add-translation`). Vocabulaire tlh/sjn/qya réutilisé depuis les lexiques de référence existants (`coller`=lan/Pado/Pata, `copier`=Honom/Samna, `mode simple page`=repris tel quel de la clé `mode_single` déjà traduite) — aucun néologisme improvisé pour ces clés.

## Icônes

Une seule icône, `BTN_PiP.png` (fournie, pas générée en PIL) — pas de panneau d'options donc pas d'icônes secondaires à générer, contrairement à `draw-shapes` (5 icônes de type de forme).

## Comment étendre

- **Ajouter un réglage** (ex. opacité) : nécessiterait de créer `_PasteImageOptionsPanel` (n'existe pas actuellement, décision explicite de ne pas en avoir) — suivre le pattern de `_ShapeOptionsPanel` (`draw-shapes`) si demandé un jour, en particulier le blindage anti-fuite de clic (`mousePressEvent`/`mouseReleaseEvent` avec `event.accept()`) et la réinitialisation du curseur au survol (`enterEvent`/`leaveEvent`), obligatoires pour tout nouveau panneau flottant de cette barre (voir CLAUDE.md règles générales de collaboration).
- **Changer le curseur de rotation ou le grisage hors-limites** : modifier `shapes_tool_qt.py` uniquement (`_build_rotate_cursor()`/`_paint_out_of_page_bounds_overlay()`), jamais dupliquer dans `paste_image_tool_qt.py` — une seule source de vérité pour les deux outils.
- **Changer la règle coin=ratio conservé/bord=libre** : `PasteImageCanvasMixin._apply_paste_resize` (ce fichier) ET `ShapeCanvasMixin._apply_shape_resize` (`shapes_tool_qt.py`) — dupliquée entre les deux mixins, à répliquer manuellement dans les deux.
- **Ajouter un futur point d'entrée** (ex. un menu contextuel "Coller ici") : appeler `_add_pasted_image(pil_img)` avec l'image obtenue, aucune autre modification nécessaire au mécanisme de manipulation/validation.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md.

## Références croisées

- `viewers` — architecture générale de la fusion progressive, tableau des outils migrés, undo/redo unifié, persistance par page, section "Le cas de coller une image" pour l'intégration dans la barre.
- `draw-shapes` — source du curseur de rotation et du grisage hors-limites (réutilisés tels quels), modèle direct pour les poignées de redimensionnement/rotation et le déplacement clavier partagé (`_shape_key_nav`).
- `clipboard` — `clipboard_has_single_image`/`get_clipboard_single_image`/`copy_single_entry_to_system_clipboard`/`IMAGE_EXTS` (`clipboard_qt.py`), toutes réutilisées telles quelles plutôt que réécrites.
- `drag-and-drop` — mécanisme de drag-out CF_HDROP de la mosaïque (`mosaic_canvas.py::_start_drag`), dont ce skill réutilise le fait qu'un drag interne pose toujours aussi des URLs de fichiers.
- `apply-image-operation` — pattern d'invalidation de caches suivi en variante (A) complète par `perform_paste_image`.
- `add-translation` — méthode de propagation aux 46 langues, lexiques tlh/sjn/qya.
