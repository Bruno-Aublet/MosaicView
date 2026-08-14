---
name: adjust-remove-colors
description: Localiser ou modifier la fonction "Suppression des couleurs" (effet manga/BD noir et blanc contrasté, distinct du niveau de gris simple), migrée dans la barre d'outils de la visionneuse principale (remove_colors_tool_qt.py). Utiliser dès qu'une tâche touche à settings['remove_colors_intensity'] ou à la réglette "Suppression des couleurs"/"Intensité".
---

# Ajustement "Suppression des couleurs" — MosaicView

Outil de la barre d'outils flottante de la visionneuse principale (9e outil migré, 5e des 8 modes d'ajustement, v1.7.4, 2026-08-14) — voir skill `viewers`, section "Le cas de la suppression des couleurs", pour l'intégration dans la barre (panneau flottant, preview live, commit, undo/redo). Ce skill-ci ne couvre que la formule PIL elle-même, inchangée depuis la migration.

**Ancien emplacement retiré** : la section "Suppression des couleurs" du panneau Ajustements classique et le mode `'remove_colors'` de l'ancienne visionneuse annexe `AdjustmentViewerDialog` ont été entièrement supprimés (2026-08-14) une fois la migration validée — plus aucune trace dans `adjustments_dialog_qt.py` ni `adjustments_viewers_qt.py`. Les clés de traduction `dialogs.adjustments.effect_remove_colors`/`remove_colors_intensity_label`, devenues orphelines, ont été retirées des 46 langues.

## Où

- UI : `modules/qt/remove_colors_tool_qt.py` — `_RemoveColorsOptionsPanel` (panneau flottant, réglette 0-100 + spinbox), `RemoveColorsCanvasMixin`/`RemoveColorsViewerMixin` (mixins hérités par `_ViewerCanvas`/`ImageViewer`, voir skill `viewers`)
- Handler : `RemoveColorsViewerMixin.perform_remove_colors()` — commit réel au relâchement du slider/perte de focus de la spinbox, réutilise `apply_image_adjustments()`
- Traitement : `adjustments_processing_qt.py::apply_adjustments()`, bloc `# ── Suppression des couleurs ──` (seul moteur de calcul, partagé, inchangé par la migration)
- Comportement du slider après commit : reste sur la valeur appliquée (ne revient PAS à 0), même principe que brightness/saturation — voir skill `viewers`.

## Ce que fait réellement ce réglage

**Ce n'est pas un simple niveau de gris** (contrairement à ce que le nom pourrait suggérer, à ne pas confondre avec l'effet "Noir et blanc" de la section Effets, skill `adjust-effects`) — c'est un traitement en 4 étapes conçu pour produire un rendu contrasté façon page de manga/BD à partir d'une image en couleur, en amplifiant le contraste des tons moyens plutôt qu'en aplatissant simplement la saturation :

```python
intensity = remove_int   # 0..100
gm   = 0.5  - (intensity / 100.0) * 0.25   # 0.50 → 0.25
ct   = 1.5  + (intensity / 100.0) * 1.5    # 1.50 → 3.00
thr  = int(150 - (intensity / 100.0) * 50) # 150 → 100
mult = 2.0  + (intensity / 100.0) * 2.5    # 2.00 → 4.50
bst  = 1.2  + (intensity / 100.0) * 0.4    # 1.20 → 1.60

img  = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=1)
arr  = np.power(np.array(img, dtype=np.float32) / 255.0, gm) * 255
img  = ImageEnhance.Contrast(Image.fromarray(arr.astype(np.uint8))).enhance(ct)
arr  = np.array(img, dtype=np.float32)
arr  = np.where(arr > thr, thr + (arr - thr) * mult, arr)
arr  = np.where((arr > 80) & (arr < 200), arr * bst, arr)
img  = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert('RGB')
```

Étapes, dans l'ordre : (1) niveaux de gris + autocontraste (`cutoff=1`, ignore 1% des valeurs extrêmes de chaque côté de l'histogramme) ; (2) correction gamma via `numpy` (assombrit les tons moyens, `gm` diminue avec l'intensité → effet plus marqué) ; (3) renforcement de contraste PIL classique (`ct` croît avec l'intensité) ; (4) deux passes `numpy.where` : la première pousse fortement les hautes lumières au-delà du seuil `thr` (facteur `mult`, jusqu'à ×4.5) pour un effet "blanc qui claque", la seconde amplifie légèrement (`bst`) la plage `80-200` (tons moyens) pour préserver un peu de dégradé au lieu d'un pur noir/blanc.

**Les 5 paramètres internes (`gm`, `ct`, `thr`, `mult`, `bst`) sont tous dérivés linéairement d'un seul curseur `intensity` (0-100)** — il n'existe aucun contrôle indépendant pour l'un de ces 5 paramètres dans l'UI actuelle. Si une demande porte sur un réglage plus fin (ex. contrôler `thr` séparément de `mult`), c'est un changement de design (ajout de nouveaux contrôles UI), pas une simple modification de formule — à clarifier avec l'utilisateur avant de l'entreprendre, cf. règle CLAUDE.md sur le périmètre explicite des demandes.

Contrairement à la plupart des autres réglages, il n'y a pas de "formule simple" à citer isolément — les 5 constantes de base (`0.5`, `0.25`, `1.5`, `150`, `50`, `2.0`, `2.5`, `1.2`, `0.4`, les bornes `80`/`200`) sont toutes calibrées empiriquement ensemble ; ne pas modifier une seule sans retester visuellement l'ensemble de la plage 0-100.

## Modifier cette fonction

Le bloc entier est autonome dans `apply_adjustments()` (`if remove_int > 0:`) — toute modification de formule doit rester dans ce bloc. Si le curseur doit changer d'échelle ou de bornes, mettre à jour `remove_colors_tool_qt.py::_RemoveColorsOptionsPanel` (`_RANGE_MIN`/`_RANGE_MAX`), seule définition UI restante désormais que le panneau classique et le viewer annexe ont été retirés.

## Références croisées

- `viewers` — section "Le cas de la suppression des couleurs" : intégration dans la barre d'outils de la visionneuse principale (panneau flottant, preview live, commit automatique, undo/redo, comportement du slider après commit).
- `apply-image-operation` — pattern suivi par `perform_remove_colors()` pour committer dans `entry['bytes']`.
- `adjust-effects` — l'effet "Noir et blanc" (`ImageOps.grayscale` simple) et "Sépia", des transformations bien plus simples à ne pas confondre avec ce réglage.
