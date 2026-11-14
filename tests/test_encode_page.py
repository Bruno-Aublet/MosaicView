"""entries.encode_page : seule fonction qui encode une page (enregistrement,
conversion, découpe, jointure, lots, simulation de compression). Chaque
règle commune y est vérifiée sur les bytes produits, relus par Pillow :

- format d'après l'extension (page_format), PNG pour une extension inconnue ;
- mode : gardé s'il s'écrit dans le format, sinon le plus proche qui garde ce
  que le format stocke ; transparence aplatie sur fond blanc pour JPEG seul ;
- BMP transparent écrit avec sa transparence déclarée, relu en RGBA ;
- profil ICC, résolution, métadonnées de la page d'origine ;
- qualité et sous-échantillonnage JPEG, WebP sans perte, compression TIFF
  (celle d'origine, sinon LZW), tailles d'une icône.
"""
import io

import pytest
from PIL import Image, ImageCms

from modules.qt.entries import encode_page, page_format, save_image_to_bytes


def reread(data):
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def encoded(img, fmt, **kwargs):
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def half_transparent():
    """Pixel 0 : rouge totalement transparent ; pixel 1 : bleu opaque."""
    img = Image.new("RGBA", (2, 1))
    img.putpixel((0, 0), (255, 0, 0, 0))
    img.putpixel((1, 0), (0, 0, 255, 255))
    return img


# ---------------------------------------------------------------------------
# Format d'après l'extension
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ext, fmt", [
    (".jpg", "JPEG"), (".JPEG", "JPEG"), (".jfif", "JPEG"), (".pjp", "JPEG"),
    (".png", "PNG"), (".webp", "WEBP"), (".gif", "GIF"), (".bmp", "BMP"),
    (".tif", "TIFF"), (".tiff", "TIFF"), (".avif", "AVIF"), (".ico", "ICO"),
    (".xyz", None), ("", None),
])
def test_page_format_never_comes_from_the_extension_name(ext, fmt):
    assert page_format(ext) == fmt


def test_unknown_format_is_written_as_png_with_its_profile():
    img = Image.new("RGB", (4, 4), (10, 20, 30))
    source = encoded(img, "PNG", icc_profile=SRGB)
    out = reread(encode_page(img, "XYZ", source))
    assert out.format == "PNG"
    assert out.info.get("icc_profile") == SRGB


# ---------------------------------------------------------------------------
# Transparence
# ---------------------------------------------------------------------------

def test_jpeg_flattens_transparency_on_white():
    out = reread(encode_page(half_transparent(), "JPEG", quality=100, subsampling=0))
    assert out.mode == "RGB"
    r, g, b = out.getpixel((0, 0))
    assert min(r, g, b) > 240                       # blanc, pas le rouge caché


def test_bmp_keeps_its_transparency_and_is_reread_with_it():
    """Pillow écrit un BMP transparent sans déclarer sa transparence : relu,
    le pixel rouge transparent redevenait rouge opaque."""
    out = reread(encode_page(half_transparent(), "BMP"))
    assert out.mode == "RGBA"
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((1, 0)) == (0, 0, 255, 255)


def test_bmp_transparent_keeps_its_resolution():
    data = encode_page(half_transparent(), "BMP", dpi=(300, 300))
    assert [round(v) for v in reread(data).info["dpi"]] == [300, 300]


def test_tools_allow_transparency_on_a_bmp_page():
    """La transparence d'un BMP étant écrite et relue, les outils ne la
    bloquent plus : outil Transparence, mode RGBA, profondeur 32 bits, et les
    outils de dessin ne l'aplatissent plus (la liste des formats qui la
    stockent y comprend .bmp)."""
    import ast_loader
    assert ".bmp" in ast_loader.load("transparency_tool_qt.py", {"_SUPPORTED_EXTS"})["_SUPPORTED_EXTS"]
    modes = ast_loader.load("image_mode_tool_qt.py", {"_BLOCKED_MODE_KEYS_BY_EXT"})
    assert "RGBA" not in modes["_BLOCKED_MODE_KEYS_BY_EXT"][".bmp"]
    depths = ast_loader.load("color_depth_tool_qt.py", {"_BLOCKED_DEPTH_KEYS_BY_EXT"})
    assert "32" not in depths["_BLOCKED_DEPTH_KEYS_BY_EXT"].get(".bmp", set())
    from pathlib import Path
    qt = Path(ast_loader.QT_DIR)
    for name in ("clone_tool_qt.py", "paste_image_tool_qt.py", "shapes_tool_qt.py",
                 "text_tool_qt.py", "blur_tool_qt.py"):
        assert "'.ico', '.bmp'" in (qt / name).read_text(encoding="utf-8"), name


