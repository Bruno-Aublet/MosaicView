"""Fenêtre GIF animé : suit les pages tant qu'elle est ouverte, garde sa place à
une page rétablie, libère les images, refuse le glisser d'une autre fenêtre, fait
défiler la liste ; question après « Modifier le GIF animé ». Thread de création :
résultat tardif abandonné.

animated_gif_dialog_qt.py importe PySide6 : ses méthodes sont extraites par AST
(tests/ast_loader), l'interface est remplacée, et l'état est un vrai AppState.
"""
import io
import re
import threading
import types

import pytest
from PIL import Image

import ast_loader
from fakes import Signal
from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import create_entry
from modules.qt.state import AppState
from modules.qt.undo_redo import entry_key

MODULE = "animated_gif_dialog_qt.py"


def page(name, color=(255, 0, 0)):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, "PNG")
    return create_entry(name, buf.getvalue(), IMAGE_EXTS)


Methods = ast_loader.load_methods(
    MODULE, "AnimatedGifDialog", {"_refresh_from_pages", "_reload_frame", "_delete_image", "_move_image"},
    entry_key=entry_key)


class Window(Methods):
    def __init__(self, state, pages, edited_key=None):
        self._state = state
        self._edited_key = edited_key
        self._worker = None
        self._frames = []
        for e in pages:
            frame = dict(e)
            frame["_page_key"] = entry_key(e)
            frame["img"] = None
            self._frames.append(frame)
        self._gif_images = list(self._frames)
        self.enabled = []
        self._create_btn = types.SimpleNamespace(setEnabled=self.enabled.append)
        self.rendered = 0

    def _render_thumbs(self):
        self.rendered += 1

    def names(self):
        return [f["orig_name"] for f in self._gif_images]


@pytest.fixture
def doc():
    st = AppState()
    st.images_data = [page("a.png"), page("b.png"), page("c.png")]
    return st


def test_deleted_page_leaves_the_gif_and_comes_back_at_its_place(doc):
    window = Window(doc, list(doc.images_data))
    removed = doc.images_data.pop(1)
    window._refresh_from_pages()
    assert window.names() == ["a.png", "c.png"]
    doc.images_data.insert(1, removed)
    window._refresh_from_pages()
    assert window.names() == ["a.png", "b.png", "c.png"]


def test_no_page_left_greys_out_create(doc):
    window = Window(doc, list(doc.images_data))
    doc.images_data.clear()
    window._refresh_from_pages()
    assert window._gif_images == [] and window.enabled == [False]


def test_page_changed_since_opening_enters_the_gif_as_it_is_now(doc):
    window = Window(doc, list(doc.images_data))
    new_bytes = page("x.png", (0, 0, 255))["bytes"]
    doc.images_data[0]["bytes"] = new_bytes
    window._refresh_from_pages()
    assert window._gif_images[0]["bytes"] is new_bytes
    assert window._gif_images[0]["img"] is None
    assert window.rendered == 1


def test_nothing_is_redrawn_when_the_pages_did_not_change(doc):
    window = Window(doc, list(doc.images_data))
    window._refresh_from_pages()
    assert window.rendered == 0


def test_window_order_is_kept_when_the_document_changes(doc):
    window = Window(doc, list(doc.images_data))
    window._move_image(2, 0)
    assert window.names() == ["c.png", "a.png", "b.png"]
    doc.images_data.pop(0)
    window._refresh_from_pages()
    assert window.names() == ["c.png", "b.png"]


def test_page_absent_from_the_document_keeps_its_place_after_a_move(doc):
    window = Window(doc, list(doc.images_data))
    removed = doc.images_data.pop(1)
    window._refresh_from_pages()
    window._move_image(1, 0)                                # c avant a, b absente
    doc.images_data.insert(1, removed)
    window._refresh_from_pages()
    assert window.names() == ["c.png", "a.png", "b.png"]


def test_frame_deleted_in_the_window_does_not_come_back(doc):
    window = Window(doc, list(doc.images_data))
    window._delete_image(1)
    window._refresh_from_pages()
    assert window.names() == ["a.png", "c.png"]


def test_pages_are_not_followed_during_a_creation_or_for_an_edited_gif(doc):
    window = Window(doc, list(doc.images_data))
    window._worker = object()
    doc.images_data.pop()
    window._refresh_from_pages()
    assert len(window._gif_images) == 3
    window = Window(doc, list(doc.images_data), edited_key=(1, 1))
    doc.images_data.pop()
    window._refresh_from_pages()
    assert len(window._gif_images) == 2


