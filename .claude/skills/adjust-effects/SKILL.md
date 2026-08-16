---
name: adjust-effects
description: Localiser ou modifier la fonction "Effets" (Noir et blanc / Sépia / Inversion de couleurs), migrée dans la barre d'outils de la visionneuse principale (effects_tool_qt.py). Utiliser dès qu'une tâche touche à settings['effect'], effects_tool_qt.py, ou aux radios d'effets de la barre d'outils.
---

# Ajustement "Effets" — MosaicView

**Migré dans la barre d'outils de la visionneuse principale le 2026-08-16** (15e outil, 2e des 3 dernières fonctions du panneau Ajustements classique — idees.txt #3, skill `viewers`). Ce skill couvre la logique PIL (inchangée) et l'intégration dans la barre (nouvelle) — pour l'orchestration générale de la barre (auto-masquage, undo/redo unifié, forçage mode simple page), voir skill `viewers`.

**Cadré explicitement sur le même modèle que la profondeur de couleur** (skill `adjust-color-depth`, décision utilisateur 2026-08-16) : groupe de `QRadioButton`, chaque clic commit IMMÉDIATEMENT (pas de preview, pas de relâchement à attendre), le radio choisi devient coché ET grisé (non re-cliquable), les autres restent cliquables pour changer directement vers un AUTRE effet. Un radio "Restaurer l'original" (`_restore_radio`) apparaît/s'active dès qu'au moins un changement a été fait, et restaure `entry['bytes']` à l'état d'avant le TOUT PREMIER changement de la session — nouveau commit, ne dépile pas l'historique.

