---
name: file-picker
description: Localiser ou modifier la fenêtre de choix d'un fichier ou d'un dossier de MosaicView (non modale, à la place des boîtes de Windows QFileDialog.get*(), interdites) — ouverture, enregistrement, choix de dossier, volet des emplacements et arborescence des dossiers (dépliage jusqu'au dossier affiché, surveillance des dossiers et des lecteurs), Nouveau dossier, Renommer, Supprimer (corbeille), message d'erreur. Utiliser dès qu'une tâche touche à file_picker_qt.py (FilePickerWindow, ask_open_file, ask_open_files, ask_save_file, ask_directory, file_filter, file_filter_text, _FileProxy, _RenameDelegate), file_picker.py (start_location, resolve, with_extension, open_file_under, drive_label, subfolders, path_parts, drive_roots, NavigationHistory…), _reveal_in_places, _sync_children, _sync_drives, aux clés dialogs.file_picker.*, ou à tout nouveau choix de fichier ou de dossier dans l'application.
---

# Fenêtre de choix d'un fichier ou d'un dossier — MosaicView

Deux modules :

| Fichier | Rôle |
|---|---|
| [modules/qt/file_picker_qt.py](../../../modules/qt/file_picker_qt.py) | La fenêtre (`FilePickerWindow`) et les quatre fonctions d'appel |
| [modules/qt/file_picker.py](../../../modules/qt/file_picker.py) | Règles sans Qt : point de départ, chemin tapé, noms multiples, filtres et extension, dossier parent, historique, nom libre d'un nouveau dossier, nom des lecteurs, comic ouvert sous un chemin, arborescence du volet de gauche (`subfolders`, `path_parts`, `drive_roots`). Testées par `tests/test_file_picker.py` |

**Pourquoi elle existe** : les boîtes de Windows (`QFileDialog.getOpenFileName(s)`, `getSaveFileName`, `getExistingDirectory`) lancent leur propre boucle d'événements et gèlent toute l'application, l'autre panneau compris (règle UI n° 4 de `CLAUDE.md`), et ne suivent ni la langue (pIqaD, tengwar), ni le thème, ni la police. Elles sont interdites : `tests/test_check_no_modal.py` (`STATIC_MODAL`) refuse tout appel `QFileDialog.get*`. Un `QFileDialog` non natif n'est pas une solution : ses textes internes restent en anglais (l'application ne charge aucune traduction de Qt), il ouvre lui-même des `QMessageBox` modales et ses menus et bulles d'aide sont natifs.

## Appeler la fenêtre

```python
from modules.qt.file_picker_qt import ask_open_file, ask_open_files, ask_save_file, ask_directory
from modules.qt.file_picker_qt import file_filter, file_filter_text

ask_save_file(parent, "clé.du.titre", départ,
              [file_filter("dialogs.file_filters.ext_files", "*.cbz", ext="CBZ")],
              on_result)                      # on_result(chemin), "" si annulé
ask_open_files(parent, "clé.du.titre", départ, filtres, on_result)   # liste, [] si annulé
ask_directory(parent, "clé.du.titre", départ, on_result)             # chemin, "" si annulé
```

- **Titre** : sa clé de traduction, jamais un texte traduit (barre dessinée par Qt, skill `window-title`).
- **Départ** : dossier, fichier (nom proposé à l'enregistrement, extension sélectionnée à part) ou `""` (dossier personnel). Un dossier disparu remonte à son plus proche parent existant (`start_location`, `existing_ancestor`) ; en mode dossier, `départ` n'est jamais pris pour un nom de fichier.
- **Filtres** : `file_filter(clé, motifs…, **params)` → libellé traduit suivi de ses motifs (« Fichiers CBZ (*.cbz) ») ; `file_filter_text(clé, motifs…)` pour une clé dont la traduction contient déjà ses motifs (`dialogs.open_file.filter_archives`), les motifs étant répétés dans le code pour le tri des fichiers — depuis les listes communes (`archive_names.IMAGE_EXTS`, `COMIC_EXTS`), jamais recopiés. Libellés retraduits à chaque changement de langue.
- **`center_on`** : widget de centrage autre que `parent` (parent masqué : `LibraryWindow._dialog_anchor()`, skill `library`).
- **Parent** : la fenêtre de choix est son enfant, donc détruite avec lui ; sa réponse ne vise jamais une fenêtre détruite (ex. `NewDbDialog`, `_MacroReadDialog`).

### La suite est différée : tout revérifier

La fenêtre est non modale : pendant le choix, l'utilisateur a pu fermer ou changer le document, démarrer une opération, changer de panneau. La suite revérifie donc ce dont elle dépend, comme toute réponse non modale (`CLAUDE.md`, règle UI n° 4) :
- document d'un panneau : `state` capturé avant l'appel (jamais le singleton), `doc_generation` inchangé, `operation_running()` faux — `file_operations_qt._choice_still_valid` / `_ask_cbz_path` (skill `save-export`), `thumbnail_export_qt.export_thumbnails` ;
- page visée : même objet encore dans le document (`PanelWidget._replace_corrupted_image`, skill `corrupted-images`) ;
- ajout de pages : `state.run_when_idle` (`PanelWidget._open_file_dialog`, skill `undo-redo`), et rien si un autre comic a été ouvert dans le panneau pendant le choix (`doc_generation` changé, panneau non vide ; panneau vidé : les fichiers y sont ouverts) ;
- Bibliothèque : même base ouverte (`self._db is db`), aucun chargement (skill `library`).

**Piège** : la suite d'une commande est souvent une fonction imbriquée (`_on_path_chosen`, `_convert_to`). Elle doit être définie avant l'appel qui ouvre la fenêtre, ou avant `_run_validation_chain`, qui peut appeler `_on_validated` tout de suite.

## La fenêtre — `FilePickerWindow(QuestionWindowMixin, QDialog)`

Quatre modes (`OPEN_FILE`, `OPEN_FILES`, `SAVE_FILE`, `DIRECTORY`), réponse par `result_signal` (`ask_async`), une seule fois ; croix, Échap et Annuler répondent `""` (`[]` en `OPEN_FILES`). Fenêtre de type `Qt.Window` (Agrandir), redimensionnable, 900 × 560.

- **Barre de navigation** : Précédent, Suivant, Dossier parent — icônes de l'application (`BTN_Arrow_LEFT.png`, `BTN_Arrow_RIGHT.png` de la colonne d'icônes, `Folder-up.png` de la mosaïque), bulles d'aide `OverlayTooltip` ; barre d'adresse modifiable (`resolve` : relatif au dossier affiché, `%VAR%`, `~`, « C: » = racine ; un fichier tapé est choisi en mode ouverture) ; bouton Nouveau dossier. Clavier : Retour arrière (hors zone de saisie), Alt+←/→/↑. Historique : `NavigationHistory`.
- **Volet de gauche — arborescence** (`self._places`, `QTreeWidget` sans en-tête, séparé de la liste par un `QSplitter` : poignée pour l'élargir, 230 px au départ, 140 au moins ; colonne à la largeur des noms, `ResizeToContents`, un nom long fait défiler le volet horizontalement). En haut, Ce PC (liste des lecteurs, chemin `COMPUTER` = `""`, `_computer_item`) et les raccourcis Bureau, Documents, Téléchargements, Images (`_PLACES`, `QStandardPaths`, montrés s'ils existent), qui ne se déplient pas ; puis les lecteurs (`_drive_items`, racine `_norm` → nœud), « Nom (C:) » comme l'Explorateur (`drive_label` + `drive_display`). `drive_label` lit le nom du volume (`GetVolumeInformationW`) ou, pour un lecteur réseau, le chemin du partage dans la table des connexions (`WNetGetConnectionW`, sans contacter le serveur) ; un lecteur de CD/DVD n'est pas interrogé (un disque à faire tourner bloquerait l'ouverture). Le nom vient du disque : caractères de contrôle retirés. Données des nœuds : `_PATH_ROLE` (chemin), `_KEY_ROLE` (clé du libellé traduit, retraduit par `_retranslate` sur les nœuds de premier niveau), `_LOADED_ROLE` (sous-dossiers lus).
  - **Dépliage** : un lecteur ou un dossier montre sa flèche avant toute lecture (`ShowIndicator`, `_new_folder_item`) ; ses sous-dossiers ne sont lus qu'au premier dépliage (`itemExpanded` → `_fill_children`), par `file_picker.subfolders` (dossiers seulement, ni cachés ni système, tri naturel `archive_names.natural_key`, `[]` si illisible), une seule fois : le nœud est ensuite surveillé.
  - **Suivi du dossier affiché** : `_go_to` appelle `_reveal_in_places` : tout se replie (`collapseAll`), puis le chemin est déplié sous son lecteur (`file_picker.path_parts` : racine et dossiers successifs ; un chemin réseau sans lettre ne met rien en évidence), le dossier affiché devient l'élément courant et il est centré verticalement par `_center_place_vertically` (`scrollToItem(…, PositionAtCenter)`, refait au tour suivant par `_center_current_place` : les nœuds tout juste dépliés ne sont placés qu'au prochain calcul de la vue, sans cela le dossier restait collé en bas). **Piège** : dans un `QTreeView`, `PositionAtCenter` centre aussi horizontalement, sur le milieu de la colonne (largeur du nom le plus long, `ResizeToContents`) : le volet partait vers la droite. `_center_place_vertically` ramène donc le défilement horizontal à gauche (`horizontalScrollBar().setValue(0)`) après chaque centrage ; tout nouveau centrage dans le volet passe par elle. Un dossier ouvert par un raccourci est déplié sous son lecteur réel, jamais sous le raccourci : un seul chemin déplié. Un dossier caché ou introuvable arrête le dépliage sur son plus proche parent. Une branche dépliée à la main se replie au changement de dossier suivant (choix de l'utilisateur).
  - **Clic ou Entrée** sur un nœud (`_on_place_clicked`) : `_go_to` au tour suivant de la boucle d'événements (`QTimer.singleShot(0, self, …)`). **Piège** : `_go_to` peut retirer des nœuds de l'arbre (dossier disparu), dont celui qui émet le signal ; jamais pendant son émission.
  - **Surveillance des dossiers** : tout nœud lu est ajouté à un `QFileSystemWatcher` (`self._watched`, chemin `_norm` → nœud). `directoryChanged` → `_on_folder_changed` → `_sync_children`, qui aligne les sous-dossiers sur le disque **sur place** : dossier disparu retiré (et ses descendants ne sont plus surveillés, `_forget`), nouveau inséré à sa place dans l'ordre, nœuds restants gardés avec leur état déplié. **Piège** : relire tout le chemin à chaque changement de dossier (nœuds détruits puis recréés) causait une latence gênante ; la surveillance rend cette relecture inutile, seul un dossier jamais lu est lu. Le dossier affiché supprimé : la fenêtre remonte à son plus proche parent existant (`existing_ancestor`).
  - **Lecteurs branchés ou retirés** : minuterie d'une seconde (`_drives_timer`, arrêtée par `_answer`) → `_check_drives` → `_sync_drives`, qui compare `file_picker.drive_roots()` (masque `GetLogicalDrives`, aucun accès aux disques) à la liste connue : lecteur apparu inséré dans l'ordre des lettres après les raccourcis, lecteur retiré enlevé (`_forget`). Le dossier affiché sur un lecteur retiré : retour à son plus proche parent existant, ou Ce PC.
- **Liste** : `QFileSystemModel` (dossiers, fichiers sauf en mode dossier, filtres par `setNameFilters`, fichiers cachés exclus) derrière `_FileProxy` : titres de colonnes traduits, taille par `utils.format_file_size`, date « AAAA-MM-JJ HH:MM » (`format_date`), colonne Type masquée, dossiers en tête dans les deux sens du tri, tri naturel des noms (`archive_loader._natural_sort_key`), glisser-déposer retiré. Double-clic ou Entrée : un dossier s'ouvre, un fichier est choisi. Sélection multiple dans tous les modes (Supprimer) ; le champ du nom reprend les fichiers sélectionnés (les dossiers en mode dossier, entre guillemets en `OPEN_FILES` : `join_names` / `split_names`).
- **Bas de fenêtre** : nom (« Nom du fichier » ou « Dossier ») et filtre de types, puis la rangée **message — Valider — Annuler**. Aucun bouton n'est « par défaut » (`setAutoDefault(False)`) : Entrée dans la barre d'adresse navigue, Entrée dans le champ du nom valide.
- **Message d'erreur** (`_set_status(callable)`, retraduit par `_retranslate`) : à gauche des boutons, en rouge (`theme["memory_red"]`), **à place fixe** — hauteur de deux lignes posée par `_retranslate` d'après la police, largeur prise sur l'espace libre (`QSizePolicy.Ignored`), jamais masqué, seulement vidé. **Piège** : un message qui apparaît dans le layout (`show()`/`hide()`) fait bouger toute la fenêtre. Texte en `Qt.PlainText` (il cite des noms de fichiers). **Effacé dès que l'utilisateur agit**, jamais par une minuterie (un message qui disparaît seul peut ne pas avoir été lu) : changement de sélection (`_on_selection_changed`), frappe dans le champ du nom ou la barre d'adresse (`textEdited`, pas `textChanged` : le texte posé par le code ne l'efface pas), changement de filtre (`_apply_filter`), changement de dossier (`_go_to`), et au début de chaque action (`_accept`, `_create_folder`, `_rename_selected`, `_delete_selected`), qui affiche ensuite son propre message si elle échoue. Bouger la souris ou faire défiler la liste ne l'efface pas. Une nouvelle action qui peut afficher un message commence par `_set_status(None)`.

### Validation

- **Dossier** : le dossier tapé ou sélectionné, sinon celui affiché (pas « Ce PC »).
- **Ouverture** : chaque nom doit être un fichier existant ; un nom qui est un dossier y navigue.
- **Enregistrement** : nom validé par `_valid_name` (`utils.windows_safe_component` : ni caractère interdit, ni point ou espace final, ni nom réservé) ; extension du filtre ajoutée si le nom ne répond à aucun de ses motifs (`with_extension`, comme la boîte de Windows) ; fichier existant → `ConfirmYNDialog` non modal « Remplacer le fichier » ; une nouvelle validation pendant la question la rend caduque (jeton `_overwrite_token`). Testé, avec Renommer et Supprimer (`rename_item`, `_delete_selected`), par `tests/test_file_picker_actions.py`.
- **Fermeture avant la réponse** : `FilePickerWindow._answer` masque la fenêtre, puis répond au tour suivant de la boucle d'événements (`QTimer.singleShot(0, self, …)`, rattaché à la fenêtre : détruite avec son parent entre-temps, elle ne répond pas). **Piège** : répondre d'abord laisserait la fenêtre affichée par-dessus le texte rouge de progression de l'enregistrement qui suit, jusqu'à la fin de l'écriture.

### Nouveau dossier, Renommer, Supprimer

Clic droit sur la liste (`_show_list_menu`, menu `_themed_menu_stylesheet`, `deleteLater` après `exec`, skill `qt-context-menus`) : Nouveau dossier, Renommer (F2), Supprimer (Suppr). Un clic droit sur un élément non sélectionné le sélectionne seul ; sur un vide, retire la sélection. Les lecteurs (liste « Ce PC ») ne se renomment ni ne se suppriment.

- **Nouveau dossier** : `QFileSystemModel.mkdir`, nom libre « Nouveau dossier (2) »… (`unique_folder_name`) en alphabet latin (`_wt` : il est écrit sur le disque), nom ouvert aussitôt à la saisie.
- **Renommer** : saisie dans la liste (`_RenameDelegate`, zone de saisie au menu traduit). Le modèle est en écriture (`setReadOnly(False)`), mais `_FileProxy.flags` ne rend modifiable que l'élément dont le renommage a été demandé (`_renaming`, `is_renamable`) et la vue n'a aucun déclencheur d'édition (`NoEditTriggers`, `edit()` appelé par le code seulement). Nom contrôlé par `_valid_name`, puis `setData` (aucune fenêtre de Qt en cas d'échec : message `rename_failed`).
- **Supprimer** : `ConfirmYNDialog` (« Mettre à la corbeille », un élément ou `{count}`), puis `utils.send_to_trash` pour chacun (seule mise à la corbeille de l'application, jamais de suppression définitive) ; premier échec → message `delete_failed` (`temp_files.exception_text`). Un élément qui ne peut pas aller à la Corbeille (clé USB, partage réseau, Corbeille désactivée, fichier trop gros : `recycle_bin.trash_problem`, skill `save-export`) est annoncé sous la question (`ConfirmYNDialog(…, warning=…)` : raison pour un élément, `messages.trash.many_permanent` avec leur nombre pour plusieurs).
- **Refus** (`_refuse_if_in_use`, relu à la demande et à la réponse de la confirmation) : élément qui est, ou dossier qui contient, un fichier ouvert dans l'application (`open_file_under` sur la liste de `_open_documents` : `current_file` de chaque panneau de `MainWindow._all_panels`, trouvés quelle que soit la fenêtre parente, et base de la Bibliothèque, `library_window._library_window._db.db_path`, skill `library`) → `file_open` (« ouvert dans MosaicView ») ; ou un fichier que l'application lit ou va lire (`_files_in_use` : `PanelWidget._files_in_use` de chaque panneau — archives d'un chargement en cours, fichiers en attente `_pending_load_paths`/`_queued_image_loads`/`_pdf_merge_queue`, images en cours de chargement, archive d'où une page illisible serait relue `entry["unread_member"]["path"]` —, plus les lectures inscrites dans `modules/qt/files_in_use.py` : archive en cours d'ajout, `import_merge_qt._start_worker`, et PDF en cours d'ajout de la question à la fin, `PanelWidget._merge_next_pdf`) → `file_in_use` (« en cours d'utilisation, réessayez une fois l'opération terminée ») ; ou un fichier d'un lot en cours (`batch_claims.batch_file_under`, skill `batch-processing`) → `messages.errors.file_in_batch`. Un nouveau type de fichier tenu ouvert par l'application s'ajoute à `_open_documents` ; une nouvelle lecture d'un fichier hors du document (ajout, import…) s'inscrit par `files_in_use.hold(chemin)` et se retire par `release(jeton)` sur tous ses chemins de fin, ou s'ajoute à `PanelWidget._files_in_use`. **Même fichier par deux chemins** : `open_file_under` compare le chemin écrit et le chemin réel (`_path_keys`, `os.path.realpath` : jonction, lien, lecteur subst ou réseau). Testé par `tests/test_files_in_use.py` et `tests/test_file_picker.py`. Enregistrer par-dessus le comic ouvert reste permis : ce n'est pas ce chemin.

## Langue, thème, police

`_apply_theme` (feuille de style complète : liste, en-têtes, champs, liste déroulante, boutons, bouton à icône `#navButton` sans marge) et `_retranslate` (textes, libellés des filtres en conservant le filtre choisi, emplacements, bulles d'aide, en-têtes par `headerDataChanged`, `get_current_font()` sur chaque widget, hauteur du message) sont rejoués à chaque `language_signal` par `AppWindowMixin._connect_language` (skill `window-title`). Clés : `dialogs.file_picker.*` (skill `add-translation`).

## Comment modifier

- **Nouveau choix de fichier ou de dossier** : une des quatre fonctions `ask_*`, jamais `QFileDialog`, et une suite qui revérifie ce qui a pu changer (section « La suite est différée »).
- **Nouvelle règle de chemin ou de nom** : dans `file_picker.py`, avec son test dans `tests/test_file_picker.py`.
- **Nouvelle commande du clic droit** : `_show_list_menu`, texte par une clé `dialogs.file_picker.*`, refus par `_refuse_if_in_use` si elle touche à un fichier sur le disque.
- **Nouveau texte affiché** : `_retranslate` (police comprise) ; un message d'erreur passe par `_set_status(lambda: _("clé", …))`.

## Références croisées

- `window-title` — `AppWindowMixin` / `QuestionWindowMixin`, barre de titre, `show_nonmodal`.
- `save-export` — `_ask_cbz_path`, `_choice_still_valid`, export vers un dossier, export des vignettes.
- `batch-processing` — `_ask_batch_directory`, `batch_file_under`.
- `library` — choix d'une base, des dossiers, export Excel, `_dialog_anchor`.
- `corrupted-images` — remplacement d'une image abîmée.
- `qt-context-menus`, `qt-tooltips`, `dark-mode`, `fonts` — menu du clic droit, bulles d'aide, thème, police.
- `add-translation` — filtres (`file_filter`, `file_filter_text`) et clés `dialogs.file_picker.*`.
