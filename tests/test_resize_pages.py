"""Redimensionnement des pages (resize_dialog_qt) : le vrai travail du worker
(_ResizeWorker._run) et le repérage des pages doubles
(cluster_and_find_reference).

Vérifié sur les pages produites, relues :
- par pourcentage : dimensions exactes, format et mode gardés ;
- largeur imposée : proportions gardées ; page double (multiplicateur 2) :
  deux fois la largeur cible ; choix « ne pas toucher » d'une page aberrante
  respecté ; largeur et hauteur dissociées : la dimension non saisie garde la
  valeur de chaque page ;
- fenêtre des dimensions aberrantes : une réponse par page, même pour des
  pages de même nom, question sur la seule dimension aberrante et saisie ;
- GIF animé : toutes ses images redimensionnées ;
- pages 1 bit et à palette : mode gardé, traits fins conservés en réduction ;
- page sans données ou illisible : signalée, les autres traitées ;
- annulation avant le début : aucune page touchée ;
- regroupement des largeurs : simple, double, page aberrante à part ;
- validation de la fenêtre (_on_ok) : une page dont la fenêtre ignore le
  changement de taille n'est pas déformée ;
- fenêtre ouverte : elle suit les pages sélectionnées quand elles changent,
  sortent du document ou y reviennent par Annuler (_refresh_from_pages,
  _current_selection, AppState.add_document_listener).

resize_dialog_qt.py importe PySide6 : le worker est extrait par AST
(tests/ast_loader.load_methods), ses signaux sont remplacés.
"""
import ast
import io
import threading
import types
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.entries import (create_entry, free_image_memory, open_page_image, page_size,
                                save_image_to_bytes)
from modules.qt.image_ops import has_several_frames, resize_keeping_mode, transform_animated_entry
from modules.qt.state import AppState
from modules.qt.undo_redo import adopt_entry_key, entry_key, save_state_data

import ast_loader
from fakes import Signal as _Signal, tr

cluster_and_find_reference = ast_loader.load(
    "resize_dialog_qt.py", {"cluster_and_find_reference"}, Counter=Counter)["cluster_and_find_reference"]

Base = ast_loader.load_methods(
    "resize_dialog_qt.py", "_ResizeWorker", {"_run"},
    open_page_image=open_page_image, has_several_frames=has_several_frames,
    resize_keeping_mode=resize_keeping_mode, entry_key=entry_key,
    transform_animated_entry=transform_animated_entry, save_image_to_bytes=save_image_to_bytes,
    build_qimage_for_entry=lambda entry: None, free_image_memory=free_image_memory)


class Worker(Base):
    def __init__(self, entries, scale_factor=1.0, target_width=None, target_height=None,
                 custom=False, width_mapping=None, outlier_choices=None, multi_page=False,
                 free_ratio=False):
        self._selected_entries, self._state = entries, AppState()
        self._use_custom_dim, self._scale_factor = custom, scale_factor
        self._target_width, self._target_height = target_width, target_height
        self._multi_page, self._nb_files = multi_page, len(entries)
        self._has_diff_widths, self._has_diff_heights = bool(width_mapping), False
        self._reference_width, self._reference_height = target_width, None
        self._width_mapping, self._height_mapping = width_mapping or {}, {}
        self._outlier_choices = outlier_choices or {}
        self._free_ratio = free_ratio
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


def test_free_ratio_keeps_each_page_original_other_dimension():
    # Case « Dissocier largeur et hauteur » : une dimension non saisie garde la
    # valeur de chaque page, les proportions ne sont plus suivies.
    tall, short = page("p01.png", (1006, 1527)), page("p02.png", (1000, 1489))
    Worker([tall, short], target_width=100, custom=True, free_ratio=True)._run()
    assert opened(tall).size == (100, 1527)
    assert opened(short).size == (100, 1489)
    other = page("p03.png", (1000, 1500))
    Worker([other], target_height=300, custom=True, free_ratio=True)._run()
    assert opened(other).size == (1000, 300)


