"""Vignette de la mosaïque tirée de l'image décodée pour la vérification
(entries.create_entry → entry["_checked_thumb"], repris par
take_checked_thumbnail) : la page n'est décodée qu'une fois au chargement, et
la vignette est la même que celle faite à partir des octets."""
import io

from PIL import Image, ImageChops

from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import (THUMB_SIZES, create_entry, page_thumbnail,
                                take_checked_thumbnail)


def encoded(img, fmt, **options):
    buffer = io.BytesIO()
    img.save(buffer, fmt, **options)
    return buffer.getvalue()


def thumbnail_from_bytes(data):
    return page_thumbnail(Image.open(io.BytesIO(data)))


def test_thumbnail_of_the_check_is_the_one_made_from_the_bytes():
    img = Image.new("RGB", (800, 1200), (200, 30, 30))
    img.paste((20, 20, 220), (100, 100, 700, 600))
    data = encoded(img, "PNG")
    entry = create_entry("001.png", data, IMAGE_EXTS)
    thumb = take_checked_thumbnail(entry)
    assert thumb is not None and thumb.size == THUMB_SIZES[2]
    assert ImageChops.difference(thumb, thumbnail_from_bytes(data)).getbbox() is None
    assert "_checked_thumb" not in entry                 # retirée de l'entrée


def test_exif_orientation_is_applied():
    img = Image.new("RGB", (1200, 600), (10, 200, 10))  # paysage, tourné à l'affichage
    exif = Image.Exif()
    exif[0x0112] = 6                                     # rotation de 90°
    data = encoded(img, "JPEG", exif=exif)
    thumb = take_checked_thumbnail(create_entry("001.jpg", data, IMAGE_EXTS))
    box = thumb.getbbox()                                # contenu non transparent
    assert box[3] - box[1] > box[2] - box[0]             # affichée en portrait
    assert ImageChops.difference(thumb, thumbnail_from_bytes(data)).getbbox() is None


def test_transparent_page_keeps_its_checkerboard():
    img = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    data = encoded(img, "PNG")
    thumb = take_checked_thumbnail(create_entry("001.png", data, IMAGE_EXTS))
    assert ImageChops.difference(thumb, thumbnail_from_bytes(data)).getbbox() is None


def test_animated_gif_thumbnail_is_its_first_frame():
    frames = [Image.new("P", (300, 300), i) for i in (1, 2, 3)]
    frames[0].putpalette([0, 0, 0, 250, 0, 0, 0, 250, 0, 0, 0, 250] + [0] * 756)
    buffer = io.BytesIO()
    frames[0].save(buffer, "GIF", save_all=True, append_images=frames[1:], duration=100)
    data = buffer.getvalue()
    entry = create_entry("001.gif", data, IMAGE_EXTS)
    assert entry["is_animated_gif"]
    thumb = take_checked_thumbnail(entry)
    assert ImageChops.difference(thumb, thumbnail_from_bytes(data)).getbbox() is None


def test_thumbnail_of_retouched_page_is_not_used():
    data = encoded(Image.new("RGB", (100, 100), "red"), "PNG")
    entry = create_entry("001.png", data, IMAGE_EXTS)
    entry["bytes"] = encoded(Image.new("RGB", (100, 100), "blue"), "PNG")   # page retouchée
    assert take_checked_thumbnail(entry) is None
    assert "_checked_thumb" not in entry


def test_no_thumbnail_for_an_unreadable_page():
    entry = create_entry("001.jpg", b"\xff\xd8\xff garbage", IMAGE_EXTS)
    assert entry["is_corrupted"]
    assert take_checked_thumbnail(entry) is None


def test_caller_image_is_not_reduced_without_in_place():
    img = Image.new("RGB", (800, 600), "white")
    page_thumbnail(img)
    assert img.size == (800, 600)
