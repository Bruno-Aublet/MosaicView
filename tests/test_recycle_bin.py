"""Un fichier peut-il aller à la Corbeille ? (recycle_bin.trash_problem)

Windows supprime définitivement, sans rien dire, un fichier qu'il ne peut pas
recycler : les fenêtres qui proposent la Corbeille l'annoncent d'avance. Les
accès à Windows sont remplacés : volumes simulés."""
import pytest

from modules.qt import recycle_bin


@pytest.fixture
def volumes(monkeypatch):
    """{racine: réglages} ; table["__of__"] donne le volume d'un chemin."""
    table = {}

    def info(volume):
        return table[volume]

    monkeypatch.setattr(recycle_bin, "_volume_of", lambda p: table["__of__"](p))
    monkeypatch.setattr(recycle_bin, "_drive_type", lambda v: info(v).get("type", 3))
    monkeypatch.setattr(recycle_bin, "_has_recycle_bin", lambda v: info(v).get("bin", True))
    monkeypatch.setattr(recycle_bin, "_volume_settings",
                        lambda v: (info(v).get("nuke"), info(v).get("max_mb")))
    monkeypatch.setattr(recycle_bin, "_policy_disabled", lambda: table.get("__policy__", False))
    return table


def one_volume(volumes, **settings):
    volumes["V:\\"] = settings
    volumes["__of__"] = lambda p: "V:\\"


def make_file(tmp_path, size):
    path = tmp_path / "a.cbr"
    path.write_bytes(b"x" * size)
    return str(path)


def test_fixed_disk_with_recycle_bin_has_no_problem(volumes, tmp_path):
    one_volume(volumes, max_mb=10)
    assert recycle_bin.trash_problem(make_file(tmp_path, 100)) is None


@pytest.mark.parametrize("settings", [
    {"type": recycle_bin.DRIVE_REMOVABLE},       # clé USB, carte mémoire
    {"type": recycle_bin.DRIVE_REMOTE},          # lecteur réseau
    {"type": recycle_bin.DRIVE_CDROM},
    {"bin": False},                              # SHQueryRecycleBinW échoue
])
def test_drives_without_recycle_bin(volumes, tmp_path, settings):
    one_volume(volumes, **settings)
    assert recycle_bin.trash_problem(make_file(tmp_path, 1)) == ("no_bin",)


def test_network_share_path_has_no_recycle_bin(volumes):
    volumes["\\\\serveur\\partage\\"] = {}
    volumes["__of__"] = lambda p: "\\\\serveur\\partage\\"
    assert recycle_bin.trash_problem("\\\\serveur\\partage\\a.cbr") == ("no_bin",)


def test_recycle_bin_disabled_on_the_disk_or_by_policy(volumes, tmp_path):
    one_volume(volumes, nuke=1)
    path = make_file(tmp_path, 1)
    assert recycle_bin.trash_problem(path) == ("disabled",)
    one_volume(volumes, nuke=0)
    volumes["__policy__"] = True
    assert recycle_bin.trash_problem(path) == ("disabled",)


def test_file_larger_than_the_recycle_bin(volumes, tmp_path):
    one_volume(volumes, max_mb=1)
    small = make_file(tmp_path, 1024 * 1024)
    assert recycle_bin.trash_problem(small) is None              # exactement le maximum
    big = make_file(tmp_path, 1024 * 1024 + 1)
    assert recycle_bin.trash_problem(big) == ("too_large", 1024 * 1024 + 1, 1024 * 1024)


def test_folder_is_never_too_large(volumes, tmp_path):
    one_volume(volumes, max_mb=0)
    assert recycle_bin.trash_problem(str(tmp_path)) is None


def test_failed_detection_reports_nothing(monkeypatch):
    def fail(path):
        raise OSError("GetVolumePathNameW")
    monkeypatch.setattr(recycle_bin, "_volume_of", fail)
    assert recycle_bin.trash_problem("C:\\a.cbr") is None


def test_count_reads_each_volume_once(volumes, tmp_path, monkeypatch):
    one_volume(volumes, type=recycle_bin.DRIVE_REMOVABLE)
    calls = []
    real = recycle_bin._volume_problem
    monkeypatch.setattr(recycle_bin, "_volume_problem", lambda v: calls.append(v) or real(v))
    paths = [make_file(tmp_path, 1)] * 5
    assert recycle_bin.trash_problem_count(paths) == 5
    assert calls == ["V:\\"]
    assert recycle_bin.trash_problem_count([]) == 0


def test_warning_texts(monkeypatch):
    import modules.qt.localization as localization
    seen = []
    monkeypatch.setattr(localization, "_", lambda key, **kw: seen.append((key, kw)) or key)
    assert recycle_bin.warning_text(None) == ""
    assert recycle_bin.warning_text(("no_bin",)) == "messages.trash.permanent_no_bin"
    assert recycle_bin.warning_text(("disabled",)) == "messages.trash.permanent_disabled"
    assert recycle_bin.warning_text(("too_large", 3, 2)) == "messages.trash.permanent_too_large"
    assert set(seen[-1][1]) == {"size", "max_size"}


def test_real_system_drive_is_read_without_error():
    """Sur la machine : le disque système a une Corbeille, la détection ne
    lève jamais d'exception."""
    import os
    assert recycle_bin.trash_problem(os.environ.get("SystemRoot", "C:\\Windows")) in (
        None, ("disabled",))
