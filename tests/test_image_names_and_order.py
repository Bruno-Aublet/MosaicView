"""Règles de nommage et d'ordre des pages communes aux lecteurs d'archives
(archive_names.py) : extension corrigée d'après le vrai format, ordre de
lecture d'un EPUB, rang de lecture dans le nom des pages d'un EPUB.

EPUB de synthèse construits à la volée avec zipfile.
"""
import io
import zipfile

from PIL import Image

from modules.qt.archive_names import (
    epub_page_name,
    epub_reading_order,
    name_for_real_format,
    open_zip,
    same_image_format,
    without_epub_rank,
    zip_member_name,
)
from modules.qt.entries import create_entry


def image_bytes(fmt):
    buf = io.BytesIO()
    Image.new("RGBA" if fmt == "PNG" else "RGB", (8, 8), "red").save(buf, fmt)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Extension d'après le vrai format
# ---------------------------------------------------------------------------

def test_wrong_extension_takes_the_real_format():
    assert name_for_real_format("Ch1/page.jpg", "PNG") == "Ch1/page.png"
    assert name_for_real_format("page.png", "WEBP") == "page.webp"
    assert name_for_real_format("page.tif", "JPEG") == "page.jpg"


def test_matching_or_unknown_format_keeps_the_name():
    assert name_for_real_format("page.jpeg", "JPEG") == "page.jpeg"
    assert name_for_real_format("page.JFIF", "JPEG") == "page.JFIF"
    assert name_for_real_format("page.tiff", "TIFF") == "page.tiff"
    assert name_for_real_format("photo.jpg", "MPO") == "photo.jpg"
    assert name_for_real_format("page.jpg", "HEIF") == "page.jpg"
    assert name_for_real_format("page.jpg", None) == "page.jpg"


def test_same_image_format():
    assert same_image_format(".jpg", ".JPEG")
    assert same_image_format(".tif", ".tiff")
    assert not same_image_format(".png", ".jpg")
    assert not same_image_format("", ".jpg")


def test_page_with_wrong_extension_is_renamed_when_read():
    entry = create_entry("Ch1/01.jpg", image_bytes("PNG"), (".jpg", ".png"))
    assert entry["orig_name"] == "Ch1/01.png"
    assert entry["extension"] == ".png"
    assert entry["extension_corrected_from"] == ".jpg"


def test_page_with_right_extension_is_not_flagged():
    entry = create_entry("01.png", image_bytes("PNG"), (".jpg", ".png"))
    assert entry["orig_name"] == "01.png"
    assert "extension_corrected_from" not in entry


# ---------------------------------------------------------------------------
# Ordre de lecture d'un EPUB
# ---------------------------------------------------------------------------

def make_epub():
    """EPUB dont l'ordre de lecture (spine : p2 puis p1) contredit l'ordre
    des noms, avec une couverture déclarée seulement dans l'OPF et une image
    affichée par aucun document."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("META-INF/container.xml",
                    '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/>'
                    '</rootfiles></container>')
        zf.writestr("OEBPS/content.opf",
                    '<package><manifest>'
                    '<item id="c" href="img/zcover.jpg" properties="cover-image"/>'
                    '<item id="p1" href="text/p1.xhtml"/><item id="p2" href="text/p2.xhtml"/>'
                    '</manifest><spine><itemref idref="p2"/><itemref idref="p1"/></spine></package>')
        zf.writestr("OEBPS/text/p1.xhtml", '<html><img src="../img/b%20x.jpg"/></html>')
        zf.writestr("OEBPS/text/p2.xhtml",
                    '<html><svg><image xlink:href="../img/a.jpg"/></svg></html>')
        for name in ("OEBPS/img/a.jpg", "OEBPS/img/b x.jpg",
                     "OEBPS/img/zcover.jpg", "OEBPS/img/extra.png"):
            zf.writestr(name, b"x")
    return zipfile.ZipFile(io.BytesIO(buf.getvalue()))


def epub_images(zf):
    return [n for n in zf.namelist() if n.endswith((".jpg", ".png"))]


def test_epub_pages_follow_the_reading_order():
    zf = make_epub()
    assert epub_reading_order(zf, epub_images(zf)) == [
        "OEBPS/img/zcover.jpg", "OEBPS/img/a.jpg", "OEBPS/img/b x.jpg", "OEBPS/img/extra.png"]


def test_incomplete_epub_falls_back_to_name_order():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in ("img/10.jpg", "img/2.jpg"):
            zf.writestr(name, b"x")
    zf = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    assert epub_reading_order(zf, epub_images(zf)) == ["img/2.jpg", "img/10.jpg"]


# ---------------------------------------------------------------------------
# Noms ZIP écrits sans le drapeau UTF-8
# ---------------------------------------------------------------------------

NAMES = ["Été_page_01.jpg", "Tome 3 - L'Île.jpg", "ÂGE D'OR.jpg", "café crème.jpg",
         "Noël.jpg", "plain.jpg"]


def zip_with_raw_names(raw_names):
    """ZIP dont les noms sont les octets donnés, sans drapeau UTF-8 (zipfile
    met ce drapeau à tout nom non ASCII) : écrit avec des noms ASCII de même
    longueur, remplacés ensuite dans les en-têtes."""
    buf = io.BytesIO()
    placeholders = []
    with zipfile.ZipFile(buf, "w") as zf:
        for i, raw in enumerate(raw_names):
            placeholder = (f"N{i:02d}" + "x" * len(raw))[:len(raw)].encode()
            placeholders.append(placeholder)
            zf.writestr(placeholder.decode(), f"data{i}".encode())
    data = buf.getvalue()
    for placeholder, raw in zip(placeholders, raw_names):
        data = data.replace(placeholder, raw)
    return io.BytesIO(data)


def test_unflagged_names_are_read_in_their_real_encoding():
    for encoding in ("utf-8", "cp850", "cp437", "cp1252"):
        names = []
        for name in NAMES:
            try:
                name.encode(encoding)
                names.append(name)
            except UnicodeEncodeError:
                pass
        with open_zip(zip_with_raw_names([n.encode(encoding) for n in names])) as zf:
            assert zf.namelist() == names, encoding
            # Lecture par nom et par ZipInfo : mêmes données
            assert [zf.read(n) for n in names] == [zf.read(i) for i in zf.infolist()]


def test_name_read_by_python_without_flag():
    # Python lit en cp437 un nom sans drapeau UTF-8
    assert zip_member_name("Été".encode("utf-8").decode("cp437")) == "Été"
    assert zip_member_name("L'Île".encode("cp850").decode("cp437")) == "L'Île"
    assert zip_member_name("café".encode("cp1252").decode("cp437")) == "café"
    assert zip_member_name("plain.jpg") == "plain.jpg"
    # Nom cp437 correct : inchangé
    assert zip_member_name("épisode") == "épisode"


def test_flagged_names_are_untouched():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("├ët├⌐.jpg", b"x")      # drapeau UTF-8 posé par zipfile
    with open_zip(io.BytesIO(buf.getvalue())) as zf:
        assert zf.namelist() == ["├ët├⌐.jpg"]


def test_epub_page_names_carry_their_rank():
    assert epub_page_name(3, "OEBPS/img/b x.jpg") == "0003_b x.jpg"
    assert without_epub_rank("0003_b x.jpg") == "b x.jpg"
    assert without_epub_rank("NEW-0012_c.png") == "c.png"
    assert without_epub_rank("page_01.jpg") == "page_01.jpg"
