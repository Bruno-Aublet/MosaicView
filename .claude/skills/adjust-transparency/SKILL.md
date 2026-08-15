---
name: adjust-transparency
description: Localiser ou modifier la logique de la fonction "Transparence" (rendre une couleur transparente par clic, zone/flood fill ou global) — outil "transparency" de la barre d'outils de la visionneuse principale. Utiliser dès qu'une tâche touche à transparency_tool_qt.py, à apply_transparency_click(), ou au panneau flottant flood/global/tolérance.
---

# Ajustement "Transparence" — MosaicView

**Migré dans la barre d'outils de la visionneuse principale (v1.7.5, 2026-08-15, 13e et dernier outil migré)** : ni le panneau Ajustements classique (ancienne section "Transparence", groupe `_grp_transp` — texte d'info + bouton "Ajuster avec la visionneuse"), ni le mode `'transparency'` de `AdjustmentViewerDialog` (`adjustments_viewers_qt.py`, désormais supprimé en totalité) n'existent plus. Seul point d'entrée désormais : l'icône "Transparence" (`BTN_Transparency.png`) de la barre d'outils flottante de `ImageViewer`. Cette migration a clos tout le chantier de fusion des visionneuses (idees.txt #3) : plus aucun mode d'ajustement ne reste hors de cette barre.

## Où

Tout le code (UI, geste souris, application) : `modules/qt/transparency_tool_qt.py` — mixins `TransparencyCanvasMixin`/`TransparencyViewerMixin`, panneau flottant `_TransparencyOptionsPanel`. Voir skill `viewers` pour l'intégration transversale (bouton "Valider"/"Annuler" partagés, undo/redo, blindage anti-fuite de clic).

## Formats supportés

`_SUPPORTED_EXTS = {'.png', '.webp', '.ico', '.avif'}` (`is_transparency_supported_entry(entry)`) — un canal alpha est requis, mêmes extensions que l'ancien mode. Contrairement à l'ancien filtrage à deux niveaux (panneau + viewer), il n'y a plus qu'un seul filtre : l'icône de la barre est grisée/désactivée (même mécanisme que `compression`, `_ToolButton.set_enabled_state`) quand la page affichée n'est pas dans un de ces 4 formats — `ImageViewer._refresh_transparency_button_state()`, appelée à l'ouverture de la visionneuse, à chaque changement de page, et après undo/redo.

## Panneau flottant — bascule zone/global + tolérance, PAS de pipette dédiée

Une seule ligne : label "Zone" (cliquable) + mini-slider bascule 2 positions + label "Global" (cliquable) | réglette + spin tolérance. Contrairement à `levels` (2 pipettes parmi plusieurs gestes possibles, donc un bouton pour "armer" chacune), **il n'y a qu'un seul geste possible dans cet outil** : le clic sur l'image. Pas de bouton pipette — le curseur pipette custom (même construction croix-de-visée-évidée que `levels`, voir skill `viewers`) est posé directement sur le canvas dès que l'outil est sélectionné dans la barre (`_TransparencyOptionsPanel.set_visible_for_tool`), retiré à la désélection.

`transparency_type` (`"flood"` ou `"global"`) : bascule via `QSlider` 2 positions **et** via un clic direct sur l'un des deux labels (`_ClickableLabel`, ajouté le 2026-08-15 après retour utilisateur — viser la petite poignée du slider seul était jugé peu pratique). `tolerance` (0-255, défaut 30) : slider + `QSpinBox` synchronisés bidirectionnellement, même pattern que les autres panneaux à réglette.

## `apply_transparency_click(px, py)` — le cœur de la logique (`TransparencyViewerMixin`)

```python
ref = pixels[px, py]
if ref[3] == 0: return   # déjà transparent, rien à faire
```

- **`flood`** (zone) : parcours en pile (flood fill 4-connexe, pas récursif) à partir du pixel cliqué, propageant tant que la couleur reste dans la tolérance de la couleur de référence. Seul l'alpha est mis à `0`, RGB jamais modifié.
- **`global`** : double boucle sur toute l'image, rend transparent tout pixel dont la couleur est dans la tolérance — indépendamment de sa position. Coûteux sur une grande image (boucle Python pixel par pixel, pas vectorisé) — même famille de risque que `_make_checkerboard_pil` (skill `viewers`).

Tolérance testée en distance de Chebyshev (`max(abs(Δr), abs(Δg), abs(Δb))`), identique à l'ancien mode.

## Image de travail par page — ACCUMULE plusieurs clics, contrairement à `levels`

**Différence architecturale majeure avec les 11 autres outils déjà migrés** : `levels` (et tous les modes preview-slider) commitent immédiatement à chaque geste, sans bouton "Valider". `transparency` fait l'inverse — décision explicite utilisateur (2026-08-15), reprend le comportement de l'ancienne image de travail par page :

- `ImageViewer._transp_work_img_by_page` (`dict[int, PIL.Image]`) : image RGBA de travail par page, initialisée une seule fois au premier clic (`_get_or_init_transp_work_img`, copie de `entry['bytes']` convertie en RGBA), mutée en mémoire à chaque clic suivant — **jamais `entry['bytes']` directement** tant que "Valider" n'a pas été cliqué.
- `_update_transparency_preview()` affiche cette image de travail en réutilisant `self._sharpness_preview_img` (champ de preview partagé avec les 6 autres modes preview-slider, un seul outil actif à la fois).
- Persistance par page : navigation entre pages ne perd pas le travail en cours (`_restore_transparency_for_page` réaffiche l'image de travail mémorisée) ; `_save_transparency_for_current_page` est un no-op délibéré (l'image de travail est déjà indexée par page, rien à extraire d'un état canvas comme pour crop).
- Rejoint `_ALWAYS_VISIBLE_VALIDATE_TOOLS` (`image_viewer_qt.py`) : bouton "Valider" **et** son jumeau "Annuler" (voir skill `viewers`), tous deux toujours visibles tant que l'outil est actif, verts/rouges dès qu'il y a un travail en attente (`_validate_tool_has_work("transparency")` teste `current_idx in _transp_work_img_by_page`).

## Échap / Suppr / bouton "Annuler" — tout annuler d'un coup, PAS un undo clic par clic

`_clear_transparency_work()` (appelée par `_on_escape`, `_on_shape_delete_key`, et le bouton "Annuler" via `_cancel_tool_work("transparency")`) jette entièrement `_transp_work_img_by_page[current_idx]` — retour à l'image d'origine. Décision explicite utilisateur (2026-08-15) : **pas de pile d'annulation locale** comme l'ancien `_transp_history`/`_transp_redo_stks` — annuler un clic de trop implique de tout recommencer sur cette page, pas de revenir en arrière clic par clic.

## Piège corrigé (2026-08-15) — undo/redo global bloqué pendant un travail en attente

L'image de travail RGBA est une copie figée de `entry['bytes']` capturée au premier clic — un `Ctrl+Z`/`Ctrl+Y` (ou les boutons undo/redo de la barre) pendant qu'elle existe restaurerait `entry['bytes']` à un état antérieur **sous** cette copie figée, qui resterait périmée : valider ensuite écraserait silencieusement le undo. Corrigé en bloquant Undo/Redo (no-op silencieux, pas de dialogue) tant qu'il reste du travail non validé sur n'importe quel outil à bouton "Valider" (`ImageViewer._block_undo_redo_for_unvalidated_work`, réutilise `_has_unvalidated_work()`) — pas spécifique à cet outil, généralisé aux 5 (crop/straighten/text/shapes/transparency), voir skill `viewers`.

## Application (`perform_transparency`)

Contrairement à l'ancien mode (chemin d'application séparé de `apply_image_adjustments()`), le nouveau reste dans la même veine mais reste indépendant du pipeline générique de `adjustments_processing_qt.py` — il écrit directement l'image de travail dans `entry['bytes']` :
```python
entry["img"] = work_img.copy()
entry["bytes"] = save_image_to_bytes(entry)
```
Format de sauvegarde selon l'extension d'origine (`save_image_to_bytes`, PNG/WEBP/AVIF/ICO). Suit le pattern d'invalidation de caches standard (skill `apply-image-operation`) : `save_state()` avant, `save_state(force=True)` après, une seule entrée d'historique pour la page courante — contrairement à l'ancien mode qui appliquait **toutes** les pages ayant une image de travail en une seule opération multi-page. Ce changement de granularité (une page à la fois, comme les autres outils de la barre) découle directement de la persistance par page désormais commune à tout le chantier.

## Modifier cette fonction

Logique de flood fill / global / tolérance → `apply_transparency_click` dans `transparency_tool_qt.py`. Formats supportés → `_SUPPORTED_EXTS`/`is_transparency_supported_entry`. Format de sauvegarde par extension → `perform_transparency`.

## Références croisées

- `viewers` — vue d'ensemble de la barre d'outils, undo/redo unifié, bouton "Valider"/"Annuler" partagé, blindage anti-fuite de clic des panneaux flottants, curseur pipette (pattern repris tel quel de `levels`).
- `adjustments-panel` — le panneau Ajustements classique restant (profondeur de couleur, effets, mode d'image) ; la section "Transparence" n'y existe plus.
- `adjust-levels` — seul autre outil migré avec un vrai geste souris (pipette) ; diffère par le commit immédiat sans bouton "Valider", contrairement à celui-ci.
- `apply-image-operation` — pattern d'invalidation de caches suivi par `perform_transparency`.
- `undo-redo` — historique global de l'application ; ce mode y contribue désormais une entrée par page validée, plus d'historique local séparé comme l'ancien viewer.