def test_outlier_left_untouched_on_request():
    normal, odd = page("p01.png", (1000, 1500)), page("p02.png", (300, 1500))
    before = odd["bytes"]
    worker = Worker([normal, odd], target_width=800, custom=True, multi_page=True,
                    width_mapping={1000: 1}, outlier_choices={entry_key(odd): {"width_mult": 0}})
    worker._run()
    assert odd["bytes"] is before and odd not in worker.resized
    assert opened(normal).size == (800, 1200)


def test_outlier_answer_is_not_shared_with_a_normal_page_of_the_same_name():
    odd, normal = page("same.png", (300, 1500)), page("same.png", (1000, 1500))
    before = odd["bytes"]
    worker = Worker([odd, normal], target_width=500, custom=True, multi_page=True,
                    width_mapping={1000: 1}, outlier_choices={entry_key(odd): {"width_mult": 0}})
    worker._run()
    assert odd["bytes"] is before                          # « ne pas redimensionner »
    assert opened(normal).size == (500, 750)               # page normale, redimensionnée


def test_outliers_of_the_same_name_each_keep_their_own_answer():
    first, second = page("same.png", (300, 1500)), page("same.png", (200, 1500))
    worker = Worker([first, second], target_width=500, custom=True, multi_page=True,
                    width_mapping={1000: 1},
                    outlier_choices={entry_key(first): {"width_mult": 1},
                                     entry_key(second): {"width_mult": 2}})
    worker._run()
    assert opened(first).width == 500 and opened(second).width == 1000


def test_animated_gif_keeps_every_frame():
    frames = [Image.new("RGB", (40, 20), color) for color in ((255, 0, 0), (0, 0, 255))]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    gif = create_entry("p01.gif", buf.getvalue(), IMAGE_EXTS)
    Worker([gif], scale_factor=0.5)._run()
    out = opened(gif)
    assert out.n_frames == 2 and out.size == (20, 10)


def encoded(img, name, **save_options):
    buf = io.BytesIO()
    img.save(buf, **save_options)
    return create_entry(name, buf.getvalue(), IMAGE_EXTS)


def one_bit_lines(size=(200, 200), gap=4):
    """Page 1 bit : traits noirs d'un pixel sur les lignes paires, que le plus
    proche voisin efface tous lors d'une réduction de moitié."""
    img = Image.new("1", size, 1)
    for y in range(0, size[1], gap):
        for x in range(size[0]):
            img.putpixel((x, y), 0)
    return img


def palette_lines(size=(200, 200), gap=4):
    """Même dessin à palette : traits rouges sur blanc."""
    img = Image.new("P", size, 0)
    img.putpalette([255, 255, 255, 200, 30, 30] + [0] * (254 * 3))
    for y in range(0, size[1], gap):
        for x in range(size[0]):
            img.putpixel((x, y), 1)
    return img


def palette_with_marked_corner():
    """Page à palette 64×64, carré du haut à gauche à l'indice 2."""
    img = Image.new("P", (64, 64), 1)
    img.putpalette([0, 0, 0, 200, 30, 30, 0, 0, 255] + [0] * (253 * 3))
    img.paste(2, (0, 0, 32, 32))
    return img


def test_reduced_one_bit_page_stays_one_bit_and_keeps_its_thin_lines():
    entry = encoded(one_bit_lines(), "p01.png", format="PNG")
    Worker([entry], scale_factor=0.5)._run()
    out = opened(entry)
    assert (out.format, out.mode, out.size) == ("PNG", "1", (100, 100))
    # Chaque tranche de 2 lignes (4 lignes d'origine) garde de l'encre
    rows = [[out.getpixel((x, y)) for x in range(100)] for y in range(100)]
    for y in range(0, 98, 2):
        assert 0 in rows[y] or 0 in rows[y + 1]


def test_enlarged_one_bit_page_has_clean_edges():
    img = Image.new("1", (40, 40), 1)
    img.paste(0, (0, 0, 20, 40))
    out = resize_keeping_mode(img, (80, 80))
    assert out.mode == "1"
    # Un seul passage du noir au blanc sur chaque ligne : aucun point de trame
    for y in range(80):
        row = [out.getpixel((x, y)) for x in range(80)]
        assert sum(a != b for a, b in zip(row, row[1:])) == 1


