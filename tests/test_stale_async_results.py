"""Résultats asynchrones périmés : un résultat qui arrive après une annulation,
après le lancement d'une opération plus récente, après la fermeture ou le
remplacement du document, ou une seconde fois, est ignoré — il ne modifie ni
le document courant ni l'état du panneau.

Qt ne vérifie la connexion d'un signal qu'à son émission : un résultat émis
juste avant que ses slots soient débranchés (annulation, nouveau chargement)
est quand même livré. Les tests rejouent cette livraison en appelant le slot
avec, pour émetteur (`sender()`), le worker qui l'a émis, dans l'ordre
voulu : aucun thread, aucune attente.

Couverts : chargement d'une archive (ArchiveLoader) et d'un PDF (PdfLoader),
qui remplacent le document ; vérification des mises à jour ComicVine et
« Tout mettre à jour » ; aperçu de la Bibliothèque ; ajout d'une archive ou
d'un PDF au comic ouvert ; images déposées puis archive ouverte.

Les classes testées viennent de modules qui importent PySide6 : leurs
méthodes sont extraites par tests/ast_loader.py et exécutées avec un vrai
AppState ; seuls les workers, l'affichage et les fenêtres sont remplacés.
"""
import inspect
import os
import sys
import threading
import types
import zipfile

import pytest
from PySide6 import QtGui, QtWidgets

import ast_loader
from fakes import Signal, tr
from modules.qt import utils
from modules.qt.archive_names import open_zip
from modules.qt.comic_info import name_incoming_comic_infos
from modules.qt.state import AppState


# ---------------------------------------------------------------------------
# Chargement d'une archive (archive_loader.ArchiveLoader)
# ---------------------------------------------------------------------------

class FakeLoadWorker:
    """LoadWorker sans thread : le test émet lui-même ses signaux."""
    created = []

    def __init__(self, filepaths, multi=False):
        self.filepaths = filepaths
        self.progress, self.load_finished, self.error = Signal(), Signal(), Signal()
        self.cancelled, self.need_ext_dialog, self.file_renamed = Signal(), Signal(), Signal()
        self.finished = Signal()                   # finished natif de QThread
        self._cancelled = threading.Event()
        self.running = True
        self.deleted = False
        FakeLoadWorker.created.append(self)

    def start(self): pass
    def set_ext_result(self, choice): pass
    def setParent(self, parent): pass
    def wait(self): self.running = False
    def deleteLater(self): self.deleted = True

    def isFinished(self):
        return not self.running


class FakeDialog:
    shown = []

    def __init__(self, parent, title_key, text):
        self.title_key = title_key

    def show_nonmodal(self):
        FakeDialog.shown.append(self.title_key)


class FakeScene:
    def clear(self): pass


class FakeCanvas:
    def __init__(self):
        self._empty_items, self._items, self._drop_indicator_items = [], [], []
        self.renders = 0

    def scene(self):
        return FakeScene()

    def render_mosaic(self):
        self.renders += 1


_ORPHANS = []
_install_loaded_pages = ast_loader.load("archive_loader.py",
                                        {"_install_loaded_pages"})["_install_loaded_pages"]
_LoaderMethods = ast_loader.load_methods(
    "archive_loader.py", "ArchiveLoader",
    {"load", "_detach_worker", "cancel", "_cleanup_worker", "_from_current_worker",
     "_on_progress", "_on_finished", "_on_error", "_on_cancelled", "_on_file_renamed",
     "_on_need_ext_dialog"},
    LoadWorker=FakeLoadWorker, _orphan_workers=_ORPHANS, _MessageDialog=FakeDialog,
    ExtensionCorrectionDialog=None,
    _install_loaded_pages=_install_loaded_pages,
    _attach_read_errors=lambda images_data, errors: list(errors),
    _detect_zip_compression_state=ast_loader.load(
        "archive_loader.py", {"_detect_zip_compression_state"},
        os=os, zipfile=zipfile, open_zip=open_zip)["_detect_zip_compression_state"])


class Loader(_LoaderMethods):
    """ArchiveLoader d'un panneau : vrai AppState, affichage remplacé."""

    def __init__(self):
        self._win = type("Win", (), {"_refresh_title": lambda self: None})()
        self._canvas = FakeCanvas()
        self._state = AppState()
        self._worker = None
        self._ext_dialog = None
        self._loading_item_holder = [None]
        self._cancel_item_holder = [None]
        self.loading_started, self.loading_finished = Signal(), Signal()
        self._sender = None

    def _show_loading_text(self, percent): pass
    def _hide_loading_text(self): pass

    def sender(self):
        return self._sender

    def deliver(self, worker, slot, *args):
        """Livraison par Qt d'un signal émis par `worker` (même débranché
        depuis : signal déjà en file d'attente)."""
        self._sender = worker
        try:
            getattr(self, slot)(*args)
        finally:
            self._sender = None


def page(name):
    return {"orig_name": name, "name": name, "bytes": name.encode(), "extension": ".jpg",
            "is_image": True, "is_dir": False}


@pytest.fixture
def loader():
    FakeLoadWorker.created.clear()
    FakeDialog.shown.clear()
    _ORPHANS.clear()
    return Loader()


def names(state):
    return [e["orig_name"] for e in state.images_data]