def test_thumbnail_frees_a_page_image_but_keeps_an_extracted_one(monkeypatch):
    """Sinon toute la sélection reste décodée tant que la fenêtre est ouverte.
    Une image extraite d'un GIF n'existe qu'en mémoire."""
    freed = []
    Thumb = ast_loader.load_methods(
        MODULE, "_ThumbWidget", {"_load_thumb"},
        ensure_image_loaded=lambda e: e.setdefault("img", object()),
        _pil_to_qpixmap=lambda *a: types.SimpleNamespace(isNull=lambda: False),
        free_image_memory=freed.append)
    label = types.SimpleNamespace(setPixmap=lambda px: None, setText=lambda t: None)

    class Widget(Thumb):
        THUMB_SIZE = 100
        _img_lbl = label

        def __init__(self, entry):
            self._entry = entry

    with_bytes, in_memory = {"bytes": b"x"}, {"img": object()}
    Widget(with_bytes)._load_thumb()
    Widget(in_memory)._load_thumb()
    assert freed == [with_bytes]


# ── Délai ────────────────────────────────────────────────────────────────────

delays = ast_loader.load(MODULE, {"_delay_range", "_delay_stats", "_DELAY_MIN", "_DELAY_MAX"})


class CommonDelay(ast_loader.load_methods(MODULE, "AnimatedGifDialog", {"_on_delay_changed"})):
    def __init__(self, frames):
        self._frames = frames
        self.calls = []
        self._thumbs_container = types.SimpleNamespace(refresh_delays=lambda: self.calls.append("thumbs"))

    def _update_fps_label(self, value):
        self.calls.append(value)

    def _update_metadata(self):
        self.calls.append("metadata")


def test_common_delay_applies_to_every_image():
    """Y compris une page absente du GIF, que Annuler peut y faire revenir."""
    frames = [{"gif_duration": 80}, {"gif_duration": 3000}]
    window = CommonDelay(frames)
    window._on_delay_changed(200)
    assert [f["gif_duration"] for f in frames] == [None, None]
    assert window.calls == ["thumbs", 200, "metadata"]


