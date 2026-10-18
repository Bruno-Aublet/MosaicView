"""Page au-delà de la limite de pixels de Pillow (Image.MAX_IMAGE_PIXELS).

create_entry la marque trop grande et affiche ses dimensions, lues dans
l'en-tête par entries._header_size, sans jamais lever la limite pour toute
l'application : une image ouverte au même moment par un autre thread y
échapperait.
"""
import io

import pytest
from PIL import Image

from modules.qt import entries
from modules.qt.archive_names import IMAGE_EXTS


def _png(width, height):
    buf = io.BytesIO()
    Image.new("L", (width, height)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def small_limit(monkeypatch):
    # 100x80 = 8 000 pixels, plus du double de la limite : Image.open refuse
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)


def test_header_size_reads_dimensions_of_a_refused_image(small_limit):
    data = _png(100, 80)
    with pytest.raises(Image.DecompressionBombError):
        Image.open(io.BytesIO(data))
    assert entries._header_size(data) == (100, 80)
    assert Image.MAX_IMAGE_PIXELS == 1000


def test_too_large_page_shows_its_dimensions_and_keeps_the_limit(small_limit, monkeypatch):
    seen = []
    real_header_size = entries._header_size

    def spy(data):
        result = real_header_size(data)
        # La limite est restée en place pendant la lecture des dimensions
        seen.append(Image.MAX_IMAGE_PIXELS)
        return result

    monkeypatch.setattr(entries, "_header_size", spy)
    entry = entries.create_entry("big.png", _png(100, 80), IMAGE_EXTS)
    assert entry["is_too_large"] and entry["is_corrupted"]
    assert entry["corruption_reason"] == "100x80 (8,000 pixels)"
    assert seen == [1000]
    assert Image.MAX_IMAGE_PIXELS == 1000


def test_header_size_of_unreadable_data():
    assert entries._header_size(b"not an image at all") == (0, 0)
