"""
title_bar_layout.py — Disposition des titres dans la barre de titre (sans Qt).

Calcul pur, utilisé par `TitleBar._relayout` (title_bar_qt.py) et testé par
tests/test_title_bar_layout.py. Toutes les positions sont en pixels logiques,
dans les coordonnées de la barre.

Règles (skill `window-title`) :
- un panneau qui montre un comic ("comic") : titre centré sur le panneau,
  colonne d'icônes comprise ;
- un panneau vide ("app") : nom de l'appli, version et devise, collés au
  bord extérieur de la fenêtre (à gauche pour le panneau de gauche ou le
  panneau seul, à droite pour le panneau de droite) ;
- aucun texte (None) : le panneau de droite quand les deux sont vides, le
  texte de l'appli n'étant affiché qu'une fois, à gauche ;
- en double panneau, le séparateur est centré sur la séparation entre les
  panneaux, et aucun titre ne le touche ;
- en double panneau, l'icône de l'appli est répétée juste après le
  séparateur, en tête du panneau de droite ;
- rien ne chevauche les icônes ni les boutons : un titre centré est décalé,
  puis raccourci s'il ne tient toujours pas.
"""

# Espace laissé de chaque côté du séparateur
SEPARATOR_GAP = 12

COMIC = "comic"
APP = "app"


def panel_kinds(names, split):
    """Contenu de chaque panneau d'après le nom de son comic (None si vide)."""
    if split and all(name is None for name in names):
        return [APP, None]
    return [COMIC if name is not None else APP for name in names]


def _clamp(value, low, high):
    return max(low, min(value, high))


def _place(kind, region, span, text_width, right_side):
    """(x, largeur) d'un titre dans sa zone, ou None s'il n'y a rien à afficher."""
    if kind is None:
        return None
    start, end = region
    width = min(text_width, end - start)
    if width <= 0:
        return None
    if kind == COMIC:
        center = (span[0] + span[1]) / 2
        x = _clamp(round(center - width / 2), start, end - width)
    elif right_side:
        x = end - width
    else:
        x = start
    return x, width


def layout_titles(left, right, panels, separator, right_icon=0):
    """Place les titres de la barre.

    left, right : bornes de la zone de texte (après l'icône, avant les boutons).
    panels : un tuple (x0, x1, kind, text_width) par panneau affiché, de
        gauche à droite ; kind : COMIC, APP ou None.
    separator : None en mode un panneau, sinon (centre de la séparation,
        largeur du séparateur).
    right_icon : largeur de l'icône posée juste après le séparateur, en tête
        du panneau de droite (0 : pas d'icône) ; elle tient lieu de marge
        après le séparateur, comme l'icône de gauche au bord de la fenêtre.
    Retourne (placements, separator_x) : un (x, largeur) ou None par panneau,
    et la position du séparateur (None en mode un panneau).
    """
    if separator is None or len(panels) < 2:
        x0, x1, kind, text_width = panels[0]
        return [_place(kind, (left, right), (x0, x1), text_width, False)], None

    center, sep_width = separator
    sep_x = _clamp(round(center - sep_width / 2), left, max(left, right - sep_width))
    right_start = sep_x + sep_width + (right_icon if right_icon > 0 else SEPARATOR_GAP)
    regions = [
        (max(left, panels[0][0]), min(sep_x - SEPARATOR_GAP, panels[0][1])),
        (max(right_start, panels[1][0]), min(right, panels[1][1])),
    ]
    placements = [
        _place(kind, region, (x0, x1), text_width, index == 1)
        for index, ((x0, x1, kind, text_width), region) in enumerate(zip(panels, regions))
    ]
    return placements, sep_x
