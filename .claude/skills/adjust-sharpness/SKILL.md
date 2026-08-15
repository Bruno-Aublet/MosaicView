---
name: adjust-sharpness
description: Localiser ou modifier les fonctions "Netteté" et "Netteté adaptative" (Unsharp Mask) de MosaicView. Utiliser dès qu'une tâche touche à settings['sharpness'], settings['unsharp_radius'/'unsharp_percent'/'unsharp_threshold'], ImageFilter.UnsharpMask, ou la barre d'outils flottante de la visionneuse principale en mode sharpness/unsharp.
---

# Ajustements "Netteté" et "Netteté adaptative" — MosaicView

Deux réglages distincts mais étroitement liés (tous deux affectent la netteté perçue) — **indépendants dans le code** : deux blocs séparés dans `settings`, appliqués l'un après l'autre par la même fonction de traitement. Partagent un seul et même emplacement UI, bi-mode, dans la barre d'outils flottante de la visionneuse principale (v1.7.4) — plus aucun panneau ni visionneuse dédiés depuis le retrait de l'ancien panneau Ajustements classique pour ces deux réglages (voir section UI plus bas). Pour l'orchestration générale du panneau Ajustements restant (6 autres modes), voir skill `adjustments-panel`.

## Formule PIL (`adjustments_processing_qt.py::apply_adjustments()`)

Indépendante de toute UI — seul et unique moteur de calcul, appelé par la barre d'outils de la visionneuse (preview live ET commit réel, voir plus bas).

### Netteté simple (`sharpness`)

```python
if sharpness != 0:
    if sharpness > 0:
        img = ImageEnhance.Sharpness(img).enhance(1.0 + sharpness / 100.0)
    else:
        img = img.filter(ImageFilter.GaussianBlur(abs(sharpness) / 20.0))
```
**Asymétrie voulue** : les valeurs positives et négatives empruntent deux mécanismes PIL complètement différents. Positif → `ImageEnhance.Sharpness` (renforcement de contraste local, va jusqu'à ×2.0 d'intensité à +100). Négatif → flou gaussien (`GaussianBlur`, rayon jusqu'à 5.0px à -100) — ce n'est **pas** un "moins de netteté" au sens `ImageEnhance.Sharpness(factor<1)`, c'est un vrai flou. Si le comportement doit devenir symétrique, c'est un changement de comportement visible à documenter, pas un simple ajustement de formule.

### Netteté adaptative / Unsharp Mask (3 paramètres)

```python
if unsharp_percent > 0:
    img = img.filter(ImageFilter.UnsharpMask(
        radius=unsharp_radius, percent=unsharp_percent, threshold=unsharp_thresh))
```
Wrapper direct de `PIL.ImageFilter.UnsharpMask`. Bornes : `unsharp_radius` (0.5-5.0, float), `unsharp_percent` (0-200, int), `unsharp_threshold` (0-30, int) — défauts respectifs 2.0/0/3. **C'est `unsharp_percent` qui active/désactive le filtre** (condition `> 0`), pas `unsharp_radius`/`unsharp_threshold` qui n'ont aucun effet seuls. Bouger radius/threshold sans toucher percent ne produit donc visuellement aucun changement — comportement attendu de PIL, pas un bug.

**Différence fonctionnelle avec la netteté simple positive** : Unsharp Mask a un seuil (`threshold`) qui ignore les variations de contraste locales trop faibles (évite d'amplifier le bruit dans les zones plates), alors que `ImageEnhance.Sharpness` amplifie uniformément. Les deux peuvent être cumulés (les deux blocs s'appliquent l'un après l'autre dans le pipeline) — pas de garde-fou empêchant de combiner les deux, c'est un choix créatif laissé à l'utilisateur.

## UI — barre d'outils de la visionneuse principale (v1.7.4), seul point d'accès

**Historique** : les deux réglages vivaient à l'origine dans le panneau Ajustements classique (`adjustments_dialog_qt.py`, sections `_grp_sharp`/`_grp_unsharp`) et dans `AdjustmentViewerDialog` (`adjustments_viewers_qt.py`, modes `'sharpness'`/`'unsharp'`). Les deux ont migré vers la barre d'outils flottante de la visionneuse principale (skill `viewers`, section "Le cas de la netteté") en deux passes séparées — sharpness d'abord, puis unsharp — et **l'ancien panneau/visionneuse ont été entièrement retirés à chaque fois**, pas seulement redirigés (voir CHANGELOG.md [1.7.4]). Ces attributs/modes n'existent donc plus nulle part dans le code actuel — ne pas les chercher ni les recréer.

- Module : `modules/qt/sharpness_tool_qt.py`.
- Icône bi-mode unique dans la barre (`state.sharpness_mode` 0=sharpness/1=unsharp, clic droit pour basculer, icône elle-même changée `BTN_Sharpness.png`/`BTN_Unsharp.png`) — détail complet du mécanisme (preview live, commit auto au relâchement, undo/redo par point d'historique, persistance après changement de page/undo-redo) dans le skill `viewers`, section "Le cas de la netteté". Ce skill-ci ne couvre que la formule PIL et ses bornes, pas l'intégration UI.
- Panneaux flottants : `_SharpnessOptionsPanel` (1 réglette -100..+100) en mode 0, `_UnsharpOptionsPanel` (3 réglettes radius/percent/threshold, disposition horizontale) en mode 1 — jamais affichés simultanément.
- Les deux modes réutilisent `apply_adjustments()`/`apply_image_adjustments()` (`adjustments_processing_qt.py`) sans dupliquer la formule — même fonction qu'utilisait l'ancien panneau.

## Conversion slider → valeur

Le slider radius (`_UnsharpOptionsPanel._radius_slider`, `viewer_toolbar_qt.py`/`sharpness_tool_qt.py`) stocke un entier (`5..50`) divisé par 10 pour obtenir un flottant (`round(val / 10.0, 1)`) — pattern classique pour un `QSlider` (int uniquement) représentant une valeur décimale. Reproduire ce pattern (pas `QDoubleSpinBox`) pour tout nouveau réglage décimal, par cohérence avec `gamma` (skill `adjust-levels`) qui suit le même principe.

## Modifier ces fonctions

- Changer l'intensité/la formule de la netteté simple → bloc `if sharpness != 0` de `apply_adjustments()` (`adjustments_processing_qt.py`).
- Changer les bornes ou le comportement de l'Unsharp Mask → bloc `if unsharp_percent > 0` de la même fonction, **et** les bornes des sliders dans `_UnsharpOptionsPanel` (`sharpness_tool_qt.py`) — un seul endroit UI à mettre à jour désormais (plus deux dialogs à synchroniser comme du temps de l'ancien panneau).
- Changer l'apparence/le comportement des panneaux flottants (réglettes, tooltips, positionnement) → `_SharpnessOptionsPanel`/`_UnsharpOptionsPanel` dans `sharpness_tool_qt.py`, voir skill `viewers`.

## Références croisées

- `viewers` — intégration complète dans la barre d'outils de la visionneuse principale (icône bi-mode, preview live, commit, undo/redo, persistance) : section "Le cas de la netteté".
- `adjustments-panel` — structure générale du panneau Ajustements restant (6 modes, sharpness/unsharp n'en font plus partie), `_get_settings()`.
