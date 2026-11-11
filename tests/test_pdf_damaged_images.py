"""Image abîmée dans un PDF : les pages saines sont gardées, la page abîmée est
signalée, jamais ressortie blanche en silence ni cause de l'abandon du PDF.

Le vrai moteur (pdf_loading_qt._pdf_persistent_process, extrait par ast_loader)
tourne ici dans le process du test, sur des PDF écrits à la main : PyMuPDF
refuse d'en créer un avec une image JPEG 2000 illisible.
"""
import io
import queue
import sys
import threading
import types
import zlib

import pytest
from PIL import Image

fitz = pytest.importorskip("pymupdf")

import ast_loader
from fakes import Signal, tr
from modules.qt import error_log
from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import create_entry, is_altered, mark_altered
from modules.qt.pdf_page_images import image_damage, original_images, page_resolution, raw_page_image

BAD_IMAGE = (b"<</Type/XObject/Subtype/Image/Width 200/Height 300/Filter/JPXDecode"
             b"/ColorSpace/DeviceRGB/BitsPerComponent 8/Length 360>>stream\n"
             + b"\x00\x01garbage" * 40 + b"\nendstream")


def good_image():
    buf = io.BytesIO()
    Image.new("RGB", (200, 300), (0, 120, 200)).save(buf, "JPEG", quality=90)
    jpeg = buf.getvalue()
    return (b"<</Type/XObject/Subtype/Image/Width 200/Height 300/Filter/DCTDecode"
            b"/ColorSpace/DeviceRGB/BitsPerComponent 8/Length %d>>stream\n%s\nendstream"
            % (len(jpeg), jpeg))


def write_pdf(path, pages, draw=b"q 200 0 0 300 0 0 cm /Im0 Do Q"):
    """PDF de len(pages) pages de 200×300 pt : chaque page dessine l'image
    `pages[i]` (dictionnaire et flux PDF), toutes ses ressources étant listées."""
    first_image = 4 + len(pages)
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[%s]/Count %d>>" % (
            b" ".join(b"%d 0 R" % (4 + i) for i in range(len(pages))), len(pages)),
        b"<</Length %d>>stream\n%s\nendstream" % (len(draw), draw),
    ]
    for i in range(len(pages)):
        objects.append(b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 300]/Contents 3 0 R"
                       b"/Resources<</XObject<</Im0 %d 0 R>>>>>>" % (first_image + i))
    objects.extend(pages)
    data = b"%PDF-1.5\n"
    offsets = []
    for number, content in enumerate(objects, 1):
        offsets.append(len(data))
        data += b"%d 0 obj\n%s\nendobj\n" % (number, content)
    start = len(data)
    data += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        data += b"%010d 00000 n \n" % offset
    data += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (len(objects) + 1, start)
    path.write_bytes(data)
    return str(path)


class Channel:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


@pytest.fixture
def run_engine(monkeypatch):
    """run_engine(*commandes) : messages rendus par le vrai moteur."""
    # Fichier de secours des plantages natifs : propre à un vrai process enfant
    monkeypatch.setattr(error_log, "start_child_crash_log", lambda: None)
    ns = ast_loader.load(
        "pdf_loading_qt.py",
        {"_pdf_persistent_process", "pdf_image_ext", "_send_page_error", "_send_request_error",
         "_CHANNEL_CLOSED", "_JPEG_TOO_LARGE_MARK"},
        logged_child_process=error_log.logged_child_process, page_resolution=page_resolution,
        image_damage=image_damage, original_images=original_images, raw_page_image=raw_page_image)

    def run(*commands):
        commands_queue = queue.Queue()
        for command in commands:
            commands_queue.put(command)
        commands_queue.put(("quit",))
        channel = Channel()
        ns["_pdf_persistent_process"](commands_queue, channel)
        return channel.sent
    return run


def kinds(messages):
    return [m[0] for m in messages if m[0] not in ("progress", "scan_progress")]


def test_page_with_a_damaged_image_is_reported_instead_of_being_blank(tmp_path, run_engine):
    path = write_pdf(tmp_path / "a.pdf", [good_image(), BAD_IMAGE, good_image()])
    messages = run_engine(("run", path, 0))
    assert kinds(messages) == ["total", "page", "page_error", "page", "done"]
    error = next(m for m in messages if m[0] == "page_error")
    assert error[1] == 1 and "JPX" in error[2]


