"""Annuler/Refaire bloqués pendant une opération en arrière-plan sur le document.

Sans ce blocage, un Annuler pendant une rotation, un redressement, un
redimensionnement ou une conversion restaurerait un snapshot que le worker
écraserait ensuite, et son save_state de fin effacerait la branche Refaire.
"""
from modules.qt.state import AppState
from modules.qt.undo_redo import save_state_data
from modules.qt.undo_redo_qt import redo_action_qt, undo_action_qt


def make_entry(data, name):
    return {"orig_name": name, "bytes": data, "extension": ".bmp", "is_image": True}


def state_with_two_steps():
    st = AppState()
    st.images_data = [make_entry(b"A", "p01.bmp")]
    save_state_data(st, force=True)
    st.images_data.append(make_entry(b"B", "p02.bmp"))
    save_state_data(st, force=True)
    return st


def noop(*_args, **_kwargs):
    pass


def test_begin_and_end_operation():
    st = AppState()
    token = object()
    assert not st.operation_running()
    st.begin_operation(token)
    assert st.operation_running()
    st.end_operation(token)
    assert not st.operation_running()


def test_end_operation_removes_only_its_token():
    st = AppState()
    first, second = object(), object()
    st.begin_operation(first)
    st.begin_operation(second)
    st.end_operation(first)
    assert st.operation_running()
    st.end_operation(second)
    assert not st.operation_running()


def test_undo_is_ignored_during_operation():
    st = state_with_two_steps()
    st.begin_operation(object())
    undo_action_qt(st, noop, noop, noop, noop)
    assert st.history_index == 1
    assert len(st.images_data) == 2


def test_redo_is_ignored_during_operation():
    st = state_with_two_steps()
    st.history_index = 0
    st.begin_operation(object())
    redo_action_qt(st, noop, noop, noop, noop)
    assert st.history_index == 0


def test_undo_works_again_after_operation():
    st = state_with_two_steps()
    token = object()
    st.begin_operation(token)
    st.end_operation(token)
    undo_action_qt(st, noop, noop, noop, noop)
    assert st.history_index == 0


# ---------------------------------------------------------------------------
# Commandes bloquées pendant une opération en arrière-plan
# (panel_widget.BUSY_BLOCKED_COMMANDS, icon_toolbar_qt._BUSY_BLOCKED_ICONS)
# ---------------------------------------------------------------------------

import ast
from pathlib import Path

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


def test_blocked_commands_exist():
    """Une clé mal orthographiée ne bloquerait rien, sans aucune erreur."""
    blocked = _frozenset_constant("panel_widget.py", "BUSY_BLOCKED_COMMANDS")
    commands = _dict_keys_in("menubar_callbacks_qt.py", "build_menubar_callbacks")
    assert blocked <= commands, sorted(blocked - commands)


def test_blocked_icons_exist():
    blocked = _frozenset_constant("icon_toolbar_qt.py", "_BUSY_BLOCKED_ICONS")
    icons = _dict_keys_in("icon_toolbar_qt.py", "build_icon_toolbar")
    assert blocked <= icons, sorted(blocked - icons)


