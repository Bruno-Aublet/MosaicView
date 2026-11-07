"""Visionneuse ouverte pendant que la mosaïque modifie les pages du document
(image_viewer_qt.ImageViewer._sync_with_document, sync_image_viewers_with_document).

Vérifié :
- page affichée supprimée (ou déplacée vers l'autre panneau, ou Annuler de son
  ajout) : son travail non validé visait une page qui n'existe plus —
  l'appliquer à la page qui a pris sa place la modifierait à tort. La
  visionneuse se ferme sans demander, sauf s'il reste du travail sur d'autres
  pages : elle passe alors à l'image la plus proche et le garde ;
- page déplacée (page insérée ou supprimée avant elle) : la visionneuse suit
  la même page, jamais celle qui a pris sa position ; réaffichée en lecture,
  pas pendant un outil actif ;
- page restée à sa place : seul son nom est remis à jour ;
- seules les visionneuses de ce document sont prévenues, une visionneuse
  détruite est oubliée.

image_viewer_qt.py importe PySide6 : les méthodes sont extraites par AST
(tests/ast_loader.load_methods), l'objet visionneuse est remplacé.
"""
import types

from modules.qt.state import AppState

import ast_loader

Base = ast_loader.load_methods("image_viewer_qt.py", "ImageViewer",
                               {"current_idx", "_current_page_present", "_sync_with_document",
                                "_has_work_on_other_pages", "_leave_removed_page"})
_PageWorkStore = ast_loader.load("image_viewer_qt.py", {"_PageWorkStore"})["_PageWorkStore"]


def page(name):
    return {"orig_name": name, "bytes": name.encode(), "extension": ".png", "is_image": True}


class Viewer(Base):
    def __init__(self, state, index, tool=None):
        self._doc_state = state
        self.current_idx = index
        self._toolbar = types.SimpleNamespace(active_tool=tool)
        self._close_confirmed = False
        self._closed = False
        self._macro_reading = False
        self._macro_recording = False
        self._macro_record_dialog = None
        self._crop_by_page = _PageWorkStore(state)
        self._straighten_by_page = _PageWorkStore(state)
        self._text_blocks_by_page = _PageWorkStore(state)
        self._shapes_by_page = _PageWorkStore(state)
        self._pasted_images_by_page = _PageWorkStore(state)
        self._transp_work_img_by_page = _PageWorkStore(state)
        self.events = []

    def close(self):
        self.events.append("fermée")
        self._closed = True

    def _forget_outdated_transparency_work(self):
        pass

    def _macro_cancel_recording(self):
        self.events.append("enregistrement abandonné")

    def get_image_indices(self):
        return [i for i, e in enumerate(self._doc_state.images_data) if e["is_image"]]

    def _show_image_position(self, img_indices, position, save_work=True):
        self.current_idx = img_indices[position]
        self.events.append(("page", self.current_idx, save_work))

    def display_image(self):
        self.events.append(("affichée", self.current_idx))

    def _update_name_label(self):
        self.events.append("nom")


def document(*names):
    st = AppState()
    st.images_data = [page(n) for n in names]
    return st


def test_deleted_page_closes_the_viewer_without_asking():
    st = document("p01", "p02", "p03")
    viewer = Viewer(st, 1)
    del st.images_data[1]
    viewer._sync_with_document()
    assert viewer.events == ["fermée"]
    assert viewer._close_confirmed is True          # pas de question sur le travail en cours


def test_deleted_page_keeps_the_viewer_open_for_work_on_another_page():
    st = document("p01", "p02", "p03")
    viewer = Viewer(st, 1)
    viewer._shapes_by_page[2] = ["rectangle"]
    del st.images_data[1]
    viewer._sync_with_document()
    assert viewer.events == [("page", 1, False)]    # travail du canvas jamais rangé sur la voisine
    assert st.images_data[viewer.current_idx]["orig_name"] == "p03"
    assert viewer._shapes_by_page[1] == ["rectangle"]
    assert viewer._close_confirmed is False


