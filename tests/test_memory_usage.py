"""Mémoire de MosaicView pour l'indicateur de la barre de statut
(modules/qt/memory_usage.py, sans Qt).

Vérifié :
- couleur d'après le pourcentage affiché de la mémoire du PC : vert jusqu'à
  33 %, orange de 34 à 66 %, rouge à partir de 67 % (un tiers par couleur,
  comme le cadran de la jauge) ;
- estimation d'un panneau : octets des pages comptés une fois même s'ils
  sont partagés avec l'historique, page que seul l'historique garde comptée,
  images décodées Pillow et images Qt comprises, panneau vide nul ; pages
  déjà lues par un chargement en cours comptées au fur et à mesure (une
  fois seulement quand elles arrivent dans le document), plus après le
  retrait de leur inscription ni après la fermeture du document, page en
  train de changer sautée sans erreur ; inscription et retrait présents
  dans les cinq chargements ;
- programmes annexes : enfants et petits-enfants retrouvés, process dont le
  numéro a été réattribué écartés, process disparus ignorés ; reconnus par
  leur exe (PDF, 7-Zip, UnRAR), les autres programmes lancés par MosaicView
  (Bloc-notes…) et ce qu'ils lancent n'étant pas comptés ;
- programme de démarrage de la version « un seul fichier » : reconnu
  seulement pour cette version, le même exe, créé avant MosaicView ;
- programmes annexes maintenus entre deux mesures (HelperHold) : gardés
  pendant leur délai quand une mesure ne les voit pas, remplacés par une
  nouvelle mesure qui les voit (délai relancé), un délai par programme,
  retirés après leur délai, oubliés à la fin d'un chargement ou d'un lot
  (mesure immédiate seulement en fin de chargement), total, noms et tailles
  dans l'ordre d'affichage ;
- indicateur de la barre de statut (StatusBar.refresh_memory, extraite par
  AST) : pourcentage de MosaicView et de ses programmes annexes, couleur de
  son seuil, tiret grisé sans mesure ; info-bulle recalculée à chaque
  mesure seulement quand elle est affichée ;
- info-bulle (_memory_tooltip_html, extraite par AST) : MosaicView, programmes
  annexes (seulement s'il y en a) et panneau, en taille et en pourcentage
  (« moins de 1% » quand l'arrondi donnerait 0 % ; panneau vide sans
  « environ » ni pourcentage),
  jauge à l'aiguille sur le pourcentage de MosaicView, explication sans
  jauge si la mesure manque, texte échappé ;
- jauges livrées (_gauge_icon_src) : une par pourcentage de 0 à 100, en 48
  et 96 px, pourcentage ramené entre 0 et 100 ;
- vraie mesure sous Windows : MosaicView, mémoire du PC, et un programme
  annexe réellement lancé compté dans les programmes annexes.
"""
import subprocess
import sys
import time
import types
from html import unescape

from PIL import Image

import ast_loader
from modules.qt import memory_usage as mu
from modules.qt.state import AppState


# ---------------------------------------------------------------------------
# Couleur et pourcentage
# ---------------------------------------------------------------------------

def test_level_follows_the_thresholds():
    # un tiers de l'échelle par couleur, comme les zones du cadran de la jauge
    assert mu.memory_level(0) == "green"
    assert mu.memory_level(33) == "green"
    assert mu.memory_level(34) == "orange"
    assert mu.memory_level(66) == "orange"
    assert mu.memory_level(67) == "red"
    assert mu.memory_level(130) == "red"


def test_percent_is_rounded_and_safe_without_ram():
    assert mu.percent(5 * 1024, 100 * 1024) == 5
    assert mu.percent(1, 3) == 33
    assert mu.percent(10, 0) == 0


# ---------------------------------------------------------------------------
# Estimation d'un panneau
# ---------------------------------------------------------------------------

class FakeQtImage:
    """QPixmap reconnue sans Qt : width, height, depth."""

    def __init__(self, width, height):
        self._w, self._h = width, height

    def width(self):
        return self._w

    def height(self):
        return self._h

    def depth(self):
        return 32


