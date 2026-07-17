---
name: printing
description: Localiser ou modifier l'impression Windows de MosaicView (sélection ou BD entière via le PhotoPrintingWizard du Shell). Utiliser dès qu'une tâche touche à printing_qt.py, print_selection/print_all, ou aux boutons/menus "Imprimer".
---

# Impression Windows — MosaicView

Impression des pages de la BD (sélection ou totalité) via le dialogue natif **PhotoPrintingWizard** de l'Explorateur Windows — le même wizard que celui qui s'ouvre quand on fait clic droit → Imprimer sur une sélection de photos dans l'Explorateur. Tout tient dans un seul fichier : `modules/qt/printing_qt.py`.

## Vue d'ensemble du mécanisme

MosaicView ne dessine aucune UI d'impression lui-même — il pilote le composant COM Windows du wizard photo :

1. Les images à imprimer (`entry["bytes"]`) sont assemblées en un **TIFF multi-pages** dans un dossier temporaire (`print_<timestamp>.tiff`), dans un `QThread` séparé pour ne pas geler l'UI.
2. Ce TIFF est aussitôt **ré-éclaté en JPEG** un par un (`{i:04d}.jpg`) dans un sous-dossier dédié (`printjob_<timestamp>/`) — étape intermédiaire nécessaire car le wizard attend une liste de fichiers image, pas un TIFF multi-pages.
3. Le dossier de JPEG est transformé en objet COM `IDataObject` (`SHCreateDataObject`), puis "déposé" par simulation de drag & drop (`IDropTarget::DragEnter` + `::Drop`) sur une instance COM du PhotoPrintingWizard (CLSID `{60fd46de-f830-4894-a628-6fa81bc0190d}`) — exactement comme si l'utilisateur avait fait glisser les fichiers sur le wizard.

Tout est fait en `ctypes` brut (pas de lib Python d'impression, pas de dépendance externe) — `PRINT_AVAILABLE = True` en dur, le mécanisme n'utilisant que `shell32`/`ole32`, toujours présents sur Windows.

## Pourquoi passer par TIFF puis re-éclater en JPEG

Ça peut sembler un détour inutile (JPEG → TIFF → JPEG), mais :
- Le TIFF multi-pages sert de **conteneur intermédiaire unique et ordonné** produit par le worker thread à partir des `entry["bytes"]` d'origine (formats hétérogènes : PNG, JPEG, GIF, BMP...), avec conversion systématique en RGB (aplatissement de la transparence sur fond blanc pour RGBA/LA/P — voir `_PrintWorker.run()`).
- Le wizard Windows attend des **fichiers sur disque** listés par PIDL, pas des données en mémoire — d'où le re-éclatement en JPEG nommés `0000.jpg`, `0001.jpg`... (ordre alphanumérique = ordre de lecture) juste avant l'appel COM.

## Les deux points d'entrée publics

- **`print_selection(parent, canvas, state)`** — imprime `state.selected_indices`, filtré aux entrées `is_image` et non `is_corrupted` (voir skill `corrupted-images`). Vide → `ErrorDialog` "aucune sélection"/"aucune image valide dans la sélection".
- **`print_all(parent, canvas, state)`** — même filtre mais sur tout `state.images_data`. Vide → `ErrorDialog` "aucune image".

Les deux affichent d'abord `ErrorDialog` "impression non disponible" si `PRINT_AVAILABLE` est faux (mort de code aujourd'hui, gardé pour un scénario futur où le mécanisme deviendrait indisponible), puis délèguent à `_print_images(images, parent, canvas)`.

## `_print_images()` — orchestration

1. Affiche un **overlay de progression** sur le canvas (`labels.print_preparing`, voir skill `canvas-overlay-progress`) — pas de pourcentage ni de bouton Annuler ici (contrairement à `rotate-flip`/`page-resize`), car la préparation TIFF est rapide et non annulable une fois lancée.
2. Lance un `_PrintWorker(images_to_print)` (`QThread`) qui construit le TIFF en tâche de fond.
3. `worker.ready` → masque l'overlay, appelle `_open_print_dialog(tiff_path, parent)` (le bloc COM ctypes décrit plus haut).
4. `worker.no_images` → masque l'overlay, `ErrorDialog` "aucune image valide" (cas où toutes les images ont échoué à s'ouvrir avec PIL malgré le filtre `is_image`/`is_corrupted` en amont).
5. `worker.error` → masque l'overlay, `ErrorDialog` avec `play_sound=True` (voir skill `wilhelm-scream-easter-egg` — erreur système rare et grave, déjà en place ici).
6. `_open_print_dialog()` a son propre `try/except` : toute erreur COM (échec `SHParseDisplayName`, `SHBindToObject`, `SHCreateDataObject`, `CoCreateInstance`) remonte dans une `ErrorDialog` avec `play_sound=True` également.

Reconnexion de langue pendant l'overlay (`language_signal.changed` → re-rendu du texte de progression), déconnectée explicitement dans `_hide_overlay()` avant `hide_canvas_text` — pattern conforme à la règle CLAUDE.md n°2 sur la retraduction à la volée.

