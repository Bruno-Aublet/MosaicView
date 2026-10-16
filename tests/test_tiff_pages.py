"""Ouverture d'un TIFF de plusieurs pages ouvert seul (create_entries_from_tiff).

Chaque page devient une page de la mosaïque. Une page illisible n'est jamais
ignorée en silence : elle est rendue dans `failures`, que l'appelant signale
(fenêtre « Fichiers ignorés »), sinon le document créé ensuite aurait une page
de moins sans que personne le sache.
"""
import pytest
from PIL import Image

from modules.qt.entries import create_entries_from_tiff

tifffile = pytest.importorskip("tifffile")

EXTS = (".jpg", ".tif", ".tiff")
COLORS = ("red", "green", "blue")


def write_tiff(path):
    pages = [Image.new("RGB", (64, 64), c) for c in COLORS]
    pages[0].save(path, "TIFF", save_all=True, append_images=pages[1:],
                  compression="tiff_deflate")


def corrupt_page(path, index):
    """Écrase les données compressées d'une page : elle ne se décode plus."""
    with tifffile.TiffFile(path) as tif:
        page = tif.pages[index]
        offset, count = page.dataoffsets[0], page.databytecounts[0]
    data = bytearray(path.read_bytes())
    data[offset:offset + count] = b"\xff" * count
    path.write_bytes(bytes(data))


def test_every_page_becomes_an_entry(tmp_path):
    path = tmp_path / "comic.tif"
    write_tiff(path)
    failures = []
    entries = create_entries_from_tiff(str(path), EXTS, failures=failures)
    assert [e["orig_name"] for e in entries] == [
        "comic_page_0001.tif", "comic_page_0002.tif", "comic_page_0003.tif"]
    assert failures == []


def test_unreadable_page_is_reported(tmp_path):
    path = tmp_path / "comic.tif"
    write_tiff(path)
    corrupt_page(path, 1)
    failures = []
    entries = create_entries_from_tiff(str(path), EXTS, failures=failures)
    assert [e["orig_name"] for e in entries] == ["comic_page_0001.tif", "comic_page_0003.tif"]
    assert len(failures) == 1
    page, message = failures[0]
    assert page == 2
    assert message


def test_failures_argument_is_optional(tmp_path):
    path = tmp_path / "comic.tif"
    write_tiff(path)
    corrupt_page(path, 1)
    assert len(create_entries_from_tiff(str(path), EXTS)) == 2


def _decoded(entry):
    return Image.open(io.BytesIO(entry["bytes"]))


def test_single_page_tiff_keeps_its_bytes_and_name(tmp_path):
    path = tmp_path / "scan.tiff"
    Image.new("RGB", (16, 16), "red").save(path, "TIFF", compression="tiff_lzw")
    entries = create_entries_from_tiff(str(path), EXTS)
    assert [e["orig_name"] for e in entries] == ["scan.tiff"]
    assert entries[0]["bytes"] == path.read_bytes()


@pytest.mark.parametrize("mode, color, expected", [
    ("P", 1, (255, 0, 0)),
    ("CMYK", (255, 0, 0, 0), None),
    ("RGBA", (255, 0, 0, 0), None),
    ("I;16", 30000, None),
    ("1", 1, None),
])
def test_every_page_keeps_its_mode_and_pixels(tmp_path, mode, color, expected):
    """Jamais de JPEG : palette, CMJN, alpha, 16 bits et 1 bit restent tels
    quels, page par page, avec la compression d'origine."""
    pages = [Image.new(mode, (16, 16), color) for _i in range(2)]
    if mode == "P":
        for page in pages:
            page.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
    path = tmp_path / "comic.tif"
    pages[0].save(path, "TIFF", save_all=True, append_images=pages[1:],
                  compression="tiff_deflate", dpi=(300, 300))
    failures = []
    entries = create_entries_from_tiff(str(path), EXTS, failures=failures)
    assert failures == []
    assert [e["orig_name"] for e in entries] == ["comic_page_0001.tif", "comic_page_0002.tif"]
    for entry in entries:
        img = _decoded(entry)
        assert img.format == "TIFF"
        assert img.mode == mode
        assert img.info.get("compression") == "tiff_adobe_deflate"
        assert round(img.info["dpi"][0]) == 300
        if expected is not None:
            assert img.convert("RGB").getpixel((0, 0)) == expected
        elif mode == "1":
            # Pillow rend 1 pour une image créée, 255 pour une image relue
            assert img.convert("L").getpixel((0, 0)) == 255
        else:
            assert img.getpixel((0, 0)) == pages[0].getpixel((0, 0))


