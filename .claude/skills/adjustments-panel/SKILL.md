---
name: adjustments-panel
description: Localiser ou modifier le panneau "Ajustements d'image" de MosaicView (structure 3 colonnes, aperçu temps réel, flux Appliquer/Réinitialiser/Annuler). Utiliser dès qu'une tâche touche à AdjustmentsDialog ou adjustments_dialog_qt.py — pour une fonction de réglage précise, voir les skills adjust-*.
---

# Panneau "Ajustements d'image" — MosaicView

Ce skill couvre le **panneau/dialog lui-même** (structure, aperçu, flux d'application) — pas les fonctions d'ajustement individuelles. Pour la logique PIL d'une fonction précise (formule, plage de valeurs, cas limites), voir les skills dédiés : `adjust-color-depth`, `adjust-compression`, `adjust-sharpness` (netteté + netteté adaptative), `adjust-brightness-contrast`, `adjust-levels`, `adjust-transparency`, `adjust-image-mode`, `adjust-remove-colors`, `adjust-saturation`, `adjust-effects`.

## Où — fichiers concernés

| Fichier | Rôle |
|---|---|
| `modules/qt/adjustments_dialog_qt.py` | Le panneau lui-même : classe `AdjustmentsDialog`, point d'entrée public `show_image_adjustments_dialog(parent, callbacks)` |
| `modules/qt/adjustments_processing_qt.py` | Logique PIL pure (sans Qt) : `apply_adjustments()` (aperçu ET application réelle), `apply_image_adjustments()` (écrit dans `entry['bytes']`), `compute_auto_levels()`, `detect_jpeg_quality()` |
| `modules/qt/adjustments_viewers_qt.py` | La visionneuse plein écran ouverte par chaque bouton "Ajuster avec la visionneuse" — voir skill `viewers`, section `AdjustmentViewerDialog` |

## Quand — points d'entrée

Trois chemins ouvrent tous la même fenêtre via `show_image_adjustments_dialog(mw, mw._adjustments_callbacks())` :
- Menu Édition → "Ajustements d'image" (`menubar_qt.py`)
- Menu contextuel (clic droit sur une sélection, `context_menus_qt.py`)
- Icône dédiée de la colonne d'outils (`icon_toolbar_qt.py`, voir skill `icon-toolbar`)

Le point d'entrée refuse d'ouvrir (affiche un `MsgDialog` non-modal) si `state.selected_indices` est vide, ou si la sélection ne contient aucune image valide (`is_image`). Sinon, `AdjustmentsDialog(parent, selected_entries, callbacks)` est instancié avec la liste des entrées sélectionnées — **1 ou plusieurs images**, le panneau gère les deux cas (voir section Multi-image plus bas).

`callbacks` vient de `PanelWidget._adjustments_callbacks()` (`panel_widget.py:1573`) : contient `save_state`, `render_mosaic`, `state` — tous préfixés par un wrapper `_with_state()` qui bascule temporairement `modules.qt.state.state` sur l'état du panneau actif le temps de l'appel. C'est ce qui permet au panneau d'ajustements de rester agnostique du split-view (panel1/panel2) — voir skill `panels` pour le mécanisme général de callbacks par panneau.

## Comment — structure du panneau

`AdjustmentsDialog` est un `QDialog` non-modal (`Qt.Window` + `setModal(False)`), construit par `_build_ui()` en 3 colonnes dans une `QScrollArea` :

- **Colonne gauche** (`_build_left_column`) : Profondeur de couleur, Compression, Netteté, Netteté adaptative, Transparence
- **Colonne droite** (`_build_right_column`) : Luminosité et contraste, Niveaux noir/blanc, Mode d'image
- **Colonne aperçu** (`_build_preview_column`, largeur fixe 330px) : Aperçu, Suppression des couleurs, Saturation, Effets

Le placement dans une colonne donnée est **purement une question de mise en page** (équilibrer visuellement les 3 colonnes) — aucune relation avec un regroupement logique des traitements. Ne pas supposer qu'une section appartient "logiquement" à une colonne : `_grp_effects` par exemple est construit dans `_build_left_column` puis physiquement déplacé (`layout.addWidget(self._grp_effects)`) dans `_build_preview_column` — grep `self._grp_<section>` pour retrouver où un groupe donné est réellement instancié vs affiché si la disposition doit changer.

Chaque section est un `QGroupBox` avec son propre style thème (`_groupbox_style`), sa police (`_set_groupbox_font`), et pour la plupart un bouton "Ajuster avec la visionneuse" (`_open_viewer(mode)`, voir section dédiée plus bas). Les sections "Profondeur de couleur", "Mode d'image" et "Effets" sont des groupes de `QRadioButton` (`QButtonGroup`) plutôt que des réglettes.

### Désactivation intelligente selon le mode PIL courant

`_disable_current_mode_radios()` (appelé une fois à la construction) désactive le radio "Profondeur de couleur" et "Mode d'image" correspondant au mode PIL **déjà courant** des images sélectionnées, mais seulement si **toutes** les images sélectionnées partagent exactement le même mode PIL (`len(modes) == 1`). Objectif : empêcher de choisir une option qui ne changerait rien. Table de correspondance mode PIL → clé UI : `PIL_TO_DEPTH`/`PIL_TO_MODE` en début de méthode.

### Sections grisées selon le format d'origine

- **Compression** : n'a de sens que pour JPEG/WebP/AVIF (`_has_compressible`, détecté sur l'extension de chaque entrée). Si aucune image sélectionnée n'est compressible, toute la section est grisée (slider + bouton visionneuse désactivés, style de titre passé en gris).
- **Transparence** : n'a de sens que pour PNG/WebP/ICO/AVIF (`_has_transparent`). Section grisée si aucune image sélectionnée n'a de format supportant la transparence — voir aussi skill `adjust-transparency` pour le filtrage supplémentaire fait côté visionneuse.