def entry(data, name="a.jpg", **extra):
    e = {"orig_name": name, "bytes": data, "extension": ".jpg", "is_image": True}
    e.update(extra)
    return e


def history_copy(e):
    return {"orig_name": e["orig_name"], "bytes": e["bytes"], "extension": ".jpg",
            "is_image": True, "meta": {}, "_original_id": 1}


def test_empty_panel_is_zero():
    assert mu.panel_memory_estimate(AppState()) == 0


def test_bytes_shared_with_the_history_are_counted_once():
    st = AppState()
    page = entry(b"x" * 1000)
    st.images_data = [page]
    st.history = [{"entries": [history_copy(page)]}, {"entries": [history_copy(page)]}]
    assert mu.panel_memory_estimate(st) == 1000


def test_page_kept_only_by_the_history_is_counted():
    st = AppState()
    old, new = entry(b"o" * 700), entry(b"n" * 300)
    st.images_data = [new]
    st.history = [{"entries": [history_copy(old)]}, {"entries": [history_copy(new)]}]
    assert mu.panel_memory_estimate(st) == 1000


def test_bytes_in_page_properties_of_the_history_are_counted_once():
    st = AppState()
    data = b"z" * 400
    st.images_data = [entry(data, altered_bytes=data)]
    copy = history_copy(st.images_data[0])
    copy["meta"] = {"altered_bytes": data, "dpi": (300, 300)}
    st.history = [{"entries": [copy]}]
    assert mu.panel_memory_estimate(st) == 400


def test_decoded_images_and_thumbnails_are_counted():
    st = AppState()
    st.images_data = [entry(b"b" * 100,
                            img=Image.new("RGB", (10, 20)),             # 4 octets/pixel
                            large_thumb_pil=Image.new("L", (5, 5)),     # 1 octet/pixel
                            qt_pixmap_large=FakeQtImage(4, 4))]         # 32 bits
    assert mu.panel_memory_estimate(st) == 100 + 10 * 20 * 4 + 5 * 5 + 4 * 4 * 4


def test_pages_being_read_are_counted_as_they_arrive():
    st = AppState()
    pages = []                                 # liste remplie par le worker
    mu.reading_started(st, pages)
    assert mu.panel_memory_estimate(st) == 0
    pages.append(entry(b"a" * 300, qt_pixmap_large=FakeQtImage(2, 2)))
    assert mu.panel_memory_estimate(st) == 300 + 2 * 2 * 4
    pages.append(entry(b"b" * 200))
    assert mu.panel_memory_estimate(st) == 300 + 16 + 200


def test_pages_handed_to_the_document_are_counted_once():
    # fin du chargement : pages dans le document et encore dans la liste du
    # worker jusqu'au retrait de son inscription
    st = AppState()
    pages = [entry(b"a" * 300), entry(b"b" * 200)]
    mu.reading_started(st, pages)
    st.images_data = list(pages)
    assert mu.panel_memory_estimate(st) == 500
    mu.reading_finished(st, pages)
    assert mu.panel_memory_estimate(st) == 500 and st.pages_being_read == []


def test_abandoned_reading_is_no_longer_counted():
    st = AppState()
    pages = [entry(b"a" * 300)]
    mu.reading_started(st, pages)
    mu.reading_finished(st, pages)
    assert mu.panel_memory_estimate(st) == 0
    mu.reading_finished(st, pages)             # retrait répété : sans effet
    assert st.pages_being_read == []


def test_added_pages_are_counted_with_the_document():
    st = AppState()
    st.images_data = [entry(b"d" * 100)]
    mu.reading_started(st, [entry(b"n" * 40)])
    assert mu.panel_memory_estimate(st) == 140


def test_reading_for_a_closed_document_is_ignored_then_dropped():
    st = AppState()
    old = [entry(b"o" * 700)]
    mu.reading_started(st, old)
    st.doc_generation += 1                     # fichier fermé ou remplacé
    assert mu.panel_memory_estimate(st) == 0
    new = [entry(b"n" * 50)]
    mu.reading_started(st, new)
    assert mu.panel_memory_estimate(st) == 50
    assert [pages for _gen, pages in st.pages_being_read] == [new]


