---
name: adjust-image-mode
description: Localiser ou modifier la fonction "Mode d'image" (RGB/RGBA/L/LA/CMYK/1 bit/P), migrée dans la barre d'outils de la visionneuse principale (image_mode_tool_qt.py). Utiliser dès qu'une tâche touche à settings['image_mode'], image_mode_tool_qt.py, ou aux radios de mode d'image de la barre d'outils.
---

# Ajustement "Mode d'image" — MosaicView

Il n'existe qu'une seule fenêtre dans toute l'application pour la lecture ET l'édition d'image — cette fonction vit dans la barre d'outils de la visionneuse principale, voir skill `viewers` pour l'orchestration générale de la barre (auto-masquage, undo/redo unifié, forçage mode simple page). Ce skill couvre la logique PIL et l'intégration dans la barre.

**Cadré explicitement sur le même modèle que la profondeur de couleur** (skill `adjust-color-depth`) — plus fidèlement qu'Effets (skill `adjust-effects`), qui s'en écarte : Mode d'image a un vrai équivalent PIL détectable pour chaque option (contrairement aux effets), donc le pattern de verrouillage EST dérivé du mode réel de l'image, exactement comme color_depth. Groupe de `QRadioButton`, chaque clic commit IMMÉDIATEMENT, le radio choisi devient coché ET grisé (non re-cliquable), les autres restent cliquables. Un radio "Restaurer l'original" (`_restore_radio`) apparaît/s'active dès qu'au moins un changement a été fait, et restaure `entry['bytes']` à l'état d'avant le TOUT PREMIER changement de la session — nouveau commit, ne dépile pas l'historique.

## Où

- **UI (barre d'outils)** : `modules/qt/image_mode_tool_qt.py` — `_ImageModeOptionsPanel` (panneau flottant, 3 lignes de radios : Restaurer l'original + RGB + RGBA sur la 1ère, L + LA + CMYK sur la 2e, 1 bit + P sur la 3e), `ImageModeCanvasMixin` (vide, aucun geste souris/overlay), `ImageModeViewerMixin` (`perform_image_mode(key)`, `perform_restore_image_mode()`, `_sync_image_mode_panel()`)
- **Icône de la barre** : `BTN_Image_Mode.png`, `tool_id="image_mode"` dans `viewer_toolbar_qt.py::_ViewerToolbar.__init__` — pas de bi-mode. L'icône elle-même n'est jamais grisée, mais certains des 7 radios de mode peuvent l'être individuellement selon le format d'origine — voir section "Modes bloqués par format d'origine" plus bas.
- **Traitement PIL** : `image_processing_qt.py::apply_adjustments()`, bloc `# ── Mode d'image ──` (après les niveaux, avant la profondeur de couleur)
- `_PIL_TO_MODE` (`image_mode_tool_qt.py`) fait correspondre chaque mode PIL à sa clé de radio, utilisé par le mécanisme de verrouillage (voir plus bas).

## Valeurs possibles (`settings['image_mode']`)

| Clé UI | Mode PIL cible | Remarque |
|---|---|---|
| `unchanged` (défaut, mais SANS radio équivalent dans la barre — voir écart ci-dessous) | aucun changement | |
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
Le cas générique (`else`) délègue directement à `img.convert(image_mode)` — **la clé UI doit donc être exactement un nom de mode PIL valide** pour tous les cas sauf `BW1`. Toute nouvelle option ajoutée à ce groupe de radios doit soit correspondre à un nom PIL exact (`RGB`, `L`, `CMYK`...), soit recevoir sa propre branche `if` dédiée comme `BW1`. L'exception est avalée silencieusement (`except Exception: pass`) — une conversion PIL impossible laisse l'image dans son mode précédent sans avertir l'utilisateur.

**Pas de radio "Ne pas modifier"/`unchanged`** dans `_ImageModeOptionsPanel`. Le radio verrouillé sur le mode PIL réel joue déjà ce rôle — exactement comme pour Profondeur de couleur, cliquer un mode déjà actif n'aurait aucun effet, il est donc simplement grisé plutôt que proposé comme un 8e choix redondant. La valeur `'unchanged'` reste acceptée par `apply_adjustments()` comme défaut neutre (no-op) mais n'a pas de radio équivalent.

## `for_preview` — reconversion pour affichage

