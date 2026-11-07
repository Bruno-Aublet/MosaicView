"""Conversion de format des pages sélectionnées : déroulement complet
(conversion_dialogs_qt.perform_conversion et _ConversionWorker._run).

Vérifié :
- chaque page convertie insérée juste après son original, au fil de son
  arrivée : supprimer les originaux laisse chaque page à sa place ;
- seuls les originaux réellement convertis sont proposés à la suppression ;
  les échecs sont expliqués page par page ; rien de converti : aucune
  question, opération terminée ;
- « Supprimer les originaux » ne retire un original que si sa page convertie
  est encore dans le document ;
- l'opération reste en cours jusqu'à la réponse à « Supprimer les
  originaux ? », puis se termine (point d'historique) ;
- Annuler : pages déjà insérées retirées, page arrivée ensuite ignorée ;
  fichier fermé pendant la conversion ou pendant la question : rien n'est
  touché dans le document suivant, opération terminée ;
- le worker n'ajoute aux originaux convertis que les pages réellement
  converties (vraie conversion, page illisible comprise) ;
- position des pages converties indépendante de la sélection au moment du clic ;
- attributs de <Page> repris par la page convertie quand elle remplace son
  original, jamais quand les deux restent ; ceux du fichier sont recalculés ;
- fenêtres de format et de qualité : elles suivent les pages tant qu'elles sont
  ouvertes (page supprimée, déplacée, rétablie par Annuler) et ne convertissent
  que les pages encore dans le document.

conversion_dialogs_qt.py importe PySide6 : les fonctions sont extraites par
AST (tests/ast_loader.py) ; worker, fenêtres et texte de progression sont
remplacés, la vraie conversion (image_ops.convert_image_data) est employée
par le worker.
"""
import io
import sys
import threading
import types

import pytest
from PIL import Image

from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.comic_info import (build_page_attrs_map, parse_comic_info_xml,
                                   sync_pages_in_xml_data)
from modules.qt.entries import create_entry, free_image_memory
from modules.qt.image_ops import _ANIMATABLE_FORMATS, convert_image_data, is_animated_entry
from modules.qt.state import AppState
from modules.qt.undo_redo import adopt_entry_key, entry_key
from modules.qt.utils import cancel_operations_on_close

import ast_loader
from fakes import Signal as _Signal, tr as _tr


class FakeWorker:
    created = []

    def __init__(self, target_format, quality, selected_entries, lossless=False):
        self.selected = selected_entries
        self._cancelled = threading.Event()
        self.converted_sources, self.failures = [], []
        for name in ("progress", "entry_ready", "finished", "cancelled"):
            setattr(self, name, _Signal())
        FakeWorker.created.append(self)

    def start(self):
        pass

    def wait(self):
        pass

    def deleteLater(self):
        pass


class Ui:
    """Fenêtres et overlay remplacés."""

    def __init__(self):
        self.errors, self.questions, self.cancel_button = [], [], None

    def show_cancel_item(self, canvas, text, holder, cancel_fn, anchor_lbl=None):
        self.cancel_button = cancel_fn

    def error_dialog(self, parent, title_fn, message_fn, **kwargs):
        self.errors.append(message_fn())
        return types.SimpleNamespace(show_nonmodal=lambda: None)

    def complete_dialog(self, parent, converted, target_format, sources, converted_entries,
                        callbacks, on_done=None, animation_lost=0):
        self.questions.append({"sources": sources, "converted": converted, "on_done": on_done,
                               "animation_lost": animation_lost})


@pytest.fixture
def ui(monkeypatch):
    FakeWorker.created = []
    u = Ui()
    web = types.ModuleType("modules.qt.web_import_qt")
    web._show_cancel_item = u.show_cancel_item
    dq = types.ModuleType("modules.qt.dialogs_qt")
    dq.ErrorDialog = u.error_dialog
    monkeypatch.setitem(sys.modules, "modules.qt.web_import_qt", web)
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", dq)
    u.ns = ast_loader.load(
        "conversion_dialogs_qt.py", {"perform_conversion", "_conversion_failures_message"},
        _=_tr, _wt=_tr, _state_module=types.SimpleNamespace(state=None),
        _show_canvas_text=lambda *a: None, _hide_canvas_text=lambda *a: None,
        _ConversionWorker=FakeWorker, show_conversion_complete_dialog=u.complete_dialog,
        _ANIMATABLE_FORMATS=_ANIMATABLE_FORMATS, is_animated_entry=is_animated_entry)
    return u