@pytest.mark.parametrize("mode", ["LA", "PA"])
def test_bmp_of_other_transparent_modes_keeps_transparency(mode):
    img = half_transparent().convert(mode)
    assert reread(encode_page(img, "BMP")).mode == "RGBA"


def test_bmp_of_a_transparent_palette_page_keeps_its_transparency():
    """BMP ne stocke pas la transparence d'une palette : écrit tel quel, le
    pixel transparent redevenait opaque."""
    img = half_transparent().convert("P")
    img.info["transparency"] = img.getpixel((0, 0))
    out = reread(encode_page(img, "BMP"))
    assert out.mode == "RGBA" and out.getpixel((0, 0))[3] == 0


def test_opaque_bmp_is_an_ordinary_bmp():
    data = encode_page(Image.new("RGB", (3, 2), (1, 2, 3)), "BMP")
    assert int.from_bytes(data[14:18], "little") == 40      # en-tête de base
    assert reread(data).getpixel((0, 0)) == (1, 2, 3)


@pytest.mark.parametrize("fmt", ["PNG", "WEBP", "TIFF", "GIF"])
def test_formats_that_store_transparency_keep_it(fmt):
    kwargs = {"lossless": True} if fmt == "WEBP" else {}
    out = reread(encode_page(half_transparent(), fmt, **kwargs)).convert("RGBA")
    assert out.getpixel((0, 0))[3] == 0


def test_transparent_palette_page_in_tiff_keeps_its_transparency():
    img = half_transparent().convert("P")
    img.info["transparency"] = img.getpixel((0, 0))
    out = reread(encode_page(img, "TIFF")).convert("RGBA")
    assert out.getpixel((0, 0))[3] == 0


# ---------------------------------------------------------------------------
# Modes que le format n'écrit pas
# ---------------------------------------------------------------------------

def test_one_bit_page_can_be_written_as_gif():
    """Une page GIF passée en 1 bit faisait échouer l'enregistrement."""
    img = Image.new("1", (4, 4), 1)
    assert reread(encode_page(img, "GIF")).format == "GIF"


def test_cmyk_page_is_kept_in_cmyk_where_the_format_stores_it():
    img = Image.new("CMYK", (4, 4), (0, 100, 200, 0))
    assert reread(encode_page(img, "JPEG")).mode == "CMYK"
    assert reread(encode_page(img, "TIFF")).mode == "CMYK"


@pytest.mark.parametrize("fmt", ["PNG", "BMP", "ICO", "WEBP"])
def test_cmyk_page_is_converted_where_the_format_does_not_store_it(fmt):
    img = Image.new("CMYK", (16, 16), (0, 0, 0, 0))          # blanc
    out = reread(encode_page(img, fmt)).convert("RGB")
    assert min(out.getpixel((0, 0))) > 240


def test_16_bit_gray_stays_16_bit_in_png_and_tiff():
    img = Image.new("I;16", (4, 4), 40000)
    assert reread(encode_page(img, "PNG")).mode.startswith("I")
    assert reread(encode_page(img, "TIFF")).mode.startswith("I;16")


def test_16_bit_gray_is_scaled_not_clipped_in_jpeg():
    img = Image.new("I;16", (4, 4), 32768)                   # gris moyen
    value = reread(encode_page(img, "JPEG", quality=100)).getpixel((0, 0))
    assert 120 <= value <= 135


# ---------------------------------------------------------------------------
# Profil, résolution, métadonnées
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "TIFF", "WEBP"])
def test_profile_of_the_original_page_is_kept(fmt):
    """Image recomposée par un outil (img.info perdu) : profil repris des
    bytes d'origine."""
    source = encoded(Image.new("RGB", (4, 4)), "PNG", icc_profile=SRGB)
    out = reread(encode_page(Image.new("RGB", (4, 4), (9, 9, 9)), fmt, source))
    assert out.info.get("icc_profile") == SRGB


def test_profile_not_matching_the_written_mode_is_dropped():
    source = encoded(Image.new("RGB", (4, 4)), "PNG", icc_profile=SRGB)
    out = reread(encode_page(Image.new("L", (4, 4), 9), "PNG", source))
    assert not out.info.get("icc_profile")


def test_resolution_of_the_original_page_is_kept_by_default():
    source = encoded(Image.new("RGB", (4, 4)), "PNG", dpi=(300, 600))
    out = reread(encode_page(Image.new("RGB", (4, 4)), "TIFF", source))
    assert [round(float(v)) for v in out.info["dpi"]] == [300, 600]


def test_resolution_can_be_left_out():
    source = encoded(Image.new("RGB", (4, 4)), "PNG", dpi=(300, 300))
    out = reread(encode_page(Image.new("RGB", (4, 4)), "PNG", source, dpi=None))
    assert "dpi" not in out.info