def test_result_of_the_current_load_is_installed(loader):
    """Témoin : sans résultat périmé, le fichier chargé devient le document."""
    loader.load(["Aaaa.cbz"])
    [worker] = FakeLoadWorker.created
    loader.deliver(worker, "_on_finished", [page("a1.jpg"), page("a2.jpg")], [], "Aaaa.cbz")
    assert names(loader._state) == ["a1.jpg", "a2.jpg"]
    assert loader._state.current_file == "Aaaa.cbz"
    assert len(loader.loading_finished.emitted) == 1
    assert loader._worker is None and worker.deleted


def test_result_of_a_replaced_load_never_reaches_the_new_document(loader):
    """A est remplacé par B ; la fin de A, émise avant son débranchement, est
    livrée ensuite : rien de A n'est installé, B reste le chargement courant
    et s'installe seul à sa fin."""
    loader.load(["Aaaa.cbz"])
    loader.load(["Bbbb.cbz"])
    worker_a, worker_b = FakeLoadWorker.created
    generation_b = loader._state.doc_generation
    loader.deliver(worker_a, "_on_finished", [page("a1.jpg")], [], "Aaaa.cbz")
    assert names(loader._state) == []
    assert loader._state.current_file == "Bbbb.cbz"
    assert loader._state.doc_generation == generation_b
    assert loader._worker is worker_b and not worker_b.deleted
    assert loader.loading_finished.emitted == []
    loader.deliver(worker_b, "_on_finished", [page("b1.jpg")], [], "Bbbb.cbz")
    assert names(loader._state) == ["b1.jpg"]
    assert loader._state.current_file == "Bbbb.cbz"


@pytest.mark.parametrize("slot, args", [("_on_cancelled", ()), ("_on_error", ("erreur",)),
                                        ("_on_file_renamed", ("Aaaa.cbr",)),
                                        ("_on_need_ext_dialog", ("Aaaa.cbr", "CBZ", ".cbr"))])
def test_late_end_of_a_replaced_load_leaves_the_new_one_alone(loader, slot, args):
    """Annulation, erreur, renommage d'extension ou question sur l'extension
    de A livrés pendant le chargement de B : B garde son fichier, aucune
    fenêtre (la fenêtre de question est remplacée par None : l'ouvrir lèverait
    une exception), aucune fin de chargement annoncée."""
    loader.load(["Aaaa.cbz"])
    loader.load(["Bbbb.cbz"])
    worker_a, worker_b = FakeLoadWorker.created
    loader.deliver(worker_a, slot, *args)
    assert loader._state.current_file == "Bbbb.cbz"
    assert loader._worker is worker_b
    assert FakeDialog.shown == []
    assert loader.loading_finished.emitted == []


def test_result_queued_before_cancel_is_ignored(loader):
    """Clic sur Annuler traité avant la fin déjà émise : le fichier annulé
    n'est jamais installé, et la fin du chargement n'est annoncée qu'une
    fois (par l'annulation)."""
    loader.load(["Aaaa.cbz"])
    [worker] = FakeLoadWorker.created
    loader.cancel()
    assert worker._cancelled.is_set()
    loader.deliver(worker, "_on_finished", [page("a1.jpg")], [], "Aaaa.cbz")
    assert names(loader._state) == []
    assert loader._state.current_file is None
    assert len(loader.loading_finished.emitted) == 1


def test_error_then_cancelled_of_the_same_load_ends_it_once(loader):
    """Un échec du renommage d'extension émet `error` puis `cancelled` : une
    seule fin (une fenêtre d'erreur, une seule annonce), sans quoi
    l'historique et les fichiers récents seraient traités deux fois."""
    loader.load(["Aaaa.cbr"])
    [worker] = FakeLoadWorker.created
    loader.deliver(worker, "_on_error", "renommage impossible")
    loader.deliver(worker, "_on_cancelled")
    assert FakeDialog.shown == ["messages.warnings.cannot_open_file.title"]
    assert len(loader.loading_finished.emitted) == 1


def test_finished_delivered_twice_is_installed_once(loader):
    loader.load(["Aaaa.cbz"])
    [worker] = FakeLoadWorker.created
    loader.deliver(worker, "_on_finished", [page("a1.jpg")], [], "Aaaa.cbz")
    loader._state.images_data.append(page("ajout.jpg"))
    loader.deliver(worker, "_on_finished", [page("a1.jpg")], [], "Aaaa.cbz")
    assert names(loader._state) == ["a1.jpg", "ajout.jpg"]
    assert len(loader.loading_finished.emitted) == 1


# ---------------------------------------------------------------------------
# Chargement d'un PDF (pdf_loading_qt.PdfLoader)
# ---------------------------------------------------------------------------

class FakePdfWorker:
    """PdfLoadWorker sans thread ni process."""
    created = []
    SIGNALS = ("progress", "finished", "error", "cancelled", "password_error", "empty_pdf",
               "no_images", "scanning")

    def __init__(self, filepath, dpi, process, in_q, out_conn, images_only=False,
                 join_cut_images=False):
        self.filepath = filepath
        for name in self.SIGNALS:
            setattr(self, name, Signal())
        self._cancelled = threading.Event()
        self.page_errors = []
        self.started = self.killed = self.deleted = False
        FakePdfWorker.created.append(self)

    def start(self): self.started = True
    def _kill_own_process(self): self.killed = True
    def wait(self): pass
    def deleteLater(self): self.deleted = True

    def queued(self, name):
        """Slots branchés sur le signal `name` au moment où le worker l'émet :
        livrés par Qt même si le signal est débranché avant leur exécution."""
        slots = list(getattr(self, name)._slots)

        def deliver(*args):
            for slot in slots:
                slot(*args)
        return deliver


