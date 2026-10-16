"""Résolution (DPI) des pages écrites dans un CBZ.

Une page issue d'un PDF porte comme entry["dpi"] sa résolution de rendu, que
ses bytes ne contiennent pas. L'enregistrement doit l'inscrire dans le fichier
écrit sans jamais ré-encoder un JPEG (perte de qualité à chaque sauvegarde) et
sans modifier la page en mémoire (modification hors historique).

file_operations_qt.py importe PySide6 au niveau module : les fonctions et
constantes sont extraites par AST et exécutées seules, sans aucun import Qt.
Toute nouvelle dépendance de ces fonctions doit être ajoutée à _NS ou à la
liste extraite.
"""
import ast
import io
from pathlib import Path

from PIL import Image

from modules.qt.entries import open_page_image, save_image_to_bytes
from modules.qt.image_ops import has_several_frames

ROOT = Path(__file__).resolve().parent.parent
NAMES = {"_DPI_WRITABLE_EXTS", "_JPEG_EXTS", "jpeg_bytes_with_dpi", "_bytes_with_entry_dpi"}


def _load():
    source = (ROOT / "modules" / "qt" / "file_operations_qt.py").read_text(encoding="utf-8")
    nodes = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in NAMES:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in NAMES for t in node.targets):
            nodes.append(node)
    from modules.qt.archive_names import JPEG_EXTS
    ns = {"io": io, "Image": Image, "save_image_to_bytes": save_image_to_bytes,
          "open_page_image": open_page_image, "has_several_frames": has_several_frames,
          "JPEG_EXTS": JPEG_EXTS}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "file_operations_qt.py", "exec"), ns)
    assert NAMES <= set(ns)
    return ns


_NS = _load()
jpeg_bytes_with_dpi = _NS["jpeg_bytes_with_dpi"]
_bytes_with_entry_dpi = _NS["_bytes_with_entry_dpi"]


def jpeg(dpi=None):
    buf = io.BytesIO()
    kwargs = {"dpi": (dpi, dpi)} if dpi else {}
    Image.new("RGB", (40, 30), (200, 10, 10)).save(buf, "JPEG", quality=80, **kwargs)
    return buf.getvalue()


def read_dpi(data):
    dpi = Image.open(io.BytesIO(data)).info.get("dpi")
    return round(dpi[0]) if dpi else None


def strip_jfif(data):
    length = int.from_bytes(data[4:6], "big")
    return data[:2] + data[4 + length:]


# ---------------------------------------------------------------------------
# jpeg_bytes_with_dpi
# ---------------------------------------------------------------------------

def test_existing_jfif_density_is_rewritten_in_place():
    data = jpeg(96)
    out = jpeg_bytes_with_dpi(data, 300)
    assert read_dpi(out) == 300
    assert len(out) == len(data)
    assert out[18:] == data[18:]          # données d'image identiques


def test_missing_jfif_segment_is_added():
    data = strip_jfif(jpeg())
    assert read_dpi(data) is None
    out = jpeg_bytes_with_dpi(data, 150)
    assert read_dpi(out) == 150
    assert out.endswith(data[2:])         # tout l'original suit le segment ajouté


def test_image_pixels_are_unchanged():
    data = jpeg(72)
    before = Image.open(io.BytesIO(data)).convert("RGB").tobytes()
    after = Image.open(io.BytesIO(jpeg_bytes_with_dpi(data, 600))).convert("RGB").tobytes()
    assert before == after


def test_not_a_jpeg_returns_none():
    assert jpeg_bytes_with_dpi(b"\x89PNG not a jpeg", 300) is None
    assert jpeg_bytes_with_dpi(jpeg(), 0) is None


# ---------------------------------------------------------------------------
# _bytes_with_entry_dpi
# ---------------------------------------------------------------------------

def entry(data, ext, dpi):
    return {"orig_name": "page" + ext, "bytes": data, "extension": ext,
            "is_image": True, "img": None, "dpi": dpi}


def test_pdf_jpeg_page_gets_its_dpi_without_reencoding():
    data = jpeg()
    page = entry(data, ".jpg", 300)
    out = _bytes_with_entry_dpi(page)
    assert read_dpi(out) == 300
    assert out[18:] == data[18:]
    assert page["bytes"] is data          # page en mémoire non modifiée


def test_matching_dpi_returns_same_bytes():
    data = jpeg(300)
    assert _bytes_with_entry_dpi(entry(data, ".jpg", 300)) is data


def test_png_page_gets_its_dpi():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buf, "PNG")
    data = buf.getvalue()
    page = entry(data, ".png", 200)
    out = _bytes_with_entry_dpi(page)
    assert read_dpi(out) == 200
    assert page["bytes"] is data


def test_webp_page_is_never_reencoded():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buf, "WEBP")
    data = buf.getvalue()
    assert _bytes_with_entry_dpi(entry(data, ".webp", 300)) is data


def test_multiframe_image_is_written_as_is():
    data = jpeg()
    page = entry(data, ".tif", 300)
    page["is_multipage_tiff"] = True
    assert _bytes_with_entry_dpi(page) is data


def test_page_without_dpi_is_written_as_is():
    data = jpeg()
    assert _bytes_with_entry_dpi(entry(data, ".jpg", None)) is data
