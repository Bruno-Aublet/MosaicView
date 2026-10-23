---
name: page-split
description: Localiser ou modifier la scission d'une page de MosaicView (découpe d'une image sélectionnée en N parties égales, horizontale ou verticale). Utiliser dès qu'une tâche touche à split_dialog_qt.py, SplitDialog, ou split_page.
---

# Scission de page — MosaicView

Découpe une **seule** image sélectionnée en N parties égales (2 à 10), horizontalement ou verticalement, et insère les N nouvelles pages juste après l'image d'origine. Usage typique : séparer une planche double/triple scannée en une seule image en pages individuelles — l'opération **inverse** de la fusion (voir skill `page-merge`), mais avec une différence structurelle importante : ici l'image source **reste en place** dans la mosaïque après l'opération, elle n'est pas remplacée ni supprimée.

## Fichier unique — `modules/qt/split_dialog_qt.py`

Tout le mécanisme tient dans un seul fichier (~360 lignes), pas de séparation UI/métier comme pour la fusion (`image_ops.py` vs `merge_dialog_qt.py`) — le découpage est assez simple (crop PIL en bandes égales) pour rester inline dans la fonction `split_page()`.

- **`SplitDialog`** — fenêtre de réglages (nombre de parties, direction), non-modale, taille fixe.
- **`split_page(parent, callbacks)`** — point d'entrée public : valide la sélection, ouvre `SplitDialog`, exécute la découpe si confirmée.

## Point d'entrée — `split_page(parent, callbacks)`

Câblé depuis trois endroits, tous vers `PanelWidget._split_page_callbacks()` (`panel_widget.py`) puis `mw._split_page_qt` (`build_menubar_callbacks`, `menubar_callbacks_qt.py`) :
- Menu contextuel d'une vignette (`show_image_context_menu`, `context_menus_qt.py`) et menu Images (`_populate_images_menu`, `menubar_qt.py`).
- Bouton "Scinder la page" de la colonne d'icônes (`ICON_DEFINITIONS`, `icon_toolbar_qt.py`, activé seulement si `has_selected_images() and selection_count() == 1` — voir skill `icon-toolbar`, `_ACTIVATION_RULES`).

**Dict `callbacks`** (`PanelWidget._split_page_callbacks()`) : `save_state`, `render_mosaic`, `update_button_text`, `state` — plus petit que le contrat de fusion (`page-merge`), pas de `clear_selection`/`renumber_no_save` ici (voir section renumérotation plus bas pour pourquoi).

### Garde-fous avant ouverture du dialogue

Trois vérifications séquentielles, chacune avec son propre `MsgDialog` distinct :
1. Aucune sélection → `messages.warnings.no_selection_split`.
2. Plusieurs entrées sélectionnées → `messages.warnings.multi_selection_split` (contrairement à la fusion qui *exige* au moins 2 sélections, la scission n'en accepte qu'**une seule**, exactement).
3. L'entrée sélectionnée n'est pas une image → `messages.warnings.invalid_selection_split`.
4. `ensure_image_loaded(entry)` échoue (image corrompue/illisible) → même message `invalid_selection_split` réutilisé — pas de message dédié pour ce cas précis.

## `SplitDialog` — réglages

Fenêtre simple à taille fixe (`420×280`), pas de canvas de disposition comme `MergeDialog` :
- **Nombre de parties** : `QSpinBox`, plage `2` à `10` (bornes UI **et** revalidées manuellement dans `_on_ok` — double vérification, la seconde protège contre une valeur qui contournerait le spinbox par un autre moyen). Hors bornes → `MsgDialog` `invalid_number_split`, le dialogue reste ouvert.
- **Direction** : `QButtonGroup` avec 2 `QRadioButton` (horizontal/vertical), **vertical coché par défaut** — cohérent avec le sens de lecture le plus courant des comics (planches doubles côte à côte, découpées verticalement en pages individuelles).
- **Avertissement** (`dialogs.split.warning`) — texte gris italique (couleur adaptée dynamiquement au thème, `#666666` clair / `#999999` sombre — calcul direct dans `_retranslate()`, pas via `theme['disabled']` comme documenté ailleurs dans le projet ; à harmoniser si une refonte de ce dialogue est demandée).
- `num_pages`/`direction` exposés en `@property` sur le dialogue après confirmation, lus par `split_page()` après le callback `ask_async`.
- **Fermeture** : `_finish` (boutons) émet la réponse puis `hide()` + `deleteLater()` ; la croix (`closeEvent`) et Échap (`reject`, qui appelle `close()`) répondent « annulé ». Fenêtre en `WA_DeleteOnClose`. **Piège** : `QDialog.reject` masque la fenêtre sans passer par `closeEvent` — sans ce `reject`, ni réponse, ni destruction. Même modèle pour `ZipCompressionDialog`, `OutlierDialog`, `SizeAdjustmentDialog`/`YesNoCancelDialog` (jointure), `ExtensionCorrectionDialog`, `DpiDialog`, `PdfUnlockDialog` et `_FirstPageDialog`. Garde-fou : `tests/test_check_dialogs_destroyed.py`.

