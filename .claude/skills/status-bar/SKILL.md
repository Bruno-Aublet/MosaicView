---
name: status-bar
description: Localiser ou modifier la barre de statut en bas de chaque panneau (texte répertoires/fichiers/sélection à gauche, indicateurs cliquables renumérotation/ZIP/doublons/marque-pages à droite, indicateur de mémoire en dernier). Utiliser dès qu'une tâche touche à StatusBar, status_bar_qt.py, _update_status_bar, memory_usage.py (mesure de la mémoire de MosaicView et de ses programmes annexes, estimation d'un panneau, seuils de couleur), MainWindow._refresh_memory_indicators ou aux icônes Memory_Gauge_*.
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
   sous-layout serré (`icons_layout`, espacement 2 px, aucun séparateur entre
   elles) : l'espacement prévu entre des textes les écarterait inutilement.
   `_duplicate_sep` est placé en tête de ce sous-layout, et non dans le layout
   principal : l'écart entre l'indicateur ZIP et l'icône doublons est ainsi
   plus court (10 px avant le séparateur, 2 px après) que celui entre deux
   indicateurs texte. Chacun est un
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

### Indicateur de mémoire (dernière position, après les icônes)

Un `QLabel` simple (`_memory_indicator`, **pas** un `_ClickableLabel` : aucun
effet au clic, pas de curseur main), placé à la fin d'`icons_layout`, sans
séparateur, à 3 px de plus que l'écart de 2 px entre les icônes doublons et
marque-pages : marge gauche du label (`setContentsMargins(3, 0, 0, 0)`), pas
`addSpacing()`, qui recevrait aussi l'écart du layout de chaque côté.
Il affiche la part de la mémoire physique du PC occupée par
MosaicView et ses programmes annexes (`labels.memory_indicator`,
« {percent}% » ; « %{percent} » en turc), en couleur de seuil, ou un tiret
grisé (`labels.memory_indicator_unknown`) si la mesure manque.

- **Mesure** — `modules/qt/memory_usage.py` (sans Qt) :
  - `measure()` → `MemoryMeasure(app, helpers, ram, helper_names)` ou `None`
    (Windows ne la donne pas). Plage de travail lue par `ctypes`
    (`K32GetProcessMemoryInfo`, `GlobalMemoryStatusEx`, liste des process par
    `CreateToolhelp32Snapshot`). `app` compte MosaicView et, dans la version
    « un seul fichier » de l'exe seulement (`_is_onefile` : `sys._MEIPASS`
    hors du dossier de l'exe — le `_internal` de la version dossier n'en est
    pas), son programme de démarrage PyInstaller, qui reste ouvert toute la
    session (`_is_launcher` : même exe, créé avant MosaicView).
  - **Programmes annexes** : seuls ceux que `helper_kind` reconnaît par leur
    exe — même exe que MosaicView (ou `sys._base_executable` dans un
    environnement virtuel) → « PDF » (process de `_spawn_pdf_process` et de
    `PdfProbe`) ; `7z.exe` → « 7-Zip » ; `UnRAR.exe` → « UnRAR »
    (`HELPER_NAMES`, ordre d'affichage). `_descendants` parcourt les
    descendants de MosaicView sans suivre un autre programme (Bloc-notes,
    application par défaut d'un fichier, Explorateur), qui n'est jamais
    compté, ni ce qu'il lance ; un process dont le parent annoncé a été créé
    après lui est écarté (numéro de process réattribué par Windows). **Un
    nouveau programme externe lancé pour le travail de MosaicView** s'ajoute
    à `helper_kind` et `HELPER_NAMES`, sinon sa mémoire n'est pas comptée.
  - `panel_memory_estimate(state)` : octets des pages du document **et** de
    son historique (`state.history`, propriétés `meta` comprises), images
    Pillow décodées (`_pil_size`, selon le mode) et images Qt (reconnues sans
    importer Qt : `sizeInBytes`, sinon `width × height × depth`), chaque objet
    compté une fois (l'historique partage les octets des pages inchangées).
    Coûteux sur une grosse BD : jamais appelée à chaque mesure, seulement
    pour l'info-bulle affichée (plus bas).
  - `memory_level(part)` : `ORANGE_FROM = 0.50`, `RED_FROM = 0.75` (vert sous
    50 %, orange de 50 à 75 % exclu, rouge au-delà) ; `percent(size, ram)`.
- **Une seule mesure pour toute l'application** : `MainWindow._memory_timer`
  (`MosaicView.py`, 1 s, plus une mesure au démarrage) appelle
  `_refresh_memory_indicators`, qui ne fait rien tant que la fenêtre est
  réduite, range la mesure par `status_bar_qt.set_memory_measure` (variable
  de module `_memory_measure`) puis appelle `StatusBar.refresh_memory()` de
  chaque panneau. Une barre créée entre deux mesures (ouverture du double
  panneau) affiche la dernière tout de suite, `refresh()` appelant aussi
  `refresh_memory()` (avant le court-circuit `state is None` : l'indicateur
  vaut pour toute l'application, même sans fichier ouvert).
- **Couleurs** : clés `memory_green` / `memory_orange` / `memory_red` de
  `THEMES` (skill `dark-mode`), une teinte par thème.
- **Info-bulle** (`_memory_tooltip_html`) : jauge de la couleur du seuil à
  gauche, puis « MosaicView : {size} ({percent}%…) » (programmes annexes
  compris), « dont programmes annexes ({names}) : … » seulement quand l'un
  d'eux tourne (`names` : `helper_names` joints par des virgules, noms jamais
  traduits), et « Ce panneau : environ … » (`panel_memory_estimate` du state
  donné par `set_memory_state_getter`, branché sur `lambda: self._state` dans
  `PanelWidget._build_center_panel` — jamais le singleton). Clés
  `tooltip.memory_indicator_app/_helpers/_panel/_unknown`. Texte échappé
  (`html.escape`). Format `<table>` (skill `qt-tooltips`).
  - **Calcul seulement quand quelqu'un la voit** : `StatusBar` est installé
    comme filtre d'événements sur `_memory_indicator` **après** `track()`
    (un filtre installé en dernier passe en premier) : sur `QEvent.Enter`,
    `_update_memory_tooltip()` calcule le contenu juste avant que le suivi du
    tooltip ne l'affiche. Ensuite, `refresh_memory()` ne le recalcule (puis
    `force_refresh_visible`) que si `OverlayTooltip.is_visible_for(indicateur)`.
  - **Jauge** : `icons/Memory_Gauge_0x_<Couleur>_48.png` (48 px) et
    `…_48@2x.png` (96 px), livrées déjà réduites, tirées des 512 px
    `Memory_Gauge_0x_<Couleur>.png`. Le texte riche de Qt réduit une image
    sans lissage : `_gauge_icon_src(level, ratio)` choisit lui-même la
    version 96 px dès que `devicePixelRatioF() > 1` (la recherche
    automatique des fichiers `@2x` de Qt ne fonctionne pas avec une adresse
    `file:///`) et l'affiche à `_GAUGE_TOOLTIP_SIZE` (48) px ; fichier
    absent → info-bulle sans image.
- **Tests** : `tests/test_memory_usage.py` (seuils, estimation d'un panneau,
  programmes annexes et programme de démarrage, vraie mesure avec un
  programme lancé par le test, `refresh_memory` et `_memory_tooltip_html`
  extraites par AST).

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
3. Rafraîchit l'indicateur marque-pages (`_refresh_bookmark_indicator()`) et
   l'indicateur de mémoire (`refresh_memory()`), puis traite le cas
   `state is None`.
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
