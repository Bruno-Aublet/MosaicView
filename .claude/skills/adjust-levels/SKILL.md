---
name: adjust-levels
description: Localiser ou modifier la fonction "Niveaux noir/blanc" (seuil, point noir, gamma, point blanc, auto) du panneau Ajustements d'image, et le mode pipette de la visionneuse dédiée. Utiliser dès qu'une tâche touche à settings['threshold']/'black_point'/'gamma'/'white_point' ou compute_auto_levels().
---

# Ajustement "Niveaux noir/blanc" — MosaicView

Section réglette du panneau Ajustements d'image (colonne droite, 2e section) — la plus riche des sections avec 4 réglages internes + un bouton d'automatisation + une visionneuse dédiée à comportement propre (pipettes). Pour l'orchestration générale du panneau, voir skill `adjustments-panel`.

## Où

- UI panneau : `adjustments_dialog_qt.py::_build_right_column()`, groupe `self._grp_levels` — 4 sliders (`_threshold_slider` 0-255 défaut 128, `_black_pt_slider` 0-255 défaut 0, `_gamma_slider` 10-300 ×0.01 défaut 100=1.0, `_white_pt_slider` 0-255 défaut 255) + bouton `_btn_auto_levels`
- Traitement : `adjustments_processing_qt.py::apply_adjustments()`, blocs `# ── Seuil ──` et `# ── Niveaux avancés ──`
- Calcul auto : `adjustments_processing_qt.py::compute_auto_levels(image_bytes)`
- Visionneuse dédiée : mode `'levels'` — **seul mode avec des pipettes cliquables sur l'image** plutôt que des réglettes dans la toolbar

## Seuil (`threshold`) — indépendant des 3 autres

```python
if threshold != 128:
    img = img.convert('L').point(lambda p: 255 if p > threshold else 0).convert('RGB')
```
Binarisation noir/blanc pur à un seuil fixe. `128` = valeur neutre = pas de binarisation. **Ce réglage n'a pas de bouton "Ajuster avec la visionneuse" dédié dans ce panneau** ni de pipette — seule une réglette dans le panneau principal. Ne pas confondre avec le mode `'1'` de Profondeur de couleur (skill `adjust-color-depth`) qui binarise aussi mais à un seuil fixe à 128, non réglable.

## Point noir / Gamma / Point blanc — LUT combinée

```python
if black_pt != 0 or white_pt != 255 or gamma != 1.0:
    img = img.convert('RGB')
    lut = []
    for i in range(256):
        n = (i - black_pt) / (white_pt - black_pt) if white_pt != black_pt else 0
        lut.append(int(pow(max(0, min(1, n)), 1.0 / gamma) * 255))
    r, g, b = img.split()
    img = Image.merge('RGB', (r.point(lut), g.point(lut), b.point(lut)))
```
Une seule LUT 256 valeurs (`point()`) appliquée identiquement aux 3 canaux R/G/B — remapping linéaire `[black_pt, white_pt] → [0, 1]` (clampé), puis correction gamma (`pow(n, 1/gamma)`), puis remise à l'échelle `[0, 255]`. Les 3 valeurs sont combinées en **une seule passe**, pas 3 passes successives — modifier l'une sans les autres ne recalcule que cette LUT unique, il n'y a pas d'ordre d'application entre point noir/gamma/point blanc à préserver puisqu'ils sont mathématiquement fusionnés.

`gamma` slider : entier `10..300` divisé par 100 (`round(val / 100.0, 2)`) → flottant `0.10..3.00`, défaut `100 = 1.0`.

## Ajustement automatique (`compute_auto_levels`)

```python
def compute_auto_levels(image_bytes):
    # percentile 1% → black_val, percentile 99% → white_val (sur la moyenne RGB par pixel)
```
Calcule les valeurs de point noir/blanc via les percentiles 1%/99% de la luminance moyenne (`arr.mean(axis=1)` sur les 3 canaux, triés, indexés à `1%`/`99%` de la population de pixels). Clampé à `black ∈ [0, 254]`, `white ∈ [black+1, 255]` (garantit toujours `white > black`, évite une division par zéro dans la LUT). Retourne `(0, 255)` (= no-op) en cas d'exception.

