"""Remplacement d'un fichier verrouillé par un autre programme.

Sous Windows, os.replace échoue si le fichier de destination est ouvert
ailleurs (lecteur de BD, antivirus, scan de la Bibliothèque).
replace_file_retrying retente brièvement puis lève FileInUseError, dont le
message explique la cause, au lieu du message système brut ; un fichier en
lecture seule lève ReadOnlyFileError, au message tout aussi explicite.
"""
import os
import stat
import sys

import pytest

from modules.qt.utils import (
    FileInUseError,
    ReadOnlyFileError,
    fsync_file,
    replace_file_atomically,
    replace_file_retrying,
)

windows_only = pytest.mark.skipif(sys.platform != "win32",
                                  reason="verrouillage de fichier propre à Windows")


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _seconds: None)


def test_replaces_free_file(tmp_path):
    src, dest = tmp_path / "new.tmp", tmp_path / "comic.cbz"
    src.write_bytes(b"NEW")
    dest.write_bytes(b"OLD")
    replace_file_retrying(str(src), str(dest))
    assert dest.read_bytes() == b"NEW"
    assert not src.exists()


@windows_only
def test_locked_file_raises_file_in_use(tmp_path, no_wait):
    src, dest = tmp_path / "new.tmp", tmp_path / "comic.cbz"
    src.write_bytes(b"NEW")
    dest.write_bytes(b"OLD")
    with open(dest, "rb"):
        with pytest.raises(FileInUseError) as info:
            replace_file_retrying(str(src), str(dest))
    assert info.value.path == str(dest)
    assert dest.read_bytes() == b"OLD"
    assert src.exists()


@windows_only
def test_lock_released_during_retries_succeeds(tmp_path, monkeypatch):
    src, dest = tmp_path / "new.tmp", tmp_path / "comic.cbz"
    src.write_bytes(b"NEW")
    dest.write_bytes(b"OLD")
    handle = open(dest, "rb")
    # Le verrou est levé pendant la première attente.
    monkeypatch.setattr("time.sleep", lambda _seconds: handle.close())
    replace_file_retrying(str(src), str(dest))
    assert dest.read_bytes() == b"NEW"


@windows_only
def test_read_only_file_raises_read_only_error(tmp_path, no_wait):
    """Message de l'application (traduit), pas « [WinError 5] Accès refusé »
    dans la langue de Windows ; jamais confondu avec un fichier ouvert ailleurs."""
    src, dest = tmp_path / "new.tmp", tmp_path / "comic.cbz"
    src.write_bytes(b"NEW")
    dest.write_bytes(b"OLD")
    os.chmod(dest, stat.S_IREAD)
    try:
        with pytest.raises(ReadOnlyFileError) as info:
            replace_file_retrying(str(src), str(dest))
        assert not isinstance(info.value, FileInUseError)
        assert info.value.path == str(dest)
        assert dest.read_bytes() == b"OLD"
    finally:
        os.chmod(dest, stat.S_IWRITE | stat.S_IREAD)


@windows_only
def test_atomic_write_onto_locked_file_keeps_original(tmp_path, no_wait):
    dest = tmp_path / "comic.cbz"
    dest.write_bytes(b"ORIGINAL")
    with open(dest, "rb"):
        with pytest.raises(FileInUseError):
            replace_file_atomically(str(dest), lambda tmp: open(tmp, "wb").write(b"NEW"))
    assert dest.read_bytes() == b"ORIGINAL"
    assert [n for n in os.listdir(tmp_path) if n.startswith(".~mosaicview_")] == []


def test_file_in_use_message_is_computed_at_display():
    """Le texte est recalculé à chaque str() (suit un changement de langue)."""
    error = FileInUseError(os.path.join("dossier", "comic.cbz"))
    assert str(error)
    assert str(error) == str(error)


def test_fsync_file_keeps_content(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"CONTENT")
    fsync_file(str(path))
    assert path.read_bytes() == b"CONTENT"
