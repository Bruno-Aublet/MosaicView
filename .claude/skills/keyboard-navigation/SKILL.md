---
name: keyboard-navigation
description: Localiser ou modifier la navigation clavier TAB/Shift+TAB entre zones (colonne d'icônes, menu, onglets, canvas), y compris la bascule vers le second panneau en split-view. Utiliser dès qu'une tâche touche à keyboard_nav_qt.py, ZoneTabNavigator, ou à la touche TAB.
---

# Navigation clavier TAB entre zones — MosaicView

Gère le cycle `Tab`/`Shift+Tab` entre les 4 zones focusables d'un panneau (colonne d'icônes → barre de menu → barre d'onglets → canvas), avec bascule automatique vers le second panneau en split-view quand on boucle. Un seul fichier, une seule classe : `modules/qt/keyboard_nav_qt.py::ZoneTabNavigator`.

## Constructeur réel

`ZoneTabNavigator` prend 3 callables, pas des références figées à des widgets (vérifié dans le code et dans l'unique site d'instanciation, `MosaicView.py:229-234`) :

```python
ZoneTabNavigator(
    get_active_panel = lambda: self._active_panel,
    get_other_panel  = lambda: (self._panel2 if self._active_panel is self._panel else self._panel)
                               if self._split_active else None,
    set_active_panel = self._set_active_panel,
)
```

La classe interroge le panneau actif à la demande (`self._panel()` interne) plutôt que de recevoir des références figées à la construction — nécessaire puisque le panneau actif change en cours de session (bascule panel1/panel2 en split-view).

## Les 4 zones

`_zones()` retourne toujours, dans cet ordre fixe : `[p._left_panel, p._menubar, p._tab_bar, p._canvas]` — respectivement la colonne d'icônes (skill `icon-toolbar`), la barre de menu (skill `menu-bar`), la barre d'onglets (skill `tabs`), et le canvas de la mosaïque. `p` est toujours le panneau **actif au moment de l'appel** (`self._get_active_panel()`), jamais mis en cache.

## `focus_next_prev(next_)` — le cycle principal

Appelée depuis `MainWindow.focusNextPrevChild(next_)` (`MosaicView.py:888-889`), le point d'accroche Qt standard pour intercepter la navigation `Tab` au niveau de la fenêtre entière.

1. Détermine la zone actuellement focalisée (`_current_zone_index`) — cas spécial : si la barre de menu a une action active (`menubar.activeAction() is not None`, ex. un menu déroulé), c'est elle qui compte, indépendamment du focus Qt réel. Sinon, remonte l'arbre des parents du widget focalisé (`_is_descendant`) pour savoir à quelle zone il appartient.
2. Avance/recule d'un cran (`step = 1 si next_, -1 sinon`) et tente `_focus_zone()` sur la zone suivante ; si elle est vide (retourne `False`), continue vers la zone d'après.
3. **Bascule de panneau au bouclage** : si l'index calculé "repasse derrière" l'index de départ (`is_wrap`) — c'est-à-dire qu'on a fait un tour complet sans que la boucle for ait déjà réussi à focaliser une zone — et qu'un second panneau existe (`get_other_panel()` non `None`, donc uniquement en split-view), appelle `set_active_panel(other_panel)` puis recommence la recherche de zone focusable **sur le nouveau panneau actif**, en partant du début (`Tab`) ou de la fin (`Shift+Tab`) de sa liste de zones.

## `_focus_zone(zone)` — mise au point spécifique à chaque zone

Pas de logique uniforme — chaque zone a son propre traitement :
- **Colonne d'icônes** (`p._left_panel`) : focalise le premier bouton icône (`p._icon_toolbar.get_first_icon()`) si disponible, sinon le panneau lui-même. Toujours considérée non-vide (retourne toujours `True`).
- **Barre de menu** (`p._menubar`) : cas particulier le plus complexe — si plus d'une action existe, active la 2e action (`actions[1]`, la première étant probablement le chevron de la colonne d'icônes, voir skill `menu-bar`) via `setActiveAction`, **puis referme immédiatement le sous-menu qui vient de s'ouvrir** en simulant un appui `Escape` (`QKeyEvent` synthétique envoyé via `QCoreApplication.sendEvent`, différé d'un tick avec `QTimer.singleShot(0, ...)`) — le but est de donner le focus clavier à la barre de menu **sans** laisser un menu déroulé visuellement ouvert. Vérifie la validité de l'action via `shiboken6.isValid` avant d'y toucher (protection contre un objet C++ déjà détruit entre-temps, voir skill `undo-redo`/mémoire QThread pour le même genre de garde-fou ailleurs dans le projet).
- **Barre d'onglets** (`p._tab_bar`) : focalise le bouton Mosaïque ou, à défaut, Métadonnées (`_btn_mosaic or _btn_metadata`) — retourne `False` si aucun des deux boutons n'existe (cas normalement impossible en pratique).
- **Canvas** (`p._canvas`) : seulement si `state.images_data` n'est pas vide (retourne `False` sinon, donc **ignoré dans le cycle si le panneau est vide** — un panneau sans image chargée n'a pas de zone canvas focalisable). Si aucun item n'a le focus (`_focused_idx is None`), positionne le focus sur le premier item (`_set_focus(0)`) et défile la vue pour le rendre visible (`_scroll_to`).

