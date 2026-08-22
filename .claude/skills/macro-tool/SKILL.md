---
name: macro-tool
description: Localiser et modifier l'outil "Macros" de la visionneuse principale (enregistrer/rejouer une séquence d'actions sur une ou plusieurs pages). Utiliser dès qu'une demande porte sur "les macros", "enregistrer une séquence", ou les icônes Enregistrer/Lire de la barre d'outils de la visionneuse.
---

# Outil "Macros" — MosaicView

Deux icônes de la barre d'outils flottante de la visionneuse principale — "Enregistrer" et "Lire" — permettent de capturer une séquence d'actions faites sur une page (crop, niveaux, clonage, ajustements couleur, etc.) et de la rejouer à l'identique sur une ou plusieurs autres pages, comme un petit traitement par lot. Voir skill `viewers`, section "Le cas des macros", pour l'intégration dans la barre (icônes `_ActionButton`, grisage réciproque, mode simple page).

## Les deux fichiers

| Fichier | Rôle |
|---|---|
| `modules/qt/macro_engine.py` | Module pur, sans dépendance Qt : stockage sur disque, validation de nom, dispatcher headless par outil, boucle de lecture |
| `modules/qt/macro_tool_qt.py` | `MacroCanvasMixin`/`MacroViewerMixin` (hérités par `_ViewerCanvas`/`ImageViewer`), les 4 fenêtres (`_MacroRecordDialog`, `_MacroNameDialog`, `_MacroReadDialog`, `_MacroReportDialog`), `_MacroReadLockOverlay`, point d'entrée public `read_macro_on_selection_qt` |

## Stockage — un fichier JSON par macro

