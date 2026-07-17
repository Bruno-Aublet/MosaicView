from PIL import Image

from modules.qt.image_ops import (
    merge_images_vertically,
    merge_images_horizontally,
    detect_merge_adjustment,
    merge_images_2d,
)


def make_img(width, height, color="white"):
    return Image.new("RGB", (width, height), color)


def make_position(img, x, y):
    return {"entry": {"img": img}, "x": x, "y": y}


# ---------------------------------------------------------------------------
# merge_images_vertically
# ---------------------------------------------------------------------------

def test_merge_vertically_empty_list_returns_none():
    assert merge_images_vertically([]) is None


def test_merge_vertically_same_width_stacks_heights():
    imgs = [make_img(100, 50), make_img(100, 80)]
    result = merge_images_vertically(imgs)
    assert result.size == (100, 130)


def test_merge_vertically_keep_original_uses_max_width_no_resize():
    imgs = [make_img(100, 50), make_img(200, 80)]
    result = merge_images_vertically(imgs, adjustment_mode="keep_original")
    # keep_original : pas de redimensionnement, canvas = max_width, hauteurs sommées telles quelles
    assert result.size == (200, 130)


def test_merge_vertically_enlarge_small_matches_max_width():
    imgs = [make_img(100, 50), make_img(200, 80)]
    result = merge_images_vertically(imgs, adjustment_mode="enlarge_small")
    assert result.width == 200
    # image 100x50 agrandie à 200 de large -> hauteur *2 = 100 ; + 80 inchangée = 180
    assert result.height == 180


def test_merge_vertically_reduce_large_matches_min_width():
    imgs = [make_img(100, 50), make_img(200, 80)]
    result = merge_images_vertically(imgs, adjustment_mode="reduce_large")
    assert result.width == 100
    # image 200x80 réduite à 100 de large -> hauteur /2 = 40 ; + 50 inchangée = 90
    assert result.height == 90


def test_merge_vertically_single_image_returns_same_dimensions():
    result = merge_images_vertically([make_img(300, 400)])
    assert result.size == (300, 400)


# ---------------------------------------------------------------------------
# merge_images_horizontally
# ---------------------------------------------------------------------------

def test_merge_horizontally_empty_list_returns_none():
    assert merge_images_horizontally([]) is None


def test_merge_horizontally_same_height_sums_widths():
    imgs = [make_img(100, 200), make_img(150, 200)]
    result = merge_images_horizontally(imgs)
    assert result.size == (250, 200)


def test_merge_horizontally_enlarge_small_matches_max_height():
    imgs = [make_img(100, 100), make_img(100, 200)]
    result = merge_images_horizontally(imgs, adjustment_mode="enlarge_small")
    assert result.height == 200
    # image 100x100 agrandie à hauteur 200 -> largeur *2 = 200 ; + 100 inchangée = 300
    assert result.width == 300


def test_merge_horizontally_reduce_large_matches_min_height():
    imgs = [make_img(100, 100), make_img(100, 200)]
    result = merge_images_horizontally(imgs, adjustment_mode="reduce_large")
    assert result.height == 100
    # image 100x200 réduite à hauteur 100 -> largeur /2 = 50 ; + 100 inchangée = 150
    assert result.width == 150


# ---------------------------------------------------------------------------
# detect_merge_adjustment
# ---------------------------------------------------------------------------

def test_detect_merge_adjustment_empty_returns_no_adjustment():
    need, dim_type, dims = detect_merge_adjustment([])
    assert need is False
    assert dim_type == "height"
    assert dims == []


def test_detect_merge_adjustment_single_row_same_height_no_adjustment():
    positions = [make_position(make_img(100, 200), 0, 0), make_position(make_img(150, 200), 100, 0)]
    need, dim_type, dims = detect_merge_adjustment(positions)
    assert need is False


def test_detect_merge_adjustment_same_row_different_heights_needs_adjustment():
    positions = [make_position(make_img(100, 200), 0, 0), make_position(make_img(150, 300), 100, 0)]
    need, dim_type, dims = detect_merge_adjustment(positions)
    assert need is True
    assert dim_type == "height"
    assert set(dims) == {200, 300}


def test_detect_merge_adjustment_different_rows_different_widths_needs_adjustment():
    # Deux lignes distinctes (Y différents), largeurs de ligne différentes
    positions = [
        make_position(make_img(100, 200), 0, 0),
        make_position(make_img(300, 200), 0, 300),
    ]
    need, dim_type, dims = detect_merge_adjustment(positions)
    assert need is True
    assert dim_type == "width"
    assert set(dims) == {100, 300}


def test_detect_merge_adjustment_grid_aligned_no_adjustment_needed():
    # Grille 2x2 parfaitement alignée : aucun ajustement nécessaire
    positions = [
        make_position(make_img(100, 100), 0, 0),
        make_position(make_img(100, 100), 100, 0),
        make_position(make_img(100, 100), 0, 100),
        make_position(make_img(100, 100), 100, 100),
    ]
    need, dim_type, dims = detect_merge_adjustment(positions)
    assert need is False


# ---------------------------------------------------------------------------
# merge_images_2d
# ---------------------------------------------------------------------------

def test_merge_2d_empty_returns_none():
    assert merge_images_2d([]) is None


def test_merge_2d_single_image_returns_same_size():
    positions = [make_position(make_img(200, 300), 0, 0)]
    result = merge_images_2d(positions)
    assert result.size == (200, 300)


def test_merge_2d_single_row_side_by_side():
    positions = [make_position(make_img(100, 200), 0, 0), make_position(make_img(150, 200), 100, 0)]
    result = merge_images_2d(positions)
    assert result.size == (250, 200)


def test_merge_2d_single_column_stacked():
    positions = [make_position(make_img(200, 100), 0, 0), make_position(make_img(200, 150), 0, 100)]
    result = merge_images_2d(positions)
    assert result.size == (200, 250)


def test_merge_2d_grid_2x2_aligned():
    positions = [
        make_position(make_img(100, 100), 0, 0),
        make_position(make_img(100, 100), 100, 0),
        make_position(make_img(100, 100), 0, 100),
        make_position(make_img(100, 100), 100, 100),
    ]
    result = merge_images_2d(positions)
    assert result.size == (200, 200)


def test_merge_2d_needs_adjustment_calls_ask_adjustment_func():
    positions = [make_position(make_img(100, 200), 0, 0), make_position(make_img(150, 300), 100, 0)]
    calls = []

    def ask(dimension_type, dimensions_list):
        calls.append((dimension_type, dimensions_list))
        return "enlarge_small"

    result = merge_images_2d(positions, ask_adjustment_func=ask)
    assert len(calls) == 1
    assert calls[0][0] == "height"
    assert result is not None


def test_merge_2d_ask_adjustment_returns_none_cancels_merge():
    positions = [make_position(make_img(100, 200), 0, 0), make_position(make_img(150, 300), 100, 0)]

    def ask_cancel(dimension_type, dimensions_list):
        return None

    result = merge_images_2d(positions, ask_adjustment_func=ask_cancel)
    assert result is None


def test_merge_2d_no_ask_adjustment_func_defaults_to_keep_original():
    positions = [make_position(make_img(100, 200), 0, 0), make_position(make_img(150, 300), 100, 0)]
    # Pas de ask_adjustment_func fourni -> merge quand même, avec 'keep_original'
    result = merge_images_2d(positions, ask_adjustment_func=None)
    assert result is not None
