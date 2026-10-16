"""Ce que les opérations de MosaicView doivent conserver d'une page :
orientation EXIF (page affichée et réenregistrée droite, EXIF gardé sans
l'orientation), profil ICC, qualité choisie dans l'outil Compression,
transparence (conversion vers TIFF, jointure), niveaux de gris 16 bits
(affichage, réglages, outils de dessin), boucle d'un GIF joué une fois.

Chaque test compare une propriété du résultat à la valeur attendue, jamais
la seule réouverture du fichier. Images de synthèse générées à la volée.
"""
import io

import numpy as np
import pytest
from PIL import Image, ImageCms

from modules.qt.entries import (
    animation_play_count,
    create_entry,
    detect_jpeg_quality,
    drawing_base_image,
    drawn_page_image,
    open_page_image,
    page_high_depth,
    page_size,
    rotate_page_image,
    save_image_to_bytes,
    to_display_mode,
)
from modules.qt.image_ops import (
    convert_image_data,
    merge_images_2d,
    transform_animated_gif,
    transform_multipage_tiff,
)
from modules.qt.image_processing_qt import ENCODED_BYTES_KEY, apply_adjustments

ORIENTATION = 0x0112
MAKE = 0x010F
SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def encode(img, fmt, **kwargs):
    buf = io.BytesIO()
    img.save(buf, fmt, **kwargs)
    return buf.getvalue()


def decode(data):
    return Image.open(io.BytesIO(data))


def rotated_photo():
    """JPEG de 40×20 stocké couché, avec Orientation=6 (à afficher tourné
    d'un quart de tour : 20×40), marque d'appareil et profil sRGB."""
    exif = Image.Exif()
    exif[ORIENTATION] = 6
    exif[MAKE] = "Canon"
    pixels = np.zeros((20, 40, 3), np.uint8)
    pixels[:, :20] = 255
    return encode(Image.fromarray(pixels), "JPEG", exif=exif.tobytes(),
                  icc_profile=SRGB, quality=90)


def page_entry(name, data, img=None):
    return {"orig_name": name, "extension": "." + name.rsplit(".", 1)[1],
            "bytes": data, "img": img, "is_image": True, "dpi": None}


# ---------------------------------------------------------------------------
# Orientation EXIF
# ---------------------------------------------------------------------------

def test_page_is_shown_upright():
    data = rotated_photo()
    assert open_page_image(data).size == (20, 40)
    assert page_size(data) == (20, 40)
    entry = create_entry("p.jpg", data, (".jpg",))
    assert (entry["img_width"], entry["img_height"]) == (20, 40)


def test_edited_page_stays_upright_and_keeps_its_exif():
    """Pixels tournés, orientation retirée (sinon les lecteurs la
    tourneraient une seconde fois), reste de l'EXIF et profil gardés."""
    data = rotated_photo()
    img = apply_adjustments(open_page_image(data), {"brightness": 20, "original_ext": ".jpg"})
    out = decode(save_image_to_bytes(page_entry("p.jpg", data, img)))
    assert out.size == (20, 40)
    assert out.getexif().get(ORIENTATION) is None
    assert out.getexif().get(MAKE) == "Canon"
    assert out.info.get("icc_profile")


def test_conversion_keeps_icc_and_exif_without_orientation():
    data = rotated_photo()
    for target in ("WEBP", "PNG", "TIFF", "JPEG"):
        new_entry, error = convert_image_data(page_entry("p.jpg", data), target, 90)
        assert error is None
        out = decode(new_entry["bytes"])
        assert out.size == (20, 40), target
        assert out.info.get("icc_profile"), target
        assert out.getexif().get(ORIENTATION) is None, target
        if target != "TIFF":   # pas d'EXIF écrit dans un TIFF
            assert out.getexif().get(MAKE) == "Canon", target


def test_tiff_page_rotation_keeps_its_size_and_pixels():
    """Les balises d'un TIFF (largeur, hauteur…) ne sont jamais réécrites
    comme des métadonnées : la page tournée garderait ses anciennes
    dimensions."""
    page = Image.new("RGBA", (32, 24), (255, 0, 0, 0))
    page.paste((0, 0, 255, 255), (16, 0, 32, 24))
    data = encode(page, "TIFF")
    entry = page_entry("p.tif", data)
    rotated = open_page_image(data).rotate(90, expand=True)
    entry["img"] = rotated
    out = decode(save_image_to_bytes(entry))
    assert out.size == (24, 32)
    assert np.array_equal(np.asarray(out.convert("RGBA")), np.asarray(rotated))


