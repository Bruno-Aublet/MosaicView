---
name: flatten-directories
description: Localiser ou modifier l'aplatissement de l'arborescence des répertoires (fusion des entrées en sous-dossiers dans un seul niveau plat). Utiliser dès qu'une tâche touche à flatten_directories_qt.py ou au bouton/menu "Aplatir l'arborescence".
---

# Aplatissement des répertoires — MosaicView

Fusionne toutes les entrées d'une archive organisée en sous-dossiers (`entry["is_dir"]` et/ou noms contenant `/`) dans un seul niveau plat — supprime la structure de dossiers, garde les fichiers avec des noms dérivés (renommés en cas de collision). Un seul fichier, une seule fonction : `modules/qt/flatten_directories_qt.py::flatten_directories_qt`.

## Quand l'action est disponible

Activation contextuelle calculée à 3 endroits indépendants (pas de fonction commune) :
- `_populate_archives_menu` (`menubar_qt.py`, voir skill `menu-bar`) : `can_flatten = any(is_dir) or any('/' in orig_name and not is_dir)` sur `state.images_data` (même calcul dans le menu contextuel, `context_menus_qt.py`).
- `_ACTIVATION_RULES["flatten_directories"]` (`icon_toolbar_qt.py`) : `sg["has_subdirs"]()` — bouton de la colonne d'icônes, voir skill `icon-toolbar`.
- Menu contextuel (`context_menus_qt.py`, `show_canvas_context_menu` et `show_dir_context_menu`) : même clé de callback, condition d'activation gérée par l'appelant du menu (pas montrée ici).

Les trois lisent des conditions équivalentes mais **recalculées indépendamment** — cohérent avec le reste du projet (voir skill `menu-bar`, "Duplication des conditions d'activation").

## Logique métier (`flatten_directories_qt`)

1. Lit `state.images_data`, toujours la liste complète du document : la navigation dans les sous-dossiers ne filtre que l'affichage (`get_visible_entries_qt`, `mosaic_canvas.py`, d'après `state.current_directory`), jamais `images_data`. **Jamais une copie de la liste prise plus tôt** (au chargement, par exemple) : les suppressions, découpes, conversions, collages et dépôts depuis l'autre panneau faits depuis n'y figureraient pas — aplatir à partir d'elle ferait réapparaître des pages supprimées et disparaître des pages ajoutées. Il n'existe pas de liste « complète » distincte de `images_data`.
2. Retourne immédiatement si **rien à aplatir** (`not dirs and not has_files_with_paths`) — pas d'effet de bord, pas de save_state, complètement no-op.
3. `_save_state_data(state, force=True)` **avant** modification — `force=True` explicite (pas le comportement par défaut) car l'état après aplatissement peut avoir des noms/bytes identiques au dernier snapshot dans certains cas limites, ce qui ferait échouer une détection de changement naïve (voir skill `undo-redo` pour le rôle de `force`).
4. `flatten_entries(all_data)` (fonction pure du même module, testée par `tests/test_flatten_entries.py`) rend la nouvelle liste. Elle boucle sur toutes les entrées **non-dossier** (les entrées `is_dir` elles-mêmes sont purement et simplement supprimées, pas renommées) :
   - Si `orig_name` contient `/` : découpe en segments, garde le premier segment (`first_part`) et le dernier (`filename`, le nom de fichier réel).
   - **Préserve un préfixe `NEW-`/`OLD-`** si le premier segment de chemin commence par l'un de ces deux préfixes — ce sont les marqueurs utilisés ailleurs dans l'appli pour distinguer les pages importées/fusionnées (voir skill `pdf-loading`, `import_and_merge_pdf`, préfixe `NEW{:02d}-` ; et le mécanisme équivalent pour les archives fusionnées, voir skill `archive-image-loading`/`import_merge_qt.py`). Sans cette préservation, aplatir une archive contenant des pages fusionnées perdrait la trace visuelle de leur origine.
   - Sinon (pas de préfixe reconnu) : le nom devient juste `filename`, sans aucun segment de chemin.
5. **Résolution de collision** : si le nouveau nom (`orig_name` ou `NEW-`/`OLD-` + filename) est déjà pris (`seen_names`), suffixe `_1`, `_2`, etc. avant l'extension jusqu'à trouver un nom libre — deux fichiers `chapitre1/page01.jpg` et `chapitre2/page01.jpg` deviennent `page01.jpg` et `page01_1.jpg`.
   - **Exception — un `ComicInfo.xml` sorti d'un dossier** (`Bonus/ComicInfo.xml`) : il arrive à la racine, où il serait pris pour le ComicInfo.xml de la BD. Il n'entre pas dans `seen_names` et est nommé après la boucle comme toute arrivée (`comic_info.name_incoming_comic_infos`, skill `comicinfo-metadata-editor`) : `ComicInfo_0002.xml`… si la BD a déjà le sien, sinon le premier devient son `ComicInfo.xml` (ses métadonnées apparaissent, `sync_comic_metadata`). **Piège** : traité par la boucle ordinaire, celui qui vient en premier dans la liste prendrait le nom `ComicInfo.xml` — le fichier du dossier prendrait alors la place du vrai, et ses métadonnées remplaceraient celles de la BD sans rien dire.
