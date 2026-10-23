"""Visionneuse ouverte pendant que la mosaïque modifie les pages du document
(image_viewer_qt.ImageViewer._sync_with_document, sync_image_viewers_with_document).

Vérifié :
- page affichée supprimée (ou déplacée vers l'autre panneau, ou Annuler de son
  ajout) : la visionneuse se ferme sans demander, son travail non validé
  visait une page qui n'existe plus — l'appliquer à la page qui a pris sa
  place la modifierait à tort ;
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
                               {"current_idx", "_current_page_present", "_sync_with_document"})


def page(name):
    return {"orig_name": name, "bytes": name.encode(), "extension": ".png", "is_image": True}


class Viewer(Base):
    def __init__(self, state, index, tool=None):
        self._doc_state = state
        self.current_idx = index
        self._toolbar = types.SimpleNamespace(active_tool=tool)
        self._close_confirmed = False
        self._closed = False
        self.events = []

    def close(self):
        self.events.append("fermée")
        self._closed = True

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
