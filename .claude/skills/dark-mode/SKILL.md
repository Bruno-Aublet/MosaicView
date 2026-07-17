---
name: dark-mode
description: Localiser ou modifier le mode sombre / thème clair-sombre de MosaicView (THEMES dans state.py, toggle_theme_qt.py, pattern _apply_theme() par fenêtre). Utiliser dès qu'une tâche touche à dark_mode, get_current_theme, ou à une couleur qui ne suit pas le thème.
---

# Mode sombre / thème — MosaicView

Le thème clair/sombre n'est pas un mécanisme Qt natif (pas de `QPalette` seule) : c'est un dict de couleurs choisi par un booléen d'état, appliqué à la main via stylesheet à **chaque** fenêtre/widget qui affiche une couleur. Il n'y a pas de mise à jour automatique — tout widget qui ne rejoue pas son application de thème au bon moment reste figé dans l'ancien thème.

## 1. Où sont définies les couleurs

Tout est dans `modules/qt/state.py`, dict `THEMES` (~ligne 117) :

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
    }
}

def get_current_theme():
    """Retourne le thème actuel (clair ou sombre) selon state.dark_mode"""
    return THEMES["dark"] if state.dark_mode else THEMES["light"]
```

**Pour ajouter une nouvelle couleur thémée** : ajouter la clé dans les DEUX sous-dicts (`light` et `dark`), jamais un seul — sinon `theme["ma_clé"]` lève un `KeyError` en mode clair ou reste figé dans l'ancien thème en mode sombre. Utiliser `theme.get("ma_clé", repli)` uniquement pour une clé ajoutée après coup dans du code qui doit rester compatible avec un thème potentiellement incomplet (rare — préférer ajouter la clé aux deux dicts).

Certains fichiers redéfinissent des couleurs inline au lieu de lire `THEMES` (ex. `mosaic_canvas.py::_apply_theme_bg` ligne ~1104 : `bg = "#2b2b2b" if dark else "#f5f5f5"`, dupliqué de `THEMES["dark"]["bg"]`). C'est de la dette existante, pas un pattern à reproduire — pour une nouvelle couleur, toujours passer par `get_current_theme()`, pas par un `if dark else` en dur.

## 2. Où vit l'état `dark_mode`

`dark_mode` est un booléen **par panneau**, pas un singleton global unique — voir skill `panels` pour le mécanisme général panel1/panel2. Trois emplacements à connaître :

- `panel._state.dark_mode` — l'état réel de CE panneau (`AppState.dark_mode`, `state.py` ligne ~73, défaut `False`).
- `modules.qt.state.state` (module-level, importé partout comme `_state_module.state` ou `state`) — pointeur vers le `_state` du panneau **actif**, réassigné à chaque changement de focus/langue/thème (voir `MosaicView.py::_toggle_theme` et `_on_language_change`). `get_current_theme()` lit toujours CE pointeur, jamais un panneau précis — donc l'appeler seulement dans un contexte où `state` pointe déjà vers le bon panneau.
- Config persistée : `get_config_manager().get_dark_mode()` / `.set_dark_mode(dark_mode, save=True)` (`config_manager.py` ligne ~175 et ~324, clé `'dark_mode'`, défaut `False`). C'est la source de vérité au démarrage et pour un nouveau panel2 ouvert en split (`MosaicView.py:459` : `self._panel2._state.dark_mode = get_config_manager().get_dark_mode()`).

**Piège** : les deux panneaux partagent le MÊME thème visuel (pas de dark_mode indépendant par panneau à l'usage) — `_toggle_theme()` bascule le panneau actif puis **synchronise manuellement** `dark_mode` sur tous les autres panneaux (`MosaicView.py:341-350`). Si un nouveau code lit `dark_mode`, le lire sur le panneau concerné (`panel._state.dark_mode`) plutôt que de supposer une seule source globale.

## 3. Bascule et application — `toggle_theme_qt.py`

Trois fonctions, à ne pas confondre :

- **`toggle_theme(app, canvas, left_panel, tab_bar=None)`** — inverse `state.dark_mode`, sauvegarde en config, puis appelle `apply_app_theme` + `apply_theme`. Bas niveau, généralement pas à appeler directement (voir `MainWindow._toggle_theme()` ci-dessous qui orchestre le multi-panneau).
- **`apply_app_theme(app)`** — partie **globale à l'application**, à appeler **UNE SEULE FOIS** par changement de thème quel que soit le nombre de panneaux : stylesheet `QApplication` (menus, tooltips natifs, scrollbars, `QDialog` génériques...), `QPalette` Qt (radio/checkbox Fusion), barre de titre Windows via DWM (`_set_titlebar_dark`), et retranslate de toutes les fenêtres secondaires ouvertes qui gèrent le thème (liste en dur ligne ~206-212 : `_LicenseDialog`, `_ChangelogDialog`, `_ComicVineDialog`, `LibraryWindow`, etc. — **toute nouvelle fenêtre top-level avec son propre `_apply_theme()`/`_retranslate()` doit être ajoutée à cette liste**, sinon elle ne se met pas à jour si elle reste ouverte pendant un bascule de thème).
- **`apply_theme(app, canvas, left_panel, tab_bar=None, render=True)`** — partie **spécifique à UN panneau** : fond du canvas (`canvas._apply_theme_bg()`), fond du `left_panel`, couleur de survol des icônes de la toolbar (`IconToolbarQt.set_hover_color`/`set_slider_theme`), thème de la tab_bar. À appeler une fois par panneau visible.

Orchestration réelle multi-panneaux : `MainWindow._toggle_theme()` dans `MosaicView.py:329-362`. Pattern à suivre pour tout nouveau point d'entrée qui bascule le thème :
1. `toggle_theme(...)` sur le panneau actif (appelle `apply_app_theme` une fois + `apply_theme` pour ce panneau).
2. Rejouer manuellement `_metadata_tab.apply_theme()`, `apply_separator_theme()`, `_update_status_bar()` pour le panneau actif (ces méthodes ne sont PAS dans `apply_theme()` générique).
3. Boucler sur `self._all_panels()` (skill `panels`) pour synchroniser `dark_mode` + rejouer `apply_theme()` (pas `apply_app_theme`, déjà fait) + les mêmes méthodes annexes sur chaque autre panneau, en basculant temporairement `_state_module.state = p._state` pour que `get_current_theme()` lise le bon panneau pendant l'itération.
4. `language_signal.emit(...)` en fin de fonction pour notifier les fenêtres secondaires (bibliothèque, dialogs ouverts) — nécessaire car `apply_app_theme` a déjà tourné mais certaines fenêtres se retraduisent seulement sur ce signal.

## 4. Barre de titre Windows (DWM)

`_set_titlebar_dark(window, dark, force_repaint=False)` dans `toggle_theme_qt.py` (ligne ~21) — utilise `DwmSetWindowAttribute` (attribut 20 = `DWMWA_USE_IMMERSIVE_DARK_MODE`) via `ctypes`, no-op hors Windows (`sys.platform != "win32"`). `force_repaint=True` envoie `WM_NCACTIVATE` pour forcer Windows 10 à repeindre immédiatement (le DWM seul ne suffit pas toujours). Appelé automatiquement par `apply_app_theme` pour toutes les fenêtres top-level visibles, et séparément au démarrage dans `session_restore_qt.py:50-52` (200ms après `show()`, pour éviter un flash blanc).

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
- **Reconnecter sur `language_signal.changed`** si la fenêtre peut rester ouverte pendant un changement de thème (le signal de langue est aussi émis en fin de `_toggle_theme()`, voir §3 point 4) : `self._lang_handler = lambda _: (self._apply_theme(), self._apply_font()); language_signal.changed.connect(self._lang_handler)`, déconnecté dans `finished`/`_on_close` — même piège `deleteLater()` sans `finished` que documenté dans `CLAUDE.md` règle UI n°2.
- **Si la fenêtre est top-level et doit réagir à un bascule de thème même sans changement de langue** (cas des dialogs listés dans `apply_app_theme`, §3) : ajouter la classe à la liste d'imports + `isinstance(...)` dans `apply_app_theme()` (`toggle_theme_qt.py` ligne ~182-213), qui appelle `widget._retranslate()` (pas `_apply_theme()` directement — la convention de cette liste précise est que `_retranslate()` doit lui-même rappeler `_apply_theme()` en interne).
- **Toujours lire les couleurs via `get_current_theme()`**, jamais un `"#xxxxxx" if dark else "#yyyyyy"` en dur sauf si on reproduit une teinte dérivée absente de `THEMES` (ex. `mosaic_canvas.py` dérive `sep`/`handle`/`line` pour les scrollbars à partir de `dark` directement plutôt que d'ajouter ces clés à `THEMES` — dette tolérée, pas un modèle à copier pour une couleur nouvelle qui a sa place naturelle dans `THEMES`).

## 6. Icône et menu pour basculer le thème

- Bouton de la colonne d'icônes : id `"toggle_theme"` dans `ICON_DEFINITIONS`/`_ACTIVATION_RULES` (`icon_toolbar_qt.py`) — voir skill `icon-toolbar` pour la mécanique générale des boutons. Callback câblé ligne ~2185 : `"toggle_theme": mw._toggle_theme`.
- Entrée de menu : `menubar_qt.py:475` et `menubar_callbacks_qt.py:136`, même callback `mw._toggle_theme`.
- Menu contextuel canvas : `context_menus_qt.py:637`, callback `callbacks.get('toggle_theme')` — voir skill `qt-context-menus`.

Les trois pointent vers la même méthode `MainWindow._toggle_theme()` (§3) — ne jamais appeler `toggle_theme()` (bas niveau) directement depuis un nouveau point d'entrée UI, toujours passer par `MainWindow._toggle_theme()` pour garder la synchro multi-panneaux et les fenêtres secondaires à jour.

## 7. Démarrage / restauration de session

`session_restore_qt.py::restore_session()` (ligne ~24-28, voir skill `session-restore` pour le reste de la restauration de session) applique le thème **avant** `win.show()` pour éviter un flash blanc au lancement : lit `cfg.get_dark_mode()`, force `win._state.dark_mode = True` si nécessaire, puis `apply_app_theme(app)` + `apply_theme(..., render=False)` (pas de rendu de mosaïque à ce stade, la fenêtre n'est pas encore affichée).

## Checklist avant de considérer un changement de thème terminé

1. La couleur ajoutée/modifiée existe dans **les deux** sous-dicts de `THEMES` (`state.py`).
2. Le widget/fenêtre concerné a une méthode `_apply_theme()` (ou équivalent) appelée à la construction.
3. Si la fenêtre peut rester ouverte pendant un bascule thème/langue : reconnectée à `language_signal.changed`, déconnectée proprement à la fermeture.
4. Si c'est une fenêtre top-level qui doit réagir même sans changement de langue : ajoutée à la liste `isinstance(...)` d'`apply_app_theme()`.
5. Testé mentalement (ou par lecture de code) dans les deux sens clair→sombre ET sombre→clair, pas seulement un sens.