def test_images_mode_keeps_the_healthy_images(tmp_path, run_engine):
    """Le relevé préalable des pages ne fait plus abandonner tout le PDF, et
    la page abîmée garde sa place dans l'ordre des messages."""
    path = write_pdf(tmp_path / "a.pdf", [good_image(), BAD_IMAGE, good_image()])
    messages = run_engine(("run_images", path, False))
    assert kinds(messages) == ["total", "page", "page_error", "page", "done"]
    assert next(m for m in messages if m[0] == "page_error")[1] == 1


def test_images_mode_reports_a_damaged_last_page(tmp_path, run_engine):
    path = write_pdf(tmp_path / "a.pdf", [good_image(), BAD_IMAGE])
    assert kinds(run_engine(("run_images", path, False))) == ["total", "page", "page_error", "done"]


def test_images_mode_with_only_damaged_images_shows_their_errors(tmp_path, run_engine):
    """« Aucune image » n'est pas vrai : le message annoncerait un PDF sans
    image, et ses erreurs seraient perdues."""
    path = write_pdf(tmp_path / "a.pdf", [BAD_IMAGE])
    assert kinds(run_engine(("run_images", path, False))) == ["total", "page_error", "done"]


def test_images_mode_on_a_pdf_without_image(tmp_path, run_engine):
    doc = fitz.open()
    doc.new_page()
    path = str(tmp_path / "a.pdf")
    doc.save(path)
    assert kinds(run_engine(("run_images", path, False))) == ["total", "no_images"]


def test_image_unreadable_while_being_extracted_is_reported_with_its_page(tmp_path, run_engine, monkeypatch):
    """Erreur levée pendant l'extraction d'une image (placement_image), après
    le relevé des pages : signalée, sans interrompre le document."""
    from modules.qt import pdf_page_images
    real = pdf_page_images.placement_image

    def failing(doc, placement, xref_png):
        if placement["page"] == 1:
            raise ValueError("Aaaa")
        return real(doc, placement, xref_png)
    monkeypatch.setattr(pdf_page_images, "placement_image", failing)
    path = write_pdf(tmp_path / "a.pdf", [good_image(), good_image(), good_image()])
    messages = run_engine(("run_images", path, False))
    assert kinds(messages) == ["total", "page", "page_error", "page", "done"]
    assert next(m for m in messages if m[0] == "page_error")[1:] == (1, "Aaaa")


def test_batch_reports_the_damaged_page(tmp_path, run_engine):
    """Le lot supprime le PDF après la conversion : une page ressortie blanche
    serait une perte définitive. Une page signalée le fait garder."""
    path = write_pdf(tmp_path / "a.pdf", [good_image(), BAD_IMAGE])
    messages = run_engine(("batch_open", path), ("batch_convert", ["0001.jpg", "0002.jpg"]))
    assert kinds(messages) == ["batch_ready", "batch_page", "page_error", "done"]


def test_damaged_image_only_listed_by_the_page_does_not_lose_the_page(tmp_path):
    """Ressources communes à tout le document : une image abîmée qu'une page
    liste sans la dessiner n'empêche pas de la rendre."""
    path = write_pdf(tmp_path / "a.pdf", [BAD_IMAGE], draw=b"0 0 m 10 10 l S")
    doc = fitz.open(path)
    page = doc[0]
    assert len(page.get_images(full=True)) == 1
    assert page_resolution(doc, page, page.get_images(full=True)) == (300, 0, None)


# Image abîmée que PyMuPDF rend quand même, noire ou coupée, sans lever d'erreur :
# seuls les avertissements de MuPDF la révèlent. Ces tests surveillent aussi leur
# texte, qu'une mise à jour de PyMuPDF pourrait changer.

NOISE = Image.effect_noise((200, 300), 60).convert("RGB")
# Un tracé en plus de l'image : la page est rendue, jamais extraite telle quelle
DRAW_AND_LINE = b"q 200 0 0 300 0 0 cm /Im0 Do Q 0 0 m 10 10 l S"


def image_object(filter_name, stream):
    return (b"<</Type/XObject/Subtype/Image/Width 200/Height 300/Filter/%s"
            b"/ColorSpace/DeviceRGB/BitsPerComponent 8/Length %d>>stream\n%s\nendstream"
            % (filter_name, len(stream), stream))


