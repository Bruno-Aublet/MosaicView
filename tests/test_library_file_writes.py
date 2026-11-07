"""Écritures de la Bibliothèque dans les fichiers de l'utilisateur
(library_window.LibraryWindow) :

- _rewrite_comicinfo_in_cbz (édition du ComicInfo.xml, import ComicVine) :
  seul le ComicInfo.xml de la BD est remplacé, à sa place ; un autre à la
  racine est gardé en annexe ; annexes, pages homonymes et commentaire
  d'archive gardés ; un CBZ écrit qui échoue à son contrôle, un fichier traité
  par un lot ou une écriture arrêtée laisse l'original intact ;
- _convert_file_to_cbz (CBR/CB7/CBT → CBZ) : CBZ écrit avec toutes les pages
  lues ; question avant d'écrire une page altérée ; nom déjà pris remplacé par
  le suivant de libre ; rien n'est écrit si des fichiers de l'archive sont
  illisibles ; extension corrigée suivie par la base ; l'original n'est supprimé
  qu'à la demande et après relecture du CBZ ; la base n'est mise à jour que si
  c'est toujours celle de la fenêtre ; texte rouge et commandes grisées de la
  lecture à la fin de l'écriture ;
- _run_file_task, _FileTaskWorker : une écriture à la fois hors du thread de
  l'interface, la suivante démarre même si la suite de la précédente échoue ;
- un seul éditeur de ComicInfo.xml, commande grisée tant qu'il est ouvert,
  tableau mis à jour avec le tri suspendu.

library_window.py importe PySide6 : les méthodes sont extraites par AST
(tests/ast_loader.load_methods) ; chargeur, fenêtres et base sont remplacés. Dans
la plupart des tests, les écritures s'exécutent aussitôt (Library._run_file_task).
"""
import io
import os
import sys
import threading
import types
import zipfile

import pytest
from PIL import Image

from modules.qt import batch_claims, utils
from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import create_entry

import ast_loader
from fakes import Signal as _Signal, tr as _tr


_STOP = ast_loader.load("library_window.py", {"_FileTaskStopped", "_stop_if_asked"})

Base =ast_loader.load_methods("library_window.py", "LibraryWindow",
                               {"_rewrite_comicinfo_in_cbz", "_convert_file_to_cbz",
                                "_open_comicinfo_editor", "_bring_comicinfo_editor_to_front",
                                "_comicinfo_editor_open", "_on_comicinfo_editor_closed"},
                               os=os, threading=threading, _=_tr, _wt=_tr,
                               _ALL_COLUMNS=[], Qt=types.SimpleNamespace(UserRole=0),
                               _FileTaskStopped=_STOP["_FileTaskStopped"],
                               _stop_if_asked=_STOP["_stop_if_asked"])


class FakeLoadWorker:
    created = []

    def __init__(self, paths, multi=False):
        self.paths = paths
        self.error_text_fn = None
        self.ext_results = []
        for name in ("progress", "load_finished", "error", "cancelled", "need_ext_dialog",
                     "file_renamed"):
            setattr(self, name, _Signal())
        FakeLoadWorker.created.append(self)

    def start(self):
        pass

    def set_ext_result(self, result):
        self.ext_results.append(result)

    def wait(self):                                       # utils.dispose_qthread
        pass

    def deleteLater(self):
        pass


class Dialogs:
    """Fenêtres remplacées : erreurs affichées, question « Supprimer
    l'original ? » (réponse donnée par le test)."""

    def __init__(self):
        self.errors, self.questions, self.claims_refused = [], [], False
        self.altered_answer, self.altered_asked = True, []

    def save_success(self, parent, *args, on_done=None, **kwargs):
        self.questions.append(on_done)

    def confirm_altered(self, parent, on_done, entries):
        self.altered_asked.append(entries)
        on_done(self.altered_answer)

    def error_dialog(self, parent, title_fn, message_fn, **kwargs):
        self.errors.append(message_fn())
        return types.SimpleNamespace(show_nonmodal=lambda: None)

    def claim(self, parent, paths):
        if self.claims_refused:
            return None
        claims = batch_claims.BatchClaims()
        for path in paths:
            assert claims.claim(path)
        return claims


