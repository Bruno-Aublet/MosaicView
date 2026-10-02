---
name: image-format-conversion
description: Localiser ou modifier la conversion de format des images sélectionnées de la mosaïque (PNG/JPEG/WEBP/AVIF/BMP/TIFF/GIF statique ou animé). Utiliser dès qu'une tâche touche à conversion_dialogs_qt.py, convert_selected_images, ou au menu "Convertir" sur une sélection.
---

# Conversion de format d'images — MosaicView

Convertit une ou plusieurs images **sélectionnées** dans la mosaïque vers un autre format (PNG, JPEG, WEBP, AVIF, BMP, TIFF, GIF statique ou animé). Les images converties sont **ajoutées** à côté des originales (pas de remplacement en place) — l'utilisateur choisit ensuite de garder les deux, supprimer les originaux, ou annuler toute la conversion.

Distinct de `batch-img-convert` (qui convertit des **fichiers image isolés en CBZ**, un tout autre flux) — ici on convertit le **format d'encodage** de pages déjà présentes dans une mosaïque ouverte. Distinct aussi de `adjust-compression`/`adjust-image-mode` (qui changent la qualité/le mode d'une image existante **en place**, via le panneau Ajustements) : cette fonction crée toujours une **nouvelle entrée** séparée.

## Les 3 fonctions publiques — `modules/qt/conversion_dialogs_qt.py`

- **`convert_selected_images(parent, callbacks)`** — point d'entrée. Vérifie la sélection (vide → `MsgDialog` "aucune sélection" ; sélection sans image valide → `MsgDialog` "sélection invalide"), puis ouvre `_ConvertFormatDialog`.
- **`show_quality_dialog(parent, target_format, selected_entries, callbacks)`** — ouvre `_QualityDialog`, appelée uniquement si le format cible est JPEG/WEBP/AVIF (formats à qualité réglable).
- **`show_conversion_complete_dialog(parent, converted, target_format, selected_entries, converted_entries, callbacks, on_done)`** — ouvre `_ConversionCompleteDialog` après une conversion réussie ; **exécute elle-même** la suppression des entrées (originales ou converties) selon le choix de l'utilisateur, avant d'appeler `on_done(action)`.

## Flux complet

1. **`_ConvertFormatDialog`** — 8 radios de format (`PNG`, `JPEG`, `WEBP`, `AVIF`, `BMP`, `TIFF`, `GIF_STATIC`, `GIF_ANIMATED`). Le radio `GIF_ANIMATED` est **désactivé** si une seule image est sélectionnée (`rb.setEnabled(False)` si `len(selected_entries) == 1`) — un GIF animé nécessite plusieurs frames sources.
   - PNG/BMP/TIFF/GIF_STATIC → conversion directe (`callbacks['perform_conversion'](fmt, 95, entries)`, qualité 95 fixe, sans signification réelle pour ces formats).
   - JPEG/WEBP/AVIF → ouvre `_QualityDialog` (qualité réglable).
   - GIF_ANIMATED → délègue entièrement à `callbacks['show_animated_gif_dialog'](selected_entries)` — **sort du flux de ce fichier**, voir skill `animated-gif`.
