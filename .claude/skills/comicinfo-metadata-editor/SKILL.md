---
name: comicinfo-metadata-editor
description: Localiser ou modifier l'édition/sérialisation du fichier ComicInfo.xml de MosaicView (formulaire complet, synchronisation des balises Pages). Utiliser dès qu'une tâche touche à comic_info.py, comicinfo_dialog_qt.py, parse_comic_info_xml, ou sync_pages_in_xml_data.
---

# Édition des métadonnées ComicInfo.xml — MosaicView

Édite **localement**, sans aucun accès réseau, le fichier `ComicInfo.xml` d'une archive (standard de facto de métadonnées pour comics, un XML embarqué dans le CBZ/CBR/CB7). Formulaire complet de tous les champs standards. Ne pas confondre avec la **récupération** de ces mêmes champs depuis l'API ComicVine (recherche en ligne, téléchargement) — voir skill `comicvine-metadata-fetch`, qui réutilise la sérialisation décrite ici pour écrire ce qu'il télécharge. Ne pas confondre non plus avec l'édition d'un fichier `.nfo` (skill `nfo-editor`, texte libre sans structure) — les deux partagent le même point d'entrée générique de double-clic sur une entrée non-image (`_open_non_image_entry`, `panel_widget.py`), qui route vers l'un ou l'autre selon le nom de fichier.

## Fichiers clés

- **`modules/qt/comic_info.py`** — cœur métier pur (aucun Qt) : parsing XML → dict, sérialisation dict/arbre → bytes au format exact attendu, synchronisation de la section `<Pages>`, lecture depuis une archive sur disque.
- **`modules/qt/comicinfo_dialog_qt.py`** — fenêtre `_ComicInfoDialog`, le formulaire complet (toutes sections, tous champs) en mode création ou édition.
- **`modules/qt/panel_widget.py`** — `PanelWidget._edit_comicinfo()` (ligne ~2413) : point d'entrée qui choisit création vs édition et branche les callbacks d'écriture dans la mosaïque.
- **`modules/qt/library_db.py`** / **`library_window.py`** — la Bibliothèque (voir skill `library`) lit ces mêmes champs (via `parse_comic_info_xml`/`read_comic_info` au scan, `LibraryDB._index_file`) pour les indexer et les rendre cherchables, en lecture seule uniquement — elle n'édite jamais un `ComicInfo.xml`. Après une édition faite depuis ce skill-ci sur un fichier déjà indexé, la Bibliothèque ne se met à jour qu'au prochain scan ou via `reindex_files()` (voir skill `library`) — pas automatiquement.
- **`modules/qt/batch_metadata_dialog_qt.py`** — l'assistant d'import ComicVine **en masse** (voir skill `batch-processing`) construit, pour chaque fichier du lot, un `AppState` allégé (couverture + `ComicInfo.xml` seuls, pas la mosaïque complète) via `_load_state_for_file()`, appelle `parse_comic_info_xml()` pour lire l'existant puis, in fine, `write_comic_metadata_from_scraper()` (décrit plus bas) pour écrire — mêmes fonctions de ce skill-ci, réutilisées telles quelles sur un state minimal plutôt que sur un panneau réel.

## Les deux couches — parsing/sérialisation vs formulaire

### Couche métier pure — `comic_info.py`

- **`parse_comic_info_xml(xml_data)`** — parse un XML `ComicInfo.xml` (bytes) vers un dict à plat (`title`, `series`, `number`... ~35 champs, voir la liste complète dans le fichier) plus une clé `pages` optionnelle (liste de dicts d'attributs `<Page>`) si une section `<Pages>` existe. Retourne `None` sur erreur de parsing (jamais d'exception qui remonte).
- **`_serialize_comic_xml(root, original_bytes=None)`** — reconstruit les bytes XML dans un **format exact** attendu par les autres lecteurs ComicInfo (ComicRack et compatibles) :
  - Déclaration `<?xml version="1.0"?>` sans `encoding` explicite.
  - Fins de ligne `\r\n` (pas `\n`).
  - Balise ouvrante `<ComicInfo>` **extraite telle quelle des bytes originaux** si fournis (préserve un éventuel `xmlns`) — jamais régénérée depuis zéro si `original_bytes` est disponible.
  - Éléments simples : 2 espaces d'indentation, texte échappé (`&`/`<`/`>`/`"`).
  - `<Pages>` : 4 espaces, éléments `<Page ... />` auto-fermants, **ordre d'attributs canonique** `Image, ImageSize, ImageWidth, ImageHeight` puis le reste.
  - `<MosaicViewTrace date="..." url="..." />` : élément auto-fermant à attributs, même traitement que `<Page>` (voir plus bas).
  - **Toujours passer `original_bytes`** (les bytes XML existants) à cette fonction quand on édite un fichier déjà présent — sinon la balise `<ComicInfo>` perd un `xmlns` éventuel écrit par un autre outil.