@pytest.fixture
def dialogs(monkeypatch):
    FakeLoadWorker.created = []
    monkeypatch.setattr(batch_claims, "_owners", {})
    d = Dialogs()
    trash = []
    fo = types.ModuleType("modules.qt.file_operations_qt")
    fo.SaveSuccessDialog = d.save_success
    fo._claim_files_for_write = d.claim
    fo._confirm_altered_pages_qt = d.confirm_altered
    monkeypatch.setitem(sys.modules, "shiboken6", types.SimpleNamespace(isValid=lambda w: True))
    # Corbeille (converted_cbz passe par utils.send_to_trash) : jamais la vraie
    monkeypatch.setattr(utils, "send_to_trash",
                        lambda path: (trash.append(os.path.basename(path)), os.remove(path)))
    loader = types.ModuleType("modules.qt.archive_loader")
    loader.LoadWorker = FakeLoadWorker
    loader.ExtensionCorrectionDialog = None
    # Vraie limite de taille des membres (archive_loader importe Qt) ;
    # abaissée par un test dans l'espace de noms de la fonction extraite
    from modules.qt.archive_names import MAX_MEMBER_SIZE
    loader._check_member_size = ast_loader.load("archive_loader.py", {"_check_member_size"},
                                                MAX_MEMBER_SIZE=MAX_MEMBER_SIZE)["_check_member_size"]
    dq = types.ModuleType("modules.qt.dialogs_qt")
    dq.ErrorDialog = d.error_dialog
    for name, module in (("modules.qt.file_operations_qt", fo),
                         ("modules.qt.archive_loader", loader), ("modules.qt.dialogs_qt", dq)):
        monkeypatch.setitem(sys.modules, name, module)
    import modules.qt.config_manager as config_manager
    monkeypatch.setattr(config_manager, "get_config_manager",
                        lambda: types.SimpleNamespace(get_zip_compression_level=lambda: 0))
    d.trash = trash
    d.loader = loader
    return d


class FakeDb:
    def __init__(self):
        self.reindexed, self.removed = [], []

    def reindex_files(self, paths):
        self.reindexed += [os.path.basename(p) for p in paths]

    def remove_by_id(self, row_id):
        self.removed.append(row_id)

    def get_by_filepath(self, path):
        return {"id": 70}


class Library(Base):
    stop_tasks = False                # écriture arrêtée (fermeture de l'application)

    def __init__(self, dialogs):
        self._db = FakeDb()
        self._convert_worker = None
        self._convert_percent = None
        self._comicinfo_editor = None
        self._dialogs = dialogs
        self.refreshed = []
        self.sounds = []
        self.commands_updated = 0

    def _refresh_file_text(self):
        pass

    def _update_file_commands(self):
        self.commands_updated += 1

    def _run_file_task(self, task, on_finished, stop):
        if self.stop_tasks:
            stop.set()
        try:
            result = task()
        except Exception as e:
            on_finished(None, e)
        else:
            on_finished(result, None)

    def _show_error(self, message_fn, play_sound=True):
        self._dialogs.errors.append(message_fn())
        self.sounds.append(play_sound)

    def _refresh_row_after_convert(self, old_id, new_path):
        self.refreshed.append((old_id, os.path.basename(new_path)))


def rewrite(library, path, name, data):
    """_rewrite_comicinfo_in_cbz : rend ce qu'elle transmet à on_done."""
    answers = []
    library._rewrite_comicinfo_in_cbz(path, name, data, on_done=answers.append)
    [written] = answers
    return written


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
    assert rewrite(Library(dialogs), path, "ComicInfo.xml", b"<nouveau/>") is True
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
    assert rewrite(Library(dialogs), path, "ComicInfo.xml", b"<n/>") is False
    assert open(path, "rb").read() == before
    assert sorted(os.listdir(tmp_path)) == ["Aaaa.cbz"]
    assert len(dialogs.errors) == 1


@pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")
def test_member_too_large_keeps_the_original(tmp_path, dialogs, monkeypatch):
    """Membre recopié dont la taille annoncée dépasse MAX_MEMBER_SIZE (limite
    abaissée) : refusé avant d'être lu en mémoire, original intact, erreur
    affichée. Il était chargé entier en mémoire."""
    path = make_cbz(tmp_path / "Aaaa.cbz")
    before = open(path, "rb").read()
    monkeypatch.setitem(dialogs.loader._check_member_size.__globals__, "MAX_MEMBER_SIZE", 20)
    assert rewrite(Library(dialogs), path, "ComicInfo.xml", b"<n/>") is False
    assert open(path, "rb").read() == before
    assert sorted(os.listdir(tmp_path)) == ["Aaaa.cbz"]
    assert len(dialogs.errors) == 1


