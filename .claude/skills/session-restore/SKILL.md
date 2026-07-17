---
name: session-restore
description: Localiser ou modifier la restauration de session au démarrage de MosaicView (géométrie, thème, sidebar/minimap, reset aux valeurs par défaut). Utiliser dès qu'une tâche touche à session_restore_qt.py, restore_session/save_session, ou au comportement juste après le lancement.
---

# Restauration de session — MosaicView

Restaure l'apparence de la fenêtre telle qu'elle était à la dernière fermeture (géométrie, thème, sidebar, split-view...) et la persiste à la fermeture suivante. **Ne couvre pas tout ce qui est restauré au démarrage** — certains éléments (langue, split-view, plein écran) sont restaurés ailleurs, par du code adjacent mais pas dans ce fichier ; voir la section "Ce qui n'est PAS dans `session_restore_qt.py`" plus bas, à lire avant de supposer qu'un comportement de démarrage vit ici.

## Fichier central — `modules/qt/session_restore_qt.py`

Quatre fonctions, aucune classe, appelées depuis `MosaicView.py` (`MainWindow`) :

| Fonction | Appelée depuis | Quand |
|---|---|---|
| `restore_session(win)` | `MainWindow.__init__` (`MosaicView.py:241`) | à la construction de la fenêtre, avant `show()` |
| `save_session(win)` | `MainWindow.closeEvent` (`MosaicView.py:835`, via `save_session_cb`) | à la fermeture réelle de l'application |
| `reset_to_defaults(win)` | `MainWindow._reset_to_defaults` (`MosaicView.py:404`) | menu "Réinitialiser aux valeurs par défaut" |
| `save_sidebar_state(collapsed)` | `PanelWidget` au repli/dépli de la sidebar (`panel_widget.py:2264`) | à chaque bascule manuelle de la colonne d'icônes |

## Emplacement de sauvegarde — un seul fichier JSON dans %APPDATA%

`ConfigManager` (`modules/qt/config_manager.py:65-83`) — **pas spécifique à la session**, c'est la config applicative entière (thème, langue, tous les réglages par panneau, etc.). Voir skill `config-storage` pour le détail complet (emplacement `%APPDATA%\MosaicView\.mosaicview_config.json` depuis la v1.6.2, migration automatique depuis l'ancien emplacement `%TEMP%`, chiffrement DPAPI de la clé API...) — résumé pertinent pour ce skill :

- Format JSON simple, un dict à plat fusionné avec `DEFAULT_CONFIG` au chargement (une clé absente du fichier existant retombe sur sa valeur par défaut, pas d'erreur).
- `session_restore_qt.py` ne lit/écrit jamais ce fichier directement — toujours via les getters/setters typés de `ConfigManager` (`get_maximized()`, `set_window_size()`, etc.), jamais `cfg.config['xxx']` en dur.
- Le menu "Effacer le fichier de configuration" (`PanelWidget._clear_config_file`, voir skill `config-storage`) supprime ce fichier entier — au prochain démarrage, tout repart de `DEFAULT_CONFIG` (fenêtre centrée par défaut, thème clair, langue système détectée).

## `restore_session(win)` — ce qui est restauré, dans l'ordre

Tout le travail est différé dans un `QTimer.singleShot(50, _restore)` — **pas exécuté immédiatement** à l'appel de la fonction, pour laisser la construction Qt de la fenêtre (widgets, layouts) se stabiliser avant de toucher à sa géométrie/son thème. `win` est toujours `MainWindow`.

1. **Thème sombre** (`cfg.get_dark_mode()`) : appliqué **avant** `show()` pour éviter tout flash visuel clair→sombre à l'écran. Positionne `win._state.dark_mode`, appelle `apply_app_theme(app)` + `apply_theme(app, canvas, left_panel, tab_bar, render=False)` (voir skill `dark-mode` pour le mécanisme complet). `render=False` : pas de `render_mosaic()` ici, la mosaïque est encore vide à ce stade.
2. **Sidebar repliée** (`cfg.get_sidebar_collapsed()`) : appelle `win._toggle_sidebar()` si vrai. **Doit être fait avant `show()`** — sinon le premier calcul de layout Qt se base sur la colonne encore dépliée (taille par défaut), impose un `minimumSize` plus large que la géométrie sauvegardée, et force la fenêtre à s'agrandir au-delà de la taille demandée juste après. Piège déjà rencontré et documenté en commentaire dans le code.
3. **Minimap visible** (`cfg.get_minimap_visible()`) : `win._panel._toggle_minimap()` si vrai — voir skill `minimap`. Uniquement pour `win._panel` (panel1) ici ; panel2 n'existe pas encore à ce stade (créé plus tard si le split était actif, voir section split plus bas) donc sa propre minimap suit son propre cycle de restauration.
4. **Affichage de la fenêtre** : `showMaximized()` si `cfg.get_maximized()` (et pas déjà plein écran), sinon `show()` normal.
5. **Barre de titre Windows sombre** (DWM) : appliquée **après** `show()` via un `QTimer.singleShot(200, ...)` supplémentaire — `WM_NCACTIVATE` force un repaint de la barre de titre sans vider tout l'écran, contrairement à un changement avant l'affichage réel. Voir skill `dark-mode`, section barre de titre Windows.
6. **Largeur de la colonne d'icônes** (`cfg.get_buttons_column_width()`) : appliquée seulement sur `win._panel` (panel1), via `panel._splitter.setSizes([saved_w, total - saved_w])` si la sidebar est visible. Si la colonne est repliée au démarrage, la largeur sauvegardée est juste **mémorisée** (`panel._saved_sidebar_width`) sans toucher au splitter caché, pour être réappliquée à la prochaine ouverture manuelle de la colonne. `adapt_cols_to_width()` (recalcul du nombre de colonnes de la grille d'icônes, voir skill `icon-toolbar`) n'est appelé que si la sidebar est visible — sinon la largeur lue serait périmée et fausserait le calcul.