def png(name, color):
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(buf, "PNG")
    return create_entry(name, buf.getvalue(), IMAGE_EXTS)


class Document:
    def __init__(self):
        self.state = AppState()
        self.a, self.b, self.c = png("a.png", "red"), png("b.png", "green"), png("c.png", "blue")
        self.state.images_data = [self.a, self.b, self.c]
        self.state.selected_indices = {0, 1}
        self.saves = 0
        self.parent = types.SimpleNamespace(_canvas=types.SimpleNamespace())

    def callbacks(self):
        def save():
            self.saves += 1
        return {"state": self.state, "save_state": save, "render_mosaic": lambda: None,
                "update_button_text": lambda: None}

    def start(self, ui, fmt="WEBP", pages=None):
        ui.ns["perform_conversion"](self.parent, fmt, 90, pages or [self.a, self.b],
                                    self.callbacks())
        [worker] = FakeWorker.created
        assert self.state.operation_running()
        return worker

    def names(self):
        return [e["orig_name"] for e in self.state.images_data]


def converted(name):
    return {"orig_name": name, "bytes": b"x", "extension": ".webp", "is_image": True}


def test_each_converted_page_arrives_just_after_its_original(ui):
    doc = Document()
    worker = doc.start(ui)
    worker.entry_ready.emit(doc.a, converted("a.webp"))
    worker.entry_ready.emit(doc.b, converted("b.webp"))
    assert doc.names() == ["a.png", "a.webp", "b.png", "b.webp", "c.png"]


def test_only_converted_originals_are_offered_for_deletion(ui):
    doc = Document()
    worker = doc.start(ui)
    worker.entry_ready.emit(doc.a, converted("a.webp"))
    worker.converted_sources = [doc.a]
    worker.failures = [{"kind": "too_large", "name": "b.png", "width": 20000, "height": 20000,
                        "max": 16383}]
    worker.finished.emit([], 1)
    [question] = ui.questions
    assert question["sources"] == [doc.a]
    assert len(ui.errors) == 1 and "b.png" in ui.errors[0]   # cause expliquée
    assert doc.state.operation_running()                      # jusqu'à la réponse
    question["on_done"]("keep_all")
    assert not doc.state.operation_running()
    assert doc.saves == 2                                     # avant, puis après la réponse


def test_nothing_converted_ends_without_question(ui):
    doc = Document()
    worker = doc.start(ui)
    worker.failures = [{"kind": "unreadable", "name": "a.png"}]
    worker.finished.emit([], 0)
    assert ui.questions == [] and len(ui.errors) == 1
    assert not doc.state.operation_running()


def test_cancel_removes_inserted_pages_and_ignores_late_ones(ui):
    doc = Document()
    worker = doc.start(ui)
    worker.entry_ready.emit(doc.a, converted("a.webp"))
    ui.cancel_button()                                        # bouton Annuler
    assert worker._cancelled.is_set()
    assert doc.names() == ["a.png", "b.png", "c.png"]
    worker.entry_ready.emit(doc.b, converted("b.webp"))       # arrivée après l'annulation
    worker.cancelled.emit()
    assert doc.names() == ["a.png", "b.png", "c.png"]
    assert not doc.state.operation_running()


def close_file(doc):
    """Comme force_close_file : opérations annulées, document vidé et changé."""
    cancel_operations_on_close(doc.parent._canvas)
    doc.state.images_data = []
    doc.state.running_operations = []
    doc.state.doc_generation += 1


def test_file_closed_during_the_conversion_touches_nothing(ui):
    doc = Document()
    worker = doc.start(ui)
    close_file(doc)
    assert worker._cancelled.is_set()
    worker.entry_ready.emit(doc.a, converted("a.webp"))
    worker.finished.emit([], 1)
    assert doc.state.images_data == [] and ui.questions == []
    assert not doc.state.operation_running()


