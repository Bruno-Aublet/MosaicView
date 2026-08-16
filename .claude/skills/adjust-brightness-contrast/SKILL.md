---
name: adjust-brightness-contrast
description: Localiser ou modifier la fonction "Luminosité et contraste" de MosaicView. Utiliser dès qu'une tâche touche à settings['brightness'/'contrast'], ImageEnhance.Brightness/Contrast, ou à la barre d'outils flottante de la visionneuse principale en mode brightness.
---

# Ajustement "Luminosité et contraste" — MosaicView

Deux réglages liés (une seule section UI, un seul panneau flottant) mais **indépendants dans le code** : deux clés `settings` séparées, appliquées l'une après l'autre par la même fonction de traitement. Vivent dans la barre d'outils flottante de la visionneuse principale (v1.7.4) — plus aucun panneau ni visionneuse dédiés depuis le retrait de l'ancien panneau Ajustements classique pour ce réglage (voir section UI plus bas). L'ancien panneau Ajustements classique (`AdjustmentsDialog`) a lui-même été supprimé en totalité le 2026-08-16, une fois ses 3 dernières fonctions (profondeur de couleur, effets, mode d'image) migrées à leur tour — voir skill `viewers`.

## Formule PIL (`image_processing_qt.py::apply_adjustments()`)

Indépendante de toute UI — seul et unique moteur de calcul, appelé par la barre d'outils de la visionneuse (preview live ET commit réel, voir plus bas). Tout début de la fonction, avant tous les autres réglages (netteté, effets, niveaux, mode d'image, profondeur…), qui s'appliquent donc sur le résultat déjà éclairci/contrasté.

```python
if brightness != 0:
    img = ImageEnhance.Brightness(img).enhance(1.0 + brightness / 100.0)
if contrast != 0:
    img = ImageEnhance.Contrast(img).enhance(1.0 + contrast / 100.0)
```

Wrappers directs de `PIL.ImageEnhance` — mapping linéaire `[-100, 100] → [0.0, 2.0]` du facteur `enhance()`, `0` UI = `1.0` PIL = image inchangée. La luminosité est appliquée **avant** le contraste dans le pipeline (ordre fixe, pas configurable) — inverser l'ordre changerait légèrement le résultat visuel sur des images à fort contraste, ne pas le faire sans concertation explicite.

## UI — barre d'outils de la visionneuse principale (v1.7.4), seul point d'accès

**Historique** : ce réglage vivait à l'origine dans le panneau Ajustements classique (`adjustments_dialog_qt.py`, section `_grp_bc`, sliders `_bright_slider`/`_contrast_slider`) et dans `AdjustmentViewerDialog` (`adjustments_viewers_qt.py`, mode `'brightness'`). Il a migré vers la barre d'outils flottante de la visionneuse principale (skill `viewers`, section "Le cas de la luminosité/contraste") et **l'ancien panneau/visionneuse ont été entièrement retirés**, pas seulement redirigés (voir CHANGELOG.md [1.7.4]). Ces attributs/mode n'existent donc plus nulle part dans le code actuel — ne pas les chercher ni les recréer.

- Module : `modules/qt/brightness_tool_qt.py` (dédié, pas dans `sharpness_tool_qt.py` — voir CLAUDE.md, chaque outil migré a son propre module).
- Une seule icône dans la barre, **pas de bi-mode** (contrairement à sharpness/unsharp) : `BTN_Brightness.png`, fixe.
- Panneau flottant unique `_BrightnessOptionsPanel` : les 2 réglettes luminosité + contraste empilées verticalement dans le même panneau (reprend la disposition de l'ancien panneau Ajustements classique, `_grp_bc`, plutôt qu'une disposition horizontale inédite).
- Réutilise `apply_adjustments()`/`apply_image_adjustments()` (`image_processing_qt.py`) sans dupliquer la formule — même fonction qu'utilisait l'ancien panneau.
- Détail complet du mécanisme d'intégration (preview live, commit auto au relâchement, undo/redo par point d'historique, persistance après changement de page/undo-redo, champ de preview partagé avec sharpness/unsharp) dans le skill `viewers`, section "Le cas de la luminosité/contraste". Ce skill-ci ne couvre que la formule PIL et ses bornes, pas l'intégration UI.

## Modifier cette fonction

- Changer l'intensité/la formule → les deux blocs `if brightness != 0` / `if contrast != 0` de `apply_adjustments()` (`image_processing_qt.py`).
- Changer les bornes (`-100..100`) → même bloc, **et** les bornes des sliders dans `_BrightnessOptionsPanel` (`brightness_tool_qt.py`) — un seul endroit UI à mettre à jour désormais (plus deux dialogs à synchroniser comme du temps de l'ancien panneau).
- Changer l'apparence/le comportement du panneau flottant (réglettes, tooltips, positionnement) → `_BrightnessOptionsPanel` dans `brightness_tool_qt.py`, voir skill `viewers`.

## Références croisées

- `viewers` — intégration complète dans la barre d'outils de la visionneuse principale (icône, preview live, commit, undo/redo, persistance) : section "Le cas de la luminosité/contraste".
- `adjust-levels` — réglage plus fin (point noir/gamma/point blanc) qui recouvre partiellement le même objectif visuel mais via une LUT plutôt qu'un facteur `ImageEnhance`, appliqué plus tard dans le pipeline, aussi migré dans la barre d'outils de la visionneuse.
- `adjust-sharpness` — même chantier de migration (idees.txt #3), même méthode (module dédié, panneau flottant, retrait complet de l'ancien panneau), pattern de référence pour cette migration.