class PdfWin:
    def __init__(self):
        self.loading_finished = 0

    def _on_loading_finished(self):
        self.loading_finished += 1


_PdfLoaderMethods = ast_loader.load_methods(
    "pdf_loading_qt.py", "PdfLoader",
    {"_detach_worker", "cancel", "_start_pdf_loading", "_cleanup_worker", "_on_finished",
     "_on_cancelled"},
    PdfLoadWorker=FakePdfWorker,
    _warn_pdf_pages_ignored=lambda win, page_errors: None)


class PdfLoader(_PdfLoaderMethods):
    def __init__(self):
        self._win = PdfWin()
        self._canvas = FakeCanvas()
        self._state = AppState()
        self._worker = None
        self._process = self._in_q = self._out_conn = None

    def _show_overlay(self, *args, **kwargs): pass
    def _hide_overlay(self): pass


@pytest.fixture
def pdf_loader(monkeypatch):
    FakePdfWorker.created.clear()
    parked = []
    monkeypatch.setattr(utils, "park_qthread", parked.append)
    # _on_finished importe _install_loaded_pages d'archive_loader : sa vraie
    # fonction, sans charger le module (qui construit des fenêtres Qt)
    monkeypatch.setitem(sys.modules, "modules.qt.archive_loader",
                        types.SimpleNamespace(_install_loaded_pages=_install_loaded_pages))
    loader = PdfLoader()
    loader.process_events = lambda: None
    # Traitement des événements avant le branchement des signaux du worker :
    # le test y place ce que l'utilisateur fait à ce moment-là
    monkeypatch.setattr(QtWidgets, "QApplication",
                        types.SimpleNamespace(processEvents=lambda: loader.process_events()))
    loader.parked = parked
    return loader


def test_pdf_result_of_the_current_load_is_installed(pdf_loader):
    pdf_loader._start_pdf_loading("Aaaa.pdf", 150)
    [worker] = FakePdfWorker.created
    assert worker.started
    worker.finished.emit([page("p1.jpg")], False)
    assert names(pdf_loader._state) == ["p1.jpg"]
    assert pdf_loader._worker is None and worker.deleted
    assert pdf_loader._win.loading_finished == 1


def test_pdf_result_queued_before_cancel_is_ignored(pdf_loader):
    pdf_loader._start_pdf_loading("Aaaa.pdf", 150)
    [worker] = FakePdfWorker.created
    late_finished = worker.queued("finished")
    pdf_loader.cancel()
    late_finished([page("p1.jpg")], False)
    assert names(pdf_loader._state) == []
    assert pdf_loader._state.current_file is None
    assert pdf_loader._win.loading_finished == 1           # annoncée par l'annulation seule
    assert worker._cancelled.is_set() and pdf_loader.parked == [worker]


def test_pdf_result_of_a_replaced_load_never_reaches_the_new_one(pdf_loader):
    """Fin de A livrée après le démarrage de B : rien de A n'est installé,
    B n'est ni libéré ni arrêté, et sa propre fin s'installe."""
    pdf_loader._start_pdf_loading("Aaaa.pdf", 150)
    worker_a = FakePdfWorker.created[0]
    late_finished, late_cancelled = worker_a.queued("finished"), worker_a.queued("cancelled")
    pdf_loader._state.current_file = "Bbbb.pdf"
    pdf_loader._start_pdf_loading("Bbbb.pdf", 150)
    worker_b = FakePdfWorker.created[1]
    late_finished([page("a1.jpg")], False)
    late_cancelled()
    assert names(pdf_loader._state) == []
    assert pdf_loader._state.current_file == "Bbbb.pdf"
    assert pdf_loader._worker is worker_b and not worker_b.deleted
    assert pdf_loader._win.loading_finished == 0
    worker_b.finished.emit([page("b1.jpg")], False)
    assert names(pdf_loader._state) == ["b1.jpg"]


def test_pdf_load_cancelled_while_events_are_processed_kills_its_process(pdf_loader):
    """Annuler (ou fermeture du fichier) traité pendant le traitement des
    événements qui précède le démarrage du worker : le worker ne démarre
    pas, n'est branché à rien, et le process PDF qui lui était confié est
    arrêté — il ne le ferait jamais lui-même."""
    pdf_loader.process_events = pdf_loader.cancel
    pdf_loader._start_pdf_loading("Aaaa.pdf", 150)
    [worker] = FakePdfWorker.created
    assert worker.killed and not worker.started
    assert all(getattr(worker, name)._slots == [] for name in FakePdfWorker.SIGNALS)
    assert pdf_loader._worker is None


# ---------------------------------------------------------------------------
# Vérification des mises à jour ComicVine (comicvine_update_check_qt)
# ---------------------------------------------------------------------------

