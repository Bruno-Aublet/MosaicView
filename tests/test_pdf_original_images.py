"""Mode « images brutes » de l'ouverture d'un PDF (pdf_page_images.original_images) :
seules les images du PDF sont reprises, telles qu'enregistrées, et, sur
demande, une image coupée entre deux pages est recollée quand la découpe est
certaine — jamais deux pages entières, ni deux morceaux dont les bords ne se
raccordent pas, ni une page suivie d'une image plus courte sans rapport.

PDF de synthèse construits à la volée avec PyMuPDF : pages de 612×792 pt dont
les images occupent la zone 72 → 720 pt, à 1 pixel par point.
"""
import io

import pytest
from PIL import Image

fitz = pytest.importorskip("pymupdf")

from modules.qt.pdf_page_images import continues, original_images

PAGE_W, PAGE_H = 612, 792
TOP, BOTTOM = 72, 720
LEFT, WIDTH = 72, 300


def png_bytes(image):
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def jpeg_bytes(size=(WIDTH, 648)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 180, 160)).save(buf, "JPEG", quality=80)
    return buf.getvalue()


def gradient(height):
    """Image de WIDTH × height en dégradé vertical : deux morceaux découpés
    dedans se raccordent."""
    red = Image.linear_gradient("L").resize((WIDTH, height))
    return Image.merge("RGB", (red, Image.new("L", red.size, 90), Image.new("L", red.size, 160)))


def solid(height, color):
    return Image.new("RGB", (WIDTH, height), color)


def rect(top, height):
    return fitz.Rect(LEFT, top, LEFT + WIDTH, top + height)


def make_pdf(pages):
    """PDF relu dont chaque page est construite par une fonction de `pages`."""
    doc = fitz.open()
    for build in pages:
        build(doc.new_page(width=PAGE_W, height=PAGE_H))
    return fitz.open("pdf", doc.tobytes())


def draw(image, top):
    """Page qui dessine `image` (1 pixel par point) à partir de `top`."""
    return lambda page: page.insert_image(rect(top, image.height), stream=png_bytes(image))


def extract(doc, join=True):
    def xref_png(xref):
        pix = fitz.Pixmap(doc, xref)
        if pix.n - pix.alpha >= 4:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        return pix.tobytes("png")
    return list(original_images(doc, xref_png, join_cut_images=join))


def opened(result):
    return Image.open(io.BytesIO(result["data"]))


def squashed(image):
    """Page qui dessine `image` entière, écrasée pour tenir dans la zone :
    image restée entière, qui donne sa taille de référence au PDF."""
    return lambda page: page.insert_image(rect(TOP, 648), stream=png_bytes(image),
                                          keep_proportion=False)


def split_pages(picture):
    """Deux pages : le haut de `picture` jusqu'en bas de la zone, le reste en
    haut de la page suivante."""
    return [draw(picture.crop((0, 0, WIDTH, 648)), TOP),
            draw(picture.crop((0, 648, WIDTH, picture.height)), TOP)]


def test_image_split_across_two_pages_is_joined_back_exactly():
    """Le PDF contient aussi une image entière de même taille (référence)."""
    picture = gradient(700)
    results = extract(make_pdf([squashed(gradient(700))] + split_pages(picture)))
    assert [r["pieces"] for r in results] == [1, 2]
    assert results[1]["page"] == 1
    joined = opened(results[1])
    assert joined.format == "PNG"
    assert joined.size == picture.size
    assert joined.convert("RGB").tobytes() == picture.tobytes()


def test_pieces_stay_apart_when_joining_is_not_asked():
    results = extract(make_pdf([squashed(gradient(700))] + split_pages(gradient(700))), join=False)
    assert [r["pieces"] for r in results] == [1, 1, 1]


def test_page_followed_by_a_shorter_image_is_not_joined():
    """Page entière puis, en haut de la page suivante, une image plus courte
    sans rapport, de même largeur et au bord semblable (blanc) : la géométrie
    seule les prendrait pour les morceaux d'une même image. Leur taille
    totale ne correspond à aucune image entière du PDF."""
    white = (255, 255, 255)
    results = extract(make_pdf([squashed(gradient(700)),
                                draw(solid(648, white), TOP),
                                draw(solid(30, white), TOP)]))
    assert [r["pieces"] for r in results] == [1, 1, 1]


def test_single_cut_without_reference_stays_apart():
    """Aucune image entière de même taille, une seule découpe : rien ne
    permet d'en être certain."""
    results = extract(make_pdf(split_pages(gradient(700))))
    assert [r["pieces"] for r in results] == [1, 1]


def test_cuts_of_the_same_size_are_their_own_reference():
    """Toutes les images du PDF sont coupées : trois découpes qui donnent la
    même taille suffisent à la confirmer."""
    pages = split_pages(gradient(700)) * 3
    results = extract(make_pdf(pages))
    assert [r["pieces"] for r in results] == [2, 2, 2]


