---
name: status-bar
description: Localiser ou modifier la barre de statut en bas de chaque panneau (texte répertoires/fichiers/sélection à gauche, indicateurs cliquables renumérotation/ZIP/doublons/marque-pages à droite). Utiliser dès qu'une tâche touche à StatusBar, status_bar_qt.py, ou _update_status_bar.
---

# Barre de statut — MosaicView

## Où elle se situe

Un widget `StatusBar` par panneau (`panel1` **et** `panel2`, pas une barre unique
partagée) — voir skill `panels` pour la distinction panel1/panel2. Elle est
placée **dans le panneau central lui-même**, en bas, sous une séparatrice fine
(`_status_separator`), pas via `QMainWindow.setStatusBar()` qui s'étendrait sur
toute la largeur de la fenêtre (sous la colonne d'icônes aussi). Construction :
`PanelWidget._build_center_panel` ([panel_widget.py](modules/qt/panel_widget.py)), classe dans
[status_bar_qt.py](modules/qt/status_bar_qt.py).

Hauteur fixe 22px (`setFixedHeight(22)`).

## Les 2 parties (ne jamais les confondre)

La barre est un unique `QHBoxLayout` avec, dans l'ordre :

1. **Partie gauche — le label texte** (`self._label`, stretch=1, occupe tout
   l'espace disponible et se fait tronquer en premier si la fenêtre rétrécit) :
   affiche `dirs répertoire(s)  files fichier(s) (total_size)  selected
   sélectionné(s) (selected_size)` — clé `labels.status_bar`
   (dans `StatusBar.refresh`). Calculé
   à chaque `refresh()` à partir de `state.images_data` et
   `state.selected_indices` :
   - `dirs_count` : nombre de préfixes de dossier distincts dans `orig_name`
     (avant le premier `/`), hors entrées `is_dir`.
   - `files_count` : nombre d'entrées qui ne sont pas des dossiers.
   - `selected_count` : `len(state.selected_indices)`.
   - `total_size` / `selected_size` : somme des `len(entry["bytes"])`, formatée
     via `modules/qt/utils.py::format_file_size()`.

2. **Partie droite — 4 indicateurs cliquables** : renumérotation → ZIP →
   doublons → marque-pages, dans cet ordre visuel de gauche à droite. Les deux
   indicateurs texte (renumérotation, ZIP) et le groupe des icônes sont séparés
   par des `QLabel(" ")` fins (`_indicator_sep`, `_duplicate_sep`, couleur
   `theme['separator']`), plus l'espacement du layout principal (10 px). Les
   deux icônes sans texte (doublons, marque-pages) sont regroupées dans un
   sous-layout serré (`icons_layout`, espacement 2 px, sans séparateur) :
   l'espacement prévu entre des textes les écarterait inutilement. Chacun est un
   `_ClickableLabel` (sous-classe locale de `QLabel`, curseur main, gère clic
   gauche **et** clic droit séparément via `_on_click`/`_on_right_click`).

| Indicateur | Attribut | Clic gauche / clic droit | Renvoie vers |
|---|---|---|---|
| Renumérotation | `_renumber_indicator` | change de mode / — | skill `renumbering` |
| Compression ZIP | `_zip_indicator` | réenregistre au taux par défaut / fenêtre du taux par défaut | skill `zip-compression` |
| Doublons | `_duplicate_indicator` | fenêtre des doublons (seulement s'il y en a) / — | skill `duplicate-detection` |
| Marque-pages | `_bookmark_indicator` | bascule le mode marque-pages on/off du panneau / menu « Effacer le marque-page de cette BD » + « Effacer tous les marque-pages » | skill `bookmarks` |

### Indicateur marque-pages

- Icône : le ruban de la mosaïque (`mosaic_canvas._get_bookmark_pixmap`, fichier
  `icons/Bookmark Ribbon.png`), en couleur en mode on, convertie en niveaux de
  gris (cache module `_BOOKMARK_INDICATOR_PIXMAP_GRAY`) en mode off — fonction
  `_get_bookmark_indicator_pixmap(grayed)`. Taille calée sur la police comme le
  badge doublons (`max(14, get_current_font(9).pointSize() + 6)`).
- Le mode est lu par un getter injecté par le panneau,
  `set_bookmarks_enabled_getter(getter)` → `PanelWidget._bookmarks_enabled`,
  jamais par la config globale (le mode est propre à chaque panneau, voir
  skill `bookmarks`).
- Mis à jour par `_refresh_bookmark_indicator()`, appelée dans `refresh()`
  **avant** le court-circuit `state is None` : le mode est un réglage du
  panneau, pas du fichier, l'indicateur reste donc actif et cliquable sans
  fichier ouvert.
- Tooltip : `tooltip.bookmark_indicator_on` / `tooltip.bookmark_indicator_off`
  — trois lignes courtes : l'état seul (« Marque-pages activés »), puis
  « Clic gauche pour les désactiver. » / « Clic droit pour les effacer. ».
  Volontairement sans explication du fonctionnement (pose à la fermeture de
  la visionneuse, reprise de lecture…), qui est dans le mode d'emploi
  (`help.viewer_content`, skill `user-guide`).
- Callbacks branchés dans `PanelWidget._build_center_panel` :
  `set_bookmark_click_callback(self._toggle_bookmarks_enabled)` et
  `set_bookmark_right_click_callback(self._show_bookmark_indicator_menu)`. Le
  menu est construit par `context_menus_qt.show_bookmark_indicator_menu`.

## Comment elle se met à jour — `refresh(state)`

Un seul point d'entrée : `StatusBar.refresh(state)`
([status_bar_qt.py](modules/qt/status_bar_qt.py)), appelé via
`PanelWidget._update_status_bar()`
([panel_widget.py](modules/qt/panel_widget.py)) :
```python
def _update_status_bar(self):
    self._status_bar.refresh(self._state)
    self._status_bar._overlay_tip._apply_style()
```
`_update_status_bar()` est rappelé après quasiment toute mutation d'état
pertinente (sélection, ouverture/fermeture de fichier, changement de mode de
renumérotation ou de marque-pages, retour du dialogue ZIP, drag & drop, changement
de langue ou de thème, réinitialisation, etc.) — grep `_update_status_bar` dans
`panel_widget.py`, `MosaicView.py` et `session_restore_qt.py` pour la liste
complète des déclencheurs plutôt que de supposer qu'un seul endroit suffit.

`state is None` (aucun fichier/panneau actif) → tous les textes sont vidés et
l'indicateur doublons est forcé à l'état grisé/inactif ; l'indicateur
marque-pages, déjà rafraîchi avant ce test, n'est pas concerné.

### Ce que fait `refresh()` à chaque appel

1. Réapplique `get_current_font(9)` sur le label, les indicateurs et les
   séparateurs (règle UI n°3 — jamais un `setFont()` figé une seule fois à la
   création).
2. Réapplique la couleur du thème courant (`get_current_theme()`) sur chaque
   élément (règle UI n°1).
3. Rafraîchit l'indicateur marque-pages (`_refresh_bookmark_indicator()`), puis
   traite le cas `state is None`.
4. Recalcule et réaffiche le texte de gauche (`labels.status_bar`).
5. Recalcule le texte + tooltip de l'indicateur renumérotation selon
   `state.renumber_mode` (0/1/2 → OFF/Auto/Simple).
6. Recalcule le texte + tooltip + curseur + couleur (grisée si aucun fichier
   ouvert) de l'indicateur ZIP selon `state.zip_compression_state` et le
   réglage par défaut du panneau.
7. Recalcule l'icône (badge doublon grisé ou coloré) + tooltip + curseur de
   l'indicateur doublons via `has_any_duplicate(state)`.
8. Pour chaque indicateur, après `set_tracked_html(...)`, appelle
   `self._overlay_tip.force_refresh_visible(indicator)` — si le tooltip de
   **cet** indicateur précis est actuellement affiché (ex. la souris est
   dessus au moment du clic), son contenu est rafraîchi immédiatement plutôt
   que d'attendre le prochain `MouseMove`. Voir skill `qt-tooltips` pour le
   mécanisme général de `force_refresh_visible()`. **Ne jamais** remplacer cet
   appel par un test `self._overlay_tip._label.isVisible()` : comme les
   indicateurs partagent le même `OverlayTooltip` et que `refresh()` les
   recalcule tous à la suite dans un seul appel, `isVisible()` resterait vrai
   après qu'un premier indicateur a déjà forcé son propre réaffichage — un
   second indicateur ferait alors apparaître son tooltip à tort, même si la
   souris ne l'a jamais survolé (ex. un clic sur l'indicateur de renumérotation
   afficherait le tooltip des doublons).

## Tooltips — `OverlayTooltip` obligatoire

Les indicateurs sont suivis par un unique `OverlayTooltip` par statusbar
(`self._overlay_tip`), instancié avec `tooltip_parent` = le **panneau central**
(pas la statusbar elle-même, trop étroite en hauteur pour contenir l'overlay
correctement) — voir skill `qt-tooltips` pour le mécanisme général. Jamais
`setToolTip()` natif ici non plus. Le HTML est construit via
`_format_tooltip()` local (fonction module de `status_bar_qt.py`)
qui échappe le texte et le wrap dans un `<p>` avec `max-width: 320px` pour le
retour à la ligne automatique des tooltips longs (ex. explication du niveau
ZIP par défaut).

## Comment ajouter un nouvel indicateur

1. Ajouter un `_ClickableLabel()` dans `__init__`, l'ajouter au layout avec
   `layout.addWidget(indicator, 0)` (stretch 0, il ne doit jamais grandir) et,
   si besoin d'un séparateur avant lui, un `QLabel(" ")` du même style que
   `_indicator_sep`/`_duplicate_sep` (police et couleur réappliquées dans
   `refresh()`). Un indicateur sans texte (simple icône) va plutôt dans
   `icons_layout`, à côté des icônes doublons et marque-pages, sans
   séparateur.
2. L'enregistrer auprès de l'overlay : `self._overlay_tip.track(indicator, "")`.
3. Ajouter une méthode `set_xxx_click_callback(callback)` (et
   `set_xxx_right_click_callback` si un clic droit est nécessaire) qui affecte
   `indicator._on_click` / `_on_right_click` — ne jamais connecter la logique
   métier directement dans `status_bar_qt.py`, la brancher depuis
   `PanelWidget` (pattern des indicateurs existants, appels `set_*_callback` dans
   `PanelWidget._build_center_panel`) pour garder
   `status_bar_qt.py` indépendant de la logique applicative. Un réglage propre
   au panneau se lit de même par un getter injecté (`set_zip_level_getter`,
   `set_bookmarks_enabled_getter`).
4. Dans `refresh()`, calculer texte/tooltip/curseur/couleur à partir de
   `state`, en respectant les règles UI n°1/2/3 (thème, police, retraduction)
   à chaque appel — pas seulement à la création. Terminer par
   `self._overlay_tip.set_tracked_html(html, indicator)` puis
   `self._overlay_tip.force_refresh_visible(indicator)`, comme les indicateurs
   existants (voir point 8 ci-dessus).
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
- **Réglages par panneau, pas globaux.** `set_zip_level_getter()` doit
  pointer vers `_zip_compression_config()` du panneau concerné et
  `set_bookmarks_enabled_getter()` vers `PanelWidget._bookmarks_enabled`
  (`ConfigManager` pour panel1, `Panel2Config` pour panel2) — jamais
  `get_config_manager()` en dur, sinon le réglage du panneau 2 écrase
  silencieusement celui du panneau 1 à l'affichage (voir skills
  `zip-compression` et `bookmarks`).
- **Ne pas coder en dur un texte de statut ailleurs.** Un `.setText(...)`
  appelé sur `self._label` ou un indicateur depuis un callback/worker en dehors
  de `refresh()` resterait figé dans l'ancienne langue au changement de langue
  (règle UI n°2) — toujours repasser par `_update_status_bar()` qui rappelle
  `refresh(state)` en entier plutôt que de patcher un `setText()` isolé.
- **`state is None` court-circuite la suite de `refresh()`.** Toute logique qui
  dépend du fichier ouvert va après ce test ; tout indicateur qui doit rester
  à jour sans fichier ouvert (comme les marque-pages) doit être rafraîchi
  avant.
