import io
import tarfile
import zipfile

from modules.qt.archive_type_detector import detect_archive_type


def make_zip(path, with_epub_mimetype=False, empty=False):
    with zipfile.ZipFile(path, "w") as zf:
        if with_epub_mimetype:
            zf.writestr("mimetype", "application/epub+zip")
        if not empty and not with_epub_mimetype:
            zf.writestr("page01.jpg", b"fake image bytes")
    return path


def make_tar(path, compression=""):
    mode = f"w:{compression}" if compression else "w"
    with tarfile.open(path, mode) as tf:
        data = b"fake image bytes"
        info = tarfile.TarInfo(name="page01.jpg")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return path


def write_magic(path, magic_bytes):
    path.write_bytes(magic_bytes + b"\x00" * 16)
    return path


# ---------------------------------------------------------------------------
# CBZ / EPUB (ZIP-based)
# ---------------------------------------------------------------------------

def test_detect_cbz_plain_zip(tmp_path):
    p = make_zip(tmp_path / "comic.cbz")
    assert detect_archive_type(str(p)) == "CBZ"


def test_detect_epub_via_mimetype_entry(tmp_path):
    p = make_zip(tmp_path / "book.epub", with_epub_mimetype=True)
    assert detect_archive_type(str(p)) == "EPUB"


def test_detect_cbz_zip_with_unrelated_mimetype_entry(tmp_path):
    p = tmp_path / "weird.cbz"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("mimetype", "text/plain")
        zf.writestr("page01.jpg", b"data")
    assert detect_archive_type(str(p)) == "CBZ"


def test_detect_cbz_empty_zip_end_of_central_directory_magic(tmp_path):
    # ZIP vide : magic bytes 'PK\x05\x06' au lieu de 'PK\x03\x04'
    p = tmp_path / "empty.cbz"
    p.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    assert detect_archive_type(str(p)) == "CBZ"


# ---------------------------------------------------------------------------
# CBR (RAR)
# ---------------------------------------------------------------------------

def test_detect_cbr_rar4_magic(tmp_path):
    p = write_magic(tmp_path / "comic.cbr", b"Rar!\x1a\x07\x00")
    assert detect_archive_type(str(p)) == "CBR"


def test_detect_cbr_rar5_magic(tmp_path):
    p = write_magic(tmp_path / "comic.cbr", b"Rar!\x1a\x07\x01\x00")
    assert detect_archive_type(str(p)) == "CBR"


# ---------------------------------------------------------------------------
# CB7 (7z)
# ---------------------------------------------------------------------------

def test_detect_cb7_magic(tmp_path):
    p = write_magic(tmp_path / "comic.cb7", b"7z\xbc\xaf\x27\x1c")
    assert detect_archive_type(str(p)) == "CB7"


# ---------------------------------------------------------------------------
# CBT (TAR — pas de magic bytes fixes, détection via tarfile.is_tarfile)
# ---------------------------------------------------------------------------

def test_detect_cbt_plain_tar(tmp_path):
    p = make_tar(tmp_path / "comic.cbt")
    assert detect_archive_type(str(p)) == "CBT"


def test_detect_cbt_gzip_tar(tmp_path):
    p = make_tar(tmp_path / "comic.cbt", compression="gz")
    assert detect_archive_type(str(p)) == "CBT"


def test_detect_cbt_bzip2_tar(tmp_path):
    p = make_tar(tmp_path / "comic.cbt", compression="bz2")
    assert detect_archive_type(str(p)) == "CBT"


# ---------------------------------------------------------------------------
# Cas limites
# ---------------------------------------------------------------------------

def test_detect_unknown_format_returns_none(tmp_path):
    p = tmp_path / "not_an_archive.txt"
    p.write_bytes(b"just some plain text content")
    assert detect_archive_type(str(p)) is None


def test_detect_nonexistent_file_returns_none(tmp_path):
    missing = tmp_path / "does_not_exist.cbz"
    assert detect_archive_type(str(missing)) is None


def test_detect_empty_file_returns_none(tmp_path):
    p = tmp_path / "empty.bin"
    p.write_bytes(b"")
    assert detect_archive_type(str(p)) is None


def test_detect_corrupted_zip_magic_but_invalid_content_still_cbz(tmp_path):
    # Magic bytes ZIP valides en tête mais contenu corrompu ensuite :
    # zipfile.ZipFile() lève une exception (capturée), retombe sur "CBZ" par défaut.
    p = tmp_path / "corrupted.cbz"
    p.write_bytes(b"PK\x03\x04" + b"\xff" * 20)
    assert detect_archive_type(str(p)) == "CBZ"