def test_deleted_page_closes_the_viewer_when_only_it_had_work():
    st = document("p01", "p02")
    viewer = Viewer(st, 1)
    viewer._shapes_by_page[1] = ["rectangle"]
    del st.images_data[1]
    viewer._sync_with_document()
    assert viewer.events == ["fermée"]


def test_pending_transparency_on_another_page_keeps_the_viewer_open():
    st = document("p01", "p02")
    viewer = Viewer(st, 1)
    viewer._transp_work_img_by_page[0] = object()
    del st.images_data[1]
    viewer._sync_with_document()
    assert viewer.events == [("page", 0, False)]


def test_last_page_deleted_goes_to_the_previous_one():
    st = document("p01", "p02", "p03")
    viewer = Viewer(st, 2)
    viewer._crop_by_page[0] = (0, 0, 1, 1)
    del st.images_data[2]
    viewer._sync_with_document()
    assert st.images_data[viewer.current_idx]["orig_name"] == "p02"


def test_next_page_that_is_not_an_image_is_skipped():
    st = document("p01", "p02", "notes", "p03")
    st.images_data[2]["is_image"] = False
    viewer = Viewer(st, 1)
    viewer._text_blocks_by_page[3] = ["texte"]
    del st.images_data[1]                           # « notes » prend la position 1
    viewer._sync_with_document()
    assert st.images_data[viewer.current_idx]["orig_name"] == "p01"


def test_viewer_reading_a_macro_still_closes():
    st = document("p01", "p02")
    viewer = Viewer(st, 1)
    viewer._macro_reading = True
    viewer._shapes_by_page[0] = ["rectangle"]
    del st.images_data[1]
    viewer._sync_with_document()
    assert viewer.events == ["fermée"]


def test_macro_recording_stops_when_its_page_is_removed():
    st = document("p01", "p02")
    viewer = Viewer(st, 1)
    viewer._macro_recording = True
    viewer._macro_record_dialog = types.SimpleNamespace(close=lambda: viewer.events.append("dialogue fermé"))
    viewer._shapes_by_page[0] = ["rectangle"]
    del st.images_data[1]
    viewer._sync_with_document()
    assert viewer.events == ["enregistrement abandonné", "dialogue fermé", ("page", 0, False)]


ShowPosition = ast_loader.load_methods("image_viewer_qt.py", "ImageViewer", {"_show_image_position"})


class Changing(ShowPosition):
    """Rend la liste des rangements demandés et la page à ce moment-là."""
    page_mode = "single"

    def __init__(self):
        self.current_idx = 0
        self.saved_on = []
        self._canvas = types.SimpleNamespace(clear_clone_source=lambda: None)
        self.display_image = lambda keep_crop_rect=False: None

    def __getattr__(self, name):
        if name.startswith("_save_"):
            return lambda: self.saved_on.append(self.current_idx)
        if name.startswith(("_restore_", "_reset_", "_refresh_", "_sync_", "_check_")):
            return lambda *args: None
        raise AttributeError(name)


def test_changing_page_stores_the_work_of_the_page_left():
    viewer = Changing()
    viewer._show_image_position([0, 1], 1)
    assert viewer.saved_on == [0] * 5 and viewer.current_idx == 1


def test_leaving_a_removed_page_stores_nothing():
    viewer = Changing()
    viewer._show_image_position([0, 1], 1, save_work=False)
    assert viewer.saved_on == [] and viewer.current_idx == 1


def tool_points_viewer(size_of):
    Points = ast_loader.load_methods("image_viewer_qt.py", "ImageViewer",
                                     {"_forget_tool_points_if_page_resized"},
                                     class_attrs=("_tool_points_page",), page_size=size_of)
    viewer = Points()
    viewer.current_idx = 0
    viewer.cleared = 0

    def clear():
        viewer.cleared += 1

    viewer._canvas = types.SimpleNamespace(clear_clone_source=clear, _blur_last_point_img=(1, 1),
                                           _blur_line_mode=True, _blur_line_end=(2, 2))
    return viewer