# ---------------------------------------------------------------------------
# Profil ICC
# ---------------------------------------------------------------------------

def test_png_keeps_its_profile_after_brightness():
    data = encode(Image.new("RGB", (16, 16), (100, 50, 20)), "PNG", icc_profile=SRGB)
    img = apply_adjustments(open_page_image(data), {"brightness": 20, "original_ext": ".png"})
    assert decode(save_image_to_bytes(page_entry("p.png", data, img))).info.get("icc_profile")


def test_rgb_profile_is_not_written_into_a_grayscale_png():
    data = encode(Image.new("RGB", (16, 16), (100, 50, 20)), "PNG", icc_profile=SRGB)
    gray = open_page_image(data).convert("L")
    assert decode(save_image_to_bytes(page_entry("p.png", data, gray))).info.get("icc_profile") is None


# ---------------------------------------------------------------------------
# Outil Compression sur un JPEG
# ---------------------------------------------------------------------------

def test_jpeg_compression_writes_the_chosen_quality():
    """Les bytes encodés à la qualité choisie sont repris tels quels : un
    réencodage à la qualité d'origine doublait la taille du fichier."""
    rng = np.random.default_rng(0)
    pixels = rng.random((300, 400, 3)) * 60 + np.linspace(0, 190, 400)[None, :, None]
    page = Image.fromarray(pixels.astype(np.uint8))
    original = encode(page, "JPEG", quality=95)
    result = apply_adjustments(open_page_image(original),
                               {"compression_quality": 30, "original_ext": ".jpg"})
    encoded = result.info[ENCODED_BYTES_KEY]
    direct = encode(page, "JPEG", quality=30, optimize=True)
    assert len(encoded) < len(direct) * 1.1


# ---------------------------------------------------------------------------
# Transparence
# ---------------------------------------------------------------------------

def test_transparent_palette_converted_to_tiff_stays_transparent():
    palette = Image.new("P", (8, 8), 0)
    palette.putpalette([255, 0, 0, 0, 255, 0] + [0] * 762)
    palette.info["transparency"] = 0
    new_entry, error = convert_image_data(page_entry("t.gif", encode(palette, "GIF")), "TIFF", 95)
    assert error is None
    assert decode(new_entry["bytes"]).convert("RGBA").getpixel((0, 0))[3] == 0


def _join_positions():
    transparent = Image.new("RGBA", (10, 10), (255, 0, 0, 0))
    opaque = Image.new("RGB", (10, 20), "blue")
    return [{"entry": {"img": transparent}, "x": 0, "y": 0},
            {"entry": {"img": opaque}, "x": 10, "y": 0}]


def test_join_keeps_transparency_and_fills_uncovered_areas_in_white():
    merged = merge_images_2d(_join_positions(), keep_alpha=True)
    assert merged.mode == "RGBA"
    assert merged.getpixel((2, 8))[3] == 0                      # dans la page transparente
    assert merged.getpixel((2, 1)) == (255, 255, 255, 255)      # couvert par aucune page


def test_flattened_join_never_shows_hidden_colors():
    merged = merge_images_2d(_join_positions())
    assert merged.getpixel((2, 8)) == (255, 255, 255)


# ---------------------------------------------------------------------------
# Niveaux de gris 16 bits
# ---------------------------------------------------------------------------

def gray16_page():
    return encode(Image.fromarray(np.arange(64, dtype=np.uint16).reshape(8, 8) * 1000), "PNG")


def test_16bit_page_is_not_shown_white():
    assert drawing_base_image(gray16_page()).getpixel((3, 3))[:3] != (255, 255, 255)
    assert to_display_mode(decode(gray16_page())).mode == "L"


def test_16bit_page_joined_is_not_white():
    page = Image.fromarray(np.full((8, 8), 40000, np.uint16))
    assert merge_images_2d([{"entry": {"img": page}, "x": 0, "y": 0}]).getpixel((0, 0)) != (255, 255, 255)


def test_16bit_page_converted_to_color_is_not_white():
    result = apply_adjustments(decode(gray16_page()), {"image_mode": "RGB"})
    assert result.getpixel((3, 3)) != (255, 255, 255)


