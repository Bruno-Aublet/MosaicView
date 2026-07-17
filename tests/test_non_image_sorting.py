from modules.qt.non_image_sorting import reposition_non_images, _natural_key


def make_image(name):
    return {"orig_name": name, "is_image": True}


def make_non_image(name):
    return {"orig_name": name, "is_image": False}


# ---------------------------------------------------------------------------
# _natural_key
# ---------------------------------------------------------------------------

def test_natural_key_splits_name_and_extension():
    key = _natural_key("page10.jpg")
    assert key[1] == ".jpg"


def test_natural_key_numeric_part_sorts_as_int_not_string():
    # "9" doit trier avant "10" (tri naturel), pas après (tri alphabétique pur)
    assert _natural_key("page9.jpg") < _natural_key("page10.jpg")


def test_natural_key_case_insensitive():
    assert _natural_key("Page1.jpg") == _natural_key("page1.jpg")


# ---------------------------------------------------------------------------
# reposition_non_images
# ---------------------------------------------------------------------------

def test_reposition_empty_list_returns_as_is():
    assert reposition_non_images([]) == []


def test_reposition_no_non_images_returns_same_list_object():
    images_data = [make_image("01.jpg"), make_image("02.jpg")]
    result = reposition_non_images(images_data)
    assert result is images_data


def test_reposition_preserves_relative_order_of_images():
    # Les images gardent leur ordre actuel entre elles, peu importe leurs noms
    images_data = [make_image("z.jpg"), make_image("a.jpg"), make_non_image("readme.txt")]
    result = reposition_non_images(images_data)
    image_names_in_result = [e["orig_name"] for e in result if e["is_image"]]
    assert image_names_in_result == ["z.jpg", "a.jpg"]


def test_reposition_inserts_non_image_between_numbered_images():
    images_data = [make_image("01.jpg"), make_non_image("01_notes.txt"), make_image("02.jpg")]
    result = reposition_non_images(images_data)
    names = [e["orig_name"] for e in result]
    assert names == ["01.jpg", "01_notes.txt", "02.jpg"]


def test_reposition_non_image_sorting_after_last_numbered_image():
    # "zzz_end_notes.txt" trie après "02.jpg" alphanumériquement ('z' > chiffre)
    images_data = [make_image("02.jpg"), make_non_image("zzz_end_notes.txt")]
    result = reposition_non_images(images_data)
    names = [e["orig_name"] for e in result]
    assert names == ["02.jpg", "zzz_end_notes.txt"]


def test_reposition_multiple_non_images():
    images_data = [
        make_image("01.jpg"),
        make_non_image("01_a.txt"),
        make_non_image("01_b.txt"),
        make_image("02.jpg"),
    ]
    result = reposition_non_images(images_data)
    names = [e["orig_name"] for e in result]
    assert names == ["01.jpg", "01_a.txt", "01_b.txt", "02.jpg"]


def test_reposition_does_not_lose_any_entry():
    images_data = [
        make_image("z.jpg"), make_non_image("m.txt"),
        make_image("a.jpg"), make_non_image("b.txt"),
    ]
    result = reposition_non_images(images_data)
    assert len(result) == len(images_data)
    assert set(id(e) for e in result) == set(id(e) for e in images_data)