6. Réassigne `state.images_data`, vide `state.current_directory`, `state.modified = True`.
7. `sync_pages_in_xml_data(state, emit_signal=False)` (voir skill `comicinfo-metadata-editor`) — les noms de page dans `ComicInfo.xml` doivent suivre les noms de fichiers aplatis ; `emit_signal=False` explicite pour ne pas déclencher un rafraîchissement prématuré de l'onglet métadonnées avant la fin du traitement local.
8. `save_state_func()` (redo, **après** modification) — c'est le paramètre `save_state_func` de la fonction, distinct de l'appel `_save_state_data` du point 3 (import direct, pas passé en paramètre) : deux mécanismes de sauvegarde d'état différents dans la même fonction, voir "Pièges".
9. `render_mosaic()`, `refresh_states()` (rafraîchit la colonne d'icônes), `status_changed.emit()` (signal Qt, voir skill `status-bar`).
10. `QTimer.singleShot(0, lambda: metadata_signal.emit(state))` — différé d'un tick pour laisser le rendu de la mosaïque se terminer avant de notifier l'onglet Métadonnées (voir skill `tabs`) qu'un rafraîchissement est nécessaire. Le state est capturé dans la lambda pour que seul l'onglet de ce panneau se reconstruise.

## Point d'entrée UI → callback

Les 3 déclencheurs (menu Archives, bouton colonne d'icônes, menu contextuel) convergent vers `mw._flatten_directories` (`build_menubar_callbacks`, `menubar_callbacks_qt.py`), défini dans `PanelWidget._flatten_directories` (`panel_widget.py`), qui appelle `flatten_directories_qt(parent, render_mosaic, refresh_states, status_changed, save_state_func)` avec les callbacks concrets du panneau courant.

## Comment modifier

- **Changer la règle de préservation de préfixe** (actuellement `NEW-`/`OLD-` seulement) : modifier la condition `if first_part.startswith("NEW-"): ... elif first_part.startswith("OLD-"): ...` — si un futur préfixe de provenance est introduit ailleurs dans l'appli (voir skill `archive-image-loading`/`pdf-loading` pour où ces préfixes sont générés), il faudrait l'ajouter ici aussi pour qu'il survive à un aplatissement.
- **Changer la résolution de collision de noms** (actuellement suffixe numérique `_1`, `_2`...) : boucle `while new_name in seen_names` de `flatten_entries`, juste après le calcul de `base, ext` (sans effet sur le ComicInfo.xml, nommé par `name_incoming_comic_infos`).
- **Étendre l'aplatissement pour respecter un ordre différent** : actuellement la liste rendue par `flatten_entries` suit l'ordre de `all_data` (pas de tri explicite) — un futur besoin de conserver un ordre naturel après aplatissement demanderait un tri explicite dans `flatten_entries`.

## Pièges connus

- **Deux mécanismes de sauvegarde d'état distincts dans la même fonction** — `_save_state_data(state, force=True)` (import direct du module `undo_redo`, appelé **avant** la modification) et `save_state_func()` (callback injecté, appelé **après**) ne sont pas la même fonction : le premier est toujours le même import fixe, le second est fourni par l'appelant (`panel_widget.py`) et pourrait en théorie pointer vers une implémentation différente. Ne pas supposer qu'un changement dans l'un se répercute sur l'autre.
- **Les entrées `is_dir` sont supprimées silencieusement, jamais renommées** — un dossier vide ou contenant uniquement d'autres dossiers disparaît sans trace après aplatissement ; ce n'est pas un bug, c'est le but de la fonction, mais à garder en tête si un futur besoin demande de préserver une trace de la structure d'origine.
- **`sync_pages_in_xml_data(..., emit_signal=False)`** — si une modification future de ce fichier appelle une autre fonction qui touche au XML sans passer `emit_signal=False`, un signal prématuré pourrait rafraîchir l'onglet Métadonnées avant que `render_mosaic()`/`refresh_states()` n'aient tourné, provoquant un état d'affichage transitoire incohérent.
- **Fonction "tout ou rien" sans confirmation utilisateur** — contrairement à d'autres opérations destructives du projet (suppression, fermeture sans sauvegarder), l'aplatissement s'exécute **immédiatement** au clic, sans dialogue de confirmation ; seul l'undo (`Ctrl+Z`) permet de revenir en arrière.

## Références croisées

- `undo-redo` — `_save_state_data(force=True)`, rôle du paramètre `force` pour un état qui pourrait sembler identique au dernier snapshot.
- `comicinfo-metadata-editor` — `sync_pages_in_xml_data`, synchronisation des noms de page dans le XML après renommage.
- `menu-bar` / `icon-toolbar` / `qt-context-menus` — les 3 points d'entrée UI, chacun avec sa propre condition d'activation recalculée indépendamment.
- `mosaic-thumbnails` — `get_visible_entries_qt`, filtrage d'affichage par sous-dossier (`state.current_directory`), sans effet sur `images_data`.
- `pdf-loading` / `archive-image-loading` — origine des préfixes `NEW-`/`OLD-` préservés lors de l'aplatissement.
- `tabs` — `metadata_signal`, notification différée de l'onglet Métadonnées après aplatissement.
- `status-bar` — `status_changed` signal Qt émis en fin de traitement.