def test_prefix_is_added_to_page_names(tmp_path):
    path = tmp_path / "comic.tif"
    write_tiff(path)
    entries = create_entries_from_tiff(str(path), EXTS, add_prefix=True)
    assert entries[0]["orig_name"] == "NEW-comic_page_0001.tif"


# ---------------------------------------------------------------------------
# TIFF de plusieurs pages DANS une archive (expand_multipage_tiffs)
# Décision de conception : un TIFF multipage n'a pas sa place dans un comic ;
# chacune de ses pages devient une page du comic, en restant un TIFF.
# ---------------------------------------------------------------------------

import io

from modules.qt.entries import create_entry, expand_multipage_tiffs, split_multipage_tiff_pages


def multipage_tiff_bytes(mode="CMYK", compression="tiff_deflate", dpi=(300, 300)):
    colors = {"CMYK": [(255, 0, 0, 0), (0, 255, 0, 0), (0, 0, 255, 0)],
              "RGB": list(COLORS)}[mode]
    pages = [Image.new(mode, (32, 48), c) for c in colors]
    buf = io.BytesIO()
    pages[0].save(buf, "TIFF", save_all=True, append_images=pages[1:],
                  compression=compression, dpi=dpi)
    return buf.getvalue()


def test_split_keeps_format_mode_compression_and_dpi():
    pages = split_multipage_tiff_pages(multipage_tiff_bytes())
    assert len(pages) == 3
    for data in pages:
        img = Image.open(io.BytesIO(data))
        assert img.format == "TIFF"
        assert img.mode == "CMYK"
        assert getattr(img, "n_frames", 1) == 1
        assert img.info.get("compression") == "tiff_adobe_deflate"
        assert round(img.info["dpi"][0]) == 300


def test_every_tiff_page_becomes_a_page_in_place():
    first = create_entry("Ch1/01.jpg", b"not decoded", EXTS)
    tiff = create_entry("Ch1/02.tif", multipage_tiff_bytes(), EXTS)
    tiff["source_archive"] = "other.cbz"
    last = create_entry("Ch1/03.jpg", b"not decoded", EXTS)
    assert tiff["is_multipage_tiff"]
    result = expand_multipage_tiffs([first, tiff, last], EXTS)
    assert [e["orig_name"] for e in result] == [
        "Ch1/01.jpg", "Ch1/02_page_0001.tif", "Ch1/02_page_0002.tif",
        "Ch1/02_page_0003.tif", "Ch1/03.jpg"]
    assert result[0] is first and result[-1] is last
    for page in result[1:4]:
        assert not page["is_multipage_tiff"]
        assert page["source_archive"] == "other.cbz"


def test_single_page_tiff_is_left_alone():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "TIFF")
    entry = create_entry("p.tif", buf.getvalue(), EXTS)
    assert expand_multipage_tiffs([entry], EXTS) == [entry]


def test_unsplittable_tiff_is_kept_whole():
    """Un TIFF qui ne peut pas être découpé n'est jamais perdu : il reste en
    une seule entrée."""
    entry = {"orig_name": "p.tif", "bytes": b"broken", "is_multipage_tiff": True}
    assert expand_multipage_tiffs([entry], EXTS) == [entry]


# ---------------------------------------------------------------------------
# TIFF sans résolution : Pillow l'annonce à 1 dpi, valeur qui ne doit être ni
# affichée ni écrite (image_dpi)
# ---------------------------------------------------------------------------

from modules.qt.entries import image_dpi

X_RESOLUTION = 282


def test_tiff_without_resolution_has_no_dpi():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "TIFF")
    img = Image.open(io.BytesIO(buf.getvalue()))
    assert image_dpi(img) is None
    assert image_dpi(img.copy()) is None
    assert create_entry("p.tif", buf.getvalue(), EXTS)["dpi"] is None


def test_real_resolution_is_kept():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "TIFF", dpi=(300, 300))
    assert round(image_dpi(Image.open(io.BytesIO(buf.getvalue())))[0]) == 300


def test_split_tiff_without_resolution_writes_none():
    pages = split_multipage_tiff_pages(multipage_tiff_bytes(mode="RGB", dpi=None))
    for data in pages:
        assert X_RESOLUTION not in Image.open(io.BytesIO(data)).tag_v2
