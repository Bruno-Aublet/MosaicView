"""Images à plusieurs frames, modes d'image peu courants, échecs de conversion.

- Un TIFF de plusieurs pages contenu dans une archive est une seule page de la
  mosaïque : rotation, miroir et découpe doivent garder toutes ses pages, et il
  ne se joint jamais (la jointure n'en garderait que la première).
- _to_writable_mode ne doit jamais écrêter les entiers 32 bits (page blanche)
  ni assombrir une page uniforme.
- convert_image_data explique pourquoi une page n'a pas pu être convertie,
  au lieu de l'ignorer (elle serait sinon supprimée avec les originaux).
"""
import io

from PIL import Image

from modules.qt.entries import create_entry
from modules.qt.image_ops import (
    _to_writable_mode,
    animations_joinable,
    cmyk_to_rgb,
    convert_image_data,
    flip_entry_data,
    has_several_frames,
    rotate_entry_data,
    transform_multipage_tiff,
)

TIFF_EXTS = (".tif", ".tiff", ".jpg", ".png")
COLORS = ((255, 0, 0), (0, 255, 0), (0, 0, 255))


def multipage_tiff(size=(40, 20)):
    pages = [Image.new("RGB", size, c) for c in COLORS]
    buf = io.BytesIO()
    pages[0].save(buf, "TIFF", save_all=True, append_images=pages[1:],
                  compression="tiff_lzw", dpi=(300, 300))
    return buf.getvalue()


def tiff_pages(data):
    im = Image.open(io.BytesIO(data))
    out = []
    for i in range(im.n_frames):
        im.seek(i)
        out.append(im.convert("RGB"))
    return out


# ---------------------------------------------------------------------------
# TIFF de plusieurs pages
# ---------------------------------------------------------------------------

def test_multipage_tiff_is_detected():
    entry = create_entry("p.tif", multipage_tiff(), TIFF_EXTS)
    assert entry["is_multipage_tiff"]
    assert has_several_frames(entry)
    assert not entry.get("is_animated_image")


def test_single_page_tiff_is_not_multipage():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, "TIFF")
    entry = create_entry("s.tif", buf.getvalue(), TIFF_EXTS)
    assert not entry["is_multipage_tiff"]
    assert not has_several_frames(entry)


def test_rotation_keeps_every_tiff_page():
    entry = create_entry("p.tif", multipage_tiff(), TIFF_EXTS)
    assert rotate_entry_data(entry, 90)
    pages = tiff_pages(entry["bytes"])
    assert len(pages) == 3
    assert all(p.size == (20, 40) for p in pages)
    assert [p.getpixel((0, 0)) for p in pages] == list(COLORS)


def test_rotation_keeps_tiff_compression_and_dpi():
    entry = create_entry("p.tif", multipage_tiff(), TIFF_EXTS)
    rotate_entry_data(entry, -90)
    im = Image.open(io.BytesIO(entry["bytes"]))
    assert im.info.get("compression") == "tiff_lzw"
    assert round(im.info["dpi"][0]) == 300


def test_mirror_keeps_every_tiff_page():
    entry = create_entry("p.tif", multipage_tiff(), TIFF_EXTS)
    assert flip_entry_data(entry, "horizontal")
    assert len(tiff_pages(entry["bytes"])) == 3


def test_crop_of_multipage_tiff_keeps_every_page():
    """Découpe d'une page (split_page) : chaque morceau garde toutes les pages."""
    data = transform_multipage_tiff({"bytes": multipage_tiff()}, lambda f: f.crop((0, 0, 20, 20)))
    pages = tiff_pages(data)
    assert len(pages) == 3
    assert all(p.size == (20, 20) for p in pages)


def test_multipage_tiff_is_never_joinable():
    tiff = create_entry("p.tif", multipage_tiff(), TIFF_EXTS)
    still = {"orig_name": "a.jpg", "is_image": True}
    assert not animations_joinable([tiff, still])
    assert animations_joinable([still, dict(still)])


# ---------------------------------------------------------------------------
# _to_writable_mode / cmyk_to_rgb
# ---------------------------------------------------------------------------

def test_32bit_values_are_scaled_not_clipped():
    img = Image.new("I", (4, 4), 0)
    img.putpixel((0, 0), 70000)
    img.putpixel((1, 0), 35000)
    out = _to_writable_mode(img)
    assert out.mode == "L"
    assert out.getpixel((0, 0)) == 255
    assert 120 <= out.getpixel((1, 0)) <= 135      # niveau relatif conservé
    assert out.getpixel((2, 0)) == 0


def test_uniform_32bit_page_stays_light():
    """Une page uniforme au-dessus de 255 ne doit pas devenir noire."""
    out = _to_writable_mode(Image.new("I", (4, 4), 40000))
    assert out.getextrema()[0] >= 250


def test_negative_float_values_are_stretched():
    img = Image.new("F", (4, 4), -5.0)
    img.putpixel((0, 0), 5.0)
    out = _to_writable_mode(img)
    assert out.getextrema() == (0, 255)


def test_8bit_range_is_left_unchanged():
    out = _to_writable_mode(Image.new("I", (4, 4), 100))
    assert out.getextrema() == (100, 100)


def test_cmyk_with_unusable_profile_falls_back_to_plain_conversion():
    img = Image.new("CMYK", (4, 4), (0, 255, 255, 0))
    img.info["icc_profile"] = b"not a real profile"
    out = cmyk_to_rgb(img)
    assert out.mode == "RGB"
    assert out.getpixel((0, 0)) == (255, 0, 0)


# ---------------------------------------------------------------------------
# convert_image_data : cause de l'échec
# ---------------------------------------------------------------------------

def test_page_too_tall_for_webp_is_reported_not_dropped():
    buf = io.BytesIO()
    Image.new("RGB", (10, 17000), "white").save(buf, "PNG")
    entry = create_entry("long.png", buf.getvalue(), TIFF_EXTS)
    new_entry, failure = convert_image_data(entry, "WEBP", 90)
    assert new_entry is None
    assert failure["kind"] == "too_large"
    assert failure["max"] == 16383
    assert failure["height"] == 17000
    assert failure["name"] == "long.png"


def test_unreadable_page_is_reported():
    entry = {"orig_name": "bad.png", "bytes": b"not an image", "extension": ".png",
             "is_image": True, "img": None}
    new_entry, failure = convert_image_data(entry, "JPEG", 90)
    assert new_entry is None
    assert failure["kind"] == "unreadable"


def test_16bit_grayscale_converts_to_jpeg():
    img = Image.new("I;16", (8, 8), 40000)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    entry = create_entry("g16.png", buf.getvalue(), TIFF_EXTS)
    new_entry, failure = convert_image_data(entry, "JPEG", 90)
    assert failure is None
    assert new_entry is not None
