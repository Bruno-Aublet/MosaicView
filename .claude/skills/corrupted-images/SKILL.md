---
name: corrupted-images
description: Localiser ou modifier la détection et le remplacement des images corrompues (is_corrupted/is_too_large au chargement, cadre rouge dans la mosaïque, menu remplacer/supprimer), et les pages altérées (somme de contrôle fausse mais image lisible : cadre orange, AlteredMemberError, create_page_entry, mark_altered/is_altered, question « Pages altérées » avant l'enregistrement). Utiliser dès qu'une tâche touche à entry["is_corrupted"], corruption_reason, _replace_corrupted_image, altered_bytes ou _confirm_altered_pages_qt.
---

# Images corrompues — MosaicView

Détection automatique des images illisibles ou dont le décodage échoue au chargement d'une archive, avec signalement visuel dans la mosaïque et deux actions dédiées (remplacer par un fichier valide, ou supprimer la page). Distinct de la détection de **doublons** (skill `duplicate-detection`, pages identiques mais valides) — une entrée corrompue est explicitement **exclue** du calcul de hash de doublons, les deux mécanismes ne se chevauchent jamais sur la même entrée.

## Deux points de détection distincts

### 1. Au chargement — `create_entry()` (`entries.py`)

Point de passage obligé (skill `archive-image-loading`) pour toute image ajoutée à `images_data`. Tente `Image.open()` + `img.verify()` + un second `Image.open()` + `img.load()` (le `load()` force le décodage complet, pour qu'une image tronquée ou abîmée soit détectée dès le chargement). Deux issues d'erreur, marquées différemment :

- **`DecompressionBombError`**, levée par `Image.open()` → `is_corrupted = True`, **`is_too_large = True`**, `corruption_reason` = dimensions lisibles au format `"{w}x{h} ({pixels:,} pixels)"`. **Limite réelle : 1 milliard de pixels.** `MosaicView.py` règle `Image.MAX_IMAGE_PIXELS = 500_000_000`, mais Pillow ne lève l'erreur qu'au-delà du **double** de cette valeur ; entre 500 millions et 1 milliard, il n'émet qu'un avertissement (`DecompressionBombWarning`, non transformé en erreur) et l'image s'ouvre. Valeur gardée pour les très grands scans (rapport d'audit, Audit 6, P3-2). Les dimensions sont lues dans l'en-tête par `entries._header_size(data)`, qui appelle directement le lecteur du format (`Image.OPEN`, formats de `Image.ID`) sans décoder l'image ni appliquer le contrôle de taille. **Piège** : ne jamais mettre `Image.MAX_IMAGE_PIXELS` à `None`, même un instant, pour relire les dimensions — la limite serait levée pour toute l'application, et une image ouverte au même moment par un autre thread y échapperait. Testé par `tests/test_too_large_page_size.py`.
- **Toute autre exception** (fichier tronqué, format non reconnu, données vides explicitement vérifiées via `len(data) == 0`, dimensions nulles/négatives) → `is_corrupted = True`, `is_too_large = False`, `corruption_reason` = `str(e)` brut (message d'exception Python, pas traduit). « Format non reconnu » couvre aussi un contenu d'un format que Pillow sait lire mais que MosaicView ne gère pas (EPS, PPM, PSD… nommé `.jpg`) : Pillow est restreint au démarrage aux formats gérés (`archive_names.restrict_pillow_formats`, skill `archive-image-loading`).
- **Aucune donnée** (`data is None` : membre de l'archive illisible — CRC faux dont les octets ne se décodent pas (voir « Pages altérées »), compression non prise en charge, membre chiffré, membre de plus de `MAX_MEMBER_SIZE` (500 Mo, raison « 4.20 Go > 500.0 Mo ») —) → `is_corrupted = True` ; `corruption_reason` = message technique de l'erreur de lecture, rattaché par `archive_loader._attach_read_errors` à la fin d'une ouverture, ou posé directement par `ImportMergeWorker` lors de l'ajout d'une archive à un comic ouvert (la page n'est alors signalée que par son cadre rouge, plus dans « Fichiers ignorés » — skill `archive-image-loading`), sinon `None` (tooltip : `tooltip.unknown_error`, `get_tooltip_text` lisant `corruption_reason or …`). Cadre rouge, Remplacer/Supprimer comme toute page corrompue ; tant qu'elle n'est ni remplacée ni supprimée, les enregistrements sont refusés (`MissingPageDataError`, skill `save-export`) — l'écrire sans elle la perdrait.

Dans les deux cas, `entry["img"] = None` et **le hash MD5 n'est jamais calculé** (fin de `create_entry`, condition `not entry["is_corrupted"]`) — une entrée corrompue n'apparaît donc jamais comme doublon d'une autre, qu'elle soit corrompue ou non.

### 2. Détection tardive — `ensure_image_loaded()` (`entries.py`)

Le lazy loading (skill `archive-image-loading`) peut différer le décodage complet d'une image jusqu'à son premier affichage réel (visionneuse, aperçu, opération d'édition). Si ce décodage échoue **alors que `create_entry()` avait réussi** (cas rare mais possible : fichier partiellement valide qui passe `verify()` mais échoue au décodage complet différé, ou fichier modifié sur disque entre-temps), `ensure_image_loaded` attrape l'exception et marque `entry["is_corrupted"] = True` **a posteriori** — sans distinction `is_too_large`/`corruption_reason` détaillée à ce second point (`corruption_reason` reste `None`, seul `is_corrupted` passe à `True`). `ensure_image_loaded` retourne immédiatement `None` sans retenter si l'entrée est déjà marquée corrompue (`if entry.get("is_corrupted"): return None`, tout en haut de la fonction) — pas de nouvelle tentative de décodage à chaque appel.

## Affichage visuel dans la mosaïque

- **Cadre rouge** (`ThumbnailItem.paint()`, `mosaic_canvas.py`) : `QPen(QColor(200, 0, 0), 3)` dessiné autour de la vignette si `entry.get("is_corrupted", False)` — dessiné **avant** les cadres de sélection/focus (qui peuvent donc se superposer par-dessus si l'entrée corrompue est aussi sélectionnée).
- **Repris sur la minimap** (`_paint_overlays()`, `minimap_widget_qt.py`, skill `minimap`) : même cadre rouge, redessiné à l'échelle de la mini-vignette (`QPen(QColor(200, 0, 0), 2)`).
- **Icône dédiée** (`get_icon_pil_for_entry`, `entries.py`) : `icons/fichier-corrompu.png` (`ICON_MAP["corrupted"]`), prioritaire sur toute icône par extension — vérifiée **avant** le test dossier/extension dans la chaîne de conditions.
- **Tooltip dédié** (`get_tooltip_text`, `tooltips_qt.py`, skill `qt-tooltips`) : message différent selon `is_too_large` — `tooltip.too_large_header` (*"Image trop grande pour être chargée"*) avec les pixels en cause, ou `tooltip.corrupted_header` (*"Image corrompue"*) avec la raison traduite par `read_errors.read_error_text(corruption_reason)` (cause reconnue dans la langue de l'utilisateur, sinon « illisible (message technique) » tronqué à 80 caractères — skill `archive-image-loading`, « Raisons d'une page ou d'un fichier illisible »). Les deux se terminent par une instruction explicite (`right_click_instruction1`/`2`, *"Clic droit pour remplacer ou supprimer cette image."*) — le tooltip sert donc aussi de découverte de fonctionnalité pour l'utilisateur.
- **Aucune fenêtre de signalement** à la fin du chargement : le cadre rouge, l'icône et l'info-bulle suffisent (choix de l'utilisateur). Ne pas réintroduire de fenêtre listant les pages illisibles : elle s'ouvrirait en même temps que « Fichiers ignorés » (fichiers non-image illisibles), superposée à elle.

## Pages altérées — somme de contrôle fausse, image lisible (cadre orange)

Une archive note pour chaque membre une somme de contrôle (CRC) calculée à sa création. Quand les octets ont changé depuis (disque défaillant, copie abîmée), la lecture normale échoue. Ces octets peuvent pourtant encore former une image affichable, avec des défauts. Une telle page est montrée, mais signalée.

- **Lecteurs** (`archive_loader.py`) : en cas de CRC faux, chacun relit les octets sans ce contrôle et lève **`AlteredMemberError(message, data)`**, qui hérite d'`Exception`.
  - ZIP : `_zip_member_reader`, qui relit le membre avec `_expected_crc = None`.
  - RAR : `_rar_read`. rarfile ne rend aucun octet, même avec `crc_check=False` ; le membre est donc relu par `_rar_bytes_without_crc`, qui lance `UnRAR p -inul -kb` : code de sortie 3 = CRC faux, et un nom contenant `*`/`?` n'est jamais relu.
  - CB7 : `_read_7z_file`, sur « CRC Failed » avec des octets reçus. « Data Error » (données compressées détruites) reste une `RuntimeError`.
  - Les deux programmes externes sont lus par `_run_and_read`, avec la limite `MAX_MEMBER_SIZE`.
  - CBT n'est pas concerné : le TAR n'a pas de somme de contrôle.
  - Une archive *compressée* abîmée donne presque toujours des octets faux après l'endroit touché : la page ne se décode pas et reste rouge. C'est le cas attendu.
- **Qui en profite** : seules l'ouverture (`LoadWorker`) et l'ajout d'une archive (`ImportMergeWorker`) passent par **`create_page_entry(file_name, read)`**.
  - Si le membre est une image qui se décode, la page est affichée et marquée par `entries.mark_altered`.
  - Sinon, elle redevient la page rouge sans données habituelle, avec la raison du lecteur (copie possible via `unread_member`, skill `clipboard`).
  - Un fichier qui n'est pas une image (ComicInfo.xml abîmé) n'est jamais repris ainsi.
  - Conversions par lot, Bibliothèque, import de métadonnées en lot : ils ne traitent pas `AlteredMemberError`, qui reste pour eux une erreur de lecture ordinaire. Ce choix est voulu par l'utilisateur : chaque page altérée doit être regardée une par une, jamais traitée par lot.
- **La marque suit les octets** (`entries.py`) :
  - `mark_altered(entry)` garde une référence vers les octets lus (`entry["altered_bytes"]`). `is_altered(entry)` est vraie tant que `entry["bytes"] is entry["altered_bytes"]`.
  - Une retouche ou un remplacement donne de nouveaux octets et efface la marque, sans qu'aucune des opérations qui modifient `entry["bytes"]` ait à la connaître.
  - Annuler rend les mêmes octets (référence partagée, skill `undo-redo`) et donc la marque ; l'historique garde `altered_bytes` dans les propriétés de la page (`_entry_metadata`).
  - `expand_multipage_tiffs` marque toutes les pages d'un TIFF altéré.
  - `forget_altered_pages` retire les marques après l'écriture du document entier (`file_operations_qt._write_document_unlocked`, branche `whole_document`), puisque le fichier écrit a des sommes de contrôle justes. Ce n'est pas fait pour une sélection enregistrée dans un autre fichier.
- **Affichage** :
  - cadre `ALTERED_FRAME_COLOR` (orange, `mosaic_canvas.py`) dans `ThumbnailItem.paint()`, dessiné seulement si la page n'est pas rouge ;
  - même cadre dans la minimap ;
  - ligne `tooltip.altered` sous le nom dans `get_tooltip_text`.
  - La page n'est pas `is_corrupted` : visionneuse, retouches, impression, doublons la traitent comme une page saine.
- **Enregistrement** : `_confirm_altered_pages_qt(parent, on_done, entries)` (`file_operations_qt.py`), étape de la chaîne de validation de `save_as_cbz`, `apply_new_names`, `create_cbz_from_images` et `save_selection_as_cbz`. Pour cette dernière, seules les pages sélectionnées comptent.
  - La fenêtre `ConfirmDialog` est en texte brut (`plain_text=True`, car les noms de pages viennent de l'archive) et son bouton de validation est `save_anyway`.
  - Clés `messages.warnings.altered_pages_save` : `message_one`, `message_many`, `message_many_more` au-delà de `_ALTERED_PAGES_LISTED` (10) pages nommées.
  - L'export vers un dossier, le presse-papiers et le glisser vers l'Explorateur ne demandent rien : un fichier isolé n'a pas de somme de contrôle.
- Testé par `tests/test_altered_pages.py` : vraies archives ZIP et 7z ; RAR créé par WinRAR, test ignoré s'il est absent ; marque qui suit les octets ; page déplacée vers l'autre panneau (marque reportée par `PanelWidget._on_inter_panel_drop._copy_entry`, skill `clipboard`) ; TIFF de plusieurs pages ; question d'enregistrement.

## Remplacement — `PanelWidget._replace_corrupted_image(idx)` (`panel_widget.py`)

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

`context_menu.image.corrupted_delete` (`context_menus_qt.py`, `show_image_context_menu`) route directement vers `callbacks['delete_selected']` — **pas de logique spécifique aux entrées corrompues**, c'est le mécanisme générique de suppression de page du projet, simplement affiché comme option supplémentaire quand l'entrée sélectionnée est corrompue (pertinent quand aucun fichier de remplacement n'est disponible).

## Point d'entrée UI — menu contextuel uniquement, sélection unique obligatoire

**Un seul point d'entrée**, pas trois comme les skills d'édition d'image précédents : le menu contextuel (`show_image_context_menu` dans `context_menus_qt.py`, skill `qt-context-menus`). Pas de barre de menu, pas de bouton dédié dans la colonne d'icônes.

`is_corrupted` (variable locale calculée en tête de `show_image_context_menu`) exige **`single_image_selection`** — une seule entrée sélectionnée, qui doit être cette entrée précise et être marquée corrompue — sinon les deux actions (`corrupted_replace`/`corrupted_delete`) apparaissent grisées (`_add_disabled`). **Aucun remplacement en masse** n'est possible depuis ce menu : sélectionner plusieurs entrées corrompues simultanément désactive les deux actions plutôt que de proposer un remplacement/suppression par lot.

Callback `replace_corrupted_image` appelé avec `next(iter(st.selected_indices))` — extrait le seul index de l'ensemble de sélection (garanti singleton par la condition `single_image_selection` en amont).

## Exclusions dans le reste du projet

Une entrée corrompue est systématiquement filtrée hors de la plupart des opérations qui listent des "images valides" à travers le projet — grep `is_corrupted` pour la liste exhaustive à jour plutôt que d'en supposer une ici, mais notamment :
- **Détection de doublons** (`duplicate_detection_qt.py`) — exclue du calcul (`not entry.get("is_image") or entry.get("is_corrupted")`).
- **Sélection de départ des visionneuses d'édition** (crop/redressement/clonage dans la visionneuse principale — skills `page-crop`/`page-straighten`/`clone-zone` — et `add-text-to-image`) — chacune filtre `image_entries` avec `not e.get('is_corrupted')` avant d'ouvrir/présélectionner.
- **`ensure_image_loaded`** — court-circuite immédiatement sur une entrée déjà marquée corrompue, jamais de nouvelle tentative de décodage.

## Traductions

Pages altérées : `tooltip.altered` et `messages.warnings.altered_pages_save.*` (`title`, `message_one`, `message_many`, `message_many_more`, `save_anyway`).

`locales/fr.json` : `context_menu.image.corrupted_replace` (`"⚠️ Image corrompue - Remplacer"`) et `context_menu.image.corrupted_delete` pour le menu ; `tooltip.corrupted_header`/`too_large_header`/`right_click_instruction1`/`right_click_instruction2`/`file`/`reason`/`unknown_error`/`too_large_pixels` (section `tooltip`) pour le tooltip dédié ; `dialogs.replace_corrupted_image.title`/`filter_images`/`filter_all` pour le sélecteur de fichier ; `messages.errors.load_image_failed.title`/`message` pour l'échec de remplacement. Voir skill `add-translation`.

**Mode d'emploi** : la section « La mosaïque » explique les deux cadres en quelques lignes, sans terme technique (`help.mosaic_frames_content`, affichée après `help.mosaic_content`, skill `user-guide`) : rouge = page trop abîmée pour être affichée, à remplacer ou supprimer par clic droit, qui peut empêcher d'enregistrer selon le cas ; orange = page affichée mais peut-être abîmée, à regarder dans la visionneuse, cadre qui disparaît à la modification, avertissement avant l'enregistrement. **Piège de formulation** : ne jamais écrire qu'une page orange « a changé » ou « a été modifiée » ; l'utilisateur le comprend comme une modification par quelqu'un, alors que seule la vérification du fichier échoue. Formulation retenue : « son contenu ne correspond plus au contrôle enregistré dans le fichier ». Si ce comportement change (par exemple, une page rouge qui empêcherait toujours l'enregistrement), mettre ce texte à jour dans les 46 langues.

## Comment étendre

- **Recalculer `img_width`/`img_height` après remplacement** (actuellement non fait, voir piège ci-dessus) : ajouter la relecture des dimensions dans `_replace_corrupted_image` juste après `entry["bytes"] = data`, en s'inspirant de `create_entry()` (`img.width`/`img.height`) — changement mineur mais à confirmer, ce silence pourrait être un oubli plutôt qu'un choix délibéré.
- **Permettre un remplacement en masse** (actuellement bloqué à une seule sélection) : changerait `is_corrupted`/le callback pour accepter `multi_image_selection`, itérer et ouvrir un sélecteur de fichier par entrée (ou une correspondance par nom) — changement de comportement significatif, à valider avec l'utilisateur avant de l'implémenter.
- **Distinguer `is_too_large` dans le message d'échec de remplacement** (actuellement message générique `load_image_failed`) : `_replace_corrupted_image`, bloc `except Exception as e` — détecter `DecompressionBombError` spécifiquement comme le fait `create_entry()`.
- **Compléter l'explication du mode d'emploi** : `help.mosaic_frames_content` (section « La mosaïque », skill `user-guide`) ; texte validé par l'utilisateur, à lui soumettre avant toute modification.

## Tests de la détection

`tests/test_page_edge_cases.py` : fichier vide, PNG et JPEG tronqués, octets quelconques, texte nommé en image → `is_corrupted`, `corruption_reason` renseignée, octets d'origine gardés, `is_image` vrai ; page de 1×1 lisible ; 120 versions abîmées au hasard (graine fixe) d'un JPEG et d'un PNG : `create_entry` ne lève jamais, et toute page déclarée lisible a des dimensions valides et se décode. Page trop grande : `tests/test_too_large_page_size.py` ; pages altérées : `tests/test_altered_pages.py`.

## Pièges connus

- **Deux points de détection distincts** (`create_entry()` au chargement, `ensure_image_loaded()` en tardif) — un bug de détection doit être diagnostiqué en identifiant lequel des deux est en cause ; seul le premier renseigne `is_too_large`/`corruption_reason` en détail.
- **`is_too_large` est un sous-cas de `is_corrupted`**, pas un état indépendant — toujours vérifier `is_corrupted` en premier, `is_too_large` n'a de sens que si `is_corrupted` est déjà vrai.
- **Le remplacement ne recalcule pas `img_width`/`img_height`** — peuvent rester obsolètes après un remplacement par une image de dimensions différentes.
- **Aucun remplacement/suppression en masse** — le menu se désactive entièrement dès que plus d'une entrée est sélectionnée, même si toutes sont corrompues.
- **`corruption_reason` est stocké brut, traduit seulement à l'affichage** — message technique de l'exception (ou texte de `create_entry`, « Fichier image vide », « Dimensions invalides »), jamais une clé de traduction ni un texte déjà traduit : la traduction se fait à chaque affichage de l'info-bulle par `read_error_text`, et suit donc un changement de langue. Un nouveau message brut produit par le code doit être reconnu par un motif de `read_errors._CAUSES`, sinon il s'affiche « illisible (message) ».
- **Entrée corrompue exclue du hash de doublons** — ne jamais supposer qu'une entrée corrompue peut apparaître dans un groupe de doublons détecté.

## Références croisées

- `archive-image-loading` — `create_entry()`/`ensure_image_loaded()`, points de détection ; lazy loading expliquant pourquoi la détection peut survenir tardivement.
- `duplicate-detection` — exclusion explicite et réciproque : une entrée corrompue n'a jamais de `_hash` calculé, ne peut donc jamais faire partie d'un groupe de doublons.
- `qt-tooltips` — tooltip dédié `get_tooltip_text`, seule documentation utilisateur du mécanisme de remplacement actuellement.
- `qt-context-menus` — les deux actions du menu contextuel (remplacer/supprimer), désactivées hors sélection unique corrompue.
- `undo-redo` — pattern standard à deux `save_state`/`force=True` suivi par le remplacement, cohérent avec une modification en place (contrairement aux créateurs de nouvelle entrée `create-ico`/`animated-gif`/`page-split`/`nfo-editor`).
- `page-crop` / `page-straighten` / `clone-zone` / `add-text-to-image` — filtrent chacune les entrées corrompues de leur liste de pages navigables au démarrage.
- `mosaic-thumbnails` — cadre rouge dessiné dans `ThumbnailItem.paint()`, icône dédiée dans `get_icon_pil_for_entry`.
- `minimap` — même cadre rouge repris à l'échelle réduite dans `_paint_overlays()`.
- `user-guide` — explication des cadres rouge et orange dans la section « La mosaïque » (`help.mosaic_frames_content`).
