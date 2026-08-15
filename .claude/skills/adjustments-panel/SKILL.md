---
name: adjustments-panel
description: Localiser ou modifier le panneau "Ajustements d'image" de MosaicView (structure 3 colonnes, aperçu temps réel, flux Appliquer/Réinitialiser/Annuler). Utiliser dès qu'une tâche touche à AdjustmentsDialog ou adjustments_dialog_qt.py — pour une fonction de réglage précise, voir les skills adjust-*.
---

# Panneau "Ajustements d'image" — MosaicView

Ce skill couvre le **panneau/dialog lui-même** (structure, aperçu, flux d'application) — pas les fonctions d'ajustement individuelles. **Plus aucune fenêtre annexe ni bouton "Ajuster avec la visionneuse" désormais** (2026-08-16) : le panneau ne contient plus que 3 sections, chacune purement locale à ce dialog — Profondeur de couleur (`adjust-color-depth`), Effets (`adjust-effects`), Mode d'image (`adjust-image-mode`). Les 8 autres réglages qui vivaient ici (netteté/netteté adaptative, luminosité/contraste, saturation, suppression des couleurs, compression, niveaux noir/blanc, transparence) ont tous migré, un par un, dans la barre d'outils de la visionneuse principale (skill `viewers`) — leurs skills dédiés (`adjust-sharpness`, `adjust-brightness-contrast`, `adjust-saturation`, `adjust-remove-colors`, `adjust-compression`, `adjust-levels`, `adjust-transparency`) documentent leur logique PIL, mais plus rien de leur UI ne concerne ce fichier.

**CES 3 SECTIONS RESTANTES ONT ELLES AUSSI VOCATION À MIGRER** (confirmé explicitement par l'utilisateur le 2026-08-16, voir `idees.txt` point 3 et skill `viewers` section "3 fonctions restantes") — ce panneau n'est pas un état stable définitif, c'est ce qu'il reste du chantier de fusion des visionneuses (idees.txt #3) avant qu'il ne soit réellement clos. Une fois ces 3 fonctions migrées, `AdjustmentsDialog`/`adjustments_dialog_qt.py` pourra disparaître entièrement à son tour. Aucune UI cible n'est encore tranchée pour cette dernière étape (panneau flottant à radios vs autre approche, pertinence d'un preview live, une icône par fonction ou un regroupement) — ne pas l'improviser, voir `idees.txt` pour les points ouverts.

## Où — fichiers concernés

| Fichier | Rôle |
|---|---|
| `modules/qt/adjustments_dialog_qt.py` | Le panneau lui-même : classe `AdjustmentsDialog`, point d'entrée public `show_image_adjustments_dialog(parent, callbacks)` |
| `modules/qt/adjustments_processing_qt.py` | Logique PIL pure (sans Qt) : `apply_adjustments()` (aperçu ET application réelle), `apply_image_adjustments()` (écrit dans `entry['bytes']`), `compute_auto_levels()`, `detect_jpeg_quality()` — ces deux dernières restent le moteur de calcul partagé des outils `levels_tool_qt.py`/`compression_tool_qt.py` de la barre, bien que leur UI n'ait plus rien à voir avec ce panneau |

## Quand — points d'entrée

Trois chemins ouvrent tous la même fenêtre via `show_image_adjustments_dialog(mw, mw._adjustments_callbacks())` :
- Menu Édition → "Ajustements d'image" (`menubar_qt.py`)
- Menu contextuel (clic droit sur une sélection, `context_menus_qt.py`)
- Icône dédiée de la colonne d'outils (`icon_toolbar_qt.py`, voir skill `icon-toolbar`)

Le point d'entrée refuse d'ouvrir (affiche un `MsgDialog` non-modal) si `state.selected_indices` est vide, ou si la sélection ne contient aucune image valide (`is_image`). Sinon, `AdjustmentsDialog(parent, selected_entries, callbacks)` est instancié avec la liste des entrées sélectionnées — **1 ou plusieurs images**, le panneau gère les deux cas (voir section Multi-image plus bas).

`callbacks` vient de `PanelWidget._adjustments_callbacks()` (`panel_widget.py:1573`) : contient `save_state`, `render_mosaic`, `state` — tous préfixés par un wrapper `_with_state()` qui bascule temporairement `modules.qt.state.state` sur l'état du panneau actif le temps de l'appel. C'est ce qui permet au panneau d'ajustements de rester agnostique du split-view (panel1/panel2) — voir skill `panels` pour le mécanisme général de callbacks par panneau.

## Comment — structure du panneau

`AdjustmentsDialog` est un `QDialog` non-modal (`Qt.Window` + `setModal(False)`), construit par `_build_ui()` en 3 colonnes dans une `QScrollArea` :

- **Colonne gauche** (`_build_left_column`) : Profondeur de couleur seule désormais — Netteté et Netteté adaptative ont été entièrement retirées de ce panneau (2026-08-14, skill `adjust-sharpness`), Compression aussi (2026-08-15, skill `adjust-compression`), Transparence enfin (2026-08-16, skill `adjust-transparency`, dernière des 8 sections d'ajustement à migrer), toutes vivent désormais uniquement dans la barre d'outils de la visionneuse principale. Profondeur de couleur elle-même a vocation à migrer à son tour — voir plus haut.
- **Colonne droite** (`_build_right_column`) : Mode d'image seul désormais — Luminosité et contraste ont été entièrement retirées de ce panneau (2026-08-14, skill `adjust-brightness-contrast`), Niveaux noir/blanc aussi (2026-08-15, skill `adjust-levels`), même destination
- **Colonne aperçu** (`_build_preview_column`, largeur fixe 330px) : Aperçu, Effets — la Saturation et la Suppression des couleurs ont elles aussi été entièrement retirées de ce panneau (2026-08-14, skills `adjust-saturation`/`adjust-remove-colors`), même destination que netteté/luminosité-contraste/niveaux/transparence

Le placement dans une colonne donnée est **purement une question de mise en page** (équilibrer visuellement les 3 colonnes) — aucune relation avec un regroupement logique des traitements. Ne pas supposer qu'une section appartient "logiquement" à une colonne : `_grp_effects` par exemple est construit dans `_build_left_column` puis physiquement déplacé (`layout.addWidget(self._grp_effects)`) dans `_build_preview_column` — grep `self._grp_<section>` pour retrouver où un groupe donné est réellement instancié vs affiché si la disposition doit changer.

Chaque section est un `QGroupBox` avec son propre style thème (`_groupbox_style`), sa police (`_set_groupbox_font`). **Plus aucune section n'a de bouton "Ajuster avec la visionneuse"** depuis le retrait de Transparence (2026-08-16, dernière section à en avoir un) — `_open_viewer(mode)` a été supprimée avec elle, voir section dédiée plus bas. Les 3 sections restantes ("Profondeur de couleur", "Mode d'image", "Effets") sont toutes des groupes de `QRadioButton` (`QButtonGroup`) plutôt que des réglettes.

### Désactivation intelligente selon le mode PIL courant

`_disable_current_mode_radios()` (appelé une fois à la construction) désactive le radio "Profondeur de couleur" et "Mode d'image" correspondant au mode PIL **déjà courant** des images sélectionnées, mais seulement si **toutes** les images sélectionnées partagent exactement le même mode PIL (`len(modes) == 1`). Objectif : empêcher de choisir une option qui ne changerait rien. Table de correspondance mode PIL → clé UI : `PIL_TO_DEPTH`/`PIL_TO_MODE` en début de méthode.

**Plus aucune section grisée selon le format d'origine** depuis le retrait de Transparence (2026-08-16, seule section de ce panneau dont la disponibilité dépendait du format — `_has_transparent` a été retiré avec elle). L'équivalent existe désormais côté barre d'outils de la visionneuse (icône `transparency` grisée selon le format de la page affichée, skill `adjust-transparency`), mécanisme entièrement séparé de celui-ci.

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

Un seul dict, une seule fonction, utilisée à l'identique pour l'aperçu ET l'application réelle (voir `apply_adjustments()` / `apply_image_adjustments()` dans `adjustments_processing_qt.py`) — **toute nouvelle section du panneau doit ajouter sa valeur ici**, sinon elle sera visible dans l'UI mais invisible pour le rendu et l'application. Clés actuelles : `color_depth`, `effect`, `image_mode`, `original_ext` — les 3 sections restantes de ce panneau. **`sharpness`/`unsharp_radius`/`unsharp_percent`/`unsharp_threshold` (skill `adjust-sharpness`), `brightness`/`contrast` (skill `adjust-brightness-contrast`), `saturation` (skill `adjust-saturation`), `remove_colors_intensity` (skill `adjust-remove-colors`), `compression_quality`/`initial_quality` (skill `adjust-compression`), `threshold`/`black_point`/`gamma`/`white_point` (skill `adjust-levels`) et `transparency_type`/`transparency_tolerance` (skill `adjust-transparency`, dernières clés retirées, 2026-08-16) ne font plus partie de ce dict** depuis le retrait des sections correspondantes — `apply_adjustments()` continue d'accepter les premières (valeurs par défaut neutres si absentes) mais n'a jamais eu besoin de traiter les 2 dernières (la transparence a toujours eu son propre chemin d'application, jamais celui-ci) ; ces clés sont désormais fournies par `sharpness_tool_qt.py`/`brightness_tool_qt.py`/`saturation_tool_qt.py`/`remove_colors_tool_qt.py`/`compression_tool_qt.py`/`levels_tool_qt.py`/`transparency_tool_qt.py`, jamais ce panneau.

## `_open_viewer(mode)` — SUPPRIMÉ (2026-08-16)

Ce panneau ouvrait autrefois `AdjustmentViewerDialog` en plein écran (bouton "Ajuster avec la visionneuse") pour affiner un réglage sur l'image à pleine résolution/zoom plutôt que sur la vignette 300×300 de l'aperçu. La dernière section à en disposer était Transparence — une fois migrée dans la barre d'outils de la visionneuse principale (2026-08-16, skill `adjust-transparency`), plus aucune section de ce panneau n'a de mode viewer dédié, et `_open_viewer()` a été supprimée avec elle. `AdjustmentViewerDialog`/`adjustments_viewers_qt.py` n'existe plus du tout — voir skill `viewers`.

## Le flux Appliquer / Réinitialiser / Annuler

- **Réinitialiser** (`_on_reset`) : remet tous les attributs internes et tous les widgets à leurs valeurs par défaut (bloque les signaux le temps de l'opération pour éviter N rafraîchissements d'aperçu), puis un seul `_retranslate()` + `_update_preview()` final.
- **Annuler** (`_on_cancel` / `reject()`) : ferme sans rien appliquer. Si un traitement multi-image est en cours (`_progress_lbl` visible), le clic sur Annuler ne ferme pas immédiatement — il positionne `_cancel_requested = True`, lu par la boucle d'application au prochain tour (voir plus bas). **Le cas particulier "Ajustement automatique" (niveaux) a disparu avec la section elle-même** (2026-08-15, skill `adjust-levels`) — `reject()` n'a plus rien à restaurer.
- **Appliquer** (`_on_apply`) : voir section suivante.

### Application réelle — image unique vs multi-image

Cas **une seule image** : appel direct à `_apply_image_adjustments(self._selected_entries, self._get_settings(), callbacks=self._callbacks)` — cette fonction gère elle-même tout le pattern undo/redo + invalidation caches (voir skill `apply-image-operation`), le panneau n'a rien d'autre à faire.

Cas **plusieurs images** : le panneau boucle lui-même sur `self._selected_entries`, appelant `_apply_image_adjustments([entry], settings, callbacks=callbacks_no_save)` **image par image**, avec `save_state`/`render_mosaic` retirés du dict de callbacks pour cette boucle (`callbacks_no_save`) — le panneau gère l'unique paire `save_state()`/`save_state(force=True)` lui-même, **avant/après la boucle entière**, pas à chaque image, pour ne produire qu'une seule entrée dans l'historique undo/redo pour tout le lot. `QApplication.processEvents()` est appelé à chaque itération pour garder l'UI réactive et afficher la progression (`_progress_lbl`, "Ajustement X/N").

**Annulation en cours de lot** : si `_cancel_requested` devient `True` pendant la boucle (clic sur "Annuler" pendant le traitement), la boucle s'arrête, et les entrées déjà traitées (`processed`) sont **restaurées** à partir d'un snapshot pris avant tout traitement (`orig_bytes`/`orig_thumbs`, capturé avant le premier appel — donc avant tout `save_state`, pour ne jamais polluer la pile undo d'un lot annulé). Aucun `save_state` n'a lieu dans ce chemin — la fenêtre reste ouverte, rien n'est historisé.

**Le cas spécial "Ajustement automatique" + multi-image a disparu avec la section Niveaux** (2026-08-15) — l'outil "niveaux" de la visionneuse (skill `adjust-levels`) n'agit que sur la page affichée, jamais sur un lot ; ce panneau n'a donc plus besoin de ce branchement.

## Références croisées

- `viewers` — la visionneuse principale et sa barre d'outils, où vivent désormais les 8 modes d'ajustement qui ont quitté ce panneau, plus les 4 autres outils "macro" qui n'ont jamais fait partie de ce panneau (crop/straighten/clone/texte) et l'outil "formes" (neuf).
- `apply-image-operation` — le pattern générique (undo/redo + invalidation caches) que `apply_image_adjustments()` respecte.
- `undo-redo` — mécanisme d'historique sous-jacent à `save_state`.
- `panels` — callbacks par panneau (`_with_state`), pourquoi le dialog reste agnostique de panel1/panel2.
- `comicinfo-metadata-editor` — `update_page_entries_in_xml_data()` est appelé après application pour resynchroniser les balises `<Page>` du `ComicInfo.xml` des entrées modifiées.
- Skills par fonction : `adjust-color-depth`, `adjust-image-mode`, `adjust-effects` — les 3 seules sections restées dans ce panneau. `adjust-sharpness`, `adjust-brightness-contrast`, `adjust-saturation`, `adjust-remove-colors`, `adjust-compression`, `adjust-levels` et `adjust-transparency` documentent la logique PIL de 7 outils qui ont tous quitté ce panneau pour la barre d'outils de la visionneuse (skill `viewers`).

## Avant de modifier ce panneau

1. Section UI (disposition, style, grisage) → ce fichier `adjustments_dialog_qt.py` uniquement.
2. Logique de calcul PIL d'un réglage → `adjustments_processing_qt.py::apply_adjustments()`, jamais dupliquée dans le dialog ou le viewer.
3. Toute nouvelle section doit : (a) avoir sa clé dans `_get_settings()`, (b) être remise à sa valeur par défaut dans `_on_reset()`, (c) être retraduite dans `_retranslate()` avec `setFont`+`setText`, (d) être re-stylée dans `_apply_theme()` — les 8 règles UI obligatoires de `CLAUDE.md` s'appliquent intégralement à ce dialog malgré sa taille.
4. Respecter les règles UI générales (`CLAUDE.md`) : non-modale, thème dynamique, retraduction à la volée, `_wt()` pour le titre de fenêtre, tooltips via `OverlayTooltip` (déjà utilisé ici pour le tooltip du bouton "Ajustement automatique").
