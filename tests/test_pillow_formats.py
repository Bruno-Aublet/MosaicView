"""archive_names.restrict_pillow_formats : seuls les formats d'image gérés sont
reconnus à l'ouverture. Une page nommée .jpg contenant un EPS ne doit jamais
être confiée à Ghostscript."""
import io

import pytest
from PIL import Image, UnidentifiedImageError

from modules.qt.archive_names import restrict_pillow_formats


@pytest.fixture
def restricted():
    Image.init()
    saved = list(Image.ID)
    restrict_pillow_formats()
    yield
    Image.ID[:] = saved


def _bytes(fmt):
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (10, 20, 30)).save(buf, fmt)
    return buf.getvalue()


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "GIF", "WEBP", "BMP", "TIFF", "ICO", "AVIF"])
def test_managed_formats_still_open(restricted, fmt):
    assert Image.open(io.BytesIO(_bytes(fmt))).format == fmt


def test_eps_is_not_recognised(restricted, monkeypatch):
    from PIL import EpsImagePlugin
    called = []
    monkeypatch.setattr(EpsImagePlugin, "Ghostscript", lambda *a, **k: called.append(1))
    eps = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n"
    with pytest.raises(UnidentifiedImageError):
        Image.open(io.BytesIO(eps)).load()
    assert not called


def test_unmanaged_format_is_not_recognised(restricted):
    with pytest.raises(UnidentifiedImageError):
        Image.open(io.BytesIO(_bytes("PPM")))


def test_page_with_eps_content_becomes_unreadable(restricted):
    from modules.qt.archive_names import IMAGE_EXTS
    from modules.qt.entries import create_entry
    eps = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n"
    entry = create_entry("page01.jpg", eps, IMAGE_EXTS)
    assert entry["is_corrupted"]