**Panel2 (split-view) n'est PAS restauré par cette fonction** — voir section suivante.

## Ce qui n'est PAS dans `session_restore_qt.py`

Trois éléments de démarrage, souvent confondus avec la "restauration de session", vivent ailleurs :

- **Split-view (panel2)** — `MosaicView.py:244-245` : `if get_config_manager().get_split_active(): QTimer.singleShot(0, self._open_split)`, juste après l'appel à `restore_session(self)` dans `MainWindow.__init__`, mais **en dehors** de `session_restore_qt.py`. Voir skill `panels` pour le cycle de vie de `_open_split()`. Panel2, une fois recréé, restaure ses propres réglages indépendants (`Panel2Config`, largeur de colonne `_panel2`, minimap `_panel2`...) au moment de sa construction — pas via une deuxième invocation de `restore_session`.
- **Plein écran** — `MosaicView.py:224-225` : `if get_config_manager().get_fullscreen(): QTimer.singleShot(0, self._toggle_fullscreen)`, câblé dans `MainWindow.__init__` **avant** l'appel à `restore_session`, indépendamment de celle-ci.
- **Langue** — restaurée par `LocalizationManager.__init__` (`modules/qt/localization.py:71-100`, instancié très tôt via `init_localization()` dans `MosaicView.py:121`, avant même la construction de `MainWindow`) : lit `cfg.get_language()`, retombe sur `detect_system_language()` si `None` (aucune langue explicitement choisie). Ne fait donc **aucun aller-retour** avec `session_restore_qt.py`. Voir skill `add-translation` pour la mécanique de traduction elle-même.
- **Taille de police** (`cfg.get_font_size_offset()`) : appliquée au fil de la construction normale de l'UI (chaque widget appelle `get_current_font()`), pas un pas explicite de `restore_session` — voir skill `dark-mode`/règle UI CLAUDE.md n°3.

**Piège pour toute nouvelle tâche "ajouter X à la restauration de session"** : vérifier d'abord si l'élément concerné a déjà son propre mécanisme de restauration indépendant (comme les trois ci-dessus) avant de l'ajouter dans `restore_session()` — dupliquer la logique dans les deux endroits créerait une désynchronisation.

## `save_session(win)` — ce qui est sauvegardé à la fermeture

Appelée depuis `on_window_close` (`file_close_qt.py`, voir skill `file-close`) via le callback `save_session_cb`, **seulement pour panel1** (`win._panel`) — voir la section suivante pour la raison structurelle de cette asymétrie avec panel2.

