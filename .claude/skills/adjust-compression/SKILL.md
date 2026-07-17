---
name: adjust-compression
description: Localiser ou modifier la fonction "Compression" (qualité JPEG) du panneau Ajustements d'image. Utiliser dès qu'une tâche touche à settings['compression_quality'], detect_jpeg_quality(), ou au slider de qualité de compression.
---

# Ajustement "Compression" — MosaicView

Section réglette du panneau Ajustements d'image (colonne gauche, 2e section). Pour l'orchestration générale du panneau, voir skill `adjustments-panel`.

## Où

- UI : `adjustments_dialog_qt.py::_build_left_column()`, groupe `self._grp_comp` / `self._comp_slider` (range 1-100)
- Handler : `_on_comp_changed(val)` → `self._comp_quality = val` → `_update_preview()`
- Détection : `adjustments_processing_qt.py::detect_jpeg_quality(image_bytes)`
- Traitement : `adjustments_processing_qt.py::apply_adjustments()`, bloc `# ── Simulation compression JPEG ──` (tout dernier bloc de la fonction)
- Visionneuse dédiée : `AdjustmentViewerDialog` mode `'compression'` (voir skill `viewers`)

## Détection de la qualité initiale

À l'ouverture du panneau, `detect_jpeg_quality()` est appelée sur **chaque** entrée sélectionnée dont le format est JPEG (`img.format in ('JPEG', 'JPG')`), en lisant la moyenne de la première table de quantification EXIF (`img.quantization[0]`). Retourne `None` si l'image n'est pas un JPEG. La qualité initiale du slider (`initial_quality`) est la **médiane** des qualités détectées sur tout le lot (`jpeg_qualities.sort()` puis élément du milieu), ou `85` par défaut si aucune image compressible n'est sélectionnée. Seuils de mapping table de quantification → qualité approximative : `<15→95`, `<25→85`, `<40→75`, `<60→60`, sinon `50`.

Cette détection sert uniquement à **positionner le curseur** au démarrage (éviter de re-compresser une image déjà à qualité 60 en partant d'un défaut à 100) — elle n'est pas réutilisée ailleurs dans le pipeline d'application.

## Application (`apply_adjustments()`)

La simulation de compression JPEG est le **dernier** traitement appliqué dans `apply_adjustments()`, après tous les autres réglages. Condition d'application :
```python
if comp_q < 100 and color_depth not in ('1', '32') and image_mode not in ('BW1', 'RGBA', 'LA', 'CMYK', 'P'):
```
Elle est **silencieusement ignorée** si la profondeur de couleur cible ou le mode d'image cible est incompatible avec JPEG — ne pas ajouter de message d'erreur ici, c'est le comportement voulu (les autres réglages de la même passe restent appliqués). Si l'image a un canal alpha (`RGBA`), un fond blanc est composé dessous avant compression (`Image.new('RGB', ..., (255,255,255))` + `paste(mask=alpha)`) car JPEG ne supporte pas la transparence — la couleur de fond `(255, 255, 255)` est codée en dur, pas configurable par l'utilisateur.

La compression est simulée en encodant réellement l'image en JPEG dans un buffer mémoire (`img.save(buf, format='JPEG', quality=comp_q, optimize=True)`) puis en la rouvrant (`Image.open(buf)`) — ce n'est donc pas une approximation visuelle, l'aperçu montre le résultat JPEG réel à ce niveau de qualité, y compris ses artefacts de compression.

## Section grisée si non applicable

`_has_compressible` (calculé une fois à l'ouverture du panneau, sur l'extension de chaque entrée : `.jpg`/`.jpeg`/`.webp`/`.avif`) grise toute la section (slider, label, bouton visionneuse désactivés + titre en gris `#888888`) si aucune image sélectionnée n'a un format compressible. Voir skill `adjustments-panel` pour le mécanisme de grisage partagé avec la section Transparence.

## Modifier cette fonction

Pour changer le comportement de compression : modifier uniquement le bloc final de `apply_adjustments()`. Pour changer la détection de qualité initiale : `detect_jpeg_quality()`. Ne pas dupliquer la logique de compression ailleurs — `apply_adjustments()` est la seule source de vérité, utilisée à l'identique par l'aperçu du panneau, la visionneuse dédiée et l'application réelle.

## Références croisées

- `adjustments-panel` — structure générale, section grisée, `_get_settings()`.
- `viewers` — mode `'compression'` de `AdjustmentViewerDialog`.
- `zip-compression` — homonyme trompeur : concerne la compression **ZIP** du CBZ final (STORED/DEFLATED), un mécanisme totalement différent et sans rapport avec la qualité JPEG d'une image individuelle.
