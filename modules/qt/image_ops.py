# -------------------------
# Opérations sur les images (logique data)
# -------------------------
import os
import io
from PIL import Image
from modules.qt.entries import ensure_image_loaded, save_image_to_bytes, free_image_memory


def rotate_entry_data(entry, angle, state=None):
    """Fait pivoter une entrée image du nombre de degrés spécifié.
    angle: -90 pour rotation droite (horaire), 90 pour rotation gauche (anti-horaire).
    Retourne True si la rotation a été effectuée, False sinon."""
    if not entry["is_image"]:
        return False

    img = ensure_image_loaded(entry)
    if img is None:
        return False

    rotated_img = img.rotate(angle, expand=True)
    img.close()
    entry["img"] = rotated_img

    entry["bytes"] = save_image_to_bytes(entry)
    entry["large_thumb_pil"] = None

    if state is not None:
        from modules.qt.comic_info import get_page_image_index, update_page_entries_in_xml_data
        idx = get_page_image_index(state, entry)
        if idx is not None:
            update_page_entries_in_xml_data(state, [(idx, entry)])

    free_image_memory(entry)
    return True


def flip_entry_data(entry, direction, state=None):
    """Retourne une entrée image selon la direction spécifiée.
    direction: 'horizontal' (miroir gauche-droite) ou 'vertical' (miroir haut-bas).
    Retourne True si le retournement a été effectué, False sinon."""
    if not entry["is_image"]:
        return False

    img = ensure_image_loaded(entry)
    if img is None:
        return False

    if direction == 'horizontal':
        flipped_img = img.transpose(Image.FLIP_LEFT_RIGHT)
    else:
        flipped_img = img.transpose(Image.FLIP_TOP_BOTTOM)

    img.close()
    entry["img"] = flipped_img

    entry["bytes"] = save_image_to_bytes(entry)
    entry["large_thumb_pil"] = None

    if state is not None:
        from modules.qt.comic_info import get_page_image_index, update_page_entries_in_xml_data
        idx = get_page_image_index(state, entry)
        if idx is not None:
            update_page_entries_in_xml_data(state, [(idx, entry)])

    free_image_memory(entry)
    return True


def convert_image_data(entry, target_format, quality):
    """Convertit une entrée image vers un nouveau format.
    Retourne (new_entry_dict, None) en cas de succès, (None, error_msg) en cas d'erreur.
    new_entry_dict contient les clés data (pas de tk_img)."""
    try:
        img = ensure_image_loaded(entry)
        if img is None:
            return None, f"{entry['orig_name']} : image non chargée"

        # Récupère le DPI de l'image source (tuple (x, y) ou None)
        _raw_dpi = entry.get("dpi") or img.info.get("dpi")
        if _raw_dpi:
            if isinstance(_raw_dpi, (int, float)):
                source_dpi = (int(_raw_dpi), int(_raw_dpi))
            elif isinstance(_raw_dpi, (tuple, list)) and len(_raw_dpi) >= 2:
                source_dpi = (int(_raw_dpi[0]), int(_raw_dpi[1]))
            else:
                source_dpi = None
        else:
            source_dpi = None

        # Détermine le nouveau nom de fichier
        old_name = entry["orig_name"]
        name_without_ext = os.path.splitext(old_name)[0]

        ext_map = {
            "PNG": ".png",
            "JPEG": ".jpg",
            "WEBP": ".webp",
            "BMP": ".bmp",
            "TIFF": ".tiff",
            "GIF": ".gif"
        }
        new_ext = ext_map.get(target_format, ".png")
        new_name = name_without_ext + new_ext

        # Crée une copie de l'image pour la conversion
        converted_img = img.copy()

        # Conversion du mode CMYK/I/F → RGB
        if converted_img.mode in ("CMYK", "YCbCr", "I", "F"):
            old_img = converted_img
            converted_img = converted_img.convert("RGB")
            old_img.close()

        # Conversion en bytes selon le format cible
        img_bytes = io.BytesIO()

        if target_format == "JPEG":
            # JPEG ne supporte pas la transparence
            if converted_img.mode in ("RGBA", "LA", "P"):
                rgb_img = Image.new("RGB", converted_img.size, (255, 255, 255))
                if converted_img.mode == "P":
                    rgba_temp = converted_img.convert("RGBA")
                    rgb_img.paste(rgba_temp, mask=rgba_temp.split()[-1])
                    rgba_temp.close()
                    del rgba_temp
                else:
                    rgb_img.paste(converted_img, mask=converted_img.split()[-1] if converted_img.mode in ("RGBA", "LA") else None)
                converted_img.close()
                converted_img = rgb_img
            jpeg_kwargs = {"quality": quality, "optimize": True}
            if source_dpi:
                jpeg_kwargs["dpi"] = source_dpi
            converted_img.save(img_bytes, format=target_format, **jpeg_kwargs)
        elif target_format == "WEBP":
            converted_img.save(img_bytes, format=target_format, quality=quality)
        elif target_format == "GIF":
            if converted_img.mode not in ("P", "L"):
                old_img = converted_img
                converted_img = converted_img.convert("P", palette=Image.ADAPTIVE, colors=256)
                old_img.close()
            converted_img.save(img_bytes, format=target_format)
        elif target_format == "TIFF":
            tiff_kwargs = {}
            if source_dpi:
                tiff_kwargs["dpi"] = source_dpi
            converted_img.save(img_bytes, format=target_format, **tiff_kwargs)
        else:
            # PNG, BMP : pas de DPI via save()
            converted_img.save(img_bytes, format=target_format)

        # Crée la nouvelle entrée
        img_bytes.seek(0)
        new_entry = {
            "orig_name": new_name,
            "extension": new_ext,
            "bytes": img_bytes.getvalue(),
            "img": None,
            "is_image": True,
            "thumb": None,
            "img_id": None,
            "dpi": source_dpi if source_dpi else None,
            "img_width": converted_img.width,
            "img_height": converted_img.height,
        }

        # Nettoyage mémoire
        converted_img.close()
        del converted_img
        del img_bytes
        free_image_memory(entry)

        return new_entry, None

    except Exception as e:
        # Nettoyage en cas d'erreur
        try:
            if 'converted_img' in dir():
                converted_img.close()
        except Exception:
            pass
        try:
            if 'img_bytes' in dir():
                img_bytes.close()
        except Exception:
            pass
        return None, f"{entry['orig_name']} : {str(e)}"


