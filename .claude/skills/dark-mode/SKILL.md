---
name: dark-mode
description: Localiser ou modifier le mode sombre / thème clair-sombre de MosaicView (THEMES dans state.py, toggle_theme_qt.py, pattern _apply_theme() par fenêtre). Utiliser dès qu'une tâche touche à dark_mode, get_current_theme, ou à une couleur qui ne suit pas le thème.
---

# Mode sombre / thème — MosaicView

Le thème clair/sombre n'est pas un mécanisme Qt natif (pas de `QPalette` seule) : c'est un dict de couleurs choisi par un booléen d'état, appliqué à la main via stylesheet à **chaque** fenêtre/widget qui affiche une couleur. Il n'y a pas de mise à jour automatique — tout widget qui ne rejoue pas son application de thème au bon moment reste figé dans l'ancien thème.

## 1. Où sont définies les couleurs

Tout est dans `modules/qt/state.py`, dict `THEMES` :

```python
THEMES = {
    "light": {
        "bg": "#f5f5f5",          # Fond clair pour canvas et main_frame
        "canvas_bg": "#f5f5f5",   # Fond du canvas/onglets
        "toolbar_bg": "#e0e0e0",  # Fond du bandeau de boutons
        "separator": "#808080",   # Séparateur
        "text": "#000000",        # Texte
        "disabled": "#999999",    # Texte désactivé
        "entry_bg": "#ffffff",    # Fond des champs de saisie
        "link": "#0066cc",        # Couleur des liens hypertextes
        "tooltip_bg": "#ffffe0",  # Fond des info-bulles
        "tooltip_fg": "#000000",  # Texte des info-bulles
        "icon_hover": "#cccccc",  # Fond survol icônes toolbar
        # Indicateur de mémoire de la barre de statut (memory_usage.memory_level)
        "memory_green": "#1e7d32",
        "memory_orange": "#c25e00",
        "memory_red": "#c62828",
    },
    "dark": {
        "bg": "#2b2b2b",
        "canvas_bg": "#2b2b2b",
        "toolbar_bg": "#1e1e1e",
        "separator": "#555555",
        "text": "#ffffff",
        "disabled": "#aaaaaa",
        "entry_bg": "#3c3c3c",
        "link": "#66b3ff",
        "tooltip_bg": "#3c3c3c",
        "tooltip_fg": "#ffffff",
        "icon_hover": "#4a4a4a",
        "memory_green": "#66bb6a",
        "memory_orange": "#ffa726",
        "memory_red": "#ef5350",
    }
}

def get_current_theme():
    """Retourne le thème actuel (clair ou sombre) selon state.dark_mode"""
    return THEMES["dark"] if state.dark_mode else THEMES["light"]
```

Les trois couleurs `memory_*` sont celles du pourcentage de l'indicateur de mémoire (skill `status-bar`) : plus sombres en thème clair, plus claires en thème sombre, pour rester lisibles sur le fond de chaque thème. L'orange et le rouge y sont voulus (orange à partir de 34 %, rouge à partir de 67 % de la mémoire du PC : un tiers par couleur, comme le cadran de la jauge), par exception à la règle « pas de couleur vive pour un avertissement non bloquant » de `CLAUDE.md`.

**Pour ajouter une nouvelle couleur thémée** : ajouter la clé dans les DEUX sous-dicts (`light` et `dark`), jamais un seul — sinon `theme["ma_clé"]` lève un `KeyError` en mode clair ou reste figé dans l'ancien thème en mode sombre. Utiliser `theme.get("ma_clé", repli)` uniquement pour une clé ajoutée après coup dans du code qui doit rester compatible avec un thème potentiellement incomplet (rare — préférer ajouter la clé aux deux dicts).

Certains fichiers redéfinissent des couleurs inline au lieu de lire `THEMES` (ex. `mosaic_canvas.py::_apply_theme_bg` : `bg = "#2b2b2b" if dark else "#f5f5f5"`, dupliqué de `THEMES["dark"]["bg"]`). C'est de la dette existante, pas un pattern à reproduire — pour une nouvelle couleur, toujours passer par `get_current_theme()`, pas par un `if dark else` en dur.

## 2. Où vit l'état `dark_mode`

`dark_mode` est un booléen **par panneau**, pas un singleton global unique — voir skill `panels` pour le mécanisme général panel1/panel2. Trois emplacements à connaître :

