---
name: adjust-color-depth
description: Localiser ou modifier la fonction "Profondeur de couleur" (32/24/8/1 bit), migrée dans la barre d'outils de la visionneuse principale (color_depth_tool_qt.py). Utiliser dès qu'une tâche touche à settings['color_depth'], color_depth_tool_qt.py, ou aux radios de profondeur de couleur de la barre d'outils.
---

# Ajustement "Profondeur de couleur" — MosaicView

Fonction intégrée dans la barre d'outils de la visionneuse principale (skill `viewers`, section "Le cas de la profondeur de couleur"). Ce skill couvre la logique PIL et l'intégration dans la barre — pour l'orchestration générale de la barre (auto-masquage, undo/redo unifié, forçage mode simple page), voir skill `viewers`.

**Contrairement aux modes à réglette/pipette** (preview live + commit au relâchement), cette section est un groupe de `QRadioButton` avec un pattern de **verrouillage** : chaque clic sur une profondeur commit IMMÉDIATEMENT (pas de preview, pas de relâchement à attendre), puis ce radio devient coché ET grisé (non re-cliquable, puisque re-choisir la même profondeur ne changerait rien) — les 3 autres profondeurs restent cliquables pour changer directement vers une AUTRE profondeur. Un radio "Restaurer l'original" (`_restore_radio`) apparaît/s'active dès qu'au moins un changement a été fait, et restaure `entry['bytes']` à l'état d'avant le TOUT PREMIER changement de la session (pas un simple undo d'un cran, même après plusieurs profondeurs enchaînées) — nouveau commit, ne dépile pas l'historique.

## Où

- **UI (barre d'outils)** : `modules/qt/color_depth_tool_qt.py` — `_ColorDepthOptionsPanel` (panneau flottant, 2 lignes de radios + phrase d'info), `ColorDepthCanvasMixin` (vide, aucun geste souris/overlay), `ColorDepthViewerMixin` (`perform_color_depth(key)`, `perform_restore_color_depth()`, `_sync_color_depth_panel()`)
- **Icône de la barre** : `BTN_Color_Depth.png`, `tool_id="color_depth"` dans `viewer_toolbar_qt.py::_ViewerToolbar.__init__` — pas de bi-mode. L'icône elle-même n'est jamais grisée (contrairement à compression/transparency), mais certains des 4 radios de profondeur peuvent l'être individuellement selon le format d'origine — voir section "Profondeurs bloquées par format d'origine" plus bas.
- **Traitement PIL** : `image_processing_qt.py::apply_adjustments()`, bloc `# ── Profondeur de couleur ──` (fin de la fonction, après le mode d'image)

## Valeurs possibles (`settings['color_depth']`)

| Clé | Effet PIL |
|---|---|
| `unchanged` (défaut) | aucun changement |
| `32` | force `img.convert('RGBA')` |
| `24` | force `img.convert('RGB')` |
| `8` | si extension d'origine JPEG (`.jpg`/`.jpeg`/`.jfif`/`.pjpeg`/`.pjp`) → `img.convert('L')` (niveaux de gris, JPEG ne supporte pas la palette indexée) ; sinon → `img.convert('P', palette=Image.ADAPTIVE, colors=256)` (palette 256 couleurs adaptative) — reste réellement en mode `P`, **jamais** reconverti en RGB après coup (le choix de l'utilisateur ne doit jamais être silencieusement défait) |
| `1` | `img.convert('L').point(lambda p: 255 if p > 128 else 0, '1')` — seuil fixe à 128, noir et blanc pur 1-bit |

**Dépendance à `original_ext`** : le comportement de `'8'` change selon le format source — `apply_image_adjustments()` (`image_processing_qt.py`) injecte automatiquement `settings['original_ext']` depuis `entry['extension']`, `color_depth_tool_qt.py::perform_color_depth()` n'a pas besoin de le passer explicitement. Ne pas court-circuiter ce paramètre en testant le mode PIL actuel de l'image — c'est bien l'extension de fichier d'origine qui pilote la branche, pas le mode PIL courant.

**`for_preview`** : n'a pas d'usage réel côté barre d'outils (pas de preview live pour cet outil, chaque clic commit directement avec `for_preview` implicite `False`).

## Interaction avec le mode d'image

Ce réglage s'applique **après** le bloc "Mode d'image" (`settings['image_mode']`) dans `apply_adjustments()` — les deux peuvent donc se cumuler et le dernier (profondeur de couleur) a le dernier mot sur le mode PIL final. Voir skill `adjust-image-mode` pour l'autre réglage de mode (`image_mode_tool_qt.py`) — ne pas les confondre : "Mode d'image" cible le mode PIL exact (RGB/RGBA/L/LA/CMYK/1/P), "Profondeur de couleur" est une simplification grand public à 4 choix qui recouvre partiellement les mêmes modes PIL mais avec un vocabulaire différent (bits plutôt que noms de mode). Un commit `color_depth` peut donc, dans de rares cas, être immédiatement suivi d'un commit `image_mode` (ou l'inverse) — chacun via son propre outil de la barre, chacun sa propre entrée d'historique.

## Verrouillage des radios (`_ColorDepthOptionsPanel.sync_to_page_state`)

Le panneau flottant **recalcule l'état des 5 radios à chaque resynchronisation** (`ColorDepthViewerMixin._sync_color_depth_panel()`, appelée au changement de page, à la sélection de l'outil, après chaque commit/restauration, et après un undo/redo) — dérivé du mode PIL RÉEL de l'image affichée à l'instant T, pas d'une valeur mémorisée :

- **`locked_key`** (quel radio de profondeur apparaît coché+grisé) : `_PIL_TO_DEPTH.get(img.mode)` sur l'image courante — `RGBA→'32'`, `RGB→'24'`, `L→'8'`, `P→'8'`, `'1'→'1'`, `None` si le mode réel ne correspond à aucune des 4 profondeurs (LA, CMYK — jamais grisé dans ce cas).
- **`has_original_saved`** (active "Restaurer l'original") : `self.current_idx in state.color_depth_original_bytes_by_page`.
- **`pil_mode`** (phrase d'info, voir plus bas) : le mode PIL brut, transmis même quand `locked_key` est `None`.

**Piège — `QButtonGroup.blockSignals(True)` sur le groupe entier NE bloque PAS le signal `toggled` de chaque `QRadioButton` individuellement** : un `setChecked()` pendant la resynchronisation redéclencherait `perform_color_depth()`/`perform_restore_color_depth()` en boucle. `blockSignals(True)`/`False` doit être posé sur CHAQUE radio, pas seulement sur le groupe.

**`_restore_radio` n'appartient PAS à `self._group`** (le `QButtonGroup` des 4 profondeurs) et a `setAutoExclusive(False)` posé une fois pour toutes à sa création — `QRadioButton.autoExclusive` (défaut `True`) est une propriété PROPRE à chaque radio, indépendante d'un éventuel `QButtonGroup`, qui fait s'exclure mutuellement deux radios frères même hors groupe. Sans ce retrait, le tout premier `addButton()` d'un `QButtonGroup` exclusif coche automatiquement ce bouton, et aucun `setChecked(False)`/`setExclusive(False)` temporaire ne suffirait à le décocher durablement (l'auto-exclusivité du radio lui-même reprendrait la main). "Restaurer l'original" n'est de toute façon pas un choix de PROFONDEUR parmi d'autres — c'est une action "annuler tout" séparée, jamais censée coexister visuellement "sélectionnée" avec un radio de profondeur.

## Phrase d'info "Cette page est actuellement en {format}."

Sous les radios, en italique, couleur de texte du thème (jamais de couleur vive — CLAUDE.md "détails de style annexes"), **toujours visible** — sans elle, un radio grisé sans explication déroute l'utilisateur. Couvre TOUS les modes PIL rencontrables, pas seulement les 4 profondeurs de ce panneau : `_PIL_MODE_LABEL_KEYS` (`color_depth_tool_qt.py`) mappe RGB/RGBA/L/LA/CMYK/1/P vers les libellés déjà validés `dialogs.adjustments.image_mode_*` (skill `adjust-image-mode`, réutilisation mécanique plutôt que retraduction neuve). Pour LA/CMYK (aucun radio équivalent parmi les 4 profondeurs), la phrase affiche quand même le mode PIL brut, avec aucun radio grisé.

## Piège visuel — indicateur `::indicator` rempli à tort en état désactivé

Sur un panneau `WA_StyledBackground` (comme tous les panneaux flottants de cette barre), un `QRadioButton` stylé uniquement en `color`/`background` laisse l'indicateur natif (la puce ronde) invisible — voir skill `viewers`, `_CloneOptionsPanel`, `shapes_tool_qt.py`. `_apply_theme()` pose donc un style `QRadioButton::indicator` explicite. **Piège plus subtil** : un style qui remplit `QRadioButton::indicator:disabled` avec `background: theme['separator']` même quand le radio n'est PAS coché se lit visuellement comme "coché" quel que soit l'état logique réel — un cercle rempli d'une couleur pleine (même grise/neutre) trompe l'œil, indépendamment de ce que retourne `isChecked()`. L'état désactivé-non-coché doit rester **creux** (`background: theme['bg']`, identique à l'état activé-non-coché) — seul `:checked` (avec ou sans `:disabled`) remplit l'indicateur d'une couleur pleine (accent `#4a90d9`, cohérent avec le reste de la barre).

## Profondeurs bloquées par format d'origine

Jamais de conversion silencieuse du format de fichier : un choix qui produirait une perte silencieuse à la sauvegarde (transparence, palette) reste impossible à sélectionner plutôt que dégradé après coup. `_BLOCKED_DEPTH_KEYS_BY_EXT` (`color_depth_tool_qt.py`) liste, par extension d'origine, l'ensemble des clés de profondeur à griser :

| Extension | Profondeurs bloquées | Raison |
|---|---|---|
| `.jpg`/`.jpeg`/`.jfif`/`.pjpeg`/`.pjp` | `32`, `1` | JPEG ne supporte ni la transparence ni le 1-bit bilevel |
| `.gif` | `32` | GIF n'a qu'une transparence binaire (pas de canal alpha réel) |
| `.bmp` | `32` | Pillow écrit bien un canal alpha 32-bit en BMP, mais ne le redétecte pas à la relecture (header BMP classique ambigu sur la présence d'alpha, contrairement à `BITMAPV4HEADER`/`BITMAPV5HEADER` avec masques explicites) — transparence non fiable |

`_sync_color_depth_panel()` calcule `blocked_keys` (recherche dans ce dict) et `blocked_format_label` (l'extension réelle du fichier en majuscules, ex. `.jpg`→`JPG`, jamais un nom de format normalisé — un fichier `.jpg` doit toujours s'afficher comme `JPG`, jamais `JPEG`) à chaque resynchronisation. Un radio bloqué reste `setEnabled(True)` (sinon Qt cesse d'envoyer les événements souris et son tooltip ne se déclencherait jamais) : c'est `BlockableRadioButton.blocked` (`clone_tool_qt.py`, classe partagée) qui rejette le clic dans `mousePressEvent`, avant que Qt ne coche le radio — le grisage visuel passe par la property Qt `blocked` (sélecteur CSS `QRadioButton[blocked="true"]` dans `_apply_theme()`). Tooltip explicatif au survol (clé de traduction `viewer.color_depth_panel_blocked_format`, paramétrée par `{format}`) via `OverlayTooltip.track()` standard puisque le radio reste actif. `_blocked_format_label`/liste bloquée sont mémorisés sur `self` et rejoués par `retranslate()` (changement de langue) via `_update_blocked_tooltips()` — sans ça le tooltip resterait figé dans l'ancienne langue.

## Garde-fou de dernier recours — compression forcée en PNG

Dans `apply_image_adjustments()` (`image_processing_qt.py`), si le mode PIL résultant est `RGBA`/`LA`/`P`/`1` et l'extension d'origine est JPEG (`.jpg`/`.jpeg`/`.jfif`/`.pjpeg`/`.pjp`), l'extension de sauvegarde (et `entry['orig_name']`) sont mises à jour vers `.png` (JPEG ne supporte aucun de ces modes). Depuis le blocage UI ci-dessus, ce cas ne devrait normalement jamais se produire en pratique depuis la visionneuse — ce garde-fou reste un filet de sécurité pour tout autre chemin qui produirait la même combinaison (ex. une future macro rejouée sur un fichier créé avant ce blocage). Générique, partagé avec `adjust-image-mode` et `adjust-transparency` — ne pas le dupliquer.

## Snapshot "avant premier changement" — `state.color_depth_original_bytes_by_page`

Dict `{page_idx: bytes}` sur `state` (pas sur `ImageViewer`), capturé au premier clic sur une profondeur pour une page donnée, jamais écrasé tant qu'il existe (un enchaînement 32→24→8 bits garde le TOUT premier snapshot). **Doit survivre au changement de page ET à un Ctrl+Z/Ctrl+Y** (sinon il y a un risque de confusion pour l'utilisateur) : contrairement aux dicts `state.*_value_by_history_index` des autres modes d'ajustement (indexés par `(page, history_index)`, RESYNCHRONISÉS à chaque changement de page/undo-redo), celui-ci n'est jamais réinitialisé par `navigate()`/`_refresh_after_undo_redo()` — seul un clic sur "Restaurer l'original" retire l'entrée pour cette page précise. Voir skill `viewers` pour le détail des autres dicts par comparaison.

## Modifier cette fonction

Pour changer la formule d'une profondeur existante ou en ajouter une nouvelle : modifier uniquement le bloc `# ── Profondeur de couleur ──` de `apply_adjustments()` (`image_processing_qt.py`). Pour ajouter une nouvelle option dans la barre : ajouter le radio dans `_ColorDepthOptionsPanel.__init__` (`_depth_radios`, `_DEPTH_KEYS`, `_ROW_FOR_KEY` pour la répartition sur les 2 lignes), une entrée dans `_DEPTH_LABEL_KEYS` (+ clé de traduction `dialogs.adjustments.depth_xxx`, déjà existante et réutilisée pour les 4 profondeurs actuelles), et une entrée dans `_PIL_TO_DEPTH` si la nouvelle option correspond à un mode PIL détectable pour le verrouillage automatique. Pour bloquer une profondeur sur un nouveau format : ajouter/étendre une entrée dans `_BLOCKED_DEPTH_KEYS_BY_EXT` — vérifier d'abord (recherche web + test isolé hors app) ce que Pillow supporte réellement en écriture ET en relecture pour ce format avant de décider quoi bloquer, ne jamais supposer par analogie avec un autre format.

## Références croisées

- `viewers` — barre d'outils de la visionneuse, orchestration transversale (auto-masquage, undo/redo unifié, forçage mode simple page, mécanisme des boutons Valider/Annuler — non utilisés par cet outil, chaque clic est déjà un commit complet).
- `adjust-image-mode` — l'autre réglage de mode, appliqué juste avant celui-ci dans le pipeline, même modèle de panneau dans la barre d'outils, même mécanisme de blocage par format.
- `apply-image-operation` — pattern undo/redo respecté par `apply_image_adjustments()`.
- `clone-zone` — héberge `BlockableRadioButton`, la classe partagée utilisée pour griser un radio tout en gardant son tooltip actif.
- `qt-tooltips` — `OverlayTooltip`, tooltip de l'icône `color_depth` de la barre et des radios bloqués par format.