- **`read_comic_info(filepath)`** — lit un `ComicInfo.xml` directement depuis un fichier CBZ/CBR/CBT **sur disque** (pas depuis `images_data` en mémoire) : utilisé par le scan de la Bibliothèque (voir skill `library`), pas par l'éditeur du panneau (qui lit `entry["bytes"]` directement, déjà en mémoire).

### Couche UI — `comicinfo_dialog_qt.py`

`_ComicInfoDialog` : un unique formulaire (~35 champs répartis en 6 sections : Série, Publication, Classification, Crédits, Contenu, Divers), utilisé en mode **création** (`show_comicinfo_create_dialog`) ou **édition** (`show_comicinfo_edit_dialog`) selon qu'un `ComicInfo.xml` existe déjà dans l'archive (`has_comic_info_entry(state)`).

- **`_FIELDS`** — définition déclarative de toutes les sections/champs `(clé_i18n_titre, [(clé_i18n_label, tag_xml, largeur), ...])`, largeur 1 (demi-ligne, jumelé avec le champ suivant si aussi largeur 1) ou 2 (ligne entière). **Point unique à modifier pour ajouter/réordonner un champ visible dans le formulaire.**
- **`_COMBO_ITEMS`** — champs à liste fixe (`Month`, `Day`, `AgeRating`, `BlackAndWhite`, `Manga`, `SeriesComplete`) : mapping `valeur_xml → clé_i18n_label`. Cas spécial `LanguageISO` (`None` dans le dict) : peuplé dynamiquement par `_get_iso_combo_items()` depuis `locales/language_names.json`, trié par label traduit dans la langue UI courante — pas une liste statique comme les autres combos.
- **`_INT_TAGS`** — champs à `QIntValidator` (Number, Count, Volume, AlternateNumber, AlternateCount, StoryArcNumber, Year).
- **`_AutoResizeTextEdit`** — `QTextEdit` dont la hauteur suit le contenu en continu (grandit/rétrécit à la frappe), utilisé pour tous les champs "longs" (`long_text=True` dans `_FIELDS`, largeur 2 avec du texte potentiellement multi-lignes : Summary, Notes, Writer, Characters, Web...). Recalcul de hauteur en plusieurs passes différées (`_refresh_long_text_heights`, `passes=3`) car l'apparition/disparition de la scrollbar du `QScrollArea` parent change la largeur disponible, qui change à son tour le nombre de lignes nécessaires.
- **`PageCount`** — toujours **non éditable**, recalculé automatiquement depuis `get_current_image_count(state)` (nombre réel d'images dans `images_data`, hors ComicInfo.xml et dossiers) via `_setup_page_count()`, jamais depuis une valeur saisie.
- **`_KEY_TO_TAG`** — mapping clé dict (`comic_metadata`) → tag XML, utilisé pour pré-remplir les champs depuis un dict de métadonnées (`_apply_metadata_to_fields`), que ce dict vienne du parsing local (`_populate_from_entry`) ou d'un scraping ComicVine déjà appliqué (rechargement après `_on_updates_applied`).

## Flux création vs édition (`PanelWidget._edit_comicinfo`, panel_widget.py:2413)

Une seule méthode d'entrée choisit entre les deux modes selon `has_comic_info_entry(st)` :

- **Création** — `_inject_fn(filename, xml_bytes)` : construit une nouvelle `entry` via `create_entry()`, l'ajoute à `st.images_data`, encadrée par `save_state_qt` avant/après (point undo), puis `sync_pages_in_xml_data`, `render_mosaic()`, `_refresh_toolbar_states()`, et recharge les métadonnées affichées (`_reload_comicinfo_metadata`).
- **Édition** — `_edit_fn(new_filename, xml_bytes)` : retrouve l'entrée `ComicInfo.xml` existante dans `images_data`, `self.save_state(force=True)` **avant et après** la modification (2 points undo distincts encadrant le changement — voir skill `undo-redo` pour pourquoi `force=True` est nécessaire ici), remplace `entry["bytes"]` en place, `sync_pages_in_xml_data`, met à jour titre/statusbar/toolbar.

Dans les deux cas, `sync_pages_in_xml_data(state)` est appelée après écriture — voir section suivante, c'est ce qui garde `<Pages>` cohérent avec le contenu réel de la mosaïque.

## Synchronisation de `<Pages>` — `sync_pages_in_xml_data()`

Resynchronise la section `<Pages>` du XML avec l'état courant de `images_data`, après **tout** changement qui peut affecter l'ordre ou le nombre de pages (réordonnancement, ajout, suppression, drag & drop, import).

- Guard : ne fait **rien** si `'pages' not in state.comic_metadata` — un `ComicInfo.xml` qui n'a jamais eu de `<Pages>` n'en gagne pas une automatiquement (sauf cas particulier : `write_comic_metadata_from_scraper` en injecte une vide explicitement si absente, pour que cette fonction ait quelque chose à peupler ensuite).
- **`state._page_attrs_by_entry_id`** (dict `{id(entry): {attr: val}}`) — cache des attributs `<Page>` connus (`ImageWidth`, `ImageHeight`, `ImageSize`, et tout attribut custom d'un autre outil), indexé par **identité d'objet Python** (`id(entry)`), jamais par index — un réordonnancement ne doit pas faire hériter les attributs d'une page à une autre. Reconstruit par `build_page_attrs_map(state)` à appeler après **toute** assignation de `state.comic_metadata` (chargement de fichier, undo/redo) — sinon ce cache reste périmé et pointe vers des objets `entry` qui n'existent plus.
- Pour une entrée sans attributs connus (nouvelle page), calcule `ImageWidth`/`ImageHeight` (PIL, décode `entry["bytes"]`) et `ImageSize` (longueur des bytes) à la volée — coûteux sur un gros lot de pages neuves, mais nécessaire pour rester complet.
- Recalcule aussi `PageCount` en cohérence avec le nombre réel d'entrées image.
- `emit_signal=False` : utile depuis un thread worker (ne pas émettre de signal Qt hors du thread UI) — l'appelant doit alors déclencher lui-même un rafraîchissement UI après coup.

**`update_page_entries_in_xml_data(state, entries_with_idx)`** — variante plus ciblée : met à jour uniquement `ImageWidth`/`ImageHeight`/`ImageSize` pour une liste explicite de `(page_image_index, entry)` déjà modifiées (ex. après un crop/resize sur une sélection), sans reconstruire toute la section `<Pages>`. À préférer à `sync_pages_in_xml_data` complet quand seul le contenu de quelques pages a changé, pas leur ordre/nombre — voir skill `apply-image-operation` pour le pattern général d'invalidation après modification de `entry["bytes"]`. Appelée notamment par `apply_image_adjustments()` (`image_processing_qt.py`) après chaque commit d'un outil d'ajustement de la barre d'outils de la visionneuse (skill `viewers`), et séparément par `perform_transparency()` (`transparency_tool_qt.py`, skill `adjust-transparency`) qui a son propre chemin d'application hors de `apply_image_adjustments()`.

**`get_page_image_index(state, entry)`** — traduit une `entry` en son index `<Page Image="N">` (position parmi les images réelles, hors dossiers et hors ComicInfo.xml) — nécessaire pour appeler `update_page_entries_in_xml_data` correctement.

## Écriture depuis un scraping ComicVine — `write_comic_metadata_from_scraper()`

Point de jonction avec le skill `comicvine-metadata-fetch` : cette fonction (dans `comic_info.py`, pas dans le module scraper) reçoit un dict `meta` déjà téléchargé (`get_issue_details`) et l'applique au `ComicInfo.xml` local :

1. Retrouve ou crée l'entrée `ComicInfo.xml` dans `images_data` (essaie UTF-8 puis fallback latin-1 sur le XML existant si le parsing UTF-8 échoue).
2. Applique chaque champ de `_SCRAPER_FIELD_MAP` (sous-ensemble des champs que le scraper renseigne réellement — pas les 35 champs du formulaire complet) **seulement si `meta[field]` est non vide** — un champ vide côté ComicVine ne réinitialise jamais un champ local déjà rempli.
3. Si une URL `web` a été téléchargée, ajoute/remplace une ligne de traçabilité dans `Notes` : `"MosaicView: metadata retrieved on {YYYY-MM-DD}."` (`_update_trace_note`) — préserve le reste du contenu de `Notes` (texte utilisateur ou d'un autre outil), ne remplace que la ligne portant ce préfixe exact.
4. Régénère `state.comic_metadata` depuis les nouveaux bytes et injecte une `<Pages>` vide si absente, pour que `sync_pages_in_xml_data` (appelé juste après) ait quelque chose à peupler.
5. Émet `metadata_signal` (signal Qt global, voir `modules/qt/metadata_signal.py`) pour que l'onglet métadonnées (voir skill `tabs` pour son fonctionnement, y compris le piège du signal global partagé entre panel1/panel2) et toute autre UI affichant `comic_metadata` se rafraîchissent.

**`diff_comic_metadata(local_meta, remote_meta)`** — compare champ par champ sur `_DIFF_FIELDS` (= `_SCRAPER_FIELD_MAP` moins `imprint`, jamais renseigné par le scraper) pour la fonctionnalité "Vérifier les mises à jour" — voir skill `comicvine-metadata-fetch` pour le flux complet côté UI (`_UpdateDiffDialog`).

## `MosaicViewTrace` — élément de traçabilité à attributs

Distinct de la ligne texte dans `Notes` : un élément auto-fermant `<MosaicViewTrace date="..." url="..." />`, sérialisé avec le même traitement que `<Page>` (ordre d'attributs canonique `date, url` puis le reste). Préservé explicitement lors d'une réédition manuelle du formulaire (`_ComicInfoDialog._build_xml_bytes`, mode édition) via un `orig_root.find("MosaicViewTrace")` recopié tel quel dans le nouvel arbre — ne pas le perdre en modifiant `_build_xml_bytes` ou une fonction similaire.

## Sécurité — parsing XML durci

`comic_info.py` utilise `defusedxml.ElementTree.fromstring` si disponible (fallback silencieux vers `xml.etree.ElementTree.fromstring` sinon) : un `ComicInfo.xml` peut venir d'une **archive téléchargée**, donc potentiellement malveillante (attaque XXE via entités externes). **Toujours importer `_safe_fromstring` depuis `comic_info.py`** pour parser un `ComicInfo.xml`, jamais `ET.fromstring` directement dans un nouveau call-site — `comicinfo_dialog_qt.py::_build_xml_bytes` le fait déjà correctement en mode édition (commentaire explicite dans le code à ce sujet).

## Comment étendre

- **Ajouter un nouveau champ ComicInfo standard** : l'ajouter (a) à `parse_comic_info_xml()` (`comic_info.py`, une ligne `metadata['xxx'] = root.findtext('Xxx', '')`), (b) à `_FIELDS` (`comicinfo_dialog_qt.py`, section + largeur), (c) à `_KEY_TO_TAG` si le champ doit pouvoir être pré-rempli depuis un dict externe (scraping ComicVine ou import), (d) à `_SCRAPER_FIELD_MAP` dans `comic_info.py` seulement si ComicVine renseigne réellement ce champ (sinon laisser de côté, voir le cas `imprint` déjà présent mais jamais rempli).
- **Ajouter un champ à liste fixe (combo)** : `_COMBO_ITEMS` (`comicinfo_dialog_qt.py`) avec la liste `(valeur_xml, clé_i18n)` — l'entrée vide `("", "")` en tête est une convention systématique (affichée en gris/italique via `comicinfo.combo_empty`), à reproduire pour tout nouveau combo.
- **Changer le format de sérialisation XML** (indentation, ordre d'attributs, encodage) : uniquement `_serialize_comic_xml()` — fonction testable sans Qt, mais **attention à la compatibilité** avec les autres lecteurs ComicInfo (ComicRack et dérivés) qui peuvent être stricts sur le format exact ; ne pas changer sans raison forte.
- **Ajouter les clés de traduction d'un nouveau champ** dans tous les fichiers `locales/*.json` — voir skill `add-translation`.

## Pièges connus

- **Ne jamais régénérer `<ComicInfo>` sans `original_bytes`** en mode édition — perdrait un `xmlns` custom d'un autre outil qui aurait écrit ce fichier.
- **`state._page_attrs_by_entry_id` indexé par `id(entry)`, jamais par position** — un réordonnancement de pages ferait hériter à tort les attributs `<Page>` d'une image à une autre si indexé par position (même piège que documenté dans le skill `duplicate-detection` pour un cache différent, mais la même leçon).
- **`sync_pages_in_xml_data` ne fait rien si `'pages' not in comic_metadata`** — un `ComicInfo.xml` créé sans jamais avoir eu de section `<Pages>` (ex. import minimal) reste sans `<Pages>` tant que rien ne l'injecte explicitement (voir l'injection volontaire dans `write_comic_metadata_from_scraper` si ce comportement doit être répliqué ailleurs).
- **`PageCount` du formulaire est en lecture seule** — toujours dérivé du nombre réel d'images, ne jamais le rendre éditable sans réexaminer tous les points qui en dépendent (statusbar, bibliothèque).
- **Le bouton "Vérifier les mises à jour" n'apparaît que si `get_source_comicvine_issue_id()` retourne un id** (voir skill `comicvine-metadata-fetch` pour le détail de cette fonction et ses limites).
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour toute modification de `_ComicInfoDialog` (non-modale déjà en place, `_wt()` pour le titre déjà en place, retraduction dynamique des ~35 labels + combos déjà en place — modèle à suivre pour tout ajout de champ).
