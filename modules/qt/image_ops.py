# -------------------------
# Opérations sur les images (logique data)
# -------------------------
import os
import io
from PIL import Image, ImageSequence
from modules.qt.entries import ensure_image_loaded, save_image_to_bytes, free_image_memory


# Formats cibles de la conversion capables de stocker une animation.
_ANIMATABLE_FORMATS = ("WEBP", "PNG", "AVIF")


def transform_animated_gif(entry, frame_fn):
    """Applique frame_fn (Image RGBA -> Image) à chaque frame d'un GIF animé
    et retourne les bytes du GIF réassemblé, durées et boucle conservées.

    Sans ce traitement frame par frame, toute opération qui passe par
    ensure_image_loaded() ne voit que la première frame et aplatit
    l'animation en une image fixe.

    Les frames lues par Pillow sont des images déjà composées (pas les
    rectangles partiels du fichier d'origine) : chacune est réécrite en
    image complète avec disposal=2, sinon les zones transparentes laisseraient
    voir la frame précédente une seconde fois. Met aussi à jour
    img_width/img_height de l'entrée."""
    src = Image.open(io.BytesIO(entry["bytes"]))
    loop = src.info.get("loop")
    frames = []
    durations = []
    for frame in ImageSequence.Iterator(src):
        durations.append(frame.info.get("duration", 100))
        frames.append(frame_fn(frame.copy().convert("RGBA")))
    src.close()

    save_kwargs = {"save_all": True, "append_images": frames[1:],
                   "duration": durations, "disposal": 2}
    if loop is not None:
        save_kwargs["loop"] = loop
    out = io.BytesIO()
    frames[0].save(out, format="GIF", **save_kwargs)
    entry["img_width"], entry["img_height"] = frames[0].size
    for f in frames:
        f.close()
    return out.getvalue()


def rotate_entry_data(entry, angle, state=None):
    """Fait pivoter une entrée image du nombre de degrés spécifié.
    angle: -90 pour rotation droite (horaire), 90 pour rotation gauche (anti-horaire).
    Retourne True si la rotation a été effectuée, False sinon."""
    if not entry["is_image"]:
        return False

    if entry.get("is_animated_gif") and entry.get("bytes") and not entry.get("is_corrupted"):
        free_image_memory(entry)
        entry["bytes"] = transform_animated_gif(entry, lambda f: f.rotate(angle, expand=True))
        entry["large_thumb_pil"] = None
        entry["_hash"] = None
        _update_page_xml(entry, state)
        return True

    img = ensure_image_loaded(entry)
    if img is None:
        return False

    rotated_img = img.rotate(angle, expand=True)
    img.close()
    entry["img"] = rotated_img

    entry["bytes"] = save_image_to_bytes(entry)
    entry["large_thumb_pil"] = None
    entry["_hash"] = None

    _update_page_xml(entry, state)

    free_image_memory(entry)
    return True


def _update_page_xml(entry, state):
    """Met à jour la balise <Page> de ComicInfo.xml après modification des bytes."""
    if state is None:
        return
    from modules.qt.comic_info import get_page_image_index, update_page_entries_in_xml_data
    idx = get_page_image_index(state, entry)
    if idx is not None:
        update_page_entries_in_xml_data(state, [(idx, entry)])


