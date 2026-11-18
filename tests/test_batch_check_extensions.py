"""Lot « Vérifier les extensions » (batch_dialogs_qt) : choix des fichiers,
vérification dans le thread du lot, renommage après validation, fichiers
ouverts dans un panneau, suivi des fichiers renommés, journal.

batch_dialogs_qt.py importe PySide6 : ses fonctions sont extraites par
tests/ast_loader.py ; panneaux, configuration et signaux sont remplacés par
des faux. La détection elle-même (misnamed_archives.check_extension) est
testée par test_misnamed_archives.py.
"""
import io
import os
import sys
import tarfile
import types
import zipfile

import pytest

import ast_loader
import modules.qt
from modules.qt import recent_files
from modules.qt.misnamed_archives import check_extension
from modules.qt.utils import walk_without_links

MODULE = "batch_dialogs_qt.py"


def fake_wt(key, **params):
    return key + (f" {params}" if params else "")


def make_zip(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("p01.jpg", b"A")
    return str(path)


def make_tgz(path):
    with tarfile.open(path, "w:gz") as tf:
        info = tarfile.TarInfo("p01.jpg")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"A"))
    return str(path)


def make_pdf(path):
    path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    return str(path)


# ---------------------------------------------------------------------------
# Fichiers pris par le lot
# ---------------------------------------------------------------------------

def test_files_of_checked_extensions_are_taken_in_subfolders(tmp_path):
    ns = ast_loader.load(MODULE, {"extension_check_files"}, os=os,
                         walk_without_links=walk_without_links)
    (tmp_path / "sous").mkdir()
    for name in ("b.cbz", "a.PDF", "image.jpg", "notes.txt", "sous/c.tar.gz", "sous/d.rar"):
        (tmp_path / name).write_bytes(b"x")
    found = ns["extension_check_files"]([str(tmp_path)])
    assert [os.path.basename(f) for f in found] == ["a.PDF", "b.cbz", "c.tar.gz", "d.rar"]


# ---------------------------------------------------------------------------
# Vérification (thread du lot)
# ---------------------------------------------------------------------------

class FakeSignal:
    def __init__(self):
        self.sent = []

    def emit(self, value):
        self.sent.append(value)


class FakeSignals:
    def __init__(self):
        self.update_filename = FakeSignal()
        self.update_progress = FakeSignal()


class Stop(BaseException):
    pass


def load_check(stop_at=None):
    calls = [0]

    def raise_if_stopped():
        calls[0] += 1
        if stop_at is not None and calls[0] > stop_at:
            raise Stop()

    return ast_loader.load(MODULE, {"_check_extensions_of", "_EXTENSION_PROGRESS_INTERVAL"},
                           os=os, _=fake_wt, _raise_if_batch_stopped=raise_if_stopped)


def test_check_sorts_well_named_misnamed_and_unknown_files(tmp_path):
    files = [make_zip(tmp_path / "a.cbz"), make_zip(tmp_path / "b.cbr"),
             make_pdf(tmp_path / "c.cbz"), str(tmp_path / "d.cb7")]
    (tmp_path / "d.cb7").write_bytes(b"ni archive ni pdf")
    found, unknown, well_named, handled = [], [], [0], [0]
    signals = FakeSignals()
    load_check()["_check_extensions_of"](files, found, unknown, well_named, handled, signals)
    assert [(os.path.basename(c.path), c.new_extension) for c in found] == [
        ("b.cbr", ".cbz"), ("c.cbz", ".pdf")]
    assert unknown == [files[3]]
    assert (well_named[0], handled[0]) == (1, 4)
    # Progression : fonctions résolues par la fenêtre, dernier fichier compris
    assert signals.update_filename.sent[-1] == "d.cb7"
    assert all(callable(text) for text in signals.update_progress.sent)


def test_cancelled_check_counts_only_the_files_checked(tmp_path):
    files = [make_zip(tmp_path / f"{n}.cbr") for n in "abc"]
    found, unknown, well_named, handled = [], [], [0], [0]
    with pytest.raises(Stop):
        load_check(stop_at=2)["_check_extensions_of"](
            files, found, unknown, well_named, handled, FakeSignals())
    assert handled[0] == 2 and len(found) == 2