def merge_images_vertically(images_list, adjustment_mode='keep_original'):
    """
    Fusionne plusieurs images verticalement sans perte de qualité.

    Args:
        images_list: Liste des images PIL à fusionner
        adjustment_mode: 'keep_original', 'enlarge_small', ou 'reduce_large'
    """
    if not images_list:
        return None

    max_width = max(img.width for img in images_list)
    min_width = min(img.width for img in images_list)

    adjusted_images = []

    if adjustment_mode == 'enlarge_small':
        for img in images_list:
            if img.width < max_width:
                ratio = max_width / img.width
                new_height = int(img.height * ratio)
                resized_img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                adjusted_images.append(resized_img)
            else:
                adjusted_images.append(img)
        target_width = max_width

    elif adjustment_mode == 'reduce_large':
        for img in images_list:
            if img.width > min_width:
                ratio = min_width / img.width
                new_height = int(img.height * ratio)
                resized_img = img.resize((min_width, new_height), Image.Resampling.LANCZOS)
                adjusted_images.append(resized_img)
            else:
                adjusted_images.append(img)
        target_width = min_width

    else:  # keep_original
        adjusted_images = images_list
        target_width = max_width

    total_height = sum(img.height for img in adjusted_images)
    merged_img = Image.new('RGB', (target_width, total_height))

    y_offset = 0
    for img in adjusted_images:
        x_offset = (target_width - img.width) // 2
        merged_img.paste(img, (x_offset, y_offset))
        y_offset += img.height

    return merged_img


def merge_images_horizontally(images_list, adjustment_mode='keep_original'):
    """
    Fusionne plusieurs images horizontalement sans perte de qualité.

    Args:
        images_list: Liste des images PIL à fusionner
        adjustment_mode: 'keep_original', 'enlarge_small', ou 'reduce_large'
    """
    if not images_list:
        return None

    max_height = max(img.height for img in images_list)
    min_height = min(img.height for img in images_list)

    adjusted_images = []

    if adjustment_mode == 'enlarge_small':
        for img in images_list:
            if img.height < max_height:
                ratio = max_height / img.height
                new_width = int(img.width * ratio)
                resized_img = img.resize((new_width, max_height), Image.Resampling.LANCZOS)
                adjusted_images.append(resized_img)
            else:
                adjusted_images.append(img)
        target_height = max_height

    elif adjustment_mode == 'reduce_large':
        for img in images_list:
            if img.height > min_height:
                ratio = min_height / img.height
                new_width = int(img.width * ratio)
                resized_img = img.resize((new_width, min_height), Image.Resampling.LANCZOS)
                adjusted_images.append(resized_img)
            else:
                adjusted_images.append(img)
        target_height = min_height

    else:  # keep_original
        adjusted_images = images_list
        target_height = max_height

    total_width = sum(img.width for img in adjusted_images)
    merged_img = Image.new('RGB', (total_width, target_height))

    x_offset = 0
    for img in adjusted_images:
        y_offset = (target_height - img.height) // 2
        merged_img.paste(img, (x_offset, y_offset))
        x_offset += img.width

    return merged_img