def test_one_bit_page_keeps_its_group4_compression_and_dpi():
    entry = encoded(one_bit_lines(), "p01.tif", format="TIFF", compression="group4", dpi=(300, 300))
    Worker([entry], scale_factor=0.5)._run()
    out = opened(entry)
    assert (out.mode, out.size) == ("1", (100, 100))
    assert out.info["compression"] == "group4"
    assert tuple(round(v) for v in out.info["dpi"]) == (300, 300)


def test_reduced_palette_page_stays_palette_and_keeps_its_thin_lines():
    entry = encoded(palette_lines(), "p01.png", format="PNG")
    Worker([entry], scale_factor=0.5)._run()
    out = opened(entry)
    assert (out.format, out.mode, out.size) == ("PNG", "P", (100, 100))
    rgb = out.convert("RGB")
    # Chaque trait reste présent, mêlé au blanc
    for y in range(2, 96, 2):
        assert rgb.getpixel((50, y)) != (255, 255, 255) or rgb.getpixel((50, y + 1)) != (255, 255, 255)


def test_palette_page_keeps_exact_colors_when_they_fit():
    img = Image.new("P", (40, 40), 0)
    img.putpalette([17, 34, 51, 200, 30, 30] + [0] * (254 * 3))
    img.paste(1, (20, 0, 40, 40))
    out = resize_keeping_mode(img, (20, 20)).convert("RGB")
    assert out.getpixel((2, 10)) == (17, 34, 51) and out.getpixel((17, 10)) == (200, 30, 30)


def test_palette_page_with_too_many_mixed_colors_stays_palette_and_close():
    gradient = Image.new("RGB", (128, 128))
    for y in range(128):
        for x in range(128):
            gradient.putpixel((x, y), (x * 2, y * 2, (x + y)))
    source = gradient.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    out = resize_keeping_mode(source, (64, 64))
    assert out.mode == "P"
    reference = source.convert("RGB").resize((64, 64), Image.Resampling.LANCZOS)
    difference = [abs(a - b) for a, b in zip(reference.tobytes(), out.convert("RGB").tobytes())]
    assert sum(difference) / len(difference) < 5


@pytest.mark.parametrize("name, fmt", [("p01.png", "PNG"), ("p01.gif", "GIF")])
def test_palette_page_keeps_its_clear_transparency(name, fmt):
    entry = encoded(palette_with_marked_corner(), name, format=fmt, transparency=2)
    Worker([entry], scale_factor=0.5)._run()
    out = opened(entry)
    assert (out.format, out.mode, out.size) == (fmt, "P", (32, 32))
    rgba = out.convert("RGBA")
    assert rgba.getpixel((4, 4))[3] == 0                   # coin transparent
    assert rgba.getpixel((28, 28))[3] == 255               # reste de la page opaque


def test_palette_page_transparent_stays_transparent_with_many_colors():
    """Trop de couleurs pour la palette exacte : un indice reste réservé à la
    transparence."""
    img = Image.new("P", (64, 64), 0)
    palette = []
    for i in range(255):
        palette += [i, 255 - i, (i * 7) % 256]
    img.putpalette(palette + [0, 0, 0])
    for y in range(64):
        for x in range(64):
            img.putpixel((x, y), (x * 3 + y * 5) % 255)
    img.paste(255, (0, 0, 32, 32))
    img.info["transparency"] = 255
    out = resize_keeping_mode(img, (32, 32))
    assert out.mode == "P" and out.info["transparency"] == 255
    rgba = out.convert("RGBA")
    assert rgba.getpixel((4, 4))[3] == 0 and rgba.getpixel((28, 28))[3] == 255


