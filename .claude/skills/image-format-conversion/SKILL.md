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
4. **`_ConversionWorker.run()`** — pour chaque entrée sélectionnée : `convert_image_data(entry, target_format, quality)` (voir ci-dessous), puis si succès `build_qimage_for_entry` + `free_image_memory` + insertion dans `state.images_data` **à un index qui s'incrémente à chaque insertion** (`insert_idx += 1` avant chaque `insert`) — les nouvelles entrées se retrouvent donc dans le même ordre que la sélection d'origine, juste après elle. `self._inserted_entries` est une **liste partagée** avec `perform_conversion` (passée par référence), pas un signal — permet l'annulation à mi-parcours de retrouver exactement ce qui a déjà été inséré.
5. **Fin normale** (`on_finished`) : si `converted == 0`, pas de dialogue, juste un rafraîchissement. Sinon, `show_conversion_complete_dialog` avec 3 choix :
   - **Supprimer les originaux** (`delete_orig`) — retire `selected_entries` de `state.images_data` par `id()`.
   - **Annuler la conversion** (`delete_conv`) — retire les entrées **converties** (`converted_entries`/`inserted_entries`) par `id()`, comme si la conversion n'avait jamais eu lieu.
   - **Garder les deux** (`None`) — ne retire rien.
   
   Dans les 3 cas : `sync_pages_in_xml_data(state)` (voir skill `comicinfo-metadata-editor`, le nombre de pages a changé) puis un **second** `callbacks['save_state']()` (redo) — le pattern est donc save_state avant le lot + save_state après la résolution du dialogue de fin, pas juste après le worker.

## `convert_image_data()` — `modules/qt/image_ops.py:72`

