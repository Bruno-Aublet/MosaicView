---
name: adjust-transparency
description: Localiser ou modifier la logique de la fonction "Transparence" (rendre une couleur transparente par clic, zone/flood fill ou global) — outil "transparency" de la barre d'outils de la visionneuse principale. Utiliser dès qu'une tâche touche à transparency_tool_qt.py, à apply_transparency_click(), ou au panneau flottant flood/global/tolérance.
---

# Ajustement "Transparence" — MosaicView

Outil de la barre d'outils de la visionneuse principale. Seul point d'entrée : l'icône "Transparence" (`BTN_Transparency.png`) de la barre d'outils flottante de `ImageViewer`.

## Où

Tout le code (UI, geste souris, application) : `modules/qt/transparency_tool_qt.py` — mixins `TransparencyCanvasMixin`/`TransparencyViewerMixin`, panneau flottant `_TransparencyOptionsPanel`. Voir skill `viewers` pour l'intégration transversale (bouton "Valider"/"Annuler" partagés, undo/redo, blindage anti-fuite de clic).

## Formats supportés

`_SUPPORTED_EXTS = {'.png', '.webp', '.ico', '.avif', '.tiff', '.tif'}` (`is_transparency_supported_entry(entry)`) — un canal alpha réellement fiable à la relecture est requis. `is_transparency_supported_entry` renvoie aussi `False` pour toute image animée (`image_ops.is_animated_entry`) : l'outil n'écrit qu'une image et aplatirait l'animation ; le tooltip affiche alors `viewer.toolbar_disabled_animated_gif` (skill `viewers`, « Grisage des outils sur une image animée »). L'icône de la barre est grisée/désactivée (même mécanisme que `compression`, `_ToolButton.set_enabled_state`) quand la page affichée n'est pas dans un de ces formats ou est animée — `ImageViewer._refresh_transparency_button_state()`, appelée à l'ouverture de la visionneuse, à chaque changement de page, et après undo/redo. **BMP volontairement exclu** malgré une écriture RGBA techniquement possible en Pillow : le header BMP classique (`BITMAPINFOHEADER`, 40 octets) est ambigu sur la présence d'un canal alpha, donc Pillow (et la plupart des logiciels) ne le redétecte pas à la relecture — voir skill `adjust-color-depth`, section blocage par format, pour le détail.

## Panneau flottant — bascule zone/global + tolérance, PAS de pipette dédiée

Une seule ligne : label "Zone" (cliquable) + mini-slider bascule 2 positions + label "Global" (cliquable) | réglette + spin tolérance. Contrairement à `levels` (2 pipettes parmi plusieurs gestes possibles, donc un bouton pour "armer" chacune), **il n'y a qu'un seul geste possible dans cet outil** : le clic sur l'image. Pas de bouton pipette — le curseur pipette custom (même construction croix-de-visée-évidée que `levels`, voir skill `viewers`) est posé directement sur le canvas dès que l'outil est sélectionné dans la barre (`_TransparencyOptionsPanel.set_visible_for_tool`), retiré à la désélection.

`transparency_type` (`"flood"` ou `"global"`) : bascule via `QSlider` 2 positions **et** via un clic direct sur l'un des deux labels (`_ClickableLabel` — viser la petite poignée du slider seul est jugé peu pratique). `tolerance` (0-255, défaut 30) : slider + `QSpinBox` synchronisés bidirectionnellement, même pattern que les autres panneaux à réglette.

## `apply_transparency_click(px, py)` — le cœur de la logique (`TransparencyViewerMixin`)

```python
ref = pixels[px, py]
if ref[3] == 0: return   # déjà transparent, rien à faire
```

Le masque des pixels dans la tolérance (et pas déjà transparents) se calcule sur toute l'image d'un coup, avec numpy. **Jamais de parcours pixel par pixel en Python** : le clic se fait sur le thread de l'interface, et mémoriser chaque pixel visité montait à 2 Go pour 6 millions de pixels. Aujourd'hui, environ 1 seconde au plus sur cette taille.

- **`flood`** (zone) : `_connected_zone(matching, x, y)` (fonction module) garde la partie du masque reliée au pixel cliqué par les 4 voisins directs, en avançant par segments horizontaux entiers. Seul l'alpha est mis à `0`, RGB jamais modifié (`work_img.putalpha`, même objet image).
- **`global`** : tout le masque, indépendamment de la position.

Testé par `tests/test_transparency_click.py`, qui compare le résultat à un parcours pixel par pixel écrit dans le test.

Tolérance testée en distance de Chebyshev (`max(abs(Δr), abs(Δg), abs(Δb))`), identique à l'ancien mode.

## Image de travail par page — ACCUMULE plusieurs clics, contrairement à `levels`

**Différence architecturale avec les autres outils de la barre** : `levels` (et tous les modes preview-slider) commitent immédiatement à chaque geste, sans bouton "Valider". `transparency` fait l'inverse — choix délibéré, l'image de travail accumule les clics avant validation :

