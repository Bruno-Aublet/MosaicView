"""Jointure d'images fixes : la planche est encodée par l'encodeur commun
(entries.encode_page) dans le format des pages jointes
(merge_dialog_qt.MergeDialog._finish_join).

Vérifié sur la planche produite, relue :
- BMP transparents : planche BMP qui garde la transparence ;
- TIFF : planche compressée (LZW), jamais non compressée ;
- profil ICC repris seulement s'il est le même pour toutes les pages ;
- résolution des pages reprise ;
- JPEG : qualité 95.

merge_dialog_qt.py importe PySide6 : la méthode est extraite par AST
(tests/ast_loader.load_methods), la fenêtre est remplacée.
"""
import io
import types

from PIL import Image, ImageCms

import ast_loader
from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import create_entry, encode_page, image_dpi, page_icc_profile
from modules.qt.image_ops import _has_alpha, is_animated_entry, merge_images_2d

join_output = ast_loader.load(
    "merge_dialog_qt.py", {"join_output", "_JOIN_OUTPUTS", "_JOIN_ALPHA_FORMATS"},
    _has_alpha=_has_alpha)["join_output"]

Base = ast_loader.load_methods(
    "merge_dialog_qt.py", "MergeDialog", {"_finish_join"}, join_output=join_output,
    is_animated_entry=is_animated_entry, merge_images_2d=merge_images_2d,
    _get_next_collage_number=lambda state: 1, _=lambda key: "Collage",
    image_dpi=image_dpi, page_icc_profile=page_icc_profile, encode_page=encode_page,
    create_entry=create_entry, IMAGE_EXTS=IMAGE_EXTS)

SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


class Join(Base):
    def __init__(self):
        self._state = types.SimpleNamespace(doc_generation=1, operation_running=lambda: False)
        self._doc_generation = 1
        self.result = None

    def _image_positions(self, positions):
        return [{"entry": {"img": Image.open(io.BytesIO(p["entry"]["bytes"]))},
                 "x": p["x"], "y": p["y"]} for p in positions]

    def parent(self):
        return None

    def close(self):
        pass

    def _ask_delete_sources(self, parent, new_entry):
        self.result = new_entry


def encoded(img, fmt, **kwargs):
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def joined(*pages):
    """Planche des pages `(nom, bytes)` côte à côte, relue."""
    positions, x = [], 0
    for name, data in pages:
        entry = create_entry(name, data, IMAGE_EXTS)
        positions.append({"entry": entry, "x": x, "y": 0})
        x += Image.open(io.BytesIO(data)).width
    join = Join()
    join._finish_join(positions, "keep_original")
    assert join.result is not None
    data = join.result["bytes"]
    img = Image.open(io.BytesIO(data))
    img.load()
    return join.result, img, data


def transparent(color):
    img = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    img.paste(color + (255,), (2, 2, 6, 6))
    return img


def test_transparent_bmp_pages_give_a_transparent_bmp():
    a = encode_page(transparent((255, 0, 0)), "BMP")
    b = encode_page(transparent((0, 0, 255)), "BMP")
    entry, img, _data = joined(("p01.bmp", a), ("p02.bmp", b))
    assert entry["orig_name"].endswith(".bmp")
    assert img.mode == "RGBA"
    assert img.getpixel((0, 0))[3] == 0
    assert img.getpixel((3, 3)) == (255, 0, 0, 255)


def test_tiff_pages_give_a_compressed_tiff():
    a = encoded(Image.new("RGB", (8, 8), (10, 20, 30)), "TIFF")
    b = encoded(Image.new("RGB", (8, 8), (40, 50, 60)), "TIFF")
    _entry, img, _data = joined(("p01.tif", a), ("p02.tif", b))
    assert img.format == "TIFF"
    assert img.info["compression"] == "tiff_lzw"


def test_common_profile_is_kept():
    a = encoded(Image.new("RGB", (8, 8)), "PNG", icc_profile=SRGB)
    b = encoded(Image.new("RGB", (8, 8)), "PNG", icc_profile=SRGB)
    _entry, img, _data = joined(("p01.png", a), ("p02.png", b))
    assert img.info.get("icc_profile") == SRGB


def test_different_profiles_give_no_profile():
    a = encoded(Image.new("RGB", (8, 8)), "PNG", icc_profile=SRGB)
    b = encoded(Image.new("RGB", (8, 8)), "PNG")
    _entry, img, _data = joined(("p01.png", a), ("p02.png", b))
    assert not img.info.get("icc_profile")


def test_resolution_of_the_pages_is_kept():
    a = encoded(Image.new("RGB", (8, 8)), "PNG", dpi=(300, 300))
    b = encoded(Image.new("RGB", (8, 8)), "PNG", dpi=(300, 300))
    _entry, img, _data = joined(("p01.png", a), ("p02.png", b))
    assert [round(float(v)) for v in img.info["dpi"]] == [300, 300]


# ---------------------------------------------------------------------------
# Décision commune à la jointure et à son aperçu (join_output)
# ---------------------------------------------------------------------------

def positions_of(*extensions):
    return [{"entry": {"extension": ext}, "x": 0, "y": 0} for ext in extensions]


def test_same_format_pages_give_their_format():
    assert join_output(positions_of(".jfif", ".jfif"), []) == ("JPEG", ".jpg", False)
    assert join_output(positions_of(".tif", ".TIF"), []) == ("TIFF", ".tiff", False)


def test_mixed_formats_give_png():
    assert join_output(positions_of(".jpg", ".png"), [])[:2] == ("PNG", ".png")


def test_transparency_is_kept_only_where_the_format_stores_it():
    alpha = [transparent((255, 0, 0))]
    assert join_output(positions_of(".bmp", ".bmp"), alpha)[2] is True
    assert join_output(positions_of(".png", ".png"), alpha)[2] is True
    assert join_output(positions_of(".jpg", ".jpg"), alpha)[2] is False
    assert join_output(positions_of(".png", ".png"), [Image.new("RGB", (4, 4))])[2] is False


def test_preview_takes_the_same_decision_as_the_join():
    """L'aperçu aplatissait toujours sur blanc, alors que la planche gardait
    la transparence : il passe la décision de join_output à merge_images_2d."""
    import ast
    from pathlib import Path
    tree = ast.parse((Path(ast_loader.QT_DIR) / "merge_dialog_qt.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MergeDialog")
    for name in ("_update_preview", "_finish_join"):
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
        calls = {getattr(c.func, "id", None): c for c in ast.walk(method) if isinstance(c, ast.Call)}
        assert "join_output" in calls, name
        assert any(k.arg == "keep_alpha" for k in calls["merge_images_2d"].keywords), name


def test_jpeg_pages_give_a_jpeg_at_quality_95():
    from modules.qt.entries import detect_jpeg_quality
    a = encoded(Image.new("RGB", (16, 16), (200, 10, 10)), "JPEG", quality=60)
    b = encoded(Image.new("RGB", (16, 16), (10, 200, 10)), "JPEG", quality=60)
    _entry, img, data = joined(("p01.jpg", a), ("p02.jpg", b))
    assert img.format == "JPEG"
    assert detect_jpeg_quality(data) == 95
