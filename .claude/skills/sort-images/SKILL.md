---
name: sort-images
description: Localiser ou modifier le tri des images de la mosaïque (nom, type, poids, largeur, hauteur, résolution, DPI, croissant/décroissant). Utiliser dès qu'une tâche touche à sort_images, sorting.py/sorting_qt.py, ou à un des 3 points d'entrée UI du tri.
---

# Tri des images de la mosaïque — MosaicView

Le tri réordonne définitivement `state.images_data` selon un critère choisi par l'utilisateur (nom, type, poids, largeur, hauteur, résolution, DPI), avec bascule croissant/décroissant au second clic sur le même critère. C'est une opération **manuelle et ponctuelle** : contrairement à la [[renumbering]] (qui renomme sans jamais trier) elle ne se déclenche jamais automatiquement après un drag & drop, un merge ou un import.

## Où — fichiers impliqués

- **`modules/qt/sorting.py`** — logique métier pure (aucun import Qt) : `get_sort_key(entry, sort_method, ...)` calcule la clé de tri d'une entrée, `sort_images(sort_method, callbacks)` fait le tri effectif de `state.images_data` et orchestre bascule d'ordre + sauvegarde undo + réaffichage.
- **`modules/qt/sorting_qt.py`** — couche Qt fine : `sort_images_qt(...)` adapte `sort_images()` aux callbacks Qt du panneau (save_state, render_mosaic, refresh_toolbar), `show_sort_menu_qt(parent, sort_method_cb)` affiche le menu popup déclenché par le bouton de la colonne d'icônes.
- **`modules/qt/panel_widget.py:1370-1376`** — `_sort_images(sort_method)` et `_show_sort_menu(event)`, les deux méthodes de `PanelWidget` qui font le pont entre l'UI et `sorting_qt.py`. Toujours appelées avec `self._state` explicite (jamais de swap du state global).
- **`modules/qt/state.py:77-79`** — `state.current_sort_method` (None ou l'un des 7 identifiants ci-dessous) et `state.current_sort_order` ("asc"/"desc"), **par panneau** (chaque `PanelWidget` a son propre `AppState`).
- **`modules/qt/entries.py:581`** — `get_image_metadata(entry)` : lit les dimensions/DPI d'une image via PIL sans la charger entièrement (utilisé pour les tris width/height/resolution/dpi).
- **`modules/qt/archive_loader.py:126`** — `_natural_sort_key(text)` : tri alphanumérique naturel (insensible à la casse, "10" > "9"), réutilisé pour le tri "name".
- **`modules/qt/undo_redo.py:107-108`** et **`undo_redo_qt.py:154-155`** — `current_sort_method`/`current_sort_order` font partie du snapshot undo/redo, donc annuler/refaire restaure aussi l'état de tri affiché (mais pas l'ordre des images lui-même au-delà de ce que le snapshot général restaure déjà).

## Ce que ça fait, précisément

`sort_images(sort_method, callbacks)` (`sorting.py:51`) :
1. Ne fait rien si `state.images_data` est vide.
2. **Bascule d'ordre** : si `sort_method` est identique au tri déjà actif (`state.current_sort_method`), inverse `state.current_sort_order` (asc↔desc). Sinon adopte le nouveau critère en ordre `asc`.
3. Trie `state.images_data` en place (`list.sort`) avec `key=lambda e: get_sort_key(e, sort_method, ...)` et `reverse=(order == "desc")`.
4. Marque `state.modified = True`.
5. Appelle `callbacks["save_state"]()` — **après** le tri, comme toutes les autres opérations qui modifient la mosaïque (point undo créé une fois le nouvel ordre en place, cohérent avec le pattern décrit dans [[undo-redo]]).
6. Réaffiche (`render_mosaic`) et rafraîchit l'état des boutons de la toolbar (`update_button_text`, en réalité `_refresh_toolbar_states` générique, pas un indicateur visuel dédié au tri).

