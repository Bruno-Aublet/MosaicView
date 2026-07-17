---
name: qt-context-menus
description: Ajouter, modifier ou déboguer un menu contextuel (clic droit) sur un widget Qt (QLineEdit, QTextEdit, QLabel, QTextBrowser) dans MosaicView. Utiliser dès qu'une tâche implique customContextMenuRequested, setContextMenuPolicy, ou un menu clic-droit non stylé/non traduit.
---

# Menus contextuels Qt — MosaicView

**Jamais de menu contextuel Qt natif** : tout widget avec clic droit (`QLineEdit`, `QTextEdit`, `QLabel` en mode lien/sélectionnable, `QTextBrowser`, etc.) doit remplacer le menu Qt par défaut (anglais, non stylé) par un menu traduit et thémé via `setContextMenuPolicy(Qt.CustomContextMenu)` + `customContextMenuRequested`.

## Fonctions génériques dans `modules/qt/utils.py`

- `setup_lineedit_context_menu(edit, allow_copy_cut=True)` — QLineEdit ; `allow_copy_cut=False` pour un champ sensible (mot de passe/clé API)
- `setup_textedit_context_menu(edit)` — QTextEdit
- `setup_text_browser_context_menu(browser)` — QTextBrowser
- `setup_link_label_context_menu(label, get_url)` — QLabel avec URL(s) web cliquable(s) ; `get_url` : callable → `str` ou `list[(nom, url)]`, jamais une valeur figée
- `setup_path_label_context_menu(label, get_path, open_fn)` — QLabel chemin fichier/dossier (Explorateur Windows, pas URL web)
- `setup_html_label_context_menu(label)` — QLabel HTML générique, liens extraits du HTML courant à l'ouverture du menu
- `setup_selectable_label_context_menu(label)` — QLabel sélectionnable sans lien (`Qt.TextSelectableByMouse`) : Copier / Tout sélectionner. Définie mais pas encore appelée ailleurs dans le projet — prête à l'emploi pour un futur QLabel sélectionnable.

## Pièges

- **`mousePressEvent` custom** : filtrer avec `if e.button() == Qt.LeftButton`, sinon le clic droit est absorbé et `customContextMenuRequested` ne se déclenche jamais. S'applique aussi à tout `mousePressEvent` custom préexistant sur un widget cliquable (ex. un label qui ouvre l'Explorateur au clic gauche) : sans ce filtre, il absorbe aussi le clic droit et empêche `customContextMenuRequested` de se déclencher.
- **Palette héritée grisée** : utiliser `_themed_menu_stylesheet(font)` dans `utils.py`.
- **`setContextMenuPolicy(Qt.DefaultContextMenu)` forcé explicitement** sur un widget existant peut être un choix délibéré antérieur pour garder le menu natif (ex. un champ mot de passe) — vérifier avant de le retirer par réflexe. Dans ce cas, remplacer par le menu custom avec `allow_copy_cut=False` plutôt que de laisser un menu natif non traduit.

## Items désactivés dans un menu

Tout item de menu désactivé (`QAction.setEnabled(False)`, y compris une `QAction` de sous-menu créée via `menu.addMenu(...)`) doit utiliser la couleur `disabled` du thème courant (`theme["disabled"]` — **jamais** une couleur codée en dur, elle diffère entre thème clair `#999999` et sombre `#aaaaaa`), via le stylesheet du menu :
```python
theme = get_current_theme()
menu.setStyleSheet(
    f'QMenu {{ font-family: "{font.family()}"; font-size: {font.pointSize()}pt; }} '
    f'QMenu::item:disabled {{ color: {theme["disabled"]}; }}'
)
```
Voir `_themed_menu_stylesheet(font)` dans `utils.py` (déjà en place pour les `setup_*_context_menu` — fixe aussi `background-color`/`color`/`border` du menu, pas seulement la couleur disabled) et `_make_menu(parent)` dans `context_menus_qt.py` (stylesheet distinct et plus minimal : uniquement police + `QMenu::item:disabled` — pas de `background-color`/`color`/`border` explicites sur le menu lui-même). Les deux fonctions ne sont **pas** interchangeables, ne pas supposer qu'elles partagent le même stylesheet.

**Piège — ne pas mettre en italique.** `font-style: italic` (en CSS ou via `act.setFont()`) sur un item désactivé casse l'alignement de tout le menu : Qt calcule l'indentation/la largeur de chaque `QAction` selon sa propre police, donc un item avec une police différente des autres (italique vs normale) décale visuellement tout le menu (icônes, raccourcis clavier, flèches de sous-menu désalignés). Constaté et corrigé (2026-07-06). La couleur grisée seule suffit à distinguer un item désactivé — ne pas ajouter l'italique.

`context_menus_qt.py::_disable_action(act)` centralise `act.setEnabled(False)` pour tous les sites de désactivation du fichier (items simples via `_add_disabled(menu, label)`, et `QAction` de sous-menu désactivées après coup) — le style vient uniquement du stylesheet du menu, ne rien ajouter sur l'action elle-même.

## QMenu/QMenuBar — police écrasée par le stylesheet global

Voir skill `fonts` pour `get_current_font()`, la fonction qui doit fournir ce `font`. Sur Windows, `setFont()` sur `QMenu`/`QMenuBar` est écrasé par le stylesheet global (`app.setStyleSheet` contient `QMenuBar { font-family: ... }`/`QMenu { font-family: ... }`, prioritaire sur `setFont()`). Pour tout `QMenu`/`QMenuBar` qui doit respecter la police courante, appeler en plus de `setFont` :
```python
menu.setStyleSheet(f'QMenu {{ font-family: "{font.family()}"; font-size: {font.pointSize()}pt; }}')
mb.setStyleSheet(f'QMenuBar {{ font-family: "{font.family()}"; font-size: {font.pointSize()}pt; }}')
```
Points d'application : `build_menubar`/`_add_submenu` dans `menubar_qt.py` (sur `mb` et chaque menu/sous-menu), `_make_menu` dans `context_menus_qt.py`.
