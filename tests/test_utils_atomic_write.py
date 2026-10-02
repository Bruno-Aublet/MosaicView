"""Écriture des CBZ : remplacement atomique et estimation de la place nécessaire."""
import os
import zipfile

import pytest

from modules.qt.utils import estimate_cbz_size, replace_file_atomically


def leftovers(folder):
    return [name for name in os.listdir(folder) if name.startswith(".~mosaicview_")]


# ---------------------------------------------------------------------------
# replace_file_atomically
# ---------------------------------------------------------------------------

def test_creates_new_file(tmp_path):
    dest = tmp_path / "new.cbz"
    replace_file_atomically(str(dest), lambda tmp: open(tmp, "wb").write(b"NEW"))
    assert dest.read_bytes() == b"NEW"
    assert leftovers(tmp_path) == []


def test_replaces_existing_file(tmp_path):
    dest = tmp_path / "comic.cbz"
    dest.write_bytes(b"ORIGINAL")
    replace_file_atomically(str(dest), lambda tmp: open(tmp, "wb").write(b"NEW"))
    assert dest.read_bytes() == b"NEW"
    assert leftovers(tmp_path) == []


def test_failed_write_keeps_original_and_removes_temp(tmp_path):
    dest = tmp_path / "comic.cbz"
    dest.write_bytes(b"ORIGINAL")

    def write_then_fail(tmp):
        with open(tmp, "wb") as f:
            f.write(b"NE")
        raise OSError(28, "No space left on device")

    with pytest.raises(OSError):
        replace_file_atomically(str(dest), write_then_fail)
    assert dest.read_bytes() == b"ORIGINAL"
    assert leftovers(tmp_path) == []


def test_failed_write_creates_nothing(tmp_path):
    dest = tmp_path / "new.cbz"

    def fail(tmp):
        raise RuntimeError("write error")

    with pytest.raises(RuntimeError):
        replace_file_atomically(str(dest), fail)
    assert not dest.exists()
    assert leftovers(tmp_path) == []


def test_interruption_removes_temp(tmp_path):
    class Interrupted(BaseException):
        pass

    dest = tmp_path / "comic.cbz"
    dest.write_bytes(b"ORIGINAL")

    def interrupted(tmp):
        raise Interrupted()

    with pytest.raises(Interrupted):
        replace_file_atomically(str(dest), interrupted)
    assert dest.read_bytes() == b"ORIGINAL"
    assert leftovers(tmp_path) == []


def test_temp_file_is_next_to_destination(tmp_path):
    """Même dossier, donc même volume : os.replace remplace sans recopier."""
    dest = tmp_path / "sub" / "comic.cbz"
    dest.parent.mkdir()
    seen = []
    replace_file_atomically(str(dest), lambda tmp: (seen.append(tmp), open(tmp, "wb").close()))
    assert os.path.dirname(seen[0]) == str(dest.parent)


# ---------------------------------------------------------------------------
# estimate_cbz_size
# ---------------------------------------------------------------------------

def entry(name, data, is_dir=False):
    return {"orig_name": name, "bytes": data, "is_dir": is_dir}


def test_estimate_is_at_least_the_real_size(tmp_path):
    entries = [entry("p01.jpg", os.urandom(5000)),
               entry("Été/page 02.png", os.urandom(300)),
               entry("ComicInfo.xml", b"<ComicInfo/>")]
    out = tmp_path / "out.cbz"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as zf:
        for e in entries:
            zf.writestr(e["orig_name"], e["bytes"])
    assert estimate_cbz_size(entries) >= out.stat().st_size


def test_estimate_ignores_folders_and_missing_data():
    base = estimate_cbz_size([])
    assert estimate_cbz_size([entry("dir/", b"", is_dir=True),
                              entry("unread.jpg", None)]) == base


def test_estimate_of_empty_archive_covers_an_empty_zip(tmp_path):
    out = tmp_path / "empty.cbz"
    zipfile.ZipFile(out, "w").close()
    assert estimate_cbz_size([]) >= out.stat().st_size
