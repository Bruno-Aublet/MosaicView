"""Déplacement de pages d'un panneau à l'autre par glisser-déposer
(panel_widget.PanelWidget._on_inter_panel_drop).

Vérifié :
- les pages déplacées quittent le panneau source et arrivent, dans le même
  ordre, à la position du dépôt dans la cible, en copies indépendantes ;
  sélectionnées dans la cible ; point d'historique dans les deux panneaux ;
- refus complet (rien ne bouge) pendant une opération en arrière-plan sur
  l'un des deux documents, ou pendant le chargement d'un fichier dans la cible ;
- un ComicInfo.xml déposé dans une BD qui en a déjà un devient
  ComicInfo_0002.xml (une BD n'a qu'un ComicInfo.xml) ;
- position de dépôt hors limites ramenée à la fin ; source vidée sans fichier :
  non marquée modifiée ;
- les attributs <Page> du ComicInfo.xml (couverture, double page) suivent chaque
  page déplacée, et ne tombent jamais sur une autre page.

panel_widget.py importe PySide6 : la méthode est extraite par AST
(tests/ast_loader.load_methods), les panneaux sont remplacés.
"""
import types

from modules.qt.comic_info import sync_comic_metadata
from modules.qt.state import AppState
from modules.qt.undo_redo import save_state_data

import ast_loader

Base = ast_loader.load_methods("panel_widget.py", "PanelWidget", {"_on_inter_panel_drop"})


def page(name, data=None):
    return {"orig_name": name, "bytes": data or name.encode(), "extension": ".png",
            "is_image": True}


def comic_info():
    return {"orig_name": "ComicInfo.xml", "bytes": b"<ComicInfo/>", "extension": ".xml",
            "is_image": False}


class Panel(Base):
    def __init__(self, window, *entries, loading=False):
        self._main_window = window
        self._state = AppState()
        self._state.images_data = list(entries)
        self._state.current_file = "Aaaa.cbz"
        save_state_data(self._state, force=True)
        self._canvas = types.SimpleNamespace(render_mosaic=lambda: None)
        self._metadata_tab = types.SimpleNamespace(update_pages=lambda pages: None)
        self._loading = loading
        self.saves = 0
        window.panels.append(self)

    def _replacing_load_in_progress(self):
        return self._loading

    def _sync_comic_metadata(self):
        return sync_comic_metadata(self._state)

    def _renumber_no_save(self, on_done=None):
        on_done()

    def save_state(self, force=False):
        self.saves += 1
        save_state_data(self._state, force=force)

    def names(self):
        return [e["orig_name"] for e in self._state.images_data]


def two_panels(source_entries, target_entries, loading=False):
    window = types.SimpleNamespace(panels=[])
    window._all_panels = lambda: list(window.panels)
    return Panel(window, *source_entries), Panel(window, *target_entries, loading=loading)


def drop(source, target, indices, position):
    target._on_inter_panel_drop(indices, position, str(id(source._canvas)))


def test_moved_pages_leave_the_source_and_arrive_in_order():
    source, target = two_panels([page("a1"), page("a2"), page("a3")], [page("b1"), page("b2")])
    moved = [source._state.images_data[0], source._state.images_data[2]]
    drop(source, target, [0, 2], 1)
    assert source.names() == ["a2"]
    assert target.names() == ["b1", "a1", "a3", "b2"]
    arrived = target._state.images_data[1:3]
    assert all(c is not m for c, m in zip(arrived, moved))      # copies indépendantes
    assert [c["bytes"] for c in arrived] == [b"a1", b"a3"]
    assert target._state.selected_indices == {1, 2}
    assert source.saves == 1 and target.saves == 1
    assert source._state.modified and target._state.modified


def test_nothing_moves_during_an_operation_on_the_source():
    source, target = two_panels([page("a1")], [page("b1")])
    source._state.begin_operation(object())
    drop(source, target, [0], 0)
    assert source.names() == ["a1"] and target.names() == ["b1"]
    assert source.saves == target.saves == 0


def test_nothing_moves_during_an_operation_on_the_target():
    source, target = two_panels([page("a1")], [page("b1")])
    target._state.begin_operation(object())
    drop(source, target, [0], 0)
    assert source.names() == ["a1"] and target.names() == ["b1"]


