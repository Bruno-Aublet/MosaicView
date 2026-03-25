"""
tooltips_qt.py — Fonctions de tooltip sans tkinter (version PySide6).
Extrait de modules/tooltips.py, uniquement les fonctions utilisées par Qt.
"""

import io
import os

from modules.qt.localization import _
from modules.qt.entries import get_image_metadata
from modules.qt.utils import format_file_size as _format_file_size


def get_tooltip_text(state, idx: int) -> str:
    """
    Retourne le texte du tooltip pour l'image à l'index idx.
    Retourne "" si rien à afficher.
    """
    if idx >= len(state.images_data):
        return ""
    entry = state.images_data[idx]
    info_lines = []

    filename = os.path.basename(entry.get("orig_name", ""))
    separator = "─" * 30

    if entry.get("is_corrupted"):
        if entry.get("is_too_large"):
            info_lines.append(_("tooltip.too_large_header"))
            info_lines.append("")
            info_lines.append(_("tooltip.file", name=entry['orig_name']))
            info_lines.append(_("tooltip.too_large_pixels", pixels=entry.get("corruption_reason", "")))
        else:
            info_lines.append(_("tooltip.corrupted_header"))
            info_lines.append("")
            info_lines.append(_("tooltip.file", name=entry['orig_name']))
            reason = entry.get("corruption_reason", _("tooltip.unknown_error"))
            info_lines.append(_("tooltip.reason", reason=reason[:50]))
        info_lines.append("")
        info_lines.append(_("tooltip.right_click_instruction1"))
        info_lines.append(_("tooltip.right_click_instruction2"))
    else:
        info_lines.append(filename)

        if any(e.get("source_archive") for e in state.images_data):
            source = entry.get("source_archive")
            if source == "loose":
                info_lines.append(separator)
                info_lines.append(_("tooltip.source_loose"))
            elif source == "web":
                info_lines.append(separator)
                info_lines.append(_("tooltip.source_web"))
            elif source:
                info_lines.append(separator)
                info_lines.append(_("tooltip.source_archive", name=source))
            else:
                # Entrée du fichier de base
                base_name = os.path.basename(state.current_file) if state.current_file else ""
                if base_name:
                    info_lines.append(separator)
                    info_lines.append(_("tooltip.source_archive", name=base_name))

        info_lines.append(separator)
        img_bytes = entry.get("bytes")
        if img_bytes:
            info_lines.append(_("tooltip.file_size", size=_format_file_size(len(img_bytes))))
        metadata = get_image_metadata(entry)
        if metadata:
            width, height = metadata["size"]
            if entry.get("extension", "").lower() == ".ico" and img_bytes:
                try:
                    from PIL import Image
                    ico_img = Image.open(io.BytesIO(img_bytes))
                    ico_sizes = sorted(ico_img.info.get("sizes", set()))
                    if ico_sizes:
                        sizes_str = ", ".join(f"{w}×{h}" for w, h in ico_sizes)
                        dim_label = _("tooltip.dimensions", width=1, height=1).split("1")[0]
                        info_lines.append(f"{dim_label}{sizes_str} px")
                    else:
                        info_lines.append(_("tooltip.dimensions", width=width, height=height))
                except Exception:
                    info_lines.append(_("tooltip.dimensions", width=width, height=height))
            else:
                info_lines.append(_("tooltip.dimensions", width=width, height=height))
            megapixels = (width * height) / 1_000_000
            info_lines.append(_("tooltip.resolution", mp=megapixels))
            dpi = metadata.get("dpi")
            if dpi:
                if isinstance(dpi, tuple):
                    dpi_x, dpi_y = dpi
                    if dpi_x == dpi_y:
                        info_lines.append(_("tooltip.dpi_single", dpi=int(dpi_x)))
                    else:
                        info_lines.append(_("tooltip.dpi_double", dpi_x=int(dpi_x), dpi_y=int(dpi_y)))
                else:
                    info_lines.append(_("tooltip.dpi_single", dpi=int(dpi)))
            else:
                info_lines.append(_("tooltip.dpi_undefined"))
            mode = metadata.get("mode")
            if mode:
                mode_to_bits = {
                    '1': 1, 'L': 8, 'P': 8, 'RGB': 24, 'RGBA': 32,
                    'CMYK': 32, 'LAB': 24, 'HSV': 24, 'I': 32, 'F': 32,
                    'LA': 16, 'PA': 16, 'I;16': 16,
                }
                bits = mode_to_bits.get(mode)
                if bits:
                    info_lines.append(_("tooltip.color_depth", bits=bits, mode=mode))

    return "\n".join(info_lines)


def get_directory_tooltip_text(state, dir_name: str) -> str:
    """Retourne le texte du tooltip pour un dossier virtuel."""
    file_count = 0
    total_size = 0
    for entry in state.images_data:
        if entry.get("is_dir"):
            continue
        if entry["orig_name"].startswith(dir_name):
            file_count += 1
            img_bytes = entry.get("bytes")
            if img_bytes:
                total_size += len(img_bytes)
    total_size_mb = total_size / (1024 * 1024)
    return _("labels.directory_tooltip",
             name=dir_name.rstrip("/"),
             count=file_count,
             size=f"{total_size_mb:.2f}")


def get_folder_up_tooltip_text() -> str:
    """Retourne le texte du tooltip pour l'icône de remontée."""
    return _("labels.folder_up_tooltip")
