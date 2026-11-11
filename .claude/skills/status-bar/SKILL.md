---
name: status-bar
description: Localiser ou modifier la barre de statut en bas de chaque panneau (texte répertoires/fichiers/sélection à gauche, indicateurs cliquables renumérotation/ZIP/doublons/marque-pages/pages « Supprimée » à droite, indicateur de mémoire en dernier). Utiliser dès qu'une tâche touche à StatusBar, status_bar_qt.py, _update_status_bar, memory_usage.py (mesure de la mémoire de MosaicView et de ses programmes annexes, maintien des programmes annexes entre deux mesures : HelperHold / hold_helper_programs / helper_programs_finished / forget_helper_programs, estimation d'un panneau, pages déjà lues par un chargement en cours : reading_started / reading_finished / pages_read / state.pages_being_read, seuils de couleur), MainWindow._sample_memory / _refresh_memory_indicators / _show_memory ou aux jauges de memory_gauge/ (tools/make_memory_gauges.py, sources icons/Memory_Gauge_00*.png).
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
     via `modules/qt/utils.py::format_file_size()` (unités traduites `units.*`,
     séparateur décimal de la langue ; la barre est recalculée au changement de
     langue, `MosaicView._on_language_change`).

2. **Partie droite — 5 indicateurs cliquables** : renumérotation → ZIP →
   doublons → marque-pages → pages « Supprimée », dans cet ordre visuel de gauche à droite. Les
   écarts renumérotation → ZIP et ZIP → doublons valent tous deux 10 px :
   l'espacement du layout principal (`layout.setSpacing(10)`), sans aucun
   widget séparateur (un `QLabel(" ")` ajouterait la largeur d'une espace,
   variable selon la police et sa taille, et rendrait ces écarts inégaux). Les
   trois icônes sans texte (doublons, marque-pages, pages « Supprimée ») sont regroupées dans un
   sous-layout serré (`icons_layout`, espacement 2 px) : l'espacement prévu
   entre des textes les écarterait inutilement. Ce sous-layout est ajouté au
   layout principal juste après l'indicateur ZIP, dont il est donc séparé par
   les mêmes 10 px. Chacun est un
   `_ClickableLabel` (sous-classe locale de `QLabel`, curseur main, gère clic
   gauche **et** clic droit séparément via `_on_click`/`_on_right_click`).