def test_palette_png_keeps_a_partial_transparency():
    img = Image.new("P", (40, 40), 0)
    img.putpalette([200, 30, 30, 0, 0, 255] + [0] * (254 * 3))
    img.paste(1, (20, 0, 40, 40))
    entry = encoded(img, "p01.png", format="PNG", transparency=bytes([128, 255]))
    Worker([entry], scale_factor=0.5)._run()
    out = opened(entry)
    assert out.mode == "P"
    rgba = out.convert("RGBA")
    assert 100 < rgba.getpixel((2, 10))[3] < 160           # moitié transparente gardée
    assert rgba.getpixel((17, 10))[3] >= 250               # palette recalculée : à peine arrondie


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


@pytest.mark.parametrize("width", [700, 1300, 1490, 1510, 1700, 2300])
def test_width_close_to_neither_a_single_nor_a_double_page_is_asked_about(width):
    _reference, multipliers, outliers = cluster_and_find_reference([1000] * 8 + [width])
    assert outliers == {width} and width not in multipliers


@pytest.mark.parametrize("width, multiplier", [(800, 1), (1200, 1), (1800, 2), (2000, 2), (2200, 2)])
def test_width_close_to_a_single_or_double_page_gets_its_multiplier(width, multiplier):
    _reference, multipliers, outliers = cluster_and_find_reference([1000] * 8 + [width])
    assert outliers == set() and multipliers[width] == multiplier


def test_regular_comic_asks_nothing():
    _reference, _multipliers, outliers = cluster_and_find_reference(
        [1000, 1010, 995, 1005, 2000, 2010, 1990, 1000])
    assert outliers == set()


def test_no_dimension_gives_no_reference():
    assert cluster_and_find_reference([]) == (0, {}, set())


# --- Fenêtre des dimensions aberrantes : à qui et sur quoi elle pose la question

pages_to_ask = ast_loader.load("resize_dialog_qt.py", {"_outlier_pages_to_ask"},
                               page_size=page_size)["_outlier_pages_to_ask"]


def asked(entries, target_width, target_height):
    return [(p["entry"], p["has_width_outlier"], p["has_height_outlier"])
            for p in pages_to_ask(entries, {300}, {4500}, target_width, target_height)]


def test_only_an_aberrant_and_entered_dimension_is_asked_about():
    narrow, tall = page("p01.png", (300, 1500)), page("p02.png", (1000, 4500))
    normal = page("p03.png", (1000, 1500))
    pages = [narrow, tall, normal]
    # Largeur seule : la hauteur aberrante suit les proportions, rien à demander
    assert asked(pages, 500, None) == [(narrow, True, False)]
    # Hauteur seule : l'inverse
    assert asked(pages, None, 800) == [(tall, False, True)]
    # Les deux : chaque page, sur sa seule dimension aberrante
    assert asked(pages, 500, 800) == [(narrow, True, False), (tall, False, True)]


def test_pages_without_readable_dimensions_are_not_asked_about():
    empty = page("p01.png", (300, 1500))
    empty["bytes"] = None
    broken = page("p02.png", (300, 1500))
    broken["bytes"] = b"pas une image"
    assert asked([empty, broken], 500, None) == []


class _FakeQt:
    """Widgets remplacés : chaque appel rend un objet distinct."""

    def __call__(self, *args, **kwargs):
        return MagicMock()

    def __getattr__(self, name):
        return MagicMock()


def outlier_dialog_groups(pages, target_width, target_height):
    """_choice_groups de la fenêtre construite sur ces pages."""
    built = ast_loader.load_methods(
        "resize_dialog_qt.py", "OutlierDialog", {"_build_page_widgets"},
        QFrame=_FakeQt(), QHBoxLayout=_FakeQt(), QVBoxLayout=_FakeQt(), QWidget=_FakeQt(),
        QLabel=_FakeQt(), QRadioButton=_FakeQt(), QButtonGroup=_FakeQt(),
        QPixmap=_FakeQt(), QImage=_FakeQt(), Qt=_FakeQt(), _=tr, _get_current_font=MagicMock(),
        page_size=page_size, open_page_image=open_page_image, entry_key=entry_key, io=io)()
    built._outlier_pages = pages
    built._target_width, built._target_height = target_width, target_height
    built._reference_width, built._reference_height = 1000, 1500
    built._choice_groups, built._scroll_layout = {}, MagicMock()
    built._check_all_selected = lambda: None
    built._build_page_widgets()
    return built._choice_groups


