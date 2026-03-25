"""
page_detection.py — Détection des pages multiples (doubles, triples…) dans un comic.

La détection est basée sur le ratio largeur/hauteur relatif à la médiane des pages portrait,
ce qui s'adapte au contenu réel du comic (contrairement à un seuil absolu fixe).
"""


def compute_reference_ratio(ratios):
    """Calcule le ratio de référence (médiane des pages portrait) à partir d'une liste de ratios."""
    portrait_ratios = [r for r in ratios if 0 < r < 1]
    if portrait_ratios:
        portrait_ratios_sorted = sorted(portrait_ratios)
        mid = len(portrait_ratios_sorted) // 2
        if len(portrait_ratios_sorted) % 2 == 0:
            return (portrait_ratios_sorted[mid - 1] + portrait_ratios_sorted[mid]) / 2
        else:
            return portrait_ratios_sorted[mid]
    return 0.70


def compute_auto_multipliers(ratios):
    """Calcule les multiplicateurs de pages à partir d'une liste de ratios largeur/hauteur."""
    reference_ratio = compute_reference_ratio(ratios)

    multipliers = []
    for r in ratios:
        if r <= 0 or reference_ratio <= 0:
            multipliers.append(1)
        else:
            mult = max(1, round(r / reference_ratio))
            multipliers.append(mult)

    return multipliers
