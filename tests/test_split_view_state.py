"""
test_split_view_state.py — Isolation de l'état entre les deux panneaux (split-view).

Couvre les deux fonctions pures de modules/qt/state.py :
  - resolve_state_after_wrapped_call : valeur redonnée au singleton `state` à
    la fin d'un callback enveloppé par PanelWidget._build_menubar_callbacks ;
  - AppState.reset_per_file_tool_memory : purge, à la fermeture d'un fichier,
    de la mémoire des outils de la visionneuse indexée par page/history_index.
"""
from modules.qt.state import AppState, resolve_state_after_wrapped_call


# ── resolve_state_after_wrapped_call ────────────────────────────────────────

def test_split_closed_from_panel2_menu_follows_new_active_panel():
    # Fermeture du split depuis le menu du panneau 2 : prev = panel2 (cliqué),
    # le callback rend le panneau 1 actif → le singleton doit suivre panel1,
    # même si panel2 (masqué, pas détruit) existe toujours.
    p1, p2 = AppState(), AppState()
    result = resolve_state_after_wrapped_call(
        prev=p2, panel_state=p2, active_before=p2, active_after=p1,
        valid_states=[p1, p2],
    )
    assert result is p1


def test_unchanged_active_panel_restores_prev():
    p1, p2 = AppState(), AppState()
    result = resolve_state_after_wrapped_call(
        prev=p1, panel_state=p2, active_before=p1, active_after=p1,
        valid_states=[p1, p2],
    )
    assert result is p1


def test_prev_from_unknown_panel_falls_back_to_panel_state():
    p1, orphan = AppState(), AppState()
    result = resolve_state_after_wrapped_call(
        prev=orphan, panel_state=p1, active_before=None, active_after=None,
        valid_states=[p1],
    )
    assert result is p1


def test_unknown_active_panel_restores_valid_prev():
    p1, p2 = AppState(), AppState()
    result = resolve_state_after_wrapped_call(
        prev=p2, panel_state=p1, active_before=None, active_after=None,
        valid_states=[p1, p2],
    )
    assert result is p2


# ── AppState.reset_per_file_tool_memory ─────────────────────────────────────

_PER_FILE_DICTS = (
    "sharpness_value_by_history_index",
    "unsharp_value_by_history_index",
    "brightness_value_by_history_index",
    "saturation_value_by_history_index",
    "remove_colors_value_by_history_index",
    "compression_value_by_history_index",
    "levels_value_by_history_index",
    "color_depth_original_bytes_by_page",
    "effect_original_bytes_by_page",
    "effect_key_by_page",
    "image_mode_original_bytes_by_page",
)


def test_reset_per_file_tool_memory_clears_every_per_page_dict():
    st = AppState()
    for name in _PER_FILE_DICTS:
        getattr(st, name)[3] = "page 3 de l'ancien fichier"
    st.reset_per_file_tool_memory()
    for name in _PER_FILE_DICTS:
        assert getattr(st, name) == {}, name


def test_reset_per_file_tool_memory_keeps_dict_identity_and_crop_mask():
    st = AppState()
    before = st.color_depth_original_bytes_by_page
    st.crop_mask_px = (1, 2, 3, 4)
    st.reset_per_file_tool_memory()
    assert st.color_depth_original_bytes_by_page is before
    assert st.crop_mask_px == (1, 2, 3, 4)


def test_reset_per_file_tool_memory_does_not_touch_other_panel():
    p1, p2 = AppState(), AppState()
    p2.effect_key_by_page[0] = "sepia"
    p1.reset_per_file_tool_memory()
    assert p2.effect_key_by_page == {0: "sepia"}


def test_every_per_page_dict_of_appstate_is_purged():
    # Garde-fou : un nouveau dict *_by_history_index / *_by_page ajouté à
    # AppState doit aussi être purgé à la fermeture du fichier.
    st = AppState()
    per_file = [n for n, v in vars(st).items()
                if isinstance(v, dict) and (n.endswith("_by_history_index")
                                            or n.endswith("_by_page"))]
    for name in per_file:
        getattr(st, name)[0] = object()
    st.reset_per_file_tool_memory()
    leftovers = [n for n in per_file if getattr(st, n)]
    assert not leftovers, f"Non purgés par reset_per_file_tool_memory : {leftovers}"
