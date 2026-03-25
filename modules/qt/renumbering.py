"""
renumbering.py — Fonctions de renumérotation des pages (version Qt, sans tkinter).
show_first_page_dialog est monkey-patché par renumbering_qt.py avec une version PySide6.
"""

import os
import io
from PIL import Image

from modules.qt import state as _state_module
from modules.qt.non_image_sorting import reposition_non_images
from modules.qt.page_detection import compute_reference_ratio, compute_auto_multipliers


def generate_auto_filenames(multipliers, extensions, first_page_mode=None, first_page_total=None):
    """Génère les noms de fichiers à partir des multiplicateurs."""
    if isinstance(extensions, str):
        extensions = [extensions] * len(multipliers)

    if first_page_mode == 'exclude':
        total_logical_pages = sum(multipliers[1:]) if len(multipliers) > 1 else 0
    elif first_page_mode == 'joint':
        total_logical_pages = sum(multipliers[1:]) + 2 if len(multipliers) > 1 else 2
    else:
        total_logical_pages = sum(multipliers)

    digits = max(2, len(str(total_logical_pages))) if total_logical_pages > 0 else 2

    filenames = []
    page_number = 1

    for i, mult in enumerate(multipliers):
        ext = extensions[i]

        if i == 0 and first_page_mode is not None:
            if first_page_mode == 'exclude':
                filenames.append(None)
                continue
            elif first_page_mode == 'joint':
                last_page = first_page_total if first_page_total else total_logical_pages
                start = str(page_number).zfill(digits)
                end = str(last_page).zfill(digits)
                filenames.append(start + "-" + end + ext)
                page_number += 1
                continue

        if mult == 1:
            filenames.append(str(page_number).zfill(digits) + ext)
        elif mult <= 4:
            parts = [str(page_number + j).zfill(digits) for j in range(mult)]
            filenames.append("-".join(parts) + ext)
        else:
            start = str(page_number).zfill(digits)
            end = str(page_number + mult - 1).zfill(digits)
            filenames.append(start + "---" + end + ext)
        page_number += mult

    return filenames


def show_first_page_dialog(first_entry, first_mult, total_logical_pages, callbacks):
    """Monkey-patché par renumbering_qt.py avec une version PySide6."""
    return 'auto'


def renumber_pages_auto(callbacks):
    """Renumérotation avec auto-détection des pages multiples via le ratio largeur/hauteur."""
    image_entries = [e for e in _state_module.state.images_data if e["is_image"]]
    if not image_entries:
        return

    ratios = []
    for entry in image_entries:
        w = entry.get("img_width")
        h = entry.get("img_height")
        if w and h:
            ratios.append(w / h)
        else:
            try:
                img = Image.open(io.BytesIO(entry["bytes"]))
                ratios.append(img.width / img.height)
                img.close()
            except Exception:
                ratios.append(0)

    multipliers = compute_auto_multipliers(ratios)
    extensions = [entry["extension"] for entry in image_entries]

    first_page_mode = None
    first_mult = multipliers[0] if multipliers else 1

    if first_mult > 1 and callbacks.get("root"):
        last_page_joint = sum(multipliers[1:]) + 2 if len(multipliers) > 1 else 2
        choice = show_first_page_dialog(
            image_entries[0],
            first_mult,
            last_page_joint,
            callbacks
        )
        if choice is None:
            return
        first_page_mode = choice

    callbacks["save_state"]()

    filenames = generate_auto_filenames(
        multipliers,
        extensions,
        first_page_mode=first_page_mode,
        first_page_total=sum(multipliers[1:]) + 2 if len(multipliers) > 1 else 2
    )

    for i, entry in enumerate(image_entries):
        new_name = filenames[i]
        if new_name is not None:
            entry["orig_name"] = new_name

    _state_module.state.images_data = reposition_non_images(_state_module.state.images_data)
    _state_module.state.modified = True
    callbacks["save_state"]()
    callbacks["render_mosaic"]()
    callbacks["update_button_text"]()


def renumber_pages(callbacks):
    """Renumérotation de toutes les images selon leur position actuelle."""
    callbacks["save_state"]()

    total_images = len([e for e in _state_module.state.images_data if e["is_image"]])
    digits = max(2, len(str(total_images)))
    counter = 1

    for entry in _state_module.state.images_data:
        if entry["is_image"]:
            entry["orig_name"] = str(counter).zfill(digits) + entry["extension"]
            counter += 1

    _state_module.state.images_data = reposition_non_images(_state_module.state.images_data)
    _state_module.state.modified = True
    callbacks["save_state"]()
    callbacks["render_mosaic"]()
    callbacks["update_button_text"]()
