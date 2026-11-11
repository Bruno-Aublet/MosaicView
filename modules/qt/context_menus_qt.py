"""
context_menus_qt.py — Menus contextuels (clic droit) de MosaicView.

Clic droit sur une zone vide de la mosaïque : les 8 menus de la barre, en
sous-menus, construits par les mêmes fonctions (menubar_qt.bar_menus) — leur
contenu et leur grisage sont donc toujours ceux de la barre.
Clic droit sur une ou des vignettes : le menu Images de la barre
(menubar_qt._populate_images_menu), puis les commandes propres aux pages
cliquées.
"""

from PySide6.QtWidgets import QMenu
from PySide6.QtGui import QAction

from modules.qt.localization import _
from modules.qt import state as _state_module


# ── Helpers ───────────────────────────────────────────────────────────────────

class _GuardedMenu(QMenu):
    """QMenu qui grise toute action dont le callback est une commande bloquée
    pendant une opération en arrière-plan sur le document (attribut
    mv_blocked posé par PanelWidget._build_menubar_callbacks, voir
    panel_widget.BUSY_BLOCKED_COMMANDS). La commande est de toute façon sans
    effet dans ce cas ; le grisage le montre."""

    def addAction(self, *args, **kwargs):
        action = super().addAction(*args, **kwargs)
        for arg in list(args) + list(kwargs.values()):
            busy = getattr(arg, "mv_blocked", None)
            if busy is not None and busy():
                action.setEnabled(False)
        return action


def _make_menu(parent) -> QMenu:
    from modules.qt.font_manager_qt import get_current_font as _get_current_font
    menu = _GuardedMenu(parent)
    font = _get_current_font(9)
    theme = _state_module.get_current_theme()
    menu.setStyleSheet(
        f'QMenu {{ font-family: "{font.family()}"; font-size: {font.pointSize()}pt; }} '
        f'QMenu::item:disabled {{ color: {theme["disabled"]}; }}'
    )
    return menu


def _disable_action(act: QAction) -> QAction:
    """Désactive une QAction (item simple ou sous-menu)."""
    act.setEnabled(False)
    return act


def _add_disabled(menu: QMenu, label: str) -> QAction:
    act = menu.addAction(label)
    return _disable_action(act)


# ═══════════════════════════════════════════════════════════════════════════════
# Menu canvas — zone vide
# ═══════════════════════════════════════════════════════════════════════════════

def show_canvas_context_menu(global_pos, parent, callbacks: dict):
    """Menu clic droit sur une zone vide du canvas : afficher/masquer la
    colonne d'icônes et la minimap, puis les menus de la barre."""
    from modules.qt.menubar_qt import bar_menus

    menu = _make_menu(parent)

    toolbar_visible = callbacks.get('get_toolbar_visible', lambda: True)()
    sidebar_label = (_("context_menu.canvas_with_file.hide_sidebar") if toolbar_visible
                     else _("context_menu.canvas_with_file.show_sidebar"))
    menu.addAction(sidebar_label, callbacks['toggle_toolbar'])

    minimap_visible = callbacks.get('get_minimap_visible', lambda: False)()
    minimap_label = (_("context_menu.canvas_with_file.hide_minimap") if minimap_visible
                      else _("context_menu.canvas_with_file.show_minimap"))
    menu.addAction(minimap_label, callbacks['toggle_minimap'])

    menu.addSeparator()

    for title, populate in bar_menus():
        submenu = _make_menu(menu)
        submenu.setTitle(title)
        populate(submenu, callbacks)
        menu.addMenu(submenu)

    menu.exec(global_pos)
    menu.deleteLater()


# ═══════════════════════════════════════════════════════════════════════════════
# Menu image — clic droit sur une vignette
# ═══════════════════════════════════════════════════════════════════════════════

