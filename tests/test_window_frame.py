"""Cadre Windows des fenêtres à barre de titre dessinée par Qt
(window_frame_qt.py) : bords qui redimensionnent selon les axes libres de la
fenêtre, et boutons de la barre selon le type de la fenêtre.

Fenêtre de 400 × 300 pixels, bords de 5 pixels.
"""
from modules.qt.window_frame_qt import (
    HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT, HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT,
    HTTOPRIGHT, caption_buttons, hit_test_code,
)

W, H, B = 400, 300, 5


def code(x, y, horizontal=True, vertical=True):
    return hit_test_code(x, y, W, H, B, horizontal, vertical)


def test_resizable_window_has_all_edges_and_corners():
    assert code(0, 0) == HTTOPLEFT
    assert code(W - 1, 0) == HTTOPRIGHT
    assert code(0, H - 1) == HTBOTTOMLEFT
    assert code(W - 1, H - 1) == HTBOTTOMRIGHT
    assert code(200, 0) == HTTOP
    assert code(200, H - 1) == HTBOTTOM
    assert code(0, 150) == HTLEFT
    assert code(W - 1, 150) == HTRIGHT
    assert code(200, 150) is None
    assert code(B, B) is None


def test_fixed_size_window_has_no_resizing_edge():
    for x, y in ((0, 0), (W - 1, H - 1), (200, 0), (0, 150), (W - 1, 150), (200, H - 1)):
        assert code(x, y, horizontal=False, vertical=False) is None


def test_fixed_width_window_resizes_only_vertically():
    # Bords gauche et droit inertes ; un coin ne garde que le haut ou le bas
    assert code(0, 150, horizontal=False) is None
    assert code(W - 1, 150, horizontal=False) is None
    assert code(0, 0, horizontal=False) == HTTOP
    assert code(W - 1, H - 1, horizontal=False) == HTBOTTOM
    assert code(200, H - 1, horizontal=False) == HTBOTTOM


def test_fixed_height_window_resizes_only_horizontally():
    assert code(200, 0, vertical=False) is None
    assert code(200, H - 1, vertical=False) is None
    assert code(0, 0, vertical=False) == HTLEFT
    assert code(W - 1, H - 1, vertical=False) == HTRIGHT


def buttons(window_type=True, min_hint=False, max_hint=False, parent=True,
            fixed_w=False, fixed_h=False):
    return caption_buttons(window_type, min_hint, max_hint, parent, fixed_w, fixed_h)


def test_dialog_type_keeps_only_close():
    # Type Qt.Dialog : Windows n'affichait que Fermer
    assert buttons(window_type=False, parent=False) == (False, False)
    assert buttons(window_type=False) == (False, False)


def test_window_type_without_parent_gets_minimize_and_maximize():
    assert buttons(parent=False) == (True, True)


def test_window_with_parent_never_minimizes():
    # Réduite, une fenêtre qui a un parent devient une petite barre au-dessus
    # de la barre des tâches, sans bouton dans celle-ci
    assert buttons() == (False, True)
    assert buttons(window_type=False, min_hint=True) == (False, False)


def test_fixed_dimension_removes_maximize():
    assert buttons(parent=False, fixed_w=True) == (True, False)
    assert buttons(parent=False, fixed_h=True) == (True, False)
    assert buttons(fixed_w=True, fixed_h=True) == (False, False)


def test_explicit_hints_on_a_dialog_type():
    assert buttons(window_type=False, max_hint=True) == (False, True)
    assert buttons(window_type=False, min_hint=True, parent=False) == (True, False)
