---
name: adjust-compression
description: Localiser ou modifier la fonction "Compression" (qualité JPEG) de MosaicView. Utiliser dès qu'une tâche touche à settings['compression_quality'], detect_jpeg_quality(), ou à l'outil compression de la barre d'outils de la visionneuse.
---

# Ajustement "Compression" — MosaicView

Outil de la barre d'outils flottante de la visionneuse principale. Pour l'orchestration générale de cette barre d'outils, voir skill `viewers`, section "Le cas de la compression". Seul chemin d'accès à cette fonction : l'icône "compression" de la barre d'outils de la visionneuse principale.

## Où

- UI : `compression_tool_qt.py` (`_CompressionOptionsPanel`, `CompressionCanvasMixin`, `CompressionViewerMixin`) — voir skill `viewers` pour le détail complet du panneau flottant, du grisage conditionnel de l'icône et de la mécanique preview/commit.
- Détection de qualité : `image_processing_qt.py::detect_jpeg_quality(image_bytes)`
- Traitement : `image_processing_qt.py::apply_adjustments()`, bloc `# ── Simulation compression JPEG ──` (tout dernier bloc de la fonction), précédé de la branche d'encodage natif WebP/AVIF (voir plus bas)
- Détection du format compressible : `compression_tool_qt.py::is_compressible_entry(entry)`/`COMPRESSIBLE_EXTENSIONS = (".jpg", ".jpeg", ".webp", ".avif")`

## Détection de la qualité initiale/de resynchronisation

`detect_jpeg_quality()` (`image_processing_qt.py`) retourne `None` si l'image n'est pas un JPEG (le curseur part alors de 100 pour un WebP ou un AVIF), sinon délègue à `entries.detect_jpeg_quality` : **une seule estimation dans l'application**, celle qui sert aussi au réencodage d'une page (comparaison de la première table de quantification aux tables standard de qualité 100 à 50, calculées une fois par session — skill `apply-image-operation`). Résultat borné à 50-100 : à ne réutiliser que pour positionner le curseur au premier affichage, jamais pour vérifier après coup une valeur qui vient d'être appliquée (voir piège ci-dessous). Testé par `tests/test_page_preservation.py` (`test_compression_tool_uses_the_same_jpeg_quality_estimate`).

Dans `compression_tool_qt.py`, cette fonction sert de valeur de **repli** dans `_reset_compression_preview()` : uniquement quand aucun commit de CET outil n'existe encore pour la page et le point d'historique courants (`state.tool_value_key`, skill `viewers`) (première ouverture de l'outil sur une page). Elle n'est jamais réutilisée ailleurs dans le pipeline d'application réelle.

**Piège** : ne jamais resynchroniser le slider après un commit en rappelant `detect_jpeg_quality(entry['bytes'])` sur l'image tout juste recompressée, dans l'idée d'afficher la qualité "réelle" plutôt que la valeur cible. L'estimation est bornée à 50-100 : une compression à qualité 1 ressortirait à 50, donnant l'impression trompeuse que le réglage n'a pas été appliqué. Le slider doit rester sur la valeur CIBLE qui vient d'être commitée (`panel.value`), jamais resynchronisé sur une redétection EXIF après coup.

## Application (`apply_adjustments()`)

La simulation de compression JPEG est le **dernier** traitement appliqué dans `apply_adjustments()`, après tous les autres réglages. Condition d'application :
```python
if comp_q < 100 and color_depth not in ('1', '32') and image_mode not in ('BW1', 'RGBA', 'LA', 'CMYK', 'P'):
```
Elle est **silencieusement ignorée** si la profondeur de couleur cible ou le mode d'image cible est incompatible avec JPEG — ne pas ajouter de message d'erreur ici, c'est le comportement voulu (les autres réglages de la même passe restent appliqués). Si l'image a un canal alpha (`RGBA`), un fond blanc est composé dessous avant compression (`Image.new('RGB', ..., (255,255,255))` + `paste(mask=alpha)`) car JPEG ne supporte pas la transparence — la couleur de fond `(255, 255, 255)` est codée en dur, pas configurable par l'utilisateur.

La compression est simulée en encodant réellement l'image en JPEG dans un buffer mémoire (`img.save(buf, format='JPEG', quality=comp_q, optimize=True)`) puis en la rouvrant (`Image.open(buf)`) — ce n'est donc pas une approximation visuelle, l'aperçu montre le résultat JPEG réel à ce niveau de qualité, y compris ses artefacts de compression.

### Pages WebP/AVIF : encodage natif, pas de simulation JPEG

Placé juste avant le bloc de simulation JPEG : si `settings['original_ext']` est `.webp` ou `.avif` (`_NATIVE_COMPRESSION_FORMATS`, `image_processing_qt.py`), l'image est encodée directement dans son propre format à `quality=comp_q` (convertie au préalable en RGB/RGBA si son mode n'est ni RGB, RGBA ni L), puis rouverte. Deux raisons : la simulation JPEG aplatirait l'alpha sur fond blanc, et un second encodage par `save_image_to_bytes` (qualité 95 pour ces formats) annulerait l'essentiel du gain de taille.