def test_file_closed_during_the_question_touches_nothing(ui):
    doc = Document()
    worker = doc.start(ui)
    worker.entry_ready.emit(doc.a, converted("a.webp"))
    worker.converted_sources = [doc.a]
    worker.finished.emit([], 1)
    saves = doc.saves
    close_file(doc)
    ui.questions[0]["on_done"]("delete_orig")
    assert doc.saves == saves and doc.state.images_data == []


def answer_delete_originals(doc, sources, converted_entries):
    """Vraie show_conversion_complete_dialog, fenêtre remplacée : réponse
    « Supprimer les originaux »."""
    handlers = []

    class FakeDialog:
        def __init__(self, parent, converted, target_format, sources, converted_entries,
                     handler, animation_lost=0):
            handlers.append(handler)

        def show_nonmodal(self):
            pass

    ns = ast_loader.load(
        "conversion_dialogs_qt.py",
        {"show_conversion_complete_dialog", "_originals_with_their_conversion"},
        _state_module=types.SimpleNamespace(state=None), _ConversionCompleteDialog=FakeDialog)
    ns["show_conversion_complete_dialog"](doc.parent, len(sources), "WEBP", sources,
                                          converted_entries, {"state": doc.state})
    handlers[0]("delete_orig")


def test_delete_originals_removes_each_original_with_its_conversion():
    doc = Document()
    a_conv, b_conv = converted("a.webp"), converted("b.webp")
    doc.state.images_data = [doc.a, doc.b, a_conv, b_conv, doc.c]
    answer_delete_originals(doc, [doc.a, doc.b], [a_conv, b_conv])
    assert doc.names() == ["a.webp", "b.webp", "c.png"]


def test_delete_originals_keeps_an_original_whose_conversion_disappeared():
    doc = Document()
    a_conv, b_conv = converted("a.webp"), converted("b.webp")
    doc.state.images_data = [doc.a, doc.b, b_conv, doc.c]     # a.webp retirée entre-temps
    answer_delete_originals(doc, [doc.a, doc.b], [a_conv, b_conv])
    assert doc.names() == ["a.png", "b.webp", "c.png"]


def test_operation_started_meanwhile_prevents_the_conversion(ui):
    doc = Document()
    doc.state.begin_operation(object())
    ui.ns["perform_conversion"](doc.parent, "WEBP", 90, [doc.a], doc.callbacks())
    assert FakeWorker.created == [] and doc.saves == 0


def test_converted_pages_stay_by_their_originals_whatever_the_selection(ui):
    doc = Document()
    doc.state.selected_indices = {2}                          # sélection changée avant le clic
    worker = doc.start(ui)
    worker.entry_ready.emit(doc.a, converted("a.webp"))
    assert doc.names() == ["a.png", "a.webp", "b.png", "c.png"]


def test_converted_page_follows_its_original_wherever_it_moved(ui):
    doc = Document()
    doc.state.images_data = [doc.b, doc.c, doc.a]             # pages déplacées avant le clic
    worker = doc.start(ui)
    worker.entry_ready.emit(doc.a, converted("a.webp"))
    assert doc.names() == ["b.png", "c.png", "a.png", "a.webp"]


def convert_first_and_last(doc, ui):
    """Conversion d'une sélection non contiguë : a et c, pas b."""
    worker = doc.start(ui, pages=[doc.a, doc.c])
    a_conv, c_conv = converted("a.webp"), converted("c.webp")
    worker.entry_ready.emit(doc.a, a_conv)
    worker.entry_ready.emit(doc.c, c_conv)
    return a_conv, c_conv


def test_kept_originals_each_have_their_copy_beside_them(ui):
    doc = Document()
    convert_first_and_last(doc, ui)
    assert doc.names() == ["a.png", "a.webp", "b.png", "c.png", "c.webp"]


def test_deleting_the_originals_keeps_each_page_at_its_place(ui):
    doc = Document()
    a_conv, c_conv = convert_first_and_last(doc, ui)
    answer_delete_originals(doc, [doc.a, doc.c], [a_conv, c_conv])
    assert doc.names() == ["a.webp", "b.png", "c.webp"]


def test_page_that_failed_to_convert_stays_at_its_place(ui):
    doc = Document()
    worker = doc.start(ui, pages=[doc.a, doc.c])
    c_conv = converted("c.webp")                              # a n'a pas pu être convertie
    worker.entry_ready.emit(doc.c, c_conv)
    answer_delete_originals(doc, [doc.c], [c_conv])
    assert doc.names() == ["a.png", "b.png", "c.webp"]