def test_page_changing_while_it_is_read_is_skipped_this_time():
    class Changing(dict):
        def values(self):
            raise RuntimeError("dictionary changed size during iteration")

    st = AppState()
    mu.reading_started(st, [Changing(bytes=b"x" * 10), entry(b"y" * 20)])
    assert mu.panel_memory_estimate(st) == 20


def test_each_loader_counts_its_pages_while_reading():
    """Les cinq chargements (ouverture d'une archive, d'un PDF, d'images ;
    ajout d'une archive, d'un PDF) remplissent la liste inscrite, et chaque
    chargeur l'inscrit puis la retire."""
    sources = {
        "archive_loader.py": ["all_data = self.pages_read", "reading_started(st, self._worker.pages_read)",
                              "reading_finished(self._state, worker.pages_read)",
                              "reading_finished(self._state, self._worker.pages_read)"],
        "pdf_loading_qt.py": ["temp_entries = self.pages_read", "new_entries = self.pages_read",
                              "reading_started(st, self._worker.pages_read)",
                              "reading_finished(self._state, worker.pages_read)",
                              "reading_finished(self._state, self._worker.pages_read)",
                              "reading_started(state, worker.pages_read)",
                              "reading_finished(state, worker.pages_read)",
                              # fin de l'ajout d'un PDF : process PDF retiré tout de suite
                              "helper_programs_finished()"],
        "import_merge_qt.py": ["new_entries  = self.pages_read",
                               "reading_started(state, worker.pages_read)",
                               "reading_finished(state, worker.pages_read)"],
        "panel_widget.py": ["new_entries  = self.pages_read",
                            "reading_started(st, worker.pages_read)",
                            "reading_finished(st, worker.pages_read)"],
    }
    for name, snippets in sources.items():
        text = (ast_loader.QT_DIR / name).read_text(encoding="utf-8")
        missing = [s for s in snippets if s not in text]
        assert not missing, f"{name} : {missing}"


def test_pillow_storage_per_mode():
    assert mu._pil_size(Image.new("1", (8, 2))) == 16
    assert mu._pil_size(Image.new("P", (8, 2))) == 16
    assert mu._pil_size(Image.new("I;16", (8, 2))) == 32
    assert mu._pil_size(Image.new("F", (8, 2))) == 64
    assert mu._pil_size(Image.new("LA", (8, 2))) == 64
    assert mu._pil_size(Image.new("RGBA", (8, 2))) == 64


# ---------------------------------------------------------------------------
# Programmes annexes et programme de démarrage
# ---------------------------------------------------------------------------

def pdf(pid):
    return "PDF"


def test_children_and_grandchildren_are_found():
    parents = {10: 1, 20: 10, 21: 10, 30: 20, 99: 1}
    created = {20: 101, 21: 102, 30: 103, 99: 50}
    found = mu._descendants(10, 100, parents, created.get, pdf)
    assert sorted(found) == [(20, "PDF"), (21, "PDF"), (30, "PDF")]


def test_process_with_a_reused_parent_number_is_not_a_child():
    # 40 annonce 10 pour parent, mais existait avant lui : son vrai parent
    # s'est terminé et 10 a été réattribué à MosaicView.
    parents = {10: 1, 40: 10, 41: 40}
    created = {40: 90, 41: 95}
    assert mu._descendants(10, 100, parents, created.get, pdf) == []


def test_vanished_process_is_ignored():
    parents = {10: 1, 20: 10, 21: 10}
    created = {21: 105}                       # 20 terminé entre-temps
    assert mu._descendants(10, 100, parents, created.get, pdf) == [(21, "PDF")]


def test_process_that_is_its_own_parent_does_not_loop():
    parents = {0: 0, 10: 0, 20: 10}
    assert mu._descendants(10, 100, parents, {20: 101}.get, pdf) == [(20, "PDF")]


def test_other_programs_and_what_they_launch_are_not_counted():
    # 20 : 7-Zip ; 21 : Bloc-notes d'un journal, qui a lui-même lancé 31
    parents = {10: 1, 20: 10, 21: 10, 31: 21}
    created = {20: 101, 21: 102, 31: 103}
    kinds = {20: "7-Zip"}
    assert mu._descendants(10, 100, parents, created.get, kinds.get) == [(20, "7-Zip")]


