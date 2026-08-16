---
name: adjust-compression
description: Localiser ou modifier la fonction "Compression" (qualité JPEG) de MosaicView. Utiliser dès qu'une tâche touche à settings['compression_quality'], detect_jpeg_quality(), ou à l'outil compression de la barre d'outils de la visionneuse.
---

# Ajustement "Compression" — MosaicView

Outil de la barre d'outils flottante de la visionneuse principale (10e outil migré, 6e des 8 modes d'ajustement, v1.7.5). Pour l'orchestration générale de cette barre d'outils, voir skill `viewers`, section "Le cas de la compression".

**Historique** : cette fonction vivait à l'origine dans le panneau Ajustements classique (section "Compression", colonne gauche) avec sa propre visionneuse dédiée (`AdjustmentViewerDialog` mode `'compression'`). Les deux ont été **entièrement retirées** le 2026-08-15 une fois la migration vers la barre d'outils validée — même principe déjà appliqué à sharpness/unsharp/brightness/saturation/remove_colors. Il n'existe donc plus qu'un seul chemin d'accès à cette fonction : l'icône "compression" de la barre d'outils de la visionneuse principale.

## Où

- UI : `compression_tool_qt.py` (`_CompressionOptionsPanel`, `CompressionCanvasMixin`, `CompressionViewerMixin`) — voir skill `viewers` pour le détail complet du panneau flottant, du grisage conditionnel de l'icône et de la mécanique preview/commit.
- Détection de qualité : `image_processing_qt.py::detect_jpeg_quality(image_bytes)`
- Traitement : `image_processing_qt.py::apply_adjustments()`, bloc `# ── Simulation compression JPEG ──` (tout dernier bloc de la fonction)
- Détection du format compressible : `compression_tool_qt.py::is_compressible_entry(entry)`/`COMPRESSIBLE_EXTENSIONS = (".jpg", ".jpeg", ".webp", ".avif")`

## Détection de la qualité initiale/de resynchronisation

`detect_jpeg_quality()` est appelée sur une entrée dont le format est JPEG (`img.format in ('JPEG', 'JPG')`), en lisant la moyenne de la première table de quantification EXIF (`img.quantization[0]`). Retourne `None` si l'image n'est pas un JPEG. Seuils de mapping table de quantification → qualité approximative : `<15→95`, `<25→85`, `<40→75`, `<60→60`, sinon `50` — **mapping volontairement grossier**, à ne réutiliser que pour positionner un curseur au premier affichage, jamais pour vérifier après coup une valeur qui vient d'être appliquée (voir piège ci-dessous).

Dans `compression_tool_qt.py`, cette fonction sert de valeur de **repli** dans `_reset_compression_preview()` : uniquement quand aucun commit de CET outil n'existe encore pour `(page, history_index)` courants (première ouverture de l'outil sur une page). Elle n'est jamais réutilisée ailleurs dans le pipeline d'application réelle.

**Piège vécu et corrigé (2026-08-15)** : un premier jet de `perform_compression()` (commit du slider) tentait de resynchroniser le slider après chaque commit en rappelant `detect_jpeg_quality(entry['bytes'])` sur l'image tout juste recompressée, dans l'idée d'afficher la qualité "réelle" plutôt qu'une valeur cible qui diverge après plusieurs recompressions. Mais le mapping à 5 paliers est bien trop grossier pour cet usage : une compression à qualité 1 retombe dans le premier seuil (`avg_q < 15`) et ressort à 95, donnant l'impression trompeuse que rien n'a été appliqué. Corrigé : le slider reste sur la valeur CIBLE qui vient d'être commitée (`panel.value`), jamais resynchronisé sur une redétection EXIF après coup.

## Application (`apply_adjustments()`)

La simulation de compression JPEG est le **dernier** traitement appliqué dans `apply_adjustments()`, après tous les autres réglages. Condition d'application :
```python
if comp_q < 100 and color_depth not in ('1', '32') and image_mode not in ('BW1', 'RGBA', 'LA', 'CMYK', 'P'):
```
Elle est **silencieusement ignorée** si la profondeur de couleur cible ou le mode d'image cible est incompatible avec JPEG — ne pas ajouter de message d'erreur ici, c'est le comportement voulu (les autres réglages de la même passe restent appliqués). Si l'image a un canal alpha (`RGBA`), un fond blanc est composé dessous avant compression (`Image.new('RGB', ..., (255,255,255))` + `paste(mask=alpha)`) car JPEG ne supporte pas la transparence — la couleur de fond `(255, 255, 255)` est codée en dur, pas configurable par l'utilisateur.

La compression est simulée en encodant réellement l'image en JPEG dans un buffer mémoire (`img.save(buf, format='JPEG', quality=comp_q, optimize=True)`) puis en la rouvrant (`Image.open(buf)`) — ce n'est donc pas une approximation visuelle, l'aperçu montre le résultat JPEG réel à ce niveau de qualité, y compris ses artefacts de compression.

## Icône grisée si non applicable

`is_compressible_entry(entry)` (`compression_tool_qt.py`) détermine si la page affichée est compressible (extension JPG/JPEG/WEBP/AVIF). Si ce n'est pas le cas, l'icône de la barre d'outils est grisée/désactivée (`_ToolButton.set_enabled_state(False)`, `viewer_toolbar_qt.py`) et le tooltip affiche un texte explicatif différent (`viewer.toolbar_compression_disabled`) — piloté par `ImageViewer._refresh_compression_button_state()`, rappelée à chaque changement de page. Voir skill `viewers`, section "Le cas de la compression", pour le détail complet du mécanisme de grisage (seul outil migré de la barre d'outils à en avoir un).

## Modifier cette fonction

Pour changer le comportement de compression : modifier uniquement le bloc final de `apply_adjustments()`. Pour changer la détection de qualité : `detect_jpeg_quality()`. Ne pas dupliquer la logique de compression ailleurs — `apply_adjustments()` est la seule source de vérité, utilisée à l'identique par le preview live de l'outil et l'application réelle (`apply_image_adjustments()`).

## Références croisées

- `viewers` — outil "compression" de la barre d'outils flottante de la visionneuse principale (section "Le cas de la compression") : panneau flottant, preview live, commit, grisage conditionnel de l'icône, undo/redo.
- `zip-compression` — homonyme trompeur : concerne la compression **ZIP** du CBZ final (STORED/DEFLATED), un mécanisme totalement différent et sans rapport avec la qualité JPEG d'une image individuelle.