class _TruncatingSignal(Signal):
    """Comme un signal PySide : un slot qui attend moins d'arguments que le
    signal n'en émet ne reçoit que les premiers (`_cleanup()` est branché sur
    `finished(dict)`)."""

    def emit(self, *args):
        self.emitted.append(args)
        for slot in list(self._slots):
            params = inspect.signature(slot).parameters.values()
            if any(p.kind == p.VAR_POSITIONAL for p in params):
                slot(*args)
            else:
                slot(*args[:len(params)])


class FakeCheckWorker:
    created = []

    def __init__(self, api_key, issue_id):
        self.finished, self.error = _TruncatingSignal(), _TruncatingSignal()
        self.started = self.deleted = False
        FakeCheckWorker.created.append(self)

    def start(self): self.started = True
    def wait(self): pass
    def deleteLater(self): self.deleted = True


class Shown:
    """Fenêtres ouvertes : (sorte, fenêtre)."""
    windows = []


def _fake_window(kind):
    class Window:
        def __init__(self, parent, *args, **kwargs):
            self.kwargs = kwargs
            Shown.windows.append((kind, self))

        def show_nonmodal(self): pass
        def show(self): pass
        def raise_(self): pass
        def activateWindow(self): pass
    return Window


class BusyButton:
    def __init__(self, destroyed=False):
        self.destroyed, self.enabled, self.checking = destroyed, True, False

    def setProperty(self, name, value):
        if self.destroyed:
            raise RuntimeError("Internal C++ object already deleted")
        self.checking = value

    def setEnabled(self, value):
        self.enabled = value

    def setText(self, text): pass


_RUNNING_CHECKS = []
_update_check = ast_loader.load(
    "comicvine_update_check_qt.py", {"show_comicvine_update_check"},
    _CheckUpdateWorker=FakeCheckWorker, _running_workers=_RUNNING_CHECKS,
    _UpdateDiffDialog=_fake_window("diff"), _=tr, _wt=tr)["show_comicvine_update_check"]
_DiffMethods = ast_loader.load_methods(
    "comicvine_update_check_qt.py", "_UpdateDiffDialog", {"_on_update_clicked"}, _=tr, _wt=tr)

REMOTE = {"series": "Aaaa", "title": "Bbbb"}


@pytest.fixture
def update_check(monkeypatch):
    FakeCheckWorker.created.clear()
    Shown.windows.clear()
    _RUNNING_CHECKS.clear()
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", types.SimpleNamespace(
        InfoDialog=_fake_window("info"), ErrorDialog=_fake_window("error"),
        position_dialog_on_parent=lambda *a: None))
    state = AppState()
    state.comic_metadata = {"series": "Aaaa"}
    return state


def start_check(state, busy=None):
    _update_check(None, state, "cle", 1, busy_widget=busy)
    [worker] = FakeCheckWorker.created
    return worker


def test_update_check_answer_for_the_current_document_shows_the_differences(update_check):
    worker = start_check(update_check)
    worker.finished.emit(dict(REMOTE))
    [(kind, window)] = Shown.windows
    assert kind == "diff" and window.kwargs["generation"] == update_check.doc_generation
    assert worker.deleted and _RUNNING_CHECKS == []


@pytest.mark.parametrize("signal, payload", [("finished", dict(REMOTE)), ("error", "réseau")])
def test_update_check_answer_after_the_document_changed_shows_nothing(update_check, signal,
                                                                      payload):
    """Réponse réseau arrivée après la fermeture du comic ou l'ouverture d'un
    autre : aucune fenêtre (différences, « à jour » ou erreur) ; bouton
    rendu, worker libéré."""
    busy = BusyButton()
    worker = start_check(update_check, busy)
    assert busy.checking and not busy.enabled
    update_check.doc_generation += 1
    getattr(worker, signal).emit(payload)
    assert Shown.windows == []
    assert not busy.checking and busy.enabled
    assert worker.deleted and _RUNNING_CHECKS == []


def test_update_check_answer_after_its_button_was_destroyed(update_check):
    """Bouton détruit avec son onglet ou sa fenêtre pendant la requête : la
    réponse s'affiche quand même, sans exception."""
    busy = BusyButton()
    worker = start_check(update_check, busy)
    busy.destroyed = True
    worker.finished.emit(dict(REMOTE))
    assert [kind for kind, _w in Shown.windows] == ["diff"]
    assert worker.deleted


class DiffWindow(_DiffMethods):
    """Fenêtre des différences ouverte pour `state`, à la génération
    `generation` du document."""

    def __init__(self, state, generation):
        self._state, self._generation = state, generation
        self._remote_meta, self._center_parent = dict(REMOTE), None
        self.done = []
        self._on_done = lambda: self.done.append(True)
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def written(monkeypatch):
    from modules.qt import comic_info
    calls = []
    monkeypatch.setattr(comic_info, "write_comic_metadata_from_scraper",
                        lambda state, meta: calls.append((state, meta)))
    return calls


def test_update_all_writes_into_the_document_it_was_compared_with(update_check, written):
    window = DiffWindow(update_check, update_check.doc_generation)
    window._on_update_clicked()
    assert written == [(update_check, REMOTE)] and window.done == [True]
    assert window.closed
    assert [kind for kind, _w in Shown.windows] == ["info"]


