"""Écriture d'un fichier par l'import de métadonnées en lot (_save_state_for_file).

- Tous les fichiers de l'archive d'origine sont recopiés (pages de tout format,
  .nfo…), sauf l'ancien ComicInfo.xml remplacé par le nouveau, et ses
  dossiers, vides compris.
- Un échec d'écriture lève une exception (jamais avalée) : l'original reste
  intact et aucun temporaire ne traîne.
- L'annulation (annulation du lot, fermeture de l'application) arrête
  l'écriture proprement, original intact.

batch_metadata_dialog_qt.py importe PySide6 au niveau module : la méthode et
ses dépendances sont extraites par tests/ast_loader.py et exécutées seules,
sans aucun import Qt ; une nouvelle dépendance fait échouer le test en la
nommant. L'écrivain commun (converted_cbz.write_cbz, delete_source) a ses
propres tests dans test_converted_cbz.py.
"""
import io
import os
import subprocess
import sys
import tarfile
import threading
import types
import zipfile
from pathlib import Path

import pytest

import ast_loader
from modules.qt import archive_members
from modules.qt.archive_members import open_archive
from modules.qt.archive_names import MAX_MEMBER_SIZE, open_zip
from modules.qt.temp_files import exception_text

SEVEN_ZIP = Path(__file__).resolve().parent.parent / "7zip" / "7z.exe"

# Lecteur d'archive commun (archive_members, sans Qt) : le vrai
_DEPS = dict(os=os, _=lambda key, **kw: key, open_zip=open_zip, exception_text=exception_text,
             open_archive=open_archive)
_NS = ast_loader.load("batch_metadata_dialog_qt.py", {"_is_comicinfo_name", "_SaveCancelled"},
                      **_DEPS)
SaveCancelled = _NS["_SaveCancelled"]
save_state_for_file = ast_loader.load_methods(
    "batch_metadata_dialog_qt.py", "_BatchMetadataOrchestrator", {"_save_state_for_file"},
    _is_comicinfo_name=_NS["_is_comicinfo_name"], _SaveCancelled=SaveCancelled,
    **_DEPS)._save_state_for_file


@pytest.fixture(autouse=True)
def fake_config(monkeypatch):
    """Le gestionnaire de configuration n'existe pas hors de l'application."""
    import modules.qt.config_manager as config_manager
    monkeypatch.setattr(config_manager, "get_config_manager",
                        lambda: types.SimpleNamespace(get_zip_compression_level=lambda: 0))


def _fake_archive_loader(monkeypatch):
    """Module de remplacement de modules.qt.archive_loader (qui importe Qt),
    importé par la recopie d'un CBZ : il contient la vraie fonction
    _check_member_size (archive_members.py), extraite par ast_loader, avec la
    vraie constante MAX_MEMBER_SIZE d'archive_names. set_limit change la
    limite de cette fonction et celle du vrai lecteur commun (conversions
    CBR, CBT, CB7)."""
    fake = types.ModuleType("modules.qt.archive_loader")
    fake.__dict__.update(ast_loader.load("archive_members.py", {"_check_member_size"},
                                         MAX_MEMBER_SIZE=MAX_MEMBER_SIZE))

    def set_limit(size):
        # Espace de noms de la fonction extraite, que lit _check_member_size
        fake._check_member_size.__globals__["MAX_MEMBER_SIZE"] = size
        monkeypatch.setattr(archive_members, "MAX_MEMBER_SIZE", size)
    fake.set_limit = set_limit
    return fake


@pytest.fixture(autouse=True)
def fake_archive_loader(monkeypatch):
    fake = _fake_archive_loader(monkeypatch)
    monkeypatch.setitem(sys.modules, "modules.qt.archive_loader", fake)
    return fake


def orchestrator(permanent_delete=True):
    return types.SimpleNamespace(_permanent_delete=permanent_delete)


NEW_XML = b"<ComicInfo><Series>New</Series></ComicInfo>"


def xml_entry():
    return {"orig_name": "ComicInfo.xml", "bytes": NEW_XML, "is_image": False}


