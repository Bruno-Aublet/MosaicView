---
name: undo-redo
description: Localiser ou modifier le système Annuler/Refaire de MosaicView (historique par panneau, snapshots de state.images_data). Utiliser dès qu'une tâche touche à undo_redo.py, undo_redo_qt.py, save_state_qt, ou state.history/history_index.
---

# Annuler / Refaire (undo/redo) — MosaicView

Historique linéaire par panneau (pas un arbre) : chaque action modifiante pousse un **snapshot complet** de `state.images_data` sur une pile, `state.history_index` pointe la position courante. Annuler = reculer l'index, Refaire = l'avancer, dans les deux cas on **restaure** le snapshot ciblé plutôt que de rejouer/défaire une opération inverse. Ce skill ne couvre que le mécanisme générique — voir skill `apply-image-operation` pour le pattern que doit suivre une fonction qui modifie `entry["bytes"]` (invalidation de caches en plus de l'appel `save_state`).

## Deux fichiers, deux couches

- **`modules/qt/undo_redo.py`** — logique métier pure (aucun Qt, aucun PIL au-delà d'une comparaison de taille) : `save_state_data`, `undo_data`/`redo_data`, `can_undo`/`can_redo`, `reset_history`, `pop_last_state`. Manipule directement `state.history`/`state.history_index`.
- **`modules/qt/undo_redo_qt.py`** — couche Qt : `restore_state_qt` (reconstruit `images_data` avec de vrais objets Qt/PIL depuis un snapshot), et les wrappers publics `save_state_qt`/`undo_action_qt`/`redo_action_qt`/`rollback_to_current_state_qt` que le reste du code appelle réellement. Réexporte aussi `reset_history`/`pop_last_state` de la couche pure (import direct pour `panel_widget.py`, pas de réimplémentation).

**Toujours appeler la version `_qt` depuis du code Qt** (`save_state_qt`, pas `save_state_data` directement) — la version pure ne rafraîchit ni la toolbar ni la mosaïque, elle ne fait que manipuler la pile.

## L'historique — structure et portée

`state.history` (liste) / `state.history_index` (int, `-1` si vide) sont des attributs d'`AppState` (`modules/qt/state.py:45-46`) — **un historique par panneau** (voir skill `panels` : chaque `PanelWidget` a son propre `self._state`, donc sa propre pile ; annuler dans panel1 ne touche jamais panel2). `MAX_HISTORY = 20` (`undo_redo.py:6`) : la pile est bornée, un dépassement fait glisser le plus ancien snapshot (`state.history.pop(0)` + décrément de l'index).

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

`_create_entries_snapshot_from()` (`undo_redo.py:9`) ne copie que les champs "durables" d'une entrée (`orig_name`, `bytes`, `extension`, `is_image`, `is_dir`, `is_corrupted`, `corruption_reason`) — **jamais** les objets Qt/PIL vivants (`img`, `qt_pixmap_large`, `name_entry`...), qui n'existent que côté `restore_state_qt` et sont reconstruits à la demande.

- **`entry["bytes"]` est partagé par référence, jamais copié** — un snapshot ne duplique pas les données binaires. C'est valide **parce que** `entry["bytes"]` n'est jamais muté en place ailleurs dans le projet : toute modification remplace la référence entière (`entry["bytes"] = nouveaux_bytes`), jamais un `bytearray` modifié sur place. Voir skill `apply-image-operation`, c'est une des raisons pour lesquelles ce pattern est obligatoire — le casser romprait silencieusement l'undo/redo (un ancien snapshot se retrouverait avec les bytes déjà modifiés).
- **`entry_copy['_original_id'] = id(e)`** — l'identité Python de l'objet entrée au moment du snapshot, utilisée ensuite par `restore_state_qt` pour décider de **réutiliser** l'objet `entry` existant (s'il est encore présent dans `images_data`) plutôt que d'en fabriquer un neuf. Comparer par `id()`, pas par nom — un renommage ou une renumérotation change `orig_name` mais pas l'identité de l'objet.

### Détection de changement — `_is_state_identical()`

