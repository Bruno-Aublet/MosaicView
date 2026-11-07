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

## Décodage du contenu en mode édition — UTF-8 puis codage Windows

`_annex_text(raw)` (`nfo_dialog_qt.py`) : UTF-8, sinon `archive_names.windows_text` (cp1252, puis latin-1 pour les quelques octets que cp1252 ne définit pas). Les fichiers `.nfo` sont un format ancien et informel, souvent produits par des outils Windows historiques. Cette lecture ne peut pas échouer, donc l'édition reste toujours possible. **Piège** : lu directement en latin-1, un fichier cp1252 perd `’`, `€`, `–`, guillemets et `…`, devenus des caractères de contrôle invisibles, et la validation les écrit ainsi dans le fichier, sans annexe pour les récupérer. Testé par `tests/test_windows_text.py`.

**Limite connue** : un `.nfo` en cp437 (DOS, dessins en caractères) reste mal affiché, ce codage ne se reconnaissant pas de façon sûre. Le valider écrit ce texte mal lu.

**À la sauvegarde, le contenu est toujours réencodé en UTF-8** (`content.encode("utf-8")`, dans les deux callbacks `_edit_fn`/`_inject_nfo` côté appelant, avant `_commit_annex_text`) — un fichier `.nfo` cp1252 modifié puis enregistré change de codage, en gardant le même texte.

**« Enregistrer » grisé tant que rien n'a changé** (mode édition) : `self._initial` garde le nom et le texte de départ tels que relus par les champs (`toPlainText()` après `setPlainText`, fins de ligne normalisées), et `_update_save_button`, branché sur `textChanged` des deux champs, n'active le bouton que s'ils diffèrent ; il se regrise si l'on revient au contenu de départ. **Piège** : enregistrer sans modification marquait le document modifié et réécrivait le fichier en UTF-8. Le mode création garde son bouton actif (un nom vide est refusé par un message).

