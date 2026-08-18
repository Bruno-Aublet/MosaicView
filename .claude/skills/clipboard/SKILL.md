---
name: clipboard
description: Localiser ou modifier le copier/couper/coller vers/depuis le presse-papiers système Windows (CF_HDROP fichiers, CF_DIB bitmap), pages sélectionnées ou archive complète. Utiliser dès qu'une tâche touche à clipboard_qt.py ou aux raccourcis Ctrl+C/X/V.
---

# Copier / Couper / Coller — MosaicView

Presse-papiers **système Windows** (pas un presse-papiers interne à l'application) : passe par `win32clipboard`/`win32con` (pywin32), avec un dossier temporaire intermédiaire sur disque pour le copier/couper de pages. Fichier central unique : [modules/qt/clipboard_qt.py](../../../modules/qt/clipboard_qt.py).

## Les fonctions

| Fonction | Rôle | Ligne |
|---|---|---|
| `copy_to_system_clipboard(get_temp_dir_func, parent=None)` | Copie les pages sélectionnées de la mosaïque, `state.selected_indices` (CF_HDROP) | [clipboard_qt.py:74](../../../modules/qt/clipboard_qt.py#L74) |
| `copy_single_entry_to_system_clipboard(entry, get_temp_dir_func, parent=None)` | Copie UNE entrée précise, indépendamment de `state.selected_indices` — utilisée par le Ctrl+C dédié de la visionneuse (skill `paste-image`), où la page affichée peut diverger de la sélection de la mosaïque (CF_HDROP) | `clipboard_qt.py` |
| `cut_selected(get_temp_dir_func, render_mosaic, save_state, parent=None)` | Copie puis supprime les pages sélectionnées de la mosaïque | [clipboard_qt.py:160](../../../modules/qt/clipboard_qt.py#L160) |
| `paste_from_system_clipboard(parent, load_files_callback, save_state, render_mosaic, clear_selection, natural_sort_key)` | Colle des fichiers (CF_HDROP) ou une image bitmap (CF_DIB) dans la mosaïque | [clipboard_qt.py:183](../../../modules/qt/clipboard_qt.py#L183) |
| `clipboard_has_single_image() -> bool` | Test LECTURE SEULE (pas d'extraction) : le presse-papiers contient-il EXCLUSIVEMENT une image (CF_DIB, ou CF_HDROP à un seul fichier reconnu image) ? Utilisée par le grisage live de l'icône "Coller une image" (`QClipboard.dataChanged`, skill `paste-image`) | `clipboard_qt.py` |
| `get_clipboard_single_image()` | Retourne l'objet PIL réel si `clipboard_has_single_image()` est vrai, sinon `None` — utilisée au moment de coller réellement (skill `paste-image`) | `clipboard_qt.py` |
| `copy_archive_to_clipboard(parent)` | Copie le fichier archive courant entier (CF_HDROP) — pas les pages | [clipboard_qt.py:20](../../../modules/qt/clipboard_qt.py#L20) |

`copy_to_system_clipboard`/`copy_single_entry_to_system_clipboard` partagent un cœur commun, `_copy_entries_to_system_clipboard(entries, get_temp_dir_func, parent=None)` (écriture sur disque + pose du CF_HDROP) — ne jamais dupliquer cette logique pour un futur besoin de copie d'une liste d'entrées arbitraire, passer par cette fonction.

`copy_to_system_clipboard`/`cut_selected`/`paste_from_system_clipboard`/`copy_archive_to_clipboard` sont appelées depuis des wrappers `PanelWidget._copy_selected` / `_cut_selected` / `_paste_ctrl_v` / `_copy_archive_to_clipboard` ([panel_widget.py:1786-1814](../../../modules/qt/panel_widget.py#L1786)), qui injectent les callbacks nécessaires (`self._get_temp_dir`, `self._canvas.render_mosaic`, `self.save_state`, `self._load_files`, `self._canvas._clear_selection_and_emit`). `copy_single_entry_to_system_clipboard`/`clipboard_has_single_image`/`get_clipboard_single_image` sont appelées directement depuis la visionneuse (`image_viewer_qt.py`/`viewer_toolbar_qt.py`, skill `paste-image`), pas de wrapper `PanelWidget` équivalent — la visionneuse n'a pas de `PanelWidget` sous-jacent au sens de la mosaïque.

## Ce que "copier" copie réellement

`copy_to_system_clipboard` n'écrit pas directement les bytes de `entry["bytes"]` dans le presse-papiers Windows : il les **extrait sur disque** dans un dossier temporaire `clipboard_<id>_<timestamp>/`, puis pose un CF_HDROP (liste de chemins de fichiers) pointant vers ce dossier. C'est pour ça qu'un Ctrl+V dans l'Explorateur Windows (ou dans un autre panneau MosaicView) fonctionne : le système voit de vrais fichiers, pas un flux d'octets applicatif.

- `entry["bytes"] is None` ou `entry.get("is_dir")` → entrée ignorée silencieusement (pas de fichier réel à copier).
- Nom d'entrée piégé (`../`, chemin absolu, autre lettre de lecteur) → `safe_join()` ([utils.py:369](../../../modules/qt/utils.py#L369)) retourne `None`, l'entrée est comptée dans `skipped` et un avertissement non-modal s'affiche (`_warn_unsafe_paths_skipped`) — protection anti Zip Slip, l'entrée provient potentiellement d'une archive externe non fiable.
- Si toutes les entrées sélectionnées sont invalides/dangereuses → aucun CF_HDROP n'est posé, seul l'avertissement s'affiche.

`copy_archive_to_clipboard` est différent : il pose directement `state.current_file` (le chemin du CBZ/CBR ouvert) en CF_HDROP, sans extraction — copie l'archive **en tant que fichier**, indépendamment de toute sélection de pages.

## `IMAGE_EXTS` — extensions reconnues comme "image"

Constante module (`clipboard_qt.py`) : `('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff', '.tif', '.ico', '.jfif', '.pjpeg', '.pjp', '.avif')`. Une seule source de vérité, consommée par `paste_from_system_clipboard` (branche CF_HDROP), `clipboard_has_single_image()` et le `dragEnterEvent`/`dropEvent` de la visionneuse (skill `paste-image`) — ne jamais dupliquer cette liste ailleurs, importer `IMAGE_EXTS` depuis ce module.

## Coller — deux formats gérés

`paste_from_system_clipboard` teste dans l'ordre :
1. **CF_HDROP** (fichiers/dossiers copiés depuis l'Explorateur ou une autre instance MosaicView) → `load_files_callback(list(files))`, qui route vers `PanelWidget._load_files()` (voir skill `archive-image-loading`). `_paste_ctrl_v` ([panel_widget.py:1877](../../../modules/qt/panel_widget.py#L1877)) enveloppe ce callback pour passer `rename_collisions_as_copy=True` à `_load_files` : si le nom d'un fichier collé collisionne avec une entrée déjà présente dans `state.images_data` (cas normal d'un copier/coller de page **interne** à la mosaïque), il est renommé `nom-COPY.ext`, puis `nom-COPY2.ext`, `nom-COPY3.ext`... pour les copies suivantes de la même page, **au lieu** du préfixe générique `NEW-` — afin que la copie reste triée juste à côté de sa page source dans l'ordre alphanumérique de la mosaïque. En l'absence de collision (ex. fichier collé depuis l'Explorateur, nom déjà unique), le nom n'est pas modifié du tout. Voir skill `archive-image-loading` (section `_ImageLoadWorker`) pour le détail de cette logique, qui vit dans `_ImageLoadWorker`/`_start_image_load`, pas dans `clipboard_qt.py`.
2. **CF_DIB** (image bitmap copiée depuis un autre logiciel — capture d'écran, éditeur d'image, navigateur) → `PIL.ImageGrab.grabclipboard()`, ré-encodée en PNG, nommée `pasted_N.png` (N = premier entier disponible pour éviter une collision), ajoutée via `create_entry()` (voir skill `archive-image-loading`) puis triée par `natural_sort_key` dans `state.images_data`.
3. Ni l'un ni l'autre → ne fait rien silencieusement (pas de message).

Si `pywin32` n'est pas installé (`ImportError`), les 3 fonctions de copie/coupe/coller affichent un message non-modal `messages.info.pywin32_required` et abandonnent — sauf `copy_to_system_clipboard` qui abandonne silencieusement (utilisée aussi en interne par `cut_selected`, où un message serait redondant avec celui déjà affiché côté coupe... en pratique elle ne montre aucun message du tout en l'absence de pywin32, à vérifier si un comportement visible est souhaité un jour).

## Undo/redo et renumérotation

- `cut_selected` appelle `save_state()` **avant** de retirer les entrées de `state.images_data` → un Ctrl+Z restaure les pages coupées (voir skill `undo-redo`).
- `paste_from_system_clipboard` (branche CF_DIB uniquement) appelle `save_state()` avant d'ajouter l'entrée collée — un coller d'image bitmap est annulable. La branche CF_HDROP ne fait pas de `save_state()` elle-même : c'est `load_files_callback`/`_load_files` qui gère son propre point d'historique (voir skill `archive-image-loading`).
- Les deux branches modifiantes (`cut_selected`, coller CF_DIB) appellent `sync_pages_in_xml_data(state)` ([comic_info.py](../../../modules/qt/comic_info.py)) pour garder `<Pages>` du ComicInfo.xml synchronisé — voir skill `comicinfo-metadata-editor`.
- Ni coupe ni collage ne déclenchent de renumérotation automatique (`renumber_no_save`) — contrairement au drag & drop ou au merge. Si ce comportement doit changer, voir skill `renumbering`.

## Points d'entrée UI

3 chemins convergent vers les mêmes 4 fonctions :

| UI | Copier | Couper | Coller | Copier archive |
|---|---|---|---|---|
| Raccourcis clavier globaux | `Ctrl+C` | `Ctrl+X` | `Ctrl+V` | — |
| Barre de menu (`menubar_qt.py:151-155`) | ✓ | ✓ | ✓ | ✓ |
| Menu contextuel canvas vide/avec fichier (`context_menus_qt.py:211-217`) | — | — | ✓ | ✓ |
| Menu contextuel sur une page (`context_menus_qt.py:544-559`) | ✓ | ✓ | — | — |
| Colonne d'icônes (`icon_toolbar_qt.py`, ids `copy_selected`/`cut_selected`/`paste`) | ✓ | ✓ | ✓ | — |

- Raccourcis câblés dans [MosaicView.py:207-209](../../../MosaicView.py#L207) sur `self._active_panel` (voir skill `panels` — c'est toujours le panneau **actif** qui reçoit l'action, pertinent en split-view).
- `copy_selected`/`cut_selected` désactivés dans la colonne d'icônes et grisés dans les menus si `state.selected_indices` est vide (`has_selection`/`has_sel`) ; `copy_archive_to_clipboard` désactivé si `state.current_file` est vide.
- `paste` n'a pas de condition d'activation dans `icon_toolbar_qt.py` (`"paste": None` — toujours actif), contrairement aux menus qui n'affichent pas non plus de garde particulière côté "presse-papiers vide" (le clic sur Coller sans rien à coller ne fait juste rien).

## Modifier la fonction

- **Changer ce qui est copié/collé** (ex. ajouter un format CF_* supplémentaire) : modifier `paste_from_system_clipboard`, ajouter un `elif win32clipboard.IsClipboardFormatAvailable(win32con.CF_XXX)` après la branche CF_DIB.
- **Changer le nommage des fichiers collés/copiés** : `pasted_{counter}.png` (coller bitmap) est codé en dur dans `clipboard_qt.py:224` ; le nom des fichiers copiés (CF_HDROP posé par `copy_to_system_clipboard`) reprend `entry["orig_name"]` tel quel ; le suffixe `-COPY`/`-COPYn` appliqué en cas de collision lors du coller (CF_HDROP) se change dans `_ImageLoadWorker._copy_suffixed_name()` (`panel_widget.py`), pas ici — `clipboard_qt.py` ne fait que déclencher le flag `rename_collisions_as_copy` via le wrapper `_paste_ctrl_v`, il ne connaît pas la logique de renommage elle-même.
- **Ajouter un nouveau point d'entrée UI** : réutiliser les wrappers `PanelWidget._copy_selected`/`_cut_selected`/`_paste_ctrl_v`/`_copy_archive_to_clipboard`, ne jamais réimporter `clipboard_qt` directement depuis un nouveau fichier menu/icône sans passer par ces wrappers (ils portent les callbacks corrects du panneau).
- **Tout nouveau dialogue de ce module doit suivre les 8 règles UI Qt de CLAUDE.md** (non-modal, thème, langue, police, titre via `_wt()`, tooltips `OverlayTooltip`) — les dialogues existants (`MsgDialog`, `InfoDialog`, `ErrorDialog`) le font déjà correctement via `show_nonmodal()`/`.show()` + lambdas.

## Bug corrigé — dossiers `clipboard_*` mal placés (historique)

Jusqu'à sa correction, `PanelWidget._get_temp_dir()` ([panel_widget.py:747](../../../modules/qt/panel_widget.py#L747)) — la fonction injectée comme `get_temp_dir_func` dans `copy_to_system_clipboard`/`cut_selected` — retournait `os.path.realpath(tempfile.gettempdir())`, c'est-à-dire **la racine `%TEMP%`**, au lieu de `%TEMP%\MosaicViewTemp\`. Conséquence : les dossiers `clipboard_*` créés par un copier/couper de pages atterrissaient hors de portée des deux mécanismes de nettoyage (`cleanup_all_temp_files()` rétention 12h, et le bouton "Effacer le presse-papiers"), qui ne scannent que `%TEMP%\MosaicViewTemp\` — ils s'accumulaient indéfiniment dans la racine `%TEMP%`, jamais nettoyés.

**Fixé** : `_get_temp_dir()` appelle maintenant `get_mosaicview_temp_dir()` (`temp_files.py`), donc pointe vers `%TEMP%\MosaicViewTemp\` comme prévu. Ce même callback est aussi injecté sous le nom `"get_mosaicview_temp_dir"` dans les dialogues batch ([panel_widget.py:687](../../../modules/qt/panel_widget.py#L687) et [panel_widget.py:789](../../../modules/qt/panel_widget.py#L789), voir skill `batch-processing`) — le fix corrige donc aussi le placement des logs de batch par la même occasion.

**Migration des résidus** : les dossiers `clipboard_*` déjà créés dans la racine `%TEMP%` par une version antérieure au fix ne sont pas retrouvés par `_get_temp_dir()` une fois corrigé (il ne regarde plus que dans `MosaicViewTemp`). Un nettoyage dédié, `cleanup_legacy_root_clipboard_dirs()` (`temp_files.py`, voir skill `temp-files`), est appelé une fois au lancement dans `MosaicView.py::main()` juste après `cleanup_stale_mei_dirs()` : il balaie la racine `%TEMP%` et supprime tout dossier `clipboard_*` qui s'y trouve, sans condition d'âge (leur seule présence à cet endroit suffit à les qualifier d'orphelins, plus aucune version corrigée ne peut en créer là).

## Sécurité

- `safe_join()` bloque toute tentative d'évasion de dossier (Zip Slip) sur les noms d'entrées lors de l'extraction pour copie — cohérent avec la règle CLAUDE.md sur les données d'origine externe.
- Le CF_HDROP posé par `copy_archive_to_clipboard` expose le chemin réel de l'archive sur le disque de l'utilisateur — comportement attendu (équivalent à un copier de fichier dans l'Explorateur), pas une fuite.

## Référencements croisés

- **`temp-files`** — emplacement nominal `%TEMP%\MosaicViewTemp\`, rétention 12h des dossiers `clipboard_*`, bouton "Effacer le presse-papiers".
- **`batch-processing`** — partage le même callback `_get_temp_dir`/`get_mosaicview_temp_dir` pour ses logs d'erreur, concerné par le même bug/fix (voir section historique ci-dessus).
- **`undo-redo`** — `save_state()` appelé par `cut_selected` et par le coller CF_DIB.
- **`archive-image-loading`** — `load_files_callback`/`_load_files` (coller CF_HDROP), `create_entry()` (coller CF_DIB).
- **`comicinfo-metadata-editor`** — `sync_pages_in_xml_data()` appelé après coupe/collage.
- **`panels`** — les raccourcis clavier globaux agissent sur `self._active_panel`, jamais sur un panneau fixe.
- **`renumbering`** — coupe/collage ne déclenchent volontairement aucune renumérotation automatique, à comparer avec `drag-and-drop`/`page-merge` qui en déclenchent une.
- **`drag-and-drop`** — autre mécanisme d'entrée/sortie de fichiers (CF_HDROP en drag-out), fichier séparé (`mosaic_canvas.py`), dossiers `drag_*` distincts des `clipboard_*`.
- **`paste-image`** — Ctrl+C/Ctrl+V dédiés de la visionneuse principale et grisage live de son icône "Coller une image" consomment `copy_single_entry_to_system_clipboard`/`clipboard_has_single_image`/`get_clipboard_single_image`/`IMAGE_EXTS` (ce fichier) sans les réécrire.

## Pièges connus

- **`pywin32` non installé** : les 3 fonctions dépendantes de `win32clipboard` échouent silencieusement ou avec message selon le chemin — ne pas supposer que pywin32 est garanti présent lors d'un test manuel sur une machine sans l'application compilée/installée normalement.
- **Ne jamais coder en dur un chemin `%TEMP%\clipboard_...`** — toujours utiliser le callback `get_temp_dir_func` injecté (lui-même basé sur `get_mosaicview_temp_dir()` depuis le fix).
- **Le suffixe `-COPY`/`-COPYn` n'est actif que pour le coller CF_HDROP déclenché par `_paste_ctrl_v`** — un drag & drop de fichiers, un import depuis le menu "Ouvrir", ou tout autre appel à `_load_files()` sans `rename_collisions_as_copy=True` continue de préfixer `NEW-` en cas d'ajout à une session déjà ouverte, même si le nom ne collisionne pas réellement. Ne pas supposer que ce nouveau comportement s'applique ailleurs qu'au Ctrl+V/menu "Coller" sans vérifier l'appelant.
