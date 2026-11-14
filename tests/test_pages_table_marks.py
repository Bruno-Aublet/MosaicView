"""Type de page et double page depuis le tableau Pages de l'onglet
Métadonnées (tabs_qt.MetadataTab) : la modification vaut pour les pages que
l'utilisateur voyait.

Une ligne désigne sa page par la position <Page Image="N"> du modèle qui
l'affiche. Pendant une reconstruction du tableau (thread _PagesModelBuilder,
lancée à chaque modification du document), ou une fois le modèle remplacé
(choix de la liste livré en différé, menu resté ouvert), ces positions
peuvent désigner d'autres pages : la modification est alors sans effet
(MetadataTab._pages_table_is_current).

Méthodes extraites par tests/ast_loader.py ; tableau et modèle remplacés,
vraies fonctions du ComicInfo.xml.
"""
import io
import os
import types

from PIL import Image

import ast_loader
from modules.qt.comic_info import sync_comic_metadata
from modules.qt.state import AppState


class FakeModel:
    """_PagesTableModel : une ligne par position <Page Image="N">."""

    def __init__(self, positions):
        self.positions = list(positions)

    def rowCount(self):
        return len(self.positions)

    def image_index(self, row):
        return self.positions[row]


class FakeTable:
    def __init__(self, model, selected_rows=()):
        self._model = model
        self.selected_rows = set(selected_rows)

    def model(self):
        return self._model

    def selectionModel(self):
        rows = [types.SimpleNamespace(row=lambda r=r: r) for r in sorted(self.selected_rows)]
        return types.SimpleNamespace(selectedRows=lambda: rows)


Methods = ast_loader.load_methods(
    "tabs_qt.py", "MetadataTab",
    {"_pages_table_is_current", "_page_types_editable", "_selected_page_positions",
     "_apply_page_marks", "_on_page_type_chosen", "_on_double_page_clicked"},
    _PagesTableModel=FakeModel)


class Panel:
    def __init__(self):
        self.marked = []

    def _set_page_marks(self, entries, **marks):
        self.marked.append(([e["orig_name"] for e in entries], marks))

    def parent(self):
        return None


class Tab(Methods):
    def __init__(self, state, model):
        self._state = state
        self._pages_table = FakeTable(model)
        self._pages_builder = None
        self._panel = Panel()
        self._sender = model

    def parent(self):
        return self._panel

    def sender(self):
        return self._sender


def page(name):
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), "white").save(buf, "PNG")
    return {"orig_name": name, "bytes": buf.getvalue(), "extension": os.path.splitext(name)[1],
            "is_image": True, "is_dir": False}


def document():
    st = AppState()
    xml = (b'<ComicInfo><Title>Aaaa</Title><Pages><Page Image="0"/><Page Image="1"/>'
           b'<Page Image="2"/></Pages></ComicInfo>')
    st.images_data = [{"orig_name": "ComicInfo.xml", "bytes": xml, "extension": ".xml",
                       "is_image": False, "is_dir": False},
                      page("01.png"), page("02.png"), page("03.png")]
    sync_comic_metadata(st, force=True)
    return st


def test_type_chosen_applies_to_the_page_of_the_row():
    tab = Tab(document(), FakeModel([0, 1, 2]))
    tab._on_page_type_chosen(1, "Story")
    assert tab._panel.marked == [(["02.png"], {"page_type": "Story"})]


def test_type_chosen_applies_to_every_selected_row():
    tab = Tab(document(), FakeModel([0, 1, 2]))
    tab._pages_table.selected_rows = {0, 2}
    tab._on_page_type_chosen(2, "Story")
    assert tab._panel.marked == [(["01.png", "03.png"], {"page_type": "Story"})]


def test_choice_from_a_replaced_model_does_nothing():
    """Choix livré en différé après une reconstruction du tableau : il vient
    de l'ancien modèle, dont les positions ne décrivent plus le document."""
    old = FakeModel([0, 1, 2])
    tab = Tab(document(), old)
    tab._pages_table._model = FakeModel([0, 1, 2])
    tab._on_page_type_chosen(1, "Story")
    assert tab._panel.marked == []


def test_nothing_is_changed_while_the_table_is_rebuilt():
    """Pages réordonnées : le tableau affiché décrit encore l'ancien ordre
    jusqu'à la fin de sa reconstruction."""
    st = document()
    tab = Tab(st, FakeModel([0, 1, 2]))
    st.images_data[1], st.images_data[3] = st.images_data[3], st.images_data[1]
    tab._pages_builder = object()
    tab._on_page_type_chosen(0, "FrontCover")
    tab._on_double_page_clicked(0)
    assert tab._panel.marked == []
    assert not tab._page_types_editable()


def test_marks_follow_the_rebuilt_table():
    st = document()
    tab = Tab(st, FakeModel([0, 1, 2]))
    st.images_data[1], st.images_data[3] = st.images_data[3], st.images_data[1]
    rebuilt = FakeModel([0, 1, 2])
    tab._pages_table._model = tab._sender = rebuilt
    tab._on_page_type_chosen(0, "FrontCover")
    assert tab._panel.marked == [(["03.png"], {"page_type": "FrontCover"})]


def test_menu_answer_after_the_model_changed_does_nothing():
    """Menu du clic droit : positions lues à son ouverture, appliquées au
    choix d'une entrée ; le modèle a pu être remplacé entre-temps."""
    old = FakeModel([0, 1, 2])
    tab = Tab(document(), old)
    tab._pages_table._model = FakeModel([0, 1, 2])
    tab._apply_page_marks([1], old, page_type="Story")
    assert tab._panel.marked == []


def test_double_page_on_the_current_table():
    tab = Tab(document(), FakeModel([0, 1, 2]))
    tab._on_double_page_clicked(2)
    assert tab._panel.marked == [(["03.png"], {"double_page": True})]


def test_nothing_during_an_operation():
    st = document()
    tab = Tab(st, FakeModel([0, 1, 2]))
    token = object()
    st.begin_operation(token)
    tab._on_page_type_chosen(1, "Story")
    st.end_operation(token)
    assert tab._panel.marked == []
