---
name: adjust-levels
description: Localiser ou modifier la logique PIL de la fonction "Niveaux noir/blanc" (seuil, point noir, gamma, point blanc, auto) — moteur de calcul dans image_processing_qt.py, consommé par l'outil "levels" de la barre d'outils de la visionneuse principale. Utiliser dès qu'une tâche touche à settings['threshold']/'black_point'/'gamma'/'white_point'/compute_auto_levels(), ou à la LUT/formule de binarisation elles-mêmes.
---

# Ajustement "Niveaux noir/blanc" — MosaicView

Outil de la barre d'outils de la visionneuse principale. Seul point d'entrée : l'icône "Niveaux" (`BTN_Levels.png`) de la barre d'outils flottante de `ImageViewer`, voir skill `viewers` section "Le cas des niveaux" pour l'intégration complète (panneau à 2 lignes/7 contrôles, pipettes, curseur custom, undo/redo unifié). **Ce skill-ci ne couvre que la formule PIL de traitement**, dans `image_processing_qt.py` — c'est le seul moteur de calcul, partagé, dont dépend l'outil.

## Où

- Traitement : `image_processing_qt.py::apply_adjustments()`, blocs `# ── Seuil ──` et `# ── Niveaux avancés ──`
- Calcul auto : `image_processing_qt.py::compute_auto_levels(image_bytes)`
- Intégration barre d'outils (UI, pipettes, panneau flottant, undo/redo) : `modules/qt/levels_tool_qt.py` — voir skill `viewers`

## Seuil (`threshold`) — indépendant des 3 autres

```python
if threshold != 128:
    img = img.convert('L').point(lambda p: 255 if p > threshold else 0).convert('RGB')
```
Binarisation noir/blanc pur à un seuil fixe. `128` = valeur neutre = pas de binarisation. Ne pas confondre avec le mode `'1'` de Profondeur de couleur (skill `adjust-color-depth`) qui binarise aussi mais à un seuil fixe à 128, non réglable.

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

`gamma` : flottant `0.10..3.00`, défaut `1.0` — dans `levels_tool_qt.py::_LevelsOptionsPanel`, le slider est un entier `10..300` divisé par 100 (`round(val / 100.0, 2)`), même mapping que l'ancien panneau classique.

## Ajustement automatique (`compute_auto_levels`)

```python
def compute_auto_levels(image_bytes):
    # percentile 1% → black_val, percentile 99% → white_val (sur la moyenne RGB par pixel)
```
Calcule les valeurs de point noir/blanc via les percentiles 1%/99% de la luminance moyenne (`arr.mean(axis=1)` sur les 3 canaux, triés, indexés à `1%`/`99%` de la population de pixels). Clampé à `black ∈ [0, 254]`, `white ∈ [black+1, 255]` (garantit toujours `white > black`, évite une division par zéro dans la LUT). Retourne `(0, 255)` (= no-op) en cas d'exception.

**Point d'entrée unique** : `LevelsViewerMixin.perform_auto_levels()` (`levels_tool_qt.py`), appelé par le bouton "Auto" du panneau flottant — calcule sur la page réellement affichée dans la visionneuse (`entry['bytes']` courant, pas un aperçu figé), met à jour les 2 sliders point noir/point blanc, puis commit immédiatement (même geste qu'un clic pipette, pas de relâchement à attendre).

## Pipettes — clic sur l'image dans la visionneuse principale

Comportement de référence identique à l'ancien mode `'levels'` (repris tel quel) : cliquer sur l'image avec une pipette armée fixe `black_point` ou `white_point` à la **luminance du pixel cliqué** (`int(sum(pixel[:3]) / 3)` si RGB, valeur brute si niveaux de gris). Implémentation actuelle : `LevelsCanvasMixin.levels_pipette_click()` (`levels_tool_qt.py`), appelée depuis `_ViewerCanvas.mousePressEvent` — conversion écran→image via `display_offset_x/y`/`display_width/height` (même calcul que le crop, pas l'ancien calcul zoom/offset du viewer annexe). Gamma n'a pas de pipette (aucun pixel ne "possède" une valeur de gamma) — voir skill `viewers` pour la justification complète et le détail du curseur custom (icône + croix de visée évidée + hotspot).

## Modifier cette fonction

- Formule seuil/LUT → blocs correspondants de `apply_adjustments()` (`image_processing_qt.py`).
- Percentiles de l'auto-niveaux → `compute_auto_levels()` (actuellement 1%/99%, codés en dur `int(len(arr) * 0.01)`).
- Comportement des pipettes/panneau flottant/undo — `levels_tool_qt.py`, voir skill `viewers` section "Le cas des niveaux" pour le détail complet (undo/redo unifié avec l'historique du panneau).

## Références croisées

- `viewers` — section "Le cas des niveaux" : intégration complète dans la barre d'outils (panneau 2 lignes/7 contrôles, pipettes, curseur custom, undo/redo unifié — voir pièges connus : hotspot du curseur, croix de visée pleine masquant le pixel visé, reset du pan après commit, police CSUR manquante sur 3 boutons).
- `adjust-brightness-contrast` — objectif visuel proche (assombrir/éclaircir) mais formule `ImageEnhance` distincte, appliquée plus tôt dans le pipeline.
- `adjust-color-depth` — mode `'1'` (binarisation à seuil fixe 128), à ne pas confondre avec le seuil réglable de cette section.
