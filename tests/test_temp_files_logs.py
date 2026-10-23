"""Journaux des traitements par lot dans %TEMP%\\MosaicViewTemp.

- unique_log_path : deux lots du même type terminés dans la même minute ne
  doivent jamais écrire dans le même journal (le nom n'est daté qu'à la minute).
- cleanup_all_temp_files(keep_logs=True), appelé à la fermeture : tous les
  journaux de moins de LOG_MAX_AGE_DAYS jours survivent, quel que soit le
  type de lot ; les plus anciens sont supprimés.
"""
import datetime
import os

import pytest

from modules.qt import temp_files
from modules.qt.temp_files import cleanup_all_temp_files, unique_log_path, log_text

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


def test_closing_removes_logs_older_than_the_limit(fake_temp):
    """Un fichier par lot s'accumulerait sinon sans limite (audit 8, P4-2)."""
    import time
    old, recent = fake_temp / ALL_LOGS[0], fake_temp / ALL_LOGS[1]
    old.write_text("log")
    recent.write_text("log")
    limit = temp_files.LOG_MAX_AGE_DAYS * 24 * 60 * 60
    past = time.time() - limit - 60
    os.utime(old, (past, past))
    nearly = time.time() - limit + 3600
    os.utime(recent, (nearly, nearly))
    cleanup_all_temp_files(keep_logs=True)
    assert os.listdir(fake_temp) == [recent.name]


def test_legacy_cleanup_removes_only_mosaicview_clipboard_dirs(fake_temp):
    """%TEMP% est partagé : seuls les dossiers nommés comme MosaicView les
    nommait (clipboard_<id>_<horodatage>) sont effacés."""
    temp = fake_temp.parent
    for name in ("clipboard_2155_1760000000", "clipboard_other_app", "clipboard_cache"):
        (temp / name).mkdir()
    temp_files.cleanup_legacy_root_clipboard_dirs()
    remaining = sorted(n for n in os.listdir(temp) if n.startswith("clipboard_"))
    assert remaining == ["clipboard_cache", "clipboard_other_app"]


def test_log_text_keeps_one_line_per_error():
    """Un nom de membre avec un retour à la ligne ne crée pas de fausse ligne
    dans le journal d'un lot : le caractère est écrit sous forme échappée."""
    assert log_text("x.cbr: page\nFAUSSE LIGNE.jpg: CRC") == "x.cbr: page\\nFAUSSE LIGNE.jpg: CRC"
    assert log_text("a\r\tb\x07") == "a\\r\\tb\\x07"
    assert log_text("Résumé normal — ok") == "Résumé normal — ok"
    assert log_text(ValueError("e")) == "e"


def test_manual_cleanup_removes_logs(fake_temp):
    for name in ALL_LOGS:
        (fake_temp / name).write_text("log")
    assert cleanup_all_temp_files(keep_logs=False) == []
    assert os.listdir(fake_temp) == []


def test_cleanup_reports_what_could_not_be_deleted(fake_temp, monkeypatch):
    """Un élément encore utilisé est rendu avec sa raison, jamais avalé : le
    bouton du mode d'emploi annoncerait sinon un succès."""
    (fake_temp / "locked.jpg").write_bytes(b"x")
    (fake_temp / "free.jpg").write_bytes(b"x")
    real_unlink = os.unlink

    def unlink(path):
        if os.path.basename(path) == "locked.jpg":
            raise PermissionError("in use")
        real_unlink(path)
    monkeypatch.setattr(temp_files.os, "unlink", unlink)
    failures = cleanup_all_temp_files(keep_logs=False)
    assert failures == [("locked.jpg", "in use")]
    assert os.listdir(fake_temp) == ["locked.jpg"]
