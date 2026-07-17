---
name: page-split
description: Localiser ou modifier la scission d'une page de MosaicView (découpe d'une image sélectionnée en N parties égales, horizontale ou verticale). Utiliser dès qu'une tâche touche à split_dialog_qt.py, SplitDialog, ou split_page.
---

# Scission de page — MosaicView

Découpe une **seule** image sélectionnée en N parties égales (2 à 10), horizontalement ou verticalement, et insère les N nouvelles pages juste après l'image d'origine. Usage typique : séparer une planche double/triple scannée en une seule image en pages individuelles — l'opération **inverse** de la fusion (voir skill `page-merge`), mais avec une différence structurelle importante : ici l'image source **reste en place** dans la mosaïque après l'opération, elle n'est pas remplacée ni supprimée.

## Fichier unique — `modules/qt/split_dialog_qt.py`

Tout le mécanisme tient dans un seul fichier (~360 lignes), pas de séparation UI/métier comme pour la fusion (`image_ops.py` vs `merge_dialog_qt.py`) — le découpage est assez simple (crop PIL en bandes égales) pour rester inline dans la fonction `split_page()`.

- **`SplitDialog`** — fenêtre de réglages (nombre de parties, direction), non-modale, taille fixe.
- **`split_page(parent, callbacks)`** — point d'entrée public : valide la sélection, ouvre `SplitDialog`, exécute la découpe si confirmée.

## Point d'entrée — `split_page(parent, callbacks)`

Câblé depuis trois endroits, tous vers `PanelWidget._split_page_callbacks()` (`panel_widget.py:1450`) puis `mw._split_page_qt` (`menubar_callbacks_qt.py:100`) :
- Menu contextuel canvas (`context_menus_qt.py:449`) et menu Fichier (`menubar_qt.py:218`).
- Bouton "Scinder la page" de la colonne d'icônes (`icon_toolbar_qt.py:71`, activé seulement si `has_selected_images() and selection_count() == 1` — voir skill `icon-toolbar`, `_ACTIVATION_RULES`).

**Dict `callbacks`** (`PanelWidget._split_page_callbacks()`) : `save_state`, `render_mosaic`, `update_button_text`, `state` — plus petit que le contrat de fusion (`page-merge`), pas de `clear_selection`/`renumber_no_save` ici (voir section renumérotation plus bas pour pourquoi).

### Garde-fous avant ouverture du dialogue

Trois vérifications séquentielles, chacune avec son propre `MsgDialog` distinct :
1. Aucune sélection → `messages.warnings.no_selection_split`.
2. Plusieurs entrées sélectionnées → `messages.warnings.multi_selection_split` (contrairement à la fusion qui *exige* au moins 2 sélections, la scission n'en accepte qu'**une seule**, exactement).
3. L'entrée sélectionnée n'est pas une image → `messages.warnings.invalid_selection_split`.
4. `ensure_image_loaded(entry)` échoue (image corrompue/illisible) → même message `invalid_selection_split` réutilisé — pas de message dédié pour ce cas précis.

## `SplitDialog` — réglages

Fenêtre simple à taille fixe (`420×280`), pas de canvas de disposition comme `MergeDialog` :
- **Nombre de parties** : `QSpinBox`, plage `2` à `10` (bornes UI **et** revalidées manuellement dans `_on_ok` — double vérification, la seconde protège contre une valeur qui contournerait le spinbox par un autre moyen). Hors bornes → `MsgDialog` `invalid_number_split`, le dialogue reste ouvert.
- **Direction** : `QButtonGroup` avec 2 `QRadioButton` (horizontal/vertical), **vertical coché par défaut** — cohérent avec le sens de lecture le plus courant des comics (planches doubles côte à côte, découpées verticalement en pages individuelles).
- **Avertissement** (`dialogs.split.warning`) — texte gris italique (couleur adaptée dynamiquement au thème, `#666666` clair / `#999999` sombre — calcul direct dans `_retranslate()`, pas via `theme['disabled']` comme documenté ailleurs dans le projet ; à harmoniser si une refonte de ce dialogue est demandée).
- `num_pages`/`direction` exposés en `@property` sur le dialogue après confirmation, lus par `split_page()` après le callback `ask_async`.

## La découpe réelle — dans `split_page()`, pas dans un module séparé

Contrairement à la fusion (logique dans `image_ops.py`), tout le calcul de découpe est **inline** dans la fonction `_on_confirmed` de `split_page()` :

