---
name: page-merge
description: Localiser ou modifier la fusion/jointure de pages (assemblage d'images sélectionnées en une planche, disposition 2D par glisser-déposer, ajustement de taille). Utiliser dès qu'une tâche touche à merge_dialog_qt.py, MergeDialog, merge_images_2d, ou open_merge_window.
---

# Fusion / jointure de pages — MosaicView

Assemble plusieurs pages sélectionnées (au moins 2, toutes images) en une seule image composite ("Collage_XX.ext"), qui remplace ou s'insère à la place des pages sources dans la mosaïque. Usage typique : rassembler une planche double/triple scannée en plusieurs fichiers séparés. Ne pas confondre avec la **fusion d'archives** (`import_merge_qt.py`, ajouter le contenu d'une deuxième archive à la suite — voir skill `archive-image-loading`) : ici on combine visuellement des *images déjà présentes* dans le même comic en une planche unique. Opération conceptuellement inverse de la **scission** (voir skill `page-split`) — mais attention, la scission ne retire jamais l'image source alors que la fusion, elle, peut remplacer ou supprimer les sources selon la réponse de l'utilisateur ; ce ne sont pas des symétriques parfaites.

## Fichiers clés

- **`modules/qt/merge_dialog_qt.py`** (~1080 lignes) — tout le mécanisme UI : `MergeDialog` (fenêtre principale), `MiniMosaicCanvas` (disposition 2D par glisser-déposer avec snap magnétique), `SizeAdjustmentDialog` (choix du mode d'ajustement de taille), `YesNoCancelDialog` (suppression des sources), point d'entrée public `open_merge_window()`.
- **`modules/qt/image_ops.py`** — logique métier pure (PIL, aucun Qt) : `merge_images_2d()` (assemblage final selon les positions 2D), `merge_images_horizontally()` (fusion d'une ligne, avec ajustement de taille), `detect_merge_adjustment()` (détection préalable, sans fusionner, pour poser la question à l'utilisateur avant).
- **`modules/qt/panel_widget.py`** — `_merge_callbacks()` (contrat de callbacks fourni au dialogue).
- **`modules/qt/entries.py`** — `create_entry()` pour fabriquer l'entrée de la page fusionnée (voir skill `archive-image-loading`).
- **`modules/qt/comic_info.py`** — `sync_pages_in_xml_data()` appelée après insertion pour garder `<Pages>` cohérent (voir skill `comicinfo-metadata-editor`).
- **`modules/qt/undo_redo.py`** — `save_state_data()` (import direct de la couche pure, pas `save_state_qt`) encadre la modification de `images_data` (voir skill `undo-redo`).

## Point d'entrée — `open_merge_window(parent, callbacks)`

Câblé depuis trois endroits, tous vers `PanelWidget._merge_callbacks()` (`panel_widget.py:1642`) puis `mw._open_merge_window_qt` (`menubar_callbacks_qt.py:99`) :
- Menu contextuel canvas (`context_menus_qt.py:444`) et menu Fichier (`menubar_qt.py:216`) — actifs seulement si sélection valide.
- Bouton "Joindre les pages" de la colonne d'icônes (`icon_toolbar_qt.py:2172`, voir skill `icon-toolbar`).

Garde-fous avant même d'ouvrir la fenêtre :
- Moins de 2 entrées sélectionnées → `MsgDialog` "sélection insuffisante", rien ne s'ouvre.
- Une entrée sélectionnée n'est pas une image (dossier, ComicInfo.xml...) → `MsgDialog` "sélection invalide".

**Dict `callbacks`** attendu (même style que les autres contrats du projet — voir skills `web-import`/`batch-processing` pour des exemples similaires) : `save_state`, `render_mosaic`, `update_button_text`, `clear_selection`, `renumber_no_save`, `state` — construit uniquement par `PanelWidget._merge_callbacks()` (`panel_widget.py:1642`), jamais assemblé à la main ailleurs.

## `MergeDialog` — disposition 2D puis fusion

Fenêtre non-modale en deux parties : un canvas de disposition libre en haut, une prévisualisation en bas.

### `MiniMosaicCanvas` — disposition par glisser-déposer

Widget peint entièrement à la main (`QPainter`, pas de `QGraphicsScene`) affichant une miniature par page sélectionnée, librement déplaçable :
- **Position initiale** : grille 4 colonnes (`_init_positions`), pas l'ordre de sélection affiché en ligne — l'utilisateur réorganise ensuite librement.
- **Snap magnétique** (`_apply_drag_position`, `SNAP_DIST = 15px`) : pendant un drag, calcule les 4 alignements possibles avec chaque autre vignette (bord gauche/droit/haut/bas), choisit le plus proche parmi les candidats horizontaux et verticaux, et **vérifie l'absence de chevauchement** (`would_overlap`) avant d'appliquer le snap — un snap qui provoquerait une collision est ignoré. Dessine des lignes de guidage rouges (pleines pour l'alignement, pointillées pour l'axe) pendant le glissement.
- **Autoscroll** pendant le drag près des bords du `QScrollArea` parent (`_update_autoscroll`/`_on_autoscroll_tick`, timer 60fps) : au lieu de simplement translater la vignette selon le delta de scroll, **relit la position réelle du curseur système** (`QCursor.pos()`) et rejoue le calcul complet de `_apply_drag_position` — nécessaire car le scroll change l'origine du canvas sous un curseur resté immobile à l'écran ; une simple translation romprait le calcul de snap.
- **Numéro affiché sous chaque vignette** = position dans `self._thumbs` (ordre d'insertion depuis `state.selected_indices` trié), pas recalculé après un drag — purement indicatif, ne détermine pas l'ordre de fusion (celui-ci est déterminé géométriquement par ligne/colonne, voir plus bas).
- `get_positions_data()` — retourne `[{"entry", "x", "y"}, ...]`, c'est **le seul contrat** entre ce widget et l'algorithme de fusion (`merge_images_2d`).

### Prévisualisation en direct — `_update_preview` / debounce

Recalculée à chaque déplacement significatif, mais **débattue** (`QTimer` `singleShot`, 150ms, redémarré à chaque appel) plutôt qu'à chaque pixel de `mouseMoveEvent` — la recomposition redimensionne les images sources en pleine résolution (coûteux).

- Réutilise **le vrai algorithme de fusion** (`merge_images_2d`) sur des copies réduites des images (`PREVIEW_SRC_MAX = 400px` sur la plus grande dimension) plutôt qu'un rendu approximatif — garantit que les proportions relatives entre images de tailles très différentes (ex. une page issue d'un join précédent, disproportionnée) restent fidèles au résultat final, ce que le placement libre du canvas du haut (purement visuel, tailles de vignette fixes) ne représente pas.
- **Un seul facteur d'échelle commun** à toutes les images (basé sur la plus grande dimension parmi toutes les sources), pas un `thumbnail()` individuel par image — un rescale indépendant casserait la cohérence des largeurs/hauteurs relatives dès que les ratios diffèrent entre sources.
- `ask_adjustment_func=None` pour la prévisualisation — pas de dialogue d'ajustement pendant l'aperçu, contrairement à la fusion réelle (voir plus bas).

### `_on_join` / `_finish_join` — la fusion réelle, en 3 étapes asynchrones

1. **Détection** (`detect_merge_adjustment`, calcul pur, aucune UI) : si les hauteurs diffèrent au sein d'une ligne, ou les largeurs de ligne diffèrent entre lignes, un ajustement est nécessaire. Si oui → `SizeAdjustmentDialog` (non-modal) demande le mode ; le dialogue **suivant** (`YesNoCancelDialog`) n'est enchaîné qu'après la réponse, jamais avant.
2. **Fusion PIL** (`merge_images_2d`, mode déjà décidé passé en callback figé `lambda dt, dl: adjustment_mode` — aucun second dialogue ne peut se déclencher ici) : produit une seule image PIL, encodée dans le format d'origine si toutes les sources partagent la même extension, sinon PNG par défaut. DPI hérité de la première image source qui en porte un.
3. **Suppression des sources ?** (`YesNoCancelDialog`, Oui/Non/Annuler) — voir section suivante pour ce que chaque réponse fait exactement à `images_data`.

## Les 3 réponses de `YesNoCancelDialog` — effet sur `images_data`

`first_join_idx = min(state.selected_indices)` est capturé **avant** toute modification de `state` — position de référence pour l'insertion, quelle que soit la réponse :

| Réponse | Effet |
|---|---|
| **Annuler** | Rien n'a encore été modifié dans `state` (la fusion PIL a eu lieu, mais aucune écriture dans `images_data`) — juste `render_mosaic()`/`update_button_text()` pour rafraîchir l'UI, aucun point undo créé. |
| **Oui** (supprimer les sources) | Toutes les pages sélectionnées **sauf** `first_join_idx` sont retirées (`images_data.pop`, ordre décroissant pour ne pas décaler les index restants), puis `images_data[first_join_idx] = new_entry` — la page fusionnée **prend la place** de la première page source. |
| **Non** (conserver les sources) | Aucune suppression : `images_data.insert(first_join_idx, new_entry)` — la page fusionnée **s'intercale juste avant** la première page source, qui reste inchangée à sa place (désormais décalée d'un cran). |

**Cas particulier `renumber_mode == 0` (OFF)** : indépendamment de la réponse Oui/Non, comportement **historique** différent — la page fusionnée est toujours insérée en **tête** de mosaïque (`insert_idx = 0`), sous son nom `Collage_XX` brut, sans toucher aux pages sources ni les supprimer. Ce cas court-circuite la logique Oui/Non décrite ci-dessus.

## Undo/redo — deux points encadrant la modification

Suit le pattern standard (voir skill `undo-redo`), mais via `save_state_data` importé **directement** de la couche pure (`from modules.qt.undo_redo import save_state_data as _save_state_data`), pas `save_state_qt` — parce que le rafraîchissement toolbar est géré séparément par les callbacks du dialogue (`update_button_text`), pas par le wrapper Qt standard :

1. `_save_state_data(state, force=True)` **avant** toute modification de `images_data` — `force=True` nécessaire car rien n'a encore changé à cet instant (même raison que documentée dans le skill `undo-redo` pour `NameEdit`).
2. Modification de `images_data` selon la réponse (voir tableau ci-dessus).
3. `sync_pages_in_xml_data(state)` — voir skill `comicinfo-metadata-editor`.
4. `clear_selection()` — la sélection est vidée **avant** la renumérotation éventuelle, pas restaurée immédiatement.
5. **Renumérotation conditionnelle** (`renumber_mode != 0`) : `self._callbacks["renumber_no_save"](on_done=_finish)` — potentiellement **asynchrone** (dialogue non-modal "1ère page multiple", voir skill `renumbering`) ; `_finish` n'est appelé qu'**après** résolution complète de la renumérotation, jamais en séquence directe.
6. Dans `_finish()` : la nouvelle entrée est retrouvée **par identité d'objet** (`e is new_entry`), pas par l'index d'insertion figé — nécessaire car la renumérotation ne réordonne pas la liste aujourd'hui mais pourrait le faire à l'avenir (commentaire explicite dans le code). Sa position est ajoutée à `selected_indices` (la page fusionnée devient la sélection après coup).
7. `_save_state_data(state, force=True)` **après** modification complète (y compris après renumérotation) — deuxième point undo, celui que redo restaurerait.
8. `render_mosaic()` + `update_button_text()`.

## Algorithme d'assemblage — `image_ops.py`

Logique purement géométrique, aucune dépendance Qt — testable indépendamment de l'UI.

### Regroupement en lignes (`detect_merge_adjustment` et `merge_images_2d`, dupliqué à l'identique entre les deux fonctions)

1. Trie les items par position Y, les regroupe en "lignes" si leur Y diffère de moins de `align_threshold = 20px` (seuil sur les coordonnées miniatures du canvas, pas sur les pixels réels des images).
2. Trie chaque ligne par X (gauche à droite).
3. **Détection d'ajustement nécessaire** :
   - Si une ligne contient plusieurs images de hauteurs différentes → ajustement de type `'height'`.
   - Sinon, si plusieurs lignes ont des largeurs totales différentes → ajustement de type `'width'`.
   - Sinon, aucun ajustement nécessaire, fusion directe.

**`detect_merge_adjustment` et `merge_images_2d` dupliquent cette détection** (code quasi identique) — la première existe uniquement pour permettre de poser la question à l'utilisateur **avant** de lancer la fusion réelle (UI non-modale asynchrone), la seconde refait le calcul en interne pour décider si son propre `ask_adjustment_func` doit être appelé. Toute modification du critère de détection doit être répliquée dans les deux fonctions.

### `merge_images_horizontally()` — fusion d'une ligne, 3 modes

- `'keep_original'` : colle les images telles quelles, sans redimensionnement — la ligne résultante peut avoir des bandes blanches irrégulières si les hauteurs diffèrent (comportement par défaut si aucun ajustement n'était nécessaire).
- `'enlarge_small'` : agrandit chaque image plus petite que la plus haute de la ligne (`max_height`) jusqu'à cette hauteur, ratio conservé (largeur recalculée proportionnellement), LANCZOS.
- `'reduce_large'` : réduit chaque image plus grande que la plus petite (`min_height`) jusqu'à cette hauteur, même logique inverse.

### Assemblage final multi-lignes (`merge_images_2d`, après fusion de chaque ligne)

- Calcule un offset X réel pour chaque ligne en se basant sur la **ligne de référence** (celle avec le plus d'images) : construit une table position-miniature → offset-pixels-réel cumulé, puis pour toute position X d'une autre ligne, retrouve l'offset réel de l'image la plus proche en X dans la ligne de référence (`mini_x_to_real_offset`). Permet de préserver approximativement l'alignement horizontal voulu par l'utilisateur dans le canvas de disposition, même si les lignes n'ont pas le même nombre d'images.
- Fond blanc RGB (`Image.new('RGB', ..., (255,255,255))`) — jamais transparent, contrairement aux vignettes de la mosaïque qui peuvent avoir un fond damier (voir skill `mosaic-thumbnails`).

## Interaction avec les onglets

`sync_pages_in_xml_data(state)` (voir skill `comicinfo-metadata-editor`) régénère la section `<Pages>` du `ComicInfo.xml` si présent, ce qui met à jour `state.comic_metadata['pages']` — l'onglet métadonnées reflète alors la nouvelle page fusionnée avec ses attributs recalculés (`ImageWidth`/`ImageHeight`/`ImageSize`). Pas de rafraîchissement d'onglet explicite dans `merge_dialog_qt.py` lui-même : ce sont les callbacks fournis (`render_mosaic`, `update_button_text`) qui déclenchent la suite, pas un `update_tabs_cb` direct comme dans le cas undo/redo standard — si un onglet doit refléter le changement, vérifier que le chemin d'appel s'en charge (voir skill `undo-redo`, callback `update_tabs_cb` normalement passé par `_undo_redo_callbacks()`, absent ici car ce n'est pas un vrai undo/redo mais une modification directe).

## Interaction avec les panneaux

Un seul panneau à la fois : `MergeDialog` reçoit `callbacks['state']` (résolu vers `self._state` du panneau appelant) — aucune interaction cross-panel, contrairement au drag & drop inter-panneaux (voir skill `drag-and-drop`). Fusionner dans panel1 ne touche jamais `images_data` de panel2.

## Comment étendre

- **Ajouter un mode d'ajustement de taille** : ajouter la branche dans `merge_images_horizontally()` (`image_ops.py`), un radio bouton dans `SizeAdjustmentDialog` (`merge_dialog_qt.py`), et les clés de traduction `dialogs.join.*` correspondantes (voir skill `add-translation`).
- **Changer le format de sortie de la page fusionnée** : uniquement `ext_to_format`/`out_ext` dans `_finish_join()` — actuellement PNG si les sources ont des extensions mixtes, sinon le format commun.
- **Changer le nommage des pages fusionnées** (`Collage_XX`) : `_get_next_collage_number()` scanne déjà les préfixes `Collage`/`Merged`/`Fusionado`/`Zusammengeführt` (une par langue déjà supportée dans le code, pas généré dynamiquement depuis les traductions) pour éviter les collisions de numéro — ajouter un nouveau préfixe à cette liste si une langue supplémentaire utilise un mot différent.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour toute modification des 3 dialogues (`MergeDialog`, `SizeAdjustmentDialog`, `YesNoCancelDialog` — tous déjà non-modaux, `_wt()` pour les titres déjà en place).

## Pièges connus

- **`detect_merge_adjustment` et `merge_images_2d` dupliquent la détection** — toute modification du critère (seuil d'alignement, comparaison de dimensions) doit être répliquée dans les deux fonctions, sinon la question posée à l'utilisateur (`SizeAdjustmentDialog`) et le comportement réel de fusion peuvent diverger.
- **`renumber_mode == 0` a un comportement historique différent** (insertion en tête, jamais à la position de la première page source) — ne pas supposer que la logique Oui/Non/Annuler s'applique uniformément à tous les modes de renumérotation.
- **La nouvelle entrée est retrouvée par identité d'objet (`is`), jamais par index** après la renumérotation potentiellement asynchrone — un futur changement qui réordonnerait la liste pendant la renumérotation casserait silencieusement le code s'il était réécrit pour utiliser un index figé.
- **`first_join_idx` est capturé avant toute modification** — ne jamais le recalculer après une suppression/insertion partielle, il sert de référence stable pour toute la suite de `_finish_join`.
- **La suppression des sources en cas d'"Annuler"** ne fait rien à `state` — un futur ajout de logique dans cette branche doit rester conscient qu'aucune modification n'a eu lieu, pas la peine de restaurer quoi que ce soit.
- **Le fond de la page fusionnée est toujours blanc opaque**, jamais transparent — une image source avec canal alpha perd sa transparence dans le résultat final (comportement actuel, pas documenté comme configurable).