def test_outlier_dialog_has_one_answer_per_page_of_the_same_name():
    first, second = page("same.png", (300, 1500)), page("same.png", (200, 1500))
    groups = outlier_dialog_groups(
        [{"entry": first, "has_width_outlier": True, "has_height_outlier": False},
         {"entry": second, "has_width_outlier": True, "has_height_outlier": False}], 500, None)
    assert set(groups) == {entry_key(first), entry_key(second)}
    assert groups[entry_key(first)]["width"] is not groups[entry_key(second)]["width"]


def test_outlier_dialog_only_offers_the_aberrant_dimension():
    narrow, tall = page("p01.png", (300, 1500)), page("p02.png", (1000, 4500))
    groups = outlier_dialog_groups(
        [{"entry": narrow, "has_width_outlier": True, "has_height_outlier": False},
         {"entry": tall, "has_width_outlier": False, "has_height_outlier": True}], 500, 800)
    assert set(groups[entry_key(narrow)]) == {"width"}
    assert set(groups[entry_key(tall)]) == {"height"}


# --- Validation de la fenêtre : dimensions relues au clic sur OK ---------------

helpers = ast_loader.load(
    "resize_dialog_qt.py", {"_page_current_size", "_selection_summary", "_pages_changed_since"},
    page_size=page_size)

Dialog = ast_loader.load_methods(
    "resize_dialog_qt.py", "ResizeDialog",
    {"_on_ok", "_refresh_from_pages", "_current_selection", "_update_pct_estimates",
     "_on_pct_selected", "_update_diff_dim_visibility", "_update_ok_state"},
    entry_key=entry_key, _=tr, _state_module=types.SimpleNamespace(state=None), MsgDialog=None, OutlierDialog=None,
    _start_resize_worker=lambda *args, **options: STARTED.append(args),
    _outlier_pages_to_ask=pages_to_ask, cluster_and_find_reference=cluster_and_find_reference, page_size=page_size,
    _pages_changed_since=helpers["_pages_changed_since"],
    _selection_summary=helpers["_selection_summary"])

STARTED = []


def in_document(dlg, entries):
    """Fenêtre ouverte sur ces pages, seules pages de son document."""
    dlg._state = AppState()
    dlg._state.images_data = list(entries)
    dlg._callbacks = {"state": dlg._state}
    dlg._page_keys = [entry_key(e) for e in entries]
    dlg._selected_entries, dlg._nb_files = list(entries), len(entries)
    return dlg


def validate(entries, cur, width, height, free_ratio=False):
    """Clique sur OK dans une fenêtre ouverte sur des pages de taille `cur`.
    Rend la largeur et la hauteur transmises au worker."""
    STARTED.clear()
    dlg = in_document(Dialog(), entries)
    dlg._same_dim, (dlg._cur_w, dlg._cur_h) = True, cur
    dlg._width_edit = types.SimpleNamespace(text=lambda: str(width))
    dlg._height_edit = types.SimpleNamespace(text=lambda: str(height))
    dlg._free_ratio_cb = types.SimpleNamespace(isChecked=lambda: free_ratio)
    dlg._multi_page_cb = None
    dlg.close = lambda: None
    dlg._on_ok()
    assert len(STARTED) == 1
    return STARTED[0][4], STARTED[0][5]


def test_page_with_pending_viewer_work_is_not_resized(monkeypatch):
    from modules.qt import viewer_busy
    monkeypatch.setattr(viewer_busy, "pending_page_work_refused", lambda *args: True)
    STARTED.clear()
    dlg = in_document(Dialog(), [page("p01.png", (60, 40))])
    dlg._on_ok()
    assert STARTED == []