def noise_jpeg():
    buf = io.BytesIO()
    NOISE.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def noise_flate():
    return zlib.compress(NOISE.tobytes())


def flipped_middle(data):
    middle = len(data) // 2
    return data[:middle] + bytes(b ^ 0xFF for b in data[middle:middle + 500]) + data[middle + 500:]


DAMAGED = {
    "jpeg_garbage": lambda: image_object(b"DCTDecode", b"\x00\x01garbage" * 40),
    "jpeg_cut": lambda: image_object(b"DCTDecode", noise_jpeg()[:len(noise_jpeg()) // 2]),
    "flate_garbage": lambda: image_object(b"FlateDecode", b"\x00\x01garbage" * 40),
    "flate_cut": lambda: image_object(b"FlateDecode", noise_flate()[:len(noise_flate()) // 2]),
    "flate_too_short": lambda: image_object(b"FlateDecode", zlib.compress(NOISE.tobytes()[:9000])),
    "flate_middle": lambda: image_object(b"FlateDecode", flipped_middle(noise_flate())),
}


@pytest.mark.parametrize("damage", DAMAGED)
def test_rendered_page_with_a_damaged_image_is_kept_and_reported(tmp_path, run_engine, damage):
    path = write_pdf(tmp_path / "a.pdf", [DAMAGED[damage](), good_image()], draw=DRAW_AND_LINE)
    assert kinds(run_engine(("run", path, 0))) == ["total", "page_damaged", "page", "page", "done"]
    assert kinds(run_engine(("run", path, 150))) == ["total", "page_damaged", "page", "page", "done"]


def test_healthy_images_are_never_reported(tmp_path, run_engine):
    path = write_pdf(tmp_path / "a.pdf", [image_object(b"FlateDecode", noise_flate()),
                                          image_object(b"DCTDecode", noise_jpeg())], draw=DRAW_AND_LINE)
    assert kinds(run_engine(("run", path, 0))) == ["total", "page", "page", "done"]


def test_batch_reports_a_rendered_damaged_page_as_an_error(tmp_path, run_engine):
    path = write_pdf(tmp_path / "a.pdf", [DAMAGED["flate_cut"]()], draw=DRAW_AND_LINE)
    messages = run_engine(("batch_open", path), ("batch_convert", ["0001.jpg"]))
    assert kinds(messages) == ["batch_ready", "page_error", "done"]


def test_batch_reports_an_extracted_damaged_image_as_an_error(tmp_path, run_engine):
    """Image seule sur sa page, extraite telle quelle, sans rendu."""
    path = write_pdf(tmp_path / "a.pdf", [DAMAGED["flate_cut"]()])
    messages = run_engine(("batch_open", path), ("batch_convert", ["0001.jpg"]))
    assert kinds(messages) == ["batch_ready", "page_error", "done"]


def test_images_mode_marks_the_damaged_image(tmp_path, run_engine):
    path = write_pdf(tmp_path / "a.pdf", [good_image(), DAMAGED["flate_cut"]()])
    messages = run_engine(("run_images", path, False))
    assert kinds(messages) == ["total", "page", "page_damaged", "page", "done"]


def test_zlib_warning_about_another_stream_does_not_accuse_the_images(tmp_path):
    """Contrôle mal écrit dans le contenu de la page ou une police : sans
    conséquence, et fréquent."""
    warning = "ignoring zlib error: incorrect data check"
    path = write_pdf(tmp_path / "a.pdf", [image_object(b"FlateDecode", noise_flate())])
    doc = fitz.open(path)
    assert image_damage(doc, doc[0], warning) == ""
    path = write_pdf(tmp_path / "b.pdf", [DAMAGED["flate_middle"]()])
    doc = fitz.open(path)
    assert image_damage(doc, doc[0], warning) == warning


def test_unrelated_warnings_are_ignored(tmp_path):
    path = write_pdf(tmp_path / "a.pdf", [good_image()])
    doc = fitz.open(path)
    assert image_damage(doc, doc[0], "premature end of data in flate filter\nunknown font") == ""


WORKERS = {
    "PdfLoadWorker": ("page", 0),
    "PdfMergeWorker": ("merge_page", "page_0001.jpg"),
}


@pytest.mark.parametrize("worker_name", WORKERS)
def test_worker_marks_only_the_damaged_page(worker_name, monkeypatch):
    """Le cadre orange et la question avant l'enregistrement suivent la page
    annoncée, jamais la suivante."""
    kind, first_key = WORKERS[worker_name]
    buf = io.BytesIO()
    NOISE.save(buf, "JPEG")
    jpeg = buf.getvalue()
    second_key = 1 if kind == "page" else "page_0002.jpg"
    messages = [("total", 2, False), ("page_damaged", 0, "Aaaa"), (kind, first_key, jpeg, 72),
                (kind, second_key, jpeg, 72), ("done",)]
    monkeypatch.setitem(sys.modules, "modules.qt.mosaic_canvas",
                        types.SimpleNamespace(build_qimage_for_entry=lambda entry: None))
    methods = ast_loader.load_methods(
        "pdf_loading_qt.py", worker_name, {"run"},
        create_entry=create_entry, mark_altered=mark_altered, IMAGE_EXTS=IMAGE_EXTS,
        pdf_image_ext=ast_loader.load("pdf_loading_qt.py", {"pdf_image_ext"})["pdf_image_ext"],
        read_pdf_message=lambda _conn, _process, _timeout: messages.pop(0),
        PDF_STOPPED_MESSAGE=("process_stopped",), PDF_PROCESS_STOPPED="",
        _spawn_pdf_process=lambda: (None, types.SimpleNamespace(put=lambda _m: None), None))

    class Worker(methods):
        def __init__(self):
            self._cancelled = threading.Event()
            self._in_q = types.SimpleNamespace(put=lambda _m: None)
            self._out_conn = self._process = None
            self._dpi, self._filepath, self._merge_prefix = 0, "Aaaa.pdf", ""
            self._images_only = self._join_cut_images = False
            self.page_errors = []
            self.pages_read = []
            self.progress, self.finished = Signal(), Signal()

        def _kill_own_process(self):
            pass

    worker = Worker()
    worker.run()
    [(entries, _owner)] = worker.finished.emitted
    assert [is_altered(entry) for entry in entries] == [True, False]


def test_metadata_import_refuses_a_damaged_page():
    """Il supprime le PDF après l'écriture du CBZ, comme le lot."""
    source = (ast_loader.QT_DIR / "batch_metadata_dialog_qt.py").read_text(encoding="utf-8")
    assert "elif kind in ('page_error', 'page_damaged'):" in source


# Page trop grande pour le JPEG

def text_of_page_error(message):
    ns = ast_loader.load("pdf_loading_qt.py", {"pdf_page_error_text", "_JPEG_TOO_LARGE_MARK"}, _=tr)
    return ns["pdf_page_error_text"](message)


def test_page_too_large_for_jpeg_adds_the_limit_to_the_error(tmp_path, run_engine, capfd):
    """L'ancien message est gardé, la limite s'y ajoute, et rien n'est écrit
    dans la console par libjpeg. Page de 120000 pixels de large à 600 dpi."""
    doc = fitz.open()
    doc.new_page(width=14400, height=3)
    path = str(tmp_path / "a.pdf")
    doc.save(path)
    messages = run_engine(("run", path, 600))
    assert kinds(messages) == ["total", "page_error", "done"]
    text = text_of_page_error(next(m for m in messages if m[0] == "page_error"))
    assert "broken data stream when writing image file" in text
    assert "jpeg_too_large" in text and "65500" in text
    assert "\x00" not in text
    assert "Maximum supported" not in capfd.readouterr().err


def test_page_under_the_jpeg_limit_is_written(tmp_path, run_engine):
    """60000 pixels de large à 300 dpi : sous la limite, la page s'écrit."""
    doc = fitz.open()
    doc.new_page(width=14400, height=3)
    path = str(tmp_path / "a.pdf")
    doc.save(path)
    assert kinds(run_engine(("run", path, 300))) == ["total", "page", "done"]


def test_other_page_errors_are_shown_as_they_are():
    assert text_of_page_error(("page_error", 2, "Aaaa")) == tr(
        "messages.errors.pdf_failure.page_error", page=3, error="Aaaa")


def test_request_error_without_text_gives_its_type():
    sent = Channel()
    ns = ast_loader.load("pdf_loading_qt.py", {"_send_request_error"})
    ns["_send_request_error"](sent, MemoryError())
    assert sent.sent == [("error", "MemoryError")]
