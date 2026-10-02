---
name: panels
description: Localiser ou modifier la gestion des panneaux de MosaicView (panel1/panel2 en split-view, panneau virtuel de la bibliothèque). Utiliser dès qu'une tâche touche à PanelWidget, _all_panels, _active_panel, Panel2Config, VirtualLibraryPanel, ou doit se comporter selon le panneau.
---

# Panneaux — MosaicView

MosaicView a une fonction de double interface : deux panneaux côte à côte (split-view), chacun une instance complète et indépendante de l'application (sa propre mosaïque, ses propres onglets, sa propre barre de statut). C'est la raison architecturale de la règle CLAUDE.md n°4 (jamais de fenêtre modale) — une modale gèlerait aussi l'autre panneau.

## Fichiers clés

- **`modules/qt/panel_widget.py`** — classe `PanelWidget(QWidget)` : l'implémentation complète d'un panneau (colonne d'icônes + menubar + onglets + canvas mosaïque + statusbar). Panel1 et panel2 sont chacun **une instance** de cette même classe.
- **`MosaicView.py`** — classe `MainWindow` : orchestre les deux instances (`self._panel`, `self._panel2`), le splitter qui les sépare, et le panneau actif.
- **`modules/qt/virtual_library_panel.py`** — `VirtualLibraryPanel`, un panneau *sans UI* utilisé uniquement par la fenêtre Bibliothèque (voir plus bas).
- **`modules/qt/config_manager.py`** — clés de config dédoublées `*_panel2` + classe adaptateur `Panel2Config`.
- **`modules/qt/state.py`** — `AppState`, la classe d'état ; `state` = singleton module-level pointant vers l'`AppState` du panneau actif.

## Ce que contient un `PanelWidget`

Chaque `PanelWidget` possède, en propre, sans rien partager avec l'autre panneau :
- **`self._state`** — une instance `AppState` (`modules/qt/state.py`) : `images_data`, `current_file`, `selected_indices`, `history`/`history_index` (undo/redo, voir skill `undo-redo` — un historique indépendant par panneau), `renumber_mode`, `zip_compression_state`, `dark_mode`, etc. C'est **tout l'état métier** du panneau.
- **`self._canvas`** — `MosaicCanvas` (la grille de vignettes, voir skill `mosaic-thumbnails`).
- **`self._icon_toolbar`** — `IconToolbarQt` (colonne d'icônes, voir skill `icon-toolbar`).
- **`self._tab_bar`** / **`self._metadata_tab`** — onglets mosaïque/infos (voir skill `tabs`), chacun lié explicitement au `state` de son panneau, jamais au singleton global.
- **`self._status_bar`** — `StatusBar`.
- **`self._minimap_panel`** — minimap (voir skill `minimap`).
- **`self._loader`** / **`self._pdf_loader`** — chargeurs d'archive/PDF, construits avec `self._canvas` et `self._state` (voir skill `archive-image-loading` pour les archives classiques, skill `pdf-loading` pour le PDF — chaque panneau a sa propre instance et son propre process PDF dédié, aucun chargement n'est partagé entre panel1/panel2).
- **`self._menubar`** — sa propre `QMenuBar`, construite via `build_menubar(self, self._build_menubar_callbacks(), self._menubar)`.
- **`self._is_primary`** (bool) — `True` pour panel1, `False` pour panel2. Utilisé pour choisir entre config directe (panel1) et `Panel2Config` (panel2), et pour sauter certaines inits ponctuelles (ex. préchauffage bibliothèque, `QTimer.singleShot(2000, self._prewarm_library)` seulement si primary).

`PanelWidget` expose volontairement les **mêmes attributs publics que `MainWindow` exposait avant l'introduction du split** (commentaire en tête de fichier), pour que tout module qui reçoit `mw`/`self` en paramètre continue de fonctionner sans changement, qu'il s'agisse de panel1 ou panel2.

## Le singleton `modules.qt.state.state`

Beaucoup de modules (undo/redo, fichiers récents, `get_current_theme()`...) lisent l'état applicatif via un singleton global `modules.qt.state.state`, pas via un paramètre explicite. Avec deux panneaux, ce singleton doit **toujours pointer vers l'`AppState` du panneau concerné par l'opération en cours** :

- Assigné à la création de chaque `PanelWidget` (`PanelWidget.__init__`, bloc "État applicatif propre à ce panneau") : `_state_module.state = self._state`.
- Redirigé par **`MainWindow.eventFilter`** (`MosaicView.py`, filtre installé sur toute la `QApplication`) : **uniquement en split-view, et uniquement quand le panneau cliqué n'est pas déjà `_active_panel`**. Sur `MouseButtonPress`, si le widget cliqué descend de `_frame1`/`_frame2` d'un panneau non actif, `_active_panel` et le singleton basculent ensemble immédiatement vers ce panneau (avant le `mouseReleaseEvent` qui déclenche l'action), la mise à jour visuelle des bordures étant différée via `QTimer.singleShot(0, ...)`. Un clic dans un `QMenu` popup ou une fenêtre séparée (hors des deux frames) ne change rien.
- Redirigé temporairement par **`PanelWidget._build_menubar_callbacks()`** (`panel_widget.py`) : chaque callback de menu/callback métier est enveloppé pour basculer `state` vers `panel_state` avant exécution. Après l'appel, la valeur à remettre est décidée par la fonction pure **`state.py::resolve_state_after_wrapped_call`** : si le callback a changé `_active_panel` (ex. fermeture du split depuis le menu de panel2, qui rend panel1 actif), le singleton suit le nouveau panneau actif ; sinon l'état précédent est restauré s'il appartient encore à un panneau de `mw._all_panels()`, à défaut `panel_state`.
- Redirigé explicitement pendant `_open_split()` (le temps d'appliquer le thème à panel2 fraîchement créé) et par `MainWindow._set_active_panel()` (navigation clavier TAB vers l'autre panneau, minimap, clic sur le cadre d'un panneau). Remis sur le panneau actif en fin de `_on_language_change`/`_toggle_theme`, qui le basculent panneau par panneau, et dans le `finally` de `MainWindow.closeEvent` après `on_window_close` (fermeture éventuellement annulée).
- Redirigé temporairement par `VirtualLibraryPanel.activate()` (context manager) — voir plus bas.

**Invariant à préserver : singleton == `_active_panel._state`** hors de ces redirections temporaires. `eventFilter` ne recale le singleton que lorsque le panneau actif **change** (et jamais hors split) : si le singleton est laissé sur un autre panneau alors que `_active_panel` ne bouge pas, rien ne le corrige — cliquer dans le panneau actif ne suffit pas. Toute redirection temporaire doit donc se terminer par un `finally` qui le remet sur le panneau actif (ou sur l'état précédent si le panneau actif n'a pas changé). Consommateurs qui en dépendent sans recevoir de state : les raccourcis globaux Ctrl+C/X/V (`clipboard_qt.py` lit le singleton), les callbacks **non enveloppés** de la colonne d'icônes de panel1 (`icon_toolbar_qt.py::build_icon_toolbar`, `build_menubar_callbacks(mw)` brut quand `is_primary`, contrairement à panel2 qui reçoit la version enveloppée), `toggle_theme`.

**Piège — lecture différée du singleton** : un callback exécuté **après** un dialogue non modal (réponse Oui/Non, chaîne de validation, fin de worker) ne doit jamais lire `modules.qt.state.state` : entre-temps l'utilisateur a pu cliquer dans l'autre panneau, et le singleton pointe sur lui (un clic dans le dialogue lui-même ne change rien). Capturer le state au lancement et le transmettre explicitement. C'est pourquoi les fonctions de `file_operations_qt.py` lisent `callbacks["state"]` (fourni par `PanelWidget._file_op_callbacks()`) et ne retombent sur le singleton qu'en repli — elles sont appelées en différé par le dialogue de fermeture (`create_cbz_cb`/`apply_new_names_cb`, voir skill `file-close`). Garde-fou : `tests/test_check_no_deferred_singleton.py`, qui scanne les fichiers listés dans son `SCANNED_FILES` (`file_operations_qt.py`, `web_import_qt.py`) — y ajouter tout nouveau module dont une opération différée ou un thread lit l'état du panneau. Même règle pour une fenêtre non modale qui mémorise l'état à sa construction (ex. `AnimatedGifDialog`, qui reçoit `callbacks["state"]`) : state du panneau via ses callbacks, singleton seulement en repli. Pour tout nouveau code, préférer passer `self._state` explicitement plutôt que de compter sur le singleton.

## `MainWindow` : orchestration des deux panneaux

Dans `MosaicView.py` :

- **`self._panel`** — panel1, créé dans `MainWindow.__init__` (toujours présent, jamais détruit).
- **`self._panel2`** — panel2, `None` tant que le split n'a jamais été ouvert.
- **`self._split_active`** (bool) — split visible ou non.
- **`self._active_panel`** — le panneau qui a le focus logique (raccourcis clavier globaux Ctrl+O/Z/Y/C/X/V, Escape... routés vers `self._active_panel`).
- **`self._all_panels()`** (définie deux fois à l'identique dans `MainWindow`, la seconde définition prévaut) — retourne `[self._panel]` tant que panel2 n'a jamais été créé, puis `[self._panel, self._panel2]` **même split fermé** (panel2 est alors masqué, pas détruit — voir plus bas). **Point d'entrée systématique pour toute action qui doit s'appliquer aux deux panneaux** (changement de langue, de thème, de police, reset aux valeurs par défaut, sync des menus récents...). Chercher les boucles `for p in self._all_panels():` comme modèle avant d'ajouter un nouveau traitement global. À part : le titre de fenêtre (`update_window_title`, voir skill `window-title`) lit `window._panel`/`window._panel2` directement plutôt que d'itérer sur `_all_panels()`, puisqu'il doit distinguer explicitement les deux côtés pour construire le titre combiné `file1 ||| file2`.
- **`MainWindow` expose des raccourcis en `@property`** (`_state`, `_canvas`, `_left_panel`, `_tab_bar`, `_icon_toolbar`, `_sidebar_visible`, `_splitter`) qui délèguent tous à `self._active_panel`. Du code legacy qui accède à `mw._canvas` etc. sans savoir qu'il y a deux panneaux continue donc de fonctionner, sur le panneau actif.

### Cycle de vie du split (`_open_split` / `_close_split`)

- **Restauration au démarrage** : `MainWindow.__init__` (`MosaicView.py`), `if get_config_manager().get_split_active(): QTimer.singleShot(0, self._open_split)` — appelé juste après `restore_session(self)` mais **en dehors** de `session_restore_qt.py` (voir skill `session-restore` pour le reste de la restauration de session, qui ne touche jamais à panel2).
- **Panel2 est créé une seule fois** (`first_open = self._panel2 is None`), puis **jamais détruit** — seulement masqué (`self._frame2.hide()`) à la fermeture du split et réaffiché (`self._frame2.show()`) à la réouverture.
  - **Why** : détruire l'arbre Qt de panel2 (`setParent(None)`/`deleteLater()`) déclenche une invalidation shiboken en chaîne qui corrompt des wrappers Python de panel1 (items du canvas, menus) alors que leurs objets C++ sont encore vivants, et laisse le `QSplitter` dans un état qui finit en access violation à la ré-insertion. Ce point est documenté en commentaire dans `_close_split` — ne pas "nettoyer" ce pattern en le remplaçant par une vraie destruction sans revalider soigneusement.
  - Conséquence pratique : le hook molette et les mises à jour langue/thème doivent tolérer un panel2 caché (`isVisible() == False`) et continuer de s'appliquer dessus via `_all_panels()`, pour qu'il soit à jour au moment où il redevient visible.
- **Première ouverture** (`first_open`) : panel2 **hérite** de panel1 pour la disposition de la colonne d'icônes et sa taille, *seulement* si panel2 n'a pas déjà sa propre config sauvegardée (`cfg.get_icon_toolbar_layout_panel2() is None`, etc.) — donc l'héritage ne se produit qu'une fois, jamais de resynchronisation automatique ensuite.
- **Fermeture** (`_close_split` → `_finish_close_split`) : si panel2 a un fichier ouvert/modifié, passe par `close_file(..., on_closed=self._finish_close_split)` (potentiellement un dialogue non-modal de confirmation) avant de réellement masquer le panneau — ne jamais court-circuiter cette confirmation en fermant directement.
- **`_set_active_panel(panel)`** — change `self._active_panel`, redirige le singleton `state`, et met à jour la bordure colorée active/inactive des deux frames (`_set_frame_active`).

## Config dédoublée par panneau (`Panel2Config`)

Chaque réglage **persisté par panneau** (pas juste par session) a deux clés dans `config_manager.py` :
- Panel1 : clé directe (ex. `icon_toolbar_layout`, `renumber_mode`, `zip_compression_level`).
- Panel2 : même concept sous suffixe `_panel2` (ex. `icon_toolbar_layout_panel2`), avec ses propres méthodes `get_/set_..._panel2()`.

**`Panel2Config`** (classe adaptateur, `config_manager.py`) redirige les **mêmes noms de méthode** (`get_icon_toolbar_layout()`, `set_renumber_mode()`, etc.) vers les clés `_panel2` — ainsi le code consommateur (ex. `IconToolbarQt`, voir skill `icon-toolbar`) ne sait jamais s'il parle à panel1 ou panel2, il appelle toujours la même API sur l'objet `config` qu'on lui a injecté.

Pattern à réutiliser dans `PanelWidget` pour tout nouveau réglage à dédoubler :
```python
def _ma_nouvelle_config(self):
    if self._is_primary:
        return get_config_manager()
    from modules.qt.config_manager import Panel2Config
    return Panel2Config(get_config_manager())
```
(voir `_renumber_config()` et `_zip_compression_config()` dans `panel_widget.py` comme modèles existants).

**Il n'y a aucune synchronisation automatique entre panel1 et panel2** pour ces réglages : personnaliser le panneau 1 ne change rien au panneau 2, et inversement (sauf l'héritage ponctuel à la toute première ouverture du split, voir plus haut). Une synchro cross-panneaux est un comportement à construire, pas un bug existant.

## La bibliothèque comme « panneau virtuel »

**`VirtualLibraryPanel`** (`modules/qt/virtual_library_panel.py`) n'est **pas un `QWidget`** — c'est un panneau purement logique : juste un `AppState` interne, jamais affiché, jamais ajouté à aucun splitter, jamais compté dans `_all_panels()`.

- **Pourquoi il existe** : `library_window.py` a besoin de réutiliser un vrai `AppState` (pour `images_data`, `comic_metadata`, etc., et pour bénéficier du même code qui lit `modules.qt.state.state`) sans construire tout l'attirail d'un panneau réel (canvas, menubar, toolbar) et sans jamais interférer avec panel1/panel2.
- **`open_from_file(abs_path)`** — peuple `self._state` en lisant un `.cbz` sur disque (liste des entrées + `ComicInfo.xml`), sans passer par `ArchiveLoader`. Retourne `False` si le fichier n'est pas un `.cbz` lisible.
- **`activate()`** — context manager : redirige le singleton `modules.qt.state.state` vers ce panneau virtuel le temps du bloc, puis restaure l'état précédent — même pattern que le wrapping fait par `PanelWidget._build_menubar_callbacks()`.
  ```python
  panel = VirtualLibraryPanel()
  if panel.open_from_file(abs_path):
      with panel.activate():
          ...  # code qui lit/écrit modules.qt.state.state
  ```

**Piège** : ne jamais confondre ce panneau virtuel avec panel1/panel2. Il ne doit jamais apparaître dans une boucle `_all_panels()`, ne déclenche aucune UI, et son cycle de vie est local à l'opération qui l'a créé (pas de réutilisation entre deux appels comme pour panel2).

Pour tout ce qui concerne le moteur SQLite, la fenêtre `LibraryWindow`, le scan et la recherche par critères, voir skill `library` — cette section-ci ne couvre que le rôle de panneau logique de `VirtualLibraryPanel`.

## Ajouter une fonctionnalité qui doit se comporter différemment selon le panneau

1. Le state métier va dans `AppState` (`self._state` du panneau) — jamais dans un attribut partagé.
2. Si la fonctionnalité doit persister par panneau (survit à la fermeture/réouverture de l'appli) : dédoubler la clé de config comme décrit ci-dessus (`*_panel2` + méthode adaptateur dans `Panel2Config`).
3. Si la fonctionnalité doit s'appliquer aux deux panneaux à la fois (thème, langue, police, reset) : itérer sur `mw._all_panels()`, jamais sur `self._panel`/`self._panel2` en dur (panel2 peut être `None` ou caché).
4. Ne jamais supposer qu'un seul panneau existe : toute nouvelle icône, callback, ou état doit fonctionner identiquement si instancié deux fois côte à côte (voir aussi le piège correspondant dans la skill `icon-toolbar`).
5. Si le code doit lire l'état "courant" sans qu'un panneau lui soit explicitement passé, vérifier que le singleton `modules.qt.state.state` est garanti à jour à cet endroit (voir section singleton ci-dessus) — sinon, préférer un paramètre explicite.
