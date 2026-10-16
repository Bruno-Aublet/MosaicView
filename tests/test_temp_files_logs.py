"""Journaux des traitements par lot dans %TEMP%\\MosaicViewTemp.

- unique_log_path : deux lots du même type terminés dans la même minute ne
  doivent jamais écrire dans le même journal (le nom n'est daté qu'à la minute).
- cleanup_all_temp_files(keep_logs=True), appelé à la fermeture : tous les
  journaux survivent, quel que soit le type de lot.
"""
import datetime
import os

import pytest

from modules.qt import temp_files
from modules.qt.temp_files import cleanup_all_temp_files, unique_log_path

NOW = datetime.datetime(2026, 10, 8, 14, 32, 45)


def test_first_log_has_plain_name(tmp_path):
    path = unique_log_path(str(tmp_path), "cb7tocbz", NOW)
    assert os.path.basename(path) == "Log_cb7tocbz_2026_10_08_14_32.txt"


def test_same_minute_logs_get_suffixes(tmp_path):
    names = []
    for _i in range(3):
        path = unique_log_path(str(tmp_path), "cb7tocbz", NOW)
        open(path, "w").close()
        names.append(os.path.basename(path))
    assert names == ["Log_cb7tocbz_2026_10_08_14_32.txt",
                     "Log_cb7tocbz_2026_10_08_14_32_2.txt",
                     "Log_cb7tocbz_2026_10_08_14_32_3.txt"]


def test_other_batch_type_is_not_suffixed(tmp_path):
    open(unique_log_path(str(tmp_path), "cb7tocbz", NOW), "w").close()
    path = unique_log_path(str(tmp_path), "pdftocbz", NOW)
    assert os.path.basename(path) == "Log_pdftocbz_2026_10_08_14_32.txt"


@pytest.fixture
def fake_temp(tmp_path, monkeypatch):
    """Redirige le dossier temporaire système vers tmp_path."""
    monkeypatch.setattr(temp_files.tempfile, "gettempdir", lambda: str(tmp_path))
    folder = tmp_path / "MosaicViewTemp"
    folder.mkdir()
    return folder


ALL_LOGS = ["Log_cbrtocbz_2026_10_08_14_32.txt", "Log_cb7tocbz_2026_10_08_14_32.txt",
            "Log_cbttocbz_2026_10_08_14_32_2.txt", "Log_pdftocbz_2026_10_08_14_32.txt",
            "Log_imgtocbz_2026_10_08_14_32.txt", "Log_recompress_2026_10_08_14_32.txt",
            "Log_metadata_2026_10_08_14_32.txt", "Log_scan.txt"]


def test_closing_keeps_every_batch_log(fake_temp):
    for name in ALL_LOGS:
        (fake_temp / name).write_text("log")
    (fake_temp / "extracted.jpg").write_bytes(b"x")
    cleanup_all_temp_files(keep_logs=True)
    assert sorted(os.listdir(fake_temp)) == sorted(ALL_LOGS)


def test_manual_cleanup_removes_logs(fake_temp):
    for name in ALL_LOGS:
        (fake_temp / name).write_text("log")
    cleanup_all_temp_files(keep_logs=False)
    assert os.listdir(fake_temp) == []
