---
name: status-bar
description: Localiser ou modifier la barre de statut en bas de chaque panneau (texte répertoires/fichiers/sélection à gauche, indicateurs cliquables renumérotation/ZIP/doublons à droite). Utiliser dès qu'une tâche touche à StatusBar, status_bar_qt.py, ou _update_status_bar.
---

# Barre de statut — MosaicView

## Où elle se situe

Un widget `StatusBar` par panneau (`panel1` **et** `panel2`, pas une barre unique
partagée) — voir skill `panels` pour la distinction panel1/panel2. Elle est
placée **dans le panneau central lui-même**, en bas, sous une séparatrice fine
(`_status_separator`), pas via `QMainWindow.setStatusBar()` qui s'étendrait sur
toute la largeur de la fenêtre (sous la colonne d'icônes aussi). Construction :
[panel_widget.py:497-511](modules/qt/panel_widget.py#L497-L511), classe dans
[status_bar_qt.py](modules/qt/status_bar_qt.py).

Hauteur fixe 22px (`setFixedHeight(22)`).

## Les 2 parties (ne jamais les confondre)

La barre est un unique `QHBoxLayout` avec, dans l'ordre :

1. **Partie gauche — le label texte** (`self._label`, stretch=1, occupe tout
   l'espace disponible et se fait tronquer en premier si la fenêtre rétrécit) :
   affiche `dirs répertoire(s)  files fichier(s) (total_size)  selected
   sélectionné(s) (selected_size)` — clé `labels.status_bar`
   ([status_bar_qt.py:188-194](modules/qt/status_bar_qt.py#L188-L194)). Calculé
   à chaque `refresh()` à partir de `state.images_data` et
   `state.selected_indices` :
   - `dirs_count` : nombre de préfixes de dossier distincts dans `orig_name`
     (avant le premier `/`), hors entrées `is_dir`.
   - `files_count` : nombre d'entrées qui ne sont pas des dossiers.
   - `selected_count` : `len(state.selected_indices)`.
   - `total_size` / `selected_size` : somme des `len(entry["bytes"])`, formatée
     via `modules/qt/utils.py::format_file_size()`.

2. **Partie droite — 3 indicateurs cliquables**, séparés par des `QLabel(" ")`
   fins (couleur `theme['separator']`) : renumérotation → ZIP → doublons, dans
   cet ordre visuel de gauche à droite. Chacun est un `_ClickableLabel`
   (sous-classe locale de `QLabel`, curseur main, gère clic gauche **et** clic
   droit séparément via `_on_click`/`_on_right_click`).

| Indicateur | Attribut | Renvoie vers |
|---|---|---|
| Renumérotation | `_renumber_indicator` | skill `renumbering` |
| Compression ZIP | `_zip_indicator` | skill `zip-compression` |
| Doublons | `_duplicate_indicator` | skill `duplicate-detection` |

## Comment elle se met à jour — `refresh(state)`

Un seul point d'entrée : `StatusBar.refresh(state)`
([status_bar_qt.py:152-240](modules/qt/status_bar_qt.py#L152-L240)), appelé via
`PanelWidget._update_status_bar()`
([panel_widget.py:1933-1935](modules/qt/panel_widget.py#L1933-L1935)) :
```python
def _update_status_bar(self):
    self._status_bar.refresh(self._state)
    self._status_bar._overlay_tip._apply_style()
```
`_update_status_bar()` est rappelé après quasiment toute mutation d'état
pertinente (sélection, ouverture/fermeture de fichier, changement de mode de
renumérotation, retour du dialogue ZIP, drag & drop, etc.) — grep
`_update_status_bar` dans `panel_widget.py` pour la liste complète des
déclencheurs plutôt que de supposer qu'un seul endroit suffit.

`state is None` (aucun fichier/panneau actif) → tous les textes sont vidés et
l'indicateur doublons est forcé à l'état grisé/inactif.

### Ce que fait `refresh()` à chaque appel

1. Réapplique `get_current_font(9)` sur le label et les 3 indicateurs +
   séparateurs (règle UI n°3 — jamais un `setFont()` figé une seule fois à la
   création).
2. Réapplique la couleur du thème courant (`get_current_theme()`) sur chaque
   élément (règle UI n°1).
3. Recalcule et réaffiche le texte de gauche (`labels.status_bar`).
4. Recalcule le texte + tooltip de l'indicateur renumérotation selon
   `state.renumber_mode` (0/1/2 → OFF/Auto/Simple).
5. Recalcule le texte + tooltip + curseur + couleur (grisée si aucun fichier
   ouvert) de l'indicateur ZIP selon `state.zip_compression_state` et le
   réglage par défaut du panneau.
6. Recalcule l'icône (badge doublon grisé ou coloré) + tooltip + curseur de
   l'indicateur doublons via `has_any_duplicate(state)`.
7. Pour chacun des 3 indicateurs, après `set_tracked_html(...)`, appelle
   `self._overlay_tip.force_refresh_visible(indicator)` — si le tooltip de
   **cet** indicateur précis est actuellement affiché (ex. la souris est
   dessus au moment du clic), son contenu est rafraîchi immédiatement plutôt
   que d'attendre le prochain `MouseMove`. Voir skill `qt-tooltips` pour le
   mécanisme général de `force_refresh_visible()`. **Ne jamais** remplacer cet
   appel par un test `self._overlay_tip._label.isVisible()` : comme les 3
   indicateurs partagent le même `OverlayTooltip` et que `refresh()` recalcule
   les 3 à la suite dans un seul appel, `isVisible()` resterait vrai après
   qu'un premier indicateur a déjà forcé son propre réaffichage — un second
   indicateur ferait alors apparaître son tooltip à tort, même si la souris ne
   l'a jamais survolé (ex. cliquer sur l'indicateur de renumérotation faisait
   apparaître le tooltip des doublons).

## Tooltips — `OverlayTooltip` obligatoire

Les 3 indicateurs sont suivis par un unique `OverlayTooltip` par statusbar
(`self._overlay_tip`), instancié avec `tooltip_parent` = le **panneau central**
(pas la statusbar elle-même, trop étroite en hauteur pour contenir l'overlay
correctement) — voir skill `qt-tooltips` pour le mécanisme général. Jamais
`setToolTip()` natif ici non plus. Le HTML est construit via
`_format_tooltip()` local ([status_bar_qt.py:48-54](modules/qt/status_bar_qt.py#L48-L54))
qui échappe le texte et le wrap dans un `<p>` avec `max-width: 320px` pour le
retour à la ligne automatique des tooltips longs (ex. explication du niveau
ZIP par défaut).

## Comment ajouter un nouvel indicateur

1. Ajouter un `_ClickableLabel()` dans `__init__`, l'ajouter au layout avec
   `layout.addWidget(indicator, 0)` (stretch 0, il ne doit jamais grandir) et,
   si besoin d'un séparateur avant lui, un `QLabel(" ")` du même style que
   `_indicator_sep`/`_duplicate_sep`.
2. L'enregistrer auprès de l'overlay : `self._overlay_tip.track(indicator, "")`.
3. Ajouter une méthode `set_xxx_click_callback(callback)` (et
   `set_xxx_right_click_callback` si un clic droit est nécessaire) qui affecte
   `indicator._on_click` / `_on_right_click` — ne jamais connecter la logique
   métier directement dans `status_bar_qt.py`, la brancher depuis
   `PanelWidget` (pattern des 3 indicateurs existants,
   [panel_widget.py:503-510](modules/qt/panel_widget.py#L503-L510)) pour garder
   `status_bar_qt.py` indépendant de la logique applicative.
4. Dans `refresh()`, calculer texte/tooltip/curseur/couleur à partir de
   `state`, en respectant les règles UI n°1/2/3 (thème, police, retraduction)
   à chaque appel — pas seulement à la création. Terminer par
   `self._overlay_tip.set_tracked_html(html, indicator)` puis
   `self._overlay_tip.force_refresh_visible(indicator)`, comme les 3
   indicateurs existants (voir point 7 ci-dessus).
5. Ajouter les clés de traduction (`labels.xxx_indicator*`,
   `tooltip.xxx_indicator*`) dans tous les fichiers `locales/*.json` — voir
   skill `add-translation`.

## Pièges

- **Largeur minimale du label de gauche.** `self._label` a
  `setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)` et
  `setMinimumWidth(0)` **ensemble** — l'un sans l'autre ne suffit pas, Qt
  utilise quand même le `sizeHint` basé sur le texte complet dans le calcul du
  minimum du layout sinon, ce qui empêcherait tout le panneau (donc la colonne
  d'icônes à côté) de rétrécir en dessous de la largeur du texte de statut. Ne
  pas retirer l'un des deux réglages en le croyant redondant.
- **Réglage ZIP par panneau, pas global.** `set_zip_level_getter()` doit
  pointer vers `_zip_compression_config()` du panneau concerné (`ConfigManager`
  pour panel1, `Panel2Config` pour panel2) — jamais `get_config_manager()` en
  dur, sinon le réglage du panneau 2 écrase silencieusement celui du panneau 1
  à l'affichage (voir skill `zip-compression`, section pièges).
- **Ne pas coder en dur un texte de statut ailleurs.** Un `.setText(...)`
  appelé sur `self._label` ou un indicateur depuis un callback/worker en dehors
  de `refresh()` resterait figé dans l'ancienne langue au changement de langue
  (règle UI n°2) — toujours repasser par `_update_status_bar()` qui rappelle
  `refresh(state)` en entier plutôt que de patcher un `setText()` isolé.
- **`state is None` doit rester géré en premier** dans `refresh()` — toute
  nouvelle logique ajoutée après le calcul de `dirs_count`/`files_count`/etc.
  doit tenir compte du fait que ce chemin court-circuite tout le reste de la
  fonction.