def test_exif_of_the_original_page_is_kept_without_its_orientation():
    exif = Image.Exif()
    exif[0x010F] = "Aaaa"            # fabricant
    exif[0x0112] = 6                 # orientation : déjà appliquée aux pixels
    source = encoded(Image.new("RGB", (4, 4)), "JPEG", exif=exif.tobytes())
    out = reread(encode_page(Image.new("RGB", (4, 4)), "JPEG", source)).getexif()
    assert out.get(0x010F) == "Aaaa"
    assert out.get(0x0112) in (None, 1)


# ---------------------------------------------------------------------------
# Réglages propres aux formats
# ---------------------------------------------------------------------------

def test_jpeg_keeps_the_subsampling_of_the_original_page():
    from PIL import JpegImagePlugin
    source = encoded(Image.new("RGB", (16, 16), (200, 10, 10)), "JPEG", subsampling=0)
    out = reread(encode_page(Image.new("RGB", (16, 16), (10, 200, 10)), "JPEG", source))
    assert JpegImagePlugin.get_sampling(out) == 0


def test_explicit_subsampling_and_quality_are_used():
    from PIL import JpegImagePlugin
    data = encode_page(Image.new("RGB", (16, 16)), "JPEG", quality=100, subsampling=0)
    assert JpegImagePlugin.get_sampling(reread(data)) == 0


def test_lossless_webp_stays_lossless():
    source = encoded(Image.new("RGB", (8, 8), (1, 2, 3)), "WEBP", lossless=True)
    data = encode_page(Image.new("RGB", (8, 8), (4, 5, 6)), "WEBP", source)
    assert b"VP8L" in data[:64]


def test_lossy_choice_overrides_a_lossless_original():
    source = encoded(Image.new("RGB", (8, 8)), "WEBP", lossless=True)
    data = encode_page(Image.new("RGB", (8, 8)), "WEBP", source, lossless=False)
    assert b"VP8L" not in data[:64]


def test_tiff_keeps_the_compression_of_the_original_page():
    source = encoded(Image.new("RGB", (4, 4)), "TIFF", compression="tiff_adobe_deflate")
    out = reread(encode_page(Image.new("RGB", (4, 4)), "TIFF", source))
    assert out.info["compression"] == "tiff_adobe_deflate"


def test_tiff_without_an_original_compression_is_written_in_lzw():
    """Jamais un TIFF non compressé par défaut : bien plus lourd que la page."""
    source = encoded(Image.new("RGB", (4, 4)), "PNG")
    out = reread(encode_page(Image.new("RGB", (4, 4)), "TIFF", source))
    assert out.info["compression"] == "tiff_lzw"


def test_icon_keeps_the_sizes_of_the_original():
    source = encoded(Image.new("RGBA", (64, 64)), "ICO", sizes=[(16, 16), (48, 48)])
    out = Image.open(io.BytesIO(encode_page(Image.new("RGBA", (64, 64)), "ICO", source)))
    assert sorted(out.info["sizes"]) == [(16, 16), (48, 48)]


# ---------------------------------------------------------------------------
# Images à plusieurs images (encode_frames) : mêmes règles qu'une image fixe
# ---------------------------------------------------------------------------

def animated(fmt, **kwargs):
    frames = [Image.new("RGBA", (8, 8), c) for c in ((255, 0, 0, 255), (0, 0, 255, 255))]
    return encoded(frames[0], fmt, save_all=True, append_images=frames[1:],
                   duration=[100, 200], loop=0, **kwargs)


def exif_with_maker():
    exif = Image.Exif()
    exif[0x010F] = "Aaaa"
    exif[0x0112] = 6
    return exif.tobytes()


@pytest.mark.parametrize("fmt", ["WEBP", "PNG"])
def test_rotated_animation_keeps_profile_and_metadata(fmt):
    """Une rotation d'image animée perdait le profil de couleur et l'EXIF."""
    from modules.qt.image_ops import transform_animated_image
    source = animated(fmt, icc_profile=SRGB, exif=exif_with_maker())
    data = transform_animated_image({"bytes": source, "extension": "." + fmt.lower()},
                                    lambda f: f.rotate(90))
    out = reread(data)
    assert getattr(out, "n_frames", 1) == 2
    assert out.info.get("icc_profile") == SRGB
    exif = out.getexif()
    assert exif.get(0x010F) == "Aaaa"
    assert exif.get(0x0112) in (None, 1)        # orientation non réécrite


def test_animation_keeps_durations_and_loop():
    from modules.qt.image_ops import transform_animated_image
    data = transform_animated_image({"bytes": animated("WEBP"), "extension": ".webp"},
                                    lambda f: f.transpose(Image.Transpose.FLIP_LEFT_RIGHT))
    out = Image.open(io.BytesIO(data))
    durations = []
    for i in range(out.n_frames):
        out.seek(i)
        out.load()
        durations.append(out.info["duration"])
    assert durations == [100, 200]
    assert out.info.get("loop") == 0


