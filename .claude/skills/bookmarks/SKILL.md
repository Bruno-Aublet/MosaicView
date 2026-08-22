---
name: bookmarks
description: Localiser ou modifier le marque-page de MosaicView (sauvegarde auto de la page en cours, popup "reprendre la lecture ?", icône ruban sur la vignette). Utiliser dès qu'une tâche touche à cfg.get_bookmark/set_bookmark ou _BookmarkPopup.
---

# Marque-pages (bookmarks) — MosaicView

Un marque-page mémorise **une page par archive** (CBZ/CBR/CB7/PDF ouvert), pour proposer de reprendre la lecture au bon endroit à la prochaine ouverture. Stockage global (config utilisateur), pas par panneau.

## Stockage — `config_manager.py`

Un seul dict `bookmarks` dans la config globale, clé = chemin absolu du fichier, valeur = index de page (position parmi les images, pas index brut dans `images_data`). API dans `modules/qt/config_manager.py:651-682` :

- `get_bookmark(filepath) -> int | None`
- `set_bookmark(filepath, page_idx, save=True)`
- `remove_bookmark(filepath, save=True)`
- `clear_bookmarks(save=True)`
- `has_any_bookmark() -> bool`
- `get_bookmarks() -> dict`

Toutes normalisent la clé via `os.path.abspath(filepath)`. Le dict est **partagé entre les deux panneaux** (config singleton) : un marque-page posé dans le panneau 1 est visible/proposé aussi dans le panneau 2 pour le même fichier.

`page_idx` est une **position parmi les entrées `is_image`** (`img_indices = [i for i, e in enumerate(state.images_data) if e.get("is_image")]`), pas un index brut dans `images_data` — nécessaire pour rester correct malgré les pages non-image (ComicInfo.xml, dossiers).

## Écriture — `image_viewer_qt.py`

`ImageViewerQt.closeEvent()` (ligne ~733) appelle `_save_bookmark(state)` (ligne ~762) à **chaque fermeture** de la visionneuse principale :
- Ne sauvegarde rien si la page courante est la première (`current_pos == 0`) ou la dernière (`current_pos >= last_pos`) — pas de marque-page utile en début/fin de lecture.
- Sinon `cfg.set_bookmark(filepath, current_pos)` puis appelle le callback `on_bookmark_changed(real_idx)` pour rafraîchir l'icône dans la mosaïque immédiatement (sans attendre une réouverture).

`_check_clear_bookmark_on_last_page()` (ligne ~907) est appelée à chaque changement de page dans la visionneuse : si la nouvelle page affichée est la dernière, **supprime automatiquement** le marque-page existant (`cfg.remove_bookmark` + `on_bookmark_changed(None)`) — la lecture est considérée terminée.

Il n'y a pas de bouton "poser un marque-page" dédié : c'est entièrement automatique, basé sur la page affichée au moment de la fermeture de la visionneuse.

## Popup "reprendre la lecture ?" — `panel_widget.py`

Classe `_BookmarkPopup` (ligne ~150) : `QDialog` non-modal minimal (message + boutons Oui/Non), respecte thème/langue/police comme toute fenêtre Qt de l'appli (voir règles UI CLAUDE.md).

Déclenchement : `_on_loading_finished()` (appelé après tout chargement, y compris rechargements) appelle `_maybe_show_bookmark_popup()` (ligne ~1857) :

1. Lit `self._state.current_file` (état **du panneau**, pas global) — si vide, abandonne.
2. **Garde-fou anti-répétition** : `self._bookmark_popup_shown_for` (attribut du panneau) mémorise le dernier fichier pour lequel le popup a déjà été proposé. Si `current_file` est identique, n'affiche rien — sinon le popup réapparaîtrait à chaque rechargement de la mosaïque au sein d'un même comics déjà ouvert (import de pages, Ctrl+V, drop de fichier), pas seulement à l'ouverture initiale.
3. **Ce garde-fou est réinitialisé (`None`) dans `_close_bookmark_popup()`** (ligne ~1908), appelée uniquement lors de la fermeture explicite du fichier (`refresh_tabs` dans `_file_close_args`). Sans cette réinitialisation, rouvrir le même fichier après l'avoir fermé ne réaffiche jamais le popup.
4. Si un marque-page existe (`cfg.get_bookmark`) et que `page_idx` est dans les bornes de la mosaïque actuelle, construit et affiche `_BookmarkPopup` ; le bouton Oui ouvre la visionneuse directement sur la page mémorisée (`_open_image_viewer(real_idx)`).

