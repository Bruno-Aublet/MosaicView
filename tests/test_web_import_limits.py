"""Import Web : limites face à une page ou un serveur hostiles (ou démesurés).

Sans elles, une boucle JS de 10^12 itérations produirait des milliards
d'adresses, une expression géante ferait tourner la regex de concaténation
pendant des heures, et une réponse sans fin remplirait la mémoire.
"""
import io
import time

import pytest

from modules.qt import web_import_qt as web
from modules.qt.web_import_qt import extract_images_from_js_loops

PAGE = ("<script>const BASE='img/'; function pad(n){return String(n).padStart(3,'0')} "
        "for (let i = 1; i <= 3; i++) { el.src = BASE + pad(i) + '.jpg'; }</script>")


def test_normal_loop_is_resolved():
    assert extract_images_from_js_loops("https://ex.com/p/", PAGE) == [
        "https://ex.com/p/img/001.jpg", "https://ex.com/p/img/002.jpg",
        "https://ex.com/p/img/003.jpg"]


def test_huge_loop_is_ignored_quickly():
    page = PAGE.replace("i <= 3", "i <= 1000000000000")
    start = time.monotonic()
    assert extract_images_from_js_loops("https://ex.com/p/", page) == []
    assert time.monotonic() - start < 2


def test_giant_expression_is_ignored_quickly():
    page = "for (let i = 1; i <= 3; i++) { el.src = " + "a" * 1_000_000 + "!; }"
    start = time.monotonic()
    assert extract_images_from_js_loops("https://ex.com/", page) == []
    assert time.monotonic() - start < 5


def test_number_of_urls_is_capped():
    page = "for (let i = 1; i <= 4000; i++) { el.src = 'x' + i + '.jpg'; }"
    assert len(extract_images_from_js_loops("https://ex.com/", page)) == web._MAX_EXTRACTED_URLS


class _Response:
    def __init__(self, data, length=None, endless=False):
        self._buf = io.BytesIO(data)
        self._endless = endless
        self.headers = {"Content-Length": str(length)} if length is not None else {}

    def read(self, n=-1):
        if self._endless:
            return b"x" * n
        return self._buf.read(n)


def test_read_limited_returns_the_content():
    assert web._read_limited(_Response(b"abc" * 1000), 10_000) == b"abc" * 1000


def test_read_limited_refuses_an_announced_size_over_the_limit():
    with pytest.raises(web.DownloadTooLargeError):
        web._read_limited(_Response(b"", length=10_000), 100)


def test_read_limited_stops_an_endless_response():
    with pytest.raises(web.DownloadTooLargeError):
        web._read_limited(_Response(b"", endless=True), 1024 * 1024)


def test_read_limited_returns_none_when_cancelled():
    assert web._read_limited(_Response(b"abc"), 100, cancel_flag=[True]) is None


def test_page_title_never_contains_credentials_or_port():
    """Préfixe des noms de pages : nom du site seul, jamais l'identifiant ni le
    mot de passe qu'une adresse peut porter."""
    assert web.page_title_from_url("https://bruno:Secret@galerie.exemple.com:8080/a") \
        == "galerie.exemple.com"
    assert web.page_title_from_url("https://www.exemple.com/album") == "exemple.com"
    assert web.page_title_from_url("pas une adresse") == ""


def test_missed_image_name_never_contains_credentials():
    """Nom d'une image manquée dans la fenêtre « Fichiers ignorés » : fin du
    chemin, décodée, sans paramètres, ou nom du site ; jamais l'identifiant
    ni le mot de passe de l'adresse."""
    assert web._url_display_name(
        "https://bruno:Secret@ex.com/album/page%20012.jpg?token=abc") == "page 012.jpg"
    assert web._url_display_name("https://bruno:Secret@ex.com:8080/") == "ex.com"
    assert web._url_display_name("https://ex.com/a%0Ab.jpg") == "a_b.jpg"
