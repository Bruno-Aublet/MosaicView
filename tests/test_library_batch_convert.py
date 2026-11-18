"""« Convertir en CBZ » de la Bibliothèque sur plusieurs fichiers : un lot
par type (CBR, CB7, CBT, PDF, images), enchaînés, base mise à jour après
chacun ; seule l'annulation par l'utilisateur (confirmation, choix du mode,
bouton Annuler pendant un lot) arrête la chaîne ; un lot refusé ou des
fichiers déjà pris par un autre lot la laissent continuer et sont annoncés
dans le résumé ; un seul résumé, à la fin. Une seule
archive CBR/CB7/CBT reste convertie directement ; un seul PDF ou une seule
image passe par le lot de son type, sans confirmation ni suppression, puis
« Supprimer l'original ? ».

library_window.py importe PySide6 : méthodes extraites par AST
(tests/ast_loader.load_methods) ; base, lots et tableau sont remplacés."""
import os
import sys
import types

import pytest

import ast_loader
from modules.qt.batch_claims import BatchClaims

Base =ast_loader.load_methods(
    "library_window.py", "LibraryWindow",
    {"_convertible_selection", "_can_convert_selection", "_convert_by_batches",
     "_index_after_batch", "_action_convert_to_cbz", "_convert_single_by_batch",
     "_batch_directories"},
    class_attrs=("_CONVERTIBLE_EXTS",), os=os)


class FakeDB:
    def __init__(self, paths):
        self.paths = dict(enumerate(paths, 1))
        self.reindexed, self.removed = [], []

    def get_absolute_path(self, comic_id):
        return self.paths.get(comic_id)

    def reindex_files(self, paths):
        self.reindexed += paths

    def remove_by_id(self, comic_id):
        self.removed.append(comic_id)


class Library(Base):
    def __init__(self, paths, selected):
        self._db = FakeDB(paths)
        self.selected = selected
        self._batch_chain_running = False
        self.refreshed, self.converted_one, self.commands = [], [], 0
        self.deletion_offered = []

    def _selected_ids(self):
        return list(self.selected)

    def _is_loading(self):
        return False

    def _file_task_running(self):
        return False

    def _update_file_commands(self):
        self.commands += 1

    def _batch_callbacks(self):
        return {"cb": True}

    def _refresh_row_after_convert(self, old_id, new_path):
        self.refreshed.append((old_id, os.path.basename(new_path)))

    def _convert_file_to_cbz(self, path, old_id):
        self.converted_one.append(os.path.basename(path))

    def _offer_source_deletion(self, db, source_id, source_path, new_path, expected_count,
                               written_signature, source_signature, on_done=None):
        self.deletion_offered.append((source_id, os.path.basename(source_path),
                                      os.path.basename(new_path), expected_count,
                                      written_signature, source_signature))


@pytest.fixture
def batches(monkeypatch):
    """Lots remplacés : chaque appel est noté, sa fin (on_finished) est
    déclenchée par le test."""
    calls = _Calls()

    def launcher(kind):
        def run(parent, files, directory, callbacks, directories=None, on_finished=None):
            calls.append(types.SimpleNamespace(kind=kind, files=files, directory=directory,
                                               directories=directories, finish=on_finished))
        return run

    fake = types.ModuleType("modules.qt.batch_dialogs_qt")
    for kind in ("cbr", "cb7", "cbt", "pdf", "img"):
        setattr(fake, f"batch_convert_{kind}_to_cbz_confirm", launcher(kind))
    fake.batch_convert_image_list = launcher("image")

    def single(parent, path, callbacks, kept, on_finished=None):
        calls.append(types.SimpleNamespace(kind="single", files=[path], kept=kept,
                                           finish=on_finished))
    fake.convert_single_file_to_cbz = single
    fake.BATCH_REFUSED = "refused"
    calls.summaries_shown, calls.overalls = [], []

    def show_chain_summary(parent, summaries, overall=None, title_key=None):
        calls.summaries_shown.append(list(summaries))
        calls.overalls.append(overall)
    fake.show_chain_summary = show_chain_summary
    monkeypatch.setitem(sys.modules, "modules.qt.batch_dialogs_qt", fake)
    return calls