# Commandes (et valeurs) de build_menubar_callbacks laissées libres pendant une
# opération en arrière-plan, par motif. Toute nouvelle clé doit être classée,
# ici ou dans panel_widget.BUSY_BLOCKED_COMMANDS : une commande qui modifie ou
# enregistre le document, oubliée, mêlerait ses changements à ceux du worker.
FREE_WHILE_BUSY = {
    "valeur ou lecture d'un état, pas une commande": {
        "MAX_FONT_SIZE_OFFSET", "MIN_FONT_SIZE_OFFSET", "PRINT_AVAILABLE", "can_redo",
        "can_undo", "get_available_languages", "get_bookmarks_enabled",
        "get_current_image_count", "get_current_language", "get_dark_mode",
        "get_font_manager", "get_font_size_offset", "get_is_fullscreen", "get_languages",
        "get_minimap_visible", "get_recent_files", "get_split_active", "get_thumb_size",
        "get_toolbar_visible", "has_subdirectory_structure", "scan_available",
        "scan_log_exists"},
    "lecture, sélection, copie, impression, affichage : ne modifient pas les pages": {
        "clear_selection", "invert_selection", "select_all", "select_single",
        "copy_selected", "copy_archive_to_clipboard", "print_all", "print_selection",
        "render_mosaic", "toggle_minimap", "toggle_toolbar", "toggle_fullscreen",
        "increase_thumb_size", "decrease_thumb_size", "increase_font_size",
        "decrease_font_size", "toggle_theme", "change_language", "set_language",
        "toggle_split_ui"},
    "bloqués par undo_action_qt / redo_action_qt eux-mêmes (tests ci-dessus)": {
        "undo_action", "redo_action"},
    "fermeture : arrête l'opération (cancel_operations_on_close, doc_generation)": {
        "close_file", "on_window_close"},
    "lots : fichiers du disque, pas les pages du document ouvert": {
        "batch_convert_cb7_to_cbz", "batch_convert_cbr_to_cbz", "batch_convert_cbt_to_cbz",
        "batch_convert_img_to_cbz", "batch_convert_pdf_to_cbz", "batch_metadata"},
    "fenêtre qui se protège elle-même (delete_entries_by_index relit operation_running)": {
        "show_duplicates_window"},
    "réglages, aide, liens, fichiers de l'application hors document": {
        "change_apikey", "check_for_updates", "clear_clipboard_files", "clear_config_file",
        "clear_recent_files", "clear_recent_files_about", "clear_temp_files",
        "copy_mail_address", "delete_all_bookmarks", "delete_bookmark",
        "export_piqad_font", "export_tengwar_fonts", "open_config_folder", "open_github",
        "open_library", "open_mail", "open_scan_log", "open_temp_folder", "open_webpage",
        "open_zip_compression_dialog", "reset_settings", "reset_to_defaults",
        "reveal_scan_log", "save_all_icons", "show_changelog", "show_distillat_dialog",
        "show_donation_dialog", "show_error_log", "show_full_7zip_license",
        "show_full_gpl_license", "show_full_piqad_license", "show_full_tengwar_license",
        "show_full_unrar_license", "show_license_dialog", "show_user_guide",
        "toggle_bookmarks_enabled"},
}


def test_every_command_is_classified_for_background_operations():
    commands = _dict_keys_in("menubar_callbacks_qt.py", "build_menubar_callbacks")
    blocked = _frozenset_constant("panel_widget.py", "BUSY_BLOCKED_COMMANDS")
    free = set().union(*FREE_WHILE_BUSY.values())
    assert len(commands) > 100, "le scan ne voit plus les commandes"
    unclassified = sorted(commands - blocked - free)
    assert not unclassified, (
        "Commande à classer : modifie-t-elle ou enregistre-t-elle le document ? "
        "Si oui, l'ajouter à panel_widget.BUSY_BLOCKED_COMMANDS (et son icône à "
        "icon_toolbar_qt._BUSY_BLOCKED_ICONS), sinon à FREE_WHILE_BUSY avec le motif : "
        + ", ".join(unclassified))
    assert not (blocked & free), sorted(blocked & free)
    assert free <= commands, f"Commandes disparues : {sorted(free - commands)}"


def _panel_callbacks(monkeypatch, raw):
    """Callbacks d'un panneau, enveloppés par le vrai
    PanelWidget._build_menubar_callbacks (extrait par AST)."""
    import types
    import ast_loader
    from modules.qt import state as state_module
    blocked = ast_loader.load("panel_widget.py", {"BUSY_BLOCKED_COMMANDS"})["BUSY_BLOCKED_COMMANDS"]
    Base = ast_loader.load_methods("panel_widget.py", "PanelWidget", {"_build_menubar_callbacks"},
                                   _build_menubar_callbacks_module=lambda panel: dict(raw),
                                   BUSY_BLOCKED_COMMANDS=blocked)
    other = types.SimpleNamespace(_state=AppState())
    monkeypatch.setattr(state_module, "state", other._state)   # panneau actif : l'autre

    class Panel(Base):
        def __init__(self):
            self._state = AppState()
            self._main_window = types.SimpleNamespace(
                _active_panel=other, _all_panels=lambda: [other, self])
    panel = Panel()
    return panel, other, panel._build_menubar_callbacks()


def test_blocked_command_does_nothing_and_is_greyed_while_busy(monkeypatch):
    calls = []
    panel, _other, cb = _panel_callbacks(monkeypatch, {
        "rotate_selected_right": lambda: calls.append("rotation"),
        "select_all": lambda: calls.append("sélection")})
    token = object()
    panel._state.begin_operation(token)
    assert cb["rotate_selected_right"].mv_blocked() is True
    assert cb["rotate_selected_right"]() is None
    cb["select_all"]()                               # commande libre : exécutée
    assert calls == ["sélection"]
    assert not hasattr(cb["select_all"], "mv_blocked")
    panel._state.end_operation(token)
    assert cb["rotate_selected_right"].mv_blocked() is False
    cb["rotate_selected_right"]()
    assert calls == ["sélection", "rotation"]


