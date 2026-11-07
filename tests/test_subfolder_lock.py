"""Dans un sous-dossier de la mosaïque, on ne peut que descendre dans un
dossier, remonter ou aplatir les dossiers (skill flatten-directories, « Dans un
sous-dossier »). Vérifié ici :
- chaque commande du panneau est bloquée dans un sous-dossier
  (panel_widget.SUBFOLDER_BLOCKED_COMMANDS) ou déclarée libre avec son motif :
  une commande nouvelle, non classée, fait échouer le test ;
- touches de la mosaïque, dépôt de fichiers, raccourcis globaux et clic droit
  sur une icône sans effet.
Souris et colonne d'icônes : tests/test_subfolder_right_click.py ; commandes
enveloppées : tests/test_operation_running.py.

Ces modules importent PySide6 : les méthodes sont extraites par AST, Qt est
remplacé par des objets simples.
"""
import ast
import os
import types
from pathlib import Path

import ast_loader

ROOT = Path(__file__).resolve().parent.parent


def _frozenset_constant(module, name):
    tree = ast.parse((ROOT / "modules" / "qt" / module).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return set(ast.literal_eval(node.value.args[0]))
    raise AssertionError(f"{name} introuvable dans {module}")


def _dict_keys_in(module, function):
    tree = ast.parse((ROOT / "modules" / "qt" / module).read_text(encoding="utf-8"))
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Dict):
                    keys.update(k.value for k in sub.keys if isinstance(k, ast.Constant))
    return keys


# ── Classement des commandes ─────────────────────────────────────────────────

# Commandes (et valeurs) de build_menubar_callbacks laissées libres dans un
# sous-dossier, par motif. Toute nouvelle clé doit être classée, ici ou dans
# panel_widget.SUBFOLDER_BLOCKED_COMMANDS : une commande sur la BD, oubliée,
# agirait depuis un sous-dossier.
FREE_IN_SUBFOLDER = {
    "le seul geste permis avec la navigation": {"flatten_directories"},
    "valeur ou lecture d'un état, pas une commande": {
        "MAX_FONT_SIZE_OFFSET", "MIN_FONT_SIZE_OFFSET", "PRINT_AVAILABLE", "can_redo",
        "can_undo", "get_available_languages", "get_bookmarks_enabled",
        "get_current_image_count", "get_current_language", "get_dark_mode",
        "get_font_manager", "get_font_size_offset", "get_is_fullscreen", "get_languages",
        "get_minimap_visible", "get_panel", "get_recent_files", "get_split_active",
        "get_thumb_size", "get_toolbar_visible", "has_subdirectory_structure",
        "scan_available", "scan_log_exists"},
    "affichage seul : ne touchent pas à la BD": {
        "clear_selection", "render_mosaic", "toggle_minimap", "toggle_toolbar",
        "toggle_fullscreen", "increase_thumb_size", "decrease_thumb_size",
        "increase_font_size", "decrease_font_size", "toggle_theme", "change_language",
        "set_language", "toggle_split_ui"},
    "appelée par le menu du clic droit, qui ne s'ouvre pas dans un sous-dossier": {
        "select_single"},
    "fermeture de l'application": {"on_window_close"},
    "lots : fichiers du disque, pas la BD ouverte": {
        "batch_convert_cb7_to_cbz", "batch_convert_cbr_to_cbz", "batch_convert_cbt_to_cbz",
        "batch_convert_img_to_cbz", "batch_convert_pdf_to_cbz", "batch_metadata"},
    "réglages, aide, liens, fichiers de l'application hors BD": {
        "change_apikey", "check_for_updates", "clear_clipboard_files", "clear_config_file",
        "clear_recent_files", "clear_recent_files_about", "clear_temp_files",
        "copy_mail_address", "export_piqad_font", "export_tengwar_fonts",
        "open_config_folder", "open_github", "open_library", "open_mail", "open_scan_log",
        "open_temp_folder", "open_webpage", "open_zip_compression_dialog",
        "reset_settings", "reset_to_defaults", "reveal_scan_log", "save_all_icons",
        "show_changelog", "show_distillat_dialog", "show_donation_dialog",
        "show_error_log", "show_full_7zip_license", "show_full_gpl_license",
        "show_full_piqad_license", "show_full_tengwar_license", "show_full_unrar_license",
        "show_license_dialog", "show_user_guide", "toggle_bookmarks_enabled"},
}


