"""Écriture des CBZ de sortie des traitements par lot.

- _interruptible_zip_writer : le CBZ est écrit dans un temporaire du même
  dossier et ne prend son nom qu'une fois complet ; un échec ou une
  interruption ne laisse jamais de CBZ tronqué portant un nom valide.
- _normalized_page_bytes : les pages en entiers 32 bits ne deviennent pas
  blanches, les autres pages sont écrites telles quelles.

batch_dialogs_qt.py importe PySide6 au niveau module : classes et fonctions
sont extraites par AST et exécutées seules, sans aucun import Qt. Toute
nouvelle dépendance doit être ajoutée à l'espace de noms ou à la liste extraite.
"""
import ast
import contextlib
import io
import os
import threading
import zipfile
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
NAMES = {"_BatchInterrupted", "_BatchCancelled", "_raise_if_batch_stopped",
         "_InterruptibleZip", "_interruptible_zip_writer",
         "_normalized_page_bytes", "_IncompleteCbz"}

_PAGE_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP",
                 ".bmp": "BMP", ".tiff": "TIFF", ".tif": "TIFF", ".gif": "GIF", ".avif": "AVIF"}


def _load():
    source = (ROOT / "modules" / "qt" / "batch_dialogs_qt.py").read_text(encoding="utf-8")
    nodes = [n for n in ast.parse(source).body
             if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in NAMES]
    assert {n.name for n in nodes} == NAMES
    ns = {"os": os, "io": io, "zipfile": zipfile, "contextlib": contextlib,
          "_batch_shutdown": threading.Event(), "_batch_local": threading.local(),
          "_PAGE_FORMATS": _PAGE_FORMATS}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "batch_dialogs_qt.py", "exec"), ns)
    return ns


_NS = _load()
writer = _NS["_interruptible_zip_writer"]
BatchInterrupted = _NS["_BatchInterrupted"]
BatchCancelled = _NS["_BatchCancelled"]
IncompleteCbz = _NS["_IncompleteCbz"]
normalized = _NS["_normalized_page_bytes"]


@pytest.fixture(autouse=True)
def reset_shutdown():
    _NS["_batch_shutdown"].clear()
    _NS["_batch_local"].cancel = None
    yield
    _NS["_batch_shutdown"].clear()
    _NS["_batch_local"].cancel = None


def leftovers(folder):
    return [n for n in os.listdir(folder) if n.startswith(".~mosaicview_")]


# ---------------------------------------------------------------------------
# _interruptible_zip_writer
# ---------------------------------------------------------------------------

def test_complete_write_creates_cbz(tmp_path):
    out = tmp_path / "comic.cbz"
    with writer(str(out)) as zf:
        zf.writestr("p01.jpg", b"page")
    with zipfile.ZipFile(out) as z:
        assert z.namelist() == ["p01.jpg"]
    assert leftovers(tmp_path) == []


def test_archive_comment_is_written(tmp_path):
    """Le commentaire d'archive (métadonnées ComicBookInfo) recopié par la
    recompression doit atteindre le vrai ZIP, pas l'enveloppe."""
    out = tmp_path / "comic.cbz"
    with writer(str(out)) as zf:
        zf.writestr("p01.jpg", b"page")
        zf.comment = b'{"ComicBookInfo/1.0": {}}'
    with zipfile.ZipFile(out) as z:
        assert z.comment == b'{"ComicBookInfo/1.0": {}}'


def test_cbz_name_appears_only_once_complete(tmp_path):
    out = tmp_path / "comic.cbz"
    with writer(str(out)) as zf:
        zf.writestr("p01.jpg", b"page")
        assert not out.exists()
        assert len(leftovers(tmp_path)) == 1


def test_error_leaves_no_cbz_and_no_temp(tmp_path):
    out = tmp_path / "comic.cbz"
    with pytest.raises(OSError):
        with writer(str(out)) as zf:
            zf.writestr("p01.jpg", b"page")
            raise OSError(28, "No space left on device")
    assert not out.exists()
    assert leftovers(tmp_path) == []


