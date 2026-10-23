"""Écritures de la Bibliothèque dans les fichiers de l'utilisateur
(library_window.LibraryWindow) :

- _rewrite_comicinfo_in_cbz (édition du ComicInfo.xml, import ComicVine) :
  seul le ComicInfo.xml de la racine est remplacé ; annexes, pages
  homonymes et commentaire d'archive gardés ; un CBZ écrit qui échoue à son
  contrôle, ou un fichier traité par un lot, laisse l'original intact ;
- _convert_file_to_cbz (CBR/CB7/CBT → CBZ) : CBZ écrit avec toutes les pages
  lues ; rien n'est écrit si un CBZ du même nom existe ou si des fichiers de
  l'archive sont illisibles ; l'original n'est supprimé qu'à la demande et
  après relecture du CBZ ; la base n'est mise à jour que si c'est toujours
  celle de la fenêtre.

library_window.py importe PySide6 : les méthodes sont extraites par AST
(tests/ast_loader.load_methods) ; chargeur, fenêtres et base sont remplacés.
"""
import io
import os
import sys
import types
import zipfile

import pytest
from PIL import Image

from modules.qt import utils
from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import create_entry

import ast_loader
from fakes import Signal as _Signal, tr as _tr


Base =ast_loader.load_methods("library_window.py", "LibraryWindow",
                               {"_rewrite_comicinfo_in_cbz", "_convert_file_to_cbz"},
                               os=os, _=_tr, _wt=_tr)


class FakeLoadWorker:
    created = []

    def __init__(self, paths, multi=False):
        self.paths = paths
        self.error_text_fn = None
        for name in ("progress", "load_finished", "error", "cancelled", "need_ext_dialog"):
            setattr(self, name, _Signal())
        FakeLoadWorker.created.append(self)

    def start(self):
        pass

    def wait(self):                                       # utils.dispose_qthread
        pass

    def deleteLater(self):
        pass


class Dialogs:
    """Fenêtres remplacées : erreurs affichées, question « Supprimer
    l'original ? » (réponse donnée par le test)."""

    def __init__(self):
        self.errors, self.questions, self.claims_refused = [], [], False

    def save_success(self, parent, *args, on_done=None, **kwargs):
        self.questions.append(on_done)

    def error_dialog(self, parent, title_fn, message_fn, **kwargs):
        self.errors.append(message_fn())
        return types.SimpleNamespace(show_nonmodal=lambda: None)

    def claim(self, parent, paths):
        if self.claims_refused:
            return None
        return types.SimpleNamespace(release_all=lambda: None)


@pytest.fixture
def dialogs(monkeypatch):
    FakeLoadWorker.created = []
    d = Dialogs()
    trash = []
    fo = types.ModuleType("modules.qt.file_operations_qt")
    fo.SaveSuccessDialog = d.save_success
    fo._safe_delete = lambda path: (trash.append(os.path.basename(path)), os.remove(path))
    fo._claim_files_for_write = d.claim
    loader = types.ModuleType("modules.qt.archive_loader")
    loader.LoadWorker = FakeLoadWorker
    loader.ExtensionCorrectionDialog = None
    dq = types.ModuleType("modules.qt.dialogs_qt")
    dq.ErrorDialog = d.error_dialog
    for name, module in (("modules.qt.file_operations_qt", fo),
                         ("modules.qt.archive_loader", loader), ("modules.qt.dialogs_qt", dq)):
        monkeypatch.setitem(sys.modules, name, module)
    import modules.qt.config_manager as config_manager
    monkeypatch.setattr(config_manager, "get_config_manager",
                        lambda: types.SimpleNamespace(get_zip_compression_level=lambda: 0))
    d.trash = trash
    return d


class FakeDb:
    def __init__(self):
        self.reindexed, self.removed = [], []

    def reindex_files(self, paths):
        self.reindexed += [os.path.basename(p) for p in paths]

    def remove_by_id(self, row_id):
        self.removed.append(row_id)


