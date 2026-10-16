"""Écriture des CBZ : remplacement atomique et estimation de la place nécessaire."""
import os
import time
import zipfile

import pytest

from modules.qt.utils import (
    STALE_TEMP_AGE, MissingPageDataError, WrittenFileCheckError, cbz_members,
    estimate_cbz_size, make_temp_path, release_temp_path, remove_stale_temp_files,
    replace_file_atomically, verify_written_cbz,
)


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
# Temporaires abandonnés par un arrêt brutal
# ---------------------------------------------------------------------------

def _make_old(path, age=STALE_TEMP_AGE + 60):
    path.write_bytes(b"partial")
    old = time.time() - age
    os.utime(path, (old, old))


def test_old_orphan_temp_is_removed_on_next_write(tmp_path):
    orphan = tmp_path / ".~mosaicview_abc123.tmp"
    _make_old(orphan)
    replace_file_atomically(str(tmp_path / "comic.cbz"), lambda tmp: open(tmp, "wb").write(b"NEW"))
    assert not orphan.exists()
    assert leftovers(tmp_path) == []


def test_recent_temp_is_kept(tmp_path):
    """Un temporaire récent peut appartenir à une écriture encore en cours."""
    recent = tmp_path / ".~mosaicview_abc123.tmp"
    recent.write_bytes(b"partial")
    remove_stale_temp_files(str(tmp_path))
    assert recent.exists()


def test_temp_of_running_write_is_kept(tmp_path):
    """Une écriture en cours dans le même dossier ne perd jamais son temporaire,
    même ancien (écriture longue sur un support lent)."""
    tmp = make_temp_path(str(tmp_path))
    try:
        old = time.time() - STALE_TEMP_AGE - 60
        os.utime(tmp, (old, old))
        remove_stale_temp_files(str(tmp_path))
        assert os.path.exists(tmp)
    finally:
        release_temp_path(tmp)
        os.remove(tmp)


def test_user_files_are_never_removed(tmp_path):
    """Seuls les noms de temporaire MosaicView sont visés."""
    names = ["comic.cbz", "notes.tmp", ".~mosaicview_x.cbz", "~mosaicview_x.tmp"]
    for name in names:
        _make_old(tmp_path / name)
    remove_stale_temp_files(str(tmp_path))
    assert sorted(os.listdir(tmp_path)) == sorted(names)


def test_missing_folder_is_ignored(tmp_path):
    remove_stale_temp_files(str(tmp_path / "absent"))


# ---------------------------------------------------------------------------
# verify_written_cbz : relecture avant suppression de la source
# ---------------------------------------------------------------------------