1. `callbacks["save_state"]()` — **un seul appel**, **avant** toute modification (voir section undo/redo plus bas pour la particularité de ce point).
2. Dimensions lues depuis l'image déjà chargée (`img.size`, capturée par closure depuis la validation initiale — pas rerelue après `save_state`).
3. Format de sortie déduit de l'extension d'origine (`ext.upper()[1:]`, `JPG`→`JPEG` normalisé pour PIL) — **chaque partie garde le même format que l'image source**, pas de conversion.
4. **Découpe horizontale** (bandes empilées verticalement, séparées par des lignes horizontales) : `split_height = height / num_pages`, crop `(0, top, width, bottom)` pour chaque bande — malgré le nom "horizontale", c'est la **hauteur** qui est divisée (le résultat produit des pages qui se lisent de haut en bas, chacune sur toute la largeur).
5. **Découpe verticale** (bandes côte à côte, séparées par des lignes verticales) : `split_width = width / num_pages`, crop `(left, 0, right, height)` — c'est la **largeur** qui est divisée (utilisé pour une planche double scannée en un seul fichier large, à séparer en pages individuelles côte à côte).
6. **JPEG/WEBP** : conversion préalable en RGB si le mode source a un canal alpha (`RGBA`/`LA`/`P`), sauvegarde `quality=100, subsampling=0` (qualité maximale, pas de sous-échantillonnage chroma — évite toute perte supplémentaire sur un découpage qui ne devrait introduire aucune dégradation visible). Les autres formats (PNG, BMP, TIFF, GIF, AVIF) sont sauvegardés sans paramètres de qualité explicites.
7. **Nommage** : `{base_name}_part{i+1:02d}{ext}` — suffixe numéroté à 2 chiffres minimum, toujours dans l'ordre de la découpe (partie 1 = bande la plus en haut/à gauche).
8. Chaque partie devient une entrée via `create_entry()` (voir skill `archive-image-loading`) — un nouvel objet Python distinct par partie, aucun partage de référence entre elles.

## Insertion dans `images_data` — l'image source n'est jamais retirée

Différence structurelle majeure avec la fusion (`page-merge`, qui remplace ou insère à la place des sources) :

```python
for i, new_entry in enumerate(new_entries):
    state.images_data.insert(idx + 1 + i, new_entry)
```

- L'entrée d'origine (`idx`) **reste dans `images_data`**, à sa position initiale — elle n'est ni supprimée ni remplacée.
- Les N nouvelles parties sont insérées **juste après**, dans l'ordre (partie 1 à `idx+1`, partie 2 à `idx+2`, etc.).
- Résultat après l'opération : `[..., image_originale, partie1, partie2, ..., partieN, ...]` — la mosaïque contient donc **N+1 images** au total pour cette page (l'original + les N morceaux), pas juste N.
- **`free_image_memory(entry)`** est appelée sur l'entrée d'origine juste après l'insertion — libère `entry["img"]` (l'objet PIL décodé) tout en gardant `entry["bytes"]` intacts, car l'image source reste affichée dans la mosaïque mais son objet PIL complet n'est plus utile immédiatement après la découpe (voir skill `archive-image-loading`, section lazy loading).