def test_helper_programs_are_recognized_by_their_exe():
    own = (r"C:\Apps\MosaicView.exe", None)
    assert mu.helper_kind(r"C:\Apps\7zip\7z.exe", own) == "7-Zip"
    assert mu.helper_kind(r"C:\Apps\unrar\UnRAR.exe", own) == "UnRAR"
    assert mu.helper_kind(r"c:\apps\MOSAICVIEW.exe", own) == "PDF"     # process PDF
    assert mu.helper_kind(r"C:\Windows\System32\notepad.exe", own) is None
    assert mu.helper_kind(r"C:\Other\MosaicView.exe", own) is None    # un autre exe
    assert mu.helper_kind(None, own) is None
    # environnement virtuel : le python.exe de l'environnement relance celui
    # de l'installation, les deux sont MosaicView
    venv = (r"C:\Proj\.venv\Scripts\python.exe", r"C:\Python311\python.exe")
    assert mu.helper_kind(r"C:\Python311\python.exe", venv) == "PDF"


def test_onefile_is_recognized_only_for_that_version():
    exe = r"C:\Apps\MosaicView.exe"
    assert mu._is_onefile(True, exe, r"C:\Users\x\AppData\Local\Temp\_MEI1234")
    assert mu._is_onefile(True, exe, r"D:\Temp\_MEI1234")           # autre lecteur
    assert not mu._is_onefile(True, exe, r"C:\Apps")                # version dossier
    assert not mu._is_onefile(True, exe, r"C:\Apps\_internal")      # PyInstaller 6
    assert not mu._is_onefile(True, exe, r"c:\apps\_INTERNAL")
    assert not mu._is_onefile(False, sys.executable, None)          # depuis Python


def test_launcher_must_be_the_same_exe_created_before():
    exe = r"C:\Apps\MosaicView.exe"
    assert mu._is_launcher(r"c:\apps\mosaicview.EXE", 50, exe, 100)
    assert not mu._is_launcher(r"C:\Windows\explorer.exe", 50, exe, 100)
    assert not mu._is_launcher(exe, 150, exe, 100)                  # numéro réattribué
    assert not mu._is_launcher(None, 50, exe, 100)
    assert not mu._is_launcher(exe, None, exe, 100)


# ---------------------------------------------------------------------------
# Programmes annexes maintenus entre deux mesures (HelperHold)
# ---------------------------------------------------------------------------

def seen(*sizes, app=100, ram=1000):
    """Mesure qui voit les programmes annexes `sizes` ((nom, octets)…)."""
    return mu._with_helpers(mu.MemoryMeasure(app, 0, ram), dict(sizes))


def test_helper_missed_by_a_measure_is_kept_during_its_delay():
    # un UnRAR par page : la mesure tombe tantôt pendant l'un, tantôt entre deux
    hold = mu.HelperHold(seconds=3)
    assert hold.apply(seen(("UnRAR", 40)), now=10).helpers == 40
    kept = hold.apply(seen(), now=12)
    assert (kept.helpers, kept.helper_names, kept.helper_sizes) == (40, ("UnRAR",), (("UnRAR", 40),))
    assert kept.app == 100 and kept.ram == 1000          # le reste de la mesure est inchangé
    gone = hold.apply(seen(), now=13.5)                  # délai écoulé
    assert (gone.helpers, gone.helper_names, gone.helper_sizes) == (0, (), ())


def test_new_measure_of_a_helper_replaces_it_and_restarts_its_delay():
    hold = mu.HelperHold(seconds=3)
    hold.apply(seen(("UnRAR", 40)), now=10)
    assert hold.apply(seen(("UnRAR", 25)), now=12).helpers == 25    # remplacée, même plus petite
    assert hold.apply(seen(), now=14.5).helpers == 25               # délai compté depuis 12
    assert hold.apply(seen(), now=15.5).helpers == 0


