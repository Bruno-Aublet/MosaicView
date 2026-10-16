---
name: apply-image-operation
description: Écrire ou modifier une fonction qui change les bytes d'une image de la mosaïque (crop, resize, rotation, conversion, ajustements...). Utiliser dès qu'une tâche touche à entry['bytes'] dans images_data, pour ne pas casser l'undo/redo ni les caches de vignettes.
---

# Appliquer une opération image (mosaïque + undo/redo) — MosaicView

Quand une fonction modifie `entry['bytes']` dans `images_data`, il faut impérativement suivre ce pattern en 5 étapes. Voir skill `undo-redo` pour le fonctionnement interne du mécanisme d'historique lui-même (snapshots, `_original_id`, pourquoi `entry["bytes"]` ne doit jamais être muté en place) — ce skill-ci se concentre sur le contrat à respecter côté appelant.

## 1. Sauvegarder l'état AVANT modification (undo)
```python
if save_state:
    save_state(force=True)
```
`force=True` est obligatoire — sans ça, si l'état courant est déjà dans l'historique (mêmes bytes), `save_state_data` retourne `False` et ne sauvegarde rien.

## 2. Modifier les bytes ET invalider tous les caches vignettes

**Il n'existe pas de fonction utilitaire centralisée** — chaque module invalide les caches inline. Deux variantes existent dans le code actuel, préférer la variante complète (A) pour toute nouvelle fonction :

**A. Invalidation complète** (`image_processing_qt.py`, `crop_tool_qt.py`, `straighten_tool_qt.py`, `clone_tool_qt.py`, `text_tool_qt.py`) :
```python
entry['bytes'] = <nouveaux bytes>
entry['img'] = None
entry['_thumbnail'] = None
entry['large_thumb_pil'] = None   # cache PIL
entry['qt_pixmap_large'] = None   # cache Qt — CRITIQUE pour render_mosaic
entry['qt_qimage_large'] = None   # cache Qt intermédiaire
entry['_hash'] = None             # cache détection de doublons
```

**B. Invalidation partielle** (`image_transforms_qt.py::_regenerate_thumbnail_qt(entry)`, utilisée après rotation/flip) : n'invalide que `qt_pixmap_large`, `qt_qimage_large`, `_hash` — `entry['img']` y est réassigné directement à la nouvelle image (pas mis à `None`) par l'appelant (`rotate_entry_data`/`flip_entry_data` dans `image_ops.py`), et `_thumbnail` n'y est pas touché. Ne pas copier cette variante pour une nouvelle fonction sans vérifier qu'elle correspond bien à ton cas — préférer (A), plus sûre.

Sans invalider `qt_pixmap_large`, `render_mosaic` réaffiche l'ancienne vignette.

Si une fonctionnalité ajoute un autre cache dérivé de `entry['bytes']`, l'invalider ici aussi dans la même liste.

### Décoder la page : toujours dans le sens d'affichage

Une page à afficher, mesurer ou modifier se décode par `ensure_image_loaded(entry)` ou `open_page_image(entry['bytes'])` (`entries.py`), jamais par un `Image.open(io.BytesIO(entry['bytes']))` direct : ces deux fonctions appliquent l'orientation EXIF (`oriented`, `ImageOps.exif_transpose`), comme les autres lecteurs. Ses dimensions sans décodage : `page_size(data)` / `oriented_size(img)`. **Piège** : `save_image_to_bytes` réécrit l'EXIF **sans** l'orientation (les pixels sont censés être déjà tournés) ; une page décodée sans `oriented`, puis réenregistrée, serait tournée d'un quart de tour dans les lecteurs qui respectent l'EXIF. Les images à plusieurs images (animations, TIFF multipage) ne sont pas tournées ; un JPEG MPO (photo de téléphone) l'est (`_has_several_images`).

