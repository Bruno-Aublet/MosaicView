"""Jointure de pages : la fenêtre ne laisse en mémoire ni elle-même ni les
images qu'elle a décodées (audit 8, P1-1).

- _decoded_source : l'image décodée d'une page source est gardée par la
  fenêtre, jamais posée sur la page ; une image déjà décodée sur la page
  (visionneuse ouverte) est reprise telle quelle.
- MergeDialog est détruite à sa fermeture, et libère alors ses images.
- Aucun autre décodage ni aucune image posée sur une page dans le module.
- _disconnect_lang ne déconnecte qu'une fois (finished puis destroyed).

merge_dialog_qt.py importe PySide6 au niveau module : ses fonctions sont
extraites par AST et exécutées seules.
"""
import ast
import io
import sys
import types
from pathlib import Path

from PIL import Image

from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import create_entry, ensure_image_loaded

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "modules" / "qt" / "merge_dialog_qt.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _extract(names, namespace):
    nodes = [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "merge_dialog_qt.py", "exec"), namespace)
    return namespace


def _class(name):
    return next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == name)


def _method(cls, name):
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _png_entry(name="p1.png"):
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 10, 10)).save(buf, "PNG")
    return create_entry(name, buf.getvalue(), IMAGE_EXTS)


_NS = _extract({"_decoded_source"}, {"ensure_image_loaded": ensure_image_loaded})
_decoded_source = _NS["_decoded_source"]


def test_decoded_image_is_kept_by_the_window_not_by_the_page():
    entry = _png_entry()
    cache = {}
    img = _decoded_source(entry, cache)
    assert img is not None and img.size == (40, 30)
    assert entry.get("img") is None
    assert _decoded_source(entry, cache) is img      # décodée une seule fois
    cache.clear()                                    # fermeture de la fenêtre
    assert entry.get("img") is None


def test_image_already_decoded_on_the_page_is_reused_and_not_cached():
    entry = _png_entry()
    on_page = ensure_image_loaded(entry)
    cache = {}
    assert _decoded_source(entry, cache) is on_page
    assert cache == {}
    assert entry["img"] is on_page                   # laissée à son propriétaire


def test_unreadable_page_gives_none():
    entry = create_entry("p1.png", b"not an image", IMAGE_EXTS)
    cache = {}
    assert _decoded_source(entry, cache) is None
    assert entry.get("img") is None


def test_merge_dialog_is_destroyed_on_close_and_releases_its_images():
    cls = _class("MergeDialog")
    init = ast.unparse(_method(cls, "__init__"))
    assert "setAttribute(Qt.WA_DeleteOnClose)" in init
    assert "self.finished.connect(self._release_images)" in init
    release = ast.unparse(_method(cls, "_release_images"))
    assert "self._decoded.clear()" in release
    assert "self._preview_timer.stop()" in release


def test_no_other_decoding_or_image_set_on_a_page_in_the_module():
    """Tout décodage passe par _decoded_source ; aucune image n'est posée sur
    une page (entry["img"] = …), sauf la remise à None de _decoded_source."""
    for node in ast.walk(TREE):
        if not isinstance(node, ast.FunctionDef) or node.name == "_decoded_source":
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and ast.unparse(sub.func) == "ensure_image_loaded":
                raise AssertionError(f"ensure_image_loaded dans {node.name}")
            if isinstance(sub, ast.Assign):
                for target in sub.targets:
                    if (isinstance(target, ast.Subscript)
                            and ast.unparse(target.slice) in ("'img'", '"img"')):
                        raise AssertionError(f"image posée sur une page dans {node.name}")


def test_layout_functions_receive_the_window_images():
    """detect_merge_adjustment et merge_images_2d lisent entry["img"], vide
    sur les pages : ils reçoivent les images de la fenêtre (_image_positions,
    ou les réductions de l'aperçu), jamais les positions des pages."""
    calls = [node for node in ast.walk(TREE)
             if isinstance(node, ast.Call)
             and ast.unparse(node.func) in ("detect_merge_adjustment", "merge_images_2d")]
    assert len(calls) == 3
    for call in calls:
        arg = ast.unparse(call.args[0])
        assert arg.startswith("self._image_positions(") or arg in ("image_positions",
                                                                    "preview_positions"), arg
    method = ast.unparse(_method(_class("MergeDialog"), "_image_positions"))
    assert "_decoded_source(" in method


def test_image_positions_feed_the_real_layout_detection():
    from modules.qt.image_ops import detect_merge_adjustment
    small = _png_entry("a.png")
    buf = io.BytesIO()
    Image.new("RGB", (40, 60)).save(buf, "PNG")
    tall = create_entry("b.png", buf.getvalue(), IMAGE_EXTS)
    cache = {}
    positions = [{"entry": {"img": _decoded_source(e, cache)}, "x": x, "y": 0}
                 for e, x in ((small, 0), (tall, 100))]
    need, dimension_type, _dims = detect_merge_adjustment(positions)
    assert need and dimension_type == "height"
    assert small.get("img") is None and tall.get("img") is None


class _FakeSignal:
    def __init__(self):
        self.disconnected = []

    def disconnect(self, handler):
        self.disconnected.append(handler)


def test_language_is_disconnected_only_once(monkeypatch):
    fake = types.ModuleType("modules.qt.language_signal")
    fake.language_signal = types.SimpleNamespace(changed=_FakeSignal())
    monkeypatch.setitem(sys.modules, "modules.qt.language_signal", fake)
    ns = _extract({"_disconnect_lang"}, {})
    dialog = types.SimpleNamespace(_lang_handler=lambda _lang: None)
    handler = dialog._lang_handler
    ns["_disconnect_lang"](dialog)                   # finished
    ns["_disconnect_lang"](dialog)                   # destroyed
    assert fake.language_signal.changed.disconnected == [handler]