def test_each_helper_has_its_own_delay():
    hold = mu.HelperHold(seconds=3)
    hold.apply(seen(("UnRAR", 40)), now=10)
    both = hold.apply(seen(("PDF", 7)), now=12)
    # ordre d'affichage (HELPER_NAMES), pas ordre d'apparition
    assert both.helper_names == ("PDF", "UnRAR") and both.helpers == 47
    assert both.helper_sizes == (("PDF", 7), ("UnRAR", 40))
    later = hold.apply(seen(), now=14)                   # UnRAR expiré, PDF encore gardé
    assert later.helper_names == ("PDF",) and later.helpers == 7


def test_forgotten_helpers_are_no_longer_kept():
    hold = mu.HelperHold(seconds=3)
    hold.apply(seen(("UnRAR", 40), ("7-Zip", 5)), now=10)
    hold.forget()
    assert hold.apply(seen(), now=10.5).helper_names == ()
    # un programme qui tourne encore au moment de la mesure revient aussitôt
    assert hold.apply(seen(("7-Zip", 6)), now=11).helper_sizes == (("7-Zip", 6),)


def test_missing_measure_stays_missing_and_keeps_the_helpers():
    hold = mu.HelperHold(seconds=3)
    hold.apply(seen(("UnRAR", 40)), now=10)
    assert hold.apply(None, now=11) is None              # indicateur sans valeur
    assert hold.apply(seen(), now=12).helpers == 40


def test_end_of_loading_forgets_the_helpers_and_measures_at_once(monkeypatch):
    calls = []
    monkeypatch.setattr(mu, "_helper_hold", mu.HelperHold(seconds=3))
    monkeypatch.setattr(mu, "_measure_now", lambda: calls.append("measure"))
    mu.hold_helper_programs(seen(("UnRAR", 40)), now=10)
    mu.helper_programs_finished()
    assert calls == ["measure"]
    assert mu.hold_helper_programs(seen(), now=10.5).helpers == 0


def test_end_of_batch_forgets_the_helpers_without_measuring(monkeypatch):
    # appelée depuis le thread du lot : aucune mesure (fonction du thread Qt)
    calls = []
    monkeypatch.setattr(mu, "_helper_hold", mu.HelperHold(seconds=3))
    monkeypatch.setattr(mu, "_measure_now", lambda: calls.append("measure"))
    mu.hold_helper_programs(seen(("7-Zip", 5)), now=10)
    from modules.qt.batch_claims import BatchClaims
    BatchClaims.start([]).finish()
    assert calls == []
    assert mu.hold_helper_programs(seen(), now=10.5).helpers == 0


def test_end_of_loading_without_main_window_only_forgets(monkeypatch):
    monkeypatch.setattr(mu, "_helper_hold", mu.HelperHold(seconds=3))
    monkeypatch.setattr(mu, "_measure_now", None)
    mu.hold_helper_programs(seen(("PDF", 9)), now=10)
    mu.helper_programs_finished()
    assert mu.hold_helper_programs(seen(), now=10.5).helpers == 0


# ---------------------------------------------------------------------------
# Indicateur de la barre de statut (status_bar_qt.StatusBar.refresh_memory)
# ---------------------------------------------------------------------------

class FakeLabel:
    def __init__(self):
        self.text = self.style = None

    def setFont(self, font):
        pass

    def setText(self, text):
        self.text = text

    def setStyleSheet(self, style):
        self.style = style


THEME = {"disabled": "#999", "memory_green": "#0a0", "memory_orange": "#f80",
         "memory_red": "#e00"}


def shown(measure):
    """Texte et couleur de l'indicateur pour une mesure."""
    Base = ast_loader.load_methods(
        "status_bar_qt.py", "StatusBar", {"refresh_memory"},
        _memory_measure=measure, _=lambda key, **kw: f"{key}{kw}",
        get_current_theme=lambda: THEME, get_current_font=lambda size: None,
        percent=mu.percent, memory_level=mu.memory_level)

    class Bar(Base):
        def __init__(self):
            self._memory_indicator = FakeLabel()
            self._overlay_tip = types.SimpleNamespace(is_visible_for=lambda widget: False)

    bar = Bar()
    bar.refresh_memory()
    return bar._memory_indicator.text, bar._memory_indicator.style