def test_copy_of_an_original_gone_meanwhile_is_never_lost(ui):
    doc = Document()
    worker = doc.start(ui, pages=[doc.a, doc.b])
    worker.entry_ready.emit(doc.a, converted("a.webp"))
    doc.state.images_data.remove(doc.b)
    worker.entry_ready.emit(doc.b, converted("b.webp"))       # après la page convertie précédente
    assert doc.names() == ["a.png", "a.webp", "b.webp", "c.png"]


# ---------------------------------------------------------------------------
# Attributs de <Page> : suivent la page qui remplace son original
# ---------------------------------------------------------------------------

PAGES_XML = (b'<ComicInfo><PageCount>3</PageCount><Pages>'
             b'<Page Image="0" Type="FrontCover" ImageWidth="8" ImageHeight="6" ImageSize="1"/>'
             b'<Page Image="1" DoublePage="true" ImageWidth="8" ImageHeight="6" ImageSize="1"/>'
             b'<Page Image="2" ImageWidth="8" ImageHeight="6" ImageSize="1"/>'
             b'</Pages></ComicInfo>')


def document_with_pages(xml=PAGES_XML):
    doc = Document()
    info = create_entry("ComicInfo.xml", xml, IMAGE_EXTS)
    doc.state.images_data = [info, doc.a, doc.b, doc.c]
    doc.state.comic_metadata = parse_comic_info_xml(xml)
    build_page_attrs_map(doc.state)
    return doc


def bigger_copy(name):
    buf = io.BytesIO()
    Image.new("RGB", (16, 12), "white").save(buf, "PNG")
    return create_entry(name, buf.getvalue(), IMAGE_EXTS)


def insert_after(doc, original, copy):
    data = doc.state.images_data
    data.insert(next(i for i, e in enumerate(data) if e is original) + 1, copy)


def written_pages(doc):
    sync_pages_in_xml_data(doc.state, emit_signal=False)
    return doc.state.comic_metadata["pages"]


def test_copy_replacing_its_original_takes_over_its_page_information():
    doc = document_with_pages()
    copy = bigger_copy("a.webp")
    insert_after(doc, doc.a, copy)
    answer_delete_originals(doc, [doc.a], [copy])
    first, double, _third = written_pages(doc)
    assert first["Type"] == "FrontCover"
    assert (first["ImageWidth"], first["ImageHeight"]) == ("16", "12")  # ceux de la copie
    assert first["ImageSize"] == str(len(copy["bytes"]))
    assert double["DoublePage"] == "true"
    assert parse_comic_info_xml(doc.state.images_data[0]["bytes"])["pages"][0]["Type"] == "FrontCover"


def test_information_of_other_programs_follows_the_page():
    doc = document_with_pages(
        b'<ComicInfo><Pages><Page Image="0" Key="Aaaa" Bookmark="Bbbb" Cccc="Dddd"/>'
        b'<Page Image="1"/><Page Image="2"/></Pages></ComicInfo>')
    copy = bigger_copy("a.webp")
    insert_after(doc, doc.a, copy)
    answer_delete_originals(doc, [doc.a], [copy])
    first = written_pages(doc)[0]
    assert (first["Key"], first["Bookmark"], first["Cccc"]) == ("Aaaa", "Bbbb", "Dddd")


def test_original_kept_alone_holds_the_information():
    doc = document_with_pages()
    copy = bigger_copy("a.webp")
    insert_after(doc, doc.a, copy)                            # « Garder les deux »
    original, kept_copy = written_pages(doc)[:2]
    assert original["Type"] == "FrontCover"
    assert "Type" not in kept_copy and kept_copy["ImageWidth"] == "16"


def test_original_whose_copy_disappeared_keeps_its_information():
    doc = document_with_pages()
    answer_delete_originals(doc, [doc.a], [bigger_copy("a.webp")])
    assert written_pages(doc)[0]["Type"] == "FrontCover"