def test_tool_points_are_forgotten_when_the_page_changes_size():
    st = document("p01")
    viewer = tool_points_viewer(lambda data: (len(data), 1))
    viewer._forget_tool_points_if_page_resized(st)
    assert viewer.cleared == 0                      # premier affichage
    st.images_data[0]["bytes"] = b"plus long"
    viewer._forget_tool_points_if_page_resized(st)
    assert viewer.cleared == 1 and viewer._canvas._blur_last_point_img is None
    assert viewer._canvas._blur_line_mode is False and viewer._canvas._blur_line_end is None


def test_tool_points_stay_when_the_size_does_not_change():
    st = document("p01")
    viewer = tool_points_viewer(lambda data: (10, 10))
    viewer._forget_tool_points_if_page_resized(st)
    viewer._forget_tool_points_if_page_resized(st)
    assert viewer.cleared == 0 and viewer._canvas._blur_last_point_img == (1, 1)


def test_going_to_a_page_of_another_size_is_not_a_change_of_size():
    st = document("p01", "autre page")
    viewer = tool_points_viewer(lambda data: (len(data), 1))
    viewer._forget_tool_points_if_page_resized(st)
    viewer.current_idx = 1
    viewer._forget_tool_points_if_page_resized(st)
    assert viewer.cleared == 0


def test_unreadable_page_forgets_nothing():
    st = document("p01")

    def unreadable(data):
        raise OSError("page illisible")

    viewer = tool_points_viewer(unreadable)
    viewer._forget_tool_points_if_page_resized(st)
    viewer._forget_tool_points_if_page_resized(st)
    assert viewer.cleared == 0


def test_page_moved_by_an_insertion_is_followed_and_shown_again():
    st = document("p01", "p02")
    viewer = Viewer(st, 1)
    st.images_data.insert(0, page("p00"))
    viewer._sync_with_document()
    assert viewer.events == [("affichée", 2)]
    assert st.images_data[viewer.current_idx]["orig_name"] == "p02"


def test_page_that_takes_the_old_position_is_never_the_one_shown():
    st = document("p01", "p02", "p03")
    viewer = Viewer(st, 2)
    del st.images_data[0]                           # p03 passe en position 1
    viewer._sync_with_document()
    assert st.images_data[viewer.current_idx]["orig_name"] == "p03"


def test_page_moved_while_a_tool_is_active_is_not_redisplayed():
    """Un outil actif impose la page simple, déjà correcte : réafficher
    perdrait le tracé en cours."""
    st = document("p01", "p02")
    viewer = Viewer(st, 1, tool="crop")
    st.images_data.insert(0, page("p00"))
    viewer._sync_with_document()
    assert viewer.events == ["nom"]
    assert viewer.current_idx == 2


def test_page_left_in_place_only_updates_its_name():
    st = document("p01", "p02")
    viewer = Viewer(st, 1)
    st.images_data[1]["orig_name"] = "renommée"
    st.images_data.append(page("p03"))
    viewer._sync_with_document()
    assert viewer.events == ["nom"]


# ---------------------------------------------------------------------------
# sync_image_viewers_with_document
# ---------------------------------------------------------------------------

def _sync(refs):
    ns = ast_loader.load("image_viewer_qt.py", {"sync_image_viewers_with_document"},
                         image_viewer_refs=refs)
    return ns["sync_image_viewers_with_document"]


def test_only_the_viewers_of_that_document_are_told():
    mine, other = document("p01"), document("q01")
    seen = []
    on_mine = types.SimpleNamespace(_doc_state=mine, _closed=False,
                                    _sync_with_document=lambda: seen.append("mine"))
    on_other = types.SimpleNamespace(_doc_state=other, _closed=False,
                                     _sync_with_document=lambda: seen.append("other"))
    closed = types.SimpleNamespace(_doc_state=mine, _closed=True,
                                   _sync_with_document=lambda: seen.append("closed"))
    _sync([on_mine, on_other, closed])(mine)
    assert seen == ["mine"]


def test_destroyed_viewer_is_forgotten():
    st = document("p01")

    def destroyed():
        raise RuntimeError("Internal C++ object already deleted")
    gone = types.SimpleNamespace(_doc_state=st, _closed=False, _sync_with_document=destroyed)
    refs = [gone]
    _sync(refs)(st)
    assert refs == []