# ---------------------------------------------------------------------------
# Renommage après validation
# ---------------------------------------------------------------------------

@pytest.fixture
def rename():
    return ast_loader.load(MODULE, {"_rename_checked_files"}, os=os, _wt=fake_wt,
                           _failure_text=lambda e: str(e))["_rename_checked_files"]


def test_misnamed_files_are_renamed_without_overwriting(tmp_path, rename):
    checks = [check_extension(make_zip(tmp_path / "a.cbr")),
              check_extension(make_zip(tmp_path / "b.tar.gz"))]
    (tmp_path / "a.cbz").write_bytes(b"autre BD")
    errors = []
    renamed = rename(checks, [], errors)
    assert [os.path.basename(new) for _check, new in renamed] == ["a (1).cbz", "b.zip"]
    assert errors == [] and (tmp_path / "a.cbz").read_bytes() == b"autre BD"


def test_file_changed_or_gone_since_the_check_is_left_as_is(tmp_path, rename):
    changed = check_extension(make_zip(tmp_path / "a.cbr"))
    gone = check_extension(make_zip(tmp_path / "b.cbr"))
    os.remove(tmp_path / "a.cbr")
    make_tgz(tmp_path / "a.cbr")      # réécrit dans un autre format entre-temps
    os.remove(tmp_path / "b.cbr")
    errors = []
    assert rename([changed, gone], [], errors) == []
    assert errors == ["a.cbr: messages.errors.batch_extension_changed",
                      "b.cbr: messages.errors.batch_extension_changed"]
    assert sorted(os.listdir(tmp_path)) == ["a.cbr"]


def test_file_in_use_is_not_renamed(tmp_path, rename):
    check = check_extension(make_zip(tmp_path / "a.cbr"))
    errors = []
    assert rename([check], [check.path], errors) == []
    assert errors == ["a.cbr: dialogs.file_picker.file_in_use {'name': 'a.cbr'}"]
    assert sorted(os.listdir(tmp_path)) == ["a.cbr"]


def test_only_checked_files_are_renamed_the_others_are_left_as_is(tmp_path):
    split = ast_loader.load(MODULE, {"_split_chosen"})["_split_chosen"]
    checks = [check_extension(make_zip(tmp_path / f"{n}.cbr")) for n in "abc"]
    to_rename, kept = split(checks, {checks[0].path, checks[2].path})
    assert [c.path for c in to_rename] == [checks[0].path, checks[2].path]
    assert [c.path for c in kept] == [checks[1].path]
    # Sans liste de fichiers cochés : tous renommés
    assert split(checks, None) == (checks, [])
    # Aucune case cochée : aucun renommage
    assert split(checks, set()) == ([], checks)


# ---------------------------------------------------------------------------
# Fichiers ouverts dans un panneau
# ---------------------------------------------------------------------------

class FakeState:
    def __init__(self, current_file, busy=False):
        self.current_file = current_file
        self.busy = busy

    def operation_running(self):
        return self.busy


class FakePanel:
    def __init__(self, current_file, busy=False, loading=()):
        self._state = FakeState(current_file, busy)
        self.loading = list(loading)
        self.titles = 0
        self.status = 0

    def _files_in_use(self):
        files = [self._state.current_file] if self._state.current_file else []
        return files + self.loading

    def _refresh_title(self):
        self.titles += 1

    def _update_status_bar(self):
        self.status += 1


def files_in_use_except_documents(panels):
    ns = ast_loader.load(MODULE, {"_files_in_use_except_documents"})
    return ns["_files_in_use_except_documents"](panels)


def test_document_of_an_idle_panel_may_be_renamed():
    assert files_in_use_except_documents([FakePanel("C:\\a.cbr")]) == []


