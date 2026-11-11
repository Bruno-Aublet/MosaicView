---
name: qt-context-menus
description: Ajouter, modifier ou déboguer un menu contextuel (clic droit) sur un widget Qt (QLineEdit, QTextEdit, QLabel, QTextBrowser, QSpinBox, ascenseur QScrollBar) dans MosaicView, ou les menus du clic droit de la mosaïque (context_menus_qt.py : show_canvas_context_menu, qui reprend les menus de la barre par menubar_qt.bar_menus, et show_image_context_menu, qui reprend _populate_images_menu). Utiliser dès qu'une tâche implique customContextMenuRequested, setContextMenuPolicy, le filtre commun utils._QtMenuReplacer / install_qt_menu_replacer, un menu du clic droit de la mosaïque, ou un menu clic-droit non stylé/non traduit.
---

# Menus contextuels Qt — MosaicView

**Jamais de menu contextuel Qt natif** : tout widget avec clic droit (`QLineEdit`, `QTextEdit`, `QLabel` en mode lien/sélectionnable, `QTextBrowser`, etc.) doit remplacer le menu Qt par défaut (anglais, non stylé) par un menu traduit et thémé via `setContextMenuPolicy(Qt.CustomContextMenu)` + `customContextMenuRequested`.

## Fonctions génériques dans `modules/qt/utils.py`

- `setup_lineedit_context_menu(edit, allow_copy_cut=True)` — QLineEdit ; `allow_copy_cut=False` pour un champ sensible (mot de passe/clé API)
- `setup_textedit_context_menu(edit)` — QTextEdit : Annuler / Refaire (historique de la zone, `document().isUndoAvailable()`/`isRedoAvailable()`), Couper / Copier / Coller / Tout sélectionner, comme le menu d'un QLineEdit ; en lecture seule, seuls Copier et Tout sélectionner restent actifs. Utilisée par toutes les zones de texte (nom d'une vignette, outil texte, commentaire du GIF, éditeurs ComicInfo et NFO, journal d'erreurs)
- `setup_text_browser_context_menu(browser)` — QTextBrowser
- `setup_link_label_context_menu(label, get_url)` — QLabel avec URL(s) web cliquable(s) ; `get_url` : callable → `str` ou `list[(nom, url)]`, jamais une valeur figée
- `setup_path_label_context_menu(label, get_path, open_fn)` — QLabel chemin fichier/dossier (Explorateur Windows, pas URL web)
- `setup_html_label_context_menu(label)` — QLabel HTML générique, liens extraits du HTML courant à l'ouverture du menu
- `setup_selectable_label_context_menu(label)` — QLabel sélectionnable sans lien (`Qt.TextSelectableByMouse`) : Copier / Tout sélectionner. Définie mais pas encore appelée ailleurs dans le projet — prête à l'emploi pour un futur QLabel sélectionnable.

## Menus du clic droit de la mosaïque — `modules/qt/context_menus_qt.py`

Ouverts par `MosaicCanvas.mousePressEvent` (clic droit, rien dans un sous-dossier), branchés dans `PanelWidget.__init__` (« Menus contextuels ») avec le dict `self._build_menubar_callbacks()` du panneau — le même que celui de la barre, `state` compris. **Aucune commande n'y est écrite deux fois** : leur contenu vient des fonctions de la barre (skill `menu-bar`), avec ses libellés, raccourcis affichés et conditions de grisage.