def data(name, **extra):
    """Données du résumé d'un lot rendues à la chaîne (nommées pour les
    retrouver dans le résumé affiché)."""
    return dict({"name": name, "cancelled": False}, **extra)


class _Calls(list):
    """Liste des lots lancés, avec les résumés affichés en fin de chaîne."""


def files(tmp_path, *names):
    paths = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"x")
        paths.append(str(path))
    return paths


def test_one_archive_is_converted_directly(tmp_path, batches):
    lib = Library(files(tmp_path, "Aaaa.cbr"), [1])
    assert lib._can_convert_selection()
    lib._action_convert_to_cbz()
    assert lib.converted_one == ["Aaaa.cbr"] and batches == []


@pytest.mark.parametrize("name", ["Aaaa.pdf", "Aaaa.jpg"])
def test_one_pdf_or_image_is_converted_then_deletion_is_offered(tmp_path, batches, name):
    """PDF ou image seul : lot de son type sans confirmation ni suppression,
    puis « Supprimer l'original ? » comme pour une archive seule."""
    lib = Library(files(tmp_path, name), [1])
    assert lib._can_convert_selection()
    lib._action_convert_to_cbz()
    assert [c.kind for c in batches] == ["single"] and lib.converted_one == []
    assert lib._batch_chain_running and not lib._can_convert_selection()
    cbz = str(tmp_path / "Aaaa.cbz")
    batches[0].kept[cbz] = (3, "signature", "source")      # noté par le lot
    batches[0].finish([cbz], ("pdf", "p"))
    assert not lib._batch_chain_running
    assert lib.deletion_offered == [(1, name, "Aaaa.cbz", 3, "signature", "source")]
    assert batches.summaries_shown == []                    # la question tient lieu de résumé


def test_one_file_that_fails_shows_the_batch_summary(tmp_path, batches):
    lib = Library(files(tmp_path, "Aaaa.pdf"), [1])
    lib._action_convert_to_cbz()
    batches[0].finish([], ("pdf", "erreurs"))
    assert lib.deletion_offered == []
    assert batches.summaries_shown == [[("pdf", "erreurs")]]
    assert not lib._batch_chain_running


def test_protected_pdf_is_converted_without_deletion_question(tmp_path, batches):
    """PDF protégé par un mot de passe propriétaire : jamais supprimé, donc
    absent de `kept` ; le CBZ entre dans la base et le résumé est affiché."""
    lib = Library(files(tmp_path, "Aaaa.pdf"), [1])
    lib._action_convert_to_cbz()
    cbz = tmp_path / "Aaaa.cbz"
    cbz.write_bytes(b"x")
    batches[0].finish([str(cbz)], ("pdf", "p"))
    assert lib.deletion_offered == [] and lib._db.reindexed == [str(cbz)]
    assert batches.summaries_shown == [[("pdf", "p")]]


def test_one_file_refused_does_nothing(tmp_path, batches):
    lib = Library(files(tmp_path, "Aaaa.png"), [1])
    lib._action_convert_to_cbz()
    batches[0].finish(None, "refused")                      # un autre lot le traite
    assert lib.deletion_offered == [] and batches.summaries_shown == []
    assert not lib._batch_chain_running


def test_cbz_and_missing_files_are_ignored(tmp_path, batches):
    paths = files(tmp_path, "Aaaa.cbz", "Bbbb.cbr") + [str(tmp_path / "Cccc.cbr")]
    lib = Library(paths, [1, 2, 3])
    assert [os.path.basename(p) for _i, p in lib._convertible_selection()] == ["Bbbb.cbr"]