N'a pas d'usage réel côté barre d'outils (pas de preview live pour cet outil, chaque clic commit directement avec `for_preview` implicite `False`). Le comportement du flag lui-même : ne montre jamais le mode PIL exact demandé pour `CMYK`, `P` ou `'1'` — toujours reconverti en RGB/RGBA/L affichable, `img.mode == '1'` passant par un buffer PNG intermédiaire.

## Interaction avec Profondeur de couleur

Ce bloc s'exécute **avant** celui de la profondeur de couleur (skill `adjust-color-depth`) dans `apply_adjustments()` — si les deux outils sont utilisés sur la même page dans la visionneuse (aucun garde-fou UI ne les rend mutuellement exclusifs), la profondeur de couleur a le dernier mot sur le mode PIL final. Exemple : un commit `image_mode='CMYK'` suivi d'un commit `color_depth='24'` → le résultat final est `RGB`, pas `CMYK`. Chacun via son propre outil de la barre, chacune sa propre entrée d'historique.

## Verrouillage des radios (`_ImageModeOptionsPanel.sync_to_page_state`)

Comme `_ColorDepthOptionsPanel` (skill `adjust-color-depth`), le panneau flottant **recalcule l'état des 8 radios à chaque resynchronisation** (`ImageModeViewerMixin._sync_image_mode_panel()`, appelée au changement de page, à la sélection de l'outil, après chaque commit/restauration, et après un undo/redo) — dérivé du mode PIL RÉEL de l'image affichée à l'instant T, pas d'une valeur mémorisée :

- **`locked_key`** (quel radio de mode apparaît coché+grisé) : `_PIL_TO_MODE.get(img.mode)` sur l'image courante — `RGB→'RGB'`, `RGBA→'RGBA'`, `L→'L'`, `LA→'LA'`, `CMYK→'CMYK'`, `'1'→'BW1'`, `P→'P'`. Contrairement à color_depth (où LA/CMYK n'ont pas d'équivalent parmi les 4 profondeurs), les 7 radios de ce panneau couvrent déjà tous les modes PIL rencontrables — `locked_key` ne devrait donc théoriquement jamais être `None` en pratique, mais le code reste défensif sur ce cas.
- **`has_original_saved`** (active "Restaurer l'original") : `self.current_idx in state.image_mode_original_bytes_by_page`.

Même piège `blockSignals` que `_ColorDepthOptionsPanel` : bloquer les signaux du `QButtonGroup` NE bloque PAS le signal `toggled` de chaque `QRadioButton` individuellement — `blockSignals(True)`/`False` doit être posé sur CHAQUE radio. Même garde `setExclusive(False)` temporaire que `_EffectsOptionsPanel` (skill `adjust-effects`) posée par cohérence/robustesse pour le cas `locked_key is None`, même si ce cas ne devrait normalement jamais se produire ici.

`_restore_radio` n'appartient PAS à `self._group` (le `QButtonGroup` des 7 modes), `setAutoExclusive(False)` posé une fois pour toutes à sa création — même raison que `_ColorDepthOptionsPanel` : ce n'est pas un choix de MODE parmi d'autres, c'est une action "annuler tout" séparée.

## Modes bloqués par format d'origine

Jamais de conversion silencieuse du format de fichier : un choix qui produirait une perte silencieuse à la sauvegarde reste impossible à sélectionner plutôt que dégradé après coup. `_BLOCKED_MODE_KEYS_BY_EXT` (`image_mode_tool_qt.py`) liste, par extension d'origine, l'ensemble des clés de mode à griser :