def leftovers(folder):
    return [n for n in os.listdir(folder) if n.endswith(".~tmp") or n.startswith(".~mosaicview_")]


def make_cbz(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def read_zip(path):
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


ORIGINAL = {"p01.jpg": b"jpg", "p02.tif": b"tif", "p03.jfif": b"jfif",
            "info.nfo": b"nfo", "ComicInfo.xml": b"<ComicInfo/>"}


# ---------------------------------------------------------------------------
# CBZ existant
# ---------------------------------------------------------------------------

def cbz_state(cbz):
    return types.SimpleNamespace(modified=True, images_data=[
        xml_entry(),
        {"orig_name": "__cbz_manifest__", "bytes": None, "_cbz_path": str(cbz)},
    ])


def test_unmodified_state_writes_nothing(tmp_path):
    cbz = tmp_path / "comic.cbz"
    make_cbz(cbz, ORIGINAL)
    before = cbz.read_bytes()
    state = cbz_state(cbz)
    state.modified = False
    save_state_for_file(orchestrator(), str(cbz), state)
    assert cbz.read_bytes() == before


def test_cbz_keeps_every_file_and_replaces_comicinfo(tmp_path):
    cbz = tmp_path / "comic.cbz"
    make_cbz(cbz, ORIGINAL)
    save_state_for_file(orchestrator(), str(cbz), cbz_state(cbz))
    content = read_zip(cbz)
    assert content["ComicInfo.xml"] == NEW_XML
    for name in ("p01.jpg", "p02.tif", "p03.jfif", "info.nfo"):
        assert content[name] == ORIGINAL[name]
    with zipfile.ZipFile(cbz) as zf:
        assert len(zf.namelist()) == len(set(zf.namelist()))
    assert leftovers(tmp_path) == []


def test_cbz_keeps_archive_comment(tmp_path):
    """Commentaire d'archive (métadonnées ComicBookInfo) conservé."""
    cbz = tmp_path / "comic.cbz"
    make_cbz(cbz, ORIGINAL)
    with zipfile.ZipFile(cbz, "a") as zf:
        zf.comment = b'{"ComicBookInfo/1.0": {"series": "X"}}'
    save_state_for_file(orchestrator(), str(cbz), cbz_state(cbz))
    with zipfile.ZipFile(cbz) as zf:
        assert zf.comment == b'{"ComicBookInfo/1.0": {"series": "X"}}'


def test_cbz_member_too_large_fails_and_keeps_original(tmp_path, fake_archive_loader):
    """Un membre recopié au-delà de la limite de taille n'est pas lu en
    mémoire : l'écriture échoue et le CBZ d'origine reste intact."""
    fake_archive_loader.set_limit(4)
    cbz = tmp_path / "comic.cbz"
    make_cbz(cbz, {"p01.jpg": b"1234", "big.jpg": b"12345", "ComicInfo.xml": b"<x/>"})
    before = cbz.read_bytes()
    with pytest.raises(ValueError):
        save_state_for_file(orchestrator(), str(cbz), cbz_state(cbz))
    assert cbz.read_bytes() == before
    assert leftovers(tmp_path) == []


def test_cancel_keeps_original_untouched(tmp_path):
    cbz = tmp_path / "comic.cbz"
    make_cbz(cbz, ORIGINAL)
    before = cbz.read_bytes()
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(SaveCancelled):
        save_state_for_file(orchestrator(), str(cbz), cbz_state(cbz), cancel_event=cancelled)
    assert cbz.read_bytes() == before
    assert leftovers(tmp_path) == []


def test_cbz_rewritten_between_loading_and_saving_keeps_its_new_pages(tmp_path):
    """L'import est enregistré plusieurs minutes après le chargement du
    fichier : un autre programme a pu réécrire le CBZ entre-temps. Les pages
    sont relues au moment d'enregistrer, jamais reprises du chargement : les
    pages écrites entre-temps restent, seul ComicInfo.xml change. La
    couverture lue au chargement (affichée dans la fenêtre ComicVine) n'est
    jamais réécrite à la place de la page du fichier."""
    cbz = tmp_path / "comic.cbz"
    make_cbz(cbz, ORIGINAL)
    state = cbz_state(cbz)
    state.images_data.insert(0, {"orig_name": "p01.jpg", "bytes": ORIGINAL["p01.jpg"],
                                 "is_image": True, "is_dir": False})
    rewritten = {"p01.jpg": b"page refaite", "p04.jpg": b"page ajoutee", "ComicInfo.xml": b"<x/>"}
    make_cbz(cbz, rewritten)
    save_state_for_file(orchestrator(), str(cbz), state)
    content = read_zip(cbz)
    assert content == {"p01.jpg": b"page refaite", "p04.jpg": b"page ajoutee",
                       "ComicInfo.xml": NEW_XML}
    assert leftovers(tmp_path) == []


def disk_almost_full(monkeypatch):
    """10 octets libres (utils.check_free_space, appelée par write_cbz)."""
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: types.SimpleNamespace(free=10))