def test_document_of_a_busy_or_loading_panel_stays_in_use():
    assert files_in_use_except_documents([FakePanel("C:\\a.cbr", busy=True)]) == ["C:\\a.cbr"]
    assert files_in_use_except_documents(
        [FakePanel("C:\\a.cbr", loading=["C:\\a.cbr"])]) == ["C:\\a.cbr"]


class FakeConfig:
    def __init__(self, bookmarks):
        self.bookmarks = dict(bookmarks)

    def get_bookmark(self, path):
        return self.bookmarks.get(path)

    def remove_bookmark(self, path, save=True):
        self.bookmarks.pop(path, None)

    def set_bookmark(self, path, page, save=True):
        self.bookmarks[path] = page


def test_renamed_file_is_followed_by_panels_recents_and_bookmarks(tmp_path, monkeypatch):
    old, new = str(tmp_path / "a.cbr"), str(tmp_path / "a.cbz")
    moved = []
    fake_recent = types.SimpleNamespace(rename_in_recent_files=lambda o, n: moved.append((o, n)))
    monkeypatch.setattr(modules.qt, "recent_files", fake_recent)
    monkeypatch.setitem(sys.modules, "modules.qt.recent_files", fake_recent)
    config = FakeConfig({old: 7})
    synced = []
    ns = ast_loader.load(MODULE, {"_follow_renamed_files"},
                         get_config_manager=lambda: config)
    panel, other = FakePanel(old), FakePanel(str(tmp_path / "b.cbz"))
    panel._main_window = types.SimpleNamespace(_sync_recent_menus=lambda: synced.append(1))
    check = types.SimpleNamespace(path=old)
    ns["_follow_renamed_files"]([panel, other], [(check, new)])
    assert panel._state.current_file == new and (panel.titles, panel.status) == (1, 1)
    assert other._state.current_file == str(tmp_path / "b.cbz") and other.titles == 0
    assert moved == [(old, new)] and config.bookmarks == {new: 7} and synced == [1]


def test_recent_files_keep_their_rank_after_a_rename(tmp_path, monkeypatch):
    a, b, c = (str(tmp_path / n) for n in ("a.cbr", "b.cbz", "c.cbz"))
    saved = []
    config = types.SimpleNamespace(get_recent_files=lambda: [b, a, c],
                                   set_recent_files=lambda files: saved.append(files))
    monkeypatch.setattr(recent_files, "get_config_manager", lambda: config)
    recent_files.rename_in_recent_files(a.upper(), str(tmp_path / "a.cbz"))
    assert saved == [[b, str(tmp_path / "a.cbz"), c]]


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

def test_log_tells_counts_and_lists_every_file(tmp_path):
    ns = ast_loader.load(MODULE, {"_write_extensions_log", "log_line", "log_title"},
                         os=os, _wt=fake_wt, log_text=lambda t: t,
                         unique_log_path=lambda d, kind, now: os.path.join(d, f"Log_{kind}.txt"))
    renamed_check = check_extension(make_zip(tmp_path / "a.tar.gz"))
    kept_check = check_extension(make_zip(tmp_path / "b.cbr"))
    data = {"directory": str(tmp_path), "checked": 5, "ignored_count": 3, "well_named": 1}
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    path = ns["_write_extensions_log"](
        {"get_mosaicview_temp_dir": lambda: str(log_dir)}, data,
        [(renamed_check, str(tmp_path / "a.zip"))], [kept_check], [str(tmp_path / "c.cbz")],
        ["d.cbr: erreur"])
    text = open(path, encoding="utf-8").read()
    for expected in ("title_extensions", "files_handled {'count': 5}",
                     "files_ignored {'count': 3}", "well_named {'count': 1}",
                     "renamed_kind {'kind': 'TAR.GZ→ZIP', 'count': 1}",
                     "not_renamed {'count': 1}", "unknown_format {'count': 1}",
                     "errors {'count': 1}",
                     f"  - {renamed_check.path} → a.zip",
                     f"  - {kept_check.path} → .cbz",
                     f"  - {tmp_path / 'c.cbz'}",
                     "  - d.cbr: erreur"):
        assert expected in text, expected