def test_every_command_is_classified_for_subfolders():
    commands = _dict_keys_in("menubar_callbacks_qt.py", "build_menubar_callbacks")
    blocked = _frozenset_constant("panel_widget.py", "SUBFOLDER_BLOCKED_COMMANDS")
    free = set().union(*FREE_IN_SUBFOLDER.values())
    assert len(commands) > 100, "le scan ne voit plus les commandes"
    unclassified = sorted(commands - blocked - free)
    assert not unclassified, (
        "Commande à classer : agit-elle sur la BD ouverte ou sa sélection ? Si oui, "
        "l'ajouter à panel_widget.SUBFOLDER_BLOCKED_COMMANDS, sinon à "
        "FREE_IN_SUBFOLDER avec le motif : " + ", ".join(unclassified))
    assert not (blocked & free), sorted(blocked & free)
    assert blocked <= commands, f"Clés bloquées inconnues : {sorted(blocked - commands)}"
    assert free <= commands, f"Commandes disparues : {sorted(free - commands)}"


# ── Touches de la mosaïque ───────────────────────────────────────────────────

class Thumb:
    real_idx = 0


class Folder:
    entry = {"orig_name": "Aaaa/Cccc/"}


KEYS = types.SimpleNamespace(
    Key_Escape="Échap", Key_Right="droite", Key_Left="gauche", Key_Down="bas", Key_Up="haut",
    Key_Home="début", Key_End="fin", Key_Return="Entrée", Key_Enter="Entrée pavé",
    Key_A="A", Key_I="I", Key_Space="Espace", Key_Delete="Suppr", Key_Backspace="Retour",
    ControlModifier=1, ShiftModifier=2)

Keys = ast_loader.load_methods(
    "mosaic_canvas.py", "MosaicCanvas", {"keyPressEvent", "_in_subfolder"},
    Qt=KEYS, QGraphicsProxyWidget=type("Proxy", (), {}), NameEdit=type("NameEdit", (), {}),
    ThumbnailItem=Thumb, DirItem=Folder)


class QtBase:
    def keyPressEvent(self, event):
        pass


class KeyCanvas(Keys, QtBase):
    def __init__(self, directory, focused):
        self._state = types.SimpleNamespace(current_directory=directory,
                                            images_data=[{"is_image": True}])
        self._scene = types.SimpleNamespace(focusItem=lambda: None)
        self._items = [focused, Thumb()]
        self._focused_idx = 0
        self._focused = focused
        self.done = []
        self._open_image_viewer_callback = lambda idx: self.done.append("visionneuse")
        self._open_non_image_callback = lambda entry: self.done.append("fichier")
        self._delete_selected_callback = lambda: self.done.append("suppression")
        self.status_changed = types.SimpleNamespace(emit=lambda: None)

    def _cols(self):
        return 2

    def _item_by_visual(self, visual):
        return self._items[visual]

    def _select_all(self):
        self.done.append("tout sélectionner")

    def _invert_selection(self):
        self.done.append("inverser")

    def _toggle_item(self, item):
        self.done.append("sélection")

    def _select_range(self, start, end):
        self.done.append("plage")

    def _set_focus(self, visual):
        self._focused_idx = visual

    def _scroll_to(self, item):
        pass

    def navigate_to_directory(self, name):
        self.done.append(("entrer", name))

    def navigate_up(self):
        self.done.append("remonter")

    def _clear_selection(self):
        pass


def press(key, modifiers=0):
    return types.SimpleNamespace(key=lambda: key, modifiers=lambda: modifiers)


def test_keys_that_act_on_pages_do_nothing_in_a_subfolder():
    canvas = KeyCanvas("Aaaa/", Thumb())
    for event in (press(KEYS.Key_Return), press(KEYS.Key_Delete), press(KEYS.Key_Backspace),
                  press(KEYS.Key_A, KEYS.ControlModifier), press(KEYS.Key_I, KEYS.ControlModifier),
                  press(KEYS.Key_Space), press(KEYS.Key_Right, KEYS.ShiftModifier)):
        canvas.keyPressEvent(event)
    assert canvas.done == []


def test_arrows_and_enter_on_a_folder_still_work_in_a_subfolder():
    canvas = KeyCanvas("Aaaa/", Folder())
    canvas.keyPressEvent(press(KEYS.Key_Return))
    assert canvas.done == [("entrer", "Aaaa/Cccc/")]
    canvas.keyPressEvent(press(KEYS.Key_Right))
    assert canvas._focused_idx == 1