def test_incomplete_cbz_never_replaces_an_existing_file(tmp_path):
    """Deux lots sur le même dossier : le premier a écrit comic.cbz complet
    (et supprimé la source) pendant que le second, en retard, perdait des
    pages. Le CBZ incomplet du second, jeté par _IncompleteCbz, ne doit ni
    remplacer ni supprimer celui du premier."""
    out = tmp_path / "comic.cbz"
    with zipfile.ZipFile(out, "w") as done:
        done.writestr("p01.jpg", b"page 1")
        done.writestr("p02.jpg", b"page 2")
    complete = out.read_bytes()

    with pytest.raises(IncompleteCbz) as raised:
        with writer(str(out)) as zf:
            zf.writestr("p01.jpg", b"page 1")
            raise IncompleteCbz([("p02.jpg", "source deleted")])
    assert raised.value.failures == [("p02.jpg", "source deleted")]
    assert out.read_bytes() == complete
    assert leftovers(tmp_path) == []


def test_interruption_at_exit_leaves_nothing(tmp_path):
    out = tmp_path / "comic.cbz"
    with pytest.raises(BatchInterrupted):
        with writer(str(out)) as zf:
            zf.writestr("p01.jpg", b"page")
            _NS["_batch_shutdown"].set()
            zf.writestr("p02.jpg", b"page")
    assert not out.exists()
    assert leftovers(tmp_path) == []


def test_cancel_button_leaves_nothing_and_keeps_existing_file(tmp_path):
    """Annuler dans la fenêtre de progression : le CBZ en cours est jeté
    comme à la fermeture de l'application, sans toucher au fichier de même
    nom ; l'exception reste une _BatchInterrupted pour traverser les
    `except Exception` des boucles de lot."""
    out = tmp_path / "comic.cbz"
    out.write_bytes(b"existing")
    cancel = threading.Event()
    _NS["_batch_local"].cancel = cancel
    with pytest.raises(BatchCancelled) as raised:
        with writer(str(out)) as zf:
            zf.writestr("p01.jpg", b"page")
            cancel.set()
            zf.writestr("p02.jpg", b"page")
    assert isinstance(raised.value, BatchInterrupted)
    assert out.read_bytes() == b"existing"
    assert leftovers(tmp_path) == []


def test_recompress_temp_target_is_replaced(tmp_path):
    """La recompression écrit vers son propre temporaire, qui peut exister."""
    target = tmp_path / "comic.cbz.~recompress.tmp"
    target.write_bytes(b"old")
    with writer(str(target)) as zf:
        zf.writestr("p01.jpg", b"page")
    assert zipfile.is_zipfile(target)


# ---------------------------------------------------------------------------
# _normalized_page_bytes
# ---------------------------------------------------------------------------

def encoded(img, fmt):
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def test_rgb_page_is_written_unchanged():
    raw = encoded(Image.new("RGB", (8, 8), "red"), "JPEG")
    assert normalized(raw, Image.open(io.BytesIO(raw)), ".jpg") is raw


def test_32bit_tiff_page_is_not_white():
    img = Image.new("I", (8, 8), 0)
    img.putpixel((0, 0), 60000)
    raw = encoded(img, "TIFF")
    out = Image.open(io.BytesIO(normalized(raw, Image.open(io.BytesIO(raw)), ".tif")))
    assert out.format == "TIFF"
    assert out.mode == "L"
    assert out.getpixel((0, 0)) == 255
    assert out.getpixel((1, 1)) == 0


def test_cmyk_jpeg_page_becomes_rgb_jpeg():
    raw = encoded(Image.new("CMYK", (8, 8), (0, 255, 255, 0)), "JPEG")
    out = Image.open(io.BytesIO(normalized(raw, Image.open(io.BytesIO(raw)), ".jpg")))
    assert out.format == "JPEG"
    assert out.mode == "RGB"


def test_dpi_is_kept_on_normalized_page():
    img = Image.new("CMYK", (8, 8), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "JPEG", dpi=(300, 300))
    raw = buf.getvalue()
    out = Image.open(io.BytesIO(normalized(raw, Image.open(io.BytesIO(raw)), ".jpg")))
    assert round(out.info["dpi"][0]) == 300


def test_multipage_cmyk_tiff_keeps_every_page():
    """Réencodé, un TIFF multipage ne garderait que sa première page, et
    l'archive source est supprimée après conversion : il est écrit tel quel."""
    frames = [Image.new("CMYK", (8, 8), c) for c in
              ((255, 0, 0, 0), (0, 255, 0, 0), (0, 0, 255, 0))]
    buf = io.BytesIO()
    frames[0].save(buf, "TIFF", save_all=True, append_images=frames[1:])
    raw = buf.getvalue()
    out = normalized(raw, Image.open(io.BytesIO(raw)), ".tif")
    assert out is raw
    assert Image.open(io.BytesIO(out)).n_frames == 3