def test_comicinfo_of_the_pages_folder_is_replaced_in_place(tmp_path, dialogs):
    """Archive faite en compressant un dossier : son ComicInfo.xml, rangé
    avec les pages, est celui de la BD (comic_info.comic_info_member) et il
    est réécrit à sa place ; aucun second ComicInfo.xml n'apparaît à la racine."""
    path = str(tmp_path / "Aaaa.cbz")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Aaaa/p01.png", png("red"))
        zf.writestr("Aaaa/ComicInfo.xml", b"<ancien/>")
        zf.writestr("Aaaa/p02.png", png("blue"))
    assert rewrite(Library(dialogs), path, "Aaaa/ComicInfo.xml", b"<nouveau/>")
    members, _comment = zip_members(path)
    assert members == [("Aaaa/p01.png", png("red")), ("Aaaa/ComicInfo.xml", b"<nouveau/>"),
                       ("Aaaa/p02.png", png("blue"))]


def test_damaged_comicinfo_is_kept_next_to_the_new_one(tmp_path, dialogs, monkeypatch):
    """Ancien ComicInfo.xml lisible seulement réparé : gardé intact sous un
    nom d'annexe libre, et l'utilisateur en est informé."""
    shown = []
    monkeypatch.setattr(sys.modules["modules.qt.dialogs_qt"], "InfoDialog",
                        lambda parent, title, message: ("info", title, message), raising=False)
    damaged = b"<ComicInfo><Title>Aaaa</Title>"
    path = str(tmp_path / "Aaaa.cbz")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", damaged)
        zf.writestr("ComicInfo_0002.xml", b"<annexe/>")
        zf.writestr("p01.png", png("red"))
    library = Library(dialogs)
    library._show_dialog = shown.append
    assert rewrite(library, path, "ComicInfo.xml", b"<ComicInfo/>")
    members, _comment = zip_members(path)
    assert members == [("ComicInfo.xml", b"<ComicInfo/>"), ("ComicInfo_0003.xml", damaged),
                       ("ComicInfo_0002.xml", b"<annexe/>"), ("p01.png", png("red"))]
    assert len(shown) == 1 and "ComicInfo_0003.xml" in shown[0][2]()


@pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")
def test_file_in_a_running_batch_is_not_rewritten(tmp_path, dialogs):
    path = make_cbz(tmp_path / "Aaaa.cbz")
    before = open(path, "rb").read()
    dialogs.claims_refused = True
    assert rewrite(Library(dialogs), path, "ComicInfo.xml", b"<n/>") is False
    assert open(path, "rb").read() == before


@pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")
def test_second_comicinfo_of_the_root_is_kept_as_an_annex(tmp_path, dialogs):
    """Comme à l'ouverture dans un panneau : une BD n'a qu'un ComicInfo.xml, le
    suivant reste dans l'archive sous un nom d'annexe. Il était supprimé."""
    path = str(tmp_path / "Aaaa.cbz")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", b"<premier/>")
        zf.writestr("ComicInfo.xml", b"<second/>")
        zf.writestr("p01.png", png("red"))
    assert rewrite(Library(dialogs), path, "ComicInfo.xml", b"<nouveau/>")
    members, _comment = zip_members(path)
    assert members == [("ComicInfo.xml", b"<nouveau/>"), ("ComicInfo_0002.xml", b"<second/>"),
                       ("p01.png", png("red"))]


@pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")
def test_stopped_write_keeps_the_original_and_shows_nothing(tmp_path, dialogs):
    """Fermeture de l'application pendant l'écriture."""
    path = make_cbz(tmp_path / "Aaaa.cbz")
    before = open(path, "rb").read()
    library = Library(dialogs)
    library.stop_tasks = True
    assert rewrite(library, path, "ComicInfo.xml", b"<n/>") is False
    assert open(path, "rb").read() == before
    assert sorted(os.listdir(tmp_path)) == ["Aaaa.cbz"]
    assert dialogs.errors == []


def edit_comicinfo(tmp_path, dialogs, monkeypatch):
    """Ouvre l'éditeur (remplacé) sur une archive faite en compressant un dossier.
    Rend le CBZ, la bibliothèque et la fonction appelée quand il enregistre."""
    path = str(tmp_path / "Aaaa.cbz")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Aaaa/ComicInfo.xml", b"<ancien/>")
        zf.writestr("Aaaa/p01.png", png("red"))
    opened = []

    def show_editor(parent, entry, edit_fn, state):
        opened.append(edit_fn)
        return FakeEditor()
    monkeypatch.setitem(sys.modules, "modules.qt.virtual_library_panel", types.SimpleNamespace(
        VirtualLibraryPanel=lambda: types.SimpleNamespace(open_from_file=lambda p: True, state=None)))
    monkeypatch.setitem(sys.modules, "modules.qt.comicinfo_dialog_qt", types.SimpleNamespace(
        show_comicinfo_edit_dialog=show_editor))
    library = Library(dialogs)
    library._db.get_by_filepath = lambda path: None   # pas de tableau à mettre à jour ici
    library._open_comicinfo_editor(path)
    [edit_fn] = opened
    return path, library, edit_fn


