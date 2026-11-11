---
name: create-ico
description: Localiser ou modifier la création de fichiers .ico multi-résolution dans MosaicView (fenêtre 2 phases découpe/transparence, suivi de la page pendant que la fenêtre est ouverte, commande grisée tant que la BD a des dossiers, blocage de sauvegarde CBZ tant qu'un .ico traîne dans la mosaïque). Utiliser dès qu'une tâche touche à ico_creator_qt.py, IcoCreatorDialog, _page_image, _on_document_changed, ou _check_no_ico.
---

# Création de fichier .ico — MosaicView

Fenêtre dédiée à 2 phases qui transforme une image **sélectionnée** de la mosaïque en un fichier `.ico` Windows multi-résolution (tailles de `_ICO_SIZES`, de 16 à 256 px), inséré comme nouvelle entrée juste après l'image source. Usage typique : générer une icône d'application ou de raccourci à partir d'une image existante, sans quitter MosaicView.

**Fichier unique — `modules/qt/ico_creator_qt.py`** (~1170 lignes), une seule classe `QDialog` (`IcoCreatorDialog`) qui reconstruit entièrement son contenu entre les deux phases (`_build_phase_a()`/`_build_phase_b()`, widget de phase précédent détruit et remplacé, pas deux fenêtres séparées).

**Image de la page — `_page_image(entry)`** (fonction de module, sans Qt) : la fenêtre (`WA_DeleteOnClose`, détruite à sa fermeture) travaille sur sa copie RGBA pleine résolution (`_original_img`), et son affichage sur un `QPixmap` fait une fois (`_source_pixmap`). Gris 16 bits ramenés à 256 nuances (`to_display_mode`) avant la conversion en RGBA : **piège**, convertis directement, ils sortaient blancs. L'image décodée par `ensure_image_loaded` pour la construire est retirée de la page (`entry["img"] = None`) si la page ne l'avait pas avant ; une image déjà décodée (visionneuse ouverte) est laissée : sinon elle resterait en mémoire jusqu'à la fermeture du fichier.

**Commande grisée tant que la BD a des dossiers** (menu Images `_populate_images_menu`, repris par le clic droit `show_image_context_menu`, règle `create_ico` de la colonne d'icônes, avec `has_subdirectory_structure` / `has_subdirs`), et `create_ico_from_selected` sort sans rien faire (`_has_folders`). **Piège** : l'icône d'une page `Aaaa/001.jpg` était nommée `ICO001_Aaaa/001.ico` et créait un dossier à part, invisible depuis celui de la page, tout en bloquant l'enregistrement. Voir aussi skill `flatten-directories`, « Dans un sous-dossier ».

**Suivi de la page** (fenêtre non modale) : `_page_key` (`undo_redo.entry_key`, jeton qui survit à Annuler) et `_page_bytes` (bytes dont vient l'image) ; `_on_document_changed`, inscrit par `state.add_document_listener` (retiré à `finished` et à `destroyed`, skill `undo-redo`) :
- découpe (phase A) : page retouchée → image rechargée, zone de découpe recentrée ;
- transparence (phase B) : page retouchée → travail gardé, avertissement ;
- page supprimée (les deux phases) → avertissement ; page revenue avec ses bytes d'origine (Annuler) → avertissement retiré ;
- « Retour à la découpe » repart de l'image actuelle de la page.
L'avertissement (`_warn_label`, recréé à chaque phase par `_make_warn_label`, texte `dialogs.ico_creator.source_changed`, `_retranslate_warning`) est en rouge `#cc0000`, gras italique, à la demande de l'utilisateur.

**Image illisible** : une page sans cadre rouge peut encore échouer au décodage (`ensure_image_loaded` rend `None`). `IcoCreatorDialog.__init__` pose alors `self.load_failed = True` et s'arrête avant de construire ses widgets ; `create_ico_from_selected` détruit la fenêtre sans l'afficher (`deleteLater()`) et ouvre à la place une `ErrorDialog` (titre `dialogs.ico_creator.unreadable_title`, message `dialogs.ico_creator.unreadable_message` avec `name=entry["orig_name"]`, en lambda), centrée sur le panneau, sans cri de Wilhelm. Le nom de la page vient de l'archive : `ErrorDialog` l'affiche en texte brut. **Piège** : sans ce contrôle, la fenêtre se construisait sur une image absente (exception dans `convert`), et la commande semblait sans effet.

## Phase A — découpe carrée

Objectif : réduire l'image source à une zone **carrée** (les `.ico` sont toujours carrés), via un cadre rouge interactif très proche de celui de `page-crop` — mêmes principes de poignées de coin/bord/déplacement, mêmes curseurs, mais implémentation **indépendante** (`_CropCanvas`, pas `_ViewerCanvas` de `image_viewer_qt.py`, aucun code partagé) avec une contrainte supplémentaire : le cadre reste **toujours carré**, jamais rectangulaire.

- **Contrainte carrée systématique** : `_constrain_square_from_corner`/`_constrain_square_from_edge` recalculent toujours un côté égal en largeur et hauteur, quelle que soit la poignée déplacée — glisser un coin agrandit dans la diagonale (le côté opposé au point d'ancrage bouge à la même distance en x et y, `side = max(abs(dx), abs(dy))`), glisser un bord recentre le carré autour de son centre précédent (`_constrain_square_from_edge`, garde `cx`/`cy` fixes et ajuste le demi-côté symétriquement) plutôt que de simplement translater un seul côté.
- **Zoom molette+Ctrl** (`Ctrl+Molette`, `Ctrl++`/`Ctrl+-`/`Ctrl+0`) et **pan clic droit** — mêmes raccourcis que les autres visionneuses (skill `viewers`), mais implémentation locale à ce fichier.
- **Affichage** (`_display_image_a`, `_CropCanvas.paintEvent`) : l'image n'est réduite par Qt qu'une fois par taille d'affichage, et jamais agrandie dans un pixmap — le canvas l'agrandit au dessin (`drawPixmap` vers un `QRectF`), et le damier est un motif (`_checkerboard_brush`) rempli d'un seul appel. **Piège** : redimensionnée par Pillow puis posée sur un damier dessiné carreau par carreau, une image de 64 pixels au zoom maximal demandait environ 518 000 carreaux et 130 Mo à chaque cran ou redimensionnement de la fenêtre.
- **Coordonnées persistantes relatives** (`rect_rel_x1`/`rect_rel_y1`/`rect_rel_size`, 0-1) — même principe que `page-crop` (`crop_rel_x1`...) pour survivre aux changements de zoom/pan/redimensionnement de fenêtre.
- **Deux façons de valider** : "Valider la découpe" (`_on_validate_crop`, garde le cadre carré tracé) ou "Valider sans découper" (`_on_validate_no_crop`, ignore le cadre et fait un **letterboxing** — l'image entière est centrée sur un canevas carré transparent de côté `max(largeur, hauteur)`, sans perte de contenu ni recadrage réel). Double-clic à l'intérieur du cadre (`on_double_click`) équivaut au premier bouton.
- **Undo/redo local à la phase A** (`_undo_stack_a`/`_redo_stack_a`) : `_CropCanvas` retient `(rect_rel_x1, rect_rel_y1, rect_rel_size)` au clic (`_rel_before`) et, au relâchement, appelle `on_rect_committed(état_d_avant)` → `_undo_push_a(before)` **seulement si le cadre a bougé** (`_rect_moved`, écart au-delà de `_RECT_UNCHANGED` : l'aller-retour vers l'écran laisse des écarts d'arrondi). **Piège** : enregistrée au clic, une étape d'annulation sans effet s'ajoutait à chaque simple clic. Indépendant de l'historique global de l'application (`save_state`).

## Transition A → B — `_prepare_phase_b`

Que la validation vienne du crop ou du "sans découper", le résultat est systématiquement redimensionné à **256×256** (`Image.LANCZOS`) et stocké dans `self._ico_img_rgba` — c'est cette image 256×256 qui sera travaillée en phase B, puis réduite aux tailles de `_ICO_SIZES` uniquement à la sauvegarde finale, pas avant. Les piles undo/redo de la phase B sont vidées, les raccourcis clavier de la phase A désactivés. Le nom n'est pas choisi ici, mais à la validation finale.

## Phase B — transparence par pipette flood-fill

Objectif : rendre transparentes les zones de fond de l'image 256×256 (typiquement un fond uni autour du sujet), affichée sur un damier gris pour visualiser l'alpha en temps réel.

- **Pipette** (`_btn_pipette`, bouton bascule) : **active par défaut à l'entrée en phase B** — `_build_phase_b()` fait `_btn_pipette.setChecked(True)` et `_transp_canvas.set_pipette(True, ...)` dès la construction, donc le curseur est une croix et le premier clic sur l'image applique déjà la transparence. Un clic sur le bouton la désactive (`_toggle_pipette` → `set_pipette(False)`, curseur flèche, clic gauche sans effet). Comme la phase B est reconstruite à chaque validation de la phase A, un aller-retour "Retour à la découpe" → revalidation réactive la pipette. Un clic gauche pipette active déclenche `_on_pipette_click(img_x, img_y)`.
  - **Piège** : les deux réglages (`setChecked(True)` sur le bouton ET `set_pipette(True)` sur le canvas) vont ensemble — le bouton seul afficherait l'état enfoncé sans que le canvas réagisse au clic, le canvas seul réagirait sans que le bouton le montre. `setChecked(True)` doit aussi précéder l'appel à `_retranslate()` en fin de `_build_phase_b()`, qui choisit le style enfoncé/relâché selon `isChecked()`.
- **Clic de la pipette** (`_TransparencyCanvas.mousePressEvent`) : pixel calculé par `math.floor`, jamais `int` — `int` ramenait à 0 un clic juste à gauche ou au-dessus de l'image, qui agissait alors sur son bord.
- **Algorithme flood-fill 4-connexe** — `_apply_transparency(px, py, tolerance)` (`ico_creator_qt.py`) : lit la couleur RGB du pixel cliqué comme référence, puis propage par une pile explicite (pas de récursion, évite tout risque de dépassement de pile Python sur une grande zone à 256×256 = jusqu'à 65536 pixels) aux 4 voisins directs (haut/bas/gauche/droite, pas les diagonales) tant que leur écart de couleur (`max(abs(dr), abs(dg), abs(db))`, distance de Chebyshev, pas euclidienne) reste sous la **tolérance** réglée par curseur. Chaque pixel accepté voit son canal alpha mis à `0` directement via `pixels[cx, cy] = (pr, pg, pb, 0)` (mutation en place du buffer PIL `.load()`, pas de `putpixel` un par un — plus rapide sur une grande zone).
- **Piège** : un pixel déjà transparent (`ref[3] == 0`) au point cliqué fait sortir immédiatement de la fonction sans rien faire — cliquer une seconde fois sur une zone déjà rendue transparente est un no-op silencieux, pas une erreur.
- **Tolérance** (0 à 255, défaut 15, constantes `_TOLERANCE_MIN`/`_TOLERANCE_MAX`/`_TOLERANCE_DEFAULT` en tête de fichier) — un `FocusSlider` (`_tol_slider`) et un `QSpinBox` (`_tol_spin`, réglage fin) côte à côte dans la barre du bas, synchronisés dans les deux sens par `_on_tol_slider_changed`/`_on_tol_spin_changed` (mise à jour de l'autre widget encadrée par `blockSignals(True/False)` pour éviter la boucle de signaux — même schéma que la tolérance de l'outil transparence de la visionneuse, skill `adjust-transparency`). `_on_pipette_click` lit `_tol_slider.value()`, toujours à jour quel que soit le widget manipulé. Style de la spinbox (couleurs du thème) et police (`_get_current_font(11)`) réappliqués dans `_retranslate_b()`. Pas de prévisualisation pendant le réglage : l'effet n'apparaît qu'au clic pipette suivant.
- **Undo/redo local à la phase B** (`_undo_stack_b`/`_redo_stack_b`) : contrairement à la phase A qui ne stocke que 3 flottants, ici chaque entrée est une **copie complète** de l'image PIL 256×256 (`self._ico_img_rgba.copy()`) — capturée avant chaque application de pipette (`_on_pipette_click` → `_undo_push_b`). Coût mémoire plus élevé mais nécessaire puisqu'un flood-fill n'est pas décrit par une poignée de paramètres réutilisables comme un simple rectangle.
- **"Retour à la découpe"** (`_on_back_to_crop`) : revient à la phase A en réinitialisant le zoom, **perd tout le travail de transparence de la phase B** (pas de sauvegarde croisée entre phases) — un aller-retour reconstruit `_ico_img_rgba` depuis zéro à la prochaine validation de la phase A.

## Undo/redo partagé — un seul jeu de boutons, deux piles distinctes

Les boutons Undo/Redo de la barre du bas sont **physiquement différents par phase** (`_btn_undo_a`/`_btn_redo_a` vs `_btn_undo_b`/`_btn_redo_b`, reconstruits à chaque `_build_phase_a`/`_build_phase_b`) mais `_undo()`/`_redo()` sont des méthodes **uniques** sur `IcoCreatorDialog` qui aiguillent selon `self._phase` (`'a'` ou `'b'`) vers la pile appropriée — pas de confusion possible entre les deux car les piles ne sont jamais consultées hors de la phase à laquelle elles appartiennent.

## Nommage automatique — `_get_ico_name` (`ico_creator_qt.py`)

Motif `ICO{NNN}_{nom_original}.ico`, `NNN` sur 3 chiffres. Le numéro est calculé en scannant **toutes** les entrées déjà présentes dans `images_data` à la recherche du motif `^ICO(\d+)_` (regex insensible à la casse) et en prenant le maximum existant + 1. Le nom est choisi **à la validation finale** (`_on_validate_final`), puis passé par `utils.unique_names` avec tous les noms du document : jamais un nom déjà pris. **Piège** : choisi au passage à la transparence, deux fenêtres ouvertes sur la même page donnaient deux icônes du même nom.

## Validation finale — `_on_validate_final` (`ico_creator_qt.py`)

Sans effet (fenêtre ouverte) si une opération en arrière-plan (rotation…) tourne sur le document (`operation_running()`, skill `undo-redo`) : la fenêtre est non modale, et une insertion pendant l'opération serait mêlée à ses résultats.

1. `self._ico_img_rgba.save(buf, format="ICO", sizes=_ICO_SIZES)` — **Pillow génère lui-même toutes les tailles de `_ICO_SIZES`** à partir de la seule image 256×256 travaillée, un seul appel `save()` produit le fichier `.ico` multi-résolution complet ; pas de redimensionnement manuel par résolution dans ce fichier.
2. Construction d'un **nouvel objet entrée** (`new_entry`, dict minimal : `orig_name`/`extension`/`bytes`/`img`/`is_image`/`thumb`/`img_id`/`qt_pixmap_large`) — **pas** via `create_entry()` (skill `archive-image-loading`), contrairement au point de passage habituel du projet pour créer une entrée ; champs volontairement réduits au strict nécessaire pour l'affichage immédiat dans la mosaïque.
3. **Insertion juste après l'image source**, retrouvée par son jeton (`_find_page`, `undo_redo.entry_key` : une page rétablie par Annuler est un nouvel objet) au moment de valider — jamais `self._idx` capturé à l'ouverture : la fenêtre est non modale, la mosaïque a pu changer entre-temps. Source supprimée entre-temps → icône insérée à la position d'origine de la page (bornée à la longueur du document). L'image d'origine n'est jamais retirée ni modifiée, comme `page-split` (résultat ajouté, source conservée) plutôt que comme `page-crop`/`rotate-flip` (source remplacée en place).
4. `sync_pages_in_xml_data(state)` (skill `comicinfo-metadata-editor`) pour intégrer la nouvelle page au `ComicInfo.xml` si présent.
5. **`save_state_qt(...)` avant et après l'insertion** (règle du projet, skill `undo-redo`) : celui d'avant ne crée un état que si le document a changé depuis le dernier. **Piège** : avec un seul état, posé après, Annuler l'icône annulait aussi une modification d'avant restée sans le sien.
6. `render_mosaic()` + `refresh_toolbar()`, puis `self.close()` — **aucun message de confirmation** : l'apparition du `.ico` dans la mosaïque, juste après l'image source, sert de retour visuel. Choix voulu par l'utilisateur (une fenêtre de succès à chaque création est jugée dérangeante) — ne pas en ajouter une.

**Tests** : `tests/test_crop_and_icon.py` exécute, sans Qt (`tests/ast_loader.load_methods`, affichage de la phase B remplacé par une fonction vide), `_CropCanvas.get_crop_in_image_coords` et `_clamp_rect_to_image` (carré converti en pixels de l'image, gardé dans l'image, carré vide si l'image n'est pas affichée), `_on_validate_crop` / `_on_validate_no_crop` / `_prepare_phase_b` (carré recadré, ou image complétée en carré transparent, ramenés à 256×256, piles d'annulation de la phase B vidées), `_apply_transparency` (couleur voisine dans la tolérance rendue transparente, autre couleur gardée, pixel déjà transparent sans effet) et `_on_validate_final` (`.ico` aux tailles de `_ICO_SIZES`, inséré après la page source retrouvée même déplacée, à sa position d'origine si elle a été supprimée, états d'historique avant et après, nom jamais déjà pris, rien pendant une opération) ; aussi `_page_image` (gris 16 bits), `_has_folders` et `create_ico_from_selected` (refus avec dossiers), le grisage dans les deux menus et la colonne d'icônes, `_on_document_changed` (page rechargée, avertissement, page revenue), `_CropCanvas.mouseReleaseEvent` (étape d'annulation seulement si le cadre a bougé) et `_TransparencyCanvas.mousePressEvent` (clic hors de l'image). Une méthode d'affichage nouvellement appelée par ce calcul s'ajoute, vide, à la classe `IconWindow` du test.

## Blocage de sauvegarde tant qu'un `.ico` traîne dans la mosaïque — `_check_no_ico`

**Mécanisme central à connaître, situé hors de ce fichier** : `modules/qt/file_operations_qt.py::_check_no_ico(parent, state=None)` vérifie si `state.images_data` contient une entrée dont `orig_name` se termine par `.ico`, et bloque l'opération avec un `MsgDialog` (`dialogs.ico_creator.save_blocked_title`/`save_blocked_message`) si c'est le cas. Appelé en tête de `save_as_cbz`, `save_selection_as_cbz`, `create_cbz_from_images` et `apply_new_names`, toujours avec le `state` du panneau qui sauvegarde (voir skill `save-export`, section "State du panneau") — sans ce `state`, il vérifierait le panneau pointé par le singleton, qui peut être l'autre panneau en split-view. Grep `_check_no_ico` pour la liste exhaustive à jour.

**Raison** : un `.ico` n'est pas une page de bande dessinée normale — le laisser dans un CBZ produirait une archive avec un fichier hors format que les lecteurs de comics ne sauraient pas afficher correctement. Le blocage force l'utilisateur à retirer (Suppr) ou déplacer (Ctrl+X, skill `clipboard`) le `.ico` avant de pouvoir sauvegarder, plutôt que de le laisser silencieusement polluer l'archive.

Ce n'est **pas** un mécanisme de undo/redo ni une validation dans `IcoCreatorDialog` lui-même — c'est un garde-fou global, découplé, qui s'applique à n'importe quel `.ico` présent dans la mosaïque quelle que soit son origine (créé par ce dialogue, importé depuis le web, glissé-déposé, etc.).

## Traitement du `.ico` ailleurs dans le projet

Une fois inséré dans la mosaïque, un `.ico` reste une entrée `is_image: True` comme les autres et peut subir les mêmes opérations (rotation, crop, ajustements...) — **`entries.py::save_image_to_bytes()` gère nativement le cas `.ico`** : elle relit `info['sizes']` du fichier `.ico` d'origine (`entry['bytes']`, un `set` de tuples `(w, h)` exposé par PIL) et régénère exactement ce même jeu de résolutions à la sauvegarde (`save(format='ICO', sizes=list(original_sizes))`) — ni plus ni moins de tailles que le fichier n'en avait avant modification, qu'il soit mono ou multi-résolution. Le mode PIL choisi par l'utilisateur via les outils mode d'image/profondeur de couleur de la visionneuse est respecté tel quel (Pillow écrit nativement l'ICO en `1`/`L`/`P`/`RGB`/`RGBA` — seuls `LA`/`CMYK` sont convertis vers l'équivalent avec/sans alpha faute d'écriture ICO directe), contrairement à `ico_creator_qt.py` (création d'une icône neuve) qui force volontairement RGBA pour garantir la transparence par défaut. **Piège si ce cas `.ico` explicite venait à disparaître** : le fichier retomberait silencieusement dans le `else` générique de `save_image_to_bytes()` (sauvegarde en PNG), perdant son format multi-résolution — ce chemin est partagé par **tous** les outils qui modifient une image (crop, straighten, clone, texte, formes, deskew, tous les outils de la barre d'ajustement), pas un seul point d'entrée isolé. Ne jamais réintroduire un `save()` direct avec `fmt = 'ICO'` sans `sizes=` dans un outil particulier — ça ne produirait qu'une seule résolution au lieu du jeu complet, la correction doit rester centralisée dans `save_image_to_bytes()`.

## Points d'entrée UI

Trois, tous nécessitant **exactement une seule** image sélectionnée (`create_ico_from_selected`, `ico_creator_qt.py` — retourne silencieusement, sans message d'erreur, si la sélection est vide, multiple, invalide ou corrompue ; contrairement à `page-resize`/`page-crop` qui affichent des `MsgDialog` dédiés pour ces cas) :

1. **Menu contextuel** (clic droit mosaïque, skill `qt-context-menus`) — entrée de `_populate_images_menu` (`menubar_qt.py`) reprise par `show_image_context_menu` (`context_menus_qt.py`), clé `context_menu.image.create_ico`.
2. **Barre de menu** — `_populate_images_menu` (`menubar_qt.py`), même clé.
3. **Colonne d'icônes** (skill `icon-toolbar`) — bouton id `"create_ico"` (`ICON_DEFINITIONS`, `icon_toolbar_qt.py`, icône `BTN_ICO.png`, **pas de tooltip dédié** `tooltip_key: None`, comme `page-resize`/`page-crop` — utilise le libellé générique `context_menu.image.create_ico`), activé si `single_image_selected()`.

Callbacks (`PanelWidget._ico_callbacks()`, `panel_widget.py`) : seulement `render_mosaic`, `refresh_toolbar`, `state` — **pas de `save_state`** dans le dict transmis (l'appel se fait directement via `save_state_qt(state, ...)` importé en tête de fichier, pas via un callback injecté comme dans les autres skills d'édition d'image).

## Traductions

`locales/fr.json`, section `dialogs.ico_creator` : `title` (titre de la fenêtre, posé par `set_window_title`), tous les libellés des deux phases (`btn_validate_crop`/`btn_validate_no_crop`/`btn_cancel`/`btn_transparency`/`btn_back_to_crop`/`btn_validate_final`/`tolerance_label`/`info_message`, qui reçoit la liste des tailles par le paramètre `{sizes}`, construit dans `_update_info_label_a` à partir de `_ICO_SIZES` et joint par le séparateur traduit `sizes_separator` (`", "`, `"، "` en arabe) — jamais une liste écrite dans les traductions), et **`save_blocked_title`/`save_blocked_message`** pour le garde-fou de `_check_no_ico` (logiquement rattachées à cette section malgré leur usage dans un fichier différent). `overwrite_title`/`overwrite_message` existent aussi dans cette section mais ne sont référencées par aucun fichier Python (clés mortes) — ne pas s'appuyer dessus. Voir skill `add-translation`.

**A une section dans le mode d'emploi** (liste des sections de `_HelpDialog._build_ui`, `user_guide_qt.py`, clé `help.create_icon`/`help.create_icon_content`) — comme `page-resize`/`page-crop`, contrairement à `add-text-to-image` (skill `user-guide`).

## Comment étendre

- **Ajouter/retirer une résolution générée** (actuellement 16, 20, 24, 32, 40, 48, 64, 96, 128 et 256 : les tailles de Windows aux échelles d'affichage 100 à 250 %, plus 128) : une seule constante `_ICO_SIZES` en tête de fichier, transmise telle quelle à `Image.save(..., sizes=...)` ; le message de la phase A (`info_message`) et le tooltip de la vignette (`tooltips_qt.py`, qui lit les tailles présentes dans le fichier) suivent d'eux-mêmes, aucune traduction à reprendre. **256×256 est le maximum** : le format ICO code largeur et hauteur sur un octet (0 = 256), et Pillow écarte silencieusement toute taille supérieure — ajouter `(512, 512)` ne changerait rien au fichier produit.
- **Changer l'algorithme de détection de couleur similaire** (actuellement distance de Chebyshev max des 3 canaux RGB) : `_apply_transparency`, la ligne `if max(abs(pr - r), abs(pg - g), abs(pb - b)) > tolerance`.
- **Passer le flood-fill en 8-connexe** (inclure les diagonales) : ajouter les 4 voisins diagonaux à la liste dans la boucle `for nx, ny in (...)`.
- **Autoriser un cadre rectangulaire en phase A** (actuellement toujours contraint carré) : changement de nature du fichier, `.ico` exige des icônes carrées par spécification Windows — ne pas faire sans confirmation explicite, cette contrainte n'est probablement pas négociable.
- **Faire persister le travail de transparence à un retour en phase A** (actuellement perdu, voir section phase B) : nécessiterait de conserver `_ico_img_rgba` avant reconstruction plutôt que de le régénérer entièrement à la prochaine validation de phase A — changement de comportement notable, à valider avec l'utilisateur avant de l'implémenter.
- Respecter les 9 règles UI Qt obligatoires du CLAUDE.md pour `IcoCreatorDialog` (non-modale déjà en place, barre de titre dessinée par Qt avec `set_window_title`, skill `window-title`, déjà en place).

## Pièges connus

- **Contrainte carrée systématique en phase A** — toute poignée de redimensionnement recalcule un côté égal, ne jamais introduire de logique qui permettrait un rectangle.
- **États d'historique avant et après l'insertion** — jamais un seul : Annuler l'icône annulerait aussi une modification d'avant restée sans état.
- **La fenêtre suit la page par son jeton (`_page_key`), jamais par l'objet `_entry` seul** — une page rétablie par Annuler est un nouvel objet.
- **Nouvelle entrée construite manuellement, pas via `create_entry()`** — champs volontairement réduits ; si un champ attendu ailleurs dans le projet manque sur une entrée `.ico` fraîchement créée, vérifier ici en premier plutôt que dans `entries.py`.
- **Retour "à la découpe" perd la phase B sans confirmation** — voulu par l'utilisateur, ne pas ajouter de question.
- **Clic pipette sur pixel déjà transparent = no-op silencieux**, pas une erreur.
- **Image animée ou TIFF de plusieurs pages : icône faite de la première image, sans message** — voulu par l'utilisateur (une icône est fixe), ne rien ajouter.
- **`_check_no_ico` est un garde-fou global découplé**, pas une validation locale à `IcoCreatorDialog` — un `.ico` bloque la sauvegarde CBZ quelle que soit son origine, pas seulement ceux créés par ce dialogue.
- **`overwrite_title`/`overwrite_message` sont des clés mortes** — présentes dans les locales, utilisées par aucun fichier Python.
- **Pipette active par défaut en phase B** — bouton enfoncé et canvas armés ensemble dans `_build_phase_b()` ; ne jamais n'en régler qu'un des deux.
- **Pas de message de succès à la validation finale** — volontaire, ne pas en réintroduire un (voir étape 6 de la validation finale).
- **Pas de callback `save_state` dans `_ico_callbacks`** — contrairement au pattern des autres skills d'édition d'image, l'appel undo/redo se fait via un import direct de `save_state_qt`.

## Références croisées

- `page-crop` — cadre rouge interactif très similaire en interaction (poignées, curseurs, coordonnées relatives persistantes) mais implémentation totalement indépendante (`_CropCanvas` ici vs `_ViewerCanvas` là-bas) et avec une contrainte supplémentaire (carré forcé).
- `page-split` — autre fonctionnalité où la source reste en place et le résultat est inséré juste après elle.
- `flatten-directories` — « Dans un sous-dossier » : la création d'icône est grisée tant que la BD a des dossiers, et rien n'est possible dans un sous-dossier.
- `viewers`/`adjust-transparency` — l'outil transparence de la barre d'outils de la visionneuse ; tout outil de cette barre appliqué à une entrée `.ico` bénéficie de la préservation multi-résolution décrite ci-dessus.
- `apply-image-operation` — `save_image_to_bytes()` (`entries.py`), le point de passage générique de sauvegarde après modification de `entry['img']` ; référencé par la quasi-totalité des outils d'édition d'image du projet.
- `adjust-color-depth`/`adjust-image-mode` — le mode PIL choisi par ces deux outils est respecté pour `.ico` (voir ci-dessus), contrairement au forçage RGBA volontaire de `ico_creator_qt.py`.
- `apply-image-operation` — ce fichier ne suit pas le pattern documenté (pas de `save_state` avant modification, entrée construite manuellement) car il s'agit d'un **ajout** de nouvelle entrée, pas d'une modification de `entry['bytes']` existant.
- `archive-image-loading` — `create_entry()`, le point de passage habituel du projet pour créer une entrée, volontairement contourné ici.
- `undo-redo` — `save_state_qt` appelé directement plutôt que via le callback `save_state` injecté habituel.
- `comicinfo-metadata-editor` — `sync_pages_in_xml_data` après insertion de la nouvelle entrée `.ico`.
- `icon-toolbar` — bouton "create_ico" de la colonne d'icônes (sans tooltip dédié, comme "resize"/"crop").
- `qt-context-menus` — entrée du menu contextuel clic droit.
- `save-export` — `_check_no_ico` bloque toute sauvegarde CBZ tant qu'une entrée `.ico` traîne dans la mosaïque ; voir ce skill pour le détail des 6 méthodes de sauvegarde concernées.
- `clipboard` — Ctrl+X pour déplacer un `.ico` bloquant la sauvegarde vers un autre panneau plutôt que le supprimer.
- `user-guide` — section `help.create_icon` existante, à maintenir à jour.
- `animated-gif` — autre créateur de nouvelle entrée ; `animated-gif` réutilise `create_entry()` (contrairement à ce fichier qui construit son dict manuellement), pose un état d'historique avant et après l'ajout, et nomme lui aussi par recherche du numéro maximum (`gif_writer.next_gif_name`).
- `nfo-editor` — la création d'un `.nfo` suit au contraire le pattern standard à deux `save_state`, contrairement à ce fichier et à `animated-gif`/`page-split` — à noter si une cohérence inter-créateurs d'entrée est un jour recherchée.
