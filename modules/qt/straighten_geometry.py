"""
modules/qt/straighten_geometry.py — Calcul d'angle de correction pour le redressement manuel.

Fonction pure (aucune dépendance Qt), partagée entre la visionneuse de
redressage dédiée (straighten_viewer_qt.py) et l'outil Redressage de la
barre d'outils flottante de la visionneuse principale (image_viewer_qt.py).
"""

import math


def line_to_correction(ix1, iy1, ix2, iy2):
    """Calcule l'angle de correction d'un trait (coordonnées image).
    Retourne (correction_deg, category, vertical_sign) — category : 'h' ou 'v',
    vertical_sign : signe de l'angle brut avant réduction (utilisé seulement si category == 'v').
    Retourne (None, None, None) si le trait est nul."""
    dx = ix2 - ix1
    dy = iy2 - iy1
    if dx == 0 and dy == 0:
        return None, None, None

    angle_rad = math.atan2(dy, dx)
    angle_deg = math.degrees(angle_rad)

    # Normalise dans [-90, 90]
    if angle_deg > 90:
        angle_deg -= 180
    elif angle_deg < -90:
        angle_deg += 180

    # Détermine si le trait est plutôt horizontal ou vertical
    # Note : PIL.rotate() tourne en anti-horaire pour les angles positifs,
    # mais l'axe Y écran est vers le bas (anti-mathématique) → compensé par la formule ci-dessous.
    abs_angle = abs(angle_deg)
    if abs_angle <= 45:
        return angle_deg, 'h', None
    # Trait plutôt vertical → angle par rapport à la verticale
    if angle_deg >= 0:
        return angle_deg - 90, 'v', 1
    return angle_deg + 90, 'v', -1