Les bytes produits sont transmis à `apply_image_adjustments()` via `img.info[ENCODED_BYTES_KEY]` (posé seulement hors preview), qui les reprend tels quels au lieu d'appeler `save_image_to_bytes` — et met à jour `img_width`/`img_height` et `is_animated_image` lui-même dans ce cas. **Piège** : `original_ext` doit être présent dans les settings, y compris pour le preview (`_update_compression_preview` le passe), sinon le preview retombe sur la simulation JPEG et ne correspond plus au résultat commité. 

### Pages JPEG : bytes de la simulation repris tels quels

Pour une page JPEG (et ses synonymes, `_JPEG_EXTS`), les bytes encodés à `comp_q` par la simulation sont aussi posés dans `img.info[ENCODED_BYTES_KEY]` (hors preview) et repris tels quels. **Piège** : réencodés par `save_image_to_bytes`, ils l'étaient à la qualité estimée de l'original (≈ 95) — deux pertes successives, pour un fichier environ deux fois plus lourd que celui demandé. Les deux encodages (natif WebP/AVIF et simulation JPEG) reportent profil ICC, DPI, EXIF, XMP et commentaire de l'image d'origine (`_kept_metadata`, sur `source_info`, le `img.info` relevé au début d'`apply_adjustments`, avant que les réglages ne le perdent) ; l'image étant ouverte par `open_page_image`, l'EXIF n'a déjà plus d'orientation. Garde-fou : `tests/test_page_preservation.py` (`test_jpeg_compression_writes_the_chosen_quality`).

## Icône grisée si non applicable

`is_compressible_entry(entry)` (`compression_tool_qt.py`) détermine si la page affichée est compressible : extension JPG/JPEG/WEBP/AVIF **et** image non animée (`image_ops.is_animated_entry` — la compression n'écrit qu'une image et aplatirait un WebP/AVIF animé). Si ce n'est pas le cas, l'icône de la barre d'outils est grisée/désactivée (`_ToolButton.set_enabled_state(False)`, `viewer_toolbar_qt.py`) et le tooltip affiche un texte explicatif différent (`viewer.toolbar_compression_disabled`, remplacé par `viewer.toolbar_disabled_animated_gif` sur une image animée) — piloté par `ImageViewer._refresh_compression_button_state()`, rappelée à chaque changement de page. Comme toutes les vérifications d'application de l'outil passent par `is_compressible_entry`, une image animée n'est jamais compressée. Voir skill `viewers`, section "Le cas de la compression" et « Grisage des outils sur une image animée », pour le détail du mécanisme de grisage.

## Modifier cette fonction

Pour changer le comportement de compression : modifier uniquement le bloc final de `apply_adjustments()`. Pour changer la détection de qualité : `detect_jpeg_quality()`. Ne pas dupliquer la logique de compression ailleurs — `apply_adjustments()` est la seule source de vérité, utilisée à l'identique par le preview live de l'outil et l'application réelle (`apply_image_adjustments()`).

## Références croisées

- `viewers` — outil "compression" de la barre d'outils flottante de la visionneuse principale (section "Le cas de la compression") : panneau flottant, preview live, commit, grisage conditionnel de l'icône, undo/redo.
- `zip-compression` — homonyme trompeur : concerne la compression **ZIP** du CBZ final (STORED/DEFLATED), un mécanisme totalement différent et sans rapport avec la qualité JPEG d'une image individuelle.
