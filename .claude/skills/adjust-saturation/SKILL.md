---
name: adjust-saturation
description: Localiser ou modifier la fonction "Saturation" (formule PIL), migrée dans la barre d'outils de la visionneuse principale (saturation_tool_qt.py). Utiliser dès qu'une tâche touche à settings['saturation'], ImageEnhance.Color, ou à la réglette de saturation.
---

# Ajustement "Saturation" — MosaicView

**Migrée dans la barre d'outils de la visionneuse principale (v1.7.4, 2026-08-14, 4e mode d'ajustement migré)** : l'ancienne section du panneau Ajustements classique (`adjustments_dialog_qt.py`, groupe `_grp_sat`) et le mode `'saturation'` de l'ancienne visionneuse annexe (`adjustments_viewers_qt.py`) ont été **entièrement retirés**. La saturation ne vit désormais que dans `modules/qt/saturation_tool_qt.py` (icône `BTN_Saturation.png` de `_ViewerToolbar`, panneau flottant `_SaturationOptionsPanel`) — voir skill `viewers`, section "Le cas de la saturation", pour l'UI/le flux complet (preview live, commit au relâchement, slider qui reste sur la valeur appliquée — corrigé le 2026-08-14, il revenait initialement à 0 par erreur). Ce skill-ci ne couvre que la formule PIL, inchangée.

## Où

- UI : `saturation_tool_qt.py::_SaturationOptionsPanel` (réglette -100..+100, défaut 0) dans la barre d'outils de `image_viewer_qt.py::ImageViewer`
- Commit : `saturation_tool_qt.py::SaturationViewerMixin.perform_saturation()`
- Traitement : `adjustments_processing_qt.py::apply_adjustments()`, bloc luminosité/contraste/netteté/saturation (tout début de fonction, dernier des 4) — moteur de calcul inchangé, partagé, appelé désormais par `saturation_tool_qt.py` au lieu du panneau classique

## Formule

```python
if saturation != 0:
    img = ImageEnhance.Color(img).enhance(max(0.0, 1.0 + saturation / 100.0))
```
Wrapper direct de `PIL.ImageEnhance.Color` — mapping linéaire `[-100, 100] → [0.0, 2.0]`, `0` UI = `1.0` PIL = inchangé. **Seul réglage de ce groupe de 4 (luminosité/contraste/netteté/saturation) à clamper explicitement la borne basse avec `max(0.0, ...)`** — sans ce clamp, `saturation = -150` (hors bornes UI mais atteignable si la formule était réutilisée ailleurs avec une plage différente) produirait un facteur négatif, invalide pour `ImageEnhance.Color`. Ce garde-fou est donc une protection défensive, pas un comportement observable dans la plage UI actuelle (-100 minimum → facteur `0.0` exactement, jamais négatif).

`-100` = image totalement désaturée (équivalent visuel à un niveau de gris, mais **reste en mode RGB** — contrairement à l'effet "Noir et blanc" de la section Effets qui convertit réellement le mode PIL, voir skill `adjust-effects`). `+100` = saturation doublée.

Ce bloc s'exécute avant tous les autres traitements du pipeline (netteté adaptative, effets, seuil, niveaux, mode d'image, profondeur, compression) — un effet "Noir et blanc" appliqué après annulerait visuellement toute saturation déjà réglée, ce qui est le comportement normal attendu si l'utilisateur combine les deux.

## Modifier cette fonction

Formule et bornes uniquement dans ce bloc de `apply_adjustments()`. Bornes de slider à répliquer dans `saturation_tool_qt.py::_SaturationOptionsPanel._RANGE_MIN/_RANGE_MAX` si elles changent.

## Références croisées

- `viewers` — section "Le cas de la saturation" : UI de la barre d'outils, preview live, commit, `state.saturation_value_by_history_index`.
- `apply-image-operation` — pattern suivi par `perform_saturation()` pour committer dans `entry['bytes']`.
- `adjust-effects` — l'effet "Noir et blanc" convertit réellement le mode PIL en niveaux de gris, contrairement à une désaturation à -100 qui reste en RGB.