def test_several_files_are_chained_by_type(tmp_path, batches):
    paths = files(tmp_path, "Aaaa.jpg", "Bbbb.pdf", "Cccc.cbr", "Dddd.cbr", "Eeee.png")
    lib = Library(paths, [1, 2, 3, 4, 5])
    lib._action_convert_to_cbz()
    assert [c.kind for c in batches] == ["cbr"]            # un lot à la fois
    assert lib._batch_chain_running and not lib._can_convert_selection()
    assert [os.path.basename(p) for p in batches[0].files] == ["Cccc.cbr", "Dddd.cbr"]
    assert batches[0].directory == str(tmp_path)
    batches[0].finish([], ("cbr", data("r")))
    assert [c.kind for c in batches] == ["cbr", "pdf"]
    batches[1].finish([], ("pdf", data("p")))
    assert [c.kind for c in batches] == ["cbr", "pdf", "image"]
    assert [os.path.basename(p) for p in batches[2].files] == ["Aaaa.jpg", "Eeee.png"]
    assert batches.summaries_shown == []                    # rien avant la fin
    batches[2].finish([], ("img", data("i")))
    assert not lib._batch_chain_running
    # Un seul résumé, à la fin de la chaîne, qui regroupe tous les lots
    assert batches.summaries_shown == [[("cbr", data("r")), ("pdf", data("p")),
                                        ("img", data("i"))]]
    assert batches.overalls == [None]                       # rien d'autre à annoncer


def test_a_cancelled_batch_stops_the_chain(tmp_path, batches):
    lib = Library(files(tmp_path, "Aaaa.pdf", "Bbbb.cbr"), [1, 2])
    lib._action_convert_to_cbz()
    batches[0].finish(None, None)                           # confirmation annulée
    assert [c.kind for c in batches] == ["cbr"]
    assert not lib._batch_chain_running
    assert batches.summaries_shown == [[]]                  # aucun lot fait : rien à montrer
    assert batches.overalls == [None]


def test_a_batch_cancelled_after_another_shows_the_finished_one(tmp_path, batches):
    lib = Library(files(tmp_path, "Aaaa.pdf", "Bbbb.cbr"), [1, 2])
    lib._action_convert_to_cbz()
    batches[0].finish([], ("cbr", data("r")))
    batches[1].finish(None, None)                           # lot PDF annulé
    assert batches.summaries_shown == [[("cbr", data("r"))]]
    assert batches.overalls == [{"stopped": True, "busy": 0}]


def test_cancel_button_during_a_batch_stops_the_chain(tmp_path, batches):
    """Bouton Annuler pendant un lot : ce qu'il a fait est gardé, les lots
    suivants ne sont pas lancés, le résumé annonce l'arrêt."""
    lib = Library(files(tmp_path, "Aaaa.pdf", "Bbbb.cbr", "Cccc.png"), [1, 2, 3])
    lib._action_convert_to_cbz()
    batches[0].finish([], ("cbr", data("r", cancelled=True)))
    assert [c.kind for c in batches] == ["cbr"]
    assert not lib._batch_chain_running
    assert batches.summaries_shown == [[("cbr", data("r", cancelled=True))]]
    assert batches.overalls == [{"stopped": True, "busy": 0}]


def test_a_refused_batch_lets_the_chain_go_on(tmp_path, batches):
    """Lot refusé à son démarrage (un autre lot a pris l'un de ses fichiers) :
    les lots suivants sont lancés, ses fichiers annoncés comme laissés de côté."""
    lib = Library(files(tmp_path, "Aaaa.pdf", "Bbbb.cbr", "Cccc.cbr"), [1, 2, 3])
    lib._action_convert_to_cbz()
    batches[0].finish(None, "refused")
    assert [c.kind for c in batches] == ["cbr", "pdf"]
    batches[1].finish([], ("pdf", data("p")))
    assert not lib._batch_chain_running
    assert batches.summaries_shown == [[("pdf", data("p"))]]
    assert batches.overalls == [{"stopped": False, "busy": 2}]


