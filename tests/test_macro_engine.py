import json
import os
from types import SimpleNamespace

from modules.qt import macro_engine
from modules.qt.macro_engine import (
    _apply_restore_step,
    _remember_tool_start,
    history_top,
    load_macro,
    reading_context_changed,
    rename_macro,
    rollback_macro_reading,
    sync_recorded_steps,
)


def make_state(n_history=2, page_bytes=b"P"):
    history = [{"entries": i} for i in range(n_history)]
    return SimpleNamespace(
        history=history,
        history_index=n_history - 1,
        images_data=[{"bytes": page_bytes}],
        modified=False,
    )


def make_viewer(state):
    return SimpleNamespace(
        callbacks={"state": state},
        current_idx=0,
        display_image=lambda **kwargs: None,
    )


def step(name):
    return {"tool": name, "params": {}, "label_key": "macro.step_" + name, "label_args": {}}


def start_reading(viewer, state, pushed_before=False, touched=()):
    """Contexte posé par run_macro_on_entries au début d'une lecture."""
    viewer._macro_read_pushed_before = pushed_before
    viewer._macro_read_doc_generation = getattr(state, "doc_generation", 0)
    viewer._macro_read_expected_top = history_top(state)
    viewer._macro_read_stop_reason = None
    viewer._macro_read_page_start = {id(e): (e, e["bytes"]) for e in touched}


# ── Rollback d'une lecture interrompue par la fermeture de la visionneuse ───

def test_rollback_keeps_history_when_nothing_was_pushed_before():
    state = make_state(n_history=3, page_bytes=b"before")
    viewer = make_viewer(state)
    start_reading(viewer, state, touched=state.images_data)
    state.images_data[0]["bytes"] = b"after"

    assert rollback_macro_reading(viewer) is True

    assert state.images_data[0]["bytes"] == b"before"
    assert len(state.history) == 3
    assert state.history_index == 2


def test_rollback_pops_the_state_pushed_before_reading():
    state = make_state(n_history=3)
    viewer = make_viewer(state)
    start_reading(viewer, state, pushed_before=True)

    rollback_macro_reading(viewer)

    assert len(state.history) == 2
    assert state.history_index == 1


def test_rollback_restores_a_page_moved_since_the_start():
    state = make_state(page_bytes=b"before")
    page = state.images_data[0]
    viewer = make_viewer(state)
    start_reading(viewer, state, touched=[page])
    page["bytes"] = b"after"
    state.images_data.insert(0, {"bytes": b"other"})  # page décalée, sans historique

    rollback_macro_reading(viewer)

    assert page["bytes"] == b"before"
    assert state.images_data[0]["bytes"] == b"other"


def test_rollback_does_nothing_on_a_closed_or_replaced_document():
    state = make_state(page_bytes=b"before")
    state.doc_generation = 0
    viewer = make_viewer(state)
    start_reading(viewer, state, pushed_before=True, touched=state.images_data)
    state.images_data[0]["bytes"] = b"after"
    state.doc_generation = 1  # force_close_file / chargement d'un autre fichier

    assert rollback_macro_reading(viewer) is False
    assert state.images_data[0]["bytes"] == b"after"
    assert len(state.history) == 2


def test_rollback_does_nothing_after_a_change_made_from_the_mosaic():
    state = make_state(page_bytes=b"before")
    viewer = make_viewer(state)
    start_reading(viewer, state, pushed_before=True, touched=state.images_data)
    state.images_data[0]["bytes"] = b"after"
    state.history.append({"entries": "deletion"})  # action de la mosaïque
    state.history_index += 1

    assert rollback_macro_reading(viewer) is False
    assert state.images_data[0]["bytes"] == b"after"
    assert len(state.history) == 3


# ── Contexte du document pendant une lecture ────────────────────────────────

def test_reading_context_detects_closing_replacing_and_modifying():
    state = make_state()
    state.doc_generation = 4
    viewer = make_viewer(state)
    start_reading(viewer, state)
    assert reading_context_changed(viewer, state) is None

    viewer._macro_read_stop_reason = "closed"  # cancel_operations_on_close
    assert reading_context_changed(viewer, state) == "closed"
    viewer._macro_read_stop_reason = None

    state.doc_generation = 5
    assert reading_context_changed(viewer, state) == "closed"
    state.doc_generation = 4

    state.history_index -= 1  # undo depuis la mosaïque
    assert reading_context_changed(viewer, state) == "modified"


