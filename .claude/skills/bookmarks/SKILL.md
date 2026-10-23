---
name: bookmarks
description: Localiser ou modifier le marque-page de MosaicView (mode marque-pages on/off par panneau, sauvegarde auto de la page en cours, popup "reprendre la lecture ?", icône ruban sur la vignette, indicateur de la barre de statut). Utiliser dès qu'une tâche touche à cfg.get_bookmark/set_bookmark, get_bookmarks_enabled, _BookmarkPopup ou _bookmark_indicator.
---

# Marque-pages (bookmarks) — MosaicView

Un marque-page mémorise **une page par archive** (CBZ/CBR/CB7/PDF ouvert), pour proposer de reprendre la lecture au bon endroit à la prochaine ouverture. Les marque-pages eux-mêmes sont stockés dans la config globale (communs aux deux panneaux) ; le **mode marque-pages** (on/off), lui, est propre à chaque panneau.

## Stockage des marque-pages — `config_manager.py`

Un seul dict `bookmarks` dans la config globale, clé = chemin absolu du fichier, valeur = index de page (position parmi les images, pas index brut dans `images_data`). API dans `modules/qt/config_manager.py` (`ConfigManager.get_bookmarks` … `has_any_bookmark`) :

- `get_bookmark(filepath) -> int | None`
- `set_bookmark(filepath, page_idx, save=True)`
- `remove_bookmark(filepath, save=True)`
- `clear_bookmarks(save=True)`
- `has_any_bookmark() -> bool`
- `get_bookmarks() -> dict`

Toutes normalisent la clé via `os.path.abspath(filepath)`. Le dict est **partagé entre les deux panneaux** (config singleton) : un marque-page posé dans le panneau 1 est visible aussi dans le panneau 2 pour le même fichier.

`page_idx` est une **position parmi les entrées `is_image`** (`img_indices = [i for i, e in enumerate(state.images_data) if e.get("is_image")]`), pas un index brut dans `images_data` — nécessaire pour rester correct malgré les pages non-image (ComicInfo.xml, dossiers).

## Mode marque-pages on/off — par panneau

- **Mode on** : la visionneuse pose un marque-page à sa fermeture, et le popup « reprendre la lecture ? » est proposé à l'ouverture d'un fichier qui en a un.
- **Mode off** (défaut) : aucun marque-page n'est posé et le popup n'est pas proposé. Les marque-pages existants restent affichés dans la mosaïque (ruban), restent effaçables par les menus, et sont toujours supprimés automatiquement quand la dernière page est atteinte.

Stockage : clés `bookmarks_enabled` (panel1) et `bookmarks_enabled_panel2` (panel2) de `ConfigManager.DEFAULT_CONFIG`, toutes deux `False` par défaut. Accès :
- `ConfigManager.get_bookmarks_enabled()` / `set_bookmarks_enabled(enabled, save=True)` → panel1 ;
- `ConfigManager.get_bookmarks_enabled_panel2()` / `set_bookmarks_enabled_panel2(...)` → panel2 ;
- `Panel2Config.get_bookmarks_enabled()` / `set_bookmarks_enabled()` redirigent vers les clés `_panel2` (voir skill `panels`).