def test_lossless_animated_webp_stays_lossless():
    from modules.qt.entries import _webp_is_lossless
    from modules.qt.image_ops import transform_animated_image
    source = animated("WEBP", lossless=True)
    data = transform_animated_image({"bytes": source, "extension": ".webp"}, lambda f: f.rotate(90))
    assert _webp_is_lossless(data)


def test_multipage_tiff_transform_keeps_profile_resolution_and_compression():
    from modules.qt.image_ops import transform_multipage_tiff
    pages = [Image.new("RGB", (8, 8), c) for c in ((255, 0, 0), (0, 0, 255))]
    source = encoded(pages[0], "TIFF", save_all=True, append_images=pages[1:],
                     compression="tiff_adobe_deflate", dpi=(300, 300), icc_profile=SRGB)
    out = reread(transform_multipage_tiff({"bytes": source}, lambda f: f.rotate(90)))
    assert out.n_frames == 2
    assert out.info["compression"] == "tiff_adobe_deflate"
    assert [round(float(v)) for v in out.info["dpi"]] == [300, 300]
    assert out.info.get("icc_profile") == SRGB


def test_converted_animation_keeps_its_profile():
    from modules.qt.archive_names import IMAGE_EXTS
    from modules.qt.entries import create_entry
    from modules.qt.image_ops import convert_image_data
    entry = create_entry("a.webp", animated("WEBP", icc_profile=SRGB), IMAGE_EXTS)
    assert entry.get("is_animated_image")
    new_entry, error = convert_image_data(entry, "PNG", 95)
    assert error is None
    out = reread(new_entry["bytes"])
    assert out.info.get("icc_profile") == SRGB and out.n_frames == 2


def test_animated_gif_keeps_its_comment_and_loop():
    from modules.qt.image_ops import transform_animated_gif
    frames = [Image.new("P", (8, 8), i) for i in (1, 2)]
    source = encoded(frames[0], "GIF", save_all=True, append_images=frames[1:],
                     duration=[100, 100], loop=3, comment=b"Aaaa")
    out = reread(transform_animated_gif({"bytes": source}, lambda f: f.rotate(90)))
    assert out.info.get("loop") == 3
    assert out.info.get("comment") == b"Aaaa"


# ---------------------------------------------------------------------------
# Pages séparées d'un TIFF de plusieurs pages (entries._single_page_tiff_bytes)
# ---------------------------------------------------------------------------

def test_single_tiff_page_keeps_its_compression():
    from modules.qt.entries import _single_page_tiff_bytes
    data = _single_page_tiff_bytes(Image.new("RGB", (4, 4)), "tiff_adobe_deflate", (300, 300))
    out = reread(data)
    assert out.info["compression"] == "tiff_adobe_deflate"
    assert [round(float(v)) for v in out.info["dpi"]] == [300, 300]


@pytest.mark.parametrize("compression", [None, "group4"])
def test_single_tiff_page_without_a_usable_compression_is_written_in_lzw(compression):
    """Sans compression d'origine, ou avec une compression que la page ne
    peut pas recevoir (CCITT sur une page en couleur) : LZW, jamais un TIFF
    non compressé."""
    from modules.qt.entries import _single_page_tiff_bytes
    out = reread(_single_page_tiff_bytes(Image.new("RGB", (4, 4)), compression))
    assert out.info["compression"] == "tiff_lzw"


def test_single_tiff_page_keeps_its_profile():
    from modules.qt.entries import _single_page_tiff_bytes
    out = reread(_single_page_tiff_bytes(Image.new("RGB", (4, 4)), None, None, SRGB))
    assert out.info.get("icc_profile") == SRGB


# ---------------------------------------------------------------------------
# Enregistrement d'une page : save_image_to_bytes passe par encode_page
# ---------------------------------------------------------------------------

def test_saving_a_transparent_bmp_page_keeps_its_transparency():
    entry = {"img": half_transparent(), "extension": ".bmp",
             "bytes": encoded(Image.new("RGB", (2, 1)), "BMP")}
    out = reread(save_image_to_bytes(entry))
    assert out.mode == "RGBA" and out.getpixel((0, 0))[3] == 0


def test_saving_a_page_with_an_unknown_extension_gives_png():
    entry = {"img": Image.new("RGB", (4, 4)), "extension": ".xyz", "bytes": None}
    assert reread(save_image_to_bytes(entry)).format == "PNG"


def test_saving_a_resized_page_writes_the_chosen_resolution():
    source = encoded(Image.new("RGB", (4, 4)), "PNG", dpi=(300, 300))
    entry = {"img": reread(source), "extension": ".png", "bytes": source, "dpi": 150}
    out = reread(save_image_to_bytes(entry))
    assert [round(float(v)) for v in out.info["dpi"]] == [150, 150]