class Library(Base):
    def __init__(self, dialogs):
        self._db = FakeDb()
        self._convert_worker = None
        self._dialogs = dialogs
        self.refreshed = []

    def _show_error(self, message_fn, play_sound=True):
        self._dialogs.errors.append(message_fn())

    def _refresh_row_after_convert(self, old_id, new_path):
        self.refreshed.append((old_id, os.path.basename(new_path)))


def png(color):
    buf = io.BytesIO()
    Image.new("RGB", (6, 4), color).save(buf, "PNG")
    return buf.getvalue()


def zip_members(path):
    with zipfile.ZipFile(path) as zf:
        return [(i.filename, zf.read(i)) for i in zf.infolist()], zf.comment


# ---------------------------------------------------------------------------
# Réécriture du ComicInfo.xml
# ---------------------------------------------------------------------------

def make_cbz(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", b"<ancien/>")
        zf.writestr("p01.png", png("red"))
        zf.writestr("p01.png", png("blue"))              # homonyme
        zf.writestr("ComicInfo_0002.xml", b"<annexe/>")
        zf.writestr("Ch1/ComicInfo.xml", b"<dossier/>")
        zf.comment = b'{"ComicBookInfo/1.0": {}}'
    return str(path)


@pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")
def test_only_the_root_comicinfo_is_replaced(tmp_path, dialogs):
    path = make_cbz(tmp_path / "Aaaa.cbz")
    assert Library(dialogs)._rewrite_comicinfo_in_cbz(path, "ComicInfo.xml", b"<nouveau/>") is True
    members, comment = zip_members(path)
    assert members == [("ComicInfo.xml", b"<nouveau/>"), ("p01.png", png("red")),
                       ("p01.png", png("blue")), ("ComicInfo_0002.xml", b"<annexe/>"),
                       ("Ch1/ComicInfo.xml", b"<dossier/>")]
    assert comment == b'{"ComicBookInfo/1.0": {}}'
    assert dialogs.errors == []


@pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")
def test_failed_check_keeps_the_original(tmp_path, dialogs, monkeypatch):
    path = make_cbz(tmp_path / "Aaaa.cbz")
    before = open(path, "rb").read()

    def fail(tmp, count, shown_path=None):
        raise utils.WrittenFileCheckError(shown_path or tmp, detail="structure")
    monkeypatch.setattr(utils, "check_written_cbz_structure", fail)
    assert Library(dialogs)._rewrite_comicinfo_in_cbz(path, "ComicInfo.xml", b"<n/>") is False
    assert open(path, "rb").read() == before
    assert sorted(os.listdir(tmp_path)) == ["Aaaa.cbz"]
    assert len(dialogs.errors) == 1


@pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")
def test_file_in_a_running_batch_is_not_rewritten(tmp_path, dialogs):
    path = make_cbz(tmp_path / "Aaaa.cbz")
    before = open(path, "rb").read()
    dialogs.claims_refused = True
    assert Library(dialogs)._rewrite_comicinfo_in_cbz(path, "ComicInfo.xml", b"<n/>") is False
    assert open(path, "rb").read() == before


# ---------------------------------------------------------------------------
# Conversion en CBZ
# ---------------------------------------------------------------------------

def start_conversion(tmp_path, dialogs):
    source = tmp_path / "Aaaa.cbr"
    source.write_bytes(b"Rar! archive source")
    library, done = Library(dialogs), []
    library._convert_file_to_cbz(str(source), 7, on_done=done.append)
    [worker] = FakeLoadWorker.created
    return library, worker, source, done


def pages():
    return [create_entry("p01.png", png("red"), IMAGE_EXTS),
            create_entry("p02.png", png("blue"), IMAGE_EXTS)]


def test_converted_cbz_has_every_page_and_original_deleted_on_request(tmp_path, dialogs):
    library, worker, source, done = start_conversion(tmp_path, dialogs)
    worker.load_finished.emit(pages(), [], str(source))
    members, _comment = zip_members(tmp_path / "Aaaa.cbz")
    assert members == [("p01.png", png("red")), ("p02.png", png("blue"))]
    [answer] = dialogs.questions
    answer(True)                                          # « Supprimer l'original ? » : Oui
    assert dialogs.trash == ["Aaaa.cbr"]
    assert library._db.reindexed == ["Aaaa.cbz"] and library._db.removed == [7]
    assert library.refreshed == [(7, "Aaaa.cbz")]
    assert done == [str(tmp_path / "Aaaa.cbz")]


def test_original_kept_when_the_user_says_no(tmp_path, dialogs):
    library, worker, source, done = start_conversion(tmp_path, dialogs)
    worker.load_finished.emit(pages(), [], str(source))
    dialogs.questions[0](False)
    assert source.exists() and dialogs.trash == []
    assert library._db.removed == [] and library.refreshed == [(None, "Aaaa.cbz")]


def test_original_kept_when_the_cbz_does_not_reread(tmp_path, dialogs, monkeypatch):
    def fail(path, expected_count=None, shown_path=None):
        raise utils.WrittenFileCheckError(path, detail="relecture")
    # Remplacée avant le lancement : la conversion l'importe en commençant
    monkeypatch.setattr(utils, "verify_written_cbz", fail)
    library, worker, source, _done = start_conversion(tmp_path, dialogs)
    worker.load_finished.emit(pages(), [], str(source))
    dialogs.questions[0](True)
    assert source.exists() and dialogs.trash == []
    assert len(dialogs.errors) == 1
    assert library._db.removed == []


def test_nothing_written_when_a_cbz_of_that_name_exists(tmp_path, dialogs):
    library, worker, source, done = start_conversion(tmp_path, dialogs)
    (tmp_path / "Aaaa.cbz").write_bytes(b"CBZ existant")
    worker.load_finished.emit(pages(), [], str(source))
    assert (tmp_path / "Aaaa.cbz").read_bytes() == b"CBZ existant"
    assert dialogs.questions == [] and done == [None]


def test_nothing_written_when_files_of_the_archive_are_unreadable(tmp_path, dialogs):
    library, worker, source, done = start_conversion(tmp_path, dialogs)
    worker.load_finished.emit(pages(), ["info.nfo: CRC faux"], str(source))
    assert not (tmp_path / "Aaaa.cbz").exists()
    assert dialogs.questions == [] and done == [None]
    assert len(dialogs.errors) == 1


def test_page_without_data_is_never_written_and_nothing_replaces_anything(tmp_path, dialogs):
    library, worker, source, done = start_conversion(tmp_path, dialogs)
    unread = create_entry("p02.png", None, IMAGE_EXTS)
    worker.load_finished.emit([pages()[0], unread], [], str(source))
    assert not (tmp_path / "Aaaa.cbz").exists()
    assert sorted(os.listdir(tmp_path)) == ["Aaaa.cbr"]
    assert done == [None]


def test_database_replaced_meanwhile_is_left_alone(tmp_path, dialogs):
    library, worker, source, done = start_conversion(tmp_path, dialogs)
    worker.load_finished.emit(pages(), [], str(source))
    old_db = library._db
    library._db = FakeDb()                                # autre base ouverte entre-temps
    dialogs.questions[0](True)
    assert old_db.reindexed == [] and library._db.reindexed == []
    assert library.refreshed == []
    assert dialogs.trash == ["Aaaa.cbr"]                  # le fichier, lui, est converti


def test_second_conversion_while_one_is_running_is_refused(tmp_path, dialogs):
    library, _worker, source, _done = start_conversion(tmp_path, dialogs)
    done = []
    library._convert_file_to_cbz(str(source), 7, on_done=done.append)
    assert done == [None]
    assert len(FakeLoadWorker.created) == 1
    assert len(dialogs.errors) == 1
