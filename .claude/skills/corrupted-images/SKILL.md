---
name: corrupted-images
description: Localiser ou modifier la détection et le remplacement des images corrompues (is_corrupted/is_too_large au chargement, cadre rouge dans la mosaïque, menu remplacer/supprimer). Utiliser dès qu'une tâche touche à entry["is_corrupted"], corruption_reason, ou _replace_corrupted_image.
---

# Images corrompues — MosaicView

Détection automatique des images illisibles ou dont le décodage échoue au chargement d'une archive, avec signalement visuel dans la mosaïque et deux actions dédiées (remplacer par un fichier valide, ou supprimer la page). Distinct de la détection de **doublons** (skill `duplicate-detection`, pages identiques mais valides) — une entrée corrompue est explicitement **exclue** du calcul de hash de doublons, les deux mécanismes ne se chevauchent jamais sur la même entrée.

## Deux points de détection distincts

### 1. Au chargement — `create_entry()` (`entries.py:181-258`)

Point de passage obligé (skill `archive-image-loading`) pour toute image ajoutée à `images_data`. Tente `Image.open()` + `img.verify()` + un second `Image.open()` + `img.load()` (le `load()` force le décodage complet, ce qui déclenche une éventuelle `DecompressionBombError` sur une image aux dimensions démesurées — `verify()` seul ne charge pas les pixels et ne suffit donc pas à détecter ce cas). Deux issues d'erreur, marquées différemment :