**Deux points d'entrée distincts, mêmes calculs, comportements différents autour** :
1. **Bouton "Ajustement automatique" du panneau** (`AdjustmentsDialog._on_auto_levels`) : calcule sur `self._original_preview_img` uniquement (première image de la sélection). Sauvegarde `_pre_auto_black_point`/`_pre_auto_white_point` avant d'écraser les sliders — permet à `reject()` (Annuler) de restaurer les valeurs précédentes si l'utilisateur clique Auto puis Annuler. **En application multi-image**, si ce bouton panneau a été cliqué, `_on_apply` recalcule `compute_auto_levels` **individuellement pour chaque image du lot** (pas les valeurs figées de l'aperçu) — voir skill `adjustments-panel` section "Cas spécial Ajustement automatique + multi-image".
2. **Bouton "Ajustement auto" du viewer** (`AdjustmentViewerDialog._on_auto_levels`) : calcule sur la page courante affichée dans le viewer, pousse un état dans l'historique undo local du viewer (`_levels_push_history()`) avant d'écraser.

## Mode `'levels'` de la visionneuse — pipettes, pas de réglettes

Seul mode de `AdjustmentViewerDialog` sans réglette dans la toolbar : la barre du bas propose deux boutons-pipette bascule (`_black_pip_btn`/`_white_pip_btn`, style `_pip_btn_style` avec état `:checked` distinct), un curseur custom par pipette (icônes `pipette_noire.png`/`pipette_blanche.png`), et un clic sur l'image (`_on_image_click` → conversion coordonnées écran→image en tenant compte du zoom/pan, puis `img.getpixel()`) fixe `black_point` ou `white_point` à la **luminance du pixel cliqué** (`int(sum(pixel[:3]) / 3)` si RGB, ou la valeur brute si niveaux de gris).

**Undo/redo local au viewer, par page** : `_levels_history`/`_levels_redo_stack` (pile de tuples `(black_pt, white_pt, gamma)`) — poussé avant chaque clic pipette ou clic "Auto". Raccourcis `Ctrl+Z`/`Ctrl+Y` câblés uniquement en mode `'levels'` (et `'transparency'`, avec des piles séparées). **État sauvegardé par page** lors de la navigation ◀/▶ entre images (`_save_levels_state`/`_restore_levels_state`, dicts indexés par `_current_idx`) — si l'utilisateur règle les niveaux de la page 1 puis passe à la page 2, la page 1 garde ses réglages et son historique en mémoire pendant toute la session du viewer.

**Bouton "Appliquer" propre à ce mode** (`_apply_levels_all`, pas `_apply_to_current`/`_apply_to_all` génériques des autres modes) : applique à **chaque page qui a des valeurs de niveaux non neutres** (`bp != 0 or wp != 255 or g != 1.0`), en utilisant les valeurs sauvegardées par page (`_levels_values`) — une page jamais visitée ou laissée aux valeurs par défaut n'est **pas** retouchée. Contrairement aux autres modes, il n'y a pas de bouton "Appliquer à cette image" séparé d'un "Appliquer à toutes" — un seul bouton qui gère intelligemment le multi-page.

## Modifier cette fonction

- Formule seuil/LUT → blocs correspondants de `apply_adjustments()`.
- Percentiles de l'auto-niveaux → `compute_auto_levels()` (actuellement 1%/99%, codés en dur `int(len(arr) * 0.01)`).
- Comportement des pipettes/undo local → `adjustments_viewers_qt.py`, méthodes préfixées `_levels_`.

## Références croisées

- `adjustments-panel` — flux Appliquer multi-image, cas spécial auto-niveaux.
- `viewers` — vue d'ensemble des 8 modes de `AdjustmentViewerDialog`.
- `adjust-brightness-contrast` — objectif visuel proche (assombrir/éclaircir) mais formule `ImageEnhance` distincte, appliquée plus tôt dans le pipeline.
- `adjust-color-depth` — mode `'1'` (binarisation à seuil fixe 128), à ne pas confondre avec le seuil réglable de cette section.