## La découpe réelle — dans `split_page()`, pas dans un module séparé

Contrairement à la fusion (logique dans `image_ops.py`), tout le calcul de découpe est **inline** dans la fonction `_on_confirmed` de `split_page()` (sans effet si une opération en arrière-plan tourne sur le document, `state.operation_running()` — skill `undo-redo` : la fenêtre est non modale, et une découpe pendant une rotation pousserait un état intermédiaire que son annulation prendrait pour l'état d'avant) :

1. **Page retrouvée par identité** (`e is entry` dans `state.images_data`) et **image relue** (`free_image_memory` puis `ensure_image_loaded`) — jamais l'index ni l'image capturés à l'ouverture : la fenêtre est non modale, la page a pu être déplacée, tournée ou supprimée entre-temps. Page disparue ou illisible → rien n'est fait.
2. `callbacks["save_state"]()` **avant** toute modification, puis dimensions lues sur l'image relue (`img.size`).
3. **Chaque partie garde le même format que l'image source**, pas de conversion. Encodage par `_encode_part(part)` (closure de `_on_confirmed`) : JPEG et ses synonymes (`.jpg/.jpeg/.jfif/.pjpeg/.pjp`) et WebP ont leur branche dédiée (point 6), tout autre format passe par `save_image_to_bytes` sur un dict temporaire (`{"img", "extension", "bytes", "dpi"}` — `bytes` d'origine pour retrouver les tailles d'un ICO). **Ne jamais déduire un nom de format Pillow de l'extension** (`ext.upper()[1:]`) : `TIF`, `JFIF`, `PJPEG`, `PJP` ne sont pas des formats Pillow (`KeyError` non intercepté, découpe impossible).
4. **Découpe horizontale** (bandes empilées verticalement, séparées par des lignes horizontales) : `split_height = height / num_pages`, crop `(0, top, width, bottom)` pour chaque bande — malgré le nom "horizontale", c'est la **hauteur** qui est divisée (le résultat produit des pages qui se lisent de haut en bas, chacune sur toute la largeur).
5. **Découpe verticale** (bandes côte à côte, séparées par des lignes verticales) : `split_width = width / num_pages`, crop `(left, 0, right, height)` — c'est la **largeur** qui est divisée (utilisé pour une planche double scannée en un seul fichier large, à séparer en pages individuelles côte à côte).
6. **JPEG** : conversion préalable en RGB si le mode n'est ni RGB, L ni CMYK, sauvegarde `quality=100, subsampling=0` (qualité maximale, pas de sous-échantillonnage chroma — évite toute perte supplémentaire sur un découpage qui ne devrait introduire aucune dégradation visible). **WebP** : sans perte si la page l'est (`_webp_is_lossless`), sinon `quality=100`, sans conversion (alpha conservé). Les deux reportent le DPI (`image_dpi`, sinon `entry["dpi"]`), le profil ICC (`page_icc_profile`) et les métadonnées (`metadata_save_kwargs`, EXIF sans l'orientation : l'image vient d'`ensure_image_loaded`, déjà dans le sens d'affichage). Les autres formats (PNG, BMP, TIFF, GIF, AVIF, ICO) passent par `save_image_to_bytes` (point 3), qui applique les mêmes règles.
   **Image animée** : `_part_bytes(box)` découpe chaque frame, chaque partie reste animée dans le format d'origine — GIF (`is_animated_gif`) via `transform_animated_gif({"bytes": entry["bytes"]}, lambda f: f.crop(box))` (skill `rotate-flip`), WebP/PNG/AVIF (`is_animated_image`) via `transform_animated_image({"bytes": entry["bytes"], "extension": ext}, ...)` (`image_ops.py`, durées et boucle conservées, WebP/AVIF en `quality=100`). Une partie couvrant une zone qui ne bouge dans aucune frame peut ressortir avec une seule frame (Pillow fusionne les frames identiques à l'enregistrement) : ce n'est alors plus une image animée, comportement normal. Les parties d'une même image animée ont les mêmes frames et durées : elles peuvent être rejointes en une image animée (skill `page-merge`, `animations_joinable`).