## L'aperçu (colonne droite, 300×300)

`_update_preview()` est appelé après **chaque** changement de valeur (tous les handlers `_on_*_changed` finissent par l'appeler) et lit `self._original_preview_img` — **toujours la première image de la sélection uniquement** (`selected_entries[0]`, chargée une seule fois à la construction dans `__init__`). Avec une sélection multi-images, l'aperçu ne reflète donc que la première image, jamais les suivantes — c'est voulu (documenté par le label `preview_warning` sous la vignette, "L'aperçu peut différer du rendu final").

Pipeline de rendu de l'aperçu :
1. `_get_settings()` sérialise l'état courant du dialog en un `dict` unique (une seule fonction, partagée avec l'application réelle — voir plus bas)
2. `_apply_adjustments(original.copy(), settings, for_preview=True)` dans `adjustments_processing_qt.py` — **le flag `for_preview=True` reconvertit systématiquement le résultat en RGB/RGBA/L affichable par Qt**, même quand le mode/profondeur cible n'est pas nativement affichable (ex. mode `'1'` noir et blanc pur, ou `'CMYK'`) ; l'application réelle (sans ce flag) préserve au contraire le mode exact demandé
3. Si l'image source a un canal alpha, un damier gris (`_make_checkerboard_pil`, voir skill `viewers` section "Piège performance") est composé derrière le résultat — **redimensionnement d'abord, damier ensuite**, pour que la taille du carreau reste visuellement constante quelle que soit la résolution source
4. `_pil_to_qpixmap(img, max_size=300, is_bw=...)` convertit en `QPixmap` via un buffer PNG intermédiaire ; le rééchantillonnage est `NEAREST` en noir et blanc pur (préserve les bords nets), `LANCZOS` sinon

**`self._preview_pixmap_ref`** retient une référence forte sur le dernier `QPixmap` généré — sans elle, le pixmap serait éligible au garbage-collection Python alors que Qt le référence encore en interne, provoquant un crash différé. Ne jamais supprimer cette assignation en modifiant `_update_preview()`.