def test_editor_rewrites_the_comicinfo_of_the_pages_folder_in_place(tmp_path, dialogs, monkeypatch):
    """L'éditeur rend toujours « ComicInfo.xml » : le fichier serait déplacé à la racine."""
    path, _library, edit_fn = edit_comicinfo(tmp_path, dialogs, monkeypatch)
    edit_fn("ComicInfo.xml", b"<nouveau/>")
    members, _comment = zip_members(path)
    assert members == [("Aaaa/ComicInfo.xml", b"<nouveau/>"), ("Aaaa/p01.png", png("red"))]


class FakeEditor:
    def __init__(self, visible=True):
        self.visible, self.raised = visible, 0
        self.finished = _Signal()

    def isVisible(self):
        return self.visible

    def raise_(self):
        self.raised += 1

    def activateWindow(self):
        pass


def test_open_editor_comes_back_instead_of_a_second_one(tmp_path, dialogs):
    library = Library(dialogs)
    library._comicinfo_editor = editor = FakeEditor()
    library._open_comicinfo_editor(str(tmp_path / "inutile.cbz"))    # ne lit rien
    assert editor.raised == 1 and dialogs.errors == []


def test_closed_editor_does_not_block_a_new_one(tmp_path, dialogs, monkeypatch):
    library = Library(dialogs)
    library._comicinfo_editor = FakeEditor(visible=False)
    assert library._bring_comicinfo_editor_to_front() is False


def test_commands_follow_the_editor_while_it_is_open(tmp_path, dialogs, monkeypatch):
    """La commande Modifier est grisée à l'ouverture, rendue à la fermeture."""
    _path, library, _edit_fn = edit_comicinfo(tmp_path, dialogs, monkeypatch)
    editor = library._comicinfo_editor
    assert library._comicinfo_editor_open() and library.commands_updated == 1
    editor.finished.emit(0)
    assert library._comicinfo_editor is None and not library._comicinfo_editor_open()
    assert library.commands_updated == 2


COMMANDS = ast_loader.load_methods("library_window.py", "LibraryWindow",
                                   {"_can_fetch_metadata", "_can_edit_comicinfo",
                                    "_update_file_commands", "_file_task_running"})


class CommandsLibrary(COMMANDS):
    def __init__(self):
        self._file_worker = self._convert_worker = None
        self.loading, self.editor_open, self.has_comicinfo = False, False, True
        self._btn_fetch_meta = types.SimpleNamespace(setEnabled=self._fetch_enabled)
        self._btn_edit_comicinfo = types.SimpleNamespace(setEnabled=self._edit_enabled)

    def _fetch_enabled(self, value):
        self.fetch_enabled = value

    def _edit_enabled(self, value):
        self.edit_enabled = value

    def _is_loading(self):
        return self.loading

    def _comicinfo_editor_open(self):
        return self.editor_open

    def _selection_has_comicinfo(self):
        return self.has_comicinfo


@pytest.mark.parametrize("change", [
    {"_convert_worker": object()},         # lecture de l'archive
    {"_convert_worker": True},             # après la lecture, jusqu'à la fin de l'écriture
    {"_file_worker": object()},            # écriture
    {"loading": True},                     # chargement de la base
])
def test_every_command_is_greyed_while_a_file_is_being_processed(change):
    library = CommandsLibrary()
    library._update_file_commands()
    assert library.fetch_enabled and library.edit_enabled
    for name, value in change.items():
        setattr(library, name, value)
    library._update_file_commands()
    assert not library.fetch_enabled and not library.edit_enabled


def test_only_the_edit_command_is_greyed_while_the_editor_is_open():
    library = CommandsLibrary()
    library.editor_open = True
    library._update_file_commands()
    assert library.fetch_enabled and not library.edit_enabled


def test_edit_command_needs_a_comicinfo():
    library = CommandsLibrary()
    library.has_comicinfo = False
    library._update_file_commands()
    assert library.fetch_enabled and not library.edit_enabled


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
    monkeypatch.setattr(utils, "verify_written_cbz", fail)
    library, worker, source, _done = start_conversion(tmp_path, dialogs)
    worker.load_finished.emit(pages(), [], str(source))
    dialogs.questions[0](True)
    assert source.exists() and dialogs.trash == []
    assert len(dialogs.errors) == 1
    assert library._db.removed == []