def test_unreadable_copy_keeps_the_information_without_dimensions():
    doc = document_with_pages()
    copy = converted("a.webp")                                # octets non décodables
    insert_after(doc, doc.a, copy)
    answer_delete_originals(doc, [doc.a], [copy])
    first = written_pages(doc)[0]
    assert first["Type"] == "FrontCover" and "ImageWidth" not in first


# ---------------------------------------------------------------------------
# Fenêtres de format et de qualité : elles suivent les pages
# ---------------------------------------------------------------------------

follow = ast_loader.load("conversion_dialogs_qt.py", {"_pages_in_document", "_follow_pages"},
                         entry_key=entry_key)
QUALITY_OPENED = []


def _show_quality(parent, fmt, pages, callbacks, page_keys=None):
    QUALITY_OPENED.append((fmt, pages, page_keys))


FormatBase = ast_loader.load_methods(
    "conversion_dialogs_qt.py", "_ConvertFormatDialog", {"_refresh_from_pages", "_on_convert"},
    _pages_in_document=follow["_pages_in_document"], show_quality_dialog=_show_quality)
QualityBase = ast_loader.load_methods(
    "conversion_dialogs_qt.py", "_QualityDialog", {"_refresh_from_pages", "_on_convert"},
    _pages_in_document=follow["_pages_in_document"], _show_files_info=lambda *args: None)


class FakeRadio:
    def __init__(self, fmt, checked=False, enabled=True):
        self._format_val, self.checked, self.enabled = fmt, checked, enabled

    def isChecked(self):
        return self.checked

    def isEnabled(self):
        return self.enabled

    def setEnabled(self, enabled):
        self.enabled = enabled

    def setChecked(self, checked):
        self.checked = checked


class FakeButton:
    enabled = True

    def setEnabled(self, enabled):
        self.enabled = enabled


class WindowParts:
    """Reste de la fenêtre : pages, boutons, réponses du bouton Convertir."""

    def open_on(self, doc, pages):
        self._state = doc.state
        self._page_keys = [entry_key(e) for e in pages]
        self._selected_entries = list(pages)
        self._convert_btn = FakeButton()
        self.conversions, self.closed = [], False
        self._callbacks = {
            "state": doc.state,
            "perform_conversion":
                lambda fmt, quality, entries, lossless=False: self.conversions.append(list(entries)),
            "show_animated_gif_dialog": lambda entries: self.conversions.append(list(entries)),
        }
        return self

    def close(self):
        self.closed = True


class FormatWindow(WindowParts, FormatBase):
    def open_on(self, doc, pages, checked="PNG"):
        super().open_on(doc, pages)
        self._chosen_format = "PNG"
        self._radios = [FakeRadio(fmt, fmt == checked) for fmt in ("PNG", "JPEG", "GIF_ANIMATED")]
        return self

    def parent(self):
        return None

    def _retranslate(self):
        pass


class QualityWindow(WindowParts, QualityBase):
    _target_format, _quality_value, _lossless_cb, _files_info_lbl = "JPEG", 85, None, None


def _gif_radio(window):
    return next(rb for rb in window._radios if rb._format_val == "GIF_ANIMATED")


@pytest.mark.parametrize("window_class", [FormatWindow, QualityWindow])
def test_deleted_page_leaves_the_window_and_is_not_converted(window_class):
    doc = Document()
    window = window_class().open_on(doc, [doc.a, doc.b])
    doc.state.images_data.remove(doc.b)
    window._refresh_from_pages()
    assert window._selected_entries == [doc.a]
    window._on_convert()
    assert window.conversions == [[doc.a]] and window.closed


@pytest.mark.parametrize("window_class", [FormatWindow, QualityWindow])
def test_page_deleted_without_notice_is_not_converted_either(window_class):
    doc = Document()
    window = window_class().open_on(doc, [doc.a, doc.b])
    doc.state.images_data.remove(doc.b)
    window._on_convert()
    assert window.conversions == [[doc.a]]


@pytest.mark.parametrize("window_class", [FormatWindow, QualityWindow])
def test_page_restored_by_undo_is_the_one_converted(window_class):
    doc = Document()
    window = window_class().open_on(doc, [doc.a, doc.b])
    key = entry_key(doc.b)
    restored = dict(doc.b)                                    # Annuler recrée la page
    adopt_entry_key(restored, key)
    doc.state.images_data[1] = restored
    window._on_convert()
    assert window.conversions[0][1] is restored