def test_history_top_detects_a_real_push_even_when_history_is_full():
    state = make_state(n_history=20)
    before = history_top(state)
    # Historique plein : save_state ajoute puis retire le plus ancien, l'indice ne bouge pas.
    state.history.append({"entries": "new"})
    state.history.pop(0)
    assert state.history_index == 19
    assert history_top(state) is not before


# ── B2 : liste des étapes pendant un enregistrement ─────────────────────────

def test_undo_of_rotation_with_two_history_states_removes_only_that_step():
    h0, h_crop, h_rot_before, h_rot_after = ({"n": i} for i in range(4))
    history = [h0, h_crop, h_rot_before, h_rot_after]
    steps, snapshots, redo = [step("crop"), step("rotate")], [h_crop, h_rot_after], []

    sync_recorded_steps(steps, snapshots, redo, history, 2)
    assert [s["tool"] for s in steps] == ["crop"]

    # Second Ctrl+Z : retour sur l'état après le recadrage, toujours appliqué.
    sync_recorded_steps(steps, snapshots, redo, history, 1)
    assert [s["tool"] for s in steps] == ["crop"]

    sync_recorded_steps(steps, snapshots, redo, history, 0)
    assert steps == []

    sync_recorded_steps(steps, snapshots, redo, history, 3)
    assert [s["tool"] for s in steps] == ["crop", "rotate"]
    assert redo == []


def test_undo_never_removes_steps_taken_from_an_existing_macro():
    h0, h1 = {"n": 0}, {"n": 1}
    steps, snapshots, redo = [step("crop")], [None], []
    sync_recorded_steps(steps, snapshots, redo, [h0, h1], 0)
    assert [s["tool"] for s in steps] == ["crop"]


def test_undo_with_nothing_left_to_undo_keeps_steps():
    h_trimmed = {"n": "trimmed"}
    history = [{"n": i} for i in range(20)]
    # Snapshot sorti de l'historique plein : l'étape n'est plus annulable.
    steps, snapshots, redo = [step("blur")], [h_trimmed], []
    sync_recorded_steps(steps, snapshots, redo, history, 0)
    assert [s["tool"] for s in steps] == ["blur"]


# ── B7 : renommage ──────────────────────────────────────────────────────────

def test_rename_removes_the_old_file(tmp_path, monkeypatch):
    monkeypatch.setattr(macro_engine, "get_macros_dir", lambda: str(tmp_path))
    macro_engine.save_macro({"name": "Alpha", "description": "", "steps": []})

    rename_macro("Alpha", {"name": "Beta", "description": "d", "steps": []})

    assert sorted(os.listdir(tmp_path)) == ["Beta.json"]
    with open(tmp_path / "Beta.json", encoding="utf-8") as f:
        data = json.load(f)
    assert data["name"] == "Beta" and data["description"] == "d"


def test_rename_changing_only_case_keeps_the_macro(tmp_path, monkeypatch):
    monkeypatch.setattr(macro_engine, "get_macros_dir", lambda: str(tmp_path))
    macro_engine.save_macro({"name": "alpha", "description": "", "steps": []})

    rename_macro("alpha", {"name": "Alpha", "description": "", "steps": []})

    assert os.listdir(tmp_path) == ["Alpha.json"]


# ── B8 : fichier de macro mal formé ─────────────────────────────────────────

def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def test_load_macro_rejects_a_malformed_step(tmp_path):
    path = tmp_path / "bad.json"
    write_json(path, {"name": "bad", "steps": [{"tool": "crop"}]})
    assert load_macro(str(path)) is None


def test_load_macro_accepts_a_valid_macro(tmp_path):
    path = tmp_path / "good.json"
    write_json(path, {"name": "good", "steps": [step("crop")]})
    macro = load_macro(str(path))
    assert macro is not None and macro["description"] == ""


# ── B9 : "Restaurer l'original" au rejeu ────────────────────────────────────

def test_restore_keeps_changes_made_by_other_tools_before():
    state = make_state(page_bytes=b"start")
    viewer = make_viewer(state)
    entry = state.images_data[0]

    entry["bytes"] = b"cropped"            # étape recadrage
    _remember_tool_start(viewer, "effect")
    entry["bytes"] = b"sepia"              # étape effet

    assert _apply_restore_step(viewer, "restore_effect") is True
    assert entry["bytes"] == b"cropped"


def test_restore_without_any_step_of_that_tool_changes_nothing():
    state = make_state(page_bytes=b"cropped")
    viewer = make_viewer(state)
    assert _apply_restore_step(viewer, "restore_image_mode") is True
    assert state.images_data[0]["bytes"] == b"cropped"