def test_original_kept_when_the_cbz_was_rewritten_before_the_answer(tmp_path, dialogs,
                                                                     monkeypatch):
    """CBZ réécrit ailleurs (autre programme, panneau) pendant que la question
    « Supprimer l'original ? » attendait : original gardé, erreur affichée."""
    # Message de converted_cbz.ConvertedCbzChangedError, traduit à l'affichage
    from modules.qt import localization
    monkeypatch.setattr(localization, "_", _tr)
    library, worker, source, _done = start_conversion(tmp_path, dialogs)
    worker.load_finished.emit(pages(), [], str(source))
    with zipfile.ZipFile(tmp_path / "Aaaa.cbz", "w") as zf:
        zf.writestr("p01.png", png("green"))
        zf.writestr("p02.png", png("green") + b"autre taille")
    dialogs.questions[0](True)
    assert source.exists() and dialogs.trash == []
    assert dialogs.errors == ["messages.errors.converted_cbz_changed [('name', 'Aaaa.cbz'), "
                              "('source', 'Aaaa.cbr')]"]
    assert library._db.removed == [] and library.refreshed == [(None, "Aaaa.cbz")]


def test_name_already_taken_gets_the_next_free_one(tmp_path, dialogs):
    """Comme dans les lots : ni refus ni fichier écrasé."""
    (tmp_path / "Aaaa.cbz").write_bytes(b"CBZ existant")
    _library, worker, source, done = start_conversion(tmp_path, dialogs)
    worker.load_finished.emit(pages(), [], str(source))
    dialogs.questions[0](False)
    assert (tmp_path / "Aaaa.cbz").read_bytes() == b"CBZ existant"
    assert len(zip_members(tmp_path / "Aaaa (1).cbz")[0]) == 2
    assert done == [str(tmp_path / "Aaaa (1).cbz")] and dialogs.errors == []


def test_name_taken_during_the_reading_gets_the_next_free_one(tmp_path, dialogs):
    """Réservé auprès des lots seulement : un autre programme a pu le créer."""
    _library, worker, source, done = start_conversion(tmp_path, dialogs)
    (tmp_path / "Aaaa.cbz").write_bytes(b"CBZ existant")
    worker.load_finished.emit(pages(), [], str(source))
    dialogs.questions[0](False)
    assert (tmp_path / "Aaaa.cbz").read_bytes() == b"CBZ existant"
    assert done == [str(tmp_path / "Aaaa (1).cbz")] and dialogs.errors == []


def test_source_held_by_a_batch_is_refused_before_any_reading(tmp_path, dialogs):
    source = tmp_path / "Aaaa.cbr"
    source.write_bytes(b"Rar! archive source")
    dialogs.claims_refused = True
    library, done = Library(dialogs), []
    library._convert_file_to_cbz(str(source), 7, on_done=done.append)
    assert FakeLoadWorker.created == [] and done == [None]
    assert library._convert_worker is None and library._convert_percent is None


def test_conversion_is_in_progress_from_the_reading_to_the_end_of_the_write(tmp_path, dialogs):
    """Texte rouge et commandes grisées couvrent aussi la lecture, souvent le plus long."""
    library, worker, source, _done = start_conversion(tmp_path, dialogs)
    assert library._convert_worker is worker and library._convert_percent == 0
    assert library.commands_updated == 1
    worker.progress.emit(40)
    assert library._convert_percent == 40
    held = []
    library._run_file_task = lambda task, on_finished, stop: held.append((task, on_finished))
    worker.load_finished.emit(pages(), [], str(source))
    assert library._convert_worker is True and library._convert_percent is None
    assert batch_claims.is_claimed(tmp_path / "Aaaa.cbz")
    task, on_finished = held[0]
    on_finished(task(), None)
    assert library._convert_worker is None and library.commands_updated == 2
    assert not batch_claims.is_claimed(tmp_path / "Aaaa.cbz")
    assert not batch_claims.is_claimed(source)


@pytest.mark.parametrize("end", ["cancelled", "error"])
def test_interrupted_reading_ends_the_conversion(tmp_path, dialogs, end):
    library, worker, source, done = start_conversion(tmp_path, dialogs)
    if end == "cancelled":
        worker.cancelled.emit()
    else:
        worker.error.emit("archive illisible")
    assert library._convert_worker is None and library._convert_percent is None
    assert library.commands_updated == 2 and done == [None]
    assert not batch_claims.is_claimed(source)