## Comment modifier

- **Changer la qualité JPEG intermédiaire** : `quality=95` dans `_open_print_dialog()`, ligne `img.save(..., 'JPEG', quality=95)`. N'affecte que le fichier temporaire envoyé au wizard, jamais `entry["bytes"]" (aucune écriture dans la mosaïque — l'impression ne modifie jamais les données de l'appli, pas de undo/redo à prévoir).
- **Changer le format du conteneur intermédiaire** (TIFF) : nécessiterait de revoir `_PrintWorker.run()` (compression `tiff_deflate`) ET la boucle de ré-éclatement dans `_open_print_dialog()`, qui rouvre spécifiquement le TIFF avec `Image.open(tiff_path)` + `.seek(i)`/`n_frames` — les deux doivent rester cohérents.
- **Ajouter un filtre supplémentaire aux images imprimées** (ex. exclure certains formats) : modifier la compréhension de liste dans `print_selection`/`print_all` (le filtre `is_image and not is_corrupted` est dupliqué dans les deux fonctions — pas de fonction commune de filtrage, à garder en tête si on ajoute une condition dans l'une sans la répliquer dans l'autre).
- **Déboguer un échec COM** : chaque étape (`SHParseDisplayName`, `SHBindToObject`, `SHCreateDataObject`, `CoCreateInstance`) lève une `RuntimeError` avec le code `hr` hexadécimal — le message d'erreur affiché à l'utilisateur inclut `str(e)`, donc ce code apparaît directement dans `ErrorDialog`.

## Points d'entrée UI

Convergent tous vers les mêmes deux fonctions, avec les mêmes paramètres `(mw, mw._canvas, st)` :

1. **Colonne d'icônes** (`icon_toolbar_qt.py:73-74`) — boutons `print_selection`/`print_all` (icônes `BTN_Print.png`/`BTN_Print_All.png`), activés seulement si `print_available()` (toujours vrai en pratique) et `has_selection()`/`has_images()` respectivement (`_ACTIVATION_RULES`, `icon_toolbar_qt.py:151-152`). Voir skill `icon-toolbar`.
2. **Menu contextuel** (clic droit, `context_menus_qt.py:156,538`) — `buttons.print_all`/`buttons.print_selection`, désactivés (`_add_disabled`) si la condition n'est pas remplie. Voir skill `qt-context-menus`.
3. **Barre de menu** (`menubar_qt.py:129,131` + callbacks `menubar_callbacks_qt.py:66-67`) — mêmes clés de traduction, menu Fichier probablement (à vérifier sur place selon le contexte du menu).

## Traductions

Clés dans `locales/fr.json` : `buttons.print_selection`/`print_all` (labels UI), `labels.print_preparing` (overlay), `messages.warnings.no_selection_print`/`no_valid_selection_print`/`no_image_print`/`no_valid_image_print` (titre+message), `messages.errors.print_not_available`/`print_error` (titre+message, avec placeholder `{error}` pour ce dernier). Voir skill `add-translation`.

## Pièges connus

- **Aucune interaction avec l'undo/redo** — l'impression ne touche jamais `entry["bytes"]`, ne pas chercher à y brancher `save_state`/`rollback` si une évolution future en avait l'air de nécessiter un.
- **Le TIFF et les JPEG intermédiaires ne sont jamais explicitement nettoyés** dans ce fichier — ils finissent dans `%TEMP%\MosaicViewTemp\` et sont couverts par le nettoyage périodique général (voir skill `temp-files`), pas par une suppression immédiate après impression.
- **`_PrintWorker` est laissé sans référence externe après `deleteLater()`** dans `_on_ready`/`_on_no_images`/`_on_error` — contrairement à `rotate-flip` qui garde une liste anti-GC (`_active_workers`), ici le worker a déjà émis son signal terminal au moment du `deleteLater()`, donc pas de risque de destruction prématurée en cours de traitement.
- **`flip_entry_data`-like : pas de validation stricte des PIDL relatifs** — si un fichier JPEG échoue à être résolu en PIDL (`ParseDisplayName` renvoie un `hr` non nul), il est silencieusement omis de `rel_pidls` sans avertissement — une page pourrait manquer à l'impression sans message d'erreur si ce cas se produit.

## Références croisées

- `canvas-overlay-progress` — mécanisme de l'overlay `labels.print_preparing`.
- `wilhelm-scream-easter-egg` — `play_sound=True` déjà en place sur les deux `ErrorDialog` d'erreur système (échec COM, échec worker).
- `corrupted-images` — filtre `is_corrupted` appliqué aux deux fonctions publiques.
- `icon-toolbar` / `qt-context-menus` — les 3 points d'entrée UI et leur activation contextuelle.
- `temp-files` — dossier `printjob_*` et TIFF temporaire, nettoyés par le mécanisme général de nettoyage, pas par ce fichier.
- `add-translation` — clés de traduction utilisées par ce module.