def test_drawing_keeps_16bit_precision_outside_the_drawing():
    data = gray16_page()
    drawn = drawing_base_image(data)
    drawn.putpixel((0, 0), (255, 0, 0, 255))
    result = drawn_page_image(data, drawn)
    assert result.mode == "I;16"
    assert result.getpixel((3, 3)) == decode(data).getpixel((3, 3))
    red_as_gray = Image.new("RGB", (1, 1), (255, 0, 0)).convert("L").getpixel((0, 0))
    assert result.getpixel((0, 0)) == red_as_gray * 257


def test_drawing_on_an_8bit_page_is_returned_unchanged():
    data = encode(Image.new("RGB", (8, 8), "white"), "PNG")
    drawn = drawing_base_image(data)
    assert drawn_page_image(data, drawn) is drawn


def test_drawing_on_a_grayscale_jpeg_page_stays_grayscale():
    data = encode(Image.new("L", (8, 8), 200), "JPEG")
    drawn = drawing_base_image(data)
    drawn.putpixel((0, 0), (255, 0, 0, 255))
    result = drawn_page_image(data, drawn)
    assert result.mode == "L"


def test_drawing_on_a_1bit_page_stays_1bit():
    data = encode(Image.new("1", (8, 8), 1), "PNG")
    drawn = drawing_base_image(data)
    drawn.putpixel((0, 0), (0, 0, 0, 255))
    result = drawn_page_image(data, drawn)
    assert result.mode == "1"
    assert result.getpixel((0, 0)) == 0 and result.getpixel((3, 3)) == 255


def test_drawing_on_a_palette_png_keeps_a_palette_and_its_transparency():
    page = Image.new("RGBA", (8, 8), (255, 0, 0, 0)).quantize(colors=16)
    data = encode(page, "PNG")
    drawn = drawing_base_image(data)
    drawn.putpixel((0, 0), (0, 0, 255, 255))
    result = drawn_page_image(data, drawn)
    assert result.mode == "P"
    reread = decode(encode(result, "PNG")).convert("RGBA")
    assert reread.getpixel((3, 3))[3] == 0
    assert reread.getpixel((0, 0)) == (0, 0, 255, 255)


# ---------------------------------------------------------------------------
# Conversion : WebP sans perte, 16 bits, résolution, boucle d'une animation
# ---------------------------------------------------------------------------

def test_lossless_webp_conversion_is_pixel_identical():
    rng = np.random.default_rng(0)
    pixels = (rng.random((24, 32, 3)) * 255).astype(np.uint8)
    data = encode(Image.fromarray(pixels), "PNG")
    new_entry, error = convert_image_data(page_entry("p.png", data), "WEBP", 90, lossless=True)
    assert error is None
    assert np.array_equal(np.asarray(decode(new_entry["bytes"]).convert("RGB")), pixels)


def test_16bit_page_converted_to_png_or_tiff_stays_16bit():
    data = encode(Image.fromarray(np.full((8, 8), 30000, np.uint16)), "TIFF")
    for target in ("PNG", "TIFF"):
        new_entry, error = convert_image_data(page_entry("p.tif", data), target, 95)
        assert error is None
        out = decode(new_entry["bytes"])
        assert out.mode.startswith("I"), target
        assert out.getpixel((0, 0)) == 30000, target


def test_png_conversion_writes_the_resolution_rounded():
    data = encode(Image.new("RGB", (8, 8)), "PNG", dpi=(300, 300))
    new_entry, error = convert_image_data(page_entry("p.png", data), "PNG", 95)
    assert error is None
    assert round(decode(new_entry["bytes"]).info["dpi"][0]) == 300
    new_entry, error = convert_image_data(page_entry("p.png", data), "JPEG", 95)
    assert decode(new_entry["bytes"]).info["dpi"] == (300, 300)


def test_gif_played_once_stays_played_once_in_webp_and_png():
    frames = [Image.new("RGB", (8, 8), color) for color in ("red", "blue")]
    gif = encode(frames[0], "GIF", save_all=True, append_images=frames[1:], duration=100)
    entry = page_entry("a.gif", gif)
    entry["is_animated_gif"] = True
    for target in ("WEBP", "PNG"):
        new_entry, error = convert_image_data(entry, target, 90)
        assert error is None
        assert decode(new_entry["bytes"]).info.get("loop") == 1, target


# ---------------------------------------------------------------------------
# GIF animé joué une fois
# ---------------------------------------------------------------------------