| Extension | Modes bloqués | Raison |
|---|---|---|
| `.jpg`/`.jpeg`/`.jfif`/`.pjpeg`/`.pjp` | `RGBA`, `LA`, `P`, `BW1` | JPEG ne supporte ni la transparence, ni la palette indexée, ni le 1-bit bilevel |
| `.gif` | `RGBA`, `LA`, `CMYK` | GIF n'a qu'une transparence binaire (pas de canal alpha réel) et ne supporte pas CMYK (Pillow lève une exception à la sauvegarde) |
| `.bmp` | `RGBA`, `LA` | Pillow écrit bien un canal alpha 32-bit en BMP, mais ne le redétecte pas à la relecture (header BMP classique ambigu sur la présence d'alpha) — transparence non fiable |

Même mécanisme que `adjust-color-depth` (même modèle de panneau) : `_sync_image_mode_panel()` calcule `blocked_keys`/`blocked_format_label` (extension réelle en majuscules, jamais un nom de format normalisé) à chaque resynchronisation ; `BlockableRadioButton.blocked` (`clone_tool_qt.py`) rejette le clic tout en gardant le radio `setEnabled(True)` (tooltip fonctionnel via `OverlayTooltip.track()` standard) ; property Qt `blocked` pour le style visuel ; `retranslate()` rejoue le tooltip via `_update_blocked_tooltips()`.

## Garde-fou de dernier recours — compression forcée en PNG

Comme pour la profondeur de couleur, si le mode résultant est `RGBA`/`LA`/`P`/`'1'` et le fichier d'origine est JPEG (`.jpg`/`.jpeg`/`.jfif`/`.pjpeg`/`.pjp`), l'extension de sauvegarde (et `entry['orig_name']`) sont mises à jour vers `.png` dans `apply_image_adjustments()`. Depuis le blocage UI ci-dessus, ce cas ne devrait normalement jamais se produire en pratique — filet de sécurité, un seul garde-fou partagé, voir skill `adjust-color-depth`.

## Snapshot "avant premier changement" — `state.image_mode_original_bytes_by_page`

Dict `{page_idx: bytes}` sur `state` (pas sur `ImageViewer`), même principe que `state.color_depth_original_bytes_by_page` (skill `adjust-color-depth`) : capturé au premier clic sur un mode pour une page donnée, jamais écrasé tant qu'il existe (un enchaînement RGB→CMYK→L garde le TOUT premier snapshot). **Survit au changement de page ET à un Ctrl+Z/Ctrl+Y** pendant que l'outil est actif — jamais réinitialisé par `navigate()`/`_refresh_after_undo_redo()`, seul un clic sur "Restaurer l'original" retire l'entrée pour cette page précise. Contrairement à Effets (`state.effect_key_by_page`), pas besoin d'un dict séparé pour la clé verrouillée : le mode PIL réel de l'image fait foi.

## Modifier cette fonction

Nouvelle option de mode → ajouter le radio dans `_ImageModeOptionsPanel.__init__` (`_mode_radios`, `_MODE_KEYS`, `_ROW_FOR_KEY` pour la répartition sur les 3 lignes), une entrée dans `_MODE_LABEL_KEYS` (+ clé de traduction `dialogs.adjustments.image_mode_xxx`, déjà existante et réutilisée pour les 7 modes actuels), et si la clé UI ne correspond pas exactement à un nom de mode PIL, une branche `if`/`elif` dédiée dans le bloc `apply_adjustments()` (`image_processing_qt.py`, suivre le modèle de `BW1`). Ajouter aussi l'entrée correspondante dans `_PIL_TO_MODE` si le nouveau mode doit bénéficier du verrouillage automatique. Pour bloquer un mode sur un nouveau format : ajouter/étendre une entrée dans `_BLOCKED_MODE_KEYS_BY_EXT` — vérifier d'abord (recherche web + test isolé hors app) ce que Pillow supporte réellement en écriture ET en relecture pour ce format avant de décider quoi bloquer.

## Références croisées

- `viewers` — barre d'outils de la visionneuse, orchestration transversale (auto-masquage, undo/redo unifié, forçage mode simple page, mécanisme des boutons Valider/Annuler — non utilisés par cet outil, chaque clic est déjà un commit complet).
- `adjust-color-depth` — réglage frère appliqué juste après, qui a le dernier mot en cas de cumul ; modèle de pattern (radios + commit immédiat + verrouillage dérivé du mode réel, blocage par format) sur lequel cet outil est cadré fidèlement.
- `adjust-effects` — cadrée sur le même modèle mais avec des écarts (pas de verrouillage dérivé de l'image, radio mémorisé par page).
- `apply-image-operation` — pattern undo/redo, forçage `.png` sur JPEG incompatible.
- `clone-zone` — héberge `BlockableRadioButton`, la classe partagée utilisée pour griser un radio tout en gardant son tooltip actif.
- `qt-tooltips` — `OverlayTooltip`, tooltip de l'icône `image_mode` de la barre et des radios bloqués par format.
