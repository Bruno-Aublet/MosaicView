---
name: icon-toolbar
description: Localiser ou modifier la colonne d'icônes verticale de MosaicView (ajout/ordre/activation des boutons, configuration, paliers de taille, footer réglette/combo langue). Utiliser dès qu'une tâche touche à icon_toolbar_qt.py, ICON_DEFINITIONS, ou à un bouton de cette colonne.
---

# Colonne d'icônes — MosaicView

La colonne d'icônes est la barre verticale à gauche de la mosaïque, dans chaque panneau (deux instances indépendantes en split-view). Ce n'est pas une `QToolBar` Qt native : c'est une grille de `QLabel` cliquables (`IconLabel`) dans un `QGridLayout`, avec drag & drop interne pour réordonner, plus un footer (taille icônes, réglette vignettes, combo langue, copyright).

## Fichier unique — `modules/qt/icon_toolbar_qt.py` (~2200 lignes)

Tout vit dans ce seul fichier :
- **`ICON_DEFINITIONS`** (ligne ~33) : liste ordonnée de tous les boutons connus de l'appli (actifs par défaut + "hors layout par défaut"). Chaque entrée : `{"id", "tooltip_key", "png"}`, parfois `"img_path"` (icône hors dossier `icons/`, ex. mail/paypal) ou `"png_alt"/"tooltip_key_alt"` (bouton à deux états, ex. `split_ui`).
- **`DEFAULT_LAYOUT`** (ligne ~103) : sous-ensemble d'ids d'`ICON_DEFINITIONS`, dans l'ordre, affiché par défaut pour un nouveau profil (avant toute personnalisation utilisateur).
- **`_ACTIVATION_RULES`** (ligne ~120) : dict `id → callable(state_getters) -> bool | None`. `None` = toujours actif (jamais grisé).
- **`class IconLabel(QLabel)`** (ligne ~190) : une icône. Survol, focus clavier, clic gauche (déclenche l'action), clic droit (spécial `renumber`/`open_mail`), drag & drop.
  - Clic droit sur `open_mail` → `_show_mail_context_menu()` (ligne ~2001, menu "copier l'adresse") ; clic gauche → callback `open_mail` de `toolbar_callbacks` (`build_icon_toolbar`, ouvre le client mail). Les deux passent par `get_support_email()` (`modules/qt/utils.py`), jamais l'adresse en dur — reconstruite à partir de morceaux séparés pour ne pas apparaître en clair dans le repo public (cible de scraping). Même fonction réutilisée par les skills `menu-bar` et `scan`.
- **`class IconGrid(QWidget)`** (ligne ~480) : le `QGridLayout` conteneur, gère la logique de drop (calcul de l'index d'insertion, indicateur visuel rouge).
- **`class ThumbSizeSlider`** / **`class LanguageComboWidget`** (ligne ~639 / ~957) : les deux widgets du footer, sous les boutons `[−][⚙][+]`.
- **`class _IconConfigDialog(QDialog)`** (ligne ~1101) : fenêtre non-modale de configuration (icônes actives ↔ masquées, checkboxes réglette/combo).
- **`class IconToolbarQt(QWidget)`** (ligne ~1413) : le widget racine, orchestrateur de tout ce qui précède.
- **`build_icon_toolbar(mw, *, is_primary=True)`** (ligne ~2086) : factory appelée par `panel_widget.py`, construit `state_getters` et `callbacks` à partir de `MainWindow`/`PanelWidget` et instancie `IconToolbarQt`.

## Ajouter une nouvelle icône

1. Déposer le PNG dans `icons/` (ou ailleurs si `img_path` est utilisé, voir `open_mail`/`donation`).
2. Ajouter une entrée dans `ICON_DEFINITIONS` avec un `id` unique, le nom du PNG, et une `tooltip_key` (clé de traduction — `None` seulement si le tooltip est géré autrement, ex. `renumber` qui a un tooltip dynamique selon son mode).
3. Si le bouton doit apparaître par défaut : l'ajouter à `DEFAULT_LAYOUT` à la position voulue. Sinon il reste disponible via la fenêtre de configuration (`⚙`) dans la colonne "masquées".
4. Ajouter une règle dans `_ACTIVATION_RULES` (ou `None` si toujours actif) — s'appuie sur les clés de `state_getters` (voir `build_icon_toolbar`, ligne ~2102, pour les state_getters existants : `has_file`, `has_images`, `has_selection`, `has_selected_images`, `has_undo`, `has_redo`, `single_image_selected`, etc. — en ajouter un nouveau si besoin).
5. Ajouter le callback correspondant dans `toolbar_callbacks` (ligne ~2132 de `build_icon_toolbar`), typiquement en réutilisant une entrée déjà présente dans `cb` (`build_menubar_callbacks`/`mw._build_menubar_callbacks`) — la colonne d'icônes et le menu bar partagent la même couche de callbacks, ne pas dupliquer la logique métier.
6. Ajouter une entrée dans `IconToolbarQt._LABEL_KEYS` (ligne ~1987) — clé de traduction du **nom affiché dans la fenêtre de configuration** (`_IconConfigDialog`), distincte de `tooltip_key`. Sans ça, `_get_icon_label()` retourne l'`id` brut non traduit.
7. Si le bouton a un tooltip fixe (pas dynamique comme `renumber`/`split_ui`), rien d'autre à faire : `IconLabel.enterEvent` (ligne ~233) résout `tooltip_key` automatiquement via `OverlayTooltip` (skill `qt-tooltips`) — ne jamais appeler `setToolTip()` natif.

**Ne jamais** coder en dur un texte de tooltip ou de label — toujours passer par une `tooltip_key`/entrée `_LABEL_KEYS` résolue via `_()`, cf. règle CLAUDE.md sur le texte codé en dur.

## Activation/désactivation contextuelle (icône grisée)

`refresh_states()` (ligne ~1667) parcourt `self._icon_widgets` et appelle `_is_active(icon_id)` pour chaque icône, qui exécute la règle correspondante dans `_ACTIVATION_RULES` contre `state_getters`. Une exception dans la règle est absorbée silencieusement (`return False` — l'icône se grise plutôt que de crasher).

`refresh_states()` doit être rappelé après **tout événement qui change l'éligibilité d'un bouton** : sélection modifiée, fichier ouvert/fermé, undo/redo empilé, etc. Chercher les appels existants dans `panel_widget.py` (ex. lignes ~1387, ~1822, ~1947, ~1951) comme modèle si un nouvel événement doit aussi déclencher un rafraîchissement.

Cas spécial `split_ui` (bouton à deux états, icône ET tooltip changent selon `split_active`) : `refresh_states()` gère aussi ce swap de pixmap (ligne ~1670-1696) — s'inspirer de ce bloc pour tout futur bouton bi-état plutôt que de forcer un cas via `_ACTIVATION_RULES` (qui ne gère que actif/grisé, pas le changement d'icône).

## Ordre et visibilité — persistance et réordonnancement

### Drag & drop interne (glisser une icône pour réordonner)

`IconGrid._calc_insert()` (ligne ~510) calcule la position d'insertion par distance de Manhattan au centre de la cellule la plus proche, puis décide avant/après selon le quadrant du curseur dans la cellule. `IconGrid.dropEvent` → `IconToolbarQt._reorder_by_drop()` (ligne ~2057) modifie `self._layout` (liste d'ids, ordre d'affichage), sauvegarde en config, puis `_populate_grid()` reconstruit toute la grille.

Auto-scroll pendant le drag près des bords du `QScrollArea` : `IconGrid._update_auto_scroll()`/`_do_auto_scroll()` (ligne ~569-598), indépendant du calcul d'insertion.

### Fenêtre de configuration (`⚙`) — `_IconConfigDialog`

Non-modale (`setModal(False)` + `setWindowModality(Qt.NonModal)`, conforme à la règle CLAUDE.md n°4), deux panneaux (actives / masquées) avec sélection multiple + flèches ← → pour déplacer, boutons Réinitialiser/OK/Annuler. `_do_ok()` (ligne ~1393) applique `self._active_ids` à `tb._layout`, les checkboxes réglette/combo à `tb._show_thumb_slider`/`tb._show_lang_combo`, sauvegarde tout en config, puis `tb._populate_grid()`.

### Persistance — indépendante par panneau

Chaque panneau (1 et 2 en split-view) a ses propres clés de config, via `modules/qt/config_manager.py` :
- Panneau 1 : `icon_toolbar_layout`, `icon_size_index`, `show_thumb_slider`, `show_lang_combo` (méthodes `get_/set_` directes, ligne ~535-561).
- Panneau 2 : mêmes concepts sous `*_panel2` (ligne ~605-627), exposés au panneau 2 via un petit adaptateur (ligne ~693-715) qui redirige les mêmes noms de méthode (`get_icon_toolbar_layout`, etc.) vers les clés `_panel2` — **`IconToolbarQt` ne sait jamais si elle est panneau 1 ou 2**, elle appelle toujours les mêmes noms sur l'objet `config` qu'on lui a injecté.
- La **largeur** de la colonne (`buttons_column_width`/`buttons_column_width_panel2`, largeur du splitter interne du panneau, distincte du palier d'icône) n'est pas gérée par ce fichier : elle est lue/écrite par `session_restore_qt.py` (panneau 1) et `MosaicView.py::_open_split` (panneau 2), et contrainte par `PanelWidget._update_splitter_constraints()` (`panel_widget.py`). Voir piège dédié ci-dessous.

Il n'y a **aucune synchronisation automatique** entre les deux panneaux : personnaliser la colonne du panneau 1 ne change rien au panneau 2, et inversement. Si une tâche demande une synchro cross-panneaux, c'est un comportement nouveau à construire, pas un bug existant.

## Paliers de taille d'icône

```python
# ICON_SIZE_LEVELS — (taille px, colonnes max), ligne ~110
[(96, 3), (64, 4), (48, 5)]
```

Index 0 = grande (défaut), index 2 = petite. Boutons `[−]`/`[+]` du footer (`_decrease_icon_size`/`_increase_icon_size`, ligne ~1848) déplacent `_size_index` et appellent `_apply_size_change()` (ligne ~1858) : vide les caches pixmap, sauvegarde en config, recalcule `_cols` selon la largeur réellement disponible (pas seulement le max du palier — `adapt_cols_to_width()` ligne ~1922 fait ce recalcul aussi au redimensionnement du splitter, voir `panel_widget.py::_on_splitter_moved`), reconstruit la grille, restaure le focus clavier sur l'icône qui l'avait (par `icon_id`, pas par référence Qt directe — le widget peut avoir été détruit par `deleteLater()` entre-temps).

`FOOTER_SCALE_LEVELS = [1.0, 0.8, 0.65]` (ligne ~117) fait rétrécir en parallèle la réglette de vignettes et le combo langue du footer (`_apply_footer_scale()`, ligne ~1914) — volontairement pas strictement proportionnel au palier d'icône, juste assez pour rester lisible aux petites tailles.

**Ajouter un 4e palier** : ajouter un triplet à `ICON_SIZE_LEVELS` et une valeur à `FOOTER_SCALE_LEVELS` à la même position — tout le reste (bornes des boutons `[−]/[+]`, `_update_size_buttons()` ligne ~1910) lit dynamiquement `len(ICON_SIZE_LEVELS)`, pas de borne codée en dur à corriger ailleurs dans ce fichier.

## Footer — réglette vignettes et combo langue

Sous les boutons `[−][⚙][+]`, une ligne optionnelle (masquable indépendamment via les checkboxes de `_IconConfigDialog`) contenant `ThumbSizeSlider` (3 crans small/normal/large, Espace pour cycler — voir skill `mosaic-thumbnails` pour ce que ce slider pilote côté mosaïque) et `LanguageComboWidget` (liste déroulante des langues, coche ✓ sur la langue active, police spéciale appliquée par item pour les langues CSUR piqaD/Tengwar via `_LangComboDelegate` — voir skill `fonts` pour le mécanisme de sélection de police complet, dont ce combo est un cas particulier avec une taille fixe indépendante de l'offset global). Puis un séparateur et le copyright cliquable (`_FooterLabel`, ouvre la licence — voir skill `license-window` pour la fenêtre elle-même et les autres points d'entrée).

## Navigation clavier

Chaque widget focusable (icône, boutons `[−][⚙][+]`, slider, combo, copyright) implémente `keyPressEvent` avec ↑↓←→ pour circuler. `IconToolbarQt._navigate()` (ligne ~1771, grille d'icônes) et `_navigate_footer()`/`_navigate_footer_horiz()` (footer, ligne ~1734+) gèrent la transition entre les deux zones. Si un nouveau widget est ajouté au footer, il doit exposer un attribut `._toolbar` pointant vers l'`IconToolbarQt` parente (pattern déjà suivi par `_FooterBtn`, `_ThumbSlider`, `_LangCombo`, `_FooterLabel`) et déléguer à `_navigate_footer`/`_navigate_footer_horiz` sur ↑↓←→.

## Conformité aux règles UI Qt obligatoires (CLAUDE.md)

Avant de considérer un changement sur cette colonne comme terminé, vérifier explicitement :
- Thème sombre/clair : `set_hover_color()` et `set_slider_theme()` doivent être appelés au changement de thème (chercher les call sites dans `panel_widget.py`/`MosaicView.py`).
- Changement de langue à la volée : `update_language()` (ligne ~1963) retraduit label réglette + tooltips footer + copyright ; `_IconConfigDialog._retranslate()` (ligne ~1214) fait de même pour la fenêtre de config, connectée à `language_signal.changed` et déconnectée dans `_on_close()` (piège `deleteLater()` sans `finished` — voir CLAUDE.md règle UI n°2 : ce dialogue utilise `accept()`/`reject()` donc `finished` est bien émis, mais si un futur dialogue dérivé change ce comportement, revérifier).
- `_IconConfigDialog` est déjà non-modale, centrée via `_center_on_widget` dans `showEvent`, titre via `_wt()` — **modèle à copier**, mais noter le piège `showEvent` + `QTimer.singleShot(0, ...)` documenté dans CLAUDE.md (flash visible) : ce dialogue particulier l'utilise encore, ne pas le prendre comme référence pour le pattern de centrage sans flash, seulement pour la structure non-modale/traduction/thème.

## Pièges connus

- **Le layout stocké (`self._layout`) ne contient que des ids présents dans `self._defs`** — filtré à la lecture de la config (`[i for i in saved if i in self._defs]`, ligne ~1433) : si un id est renommé/supprimé d'`ICON_DEFINITIONS`, les configs utilisateur existantes qui le référencent l'oublient silencieusement plutôt que de crasher. Pas besoin de migration explicite.
- **Les caches pixmap (`_pm_cache`/`_pm_cache_gray`) sont indexés par `icon_id`** (et `f"split_ui_{png_key}"` pour le cas bi-état) — vidés uniquement au changement de palier de taille (`_apply_size_change`). Une icône PNG remplacée sur disque en cours de session ne sera pas rechargée sans redémarrage ou changement de palier.
- **`_reorder_by_drop` évite les no-ops** (insérer juste avant/après sa propre position ne fait rien, ligne ~2071) — sinon `_populate_grid()` se déclenche inutilement et fait perdre le focus clavier en cours de drag.
- Deux instances d'`IconToolbarQt` coexistent en split-view (une par `PanelWidget`), totalement indépendantes (pas de référence croisée) — toute modification doit être pensée pour fonctionner identiquement sur les deux, sans supposer qu'une seule existe.
- **Largeur de colonne sauvegardée (`buttons_column_width*`) pouvant dépasser le maximum courant** : cette largeur est indépendante du palier d'icône (`icon_size_index*`) et n'est jamais reclampée quand on change de palier en cours de session (`_apply_size_change()` ne touche que `_cols`/la grille, pas `_left_panel.setMaximumWidth()` — c'est `PanelWidget._update_splitter_constraints()`, appelé séparément via le callback `on_icon_size_changed`, qui fixe ce maximum). Si l'utilisateur élargit la colonne à un grand palier d'icône puis la réduit, la largeur restée en config peut dépasser le maximum du nouveau palier. Corrigé au redémarrage (`session_restore_qt.py::_restore` et `MosaicView.py::_apply_ratio`) en clampant la largeur restaurée à `_left_panel.minimumWidth()`/`maximumWidth()` juste après l'appel à `_update_splitter_constraints()` — sans ce clamp, `QSplitter.setSizes()` place le séparateur à la largeur sauvegardée alors que `_left_panel` refuse de dépasser son maximum, laissant un espace vide visible entre la colonne et le séparateur (bug corrigé en 1.6.4).

## Références croisées

- `menu-bar` — structure de callbacks parallèle (`build_menubar_callbacks`), sans configuration par panneau ni fenêtre de personnalisation ; nombreuses entrées dupliquées entre les deux (rotation, conversion, redimensionnement, aplatissement...).
- `keyboard-navigation` — `get_first_icon()`, point d'entrée de la colonne d'icônes dans le cycle TAB global entre zones (`ZoneTabNavigator`).
- `license-window` — `_FooterLabel`, widget copyright du footer qui ouvre la fenêtre de licence.
