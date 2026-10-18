"""Lectures de PDF de la Bibliothèque dans un process séparé
(modules/qt/pdf_probe.py) : un PDF qui fait planter MuPDF ne tue que ce
process, et la demande renvoie None au lieu de bloquer."""
import pytest

fitz = pytest.importorskip("pymupdf")

from modules.qt import library_db
from modules.qt import pdf_probe
from modules.qt.pdf_probe import PdfProbe


def _make_pdf(path, pages=3):
    doc = fitz.open()
    for _i in range(pages):
        doc.new_page(width=200, height=300)
    doc.save(str(path))
    doc.close()


@pytest.fixture
def probe():
    p = PdfProbe(timeout=60)
    yield p
    p.shutdown()


def test_page_count_and_cover(tmp_path, probe):
    pdf = tmp_path / "comic.pdf"
    _make_pdf(pdf)
    assert probe.page_count(str(pdf)) == 3
    w, h, samples = probe.cover_rgb(str(pdf))
    assert (w, h) == (200, 300)
    assert len(samples) == w * h * 3


def test_unreadable_pdf_gives_none_and_the_process_keeps_working(tmp_path, probe):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    good = tmp_path / "good.pdf"
    _make_pdf(good, pages=2)
    assert probe.page_count(str(bad)) is None
    assert probe.page_count(str(good)) == 2


def test_dead_process_is_replaced_at_the_next_request(tmp_path, probe):
    """Process planté (simulé par close()) : la demande suivante en relance un."""
    pdf = tmp_path / "comic.pdf"
    _make_pdf(pdf, pages=4)
    assert probe.page_count(str(pdf)) == 4
    probe.close()
    assert probe.page_count(str(pdf)) == 4


def test_no_request_after_shutdown(tmp_path, probe):
    pdf = tmp_path / "comic.pdf"
    _make_pdf(pdf)
    probe.shutdown()
    assert probe.page_count(str(pdf)) is None
    assert probe._proc is None


def test_cover_jpeg_for_batch_metadata_import(tmp_path, probe):
    pdf = tmp_path / "comic.pdf"
    _make_pdf(pdf)
    status, data = probe.call(('cover_jpeg', str(pdf), 96))
    assert status == pdf_probe.OK
    assert data[:3] == b"\xff\xd8\xff"


def test_error_message_is_returned(tmp_path, probe):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    status, message = probe.call(('cover_jpeg', str(bad), 96))
    assert status == pdf_probe.ERROR
    assert message


def test_unlock_writes_an_unencrypted_copy(tmp_path, probe):
    locked = tmp_path / "locked.pdf"
    doc = fitz.open()
    for _i in range(2):
        doc.new_page()
    doc.save(str(locked), encryption=fitz.PDF_ENCRYPT_AES_256,
             owner_pw="owner", user_pw="", permissions=0)
    doc.close()
    dest = tmp_path / "locked_unlocked.pdf"
    status, _result = probe.call(('unlock', str(locked), str(dest)), timeout=None)
    assert status == pdf_probe.OK
    out = fitz.open(str(dest))
    try:
        assert not out.is_encrypted
        assert out.page_count == 2
    finally:
        out.close()


def test_count_pages_reads_a_pdf_only_through_the_probe(tmp_path, probe):
    pdf = tmp_path / "comic.pdf"
    _make_pdf(pdf, pages=5)
    assert library_db._count_pages(str(pdf)) is None
    assert library_db._count_pages(str(pdf), probe) == 5
