---
name: renumbering
description: Localiser ou modifier la renumérotation des pages (noms de fichiers) dans MosaicView — 3 modes OFF/auto/simple, détection des pages doubles/triples, repositionnement des non-images. Utiliser dès qu'une tâche touche à renumber_mode, renumber_pages_auto, ou page_detection.py.
---

# Renumérotation des pages — MosaicView

La renumérotation réécrit `entry["orig_name"]` de toutes les entrées `is_image` d'un panneau pour leur donner des noms séquentiels cohérents (ex. `001.jpg`, `002-003.jpg`, `150---153.jpg`), tout en conservant les non-images (ComicInfo.xml, dossiers) à leur place alphanumérique naturelle. C'est indépendant du **tri** (voir `sort_images_qt` — la renumérotation ne trie jamais, elle renomme dans l'ordre déjà affiché).

## Fichiers impliqués

- **`modules/qt/renumbering.py`** — logique métier pure (aucun import Qt) : calcul des multiplicateurs, génération des noms, application aux entrées. Portée depuis l'ancienne version tkinter, gardée séparée de Qt exprès.
- **`modules/qt/renumbering_qt.py`** — couche Qt : dialogue non-modal `_FirstPageDialog` (choix "1ère page multiple") + deux points d'entrée `renumber_pages_auto_qt` / `renumber_pages_qt` qui adaptent la logique métier aux callbacks Qt (render, save_state, panneau cible).
- **`modules/qt/page_detection.py`** — `compute_reference_ratio()` / `compute_auto_multipliers()` : détection des pages multiples par ratio largeur/hauteur, indépendant de Qt et de la renumérotation (aussi utilisé par `pdf_loading_qt.py` et `batch_dialogs_qt.py` pour nommer des CBZ créés depuis un PDF, sans passer par le panneau).
- **`modules/qt/non_image_sorting.py`** — `reposition_non_images()` : réinsère les non-images à leur place alphanumérique naturelle après renumérotation des images.
- **`modules/qt/panel_widget.py`** — orchestration par panneau : `_renumber_pages_auto`, `_renumber_pages`, `_renumber_no_save`, `_renumber_btn_action`, `_toggle_renumber_mode`.
- **`modules/qt/state.py:67`** — `state.renumber_mode` (0/1/2), par défaut 1.
- **`modules/qt/config_manager.py:571-641`** — persistance du mode, séparée par panneau (`renumber_mode` / `renumber_mode_panel2`).
- **`modules/qt/icon_toolbar_qt.py`** — bouton "renumber" de la colonne d'icônes (clic gauche = agit, clic droit = change de mode).
- **`modules/qt/status_bar_qt.py:196-206`** — indicateur textuel du mode courant dans la barre de statut (cliquable, voir `set_renumber_click_callback`). Pour le mécanisme générique de la barre (layout, `refresh()`, tooltips, comment ajouter/modifier un indicateur) → skill `status-bar`.

## Les 3 modes (`state.renumber_mode`)

Persisté **par panneau** (`renumber_mode` pour le panneau 1, `renumber_mode_panel2` pour le panneau 2 — pas de synchro automatique entre panneaux, comme les autres réglages par panneau du projet).

| Valeur | Nom | Comportement |
|---|---|---|
| `0` | OFF | Aucune renumérotation automatique. Les déclenchements automatiques (drag & drop, merge, cross-panel) sont no-op. La renumérotation manuelle reste possible via le menu/bouton. |
| `1` | Auto (défaut) | Détection des pages doubles/triples via ratio largeur/hauteur (`compute_auto_multipliers`). Si la 1ère page est détectée comme multiple, ouvre `_FirstPageDialog` pour trancher (voir plus bas). |
| `2` | Simple | Numérotation séquentielle stricte, une image = un numéro, aucune détection de page multiple. |

Basculer le mode : clic droit sur l'icône "renumber" de la colonne d'icônes (`_toggle_renumber_mode`, cycle 0→1→2→0) ou clic sur l'indicateur de la statusbar (même callback). Le mode est réinitialisé à `1` à l'ouverture d'une session restaurée sans config explicite (`session_restore_qt.py:203`).

## Mode auto — détection des pages multiples

`compute_auto_multipliers(ratios)` dans `page_detection.py` :
1. `compute_reference_ratio(ratios)` calcule la **médiane des ratios portrait** (`0 < ratio < 1`) parmi toutes les pages — ratio de référence adaptatif au comic réel (pas un seuil fixe). Fallback `0.70` si aucune page portrait.
2. Pour chaque page, `mult = max(1, round(ratio / reference_ratio))` — une page deux fois plus large que la référence devient `mult=2` (double page), trois fois → `mult=3`, etc.

`generate_auto_filenames(multipliers, extensions, first_page_mode, first_page_total)` dans `renumbering.py` construit les noms :
- `mult == 1` → `"001.jpg"`
- `2 <= mult <= 4` → pages jointes par tiret : `"002-003.jpg"`, `"004-005-006.jpg"`
- `mult > 4` → notation intervalle avec triple tiret : `"010---015.jpg"` (évite un nom à rallonge pour un scan très large)
- Nombre de chiffres (`digits`) = `max(2, len(str(total_logical_pages)))`, calculé sur le **total de pages logiques** (somme des multiplicateurs), pas le nombre de fichiers.

## Dialogue "1ère page multiple" (`_FirstPageDialog`)

Ne se déclenche qu'en **mode auto** (1) et seulement si `first_mult > 1` (la toute première image du panneau est détectée comme double/triple page — typiquement une couverture + 4ᵉ de couverture scannées ensemble). Non-modal, centré sur le panneau source, avec vignette de la page concernée. Trois choix (`_on_ok` lit le bouton radio coché via `option_value`) :

- **`auto`** — traite la 1ère page comme les autres : nommée selon son multiplicateur réel (ex. `01-02.jpg` si double).
- **`joint`** — traite la 1ère page comme une **planche jointe** (couverture + dernière page reliées) : nommée `01-{dernière page}.ext` (ex. `01-156.jpg`), et compte pour **2 pages logiques** au lieu de son multiplicateur réel.
- **`exclude`** — exclut la 1ère page de la numérotation (`orig_name` inchangé, `filenames[0] = None` dans `generate_auto_filenames`) ; les pages suivantes démarrent à `01`.

Annuler le dialogue (`_on_cancel` / fermeture) → toute la renumérotation est abandonnée (`choice is None` remonte jusqu'à `renumber_pages_auto` qui `return` sans rien modifier, pas de `save_state()` appelé).

Le calcul de multiplicateurs pour proposer le dialogue est fait **avant** toute modification (`compute_first_page_info()`), pour permettre une UI non-modale : on doit pouvoir afficher l'état réel des pages puis attendre la réponse utilisateur de façon asynchrone (`ask_async` / callback), jamais bloquante.

## Repositionnement des non-images (`reposition_non_images`)

Après renumérotation des images, les entrées non-image (ComicInfo.xml, dossiers) doivent reprendre leur place naturelle dans l'ordre alphanumérique global (ex. `ComicInfo.xml` doit rester avant `001.jpg` s'il triait avant alphabétiquement). Algorithme (`non_image_sorting.py`) :
1. Trie l'ensemble des entrées (images renumérotées + non-images) par `orig_name` en tri naturel (`_natural_key`, insensible à la casse, "10" > "9") — c'est l'ordre de référence.
2. Reparcourt cet ordre : chaque image consomme le curseur `images` dans son **ordre relatif actuel** (jamais retrié entre elles), chaque non-image s'insère à la position dictée par le tri alphanumérique.

Résultat : les images gardent leur ordre relatif (celui voulu par la renumérotation/le drag & drop), seules les non-images se glissent autour.

## Renumérotation manuelle vs déclenchement automatique

Deux points d'entrée Qt dans `renumbering_qt.py`, tous deux **non-modaux et liés explicitement à un panneau** (`state=self._state`, jamais de swap du state global — garantit qu'une action dans un panneau ne touche jamais l'autre pendant qu'un dialogue non-modal est ouvert) :