- **`DecompressionBombError`** (image dont `largeur × hauteur` dépasse la limite PIL `Image.MAX_IMAGE_PIXELS`) → `is_corrupted = True`, **`is_too_large = True`**, `corruption_reason` = dimensions lisibles au format `"{w}x{h} ({pixels:,} pixels)"`. Les dimensions sont relues séparément avec `Image.MAX_IMAGE_PIXELS = None` temporairement désactivé (pour ne pas redéclencher la même erreur juste en lisant `width`/`height`), restauré juste après dans un `finally`.
- **Toute autre exception** (fichier tronqué, format non reconnu, données vides explicitement vérifiées via `len(data) == 0`, dimensions nulles/négatives) → `is_corrupted = True`, `is_too_large = False`, `corruption_reason` = `str(e)` brut (message d'exception Python, pas traduit).

Dans les deux cas, `entry["img"] = None` et **le hash MD5 n'est jamais calculé** (`entries.py:262-264`, condition `not entry["is_corrupted"]`) — une entrée corrompue n'apparaît donc jamais comme doublon d'une autre, qu'elle soit corrompue ou non.

### 2. Détection tardive — `ensure_image_loaded()` (`entries.py:480-522`)

Le lazy loading (skill `archive-image-loading`) peut différer le décodage complet d'une image jusqu'à son premier affichage réel (visionneuse, aperçu, opération d'édition). Si ce décodage échoue **alors que `create_entry()` avait réussi** (cas rare mais possible : fichier partiellement valide qui passe `verify()` mais échoue au décodage complet différé, ou fichier modifié sur disque entre-temps), `ensure_image_loaded` attrape l'exception et marque `entry["is_corrupted"] = True` **a posteriori** — sans distinction `is_too_large`/`corruption_reason` détaillée à ce second point (`corruption_reason` reste `None`, seul `is_corrupted` passe à `True`). `ensure_image_loaded` retourne immédiatement `None` sans retenter si l'entrée est déjà marquée corrompue (`if entry.get("is_corrupted"): return None`, tout en haut de la fonction) — pas de nouvelle tentative de décodage à chaque appel.

## Affichage visuel dans la mosaïque

- **Cadre rouge** (`ThumbnailItem.paint()`, `mosaic_canvas.py:714-722`) : `QPen(QColor(200, 0, 0), 3)` dessiné autour de la vignette si `entry.get("is_corrupted", False)` — dessiné **avant** les cadres de sélection/focus (qui peuvent donc se superposer par-dessus si l'entrée corrompue est aussi sélectionnée).
- **Repris sur la minimap** (`_paint_overlays()`, `minimap_widget_qt.py`, skill `minimap`) : même cadre rouge, redessiné à l'échelle de la mini-vignette (`QPen(QColor(200, 0, 0), 2)`).
- **Icône dédiée** (`get_icon_pil_for_entry`, `entries.py:42-54`) : `icons/fichier-corrompu.png` (`ICON_MAP["corrupted"]`), prioritaire sur toute icône par extension — vérifiée **avant** le test dossier/extension dans la chaîne de conditions.
- **Tooltip dédié** (`get_tooltip_text`, `tooltips_qt.py:14-41`, skill `qt-tooltips`) : message différent selon `is_too_large` — `tooltip.too_large_header` (*"Image trop grande pour être chargée"*) avec les pixels en cause, ou `tooltip.corrupted_header` (*"Image corrompue"*) avec la raison tronquée à 50 caractères (`reason[:50]`, évite un tooltip démesurément long sur une exception verbeuse). Les deux se terminent par une instruction explicite (`right_click_instruction1`/`2`, *"Clic droit pour remplacer ou supprimer cette image."*) — le tooltip sert donc aussi de découverte de fonctionnalité pour l'utilisateur.

## Remplacement — `PanelWidget._replace_corrupted_image(idx)` (`panel_widget.py:1727`)

1. Garde-fous : `idx` dans les bornes de `images_data`, entrée réellement marquée `is_corrupted` (retourne silencieusement sinon).
2. `QFileDialog.getOpenFileName` — dossier initial déduit de `state.current_file` si disponible, sinon dernier dossier ouvert en config (`ConfigManager.get('last_open_dir', "")`). Filtres traduits (`dialogs.replace_corrupted_image.filter_images`/`filter_all`).
3. **Double validation PIL du fichier choisi** avant tout remplacement : `Image.open()` + `img.verify()` puis un second `Image.open()` (même pattern à deux ouvertures que `create_entry()` — `verify()` laisse l'objet dans un état qui ne permet plus de le réutiliser pour un chargement normal, d'où la réouverture) — un fichier de remplacement lui-même invalide échoue ici et n'écrase jamais l'entrée corrompue existante.
4. `save_state_qt(..., force=True)` **avant** modification — `force=True` explicite (skill `undo-redo`), cohérent avec une opération anticipative où l'état pourrait déjà être identique au dernier snapshot.
5. Remplacement : `entry["bytes"] = data`, `is_corrupted`/`corruption_reason` réinitialisés (`False`/`None`), invalidation cache — **`_hash` remis à `None`** (l'entrée redevient éligible à la détection de doublons, skill `duplicate-detection`) et `qt_pixmap_large`/`qt_qimage_large` retirés (`pop`, pas `= None`, même style que `page-resize`).
6. `save_state_qt(..., force=True)` **après** modification — pattern standard à deux appels (contrairement à `create-ico`/`animated-gif`/`page-split` qui n'en ont qu'un, voir ces skills) : cohérent puisqu'il s'agit ici d'une **modification en place** d'une entrée existante, pas d'un ajout de nouvelle entrée.
7. `render_mosaic()` + `_refresh_toolbar_states()`.

**Piège à noter** : le remplacement ne touche **que** `entry["bytes"]` — le nom de fichier (`orig_name`), les dimensions stockées (`img_width`/`img_height`, pas recalculées ici), et toute autre métadonnée restent inchangés. Si l'image de remplacement a des dimensions différentes de l'original attendu, `img_width`/`img_height` de l'entrée peuvent rester obsolètes jusqu'au prochain point du code qui les relit depuis les bytes à jour (à vérifier au cas par cas si un bug de dimensions incorrectes est signalé après un remplacement).

**Erreur silencieuse côté utilisateur, pas silencieuse côté log** : toute exception pendant l'ouverture/la lecture du fichier de remplacement affiche `MsgDialog` (`messages.errors.load_image_failed`) avec le message d'exception brut interpolé — pas de distinction `is_too_large` à ce stade (contrairement à la détection initiale), un remplacement par un fichier trop volumineux échoue simplement avec le message d'erreur générique.

## Suppression — second point d'entrée du menu

`context_menu.image.corrupted_delete` (`context_menus_qt.py:482-484`) route directement vers `callbacks['delete_selected']` — **pas de logique spécifique aux entrées corrompues**, c'est le mécanisme générique de suppression de page du projet, simplement affiché comme option supplémentaire quand l'entrée sélectionnée est corrompue (pertinent quand aucun fichier de remplacement n'est disponible).

## Point d'entrée UI — menu contextuel uniquement, sélection unique obligatoire

**Un seul point d'entrée**, pas trois comme les skills d'édition d'image précédents : le menu contextuel (`context_menus_qt.py:476-484`, skill `qt-context-menus`). Pas de barre de menu, pas de bouton dédié dans la colonne d'icônes.

`is_corrupted` (variable calculée en amont du menu, `context_menus_qt.py:383`) exige **`single_image_selection`** — une seule entrée sélectionnée, qui doit être cette entrée précise et être marquée corrompue — sinon les deux actions (`corrupted_replace`/`corrupted_delete`) apparaissent grisées (`_add_disabled`). **Aucun remplacement en masse** n'est possible depuis ce menu : sélectionner plusieurs entrées corrompues simultanément désactive les deux actions plutôt que de proposer un remplacement/suppression par lot.

Callback `replace_corrupted_image` appelé avec `next(iter(st.selected_indices))` — extrait le seul index de l'ensemble de sélection (garanti singleton par la condition `single_image_selection` en amont).

## Exclusions dans le reste du projet

Une entrée corrompue est systématiquement filtrée hors de la plupart des opérations qui listent des "images valides" à travers le projet — grep `is_corrupted` pour la liste exhaustive à jour plutôt que d'en supposer une ici, mais notamment :
- **Détection de doublons** (`duplicate_detection_qt.py:31`) — exclue du calcul (`not entry.get("is_image") or entry.get("is_corrupted")`).
- **Sélection de départ des visionneuses d'édition** (`page-straighten`, `add-text-to-image`, `clone-zone`) — chacune filtre `image_entries` avec `not e.get('is_corrupted')` avant d'ouvrir sa fenêtre.
- **`ensure_image_loaded`** — court-circuite immédiatement sur une entrée déjà marquée corrompue, jamais de nouvelle tentative de décodage.

## Traductions

`locales/fr.json` : `context_menu.image.corrupted_replace` (`"⚠️ Image corrompue - Remplacer"`, ligne 98) et `context_menu.image.corrupted_delete` pour le menu ; `tooltip.corrupted_header`/`too_large_header`/`right_click_instruction1`/`right_click_instruction2`/`file`/`reason`/`unknown_error`/`too_large_pixels` (section `tooltip`, lignes 1255+) pour le tooltip dédié ; `dialogs.replace_corrupted_image.title`/`filter_images`/`filter_all` pour le sélecteur de fichier ; `messages.errors.load_image_failed.title`/`message` pour l'échec de remplacement. Voir skill `add-translation`.

**Absent du mode d'emploi** (`user_guide_qt.py`) — le tooltip fait office de seule documentation utilisateur pour cette fonctionnalité (skill `user-guide`).

## Comment étendre

- **Recalculer `img_width`/`img_height` après remplacement** (actuellement non fait, voir piège ci-dessus) : ajouter la relecture des dimensions dans `_replace_corrupted_image` juste après `entry["bytes"] = data`, en s'inspirant de `create_entry()` (`img.width`/`img.height`) — changement mineur mais à confirmer, ce silence pourrait être un oubli plutôt qu'un choix délibéré.
- **Permettre un remplacement en masse** (actuellement bloqué à une seule sélection) : changerait `is_corrupted`/le callback pour accepter `multi_image_selection`, itérer et ouvrir un sélecteur de fichier par entrée (ou une correspondance par nom) — changement de comportement significatif, à valider avec l'utilisateur avant de l'implémenter.
- **Distinguer `is_too_large` dans le message d'échec de remplacement** (actuellement message générique `load_image_failed`) : `_replace_corrupted_image`, bloc `except Exception as e` — détecter `DecompressionBombError` spécifiquement comme le fait `create_entry()`.
- **Ajouter une section au mode d'emploi** : suivre le pattern des autres sections `help.*` (skill `user-guide`) — actuellement seul le tooltip informe l'utilisateur de l'existence du remplacement.

## Pièges connus

- **Deux points de détection distincts** (`create_entry()` au chargement, `ensure_image_loaded()` en tardif) — un bug de détection doit être diagnostiqué en identifiant lequel des deux est en cause ; seul le premier renseigne `is_too_large`/`corruption_reason` en détail.
- **`is_too_large` est un sous-cas de `is_corrupted`**, pas un état indépendant — toujours vérifier `is_corrupted` en premier, `is_too_large` n'a de sens que si `is_corrupted` est déjà vrai.
- **Le remplacement ne recalcule pas `img_width`/`img_height`** — peuvent rester obsolètes après un remplacement par une image de dimensions différentes.
- **Aucun remplacement/suppression en masse** — le menu se désactive entièrement dès que plus d'une entrée est sélectionnée, même si toutes sont corrompues.
- **`corruption_reason` n'est jamais traduit** — message d'exception Python brut affiché tel quel dans le tooltip (tronqué à 50 caractères), pas une clé `locales/*.json`.
- **Entrée corrompue exclue du hash de doublons** — ne jamais supposer qu'une entrée corrompue peut apparaître dans un groupe de doublons détecté.

## Références croisées

- `archive-image-loading` — `create_entry()`/`ensure_image_loaded()`, points de détection ; lazy loading expliquant pourquoi la détection peut survenir tardivement.
- `duplicate-detection` — exclusion explicite et réciproque : une entrée corrompue n'a jamais de `_hash` calculé, ne peut donc jamais faire partie d'un groupe de doublons.
- `qt-tooltips` — tooltip dédié `get_tooltip_text`, seule documentation utilisateur du mécanisme de remplacement actuellement.
- `qt-context-menus` — les deux actions du menu contextuel (remplacer/supprimer), désactivées hors sélection unique corrompue.
- `undo-redo` — pattern standard à deux `save_state`/`force=True` suivi par le remplacement, cohérent avec une modification en place (contrairement aux créateurs de nouvelle entrée `create-ico`/`animated-gif`/`page-split`/`nfo-editor`).
- `page-straighten` / `add-text-to-image` / `clone-zone` — filtrent chacune les entrées corrompues de leur liste de pages navigables au démarrage.
- `mosaic-thumbnails` — cadre rouge dessiné dans `ThumbnailItem.paint()`, icône dédiée dans `get_icon_pil_for_entry`.
- `minimap` — même cadre rouge repris à l'échelle réduite dans `_paint_overlays()`.
- `user-guide` — absence actuelle de section dédiée.