def test_cbz_without_room_on_the_disk_is_refused_and_original_kept(tmp_path, monkeypatch):
    """Place vérifiée avant d'écrire (taille des membres recopiés et du
    nouveau ComicInfo.xml) : DiskSpaceError, que _SaveWorker transmet par
    son signal disk_full pour arrêter l'import. Original intact."""
    from modules.qt.utils import DiskSpaceError
    cbz = tmp_path / "comic.cbz"
    make_cbz(cbz, ORIGINAL)
    before = cbz.read_bytes()
    disk_almost_full(monkeypatch)
    with pytest.raises(DiskSpaceError) as caught:
        save_state_for_file(orchestrator(), str(cbz), cbz_state(cbz))
    assert caught.value.needed > sum(len(v) for v in ORIGINAL.values()) + len(NEW_XML)
    assert cbz.read_bytes() == before
    assert leftovers(tmp_path) == []


def test_conversion_without_room_on_the_disk_creates_nothing_and_keeps_the_source(
        tmp_path, monkeypatch):
    from modules.qt.utils import DiskSpaceError
    cbt = tmp_path / "comic.cbt"
    make_cbt(cbt, ORIGINAL)
    disk_almost_full(monkeypatch)
    with pytest.raises(DiskSpaceError):
        save_state_for_file(orchestrator(permanent_delete=True), str(tmp_path / "comic.cbz"),
                            cbt_state(cbt))
    assert sorted(os.listdir(tmp_path)) == ["comic.cbt"]


def test_save_worker_reports_a_full_disk_apart_from_other_errors(monkeypatch):
    """_SaveWorker.run : un disque plein (DiskSpaceError ou ENOSPC) part par
    le signal disk_full, qui arrête l'import ; toute autre erreur par error,
    qui passe au fichier suivant."""
    import errno
    from modules.qt import localization
    from modules.qt.utils import DiskSpaceError
    from fakes import Signal
    lots = types.ModuleType("modules.qt.batch_dialogs_qt")
    lots._is_disk_full = ast_loader.load("batch_dialogs_qt.py", {"_is_disk_full"})["_is_disk_full"]
    monkeypatch.setitem(sys.modules, "modules.qt.batch_dialogs_qt", lots)
    Worker = ast_loader.load_methods("batch_metadata_dialog_qt.py", "_SaveWorker", {"run"},
                                     _SaveCancelled=SaveCancelled, exception_text=exception_text)
    monkeypatch.setattr(localization, "_", lambda key, **kw: key)

    class Run(Worker):
        def __init__(self, error):
            self.done, self.error, self.disk_full, self.progress = (Signal(), Signal(),
                                                                    Signal(), Signal())
            self._filepath, self._state, self._cancelled = "x.cbz", None, threading.Event()

            def fail(*args, **kwargs):
                raise error
            self._orc = types.SimpleNamespace(_save_state_for_file=fail)

    for error, signal in ((DiskSpaceError(500, 10), "disk_full"),
                          (OSError(errno.ENOSPC, "No space left on device"), "disk_full"),
                          (OSError(errno.EACCES, "Accès refusé"), "error")):
        worker = Run(error)
        received = {name: [] for name in ("disk_full", "error")}
        worker.disk_full.connect(received["disk_full"].append)
        worker.error.connect(received["error"].append)
        worker.run()
        assert [name for name, values in received.items() if values] == [signal], error


