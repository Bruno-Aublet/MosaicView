"""Archives mal nommées (extension qui ne correspond pas au format réel).

- misnamed_archives.misnamed_real_kind : format réel rendu seulement pour une
  extension d'archive qui annonce un autre format ;
- misnamed_archives.rename_to_real_format : règle commune de renommage, jamais
  d'écrasement, jamais un fichier ouvert, utilisé ou pris par un autre lot ;
- import de métadonnées en lot (batch_metadata_dialog_qt) : chargement arrêté
  pour un fichier mal nommé, question au fil du lot, réponse appliquée
  (renommé puis rechargé, ou laissé de côté avec sa raison), réponse gardée
  pour les fichiers suivants, réponse tardive ignorée.

batch_metadata_dialog_qt.py importe PySide6 : ses méthodes sont extraites par
tests/ast_loader.py ; la fenêtre de la question et les fichiers ouverts de
l'application (file_picker_qt) sont remplacés par des faux.
"""
import io
import os
import sys
import tarfile
import types
import zipfile

import pytest

import ast_loader
from modules.qt.archive_members import open_archive
from modules.qt.batch_claims import BatchClaims
from modules.qt.misnamed_archives import (MisnamedArchiveError, RenameRefusedError,
                                          check_extension, checked_extension,
                                          misnamed_real_kind, real_file_kind, rename_label,
                                          rename_to_real_format, rename_with_extension)
from modules.qt.temp_files import exception_text


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


# ---------------------------------------------------------------------------
# Détection
# ---------------------------------------------------------------------------

def test_archive_of_another_format_is_misnamed(tmp_path):
    assert misnamed_real_kind(make_zip(tmp_path / "a.cbr")) == "CBZ"
    assert misnamed_real_kind(make_tgz(tmp_path / "b.cb7")) == "CBT"


def test_well_named_archives_are_not_misnamed(tmp_path):
    assert misnamed_real_kind(make_zip(tmp_path / "a.cbz")) is None
    assert misnamed_real_kind(make_zip(tmp_path / "a.zip")) is None      # ZIP : un CBZ
    assert misnamed_real_kind(make_tgz(tmp_path / "b.cbt")) is None


def test_unknown_format_or_non_archive_extension_is_not_misnamed(tmp_path):
    junk = tmp_path / "c.cbr"
    junk.write_bytes(b"ni rar ni rien")
    assert misnamed_real_kind(str(junk)) is None                      # lecture le dira
    assert misnamed_real_kind(make_zip(tmp_path / "d.pdf")) is None   # pas une archive
    assert misnamed_real_kind(str(tmp_path / "absent.cbr")) is None


# ---------------------------------------------------------------------------
# Renommage
# ---------------------------------------------------------------------------

def test_rename_gives_the_extension_of_the_real_format(tmp_path):
    src = make_zip(tmp_path / "a.cbr")
    assert rename_to_real_format(src, "CBZ") == str(tmp_path / "a.cbz")
    assert sorted(os.listdir(tmp_path)) == ["a.cbz"]


def test_rename_never_overwrites_an_existing_file(tmp_path):
    src = make_zip(tmp_path / "a.cbr")
    (tmp_path / "a.cbz").write_bytes(b"autre BD")
    assert rename_to_real_format(src, "CBZ") == str(tmp_path / "a (1).cbz")
    assert (tmp_path / "a.cbz").read_bytes() == b"autre BD"


def test_file_open_or_in_use_in_the_application_is_not_renamed(tmp_path):
    src = make_zip(tmp_path / "a.cbr")
    with pytest.raises(RenameRefusedError) as caught:
        rename_to_real_format(src, "CBZ", open_files=[src])
    assert caught.value.key == "dialogs.file_picker.file_open" and caught.value.name == "a.cbr"
    with pytest.raises(RenameRefusedError) as caught:
        rename_to_real_format(src, "CBZ", files_in_use=[src])
    assert caught.value.key == "dialogs.file_picker.file_in_use"
    assert sorted(os.listdir(tmp_path)) == ["a.cbr"]