def test_nothing_moves_while_the_target_is_loading_a_file():
    source, target = two_panels([page("a1")], [page("b1")], loading=True)
    drop(source, target, [0], 0)
    assert source.names() == ["a1"] and target.names() == ["b1"]


def test_unknown_source_moves_nothing():
    source, target = two_panels([page("a1")], [page("b1")])
    target._on_inter_panel_drop([0], 0, "inconnu")
    assert source.names() == ["a1"] and target.names() == ["b1"]


def test_second_comicinfo_arrives_as_an_annex():
    source, target = two_panels([comic_info(), page("a1")], [comic_info(), page("b1")])
    drop(source, target, [0], 2)
    assert target.names() == ["ComicInfo.xml", "b1", "ComicInfo_0002.xml"]
    assert source.names() == ["a1"]


def test_drop_position_beyond_the_end_appends():
    source, target = two_panels([page("a1")], [page("b1")])
    drop(source, target, [0], 99)
    assert target.names() == ["b1", "a1"]


def test_source_emptied_without_file_is_not_modified():
    source, target = two_panels([page("a1")], [page("b1")])
    source._state.current_file = None
    drop(source, target, [0], 0)
    assert source.names() == []
    assert source._state.modified is False


def test_selection_is_set_before_the_mosaic_is_drawn():
    """La mosaïque ne lit la sélection qu'en se dessinant, pendant la renumérotation :
    posée après, les pages encadrées ne seraient pas celles que visent les commandes."""
    source, target = two_panels([page("a1"), page("a2")], [page("b1"), page("b2"), page("b3")])
    target._state.selected_indices = {2}
    seen = []

    def renumber_then_draw(on_done=None):
        seen.append(sorted(target._state.selected_indices))
        on_done()
    target._renumber_no_save = renumber_then_draw
    drop(source, target, [0, 1], 1)
    assert seen == [[1, 2]]
    assert sorted(target._state.selected_indices) == [1, 2]


def comic_info_with_pages():
    """Page 0 : couverture ; page 2 : double page."""
    return {"orig_name": "ComicInfo.xml", "extension": ".xml", "is_image": False,
            "bytes": b'<ComicInfo><Pages><Page Image="0" Type="FrontCover"/><Page Image="1"/>'
                     b'<Page Image="2" DoublePage="true"/><Page Image="3"/></Pages></ComicInfo>'}


def marks(panel):
    """Pour chaque page, dans l'ordre : son nom et ses marquages."""
    st = panel._state
    pages = [e for e in st.images_data if e["is_image"]]
    return [(e["orig_name"], {k: v for k, v in meta.items()
                              if k in ("Type", "DoublePage")})
            for e, meta in zip(pages, st.comic_metadata["pages"])]


def marked_panels(target_entries):
    source_entries = [comic_info_with_pages()] + [page(f"a{i}") for i in range(4)]
    source, target = two_panels(source_entries, target_entries)
    sync_comic_metadata(source._state)
    sync_comic_metadata(target._state)
    return source, target


def test_comicinfo_with_some_pages_keeps_each_page_marks():
    """Attributs lus par position dans le XML reçu : a2 deviendrait la couverture."""
    source, target = marked_panels([])
    drop(source, target, [0, 3, 4], 0)
    assert marks(target) == [("a2", {"DoublePage": "true"}), ("a3", {})]


def test_page_alone_keeps_its_marks_and_the_target_keeps_its_own():
    source, target = marked_panels([comic_info_with_pages()] + [page(f"b{i}") for i in range(4)])
    drop(source, target, [3], 2)
    assert marks(target) == [("b0", {"Type": "FrontCover"}), ("a2", {"DoublePage": "true"}),
                             ("b1", {}), ("b2", {"DoublePage": "true"}), ("b3", {})]


def test_cover_mark_follows_its_page_even_if_the_target_has_a_cover():
    source, target = marked_panels([comic_info_with_pages()] + [page(f"b{i}") for i in range(4)])
    drop(source, target, [1], 5)
    assert marks(target)[-1] == ("a0", {"Type": "FrontCover"})
    assert marks(target)[0] == ("b0", {"Type": "FrontCover"})


def test_comicinfo_alone_marks_no_page_of_the_target():
    source, target = marked_panels([page("b0"), page("b1"), page("b2")])
    drop(source, target, [0], 0)
    assert marks(target) == [("b0", {}), ("b1", {}), ("b2", {})]
    assert source._state.comic_metadata is None
