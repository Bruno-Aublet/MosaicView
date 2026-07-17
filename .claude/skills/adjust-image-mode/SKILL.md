---
name: adjust-image-mode
description: Localiser ou modifier la fonction "Mode d'image" (RGB/RGBA/L/LA/CMYK/1 bit/P) du panneau Ajustements d'image. Utiliser dès qu'une tâche touche à settings['image_mode'] ou aux radios de mode d'image.
---

# Ajustement "Mode d'image" — MosaicView

Section radio du panneau Ajustements d'image (colonne droite, dernière section). Pour l'orchestration générale du panneau, voir skill `adjustments-panel` — et surtout la skill `adjust-color-depth`, dont le rôle est proche et qui s'applique juste après celui-ci dans le pipeline.

## Où

- UI : `adjustments_dialog_qt.py::_build_right_column()`, groupe `self._grp_image_mode` / `self._mode_radios` (8 `QRadioButton`)
- Handler : `_on_image_mode_changed(key)` → `self._image_mode = key` → `_update_preview()`
- Traitement : `adjustments_processing_qt.py::apply_adjustments()`, bloc `# ── Mode d'image ──` (après les niveaux, avant la profondeur de couleur)

## Valeurs possibles (`settings['image_mode']`)

| Clé UI | Mode PIL cible | Remarque |
|---|---|---|
| `unchanged` (défaut) | aucun changement | |
| `RGB` | `RGB` | |
| `RGBA` | `RGBA` | |
| `L` | `L` (niveaux de gris) | |
| `LA` | `LA` (niveaux de gris + alpha) | |
| `CMYK` | `CMYK` | mode impression, non affichable nativement par Qt |
| `BW1` | `'1'` PIL | seul cas où la clé UI (`BW1`) diffère du nom du mode PIL réel (`'1'`) — voir bloc dédié ci-dessous |
| `P` | `P` (palette 256 couleurs) | conversion `img.convert('P')` **sans** argument `palette=Image.ADAPTIVE` (contrairement à Profondeur de couleur `'8'`, qui lui précise explicitement une palette adaptative) |

```python
if image_mode != 'unchanged':
    if image_mode == 'BW1':
        img = img.convert('L').point(lambda p: 255 if p > 128 else 0, '1')
    else:
        try:
            img = img.convert(image_mode)
        except Exception:
            pass
```
Le cas générique (`else`) délègue directement à `img.convert(image_mode)` — **la clé UI doit donc être exactement un nom de mode PIL valide** pour tous les cas sauf `BW1`. Toute nouvelle option ajoutée à ce groupe de radios doit soit correspondre à un nom PIL exact (`RGB`, `L`, `CMYK`...), soit recevoir sa propre branche `if` dédiée comme `BW1`. L'exception est avalée silencieusement (`except Exception: pass`) — une conversion PIL impossible laisse l'image dans son mode précédent sans avertir l'utilisateur, comportement voulu pour ne pas interrompre un traitement multi-image sur une seule image problématique.

## `for_preview` — reconversion pour affichage

```python
if for_preview:
    if img.mode == '1':
        # buffer PNG intermédiaire puis reconversion RGB
    elif img.mode not in ('RGB', 'RGBA', 'L'):
        img = img.convert('RGB')   # ex. CMYK, P → RGB pour affichage Qt
```
Comme pour la profondeur de couleur (skill `adjust-color-depth`), l'aperçu **ne montre jamais** le mode PIL exact demandé pour `CMYK`, `P` ou `'1'` — il est toujours reconverti en RGB/RGBA/L affichable. Le mode réellement écrit dans le fichier (application réelle, `for_preview=False`) est en revanche exact. Ne pas se fier à l'aperçu pour valider visuellement qu'un mode CMYK/P a été correctement appliqué — seule une inspection du fichier de sortie le confirme.

## Interaction avec Profondeur de couleur

Ce bloc s'exécute **avant** celui de la profondeur de couleur (skill `adjust-color-depth`) dans `apply_adjustments()` — si les deux réglages sont utilisés simultanément (aucun garde-fou UI ne les rend mutuellement exclusifs), la profondeur de couleur a le dernier mot sur le mode PIL final. Exemple : `image_mode='CMYK'` + `color_depth='24'` → le résultat final est `RGB`, pas `CMYK`, car le bloc profondeur reconvertit après coup.

## Désactivation automatique

Partage le même mécanisme que Profondeur de couleur : `_disable_current_mode_radios()` désactive le radio du mode PIL déjà courant, via la table `PIL_TO_MODE` (`RGB→'RGB'`, `RGBA→'RGBA'`, `L→'L'`, `LA→'LA'`, `CMYK→'CMYK'`, `'1'→'BW1'`, `P→'P'`) — appliquée seulement si toutes les images sélectionnées partagent le même mode PIL. Voir skill `adjustments-panel` pour le détail complet du mécanisme.

## Compression forcée en PNG

Comme pour la profondeur de couleur, si le mode résultant est `RGBA`/`LA`/`P`/`'1'` et le fichier d'origine est JPEG, l'extension de sauvegarde est forcée à `.png` dans `apply_image_adjustments()` (JPEG ne supporte aucun de ces modes) — un seul garde-fou partagé, voir skill `adjust-color-depth`.

## Modifier cette fonction

Nouvelle option de mode → ajouter le radio dans `_build_right_column()` (`self._mode_radios`), une entrée `mode_labels` dans `_retranslate()` (+ clé de traduction `dialogs.adjustments.image_mode_xxx`), et si la clé UI ne correspond pas exactement à un nom de mode PIL, une branche `if`/`elif` dédiée dans le bloc `apply_adjustments()` (suivre le modèle de `BW1`). Ajouter aussi l'entrée correspondante dans `PIL_TO_MODE` si le nouveau mode doit bénéficier de la désactivation automatique.

## Références croisées

- `adjustments-panel` — structure générale, désactivation automatique, `_get_settings()`.
- `adjust-color-depth` — réglage frère appliqué juste après, qui a le dernier mot en cas de cumul.
- `apply-image-operation` — pattern undo/redo, forçage `.png` sur JPEG incompatible.