def test_gif_played_once_has_no_loop():
    frames = [Image.new("RGB", (8, 8), color) for color in ("red", "blue")]
    data = encode(frames[0], "GIF", save_all=True, append_images=frames[1:], duration=100)
    assert create_entry("a.gif", data, (".gif",))["gif_loop"] is None


def test_viewer_plays_animation_as_many_times_as_the_file_says():
    frames = [Image.new("RGB", (8, 8), color) for color in ("red", "blue")]

    def plays(fmt, **loop):
        data = encode(frames[0], fmt, save_all=True, append_images=frames[1:], duration=100, **loop)
        return animation_play_count({"bytes": data})

    assert plays("GIF") == 1
    assert plays("GIF", loop=2) == 3
    assert plays("GIF", loop=0) is None
    assert plays("WEBP", loop=1) == 1
    assert plays("WEBP") is None
    assert plays("PNG", loop=3) == 3
    assert plays("PNG") is None


# ---------------------------------------------------------------------------
# Compression TIFF
# ---------------------------------------------------------------------------

def test_tiff_page_keeps_its_compression_after_an_edit():
    data = encode(Image.new("RGB", (16, 16), "red"), "TIFF", compression="tiff_lzw")
    # Image recomposée par un outil : img.info (compression comprise) perdu
    entry = page_entry("p.tif", data, Image.new("RGB", (16, 16), "blue"))
    assert decode(save_image_to_bytes(entry)).info["compression"] == "tiff_lzw"


def test_group4_page_turned_gray_is_written_with_a_compatible_compression():
    data = encode(Image.new("1", (16, 16), 1), "TIFF", compression="group4")
    # convert garde img.info["compression"] = "group4", que Pillow ne sait
    # pas écrire en niveaux de gris (le programme plantait)
    gray = decode(data).convert("L")
    out = decode(save_image_to_bytes(page_entry("p.tif", data, gray)))
    assert out.mode == "L"
    assert out.info["compression"] == "tiff_lzw"


def test_multipage_group4_tiff_can_be_rotated():
    pages = [Image.new("1", (16, 24), 1) for _i in range(3)]
    data = encode(pages[0], "TIFF", save_all=True, append_images=pages[1:], compression="group4")
    out = decode(transform_multipage_tiff({"bytes": data}, lambda f: f.transpose(Image.ROTATE_90)))
    assert out.n_frames == 3
    assert out.size == (24, 16)
    assert out.info["compression"] == "tiff_lzw"


def test_conversion_to_tiff_is_compressed_without_loss():
    img = Image.new("RGB", (16, 16), (10, 120, 200))
    new_entry, error = convert_image_data(page_entry("p.png", encode(img, "PNG")), "TIFF", 95)
    assert error is None
    out = decode(new_entry["bytes"])
    assert out.info["compression"] == "tiff_lzw"
    assert out.convert("RGB").tobytes() == img.tobytes()


# ---------------------------------------------------------------------------
# GIF animé transformé
# ---------------------------------------------------------------------------

def many_colors_gif(transparent=False):
    frames = []
    for k in range(4):
        frame = Image.new("RGBA", (60, 40), (0, 0, 0, 0) if transparent else (0, 0, 0, 255))
        for x in range(60):
            for y in range(20):
                frame.putpixel((x, y), (x * 4, 255 - x * 4, (x * 7) % 256, 255))
        frame.putpixel((k * 10, 30), (255, 0, 0, 255))
        frames.append(frame)
    return encode(frames[0], "GIF", save_all=True, append_images=frames[1:],
                  duration=[100, 200, 300, 400], loop=0, comment=b"note", disposal=2)


def gif_frames(data):
    src = decode(data)
    frames = []
    for i in range(src.n_frames):
        src.seek(i)
        frames.append(src.convert("RGBA"))
    return frames


@pytest.mark.parametrize("transparent", [False, True])
def test_rotated_gif_keeps_exact_colors_comment_and_durations(transparent):
    data = many_colors_gif(transparent)
    out = transform_animated_gif({"bytes": data}, lambda f: f.transpose(Image.ROTATE_90))
    expected = [f.transpose(Image.ROTATE_90) for f in gif_frames(data)]
    assert [f.tobytes() for f in gif_frames(out)] == [f.tobytes() for f in expected]
    assert decode(out).info.get("comment") == b"note"
    src = decode(out)
    durations = []
    for i in range(src.n_frames):
        src.seek(i)
        durations.append(src.info["duration"])
    assert durations == [100, 200, 300, 400]