def test_write_failure_is_raised_and_original_kept(tmp_path):
    cbz = tmp_path / "comic.cbz"
    make_cbz(cbz, ORIGINAL)
    before = cbz.read_bytes()
    state = cbz_state(cbz)
    state.images_data[1]["_cbz_path"] = str(tmp_path / "missing.cbz")
    with pytest.raises(OSError):
        save_state_for_file(orchestrator(), str(cbz), state)
    assert cbz.read_bytes() == before
    assert leftovers(tmp_path) == []


# ---------------------------------------------------------------------------
# CBT converti en CBZ
# ---------------------------------------------------------------------------

def make_cbt(path, members):
    with tarfile.open(path, "w") as tf:
        for name, data in members.items():
            src = path.parent / ("src_" + name.replace("/", "_"))
            src.write_bytes(data)
            tf.add(src, arcname=name)
            src.unlink()


def cbt_state(cbt):
    state = types.SimpleNamespace(modified=True, images_data=[
        xml_entry(),
        {"orig_name": "__cbt_manifest__", "bytes": None, "_cbt_path": str(cbt)},
    ])
    state._original_filepath = str(cbt)
    return state


def test_conversion_keeps_the_archive_comment_of_the_source(tmp_path, monkeypatch):
    """Commentaire d'archive de la source (ComicBookInfo d'un CBR, lu par
    ArchiveReader.comment) repris dans le CBZ : la source est supprimée
    ensuite."""
    comment = b'{"ComicBookInfo/1.0": {"series": "Aaaa"}}'
    monkeypatch.setattr(archive_members.ArchiveReader, "comment", lambda self: comment)
    cbt = tmp_path / "comic.cbt"
    make_cbt(cbt, ORIGINAL)
    out = tmp_path / "comic.cbz"
    save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt))
    with zipfile.ZipFile(out) as zf:
        assert zf.comment == comment


def test_cbt_conversion_keeps_every_file(tmp_path):
    """Pages TIFF/JFIF et fichiers annexes recopiés, ComicInfo.xml d'un dossier
    compris (ce n'est pas le ComicInfo.xml de la BD) : l'original est supprimé
    ensuite."""
    cbt = tmp_path / "comic.cbt"
    make_cbt(cbt, {**ORIGINAL, "sub/ComicInfo.xml": b"<old/>"})
    out = tmp_path / "comic.cbz"
    save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt))
    content = read_zip(out)
    assert content["ComicInfo.xml"] == NEW_XML
    for name in ("p01.jpg", "p02.tif", "p03.jfif", "info.nfo"):
        assert content[name] == ORIGINAL[name]
    assert content["sub/ComicInfo.xml"] == b"<old/>"
    assert not cbt.exists()


def zip_folders(path):
    with zipfile.ZipFile(path) as zf:
        return sorted(i.filename for i in zf.infolist() if i.is_dir())


def test_cbt_conversion_keeps_its_folders_empty_ones_included(tmp_path):
    """Un dossier de la source, vide ou non, est recopié (l'original est
    supprimé ensuite), sous un nom contenu comme les fichiers (« ../x » → « x »),
    sans compter parmi les fichiers relus."""
    cbt = tmp_path / "comic.cbt"
    with tarfile.open(cbt, "w") as tf:
        for name in ("./Ch1", "Vide", "../Piege"):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            tf.addfile(info)
        info = tarfile.TarInfo("./Ch1/p01.jpg")
        info.size = 3
        tf.addfile(info, io.BytesIO(b"jpg"))
    out = tmp_path / "comic.cbz"
    assert save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt)) is None
    assert zip_folders(out) == ["Ch1/", "Piege/", "Vide/"]
    assert read_zip(out)["Ch1/p01.jpg"] == b"jpg"
    assert not cbt.exists()


@pytest.mark.skipif(sys.platform != "win32" or not SEVEN_ZIP.exists(),
                    reason="7z.exe embarqué requis (Windows)")