def test_update_all_after_the_document_changed_writes_nothing(update_check, written):
    """« Tout mettre à jour » cliqué après la fermeture du comic comparé et
    l'ouverture d'un autre dans le panneau : rien n'est écrit dans le
    nouveau document, aucune confirmation, fenêtre fermée."""
    window = DiffWindow(update_check, update_check.doc_generation)
    update_check.doc_generation += 1
    window._on_update_clicked()
    assert written == [] and window.done == []
    assert window.closed and Shown.windows == []


def test_update_all_during_an_operation_waits_for_its_end(update_check, written):
    """Clic pendant une rotation : écrit à la fin de l'opération (une
    annulation l'effacerait sinon)."""
    window = DiffWindow(update_check, update_check.doc_generation)
    rotation = object()
    update_check.begin_operation(rotation)
    window._on_update_clicked()
    assert written == []
    update_check.end_operation(rotation)
    assert written == [(update_check, REMOTE)]


# ---------------------------------------------------------------------------
# Aperçu de la Bibliothèque (library_window.LibraryWindow)
# ---------------------------------------------------------------------------

class _ReadySignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot, connection_type=None):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots.remove(slot)


class FakePreviewWorker:
    created = []

    def __init__(self, path, width, height, pdf_probe):
        self.path, self.pdf_probe = path, pdf_probe
        self.ready = _ReadySignal()
        self.cancelled = False
        FakePreviewWorker.created.append(self)

    def start(self): pass

    def isRunning(self):
        return True


class Cover:
    """QImage de la couverture lue par un worker d'aperçu."""

    def __init__(self, path):
        self.path = path

    def isNull(self):
        return False


class PreviewLabel:
    def __init__(self):
        self.shown = None

    def show_unavailable(self, text, font=None):
        self.shown = "indisponible"

    def clear_pixmap(self):
        self.shown = None

    def set_source_pixmap(self, pixmap):
        self.shown = pixmap


_PreviewMethods = ast_loader.load_methods(
    "library_window.py", "LibraryWindow",
    {"_clear_preview", "_cancel_preview", "_start_preview_worker", "_on_preview_ready"},
    _PreviewWorker=FakePreviewWorker, _=tr, os=os,
    Qt=types.SimpleNamespace(QueuedConnection="queued"))


class Library(_PreviewMethods):
    def __init__(self, paths):
        self._db = types.SimpleNamespace(get_absolute_path=paths.get)
        self._preview_worker = self._preview_probe = None
        self._preview_label = PreviewLabel()
        self._preview_panel = types.SimpleNamespace(width=lambda: 300, height=lambda: 400)
        self._sender = None

    def _update_preview_info(self, row, path): pass
    def _populate_meta(self, meta): pass

    def sender(self):
        return self._sender

    def select(self, comic_id):
        self._start_preview_worker(comic_id)
        return FakePreviewWorker.created[-1] if self._preview_worker else None

    def deliver(self, worker):
        """Couverture émise par `worker` (slots captés à l'émission)."""
        self._sender = worker
        try:
            for slot in list(worker.ready.slots) or [self._on_preview_ready]:
                slot(Cover(worker.path))
        finally:
            self._sender = None


@pytest.fixture
def library(tmp_path, monkeypatch):
    FakePreviewWorker.created.clear()
    parked = []
    monkeypatch.setattr(utils, "park_qthread", parked.append)
    monkeypatch.setitem(sys.modules, "modules.qt.font_manager_qt",
                        types.SimpleNamespace(get_current_font=lambda size: None))
    monkeypatch.setattr(QtGui, "QPixmap",
                        types.SimpleNamespace(fromImage=lambda image: f"couverture {image.path}"))
    a, b = tmp_path / "Aaaa.cbz", tmp_path / "Bbbb.cbz"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    window = Library({1: str(a), 2: str(b), 3: str(tmp_path / "absent.cbz")})
    window.parked = parked
    return window


def test_preview_of_the_selected_comic_is_shown(library):
    worker = library.select(1)
    library.deliver(worker)
    assert library._preview_label.shown == f"couverture {worker.path}"


def test_preview_of_the_previous_selection_never_replaces_the_current_one(library):
    """A sélectionné, puis B : la couverture de A, émise avant l'arrêt de
    son worker, arrive après celle de B ou avant — seule celle de B est
    affichée."""
    worker_a = library.select(1)
    worker_b = library.select(2)
    assert worker_a.cancelled and library.parked == [worker_a]
    library.deliver(worker_b)
    library.deliver(worker_a)
    assert library._preview_label.shown == f"couverture {worker_b.path}"


def test_preview_of_the_previous_selection_never_hides_unavailable(library):
    """B n'existe plus sur le disque : « aperçu indisponible » reste affiché
    quand la couverture de A arrive."""
    worker_a = library.select(1)
    assert library.select(3) is None
    library.deliver(worker_a)
    assert library._preview_label.shown == "indisponible"


def test_preview_arriving_after_the_database_changed_is_ignored(library):
    """Autre base ouverte (aperçu vidé) avant l'arrivée de la couverture."""
    worker_a = library.select(1)
    library._clear_preview()
    library.deliver(worker_a)
    assert library._preview_label.shown is None


# ---------------------------------------------------------------------------
# Ajout d'une archive au comic ouvert (import_merge_qt.import_and_merge_archive)
# ---------------------------------------------------------------------------