def test_page_changed_without_notice_is_not_distorted_at_validation():
    rotated = page("p01.png", (60, 40))                   # 40 × 60 pour la fenêtre
    target_width, target_height = validate([rotated], (40, 60), 20, 30)
    assert (target_width, target_height) == (20, None)
    worker = Worker([rotated], target_width=target_width, target_height=target_height, custom=True)
    worker._run()
    assert opened(rotated).size == (20, 13)               # 60 × 40 : proportions gardées


def test_unchanged_pages_get_both_dimensions():
    pages = [page("p01.png", (40, 60)), page("p02.png", (40, 60))]
    assert validate(pages, (40, 60), 20, 30) == (20, 30)


def test_free_ratio_keeps_both_dimensions_even_if_a_page_changed():
    assert validate([page("p01.png", (60, 40))], (40, 60), 20, 30, free_ratio=True) == (20, 30)


def test_unreadable_page_is_not_seen_as_changed():
    broken = {"orig_name": "p01.png", "bytes": b"xx"}
    empty = {"orig_name": "p02.png", "bytes": None}
    assert helpers["_pages_changed_since"]([broken, empty], 40, 60) is False


# --- Mise à jour de la fenêtre quand une page sélectionnée change --------------

class Field:
    def __init__(self, value=""):
        self.value = value

    def text(self):
        return self.value

    def setText(self, value):
        self.value = value

    def clear(self):
        self.value = ""


class Radio:
    def __init__(self, group, pct_type, pct_value=0):
        self.group = group
        self.props = {"pct_type": pct_type, "pct_value": pct_value}

    def property(self, name):
        return self.props[name]

    def setProperty(self, name, value):
        self.props[name] = value

    def setChecked(self, checked):
        self.group.checked = self if checked else None


def open_window(entries, width="", height="", percent=None):
    """Fenêtre ouverte sur ces pages, champs et pourcentage comme l'utilisateur
    les a laissés. Tailles et poids de la fenêtre : ceux des pages à cet instant."""
    dlg = in_document(Dialog(), entries)
    dlg._multi_page_cb = None
    dlg._total_size_mb, dlg._same_dim, dlg._cur_w, dlg._cur_h = helpers["_selection_summary"](entries)
    dlg._aspect = dlg._cur_w / dlg._cur_h if dlg._same_dim else 1.0
    dlg._width_edit, dlg._height_edit = Field(width), Field(height)
    dlg._pct_group = types.SimpleNamespace(checked=None)
    dlg._pct_group.checkedButton = lambda: dlg._pct_group.checked
    dlg._rb_zero = Radio(dlg._pct_group, "zero")
    half = Radio(dlg._pct_group, "reduce", 50)
    dlg._reduc_rbs, dlg._enlarg_rbs = [half], [Radio(dlg._pct_group, "enlarge", 100)]
    if percent:
        half.setChecked(True)
    labels = {}
    dlg._custom_estimated_lbl = types.SimpleNamespace(
        setText=lambda text: labels.__setitem__("estimate", text),
        setVisible=lambda shown: labels.__setitem__("estimate_shown", shown))
    dlg._diff_dim_lbl = types.SimpleNamespace(
        setVisible=lambda shown: labels.__setitem__("warning_shown", shown))
    dlg._free_ratio_cb = types.SimpleNamespace(isChecked=lambda: False)
    dlg._multi_page_state = True
    dlg._updating = False
    dlg.retranslated = 0

    def _retranslate():
        dlg.retranslated += 1
    dlg._retranslate = _retranslate
    dlg._update_ok_state = lambda: None
    dlg._update_custom_estimate = lambda: labels.__setitem__("estimate", "saisie")
    dlg.labels = labels
    return dlg


def replace_page(entry, size):
    entry["bytes"] = page(entry["orig_name"], size)["bytes"]


