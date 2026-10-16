"""La sélection des pages (AppState.selected_indices) suit ses pages.

Vue de l'extérieur, c'est un ensemble de positions dans images_data ; en
interne, elle retient les pages elles-mêmes. Rangée par position, elle
désignait d'autres pages dès qu'une page était insérée ou supprimée avant
elles (page convertie, image importée pendant une opération…), et la
commande suivante s'appliquait à ces autres pages.
"""
from modules.qt.state import AppState


def make_entry(name):
    return {"orig_name": name, "bytes": name.encode(), "extension": ".jpg", "is_image": True}


def state_with(*names):
    st = AppState()
    st.images_data = [make_entry(n) for n in names]
    return st


def test_behaves_like_a_set_of_positions():
    st = state_with("a", "b", "c")
    sel = st.selected_indices
    assert not sel and len(sel) == 0
    sel.add(2)
    sel.add(0)
    assert sel and len(sel) == 2
    assert 0 in sel and 1 not in sel and 2 in sel
    assert list(sel) == [0, 2]            # positions croissantes
    assert sorted(sel, reverse=True) == [2, 0]
    assert max(sel) == 2
    sel.discard(0)
    assert list(sel) == [2]
    sel.clear()
    assert not sel


def test_assignment_replaces_the_selection():
    st = state_with("a", "b", "c")
    st.selected_indices = {0, 1}
    st.selected_indices = {2}
    assert list(st.selected_indices) == [2]


def test_selection_follows_its_page_when_a_page_is_inserted_before():
    st = state_with("a", "b", "c")
    st.selected_indices = {1}                           # page "b"
    st.images_data.insert(0, make_entry("new"))
    assert list(st.selected_indices) == [2]
    assert st.images_data[2]["orig_name"] == "b"


def test_deleted_page_leaves_the_selection():
    st = state_with("a", "b", "c")
    st.selected_indices = {1, 2}
    del st.images_data[1]
    assert list(st.selected_indices) == [1]
    assert st.images_data[1]["orig_name"] == "c"


def test_selection_survives_a_new_list_and_a_reorder():
    st = state_with("a", "b", "c")
    st.selected_indices = {0}                           # page "a"
    st.images_data = list(reversed(st.images_data))     # nouvelle liste, ordre inversé
    assert list(st.selected_indices) == [2]


def test_out_of_range_position_is_ignored():
    st = state_with("a")
    st.selected_indices.add(5)
    st.selected_indices = {3, 0}
    assert list(st.selected_indices) == [0]


def test_set_operations_return_plain_positions():
    st = state_with("a", "b", "c")
    st.selected_indices = {1}
    inverted = set(range(3)) - st.selected_indices      # « Inverser la sélection »
    assert inverted == {0, 2} and type(inverted) is set
    st.selected_indices = inverted
    assert list(st.selected_indices) == [0, 2]