**Fichier illisible** (membre d'archive non lu, `bytes` à `None`) : la fenêtre ne s'ouvre pas, le double-clic affiche la raison (`entries.is_unreadable_file`, skill `corrupted-images`).

## Validation avant sauvegarde — `_checked_filename`

Commune aux deux modes (`_on_create` l'appelle, puis `edit_fn` ou `inject_fn`) ; chaque refus affiche un `ErrorDialog` (`nfo.error_title`) et rend le focus au champ :

1. **Nom de fichier non vide** — `nfo.error_empty_name`.
2. **Extension `.nfo` forcée** si absente (insensible à la casse) — l'utilisateur peut taper juste le nom.
3. **Nom valable sous Windows** — `_name_error_key(filename)` (fonction de module) : `/` ou `\` refusés (ils créaient un dossier dans la BD), puis même règle que `utils.windows_safe_component`, qui corrige au lieu de refuser : caractères interdits ou de contrôle → `dialogs.macro_name_error.invalid_chars`, nom réservé (`CON`, `NUL`, `COM1`…) → `dialogs.macro_name_error.reserved_name` (textes génériques, partagés avec les macros).
4. **Détection de doublon** (`_name_taken`) dans `state.images_data`, comparaison insensible à la casse sur `orig_name` — en mode édition, l'entrée en cours d'édition elle-même est exclue. Elle est reconnue par son **jeton** (`undo_redo.entry_key`), jamais par identité d'objet. **Piège** : supprimée puis rétablie par un Annuler pendant que la fenêtre est ouverte, l'entrée est recréée sous un autre objet avec le même jeton ; une comparaison par `is` la prenait pour un doublon d'elle-même et refusait l'enregistrement.

Testé par `tests/test_nfo_dialog.py` (fonctions et méthodes extraites par AST).

Aucune validation sur le **contenu** du texte (peut être vide, aucune limite de taille, aucun filtrage de caractères).

## Callbacks réels — définis côté appelant, pas dans ce fichier

`nfo_dialog_qt.py` ne touche jamais directement à `state.images_data` ni à `entry['bytes']` — il se contente d'appeler `inject_fn`/`edit_fn` fournis par l'appelant avec le nom et le contenu validés. La vraie logique métier vit dans `panel_widget.py` :

Création (`PanelWidget._show_nfo_dialog` → `_inject_nfo`) et édition (`PanelWidget._open_nfo_for_edit`, qui capture le jeton de l'entrée — `undo_redo.entry_key` — à l'ouverture, → `_edit_fn`) aboutissent toutes deux à **`PanelWidget._commit_annex_text(key, filename, bytes)`** (`key=None` en création) :

1. Opération en arrière-plan sur le document (rotation…) : la validation est appliquée à sa fin (`state.run_when_idle`, skill `undo-redo`).
2. Retrouve l'entrée **au moment de la validation**, par son jeton (`_entry_by_key`) ; absente (création, ou fichier supprimé pendant l'édition), un nouveau fichier est ajouté par `_append_annex_entry` : **`create_entry(filename, data, IMAGE_EXTS)`** (skill `archive-image-loading`), `.nfo` n'étant pas dans `IMAGE_EXTS` l'entrée a `is_image: False` (icône `icons/nfo.png`), puis `source_archive = "loose"`. **Piège** : la fenêtre est non modale ; écrire dans l'objet capturé à l'ouverture perdait le texte si l'entrée avait été supprimée ou recréée par un Annuler entre-temps.
3. `save_state()` avant et après, sans `force` (skill `undo-redo`) ; `orig_name` et `bytes` réassignés (pas de cache vignette à invalider pour du texte), `st.modified = True`, `sync_pages_in_xml_data(st)` (skill `comicinfo-metadata-editor`), puis `render_mosaic()`, `_refresh_title()`, `_update_status_bar()`, `_refresh_toolbar_states()`.

## Point d'entrée d'édition — double-clic générique sur fichier non-image

**Mécanisme partagé, pas propre à ce skill** : `PanelWidget._open_non_image_entry` (câblée comme `self._canvas._open_non_image_callback`) est le handler de double-clic sur **n'importe quelle** entrée non-image de la mosaïque. Elle route :
- Fichier illisible (`entries.is_unreadable_file`), quel qu'il soit → `ErrorDialog` `messages.errors.unreadable_file` (nom, raison traduite), rien ne s'ouvre (skill `corrupted-images`).
- `.nfo` → `_open_nfo_for_edit` (ce skill).
- `ComicInfo.xml` (insensible à la casse, comparaison sur le nom complet) → `_open_comicinfo_for_edit` (skill `comicinfo-metadata-editor`).
- Tout le reste → ouverture avec l'application Windows par défaut (`_open_file_with_default_app`).

Une tâche qui modifierait ce routage doit vérifier l'impact sur les deux autres branches, pas seulement `.nfo`.

## Points d'entrée UI — création uniquement

Contrairement aux autres skills d'édition d'image, **aucun menu ni bouton n'ouvre le mode édition directement** — le mode édition n'est atteignable que par le double-clic générique décrit ci-dessus. Le mode **création** a 3 points d'entrée classiques, tous inconditionnels (pas de garde-fou de sélection, un `.nfo` peut être créé même mosaïque vide) :

1. **Menu contextuel** (skill `qt-context-menus`) — `show_canvas_context_menu` (`context_menus_qt.py`), clé `nfo.menu_item`.
2. **Barre de menu** — `_populate_file_menu` (`menubar_qt.py`), même clé.
3. **Colonne d'icônes** (skill `icon-toolbar`) — bouton id `"create_nfo"` (`toolbar_callbacks` de `build_icon_toolbar`, `icon_toolbar_qt.py`), tooltip `nfo.tooltip`.

## Menus du clic droit des deux champs

Les menus communs de l'application (skill `qt-context-menus`), traduits et aux couleurs du thème : `setup_lineedit_context_menu` pour le nom du fichier, `setup_textedit_context_menu` pour le contenu (Annuler / Refaire / Couper / Copier / Coller / Tout sélectionner). **Piège** : le champ du nom n'avait aucun menu remplacé (menu natif anglais), et la zone de texte un menu local qui ne suivait pas le thème sombre, sans Annuler ni Refaire.

## Raccourci Home/End personnalisé — `eventFilter`

Intercepte `Home`/`End` (avec ou sans Shift, pour la sélection) dans le `QTextEdit` pour aller au **tout début/toute fin du texte entier** plutôt que début/fin de ligne (comportement Qt par défaut) — reproduit le raccourci `Ctrl+Home`/`Ctrl+End` sur la touche simple. Comportement délibéré (commentaire explicite dans le code), pas un oubli — à ne pas "corriger" vers le comportement Qt standard sans consigne explicite.

## Traductions

`locales/fr.json`, section `nfo` : `window_title`/`window_title_edit` (deux clés distinctes selon le mode, titre de la fenêtre posé une fois par `set_window_title` dans `__init__`), `filename_label`/`content_label`, `btn_create`/`btn_save` (deux clés selon le mode)/`btn_clear`, `menu_item`/`tooltip`, `success_title`/`success_message`, `error_title`/`error_empty_name`/`error_write`/`error_duplicate`. Voir skill `add-translation`.

**A une section dans le mode d'emploi** (liste des sections de `_HelpDialog._build_ui`, `user_guide_qt.py`, clé `help.nfo_editor`/`help.nfo_editor_content`) — comme `page-resize`/`page-crop`/`create-ico` (skill `user-guide`).

## Comment étendre

- **Ajouter une règle sur le nom** : dans `_checked_filename` (une seule fois pour les deux modes), ou dans `_name_error_key` si elle porte sur le nom seul.
- **Ajouter une validation de contenu** (ex. limite de taille) : dans `_on_create`, avant l'appel à `inject_fn`/`edit_fn` — actuellement aucune limite.
- Respecter les 9 règles UI Qt obligatoires du CLAUDE.md pour `_NfoDialog` (non-modale déjà en place, barre de titre dessinée par Qt avec `set_window_title`, skill `window-title`, menus communs du clic droit).

## Pièges connus

- **Ré-encodage systématique en UTF-8 à la sauvegarde**, même si le fichier source était en codage Windows — un `.nfo` non-UTF-8 modifié puis enregistré change de codage.
- **Aucun menu/bouton n'ouvre le mode édition directement** — uniquement accessible via le double-clic générique `_open_non_image_entry`, partagé avec le routage vers `ComicInfo.xml` et l'application par défaut.
- **Fermer la fenêtre avec du texte tapé le perd sans question** : comportement voulu (choix de l'utilisateur), comme l'éditeur ComicInfo.
- **Home/End personnalisés délibérément** (texte entier, pas ligne courante) — comportement voulu, ne pas "corriger" par réflexe.

## Références croisées

- `comicinfo-metadata-editor` — partage le même point d'entrée générique de double-clic (`_open_non_image_entry`) et une forme similaire de callback `edit_fn`, mais format et complexité très différents (XML structuré ~35 champs vs texte libre).
- `archive-image-loading` — `create_entry()` réutilisé pour la nouvelle entrée `.nfo` (comme `animated-gif`, contrairement à `create-ico`).
- `undo-redo` — `save_state` avant et après, `run_when_idle` pendant une opération.
- `apply-image-operation` — hors périmètre direct (pas d'image, pas de cache vignette à invalider) mais comparaison utile sur la mutation en place de `entry['bytes']`, correcte ici pour du texte contrairement à la règle stricte documentée pour les images.
- `icon-toolbar` — bouton "create_nfo" de la colonne d'icônes.
- `qt-context-menus` — entrée du menu contextuel clic droit de la mosaïque, et menus communs des deux champs de la fenêtre.
- `corrupted-images` — fichier `.nfo` illisible (`entries.is_unreadable_file`) : cadre rouge, raison au double-clic.
- `qt-tooltips` — tooltip du bouton colonne d'icônes.
- `user-guide` — section `help.nfo_editor` existante, à maintenir à jour.
