"""Visionneuse : pages de type « Supprimée » (Type="Deleted" du ComicInfo.xml)
sautées à la lecture quand le réglage est actif.

Vérifié :
- comic_info.is_deleted_page reconnaît le type, seul ou parmi d'autres ;
- la liste de lecture (get_reading_indices) garde toutes les pages quand le
  réglage est inactif, retire les pages « Supprimée » quand il est actif, et
  garde toujours la page affichée (ouverte directement) ;
- get_image_indices n'est jamais filtrée (positions mémorisées : marque-page,
  nom « 3/10 ») ;
- le libellé du mode dit quand le réglage est actif, et pourquoi une page
  « Supprimée » s'affiche ;
- le réglage est désactivé par défaut et remis à zéro par « Réinitialiser
  l'application » ;
- dans la mosaïque, la vignette d'une page « Supprimée » est reconnue d'après
  le document de sa scène, son image grisée n'est recalculée que si l'image
  de la vignette a changé, et le grisé suit le réglage de la visionneuse.

image_viewer_qt.py, mosaic_canvas.py et session_restore_qt.py importent Qt : méthodes extraites
par AST (tests/ast_loader.py), l'interface est remplacée.
"""
import ast
import types

import pytest

import ast_loader
from fakes import tr
from modules.qt.comic_info import is_deleted_page
from modules.qt.config_manager import ConfigManager
from modules.qt.state import AppState


def document(*types_):
    """Document d'images dont la page n a le type types_[n] ("" : aucun)."""
    state = AppState()
    state.images_data = [{"orig_name": f"{n:02d}.jpg", "is_image": True}
                         for n in range(len(types_))]
    state._page_attrs_by_entry_id = {id(e): ({"Type": t} if t else {})
                                     for e, t in zip(state.images_data, types_)}
    return state


def test_deleted_type_is_recognised_alone_or_among_others():
    state = document("Deleted", "Story Deleted", "Story", "")
    assert [is_deleted_page(state, e) for e in state.images_data] == [True, True, False, False]


Reading = ast_loader.load_methods("image_viewer_qt.py", "ImageViewer",
                                  {"get_image_indices", "get_reading_indices"},
                                  _state_module=types.SimpleNamespace(state=None))


class Viewer(Reading):
    def __init__(self, state, current, skip):
        self.callbacks = {"state": state}
        self.current_idx = current
        self.skip = skip

    def _skip_deleted_pages(self):
        return self.skip


def test_all_pages_are_read_by_default():
    state = document("", "Deleted", "", "Deleted")
    assert Viewer(state, 0, skip=False).get_reading_indices() == [0, 1, 2, 3]


def test_deleted_pages_are_skipped_when_the_setting_is_on():
    state = document("", "Deleted", "", "Deleted")
    assert Viewer(state, 0, skip=True).get_reading_indices() == [0, 2]


def test_displayed_deleted_page_stays_in_the_reading_list():
    """Ouverte directement (double-clic sur sa vignette), elle est montrée,
    et la navigation doit la retrouver dans la liste pour avancer."""
    state = document("", "Deleted", "", "Deleted")
    assert Viewer(state, 1, skip=True).get_reading_indices() == [0, 1, 2]


def test_image_indices_are_never_filtered():
    state = document("", "Deleted", "")
    assert Viewer(state, 0, skip=True).get_image_indices() == [0, 1, 2]


# ── Navigation : page suivante, précédente, double page, marque-page ────────

Navigation = ast_loader.load_methods(
    "image_viewer_qt.py", "ImageViewer",
    {"get_image_indices", "get_reading_indices", "navigate", "_double_page_pair_start",
     "_check_clear_bookmark_on_last_page"},
    _state_module=types.SimpleNamespace(state=None))


class Bookmarks:
    """Configuration réduite aux marque-pages."""
    def __init__(self, path=None):
        self.marks = {path: 3} if path else {}

    def get_bookmark(self, path):
        return self.marks.get(path)

    def remove_bookmark(self, path):
        self.marks.pop(path, None)