**Niveaux de gris 16 bits, entiers 32 bits, flottants** (`I;16*`, `I`, `F`) : un `convert('RGB'/'RGBA'/'L')` direct rend blanc tout ce qui dépasse 255. Pour l'écran et l'impression, passer par `to_screen_mode(img)` : `to_display_mode` (256 nuances, via `image_ops._to_writable_mode`) plus une page CMJN convertie en RVB par son profil ICC (`image_ops.cmyk_to_rgb` ; sans profil, couleurs fausses à l'écran) — déjà fait par `create_centered_thumbnail`, les `_pil_to_qimage`/`_pil_to_qpixmap` des vignettes et de la visionneuse, l'aperçu de la renumérotation et l'impression. **Jamais `to_screen_mode` pour une image enregistrée dans le comic** : les outils de dessin, la jointure et le GIF animé gardent `to_display_mode`, et une page CMJN enregistrée reste en CMJN ; `apply_adjustments` (`image_processing_qt.py`) le fait aussi avant tout réglage. Les outils de dessin de la visionneuse (texte, formes, collage d'image, clonage, flou) prennent leur image de travail par `drawing_base_image(data)` et passent leur résultat par `drawn_page_image(data, drawn)` avant `save_image_to_bytes` : **la page retrouve son mode d'origine**, et un dessin en couleur devient gris (ou noir et blanc) sur une page qui n'est pas en couleur. Gris 16 bits : seuls les pixels changés par le dessin prennent sa valeur (en gris, 256 nuances), le reste garde sa précision ; niveaux de gris (`L`) : dessin aplati sur blanc puis en gris ; 1 bit : idem, puis seuil à mi-gris (128, comme l'outil Mode d'image) ; `LA` : en `LA` ; palette (`P`, `PA`) hors GIF : palette de 256 couleurs recalculée, transparence comprise (un GIF garde l'image RGBA, que son enregistrement quantifie lui-même) ; toute autre page (RGB, RGBA, CMJN) : telle quelle. Sans ce retour, toute page passait en RGBA : un JPEG en niveaux de gris devenait un JPEG couleur, un PNG 1 bit une image 32 bits, bien plus lourds. Passer la page en couleur est le rôle de l'outil Mode d'image (skill `adjust-image-mode`). Testé par `tests/test_page_preservation.py`.

### Encoder les nouveaux bytes : toujours `save_image_to_bytes(entry)`

Pour produire `entry['bytes']` à partir d'une image PIL modifiée, poser `entry['img'] = <image>` puis appeler `save_image_to_bytes(entry)` (`entries.py`) — **jamais un `img.save(...)` local avec un format déduit du nom de fichier**. Cette fonction choisit le format d'après `entry['extension']` (JPEG et synonymes JFIF/PJPEG/PJP, PNG, WebP, AVIF, GIF, BMP, TIFF, ICO avec ses tailles d'origine), réapplique le DPI et la qualité JPEG estimée, gère les modes qu'un format ne sait pas écrire (LA/CMYK pour BMP/ICO, alpha aplati sur blanc pour JPEG), et met à jour `img_width`/`img_height` (lus par la renumérotation et la détection des pages multiples) ainsi que `is_animated_image` (remis à `False` : une seule image est écrite). Elle lit `entry['bytes']` d'origine (qualité JPEG, tailles ICO, sous-échantillonnage JPEG via `_source_encoding_info`, profil ICC, métadonnées ; `_webp_is_lossless` pour garder un WebP sans perte) : l'appeler **avant** de remplacer ces bytes.

- **Résolution** : `entries.page_dpi(entry, img)` donne le couple `(x, y)` à écrire. `entry["dpi"]` ne garde que la valeur horizontale (affichée, changée par le redimensionnement) : la résolution de l'image est reprise telle quelle tant que sa valeur horizontale est celle-là, sinon la nouvelle valeur vaut dans les deux sens. Sans cela, une page à 300 × 600 dpi était réécrite à 300 × 300. Aussi utilisée par `image_ops.convert_image_data`.
- **Compression TIFF** : `entries.tiff_save_compression(img, data, default="raw")` donne la compression à passer **toujours** à Pillow, `"raw"` compris — sans valeur passée, Pillow reprend celle de `img.info`, même incompatible avec le mode modifié. Compression de l'image, sinon des bytes d'origine ; remplacée par LZW (sans perte, lue partout) si elle ne convient pas au mode écrit, pour un TIFF JPEG (Pillow le réencoderait à qualité 75) et pour toute compression exotique. **Piège** : Pillow ne refuse pas toujours une combinaison incompatible, il peut faire planter le programme (CCITT Group 3/4 hors image 1 bit, JPEG sur une image 1 bit, à palette ou 16 bits — vérifié : code de sortie 139). Utilisée par `save_image_to_bytes`, `convert_image_data` (`default="tiff_lzw"` : une conversion vers TIFF est compressée) et `image_ops.transform_multipage_tiff`.
- **Qualité JPEG estimée** : `detect_jpeg_quality` compare les tables de quantification du fichier à des tables de référence calculées une seule fois par session (`_jpeg_quality_tables`), et non plus à chaque appel (51 encodages à chaque enregistrement).
- **Rotation d'un angle quelconque** : `entries.rotate_page_image(img, angle, fillcolor=None)` (interpolation bicubique, page agrandie, coins remplis de blanc ou de noir). **Piège** : Pillow n'interpole pas une image 16 bits (`I;16*`) et l'écrase (valeurs plafonnées à 255, page presque noire) — elle est tournée en entiers 32 bits puis ramenée en 16 bits. Utilisée par le redressement manuel et automatique (skill `page-straighten`).
- **Profondeur au-delà de 8 bits** : `entries.page_high_depth(data)` rend `"gray16"` (niveaux de gris 16 bits, que Pillow lit tels quels : gardés par la conversion, les outils de dessin, le recadrage, la rotation et le redressement, réduits à 8 bits par les réglages), `"color16"` (PNG ou TIFF à 16 bits par couleur, que Pillow lit déjà en 8 bits : réduits par toute modification) ou None, d'après l'en-tête seul. Sert à l'avertissement de la visionneuse (skill `viewers`).
- **Profil ICC** : `page_icc_profile(img, data, mode=None)`, règle commune à tous les formats et à tous les encodeurs (conversion, découpe, jointure, lots) — profil de `img.info`, sinon des bytes d'origine (perdu par toute recomposition : `ImageEnhance`, `Image.new`), retenu seulement si son espace couleur correspond au mode écrit (`_icc_for_mode`, `_ICC_SPACE_BY_MODE`). **Piège** : pour PNG, TIFF et AVIF, toujours passer `icc_profile=` à `save()`, même `None` : sans l'argument, Pillow reprend celui de `img.info`, qu'il conserve à travers `convert()` (profil RGB écrit dans une image en niveaux de gris, profil CMYK sur une image passée en RGB).
- **Métadonnées** : `metadata_save_kwargs(fmt, data)` (sur `page_metadata`) — EXIF pour JPEG, WebP, AVIF et PNG ; XMP pour JPEG, WebP et AVIF ; commentaire pour JPEG. EXIF et XMP **sans l'orientation** (Pillow relit aussi l'orientation dans le XMP : retirée des deux), sauf `keep_orientation=True` pour des pixels réencodés sans avoir été tournés (normalisation des pages des lots, `batch_dialogs_qt._normalized_page_bytes`). **Piège** : jamais d'EXIF lu dans un TIFF ni écrit dans un TIFF — `getexif()` d'un TIFF rend les balises de structure du fichier (largeur, hauteur, emplacement des données), qui réécrites abîmaient l'image (dimensions d'avant une rotation).
- **TIFF** : la transparence d'une palette (`P` + `transparency`) n'est pas écrite par Pillow en TIFF ; l'image passe en RGBA.
Garde-fous : `tests/test_image_formats.py`, `tests/test_page_preservation.py`. Une liste locale de formats retombant sur JPEG a déjà transformé des pages TIFF/AVIF/ICO en JPEG sous leur extension d'origine (skill `page-resize`).