7. **Nommage** : `{base_name}_part{i+1:02d}{ext}` — suffixe numéroté à 2 chiffres minimum, toujours dans l'ordre de la découpe (partie 1 = bande la plus en haut/à gauche).
8. Chaque partie devient une entrée via `create_entry(new_name, bytes, IMAGE_EXTS)` (voir skill `archive-image-loading`) — un nouvel objet Python distinct par partie, aucun partage de référence entre elles. `IMAGE_EXTS` est la liste de référence importée de `archive_loader.py` (pas une liste locale) : sans elle, les parties d'un ICO/JFIF/PJPEG/PJP ne seraient pas reconnues comme images.

## Insertion dans `images_data` — l'image source n'est jamais retirée

Différence structurelle majeure avec la fusion (`page-merge`, qui remplace ou insère à la place des sources) :

```python
for i, new_entry in enumerate(new_entries):
    state.images_data.insert(idx + 1 + i, new_entry)
```

- L'entrée d'origine (`idx`) **reste dans `images_data`**, à sa position initiale — elle n'est ni supprimée ni remplacée.
- Les N nouvelles parties sont insérées **juste après**, dans l'ordre (partie 1 à `idx+1`, partie 2 à `idx+2`, etc.).
- Résultat après l'opération : `[..., image_originale, partie1, partie2, ..., partieN, ...]` — la mosaïque contient donc **N+1 images** au total pour cette page (l'original + les N morceaux), pas juste N.
- **`free_image_memory(entry)`** est appelée sur l'entrée d'origine juste après l'insertion — libère `entry["img"]` (l'objet PIL décodé) tout en gardant `entry["bytes"]` intacts, car l'image source reste affichée dans la mosaïque mais son objet PIL complet n'est plus utile immédiatement après la découpe (voir skill `archive-image-loading`, section lazy loading).

**Ce comportement (garder l'original) n'est signalé nulle part dans l'UI** — un utilisateur qui s'attend à ce que la scission "remplace" la page par ses morceaux doit supprimer manuellement l'originale après coup s'il le souhaite. Vérifier ce point avant de le changer, ça pourrait être une décision de conception délibérée (permettre de comparer/annuler visuellement) plutôt qu'un oubli.

## Undo/redo — deux points, avant et après

Pattern à deux appels du skill `undo-redo`, comme la fusion de pages (skill `page-merge`) : `save_state()` **avant** la modification, puis à nouveau **après** l'insertion des parties et `sync_pages_in_xml_data`. **Piège** : sans le second appel, Annuler déferait d'un coup la découpe et l'action précédente, et Refaire ne rétablirait pas la découpe.

Voir aussi skills `create-ico` et `animated-gif` : fonctionnalités avec un seul point undo (après insertion), parce qu'il s'agit d'un ajout de nouvelle page (le `.ico`/le GIF généré) plutôt que d'une modification en place.

## Interaction avec les onglets et le ComicInfo.xml

`sync_pages_in_xml_data(state)` (voir skill `comicinfo-metadata-editor`) est appelée après insertion des nouvelles entrées — régénère la section `<Pages>` du `ComicInfo.xml` si présent, pour que les N+1 pages (originale + parties) soient toutes correctement représentées avec leurs attributs recalculés (`ImageWidth`/`ImageHeight`/`ImageSize`).

Pas de callback `update_tabs`/`clear_selection` dans le contrat (`_split_page_callbacks`) — contrairement à la fusion. La sélection **n'est pas modifiée** après la scission : l'entrée originale reste sélectionnée (aucun code ne touche à `state.selected_indices`), les nouvelles parties ne sont jamais automatiquement sélectionnées.

## Interaction avec la renumérotation

**Aucun déclenchement automatique** de renumérotation après une scission — contrairement à la fusion (`page-merge`, qui appelle `renumber_no_save` si `renumber_mode != 0`) et au drag & drop (voir skill `renumbering`, section déclencheurs automatiques). `split_page()` ne touche jamais à `state.needs_renumbering` ni n'appelle de callback de renumérotation. Les nouvelles parties portent un nom généré (`_partNN`) qui reste tel quel jusqu'à ce que l'utilisateur déclenche une renumérotation manuelle (bouton dédié ou menu, voir skill `renumbering`).

**Piège potentiel à vérifier si une tâche touche à ce fichier** : ce silence sur la renumérotation pourrait être un oubli plutôt qu'un choix délibéré, contrairement au cas `renumber_mode == 0` de la fusion qui est un comportement historique explicitement documenté en commentaire. Si le comportement doit être aligné avec la fusion, ajouter l'appel à `callbacks["renumber_no_save"]` suivrait le même pattern que `_finish_join` dans `merge_dialog_qt.py` (voir skill `page-merge`) — mais ne pas le faire sans confirmation explicite, ça changerait un comportement existant.

## Interaction avec les panneaux

Un seul panneau à la fois, comme la fusion : `callbacks['state']` résolu vers `self._state` du panneau appelant (`PanelWidget._split_page_callbacks`) — aucune interaction cross-panel.

## Comment étendre

- **Changer la plage de nombre de parties** (actuellement 2-10) : `self._spinbox.setRange(2, 10)` dans `SplitDialog.__init__` **et** la revalidation manuelle dans `_on_ok` (`if num_pages < 2 or num_pages > 10`) — les deux bornes doivent rester synchronisées.
- **Ajouter un mode de découpe supplémentaire** (ex. parties inégales, découpe par pourcentage) : nouvelle option dans `SplitDialog` (radio ou combo), nouvelle branche dans `_on_confirmed` de `split_page()` — pas de fonction métier séparée à réutiliser, le calcul de crop est actuellement inline.
- **Changer le nommage des parties** (`_partNN`) : uniquement la f-string `new_name = f"{base_name}_part{i+1:02d}{ext}"`, dans les deux branches horizontale/verticale (dupliqué, à modifier aux deux endroits).
- **Faire disparaître l'image source après scission** (si demandé explicitement) : ajouter un retrait de `state.images_data[idx]` après l'insertion des parties — attention à décaler `idx+1+i` en conséquence si ce changement est fait, et à vérifier l'impact sur `free_image_memory` (qui deviendrait inutile si l'entrée est supprimée).
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour `SplitDialog` (non-modale déjà en place, `_wt()` pour le titre déjà en place).