# ---------------------------------------------------------------------------
# Résolution, qualité JPEG, profondeur
# ---------------------------------------------------------------------------

def test_different_horizontal_and_vertical_resolutions_are_kept():
    data = encode(Image.new("RGB", (16, 16), "red"), "PNG", dpi=(300, 600))
    entry = page_entry("p.png", data, Image.new("RGB", (16, 16), "blue"))
    entry["dpi"] = 300
    x, y = decode(save_image_to_bytes(entry)).info["dpi"]
    assert (round(x), round(y)) == (300, 600)
    # Résolution changée par l'utilisateur : la nouvelle valeur vaut dans les deux sens
    entry = page_entry("p.png", data, Image.new("RGB", (16, 16), "blue"))
    entry["dpi"] = 150
    x, y = decode(save_image_to_bytes(entry)).info["dpi"]
    assert (round(x), round(y)) == (150, 150)


def test_jpeg_quality_estimate():
    for quality in (60, 80, 95):
        assert detect_jpeg_quality(encode(Image.new("RGB", (32, 32), "red"), "JPEG",
                                          quality=quality)) == quality
    assert detect_jpeg_quality(encode(Image.new("RGB", (8, 8)), "PNG")) == 95


def test_compression_tool_uses_the_same_jpeg_quality_estimate():
    from modules.qt.image_processing_qt import detect_jpeg_quality as tool_estimate
    for quality in (55, 72, 88):
        data = encode(Image.new("RGB", (32, 32), "red"), "JPEG", quality=quality)
        assert tool_estimate(data) == detect_jpeg_quality(data) == quality
    # Pas un JPEG : None, l'outil part de sa propre valeur
    assert tool_estimate(encode(Image.new("RGB", (8, 8)), "WEBP")) is None


def test_cmyk_page_is_shown_through_its_profile(monkeypatch):
    from modules.qt import image_ops
    from modules.qt.entries import to_screen_mode
    calls = []
    real = image_ops.cmyk_to_rgb

    def spy(img):
        calls.append(img.mode)
        return real(img)

    monkeypatch.setattr(image_ops, "cmyk_to_rgb", spy)
    cmyk = Image.new("CMYK", (4, 4), (0, 255, 255, 0))
    assert to_screen_mode(cmyk).mode == "RGB"
    assert calls == ["CMYK"]
    # Les autres pages : conversion d'affichage habituelle
    rgb = Image.new("RGB", (4, 4), "red")
    assert to_screen_mode(rgb) is rgb
    gray16 = Image.fromarray(np.full((4, 4), 65535, np.uint16))
    assert to_screen_mode(gray16).mode == "L"


def png_48_bits():
    """PNG RGB à 16 bits par couleur (Pillow ne sait pas en écrire)."""
    import struct
    import zlib
    width = height = 4
    raw = b"".join(b"\x00" + b"\x12\x34" * 3 * width for _i in range(height))

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 16, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_pages_above_8_bits_per_color_are_detected():
    gray16 = Image.fromarray(np.full((4, 4), 30000, np.uint16))
    assert page_high_depth(encode(gray16, "PNG")) == "gray16"
    assert page_high_depth(encode(gray16, "TIFF")) == "gray16"
    assert page_high_depth(png_48_bits()) == "color16"
    assert page_high_depth(encode(Image.new("RGB", (4, 4)), "PNG")) is None
    assert page_high_depth(encode(Image.new("L", (4, 4)), "TIFF")) is None
    assert page_high_depth(None) is None


def test_straightened_16bit_page_keeps_its_values():
    values = np.tile(np.linspace(0, 65535, 64).astype(np.uint16), (48, 1))
    img = Image.fromarray(values)
    for fill, corner in ((None, 0), ("white", 65535)):
        rotated = rotate_page_image(img, 5, fillcolor=fill)
        out = np.asarray(rotated)
        assert rotated.mode == "I;16"
        assert out.max() > 60000          # Pillow seul plafonnait à 255
        assert out[0, 0] == corner
    # Toute autre page : rotation habituelle
    rgb = rotate_page_image(Image.new("RGB", (20, 20), "red"), 5, fillcolor="white")
    assert rgb.mode == "RGB" and rgb.getpixel((0, 0)) == (255, 255, 255)