def test_window_follows_a_rotated_page_and_replaces_the_typed_values():
    entry = page("p01.png", (40, 60))
    dlg = open_window([entry], "20", "30")
    replace_page(entry, (60, 40))
    dlg._refresh_from_pages()
    assert (dlg._cur_w, dlg._cur_h, dlg._aspect) == (60, 40, 1.5)
    assert (dlg._width_edit.value, dlg._height_edit.value) == ("60", "40")
    assert dlg._pct_group.checked is dlg._rb_zero
    assert dlg.labels["estimate_shown"] is True and dlg.retranslated == 1


def test_window_changes_nothing_when_the_pages_did_not_change():
    entry = page("p01.png", (40, 60))
    dlg = open_window([entry], "20", "30")
    dlg._refresh_from_pages()
    assert (dlg._width_edit.value, dlg._height_edit.value) == ("20", "30")
    assert dlg._pct_group.checked is None and dlg.retranslated == 0


def test_window_keeps_typed_values_when_only_the_weight_changed():
    entry = page("p01.png", (40, 60))
    dlg = open_window([entry], "20", "30")
    before = dlg._total_size_mb
    buf = io.BytesIO()
    Image.effect_noise((40, 60), 50).save(buf, "PNG")     # même taille, autre poids
    entry["bytes"] = buf.getvalue()
    dlg._refresh_from_pages()
    assert dlg._total_size_mb != before
    assert (dlg._width_edit.value, dlg._height_edit.value) == ("20", "30")
    assert dlg.labels["estimate"] == "saisie" and dlg.retranslated == 1


def test_checked_percentage_stays_and_its_estimates_follow_the_weight():
    entry = page("p01.png", (40, 60))
    dlg = open_window([entry], percent=True)
    half = dlg._reduc_rbs[0]
    replace_page(entry, (400, 600))
    dlg._refresh_from_pages()
    assert dlg._pct_group.checked is half
    assert (dlg._width_edit.value, dlg._height_edit.value) == ("", "")
    assert half.property("pct_est") == pytest.approx(dlg._total_size_mb * 0.25)
    assert dlg._enlarg_rbs[0].property("pct_est") == pytest.approx(dlg._total_size_mb * 4)


def test_pages_becoming_different_empty_the_fields_and_show_the_warning():
    first, second = page("p01.png", (40, 60)), page("p02.png", (40, 60))
    dlg = open_window([first, second], "20", "30")
    dlg._multi_page_state = False
    replace_page(second, (60, 40))
    dlg._refresh_from_pages()
    assert dlg._same_dim is False and dlg._aspect == 1.0
    assert (dlg._width_edit.value, dlg._height_edit.value) == ("", "")
    assert dlg.labels["estimate_shown"] is False and dlg.labels["warning_shown"] is True


def test_pages_becoming_identical_fill_the_fields_and_hide_the_warning():
    first, second = page("p01.png", (40, 60)), page("p02.png", (60, 40))
    dlg = open_window([first, second])
    replace_page(second, (40, 60))
    dlg._refresh_from_pages()
    assert dlg._same_dim is True
    assert (dlg._width_edit.value, dlg._height_edit.value) == ("40", "60")
    assert dlg.labels["estimate_shown"] is True and dlg.labels["warning_shown"] is False


# --- Pages sorties du document ou rétablies pendant que la fenêtre est ouverte --

def restored_copy(entry):
    """Page rétablie par Annuler : un nouvel objet, avec le jeton de l'ancienne
    (undo_redo_qt.restore_state_qt)."""
    copy = dict(entry)
    adopt_entry_key(copy, entry_key(entry))
    return copy


def test_deleted_page_leaves_the_window_counts():
    kept, deleted = page("p01.png", (40, 60)), page("p02.png", (60, 40))
    dlg = open_window([kept, deleted])
    cb_visible = []
    dlg._multi_page_cb = types.SimpleNamespace(setVisible=cb_visible.append)
    dlg._state.images_data = [kept]
    dlg._refresh_from_pages()
    assert dlg._selected_entries == [kept] and dlg._nb_files == 1
    assert (dlg._same_dim, dlg._cur_w, dlg._cur_h) == (True, 40, 60)
    assert dlg._total_size_mb == pytest.approx(len(kept["bytes"]) / (1024 * 1024))
    assert (dlg._width_edit.value, dlg._height_edit.value) == ("40", "60")
    assert cb_visible == [False] and dlg.retranslated == 1