- `ImageViewer._transp_work_img_by_page` (`_PageWorkStore` : accès par position de page, rangement par clé de page, skill `viewers`) : image RGBA de travail par page, initialisée une seule fois au premier clic (`_get_or_init_transp_work_img`, `drawing_base_image(entry['bytes'])` : RGBA dans le sens d'affichage, 16 bits ramenés à 256 nuances, CMJN converti par son profil ; une page CMJN passe donc en RVB à la validation, une image transparente ne pouvant pas rester en CMJN), mutée en mémoire à chaque clic suivant — **jamais `entry['bytes']` directement** tant que "Valider" n'a pas été cliqué.
- `_update_transparency_preview()` affiche cette image de travail en réutilisant `self._sharpness_preview_img` (champ de preview partagé avec les 6 autres modes preview-slider, un seul outil actif à la fois).
- Persistance par page : navigation entre pages ne perd pas le travail en cours (`_restore_transparency_for_page` réaffiche l'image de travail mémorisée). **Piège d'ordre** : ce champ de preview étant partagé, les `_reset_*_preview` le vident ; `_show_image_position` et `_refresh_after_undo_redo` appellent donc `_restore_transparency_for_page` après eux, et `_ViewerToolbar.set_active_tool` le rappelle au retour sur l'outil, sinon la page s'afficherait sans la transparence alors que Valider l'appliquerait ; `_save_transparency_for_current_page` est un no-op délibéré (l'image de travail est déjà indexée par page, rien à extraire d'un état canvas comme pour crop).
- **Page modifiée entre-temps** : l'image de travail est une copie de la page, et la valider effacerait tout changement fait depuis le premier clic (rotation depuis la mosaïque, autre outil…). `_transp_base_bytes_by_page` retient les octets de la page au premier clic ; `_drop_transparency_work_if_page_changed(idx)` abandonne le travail, sans message, s'ils diffèrent de `entry['bytes']`. Appelée par `display_image` (page affichée), `perform_transparency` (rien n'est écrit) et, pour toutes les pages, `_forget_outdated_transparency_work`, depuis `_has_unvalidated_work` (pas de question à la fermeture pour un travail abandonné). Une opération annulée ensuite ne le fait pas revenir. Toute écriture de l'image de travail ou tout abandon retire aussi l'entrée de `_transp_base_bytes_by_page`. Testé par `tests/test_transparency_outdated_work.py`.
- Rejoint `_ALWAYS_VISIBLE_VALIDATE_TOOLS` (`image_viewer_qt.py`) : bouton "Valider" **et** son jumeau "Annuler" (voir skill `viewers`), tous deux toujours visibles tant que l'outil est actif, verts/rouges dès qu'il y a un travail en attente (`_validate_tool_has_work("transparency")` teste `current_idx in _transp_work_img_by_page`).

## Échap / Suppr / bouton "Annuler" — tout annuler d'un coup, PAS un undo clic par clic

`_clear_transparency_work()` (appelée par `_on_escape`, `_on_shape_delete_key`, et le bouton "Annuler" via `_cancel_tool_work("transparency")`) jette entièrement `_transp_work_img_by_page[current_idx]` — retour à l'image d'origine. Choix délibéré : **pas de pile d'annulation locale** — annuler un clic de trop implique de tout recommencer sur cette page, pas de revenir en arrière clic par clic.

## Piège — undo/redo global bloqué pendant un travail en attente

L'image de travail RGBA est une copie figée de `entry['bytes']` capturée au premier clic — un `Ctrl+Z`/`Ctrl+Y` (ou les boutons undo/redo de la barre) pendant qu'elle existe restaurerait `entry['bytes']` à un état antérieur **sous** cette copie figée, qui resterait périmée : valider ensuite écraserait silencieusement le undo. D'où le blocage d'Undo/Redo (no-op silencieux, pas de dialogue) tant qu'il reste du travail non validé sur n'importe quel outil à bouton "Valider" (`ImageViewer._block_undo_redo_for_unvalidated_work`, réutilise `_has_unvalidated_work()`) — pas spécifique à cet outil, généralisé à crop/straighten/text/shapes/transparency, voir skill `viewers`.

## Application (`perform_transparency`)

**Enregistrement impossible** : même chemin que le texte (skill `add-text-to-image`, « Enregistrement impossible ») ; l'image de travail reste en place pour réessayer.

Indépendant du pipeline générique de `image_processing_qt.py` — écrit directement l'image de travail dans `entry['bytes']` :
```python
entry["img"] = work_img.copy()
entry["bytes"] = save_image_to_bytes(entry)
```
Format de sauvegarde selon l'extension d'origine (`save_image_to_bytes`, `entries.py` — gère chaque format supporté par cet outil nativement : PNG/WEBP/AVIF/ICO/TIFF). Suit le pattern d'invalidation de caches standard (skill `apply-image-operation`) : `save_state()` avant, `save_state(force=True)` après, une seule entrée d'historique pour la page courante.

## Modifier cette fonction

Logique de flood fill / global / tolérance → `apply_transparency_click` dans `transparency_tool_qt.py`. Formats supportés → `_SUPPORTED_EXTS`/`is_transparency_supported_entry`. Format de sauvegarde par extension → `perform_transparency`.

## Références croisées

- `viewers` — vue d'ensemble de la barre d'outils, undo/redo unifié, bouton "Valider"/"Annuler" partagé, blindage anti-fuite de clic des panneaux flottants, curseur pipette (pattern repris tel quel de `levels`).
- `adjust-levels` — seul autre outil de la barre avec un vrai geste souris (pipette) ; diffère par le commit immédiat sans bouton "Valider", contrairement à celui-ci.
- `apply-image-operation` — pattern d'invalidation de caches suivi par `perform_transparency`.
- `undo-redo` — historique global de l'application ; ce mode y contribue une entrée par page validée.
