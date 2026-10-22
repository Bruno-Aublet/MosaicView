# Rapports d'audit — MosaicView

Ce fichier regroupe les rapports des audits de code successifs de MosaicView. Chaque audit a sa propre section, ajoutée à la suite des précédentes ; les constatations d'un audit ne sont jamais réécrites après coup — un problème corrigé reste décrit tel qu'il avait été constaté, suivi de sa correction.

Il est conçu pour être lu sans accès aux conversations dans lesquelles les audits ont été menés, notamment pour établir une synthèse de plusieurs audits.

**Conventions communes à tous les audits**

- **Priorités** : P0 perte ou corruption de données · P1 état interne incohérent ou mauvais contexte · P2 comportement incorrect sans perte de données · P3 robustesse, ressources, nettoyage · P4 maintenabilité.
- **Certitude** : *Confirmed* (démontré par le code, et le plus souvent reproduit) · *Very likely* (mécanisme établi, reproduction non tentée) · *Possible* (scénario plausible, conditions rares ou non vérifiées).
- **Emplacements** : fichier + symbole (`module.py` → `Classe.méthode`, fonction). Pas de numéros de ligne : ils se décalent à chaque modification.
- **Statut** : *Corrigé* (correction appliquée, testée par l'utilisateur dans l'application, et couverte par un test automatique quand le code peut s'exécuter sans Qt) · *Non corrigé* (avec la raison).

## Sommaire

1. [Audit 1 — Interactions entre systèmes (version 1.8.7)](#audit-1--interactions-entre-systèmes-version-187)
2. [Audit 2 — Opérations destructives et conservation des données (version 1.8.8)](#audit-2--opérations-destructives-et-conservation-des-données-version-188)
3. [Audit 3 — Machines à états et transitions impossibles (version 1.8.8)](#audit-3--machines-à-états-et-transitions-impossibles-version-188)
4. [Audit 4 — Asynchronisme, concurrence et opérations en arrière-plan (version 1.8.8)](#audit-4--asynchronisme-concurrence-et-opérations-en-arrière-plan-version-188)
5. [Audit 5 — Préservation des formats et des données lors des conversions (version 1.8.8)](#audit-5--préservation-des-formats-et-des-données-lors-des-conversions-version-188)
6. [Audit 6 — Sécurité, fichiers malformés et entrées hostiles (version 1.8.9)](#audit-6--sécurité-fichiers-malformés-et-entrées-hostiles-version-189)
7. [Audit 7 — Gestion des erreurs, des annulations et des retours utilisateur (version 1.8.11)](#audit-7--gestion-des-erreurs-des-annulations-et-des-retours-utilisateur-version-1811)

---

# Audit 1 — Interactions entre systèmes (version 1.8.7)

- **Période** : octobre 2026 (audit, puis corrections publiées dans la version 1.8.8).
- **Objet** : rechercher les bugs qui apparaissent quand plusieurs systèmes, fiables pris isolément, sont utilisés ensemble — sauvegarde, conversions, traitements par lot, fermeture, annulation, opérations en arrière-plan, double panneau (split view), Bibliothèque, macros, PDF, images animées, modes d'image.
- **Méthode** : lecture du code réel (`MosaicView.py`, `modules/qt/`), des skills de `.claude/skills/` et de `CLAUDE.md` (pour distinguer un comportement voulu d'un bug), des tests de `tests/` ; suivi des flux d'exécution aux points de jonction entre systèmes ; reproduction des échecs suspects avec Pillow quand c'était possible ; élimination des fausses pistes. Aucune modification de code avant présentation du rapport initial ; corrections ensuite, une par une, validées par l'utilisateur dans l'application.
- **Code de référence** : MosaicView 1.8.7, PySide6 (Qt), Pillow, PyMuPDF (process séparé pour le PDF), SQLite (Bibliothèque).

## Executive summary

| Priorité | Constats | Certitude | Statut |
|---|---|---|---|
| **P0** — perte ou corruption de données | 7 | 7 Confirmed | 7 corrigés |
| **P1** — état ou contexte incohérent | 8 | 5 Confirmed, 2 Very likely, 1 Possible | 8 corrigés |
| **P2** — comportement incorrect | 8 | 3 Confirmed, 1 Very likely, 4 Possible | 8 corrigés (dont un par la correction d'un P1) |
| **P3** — robustesse et ressources | 4 | 1 Confirmed, 3 Possible | 4 corrigés |
| **P4** — maintenabilité | 4 | — | 2 traités, 2 ouverts |
| **Constats ajoutés pendant les corrections** | 6 | Confirmed | 6 corrigés |

Une douzaine de fausses pistes ont été examinées et écartées (voir [Fausses pistes écartées](#fausses-pistes-écartées)).

**Principales zones à risque identifiées**

1. **Les sauvegardes dont la fin est différée** (`apply_new_names`, `create_cbz_from_images`) : le document était déclaré sauvegardé à la fermeture de la fenêtre de confirmation, pas au moment de l'écriture — des modifications faites entre-temps étaient marquées sauvegardées, puis perdues.
2. **Le PDF** : une page qui ne se rendait pas était ignorée en silence, partout (ouverture, fusion, lot PDF→CBZ, import de métadonnées en lot) — avec suppression du PDF source dans les lots.
3. **L'import de métadonnées en lot** sur des fichiers non-CBZ : écrasement d'un CBZ existant, perte de pages TIFF/JFIF et de fichiers annexes avant suppression de l'original, échecs d'écriture comptés comme réussis.
4. **Les listes et index figés** : `state.all_entries` (copie prise au chargement), indices de page capturés à l'ouverture d'une fenêtre non modale puis réutilisés à sa validation.
5. **L'absence de coordination entre écrivains d'un même fichier** : deux panneaux ouverts sur le même fichier, Bibliothèque, lots.
6. **L'absence d'exclusion** entre une opération en arrière-plan et Annuler/Refaire dans le même document.

**Cause commune la plus fréquente** : une erreur avalée en silence dans une boucle (`except Exception: continue` / `pass`), qui fait disparaître une page sans que personne le sache. Elle est à l'origine de P0-2, P0-5, P1-7 et des deux constats C-5 et C-6 ajoutés en cours de correction. Un test de garde l'interdit désormais (voir [P4-4](#p4-4--des-except-exception-continue-dans-des-boucles)).

**Solidité constatée** : l'isolation des deux panneaux (state explicite par panneau, test AST anti-singleton), le remplacement atomique des CBZ (`replace_file_atomically`), la gestion des macros et les lots CBR/CB7/CBT étaient déjà robustes (voir [Zones sans problème trouvé](#interaction-areas-with-no-issues-found)).

### Causes communes

| Cause | Constats concernés |
|---|---|
| Erreur avalée en silence dans une boucle de pages ou de fichiers | P0-2, P0-5, P1-7, C-5, C-6 |
| Index, sélection ou image capturés à l'ouverture d'une fenêtre non modale, relus à sa validation | P1-3, P1-4, P2-1, P2-3 |
| Plusieurs implémentations de « réécrire un CBZ » aux garanties divergentes | P0-3, P0-6, P2-2 (→ P4-1) |
| Convention « sauvegarder l'état avant **et** après » non imposée | P1-2 (→ P4-2) |
| Liste figée `all_entries` sans règle de mise à jour | P0-4 (→ P4-3) |
| Aucune notion d'« opération en cours sur ce document » | P1-1, P2-3 |
| Fin d'opération différée qui ne vérifie pas que le contexte n'a pas changé | P0-1, P1-5, P1-6, P2-7 |
| `id()` Python utilisé comme identité durable | P2-5 |

---

## P0 — Data loss / corruption

### P0-1 · Des modifications faites pendant la confirmation « Enregistré » sont marquées sauvegardées, puis détruites par « Enregistrer et fermer »

- **Certitude** : Confirmed · **Gravité** : haute · **Statut** : Corrigé
- **Code** : `file_operations_qt.py` → `apply_new_names` (branches CBZ, CBR, PDF), `_finish_apply_new_names`, `create_cbz_from_images` ; `file_close_qt.py` → `close_file` (`_apply_and_close`, `_yes`).
- **Constat** : après l'écriture du CBZ, une fenêtre de confirmation **non modale** (`InfoDialogClickablePath`) s'affiche ; la fin de la sauvegarde n'est exécutée qu'à sa fermeture. Pendant ce temps, l'utilisateur peut continuer à modifier le document. À la fermeture de la confirmation, `_finish_apply_new_names` force `state.modified = False` et appelle `mark_history_saved`, sans vérifier que le document est encore celui qui a été écrit.
- **Scénario** : CBZ modifié → croix de l'onglet → « Enregistrer et fermer » → le CBZ est écrit, la confirmation s'affiche → sans la fermer, l'utilisateur supprime une page, pivote une image → il clique OK.
- **Comportement constaté** : le document est marqué non modifié, puis `_on_complete` appelle `_force` : il est fermé **sans confirmation** ; les modifications de l'étape 3 sont perdues. Sans fermeture, même effet différé : `modified = False` alors que le disque ne contient pas ces modifications, et la fermeture suivante ne demande rien. Le chemin `create_cbz_from_images` ne vérifiait que `doc_generation` (document remplacé), pas une modification du même document.
- **Attendu** : ne déclarer sauvegardé (et ne fermer) que si le document est resté identique à ce qui a été écrit. `save_as_cbz` le faisait déjà, en comparant le sommet de l'historique (`history_top`) avant et après l'écriture.
- **Correction** : `undo_redo.py` → `content_snapshot(state)` capture le contenu au moment de l'écriture (références, sans copie des bytes) ; `content_unchanged(state, snapshot)` le compare au contenu courant (mêmes entrées, mêmes noms, mêmes bytes). `_finish_apply_new_names` et la fin de `create_cbz_from_images` ne marquent le document sauvegardé, et ne rendent `True` à la fermeture, que si `doc_generation` est inchangé **et** `content_unchanged`. Sinon `on_complete(False)` : le document reste ouvert et modifié.
- **Tests** : `tests/test_undo_redo_keys.py` (`content_snapshot`/`content_unchanged` : suppression, remplacement de bytes, renommage, page remplacée par une copie identique). Le flux complet (fenêtre de confirmation) dépend de Qt : test manuel.
- **Interactions** : sauvegarde × fenêtres non modales (règle UI n° 4 de `CLAUDE.md`) × fermeture × historique.

### P0-2 · Une page PDF qui ne se rend pas est ignorée en silence, puis le PDF source est supprimé

- **Certitude** : Confirmed · **Gravité** : haute · **Statut** : Corrigé
- **Code** : `pdf_loading_qt.py` → process PDF `_pdf_persistent_process` (fonctions `_convert`, `_convert_merge`, branche `batch_convert`), `PdfLoadWorker`, `PdfMergeWorker`, `PdfLoader._on_finished`, `import_and_merge_pdf` ; `batch_dialogs_qt.py` → `_run_pdf_conversion` ; `batch_metadata_dialog_qt.py` → `_save_state_for_file` (manifeste PDF).
- **Constat** : dans le process PDF, chaque boucle de rendu de page était protégée par `except Exception: continue`. Une page corrompue, ou qui provoquait une `MemoryError` au rendu (jusqu'à 2400 dpi), était sautée sans aucun message, puis `'done'` était envoyé comme si tout s'était bien passé.
- **Scénario** : un PDF partiellement corrompu passe dans la conversion PDF→CBZ par lot.
- **Comportement constaté** : le CBZ produit a N−1 pages, il est compté comme converti, et **le PDF source est supprimé**. Même cause ailleurs : import de métadonnées en lot sur un PDF (même suppression de la source) ; ouverture d'un PDF dans un panneau (document incomplet sans avertissement, et « Appliquer et créer un CBZ » propose ensuite de supprimer le PDF).
- **Contraste** : les lots CBR/CB7/CBT géraient déjà ce cas (`_copy_pages_to_cbz` renvoie les pages en échec, `_discard_incomplete_cbz` conserve la source).
- **Correction** :
  - le process envoie `('page_error', page_num, message)` (`_send_page_error`) pour chaque page non rendue, puis continue ;
  - `PdfLoadWorker`/`PdfMergeWorker` accumulent ces messages bruts dans `page_errors` ; `PdfLoader._on_finished` et `import_and_merge_pdf` les affichent dans la fenêtre « Fichiers ignorés » (`_warn_pdf_pages_ignored`, texte recalculé à chaque changement de langue) ;
  - le lot PDF compte les pages écrites (`pages_written`) et les échecs (`page_failures`) : si `pages_written != total_pages` ou s'il y a un échec, le CBZ est supprimé, le PDF conservé, et l'erreur consignée au journal ;
  - l'import de métadonnées en lot lève une exception sur `page_error` (CBZ abandonné, PDF conservé).
- **Tests** : non automatisables en l'état (la boucle du lot PDF est une fonction imbriquée de `_run_pdf_conversion`, le process est un sous-processus PyMuPDF) ; test manuel. **Test manquant** : un faux process qui saute une page, pour vérifier que le PDF est conservé (nécessite d'extraire la boucle de `_run_pdf_conversion`).
- **Interactions** : PDF × lot × suppression de la source × import de métadonnées × ouverture/fusion.

### P0-3 · L'import de métadonnées en lot (fichiers non-CBZ) écrase un CBZ existant et perd des pages

- **Certitude** : Confirmed · **Gravité** : haute · **Statut** : Corrigé
- **Code** : `batch_metadata_dialog_qt.py` → `_BatchMetadataOrchestrator._load_state_for_file`, `_save_state_for_file`.
- **Constat** : trois défauts indépendants.
  - **a. Écrasement** : la cible d'un CBR/CB7/CBT/PDF converti était `X.cbz`, sans test d'existence, puis `os.replace`. Dans un dossier contenant `X.cbr` **et** `X.cbz`, `X.cbr` est traité en premier (tri naturel) : le `X.cbz` existant est écrasé définitivement (sans corbeille), puis `X.cbr` est supprimé. Les autres chemins de conversion évitaient ce cas (suffixe « (1) » dans les lots, refus dans la Bibliothèque).
  - **b. Liste blanche d'extensions** : les branches CBR/CBT/CB7 ne recopiaient que `.jpg .jpeg .png .gif .webp .bmp .avif`. Les pages `.tif/.tiff/.jfif` et les fichiers annexes (`.nfo`, `.txt`) étaient perdus — puis l'original était supprimé.
  - **c. PDF** : même problème que P0-2.
  - Défaut associé : le manifeste CB7 n'était posé qu'à l'intérieur d'un `if`, ce qui pouvait laisser un CB7 sans manifeste.
- **Correction** : cible libre `X (n).cbz` si `X.cbz` existe ; recopie de **tous** les fichiers de l'archive source (aucune liste blanche), sauf l'ancien `ComicInfo.xml` (`_is_comicinfo_name`, à la racine ou dans un dossier), remplacé par le nouveau ; manifeste CB7 posé hors du `if` ; `page_error` du PDF → exception.
- **Tests** : `tests/test_batch_metadata_save.py` (CBZ : tous les fichiers recopiés, ComicInfo remplacé, pas de doublon ; CBT : pages TIFF/JFIF et `.nfo` conservées, ancien ComicInfo de sous-dossier exclu, original supprimé seulement après succès ; échec de lecture : original intact).
- **Interactions** : lot de métadonnées × conversion de format × suppression de la source × fichiers existants.

### P0-4 · « Aplatir les répertoires » reconstruit le document depuis une liste figée au chargement

- **Certitude** : Confirmed · **Gravité** : haute · **Statut** : Corrigé
- **Code** : `flatten_directories_qt.py` → `flatten_directories` ; `state.py` → `AppState.all_entries`.
- **Constat** : l'aplatissement partait de `state.all_entries` quand elle était non vide. Cette liste n'était réaffectée qu'au chargement, à la fusion, à l'aplatissement et à l'undo — ni la suppression de pages, ni la découpe, la conversion, le collage, le dépôt entre panneaux ou la création GIF/ICO ne la mettaient à jour.
- **Scénario** : CBZ avec sous-dossiers → supprimer 3 pages, découper une page, convertir une page en WebP avec « supprimer les originaux » → Aplatir.
- **Comportement constaté** : les 3 pages supprimées et l'original converti **réapparaissent** ; les morceaux de découpe et la page convertie **disparaissent**. Récupérable par Annuler, mais invisible sur 200 pages ; perte réelle si l'utilisateur sauvegarde.
- **Correction** : aplatissement à partir de `state.images_data`, qui est toujours la liste complète du document (la navigation dans les sous-dossiers ne filtre que l'affichage). La liste `all_entries` a ensuite été supprimée entièrement (voir P4-3).
- **Tests** : test manuel (le module dépend de Qt). Garde indirecte : `all_entries` n'existe plus.
- **Interactions** : aplatissement × suppression × découpe × conversion × collage × double panneau × historique.

### P0-5 · Conversion de format : « Supprimer les originaux » retire aussi les pages qui n'ont pas pu être converties

- **Certitude** : Confirmed (échecs reproduits avec Pillow) · **Gravité** : haute · **Statut** : Corrigé
- **Code** : `conversion_dialogs_qt.py` → `_ConversionWorker.run`, `show_conversion_complete_dialog` (`_handle_action`), `perform_conversion` ; `image_ops.py` → `convert_image_data`.
- **Constat** : le worker ignorait l'erreur renvoyée par `convert_image_data` ; la fenêtre de fin retirait **tous** les `selected_entries`, convertis ou non.
- **Échecs réels vérifiés** : page de webtoon 800×17000 → WebP (`Image size exceeds WebP limit of 16383 pixels`) ; PNG gris 16 bits → JPEG (`cannot write mode I;16 as JPEG`).
- **Comportement constaté** : aucune erreur affichée, et « Supprimer les originaux » efface une page qui n'existe sous aucune autre forme.
- **Correction** :
  - le worker tient la liste des sources réellement converties (`converted_sources`) et des échecs (`failures`) ; « Supprimer les originaux » ne retire que les sources converties ;
  - une fenêtre « Conversion impossible » explique chaque échec, page par page : image trop grande pour le format (`FORMAT_MAX_SIZE` : WebP 16383, AVIF 32768, JPEG 65500, GIF 65535 pixels de largeur ou de hauteur), image illisible ou endommagée, mémoire insuffisante, erreur inattendue (`convert_image_data` renvoie `(None, {"kind": ...})`) ;
  - les vrais échecs de conversion ont été supprimés : `_to_writable_mode` ramène 16 bits (`I;16*`) → L, entiers 32 bits/flottants (`I`, `F`) → L par mise à l'échelle (et non écrêtage), CMYK/YCbCr/LAB/HSV → RGB, mode 1 → L avant GIF ; les très grandes pages en AVIF ne deviennent plus illisibles.
- **Tests** : `tests/test_image_frames_and_modes.py` (page trop haute pour WebP → `too_large` avec dimensions et limite ; image illisible → `unreadable` ; PNG 16 bits → JPEG converti ; modes 32 bits mis à l'échelle). La sélection des originaux à supprimer est dans une fenêtre Qt : test manuel.
- **Interactions** : conversion × suppression × modes d'image × formats.

### P0-6 · Aucune coordination entre écrivains d'un même fichier

- **Certitude** : Confirmed (absence de garde vérifiée dans le code) · **Gravité** : moyenne à haute · **Statut** : Corrigé (selon la décision de l'utilisateur)
- **Code** : `panel_widget.py` → `_load_files` ; `file_operations_qt.py` → `_write_apply_new_names`, `save_as_cbz` ; `library_window.py` → `_rewrite_comicinfo_in_cbz` ; `batch_metadata_dialog_qt.py` → `_save_state_for_file`.
- **Constat** : rien ne vérifiait qu'un fichier ouvert n'avait pas été réécrit ailleurs depuis son ouverture.
- **Scénarios** :
  - **a.** `X.cbz` ouvert dans les deux panneaux (cas prévu par le code des marque-pages). On sauvegarde A, puis B : les modifications de A sont écrasées sans avertissement.
  - **b.** `X.cbz` ouvert dans un panneau ; ses métadonnées sont éditées depuis la Bibliothèque (ou par un import ComicVine en lot sur le dossier) ; on sauvegarde le panneau : il réécrit son ancien `ComicInfo.xml`, et la base affiche des métadonnées qui ne sont plus dans le fichier.
- **Décision de l'utilisateur** : ouvrir un même fichier dans les deux panneaux est légitime ; **aucun avertissement à l'ouverture**. Protection retenue : vérifier au moment de l'écriture.
- **Correction** : `utils.py` → `file_signature(path)` = `(st_mtime_ns, st_size)` ; `state.disk_signature` est mémorisée au chargement (`ArchiveLoader._on_finished`, remise à `None` par `load()` et par `force_close_file`) et après chaque écriture de l'application (CBZ, conversions, Enregistrer sous, création de CBZ). Avant de réécrire le fichier du document, `_confirm_if_changed_on_disk` compare la signature : si le fichier a changé ailleurs, un `ConfirmYNDialog` non modal demande confirmation, réponse **Non** par défaut, avec garde `doc_generation` pour la réponse différée.
- **Tests** : test manuel (dialogue Qt). **Test manquant** : `_confirm_if_changed_on_disk` avec signature modifiée (nécessite d'extraire la décision du dialogue).
- **Interactions** : double panneau × sauvegarde × Bibliothèque × lots × programmes externes.

### P0-7 · L'export de plusieurs pages vers un dossier écrase les fichiers existants sans confirmation, et pas de façon atomique

- **Certitude** : Confirmed · **Gravité** : moyenne · **Statut** : Corrigé
- **Code** : `file_operations_qt.py` → `save_selection_to_folder` (branche multi-sélection).
- **Constat** : `open(file_path, "wb")` direct pour chaque page. Exporter `01.jpg … 20.jpg` vers un dossier qui contient déjà `01.jpg` (cas très courant) remplaçait les fichiers sans question ; un échec en cours d'écriture (disque plein) tronquait le fichier existant. En sélection simple, le dialogue natif demandait confirmation : comportement incohérent.
- **Correction** : liste des cibles figée à l'ouverture ; si des fichiers existent, une seule question pour tous (`ExistingFilesDialog`, deux boutons Remplacer/Annuler, **tout ou rien** — l'utilisateur a refusé un export partiel) ; écriture par `_write_file_safely` (temporaire du même dossier, `replace_file_atomically`). Ajout demandé par l'utilisateur : la fenêtre d'export s'ouvre dans le dernier dossier d'export utilisé (`last_export_dir`).
- **Tests** : `replace_file_atomically` est couvert par `tests/test_utils_atomic_write.py` et `tests/test_utils_replace_retry.py` ; la question à l'utilisateur est en Qt : test manuel.
- **Interactions** : export × fichiers existants × disque plein.

---

## P1 — State / context integrity

### P1-1 · Aucune exclusion entre une opération en arrière-plan et Annuler/Refaire dans le même document

- **Certitude** : Very likely · **Statut** : Corrigé
- **Code** : `image_transforms_qt.py` (rotation/miroir), `deskew_qt.py` (redressement automatique), `resize_dialog_qt.py` (redimensionnement), `conversion_dialogs_qt.py` (conversion) ; `undo_redo_qt.py` → `undo_action_qt`, `redo_action_qt` ; `state.py`.
- **Constat** : ces opérations protégeaient contre un **changement de document** (`doc_generation`), pas contre un Annuler ou une modification du **même** document. Le drapeau `state.converting` (« pour bloquer les événements pendant la conversion ») n'était lu nulle part.
- **Scénario** : rotation de 50 pages, Ctrl+Z pendant le traitement (réflexe naturel pour l'annuler).
- **Comportement possible** : l'Annuler défait l'action **précédente** ; le worker continue d'écrire dans des entrées restaurées ; son `save_state()` final tronque la branche Refaire. Résultat : action précédente perdue, pages à moitié pivotées.
- **Correction** : `AppState.running_operations` (`begin_operation(token)`, `end_operation(token)`, `operation_running()`), inscrite par les quatre opérations au démarrage et retirée sur tous leurs chemins de fin ; vidée par `force_close_file`. Tant qu'une opération tourne : `undo_action_qt`/`redo_action_qt` sont sans effet (Ctrl+Z/Ctrl+Y, menu Édition, colonne d'icônes, visionneuse passent tous par elles) et les icônes Annuler/Refaire sont grisées (`icon_toolbar_qt.py`, state_getters `has_undo`/`has_redo`).
- **Tests** : `tests/test_operation_running.py`.
- **Interactions** : opérations en arrière-plan × historique × visionneuse × colonne d'icônes.

### P1-2 · Découpe de page et Couper (Ctrl+X) sans `save_state()` après l'opération

- **Certitude** : Confirmed · **Statut** : Corrigé
- **Code** : `split_dialog_qt.py` → `split_page` (`_on_confirmed`) ; `clipboard_qt.py` → `cut_selected`.
- **Constat** : la règle du skill undo-redo (« `save_state()` avant **et** après une modification ») n'était pas appliquée : un Annuler juste après défaisait la découpe **et** l'action précédente ; Refaire ne restaurait pas la découpe. Aggravant pour Couper : un échec de copie vers le presse-papiers (exception avalée) supprimait quand même les pages.
- **Correction** : `save_state()` après la découpe et après la coupe ; `_copy_entries_to_system_clipboard`/`copy_to_system_clipboard` renvoient un booléen, et `cut_selected` ne retire les pages que si la copie a réussi.
- **Tests** : test manuel (Qt). Cause de fond non résolue : voir P4-2.
- **Interactions** : découpe/coupe × historique × presse-papiers.

### P1-3 · Indices et images capturés à l'ouverture d'un dialogue non modal

- **Certitude** : Confirmed · **Statut** : Corrigé
- **Code** : `split_dialog_qt.py` → `split_page` (capturait `idx`, `entry` et `img`) ; `ico_creator_qt.py` → `IcoCreatorDialog._on_validate_final` (insérait à `self._idx + 1`).
- **Constat** : si l'utilisateur supprime ou réordonne des pages pendant que la fenêtre est ouverte, les morceaux sont insérés au mauvais endroit ; si la page a été pivotée entre-temps, la découpe se fait sur l'image d'avant la rotation.
- **Correction** : à la validation, la page est retrouvée **par identité** (`e is entry`) dans `images_data` et son image relue (`free_image_memory` puis `ensure_image_loaded`) ; page disparue → rien n'est fait. Même principe pour le `.ico`.
- **Tests** : test manuel (Qt).

### P1-4 · La suppression efface la sélection présente au moment de « Oui », pas celle annoncée

- **Certitude** : Confirmed · **Statut** : Corrigé
- **Code** : `panel_widget.py` → `_delete_selected_qt` (`_do_delete`).
- **Constat** : le dialogue annonce « 3 pages (x Mo) », mais `_do_delete` relisait `selected_indices` à la validation. L'utilisateur change de sélection, valide, et d'autres pages sont supprimées.
- **Correction** : les pages à supprimer sont capturées à l'ouverture de la question et supprimées par identité.
- **Tests** : test manuel (Qt).

### P1-5 · Fermer un fichier pendant qu'une visionneuse a du travail non validé la laisse ouverte

- **Certitude** : Very likely · **Statut** : Corrigé
- **Code** : `file_close_qt.py` → `close_file`, `force_close_file` ; `image_viewer_qt.py` → `ImageViewer._has_unvalidated_work`, `closeEvent` ; `MosaicView.py` → `MainWindow.closeEvent`.
- **Constat** : seule la fermeture de l'application traitait la visionneuse avant le fichier. Fermer un onglet (ou ouvrir un fichier depuis la Bibliothèque) vidait le document, puis la visionneuse refusait de se fermer (travail non validé). Elle restait ouverte sur un document vidé ; si l'utilisateur répondait « Non » puis chargeait un autre fichier, valider le recadrage l'appliquait à la page de même index du **nouveau** comic.
- **Correction** : `close_file` traite d'abord toute visionneuse de ce panneau qui a du travail non validé (sa propre question `viewer.unvalidated_work_*`) et renvoie `False` sans fermer le fichier ; l'utilisateur relance la fermeture.
- **Tests** : test manuel (Qt).
- **Interactions** : fermeture × visionneuse × Bibliothèque × double panneau.

### P1-6 · La Bibliothèque est réindexée dans la mauvaise base, ou pas du tout

- **Certitude** : Confirmed · **Statut** : Corrigé
- **Code** : `panel_widget.py` → `_on_file_saved`, `_load_files` ; `library_db.py` → `LibraryDB.reindex_files` ; `library_window.py` → `_open_in_mosaicview`, `_write_comicinfo_and_reindex`, `_convert_file_to_cbz`, `_open_comicinfo_editor`, `_action_fetch_metadata`.
- **Constat** :
  - `_on_file_saved` utilisait la base ouverte *au moment de la sauvegarde* (`lib._db`), et `reindex_files` indexait n'importe quel chemin, y compris hors des dossiers de la base. Comic ouvert depuis la base A, ouverture de la base B, sauvegarde : une ligne étrangère (`../../…`) était insérée dans B. `_write_comicinfo_and_reindex` avait le même problème.
  - `panel._library_window` était remis à `None` par **tout** `_load_files`, fusion comprise : après avoir fusionné une image, la sauvegarde ne rafraîchissait plus la Bibliothèque.
- **Correction** : `reindex_files` ignore tout fichier hors des répertoires de la base (`_is_in_library_dirs` : maître et additionnels, extension indexée) ; le panneau mémorise la base d'origine (`panel._library_db`) et ne réindexe que si c'est encore la base ouverte ; le lien suit le document (conservé par une fusion, coupé par un nouveau document) ; chaque fin d'action différée de la Bibliothèque vérifie `db is self._db` (couvre aussi P2-7).
- **Tests** : `tests/test_library_reindex.py`.
- **Interactions** : Bibliothèque × sauvegarde × fusion × changement de base.

### P1-7 · Import de métadonnées en lot : un échec d'écriture est compté comme réussi

- **Certitude** : Confirmed · **Statut** : Corrigé
- **Code** : `batch_metadata_dialog_qt.py` → `_save_state_for_file`, `_SaveWorker`, `_on_file_done` (`_on_save_error`).
- **Constat** : `_save_state_for_file` attrapait toute exception (`except Exception: return`), donc `_SaveWorker` émettait `done` ; de plus `_on_save_error` incrémentait aussi `_done_count`. Le résumé annonçait des fichiers traités qui ne l'avaient pas été (l'original n'était pas supprimé : pas de perte, mais un résumé faux).
- **Correction** : les exceptions remontent (`_SaveWorker.error` → `_errors`, résumé et journal) ; `_on_save_error` n'incrémente plus `_done_count`.
- **Tests** : `tests/test_batch_metadata_save.py` (`test_write_failure_is_raised_and_original_kept`, `test_cbt_failure_keeps_original`).

### P1-8 · Protocole du lot PDF désynchronisé après un timeout

- **Certitude** : Possible · **Statut** : Corrigé
- **Code** : `batch_dialogs_qt.py` → `_run_pdf_conversion` (`do_conversion`, `_convert_all`).
- **Constat** : l'ouverture d'un PDF B dépasse 30 s (calcul des ratios sur un très gros PDF) ; B est marqué en échec ; son `batch_ready` tardif est ensuite pris pour celui du PDF C. Nombre de pages et noms de B appliqués à C ; le drapeau « protégé par mot de passe propriétaire » de B (False) pouvait entraîner la **suppression d'un C protégé**, qui aurait dû être conservé.
- **Correction** : après toute fin anormale d'un document (timeout à l'ouverture ou pendant les pages, `('error', …)`, exception d'écriture), le process est détruit et remplacé (`_restart_process`) ; il est détruit dans un `finally` en toutes circonstances (y compris interruption à la fermeture de l'application).
- **Tests** : non automatisable (process PyMuPDF, boucle imbriquée) ; test manuel limité (cas rare).

---

## P2 — Functional correctness

| # | Constat initial | Certitude | Correction | Tests |
|---|---|---|---|---|
| **P2-1** | `image_transforms_qt.py` → `rotate_selected_qt`/`flip_selected_qt` : `_real_idx` était faux si la sélection contenait une entrée non image (les entrées filtrées étaient associées à la liste non filtrée des indices) ; la mauvaise vignette était rafraîchie après rotation ou miroir. | Confirmed | Position retrouvée par identité dans `images_data` à la fin du traitement ; même principe dans `deskew_qt.py` et `resize_dialog_qt.py`. | Manuel |
| **P2-2** | `file_operations_qt.py` → `_write_zip_with_progress` (« Enregistrer sous ») : les pages issues d'un PDF portent `entry["dpi"]` = résolution de rendu, absente de leurs bytes ; l'écart provoquait un **ré-encodage JPEG avec perte** à chaque sauvegarde, et `entry["bytes"]` était modifié hors historique. « Appliquer et créer un CBZ » (`_write_cbz_entries`) ne le faisait pas : comportement incohérent. | Very likely | `_bytes_with_entry_dpi` : JPEG → densité écrite dans l'en-tête JFIF sans ré-encodage (`jpeg_bytes_with_dpi`) ; PNG/TIFF → ré-encodage sans perte ; jamais pour une image à plusieurs frames ; page en mémoire jamais modifiée. Utilisé par tous les enregistrements. | `test_save_dpi_bytes.py` |
| **P2-3** | `conversion_dialogs_qt.py` → `perform_conversion` : la position d'insertion des pages converties (index calculé au lancement puis incrémenté) dérivait si la mosaïque changeait pendant la conversion. | Possible | Insertion après la dernière page convertie, sinon après la page d'ancrage, retrouvées par identité (`_insert_position`). | Manuel |
| **P2-4** | Lots (`batch_dialogs_qt.py`, CBR/CB7/CBT et IMG) et `image_ops.py` : les modes `I`/`F` convertis en RGB étaient écrêtés à 255 (page blanche pour les entiers 32 bits) ; CMYK réencodé sans son profil ICC. | Possible (rare) | `_normalized_page_bytes` commun aux lots, via `_to_writable_mode` (mise à l'échelle depuis 0 pour les valeurs positives, étirement si valeurs négatives) ; `cmyk_to_rgb` convertit par le profil ICC (LittleCMS) avec repli naïf. | `test_image_frames_and_modes.py`, `test_batch_zip_writer.py` |
| **P2-5** | `undo_redo.py` : l'historique identifiait les pages par `id()`. Après ramasse-miettes, un `id` réattribué à une autre entrée lui faisait recevoir les bytes et le nom du snapshot en gardant ses autres champs (DPI, source, dimensions…). | Possible | Jeton unique par page (`entry_key`, compteur jamais réutilisé, lié à l'`id` de l'objet qui l'a reçu : une copie `dict(entry)` reçoit son propre jeton) ; une entrée recréée par la restauration reprend le jeton (`adopt_entry_key`). | `test_undo_redo_keys.py` |
| **P2-6** | `MosaicView.py` → `MainWindow.closeEvent` : en double panneau, `_panel2._loader.cancel()` était appelé **avant** toute confirmation ; si l'utilisateur annulait la fermeture, le chargement du panneau 2 était perdu. | Confirmed | Arrêt seulement une fois la fermeture acquise (`can_close`). | Manuel |
| **P2-7** | `library_window.py` → `_convert_file_to_cbz`, `_open_comicinfo_editor` : les fins différées capturaient `db` ; base fermée entre-temps → exception et aucun rafraîchissement (le fichier, lui, était bien écrit). | Confirmed | Couvert par la correction de P1-6 (`db is self._db`). | `test_library_reindex.py` (partiel) |
| **P2-8** | `entries.py`/`image_ops.py` : un TIFF de plusieurs pages **contenu dans une archive** est une seule page de la mosaïque ; rotation, miroir, redimensionnement, redressement, découpe et outils de la visionneuse n'en gardaient que la première page. (Ouvert seul, un TIFF multipage est découpé en pages indépendantes : non concerné.) | Possible | `is_multipage_tiff` posé au chargement ; `has_several_frames` → traitement page par page (`transform_multipage_tiff`, compression et DPI conservés) ; outils de la visionneuse grisés avec leur propre explication (`viewer.toolbar_disabled_multipage_tiff`) ; jointure refusée. | `test_image_frames_and_modes.py` |

---

## P3 — Robustness / resource lifecycle

### P3-1 · Journaux de lot effacés à la fermeture, ou écrasés

- **Certitude** : Confirmed · **Statut** : Corrigé
- **Code** : `temp_files.py` → `cleanup_all_temp_files(keep_logs=True)` ; noms de journaux dans `batch_dialogs_qt.py` et `batch_metadata_dialog_qt.py`.
- **Constat** : à la fermeture, seuls les journaux `Log_pdftocbz_*`, `Log_cbrtocbz_*`, `Log_imgtocbz_*` et `Log_scan.txt` étaient conservés ; ceux des lots CB7, CBT, recompression et métadonnées étaient effacés alors que leur résumé indique le chemin du journal. Noms datés à la minute : deux lots du même type terminés dans la même minute écrivaient dans le même journal.
- **Décision de l'utilisateur** : conserver tous les journaux, et suffixer les noms.
- **Correction** : tout fichier `Log_*.txt` est épargné à la fermeture (préfixe commun, pas de liste de types) ; `unique_log_path` ajoute `_2`, `_3`… si le nom existe déjà.
- **Tests** : `tests/test_temp_files_logs.py`.

### P3-2 · Les lots écrivent directement sous le nom final

- **Certitude** : Possible · **Statut** : Corrigé
- **Code** : `batch_dialogs_qt.py` → `_interruptible_zip_writer`.
- **Constat** : un `close()` en échec (disque plein) ou un crash du process laissait un CBZ tronqué portant un nom valide (la source était conservée).
- **Correction** : écriture dans un temporaire `.~mosaicview_*.tmp` du même dossier, renommé au nom final seulement après fermeture réussie du ZIP ; sur toute exception, temporaire supprimé et nom final jamais créé.
- **Tests** : `tests/test_batch_zip_writer.py`.

### P3-3 · Écriture du lot de métadonnées non annulable ; pas de `fsync` avant remplacement

- **Certitude** : Possible · **Statut** : Corrigé
- **Code** : `batch_metadata_dialog_qt.py` → `_SaveWorker`, `_save_state_for_file`, `cancel` ; `utils.py` → `replace_file_atomically`.
- **Constat** : `_SaveWorker` n'avait pas de drapeau d'annulation : à la sortie, `stop_running_qthreads` attendait 3 s puis le thread était détruit en pleine écriture, avec un `.~tmp` orphelin. `replace_file_atomically` ne forçait pas l'écriture sur disque avant `os.replace` : une coupure de courant pouvait laisser un fichier vide sous le nom final.
- **Correction** : `_SaveWorker._cancelled` (`threading.Event`, convention reconnue par `stop_running_qthreads`), testé avant chaque fichier écrit (`_CancellableZip`) et pendant l'attente du process PDF ; annulation → temporaire supprimé, original intact. `fsync_file` avant chaque remplacement atomique (CBZ, lots, métadonnées).
- **Tests** : `tests/test_batch_metadata_save.py` (`test_cancel_keeps_original_untouched`), `tests/test_utils_replace_retry.py` (`fsync_file`).

### P3-4 · Remplacement impossible d'un fichier ouvert par un autre programme, sans raison claire

- **Certitude** : Possible · **Statut** : Corrigé
- **Code** : `utils.py` → `replace_file_atomically`.
- **Constat** : sous Windows, `os.replace` échoue si la destination est ouverte par un autre programme (lecteur de BD, antivirus, scan ou aperçu de la Bibliothèque). La sauvegarde échouait proprement, mais avec un message système brut.
- **Correction** : `replace_file_retrying` retente quelques fois (≈ 1 s au total) ; s'il reste bloqué, `FileInUseError` donne la cause (texte traduit à chaque affichage, clé `messages.errors.file_in_use`) ; une vraie interdiction (lecture seule) reste une `PermissionError` ordinaire. Utilisé par tous les remplacements atomiques.
- **Tests** : `tests/test_utils_replace_retry.py` (fichier verrouillé, verrou levé pendant les tentatives, lecture seule, original intact).

---

## P4 — Maintainability

### P4-1 · Trois implémentations de « réécrire un CBZ »

- **Statut** : **Non corrigé** (refonte à planifier).
- **Code** : `file_operations_qt.py` (sauvegarde et conversions des panneaux), `library_window.py` (`_rewrite_comicinfo_in_cbz`, conversion vers CBZ), `batch_metadata_dialog_qt.py` (`_save_state_for_file`).
- **Constat** : garanties divergentes (test d'existence de la cible, liste d'extensions recopiées, DPI, vérification d'espace disque) — cause commune de P0-3, P0-6 et P2-2.
- **État après corrections** : les trois chemins ont été rapprochés (temporaire du même dossier, `fsync`, nouvelles tentatives sur fichier verrouillé, recopie de tous les fichiers) mais restent distincts ; seule la vérification d'espace disque reste propre aux panneaux.
- **Piste** : une fonction commune « écrire un CBZ à partir de pages et d'une archive source », utilisée par les trois. Coût et risque élevés (trois chemins d'écriture à retester).

### P4-2 · La convention « sauvegarder l'état avant et après » n'est imposée par rien

- **Statut** : **Non corrigé** (refonte à planifier).
- **Constat** : cause de P1-2 ; les deux oublis connus sont corrigés, rien n'empêche le suivant.
- **Piste** : enveloppe `run_document_op(state, fn)` qui encadre l'action ; éventuellement un test AST signalant toute modification de `images_data` hors de ce cadre. Coût moyen (nombreux points d'appel, transformation mécanique).

### P4-3 · `all_entries` sans règle définie ; `state.converting` mort

- **Statut** : Corrigé.
- **Constat** : `all_entries` (cause de P0-4) était maintenue à 8 endroits sans invariant ; après correction de P0-4 elle ne servait plus qu'à proposer la commande « Aplatir les répertoires » (`context_menus_qt.py`, `menubar_qt.py`), qui pouvait rester proposée à tort. `state.converting` était posé et retiré mais lu nulle part.
- **Correction** : « Aplatir » calculé sur `images_data` ; `all_entries` supprimée partout (état, chargements, fusion, aplatissement, fermeture, historique) ; `converting` supprimé (le blocage est assuré par `running_operations`, P1-1).
- **Tests** : couverts indirectement par la suite existante (`test_undo_redo.py`, `test_undo_redo_keys.py`).

### P4-4 · Des `except Exception: continue` dans des boucles

- **Statut** : Corrigé (garde ajoutée).
- **Constat** : cause commune de P0-2, P0-5, P1-7.
- **Correction** : `tests/test_check_no_swallowed_loop_errors.py` (scan AST de `modules/qt/` et `MosaicView.py`) échoue sur tout nouvel `except` large dont le seul contenu est `continue` dans une boucle ; les cas réellement sans conséquence sont déclarés dans sa liste `ALLOWED` avec leur justification (7 cas : détection de la résolution des pages PDF ×3, dimensions des métadonnées `<Pages>`, recherche du plateau du scanner, fenêtre des dimensions aberrantes, export caché des vignettes) ; un second test vérifie que cette liste reste à jour. Le recensement a fait apparaître C-5 et C-6.

---

## Constats ajoutés pendant les corrections

Ces problèmes ne figuraient pas dans le rapport initial ; ils sont apparus en corrigeant les constats ci-dessus.

| # | Priorité | Constat | Correction | Tests |
|---|---|---|---|---|
| **C-1** | P2 | Messages d'erreur des lots écrits en dur, en français ou en anglais, dans les journaux et la liste « Fichiers ignorés » du PDF (`« PDF vide »`, `« PDF protégé par mot de passe »`, `page N: …`, `… page(s) could not be converted …`, `timeout`). Règle `CLAUDE.md` : aucun texte en dur. | Clés `messages.errors.pdf_failure.*` et `messages.errors.batch_incomplete_kept` dans toutes les langues ; erreurs de page stockées brutes et traduites à l'affichage. | `test_batch_copy_pages.py` (adapté) |
| **C-2** | P1 (évité) | En introduisant ces clés : `_save_state_for_file` utilisait `_` comme variable de déballage (`_, filename, … = msg`), ce qui aurait rendu chaque appel `_("…")` de la fonction invalide (piège `_` écrasé de `CLAUDE.md`). | Variables renommées `_kind`. | `test_batch_metadata_save.py` |
| **C-3** | P2 | Le changement de taille de police ne mettait à jour que la fenêtre principale, pas les fenêtres déjà ouvertes (constaté en vérifiant les règles UI des nouvelles fenêtres). | `MosaicView.py` → `_notify_font_size_changed` émet `language_signal` ; règle n° 3 de `CLAUDE.md` mise à jour. | Manuel |
| **C-4** | P2 | Fenêtre de confirmation d'export (`FileSavedDialog`) : textes non centrés (règle de style de `CLAUDE.md`). | Libellés centrés. | Manuel |
| **C-5** | P0 | `panel_widget.py` → `_ImageLoadWorker.run` et `entries.py` → `create_entries_from_tiff` (ouverture d'images isolées) : la liste `errors` du worker (images > 500 Mo) n'était **jamais transmise** ; un fichier illisible ou inaccessible était ignoré (`except: pass`, ou `create_entry_from_file` renvoyant `None`) ; une page de TIFF illisible était sautée (tifffile) ou **interrompait la lecture des pages suivantes** (PIL), sans aucun avertissement. Le CBZ créé ensuite avait des pages en moins. | Erreurs transmises (`_ImageLoadWorker.errors`) et listées dans la fenêtre « Fichiers ignorés » (`_image_load_error_text`, clés `messages.warnings.files_ignored.unreadable`/`.tiff_page`) ; `create_entries_from_tiff(…, failures=…)` rend chaque page illisible. | `test_tiff_pages.py` |
| **C-6** | P2 | `printing_qt.py` → `_PrintWorker.run` : une page illisible n'était pas imprimée, sans avertissement. | Pages listées dans la fenêtre « Fichiers ignorés » après le lancement de l'impression (`messages.warnings.files_ignored.not_printed`). | Manuel |

---

## Interaction areas with no issues found

*No issue found after code and test analysis* pour :

- **Macros × erreurs × undo × fermeture × double panneau** : garde `doc_generation`, comparaison `history_top`, rollback complet d'une lecture interrompue, refus des outils inapplicables sur image animée, travail en attente purgé.
- **« Enregistrer sous » / création pendant la fermeture du document** : `_DocumentChangedDuringSave`, itération sur une copie de la liste des pages.
- **`replace_file_atomically`** et le test AST des écritures ZIP (`test_check_atomic_file_writes.py`).
- **Lots CBR/CB7/CBT** : page en échec signalée, source conservée, arrêt propre à la fermeture.
- **Double panneau × opérations de fichier différées** : state explicite par panneau, test AST `test_check_no_deferred_singleton.py`.
- **Images animées × rotation/miroir/redimensionnement/redressement/découpe** : toutes les frames conservées ; outils de la visionneuse grisés sur image animée.
- **Annulation de rotation/redressement** : restauration seulement après l'arrêt réel du worker.
- **Recompression par lot** et **conversion en lot d'images en un seul CBZ** : suppression des sources seulement après fermeture réussie du ZIP.

## Fausses pistes écartées

- Lecture du singleton d'état par le presse-papiers : appel synchrone, l'invariant « singleton = panneau actif » tient.
- « Enregistrer sous » pendant une fermeture : protégé (`_DocumentChangedDuringSave`).
- Recompression par lot, lots CBR/CB7/CBT : déjà robustes (voir ci-dessus).
- Macros sur image animée : refusées proprement.
- Ouvrir un même fichier dans les deux panneaux : comportement voulu (décision de l'utilisateur, P0-6).

---

## Missing tests

**Recommandés par l'audit et ajoutés** (suite passée de 410 à 490 tests) :

| Fichier | Couvre |
|---|---|
| `test_undo_redo_keys.py` | P0-1 (contenu inchangé depuis l'écriture), P2-5 (jeton des pages) |
| `test_operation_running.py` | P1-1 |
| `test_library_reindex.py` | P1-6 |
| `test_image_frames_and_modes.py` | P0-5 (cause des échecs), P2-4, P2-8 |
| `test_save_dpi_bytes.py` | P2-2 |
| `test_batch_zip_writer.py` | P3-2, P2-4 (lots) |
| `test_batch_metadata_save.py` | P0-3, P1-7, P3-3 |
| `test_temp_files_logs.py` | P3-1 |
| `test_utils_replace_retry.py` | P3-3 (`fsync`), P3-4 |
| `test_tiff_pages.py` | C-5 |
| `test_check_no_swallowed_loop_errors.py` | P4-4 (garde) |

Les tests de `batch_dialogs_qt.py`, `file_operations_qt.py` et `batch_metadata_dialog_qt.py` extraient les fonctions par AST (ces modules importent des widgets Qt) : toute nouvelle dépendance d'une fonction extraite doit être ajoutée à l'espace de noms du test, sinon le test plante — ou, si l'appel est dans un `try/except`, passe sans rien vérifier.

**Toujours manquants** (code non exécutable hors de l'application en l'état) :

- protocole du lot PDF avec un faux process qui saute une page ou ne répond plus (P0-2, P1-8) — nécessite d'extraire la boucle de `_run_pdf_conversion` ;
- sélection des originaux à supprimer après une conversion partielle (P0-5) — logique dans une fenêtre Qt ;
- confirmation avant réécriture d'un fichier modifié ailleurs (P0-6) ;
- découpe, coupe, suppression et fermeture avec visionneuse (P1-2 à P1-5) ;
- tests d'interaction de bout en bout suggérés par la demande d'audit (GIF animé + redimensionnement + annuler + enregistrer ; macro + double panneau ; Bibliothèque + comic ouvert + sauvegarde…) : ils exigent une application Qt instanciée, ce que la suite de tests s'interdit.

## Recommended fixes

Tous les correctifs recommandés par le rapport initial ont été appliqués, dans l'ordre P0 → P1 → P2 → P3, puis P4-3 et P4-4. Restent ouverts (P4-1, P4-2) ou traités après coup (point 3) :

1. **P4-1** — fonction commune d'écriture de CBZ (refonte, risque élevé).
2. **P4-2** — enveloppe garantissant la sauvegarde de l'état avant et après chaque action.
3. **Hors périmètre, relevé en route** : les fenêtres d'erreur de l'impression (`printing_qt.py`) étaient affichées par `.show()` au lieu de `show_nonmodal()` et n'étaient donc pas centrées sur le panneau (règle UI n° 5 de `CLAUDE.md`). **Statut** : Corrigé après la clôture de l'audit (les 9 appels passent par `show_nonmodal()`).

---

# Audit 2 — Opérations destructives et conservation des données (version 1.8.8)

- **Période** : octobre 2026 (audit, puis corrections intégrées à la version 1.8.8, pas encore publiée au moment des corrections).
- **Objet** : vérifier que chaque opération qui supprime, remplace, convertit ou réécrit des données de l'utilisateur ne peut pas en perdre : sauvegardes, conversions avec suppression de l'original, traitements par lot, Bibliothèque, opérations sur les pages, Annuler/Refaire, « Restaurer l'original », fermeture et interruption, écrits secondaires (configuration, macros, fichiers temporaires).
- **Méthode** : lecture du code réel et des skills ; suivi de chaque opération jusqu'à l'écriture sur disque et à la suppression de la source ; reproductions sans Qt (fonctions extraites par AST, Pillow, `zipfile`, `tarfile`, le `7z.exe` embarqué) dans un dossier temporaire. Aucune modification de code avant présentation du rapport initial ; corrections ensuite, validées par l'utilisateur dans l'application avec des fichiers de test fournis.
- **Code de référence** : MosaicView 1.8.8 (état du dépôt, qui contient déjà les corrections de l'Audit 1 — la demande d'audit parlait de 1.8.7).

## Executive summary

| Priorité | Constats | Certitude | Statut |
|---|---|---|---|
| **P0** — perte ou corruption de données | 5 | 5 Confirmed (4 reproduits) | 5 corrigés |
| **P1** — risque sérieux de perte | 4 | 4 Confirmed | 4 corrigés |
| **P2** — comportement destructif incorrect mais récupérable | 10 | 4 Confirmed, 6 Possible | 6 corrigés, 4 non corrigés (décision de l'utilisateur) |
| **P3** — robustesse | 5 | 2 Confirmed, 3 Possible | 5 corrigés |
| **P4** — maintenabilité | 5 | — | 3 corrigés, 2 partiellement traités (reste reporté) |
| **Constats ajoutés pendant les corrections** | 8 | Confirmed | 8 corrigés (plus 1 message non reproduit) |

**Principales zones à risque identifiées**

1. **La lecture des archives** : six lecteurs différents (ouverture, ajout, lots, import de métadonnées, Bibliothèque, ComicInfo), chacun avec ses propres filtres. Cause de P0-1, P0-3, P1-3, P1-4 et P2-1.
2. **Les pages « sans données »** : une page illisible au chargement disparaissait à l'écriture sans avertissement (P0-5).
3. **Les erreurs avalées** dans l'import de métadonnées en lot (P0-2), même famille que l'Audit 1, P4-4.
4. **Les états mémorisés par position de page** : « Restaurer l'original » (P1-1), travail non validé de la visionneuse (P4-3).
5. **Les snapshots d'Annuler**, qui ne gardaient que 7 champs par page (P1-2).

**Solidité constatée** : le remplacement atomique (temporaire du même dossier, `fsync`, nouvelles tentatives sur fichier verrouillé) était déjà utilisé par **tous** les chemins d'écriture de CBZ suivis, et aucune source n'était supprimée avant que sa destination soit fermée et renommée. Le problème n'était pas l'écriture, mais **ce qu'on donnait à écrire** — et l'absence de relecture avant suppression (P3-5).

**Décision de conception prise pendant l'audit** : un TIFF de plusieurs pages contenu dans une archive est découpé en pages à l'ouverture (C-3). Elle remplace l'approche de l'Audit 1, P2-8 (TIFF multipage gardé en une seule page de la mosaïque, traité page par page), dont le constat reste décrit tel quel ci-dessus.

### Causes communes

| Cause | Constats concernés |
|---|---|
| Lecteurs d'archives aux règles divergentes (attribut 7z, filtre d'extensions, nom de base, lecture ZIP par nom, tri) | P0-1, P0-3, P1-3, P1-4, P2-1, C-8 (→ P4-1) |
| Page sans données traitée comme absente (`bytes is None` → sautée) | P0-5 (→ P4-4) |
| Erreur avalée (`except Exception: pass`) avant une suppression de source | P0-2 (→ P4-4) |
| Donnée rattachée à la position d'une page | P1-1, P4-3 |
| Liste blanche de champs dans les snapshots d'historique | P1-2 (→ P4-2) |
| Document modifiable pendant une opération qui traite les événements | P2-5, P3-4, C-5 |
| Aucune vérification du fichier produit avant suppression de la source | P3-5 |

---

## P0 — Perte ou corruption de données

### P0-1 · CB7 : les membres dont l'attribut n'est pas exactement « A » sont ignorés en silence

- **Certitude** : Confirmed (reproduit) · **Statut** : Corrigé
- **Code** : `archive_loader.py` → `_list_7z_files`. Appelants : `batch_dialogs_qt.py` → `_run_cb7_conversion` ; `batch_metadata_dialog_qt.py` → `_extract_non_cbz` ; `ArchiveLoader._get_namelist` (ouverture dans un panneau, conversion de la Bibliothèque) ; `import_merge_qt.py`.
- **Constat** : un membre n'était gardé que si son attribut valait exactement `A`.
- **Reproduction** : archive de 4 pages créée avec le 7z.exe embarqué, attributs `A`, `RA` (lecture seule), `HA` (caché), `N` (sans bit archive) : `_list_7z_files` renvoyait `['a.jpg']`.
- **Scénario** : lot CB7 → CBZ sur des scans copiés depuis un CD (lecture seule) ou un NAS (bit archive absent).
- **Comportement constaté** : CBZ incomplet, aucun échec signalé (les pages absentes de la liste n'étaient même pas tentées), CB7 supprimé. Même effet dans l'import de métadonnées en lot ; dans un panneau, pages manquantes puis « Supprimer l'original ? ».
- **Correction** : `_is_7z_file_block(path, attributes)` — un membre est un fichier sauf si `D` figure dans le premier mot de ses attributs (jamais une égalité avec `A` : `RA`, `HA`, `N`, attributs Unix `A_ -rw-r--r--`).
- **Tests** : `tests/test_7z_member_extraction.py` (vraie archive avec fichiers en lecture seule, cachés, sans bit archive ; dossiers exclus).

### P0-2 · Import de métadonnées en lot : un échec d'extraction remplace l'original par un CBZ ne contenant que ComicInfo.xml

- **Certitude** : Confirmed (écriture reproduite, chargement établi par le code) · **Statut** : Corrigé
- **Code** : `batch_metadata_dialog_qt.py` → `_BatchMetadataOrchestrator._load_state_for_file`, `_save_state_for_file`.
- **Constat** : `try: images_data = self._extract_non_cbz(...) except Exception: pass`. Pour un CBR illisible (en-tête endommagé, en-têtes chiffrés), un CB7 refusé par 7z ou un PDF en échec, `images_data` restait vide et sans manifeste ; après le choix ComicVine, la branche `else` écrivait un CBZ ne contenant que `ComicInfo.xml`, puis supprimait l'original.
- **Reproduction** : `X.cbr` supprimé, `X.cbz` = `['ComicInfo.xml']`.
- **Correction** : l'exception de chargement n'est plus avalée (fichier compté en erreur) ; garde dans `_save_state_for_file` : un fichier converti sans manifeste CBR/CBT/CB7/PDF lève `ValueError(messages.errors.batch_no_source_pages)` avant toute écriture.
- **Tests** : `tests/test_batch_metadata_save.py` (`test_conversion_without_manifest_writes_nothing_and_keeps_original`).

### P0-3 · « Images → un seul CBZ » : homonymes de sous-dossiers écrits sous le même nom, puis sources supprimées

- **Certitude** : Confirmed (comportement de `zipfile` reproduit) · **Statut** : Corrigé
- **Code** : `batch_dialogs_qt.py` → `batch_convert_img_to_cbz` (parcours récursif `os.walk`), `_run_imgs_to_single_cbz` ; aussi le dépôt de plusieurs dossiers (`drop_handler_qt.py`).
- **Constat** : `cbz.writestr(basename, …)` sans nom unique. `Ch1/001.jpg` et `Ch2/001.jpg` donnaient deux entrées `001.jpg` ; `zipfile.read(nom)` rend le contenu du **second** pour les deux (reproduit : `[b'BBB', b'BBB']`). Toutes les sources étaient ensuite supprimées.
- **Correction** : tri par chemin relatif au dossier commun des images, noms à plat qui gardent le dossier (`_flat_names` : `Ch1/001.jpg` → `Ch1_001.jpg`) puis `_unique_flat_name` (suffixe `_2`, `_3`…), comme les lots CBR/CB7/CBT.
- **Tests** : `tests/test_batch_unique_flat_name.py`.

### P0-4 · Lots CBR/CB7/CBT : un TIFF multipage en CMJN, YCbCr, I ou F est réduit à sa première page, puis la source est supprimée

- **Certitude** : Confirmed (reproduit), cas rare · **Statut** : Corrigé
- **Code** : `batch_dialogs_qt.py` → `_normalized_page_bytes`, appelée par `_copy_pages_to_cbz`.
- **Constat** : la normalisation du mode couleur réencodait l'image, ce qui ne gardait que la première page d'un TIFF multipage. Reproduction : TIFF CMJN de 3 pages → 1 page RGB, sans échec remonté.
- **Correction** : un fichier à plusieurs images (`n_frames > 1`) est écrit tel quel.
- **Tests** : `tests/test_batch_zip_writer.py` (`test_multipage_cmyk_tiff_keeps_every_page`).
- **Lien** : chemin (re)défini par la correction de l'Audit 1, P2-4.

### P0-5 · Une page illisible au chargement est retirée sans avertissement à l'écriture

- **Certitude** : Confirmed (mécanisme établi par le code, échecs de lecture reproduits) · **Statut** : Corrigé
- **Code** : `archive_loader.py` → `ArchiveLoader.load_archive` ; `entries.py` → `create_entry` ; `file_operations_qt.py` → `_write_cbz_entries`, `_write_zip_with_progress` ; `library_window.py` → `_convert_file_to_cbz`.
- **Constat** : sur erreur de lecture (CRC faux `BadZipFile: Bad CRC-32`, Deflate64 `NotImplementedError`, membre RAR chiffré), la page restait dans la mosaïque avec `bytes = None`, non marquée corrompue. Les écrivains sautaient toute entrée `bytes is None`, et la conversion de la Bibliothèque ignorait la liste `errors` du chargeur.
- **Scénarios** : CBZ avec un membre endommagé → petite modification → Appliquer : le fichier est réécrit sans ce membre, alors qu'il restait récupérable par un outil ZIP. Un CBZ en Deflate64 (aucune page lisible) était remplacé par un ZIP vide. Conversion puis « Supprimer l'original ? ».
- **Correction** :
  - `create_entry` marque corrompue une image sans données ; `_attach_read_errors` rattache l'erreur de lecture à la page comme `corruption_reason` (affichée dans son info-bulle) au lieu d'une seconde fenêtre (C-2) ;
  - `utils.cbz_members(entries)`, point de passage de toute écriture de CBZ, lève `MissingPageDataError` (texte traduit `messages.errors.pages_without_data`, liste des fichiers) ; `_check_pages_have_data` refuse l'enregistrement avant toute question (Enregistrer sous, sélection, création, Appliquer) ;
  - la conversion de la Bibliothèque refuse d'écrire si le chargement a signalé une erreur.
- **Tests** : `tests/test_utils_atomic_write.py` (`cbz_members`), `tests/test_archive_member_reading.py` (`_attach_read_errors`).
- **Lien** : complément de l'Audit 1, C-5 (erreurs de chargement corrigées pour l'affichage, pas pour l'écriture).

## P1 — Risque sérieux de perte

### P1-1 · « Restaurer l'original » s'applique à la mauvaise page dès que l'ordre des pages change

- **Certitude** : Confirmed (code) · **Statut** : Corrigé
- **Code** : `state.py` → `color_depth_original_bytes_by_page`, `effect_original_bytes_by_page`, `effect_key_by_page`, `image_mode_original_bytes_by_page` ; `color_depth_tool_qt.py`, `effects_tool_qt.py`, `image_mode_tool_qt.py` → `perform_restore_*`.
- **Constat** : dictionnaires indexés par **position** de page, purgés seulement à la fermeture du fichier. Page 5 → 8 bits, suppression de la page 3, puis « Restaurer l'original » sur la nouvelle page 5 : l'image de l'ancienne page 5 remplaçait celle de l'ancienne page 6.
- **Correction** : `AppState.page_key(idx)` = jeton durable de la page (`undo_redo.entry_key`, repris par une restauration d'Annuler) ; les trois dictionnaires sont indexés par cette clé.
- **Tests** : `tests/test_undo_redo_keys.py` (`page_key`).

### P1-2 · Annuler recrée les pages supprimées sans leurs propriétés : un GIF animé restauré devient une image fixe

- **Certitude** : Confirmed (code) · **Statut** : Corrigé
- **Code** : `undo_redo.py` → snapshot des entrées ; `undo_redo_qt.py` → `_build_new_entry_qt`.
- **Constat** : le snapshot ne gardait que 7 champs. GIF animé → Supprimer → Annuler → rotation : `has_several_frames` faux, seule la première frame gardée, outils de la visionneuse plus grisés. Autres champs perdus : `dpi` (pages de PDF), `gif_*`, `source_archive`, `is_too_large`.
- **Correction** : liste d'exclusions au lieu d'une liste blanche — `_entry_metadata(entry)` copie tous les champs sauf ceux de `_NOT_IN_META` (bytes, image en mémoire, widgets, champs recalculés) dans `meta` ; `_build_new_entry_qt` repart de `meta`.
- **Tests** : `tests/test_undo_redo_keys.py` (`meta`).
- **Lien** : Audit 1, P2-5.

### P1-3 · ZIP avec des noms en double : MosaicView peut en créer, puis perd le contenu du premier homonyme

- **Certitude** : Confirmed (comportement de `zipfile` reproduit) · **Statut** : Corrigé
- **Constat** : partout, lecture par nom (`archive.read(nom)`), qui rend le dernier homonyme pour chacun. MosaicView créait des doublons (« Ignorer et sauvegarder » de `DuplicateFilenameDialog` ; conversion CBT/EPUB de la Bibliothèque qui aplatissait en nom de base) ; la recompression, `_rewrite_comicinfo_in_cbz`, la branche CBZ de l'import de métadonnées et toute sauvegarde après réouverture remplaçaient ensuite le premier par une copie du second.
- **Correction** :
  - lecture par `ZipInfo` : `_zip_member_reader(archive)` à l'ouverture et à l'ajout (rend chaque homonyme à son tour) ; `zf.read(info)` dans la recompression, la Bibliothèque et l'import de métadonnées (seul le premier membre d'un nom réécrit est remplacé, un homonyme est recopié) ;
  - `cbz_members` donne des noms uniques à l'écriture (`_2`, `_3`…, jamais un nom déjà porté) ; le texte de `DuplicateFilenameDialog` l'annonce (C-1).
- **Tests** : `tests/test_archive_member_reading.py`, `tests/test_utils_atomic_write.py`, `tests/test_batch_metadata_save.py` (`test_cbz_homonym_members_are_both_kept`).

### P1-4 · CBT ouvert dans un panneau ou converti par la Bibliothèque : ComicInfo.xml et les fichiers annexes sont perdus, puis la suppression du CBT est proposée

- **Certitude** : Confirmed (code) · **Statut** : Corrigé
- **Code** : `archive_loader.py` → `ArchiveLoader._get_namelist` (branche CBT) ; `import_merge_qt.py`.
- **Constat** : seules les images (`IMAGE_EXTS`) étaient gardées, et les chemins aplatis en nom de base ; la conversion produisait un CBZ sans ComicInfo.xml, `.nfo` ni dossiers, avant « Supprimer l'original ? ».
- **Correction** : tous les fichiers du TAR, sous leur chemin complet normalisé (`_tar_entry_name` : sans `./` ni `/` initial).
- **Tests** : `tests/test_archive_member_reading.py`.

## P2 — Comportement destructif incorrect mais récupérable

| # | Constat initial | Certitude | Statut et correction | Tests |
|---|---|---|---|---|
| **P2-1** | Lots CBR/CB7/CBT et « un seul CBZ » : tri par **nom de base** ; les chapitres de sous-dossiers étaient entrelacés (Ch1 p1, Ch2 p1, Ch1 p2…), puis la source supprimée. | Confirmed | **Corrigé** : tri par chemin complet ; noms à plat qui gardent le dossier devant le nom (`_flat_names`, dossier commun retiré). | `test_batch_copy_pages.py`, `test_batch_unique_flat_name.py` |
| **P2-2** | Le **commentaire d'archive ZIP** (métadonnées ComicBookInfo de ComicRack/ComicTagger) n'était conservé par aucune réécriture. | Confirmed | **Corrigé** : `file_operations_qt._zip_comment` lu avant l'écriture et recopié (sauvegarde, Appliquer) ; recompression (`zout.comment = zin.comment`, setter de `_InterruptibleZip`), Bibliothèque, import de métadonnées. | `test_batch_zip_writer.py`, `test_batch_metadata_save.py` |
| **P2-3** | « Restaurer l'original » revient à l'état d'avant le premier clic de l'outil et efface aussi les retouches d'autres outils faites ensuite. | Confirmed | **Non corrigé** : comportement voulu (décision de l'utilisateur). | — |
| **P2-4** | Annuler/Refaire ne vidait pas l'image PIL en mémoire (`entry["img"]`) des pages non affichées ; une retouche suivante pouvait repartir d'une image périmée. | Possible (confirmé réel par l'utilisateur) | **Corrigé** : `undo_redo_qt._reload_thumb_qt` appelle `free_image_memory(entry)`. | Manuel |
| **P2-5** | Pendant une opération en arrière-plan, seuls Annuler/Refaire étaient bloqués ; suppression, réordonnancement, déplacement et sauvegarde restaient possibles. | Possible | **Corrigé** : `panel_widget.BUSY_BLOCKED_COMMANDS` (commandes sans effet et grisées dans la barre de menus et les menus contextuels), `icon_toolbar_qt._BUSY_BLOCKED_ICONS`, gardes directes (Ctrl+O, dépôt, Suppr, Ctrl+X/V, glisser-déposer interne et entre panneaux, outils de la visionneuse, macros). | `test_operation_running.py` (clés existantes) |
| **P2-6** | Fin de conversion de format : le choix « supprimer les originaux / les converties » est appliqué sans vérifier que les pages converties sont toujours là. | Possible | **Non corrigé** (décision de l'utilisateur ; récupérable par Annuler). | — |
| **P2-7** | Jointure avec la renumérotation désactivée : « Oui, supprimer les sources » ignoré. | Confirmed | **Corrigé** : les sources sont retirées avant l'insertion de la planche. | Manuel |
| **P2-8** | Déplacement entre panneaux : deux historiques indépendants ; Annuler dans la destination ne rend pas les pages à la source. | Possible | **Non corrigé** (décision de l'utilisateur). | — |
| **P2-9** | Conversions de `_write_apply_new_names` : rien n'empêchait de choisir l'original lui-même comme destination ; il était écrasé, puis proposé à la suppression. | Possible | **Corrigé** : `_is_conversion_source` (`is_same_path`, message `messages.errors.convert_target_is_source`) dans les branches CBR/CB7/CBT/EPUB et PDF. Les tests de l'utilisateur ont montré que le cas réel passait par `save_as_cbz` (icône Enregistrer sur un CBR non modifié, nom du CBR saisi dans la fenêtre Windows) : même refus ajouté dans `save_as_cbz` quand le fichier ouvert n'est pas un CBZ. | Manuel |
| **P2-10** | Suppression « vers la Corbeille » sur une clé USB ou un partage réseau : définitive (pas de Corbeille côté Windows). | Possible | **Non corrigé** : comportement de Windows. La relecture avant suppression (P3-5) réduit le risque. | — |

## P3 — Robustesse

### P3-1 · Après un plantage, des temporaires restent dans le dossier de l'utilisateur

- **Certitude** : Confirmed · **Statut** : Corrigé
- **Constat** : trois formats jamais nettoyés : `.~mosaicview_*.tmp`, `X.cbz.~tmp` (import de métadonnées), `X.cbz.~recompress.tmp` (un ZIP complet).
- **Correction** : `utils.make_temp_path(dossier)` crée le temporaire (nom unique `.~mosaicview_*.tmp`), l'inscrit parmi les écritures en cours (`release_temp_path`) et appelle d'abord `remove_stale_temp_files(dossier)`, qui supprime les temporaires de ce dossier qu'aucune écriture en cours ne détient et non modifiés depuis 10 minutes (`STALE_TEMP_AGE`). Utilisé par `replace_file_atomically`, `_interruptible_zip_writer`, la recompression et l'import de métadonnées. Le ménage a lieu à la prochaine écriture dans le même dossier.
- **Tests** : `tests/test_utils_atomic_write.py` (orphelin ancien supprimé, récent gardé, écriture en cours jamais touchée, fichiers de l'utilisateur jamais visés, dossier absent).

### P3-2 · Configuration et macros écrites par `open('w')`

- **Certitude** : Confirmed · **Statut** : Corrigé
- **Constat** : un disque plein ou un plantage pendant l'écriture tronquait le fichier : marque-pages, récents, clé API, disposition de la colonne d'icônes ou macro perdus (un JSON illisible fait repartir des valeurs par défaut).
- **Correction** : `config_manager.write_json_atomically(path, data)` (bibliothèque standard seule) — temporaire du même dossier, `fsync`, `os.replace` ; utilisé par `save_config`, `_write_icon_toolbar_config`, `macro_engine.save_macro`. Coût mesuré : quelques millisecondes.
- **Tests** : `tests/test_config_manager.py`.

### P3-3 · `os.replace` remplace l'objet fichier : identité perdue

- **Certitude** : Possible · **Statut** : Corrigé
- **Constat** : date de création, attributs, droits d'accès et flux annexes perdus ; un CBZ atteint par un lien symbolique devenait un fichier ordinaire.
- **Correction** : `utils._replace_keeping_identity` passe par `ReplaceFileW` quand la destination existe (repli sur `os.replace` si le volume ne le permet pas ; si `ReplaceFileW` a déjà retiré la destination de son nom, le temporaire, seul contenu complet, prend sa place) ; `replace_file_atomically` écrit dans la cible d'un lien symbolique. Les liens durs restent rompus (inévitable sans écriture en place).
- **Tests** : `tests/test_utils_atomic_write.py` (date de création et attribut caché conservés, lien symbolique — sauté si Windows n'autorise pas leur création).

### P3-4 · Aucune protection contre la réentrance pendant l'écriture d'un enregistrement

- **Certitude** : Possible · **Statut** : Corrigé
- **Constat** : `_write_document_with_progress` traite les événements (`processEvents`) pour garder l'application utilisable ; un second Enregistrer lançait une écriture imbriquée du même fichier, et une suppression changeait la liste en cours d'écriture.
- **Correction** : l'écriture s'inscrit comme opération en cours du document (`begin_operation`/`end_operation` dans un `finally`) : les commandes de P2-5 sont bloquées pendant l'écriture ; la navigation reste libre.
- **Tests** : `tests/test_operation_running.py` (verrou actif pendant l'écriture, libéré après un échec).

### P3-5 · Aucune relecture du CBZ produit avant de supprimer la source

- **Certitude** : Possible · **Statut** : Corrigé
- **Constat** : seule l'absence d'exception comptait ; un CBZ abîmé à l'écriture (disque défaillant, support amovible retiré) passait pour réussi, et l'original, seule copie intacte, partait à la Corbeille.
- **Correction** : `utils.verify_written_cbz(path, expected_count)` — `ZipFile.testzip` (CRC de chaque fichier) et nombre de fichiers ; échec → `WrittenFileCheckError` (texte traduit `messages.errors.written_file_check_failed`, détail `written_file_count`), original gardé. Appelée avant chaque suppression de source : Enregistrer sous, Appliquer (CBR/CB7/CBT/EPUB et PDF), les cinq lots de conversion (`_cbz_verified`, qui supprime le CBZ défectueux), l'import de métadonnées, la Bibliothèque. Les fenêtres d'erreur jouent le cri de Wilhelm (accord de l'utilisateur).
- **Tests** : `tests/test_utils_atomic_write.py` (CBZ sain, nombre faux, membre corrompu, CBZ tronqué, dossiers non comptés). Le cas ne se provoque pas dans l'application : vérification manuelle du chemin normal seulement.

## P4 — Maintenabilité

### P4-1 · Six lecteurs d'archives aux règles divergentes

- **Statut** : **Partiellement traité**, reste reporté.
- **Constat** : attribut 7z, filtre d'extensions du CBT, aplatissement en nom de base, lecture ZIP par nom, tri — cause de P0-1, P0-3, P1-3, P1-4, P2-1. Pendant de l'Audit 1, P4-1 (trois écrivains).
- **Fait** : chaque divergence qui perdait des pages a été corrigée (voir ces constats) ; nouveau module sans Qt `archive_names.py` (`tar_entry_name`), utilisé par l'ouverture, l'ajout, le lot CBT et l'import de métadonnées (voir C-8).
- **Fait ensuite (version 1.8.11)** : la Bibliothèque (`library_db.py` → `_comicinfo_lookup`, appelée par `_has_comicinfo_in_archive` et `_read_comicinfo_from_archive` ; `_count_pages`) suit les règles de l'ouverture : ZIP ouvert par `open_zip`, premier `ComicInfo.xml` de la racine (`comic_info.is_comic_info_name`, nom TAR passé par `tar_entry_name`), membres lus par leur objet d'archive et non par nom, dossiers et liens TAR non comptés comme pages. Avant, un CBZ à deux `ComicInfo.xml` homonymes faisait cataloguer le second (l'ouverture retient le premier) et le `./ComicInfo.xml` d'un CBT n'était pas reconnu. Vérifié sans Qt sur des archives construites pour l'occasion.
- **Constaté ensuite, corrigé (version 1.8.11)** :
  - la Bibliothèque lisait le `ComicInfo.xml` sans la limite `MAX_MEMBER_SIZE`, définie dans `archive_loader.py`, qui importe Qt, alors que `library_db` est testé sans Qt : une archive piégée avec un `ComicInfo.xml` énorme épuisait la mémoire pendant le scan. La constante est déplacée dans `archive_names.py` (sans Qt, source unique, importée par `archive_loader.py` et par `entries.py`, dont les deux copies en dur de la même valeur sont supprimées) ; `library_db._comicinfo_lookup` contrôle la taille annoncée avant la lecture (`_check_comicinfo_size`), et le comic est catalogué sans métadonnées. Les trois tests qui extrayaient la constante par AST depuis `archive_loader.py` (`test_archive_member_reading`, `test_7z_member_extraction`, `test_batch_metadata_save`) la reçoivent d'`archive_names`. Test : `tests/test_library_db.py` (`test_comicinfo_beyond_the_size_limit_is_not_read`).
  - `comic_info.read_comic_info` ouvrait le ZIP par `zipfile.ZipFile` direct et ne reconnaissait pas le `./ComicInfo.xml` d'un CBT : il passe maintenant par `open_zip` et `tar_entry_name`, comme l'ouverture du comic. Sans effet visible aujourd'hui (son seul appelant, `virtual_library_panel`, n'ouvre que des CBZ, et le nom `ComicInfo.xml` est en ASCII) : règles alignées pour un appelant futur. Test : `tests/test_comic_info.py` (`test_read_comic_info_follows_the_rules_of_opening`).
- **Reste** : une seule source de règles pour lister et lire les membres de chaque format — une quarantaine d'endroits, et plusieurs tests extraient les fonctions par AST depuis `archive_loader.py`. Reporté à une session dédiée, avec le point suivant (P4-4), dans le fichier d'idées de l'utilisateur. Unifier la clé de tri changerait l'ordre des pages d'archives existantes : à décider explicitement.

### P4-2 · Snapshot d'historique en liste blanche de 7 champs

- **Statut** : Corrigé (par la correction de P1-2 : liste d'exclusions `_NOT_IN_META`, tout nouveau champ est conservé par défaut).

### P4-3 · États mémorisés par position de page

- **Statut** : Corrigé.
- **Constat** : originaux des trois outils (P1-1), et aussi le travail non validé de la visionneuse (`_crop_by_page`, `_straighten_by_page`, `_text_blocks_by_page`, `_shapes_by_page`, `_pasted_images_by_page`, `_transp_work_img_by_page`) et `current_idx`. Vérifié : la visionneuse est non modale, la mosaïque du même panneau reste modifiable pendant qu'elle est ouverte.
- **Correction** : `image_viewer_qt.py` → `_PageWorkStore` (accès par position, rangement par clé de page, oubli du travail d'une page retirée) pour les six magasins ; `ImageViewer.current_idx` devient une propriété qui suit la page par sa clé ; `sync_image_viewers_with_document(state)`, appelée par `MosaicCanvas.render_mosaic`, ferme sans confirmation une visionneuse dont la page a été retirée du document (son travail visait une page qui n'existe plus).
- **Tests** : `tests/test_viewer_page_work.py` ; parcours complet testé manuellement (recadrage en attente sur la page 4, suppression de la page 1, validation sur la bonne page ; fermeture à la suppression de la page affichée).

### P4-4 · Les écrivains n'avaient aucune notion de « page obligatoire » ; la garde ne couvre pas `except: pass` hors boucle

- **Statut** : **Corrigé** (garde des `except: pass` ajoutée en version 1.8.11, voir « Avancement » et « Garde ajoutée » ci-dessous).
- **Fait** : `cbz_members` refuse toute page sans données (P0-5).
- **Reste** : étendre la garde de l'Audit 1, P4-4 aux `except Exception: pass` (plusieurs centaines dans `modules/qt/`, dont certains légitimes). Reporté à la même session que P4-1.
- **Avancement (version 1.8.11)** : recensement des `except` larges (`Exception`, `BaseException`, `except:` nu) dont le seul contenu est `pass`, dans `modules/qt/` et `MosaicView.py` : 441 `except` larges, dont 149 de ce type. Chacun a été relu dans sa fonction et classé :
  - **sans conséquence** (fermeture d'un objet détruit juste après, déconnexion d'un signal, nettoyage de temporaires, ouverture de l'Explorateur, son, barre de titre Windows, liste des récents…) — marqué par un commentaire `# sans conséquence : <raison>` sur la ligne du `pass` ;
  - **repli** (une valeur de repli prend le relais sans perte : vignette ou icône par défaut, dimensions ou type inconnus, page écrite telle quelle, configuration par défaut, police précédente…) — marqué `# repli : <raison>`, préfixe qui désigne aussi les endroits où un futur journal des erreurs aurait un intérêt ;
  - **à corriger** (l'utilisateur devrait être prévenu) — 14 sites, laissés sans marque : suppression de l'original échouée dans l'import de métadonnées en lot (`batch_metadata_dialog_qt._save_state_for_file`), images non téléchargées dans l'import web (`web_import_qt`, worker de téléchargement, 2 sites), GIF animé tronqué à sa modification (`AnimatedGifDialog.__init__`), `<Pages>` et `MosaicViewTrace` perdus à l'édition d'un ComicInfo.xml illisible (`comicinfo_dialog_qt._build_xml_bytes`), message de succès malgré des copies échouées (`user_guide_qt`, 3 exports) ou un effacement échoué (`panel_widget._clear_config_file`, `_clear_clipboard_files`, 3 sites), ComicInfo.xml illisible sans explication (`archive_loader` → `_on_finished`, `panel_widget._reload_comicinfo_metadata`), conversion de mode d'image refusée sans message (`image_processing_qt.apply_adjustments`).
  135 sites sont marqués (aucun changement de comportement).
- **Reste ensuite** : corriger les 14 sites, un par un après validation de l'utilisateur ; puis un test de garde qui refuse tout `except` large réduit à `pass` sans l'un des deux marqueurs.
- **Corrigés (version 1.8.11)** :
  - `batch_metadata_dialog_qt._save_state_for_file` — original d'une conversion impossible à supprimer : la raison est rendue par la fonction, transmise par `_SaveWorker.done(str)`, et `_on_save_done` (thread de l'interface) compte le fichier comme traité et l'ajoute aux erreurs du lot (nouvelle clé `messages.errors.batch_source_not_deleted`, traduite dans toutes les langues et vérifiée en mode « par clés » du skill `verify-translation`), comme les conversions par lot. Tests : `tests/test_batch_metadata_save.py` (`test_conversion_reports_original_that_could_not_be_deleted`, `test_conversion_with_original_deleted_reports_nothing`).
  - `web_import_qt._DownloadWorker.run` (2 sites) — images manquées de l'import web : chaque échec est rangé (`not_downloaded` avec le message réseau, `too_large`, `not_image`) et émis avec les entrées (`finished(new_entries, failures)`) ; `WebDownloadController._on_finished` ouvre la fenêtre « Fichiers ignorés » quand des images ont été ajoutées et d'autres manquées (rien après une annulation ou si le document a changé ; le cas « aucune image » garde son message). Nom affiché par `_url_display_name` : jamais l'adresse entière, qui peut porter un identifiant et un mot de passe. Trois clés `messages.warnings.files_ignored.web_*`, traduites et vérifiées (mode « par clés »). Test : `tests/test_web_import_limits.py` (`test_missed_image_name_never_contains_credentials`).
  - `animated_gif_dialog_qt` — extraction des images d'un GIF animé à modifier : déplacée dans `_extract_gif_frames`, qui rend les images lues et l'arrêt de la lecture ; `show_animated_gif_dialog` l'appelle avant d'ouvrir la fenêtre : aucune image lue → la fenêtre ne s'ouvre pas (message) ; lecture arrêtée → message « {read} images sur {total} » par-dessus la fenêtre. Sauter l'image fautive a été écarté après essai sur des GIF abîmés : Pillow ne peut pas atteindre les images suivantes, qui ne contiennent de toute façon souvent que les différences avec la précédente. Le cas se produit surtout par manque de mémoire, un GIF tronqué étant déjà refusé au chargement. Trois clés `dialogs.gif_animated.read_error_*`, traduites et vérifiées. Test : `tests/test_gif_frame_extraction.py`.
  - `comicinfo_dialog_qt._build_xml_bytes` — le `pass` n'était que le dernier maillon : le formulaire de modification s'ouvrait vide, sans explication, quand le ComicInfo.xml existant était illisible, et l'enregistrer remplaçait tout son ancien contenu. `_populate_from_entry` repère maintenant ce cas (`_existing_xml_unreadable`) et `show_comicinfo_edit_dialog` affiche un message par-dessus le formulaire (enregistrer remplace tout, fermer sans enregistrer garde l'ancien contenu ; pas de mention d'Annuler, absent quand la Bibliothèque réécrit directement le CBZ). Le `pass` de `_build_xml_bytes` devient un repli marqué. Deux clés `comicinfo.unreadable_*`, traduites et vérifiées. Test manuel seulement (fenêtre Qt).
    Constaté au passage et corrigé avec l'accord de l'utilisateur : même lisible, le ComicInfo.xml perdait à l'enregistrement du formulaire les balises que celui-ci ne gère pas (ajoutées par d'autres logiciels), et toute réécriture (`_serialize_comic_xml`) aplatissait en texte seul celles qui ont des attributs ou des sous-balises. Le formulaire les recopie désormais (`comic_info.unmanaged_elements`), et l'écriture les reproduit fidèlement (`_element_lines`). Tests : `tests/test_comic_info.py` (3 tests).
  - `user_guide_qt` (3 sites : `export_tengwar_fonts`, `export_wilhelm_scream`, `save_all_icons`) — copies échouées avalées : succès annoncé sans mentionner les fichiers manquants, échec complet présenté comme « aucun fichier trouvé ». Les trois boucles identiques sont remplacées par `_copy_resource_files` (copiés + échecs avec leur raison) et `_report_export` (succès, plus fenêtre « Fichiers ignorés » si une partie a échoué ; erreur avec la vraie raison si tout a échoué). L'écrasement sans question d'un fichier de même nom dans le dossier choisi est gardé, par décision de l'utilisateur. Une clé `messages.warnings.files_ignored.not_copied`, traduite et vérifiée. Test : `tests/test_resource_export_copy.py`.
  - `panel_widget._clear_config_file`, `_clear_clipboard_files` (3 sites) — « effacé avec succès » affiché malgré un échec. Chaque échec est relevé, et `_show_clear_result` affiche soit le succès, soit le nombre d'éléments restants avec la première raison. Même défaut, ajouté à la demande de l'utilisateur, sur « Effacer les fichiers temporaires » : `temp_files.cleanup_all_temp_files` rend maintenant ses éléments non effacés (ses deux `pass` marqués « sans conséquence » à l'étape de marquage le restent pour le nettoyage automatique de la fermeture, qui ignore ce retour, mais pas pour le bouton). Quatre clés `help.config_*`, traduites et vérifiées. Test : `tests/test_temp_files_logs.py` (`test_cleanup_reports_what_could_not_be_deleted`).
  - `archive_loader` → `_on_finished`, `panel_widget._reload_comicinfo_metadata` (2 sites) — à la relecture, ces `pass` n'attrapent qu'une erreur inattendue : un ComicInfo.xml illisible ne lève rien, sa lecture rend « pas de métadonnées », et l'onglet Métadonnées disparaissait sans explication. Correction : `comic_info.comic_info_unreadable` (déduit de l'état : ComicInfo.xml présent, aucune métadonnée lue) et `shows_metadata_tab`, utilisée par `TabBar.update` et `PanelWidget._sync_tabs_with_document` ; l'onglet reste affiché et `MetadataTab.refresh` y place un message et le bouton Modifier. Les deux `pass` deviennent des replis marqués. Une clé `tabs.comicinfo_unreadable`, traduite et vérifiée. Test : `tests/test_comic_info.py` (`test_unreadable_comicinfo_still_shows_the_metadata_tab`).
  - `image_processing_qt.apply_adjustments` — conversion de mode d'image refusée sans message. Toutes les conversions possibles vers les modes de l'outil ont été essayées avec Pillow : seul un mode réellement rencontré échoue, `LAB` (TIFF CIELab) vers L, LA, CMYK, P et 1 bit. `_convert_mode` passe alors d'abord par RGB (`image_ops._to_writable_mode`) ; un refus restant n'est plus avalé et s'affiche par le message existant de l'outil (`messages.errors.image_mode_failed`). Aucune nouvelle clé. Test : `tests/test_image_formats.py` (`test_cielab_image_converts_to_every_image_mode`, 7 cas).
  Les 14 sites à corriger le sont tous : il ne reste dans `modules/qt/` et `MosaicView.py` aucun `except` large réduit à `pass` sans l'un des deux marqueurs (136 sites marqués).
- **Garde ajoutée (version 1.8.11)** : `tests/test_check_no_silent_pass.py` échoue sur tout `except` large (`Exception`, `BaseException`, `except:` nu, tuple qui en contient un) réduit à `pass` dont la ligne ne porte pas `# sans conséquence : <raison>` ou `# repli : <raison>` ; un second test vérifie le détecteur sur des cas construits (marqué, non marqué, commentaire libre, marqueur sans raison, exception étroite). Règle inscrite dans `CLAUDE.md`. **P4-4 : Corrigé.**
- **Complément (version 1.8.11) — journal des erreurs inattendues** : les erreurs que rien ne rattrape (exception non interceptée, y compris dans un slot Qt ou un thread, et plantage natif) ne laissaient aucune trace dans l'exécutable, qui n'a pas de console (`sys.stderr` vaut `None`), et se perdaient dans le terminal en mode développement. `modules/qt/error_log.py` (sans Qt), lancé par `MosaicView.main` (`start`), installe `sys.excepthook`, `threading.excepthook` et `faulthandler` vers un fichier par session dans `%APPDATA%\MosaicView\errors` (rien n'est écrit sans erreur ; 10 jours, 20 fichiers, 1 Mo par session au plus ; écriture forcée sur le disque à chaque trace), dans les deux modes, le terminal recevant toujours la trace. Consultation : fenêtre `error_log_dialog_qt` (menu « À propos » de la barre de menus et du menu contextuel du canvas vide, section Configuration du mode d'emploi), qui affiche tous les journaux du plus récent au plus ancien, les copie ou les envoie par mail (presse-papiers puis `mailto:`). Les sites marqués `# repli :` restent sans trace : ils ne cachent qu'une valeur par défaut, et le journal ne garde que les erreurs inattendues. Tests : `tests/test_error_log.py` (7). Détail : skill `config-storage`.

### P4-5 · Trois implémentations de « mettre à la Corbeille »

- **Statut** : Corrigé.
- **Constat** : `file_operations_qt._safe_delete` et `PanelWidget._safe_delete_file` se repliaient sur une **suppression définitive** si send2trash manquait ; l'import de métadonnées avait son propre repli `SHFileOperationW` ; la Bibliothèque appelait send2trash directement.
- **Correction** : `utils.send_to_trash(path)`, seule implémentation (send2trash, repli `SHFileOperationW` avec `FOF_ALLOWUNDO`, `OSError` si le fichier n'a pas été déplacé, jamais de suppression définitive à la place).
- **Tests** : `tests/test_check_single_trash.py` (garde AST : aucun import de send2trash ni appel `SHFileOperationW` hors `utils.py`).

---

## Constats ajoutés pendant les corrections

| # | Priorité | Constat | Correction | Tests |
|---|---|---|---|---|
| **C-1** | P2 | `DuplicateFilenameDialog` ne suivait ni le thème ni `get_current_font()` (un `font-size` de stylesheet écrasait la police) ; son texte parlait de « doublons » et de « problèmes dans certains lecteurs », alors que trois fichiers ou plus peuvent porter le même nom et que la vraie conséquence était une perte de page. | `_retranslate` réapplique thème, police et hauteur du message ; texte réécrit dans toutes les langues (« tous ces fichiers sauf le premier recevront un suffixe (_2, _3…) »). | Manuel |
| **C-2** | P2 | En corrigeant P0-5 : une page illisible apparaissait à la fois dans « Images corrompues » et « Fichiers ignorés », deux fenêtres superposées. | `_attach_read_errors` rattache l'erreur à la page (raison technique dans l'info-bulle) ; seule « Images corrompues » s'affiche. | `test_archive_member_reading.py` |
| **C-3** | P0 | Un TIFF multipage **contenu dans une archive** n'apparaissait que comme une seule page : ses autres pages n'étaient ni visibles, ni copiables, ni déplaçables une à une, et les lecteurs de BD n'en montrent que la première. | **Décision définitive de l'utilisateur, inscrite dans `CLAUDE.md`** : à l'ouverture ou à l'ajout d'une archive, chaque page devient une page du comic, en restant un TIFF (`entries.expand_multipage_tiffs`, `split_multipage_tiff_pages` : compression, DPI et profil ICC conservés ; un TIFF non découpable reste entier). Les conversions par lot ne découpent pas (décision de l'utilisateur). | `test_tiff_pages.py` |
| **C-4** | P0 | Enregistrer sous sur un fichier ouvert non-CBZ (CBR…) avec son propre nom : le CBR était remplacé par un CBZ gardant l'extension `.cbr`, l'original perdu (cas réel de P2-9, trouvé par les messages de diagnostic). | Refus dans `save_as_cbz` (voir P2-9). | Manuel |
| **C-5** | P2 | Entrer dans un sous-dossier pendant « Enregistrer sous » faisait échouer l'enregistrement (« Internal C++ object (QGraphicsTextItem) already deleted ») : la reconstruction de la mosaïque détruisait le texte rouge « Sauvegarde X% ». Défaut antérieur à l'audit, révélé par le test de P3-4. | `_SavingOverlay` vérifie que son texte existe encore et le recrée sinon. | Manuel (confirmé par messages de diagnostic) |
| **C-6** | P2 | Enregistrer par-dessus un fichier en lecture seule affichait le message brut de Windows (« [WinError 5] Accès refusé »), dans la langue de Windows et non celle de l'application. | `ReadOnlyFileError` (sous-classe de `PermissionError`, texte `messages.errors.file_read_only` traduit à chaque affichage, dans toutes les langues). | `test_utils_replace_retry.py` |
| **C-7** | P3 | Le changement de thème ralentissait au fil de la session (jusqu'à 4,9 s mesurées) : `QMenu.clear()` et `QMenuBar.clear()` ne détruisent pas les sous-menus, qui s'accumulaient (18 → 45 menus cachés) et que Qt restylait à chaque changement. Mesures : le reste du coût vient de la Bibliothèque cachée (~770 widgets), gardée vivante volontairement — non modifiée (décision de l'utilisateur). | `menubar_qt._delete_submenus` avant chaque repeuplement d'un menu et chaque reconstruction de la barre ; nombre de menus stable (17 à 20) après correction. | Manuel (mesures avant/après) |
| **C-8** | P2 | Lot CBT → CBZ et import de métadonnées en lot : membres TAR enregistrés sous `./page.jpg` écrits tels quels ; le CBZ produit avait un faux sous-dossier « . » à aplatir avant tout enregistrement (divergence avec l'ouverture, qui normalisait déjà). | `archive_names.tar_entry_name`, partagé par tous les lecteurs. | `test_batch_metadata_save.py` |

**Non reproduit** : un message Qt « Ambiguous shortcut overload: Ctrl+Z » vu une fois par l'utilisateur ; un diagnostic a été installé, le message n'est jamais réapparu, et le diagnostic a été retiré.

---

## Opérations sans problème trouvé

*No issue found after code analysis and reproduction* pour :

- **Remplacement atomique** : Appliquer `.cbz`, Enregistrer sous, création de CBZ, sélection, export vers un dossier, recompression, Bibliothèque et lots passaient tous par `replace_file_atomically` ou `_interruptible_zip_writer` ; `_check_disk_space` présent dans les panneaux ; ailleurs, un disque plein laisse l'original intact.
- **Fermeture au pire moment** : soit l'ancien fichier, soit le nouveau, jamais un fichier partiel ; un plantage entre le renommage et la suppression de la source laisse les deux (seul résidu : un temporaire, P3-1).
- **Lots CBR/CB7/CBT/PDF** : page en échec → CBZ jeté et source gardée ; nom de destination libre ; mélange de réussites, d'échecs et d'une interruption : chaque source n'est supprimée qu'après sa propre réussite (hors P0-1 et P0-4).
- **Suppression, découpe, jointure, conversion, aplatissement** : pages désignées par identité, état sauvegardé avant et après.

## Fausses pistes écartées

- Le trio d'outils (profondeur de couleur, effets, mode d'image) ne peut pas changer une page JPEG de format : les choix incompatibles sont grisés.
- Les noms générés par la renumérotation automatique du lot PDF sont toujours uniques (3000 cas aléatoires testés).
- L'annulation d'une rotation fait bien son retour arrière (`_finish_cancel`).
- Les fichiers du presse-papiers sont conservés 12 h à la fermeture.
- La lenteur du changement de thème ne venait pas de l'écriture atomique de la configuration (P3-2) : 3 à 15 ms mesurées par enregistrement.

---

## Missing tests

**Recommandés par l'audit et ajoutés** (suite passée de 490 à 558 tests) :

| Fichier | Couvre |
|---|---|
| `test_7z_member_extraction.py` | P0-1 |
| `test_batch_metadata_save.py` | P0-2, P1-3, P2-2, C-8 |
| `test_batch_unique_flat_name.py`, `test_batch_copy_pages.py` | P0-3, P2-1 |
| `test_batch_zip_writer.py` | P0-4, P2-2 |
| `test_utils_atomic_write.py` | P0-5 (`cbz_members`), P1-3, P3-1, P3-3, P3-5 |
| `test_archive_member_reading.py` (nouveau) | P0-5, P1-3, P1-4, C-2 |
| `test_undo_redo_keys.py` | P1-1, P1-2 |
| `test_tiff_pages.py` | C-3 |
| `test_operation_running.py` | P2-5, P3-4 |
| `test_config_manager.py` | P3-2 |
| `test_utils_replace_retry.py` | C-6 |
| `test_viewer_page_work.py` (nouveau) | P4-3 |
| `test_check_single_trash.py` (nouveau) | P4-5 (garde) |

**Toujours manquants** (code non exécutable sans Qt en l'état) :

- refus d'enregistrer une page sans données dans les fenêtres d'enregistrement (`_check_pages_have_data`) ;
- refus d'une destination égale à l'original (P2-9, C-4) ;
- relecture avant suppression dans les fins différées (`_after_save_as`, `_after_cbz_converted`, Bibliothèque) ;
- recréation du texte « Sauvegarde X% » (C-5) et destruction des sous-menus (C-7) ;
- fermeture de la visionneuse quand sa page est retirée (`_sync_with_document`) ;
- commandes grisées pendant une opération (seule l'existence des clés est vérifiée).

## Recommended fixes

Tous les correctifs P0 et P1 recommandés ont été appliqués, puis les P2 retenus par l'utilisateur, tous les P3, et P4-2, P4-3 et P4-5. Restent ouverts :

1. **P4-1** — une seule source de règles pour lire les archives (session dédiée).
2. **P4-4** — garde contre les `except Exception: pass` hors boucle : corrigé en 1.8.11 (sites relus et marqués, 14 sites corrigés, test de garde `test_check_no_silent_pass.py`).
3. **P2-3, P2-6, P2-8** — non corrigés par décision de l'utilisateur ; **P2-10** — comportement de Windows.
4. **Coût de la Bibliothèque cachée au changement de thème** (C-7) — laissé tel quel : la fenêtre est gardée vivante volontairement.

---

# Audit 3 — Machines à états et transitions impossibles (version 1.8.8)

- **Période** : octobre 2026 (audit, puis corrections intégrées à la version 1.8.8, pas encore publiée au moment des corrections).
- **Objet** : reconstruire les machines à états implicites de l'application (document d'un panneau, chargement, opération en arrière-plan, producteurs de pages asynchrones, historique et indicateur « modifié », sauvegarde, fermeture, visionneuse, macro, double panneau, Bibliothèque, lots) et rechercher les états incohérents ou impossibles : document fermé mais encore utilisé, opération annulée dont le résultat s'applique quand même, historique qui ne correspond plus au contenu, action appliquée au mauvais objet ou au mauvais panneau.
- **Méthode** : lecture du code réel (`MosaicView.py`, `modules/qt/`), de ce fichier (audits 1 et 2) et des tests ; reconstitution des états à partir des drapeaux, références `None`, workers, compteurs (`doc_generation`) et fermetures de dialogues ; recherche des transitions qui contournent les gardes existantes. Aucune reproduction dans l'application pendant l'audit (Qt non lancé) ; corrections ensuite, testées par l'utilisateur dans l'application.
- **Code de référence** : MosaicView 1.8.8 (état du dépôt, qui contient les corrections des audits 1 et 2 — la demande d'audit parlait de 1.8.7).

## Executive summary

| Priorité | Constats | Certitude | Statut |
|---|---|---|---|
| **P0** — intégrité des données | 2 | 1 Confirmed, 1 Very likely | 2 corrigés |
| **P1** — intégrité de l'état ou du contexte | 6 | 4 Confirmed, 2 Very likely | 6 corrigés |
| **P2** — état fonctionnel incorrect | 7 | 4 Confirmed, 1 Very likely, 2 Possible | 7 corrigés (P2-1 avec P0-1, les six autres dans un second temps) |
| **P3** — robustesse | 4 | 3 Confirmed, 1 Possible | 4 corrigés (dans un troisième temps) |
| **P4** — maintenabilité | 3 | — | Non traités |
| **Constats ajoutés pendant les corrections** | 9 | Confirmed | 9 corrigés |

**Machines à états identifiées** : document d'un panneau (`AppState` : `current_file`, `images_data`, `modified`, `doc_generation`, `disk_signature`, `history`/`history_index`, `running_operations`), chargement (`ArchiveLoader._worker`, `PdfLoader._worker`/`_process`, `_pending_load_paths`, `_image_worker`, `canvas._loading`), opération en arrière-plan (rotation, miroir, redressement automatique, redimensionnement, conversion, écriture d'un enregistrement), producteurs de pages asynchrones (numérisation, import web, fusion d'archive ou de PDF, ajout d'images, ComicVine, fichier modifié par un programme externe), sauvegarde et sa confirmation non modale, fermeture et ses fenêtres, visionneuse (`_PageWorkStore`), macro (enregistrement, lecture), double panneau, Bibliothèque (base, chargement de la liste, scan), import de métadonnées en lot, lots.

**Transitions les plus sensibles**

1. Une opération en cours pendant qu'autre chose modifie le document : fin d'un producteur asynchrone, ou validation d'une fenêtre non modale ouverte avant l'opération. Le verrou des audits précédents (Audit 1, P1-1 ; Audit 2, P2-5) n'était vérifié qu'à l'entrée des commandes.
2. L'annulation de cette opération, qui restaurait le sommet de l'historique (ou `modified` d'avant) en effaçant ce qui s'était passé entre-temps.
3. Une confirmation destructive appliquée à l'objet courant au moment du clic, et non à l'objet annoncé.
4. Un chargement remplaçant (archive, PDF) pendant lequel d'autres sources écrivaient dans la liste de pages qu'il allait remplacer.

**Causes communes** : il n'existait pas de notion unique d'« activité en cours sur ce document » (neuf drapeaux distincts, chacun connu d'une partie du code), et les fins différées ne refaisaient pas toutes les mêmes contrôles. C'est le prolongement de la cause « fin d'opération différée qui ne vérifie pas que le contexte n'a pas changé » de l'Audit 1, et de son P4-2 (convention « état avant et après » non imposée), qui reste ouvert.

**Solidité constatée** : `doc_generation` capturé par toutes les opérations asynchrones ; à la fermeture d'un document, `_close_comic_child_dialogs` ferme toutes les fenêtres dont le panneau est le parent, et toutes traitent leur fermeture comme une annulation, ce qui coupe la quasi-totalité des rappels périmés ; chargement d'archive, macro, fin de sauvegarde (`content_unchanged`) et indicateur « modifié » dans les séquences Ouvrir → Modifier → Enregistrer → Annuler/Refaire.

### Causes communes

| Cause | Constats concernés |
|---|---|
| Verrou d'opération vérifié à l'entrée des commandes seulement | P0-2, P1-1, P1-2 (→ P4-1) |
| Ajout sans état d'historique « après » | P1-3 (→ Audit 1, P4-2) |
| Fin différée qui relit l'objet courant au lieu de l'objet capturé | P0-1, P2-1, P1-5, C-3 (→ P4-2) |
| Donnée rattachée à une position (page ou point d'historique) ou à un nom | P2-3, P2-6 (→ P4-3 ; Audit 2, P1-1) |
| Action concurrente non exclue, ou exclue trop tard | P2-2, P2-7, C-7 |
| Chargement remplaçant sans exclusion des autres producteurs | P1-4, C-1, P3-2 |
| Indicateur unique partagé par des opérations concurrentes (booléen, position d'affichage) | P3-1, C-8 |
| Panneau masqué considéré comme vide | P1-6, C-6 |

---

## P0 — Data integrity

### P0-1 · Bibliothèque : « Supprimer la base » met à la Corbeille la base ouverte au moment de la confirmation, pas celle annoncée

- **Certitude** : Confirmed (code) · **Gravité** : moyenne à haute (Corbeille ; suppression définitive sur un partage réseau ou une clé USB, Audit 2, P2-10) · **Statut** : Corrigé
- **Code** : `library_window.py` → `LibraryWindow._action_delete_db`, `_on_delete_accepted`, `_action_open_db` ; `library_dialogs.py` → `ConfirmDeleteDialog`.
- **Constat** : le dialogue de confirmation (non modal) gardait le chemin de la base uniquement pour l'afficher ; `_on_delete_accepted` relisait `self._db.db_path` à la validation. Rien ne fermait le dialogue quand une autre base était ouverte.
- **Scénario** : base A ouverte → Supprimer la base → sans répondre, ouvrir la base B (bases récentes, ou dépôt d'un `.mvdb`) → Confirmer.
- **Comportement constaté** : `B.mvdb` et `B.mvdb.old` partaient à la Corbeille ; A restait intacte.
- **Attendu** : supprimer A, ou rien si A n'est plus la base ouverte.
- **Correction** : la base visée est capturée au lancement (`db = self._db`) et transmise à la suite ; `_same_db(current, expected)` (même objet, ou même fichier `.mvdb` rouvert, comparé par `is_same_path`) conditionne la suppression. Le test de l'utilisateur a montré qu'un refus silencieux laissait croire la base A supprimée (C-2) : les confirmations liées à la base (`self._db_dialogs`) sont désormais fermées par `_close_db_dialogs()` dès que la base change (création, ouverture, fermeture, suppression).
- **Tests** : `tests/test_library_deferred_db_actions.py` (`_same_db` extraite par AST : même objet, autre base, même fichier rouvert, aucune base). Fermeture de la confirmation : test manuel, validé.
- **Interactions** : Bibliothèque × fenêtres non modales × bases récentes × dépôt de `.mvdb`. Même famille que l'Audit 1, P1-4 (suppression de la sélection présente au clic).

### P0-2 · Annuler une rotation, un miroir, un redressement ou un redimensionnement efface les pages ajoutées pendant le traitement, ou rend « non modifié » un document qui ne l'est plus

- **Certitude** : Very likely (mécanisme établi par le code) · **Gravité** : haute pour une page numérisée (irrécupérable sans renumériser), moyenne sinon · **Statut** : Corrigé
- **Code** : `image_transforms_qt.py` → `_run_transform._finish_cancel` (rollback) ; même schéma dans `deskew_qt.py` ; `resize_dialog_qt.py` → `_start_resize_worker._restore_after_cancel` (`state.modified = modified_before`, `pop_last_state`). Producteurs qui écrivaient sans tenir compte d'une opération : `scan_dialog_qt.py` → `ScanController._on_finished`, `_add_entry_to_mosaic` ; `web_import_qt.py` → `_add_entries_to_mosaic` ; `import_merge_qt.py` → `on_finished` ; `pdf_loading_qt.py` → `import_and_merge_pdf.on_finished` ; `panel_widget.py` → `_start_image_load.on_finished` ; fenêtres ComicVine ; `PanelWidget._on_non_image_file_modified`.
- **Constat** : un producteur n'est pas une opération (`running_operations`) ; sa fin modifiait le document pendant une rotation, et l'annulation de celle-ci supposait que rien d'autre n'avait changé.
- **Scénarios** : (a) numérisation lancée → rotation de 30 pages → la page arrive pendant la rotation → Annuler : le rollback remet `images_data` au sommet de l'historique, pris avant la rotation, sans la page numérisée, qui n'existe dans aucun état. (b) Comic non modifié → fusion d'archive lancée → redimensionnement → la fusion arrive (`modified=True`) → Annuler le redimensionnement : `modified = modified_before = False` ; la fermeture ne demande rien et la fusion est perdue. `pop_last_state` dépilait aussi le sommet courant, éventuellement l'état poussé par une autre action.
- **Attendu** : aucun ajout dans le document tant qu'une opération tourne ; une annulation ne défait que ce que l'opération a fait.
- **Correction** :
  - `AppState.run_when_idle(fn)` (`state.py`) : exécute `fn` tout de suite si aucune opération ne tourne, sinon le range dans `pending_when_idle` avec le `doc_generation` du moment ; `end_operation` les exécute quand la dernière opération se termine, en abandonnant ceux d'un document fermé ou remplacé. `force_close_file` vide la file.
  - Branché sur la numérisation (`_add_scanned_page`), l'import web (`_append_entries`), les fusions d'archive et de PDF (`_append_merged`), l'ajout d'images, les trois écritures ComicVine, le fichier non-image modifié à l'extérieur (`_apply_non_image_modification`), la planche d'une jointure, la renumérotation après la question de la première page, les éditeurs ComicInfo.xml et NFO.
  - `_write_document_with_progress` : un ajout appliqué par l'`end_operation` de son verrou interdit de marquer le document enregistré (C-4).
  - `_restore_after_cancel` ne remet `modified` et ne dépile l'historique que si son sommet est encore celui du lancement (`top_at_start`).
- **Tests** : `tests/test_operation_running.py` (ajout immédiat sans opération, mis en attente jusqu'à la fin de la dernière opération, abandonné si le document a changé, ajout pendant une écriture non marqué enregistré). Scénario (b) validé par l'utilisateur ; scénario (a) non testé dans l'application (aucun scanner pendant le test).
- **Interactions** : opérations en arrière-plan × numérisation × import web × fusion × ComicVine × historique × enregistrement.

---

## P1 — State/context integrity

### P1-1 · Le verrou d'opération n'était pas revérifié à la validation des fenêtres non modales

- **Certitude** : Confirmed (code : aucune de ces validations ne relisait `operation_running`) · **Statut** : Corrigé
- **Code** : `ResizeDialog._on_ok`/`_finish_resize` ; `PanelWidget._delete_selected_qt._do_delete` ; `split_dialog_qt.split_page` (`_on_confirmed`) ; `IcoCreatorDialog._on_validate_final` ; `MergeDialog._finish_join` ; `AnimatedGifDialog._create_animated_gif` ; `conversion_dialogs_qt.perform_conversion` ; `duplicate_detection_qt.delete_entries_by_index` ; chaîne de validation de l'enregistrement (`_run_validation_chain` → `_on_validated`, `_confirm_if_changed_on_disk`) ; `file_close_qt.close_file._apply_and_close` (« Enregistrer et fermer » appelle `apply_new_names_cb` sans passer par le blocage des commandes) ; éditeurs NFO et ComicInfo ; `NameEdit` (champ de nom de la mosaïque).
- **Constat** : la fenêtre ouverte avant le lancement d'une opération restait validable pendant celle-ci.
- **Scénarios et comportements constatés** : OK dans Redimensionner pendant une rotation → deux workers écrivent les mêmes entrées (`img`, `bytes`) ; « Oui » à la suppression ou validation d'une découpe → état intermédiaire poussé dans l'historique, que l'annulation de la rotation prenait pour l'état d'avant (annulation partielle) ; « Enregistrer et fermer » pendant la rotation → CBZ écrit avec des pages à moitié traitées, confirmation « Enregistré », puis comic laissé ouvert sans explication.
- **Attendu** : la suite différée refait le contrôle, comme le faisait déjà `_macro_run`.
- **Correction** : chacune de ces suites relit `state.operation_running()` et reste sans effet (la fenêtre reste ouverte quand c'est possible) ; `_document_still_writable(state)` pour toutes les suites d'enregistrement ; `apply_new_names` et `create_cbz_from_images` refusent d'entrée (`_done(False)`) ; `ThumbnailItem._activate_name_edit` n'ouvre pas le champ de nom. La planche d'une jointure et la renumérotation après question sont différées (`run_when_idle`) plutôt que refusées. Choix validé : refus silencieux, comme les commandes grisées (aucun message générique « opération en cours » n'existe ; en créer un demanderait une nouvelle clé de traduction dans toutes les langues).
- **Tests** : test manuel, validé (redimensionnement et suppression confirmés pendant une rotation). **Test manquant** : un garde AST qui imposerait la relecture du verrou dans toute suite différée (nécessite d'abord un contrôle commun, voir P4-2).
- **Interactions** : fenêtres non modales × opérations × historique × enregistrement × fermeture.

### P1-2 · Fin de conversion : verrou levé et état « après » différé jusqu'à la réponse à « Supprimer les originaux ? »

- **Certitude** : Confirmed (code) · **Statut** : Corrigé
- **Code** : `conversion_dialogs_qt.py` → `perform_conversion` (`on_finished`, `_cleanup`, `_after_complete`), `show_conversion_complete_dialog._handle_action`.
- **Constat** : `_cleanup`, connecté à la fin du worker, appelait `end_operation` alors que la question restait affichée ; l'état « après conversion » n'était poussé qu'à la réponse.
- **Scénario** : action X (recadrage) → conversion → la question s'affiche → Ctrl+Z → réponse.
- **Comportement constaté** : Ctrl+Z défaisait la conversion **et** X ; la réponse appelait `save_state()`, qui coupait la branche Refaire : X était irrécupérable.
- **Correction** : la conversion reste inscrite jusqu'à la réponse (`awaiting_choice`) ; `_after_complete` pousse l'état puis appelle `end_operation`. Fermer la fenêtre vaut « Garder les deux » (son `closeEvent` rappelle l'action), le verrou est donc toujours libéré. `_after_complete` est sans effet si le document a changé (C-5).
- **Tests** : test manuel, validé. **Lien** : Audit 2, P2-6 (même fenêtre, autre défaut : choix appliqué sans vérifier que les pages converties existent encore — non corrigé par décision de l'utilisateur).

### P1-3 · Six ajouts sans état d'historique « après » eux

- **Certitude** : Confirmed (code) · **Statut** : Corrigé · **Lien** : Audit 1, P1-2 (même cause) et P4-2 (convention non imposée, toujours ouvert)
- **Code** : `scan_dialog_qt._add_entry_to_mosaic` ; `web_import_qt._add_entries_to_mosaic` ; `clipboard_qt.paste_from_system_clipboard` (branche CF_DIB) ; `import_merge_qt.on_finished` ; `pdf_loading_qt.import_and_merge_pdf.on_finished` ; `mosaic_canvas.NameEdit` (état « avant » à la première frappe, rien après).
- **Scénario** : supprimer une page → numériser une page → Ctrl+Z → Ctrl+Y.
- **Comportement constaté** : Ctrl+Z défaisait la suppression et la numérisation ensemble ; Ctrl+Y rétablissait la suppression, mais la page numérisée ne revenait jamais (absente de tout état).
- **Correction** : `save_state` avant et après chacun de ces ajouts ; pour le champ de nom, `NameEdit._save_state_after_rename` à la validation (Entrée ou perte du focus) si le nom a changé (`_renamed`).
- **Tests** : `tests/test_additions_undo_point.py` (numérisation et import web extraits par AST : sommet de l'historique = contenu après l'ajout, Refaire rend la page numérisée). Collage, fusions et renommage : test manuel, validé.

### P1-4 · Pendant le chargement d'une archive ou d'un PDF, des pages venues d'ailleurs étaient écrites dans la liste que la fin du chargement remplace, et l'historique gardait un état étranger au document

- **Certitude** : Very likely · **Statut** : Corrigé
- **Code** : `panel_widget.py` → `_on_inter_panel_drop`, `_on_loading_finished` ; `archive_loader.py` → `ArchiveLoader.load`, `_on_finished` ; `pdf_loading_qt.py` → `PdfLoader._start_pdf_loading`, `_on_finished`. Seule l'ouverture de fichiers était mise en attente (`_pending_load_paths`).
- **Scénario** : le panneau 2 charge un gros CBZ X → trois pages sont glissées depuis le panneau 1 → fin du chargement → Ctrl+Z → Appliquer.
- **Comportement constaté** : les pages quittaient le panneau 1 puis étaient écrasées par les pages chargées ; l'historique du panneau 2 valait `[pages déposées, X]`, Annuler était disponible dès l'ouverture et restaurait les seules pages déposées avec `current_file = X` ; « Appliquer » aurait remplacé le contenu de X par elles. Même perte pour une image collée, importée du web ou numérisée pendant le chargement.
- **Correction** : `archive_loader._install_loaded_pages(state, pages)`, appelée par les deux fins de chargement : pages lues suivies des pages ajoutées pendant le chargement (document alors modifié), puis `reset_history`. Le dépôt depuis l'autre panneau est refusé pendant un chargement (`_replacing_load_in_progress`), les pages restent dans le panneau source. Voir aussi C-1.
- **Tests** : `tests/test_loaded_pages_install.py` (pages lues, ajouts conservés à la suite, historique pris pendant le chargement abandonné). Glisser-déposer et import web pendant un chargement : test manuel, validé.

### P1-5 · Éditeurs ComicInfo.xml et NFO : la validation écrivait dans l'entrée capturée à l'ouverture, même si elle n'était plus dans le document

- **Certitude** : Confirmed (code) · **Statut** : Corrigé
- **Code** : `panel_widget.py` → `_open_comicinfo_for_edit`, `_open_nfo_for_edit`, `_edit_comicinfo`, `_show_nfo_dialog`, `_reload_comicinfo_metadata`. Par contraste, la modification par un programme externe vérifiait déjà la présence de l'entrée.
- **Scénario** : éditeur ComicInfo.xml ouvert → ComicInfo.xml supprimé dans la mosaïque puis rétabli par Ctrl+Z (nouvel objet) → modification d'un champ → OK → Enregistrer.
- **Comportement constaté** : les modifications allaient dans l'objet orphelin ; l'onglet Métadonnées (rechargé depuis les bytes validés) les affichait, mais le fichier enregistré gardait l'ancien XML.
- **Correction** : `_commit_comicinfo(key, …)` et `_commit_annex_text(key, …)`, communs à la création et à l'édition : entrée retrouvée par son jeton (`_entry_by_key`, `undo_redo.entry_key`) au moment de la validation ; à défaut, le `ComicInfo.xml` du document ; à défaut, le fichier est ajouté (`_append_annex_entry`). Validation pendant une opération : différée (`run_when_idle`). `save_state` avant et après sans `force` (une validation sans changement ne crée plus de point d'historique vide).
- **Tests** : la reconnaissance du jeton après recréation est couverte par `tests/test_undo_redo_keys.py` (`test_adopted_key_is_recognised`) ; flux complet : test manuel, validé après la correction de C-3.

### P1-6 · Fermer le double panneau pendant un ajout en cours dans le panneau 2 vide

- **Certitude** : Very likely · **Statut** : Corrigé
- **Code** : `MosaicView.py` → `MainWindow._close_split` (aucune question si le panneau 2 est vide et non modifié), `_finish_close_split` (seul le chargeur d'archive était annulé), `closeEvent` (ne consulte le panneau 2 que si le double panneau est affiché).
- **Scénario** : panneau 2 vide → numérisation, import web, ouverture d'un PDF ou d'images → fermeture du double panneau avant la fin → l'ajout arrive dans le panneau caché → fermeture de l'application.
- **Comportement constaté** : aucune confirmation ; une page numérisée était perdue.
- **Correction** : `MainWindow._stop_panel2_activities()` — fenêtres du document (`_close_comic_child_dialogs`), chargements (`_shutdown_loading_on_close`), opérations inscrites (`cancel_operations_on_close`), process PDF, `doc_generation += 1` (résultat d'un appel non interruptible abandonné), file `pending_when_idle` vidée. Voir C-6.
- **Tests** : test manuel, validé (import web et PDF au choix de la résolution).

---

## P2 — Functional state errors

| # | Constat initial | Certitude | Statut | Tests |
|---|---|---|---|---|
| **P2-1** | `library_window.py` → `_on_rename_accepted` : renommer la base renommait celle ouverte au moment de la validation (même mécanisme que P0-1). | Confirmed | **Corrigé** avec P0-1 (`_same_db`, fermeture de la confirmation). | `test_library_deferred_db_actions.py` |
| **P2-2** | `_on_new_db_accepted` ne vérifie pas qu'une base est en cours de chargement : la boucle `_load_batch` continue sur la base fermée (erreur SQLite dans un `QTimer`), `_load_worker` reste vrai, la Bibliothèque reste « en chargement » et la nouvelle base n'est jamais scannée. | Very likely | **Corrigé** : `_on_new_db_accepted` arrête d'abord un chargement en cours (`_cancel_db_load`, qui lève `_load_cancelled`). | Manuel (non réalisé, scénario rare) |
| **P2-3** | `undo_redo_qt.restore_state_qt` restaure la sélection par **noms** : deux pages homonymes, une seule sélectionnée → après Annuler, les deux sont sélectionnées, et une suppression suivante vise les deux. | Confirmed (mécanisme), Possible (cas) | **Corrigé** : les états de l'historique gardent les jetons des pages sélectionnées (`selected_keys`, `entry_key`), restaurés par jeton. | `test_undo_redo_keys.py` (`test_restored_selection_ignores_homonyms`) ; manuel, validé |
| **P2-4** | `PdfLoader._on_empty_pdf` ne remet pas `current_file` à `None` (contrairement à `_on_error`) : un PDF vide détecté par le worker laisse un document fantôme, et le fichier ouvert ensuite y est fusionné. | Confirmed (code), Possible (cas : pré-ouverture de plus de 5 s) | **Corrigé** : `current_file` remis à `None`, mosaïque redessinée. | Manuel (non réalisé, aucun PDF vide disponible) |
| **P2-5** | `image_transforms_qt._TransformWorker.run` avale toute exception (`except Exception: pass`) : une page qui ne pivote pas est ignorée sans message. Hors de portée du garde de l'Audit 1, P4-4 (pas de `continue`). | Confirmed | **Corrigé** : le worker range dans `failures` chaque page dont l'opération renvoie `False` ou lève une exception ; elles sont listées en fin de traitement dans la fenêtre « Fichiers ignorés » (nouvelle clé `messages.warnings.files_ignored.not_transformed`, traduite dans toutes les langues). | Manuel (non réalisé, échec difficile à provoquer) |
| **P2-6** | Valeurs des réglettes de la visionneuse (`state.*_value_by_history_index`) indexées par (position de page, `history_index`) : décalées après une suppression ou un déplacement, après saturation de l'historique ou sur une nouvelle branche. Affichage seulement. | Confirmed | **Corrigé** : clé `AppState.tool_value_key(idx)` = (jeton de la page, `undo_redo.history_point_key`, numéro unique `serial` de chaque état de l'historique), dans les 7 outils. Une autre action sur le document crée un nouveau point d'historique et remet la réglette à 0, par conception (premier scénario de test, mal posé, corrigé avant validation). | `test_undo_redo_keys.py` (`test_tool_value_key_follows_the_page`, `test_history_point_key_survives_saturated_history`) ; manuel, validé |
| **P2-7** | Import de métadonnées en lot lancé deux fois sur le même dossier : aucun garde, deux orchestrateurs traitent les mêmes fichiers (conversions en double). | Possible | **Corrigé** : `_files_in_running_batch` refuse un import dont un fichier est traité par un orchestrateur non terminé ou réservé par une confirmation encore affichée (voir C-7), contrôle refait au démarrage ; message `dialogs.batch_metadata.already_running_*`, traduit dans toutes les langues. | `test_batch_metadata_running.py` (nouveau) ; manuel, validé |

## P3 — Robustness

| # | Constat initial | Certitude | Statut et correction | Tests |
|---|---|---|---|---|
| **P3-1** | `canvas._loading` est un booléen partagé par des producteurs concurrents : le premier terminé le remet à faux, le texte d'accueil peut réapparaître sous un import encore en cours. | Possible | **Corrigé** : `MosaicCanvas.hold_empty_hint(token)`/`release_empty_hint(token)`, un jeton par chargement (`"image_load"`, le `ScanController`, `("web", numéro du dépôt)`) ; `_loading` devient une propriété en lecture seule, vraie tant qu'un jeton est tenu (une affectation oubliée échouerait au lieu de passer inaperçue). Le test a révélé C-8. | Manuel, validé |
| **P3-2** | Fichiers déposés pendant un chargement PDF qui échoue (erreur, mot de passe, PDF vide, choix de résolution annulé) : laissés dans `_pending_load_paths` sans être traités ni signalés. | Confirmed (code) | **Corrigé** : `PdfLoader._after_failed_load` fait traiter la file par `PanelWidget._run_pending_loads` sur chaque sortie en échec. Celle-ci abandonnait volontairement, et sans message, les fichiers d'un chargement échoué ou annulé (ils visaient une fusion dans ce document) : ils sont désormais **ouverts** dans le panneau resté vide, choix validé par l'utilisateur (aucune perte, aucun nouveau texte à traduire). Seuls ceux d'un document fermé entre-temps restent abandonnés. Lots triés avant la boucle (le premier lot rouvert change `doc_generation`) ; file vidée avant de fermer les fenêtres du panneau 2 masqué. | Manuel, validé |
| **P3-3** | `force_close_file` ne remet pas à zéro `first_image_dir`, `original_page_count`, `current_sort_method`/`order`, `focused_index`, `selected_dir_visual_idx` : état résiduel du document précédent (tri affiché, dossier proposé). | Confirmed | **Corrigé** : remis aux valeurs d'`AppState.__init__`, ainsi que `real_to_visual`/`visual_to_real`. | Manuel, validé |
| **P3-4** | Rotation : la mise à jour de `ComicInfo.xml` (`image_ops._update_page_xml`) s'exécute dans le thread du worker (parcours de `images_data`, réécriture de l'entrée XML, émission de `metadata_pages_signal`). Le verrou d'opération limite le risque. | Confirmed | **Corrigé** : les opérations du worker reçoivent `state=None` ; `_run_transform.on_finished` met à jour `<Pages>` une seule fois, sur le thread Qt, pour les pages réellement traitées (`failed_entries`, par identité), avant le `save_state` de fin. Règle générale ajoutée à `CLAUDE.md` (« Workers asynchrones »). | Manuel, validé |

## P4 — Maintainability

- **P4-1 · Pas de registre unique des activités d'un document** (**Non traité**). `running_operations` ne connaît que cinq opérations ; chargements, producteurs, numérisation, lecture de macro, chargement de la Bibliothèque ont chacun leur drapeau. Cause commune de P0-2, P1-1, P1-2, P1-4, P1-6. `run_when_idle` couvre les producteurs, mais pas les chargements. Piste : un registre par document distinguant chargement remplaçant, opération exclusive et producteur, consulté à l'entrée des commandes et par les fins différées.
- **P4-2 · Les fins différées ne refont pas toutes les mêmes contrôles** (**Non traité**, partiellement réduit). Chaque suite (réponse de dialogue, fin de worker) choisit parmi `doc_generation`, présence par identité ou par jeton, `operation_running`, base courante. Les corrections de cet audit ajoutent les contrôles manquants au cas par cas. Piste : un contexte capturé au lancement (génération, jetons des pages visées, objet visé) avec une méthode `still_valid()`.
- **P4-3 · Sélection positionnelle** (`selected_indices`), convertie en noms dans l'historique (**Corrigé**) : cause de P2-3 ; une insertion pendant une conversion décale aussi la sélection.
  - **Correction** : `state.PageSelection`, ensemble mutable qui retient les jetons des pages sélectionnées (`entry_key`) et les présente comme des positions dans `images_data`, recalculées à chaque lecture ; `AppState.selected_indices` devient une propriété dont l'affectation remplace le contenu. Une page insérée ou supprimée avant la sélection ne la décale plus, une page supprimée en sort, une position hors liste est ignorée. Les usages existants (test, taille, parcours, appartenance, ajout, retrait, affectation, différence d'ensembles) sont inchangés.
  - **Tests** : `test_page_selection.py` (nouveau).

---

## Constats ajoutés pendant les corrections

| # | Priorité | Constat | Correction | Tests |
|---|---|---|---|---|
| **C-1** | P1 | En corrigeant P1-4 : `PdfLoader._start_pdf_loading` vidait `images_data` au clic sur OK du choix de résolution ; une page ajoutée pendant ce choix (import web, numérisation) était effacée. | La liste n'est plus vidée (le panneau était vide à l'ouverture du PDF) ; les ajouts sont gardés par `_install_loaded_pages`. | Manuel |
| **C-2** | P2 | Test de P0-1 par l'utilisateur : le refus silencieux de la confirmation de suppression laissait croire que la base A avait été supprimée. | Confirmations liées à la base fermées dès que la base change (`_close_db_dialogs`). | Manuel, validé |
| **C-3** | P1 | Test de P1-5 par l'utilisateur : l'éditeur NFO excluait de sa détection de doublon le fichier édité **par identité d'objet** ; recréé par Annuler, il était pris pour un doublon de lui-même et l'enregistrement était refusé (« Un fichier nommé test.nfo existe déjà dans la mosaïque »). | `nfo_dialog_qt._NfoDialog._on_create` : exclusion par jeton (`undo_redo.entry_key`). | Manuel, validé |
| **C-4** | P0 (évité) | En introduisant `run_when_idle` : l'écriture d'un enregistrement étant une opération, un ajout en attente appliqué par son `end_operation` aurait été marqué enregistré alors qu'il n'était pas dans le fichier. | `_write_document_with_progress` renvoie `False` si un ajout a été appliqué en fin d'écriture. | `test_operation_running.py` (`test_addition_during_write_is_not_marked_saved`) |
| **C-5** | P3 | En corrigeant P1-2 : la fenêtre de fin de conversion, fermée avec le document, rappelait son action ; `_after_complete` poussait alors un état dans l'historique du document suivant. | Garde `doc_generation` dans `_after_complete`. | Manuel |
| **C-6** | P2 | En corrigeant P1-6 : le choix de résolution d'un PDF en attente dans le panneau 2 restait ouvert sur un process arrêté. | `_stop_panel2_activities` ferme d'abord les fenêtres du document (leur fermeture vaut annulation et fait son propre nettoyage). | Manuel, validé |
| **C-7** | P2 | Test de P2-7 par l'utilisateur : la première correction ne regardait que les imports démarrés ; deux fenêtres de confirmation pouvaient être ouvertes en même temps sur le même dossier. | Fichiers réservés tant que leur confirmation est affichée (`_pending_confirmations`, libérés à sa fermeture avant le démarrage de l'import). | `test_batch_metadata_running.py` (`test_confirmation_still_displayed_blocks_a_second_one`) ; manuel, validé |
| **C-8** | P3 | Test de P3-1 par l'utilisateur : les textes rouges de progression de deux opérations simultanées (deux imports web, numérisation et chargement…) s'écrivaient l'un sur l'autre. `canvas_overlay_qt.show_canvas_text` centrait chaque texte seul, et le lien Annuler (`web_import_qt._show_cancel_item`) se plaçait sous son texte au seul moment de son affichage. Défaut antérieur à l'audit, commun aux 27 appels du mécanisme. | Chaque widget tient la liste de ses textes (`_mv_overlay_labels`) ; `_layout_overlays` les empile, chacun suivi de son lien Annuler (rattaché par `attach_overlay_label`), l'ensemble restant centré ; réempilement à chaque affichage, mise à jour ou retrait. Aucun des 27 appels modifié. | Manuel, validé |
| **C-9** | P4 | En corrigeant C-8 : le texte « Sauvegarde X % » (`file_operations_qt._SavingOverlay`) n'utilisait pas ce mécanisme mais un texte dessiné dans la scène de la mosaïque (`QGraphicsTextItem`). L'historique du dépôt montre que les deux mécanismes étaient identiques à la version 1.0.0 : le mécanisme commun est passé ensuite à un label posé sur le canvas (fixe au défilement), la sauvegarde n'a pas suivi. Ce reliquat est la cause de l'Audit 2, C-5 (texte détruit par une reconstruction de la mosaïque pendant l'écriture, contourné en le recréant), et il échappait à l'empilement de C-8. | `_SavingOverlay` enveloppe `show_canvas_text`/`hide_canvas_text` (mêmes méthodes `update`/`remove`) ; le contournement de l'Audit 2, C-5 n'a plus lieu d'être et a été retiré. | Manuel, validé (sauvegarde pendant un import web, entrée dans un sous-dossier pendant l'enregistrement) |

En corrigeant P1-5, les éditeurs ComicInfo.xml en création réutilisent un `ComicInfo.xml` apparu entre-temps (par exemple par ComicVine) au lieu d'en ajouter un second du même nom, qui aurait bloqué l'enregistrement.

---

## State machines audited with no issue found

*No issue found after code analysis* pour :

- **Historique et indicateur « modifié »** dans les séquences Ouvrir → Modifier → Enregistrer → Annuler ; Modifier A, B → Enregistrer → Annuler B, Annuler A ; Enregistrer → Annuler → Refaire ; Annuler après saturation de l'historique (`mark_history_saved`, couverts par `tests/test_undo_redo.py`). « Modifier pendant l'enregistrement » est bloqué pendant `_write_document_with_progress` et couvert par `content_unchanged` pendant la confirmation (Audit 1, P0-1).
- **Fermeture** : `_close_comic_child_dialogs` ferme toutes les fenêtres du panneau, et leur fermeture vaut annulation (`ConfirmDialog`, `QuestionYNCDialog`, `ConfirmYNDialog`, `SaveSuccessDialog`, `DuplicateFilenameDialog`, fenêtre de fin de conversion).
- **Chargement d'archive** : `_detach_worker`, question de correction d'extension, fichiers mis en attente avec leur génération, réouverture rapide.
- **Macro** : contrôles à l'ouverture et au lancement (`_macro_run`, modèle repris par P1-1), `reading_context_changed`, rollback qui ne dépile que ce qu'il a poussé.
- **Visionneuse** : travail rangé par jeton de page (`_PageWorkStore`), fermeture si la page affichée disparaît.
- **Import web** : file `_WebDropQueue` libérée sur tous les chemins, génération, arrêt à la fermeture.
- **Bibliothèque** : arrêt du scan avant tout changement de base, `db is self._db` dans les fins d'action (Audit 1).
- **Double panneau** : invariant « singleton = panneau actif » et state explicite (`test_split_view_state.py`, `test_check_no_deferred_singleton.py`).
- **Lots** : arrêt coopératif à la sortie, écrivain interruptible, confirmation à la fermeture.

## Fausses pistes écartées

- Un dialogue de fermeture resté ouvert qui fermerait le document suivant : il est fermé avec le document par `_close_comic_child_dialogs`.
- Annuler pendant une lecture de macro : détecté par `reading_context_changed`.
- Visionneuse avec travail non validé lors d'une fermeture déclenchée par une ancienne confirmation : elle se ferme sans confirmation parce que sa page n'existe plus (comportement voulu).

## Broken or missing invariants

| Invariant | Garanti par | Violé par (avant correction) |
|---|---|---|
| Pendant une opération, rien d'autre ne modifie le document ni l'historique | entrées des commandes ; désormais aussi `run_when_idle` et les suites différées | P0-2, P1-1, P1-2 |
| Annuler une opération ramène exactement l'état d'avant | rollback / restauration | P0-2, P1-1 |
| `modified` ⇔ contenu différent du fichier | `mark_history_saved`, `content_unchanged` | P0-2 |
| Toute modification a un état avant et après | convention (Audit 1, P4-2) | P1-3 |
| L'historique ne contient que des états du document courant | `reset_history` à l'ouverture et désormais en fin de chargement | P1-4 |
| Une modification différée vise un objet encore présent | identité (découpe, ICO, suppression), désormais jeton (éditeurs) | P1-5, C-3 |
| Une confirmation destructive s'applique à l'objet annoncé | pages (Audit 1, P1-4) ; désormais base de la Bibliothèque | P0-1, P2-1 |
| Un panneau qui contient du travail est consulté à la fermeture de l'application | `closeEvent` ; désormais arrêt des activités du panneau masqué | P1-6 |
| Un document ouvert a des pages ou un chargement en cours | chargeurs, désormais aussi la fin d'un PDF vide | P2-4 |
| Une donnée rattachée à une page ou à un point d'historique suit cette page ou cet état | jetons de page (`page_key`), désormais aussi numéro unique de l'état (`history_point_key`) | P2-3, P2-6 |
| Deux traitements ne visent jamais les mêmes fichiers en même temps | lots (Audit 1) ; désormais aussi l'import de métadonnées en lot | P2-7, C-7 |

## Missing transition tests

**Recommandés par l'audit et ajoutés** (suite passée de 558 à 573 tests avec les corrections P0/P1, puis à 582 avec les corrections P2, et à 589 avec P4-3) :

| Fichier | Couvre |
|---|---|
| `test_library_deferred_db_actions.py` (nouveau) | P0-1, P2-1 |
| `test_operation_running.py` | P0-2 (`run_when_idle`), C-4 |
| `test_additions_undo_point.py` (nouveau) | P1-3 |
| `test_loaded_pages_install.py` (nouveau) | P1-4 |
| `test_undo_redo_keys.py` | P2-3 (sélection par jeton), P2-6 (`tool_value_key`, `history_point_key`) |
| `test_batch_metadata_running.py` (nouveau) | P2-7, C-7 |
| `test_page_selection.py` (nouveau) | P4-3 |

Les corrections P3, C-8 et C-9 n'ajoutent aucun test automatique : tout leur code est dans des modules Qt (canvas, chargeurs, fenêtres), que la suite n'exécute pas.

**Toujours manquants** (code non exécutable sans Qt en l'état) :

- validations différées refusées pendant une opération (P1-1) — nécessite un contrôle commun testable (P4-2) ou un garde AST ;
- verrou de conversion maintenu jusqu'à la réponse (P1-2) ;
- refus du dépôt entre panneaux pendant un chargement (P1-4) ;
- éditeurs retrouvant leur entrée par jeton (P1-5, C-3) — `_entry_by_key` est une méthode de `PanelWidget` ;
- arrêt des activités du panneau 2 masqué (P1-6, C-6) ;
- fermeture des confirmations de la Bibliothèque au changement de base (C-2) ;
- arrêt du chargement de la liste à la création d'une base (P2-2), document fantôme après un PDF vide (P2-4), échec de rotation listé (P2-5) — logique dans des fenêtres ou des workers Qt ; aucun des trois n'a pu être reproduit dans l'application (scénario rare, aucun PDF vide disponible, échec difficile à provoquer) ;
- empilement des textes de progression (C-8) : `_layout_overlays` ne dépend que des hauteurs des labels et pourrait être extrait en fonction pure de calcul des positions, testable sans Qt.

## Recommended fixes

Tous les P0 et P1 ont été corrigés, ainsi que P2-1, dans l'ordre demandé par l'utilisateur (P0-1/P2-1, P0-2 + P1-1 + P1-2, P1-3, P1-4, P1-5, P1-6), puis les constats C-1 à C-6. Les P2-2 à P2-7 ont été corrigés dans un second temps, à la demande de l'utilisateur, avec C-7 ; leurs trois nouveaux messages ont été traduits dans toutes les langues puis vérifiés (mode « par clés » du skill `verify-translation`). Les P3-1 à P3-4 ont été corrigés dans un troisième temps, avec C-8 et C-9 (empilement des textes de progression, sauvegarde rattachée au mécanisme commun, à la demande de l'utilisateur). P4-3 a été corrigé ensuite (sélection par jeton de page). Restent ouverts, reportés par l'utilisateur à une session dédiée commune à tous les P4 encore ouverts des trois audits (Audit 1, P4-1 et P4-2 ; Audit 2, P4-1 et P4-4), notée dans son fichier d'idées :

1. **P4-1** — registre unique des activités d'un document.
2. **P4-2** — contexte capturé au lancement avec `still_valid()`, qui permettrait aussi un test de garde des suites différées ; à concevoir avec P4-1 et l'Audit 1, P4-2 (enveloppe « avant/après »).

## Complément — événements de l'interface et focus des fenêtres

Examen ciblé, demandé après un défaut découvert par hasard et corrigé avant cet examen : une molette dans l'Explorateur Windows posé devant MosaicView changeait la langue par le combo des langues de la colonne d'icônes, recouvert.

**Périmètre vérifié** : hook souris global (`wheel_hook.py`, `WheelHook`, seul mécanisme de l'application qui reçoit des événements destinés à d'autres programmes) ; `_LangCombo.wheelEvent` / `wheel_from_hook` ; filtre d'événements installé sur l'application (`MainWindow.eventFilter` : changement de panneau actif au clic en double panneau, `ZoneTabNavigator.key_filter` limité à la barre de menus) ; raccourcis globaux (`QShortcut` de `MainWindow`, contexte fenêtre, sauf F5 en contexte application, qui ne fait que redessiner la mosaïque) ; `focusWindowChanged` (couleur de la barre de titre seulement) ; gestionnaires de molette des widgets ; survols et glisser-déposer.

**Le correctif de la molette** (`_foreign_window_at` : processus de la fenêtre racine sous le curseur comparé à celui de l'application)
- **Cause** : le hook relaie la molette au combo quand la fenêtre de l'application est inactive ; le seul contrôle de recouvrement (`QApplication.widgetAt`) ne connaît que les fenêtres Qt de l'application, et une fenêtre d'un autre programme posée dessus lui échappait.
- **Verdict** : correction à la source pour les fenêtres des autres programmes, sans régression trouvée.
- **Reste ouvert** (**Confirmé par le code**, non reproduit, laissé en l'état par décision de l'utilisateur et noté dans son fichier d'idées) : une fenêtre native Windows ouverte par l'application elle-même (Ouvrir / Enregistrer sous de `QFileDialog`, impression) n'est ni une fenêtre Qt ni d'un autre processus ; posée sur le combo, elle n'arrête pas la molette. Correction possible : comparer la fenêtre racine sous le curseur à celle du combo.

**Constats**

| # | Certitude | Constat | Statut |
|---|---|---|---|
| **E-1** | Confirmé (code) | Défilement du hook arrondi par `delta // 120 * 120`, qui arrondit vers −∞ : un petit delta de pavé tactile ne faisait rien vers le haut et un cran entier vers le bas (combo des langues, fenêtre inactive). | **Corrigé** : deltas cumulés dans `WheelHook._wheel_accum` jusqu'à un cran entier, tronqués vers zéro (`int(... / 120)`). Manuel. |
| **E-2** | Confirmé (code) | Fenêtres natives Windows de l'application posées sur le combo (voir ci-dessus). | Non traité (décision de l'utilisateur). |
| **E-3** | Hypothèse | Le hook voit passer toutes les molettes du système et a besoin du verrou de l'interpréteur Python : une application très occupée pourrait ralentir la molette dans les autres programmes, ou Windows retirer le hook (`LowLevelHooksTimeout`). Aucun symptôme observé. | Non traité. |

**Sans problème trouvé** : raccourcis clavier (actifs seulement dans la fenêtre principale ; après correction du conflit de la barre de menus, voir ci-dessous), changement de panneau actif, navigation Tab, tooltips et survols (Windows n'envoie pas de survol à une fenêtre recouverte), glisser-déposer.

**Constat lié, corrigé pendant les tests de P4-3** : `QAction::event: Ambiguous shortcut overload: Ctrl+Z` dans la console. Les entrées de la barre de menus portaient un raccourci actif (Ctrl+Z, Ctrl+Y, Ctrl+C…) en concurrence avec le raccourci global de la fenêtre dès que le menu avait été ouvert une fois ; Qt n'en déclenchait alors aucun (Annuler sans effet, historique intact — confirmé par des prints). Correction : `menubar_qt._add_action` pose `Qt.WidgetShortcut`, le raccourci n'est plus qu'affiché dans le menu. Manuel, validé.

**Tests** : aucun test automatique. Le hook dépend de vraies fenêtres Windows et d'événements système ; un test fiable n'est pas possible sans extraire la règle de décision en fonction pure. Vérifications manuelles : molette dans l'Explorateur posé sur le combo ; molette au pavé tactile sur le combo pendant qu'une autre fenêtre est active.

---

# Audit 4 — Asynchronisme, concurrence et opérations en arrière-plan (version 1.8.8)

- **Période** : octobre 2026 (audit, puis corrections intégrées à la version 1.8.8, pas encore publiée au moment des corrections).
- **Objet** : rechercher les bugs dus à des opérations qui ne s'exécutent pas au même moment — workers (`QThread`), threads Python, process PDF et sous-processus 7z/unrar, timers, fins différées (fenêtres non modales, `run_when_idle`), boucles d'événements imbriquées (`processEvents`, fenêtres natives) — : résultats obsolètes appliqués au mauvais document, annulations sans effet, accès à l'interface ou à l'état du document depuis un autre thread, écrivains concurrents d'un même fichier, blocages, workers non libérés. Distinct des audits 1 (interactions), 2 (opérations destructives) et 3 (machines à états), dont il reprend les constats encore ouverts quand ils relèvent de la concurrence.
- **Méthode** : lecture du code des 29 classes `QThread`, des 10 points de création de `threading.Thread`, du process PDF, des 26 appels `processEvents` et des fins différées ; lecture de ce fichier (audits 1 à 3) et des tests. Pour chaque résultat asynchrone : identification de la garde qui l'empêche de s'appliquer au mauvais contexte (génération du document, identité du worker, numéro de séquence, contenu). Distinction systématique entre « code théoriquement concurrent » et « séquence d'exécution concrète qui produit un défaut ». Aucune reproduction dans l'application pendant l'audit (Qt non lancé) ; corrections ensuite, testées par l'utilisateur dans l'application.
- **Code de référence** : MosaicView 1.8.8 (état du dépôt, qui contient les corrections des audits 1 à 3 — la demande d'audit parlait de 1.8.7).

## Executive summary

| Priorité | Constats | Certitude | Statut |
|---|---|---|---|
| **P0** — intégrité des données | 1 | Very likely | Corrigé |
| **P1** — race condition / intégrité du contexte | 6 | 3 Confirmed, 3 Possible | 6 corrigés (P1-1, P1-2, P1-3 validés dans l'application ; P1-4 non reproductible à la main ; P1-5, P1-6 non testés dans l'application) |
| **P2** — erreurs fonctionnelles asynchrones | 7 | 4 Confirmed, 1 Very likely, 2 Possible | 7 corrigés (P2-3 par la correction de P1-4) |
| **P3** — robustesse et ressources | 7 | 5 Confirmed, 2 Possible | 7 corrigés (P3-1 à P3-5 validés dans l'application ; P3-6 et P3-7 non testés dans l'application) |
| **P4** — maintenabilité | 4 | — | P4-3 corrigé ; P4-1, P4-2, P4-4 non traités (décision définitive) |
| **Constats ajoutés pendant les corrections** | 6 | Confirmed | 6 corrigés |

**Mécanismes asynchrones identifiés : une cinquantaine** — 29 classes `QThread` ; 10 points de création de `threading.Thread` (les 7 types de lots via `_start_batch_thread`, surveillance d'un fichier ouvert dans une application externe, vérification de mise à jour, préchargement des langues, recentrage de la fenêtre native d'enregistrement, ouverture de l'Explorateur, crochet de molette) ; un process PDF par chargement, fusion ou lot, et un sous-processus 7z ou unrar par membre d'archive lu ; 26 appels `processEvents` (écriture d'un enregistrement, lecture de macro, lots, GIF animé, fusion PDF) ; les boucles imbriquées des fenêtres natives `QFileDialog` ; la file `AppState.run_when_idle` ; environ 140 `QTimer.singleShot`.

**Principaux risques**
1. **Aucune exclusion entre traitements par lot de conversion** sur les mêmes fichiers (P0-1) : le seul chemin trouvé qui pouvait détruire une BD.
2. **Des workers écrivent encore dans l'état du document depuis leur thread** (redimensionnement, redressement automatique, P1-1) : Audit 3, P3-4 n'avait corrigé que la rotation.
3. **Des fins asynchrones sans identité d'opération** : vérification de mise à jour ComicVine (P1-2), chaîne de validation des enregistrements (P1-3), annulations qui se contentent de débrancher les slots (P1-4).
4. **PyMuPDF appelé dans le process principal depuis plusieurs threads** (P1-5), alors que les panneaux ont été isolés dans des process précisément pour l'éviter.

**Opérations les plus sensibles** : lots de conversion CBR/CB7 (source rouverte à chaque page), redimensionnement avec annulation, chargements qui remplacent le document (archive, PDF), « Appliquer », Bibliothèque (scan, aperçu).

**Thread de l'interface / thread de fond** : modification de ComicInfo.xml, `comic_metadata`, `_page_attrs_by_entry_id` et `state.modified` depuis un worker (P1-1) ; création de `QPixmap` hors du thread de l'interface (P3-1) ; attentes bloquantes sur le thread de l'interface (P2-4, P3-2).

**Solidité constatée** : `doc_generation` capturé par presque toutes les fins asynchrones ; verrou `running_operations` et `run_when_idle` (audits 1 et 3) ; identité du worker vérifiée par la rotation, la conversion de format, la jointure animée et le chargement d'images ; numéro de séquence de l'import de métadonnées en lot ; fermeture des fenêtres filles avec le document ; ordre d'arrêt à la sortie (lots, puis `QThread`, puis process). La visionneuse charge ses pages de façon synchrone : le scénario « l'image A arrive après la B » n'y existe pas.

### Causes communes

| Cause | Constats concernés |
|---|---|
| Aucune coordination entre écrivains d'un même fichier (contrôle par simple test d'existence ou de signature avant l'écriture) | P0-1, P1-6 (→ Audit 1, P0-6) |
| Un CBZ incomplet renommé sous le nom final, puis supprimé par ce nom | P0-1 |
| Worker qui modifie l'état du document au lieu de rendre son résultat au thread de l'interface | P1-1 (→ Audit 3, P3-4) |
| Fin asynchrone sans génération du document ni identité de l'opération | P1-2, P1-3, P1-4, P2-2 (→ Audit 3, P4-2) |
| Activité non inscrite comme opération du document | P2-1, P2-5 (→ Audit 3, P4-1) |
| Bibliothèque non thread-safe utilisée depuis plusieurs threads | P1-5, P3-1 |
| Attente synchrone sur le thread de l'interface | P2-4, P3-2 |

---

## P0 — Data integrity

### P0-1 · Deux lots de conversion sur les mêmes fichiers : le lot en retard remplace le CBZ du premier par un CBZ incomplet, puis le supprime, alors que la source est déjà supprimée

- **Certitude** : Very likely (mécanisme établi par le code ; correction validée par l'utilisateur dans l'application) · **Gravité** : haute — perte définitive avec « Supprimer définitivement », sinon seule copie dans la Corbeille (définitive sur une clé USB ou un partage réseau, Audit 2, P2-10) · **Statut** : Corrigé
- **Code** : `batch_dialogs_qt.py` → `_run_cbr_conversion` et `_run_cb7_conversion` (`do_conversion`), `_interruptible_zip_writer`, `_copy_pages_to_cbz`, `_discard_incomplete_cbz` (supprimé par la correction), `_start_batch_thread` ; `batch_metadata_dialog_qt.py` → `_BatchMetadataOrchestrator._load_state_for_file`.
- **Constat** : deux défauts se combinaient.
  - Rien n'empêchait deux lots de traiter les mêmes fichiers : la fenêtre de progression est non modale, le même dossier pouvait être relancé depuis le menu, par un nouveau dépôt ou depuis l'autre panneau. `_files_in_running_batch` (Audit 3, P2-7) ne protégeait que l'import de métadonnées contre lui-même. Le nom du CBZ cible n'était choisi que par `os.path.exists`, qui ne voit pas un CBZ encore en cours d'écriture dans un temporaire.
  - Une page en échec ne levait pas d'exception : le bloc `with _interruptible_zip_writer` se terminait normalement, **renommait le CBZ incomplet sous le nom final** (`replace_file_retrying`), puis `_discard_incomplete_cbz` le supprimait par ce nom — même logique dans le lot PDF (`os.remove(cbz_path)` après le `with`).
- **Scénario de déclenchement** (état initial : `X.cbr` présent, `X.cbz` absent) :
  1. Le lot A commence `X.cbr` ; `X.cbz` n'existe pas, A vise `X.cbz` et écrit dans un temporaire.
  2. Le lot B, lancé sur le même dossier, arrive à `X.cbr` pendant que A l'écrit encore ; `X.cbz` n'existe toujours pas, B vise aussi `X.cbz`.
  3. A termine : `X.cbz` complet, relu sans erreur, `X.cbr` supprimé — possible parce que rarfile et 7z rouvrent l'archive à chaque page, sans la garder verrouillée.
  4. B lit la page suivante de `X.cbr` disparu : les pages suivantes échouent.
  5. Le bloc d'écriture de B se termine normalement : le CBZ incomplet de B **remplace** le `X.cbz` complet de A.
  6. B supprime `X.cbz` (CBZ incomplet).
- **Comportement constaté** : ni `X.cbr` ni `X.cbz`. Les lots CBT et PDF n'étaient pas exposés à l'étape 3 (`tarfile` et MuPDF gardent la source ouverte, sa suppression échoue sous Windows). Variante : l'import de métadonnées en lot choisissait sa cible `X (n).cbz` au chargement, plusieurs minutes avant l'écriture ; un lot de conversion pouvait créer ce nom entre-temps, et `replace_file_retrying` l'écrasait.
- **Attendu** : un second lot ne traite jamais les fichiers d'un lot en cours ; un CBZ incomplet ne prend jamais le nom final.
- **Correction appliquée** :
  - **CBZ incomplet jamais renommé** : l'incomplétude est constatée **dans** le bloc `with _interruptible_zip_writer`, qui lève `_IncompleteCbz(failures)` ; le temporaire est supprimé par l'écrivain, aucun fichier ne porte le nom du CBZ. `_discard_incomplete_cbz` devient `_log_incomplete_cbz`, qui ne fait que consigner l'erreur. Lots CBR, CB7, CBT et PDF ; dans le lot PDF, la suppression du CBZ par son nom après une exception est retirée, et le process n'est remplacé qu'après un timeout ou une erreur du process (`process_failed`), comme avant.
  - **Registre commun des lots** : nouveau module sans Qt `batch_claims.py` (`BatchClaims`, verrou unique). `BatchClaims.start(fichiers)` inscrit un lot, ou rend `None` si l'un de ses fichiers appartient à un lot en cours (test et inscription sous le même verrou) ; `finish()` le termine ; `claim(chemin)` réserve le fichier traité ; `reserve(free_name_candidates(base, ext))` rend le premier nom qui n'existe ni sur le disque ni dans les réservations d'un autre lot, et le réserve.
  - **Refus du second lot** (choix de l'utilisateur, plutôt que le partage des fichiers entre les deux lots proposé d'abord, dont le résumé annonçait des « erreurs » pour les fichiers laissés à l'autre lot) : `_refuse_if_batch_running` avant chaque fenêtre de confirmation, `_start_batch_claims` au début de chaque `_run_*` (la confirmation étant non modale). Fenêtre « Traitement par lot déjà en cours » (`dialogs.batch_running.title`/`.message`, `MsgDialog` non modal). Les 7 traitements de `batch_dialogs_qt.py` sont couverts et se bloquent entre eux ; `_start_batch_thread(target, parent, claims)` termine le lot dans son `finally`, quelle que soit la fin du thread.
  - **Import de métadonnées en lot** : refusé si un lot de conversion traite ses fichiers (et inversement), lot inscrit par `BatchClaims.start` dans `_start_batch` et terminé dans `_show_summary` ; fichier courant réservé dans `_load_and_open`, CBZ cible réservé dans `_load_state_for_file` jusqu'au fichier suivant. Un fichier réservé par un autre lot est laissé de côté avec le message de journal `messages.errors.batch_file_in_other_batch` (filet de sécurité).
  - Trois nouvelles clés traduites dans toutes les langues, puis vérifiées (mode « par clés » du skill `verify-translation`) : quatre langues (estonien, indonésien, japonais, maltais) alignées sur le terme déjà employé pour « traitement par lot ».
- **Tests** : `tests/test_batch_claims.py` (nouveau, 12 tests : fichier refusé à un autre lot, nom non attribué deux fois même s'il n'existe pas encore sur le disque, second lot refusé, lot terminé qui ne bloque plus, deux lancements simultanés synchronisés par `threading.Barrier` dont un seul démarre) ; `tests/test_batch_zip_writer.py` (`test_incomplete_cbz_never_replaces_an_existing_file` : le scénario exact, un `X.cbz` complet n'est ni remplacé ni supprimé) ; `tests/test_batch_copy_pages.py` (`_log_incomplete_cbz` consigne sans toucher à aucun fichier). Suite passée de 589 à 602 tests. Test manuel validé par l'utilisateur : même dossier lancé depuis les deux panneaux (refus du second, conversion complète par le premier), relance après la fin du premier.
- **Interactions** : lots × double panneau × import de métadonnées × Corbeille. Même famille que l'Audit 1, P0-6 (aucune coordination entre écrivains d'un même fichier), qui reste partiellement ouverte pour les écritures hors lots (P1-6).

---

## P1 — Race conditions / context integrity

### P1-1 · Redimensionnement et redressement automatique modifient ComicInfo.xml depuis leur thread ; annuler un redimensionnement laisse des `<Pages>` fausses

- **Certitude** : Confirmed (code) · **Statut** : Corrigé · **Lien** : Audit 3, P3-4 (corrigé pour la rotation seulement) ; règle « Workers asynchrones » de `CLAUDE.md`.
- **Code** : `resize_dialog_qt.py` → `_ResizeWorker.run` (`update_page_entries_in_xml_data(state, …, emit_signal=False)`), `_start_resize_worker._restore_after_cancel` ; `deskew.py` → `deskew_entry_data(entry, state)`, appelée par `deskew_qt._DeskewWorker.run` avec le `state` du panneau (`emit_signal=True` : émission de `metadata_pages_signal` depuis le worker) ; `comic_info.py` → `update_page_entries_in_xml_data`.
- **Thread** : le worker, au lieu du thread de l'interface. Il parcourt `images_data`, réécrit les bytes de l'entrée ComicInfo.xml, `comic_metadata['pages']` et `state._page_attrs_by_entry_id`, et pose `state.modified = True`.
- **Scénario A** (redimensionnement) : CBZ avec un ComicInfo.xml contenant `<Pages>`, non modifié → redimensionner 50 pages → Annuler à 50 %. **Constaté** : `_restore_after_cancel` remet les bytes des pages mais pas l'entrée ComicInfo.xml ; les 25 premières `<Page>` gardent les dimensions et tailles redimensionnées, et `state.modified` est remis à `False`, ce qui masque l'écart. Le prochain enregistrement du document écrit des `ImageWidth`/`ImageHeight`/`ImageSize` faux. **Attendu** : annuler rend aussi le ComicInfo.xml d'avant. (L'annulation du redressement passe par le retour au sommet de l'historique, qui restaure aussi ComicInfo.xml.)
- **Scénario B** (fermeture pendant le traitement, Possible) : le worker finit sa page en cours après la fermeture du document et réaffecte l'ancien `_page_attrs_by_entry_id` au `state` du panneau, et pose `state.modified = True` — sur le document suivant s'il a été ouvert et chargé dans cette fenêtre de temps.
- **Correction appliquée** : même modèle que la rotation. `_ResizeWorker.run` ne touche plus à ComicInfo.xml et range les pages réellement redimensionnées dans `resized` ; `_start_resize_worker.on_finished` met à jour leurs `<Pages>` en une fois, sur le thread de l'interface, avant le `save_state` de fin (une annulation ne passe plus par cette mise à jour, `<Pages>` reste celui d'avant). `_DeskewWorker.run` appelle `deskew_entry_data(entry, None)` et range les pages redressées dans `straightened` ; `_run_deskew.on_finished` met à jour leurs `<Pages>`. Le redressement depuis la visionneuse (thread de l'interface) garde son `state`.
- **Tests** : test manuel validé par l'utilisateur (redimensionnement annulé à mi-parcours sur un CBZ dont le ComicInfo.xml contient `<Pages>` : dimensions inchangées dans l'onglet Métadonnées ; redimensionnement et redressement complets : dimensions mises à jour). Toujours manquant : garde AST interdisant `update_page_entries_in_xml_data`, `state.modified` et `metadata_*signal.emit` dans le `run()` d'une sous-classe de `QThread` (P4-3).

### P1-2 · « Vérifier les mises à jour ComicVine » : la réponse d'un document est appliquée au document ouvert entre-temps

- **Certitude** : Confirmed (code) · **Statut** : Corrigé
- **Code** : `comicvine_update_check_qt.py` → `show_comicvine_update_check` (`_on_finished`), `_UpdateDiffDialog._on_update_clicked`. Appelants concernés : `PanelWidget._check_comicvine_updates` (onglet Métadonnées) et l'éditeur ComicInfo.xml (`comicinfo_dialog_qt.py`). Le chemin de la Bibliothèque (`LibraryWindow._action_check_updates_library`) a son propre `state` par fichier : sain.
- **Scénario** : comic A → « Vérifier les mises à jour » → la requête réseau part (1 à 10 s) → fermeture de A, ouverture de B → la réponse arrive.
- **Comportement constaté** : `_on_finished` compare les métadonnées de B à celles de l'épisode de A et affiche les différences ; « Tout mettre à jour » écrit les métadonnées de A dans le ComicInfo.xml de B (`run_when_idle` capture la génération de B, il n'y a donc aucune garde). Effets secondaires : depuis l'éditeur fermé, `on_done` repeuple un éditeur dont l'entrée appartient à A ; si le bouton déclencheur a été détruit, `_restore_busy_widget` lève `RuntimeError`. Le worker n'est jamais libéré (P3-5).
- **Attendu** : la réponse est ignorée si le document a changé.
- **Correction appliquée** : `show_comicvine_update_check` capture `state.doc_generation` au lancement ; `_on_finished` et `_on_error` ignorent la réponse si le document a changé (pas de fenêtre de différences ni d'erreur pour un document fermé) ; la génération est transmise à `_UpdateDiffDialog` (`generation=`), dont `_on_update_clicked` se ferme sans rien écrire dans le même cas. `_restore_busy_widget` ignore un bouton détruit entre-temps. L'inscription par `register_cancel_on_close` n'a pas été ajoutée : la génération suffit, et la requête réseau ne peut pas être interrompue.
- **Tests** : test manuel validé par l'utilisateur (vérification lancée, fermeture du comic et ouverture d'un autre avant la réponse : aucune fenêtre). Test automatique manquant : la décision est dans des fermetures de fonctions Qt (voir « Missing concurrency tests »).

### P1-3 · La chaîne de validation d'un enregistrement reprend après la fermeture du document

- **Certitude** : Confirmed (code) · **Statut** : Corrigé
- **Code** : `file_operations_qt.py` → `_validate_filenames_qt` (branche « Oui » : `info_dlg.finished` → `on_done(True)`), `_run_validation_chain`, `_on_validated` de `save_as_cbz`, `apply_new_names` et `create_cbz_from_images` (aucun ne capture la génération du document).
- **Scénario** : un nom de page contient un caractère refusé (par exemple `..` dans `Fin...jpg`) → Appliquer → « Oui, corriger » → la fenêtre « Correction effectuée » s'affiche → sans la fermer, fermeture de l'onglet et « Fermer sans enregistrer ». `force_close_file` vide l'état, puis `_close_comic_child_dialogs` ferme cette fenêtre : `finished` est émis, la chaîne continue sur l'état vidé.
- **Comportement constaté** : `apply_new_names` et `save_as_cbz` lèvent `TypeError` (`os.path.splitext(None)`), sans écriture ; `create_cbz_from_images` ouvre la fenêtre « Créer un CBZ » pour un panneau vide et peut écrire un CBZ vide, puis poser `current_file` sur un panneau sans page.
- **Attendu** : fermer le document abandonne la chaîne.
- **Correction appliquée** : `_run_validation_chain(steps, on_all_passed, state=None)` capture `state.doc_generation` et s'arrête en silence avant chaque étape et avant `on_all_passed` si le document a été fermé ou remplacé ; `save_as_cbz`, `create_cbz_from_images` et `apply_new_names` lui passent leur `state`. La fenêtre « Correction effectuée » continue de valoir « continuer » à sa fermeture : c'est la garde de génération qui arrête la chaîne.
- **Tests** : test manuel validé par l'utilisateur (le scénario ci-dessus : rien ne se passe après « Fermer sans enregistrer »). Test automatique possible en extrayant `_run_validation_chain` par AST, non ajouté.
- **Remarque, relevée pendant le test (voir C-4)** : `_validate_filenames_qt` refusait tout nom contenant `..` à n'importe quelle position, y compris des noms légitimes.

### P1-4 · Annulations par simple débranchement des slots : un résultat déjà en file d'attente est appliqué quand même

- **Certitude** : Possible (Qt vérifie la connexion au moment où l'appel différé est posté, pas à sa livraison : un résultat émis juste avant l'annulation est livré à un récepteur encore vivant) · **Statut** : Corrigé (non reproductible à la main : course de quelques millisecondes)
- **Code** : slots sans contrôle d'identité — `archive_loader.py` → `ArchiveLoader._on_finished`, `_on_error`, `_on_cancelled` (méthodes du chargeur, qui reste vivant) ; `pdf_loading_qt.py` → `PdfLoader._on_finished` (appellerait `_cleanup_worker()` sur le **nouveau** worker : `wait()` sur le thread de l'interface jusqu'à la fin du nouveau chargement, puis `deleteLater`) ; `import_merge_qt.py` → `import_and_merge_archive._cancel` ; `pdf_loading_qt.py` → `import_and_merge_pdf._cancel` ; `comicvine_dialog_qt.py` → `_park_running_worker` (résultat de recherche périmé affiché après un résultat tiré du cache).
- **Scénario** (chargement d'archive) : le worker émet `load_finished` ; dans la même itération de la boucle d'événements, un clic sur Annuler (ou la fermeture de l'onglet) est traité d'abord → `_detach_worker` ; `_on_finished` est livré ensuite.
- **Comportement possible** : le fichier annulé est installé quand même (`current_file` remis, pages installées) ; si un autre chargement a commencé, ses pages et celles de l'ancien se mélangent.
- **Attendu** : résultat ignoré, comme le font déjà la rotation, la conversion de format, le chargement d'images et la jointure animée (`worker_ref[0] is worker`).
- **Correction appliquée** :
  - `ArchiveLoader._from_current_worker()` (`self.sender() is self._worker`), testé en tête des six slots du chargeur (`_on_progress`, `_on_finished`, `_on_error`, `_on_cancelled`, `_on_file_renamed`, `_on_need_ext_dialog`) ;
  - `PdfLoader._start_pdf_loading` connecte chaque signal par une fonction qui teste `self._worker is worker` ;
  - `import_and_merge_archive` et `import_and_merge_pdf` : `_cancel` pose `cancelled_flag`, testé en premier par les slots de fin (la référence du worker ne peut pas servir : `_cleanup`, connecté avant eux, la remet à `None`) ;
  - fenêtre ComicVine : les slots de `_do_search`, `_go_to_issues`, `_apply_metadata`, `_load_issue_image` et `_load_first_issue_image` vérifient que leur worker est encore le courant.
- **Constat lié, corrigé avec** : `PdfLoader._start_pdf_loading` traite les événements (`processEvents`) avant de brancher les signaux du worker ; un Annuler (ou une fermeture du fichier) traité à ce moment détachait le worker avant son démarrage, puis la suite levait une exception sur `self._worker` devenu `None` et le process PDF confié au worker n'était jamais arrêté (jusqu'à la sortie de l'application). La suite vérifie désormais que le worker est toujours le courant, et sinon tue elle-même son process.
- **Tests** : pas de test automatique (slots Qt) ; la course n'est pas reproductible à la main. Règle ajoutée à `CLAUDE.md` (« Workers asynchrones ») et aux skills `archive-image-loading`, `pdf-loading`, `comicvine-metadata-fetch`.

### P1-5 · PyMuPDF appelé depuis plusieurs threads du process principal

- **Certitude** : Possible (PyMuPDF n'est pas utilisable depuis plusieurs threads ; c'est la raison de l'isolement des panneaux dans un process PDF, voir `tests/test_check_no_shared_pdf_process.py`) · **Statut** : Corrigé (non testé dans l'application : les aperçus de la Bibliothèque ne s'affichent pas sur la machine de l'utilisateur ; garde automatique en place)
- **Code** : `library_db.py` → `_count_pages` (appelée par `_ScanWorker`) ; `library_window.py` → `_PreviewWorker.run` ; `batch_metadata_dialog_qt.py` → `_extract_non_cbz` (couverture PDF, dans `_LoadWorker`) ; `pdf_unlock_qt.py` → `_save_unlocked` (thread de l'interface).
- **Scénario** : scan de la Bibliothèque sur un dossier de PDF pendant que l'utilisateur sélectionne un PDF dans la liste (aperçu) ou qu'un import de métadonnées en lot charge un PDF : deux threads dans MuPDF en même temps.
- **Conséquence possible** : plantage natif de l'application, avec perte du travail non enregistré des deux panneaux.
- **Correction appliquée** : nouveau module sans Qt `pymupdf_lock.py` (`PYMUPDF_LOCK`, `threading.RLock`) ; les quatre accès ouvrent, lisent et ferment leur document dans un bloc `with PYMUPDF_LOCK:`. Un process par accès a été écarté : ces lectures sont courtes et fréquentes (une par fichier PDF au scan), le coût de démarrage d'un process par appel serait disproportionné. Ce verrou empêche deux appels simultanés, pas le plantage dû à un PDF malformé, qui reste le risque connu de ces quatre accès (skill `pdf-loading`).
- **Tests** : `tests/test_check_pymupdf_lock.py` (nouveau, 2 tests : tout `fitz.open` de `modules/qt/` est dans un bloc `with PYMUPDF_LOCK:` ou dans le process dédié ; le scan trouve bien les appels existants). Test dans l'application non réalisé (aperçus indisponibles sur la machine de l'utilisateur) : scan d'un dossier de PDF en parcourant les aperçus.

### P1-6 · Le contrôle « fichier modifié ailleurs » ne couvre pas deux écritures qui se chevauchent

- **Certitude** : Possible · **Statut** : Corrigé (non testé dans l'application, choix de l'utilisateur) · **Lien** : Audit 1, P0-6 (contrôle fait avant l'écriture seulement).
- **Code** : `file_operations_qt.py` → `_write_document_with_progress` (qui traite les événements pendant l'écriture) ; `batch_metadata_dialog_qt.py` → `_save_state_for_file` ; `library_window.py` → `_rewrite_comicinfo_in_cbz`.
- **Scénario** : un panneau enregistre `X.cbz` (plusieurs secondes) ; pendant ce temps, l'import de métadonnées en lot relit l'ancien `X.cbz` et le remplace après le panneau, qui a mémorisé `disk_signature` juste après sa propre écriture.
- **Comportement possible** : le fichier contient les anciennes pages et le nouveau ComicInfo.xml ; si l'utilisateur ferme le panneau (marqué enregistré), ses modifications sont perdues.
- **Correction appliquée** : le registre `batch_claims.py` de P0-1 est étendu aux écritures hors lots. `file_operations_qt._claim_files_for_write(parent, paths)` réserve les fichiers lus, remplacés ou créés le temps de l'écriture, et les libère dans un `finally` : `save_as_cbz` (destination et fichier ouvert), `create_cbz_from_images`, les trois branches de `_write_apply_new_names`, et dans la Bibliothèque `_rewrite_comicinfo_in_cbz` et `_convert_file_to_cbz` (source et CBZ cible). Un fichier détenu par un lot (l'import de métadonnées en lot réserve son fichier courant du chargement à l'enregistrement) est refusé avec une fenêtre d'erreur (nouvelle clé `messages.errors.file_in_batch`, traduite dans toutes les langues et vérifiée) ; un lot qui atteint un fichier pendant l'écriture d'un panneau le laisse de côté (`_claim_file`). Deux panneaux sur le même fichier restent couverts par le contrôle de signature (Audit 1, P0-6) : leurs écritures sont synchrones ou verrouillées, elles ne se chevauchent pas.
- **Tests** : couverts par `tests/test_batch_claims.py` pour le registre ; le refus dans les fenêtres d'enregistrement est en Qt : test dans l'application non réalisé (scénario proposé : import de métadonnées en lot sur un dossier, puis enregistrement depuis un panneau du fichier affiché dans la fenêtre ComicVine : refus avec message).

---

## P2 — Functional async errors

| # | Constat | Certitude | Code | Séquence → effet | Statut et correction recommandée |
|---|---|---|---|---|---|
| **P2-1** | Images en cours de chargement perdues, et texte « Chargement X % » figé, si une archive est ouverte avant leur fin | Confirmed | `PanelWidget._start_image_load.on_finished` | Panneau vide → dépôt d'images → Ctrl+O sur un CBZ avant la fin. Le chargement d'images n'est pas un « chargement remplaçant » (`_replacing_load_in_progress`), donc `ArchiveLoader.load` fait `doc_generation += 1` ; `on_finished` sort sans `_hide()`, sans libérer `_image_worker` ni le jeton `_IMAGE_LOAD_HINT` : images déposées ignorées sans message, texte figé (le lien Annuler l'efface). | **Corrigé** : `PanelWidget._start_replacing_load` arrête le chargement d'images en cours (`_stop_image_load` : texte masqué, jeton libéré) avant d'ouvrir l'archive ou le PDF, et remet ses fichiers en attente avec la génération du nouveau document : ils sont ajoutés au comic une fois chargé (ou ouverts si le chargement échoue). `on_finished` masque aussi son texte avant de sortir sur une génération changée. Manuel, validé. |
| **P2-2** | Aperçu de la Bibliothèque : la couverture du comic précédent s'affiche pour le comic sélectionné | Confirmed (code) | `LibraryWindow._cancel_preview`, `_on_preview_ready` | Le worker A émet `ready` → sélection de B → `wait()` → B démarre ; le `ready` de A est livré ensuite. Si le fichier de B est absent, la couverture de A remplace « aperçu indisponible » et y reste. | **Corrigé** : `_on_preview_ready` ignore un résultat dont l'émetteur (`self.sender()`) n'est plus `self._preview_worker`. Le test de l'utilisateur a révélé un défaut voisin, l'aperçu non vidé au changement de base (C-5). Manuel, validé avec C-5. |
| **P2-3** | Double fin de chargement | Confirmed (code), rare | `LoadWorker._resolve_filepath` → `load_archive` | Un échec du renommage d'extension émet `error` **puis** `cancelled` : `loading_finished` émis deux fois, `_on_loading_finished` exécuté deux fois (historique, fichiers récents, `_run_pending_loads`). | **Corrigé par P1-4**, sans code propre : `_on_error` libère le worker (`self._worker = None`), et le `cancelled` qui suit est ignoré par `_from_current_worker`, son émetteur n'étant plus le worker courant. |
| **P2-4** | Le scan de la Bibliothèque garde le verrou d'écriture SQLite jusqu'à sa fin | Very likely | `LibraryDB.scan` (un seul `commit()` final), connexions séparées, délai SQLite par défaut de 5 s | Pendant un scan long : « Marquer comme lu », réindexation après l'enregistrement d'un panneau (`_on_file_saved`), édition de ComicInfo.xml → thread de l'interface figé 5 s, puis `database is locked` (erreur affichée, ou réindexation perdue sans message). | **Corrigé** : écritures du scan validées au plus toutes les `_SCAN_COMMIT_INTERVAL` secondes (0,2 s, `_commit_if_due` après chaque fichier indexé ou retiré) plutôt qu'à chaque fichier (coût d'une validation par fichier sur un gros dossier) ou une seule fois à la fin. Le mode WAL a été écarté : il ne lève pas le verrou d'écriture entre deux écrivains. Test : `test_library_db.py` (`test_window_can_write_while_a_scan_is_running`, délai mis à 0, écriture d'une seconde connexion pendant le scan). Pas de test dans l'application : la situation demande un scan long et une écriture pendant celui-ci, que l'utilisateur n'a jamais rencontrée. |
| **P2-5** | La lecture d'une macro n'est pas une opération du document | Possible | `macro_engine._read_pages` (`processEvents`) | Un clic sur Rotation dans la mosaïque traité avant la première étape : le `save_state` « avant » de la rotation ne pousse rien, `reading_context_changed` ne voit rien ; le worker de rotation et la macro écrivent la même entrée, une des deux modifications est perdue. Lien : Audit 3, P4-1. | **Corrigé** : `run_macro_on_entries` inscrit la lecture comme opération du document (`begin_operation` avant la lecture, `end_operation` dans un `finally`). Les étapes de la macro passent par les `perform_*` de la visionneuse, qui ne testent pas `operation_running` ; l'annulation à la fermeture de la visionneuse (`rollback_macro_reading`) ne passe pas par Annuler. Le test de l'utilisateur a révélé une régression de cette correction, corrigée (C-6). Manuel, validé avec C-6. |
| **P2-6** | Un ajout en attente qui lève une exception casse `end_operation` | Possible | `AppState.end_operation` | La file est vidée avant l'exécution : les ajouts suivants sont perdus (une page numérisée par exemple). Dans le `finally` de `_write_document_with_progress`, une écriture réussie est présentée comme un échec (`current_file`, `disk_signature`, `modified` non mis à jour). | **Corrigé** : chaque ajout est exécuté dans un `try` (trace dans la console), les suivants sont toujours appliqués. Test : `test_operation_running.py` (`test_failed_addition_does_not_drop_the_next_ones`). |
| **P2-7** | Redimensionnement : une page en échec est ignorée sans message | Confirmed | `_ResizeWorker.run` (`except Exception: pass`) | Même défaut que l'Audit 3, P2-5, corrigé pour la rotation seulement : la page est comptée comme traitée sans l'être. | **Corrigé** : le worker range les pages sans données ou en exception dans `failures` ; `on_finished` les liste dans la fenêtre « Fichiers ignorés » de la rotation (clé existante `messages.warnings.files_ignored.not_transformed`, aucune nouvelle traduction). Échec difficile à provoquer : pas de test dans l'application. |

## P3 — Robustness / resources

| # | Constat | Certitude | Code | Statut |
|---|---|---|---|---|
| **P3-1** | `QPixmap` créé hors du thread de l'interface. Toléré par Qt sous Windows, mais même famille que le plantage de liaisons Python/Qt déjà rencontré (commentaire de `tabs_qt._PagesModelBuilder`). | Possible | `batch_dialogs_qt._pil_to_qpixmap` (7 appels depuis les threads de lot), `library_window._PreviewWorker.run` | **Corrigé** : les threads émettent une `QImage` (`.copy()`, détachée du tampon PIL), convertie en `QPixmap` par le slot sur le thread de l'interface (`_pil_to_qimage` et `_ProgressDialog.set_thumbnail` ; `_PreviewWorker.ready` et `_on_preview_ready`). Règle ajoutée à `CLAUDE.md`. Manuel, validé. |
| **P3-2** | Thread de l'interface bloqué (les deux panneaux figés) | Confirmed | `LibraryWindow._cancel_preview` (`wait()` à chaque changement de sélection) ; `PdfLoader.load._on_dpi_chosen` (`join` jusqu'à environ 5 s) ; « Appliquer » d'un `.cbz` et « Enregistrer la sélection » écrits de façon synchrone, sans progression ; `pdf_unlock_qt._save_unlocked` ; verrou SQLite (P2-4) | **Corrigé** : `_cancel_preview` débranche le worker et le confie à `park_qthread` sans l'attendre ; la fin de la pré-ouverture PDF est attendue par un `QTimer` (`_when_preopen_done`, abandon si `doc_generation` change) ; « Enregistrer la sélection » et les trois branches d'« Appliquer » passent par `_write_document_with_progress` (nouveau paramètre `entries`, enveloppe `_write_cbz_with_overlay`) : progression, verrou du document, abandon si le document change — `_write_cbz_entries`, devenue sans appelant, est supprimée (garde-fou `test_check_atomic_file_writes.py` adapté) ; la copie déverrouillée d'un PDF est écrite par un `QThread` (`_UnlockWorker`). Verrou SQLite : P2-4. Manuel, validé. |
| **P3-3** | Annulation sans effet sur la lecture | Confirmed | `LoadWorker.load_multiple_archives`, phase 2 : `_cancelled` n'y est pas testé ; toutes les archives sont lues après Annuler ou la fermeture du fichier, fichiers gardés ouverts | **Corrigé** : `_cancelled` testé avant chaque archive (phase 1) et chaque page de chaque format (phase 2), sortie sans signal comme `load_archive`. Manuel, validé. |
| **P3-4** | Un lot de conversion ne peut pas être annulé par l'utilisateur, seulement à la fermeture de l'application | Confirmed | `_ProgressDialog.closeEvent` refuse la fermeture ; aucun bouton | **Corrigé** : bouton Annuler (la croix fait de même) → `cancel_event` du lot, rangé par `_start_batch_thread` dans un `threading.local` ; `_raise_if_batch_stopped` (avant chaque écriture du CBZ et au début de chaque fichier, jamais entre l'écriture et la suppression de la source) lève `_BatchCancelled`, sous-classe de `_BatchInterrupted` : CBZ en cours jeté, source gardée, puis `conversion_done` et résumé des fichiers déjà traités. Libellé du bouton : clé existante `buttons.cancel`, aucune nouvelle traduction. Test : `test_batch_zip_writer.py` (`test_cancel_button_leaves_nothing_and_keeps_existing_file`). Manuel, validé. |
| **P3-5** | Workers et fenêtres jamais libérés | Confirmed | `show_comicvine_update_check` (`_running_workers` vidé dans le slot du `finished` personnalisé, sans `dispose_qthread`) ; `_ComicVineUrlDialog` (fenêtre gardée cachée comme enfant du panneau, worker jamais parqué) ; `tabs_qt._PagesModelBuilder` (le `done` périmé d'un ancien builder fait `wait()` puis `deleteLater` sur le builder courant) | **Corrigé** : `_cleanup` appelle `dispose_qthread` ; `_ComicVineUrlDialog` en `WA_DeleteOnClose`, worker libéré dans ses slots de fin (`_release_worker`) ou parqué à la fermeture ; `_on_pages_model_ready` ignore un émetteur qui n'est plus le builder courant, `_drop_pages_builder` commun à `refresh` et `update_pages`. Manuel, validé. |
| **P3-6** | À la sortie, un worker non interruptible qui dépasse 3 s (réseau avec délai de 10 s, WIA, aperçu PyMuPDF) est détruit en cours d'exécution | Possible | `utils.stop_running_qthreads` (délai global) | **Corrigé** : les workers encore actifs après le délai restent référencés (`_qthreads_left_running`) ; à la fin de `main()`, si `qthreads_still_running()`, le process se termine par `os._exit` (flux de sortie vidés) au lieu de la destruction des objets restants. Sortie normale inchangée. Règle ajoutée à `CLAUDE.md`. Situation difficile à provoquer : pas de test dans l'application. |
| **P3-7** | Un thread de surveillance par fichier non-image ouvert dans une application externe, qui interroge le disque jusqu'à 1 h, même après la fermeture du document | Confirmed (mineur) | `open_with_default_app_qt._start_watch_thread` | **Corrigé** : arrêt au changement de `doc_generation` et à la réouverture du même fichier temporaire (`_watch_stops`, un `threading.Event` par fichier). Pas de test dans l'application. |

## P4 — Maintainability

Décision de l'utilisateur : seul P4-3 est corrigé (facile, sans risque pour l'application) ; P4-1, P4-2 et P4-4 ne seront pas traités, ni maintenant ni plus tard — réécrire du code qui fonctionne, dans des zones où une erreur est difficile à détecter (plantages différés, suppression des sources des lots), pour aucun gain visible.

- **P4-1 · Cinq conventions contre les résultats périmés** (**Non traité, définitivement**) : `doc_generation`, identité du worker, numéro de séquence, contenu (`content_snapshot`/`history_top`), simple débranchement des slots ; aucune n'est imposée. Cause de P1-2, P1-3, P1-4, P2-2. Prolonge l'Audit 3, P4-2 (contexte capturé avec `still_valid()`).
- **P4-2 · Six manières de garder un worker en vie** (**Non traité, définitivement**) : `dispose_qthread`, `park_qthread`, `_orphan_workers`, `_dying_workers`, `_active_workers`, `_running_workers`. Dix workers redéfinissent `finished` et masquent le signal natif de `QThread` ; le redimensionnement doit s'en protéger par trois drapeaux (`settled`, `cleaned`, `cancel_requested`).
- **P4-3 · La règle « un worker ne touche pas l'état du document » n'est contrôlée par aucun test** (**Corrigé**) : cause de P1-1. Garde-fou `tests/test_check_worker_document_state.py` (nouveau, 4 tests) : scan AST du code exécuté par le `run()` de chaque `QThread` de `modules/qt/` (ses méthodes et les fonctions du module qu'il appelle, de proche en proche) ; échoue sur toute écriture ou modification d'un attribut d'état du document d'un autre objet que le worker (`images_data`, `comic_metadata`, `modified`, historique, sélection…), tout appel à une fonction qui modifie le document (`update_page_entries_in_xml_data`, `save_state`, `run_when_idle`…) et toute émission de `metadata_signal`/`metadata_pages_signal`. Les entrées traitées (`entry["bytes"]`) et les attributs du worker restent permis. Aucune violation dans les 30 workers actuels ; un auto-contrôle vérifie la détection sur un worker fictif fautif (dont le schéma de P1-1). Liste d'exceptions justifiées `ALLOWED`, vide. Référencé dans `CLAUDE.md`.
- **P4-4 · Sept boucles de lot presque identiques** (**Partiellement traité ; le reste ne sera pas traité**) : la correction de P0-1 a dû être recopiée dans chacune ; `batch_claims.py` met en commun la réservation des fichiers et des noms, mais la boucle reste dupliquée (choix assumé, skill `batch-processing`, « Pourquoi pas de factorisation »).

---

## Constats ajoutés pendant les corrections

| # | Priorité | Constat | Correction | Tests |
|---|---|---|---|---|
| **C-1** | P2 | En corrigeant P0-1 : l'import de métadonnées en lot dont aucun fichier n'ouvrait la fenêtre ComicVine (tous ignorés par « ignorer ceux qui ont déjà un ComicInfo.xml », tous illisibles, ou réservés par un autre lot) ne se terminait jamais : aucun résumé, et l'import restait inscrit, bloquant tout nouvel import sur ces fichiers. | `_load_and_open` en fin de liste appelle `_show_summary` quand aucune fenêtre n'est ouverte. | Manuel |
| **C-3** | P2 | En corrigeant P1-4 : `PdfLoader._start_pdf_loading` traite les événements avant de brancher les signaux du worker ; un Annuler ou une fermeture du fichier traités à ce moment détachaient le worker avant son démarrage, la suite levait une exception sur `self._worker` devenu `None`, et le process PDF confié au worker restait actif jusqu'à la sortie de l'application. | La suite vérifie que le worker est encore le courant, sinon tue son process et s'arrête (détail dans P1-4). | Manuel (course étroite) |
| **C-4** | P2 | Relevé pendant le test de P1-3 : `_validate_filenames_qt` signalait comme invalide (« sortie du dossier ») tout nom contenant `..` à n'importe quelle position — `Fin...jpg`, `Chapitre 1..2.jpg` — et « Oui, corriger » y retirait les doubles points. Seul un segment de chemin égal à `..` fait sortir un fichier du dossier d'extraction. | Contrôle et correction limités au segment entier (`_has_parent_dir_segment`, `_without_parent_dir_segments`, fonctions pures) ; un nom réduit à rien par la correction devient `fichier` + extension, comme avant. | `test_parent_dir_segment.py` (nouveau, 3 tests) ; manuel, validé (`Fin...jpg` enregistré sans signalement) |
| **C-5** | P2 | Test de P2-2 par l'utilisateur : aperçu d'un comic affiché dans la Bibliothèque, puis ouverture d'une autre base par « Bases de données récentes » → l'aperçu du comic de l'ancienne base restait affiché. `_action_open_db` remplace la base ouverte sans passer par `_action_close_db`, seul chemin qui vidait l'aperçu ; `_on_new_db_accepted` (création d'une base) avait le même défaut. Diagnostic confirmé par des prints (`db_ouverte=True apercu_affiche=True` à la seconde ouverture). | `LibraryWindow._clear_preview()` (couverture, chemin et taille, métadonnées, aperçu en cours arrêté), commun à `_on_selection_changed`, `_action_close_db`, `_action_open_db` et `_on_new_db_accepted`. | Manuel, validé |
| **C-6** | P2 (régression de P2-5) | Test de P2-5 par l'utilisateur : après une macro lancée depuis la colonne d'icônes, les icônes de rotation restaient grisées et sans effet jusqu'au changement de sélection. La colonne était rafraîchie pendant la lecture avec le verrou posé (`busy=True`), puis jamais après sa levée. Diagnostic confirmé par des prints (`active=False busy=False has_selected_images=True` après la lecture). | `ImageViewer._macro_run` appelle `callbacks["update_button_text"]` (`PanelWidget._refresh_toolbar_states`) au retour de `run_macro_on_entries`, avant toute autre branche, comme les autres opérations après `end_operation`. Régression jamais publiée : absente du CHANGELOG. | Manuel, validé |
| **C-2** | P4 | Le skill `batch-cb7-convert` affirmait que le lot CBR n'ouvre son archive qu'une fois ; rarfile la rouvre à chaque page lue, ce qui rendait P0-1 possible. | Skill corrigé ; la règle générale (réservations, CBZ incomplet jamais renommé) est inscrite dans `CLAUDE.md` et le skill `batch-processing`. | — |

---

## Async mechanisms audited with no issue found

*No issue found after code analysis* pour :

- **Rotation et miroir** (`image_transforms_qt._run_transform`) : identité du worker, retour arrière seulement après l'arrêt réel du worker (y compris « annulé pendant la dernière page »), `<Pages>` mis à jour sur le thread de l'interface.
- **Conversion de format** (`conversion_dialogs_qt.perform_conversion`) : pages insérées sur le thread de l'interface, identité du worker dans `on_entry_ready`/`on_finished`, verrou tenu jusqu'à la réponse.
- **Jointure animée** (`merge_dialog_qt._start_animated_join`) : données d'entrée copiées, annulation par identité, génération vérifiée.
- **Numérisation, import web, fusions d'archive et de PDF** : génération du document et `run_when_idle` ; un seul scan à la fois entre les deux panneaux.
- **Import de métadonnées en lot** : numéro de séquence des chargements, annulation coopérative de l'écriture, relecture de la source au moment d'enregistrer.
- **Écriture d'un enregistrement** (`_write_document_with_progress`) : verrou d'opération, `_DocumentChangedDuringSave`, ajout pendant l'écriture non marqué enregistré.
- **Visionneuse** : pages chargées de façon synchrone (le scénario « l'image A remplace la B » est impossible) ; timer des GIF arrêté au changement de page et à la fermeture.
- **Vérification de mise à jour de l'application, préchargement des langues** : résultats renvoyés au thread de l'interface par signal.
- **Sortie de l'application** : confirmation si un lot tourne, puis arrêt des lots, des `QThread` et des process, dans cet ordre.
- **Panneau 2 masqué** : `_stop_panel2_activities` (Audit 3, P1-6).

## Fausses pistes écartées

- **Deux lecteurs sur le canal du process PDF** (`PdfLoader.load`, thread `_drain_preopen` et worker) : le thread de vidage s'arrête au plus 5 s après sa dernière attente, et `join(timeout=10)` attend sa fin avant de démarrer le worker. Seul reste un blocage du thread de l'interface (P3-2).
- **Destruction d'un worker dans le slot du `finished` natif de `QThread`** (`_BatchMetadataOrchestrator._retire_save_worker`, `ScanController._cleanup`) : le destructeur de `QThread` attend la fin d'un thread qui est en train de terminer ; seul le signal personnalisé émis à la dernière ligne de `run()` est dangereux (règle de `CLAUDE.md`).
- **Signal de métadonnées émis depuis le worker de redressement** : ses récepteurs sont des méthodes de `MetadataTab`, le signal leur est livré sur le thread de l'interface ; le défaut réel est l'écriture de l'état (P1-1).
- **Annuler pendant une lecture de macro** : détecté par `reading_context_changed` (Audit 3).

## Shared state and synchronization map

| Donnée | Écrivains (thread) | Protection | Suffisante ? |
|---|---|---|---|
| Bytes et image des pages traitées | workers d'opération ; macro (interface) | `running_operations` à l'entrée des commandes | Non pendant une macro (P2-5) |
| `images_data` (liste) | interface seulement, ajouts par `run_when_idle` | verrou + génération | Oui |
| Entrée ComicInfo.xml, `comic_metadata`, `_page_attrs_by_entry_id`, `modified` | interface ; workers de redimensionnement et de redressement (avant correction) | thread de l'interface seulement, depuis P1-1 | Oui depuis P1-1 |
| Historique | interface | verrou (Annuler/Refaire bloqués) | Oui, sauf macro |
| Fichiers sur disque | panneaux, lots, import de métadonnées, Bibliothèque | `batch_claims` (lots, depuis P0-1 ; panneaux et Bibliothèque pendant leurs écritures, depuis P1-6) ; signature lue avant l'écriture (entre panneaux) | Oui |
| Base `.mvdb` | interface et `_ScanWorker` (connexions séparées) | délai SQLite de 5 s ; validations du scan au plus toutes les 0,2 s depuis P2-4 | Oui depuis P2-4 |
| Contexte MuPDF du process principal | scan, aperçu, import de métadonnées, déverrouillage | `PYMUPDF_LOCK`, depuis P1-5 | Oui |
| Listes de workers au niveau du module | interface | — | Oui |

## Stale callback risks

| Résultat asynchrone | Garde |
|---|---|
| Rotation, redressement, redimensionnement, conversion, jointure, chargement d'images, numérisation, import web, fusions | génération (+ identité du worker) — OK |
| Chargement d'archive ou de PDF, fusion d'archive ou de PDF, recherche ComicVine | débranchement seul avant P1-4 ; identité du worker ou drapeau d'annulation depuis — OK |
| Vérification de mise à jour ComicVine | aucune avant P1-2 ; génération du document depuis — OK |
| Chaîne de validation d'un enregistrement | aucune avant P1-3 ; génération du document depuis — OK |
| Aperçu de la Bibliothèque, tableau des pages de l'onglet Métadonnées | aucune avant P2-2 et P3-5 ; émetteur (`self.sender()`) comparé au worker courant depuis — OK |
| Surveillance d'un fichier ouvert dans une application externe | appartenance de la page au document ; arrêt au changement de `doc_generation` depuis P3-7 — OK |
| Import de métadonnées en lot | numéro de séquence — OK |
| Lots de conversion | `batch_claims` — OK depuis P0-1 |

## Missing concurrency tests

**Ajoutés** (suite passée de 589 à 602 tests avec P0-1, à 604 avec P1-5, à 607 avec C-4, à 609 avec les P2, à 610 avec P3-4, puis à 614 avec P4-3) :

| Fichier | Couvre |
|---|---|
| `test_batch_claims.py` (nouveau) | P0-1 : réservations, refus du second lot, deux lancements simultanés (`threading.Barrier`) ; registre utilisé aussi par P1-6 |
| `test_check_worker_document_state.py` (nouveau) | P4-3 (cause de P1-1) : aucun worker ne modifie l'état du document depuis son thread (garde AST, auto-contrôle sur un worker fictif fautif) |
| `test_check_pymupdf_lock.py` (nouveau) | P1-5 : tout appel PyMuPDF du process de l'application sous `PYMUPDF_LOCK` (garde AST) |
| `test_parent_dir_segment.py` (nouveau) | C-4 : seul un segment `..` est refusé, les points dans un nom sont acceptés |
| `test_library_db.py` | P2-4 : écriture d'une seconde connexion pendant un scan (course rendue déterministe par un délai de validation nul et une écriture dans le rappel de progression) |
| `test_operation_running.py` | P2-6 : un ajout en attente en échec n'empêche pas les suivants |
| `test_batch_zip_writer.py` | P0-1 : CBZ incomplet qui ne remplace ni ne supprime un fichier existant ; P3-4 : annulation par le bouton, CBZ en cours jeté sans toucher au fichier de même nom |
| `test_check_atomic_file_writes.py` | P3-2 : adapté à la suppression de `_write_cbz_entries` (`_write_zip_with_progress`, seul écrivain ZIP de `file_operations_qt.py`) |
| `test_batch_copy_pages.py` | P0-1 : `_log_incomplete_cbz` ne touche à aucun fichier |

**Toujours manquants** — les tests existants vérifient des séquences jouées dans l'ordre ; aucun ne reproduit une course entre threads en dehors de `test_batch_claims.py`. À rendre déterministes (événements, barrières, appels déclenchés à la main) plutôt que fondés sur `sleep()` :

- P1-2, P1-3, P1-4, P2-2 : extraire la décision « résultat encore valide ? » (génération et identité capturées) en fonction pure, testée avec des appels dans un ordre inattendu (A après B, après fermeture, après annulation, double appel) ;
- P2-1 : une fonction pure « chargements en cours » couvrant aussi le chargement d'images ;
- P2-4 : deux connexions SQLite sur une base temporaire, la première gardant une transaction ouverte, la seconde devant pouvoir écrire ;
- P2-6 : `end_operation` avec un ajout qui lève une exception, suivi d'un second qui doit s'exécuter ;
- P0-1, lot entier : une boucle de lot extraite, avec une source supprimée entre deux pages (le registre et l'écrivain sont testés, pas la boucle elle-même, imbriquée dans `_run_*`).

## Recommended fixes

P0-1 a été corrigé en premier, à la demande de l'utilisateur, puis les six P1 d'une traite (avec C-3), à sa demande. Suite complète passée (604 tests ; 607 avec C-4). P1-1, P1-2 et P1-3 validés dans l'application ; P1-4 n'est pas reproductible à la main ; P1-5 et P1-6 n'ont pas été testés dans l'application (aperçus indisponibles, choix de l'utilisateur). Restent :

1. **P2** — tous corrigés, d'une traite, à la demande de l'utilisateur (P2-3 par la correction de P1-4) ; suite complète passée (609 tests). P2-1, P2-2 et P2-5 validés dans l'application, avec les constats C-5 et C-6 révélés par ces tests ; P2-4 couvert par un test automatique ; P2-6 et P2-7 non testés dans l'application (test automatique pour P2-6, échec difficile à provoquer pour P2-7).
2. **P3** — tous corrigés, d'une traite, à la demande de l'utilisateur ; suite complète passée (610 tests). P3-1 à P3-5 validés dans l'application ; P3-6 et P3-7 non testés dans l'application (situations difficiles à provoquer).
3. **P4** — P4-3 corrigé (garde-fou `test_check_worker_document_state.py`, suite à 614 tests) ; P4-1, P4-2 et P4-4 ne seront pas traités (décision de l'utilisateur).

---

# Audit 5 — Préservation des formats et des données lors des conversions (version 1.8.8)

- **Période** : octobre 2026 (audit, puis premières corrections intégrées à la version 1.8.8, pas encore publiée au moment des corrections).
- **Objet** : vérifier que les opérations de lecture, conversion, transformation, enregistrement et réencodage préservent ce qui doit l'être — pas seulement que le fichier produit se rouvre : transparence, animation, métadonnées (EXIF, XMP, ComicInfo.xml), profil ICC, profondeur de couleur, qualité, DPI, dimensions, ordre et noms des pages, contenu des pages PDF. Distinguer la perte inhérente à un format cible (alpha d'un JPEG, animation d'un JPEG, passage d'un PDF au matriciel) de la perte causée par MosaicView. Distinct des audits 1 (interactions), 2 (opérations destructives), 3 (machines à états) et 4 (asynchronisme).
- **Méthode** : lecture du code réel de tous les encodeurs et décodeurs (`entries.py`, `image_ops.py`, `image_processing_qt.py`, `file_operations_qt.py`, `batch_dialogs_qt.py`, `pdf_loading_qt.py`, `split_dialog_qt.py`, `merge_dialog_qt.py`, `animated_gif_dialog_qt.py`, outils de dessin de la visionneuse, `comic_info.py`, chargeurs d'archives) ; suivi de chaque propriété depuis le décodage jusqu'à l'encodage final ; reproductions sans Qt avec Pillow 12.3.0, tifffile 2025.10.4, PyMuPDF 1.28.2 et `zipfile` (scripts hors du dépôt, fonctions extraites par AST quand le module importe Qt). Aucune modification de code avant présentation du rapport initial ; corrections ensuite, validées par l'utilisateur dans l'application avec des fichiers de test fournis (CBZ avec ComicInfo.xml piégé, TIFF palette/CMJN/RGBA/1 bit/plusieurs pages, PDF avec lettrage vectoriel, page tournée, image transparente).
- **Code de référence** : MosaicView 1.8.8 (état du dépôt, qui contient les corrections des audits 1 à 4 — la demande d'audit parlait de 1.8.7).

## Executive summary

| Priorité | Constats | Certitude | Statut |
|---|---|---|---|
| **P0** — perte ou corruption de données | 3 | 3 Confirmed (reproduits) | 3 corrigés, validés dans l'application |
| **P1** — altération significative non intentionnelle | 9 | 9 Confirmed (6 reproduits) | 9 corrigés, validés dans l'application (P1-2 par tests automatiques seulement) |
| **P2** — erreurs fonctionnelles de conversion | 13 | 10 Confirmed, 2 Very likely, 1 Possible | 11 corrigés et validés dans l'application ; P2-3 corrigé avec P1-2 ; P2-13 accepté (choix de l'utilisateur) |
| **P3** — robustesse | 8 | 5 Confirmed, 3 Possible | 6 corrigés et validés dans l'application (P3-6 et P3-7 par tests automatiques seulement) ; P3-4 en partie corrigé (noms sans drapeau UTF-8), le reste accepté ; P3-8 accepté (choix de l'utilisateur) |
| **P4** — maintenabilité | 5 | — | P4-2, P4-3, P4-5 corrigés ; P4-4 réglé par P0-2 ; P4-1 reporté (session dédiée) |
| **Constats ajoutés pendant les corrections** | 14 | 13 Confirmed, 1 Very likely | C-1, C-3 à C-13 corrigés et validés ; C-2 (documentation) corrigé ; C-14 corrigé (tests automatiques seulement) |

**Formats réellement pris en charge** (d'après le code, `IMAGE_EXTS`) : images JPEG (et ses synonymes JFIF, PJPEG, PJP), PNG et APNG, WebP fixe et animé, GIF fixe et animé, BMP, TIFF (une ou plusieurs pages), AVIF fixe et animé, ICO ; archives CBZ, CBR, CB7, CBT, EPUB (images seulement) ; documents PDF. **MosaicView n'écrit que du CBZ** : aucun chemin CBZ → CB7 ou CBZ → CBR, les allers-retours correspondants sont sans objet. Le JPEG multi-images (MPO, photos de téléphone) n'est pas pris en charge (P2-7).

**Propriétés les plus fragiles** : profil ICC, EXIF (orientation comprise), transparence hors PNG/WebP, 16 bits, contenu des pages PDF, ComicInfo.xml.

**Conversions présentant les principaux risques** : PDF → CBZ en mode « original » (mode par défaut de l'ouverture, seul mode du lot) ; ouverture d'un TIFF isolé ; outil Compression sur JPEG ; édition d'un GIF animé ; jointure de pages transparentes ; réécriture de ComicInfo.xml.

**Solidité constatée** : l'enregistrement d'un CBZ **recopie les octets d'origine sans réencodage** (seule exception : l'écriture du DPI — en-tête JFIF réécrit sans réencodage pour un JPEG, réencodage sans perte pour PNG et TIFF) ; commentaire d'archive conservé ; noms de membres uniques ; tri naturel (1, 2, 10) commun aux chargeurs et aux lots ; rotations et miroirs à 90° sans perte pour les formats sans perte ; animations et TIFF de plusieurs pages traités image par image ; TIFF de plusieurs pages d'une archive découpés en gardant compression, DPI et ICC ; JPEG réencodé avec son sous-échantillonnage et son ICC (contrôlé par mode) ; WebP sans perte qui reste sans perte ; CMJN converti par son profil ICC ; valeurs 32 bits remises à l'échelle au lieu d'être écrêtées.

### Causes communes

| Cause | Constats concernés |
|---|---|
| **Huit encodeurs aux règles divergentes** (ICC, DPI, EXIF, qualité, alpha) : `save_image_to_bytes`, `convert_image_data`, `split._encode_part`, jointure (`_finish_join`), `_normalized_page_bytes`, `create_entries_from_tiff`, fenêtre GIF animé, `transform_*` | P0-2, P1-2, P1-5, P1-6, P1-9, P2-1, P2-3 (→ P4-1 ; même famille que l'Audit 2, P4-1, six lecteurs d'archives aux règles divergentes) |
| **Information perdue dès le décodage** : EXIF jamais lu, `img.info` (ICC, compression TIFF) perdu par toute recomposition, `tifffile.asarray()` qui rend des données brutes sans leur sens (palette, CMJN, 16 bits) | P0-2, P1-2, P1-3, P1-7, P3-1 |
| **Contenu d'une page supposé réduit à son image intégrée** (PDF) | P0-1, P1-4, P3-7 (même famille que l'Audit 1, P0-2 : page PDF perdue en silence avant suppression de la source) |
| **Valeurs écrites sans échappement ni contrôle** dans un format texte | P0-3 |
| **Option de l'utilisateur remplacée par une valeur déduite** (qualité JPEG estimée, boucle, durées) | P1-1, P1-6, P2-4 |
| **Valeurs par défaut de Pillow prises pour des données** (`dpi = (1, 1)` d'un TIFF sans résolution) | C-1 |

---

## P0 — Data loss / corruption

### P0-1 · PDF en mode « original » : seule l'image intégrée est gardée, le texte et les tracés de la page disparaissent ; le lot PDF → CBZ supprime ensuite le PDF

- **Certitude** : Confirmed (reproduit avec PyMuPDF 1.28.2) · **Gravité** : haute — perte définitive du lettrage dans le lot · **Statut** : Corrigé · **Lien** : Audit 1, P0-2 (page PDF perdue en silence)
- **Format source → cible** : PDF → pages de la mosaïque, CBZ (ouverture, fusion dans un comic ouvert, lot PDF → CBZ).
- **Code** : `pdf_loading_qt.py` → `_pdf_persistent_process` (branches `_convert`, `_convert_merge`, `batch_convert`) ; `batch_dialogs_qt.py` → `_run_pdf_conversion._convert_all`.
- **Propriété** : contenu de la page (texte, tracés vectoriels, annotations).
- **Chemin** : `page.get_images()` ; si `len(image_list) == 1 and (not has_text or max_dpi >= 300)`, la page était remplacée par `doc.extract_image(xref)["image"]`.
- **Point de perte** : la condition d'extraction. Une page portant du **texte visible** par-dessus une image d'au moins 300 dpi (lettrage vectoriel des BD numériques) donnait l'image seule ; les tracés vectoriels (cases, bordures) et les annotations n'étaient même pas examinés (`has_text` ne regarde que le texte). Le seuil de 300 dpi supposait une couche OCR invisible, sans pouvoir la distinguer d'un texte visible.
- **Reproduction** : page de 595×842 pt, image de 2480×3508, texte « BULLE DE TEXTE VISIBLE » → `has_text=True`, `max_dpi=300`, `use_raw=True`, image extraite sans le texte.
- **Comportement constaté** : choix « original » coché par défaut dans `DpiDialog` ; le lot n'a pas d'autre mode ; la relecture du CBZ (bon nombre de pages) réussit, puis le PDF est supprimé ou mis à la Corbeille. Lettrage perdu sans aucun signalement.
- **Attendu** : extraire l'image telle quelle seulement si elle est le seul contenu de la page, sinon rendre la page.
- **Correction appliquée** (option A choisie par l'utilisateur : tout texte, même invisible, fait rendre la page ; la détection fine des couches OCR invisibles, option C, a été écartée) : nouveau module sans Qt `pdf_page_images.py`. `raw_page_image(doc, page, image_list, xref)` rend les bytes de l'image seulement si : une seule image, dessinée une seule fois (`page.get_image_info`), droite (transformation sans rotation ni miroir) et couvrant la page (tolérance `_PAGE_COVER_TOLERANCE`) ; aucun texte (`get_text`) ; aucun tracé (`get_drawings`) ; aucune annotation ni champ de formulaire ; page sans `/Rotate` ; image sans `SMask`, `Mask`, `ImageMask` ni `Decode` ; format JPEG, PNG ou WebP. Sinon `None` et la page est rendue. Les trois branches de `_pdf_persistent_process` appellent cette fonction (la décision n'est plus écrite en trois exemplaires).
- **Tests** : `tests/test_pdf_page_images.py` (nouveau, 10 tests, PDF de synthèse construits avec PyMuPDF : page image seule extraite telle quelle ; texte, tracé, annotation, page tournée, image retournée, image avec marges, image avec masque → rendue). Manuel, validé : PDF à lettrage vectoriel ouvert et converti en lot (bulle et texte présents), PDF de scans toujours extrait sans perte.
- **Interactions** : lots × Corbeille × fusion PDF dans un comic ouvert.

### P0-2 · Ouvrir un TIFF isolé convertit chaque page en JPEG et détruit les modes palette, 16 bits, CMJN et alpha

- **Certitude** : Confirmed (reproduit avec tifffile 2025.10.4) · **Gravité** : haute — pages corrompues dans le CBZ produit, source gardée sur le disque · **Statut** : Corrigé · **Lien** : contredit la décision de l'Audit 2, C-3 (TIFF de plusieurs pages d'une archive gardés en TIFF, page par page)
- **Format source → cible** : TIFF ouvert seul (double-clic, dépôt, ajout) → pages de la mosaïque, CBZ.
- **Code** : `entries.py` → `create_entries_from_tiff` ; appelant : `panel_widget.py` → `_ImageLoadWorker.run` (branche `.tiff`/`.tif`).
- **Propriétés** : mode de couleur, alpha, profondeur, format, extension, nombre de pages.
- **Chemin** : `tifffile` en priorité : `page.asarray()` → `Image.fromarray()` → `convert('RGB')` si le mode n'est ni RGB ni L → JPEG qualité 100 → nom renommé en `.jpg` ; repli PIL identique (JPEG). Même pour un TIFF d'une seule page.
- **Point de perte** : le décodage par `tifffile.asarray()`, qui rend des données brutes sans leur sens, puis l'encodage JPEG.

| Source TIFF | Résultat reproduit |
|---|---|
| Palette (index 1 = rouge) | index lus comme niveaux de gris : page **quasi noire** (`L`, valeur 1) |
| Gris 16 bits (30000) | **page blanche** (255, 255, 255) |
| CMJN cyan pur | tableau à 4 canaux lu comme RGBA, K perdu : **rouge** (254, 0, 0) |
| RGBA transparent | alpha supprimé, couleurs cachées visibles |
| Group4 1 bit | tifffile échoue, repli PIL en JPEG |

  Les SubIFD (vignettes, pyramides) étaient en plus **ajoutés comme pages**.
- **Attendu** : la règle des TIFF d'archive — chaque page reste un TIFF, mode, compression, DPI et ICC conservés.
- **Correction appliquée** : `create_entries_from_tiff` réécrite. TIFF d'une seule page (ou que PIL n'ouvre pas) : une entrée avec les bytes d'origine et le nom du fichier. TIFF de plusieurs pages : une entrée par page, `<nom>_page_0001<ext>`, chaque page enregistrée par `_single_page_tiff_bytes` (fonction extraite de `split_multipage_tiff_pages`, désormais commune) ; une page illisible n'arrête plus la lecture des suivantes. Repli `_tifffile_page_bytes` pour une page que PIL ne sait pas décoder, limité aux données sans ambiguïté (gris 8/16 bits, RGB/RGBA 8 bits, réécrites en TIFF sans perte) ; toute autre page est signalée comme illisible. SubIFD ignorés. Règle étendue dans `CLAUDE.md` (« TIFF de plusieurs pages… même règle pour un fichier TIFF ouvert seul »).
- **Tests** : `tests/test_tiff_pages.py` (noms attendus en `.tif` ; nouveaux : TIFF d'une seule page repris octet pour octet, mode et pixels conservés page par page pour palette, CMJN, RGBA, 16 bits et 1 bit, préfixe `NEW-`). Manuel, validé avec des TIFF palette, CMJN, RGBA, 1 bit group4 et palette de trois pages.
- **Interactions** : « Créer un CBZ » à partir d'images isolées ; coller depuis le presse-papiers (même worker).

### P0-3 · ComicInfo.xml réécrit en XML invalide (attributs `<Page>` non échappés, namespace par défaut)

- **Certitude** : Confirmed (reproduit sur `_serialize_comic_xml` extraite par AST) · **Gravité** : haute — métadonnées illisibles par tous les lecteurs, puis effacées · **Statut** : Corrigé
- **Code** : `comic_info.py` → `_serialize_comic_xml`, appelée indirectement par 34 sites (`sync_pages_in_xml_data`, `update_page_entries_in_xml_data`, `update_page_count_in_xml_data`, `write_comic_metadata_from_scraper`, éditeur ComicInfo) — presque toute opération sur les pages réécrit le fichier.
- **Propriété** : métadonnées ComicInfo.xml.
- **Constat** : les valeurs d'attributs de `<Page>` et `<MosaicViewTrace>` étaient concaténées sans échappement : `Bookmark="Tom &amp; Jerry"` relu puis écrit `Bookmark="Tom & Jerry"` → document mal formé. Avec un `xmlns` par défaut, ElementTree nomme les balises `{uri}Title` : la sérialisation écrivait `<{http://…}Title>`, et la lecture (`findtext('Title')`) rendait toutes les métadonnées vides.
- **Conséquence** : ComicInfo.xml illisible pour tous les lecteurs ; au prochain import ComicVine, l'échec d'analyse fait repartir de `ET.Element("ComicInfo")` (`write_comic_metadata_from_scraper`) : toutes les métadonnées existantes remplacées.
- **Correction appliquée** : `_xml_attr` (échappement de `&`, `<`, `>`, `"`, sauts de ligne et tabulations en références de caractère) et `_attrs_xml` (ordre canonique conservé) pour tous les attributs ; `_xml_text` pour le texte ; nom de balise écrit sans namespace ; nouvelle fonction `_comic_fromstring` (parseur durci `_safe_fromstring`, puis namespace retiré des balises et attributs) utilisée par toutes les lectures de ComicInfo.xml de `comic_info.py` et par `comicinfo_dialog_qt._build_xml_bytes`. La balise `<ComicInfo xmlns=…>` reprise des bytes d'origine garde le namespace : les balises sans préfixe restent dans le même namespace. Les autres XML (conteneur EPUB lu par la Bibliothèque) gardent `_safe_fromstring`, qui conserve les namespaces.
- **Tests** : `tests/test_comic_info.py` (5 nouveaux : attributs avec `&`, `"`, `<` relus à l'identique, sauts de ligne dans un attribut, URL avec `&` dans `<MosaicViewTrace>`, namespace par défaut conservé sans balises préfixées, métadonnées d'un ComicInfo à namespace lues). Manuel, validé : marque-page `Tom & "Jerry"` après une rotation et un enregistrement, ComicInfo à namespace lu puis réenregistré.

---

## P1 — Significant unintended alteration

### P1-1 · Outil Compression sur JPEG : la qualité choisie n'est pas celle du fichier écrit

- **Certitude** : Confirmed (reproduit) · **Statut** : Corrigé
- **Code** : `image_processing_qt.py` → `apply_adjustments` (simulation JPEG) puis `apply_image_adjustments` → `entries.save_image_to_bytes`.
- **Chemin** : image encodée à la qualité choisie (q30) puis rouverte ; `save_image_to_bytes` la **réencode** à `detect_jpeg_quality(octets d'origine)` (≈ 95). Le raccourci `ENCODED_BYTES_KEY` n'existe que pour WebP et AVIF.
- **Reproduction** : original q95 de 316 Ko ; q30 directe : 24,9 Ko ; résultat MosaicView : **51,6 Ko** — deux pertes successives, fichier deux fois plus lourd que demandé.
- **Attendu** : reprendre les octets encodés à la qualité choisie, comme pour WebP et AVIF.
- **Correction appliquée** : pour une page JPEG (`_JPEG_EXTS`), les bytes de la simulation sont posés dans `img.info[ENCODED_BYTES_KEY]` (hors aperçu) et repris tels quels par `apply_image_adjustments`. Les deux encodages de la compression (natif WebP/AVIF, simulation JPEG) reportent profil ICC, DPI, EXIF, XMP et commentaire de l'image d'origine (`_kept_metadata`, sur le `img.info` relevé au début d'`apply_adjustments`). Mesuré après correction : 24,9 Ko pour 24,7 Ko en encodage direct.
- **Tests** : `tests/test_page_preservation.py` (`test_jpeg_compression_writes_the_chosen_quality`). Manuel, validé (poids nettement plus faible).

### P1-2 · Profil ICC perdu selon le chemin suivi

- **Certitude** : Confirmed (comportement de Pillow 12.3.0 reproduit) · **Statut** : Corrigé (corrige aussi P2-3)
- **Code** : `entries.save_image_to_bytes`, `image_ops.convert_image_data`, `split_dialog_qt` → `_encode_part`, `merge_dialog_qt` → `_finish_join`.
- **Constat** : `save_image_to_bytes` ne reprend l'ICC des octets d'origine (`_source_encoding_info`) que pour JPEG et WebP ; pour PNG, TIFF et AVIF, il dépend de `img.info`, perdu par `ImageEnhance` (luminosité, contraste, saturation : `blend`) et par toute recomposition `Image.new` (reproduit : `brightness keeps icc: False`). `convert_image_data` ne passe jamais `icc_profile` : cibles JPEG, WebP et GIF sans profil (PNG, TIFF et AVIF le gardent par le repli de Pillow sur `info`). Découpe (JPEG, WebP) et jointure : toujours perdu.
- **Conséquence** : une page Adobe RGB ou Display P3 change de couleurs après une simple luminosité (PNG) ou une conversion JPEG → WebP.
- **Attendu** : une règle ICC unique (profil des octets d'origine, filtré par `_icc_for_mode`) pour tous les formats et encodeurs.
- **Correction appliquée** : `entries.page_icc_profile(img, data, mode=None)` — profil de `img.info`, sinon des bytes d'origine, retenu seulement s'il correspond au mode écrit (`_ICC_SPACE_BY_MODE` complété : `PA`, gris 16 et 32 bits). Utilisée par `save_image_to_bytes` (PNG, TIFF, AVIF en plus de JPEG et WebP), `convert_image_data` (toutes les cibles qui stockent un profil), la découpe, la jointure (profil repris seulement s'il est le même pour toutes les pages) et la normalisation des pages des lots. Pour PNG, TIFF et AVIF, `icc_profile=` est toujours passé à `save()`, même `None` : sans lui, Pillow reprend celui de `img.info`, même s'il ne correspond plus au mode — ce qui corrige aussi P2-3.
- **Tests** : `tests/test_page_preservation.py` (`test_png_keeps_its_profile_after_brightness`, `test_rgb_profile_is_not_written_into_a_grayscale_png`, `test_conversion_keeps_icc_and_exif_without_orientation`). Pas de test manuel (le profil n'est pas visible dans l'application).

### P1-3 · EXIF (orientation comprise), XMP et commentaires supprimés à chaque réencodage ; l'orientation n'est jamais appliquée à l'affichage

- **Certitude** : Confirmed (aucune occurrence de `exif` dans `modules/` ; perte reproduite pour JPEG, PNG et WebP) · **Statut** : Corrigé
- **Code** : tous les encodeurs ; affichage par `mosaic_canvas._pil_to_qimage`, sans `exif_transpose`.
- **Scénario** : photo JPEG `Orientation=6`, affichée droite par les lecteurs qui respectent l'EXIF, couchée dans MosaicView → modification qui n'est pas une rotation (luminosité, recadrage, macro) → tag supprimé → page couchée dans tous les lecteurs.
- **Attendu** : appliquer l'orientation au décodage et réécrire l'EXIF avec `Orientation=1`, ou au minimum conserver l'EXIF (choix à faire avec l'utilisateur).
- **Correction appliquée** (choix de l'utilisateur : afficher droit comme les autres lecteurs, réenregistrer tourné sans orientation, garder le reste de l'EXIF) :
  - Décodage dans le sens d'affichage : `entries.oriented(img)` (`ImageOps.exif_transpose`, sauf images à plusieurs images ; un JPEG MPO est traité comme une photo), `open_page_image(data)`, `ensure_image_loaded` ; dimensions sans décodage `oriented_size`/`page_size`. Appliqué aux vignettes (`create_centered_thumbnail`), à `create_entry` et `get_image_metadata` (dimensions), à `apply_image_adjustments` et aux aperçus des outils de réglage, aux outils de dessin, à la transparence, au redimensionnement, à la renumérotation, à `<Pages>` de ComicInfo.xml, aux macros (taille de page), à l'impression et à l'écriture du DPI d'un PNG/TIFF à l'enregistrement (`_bytes_with_entry_dpi`).
  - Réécriture des métadonnées : `page_metadata`/`metadata_save_kwargs` — EXIF pour JPEG, WebP, AVIF et PNG, XMP pour JPEG, WebP et AVIF, commentaire JPEG ; orientation retirée de l'EXIF **et** du XMP (Pillow la relit dans le XMP), sauf `keep_orientation=True` pour la normalisation des pages des lots (pixels non tournés). Utilisée par `save_image_to_bytes`, la conversion, la découpe, la compression et les lots.
  - Une page jamais modifiée garde ses octets d'origine (orientation comprise), cohérente avec ce qu'affiche MosaicView.
  - Règle ajoutée à `CLAUDE.md` : toute page affichée, mesurée ou modifiée se décode par `ensure_image_loaded`/`open_page_image`, jamais par un `Image.open` direct.
- **Tests** : `tests/test_page_preservation.py` (`test_page_is_shown_upright`, `test_edited_page_stays_upright_and_keeps_its_exif`, `test_conversion_keeps_icc_and_exif_without_orientation`). Manuel, validé (photo affichée droite, toujours droite après luminosité et enregistrement en CBZ).
- **Lien** : la correction a révélé C-4 (EXIF d'un TIFF).

### P1-4 · PDF « original » : rotation de la page ignorée et masque de transparence (SMask) perdu

- **Certitude** : Confirmed (reproduit) · **Statut** : Corrigé (avec P0-1, même mécanisme)
- **Constat** : une page `/Rotate 90` était extraite non tournée (paysage affiché, portrait produit), et `max_dpi` calculé sur le rectangle tourné (424 au lieu de 300). Une image avec `SMask` (`smask=17`) était extraite en RGB sans son masque : une zone transparente posée sur le fond blanc de la page devenait une zone de couleur pleine.
- **Correction appliquée** : `raw_page_image` refuse l'extraction pour une page tournée ou une image masquée (P0-1) ; `best_page_image` (`pdf_page_images.py`), commune aux trois branches, calcule la résolution sur `page.cropbox` (page non tournée).
- **Tests** : `tests/test_pdf_page_images.py` (`test_rotated_page_forces_rendering`, `test_image_with_transparency_mask_forces_rendering`, `test_resolution_ignores_page_rotation`). Manuel, validé (page tournée en paysage, image transparente sur fond blanc).

### P1-5 · Jointure fixe : zones transparentes qui montrent leurs couleurs cachées, pages 16 bits blanches, puis suppression des sources proposée

- **Certitude** : Confirmed (reproduit : collage RGBA sans masque → (255, 0, 0) visible ; `I;16` → blanc) · **Statut** : Corrigé
- **Code** : `image_ops.merge_images_2d`, `merge_images_horizontally`, `merge_images_vertically` (`Image.new('RGB')` + `paste` sans masque). La jointure animée aplatit correctement (`_flatten_on_white`).
- **Attendu** : même composition que la jointure animée, ou résultat RGBA si toutes les sources sont dans un format qui garde l'alpha.
- **Correction appliquée** (choix de l'utilisateur : garder la transparence quand le format de sortie la stocke, zones couvertes par aucune page en blanc) : `merge_images_2d`/`merge_images_horizontally`/`merge_images_vertically` reçoivent `keep_alpha`. `_merge_source` prépare chaque image (16 bits ramenés à 256 nuances par `to_display_mode` ; RGBA avec `keep_alpha`, sinon aplatie sur blanc si elle a de la transparence), `_merge_canvas` crée une planche blanche opaque (RGB ou RGBA), où chaque image est collée sans masque. `MergeDialog._finish_join` choisit le format de sortie **avant** la fusion et pose `keep_alpha` si ce format stocke la transparence (PNG, WebP, AVIF, TIFF, GIF) et qu'une source en a (`_has_alpha`) ; profil ICC repris s'il est commun à toutes les pages. Effet visible : les zones d'une ligne non couvertes par une image plus petite, noires dans `merge_images_vertically`/`merge_images_horizontally`, sont désormais blanches comme le reste de la planche.
- **Tests** : `tests/test_page_preservation.py` (`test_join_keeps_transparency_and_fills_uncovered_areas_in_white`, `test_flattened_join_never_shows_hidden_colors`, `test_16bit_page_joined_is_not_white`). Manuel, validé (damier autour des formes, aucune couleur cachée, blanc hors des pages).

### P1-6 · « Modifier le GIF animé » : durées, transparence, palette et boucle altérées

- **Certitude** : Confirmed (code) · **Statut** : Corrigé
- **Code** : `animated_gif_dialog_qt.py` (valeurs initiales de la fenêtre, création du GIF) ; `entries.create_entry` (`gif_loop`).
- **Constat** : une durée unique (`delay_ms` = **moyenne** des durées) remplace celle de chaque image ; toutes les images sont composées sur fond **blanc** RGB (transparence perdue) et quantifiées avec la **palette de la première** ; `gif_loop = info.get('loop', 0)` : un GIF joué une fois devient une boucle infinie. Le résultat est ajouté comme nouvelle entrée (original gardé) : altération involontaire, pas de perte.
- **Correction appliquée** (choix de l'utilisateur : délai propre à chaque image gardé tant que le réglage n'est pas modifié, sans réglage par image dans la fenêtre — idée notée pour plus tard) :
  - chaque image extraite porte sa durée (`gif_duration`), qui la suit si la liste est réordonnée ; si le réglage « Délai » n'a pas changé, ces durées sont écrites ;
  - transparence gardée : planche RGBA si une image en a, images collées sans masque, fond non couvert blanc ;
  - une palette par image (Pillow quantifie chaque image à l'enregistrement) ;
  - `gif_loop` vaut `None` pour un GIF sans bloc de répétition ; la fenêtre affiche alors 1 répétition et, tant que le réglage n'est pas modifié (`_loop_unset`), le GIF créé reste sans bloc de répétition ;
  - **constaté pendant la correction** : avec `optimize=True`, Pillow rend opaques (blanches) les zones transparentes des images qui suivent la première (reproduit) ; l'option est ignorée quand des images sont transparentes.
- **Tests** : `tests/test_page_preservation.py` (`test_gif_played_once_has_no_loop`) ; le reste dépend de la fenêtre Qt. Manuel, validé après la correction de C-3 (pause sur la 3e image, fond transparent, une seule lecture).

### P1-7 · Pages en gris 16 bits : affichées blanches, et les outils de dessin écrivent une page blanche

- **Certitude** : Confirmed (Pillow : `I;16` → RGBA = (255, 255, 255, 255)) · **Statut** : Corrigé
- **Code** : `mosaic_canvas._pil_to_qimage`, `entries.create_centered_thumbnail` ; outils texte, formes, collage d'image, clonage, flou (`base_img_raw.convert('RGBA')`).
- **Conséquence** : vignette et visionneuse blanches (l'utilisateur peut croire la page vide et la supprimer) ; ajouter un texte remplace les données par du blanc.
- **Correction appliquée** (choix de l'utilisateur : la page reste en gris 16 bits, seuls les pixels dessinés passent à 256 nuances, un dessin en couleur devient gris ; passer la page en couleur reste possible avec l'outil Mode d'image) :
  - affichage : `entries.to_display_mode` (256 nuances, `image_ops._to_writable_mode`) dans `create_centered_thumbnail`, `mosaic_canvas._pil_to_qimage`, `image_viewer_qt._pil_to_qpixmap`/`_compose_on_checkerboard`, la fenêtre GIF animé, la renumérotation et l'impression ;
  - réglages : `apply_adjustments` ramène une page `I;16*`, `I` ou `F` à 256 nuances avant tout réglage — « Mode d'image → RGB » et « Profondeur → 24 bits » la rendaient blanche aussi ;
  - outils de dessin (texte, formes, collage, clonage, flou) : image de travail `drawing_base_image(data)`, résultat passé par `drawn_page_image(data, drawn)`, qui garde la page en 16 bits et n'y écrit que les pixels changés (valeur grise × 257) ; image de travail de la transparence par `drawing_base_image`.
- **Tests** : `tests/test_page_preservation.py` (`test_16bit_page_is_not_shown_white`, `test_16bit_page_converted_to_color_is_not_white`, `test_drawing_keeps_16bit_precision_outside_the_drawing`, `test_drawing_on_an_8bit_page_is_returned_unchanged`). Manuel, validé (dégradé affiché, texte ajouté en gris, passage en couleur sans page blanche).

### P1-8 · Conversion vers TIFF : transparence de palette perdue

- **Certitude** : Confirmed (reproduit : P avec transparence → TIFF → alpha 255) · **Statut** : Corrigé
- **Code** : `image_ops.convert_image_data`, branche TIFF : image P transparente (GIF, PNG à palette) enregistrée en P sans transparence, alors que TIFF sait écrire du RGBA.
- **Correction appliquée** : une image `P` avec `transparency` passe en RGBA avant l'écriture TIFF, dans `convert_image_data` et dans la branche TIFF de `save_image_to_bytes`.
- **Tests** : `tests/test_page_preservation.py` (`test_transparent_palette_converted_to_tiff_stays_transparent`). Manuel, validé.

### P1-9 · Découpe : un WebP sans perte devient avec perte ; les morceaux JPEG perdent leur DPI et leur ICC

- **Certitude** : Confirmed (code) · **Statut** : Corrigé
- **Code** : `split_dialog_qt` → `_encode_part`, qui contourne `save_image_to_bytes` pour JPEG (q100, `subsampling=0`, sans `dpi` ni `icc_profile`) et WebP (`quality=100`, jamais `lossless`).
- **Correction appliquée** : `_encode_part` garde sa qualité maximale propre, et ajoute le DPI (`image_dpi`, sinon `entry["dpi"]`), le profil ICC (`page_icc_profile`) et les métadonnées (`metadata_save_kwargs`) ; un WebP sans perte (`_webp_is_lossless`) donne des morceaux sans perte. Les autres formats passaient déjà par `save_image_to_bytes`, qui applique désormais les mêmes règles.
- **Tests** : aucun automatique (fonction imbriquée dans la fenêtre Qt). Manuel, validé (morceaux WebP sans perte).

---

## P2 — Functional conversion errors

Constats d'origine ci-dessous ; corrections dans le tableau qui suit. P2-3 a été **corrigé par la correction de P1-2** (`icc_profile` toujours passé à `save()` pour PNG, TIFF et AVIF, filtré par `page_icc_profile` ; test `test_rgb_profile_is_not_written_into_a_grayscale_png`). Les onze autres P2 corrigés l'ont été d'une traite, après les choix de l'utilisateur, et validés dans l'application ; P2-13 est **accepté**.

| # | Constat | Certitude | Code |
|---|---|---|---|
| **P2-1** | Conversion vers PNG : DPI non écrit (la branche PNG ne le passe pas à `save()`, alors que Pillow sait l'écrire) ; DPI tronqué par `int()` (299,9994 → 299) | Confirmed | `convert_image_data` |
| **P2-2** | Conversion vers WebP jamais sans perte, même depuis PNG ou WebP sans perte au réglage « Maximum » ; aucune option | Confirmed | `convert_image_data`, `_QualityDialog` |
| **P2-3** | PNG et TIFF écrivent un profil ICC qui ne correspond pas au mode (profil RGB dans une image L après un changement de mode, reproduit), contrairement à la règle `_icc_for_mode` appliquée à JPEG et WebP | Confirmed | `save_image_to_bytes` |
| **P2-4** | Conversion animée : sans `loop` source (GIF joué une fois), WebP, APNG et AVIF bouclent à l'infini ; animé → JPEG, BMP, TIFF ou GIF fixe garde la première image sans avertir, puis « Supprimer les originaux » retire l'animation | Confirmed / Possible | `convert_image_data`, `show_conversion_complete_dialog` |
| **P2-5** | Conversion 16 bits → PNG ou TIFF réduite à 8 bits alors que ces cibles gardent les 16 bits (`_to_writable_mode` appliquée à toutes les cibles) | Confirmed | `convert_image_data` |
| **P2-6** | EPUB : pages triées par nom de fichier et non par ordre de lecture (spine) ; dossiers réduits au nom de base | Very likely | `ArchiveLoader._get_namelist`, `load_archive` |
| **P2-7** | JPEG MPO (photos de téléphone, Pillow : format `MPO`, `n_frames=2`) : refusé par le lot images → CBZ comme « multi-frame » ; renommé `.mpo` par l'import web, donc plus reconnu comme image | Confirmed | `_run_img_conversion`, `web_import_qt` |
| **P2-8** | Format de réencodage choisi d'après l'extension, jamais d'après le contenu : un PNG nommé `.jpg` devient JPEG (alpha aplati, avec perte) à la première modification. L'import web corrige l'extension, le chargement d'archive non | Possible | `save_image_to_bytes` |
| **P2-9** | Restaurer l'original (profondeur, mode, effets) ne met pas à jour `ImageSize` de `<Pages>` | Confirmed | `perform_restore_*` (`color_depth_tool_qt.py`, `image_mode_tool_qt.py`, `effects_tool_qt.py`) |
| **P2-10** | Export d'une page : la boîte native permet de changer l'extension, les octets sont écrits tels quels (contenu ≠ extension) ; l'export n'ajoute pas le DPI que l'enregistrement CBZ ajoute (pages PDF) | Confirmed | `save_selection_to_folder` |
| **P2-11** | Lots d'images → CBZ : `ZIP_STORED` imposé, préférence de compression ZIP ignorée | Confirmed | `_run_img_conversion`, `_run_imgs_to_single_cbz` |
| **P2-12** | Outils de dessin : tout passe en RGBA ; un JPEG en niveaux de gris devient un JPEG RGB, un PNG 1 bit, L ou P devient RGBA (`_orig_mode` mémorisé mais utilisé seulement pour aplatir) | Confirmed | texte, formes, collage, clonage, flou |
| **P2-13** | Réencodage d'un WebP ou AVIF avec perte toujours à qualité 95 (`detect_jpeg_quality` rend 95 hors JPEG) ; rotation à 90° d'un JPEG réencodée avec perte au lieu d'une transposition sans perte | Confirmed | `save_image_to_bytes` |

**Corrections appliquées :**

| # | Statut | Correction | Tests |
|---|---|---|---|
| **P2-1** | Corrigé | `convert_image_data` écrit le DPI dans la cible PNG, et l'arrondit (`round()`) au lieu de le tronquer : un PNG stocke sa résolution en points par mètre, relue 299,9994 pour 300. | `test_png_conversion_writes_the_resolution_rounded` |
| **P2-2** | Corrigé (choix A : case dans la fenêtre) | Case « Sans perte » (`_QualityDialog._lossless_cb`, clé `dialogs.convert.lossless`) pour le WebP seulement : elle désactive presets et curseur, et `perform_conversion` → `_ConversionWorker` → `convert_image_data(..., lossless=True)` écrit un WebP sans perte (images fixes et animées). Vérifié avec la version de Pillow embarquée : JPEG et AVIF ne savent pas être sans perte (même à qualité 100, l'image n'est pas identique) ; PNG, TIFF, BMP et GIF le sont toujours. | `test_lossless_webp_conversion_is_pixel_identical` |
| **P2-4** | Corrigé (choix A : avertissement) | Nombre de lectures traduit d'un format à l'autre par `image_ops._animation_loop_for` : GIF sans valeur `loop` → 1, `0` → 0, `N` → N+1 en WebP et PNG animé (l'AVIF n'enregistre pas cette valeur). Fenêtre de fin de conversion : `on_finished` compte les originaux animés convertis vers un format sans animation et `_ConversionCompleteDialog` affiche `messages.questions.conversion_complete.animation_lost` au-dessus des boutons (avertissement non bloquant, couleur de texte du thème). | `test_gif_played_once_stays_played_once_in_webp_and_png` |
| **P2-5** | Corrigé | Le gris 16 bits (`I;16*`) converti en PNG ou TIFF est écrit tel quel au lieu de passer par `_to_writable_mode`. | `test_16bit_page_converted_to_png_or_tiff_stays_16bit` |
| **P2-6** | Corrigé | `archive_names.epub_reading_order` (spine de l'OPF, puis images de chaque document dans leur ordre d'apparition, couverture déclarée en tête, ordre des noms si l'EPUB est incomplet ; expressions régulières, aucun parseur XML) et `epub_page_name` (rang de lecture devant le nom de base, `0001_…`, pour que l'ordre survive aux tris et aux autres lecteurs), appliqués par les trois branches ZIP d'`ArchiveLoader` et par `import_merge_qt.py` ; `without_epub_rank` retrouve le nom de base pour rattacher les erreurs de lecture (`_attach_read_errors`). | `tests/test_image_names_and_order.py` (3 tests EPUB) ; `test_archive_member_reading.py` adapté |
| **P2-7** | Corrigé | Un JPEG MPO compte pour une seule image dans les deux fonctions du lot images → CBZ (`_run_img_conversion`, `_run_imgs_to_single_cbz`) et dans `_normalized_page_bytes` ; l'import web associe `MPO` à `jpg`. | Manuel (lot sur un dossier contenant une photo MPO) |
| **P2-8** | Corrigé (choix 3A-1 : correction dès l'ouverture) | `archive_names.name_for_real_format` : à l'ouverture et à l'ajout (`entries.create_entry`), une page dont l'extension annonce un autre format que le sien prend l'extension de son vrai format, `extension_corrected_from` garde l'ancienne (info-bulle `tooltip.extension_corrected`) et `_install_loaded_pages` marque le comic modifié. Même règle dans les lots CBR/CB7/CBT (`_copy_pages_to_cbz`) et images → CBZ. Seuls les formats gérés sont concernés ; une extension synonyme (`.jpeg`, `.tiff`) est gardée. | `tests/test_image_names_and_order.py` (5 tests) |
| **P2-9** | Corrigé | `perform_restore_color_depth`, `perform_restore_image_mode`, `perform_restore_effect` mettent à jour la ligne de la page dans `<Pages>` (`update_page_entries_in_xml_data`) et effacent `_hash`. | Manuel |
| **P2-10** | Corrigé (choix A, avec la précision de l'utilisateur : extension ajoutée, rien retiré) | Export d'une seule page : si l'extension tapée ne désigne pas le format de la page (`archive_names.same_image_format`), l'extension de la page est ajoutée (`xxx.png` pour un JPEG → `xxx.png.jpg`) ; ce nom complété n'ayant pas été vu par la boîte native, `ExistingFilesDialog` demande avant de le remplacer. Toutes les pages exportées passent par `_bytes_with_entry_dpi`, comme dans un CBZ. | Manuel |
| **P2-11** | Corrigé (confirmé par l'utilisateur) | Les deux fonctions du lot images → CBZ écrivent avec `zip_compression_kwargs(get_zip_compression_level())`, comme les autres lots. La documentation présentait l'absence de compression comme un choix voulu ; le niveau par défaut (0) reste sans compression. | — |
| **P2-12** | Corrigé (choix A : mode d'origine rendu) | `entries.drawn_page_image` rend la page dans son mode d'origine après un outil de dessin : niveaux de gris (dessin aplati sur blanc puis en gris), 1 bit (seuil à 128), `LA`, palette hors GIF (palette de 256 couleurs recalculée, transparence comprise), en plus du gris 16 bits (P1-7). Un dessin en couleur devient gris ; passer la page en couleur reste le rôle de l'outil Mode d'image. | `test_drawing_on_a_grayscale_jpeg_page_stays_grayscale`, `test_drawing_on_a_1bit_page_stays_1bit`, `test_drawing_on_a_palette_png_keeps_a_palette_and_its_transparency` |
| **P2-13** | Accepté (choix de l'utilisateur) | Aucune modification. La qualité d'un WebP ou d'un AVIF avec perte n'est pas inscrite dans le fichier : aucun outil ne peut la retrouver, seulement l'estimer grossièrement ; 95 ne dégrade pas l'image. Une rotation JPEG sans perte demanderait d'embarquer un outil externe (`jpegtran`), jugé disproportionné ; un tel outil ne mesure pas la qualité. | — |

## P3 — Robustness

Constats d'origine ci-dessous ; corrections dans le tableau qui suit. Six P3 corrigés d'une traite après les choix de l'utilisateur, P3-4 et P3-8 acceptés ; la partie « noms sans drapeau UTF-8 » de P3-4 a ensuite été corrigée, à la demande de l'utilisateur.

| # | Constat | Certitude |
|---|---|---|
| **P3-1** | Compression TIFF perdue après un outil qui recompose l'image (fichier non compressé, taille multipliée) ; cible TIFF de la conversion non compressée | Confirmed |
| **P3-2** | `transform_animated_gif` : images pleines avec `disposal=2` et requantification à chaque image (fichier plus lourd, couleurs requantifiées) ; commentaire du GIF perdu | Confirmed |
| **P3-3** | PNG RGB 48 bits ouvert en 8 bits par Pillow (toute modification réduit la profondeur) ; gris 16 bits réduit par `_to_filterable` — réduction nécessaire, jamais signalée | Confirmed |
| **P3-4** | ZIP : dates et attributs des membres non conservés ; noms anciens sans drapeau UTF-8 décodés en cp437 (caractères faux si la source était en cp1252) ; fichiers `__MACOSX/._*` chargés comme pages corrompues et recopiés par les lots | Confirmed / Possible |
| **P3-5** | DPI non isotrope réduit à sa valeur x (`entry["dpi"] = dpi[0]`) | Confirmed |
| **P3-6** | `entries.detect_jpeg_quality` encode environ 51 JPEG à chaque appel (à chaque enregistrement d'une modification JPEG) | Confirmed |
| **P3-7** | PDF : un JPEG extrait brut ne porte ni le profil ICCBased ni, avant P0-1, le tableau `/Decode` (CMJN Adobe inversé) — `/Decode` est désormais refusé par `raw_page_image`, l'ICC reste perdu | Possible |
| **P3-8** | Redressement automatique : coins remplis de blanc opaque (`fillcolor="white"`) sur une page RGBA | Possible |

**Corrections appliquées :**

| # | Statut | Correction | Tests |
|---|---|---|---|
| **P3-1** | Corrigé | `entries.tiff_save_compression(img, data, default="raw")` : compression de l'image, sinon des bytes d'origine, **toujours passée** à Pillow (sans valeur, Pillow reprend celle de `img.info`, même incompatible) ; LZW à la place d'une compression qui ne convient pas au mode écrit (CCITT hors 1 bit, JPEG hors RGB/L/CMJN), d'un TIFF JPEG (Pillow le réencoderait à qualité 75) et de toute compression exotique. Utilisée par `save_image_to_bytes`, `convert_image_data` (LZW par défaut : conversion vers TIFF compressée) et `transform_multipage_tiff`. A corrigé au passage C-8. | `test_tiff_page_keeps_its_compression_after_an_edit`, `test_group4_page_turned_gray_is_written_with_a_compatible_compression`, `test_multipage_group4_tiff_can_be_rotated`, `test_conversion_to_tiff_is_compressed_without_loss`. Manuel, validé (2,2 Mo au lieu d'environ 6 Mo ; le poids baisse quand la page s'éclaircit, signe que la compression agit) |
| **P3-2** | Corrigé | `transform_animated_gif` : frames réécrites avec leurs couleurs exactes quand elles tiennent dans une palette (`_exact_palette_frame`, comptage rapide `getcolors(257)` d'abord) ; `disposal=1` sans transparence (Pillow n'écrit que ce qui change), `disposal=2` avec (vérifié : `disposal=1` laisse alors voir la frame précédente) ; commentaire passé explicitement. Mesuré : écart de couleur 15 → 0, fichier plus léger que l'original. Le commentaire n'était en fait pas perdu dans le cas reproduit (Pillow le gardait par `img.info`), mais l'aurait été après les nouvelles frames à palette. | `test_rotated_gif_keeps_exact_colors_comment_and_durations` (avec et sans transparence). Manuel, validé |
| **P3-3** | Corrigé (choix A : avertir sans bloquer) | Garder le 16 bits partout est impossible avec Pillow (aucun mode couleur 16 bits par canal, filtres en 8 bits). Bandeau non bloquant en bas de la visionneuse (`bit_depth_warning_qt._BitDepthWarning`, clé `viewer.bit_depth_warning`) quand l'outil choisi réduirait la page : `entries.page_high_depth` → `"color16"` (PNG ou TIFF 16 bits par couleur : tout outil) ou `"gray16"` (réglages et transparence). | `test_pages_above_8_bits_per_color_are_detected`. Manuel, validé (thème, langue, police, clic absorbé) |
| **P3-4** | En partie corrigé (choix de l'utilisateur) | Fichiers `__MACOSX` et dates : acceptés — les fichiers `__MACOSX` restent visibles (ils sont dans l'archive, l'utilisateur décide), les pages réécrites prennent la date du jour (elles ont été modifiées ce jour). **Noms sans drapeau UTF-8 : corrigés dans un second temps, à la demande de l'utilisateur.** La mesure a montré que le constat d'origine était incomplet : sont aussi mal lus les noms UTF-8 sans le drapeau (outils Mac et Linux) et les majuscules accentuées des noms cp850 (dossier compressé de Windows en français : `L'Île` lu `L'╫le`). Première règle envisagée (UTF-8, puis cp850 si la lecture cp437 contient des caractères non latins) écartée après mesure : 7 noms cp1252 sur 22 y devenaient des noms faux d'apparence normale (`café` lu `cafÚ`). Règle retenue (`archive_names.zip_member_name`) : UTF-8 valide avec des caractères non ASCII → UTF-8 ; nom cp437 peu plausible (caractères non latins, ou majuscule accentuée collée après une minuscule) → cp850 s'il est plausible, sinon cp1252 ; sinon inchangé. Sur 22 noms × 4 codages : tout juste en UTF-8, cp437 et cp850, 19 sur 22 en cp1252 (limite : accent sur la première lettre d'un mot cp1252, `épisode` lu `Úpisode`). Appliquée à l'ouverture (`open_zip`, `fix_zip_names` : `ZipInfo.filename` et `NameToInfo` corrigés, jamais `orig_filename` que zipfile compare à l'en-tête local) dans le chargeur, l'ajout d'archive, la recompression en lot, l'import de métadonnées en lot et la Bibliothèque. | `tests/test_image_names_and_order.py` (3 nouveaux : quatre codages, lecture par nom, noms marqués UTF-8 intacts) ; `test_batch_metadata_save.py` adapté (`open_zip` dans l'espace de noms de la fonction extraite). Manuel, validé (CBZ UTF-8, cp850 et cp1252 ; enregistrement ; recompression) |
| **P3-5** | Corrigé | `entries.page_dpi(entry, img)` : couple `(x, y)` de l'image repris tant que sa valeur horizontale est celle d'`entry["dpi"]`, sinon la nouvelle valeur dans les deux sens. Utilisée par `save_image_to_bytes` et `convert_image_data`. | `test_different_horizontal_and_vertical_resolutions_are_kept`. Manuel, validé (fichier exporté relu : 300 × 600) |
| **P3-6** | Corrigé | Tables de référence de `detect_jpeg_quality` calculées une seule fois par session (`_jpeg_quality_tables`), même estimation. | `test_jpeg_quality_estimate` |
| **P3-7** | Corrigé | `pdf_page_images` : profil `/ICCBased` de l'image ajouté aux bytes extraits sans réencodage avec perte (APP2 d'un JPEG, PNG réécrit sans perte), sauf un profil sRGB (`_is_srgb`). **Constaté pendant la correction** : PyMuPDF déclare un profil sRGB pour toute image qu'il insère ; l'ajouter changeait les bytes d'une image extraite telle quelle, ce qui faisait échouer `test_page_made_of_one_image_is_extracted_as_is` (et pytest, en calculant la différence de deux images de plusieurs centaines de Ko, semblait bloqué). | `test_extracted_image_gets_the_color_profile_stored_apart` (JPEG et PNG) |
| **P3-8** | Accepté (choix de l'utilisateur) | Coins blancs gardés ; l'utilisateur recadre ensuite. Le redressement manuel remplit désormais lui aussi de blanc (C-7). | — |

## P4 — Maintainability

Constats d'origine ci-dessous ; corrections dans le tableau qui suit.

| # | Constat |
|---|---|
| **P4-1** | Huit encodeurs aux règles divergentes (ICC, DPI, EXIF, qualité, alpha) — cause de P1-2, P1-5, P1-9, P2-1, P2-3 ; même famille que l'Audit 2, P4-1. **Partiellement traité** par les corrections des P1 : profil ICC (`page_icc_profile`), métadonnées (`metadata_save_kwargs`), résolution (`image_dpi`) et décodage (`open_page_image`, `drawing_base_image`) ont désormais une règle commune utilisée par tous les encodeurs ; les encodeurs eux-mêmes restent distincts (qualité, choix du format) |
| **P4-2** | `IMAGE_EXTS` défini 9 fois ; correspondances extension → format dupliquées (`ext_map`, `_PAGE_FORMATS`, `ext_to_format`, `_ANIMATED_EXT_TO_FORMAT`, `_DPI_WRITABLE_EXTS`, `_JPEG_EXTS`) |
| **P4-3** | Deux fonctions `detect_jpeg_quality` aux algorithmes différents (`entries.py`, `image_processing_qt.py`) |
| **P4-4** | TIFF isolé et TIFF d'archive suivaient des règles opposées (cause de P0-2) — **réglé par la correction de P0-2** (`_single_page_tiff_bytes` commune) |
| **P4-5** | CMJN : les lots convertissent en RGB par le profil ICC, l'enregistrement depuis un panneau garde le CMJN, l'affichage convertit sans profil |

**Corrections appliquées :**

| # | Statut | Correction | Tests |
|---|---|---|---|
| **P4-1** | Reporté (choix de l'utilisateur) | Reste à faire : une fonction d'encodage commune, chaque appelant ne fixant que format et qualité. Les règles communes sont en place (profil ICC, métadonnées, résolution `image_dpi`/`page_dpi`, compression TIFF `tiff_save_compression`, décodage). Noté pour une session dédiée. | — |
| **P4-2** | Corrigé | `IMAGE_EXTS` et `JPEG_EXTS` définies une seule fois dans `archive_names.py` (module sans Qt) et importées partout : les 9 copies identiques de la liste des images et les 3 copies des extensions JPEG remplacées par un import, sans changement de comportement. Les 4 listes plus courtes (vignettes des lots CB7 et CBT, nombre de pages et couverture de la Bibliothèque, sans `.ico`/`.jfif`/`.pjpeg`/`.pjp`) remplacées aussi, à la demande de l'utilisateur : la Bibliothèque compte désormais les pages comme la mosaïque. `IMAGE_EXTS` appelée par son nom partout, sans alias (`image_exts` de `batch_dialogs_qt.py` supprimé). Les tables extension → format restent distinctes : chacune a un rôle propre (formats que la jointure sait écrire, extension cible d'une conversion, formats animés), ce ne sont pas des copies d'une même donnée. | `test_page_count_uses_the_same_image_list_as_the_mosaic` ; `test_save_dpi_bytes.py` adapté (`JPEG_EXTS` dans l'espace de noms de la fonction extraite). Manuel, validé (CBZ à pages `.jfif` : 4 pages et bonne couverture dans la Bibliothèque) |
| **P4-3** | Corrigé | `image_processing_qt.detect_jpeg_quality` (curseur de départ de l'outil Compression, 5 paliers) délègue à `entries.detect_jpeg_quality` (moindres carrés), en gardant `None` pour une image qui n'est pas un JPEG (le curseur part alors de 100 pour un WebP ou un AVIF, comme avant). Commentaires de `compression_tool_qt.py` qui citaient les 5 paliers mis à jour. | `test_compression_tool_uses_the_same_jpeg_quality_estimate` |
| **P4-5** | Corrigé (choix de l'utilisateur : affichage seulement, enregistrement inchangé) | `entries.to_screen_mode` : une page CMJN convertie en RVB par son profil ICC (`cmyk_to_rgb`), réservée à l'écran et à l'impression (vignettes, visionneuse, aperçu de la renumérotation, impression). Outils de dessin, jointure et GIF animé gardent `to_display_mode` ; l'enregistrement depuis la mosaïque recopie la page, toujours en CMJN. Mesuré sur une page CMJN FOGRA39 : vert (30, 160, 60) affiché (50, 220, 6) avant, (40, 152, 60) après. Les lots gardent leur conversion en RVB. | `test_cmyk_page_is_shown_through_its_profile`. Manuel, validé |

---

## Constats ajoutés pendant les corrections

| # | Priorité | Constat | Correction | Tests |
|---|---|---|---|---|
| **C-1** | P3 | Relevé pendant le test manuel de P0-2 : l'info-bulle d'un TIFF sans résolution affichait « DPI : 1 ». Pillow annonce `dpi = (1, 1)` pour un TIFF sans balise de résolution, et cette valeur suit l'image dans ses copies (`img.info`) : elle était affichée, mémorisée comme résolution de la page et **écrite** dans les fichiers produits (découpage d'un TIFF de plusieurs pages, conversion, modification, normalisation des lots, jointure). Défaut antérieur à l'audit, élargi par P0-2 au TIFF isolé. | `entries.image_dpi(img)` : résolution `(x, y)` ou `None`, une résolution d'1 dpi ou moins valant absence de résolution (la balise TIFF ne suffit pas : elle disparaît dans les copies, le `(1, 1)` reste). Utilisée à la place de `img.info.get("dpi")` par `create_entry`, `get_image_metadata`, `save_image_to_bytes`, `split_multipage_tiff_pages`, `create_entries_from_tiff`, `image_ops` (`transform_multipage_tiff`, `convert_image_data`), `merge_dialog_qt._finish_join`, `batch_dialogs_qt._normalized_page_bytes`. | `test_tiff_pages.py` (3 nouveaux : TIFF sans résolution → `None`, y compris après copie ; 300 dpi conservés ; découpage sans résolution écrite). Manuel, validé (« DPI : non défini » au lieu de 1, 300 dpi pour le TIFF qui en porte) |
| **C-2** | P4 | Documentation : le skill `archive-image-loading` présentait la conversion en JPEG des TIFF isolés comme le comportement voulu ; le skill `image-format-conversion` affirmait que le DPI source est préservé, alors que la branche PNG ne l'écrit pas (P2-1). | Skills `archive-image-loading`, `image-format-conversion`, `pdf-loading`, `batch-pdf-convert`, `comicinfo-metadata-editor` et `check-embedded-versions` mis à jour. | — |
| **C-3** | P0 (antérieur à l'audit) | Relevé pendant le test manuel de P1-6 : « Modifier le GIF animé » perdait **toujours la première image** du GIF. Diagnostic confirmé par des prints : 3 images reçues à l'ouverture de la fenêtre, 2 à la création. `_update_metadata` (texte d'informations de la fenêtre) lisait la première image puis libérait sa mémoire (`free_image_memory`) ; une page normale est relue depuis ses bytes, mais les images extraites d'un GIF n'existent qu'en mémoire : la première était perdue avant même le clic sur Créer. Le GIF d'origine restait dans la mosaïque (le résultat est une nouvelle entrée). | Mémoire libérée seulement si l'image peut être relue (`first.get("bytes")`). | Manuel, validé (3 images, rond rouge présent) |
| **C-4** | P0 (régression de la correction de P1-3, jamais publiée) | Relevé par la suite de tests (`test_image_formats.py`, rotations d'un TIFF RGBA) : pour un TIFF, `getexif()` de Pillow rend les balises de structure du fichier lui-même (largeur, hauteur, emplacement des données…), pas un bloc EXIF. `page_metadata` les relisait comme métadonnées, et `save_image_to_bytes` les réécrivait dans le TIFF tourné, avec les anciennes dimensions : image abîmée. | `page_metadata` ne lit plus d'EXIF dans un TIFF ; `metadata_save_kwargs` n'en écrit jamais dans un TIFF (Pillow les fusionne avec les balises qui décrivent l'image). Une conversion vers TIFF ne garde donc pas les métadonnées d'appareil photo. | `test_image_formats.py` (rotations TIFF, existant) ; `test_page_preservation.py` (`test_tiff_page_rotation_keeps_its_size_and_pixels`) |
| **C-5** | P3 (antérieur à l'audit) | Relevé pendant le test manuel de P1-1 : après Ctrl+Z / Ctrl+Y, l'info-bulle d'une vignette gardait le poids d'avant jusqu'au prochain mouvement de souris. Diagnostic par prints : la page était bien restaurée (2 082 535 → 211 318 octets), seul le texte affiché n'était pas recalculé (calculé aux seuls événements de survol). Correction hors du périmètre de l'audit, faite à la demande de l'utilisateur. | `MosaicCanvas.refresh_item_tooltip()` recalcule l'info-bulle affichée pour l'item sous le curseur ; `OverlayTooltip.is_visible()` ; appelé par `undo_redo_qt.restore_state_qt`. | Manuel, validé |
| **C-6** | P3 (antérieur à l'audit) | Relevé pendant le test manuel de P2-4 : un GIF joué une fois, converti en WebP, se jouait en boucle dans la visionneuse. Le fichier produit était correct (`loop=1`, relu avec Pillow) : la lecture animée de la visionneuse rejouait toujours une animation en boucle, quel que soit le nombre de lectures du fichier. Correction hors du périmètre de l'audit, faite à la demande de l'utilisateur. | `entries.animation_play_count(entry)` : nombre de lectures d'après l'en-tête du fichier (règles de `_animation_loop_for` : GIF sans valeur = 1, `N` = N+1 ; WebP et PNG animé : `N` ; `0` et AVIF : en boucle). `ImageViewer._animate_gif_frame` compte les lectures et s'arrête sur la dernière image, ▶ relançant depuis la première. | `test_viewer_plays_animation_as_many_times_as_the_file_says`. Manuel, validé (GIF et WebP joués une fois, GIF en boucle inchangé) |
| **C-7** | P0 (antérieur à l'audit) | Relevé en vérifiant les opérations sur le gris 16 bits pour P3-3 : `Image.rotate` avec interpolation (bilinéaire, bicubique) d'un angle quelconque écrase une image `I;16` (valeurs plafonnées à 255, page presque noire) — redressement manuel et automatique. Reproduit avec Pillow seul. | `entries.rotate_page_image(img, angle, fillcolor)` : image `I;16*` tournée en entiers 32 bits puis ramenée en 16 bits (bornée à 0-65535) ; utilisée par `straighten_tool_qt.perform_straighten` et `deskew.deskew_entry_data`. À la demande de l'utilisateur, le redressement manuel remplit aussi les coins de blanc, comme l'automatique. | `test_straightened_16bit_page_keeps_its_values`. Manuel, validé |
| **C-8** | P0 (antérieur à l'audit) | Relevé pendant la correction de P3-1 : une page TIFF 1 bit compressée en CCITT Group 4 (scans noir et blanc) passée en niveaux de gris ou retouchée gardait `compression="group4"` dans `img.info`, que Pillow réutilisait : erreur d'écriture puis plantage du programme (reproduit, code de sortie 139) ; un TIFF de plusieurs pages Group 4 ne pouvait jamais être tourné (le repli « sans compression » échouait aussi, Pillow reprenant `img.info`). | Corrigé par `tiff_save_compression` (P3-1), compression toujours passée explicitement. | Tests de P3-1. Manuel, validé |
| **C-9** | P2 (antérieur à l'audit) | Relevé par l'utilisateur pendant les tests de P3 : une visionneuse ouverte n'était pas rafraîchie après une rotation ou un miroir faits depuis la mosaïque ; même défaut, vérifié dans le code, pour le redimensionnement, le redressement automatique et la lecture d'une macro par une autre visionneuse. `render_mosaic` ne prévient la visionneuse que des pages déplacées ou retirées, pas d'un contenu modifié. Correction faite à la demande de l'utilisateur. | `refresh_image_viewers_after_external_undo_redo(state, except_viewer=None)` appelée en fin de `image_transforms_qt._run_transform`, `resize_dialog_qt._start_resize_worker`, `deskew_qt._run_deskew` et `MacroViewerMixin._macro_run` (autres visionneuses). | Manuel, validé |
| **C-10** | P3 (antérieur à l'audit) | Même vérification : le texte « nom (position/total) » en bas de la visionneuse n'était calculé qu'à l'affichage d'une page — un renommage, une renumérotation ou des pages ajoutées ou retirées ailleurs le laissaient faux. | `ImageViewer._set_name_label_pages` / `_update_name_label` (pages retrouvées par identité), appelée par `_sync_with_document` et par `NameEdit._save_state_after_rename`. | Manuel, validé |
| **C-11** | P2 (régression de la correction de P4-2, publiée en 1.8.8) | Signalé par l'utilisateur après la publication : déposer un ou plusieurs dossiers sur MosaicView ne faisait plus rien (ni fenêtre de choix du traitement par lot, ni message). La correction de P4-2 a supprimé l'alias `image_exts` de `batch_dialogs_qt.py`, mais `drop_handler_qt._show_batch_drop_dialog` l'importait encore, dans un import local à la fonction : `ImportError` à chaque drop de dossier, avalée par Qt dans le gestionnaire du drop (trace console seulement). Le drop de fichiers, qui ne passe pas par cette fonction, n'était pas touché. Cause établie par lecture du code (nom absent du module), sans prints. La recherche des imports restants lors de P4-2 n'a pas couvert les imports locaux sous alias (`image_exts as _image_exts`). | `_show_batch_drop_dialog` importe `IMAGE_EXTS` depuis `archive_names` (même liste qu'avant). Piège documenté dans `CLAUDE.md` (supprimer ou renommer un nom : chercher aussi les imports locaux) et dans les skills `drag-and-drop` et `batch-processing`. Entrée [1.8.9] du CHANGELOG. | Manuel, validé |
| **C-12** | P2 (antérieur à l'audit) | Relevé en vérifiant, après C-11, que chaque nom importé d'un module du projet y existe (scan AST de `MosaicView.py` et `modules/`) : `PanelWidget._batch_metadata` (import de métadonnées en lot lancé depuis le menu) importait `drop_handler_qt._show_centered_msgbox`, qui n'existe plus. Un dossier choisi sans CBZ/CBR/CB7/CBT/PDF levait une `ImportError` avalée par Qt : aucun message. | `ErrorDialog` non modale, mêmes clés que la branche équivalente du drop (`dialogs.batch_metadata.no_files_title`/`_message`), titre en `_wt()`, textes en lambdas. Entrée [1.8.9] du CHANGELOG. Skill `batch-processing` mis à jour. | Manuel, validé |
| **C-13** | P4 (antérieur à l'audit) | Même scan : `_PdfSummaryDialog._on_ok` (fin d'un lot PDF → CBZ) importait `pdf_unlock_qt.show_batch_pdf_unlock_dialog`, une fonction qui n'a jamais existé dans aucune version ; l'erreur était masquée par un `except Exception: pass`. Aucun déverrouillage en lot n'était donc jamais proposé ; les PDF protégés par mot de passe propriétaire restent convertis, et leur source conservée. | Appel mort retiré (choix de l'utilisateur, plutôt que de créer la fonctionnalité), avec les trois clés du bilan qui ne servaient qu'à lui (`owner_protected_pdfs`, `is_permanent`, `safe_delete_file`) ; aucun changement visible. Skills `batch-pdf-convert` et `batch-processing` corrigés (ils décrivaient ce déverrouillage comme existant). | Manuel, validé |
| **C-14** | P0 (antérieur à l'audit ; Very likely, conditions rares : support retiré, disque plein ou défaillant pendant l'écriture) | Signalé par une revue de code externe, puis vérifié dans le code et élargi. Dans `save_as_cbz`, le CBZ écrit n'était relu (`verify_written_cbz`) que si l'utilisateur demandait la suppression de l'original, **après** que `current_file`, `modified = False` et `mark_history_saved` avaient été appliqués ; en cas d'échec, l'original était gardé mais rien n'était défait : le document passait pour enregistré dans un fichier abîmé, et sa fermeture ne demandait plus confirmation — modifications perdues (l'original ne les contient pas). Constats ajoutés à la vérification : sans demande de suppression, aucune relecture ; l'enregistrement par-dessus le fichier ouvert (« Enregistrer sous » sur lui-même, « Appliquer » sur un CBZ) remplaçait l'original sans aucun contrôle ; les conversions de `_write_apply_new_names` (`_after_cbz_converted`, `_after_pdf_converted`) appelaient `_finish_apply_new_names` même après l'échec, qui marquait le document enregistré. | Une relecture complète à chaque enregistrement a été écartée par l'utilisateur (plusieurs secondes sur un gros comic). `utils.check_written_cbz_structure` : contrôle de structure sans décompression (répertoire central, nombre de fichiers, en-têtes locaux, débordement des données), appelé dans `_write_document_unlocked` sur le temporaire avant `os.replace` — échec = erreur d'enregistrement ordinaire, destination intacte, document resté « modifié » ; couvre tous les enregistrements depuis un panneau. `_write_zip_with_progress` retourne le nombre de fichiers écrits. Relecture complète inchangée avant suppression ; en cas d'échec, `_mark_save_unverified` repasse le document à « modifié » (`undo_redo.mark_history_unsaved`), et `_done(False)` remplace `_finish_apply_new_names` dans les conversions. Skills `save-export` (dont une description périmée de `_SavingOverlay`, qui passe par `canvas_overlay_qt`) et `undo-redo`, `CLAUDE.md` mis à jour. Entrée [1.8.9] du CHANGELOG. | `test_utils_atomic_write.py` (6 nouveaux : CBZ correct avec dossier, mauvais nombre de fichiers, fichier tronqué, en-tête local abîmé, message nommant le fichier final, original gardé quand le contrôle échoue dans `replace_file_atomically`). Pas de test manuel (échec d'écriture difficile à provoquer) |

---

## Formats and conversions audited with no issue found

- **CBZ / CBR / CB7 / CBT → CBZ** (panneau et lots) : octets recopiés, ordre naturel, commentaire ZIP, homonymes renommés, dossiers aplatis dans l'ordre de lecture, relecture avant suppression de la source.
- **Rotations et miroirs** : sans perte pour PNG, TIFF, BMP, GIF ; mode, palette, alpha conservés ; animations et TIFF de plusieurs pages traités page par page.
- **JPEG → JPEG** (modification) : sous-échantillonnage, ICC (contrôlé par mode) et DPI conservés.
- **WebP sans perte** : reste sans perte, y compris animé.
- **Conversions vers JPEG et BMP** : alpha aplati sur blanc (perte inhérente au format, documentée) ; **vers GIF** : transparence gardée.
- **Animations converties vers WebP, APNG, AVIF** : nombre d'images, ordre et durées conservés (hors boucle, P2-4).
- **CMJN → RGB** par le profil ICC (`cmyk_to_rgb`) ; valeurs `I`/`F` remises à l'échelle ; limites `FORMAT_MAX_SIZE`.
- **PDF rendu** (DPI choisi, ou page non extractible) : fond blanc (`alpha=False`, perte normale du passage en matriciel), JPEG q100, extension corrigée par `pdf_image_ext`, DPI écrit à l'enregistrement sans réencodage.
- **Restaurer l'original** : la page restaurée est l'octet pour octet d'avant le premier changement de l'outil sur cette page (rattachée à la page, Audit 2, P1-1) ; seul `ImageSize` de `<Pages>` n'est pas resynchronisé (P2-9).
- **Fausses pistes écartées** : `zipfile` décode les anciens noms en cp437, qui partage avec cp850 les accents usuels (é = 0x82) — seul un nom écrit en cp1252 est mal relu (P3-4) ; les rotations à 90° des formats sans perte passent par `transpose` (aucune interpolation) ; la compression TIFF est conservée par Pillow à travers les copies (`info`), elle n'est perdue qu'après une recomposition (P3-1).

## Data properties preservation matrix

| Propriété | Lecture | Représentation interne | Conversion | Save | Batch | Préservée ? |
|---|---|---|---|---|---|---|
| Alpha | OK (TIFF isolé : P0-2, corrigé) | octets d'origine | JPEG, BMP aplatis (inhérent) ; TIFF palette gardée depuis P1-8 | octets bruts | bruts | Oui hors formats sans alpha (jointure : P1-5 corrigé) |
| Animation | OK (drapeaux, durées) | octets + images à la demande | images et durées OK ; boucle (P2-4) | brute | refusée (images) / brute | Partielle (P2-4) ; édition GIF corrigée (P1-6, C-3) ; boucle et avertissement corrigés (P2-4), lecture dans la visionneuse (C-6) |
| ICC | OK | `img.info`, repris des bytes d'origine (`page_icc_profile`) | gardé si le format le stocke (P1-2) | brut ou `page_icc_profile` | converti en sRGB (CMJN) | Oui depuis P1-2 |
| EXIF / XMP | orientation appliquée (`oriented`) | dans les bytes d'origine | gardé sans orientation (sauf TIFF) | gardé sans orientation | gardé, orientation comprise | Oui depuis P1-3 (pas en TIFF, C-4) |
| Profondeur | gris 16 bits OK, affiché en 256 nuances ; 48 bits → 8 | — | 16 → 8 inutile (P2-5) | brute ; dessin : 16 bits gardés (P1-7) | I/F normalisés | Partielle (P2-5, P3-3) ; P2-5 et modes gris, 1 bit, palette après un dessin (P2-12) corrigés, reste P3-3 |
| Dimensions | sens d'affichage (`oriented_size`) | `img_width`/`img_height` | OK | OK | OK | Oui (PDF tourné : P1-4, corrigé) |
| Ordre des pages | tri naturel | liste | — | ordre de la liste | tri naturel | Oui (EPUB : P2-6, corrigé) |
| Qualité | — | — | réglage respecté | qualité estimée ; Compression : qualité choisie | q100 si normalisé | Oui depuis P1-1 (WebP/AVIF à 95, P2-13, accepté) ; WebP sans perte possible (P2-2) |
| DPI | `image_dpi` (C-1) | `entry["dpi"]` (valeur x) | PNG perdu, troncature (P2-1) | écrit | conservé | Partielle ; oui depuis P2-1 (export d'une page : P2-10) |
| Contenu des pages PDF | rendu ou image intégrée | octets | — | brut | idem | Oui depuis P0-1 |
| ComicInfo.xml | `_comic_fromstring` | octets XML | — | réécrit | — | Oui depuis P0-3 |

**Allers-retours** (état au moment de l'audit) : PNG → WebP → PNG avec perte, sans option sans perte (P2-2) ; JPEG → PNG → JPEG : ICC perdu à l'étape JPEG (P1-2), EXIF perdu (P1-3), DPI perdu à l'étape PNG (P2-1) ; CBZ → CBZ identique (seules les dates des membres changent, P3-4). Après les corrections des P1, seuls la perte de l'option sans perte (P2-2) et le DPI d'un PNG converti (P2-1) restent. Après les corrections des P2 : PNG → WebP sans perte → PNG identique pixel pour pixel ; JPEG → PNG → JPEG garde ICC, EXIF et DPI (perte JPEG inhérente seulement).

## Missing format/conversion tests

**Ajoutés** (suite passée de 614 à 636 tests avec les P0 et P1-4, à 639 avec C-1, à 655 avec les P1 et C-4, à 670 avec les P2, à 671 avec C-6, puis à 683 avec les P3 et C-7, à 686 avec les noms ZIP de P3-4, puis à 689 avec les P4, puis à 690 avec C-11 à C-13, puis à 696 avec C-14) :

| Fichier | Couvre |
|---|---|
| `test_pdf_page_images.py` (nouveau, 10 tests) | P0-1, P1-4 : extraction brute seulement pour une page réduite à son image ; résolution calculée sur la page non tournée |
| `test_tiff_pages.py` (10 nouveaux, noms attendus modifiés) | P0-2 : TIFF isolé gardé en TIFF, mode et pixels conservés par type ; C-1 : TIFF sans résolution |
| `test_comic_info.py` (5 nouveaux) | P0-3 : attributs échappés, namespace par défaut |
| `test_check_no_swallowed_loop_errors.py` | liste `ALLOWED` adaptée : la boucle de résolution des images d'une page PDF n'existe plus qu'une fois (`pdf_page_images.best_page_image`) |
| `test_page_preservation.py` (nouveau, 16 tests) | P1-1 (qualité de la compression JPEG), P1-2 (profil après luminosité, profil RGB refusé en niveaux de gris), P1-3 (page affichée droite, EXIF gardé sans orientation après modification et conversion), P1-5 (transparence gardée, fond blanc, aucune couleur cachée, 16 bits), P1-7 (affichage, passage en couleur, dessin gardant les 16 bits), P1-8 (palette transparente vers TIFF), P1-6 (GIF joué une fois), C-4 (rotation d'un TIFF) |
| `test_save_dpi_bytes.py` | adapté : `_bytes_with_entry_dpi`, extraite par AST, utilise `open_page_image` |
| `test_page_preservation.py` (8 nouveaux) | P2-1 (DPI d'un PNG converti, arrondi), P2-2 (WebP sans perte identique), P2-4 (GIF joué une fois en WebP et PNG), P2-5 (16 bits gardés en PNG et TIFF), P2-12 (dessin sur une page en gris, 1 bit, palette), C-6 (nombre de lectures d'une animation) |
| `test_image_names_and_order.py` (nouveau, 8 tests) | P2-8 (extension d'après le vrai format, page renommée et signalée), P2-6 (ordre de lecture EPUB, repli sur l'ordre des noms, rang dans le nom), `same_image_format` (P2-10) |
| `test_archive_member_reading.py` | adapté : erreurs de lecture rattachées aux pages d'EPUB nommées par leur rang |
| `test_page_preservation.py` (10 nouveaux) | P3-1 et C-8 (compression TIFF gardée, Group 4 passé en gris, TIFF Group 4 de plusieurs pages tourné, conversion vers TIFF compressée), P3-2 (couleurs exactes d'un GIF tourné, avec et sans transparence), P3-5, P3-6, P3-3 (détection des pages 16 bits), C-7 (rotation d'une page 16 bits) |
| `test_pdf_page_images.py` (2 nouveaux) | P3-7 : profil ICC rangé à part ajouté aux images extraites (JPEG, PNG) |
| `test_image_names_and_order.py` (3 nouveaux) | P3-4 : noms ZIP sans drapeau UTF-8 relus dans leur vrai codage (UTF-8, cp437, cp850, cp1252), lecture par nom, noms marqués UTF-8 intacts |
| `test_batch_metadata_save.py` | adapté : `open_zip` ajouté à l'espace de noms de `_save_state_for_file`, extraite par AST |
| `test_page_preservation.py` (2 nouveaux) | P4-3 (même estimation de qualité JPEG pour l'outil Compression), P4-5 (page CMJN affichée par son profil) |
| `test_library_db.py` (1 nouveau) | P4-2 : nombre de pages compté avec la liste unique des extensions d'image |
| `test_save_dpi_bytes.py` | adapté : `JPEG_EXTS` ajouté à l'espace de noms des fonctions extraites |
| `test_utils_atomic_write.py` (6 nouveaux) | C-14 : contrôle rapide de la structure d'un CBZ écrit, original gardé si ce contrôle échoue |
| `test_check_internal_imports.py` (nouveau, 1 test) | C-11 à C-13 : scan AST statique, sans Qt, de `MosaicView.py` et `modules/` — chaque nom importé d'un module `modules.…`, imports locaux compris, doit y être défini au niveau module ou désigner un sous-module |

**Toujours manquants** (critères : propriétés comparées à une référence calculée directement avec Pillow ; égalité des pixels pour les formats sans perte, PSNR au-dessus d'un seuil et taille comparée à un encodage direct pour les formats avec perte — jamais la seule réouverture du fichier) :

- P1-6 (durées et transparence) et C-3 : création d'un GIF par la fenêtre — la logique est dans la fenêtre Qt, à extraire en fonction pure pour la tester ;
- P1-9 : découpe d'un WebP sans perte — `_encode_part` est imbriquée dans la fenêtre Qt ;
- C-5 : rafraîchissement de l'info-bulle — dépend de Qt ;
- P2-4 : avertissement de la fenêtre de fin de conversion — dépend de Qt ;
- P2-7 : lot images → CBZ sur une photo MPO — la boucle est imbriquée dans le lot (fonction extraite par AST à prévoir) ;
- P2-9, P2-10 : `<Pages>` après « Restaurer l'original », nom complété à l'export d'une page — dépendent de la visionneuse et de la boîte native ;
- C-6 : arrêt de la lecture dans la visionneuse — dépend de Qt (seul le nombre de lectures est testé) ;
- C-14 : document repassé à « modifié » après l'échec de la relecture complète (`_mark_save_unverified`) — dépend des fenêtres de confirmation Qt.

## Recommended fixes

Les trois P0 et P1-4 ont été corrigés d'une traite, à la demande de l'utilisateur, puis C-1 ; suite complète passée (639 tests) ; tous validés dans l'application. Les huit autres P1 ont ensuite été corrigés d'une traite, après les choix de l'utilisateur (P1-3 : afficher droit et réenregistrer tourné ; P1-5 : transparence gardée si le format la stocke, fond non couvert blanc ; P1-6 : délai propre à chaque image tant que le réglage n'est pas modifié ; P1-7 : page gardée en 16 bits, seuls les pixels dessinés à 256 nuances), avec P2-3 et les constats C-3, C-4 et C-5 ; suite complète passée (655 tests) ; tous validés dans l'application, sauf P1-2 (tests automatiques seulement, le profil n'étant pas visible dans l'application). Restent :

1. ~~**Les P2**~~ — faits : onze P2 corrigés d'une traite après les choix de l'utilisateur (P2-2 : case « Sans perte » pour le WebP ; P2-4 : avertissement dans la fenêtre de fin ; P2-8 : extension corrigée dès l'ouverture ; P2-10 : extension ajoutée au nom tapé ; P2-12 : mode d'origine rendu après un dessin), P2-13 accepté, avec le constat C-6 ; suite complète passée (671 tests) ; tous validés dans l'application. Trois nouvelles clés de traduction (`dialogs.convert.lossless`, `messages.questions.conversion_complete.animation_lost`, `tooltip.extension_corrected`), traduites dans toutes les langues et vérifiées (skill `verify-translation`, mode par clés : 9 corrections de vocabulaire et de ponctuation).
2. ~~**Les P3**~~ — faits : six P3 corrigés d'une traite après les choix de l'utilisateur (P3-3 : avertissement sans blocage ; P3-4 et P3-8 acceptés, puis la partie « noms sans drapeau UTF-8 » de P3-4 corrigée, suite passée à 686 tests), avec les constats C-7 à C-10 ; suite complète passée (683 tests) ; validés dans l'application. Nouvelle clé `viewer.bit_depth_warning`, traduite dans toutes les langues et vérifiée (3 reformulations : lituanien, letton, estonien). Constat annexe, non corrigé : les garde-fous `test_check_*` relisent chacun tout le code de `modules/qt/`, environ 23 s sur les 50 s de la suite ; une lecture unique partagée est proposée.
3. ~~**P4-2, P4-3, P4-5**~~ — faits (P4-5 : affichage seulement, à la demande de l'utilisateur) ; suite passée à 689 tests ; validés dans l'application.
4. **P4-1**, reporté à une session dédiée : un encodeur commun (les règles ICC, métadonnées, DPI, compression TIFF et décodage sont déjà communes). Noté dans le fichier d'idées de l'utilisateur.
La clé de traduction `dialogs.gif_animated.creating_quantizing`, devenue inutilisée par la correction de P1-6 (plus de quantification faite par MosaicView), a été retirée des 46 fichiers de langue, après vérification qu'aucun code, test, page web ni outil ne l'utilisait.

---

# Audit 6 — Sécurité, fichiers malformés et entrées hostiles (version 1.8.9)

- **Période** : octobre 2026 (audit, puis corrections intégrées à la version 1.8.9, pas encore publiée au moment des corrections).
- **Objet** : déterminer si un fichier, une archive, une image, un PDF, une réponse réseau ou une autre donnée externe spécialement conçue peut provoquer un plantage, une corruption ou une perte de données, un blocage, une consommation excessive de ressources, l'écrasement d'un fichier inattendu, l'accès à un emplacement inattendu, l'exécution d'un code ou d'une commande, ou le contournement d'une validation. Distinguer une entrée inhabituelle mais légitime d'une entrée réellement dangereuse : un fichier malformé qui produit une simple erreur n'est pas une vulnérabilité. Distinct des audits 1 (interactions), 2 (opérations destructives), 3 (machines à états), 4 (asynchronisme) et 5 (préservation des formats).
- **Méthode** : cartographie de toutes les frontières entre données externes et internes (archives, images, PDF, ComicInfo.xml, ComicVine, import Web, `.url`/`.webloc`, base `.mvdb`, macros, glisser-déposer, canal d'instance unique) ; lecture du code de chaque point d'entrée jusqu'à l'usage final (chemin, sous-processus, affichage, écriture) ; reproductions sans Qt dans un dossier de travail hors du dépôt : `7z.exe` embarqué (noms de membres à jokers), création de fichiers piégés sur NTFS (point final, `:`, `::$DATA`), `os.walk` sur une jonction NTFS, temps d'exécution des expressions régulières de l'import Web, registre de formats de Pillow 12.3.0, code de `rarfile` 4.3. Deux points dépendant de Qt ou de Windows ont été confirmés par l'utilisateur dans l'application avec des CBZ de test inoffensifs (P0-2, P1-1). Aucune modification de code avant présentation du rapport initial ; corrections ensuite, à la demande de l'utilisateur.
- **Code de référence** : MosaicView 1.8.9 en cours (dépôt au commit `0ac1d05`, qui contient les corrections des audits 1 à 5 ; la demande d'audit parlait de 1.8.7). Python 3.11.9, Pillow 12.3.0, PyMuPDF 1.28.2, rarfile 4.3.

## Executive summary

| Priorité | Constats | Certitude | Statut |
|---|---|---|---|
| **P0** — sécurité critique / intégrité des données | 2 | 2 Confirmed (reproduits ; P0-2 confirmé dans l'application) | 2 corrigés, validés dans l'application |
| **P1** — vulnérabilité significative | 2 | 1 Confirmed (confirmé dans l'application), 1 Possible | 2 corrigés ; P1-1 validé dans l'application, P1-2 par tests automatiques seulement |
| **P2** — fichiers malformés | 7 | 5 Confirmed, 2 Possible | 7 corrigés (P2-3 autrement que recommandé, après vérification) ; validés dans l'application ou par tests automatiques |
| **P3** — ressources / robustesse | 7 | 2 Confirmed, 3 Very likely, 2 Possible | P3-1 (CB7 dans un second temps, version 1.8.10), P3-4, P3-7 corrigés ; P3-5 et P3-6 corrigés (1.8.10) ; P3-2, P3-3 non corrigés (raisons dans les fiches) |
| **P4** — maintenabilité / défense en profondeur | 6 | — | P4-2 et P4-4 réglés par P1-2 et P0-2 ; P4-1 en partie (garde-fou AST ajouté en 1.8.10, fonction commune écartée) ; P4-3 et P4-6 corrigés (1.8.10) ; P4-5 non corrigé, reporté |
| **Constats ajoutés pendant les corrections** | 16 | — | C-1 et C-2 sans correction nécessaire ; C-3 à C-16 corrigés (version 1.8.10), validés dans l'application sauf C-7 (traduction, vérifiée par les contrôles lexicaux), C-11 et C-12 (tests automatiques) ; C-10 et C-13 à C-16 antérieurs à l'audit et sans rapport avec la sécurité |

**Frontières d'entrée identifiées** : 18 (détail dans « Input validation map »).

**Principales surfaces d'attaque** : noms et contenu des membres d'archives ; métadonnées (ComicInfo.xml, ComicVine) affichées dans des `QLabel` ; fichiers non-image ouverts avec l'application par défaut ; décodeurs d'images (Pillow) et de PDF (PyMuPDF) ; pages Web de l'import ; base `.mvdb`.

**Protections existantes qui ont fait leurs preuves** : aucune archive n'est extraite sur le disque (tout est lu en mémoire : pas de Zip Slip classique) ; `utils.safe_join` pour toute écriture d'un nom venu d'une archive (vérifiée avec `..`, chemins absolus, autre lecteur, UNC, `C:x`) ; sous-processus en liste d'arguments, jamais `shell=True`, avec `--` avant les noms (7z, rarfile) ; `defusedxml` embarqué pour ComicInfo.xml, OPF des EPUB lu par expressions régulières ; `utils.open_url` qui n'ouvre que http/https ; rendu PDF dans un processus séparé dont la mort est détectée ; validation des noms de pages à l'enregistrement (`_validate_filenames_qt`) ; clé API ComicVine masquée dans les erreurs (`_redact_api_key`).

### Causes communes

| Cause | Constats concernés |
|---|---|
| **Un nom venu de l'extérieur passé tel quel à un outil qui l'interprète** : jokers de 7z, `getmember` de tarfile, normalisation des noms par Windows (point final, `:`, noms réservés) | P0-1, P0-2, P2-2, P2-6 (même famille que l'Audit 2, P1-3 : homonymes ZIP lus par nom) |
| **Texte externe affiché dans des `QLabel` en détection automatique** : aucun `QLabel` du projet n'imposait `Qt.PlainText` | P1-1 |
| **Aucune limite avant de consommer une donnée non contrôlée** (taille d'un membre, nombre d'adresses ou de tours de boucle, durée d'un téléchargement, pixels, frames) | P3-1 à P3-4 |
| **Liste noire là où il faut une liste blanche** (extensions ouvertes, formats reconnus par Pillow) | P0-2, P1-2 |
| **Relecture qui contrôle la structure, pas le contenu** : un CBZ produit avec de mauvaises pages passe la vérification avant suppression de la source | P0-1 (même angle mort que l'Audit 2, P3-5) |

---

## P0 — Critical security / data integrity

### P0-1 · CB7 : un nom de membre contenant `*` ou `?` est pris pour un joker par 7z, et la page reçoit le contenu d'autres membres

- **Certitude** : Confirmed (reproduit avec le `7z.exe` embarqué) · **Gravité** : haute — page remplacée par une autre dans le CBZ produit, puis CB7 supprimé · **Statut** : Corrigé · **Lien** : même famille que l'Audit 2, P0-1 (membres CB7 ignorés) et P1-3 (homonymes ZIP)
- **Point d'entrée** : ouverture et ajout d'un CB7, lot CB7 → CBZ, vignette de la Bibliothèque, import de métadonnées en lot.
- **Donnée contrôlée** : nom de membre d'un `.cb7`. Il ne peut pas exister sur un disque Windows, mais une archive créée ailleurs peut le contenir, et des `?` apparaissent aussi naturellement après une erreur de jeu de caractères.
- **Validation d'origine** : `--` empêche bien qu'un nom soit lu comme une option ou une liste `@fichier` (vérifié) ; rien n'empêchait l'interprétation des jokers.
- **Code** : `archive_loader.py` → `_read_7z_file` (`7z e -so <archive> -- <nom>`), appelée par `LoadWorker.load_archive`, `load_multiple_archives`, `_read_entries`, `batch_dialogs_qt.py` (lot CB7 par `_copy_pages_to_cbz`), `import_merge_qt.py`, `batch_metadata_dialog_qt.py`, `library_window.py`.
- **Reproduction** : archive contenant `page1.jpg` (`AAAA`) puis `page*.jpg` (`BBBB`) ; la lecture de `page*.jpg` rendait `AAAABBBB`.
- **Comportement constaté** : à l'ouverture, Pillow décode la première image du flux : la page montre l'image de `page1.jpg`. Dans le lot, ces octets sont écrits bruts ; `_cbz_verified` relit le CBZ (structure, nombre de pages) sans le comparer à la source, puis le CB7 est supprimé (Corbeille par défaut). Les octets d'origine restent à la fin du fichier, invisibles pour tout lecteur.
- **Attendu** : chaque nom désigne ce seul membre.
- **Correction appliquée** : option `-spd` (« pas de jokers dans les noms ») ajoutée à `_read_7z_file` ; vérifié qu'elle rend exactement le membre demandé.
- **Tests** : `tests/test_7z_member_extraction.py` → `test_wildcard_characters_in_member_name_are_literal` (archive avec `page1.jpg`, `page*.jpg`, `page?.jpg` créés par `-si<nom>`). Manuel, validé (CB7 habituels ouverts, lot CB7 → CBZ sur une copie).
- **Interactions** : lots × Corbeille ; toute nouvelle lecture CB7 doit passer par `_read_7z_file`.

### P0-2 · « Lire avec l'application par défaut » : liste noire d'extensions contournable par un point final, et incomplète (numéroté P1-2 dans le rapport initial)

- **Certitude** : Confirmed (contournement reproduit sur NTFS, lancement confirmé dans l'application) · **Gravité** : critique — exécution d'un programme contenu dans une archive téléchargée, au double-clic sur sa vignette · **Statut** : Corrigé
- **Reclassement** : le rapport initial classait ce constat en P1 (certitude « Possible » pour le lancement). Le test manuel de l'utilisateur (CBZ contenant `note.txt.`, double-clic → Bloc-notes ouvert) a montré que Windows ouvre bien le fichier malgré le point final : avec `evil.exe.`, c'est le programme qui serait lancé. Reclassé P0 (exécution de code contrôlée par un fichier).
- **Point d'entrée** : double-clic sur une entrée non-image de la mosaïque → `panel_widget.PanelWidget._open_non_image_entry` → `open_with_default_app_qt.open_file_with_default_app` → `os.startfile`.
- **Donnée contrôlée** : `orig_name` du membre d'archive.
- **Validation d'origine** : `os.path.splitext(orig_name)[1].lower() in _EXECUTABLE_EXTS` (liste noire d'une centaine d'extensions), puis `safe_join`.
- **Constat** : pour `evil.exe.`, `splitext` rend l'extension `.` : le filtre laisse passer ; `open()` crée `evil.exe` (Windows retire le point et l'espace finaux, vérifié), puis `os.startfile` le lance. Même chose pour `evil.exe ` et `evil.bat::$DATA` (flux par défaut). La liste noire était en outre incomplète : `.pyc`/`.pyo` (exécutés là où le lanceur Python est associé), `.rtf` (vecteur d'attaque d'Office, alors que `.docx` était bloqué « par principe »), `.one`, `.msu`, `.mht`, `.html` et `.svg` (script dans le navigateur).
- **Attendu** : n'ouvrir que des types sans danger, jugés sur le nom réellement écrit.
- **Correction appliquée** : liste blanche `_OPENABLE_EXTS` (texte, PDF, EPUB, sons, vidéos, images que MosaicView n'affiche pas) à la place de `_EXECUTABLE_EXTS`. Nouvelle fonction `openable_temp_name(orig_name)` : nom rendu valide sous Windows par `utils.windows_safe_relpath` (point et espace finaux retirés, `:` remplacé, `..` retirés), puis extension contrôlée sur ce nom-là. Tout autre type est refusé avec le message existant `messages.warnings.executable_file_open` (« Ouverture bloquée »), sans nouvelle clé de traduction. **Limite connue** : ce message dit que le type « peut exécuter du code », ce qui est trop fort pour un type simplement inconnu ; changer la formulation demanderait de retraduire la clé dans toutes les langues (non fait). **Complété ensuite (1.8.10)** : message remplacé par une nouvelle clé, voir C-6.
- **Tests** : `tests/test_untrusted_names.py` (12 noms refusés, dont `evil.exe.`, `evil.exe `, `evil.bat::$DATA`, `sub/evil.cmd.`, `x.pyc`, `x.rtf`, `x.html`, `x.svg` ; 5 noms acceptés sous leur nom écrit, dont `note.txt.` → `note.txt`). Manuel, validé (`.txt` et `.pdf` ouverts, `.html` et `.docx` refusés).
- **Interactions** : surveillance du fichier temporaire (`_start_watch_thread`), qui suit désormais le nom nettoyé.

---

## P1 — Significant security issues

### P1-1 · Du HTML venu d'un comic ou de ComicVine est interprété par des `QLabel` : une balise `<img>` fait charger un chemin local ou réseau dès l'affichage

- **Certitude** : Confirmed (injection établie par le code ; chargement confirmé dans l'application par l'utilisateur) · **Gravité** : haute — avec un chemin `\\serveur\partage\…`, Windows contacte ce serveur et lui envoie l'empreinte (hash NTLM) du mot de passe de session, sans aucun clic · **Statut** : Corrigé · **Lien** : règle de sécurité n° 1 de `CLAUDE.md` (même vecteur, jusque-là traité pour les clics seulement)
- **Points d'entrée** :
  - onglet Métadonnées (`tabs_qt.py` → `MetadataTab.refresh`) : le champ `web` était inséré dans `<a href="{value}">` sans échapper `"`, `<` ni `>` dans l'attribut ; tous les autres champs passaient par `_SelectableLabel(str(value))`, en détection automatique ;
  - aperçu de la Bibliothèque (`library_window.py`, construction des lignes de `_META_FIELDS` : `QLabel(str(val))`), affiché au simple clic sur une ligne, pour des fichiers indexés automatiquement ;
  - fenêtres « Fichiers ignorés » et « Images corrompues » (`archive_loader.py` → `_MessageDialog`, `_CorruptedImagesDialog`), dont les lignes commencent par des noms de membres d'archive ;
  - `comicvine_scraper._strip_html` : balises retirées **puis** `&lt;`/`&gt;` décodés — une description ComicVine (wiki modifiable par tous) contenant `&lt;img src=…&gt;` devenait une vraie balise dans le `Summary` écrit dans ComicInfo.xml.
- **Mécanisme** : `Qt::mightBeRichText` examine la première ligne du texte ; un texte qui commence par une balise connue passe le `QLabel` en texte riche, et `QTextDocument::loadResource` charge alors la source de l'image comme fichier local (chemin UNC compris).
- **Test de confirmation** (avant correction) : CBZ dont le `Summary` vaut `<img src="C:/Windows/Web/Screen/img100.jpg"> TEXTE NORMAL SI TOUT VA BIEN` → l'utilisateur a vu le fond d'écran Windows au-dessus du texte dans l'onglet Métadonnées.
- **Correction appliquée** : `setTextFormat(Qt.PlainText)` **avant** `setText` (sinon le texte riche serait analysé une première fois) dans `_SelectableLabel`, l'aperçu de la Bibliothèque, `_MessageDialog` et `_CorruptedImagesDialog` (traductions et appelants vérifiés : tout est du texte brut) ; `html.escape(value, quote=True)` pour le texte et l'attribut `href` du champ Web (le lien reste cliquable, ouvert par `open_url`) ; `_strip_html` retire, après le décodage des entités, ce qui a la forme d'une balise (`</?lettre…>`) — « a < b » reste intact.
- **Tests** : `tests/test_comicvine_strip_html.py` (nouveau, 5 tests). Manuel, validé : même CBZ → texte brut affiché, sans image ; lien Web cliquable avec son menu contextuel ; aperçu de la Bibliothèque ; fenêtre « Images corrompues ».
- **Interactions** : tout autre affichage de texte externe dans un `QLabel` (voir P4-1).

### P1-2 · Pillow choisit le décodeur d'après le contenu parmi tous ses formats, y compris EPS, rendu par Ghostscript (numéroté P1-3 dans le rapport initial)

- **Certitude** : Possible (exige Ghostscript installé ; absent de la machine de l'utilisateur) · **Statut** : Corrigé (tests automatiques seulement)
- **Point d'entrée** : toute page lue (`entries.create_entry` : `Image.open` → `verify()` → `load()` ; vignettes, lots, `ensure_image_loaded`).
- **Donnée contrôlée** : contenu d'un membre nommé `.jpg`.
- **Constat** : l'extension sert seulement à décider qu'il s'agit d'une image ; `Image.open` essaie ensuite plus de 40 formats. `collect_all('PIL')` embarque `EpsImagePlugin`, qui lance `gswin64c` pour décoder un EPS. Ghostscript a connu des failles de contournement de `-dSAFER` (2023-2024) : comportement lié à la version installée, non vérifié. Les décodeurs de formats rares élargissent aussi la surface d'attaque.
- **Correction appliquée** : `archive_names.restrict_pillow_formats()`, appelée au démarrage dans `MosaicView.py` : `Image.ID` réduit sur place aux formats de `_FORMAT_EXTENSIONS` (AVIF, BMP, DIB, GIF, PNG, ICO, JPEG, TIFF, WebP ; MPO passe par le décodeur JPEG). Vérifié : `Image.open` compare `ID` par identité et `Image.init()` ne la remplit plus ensuite ; `ImageGrab.grabclipboard` (presse-papiers) construit ses images sans passer par ce registre ; les images extraites des PDF sont limitées à JPEG, PNG et WebP ; aucune fenêtre d'ouverture ne propose d'autre format. L'enregistrement n'est pas concerné. Une page dans un autre format devient une page illisible (cadre rouge).
- **Tests** : `tests/test_pillow_formats.py` (nouveau, 11 tests : formats gérés toujours ouverts, EPS non reconnu et Ghostscript jamais appelé, PPM refusé, page `.jpg` contenant un EPS marquée corrompue ; registre restauré après chaque test).
- **Interactions** : processus PDF (`spawn`), qui réexécute le haut de `MosaicView.py` et reçoit donc la même restriction.

---

## P2 — Malformed input handling

| # | Constat | Certitude | Code |
|---|---|---|---|
| **P2-1** | Attribut `<Page Image="abc">` (ou `ImageWidth`/`ImageHeight` non entier) : `int()` sans protection dans le thread du tableau Pages ; l'exception arrête le thread, `done` n'est jamais émis, le tableau reste vide | Confirmed | `tabs_qt.py` → `_PagesModelBuilder.run` |
| **P2-2** | Noms non valides sous Windows dans les écritures sur disque : `ab:c.jpg` écrit un flux caché du fichier `ab` (vide à l'écran, vérifié) ; `CON.jpg` désigne la console ; deux pages homonymes de dossiers différents écrasent le même fichier du glisser-déposer (`basename`) ; le glisser-déposer abandonnait toutes les pages suivantes dès une erreur ; l'export vers un dossier s'arrêtait au milieu (erreur Windows), pages déjà écrites laissées | Confirmed | `mosaic_canvas.py` → `MosaicCanvas._start_drag` (bloc CF_HDROP), `clipboard_qt._copy_entries_to_system_clipboard`, `file_operations_qt.save_selection_to_folder`, `open_with_default_app_qt` |
| **P2-3** | Bibliothèque : `LibraryDB.get_absolute_path` retombait sur `normpath(join(master, rel))` quand `safe_join` refusait le chemin — validation jugée sans effet ; une ligne d'une base abîmée ou reçue d'un tiers pouvait désigner un fichier quelconque, livré à la conversion (qui supprime la source) ou à `os.startfile` | Confirmed (code) / Possible (scénario) | `library_db.py` → `LibraryDB.get_absolute_path` |
| **P2-4** | `os.walk` (Python 3.11) traverse les jonctions NTFS (vérifié) : un lot lancé sur un dossier traite, puis supprime après conversion, des fichiers situés ailleurs ; une jonction vers un dossier parent fait traiter les fichiers plusieurs fois | Confirmed | `batch_dialogs_qt.py` (collectes des lots), `drop_handler_qt.py`, `library_db.LibraryDB.scan`, `panel_widget.py` (import de métadonnées en lot) |
| **P2-5** | Macros : le champ `name` lu **dans le JSON** servait à la suppression et au renommage (`_macro_file_path`) sans repasser par `validate_macro_name` ; un nom `..\..\x` désigne un `.json` hors du dossier, un nom différent du nom de fichier fait supprimer une autre macro | Confirmed (code) / Possible (macro copiée d'un tiers ou fichier renommé à la main) | `macro_engine.py` → `load_macro` |
| **P2-6** | CBT avec deux membres de même nom : `archive.getmember(nom)` rend le **dernier** pour les deux — deux pages identiques, la première jamais montrée ; un lien symbolique homonyme placé après une page était lu à sa place (puis ignoré : page manquante) | Confirmed (documentation de `tarfile`) | `archive_loader.LoadWorker` (branches CBT), `import_merge_qt.py` → `_load_tar` |
| **P2-7** | Export d'une seule page : boîte native préremplie avec `orig_name`, qui peut être un chemin absolu (`C:/…`) ou contenir `..` — proposition d'écrire hors du dossier d'export (confirmation de l'utilisateur et de Windows nécessaires) | Possible | `file_operations_qt.save_selection_to_folder` (branche d'une seule page) |

**Corrections appliquées :**

| # | Statut | Correction | Tests |
|---|---|---|---|
| **P2-1** | Corrigé | `tabs_qt._page_attr_int(value)` : entier, 0 si la valeur n'en est pas un ; utilisée pour les trois colonnes. | `test_untrusted_names.py` (6 cas, fonction extraite par AST) |
| **P2-2** | Corrigé | Nouvelles fonctions de `utils.py` : `windows_safe_component` (caractères interdits et de contrôle → `_`, points et espaces finaux retirés, nom réservé préfixé de `_`), `windows_safe_relpath` (même règle par segment, `.`, `..` et segments vides retirés : jamais de dossier parent ni de chemin absolu), `unique_names` (« (2) », « (3) »… avant l'extension, sans tenir compte de la casse). Appliquées à l'export vers un dossier, au presse-papiers et au glisser-déposer (qui continue après une page en erreur). L'enregistrement dans un CBZ n'est pas concerné (noms du ZIP inchangés). | `test_untrusted_names.py` (11 + 8 + 1 cas). Manuel, validé (pages homonymes exportées en `p.jpg` et `p (2).jpg`, copier-coller et glisser vers l'Explorateur) |
| **P2-3** | Corrigé, **autrement que recommandé** | Vérification faite pendant la correction : le repli est **nécessaire** — un fichier d'un répertoire additionnel a un chemin relatif au maître en `../…`, ou absolu sur un autre lecteur (`LibraryDB.scan`, `os.path.relpath`). Le supprimer aurait rendu ces fichiers inaccessibles. Le chemin reconstruit n'est désormais accepté que s'il est dans l'un des répertoires de la base (`_is_in_library_dirs`, déjà utilisée par `reindex_files`), sinon `None` (les appelants ignorent alors l'action). Limite : les répertoires sont eux-mêmes lus dans la base ; une base hostile qui déclare un répertoire quelconque n'est pas arrêtée (une `.mvdb` reste un fichier de l'utilisateur, de confiance). | `test_library_db.py` (2 nouveaux : fichier d'un répertoire additionnel accessible ; chemins `../secret.pdf` et absolu refusés). Manuel, validé |
| **P2-4** | Corrigé | `utils.walk_without_links(top)` : `os.walk` qui retire de la descente les liens symboliques et les jonctions (`st_reparse_tag` = `IO_REPARSE_TAG_MOUNT_POINT` ou `IO_REPARSE_TAG_SYMLINK`) ; les autres points d'analyse (dossiers OneDrive à la demande, déduplication) restent de vrais dossiers. Utilisée aux 14 parcours de dossiers de l'utilisateur. | `test_untrusted_names.py` (jonction créée par `mklink /J` dans le dossier temporaire du test, puis retirée seule) |
| **P2-5** | Corrigé | `load_macro` donne à la macro le nom de son fichier ; celui du JSON est ignoré. | `test_macro_engine.py` (2 nouveaux). Manuel, validé (suppression et renommage) |
| **P2-6** | Corrigé | `archive_loader._tar_member_reader(archive)` : comme `_zip_member_reader`, rend chaque homonyme à son tour (tri stable : ordre de l'archive), fichiers ordinaires seulement ; utilisée par les deux branches CBT de `LoadWorker` et par `import_merge_qt._load_tar`. Le lot CBT itérait déjà sur les `TarInfo`. | `test_archive_member_reading.py` (homonymes, lien homonyme jamais lu à la place de la page). Manuel, validé (CBT habituels) |
| **P2-7** | Corrigé | Nom proposé réduit au nom de base, rendu valide (`windows_safe_component`). | — (pas de test manuel dédié) |

## P3 — Resource exhaustion / robustness

| # | Constat | Certitude | Code |
|---|---|---|---|
| **P3-1** | Aucune limite de taille avant de lire un membre en mémoire : `zf.read` (bombe de décompression ZIP), `rarfile.read`, sortie standard de 7z capturée entière, membre TAR « creux » de taille annoncée énorme rempli de zéros par `extractfile().read()` | Very likely | `archive_loader.py`, `import_merge_qt.py`, lots CBR/CB7/CBT |
| **P3-2** | `Image.MAX_IMAGE_PIXELS = 500_000_000` : l'erreur de Pillow ne tombe qu'à 1 milliard de pixels ; chaque page est décodée entièrement deux fois au chargement (`create_entry` → `load()`, puis `build_qimage_for_entry`) : pics de 3 à 4 Go pour une page « bombe » de quelques Ko | Very likely | `MosaicView.py`, `entries.create_entry`, `mosaic_canvas.build_qimage_for_entry` |
| **P3-3** | Nombres non plafonnés : toutes les images d'un WebP/AVIF/APNG animé décodées au chargement (`set_animated_image_info`), TIFF éclaté en autant d'entrées que de pages (`expand_multipage_tiffs`), pages d'un PDF, entrées d'un ZIP — chacune avec sa vignette en mémoire | Very likely | `entries.py`, `pdf_loading_qt.py` |
| **P3-4** | Import Web : `response.read()` sans limite (le délai de `urlopen` vaut pour chaque lecture, pas pour le téléchargement : une réponse lente et sans fin ne s'arrête jamais) ; `range(start, end+1)` des boucles JS sans limite (`i < 1e12`) ; expression régulière de concaténation **quadratique, mesurée** (8 000 caractères : 2,4 s ; 1 Mo : plusieurs heures) ; `_ResolveWorker` non annulable | Confirmed | `web_import_qt.py` → `extract_images_from_js_loops`, `_JS_CONCAT_TERM_RE`, `_ResolveWorker.run`, `_DownloadWorker.run` |
| **P3-5** | PyMuPDF dans le processus de l'application (nombre de pages du scan de la Bibliothèque, vignette, import de métadonnées en lot, déverrouillage) : un PDF qui fait planter MuPDF ferme l'application, hors de l'isolation du chargement PDF | Possible | `library_db.py`, `library_window.py`, `batch_metadata_dialog_qt.py`, `pdf_unlock_qt.py` (lien : Audit 4, P1-5) |
| **P3-6** | `Image.MAX_IMAGE_PIXELS = None` posé globalement depuis le thread de chargement, le temps de relire les dimensions d'une page trop grande : un autre thread qui ouvre une image pendant cette fenêtre échappe à la limite | Possible | `entries.create_entry` (branche `DecompressionBombError`) |
| **P3-7** | `cleanup_legacy_root_clipboard_dirs` supprimait au démarrage tout dossier `%TEMP%\clipboard_*`, quel que soit le logiciel qui l'avait créé | Possible | `temp_files.py` |

**Corrections appliquées :**

| # | Statut | Correction | Tests |
|---|---|---|---|
| **P3-1** | Corrigé (CB7 dans un second temps) | `archive_loader.MAX_MEMBER_SIZE` (500 Mo, la limite déjà appliquée à une image ouverte seule) et `_check_member_size(size)` : taille **annoncée** contrôlée avant toute lecture (`zipfile` ne rend jamais plus que la taille annoncée). Lecteurs `_zip_member_reader`, `_rar_read`, `_tar_member_reader`, `_tar_info_read`, utilisés à l'ouverture, à l'ajout et dans les lots CBR et CBT. Un membre trop grand devient une page illisible, avec pour raison « 4.20 Go > 500.0 Mo » (sans mots : rien à traduire), que l'enregistrement refuse d'omettre ; dans un lot, le CBZ est déclaré incomplet et la source gardée. **CB7 non couvert** : 7z ne donne la taille d'un membre qu'en modifiant `_list_7z_files` et ses appelants. **Complété ensuite (1.8.10)** : plutôt que la taille annoncée par la liste de 7z, `_read_7z_file` limite la taille **reçue**, ce qui protège tous les appelants sans modifier leur code : sortie de 7z lue par blocs de 1 Mo (`subprocess.Popen`), extraction arrêtée (`proc.kill()`) dès que le total dépasse `MAX_MEMBER_SIZE` (`_check_member_size` lève `ValueError`), sortie d'erreur lue par un thread (un tube plein bloquerait 7z, donc la lecture). Vérifié avec le `7z.exe` embarqué : page de 3 Mo refusée en 0,4 s avec une limite abaissée à 1 Mo pour l'essai. **Modifié ensuite (1.8.11)** : `MAX_MEMBER_SIZE` n'est plus définie dans `archive_loader.py` mais dans `archive_names.py` (sans Qt), source unique importée par `archive_loader.py`, par `entries.py` (qui avait deux copies en dur de la même valeur pour une image ouverte seule) et par la Bibliothèque (`library_db`, qui lisait le `ComicInfo.xml` sans limite — Audit 2, P4-1) ; `_check_member_size` reste dans `archive_loader.py`. | `test_archive_member_reading.py` (membre trop grand refusé avant lecture, ZIP et TAR) ; `test_7z_member_extraction.py` (2 nouveaux : membre au-delà de la limite refusé, erreur de lecture signalée) |
| **P3-2** | Non corrigé (choix assumé) | Limite de 500 millions de pixels gardée pour les grands scans ; le double décodage reste. | — |
| **P3-3** | Non corrigé | Un plafond risquerait de refuser des comics légitimes (gros PDF, longues animations). | — |
| **P3-4** | Corrigé | `_read_limited(response, max_bytes, cancel_flag)` : lecture par blocs, taille annoncée et reçue contrôlées, durée totale limitée (`_DOWNLOAD_DEADLINE_S` = 120 s), annulation prise en compte. Page HTML : 20 Mo ; image : 200 Mo (pas de nouvelle tentative si trop grande). Au plus 2 000 adresses par page (HTML et JS) ; boucle JS de plus de 5 000 tours ignorée ; au plus 200 boucles examinées ; expression `.src = …` de plus de 1 000 caractères ignorée avant la regex. Une image servie directement n'est plus lue par `_ResolveWorker` (elle était téléchargée deux fois). | `tests/test_web_import_limits.py` (nouveau, 8 tests : boucle normale résolue, boucle de 10¹² tours ignorée en moins de 2 s, expression de 1 Mo ignorée, nombre d'adresses plafonné, lecture limitée, réponse sans fin arrêtée, annulation). Manuel, validé (site de MosaicView, image directe) |
| **P3-5** | Non corrigé, puis corrigé (1.8.10, en deux temps) | Exige de déplacer ces appels dans un processus séparé, au-delà d'une correction ponctuelle. **Corrigé ensuite (1.8.10), pour les deux accès automatiques** (choix de l'utilisateur sur recommandation) : nouveau module sans Qt `pdf_probe.py` — `PdfProbe`, un processus `spawn` propre à chaque appelant (jamais partagé, comme le chargement PDF), lancé au premier PDF et gardé pour les suivants ; un processus mort ou sans réponse en 30 s est tué, la demande rend `None` et le suivant est relancé à la demande d'après. Le scan (`library_db._count_pages`, processus propre à chaque `LibraryDB`, arrêté par `close()`) et l'aperçu (`_PreviewWorker`, processus propre à la fenêtre, remplacé quand une lecture de couverture en cours est abandonnée, arrêté quand la fenêtre est cachée) n'appellent plus PyMuPDF dans l'application. `shutdown_pdf_probes` est branché sur `aboutToQuit` avant `stop_running_qthreads`, pour libérer aussitôt un scan ou un aperçu en attente. **Laissés dans l'application**, sous `PYMUPDF_LOCK` : couverture PDF de l'import de métadonnées en lot et déverrouillage (lancés par l'utilisateur, sur un fichier qu'il a choisi). Défaut trouvé par les tests : sous Windows, un processus arrêté paraît encore vivant un instant, et la demande suivante lui était envoyée ; `close()` marque désormais le processus à remplacer. **Complété ensuite (1.8.10), à la demande de l'utilisateur** : les deux accès laissés dans l'application passent aussi par `PdfProbe`. `PdfProbe.call` rend l'issue de la demande (réponse, erreur PyMuPDF avec son message, processus mort, délai dépassé, `PdfProbe` arrêté) et accepte une demande sans délai. Couverture de l'import de métadonnées en lot : commande `cover_jpeg`, processus propre au lot (`_BatchMetadataOrchestrator._pdf_probe`, arrêté à la fin du lot et remplacé quand un chargement de PDF est abandonné), mêmes messages d'erreur qu'avant (PDF vide, processus arrêté, sans réponse). Déverrouillage : commande `unlock`, sans délai (un gros PDF peut être long à réécrire), écrite dans un temporaire par `utils.replace_file_atomically` — un processus mort en pleine écriture ne laisse pas de copie tronquée ; l'erreur « processus arrêté » est traduite à l'affichage ; arrêté à la fermeture de l'application par `shutdown_pdf_probes`, il n'est plus un worker impossible à interrompre. Plus aucun appel PyMuPDF dans le processus de l'application : `pymupdf_lock.py` (P1-5 de l'Audit 4), devenu inutile après une recherche exhaustive dans le dépôt, est supprimé, et le garde-fou durci — `tests/test_check_pymupdf_separate_process.py` refuse toute utilisation d'un attribut de PyMuPDF (`fitz.open`, `fitz.Matrix`…) hors des fonctions exécutées dans un processus séparé (`_pdf_persistent_process`, `pdf_probe._probe_process`), au lieu d'exiger le verrou. | `test_pdf_probe.py` (nouveau, 8 tests : nombre de pages et couverture, PDF illisible, processus relancé, aucune lecture après l'arrêt, `_count_pages` sans processus, couverture JPEG, message d'erreur rendu, copie déverrouillée non chiffrée) ; `test_check_pymupdf_separate_process.py` (remplace `test_check_pymupdf_lock.py`, 2 tests). Manuel, validé (scan, aperçu, changement rapide de sélection, fermeture pendant un scan ; import en lot avec PDF, PDF passé pendant son chargement, faux PDF ; déverrouillage). |
| **P3-6** | Non corrigé, puis corrigé (1.8.10) | Fenêtre très courte (lecture d'un en-tête), risque faible. **Corrigé ensuite (1.8.10), à la demande de l'utilisateur** : `entries._header_size(data)` lit les dimensions dans l'en-tête en appelant directement le lecteur du format (`Image.OPEN`, formats de `Image.ID`, comme le fait `Image.open`), sans décoder l'image ni appliquer le contrôle de taille ; `Image.MAX_IMAGE_PIXELS` n'est plus jamais modifié à l'exécution. Raison affichée inchangée ; en-tête illisible : raison sans dimensions, comme avant. Le garde-fou des erreurs avalées dans une boucle déclare cette fonction (un format qui ne reconnaît pas les données laisse la place au suivant). Précision apportée en même temps sur P3-2 : commentaires de `MosaicView.py` et `create_entry` corrigés (refus par `Image.open`, au-delà du double de la valeur réglée, soit 1 milliard de pixels ; ils attribuaient l'erreur à `load()`). | `test_too_large_page_size.py` (nouveau, 3 tests : dimensions d'une image refusée, page marquée trop grande sans que la limite soit levée, données illisibles). Manuel, validé (CBZ de test : page de 40000×30000 avec cadre rouge et dimensions dans l'info-bulle). |
| **P3-7** | Corrigé | Seuls les dossiers nommés exactement comme MosaicView les nommait (`clipboard_<nombre>_<nombre>`) sont supprimés. | `test_temp_files_logs.py` (1 nouveau) |

## P4 — Maintainability / defense in depth

| # | Constat | Statut |
|---|---|---|
| **P4-1** | Aucune règle centrale pour afficher un texte externe ; aucun `Qt.PlainText` dans le projet avant P1-1 ; échappements au cas par cas | En partie : les affichages identifiés sont corrigés (P1-1). Reste à faire : une fonction commune et un garde-fou AST (texte externe dans un `QLabel` sans `PlainText`). **Complété ensuite (1.8.10)** : garde-fou AST `tests/test_check_plain_text_labels.py` (aucun `QLabel(str(...))` dans `modules/qt/` ; `Qt.PlainText` posé avant le premier `setText` dans les affichages connus de texte externe ; lien Web échappé avec `quote=True`). La fonction commune reste à faire. **Écartée ensuite (choix de l'utilisateur)** : une fonction `plain_label` ne ferait que remplacer une règle à retenir par une autre — un test ne peut pas savoir qu'un nouveau label affiche un texte externe ; la règle n° 3 de la section Sécurité de `CLAUDE.md` et le garde-fou suffisent. Seule alternative réellement plus sûre, jugée disproportionnée : imposer un format déclaré à tous les `QLabel` de l'application. |
| **P4-2** | `Image.open` appelé dans de nombreux modules, sans `formats=` | Réglé par P1-2 (restriction centrale au démarrage) |
| **P4-3** | La validation des noms de pages (`..`, absolus, caractères de contrôle) n'existe que dans la chaîne d'enregistrement du panneau | En partie sans objet (C-1) ; reste l'import de métadonnées en lot, qui recopie les noms tels quels dans le CBZ produit. **Corrigé ensuite (1.8.10)** : dans `_save_state_for_file`, branches de conversion CBR/CBT/CB7 seulement (la réécriture d'un CBZ existant garde les noms de l'archive de l'utilisateur), chaque nom passe par `archive_names.contained_member_name` (segments `..` et `.`, barre initiale, lettre de lecteur — `C:/x` comme `C:x` — et chemin réseau retirés, sous-dossiers conservés, nom ordinaire inchangé) puis `_converted_name`, qui le rend unique (C-9). Testé (`test_untrusted_names.py`, 11 cas ; `test_batch_metadata_save.py`). Manuel, validé (import en lot sur un CBR et un CBT habituels : mêmes pages, mêmes noms). |
| **P4-4** | Liste noire pour `os.startfile` | Réglé par P0-2 (liste blanche) |
| **P4-5** | Nom du canal d'instance unique prévisible (`MosaicView-SingleInstance-<USERNAME>`) : sur une machine partagée, un autre compte peut l'occuper en premier (déni de service, chemins ouverts divulgués) | Non corrigé (risque faible). **Examiné ensuite (1.8.10)** : correction jugée moyenne pour un gain très faible (suppose un autre compte malveillant connecté au même PC), sur un code exécuté à chaque démarrage. Solution retenue pour plus tard : avant d'envoyer le chemin, vérifier que le process serveur du canal (PID déjà obtenu par `GetNamedPipeServerProcessId`) appartient au compte courant (SID du jeton, par ctypes), sinon démarrer normalement. Écartée : nom de canal secret rangé dans le profil (plusieurs lancements simultanés depuis l'Explorateur créeraient chacun un code différent). Reporté par l'utilisateur. |
| **P4-6** | Noms de membres écrits tels quels dans les journaux de lot (un retour à la ligne crée de fausses lignes) ; le `netloc` d'une URL importée (`user:mdp@hôte:port`) peut devenir un nom de page enregistré dans le CBZ | Non corrigé (risque faible). **Corrigé ensuite (1.8.10), à la demande de l'utilisateur.** Constat précisé : un nom de page piégé est déjà signalé à l'enregistrement depuis un panneau (`_validate_filenames_qt` refuse les caractères de contrôle) et nettoyé à toute écriture sur le disque (`windows_safe_component`) ; restaient les CBZ produits par conversion et les journaux. Nettoyer les noms à l'ouverture a été écarté (renommage silencieux de toutes les pages, alors que l'enregistrement le signale déjà). Corrections : `archive_names.without_control_chars` (caractère de contrôle → `_`), appliquée par `batch_dialogs_qt._flat_names` (lots CBR, CB7, CBT) et `archive_names.contained_member_name` (import de métadonnées en lot) ; `temp_files.log_text` (caractère de contrôle écrit sous forme échappée, `\n`) pour chaque ligne variable des journaux des lots et de l'import de métadonnées. Adresse importée : `web_import_qt.page_title_from_url` ne garde que le nom d'hôte (sans identifiant, mot de passe ni port ; effet visible : nom du site en minuscules). Testé (`test_untrusted_names.py`, 2 cas ; `test_batch_unique_flat_name.py`, 1 ; `test_temp_files_logs.py`, 1 ; `test_web_import_limits.py`, 1). |

---

## Constats ajoutés pendant les corrections

| # | Constat | Suite |
|---|---|---|
| **C-1** | Les lots CBR, CB7 et CBT aplatissent déjà les noms (`batch_dialogs_qt._flat_names` : segments joints par `_`, donc `../x.jpg` → `.._x.jpg`, sans séparateur). P4-3 ne concerne donc que l'import de métadonnées en lot. | Aucune correction nécessaire |
| **C-2** | P2-3 : le repli de `get_absolute_path`, présenté dans le rapport initial comme une validation sans effet, sert aux répertoires additionnels (voir la fiche). Le test existant `test_library_reindex.py` documentait ce cas (chemins en `../…`). | Correction ajustée (P2-3) |
| **C-3** | `import_merge_qt.py` (ajout d'une archive à un comic ouvert) : dans `_load_zip` et `_load_rar`, une erreur de lecture d'un membre n'est pas rattrapée, alors que le chargeur principal en fait une page illisible ; l'effet exact sur l'ajout (abandon complet ou partiel) n'a pas été vérifié. Seule l'erreur de taille de P3-1 y est rattrapée. | **Examiné puis corrigé (1.8.10)**. Effet constaté à la lecture du code : une erreur CRC d'un membre ZIP lève `zipfile.BadZipFile`, rattrapée par `ImportMergeWorker.run` comme « le CBZ est en fait un RAR » — MosaicView proposait alors de **renommer l'archive** de l'utilisateur en `.cbr` (puis, la lecture RAR échouant, en `.cbz`) ; même chose dans l'autre sens pour un CBR (`BadRarFile`) ; toute autre erreur (compression non prise en charge, membre chiffré) abandonnait tout l'ajout (« échec de l'import ») ; CB7 et CBT ajoutaient bien une page illisible, mais sans raison, et un fichier non-image illisible n'était signalé nulle part. Aucune perte (comic inchangé, enregistrement refusant une entrée sans données). Correction : `_read(reader, file)` (lecture d'un membre, erreur rendue au lieu d'être levée) et `_note_read_error` pour les quatre formats : page illisible avec sa raison (`corruption_reason`), autre fichier listé ; le signal `finished` transporte la liste des erreurs, affichée dans « Fichiers ignorés ». Le dialogue de correction d'extension ne reste que pour une archive entière d'un autre format. Validé dans l'application (CBZ de test : une page saine, une page au CRC faux, un `.txt` au CRC faux, ajoutés à un comic ouvert). |
| **C-4** | Relevé par l'utilisateur à l'ouverture du même CBZ de test : deux fenêtres superposées à la fin du chargement, « Images corrompues » (pages illisibles) et « Fichiers ignorés » (autres fichiers illisibles), centrées toutes deux sur le panneau (`ArchiveLoader._on_finished`). | Corrigé (1.8.10), choix de l'utilisateur : fenêtre « Images corrompues » supprimée (`_CorruptedImagesDialog`, `_detect_corrupted_qt`), une page illisible étant déjà signalée par son cadre rouge, son icône et son info-bulle ; ses 4 clés de traduction (`messages.warnings.corrupted_images.*`) retirées des 46 langues. Validé dans l'application. |
| **C-5** | Relevé par l'utilisateur : la raison affichée d'une page ou d'un fichier illisible est le message brut de l'exception, en anglais (« Bad CRC-32 for file 'notes.txt' »), ou un texte français codé en dur dans `entries.create_entry` (« Fichier image vide », « Dimensions invalides »). | Corrigé (1.8.10) : nouveau module `read_errors.py` — `read_error_text(raw)` reconnaît les causes courantes (données abîmées, mot de passe, compression non prise en charge, taille au-delà de la limite, fichier incomplet, format non reconnu, fichier vide, dimensions invalides ; sinon « illisible (message technique) ») ; `files_ignored_line` reformule les lignes de « Fichiers ignorés » qui désignent un membre illisible (`unreadable_member_names`), sans toucher aux messages déjà traduits. Message brut stocké, traduit à l'affichage (info-bulle, fenêtre) : il suit un changement de langue. 10 clés `messages.read_errors.*` dans les 46 langues, vérifiées (skill `verify-translation`, mode par clés : 23 corrections de cohérence terminologique dans 17 langues). Validé dans l'application. |
| **C-6** | Suite de la limite connue de P0-2 : le message « les fichiers de type {ext} peuvent exécuter du code » est faux pour un type simplement inconnu, et donne « type README » pour un fichier sans extension. | Corrigé (1.8.10) : nouvelle clé `messages.warnings.file_open_blocked` (titre repris tel qu'il était traduit, message « Ce fichier n'a pas été ouvert : par sécurité, MosaicView n'ouvre que certains types de fichiers. », choisi par l'utilisateur), à la place de `executable_file_open` ; `_warn_blocked_file(parent)` ne cite plus le type. Traduite et vérifiée dans les 46 langues (1 correction : vietnamien). Validé dans l'application (CBZ de test : `.txt` ouvert, `.html` et `test.bat.` refusés). |
| **C-7** | Relevé en vérifiant les traductions de C-5 : en sindarin, « protégé » était traduit par `garthannen`, absent d'Eldamo ; le contrôle lexical l'acceptait comme forme mutée de `carthannen`, « fait, fabriqué » (« PDF fabriqué avec un mot secret »). | Corrigé : `barn` (« sûr, protégé », néologisme recensé, Fiona Jallings) dans les 12 valeurs concernées de `sjn.json` (messages de PDF protégé, déverrouillage de PDF, aide), pluriel `bern` ; `garthannen` noté « à ne pas employer » dans le lexique. Mots employés pour C-5, C-6 et C-7 ajoutés aux trois lexiques (et formes construites du quenya à `vocab_qya.tsv`). |
| **C-8** | Relevé en examinant P4-3 : les branches de conversion CBR et CBT de l'import de métadonnées en lot (`_BatchMetadataOrchestrator._save_state_for_file`) lisent les membres directement (`rf.read(info)`, `tf.extractfile(m).read()`), sans la limite de taille de P3-1 : un membre de plusieurs Go y serait lu en entier en mémoire (même cause que P3-1 : un chemin de lecture qui ne passe pas par les lecteurs communs). La branche CB7 était déjà couverte par `_read_7z_file`. | Corrigé (1.8.10) : `_check_member_size(info.file_size)` avant `rf.read(info)` (CBR) et `_tar_info_read` (CBT). Un membre trop grand fait échouer l'écriture de ce fichier : compté en erreur dans le résumé du lot, original gardé, temporaire supprimé. Testé (`test_cbt_member_too_large_fails_and_keeps_original`). |
| **C-9** | Relevé en examinant P4-3 : dans les mêmes branches, deux membres de même nom dans la source (ou devenus identiques après nettoyage) produisaient deux entrées ZIP de même nom ; seule la dernière est lisible par nom, et la source est supprimée ensuite — même famille que l'Audit 2, P1-3 (homonymes ZIP). | Corrigé (1.8.10) : `_converted_name` rend chaque nom unique (casse ignorée, suffixe `_2`, `_3`… avant l'extension, comme `utils.cbz_members`), y compris par rapport aux fichiers écrits par `_write_xml_only`. Testé (`test_cbt_homonyms_get_distinct_names`). |
| **C-10** | Antérieur à l'audit, sans rapport avec la sécurité. Relevé par l'utilisateur pendant le test manuel de P3-5 : dans la colonne d'aperçu de la Bibliothèque, l'espacement entre les métadonnées (série, titre, dessinateur…) changeait d'un comic à l'autre. Diagnostic par prints : le contenu de la zone défilante est étiré à toute la hauteur visible (`widgetResizable`), et les valeurs, en politique verticale `MinimumExpanding`, se partageaient la place libre — lignes de 91 pixels au lieu de 22 pour un comic à 12 champs, 44 pour un comic à 17 champs. Un comic n'influençait pas l'affichage d'un autre : l'espacement dépendait du nombre de champs du comic affiché. | Corrigé (1.8.10), à la demande de l'utilisateur : dans `LibraryWindow._populate_meta`, valeurs en politique verticale `Preferred` (hauteur donnée par `heightForWidth`) et ressort final (`addStretch(1)`) qui prend la place restante. Relevés après correction : chaque ligne a la hauteur de son texte, valeurs sur plusieurs lignes et long résumé affichés en entier. Validé dans l'application. |
| **C-11** | Relevé en déplaçant la couverture PDF de l'import de métadonnées en lot (P3-5) : dans `_load_state_for_file` et `_extract_non_cbz`, la couverture d'un CBR est lue par son nom (`rf.read(nom)`) sans contrôle de taille, celle d'un CBT sans contrôle de taille, et les quatre formats choisissent la couverture dans une liste d'extensions écrite sur place, plus courte que `IMAGE_EXTS` ; dans la branche CBZ, ComicInfo.xml est lu par son nom (dernier homonyme) et sans contrôle de taille. Même famille que P3-1 et l'Audit 2, P1-3 : un chemin de lecture qui ne passe pas par les lecteurs communs. | Corrigé (1.8.10) : couverture CBR par `_rar_read`, CBT par `_tar_info_read` ; couverture et ComicInfo.xml d'un CBZ par `_read_first` (premier membre de ce nom par son `ZipInfo`, celui que l'enregistrement remplace, taille contrôlée) ; couverture choisie dans `IMAGE_EXTS`, comme dans la mosaïque (effet visible : une première page `.jfif` ou `.ico` devient la couverture). Sans test dédié (code de chargement d'un worker Qt) ; pas encore testé dans l'application. |
| **C-12** | Relevé en corrigeant C-11 : à la réécriture d'un CBZ par l'import de métadonnées en lot (`_save_state_for_file`), chaque autre fichier de l'original est recopié par `src.read(info)` sans contrôle de taille : un membre de plusieurs Go serait lu entièrement en mémoire. | Corrigé (1.8.10) : `_check_member_size(info.file_size)` avant chaque recopie ; un membre trop grand fait échouer l'écriture de ce comic (compté en erreur, original intact, temporaire supprimé). Testé (`test_cbz_member_too_large_fails_and_keeps_original`). |
| **C-13** | Antérieur à l'audit, sans rapport avec la sécurité. Relevé par l'utilisateur : après un glisser-déposer de toutes les pages d'un comic vers l'autre panneau (ou leur suppression, ou l'ouverture d'un CBZ vide), le panneau affiche le texte d'accueil de `MosaicCanvas.render_mosaic`, identique à celui d'un panneau sans document, alors que le comic reste ouvert (onglet présent, enregistrement possible d'un CBZ vide). En outre, « Enregistrer sous » était sans effet sur un tel comic (`save_as_cbz` s'arrêtait sur `not state.images_data`, sans message), alors qu'« Enregistrer » écrivait le CBZ vide sans prévenir. | Corrigé (1.8.10), conception choisie par l'utilisateur : critère unique `AppState.document_is_empty()` (fichier ouvert, `images_data` vide — ComicInfo.xml y est rangé comme les autres fichiers) ; texte rouge du mécanisme commun (`canvas_overlay_qt`) à la place du texte d'accueil, mis à jour par `MosaicCanvas._update_empty_comic_label` à chaque reconstruction de la mosaïque, masqué pendant un chargement (`hold_empty_hint`, posé aussi par les fusions d'archive et de PDF) et pendant l'ouverture d'une archive ou d'un PDF (`_replacing_load_check`). Question non modale avant d'écrire un CBZ vide (`_confirm_empty_document_qt`, première étape de la validation de `save_as_cbz` et `apply_new_names`), qui ne bloque plus « Enregistrer sous ». 4 clés traduites dans les 46 langues et vérifiées (skill `verify-translation`, mode par clés : 3 corrections de cohérence terminologique). Validé dans l'application. |
| **C-14** | Relevé par l'utilisateur en testant C-13, antérieur à l'audit : dans `PanelWidget._on_inter_panel_drop`, le panneau qui perd son ComicInfo.xml garde ses métadonnées analysées (`comic_metadata`) et son onglet Métadonnées, qui ne disparaissait qu'après Annuler puis Refaire ; le panneau qui reçoit un ComicInfo.xml sans avoir de métadonnées ne les lisait pas. Confirmé par prints (source : ComicInfo.xml absent, `comic_metadata` présent ; cible : l'inverse). | Corrigé (1.8.10) : métadonnées retirées et onglets rafraîchis dans la source ; métadonnées lues dans la cible (`_reload_comicinfo_metadata`) avant la synchronisation des `<Pages>`. Validé dans l'application. |
| **C-15** | Relevé par l'utilisateur en testant C-14 : un panneau d'images sans fichier ouvert qui reçoit un ComicInfo.xml affiche l'onglet Métadonnées sans onglet mosaïque (`TabBar._rebuild` ne crée l'onglet mosaïque que si `current_file` est défini) : une fois l'onglet Métadonnées affiché, impossible de revenir à la mosaïque. Cas voisin documenté par le skill `tabs` : métadonnées disparues pendant que leur onglet est affiché, bouton retiré mais contenu resté sur l'onglet. | Corrigé (1.8.10), choix de l'utilisateur : onglet mosaïque présent dès qu'il y a un document, intitulé « BD sans titre » sans fichier ouvert (clé `tabs.untitled_comic`), avec sa croix de fermeture ; retour automatique à la mosaïque quand l'onglet Métadonnées affiché n'a plus de métadonnées ; présence des onglets resynchronisée après chaque reconstruction de la mosaïque (`PanelWidget._sync_tabs_with_document`). Validé dans l'application. |
| **C-16** | Relevé en corrigeant C-14 : glisser vers un comic qui a déjà son ComicInfo.xml les pages d'un comic qui a le sien (ComicInfo.xml compris) donne un comic contenant deux ComicInfo.xml. Antérieur à l'audit. Examen : même résultat par l'ajout d'une archive (`NEW01-ComicInfo.xml`), le dépôt ou le collage d'un fichier ComicInfo.xml, et l'aplatissement des répertoires (`Bonus/ComicInfo.xml` ramené à la racine, qui prenait la place du vrai s'il le précédait dans la liste, ses métadonnées remplaçant alors celles du comic). Cause commune : un ComicInfo.xml était reconnu par `endswith("comicinfo.xml")`, et le premier de la liste comptait ; les lecteurs de BD ne lisent que `ComicInfo.xml` à la racine. Constaté aussi : l'import de métadonnées en lot écartait, en convertissant un CBR/CB7/CBT supprimé ensuite, tout membre nommé ComicInfo.xml, dossiers compris (fichier perdu). | Corrigé (1.8.10), règle choisie par l'utilisateur : le ComicInfo.xml d'un comic est le fichier nommé exactement `ComicInfo.xml` à sa racine, casse ignorée (`comic_info.is_comic_info_name`/`comic_info_entry`, partout dans le document, la Bibliothèque, l'import en lot et `read_comic_info`) ; un ComicInfo.xml qui arrive dans un comic qui en a déjà un est renommé `ComicInfo_0002.xml`, `_0003`… (`name_incoming_comic_infos` : ouverture, ajout d'archive, dépôt et collage, glisser-déposer entre panneaux, aplatissement — `flatten_entries`) ; métadonnées alignées sur ce fichier, y compris après un renommage (`sync_comic_metadata`) ; renommage d'un fichier en `ComicInfo.xml` refusé quand le comic a déjà le sien (`NameEdit._refuse_second_comic_info` : ancien nom remis, message `comicinfo.error_duplicate`, clé existante) ; `ComicInfo_0002.xml` ouvert dans l'application par défaut ; import en lot : seul l'ancien ComicInfo.xml de la racine est remplacé, un second à la racine recopié en `ComicInfo_0002.xml`, celui d'un dossier recopié tel quel. 28 tests ajoutés (`test_comic_info.py`, `test_batch_metadata_save.py`, `test_flatten_entries.py`). Validé dans l'application (CBZ et CBT de test : ComicInfo.xml à la racine et dans `Bonus/`). |

## Attack surfaces audited with no issue found

- **Zip Slip et chemins de traversée** : aucune archive n'est extraite sur le disque ; tous les noms venus d'une archive passent par `safe_join` avant toute écriture (export, presse-papiers, ouverture avec l'application par défaut), ou par `basename` (glisser-déposer).
- **Liens symboliques et physiques d'archive** : TAR ignorés (`isfile()`) ; une entrée ZIP « lien Unix » n'est jamais matérialisée.
- **Sous-processus** : 7z en liste d'arguments, `--` avant les noms, et un nom `@fichier` n'est pas lu comme une liste après `--` (vérifié) ; `rarfile` place `--` et contrôle le CRC du membre lu, ce qui transforme une éventuelle concaténation par joker (archives solides seulement) en erreur de lecture plutôt qu'en mauvaise page ; `explorer` et `notepad` reçoivent des chemins internes ou de la base.
- **XML** : `defusedxml` embarqué (vérifié dans les deux `.spec`) ; OPF des EPUB analysé par expressions régulières linéaires.
- **URLs** : `open_url` refuse tout schéma autre que http/https (y compris après les espaces de tête que `urlsplit` retire) ; drop, `.url`, `.webloc` et `<img>` filtrés en http(s) ; redirection vers `file://` refusée par `urllib` ; URL d'images ComicVine filtrées (`_parse_image_url`) ; identifiants ComicVine extraits par expressions régulières numériques ; clé API masquée.
- **PDF** : processus `spawn` isolé, mort détectée (`is_alive`, `poll`), DPI limité à 2 400, mots de passe gérés.
- **ZIP** : entrées qui se chevauchent rejetées par Python 3.11.9 ; homonymes lus par `ZipInfo` ; type d'archive détecté par signature ; extension d'image corrigée d'après le vrai format.
- **Canal d'instance unique** : message limité à 128 Ko, bannière d'échange.
- **Temporaires d'enregistrement** : `mkstemp` dans le dossier de destination (nom imprévisible), nettoyage des orphelins (`make_temp_path`).
- **Info-bulles et barre de statut** : texte échappé (`html.escape`).

**Fausses pistes écartées** : requêtes vers l'intranet par les `<img>` d'une page importée (ce que fait aussi un navigateur) ; `explorer /select,` avec des virgules dans un chemin (scénario irréaliste) ; liens vers les journaux de lot (chemins internes) ; ralentissement de l'analyse de l'OPF (motifs linéaires) ; repli de `get_absolute_path` (C-2).

## Input validation map

| Source | Point d'entrée | Validation (après corrections) | Usage final |
|---|---|---|---|
| Noms de membres ZIP/RAR/7z/TAR | `LoadWorker`, `import_merge_qt`, lots | Codage (`open_zip`), extension réelle, unicité à l'écriture, `-spd` (7z), homonymes TAR, noms Windows (`windows_safe_relpath`) avant écriture sur disque, caractères de contrôle remplacés dans les CBZ de conversion (`without_control_chars`) et échappés dans les journaux (`log_text`) | Nom de page ; fichiers temporaires et exports (`safe_join`) ; argument de 7z ; affichage en texte brut |
| Contenu des membres | `create_entry`, lots | Taille annoncée ≤ 500 Mo (CB7 : taille reçue), formats Pillow restreints, `verify`/`load`, limite de pixels | Décodeurs Pillow, CBZ |
| ComicInfo.xml | `comic_info.read_comic_info` | `defusedxml`, attributs `<Page>` convertis sans exception | Onglet Métadonnées et Bibliothèque (texte brut), `open_url` |
| PDF | processus isolé pour tous les usages (chargement, lots, Bibliothèque, import de métadonnées en lot, déverrouillage) | Mot de passe, DPI ≤ 2 400 | Pages (nombre non limité) |
| Page Web, `.url`, `.webloc`, drop de lien | `web_import_qt` | Schéma http(s), tailles, durée, nombre d'adresses, boucles JS ; nom de site sans identifiant ni mot de passe (`page_title_from_url`) | `urlopen`, noms de pages |
| API ComicVine | `comicvine_scraper` | Schéma des images, `_strip_html` (balises retirées après décodage), identifiants numériques | ComicInfo.xml, affichage |
| Fichiers non-image d'une archive | `open_file_with_default_app` | Liste blanche sur le nom écrit | `os.startfile` |
| Base `.mvdb` | `library_db` | Chemin dans un répertoire de la base | Ouverture, conversion, `os.startfile` |
| JSON des macros | `macro_engine.load_macro` | Structure ; nom pris dans le nom de fichier | Chemin de fichier (suppression, renommage) |
| Dossiers choisis ou déposés | `drop_handler_qt`, lots, Bibliothèque | `walk_without_links` | Traitements, suppression des sources |
| Canal local | `single_instance_qt` | Taille ≤ 128 Ko | Ouverture d'un fichier |

**Dépendances tierces** : Pillow 12.3.0 (décodeurs restreints à 9 formats ; EPS → Ghostscript écarté) ; PyMuPDF 1.28.2 (isolé pour le chargement principal, puis pour tous ses usages en 1.8.10) ; `zipfile` et `tarfile` (bibliothèque standard de Python 3.11.9) ; rarfile 4.3 et `unrar.exe` ; `7z.exe` ; `urllib` et `requests` (délais présents ; tailles limitées pour l'import Web, pas pour ComicVine) ; `defusedxml` ; `lxml.html` (pages Web seulement) ; Qt `QLabel` (`AutoText` et `loadResource`, comportement confirmé dans l'application).

## Missing security tests

**Ajoutés** (suite passée de 696 à 773 tests, puis à 833 avec les compléments de la version 1.8.10) :

| Fichier | Couvre |
|---|---|
| `test_7z_member_extraction.py` (1 nouveau) | P0-1 : noms à jokers lus littéralement |
| `test_comicvine_strip_html.py` (nouveau, 5 tests) | P1-1 : balises écrites en entités jamais recréées, comparaisons gardées |
| `test_untrusted_names.py` (nouveau, 44 tests) | P0-2 (liste blanche jugée sur le nom écrit), P2-2 (noms Windows, unicité), P2-1 (attributs `<Page>`), P2-4 (jonction NTFS) |
| `test_pillow_formats.py` (nouveau, 11 tests) | P1-2 : formats gérés ouverts, EPS et PPM refusés, Ghostscript jamais appelé |
| `test_library_db.py` (2 nouveaux) | P2-3 : répertoire additionnel accessible, chemins hors des répertoires refusés |
| `test_macro_engine.py` (2 nouveaux) | P2-5 : nom pris dans le fichier, suppression du seul bon fichier |
| `test_archive_member_reading.py` (3 nouveaux, chargement AST adapté) | P2-6 (homonymes TAR, lien homonyme), P3-1 (membre trop grand refusé avant lecture) |
| `test_web_import_limits.py` (nouveau, 8 tests) | P3-4 |
| `test_temp_files_logs.py` (1 nouveau) | P3-7 |
| `test_7z_member_extraction.py` (2 nouveaux, 1.8.10) | P3-1 pour le CB7 : membre au-delà de la limite refusé, erreur de lecture signalée |
| `test_check_plain_text_labels.py` (nouveau, 5 tests, 1.8.10) | P1-1 et P4-1 : garde-fou AST (aucun `QLabel(str(...))`, `PlainText` avant `setText` dans les affichages connus de texte externe, lien Web échappé) |
| `test_read_errors.py` (nouveau, 22 tests, 1.8.10) | C-5 : raison choisie pour 13 messages courants, message inconnu gardé et raccourci, noms des membres illisibles (pages d'EPUB comprises), seules les erreurs de membre reformulées dans « Fichiers ignorés » (fonction de traduction remplacée : clés vérifiées, indépendamment de la langue) |
| `test_untrusted_names.py` (11 nouveaux, 1.8.10) | P4-3 : `contained_member_name` |
| `test_batch_metadata_save.py` (3 nouveaux, 1.8.10) | P4-3, C-8, C-9 : noms piégés ramenés dans le dossier, homonymes distincts, membre trop grand refusé et original gardé ; module de remplacement d'`archive_loader` (vraies fonctions de taille extraites par AST) pour rester sans Qt |
| `test_pdf_probe.py` (nouveau, 8 tests, 1.8.10) | P3-5 : lectures dans un processus séparé, PDF illisible, processus relancé après sa mort, arrêt définitif, couverture JPEG de l'import en lot, message d'erreur rendu, copie déverrouillée non chiffrée |
| `test_check_pymupdf_separate_process.py` (nouveau, 2 tests, 1.8.10, remplace `test_check_pymupdf_lock.py`) | P3-5 : aucune utilisation de PyMuPDF hors des fonctions exécutées dans un processus séparé |
| `test_untrusted_names.py`, `test_batch_unique_flat_name.py`, `test_temp_files_logs.py`, `test_web_import_limits.py` (5 nouveaux, 1.8.10) | P4-6 : caractères de contrôle remplacés dans les noms des conversions, une ligne de journal par erreur, nom de site sans identifiant ni mot de passe |
| `test_batch_metadata_save.py` (1 nouveau, 1.8.10) | C-12 : membre recopié trop grand refusé, CBZ d'origine intact |
| `test_too_large_page_size.py` (nouveau, 3 tests, 1.8.10) | P3-6 : dimensions d'une page trop grande lues sans lever la limite de pixels |

**Toujours manquants** :

- P1-1 : l'affichage lui-même dépend de Qt (seul le code est contrôlé, par le garde-fou AST) ;
- P2-2 : glisser-déposer et presse-papiers (dépendent de Qt et de l'API du presse-papiers ; seules les fonctions de noms sont testées) ;
- C-3 : erreur de lecture d'un membre pendant l'ajout d'une archive — la logique est dans le thread du worker Qt (`ImportMergeWorker.run`) ; validé dans l'application seulement ;
- C-8, C-9 pour le CBR : `rarfile` ne sait pas écrire d'archive RAR de test (seule la branche CBT est testée, même code de nommage et de contrôle) ;
- noms invalides venus d'une archive écrits par l'export vers un dossier (fonction imbriquée dans une fenêtre Qt) ;
- C-11 : choix et lecture de la couverture dans l'import de métadonnées en lot (code de chargement d'un worker Qt).

L'utilisateur a choisi de ne pas ajouter ces tests : ils demanderaient Qt, une archive RAR de test, ou de sortir du code des fenêtres pour un gain faible, alors que ces cas sont validés dans l'application.

**Fuzzing utile** (sans charge dangereuse) :

| Cible | Invariant |
|---|---|
| `safe_join`, `windows_safe_relpath` sur des noms générés | résultat sous le dossier de base, segments valides sous Windows |
| `create_entry` sur des octets mutés | jamais d'exception ; `is_corrupted` cohérent |
| `parse_comic_info_xml` et `_page_attr_int` | jamais d'exception |
| `extract_images_from_js_loops` | durée bornée, nombre d'adresses ≤ limite |
| `_strip_html` | jamais de balise en sortie |
| `_read_7z_file` sur des noms générés | octets exacts du membre demandé |
| `openable_temp_name` | aucune extension exécutable acceptée |
| `epub_reading_order`, `tar_entry_name`, `zip_member_name` | jamais d'exception ; ordre ⊆ images |

Non fait : retenu par l'utilisateur pour une session dédiée (bibliothèque Hypothesis à ajouter aux dépendances de test).

## Recommended fixes

Les deux P0, les deux P1, les sept P2, P3-1 (sauf CB7), P3-4 et P3-7 ont été corrigés à la demande de l'utilisateur, P0-1 et P1-1 étape par étape, le reste d'une traite ; suite complète passée (773 tests) ; validés dans l'application (sauf P1-2, P2-1, P2-4 et P3-7 : tests automatiques seulement ; P2-7 : ni test automatique ni test manuel dédié). Entrées ajoutées à la section [1.8.9] du CHANGELOG. Restent :

1. ~~**Skills à mettre à jour**~~ — faits : 18 skills et `CLAUDE.md` mis à jour (entrée [1.8.10] du CHANGELOG), puis de nouveau après les compléments de la version 1.8.10.
2. ~~**Message de refus de « Lire avec l'application par défaut »**~~ — fait (C-6).
3. ~~**P3-1 pour le CB7**~~ — fait, par une limite sur la taille reçue plutôt que sur la taille annoncée (voir la fiche).
4. ~~**C-3**~~ — examiné et corrigé, avec C-4 et C-5 relevés pendant son test.
5. ~~**P3-5**~~ — fait, en deux temps : scan et aperçu de la Bibliothèque, puis import de métadonnées en lot et déverrouillage ; verrou `PYMUPDF_LOCK` supprimé (voir la fiche).
6. ~~**P4-1**~~ — garde-fou AST fait ; fonction commune écartée (choix de l'utilisateur, voir la fiche).
7. ~~**Test de C-5**~~ — fait (`test_read_errors.py`).
8. ~~**P4-3**~~ — fait, avec C-8 et C-9 relevés en l'examinant.
9. ~~**P4-6**~~ — fait (noms des conversions, journaux, nom de site de l'import Web), avec C-11 et C-12 relevés au passage.
10. ~~**P3-6**~~ — fait (dimensions lues dans l'en-tête, limite jamais levée).
11. **Non corrigés, par choix** : P3-2, P3-3 (raisons dans les fiches). **Reporté** : P4-5 (solution retenue décrite dans la fiche). **Tests automatiques manquants** : non ajoutés, choix de l'utilisateur. **Fuzzing** : reporté à une session dédiée.

Compléments de la version 1.8.10 : suite complète passée (833 tests) ; validés dans l'application (CB7 : tests automatiques seulement). Entrées ajoutées à la section [1.8.10] du CHANGELOG.

---

# Audit 7 — Gestion des erreurs, des annulations et des retours utilisateur (version 1.8.11)

- **Période** : octobre 2026 (audit, puis premières corrections intégrées à la version 1.8.12, pas encore publiée au moment des corrections).
- **Objet** : suivre le cycle complet erreur → détection → propagation → récupération → état final → information de l'utilisateur, et annulation → arrêt → nettoyage → état final → information ; rechercher les erreurs avalées, les erreurs présentées comme des succès (et l'inverse), les messages faux ou incomplets, les opérations partielles, les états « occupé » qui restent posés, les annulations qui n'annulent pas, les temporaires laissés, les reprises impossibles. Distinct des audits 1 (interactions), 2 (opérations destructives), 3 (machines à états), 4 (asynchronisme), 5 (préservation des formats) et 6 (sécurité).
- **Méthode** : lecture du code des chemins d'erreur et d'annulation (enregistrement depuis un panneau et remplacement atomique, traitements par lot, import de métadonnées en lot, opérations en arrière-plan, chargeurs d'archives et de PDF, Bibliothèque, presse-papiers, macros, configuration, journal des erreurs) ; lecture de ce fichier (audits 1 à 6) et des tests. Rapport initial présenté avant toute modification ; puis, à la demande de l'utilisateur, **vérification de la réalité de chaque P0 et P1** par des essais sans Qt : appels réels de `ReplaceFileW` sur des fichiers jetables (disque local NTFS et partage SMB `\\localhost\C$`, 6 000 courses de verrous), comportement du canal `multiprocessing` à la mort du process enfant, et fonctions réelles du dépôt extraites par AST (`cut_selected`, `_save_state_for_file`, méthodes de `_BatchMetadataOrchestrator`, `load_multiple_archives`) avec de faux presse-papiers, workers et fenêtres. Ces vérifications ont fait reclasser quatre des six constats P0/P1 (voir les fiches).
- **Code de référence** : MosaicView 1.8.11 (dépôt au commit `88f3db2`, qui contient les corrections des audits 1 à 6 ; la demande d'audit parlait de 1.8.7). Python 3.11.9, Pillow 12.3.0, PySide6.

## Executive summary

| Priorité | Constats initiaux | Après vérification | Certitude | Statut |
|---|---|---|---|---|
| **P0** — intégrité des données après erreur | 2 | 0 (les deux reclassés en P3) | — | — |
| **P1** — état incorrect après erreur | 4 | 2 (P1-3 → P4, P1-4 → P2) | 2 Confirmed (reproduits) | 2 corrigés et validés (tests automatiques et tests dans l'application) |
| **P2** — erreurs visibles par l'utilisateur | 8 | 9 | 9 Confirmed (P2-1, P2-2, P2-3, P2-5, P2-7, P2-9 reproduits ; P2-4, P2-6, P2-8 par le code) | 9 corrigés d'une traite (tests automatiques pour P2-1, P2-3, P2-5, P2-7, P2-9). Validés dans l'application ceux qui se provoquent à la main : P2-3 (test 5), P2-7 (test 6), P2-8 (tests 9 et 17). Les autres ne se provoquent pas à la main : couverts par les tests automatiques ou vérifiés à la lecture du code |
| **P3** — récupération, robustesse | 6 | 8 | 6 Confirmed (P3-3 à P3-7 reproduits, P3-8 par le code), 2 défenses (P3-1 non reproduit, P3-2 manque de contrôle) | 8 corrigés d'une traite (tests automatiques pour P3-1, P3-3 à P3-6, P3-8 et en partie P3-2 ; aucun pour P3-7). Validés dans l'application : P3-6 (tests 7 et 8), P3-7 (test 9), P3-8 (test 16) ; les autres ne se provoquent pas à la main |
| **P4** — maintenabilité | 4 | 5 | 3 réels vérifiés (P4-1, P4-4 ; P4-3 réduit à un garde-fou manquant) | P4-5, P4-2, P4-4 et P4-3 corrigés (tests automatiques ; P4-5 et P4-2 sans régression dans l'application, test manuel 11 ; P4-4 validé, test manuel 12) ; P4-1 non traité |
| **Constats ajoutés pendant les corrections** | 16 | — | — | Tous traités ; ceux qui se provoquent à la main validés dans l'application (dont C-13, C-15 et C-16) |

**Catégories d'erreurs analysées** : I/O, permissions et lecture seule, fichier verrouillé, disque plein, fichier disparu, archive ou membre corrompu, PDF (mort du process), réseau et ComicVine, presse-papiers Windows, écriture de la configuration, exception d'un worker, annulation, fermeture, validation.

**Opérations les plus sensibles** : Couper (Ctrl+X) avec une page illisible dans la sélection ; annulation de l'import de métadonnées en lot ; lot PDF → CBZ quand le process MuPDF plante ; réécritures sur place d'un CBZ existant.

**Résultats trompeurs pour l'utilisateur** : un CBZ correct annoncé abîmé (P2-2) ; un fichier annoncé non traité alors que son original avait été supprimé (P1-2) ; des échecs sans aucun message (P2-3, P2-4, P2-5, P2-7) ; un export partiel présenté comme un simple échec (P2-6).

**Solidité constatée** : chaîne d'enregistrement depuis un panneau (temporaire, contrôle de structure, verrou d'opération, `_DocumentChangedDuringSave`, `content_unchanged`) ; lots CBR/CB7/CBT/images (source supprimée seulement après relecture, annulation jamais entre l'écriture et la suppression, résumé seulement après l'arrêt réel du thread) ; annulation de la rotation, du redimensionnement et de la conversion (retour arrière après l'arrêt réel du worker) ; macros (`finally`, rollback) ; remplacement atomique face aux verrous (voir l'ancien P0-1).

### Causes communes

| Cause | Constats concernés |
|---|---|
| **Réussite partielle annoncée comme réussite** : une fonction saute un élément sans le dire et rend `True` | P1-1 (même famille que l'Audit 1, P4-4, et l'Audit 2, P0-5 : page sans données traitée comme absente) |
| **Demande d'annulation prise pour l'arrêt réel** | P1-2 |
| **Échec rendu par `False` ou `None` sans message** | P2-3, P2-4, P2-5, P2-7, P3-4 |
| **Contrôle avant remplacement intégré à un seul écrivain** | P3-2 (ancien P0-2 ; même famille que l'Audit 1, P4-1, et l'Audit 5, C-14) |
| **Lecteurs du canal PDF qui ne gèrent pas tous la mort du process** | P2-1 (→ P4-2) |
| **Libération du verrou d'opération dépendante d'une fin sans exception** | P4-5 (ancien P1-3 ; prolonge l'Audit 3, P4-1) |
| **Nombre attendu recalculé après un ajout différé** | P2-2 (interaction avec `run_when_idle`, Audit 3, P0-2) |

---

## P0 — Data integrity after errors

Aucun P0 après vérification. Les deux constats initiaux sont conservés ci-dessous tels qu'ils avaient été formulés, suivis du résultat de leur vérification.

### P0-1 (reclassé P3-1) · Remplacement d'un fichier : si `ReplaceFileW` échoue après avoir retiré la destination, le temporaire, seule copie restante, est supprimé

- **Certitude initiale** : Possible · **Après vérification** : non reproduit · **Statut** : Corrigé en défense (voir P3-1)
- **Code** : `utils.py` → `_replace_keeping_identity`, `replace_file_retrying`, `replace_file_atomically` ; `batch_dialogs_qt._interruptible_zip_writer` ; `remove_stale_temp_files`.
- **Constat initial** : d'après la documentation de Microsoft, l'erreur 1176 de `ReplaceFileW` signifie que le fichier remplacé n'existe plus sous son nom (1177 : il existe sous un nom inconnu). Le code retente `os.replace` 50 × 0,1 s ; si le temporaire reste verrouillé (antivirus analysant un gros CBZ), l'exception remonte et le `except BaseException` de `replace_file_atomically` (et de `_interruptible_zip_writer`) **supprime le temporaire** ; à défaut, `remove_stale_temp_files` le supprime 10 minutes plus tard. Ni l'ancien fichier ni le nouveau ne resteraient.
- **Vérification** : appels réels de `ReplaceFileW`, sur disque local NTFS puis sur le partage SMB `\\localhost\C$` — temporaire ou destination ouverts par un autre programme avec cinq modes de partage, attributs lecture seule, et 3 000 courses où un second thread ouvre le temporaire en boucle pendant l'appel (puis 3 000 sur la destination). Résultat : **jamais 1176 ni 1177, jamais de destination disparue**. Chaque échec est une erreur 32 renvoyée avant toute modification : `ReplaceFileW` ouvre les deux fichiers dès le départ. Un verrou d'antivirus ou d'indexeur produit donc l'erreur 32, que `replace_file_retrying` traite correctement (nouvelles tentatives, puis `FileInUseError`, original intact). Seuls des volumes non testés (FAT/exFAT, NAS non Windows) restent à envisager ; l'utilisateur n'a pas été sollicité pour écrire sur ses autres lecteurs.
- **Reclassement** : P3, défense théorique : la branche fautive suppose 1176 **et** un échec de `os.replace` pendant 5 s, double condition qu'aucun essai n'a produite.

### P0-2 (reclassé P3-2) · Trois réécritures sur place remplacent le CBZ d'origine sans contrôler le fichier produit

- **Certitude initiale** : Possible · **Après vérification** : manque de défense, aucune perte démontrable · **Statut** : Corrigé (voir P3-2)
- **Code** : `batch_dialogs_qt._run_recompress` (`do_recompress`) ; `batch_metadata_dialog_qt._BatchMetadataOrchestrator._save_state_for_file` (branche CBZ : `replace_file_retrying(tmp_path, filepath)` sans contrôle ; seules les conversions sont relues) ; `library_window.LibraryWindow._rewrite_comicinfo_in_cbz`.
- **Constat initial** : contrairement aux enregistrements depuis un panneau (`_write_document_unlocked` → `check_written_cbz_structure`, Audit 5, C-14), ces trois chemins remplacent le CBZ de l'utilisateur sans contrôle du fichier produit ; une écriture abîmée sans exception remplacerait l'original.
- **Vérification** : à la relecture, toutes les causes d'échec examinées y sont signalées et laissent l'original intact — disque plein ou erreur d'écriture et de fermeture : exception, temporaire supprimé ; membre source abîmé : contrôle du CRC par `zipfile` à la lecture ; coupure de courant : `fsync` avant le remplacement dans les trois chemins. Reste une écriture abîmée **sans aucune erreur** (défaillance du disque, du support ou du pilote), le même cas que l'Audit 5, C-14, qui n'avait pas pu être provoqué non plus. La règle de `CLAUDE.md` ne vise que les CBZ écrits depuis un panneau : pas d'infraction.
- **Reclassement** : P3, incohérence avec la protection choisie par l'utilisateur pour les panneaux.

---

## P1 — Incorrect state after errors

### P1-1 · Couper retire de la B.D. une page illisible qu'il n'a pas copiée

- **Certitude** : Confirmed (reproduit sur les fonctions réelles) · **Gravité** : haute — la page disparaît au prochain enregistrement · **Statut** : Corrigé et validé (tests automatiques ; dans l'application, page réellement abîmée : Couper, Copier, collage dans l'autre panneau et dans l'Explorateur, Annuler ; archive modifiée sur le disque depuis l'ouverture : rien coupé ni copié, message). Les fichiers de test d'origine (image saine, somme de contrôle fausse) ont été refaits avec une page réellement abîmée : depuis C-15, une telle page s'ouvre en orange avec son image, et ne passe plus par la relecture dans l'archive
- **Opération** : Couper (Ctrl+X, menus, colonne d'icônes) ; aussi Copier (Ctrl+C), sans perte.
- **Type d'erreur** : page illisible à l'ouverture (membre au CRC faux, chiffré, à compression non prise en charge, au-delà de `MAX_MEMBER_SIZE`) : cadre rouge, `entry["bytes"] is None`.
- **Code** : `clipboard_qt.py` → `cut_selected`, `copy_to_system_clipboard`, `_copy_entries_to_system_clipboard`.
- **Séquence** : sélection de p1, p2 (cadre rouge) et p3 → Ctrl+X. `_copy_entries_to_system_clipboard` ne garde que les entrées à données (`to_write`), écrit p1 et p3, pose le presse-papiers et rend `True` ; `cut_selected` retire **toute** la sélection.
- **État obtenu** (reproduit sur un vrai `AppState`, faux presse-papiers) : presse-papiers = p1, p3 ; document = vide. p2 n'est ni dans la B.D. ni dans le presse-papiers ; Annuler la rend, si l'utilisateur s'en aperçoit ; enregistrer ensuite efface de l'archive son membre, encore récupérable par un outil ZIP — alors que `_check_pages_have_data` refuse justement d'omettre une telle page (Audit 2, P0-5). Avec Ctrl+C, le même saut silencieux ne détruit rien, mais la page manque au collage.
- **Attendu, précisé par l'utilisateur** : la page fautive est copiée avec les autres, **toujours avec sa page réelle** (décision non négociable) ; si sa page réelle ne peut pas être obtenue, rien n'est copié ni coupé, et un message le dit, en nommant les pages fautives ; deux messages distincts pour Copier et Couper ; « B.D. » en français.
- **Première correction, annulée** : la première version ne retirait que les pages copiées et laissait la page rouge dans le document, sélectionnée, avec la fenêtre « Fichiers ignorés ». L'utilisateur l'a refusée — l'utilisateur serait perdu si une partie de sa sélection restait en place — et le fichier a été remis à l'état du dernier commit avant la correction définitive (voir C-1).
- **Correction appliquée** :
  - **Page réelle relisible** : pour un ZIP (CBZ, EPUB), le lecteur `_zip_member_reader` expose `read.last_info` (`ZipInfo` de la dernière lecture tentée, `None` si le nom n'a pas été trouvé) ; une page restée sans données reçoit `entry["unread_member"]` = chemin absolu de l'archive, `header_offset` du membre (unique, même entre homonymes) et `utils.file_signature` de l'archive (`archive_loader._remember_unread_zip_member`, appelée par `LoadWorker.load_archive`, `load_multiple_archives` et `import_merge_qt._load_zip`). À la copie, `clipboard_qt._copy_unread_member` vérifie la signature puis recopie le membre tel qu'il est dans l'archive : `archive_names.copy_zip_member_as_stored` (nouvelle fonction sans Qt : CRC non contrôlé, `ZipExtFile._expected_crc = None`, copie par blocs sans limite de taille). Vérifié : un membre au CRC faux est relu octet pour octet, en mode stocké comme compressé. Collée, la page reprend son cadre rouge.
  - **Page réelle impossible à relire** (page d'un CBR, CB7 ou CBT, sans référence ; archive déplacée ou modifiée depuis l'ouverture ; membre chiffré, Deflate64, tronqué) : `_copy_entries_to_system_clipboard` ne pose rien dans le presse-papiers (dossier temporaire supprimé), rend `False`, et `_warn_unreadable_not_copied(parent, pages, cutting)` affiche « Fichiers ignorés » : `messages.warnings.clipboard_unreadable.copy` (« Rien n'a été copié : ces pages ne peuvent pas être relues dans le fichier d'origine. ») ou `.cut` (« Rien n'a été coupé : … Toutes les pages restent dans la B.D. »), puis une ligne par page (clé existante `messages.read_errors.line`, nom et raison traduite ; `.source_changed` pour une archive changée). `copy_to_system_clipboard` reçoit `cutting` ; `cut_selected` passe `cutting=True`. Couper garde son fonctionnement : il retire toute la sélection, seulement si la copie a réussi.
  - Aucun message quand la copie est complète. Trois nouvelles clés traduites dans les 46 fichiers de langue (langues fictives par `run_block.sh`, variantes pIqaD et tengwar régénérées), vérifiées en mode « par clés » du skill `verify-translation` : une correction (lituanien : génitif de négation, « Nieko nenukopijuota »).
  - Le principe avait d'abord été exposé à l'utilisateur, qui demandait si Couper pouvait réussir là où Copier échoue : non, Couper est Copier puis retirer, et il ne retire rien si la copie échoue.
- **Tests** : `tests/test_clipboard_unreadable_pages.py` (nouveau, 9 tests, 10 cas, fonctions réelles extraites par AST, faux presse-papiers et fausse fenêtre) : membre au CRC faux relu à l'identique (stocké et compressé), homonymes relus chacun depuis son membre, compression non prise en charge refusée, référence posée sur une page illisible, aucune référence périmée après une recherche de nom en échec, Couper de p1 + p2 (rouge) + p3 (trois pages copiées, p2 réelle, sélection retirée, aucun message), archive modifiée depuis l'ouverture (rien copié ni retiré, message de Couper nommant p2), page d'un CBR (Copier refusé, message de Copier). **Test dans l'application à faire** : CBZ avec une page au CRC faux, Ctrl+X sur trois pages dont la rouge puis Ctrl+V dans l'autre panneau ; même essai après modification de l'archive sur le disque.
- **Interactions** : historique (le champ `unread_member` suit la page, non exclu par `_NOT_IN_META`) ; déplacement entre panneaux ; visionneuse (`copy_single_entry_to_system_clipboard` passe par le même cœur, avec le texte de Copier).

### P1-2 · Import de métadonnées en lot annulé pendant la fin d'une écriture : fichier annoncé non traité alors que son original a été supprimé, réservations libérées trop tôt

- **Certitude** : Very likely dans le rapport initial · Confirmed après vérification (reproduit) · **Statut** : Corrigé et validé (tests automatiques pour le cas exact, une annulation pendant les dernières millisecondes d'une écriture, impossible à viser à la main ; annulation ordinaire et fermeture de la fenêtre ComicVine vérifiées dans l'application)
- **Opération** : import de métadonnées en lot, bouton Annuler confirmé ou fermeture de la fenêtre ComicVine pendant l'écriture d'un fichier.
- **Code** : `batch_metadata_dialog_qt.py` → `_BatchMetadataOrchestrator.cancel`, `_retire_save_worker`, `_show_summary` (`self._claims.finish()`), `_save_state_for_file` (`_check_cancel` testé seulement avant chaque `writestr` et pendant l'attente du process PDF).
- **Séquence** : le dernier membre est écrit ; pendant la fermeture du ZIP, le `fsync`, `replace_file_retrying`, `verify_written_cbz` (relecture complète) et `send_to_trash`, l'utilisateur confirme Annuler. `cancel()` débranche `done` et `error`, affiche le résumé et libère les fichiers ; le worker orphelin termine pourtant.
- **Vérification** : (1) `_save_state_for_file` réelle, conversion d'un CBT de 150 pages de 1,5 Mo, annulation posée au début de la relecture : écriture terminée normalement, CBT mis à la Corbeille ; sur un SSD, 64 % de la durée se passe après le dernier contrôle d'annulation. (2) Méthodes réelles de l'orchestrateur avec un faux worker encore en cours : résumé affiché aussitôt (0 traité), **second lot démarrable sur le même fichier** pendant l'écriture, fin réelle de l'écriture jamais comptée.
- **Attendu** : le résumé et la libération des réservations attendent la fin réelle de l'écriture, et le fichier est compté s'il a été écrit.
- **Correction appliquée** : nouvel indicateur `_cancelling`. `cancel()`, si une écriture tourne, demande son arrêt, se branche sur le `finished` **natif** du `_SaveWorker` (émis après son `done` ou son `error`, `_SaveWorker` ne le masquant pas) et rend la main ; `_finish_cancel` affiche ensuite le résumé, `_show_summary` terminant le lot. Écriture finie entre le test `isRunning()` et le branchement : `QTimer.singleShot(0, _finish_cancel)` (le résumé n'est affiché qu'une fois, `_ended`). Pendant l'attente, `cancel()` rappelé (fermeture de la fenêtre ComicVine) et `_on_next` sont sans effet. Le texte de la confirmation d'annulation (« Le fichier affiché … ne recevra pas de métadonnées ») n'a pas été modifié : il reste juste dans le cas courant, et le résumé dit désormais la vérité dans l'autre (le modifier demanderait de le retraduire).
- **Tests** : `tests/test_batch_metadata_cancel.py` (nouveau, 4 tests, méthodes extraites par AST, faux worker) : écriture menée au bout après Annuler (pas de résumé ni de libération avant la fin, puis 1 traité, aucun fichier suivant chargé, lot terminé), écriture réellement arrêtée (0 traité), écriture terminée pendant le branchement (un seul résumé), annulation sans écriture (résumé immédiat). **Test dans l'application à faire** : import en lot sur un gros CBR, Annuler pendant « Conversion en cours ».
- **Interactions** : traitements par lot (`batch_claims`, même famille que l'Audit 4, P0-1) ; Corbeille.

### P1-3 (reclassé P4-5) · Une exception dans la fin de certaines opérations laisserait le document verrouillé, impossible à enregistrer

- **Certitude initiale** : Possible (aucun déclencheur trouvé, conséquence grave) · **Après vérification** : non reproduit · **Statut** : Non traité (voir P4-5)
- **Code** : `image_transforms_qt._run_transform.on_finished`, `deskew_qt._run_deskew.on_finished` (`_release_worker()`, donc `end_operation`, appelé en fin de slot) ; `conversion_dialogs_qt.perform_conversion` (si `_ConversionWorker.run` levait, ni `finished` ni `cancelled` ne seraient émis et `_cleanup` jamais appelé).
- **Constat initial** : `operation_running()` resterait vrai ; Enregistrer, Annuler, Refaire et toutes les commandes de `BUSY_BLOCKED_COMMANDS` sans effet ; « Enregistrer et fermer » refusé en silence ; seule sortie, « Fermer sans enregistrer ». Le redimensionnement (slot `_cleanup` séparé), l'écriture et la macro (`finally`) sont sains.
- **Vérification** : chaque appel de ces fins et du `run()` de la conversion a son propre repli ou ne peut pas lever : `update_page_entries_in_xml_data` (`try` global), `get_page_image_index`, `refresh_thumbnail` → `_get_pixmap_for_size` (repli icône puis carré gris), `recompute_duplicate_groups`, `build_qimage_for_entry`, `convert_image_data` (rend un échec au lieu de lever). Aucune séquence trouvée.
- **Reclassement** : P4 — convention fragile.

### P1-4 (reclassé P2-9) · Ouverture de plusieurs archives : la première, illisible à la relecture, laisse un document qui porte son nom sans ses pages

- **Certitude initiale** : Possible · **Après vérification** : mécanisme confirmé, déclencheur rare · **Statut** : Non corrigé
- **Code** : `archive_loader.LoadWorker.load_multiple_archives` (phase 2, `except Exception as e: errors.append(str(e)[:100])` ; `actual_first_fp[0] = archive_namelists[0][0]`) ; `ArchiveLoader._on_finished`.
- **Vérification** (méthode réelle extraite par AST, deux vrais CBZ, A rendu illisible entre la liste de ses noms et la lecture de ses pages) : fichier du document `A.cbz`, pages chargées `NEW-B0.jpg`, `NEW-B1.jpg`, `NEW-B2.jpg`, aucune de A ; « Fichiers ignorés » : `[Errno 22] Argument non valide`, sans le nom de l'archive. Enregistrer remplacerait A par les pages de B. Seul déclencheur trouvé : un fichier devenu illisible dans l'intervalle (quelques millisecondes, sauf quand la question de correction d'extension l'allonge) — partage réseau coupé, clé retirée ; aucune cause interne ; une erreur s'affiche, et la perte suppose ensuite une modification et un enregistrement.
- **Reclassement** : P2.
- **Correction recommandée** : préfixer l'erreur du nom de l'archive ; prendre comme fichier du document la première archive réellement lue, ou abandonner le chargement.

---

## P2 — User-visible functional errors

Constats du rapport initial (P2-1 à P2-8) et constat reclassé (P2-9), décrits tels qu'ils ont été établis ; leurs corrections figurent dans le tableau qui suit.

**Vérification avant correction** (à la demande de l'utilisateur, comme pour les P0 et P1 ; vraies fonctions extraites par AST, sans Qt, petits fichiers jetables) :
- **P2-1** : vrai `_run_pdf_conversion` et vrai thread de lot sur deux PDF, avec un vrai process `multiprocessing` qui meurt brutalement à l'ouverture du premier → `EOFError` non rattrapée dans le thread du lot, aucun `conversion_done`, fenêtre de progression jamais fermée, second PDF jamais tenté ; seules les réservations sont libérées.
- **P2-2** : vraie chaîne d'écriture d'un enregistrement ; une image de l'import web arrive pendant l'écriture → CBZ correct de 5 fichiers (CRC bons), document à 6 pages, `verify_written_cbz(…, 6)` lève `WrittenFileCheckError` (5 écrits, 6 attendus). Pas de perte : document non marqué enregistré, original gardé.
- **P2-3** : vraie `load_multiple_archives` sur deux `.cbz` invalides → deux erreurs relevées, puis seulement `cancelled`, sans argument ; `_on_cancelled` n'affiche rien.
- **P2-5** : vrai cœur de la copie, presse-papiers qui refuse l'ouverture → Copier rend `False` sans message, Couper ne retire rien sans message.
- **P2-7** : vrai `save_macro` sur une macro existante en lecture seule → `PermissionError` ; aucun `try` dans `_on_save_clicked`.
- P2-4 et P2-6 : confirmés par la lecture du code, sans essai (décision de l'utilisateur) ; P2-8 : textes en dur visibles dans le code ; P2-9 : déjà reproduit (fiche de l'ancien P1-4).

| # | Constat | Certitude | Code | Séquence → effet | Attendu |
|---|---|---|---|---|---|
| **P2-1** | Lot PDF → CBZ : si le process PDF meurt pendant l'ouverture d'un PDF, le thread du lot meurt et la fenêtre de progression ne peut plus être fermée | Confirmed (testé : `poll()` rend `True` puis `recv()` lève `EOFError` ; `poll()` sur un canal déjà rompu lève `BrokenPipeError`) | `batch_dialogs_qt._run_pdf_conversion` (`_recv` et première attente de `batch_ready` hors `try`) ; `_start_batch_thread._run` (n'attrape que `_BatchInterrupted`) ; `_ProgressDialog.closeEvent` | Plantage natif de MuPDF sur un PDF malformé (les erreurs Python du process sont, elles, renvoyées en `('error', …)`) → exception non rattrapée, `conversion_done` jamais émis ; croix ignorée, Annuler grisé après un clic ; PDF suivants non traités, aucun résumé. Aucune perte | `_recv` rend `None` (ou l'erreur) sur `EOFError`/`OSError` et redémarre le process ; `_run` émet toujours la fin du lot |
| **P2-2** | Faux échec de relecture : un CBZ correct annoncé abîmé (cri de Wilhelm) | Confirmed (code) | `file_operations_qt.save_as_cbz._write_and_finish`, `_write_apply_new_names` (CBR/CB7/CBT/EPUB et PDF) : `written_count = len(cbz_members(state.images_data))` calculé **après** `end_operation`, qui a appliqué les ajouts en attente | Import web, numérisation, fusion ou ComicVine terminé pendant l'écriture → N+k attendus, N écrits → `written_file_check_failed`, original gardé, document repassé à « modifié » | Nombre de fichiers réellement écrits (`_write_zip_with_progress` le rend déjà, à remonter) |
| **P2-3** | Ouverture de plusieurs archives toutes illisibles : aucun message | Confirmed | `load_multiple_archives` (`if not archive_namelists: self.cancelled.emit()`, liste `errors` perdue) | Erreur présentée comme une annulation, panneau vide | Émettre `error` avec la liste |
| **P2-4** | Bibliothèque : échecs silencieux | Confirmed | `LibraryWindow._convert_file_to_cbz._on_error` (message ignoré) ; `_open_comicinfo_editor` (`except Exception: return`, `xml_bytes is None: return`) ; second lancement pendant une conversion (`return _finish(None)`) | « Convertir en CBZ », « Modifier ComicInfo » ou « Vérifier les mises à jour » sur une archive illisible : rien ne se passe | Afficher l'erreur de chargement ou de lecture |
| **P2-5** | Copier/Couper : échec sans message | Confirmed | `_copy_entries_to_system_clipboard` (`except Exception: return False` : `OpenClipboard` tenu par un autre programme, `%TEMP%` plein) ; `PanelWidget._copy_selected` ignore le retour | Ctrl+C / Ctrl+X sans effet visible. La correction de P1-1 n'a pas changé ce cas | Message (presse-papiers occupé, réessayer) |
| **P2-6** | Export vers un dossier : un échec au milieu laisse un export partiel non signalé comme tel | Confirmed | `file_operations_qt.save_selection_to_folder._write_all` | Disque plein à la page 7 sur 20 : pages 1 à 6 écrites (fichiers existants déjà remplacés après confirmation), message « Impossible d'enregistrer les fichiers : … » seul ; contredit le commentaire « tout ou rien » | Dire combien ont été écrites et lequel a échoué |
| **P2-7** | Enregistrement d'une macro : erreur d'écriture non rattrapée | Confirmed | `macro_tool_qt._MacroNameDialog._on_save_clicked` → `save_macro`/`rename_macro` sans `try` | Exception avalée par Qt (journal d'erreurs seulement), fenêtre ouverte, chaque clic échoue sans message ; `rename_macro` : nouvelle macro écrite puis `os.remove` de l'ancienne en échec → macro sous deux noms | Message par `_set_status`, avec la raison |
| **P2-8** | Messages bruts, non traduits ou faux | Confirmed | `PdfLoadWorker`/`PdfMergeWorker` : « PDF process terminated unexpectedly », « PDF pipe broken », « PDF merge process terminated unexpectedly » affichés ; `LoadWorker` : `self.error.emit(str(e)[:200])` (message brut d'une archive illisible, `read_errors.read_error_text` non utilisé) et textes `_()` résolus dans le worker puis passés en `str` à `_MessageDialog` (non retraduits, règle UI n° 2) ; lots : « format inconnu » aussi pour un fichier disparu ou verrouillé (`detect_archive_type` rend `None` sur `OSError`), « archive vide », « (suppression) » en dur dans les journaux | Diagnostic trompeur, langue figée | Clés existantes (`pdf_failure.process_stopped`), `read_error_text`, callables, clés pour les journaux |
| **P2-9** | Ancien P1-4 (voir sa fiche) | Confirmed (mécanisme), rare | `load_multiple_archives` | — | — |

**Corrections appliquées** (d'une traite, à la demande de l'utilisateur ; seule pause : validation des nouveaux textes français) :

| # | Statut | Correction | Tests |
|---|---|---|---|
| **P2-1** | Corrigé (test dans l'application impossible sans PDF faisant planter MuPDF) | `_recv` intercepte `EOFError`/`OSError` et rend `('process_stopped',)`, traité à l'ouverture comme pendant les pages : erreur du fichier (clé existante `messages.errors.pdf_failure.process_stopped`) puis `_restart_process()`. En défense, `_start_batch_thread._run` émet `conversion_done` sur toute exception imprévue, puis la relance pour le journal des erreurs (fenêtre fermée, résumé des fichiers déjà traités). Rejoué sur la reproduction : les deux PDF tentés, un process neuf pour chacun, résumé « 0 sur 2, 2 erreurs ». | `test_error_reporting.py` (`test_dead_pdf_process_is_reported_and_the_batch_finishes`, faux canal qui lève `EOFError`) |
| **P2-2** | Corrigé | `written_count` compté juste avant l'écriture (même liste que `_write_zip_with_progress`) dans `save_as_cbz._write_and_finish` et les deux conversions de `_write_apply_new_names`. | Aucun automatique (fonctions imbriquées dans les fenêtres Qt) ; manuel |
| **P2-3** | Corrigé, validé dans l'application (test manuel 5, avec C-16) | Aucune archive lisible : `error` avec les messages de chaque archive ; `cancelled` seulement si toutes les questions de correction d'extension ont été refusées sans erreur. Rejoué : les deux erreurs « archive invalide » envoyées à la fenêtre d'erreur. | `test_error_reporting.py` (`test_all_invalid_archives_are_reported_not_cancelled`) |
| **P2-4** | Corrigé | `_convert_file_to_cbz._on_error` affiche `library.convert_to_cbz_error_message` avec le texte du chargeur ; seconde conversion pendant une première : `library.convert_already_running` ; `_open_comicinfo_editor` : `library.comicinfo_read_failed` (raison traduite par `read_error_text`) et `library.comicinfo_missing`. Sans cri de Wilhelm (règle du projet : jamais sans accord). | Manuel |
| **P2-5** | Corrigé | `_copy_entries_to_system_clipboard` : sur exception, dossier temporaire supprimé et `ErrorDialog` `messages.errors.clipboard_failed` (« rien n'a été copié ni coupé », raison `strerror`). | `test_clipboard_unreadable_pages.py` (`test_clipboard_in_use_is_reported_and_nothing_is_cut`) |
| **P2-6** | Corrigé | `_write_all` : si au moins un fichier a été écrit, `messages.errors.save_files_partial` (nombre écrits sur total, dossier, fichier en échec, raison), avec le cri de Wilhelm comme le message d'échec d'export existant ; sinon message d'origine. | Manuel |
| **P2-7** | Corrigé, message validé dans l'application (test manuel 6 ; la raison citée est le texte de Windows, dans la langue du système, et ne suit pas la langue de l'application : comportement accepté par l'utilisateur). Relevé pendant ce test : un renommage refusé laissait une copie sous le nouveau nom à côté de l'ancienne (défaut antérieur, seulement rendu visible) ; corrigé dans `macro_engine.rename_macro`, qui retire le fichier écrit si l'ancien ne peut pas être retiré, puis relance l'erreur (`test_refused_rename_leaves_only_the_old_macro`), validé dans l'application | `_on_save_clicked` : `OSError` de `save_macro`/`rename_macro` affiché par `_set_status` (`dialogs.macro_name_error.save_failed`), fenêtre ouverte, étapes gardées. | `test_error_reporting.py` (`test_macro_save_failure_is_shown_and_the_window_stays_open`) |
| **P2-8** | Corrigé en partie | PDF : marqueur `PDF_PROCESS_STOPPED` émis par `PdfLoadWorker`/`PdfMergeWorker`, traduit à l'affichage par `pdf_error_text` dans des `ErrorDialog` à textes callables (`PdfLoader._on_error`, `import_and_merge_pdf.on_error`). Chargement d'archive : `LoadWorker._emit_error`/`_emit_read_error` gardent le texte en callable (`error_text_fn`), relu par `ArchiveLoader._on_error` et la Bibliothèque ; archive illisible décrite par nom et raison traduite (`read_error_text`, `messages.read_errors.line`). Journaux de lot : `messages.errors.batch_unknown_format`, `batch_empty_archive`, `batch_source_delete_failed` à la place des textes français en dur. **Reste** : les lignes de « Fichiers ignorés » de l'ouverture groupée et les en-têtes anglais des journaux (« Error details: ») restent résolus à l'écriture. | Indirects (`test_error_reporting.py`) ; manuel |
| **P2-9** | Corrigé | Archive en échec en phase 2 : pages déjà lues retirées (`del all_data[first_entry:]`), erreur préfixée de son nom, document nommé d'après la première archive lue entièrement (`read_archives`), préfixe `NEW-` seulement après elle ; aucune lue → `error`. Rejoué : document `B.cbz`, pages de B sans préfixe, erreur nommant `A.cbz`. | `test_error_reporting.py` (`test_first_archive_unreadable_when_read_gives_no_partial_pages`) |

Dix nouvelles clés (`messages.errors.clipboard_failed.title`/`.message`, `save_files_partial`, `batch_unknown_format`, `batch_empty_archive`, `batch_source_delete_failed` ; `dialogs.macro_name_error.save_failed` ; `library.convert_already_running`, `comicinfo_read_failed`, `comicinfo_missing`), texte français validé par l'utilisateur, traduites dans les 46 fichiers de langue (langues fictives par `run_block.sh`, variantes pIqaD et tengwar régénérées) et vérifiées en mode « par clés » : aucune anomalie. En sindarin, `nó` (qui n'y veut pas dire « avant ») et `sí` (« ici », « maintenant » se disant `hí`) ont été écartés avant application.

## P3 — Recovery / robustness

| # | Constat | Certitude | Code |
|---|---|---|---|
| **P3-1** | Ancien P0-1 : branche 1176/1177 de `ReplaceFileW` où le temporaire, seule copie, serait supprimé — non reproduite sur NTFS ni SMB (voir la fiche) | Possible (volumes non testés) | `utils._replace_keeping_identity`, `replace_file_atomically`, `_interruptible_zip_writer`, `remove_stale_temp_files` |
| **P3-2** | Ancien P0-2 : recompression en lot, import de métadonnées (CBZ réécrit sur place) et réécriture du ComicInfo.xml par la Bibliothèque remplacent l'original sans `check_written_cbz_structure` (voir la fiche) | Possible (écriture abîmée sans erreur) | `_run_recompress`, `_save_state_for_file`, `_rewrite_comicinfo_in_cbz` |
| **P3-3** | Collage : `OpenClipboard` sans `CloseClipboard` si `IsClipboardFormatAvailable` ou `GetClipboardData(CF_HDROP)` lève → presse-papiers de **tout Windows** bloqué jusqu'à la fermeture de MosaicView | Possible | `clipboard_qt.paste_from_system_clipboard` (fermeture hors `finally`) |
| **P3-4** | Messages vides : `str(e)` d'`EOFError` ou de `MemoryError` vaut `""` → « X.cbr: » dans les journaux, erreur vide dans le résumé de l'import de métadonnées | Confirmed | `_SaveWorker.run`, `conversion_errors.append(f"{basename}: {e}")` (lots) |
| **P3-5** | Journal de lot non écrit : erreur avalée (`except Exception: pass` suivi de `log_path = None`, deux instructions, hors de portée de `test_check_no_silent_pass`) ; résumé « renommages seulement » avec un lien `file:///None` | Confirmed | `on_done` des 7 lots ; `_CbrSummaryDialog._retranslate` et équivalents |
| **P3-6** | `save_config()` rend `False` et aucun appelant ne le lit : clé API, marque-pages, récents perdus au démarrage suivant sans avertissement (disque plein, `%APPDATA%` protégé) | Possible | `config_manager.ConfigManager.save_config` |
| **P3-7** | « Images → un seul CBZ » : si toutes les images échouent, un CBZ vide est créé | Confirmed | `_run_imgs_to_single_cbz.do_conversion` |
| **P3-8** | Diagnostic. **Erreur bien gérée mais difficile à diagnostiquer** : les erreurs affichées (`ErrorDialog` avec `str(e)`) ne laissent aucune trace dans le journal des erreurs, la pile est perdue. **Erreur mal gérée et peu visible** : le plantage natif d'un process PDF n'est journalisé nulle part (`faulthandler` seulement dans le process principal), seul « process terminated » remonte | Confirmed | `error_log.py`, `pdf_loading_qt._pdf_persistent_process` |

**Vérification avant correction** (vraies fonctions extraites par AST, sans Qt) :
- **P3-3** : vrai `paste_from_system_clipboard`, faux `win32clipboard` dont `IsClipboardFormatAvailable` lève → `OpenClipboard` appelé, `CloseClipboard` jamais.
- **P3-4** : `str(EOFError())` et `str(MemoryError())` valent `""` → ligne « X.cbr: » dans le journal, erreur vide dans le résumé de l'import de métadonnées.
- **P3-5** : écriture du journal en échec → `log_path = None` sans trace ; le résumé ne dit rien de l'échec.
- **P3-6** : `ConfigManager.set` rend `False` après un échec d'écriture ; la fenêtre de la clé API se ferme quand même et la clé est perdue au démarrage suivant. Un échec du chiffrement DPAPI enregistrait en plus une clé vide.
- **P3-7** : toutes les images refusées → CBZ vide créé sous le nom réservé.
- P3-8 : par le code ; P3-1 et P3-2 : voir les fiches des anciens P0-1 et P0-2.

**Corrections appliquées** (d'une traite, à la demande de l'utilisateur ; seule pause : validation des nouveaux textes français) :

| # | Statut | Correction | Tests |
|---|---|---|---|
| **P3-1** | Corrigé en défense | Dans la branche 1176/1177 de `_replace_keeping_identity`, si `os.replace` échoue encore et que la destination est absente : `ReplacementPendingError` (message `messages.errors.replace_pending`, qui nomme le temporaire et dit de le renommer). Le temporaire est inscrit dans `utils._kept_temp_files` : `is_kept_temp` le fait garder par `replace_file_atomically`, `_interruptible_zip_writer`, la recompression et l'import de métadonnées, et `remove_stale_temp_files` ne le supprime plus. | `test_audit7_p3.py` (`ReplaceFileW` simulé qui retire la destination, `os.replace` verrouillé ; temporaire ordinaire toujours supprimé) |
| **P3-2** | Corrigé | `check_written_cbz_structure` avant le remplacement dans `_run_recompress` (nombre de fichiers de la source), `_save_state_for_file` (toutes les branches ; `_CancellableZip.written_files` compte les fichiers écrits, dossiers exclus) et `_rewrite_comicinfo_in_cbz` (fichiers du ZIP produit). Un contrôle en échec lève `WrittenFileCheckError`, compté en erreur, original intact. | En partie : `test_audit7_p3.py` (`_CancellableZip`) et `test_batch_metadata_save.py`, qui exécute désormais le contrôle ; recompression et Bibliothèque par le code |
| **P3-3** | Corrigé | `paste_from_system_clipboard` : lecture des formats entre `OpenClipboard` et un `finally` qui referme ; le chargement des fichiers et la capture de l'image se font après la fermeture. | `test_audit7_p3.py` (lecture qui lève ; fichiers chargés après la fermeture) |
| **P3-4** | Corrigé | `temp_files.exception_text(e)` (`str(e)` ou le nom du type) dans les journaux de lot, les workers de l'import de métadonnées et le message d'échec du collage. | `test_audit7_p3.py` |
| **P3-5** | Corrigé | Les 7 `on_done` des lots et `_show_summary` de l'import de métadonnées gardent la raison (`log_error`) ; `_log_label_text` (lien seulement si le journal existe, sinon nombre d'erreurs et `dialogs.batch_log_failed`, raison échappée) remplace les liens `file:///{log_path}` des six résumés. Le libellé est créé aussi quand seul `log_error` est connu. Résumé de recompression avec erreurs mais sans journal : il montrait un libellé vide, il donne maintenant le nombre (`dialogs.batch_recompress.errors_count`). | `test_audit7_p3.py` (`_log_label_text`) |
| **P3-6** | Corrigé et validé dans l'application (tests manuels 7 et 8 : message « clé vide » qui suit la langue, clé et réglages impossibles à enregistrer, un seul avertissement par session, clé d'origine conservée) | Fenêtre de la clé API : `_on_validate`/`_on_clear` lisent le retour de `set_comicvine_api_key` et affichent `comicvine.api_key_dialog.error_save`, fenêtre ouverte ; `set_comicvine_api_key` rend `False` sans rien modifier si DPAPI échoue. Ailleurs (marque-pages, récents, réglages) : `ConfigManager.save_failed_handler`, posé par `MainWindow`, relaie l'échec par un signal (appel possible depuis un thread) vers un seul `ErrorDialog` par session (`messages.errors.config_save_failed`). | `test_audit7_p3.py` (handler appelé, clé non chiffrée non enregistrée, fenêtre de la clé, un seul avertissement) |
| **P3-7** | Corrigé et validé dans l'application (test manuel 9, avec C-11 : aucun CBZ vide, refus traduits dans le journal) | `_run_imgs_to_single_cbz` : aucune image écrite → `_IncompleteCbz` dans le bloc d'écriture, temporaire jeté, aucun CBZ créé ; chaque image en échec reste au journal. | Aucun automatique (fonction imbriquée, fenêtre de progression Qt) ; manuel |
| **P3-8** | Corrigé et validé dans l'application (test manuel 16, avec C-12) | `error_log.log_handled_error(exc)`, appelée par `ErrorDialog.__init__` avec l'exception en cours de traitement (`sys.exc_info()`), écrit sa pile (« erreur signalée à l'utilisateur ») ; sans exception en cours (message reçu d'un worker), rien. `error_log.start_child_crash_log()` au début de `_pdf_persistent_process` et `pdf_probe._probe_process` : `faulthandler` vers un journal au nom du process enfant, vide (et supprimé au démarrage suivant) s'il ne plante pas. | `test_audit7_p3.py` (trace écrite, sans session rien, journal enfant, appels présents) |

Six nouvelles clés (`comicvine.api_key_dialog.error_save` ; `messages.errors.config_save_failed.title`/`.message`, `replace_pending` ; `dialogs.batch_log_failed`, `dialogs.batch_recompress.errors_count`), texte français validé par l'utilisateur, traduites dans les 45 autres fichiers de langue (langues fictives par `run_block.sh`, variantes pIqaD et tengwar régénérées) ; contrôle final : clés présentes partout, placeholders conformes, aucune valeur restée en français, variantes CSUR transcrites.

**Vérification « par clés »** (skill `verify-translation`), faite ensuite sur ces six clés et les quatre de C-9 et C-11, avec les termes déjà employés par chaque langue : 32 écarts corrigés dans 22 langues (21 langues naturelles et le quenya). Ce sont surtout des termes différents de ceux du reste du fichier (fichier de configuration, fichiers récents, marque-pages, image illisible, icône), un accord de nombre en tchèque, et en maltais trois mots anglais (`settings`, `bookmarks`, `frames`). En quenya, `lá hentima` est remplacé par `lá cendima`, le terme du fichier. Défaut trouvé dans les trois variantes transcrites : le chemin `%APPDATA%\MosaicView` était transcrit comme du texte (pIqaD : « %APPATA% » ; tengwar : APPDATA effacé). `%APPDATA%` a été ajouté aux noms préservés de `convert_piqad_csur.py` et `convert_tengwar_csur.py`, puis les variantes ont été régénérées ; la comparaison avec leur sauvegarde ne montre que les valeurs concernées.

## P4 — Maintainability

| # | Constat |
|---|---|
| **P4-1** | Trois conventions de libération du verrou d'opération : fin du slot (rotation, redressement), slot séparé (redimensionnement, conversion), `finally` (écriture, macro). |
| **P4-2** | Quatre lecteurs du canal PDF, chacun avec sa gestion de la mort du process (`poll` protégé par `is_alive` dans `PdfLoadWorker`/`PdfMergeWorker`, rien dans le lot) ; `PdfProbe.call` est le bon modèle (issue explicite). Cause de P2-1. |
| **P4-3** | Contrôle avant remplacement non centralisé, intégré au seul écrivain des panneaux. Cause de P3-2 ; prolonge l'Audit 1, P4-1. |
| **P4-4** | 24 `ErrorDialog(...).show()` au lieu de `show_nonmodal()` (dont 14 dans `file_operations_qt.py`, surtout sur les chemins d'erreur d'enregistrement) : flash de recentrage (règle UI n° 5). **Vérification** : 38 appels en réalité (25 `ErrorDialog`, 13 `InfoDialog`, `.show()` direct ou par variable, sans `position_dialog_on_parent` avant) dans 6 fichiers ; `InfoDialog` a le même `showEvent` différé, et le premier comptage ne prenait que les `ErrorDialog` directs. |
| **P4-5** | Ancien P1-3 : la libération du verrou d'opération (`end_operation`) des fins de rotation, de redressement et de conversion dépend d'une fin sans exception ; aucun déclencheur trouvé, mais une exception imprévue rendrait le document impossible à enregistrer. Piste : `try/finally` autour des fins, et `run()` qui émet toujours un signal de fin ; garde AST d'appariement `begin_operation`/`end_operation`. Prolonge l'Audit 3, P4-1. |

**Vérification des P4 restants** (à la demande de l'utilisateur, par lecture du code et scan AST) : P4-4 réel et plus étendu (voir sa ligne) ; P4-1 réel, sans effet pour l'utilisateur, chaque convention étant sûre depuis P4-5 ; P4-3 réduit à un garde-fou manquant — toutes les écritures qui remplacent un CBZ le contrôlent depuis P3-2, les autres créent un fichier neuf relu avant suppression de la source, mais rien n'oblige une future écriture à faire ce contrôle.

**Corrections appliquées** (P4-5, P4-2, P4-4 et P4-3, à la demande de l'utilisateur ; P4-1 non traité) :

| # | Statut | Correction | Tests |
|---|---|---|---|
| **P4-5** | Corrigé | Relecture : en plus des fins, les quatre `run()` (`_TransformWorker`, `_DeskewWorker`, `_ResizeWorker`, `_ConversionWorker`) n'émettaient aucun signal sur une exception hors de leur boucle protégée — `_ConversionWorker` n'a aucune protection par page — et le `finished` natif du redimensionnement aurait alors été pris pour une réussite. Désormais `run()` → `try: self._run()` / `except Exception: self.cancelled.emit(); raise` (annulation : pages restaurées, pages converties retirées par `on_cancelled`, texte de progression masqué ; relancée pour le journal des erreurs). Fins : `on_finished` et `_finish_cancel` de la rotation et du redressement libèrent dans un `finally` (`_release_worker` une seule fois, `released`) ; conversion : `_after_complete` termine l'opération dans un `finally`, et une question de fin qui ne peut pas s'afficher (`_ask_after_conversion`) remet `awaiting_choice` à faux pour que `_cleanup` termine l'opération. Le redimensionnement, dont `_cleanup` est un slot séparé, ne demandait que le masquage du texte. Retours anticipés sur document fermé : sans risque, `force_close_file` vide les opérations. | `test_check_operation_end.py` (nouveau, 14 tests : appariement `begin`/`end_operation`, forme de `run()` pour tout `QThread` d'un module à opérations, chaque `run()` exécuté avec un `_run` qui lève, `finally` des fins, libération unique) |
| **P4-2** | Corrigé | `pdf_loading_qt.read_pdf_message(conn, process, timeout)` : message, `None` (rien, process vivant) ou `PDF_STOPPED_MESSAGE` (canal fermé ou rompu, message illisible, process mort) ; messages envoyés avant la mort lus d'abord (`poll(0)` final). Seul lecteur pour `PdfLoadWorker`, `PdfMergeWorker` (dont le `poll()` n'était pas protégé contre `BrokenPipeError`), le lot PDF (`_recv`) et l'import des métadonnées (`poll()` et `recv()` non protégés : process mort affiché « EOFError »). `PdfProbe.call` garde son modèle à statuts. | `test_pdf_channel_reader.py` (nouveau, 9 tests, dont un vrai canal `multiprocessing` d'un process qui se termine, et un garde AST : aucun `poll()`/`recv()` direct hors de la fonction commune) ; `test_error_reporting.py` utilise désormais la vraie fonction |
| **P4-4** | Corrigé et validé dans l'application (test manuel 12) | Les 38 `.show()` deviennent `.show_nonmodal()` (et les `raise_()`/`activateWindow()` qui suivaient sont retirés, la méthode les fait) dans `batch_dialogs_qt.py` (7), `clipboard_qt.py` (4), `file_operations_qt.py` (17), `library_window.py` (4), `nfo_dialog_qt.py` (4), `open_with_default_app_qt.py` (2). Changement mécanique appliqué par script (exception n° 2 de `CLAUDE.md` : fichiers sauvegardés, comparaison contrôlée : 38 lignes modifiées, 14 retirées, fins de ligne conservées). | `test_check_dialog_show_nonmodal.py` (nouveau, 2 tests : aucun `.show()` nu sur ces trois fenêtres, sauf précédé de `position_dialog_on_parent` ; le scan détecte les deux formes) |
| **P4-3** | Corrigé (garde-fou ; le code de l'application ne change pas) | `tests/test_check_atomic_file_writes.py`, règle 3 : chaque écrivain de `ALLOWED_ZIP_WRITERS` déclare dans `WRITER_CHECKS` sa nature — « remplace » (`check_written_cbz_structure` dans la fonction qui remplace, avant `replace_file_atomically`/`replace_file_retrying`) ou « neuf » (relecture `verify_written_cbz`/`_cbz_verified`) ; les 7 appelants de `_interruptible_zip_writer` (6 conversions relues, la recompression contrôlée) font l'un ou l'autre. Un écrivain ajouté sans contrôle déclaré, ou dont le contrôle disparaît ou passe après le remplacement, fait échouer la suite. Limite : contrôle statique (présence et ordre dans la fonction), il attrape l'oubli qui a produit P3-2, pas un appel placé sur un autre chemin. | 4 nouveaux tests dans ce fichier (déclaration de chaque écrivain, contrôle des écrivains, appelants des lots, détection d'un contrôle absent ou mal placé) |

---

## Constats ajoutés pendant les corrections

| # | Constat | Suite |
|---|---|---|
| **C-1** | Première correction de P1-1 faite à l'envers : elle laissait la page illisible dans le document (non coupée, sélectionnée, signalée), alors que l'utilisateur exige que Couper prenne toute la sélection et que la page copiée soit la page réelle. | Annulée (fichier remis à l'état du dernier commit), puis correction définitive après échange avec l'utilisateur (voir la fiche). |
| **C-2** | En traduisant les messages de P1-1 : l'outil `apply_block.py` des langues fictives refuse une clé absente du fichier (par conception), ce qui empêche d'ajouter un bloc neuf directement. | Bloc ajouté d'abord avec les valeurs françaises, au même format que l'outil (CRLF, indentation 2), puis remplacé par `run_block.sh`, qui a fait ses contrôles et régénéré les variantes CSUR. |
| **C-3** | En traduisant : le sindarin `gwista-`, employé pour « modifié », est donné par Eldamo comme « ignorer, ne pas savoir » (forme ancienne, déconseillée). | Vérifié : c'est le terme retenu par le lexique pour « changer » (néologisme de Paul Strack), déjà employé dans `sjn.json`. Gardé. |
| **C-4** | Hors périmètre (sécurité), relevé en lisant `deskew_qt` : `_DeskewSummaryDialog` affiche les noms de pages dans des `QLabel(f"• {name}")` sans `Qt.PlainText`. `Qt::mightBeRichText` cherche une balise **n'importe où sur la première ligne**, pas seulement au début : un nom de membre contenant `<img src="\\serveur\…">` serait interprété. La description de l'Audit 6, P1-1 (« un texte qui commence par une balise ») est inexacte sur ce point, et `test_check_plain_text_labels` ne détecte pas ce motif. | Corrigé à la demande de l'utilisateur. La recherche du même motif a trouvé deux autres cas : `resize_dialog_qt.OutlierDialog._build_page_widgets` (`QLabel(f"• {entry['orig_name']}")`) et surtout la visionneuse (`ImageViewer._name_label`, nom de la page courante sans `PlainText`, à chaque affichage d'une page). Les trois passent en `Qt.PlainText` avant `setText`. Les autres `QLabel` à f-string affichent un numéro, un libellé traduit, une valeur échappée ou un chemin de dossier du disque (Windows interdit `<` et `>` dans les noms). Garde-fou étendu (3 tests) : aucun `QLabel(f"…{valeur}…")` hors de `ALLOWED_FSTRING_LABELS` (5 cas justifiés), et `PlainText` posé avant `setText` pour les trois affichages de noms de pages. Formule de `CLAUDE.md` (Sécurité n° 3) et du skill `tabs` corrigée : une balise est reconnue n'importe où sur la première ligne. Validé dans l'application (test manuel 13 : nom de page affiché balises comprises dans la visionneuse). |
| **C-5** | Hors périmètre, relevé en lisant `_BatchMetadataOrchestrator._load_state_for_file` (branche CBZ) : le ComicInfo.xml y est cherché par `n.lower().endswith('.xml') and 'comicinfo' in n.lower()`, alors que `CLAUDE.md` impose `comic_info.is_comic_info_entry`/`comic_info_entry` (un `ComicInfo_0002.xml` ou un `Bonus/ComicInfo.xml` peut être pris pour les métadonnées de la B.D.). | Corrigé à la demande de l'utilisateur : `comic_info.is_comic_info_name`, la règle déjà employée par l'option « ignorer les fichiers ayant déjà un ComicInfo.xml » du même import. Effet sûr de l'erreur : fenêtre ComicVine pré-remplie avec les métadonnées d'un fichier annexe ; effet à l'enregistrement (remplacement du vrai ComicInfo.xml par des métadonnées bâties sur l'annexe) non vérifié, la correction le supprime de toute façon. Test : `test_batch_metadata_comicinfo.py` (nouveau, 3 tests : annexes placées avant le vrai fichier ignorées, CBZ qui n'a que des annexes sans métadonnées, casse ignorée) ; suite : 967 tests. Validé dans l'application (test manuel 14 : recherche « Asterix », pas « Tintin »). |
| **C-6** | Hors périmètre : `LoadWorker._read_entries` n'a aucun appelant (code mort). Il n'a pas reçu la référence de membre de P1-1. | Supprimé à la demande de l'utilisateur, après vérification : aucun appel direct, par attribut (`._read_entries`) ou par nom construit (`getattr`) dans tout le dépôt, aucun dans l'historique git, et les sept fonctions qu'il utilisait (`open_zip`, `_zip_member_reader`, `epub_page_name`, `create_entry`, `build_qimage_for_entry`, `_rar_read`, `_read_7z_file`) servent aussi ailleurs. Skill `archive-image-loading` corrigé (il le citait parmi les endroits à modifier pour un nouveau format). Test de non-régression dans l'application : ouverture et ajout de chaque format. Validé (test manuel 15 : CBZ, CBT, CB7 et CBR, seuls, ensemble et en ajout). |
| **C-7** | Relevé pendant l'audit, hors périmètre : dans la fenêtre de la clé API, « Veuillez saisir une clé API » (`comicvine.api_key_dialog.error_empty`) était écrit par `setText` hors de `_retranslate` et restait dans l'ancienne langue (règle UI n° 2). | Corrigé à la demande de l'utilisateur avec P3-6 (`_set_error`, texte rejoué par `_retranslate`) ; la clé était déjà traduite dans toutes les langues. |
| **C-8** | En traduisant P3-6 : le contrôle lexical klingon (`check_tlh.py`) refusait `APPDATA`, nom de dossier Windows. | Ajouté à sa liste des noms admis, comme `Windows` et `Explorer`. |
| **C-9** | Hors périmètre, relevé en corrigeant P3-7 : le journal de « Images → un seul CBZ » écrit « CBZ creation failed: … » en dur (même famille que le reste de P2-8). | Corrigé à la demande de l'utilisateur : `messages.errors.batch_cbz_creation_failed` (validée, traduite dans les 45 autres langues), ligne précédée du nom du CBZ comme les autres lignes du journal. |
| **C-10** | Relevé en corrigeant C-7 : la fenêtre de la clé API se centrait dans `showEvent` (`adjustSize` puis `QTimer.singleShot`) : affichée d'abord à sa position par défaut, puis déplacée (règle UI n° 5). | Corrigé à la demande de l'utilisateur : `_ApiKeyDialog.show_nonmodal()` fixe la taille d'après le contenu (la fenêtre n'a qu'une largeur minimale ; `adjustSize` n'écrase donc aucune taille imposée), la place par `position_dialog_on_parent`, puis l'affiche ; `show_apikey_dialog`, seul point d'entrée de ses six appelants, l'utilise. Validé dans l'application (test manuel 10 : centrée sur chaque panneau sans saut, langue et thème suivis). |
| **C-12** | Relevé par l'utilisateur en relisant le mode d'emploi : un journal des erreurs par démarrage, créé vide puis supprimé au démarrage suivant s'il l'était resté, aggravé par P3-8 (un fichier vide de plus par process PDF) ; en-têtes des entrées en français en dur ; message d'exception traduit écrit en pIqaD ou tengwar, illisible dans le Bloc-notes ou un mail. | Corrigé à la demande de l'utilisateur : un seul `errors.log`, écrit seulement en cas d'erreur (15 jours, 5 Mo), sous verrou entre process ; plantages natifs par fichiers de secours cachés, versés dans le journal (`collect_crash_buffers`) ; en-têtes traduits (`error_log.entry.*`, par `_wt()`), trace sous `localization.latin_texts()` ; envoi par mail limité aux 7 derniers jours ; anciens fichiers supprimés. Dix clés validées et traduites ; tests réécrits (`test_error_log.py`, dont deux vrais process et l'absence de caractère pIqaD). Validé dans l'application (test manuel 16 : fichier de secours nettoyé à la fermeture, entrées en français puis en klingon latin, fenêtre du journal, envoi par mail, mode d'emploi). |
| **C-14** | Relevé en vérifiant le dossier de l'utilisateur après C-12 : les tests démarrant de vrais process PDF (`test_pdf_probe.py`) écrivaient dans le vrai `%APPDATA%\MosaicView\errors` — fichiers `errors_*.log` vides avec P3-8, puis fichiers de secours `.crash_pdf_*.tmp`, jamais supprimés (process arrêtés de force par les tests). | Corrigé : `tests/conftest.py` redirige `APPDATA` vers un dossier temporaire pour toute la suite, hérité par les process démarrés par les tests (qui reprennent ce même dossier) et supprimé à la fin ; garde-fou `tests/test_check_tests_isolated.py` (2 tests, dont un vrai process) ; suite contrôlée : dossier réel identique avant et après. Fichiers déjà créés laissés en place à la demande de l'utilisateur (supprimés par la nouvelle version à son démarrage). |
| **C-15** | Relevé par l'utilisateur pendant le test de P1-1. Le fichier de test d'alors avait une image saine avec une somme de contrôle fausse : la page, rouge à l'ouverture, revenait normale au collage, une fois sortie de l'archive. L'utilisateur demande alors pourquoi une page lisible n'est pas affichée. Une page dont seule la somme de contrôle est fausse était toujours refusée sans tenter de décoder ses octets, et le comic ne pouvait être enregistré qu'après l'avoir supprimée ou remplacée. Mesures sur de vraies archives abîmées : octets récupérables et image lisible pour un CBZ, un CBR ou un CB7 stockés ; les mêmes archives compressées sont détruites au-delà de l'endroit abîmé. Le fichier de test de P1-1 est refait avec une page réellement abîmée. | Corrigé (1.8.12), conception choisie par l'utilisateur. Les lecteurs ZIP, RAR (UnRAR `-kb`) et 7z rendent les octets dans `AlteredMemberError`. À l'ouverture et à l'ajout seulement (`create_page_entry`), une image qui se décode est affichée avec un cadre orange et l'info-bulle `tooltip.altered`. La marque suit les octets lus (`mark_altered`/`is_altered`) : une retouche l'efface, Annuler la ramène. Les conversions par lot sont inchangées : chaque page se regarde une par une. Une question non modale « Pages peut-être abîmées » est posée avant tout enregistrement qui en écrirait (Enregistrer, Enregistrer sous, Créer un CBZ, Sélection en CBZ si elle en contient), et les marques sont retirées après l'écriture du document entier. Le premier texte de cette fenêtre (« a été modifiée ») a été jugé trompeur à l'essai et reformulé. 6 clés traduites dans les 46 langues et vérifiées. Tests : `tests/test_altered_pages.py` (17 tests, vraies archives). Le glisser-déposer vers l'autre panneau ne gardait la marque que parce que `bytes()` d'un objet `bytes` rend le même objet : la marque y est maintenant reportée explicitement (`_copy_entry`). Validé dans l'application (test manuel 18). |
| **C-16** | Relevé par l'utilisateur pendant le test de P2-3 : à l'ouverture de plusieurs archives illisibles, le titre de la fenêtre « Impossible d'ouvrir le fichier » suit un changement de langue, mais pas son contenu. `LoadWorker.load_multiple_archives` traduisait chaque message (archive refusée, renommage impossible) au moment de l'erreur, et la fenêtre ne recollait que ces phrases figées. Même défaut, en plus visible, pour une archive devenue illisible pendant la lecture (message technique brut, en anglais) et pour la conversion en CBZ de la Bibliothèque (pages illisibles en message brut). Les autres fenêtres « Fichiers ignorés » (presse-papiers, PDF, images, impression, export, import web) traduisaient déjà à l'affichage. | Corrigé : `read_errors.TranslatedLine` (clé, paramètres, raison brute éventuelle), rangée dans la liste des erreurs à la place de la phrase traduite et traduite par `files_ignored_line` à chaque affichage ; `_attach_read_errors` la laisse aux erreurs restantes. Les deux fenêtres d'erreur de l'ouverture groupée et celle de la Bibliothèque passent par `files_ignored_line`. Tests : `tests/test_error_reporting.py` (changement de langue pendant l'affichage, raison d'une archive illisible), `tests/test_read_errors.py` (1 nouveau). Validé dans l'application (test manuel 5). |
| **C-13** | Relevé en traitant C-12 : les lignes d'erreur des journaux des traitements par lot (`Log_*.txt`, ouverts dans le Bloc-notes) sont produites par `_()` : en klingon pIqaD et en langues tengwar, elles s'affichent en carrés. Même règle que l'Audit 7 applique au journal des erreurs. | Corrigé en trois volets. (A) Les threads de lot travaillent sous `latin_texts()` (`_start_batch_thread`, `_LoadWorker.run` et `_SaveWorker.run` de l'import de métadonnées ; les deux lignes d'erreur ajoutées sur le thread de l'interface passent par `_wt`) : les messages d'erreur traduits qu'ils construisent sont en latin. (B) Les textes de progression (`update_progress`, `update_page_bar`) sont envoyés à `_ProgressDialog` comme fonctions, résolues par la fenêtre dans sa langue et sa police, et rejouées par `_retranslate` : la fenêtre garde l'alphabet de la langue et suit un changement de langue pendant le lot (défaut antérieur, règle UI n° 2) ; le libellé de la barre de pages reçoit aussi la police courante. (C) Les en-têtes des 8 journaux, en anglais figé (reste de P2-8), sont traduits : bloc `dialogs.batch_log` (23 clés, 46 langues, vérification par clés), écrits par `log_line`/`log_title` (`_wt`). Garde-fou `tests/test_batch_logs_latin.py` (4 tests). Validé dans l'application (test manuel 17 : en-têtes traduits, journaux en lettres latines en klingon et en sindarin, progression qui suit un changement de langue pendant le lot). |
| **C-11** | Relevé en écrivant les tests manuels de C-9 : les lots d'images (un CBZ par image et un seul CBZ) refusent une image par `raise Exception("invalid or corrupted image: …")`, « unsupported format: ICO files cannot be converted », « unsupported multi-frame image (…) » : textes anglais en dur dans le journal. | Corrigé à la demande de l'utilisateur : trois clés (`messages.errors.batch_image_unreadable`, `batch_image_ico`, `batch_image_multiframe`), validées et traduites dans les 45 autres langues, aux six endroits des deux lots d'images ; détail de Pillow passé par `exception_text`. En klingon, sindarin et quenya, trois mots relevés à la relecture et corrigés avant application (`moj'be'` mal formé ; sindarin `ôl` = rêve et `ribed` absent d'Eldamo ; quenya `ole` → `ola-` « devenir », `ilya` = tous → `rimba` « nombreux »). Validé dans l'application (test manuel 9). |

---

## Error paths audited with no issue found

- **Enregistrement depuis un panneau** : document fermé pendant l'écriture → abandon silencieux, destination intacte ; échec → document resté « modifié », réservations libérées, nouvelle tentative possible ; fichier verrouillé ou en lecture seule → messages dédiés (`FileInUseError`, `ReadOnlyFileError`) ; disque plein contrôlé avant l'écriture.
- **« Enregistrer et fermer »** : un échec laisse le document ouvert ; la fermeture différée n'a lieu qu'avec `success`.
- **Lots de conversion** : page en échec → `_IncompleteCbz`, source gardée ; annulation → CBZ jeté, source gardée, résumé après l'arrêt réel ; timeout PDF → process remplacé.
- **Rotation, redressement, redimensionnement, conversion** : annulation restaurée après l'arrêt réel du worker ; échecs de page listés.
- **Chargement d'une archive seule et d'un PDF** : erreurs affichées, `current_file` remis à zéro, fichiers en attente traités.
- **Macros** : exception d'une étape = échec de l'étape, verrou levé dans un `finally`.
- **ComicVine** : réponses périmées ignorées, bouton OK réactivé après une erreur.
- **Journal des erreurs** : n'échoue jamais (`write_error` sans exception).
- **Remplacement atomique face aux verrous** : vérifié par les essais de l'ancien P0-1 (erreur 32 avant toute modification, nouvelles tentatives, original intact).

**Fausses pistes écartées** : `PdfLoadWorker` face à la mort du process (le `poll` suivi de `is_alive` rend la fenêtre d'exposition négligeable, la mort pendant l'attente fait lever `recv()` dans un `try`) ; rotation et redimensionnement annulés « affichés comme succès » ; recompression annulée (temporaire supprimé, original intact) ; réécriture du ComicInfo.xml par la Bibliothèque sur un CBZ qui n'en a pas (ses appelants exigent qu'il existe).

## Error-state invariants

| Invariant | Garanti par | Violé par |
|---|---|---|
| L'original reste intact tant qu'aucun remplacement contrôlé n'a réussi | `replace_file_atomically` + `check_written_cbz_structure` (panneaux ; désormais aussi recompression, import de métadonnées, Bibliothèque) ; temporaire gardé quand il est la seule copie (`is_kept_temp`) | P3-1, P3-2 (corrigés) |
| Après toute fin, réussie ou en erreur, `operation_running()` est faux | `end_operation` (`finally` ou slot dédié) ; `run()` qui émet toujours un signal de fin | P4-5 (corrigé) |
| Le résumé ne paraît qu'après l'arrêt réel du traitement | lots (`_start_batch_thread`) ; désormais aussi l'import de métadonnées en lot | P1-2 (corrigé), P2-1 |
| Une page n'est retirée que si elle est ailleurs, sous sa forme réelle | `cut_selected` (copie complète avant retrait), page réelle relue (`copy_zip_member_as_stored`) | P1-1 (corrigé) |
| Toute erreur visible a un message | fenêtre « Fichiers ignorés », `ErrorDialog` ; désormais aussi l'ouverture groupée, la Bibliothèque, le presse-papiers, l'export, les macros, la configuration et les journaux de lot | P2-3 à P2-7, P3-4 à P3-6 (corrigés) |
| Le fichier du document correspond aux pages chargées | `_install_loaded_pages` ; désormais `read_archives` dans l'ouverture groupée | P2-9 (corrigé) |
| Un lot va toujours jusqu'à son résumé | `_start_batch_thread` (`_BatchCancelled`, désormais toute exception) ; `_recv` du lot PDF face à un process mort | P2-1 (corrigé) |
| Les réservations sont libérées après la dernière écriture | `_start_batch_thread` (`finally`), `_claim_files_for_write` ; désormais `_finish_cancel` | P1-2 (corrigé) |
| Une ressource système ouverte est refermée | `finally` autour de chaque `OpenClipboard` | P3-3 (corrigé) |

## Missing error/cancellation tests

**Ajoutés** (suite passée de 893 à 906 tests avec les P1, puis à 911 avec les P2, à 932 avec les P3, suites complètes passées ; 955 avec P4-5 et P4-2) :

| Fichier | Couvre |
|---|---|
| `test_clipboard_unreadable_pages.py` (nouveau, 9 tests, 10 cas) | P1-1 : page réelle relue (CRC faux, homonymes), compression non prise en charge refusée, référence de membre posée et jamais périmée, Couper complet, refus avec message (archive modifiée, page sans référence) |
| `test_batch_metadata_cancel.py` (nouveau, 4 tests) | P1-2 : résumé après la fin réelle de l'écriture, fichier compté s'il a été écrit, écriture arrêtée non comptée, fin pendant le branchement, annulation sans écriture |
| `test_error_reporting.py` (nouveau, 4 tests) | P2-1 (process PDF mort : lot terminé, deux PDF tentés, process remplacé, sources gardées), P2-3 (archives toutes invalides : erreur, pas annulation), P2-9 (première archive illisible à la lecture : aucune page partielle, document nommé d'après la suivante), P2-7 (macro non enregistrée : message, fenêtre ouverte) — `_run_pdf_conversion` exécutée en entier par AST avec un faux canal, sans process |
| `test_clipboard_unreadable_pages.py` (1 nouveau) | P2-5 : presse-papiers tenu par un autre programme, message et rien de coupé |
| `test_check_operation_end.py` (nouveau, 14 tests), `test_pdf_channel_reader.py` (nouveau, 9 tests) | P4-5 et P4-2 (voir leur tableau ; suite : 955 tests) |
| `test_check_dialog_show_nonmodal.py` (nouveau, 2 tests) | P4-4 (suite : 957 tests) |
| `test_check_atomic_file_writes.py` (4 nouveaux) | P4-3 : contrôle déclaré et présent pour chaque écrivain de ZIP (suite : 961 tests) |
| `test_check_plain_text_labels.py` (3 nouveaux) | C-4 : aucun `QLabel` construit par une f-string hors des cas justifiés ; noms de pages en `PlainText` (suite : 964 tests) |
| `test_batch_metadata_comicinfo.py` (nouveau, 3 tests) | C-5 : seul le ComicInfo.xml de la racine pré-remplit l'import en lot (suite : 967 tests) |
| `test_altered_pages.py` (nouveau, 17 tests) | C-15 : page au CRC faux dans de vraies archives ZIP, 7z et RAR (affichée et marquée si elle se décode, rouge sinon), marque qui suit les octets, page déplacée vers l'autre panneau qui la garde, TIFF de plusieurs pages, question avant l'enregistrement (suite : 998 tests) |
| `test_audit7_p3.py` (nouveau, 21 tests) | P3-1 (`ReplaceFileW` simulé : temporaire gardé, ignoré par le ménage ; temporaire ordinaire supprimé), P3-2 (`_CancellableZip` et contrôle de structure), P3-3 (presse-papiers refermé), P3-4 (`exception_text`), P3-5 (`_log_label_text`), P3-6 (handler, clé non chiffrée, fenêtre de la clé API, un seul avertissement par session), P3-8 (trace d'une erreur signalée, journal d'un process enfant, appels présents dans `ErrorDialog` et les deux process PDF) |

**Toujours manquants** :
- P2-2 : nombre écrit contre nombre attendu avec un ajout appliqué en fin d'écriture — la suite d'enregistrement est dans des fonctions imbriquées et des fenêtres Qt ;
- P2-4, P2-6 : messages de la Bibliothèque et de l'export interrompu — fenêtres Qt ;
- P3-2 : contrôle dans la recompression et dans la Bibliothèque — fonctions imbriquées ou méthodes de fenêtre ;
- P3-7 : aucun CBZ vide — fonction imbriquée dans un lot à fenêtre de progression.

**Tests déterministes proposés** : faux `Connection` qui lève `EOFError` au k-ième `recv` ; `ReplaceFileW`/`os.replace` remplacés par des fonctions qui échouent à l'étape N ; faux `QThread` synchrone pour les orchestrateurs (modèle de `test_batch_metadata_cancel.py`) ; `OpenClipboard` simulé qui lève.

## Recommended fixes

À la demande de l'utilisateur, les deux P0 puis les quatre P1 ont été vérifiés avant toute correction (P0-1, P0-2 → P3 ; P1-3 → P4 ; P1-4 → P2), puis les deux P1 réels ont été corrigés et couverts par des tests automatiques ; suite complète passée (906 tests) ; tests dans l'application faits depuis (tests manuels 1 à 4). Entrée ajoutée à la section [1.8.12] du CHANGELOG ; skills `clipboard`, `archive-image-loading` et `batch-metadata-import` mis à jour ; compteurs de tests de `CLAUDE.md` et `RELEASE.txt` mis à jour. Restent :

1. ~~**P2**~~ — faits : les cinq P2 vérifiables sans Qt ont d'abord été reproduits (tous confirmés), puis les neuf ont été corrigés d'une traite, dix nouvelles clés traduites et vérifiées ; tests automatiques pour P2-1, P2-3, P2-5, P2-7, P2-9. Tests dans l'application faits depuis (tests manuels 5, 6, 9, 17). Le reste de P2-8 (lignes « Fichiers ignorés » de l'ouverture groupée et en-têtes anglais des journaux, résolus à l'écriture) a été corrigé par C-13 et C-16.
2. ~~**P3**~~ — faits : P3-3 à P3-7 reproduits avant correction, puis les huit corrigés d'une traite (P3-1 en défense), avec C-7 ; six nouvelles clés traduites et contrôlées ; 21 tests automatiques. Suite complète passée (932 tests) ; tests dans l'application faits depuis (tests manuels 7, 8, 9, 16).
3. **P4** — P4-5, P4-2, P4-4 et P4-3 faits (tests automatiques ; suite : 961 tests). Reste P4-1 (harmonisation, sans effet visible).
4. ~~**Hors périmètre**~~ — tous faits : C-4 (sécurité, trois affichages de noms de pages, garde-fou étendu), C-5 (règle ComicInfo.xml dans l'import en lot ; suite : 967 tests), C-6 (code mort supprimé), C-9, C-10 et C-11. Seul P4-1 reste, laissé de côté par décision de l'utilisateur (harmonisation sans effet visible, risquée).