def test_cb7_conversion_keeps_its_empty_folders(tmp_path):
    src = tmp_path / "src"
    (src / "Vide" / "Dedans").mkdir(parents=True)
    (src / "p01.jpg").write_bytes(b"jpg")
    cb7 = tmp_path / "comic.cb7"
    subprocess.run([str(SEVEN_ZIP), "a", "-t7z", str(cb7), "*"], cwd=src, check=True,
                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    state = types.SimpleNamespace(modified=True, images_data=[
        xml_entry(),
        {"orig_name": "__cb7_manifest__", "bytes": None, "_cb7_path": str(cb7)},
    ])
    state._original_filepath = str(cb7)
    out = tmp_path / "comic.cbz"
    assert save_state_for_file(orchestrator(permanent_delete=True), str(out), state) is None
    assert zip_folders(out) == ["Vide/", "Vide/Dedans/"]
    assert read_zip(out)["p01.jpg"] == b"jpg"
    assert not cb7.exists()


def test_conversion_reports_original_that_could_not_be_deleted(tmp_path, monkeypatch):
    """Original impossible à supprimer (fichier ouvert ailleurs…) : la raison
    est rendue, pour être signalée dans le résumé du lot, jamais avalée ; le
    CBZ écrit reste en place et l'original aussi."""
    cbt = tmp_path / "comic.cbt"
    make_cbt(cbt, {"p01.jpg": b"A"})
    out = tmp_path / "comic.cbz"
    real_remove = os.remove

    def refuse_original(path):
        if os.path.abspath(path) == os.path.abspath(cbt):
            raise PermissionError("locked")
        real_remove(path)
    monkeypatch.setattr(os, "remove", refuse_original)
    reason = save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt))
    assert reason and "locked" in reason
    assert cbt.exists()
    assert read_zip(out) == {"ComicInfo.xml": NEW_XML, "p01.jpg": b"A"}


def test_original_that_could_not_be_deleted_is_never_reported_with_an_empty_reason(
        tmp_path, monkeypatch):
    """Exception sans texte (comme EOFError, MemoryError) : la raison rendue
    nomme son type, sinon la ligne du journal du lot resterait vide."""
    cbt = tmp_path / "comic.cbt"
    make_cbt(cbt, {"p01.jpg": b"A"})
    out = tmp_path / "comic.cbz"
    real_remove = os.remove

    def refuse_original(path):
        if os.path.abspath(path) == os.path.abspath(cbt):
            raise PermissionError()
        real_remove(path)
    monkeypatch.setattr(os, "remove", refuse_original)
    reason = save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt))
    assert reason == "PermissionError"
    assert cbt.exists() and out.exists()


def test_original_goes_to_the_trash_unless_permanent_deletion(tmp_path, monkeypatch):
    import modules.qt.utils as utils
    trashed = []
    monkeypatch.setattr(utils, "send_to_trash", lambda path: (trashed.append(path), os.remove(path)))
    cbt = tmp_path / "comic.cbt"
    make_cbt(cbt, {"p01.jpg": b"A"})
    out = tmp_path / "comic.cbz"
    assert save_state_for_file(orchestrator(permanent_delete=False), str(out), cbt_state(cbt)) is None
    assert trashed == [str(cbt)] and not cbt.exists()


def test_conversion_of_a_source_rewritten_meanwhile_gives_no_cbz(tmp_path, fake_archive_loader,
                                                                 monkeypatch):
    """Un autre programme réécrit la source pendant que ses membres sont
    recopiés (date changée) : le CBZ pourrait mêler deux versions. Il n'est
    pas créé, et la source n'est pas supprimée."""
    from modules.qt.converted_cbz import SourceChangedError
    cbt = tmp_path / "comic.cbt"
    make_cbt(cbt, {"p01.jpg": b"A", "p02.jpg": b"B"})
    out = tmp_path / "comic.cbz"
    # Lecture d'un membre TAR par le lecteur commun (archive_members)
    real_read = archive_members._tar_info_read

    def read_then_rewrite(tf, member):
        st = os.stat(cbt)
        os.utime(cbt, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
        return real_read(tf, member)
    monkeypatch.setattr(archive_members, "_tar_info_read", read_then_rewrite)
    with pytest.raises(SourceChangedError):
        save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt))
    assert not out.exists() and cbt.exists()
    assert leftovers(tmp_path) == []


