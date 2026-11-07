"""Redressement d'une page : automatique (deskew.detect_skew_angle,
deskew_entry_data) et manuel (straighten_geometry.line_to_correction, angle
tiré du trait tracé dans la visionneuse).

Vérifié :
- une page inclinée d'un angle connu est détectée avec le bon angle et le bon
  signe, et ressort droite (son angle redétecté est nul) ;
- page déjà droite, page blanche, entrée qui n'est pas une image : rien ne
  change ;
- après redressement : même format, même mode, profil de couleur gardé ;
  GIF animé : toutes ses images gardées ;
- page à palette : coins blancs quelle que soit sa palette, traits lissés,
  transparence et métadonnées gardées ; page 1 bit : coins blancs, 1 bit ;
  page CMJN : vrais coins blancs ; page transparente : coins blancs opaques ;
- page grise 16 bits : inclinaison détectée ; vraie erreur séparée d'une page
  sans inclinaison, et image non écrite retirée de la page ;
- trait manuel : angle de correction, trait plutôt horizontal ou vertical,
  sens du tracé sans effet, même convention que la détection automatique.
"""
import io
import threading
import types

import numpy as np
import pytest
from PIL import Image, ImageCms, ImageDraw

from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.deskew import deskew_entry_data, detect_skew_angle
from modules.qt.entries import create_entry, rotate_page_image
from modules.qt.straighten_geometry import line_to_correction

import ast_loader


def palette_page(first_color, transparent_first=False):
    """Page à palette 200×300 en blanc (indice 1), trait rouge d'un pixel
    (indice 2) ; `first_color` est la première couleur de sa palette, inutilisée
    par le dessin, sauf le carré du centre si `transparent_first`."""
    img = Image.new("P", (200, 300), 1)
    img.putpalette(list(first_color) + [255, 255, 255, 200, 30, 30] + [0] * (253 * 3))
    for x in range(20, 180):
        img.putpixel((x, 150), 2)
    if transparent_first:
        img.paste(0, (80, 60, 120, 100))
        img.info["transparency"] = 0
    return img


@pytest.mark.parametrize("first_color", [(0, 0, 0), (200, 30, 30), (255, 255, 255)])
def test_palette_page_gets_white_corners_whatever_its_palette(first_color):
    out = rotate_page_image(palette_page(first_color), 10, fillcolor="white")
    assert out.mode == "P"
    assert out.convert("RGB").getpixel((1, 1)) == (255, 255, 255)


def test_palette_page_thin_line_is_smoothed_when_rotated():
    out = rotate_page_image(palette_page((0, 0, 0)), 10, fillcolor="white").convert("RGB")
    colors = {color for _count, color in out.getcolors(256)}
    # Couleurs mêlées au blanc le long du trait, en plus du blanc et du rouge
    assert len(colors) > 2