- **Géométrie** : si plein écran → sauvegarde uniquement le flag (`cfg.set_fullscreen(True)`), pas la géométrie (qui n'a pas de sens en plein écran). Sinon, si maximisé → sauvegarde `normalGeometry()` (la taille qu'aurait la fenêtre si elle n'était pas maximisée, pas la taille maximisée elle-même) + le flag maximisé. Sinon → `geometry()` normale.
- **Largeur de colonne d'icônes** : **pour panel1 ET panel2** (si `panel2` existe), contrairement à la restauration qui ne concerne que panel1 au moment de `restore_session` — asymétrie voulue, panel2 n'existe pas encore quand `restore_session` tourne mais existe déjà (ou a existé dans la session) au moment de `save_session`. Même logique de secours via `_saved_sidebar_width` si la colonne est repliée au moment de fermer.

## Pourquoi `save_session` ne s'exécute que sur le chemin normal de fermeture

`MainWindow.closeEvent` (`MosaicView.py:780-839`) gère la fermeture de fichier de **chaque** panneau avant d'appeler `save_session` :
- Si panel2 est ouvert avec un fichier modifié, il est fermé **en premier** (potentiellement via un dialogue de confirmation non-modal — voir skill `panels`, règle CLAUDE.md sur les dialogues de fermeture) ; si l'utilisateur annule, `event.ignore()` et **`save_session` n'est jamais atteint** pour ce cycle — le prochain clic sur fermer retraite depuis le début (`self._close_event_handled` remis à `False`).
- `save_session` n'est appelé qu'une fois **tous** les panneaux confirmés fermés (`self._close_event_handled = True`, `on_window_close` réussi pour panel1 aussi).
- **Piège pour toute modification de ce flux** : ne jamais appeler `save_session` avant d'être certain que la fermeture va réellement aboutir — un appel prématuré sauvegarderait un état sur le point d'être annulé par l'utilisateur (ex. clic sur Annuler dans une boîte de confirmation), désynchronisant la session sauvegardée de la session réellement vécue.

## `reset_to_defaults(win)` — remise à zéro complète

**Ancien doublon de nom, supprimé** : `ConfigManager` avait aussi une méthode `reset_to_defaults()` du même nom, mais elle n'était appelée nulle part dans le code (vérifié exhaustivement le 2026-07-17) — code mort, retiré de `config_manager.py`. La seule fonction de reset qui existe est celle documentée ci-dessous. Les occurrences de la chaîne `"reset_to_defaults"` restant dans le code (`icon_toolbar_qt.py`, `menubar_callbacks_qt.py`) sont soit un ID de bouton, soit une clé de dict pointant vers `mw._reset_to_defaults` (`MainWindow`) — jamais vers `ConfigManager`.

Trois points d'entrée UI, tous vers le même code : icône dédiée de la colonne d'icônes (`icon_toolbar_qt.py`), menu contextuel canvas (clic droit → "Réinitialiser"), barre de menus. Tous câblés vers `MainWindow._reset_to_defaults()` (`MosaicView.py:402`), qui importe et appelle `session_restore_qt.reset_to_defaults(self)` — `PanelWidget._reset_to_defaults()` (`panel_widget.py:883`) ne fait que relayer vers `self._main_window._reset_to_defaults()`, le reset est toujours géré au niveau de la fenêtre principale, jamais par panneau isolé.

Contrairement à `restore_session`/`save_session` (session persistée), cette fonction **applique** immédiatement des valeurs figées et les persiste, sur **tous les panneaux** (`win._all_panels()`, voir skill `panels`) :

1. Quitte le plein écran si actif.
2. Replie la sidebar et cache la minimap sur tous les panneaux — **avant** le resize de la fenêtre (ligne ~134-143) : ces deux éléments imposent une largeur minimale qui, si encore visible, bride silencieusement `resize()` plus bas au lieu d'appliquer `default_width` (piège documenté en commentaire).
3. Taille/position de fenêtre par défaut (`1240×830`, centrée sur l'écran courant avec un léger décalage vertical `-40`) — `win.setMinimumSize(0, 0)` explicite juste avant, pour libérer une contrainte minimale mise en cache par Qt malgré le masquage des éléments au point 2.
4. Repasse en thème clair si sombre (`win._toggle_theme()` — voir skill `dark-mode`).
5. Taille d'icônes et de vignettes remises à l'index par défaut sur tous les panneaux (voir skills `icon-toolbar`/`mosaic-thumbnails`).
6. Décalage de taille de police remis à 0, rechargement des polices UI sur tous les panneaux (`_reload_ui_fonts`).
7. **Langue** : redétecte la langue système (`win._loc.detect_system_language()`) et l'applique — ce point-ci **est bien** dans `session_restore_qt.py`, contrairement à la restauration normale au démarrage (voir section précédente) : le reset explicite un choix "retour à la langue système", différent de "ne rien avoir sauvegardé".
8. Largeur de colonne d'icônes par défaut recalculée depuis `ICON_SIZE_LEVELS[0]` (taille max d'icône) et `ICON_PAD`, appliquée sur tous les panneaux, mais la colonne **reste repliée** (repliée au point 2) — la largeur par défaut ne sera visible qu'à la prochaine ouverture manuelle.
9. Mode de renumérotation (`renumber_mode = 1`, auto) et compression ZIP (niveau 0, stocké) remis par défaut sur tous les panneaux — voir skills `renumbering`/`zip-compression`.
10. **Ratio du split inter-panneaux remis à 50/50** par un mécanisme indirect : `QSplitter.setSizes()` seul s'est révélé ignoré à ce stade (le splitter garde ses proportions précédentes) — la fonction **simule un vrai double-clic souris** sur le handle du splitter (`QMouseEvent` synthétique envoyé via `QApplication.sendEvent`), qui est le seul geste qui recentre fiablement un `QSplitter` à 50/50 dans ce contexte. `cfg.set_split_ratio(0.5)` est écrit **après** ce double-clic simulé (différé de 50ms), pas avant, car le double-clic déclenche lui-même `splitterMoved` → sauvegarde du ratio recalculé — écrire avant serait écrasé par cette sauvegarde automatique potentiellement imprécise (arrondi entier des tailles réelles).
11. Sauvegarde finale groupée (`cfg.save_config()`), après plusieurs `set_xxx(..., save=False)` intermédiaires pour éviter d'écrire le fichier JSON à chaque étape individuelle.

### Reset vs suppression du fichier de configuration — ne pas confondre

Deux mécanismes différents malgré une intention superficiellement proche ("remettre à zéro") :

| | **Reset** (`reset_to_defaults`) | **Suppression** (voir skill `config-storage`) |
|---|---|---|
| Fonction | `session_restore_qt.reset_to_defaults(win)` | `PanelWidget._clear_config_file()` |
| Fichier `.mosaicview_config.json` | Conservé, réécrit avec de nouvelles valeurs | Supprimé du disque |
| Effet visuel | **Immédiat** — fenêtre, thème, icônes changent sous vos yeux | **Aucun effet immédiat** — la fenêtre actuelle ne change pas |
| Marque-pages / fichiers récents / clé API | **Conservés** | **Perdus** (tout le fichier disparaît) |
| Quand le changement prend effet | Tout de suite | Au **prochain démarrage** (`ConfigManager` recrée le fichier depuis `DEFAULT_CONFIG`) |
| Portée | Réglages d'apparence/comportement uniquement | Tout le contenu du fichier, sans distinction |

En résumé : le reset est doux et sélectif (apparence à zéro, données gardées) ; la suppression est radicale et totale (tout perdu, y compris les données). Si une tâche future demande d'étendre l'un des deux, vérifier d'abord lequel des deux comportements est réellement voulu — les confondre est le piège le plus probable sur ce sujet.

## Comment étendre

- **Ajouter un nouvel élément à restaurer au démarrage** : ajouter le getter correspondant dans `ConfigManager` (avec entrée dans `DEFAULT_CONFIG`), puis l'appliquer dans `restore_session()._restore()` — **avant `show()`** si l'élément affecte le layout/la géométrie initiale (comme sidebar/minimap), après si c'est un ajustement cosmétique sans impact sur le calcul de taille (comme la barre de titre sombre). Vérifier d'abord que l'élément n'a pas déjà son propre mécanisme de restauration indépendant (voir section dédiée plus haut).
- **Ajouter le même élément à `reset_to_defaults`** : uniquement sur demande explicite — un ajout à `restore_session` ne doit pas systématiquement impliquer un ajout à `reset_to_defaults`, ce sont deux besoins différents (persister l'état réel vs revenir à des valeurs figées).
- **Ajouter un réglage par panneau à sauvegarder** : suivre le pattern largeur de colonne d'icônes dans `save_session` — vérifier tous les panneaux existants (`win._panel` et `win._panel2` si non `None`), pas seulement le panneau actif.

## Pièges connus

- **Ordre `avant`/`après` `show()` critique** : sidebar/minimap doivent être appliqués avant `show()` (impact sur le calcul de layout/minimumSize), la barre de titre sombre doit l'être après (repaint DWM). Ne pas réordonner sans comprendre pourquoi chaque étape est où elle est — les deux pièges sont documentés en commentaire dans le code source lui-même.
- **`restore_session` ne restaure que panel1** — ne pas supposer que panel2 est concerné ; son état est restauré séparément au moment de sa (re)création par `_open_split()` (voir skill `panels`).
- **Ne jamais appeler `save_session` avant confirmation réelle de fermeture** — voir section dédiée ci-dessus, un appel prématuré désynchronise la session sauvegardée si l'utilisateur annule ensuite.
- **Le double-clic simulé dans `reset_to_defaults`** (recentrage du split à 50/50) est un contournement d'un comportement Qt, pas un choix de conception arbitraire — ne pas le remplacer par un simple `setSizes()` sans revalider que Qt applique bien le nouveau ratio dans ce contexte précis (post-resize/post-repli de sidebar).
- **Le fichier de config est dans `%APPDATA%\MosaicView\`** depuis la v1.6.2 (voir skill `config-storage`) — avant cette version il vivait dans `%TEMP%/MosaicViewTemp/`, ce qui exposait la session sauvegardée (et les marque-pages) à une purge système du dossier temporaire. Ce n'est plus le cas : seule l'action "Effacer le fichier de configuration" (explicite, voulue par l'utilisateur) peut désormais faire disparaître la session sauvegardée entre deux lancements ; "Effacer les fichiers temporaires" n'a plus aucun effet dessus.