def test_unexpected_error_after_the_reading_ends_the_conversion(tmp_path, dialogs):
    """Les commandes resteraient grisées jusqu'à la fermeture de l'application."""
    def fail(parent, on_done, entries):
        raise ValueError("imprévu")
    sys.modules["modules.qt.file_operations_qt"]._confirm_altered_pages_qt = fail
    library, worker, source, _done = start_conversion(tmp_path, dialogs)
    with pytest.raises(ValueError):
        worker.load_finished.emit(pages(), [], str(source))
    assert library._convert_worker is None and library._convert_percent is None
    assert not batch_claims.is_claimed(source)


def test_refused_altered_pages_end_the_conversion(tmp_path, dialogs):
    library, worker, source, _done = start_conversion(tmp_path, dialogs)
    dialogs.altered_answer = False
    worker.load_finished.emit(pages(), [], str(source))
    assert library._convert_worker is None
    assert not batch_claims.is_claimed(tmp_path / "Aaaa.cbz")


def test_extension_correction_uses_free_names(tmp_path, dialogs):
    """Un CBZ mal nommé prend le nom réservé ; un autre format, le suivant de libre."""
    (tmp_path / "Aaaa.cbz").write_bytes(b"pris")
    (tmp_path / "Aaaa.cb7").write_bytes(b"pris")
    _library, worker, _source, _done = start_conversion(tmp_path, dialogs)
    base = str(tmp_path / "Aaaa")
    assert worker.rename_target(base, ".cbz") == base + " (1).cbz"
    assert worker.rename_target(base, ".cb7") == base + " (1).cb7"


def test_extension_question_suspends_the_text_during_the_answer(tmp_path, dialogs, monkeypatch):
    asked = []

    class Question:
        def __init__(self, parent, filepath, detected, declared):
            pass

        def ask_async(self, on_result):
            asked.append(on_result)
    monkeypatch.setitem(sys.modules, "modules.qt.library_dialogs",
                        types.SimpleNamespace(ConversionExtensionDialog=Question))
    library, worker, source, _done = start_conversion(tmp_path, dialogs)
    worker.progress.emit(10)
    worker.need_ext_dialog.emit(str(source), "CB7", "cbr")
    assert library._convert_percent is None
    asked[0]("rename")
    assert library._convert_percent == 0 and worker.ext_results == ["rename"]


def test_cancelled_extension_question_does_not_show_the_text_again(tmp_path, dialogs, monkeypatch):
    asked = []
    monkeypatch.setitem(sys.modules, "modules.qt.library_dialogs", types.SimpleNamespace(
        ConversionExtensionDialog=lambda *args: types.SimpleNamespace(ask_async=asked.append)))
    library, worker, source, _done = start_conversion(tmp_path, dialogs)
    worker.need_ext_dialog.emit(str(source), "CB7", "cbr")
    asked[0](None)
    assert library._convert_percent is None and worker.ext_results == [None]


EXT_TEXTS = ast_loader.load("library_dialogs.py", {"conversion_ext_texts"},
                            _=_tr)["conversion_ext_texts"]


def test_extension_question_of_a_misnamed_cbz_says_nothing_needs_converting():
    message, rename, keep = EXT_TEXTS(".cbz", "Aaaa.cbr", "ZIP", "CBR")
    assert message.startswith("library.convert_ext_already_cbz")
    assert "Aaaa.cbr" in message and "ZIP" in message and "CBR" in message
    assert rename.startswith("buttons.rename_to_ext")
    assert keep == _tr("library.convert_ext_copy")


def test_extension_question_of_another_format_speaks_of_the_conversion():
    message, rename, keep = EXT_TEXTS(".cb7", "Aaaa.cbr", "CB7", "CBR")
    assert message.startswith("library.convert_ext_other")
    assert rename == _tr("library.convert_ext_rename")
    assert keep == _tr("library.convert_ext_keep")


def test_altered_page_asks_first_and_writes_nothing_on_refusal(tmp_path, dialogs):
    """Le CBZ lui donnerait une somme de contrôle neuve."""
    library, worker, source, done = start_conversion(tmp_path, dialogs)
    dialogs.altered_answer = False
    entries = pages()
    worker.load_finished.emit(entries, [], str(source))
    assert dialogs.altered_asked == [entries]
    assert not (tmp_path / "Aaaa.cbz").exists()
    assert dialogs.questions == [] and done == [None]
    assert source.exists()