class FakeMergeWorker:
    created = []
    SIGNALS = ("progress", "finished", "cancelled", "error", "bad_zip_file", "bad_rar_file")

    def __init__(self, filepath, merge_prefix):
        for name in self.SIGNALS:
            setattr(self, name, Signal())
        self._cancelled = threading.Event()
        self.started = self.deleted = False
        FakeMergeWorker.created.append(self)

    def start(self): self.started = True
    def wait(self): pass
    def deleteLater(self): self.deleted = True

    queued = FakePdfWorker.queued


class MergeCanvas(FakeCanvas):
    def __init__(self):
        super().__init__()
        self.history_points = 0
        self._save_state_callback = self._save_state

    def _save_state(self):
        self.history_points += 1

    def hold_empty_hint(self, token): pass
    def release_empty_hint(self, token): pass


_merge_archive = ast_loader.load(
    "import_merge_qt.py", {"import_and_merge_archive"},
    ImportMergeWorker=FakeMergeWorker, _=tr, os=os,
    _show_canvas_text=lambda canvas, text, holder: None,
    _hide_canvas_text=lambda canvas, holder: None,
    _MsgDialog=lambda *args, **kwargs: FakeDialog(None, args[1], None),
    ExtensionCorrectionDialog=None,
    name_incoming_comic_infos=name_incoming_comic_infos,
    _natural_sort_key=lambda name: name)["import_and_merge_archive"]


@pytest.fixture
def merge(monkeypatch):
    FakeMergeWorker.created.clear()
    FakeDialog.shown.clear()
    monkeypatch.setitem(sys.modules, "modules.qt.web_import_qt",
                        types.SimpleNamespace(_show_cancel_item=lambda *a, **k: None))
    state = AppState()
    state.images_data = [page("p01.jpg")]
    canvas = MergeCanvas()
    _merge_archive("Bbbb.cbz", None, canvas, state)
    [worker] = FakeMergeWorker.created
    return types.SimpleNamespace(state=state, canvas=canvas, worker=worker)


def test_merged_pages_join_the_comic(merge):
    """Témoin : pages ajoutées, document modifié, point d'historique avant et
    après, worker libéré et retiré des annulations à la fermeture."""
    merge.worker.finished.emit([page("NEW01-p01.jpg")], [])
    assert names(merge.state) == ["NEW01-p01.jpg", "p01.jpg"]
    assert merge.state.modified and merge.canvas.history_points == 2
    assert merge.worker.deleted
    assert getattr(merge.canvas, "_cancel_on_close", []) == []


def test_merge_result_queued_before_cancel_adds_nothing(merge):
    late_finished = merge.worker.queued("finished")
    late_error = merge.worker.queued("error")
    [cancel] = merge.canvas._cancel_on_close              # lien Annuler
    cancel()
    assert merge.worker._cancelled.is_set()
    late_finished([page("NEW01-p01.jpg")], [])
    late_error("erreur")
    assert names(merge.state) == ["p01.jpg"]
    assert not merge.state.modified and merge.canvas.history_points == 0
    assert FakeDialog.shown == []


def test_merge_result_after_the_file_was_closed_adds_nothing(merge):
    """Fermeture du fichier pendant l'ajout : annulation inscrite appelée par
    force_close_file, nouveau document ; la fin déjà émise n'y ajoute rien."""
    late_finished = merge.worker.queued("finished")
    utils.cancel_operations_on_close(merge.canvas)
    merge.state.doc_generation += 1
    merge.state.images_data = []
    late_finished([page("NEW01-p01.jpg")], [])
    assert names(merge.state) == []


def test_merge_result_for_a_replaced_document_adds_nothing(merge):
    """Document remplacé sans que l'ajout ait été annulé : seule la
    génération du document l'arrête."""
    merge.state.doc_generation += 1
    merge.state.images_data = [page("autre.jpg")]
    merge.worker.finished.emit([page("NEW01-p01.jpg")], [])
    assert names(merge.state) == ["autre.jpg"]
    assert merge.canvas.history_points == 0


def test_merge_result_during_an_operation_waits_for_its_end(merge):
    """Rotation en cours : les pages sont ajoutées à sa fin (son annulation
    les effacerait sinon), et pas du tout si le document change avant."""
    rotation = object()
    merge.state.begin_operation(rotation)
    merge.worker.finished.emit([page("NEW01-p01.jpg")], [])
    assert names(merge.state) == ["p01.jpg"]
    merge.state.end_operation(rotation)
    assert names(merge.state) == ["NEW01-p01.jpg", "p01.jpg"]


def test_merge_result_during_an_operation_dropped_if_the_document_changes(merge):
    rotation = object()
    merge.state.begin_operation(rotation)
    merge.worker.finished.emit([page("NEW01-p01.jpg")], [])
    merge.state.doc_generation += 1
    merge.state.end_operation(rotation)
    assert names(merge.state) == ["p01.jpg"]


# ---------------------------------------------------------------------------
# Images déposées, puis archive ouverte avant la fin de leur chargement
# (panel_widget.PanelWidget)
# ---------------------------------------------------------------------------