def test_full_pages_are_never_joined():
    """Images qui remplissent chacune toute la zone, bords identiques : ce
    sont des pages entières, pas les morceaux d'une même image."""
    page = solid(648, (255, 255, 255))
    results = extract(make_pdf([draw(page, TOP)] * 3))
    assert [r["pieces"] for r in results] == [1, 1, 1]


def test_pieces_with_incompatible_edges_stay_apart():
    """Bande noire sous une page blanche : rien à recoller."""
    results = extract(make_pdf([draw(solid(648, (255, 255, 255)), TOP),
                                draw(solid(52, (0, 0, 0)), TOP)]))
    assert [r["pieces"] for r in results] == [1, 1]


def test_squashed_full_image_is_not_a_piece():
    """Image entière écrasée pour tenir dans la zone (échelles différentes
    en largeur et en hauteur) : jamais le haut d'une image coupée."""
    def squashed(page):
        page.insert_image(rect(TOP, 648), stream=png_bytes(gradient(700)), keep_proportion=False)
    results = extract(make_pdf([squashed, draw(gradient(700).crop((0, 648, WIDTH, 700)), TOP)]))
    assert [r["pieces"] for r in results] == [1, 1]


def test_image_used_on_two_pages_is_returned_each_time():
    def first(page):
        page.insert_image(rect(TOP, 300), stream=png_bytes(solid(300, (10, 120, 30))))

    doc = fitz.open()
    first(doc.new_page(width=PAGE_W, height=PAGE_H))
    xref = doc[0].get_images()[0][0]
    doc.new_page(width=PAGE_W, height=PAGE_H).insert_image(rect(TOP, 300), xref=xref)
    doc = fitz.open("pdf", doc.tobytes())
    results = extract(doc)
    assert [r["page"] for r in results] == [0, 1]
    assert results[0]["data"] == results[1]["data"]


def test_images_follow_the_reading_order_of_the_page():
    colors = {"top_left": (255, 0, 0), "top_right": (0, 255, 0), "bottom_left": (0, 0, 255)}

    def build(page):
        def put(x, y, color):
            page.insert_image(fitz.Rect(x, y, x + 100, y + 100),
                              stream=png_bytes(Image.new("RGB", (100, 100), color)))
        put(72, 400, colors["bottom_left"])
        put(300, 72, colors["top_right"])
        put(72, 72, colors["top_left"])

    results = extract(make_pdf([build]))
    assert [opened(r).convert("RGB").getpixel((5, 5)) for r in results] == [
        colors["top_left"], colors["top_right"], colors["bottom_left"]]


def test_pdf_without_images_gives_nothing():
    assert extract(make_pdf([lambda page: page.insert_text((100, 100), "texte")])) == []


def test_jpeg_image_is_kept_as_stored():
    data = jpeg_bytes()
    results = extract(make_pdf([lambda page: page.insert_image(rect(TOP, 648), stream=data)]))
    assert results[0]["data"] == data
    assert results[0]["dpi"] == 72


def test_decode_array_is_applied():
    """Couleurs inversées par /Decode : l'image rendue est celle que la page
    affiche, en PNG, jamais les bytes bruts."""
    def build(page):
        page.insert_image(rect(TOP, 300), stream=png_bytes(solid(300, (200, 50, 10))))

    doc = fitz.open()
    build(doc.new_page(width=PAGE_W, height=PAGE_H))
    doc.xref_set_key(doc[0].get_images()[0][0], "Decode", "[1 0 1 0 1 0]")
    doc = fitz.open("pdf", doc.tobytes())
    image = opened(extract(doc)[0])
    assert image.format == "PNG"
    assert image.convert("RGB").getpixel((5, 5)) == (55, 205, 245)


def test_transparency_is_kept():
    results = extract(make_pdf([draw(Image.new("RGBA", (WIDTH, 300), (0, 0, 255, 100)), TOP)]))
    image = opened(results[0])
    assert image.format == "PNG"
    assert image.mode == "RGBA"
    assert image.getpixel((5, 5))[3] == 100


def placement(page, top, height, first=True, last=True, scale=1.0):
    return {"page": page, "xref": 5, "width": WIDTH, "height": round(height * scale),
            "bbox": (LEFT, top, LEFT + WIDTH, top + height),
            "transform": (WIDTH, 0, 0, height, LEFT, top), "first": first, "last": last}


def test_continuation_needs_the_very_next_page():
    upper = placement(0, TOP, 648)
    assert continues(upper, placement(1, TOP, 52), (TOP, BOTTOM))
    assert not continues(upper, placement(2, TOP, 52), (TOP, BOTTOM))


def test_continuation_needs_the_last_and_the_first_image_of_their_pages():
    area = (TOP, BOTTOM)
    assert not continues(placement(0, TOP, 648, last=False), placement(1, TOP, 52), area)
    assert not continues(placement(0, TOP, 648), placement(1, TOP, 52, first=False), area)


def test_continuation_needs_the_bottom_and_the_top_of_the_area():
    area = (TOP, BOTTOM)
    assert not continues(placement(0, TOP, 600), placement(1, TOP, 52), area)
    assert not continues(placement(0, TOP, 648), placement(1, TOP + 20, 52), area)