**Garde anti-signal-prématuré** : `_preview_lbl` est initialisé à `None` en tout début de `_build_ui()` avant la construction des widgets (les sliders émettent `valueChanged` dès `setValue()` initial pendant leur construction) ; `_update_preview()` retourne immédiatement tant que `_preview_lbl` est `None`. Ne pas retirer cette garde en réorganisant `_build_ui()`.

## `_get_settings()` — le contrat entre UI et traitement

Un seul dict, une seule fonction, utilisée à l'identique pour l'aperçu ET l'application réelle (voir `apply_adjustments()` / `apply_image_adjustments()` dans `adjustments_processing_qt.py`) — **toute nouvelle section du panneau doit ajouter sa valeur ici**, sinon elle sera visible dans l'UI mais invisible pour le rendu et l'application. Clés actuelles : `color_depth`, `brightness`, `contrast`, `compression_quality`, `initial_quality`, `effect`, `sharpness`, `threshold`, `black_point`, `gamma`, `white_point`, `remove_colors_intensity`, `saturation`, `image_mode`, `original_ext`, `transparency_type`, `transparency_tolerance`, `unsharp_radius`, `unsharp_percent`, `unsharp_threshold`.

## Le bouton "Ajuster avec la visionneuse" (`_open_viewer(mode)`)

