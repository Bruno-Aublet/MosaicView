"""Pages aux cas limites lues par entries.create_entry (ouverture, ajout,
collage, lots) : jamais d'exception, et une page marquée illisible si, et
seulement si, elle ne se décode pas.

Vérifié :
- fichier vide, PNG et JPEG tronqués, octets quelconques nommés en image :
  page illisible (cadre rouge), avec sa raison, toujours reconnue comme image ;
- page de 1×1 pixel : lisible, dimensions exactes ;
- octets abîmés au hasard (graine fixe) dans un JPEG et un PNG : jamais
  d'exception, et toute page déclarée lisible a des dimensions valides et se
  décode réellement ;
- nom de page très long (300 caractères) : gardé tel quel et écrit dans un
  CBZ (le format ZIP l'accepte), relu identique.
"""
import io
import random
import zipfile

import pytest
from PIL import Image

from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import create_entry, open_page_image
from modules.qt.utils import cbz_members


def encoded(fmt, size=(64, 48)):
    img = Image.new("RGB", size, (200, 30, 30))
    for x in range(0, size[0], 4):                     # un peu de détail à décoder
        img.putpixel((x, x % size[1]), (10, 10, 240))
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


JPEG, PNG = encoded("JPEG"), encoded("PNG")


@pytest.mark.parametrize("name, data", [
    ("vide.jpg", b""),
    ("tronque.png", PNG[: len(PNG) // 2]),
    ("tronque.jpg", JPEG[: len(JPEG) // 2]),
    ("bruit.jpg", bytes(range(256)) * 4),
    ("texte.png", b"ceci n'est pas une image"),
], ids=["vide", "png-tronque", "jpeg-tronque", "bruit", "texte"])
def test_undecodable_page_is_marked_with_its_reason(name, data):
    entry = create_entry(name, data, IMAGE_EXTS)
    assert entry["is_image"] is True
    assert entry["is_corrupted"] is True
    assert entry["corruption_reason"]
    assert entry["bytes"] is data                       # octets d'origine gardés, jamais perdus


def test_one_pixel_page_is_readable():
    entry = create_entry("p.png", encoded("PNG", (1, 1)), IMAGE_EXTS)
    assert entry["is_corrupted"] is False
    assert (entry["img_width"], entry["img_height"]) == (1, 1)


@pytest.mark.parametrize("name, data", [("p.jpg", JPEG), ("p.png", PNG)], ids=["jpeg", "png"])
def test_randomly_damaged_bytes_never_raise_and_are_judged_consistently(name, data):
    rng = random.Random(1234)
    for _round in range(60):
        damaged = bytearray(data)
        for _hit in range(rng.randint(1, 8)):
            damaged[rng.randrange(len(damaged))] = rng.randrange(256)
        if rng.random() < 0.3:
            del damaged[rng.randrange(len(damaged)):]
        damaged = bytes(damaged)
        entry = create_entry(name, damaged, IMAGE_EXTS)
        if not entry["is_corrupted"]:
            assert entry["img_width"] > 0 and entry["img_height"] > 0
            img = open_page_image(damaged)              # lisible : se décode vraiment
            assert img.size[0] > 0
            img.close()


def test_very_long_page_name_is_kept_and_written_in_a_cbz(tmp_path):
    name = "a" * 296 + ".png"
    entry = create_entry(name, PNG, IMAGE_EXTS)
    assert entry["orig_name"] == name and entry["is_corrupted"] is False
    out = tmp_path / "Aaaa.cbz"
    with zipfile.ZipFile(out, "w") as zf:
        for member, page in cbz_members([entry]):
            zf.writestr(member, page["bytes"])
    with zipfile.ZipFile(out) as zf:
        [info] = zf.infolist()
        assert info.filename == name and zf.read(info) == PNG
