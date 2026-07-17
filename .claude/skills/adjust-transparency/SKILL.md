---
name: adjust-transparency
description: Localiser ou modifier la fonction "Transparence" du panneau Ajustements d'image (rendre une couleur transparente par clic, flood fill ou global). Utiliser dès qu'une tâche touche au mode 'transparency' de AdjustmentViewerDialog ou à settings['transparency_type'/'transparency_tolerance'].
---

# Ajustement "Transparence" — MosaicView

Section du panneau Ajustements d'image (colonne gauche, dernière section) — **la seule section sans réglette dans le panneau principal lui-même** : tout le travail se fait exclusivement dans la visionneuse dédiée (bouton "Ajuster avec la visionneuse" obligatoire, pas de prévisualisation dans la vignette 300×300 du panneau). Pour l'orchestration générale du panneau, voir skill `adjustments-panel`.

## Où

- UI panneau : `adjustments_dialog_qt.py::_build_left_column()`, groupe `self._grp_transp` — juste un texte d'info + le bouton visionneuse, pas de contrôle de valeur
- Toute la logique : `adjustments_viewers_qt.py`, mode `'transparency'` de `AdjustmentViewerDialog`

## Formats supportés — double filtrage

1. **Panneau** : `_has_transparent` grise toute la section si aucune image sélectionnée n'a une extension `.png`/`.webp`/`.ico`/`.avif` (voir skill `adjustments-panel`).
2. **Viewer** (filtrage supplémentaire, plus strict, à l'ouverture du mode) :
```python
_SUPPORTED_EXTS = {'.png', '.webp', '.ico', '.avif'}
supported = [e for e in selected_entries if e.get('extension', '').lower() in _SUPPORTED_EXTS]
self._skipped_count = len(selected_entries) - len(supported)
self._selected_entries = supported
```
Si la sélection mélange des formats supportés et non supportés (ex. PNG + JPEG), le viewer **retire silencieusement** les entrées non supportées de sa propre liste de travail (`self._selected_entries` du viewer devient un sous-ensemble de celle du panneau) et affiche un bandeau rouge d'avertissement (`_warning_lbl`, "N image(s) ignorée(s)"). Ne pas essayer de forcer une conversion RGBA implicite pour contourner ce filtre — JPEG en particulier ne peut structurellement pas stocker de canal alpha.

## Deux types de sélection (flood fill vs global)

`settings['transparency_type']` (`'flood'` ou `'global'`), réglé par un slider bascule 2 positions (`_transp_type_slider`, range 0-1, 0=flood, 1=global) — pas une checkbox, un mini-slider avec deux labels dont l'inactif est grisé (`_update_transp_type_labels`).

`settings['transparency_tolerance']` (0-255, défaut 30) : distance de couleur max acceptée, testée en Chebyshev (`max(abs(Δr), abs(Δg), abs(Δb))`), pas en distance euclidienne — un slider **et** un `QSpinBox` synchronisés bidirectionnellement (`_on_transp_tol_changed`/`_on_transp_tol_spin_changed`, chacun bloque les signaux de l'autre le temps de la synchro pour éviter une boucle infinie).

### `_apply_transparency_click(px, py)` — le cœur de la logique

```python
ref = img.getpixel((px, py))
if ref[3] == 0: return   # déjà transparent, rien à faire
```

- **`flood`** : parcours en pile (flood fill classique 4-connexe, pas récursif — évite un dépassement de pile Python sur de grandes zones) à partir du pixel cliqué, propageant tant que la couleur reste dans la tolérance de la couleur de référence (celle du pixel cliqué, pas une moyenne). Chaque pixel accepté voit son alpha mis à `0` (`pixels[cx, cy] = (r, g, b, 0)`) — **les canaux RGB ne sont jamais modifiés**, seul l'alpha change, donc la couleur d'origine reste techniquement présente sous la transparence.
- **`global`** : double boucle sur **toute** l'image (`for y: for x:`), rend transparent tout pixel non déjà transparent dont la couleur est dans la tolérance de la couleur de référence — indépendamment de sa position, pas de propagation depuis le point cliqué. Coûteux sur une grande image (boucle Python pixel par pixel, pas vectorisé numpy) — voir le piège de performance documenté dans le skill `viewers` pour `_make_checkerboard_pil`, même famille de risque si une image de très grande taille est traitée en mode `global`.

## Image de travail par page, undo/redo local

`_transp_work_img` (PIL RGBA de la page courante) est initialisée **une seule fois** au premier affichage de chaque page (`if self._transp_work_img is None: self._transp_work_img = original.convert('RGBA')`) — les clics successifs mutent cette même image en mémoire, jamais l'entrée réelle tant que "Appliquer" n'a pas été cliqué. Navigation ◀/▶ entre pages : `_save_transp_state`/`_restore_transp_state` (dicts `_transp_work_imgs`/`_transp_histories`/`_transp_redo_stks` indexés par `_current_idx`) — chaque page garde son propre historique de clics tant que le viewer reste ouvert.

Chaque clic pousse une copie complète de l'image de travail dans `_transp_history` avant modification (`self._transp_history.append(self._transp_work_img.copy())`) — coûteux en mémoire sur de grandes images avec beaucoup de clics successifs, mais c'est le choix actuel (pas de limite de profondeur d'historique contrairement à l'undo/redo principal de l'appli qui a `MAX_HISTORY=20`, voir skill `undo-redo`).

## Confirmation de fermeture — flux propre à ce mode

Seul mode d'ajustement avec une boîte de confirmation à 3 boutons avant fermeture (`_TransparencyUnsavedDialog`) si des clics ont été faits sans avoir cliqué "Appliquer" (`_has_unapplied_transparency()` — vérifie qu'au moins une page a un historique non vide). Interceptée à la fois sur `_cancel()` (bouton Annuler / Échap) et `closeEvent` (croix de la fenêtre) via `_confirmed_close` (flag anti-double-confirmation). Les 3 boutons : Fermer sans appliquer (`_on_discard`, vide simplement les historiques et ferme), Appliquer et fermer (`_on_apply` → `_apply_transparency()`), Annuler (referme juste la boîte, garde le viewer ouvert).

## Application (`_apply_transparency`)

Contrairement aux autres modes (qui passent par `apply_image_adjustments()` générique dans `adjustments_processing_qt.py`), la transparence a son **propre chemin d'application**, directement dans `adjustments_viewers_qt.py::_apply_transparency()` — car elle manipule des pixels déjà modifiés en mémoire (`_transp_work_imgs`), pas des paramètres numériques à passer à un pipeline. Applique **toutes les pages qui ont une image de travail**, pas seulement la page courante :
```python
for idx, work_img in self._transp_work_imgs.items():
    ...
    if save_state: save_state()
    ext = entry.get('extension', '').lower()
    fmt = 'ICO' if ext == '.ico' else 'WEBP' if ext == '.webp' else 'AVIF' if ext == '.avif' else 'PNG'
    img_to_save.save(output, format=fmt)
    entry['bytes'] = output.getvalue()
    ...
```
Suit le pattern d'invalidation de caches standard (voir skill `apply-image-operation`) mais **appelle `save_state()` à chaque page individuellement à l'intérieur de la boucle**, pas une seule fois avant/après comme le fait le panneau principal pour les autres réglages en lot — vérifier ce détail avant de "corriger" cette différence en la rapprochant du pattern multi-image du panneau (voir skill `adjustments-panel`), ce n'est pas une incohérence accidentelle mais un choix déjà en place à confirmer avec l'utilisateur avant de le changer.

## Modifier cette fonction

Logique de flood fill / global / tolérance → `_apply_transparency_click` dans `adjustments_viewers_qt.py`. Formats supportés → `_SUPPORTED_EXTS` (dupliqué nulle part ailleurs, une seule constante). Format de sauvegarde par extension → `_apply_transparency`.

## Références croisées

- `adjustments-panel` — section grisée si non applicable, bouton "Ajuster avec la visionneuse".
- `viewers` — vue d'ensemble des 8 modes, notamment le curseur croix custom (`_get_crosshair_cursor`) spécifique à ce mode.
- `apply-image-operation` — pattern d'invalidation de caches suivi par `_apply_transparency`.
- `undo-redo` — historique global de l'application, distinct de l'historique local par page de ce mode.