Chaque section (sauf Profondeur de couleur, Mode d'image, Effets — pas de mode viewer dédié) a un bouton qui ouvre `AdjustmentViewerDialog` en plein écran pour affiner le réglage sur l'image à pleine résolution/zoom, plutôt que sur la vignette 300×300 de l'aperçu. Voir skill `viewers` (section "Les 8 modes de `AdjustmentViewerDialog`") pour le détail complet de cette fenêtre — ce skill ne couvre que l'échange de données entre le panneau et elle :

- `_open_viewer(mode)` capture un **snapshot** de `_get_settings()` et le passe par référence à `AdjustmentViewerDialog` — le viewer **mute ce même dict** au fil des réglages (pas de copie de retour explicite).
- `on_close` (callback appelé quand le viewer ferme via son bouton "Appliquer") relit les valeurs modifiées du dict `settings` et les réinjecte une à une dans les attributs `self._xxx` + les sliders du panneau (avec `blockSignals(True)` le temps de la resynchronisation, pour éviter une cascade de `_update_preview()`), puis referme le viewer et rafraîchit l'aperçu du panneau — **le viewer n'applique jamais lui-même aux bytes réels dans ce chemin**, il ne fait que proposer des valeurs qui remontent au panneau parent, qui applique au clic sur "Appliquer" du panneau principal.
- `on_cancel` est un no-op pour tous les modes sauf transparence (voir skill `adjust-transparency` — la transparence a un flux d'application propre, séparé du reste, car elle modifie les pixels directement plutôt que de produire des paramètres numériques).
- Le mode `levels` a un flux différent : voir skill `adjust-levels`.

## Le flux Appliquer / Réinitialiser / Annuler

- **Réinitialiser** (`_on_reset`) : remet tous les attributs internes et tous les widgets à leurs valeurs par défaut (bloque les signaux le temps de l'opération pour éviter N rafraîchissements d'aperçu), puis un seul `_retranslate()` + `_update_preview()` final.
- **Annuler** (`_on_cancel` / `reject()`) : ferme sans rien appliquer. Cas particulier — si le bouton "Ajustement automatique" (niveaux) a été cliqué, `reject()` restaure les valeurs de point noir/blanc d'avant le clic (`_pre_auto_black_point`/`_pre_auto_white_point`), pour qu'Annuler annule vraiment tout, y compris l'auto-niveaux. Si un traitement multi-image est en cours (`_progress_lbl` visible), le clic sur Annuler ne ferme pas immédiatement — il positionne `_cancel_requested = True`, lu par la boucle d'application au prochain tour (voir plus bas).
- **Appliquer** (`_on_apply`) : voir section suivante.

### Application réelle — image unique vs multi-image

Cas **une seule image** : appel direct à `_apply_image_adjustments(self._selected_entries, self._get_settings(), callbacks=self._callbacks)` — cette fonction gère elle-même tout le pattern undo/redo + invalidation caches (voir skill `apply-image-operation`), le panneau n'a rien d'autre à faire.

Cas **plusieurs images** : le panneau boucle lui-même sur `self._selected_entries`, appelant `_apply_image_adjustments([entry], settings, callbacks=callbacks_no_save)` **image par image**, avec `save_state`/`render_mosaic` retirés du dict de callbacks pour cette boucle (`callbacks_no_save`) — le panneau gère l'unique paire `save_state()`/`save_state(force=True)` lui-même, **avant/après la boucle entière**, pas à chaque image, pour ne produire qu'une seule entrée dans l'historique undo/redo pour tout le lot. `QApplication.processEvents()` est appelé à chaque itération pour garder l'UI réactive et afficher la progression (`_progress_lbl`, "Ajustement X/N").

**Annulation en cours de lot** : si `_cancel_requested` devient `True` pendant la boucle (clic sur "Annuler" pendant le traitement), la boucle s'arrête, et les entrées déjà traitées (`processed`) sont **restaurées** à partir d'un snapshot pris avant tout traitement (`orig_bytes`/`orig_thumbs`, capturé avant le premier appel — donc avant tout `save_state`, pour ne jamais polluer la pile undo d'un lot annulé). Aucun `save_state` n'a lieu dans ce chemin — la fenêtre reste ouverte, rien n'est historisé.

**Cas spécial "Ajustement automatique" + multi-image** : si `_pre_auto_black_point` n'est pas `None` (le bouton Auto a été cliqué dans le panneau, pas dans le viewer), chaque image du lot reçoit son **propre** calcul de point noir/blanc via `compute_auto_levels(entry['bytes'])` individuellement, plutôt que les valeurs figées de l'aperçu — voir skill `adjust-levels`.

## Références croisées

- `viewers` — la fenêtre `AdjustmentViewerDialog` ouverte par chaque bouton "Ajuster avec la visionneuse", et les 4 autres visionneuses plein écran de l'application.
- `apply-image-operation` — le pattern générique (undo/redo + invalidation caches) que `apply_image_adjustments()` respecte.
- `undo-redo` — mécanisme d'historique sous-jacent à `save_state`.
- `panels` — callbacks par panneau (`_with_state`), pourquoi le dialog reste agnostique de panel1/panel2.
- `comicinfo-metadata-editor` — `update_page_entries_in_xml_data()` est appelé après application pour resynchroniser les balises `<Page>` du `ComicInfo.xml` des entrées modifiées.
- Skills par fonction : `adjust-color-depth`, `adjust-compression`, `adjust-sharpness`, `adjust-brightness-contrast`, `adjust-levels`, `adjust-transparency`, `adjust-image-mode`, `adjust-remove-colors`, `adjust-saturation`, `adjust-effects`.

## Avant de modifier ce panneau

1. Section UI (disposition, style, grisage) → ce fichier `adjustments_dialog_qt.py` uniquement.
2. Logique de calcul PIL d'un réglage → `adjustments_processing_qt.py::apply_adjustments()`, jamais dupliquée dans le dialog ou le viewer.
3. Toute nouvelle section doit : (a) avoir sa clé dans `_get_settings()`, (b) être remise à sa valeur par défaut dans `_on_reset()`, (c) être retraduite dans `_retranslate()` avec `setFont`+`setText`, (d) être re-stylée dans `_apply_theme()` — les 8 règles UI obligatoires de `CLAUDE.md` s'appliquent intégralement à ce dialog malgré sa taille.
4. Respecter les règles UI générales (`CLAUDE.md`) : non-modale, thème dynamique, retraduction à la volée, `_wt()` pour le titre de fenêtre, tooltips via `OverlayTooltip` (déjà utilisé ici pour le tooltip du bouton "Ajustement automatique").
