---
name: tabs
description: Localiser ou modifier les onglets de MosaicView (onglet mosaïque + onglet Métadonnées, au-dessus du canvas de chaque panneau). Utiliser dès qu'une tâche touche à tabs_qt.py, TabBar, MetadataTab, metadata_signal/metadata_pages_signal, ou au tableau Pages.
---

# Onglets — MosaicView

Barre fine au-dessus du canvas de chaque panneau, avec deux onglets possibles : **mosaïque** (le canvas lui-même) et **Métadonnées** (affiché seulement si `state.comic_metadata` est non vide). Ce n'est pas un `QTabWidget` natif — une barre `QWidget`/`QHBoxLayout` custom (`TabBar`) au-dessus d'un `QStackedWidget` (`_content_stack`) qui bascule entre le canvas et l'onglet métadonnées.

## Fichier unique — `modules/qt/tabs_qt.py`

- **`TabBar`** — la barre d'onglets elle-même (boutons + bouton fermeture), hauteur fixe 22px.
- **`_TabButton`** / **`_CloseButton`** — boutons custom avec élision de texte dynamique et indicateur de focus clavier.
- **`MetadataTab`** — le contenu de l'onglet métadonnées (`QScrollArea`), avec sa section Pages en tableau.
- **`_PagesTableModel`** / **`_PagesModelBuilder`** — modèle de table et thread de construction pour le tableau Pages, avec une contrainte shiboken stricte (voir section dédiée).
- **`_SelectableLabel`** — `QLabel` sélectionnable avec menu contextuel Copier/Tout sélectionner, réutilisée pour chaque valeur de métadonnée.

## Intégration dans le panneau — `PanelWidget` (`panel_widget.py`)

Chaque `PanelWidget` possède sa propre paire `TabBar`/`MetadataTab`, construites ensemble (`panel_widget.py:451-479`) :

```python
self._tab_bar = TabBar(tooltip_parent=panel)
self._tab_bar._state = self._state          # lié au state du panneau dès la création
self._tab_bar.tab_changed.connect(self._on_tab_changed)

self._content_stack = QStackedWidget()
self._canvas = MosaicCanvas(self._state)
self._content_stack.addWidget(self._canvas)       # index 0 — mosaïque
self._metadata_tab = MetadataTab()
self._metadata_tab._state = self._state
self._content_stack.addWidget(self._metadata_tab) # index 1 — métadonnées
```

**Deux panneaux (panel1/panel2 en split-view) ont chacun leur propre `TabBar`/`MetadataTab`/`_content_stack`, totalement indépendants** — voir skill `panels`. `TabBar._state`/`MetadataTab._state` sont assignés explicitement au `self._state` du panneau propriétaire **plutôt que de lire le singleton global `modules.qt.state.state`** — ce pattern (state explicite au lieu du singleton) est ce qui évite qu'un panneau lise/affiche par erreur les métadonnées de l'autre panneau (voir skill `panels`, section singleton, pour le piège général que ce pattern contourne ici).

## `_on_tab_changed(tab)` — bascule entre les deux onglets

Slot connecté au signal `TabBar.tab_changed` (`"mosaic"` | `"info"`), `panel_widget.py:935` :
- **`"mosaic"`** : `_content_stack.setCurrentIndex(0)`, redonne le focus clavier au canvas — si aucun item n'a le focus (`_focused_idx is None`), sélectionne et scrolle vers le premier item de la mosaïque.
- **`"info"`** : `_content_stack.setCurrentIndex(1)`, focus sur `_metadata_tab`. Si l'onglet n'a encore jamais été peuplé (`not _field_widgets and not _toggle_btn` — cas du tout premier clic sur l'onglet après ouverture d'un fichier), déclenche `refresh()` en différé (`QTimer.singleShot(0, ...)`) plutôt qu'immédiatement — laisse le changement d'onglet (bascule du `QStackedWidget`) se stabiliser avant de construire tous les widgets de contenu.

## `_update_tabs()` — reconstruction de la barre elle-même

