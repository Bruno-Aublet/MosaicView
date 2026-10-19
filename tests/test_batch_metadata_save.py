"""Écriture d'un fichier par l'import de métadonnées en lot (_save_state_for_file).

- Tous les fichiers de l'archive d'origine sont recopiés (pages de tout format,
  .nfo…), sauf l'ancien ComicInfo.xml remplacé par le nouveau.
- Un échec d'écriture lève une exception (jamais avalée) : l'original reste
  intact et aucun temporaire ne traîne.
- L'annulation (annulation du lot, fermeture de l'application) arrête
  l'écriture proprement, original intact.

batch_metadata_dialog_qt.py importe PySide6 au niveau module : la méthode et
ses dépendances sont extraites par AST et exécutées seules, sans aucun import
Qt. Toute nouvelle dépendance doit être ajoutée à l'espace de noms ou à la
liste extraite.
"""
import ast
import io
import os
import sys
import tarfile
import threading
import types
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULE_NAMES = {"_is_comicinfo_name", "_SaveCancelled", "_CancellableZip"}


def _load():
    source = (ROOT / "modules" / "qt" / "batch_metadata_dialog_qt.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes = [n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in MODULE_NAMES]
    orchestrator = next(n for n in tree.body
                        if isinstance(n, ast.ClassDef) and n.name == "_BatchMetadataOrchestrator")
    nodes += [n for n in orchestrator.body
              if isinstance(n, ast.FunctionDef) and n.name == "_save_state_for_file"]
    assert len(nodes) == len(MODULE_NAMES) + 1
    from modules.qt.archive_names import open_zip
    ns = {"os": os, "_": lambda key, **kw: key, "open_zip": open_zip}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "batch_metadata_dialog_qt.py", "exec"), ns)
    return ns


_NS = _load()
save_state_for_file = _NS["_save_state_for_file"]
SaveCancelled = _NS["_SaveCancelled"]


@pytest.fixture(autouse=True)
def fake_config(monkeypatch):
    """Le gestionnaire de configuration n'existe pas hors de l'application."""
    import modules.qt.config_manager as config_manager
    monkeypatch.setattr(config_manager, "get_config_manager",
                        lambda: types.SimpleNamespace(get_zip_compression_level=lambda: 0))


def _fake_archive_loader():
    """Module de remplacement de modules.qt.archive_loader (qui importe Qt),
    importé par les branches CBR/CBT : il contient les vraies fonctions
    _check_member_size et _tar_info_read, extraites par AST, et la vraie
    constante MAX_MEMBER_SIZE d'archive_names (modifiable par un test)."""
    from modules.qt.archive_names import MAX_MEMBER_SIZE
    source = (ROOT / "modules" / "qt" / "archive_loader.py").read_text(encoding="utf-8")
    wanted = {"_check_member_size", "_tar_info_read"}
    nodes = [n for n in ast.parse(source).body
             if isinstance(n, ast.FunctionDef) and n.name in wanted]
    fake = types.ModuleType("modules.qt.archive_loader")
    fake.MAX_MEMBER_SIZE = MAX_MEMBER_SIZE
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "archive_loader.py", "exec"),
         fake.__dict__)
    return fake


@pytest.fixture(autouse=True)
def fake_archive_loader(monkeypatch):
    fake = _fake_archive_loader()
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
    fake_archive_loader.MAX_MEMBER_SIZE = 4
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


def test_conversion_with_original_deleted_reports_nothing(tmp_path):
    cbt = tmp_path / "comic.cbt"
    make_cbt(cbt, {"p01.jpg": b"A"})
    out = tmp_path / "comic.cbz"
    assert save_state_for_file(orchestrator(permanent_delete=True), str(out), cbt_state(cbt)) is None
    assert not cbt.exists()


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
    fake_archive_loader.MAX_MEMBER_SIZE = 4
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
    le premier serait perdu. Le premier est remplacé par la page du state
    (couverture lue au chargement), le second recopié tel quel."""
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
