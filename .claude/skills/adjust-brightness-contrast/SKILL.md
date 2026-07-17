---
name: adjust-brightness-contrast
description: Localiser ou modifier la fonction "Luminosité et contraste" du panneau Ajustements d'image. Utiliser dès qu'une tâche touche à settings['brightness'/'contrast'], ImageEnhance.Brightness/Contrast, ou aux réglettes luminosité/contraste.
---

# Ajustement "Luminosité et contraste" — MosaicView

Section réglette du panneau Ajustements d'image (colonne droite, 1ère section) — les deux réglages partagent une seule section UI et sont traités comme une paire dans le code, mais restent deux clés `settings` indépendantes. Pour l'orchestration générale du panneau, voir skill `adjustments-panel`.

## Où

- UI : `adjustments_dialog_qt.py::_build_right_column()`, groupe `self._grp_bc` avec 2 sliders empilés : `_bright_slider` puis `_contrast_slider` (tous deux range -100..+100, défaut 0)
- Handlers : `_on_bright_changed(val)` → `self._brightness`, `_on_contrast_changed(val)` → `self._contrast`
- Traitement : `adjustments_processing_qt.py::apply_adjustments()`, tout début de la fonction
- Visionneuse dédiée : mode `'brightness'` (seul mode avec **2** réglettes dans la toolbar simultanément)

## Formule

```python
if brightness != 0:
    img = ImageEnhance.Brightness(img).enhance(1.0 + brightness / 100.0)
if contrast != 0:
    img = ImageEnhance.Contrast(img).enhance(1.0 + contrast / 100.0)
```
Wrappers directs de `PIL.ImageEnhance` — mapping linéaire `[-100, 100] → [0.0, 2.0]` du facteur `enhance()`, `0` UI = `1.0` PIL = image inchangée. La luminosité est appliquée **avant** le contraste dans le pipeline (ordre fixe, pas configurable) — inverser l'ordre changerait légèrement le résultat visuel sur des images à fort contraste, ne pas le faire sans concertation explicite.

Ce bloc est le tout premier de `apply_adjustments()` — tous les autres réglages (netteté, effets, niveaux, mode d'image, profondeur…) s'appliquent sur le résultat déjà éclairci/contrasté.

## Modifier cette fonction

Formule et bornes à modifier uniquement dans ce premier bloc de `apply_adjustments()`. Si les bornes de slider changent (`-100..100`), les mettre à jour identiquement dans `adjustments_dialog_qt.py` (`_bright_slider`/`_contrast_slider`) **et** `adjustments_viewers_qt.py` (`_bright_slider`/`_contrast_slider` du mode `'brightness'`) — deux définitions indépendantes, pas de constante partagée.

## Références croisées

- `adjustments-panel` — structure générale, `_get_settings()`.
- `viewers` — mode `'brightness'` de `AdjustmentViewerDialog`.
- `adjust-levels` — réglage plus fin (point noir/gamma/point blanc) qui recouvre partiellement le même objectif visuel mais via une LUT plutôt qu'un facteur `ImageEnhance`, appliqué plus tard dans le pipeline.