- **`renumber_pages_auto_qt(parent_widget, canvas_render_func, save_state_func, state, on_done)`** — mode auto complet, peut ouvrir `_FirstPageDialog` (asynchrone, `on_done` appelé après résolution).
- **`renumber_pages_qt(canvas_render_func, save_state_func, state)`** — mode simple, synchrone, pas de dialogue.

`panel_widget.py` orchestre ces deux fonctions selon le mode courant :

- **`_renumber_pages_auto()` / `_renumber_pages()`** — appelées par le menu/bouton dédié quand l'utilisateur déclenche *explicitement* "Renuméroter" (auto ou simple), avec `save_state_func=self.save_state` (crée un point undo dédié). Bloquées si l'archive a une structure en sous-dossiers (`_has_subdirectory_structure()` → avertissement `_warn_flatten_required_renumber`, il faut aplatir d'abord).
- **`_renumber_no_save(on_done=None)`** — variante utilisée par les déclenchements **automatiques** après une autre opération qui a déjà son propre point undo (drag & drop, merge, cross-panel) : `save_state_func=None` pour ne pas créer un second point undo redondant. Respecte le mode courant (`0` = no-op immédiat, `1` = auto avec dialogue éventuel, `2` = simple). C'est la fonction à utiliser comme modèle pour tout nouveau déclenchement automatique.
- **`_renumber_btn_action()` / `_toggle_renumber_mode()`** — gèrent le bouton icône : clic gauche déclenche l'action du mode courant (no-op si OFF), clic droit fait tourner le mode (0→1→2→0) et persiste via `_renumber_config().set_renumber_mode()`.

### Points de déclenchement automatique (`_renumber_no_save`)

- **Drag & drop intra-panneau** (réordonnancement de vignettes) — `mosaic_canvas.py:1879`, via le callback `_renumber_after_drop_callback` (assigné par `MainWindow` après création du panneau).
- **Drag & drop cross-panel** (déplacement d'images d'un panneau vers l'autre) — `panel_widget.py:2027` (panneau source) et `:2052` (panneau cible), chacun avec sa propre finalisation (`_finalize_source`/`_finalize_target`) appelée en `on_done`.
- **Merge/join de pages** (`merge_dialog_qt.py:1031-1032`, voir skill `page-merge` pour le mécanisme complet) — seulement si `renumber_mode != 0` ; en mode OFF, comportement historique : la page fusionnée est insérée en tête sous son nom `Collage_xxx` sans renumérotation.

Dans tous ces cas, `state.needs_renumbering` (posé à `True` par exemple après un import CBR/CB7 par `archive_loader.py:943/1058` — voir skill `archive-image-loading` pour le chargement lui-même —, ou un import web `web_import_qt.py:305` — voir skill `web-import`) sert uniquement à **activer/désactiver le bouton icône** (`icon_toolbar_qt.py:155` : actif seulement si `needs_renumbering` et pas de sous-dossiers et mode ≠ OFF) et le menu contextuel (`context_menus_qt.py:275`, `menubar_qt.py:246`) — ce n'est pas ce flag qui déclenche la renumérotation automatique après drop/merge, ces déclenchements sont inconditionnels (sous réserve du mode).

### Import PDF → CBZ (cas particulier sans panneau)

`batch_dialogs_qt.py:1920` et `pdf_loading_qt.py` utilisent directement `compute_auto_multipliers` / `generate_auto_filenames` pour nommer les pages extraites d'un PDF en CBZ, **sans passer par `renumber_pages_auto_qt`** (pas de panneau ni d'AppState à ce stade, c'est un traitement batch en arrière-plan). Mode auto (`renumber_mode == 1`) → noms auto ; sinon → séquentiel simple. Pas de dialogue "1ère page multiple" dans ce chemin (traitement non-interactif).

## Modifier ou étendre la renumérotation

- **Ajouter un 4ᵉ mode** : étendre le dict `{0:1, 1:2, 2:0}` dans `_toggle_renumber_mode` (`panel_widget.py`), les clés de traduction `labels.renumber_indicator_*` / `tooltip.renumber_btn_*` (locales), et les branches `if mode == ...` dans `_renumber_no_save`/`_renumber_btn_action`. Vérifier aussi `merge_dialog_qt.py` et `batch_dialogs_qt.py` qui testent la valeur numérique du mode directement.
- **Changer le format des noms générés** (ex. séparateur, notation intervalle) : uniquement dans `generate_auto_filenames()` (`renumbering.py`) — fonction pure, testable sans Qt.
- **Changer la détection des pages multiples** (seuil, méthode) : uniquement dans `page_detection.py`. Attention : cette fonction est partagée avec le chemin PDF→CBZ (`batch_dialogs_qt.py`), toute modification impacte aussi ce chemin.
- **Ajouter un nouveau déclencheur automatique** : appeler `panel._renumber_no_save(on_done=...)` (jamais `renumber_pages_auto_qt`/`renumber_pages_qt` directement) pour hériter du respect du mode courant et de l'absence de double point undo. Toujours passer par le panneau concerné (`state=self._state` implicite via `self`), jamais swapper `modules.qt.state.state` global.
- **Dialogue `_FirstPageDialog`** : suit les 8 règles UI Qt obligatoires du `CLAUDE.md` racine (non-modal, thème, langue à la volée avec `setFont` dans `_retranslate`, `_wt()` pour le titre, centrage via `position_dialog_on_parent` avant `show()`). Toute modification de ce dialogue doit repasser cette checklist.

## Références croisées additionnelles

- `save-export` — `DuplicateFilenameDialog` appelle `renumber_btn_action` (callback fourni par le panneau) quand l'utilisateur choisit "renuméroter" pour résoudre des doublons de noms avant sauvegarde CBZ ; un chemin de renumérotation de plus, distinct des déclenchements automatiques listés ci-dessus.
- `pdf-loading` — après un chargement PDF réussi, `state.needs_renumbering = True` est posé (les pages PDF n'ont pas de nom de fichier d'origine porteur d'ordre) ; voir aussi la section "Import PDF → CBZ" ci-dessus pour le chemin batch sans panneau.
- `flatten-directories` — l'aplatissement de l'arborescence est un préalable obligatoire à la renumérotation quand l'archive a une structure en sous-dossiers (`_has_subdirectory_structure()` bloque sinon, voir "Renumérotation manuelle vs déclenchement automatique" ci-dessus).
