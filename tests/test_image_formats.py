"""Conservation des formats d'image par les opérations de MosaicView.

Garde-fous contre le retour des régressions de format : format écrit d'après
l'extension, mode/alpha/palette conservés par rotation et miroir, GIF animés
traités frame par frame, réglages applicables à tous les modes PIL, alpha
conservé par les réglages de tonalité, conversions (transparence, animations),
qualité d'origine reprise au réenregistrement (sous-échantillonnage JPEG,
profil ICC, WebP sans perte). Images de synthèse 32x32 générées à la volée.
"""
import io

import numpy as np
import pytest
from PIL import Image, ImageCms, ImageSequence, JpegImagePlugin, features

from modules.qt.entries import (
    save_image_to_bytes,
    set_animated_image_info,
    _webp_is_lossless,
)
from modules.qt.image_ops import (
    convert_image_data,
    flip_entry_data,
    rotate_entry_data,
    transform_animated_gif,
)
from modules.qt.image_processing_qt import ENCODED_BYTES_KEY, apply_adjustments

HAS_AVIF = features.check("avif")
needs_avif = pytest.mark.skipif(not HAS_AVIF, reason="Pillow sans support AVIF")

SIZE = (32, 24)


# ---------------------------------------------------------------------------
# Fabrication des images de test
# ---------------------------------------------------------------------------

def make_image(mode):
    """Dégradé RGB converti dans le mode demandé. "RGBA" : moitié gauche
    totalement transparente. "P_T" : palette avec l'index 0 transparent."""
    w, h = SIZE
    arr = np.zeros((h, w, 3), np.uint8)
    arr[..., 0] = np.linspace(0, 255, w)[None, :]
    arr[..., 1] = np.linspace(0, 255, h)[:, None]
    arr[..., 2] = 128
    img = Image.fromarray(arr, "RGB")
    if mode == "RGBA":
        alpha = np.full((h, w), 255, np.uint8)
        alpha[:, : w // 2] = 0
        img = img.convert("RGBA")
        img.putalpha(Image.fromarray(alpha))
    elif mode == "P_T":
        img = img.convert("P", palette=Image.ADAPTIVE, colors=16)
    elif mode != "RGB":
        img = img.convert(mode)
    return img


def make_bytes(fmt, mode, **save_kwargs):
    img = make_image(mode)
    if mode == "P_T":
        save_kwargs.setdefault("transparency", 0)
    buf = io.BytesIO()
    img.save(buf, format=fmt, **save_kwargs)
    return buf.getvalue()


def make_square_ico():
    """Icône carrée 32x32 (une icône non carrée est redimensionnée par
    Pillow à l'enregistrement, ce qui fausserait les comparaisons)."""
    buf = io.BytesIO()
    make_image("RGBA").resize((32, 32)).save(buf, format="ICO", sizes=[(16, 16), (32, 32)])
    return buf.getvalue()


def make_animated(fmt="GIF"):
    frames = [Image.new("RGBA", SIZE, color) for color in
              ((255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255))]
    buf = io.BytesIO()
    frames[0].save(buf, format=fmt, save_all=True, append_images=frames[1:],
                   duration=[100, 200, 300], loop=0)
    return buf.getvalue()


def make_entry(name, data, **extra):
    entry = {
        "orig_name": name,
        "extension": "." + name.rsplit(".", 1)[1],
        "bytes": data,
        "img": None,
        "is_image": True,
        "is_corrupted": False,
        "dpi": None,
    }
    entry.update(extra)
    return entry


def open_bytes(data):
    return Image.open(io.BytesIO(data))


def transparent_count(data):
    alpha = np.asarray(open_bytes(data).convert("RGBA").getchannel("A"))
    return int((alpha == 0).sum())


def frame_count(data):
    return getattr(open_bytes(data), "n_frames", 1)


# ---------------------------------------------------------------------------
# save_image_to_bytes : le format écrit suit l'extension
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ext, expected", [
    (".jpg", "JPEG"), (".jpeg", "JPEG"), (".jfif", "JPEG"), (".pjpeg", "JPEG"),
    (".pjp", "JPEG"), (".png", "PNG"), (".webp", "WEBP"),
    pytest.param(".avif", "AVIF", marks=needs_avif),
    (".gif", "GIF"), (".bmp", "BMP"), (".tif", "TIFF"), (".tiff", "TIFF"),
    (".ico", "ICO"),
])
def test_save_image_to_bytes_format_follows_extension(ext, expected):
    entry = {"img": make_image("RGB"), "extension": ext, "bytes": None, "dpi": None}
    assert open_bytes(save_image_to_bytes(entry)).format == expected


def test_save_image_to_bytes_updates_dimensions():
    entry = {"img": make_image("RGB").rotate(90, expand=True), "extension": ".png",
             "bytes": None, "dpi": None, "img_width": 1, "img_height": 1}
    save_image_to_bytes(entry)
    assert (entry["img_width"], entry["img_height"]) == (SIZE[1], SIZE[0])


