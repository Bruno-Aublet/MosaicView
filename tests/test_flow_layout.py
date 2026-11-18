"""Disposition « en flux » de la ligne de boutons de la Bibliothèque
(flow_layout_qt.flow_rows) : des éléments rangés de gauche à droite, une
nouvelle rangée quand la largeur manque, aucun élément perdu.

flow_layout_qt.py importe PySide6 : la fonction est extraite par
tests/ast_loader.py et exécutée seule.
"""
import ast_loader

flow_rows = ast_loader.load("flow_layout_qt.py", {"flow_rows"})["flow_rows"]


def test_everything_on_one_row_when_wide_enough():
    assert flow_rows([50, 50, 50], 170, 10) == [[0, 1, 2]]


def test_items_go_to_the_next_row_when_the_width_runs_out():
    # 50 + 10 + 50 = 110 ; le troisième n'y tient plus
    assert flow_rows([50, 50, 50], 120, 10) == [[0, 1], [2]]


def test_spacing_counts_between_items_only():
    assert flow_rows([50, 50], 110, 10) == [[0, 1]]
    assert flow_rows([50, 50], 109, 10) == [[0], [1]]


def test_item_wider_than_the_row_is_alone_and_never_lost():
    assert flow_rows([30, 300, 30], 100, 10) == [[0], [1], [2]]


def test_order_is_kept_and_every_item_placed():
    widths = [80, 120, 40, 200, 60, 90, 150]
    rows = flow_rows(widths, 250, 6)
    assert [i for row in rows for i in row] == list(range(len(widths)))
    for row in rows:
        used = sum(widths[i] for i in row) + 6 * (len(row) - 1)
        assert used <= 250 or len(row) == 1


def test_no_item_gives_no_row():
    assert flow_rows([], 100, 6) == []