| Indicateur | Attribut | Clic gauche / clic droit | Renvoie vers |
|---|---|---|---|
| Renumérotation | `_renumber_indicator` | change de mode / — | skill `renumbering` |
| Compression ZIP | `_zip_indicator` | réenregistre au taux par défaut / fenêtre du taux par défaut | skill `zip-compression` |
| Doublons | `_duplicate_indicator` | fenêtre des doublons (seulement s'il y en a) / — | skill `duplicate-detection` |
| Marque-pages | `_bookmark_indicator` | bascule le mode marque-pages on/off du panneau / menu « Effacer le marque-page de cette BD » + « Effacer tous les marque-pages » | skill `bookmarks` |
| Pages « Supprimée » | `_skip_deleted_indicator` | bascule le réglage « pages de type Supprimée non lues » de la visionneuse (toute l'application) / — | skill `viewers` |

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

### Indicateur pages « Supprimée » (visionneuse)

- Icône `icons/Deleted_Pages.png`, réduite à `_SKIP_DELETED_ICON_PX` (64 px)
  avant tout traitement (le fichier est très grand), en couleur quand le
  réglage est actif, sinon en niveaux de gris et à opacité réduite
  (`_SKIP_DELETED_GRAYED_ALPHA`, 0.6 : le gris seul restait trop proche de
  l'icône en couleur) — `_get_skip_deleted_indicator_pixmap(grayed)`, cache
  module `_SKIP_DELETED_PIXMAPS`. Taille affichée calée sur la police comme
  les autres icônes.
- Réglage lu par un getter injecté par le panneau,
  `set_skip_deleted_getter(getter)` → `PanelWidget._skip_deleted_pages`
  (config `viewer_skip_deleted_pages`, commune aux deux panneaux, skill
  `config-storage`).
- Mis à jour par `_refresh_skip_deleted_indicator()`, appelée dans `refresh()`
  juste après `_refresh_bookmark_indicator()`, donc aussi sans fichier ouvert.
- Tooltip : `tooltip.skip_deleted_pages_indicator_on` / `_off` — l'état, puis
  la ligne du clic gauche ; `force_refresh_visible` après chaque mise à jour.
- Clic gauche : `set_skip_deleted_click_callback(self._toggle_skip_deleted_pages)`
  (`PanelWidget`) — bascule le réglage, rafraîchit la barre de statut, la
  mosaïque et la minimap des deux panneaux (grisé des pages « Supprimée »,
  `_repaint_deleted_page_marks`) et les visionneuses ouvertes
  (`refresh_image_viewers_skip_deleted_pages`). Clic droit : rien.

### Indicateur de mémoire (dernière position, après les icônes)

Un `QLabel` simple (`_memory_indicator`, **pas** un `_ClickableLabel` : aucun
effet au clic, pas de curseur main), placé à la fin d'`icons_layout`, sans
séparateur, à 3 px de plus que l'écart de 2 px entre les icônes sans texte : marge gauche du label (`setContentsMargins(3, 0, 0, 0)`), pas
`addSpacing()`, qui recevrait aussi l'écart du layout de chaque côté.
Il affiche la part de la mémoire physique du PC occupée par
MosaicView et ses programmes annexes (`labels.memory_indicator`,
« {percent}% » ; « %{percent} » en turc), en couleur de seuil, ou un tiret
grisé (`labels.memory_indicator_unknown`) si la mesure manque.

- **Mesure** — `modules/qt/memory_usage.py` (sans Qt) :
  - `measure()` → `MemoryMeasure(app, helpers, ram, helper_names,
    helper_sizes)` ou `None` (Windows ne la donne pas) ; `helpers` est le
    total des programmes annexes, `helper_sizes` les paires (nom, octets) de
    chacun, dans l'ordre de `HELPER_NAMES` (construites par `_with_helpers`). Plage de travail lue par `ctypes`
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
    Impossible de savoir quel panneau a lancé un programme annexe : pour
    Windows, son parent est le process MosaicView, quel que soit le thread
    qui l'a lancé (et `rarfile` lance UnRAR lui-même, sans en rendre le
    numéro).
  - **Maintien entre deux mesures** (`HelperHold`) : un programme annexe ne
    vit souvent que le temps d'une page (`rarfile` lance un UnRAR par
    `archive.read()` d'une page compressée, `_read_7z_file` un 7-Zip par
    membre), environ un tiers du temps du chargement. Une mesure qui le voit
    remplace sa valeur et relance son délai ; une mesure qui ne le voit pas
    garde sa dernière valeur jusqu'à `HELPER_HOLD_SECONDS` (3 s) après la
    dernière fois qu'il a été vu. Un délai par programme ; délai en temps
    réel (`time.monotonic()`), pas en nombre de mesures. Le total `helpers`
    (donc pourcentage, couleur, aiguille) suit les valeurs maintenues, pas
    seulement la ligne de l'info-bulle. Instance unique `_helper_hold`,
    utilisée par `hold_helper_programs(measure, now)`.
  - **Fin d'une opération** : `helper_programs_finished()` (thread Qt
    seulement) oublie les valeurs maintenues et fait mesurer tout de suite
    (`_measure_now`, posé par `set_measure_now` dans `MainWindow.__init__` :
    `_refresh_memory_indicators`) ; appelée en fin de chargement
    (`PanelWidget._on_loading_finished`, archives et PDF, annulation et
    erreur comprises) et d'ajout à un comic ouvert (`_cleanup` de
    `import_merge_qt.import_and_merge_archive` et de
    `pdf_loading_qt.import_and_merge_pdf`). `forget_helper_programs()`
    (tout thread, sous verrou) oublie sans mesurer : appelée par
    `BatchClaims.finish()` depuis le thread du lot (skill `batch-processing`),
    la mesure suivante (≤ 250 ms) suffit. Un programme d'une autre opération
    encore en cours revient dès qu'une mesure le voit. Une nouvelle opération
    longue qui lance des programmes annexes appelle l'une des deux à sa fin,
    sinon ils restent affichés jusqu'à l'expiration de leur délai.
  - `panel_memory_estimate(state)` : octets des pages du document **et** de
    son historique (`state.history`, propriétés `meta` comprises), images
    Pillow décodées (`_pil_size`, selon le mode) et images Qt (reconnues sans
    importer Qt : `sizeInBytes`, sinon `width × height × depth`), chaque objet
    compté une fois (l'historique partage les octets des pages inchangées).
    Coûteux sur une grosse BD : jamais appelée à chaque mesure, seulement
    pour l'info-bulle affichée (plus bas).
  - **Pages en cours de lecture** : un chargement ne remet ses pages au
    document qu'à la fin ; pour que « ce panneau » monte page après page,
    son worker range les pages lues dans son attribut `pages_read` (liste
    remplie par `run`, jamais triée sur place : un tri sur place la fait
    paraître vide à qui la lit), que le chargeur inscrit sur le thread Qt par
    `reading_started(state, worker.pages_read)` au démarrage et retire par
    `reading_finished(state, worker.pages_read)` là où il libère son worker,
    sur tous ses chemins de fin (succès, erreur, annulation, fermeture du
    fichier). Inscriptions dans `state.pages_being_read`
    (`(doc_generation, liste)`), lues par `_pages_being_read` : copie de la
    liste, page qui change pendant sa lecture sautée (`RuntimeError`) ;
    une inscription d'un autre `doc_generation` n'est pas comptée et
    disparaît à l'inscription suivante (filet de sécurité). Pages à la fois
    dans le document et dans la liste à la fin : comptées une fois.
    Les cinq chargements concernés : `archive_loader.LoadWorker`
    (`ArchiveLoader.load` / `_detach_worker` / `_cleanup_worker`),
    `pdf_loading_qt.PdfLoadWorker` (`PdfLoader._start_pdf_loading` /
    `_detach_worker` / `_cleanup_worker`), `PdfMergeWorker`
    (`import_and_merge_pdf`, `_cleanup`), `import_merge_qt.ImportMergeWorker`
    (`import_and_merge_archive`, `_cleanup`), `panel_widget._ImageLoadWorker`
    (`PanelWidget._start_image_load`, `_cleanup`). Un nouveau chargement de
    pages fait de même. Limite : des pages mises en attente par
    `state.run_when_idle` (opération en cours) ne sont pas comptées pendant
    l'attente.
  - `memory_level(share)` : prend le **pourcentage entier affiché**
    (`percent(size, ram)`), jamais la part exacte ; `ORANGE_FROM = 34`,
    `RED_FROM = 67` (vert jusqu'à 33 %, orange de 34 à 66 %, rouge à partir
    de 67 %) : un tiers par couleur, comme les zones verte, jaune et rouge
    du cadran de la jauge, sur lesquelles l'aiguille tombe exactement à ces
    valeurs. Comparé à la part exacte, 33,4 % s'afficherait « 33% » en
    orange avec une aiguille encore dans le vert.
- **Une seule mesure pour toute l'application** : `MainWindow._memory_timer`
  (`MosaicView.py`, **250 ms**) appelle `_sample_memory`, qui mesure et
  alimente le maintien des programmes annexes à chaque tour, mais n'affiche
  qu'une fois par seconde (au moins 0,9 s depuis le dernier affichage,
  `_memory_shown_at` : le timer n'est pas exact). Mesurer plus souvent
  sert à voir les programmes annexes de courte durée : à une mesure par
  seconde, UnRAR pouvait n'être vu qu'au tiers du chargement. Afficher plus
  souvent recalculerait l'info-bulle visible, dont
  `panel_memory_estimate` est coûteux. `_refresh_memory_indicators` mesure
  et affiche tout de suite (démarrage, fin de chargement). Les deux passent
  par `_show_memory`, seul endroit qui range la mesure par
  `status_bar_qt.set_memory_measure` (variable de module `_memory_measure`)
  puis appelle `StatusBar.refresh_memory()` de chaque panneau. Rien tant que
  la fenêtre est réduite. Une barre créée entre deux mesures (ouverture du double
  panneau) affiche la dernière tout de suite, `refresh()` appelant aussi
  `refresh_memory()` (avant le court-circuit `state is None` : l'indicateur
  vaut pour toute l'application, même sans fichier ouvert).
- **Couleurs** : clés `memory_green` / `memory_orange` / `memory_red` de
  `THEMES` (skill `dark-mode`), une teinte par thème.
- **Info-bulle** (`_memory_tooltip_html`) : jauge à gauche, aiguille sur le
  pourcentage de MosaicView (programmes annexes compris), puis « MosaicView : {size} ({percent}%…) » (programmes annexes
  compris), « dont programmes annexes ({names}) : … » seulement quand l'un
  d'eux tourne (`names` : `helper_names` joints par des virgules, noms jamais
  traduits), et « Ce panneau : environ … » (`panel_memory_estimate` du state
  donné par `set_memory_state_getter`, branché sur `lambda: self._state` dans
  `PanelWidget._build_center_panel` — jamais le singleton). Clés
  `tooltip.memory_indicator_app/_helpers/_panel/_unknown`. Texte échappé
  (`html.escape`). Format `<table>` (skill `qt-tooltips`).
  - **Pourcentage arrondi à 0** : `percent()` arrondit à l'entier, et « 0% »
    serait faux pour une taille non nulle (moins de 0,5 % de la mémoire du
    PC). La fonction locale `line(key, size, **params)` de
    `_memory_tooltip_html` prend alors la variante `key + "_below_one"`
    (« moins de 1% », sans paramètre `percent`) : clés
    `tooltip.memory_indicator_app_below_one`, `…_helpers_below_one`,
    `…_panel_below_one`. Un panneau vide (estimation nulle) affiche
    `tooltip.memory_indicator_panel_empty` (« Ce panneau : 0 o »), sans
    « environ » ni pourcentage : rien n'y est approximatif. Une nouvelle
    ligne de l'info-bulle passe par `line()` et déclare sa variante
    `_below_one`.
  - **Calcul seulement quand quelqu'un la voit** : `StatusBar` est installé
    comme filtre d'événements sur `_memory_indicator` **après** `track()`
    (un filtre installé en dernier passe en premier) : sur `QEvent.Enter`,
    `_update_memory_tooltip()` calcule le contenu juste avant que le suivi du
    tooltip ne l'affiche. Ensuite, `refresh_memory()` ne le recalcule (puis
    `force_refresh_visible`) que si `OverlayTooltip.is_visible_for(indicateur)`.
  - **Jauge** : une image par pourcentage entier, cadran et aiguille déjà
    assemblés, `memory_gauge/gauge_000.png` à `gauge_100.png` (48 px) et
    `gauge_NNN@2x.png` (96 px), à la racine — **jamais dans `icons/`**, dont
    le contenu est exporté avec les icônes. Dossier ajouté aux `datas` des
    deux `.spec` PyInstaller. `_gauge_icon_src(share, ratio)` ramène le
    pourcentage entre 0 et 100 (MosaicView peut dépasser la mémoire
    physique : aiguille en butée) et choisit lui-même la version 96 px dès
    que `devicePixelRatioF() > 1` (la recherche automatique des fichiers
    `@2x` de Qt ne fonctionne pas avec une adresse `file:///`), affichée à
    `_GAUGE_TOOLTIP_SIZE` (48) px ; fichier absent → info-bulle sans image.
    Le texte riche de Qt réduit une image sans lissage : d'où des images
    livrées déjà réduites, et pas un assemblage à l'exécution (qui
    demanderait en plus des fichiers temporaires, une balise `<img>` ne
    lisant qu'un fichier).
  - **Générer ou refaire les jauges** : `python tools/make_memory_gauges.py`
    (depuis la racine) relit les deux sources `icons/Memory_Gauge_00.png`
    (cadran sans aiguille, 887 px, transparent) et
    `icons/Memory_Gauge_00_needle.png` (aiguille seule, pointe à gauche),
    pose l'aiguille sur le pivot du cadran, la tourne
    (`needle_angle(percent)`), puis réduit avec lissage aux deux tailles.
    `--apercu DOSSIER` écrit une planche de contrôle (0, 33, 34, 50, 66, 67,
    100 %) au lieu des 202 images. Ses constantes sont les mesures du
    dessin : `DIAL_PIVOT` (centre du trou du cadran), `ANGLE_AT_0` /
    `ANGLE_AT_100` (225° et −45° : arc de 270°, 90° par couleur),
    `NEEDLE_PIVOT` / `NEEDLE_TIP` (rond et pointe de l'aiguille),
    `NEEDLE_LENGTH` (longueur posée). À reprendre si le dessin du cadran
    ou de l'aiguille change de place ; si les zones du cadran changent de
    taille, changer aussi `ORANGE_FROM` / `RED_FROM`.
- **Tests** : `tests/test_memory_usage.py` (seuils, estimation d'un panneau,
  programmes annexes et programme de démarrage, vraie mesure avec un
  programme lancé par le test (taille par programme comprise), maintien des
  programmes annexes (`HelperHold` : gardé pendant le délai, remplacé et
  délai relancé, délai par programme, expiration, mesure manquante, oubli
  avec mesure immédiate en fin de chargement et sans mesure en fin de lot),
  pages en cours de lecture (comptées au fur et à mesure, une fois à la
  remise au document, plus après leur retrait ni pour un document fermé,
  page qui change sautée, inscription et retrait présents dans les cinq
  chargements, process PDF retiré à la fin de l'ajout d'un PDF),
  `refresh_memory` et `_memory_tooltip_html`
  extraites par AST, variantes « moins de 1% » et panneau vide de
  l'info-bulle, aiguille sur le pourcentage de MosaicView, présence des 202
  jauges à leurs deux tailles et pourcentage ramené entre 0 et 100).

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

1. Réapplique `get_current_font(9)` sur le label et les indicateurs texte
   (règle UI n°3 — jamais un `setFont()` figé une seule fois à la création).
2. Réapplique la couleur du thème courant (`get_current_theme()`) sur chaque
   élément (règle UI n°1).
3. Rafraîchit l'indicateur marque-pages (`_refresh_bookmark_indicator()`) et
   l'indicateur de mémoire (`refresh_memory()`), puis traite le cas
   `state is None`.
4. Recalcule et réaffiche le texte de gauche (`labels.status_bar`).
5. Recalcule le texte + tooltip de l'indicateur renumérotation selon
   `state.renumber_mode` (0/1/2 → OFF/Auto/Simple).
6. Recalcule le texte + tooltip + curseur + couleur de l'indicateur ZIP
   (« ZIP: 0 », non compressé, en `memory_green` du thème ; « ZIP: 1 »,
   compressé, en `memory_red` ; « ZIP: -- » dans la couleur du texte ;
   grisé si aucun fichier ouvert) selon `state.zip_compression_state` et le
   réglage par défaut du panneau ; sans fichier ouvert, le tooltip ne parle que
   du clic droit, seul à agir alors (skill `zip-compression`).
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
   `layout.addWidget(indicator, 0)` (stretch 0, il ne doit jamais grandir),
   sans widget séparateur : l'espacement du layout (10 px) suffit et garde
   les écarts égaux. Un indicateur sans texte (simple icône) va plutôt dans
   `icons_layout`, à côté des icônes doublons et marque-pages.
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
