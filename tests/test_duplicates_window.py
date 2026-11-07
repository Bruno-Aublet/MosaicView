"""Fenêtre des doublons (duplicate_detection_qt).

Vérifié :
- la suppression vise les pages cochées elles-mêmes, jamais une position retenue :
  document changé pendant la confirmation non modale, seconde confirmation du
  même clic ;
- une seule fenêtre par panneau : la fenêtre déjà ouverte revient au premier plan ;
- bouton Supprimer grisé tant que sa question est ouverte ;
- la liste n'est reconstruite que si ses doublons ont changé, garde alors sa position
  de défilement et ne laisse aucun espace au-dessus des groupes ;
- un & d'un nom de page reste affiché tel quel.

Le module importe PySide6 : les fonctions sont extraites par AST
(tests/ast_loader.py).
"""
import sys
import types

from modules.qt.state import AppState
from modules.qt.undo_redo import save_state_data

import ast_loader


def page(name):
    return {"orig_name": name, "bytes": name.encode(), "extension": ".jpg", "is_image": True}


def document(*names):
    state = AppState()
    state.images_data = [page(n) for n in names]
    state.current_file = "Aaaa.cbz"
    save_state_data(state, force=True)
    return state


def delete(state, pages):
    ns = ast_loader.load("duplicate_detection_qt.py", {"delete_pages", "delete_entries_by_index"})
    ns["delete_pages"](state, pages, lambda force=False: save_state_data(state, force=force),
                       lambda: None)


def names(state):
    return [e["orig_name"] for e in state.images_data]


def test_checked_page_is_deleted_even_if_it_moved_during_the_confirmation():
    state = document("a", "b", "c", "d")
    checked = [state.images_data[2]]              # « c », en position 2 au clic
    state.images_data.pop(0)                      # une page avant elle retirée entre-temps
    delete(state, checked)
    assert names(state) == ["b", "d"]


def test_second_confirmation_of_the_same_click_deletes_nothing_more():
    state = document("a", "b", "c")
    checked = [state.images_data[1]]
    delete(state, checked)
    steps = len(state.history)
    delete(state, checked)
    assert names(state) == ["a", "c"]
    assert len(state.history) == steps            # pas d'étape vide dans l'historique


def test_all_checked_pages_are_deleted():
    state = document("a", "b", "c", "d")
    delete(state, [state.images_data[0], state.images_data[3]])
    assert names(state) == ["b", "c"]


def test_open_window_comes_back_instead_of_a_second_one(monkeypatch):
    monkeypatch.setitem(sys.modules, "shiboken6", types.SimpleNamespace(isValid=lambda w: True))
    opened = []
    monkeypatch.setitem(sys.modules, "modules.qt.duplicate_detection_qt", types.SimpleNamespace(
        show_duplicates_window=lambda *a: opened.append(a) or Window()))

    class Window:
        visible, raised = True, 0

        def isVisible(self):
            return self.visible

        def raise_(self):
            self.raised += 1

        def activateWindow(self):
            pass

    class Panel(ast_loader.load_methods("panel_widget.py", "PanelWidget",
                                        {"_show_duplicates_window"})):
        _duplicates_window = None
        _state, save_state, _refresh_tabs = None, None, None
        _canvas = types.SimpleNamespace(render_mosaic=None)

    panel = Panel()
    panel._show_duplicates_window()
    first = panel._duplicates_window
    panel._show_duplicates_window()
    assert len(opened) == 1 and panel._duplicates_window is first and first.raised == 1
    first.visible = False                         # fermée : la suivante est une nouvelle fenêtre
    panel._show_duplicates_window()
    assert len(opened) == 2 and panel._duplicates_window is not first


class FakeLayout:
    """Layout réduit à ce que la reconstruction utilise. Un espace est un élément
    sans widget, comme dans un QVBoxLayout."""

    def __init__(self):
        self.items = []

    def count(self):
        return len(self.items)

    def takeAt(self, index):
        widget = self.items.pop(index)
        return types.SimpleNamespace(widget=lambda: widget)

    def addWidget(self, widget):
        self.items.append(widget)

    def addStretch(self, factor):
        self.items.append(None)


class FakeScrollBar:
    position = 0

    def value(self):
        return self.position

    def setValue(self, value):
        self.position = value


GROUPS_KEY = ast_loader.load("duplicate_detection_qt.py", {"_groups_key"})["_groups_key"]


def list_window(groups):
    """Fenêtre réduite à la reconstruction de sa liste. Elle remonte en haut pendant
    une reconstruction, comme la vraie ; sa minuterie ne part que si le test le dit."""
    bar = FakeScrollBar()

    class Window(ast_loader.load_methods(
            "duplicate_detection_qt.py", "_DuplicatesWindow",
            {"_rebuild_list", "_on_scroll_range_changed", "_forget_pending_scroll", "refresh"},
            QTimer=types.SimpleNamespace(singleShot=lambda delay, fn: None),
            get_duplicate_groups=lambda state: groups,
            _groups_key=GROUPS_KEY,
            _has_subdirectory_structure=lambda state: False)):
        _state = None
        _checkboxes = []
        _shown_key = None
        _pending_scroll = None
        _scroll = types.SimpleNamespace(setVisible=lambda v: None, verticalScrollBar=lambda: bar)
        _btn_delete = _lbl_empty = types.SimpleNamespace(setVisible=lambda v: None)
        _list_layout = FakeLayout()
        _update_delete_button_state = staticmethod(lambda: None)
        built = 0

        def _retranslate(self):
            pass

        def _build_group_widget(self, number, entries, show_path):
            bar.position = 0
            self.built += 1
            return types.SimpleNamespace(setParent=lambda parent: None, group="groupe")

    return Window(), bar