## `key_filter(obj, event)` — interception TAB dans les sous-menus

Appelée depuis `MainWindow.eventFilter(obj, event)` (`MosaicView.py:881`), installé à la fois sur `QApplication.instance()` (global) et sur la barre de menu elle-même (`MosaicView.py:235-237`). Nécessaire car Qt gère `Tab`/`Shift+Tab`/`Space` **en interne** dans un `QMenu` déroulé, ce qui court-circuiterait `focusNextPrevChild` sans cette interception :

- `_is_in_menubar(obj)` vérifie si l'objet événementiel est la barre de menu du panneau actif **ou un de ses `QMenu`** (remonte la chaîne de parents des menus, puisqu'un sous-menu de sous-menu n'a pas directement la menubar comme parent immédiat).
- `Tab`/`Backtab` (Shift+Tab) dans ce contexte → appelle directement `focus_next_prev`, `return True` (événement consommé, empêche Qt de faire son propre traitement de TAB à l'intérieur du menu).
- `Space` sur un `QMenu` dont l'action active est activable et n'a pas de sous-menu → déclenche l'action (`act.trigger()`) et referme **toute la chaîne** de menus parents (`while isinstance(top.parentWidget(), QMenu): top = top.parentWidget()`) — Qt ne fait normalement ceci qu'avec `Enter`/clic, pas `Space` par défaut sur toutes les plateformes ; ce comportement est ajouté explicitement ici.

## Comment modifier

- **Ajouter une nouvelle zone au cycle** (ex. la minimap, skill `minimap`) : ajouter la référence à la liste retournée par `_zones()`, puis une nouvelle branche `elif zone is p._ma_nouvelle_zone:` dans `_focus_zone` suivant le pattern existant (retourner `True` si la mise au point a réussi, `False` si la zone doit être sautée).
- **Changer l'ordre du cycle** : modifier l'ordre des éléments dans la liste retournée par `_zones()` — le reste de la logique (`focus_next_prev`, calcul de wrap) est indépendant de l'ordre exact.
- **Changer le comportement de bascule de panneau** : `focus_next_prev`, bloc `if is_wrap and other_panel is not None...` — actuellement bascule **et** tente immédiatement de focaliser une zone du nouveau panneau (jamais un simple changement de panneau actif sans mise au point).

## Pièges connus

- **`_focus_zone` sur la barre de menu simule un appui Escape avec un délai d'un tick** — un changement qui rendrait ce timing synchrone risquerait de fermer le menu **avant** qu'il ait fini de s'ouvrir visuellement (l'ouverture elle-même est déclenchée par `setActiveAction`, qui a son propre effet asynchrone dans Qt).
- **`shiboken6.isValid` est vérifié avant de toucher à `act`** dans le callback différé — si cette vérification était retirée, un changement de menu (ex. fermeture du panneau) entre le moment où `_focus_zone` est appelée et le moment où le `QTimer.singleShot` se déclenche provoquerait un `RuntimeError: Internal C++ object already deleted`, le même genre de crash que documenté pour les `QThread` dans le skill `undo-redo`/mémoire `project_qthread_lifecycle`.
- **Le canvas est une zone "invisible" si le panneau est vide** — un utilisateur naviguant au clavier dans un panneau sans image chargée ne verra jamais le focus s'arrêter sur le canvas, ce n'est pas un bug mais peut surprendre si on cherche "pourquoi TAB saute cette zone".
- **`key_filter` est installé à 2 endroits différents** (`QApplication.instance()` et `p._menubar` directement) — un futur ajout de barre de menu (second panneau en split-view par exemple) doit vérifier si son propre `installEventFilter(self)` est nécessaire, ou si le filtre global sur `QApplication` suffit déjà (à vérifier au cas par cas, la duplication actuelle suggère qu'un seul des deux ne suffisait pas dans la pratique).

## Références croisées

- `icon-toolbar` — `get_first_icon()`, zone "colonne d'icônes" du cycle.
- `menu-bar` — comportement particulier de mise au point sur la barre de menu, fermeture du sous-menu simulée.
- `tabs` — `_btn_mosaic`/`_btn_metadata`, zone "barre d'onglets" du cycle.
- `mosaic-thumbnails` — `_focused_idx`/`_set_focus`/`_scroll_to`, zone "canvas" du cycle.
- `panels` — `_active_panel`/`_split_active`/`_set_active_panel`, mécanisme de bascule entre panel1/panel2 utilisé lors du bouclage TAB.