- **`show_canvas_context_menu`** (clic sur une zone vide) : « Masquer/Afficher la colonne d'icônes », « Masquer/Afficher la minimap », puis les 8 menus de la barre en sous-menus, tirés de `menubar_qt.bar_menus()` (la liste qu'utilise aussi `build_menubar`) : chaque sous-menu est créé par `_make_menu` puis peuplé par la même fonction `_populate_*_menu`. Le clic droit sur une zone vide ne retire pas la sélection : le sous-menu Images agit sur les pages déjà sélectionnées. **Ajouter une commande à la barre l'ajoute ici** ; ne jamais ajouter d'entrée propre à ce menu en dehors des deux lignes du haut.
- **`show_image_context_menu`** (clic sur une vignette, sélectionnée d'abord si elle ne l'était pas) : le menu Images de la barre (`_populate_images_menu`, à plat, sous-menu « Type de page » compris, skill `menu-bar`), puis, avec `menubar_qt._add_action` et les mêmes conditions que la barre : Doublons ; « Modifier le fichier ComicInfo.xml » (actif seulement si la vignette cliquée est le ComicInfo.xml du document, `comic_info_entry`) ; « Modifier le fichier NFO » (actif seulement sur un `.nfo` lisible, callback `edit_nfo` → `PanelWidget._open_nfo_for_edit(entrée)`, skill `nfo-editor`) ; « Effacer le marque-page de cette BD » ; Enregistrer la sélection, l'enregistrer dans un dossier, l'imprimer ; Copier, Couper ; Supprimer, Inverser la sélection, Tout sélectionner, Désélectionner. Le mode marque-pages et « Effacer tous les marque-pages » sont dans le menu Archives (zone vide et barre).
- **Commande à laquelle on passe une entrée** (GIF animé, `.nfo`) : construite autour d'une `lambda`, qui n'a pas l'attribut `mv_blocked` du wrapper de `_build_menubar_callbacks` ; la condition de grisage lit alors elle-même `getattr(callback, "mv_blocked", None)` (modèle : `edit_nfo` dans `_populate_file_menu` et `show_image_context_menu`), sans quoi l'entrée resterait active (mais sans effet) pendant une opération en arrière-plan.
- `show_dir_context_menu` (dossier virtuel : aplatir) et `show_bookmark_indicator_menu` (indicateur de la barre d'état : les deux effacements de marque-pages) restent des menus propres.
- **Tableau Pages de l'onglet Métadonnées** (`MetadataTab._show_page_type_menu`, skill `tabs`) : menu thémé par `_make_menu`, contenu par `menubar_qt.populate_page_type_menu`, le même que le sous-menu « Type de page » du menu Images, appliqué aux lignes sélectionnées.
- `_make_menu` / `_add_disabled` sont aussi utilisés par `scan_dialog_qt.show_scan_log_context_menu`.

## Menus construits par Qt lui-même — filtre commun de l'application

Les ascenseurs (`QScrollBar`) et les champs numériques (`QSpinBox`, `QDoubleSpinBox` et tout `QAbstractSpinBox`) ont un menu du clic droit construit par Qt, en anglais, présent dans toutes les fenêtres (listes, tableaux, zones de texte, mosaïque, outils de la visionneuse). Ils ne se traitent pas widget par widget : `utils._QtMenuReplacer`, posé une fois sur l'application par `utils.install_qt_menu_replacer(app)` (`MosaicView.main`, juste après la création de `QApplication`), les remplace partout, y compris dans les fenêtres créées plus tard.

- **Ascenseur** : aucun menu (`eventFilter` rend `True`).
- **Champ numérique**, clic sur la zone de saisie comme sur les flèches : le menu traduit d'une zone de saisie (`_show_lineedit_menu`, le même que `setup_lineedit_context_menu`), ouvert sur le `QLineEdit` du champ (`findChild(QLineEdit)` : `lineEdit()` est protégé en C++). Un champ ou sa zone de saisie qui a reçu son propre menu (`Qt.CustomContextMenu`) le garde.
- Le filtre voit chaque événement de l'application : il sort dès la première ligne pour tout ce qui n'est pas `QEvent.ContextMenu`. Rien de coûteux n'y entre.
- **Nouveau widget Qt à menu natif** de la même nature (construit par Qt, présent partout) : l'ajouter à ce filtre plutôt que widget par widget.
- Testé par `tests/test_qt_menu_replacer.py` (méthode extraite par AST, classes de Qt remplacées ; pose au démarrage vérifiée dans `MosaicView.main`).

## Pièges

- **`mousePressEvent` custom** : filtrer avec `if e.button() == Qt.LeftButton`, sinon le clic droit est absorbé et `customContextMenuRequested` ne se déclenche jamais. S'applique aussi à tout `mousePressEvent` custom préexistant sur un widget cliquable (ex. un label qui ouvre l'Explorateur au clic gauche) : sans ce filtre, il absorbe aussi le clic droit et empêche `customContextMenuRequested` de se déclencher.
- **Palette héritée grisée** : utiliser `_themed_menu_stylesheet(font)` dans `utils.py`.
- **`setContextMenuPolicy(Qt.DefaultContextMenu)` forcé explicitement** sur un widget existant peut être un choix délibéré antérieur pour garder le menu natif (ex. un champ mot de passe) — vérifier avant de le retirer par réflexe. Dans ce cas, remplacer par le menu custom avec `allow_copy_cut=False` plutôt que de laisser un menu natif non traduit.
- **`menu.deleteLater()` obligatoire après `menu.exec(...)` (ou `popup`) pour tout `QMenu` créé avec un parent**, dans la même fonction, après la lecture de l'action choisie (la destruction est différée jusqu'au retour dans la boucle d'événements). Sans lui, le parent garde le menu, ses actions et ce que leurs lambdas capturent : un menu de plus à chaque clic droit, jusqu'à la destruction du parent — la fin de la session pour la Bibliothèque ou un panneau. Garde-fou : `tests/test_check_context_menus_deleted.py`. **Cas aggravé : menu dont une `QAction` porte `setShortcut(...)`.** Dans `context_menus_qt.py`, `_make_menu(parent)` crée le `QMenu` avec le panneau comme parent Qt permanent ; sans destruction explicite après fermeture, chaque ouverture du menu (clic droit) laisse un `QMenu` fantôme vivant, avec ses `QAction` à raccourci. Une `QAction` dont le raccourci est actif (contexte par défaut) entre en conflit avec le `QShortcut` global équivalent (câblé dans `MosaicView.py`) — Qt désactive alors silencieusement le raccourci ambigu (aucun message, la touche ne fait plus rien), alors que cliquer l'item du menu continue de fonctionner puisqu'il invoque directement le callback sans passer par la résolution de raccourci. Les entrées des menus de la mosaïque viennent de `menubar_qt._add_action`, qui pose leurs raccourcis en `Qt.WidgetShortcut` (affichage seul) ; les fonctions de `context_menus_qt.py` (`show_canvas_context_menu`, `show_image_context_menu`, `show_dir_context_menu`, `show_bookmark_indicator_menu`) appellent quand même `menu.deleteLater()` juste après `menu.exec(global_pos)`, pour ne pas accumuler les menus — tout nouveau menu contextuel de ce fichier (ou tout `QMenu` ailleurs qui poserait un `setShortcut` déjà utilisé comme raccourci global) doit faire de même. La barre de menus, elle, garde ses entrées vivantes entre deux ouvertures : ses raccourcis sont posés en `Qt.WidgetShortcut` (affichage seul) par `menubar_qt._add_action` — voir skill `menu-bar`.

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