def test_enter_in_a_number_field_does_not_create_the_gif():
    """Entrée dans un champ numérique déclenchait le bouton par défaut « Créer » :
    chaque champ de la fenêtre est un _NumberSpin."""
    import ast
    tree = ast.parse((ast_loader.QT_DIR / MODULE).read_text(encoding="utf-8"))
    calls = [node.func.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    assert "QSpinBox" not in calls and "QDoubleSpinBox" not in calls
    assert calls.count("_NumberSpin") == 1 and calls.count("_SecondsSpin") == 1
    assert calls.count("_DelaySpin") == 1
    classes = {node.name: [base.id for base in node.bases] for node in ast.walk(tree)
               if isinstance(node, ast.ClassDef)}
    assert classes["_SecondsSpin"] == ["_NumberSpin"] and classes["_DelaySpin"] == ["_SecondsSpin"]


decimal_text = ast_loader.load("utils.py", {"decimal_text"})["decimal_text"]
seconds = ast_loader.load(MODULE, {"_seconds_text", "_delay_from_text", "_average_delay", "_SECONDS_RE"},
                          re=re, decimal_text=decimal_text)


@pytest.mark.parametrize("value, decimals, separator, expected", [
    (12.346, 2, ",", "12,35"), (0.5, 1, ".", "0.5"), (1234.0, 1, ",", "1234,0")])
def test_numbers_of_the_window_use_the_separator_of_the_language(value, decimals, separator, expected):
    """Fréquence, durée totale et poids estimé, comme les champs de délai ; la
    durée d'animation de l'info-bulle de la mosaïque aussi."""
    assert decimal_text(value, decimals, separator) == expected


@pytest.mark.parametrize("delay, separator, expected", [
    (100, ",", "0,10"), (1250, ".", "1.25"), (20, ",", "0,02"), (5000, ".", "5.00"), (0, ",", "0,00")])
def test_delay_is_shown_in_seconds_with_the_separator_of_the_language(delay, separator, expected):
    """Un GIF enregistre ses délais en centièmes de seconde."""
    assert seconds["_seconds_text"](delay, separator) == expected


@pytest.mark.parametrize("text, expected", [
    ("0,1", 100), ("0.10", 100), ("1,25", 1250), ("1.15", 1150), ("2", 2000), (",5", 500), ("3,", 3000),
    ("", None), (",", None), ("1,255", None), ("a", None), ("-1", None), ("1,2,3", None)])
def test_typed_delay_is_read_in_seconds_with_a_comma_or_a_point(text, expected):
    """Pas plus fin que le centième, que le GIF n'enregistrerait pas."""
    assert seconds["_delay_from_text"](text) == expected


@pytest.mark.parametrize("durations, expected", [([100, 130], 120), ([100, 100, 110], 100), ([], 100)])
def test_common_delay_of_an_edited_gif_is_a_whole_hundredth(durations, expected):
    """Sinon le délai commun affiché ne serait pas celui que le GIF enregistre."""
    assert seconds["_average_delay"](durations) == expected


def test_delay_of_a_thumbnail_changes_only_its_image():
    Thumb = ast_loader.load_methods(MODULE, "_ThumbWidget", {"_on_delay_changed"})
    updated = []

    class Widget(Thumb):
        _dialog = types.SimpleNamespace(_update_metadata=lambda: updated.append(True))

        def __init__(self, entry):
            self._entry = entry

    first, second = {"gif_duration": 100}, {"gif_duration": 100}
    Widget(first)._on_delay_changed(3000)
    assert first["gif_duration"] == 3000 and second["gif_duration"] == 100
    assert updated == [True]


def test_minus_and_plus_buttons_of_a_thumbnail_change_its_delay_by_50():
    Thumb = ast_loader.load_methods(MODULE, "_ThumbWidget", {"_step_delay"})

    class Spin:
        def __init__(self):
            self.current = 300

        def value(self):
            return self.current

        def setValue(self, value):
            self.current = value

    class Widget(Thumb):
        def __init__(self):
            self._delay_spin = Spin()

    widget = Widget()
    step = ast_loader.load(MODULE, {"_DELAY_BUTTON_STEP"})["_DELAY_BUTTON_STEP"]
    widget._step_delay(step)
    assert widget._delay_spin.current == 350
    widget._step_delay(-step)
    widget._step_delay(-step)
    assert widget._delay_spin.current == 250


def test_delay_follows_its_image_when_moved_or_when_the_page_is_retouched(doc):
    window = Window(doc, list(doc.images_data))
    window._gif_images[2]["gif_duration"] = 3000
    window._move_image(2, 0)
    assert window._gif_images[0]["gif_duration"] == 3000
    doc.images_data[2]["bytes"] = page("x.png", (0, 0, 255))["bytes"]
    window._refresh_from_pages()
    assert window.names()[0] == "c.png" and window._gif_images[0]["gif_duration"] == 3000


@pytest.mark.parametrize("delay, expected", [(100, (20, 5000)), (0, (0, 5000)), (8000, (20, 8000))])
def test_delay_outside_the_usual_range_is_shown_as_it_is(delay, expected):
    """Ramené dans les limites, il paraîtrait autre que celui qui sera écrit."""
    assert delays["_delay_range"](delay) == expected


@pytest.mark.parametrize("values, expected", [([100, 300], (0.4, 5.0)), ([0, 0], (0, 0))])
def test_duration_and_frequency_come_from_the_real_delays(values, expected):
    assert delays["_delay_stats"](values) == expected


# ── Glisser ──────────────────────────────────────────────────────────────────

ns = ast_loader.load(MODULE, {"_auto_scroll_step", "_AUTO_SCROLL_MARGIN", "_AUTO_SCROLL_STEP"})
auto_scroll_step = ns["_auto_scroll_step"]


def test_drag_near_an_edge_scrolls_the_list():
    assert auto_scroll_step(5, 800) == -ns["_AUTO_SCROLL_STEP"]
    assert auto_scroll_step(795, 800) == ns["_AUTO_SCROLL_STEP"]
    assert auto_scroll_step(400, 800) == 0


class FakeThumb:
    def __init__(self, dialog):
        self._dialog = dialog


Container = ast_loader.load_methods(MODULE, "_ThumbsContainer", {"_own_drag"}, _ThumbWidget=FakeThumb)


def drag_event(source, has_format=True):
    return types.SimpleNamespace(source=lambda: source,
                                 mimeData=lambda: types.SimpleNamespace(hasFormat=lambda m: has_format))


def test_only_thumbnails_of_this_window_can_be_dropped():
    """Le numéro d'une vignette d'une autre fenêtre désignerait ici une autre
    image, ou aucune."""
    class Box(Container):
        MIME = "x"
        _dialog = "ici"

    box = Box()
    assert box._own_drag(drag_event(FakeThumb("ici")))
    assert not box._own_drag(drag_event(FakeThumb("ailleurs")))
    assert not box._own_drag(drag_event(None))
    assert not box._own_drag(drag_event(FakeThumb("ici"), has_format=False))


# ── Question après « Modifier » ──────────────────────────────────────────────

replace = ast_loader.load(MODULE, {"_replace_edited_gif"}, entry_key=entry_key)["_replace_edited_gif"]


def replace_callbacks(calls):
    return {"save_state": lambda: calls.append("save"), "render_mosaic": lambda: calls.append("render"),
            "update_button_text": lambda: calls.append("update")}


@pytest.fixture
def edited():
    st = AppState()
    st.doc_generation = 7
    st.images_data = [page("a.png"), page("Bbbb.gif"), page("c.png"), page("Animated_1.gif")]
    st.modified = False
    return st


def keys(st):
    return entry_key(st.images_data[1]), entry_key(st.images_data[3])


def test_replacing_gives_the_new_gif_the_place_and_name_of_the_original(edited):
    original_key, new_key = keys(edited)
    new = edited.images_data[3]
    original_name = edited.images_data[1]["orig_name"]
    assert new["orig_name"] != original_name
    calls = []
    assert replace(edited, replace_callbacks(calls), original_key, new_key, 7) is True
    assert [e["orig_name"] for e in edited.images_data] == ["a.png", original_name, "c.png"]
    assert edited.images_data[1] is new
    assert edited.modified is True
    assert calls == ["save", "save", "render", "update"]    # avant et après


def test_replacing_transfers_the_page_attributes(edited):
    original_key, new_key = keys(edited)
    original, new = edited.images_data[1], edited.images_data[3]
    edited._page_attrs_by_entry_id = {id(original): {"Type": "FrontCover"}}
    replace(edited, replace_callbacks([]), original_key, new_key, 7)
    assert edited._page_attrs_by_entry_id[id(new)]["Type"] == "FrontCover"


@pytest.mark.parametrize("change", ["document", "original", "new", "operation"])
def test_replacing_does_nothing_when_the_document_changed(edited, change):
    original_key, new_key = keys(edited)
    names = [e["orig_name"] for e in edited.images_data]
    generation = 7
    if change == "document":
        generation = 6
    elif change == "original":
        del edited.images_data[1]
        names = [e["orig_name"] for e in edited.images_data]
    elif change == "new":
        del edited.images_data[3]
        names = [e["orig_name"] for e in edited.images_data]
    else:
        edited.begin_operation(object())
    assert replace(edited, replace_callbacks([]), original_key, new_key, generation) is False
    assert [e["orig_name"] for e in edited.images_data] == names


# ── Thread de création ───────────────────────────────────────────────────────

def worker_class(**deps):
    return ast_loader.load_methods(
        MODULE, "_GifWorker", {"run", "_run"}, IMAGE_EXTS=IMAGE_EXTS, create_entry=create_entry, **deps)


def make_worker(cls):
    worker = cls()
    worker.step, worker.created, worker.failed, worker.cancelled = Signal(), Signal(), Signal(), Signal()
    worker._pages, worker._settings, worker._name = [], {}, "Animated_1.gif"
    worker._cancelled = threading.Event()
    return worker


def test_worker_gives_a_ready_entry():
    data = page("x.png")["bytes"]
    cls = worker_class(build_gif=lambda *a, **k: (data, []), GifCancelled=KeyError)
    worker = make_worker(cls)
    worker.run()
    [(entry, left_out)] = worker.created.emitted
    assert entry["source_archive"] == "loose" and left_out == []


def test_worker_error_without_text_gives_its_type():
    """L'erreur de mémoire n'a pas de texte : le message restait vide."""
    def fail(*a, **k):
        raise MemoryError()

    worker = make_worker(worker_class(build_gif=fail, GifCancelled=KeyError))
    worker.run()
    assert worker.failed.emitted == [("MemoryError",)]
    assert worker.created.emitted == []


def test_worker_stopped_on_request():
    class Stop(Exception):
        pass

    def stop(*a, **k):
        raise Stop()

    worker = make_worker(worker_class(build_gif=stop, GifCancelled=Stop))
    worker.run()
    assert worker.cancelled.emitted == [()] and worker.created.emitted == []
