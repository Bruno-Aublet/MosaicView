"""Boucles complètes des traitements par lot (batch_dialogs_qt) : CBR, CB7,
CBT et images → CBZ, images → un seul CBZ, recompression.

Les briques (écriture du CBZ, copie des pages, relecture, réservations) sont
testées ailleurs ; ici, c'est leur enchaînement, sur de vrais fichiers :
- la source n'est supprimée (Corbeille, ou définitivement si demandé)
  qu'une fois le CBZ produit complet et relu ;
- relecture en échec, page illisible, archive vide : source gardée, aucun
  CBZ laissé ;
- suppression de la source impossible : signalée, CBZ gardé ;
- annulation entre deux fichiers : le suivant n'est pas touché ;
- fichier mal nommé : simplement renommé, rien de supprimé ;
- recompression : l'original n'est remplacé qu'après le contrôle du CBZ
  écrit, avec ses membres (homonymes compris) et son commentaire d'archive.

batch_dialogs_qt.py et archive_loader.py importent PySide6 : les boucles et
leurs aides sont extraites par AST (tests/ast_loader.py) et exécutées dans
leur vrai thread de lot ; seules les fenêtres (progression, résumé) et les
signaux Qt sont remplacés. Les lecteurs RAR et 7z sont remplacés par des
lecteurs en mémoire (rarfile n'écrit pas de RAR ; la lecture 7z a ses
propres tests) : ce qui est vérifié est la boucle, pas la lecture.
"""
import contextlib
import gc
import io
import os
import re
import sys
import tarfile
import threading
import types
import zipfile

import pytest
from PIL import Image

from modules.qt import utils
from modules.qt.archive_names import IMAGE_EXTS, MAX_MEMBER_SIZE, open_zip, tar_entry_name
from modules.qt.archive_type_detector import detect_archive_type
from modules.qt.batch_claims import BatchClaims, free_name_candidates
from modules.qt.temp_files import exception_text, log_text, unique_log_path

import ast_loader
from fakes import Signal as _Signal, tr as _tr

LOOPS = {"_run_cbr_conversion", "_run_cb7_conversion", "_run_cbt_conversion",
         "_run_img_conversion", "_run_imgs_to_single_cbz", "_run_recompress",
         "_run_pdf_conversion"}
HELPERS = {"_start_batch_thread", "_claim_file", "_raise_if_batch_stopped", "_BatchInterrupted",
           "_BatchCancelled", "_batch_shutdown", "_batch_local", "_batch_threads",
           "_batch_thread_parents", "log_line", "log_title", "_failure_text",
           "_copy_pages_to_cbz", "_flat_names", "_unique_flat_name", "_normalized_page_bytes",
           "_PAGE_FORMATS", "_IncompleteCbz", "_UnusableImage", "_log_incomplete_cbz",
           "_InterruptibleZip", "_interruptible_zip_writer"}

RAR_MAGIC = b"Rar!\x1a\x07\x00"
SEVEN_ZIP_MAGIC = b"7z\xbc\xaf\x27\x1c"

# Contenu des archives RAR et 7z de test : chemin → [(nom, octets ou exception)]
FAKE_ARCHIVES = {}
READ_LOG = []    # membres RAR réellement lus


def natural_key(text):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", text)]