class Reader(Navigation):
    """Visionneuse ouverte sur `current` ; _show_image_position réduite au
    changement de page et à l'effacement du marque-page qu'elle fait."""
    def __init__(self, state, current, skip, mode="single", multiple=()):
        self.callbacks = {"state": state, "on_bookmark_changed": lambda pos: None}
        self.current_idx = current
        self.skip = skip
        self.page_mode = mode
        self.multiple = set(multiple)
        self._macro_recording = False

    def _skip_deleted_pages(self):
        return self.skip

    def _paint_stroke_blocks_command(self):
        return False

    def is_multiple_page(self, idx):
        return idx in self.multiple

    def _show_image_position(self, img_indices, position, save_work=True):
        self.current_idx = img_indices[position]
        self._check_clear_bookmark_on_last_page(img_indices, position)

    def pages_read(self, delta, count):
        seen = []
        for _step in range(count):
            self.navigate(delta)
            seen.append(self.current_idx)
        return seen


@pytest.fixture
def bookmarks(monkeypatch):
    import modules.qt.config_manager as config_manager
    marks = Bookmarks(r"C:\Aaaa\a.cbz")
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: marks)
    return marks


def test_next_page_skips_deleted_pages(bookmarks):
    state = document("", "Deleted", "", "Deleted", "Deleted", "")
    assert Reader(state, 0, skip=True).pages_read(+1, 3) == [2, 5, 5]


def test_previous_page_skips_deleted_pages(bookmarks):
    state = document("", "Deleted", "", "Deleted", "Deleted", "")
    assert Reader(state, 5, skip=True).pages_read(-1, 3) == [2, 0, 0]


def test_every_page_is_read_when_the_setting_is_off(bookmarks):
    state = document("", "Deleted", "", "Deleted")
    assert Reader(state, 0, skip=False).pages_read(+1, 3) == [1, 2, 3]


def test_deleted_page_opened_directly_leads_to_its_neighbours(bookmarks):
    state = document("", "Deleted", "", "")
    assert Reader(state, 1, skip=True).pages_read(+1, 1) == [2]
    assert Reader(state, 1, skip=True).pages_read(-1, 1) == [0]


def test_deleted_page_once_left_is_not_read_again(bookmarks):
    """Quittée, la page ouverte directement n'est plus dans la liste de
    lecture : revenir en arrière la saute."""
    state = document("", "Deleted", "", "")
    reader = Reader(state, 1, skip=True)
    assert reader.pages_read(+1, 1) == [2]
    assert reader.pages_read(-1, 1) == [0]


def test_double_page_pairs_skip_deleted_pages(bookmarks):
    """Couverture seule, puis paires de pages lues : la page « Supprimée »
    ne prend pas de place dans une paire."""
    state = document("", "", "Deleted", "", "", "")
    reader = Reader(state, 0, skip=True, mode="double")
    assert reader.pages_read(+1, 3) == [1, 4, 5]


def test_double_page_multiple_page_stays_alone(bookmarks):
    state = document("", "", "Deleted", "", "", "")
    reader = Reader(state, 1, skip=True, mode="double", multiple={3})
    assert reader.pages_read(+1, 2) == [3, 4]


def test_page_not_in_the_reading_list_goes_back_to_the_first(bookmarks):
    """Page affichée retirée du document entre-temps : la lecture reprend à la
    première page lue, jamais sur une page inexistante."""
    state = document("", "Deleted", "", "")
    reader = Reader(state, 9, skip=True)
    assert reader.pages_read(+1, 1) == [0]


def test_bookmark_cleared_on_the_last_page_read(bookmarks):
    """Pages « Supprimée » en fin de BD, sautées : la dernière page lue est la
    fin de la lecture, le marque-page est effacé."""
    state = document("", "", "Deleted")
    state.current_file = r"C:\Aaaa\a.cbz"
    Reader(state, 0, skip=True).pages_read(+1, 1)
    assert bookmarks.marks == {}


def test_bookmark_kept_before_the_last_page(bookmarks):
    state = document("", "", "Deleted")
    state.current_file = r"C:\Aaaa\a.cbz"
    Reader(state, 0, skip=False).pages_read(+1, 1)
    assert bookmarks.marks == {r"C:\Aaaa\a.cbz": 3}


def test_bookmark_cleared_when_the_last_pair_is_shown(bookmarks):
    state = document("", "", "", "Deleted")
    state.current_file = r"C:\Aaaa\a.cbz"
    Reader(state, 0, skip=True, mode="double").pages_read(+1, 1)
    assert bookmarks.marks == {}