class FakeImageWorker:
    created = []

    def __init__(self, files, already_open, image_exts, existing_names=None):
        self._files = list(files)
        self.progress, self.finished, self.cancelled = Signal(), Signal(), Signal()
        self._cancelled = threading.Event()
        self.errors = []
        FakeImageWorker.created.append(self)

    def start(self): pass
    def wait(self): pass
    def deleteLater(self): pass


class PanelCanvas(FakeCanvas):
    def __init__(self):
        super().__init__()
        self.hints = set()

    def hold_empty_hint(self, token):
        self.hints.add(token)

    def release_empty_hint(self, token):
        self.hints.discard(token)


_PanelMethods = ast_loader.load_methods(
    "panel_widget.py", "PanelWidget",
    {"_start_image_load", "_stop_image_load", "_start_replacing_load", "_run_pending_loads",
     "_replacing_load_in_progress", "_shutdown_loading_on_close"},
    _ImageLoadWorker=FakeImageWorker, _IMAGE_LOAD_HINT=object(), _=tr, os=os,
    _image_load_error_text=str)


class Panel(_PanelMethods):
    def __init__(self):
        self._state = AppState()
        self._canvas = PanelCanvas()
        self._image_worker = None
        self._image_overlay_holder, self._image_cancel_holder = [None], [None]
        self._loader = types.SimpleNamespace(_worker=None, shutdown=lambda: None)
        self._pdf_loader = types.SimpleNamespace(_worker=None, _process=None)
        self._pending_load_paths = []
        self.loads = []                        # fichiers repris par _load_files

    def _load_files(self, paths, **kwargs):
        self.loads.append(list(paths))

    def _on_loading_finished(self): pass

    def drop_images(self, files):
        self._start_image_load(files, False, (".png",), self._state)
        return FakeImageWorker.created[-1]

    def open_archive(self):
        """Comme ArchiveLoader.load : nouveau document, chargement en cours."""
        def start():
            self._state.doc_generation += 1
            self._state.images_data = []
            self._loader._worker = object()
        self._start_replacing_load(start)

    def archive_loaded(self, pages):
        self._state.images_data = pages
        self._loader._worker = None
        self._run_pending_loads()


@pytest.fixture
def panel(monkeypatch):
    FakeImageWorker.created.clear()
    monkeypatch.setitem(sys.modules, "modules.qt.canvas_overlay_qt", types.SimpleNamespace(
        show_canvas_text=lambda *a: None, hide_canvas_text=lambda *a: None))
    monkeypatch.setitem(sys.modules, "modules.qt.web_import_qt",
                        types.SimpleNamespace(_show_cancel_item=lambda *a, **k: None))
    monkeypatch.setitem(sys.modules, "modules.qt.archive_loader", types.SimpleNamespace(
        _natural_sort_key=lambda name: name, _MessageDialog=FakeDialog))
    return Panel()


def test_dropped_images_are_added_when_their_load_ends(panel):
    """Témoin : images ajoutées au document, texte de progression retiré."""
    worker = panel.drop_images(["a.png"])
    worker.finished.emit([page("a.png")])
    assert names(panel._state) == ["a.png"] and panel._state.modified
    assert panel._image_worker is None and panel._canvas.hints == set()


def test_images_interrupted_by_an_archive_are_added_to_it_once_loaded(panel):
    """Images déposées dans le panneau vide, puis archive ouverte avant la fin
    de leur chargement : le chargement d'images est arrêté (texte retiré),
    son résultat tardif n'ajoute rien au document en cours de chargement, et
    ses fichiers sont repris une fois l'archive chargée, dans ce document."""
    worker = panel.drop_images(["a.png", "b.png"])
    panel.open_archive()
    assert worker._cancelled.is_set()
    assert panel._image_worker is None and panel._canvas.hints == set()
    worker.finished.emit([page("a.png"), page("b.png")])
    assert names(panel._state) == []
    assert panel.loads == []                   # pas avant la fin de l'archive
    panel.archive_loaded([page("p01.jpg")])
    assert panel.loads == [["a.png", "b.png"]]
    assert names(panel._state) == ["p01.jpg"]


def test_interrupted_images_are_dropped_when_the_archive_is_closed(panel):
    """Archive fermée pendant son chargement : l'utilisateur a renoncé à ce
    document, les images interrompues ne sont reprises nulle part."""
    panel.drop_images(["a.png"])
    panel.open_archive()
    panel._shutdown_loading_on_close()
    panel._state.doc_generation += 1
    panel._loader._worker = None
    panel._run_pending_loads()
    assert panel.loads == []


def test_images_of_a_replaced_image_load_are_ignored(panel):
    """Second dépôt avant la fin du premier : seul le second est ajouté, et
    la fin du premier ne retire pas le texte de progression du second."""
    first = panel.drop_images(["a.png"])
    second = panel.drop_images(["b.png"])
    assert first._cancelled.is_set()
    first.finished.emit([page("a.png")])
    first.cancelled.emit()
    assert names(panel._state) == [] and panel._image_worker is second
    second.finished.emit([page("b.png")])
    assert names(panel._state) == ["b.png"]


# ---------------------------------------------------------------------------
# Ajout d'un PDF au comic ouvert (pdf_loading_qt.import_and_merge_pdf)
# ---------------------------------------------------------------------------

