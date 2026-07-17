---
name: adjust-effects
description: Localiser ou modifier la fonction "Effets" du panneau Ajustements d'image (Aucun effet / Noir et blanc / Sépia / Inversion de couleurs). Utiliser dès qu'une tâche touche à settings['effect'] ou aux radios d'effets.
---

# Ajustement "Effets" — MosaicView

Section radio du panneau Ajustements d'image (colonne aperçu, dernière section — physiquement construite dans `_build_left_column()` mais affichée dans la colonne aperçu, voir skill `adjustments-panel`). Pour l'orchestration générale du panneau, voir ce même skill.

## Où

- UI : `adjustments_dialog_qt.py::_build_left_column()`, groupe `self._grp_effects` / `self._effect_radios` (4 `QRadioButton` : `none`/`grayscale`/`sepia`/`invert`) — **construit** dans la colonne gauche mais **affiché** (`layout.addWidget`) dans `_build_preview_column()`
- Handler : `_on_effect_changed(key)` → `self._effect = key` → `_update_preview()`
- Traitement : `adjustments_processing_qt.py::apply_adjustments()`, bloc `# ── Effets ──`
- Pas de visionneuse dédiée — cette section n'a pas de bouton "Ajuster avec la visionneuse" (pas de valeur continue à affiner, seulement 4 choix discrets)

## Les 4 effets

```python
if effect == 'grayscale':
    img = ImageOps.grayscale(img).convert('RGB')
elif effect == 'sepia':
    img = ImageOps.grayscale(img).convert('RGB')
    arr = np.array(img, dtype=np.float32)
    r = np.clip(arr[:,:,0]*0.393 + arr[:,:,1]*0.769 + arr[:,:,2]*0.189, 0, 255)
    g = np.clip(arr[:,:,0]*0.349 + arr[:,:,1]*0.686 + arr[:,:,2]*0.168, 0, 255)
    b = np.clip(arr[:,:,0]*0.272 + arr[:,:,1]*0.534 + arr[:,:,2]*0.131, 0, 255)
    img = Image.fromarray(np.stack([r, g, b], axis=2).astype(np.uint8))
elif effect == 'invert':
    if img.mode == 'RGBA':
        r, g, b, a = img.split()
        img = Image.merge('RGBA', (ImageOps.invert(r), ImageOps.invert(g), ImageOps.invert(b), a))
    else:
        img = ImageOps.invert(img.convert('RGB'))
```

- **`none`** (défaut) : aucun traitement, no-op.
- **`grayscale`** (Noir et blanc) : `ImageOps.grayscale()` puis reconversion en `RGB` — **reste en mode RGB visuellement gris**, ne produit pas un mode PIL `'L'` réel (contrairement à Mode d'image → `L`, skill `adjust-image-mode`, qui lui change vraiment le mode). Effet purement visuel, pas une réduction de profondeur de couleur.
- **`sepia`** : d'abord désaturé en gris (même appel que `grayscale`), puis une matrice de conversion sépia standard (constantes `0.393/0.769/0.189` etc. — la matrice sépia classique la plus répandue, ne pas la modifier sans raison visuelle explicite) appliquée via `numpy` canal par canal, avec clampage `[0, 255]` sur chaque canal indépendamment.
- **`invert`** : **seul effet à préserver explicitement le canal alpha** — branche dédiée si `img.mode == 'RGBA'` qui inverse R/G/B mais recopie le canal A tel quel (`ImageOps.invert()` seul ne sait pas gérer RGBA nativement, d'où le split/merge manuel). Le cas générique (non-RGBA) convertit d'abord en RGB avant d'inverser, perdant toute transparence potentielle (ex. mode `LA` ou `P` avec transparence) — si un besoin de préserver l'alpha sur ces modes apparaît, suivre le même pattern split/merge que la branche RGBA plutôt que d'improviser.

## Ordre dans le pipeline

Ce bloc s'exécute **après** la netteté/netteté adaptative mais **avant** la suppression des couleurs, le seuil, les niveaux, le mode d'image et la profondeur de couleur. Un effet Sépia suivi d'un réglage de Niveaux (point noir/blanc/gamma) s'applique donc sur l'image déjà teintée sépia, pas sur l'image couleur d'origine — comportement WYSIWYG cohérent avec l'ordre d'affichage des sections dans le panneau (Effets est physiquement listé après Luminosité/Netteté dans le flux visuel de gauche à droite, mais avant Niveaux qui est dans la colonne droite — l'ordre du pipeline ne suit pas l'ordre des colonnes, se fier uniquement à l'ordre des blocs dans `apply_adjustments()`).

## Exclusivité mutuelle (radios)

Contrairement à la plupart des autres sections (réglettes cumulables entre elles), les 4 effets sont un `QButtonGroup` — un seul actif à la fois, pas de cumul possible entre Noir et blanc/Sépia/Inversion. Rien n'empêche en revanche de cumuler un effet avec les autres sections du panneau (saturation, niveaux, etc.).

## Modifier cette fonction

Formule d'un effet existant → bloc correspondant de `apply_adjustments()`. Nouvel effet → ajouter le radio dans `_build_left_column()` (`self._effect_radios`), une entrée `effect_labels` dans `_retranslate()` (+ clé de traduction `dialogs.adjustments.effect_xxx`), et une branche `elif effect == '...'` dans `apply_adjustments()`.

## Références croisées

- `adjustments-panel` — structure générale, disposition colonnes vs pipeline de traitement.
- `adjust-remove-colors` — traitement bien plus complexe visant un rendu manga/BD, à ne pas confondre avec l'effet simple "Noir et blanc" de cette section.
- `adjust-saturation` — désaturation à -100 reste en RGB comme l'effet grayscale, mais via une formule `ImageEnhance.Color` différente et réglable en continu.
