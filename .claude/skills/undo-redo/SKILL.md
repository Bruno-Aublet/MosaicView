---
name: undo-redo
description: Localiser ou modifier le système Annuler/Refaire de MosaicView (historique par panneau, snapshots de state.images_data). Utiliser dès qu'une tâche touche à undo_redo.py, undo_redo_qt.py, save_state_qt, ou state.history/history_index.
---

# Annuler / Refaire (undo/redo) — MosaicView

Historique linéaire par panneau (pas un arbre) : chaque action modifiante pousse un **snapshot complet** de `state.images_data` sur une pile, `state.history_index` pointe la position courante. Annuler = reculer l'index, Refaire = l'avancer, dans les deux cas on **restaure** le snapshot ciblé plutôt que de rejouer/défaire une opération inverse. Ce skill ne couvre que le mécanisme générique — voir skill `apply-image-operation` pour le pattern que doit suivre une fonction qui modifie `entry["bytes"]` (invalidation de caches en plus de l'appel `save_state`).

## Deux fichiers, deux couches

- **`modules/qt/undo_redo.py`** — logique métier pure (aucun Qt, aucun PIL au-delà d'une comparaison de taille) : `save_state_data`, `undo_data`/`redo_data`, `can_undo`/`can_redo`, `reset_history`, `pop_last_state`, `history_top`. Manipule directement `state.history`/`state.history_index`.
- **`modules/qt/undo_redo_qt.py`** — couche Qt : `restore_state_qt` (reconstruit `images_data` avec de vrais objets Qt/PIL depuis un snapshot), et les wrappers publics `save_state_qt`/`undo_action_qt`/`redo_action_qt`/`rollback_to_current_state_qt` que le reste du code appelle réellement. Réexporte aussi `reset_history`/`pop_last_state` de la couche pure (import direct pour `panel_widget.py`, pas de réimplémentation).

**Toujours appeler la version `_qt` depuis du code Qt** (`save_state_qt`, pas `save_state_data` directement) — la version pure ne rafraîchit ni la toolbar ni la mosaïque, elle ne fait que manipuler la pile.

## L'historique — structure et portée

`state.history` (liste) / `state.history_index` (int, `-1` si vide) sont des attributs d'`AppState` (`modules/qt/state.py`, `__init__`) — **un historique par panneau** (voir skill `panels` : chaque `PanelWidget` a son propre `self._state`, donc sa propre pile ; annuler dans panel1 ne touche jamais panel2). `MAX_HISTORY = 20` (constante en tête de `undo_redo.py`) : la pile est bornée, un dépassement fait glisser le plus ancien snapshot (`state.history.pop(0)` + décrément de l'index).

Chaque entrée de `state.history` est un dict `saved_state` :

```python
{
    'entries': [...],             # snapshot des entrées (voir plus bas)
    'modified': bool,
    'needs_renumbering': bool,
    'all_entries': [...] | None,  # snapshot séparé si aplatissement de sous-dossiers actif
    'current_directory': str,
    'current_sort_method': ...,
    'current_sort_order': ...,
    'selected_names': set(str),   # noms des entrées sélectionnées au moment du snapshot
}
```

### Le snapshot d'entrée — ce qui est copié, ce qui ne l'est pas

`_create_entries_snapshot_from()` (`undo_redo.py`) ne copie que les champs "durables" d'une entrée (`orig_name`, `bytes`, `extension`, `is_image`, `is_dir`, `is_corrupted`, `corruption_reason`) — **jamais** les objets Qt/PIL vivants (`img`, `qt_pixmap_large`, `name_entry`...), qui n'existent que côté `restore_state_qt` et sont reconstruits à la demande.

- **`entry["bytes"]` est partagé par référence, jamais copié** — un snapshot ne duplique pas les données binaires. C'est valide **parce que** `entry["bytes"]` n'est jamais muté en place ailleurs dans le projet : toute modification remplace la référence entière (`entry["bytes"] = nouveaux_bytes`), jamais un `bytearray` modifié sur place. Voir skill `apply-image-operation`, c'est une des raisons pour lesquelles ce pattern est obligatoire — le casser romprait silencieusement l'undo/redo (un ancien snapshot se retrouverait avec les bytes déjà modifiés).
- **`entry_copy['_original_id'] = id(e)`** — l'identité Python de l'objet entrée au moment du snapshot, utilisée ensuite par `restore_state_qt` pour décider de **réutiliser** l'objet `entry` existant (s'il est encore présent dans `images_data`) plutôt que d'en fabriquer un neuf. Comparer par `id()`, pas par nom — un renommage ou une renumérotation change `orig_name` mais pas l'identité de l'objet.