Le tri porte sur **toutes** les entrées de `images_data`, images et non-images confondues (dossiers, `ComicInfo.xml`) — `get_sort_key` retourne `0` par défaut pour un critère non applicable à une entrée (ex. une entrée non-image lors d'un tri "resolution"), donc les non-images se retrouvent groupées ensemble à une extrémité du tri plutôt que conserver une position alphanumérique relative. C'est différent de la renumérotation, qui elle repositionne spécifiquement les non-images ([[renumbering]] → `reposition_non_images`).

## Quand — les 3 points d'entrée UI

Les trois convergent vers `panel._sort_images(sort_method)` → `sort_images_qt(...)` → `sort_images(...)`. Aucun n'a de logique propre, ce sont uniquement des présentations différentes du même menu à 7 entrées :

1. **Bouton icône "sort" de la colonne d'icônes** (`icon_toolbar_qt.py:80` définition, `:157` règle d'activation `has_images`) — clic déclenche `_show_sort_menu(event)` qui affiche un `QMenu` popup (`show_sort_menu_qt`) sous le curseur (`QCursor.pos()`). Voir [[icon-toolbar]] pour le mécanisme générique de ce bouton (règle d'activation contextuelle, `_ACTIVATION_RULES`).
2. **Menu contextuel clic-droit sur la mosaïque** (`context_menus_qt.py:258-270`) — sous-menu "Trier les pages" (titre `menu.sort`), désactivé si `not has_images`. Voir [[qt-context-menus]] pour le mécanisme générique de ces menus.
3. **Barre de menu classique** (`menubar_qt.py:261-273`, callbacks câblés dans `menubar_callbacks_qt.py:118` → `mw._sort_images`) — même sous-menu à 7 entrées, activé/désactivé selon `has_images`.

Dans les 3 cas l'activation dépend uniquement de `has_images()` (au moins une image dans le panneau) — pas besoin de sélection.

## Les 7 types de tri possibles

Identifiants passés comme `sort_method` (string) — voir `get_sort_key` (`sorting.py:8-48`) :

| `sort_method` | Critère | Calcul de la clé |
|---|---|---|
| `"name"` | Nom de fichier | `_natural_sort_key(entry["orig_name"])` — tri alphanumérique naturel insensible à la casse |
| `"type"` | Extension | `entry["extension"].lower()` |
| `"weight"` | Poids/taille en octets | `len(entry["bytes"])`, `0` si `bytes` est `None` |
| `"width"` | Largeur en pixels | `get_image_metadata(entry)["size"][0]`, `0` si pas de métadonnées |
| `"height"` | Hauteur en pixels | `get_image_metadata(entry)["size"][1]`, `0` si pas de métadonnées |
| `"resolution"` | Résolution totale | `width * height` |
| `"dpi"` | DPI (résolution physique) | premier élément si `dpi` est un tuple, sinon la valeur brute, `0` si absent |

Chaque critère bascule indépendamment asc/desc au second clic — cliquer "Par nom" puis "Par poids" ne bascule pas l'ordre du poids (il repart en asc), seul un second clic consécutif sur le **même** critère bascule.

## Clés de traduction

Les 3 points d'entrée UI utilisent la clé racine **`sort_menu.*`** (`sort_menu.sort_name`, `sort_menu.sort_type`, `sort_menu.sort_size`, `sort_menu.sort_width`, `sort_menu.sort_height`, `sort_menu.sort_resolution`, `sort_menu.sort_dpi`) pour le libellé des entrées de menu, et `menu.sort` pour le titre du sous-menu/bouton. Voir skill [[add-translation]] pour la procédure de traduction.

## Comment modifier le tri existant

- **Changer le calcul d'une clé de tri** (ex. rendre le tri "type" insensible à un préfixe) : uniquement dans `get_sort_key()` (`sorting.py`), fonction pure sans dépendance Qt, facile à isoler.
- **Changer le comportement de bascule d'ordre** (ex. ne jamais repartir en desc) : dans `sort_images()` (`sorting.py:69-73`).
- **Changer où/comment le menu de tri s'affiche** (ex. position du popup, style) : `show_sort_menu_qt()` (`sorting_qt.py`) pour le popup du bouton icône ; `context_menus_qt.py:258-270` pour le clic-droit ; `menubar_qt.py:261-273` pour la barre de menu. **Modifier les 3 en cohérence** si le changement doit s'appliquer partout (ex. ajouter un nouveau critère doit toucher les 3 endroits, voir plus bas).
- **Le tri ne fait rien de visible** : vérifier `state.images_data` n'est pas vide et que `has_images()` retourne `True` pour l'activation du bouton/menu concerné.

## Comment ajouter un nouveau type de tri

Il faut toucher **6 points**, dans cet ordre :

1. **`sorting.py::get_sort_key`** — ajouter une branche `elif sort_method == "mon_critere": return ...` qui retourne la clé de comparaison (nombre ou tuple comparable).
2. **`sorting_qt.py::show_sort_menu_qt`** — ajouter `menu.addAction(_("sort_menu.sort_mon_critere"), lambda: sort_method_cb("mon_critere"))`.
3. **`context_menus_qt.py`** (bloc `sort_submenu`, ~ligne 261-267) — ajouter la même `addAction` avec la même clé de traduction et le même identifiant `sort_method`.
4. **`menubar_qt.py`** (liste `[key, label_key]`, ~ligne 262-269) — ajouter le tuple `("mon_critere", "sort_menu.sort_mon_critere")`.
5. **`locales/*.json`** — ajouter la clé `sort_menu.sort_mon_critere` dans **toutes** les langues (voir skill [[add-translation]] pour la procédure complète, y compris les langues fictives CSUR et l'arménien).
6. Si le critère nécessite une métadonnée pas encore lue par `get_image_metadata()` (`entries.py:581`), l'ajouter à ce dictionnaire plutôt que de relire l'image ailleurs — c'est le point de passage unique pour les métadonnées légères d'image dans tout le projet.

Aucun point undo/redo à modifier : `current_sort_method`/`current_sort_order` sont génériques (stockent n'importe quelle string), le nouveau critère est automatiquement pris en charge par le snapshot existant.

## Portée et limites

- Le tri est **par panneau** — trier le panneau 1 ne touche jamais le panneau 2 en split-view (voir [[panels]] pour l'architecture générale panel1/panel2). Chaque `AppState` a son propre `current_sort_method`/`current_sort_order`.
- Le tri s'applique à `state.images_data`, jamais à `state.all_entries` séparément (les deux listes sont normalement synchronisées ailleurs dans le code — hors du périmètre de cette skill).
- Après un tri, `state.needs_renumbering` n'est pas modifié automatiquement par le tri lui-même — trier ne déclenche jamais de renumérotation ; c'est à l'utilisateur de renuméroter ensuite s'il le souhaite (bouton/menu dédié, voir [[renumbering]]).
- La Bibliothèque (panneau virtuel, voir [[library]]) n'a pas de bouton de tri — ce mécanisme ne s'applique qu'aux panneaux réels affichant une mosaïque chargée.