def test_zip_renamed_to_cbz_by_the_extension_correction_needs_no_conversion(tmp_path, dialogs):
    """Il refusait : le CBZ cible « existait », et la ligne de la base désignait
    un fichier absent."""
    library, worker, source, done = start_conversion(tmp_path, dialogs)
    renamed = tmp_path / "Aaaa.cbz"
    source.rename(renamed)
    worker.file_renamed.emit(str(renamed))
    worker.load_finished.emit(pages(), [], str(renamed))
    assert library._db.removed == [7] and library.refreshed == [(7, "Aaaa.cbz")]
    assert dialogs.errors == [] and dialogs.questions == []
    assert done == [str(renamed)]
    assert renamed.read_bytes() == b"Rar! archive source"  # rien de réécrit


def test_renamed_source_keeps_a_correct_row_whatever_the_answer(tmp_path, dialogs):
    """Source renommée, original gardé : sa ligne suit son nouveau nom."""
    library, worker, source, _done = start_conversion(tmp_path, dialogs)
    renamed = tmp_path / "Aaaa.cb7"
    source.rename(renamed)
    worker.file_renamed.emit(str(renamed))
    worker.load_finished.emit(pages(), [], str(renamed))
    dialogs.questions[0](False)
    assert library._db.removed == [7]
    assert library.refreshed == [(7, "Aaaa.cb7"), (None, "Aaaa.cbz")]


def test_deleted_renamed_source_removes_its_new_row(tmp_path, dialogs):
    library, worker, source, _done = start_conversion(tmp_path, dialogs)
    renamed = tmp_path / "Aaaa.cb7"
    source.rename(renamed)
    worker.file_renamed.emit(str(renamed))
    worker.load_finished.emit(pages(), [], str(renamed))
    dialogs.questions[0](True)
    assert library._db.removed == [7, 70]
    assert dialogs.trash == ["Aaaa.cb7"]


def test_original_kept_when_a_batch_took_it_during_the_question(tmp_path, dialogs):
    library, worker, source, _done = start_conversion(tmp_path, dialogs)
    worker.load_finished.emit(pages(), [], str(source))
    dialogs.claims_refused = True
    dialogs.questions[0](True)
    assert source.exists() and dialogs.trash == [] and library._db.removed == []


def test_second_conversion_is_refused_until_the_write_ends(tmp_path, dialogs):
    library, worker, source, _done = start_conversion(tmp_path, dialogs)
    held = []
    library._run_file_task = lambda task, on_finished, stop: held.append((task, on_finished))
    worker.load_finished.emit(pages(), [], str(source))
    assert library._convert_worker is not None
    task, on_finished = held[0]
    on_finished(task(), None)
    assert library._convert_worker is None and len(dialogs.questions) == 1


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


# ---------------------------------------------------------------------------
# Écritures hors du thread de l'interface
# ---------------------------------------------------------------------------

class FakeTaskWorker:
    started = []

    def __init__(self, task, stop):
        self.task = task
        self.done = types.SimpleNamespace(connect=lambda fn: None)

    def start(self):
        FakeTaskWorker.started.append(self)

    def wait(self):
        pass

    def deleteLater(self):
        pass


@pytest.fixture
def task_library(monkeypatch):
    FakeTaskWorker.started = []
    shown = []
    monkeypatch.setitem(sys.modules, "modules.qt.canvas_overlay_qt", types.SimpleNamespace(
        show_canvas_text=lambda widget, text, holder: shown.append(text),
        hide_canvas_text=lambda widget, holder: shown.append("masque")))

    class TaskLibrary(ast_loader.load_methods(
            "library_window.py", "LibraryWindow",
            {"_run_file_task", "_start_next_file_task", "_on_file_task_done",
             "_refresh_file_text", "_file_task_running"},
            _FileTaskWorker=FakeTaskWorker, _=_tr)):
        _right_panel = _file_overlay_holder = None

        def _update_file_commands(self):
            self.commands_updated += 1

    library = TaskLibrary()
    library._file_worker = library._file_on_finished = library._convert_worker = None
    library._convert_percent = None
    library._file_tasks = []
    library.commands_updated = 0
    library.shown = shown
    return library


def test_red_text_follows_the_conversion_and_the_writes(task_library):
    library = task_library
    library._convert_percent = 40
    library._refresh_file_text()
    assert library.shown[-1] == _tr("labels.converting", percent=40)
    library._run_file_task("a", lambda r, e: None, None)      # l'écriture a la priorité
    assert library.shown[-1] == _tr("library.export_saving")
    library._on_file_task_done(None, None)                    # retour à la lecture
    assert library.shown[-1] == _tr("labels.converting", percent=40)
    library._convert_percent = None
    library._refresh_file_text()
    assert library.shown[-1] == "masque"


def test_commands_are_updated_when_a_write_starts_and_ends(task_library):
    library = task_library
    library._run_file_task("a", lambda r, e: None, None)
    assert library.commands_updated == 1 and library._file_task_running()
    library._on_file_task_done(None, None)
    assert library.commands_updated == 2 and not library._file_task_running()