`macro_engine.get_macros_dir()` → `%APPDATA%\MosaicView\macros\` (sous-dossier dédié, créé à la demande — voir skill `config-storage` pour le principe général). Chaque macro est `{"name": str, "description": str, "steps": [...]}`, écrite dans `<nom>.json`. `validate_macro_name()` rejette (jamais de filtrage silencieux) : nom vide, >30 caractères, caractères interdits Windows, noms réservés (`CON`, `PRN`, etc.), doublon insensible à la casse. `list_macros()` retourne `(macros, errors)` — un fichier `.json` illisible/mal formé est signalé explicitement à l'utilisateur (`dialogs.macro_read.corrupt_title`), jamais ignoré silencieusement.

Chaque étape est `{"tool": "crop", "params": {...}, "label_key": "macro.step_crop", "label_args": {...}}`. `label_key`/`label_args` sont résolus dynamiquement à chaque affichage (`_(label_key, **label_args)`) — jamais un texte déjà traduit stocké dans le JSON, pour qu'une macro échangée entre utilisateurs s'affiche dans la langue active de chacun (cohérent avec la règle CLAUDE.md de retraduction dynamique).

## Pixels absolus, jamais de conversion relative

Toutes les coordonnées/valeurs capturées (rectangle de crop, points de clonage, clics de transparence...) sont des pixels absolus fixes de l'image source — jamais recalculées à l'échelle de la page cible à la lecture. Seules exceptions : le redressement automatique (recalcule l'angle nativement à chaque page, `perform_auto_straighten`) et "Restaurer l'original" (restaure l'état de LA LECTURE en cours, pas un snapshot pris à l'enregistrement — voir `_apply_restore_step` plus bas). Si un rectangle/point ne rentre pas dans la page cible à la lecture, l'étape échoue pour cette page précise, jamais de clamp/recalcul silencieux.

## Enregistrement

`MacroViewerMixin.open_macro_record_dialog()` : force le mode simple page (`page_mode = "single"` + `display_image()`), initialise `self._macro_steps = []`, ouvre `_MacroRecordDialog` (liste live des étapes capturées). Chaque `perform_*` capturable appelle en toute fin de commit `self._macro_record_step(tool_id, params, label_key, label_args)` — no-op silencieux si `self._macro_recording` n'est pas actif, donc aucun coût en usage normal hors enregistrement.

- **Ctrl+Z/Ctrl+Y pendant l'enregistrement** : `ImageViewer._undo_and_refresh`/`_redo_and_refresh` appellent aussi `_macro_pop_last_step`/`_macro_redo_last_step` — la liste live suit l'historique undo/redo réel.
- **Navigation de page bloquée** (`navigate()` no-op si `_macro_recording`) : une macro s'enregistre sur une seule page de référence.
- **Bouton "Terminer"** (libellé du bouton `_MacroRecordDialog.btn_stop`, historiquement "Stop" — corrigé car trompeur, le bouton ouvre en réalité la sauvegarde) grisé tant qu'aucune étape n'a été capturée. Au clic : `_macro_stop_recording` ferme la fenêtre live et ouvre `_MacroNameDialog` (nom obligatoire + description optionnelle).
- **`_MacroRecordDialog` a `Qt.WindowStaysOnTopHint`** : passer la visionneuse en plein écran (F11/double-clic) pendant un enregistrement ne doit jamais la faire disparaître derrière — piège rencontré, `showFullScreen()` place sinon la visionneuse au-dessus de tout le bureau, y compris cette fenêtre non liée hiérarchiquement.
- **Compléter une macro existante** (`_macro_complete_existing`, bouton "Compléter" de `_MacroReadDialog`) : reprend un enregistrement dont les nouvelles étapes s'ajoutent à la suite des existantes dans le même fichier. Référentiel de page potentiellement différent d'un bloc d'étapes à l'autre — assumé, cohérent avec "pixels absolus partout".

## Lecture — visionneuse réelle, visible mais bridée

La lecture n'utilise jamais de traitement hors écran : une vraie `ImageViewer` est affichée, avec sa barre d'outils masquée et un widget transparent (`_MacroReadLockOverlay`, posé en enfant du canvas) qui avale tous les événements souris. `MacroViewerMixin._macro_set_locked_for_reading(True)` masque la barre et affiche l'overlay ; les raccourcis clavier capturés à l'ouverture (`_macro_lockable_shortcuts`) sont désactivés. Seule la croix de fermeture de la fenêtre reste utilisable.

`macro_engine.run_macro_on_entries(macro, entries, viewer, save_state_fn)` — boucle principale :

1. Force le mode simple page si besoin (double page fausserait toute coordonnée : le pixmap affiché serait celui de 2 pages combinées).
2. Pose le verrou visuel, affiche un overlay `labels.macro_preparing` sur le canvas (`show_canvas_text`/`hide_canvas_text`, skill `canvas-overlay-progress`) avec un `QApplication.processEvents()` immédiat — sans ça, la construction du viewer ou l'appel à `save_state_fn()` (potentiellement lents) laissent l'écran figé plusieurs secondes sans aucun retour visuel après le clic.
3. Un seul `save_state()` avant toute la lecture, un seul `save_state(force=True)` après — jamais un par page ni par étape (un seul cran d'undo/redo pour tout le lot, modèle repris de `deskew_selected_qt`).
4. Pour chaque page : positionne `viewer.current_idx`, `display_image()`, puis applique chaque étape via `apply_step_to_entry(viewer, step)`. Un échec arrête la macro **pour cette page seulement** (`break`), la lecture continue sur la page suivante.
5. Classe chaque page : `ok` (toutes les étapes appliquées), `failed` (0 étape appliquée — stocké `(page_name, failed_step)`), `partial` (au moins une mais pas toutes — stocké `(page_name, applied_count, failed_step)`).

`apply_step_to_entry(viewer, step)` (dispatcher central par `step["tool"]`) appelle le `perform_*`/`perform_*_step` correspondant avec `skip_history=True`. **Chaque `perform_*` capturable doit retourner explicitement `True`/`False`** — piège réel rencontré : plusieurs `perform_*` (`remove_colors`, `brightness`, `saturation`, `compression`, `sharpness`, `unsharp`, `levels`, `color_depth`/`restore_color_depth`, `effect`/`restore_effect`, `image_mode`/`restore_image_mode`, `rotate`, `flip`) ne faisaient jamais `return True`/`False` explicitement (juste un `return` nu en cas de garde-fou, aucun `return` en fin de `try`) — `apply_step_to_entry` recevait alors toujours `None`, interprété comme un échec par `if not apply_step_to_entry(...)`, qui arrêtait la macro dès la première étape même quand son effet visuel avait réussi. **Tout nouveau `perform_*` capturable par macro doit retourner `True` en fin de commit réussi et `False` sur chaque chemin d'échec, sans exception.**

### Garde-fous de format — vérifiés côté macro, pas seulement dans l'UI

Plusieurs outils grisent une option incompatible avec le format de la page dans l'UI (icône/radio désactivé), mais ce blocage vit uniquement côté widget — sans vérification explicite côté macro, une macro enregistrée sur un format compatible puis rejouée sur un format incompatible appliquait silencieusement l'opération, produisant un résultat dégradé (ex. transparence RGBA aplatie en fond blanc sur un JPEG à la sauvegarde) au lieu d'échouer proprement pour cette page :

- **`transparency`** : `perform_transparency_step` vérifie `is_transparency_supported_entry(entry)` (`_SUPPORTED_EXTS`, `transparency_tool_qt.py`) avant d'appliquer les clics.
- **`color_depth`**/**`image_mode`** : `apply_step_to_entry` vérifie `_BLOCKED_DEPTH_KEYS_BY_EXT`/`_BLOCKED_MODE_KEYS_BY_EXT` (dicts par extension, `color_depth_tool_qt.py`/`image_mode_tool_qt.py`) avant d'appeler `perform_color_depth`/`perform_image_mode`.
- **`compression`** : `perform_compression` vérifie déjà `is_compressible_entry(entry)` en interne, ce garde-fou couvrait donc le mode macro dès l'origine.
- **`effects`** : aucun garde-fou de format n'existe (aucun effet ne dépend d'un canal alpha ou d'un mode PIL particulier), rien à vérifier côté macro.

**Pour tout nouvel outil capturable qui grise une option selon le format de la page** : reproduire ce même contrôle explicite côté macro (dans `perform_*_step` ou dans `apply_step_to_entry`), ne jamais supposer que le blocage UI suffit à protéger la lecture headless.

### "Restaurer l'original" en lecture — restauration réelle, pas un snapshot d'enregistrement

`color_depth`, `effects` et `image_mode` ont chacun un bouton "Restaurer l'original" qui, en usage normal, restaure un snapshot pris avant le premier changement de CETTE session d'édition sur CETTE page (voir skill `viewers`). En lecture de macro, ce même geste doit restaurer chaque page cible à **son propre état d'avant la lecture en cours**, pas rejouer le snapshot capturé sur la page d'enregistrement (qui n'a aucun sens sur une page différente). `_apply_restore_step(viewer, tool)` (`macro_engine.py`) gère cette restauration réelle via `viewer._macro_read_page_start_bytes[current_idx]` (capturé par `run_macro_on_entries` avant la première étape de chaque page) — les trois `perform_restore_*` gagnent un paramètre `_skip_macro_capture` pour éviter de recapturer leur propre étape (déjà gérée par `_apply_restore_step`, pas par le `perform_*` rejoué).

## Rapport final — `_MacroReportDialog`

Fenêtre non-modale, redimensionnable (`resize(520, 320)`, pas de taille fixe), affichée à la fin de `_macro_run`. Compte les 3 catégories (`ok`/`partial`/`failed`) et liste, dans un `QScrollArea` qui grandit avec la fenêtre (`stretch=1`, pas de hauteur fixe), chaque page en échec/partiel avec le nom de la page et le libellé de l'étape qui a échoué (résolu dynamiquement via `_step_label`, jamais figé).

## Fermeture pendant une lecture — rollback complet

`ImageViewer.closeEvent` intercepte en priorité `_macro_reading` : ferme la fenêtre déclenche `macro_engine.rollback_macro_reading(viewer)`, qui restaure chaque page déjà touchée à son état d'avant la première étape de CETTE lecture (`_macro_read_page_start_bytes`) et dépile le `save_state()` "avant" (`pop_last_state`) sans jamais avoir committé le `save_state(force=True)` "après" — la lecture n'a jamais eu lieu du point de vue de l'undo/redo. Toujours un rollback complet, jamais partiel. Message d'interruption (`viewer.macro_read_interrupted_title`/`_message`) parenté au panneau (`_center_parent`), pas à `self` — la visionneuse est en train de se fermer.

## Visionneuse transitoire (lecture depuis la mosaïque)

`_MacroReadDialog` accepte soit un `viewer` déjà ouvert (bouton "Lire" de la barre, visionneuse déjà là), soit un `viewer_factory` (callable créant/affichant l'`ImageViewer` à la demande — cas mosaïque). **La visionneuse ne doit s'ouvrir qu'au clic effectif sur "Lire"/"Compléter", jamais au seul choix d'une macro dans la liste** : `_ensure_viewer()` n'invoque `viewer_factory()` qu'à ce moment, et `_MacroReadDialog.close()` est appelé **avant** cette construction potentiellement lente (pas après) — sinon la fenêtre "Lire" reste visuellement au premier plan par-dessus l'overlay de préparation pendant toute la construction, donnant l'impression d'un clic ignoré.

Un viewer créé via `viewer_factory` est marqué `_macro_read_transient_viewer = True`. Ce marqueur pilote deux comportements réservés à ce cas précis, absents quand le bouton "Lire" est cliqué depuis une visionneuse que l'utilisateur avait déjà ouverte lui-même :
- **`ImageViewer.closeEvent` ne sauvegarde aucun marque-page** (`_save_bookmark`) pour un viewer transitoire — sans ce garde-fou, toute lecture de macro depuis la mosaïque créait/déplaçait un marque-page comme effet de bord de la fermeture automatique de la visionneuse en fin de lecture.
- **`_macro_run` ferme automatiquement le viewer transitoire** juste avant d'afficher le rapport final (`self.close()`), et rafraîchit `self._toolbar.refresh_undo_redo_state()` avant cette fermeture — sans quoi la visionneuse restait ouverte après un lot mosaïque, et son bouton undo restait visuellement obsolète.

## 3 points d'entrée mosaïque — lecture sur sélection

`read_macro_on_selection_qt(parent, callbacks)` (`macro_tool_qt.py`), point d'entrée public unique, câblé aux 3 endroits obligatoires (voir skill `viewers` pour le principe général de ces 3 points) :

| Fichier | Entrée |
|---|---|
| `modules/qt/menubar_callbacks_qt.py` + `menubar_qt.py` | Menu Images |
| `modules/qt/context_menus_qt.py` | `show_image_context_menu`, après l'entrée de redressement automatique |
| `modules/qt/icon_toolbar_qt.py` | `ICON_DEFINITIONS`, condition d'activation `has_selected_images` |

Filtre `state.images_data[idx].get("is_image")` (et `not is_corrupted`) — un `.nfo` ou un fichier corrompu sélectionné avec le reste est ignoré silencieusement, comportement voulu et identique à `deskew_selected_qt`. **Piège d'index déjà rencontré et corrigé** : construire les paires `(idx, entry)` dans une seule boucle filtrée, jamais par `zip()`/indexation positionnelle séparée — une page corrompue/exclue décale sinon l'association entre l'index réel et l'entrée traitée (même piège existait dans `deskew_qt.py`, corrigé au même moment).

## Traductions

Section top-level `macro.*` (`macro.step_crop`, `macro.step_levels`, etc. — un `label_key` par outil capturable), `viewer.toolbar_macro_record_tooltip`/`_instruction`, `viewer.toolbar_macro_play_tooltip`/`_instruction`, `viewer.macro_read_interrupted_title`/`_message`, `context_menu.image.read_macro`, `tooltip.read_macro_selected`, `dialogs.macro_record.*`, `dialogs.macro_read.*` (incluant `report_failed_line`/`report_partial_line`, `no_macros`, `hint_record_from_viewer`, `btn_pick_file`/`pick_file_title`), `dialogs.macro_name_error.*`, `labels.macro_preparing`, `help.macro`/`help.macro_content` (mode d'emploi). Propagées aux 45 fichiers de `locales/` (39 langues naturelles + tlh/sjn/qya latin + 3 CSUR) — vocabulaire tlh/sjn/qya consigné dans les lexiques de référence en mémoire persistante (`reference_tlh_klingon_glossary`, `reference_sjn_sindarin_glossary`, `reference_qya_quenya_glossary`).

## Comment étendre

- **Rendre un nouvel outil de la visionneuse capturable par macro** : ajouter `self._macro_record_step(tool_id, params, label_key, label_args)` en toute fin de son `perform_*` (après le commit réel), ajouter un paramètre `skip_history: bool = False` si absent (propagé au(x) `save_state()` internes), s'assurer que la fonction retourne explicitement `True`/`False` sur tous les chemins, puis ajouter une branche dans `apply_step_to_entry` (`macro_engine.py`). Si l'outil grise une option selon le format de la page, reproduire ce contrôle dans `apply_step_to_entry` (voir section dédiée ci-dessus). Ajouter la clé `macro.step_xxx` dans `locales/fr.json` puis les 44 autres langues.
- **Outils à geste composé** (nécessitant plusieurs points/clics avant validation, comme le clonage/texte/formes/transparence/coller-image) : suivre le pattern `perform_xxx_step(params)` séparé de `perform_xxx()` — reconstruit l'état nécessaire (points, blocs, clics) sans jamais dépendre d'un geste souris réel, puis appelle en interne le même chemin de commit que l'usage manuel.

## Pièges connus

- **Ne jamais référencer `idees.txt` dans le code** (commentaires, docstrings) — règle générale du projet (CLAUDE.md), ce fichier de notes personnelles n'a aucune légitimité pour les utilisateurs finaux et ses entrées disparaissent dès qu'implémentées.
- **`perform_*` sans `return` explicite** : voir section "Lecture" ci-dessus — le piège le plus coûteux rencontré sur ce chantier, silencieux et difficile à diagnostiquer sans instrumentation (aucune exception levée, juste un arrêt prématuré de la macro).
- **Mode double/continu/webtoon pendant enregistrement ou lecture** : toute coordonnée capturée/appliquée suppose le mode simple page — forcé explicitement à l'entrée de `open_macro_record_dialog`/`run_macro_on_entries`, jamais via le mécanisme générique de `_ViewerToolbar.set_active_tool` (les macros n'ont pas de `tool_id` actif).
- **Fermer `_MacroReadDialog` après avoir déjà lancé `_ensure_viewer()`** (au lieu d'avant) fait disparaître le retour visuel de préparation derrière la fenêtre encore ouverte — toujours fermer le dialogue de choix avant tout traitement potentiellement lent qui suit.