def test_file_of_another_batch_is_not_renamed(tmp_path):
    src = make_zip(tmp_path / "a.cbr")
    other = BatchClaims.start([src])
    try:
        with pytest.raises(RenameRefusedError) as caught:
            rename_to_real_format(src, "CBZ")                     # hors lot
        assert caught.value.key == "messages.errors.file_in_batch"
        mine = BatchClaims()
        other.claim(src)
        with pytest.raises(RenameRefusedError):
            rename_to_real_format(src, "CBZ", claims=mine)        # pris par l'autre lot
    finally:
        other.finish()
    assert sorted(os.listdir(tmp_path)) == ["a.cbr"]


def test_rename_by_a_batch_reserves_the_new_name(tmp_path):
    src = make_zip(tmp_path / "a.cbr")
    claims = BatchClaims.start([src])
    try:
        claims.claim(src)
        new = rename_to_real_format(src, "CBZ", claims=claims)
        assert new == str(tmp_path / "a.cbz")
        # Nom réservé pour ce lot : un autre lot ne peut pas le prendre
        assert not BatchClaims().claim(new)
    finally:
        claims.finish()


# ---------------------------------------------------------------------------
# Lot « Vérifier les extensions » : détection, famille, renommage
# ---------------------------------------------------------------------------

def make_pdf(path, prefix=b""):
    path.write_bytes(prefix + b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n")
    return str(path)


def make_rar_header(path):
    # Seuls les octets de tête comptent pour la détection
    path.write_bytes(b"Rar!\x1a\x07\x00" + b"\x00" * 32)
    return str(path)


def make_epub(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("OEBPS/p01.xhtml", "<html/>")
    return str(path)


@pytest.mark.parametrize("name, expected", [
    ("a.cbz", ".cbz"), ("a.CBR", ".cbr"), ("a.pdf", ".pdf"), ("a.epub", ".epub"),
    ("a.zip", ".zip"), ("a.rar", ".rar"), ("a.7z", ".7z"), ("a.tar", ".tar"),
    ("a.tgz", ".tgz"), ("a.Tar.Gz", ".tar.gz"), ("a.tar.bz2", ".tar.bz2"), ("a.tar.xz", ".tar.xz"),
    ("a.jpg", None), ("a.gz", None), (".zip", None), ("a", None),
])
def test_checked_extensions(name, expected):
    assert checked_extension(os.path.join("dossier", name)) == expected


def test_pdf_is_recognised_even_after_a_few_leading_bytes(tmp_path):
    assert real_file_kind(make_pdf(tmp_path / "a.cbz")) == "PDF"
    assert real_file_kind(make_pdf(tmp_path / "b.cbz", prefix=b"\x00" * 100)) == "PDF"


def test_archive_holding_a_pdf_is_an_archive_not_a_pdf(tmp_path):
    path = tmp_path / "a.pdf"
    with tarfile.open(path, "w") as tf:
        data = b"%PDF-1.4\n%%EOF\n"
        info = tarfile.TarInfo("x.pdf")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    assert real_file_kind(str(path)) == "CBT"


def test_unknown_or_missing_file_has_no_real_kind(tmp_path):
    junk = tmp_path / "a.cbr"
    junk.write_bytes(b"ni archive ni pdf")
    assert real_file_kind(str(junk)) is None
    assert real_file_kind(str(tmp_path / "absent.cbr")) is None


@pytest.mark.parametrize("name, maker, real, new", [
    # Famille comic : reste un comic
    ("a.cbr", make_zip, "CBZ", ".cbz"),
    ("a.cbz", make_rar_header, "CBR", ".cbr"),
    ("a.cb7", make_tgz, "CBT", ".cbt"),
    ("a.cbz", make_pdf, "PDF", ".pdf"),
    ("a.pdf", make_zip, "CBZ", ".cbz"),
    ("a.cbz", make_epub, "EPUB", ".epub"),
    ("a.epub", make_zip, "CBZ", ".cbz"),
    # Famille archive : reste une archive
    ("a.rar", make_zip, "CBZ", ".zip"),
    ("a.zip", make_rar_header, "CBR", ".rar"),
    ("a.7z", make_tgz, "CBT", ".tar"),
    ("a.tar.gz", make_zip, "CBZ", ".zip"),
    ("a.zip", make_epub, "EPUB", ".epub"),
    ("a.rar", make_pdf, "PDF", ".pdf"),
])
def test_misnamed_file_gets_an_extension_of_its_family(tmp_path, name, maker, real, new):
    check = check_extension(maker(tmp_path / name))
    assert (check.real_kind, check.new_extension) == (real, new)


@pytest.mark.parametrize("name, maker", [
    ("a.cbz", make_zip), ("a.zip", make_zip), ("a.cbr", make_rar_header),
    ("a.rar", make_rar_header), ("a.pdf", make_pdf), ("a.epub", make_epub),
    # Toutes les variantes de TAR annoncent le même format
    ("a.cbt", make_tgz), ("a.tar", make_tgz), ("a.tgz", make_tgz), ("a.tar.gz", make_tgz),
])
def test_well_named_file_keeps_its_extension(tmp_path, name, maker):
    check = check_extension(maker(tmp_path / name))
    assert check.real_kind is not None and check.new_extension is None


def test_unknown_format_is_reported_without_new_extension(tmp_path):
    junk = tmp_path / "a.cbz"
    junk.write_bytes(b"ni archive ni pdf")
    check = check_extension(str(junk))
    assert (check.extension, check.real_kind, check.new_extension) == (".cbz", None, None)
    assert check_extension(str(tmp_path / "a.jpg")) is None


def test_rename_replaces_a_double_extension(tmp_path):
    src = make_zip(tmp_path / "a.tar.gz")
    assert rename_with_extension(src, ".tar.gz", ".zip") == str(tmp_path / "a.zip")
    assert sorted(os.listdir(tmp_path)) == ["a.zip"]


def test_rename_with_extension_never_overwrites(tmp_path):
    src = make_zip(tmp_path / "a.rar")
    (tmp_path / "a.zip").write_bytes(b"autre")
    assert rename_with_extension(src, ".rar", ".zip") == str(tmp_path / "a (1).zip")
    assert (tmp_path / "a.zip").read_bytes() == b"autre"


def test_rename_label():
    assert rename_label(".cbr", ".cbz") == "CBR→CBZ"
    assert rename_label(".tar.gz", ".zip") == "TAR.GZ→ZIP"


# ---------------------------------------------------------------------------
# Import de métadonnées en lot
# ---------------------------------------------------------------------------

def test_loading_a_misnamed_file_reads_nothing_and_reports_its_real_format(tmp_path):
    load = ast_loader.load_methods(
        "batch_metadata_dialog_qt.py", "_BatchMetadataOrchestrator", {"_load_state_for_file"},
        os=os, open_archive=open_archive)()._load_state_for_file
    src = make_zip(tmp_path / "a.cbr")
    with pytest.raises(MisnamedArchiveError) as caught:
        load(src)
    assert caught.value.real_kind == "CBZ"


class FakeQuestion:
    shown = []

    def __init__(self, parent, files, message_key, skip_key, following_key=None, as_list=False):
        [(path, real_kind)] = files
        self.parent, self.path, self.real_kind = parent, path, real_kind
        self.closed = False
        FakeQuestion.shown.append(self)

    def ask_async(self, on_result):
        self.answer = on_result

    def close(self):
        self.closed = True


Methods = ast_loader.load_methods(
    "batch_metadata_dialog_qt.py", "_BatchMetadataOrchestrator",
    {"_on_misnamed", "_apply_misnamed_choice"},
    os=os, _wt=lambda key, **kw: key, exception_text=exception_text)


class Orchestrator(Methods):
    def __init__(self, files, claims):
        self._files, self._index, self._seq = list(files), 0, 1
        self._ended = self._cancelling = False
        self._claims = claims
        self._current_dlg, self._parent = "fenêtre ComicVine", "panneau"
        self._renamed, self._misnamed_choice, self._misnamed_dlg = [], None, None
        self._renamed_kinds = {}
        self.errors, self.loaded = [], []

    def _on_load_error(self, msg):
        self.errors.append((os.path.basename(self._files[self._index]), msg))
        self._index += 1

    def _load_and_open(self, first=False):
        self.loaded.append(self._files[self._index])


@pytest.fixture
def batch(tmp_path, monkeypatch):
    FakeQuestion.shown.clear()
    monkeypatch.setitem(sys.modules, "modules.qt.misnamed_archive_dialog_qt",
                        types.SimpleNamespace(MisnamedArchiveDialog=FakeQuestion))
    opened = []
    monkeypatch.setitem(sys.modules, "modules.qt.file_picker_qt",
                        types.SimpleNamespace(_open_documents=lambda: list(opened),
                                              _files_in_use=lambda: []))
    files = [make_zip(tmp_path / "a.cbr"), make_zip(tmp_path / "b.cbr")]
    claims = BatchClaims.start(files)
    orc = Orchestrator(files, claims)
    orc.opened = opened
    yield orc
    claims.finish()


def test_question_is_asked_over_the_comicvine_window(batch):
    batch._on_misnamed("CBZ")
    [question] = FakeQuestion.shown
    assert (question.parent, question.real_kind) == ("fenêtre ComicVine", "CBZ")
    assert batch.loaded == [] and batch.errors == []       # le lot attend la réponse


def test_renamed_file_is_reloaded_at_the_same_place(batch, tmp_path):
    batch._on_misnamed("CBZ")
    FakeQuestion.shown[0].answer(("rename", False))
    new = str(tmp_path / "a.cbz")
    assert batch.loaded == [new] and batch._files[0] == new
    assert batch._renamed == ["a.cbr → a.cbz"] and batch.errors == []


def test_skipped_file_is_reported_and_the_batch_goes_on(batch, tmp_path):
    batch._on_misnamed("CBZ")
    FakeQuestion.shown[0].answer(("skip", False))
    assert batch.errors == [("a.cbr", "messages.errors.batch_misnamed_not_renamed")]
    assert batch._index == 1 and batch._renamed == []
    assert sorted(os.listdir(tmp_path)) == ["a.cbr", "b.cbr"]


def test_answer_for_the_following_files_is_not_asked_again(batch, tmp_path):
    batch._on_misnamed("CBZ")
    FakeQuestion.shown[0].answer(("rename", True))
    batch._index = 1
    batch._on_misnamed("CBZ")
    assert len(FakeQuestion.shown) == 1
    assert batch._renamed == ["a.cbr → a.cbz", "b.cbr → b.cbz"]
    # Ligne du journal par ancienne et nouvelle extension
    assert batch._renamed_kinds == {"CBR→CBZ": 2}


def test_file_open_in_a_panel_is_not_renamed(batch, tmp_path):
    batch.opened.append(batch._files[0])
    batch._on_misnamed("CBZ")
    FakeQuestion.shown[0].answer(("rename", False))
    assert batch.errors == [("a.cbr", "dialogs.file_picker.file_open")]
    assert batch.loaded == [] and (tmp_path / "a.cbr").exists()


@pytest.mark.parametrize("change", ["ended", "cancelling", "next file"])
def test_answer_after_the_batch_moved_on_does_nothing(batch, tmp_path, change):
    batch._on_misnamed("CBZ")
    if change == "ended":
        batch._ended = True
    elif change == "cancelling":
        batch._cancelling = True
    else:
        batch._seq += 1
    FakeQuestion.shown[0].answer(("rename", True))
    assert batch.loaded == [] and batch.errors == [] and batch._misnamed_choice is None
    assert (tmp_path / "a.cbr").exists()