### Détection de changement — `_is_state_identical()`

`save_state_data()` ne pousse un nouveau snapshot que si l'état a réellement changé depuis le dernier (sauf `force=True`) :
- Compare la longueur des listes, puis pour chaque entrée : `orig_name`, `_original_id` (un id différent signale un remplacement d'objet, ex. transfert inter-panneaux), puis les bytes par `last_bytes != current_bytes`. `bytes.__ne__` court-circuite de lui-même : même objet → égal sans lecture, longueurs différentes → différent sans lecture ; le contenu n'est lu (memcmp) que pour deux objets distincts de même longueur, donc uniquement pour les pages réellement remplacées depuis le dernier snapshot. Coût négligeable (≈ 0,5 ms pour 5 Mo), aucun hash nécessaire.
- **Piège : ne jamais réduire cette comparaison à `is` + `len()`.** Deux contenus différents de même longueur sont courants : miroir d'un BMP ou d'un TIFF non compressé (taille toujours identique), rotation 90° d'un BMP carré ou aux dimensions multiples de 4. L'état serait jugé identique, le `save_state()` sans `force` d'après opération ne pousserait rien → opération impossible à annuler, ou annulée en même temps que la suivante, et un `rollback` ultérieur l'effacerait silencieusement. Couvert par `tests/test_undo_redo.py`.
- Si identique → `save_state_data` retourne `False` sans rien pousser, et `save_state_qt` ne rafraîchit pas la toolbar (pas de nouveau point undo créé pour rien).

### `force=True` — pourquoi et quand

Contourne la détection de changement et pousse un snapshot même si l'état est identique au sommet. Exemples : `PanelWidget._on_non_image_file_modified()` et le mode édition de `_edit_comicinfo()` (`force=True` avant ET après la modification). **Piège** : un `save_state(force=True)` *avant* modification alors que le sommet représente déjà l'état courant crée un point d'historique en double (un Ctrl+Z qui ne change rien visuellement) et consomme une place de `MAX_HISTORY`.

## Le cycle de vie complet d'une action annulable

**Invariant : le sommet de l'historique (`history[history_index]`) représente l'état courant.** Le premier snapshot est poussé en fin de chargement (`PanelWidget._on_loading_finished()` → `save_state_data`), ou à l'ajout d'images dans un panneau vide.

1. **Avant modification** : `PanelWidget.save_state()` (wrapper fin autour de `save_state_qt`). Normalement sans effet (état identique au sommet) ; sert de filet si l'état courant a divergé du sommet sans avoir été enregistré.
2. **Modification réelle** sur `images_data` (ajout, suppression, remplacement de `bytes`...).
3. **Après modification** : `save_state()` à nouveau — c'est **ce** snapshot qui crée le point undo (le nouvel état devient le sommet). Sans lui, l'opération n'est pas annulable.
4. **Rafraîchissement** : `render_mosaic()` + toolbar.

Annuler = `undo_data()` décrémente l'index et restaure `history[index]`, c'est-à-dire l'état d'avant la dernière opération ; `can_undo` exige donc `history_index > 0` (au moins un point en plus du snapshot initial).

## `restore_state_qt()` — comment un snapshot redevient un vrai `images_data`

Cœur de la couche Qt (`undo_redo_qt.py`), appelé par `undo_action_qt`/`redo_action_qt`/`rollback_to_current_state_qt` — jamais directement par du code métier.

1. Indexe les entrées **actuellement en mémoire** par `id()` (`entries_by_id`).
2. Pour chaque entrée du snapshot cible : si son `_original_id` correspond à une entrée encore présente → **réutilise cet objet existant**, met à jour ses champs durables en place (`orig_name`, `extension`, `bytes`, `is_corrupted`...). Sinon → `_build_new_entry_qt()` fabrique un dict d'entrée neuf avec tous les champs Qt (`qt_pixmap_large`, `name_entry`...) explicitement à `None`. **Piège** : cette liste de champs restaurés doit rigoureusement correspondre à celle copiée par `_create_entries_snapshot_from()` (section précédente) — un champ présent dans le snapshot mais absent d'ici reste silencieusement figé sur sa valeur d'avant l'undo/redo, indépendamment de ce que contient le snapshot ciblé.
3. **Invalidation ciblée des caches vignette** (`_reload_thumb_qt`) : seulement si les bytes ont réellement changé pour cette entrée (`bytes_changed = entry["bytes"] is not entry_data["bytes"]`) — vide `qt_pixmap_large`/`large_thumb_pil`/`_hash`, retire `img_width`/`img_height` (dimensions mémorisées périmées après l'undo d'une rotation ou d'un crop ; leurs lecteurs relisent alors l'en-tête de l'image restaurée) et recalcule `is_animated_image` (`set_animated_image_info`, skill `archive-image-loading` : les bytes restaurés peuvent redevenir, ou cesser d'être, une animation WebP/PNG/AVIF). Ne recalcule jamais tout en aveugle, seulement ce qui a divergé.
4. **Entrées orphelines** (objets présents dans `images_data` mais absents du snapshot cible, ex. une page ajoutée après ce point d'historique puis annulée) : leurs caches Qt sont invalidés (`qt_pixmap_large`/`qt_qimage_large` mis à `None`) pour libérer la mémoire — l'objet lui-même n'est plus référencé nulle part ensuite, donc éligible au GC Python normal.
5. `state.images_data[:] = new_images_data` — remplacement **en place** de la liste (pas de réassignation `state.images_data = ...`), important si un autre code garde une référence à l'ancienne liste.
6. Restaure `modified`/`needs_renumbering`/tri courant/`all_entries`/`current_directory` depuis le snapshot.
7. **`ComicInfo.xml`** : si le résultat restauré contient une entrée ComicInfo, reparse ses bytes (`parse_comic_info_xml`) et régénère `state.comic_metadata` + `_page_attrs_by_entry_id` (`build_page_attrs_map`) + resynchronise `<Pages>` (`sync_pages_in_xml_data`, `emit_signal=False`), puis émet une seule fois `metadata_signal.emit(state)` (onglet Métadonnées de ce panneau uniquement, voir skill `tabs`) avant `update_tabs_cb()` — voir skill `comicinfo-metadata-editor`. Si au contraire l'ancien état avait des métadonnées mais plus le nouveau, les efface explicitement.
8. **Restauration de sélection** : par **noms** (`selected_names`, pas par index ni par `id()`) — un undo/redo change potentiellement l'ordre/le contenu de `images_data`, donc un index ou une identité d'objet ne seraient pas fiables ; le nom reste le point de repère le plus stable entre deux snapshots. Si aucun des noms sélectionnés n'existe plus dans le résultat restauré, la sélection est simplement vidée (`clear_selection_cb()`).
9. `render_mosaic_cb()` — toujours appelé : `render_mosaic()` recrée de toute façon tous les items de la scène, donc rien à gagner à distinguer un cas "juste un renommage" d'un cas "structure changée". Puis repaint ciblé des seules vignettes dont les bytes ont changé (`canvas.refresh_thumbnails_visual`), nécessaire quand la fenêtre principale n'est pas active (undo/redo depuis la visionneuse).
10. `refresh_toolbar_cb()` en dernier — met à jour l'état actif/grisé des boutons Annuler/Redo (voir section suivante).

## `rollback_to_current_state_qt()` — cas particulier : annuler sans avoir avancé l'historique

Distinct d'un vrai undo : restaure le sommet actuel de l'historique (`state.history[state.history_index]`) **sans décrémenter l'index** — utilisé quand une opération a été lancée, un `save_state()` a été appelé, mais l'opération elle-même est **annulée en cours de route** (bouton « Annuler » pendant une rotation/un miroir ou un redressement automatique en lot) et qu'il faut défaire les modifications déjà appliquées en mémoire sans que ça compte comme un "vrai" pas d'historique en plus. Fourni sous la clé `"rollback"` par `PanelWidget._image_transforms_callbacks()` et `_deskew_callbacks()`, consommé par `_finish_cancel` dans `image_transforms_qt.py::_run_transform` et `deskew_qt.py::_run_deskew` (voir skills `rotate-flip` et `page-straighten`). **À n'appeler qu'une fois le worker arrêté** (réception de son signal `cancelled`/`done`), jamais au clic sur Annuler : `restore_state_qt` réutilise les mêmes dicts d'entrées, donc une page que le worker est encore en train d'écrire garderait sa version modifiée. **Jamais non plus si le document a changé depuis le lancement** (`state.doc_generation`, voir skill `file-close`) : l'historique du panneau serait alors celui d'un autre document. Même règle pour `pop_last_state` (annulation du redimensionnement, skill `page-resize`). Repose sur l'invariant "sommet = état courant" : si l'opération précédente n'a pas été enregistrée, le rollback l'efface aussi.

## Câblage côté panneau — `PanelWidget`

- **`save_state(force=False)`** — méthode d'instance, wrapper autour de `save_state_qt(self._state, self._refresh_toolbar_states, force=force)`. **Le point d'entrée à utiliser depuis n'importe quelle fonction métier du panneau** plutôt que d'appeler `save_state_qt` directement avec les bons arguments à chaque fois.
- **`_undo_redo_callbacks()`** — construit le tuple `(render_mosaic, clear_selection, update_tabs, refresh_toolbar_states)` attendu par `undo_action_qt`/`redo_action_qt`/`restore_state_qt`. Un seul endroit à modifier si un cinquième callback devient nécessaire — tous les appelants du panneau passent par cette méthode (`*self._undo_redo_callbacks()`), jamais construit à la main ailleurs.
- **`_undo_action()`**/**`_redo_action()`** — délèguent à `undo_action_qt`/`redo_action_qt` avec ce tuple de callbacks, puis rafraîchissent les visionneuses ouvertes (`_refresh_open_image_viewers`). Câblés sur `Ctrl+Z`/`Ctrl+Y` (raccourcis globaux dans `MosaicView.py`, routés vers `self._active_panel._undo_action()` — voir skill `panels`), sur le menu Édition, et sur les boutons dédiés de la colonne d'icônes (voir skill `icon-toolbar`, state_getters `has_undo`/`has_redo`).
- **`_refresh_toolbar_states`** consulte `can_undo(state)`/`can_redo(state)` (couche pure) pour activer/griser les boutons — c'est la seule chose qui a besoin d'être recalculée après chaque `save_state`/`undo`/`redo`, pas un rendu complet de la mosaïque à ce stade précis (le rendu, lui, vient de `restore_state_qt` pour undo/redo, ou du code appelant pour un simple `save_state`).

## Interaction avec les onglets — `update_tabs_cb`

`_update_tabs` (passé en 3ᵉ position du tuple `_undo_redo_callbacks`) est appelé par `restore_state_qt` **seulement si l'état de `ComicInfo.xml` a changé** (apparu, disparu, ou contenu modifié) — pas à chaque undo/redo inconditionnellement. Rafraîchit l'onglet métadonnées (voir skills `comicinfo-metadata-editor` et `tabs` pour le mécanisme de l'onglet lui-même) pour refléter les champs tels qu'ils étaient au moment du snapshot restauré. Un undo/redo qui ne touche qu'à l'ordre/au contenu des pages sans toucher au XML n'appelle pas ce callback.

## Où `save_state`/`save_state_qt` est réellement appelé

Grep `_save_state_qt\|self.save_state(` dans `panel_widget.py` pour la liste exhaustive à jour plutôt que de supposer qu'un seul endroit suffit — usages notables :
- Suppression de sélection (`_delete_selected_qt`), avec confirmation avant.
- `ComicInfo.xml` (`_edit_comicinfo`, voir skill `comicinfo-metadata-editor`) : mode création sans `force` avant/après (l'ajout de l'entrée change la longueur de `images_data`, donc détecté) ; mode édition avec `force=True` avant/après.
- `NameEdit` (renommage de vignette, `mosaic_canvas.py`) — `save_state()` **sans `force`** à la première frappe (`_on_text_changed`), avant que `orig_name` change ; aucun `save_state` après la validation du nom.
- Rotation/miroir (`image_transforms_qt.py`) — `save_state()` sans `force` avant le lancement du worker et à la fin (`on_finished`) : dépend entièrement de la détection de changement des bytes (voir piège de la section `_is_state_identical`).
- Import/fusion d'archive, ajout d'images isolées, import web — voir skills `archive-image-loading`/`web-import`. L'ajout dans un panneau vide pousse le snapshot initial.

## `reset_history()` — quand l'historique est vidé plutôt que restauré

Appelé à l'**ouverture** d'un nouveau fichier CBZ/PDF dans un panneau vide (`panel_widget.py`, juste avant de lancer le loader — voir skills `archive-image-loading` et `pdf-loading`) et à la **fermeture** d'un comic (`file_close_qt.py`, voir skill `file-close`) : `state.history = []`, `state.history_index = -1`. Un nouveau fichier ouvert dans un panneau **ne doit jamais** hériter de l'historique undo du fichier précédent — pas un cas particulier de restauration, une remise à zéro complète. Ne pas confondre avec `_close_split`/fermeture de panneau (voir skill `panels`), qui ne réinitialise pas l'historique explicitement, la destruction de `state` s'en charge (l'objet `AppState` entier disparaît).

## `mark_history_saved()` — l'indicateur `modified` après une sauvegarde du fichier

Chaque snapshot mémorise `modified` tel qu'il était à sa capture, et `restore_state_qt()` le restaure. Sans correction, un Annuler après une sauvegarde ramènerait donc un état « non modifié » (ex. le snapshot pris à l'ouverture) alors que son contenu diffère du fichier qui vient d'être écrit : la fermeture ne demanderait plus de confirmation, et le changement annulé serait perdu.

`mark_history_saved(state)` (`undo_redo.py`) est appelée après chaque sauvegarde réussie, juste après `state.modified = False` : `_finish_apply_new_names` (enregistrement et conversions), `save_as_cbz` et `create_cbz_from_images` (`file_operations_qt.py`, skill `save-export`). Elle marque tous les snapshots `modified=True`, sauf le sommet actif s'il est identique (`_is_state_identical`) au contenu courant, donc au fichier écrit. Si le sommet diffère (ex. noms appliqués depuis les `NameEdit` au moment de la sauvegarde, sans snapshot), tous restent modifiés : au pire une confirmation de trop, jamais une perte. Elle ne pousse aucun snapshot : un `save_state_data()` à cet endroit tronquerait la branche Refaire si l'utilisateur sauvegarde après des Annuler. Testée par `tests/test_undo_redo.py`. **Toute nouvelle fonction qui écrit le document sur disque et remet `modified` à `False` doit l'appeler.**

## `pop_last_state()` — annuler une sauvegarde inutile a posteriori

Retire le dernier snapshot poussé **sans** avoir touché `images_data` — utilisé quand un état a été sauvegardé par anticipation mais que l'action finit par ne rien changer : annulation en cours d'un redimensionnement (`resize_dialog_qt.py`, `_restore_after_cancel`, après avoir restauré lui-même les bytes d'origine) et lecture de macro interrompue (`macro_engine.rollback_macro_reading`). Ne fait rien si `history_index` vaut 0 (le snapshot initial n'est jamais retiré). Décrémente `history_index` et retire l'entrée correspondante — **différent d'un undo réel** : ça ne restaure rien dans `images_data`, ça corrige seulement la pile pour qu'elle ne contienne pas un point mort identique à celui d'avant.

**Piège — ne dépiler que ce qui a réellement été poussé** : chaque action sauve après elle-même, donc le sommet de l'historique décrit déjà l'état courant, et un `save_state()` "avant" non forcé n'ajoute en général rien (`save_state_data` retourne `False` sur un état identique). `pop_last_state()` retirerait alors le dernier état légitime de l'utilisateur : après un undo, sa dernière action ne pourrait plus être refaite. Mémoriser `state.history[state.history_index]` avant le `save_state()` et le comparer par identité au sommet après (`history_top`, `undo_redo.py` — utilisé par `_finish_resize`/`_restore_after_cancel` du redimensionnement et par `run_macro_on_entries`/`rollback_macro_reading` ; testé par `tests/test_undo_redo.py`) ; ne pas se fier à `history_index` seul, qui reste fixe une fois l'historique plein (`MAX_HISTORY`, le plus ancien état retiré à chaque ajout).

## Comment étendre

- **Ajouter un nouveau champ à préserver dans l'historique** (ex. un nouvel attribut d'`AppState` qui doit survivre à un undo) : l'ajouter au dict `saved_state` dans `save_state_data()` (`undo_redo.py`) **et** à sa restauration correspondante dans `restore_state_qt()` (`undo_redo_qt.py`) — les deux fichiers doivent rester synchronisés, il n'y a pas de schéma partagé validé automatiquement.
- **Ajouter un nouveau callback nécessaire à la restauration** : l'ajouter au tuple retourné par `_undo_redo_callbacks()` (`panel_widget.py`) et à la signature de `restore_state_qt`/`undo_action_qt`/`redo_action_qt` — un seul point d'assemblage par panneau, ne pas construire un tuple ad hoc dans un nouveau call-site.
- **Changer la détection de changement** (`_is_state_identical`) : uniquement dans `undo_redo.py`, fonction pure couverte par `tests/test_undo_redo.py` (sans Qt) — y ajouter un test pour tout nouveau critère.
- **Augmenter/diminuer `MAX_HISTORY`** : une seule constante dans `undo_redo.py`, pas de configuration utilisateur exposée actuellement.

## Pièges connus

- **Ne jamais appeler `save_state_data`/`restore_state_qt` directement depuis du code de panneau** — toujours passer par `PanelWidget.save_state()` et par `_undo_action`/`_redo_action` (qui utilisent `_undo_redo_callbacks()`), sinon un callback de rafraîchissement peut être oublié silencieusement.
- **`entry["bytes"]` ne doit jamais être muté en place** — toute fonction qui modifie une image doit réassigner `entry["bytes"] = nouveaux_bytes`, jamais modifier un buffer existant sur place, sous peine de corrompre silencieusement tous les anciens snapshots qui partagent la même référence (voir skill `apply-image-operation`).
- **`force=True` doit rester l'exception, pas l'habitude** — l'utiliser par réflexe partout ferait grossir l'historique de points morts inutiles et userait `MAX_HISTORY` plus vite qu'il ne devrait ; voir le piège de la section `force=True`.
- **La sélection restaurée après undo/redo est par nom, pas par index** — si une future modification introduit des noms dupliqués intentionnellement (actuellement jamais le cas dans le projet), la restauration de sélection deviendrait ambiguë ; vérifier cette hypothèse avant d'introduire un scénario avec doublons de noms.
- **`rollback_to_current_state_qt` n'est pas un "undo bonus"** — ne pas l'utiliser à la place d'un vrai `undo_action_qt` par erreur : elle ne décrémente jamais l'index, donc rappeler `undo_action_qt` juste après reculerait d'un cran de plus que prévu par rapport à l'intention de l'utilisateur.
- **Une sauvegarde du fichier ne touche pas l'historique, mais doit corriger `modified` dans les snapshots** — voir la section `mark_history_saved()` ; appelée depuis les fonctions de sauvegarde (skill `save-export`).