**Image animée** (`image_ops.is_animated_entry` : `is_animated_gif` ou `is_animated_image`, jamais l'un des deux seul) : `save_image_to_bytes` n'écrit qu'une image ; une opération géométrique doit passer par `transform_animated_entry(entry, frame_fn)` (`image_ops.py`, skill `rotate-flip`), qui garde toutes les frames dans le format d'origine, ou être refusée/grisée sur ces pages (skill `viewers`).

**TIFF de plusieurs pages** (`entry["is_multipage_tiff"]`, posé par `entries.set_animated_image_info`) : même contrainte, sans être une image animée (pas de lecture animée, pas d'affichage seul en double page, pas d'avertissement « image animée » à l'enregistrement). **Tester `image_ops.has_several_frames(entry)`** (image animée OU TIFF multipage) avant toute opération qui réécrit l'image : `transform_animated_entry` traite alors chaque page (`transform_multipage_tiff`, compression et DPI conservés), sinon l'opération est refusée/grisée. `is_animated_entry` reste réservé à ce qui concerne vraiment l'animation (lecture, avertissement, jointure animée). Testé par `tests/test_image_frames_and_modes.py`. **En pratique, ce cas est devenu un filet de sécurité** : un TIFF multipage lu dans une archive est découpé en une page du comic par page dès l'ouverture (`entries.expand_multipage_tiffs`, décision définitive documentée dans `CLAUDE.md` et le skill `archive-image-loading`) ; seul un TIFF impossible à découper garde une entrée `is_multipage_tiff`. Garder ce traitement page par page pour ce cas, sans s'en servir comme argument pour revenir à une seule vignette.

## 3. Marquer le fichier comme modifié
```python
state.modified = True
```
Sans ça, fermer le fichier ne déclenche pas le message d'avertissement.

## 4. Sauvegarder l'état APRÈS modification (redo)
```python
if save_state:
    save_state(force=True)
```
Sans ce deuxième appel, le redo ne peut pas restaurer l'état modifié.

## 5. Appeler render_mosaic
```python
if render:
    render()
```

## Callbacks attendus
- `save_state` : `MainWindow.save_state(force=False)` — supporte `force=True`.
- `render_mosaic` : `MosaicCanvas.render_mosaic()`.

## Pourquoi ce pattern exact

Ce pattern exact évite plusieurs bugs récurrents : mosaïque ne se mettant pas à jour (`qt_pixmap_large` non invalidé), undo/redo impossible (`save_state` sans `force=True` ignoré), message de fermeture absent (`state.modified` non mis à `True`).

**Toute nouvelle fonction qui modifie des bytes d'images (crop, resize, flip, conversion, etc.) doit suivre ce pattern exact — ne pas improviser une variante.**

## Références croisées

- `viewers` — appelant principal de la variante (A) via `apply_image_adjustments()` (`image_processing_qt.py`), utilisée par chaque outil d'ajustement de la barre d'outils de la visionneuse (sharpness, brightness, saturation, remove_colors, compression, levels, color_depth, effects, image_mode) sur la page affichée, un commit par geste.
- `adjust-transparency` — seul outil de la barre à appliquer ses propres bytes en dehors de `apply_image_adjustments()` (chemin dédié `perform_transparency()`), mais qui suit le même pattern d'invalidation de caches.
- `rotate-flip` — exemple de la variante (B) d'invalidation partielle : `rotate_entry_data`/`flip_entry_data` (`image_ops.py`) n'invalident que `large_thumb_pil`/`_hash`, `qt_pixmap_large`/`qt_qimage_large` étant invalidés séparément par le worker qui orchestre le traitement par lot ; pas de `force=True` explicite sur son `save_state()` initial non plus, contrairement au pattern recommandé ici.
- `page-straighten` — exemple de la variante (A) complète (contrairement à `rotate-flip`), avec en plus un second historique undo/redo interne à la fenêtre qui s'empile par-dessus le `save_state`/`force=True` standard documenté ici.
- `add-text-to-image` — même variante (A) complète, avec un troisième niveau d'historique (undo de frappe Qt natif par bloc) en plus des deux niveaux de `page-straighten`.
- `page-resize` — variante intermédiaire propre à ce fichier : invalidation de cache proche de (B) mais avec un précalcul explicite de la vignette Qt dans le thread worker (`build_qimage_for_entry`), et une annulation en cours de lot qui restaure les bytes manuellement plutôt que via `rollback_to_current_state_qt`.
- `page-crop` — variante (A) complète, avec la même optimisation `build_qimage_for_entry` que `page-resize` mais exécutée en synchrone (pas de worker QThread) ; les deux appels à `save_state` omettent `force=True`, contrairement au pattern recommandé ici.
- `clone-zone` — le clonage (3e outil migré dans la visionneuse principale) suit un pattern distinct : pas de bouton "Valider", chaque coup de pinceau (stroke) relâché est directement `save_state()`/`save_state(force=True)` et devient sa propre entrée d'historique — pas d'unité d'undo "par point peint" ni d'historique interne séparé comme l'avait l'ancienne fenêtre dédiée (supprimée).
- `create-ico` — ne suit pas ce pattern du tout : aucun `save_state` avant modification, entrée construite manuellement plutôt que via `create_entry()`, car il s'agit d'un **ajout** de nouvelle entrée (le `.ico` généré) plutôt que d'une modification de `entry['bytes']` existant.
- `animated-gif` — même famille de cas hors pattern : ajout d'une nouvelle entrée (le GIF assemblé), un seul `save_state` après, mais réutilise `create_entry()` contrairement à `create-ico`.
- `nfo-editor` — hors périmètre direct (pas d'image, pas de cache vignette) mais utile en contraste : la mutation en place de `entry['bytes']` y est correcte pour du texte, contrairement à la règle stricte documentée ici pour les images.
