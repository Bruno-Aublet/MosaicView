"""Identité des pages dans l'historique et contenu « encore celui qui a été écrit ».

- content_snapshot / content_unchanged : une sauvegarde dont la fin est
  différée (confirmation non modale) ne doit marquer le document sauvegardé que
  s'il n'a pas changé depuis l'écriture.
- entry_key : jeton qui identifie une page d'une snapshot à l'autre. Jamais
  id(entry) seul, réattribué par Python à un nouvel objet une fois l'ancien
  libéré.
"""
from modules.qt.undo_redo import (
    adopt_entry_key,
    content_snapshot,
    content_unchanged,
    entry_key,
    save_state_data,
)


class FakeState:
    def __init__(self, images_data):
        self.images_data = images_data
        self.all_entries = list(images_data)
        self.history = []
        self.history_index = -1
        self.selected_indices = set()
        self.modified = False
        self.needs_renumbering = False
        self.current_sort_method = None
        self.current_sort_order = "asc"
        self.current_directory = ""


def make_entry(data, name="p01.bmp"):
    return {"orig_name": name, "bytes": data, "extension": ".bmp", "is_image": True}


# ---------------------------------------------------------------------------
# content_snapshot / content_unchanged
# ---------------------------------------------------------------------------

def test_content_unchanged_when_nothing_happened():
    state = FakeState([make_entry(b"A", "p01.bmp"), make_entry(b"B", "p02.bmp")])
    snap = content_snapshot(state)
    assert content_unchanged(state, snap)


def test_content_changed_after_page_deleted():
    state = FakeState([make_entry(b"A", "p01.bmp"), make_entry(b"B", "p02.bmp")])
    snap = content_snapshot(state)
    state.images_data.pop()
    assert not content_unchanged(state, snap)


def test_content_changed_after_bytes_replaced():
    entry = make_entry(b"AAAA")
    state = FakeState([entry])
    snap = content_snapshot(state)
    entry["bytes"] = b"BBBB"
    assert not content_unchanged(state, snap)


def test_content_changed_after_rename():
    entry = make_entry(b"A", "p01.bmp")
    state = FakeState([entry])
    snap = content_snapshot(state)
    entry["orig_name"] = "p99.bmp"
    assert not content_unchanged(state, snap)


def test_content_changed_when_page_replaced_by_identical_copy():
    """Même nom, mêmes bytes, mais un autre objet (ex. page déposée depuis
    l'autre panneau) : ce n'est plus la page qui a été écrite."""
    entry = make_entry(b"A")
    state = FakeState([entry])
    snap = content_snapshot(state)
    state.images_data[0] = dict(entry)
    assert not content_unchanged(state, snap)


# ---------------------------------------------------------------------------
# entry_key
# ---------------------------------------------------------------------------

def test_entry_key_is_stable_for_the_same_object():
    entry = make_entry(b"A")
    assert entry_key(entry) == entry_key(entry)


def test_copied_entry_gets_its_own_key():
    """dict(entry) copie aussi le jeton stocké : la copie, d'un autre id,
    doit en recevoir un nouveau au lieu de se faire passer pour l'original."""
    entry = make_entry(b"A")
    key = entry_key(entry)
    copy = dict(entry)
    assert entry_key(copy) != key
    assert entry_key(entry) == key


def test_keys_are_never_reused():
    keys = {entry_key(make_entry(b"A")) for _i in range(200)}
    assert len(keys) == 200


def test_stale_key_from_another_object_is_not_trusted():
    """Une entrée qui porte le jeton d'un autre objet (id différent), comme
    un objet recréé à l'adresse d'une page libérée, reçoit un jeton neuf."""
    old = make_entry(b"A")
    old_key = entry_key(old)
    new = make_entry(b"B")
    new["_undo_key"] = (old_key, id(new) + 1)
    assert entry_key(new) != old_key


def test_adopted_key_is_recognised():
    entry = make_entry(b"A")
    key = entry_key(entry)
    rebuilt = make_entry(b"A")
    adopt_entry_key(rebuilt, key)
    assert entry_key(rebuilt) == key


def test_snapshot_stores_entry_key():
    entry = make_entry(b"A")
    state = FakeState([entry])
    save_state_data(state, force=True)
    assert state.history[-1]["entries"][0]["_original_id"] == entry_key(entry)


def test_selection_saved_by_key():
    first = make_entry(b"A", "p01.bmp")
    second = make_entry(b"B", "p02.bmp")
    state = FakeState([first, second])
    state.selected_indices = {1}
    save_state_data(state, force=True)
    assert state.history[-1]["selected_keys"] == {entry_key(second)}