@pytest.mark.parametrize("name, fmt", [("p01.png", "PNG"), ("p01.gif", "GIF")])
def test_rotated_palette_page_keeps_mode_and_transparency_once_saved(name, fmt):
    img = palette_page((0, 0, 0), transparent_first=True)
    out = rotate_page_image(img, 10, fillcolor="white")
    buf = io.BytesIO()
    out.save(buf, fmt)
    back = Image.open(io.BytesIO(buf.getvalue()))
    assert back.mode == "P"
    rgba = back.convert("RGBA")
    assert rgba.getpixel((1, 1)) == (255, 255, 255, 255)        # coin découvert : blanc opaque
    center = (out.width // 2, out.height // 2 - 70)
    assert rgba.getpixel(center)[3] == 0                        # carré resté transparent


def test_rotated_palette_page_keeps_its_metadata():
    img = palette_page((0, 0, 0))
    img.info["dpi"] = (300, 300)
    assert rotate_page_image(img, 10, fillcolor="white").info["dpi"] == (300, 300)


def test_cmyk_page_gets_white_corners():
    out = rotate_page_image(Image.new("CMYK", (40, 40), (10, 20, 30, 40)), 10, fillcolor="white")
    assert out.mode == "CMYK" and out.getpixel((0, 0)) == (0, 0, 0, 0)


@pytest.mark.parametrize("mode, color", [("RGBA", (10, 20, 30, 0)), ("LA", (50, 0))])
def test_transparent_page_gets_white_opaque_corners(mode, color):
    """Transparents, les coins ne se distingueraient plus de la page d'origine."""
    img = Image.new(mode, (40, 40), color)
    out = rotate_page_image(img, 10, fillcolor="white")
    assert out.mode == mode and out.convert("RGBA").getpixel((0, 0)) == (255, 255, 255, 255)


def test_opaque_page_with_an_alpha_channel_gets_white_corners():
    out = rotate_page_image(Image.new("RGBA", (40, 40), (10, 20, 30, 255)), 10, fillcolor="white")
    assert out.getpixel((0, 0)) == (255, 255, 255, 255)


def test_one_bit_page_stays_one_bit_with_white_corners():
    img = Image.new("1", (200, 300), 1)
    out = rotate_page_image(img, 10, fillcolor="white")
    assert out.mode == "1" and out.getpixel((1, 1)) == 255


def lined_page(tilt=0.0, mode="RGB"):
    """Page blanche couverte de traits horizontaux épais, tournée de `tilt`
    degrés (sens de PIL.Image.rotate)."""
    img = Image.new("RGB", (700, 500), "white")
    draw = ImageDraw.Draw(img)
    for y in range(60, 460, 40):
        draw.rectangle([60, y, 640, y + 4], fill="black")
    if tilt:
        img = img.rotate(tilt, resample=Image.Resampling.BICUBIC, fillcolor="white")
    return img.convert(mode)


def entry_of(img, name="p01.png", fmt="PNG", **save):
    buf = io.BytesIO()
    img.save(buf, fmt, **save)
    return create_entry(name, buf.getvalue(), IMAGE_EXTS)


def decoded(entry):
    return Image.open(io.BytesIO(entry["bytes"]))


# ---------------------------------------------------------------------------
# Redressement automatique
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tilt", [3.0, -2.0])
def test_skew_is_detected_with_its_sign(tilt):
    angle = detect_skew_angle(entry_of(lined_page(tilt)))
    assert angle is not None
    assert angle == pytest.approx(-tilt, abs=0.5)        # angle à appliquer pour redresser


def test_skew_of_a_16bit_gray_page_is_detected():
    gray = np.asarray(lined_page(3.0).convert("L"), dtype=np.uint16) * 257
    angle = detect_skew_angle(entry_of(Image.fromarray(gray)))
    assert angle == pytest.approx(-3.0, abs=0.5)


def test_failed_write_leaves_no_unsaved_image_in_the_page(monkeypatch):
    entry = entry_of(lined_page(3.0))
    before = entry["bytes"]

    def fail(page):
        raise OSError("disque plein")

    monkeypatch.setattr("modules.qt.deskew.save_image_to_bytes", fail)
    with pytest.raises(OSError):
        deskew_entry_data(entry)
    assert entry["img"] is None and entry["bytes"] is before


WorkerBase = ast_loader.load_methods(
    "deskew_qt.py", "_DeskewWorker", {"_run"},
    deskew_entry_data=lambda entry, state: entry["result"]() if callable(entry["result"]) else entry["result"],
    log_if_unexpected=lambda error: logged.append(error),
    exception_text=str,
)
logged = []


class Worker(WorkerBase):
    def __init__(self, entries):
        self._entries = entries
        self._cancelled = threading.Event()
        self.failed_names, self.errors, self.straightened = [], [], []
        self.success_count = 0
        self.done = self.progress = self.cancelled = types.SimpleNamespace(emit=lambda *a: None)


def test_worker_separates_a_real_error_from_a_page_without_skew():
    def fail():
        raise OSError("disque plein")

    ok = {"orig_name": "a.png", "result": True}
    straight = {"orig_name": "b.png", "result": False}
    broken = {"orig_name": "c.png", "result": fail}
    logged.clear()
    worker = Worker([ok, straight, broken])
    worker._run()
    assert worker.success_count == 1 and worker.straightened == [ok]
    assert worker.failed_names == ["b.png"]
    assert worker.errors == ["c.png: disque plein"]
    assert len(logged) == 1


def test_skewed_page_comes_out_straight():
    entry = entry_of(lined_page(3.0))
    assert deskew_entry_data(entry) is True
    again = detect_skew_angle(create_entry("p01.png", entry["bytes"], IMAGE_EXTS))
    assert again is None or abs(again) < 0.5


def test_straight_page_is_left_unchanged():
    entry = entry_of(lined_page(0.0))
    before = entry["bytes"]
    assert deskew_entry_data(entry) is False
    assert entry["bytes"] is before


def test_blank_page_and_non_image_are_left_unchanged():
    blank = entry_of(Image.new("RGB", (400, 300), "white"))
    before = blank["bytes"]
    assert detect_skew_angle(blank) is None
    assert deskew_entry_data(blank) is False and blank["bytes"] is before
    text = create_entry("info.nfo", b"texte", IMAGE_EXTS)
    assert detect_skew_angle(text) is None


def test_straightened_page_keeps_its_format_mode_and_profile():
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    entry = entry_of(lined_page(3.0), icc_profile=profile)
    assert deskew_entry_data(entry) is True
    out = decoded(entry)
    assert out.format == "PNG" and out.mode == "RGB"
    assert out.info.get("icc_profile") == profile


def test_straightened_jpeg_stays_a_jpeg():
    entry = entry_of(lined_page(3.0), name="p01.jpg", fmt="JPEG", quality=95)
    assert deskew_entry_data(entry) is True
    assert decoded(entry).format == "JPEG"


def test_straightened_animated_gif_keeps_every_frame():
    frames = [lined_page(3.0, mode="P"), lined_page(3.0).convert("P")]
    frames[1].putpixel((5, 5), 0)                         # images différentes : toutes gardées
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    entry = create_entry("p01.gif", buf.getvalue(), IMAGE_EXTS)
    assert deskew_entry_data(entry) is True
    out = decoded(entry)
    assert out.format == "GIF" and out.n_frames == 2


# ---------------------------------------------------------------------------
# Redressement manuel : angle tiré du trait tracé
# ---------------------------------------------------------------------------

def test_empty_line_gives_no_correction():
    assert line_to_correction(10, 10, 10, 10) == (None, None, None)


def test_horizontal_line_needs_no_correction():
    assert line_to_correction(0, 50, 200, 50) == (0.0, "h", None)


def test_slightly_rising_line_is_horizontal_and_its_direction_does_not_matter():
    forward = line_to_correction(0, 0, 100, -5)
    backward = line_to_correction(100, -5, 0, 0)
    assert forward[1] == backward[1] == "h"
    assert forward[0] == pytest.approx(backward[0])
    assert forward[0] < 0


@pytest.mark.parametrize("x2, sign", [(5, 1), (-5, -1)])
def test_nearly_vertical_line_is_measured_against_the_vertical(x2, sign):
    correction, category, vertical_sign = line_to_correction(0, 0, x2, 100)
    assert category == "v" and vertical_sign == sign
    assert abs(correction) == pytest.approx(2.862, abs=0.01)
    assert (correction < 0) == (sign > 0)


def test_manual_and_automatic_corrections_agree():
    """Même convention : le trait tracé sur une page inclinée donne l'angle
    que la détection automatique trouve."""
    tilt = 3.0
    automatic = detect_skew_angle(entry_of(lined_page(tilt)))
    # Trait le long d'une ligne de la page tournée de `tilt` (sens de PIL :
    # anti-horaire à l'écran, donc montant vers la droite, y décroissant)
    import math
    dx, dy = 100, -100 * math.tan(math.radians(tilt))
    manual, category, _sign = line_to_correction(0, 0, dx, dy)
    assert category == "h"
    assert manual == pytest.approx(automatic, abs=0.5)
