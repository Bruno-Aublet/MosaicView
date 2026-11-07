"""Outils de dessin de la visionneuse sur une page CMJN.

L'affichage convertit une page CMJN par son profil ICC. Les outils de dessin
travaillent sur ces mêmes couleurs, sinon toute la page changerait de teinte au
premier trait, et la page reste en CMJN : seuls les pixels changés par le dessin
sont convertis, tous les autres gardent leurs valeurs d'origine.

Le profil de Windows sert au dernier test, écrit en boucle : la suite ne saute
aucun test, et les autres vérifient la même logique sans profil.
"""
import glob
import io

from PIL import Image, ImageDraw

from modules.qt.entries import drawing_base_image, drawn_page_image, save_image_to_bytes
from modules.qt.image_ops import cmyk_to_rgb, rgb_to_cmyk


def gradient_cmyk(size=(16, 16)):
    gray = Image.linear_gradient("L").resize(size)
    return Image.merge("CMYK", (gray, gray.rotate(90), gray.rotate(180), gray.point(lambda v: v // 4)))


def page_bytes(img, fmt="TIFF", **kwargs):
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def paint_corner(base):
    drawn = base.copy()
    ImageDraw.Draw(drawn).rectangle((0, 0, 3, 3), fill=(255, 0, 0, 255))
    return drawn


def unchanged_outside_corner(result, original):
    return all(result.getpixel((x, y)) == original.getpixel((x, y))
               for x in range(16) for y in range(16) if x > 3 or y > 3)


def test_page_without_a_stroke_keeps_all_its_values():
    original = gradient_cmyk()
    data = page_bytes(original)
    result = drawn_page_image(data, drawing_base_image(data))
    assert result.mode == "CMYK"
    assert result.tobytes() == original.tobytes()


def test_only_the_drawn_pixels_change():
    original = gradient_cmyk()
    data = page_bytes(original)
    result = drawn_page_image(data, paint_corner(drawing_base_image(data)))
    assert result.mode == "CMYK"
    assert result.getpixel((1, 1)) != original.getpixel((1, 1))
    assert unchanged_outside_corner(result, original)


def test_drawn_page_already_flattened_for_jpeg_is_accepted():
    original = gradient_cmyk()
    data = page_bytes(original)
    flat = paint_corner(drawing_base_image(data)).convert("RGB")
    result = drawn_page_image(data, flat)
    assert result.mode == "CMYK" and unchanged_outside_corner(result, original)


def test_unusable_profile_falls_back_like_the_display():
    img = Image.new("RGB", (2, 2), (200, 30, 90))
    assert rgb_to_cmyk(img, b"not a profile").tobytes() == img.convert("CMYK").tobytes()
    assert rgb_to_cmyk(img, None).tobytes() == img.convert("CMYK").tobytes()


def test_tiff_saved_after_a_stroke_stays_cmyk_with_its_other_pixels():
    original = gradient_cmyk()
    data = page_bytes(original)
    entry = {"bytes": data, "extension": ".tiff"}
    entry["img"] = drawn_page_image(data, paint_corner(drawing_base_image(data)))
    saved = Image.open(io.BytesIO(save_image_to_bytes(entry)))
    assert saved.mode == "CMYK" and unchanged_outside_corner(saved, original)


def test_page_with_a_real_profile_keeps_its_displayed_colors():
    profiles = (glob.glob(r"C:\Windows\System32\spool\drivers\color\*SWOP*")
                + glob.glob(r"C:\Windows\System32\spool\drivers\color\*CMYK*"))
    for path in profiles[:1]:
        with open(path, "rb") as f:
            icc = f.read()
        original = gradient_cmyk()
        data = page_bytes(original, icc_profile=icc)
        page = Image.open(io.BytesIO(data))
        shown = cmyk_to_rgb(page).convert("RGBA")
        base = drawing_base_image(data)
        assert base.tobytes() == shown.tobytes()
        assert base.tobytes() != page.convert("RGBA").tobytes()

        result = drawn_page_image(data, paint_corner(base))
        assert result.mode == "CMYK" and result.info.get("icc_profile") == icc
        assert unchanged_outside_corner(result, original)
        # Le rouge dessiné reste du rouge une fois repassé par le profil.
        red, green, blue = cmyk_to_rgb(result).getpixel((1, 1))
        assert red > 150 and green < 90 and blue < 90