def test_conversion_with_original_deleted_reports_nothing(tmp_path):
    cbt = tmp_path / "comic.cbt"
    make_cbt(cbt, {"p01.jpg": b"A"})
    out = tmp_path / "comic.cbz"
    assert save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt)) is None
    assert not cbt.exists()


# Relecture du CBZ d'une conversion AVANT qu'il prenne son nom (Audit 10,
# constat B) : relu après, il faudrait le supprimer par ce nom s'il est abîmé,
# alors qu'un autre programme ou un panneau a pu y écrire entre-temps.

def test_converted_cbz_is_reread_before_it_gets_its_name(tmp_path, monkeypatch):
    import modules.qt.utils as utils
    cbt = tmp_path / "comic.cbt"
    make_cbt(cbt, {"p01.jpg": b"A"})
    out = tmp_path / "comic.cbz"
    seen = []
    real = utils.verify_written_cbz

    def spy(path, expected_count=None, shown_path=None):
        seen.append((os.path.basename(path).startswith(".~mosaicview_"), out.exists(),
                     expected_count, shown_path))
        return real(path, expected_count, shown_path=shown_path)
    monkeypatch.setattr(utils, "verify_written_cbz", spy)
    save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt))
    assert seen == [(True, False, 2, str(out))]   # temporaire relu, CBZ pas encore nommé
    assert not cbt.exists()


def test_converted_cbz_that_does_not_reread_is_never_created(tmp_path, monkeypatch):
    import modules.qt.utils as utils
    cbt = tmp_path / "comic.cbt"
    make_cbt(cbt, {"p01.jpg": b"A"})
    out = tmp_path / "comic.cbz"

    def fail(path, expected_count=None, shown_path=None):
        raise utils.WrittenFileCheckError(shown_path or path, detail="relecture refusée")
    monkeypatch.setattr(utils, "verify_written_cbz", fail)
    with pytest.raises(utils.WrittenFileCheckError):
        save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt))
    assert not out.exists()
    assert cbt.exists()
    assert leftovers(tmp_path) == []


def test_file_written_meanwhile_under_the_cbz_name_is_never_deleted(tmp_path, monkeypatch):
    import modules.qt.utils as utils
    cbt = tmp_path / "comic.cbt"
    make_cbt(cbt, {"p01.jpg": b"A"})
    out = tmp_path / "comic.cbz"

    def other_program_then_fail(path, expected_count=None, shown_path=None):
        out.write_bytes(b"fichier d'un autre programme")
        raise utils.WrittenFileCheckError(shown_path or path, detail="relecture refusée")
    monkeypatch.setattr(utils, "verify_written_cbz", other_program_then_fail)
    with pytest.raises(utils.WrittenFileCheckError):
        save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt))
    assert out.read_bytes() == b"fichier d'un autre programme"
    assert cbt.exists()


def test_cbz_rewritten_in_place_is_not_fully_reread(tmp_path, monkeypatch):
    """Pas de conversion, pas d'original à supprimer : contrôle rapide de
    structure seulement, comme un enregistrement depuis un panneau."""
    import modules.qt.utils as utils
    cbz = tmp_path / "comic.cbz"
    make_cbz(cbz, {"p01.jpg": b"A"})
    monkeypatch.setattr(utils, "verify_written_cbz",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("relu")))
    save_state_for_file(orchestrator(), str(cbz), cbz_state(cbz))
    assert read_zip(cbz)["ComicInfo.xml"] == NEW_XML