2. **`_QualityDialog`** — 4 presets radio (`quality_maximum`=95, `quality_high`=85, `quality_medium`=75, `quality_low`=60, voir `PRESETS`) + un `FocusSlider` 1-100 synchronisé bidirectionnellement avec les radios (choisir un preset déplace le slider ; déplacer le slider sur une valeur hors-preset désélectionne tous les radios et bascule en "qualité personnalisée"). Affiche le nombre de fichiers et leur poids total (`format_file_size`). Au clic "Convertir" → `callbacks['perform_conversion'](target_format, quality, entries)`.
3. **`perform_conversion(parent, target_format, quality, selected_entries, callbacks)`** (fonction module-level, appelée par les callbacks des deux dialogues ci-dessus) :
   - `state.converting = True`, `insert_after_idx = max(state.selected_indices)` (les nouvelles entrées s'insèrent juste après la sélection, pas à la fin de la mosaïque).
   - `callbacks['save_state']()` **avant** de lancer le worker (undo, un seul snapshot pour tout le lot — voir skill `apply-image-operation`).
   - Lance `_ConversionWorker` (QThread) avec overlay de progression + bouton Annuler (`show_canvas_text`/`_show_cancel_item`, voir skill `canvas-overlay-progress`).
4. **`_ConversionWorker.run()`** — pour chaque entrée sélectionnée : `convert_image_data(entry, target_format, quality)` (voir ci-dessous), puis si succès `build_qimage_for_entry` + `free_image_memory` + émission de `entry_ready(new_entry)`. **Le worker ne touche jamais à `state.images_data`.**
5. **Insertion côté thread UI** (`on_entry_ready` dans `perform_conversion`) : ignore l'entrée si la conversion a été annulée (`worker_ref[0] is not worker`) ou si le document a changé (`state.doc_generation`, voir skill `file-close`) ; sinon l'insère **à un index qui s'incrémente à chaque insertion** (`insert_pos[0] += 1`, parti de `insert_after_idx`) — les nouvelles entrées se retrouvent donc dans le même ordre que la sélection d'origine, juste après elle — et l'ajoute à `inserted_entries`, liste locale à `perform_conversion` qui permet à l'annulation de retrouver exactement ce qui a déjà été inséré.
6. **Fin normale** (`on_finished`) : ne fait rien si la conversion a été annulée (annulation pendant la dernière page : `finished` est émis au lieu de `cancelled`, `_cancel()` a déjà tout nettoyé) ; si le document a changé, remet seulement `state.converting` à `False`. Sinon, si `converted == 0`, pas de dialogue, juste un rafraîchissement. Sinon, `show_conversion_complete_dialog` avec 3 choix :
   - **Supprimer les originaux** (`delete_orig`) — retire `selected_entries` de `state.images_data` par `id()`.
   - **Annuler la conversion** (`delete_conv`) — retire les entrées **converties** (`converted_entries`/`inserted_entries`) par `id()`, comme si la conversion n'avait jamais eu lieu.
   - **Garder les deux** (`None`) — ne retire rien.
   
   Dans les 3 cas : `sync_pages_in_xml_data(state)` (voir skill `comicinfo-metadata-editor`, le nombre de pages a changé) puis un **second** `callbacks['save_state']()` (redo) — le pattern est donc save_state avant le lot + save_state après la résolution du dialogue de fin, pas juste après le worker.

## `convert_image_data()` — `modules/qt/image_ops.py`

Fonction métier pure (pas de dépendance Qt) : `ensure_image_loaded(entry)` puis conversion PIL. Retourne `(new_entry_dict, None)` en succès ou `(None, error_msg)` en échec (jamais d'exception qui remonte — chaque échec individuel dans le worker est silencieusement compté comme non converti).

- Préserve le DPI source (`entry.get("dpi")` ou `img.info.get("dpi")`, normalisé en tuple `(x, y)`).
- Nouveau nom de fichier = ancien nom sans extension + extension du format cible (`ext_map`, ex. `"JPEG" → ".jpg"`, `"GIF" → ".gif"` — noter que `GIF_STATIC` est traduit en `"GIF"` **avant** d'appeler `perform_conversion`, dans `_ConvertFormatDialog._on_convert`, pas dans `convert_image_data` elle-même).
- Travaille sur `img.copy()` — l'image source (`entry["img"]`/`entry["bytes"]`) n'est jamais touchée, cohérent avec le principe "nouvelle entrée, pas de remplacement en place".
- Conversion de mode gérée en interne avant l'encodage : CMYK/YCbCr/I/F → RGB ; LA/PA → RGBA (ni BMP ni GIF ne savent les écrire). Par format cible :
  - **JPEG** : alpha (RGBA/LA/P) aplati sur fond blanc.
  - **BMP** : RGBA ou P avec transparence aplatis sur fond blanc, comme JPEG — l'alpha d'un BMP 32 bits n'est pas relu (voir skill `adjust-color-depth`), et les couleurs cachées sous les zones transparentes réapparaîtraient sinon.
  - **GIF** : RGBA enregistré tel quel (Pillow le quantifie en palette en gardant la transparence, que `convert("P", ADAPTIVE)` perdrait) ; P et L tels quels ; tout autre mode quantifié en palette adaptative 256 couleurs.
  - **WebP/AVIF** : `quality` choisie ; **TIFF** : DPI conservé ; **PNG** : tel quel.
- **Image animée** (`is_animated_gif` ou `is_animated_image`, voir skill `viewers`) vers WebP/PNG/AVIF (`_ANIMATABLE_FORMATS`) : toutes les frames sont converties (`ImageSequence.Iterator`, RGBA), durées et boucle conservées (`save_all`) ; la nouvelle entrée porte `is_animated_image=True`, `gif_frame_count`, `gif_durations` pour la lecture animée dans la visionneuse. **Piège** : lire `frame.info["duration"]` APRÈS `frame.copy()` — WebP/AVIF ne renseignent la durée qu'une fois la frame décodée. Vers JPEG/BMP/TIFF : première frame seulement (format sans animation) ; `GIF_STATIC` demande explicitement une image fixe.

## Points d'entrée UI

Trois, tous menant au callback `"convert_selected_images"` de `build_menubar_callbacks` (`menubar_callbacks_qt.py`), qui appelle `convert_selected_images(mw, mw._conversion_callbacks())` (`mw` = le `PanelWidget` propriétaire) :
1. **Menu contextuel** (clic droit sur une vignette, `show_image_context_menu`, `context_menus_qt.py`) — `context_menu.image.convert`.
2. **Barre de menu**, menu Images (`_populate_images_menu`, `menubar_qt.py`) — même clé `context_menu.image.convert`.
3. **Colonne d'icônes** (bouton `"convert"` de `ICON_DEFINITIONS`, `icon_toolbar_qt.py`, actif si `has_selected_images`).

`callbacks['perform_conversion']` est câblé séparément dans `PanelWidget._conversion_callbacks()` (`panel_widget.py`), avec la signature `lambda fmt, quality, entries: _perform_conversion(self, fmt, quality, entries, self._conversion_callbacks())` — noter l'auto-référence (`_conversion_callbacks()` est rappelée à l'intérieur d'elle-même), qui fonctionne parce que le dict de callbacks est reconstruit à chaque appel plutôt que mémoïsé.

## Comment modifier

- **Ajouter un nouveau format cible** : ajouter une entrée à `_ConvertFormatDialog._FORMATS` (clé de traduction + valeur), à `ext_map` dans `convert_image_data` (`image_ops.py`), et si le format a une qualité réglable, l'ajouter à la condition `if target_format in ("JPEG", "WEBP", "AVIF")` dans `_on_convert`.
- **Changer les presets de qualité** : `_QualityDialog.PRESETS` (liste de 4 valeurs) + `preset_labels` (clés de traduction associées, dans le même ordre) — les deux listes sont zippées, donc garder la même longueur et le même ordre.
- **Changer où les entrées converties s'insèrent** : `insert_after_idx` dans `perform_conversion` — actuellement après le max des indices sélectionnés, pas après chaque entrée individuellement (donc un lot de 5 images non contiguës convergent toutes juste après la dernière sélectionnée, pas dispersées à côté de chacune).
- **Changer le comportement "Annuler la conversion"** : `show_conversion_complete_dialog._handle_action`, branche `delete_conv` — filtre par `id()` sur `converted_entries`, pas sur une plage d'indices (robuste même si la mosaïque a été retriée entre-temps).

## Pièges connus

- **`GIF_STATIC` devient `"GIF"` avant d'atteindre `convert_image_data`** — ne pas chercher `"GIF_STATIC"` dans `ext_map`, il n'y est pas ; la traduction se fait dans `_ConvertFormatDialog._on_convert` (`fmt = "GIF" if target_format == "GIF_STATIC" else target_format`).
- **`GIF_ANIMATED` ne passe jamais par `perform_conversion`/`_ConversionWorker`** — délégation complète et immédiate à `callbacks['show_animated_gif_dialog']`, donc aucune des étapes décrites ici (overlay, worker, dialogue de fin) ne s'applique à ce choix. Une modification du flux de conversion normal ne touche jamais le chemin GIF animé, et vice-versa.
- **Annulation en cours de lot** — `_cancel()` dans `perform_conversion` retire de `state.images_data` (par `id()`) les entrées de `inserted_entries`, puis `worker_ref[0] = None` fait ignorer par `on_entry_ready` toute page convertie qui arriverait encore. **Ne jamais remettre l'insertion dans le worker** : une page dont la conversion se termine juste après le clic sur Annuler survivrait alors au nettoyage de `_cancel()` (qui ne peut retirer que ce qui est déjà inséré), et la liste serait modifiée depuis un autre thread pendant que l'interface la parcourt.
- **Fermeture du fichier pendant la conversion** : `perform_conversion` inscrit `_cancel_on_close` (pas `_cancel`) par `register_cancel_on_close` au lancement ; `_cleanup` le retire. Cette annulation allégée arrête le worker, masque l'overlay et remet `state.converting` à `False`, sans retirer les pages déjà insérées, resynchroniser le XML ni redessiner : ce serait inutile sur un document que `force_close_file` vide juste après. Les pages encore en route sont ignorées par `on_entry_ready` (`worker_ref[0]` n'est plus ce worker). Voir skill `file-close`, « Arrêt des opérations à la fermeture ».
- **Libération du worker** : `dispose_qthread` (`utils.py`, `wait()` puis `deleteLater()`) dans le slot de son signal de fin — jamais `deleteLater()` seul.
- **`convert_image_data` ne lève jamais d'exception visible** — un format d'image corrompu ou une conversion PIL impossible renvoie `(None, error_msg)`, silencieusement compté comme non converti par le worker (`if new_entry:` seulement) ; `error_msg` n'est actuellement **pas affiché** à l'utilisateur nulle part dans ce fichier (ni logué) — à vérifier avant de supposer qu'un échec de conversion individuel est visible quelque part.
- **Le second `save_state()` a lieu après la résolution du dialogue de fin, pas juste après le worker** — dans les 3 branches (`delete_orig`/`delete_conv`/`None`), pas seulement en cas de succès total ; si `converted == 0`, en revanche, `save_state()` est appelé directement dans `on_finished` (pas de dialogue de fin affiché du tout).

## Références croisées

- `apply-image-operation` — pattern général save_state/undo pour une fonction qui insère de nouvelles entrées dans `images_data`.
- `animated-gif` — flux complet du choix `GIF_ANIMATED`, entièrement externe à ce fichier. La même fenêtre est aussi accessible directement par "Créer un GIF animé" (menu Images et menu contextuel) et par l'icône `animated_gif` de la colonne d'icônes, avec la même règle "au moins 2 images" dupliquée — modifier les quatre ensemble.
- `canvas-overlay-progress` — overlay `labels.converting` + bouton Annuler pendant le worker.
- `comicinfo-metadata-editor` — `sync_pages_in_xml_data`, appelée après résolution du dialogue de fin (le nombre de pages a changé).
- `batch-img-convert` — flux voisin par le nom mais totalement différent : convertit des fichiers isolés en CBZ, pas le format de pages déjà chargées.
- `adjust-compression` / `adjust-image-mode` — modifient qualité/mode **en place** sur une image existante (panneau Ajustements), alors que ce skill crée toujours une entrée séparée.
- `qt-context-menus` / `icon-toolbar` — les 2 points d'entrée UI de `convert_selected_images`.
