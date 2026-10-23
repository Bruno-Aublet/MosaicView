import io

from PIL import Image

from modules.qt.image_ops import (
    merge_images_vertically,
    merge_images_horizontally,
    detect_merge_adjustment,
    merge_images_2d,
    animations_joinable,
    merge_animated_2d,
    transform_animated_image,
    rotate_entry_data,
    flip_entry_data,
)
from modules.qt.entries import _webp_is_lossless


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
    assert calls[0][1] == [200, 300]
    # Petite image agrandie à 150×300 (proportions gardées), posée à côté
    assert result.size == (300, 300)


def test_merge_2d_ask_adjustment_returns_none_cancels_merge():
    positions = [make_position(make_img(100, 200), 0, 0), make_position(make_img(150, 300), 100, 0)]

    def ask_cancel(dimension_type, dimensions_list):
        return None

    result = merge_images_2d(positions, ask_adjustment_func=ask_cancel)
    assert result is None


def test_merge_2d_no_ask_adjustment_func_defaults_to_keep_original():
    positions = [make_position(make_img(100, 200, "red"), 0, 0),
                 make_position(make_img(150, 300, "blue"), 100, 0)]
    # Pas de ask_adjustment_func fourni -> merge quand même, avec 'keep_original' :
    # tailles d'origine, petite image centrée en hauteur, fond blanc autour
    result = merge_images_2d(positions, ask_adjustment_func=None)
    assert result.size == (250, 300)
    assert result.getpixel((50, 150)) == (255, 0, 0)     # image de gauche, intacte
    assert result.getpixel((50, 10)) == (255, 255, 255)  # au-dessus d'elle : blanc
    assert result.getpixel((200, 10)) == (0, 0, 255)     # image de droite, pleine hauteur


# ---------------------------------------------------------------------------
# animations_joinable
# ---------------------------------------------------------------------------

def make_static():
    return {"is_image": True}


def make_animated(durations, flag="is_animated_gif"):
    return {"is_image": True, flag: True,
            "gif_frame_count": len(durations), "gif_durations": list(durations)}


def test_joinable_all_static():
    assert animations_joinable([make_static(), make_static()])


def test_joinable_static_and_animated_refused():
    assert not animations_joinable([make_static(), make_animated([100, 100])])


def test_joinable_animated_webp_and_static_refused():
    assert not animations_joinable([make_animated([100, 100], "is_animated_image"), make_static()])


def test_joinable_identical_animations():
    assert animations_joinable([make_animated([100, 50, 80]), make_animated([100, 50, 80])])


def test_joinable_different_frame_count_refused():
    assert not animations_joinable([make_animated([100, 100]), make_animated([100, 100, 100])])


def test_joinable_different_durations_refused():
    assert not animations_joinable([make_animated([100, 100]), make_animated([100, 200])])


def test_joinable_gif_and_webp_within_10ms_tolerance():
    gif = make_animated([30, 30])
    webp = make_animated([33, 33], "is_animated_image")
    assert animations_joinable([gif, webp])


def test_joinable_beyond_tolerance_refused():
    assert not animations_joinable([make_animated([30, 30]), make_animated([41, 30])])


# ---------------------------------------------------------------------------
# merge_animated_2d
# ---------------------------------------------------------------------------

def make_animation_bytes(fmt, size, colors, durations):
    frames = [Image.new("RGBA", size, c) for c in colors]
    buf = io.BytesIO()
    frames[0].save(buf, format=fmt, save_all=True, append_images=frames[1:],
                   duration=durations, loop=0)
    return buf.getvalue()


def side_by_side(data_a, data_b):
    return [{"bytes": data_a, "x": 0, "y": 0}, {"bytes": data_b, "x": 100, "y": 0}]


def read_frames(data):
    im = Image.open(io.BytesIO(data))
    durations, left, right = [], [], []
    for i in range(im.n_frames):
        im.seek(i)
        im.load()
        durations.append(int(im.info.get("duration")))
        rgb = im.convert("RGB")
        left.append(rgb.getpixel((5, 5)))
        right.append(rgb.getpixel((rgb.width - 5, 5)))
    return im.format, im.size, durations, left, right


def test_merge_animated_gif_keeps_frames_and_durations():
    a = make_animation_bytes("GIF", (40, 60), ["red", "green", "blue"], [100, 200, 300])
    b = make_animation_bytes("GIF", (40, 60), ["blue", "red", "green"], [100, 200, 300])
    data = merge_animated_2d(side_by_side(a, b), [100, 200, 300], "keep_original", "GIF")
    fmt, size, durations, left, right = read_frames(data)
    assert fmt == "GIF"
    assert size == (80, 60)
    assert durations == [100, 200, 300]
    # Frame n de chaque source assemblée avec la frame n de l'autre.
    assert left[1] == (0, 128, 0)
    assert right[1] == (255, 0, 0)