def test_indicator_shows_the_percentage_in_the_color_of_its_threshold():
    gib = 1024 ** 3
    assert shown(mu.MemoryMeasure(2 * gib, 0, 16 * gib)) == (
        "labels.memory_indicator{'percent': 12}", "color: #0a0;")
    # programmes annexes comptés : 6 + 2 Go sur 16 = 50 %, orange
    assert shown(mu.MemoryMeasure(6 * gib, 2 * gib, 16 * gib)) == (
        "labels.memory_indicator{'percent': 50}", "color: #f80;")
    assert shown(mu.MemoryMeasure(12 * gib, 0, 16 * gib))[1] == "color: #e00;"
    # seuil comparé au pourcentage affiché : 33,4 % s'affiche « 33% », en vert
    assert shown(mu.MemoryMeasure(334, 0, 1000)) == (
        "labels.memory_indicator{'percent': 33}", "color: #0a0;")
    assert shown(mu.MemoryMeasure(336, 0, 1000))[1] == "color: #f80;"


def test_indicator_without_measure_shows_a_greyed_dash():
    assert shown(None) == ("labels.memory_indicator_unknown{}", "color: #999;")


def test_visible_tooltip_is_updated_with_each_measure():
    calls = []
    Base = ast_loader.load_methods(
        "status_bar_qt.py", "StatusBar", {"refresh_memory"},
        _memory_measure=mu.MemoryMeasure(1, 0, 10), _=lambda key, **kw: key,
        get_current_theme=lambda: THEME, get_current_font=lambda size: None,
        percent=mu.percent, memory_level=mu.memory_level)

    class Bar(Base):
        def __init__(self, visible):
            self._memory_indicator = FakeLabel()
            self._overlay_tip = types.SimpleNamespace(
                is_visible_for=lambda widget: visible and widget is self._memory_indicator)

        def _update_memory_tooltip(self):
            calls.append("tooltip")

    Bar(visible=False).refresh_memory()
    assert calls == []                       # personne ne la voit : rien à calculer
    Bar(visible=True).refresh_memory()
    assert calls == ["tooltip"]


def tooltip(measure, state=None, shares=None):
    """Info-bulle (status_bar_qt._memory_tooltip_html) ; tailles en octets
    bruts, jauge remplacée par une adresse fictive par pourcentage."""
    seen = [] if shares is None else shares
    ns = ast_loader.load(
        "status_bar_qt.py", {"_memory_tooltip_html", "_GAUGE_TOOLTIP_SIZE"},
        _=lambda key, **kw: f"{key}{kw}", format_file_size=lambda size: f"{size}o",
        percent=mu.percent, panel_memory_estimate=mu.panel_memory_estimate,
        _gauge_icon_src=lambda share, ratio: seen.append(share) or f"file:///{share}.png")
    return ns["_memory_tooltip_html"](measure, state, 1.0)


def test_tooltip_gives_mosaicview_helpers_and_panel_with_the_gauge_of_its_percentage():
    st = AppState()
    st.images_data = [entry(b"x" * 25)]
    shares = []
    # texte échappé dans le HTML (apostrophes en &#x27;) : comparé une fois
    # rendu à l'état de texte
    html = unescape(tooltip(mu.MemoryMeasure(40, 10, 100, ("PDF", "7-Zip")), st, shares))
    assert "tooltip.memory_indicator_app{'size': '50o', 'percent': 50}" in html
    assert ("tooltip.memory_indicator_helpers{'names': 'PDF, 7-Zip', 'size': '10o', "
            "'percent': 10}") in html
    assert "tooltip.memory_indicator_panel{'size': '25o', 'percent': 25}" in html
    # aiguille sur le pourcentage de MosaicView, programmes annexes compris
    assert shares == [50] and '<img src="file:///50.png" width="48"' in html
    # ordre des lignes : MosaicView, programmes annexes, panneau
    assert html.index("_app") < html.index("_helpers") < html.index("_panel")


def test_tooltip_without_helper_program_has_no_helper_line():
    html = tooltip(mu.MemoryMeasure(10, 0, 100), AppState())
    assert "_helpers" not in html and "_panel" in html


