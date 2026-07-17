import pytest

from modules.qt.page_detection import compute_reference_ratio, compute_auto_multipliers


# ---------------------------------------------------------------------------
# compute_reference_ratio
# ---------------------------------------------------------------------------

def test_compute_reference_ratio_no_portrait_pages_returns_default():
    # Que des pages paysage/carrées (ratio >= 1) : pas de médiane possible
    assert compute_reference_ratio([1.5, 2.0]) == 0.70


def test_compute_reference_ratio_empty_list_returns_default():
    assert compute_reference_ratio([]) == 0.70


def test_compute_reference_ratio_odd_count_returns_median():
    # portrait ratios triés: 0.5, 0.6, 0.7 -> médiane = 0.6
    assert compute_reference_ratio([0.7, 0.5, 0.6]) == 0.6


def test_compute_reference_ratio_even_count_averages_two_middles():
    # triés: 0.5, 0.6, 0.7, 0.8 -> moyenne(0.6, 0.7) = 0.65
    assert compute_reference_ratio([0.8, 0.6, 0.5, 0.7]) == pytest.approx(0.65)


def test_compute_reference_ratio_ignores_non_portrait_values():
    # 1.5 (paysage) et 0 sont ignorés du calcul de la médiane
    ratios = [1.5, 0, 0.6, 0.6, 0.6]
    assert compute_reference_ratio(ratios) == 0.6


# ---------------------------------------------------------------------------
# compute_auto_multipliers
# ---------------------------------------------------------------------------

def test_compute_auto_multipliers_all_normal_pages_gives_ones():
    ratios = [0.6, 0.6, 0.6, 0.6]
    assert compute_auto_multipliers(ratios) == [1, 1, 1, 1]


def test_compute_auto_multipliers_double_page_detected():
    # référence 0.6 (portrait) ; une page ~2x plus large que haute (double page)
    ratios = [0.6, 0.6, 1.2, 0.6]
    assert compute_auto_multipliers(ratios) == [1, 1, 2, 1]


def test_compute_auto_multipliers_triple_page_detected():
    ratios = [0.6, 0.6, 1.8, 0.6]
    assert compute_auto_multipliers(ratios) == [1, 1, 3, 1]


def test_compute_auto_multipliers_zero_ratio_defaults_to_one():
    ratios = [0.6, 0, 0.6]
    assert compute_auto_multipliers(ratios) == [1, 1, 1]


def test_compute_auto_multipliers_never_returns_zero():
    # même un ratio très petit par rapport à la référence reste >= 1
    ratios = [0.6, 0.01]
    result = compute_auto_multipliers(ratios)
    assert all(m >= 1 for m in result)


def test_compute_auto_multipliers_empty_list_returns_empty():
    assert compute_auto_multipliers([]) == []