`save_state_data()` ne pousse un nouveau snapshot que si l'état a réellement changé depuis le dernier (sauf `force=True`) :
- Compare la longueur des listes, puis pour chaque entrée : `orig_name`, `_original_id` (un id différent signale un remplacement d'objet, ex. transfert inter-panneaux), puis les bytes — **par référence d'abord** (rapide), et seulement par **longueur** si les références diffèrent (pas de comparaison octet-par-octet, jugée trop coûteuse — un changement de taille suffit à détecter une modification réelle dans l'immense majorité des cas).
- Si identique → `save_state_data` retourne `False` sans rien pousser, et `save_state_qt` ne rafraîchit pas la toolbar (pas de nouveau point undo créé pour rien).

### `force=True` — pourquoi et quand

Contourne la détection de changement — utilisé quand on **sait** qu'on va modifier l'état juste après l'appel, mais que l'état n'a *pas encore* changé au moment de sauvegarder (donc `_is_state_identical` renverrait toujours `True` et bloquerait la sauvegarde). Exemple : `NameEdit._on_text_changed()` (`mosaic_canvas.py:482-486`) appelle `save_state_func()` à la **première frappe**, avant que `orig_name` n'ait été modifié — sans `force=True` ici, rien ne serait jamais sauvegardé puisque rien n'a encore changé dans `images_data` à cet instant précis.

## Le cycle de vie complet d'une action annulable

1. **Avant modification** : le code appelant (fonction métier, handler UI) appelle `PanelWidget.save_state(force=...)` (`panel_widget.py:1437`, wrapper fin autour de `save_state_qt`) — capture l'état **actuel**, donc juste avant la modification à venir.
2. **Modification réelle** : le code effectue son changement sur `images_data` (ajout, suppression, modification de `bytes`...).
3. **Rafraîchissement** : `render_mosaic()` + mise à jour toolbar (le point undo créé active immédiatement le bouton "Annuler").

`save_state()` capture **l'état d'avant**, pas l'état d'après — c'est ce qui permet à `undo_data()` de simplement redonner `state.history[state.history_index]` après décrément : la case courante est toujours "ce qu'il faut restaurer si on annule l'action qui vient de se produire après ce point".

## `restore_state_qt()` — comment un snapshot redevient un vrai `images_data`

Cœur de la couche Qt (`undo_redo_qt.py:82`), appelé par `undo_action_qt`/`redo_action_qt`/`rollback_to_current_state_qt` — jamais directement par du code métier.

1. Indexe les entrées **actuellement en mémoire** par `id()` (`entries_by_id`).
2. Pour chaque entrée du snapshot cible : si son `_original_id` correspond à une entrée encore présente → **réutilise cet objet existant**, met à jour ses champs durables en place (`orig_name`, `bytes`, `is_corrupted`...). Sinon → `_build_new_entry_qt()` fabrique un dict d'entrée neuf avec tous les champs Qt (`qt_pixmap_large`, `name_entry`...) explicitement à `None`.
3. **Invalidation ciblée des caches vignette** (`_reload_thumb_qt`) : seulement si les bytes ont réellement changé pour cette entrée (`bytes_changed = entry["bytes"] is not entry_data["bytes"]`) — vide `qt_pixmap_large`/`large_thumb_pil`/`_hash`. Ne recalcule jamais tout en aveugle, seulement ce qui a divergé.
4. **Entrées orphelines** (objets présents dans `images_data` mais absents du snapshot cible, ex. une page ajoutée après ce point d'historique puis annulée) : leurs caches Qt sont invalidés (`qt_pixmap_large`/`qt_qimage_large` mis à `None`) pour libérer la mémoire — l'objet lui-même n'est plus référencé nulle part ensuite, donc éligible au GC Python normal.
5. `state.images_data[:] = new_images_data` — remplacement **en place** de la liste (pas de réassignation `state.images_data = ...`), important si un autre code garde une référence à l'ancienne liste.
6. Restaure `modified`/`needs_renumbering`/tri courant/`all_entries`/`current_directory` depuis le snapshot.
7. **`ComicInfo.xml`** : si le résultat restauré contient une entrée ComicInfo, reparse ses bytes (`parse_comic_info_xml`) et régénère `state.comic_metadata` + `_page_attrs_by_entry_id` (`build_page_attrs_map`) + resynchronise `<Pages>` (`sync_pages_in_xml_data`, `emit_signal=False` — pas d'émission de signal ici, `update_tabs_cb()` s'en charge juste après) — voir skill `comicinfo-metadata-editor`. Si au contraire l'ancien état avait des métadonnées mais plus le nouveau, les efface explicitement.
8. **Restauration de sélection** : par **noms** (`selected_names`, pas par index ni par `id()`) — un undo/redo change potentiellement l'ordre/le contenu de `images_data`, donc un index ou une identité d'objet ne seraient pas fiables ; le nom reste le point de repère le plus stable entre deux snapshots. Si aucun des noms sélectionnés n'existe plus dans le résultat restauré, la sélection est simplement vidée (`clear_selection_cb()`).
9. `render_mosaic_cb()` — toujours appelé, pas d'optimisation "rename seulement" contrairement à l'ancienne version tkinter (commentaire en tête de fichier) : en Qt, `render_mosaic()` recrée de toute façon tous les items de la scène à chaque fois, donc il n'y a rien à gagner à distinguer un cas "juste un renommage" d'un cas "structure changée".
10. `refresh_toolbar_cb()` en dernier — met à jour l'état actif/grisé des boutons Annuler/Redo (voir section suivante).

## `rollback_to_current_state_qt()` — cas particulier : annuler sans avoir avancé l'historique

Distinct d'un vrai undo : restaure le sommet actuel de l'historique (`state.history[state.history_index]`) **sans décrémenter l'index** — utilisé quand une opération a été lancée, un `save_state()` a été appelé, mais l'opération elle-même est **annulée en cours de route** (ex. un aperçu de rotation refusé par l'utilisateur) et qu'il faut défaire les modifications déjà appliquées en mémoire sans que ça compte comme un "vrai" pas d'historique en plus. Câblé notamment dans le contrat de callbacks de fusion de pages (`_merge_callbacks()`, clé `"rollback"`, `panel_widget.py:1552` — voir skill `page-merge`).

**Cas apparenté mais distinct — annulation en cours de lot du panneau d'ajustements** (skill `adjustments-panel`) : quand `_on_apply` traite plusieurs images en boucle et que l'utilisateur clique "Annuler" en cours de traitement, le panneau restaure les bytes/miniatures depuis un snapshot maison pris **avant tout `save_state`** plutôt que d'appeler `rollback_to_current_state_qt` — parce qu'aucun `save_state` n'a encore eu lieu à ce stade (il n'est appelé qu'une fois, avant/après la boucle entière si elle va jusqu'au bout). Ne pas supposer que ce cas passe par ce mécanisme générique.

## Câblage côté panneau — `PanelWidget`

- **`save_state(force=False)`** (`panel_widget.py:1437`) — méthode d'instance, wrapper autour de `save_state_qt(self._state, self._refresh_toolbar_states, force=force)`. **Le point d'entrée à utiliser depuis n'importe quelle fonction métier du panneau** plutôt que d'appeler `save_state_qt` directement avec les bons arguments à chaque fois.
- **`_undo_redo_callbacks()`** (`panel_widget.py:1429`) — construit le tuple `(render_mosaic, clear_selection, update_tabs, refresh_toolbar_states)` attendu par `undo_action_qt`/`redo_action_qt`/`restore_state_qt`. Un seul endroit à modifier si un cinquième callback devient nécessaire — tous les appelants du panneau passent par cette méthode (`*self._undo_redo_callbacks()`), jamais construit à la main ailleurs.
- **`_undo_action()`**/**`_redo_action()`** (`panel_widget.py:1652-1656`) — délèguent directement à `undo_action_qt`/`redo_action_qt` avec ce tuple de callbacks. Câblés sur `Ctrl+Z`/`Ctrl+Y` (raccourcis globaux dans `MosaicView.py`, routés vers `self._active_panel._undo_action()` — voir skill `panels`), sur le menu Édition, et sur les boutons dédiés de la colonne d'icônes (voir skill `icon-toolbar`, state_getters `has_undo`/`has_redo`).
- **`_refresh_toolbar_states`** consulte `can_undo(state)`/`can_redo(state)` (couche pure) pour activer/griser les boutons — c'est la seule chose qui a besoin d'être recalculée après chaque `save_state`/`undo`/`redo`, pas un rendu complet de la mosaïque à ce stade précis (le rendu, lui, vient de `restore_state_qt` pour undo/redo, ou du code appelant pour un simple `save_state`).

## Interaction avec les onglets — `update_tabs_cb`

`_update_tabs` (passé en 3ᵉ position du tuple `_undo_redo_callbacks`) est appelé par `restore_state_qt` **seulement si l'état de `ComicInfo.xml` a changé** (apparu, disparu, ou contenu modifié) — pas à chaque undo/redo inconditionnellement. Rafraîchit l'onglet métadonnées (voir skills `comicinfo-metadata-editor` et `tabs` pour le mécanisme de l'onglet lui-même) pour refléter les champs tels qu'ils étaient au moment du snapshot restauré. Un undo/redo qui ne touche qu'à l'ordre/au contenu des pages sans toucher au XML n'appelle pas ce callback.

## Où `save_state`/`save_state_qt` est réellement appelé

Grep `_save_state_qt\|self.save_state(` dans `panel_widget.py` pour la liste exhaustive à jour plutôt que de supposer qu'un seul endroit suffit — usages notables :
- Suppression de sélection (`_delete_selected_qt`), avec confirmation avant.
- Création/édition de `ComicInfo.xml` (`_edit_comicinfo`, `force=True` des deux côtés — voir skill `comicinfo-metadata-editor`, la présence/absence de l'entrée ComicInfo change la longueur de `images_data` donc `_is_state_identical` le détecterait normalement, mais le `force=True` garantit un point undo même dans les cas limites).
- `NameEdit` (renommage de vignette, `mosaic_canvas.py`) — à la première frappe, avec `force=True` implicite via l'appel direct au callback injecté (voir section `force=True` plus haut).
- Import/fusion d'archive, ajout d'images isolées, import web — voir skills `archive-image-loading`/`web-import`, chacun crée un point undo avant d'étendre `images_data` (sauf si `images_data` était déjà vide, où le premier chargement réinitialise l'historique via `reset_history` plutôt que d'empiler un état).

## `reset_history()` — quand l'historique est vidé plutôt que restauré

Appelé à l'**ouverture** d'un nouveau fichier (`archive_loader.py` — voir skill `archive-image-loading` — ou `pdf_loading_qt.py`, voir skill `pdf-loading`) et à la **fermeture** d'un comic (`force_close_file`, voir skill `file-close`) : `state.history = []`, `state.history_index = -1`. Un nouveau fichier ouvert dans un panneau **ne doit jamais** hériter de l'historique undo du fichier précédent — pas un cas particulier de restauration, une remise à zéro complète. Ne pas confondre avec `_close_split`/fermeture de panneau (voir skill `panels`), qui ne réinitialise pas l'historique explicitement, la destruction de `state` s'en charge (l'objet `AppState` entier disparaît).

## `pop_last_state()` — annuler une sauvegarde inutile a posteriori

Retire le dernier snapshot poussé **sans** avoir touché `images_data` — utilisé quand un `save_state(force=True)` a été fait de façon anticipative (voir section `NameEdit` plus haut) mais que l'action finit par ne rien changer (ex. `resize_dialog_qt.py:1421` : l'utilisateur ouvre le dialogue de redimensionnement, un état est sauvegardé par anticipation, mais annule sans appliquer de changement réel). Décrémente `history_index` et retire l'entrée correspondante — **différent d'un undo réel** : ça ne restaure rien dans `images_data`, ça corrige seulement la pile pour qu'elle ne contienne pas un point mort identique à celui d'avant.

## Comment étendre

- **Ajouter un nouveau champ à préserver dans l'historique** (ex. un nouvel attribut d'`AppState` qui doit survivre à un undo) : l'ajouter au dict `saved_state` dans `save_state_data()` (`undo_redo.py`) **et** à sa restauration correspondante dans `restore_state_qt()` (`undo_redo_qt.py`) — les deux fichiers doivent rester synchronisés, il n'y a pas de schéma partagé validé automatiquement.
- **Ajouter un nouveau callback nécessaire à la restauration** : l'ajouter au tuple retourné par `_undo_redo_callbacks()` (`panel_widget.py`) et à la signature de `restore_state_qt`/`undo_action_qt`/`redo_action_qt` — un seul point d'assemblage par panneau, ne pas construire un tuple ad hoc dans un nouveau call-site.
- **Changer la détection de changement** (`_is_state_identical`) : uniquement dans `undo_redo.py`, fonction pure testable sans Qt — attention, une comparaison plus stricte (byte à byte) coûterait cher sur une grosse archive à chaque frappe dans un champ de nom.
- **Augmenter/diminuer `MAX_HISTORY`** : une seule constante dans `undo_redo.py`, pas de configuration utilisateur exposée actuellement.

## Pièges connus

- **Ne jamais appeler `save_state_data`/`restore_state_qt` directement depuis du code de panneau** — toujours passer par `PanelWidget.save_state()` et par `_undo_action`/`_redo_action` (qui utilisent `_undo_redo_callbacks()`), sinon un callback de rafraîchissement peut être oublié silencieusement.
- **`entry["bytes"]` ne doit jamais être muté en place** — toute fonction qui modifie une image doit réassigner `entry["bytes"] = nouveaux_bytes`, jamais modifier un buffer existant sur place, sous peine de corrompre silencieusement tous les anciens snapshots qui partagent la même référence (voir skill `apply-image-operation`).
- **`force=True` doit rester l'exception, pas l'habitude** — l'utiliser par réflexe partout ferait grossir l'historique de points morts inutiles et userait `MAX_HISTORY` plus vite qu'il ne devrait ; ne l'utiliser que quand l'état n'a *littéralement pas encore changé* au moment de l'appel (cas `NameEdit`/`ComicInfo.xml`).
- **La sélection restaurée après undo/redo est par nom, pas par index** — si une future modification introduit des noms dupliqués intentionnellement (actuellement jamais le cas dans le projet), la restauration de sélection deviendrait ambiguë ; vérifier cette hypothèse avant d'introduire un scénario avec doublons de noms.
- **`rollback_to_current_state_qt` n'est pas un "undo bonus"** — ne pas l'utiliser à la place d'un vrai `undo_action_qt` par erreur : elle ne décrémente jamais l'index, donc rappeler `undo_action_qt` juste après reculerait d'un cran de plus que prévu par rapport à l'intention de l'utilisateur.