def test_rebuilding_the_list_leaves_no_space_above_the_groups():
    window, _bar = list_window([[(0, {})]])
    for _i in range(5):
        window._rebuild_list()
    items = window._list_layout.items
    assert len(items) == 2 and items[0].group == "groupe" and items[1] is None


def test_click_in_the_mosaic_does_not_rebuild_an_unchanged_list():
    groups = [[(0, page("a")), (1, page("b"))]]
    window, _bar = list_window(groups)
    window._rebuild_list()
    window.refresh()
    window.refresh()
    assert window.built == 1


def test_list_is_rebuilt_when_its_duplicates_change():
    groups = [[(0, page("a")), (1, page("b"))]]
    window, _bar = list_window(groups)
    window._rebuild_list()
    groups[0][0][1]["orig_name"] = "c"
    window.refresh()
    assert window.built == 2


def test_rebuild_keeps_the_scroll_position():
    """Hauteur connue en plusieurs étapes, d'abord nulle. Une seconde reconstruction,
    qui lit déjà 0, ne remplace pas la position à retrouver."""
    window, bar = list_window([[(0, page("a"))]])
    bar.position = 300
    window._rebuild_list()
    window._rebuild_list()
    window._on_scroll_range_changed(0, 0)
    assert bar.position == 0
    window._on_scroll_range_changed(0, 250)
    assert bar.position == 250
    window._on_scroll_range_changed(0, 500)
    assert bar.position == 300


def test_scroll_position_is_forgotten_once_the_list_is_settled():
    """Un redimensionnement de la fenêtre, plus tard, ne la rétablit pas."""
    window, bar = list_window([[(0, page("a"))]])
    bar.position = 300
    window._rebuild_list()
    window._forget_pending_scroll()
    bar.position = 10
    window._on_scroll_range_changed(0, 800)
    assert bar.position == 10


class FakeConfirm:
    opened = []

    def __init__(self, parent, count, size):
        self.finished = types.SimpleNamespace(connect=lambda fn: setattr(self, "on_finished", fn))
        self.accepted = types.SimpleNamespace(connect=lambda fn: setattr(self, "on_accepted", fn))
        FakeConfirm.opened.append(self)

    def show_nonmodal(self):
        pass


def delete_window(monkeypatch):
    FakeConfirm.opened = []
    monkeypatch.setitem(sys.modules, "modules.qt.utils",
                        types.SimpleNamespace(format_file_size=str))
    monkeypatch.setitem(sys.modules, "modules.qt.file_close_qt",
                        types.SimpleNamespace(DeleteConfirmDialog=FakeConfirm))

    class Window(ast_loader.load_methods(
            "duplicate_detection_qt.py", "_DuplicatesWindow",
            {"_on_delete_clicked", "_on_delete_confirm_closed", "_update_delete_button_state"},
            delete_pages=lambda *a: None, get_duplicate_groups=lambda state: ["groupe"])):
        _delete_confirm = None
        enabled = None
        refreshed = 0

        def refresh(self):
            self.refreshed += 1

        _btn_delete = types.SimpleNamespace(setEnabled=lambda v: None)
        _state = _save_state = _render_mosaic = _refresh_tabs = None

    window = Window()
    window._btn_delete = types.SimpleNamespace(setEnabled=lambda v: setattr(window, "enabled", v))
    checked = types.SimpleNamespace(isChecked=lambda: True)
    window._checkboxes = [(checked, 0, page("a"))]
    return window


def test_delete_button_is_blocked_while_the_question_is_open(monkeypatch):
    window = delete_window(monkeypatch)
    window._on_delete_clicked()
    assert window.enabled is False
    window._on_delete_clicked()                   # clic parti avant le grisage
    assert len(FakeConfirm.opened) == 1


def test_deletion_does_not_force_a_second_rebuild(monkeypatch):
    """La mosaïque redessinée a déjà mis la liste à jour : une seconde reconstruction
    lirait une position déjà retombée à zéro."""
    window = delete_window(monkeypatch)
    window._on_delete_clicked()
    FakeConfirm.opened[0].on_accepted()
    assert window.refreshed == 1


def test_delete_button_comes_back_when_the_question_closes(monkeypatch):
    window = delete_window(monkeypatch)
    window._on_delete_clicked()
    FakeConfirm.opened[0].on_finished(0)
    assert window.enabled is True
    window._on_delete_clicked()
    assert len(FakeConfirm.opened) == 2


def test_ampersand_of_a_page_name_is_shown():
    text = ast_loader.load("duplicate_detection_qt.py", {"_checkbox_text"})["_checkbox_text"]
    assert text("A&B.jpg") == "A&&B.jpg"
    assert text("Aaaa.jpg") == "Aaaa.jpg"