**Chaque `PanelWidget` a son propre `_bookmark_popup_shown_for` et `_bookmark_popup`** — c'est un état d'instance, pas global. Le popup lui-même est parenté au panneau qui l'a ouvert et centré dessus via `_center_on_widget`.

## Icône visuelle dans la mosaïque — `mosaic_canvas.py`

- `entry["_is_bookmarked"]` (bool) sur chaque entrée d'`images_data` marque la page bookmarkée. Une seule page peut être `True` à la fois (mono-marque-page par archive).
- `refresh_bookmark_overlay(bookmarked_real_idx)` (ligne ~960) : parcourt tous les `ThumbnailItem`, met à jour le flag et ne force un repaint (`item.update()`) que sur les items dont l'état a réellement changé — pas de reconstruction complète de la scène.
- Rendu dans `ThumbnailItem.paint()` (ligne ~741) : pixmap `icons/Bookmark Ribbon.png`, coin supérieur droit de la vignette, `setOpacity(0.85)`, taille `max(32, tw // 2)`.
- `_init_bookmark_overlay()` dans `panel_widget.py` (ligne ~1827) initialise `_is_bookmarked` à l'ouverture d'un fichier à partir de la config — avec une garde spécifique : si une entrée porte déjà `_is_bookmarked=True` en mémoire (ex. import/collage en cours de session), elle est préservée telle quelle au lieu d'être recalculée par position, car `page_idx` est une position figée qui se décale dès qu'une page est insérée avant elle.

## Suppression manuelle — menus

Deux actions exposées à trois endroits (menu Fichier de la menubar, menu contextuel canvas avec fichier ouvert, menu contextuel sur une vignette) :

- **Supprimer le marque-page** (`delete_bookmark` → `panel_widget.py::_delete_current_bookmark`, ligne ~1394) : actif seulement si `cfg.get_bookmark(state.current_file)` n'est pas `None`. Après suppression en config, rafraîchit l'overlay (`_on_bookmark_changed(None)`) sur **tous les panneaux ouverts dont `current_file` correspond au fichier concerné** (via `self._main_window._all_panels()`), pas seulement le panneau d'où l'action a été lancée.
- **Supprimer tous les marque-pages** (`delete_all_bookmarks` → `_delete_all_bookmarks`, ligne ~1404) : actif seulement si `cfg.has_any_bookmark()`. Après `cfg.clear_bookmarks()`, rafraîchit l'overlay sur **tous les panneaux ouverts sans condition** (chaque marque-page en config étant effacé, peu importe quel comics est ouvert dans quel panneau).

Call sites de la logique d'activation/désactivation (dupliquée trois fois, à garder synchronisée si le comportement change) :
- `menubar_qt.py` ligne ~276 (menu Fichier)
- `context_menus_qt.py` ligne ~162 (menu contextuel canvas, fichier ouvert)
- `context_menus_qt.py` ligne ~504 (menu contextuel vignette)

## Pièges connus

- **Ne pas confondre `page_idx` (position parmi les images) et l'index brut dans `images_data`** — toujours passer par `img_indices = [i for i, e in enumerate(...) if e.get("is_image")]` pour convertir dans un sens ou l'autre.
- **Le garde-fou `_bookmark_popup_shown_for` doit être réinitialisé à chaque fermeture réelle du fichier**, jamais lors d'un simple rechargement de mosaïque (import, Ctrl+V, drop) — sinon soit le popup réapparaît de façon intempestive à chaque rechargement, soit il ne réapparaît jamais après une fermeture/réouverture. Point d'ajustement unique : `_close_bookmark_popup()`.
- **État par panneau vs config globale** : `current_file`, `_bookmark_popup`, `_bookmark_popup_shown_for` sont propres à chaque `PanelWidget` ; le dict `bookmarks` en config est partagé entre les deux panneaux. La config seule ne suffit pas à synchroniser l'affichage : l'overlay visuel (`_is_bookmarked` + `refresh_bookmark_overlay`) est un état en mémoire propre à chaque panneau, donc toute suppression de marque-page doit explicitement rafraîchir l'overlay des autres panneaux concernés (voir section "Suppression manuelle" ci-dessus) — se contenter d'écrire en config sans le faire laisse le ruban affiché à tort dans l'autre panneau jusqu'à un rechargement.
- **Diagnostiquer un bug de popup qui n'apparaît pas** : instrumenter avec des prints (règle CLAUDE.md, jamais de fix à l'aveugle) dans `_on_loading_finished`, `_maybe_show_bookmark_popup` (chaque `return` anticipé) et `ArchiveLoader.load`/`_on_finished` — utile pour distinguer un vrai double-chargement d'un simple garde-fou qui bloque à tort.