def _cbz(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_sound_cbz_passes(tmp_path):
    path = tmp_path / "ok.cbz"
    _cbz(path, {"p01.jpg": b"A" * 1000, "p02.jpg": b"B" * 1000})
    verify_written_cbz(str(path), 2)
    verify_written_cbz(str(path))


def test_wrong_file_count_fails(tmp_path):
    path = tmp_path / "ok.cbz"
    _cbz(path, {"p01.jpg": b"A" * 1000})
    with pytest.raises(WrittenFileCheckError) as info:
        verify_written_cbz(str(path), 2)
    assert (info.value.written, info.value.expected) == (1, 2)


def test_corrupted_member_fails(tmp_path):
    """Données abîmées sur le disque après l'écriture : CRC faux."""
    path = tmp_path / "bad.cbz"
    _cbz(path, {"p01.jpg": b"A" * 5000})
    raw = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as zf:
        info = zf.infolist()[0]
    start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    raw[start + 2] ^= 0xFF
    path.write_bytes(bytes(raw))
    with pytest.raises(WrittenFileCheckError):
        verify_written_cbz(str(path))


def test_truncated_cbz_fails(tmp_path):
    path = tmp_path / "cut.cbz"
    _cbz(path, {"p01.jpg": b"A" * 5000})
    path.write_bytes(path.read_bytes()[:40])
    with pytest.raises(WrittenFileCheckError):
        verify_written_cbz(str(path))


def test_folders_are_not_counted(tmp_path):
    path = tmp_path / "dirs.cbz"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("chap1/", b"")
        zf.writestr("chap1/p01.jpg", b"A")
    verify_written_cbz(str(path), 1)


# ---------------------------------------------------------------------------
# Identité du fichier remplacé (Windows)
# ---------------------------------------------------------------------------

windows_only = pytest.mark.skipif(os.name != "nt", reason="ReplaceFileW : Windows")


@windows_only
def test_replaced_file_keeps_creation_date(tmp_path):
    dest = tmp_path / "comic.cbz"
    dest.write_bytes(b"ORIGINAL")
    old = time.time() - 86400 * 30
    _set_creation_time(str(dest), old)
    created = os.stat(dest).st_ctime
    replace_file_atomically(str(dest), lambda tmp: open(tmp, "wb").write(b"NEW"))
    assert dest.read_bytes() == b"NEW"
    assert abs(os.stat(dest).st_ctime - created) < 1


@windows_only
def test_replaced_file_keeps_hidden_attribute(tmp_path):
    import ctypes
    import stat
    dest = tmp_path / "comic.cbz"
    dest.write_bytes(b"ORIGINAL")
    ctypes.windll.kernel32.SetFileAttributesW(str(dest), stat.FILE_ATTRIBUTE_HIDDEN)
    replace_file_atomically(str(dest), lambda tmp: open(tmp, "wb").write(b"NEW"))
    assert dest.read_bytes() == b"NEW"
    assert os.stat(dest).st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN


def test_symlink_target_receives_content(tmp_path):
    """Le lien reste un lien ; c'est sa cible qui est réécrite."""
    target = tmp_path / "real.cbz"
    target.write_bytes(b"ORIGINAL")
    link = tmp_path / "link.cbz"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("création de lien symbolique non autorisée sur ce système")
    replace_file_atomically(str(link), lambda tmp: open(tmp, "wb").write(b"NEW"))
    assert link.is_symlink()
    assert target.read_bytes() == b"NEW"


def _set_creation_time(path, timestamp):
    """Date de création forcée via SetFileTime (os.utime ne la modifie pas)."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(path, 0x100, 0x7, None, 3, 0x80, None)  # FILE_WRITE_ATTRIBUTES
    assert handle not in (None, wintypes.HANDLE(-1).value)
    try:
        ticks = int((timestamp + 11644473600) * 10_000_000)
        ft = wintypes.FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)
        assert kernel32.SetFileTime(wintypes.HANDLE(handle), ctypes.byref(ft), None, None)
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


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


# ---------------------------------------------------------------------------
# cbz_members
# ---------------------------------------------------------------------------

def test_members_skip_folders_and_keep_order():
    a, b = entry("a.jpg", b"A"), entry("b.jpg", b"B")
    assert cbz_members([entry("dir/", b"", is_dir=True), b, a]) == [("b.jpg", b), ("a.jpg", a)]


def test_entry_without_data_is_refused():
    """Fichier illisible à l'ouverture : l'écrire sans lui le perdrait."""
    with pytest.raises(MissingPageDataError) as info:
        cbz_members([entry("a.jpg", b"A"), entry("broken.jpg", None), entry("x.xml", None)])
    assert info.value.names == ["broken.jpg", "x.xml"]


def test_homonyms_get_distinct_names():
    """Deux entrées ZIP de même nom : read(nom) rend le dernier contenu pour
    les deux, le premier serait perdu à la réouverture."""
    first, second, other = entry("p.jpg", b"1"), entry("p.jpg", b"2"), entry("p_2.jpg", b"3")
    names = [name for name, _e in cbz_members([first, second, other])]
    assert names[0] == "p.jpg"
    assert len(set(names)) == 3
    assert "p_2.jpg" in names and names[1] != "p_2.jpg"   # jamais le nom d'une autre page


def test_written_homonyms_are_all_readable(tmp_path):
    out = tmp_path / "dups.cbz"
    pages = [entry("p.jpg", b"1"), entry("p.jpg", b"2")]
    with zipfile.ZipFile(out, "w") as zf:
        for name, e in cbz_members(pages):
            zf.writestr(name, e["bytes"])
    with zipfile.ZipFile(out) as zf:
        assert sorted(zf.read(n) for n in zf.namelist()) == [b"1", b"2"]
