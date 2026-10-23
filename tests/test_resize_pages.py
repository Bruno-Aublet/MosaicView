"""Redimensionnement des pages (resize_dialog_qt) : le vrai travail du worker
(_ResizeWorker._run) et le repérage des pages doubles
(cluster_and_find_reference).

Vérifié sur les pages produites, relues :
- par pourcentage : dimensions exactes, format et mode gardés ;
- largeur imposée : proportions gardées ; page double (multiplicateur 2) :
  deux fois la largeur cible ; choix « ne pas toucher » d'une page aberrante
  respecté ;
- GIF animé : toutes ses images redimensionnées ;
- page sans données ou illisible : signalée, les autres traitées ;
- annulation avant le début : aucune page touchée ;
- regroupement des largeurs : simple, double, page aberrante à part.

resize_dialog_qt.py importe PySide6 : le worker est extrait par AST
(tests/ast_loader.load_methods), ses signaux sont remplacés.
"""
import io
import threading
from collections import Counter

import pytest
from PIL import Image

from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import create_entry, free_image_memory, open_page_image, save_image_to_bytes
from modules.qt.image_ops import has_several_frames, transform_animated_entry
from modules.qt.state import AppState

import ast_loader
from fakes import Signal as _Signal

cluster_and_find_reference = ast_loader.load(
    "resize_dialog_qt.py", {"cluster_and_find_reference"}, Counter=Counter)["cluster_and_find_reference"]

Base = ast_loader.load_methods(
    "resize_dialog_qt.py", "_ResizeWorker", {"_run"},
    open_page_image=open_page_image, has_several_frames=has_several_frames,
    transform_animated_entry=transform_animated_entry, save_image_to_bytes=save_image_to_bytes,
    build_qimage_for_entry=lambda entry: None, free_image_memory=free_image_memory)


class Worker(Base):
    def __init__(self, entries, scale_factor=1.0, target_width=None, target_height=None,
                 custom=False, width_mapping=None, outlier_choices=None, multi_page=False):
        self._selected_entries, self._state = entries, AppState()
        self._use_custom_dim, self._scale_factor = custom, scale_factor
        self._target_width, self._target_height = target_width, target_height
        self._multi_page, self._nb_files = multi_page, len(entries)
        self._has_diff_widths, self._has_diff_heights = bool(width_mapping), False
        self._reference_width, self._reference_height = target_width, None
        self._width_mapping, self._height_mapping = width_mapping or {}, {}
        self._outlier_choices = outlier_choices or {}
        self._cancelled = threading.Event()
        self.resized, self.failures = [], []
        self.progress, self.finished, self.cancelled = _Signal(), _Signal(), _Signal()


def page(name, size, fmt="PNG", mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, size, "red").save(buf, fmt)
    return create_entry(name, buf.getvalue(), IMAGE_EXTS)


def opened(entry):
    return Image.open(io.BytesIO(entry["bytes"]))


def test_percentage_resize_keeps_format_and_mode():
    pages = [page("p01.png", (200, 100)), page("p02.jpg", (80, 40), fmt="JPEG", mode="L")]
    worker = Worker(pages, scale_factor=0.5)
    worker._run()
    assert worker.resized == pages and worker.failures == []
    first, second = opened(pages[0]), opened(pages[1])
    assert (first.format, first.mode, first.size) == ("PNG", "RGB", (100, 50))
    assert (second.format, second.mode, second.size) == ("JPEG", "L", (40, 20))
    assert worker.finished.emitted == [()]


def test_imposed_width_keeps_proportions_and_doubles_a_double_page():
    single, double = page("p01.png", (1000, 1500)), page("p02.png", (2000, 1500))
    worker = Worker([single, double], target_width=800, custom=True, multi_page=True,
                    width_mapping={1000: 1, 2000: 2})
    worker._run()
    assert opened(single).size == (800, 1200)
    assert opened(double).size == (1600, 1200)            # page double : deux pages de large


def test_outlier_left_untouched_on_request():
    normal, odd = page("p01.png", (1000, 1500)), page("p02.png", (300, 1500))
    before = odd["bytes"]
    worker = Worker([normal, odd], target_width=800, custom=True, multi_page=True,
                    width_mapping={1000: 1}, outlier_choices={"p02.png": {"width_mult": 0}})
    worker._run()
    assert odd["bytes"] is before and odd not in worker.resized
    assert opened(normal).size == (800, 1200)


def test_animated_gif_keeps_every_frame():
    frames = [Image.new("RGB", (40, 20), color) for color in ((255, 0, 0), (0, 0, 255))]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    gif = create_entry("p01.gif", buf.getvalue(), IMAGE_EXTS)
    Worker([gif], scale_factor=0.5)._run()
    out = opened(gif)
    assert out.n_frames == 2 and out.size == (20, 10)


def test_pages_without_data_or_unreadable_are_reported_and_others_resized():
    good = page("p01.png", (100, 100))
    empty = create_entry("p02.png", None, IMAGE_EXTS)
    broken = create_entry("p03.png", b"illisible", IMAGE_EXTS)
    worker = Worker([empty, broken, good], scale_factor=0.5)
    worker._run()
    assert worker.failures == ["p02.png", "p03.png"]
    assert worker.resized == [good] and opened(good).size == (50, 50)


def test_cancel_before_the_start_touches_nothing():
    first = page("p01.png", (100, 100))
    before = first["bytes"]
    worker = Worker([first], scale_factor=0.5)
    worker._cancelled.set()
    worker._run()
    assert worker.cancelled.emitted == [()] and first["bytes"] is before


def test_widths_are_grouped_into_single_double_and_outlier_pages():
    reference, multipliers, outliers = cluster_and_find_reference(
        [1000, 1010, 995, 2000, 2010, 400])
    assert reference == pytest.approx(1001.7, abs=1)
    assert multipliers[1000] == multipliers[995] == 1
    assert multipliers[2000] == multipliers[2010] == 2
    assert outliers == {400} and 400 not in multipliers


def test_no_dimension_gives_no_reference():
    assert cluster_and_find_reference([]) == (0, {}, set())