@pytest.mark.parametrize("window_class", [FormatWindow, QualityWindow])
def test_without_any_page_the_button_is_greyed_and_the_window_stays(window_class):
    doc = Document()
    window = window_class().open_on(doc, [doc.a])
    doc.state.images_data.remove(doc.a)
    window._refresh_from_pages()
    assert window._convert_btn.enabled is False
    window._on_convert()
    assert window.conversions == [] and not window.closed
    doc.state.images_data.insert(0, doc.a)                    # Annuler
    window._refresh_from_pages()
    assert window._convert_btn.enabled is True


def test_pages_are_converted_in_the_document_order():
    doc = Document()
    window = FormatWindow().open_on(doc, [doc.a, doc.b])
    doc.state.images_data = [doc.c, doc.b, doc.a]
    window._on_convert()
    assert window.conversions == [[doc.b, doc.a]]


def test_other_pages_never_enter_the_window():
    doc = Document()
    window = FormatWindow().open_on(doc, [doc.a])
    doc.state.images_data.append(png("d.png", "white"))
    window._refresh_from_pages()
    assert window._selected_entries == [doc.a]


def test_animated_gif_needs_two_pages_and_comes_back_with_them():
    doc = Document()
    window = FormatWindow().open_on(doc, [doc.a, doc.b], checked="GIF_ANIMATED")
    doc.state.images_data.remove(doc.b)
    window._refresh_from_pages()
    assert not _gif_radio(window).isEnabled() and window._radios[0].isChecked()
    doc.state.images_data.insert(1, doc.b)
    window._refresh_from_pages()
    assert _gif_radio(window).isEnabled()


def test_quality_window_keeps_following_the_whole_selection():
    QUALITY_OPENED.clear()
    doc = Document()
    window = FormatWindow().open_on(doc, [doc.a, doc.b], checked="JPEG")
    doc.state.images_data.remove(doc.b)
    window._on_convert()
    [(fmt, pages, page_keys)] = QUALITY_OPENED
    assert fmt == "JPEG" and pages == [doc.a]
    assert page_keys == [entry_key(doc.a), entry_key(doc.b)]  # b peut encore revenir


def test_window_listens_while_open_and_stops_when_closed_or_destroyed():
    for signal_name in ("finished", "destroyed"):
        doc = Document()
        heard = []
        window = types.SimpleNamespace(_refresh_from_pages=lambda: heard.append(1),
                                       finished=_Signal(), destroyed=_Signal())
        follow["_follow_pages"](window, doc.state)
        doc.state.notify_document_changed()
        assert heard == [1]
        getattr(window, signal_name).emit()
        doc.state.notify_document_changed()
        assert heard == [1]


# ---------------------------------------------------------------------------
# _ConversionWorker._run : vraie conversion
# ---------------------------------------------------------------------------

def test_worker_lists_only_the_pages_really_converted(monkeypatch):
    mosaic = types.ModuleType("modules.qt.mosaic_canvas")
    mosaic.build_qimage_for_entry = lambda entry: None
    monkeypatch.setitem(sys.modules, "modules.qt.mosaic_canvas", mosaic)
    Base = ast_loader.load_methods("conversion_dialogs_qt.py", "_ConversionWorker", {"_run"},
                                   convert_image_data=convert_image_data,
                                   free_image_memory=free_image_memory)

    class Worker(Base):
        def __init__(self, entries):
            self._target_format, self._quality, self._lossless = "WEBP", 90, False
            self._selected_entries = entries
            self._cancelled = threading.Event()
            self.converted_sources, self.failures = [], []
            for name in ("progress", "entry_ready", "finished", "cancelled"):
                setattr(self, name, _Signal())

    good, bad = png("a.png", "red"), create_entry("b.png", b"illisible", IMAGE_EXTS)
    ready = []
    worker = Worker([good, bad])
    worker.entry_ready.connect(lambda source, new_entry: ready.append((source, new_entry)))
    worker._run()
    assert worker.converted_sources == [good]
    assert [f["name"] for f in worker.failures] == ["b.png"]
    [(source, new)] = ready
    assert source is good
    assert Image.open(io.BytesIO(new["bytes"])).format == "WEBP"