def test_merge_animated_webp_output():
    a = make_animation_bytes("WEBP", (40, 60), ["red", "green"], [100, 100])
    data = merge_animated_2d(side_by_side(a, a), [100, 100], "keep_original", "WEBP")
    fmt, size, durations, _left, _right = read_frames(data)
    assert fmt == "WEBP"
    assert size == (80, 60)
    assert len(durations) == 2


def test_merge_animated_enlarge_small_resizes_each_frame():
    a = make_animation_bytes("GIF", (40, 60), ["red", "green"], [100, 100])
    b = make_animation_bytes("GIF", (20, 30), ["blue", "red"], [100, 100])
    data = merge_animated_2d(side_by_side(a, b), [100, 100], "enlarge_small", "GIF")
    _fmt, size, _durations, _left, _right = read_frames(data)
    assert size == (80, 60)


def test_merge_animated_transparency_flattened_on_white():
    a = make_animation_bytes("PNG", (40, 60), [(255, 0, 0, 0), "green"], [100, 100])
    data = merge_animated_2d(side_by_side(a, a), [100, 100], "keep_original", "PNG")
    _fmt, _size, _durations, left, _right = read_frames(data)
    assert left[0] == (255, 255, 255)


def test_merge_animated_cancelled_returns_none():
    a = make_animation_bytes("GIF", (40, 60), ["red", "green"], [100, 100])
    assert merge_animated_2d(side_by_side(a, a), [100, 100], "keep_original", "GIF",
                             is_cancelled=lambda: True) is None


def test_merge_animated_reports_progress():
    a = make_animation_bytes("GIF", (40, 60), ["red", "green", "blue"], [100, 100, 100])
    calls = []
    merge_animated_2d(side_by_side(a, a), [100, 100, 100], "keep_original", "GIF",
                      progress_cb=lambda done, total: calls.append((done, total)))
    assert calls == [(1, 3), (2, 3), (3, 3)]


# ---------------------------------------------------------------------------
# transform_animated_image
# ---------------------------------------------------------------------------

def _check_animated_crop(fmt, ext):
    data = make_animation_bytes(fmt, (40, 60), ["red", "green", "blue"], [100, 200, 300])
    entry = {"bytes": data, "extension": ext}
    out = transform_animated_image(entry, lambda f: f.crop((0, 0, 20, 60)))
    out_fmt, size, durations, _left, _right = read_frames(out)
    assert out_fmt == fmt
    assert size == (20, 60)
    assert durations == [100, 200, 300]
    assert (entry["img_width"], entry["img_height"]) == (20, 60)


def test_transform_animated_webp_keeps_frames():
    _check_animated_crop("WEBP", ".webp")


def test_transform_animated_png_keeps_frames():
    _check_animated_crop("PNG", ".png")


# ---------------------------------------------------------------------------
# Rotation / miroir d'une image animée WebP/PNG
# ---------------------------------------------------------------------------

def _animated_image_entry(fmt, ext, **save_kwargs):
    frames = [Image.new("RGBA", (40, 60), c) for c in ("red", "green", "blue")]
    buf = io.BytesIO()
    frames[0].save(buf, format=fmt, save_all=True, append_images=frames[1:],
                   duration=[100, 200, 300], loop=0, **save_kwargs)
    return {"is_image": True, "is_animated_image": True, "bytes": buf.getvalue(),
            "extension": ext}


def test_rotate_animated_webp_keeps_frames():
    entry = _animated_image_entry("WEBP", ".webp")
    assert rotate_entry_data(entry, -90)
    fmt, size, durations, _left, _right = read_frames(entry["bytes"])
    assert fmt == "WEBP"
    assert size == (60, 40)
    assert durations == [100, 200, 300]


def test_flip_animated_png_keeps_frames():
    entry = _animated_image_entry("PNG", ".png")
    assert flip_entry_data(entry, "horizontal")
    fmt, size, durations, _left, _right = read_frames(entry["bytes"])
    assert fmt == "PNG"
    assert size == (40, 60)
    assert durations == [100, 200, 300]


def test_rotate_lossless_animated_webp_stays_lossless():
    entry = _animated_image_entry("WEBP", ".webp", lossless=True)
    assert _webp_is_lossless(entry["bytes"])
    rotate_entry_data(entry, 90)
    assert _webp_is_lossless(entry["bytes"])