def merge_images_2d(positions_data, ask_adjustment_func=None):
    """
    Fusionne plusieurs images selon leur disposition 2D exacte.
    Détecte automatiquement les différences de dimensions et propose un dialogue si nécessaire.

    Args:
        positions_data: Données de position des images (list de dicts avec 'entry', 'x', 'y')
        ask_adjustment_func: Callback(dimension_type, dimensions_list) → mode ou None si annulé.
                             Si None, utilise 'keep_original'.

    Returns:
        Image fusionnée, ou None si annulé
    """
    if not positions_data:
        return None

    # Extrait les images et leurs positions
    items = []
    for i, pos_data in enumerate(positions_data):
        items.append({
            "idx": i,
            "img": pos_data["entry"]["img"],
            "x": pos_data["x"],
            "y": pos_data["y"]
        })

    # Seuil pour considérer que deux images sont alignées (en pixels de miniature)
    align_threshold = 20

    # Groupe les images par lignes (Y similaire)
    items_sorted_by_y = sorted(items, key=lambda item: item["y"])
    rows = []
    current_row = [items_sorted_by_y[0]]

    for item in items_sorted_by_y[1:]:
        if abs(item["y"] - current_row[0]["y"]) < align_threshold:
            current_row.append(item)
        else:
            rows.append(current_row)
            current_row = [item]
    rows.append(current_row)

    # Pour chaque ligne, trie les images par X (gauche à droite)
    for row in rows:
        row.sort(key=lambda item: item["x"])

    # Détecte les différences de dimensions
    need_adjustment = False
    adjustment_mode = 'keep_original'
    dimension_type = 'height'
    dimensions_list = []

    # Vérifie les hauteurs dans chaque ligne horizontale
    for row in rows:
        if len(row) > 1:
            heights = [item["img"].height for item in row]
            if len(set(heights)) > 1:
                need_adjustment = True
                dimension_type = 'height'
                dimensions_list = heights
                break

    # Vérifie les largeurs entre les lignes
    if not need_adjustment and len(rows) > 1:
        row_widths = []
        for row in rows:
            if len(row) == 1:
                row_widths.append(row[0]["img"].width)
            else:
                row_widths.append(sum(item["img"].width for item in row))

        if len(set(row_widths)) > 1:
            need_adjustment = True
            dimension_type = 'width'
            dimensions_list = row_widths

    # Si des différences sont détectées, demande le mode d'ajustement
    if need_adjustment and ask_adjustment_func:
        adjustment_mode = ask_adjustment_func(dimension_type, dimensions_list)
        if adjustment_mode is None:
            return None

    # Fusionne chaque ligne horizontalement.
    # Pour chaque ligne, calcule l'offset X réel en se basant sur la ligne de référence
    # (la plus longue). On mappe chaque position X miniature à un offset en pixels réels
    # en comptant combien d'images réelles sont à gauche dans la ligne de référence.
    ref_row = max(rows, key=lambda r: len(r))
    ref_row_sorted = sorted(ref_row, key=lambda item: item["x"])

    # Construit une table : position X miniature → offset X réel cumulé dans la ligne de référence
    ref_x_to_real = {}
    real_offset = 0
    for item in ref_row_sorted:
        ref_x_to_real[item["x"]] = real_offset
        real_offset += item["img"].width

    # Pour une position X miniature quelconque, trouve l'offset réel le plus proche
    def mini_x_to_real_offset(mini_x):
        # Cherche l'image de ref_row dont le X mini est le plus proche
        closest = min(ref_row_sorted, key=lambda item: abs(item["x"] - mini_x))
        return ref_x_to_real[closest["x"]]

    row_data = []
    for row in rows:
        start_x_mini = min(item["x"] for item in row)
        if len(row) == 1:
            row_img = row[0]["img"]
        else:
            row_img = merge_images_horizontally([item["img"] for item in row], adjustment_mode)
        start_x_real = mini_x_to_real_offset(start_x_mini)
        row_data.append({
            "img": row_img,
            "start_x_mini": start_x_mini,
            "start_x_real": start_x_real,
        })

    # Si plusieurs lignes avec ajustement, applique le redimensionnement
    # mais conserve les offsets X calculés
    if len(row_data) > 1 and adjustment_mode != 'keep_original':
        min_x_real = min(rd["start_x_real"] for rd in row_data)
        max_width = max(rd["start_x_real"] - min_x_real + rd["img"].width for rd in row_data)
        total_height = sum(rd["img"].height for rd in row_data)
        merged_img = Image.new('RGB', (int(max_width), int(total_height)), (255, 255, 255))
        y_offset = 0
        for rd in row_data:
            x_offset = rd["start_x_real"] - min_x_real
            merged_img.paste(rd["img"], (x_offset, y_offset))
            y_offset += rd["img"].height
        return merged_img

    # Calcule les dimensions du canvas final
    min_x_real = min(rd["start_x_real"] for rd in row_data)
    max_width = max(rd["start_x_real"] - min_x_real + rd["img"].width for rd in row_data)
    total_height = sum(rd["img"].height for rd in row_data)

    merged_img = Image.new('RGB', (int(max_width), int(total_height)), (255, 255, 255))

    y_offset = 0
    for rd in row_data:
        x_offset = rd["start_x_real"] - min_x_real
        merged_img.paste(rd["img"], (x_offset, y_offset))
        y_offset += rd["img"].height

    return merged_img