# ---------------------------------------------------------------------------
# Rotation / miroir : format, mode, palette et transparence conservés
# ---------------------------------------------------------------------------

ROTATE_CASES = [
    ("rgba.png", lambda: make_bytes("PNG", "RGBA")),
    ("p_t.png", lambda: make_bytes("PNG", "P_T")),
    ("l.png", lambda: make_bytes("PNG", "L")),
    ("p_t.gif", lambda: make_bytes("GIF", "P_T")),
    ("rgba.tif", lambda: make_bytes("TIFF", "RGBA")),
    ("rgba.ico", lambda: make_square_ico()),
    ("rgba.webp", lambda: make_bytes("WEBP", "RGBA", lossless=True)),
    ("rgb.bmp", lambda: make_bytes("BMP", "RGB")),
    ("gray.jpg", lambda: make_bytes("JPEG", "L")),
]


@pytest.mark.parametrize("operation", ["rotate", "flip"])
@pytest.mark.parametrize("name, make", ROTATE_CASES, ids=[c[0] for c in ROTATE_CASES])
def test_rotate_flip_keep_format_mode_and_transparency(name, make, operation):
    data = make()
    entry = make_entry(name, data)
    if operation == "rotate":
        assert rotate_entry_data(entry, -90)
    else:
        assert flip_entry_data(entry, "horizontal")
    before, after = open_bytes(data), open_bytes(entry["bytes"])
    assert after.format == before.format
    assert after.mode == before.mode
    assert transparent_count(entry["bytes"]) == transparent_count(data)


LOSSLESS_CASES = ["rgba.png", "p_t.png", "p_t.gif", "rgba.tif", "rgba.webp", "rgb.bmp"]


@pytest.mark.parametrize("name", LOSSLESS_CASES)
def test_four_rotations_are_lossless(name):
    data = dict(ROTATE_CASES)[name]()
    entry = make_entry(name, data)
    for _i in range(4):
        rotate_entry_data(entry, -90)
    original = np.asarray(open_bytes(data).convert("RGBA"))
    result = np.asarray(open_bytes(entry["bytes"]).convert("RGBA"))
    assert np.array_equal(original, result)


# ---------------------------------------------------------------------------
# GIF animé : toutes les frames conservées
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("operation", ["rotate", "flip"])
def test_animated_gif_rotate_flip_keep_frames(operation):
    data = make_animated()
    entry = make_entry("anim.gif", data, is_animated_gif=True)
    if operation == "rotate":
        rotate_entry_data(entry, -90)
    else:
        flip_entry_data(entry, "vertical")
    result = open_bytes(entry["bytes"])
    assert result.n_frames == 3
    assert [f.info.get("duration") for f in ImageSequence.Iterator(result)] == [100, 200, 300]
    assert result.info.get("loop") == 0


def test_transform_animated_gif_crop_keeps_frames_and_size():
    data = transform_animated_gif({"bytes": make_animated()}, lambda f: f.crop((0, 0, 16, 12)))
    assert frame_count(data) == 3
    assert open_bytes(data).size == (16, 12)


# ---------------------------------------------------------------------------
# Réglages : applicables à tous les modes PIL
# ---------------------------------------------------------------------------

def image_in_mode(mode):
    if mode == "I;16":
        arr = np.linspace(0, 65535, SIZE[0] * SIZE[1]).reshape(SIZE[1], SIZE[0])
        return Image.fromarray(arr.astype(np.uint16))
    return make_image("RGB").convert(mode)


@pytest.mark.parametrize("settings", [
    {"brightness": 20}, {"contrast": 20}, {"saturation": 20},
    {"sharpness": 20}, {"sharpness": -20}, {"unsharp_percent": 50},
], ids=["brightness", "contrast", "saturation", "sharpen", "blur", "unsharp"])
@pytest.mark.parametrize("mode", ["P", "1", "I;16", "CMYK"])
def test_adjustments_accept_every_mode(mode, settings):
    result = apply_adjustments(image_in_mode(mode), settings)
    assert result.size == SIZE


def test_brightness_is_not_inverted_on_cmyk():
    img = Image.new("RGB", (4, 4), (100, 100, 100)).convert("CMYK")
    brighter = apply_adjustments(img, {"brightness": 50}).convert("RGB").getpixel((0, 0))
    assert brighter[0] > 100


@pytest.mark.parametrize("settings", [
    {"threshold": 100}, {"black_point": 10}, {"remove_colors_intensity": 50},
], ids=["threshold", "levels", "remove_colors"])
def test_tone_adjustments_keep_transparency(settings):
    img = make_image("RGBA")
    expected = int((np.asarray(img.getchannel("A")) == 0).sum())
    result = apply_adjustments(img, settings)
    assert result.mode == "RGBA"
    assert int((np.asarray(result.getchannel("A")) == 0).sum()) == expected