def test_a_conversion_counts_as_a_file_task(task_library):
    library = task_library
    library._convert_worker = True
    assert library._file_task_running()


def test_file_tasks_run_one_at_a_time_in_order(task_library):
    library, ended = task_library, []
    library._run_file_task("a", lambda r, e: ended.append("a"), None)
    library._run_file_task("b", lambda r, e: ended.append("b"), None)
    assert [w.task for w in FakeTaskWorker.started] == ["a"]
    assert library._file_task_running()
    library._on_file_task_done(None, None)
    assert ended == ["a"] and [w.task for w in FakeTaskWorker.started] == ["a", "b"]
    library._on_file_task_done(None, None)
    assert ended == ["a", "b"] and not library._file_task_running()
    assert library.shown[-1] == "masque"


def test_next_file_task_starts_even_if_the_previous_end_fails(task_library):
    library = task_library

    def fail(_result, _error):
        raise ValueError("suite en erreur")
    library._run_file_task("a", fail, None)
    library._run_file_task("b", lambda r, e: None, None)
    with pytest.raises(ValueError):
        library._on_file_task_done(None, None)
    assert [w.task for w in FakeTaskWorker.started] == ["a", "b"]


def test_file_task_worker_reports_its_result_or_its_error():
    """FileNotFoundError : erreur attendue, rien n'est écrit au journal."""
    class Worker(ast_loader.load_methods("library_window.py", "_FileTaskWorker", {"__init__", "run"})):
        pass

    def run(task):
        emitted = []
        worker = Worker(task, threading.Event())
        worker.done = types.SimpleNamespace(emit=lambda r, e: emitted.append((r, e)))
        worker.run()
        return emitted

    assert run(lambda: 5) == [(5, None)]

    def fail():
        raise FileNotFoundError("absent")
    [(result, error)] = run(fail)
    assert result is None and isinstance(error, FileNotFoundError)


# ---------------------------------------------------------------------------
# Tableau après une conversion
# ---------------------------------------------------------------------------

class SortedTable:
    """Tableau trié : écrire une cellule pendant que le tri est actif ferait
    changer la ligne de place, ses colonnes suivantes tomberaient sur une autre."""

    def __init__(self):
        self.sorting, self.rows = True, []
        self.resorted = None

    def horizontalHeader(self):
        return types.SimpleNamespace(sortIndicatorSection=lambda: 1,
                                     sortIndicatorOrder=lambda: "croissant")

    def setSortingEnabled(self, value):
        self.sorting = value

    def rowCount(self):
        return len(self.rows)

    def item(self, row, column):
        row_id = self.rows[row]
        return types.SimpleNamespace(data=lambda role: row_id)

    def removeRow(self, row):
        self.rows.pop(row)

    def insertRow(self, row):
        self.rows.insert(row, None)

    def setItem(self, row, column, value):
        assert not self.sorting
        self.rows[row] = value

    def sortByColumn(self, column, order):
        self.resorted = (column, order)


def refresh_library(rows):
    class Refresh(ast_loader.load_methods(
            "library_window.py", "LibraryWindow", {"_refresh_row_after_convert"},
            _ALL_COLUMNS=[("cle", "filename")], Qt=types.SimpleNamespace(UserRole=0))):
        pass

    fresh = {"id": 70, "filename": "Aaaa.cbz"}
    library = Refresh()
    library._db = types.SimpleNamespace(get_by_filepath=lambda path: fresh)
    library._visible_cols = ["filename"]
    library._main_rows = rows
    library._rows = rows
    library._filter_active = False
    library._table = SortedTable()
    library._table.rows = [r["id"] for r in rows]
    library._make_cell_item = lambda col, val, row_id: row_id
    return library, fresh


def test_table_is_updated_with_sorting_suspended():
    library, _fresh = refresh_library([{"id": 7}, {"id": 8}])
    library._refresh_row_after_convert(7, "Aaaa.cbz")
    assert library._table.rows == [8, 70]
    assert library._table.sorting and library._table.resorted == (1, "croissant")


def test_preview_list_stays_the_main_list_after_a_conversion():
    """L'aperçu et les menus lisent _rows : la ligne convertie y serait absente,
    et le comic semblerait ne plus avoir de ComicInfo.xml."""
    library, fresh = refresh_library([{"id": 7}, {"id": 8}])
    library._refresh_row_after_convert(7, "Aaaa.cbz")
    assert library._rows is library._main_rows
    assert library._rows == [{"id": 8}, fresh]