## Tests

`tests/test_split_page.py` exécute `split_page` (extraite par `tests/ast_loader.py`, `SplitDialog` et `MsgDialog` remplacés ; le test choisit le nombre de morceaux, le sens et la réponse) : morceaux exacts insérés après la page, deux points d'historique ; JPEG avec profil et DPI, WebP sans perte resté sans perte pixel pour pixel, GIF animé avec toutes ses images ; rien si la page a été supprimée ou une opération lancée pendant que la fenêtre était ouverte, ni sur Annuler ; sélection vide ou multiple. Une nouvelle dépendance de `split_page` s'ajoute à l'espace de noms du test (le test échoue sinon en la nommant).

## Pièges connus

- **L'image source n'est jamais retirée après scission** — ne pas supposer que "scinder" remplace la page comme le fait "fusionner" (voir skill `page-merge`) ; c'est le contraire, l'original persiste et les parties s'ajoutent.
- **Ne jamais utiliser l'index ou l'image capturés à l'ouverture de `SplitDialog`** — fenêtre non modale : à la validation, la page est retrouvée par identité et relue (voir « La découpe réelle »). Avec l'index d'ouverture, la découpe partirait d'une autre page si la mosaïque a changé entre-temps ; avec l'image d'ouverture, une rotation faite entre-temps serait perdue dans les parties.
- **Aucune renumérotation automatique déclenchée** — contrairement à la fusion et au drag & drop. Vérifier avec l'utilisateur avant d'en ajouter une, ce n'est peut-être pas un oubli.
- **"Horizontal" divise la hauteur, "vertical" divise la largeur`** — la terminologie fait référence à l'orientation des lignes de séparation entre les parties, pas à l'axe le long duquel les dimensions sont mesurées ; source de confusion possible si le code est modifié sans relire attentivement quelle branche fait quoi.
- **`ensure_image_loaded` échoué retombe sur le même message que "sélection invalide"** — un diagnostic depuis un rapport utilisateur doit distinguer ces deux cas en pratique (image manquante vs image illisible) même si le message affiché est identique.
- **La couleur du texte d'avertissement est calculée en dur** (`#666666`/`#999999` selon `dark_mode`) plutôt que via `theme['disabled']` — incohérent avec le reste du projet si une refonte visuelle de ce dialogue est un jour demandée.