def test_cbt_old_root_comicinfo_is_replaced(tmp_path):
    """« ./ComicInfo.xml » d'un CBT est l'ancien ComicInfo.xml de la BD :
    remplacé par le nouveau, pas recopié à côté."""
    cbt = tmp_path / "comic.cbt"
    make_cbt(cbt, {"./ComicInfo.xml": b"<old/>", "p01.jpg": b"A"})
    out = tmp_path / "comic.cbz"
    save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt))
    assert read_zip(out) == {"ComicInfo.xml": NEW_XML, "p01.jpg": b"A"}


def test_cbt_second_root_comicinfo_is_kept_as_annex(tmp_path):
    """Deux ComicInfo.xml à la racine : le premier est remplacé, le second est
    un fichier annexe recopié sous le nom ComicInfo_0002.xml (l'original est
    supprimé ensuite)."""
    cbt = tmp_path / "comic.cbt"
    with tarfile.open(cbt, "w") as tf:
        for name, data in (("ComicInfo.xml", b"<first/>"), ("ComicInfo.xml", b"<second/>"),
                           ("p01.jpg", b"A")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    out = tmp_path / "comic.cbz"
    save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt))
    assert read_zip(out) == {"ComicInfo.xml": NEW_XML, "ComicInfo_0002.xml": b"<second/>",
                             "p01.jpg": b"A"}


def test_cbt_member_names_are_normalized(tmp_path):
    """Membres TAR « ./p01.jpg » : écrits « p01.jpg », comme à l'ouverture d'un
    CBT ; sinon le CBZ produit aurait un faux sous-dossier « . »."""
    cbt = tmp_path / "comic.cbt"
    make_cbt(cbt, {"./p01.jpg": b"A", "./sub/p02.jpg": b"B"})
    out = tmp_path / "comic.cbz"
    save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt))
    content = read_zip(out)
    assert content["p01.jpg"] == b"A"
    assert content["sub/p02.jpg"] == b"B"
    assert not any(name.startswith(".") for name in content)


def make_raw_cbt(path, members):
    """CBT écrit membre par membre, noms gardés tels quels (homonymes et noms
    piégés compris, que tarfile.add ne produit pas)."""
    with tarfile.open(path, "w") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def test_cbt_names_never_leave_the_extraction_folder(tmp_path):
    """Un nom piégé de la source (« ../x », absolu, lettre de lecteur) ne passe
    pas dans le CBZ produit : un autre lecteur l'écrirait hors de son dossier."""
    cbt = tmp_path / "comic.cbt"
    make_raw_cbt(cbt, [("../evil.jpg", b"E"), ("/abs.jpg", b"A"), ("C:/drive.jpg", b"D"),
                       ("sub/../../up.jpg", b"U"), ("ok/p01.jpg", b"P")])
    out = tmp_path / "comic.cbz"
    save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt))
    content = read_zip(out)
    assert content["evil.jpg"] == b"E"
    assert content["abs.jpg"] == b"A"
    assert content["drive.jpg"] == b"D"
    assert content["sub/up.jpg"] == b"U"
    assert content["ok/p01.jpg"] == b"P"
    assert not any(".." in name.split("/") or name.startswith("/") or ":" in name
                   for name in content)


def test_cbt_homonyms_get_distinct_names(tmp_path):
    """Deux membres de même nom (ou devenus identiques) : deux entrées ZIP
    distinctes, chacune avec son contenu — la source est supprimée ensuite."""
    cbt = tmp_path / "comic.cbt"
    make_raw_cbt(cbt, [("p.jpg", b"first"), ("p.jpg", b"second"), ("../p.jpg", b"third")])
    out = tmp_path / "comic.cbz"
    save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt))
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert len(names) == len(set(names))
        pages = sorted(zf.read(n) for n in names if n.endswith(".jpg"))
    assert pages == [b"first", b"second", b"third"]


def test_cbt_member_too_large_fails_and_keeps_original(tmp_path, fake_archive_loader):
    fake_archive_loader.set_limit(4)
    cbt = tmp_path / "comic.cbt"
    make_raw_cbt(cbt, [("ok.jpg", b"1234"), ("big.jpg", b"12345")])
    out = tmp_path / "comic.cbz"
    with pytest.raises(ValueError):
        save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt))
    assert cbt.exists()
    assert not out.exists()
    assert leftovers(tmp_path) == []