def test_keys_still_act_on_pages_at_the_root():
    canvas = KeyCanvas("", Thumb())
    canvas.keyPressEvent(press(KEYS.Key_Return))
    canvas.keyPressEvent(press(KEYS.Key_Delete))
    assert canvas.done == ["visionneuse", "suppression"]


# ── Dépôt de fichiers ────────────────────────────────────────────────────────

Drops = ast_loader.load_methods(
    "mosaic_canvas.py", "MosaicCanvas", {"dragEnterEvent", "_in_subfolder"})


def test_nothing_can_be_dropped_into_a_subfolder():
    canvas = Drops()
    canvas._state = types.SimpleNamespace(current_directory="Aaaa/")
    ignored = []
    event = types.SimpleNamespace(ignore=lambda: ignored.append(True),
                                  acceptProposedAction=lambda: ignored.append(False))
    canvas.dragEnterEvent(event)
    assert ignored == [True]


def test_files_dropped_on_a_panel_showing_a_subfolder_are_left_alone():
    import configparser
    Panel = ast_loader.load_methods("panel_widget.py", "PanelWidget", {"_handle_dropped_paths"},
                                    os=os, configparser=configparser)
    panel = Panel()
    panel._state = types.SimpleNamespace(current_directory="Aaaa/")
    opened = []
    panel._open_library_db = opened.append
    assert panel._handle_dropped_paths([r"C:\tmp\Aaaa.mvdb", r"C:\tmp\Bbbb.cbz"]) is None
    assert opened == []


# ── Raccourcis globaux ───────────────────────────────────────────────────────

MAIN = ROOT / "MosaicView.py"
OnComic = ast_loader.load_methods(os.path.relpath(MAIN, ast_loader.QT_DIR), "MainWindow", {"_on_comic"})


def test_global_shortcuts_on_the_comic_do_nothing_in_a_subfolder():
    window = OnComic()
    done = []
    window._active_panel = types.SimpleNamespace(_state=types.SimpleNamespace(current_directory="Aaaa/"))
    window._on_comic(lambda panel: done.append("commande"))
    window._active_panel._state.current_directory = ""
    window._on_comic(lambda panel: done.append("commande"))
    assert done == ["commande"]


# Raccourcis globaux qui touchent le panneau actif sans agir sur la BD
SHORTCUTS_FREE_IN_SUBFOLDER = {
    "self._active_panel._canvas.render_mosaic()",    # F5 : réaffichage
    "self._active_panel._on_escape()",               # Échap : désélection, fermeture d'un aperçu
}


def test_every_global_shortcut_on_the_comic_goes_through_on_comic():
    """Un raccourci branché directement sur le panneau actif agirait depuis un
    sous-dossier. Il passe par MainWindow._on_comic, ou figure dans
    SHORTCUTS_FREE_IN_SUBFOLDER s'il ne touche pas à la BD."""
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    connected = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "connect"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "activated" and node.args):
            connected.append(ast.unparse(node.args[0]))
    assert len(connected) >= 9, "le scan ne voit plus les raccourcis"
    direct = [c for c in connected if "_active_panel" in c and "_on_comic" not in c
              and not any(free in c for free in SHORTCUTS_FREE_IN_SUBFOLDER)]
    assert not direct, direct
    assert sum("_on_comic" in c for c in connected) >= 7


# ── Clic droit sur une icône ─────────────────────────────────────────────────

IconClick = ast_loader.load_methods(
    "icon_toolbar_qt.py", "IconLabel", {"mousePressEvent"},
    Qt=types.SimpleNamespace(LeftButton="gauche", RightButton="droit"))


def test_right_click_on_an_icon_does_nothing_in_a_subfolder():
    def icon(in_subfolder):
        label = IconClick()
        label.icon_id = "renumber"
        label.toggled = []
        label._toolbar = types.SimpleNamespace(
            _state_getters={"in_subfolder": lambda: in_subfolder},
            _callbacks={"toggle_renumber_mode": lambda: label.toggled.append(True)})
        label.mousePressEvent(types.SimpleNamespace(button=lambda: "droit"))
        return label.toggled

    assert icon(True) == []
    assert icon(False) == [True]