class FakePdfMergeWorker:
    created = []
    SIGNALS = ("progress", "finished", "error", "password_error", "empty_pdf", "no_images",
               "scanning", "cancelled")

    def __init__(self, filepath, dpi, merge_prefix, images_only=False, join_cut_images=False):
        for name in self.SIGNALS:
            setattr(self, name, Signal())
        self._cancelled = threading.Event()
        self.page_errors = []
        self.started = self.deleted = False
        FakePdfMergeWorker.created.append(self)

    def start(self): self.started = True
    def wait(self): pass
    def deleteLater(self): self.deleted = True

    queued = FakePdfWorker.queued


_merge_pdf = ast_loader.load(
    "pdf_loading_qt.py", {"import_and_merge_pdf"},
    PDF_AVAILABLE=True, PdfMergeWorker=FakePdfMergeWorker, _=tr, _wt=tr, os=os,
    _MsgDialog=lambda *args, **kwargs: FakeDialog(None, args[1], None),
    _show_canvas_text=lambda canvas, text, holder: None,
    _hide_canvas_text=lambda canvas, holder: None,
    _warn_pdf_pages_ignored=lambda win, errors: None,
    pdf_error_text=str)["import_and_merge_pdf"]


@pytest.fixture
def merge_pdf(monkeypatch):
    FakePdfMergeWorker.created.clear()
    FakeDialog.shown.clear()
    monkeypatch.setitem(sys.modules, "modules.qt.web_import_qt",
                        types.SimpleNamespace(_show_cancel_item=lambda *a, **k: None))
    monkeypatch.setitem(sys.modules, "modules.qt.archive_loader",
                        types.SimpleNamespace(_natural_sort_key=lambda name: name))
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt",
                        types.SimpleNamespace(ErrorDialog=_fake_window("error")))
    Shown.windows.clear()
    ctx = types.SimpleNamespace(state=AppState(), canvas=MergeCanvas(), process_events=None)
    ctx.state.images_data = [page("p01.jpg")]
    monkeypatch.setattr(QtWidgets, "QApplication", types.SimpleNamespace(
        processEvents=lambda: ctx.process_events and ctx.process_events()))

    def start():
        _merge_pdf("Bbbb.pdf", 150, None, ctx.canvas, ctx.state)
        return FakePdfMergeWorker.created[-1] if FakePdfMergeWorker.created else None
    ctx.start = start
    return ctx


def test_merged_pdf_pages_join_the_comic(merge_pdf):
    worker = merge_pdf.start()
    assert worker.started
    worker.finished.emit([page("NEW01-p001.jpg")], False)
    assert names(merge_pdf.state) == ["NEW01-p001.jpg", "p01.jpg"]
    assert merge_pdf.state.modified and merge_pdf.canvas.history_points == 2
    assert worker.deleted and getattr(merge_pdf.canvas, "_cancel_on_close", []) == []


def test_merged_pdf_result_queued_before_cancel_adds_nothing(merge_pdf):
    worker = merge_pdf.start()
    late = {name: worker.queued(name) for name in ("finished", "error", "password_error")}
    [cancel] = merge_pdf.canvas._cancel_on_close          # lien Annuler
    cancel()
    late["finished"]([page("NEW01-p001.jpg")], False)
    late["error"]("erreur")
    late["password_error"]()
    assert names(merge_pdf.state) == ["p01.jpg"] and merge_pdf.canvas.history_points == 0
    assert FakeDialog.shown == [] and Shown.windows == []


def test_merged_pdf_result_after_the_file_was_closed_adds_nothing(merge_pdf):
    worker = merge_pdf.start()
    late_finished = worker.queued("finished")
    utils.cancel_operations_on_close(merge_pdf.canvas)
    merge_pdf.state.doc_generation += 1
    merge_pdf.state.images_data = []
    late_finished([page("NEW01-p001.jpg")], False)
    assert names(merge_pdf.state) == []


def test_merged_pdf_result_for_a_replaced_document_adds_nothing(merge_pdf):
    worker = merge_pdf.start()
    merge_pdf.state.doc_generation += 1
    worker.finished.emit([page("NEW01-p001.jpg")], False)
    assert names(merge_pdf.state) == ["p01.jpg"] and merge_pdf.canvas.history_points == 0


def test_merged_pdf_result_during_an_operation_waits_for_its_end(merge_pdf):
    worker = merge_pdf.start()
    rotation = object()
    merge_pdf.state.begin_operation(rotation)
    worker.finished.emit([page("NEW01-p001.jpg")], False)
    assert names(merge_pdf.state) == ["p01.jpg"]
    merge_pdf.state.end_operation(rotation)
    assert names(merge_pdf.state) == ["NEW01-p001.jpg", "p01.jpg"]


def test_pdf_merge_not_started_when_the_file_is_closed_while_events_are_processed(merge_pdf):
    """Fermeture du fichier traitée pendant le traitement des événements qui
    précède la création du worker, avant l'inscription de son annulation :
    la fusion n'est pas lancée — sinon elle viserait le document suivant et
    y ajouterait les pages du PDF."""
    def close_file():
        merge_pdf.state.doc_generation += 1
        merge_pdf.state.images_data = []
    merge_pdf.process_events = close_file
    assert merge_pdf.start() is None
    assert getattr(merge_pdf.canvas, "_cancel_on_close", []) == []
    assert names(merge_pdf.state) == []