ModeLabel = ast_loader.load_methods("image_viewer_qt.py", "ImageViewer", {"_update_mode_label"}, _=tr)


class Label:
    def __init__(self):
        self.text = None

    def setText(self, text):
        self.text = text

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class LabelViewer(ModeLabel):
    def __init__(self, skip, deleted):
        self.is_animated_gif = False
        self.page_mode = "single"
        self._mode_label = Label()
        self.skip, self.deleted = skip, deleted

    def _skip_deleted_pages(self):
        return self.skip

    def _current_page_is_deleted(self):
        return self.deleted

    def _overlay_top(self):
        return 0


def mode_lines(skip, deleted):
    viewer = LabelViewer(skip, deleted)
    viewer._update_mode_label()
    return viewer._mode_label.text.split("\n")


def test_mode_label_says_nothing_more_when_the_setting_is_off():
    assert mode_lines(skip=False, deleted=True) == ["viewer.mode_single"]


def test_mode_label_has_a_second_line_when_the_setting_is_on():
    assert mode_lines(skip=True, deleted=False) == ["viewer.mode_single", "viewer.skip_deleted_pages_active"]


def test_mode_label_explains_a_deleted_page_opened_directly():
    assert mode_lines(skip=True, deleted=True) == [
        "viewer.mode_single", "viewer.skip_deleted_pages_active", "viewer.deleted_page_shown"]


def test_setting_is_off_by_default():
    assert ConfigManager.DEFAULT_CONFIG["viewer_skip_deleted_pages"] is False


def test_reset_turns_the_setting_off():
    tree = ast_loader.module_tree(ast_loader.QT_DIR / "session_restore_qt.py")
    reset = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "reset_to_defaults")
    calls = [n for n in ast.walk(reset) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "set_viewer_skip_deleted_pages"]
    assert calls and isinstance(calls[0].args[0], ast.Constant) and calls[0].args[0].value is False


# ── Mosaïque : vignette d'une page « Supprimée » ─────────────────────────────

class _Pixmap:
    """Pixmap de vignette réduite à sa clé de cache Qt."""
    def __init__(self, key):
        self.key = key

    def cacheKey(self):
        return self.key


def thumbnail_class(state, grayings):
    """ThumbnailItem réduit à _is_deleted_page et _grayed_pixmap ; le document
    vient de la scène (_resolve_state), `grayings` compte les passages en gris."""
    def gray(pixmap):
        grayings.append(pixmap.key)
        return ("gris", pixmap.key)
    return ast_loader.load_methods("mosaic_canvas.py", "ThumbnailItem",
                                   {"_is_deleted_page", "_grayed_pixmap"},
                                   _resolve_state=lambda scene: state, _grayed_pixmap=gray,
                                   QPixmap=object)


def test_mosaic_thumbnail_knows_a_deleted_page():
    state = document("Deleted", "Story")
    Item = thumbnail_class(state, [])
    items = []
    for entry in state.images_data:
        item = Item()
        item._scene, item.entry = None, entry
        items.append(item)
    assert [i._is_deleted_page() for i in items] == [True, False]


def test_mosaic_thumbnail_grays_its_image_once_until_it_changes():
    grayings = []
    item = thumbnail_class(document("Deleted"), grayings)()
    item._grayed, item._pixmap = None, _Pixmap(1)
    assert item._grayed_pixmap() == ("gris", 1)
    item._grayed_pixmap()
    assert grayings == [1]
    item._pixmap = _Pixmap(2)          # image rechargée (retouche, taille)
    assert item._grayed_pixmap() == ("gris", 2)
    assert grayings == [1, 2]


def test_deleted_pages_are_grayed_only_when_the_viewer_skips_them(monkeypatch):
    """Le badge marque la page ; le grisé dit qu'elle ne sera pas lue."""
    import modules.qt.config_manager as config_manager
    skipped = ast_loader.load("mosaic_canvas.py", {"deleted_pages_skipped"})["deleted_pages_skipped"]
    for setting in (False, True):
        cfg = types.SimpleNamespace(get_viewer_skip_deleted_pages=lambda s=setting: s)
        monkeypatch.setattr(config_manager, "get_config_manager", lambda c=cfg: c)
        assert skipped() is setting