Fonction métier pure (pas de dépendance Qt) : `ensure_image_loaded(entry)` puis conversion PIL. Retourne `(new_entry_dict, None)` en succès ou `(None, error_msg)` en échec (jamais d'exception qui remonte — chaque échec individuel dans le worker est silencieusement compté comme non converti).

- Préserve le DPI source (`entry.get("dpi")` ou `img.info.get("dpi")`, normalisé en tuple `(x, y)`).
- Nouveau nom de fichier = ancien nom sans extension + extension du format cible (`ext_map`, ex. `"JPEG" → ".jpg"`, `"GIF" → ".gif"` — noter que `GIF_STATIC` est traduit en `"GIF"` **avant** d'appeler `perform_conversion`, dans `_ConvertFormatDialog._on_convert`, pas dans `convert_image_data` elle-même).
- Travaille sur `img.copy()` — l'image source (`entry["img"]`/`entry["bytes"]`) n'est jamais touchée, cohérent avec le principe "nouvelle entrée, pas de remplacement en place".
- Conversion de mode (CMYK/I/F → RGB, etc.) gérée en interne avant l'encodage — voir le fichier pour le détail exact des modes PIL selon le format cible.

## Points d'entrée UI

Deux, tous deux menant à `convert_selected_images(mw, mw._conversion_callbacks())` :
1. **Menu contextuel** (clic droit, `context_menus_qt.py:432`) — `context_menu.image.convert`.
2. **Colonne d'icônes** (`icon_toolbar_qt.py:2164`, callback `"convert"`) et **barre de menu** (`menubar_qt.py:210`, même clé `context_menu.image.convert`) — les deux pointent vers `menubar_callbacks_qt.py:97`.

`callbacks['perform_conversion']` est câblé séparément dans `panel_widget.py:1480` (`MainWindow._conversion_callbacks()`), avec la signature `lambda fmt, quality, entries: _perform_conversion(self, fmt, quality, entries, self._conversion_callbacks())` — noter l'auto-référence (`_conversion_callbacks()` est rappelée à l'intérieur d'elle-même), qui fonctionne parce que le dict de callbacks est reconstruit à chaque appel plutôt que mémoïsé.

## Comment modifier

- **Ajouter un nouveau format cible** : ajouter une entrée à `_ConvertFormatDialog._FORMATS` (clé de traduction + valeur), à `ext_map` dans `convert_image_data` (`image_ops.py`), et si le format a une qualité réglable, l'ajouter à la condition `if target_format in ("JPEG", "WEBP", "AVIF")` dans `_on_convert`.
- **Changer les presets de qualité** : `_QualityDialog.PRESETS` (liste de 4 valeurs) + `preset_labels` (clés de traduction associées, dans le même ordre) — les deux listes sont zippées, donc garder la même longueur et le même ordre.
- **Changer où les entrées converties s'insèrent** : `insert_after_idx` dans `perform_conversion` — actuellement après le max des indices sélectionnés, pas après chaque entrée individuellement (donc un lot de 5 images non contiguës convergent toutes juste après la dernière sélectionnée, pas dispersées à côté de chacune).
- **Changer le comportement "Annuler la conversion"** : `show_conversion_complete_dialog._handle_action`, branche `delete_conv` — filtre par `id()` sur `converted_entries`, pas sur une plage d'indices (robuste même si la mosaïque a été retriée entre-temps).

## Pièges connus

- **`GIF_STATIC` devient `"GIF"` avant d'atteindre `convert_image_data`** — ne pas chercher `"GIF_STATIC"` dans `ext_map`, il n'y est pas ; la traduction se fait dans `_ConvertFormatDialog._on_convert` (`fmt = "GIF" if target_format == "GIF_STATIC" else target_format`).
- **`GIF_ANIMATED` ne passe jamais par `perform_conversion`/`_ConversionWorker`** — délégation complète et immédiate à `callbacks['show_animated_gif_dialog']`, donc aucune des étapes décrites ici (overlay, worker, dialogue de fin) ne s'applique à ce choix. Une modification du flux de conversion normal ne touche jamais le chemin GIF animé, et vice-versa.
- **Annulation en cours de lot retire les entrées déjà insérées, une par une au fur et à mesure** — `_cancel()` dans `perform_conversion` filtre `state.images_data` par `id()` sur `inserted_entries` (liste partagée, mutée en direct par le worker) ; si le worker insère une entrée juste au moment de l'annulation, il y a une vérification du flag **après** la conversion individuelle (potentiellement longue) mais **avant** l'insertion — donc pas de race silencieuse, mais la temporalité exacte (conversion terminée mais pas encore insérée au moment du `cancel`) mérite d'être gardée en tête si un bug d'annulation est signalé.
- **`convert_image_data` ne lève jamais d'exception visible** — un format d'image corrompu ou une conversion PIL impossible renvoie `(None, error_msg)`, silencieusement compté comme non converti par le worker (`if new_entry:` seulement) ; `error_msg` n'est actuellement **pas affiché** à l'utilisateur nulle part dans ce fichier (ni logué) — à vérifier avant de supposer qu'un échec de conversion individuel est visible quelque part.
- **Le second `save_state()` a lieu après la résolution du dialogue de fin, pas juste après le worker** — dans les 3 branches (`delete_orig`/`delete_conv`/`None`), pas seulement en cas de succès total ; si `converted == 0`, en revanche, `save_state()` est appelé directement dans `on_finished` (pas de dialogue de fin affiché du tout).

## Références croisées

- `apply-image-operation` — pattern général save_state/undo pour une fonction qui insère de nouvelles entrées dans `images_data`.
- `animated-gif` — flux complet du choix `GIF_ANIMATED`, entièrement externe à ce fichier.
- `canvas-overlay-progress` — overlay `labels.converting` + bouton Annuler pendant le worker.
- `comicinfo-metadata-editor` — `sync_pages_in_xml_data`, appelée après résolution du dialogue de fin (le nombre de pages a changé).
- `batch-img-convert` — flux voisin par le nom mais totalement différent : convertit des fichiers isolés en CBZ, pas le format de pages déjà chargées.
- `adjust-compression` / `adjust-image-mode` — modifient qualité/mode **en place** sur une image existante (panneau Ajustements), alors que ce skill crée toujours une entrée séparée.
- `qt-context-menus` / `icon-toolbar` — les 2 points d'entrée UI de `convert_selected_images`.