Côté panneau (`panel_widget.py`) :
- `PanelWidget._bookmarks_config()` renvoie `ConfigManager` (panel1) ou `Panel2Config` (panel2), sur le modèle de `_zip_compression_config()`.
- `PanelWidget._bookmarks_enabled()` lit le mode du panneau. **C'est le seul point de lecture à utiliser** : il est transmis à la barre de statut (`set_bookmarks_enabled_getter`) et à la visionneuse (clé `"bookmarks_enabled"` de `_image_viewer_callbacks()`), et lu par `_maybe_show_bookmark_popup()`.
- `PanelWidget._toggle_bookmarks_enabled()` (clic gauche sur l'indicateur, ou commande de menu — voir « Bascule du mode depuis les menus ») inverse le mode **de ce panneau uniquement** puis rafraîchit sa seule barre de statut.

### Bascule du mode depuis les menus

Une seule ligne, dont le libellé propose l'inverse du mode courant du panneau : `context_menu.canvas_with_file.bookmarks_enable` (« Activer les marque-pages ») si off, `context_menu.canvas_with_file.bookmarks_disable` (« Désactiver les marque-pages ») si on. Les deux libellés ne sont jamais affichés ensemble, et la ligne est toujours active, même sans fichier ouvert (le mode se règle avant de charger une BD). Placée en tête du groupe marque-pages, juste avant les deux commandes d'effacement, dans :
- le menu Archives de la menubar (`menubar_qt.py::_populate_archives_menu`) — panneau propriétaire de la barre ;
- les menus contextuels du fond de la mosaïque (`show_canvas_context_menu`) et d'une vignette (`show_image_context_menu`), via le helper `context_menus_qt.py::_add_bookmarks_mode_action` — panneau cliqué.

Pas dans `show_bookmark_indicator_menu` : le clic gauche sur l'indicateur fait déjà la bascule.

Callbacks `get_bookmarks_enabled` (→ `PanelWidget._bookmarks_enabled`) et `toggle_bookmarks_enabled` (→ `_toggle_bookmarks_enabled`) dans `build_menubar_callbacks` (`menubar_callbacks_qt.py`), qui reçoit le panneau : chaque menu agit donc sur son propre panneau. Le libellé est recalculé à chaque ouverture (menubar reconstruite sur `aboutToShow`, menus contextuels construits à chaque clic droit) : aucune synchronisation à maintenir après une bascule par l'indicateur.

Réinitialisation de l'application (`session_restore_qt.reset_to_defaults`) : remet les deux clés à `False` directement sur `ConfigManager` (y compris celle de panel2 quand panel2 n'a jamais été créé), avant la boucle qui rappelle `_update_status_bar()` sur chaque panneau. La ligne correspondante figure dans le tooltip de la commande (`context_menu.canvas.reset`).

## Indicateur dans la barre de statut

Icône ruban (colorée = on, grisée = off) à droite de l'indicateur doublons, dans chaque barre de statut. Clic gauche → `_toggle_bookmarks_enabled` ; clic droit → `PanelWidget._show_bookmark_indicator_menu` → `context_menus_qt.show_bookmark_indicator_menu`, qui propose les deux commandes d'effacement (voir « Suppression manuelle »). Tooltips `tooltip.bookmark_indicator_on` / `tooltip.bookmark_indicator_off`. Détails d'affichage et de rafraîchissement : skill `status-bar`.

## Écriture — `image_viewer_qt.py`

`ImageViewer.closeEvent()` appelle `_save_bookmark(state)` à **chaque fermeture** de la visionneuse principale (sauf visionneuse transitoire de lecture de macro) :
- Ne fait rien si le panneau d'origine est en mode off : `self.callbacks["bookmarks_enabled"]()` faux ou absent.
- Ne sauvegarde rien si la page courante est la première (`current_pos == 0`) ou la dernière (`current_pos >= last_pos`) — pas de marque-page utile en début/fin de lecture.
- Sinon `cfg.set_bookmark(filepath, current_pos)` puis appelle le callback `on_bookmark_changed(real_idx)` pour rafraîchir l'icône dans la mosaïque immédiatement (sans attendre une réouverture).

Le mode est lu via le callback et non via la config globale : la visionneuse appartient au panneau qui l'a ouverte, et les deux panneaux peuvent avoir des modes différents. Tous les chemins d'ouverture (double-clic, popup de reprise, lecture de macro) passent par `PanelWidget._image_viewer_callbacks()`.

Le callback `on_bookmark_changed` fourni à la visionneuse est **`_on_viewer_bookmark_changed`**, pas `_on_bookmark_changed` : il rafraîchit le ruban du panneau de la visionneuse (`_on_bookmark_changed(real_idx)`), puis celui de **tout autre panneau dont `current_file` est le même fichier**, via `p._resync_bookmark_from_config()`. Ce dernier remet `_is_bookmarked` à `False` sur toutes les entrées puis rappelle `_init_bookmark_overlay()` — indispensable, car `_init_bookmark_overlay()` seul préserve un `_is_bookmarked=True` déjà posé et ne verrait donc pas le déplacement ou le retrait du marque-page. Les trois écritures de la visionneuse passent par ce callback : `_save_bookmark`, `_check_clear_bookmark_on_last_page`, `_delete_current_bookmark` (menu contextuel de la visionneuse).

