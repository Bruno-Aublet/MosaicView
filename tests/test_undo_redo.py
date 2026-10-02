import io

from PIL import Image

from modules.qt.undo_redo import (
    _create_entries_snapshot_from,
    _is_state_identical,
    can_undo,
    history_top,
    mark_history_saved,
    pop_last_state,
    redo_data,
    save_state_data,
    undo_data,
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


def snapshot(entry):
    return _create_entries_snapshot_from([entry])


def bmp_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# _is_state_identical — comparaison des bytes
# ---------------------------------------------------------------------------

def test_same_bytes_object_is_identical():
    entry = make_entry(b"AAAA")
    last = snapshot(entry)
    assert _is_state_identical(last, snapshot(entry))


def test_distinct_objects_same_content_is_identical():
    entry = make_entry(b"AAAA")
    last = snapshot(entry)
    entry["bytes"] = bytes(bytearray(b"AAAA"))
    assert entry["bytes"] is not last[0]["bytes"]
    assert _is_state_identical(last, snapshot(entry))


def test_distinct_content_same_length_is_different():
    entry = make_entry(b"AAAA")
    last = snapshot(entry)
    entry["bytes"] = b"BBBB"
    assert not _is_state_identical(last, snapshot(entry))


def test_distinct_content_different_length_is_different():
    entry = make_entry(b"AAAA")
    last = snapshot(entry)
    entry["bytes"] = b"BBBBB"
    assert not _is_state_identical(last, snapshot(entry))


# ---------------------------------------------------------------------------
# save_state_data — création des étapes d'historique
# ---------------------------------------------------------------------------

def test_same_length_different_content_creates_two_history_states():
    entry = make_entry(b"AAAA")
    st = FakeState([entry])
    assert save_state_data(st)
    entry["bytes"] = b"BBBB"
    assert save_state_data(st)
    assert len(st.history) == 2
    assert st.history[0]["entries"][0]["bytes"] == b"AAAA"
    assert st.history[1]["entries"][0]["bytes"] == b"BBBB"


def test_unchanged_state_does_not_create_history_state():
    entry = make_entry(b"AAAA")
    st = FakeState([entry])
    save_state_data(st)
    assert not save_state_data(st)
    entry["bytes"] = bytes(bytearray(b"AAAA"))
    assert not save_state_data(st)
    assert len(st.history) == 1


def test_bmp_horizontal_flip_is_undoable():
    # Même séquence que flip_selected_qt : save_state() avant et après,
    # tous deux sans force. Un miroir BMP garde exactement la même taille.
    img = Image.new("RGB", (64, 32))
    img.paste((255, 0, 0), (0, 0, 32, 32))
    original = bmp_bytes(img)
    entry = make_entry(original)
    st = FakeState([entry])
    save_state_data(st)

    save_state_data(st)
    entry["bytes"] = bmp_bytes(img.transpose(Image.FLIP_LEFT_RIGHT))
    assert len(entry["bytes"]) == len(original)
    assert save_state_data(st)

    assert can_undo(st)
    restored = undo_data(st)
    assert restored["entries"][0]["bytes"] == original


def cancel_operation(st, entry, before_bytes):
    """Séquence d'une opération annulable (redimensionnement, lecture de
    macro) : save_state "avant", modification, annulation qui restaure les
    bytes et ne dépile que ce qui a réellement été poussé."""
    top_before = history_top(st)
    save_state_data(st)
    pushed_before = history_top(st) is not top_before
    entry["bytes"] = b"CHANGED"
    entry["bytes"] = before_bytes
    if pushed_before:
        pop_last_state(st)


def test_cancelled_operation_keeps_the_last_user_action_redoable():
    entry = make_entry(b"AAAA")
    st = FakeState([entry])
    save_state_data(st)
    entry["bytes"] = b"BBBB"
    save_state_data(st)  # dernière action de l'utilisateur

    cancel_operation(st, entry, b"BBBB")

    assert len(st.history) == 2
    assert st.history_index == 1
    assert st.history[1]["entries"][0]["bytes"] == b"BBBB"


def test_cancelled_operation_removes_the_state_it_pushed():
    entry = make_entry(b"AAAA")
    st = FakeState([entry])
    save_state_data(st)
    entry["bytes"] = b"BBBB"  # changement jamais sauvegardé

    cancel_operation(st, entry, b"BBBB")

    assert len(st.history) == 1
    assert st.history_index == 0


# ---------------------------------------------------------------------------
# mark_history_saved — état « modifié » après une sauvegarde
# ---------------------------------------------------------------------------

def delete_second_page_then_save(st):
    """Ouvre, supprime la 2e page (snapshot avant/après), puis sauvegarde."""
    save_state_data(st)
    st.images_data.pop(1)
    st.modified = True
    save_state_data(st)
    st.modified = False
    mark_history_saved(st)


def test_undo_after_save_is_modified():
    st = FakeState([make_entry(b"A", "p01.bmp"), make_entry(b"B", "p02.bmp")])
    delete_second_page_then_save(st)

    restored = undo_data(st)

    # La page supprimée revient en mémoire, mais le fichier écrit ne l'a plus.
    assert len(restored["entries"]) == 2
    assert restored["modified"] is True


def test_redo_back_to_saved_state_is_not_modified():
    st = FakeState([make_entry(b"A", "p01.bmp"), make_entry(b"B", "p02.bmp")])
    delete_second_page_then_save(st)

    undo_data(st)
    restored = redo_data(st)

    assert len(restored["entries"]) == 1
    assert restored["modified"] is False


def test_saved_content_differing_from_top_snapshot_marks_everything_modified():
    entry = make_entry(b"A", "p01.bmp")
    st = FakeState([entry])
    save_state_data(st)
    entry["orig_name"] = "renamed.bmp"  # nom appliqué à la sauvegarde, sans snapshot
    st.modified = False

    mark_history_saved(st)

    assert all(snap["modified"] for snap in st.history)


def test_mark_history_saved_on_empty_history():
    st = FakeState([make_entry(b"A")])
    mark_history_saved(st)
    assert st.history == []
