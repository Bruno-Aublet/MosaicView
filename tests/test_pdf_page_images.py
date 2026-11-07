"""Mode « original » de l'ouverture d'un PDF (pdf_page_images) : l'image
intégrée n'est reprise telle quelle que si elle est tout le contenu de la
page. Sinon la page est rendue, sans quoi ce qu'elle dessine en plus de
l'image (lettrage, tracés, annotations, rotation, transparence) serait perdu,
puis le PDF supprimé par le lot PDF → CBZ.

PDF de synthèse construits à la volée avec PyMuPDF.
"""
import io

import pytest
from PIL import Image

fitz = pytest.importorskip("pymupdf")

from modules.qt import pdf_page_images
from modules.qt.pdf_page_images import page_resolution, raw_page_image

PAGE_W, PAGE_H = 595, 842


def jpeg_bytes(size=(2480, 3508)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 180, 160)).save(buf, "JPEG", quality=80)
    return buf.getvalue()


def resolution(doc, page):
    return page_resolution(doc, page, page.get_images(full=True))


def png_rgba_bytes():
    buf = io.BytesIO()
    Image.new("RGBA", (1240, 1754), (255, 0, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


def make_page(build):
    """PDF d'une page de 595×842 pt construite par build(page), relu."""
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    build(page)
    doc = fitz.open("pdf", doc.tobytes())
    return doc, doc[0]


def raw_of(doc, page):
    image_list = page.get_images(full=True)
    _dpi, _real, xref = page_resolution(doc, page, image_list)
    return raw_page_image(doc, page, image_list, xref)


def full_page_image(page, stream=None, **kwargs):
    page.insert_image(page.rect, stream=stream or jpeg_bytes(), **kwargs)


def test_page_made_of_one_image_is_extracted_as_is():
    data = jpeg_bytes()
    doc, page = make_page(lambda p: full_page_image(p, data))
    assert raw_of(doc, page) == data


def test_visible_text_over_the_image_forces_rendering():
    def build(p):
        full_page_image(p)
        p.insert_text((100, 100), "BULLE", fontsize=30)
    assert raw_of(*make_page(build)) is None


def test_vector_drawing_forces_rendering():
    def build(p):
        full_page_image(p)
        p.draw_rect(fitz.Rect(10, 10, 100, 100))
    assert raw_of(*make_page(build)) is None


def test_annotation_forces_rendering():
    def build(p):
        full_page_image(p)
        p.add_text_annot((50, 50), "note")
    assert raw_of(*make_page(build)) is None


def test_rotated_page_forces_rendering():
    def build(p):
        full_page_image(p)
        p.set_rotation(90)
    assert raw_of(*make_page(build)) is None


def test_upside_down_image_forces_rendering():
    assert raw_of(*make_page(lambda p: full_page_image(p, rotate=180))) is None


def test_image_with_margins_forces_rendering():
    assert raw_of(*make_page(
        lambda p: p.insert_image(fitz.Rect(50, 50, 545, 792), stream=jpeg_bytes()))) is None


def test_image_with_transparency_mask_forces_rendering():
    assert raw_of(*make_page(lambda p: full_page_image(p, png_rgba_bytes()))) is None


def test_resolution_ignores_page_rotation():
    def build(p):
        full_page_image(p)
        p.set_rotation(90)
    doc, page = make_page(build)
    dpi, real, xref = resolution(doc, page)
    assert xref is not None
    assert round(dpi) == round(real) == 300


def test_page_without_image():
    doc, page = make_page(lambda p: p.insert_text((100, 100), "texte"))
    assert page.get_images(full=True) == []
    assert raw_page_image(doc, page, [], None) is None


def half_red_jpeg(**save_options):
    """JPEG de 800×400, moitié gauche rouge, avec les options de `save`."""
    picture = Image.new("RGB", (800, 400), (255, 255, 255))
    picture.paste((220, 0, 0), (0, 0, 400, 400))
    buf = io.BytesIO()
    picture.save(buf, "JPEG", quality=90, **save_options)
    return buf.getvalue()


def with_exif_block(jpeg, order):
    """JPEG auquel est ajouté, après son début, un EXIF (ordre d'octets `order`)
    d'orientation 6 et de marque « Aaaa »."""
    name = {"little": b"II", "big": b"MM"}[order]
    number = lambda value, size: value.to_bytes(size, order)
    magic = number(42, 2)
    tiff = (name + magic + number(8, 4) + number(2, 2)
            + number(0x010F, 2) + number(2, 2) + number(4, 4) + b"Aaa\x00"
            + number(0x0112, 2) + number(3, 2) + number(1, 4) + number(6, 2) + b"\x00\x00"
            + number(0, 4))
    block = b"Exif\x00\x00" + tiff
    return jpeg[:2] + b"\xff\xe1" + (len(block) + 2).to_bytes(2, "big") + block + jpeg[2:]


@pytest.mark.parametrize("order", ["little", "big"])
def test_exif_orientation_ignored_by_the_pdf_is_neutralized_without_reencoding(order):
    """La page affiche l'image sans la tourner : extraite telle quelle, elle
    serait tournée d'un quart de tour dans MosaicView, qui applique l'EXIF. Le
    JPEG garde ses pixels et ses autres métadonnées, seule la balise change."""
    data = with_exif_block(half_red_jpeg(), order)
    assert Image.open(io.BytesIO(data)).getexif()[0x0112] == 6
    doc = fitz.open()
    page = doc.new_page(width=800, height=400)
    page.insert_image(page.rect, stream=data)
    doc = fitz.open("pdf", doc.tobytes())
    raw = raw_of(doc, doc[0])
    assert raw is not None and len(raw) == len(data)
    assert sum(a != b for a, b in zip(raw, data)) == 1          # un seul octet change
    img = Image.open(io.BytesIO(raw))
    assert img.size == (800, 400)
    assert img.getexif()[0x0112] == 1 and img.getexif()[0x010F] == "Aaa"


def test_orientation_stored_only_in_xmp_forces_rendering():
    """Rien à corriger dans un EXIF : la page est rendue, comme le PDF l'affiche."""
    xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           b'<rdf:Description xmlns:tiff="http://ns.adobe.com/tiff/1.0/" tiff:Orientation="6"/>'
           b'</rdf:RDF></x:xmpmeta>')
    data = half_red_jpeg(xmp=xmp)
    assert Image.open(io.BytesIO(data)).getexif()[0x0112] == 6
    doc = fitz.open()
    page = doc.new_page(width=800, height=400)
    page.insert_image(page.rect, stream=data)
    doc = fitz.open("pdf", doc.tobytes())
    assert raw_of(doc, doc[0]) is None


def small_image(size=16):
    buf = io.BytesIO()
    Image.new("RGB", (size, size), (255, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


def test_small_image_on_a_vector_page_does_not_lower_its_resolution():
    """Dessin vectoriel avec un logo de 16×16 : la page, sans texte, ressortait
    en 16×23 pixels, sa résolution étant calculée d'après le seul logo."""
    def build(p):
        p.draw_rect(fitz.Rect(40, 40, 555, 802))
        p.insert_image(fitz.Rect(50, 50, 80, 80), stream=small_image())
    dpi, _real, _xref = resolution(*make_page(build))
    assert dpi == 300


def test_annotation_counts_as_page_content():
    def build(p):
        p.insert_image(fitz.Rect(50, 50, 80, 80), stream=small_image())
        p.add_text_annot((200, 200), "note")
    assert resolution(*make_page(build))[0] == 300


def test_fine_image_drawn_small_keeps_its_own_resolution():
    """800 pixels dessinés sur 100 points : 576 dpi, et non 800 pixels comparés
    à toute la page."""
    def build(p):
        p.insert_image(fitz.Rect(200, 300, 300, 400), stream=small_image(800))
    dpi, real, _xref = resolution(*make_page(build))
    assert round(real) == 576 and round(dpi) == 576


def test_image_sharper_than_300_dpi_on_a_page_with_text_keeps_its_resolution():
    def build(p):
        p.insert_text((20, 40), "Aaaa")
        p.insert_image(fitz.Rect(200, 300, 300, 400), stream=small_image(800))
    assert round(resolution(*make_page(build))[0]) == 576


def test_low_resolution_image_filling_the_page_is_not_enlarged():
    data = jpeg_bytes((595, 842))
    dpi, _real, _xref = resolution(*make_page(lambda p: full_page_image(p, data)))
    assert round(dpi) == 72
    data = jpeg_bytes((298, 421))
    dpi, _real, _xref = resolution(*make_page(lambda p: full_page_image(p, data)))
    assert round(dpi) == 36


def test_strips_filling_the_page_are_not_enlarged():
    """Page découpée en bandes par un scanner : les images couvrent la page."""
    def build(p):
        for index in range(4):
            buf = io.BytesIO()
            Image.new("RGB", (1190, 421), (index * 60, 0, 0)).save(buf, "JPEG")
            p.insert_image(fitz.Rect(0, index * 210.5, PAGE_W, (index + 1) * 210.5),
                           stream=buf.getvalue(), keep_proportion=False)
    dpi, real, xref = resolution(*make_page(build))
    assert round(dpi) == round(real) == 144
    assert xref is None


def test_tiny_image_alone_on_a_page_keeps_a_readable_page():
    dpi, _real, _xref = resolution(*make_page(
        lambda p: p.insert_image(fitz.Rect(50, 50, 80, 80), stream=small_image())))
    assert dpi == 72


def test_listed_image_that_is_not_drawn_does_not_count():
    doc, page = make_page(lambda p: p.insert_image(fitz.Rect(10, 10, 20, 20), stream=small_image()))
    page.clean_contents()
    doc.update_stream(page.get_contents()[0], b"0 0 m 10 10 l S")
    page = doc[0]
    assert len(page.get_images(full=True)) == 1
    assert resolution(doc, page) == (300, 0, None)


def test_rendered_size_is_limited_above_300_dpi():
    """Image très fine dessinée en tout petit : plafond de 2400 dpi insuffisant
    (une page A4 ferait 557 millions de pixels)."""
    doc, page = make_page(lambda p: p.insert_image(fitz.Rect(10, 10, 11, 11), stream=small_image(1000)))
    dpi = resolution(doc, page)[0]
    pixels = (PAGE_W / 72 * dpi) * (PAGE_H / 72 * dpi)
    assert 300 < dpi < 2400
    assert pixels == pytest.approx(100_000_000, rel=0.01)


def test_large_image_filling_the_page_is_not_reduced_by_the_size_limit(monkeypatch):
    """Limite abaissée pour le test, plutôt qu'une image de 100 millions de
    pixels : sans l'exception, cette page reviendrait à 300 dpi."""
    monkeypatch.setattr(pdf_page_images, "_MAX_RENDER_PIXELS", 1_000_000)
    doc, page = make_page(lambda p: full_page_image(p, jpeg_bytes((3000, 4244))))
    dpi, real, _xref = resolution(doc, page)
    assert round(real) == 363
    assert dpi == pytest.approx(real, rel=0.01)


@pytest.mark.parametrize("fmt", ["JPEG", "PNG"])
def test_extracted_image_gets_the_color_profile_stored_apart(fmt):
    """Profil ICC rangé à part dans le PDF (/ICCBased) : ajouté aux bytes
    extraits, sans changer les pixels. Profil d'essai : le profil RGB de
    LittleCMS renommé, un profil sRGB n'étant volontairement pas ajouté."""
    from PIL import ImageCms
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    icc = srgb.replace("sRGB".encode("utf-16-be"), "Test".encode("utf-16-be"))
    buf = io.BytesIO()
    Image.new("RGB", (PAGE_W, PAGE_H), (200, 120, 40)).save(buf, fmt)
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_image(page.rect, stream=buf.getvalue())
    xref = page.get_images(full=True)[0][0]
    profile = doc.get_new_xref()
    doc.update_object(profile, "<</N 3>>")
    doc.update_stream(profile, icc)
    doc.xref_set_key(xref, "ColorSpace", f"[/ICCBased {profile} 0 R]")
    doc = fitz.open("pdf", doc.tobytes())
    page = doc[0]
    images = page.get_images(full=True)
    data = raw_page_image(doc, page, images, images[0][0])
    img = Image.open(io.BytesIO(data))
    assert img.format == fmt
    assert img.info.get("icc_profile") == icc
    assert img.convert("RGB").getpixel((10, 10)) == Image.open(buf).convert("RGB").getpixel((10, 10))