`_check_clear_bookmark_on_last_page()` est appelée par `navigate()` à chaque changement de page réussi (nouvelle page trouvée dans `img_indices`) **et** dans son cas de repli (page courante disparue de `img_indices`, ex. suppression de la page affichée depuis la mosaïque pendant que la visionneuse reste ouverte — repositionnement forcé sur la première page) : si la nouvelle page affichée est la dernière, **supprime automatiquement** le marque-page existant (`cfg.remove_bookmark` + `on_bookmark_changed(None)`) — la lecture est considérée terminée. Cette suppression ne dépend **pas** du mode marque-pages. **Piège** : ces deux points d'appel doivent rester synchronisés — un appel de `navigate()` qui omettrait celui-ci laisserait le marque-page en place à tort dans ce cas de repli.

Il n'y a pas de bouton "poser un marque-page" dédié : c'est entièrement automatique, basé sur la page affichée au moment de la fermeture de la visionneuse.

## Popup "reprendre la lecture ?" — `panel_widget.py`

Classe `_BookmarkPopup` : `QDialog` non-modal minimal (message + boutons Oui/Non), respecte thème/langue/police comme toute fenêtre Qt de l'appli (voir règles UI CLAUDE.md). `WA_DeleteOnClose` ; à sa fermeture par la croix (`closeEvent`) ou par Échap (`reject`, qui ne passe pas par `closeEvent`), `_forget_in_parent` efface la référence du panneau (`_bookmark_popup`).

Déclenchement : `_on_loading_finished()` (appelé après tout chargement, y compris rechargements) appelle `_maybe_show_bookmark_popup()` :

1. Lit `self._state.current_file` (état **du panneau**, pas global) — si vide, abandonne.
2. **Garde-fou anti-répétition** : `self._bookmark_popup_shown_for` (attribut du panneau) mémorise le dernier fichier pour lequel le popup a déjà été proposé. Si `current_file` est identique, n'affiche rien — sinon le popup réapparaîtrait à chaque rechargement de la mosaïque au sein d'un même comics déjà ouvert (import de pages, Ctrl+V, drop de fichier), pas seulement à l'ouverture initiale.
3. **Ce garde-fou est réinitialisé (`None`) dans `_close_bookmark_popup()`**, appelée uniquement lors de la fermeture explicite du fichier (`refresh_tabs` dans `_file_close_args`). Sans cette réinitialisation, rouvrir le même fichier après l'avoir fermé ne réaffiche jamais le popup.
4. Si le panneau est en mode off (`self._bookmarks_enabled()` faux), abandonne. Ce test vient **après** la pose du garde-fou : passer en mode on pendant que le fichier reste ouvert ne fait donc pas apparaître le popup, qui n'est proposé qu'à l'ouverture.
5. Si un marque-page existe (`cfg.get_bookmark`) et que `page_idx` est dans les bornes de la mosaïque actuelle, construit et affiche `_BookmarkPopup` ; le bouton Oui ouvre la visionneuse directement sur la page mémorisée (`_open_image_viewer(real_idx)`).

**Chaque `PanelWidget` a son propre `_bookmark_popup_shown_for` et `_bookmark_popup`** — c'est un état d'instance, pas global. Le popup lui-même est parenté au panneau qui l'a ouvert et centré dessus via `_center_on_widget`.

## Icône visuelle dans la mosaïque — `mosaic_canvas.py`

