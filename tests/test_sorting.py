from modules.qt.sorting import get_sort_key, sort_images


def natural_sort_key(name):
    """Double simple : tri alphanumérique naturel basique pour les tests."""
    import re
    parts = re.split(r'(\d+)', name.lower())
    return [int(p) if p.isdigit() else p for p in parts]


def get_image_metadata(entry):
    """Double simple : retourne les métadonnées déjà stockées sur l'entrée de test."""
    return entry.get("_metadata")


def make_entry(name, ext=".jpg", size_bytes=0, width=None, height=None, dpi=None):
    entry = {"orig_name": name, "extension": ext, "bytes": b"x" * size_bytes}
    if width is not None or height is not None or dpi is not None:
        entry["_metadata"] = {"size": (width or 0, height or 0), "dpi": dpi}
    return entry


class FakeState:
    def __init__(self, images_data):
        self.images_data = images_data
        self.current_sort_method = None
        self.current_sort_order = "asc"
        self.modified = False


def make_callbacks(state):
    calls = {"save_state": 0, "render_mosaic": 0, "update_button_text": 0}
    return {
        "state": state,
        "natural_sort_key": natural_sort_key,
        "get_image_metadata": get_image_metadata,
        "save_state": lambda: calls.__setitem__("save_state", calls["save_state"] + 1),
        "render_mosaic": lambda: calls.__setitem__("render_mosaic", calls["render_mosaic"] + 1),
        "update_button_text": lambda: calls.__setitem__("update_button_text", calls["update_button_text"] + 1),
    }, calls


# ---------------------------------------------------------------------------
# get_sort_key — les 7 critères
# ---------------------------------------------------------------------------

def test_get_sort_key_name_uses_natural_sort():
    entry = make_entry("page10.jpg")
    key = get_sort_key(entry, "name", natural_sort_key, get_image_metadata)
    assert key == natural_sort_key("page10.jpg")


def test_get_sort_key_type_returns_lowercase_extension():
    entry = make_entry("page1.JPG", ext=".JPG")
    assert get_sort_key(entry, "type", natural_sort_key, get_image_metadata) == ".jpg"


def test_get_sort_key_weight_returns_bytes_length():
    entry = make_entry("page1.jpg", size_bytes=1234)
    assert get_sort_key(entry, "weight", natural_sort_key, get_image_metadata) == 1234


def test_get_sort_key_weight_none_bytes_returns_zero():
    entry = {"orig_name": "page1.jpg", "extension": ".jpg", "bytes": None}
    assert get_sort_key(entry, "weight", natural_sort_key, get_image_metadata) == 0


def test_get_sort_key_width():
    entry = make_entry("page1.jpg", width=800, height=1200)
    assert get_sort_key(entry, "width", natural_sort_key, get_image_metadata) == 800


def test_get_sort_key_height():
    entry = make_entry("page1.jpg", width=800, height=1200)
    assert get_sort_key(entry, "height", natural_sort_key, get_image_metadata) == 1200


def test_get_sort_key_resolution_is_width_times_height():
    entry = make_entry("page1.jpg", width=800, height=1200)
    assert get_sort_key(entry, "resolution", natural_sort_key, get_image_metadata) == 800 * 1200


def test_get_sort_key_width_no_metadata_returns_zero():
    entry = make_entry("page1.jpg")  # pas de _metadata
    assert get_sort_key(entry, "width", natural_sort_key, get_image_metadata) == 0


def test_get_sort_key_dpi_tuple_returns_first_value():
    entry = make_entry("page1.jpg", width=800, height=1200, dpi=(300, 300))
    assert get_sort_key(entry, "dpi", natural_sort_key, get_image_metadata) == 300


def test_get_sort_key_dpi_scalar_value():
    entry = make_entry("page1.jpg", width=800, height=1200, dpi=150)
    assert get_sort_key(entry, "dpi", natural_sort_key, get_image_metadata) == 150


def test_get_sort_key_dpi_missing_returns_zero():
    entry = make_entry("page1.jpg", width=800, height=1200, dpi=None)
    assert get_sort_key(entry, "dpi", natural_sort_key, get_image_metadata) == 0


def test_get_sort_key_unknown_method_returns_zero():
    entry = make_entry("page1.jpg")
    assert get_sort_key(entry, "bogus_method", natural_sort_key, get_image_metadata) == 0


# ---------------------------------------------------------------------------
# sort_images
# ---------------------------------------------------------------------------

def test_sort_images_by_name_ascending():
    entries = [make_entry("page10.jpg"), make_entry("page2.jpg"), make_entry("page1.jpg")]
    state = FakeState(entries)
    callbacks, calls = make_callbacks(state)

    sort_images("name", callbacks)

    names = [e["orig_name"] for e in state.images_data]
    assert names == ["page1.jpg", "page2.jpg", "page10.jpg"]
    assert state.current_sort_order == "asc"
    assert state.modified is True
    assert calls["save_state"] == 1
    assert calls["render_mosaic"] == 1
    assert calls["update_button_text"] == 1


def test_sort_images_second_click_same_method_toggles_to_desc():
    entries = [make_entry("page1.jpg"), make_entry("page2.jpg")]
    state = FakeState(entries)
    callbacks, calls = make_callbacks(state)

    sort_images("name", callbacks)
    sort_images("name", callbacks)

    assert state.current_sort_order == "desc"
    names = [e["orig_name"] for e in state.images_data]
    assert names == ["page2.jpg", "page1.jpg"]


def test_sort_images_different_method_resets_to_ascending():
    entries = [make_entry("page1.jpg", size_bytes=300), make_entry("page2.jpg", size_bytes=100)]
    state = FakeState(entries)
    callbacks, calls = make_callbacks(state)

    sort_images("name", callbacks)
    sort_images("name", callbacks)  # -> desc
    sort_images("weight", callbacks)  # nouvelle méthode -> repart en asc

    assert state.current_sort_method == "weight"
    assert state.current_sort_order == "asc"
    sizes = [len(e["bytes"]) for e in state.images_data]
    assert sizes == [100, 300]


def test_sort_images_empty_images_data_does_nothing():
    state = FakeState([])
    callbacks, calls = make_callbacks(state)

    sort_images("name", callbacks)

    assert calls["save_state"] == 0
    assert state.modified is False


def test_sort_images_by_weight():
    entries = [make_entry("a.jpg", size_bytes=500), make_entry("b.jpg", size_bytes=100), make_entry("c.jpg", size_bytes=300)]
    state = FakeState(entries)
    callbacks, calls = make_callbacks(state)

    sort_images("weight", callbacks)

    sizes = [len(e["bytes"]) for e in state.images_data]
    assert sizes == [100, 300, 500]


def test_sort_images_by_resolution():
    entries = [
        make_entry("a.jpg", width=1000, height=1000),
        make_entry("b.jpg", width=100, height=100),
        make_entry("c.jpg", width=500, height=500),
    ]
    state = FakeState(entries)
    callbacks, calls = make_callbacks(state)

    sort_images("resolution", callbacks)

    names = [e["orig_name"] for e in state.images_data]
    assert names == ["b.jpg", "c.jpg", "a.jpg"]
