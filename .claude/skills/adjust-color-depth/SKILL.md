---
name: adjust-color-depth
description: Localiser ou modifier la fonction "Profondeur de couleur" du panneau Ajustements d'image (32/24/8/1 bit). Utiliser dès qu'une tâche touche à settings['color_depth'] ou aux radios de profondeur de couleur.
---

# Ajustement "Profondeur de couleur" — MosaicView

Section radio du panneau Ajustements d'image (colonne gauche, 1ère section). Pour l'orchestration générale du panneau (aperçu, application, multi-image), voir skill `adjustments-panel` — ce skill ne couvre que la logique propre à ce réglage.

## Où

- UI : `adjustments_dialog_qt.py::_build_left_column()`, groupe `self._grp_depth` / `self._depth_radios` (5 `QRadioButton` dans un `QButtonGroup`)
- Handler : `_on_depth_changed(key)` → `self._color_depth = key` → `_update_preview()`
- Traitement : `adjustments_processing_qt.py::apply_adjustments()`, bloc `# ── Profondeur de couleur ──` (fin de la fonction, après le mode d'image)

## Valeurs possibles (`settings['color_depth']`)

| Clé | Effet PIL |
|---|---|
| `unchanged` (défaut) | aucun changement |
| `32` | force `img.convert('RGBA')` |
| `24` | force `img.convert('RGB')` |
| `8` | si extension d'origine `.jpg`/`.jpeg` → `img.convert('L')` (niveaux de gris, JPEG ne supporte pas la palette indexée) ; sinon → `img.convert('P', palette=Image.ADAPTIVE, colors=256).convert('RGB')` (palette 256 couleurs adaptative, reconvertie en RGB pour l'affichage/l'export) |
| `1` | `img.convert('L').point(lambda p: 255 if p > 128 else 0, '1')` — seuil fixe à 128, noir et blanc pur 1-bit |

**Dépendance à `original_ext`** : le comportement de `'8'` change selon le format source (branché sur `settings.get('original_ext', '')`, injecté par le dialog/le traitement d'application). Ne pas court-circuiter ce paramètre en testant le mode PIL actuel de l'image — c'est bien l'extension de fichier d'origine qui pilote la branche, pas le mode PIL courant.

**`for_preview`** : en mode `'1'`, l'image 1-bit est réencodée via un buffer PNG intermédiaire puis reconvertie en RGB avant d'être renvoyée à l'aperçu Qt (qui ne sait pas afficher nativement le mode `'1'`). L'application réelle (`for_preview=False`) laisse le mode `'1'` tel quel — la profondeur réellement écrite dans le fichier final n'est donc **pas** ce que l'aperçu affiche visuellement (l'aperçu reconvertit toujours en RGB pour être affichable, quel que soit le mode cible réel).

## Interaction avec le mode d'image

Ce réglage s'applique **après** le bloc "Mode d'image" (`settings['image_mode']`) dans `apply_adjustments()` — les deux peuvent donc se cumuler et le dernier (profondeur de couleur) a le dernier mot sur le mode PIL final. Voir skill `adjust-image-mode` pour l'autre réglage de mode — ne pas les confondre : "Mode d'image" cible le mode PIL exact (RGB/RGBA/L/LA/CMYK/1/P), "Profondeur de couleur" est une simplification grand public à 5 choix qui recouvre partiellement les mêmes modes PIL mais avec un vocabulaire différent (bits plutôt que noms de mode).

## Désactivation automatique

`AdjustmentsDialog._disable_current_mode_radios()` désactive le radio correspondant au mode PIL déjà courant, uniquement si **toutes** les images sélectionnées partagent le même mode PIL. Table `PIL_TO_DEPTH` : `RGBA→'32'`, `RGB→'24'`, `L→'8'`, `P→'8'`, `'1'→'1'`. Voir skill `adjustments-panel` pour le détail.

## Compression forcée en PNG

Dans `apply_image_adjustments()` (`adjustments_processing_qt.py`), si le mode PIL résultant est `RGBA`/`LA`/`P`/`1` et l'extension d'origine est `.jpg`/`.jpeg`, l'extension de sauvegarde est forcée à `.png` (JPEG ne supporte aucun de ces modes). Ce garde-fou est générique (partagé avec `adjust-image-mode` et `adjust-transparency`) — ne pas le dupliquer si une nouvelle profondeur de couleur est ajoutée, il s'applique déjà à toute combinaison produisant un de ces 4 modes.

## Modifier cette fonction

Pour changer la formule d'une profondeur existante ou en ajouter une nouvelle : modifier uniquement le bloc `# ── Profondeur de couleur ──` de `apply_adjustments()`. Pour ajouter une nouvelle option, il faut aussi : ajouter le radio dans `_build_left_column()` (`self._depth_radios`), une entrée dans `depth_labels` de `_retranslate()` (+ clé de traduction `dialogs.adjustments.depth_xxx`), et éventuellement une entrée dans `PIL_TO_DEPTH` si la nouvelle option correspond à un mode PIL détectable pour la désactivation automatique.

## Références croisées

- `adjustments-panel` — structure générale du panneau, `_get_settings()`, flux Appliquer.
- `adjust-image-mode` — l'autre réglage de mode, appliqué juste avant celui-ci dans le pipeline.
- `apply-image-operation` — pattern undo/redo respecté par `apply_image_adjustments()`.