- `entry["_is_bookmarked"]` (bool) sur chaque entrée d'`images_data` marque la page bookmarkée. Une seule page peut être `True` à la fois (mono-marque-page par archive).
- `refresh_bookmark_overlay(bookmarked_real_idx)` : parcourt tous les `ThumbnailItem`, met à jour le flag et ne force un repaint (`item.update()`) que sur les items dont l'état a réellement changé — pas de reconstruction complète de la scène.
- Rendu dans `ThumbnailItem.paint()` : pixmap `icons/Bookmark Ribbon.png` (chargée une fois par `_get_bookmark_pixmap()`, réutilisée par l'indicateur de la barre de statut), coin supérieur droit de la vignette, `setOpacity(0.85)`, taille `max(32, tw // 2)`.
- `_init_bookmark_overlay()` dans `panel_widget.py` initialise `_is_bookmarked` à l'ouverture d'un fichier à partir de la config — avec une garde spécifique : si une entrée porte déjà `_is_bookmarked=True` en mémoire (ex. import/collage en cours de session), elle est préservée telle quelle au lieu d'être recalculée par position, car `page_idx` est une position figée qui se décale dès qu'une page est insérée avant elle.
- Le ruban s'affiche quel que soit le mode marque-pages.

## Suppression manuelle — menus

Deux actions (libellés `context_menu.canvas_with_file.delete_bookmark` « Effacer le marque-page de cette BD » et `context_menu.canvas_with_file.delete_all_bookmarks` « Effacer tous les marque-pages »), exposées à quatre endroits : menu Archives de la menubar, menu contextuel du fond de la mosaïque, menu contextuel sur une vignette, menu du clic droit sur l'indicateur de la barre de statut.

- **Effacer le marque-page de cette BD** (`delete_bookmark` → `panel_widget.py::_delete_current_bookmark`) : actif seulement si `cfg.get_bookmark(state.current_file)` n'est pas `None`. Après suppression en config, rafraîchit l'overlay (`_on_bookmark_changed(None)`) sur **tous les panneaux ouverts dont `current_file` correspond au fichier concerné** (via `self._main_window._all_panels()`), pas seulement le panneau d'où l'action a été lancée.
- **Effacer tous les marque-pages** (`delete_all_bookmarks` → `_delete_all_bookmarks`) : actif seulement si `cfg.has_any_bookmark()`. Après `cfg.clear_bookmarks()`, rafraîchit l'overlay sur **tous les panneaux ouverts sans condition** (chaque marque-page en config étant effacé, peu importe quel comics est ouvert dans quel panneau).

Ces commandes ne dépendent pas du mode marque-pages. Call sites de la logique d'activation/désactivation (dupliquée quatre fois, à garder synchronisée si le comportement change) : `menubar_qt.py` (`_populate_archives_menu`), `context_menus_qt.py` (`show_canvas_context_menu`, `show_image_context_menu`, `show_bookmark_indicator_menu`).

## Pièges connus

- **Ne pas confondre `page_idx` (position parmi les images) et l'index brut dans `images_data`** — toujours passer par `img_indices = [i for i, e in enumerate(...) if e.get("is_image")]` pour convertir dans un sens ou l'autre.
- **Le garde-fou `_bookmark_popup_shown_for` doit être réinitialisé à chaque fermeture réelle du fichier**, jamais lors d'un simple rechargement de mosaïque (import, Ctrl+V, drop) — sinon soit le popup réapparaît de façon intempestive à chaque rechargement, soit il ne réapparaît jamais après une fermeture/réouverture. Point d'ajustement unique : `_close_bookmark_popup()`.
- **Lire le mode via le panneau, jamais via `get_config_manager().get_bookmarks_enabled()`** : cette méthode ne renvoie que le mode de panel1. Tout nouveau consommateur doit passer par `PanelWidget._bookmarks_enabled()` (ou un getter/callback qui y mène), sinon le panneau 2 suivrait le mode du panneau 1.
- **État par panneau vs config globale** : `current_file`, `_bookmark_popup`, `_bookmark_popup_shown_for` et le mode marque-pages sont propres à chaque `PanelWidget` ; le dict `bookmarks` en config est partagé entre les deux panneaux. La config seule ne suffit pas à synchroniser l'affichage : l'overlay visuel (`_is_bookmarked` + `refresh_bookmark_overlay`) est un état en mémoire propre à chaque panneau, donc toute écriture de marque-page (pose, déplacement, suppression) doit explicitement rafraîchir l'overlay des autres panneaux concernés (voir `_on_viewer_bookmark_changed` et la section "Suppression manuelle" ci-dessus) — se contenter d'écrire en config sans le faire laisse le ruban faux dans l'autre panneau jusqu'à un rechargement. Tout nouveau point d'écriture de marque-page doit passer par l'un de ces mécanismes. Conséquence assumée : avec le même fichier ouvert des deux côtés, un marque-page posé depuis le panneau en mode on apparaît aussi dans la mosaïque du panneau en mode off.
- **Diagnostiquer un bug de popup qui n'apparaît pas** : instrumenter avec des prints (règle CLAUDE.md, jamais de fix à l'aveugle) dans `_on_loading_finished`, `_maybe_show_bookmark_popup` (chaque `return` anticipé, y compris celui du mode off) et `ArchiveLoader.load`/`_on_finished` — utile pour distinguer un vrai double-chargement d'un simple garde-fou qui bloque à tort.