**Écart assumé par rapport à la profondeur de couleur** : PAS de radio "Aucun effet" (contrairement à l'ancien panneau classique, qui en avait un) — décision explicite utilisateur, ça ferait doublon avec "Restaurer l'original" qui joue déjà ce rôle. Donc seulement **3 radios d'effet** (grayscale/sepia/invert) + Restaurer l'original, 4 radios au total. PAS de verrouillage dérivé du mode PIL réel de l'image non plus (contrairement à color_depth) : aucun des 3 effets ne laisse de trace détectable dans le mode PIL (grayscale/sepia restent en RGB visuellement gris/teinté, invert ne change pas le mode) — le radio verrouillé est donc mémorisé par page (`state.effect_key_by_page`), pas recalculé depuis l'image. PAS de phrase d'info "Cette page est actuellement en..." sous les radios non plus (rien d'équivalent à annoncer, puisqu'aucun mode PIL n'est concerné).

## Où

- **UI (barre d'outils)** : `modules/qt/effects_tool_qt.py` — `_EffectsOptionsPanel` (panneau flottant, 1 seule ligne de radios, libellés courts), `EffectsCanvasMixin` (vide, aucun geste souris/overlay), `EffectsViewerMixin` (`perform_effect(key)`, `perform_restore_effect()`, `_sync_effects_panel()`)
- **Icône de la barre** : `BTN_Effects.png`, `tool_id="effects"` dans `viewer_toolbar_qt.py::_ViewerToolbar.__init__` — pas de bi-mode, pas de grisage conditionnel selon le format
- **Traitement PIL (inchangé)** : `image_processing_qt.py::apply_adjustments()`, bloc `# ── Effets ──` — même moteur partagé qu'avant la migration, seul l'appelant a changé
- **Ancien emplacement (supprimé 2026-08-16)** : la section "Effets" de l'ancien panneau classique `AdjustmentsDialog`/`adjustments_dialog_qt.py` (`_grp_effects`, `_effect_radios`, `_on_effect_changed`) a été entièrement retirée — cette fenêtre a elle-même été supprimée en totalité le même jour, une fois cette migration et celle de Mode d'image terminées (voir skill `viewers`). Le radio `'none'`/"Aucun effet" qu'elle contenait n'a PAS de successeur dans le nouveau panneau (voir écart ci-dessus).

## Les 3 effets

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

Logique PIL strictement inchangée par la migration — `'none'` reste une valeur par défaut acceptée par `apply_adjustments()` (aucun traitement, no-op) mais n'a plus de radio équivalent côté barre d'outils, voir écart ci-dessus.

- **`grayscale`** (Noir et blanc) : `ImageOps.grayscale()` puis reconversion en `RGB` — **reste en mode RGB visuellement gris**, ne produit pas un mode PIL `'L'` réel (contrairement à Mode d'image → `L`, skill `adjust-image-mode`, qui lui change vraiment le mode). Effet purement visuel, pas une réduction de profondeur de couleur.
- **`sepia`** : d'abord désaturé en gris (même appel que `grayscale`), puis une matrice de conversion sépia standard (constantes `0.393/0.769/0.189` etc. — la matrice sépia classique la plus répandue, ne pas la modifier sans raison visuelle explicite) appliquée via `numpy` canal par canal, avec clampage `[0, 255]` sur chaque canal indépendamment.
- **`invert`** : **seul effet à préserver explicitement le canal alpha** — branche dédiée si `img.mode == 'RGBA'` qui inverse R/G/B mais recopie le canal A tel quel (`ImageOps.invert()` seul ne sait pas gérer RGBA nativement, d'où le split/merge manuel). Le cas générique (non-RGBA) convertit d'abord en RGB avant d'inverser, perdant toute transparence potentielle (ex. mode `LA` ou `P` avec transparence) — si un besoin de préserver l'alpha sur ces modes apparaît, suivre le même pattern split/merge que la branche RGBA plutôt que d'improviser.

## Ordre dans le pipeline

Ce bloc s'exécute **après** la netteté/netteté adaptative mais **avant** la suppression des couleurs, le seuil, les niveaux, le mode d'image et la profondeur de couleur — inchangé par la migration. Un effet Sépia suivi d'un réglage de Niveaux (point noir/blanc/gamma) s'applique donc sur l'image déjà teintée sépia, pas sur l'image couleur d'origine — comportement WYSIWYG cohérent, chaque outil de la barre appliquant son propre commit successivement sur `entry['bytes']`.

## Verrouillage des radios (`_EffectsOptionsPanel.sync_to_page_state`)

Contrairement à `_ColorDepthOptionsPanel` (skill `adjust-color-depth`), `locked_key` n'est **pas** dérivé du mode PIL réel de l'image — c'est une valeur mémorisée par page dans `state.effect_key_by_page` (`dict[int, str]`, clé = image_idx, valeur = dernier effet appliqué cette session ou absente si aucun changement). Recalculée à chaque resynchronisation (`EffectsViewerMixin._sync_effects_panel()`, appelée au changement de page, à la sélection de l'outil, après chaque commit/restauration, et après un undo/redo) :

- **`locked_key`** (quel radio d'effet apparaît coché+grisé) : `state.effect_key_by_page.get(self.current_idx)`, `None` si aucun changement n'a encore été fait sur cette page (aucun radio coché dans ce cas).
- **`has_original_saved`** (active "Restaurer l'original") : `self.current_idx in state.effect_original_bytes_by_page`.

**Piège corrigé (2026-08-16), propre à ce panneau** : quand `locked_key` redevient `None` (après "Restaurer l'original"), **aucun** des 3 radios d'effet ne doit rester coché. Un `QButtonGroup` exclusif refuse silencieusement de décocher un radio par `setChecked(False)` tant qu'aucun AUTRE bouton du groupe n'est coché à sa place — Qt n'autorise à changer l'état "coché" d'un groupe exclusif qu'en cochant un autre membre, jamais en décochant explicitement celui déjà coché. Sans traitement, le radio du dernier effet appliqué restait donc visuellement coché après une restauration, alors qu'aucun effet n'était plus en cours. Fix : `self._group.setExclusive(False)` posé temporairement le temps de la resynchronisation (`sync_to_page_state`), remis à `True` juste après — contrairement à `_ColorDepthOptionsPanel`, qui n'a jamais ce cas de figure puisqu'un radio de profondeur correspond toujours au mode PIL réel après un commit.

Même piège `blockSignals` que `_ColorDepthOptionsPanel` : bloquer les signaux du `QButtonGroup` NE bloque PAS le signal `toggled` de chaque `QRadioButton` individuellement — `blockSignals(True)`/`False` doit être posé sur CHAQUE radio, sinon un `setChecked()` pendant la resynchronisation redéclenche `perform_effect()`/`perform_restore_effect()` en boucle.

`_restore_radio` n'appartient PAS à `self._group` (le `QButtonGroup` des 3 effets), `setAutoExclusive(False)` posé une fois pour toutes à sa création — même raison que `_ColorDepthOptionsPanel` : ce n'est pas un choix d'EFFET parmi d'autres, c'est une action "annuler tout" séparée.

## Snapshot "avant premier changement" — `state.effect_original_bytes_by_page` et `state.effect_key_by_page`

Deux dicts sur `state` (pas sur `ImageViewer`), même principe que `state.color_depth_original_bytes_by_page` (skill `adjust-color-depth`) :

- **`effect_original_bytes_by_page`** (`dict[int, bytes]`) : bytes d'origine, capturés au premier clic sur un effet pour une page donnée, jamais écrasés tant qu'ils existent (un enchaînement grayscale→sepia garde le TOUT premier snapshot).
- **`effect_key_by_page`** (`dict[int, str]`) : dernier effet appliqué pour cette page — nécessaire ici car, contrairement à color_depth, l'effet appliqué n'est pas déductible du mode PIL de l'image après coup.

Les deux **survivent au changement de page ET à un Ctrl+Z/Ctrl+Y** pendant que l'outil est actif (même raison que color_depth : "sinon il y a un risque de confusion pour l'utilisateur") — jamais réinitialisés par `navigate()`/`_refresh_after_undo_redo()`, seul un clic sur "Restaurer l'original" retire les deux entrées pour cette page précise (`del`/`pop`).

## Modifier cette fonction

Formule d'un effet existant → bloc `# ── Effets ──` de `apply_adjustments()` (`image_processing_qt.py`, inchangé par la migration). Nouvel effet → ajouter le radio dans `_EffectsOptionsPanel.__init__` (`_effect_radios`, `_EFFECT_KEYS`), une entrée dans `_EFFECT_LABEL_KEYS` (+ clé de traduction `dialogs.adjustments.effect_xxx`, déjà existante et réutilisée pour les 3 effets actuels), et une branche `elif effect == '...'` dans `apply_adjustments()`.

## Références croisées

- `viewers` — barre d'outils de la visionneuse, orchestration transversale (auto-masquage, undo/redo unifié, forçage mode simple page, mécanisme des boutons Valider/Annuler — non utilisés par cet outil, chaque clic est déjà un commit complet).
- `adjust-color-depth` — le modèle de pattern (radios + commit immédiat) sur lequel cet outil est cadré, avec les écarts documentés ci-dessus.
- `adjust-image-mode` — l'autre réglage de mode migré le même jour, cadré lui plus fidèlement sur color_depth (verrouillage dérivé du mode PIL réel).
- `adjust-remove-colors` — traitement bien plus complexe visant un rendu manga/BD, à ne pas confondre avec l'effet simple "Noir et blanc" de cet outil.
- `adjust-saturation` — désaturation à -100 reste en RGB comme l'effet grayscale, mais via une formule `ImageEnhance.Color` différente et réglable en continu.
- `apply-image-operation` — pattern undo/redo respecté par `apply_image_adjustments()`.
- `qt-tooltips` — `OverlayTooltip`, tooltip de l'icône `effects` de la barre.
