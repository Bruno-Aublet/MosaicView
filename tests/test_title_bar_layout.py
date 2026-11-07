"""Disposition des titres dans la barre de titre de la fenêtre principale
(title_bar_layout.py) : un titre par panneau, centré sur son panneau s'il
montre un comic, texte de l'appli collé au bord extérieur sinon, séparateur
sur la séparation des panneaux, et jamais rien sur l'icône ni les boutons.

Barre de 1000 px : zone de texte de 36 (après l'icône) à 862 (avant les
boutons).
"""
from modules.qt.title_bar_layout import (
    APP, COMIC, SEPARATOR_GAP, layout_titles, panel_kinds,
)

LEFT, RIGHT = 36, 862
SEP_WIDTH = 20


def test_panel_kinds():
    assert panel_kinds(["Aaaa.cbz"], False) == [COMIC]
    assert panel_kinds([None], False) == [APP]
    assert panel_kinds(["Aaaa.cbz", None], True) == [COMIC, APP]
    assert panel_kinds([None, "Bbbb.cbz"], True) == [APP, COMIC]
    assert panel_kinds(["Aaaa.cbz", "Bbbb.cbz"], True) == [COMIC, COMIC]
    # Deux panneaux vides : le texte de l'appli une seule fois, à gauche
    assert panel_kinds([None, None], True) == [APP, None]


def test_single_panel_comic_is_centered_on_the_panel():
    placements, sep = layout_titles(LEFT, RIGHT, [(0, 1000, COMIC, 200)], None)
    assert placements == [(400, 200)]
    assert sep is None


def test_single_panel_app_text_is_left_aligned():
    placements, sep = layout_titles(LEFT, RIGHT, [(0, 1000, APP, 300)], None)
    assert placements == [(LEFT, 300)]


def test_centered_title_never_overlaps_the_buttons():
    # Centre du panneau = 500 ; un titre de 800 déborderait sur les boutons
    placements, _sep = layout_titles(LEFT, RIGHT, [(0, 1000, COMIC, 800)], None)
    x, width = placements[0]
    assert x >= LEFT and x + width <= RIGHT


def test_too_long_title_is_shortened_to_the_available_width():
    placements, _sep = layout_titles(LEFT, RIGHT, [(0, 1000, COMIC, 2000)], None)
    assert placements == [(LEFT, RIGHT - LEFT)]


def test_separator_follows_the_panels_separation():
    panels = [(0, 296, COMIC, 100), (304, 1000, COMIC, 100)]
    _placements, sep = layout_titles(LEFT, RIGHT, panels, (300, SEP_WIDTH))
    assert sep == 300 - SEP_WIDTH // 2


def test_two_comics_each_centered_on_its_panel():
    panels = [(0, 496, COMIC, 100), (504, 1000, COMIC, 100)]
    placements, sep = layout_titles(LEFT, RIGHT, panels, (500, SEP_WIDTH))
    assert placements[0] == (198, 100)
    assert placements[1] == (702, 100)
    assert sep == 490


def test_comic_left_app_right_is_aligned_on_the_right():
    panels = [(0, 496, COMIC, 100), (504, 1000, APP, 250)]
    placements, _sep = layout_titles(LEFT, RIGHT, panels, (500, SEP_WIDTH))
    assert placements[1] == (RIGHT - 250, 250)


def test_app_left_comic_right_is_aligned_on_the_left():
    panels = [(0, 496, APP, 250), (504, 1000, COMIC, 100)]
    placements, _sep = layout_titles(LEFT, RIGHT, panels, (500, SEP_WIDTH))
    assert placements[0] == (LEFT, 250)


def test_two_empty_panels_show_the_app_text_once_before_the_separator():
    panels = [(0, 296, APP, 600), (304, 1000, None, 0)]
    placements, sep = layout_titles(LEFT, RIGHT, panels, (300, SEP_WIDTH))
    assert placements[1] is None
    x, width = placements[0]
    assert x == LEFT
    assert x + width == sep - SEPARATOR_GAP


def test_titles_never_touch_the_separator():
    panels = [(0, 496, COMIC, 1000), (504, 1000, COMIC, 1000)]
    placements, sep = layout_titles(LEFT, RIGHT, panels, (500, SEP_WIDTH))
    assert placements[0][0] + placements[0][1] <= sep - SEPARATOR_GAP
    assert placements[1][0] >= sep + SEP_WIDTH + SEPARATOR_GAP


def test_right_title_starts_after_the_right_panel_icon():
    # Icône de l'appli posée juste après le séparateur : le titre de droite
    # commence après elle, sans la recouvrir
    icon = 36
    panels = [(0, 496, COMIC, 100), (504, 1000, COMIC, 2000)]
    placements, sep = layout_titles(LEFT, RIGHT, panels, (500, SEP_WIDTH), right_icon=icon)
    assert placements[1] == (sep + SEP_WIDTH + icon, RIGHT - (sep + SEP_WIDTH + icon))


def test_separator_stays_inside_the_text_area():
    # Séparation tout contre le bord droit : le séparateur ne passe pas
    # sous les boutons
    panels = [(0, 990, COMIC, 100), (998, 1000, APP, 100)]
    placements, sep = layout_titles(LEFT, RIGHT, panels, (994, SEP_WIDTH))
    assert sep + SEP_WIDTH <= RIGHT
    # Plus de place pour le titre de droite : rien, plutôt qu'un titre de
    # largeur nulle ou négative
    assert placements[1] is None