def flip_entry_data(entry, direction, state=None):
    """Retourne une entrée image selon la direction spécifiée.
    direction: 'horizontal' (miroir gauche-droite) ou 'vertical' (miroir haut-bas).
    Retourne True si le retournement a été effectué, False sinon."""
    if not entry["is_image"]:
        return False

    method = Image.FLIP_LEFT_RIGHT if direction == 'horizontal' else Image.FLIP_TOP_BOTTOM

    if entry.get("is_animated_gif") and entry.get("bytes") and not entry.get("is_corrupted"):
        free_image_memory(entry)
        entry["bytes"] = transform_animated_gif(entry, lambda f: f.transpose(method))
        entry["large_thumb_pil"] = None
        entry["_hash"] = None
        _update_page_xml(entry, state)
        return True

    img = ensure_image_loaded(entry)
    if img is None:
        return False

    flipped_img = img.transpose(method)

    img.close()
    entry["img"] = flipped_img

    entry["bytes"] = save_image_to_bytes(entry)
    entry["large_thumb_pil"] = None
    entry["_hash"] = None

    _update_page_xml(entry, state)

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
            "AVIF": ".avif",
            "BMP": ".bmp",
            "TIFF": ".tiff",
            "GIF": ".gif"
        }
        new_ext = ext_map.get(target_format, ".png")
        new_name = name_without_ext + new_ext

        # Image animée (GIF, WebP, PNG animé, AVIF) vers un format animable
        # (WebP, PNG animé, AVIF) : toutes les frames sont converties, durées
        # et boucle conservées. JPEG, BMP et TIFF ne gardent que la première
        # image (pas d'animation possible), comme le choix "GIF statique", qui
        # demande explicitement une image fixe.
        is_animated = entry.get("is_animated_gif") or entry.get("is_animated_image")
        if is_animated and target_format in _ANIMATABLE_FORMATS:
            free_image_memory(entry)
            src = Image.open(io.BytesIO(entry["bytes"]))
            loop = src.info.get("loop")
            frames, durations = [], []
            for frame in ImageSequence.Iterator(src):
                # Copie d'abord : WebP/AVIF ne renseignent la durée qu'une
                # fois la frame décodée.
                frames.append(frame.copy().convert("RGBA"))
                durations.append(int(frame.info.get("duration") or 100))
            src.close()
            save_kwargs = {"save_all": True, "append_images": frames[1:], "duration": durations}
            if loop is not None:
                save_kwargs["loop"] = loop
            if target_format in ("WEBP", "AVIF"):
                save_kwargs["quality"] = quality
            out = io.BytesIO()
            frames[0].save(out, format=target_format, **save_kwargs)
            width, height = frames[0].size
            for f in frames:
                f.close()
            return {
                "orig_name": new_name,
                "extension": new_ext,
                "bytes": out.getvalue(),
                "img": None,
                "is_image": True,
                "thumb": None,
                "img_id": None,
                "dpi": None,
                "img_width": width,
                "img_height": height,
                # Lecture animée dans la visionneuse (voir
                # entries.set_animated_image_info).
                "is_animated_gif": False,
                "is_animated_image": True,
                "gif_frame_count": len(durations),
                "gif_durations": durations,
            }, None

        converted_img = img.copy()

        # Conversion du mode CMYK/I/F → RGB
        if converted_img.mode in ("CMYK", "YCbCr", "I", "F"):
            old_img = converted_img
            converted_img = converted_img.convert("RGB")
            old_img.close()

        # LA/PA : ni BMP ni GIF ne savent les écrire — RGBA garde l'alpha.
        if converted_img.mode in ("LA", "PA"):
            old_img = converted_img
            converted_img = converted_img.convert("RGBA")
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
        elif target_format == "AVIF":
            converted_img.save(img_bytes, format=target_format, quality=quality)
        elif target_format == "GIF":
            # RGBA enregistré tel quel : Pillow le quantifie en palette en
            # gardant la transparence, que convert("P", ADAPTIVE) perdrait.
            if converted_img.mode not in ("P", "L", "RGBA"):
                old_img = converted_img
                converted_img = converted_img.convert("P", palette=Image.ADAPTIVE, colors=256)
                old_img.close()
            converted_img.save(img_bytes, format=target_format)
        elif target_format == "BMP":
            # L'alpha d'un BMP 32 bits n'est pas relu (voir
            # color_depth_tool_qt.py) : sans aplatissement, les couleurs
            # cachées sous les zones transparentes réapparaîtraient. Fond
            # blanc, comme pour JPEG.
            if converted_img.mode == "RGBA" or (
                    converted_img.mode == "P" and "transparency" in converted_img.info):
                rgba_temp = converted_img.convert("RGBA")
                rgb_img = Image.new("RGB", rgba_temp.size, (255, 255, 255))
                rgb_img.paste(rgba_temp, mask=rgba_temp.split()[-1])
                rgba_temp.close()
                converted_img.close()
                converted_img = rgb_img
            converted_img.save(img_bytes, format=target_format)
        elif target_format == "TIFF":
            tiff_kwargs = {}
            if source_dpi:
                tiff_kwargs["dpi"] = source_dpi
            converted_img.save(img_bytes, format=target_format, **tiff_kwargs)
        else:
            # PNG : pas de DPI via save()
            converted_img.save(img_bytes, format=target_format)

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


def detect_merge_adjustment(positions_data):
    """
    Détecte si une fusion 2D nécessitera un ajustement de taille, SANS fusionner.
    Permet de poser la question à l'utilisateur AVANT la fusion (UI non modale).

    Returns:
        (need_adjustment: bool, dimension_type: str, dimensions_list: list)
        Reproduit exactement la détection de merge_images_2d.
    """
    if not positions_data:
        return False, 'height', []

    items = []
    for i, pos_data in enumerate(positions_data):
        items.append({
            "idx": i,
            "img": pos_data["entry"]["img"],
            "x": pos_data["x"],
            "y": pos_data["y"]
        })

    align_threshold = 20
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
    for row in rows:
        row.sort(key=lambda item: item["x"])

    for row in rows:
        if len(row) > 1:
            heights = [item["img"].height for item in row]
            if len(set(heights)) > 1:
                return True, 'height', heights

    if len(rows) > 1:
        row_widths = []
        for row in rows:
            if len(row) == 1:
                row_widths.append(row[0]["img"].width)
            else:
                row_widths.append(sum(item["img"].width for item in row))
        if len(set(row_widths)) > 1:
            return True, 'width', row_widths

    return False, 'height', []


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

    for row in rows:
        row.sort(key=lambda item: item["x"])  # trie chaque ligne par X (gauche à droite)

    need_adjustment = False
    adjustment_mode = 'keep_original'
    dimension_type = 'height'
    dimensions_list = []

    # Hauteurs dans chaque ligne horizontale
    for row in rows:
        if len(row) > 1:
            heights = [item["img"].height for item in row]
            if len(set(heights)) > 1:
                need_adjustment = True
                dimension_type = 'height'
                dimensions_list = heights
                break

    # Largeurs entre les lignes
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
