---
name: adjust-sharpness
description: Localiser ou modifier les fonctions "Netteté" et "Netteté adaptative" (Unsharp Mask) du panneau Ajustements d'image. Utiliser dès qu'une tâche touche à settings['sharpness'], settings['unsharp_radius'/'unsharp_percent'/'unsharp_threshold'], ou ImageFilter.UnsharpMask.
---

# Ajustements "Netteté" et "Netteté adaptative" — MosaicView

Deux sections distinctes mais adjacentes du panneau Ajustements d'image (colonne gauche, 3e et 4e sections) — regroupées dans ce skill car étroitement liées (toutes deux affectent la netteté perçue) mais **indépendantes dans le code** : deux groupes de réglages séparés dans `settings`, appliqués l'un après l'autre. Pour l'orchestration générale du panneau, voir skill `adjustments-panel`.

## Netteté simple (`sharpness`)

- UI : `self._grp_sharp` / `self._sharp_slider` (range -100..+100), handler `_on_sharp_changed`
- Traitement : `adjustments_processing_qt.py::apply_adjustments()`, bloc luminosité/contraste/netteté/saturation (début de fonction)
- Visionneuse dédiée : mode `'sharpness'`

```python
if sharpness != 0:
    if sharpness > 0:
        img = ImageEnhance.Sharpness(img).enhance(1.0 + sharpness / 100.0)
    else:
        img = img.filter(ImageFilter.GaussianBlur(abs(sharpness) / 20.0))
```
**Asymétrie voulue** : les valeurs positives et négatives empruntent deux mécanismes PIL complètement différents. Positif → `ImageEnhance.Sharpness` (renforcement de contraste local, va jusqu'à ×2.0 d'intensité à +100). Négatif → flou gaussien (`GaussianBlur`, rayon jusqu'à 5.0px à -100) — ce n'est **pas** un "moins de netteté" au sens `ImageEnhance.Sharpness(factor<1)`, c'est un vrai flou. Si le comportement doit devenir symétrique, c'est un changement de comportement visible à documenter, pas un simple ajustement de formule.

## Netteté adaptative / Unsharp Mask (3 réglettes)

- UI : `self._grp_unsharp`, 3 sliders indépendants :
  - `_unsharp_radius_slider` (range 5-50, ×0.1 → 0.5 à 5.0px, défaut 20 = 2.0)
  - `_unsharp_percent_slider` (range 0-200, %, défaut 0 = désactivé)
  - `_unsharp_threshold_slider` (range 0-30, défaut 3)
- Handlers : `_on_unsharp_radius_changed`, `_on_unsharp_percent_changed`, `_on_unsharp_threshold_changed`
- Traitement : bloc séparé juste après le bloc luminosité/contraste/netteté/saturation
- Visionneuse dédiée : mode `'unsharp'` (3 réglettes dans la toolbar, pas dans la barre du bas)

```python
if unsharp_percent > 0:
    img = img.filter(ImageFilter.UnsharpMask(
        radius=unsharp_radius, percent=unsharp_percent, threshold=unsharp_thresh))
```
Wrapper direct de `PIL.ImageFilter.UnsharpMask` — **c'est `unsharp_percent` qui active/désactive le filtre** (condition `> 0`), pas `unsharp_radius`/`unsharp_threshold` qui n'ont aucun effet seuls. Un slider radius/threshold modifié sans toucher percent ne produit donc visuellement aucun changement — comportement attendu de PIL, pas un bug.

**Différence fonctionnelle avec la netteté simple positive** : Unsharp Mask a un seuil (`threshold`) qui ignore les variations de contraste locales trop faibles (évite d'amplifier le bruit dans les zones plates), alors que `ImageEnhance.Sharpness` amplifie uniformément. Les deux peuvent être cumulés (les deux blocs s'appliquent l'un après l'autre dans le pipeline) — pas de garde-fou empêchant de combiner les deux, c'est un choix créatif laissé à l'utilisateur.

## Conversion slider → valeur

Le slider radius stocke un entier (`5..50`) divisé par 10 pour obtenir un flottant (`round(val / 10.0, 1)`) — pattern classique pour un `QSlider` (int uniquement) représentant une valeur décimale. Reproduire ce pattern (pas `QDoubleSpinBox`) pour tout nouveau réglage décimal dans ce panneau, par cohérence avec `gamma` (skill `adjust-levels`) qui suit le même principe.

## Modifier ces fonctions

- Changer l'intensité/la formule de la netteté simple → bloc `if sharpness != 0` de `apply_adjustments()`.
- Changer les bornes ou le comportement de l'Unsharp Mask → bloc `if unsharp_percent > 0`.
- Les deux dialogs (`adjustments_dialog_qt.py` et `adjustments_viewers_qt.py`) répliquent les mêmes bornes de slider (`-100..100`, `5..50`, `0..200`, `0..30`) indépendamment — si une borne change, la mettre à jour aux deux endroits (grep `_sharp_slider`/`_unsharp_.*_slider` dans les deux fichiers).

## Références croisées

- `adjustments-panel` — structure générale, `_get_settings()`.
- `viewers` — modes `'sharpness'` et `'unsharp'` de `AdjustmentViewerDialog`.
