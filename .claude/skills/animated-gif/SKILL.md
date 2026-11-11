---
name: animated-gif
description: Localiser ou modifier la création/édition de GIF animé dans MosaicView (assemblage d'images en GIF en arrière-plan, ré-édition par extraction des frames, délai de chaque image et délai commun, boucle, question garder/remplacer après « Modifier »). Utiliser dès qu'une tâche touche à animated_gif_dialog_qt.py (AnimatedGifDialog, _ThumbWidget, _DelaySpin, _NumberSpin, _GifWorker, GifOriginalDialog), gif_writer.py (build_gif, frame_delay, next_gif_name, pages_left_out), is_animated_gif/get_gif_frame, aux menus "Créer un GIF animé" / "Modifier le GIF animé", ou à l'icône GIF animé de la colonne d'icônes.
---

# GIF animé — MosaicView

Fenêtre dédiée à deux usages qui se recouvrent dans une seule classe (`AnimatedGifDialog`, `modules/qt/animated_gif_dialog_qt.py`) : **créer** un nouveau GIF animé à partir de plusieurs images sélectionnées de la mosaïque, ou **ré-éditer** un GIF animé déjà présent (extraction de ses frames en images individuelles, modification, réassemblage). Les deux chemins convergent vers la même liste de travail interne (`self._gif_images`) et la même logique de création finale.

## Détection et lazy loading — `entries.py`, en amont de ce fichier

**Prérequis à comprendre avant de toucher à `animated_gif_dialog_qt.py`** : la détection d'un GIF multi-frame se fait dans `create_entry()` (skill `archive-image-loading`, `entries.py`), **pas** dans ce fichier. Au chargement d'un `.gif` avec `img.n_frames > 1` :

- `entry["is_animated_gif"] = True`, `entry["gif_frame_count"]` (nombre de frames, pas les frames elles-mêmes), `entry["gif_durations"]` (liste des durées en ms, une par frame), `entry["gif_loop"]`/`gif_disposal`/`gif_comment`/`gif_optimize` (métadonnées lues depuis `img.info`, `gif_optimize` étant une supposition faite par défaut à `True` plutôt qu'une valeur réellement détectée).
- **`entry["img"]` reste `None`** volontairement (lazy loading) — les frames individuelles ne sont chargées à la demande que via `get_gif_frame(entry, frame_idx)` (`entries.py`), qui vérifie `is_animated_gif`/`gif_frame_count`/`bytes` puis décode uniquement la frame demandée. Réutilisé aussi bien par la lecture (`ImageViewer`, skill `viewers`, mode lecture animée) que par ce dialogue d'édition.

Un GIF **statique** (une seule frame) n'a jamais `is_animated_gif = True` — traité comme n'importe quelle autre image du projet, hors périmètre de ce skill.

## Un GIF animé hors de cette fenêtre

Cette fenêtre reste le seul endroit où modifier le contenu d'un GIF animé. Ailleurs dans l'application :
Les WebP/PNG/AVIF animés (`is_animated_image`) suivent exactement les mêmes règles que les GIF animés ci-dessous (`image_ops.is_animated_entry`) ; seule cette fenêtre leur est fermée.
- **Conservent toutes les frames** (via `image_ops.transform_animated_entry`, skill `rotate-flip`) : rotation et miroir (mosaïque, visionneuse, macros), redimensionnement (skill `page-resize`), redressement automatique depuis la mosaïque (skill `page-straighten`), découpe (skill `page-split`), conversion vers WebP/PNG/AVIF (skill `image-format-conversion`). Les mêmes opérations conservent toutes les pages d'un TIFF de plusieurs pages (`is_multipage_tiff`, `image_ops.has_several_frames`, skill `apply-image-operation`), qui n'est pas pour autant traité en image animée.
- **Jointure de pages** (skill `page-merge`) : possible seulement entre images animées de même nombre de frames et de mêmes durées (`animations_joinable`, tous formats animés confondus), jamais avec une image fixe ; le résultat est animé, assemblé frame par frame (`merge_animated_2d`). Commandes grisées sinon.
- **Refusés** : les outils de la visionneuse qui ne traitent que la première frame, grisés avec une explication (skill `viewers`, `_ANIMATED_GIF_BLOCKED_TOOLS`) et refusés en lecture de macro (skill `macro-tool`).
- **Première frame seulement, par nature du format cible** : conversion vers JPEG/BMP/TIFF, et "GIF statique".
- Lecture animée : la visionneuse affiche toujours une image animée seule en mode double/continu, et la joue le nombre de fois prévu par le fichier (`entries.animation_play_count`, skill `viewers`).

## Les deux chemins d'entrée dans `AnimatedGifDialog.__init__`

Selon la composition de `selected_entries` reçue à la construction, deux branches radicalement différentes s'exécutent avant même l'affichage de la fenêtre (`AnimatedGifDialog.__init__`, bloc "Extraction des frames si GIF animé unique") :

### 1. Édition d'un GIF animé existant

Condition : `_is_single_animated_gif(selected_entries)` (une seule entrée, `is_animated_gif`). Dans ce cas :
- Les métadonnées d'origine (`gif_durations`/`gif_loop`/`gif_disposal`/`gif_comment`/`gif_optimize`) sont capturées dans `original_gif_metadata`, puis `selected_entries` est remplacé par les images extraites.
- **Extraction** : fonction de module `_extract_gif_frames(gif_entry)` → `(entrées, arrêtée)`, sur le thread de l'interface (un très long GIF fige l'application un moment à l'ouverture). Le GIF source est ouvert **une seule fois** (`Image.open` + `PIL.ImageSequence.Iterator`, pas une boucle `img.seek(i)` répétée, qui coûterait O(N²)) et chaque frame est extraite en une **nouvelle entrée PNG indépendante** (`{base_name}_frame_{i:03d}.png`, `is_image: True`, `is_animated_gif: False`, `from_gif_frame: True`, `gif_source: <nom du gif d'origine>`). `show_animated_gif_dialog` l'appelle **avant** de construire la fenêtre et lui passe le résultat (`AnimatedGifDialog(..., gif_extraction)` ; sans lui, `__init__` extrait lui-même).
- **Lecture arrêtée avant la fin** (`arrêtée` vrai : image qui ne se décode pas, ou mémoire insuffisante pour tout convertir en RGBA — un GIF tronqué, lui, est déjà refusé au chargement, page à cadre rouge, et ne propose pas « Modifier ») : les images lues sont gardées. Aucune image lue → la fenêtre ne s'ouvre pas, `MsgDialog` `dialogs.gif_animated.read_error_none` ; sinon elle s'ouvre, et un `MsgDialog` (parent : la fenêtre GIF, qui reste ouverte) affiche `read_error_partial` (`{read}` images lues sur `{total}` = `gif_frame_count`). **Pourquoi pas sauter l'image fautive** : Pillow lit un GIF dans l'ordre et ne peut pas atteindre les images suivantes (même `seek()` direct), et chaque image ne contient souvent que ce qui change par rapport à la précédente — les suivantes seraient fausses. **Piège** : avalée, l'erreur ouvrait la fenêtre avec une partie des images, et le GIF recréé était tronqué sans que l'utilisateur le sache. Testé par `tests/test_gif_frame_extraction.py` (fonction extraite par AST).
- **RGB si aucune image n'a de transparence**, RGBA sinon (toutes, `_same_mode_for_all`). **Piège** : en RGBA, Pillow réduit moins fidèlement les couleurs, et un GIF recréé sans rien changer perdait des couleurs (jusqu'à 23 niveaux sur 255 sur un dégradé) ; la case « Optimiser » était en plus ignorée (voir Réglages).
- Chaque entrée de frame porte sa durée d'origine (`gif_duration`, 0 compris), qui la suit si la liste est réordonnée ; voir « Délai de chaque image ». Le délai commun affiche la **moyenne** des durées d'origine, arrondie au centième de seconde (`_average_delay`, dans `self._delay_ms`).
- **Commentaire** : lu par `entries.gif_comment_text` (UTF-8, sinon codage Windows : un commentaire écrit par un ancien programme garde ses accents).
- **Boucle** : `gif_loop` (`entries.create_entry`) vaut `None` pour un GIF sans bloc de répétition, joué une seule fois — jamais remplacé par `0` (boucle infinie). Le réglage « Nombre de répétitions » (`0` = infini) ne sait pas représenter ce cas : il affiche `1`, et tant qu'il n'est pas modifié (`_loop_unset`), le GIF créé reste sans bloc de répétition (`loop` absent des paramètres de `save()`).

### 2. Création depuis plusieurs images sélectionnées

Sinon (plusieurs entrées sélectionnées, ou une seule qui n'est pas un GIF animé) : `selected_entries` est utilisé tel quel comme liste de frames de départ, valeurs par défaut (délai 100ms, boucle infinie `0`, disposal `2`, pas de commentaire, optimisation activée).

Dans les deux cas, la fenêtre travaille sur des **copies superficielles** (`e.copy()`) des entrées, indépendantes de `state.images_data` : travailler dans cette fenêtre (réordonner, supprimer une frame, changer un délai) ne touche jamais aux entrées réelles de la mosaïque. Une copie qui a des `bytes` (page de la mosaïque) reçoit `img = None` et décode sa propre image. **Piège** : une page déjà décodée (visionneuse ouverte) partagerait sinon son objet PIL avec sa copie, que la fenêtre ferme (`_free_gif_images`) : la page garderait une image fermée (`ValueError: Operation on closed image` dans la visionneuse). Les images extraites d'un GIF à modifier n'ont pas de `bytes` et gardent leur image.

- **`self._frames`** : toutes les images de la fenêtre ; **`self._gif_images`** : celles qui entreront dans le GIF. Chaque copie d'une page porte le jeton de sa page (`_page_key`, `undo_redo.entry_key`), `None` pour une image extraite.
- **La fenêtre suit les pages** (création depuis des pages seulement) : `_refresh_from_pages`, branchée par `state.add_document_listener` (skill `undo-redo`) et rappelée avant la création. Une page supprimée ou passée dans l'autre panneau quitte `_gif_images` et y revient à sa place si Annuler la rétablit (`_move_image` garde aussi sa place dans `_frames`) ; une page retouchée est relue (`_reload_frame`, qui garde son `gif_duration`). Sans effet pendant une création. La fenêtre « Modifier » ne suit pas le GIF d'origine : supprimé pendant qu'elle est ouverte, le nouveau GIF est quand même créé, et la question de fin n'a plus d'effet.
- **Mémoire** : la vignette d'une page relue depuis ses bytes libère son image (`_ThumbWidget._load_thumb`, `free_image_memory`) ; seule une image extraite d'un GIF reste décodée, n'existant qu'en mémoire.

## Réorganisation des frames — glisser-déposer horizontal

`_ThumbWidget` (source de drag, une vignette par frame : image 100×100 centrée, numéro, bouton de suppression, délai de l'image entre ses boutons - et +) et `_ThumbsContainer` (destination du drop, dessine un indicateur visuel d'insertion) :

- **Glisser venu d'une autre fenêtre GIF refusé** (`_ThumbsContainer._own_drag`) : le numéro transmis désignerait ici une autre image, ou aucune.
- **Défilement pendant un glisser** : près d'un bord de la partie visible (`_auto_scroll_step`, `_AUTO_SCROLL_MARGIN`), une minuterie fait défiler la liste, pour qu'une vignette puisse aller à une place hors de vue.
- **Seuil anti-clic-accidentel** de 5px de déplacement (`manhattanLength() < 5`) avant de démarrer un vrai `QDrag` — cohérent avec les seuils similaires documentés dans `page-crop`/`create-ico`.
- **Indicateur d'insertion** (`_ThumbsContainer.paintEvent`) : ligne verticale rouge avec triangles haut/bas, positionnée dynamiquement pendant `dragMoveEvent` selon la position x de la souris (`_insert_index_at`, compare au centre de chaque vignette plutôt qu'à ses bords).
- **Correction d'index lors d'un déplacement vers la droite** (`dropEvent`, `if src_idx < insert_idx: insert_idx -= 1`) — piège classique de réordonnancement de liste : retirer un élément avant sa nouvelle position décale tous les index suivants d'un cran, sans cette correction la frame atterrirait un cran trop loin.
- **Cache de vignette par entrée** (`entry["_thumb_px"]`) — évite de redécoder l'image PIL à chaque `rebuild()` (appelé après toute suppression/déplacement) ; nettoyé explicitement dans `_free_gif_images()` à la fermeture.
- **Suppression** (`_delete_image`) : bloquée si une seule frame reste (`if len(self._gif_images) > 1`) — impossible de vider complètement la liste depuis l'UI.

## Délai de chaque image et délai commun

- **Délai d'une image** : `entry["gif_duration"]` de sa copie ; `None` = le délai commun. `gif_writer.frame_delay(page, délai_commun)` est la seule règle, employée par l'affichage, les métadonnées et l'écriture.
- **Unité** : tout le code compte en millisecondes (valeur des champs, constantes `_DELAY_*`, `gif_duration`, `build_gif`, Pillow). Seuls les deux champs de délai (`_SecondsSpin`, dont hérite `_DelaySpin`) affichent et lisent des **secondes au centième** (`0,10 s`), seule précision qu'un GIF enregistre : `textFromValue` → `_seconds_text`, `valueFromText`/`validate` → `_delay_from_text` (virgule ou point acceptés, plus de deux décimales refusées, `_SECONDS_RE`), séparateur décimal de la langue par `utils.decimal_separator` (`QLocale` du code de langue ; langue fictive ou séparateur autre que virgule/point → point), relu à chaque affichage, donc suivi au changement de langue (`setSuffix` du `_retranslate` réaffiche le champ). **Piège** : en millisecondes, on pouvait taper un délai que le GIF ne sait pas enregistrer — Pillow tronque au centième (`int(durée / 10)`), 125 ms écrivait 120 ms sans le dire.
- **Sous chaque vignette** : `_DelaySpin` entre deux `QPushButton` « - »/« + » (`_step_delay`, pas `_DELAY_BUTTON_STEP` = 50 ms) ; flèches au pas `_DELAY_ARROW_STEP` = 10 ms (un GIF enregistre ses délais en centièmes de seconde). `show_delay` affiche le délai sans le compter comme un choix (`blockSignals`) ; `_on_delay_changed` l'écrit dans l'entrée et met à jour les métadonnées.
  - Plage élargie au délai de l'image (`_delay_range`) : un délai d'origine de 0 ou de 8000 ms s'affiche tel quel au lieu d'être ramené dans 20–5000, et reste intact tant qu'on n'y touche pas.
  - Police 11, marges et largeur de flèches identiques au délai commun : en police 9, le champ n'avait que 23 pixels de haut et ses flèches ne se voyaient plus. Vignette large de `THUMB_SIZE + 60` pour le champ et ses boutons ; hauteur du bandeau : 210 pixels.
  - La molette ne change le délai qu'après un clic dans le champ (`setFocusPolicy(Qt.StrongFocus)`, `wheelEvent`) : sinon, en parcourant la liste, elle changerait l'image passant sous la souris.
- **Délai commun** (« Appliquer un délai commun à toutes les images », clé `frame_delay_all_label`, `_SecondsSpin` de 20 à 5000 ms, soit 0,02 à 5,00 s, flèches à 10 ms, boutons - et + à 50 ms) : chaque changement (`AnimatedGifDialog._on_delay_changed`) remet `gif_duration` à `None` sur **toutes** les images de `_frames` (y compris une page absente que Annuler rétablirait) et rafraîchit les vignettes (`_ThumbsContainer.refresh_delays`). Revenir à l'ancienne valeur ne rend pas les délais individuels.
- **Entrée** dans un champ numérique de la fenêtre (`_NumberSpin.keyPressEvent`) valide le nombre sans déclencher le bouton par défaut « Créer » ; tout champ numérique de la fenêtre est un `_NumberSpin` (test `test_enter_in_a_number_field_does_not_create_the_gif`). Menu du clic droit : filtre commun de l'application (skill `qt-context-menus`).

## Réglages exposés

- **Délais** : voir la section précédente.
- **Label FPS dynamique** (`_update_fps_label`) — pour la valeur du délai commun : soit des FPS (`dialogs.gif_animated.fps_normal`, si délai `< 1000ms`) soit des secondes par frame (`fps_slow`, si délai `>= 1000ms`).
- **Nombres de la fenêtre** (champs de délai, label FPS, durée totale, fréquence, poids estimé) : écrits par `decimal_text(valeur, décimales, decimal_separator())` (`utils.py`, aussi employés par l'info-bulle de la mosaïque), avec le séparateur décimal de la langue — jamais un `f"{x:.2f}"` direct, qui écrirait toujours un point.
- **Boucles** (`QSpinBox`, 0 à 9999) — `0` signifie boucle infinie (convention GIF standard, transmise à PIL en tant que `loop=0`) ; cas du GIF joué une fois : voir section édition.
- **Disposal** (4 `QRadioButton`, valeurs 0-3) — méthode de disposition GIF standard entre chaque frame (comment le rendu précédent est traité avant d'afficher le suivant : ne rien faire, ne pas disposer, restaurer en fond, restaurer au précédent) ; textes traduits via une clé dynamique par bouton (`rb._key = f"dialogs.gif_animated.disposal_{i}"`).
- **Optimize** (`QCheckBox`) — transmis à `Image.save(..., optimize=...)`, réduction de palette/taille de fichier gérée par Pillow. **Ignoré quand des images sont transparentes** : avec `optimize`, Pillow rend opaques (blanches) les zones transparentes des images qui suivent la première.
- **Commentaire** (`QTextEdit`, encodé en UTF-8 à la sauvegarde si non vide) — métadonnée textuelle standard du format GIF, stockée dans le fichier final.
- **Panneau métadonnées en lecture seule** (`_update_metadata`) — dimensions de la première image (`_first_image_size` : sans la décoder ni la fermer, le thread de création pouvant la lire), nombre de frames, durée totale et fréquence calculées sur les vrais délais (`_delay_stats`), mention de palette, poids de fichier **estimé** (`num_frames * width * height * 0.5 / 1024`, approximation grossière, pas une mesure réelle).

## Création du GIF final

### Avant : `AnimatedGifDialog._create_animated_gif`

Sans effet si une création tourne déjà ou si une opération en arrière-plan (rotation…) tourne sur le document du panneau (`self._state.operation_running()`, skill `undo-redo`). Rappelle `_refresh_from_pages`, puis `gif_writer.pages_left_out` : pages illisibles (absentes du GIF) et pages à plusieurs images (seule la première reprise). S'il y en a, `ConfirmDialog` non modal (`left_out_title`, `left_out_unreadable`/`left_out_several`/`left_out_both`, 10 noms au plus, texte brut : noms venus de l'archive ; bouton `create_anyway`) avant `_start_creation`.

### Pendant : `_start_creation` et `_GifWorker`

La création tourne dans `_GifWorker` (QThread) : un GIF de grandes pages figeait les deux panneaux (20 secondes mesurées pour 30 pages de 1500×2200). Conventions communes (CLAUDE.md, « Workers asynchrones ») :
- `state.begin_operation(worker)` (commandes qui modifient le document grisées), `register_cancel_on_close(canvas, _cancel)` (fermeture du fichier ; d'où `"canvas"` dans `PanelWidget._animated_gif_callbacks`), `doc_generation` capturée et comparée à l'arrivée ; `_release_worker` (une seule fois) appelle `end_operation`, retire l'inscription et `dispose_qthread`.
- `run` → `_run` dans `try` / `except Exception: self.cancelled.emit(); raise` (garde-fou `tests/test_check_operation_end.py`, où `_GifWorker` et `on_created` sont déclarés).
- Le worker ne touche pas au document : il émet l'entrée prête (`create_entry`, `source_archive = "loose"`), ajoutée par la fenêtre sur le thread Qt. Erreur : `log_if_unexpected` puis `failed` avec `exception_text` (une `MemoryError` n'a pas de texte).
- **Annuler** pose `_cancelled` (la fenêtre reste ouverte) ; **la croix** aussi, puis la fenêtre se ferme à la fin réelle du thread (`_close_when_stopped`), le thread lisant ses images. Fenêtre figée pendant ce temps (`_set_creating`), progression dans `_progress_lbl` (`_on_gif_step`, texte gardé en lambda pour la retraduction).
- `_active_workers` garde une référence au QThread actif.

### Assemblage : `gif_writer.build_gif` (sans Qt)

`build_gif(pages, delay, loop, disposal, optimize, comment, on_step=None, should_stop=None)` → `(octets | None, pages écartées)`. Ne modifie jamais les pages reçues.
1. **En-têtes** (`_page_header`, sans décoder) : taille de la toile, transparence ; page illisible écartée.
2. **Une seule image décodée à la fois** : Pillow accepte un générateur pour `append_images`. Chaque image est centrée sur une toile blanche à la taille de la plus grande (`_page_canvas`, collage sans masque : ses zones transparentes le restent), RGBA si une image a de la transparence, RGB sinon.
3. **Une palette par image**, calculée par Pillow : une palette commune dégraderait les suivantes.
4. **Délais** : `frame_delay` de chaque page ; **loop** absent pour un GIF joué une fois resté inchangé ; `optimize` ignoré si une image est transparente (Pillow rendrait blanches ses zones transparentes) ; commentaire en UTF-8.
5. **Page lisible à l'en-tête mais pas au décodage** (`_PageUnreadable`) : création recommencée sans elle, les délais suivant la place des images. Signalée après coup (`page_left_out`, fenêtre « Fichiers ignorés »).
6. `should_stop()` vrai → `GifCancelled` (`expected_error`).

**Nommage** : `next_gif_name` — numéro suivant le plus grand `Animated_N.gif` du document (un simple comptage redonnait un nom déjà pris).

### Après : `_add_created_gif` et `_after_gif_added`

Nouvelle page à la fin du document, entre deux états d'historique (`save_state` avant et après), `modified`, `sync_pages_in_xml_data` (skill `comicinfo-metadata-editor`). Résultat abandonné si l'arrêt a été demandé ou si `doc_generation` a changé. La fenêtre se ferme ; la suite s'affiche sur le panneau.

**Après « Modifier »** : `GifOriginalDialog` (`QuestionWindowMixin`, croix et Échap = garder) demande de garder le GIF d'origine ou de le remplacer. `_replace_edited_gif` : le nouveau GIF prend la place, le nom, `source_archive` et les attributs `<Page>` (`transfer_page_attrs`) de l'original, entre deux états d'historique ; sans effet si le document a changé (génération, opération en cours, l'une des deux pages absente, retrouvées par leur jeton). Un premier Annuler ramène l'original, un second retire le nouveau.

## Fermeture et libération mémoire — `closeEvent`

Pendant une création, `closeEvent` arrête le thread et refuse la fermeture jusqu'à sa fin. Sinon `_free_gif_images()` ferme chaque `entry["img"]` PIL de `_frames` et vide le cache `_thumb_px`. La fenêtre hérite d'`AppWindowMixin` (skill `window-title`) : Échap suit le chemin de la croix.

## Points d'entrée UI

Quatre chemins, tous via `callbacks['show_animated_gif_dialog'](entries)`. Les conditions d'activation sont vérifiées côté appelant (au moment de construire le menu ou de rafraîchir l'état de l'icône), jamais dans `AnimatedGifDialog` lui-même.

1. **"Créer un GIF animé"** (`context_menu.image.create_animated_gif`) — menu Images (`_populate_images_menu`, `menubar_qt.py`), juste au-dessus de "Modifier le GIF animé" ; dans la barre et repris par le clic droit sur une vignette (`show_image_context_menu`, `context_menus_qt.py`) et sur une zone vide. Actif si la sélection contient **au moins 2 entrées `is_image`** (`gif_entries`/`can_create_gif`, les entrées non-images de la sélection sont ignorées et ne comptent pas). Transmet ces entrées images, dans l'ordre de la mosaïque (`sorted(selected_indices)`) → branche "création".
2. **"Modifier le GIF animé"** (`context_menu.image.edit_animated_gif`) — même menu, actif seulement si **exactement une** image sélectionnée et qu'elle est un GIF animé non corrompu (`single_entry` + `is_animated_gif` + extension `.gif` + `not is_corrupted`) → branche "édition". Entrée construite autour d'une `lambda` : non grisée pendant une opération en arrière-plan, mais sans effet.
3. **Dialogue de conversion de format** (`_ConvertFormatDialog._on_convert`, `conversion_dialogs_qt.py`, skill `image-format-conversion`) — choix "GIF animé (sélectionner plusieurs images)" (`dialogs.convert.format_gif_animated`), radio désactivé si une seule image → branche "création" avec les entrées images de la sélection.
4. **Icône `animated_gif` de la colonne d'icônes** (`icon_toolbar_qt.py`, skill `icon-toolbar`, `BTN_Animated_GIF.png`, hors `DEFAULT_LAYOUT` donc proposée parmi les icônes masquées de la fenêtre ⚙) — icône **bi-mode** qui couvre les deux commandes des menus : un seul GIF animé sélectionné (non corrompu) → branche "édition", sinon au moins 2 entrées `is_image` → branche "création", sinon grisée. La logique vit dans `build_icon_toolbar` : `_selected_animated_gif()` et `_gif_create_entries()` (exposées en `state_getters` `selected_animated_gif`/`gif_create_entries`, consommées par la règle `animated_gif` de `_ACTIVATION_RULES`) et `_animated_gif_action()` (callback du clic, teste l'édition en premier). Tooltip dynamique selon le mode : `tooltip_key` (`create_animated_gif`) / `tooltip_key_alt` (`edit_animated_gif`), résolu dans `IconLabel.enterEvent`.

**Conditions d'activation dupliquées sans source commune** : "au moins 2 images" à 3 endroits (`_populate_images_menu`, radio `GIF_ANIMATED` du dialogue de conversion, `_gif_create_entries` de la colonne d'icônes), "un seul GIF animé non corrompu" à 2 endroits (`_populate_images_menu`, `_selected_animated_gif` de la colonne d'icônes) — toute modification d'une règle doit être reportée partout. Les clics droits de la mosaïque reprennent `_populate_images_menu` et n'ont pas de condition propre.

Le callback `show_animated_gif_dialog` du dict de `build_menubar_callbacks` (`menubar_callbacks_qt.py`, partagé par la barre de menu, les clics droits de la mosaïque et la colonne d'icônes) accepte une entrée seule ou une liste (`[e] if not isinstance(e, list) else e`).

Callbacks (`PanelWidget._animated_gif_callbacks()`, `panel_widget.py`) : `parent`, `state`, `canvas` (arrêt de la création à la fermeture du fichier), `save_state`, `render_mosaic`, `update_button_text`. `AnimatedGifDialog.__init__` mémorise `self._state = callbacks.get("state") or _state_module.state` : c'est dans ce state que le GIF créé est ajouté (`images_data.append`, nom `Animated_N` de `next_gif_name`, `sync_pages_in_xml_data`). La fenêtre étant non modale, l'utilisateur peut cliquer dans l'autre panneau avant "Créer" — ce qui fait basculer le singleton global (skill `panels`) — sans que le GIF change de destination. Tout nouvel appelant doit passer le `state` de son panneau, le singleton n'est qu'un repli.

## Traductions

`locales/fr.json`, section `dialogs.gif_animated` : `window_title` (titre de la fenêtre, posé par `set_window_title`), `images_title`/`params_title`, `frame_delay_all_label`/`seconds_suffix`/`fps_slow`/`fps_normal`, `loop_label`/`loop_info`, `disposal_label`/`disposal_0`-`disposal_3`, `optimize`, `metadata_title`/`metadata_dimensions`/`metadata_frames`/`metadata_duration`/`metadata_fps`/`metadata_palette`/`metadata_size`, `comment_label`, `creating_progress`/`creating_normalizing`/`creating_saving` (textes de progression), `read_error_*` (extraction), `left_out_*`/`create_anyway`/`page_left_out` (pages écartées), `original_title`/`original_message`/`keep_original`/`replace_original` (question après « Modifier »). `seconds_suffix` reprend dans chaque langue l'abréviation de « seconde » de `metadata_duration` (klingon `lup`, sindarin et quenya `s`, transcrits en CSUR comme dans `metadata_duration`). Clés séparées : `context_menu.image.create_animated_gif`, `context_menu.image.edit_animated_gif`, `dialogs.convert.format_gif_animated` (dialogue de conversion), `messages.warnings.no_images_for_gif`/`no_valid_images`, `messages.errors.gif_creation_failed`, `buttons.create_gif`. Voir skill `add-translation`.

**Absent du mode d'emploi** (`user_guide_qt.py`) — même situation que `add-text-to-image` (skill `user-guide`).

## Comment étendre

- **Changer l'algorithme de normalisation des tailles** (actuellement centrage sur fond blanc) : `gif_writer._page_canvas`.
- **Changer la stratégie de palette** (actuellement une palette par image, calculée par Pillow à l'enregistrement) : `gif_writer._write_gif`. Une quantification faite par MosaicView doit garder la transparence (une image RGBA quantifiée par `convert("P", ADAPTIVE)` la perd).
- **Nouveau réglage** : le lire dans `_gif_settings`, le passer à `build_gif`, et l'ajouter aux widgets figés par `_set_creating`. Un nouveau champ numérique est un `_NumberSpin`.
- **Lecteur ou écrivain de fichier** : `build_gif` est passé par le fuzzing (`fuzz/fuzz_files.py`, cible `images`) ; une nouvelle fonction qui lit ou écrit un fichier de l'utilisateur s'y ajoute.
- Respecter les 9 règles UI Qt obligatoires du CLAUDE.md pour `AnimatedGifDialog` (non-modale déjà en place, barre de titre dessinée par Qt avec `set_window_title`, skill `window-title`, déjà en place).

## Tests

- `tests/test_animated_gif_creation.py` : `_gif_settings`, `_create_animated_gif` et `_add_created_gif` (extraites par `tests/ast_loader.load_methods`, réglages remplacés par des objets simples) avec le vrai `build_gif`, GIF relu : toile et centrage sur blanc, délai propre de chaque image (0 compris) ou délai commun, couleurs identiques après « Modifier », transparence, nombre de lectures, commentaire (codage Windows compris), noms `Animated_N`, pages écartées annoncées, page illisible au décodage, arrêt, rien pendant une opération ni sans page.
- `tests/test_animated_gif_window.py` : suivi des pages, libération des images des vignettes, délai commun et délai d'une image, boutons - et +, plage élargie, affichage et saisie en secondes, moyenne au centième, durée et fréquence, Entrée sans création, glisser et défilement, question après « Modifier », thread de création.
- `tests/test_gif_frame_extraction.py` : `_extract_gif_frames` (lecture arrêtée, RGB ou RGBA).
- `tests/test_viewer_gif_rhythm.py` : rythme de lecture dans la visionneuse (skill `viewers`).

## Pièges connus

- **Deux chemins d'entrée radicalement différents dans le même `__init__`** — toute modification du constructeur doit être testée dans les deux cas (édition d'un GIF existant vs création depuis une sélection multiple), pas un seul.
- **Le thread lit les images de la fenêtre** : rien ne doit les fermer ni les modifier pendant une création (`_first_image_size` ne décode ni ne ferme, `_refresh_from_pages` est sans effet, `closeEvent` attend la fin du thread).
- **Un délai n'est jamais lu par position** : il est rangé dans l'entrée de l'image (`gif_duration`), et suit donc un déplacement, une retouche (`_reload_frame`) et le retrait d'une page illisible pendant la création.
- **Délai commun : changer sa valeur efface tous les délais individuels**, sans retour arrière dans la fenêtre.
- **La suppression de frame est bloquée à 1 minimum**, pas 0 — ne pas supposer qu'une liste vide de frames est un état atteignable depuis l'UI.

## Références croisées

- `archive-image-loading` — détection `is_animated_gif`/`gif_frame_count`/`gif_durations` dans `create_entry()`, `get_gif_frame()` pour le lazy loading des frames, `create_entry()` réutilisé (contrairement à `create-ico`) pour la nouvelle entrée GIF créée.
- `viewers` — `ImageViewer` (visionneuse principale de lecture) a son propre mode de lecture animée qui consomme les mêmes `is_animated_gif`/`get_gif_frame`, mais pour l'affichage/lecture, pas l'édition (rythme et délais courts : section « Bouton play/pause ») ; aucun code partagé avec `AnimatedGifDialog`.
- `qt-context-menus` — menu du clic droit des champs numériques et des ascenseurs, remplacé pour toute l'application.
- `create-ico` — autre créateur de nouvelle entrée ; `create-ico` construit son entrée manuellement quand ce fichier réutilise `create_entry()`.
- `page-split` — même question « garder ou supprimer l'original » après l'opération, et même double Annuler.
- `undo-redo` — `begin_operation`/`end_operation`, `add_document_listener` (suivi des pages), états d'historique avant et après.
- `comicinfo-metadata-editor` — `sync_pages_in_xml_data` après insertion de la nouvelle entrée GIF, `transfer_page_attrs` au remplacement du GIF d'origine.
- `qt-context-menus` / `menu-bar` — entrées "Créer un GIF animé" et "Modifier le GIF animé" du menu contextuel et du menu Images.
- `image-format-conversion` — radio `GIF_ANIMATED`, troisième point d'entrée, même condition "au moins 2 images".
- `icon-toolbar` — icône bi-mode `animated_gif`, quatrième point d'entrée (création ou édition selon la sélection).
- `user-guide` — absence actuelle de section dédiée, à vérifier si une tâche touche à ce fichier.
- `nfo-editor` — autre créateur de nouvelle entrée du projet qui réutilise `create_entry()` (comme ce fichier, contrairement à `create-ico`), avec le même pattern à deux `save_state` (avant et après l'ajout).
- `save-export` — `_check_animated_gifs_qt` avertit avant sauvegarde CBZ que les lecteurs de BD n'afficheront que la première image d'une image animée ; le fichier écrit garde toute l'animation. Voir ce skill pour la chaîne de validation complète avant écriture.
