---
name: page-merge
description: Localiser ou modifier la fusion/jointure de pages (assemblage d'images sélectionnées en une planche, disposition 2D par glisser-déposer, ajustement de taille, jointure d'images animées frame par frame). Utiliser dès qu'une tâche touche à merge_dialog_qt.py, MergeDialog, merge_images_2d, merge_animated_2d, animations_joinable, ou open_merge_window.
---

# Fusion / jointure de pages — MosaicView

Assemble plusieurs pages sélectionnées (au moins 2, toutes images) en une seule image composite ("Collage_XX.ext"), qui remplace ou s'insère à la place des pages sources dans la mosaïque. Usage typique : rassembler une planche double/triple scannée en plusieurs fichiers séparés, ou recoller les morceaux d'une image animée scindée. Ne pas confondre avec la **fusion d'archives** (`import_merge_qt.py`, ajouter le contenu d'une deuxième archive à la suite — voir skill `archive-image-loading`) : ici on combine visuellement des *images déjà présentes* dans le même comic en une planche unique. Opération conceptuellement inverse de la **scission** (voir skill `page-split`) — mais la scission ne retire jamais l'image source alors que la fusion peut remplacer ou supprimer les sources selon la réponse de l'utilisateur ; ce ne sont pas des symétriques parfaites.

## Fichiers clés

