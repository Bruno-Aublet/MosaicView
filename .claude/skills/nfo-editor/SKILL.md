---
name: nfo-editor
description: Localiser ou modifier la création/édition de fichiers .nfo (fenêtre non-modale unique pour les deux modes, double-clic sur un .nfo existant pour l'éditer). Utiliser dès qu'une tâche touche à nfo_dialog_qt.py, show_nfo_dialog, ou au bouton/menu "Créer un fichier NFO".
---

# Éditeur de fichier NFO — MosaicView

Fenêtre non-modale unique (`_NfoDialog`, `modules/qt/nfo_dialog_qt.py`) qui sert **deux usages** selon les paramètres de construction : créer un nouveau fichier `.nfo` (texte libre) et l'insérer dans la mosaïque, ou éditer le contenu d'un fichier `.nfo` déjà présent. Fichier compact (~350 lignes), le plus simple des éditeurs de métadonnées du projet.

## Qu'est-ce qu'un `.nfo` — contexte hors code

Un fichier `.nfo` (de l'anglais "info") est une convention héritée de la scène warez/scan des années 1990-2000, largement reprise ensuite pour les archives de comics/mangas numérisés (CBZ/CBR). C'est un **fichier texte brut sans structure imposée** (contrairement à `ComicInfo.xml`, voir plus bas) : le "scanlator" ou la personne qui a assemblé l'archive y note ce qu'il souhaite — nom du groupe de scan, source, remerciements, notes de version, avertissements, liens, parfois même un art ASCII décoratif en en-tête. Aucun schéma, aucun champ obligatoire, aucune convention de nommage de section.

**Comment il est affiché par les lecteurs de BD numériques** : la grande majorité des logiciels de lecture (ComicRack, YACReader, CDisplayEx, etc.) **n'affichent pas** le `.nfo` comme une page de la BD au fil de la lecture — il est traité comme un fichier annexe de l'archive, généralement accessible via un onglet/panneau "Infos" ou "Détails du fichier" séparé de la vue de lecture, voire simplement listé parmi les fichiers non-image de l'archive sans affichage dédié du tout selon le lecteur. Ce n'est donc pas un élément visible en continu, mais une note de bas de page consultable à la demande — c'est pourquoi MosaicView le traite comme une entrée non-image de la mosaïque (icône dédiée `icons/nfo.png`, voir section création) plutôt que comme une page à afficher dans la visionneuse principale.

**Distinct de `comicinfo-metadata-editor`** — un `.nfo` est un texte libre sans structure imposée, alors que `ComicInfo.xml` (skill `comicinfo-metadata-editor`) est un XML structuré avec un schéma de ~35 champs standardisés (titre, série, numéro, auteurs, éditeur, résumé...) que les lecteurs et bibliothèques savent parser et exploiter pour le tri/la recherche/l'affichage de métadonnées structurées. Le `.nfo` reste lisible par un humain mais invisible pour tout traitement automatisé — les deux formats coexistent souvent dans une même archive, avec des rôles complémentaires (le `.nfo` pour le contexte informel, `ComicInfo.xml` pour les métadonnées structurées). Les deux partagent néanmoins le même point d'entrée de double-clic générique (`_open_non_image_entry`, voir section dédiée) et un mécanisme d'édition en place assez proche dans sa forme (callback `edit_fn` fourni par l'appelant), sans code réellement partagé entre les deux fichiers.

## Une seule classe, deux modes — `_NfoDialog`

Le mode est déterminé uniquement par la présence ou non du paramètre `entry` à la construction (`self._edit_mode = entry is not None`), pas par un paramètre `mode` explicite :

- **Mode création** (`entry=None`) : reçoit `inject_fn(filename, content)` et `state`. Champ nom de fichier vide, zone de texte vide.
- **Mode édition** (`entry=<dict>`) : reçoit `entry`, `edit_fn(new_filename, new_content)`, `state`. Champ nom de fichier pré-rempli avec le nom de base de l'entrée (sans extension ni chemin, `os.path.splitext(os.path.basename(orig))[0]`), zone de texte pré-remplie avec le contenu décodé de `entry["bytes"]`.

Les deux modes partagent **la totalité** de l'UI et de la logique de validation (`_on_create`, un seul handler qui branche selon `self._edit_mode`) — pas de duplication de fenêtre comme `page-crop`/`create-ico` ; seuls les libellés changent dynamiquement (titre, texte du bouton principal : `nfo.window_title`/`btn_create` vs `nfo.window_title_edit`/`btn_save`).

## Décodage du contenu en mode édition — tolérance UTF-8 puis latin-1

`self._text_edit.setPlainText(raw.decode("utf-8"))`, avec un `except` qui bascule sur `raw.decode("latin-1", errors="replace")` en cas d'échec — les fichiers `.nfo` sont un format ancien et informel, souvent produits par des outils Windows historiques en encodage local (CP1252/latin-1) plutôt qu'en UTF-8. Le fallback `latin-1` avec `errors="replace"` **ne peut jamais échouer** (latin-1 mappe tout octet 0-255 à un caractère), donc l'édition reste toujours possible même sur un fichier mal encodé, au prix d'un affichage éventuellement corrompu pour les caractères hors ASCII si l'encodage d'origine n'était ni UTF-8 ni latin-1.

**À la sauvegarde, le contenu est toujours réencodé en UTF-8** (`content.encode("utf-8")`, dans les deux callbacks `_edit_fn`/`_inject_nfo` côté appelant) — un fichier `.nfo` latin-1 ouvert puis sauvegardé sans modification change silencieusement d'encodage. Pas un bug documenté comme un problème connu dans le code, mais un comportement à connaître si un rapport utilisateur mentionne un `.nfo` dont l'encodage change après édition dans MosaicView.

## Validation avant sauvegarde — `_on_create`

Commune aux deux modes (dupliquée dans les deux branches du `if self._edit_mode` plutôt que factorisée, légère duplication de code à connaître si une règle de validation doit changer) :

1. **Nom de fichier non vide** — sinon `ErrorDialog` (`nfo.error_title`/`nfo.error_empty_name`), focus rendu au champ.
2. **Extension `.nfo` forcée** si absente (`if not filename.lower().endswith(".nfo"): filename += ".nfo"`) — l'utilisateur peut taper juste le nom sans extension, elle est ajoutée automatiquement, insensible à la casse pour la détection.
3. **Détection de doublon** dans `state.images_data`, comparaison insensible à la casse sur `orig_name` — en mode édition, l'entrée en cours d'édition elle-même est explicitement exclue de cette vérification (`e is not self._entry and ...`, comparaison par identité d'objet Python) pour ne pas se bloquer soi-même en renommant un fichier vers son propre nom actuel.

Aucune validation sur le **contenu** du texte (peut être vide, aucune limite de taille, aucun filtrage de caractères).

## Callbacks réels — définis côté appelant, pas dans ce fichier

`nfo_dialog_qt.py` ne touche jamais directement à `state.images_data` ni à `entry['bytes']` — il se contente d'appeler `inject_fn`/`edit_fn` fournis par l'appelant avec le nom et le contenu validés. La vraie logique métier vit dans `panel_widget.py` :

### Création — `PanelWidget._show_nfo_dialog` → `_inject_nfo` (`panel_widget.py:2394`)

1. `content.encode("utf-8")` puis **`create_entry(filename, data, IMAGE_EXTS)`** (skill `archive-image-loading`) — réutilise le point de passage standard du projet, contrairement à `create-ico` (dict construit manuellement) mais comme `animated-gif`. `.nfo` n'étant pas dans `IMAGE_EXTS`, l'entrée résultante a `is_image: False` — traitée comme un fichier non-image générique de la mosaïque (icône dédiée `icons/nfo.png`, voir `font_manager_qt.py`/mapping d'icônes par extension).
2. `entry["source_archive"] = "loose"` — forcé après coup, cohérent avec les autres créations "depuis rien" du projet (`create-ico`, `animated-gif`).
3. **Deux appels `save_state_qt(st, ...)`**, un avant l'ajout à `images_data`, un après — pattern standard à deux points (skill `undo-redo`), **contrairement à `create-ico`/`animated-gif`/`page-split`** qui n'en ont qu'un seul pour un ajout de page. La création d'un `.nfo` suit donc le pattern "normal" alors que les autres créateurs de nouvelle entrée du projet s'en écartent — à noter si une cohérence inter-fichiers est un jour recherchée.
4. `sync_pages_in_xml_data(st)` (skill `comicinfo-metadata-editor`) après l'ajout.

### Édition — `PanelWidget._open_nfo_for_edit` → `_edit_fn` (`panel_widget.py:2337`)

1. `new_content.encode("utf-8")`, puis **mutation directe** : `entry["orig_name"] = new_filename; entry["bytes"] = new_bytes` — contrairement au pattern documenté dans le skill `apply-image-operation` pour les images (jamais muter `entry['bytes']` en place, toujours réassigner un nouvel objet), ici c'est une **réassignation complète** du champ `bytes` sur le même objet `entry`, ce qui est le comportement correct et attendu pour du texte (pas de cache PIL/Qt lié aux bytes texte à invalider, contrairement à une image).
2. `self.save_state(force=True)` **avant et après** — `force=True` explicite aux deux appels, cohérent avec le skill `undo-redo` pour une opération anticipative où l'état courant pourrait déjà être identique au dernier snapshot.
3. `st.modified = True`, puis rafraîchissement complet : `render_mosaic()`, `_refresh_title()`, `_update_status_bar()`, `_refresh_toolbar_states()` — pas d'invalidation de cache vignette PIL/Qt (`large_thumb_pil`/`qt_pixmap_large`/`_hash`) puisqu'un `.nfo` n'est pas une image et n'a jamais eu ces caches.

## Point d'entrée d'édition — double-clic générique sur fichier non-image

**Mécanisme partagé, pas propre à ce skill** : `_open_non_image_entry` (`panel_widget.py:2321`, câblée comme `self._canvas._open_non_image_callback`) est le handler de double-clic sur **n'importe quelle** entrée non-image de la mosaïque. Elle route :
- `.nfo` → `_open_nfo_for_edit` (ce skill).
- `ComicInfo.xml` (insensible à la casse, comparaison sur le nom complet) → `_open_comicinfo_for_edit` (skill `comicinfo-metadata-editor`).
- Tout le reste → ouverture avec l'application Windows par défaut (`_open_file_with_default_app`).

Une tâche qui modifierait ce routage doit vérifier l'impact sur les deux autres branches, pas seulement `.nfo`.

## Points d'entrée UI — création uniquement

Contrairement aux autres skills d'édition d'image, **aucun menu ni bouton n'ouvre le mode édition directement** — le mode édition n'est atteignable que par le double-clic générique décrit ci-dessus. Le mode **création** a 3 points d'entrée classiques, tous inconditionnels (pas de garde-fou de sélection, un `.nfo` peut être créé même mosaïque vide) :

1. **Menu contextuel** (skill `qt-context-menus`) — `context_menus_qt.py:109`, clé `nfo.menu_item`.
2. **Barre de menu** — `menubar_qt.py:92`, même clé.
3. **Colonne d'icônes** (skill `icon-toolbar`) — bouton id `"create_nfo"` (`icon_toolbar_qt.py:2151`), tooltip `nfo.tooltip`.

## Menu contextuel interne au champ de texte

`_setup_text_context_menu`/`_show_text_menu` (`nfo_dialog_qt.py:241`) — menu clic droit **custom** sur le `QTextEdit` (Copier/Couper/Coller/Sélectionner tout, activation conditionnelle selon présence d'une sélection ou du presse-papiers), suivant la règle UI n°6 du CLAUDE.md (jamais le menu natif Qt). Implémentation **locale à ce fichier**, pas via le helper générique `setup_textedit_context_menu` (`modules/qt/utils.py`) que d'autres fichiers du projet réutilisent (ex. `text_tool_qt.py`, `animated_gif_dialog_qt.py` pour leurs propres `QTextEdit`) — à harmoniser si une refonte de ce dialogue est un jour demandée, mais fonctionnellement équivalent en l'état.

## Raccourci Home/End personnalisé — `eventFilter`

Intercepte `Home`/`End` (avec ou sans Shift, pour la sélection) dans le `QTextEdit` pour aller au **tout début/toute fin du texte entier** plutôt que début/fin de ligne (comportement Qt par défaut) — reproduit le raccourci `Ctrl+Home`/`Ctrl+End` sur la touche simple. Comportement délibéré (commentaire explicite dans le code), pas un oubli — à ne pas "corriger" vers le comportement Qt standard sans consigne explicite.

## Traductions

`locales/fr.json`, section `nfo` (ligne 1701) : `window_title`/`window_title_edit` (deux clés distinctes selon le mode, résolues via `_wt()` — règle UI n°7), `filename_label`/`content_label`, `btn_create`/`btn_save` (deux clés selon le mode)/`btn_clear`, `menu_item`/`tooltip`, `success_title`/`success_message`, `error_title`/`error_empty_name`/`error_write`/`error_duplicate`. Voir skill `add-translation`.

**A une section dans le mode d'emploi** (`user_guide_qt.py:649`, clé `help.nfo_editor`/`help.nfo_editor_content`) — comme `page-resize`/`page-crop`/`create-ico` (skill `user-guide`).

## Comment étendre

- **Factoriser la duplication de validation** (nom vide/extension/doublon dupliqués entre les deux branches de `_on_create`) : extraire une méthode `_validate_filename(filename) -> str | None` commune — amélioration de maintenabilité pure, sans changement de comportement si bien fait.
- **Réutiliser `setup_textedit_context_menu`** au lieu du menu contextuel custom local : remplacer `_setup_text_context_menu`/`_show_text_menu` par un appel à `modules.qt.utils.setup_textedit_context_menu(self._text_edit)` — vérifier que le résultat visuel/fonctionnel reste identique avant de le faire (règle CLAUDE.md : ne pas modifier hors périmètre sans consigne).
- **Ajouter une validation de contenu** (ex. limite de taille) : dans `_on_create`, avant l'appel à `inject_fn`/`edit_fn` — actuellement aucune limite.
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour `_NfoDialog` (non-modale déjà en place, `_wt()` pour le titre déjà en place, menu contextuel custom déjà en place).

## Pièges connus

- **Ré-encodage systématique en UTF-8 à la sauvegarde**, même si le fichier source était en latin-1 — un `.nfo` non-UTF-8 ouvert puis resauvegardé change d'encodage silencieusement.
- **Validation dupliquée entre les deux branches du mode** (création/édition) dans `_on_create` — toute évolution des règles doit être répercutée aux deux endroits tant que la factorisation n'est pas faite.
- **Aucun menu/bouton n'ouvre le mode édition directement** — uniquement accessible via le double-clic générique `_open_non_image_entry`, partagé avec le routage vers `ComicInfo.xml` et l'application par défaut.
- **La création suit le pattern standard à deux `save_state`**, contrairement à `create-ico`/`animated-gif`/`page-split` qui n'en ont qu'un — ne pas supposer que toute création de nouvelle entrée du projet suit un seul pattern uniforme.
- **Menu contextuel du `QTextEdit` implémenté localement**, pas via le helper partagé `setup_textedit_context_menu` — à harmoniser un jour, mais fonctionnellement correct en l'état.
- **Home/End personnalisés délibérément** (texte entier, pas ligne courante) — comportement voulu, ne pas "corriger" par réflexe.

## Références croisées

- `comicinfo-metadata-editor` — partage le même point d'entrée générique de double-clic (`_open_non_image_entry`) et une forme similaire de callback `edit_fn`, mais format et complexité très différents (XML structuré ~35 champs vs texte libre).
- `archive-image-loading` — `create_entry()` réutilisé pour la nouvelle entrée `.nfo` (comme `animated-gif`, contrairement à `create-ico`).
- `undo-redo` — pattern standard à deux `save_state`/`force=True` suivi ici, à comparer aux écarts documentés dans `create-ico`/`animated-gif`/`page-split`/`page-straighten`/`page-crop`.
- `apply-image-operation` — hors périmètre direct (pas d'image, pas de cache vignette à invalider) mais comparaison utile sur la mutation en place de `entry['bytes']`, correcte ici pour du texte contrairement à la règle stricte documentée pour les images.
- `icon-toolbar` — bouton "create_nfo" de la colonne d'icônes.
- `qt-context-menus` — entrée du menu contextuel clic droit de la mosaïque (à ne pas confondre avec le menu contextuel interne au champ de texte de ce dialogue).
- `qt-tooltips` — tooltip du bouton colonne d'icônes.
- `user-guide` — section `help.nfo_editor` existante, à maintenir à jour.