def show_image_context_menu(global_pos, real_idx: int, parent, callbacks: dict):
    """Menu clic droit sur une vignette : le menu Images de la barre, puis les
    commandes sur les pages cliquées (mêmes libellés et mêmes conditions que
    dans la barre)."""
    from modules.qt.menubar_qt import _ as _bar_text, _add_action, _populate_images_menu
    from modules.qt.duplicate_detection_qt import has_any_duplicate
    from modules.qt.comic_info import comic_info_entry
    from modules.qt.config_manager import get_config_manager
    from modules.qt.entries import is_unreadable_file

    state = parent._state

    # Sélectionne l'élément si pas déjà sélectionné
    if real_idx not in state.selected_indices:
        callbacks['select_single'](real_idx)

    menu = _make_menu(parent)

    st = state
    has_images = bool(st.images_data)
    has_sel = bool(st.selected_indices)

    # ── Images : le menu Images de la barre ───────────────────────────────────
    _populate_images_menu(menu, callbacks)
    menu.addSeparator()

    _add_action(menu, _bar_text("menu.show_duplicates"), callbacks.get("show_duplicates_window"),
                enabled=has_images and has_any_duplicate(st))
    menu.addSeparator()

    # ComicInfo.xml du document (à la racine ou dans le dossier des pages) :
    # actif seulement sur lui
    clicked_entry = st.images_data[real_idx] if real_idx < len(st.images_data) else None
    is_comicinfo = clicked_entry is not None and clicked_entry is comic_info_entry(st)
    _add_action(menu, _bar_text("comicvine.edit_comicinfo"), callbacks.get("edit_comicinfo"),
                enabled=is_comicinfo)

    # Fichier .nfo : actif seulement sur un .nfo lisible
    edit_nfo = callbacks.get("edit_nfo")
    nfo_busy = getattr(edit_nfo, "mv_blocked", None)
    can_edit_nfo = (clicked_entry is not None and edit_nfo is not None
                    and clicked_entry.get("orig_name", "").lower().endswith(".nfo")
                    and not is_unreadable_file(clicked_entry)
                    and not (nfo_busy is not None and nfo_busy()))
    _add_action(menu, _bar_text("nfo.menu_item_edit"),
                (lambda checked=False, e=clicked_entry: edit_nfo(e)) if can_edit_nfo else None,
                enabled=can_edit_nfo)
    menu.addSeparator()

    cfg = get_config_manager()
    has_bookmark_for_current = bool(
        st.current_file and cfg and cfg.get_bookmark(st.current_file) is not None
    )
    _add_action(menu, _bar_text("context_menu.canvas_with_file.delete_bookmark"),
                callbacks.get("delete_bookmark"), enabled=has_bookmark_for_current)
    menu.addSeparator()

    # ── Sélection (menus Fichier et Édition de la barre) ──────────────────────
    print_avail = bool(callbacks.get("PRINT_AVAILABLE"))
    _add_action(menu, _bar_text("buttons.save_selection"), callbacks.get("save_selection_as_cbz"),
                enabled=has_images and has_sel)
    _add_action(menu, _bar_text("buttons.save_to_folder"), callbacks.get("save_selection_to_folder"),
                enabled=has_images and has_sel)
    _add_action(menu, _bar_text("buttons.print_selection"), callbacks.get("print_selection"),
                enabled=print_avail and has_sel)
    menu.addSeparator()

    _add_action(menu, _bar_text("buttons.copy"), callbacks.get("copy_selected"), "Ctrl+C",
                enabled=has_sel)
    _add_action(menu, _bar_text("buttons.cut"), callbacks.get("cut_selected"), "Ctrl+X",
                enabled=has_sel)
    menu.addSeparator()

    _add_action(menu, _bar_text("menu.delete"), callbacks.get("delete_selected"), "Del",
                enabled=has_sel)
    _add_action(menu, _bar_text("menu.invert_selection"), callbacks.get("invert_selection"),
                "Ctrl+I", enabled=has_sel)
    _add_action(menu, _bar_text("menu.select_all"), callbacks.get("select_all"), "Ctrl+A",
                enabled=has_images)
    _add_action(menu, _bar_text("menu.deselect"), callbacks.get("clear_selection"), "Escape",
                enabled=has_sel)

    menu.exec(global_pos)
    menu.deleteLater()


# ═══════════════════════════════════════════════════════════════════════════════
# Menu dossier virtuel (clic droit sur icône de dossier)
# ═══════════════════════════════════════════════════════════════════════════════

def show_dir_context_menu(global_pos, parent, callbacks: dict):
    """
    Menu clic droit sur un dossier virtuel dans la mosaïque.
    """
    menu = _make_menu(parent)
    menu.addAction(_("context_menu.canvas_with_file.flatten"), callbacks['flatten_directories'])
    menu.exec(global_pos)
    menu.deleteLater()


# ═══════════════════════════════════════════════════════════════════════════════
# Menu indicateur marque-pages (clic droit sur l'icône de la barre de statut)
# ═══════════════════════════════════════════════════════════════════════════════

def show_bookmark_indicator_menu(global_pos, parent, callbacks: dict):
    """Menu clic droit sur l'indicateur de marque-pages de la barre de statut.
    Mêmes conditions d'activation que les entrées du menu Archives."""
    state = parent._state
    menu = _make_menu(parent)
    from modules.qt.config_manager import get_config_manager
    cfg = get_config_manager()
    has_bookmark_for_current = bool(
        state.current_file and cfg and cfg.get_bookmark(state.current_file) is not None
    )
    has_any_bookmark = bool(cfg and cfg.has_any_bookmark())
    if has_bookmark_for_current:
        menu.addAction(_("context_menu.canvas_with_file.delete_bookmark"),
                       callbacks.get('delete_bookmark', lambda: None))
    else:
        _add_disabled(menu, _("context_menu.canvas_with_file.delete_bookmark"))
    if has_any_bookmark:
        menu.addAction(_("context_menu.canvas_with_file.delete_all_bookmarks"),
                       callbacks.get('delete_all_bookmarks', lambda: None))
    else:
        _add_disabled(menu, _("context_menu.canvas_with_file.delete_all_bookmarks"))
    menu.exec(global_pos)
    menu.deleteLater()