def test_typed_values_stay_when_a_page_of_the_same_size_is_deleted():
    kept, deleted = page("p01.png", (40, 60)), page("p02.png", (40, 60))
    dlg = open_window([kept, deleted], "20", "30")
    dlg._state.images_data = [kept]
    dlg._refresh_from_pages()
    assert dlg._nb_files == 1 and dlg.retranslated == 1
    assert (dlg._width_edit.value, dlg._height_edit.value) == ("20", "30")


def test_page_restored_by_undo_is_the_one_resized():
    kept, other = page("p01.png", (40, 60)), page("p02.png", (40, 60))
    dlg = open_window([kept, other])
    restored = restored_copy(other)
    dlg._state.images_data = [kept, restored]
    dlg._refresh_from_pages()
    assert dlg._selected_entries[1] is restored and dlg._nb_files == 2


def test_validation_works_on_the_pages_of_the_document_at_that_moment():
    kept, other = page("p01.png", (40, 60)), page("p02.png", (40, 60))
    restored = restored_copy(other)
    STARTED.clear()
    dlg = in_document(Dialog(), [kept, other])
    dlg._same_dim, dlg._cur_w, dlg._cur_h = True, 40, 60
    dlg._width_edit = types.SimpleNamespace(text=lambda: "20")
    dlg._height_edit = types.SimpleNamespace(text=lambda: "30")
    dlg._free_ratio_cb = types.SimpleNamespace(isChecked=lambda: False)
    dlg._multi_page_cb = None
    dlg.close = lambda: None
    dlg._state.images_data = [restored]                   # l'une supprimée, l'autre recréée
    dlg._on_ok()
    assert STARTED[0][1] == [restored] and STARTED[0][1][0] is restored
    assert STARTED[0][8] == 1                             # nombre de pages transmis


def test_ok_is_greyed_without_any_page_then_comes_back_with_them():
    entry = page("p01.png", (40, 60))
    dlg = open_window([entry], percent=True)
    enabled = []
    dlg._ok_btn = types.SimpleNamespace(setEnabled=enabled.append)
    del dlg._update_ok_state                              # la vraie méthode
    dlg._state.images_data = []
    dlg._refresh_from_pages()
    assert dlg._selected_entries == [] and enabled[-1] is False
    STARTED.clear()
    dlg._on_ok()
    assert STARTED == []
    dlg._state.images_data = [restored_copy(entry)]
    dlg._refresh_from_pages()
    assert dlg._nb_files == 1 and enabled[-1] is True     # 50 % resté coché


# --- Notification des changements du document ----------------------------------

def test_listeners_hear_each_new_history_state_only():
    state = AppState()
    state.images_data = [page("p01.png", (40, 60))]
    heard = []
    listener = lambda: heard.append(True)
    state.add_document_listener(listener)
    assert save_state_data(state) is True
    assert save_state_data(state) is False                # rien n'a changé
    assert len(heard) == 1
    state.remove_document_listener(listener)
    replace_page(state.images_data[0], (60, 40))
    assert save_state_data(state) is True
    assert len(heard) == 1


def test_failing_listener_never_stops_the_others_nor_the_history():
    state = AppState()
    state.images_data = [page("p01.png", (40, 60))]
    heard = []

    def failing():
        raise ValueError("Aaaa")
    state.add_document_listener(failing)
    state.add_document_listener(lambda: heard.append(True))
    assert save_state_data(state) is True
    assert heard == [True] and len(state.history) == 1


def test_undo_and_redo_notify_the_listeners():
    source = (Path(__file__).resolve().parent.parent / "modules" / "qt"
              / "undo_redo_qt.py").read_text(encoding="utf-8")
    restore = next(n for n in ast.walk(ast.parse(source))
                   if isinstance(n, ast.FunctionDef) and n.name == "restore_state_qt")
    assert ast.unparse(restore.body[-1]) == "notify_document_changed(state)"
