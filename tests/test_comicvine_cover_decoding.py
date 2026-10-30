"""
test_comicvine_cover_decoding.py — Couverture affichée dans la fenêtre
ComicVine : seuls les octets décodés par Pillow (formats restreints) et
réencodés en JPEG sont confiés à Qt.

Quand Pillow ne lit pas l'image, comicvine_dialog_qt._bytes_to_pixmap rend
None : les octets d'origine ne partent jamais vers QImage.fromData, qui
essaierait tous les décodeurs de Qt (SVG, TGA, PDF…), hors de la restriction
des formats (archive_names.restrict_pillow_formats).

Fait partie de la suite pytest normale :  python -m pytest tests/
"""
import io

from PIL import Image

import ast_loader


def _load():
    received = []

    class FakeQImage:
        @staticmethod
        def fromData(data, fmt=None):
            received.append((bytes(data), fmt))
            return "image"

    class FakeQPixmap:
        @staticmethod
        def fromImage(image):
            return f"pixmap de {image}"

    fn = ast_loader.load("comicvine_dialog_qt.py", {"_bytes_to_pixmap"},
                         QImage=FakeQImage, QPixmap=FakeQPixmap)["_bytes_to_pixmap"]
    return fn, received


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGBA", (8, 12), (10, 20, 30, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_readable_image_is_given_to_qt_as_jpeg():
    to_pixmap, received = _load()
    assert to_pixmap(_png_bytes()) == "pixmap de image"
    [(data, fmt)] = received
    assert fmt == "JPEG"
    assert data.startswith(b"\xff\xd8")


def test_unreadable_bytes_never_reach_qt():
    to_pixmap, received = _load()
    for data in (b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>',
                 b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n",
                 b"\x00\x01\x02 pas une image"):
        assert to_pixmap(data) is None
    assert received == []