`panel_widget.py:949`, un seul appel : `self._tab_bar.update(close_callback=self._close_file, state=self._state)`. **Point d'entrée unique** pour rafraîchir la barre d'onglets (nom de fichier affiché, présence/absence de l'onglet Métadonnées) — appelé après :
- Ouverture/fermeture de fichier (`refresh_tabs` dans `_file_close_args`, voir skill `archive-image-loading`).
- Undo/redo, **seulement si l'état de `ComicInfo.xml` a changé** (voir skill `undo-redo`, `restore_state_qt` → `update_tabs_cb`, 3ᵉ callback du tuple `_undo_redo_callbacks()`).
- Import ComicVine terminé (`_on_comicvine_metadata_done`, voir skill `comicvine-metadata-fetch`), édition ComicInfo (voir skill `comicinfo-metadata-editor`).

**`TabBar.update()`/`_rebuild()` ne change pas l'onglet actif** — elle reconstruit juste les boutons visibles (le bouton Métadonnées apparaît/disparaît selon `st.comic_metadata`) en conservant `self._current_tab`. Si l'onglet Métadonnées était affiché et que `comic_metadata` devient vide entre-temps (ex. suppression de `ComicInfo.xml`), le bouton disparaît mais rien ne force explicitement un retour à l'onglet mosaïque — vérifier ce cas si un bug d'onglet "orphelin" est signalé.

## `TabBar` — construction des boutons (`_rebuild`)

Reconstruite **entièrement** à chaque appel (`update()`/`set_current_tab()`/changement de langue) — pas de mise à jour incrémentale des boutons existants, ils sont détruits (`deleteLater()`) et recréés à chaque fois :

- **Onglet mosaïque** : visible seulement si `st.current_file` est non vide. Nom affiché = `os.path.basename(st.current_file)`, avec **élision dynamique** (`_TabButton._update_elision`, `Qt.ElideRight`) recalculée à chaque `resizeEvent` de la barre — le nom complet est conservé dans `_full_text` pour le tooltip (`OverlayTooltip`, voir skill `qt-tooltips`) même quand le texte affiché est tronqué.
- **Bouton fermeture** (`_CloseButton`, croix rouge) : toujours accolé à l'onglet mosaïque, jamais indépendant.
- **Onglet Métadonnées** : visible seulement si `st.comic_metadata` est non vide (dict non vide, pas juste "existe") — voir skill `comicinfo-metadata-editor` pour ce qui peuple ce dict.
- **Navigation clavier** (`_navigate_horiz`, flèches ←/→) : cycle entre les boutons **visibles** de la barre (mosaïque → fermeture → métadonnées), pas de retour à la ligne, wrap circulaire (`% len(btns)`).
- **Tooltip** : un seul `OverlayTooltip` par `TabBar` (`self._overlay_tip`), instancié avec `tooltip_parent` = le **panneau** passé explicitement (pas la barre elle-même, 22px de haut, trop étroite pour contenir l'overlay) — voir skill `qt-tooltips`.

## `MetadataTab` — deux niveaux de mise à jour

Distinction volontaire documentée en tête de classe, à respecter pour toute nouvelle fonctionnalité de cet onglet :

- **`refresh()`** — reconstruction complète : détruit tous les widgets de contenu (`deleteLater()` sur chaque enfant du layout) et les reconstruit depuis `state.comic_metadata`. **Coûteux, à n'appeler que quand les données ont réellement changé** (nouveau fichier ouvert, ComicInfo.xml recréé/remplacé).
- **`_restyle()`** — mise à jour légère : réapplique couleurs/polices/textes traduits sur les widgets **existants**, sans rien reconstruire. Appelée au changement de thème (`apply_theme()`) et de langue (signal `language_signal.changed`).
- **`refresh_pages_only()`**/**`update_pages(pages)`** — mise à jour **encore plus ciblée** : ne touche qu'au tableau Pages (voir section dédiée), sans toucher aux autres champs de métadonnées ni reconstruire le reste de l'onglet.

### Contenu généré par `refresh()`

1. Bouton "Modifier le fichier ComicInfo.xml" (`_edit_btn`) — remonte l'arbre des parents Qt (`self.parent()` en boucle) jusqu'à trouver un objet avec `_edit_comicinfo` (le `PanelWidget`) et l'appelle. Ce pattern de "remontée d'ancêtres" évite un couplage direct entre `MetadataTab` et `PanelWidget` (le premier ne connaît pas le second par référence directe).
2. Bouton "Vérifier les mises à jour" (`_check_updates_btn`), **conditionnel** : visible seulement si `get_source_comicvine_issue_id(st.comic_metadata)` retourne un id (voir skill `comicvine-metadata-fetch`) — même pattern de remontée d'ancêtres vers `_check_comicvine_updates`.
3. Une ligne par champ non vide de `comic_metadata` (clé `pages` explicitement exclue, traitée à part) : label traduit (`metadata.{key}`) + valeur dans un `_SelectableLabel` — **sauf le champ `web`**, rendu comme un vrai lien cliquable (`setOpenExternalLinks(False)` + `linkActivated` filtré via `open_url()`, jamais `setOpenExternalLinks(True)` sur une donnée externe — voir règle CLAUDE.md sécurité n°1, ce champ est directement concerné puisque `Web` vient potentiellement d'un CBZ téléchargé).
4. Section Pages (tableau), si `comic_metadata['pages']` est présent — voir section dédiée.

## Le tableau Pages — pattern anti-crash shiboken

Section la plus délicate du fichier, avec une contrainte de conception explicitement documentée en commentaire dans le code — **ne jamais s'en écarter sans revalider soigneusement** :

- **`_PagesTableModel(QAbstractTableModel)`** — modèle de données pures (`list` de tuples `(valeur, clé_de_tri)`), **un seul wrapper Qt** pour tout le tableau. **Interdiction absolue de revenir à `QStandardItemModel`** : des centaines de `QStandardItem` (un par cellule) créés puis détruits à chaque rafraîchissement (chaque suppression/undo/redo qui republie l'onglet) corrompent la table de bindings de shiboken quand ça s'entrelace avec le churn massif d'items de la mosaïque (`render_mosaic()`, voir skill `mosaic-thumbnails`) — crash différé en access violation dans `BindingManager::releaseWrapper`, diagnostiqué par pile native (shiboken 6.10/6.11, juillet 2026).
- **`_PagesModelBuilder(QThread)`** — construit les lignes du tableau (conversion `ImageSize`/`ImageWidth`/etc. en valeurs triables) **dans un thread séparé**, mais ne construit **aucun objet Qt** dans ce thread — seulement des tuples Python purs (`str`/`int`). Les objets Qt réels (`_PagesTableModel`) sont assemblés uniquement sur le thread principal, dans `_on_pages_model_ready`. Même raison que ci-dessus : un `QStandardItemModel` construit hors thread principal puis détruit pendant que le thread principal crée/détruit en masse d'autres objets Qt provoque la même corruption.
- **`_on_pages_model_ready`** : `self._pages_builder.wait()` **avant** `deleteLater()` — le signal `done` est émis à la dernière ligne de `run()`, donc le thread tourne encore quelques microsecondes quand ce slot s'exécute ; un `deleteLater()` sans `wait()` risquerait de détruire le `QThread` pendant qu'il tourne encore (voir skill `undo-redo`/mémoire `project_qthread_lifecycle.md` pour la même classe de piège documentée ailleurs dans le projet).
- **`headers`/police** réévalués dynamiquement (`Qt.FontRole` retourne `_get_current_font(9)` à chaque `data()`) plutôt que fixés une fois — nécessaire pour suivre un changement de police (y compris les langues CSUR/klingon/tengwar) sans reconstruire tout le modèle.

**Toute nouvelle colonne/fonctionnalité sur ce tableau doit rester dans ce même pattern** (données pures dans le thread, assemblage Qt uniquement côté thread principal) — ne pas réintroduire de `QStandardItem`/`QTableWidgetItem` par cellule.

## Signaux globaux — `metadata_signal` / `metadata_pages_signal`

`modules/qt/metadata_signal.py` — deux signaux Qt **globaux** (module-level, pas par panneau) :
- **`metadata_signal.changed`** → connecté à `MetadataTab.refresh` (reconstruction complète). Émis par `write_comic_metadata_from_scraper()` (voir skill `comicvine-metadata-fetch`/`comicinfo-metadata-editor`) et par `sync_pages_in_xml_data(state)` quand `emit_signal=True` (valeur par défaut).
- **`metadata_pages_signal.changed`** → connecté à `MetadataTab.refresh_pages_only` (mise à jour ciblée du tableau Pages). Émis par `update_page_entries_in_xml_data()` (voir skill `comicinfo-metadata-editor`).

**Piège potentiel — signal global mais deux panneaux** : comme ces signaux sont globaux, **les deux `MetadataTab` (panel1 et panel2) sont connectés au même signal**. Un `metadata_signal.emit()` déclenché par une opération sur panel1 rafraîchirait donc aussi `MetadataTab` de panel2 s'il n'y prenait pas garde. **C'est pourquoi le code du drag & drop inter-panneaux (`panel_widget.py:2016,2037`, voir skill `drag-and-drop`) appelle explicitement `sync_pages_in_xml_data(state, emit_signal=False)`** puis rafraîchit **manuellement** le `_metadata_tab` du panneau concerné via `update_pages(...)` directement — contournement volontaire du signal global pour éviter une fuite cross-panel. **Tout nouveau code qui modifie `comic_metadata` d'un panneau spécifique doit suivre ce même pattern** (`emit_signal=False` + appel direct ciblé) s'il tourne dans un contexte où l'autre panneau ne doit pas être affecté.

## Interaction avec la renumérotation

Aucun lien direct — mais un `refresh_pages_only()`/`update_pages()` peut être déclenché **après** une renumérotation (voir skill `renumbering`, `_renumber_no_save` potentiellement asynchrone) pour que le tableau Pages reflète les nouveaux noms/attributs de page une fois la renumérotation terminée. Toujours enchaîner ce rafraîchissement en `on_done=`, jamais en séquence directe, pour les mêmes raisons documentées dans les skills `renumbering`/`page-merge`.

## Comment ajouter un nouveau champ affiché dans l'onglet Métadonnées

1. Le champ doit déjà exister dans le dict retourné par `parse_comic_info_xml()` (voir skill `comicinfo-metadata-editor`) — ce skill-ci n'ajoute rien à ce dict, il l'affiche seulement.
2. Ajouter la clé de traduction `metadata.{key}` dans tous les fichiers `locales/*.json` (voir skill `add-translation`) — sans elle, `refresh()` afficherait la clé brute non traduite comme label.
3. Aucune modification de code nécessaire dans `tabs_qt.py` pour un champ texte simple : `refresh()` itère déjà `st.comic_metadata.items()` génériquement (hors `pages`), un nouveau champ apparaît automatiquement s'il est non vide.
4. Pour un rendu spécial (comme le lien cliquable du champ `web`) : ajouter une branche `if key == 'nouveau_champ':` dans `refresh()` **et** répliquer le même traitement dans `_restyle()` si le rendu dépend du thème/de la langue (voir le bloc `if key == 'web':` dans les deux méthodes comme modèle).

## Comment ajouter un troisième onglet

Pas de mécanisme générique de liste d'onglets aujourd'hui (`TabBar` code en dur "mosaïque" + "métadonnées", `_content_stack` a exactement 2 index) — extension à prévoir avec soin :
1. Ajouter un `self._content_stack.addWidget(nouveau_widget)` (index 2) dans `PanelWidget.__init__`.
2. Ajouter la construction du bouton correspondant dans `TabBar._rebuild()`, avec sa propre condition de visibilité (sur le modèle du bouton Métadonnées).
3. Étendre `_navigate_horiz` (déjà générique via la liste filtrée des boutons non-`None`, devrait fonctionner sans modification si le nouveau bouton est ajouté à cette liste).
4. Étendre `_on_tab_changed` (`panel_widget.py`) avec une nouvelle valeur de `tab` et le `setCurrentIndex` correspondant.
5. Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour tout nouveau contenu d'onglet.

## Pièges connus

- **Ne jamais utiliser `QStandardItemModel`/`QStandardItem` pour le tableau Pages** — corruption shiboken différée, voir section dédiée. Toute nouvelle colonne doit passer par le même pattern données-pures-dans-un-thread.
- **`metadata_signal`/`metadata_pages_signal` sont globaux, pas par panneau** — tout code qui modifie `comic_metadata` d'un panneau spécifique dans un contexte multi-panneaux doit soit accepter que les deux `MetadataTab` se rafraîchissent, soit utiliser `emit_signal=False` + rafraîchissement manuel ciblé (voir pattern du drag & drop inter-panneaux).
- **`_PagesModelBuilder.wait()` est obligatoire avant `deleteLater()`** — omettre cet appel réintroduit le même risque de corruption mémoire que documenté dans `project_qthread_lifecycle.md` (mémoire projet) pour d'autres threads du projet.
- **`refresh()` est coûteux (reconstruction complète)** — ne pas l'appeler en boucle ou pour un simple changement de thème/langue ; `_restyle()` existe précisément pour ces cas.
- **Le bouton Métadonnées peut disparaître sans changer l'onglet actif** — si `comic_metadata` devient vide pendant que l'onglet info est affiché, rien ne force un retour automatique à la mosaïque ; vérifier ce cas avant de le considérer comme un bug à corriger sans le signaler.
- **`TabBar`/`MetadataTab` lisent `self._state` assigné explicitement, jamais le singleton `modules.qt.state.state`** — un nouveau code qui lirait le singleton par erreur dans ces classes casserait l'isolation entre panel1 et panel2 (voir skill `panels`).