def test_files_taken_by_another_batch_are_left_aside(tmp_path, batches):
    """Fichiers déjà traités par un autre lot (l'autre panneau…) : le lot de
    leur type traite les autres ; un type dont tous les fichiers sont pris
    est sauté. Annoncés dans le résumé."""
    paths = files(tmp_path, "Aaaa.cbr", "Bbbb.cbr", "Cccc.pdf")
    lib = Library(paths, [1, 2, 3])
    claims = BatchClaims.start([paths[0], paths[2]])
    try:
        lib._action_convert_to_cbz()
        assert [c.kind for c in batches] == ["cbr"]
        assert [os.path.basename(p) for p in batches[0].files] == ["Bbbb.cbr"]
        batches[0].finish([], ("cbr", data("r")))
    finally:
        claims.finish()
    assert [c.kind for c in batches] == ["cbr"]             # lot PDF sauté
    assert batches.overalls == [{"stopped": False, "busy": 2}]


def test_library_is_updated_after_each_batch(tmp_path, batches):
    paths = files(tmp_path, "Aaaa.cbr", "Bbbb.cbr")
    lib = Library(paths, [1, 2])
    lib._action_convert_to_cbz()
    # Le lot a converti Aaaa (supprimé ensuite) ; Bbbb a échoué et reste
    cbz = tmp_path / "Aaaa.cbz"
    cbz.write_bytes(b"x")
    os.remove(paths[0])
    batches[0].finish([str(cbz)], ("cbr", data("r")))
    assert lib._db.reindexed == [str(cbz)]
    assert lib._db.removed == [1]
    assert (None, "Aaaa.cbz") in lib.refreshed and (1, "Aaaa.cbz") in lib.refreshed


def test_images_of_several_folders_get_one_cbz_each(tmp_path, batches):
    """Images de plusieurs dossiers : pas de question « toutes dans un seul
    CBZ » (il irait dans leur dossier commun, sous son nom), un CBZ par image."""
    (tmp_path / "Aaaa").mkdir()
    (tmp_path / "Bbbb").mkdir()
    paths = files(tmp_path, "Aaaa/p01.jpg", "Bbbb/p02.png")
    lib = Library(paths, [1, 2])
    lib._action_convert_to_cbz()
    assert [c.kind for c in batches] == ["img"]
    assert batches[0].directories == [str(tmp_path / "Aaaa"), str(tmp_path / "Bbbb")]


def test_images_of_one_folder_get_the_mode_question(tmp_path, batches):
    lib = Library(files(tmp_path, "p01.jpg", "p02.png"), [1, 2])
    lib._action_convert_to_cbz()
    assert [c.kind for c in batches] == ["image"]
    assert batches[0].directory == str(tmp_path)


def test_batch_directories():
    split = Library._batch_directories
    assert split([r"C:\Aaaa\x.cbr", r"c:\aaaa\y.cbr"]) == (r"C:\Aaaa", None)
    assert split([r"C:\Aaaa\x.cbr", r"C:\Bbbb\y.cbr"]) == ("C:\\",
                                                            [r"C:\Aaaa", r"C:\Bbbb"])
    # Disques différents : pas de dossier commun (os.path.commonpath lèverait
    # ValueError et la chaîne planterait), le premier dossier à la place
    assert split([r"D:\Aaaa\x.cbr", r"E:\Bbbb\y.cbr"]) == (r"D:\Aaaa",
                                                            [r"D:\Aaaa", r"E:\Bbbb"])


def test_library_closed_during_the_batch_is_left_alone(tmp_path, batches):
    paths = files(tmp_path, "Aaaa.cbr", "Bbbb.cbr")
    lib = Library(paths, [1, 2])
    lib._action_convert_to_cbz()
    old_db = lib._db
    lib._db = FakeDB([])                                    # autre base ouverte
    batches[0].finish([str(tmp_path / "Aaaa.cbr")], ("cbr", data("r")))
    assert old_db.reindexed == [] and lib.refreshed == []