def test_restored_selection_ignores_homonyms():
    """Deux pages de même nom, une seule sélectionnée : après Annuler, seule
    celle-là doit l'être (une suppression suivante viserait sinon les deux)."""
    from modules.qt.state import AppState
    from modules.qt.undo_redo_qt import undo_action_qt
    first = make_entry(b"A", "page.bmp")
    second = make_entry(b"B", "page.bmp")
    state = AppState()
    state.images_data = [first, second]
    state.selected_indices = {1}
    save_state_data(state, force=True)
    state.images_data.append(make_entry(b"C", "p03.bmp"))
    save_state_data(state)
    noop = lambda *a, **k: None
    undo_action_qt(state, noop, noop, noop, noop)
    assert state.selected_indices == {1}


def test_tool_value_key_follows_the_page():
    """La valeur d'une réglette de la visionneuse reste attachée à sa page
    quand une page précédente est supprimée (l'ancienne clé par position la
    donnait à la page suivante)."""
    from modules.qt.state import AppState
    state = AppState()
    state.images_data = [make_entry(b"A", "p01.bmp"), make_entry(b"B", "p02.bmp")]
    save_state_data(state, force=True)
    key = state.tool_value_key(1)
    state.images_data.pop(0)
    assert state.tool_value_key(0) == key


def test_history_point_key_survives_saturated_history():
    """history_index reste fixe une fois l'historique plein : le repère du
    point d'historique, lui, change à chaque nouvel état."""
    from modules.qt.undo_redo import MAX_HISTORY, history_point_key
    state = FakeState([make_entry(b"A")])
    for n in range(MAX_HISTORY + 2):
        state.images_data[0]["bytes"] = bytes([n])
        save_state_data(state)
    index_before, key_before = state.history_index, history_point_key(state)
    state.images_data[0]["bytes"] = b"new"
    save_state_data(state)
    assert state.history_index == index_before
    assert history_point_key(state) != key_before


# ---------------------------------------------------------------------------
# Propriétés de la page dans la snapshot (page recréée par Annuler)
# ---------------------------------------------------------------------------

def test_snapshot_keeps_page_properties():
    """Une page supprimée puis recréée par Annuler reprend ses propriétés :
    sans is_animated_gif, un GIF animé restauré était traité comme une image
    fixe et l'opération suivante n'en gardait que la première frame."""
    entry = make_entry(b"GIF", "anim.gif")
    entry.update({"is_animated_gif": True, "gif_frame_count": 3,
                  "gif_durations": [100, 120, 80], "dpi": 300,
                  "source_archive": "other.cbz"})
    state = FakeState([entry])
    save_state_data(state, force=True)
    meta = state.history[-1]["entries"][0]["meta"]
    assert meta["is_animated_gif"] is True
    assert meta["gif_frame_count"] == 3
    assert meta["gif_durations"] == [100, 120, 80]
    assert meta["dpi"] == 300
    assert meta["source_archive"] == "other.cbz"


def test_snapshot_meta_is_not_shared_with_the_page():
    entry = make_entry(b"GIF", "anim.gif")
    entry["gif_durations"] = [100, 100]
    state = FakeState([entry])
    save_state_data(state, force=True)
    entry["gif_durations"].append(50)
    assert state.history[-1]["entries"][0]["meta"]["gif_durations"] == [100, 100]


def test_snapshot_meta_excludes_interface_and_caches():
    entry = make_entry(b"A")
    entry.update({"img": object(), "qt_pixmap_large": object(), "name_entry": object(),
                  "large_thumb_pil": object(), "_hash": "x", "_real_idx": 4,
                  "img_width": 10, "img_height": 20})
    state = FakeState([entry])
    save_state_data(state, force=True)
    meta = state.history[-1]["entries"][0]["meta"]
    for key in ("img", "qt_pixmap_large", "name_entry", "large_thumb_pil", "_hash",
                "_real_idx", "img_width", "img_height", "_undo_key", "bytes", "orig_name"):
        assert key not in meta


# ---------------------------------------------------------------------------
# AppState.page_key (originaux de "Restaurer l'original")
# ---------------------------------------------------------------------------

def test_page_key_follows_the_page_not_its_position():
    """Restaurer l'original mémorise l'original par page_key : après la
    suppression d'une page précédente, la même position désigne une autre
    page, qui ne doit pas recevoir l'original de la première."""
    from modules.qt.state import AppState
    pages = [make_entry(b"A", "p01.bmp"), make_entry(b"B", "p02.bmp"),
             make_entry(b"C", "p03.bmp")]
    state = AppState()
    state.images_data = list(pages)
    originals = {state.page_key(1): b"original B"}
    del state.images_data[0]
    assert state.page_key(1) not in originals          # p03 est maintenant en position 1
    assert state.page_key(0) in originals              # p02 garde son original
    assert state.page_key(5) is None
