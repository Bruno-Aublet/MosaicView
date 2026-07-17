import io

from PIL import Image

from modules.qt.renumbering import (
    generate_auto_filenames,
    compute_first_page_info,
    renumber_pages_auto,
    renumber_pages,
)


def make_image_entry(name, width=800, height=1200, ext=".jpg"):
    return {
        "orig_name": name, "name": name, "is_image": True, "is_dir": False,
        "extension": ext, "img_width": width, "img_height": height,
    }


def make_image_entry_from_bytes(name, width, height, ext=".jpg"):
    """Entrée sans img_width/img_height en cache : force la lecture des bytes PIL."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="JPEG")
    return {
        "orig_name": name, "name": name, "is_image": True, "is_dir": False,
        "extension": ext, "bytes": buf.getvalue(),
    }


def make_non_image_entry(name):
    return {"orig_name": name, "name": name, "is_image": False, "is_dir": False, "extension": ""}


class FakeState:
    def __init__(self, images_data):
        self.images_data = images_data
        self.modified = False


def make_callbacks(state=None):
    calls = {"save_state": 0, "render_mosaic": 0, "update_button_text": 0}

    def save_state():
        calls["save_state"] += 1

    def render_mosaic():
        calls["render_mosaic"] += 1

    def update_button_text():
        calls["update_button_text"] += 1

    callbacks = {
        "save_state": save_state,
        "render_mosaic": render_mosaic,
        "update_button_text": update_button_text,
    }
    return callbacks, calls


# ---------------------------------------------------------------------------
# generate_auto_filenames
# ---------------------------------------------------------------------------

def test_generate_auto_filenames_simple_pages():
    filenames = generate_auto_filenames([1, 1, 1], ".jpg")
    assert filenames == ["01.jpg", "02.jpg", "03.jpg"]


def test_generate_auto_filenames_double_page_dash_range():
    # mult=2 -> plage "NN-MM"
    filenames = generate_auto_filenames([1, 2, 1], ".jpg")
    assert filenames == ["01.jpg", "02-03.jpg", "04.jpg"]


def test_generate_auto_filenames_large_multiplier_uses_triple_dash():
    # mult > 4 -> plage "NN---MM"
    filenames = generate_auto_filenames([5], ".jpg")
    assert filenames == ["01---05.jpg"]


def test_generate_auto_filenames_digit_padding_scales_with_total():
    # total = 150 pages -> 3 chiffres de padding
    multipliers = [1] * 150
    filenames = generate_auto_filenames(multipliers, ".jpg")
    assert filenames[0] == "001.jpg"
    assert filenames[-1] == "150.jpg"


def test_generate_auto_filenames_per_entry_extensions():
    filenames = generate_auto_filenames([1, 1], [".jpg", ".png"])
    assert filenames == ["01.jpg", "02.png"]


def test_generate_auto_filenames_first_page_exclude_mode():
    filenames = generate_auto_filenames([1, 1, 1], ".jpg", first_page_mode="exclude")
    assert filenames[0] is None
    assert filenames[1:] == ["01.jpg", "02.jpg"]


def test_generate_auto_filenames_first_page_joint_mode():
    filenames = generate_auto_filenames([2, 1, 1], ".jpg", first_page_mode="joint", first_page_total=4)
    assert filenames[0] == "01-04.jpg"
    assert filenames[1] == "02.jpg"
    assert filenames[2] == "03.jpg"


# ---------------------------------------------------------------------------
# compute_first_page_info
# ---------------------------------------------------------------------------

def test_compute_first_page_info_uses_cached_dimensions():
    entries = [
        make_image_entry("a.jpg", width=800, height=1200),
        make_image_entry("b.jpg", width=800, height=1200),
    ]
    multipliers, first_mult = compute_first_page_info(entries)
    assert multipliers == [1, 1]
    assert first_mult == 1


def test_compute_first_page_info_reads_bytes_when_no_cached_dimensions():
    entries = [
        make_image_entry_from_bytes("a.jpg", 800, 1200),
        make_image_entry_from_bytes("b.jpg", 800, 1200),
    ]
    multipliers, first_mult = compute_first_page_info(entries)
    assert multipliers == [1, 1]


def test_compute_first_page_info_detects_double_first_page():
    entries = [
        make_image_entry("cover.jpg", width=1600, height=1200),  # paysage: double page
        make_image_entry("p1.jpg", width=800, height=1200),
        make_image_entry("p2.jpg", width=800, height=1200),
    ]
    multipliers, first_mult = compute_first_page_info(entries)
    assert first_mult == 2
    assert multipliers[0] == 2


def test_compute_first_page_info_broken_image_bytes_defaults_ratio_zero():
    entries = [{"orig_name": "broken.jpg", "extension": ".jpg", "bytes": b"not an image"}]
    multipliers, first_mult = compute_first_page_info(entries)
    assert multipliers == [1]
    assert first_mult == 1


def test_compute_first_page_info_empty_list():
    multipliers, first_mult = compute_first_page_info([])
    assert multipliers == []
    assert first_mult == 1


# ---------------------------------------------------------------------------
# renumber_pages_auto
# ---------------------------------------------------------------------------

def test_renumber_pages_auto_renames_images_in_order():
    entries = [
        make_image_entry("z_cover.jpg"),
        make_image_entry("a_p1.jpg"),
    ]
    state = FakeState(list(entries))
    callbacks, calls = make_callbacks()

    renumber_pages_auto(callbacks, state=state)

    names = [e["orig_name"] for e in state.images_data if e["is_image"]]
    assert names == ["01.jpg", "02.jpg"]
    assert state.modified is True
    assert calls["save_state"] == 2  # avant ET après modification
    assert calls["render_mosaic"] == 1
    assert calls["update_button_text"] == 1


def test_renumber_pages_auto_empty_images_data_does_nothing():
    state = FakeState([])
    callbacks, calls = make_callbacks()

    renumber_pages_auto(callbacks, state=state)

    assert state.modified is False
    assert calls["save_state"] == 0


def test_renumber_pages_auto_no_root_callback_skips_dialog_even_if_multiple_detected():
    # first_mult > 1 mais callbacks sans "root" -> pas de dialogue, mode auto direct
    entries = [
        make_image_entry("cover.jpg", width=1600, height=1200),
        make_image_entry("p1.jpg", width=800, height=1200),
    ]
    state = FakeState(list(entries))
    callbacks, calls = make_callbacks()
    assert "root" not in callbacks

    renumber_pages_auto(callbacks, state=state)

    assert calls["save_state"] >= 1
    assert state.modified is True


def test_renumber_pages_auto_repositions_non_images():
    entries = [
        make_image_entry("b.jpg"),
        make_non_image_entry("01_notes.txt"),
        make_image_entry("c.jpg"),
        make_image_entry("d.jpg"),
    ]
    state = FakeState(list(entries))
    callbacks, calls = make_callbacks()

    renumber_pages_auto(callbacks, state=state)

    # Images renommées 01.jpg/02.jpg/03.jpg (ordre d'origine préservé) ; le
    # non-image "01_notes.txt" doit se glisser entre 01.jpg et 02.jpg (natural sort).
    order = [e["orig_name"] for e in state.images_data]
    assert order == ["01.jpg", "01_notes.txt", "02.jpg", "03.jpg"]


# ---------------------------------------------------------------------------
# renumber_pages
# ---------------------------------------------------------------------------

def test_renumber_pages_simple_sequential_rename():
    entries = [
        make_image_entry("z.jpg"),
        make_image_entry("a.jpg"),
        make_image_entry("m.jpg"),
    ]
    state = FakeState(list(entries))
    callbacks, calls = make_callbacks()

    renumber_pages(callbacks, state=state)

    names = [e["orig_name"] for e in state.images_data if e["is_image"]]
    assert names == ["01.jpg", "02.jpg", "03.jpg"]
    assert state.modified is True
    assert calls["save_state"] == 2
    assert calls["render_mosaic"] == 1


def test_renumber_pages_digit_padding_scales_with_count():
    entries = [make_image_entry(f"img{i}.jpg") for i in range(150)]
    state = FakeState(list(entries))
    callbacks, calls = make_callbacks()

    renumber_pages(callbacks, state=state)

    names = [e["orig_name"] for e in state.images_data if e["is_image"]]
    assert names[0] == "001.jpg"
    assert names[-1] == "150.jpg"


def test_renumber_pages_preserves_extension_per_entry():
    entries = [
        make_image_entry("a.jpg", ext=".jpg"),
        make_image_entry("b.png", ext=".png"),
    ]
    state = FakeState(list(entries))
    callbacks, calls = make_callbacks()

    renumber_pages(callbacks, state=state)

    names = [e["orig_name"] for e in state.images_data if e["is_image"]]
    assert names == ["01.jpg", "02.png"]


def test_renumber_pages_ignores_non_image_entries_for_counter():
    entries = [
        make_image_entry("a.jpg"),
        make_non_image_entry("readme.txt"),
        make_image_entry("b.jpg"),
    ]
    state = FakeState(list(entries))
    callbacks, calls = make_callbacks()

    renumber_pages(callbacks, state=state)

    names = [e["orig_name"] for e in state.images_data if e["is_image"]]
    assert names == ["01.jpg", "02.jpg"]