def test_cbt_failure_keeps_original(tmp_path):
    cbt = tmp_path / "comic.cbt"
    cbt.write_bytes(b"not a tar archive")
    out = tmp_path / "comic.cbz"
    with pytest.raises(Exception):
        save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt))
    assert cbt.exists()
    assert not out.exists()
    assert leftovers(tmp_path) == []


extract_non_cbz = ast_loader.load_methods(
    "batch_metadata_dialog_qt.py", "_BatchMetadataOrchestrator", {"_extract_non_cbz"},
    os=os, _=lambda key, **kw: key)()._extract_non_cbz


def test_misnamed_archive_is_converted_from_its_real_format(tmp_path):
    """TAR compressé nommé .cb7 : 7-Zip, qui ouvre tout format, n'y verrait
    qu'un fichier (le TAR) et la conversion le recopierait à la place des
    pages avant de supprimer l'original. Le format réel est relevé au
    chargement et la conversion lit les pages du TAR."""
    cb7 = tmp_path / "comic.cb7"
    with tarfile.open(cb7, "w:gz") as tf:
        for name, data in (("p01.jpg", b"A"), ("p02.jpg", b"B")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    manifest = extract_non_cbz(str(cb7), ".cb7")[-1]
    assert manifest["_kind"] == "CBT"
    state = types.SimpleNamespace(modified=True, images_data=[xml_entry(), manifest])
    state._original_filepath = str(cb7)
    out = tmp_path / "comic.cbz"
    assert save_state_for_file(orchestrator(permanent_delete=True), str(out), state) is None
    assert read_zip(out) == {"ComicInfo.xml": NEW_XML, "p01.jpg": b"A", "p02.jpg": b"B"}
    assert not cb7.exists()


def test_archive_of_unknown_format_is_refused_at_loading(tmp_path):
    """Fichier compté en erreur, original gardé ; cas normal, jamais écrit
    dans le journal des erreurs (erreur attendue)."""
    from modules.qt.error_log import is_expected_error
    cbr = tmp_path / "comic.cbr"
    cbr.write_bytes(b"ni rar ni rien")
    with pytest.raises(ValueError, match="batch_unknown_format") as caught:
        extract_non_cbz(str(cbr), ".cbr")
    assert is_expected_error(caught.value)
    assert cbr.exists()


def test_conversion_without_manifest_writes_nothing_and_keeps_original(tmp_path):
    """Pages de l'original jamais lues (pas de manifeste) : écrire produirait
    un CBZ ne contenant que ComicInfo.xml, puis l'original serait supprimé."""
    cbr = tmp_path / "comic.cbr"
    cbr.write_bytes(b"Rar! unreadable")
    out = tmp_path / "comic.cbz"
    state = types.SimpleNamespace(modified=True, images_data=[xml_entry()],
                                  _original_filepath=str(cbr))
    with pytest.raises(ValueError):
        save_state_for_file(orchestrator(permanent_delete=True), str(out), state)
    assert cbr.exists()
    assert not out.exists()
    assert leftovers(tmp_path) == []


def test_cbz_homonym_members_are_both_kept(tmp_path):
    """Deux membres de même nom : read(nom) rendrait le second pour les deux,
    le premier serait perdu. Les deux sont recopiés depuis le fichier, la
    couverture lue au chargement n'en remplaçant aucun."""
    import warnings
    cbz = tmp_path / "comic.cbz"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(cbz, "w") as zf:
            zf.writestr("p01.jpg", b"first")
            zf.writestr("p01.jpg", b"second")
    state = types.SimpleNamespace(modified=True, images_data=[
        {"orig_name": "p01.jpg", "bytes": b"first", "is_image": True},
        xml_entry(),
        {"orig_name": "__cbz_manifest__", "bytes": None, "_cbz_path": str(cbz)},
    ])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        save_state_for_file(orchestrator(), str(cbz), state)
    with zipfile.ZipFile(cbz) as zf:
        pages = sorted(zf.read(i) for i in zf.infolist() if i.filename == "p01.jpg")
    assert pages == [b"first", b"second"]
