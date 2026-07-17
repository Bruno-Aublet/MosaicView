---
name: adjust-saturation
description: Localiser ou modifier la fonction "Saturation" du panneau Ajustements d'image. Utiliser dès qu'une tâche touche à settings['saturation'], ImageEnhance.Color, ou à la réglette de saturation.
---

# Ajustement "Saturation" — MosaicView

Section réglette du panneau Ajustements d'image (colonne aperçu, 3e section). Pour l'orchestration générale du panneau, voir skill `adjustments-panel`.

## Où

- UI : `adjustments_dialog_qt.py::_build_preview_column()`, groupe `self._grp_sat` / `self._sat_slider` (range -100..+100, défaut 0)
- Handler : `_on_sat_changed(val)` → `self._saturation = val` → `_update_preview()`
- Traitement : `adjustments_processing_qt.py::apply_adjustments()`, bloc luminosité/contraste/netteté/saturation (tout début de fonction, dernier des 4)
- Visionneuse dédiée : mode `'saturation'`

## Formule

```python
if saturation != 0:
    img = ImageEnhance.Color(img).enhance(max(0.0, 1.0 + saturation / 100.0))
```
Wrapper direct de `PIL.ImageEnhance.Color` — mapping linéaire `[-100, 100] → [0.0, 2.0]`, `0` UI = `1.0` PIL = inchangé. **Seul réglage de ce groupe de 4 (luminosité/contraste/netteté/saturation) à clamper explicitement la borne basse avec `max(0.0, ...)`** — sans ce clamp, `saturation = -150` (hors bornes UI mais atteignable si la formule était réutilisée ailleurs avec une plage différente) produirait un facteur négatif, invalide pour `ImageEnhance.Color`. Ce garde-fou est donc une protection défensive, pas un comportement observable dans la plage UI actuelle (-100 minimum → facteur `0.0` exactement, jamais négatif).

`-100` = image totalement désaturée (équivalent visuel à un niveau de gris, mais **reste en mode RGB** — contrairement à l'effet "Noir et blanc" de la section Effets qui convertit réellement le mode PIL, voir skill `adjust-effects`). `+100` = saturation doublée.

Ce bloc s'exécute avant tous les autres traitements du pipeline (netteté adaptative, effets, seuil, niveaux, mode d'image, profondeur, compression) — un effet "Noir et blanc" appliqué après annulerait visuellement toute saturation déjà réglée, ce qui est le comportement normal attendu si l'utilisateur combine les deux.

## Modifier cette fonction

Formule et bornes uniquement dans ce bloc de `apply_adjustments()`. Bornes de slider à répliquer identiquement dans `adjustments_dialog_qt.py::_sat_slider` et `adjustments_viewers_qt.py::_sat_slider` (mode `'saturation'`) si elles changent.

## Références croisées

- `adjustments-panel` — structure générale, `_get_settings()`.
- `viewers` — mode `'saturation'` de `AdjustmentViewerDialog`.
- `adjust-effects` — l'effet "Noir et blanc" convertit réellement le mode PIL en niveaux de gris, contrairement à une désaturation à -100 qui reste en RGB.