**Ce comportement (garder l'original) n'est signalé nulle part dans l'UI** — un utilisateur qui s'attend à ce que la scission "remplace" la page par ses morceaux doit supprimer manuellement l'originale après coup s'il le souhaite. Vérifier ce point avant de le changer, ça pourrait être une décision de conception délibérée (permettre de comparer/annuler visuellement) plutôt qu'un oubli.

## Undo/redo — un seul point, pas deux

Contrairement au pattern à deux appels documenté dans le skill `undo-redo` (un avant, un après modification) et suivi par la fusion de pages (voir skill `page-merge`) : `split_page()` n'appelle `save_state()` **qu'une seule fois, avant** la modification. Pas de second appel après l'insertion des nouvelles entrées.

**Conséquence pratique** : un `undo` après une scission restaure bien l'état d'avant (l'image source seule, sans les parties) — ça fonctionne. Mais un `redo` après cet undo dépend de la mécanique générale de l'historique (voir skill `undo-redo`, `redo_data`/`state.history`) plutôt que d'un second snapshot dédié capturé juste après la scission — à vérifier si un bug de redo est signalé sur cette fonctionnalité spécifiquement, ce point mérite d'être creusé avant de supposer que le comportement est symétrique à celui de la fusion.

Pas de `force=True` explicite sur cet appel à `save_state` (contrairement à `page-merge`/`NameEdit`, voir skill `undo-redo`) — l'état n'a en général pas de raison d'être identique au précédent snapshot à ce stade (une sélection venait d'être faite), donc l'omission est probablement sans conséquence pratique, mais reste une différence par rapport aux autres points d'appel du projet qui prennent soin d'expliciter `force=True` quand la sauvegarde est anticipative.

Voir aussi skills `create-ico` et `animated-gif` : autres fonctionnalités du projet avec un seul point undo (après insertion, pas avant) plutôt que le pattern à deux appels, chacune parce qu'il s'agit d'un ajout de nouvelle page (le `.ico`/le GIF généré) plutôt que d'une modification en place.

## Interaction avec les onglets et le ComicInfo.xml

`sync_pages_in_xml_data(state)` (voir skill `comicinfo-metadata-editor`) est appelée après insertion des nouvelles entrées — régénère la section `<Pages>` du `ComicInfo.xml` si présent, pour que les N+1 pages (originale + parties) soient toutes correctement représentées avec leurs attributs recalculés (`ImageWidth`/`ImageHeight`/`ImageSize`).

Pas de callback `update_tabs`/`clear_selection` dans le contrat (`_split_page_callbacks`) — contrairement à la fusion. La sélection **n'est pas modifiée** après la scission : l'entrée originale reste sélectionnée (aucun code ne touche à `state.selected_indices`), les nouvelles parties ne sont jamais automatiquement sélectionnées.

## Interaction avec la renumérotation

**Aucun déclenchement automatique** de renumérotation après une scission — contrairement à la fusion (`page-merge`, qui appelle `renumber_no_save` si `renumber_mode != 0`) et au drag & drop (voir skill `renumbering`, section déclencheurs automatiques). `split_page()` ne touche jamais à `state.needs_renumbering` ni n'appelle de callback de renumérotation. Les nouvelles parties portent un nom généré (`_partNN`) qui reste tel quel jusqu'à ce que l'utilisateur déclenche une renumérotation manuelle (bouton dédié ou menu, voir skill `renumbering`).

**Piège potentiel à vérifier si une tâche touche à ce fichier** : ce silence sur la renumérotation pourrait être un oubli plutôt qu'un choix délibéré, contrairement au cas `renumber_mode == 0` de la fusion qui est un comportement historique explicitement documenté en commentaire. Si le comportement doit être aligné avec la fusion, ajouter l'appel à `callbacks["renumber_no_save"]` suivrait le même pattern que `_finish_join` dans `merge_dialog_qt.py` (voir skill `page-merge`) — mais ne pas le faire sans confirmation explicite, ça changerait un comportement existant.

## Interaction avec les panneaux

Un seul panneau à la fois, comme la fusion : `callbacks['state']` résolu vers `self._state` du panneau appelant (`PanelWidget._split_page_callbacks`) — aucune interaction cross-panel.

## Comment étendre

- **Changer la plage de nombre de parties** (actuellement 2-10) : `self._spinbox.setRange(2, 10)` dans `SplitDialog.__init__` **et** la revalidation manuelle dans `_on_ok` (`if num_pages < 2 or num_pages > 10`) — les deux bornes doivent rester synchronisées.
- **Ajouter un mode de découpe supplémentaire** (ex. parties inégales, découpe par pourcentage) : nouvelle option dans `SplitDialog` (radio ou combo), nouvelle branche dans `_on_confirmed` de `split_page()` — pas de fonction métier séparée à réutiliser, le calcul de crop est actuellement inline.
- **Changer le nommage des parties** (`_partNN`) : uniquement la f-string `new_name = f"{base_name}_part{i+1:02d}{ext}"`, dans les deux branches horizontale/verticale (dupliqué, à modifier aux deux endroits).
- **Faire disparaître l'image source après scission** (si demandé explicitement) : ajouter un retrait de `state.images_data[idx]` après l'insertion des parties — attention à décaler `idx+1+i` en conséquence si ce changement est fait, et à vérifier l'impact sur `free_image_memory` (qui deviendrait inutile si l'entrée est supprimée).
- Respecter les 8 règles UI Qt obligatoires du CLAUDE.md pour `SplitDialog` (non-modale déjà en place, `_wt()` pour le titre déjà en place).

## Pièges connus

- **L'image source n'est jamais retirée après scission** — ne pas supposer que "scinder" remplace la page comme le fait "fusionner" (voir skill `page-merge`) ; c'est le contraire, l'original persiste et les parties s'ajoutent.
- **Un seul appel à `save_state()`, pas deux** — contrairement au pattern standard documenté dans le skill `undo-redo` (avant + après). Si une modification future de ce fichier ajoute un second point après la découpe, suivre le pattern `force=True` documenté dans `undo-redo`/`page-merge`.
- **Aucune renumérotation automatique déclenchée** — contrairement à la fusion et au drag & drop. Vérifier avec l'utilisateur avant d'en ajouter une, ce n'est peut-être pas un oubli.
- **"Horizontal" divise la hauteur, "vertical" divise la largeur`** — la terminologie fait référence à l'orientation des lignes de séparation entre les parties, pas à l'axe le long duquel les dimensions sont mesurées ; source de confusion possible si le code est modifié sans relire attentivement quelle branche fait quoi.
- **`ensure_image_loaded` échoué retombe sur le même message que "sélection invalide"** — un diagnostic depuis un rapport utilisateur doit distinguer ces deux cas en pratique (image manquante vs image illisible) même si le message affiché est identique.
- **La couleur du texte d'avertissement est calculée en dur** (`#666666`/`#999999` selon `dark_mode`) plutôt que via `theme['disabled']` — incohérent avec le reste du projet si une refonte visuelle de ce dialogue est un jour demandée.