- `panel._state.dark_mode` — l'état réel de CE panneau (`AppState.dark_mode`, `state.py`, défaut `False`).
- `modules.qt.state.state` (module-level, importé partout comme `_state_module.state` ou `state`) — pointeur vers le `_state` du panneau **actif**, réassigné à chaque changement de focus/langue/thème (voir `MosaicView.py::_toggle_theme` et `_on_language_change`). `get_current_theme()` lit toujours CE pointeur, jamais un panneau précis — donc l'appeler seulement dans un contexte où `state` pointe déjà vers le bon panneau.
- Config persistée : `get_config_manager().get_dark_mode()` / `.set_dark_mode(dark_mode, save=True)` (`config_manager.py`, clé `'dark_mode'`, défaut `False`). C'est la source de vérité au démarrage et pour un nouveau panel2 ouvert en split (`MainWindow._open_split`, `MosaicView.py` : `self._panel2._state.dark_mode = get_config_manager().get_dark_mode()`).

**Piège** : les deux panneaux partagent le MÊME thème visuel (pas de dark_mode indépendant par panneau à l'usage) — `_toggle_theme()` bascule le panneau actif puis **synchronise manuellement** `dark_mode` sur tous les autres panneaux (boucle sur `self._all_panels()` dans `MainWindow._toggle_theme`). Si un nouveau code lit `dark_mode`, le lire sur le panneau concerné (`panel._state.dark_mode`) plutôt que de supposer une seule source globale.

## 3. Bascule et application — `toggle_theme_qt.py`

Trois fonctions, à ne pas confondre :

- **`toggle_theme(app, canvas, left_panel, tab_bar=None)`** — inverse `state.dark_mode`, sauvegarde en config, puis appelle `apply_app_theme` + `apply_theme`. Bas niveau, généralement pas à appeler directement (voir `MainWindow._toggle_theme()` ci-dessous qui orchestre le multi-panneau).
- **`apply_app_theme(app)`** — partie **globale à l'application**, à appeler **UNE SEULE FOIS** par changement de thème quel que soit le nombre de panneaux : stylesheet `QApplication` (menus, tooltips natifs, scrollbars, `QDialog` génériques...), `QPalette` Qt (radio/checkbox Fusion), barre de titre Windows via DWM (`_set_titlebar_dark`). **Piège — ordre palette / feuille de style** : `app.setPalette` est appelé **avant** `app.setStyleSheet`, jamais après : `setStyleSheet` fige sur chaque widget sans couleur dans la feuille la palette alors courante ; posée après, la nouvelle palette ne leur parvient plus, et ils gardent les couleurs du thème précédent (constaté sur le bouton « … » de la barre de menus, `qt_menubar_ext_button` : noir sur fond sombre au démarrage en thème sombre, blanc sur fond clair après la bascule). Elle ne met à jour aucune fenêtre secondaire : celles-ci suivent le thème par `language_signal`, émis à la fin de `MainWindow._toggle_theme` (§5). **Ne jamais y rajouter une liste de fenêtres à retraduire** : chaque fenêtre ferait alors tout son travail deux fois (une fois par la liste, une fois par le signal).
- **`apply_theme(app, canvas, left_panel, tab_bar=None, render=True, rebuild=True)`** — partie **spécifique à UN panneau** : fond du canvas (`canvas._apply_theme_bg()`), fond du `left_panel`, couleur de survol des icônes de la toolbar (`IconToolbarQt.set_hover_color`, qui repose aussi le fond de l'icône survolée ou focalisée — skill `icon-toolbar` — et `set_slider_theme`), thème de la tab_bar, puis la mosaïque. À appeler une fois par panneau visible. `rebuild=True` reconstruit la mosaïque (`render_mosaic`, nouveau panneau du double affichage) ; `rebuild=False` (bascule du thème : `toggle_theme` et la boucle de `MainWindow._toggle_theme`) la recolore seulement, par `MosaicCanvas.refresh_theme_colors` (couleurs des noms et extensions des vignettes, champ de renommage en cours, message d'accueil d'un panneau vide) : vignettes, dossiers et minimap lisent le thème dans leur `paint()`, et les images des vignettes, en cache, n'en dépendent pas. **Piège** : `render_mosaic` recrée tous les items de la scène, plus d'une demi-seconde par panneau sur un comic de quelques centaines de pages ; toute nouvelle couleur portée par un item de la scène (et non lue dans `paint()`) doit être reposée dans `refresh_theme_colors`, sinon elle reste dans l'ancien thème jusqu'au prochain `render_mosaic`. `render=False` (démarrage, `restore_session`) ne touche pas à la mosaïque.

**Coût d'une bascule** : `app.setStyleSheet` fait recalculer par Qt le style de **tous** les widgets de l'application, fenêtres masquées comprises (Bibliothèque préchauffée : ~570 widgets) — environ 2 s pour un millier de widgets, le poste le plus lourd. **Piste écartée, essai fait** : écrire la feuille globale avec des renvois à la palette (`color: palette(window-text)`), la poser une seule fois et ne changer que la palette à la bascule ne marche pas — Qt lit ces renvois une seule fois, au calcul du style d'un widget, et garde ensuite la couleur trouvée (texte de la barre de menus resté noir après le passage en sombre). La feuille globale doit donc être réécrite à chaque bascule. Seules pistes restantes pour ces 2 s, jugées trop longues et risquées pour un geste rare : porter les règles thémées par les fenêtres elles-mêmes plutôt que par l'application, ou réduire les feuilles de style individuelles de la Bibliothèque (427 de ses widgets en ont une). Chaque événement que Qt envoie pendant ce calcul (des dizaines de milliers) passe par les filtres d'événements posés sur l'application : un tel filtre (`MainWindow.eventFilter`) sort tout de suite pour les types qui ne l'intéressent pas, sans import ni travail avant ce test.

Orchestration réelle multi-panneaux : `MainWindow._toggle_theme()` dans `MosaicView.py`. Pattern à suivre pour tout nouveau point d'entrée qui bascule le thème :
1. `toggle_theme(...)` sur le panneau actif (appelle `apply_app_theme` une fois + `apply_theme` pour ce panneau).
2. Rejouer manuellement `_metadata_tab.apply_theme()`, `apply_separator_theme()`, `_update_status_bar()` pour le panneau actif (ces méthodes ne sont PAS dans `apply_theme()` générique).
3. Boucler sur `self._all_panels()` (skill `panels`) pour synchroniser `dark_mode` + rejouer `apply_theme()` (pas `apply_app_theme`, déjà fait) + les mêmes méthodes annexes sur chaque autre panneau, en basculant temporairement `_state_module.state = p._state` pour que `get_current_theme()` lise le bon panneau pendant l'itération.
4. `language_signal.emit(...)` en fin de fonction : c'est par ce seul signal que les fenêtres secondaires (bibliothèque, dialogues ouverts) suivent le thème (`AppWindowMixin._on_language_signal`, §5).

## 4. Barre de titre Windows (DWM)

`_set_titlebar_dark(window, dark, force_repaint=False)` dans `toggle_theme_qt.py` — utilise `DwmSetWindowAttribute` (attribut 20 = `DWMWA_USE_IMMERSIVE_DARK_MODE`) via `ctypes`, no-op hors Windows (`sys.platform != "win32"`). `force_repaint=True` envoie `WM_NCACTIVATE` pour forcer Windows 10 à repeindre immédiatement (le DWM seul ne suffit pas toujours). Appelé automatiquement par `apply_app_theme` pour toutes les fenêtres top-level visibles, et séparément au démarrage dans `session_restore_qt.py::restore_session` (200ms après `show()`, pour éviter un flash blanc).

**Fenêtre principale : barre de titre dessinée par Qt** (`TitleBar`, `title_bar_qt.py`, skill `window-title`), pas par Windows — DWM n'y change rien de visible. Son thème suit `language_signal` (émis par `_toggle_theme`) et se lit dans la configuration (`get_config_manager().get_dark_mode()`), jamais dans `state.state`, qui n'existe pas encore quand la barre est créée. Couleurs : `toolbar_bg` (fond), `text`, `disabled` (titre quand la fenêtre n'est pas au premier plan). Exception : son séparateur et la poignée de la séparation des panneaux partagent des couleurs fixes, qui ne suivent pas le thème (`SEPARATION_COLORS` de `title_bar_qt.py`, dessinées par `_SeparationGrip` et `_EqualSplitterHandle.paintEvent`), pour ne faire qu'un.

## 5. Le pattern `_apply_theme()` par widget/fenêtre — À REPRODUIRE pour tout nouveau widget

Il n'y a **aucune propagation automatique** au-delà de ce que `apply_app_theme`/`apply_theme` couvrent explicitement (stylesheet global + fond canvas/toolbar/tab_bar). Chaque fenêtre secondaire et chaque widget custom avec ses propres couleurs doit implémenter sa propre méthode (nommée `_apply_theme()`, `apply_theme()`, ou `_apply_theme_bg()` selon les fichiers — pas de convention stricte de nom, mais toujours présente) et être rappelée au bon moment. Exemple minimal représentatif (`dialogs_qt.py::InfoDialog`) :

```python
def _apply_theme(self):
    from modules.qt.state import get_current_theme
    theme = get_current_theme()
    bg   = theme['bg']
    fg   = theme['text']
    alt  = theme.get('toolbar_bg', bg)
    sep  = theme.get('separator', '#aaaaaa')
    self.setStyleSheet(f"QDialog {{ background: {bg}; }}")
    self._lbl.setStyleSheet(f"color: {fg}; background: {bg};")
    self._btn_ok.setStyleSheet(
        f"QPushButton {{ background: {alt}; color: {fg}; "
        f"border: 1px solid {sep}; padding: 4px 12px; border-radius: 3px; }} "
        f"QPushButton:hover {{ background: {sep}; }}"
    )
```

Règles pour tout nouveau widget/fenêtre :

- **Appeler `_apply_theme()` dans `__init__`**, avant le premier `show()`.
- **Suivre `language_signal.changed`** si la fenêtre peut rester ouverte pendant un changement de thème (le signal de langue est aussi émis en fin de `_toggle_theme()`, voir §3 point 4). Une fenêtre de premier niveau le fait par `AppWindowMixin._connect_language()` (skill `window-title`), dont le gestionnaire appelle `_on_language_signal()` (`_apply_theme()` puis `_retranslate()`) à chaque émission et débranche le signal à la fermeture et à la destruction ; rien d'autre à faire pour qu'elle suive le thème. Une fenêtre gardée masquée toute la session peut redéfinir `_on_language_signal` pour remettre ce travail à son prochain affichage (modèle : `LibraryWindow`, skill `library`). Un widget qui n'est pas une fenêtre branche lui-même un gestionnaire en lambda, débranché par `disconnect_on_destroy` — même piège `deleteLater()` sans `finished` que documenté dans `CLAUDE.md` règle UI n°2.
- **Toujours lire les couleurs via `get_current_theme()`**, jamais un `"#xxxxxx" if dark else "#yyyyyy"` en dur sauf si on reproduit une teinte dérivée absente de `THEMES` (ex. `mosaic_canvas.py` dérive `sep`/`handle`/`line` pour les scrollbars à partir de `dark` directement plutôt que d'ajouter ces clés à `THEMES` — dette tolérée, pas un modèle à copier pour une couleur nouvelle qui a sa place naturelle dans `THEMES`).

## 6. Icône et menu pour basculer le thème

- Bouton de la colonne d'icônes : id `"toggle_theme"` dans `ICON_DEFINITIONS`/`_ACTIVATION_RULES` (`icon_toolbar_qt.py`) — voir skill `icon-toolbar` pour la mécanique générale des boutons. Callback câblé dans le dict `toolbar_callbacks` de `build_icon_toolbar` : `"toggle_theme": mw._toggle_theme`.
- Entrée de menu : `_populate_system_menu` (`menubar_qt.py`) et `build_menubar_callbacks` (`menubar_callbacks_qt.py`), même callback `mw._toggle_theme`.
- Clic droit sur une zone vide de la mosaïque : sous-menu Système, construit par le même `_populate_system_menu` (`menubar_qt.py`) — voir skill `qt-context-menus`.

Les trois pointent vers la même méthode `MainWindow._toggle_theme()` (§3) — ne jamais appeler `toggle_theme()` (bas niveau) directement depuis un nouveau point d'entrée UI, toujours passer par `MainWindow._toggle_theme()` pour garder la synchro multi-panneaux et les fenêtres secondaires à jour.

## 7. Démarrage / restauration de session

`session_restore_qt.py::restore_session()` (voir skill `session-restore` pour le reste de la restauration de session) applique le thème **avant** `win.show()` pour éviter un flash blanc au lancement : lit `cfg.get_dark_mode()`, force `win._state.dark_mode = True` si nécessaire, puis `apply_app_theme(app)` + `apply_theme(..., render=False)` (pas de rendu de mosaïque à ce stade, la fenêtre n'est pas encore affichée).

## Checklist avant de considérer un changement de thème terminé

1. La couleur ajoutée/modifiée existe dans **les deux** sous-dicts de `THEMES` (`state.py`).
2. Le widget/fenêtre concerné a une méthode `_apply_theme()` (ou équivalent) appelée à la construction.
3. Si la fenêtre peut rester ouverte pendant un bascule thème/langue : reconnectée à `language_signal.changed` (`_connect_language()` pour une fenêtre), déconnectée proprement à la fermeture.
4. Une couleur posée sur un item de la mosaïque (et non lue dans son `paint()`) est aussi reposée par `MosaicCanvas.refresh_theme_colors`.
5. Testé mentalement (ou par lecture de code) dans les deux sens clair→sombre ET sombre→clair, pas seulement un sens.