def test_tooltip_share_rounded_to_zero_says_less_than_one_percent():
    # 4 o sur 1000 = 0,4 % : « 0% » serait faux, la taille n'est pas nulle
    st = AppState()
    st.images_data = [entry(b"x" * 4)]
    html = unescape(tooltip(mu.MemoryMeasure(3, 1, 1000, ("PDF",)), st))
    assert "tooltip.memory_indicator_app_below_one{'size': '4o'}" in html
    assert ("tooltip.memory_indicator_helpers_below_one{'names': 'PDF', "
            "'size': '1o'}") in html
    assert "tooltip.memory_indicator_panel_below_one{'size': '4o'}" in html


def test_tooltip_empty_panel_has_neither_about_nor_percentage():
    html = unescape(tooltip(mu.MemoryMeasure(10, 0, 100), AppState()))
    assert "tooltip.memory_indicator_panel_empty{'size': '0o'}" in html
    assert "tooltip.memory_indicator_panel{" not in html


def test_tooltip_without_measure_explains_it_without_gauge():
    html = tooltip(None)
    assert "tooltip.memory_indicator_unknown" in html and "<img" not in html


def test_tooltip_text_is_escaped():
    # texte traduit inséré dans du HTML : une balise d'une traduction ne
    # doit jamais être interprétée
    ns = ast_loader.load(
        "status_bar_qt.py", {"_memory_tooltip_html", "_GAUGE_TOOLTIP_SIZE"},
        _=lambda key, **kw: "<b>x</b>", format_file_size=str, percent=mu.percent,
        panel_memory_estimate=mu.panel_memory_estimate,
        _gauge_icon_src=lambda share, ratio: None)
    html = ns["_memory_tooltip_html"](None, None, 1.0)
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>" not in html


def gauge_src(share, ratio):
    """Adresse de la jauge (status_bar_qt._gauge_icon_src), QUrl remplacé
    par le chemin lui-même."""
    import os
    ns = ast_loader.load(
        "status_bar_qt.py", {"_gauge_icon_src"}, os=os,
        QUrl=types.SimpleNamespace(
            fromLocalFile=lambda path: types.SimpleNamespace(toString=lambda: path)),
        __file__=str(ast_loader.QT_DIR / "status_bar_qt.py"))
    return ns["_gauge_icon_src"](share, ratio)


def test_gauge_exists_for_every_percentage_at_both_sizes():
    # images livrées dans memory_gauge/ (tools/make_memory_gauges.py)
    for share in range(101):
        for ratio, size in ((1.0, 48), (2.0, 96)):
            path = gauge_src(share, ratio)
            assert path is not None, (share, ratio)
            with Image.open(path) as img:
                assert img.size == (size, size) and img.mode == "RGBA"
    assert gauge_src(7, 1.0).endswith("gauge_007.png")
    assert gauge_src(7, 1.5).endswith("gauge_007@2x.png")


def test_gauge_percentage_is_kept_between_0_and_100():
    # MosaicView au-delà de la mémoire physique (mémoire virtuelle) : aiguille en butée
    assert gauge_src(130, 1.0).endswith("gauge_100.png")
    assert gauge_src(-1, 1.0).endswith("gauge_000.png")


# ---------------------------------------------------------------------------
# Vraie mesure (Windows)
# ---------------------------------------------------------------------------

def test_real_measure_counts_a_running_helper_program():
    before = mu.measure()
    assert before is not None
    assert 0 < before.app < before.ram
    helper = subprocess.Popen(
        [sys.executable, "-c", "import time; b = bytearray(64 * 1024 * 1024); time.sleep(30)"])
    try:
        deadline = time.monotonic() + 15
        during = mu.measure()
        while during.helpers < 64 * 1024 * 1024 and time.monotonic() < deadline:
            time.sleep(0.1)
            during = mu.measure()
        assert during.helpers >= 64 * 1024 * 1024
        assert during.helper_names == ("PDF",)    # même exe que le test : « PDF »
        assert during.helper_sizes == (("PDF", during.helpers),)
    finally:
        helper.kill()
        helper.wait()
