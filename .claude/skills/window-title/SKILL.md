---
name: window-title
description: Localiser ou modifier le titre de la fenêtre principale de MosaicView (nom du fichier ouvert, baseline traduite sinon, affichage combiné en split-view). Utiliser dès qu'une tâche touche à window_title_qt.py, update_window_title, ou app_baseline.
---

# Titre de la fenêtre — MosaicView

Calcule et applique le texte affiché dans la barre de titre Windows de la fenêtre principale. Un seul fichier, deux fonctions : `modules/qt/window_title_qt.py`.

## Les 3 formes du titre

1. **Aucune archive ouverte, mode non-splitté** : `"MosaicView <version> - Le couteau suisse du collectionneur de BD numériques"` — la partie après le tiret vient de la clé de traduction `app_baseline` (`_wt("app_baseline")`, voir skill `add-translation`), pas du code en dur.
2. **Archive ouverte, mode non-splitté** : `"MosaicView <version> - <nom_du_fichier.cbz>"` — juste `os.path.basename(state.current_file)`, pas le chemin complet.
3. **Mode split actif** : titre combiné des deux panneaux, séparés par `|||` — voir section dédiée ci-dessous.

`app_title` (préfixe `"MosaicView <version>"` ou juste `"MosaicView"`) est recalculé à chaque appel : import différé de `MosaicView.__version__` dans un `try/except` — si l'import échoue pour une raison quelconque (ne devrait jamais arriver en usage normal), retombe sur `_wt("app_title")` (clé de traduction, indépendante d'`app_baseline`) plutôt que de planter.

## `_title_for_state(state)` — partie fichier pour un panneau donné

Fonction interne, appelée aussi bien pour le panneau seul (mode non-splitté) que pour chacun des deux panneaux en mode split :
- Si `state.current_file` est renseigné → `os.path.basename(...)`.
- Sinon → `_wt("app_baseline")`, **avec un garde-fou** : si cette clé de traduction est absente/mal configurée, `_wt()` renvoie généralement la clé elle-même (`"app_baseline"`) plutôt qu'une traduction — la fonction détecte ce cas précis (`baseline != "app_baseline"`) et retourne une chaîne vide plutôt que d'afficher le nom de la clé brute dans la barre de titre.

## Mode split — titre combiné

`update_window_title(window, state=None)` ignore le paramètre `state` en mode split (il est utilisé seulement en mode non-splitté) et va lire directement `window._panel._state`/`window._panel2._state` (voir skill `panels`) :

- Les deux panneaux ont un fichier ouvert → `"<app_title> - file1.cbz  |||  file2.cbz"`.
- Un seul des deux → le fichier ouvert d'un côté, la baseline traduite de l'autre côté (si non vide), séparés par `|||` — ou juste le nom de fichier seul si la baseline est vide.
- Aucun des deux → la baseline seule, ou juste `app_title` si la baseline est vide.

Le séparateur `|||` (triple tiret) est spécifique au mode split — à ne pas confondre avec le simple tiret `-` qui sépare `app_title` du reste dans les 3 formes du titre.

## Point d'appel — `PanelWidget._refresh_title()`

Une seule méthode fine (`panel_widget.py:2131-2133`) délègue à `update_window_title(self._main_window, self._state)` — c'est elle, pas `update_window_title` directement, qui est exposée comme callback `update_window_title` dans les dicts de callbacks consommés ailleurs :
- `save-export` (`file_operations_qt.py:1335-1336`) — rafraîchit le titre après création d'un CBZ depuis des images isolées (`create_cbz_from_images`), puisque `state.current_file` passe de vide à renseigné à ce moment précis.
- `panel_widget.py:684` — exposée dans un dict de callbacks plus général (probablement undo/redo ou un autre flux qui peut changer `current_file`).

Aucun autre fichier n'appelle `update_window_title()` directement — toujours passer par `_refresh_title()` du panneau concerné pour bénéficier automatiquement de la résolution `window`/`state` correcte.

## Comment modifier

- **Changer le texte de la baseline** : clé de traduction `app_baseline` (voir skill `add-translation`) — pas ce fichier. `window_title_qt.py` ne fait que consommer la clé, aucun texte n'y est codé en dur pour la baseline elle-même.
- **Changer le séparateur entre nom d'app et fichier** (actuellement `" - "`) ou entre les deux panneaux en split (`"  |||  "`) : directement dans `update_window_title()`, les deux sont des littéraux Python, pas des clés de traduction (un séparateur visuel n'a pas de sens à traduire).
- **Changer le format de version affiché** : bloc `try/except` en tête de `update_window_title()`, `f"MosaicView {v}"` — attention à garder le fallback `_wt("app_title")` fonctionnel si l'import de `MosaicView.__version__` venait à échouer.
- **Ajouter un rafraîchissement du titre à un nouveau point de l'appli** (ex. une nouvelle action qui change `state.current_file`) : appeler `panel._refresh_title()` sur le panneau concerné, jamais `update_window_title()` importé directement dans un nouveau fichier.

## Pièges connus

- **`update_window_title` ignore son paramètre `state` en mode split** — un appelant qui passerait un `state` différent de `window._panel._state`/`window._panel2._state` en espérant influencer le titre en mode split serait silencieusement ignoré ; le mode split relit toujours les deux états directement depuis `window`.
- **Le garde-fou anti clé-brute (`baseline != "app_baseline"`) ne couvre que la baseline**, pas `app_title` — si la clé `app_title` est un jour absente/mal configurée, la barre de titre afficherait littéralement la chaîne `"app_title"` sans garde-fou équivalent.
- **`_title_for_state` est appelée jusqu'à 3 fois par rafraîchissement en mode split** (une fois par panneau, plus implicitement pour la baseline commune) — pas un problème de performance réel (fonction triviale), mais à garder en tête si une future modification y ajoute un calcul coûteux.

## Références croisées

- `add-translation` — clés `app_baseline`/`app_title`, seules sources de texte affiché par ce mécanisme.
- `panels` — `window._panel`/`window._panel2`, source des deux états lus en mode split.
- `save-export` — callback `update_window_title` rafraîchi après `create_cbz_from_images` (le fichier passe de "aucun" à "renseigné").
- `file-close` — bien que non appelé directement par `force_close_file`/`close_file`, le titre redevient la baseline dès que `state.current_file` est remis à `None` à la fermeture ; un futur ajout d'un rafraîchissement explicite à la fermeture devrait suivre le même pattern `_refresh_title()`.
