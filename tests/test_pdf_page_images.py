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

from modules.qt.pdf_page_images import best_page_image, raw_page_image

PAGE_W, PAGE_H = 595, 842


def jpeg_bytes(size=(2480, 3508)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 180, 160)).save(buf, "JPEG", quality=80)
    return buf.getvalue()


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
    _dpi, xref = best_page_image(page, image_list)
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
    dpi, xref = best_page_image(page, page.get_images(full=True))
    assert xref is not None
    assert round(dpi) == 300


def test_page_without_image():
    doc, page = make_page(lambda p: p.insert_text((100, 100), "texte"))
    assert best_page_image(page, page.get_images(full=True)) == (0, None)
    assert raw_page_image(doc, page, [], None) is None


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