**Piège — ne pas mettre en italique.** `font-style: italic` (en CSS ou via `act.setFont()`) sur un item désactivé casse l'alignement de tout le menu : Qt calcule l'indentation/la largeur de chaque `QAction` selon sa propre police, donc un item avec une police différente des autres (italique vs normale) décale visuellement tout le menu (icônes, raccourcis clavier, flèches de sous-menu désalignés). La couleur grisée seule suffit à distinguer un item désactivé — ne pas ajouter l'italique.

`context_menus_qt.py::_disable_action(act)` centralise `act.setEnabled(False)` pour les items ajoutés par `menu.addAction` dans ce fichier (items simples via `_add_disabled(menu, label)`) ; les entrées venues de la barre sont grisées par `menubar_qt._add_action` (`enabled=`). Le style vient uniquement du stylesheet du menu, ne rien ajouter sur l'action elle-même.

## QMenu/QMenuBar — police écrasée par le stylesheet global

Voir skill `fonts` pour `get_current_font()`, la fonction qui doit fournir ce `font`. Sur Windows, `setFont()` sur `QMenu`/`QMenuBar` est écrasé par le stylesheet global (`app.setStyleSheet` contient `QMenuBar { font-family: ... }`/`QMenu { font-family: ... }`, prioritaire sur `setFont()`). Pour tout `QMenu`/`QMenuBar` qui doit respecter la police courante, appeler en plus de `setFont` :
```python
menu.setStyleSheet(f'QMenu {{ font-family: "{font.family()}"; font-size: {font.pointSize()}pt; }}')
mb.setStyleSheet(f'QMenuBar {{ font-family: "{font.family()}"; font-size: {font.pointSize()}pt; }}')
```
Points d'application : `build_menubar`/`_add_submenu` dans `menubar_qt.py` (sur `mb` et chaque menu/sous-menu), `_make_menu` dans `context_menus_qt.py`.