- **`modules/qt/merge_dialog_qt.py`** — tout le mécanisme UI : `MergeDialog` (fenêtre principale), `MiniMosaicCanvas` (disposition 2D par glisser-déposer avec snap magnétique), `SizeAdjustmentDialog` (choix du mode d'ajustement de taille), `YesNoCancelDialog` (suppression des sources), `_AnimatedJoinWorker` (QThread de la jointure animée), point d'entrée public `open_merge_window()`.
- **`modules/qt/image_ops.py`** — logique métier pure (PIL, aucun Qt) : `merge_images_2d()` (assemblage final selon les positions 2D), `merge_images_horizontally()` (fusion d'une ligne, avec ajustement de taille), `detect_merge_adjustment()` (détection préalable, sans fusionner, pour poser la question à l'utilisateur avant), `is_animated_entry()` / `animations_joinable()` (règle de jointure des images animées), `merge_animated_2d()` (jointure frame par frame).
- **`modules/qt/panel_widget.py`** — `_merge_callbacks()` (contrat de callbacks fourni au dialogue).
- **`modules/qt/entries.py`** — `create_entry()` pour fabriquer l'entrée de la page fusionnée (voir skill `archive-image-loading`) ; pose aussi les drapeaux d'animation (`is_animated_gif`/`is_animated_image`, `gif_frame_count`, `gif_durations`) du résultat animé.
- **`modules/qt/comic_info.py`** — `sync_pages_in_xml_data()` appelée après insertion pour garder `<Pages>` cohérent (voir skill `comicinfo-metadata-editor`).
- **`modules/qt/undo_redo.py`** — `save_state_data()` (import direct de la couche pure, pas `save_state_qt`) encadre la modification de `images_data` (voir skill `undo-redo`).

## Règle de jointure des images animées — `animations_joinable(entries)`

Fonction pure de `image_ops.py`, **seule source de vérité** de la règle, appelée partout où la jointure est proposée :
- un TIFF de plusieurs pages dans la sélection (`is_multipage_tiff`) → refus : la jointure n'en garderait que la première page ;
- aucune image animée → jointure possible ;
- images animées et fixes mélangées → refus (une image animée ne se joint jamais à une image fixe) ;
- toutes animées → possible seulement si elles ont le même nombre de frames (`gif_frame_count`) et les mêmes durées frame par frame (`gif_durations`), à `_JOIN_DURATION_TOLERANCE_MS` (10 ms) près : le GIF stocke ses durées par pas de 10 ms, le WebP/PNG/AVIF à la milliseconde, une même animation lue dans deux formats peut donc différer de quelques ms.

« Animée » = `is_animated_entry(entry)` : `is_animated_gif` (GIF) **ou** `is_animated_image` (WebP/PNG/AVIF, posé par `set_animated_image_info`, voir skill `archive-image-loading`). Ne jamais tester `is_animated_gif` seul : un WebP animé passerait pour une image fixe et perdrait son animation à la jointure.

La fonction ne lit que les métadonnées posées au chargement, jamais les bytes : elle est appelée à chaque construction de menu et à chaque rafraîchissement de la colonne d'icônes.

## Point d'entrée — `open_merge_window(parent, callbacks)`

Câblé depuis trois endroits, tous vers `PanelWidget._merge_callbacks()` (`panel_widget.py`) puis `mw._open_merge_window_qt` (`build_menubar_callbacks`, `menubar_callbacks_qt.py`). Les trois sont **grisés** quand la jointure est impossible, chacun calculant `animations_joinable` sur les entrées images de la sélection :
- menu contextuel d'une vignette : `can_join` dans `show_image_context_menu` (`context_menus_qt.py`) ;
- menu Images : `can_join` dans `_populate_images_menu` (`menubar_qt.py`, voir skill `menu-bar`) ;
- colonne d'icônes : règle `join_pages` de `_ACTIVATION_RULES` (`icon_toolbar_qt.py`), qui lit le state getter `animations_joinable` de `build_icon_toolbar` (voir skill `icon-toolbar`).

Toute nouvelle entrée vers la jointure doit appliquer la même règle (au moins 2 images sélectionnées **et** `animations_joinable`).

Garde-fous dans `open_merge_window`, avant d'ouvrir la fenêtre :
- moins de 2 entrées sélectionnées → `MsgDialog` "sélection insuffisante", rien ne s'ouvre ;
- une entrée sélectionnée n'est pas une image (dossier, ComicInfo.xml...) → `MsgDialog` "sélection invalide" ;
- `animations_joinable` refuse → retour silencieux. Simple filet de sécurité : les commandes sont déjà grisées dans ce cas, aucun message n'est prévu.

**Dict `callbacks`** attendu (même style que les autres contrats du projet — voir skills `web-import`/`batch-processing`) : `save_state`, `render_mosaic`, `update_button_text`, `clear_selection`, `renumber_no_save`, `canvas` (canvas mosaïque du panneau, pour le texte de progression de la jointure animée), `state` — construit uniquement par `PanelWidget._merge_callbacks()`, jamais assemblé à la main ailleurs.

## `MergeDialog` — disposition 2D puis fusion

Fenêtre non-modale en deux parties : un canvas de disposition libre en haut, une prévisualisation en bas. À la construction, elle mémorise `_source_entries` (les entrées sélectionnées, par objet) et `_doc_generation` (`state.doc_generation`, voir skill `file-close`), utilisés jusqu'à l'insertion du résultat.

### Cycle de vie et images décodées

- **`WA_DeleteOnClose`** : la fenêtre est détruite à sa fermeture (croix, Annuler, Échap, jointure lancée). Masquée seulement, elle resterait enfant du panneau jusqu'à la fin de la session, avec les pages sources — y compris celles que la jointure supprime, que plus rien d'autre ne référence — et leurs images décodées. `_ask_delete_sources` est appelée **après** la fermeture (jointure fixe) ou à la fin du worker (jointure animée) : elle ne doit lire que des attributs Python de la fenêtre (`_state`, `_source_entries`, `_doc_generation`, `_callbacks`), jamais appeler une méthode Qt sur `self`, dont l'objet C++ est alors détruit.
- **Images décodées gardées par la fenêtre, jamais par les pages** : `_decoded_source(entry, cache)` décode une page source par `ensure_image_loaded` (orientation EXIF, première image d'une animation), range l'image dans `MergeDialog._decoded` (partagé avec `MiniMosaicCanvas`) et remet `entry["img"]` à `None`. Une image déjà décodée sur la page (visionneuse ouverte) est reprise sans être touchée. Vignettes, aperçu et jointure passent tous par elle. `_release_images`, branchée sur `finished`, arrête le minuteur de l'aperçu et vide le cache : la fenêtre peut rester référencée côté Python jusqu'à la réponse à la question sur les sources.
- **`_image_positions(positions)`** : les positions de `get_positions_data()` avec, à la place de chaque page, `{"img": image de la fenêtre}`. C'est la seule entrée donnée à `detect_merge_adjustment` et `merge_images_2d`, qui lisent `entry["img"]`. **Piège** : leur passer les positions des pages lève `AttributeError` (`entry["img"]` vide).
- La page jointe est créée sans image décodée : une retouche suivante la relit depuis ses bytes, tels qu'ils seront enregistrés.
- Garde-fou : `tests/test_merge_dialog_memory.py` (aucun autre décodage ni image posée sur une page dans le module, appels des fonctions d'assemblage, destruction et libération).

### `MiniMosaicCanvas` — disposition par glisser-déposer

Widget peint entièrement à la main (`QPainter`, pas de `QGraphicsScene`) affichant une miniature par page sélectionnée, librement déplaçable :
- **Position initiale** : grille 4 colonnes (`_init_positions`), pas l'ordre de sélection affiché en ligne — l'utilisateur réorganise ensuite librement.
- **Snap magnétique** (`_apply_drag_position`, `SNAP_DIST = 15px`) : pendant un drag, calcule les 4 alignements possibles avec chaque autre vignette (bord gauche/droit/haut/bas), choisit le plus proche parmi les candidats horizontaux et verticaux, et **vérifie l'absence de chevauchement** (`would_overlap`) avant d'appliquer le snap — un snap qui provoquerait une collision est ignoré. Dessine des lignes de guidage rouges (pleines pour l'alignement, pointillées pour l'axe) pendant le glissement.
- **Autoscroll** pendant le drag près des bords du `QScrollArea` parent (`_update_autoscroll`/`_on_autoscroll_tick`, timer 60fps) : au lieu de simplement translater la vignette selon le delta de scroll, **relit la position réelle du curseur système** (`QCursor.pos()`) et rejoue le calcul complet de `_apply_drag_position` — nécessaire car le scroll change l'origine du canvas sous un curseur resté immobile à l'écran ; une simple translation romprait le calcul de snap.
- **Numéro affiché sous chaque vignette** = position dans `self._thumbs` (ordre d'insertion depuis `state.selected_indices` trié), pas recalculé après un drag — purement indicatif, ne détermine pas l'ordre de fusion (déterminé géométriquement par ligne/colonne, voir plus bas).
- `get_positions_data()` — retourne `[{"entry", "x", "y"}, ...]`, c'est **le seul contrat** entre ce widget et l'algorithme de fusion (`merge_images_2d`, et `merge_animated_2d` via une copie des bytes).

### Prévisualisation en direct — `_update_preview` / debounce

Recalculée à chaque déplacement significatif, mais **débattue** (`QTimer` `singleShot`, 150ms, redémarré à chaque appel) plutôt qu'à chaque pixel de `mouseMoveEvent` — la recomposition redimensionne les images sources en pleine résolution (coûteux).

- Réutilise **le vrai algorithme de fusion** (`merge_images_2d`) sur des copies réduites des images (`PREVIEW_SRC_MAX = 400px` sur la plus grande dimension) plutôt qu'un rendu approximatif — garantit que les proportions relatives entre images de tailles très différentes (ex. une page issue d'un join précédent, disproportionnée) restent fidèles au résultat final, ce que le placement libre du canvas du haut (purement visuel, tailles de vignette fixes) ne représente pas.
- **Un seul facteur d'échelle commun** à toutes les images (basé sur la plus grande dimension parmi toutes les sources), pas un `thumbnail()` individuel par image — un rescale indépendant casserait la cohérence des largeurs/hauteurs relatives dès que les ratios diffèrent entre sources.
- `ask_adjustment_func=None` pour la prévisualisation — pas de dialogue d'ajustement pendant l'aperçu, contrairement à la fusion réelle.
- Images animées : l'aperçu montre la première frame (`_decoded_source`), la vraie jointure assemble toutes les frames.

### `_on_join` / `_finish_join` — la fusion réelle

1. **Détection** (`detect_merge_adjustment`, calcul pur, aucune UI) : si les hauteurs diffèrent au sein d'une ligne, ou les largeurs de ligne diffèrent entre lignes, un ajustement est nécessaire. Si oui → `SizeAdjustmentDialog` (non-modal) demande le mode ; la suite n'est enchaînée qu'après la réponse.
2. `_finish_join` abandonne (ferme la fenêtre) si `state.doc_generation` a changé depuis l'ouverture : les pages n'appartiennent plus au panneau. Il est sans effet, fenêtre ouverte, si une opération en arrière-plan (rotation…) tourne sur le document (`state.operation_running()`, skill `undo-redo`). Puis aiguillage : sources animées (`is_animated_entry` sur la première, `animations_joinable` garantissant toutes ou aucune) → `_start_animated_join` (section suivante) ; sinon fusion fixe synchrone.
3. **Fusion fixe** : format de sortie décidé **avant** la fusion — celui d'origine si toutes les sources partagent la même extension (`ext_to_format` : JPEG et ses synonymes JFIF/PJPEG/PJP → JPEG `.jpg`, GIF, PNG, WebP, BMP, TIFF, AVIF), sinon PNG par défaut. ICO n'y figure volontairement pas (une icône est limitée à 256 px et bloque la sauvegarde CBZ) : des sources toutes ICO donnent un PNG. Il décide `keep_alpha` : transparence des pages gardée si le format la stocke (PNG, WebP, AVIF, TIFF, GIF en tout ou rien) et qu'une source en a (`image_ops._has_alpha`). Puis `merge_images_2d(..., keep_alpha=keep_alpha)` (mode déjà décidé passé en callback figé `lambda dt, dl: adjustment_mode` — aucun second dialogue ne peut se déclencher ici). DPI hérité de la première image source qui en porte un (`image_dpi`). Profil ICC (`page_icc_profile`) repris seulement s'il est le même pour toutes les sources. La fenêtre se ferme, puis `_ask_delete_sources`.
4. **Suppression des sources ?** — `_ask_delete_sources(merge_parent, new_entry)`, commun aux jointures fixe et animée, voir section dédiée.

### Jointure animée — `_start_animated_join` / `_AnimatedJoinWorker`

L'encodage de dizaines de frames en pleine résolution peut prendre plusieurs secondes : la jointure animée tourne dans un QThread, avec le texte rouge de progression `labels.joining` et le bouton `[ Annuler ]` sur la mosaïque du panneau (`callbacks["canvas"]`, mécanisme `show_canvas_text`/`_show_cancel_item`, voir skill `canvas-overlay-progress`). La fenêtre se ferme dès le lancement.

- **Format de sortie** : celui des sources si elles partagent leur extension (GIF, WebP, PNG, AVIF), sinon GIF.
- **Copie des données sur le thread UI** avant le lancement : `sources = [{"bytes", "x", "y"}]` et les durées de la première source. Le worker ne lit jamais les entrées, que le panneau peut modifier pendant le traitement.
- **`merge_animated_2d(sources, durations, adjustment_mode, save_format, progress_cb, is_cancelled)`** (`image_ops.py`) : ouvre toutes les sources, et pour chaque index de frame `i`, se positionne sur la frame `i` de chacune (`seek`), l'aplatit sur fond blanc (`_flatten_on_white`), puis réutilise `merge_images_2d` avec le mode choisi. Les frames assemblées sont enregistrées en une image animée (durées de la première source, boucle de la première source ou infinie). Retourne les bytes, ou `None` si annulé.
- **Annulation** (`_cancel`, aussi inscrite par `register_cancel_on_close` pour la fermeture du fichier) : pose `worker._cancelled` (`threading.Event`, convention reconnue par `stop_running_qthreads`) et masque l'overlay. Rien n'ayant été écrit dans `state`, il n'y a aucun rollback.
- **Fin** (`on_done`) : libère le worker (`dispose_qthread`, retrait de `register_cancel_on_close`), abandonne le résultat si annulé ou si `state.doc_generation` a changé, affiche `ErrorDialog` (`messages.errors.join_failed`) si l'assemblage a échoué (`data is None`), sinon crée l'entrée (`create_entry`, nom `Collage_XX`, extensions `archive_names.IMAGE_EXTS` comme `_finish_join` — jamais une liste locale) et enchaîne sur `_ask_delete_sources`. Séquence exécutée par `tests/test_operation_lifecycle.py` (fixture `run_join`, faux worker dont le test émet les signaux) : fin normale (page jointe, question sur les sources posée au panneau, la fenêtre de jointure étant fermée), format commun gardé ou GIF, Annuler, fichier fermé, document remplacé, échec signalé ; worker libéré dans tous les cas.

## `_ask_delete_sources` — les 3 réponses de `YesNoCancelDialog`

Le dialogue est non-modal, et la jointure animée laisse le temps d'agir pendant le traitement : la sélection et l'ordre des pages peuvent avoir changé depuis le clic sur « Joindre ». À la réponse :
- abandon si `state.doc_generation` diffère de celui capturé à l'ouverture de `MergeDialog` (document fermé ou remplacé) ;
- opération en arrière-plan lancée pendant la question : la réponse est appliquée à sa fin (`state.run_when_idle`, skill `undo-redo`), sinon son annulation effacerait la planche ;
- les pages sources sont retrouvées **par identité d'objet** dans `images_data` (`source_entries` = `MergeDialog._source_entries`), jamais par `state.selected_indices` ni par des indices figés ; `first_join_idx` = la plus petite position trouvée (fin de liste si aucune source n'est plus présente).

| Réponse | Effet |
|---|---|
| **Annuler** | Rien n'est modifié dans `state` — juste `render_mosaic()`/`update_button_text()`, aucun point undo créé. |
| **Oui** (supprimer les sources) | Les pages sources retrouvées **sauf** la première sont retirées (`images_data.pop`, ordre décroissant pour ne pas décaler les index restants), puis `images_data[first_join_idx] = new_entry` — la page fusionnée **prend la place** de la première page source. Si aucune source n'est plus présente, traité comme Non. |
| **Non** (conserver les sources) | Aucune suppression : `images_data.insert(first_join_idx, new_entry)` — la page fusionnée **s'intercale juste avant** la première page source. |

**Cas particulier `renumber_mode == 0` (OFF)** : la page fusionnée est toujours insérée en **tête** de mosaïque (`insert_idx = 0`), sous son nom `Collage_XX` brut. La réponse est respectée comme dans les autres modes : « Oui » supprime les pages sources (retrouvées par identité), « Non » les garde.

## Undo/redo — deux points encadrant la modification

Suit le pattern standard (voir skill `undo-redo`), mais via `save_state_data` importé **directement** de la couche pure (`from modules.qt.undo_redo import save_state_data as _save_state_data`), pas `save_state_qt` — le rafraîchissement toolbar est géré séparément par les callbacks du dialogue (`update_button_text`) :

1. `_save_state_data(state, force=True)` **avant** toute modification de `images_data` — `force=True` nécessaire car rien n'a encore changé à cet instant (voir skill `undo-redo`, section « `force=True` — pourquoi et quand »).
2. Modification de `images_data` selon la réponse (voir tableau ci-dessus).
3. `sync_pages_in_xml_data(state)` — voir skill `comicinfo-metadata-editor`.
4. `clear_selection()` — la sélection est vidée **avant** la renumérotation éventuelle.
5. **Renumérotation conditionnelle** (`renumber_mode != 0`) : `self._callbacks["renumber_no_save"](on_done=_finish)` — potentiellement **asynchrone** (dialogue non-modal "1ère page multiple", voir skill `renumbering`) ; `_finish` n'est appelé qu'**après** résolution complète de la renumérotation.
6. Dans `_finish()` : la nouvelle entrée est retrouvée **par identité d'objet** (`e is new_entry`), pas par l'index d'insertion — la renumérotation pourrait réordonner la liste. Sa position est ajoutée à `selected_indices` (la page fusionnée devient la sélection).
7. `_save_state_data(state, force=True)` **après** modification complète (y compris après renumérotation) — deuxième point undo, celui que redo restaure.
8. `render_mosaic()` + `update_button_text()`.

## Algorithme d'assemblage — `image_ops.py`

Logique purement géométrique, aucune dépendance Qt — testée dans `tests/test_image_ops.py` (dont `animations_joinable` et `merge_animated_2d`).

### Regroupement en lignes (`detect_merge_adjustment` et `merge_images_2d`, dupliqué à l'identique entre les deux fonctions)

1. Trie les items par position Y, les regroupe en "lignes" si leur Y diffère de moins de `align_threshold = 20px` (seuil sur les coordonnées miniatures du canvas, pas sur les pixels réels des images).
2. Trie chaque ligne par X (gauche à droite).
3. **Détection d'ajustement nécessaire** :
   - Si une ligne contient plusieurs images de hauteurs différentes → ajustement de type `'height'`.
   - Sinon, si plusieurs lignes ont des largeurs totales différentes → ajustement de type `'width'`.
   - Sinon, aucun ajustement nécessaire, fusion directe.

**`detect_merge_adjustment` et `merge_images_2d` dupliquent cette détection** — la première existe uniquement pour poser la question à l'utilisateur **avant** de lancer la fusion réelle (UI non-modale asynchrone), la seconde refait le calcul en interne pour décider si son propre `ask_adjustment_func` doit être appelé. Toute modification du critère de détection doit être répliquée dans les deux fonctions.

### `merge_images_horizontally()` — fusion d'une ligne, 3 modes

- `'keep_original'` : colle les images telles quelles, sans redimensionnement — la ligne résultante peut avoir des bandes blanches irrégulières si les hauteurs diffèrent (comportement par défaut si aucun ajustement n'était nécessaire).
- `'enlarge_small'` : agrandit chaque image plus petite que la plus haute de la ligne (`max_height`) jusqu'à cette hauteur, ratio conservé (largeur recalculée proportionnellement), LANCZOS.
- `'reduce_large'` : réduit chaque image plus grande que la plus petite (`min_height`) jusqu'à cette hauteur, même logique inverse.

### Assemblage final multi-lignes (`merge_images_2d`, après fusion de chaque ligne)

- Calcule un offset X réel pour chaque ligne en se basant sur la **ligne de référence** (celle avec le plus d'images) : construit une table position-miniature → offset-pixels-réel cumulé, puis pour toute position X d'une autre ligne, retrouve l'offset réel de l'image la plus proche en X dans la ligne de référence (`mini_x_to_real_offset`). Permet de préserver approximativement l'alignement horizontal voulu par l'utilisateur dans le canvas de disposition, même si les lignes n'ont pas le même nombre d'images.
- **Planche** (`_merge_canvas`, aussi pour chaque ligne dans `merge_images_horizontally`/`merge_images_vertically`) : fond **blanc opaque** pour les zones qu'aucune image ne couvre, RGB, ou RGBA avec `keep_alpha`. Chaque image y est préparée par `_merge_source` (16 bits ramenés à 256 nuances par `to_display_mode` ; avec `keep_alpha` convertie en RGBA, sinon aplatie sur blanc par `_flatten_on_white` si elle a de la transparence) puis collée **sans masque** : ses pixels, alpha compris, remplacent le fond. **Piège** : une image transparente collée sans masque sur une planche RGB laisserait voir les couleurs cachées sous ses zones transparentes — d'où l'aplatissement préalable hors `keep_alpha`.

## Interaction avec les onglets

`sync_pages_in_xml_data(state)` (voir skill `comicinfo-metadata-editor`) régénère la section `<Pages>` du `ComicInfo.xml` si présent, ce qui met à jour `state.comic_metadata['pages']` — l'onglet métadonnées reflète alors la nouvelle page fusionnée avec ses attributs recalculés (`ImageWidth`/`ImageHeight`/`ImageSize`). Pas de rafraîchissement d'onglet explicite dans `merge_dialog_qt.py` lui-même : ce sont les callbacks fournis (`render_mosaic`, `update_button_text`) qui déclenchent la suite, pas un `update_tabs_cb` direct comme dans le cas undo/redo standard — si un onglet doit refléter le changement, vérifier que le chemin d'appel s'en charge (voir skill `undo-redo`, callback `update_tabs_cb` normalement passé par `_undo_redo_callbacks()`, absent ici car ce n'est pas un vrai undo/redo mais une modification directe).

## Interaction avec les panneaux

Un seul panneau à la fois : `MergeDialog` reçoit `callbacks['state']` et `callbacks['canvas']` (ceux du panneau appelant) — aucune interaction cross-panel, contrairement au drag & drop inter-panneaux (voir skill `drag-and-drop`). Fusionner dans panel1 ne touche jamais `images_data` de panel2, y compris quand la jointure animée se termine après un changement de panneau actif.

## Comment étendre

- **Ajouter un mode d'ajustement de taille** : ajouter la branche dans `merge_images_horizontally()` (`image_ops.py`), un radio bouton dans `SizeAdjustmentDialog` (`merge_dialog_qt.py`), et les clés de traduction `dialogs.join.*` correspondantes (voir skill `add-translation`). La jointure animée en profite automatiquement (elle réutilise `merge_images_2d` pour chaque frame).
- **Changer le format de sortie de la page fusionnée** : `ext_to_format`/`out_ext` dans `_finish_join()` pour les images fixes (PNG si les extensions sont mixtes), `ext_to_format` de `_start_animated_join()` pour les animées (GIF si les extensions sont mixtes ; seuls les formats capables de stocker une animation y figurent).
- **Assouplir ou durcir la règle des images animées** : uniquement `animations_joinable()` (et `_JOIN_DURATION_TOLERANCE_MS`) — les trois commandes et `open_merge_window` l'appellent, aucune condition n'est dupliquée ailleurs. Mettre à jour `tests/test_image_ops.py` et le mode d'emploi (`help.join_pages_content`).
- **Changer le nommage des pages fusionnées** (`Collage_XX`) : `_get_next_collage_number()` scanne les préfixes `Collage`/`Merged`/`Fusionado`/`Zusammengeführt` (liste en dur, pas générée depuis les traductions) pour éviter les collisions de numéro — ajouter un nouveau préfixe à cette liste si une langue supplémentaire utilise un mot différent.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour toute modification des 3 dialogues (`MergeDialog`, `SizeAdjustmentDialog`, `YesNoCancelDialog` — tous déjà non-modaux, `_wt()` pour les titres déjà en place).

## Pièges connus

- **`detect_merge_adjustment` et `merge_images_2d` dupliquent la détection** — toute modification du critère (seuil d'alignement, comparaison de dimensions) doit être répliquée dans les deux fonctions, sinon la question posée à l'utilisateur (`SizeAdjustmentDialog`) et le comportement réel de fusion peuvent diverger.
- **Ne jamais tester `is_animated_gif` seul** pour décider d'une jointure : passer par `is_animated_entry`/`animations_joinable`, sinon un WebP/PNG/AVIF animé est traité comme une image fixe et perd son animation.
- **Les pages sources se retrouvent par identité d'objet au moment de la réponse**, jamais par `state.selected_indices` : la question « supprimer les sources ? » est non-modale et la jointure animée asynchrone, la sélection a pu changer entre-temps et les mauvaises pages seraient supprimées.
- **Le worker de la jointure animée ne doit lire que la copie `sources`**, jamais `pos["entry"]` : le panneau peut modifier ces entrées pendant le traitement.
- **Une frame RGBA de jointure animée est aplatie sur fond blanc avant d'être collée** (`_flatten_on_white`, `merge_animated_2d`) ; la jointure fixe passe par `_merge_source` (voir « Assemblage final »).
- **`renumber_mode == 0` a un placement distinct** (insertion en tête, jamais à la position de la première page source) ; la réponse Oui/Non/Annuler, elle, s'applique comme dans les autres modes.
- **La nouvelle entrée est retrouvée par identité d'objet (`is`), jamais par index** après la renumérotation potentiellement asynchrone.
- **La réponse « Annuler »** ne fait rien à `state` — un ajout de logique dans cette branche doit rester conscient qu'aucune modification n'a eu lieu.
- **Traduction des fenêtres du module débranchée aussi à leur destruction** : `_connect_lang` branche `_disconnect_lang` sur `finished` **et** sur `destroyed`. `SizeAdjustmentDialog` est fille de `MergeDialog` : fermer la jointure pendant que la question d'ajustement est affichée la détruit sans `finished` ni `_finish`, et sa traduction resterait branchée sur `language_signal` (erreur à chaque changement de langue). `_disconnect_lang` ne déconnecte qu'une fois (`_lang_handler` remis à `None`) : PySide6 avertit en console d'une seconde déconnexion.
- **Le fond de la page fusionnée est toujours blanc opaque**, jamais transparent — une image source avec canal alpha perd sa transparence dans le résultat (images fixes comme animées).

## Références croisées

- `animated-gif` — fenêtre des GIF animés (création/édition), distincte de la jointure.
- `page-split` — scission, qui garde aussi l'animation de chaque morceau ; ses morceaux sont le cas typique de jointure animée.
- `canvas-overlay-progress` — texte rouge de progression et bouton Annuler de la jointure animée.
- `file-close` — `register_cancel_on_close` et `doc_generation`.
- `icon-toolbar`, `menu-bar` — grisage des commandes de jointure.
