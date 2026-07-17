---
name: adjust-remove-colors
description: Localiser ou modifier la fonction "Suppression des couleurs" du panneau Ajustements d'image (effet manga/BD noir et blanc contrasté, distinct du niveau de gris simple). Utiliser dès qu'une tâche touche à settings['remove_colors_intensity'] ou à la réglette "Suppression des couleurs".
---

# Ajustement "Suppression des couleurs" — MosaicView

Section réglette du panneau Ajustements d'image (colonne aperçu, 2e section). Pour l'orchestration générale du panneau, voir skill `adjustments-panel`.

## Où

- UI : `adjustments_dialog_qt.py::_build_preview_column()`, groupe `self._grp_remove_colors` / `self._remove_colors_slider` (range 0-100, défaut 0)
- Handler : `_on_remove_colors_changed(val)` → `self._remove_int = val` → `_update_preview()`
- Traitement : `adjustments_processing_qt.py::apply_adjustments()`, bloc `# ── Suppression des couleurs ──`
- Visionneuse dédiée : mode `'remove_colors'`

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

Le bloc entier est autonome dans `apply_adjustments()` (`if remove_int > 0:`) — toute modification de formule doit rester dans ce bloc. Si le curseur doit changer d'échelle ou de bornes, mettre à jour identiquement `adjustments_dialog_qt.py::_remove_colors_slider` et `adjustments_viewers_qt.py::_remove_slider` (mode `'remove_colors'`) — deux définitions indépendantes.

## Références croisées

- `adjustments-panel` — structure générale, `_get_settings()`.
- `viewers` — mode `'remove_colors'` de `AdjustmentViewerDialog`.
- `adjust-effects` — l'effet "Noir et blanc" (`ImageOps.grayscale` simple) et "Sépia", des transformations bien plus simples à ne pas confondre avec ce réglage.