def test_command_runs_on_its_own_panel_and_gives_the_singleton_back(monkeypatch):
    from modules.qt import state as state_module
    seen = []
    panel, other, cb = _panel_callbacks(monkeypatch, {
        "select_all": lambda: seen.append(state_module.state)})
    cb["select_all"]()
    assert seen == [panel._state]                    # le panneau de la commande, pas l'actif
    assert state_module.state is other._state        # singleton rendu
    assert cb["state"] is panel._state


def test_operation_on_the_other_panel_blocks_nothing_here(monkeypatch):
    calls = []
    _panel, other, cb = _panel_callbacks(monkeypatch, {
        "rotate_selected_right": lambda: calls.append("rotation")})
    other._state.begin_operation(object())
    cb["rotate_selected_right"]()
    assert calls == ["rotation"]


def _document_writer(body):
    """_write_document_with_progress (file_operations_qt.py, qui importe
    PySide6) extraite par AST et exécutée avec `body` à la place de
    l'écriture réelle."""
    tree = ast.parse((ROOT / "modules" / "qt" / "file_operations_qt.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "_write_document_with_progress")
    ns = {"_write_document_unlocked": body}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "file_operations_qt.py", "exec"), ns)
    return ns["_write_document_with_progress"]


def test_save_blocks_document_commands_while_writing():
    """Pendant l'écriture (qui traite les événements), un second
    enregistrement ou une modification des pages est sans effet."""
    st = AppState()
    seen = []
    write = _document_writer(lambda *_args: seen.append(st.operation_running()) or True)
    assert write("x.cbz", st, None, 0) is True
    assert seen == [True]
    assert not st.operation_running()


def test_addition_during_write_is_not_marked_saved():
    """Une page numérisée arrivée pendant l'écriture n'est pas dans le
    fichier : le document ne doit pas être déclaré sauvegardé."""
    st = AppState()
    st.images_data = [make_entry(b"A", "p01.bmp")]

    def body(*_args):
        st.run_when_idle(lambda: st.images_data.append(make_entry(b"S", "scan.bmp")))
        return True

    write = _document_writer(body)
    assert write("x.cbz", st, None, 0) is False
    assert len(st.images_data) == 2


# ---------------------------------------------------------------------------
# Ajouts asynchrones pendant une opération (AppState.run_when_idle)
# ---------------------------------------------------------------------------

def test_addition_runs_immediately_when_idle():
    st = AppState()
    done = []
    st.run_when_idle(lambda: done.append(1))
    assert done == [1]


def test_addition_waits_for_the_end_of_the_operation():
    """Appliqué pendant une rotation, l'ajout serait effacé par son annulation
    (retour au sommet de l'historique pris avant elle)."""
    st = AppState()
    token = object()
    done = []
    st.begin_operation(token)
    st.run_when_idle(lambda: done.append(1))
    assert done == []
    st.end_operation(token)
    assert done == [1]
    assert st.pending_when_idle == []


def test_addition_waits_for_the_last_operation():
    st = AppState()
    first, second = object(), object()
    done = []
    st.begin_operation(first)
    st.begin_operation(second)
    st.run_when_idle(lambda: done.append(1))
    st.end_operation(first)
    assert done == []
    st.end_operation(second)
    assert done == [1]


def test_failed_addition_does_not_drop_the_next_ones(capsys):
    """Un ajout en attente qui lève une exception ne fait perdre ni les ajouts
    suivants (une page numérisée), ni la fin de l'opération qui les applique."""
    st = AppState()
    token = object()
    done = []

    def broken():
        raise RuntimeError("ajout en échec")

    st.begin_operation(token)
    st.run_when_idle(broken)
    st.run_when_idle(lambda: done.append("page numérisée"))
    st.end_operation(token)
    assert done == ["page numérisée"]
    assert not st.operation_running()
    assert "ajout en échec" in capsys.readouterr().err


def test_addition_for_a_closed_document_is_dropped():
    st = AppState()
    token = object()
    done = []
    st.begin_operation(token)
    st.run_when_idle(lambda: done.append(1))
    st.doc_generation += 1
    st.end_operation(token)
    assert done == []


def test_failed_save_releases_the_lock():
    st = AppState()

    def fail(*_args):
        raise OSError(28, "No space left on device")

    write = _document_writer(fail)
    try:
        write("x.cbz", st, None, 0)
    except OSError:
        pass
    assert not st.operation_running()
