---
name: file-picker
description: Localiser ou modifier la fenêtre de choix d'un fichier ou d'un dossier de MosaicView (non modale, à la place des boîtes de Windows QFileDialog.get*(), interdites) — ouverture, enregistrement, choix de dossier, volet des emplacements et lecteurs, Nouveau dossier, Renommer, Supprimer (corbeille), message d'erreur. Utiliser dès qu'une tâche touche à file_picker_qt.py (FilePickerWindow, ask_open_file, ask_open_files, ask_save_file, ask_directory, file_filter, file_filter_text, _FileProxy, _RenameDelegate), file_picker.py (start_location, resolve, with_extension, open_file_under, drive_label, NavigationHistory…), aux clés dialogs.file_picker.*, ou à tout nouveau choix de fichier ou de dossier dans l'application.
---

# Fenêtre de choix d'un fichier ou d'un dossier — MosaicView

Deux modules :

| Fichier | Rôle |
|---|---|
| [modules/qt/file_picker_qt.py](../../../modules/qt/file_picker_qt.py) | La fenêtre (`FilePickerWindow`) et les quatre fonctions d'appel |
| [modules/qt/file_picker.py](../../../modules/qt/file_picker.py) | Règles sans Qt : point de départ, chemin tapé, noms multiples, filtres et extension, dossier parent, historique, nom libre d'un nouveau dossier, nom des lecteurs, comic ouvert sous un chemin. Testées par `tests/test_file_picker.py` |

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
- ajout de pages : `state.run_when_idle` (`PanelWidget._open_file_dialog`, skill `undo-redo`) ;
- Bibliothèque : même base ouverte (`self._db is db`), aucun chargement (skill `library`).

**Piège** : la suite d'une commande est souvent une fonction imbriquée (`_on_path_chosen`, `_convert_to`). Elle doit être définie avant l'appel qui ouvre la fenêtre, ou avant `_run_validation_chain`, qui peut appeler `_on_validated` tout de suite.

## La fenêtre — `FilePickerWindow(QuestionWindowMixin, QDialog)`

Quatre modes (`OPEN_FILE`, `OPEN_FILES`, `SAVE_FILE`, `DIRECTORY`), réponse par `result_signal` (`ask_async`), une seule fois ; croix, Échap et Annuler répondent `""` (`[]` en `OPEN_FILES`). Fenêtre de type `Qt.Window` (Agrandir), redimensionnable, 900 × 560.

- **Barre de navigation** : Précédent, Suivant, Dossier parent — icônes de l'application (`BTN_Arrow_LEFT.png`, `BTN_Arrow_RIGHT.png` de la colonne d'icônes, `Folder-up.png` de la mosaïque), bulles d'aide `OverlayTooltip` ; barre d'adresse modifiable (`resolve` : relatif au dossier affiché, `%VAR%`, `~`, « C: » = racine ; un fichier tapé est choisi en mode ouverture) ; bouton Nouveau dossier. Clavier : Retour arrière (hors zone de saisie), Alt+←/→/↑. Historique : `NavigationHistory`.
- **Volet de gauche** : Ce PC (liste des lecteurs, chemin `COMPUTER` = `""`), Bureau, Documents, Téléchargements, Images (`QStandardPaths`, montrés s'ils existent), puis les lecteurs, « Nom (C:) » comme l'Explorateur (`drive_label` + `drive_display`). `drive_label` lit le nom du volume (`GetVolumeInformationW`) ou, pour un lecteur réseau, le chemin du partage dans la table des connexions (`WNetGetConnectionW`, sans contacter le serveur) ; un lecteur de CD/DVD n'est pas interrogé (un disque à faire tourner bloquerait l'ouverture). Le nom vient du disque : caractères de contrôle retirés.
- **Liste** : `QFileSystemModel` (dossiers, fichiers sauf en mode dossier, filtres par `setNameFilters`, fichiers cachés exclus) derrière `_FileProxy` : titres de colonnes traduits, taille par `utils.format_file_size`, date « AAAA-MM-JJ HH:MM » (`format_date`), colonne Type masquée, dossiers en tête dans les deux sens du tri, tri naturel des noms (`archive_loader._natural_sort_key`), glisser-déposer retiré. Double-clic ou Entrée : un dossier s'ouvre, un fichier est choisi. Sélection multiple dans tous les modes (Supprimer) ; le champ du nom reprend les fichiers sélectionnés (les dossiers en mode dossier, entre guillemets en `OPEN_FILES` : `join_names` / `split_names`).
- **Bas de fenêtre** : nom (« Nom du fichier » ou « Dossier ») et filtre de types, puis la rangée **message — Valider — Annuler**. Aucun bouton n'est « par défaut » (`setAutoDefault(False)`) : Entrée dans la barre d'adresse navigue, Entrée dans le champ du nom valide.
- **Message d'erreur** (`_set_status(callable)`, retraduit par `_retranslate`) : à gauche des boutons, en rouge (`theme["memory_red"]`), **à place fixe** — hauteur de deux lignes posée par `_retranslate` d'après la police, largeur prise sur l'espace libre (`QSizePolicy.Ignored`), jamais masqué, seulement vidé. **Piège** : un message qui apparaît dans le layout (`show()`/`hide()`) fait bouger toute la fenêtre. Texte en `Qt.PlainText` (il cite des noms de fichiers). **Effacé dès que l'utilisateur agit**, jamais par une minuterie (un message qui disparaît seul peut ne pas avoir été lu) : changement de sélection (`_on_selection_changed`), frappe dans le champ du nom ou la barre d'adresse (`textEdited`, pas `textChanged` : le texte posé par le code ne l'efface pas), changement de filtre (`_apply_filter`), changement de dossier (`_go_to`), et au début de chaque action (`_accept`, `_create_folder`, `_rename_selected`, `_delete_selected`), qui affiche ensuite son propre message si elle échoue. Bouger la souris ou faire défiler la liste ne l'efface pas. Une nouvelle action qui peut afficher un message commence par `_set_status(None)`.