@pytest.mark.parametrize("ext, fmt", [
    (".webp", "WEBP"),
    pytest.param(".avif", "AVIF", marks=needs_avif),
])
def test_native_compression_keeps_transparency(ext, fmt):
    img = open_bytes(make_bytes(fmt, "RGBA", quality=90))
    result = apply_adjustments(img, {"compression_quality": 30, "original_ext": ext})
    encoded = result.info.get(ENCODED_BYTES_KEY)
    assert encoded is not None
    decoded = open_bytes(encoded)
    assert decoded.format == fmt
    # Compression avec perte : l'alpha l'est aussi (AVIF à qualité 30 donne
    # 1 au lieu de 0) — on vérifie qu'il n'a pas été aplati, pas sa valeur exacte.
    alpha = np.asarray(decoded.convert("RGBA").getchannel("A"))
    half = SIZE[0] // 2
    assert alpha[:, :half].max() <= 8
    assert alpha[:, half:].min() >= 247


# ---------------------------------------------------------------------------
# Conversion de format
# ---------------------------------------------------------------------------

def test_convert_transparent_to_bmp_flattens_on_white():
    entry = make_entry("a.png", make_bytes("PNG", "RGBA"))
    new_entry, error = convert_image_data(entry, "BMP", 95)
    assert error is None
    assert open_bytes(new_entry["bytes"]).convert("RGB").getpixel((0, 0)) == (255, 255, 255)


def test_convert_transparent_to_gif_keeps_transparency():
    entry = make_entry("a.png", make_bytes("PNG", "RGBA"))
    new_entry, error = convert_image_data(entry, "GIF", 95)
    assert error is None
    assert transparent_count(new_entry["bytes"]) > 0


@pytest.mark.parametrize("target", ["BMP", "GIF"])
def test_convert_grayscale_with_alpha(target):
    entry = make_entry("a.png", make_bytes("PNG", "LA"))
    new_entry, error = convert_image_data(entry, target, 95)
    assert error is None and new_entry is not None


@pytest.mark.parametrize("target, frames", [
    ("WEBP", 3), ("PNG", 3),
    pytest.param("AVIF", 3, marks=needs_avif),
    ("JPEG", 1),
])
def test_convert_animated_gif(target, frames):
    entry = make_entry("anim.gif", make_animated(), is_animated_gif=True)
    new_entry, error = convert_image_data(entry, target, 90)
    assert error is None
    assert frame_count(new_entry["bytes"]) == frames
    if frames > 1:
        assert new_entry["is_animated_image"] is True
        assert new_entry["gif_durations"] == [100, 200, 300]


# ---------------------------------------------------------------------------
# WebP / PNG / AVIF animés : détection pour la lecture
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fmt, ext", [
    ("WEBP", ".webp"), ("PNG", ".png"),
    pytest.param("AVIF", ".avif", marks=needs_avif),
])
def test_set_animated_image_info_detects_frames_and_durations(fmt, ext):
    entry = {"extension": ext, "bytes": make_animated(fmt)}
    set_animated_image_info(entry)
    assert entry["is_animated_image"] is True
    assert entry["gif_frame_count"] == 3
    assert entry["gif_durations"] == [100, 200, 300]
    assert all(isinstance(d, int) for d in entry["gif_durations"])


def test_set_animated_image_info_ignores_still_image():
    entry = {"extension": ".png", "bytes": make_bytes("PNG", "RGB")}
    set_animated_image_info(entry)
    assert entry["is_animated_image"] is False


# ---------------------------------------------------------------------------
# Qualité d'origine reprise au réenregistrement
# ---------------------------------------------------------------------------

SRGB_ICC = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def test_jpeg_keeps_444_subsampling_and_icc_profile():
    data = make_bytes("JPEG", "RGB", quality=95, subsampling=0, icc_profile=SRGB_ICC)
    entry = make_entry("a.jpg", data)
    rotate_entry_data(entry, -90)
    result = open_bytes(entry["bytes"])
    assert JpegImagePlugin.get_sampling(result) == 0
    assert result.info.get("icc_profile") == SRGB_ICC


def test_icc_profile_not_matching_the_mode_is_dropped():
    img = Image.new("RGB", SIZE)
    img.info["icc_profile"] = b"\0" * 16 + b"CMYK" + b"\0" * 108
    entry = {"img": img, "extension": ".jpg", "bytes": None, "dpi": None}
    assert open_bytes(save_image_to_bytes(entry)).info.get("icc_profile") is None


def test_lossless_webp_stays_lossless_with_icc_profile():
    data = make_bytes("WEBP", "RGBA", lossless=True, icc_profile=SRGB_ICC)
    entry = make_entry("a.webp", data)
    rotate_entry_data(entry, -90)
    assert _webp_is_lossless(entry["bytes"])
    assert open_bytes(entry["bytes"]).info.get("icc_profile") == SRGB_ICC


def test_lossy_webp_stays_lossy():
    entry = make_entry("a.webp", make_bytes("WEBP", "RGB", quality=80))
    rotate_entry_data(entry, -90)
    assert not _webp_is_lossless(entry["bytes"])
