---
name: adjust-brightness-contrast
description: Localiser ou modifier la fonction "Luminosité et contraste" de MosaicView. Utiliser dès qu'une tâche touche à settings['brightness'/'contrast'], ImageEnhance.Brightness/Contrast, ou à la barre d'outils flottante de la visionneuse principale en mode brightness.
---

# Ajustement "Luminosité et contraste" — MosaicView

Deux réglages liés (une seule section UI, un seul panneau flottant) mais **indépendants dans le code** : deux clés `settings` séparées, appliquées l'une après l'autre par la même fonction de traitement. Vivent dans la barre d'outils flottante de la visionneuse principale (voir section UI plus bas) — il n'existe pas de fenêtre d'ajustements séparée dans l'application, voir skill `viewers`.

## Formule PIL (`image_processing_qt.py::apply_adjustments()`)

Indépendante de toute UI — seul et unique moteur de calcul, appelé par la barre d'outils de la visionneuse (preview live ET commit réel, voir plus bas). Tout début de la fonction, avant tous les autres réglages (netteté, effets, niveaux, mode d'image, profondeur…), qui s'appliquent donc sur le résultat déjà éclairci/contrasté.

```python
if brightness != 0:
    img = ImageEnhance.Brightness(img).enhance(1.0 + brightness / 100.0)
if contrast != 0:
    img = ImageEnhance.Contrast(img).enhance(1.0 + contrast / 100.0)
```

Wrappers directs de `PIL.ImageEnhance` — mapping linéaire `[-100, 100] → [0.0, 2.0]` du facteur `enhance()`, `0` UI = `1.0` PIL = image inchangée. La luminosité est appliquée **avant** le contraste dans le pipeline (ordre fixe, pas configurable) — inverser l'ordre changerait légèrement le résultat visuel sur des images à fort contraste, ne pas le faire sans concertation explicite.

**Conversion préalable du mode (`_to_filterable`, `image_processing_qt.py`)** : avant ce bloc, dès que luminosité, contraste, netteté, saturation ou netteté adaptative est demandé, l'image passe par `_to_filterable(img)`. `ImageEnhance`/`ImageFilter` lèvent « image has wrong mode » (ou « cannot filter palette images ») sur les modes P (PNG 8 bits, GIF, BMP 8 bits), 1 (noir et blanc pur) et 16/32 bits : P/PA passent en RGB (RGBA si transparence de palette), 1 en L, les modes `I*` en L par décalage de 8 bits (un `convert('L')` direct écrêterait tout au-dessus de 255, image quasi blanche). CMYK passe aussi en RGB : `ImageEnhance.Brightness` mélange vers une image « sans encre », donc blanche, ce qui inverse l'effet (baisser la luminosité éclaircirait). RGB/RGBA/L/LA sont laissés tels quels. Conséquence assumée : une page palette ou CMYK ressort en RGB(A) après ces réglages (un GIF est requantifié en palette à l'enregistrement).

## UI — barre d'outils de la visionneuse principale, seul point d'accès

- Module : `modules/qt/brightness_tool_qt.py` (dédié, pas dans `sharpness_tool_qt.py` — voir CLAUDE.md, chaque outil de la barre a son propre module).
- Une seule icône dans la barre, **pas de bi-mode** (contrairement à sharpness/unsharp) : `BTN_Brightness.png`, fixe.
- Panneau flottant unique `_BrightnessOptionsPanel` : les 2 réglettes luminosité + contraste empilées verticalement dans le même panneau.
- Réutilise `apply_adjustments()`/`apply_image_adjustments()` (`image_processing_qt.py`) sans dupliquer la formule.
- Détail complet du mécanisme d'intégration (preview live, commit auto au relâchement, undo/redo par point d'historique, persistance après changement de page/undo-redo, champ de preview partagé avec sharpness/unsharp) dans le skill `viewers`, section "Le cas de la luminosité/contraste". Ce skill-ci ne couvre que la formule PIL et ses bornes, pas l'intégration UI.

## Modifier cette fonction

- Changer l'intensité/la formule → les deux blocs `if brightness != 0` / `if contrast != 0` de `apply_adjustments()` (`image_processing_qt.py`).
- Changer les bornes (`-100..100`) → même bloc, **et** les bornes des sliders dans `_BrightnessOptionsPanel` (`brightness_tool_qt.py`).
- Changer l'apparence/le comportement du panneau flottant (réglettes, tooltips, positionnement) → `_BrightnessOptionsPanel` dans `brightness_tool_qt.py`, voir skill `viewers`.

## Références croisées

- `viewers` — intégration complète dans la barre d'outils de la visionneuse principale (icône, preview live, commit, undo/redo, persistance) : section "Le cas de la luminosité/contraste".
- `adjust-levels` — réglage plus fin (point noir/gamma/point blanc) qui recouvre partiellement le même objectif visuel mais via une LUT plutôt qu'un facteur `ImageEnhance`, appliqué plus tard dans le pipeline.
- `adjust-sharpness` — même méthode (module dédié, panneau flottant) dans la barre d'outils de la visionneuse.