### Validation

- **Dossier** : le dossier tapé ou sélectionné, sinon celui affiché (pas « Ce PC »).
- **Ouverture** : chaque nom doit être un fichier existant ; un nom qui est un dossier y navigue.
- **Enregistrement** : nom validé par `_valid_name` (`utils.windows_safe_component` : ni caractère interdit, ni point ou espace final, ni nom réservé) ; extension du filtre ajoutée si le nom ne répond à aucun de ses motifs (`with_extension`, comme la boîte de Windows) ; fichier existant → `ConfirmYNDialog` non modal « Remplacer le fichier » ; une nouvelle validation pendant la question la rend caduque (jeton `_overwrite_token`).
- **Fermeture avant la réponse** : `FilePickerWindow._answer` masque la fenêtre, puis répond au tour suivant de la boucle d'événements (`QTimer.singleShot(0, self, …)`, rattaché à la fenêtre : détruite avec son parent entre-temps, elle ne répond pas). **Piège** : répondre d'abord laisserait la fenêtre affichée par-dessus le texte rouge de progression de l'enregistrement qui suit, jusqu'à la fin de l'écriture.

### Nouveau dossier, Renommer, Supprimer

Clic droit sur la liste (`_show_list_menu`, menu `_themed_menu_stylesheet`, `deleteLater` après `exec`, skill `qt-context-menus`) : Nouveau dossier, Renommer (F2), Supprimer (Suppr). Un clic droit sur un élément non sélectionné le sélectionne seul ; sur un vide, retire la sélection. Les lecteurs (liste « Ce PC ») ne se renomment ni ne se suppriment.

- **Nouveau dossier** : `QFileSystemModel.mkdir`, nom libre « Nouveau dossier (2) »… (`unique_folder_name`) en alphabet latin (`_wt` : il est écrit sur le disque), nom ouvert aussitôt à la saisie.
- **Renommer** : saisie dans la liste (`_RenameDelegate`, zone de saisie au menu traduit). Le modèle est en écriture (`setReadOnly(False)`), mais `_FileProxy.flags` ne rend modifiable que l'élément dont le renommage a été demandé (`_renaming`, `is_renamable`) et la vue n'a aucun déclencheur d'édition (`NoEditTriggers`, `edit()` appelé par le code seulement). Nom contrôlé par `_valid_name`, puis `setData` (aucune fenêtre de Qt en cas d'échec : message `rename_failed`).
- **Supprimer** : `ConfirmYNDialog` (« Mettre à la corbeille », un élément ou `{count}`), puis `utils.send_to_trash` pour chacun (seule mise à la corbeille de l'application, jamais de suppression définitive) ; premier échec → message `delete_failed` (`temp_files.exception_text`).
- **Refus** (`_refuse_if_in_use`, relu à la demande et à la réponse de la confirmation) : élément qui est, ou dossier qui contient, un fichier ouvert dans l'application (`open_file_under` sur la liste de `_open_documents` : `current_file` de chaque panneau de `MainWindow._all_panels`, trouvés quelle que soit la fenêtre parente, et base de la Bibliothèque, `library_window._library_window._db.db_path`, skill `library`) → `file_open` (« ouvert dans MosaicView ») ; ou un fichier d'un lot en cours (`batch_claims.batch_file_under`, skill `batch-processing`) → `messages.errors.file_in_batch`. Un nouveau type de fichier tenu ouvert par l'application s'ajoute à `_open_documents`. Enregistrer par-dessus le comic ouvert reste permis : ce n'est pas ce chemin.

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
