"""
non_image_sorting.py — Repositionnement alphanumérique des entrées non-image.

Après une renumérotation des images, les non-images doivent reprendre leur
place naturelle dans l'ordre alphanumérique global (images + non-images mélangés).

Fonction principale : reposition_non_images(images_data) → list
  Retourne une nouvelle liste ordonnée où chaque non-image est insérée à la
  position correspondant à l'ordre alphanumérique de son orig_name parmi
  l'ensemble de toutes les entrées (images et non-images).

Tri alphanumérique : insensible à la casse, natural sort (ex. "10" > "9").
"""

import re


def _natural_key(s: str):
    """Clé de tri alphanumérique naturel (insensible à la casse).
    Trie d'abord sur le nom sans extension, puis sur l'extension — cohérent avec
    archive_loader._natural_sort_key utilisé à l'ouverture de l'archive."""
    import os
    name, ext = os.path.splitext(s)
    name_parts = re.split(r'(\d+)', name.lower())
    name_key = [int(p) if p.isdigit() else p for p in name_parts]
    return (name_key, ext.lower())


def reposition_non_images(images_data: list) -> list:
    """
    Repositionne les entrées non-image à leur place alphanumérique naturelle
    parmi l'ensemble des entrées (images + non-images).

    Les images gardent leur ordre relatif entre elles (ordre post-renumérotation).
    Les non-images sont insérées aux positions dictées par le tri alphanumérique
    de leurs orig_name relativement aux orig_name des images voisines.

    Algorithme :
      1. Trier toutes les entrées par orig_name (natural sort) pour obtenir
         l'ordre de référence global.
      2. Parcourir cet ordre de référence en maintenant un curseur sur la liste
         des images (dans leur ordre actuel).
      3. Pour chaque entrée dans l'ordre de référence :
         - si c'est une image → avancer le curseur image, insérer à sa position
           actuelle dans images (ordre préservé).
         - si c'est un non-image → l'insérer à la position courante.

    Résultat : les non-images se glissent entre les images selon l'ordre
    alphanumérique global, sans perturber l'ordre relatif des images.
    """
    if not images_data:
        return images_data

    images    = [e for e in images_data if e.get("is_image")]
    non_images = [e for e in images_data if not e.get("is_image")]

    if not non_images:
        # Rien à repositionner
        return images_data

    # Ordre de référence global par orig_name (natural sort)
    all_entries_sorted = sorted(images_data, key=lambda e: _natural_key(e.get("orig_name", "")))

    result: list = []
    image_cursor = 0  # curseur dans la liste `images` (ordre actuel préservé)

    for entry in all_entries_sorted:
        if entry.get("is_image"):
            if image_cursor < len(images):
                result.append(images[image_cursor])
                image_cursor += 1
        else:
            result.append(entry)

    # Sécurité : s'il reste des images (ne devrait pas arriver)
    while image_cursor < len(images):
        result.append(images[image_cursor])
        image_cursor += 1

    return result