def png(color, size=(8, 6)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


RED, GREEN, BLUE = png((255, 0, 0)), png((0, 255, 0)), png((0, 0, 255))


# ---------------------------------------------------------------------------
# Lecteurs RAR et 7z en mémoire
# ---------------------------------------------------------------------------

class _BadRarFile(Exception):
    pass


class _RarCRCError(_BadRarFile):
    pass


class FakeRarFile:
    def __init__(self, path, mode="r"):
        self.filename = path
        self._members = dict(FAKE_ARCHIVES[path])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def namelist(self):
        return list(self._members)

    def getinfo(self, name):
        data = self._members[name]
        return types.SimpleNamespace(file_size=len(data) if isinstance(data, bytes) else 1)

    def read(self, name):
        READ_LOG.append(name)
        data = self._members[name]
        if isinstance(data, Exception):
            raise data
        return data


FAKE_RARFILE = types.SimpleNamespace(RarFile=FakeRarFile, BadRarFile=_BadRarFile,
                                     RarCRCError=_RarCRCError)


def fake_list_7z_files(path):
    return [name for name, _data in FAKE_ARCHIVES[path]]


def fake_read_7z_file(path, name):
    data = dict(FAKE_ARCHIVES[path])[name]
    if isinstance(data, Exception):
        raise data
    return data


def _not_expected(*_args):
    raise AssertionError("relecture d'un membre RAR par UnRAR non prévue dans ces tests")


def fake_archive_loader():
    """Module de remplacement de modules.qt.archive_loader (qui importe Qt) :
    vraies fonctions de lecture et de taille, extraites par AST."""
    module = types.ModuleType("modules.qt.archive_loader")
    module.__dict__.update(ast_loader.load(
        "archive_loader.py",
        {"_check_member_size", "_rar_read", "_tar_info_read", "_detect_zip_compression_state",
         "AlteredMemberError"},
        MAX_MEMBER_SIZE=MAX_MEMBER_SIZE, rarfile=FAKE_RARFILE, os=os, zipfile=zipfile,
        open_zip=open_zip, _rar_bytes_without_crc=_not_expected))
    return module


# ---------------------------------------------------------------------------
# Moteur PDF scripté (process séparé remplacé)
# ---------------------------------------------------------------------------

class _PdfEngine:
    """Répond aux messages du lot comme le process PDF, selon un scénario
    par fichier : pages rendues (octets), 'page_error' pour une page qui ne
    se rend pas, `error` pour une erreur du moteur, `password`, `owner`,
    `total` (nombre de pages annoncé, par défaut le nombre de pages)."""

    def reset(self):
        self.scripts, self.queue, self.spawned, self.current = {}, [], 0, None

    def handle(self, msg):
        if msg[0] == "batch_open":
            self.current = script = self.scripts[msg[1]]
            if script.get("password"):
                self.queue.append(("batch_ready", None))
            else:
                total = script.get("total", len(script["pages"]))
                self.queue.append(("batch_ready", total, [1.0] * total, None,
                                   script.get("owner", False)))
        elif msg[0] == "batch_convert":
            script, names = self.current, msg[1]
            total = len(names)
            for i, data in enumerate(script["pages"]):
                if data == "page_error":
                    self.queue.append(("page_error", i, "rendu impossible"))
                else:
                    self.queue.append(("batch_page", names[i], data, (i + 1) / total * 100,
                                       i + 1, total))
            self.queue.append(("error", "moteur en échec") if script.get("error") else ("done",))


PDF_ENGINE = _PdfEngine()
PDF_ENGINE.reset()


class _FakePdfConn:
    def poll(self, timeout=0):
        return bool(PDF_ENGINE.queue)

    def recv(self):
        return PDF_ENGINE.queue.pop(0)


def fake_pdf_module():
    """Module de remplacement de modules.qt.pdf_loading_qt (qui importe Qt) :
    vraie lecture du canal et vrais textes, process remplacé."""
    module = types.ModuleType("modules.qt.pdf_loading_qt")
    module.__dict__.update(ast_loader.load(
        "pdf_loading_qt.py",
        {"read_pdf_message", "_stopped", "PDF_STOPPED_MESSAGE", "pdf_image_ext",
         "pdf_page_error_text"}, _=_tr))

    def spawn():
        PDF_ENGINE.spawned += 1
        return (types.SimpleNamespace(is_alive=lambda: True),
                types.SimpleNamespace(put=PDF_ENGINE.handle), _FakePdfConn())
    module._spawn_pdf_process = spawn
    module._kill_pdf_process = lambda *a: None
    return module


def jpg(color):
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(buf, "JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fenêtres et signaux remplacés
# ---------------------------------------------------------------------------

class _Signals:
    def __init__(self):
        for name in ("update_filename", "update_progress", "update_page_bar", "update_thumb",
                     "conversion_done", "update_progress_img"):
            setattr(self, name, _Signal())


class _Progress:
    def __init__(self, *_args, **_kwargs):
        self.cancel_event = threading.Event()
        self.closed = False

    def show(self): pass
    def set_filename(self, *_a): pass
    def set_progress(self, *_a): pass
    def set_page_progress(self, *_a): pass
    def set_thumbnail(self, *_a): pass
    def mark_done(self): pass

    def close(self):
        self.closed = True


class Batch:
    """Un lot exécuté jusqu'à son résumé : fichiers mis à la Corbeille,
    résumé, fenêtres de progression, exceptions du thread."""

    def __init__(self, ns, logs_dir):
        self.ns, self.logs_dir = ns, logs_dir
        self.trashed, self.summaries, self.progress, self.thread_errors = [], [], [], []
        self.trash_hook = None

    def trash(self, path):
        if self.trash_hook:
            self.trash_hook(path)
        self.trashed.append(os.path.basename(path))
        os.remove(path)                        # Corbeille : le fichier quitte le dossier

    def callbacks(self):
        return {"natural_sort_key": natural_key, "safe_delete_file": self.trash,
                "get_mosaicview_temp_dir": lambda: str(self.logs_dir)}

    def run(self, loop, *args):
        self.ns[loop](None, *args)
        for thread in list(self.ns["_batch_threads"]):
            thread.join(30)
            assert not thread.is_alive(), "le lot ne s'est pas terminé"
        assert self.thread_errors == []
        [summary] = self.summaries
        assert all(p.closed for p in self.progress)
        return summary

    def log(self, summary):
        path = summary.get("log_path")
        return open(path, encoding="utf-8").read() if path else ""


@pytest.fixture
def batch(tmp_path, monkeypatch):
    FAKE_ARCHIVES.clear()
    READ_LOG.clear()
    import modules.qt.config_manager as config_manager
    monkeypatch.setattr(config_manager, "get_config_manager",
                        lambda: types.SimpleNamespace(get_zip_compression_level=lambda: 0))
    loader = fake_archive_loader()
    monkeypatch.setitem(sys.modules, "modules.qt.archive_loader", loader)
    PDF_ENGINE.reset()
    monkeypatch.setitem(sys.modules, "modules.qt.pdf_loading_qt", fake_pdf_module())
    logs = tmp_path / "logs"
    logs.mkdir()
    holder = {}

    def make_progress(*args, **kwargs):
        holder["batch"].progress.append(_Progress())
        return holder["batch"].progress[-1]

    def summary(*args):
        holder["batch"].summaries.append(args[1])

    ns = ast_loader.load(
        "batch_dialogs_qt.py", LOOPS | HELPERS,
        os=os, io=io, gc=gc, contextlib=contextlib, threading=threading, zipfile=zipfile,
        Image=Image, rarfile=FAKE_RARFILE, detect_archive_type=detect_archive_type,
        free_name_candidates=free_name_candidates, IMAGE_EXTS=IMAGE_EXTS, open_zip=open_zip,
        _tar_entry_name=tar_entry_name, _rar_read=loader._rar_read,
        _check_member_size=loader._check_member_size,
        _tar_info_read=loader._tar_info_read, _list_7z_files=fake_list_7z_files,
        _read_7z_file=fake_read_7z_file, unique_log_path=unique_log_path, log_text=log_text,
        exception_text=exception_text, _=_tr, _wt=_tr,
        QApplication=types.SimpleNamespace(processEvents=lambda: None),
        _start_batch_claims=lambda parent, files: BatchClaims.start(files),
        _ProgressDialog=make_progress, _ThreadSignals=_Signals,
        _register_batch=lambda *a: None, _unregister_batch=lambda *a: None,
        _pil_to_qimage=lambda *a: None,
        show_batch_cbr_summary=summary, show_batch_cb7_summary=summary,
        show_batch_cbt_summary=summary, show_batch_img_summary=summary,
        show_batch_recompress_summary=summary, show_batch_pdf_summary=summary,
        WrittenFileCheckError=utils.WrittenFileCheckError)
    holder["batch"] = Batch(ns, logs)
    monkeypatch.setattr(threading, "excepthook",
                        lambda args: holder["batch"].thread_errors.append(args.exc_type))
    folder = tmp_path / "comics"
    folder.mkdir()
    holder["batch"].folder = folder
    return holder["batch"]


# ---------------------------------------------------------------------------
# Fichiers de test
# ---------------------------------------------------------------------------

def make_cbt(path, pages):
    with tarfile.open(path, "w") as tf:
        for name, data in pages:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return str(path)


def make_zip(path, pages, comment=b"", compression=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(path, "w", compression) as zf:
        for name, data in pages:
            zf.writestr(name, data)
        zf.comment = comment
    return str(path)


def make_fake(path, magic, pages):
    path.write_bytes(magic + b"\x00" * 32)
    FAKE_ARCHIVES[str(path)] = list(pages)
    return str(path)


def cbz_pages(path):
    with zipfile.ZipFile(path) as zf:
        return [(info.filename, zf.read(info)) for info in zf.infolist()]


def content(folder):
    return sorted(os.listdir(folder))


def no_temp_left(folder):
    return not [n for n in os.listdir(folder) if n.startswith(utils.TEMP_FILE_PREFIX)]


def fail_reread(monkeypatch):
    def fail(path, expected_count=None, shown_path=None):
        raise utils.WrittenFileCheckError(shown_path or path, detail="relecture refusée par le test")
    monkeypatch.setattr(utils, "verify_written_cbz", fail)


def released(*paths):
    claims = BatchClaims.start(list(paths))
    if claims is None:
        return False
    claims.finish()
    return True


def run_archive_loop(batch, loop, files, permanent=False):
    return batch.run(loop, files, str(batch.folder), [str(batch.folder)], batch.callbacks(),
                     permanent)


# ---------------------------------------------------------------------------
# CBT → CBZ (vrai TAR)
# ---------------------------------------------------------------------------

def test_cbt_converted_then_sent_to_the_trash(batch):
    src = make_cbt(batch.folder / "Aaaa.cbt", [("p01.png", RED), ("p02.png", GREEN)])
    summary = run_archive_loop(batch, "_run_cbt_conversion", [src])
    assert cbz_pages(batch.folder / "Aaaa.cbz") == [("p01.png", RED), ("p02.png", GREEN)]
    assert content(batch.folder) == ["Aaaa.cbz"]
    assert batch.trashed == ["Aaaa.cbt"]
    assert summary["converted_count"] == 1 and summary["errors_count"] == 0
    assert released(src)


def test_cbt_permanent_deletion_skips_the_trash(batch):
    src = make_cbt(batch.folder / "Aaaa.cbt", [("p01.png", RED)])
    run_archive_loop(batch, "_run_cbt_conversion", [src], permanent=True)
    assert content(batch.folder) == ["Aaaa.cbz"]
    assert batch.trashed == []


def test_cbt_kept_when_the_new_cbz_does_not_reread(batch, monkeypatch):
    fail_reread(monkeypatch)
    src = make_cbt(batch.folder / "Aaaa.cbt", [("p01.png", RED), ("p02.png", GREEN)])
    summary = run_archive_loop(batch, "_run_cbt_conversion", [src])
    assert content(batch.folder) == ["Aaaa.cbt"]          # aucun CBZ, source gardée
    assert batch.trashed == []
    assert summary["converted_count"] == 0 and summary["errors_count"] == 1


def test_cbt_with_an_unreadable_page_is_kept(batch, monkeypatch):
    # Espace de noms des lecteurs extraits (celui de _check_member_size)
    readers = sys.modules["modules.qt.archive_loader"]._check_member_size.__globals__
    monkeypatch.setitem(readers, "MAX_MEMBER_SIZE", 500)
    src = make_cbt(batch.folder / "Aaaa.cbt",
                   [("p01.png", RED), ("p02.png", b"x" * 5000), ("p03.png", BLUE)])
    summary = run_archive_loop(batch, "_run_cbt_conversion", [src])
    assert content(batch.folder) == ["Aaaa.cbt"]
    assert no_temp_left(batch.folder)
    assert batch.trashed == []
    assert "messages.errors.batch_incomplete_kept" in batch.log(summary)


def test_cbt_whose_deletion_fails_is_reported_and_the_cbz_kept(batch):
    src = make_cbt(batch.folder / "Aaaa.cbt", [("p01.png", RED)])

    def refuse(path):
        raise PermissionError("Corbeille refusée")
    batch.trash_hook = refuse
    summary = run_archive_loop(batch, "_run_cbt_conversion", [src])
    assert content(batch.folder) == ["Aaaa.cbt", "Aaaa.cbz"]
    assert cbz_pages(batch.folder / "Aaaa.cbz") == [("p01.png", RED)]
    assert "messages.errors.batch_source_delete_failed" in batch.log(summary)


def test_cancel_after_the_first_file_leaves_the_next_one_untouched(batch):
    first = make_cbt(batch.folder / "Aaaa.cbt", [("p01.png", RED)])
    second = make_cbt(batch.folder / "Bbbb.cbt", [("p01.png", GREEN)])
    before = open(second, "rb").read()
    batch.trash_hook = lambda path: batch.progress[0].cancel_event.set()   # bouton Annuler
    summary = run_archive_loop(batch, "_run_cbt_conversion", [first, second])
    assert content(batch.folder) == ["Aaaa.cbz", "Bbbb.cbt"]
    assert open(second, "rb").read() == before
    assert summary["converted_count"] == 1 and summary["total"] == 2
    assert released(first, second)


def test_empty_cbt_is_reported_and_kept(batch):
    src = make_cbt(batch.folder / "Aaaa.cbt", [])
    summary = run_archive_loop(batch, "_run_cbt_conversion", [src])
    assert content(batch.folder) == ["Aaaa.cbt"]
    assert "messages.errors.batch_empty_archive" in batch.log(summary)


def test_zip_named_cbt_is_only_renamed(batch):
    src = make_zip(batch.folder / "Aaaa.cbt", [("p01.png", RED)])
    before = open(src, "rb").read()
    summary = run_archive_loop(batch, "_run_cbt_conversion", [src])
    assert content(batch.folder) == ["Aaaa.cbz"]
    assert (batch.folder / "Aaaa.cbz").read_bytes() == before
    assert batch.trashed == []
    assert summary["renamed_cbz"] == 1 and summary["converted_count"] == 0


def test_cbt_pages_of_two_chapters_keep_reading_order_and_homonyms(batch):
    src = make_cbt(batch.folder / "Aaaa.cbt",
                   [("Ch2/001.png", BLUE), ("Ch1/001.png", RED), ("Ch1/002.png", GREEN)])
    run_archive_loop(batch, "_run_cbt_conversion", [src])
    pages = cbz_pages(batch.folder / "Aaaa.cbz")
    assert [data for _name, data in pages] == [RED, GREEN, BLUE]
    assert len({name for name, _data in pages}) == 3


# ---------------------------------------------------------------------------
# CBR → CBZ (lecteur RAR en mémoire)
# ---------------------------------------------------------------------------

def test_cbr_converted_then_sent_to_the_trash(batch):
    src = make_fake(batch.folder / "Aaaa.cbr", RAR_MAGIC, [("p01.png", RED), ("p02.png", GREEN)])
    summary = run_archive_loop(batch, "_run_cbr_conversion", [src])
    assert cbz_pages(batch.folder / "Aaaa.cbz") == [("p01.png", RED), ("p02.png", GREEN)]
    assert batch.trashed == ["Aaaa.cbr"]
    assert summary["converted_count"] == 1


def test_cbr_with_an_unreadable_page_is_kept(batch):
    src = make_fake(batch.folder / "Aaaa.cbr", RAR_MAGIC,
                    [("p01.png", RED), ("p02.png", OSError("membre illisible"))])
    summary = run_archive_loop(batch, "_run_cbr_conversion", [src])
    assert content(batch.folder) == ["Aaaa.cbr"]
    assert batch.trashed == []
    assert "membre illisible" in batch.log(summary)


def test_cbr_kept_when_the_new_cbz_does_not_reread(batch, monkeypatch):
    fail_reread(monkeypatch)
    src = make_fake(batch.folder / "Aaaa.cbr", RAR_MAGIC, [("p01.png", RED)])
    run_archive_loop(batch, "_run_cbr_conversion", [src], permanent=True)
    assert content(batch.folder) == ["Aaaa.cbr"]


def test_zip_named_cbr_is_only_renamed(batch):
    src = make_zip(batch.folder / "Aaaa.cbr", [("p01.png", RED)])
    summary = run_archive_loop(batch, "_run_cbr_conversion", [src])
    assert content(batch.folder) == ["Aaaa.cbz"]
    assert batch.trashed == []
    assert summary["renamed_cbz"] == 1


def test_unknown_file_named_cbr_is_reported_and_kept(batch):
    src = batch.folder / "Aaaa.cbr"
    src.write_bytes(b"ni archive ni image")
    summary = run_archive_loop(batch, "_run_cbr_conversion", [str(src)])
    assert content(batch.folder) == ["Aaaa.cbr"]
    assert "messages.errors.batch_unknown_format" in batch.log(summary)


# ---------------------------------------------------------------------------
# CB7 → CBZ (lecteur 7z en mémoire)
# ---------------------------------------------------------------------------

def test_cb7_converted_then_sent_to_the_trash(batch):
    src = make_fake(batch.folder / "Aaaa.cb7", SEVEN_ZIP_MAGIC, [("p01.png", RED)])
    run_archive_loop(batch, "_run_cb7_conversion", [src])
    assert cbz_pages(batch.folder / "Aaaa.cbz") == [("p01.png", RED)]
    assert batch.trashed == ["Aaaa.cb7"]


def test_cb7_kept_when_the_new_cbz_does_not_reread(batch, monkeypatch):
    fail_reread(monkeypatch)
    src = make_fake(batch.folder / "Aaaa.cb7", SEVEN_ZIP_MAGIC, [("p01.png", RED)])
    run_archive_loop(batch, "_run_cb7_conversion", [src])
    assert content(batch.folder) == ["Aaaa.cb7"]
    assert batch.trashed == []


def test_cb7_with_an_unreadable_page_is_kept(batch):
    src = make_fake(batch.folder / "Aaaa.cb7", SEVEN_ZIP_MAGIC,
                    [("p01.png", RED), ("p02.png", OSError("7-Zip : erreur de données"))])
    run_archive_loop(batch, "_run_cb7_conversion", [src])
    assert content(batch.folder) == ["Aaaa.cb7"]


# ---------------------------------------------------------------------------
# Images → un CBZ par image
# ---------------------------------------------------------------------------

def test_image_converted_then_sent_to_the_trash(batch):
    src = batch.folder / "Aaaa.png"
    src.write_bytes(RED)
    summary = run_archive_loop(batch, "_run_img_conversion", [str(src)])
    assert cbz_pages(batch.folder / "Aaaa.cbz") == [("Aaaa.png", RED)]
    assert batch.trashed == ["Aaaa.png"]
    assert summary["converted_count"] == 1


def test_image_named_with_the_wrong_extension_gets_its_real_one(batch):
    src = batch.folder / "Aaaa.jpg"
    src.write_bytes(RED)                     # PNG nommé .jpg
    run_archive_loop(batch, "_run_img_conversion", [str(src)])
    assert cbz_pages(batch.folder / "Aaaa.cbz") == [("Aaaa.png", RED)]


def test_image_kept_when_the_new_cbz_does_not_reread(batch, monkeypatch):
    fail_reread(monkeypatch)
    src = batch.folder / "Aaaa.png"
    src.write_bytes(RED)
    run_archive_loop(batch, "_run_img_conversion", [str(src)])
    assert content(batch.folder) == ["Aaaa.png"]
    assert batch.trashed == []


@pytest.fixture
def error_journal(monkeypatch):
    """Erreurs écrites dans le journal des erreurs (défauts du programme)."""
    from modules.qt import error_log
    logged = []
    monkeypatch.setattr(error_log, "log_exception", lambda exc, *a: logged.append(exc))
    return logged


def test_unreadable_image_is_reported_and_kept(batch, error_journal):
    src = batch.folder / "Aaaa.png"
    src.write_bytes(RED[:20])
    summary = run_archive_loop(batch, "_run_img_conversion", [str(src)])
    assert content(batch.folder) == ["Aaaa.png"]
    assert "messages.errors.batch_image_unreadable" in batch.log(summary)
    assert error_journal == []     # cas normal : journal du lot seulement


def test_icon_and_multi_image_files_are_refused_without_error_journal(batch, error_journal):
    ico = batch.folder / "Aaaa.ico"
    Image.new("RGB", (16, 16), (1, 2, 3)).save(ico, "ICO")
    gif = batch.folder / "Bbbb.gif"
    frames = [Image.new("RGB", (8, 8), color) for color in ((255, 0, 0), (0, 0, 255))]
    frames[0].save(gif, "GIF", save_all=True, append_images=frames[1:])
    summary = run_archive_loop(batch, "_run_img_conversion", [str(ico), str(gif)])
    assert content(batch.folder) == ["Aaaa.ico", "Bbbb.gif"]
    log = batch.log(summary)
    assert "messages.errors.batch_image_ico" in log
    assert "messages.errors.batch_image_multiframe" in log
    assert error_journal == []


# ---------------------------------------------------------------------------
# Images → un seul CBZ
# ---------------------------------------------------------------------------

def run_single(batch, files, permanent=False):
    return batch.run("_run_imgs_to_single_cbz", files, str(batch.folder), batch.callbacks(),
                     permanent)


def test_images_gathered_in_one_cbz_then_sent_to_the_trash(batch):
    files = []
    for name, data in (("p02.png", GREEN), ("p01.png", RED), ("p03.png", BLUE)):
        (batch.folder / name).write_bytes(data)
        files.append(str(batch.folder / name))
    summary = run_single(batch, files)
    assert cbz_pages(batch.folder / "comics.cbz") == [("p01.png", RED), ("p02.png", GREEN),
                                                      ("p03.png", BLUE)]
    assert content(batch.folder) == ["comics.cbz"]
    assert sorted(batch.trashed) == ["p01.png", "p02.png", "p03.png"]
    assert summary["converted_count"] == 3


def test_unreadable_image_is_kept_and_the_others_gathered(batch, error_journal):
    (batch.folder / "p01.png").write_bytes(RED)
    (batch.folder / "p02.png").write_bytes(RED[:20])
    files = [str(batch.folder / "p01.png"), str(batch.folder / "p02.png")]
    summary = run_single(batch, files)
    assert cbz_pages(batch.folder / "comics.cbz") == [("p01.png", RED)]
    assert content(batch.folder) == ["comics.cbz", "p02.png"]
    assert batch.trashed == ["p01.png"]
    assert summary["errors_count"] == 1
    assert error_journal == []


def test_no_image_deleted_when_the_single_cbz_does_not_reread(batch, monkeypatch):
    fail_reread(monkeypatch)
    for name, data in (("p01.png", RED), ("p02.png", GREEN)):
        (batch.folder / name).write_bytes(data)
    run_single(batch, [str(batch.folder / "p01.png"), str(batch.folder / "p02.png")],
               permanent=True)
    assert content(batch.folder) == ["p01.png", "p02.png"]
    assert batch.trashed == []


def test_no_cbz_created_when_no_image_is_readable(batch):
    (batch.folder / "p01.png").write_bytes(RED[:20])
    run_single(batch, [str(batch.folder / "p01.png")])
    assert content(batch.folder) == ["p01.png"]
    assert no_temp_left(batch.folder)


# ---------------------------------------------------------------------------
# Recompression (remplacement de l'original sur place)
# ---------------------------------------------------------------------------

def run_recompress(batch, files, level=0):
    return batch.run("_run_recompress", files, str(batch.folder), [str(batch.folder)],
                     batch.callbacks(), level)


COMMENT = b'{"ComicBookInfo/1.0": {}}'


# Homonymes voulus (deux membres p01.png) : zipfile avertit à leur écriture,
# par make_zip comme par la recompression
@pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")
def test_recompression_keeps_every_member_and_the_archive_comment(batch):
    src = make_zip(batch.folder / "Aaaa.cbz",
                   [("p01.png", RED), ("p01.png", GREEN), ("info.nfo", b"texte")],
                   comment=COMMENT)
    summary = run_recompress(batch, [src], level=0)
    assert cbz_pages(src) == [("p01.png", RED), ("p01.png", GREEN), ("info.nfo", b"texte")]
    with zipfile.ZipFile(src) as zf:
        assert zf.comment == COMMENT
        assert all(i.compress_type == zipfile.ZIP_STORED for i in zf.infolist())
    assert summary["recompressed_count"] == 1
    assert content(batch.folder) == ["Aaaa.cbz"]
    assert batch.trashed == []


def test_cbz_already_at_the_wanted_level_is_left_untouched(batch):
    src = make_zip(batch.folder / "Aaaa.cbz", [("p01.png", RED)],
                   compression=zipfile.ZIP_STORED)
    before = open(src, "rb").read()
    summary = run_recompress(batch, [src], level=0)
    assert open(src, "rb").read() == before
    assert summary["already_optimal_count"] == 1


def test_original_kept_when_the_recompressed_cbz_fails_its_check(batch, monkeypatch):
    src = make_zip(batch.folder / "Aaaa.cbz", [("p01.png", RED), ("p02.png", GREEN)])
    before = open(src, "rb").read()

    def fail(path, expected_count, shown_path=None):
        raise utils.WrittenFileCheckError(shown_path or path, detail="structure refusée")
    monkeypatch.setattr(utils, "check_written_cbz_structure", fail)
    summary = run_recompress(batch, [src], level=0)
    assert open(src, "rb").read() == before
    assert content(batch.folder) == ["Aaaa.cbz"]
    assert summary["recompressed_count"] == 0 and summary["errors_count"] == 1


def test_real_non_cbz_archive_is_ignored(batch):
    src = make_cbt(batch.folder / "Aaaa.cbr", [("p01.png", RED)])
    before = open(src, "rb").read()
    summary = run_recompress(batch, [src], level=0)
    assert open(src, "rb").read() == before
    assert summary["ignored_count"] == 1


def test_zip_named_cbr_is_renamed_then_recompressed(batch):
    src = make_zip(batch.folder / "Aaaa.cbr", [("p01.png", RED)])
    summary = run_recompress(batch, [src], level=0)
    assert content(batch.folder) == ["Aaaa.cbz"]
    assert cbz_pages(batch.folder / "Aaaa.cbz") == [("p01.png", RED)]
    assert summary["renamed_count"] == 1 and summary["recompressed_count"] == 1


# ---------------------------------------------------------------------------
# Limite de taille par fichier de l'archive (MAX_MEMBER_SIZE), vignette comprise
# ---------------------------------------------------------------------------
# La limite vaut pour chaque fichier contenu dans l'archive, jamais pour
# l'archive entière : ici abaissée à 500 octets pour le test.

@pytest.fixture
def small_limit(monkeypatch):
    readers = sys.modules["modules.qt.archive_loader"]._check_member_size.__globals__
    monkeypatch.setitem(readers, "MAX_MEMBER_SIZE", 500)


def test_recompression_refuses_an_oversized_member_and_keeps_the_original(batch, small_limit):
    src = make_zip(batch.folder / "Aaaa.cbz", [("a.png", b"x" * 5000), ("p01.png", RED)])
    before = open(src, "rb").read()
    summary = run_recompress(batch, [src], level=0)
    assert open(src, "rb").read() == before
    assert content(batch.folder) == ["Aaaa.cbz"]          # aucun temporaire laissé
    assert summary["recompressed_count"] == 0 and summary["errors_count"] == 1


def test_large_archive_of_normal_pages_is_recompressed(batch, small_limit):
    """Archive plus grosse que la limite, mais aucun fichier ne la dépasse."""
    pages = [(f"p{i:02d}.png", png((i, i, i))) for i in range(1, 21)]
    src = make_zip(batch.folder / "Aaaa.cbz", pages)
    assert os.path.getsize(src) > 500
    summary = run_recompress(batch, [src], level=0)
    assert cbz_pages(src) == pages
    assert summary["recompressed_count"] == 1


def test_cbr_thumbnail_never_reads_an_oversized_page(batch, small_limit):
    src = make_fake(batch.folder / "Aaaa.cbr", RAR_MAGIC,
                    [("p01.png", b"x" * 5000), ("p02.png", RED)])
    run_archive_loop(batch, "_run_cbr_conversion", [src])
    assert "p01.png" not in READ_LOG
    assert content(batch.folder) == ["Aaaa.cbr"]          # page refusée : source gardée


# ---------------------------------------------------------------------------
# PDF → CBZ (moteur PDF scripté)
# ---------------------------------------------------------------------------

def make_pdf(batch, name, **script):
    path = batch.folder / name
    path.write_bytes(b"%PDF-1.4")
    PDF_ENGINE.scripts[str(path)] = script
    return str(path)


def run_pdf(batch, files, permanent=False):
    return batch.run("_run_pdf_conversion", files, str(batch.folder), [str(batch.folder)],
                     batch.callbacks(), permanent, types.SimpleNamespace(renumber_mode=0))


def test_pdf_converted_then_sent_to_the_trash(batch):
    a, b = jpg((255, 0, 0)), jpg((0, 0, 255))
    src = make_pdf(batch, "Aaaa.pdf", pages=[a, b])
    summary = run_pdf(batch, [src])
    assert cbz_pages(batch.folder / "Aaaa.cbz") == [("01.jpg", a), ("02.jpg", b)]
    assert batch.trashed == ["Aaaa.pdf"]
    assert summary["converted_count"] == 1 and summary["errors_count"] == 0
    assert PDF_ENGINE.spawned == 1


def test_pdf_page_in_png_keeps_its_real_extension(batch):
    src = make_pdf(batch, "Aaaa.pdf", pages=[RED])
    run_pdf(batch, [src])
    assert cbz_pages(batch.folder / "Aaaa.cbz") == [("01.png", RED)]


def test_pdf_with_a_page_that_does_not_render_is_kept(batch):
    src = make_pdf(batch, "Aaaa.pdf", pages=[jpg((1, 1, 1)), "page_error", jpg((2, 2, 2))])
    summary = run_pdf(batch, [src])
    assert content(batch.folder) == ["Aaaa.pdf"]
    assert batch.trashed == []
    assert "messages.errors.batch_incomplete_kept" in batch.log(summary)
    assert PDF_ENGINE.spawned == 1                     # page non rendue : moteur gardé


def test_pdf_with_missing_pages_is_kept(batch):
    src = make_pdf(batch, "Aaaa.pdf", pages=[jpg((1, 1, 1))], total=3)
    summary = run_pdf(batch, [src])
    assert content(batch.folder) == ["Aaaa.pdf"]
    assert "messages.errors.batch_incomplete_kept" in batch.log(summary)


def test_pdf_engine_error_keeps_the_pdf_and_replaces_the_engine(batch):
    first = make_pdf(batch, "Aaaa.pdf", pages=[jpg((1, 1, 1))], error=True)
    second = make_pdf(batch, "Bbbb.pdf", pages=[jpg((2, 2, 2))])
    summary = run_pdf(batch, [first, second])
    assert content(batch.folder) == ["Aaaa.pdf", "Bbbb.cbz"]
    assert PDF_ENGINE.spawned == 2                     # moteur remplacé pour le suivant
    assert summary["converted_count"] == 1 and summary["errors_count"] == 1


def test_password_protected_pdf_is_skipped_and_kept(batch):
    src = make_pdf(batch, "Aaaa.pdf", pages=[], password=True)
    summary = run_pdf(batch, [src])
    assert content(batch.folder) == ["Aaaa.pdf"]
    assert "messages.errors.pdf_encrypted_skipped" in batch.log(summary)


def test_pdf_protected_by_its_owner_is_converted_but_never_deleted(batch):
    src = make_pdf(batch, "Aaaa.pdf", pages=[jpg((1, 1, 1))], owner=True)
    summary = run_pdf(batch, [src], permanent=True)
    assert content(batch.folder) == ["Aaaa.cbz", "Aaaa.pdf"]
    assert summary["converted_count"] == 1


def test_pdf_kept_when_the_new_cbz_does_not_reread(batch, monkeypatch):
    fail_reread(monkeypatch)
    src = make_pdf(batch, "Aaaa.pdf", pages=[jpg((1, 1, 1))])
    summary = run_pdf(batch, [src])
    assert content(batch.folder) == ["Aaaa.pdf"]
    assert summary["converted_count"] == 0 and summary["errors_count"] == 1
    assert PDF_ENGINE.spawned == 1                     # le moteur avait fini son document
