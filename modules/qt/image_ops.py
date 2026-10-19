# -------------------------
# Opérations sur les images (logique data)
# -------------------------
import os
import io
from PIL import Image, ImageSequence
from modules.qt.entries import (
    ensure_image_loaded, save_image_to_bytes, free_image_memory, _webp_is_lossless, image_dpi,
    metadata_save_kwargs, page_icc_profile, to_display_mode, page_dpi, tiff_save_compression,
)


# Formats cibles de la conversion capables de stocker une animation.
_ANIMATABLE_FORMATS = ("WEBP", "PNG", "AVIF")


def transform_animated_gif(entry, frame_fn):
    """Applique frame_fn (Image RGBA -> Image) à chaque frame d'un GIF animé
    et retourne les bytes du GIF réassemblé, durées et boucle conservées.

    Sans ce traitement frame par frame, toute opération qui passe par
    ensure_image_loaded() ne voit que la première frame et aplatit
    l'animation en une image fixe.

    Les frames lues par Pillow sont des images déjà composées (pas les
    rectangles partiels du fichier d'origine). Une frame transformée qui
    tient dans une palette (256 couleurs au plus, transparence franche) est
    réécrite avec ces couleurs exactes (_exact_palette_frame) : quantifiée de
    nouveau par Pillow, chaque couleur se décalait (jusqu'à 15 niveaux sur
    255 après une simple rotation). Sans transparence, disposal=1 laisse
    Pillow n'écrire que la partie qui change d'une frame à l'autre ; avec de
    la transparence, chaque frame reste une image complète (disposal=2),
    sinon ses zones transparentes laisseraient voir la frame précédente. Le
    commentaire du GIF est gardé. Met aussi à jour img_width/img_height de
    l'entrée."""
    src = Image.open(io.BytesIO(entry["bytes"]))
    loop = src.info.get("loop")
    comment = src.info.get("comment")
    frames = []
    durations = []
    for frame in ImageSequence.Iterator(src):
        durations.append(frame.info.get("duration", 100))
        frames.append(frame_fn(frame.copy().convert("RGBA")))
    src.close()

    transparent = any(f.mode in ("RGBA", "LA", "PA") and f.getchannel("A").getextrema()[0] < 255
                      for f in frames)
    exact = [_exact_palette_frame(f) for f in frames]
    if all(p is not None for p in exact):
        for f in frames:
            f.close()
        frames = exact
    save_kwargs = {"save_all": True, "append_images": frames[1:],
                   "duration": durations, "disposal": 2 if transparent else 1}
    if loop is not None:
        save_kwargs["loop"] = loop
    if comment:
        save_kwargs["comment"] = comment
    out = io.BytesIO()
    frames[0].save(out, format="GIF", **save_kwargs)
    entry["img_width"], entry["img_height"] = frames[0].size
    for f in frames:
        f.close()
    return out.getvalue()


def _exact_palette_frame(frame):
    """Frame `frame` (RGBA après transformation) en image à palette avec ses
    couleurs exactes, None si elle ne tient pas dans une palette GIF : plus
    de 256 couleurs (une transparence comprise), ou transparence partielle
    (que le GIF ne stocke pas, Pillow l'arrondit lui-même)."""
    import numpy as np
    frame = frame.convert("RGBA")
    # Comptage rapide d'abord : plus de 257 valeurs RGBA ne tiennent jamais
    # dans une palette, inutile de trier tous les pixels
    if frame.getcolors(257) is None:
        return None
    rgba = np.asarray(frame)
    alpha = rgba[..., 3]
    if np.any((alpha > 0) & (alpha < 255)):
        return None
    opaque = alpha == 255
    keys = (rgba[..., 0].astype(np.uint32) << 16) | (rgba[..., 1].astype(np.uint32) << 8) | rgba[..., 2]
    colors, inverse = np.unique(np.where(opaque, keys, 0), return_inverse=True)
    has_transparency = not opaque.all()
    used = np.unique(keys[opaque]) if has_transparency else colors
    if len(used) + has_transparency > 256:
        return None
    lookup = {int(c): i for i, c in enumerate(used)}
    indexes = np.array([lookup.get(int(c), 0) for c in colors], np.uint8)[inverse]
    indexes = indexes.reshape(alpha.shape)
    palette = [v for c in used for v in ((int(c) >> 16) & 255, (int(c) >> 8) & 255, int(c) & 255)]
    if has_transparency:
        indexes[~opaque] = len(used)
        palette += [0, 0, 0]
    image = Image.fromarray(indexes)
    image.putpalette(palette)
    if has_transparency:
        image.info["transparency"] = len(used)
    return image


_ANIMATED_EXT_TO_FORMAT = {".webp": "WEBP", ".png": "PNG", ".avif": "AVIF"}


def transform_animated_image(entry, frame_fn):
    """Équivalent de transform_animated_gif pour un WebP/PNG/AVIF animé
    (entry["is_animated_image"]) : applique frame_fn à chaque frame et
    retourne les bytes réassemblés dans le format d'origine, durées et boucle
    conservées. Met aussi à jour img_width/img_height de l'entrée.

    entry doit porter "bytes" et "extension"."""
    save_format = _ANIMATED_EXT_TO_FORMAT[entry["extension"].lower()]
    src = Image.open(io.BytesIO(entry["bytes"]))
    loop = src.info.get("loop")
    frames = []
    durations = []
    for frame in ImageSequence.Iterator(src):
        # Copie d'abord : WebP/AVIF ne renseignent la durée qu'une fois la
        # frame décodée.
        frames.append(frame_fn(frame.copy().convert("RGBA")))
        durations.append(int(frame.info.get("duration") or 100))
    src.close()

    save_kwargs = {"save_all": True, "append_images": frames[1:], "duration": durations}
    if loop is not None:
        save_kwargs["loop"] = loop
    if save_format == "WEBP" and _webp_is_lossless(entry["bytes"]):
        # Un WebP sans perte reste sans perte : en mode avec perte, chaque
        # opération successive dégraderait toutes les frames.
        save_kwargs["lossless"] = True
    elif save_format in ("WEBP", "AVIF"):
        save_kwargs["quality"] = 100
    out = io.BytesIO()
    frames[0].save(out, format=save_format, **save_kwargs)
    entry["img_width"], entry["img_height"] = frames[0].size
    for f in frames:
        f.close()
    return out.getvalue()


def transform_multipage_tiff(entry, frame_fn):
    """Applique frame_fn à chaque page d'un TIFF de plusieurs pages
    (entry["is_multipage_tiff"]) et retourne les bytes du TIFF réassemblé,
    compression et DPI conservés. Les modes que frame_fn ne sait pas traiter
    tels quels (palette, 1 bit, 16 bits…) passent en RGB/RGBA. Met aussi à jour
    img_width/img_height de l'entrée."""
    src = Image.open(io.BytesIO(entry["bytes"]))
    dpi = image_dpi(src)
    pages = []
    for page in ImageSequence.Iterator(src):
        frame = page.copy()
        if frame.mode not in ("RGB", "RGBA", "L", "LA", "CMYK"):
            frame = frame.convert("RGBA" if "A" in frame.getbands() or frame.mode == "P" else "RGB")
        pages.append(frame_fn(frame))
    src.close()

    # Compression d'origine, remplacée par LZW si elle ne convient pas au
    # mode produit (une page 1 bit Group 4 passée en RGB) : sans valeur
    # explicite, Pillow reprendrait celle de img.info, et l'écriture échouait
    # toujours, ou faisait planter le programme
    save_kwargs = {"save_all": True, "append_images": pages[1:],
                   "compression": tiff_save_compression(pages[0], entry["bytes"])}
    if dpi:
        save_kwargs["dpi"] = dpi
    out = io.BytesIO()
    pages[0].save(out, format="TIFF", **save_kwargs)
    entry["img_width"], entry["img_height"] = pages[0].size
    for p in pages:
        p.close()
    return out.getvalue()


def has_several_frames(entry):
    """Vrai pour une image dont les bytes contiennent plusieurs images : image
    animée (is_animated_entry) ou TIFF de plusieurs pages. Les opérations qui
    réécrivent l'image doivent alors la traiter frame par frame
    (transform_animated_entry), sans quoi seule la première serait conservée."""
    return is_animated_entry(entry) or bool(entry.get("is_multipage_tiff"))


def transform_animated_entry(entry, frame_fn):
    """Applique frame_fn à chaque frame d'une image à plusieurs frames, quel
    que soit son format, et retourne les bytes réassemblés :
    transform_animated_gif pour un GIF, transform_multipage_tiff pour un TIFF
    de plusieurs pages, transform_animated_image pour un WebP/PNG/AVIF."""
    if entry.get("is_animated_gif"):
        return transform_animated_gif(entry, frame_fn)
    if entry.get("is_multipage_tiff"):
        return transform_multipage_tiff(entry, frame_fn)
    return transform_animated_image(entry, frame_fn)


def rotate_entry_data(entry, angle, state=None):
    """Fait pivoter une entrée image du nombre de degrés spécifié.
    angle: -90 pour rotation droite (horaire), 90 pour rotation gauche (anti-horaire).
    Retourne True si la rotation a été effectuée, False sinon."""
    if not entry["is_image"]:
        return False

    if has_several_frames(entry) and entry.get("bytes") and not entry.get("is_corrupted"):
        free_image_memory(entry)
        entry["bytes"] = transform_animated_entry(entry, lambda f: f.rotate(angle, expand=True))
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

    if has_several_frames(entry) and entry.get("bytes") and not entry.get("is_corrupted"):
        free_image_memory(entry)
        entry["bytes"] = transform_animated_entry(entry, lambda f: f.transpose(method))
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


# Plus grande largeur/hauteur que chaque format peut écrire ET relire. Au-delà,
# l'écriture échoue (WebP, JPEG, GIF) ou, pire, produit un fichier que Pillow
# ne sait plus ouvrir (AVIF au-delà de 32768 px). PNG, BMP et TIFF : pas de
# limite atteignable par une page.
FORMAT_MAX_SIZE = {"WEBP": 16383, "AVIF": 32768, "JPEG": 65500, "GIF": 65535}


def _to_writable_mode(img):
    """Ramène les modes qu'aucun format cible ne sait écrire (ou pas tous) à un
    mode courant, sans écrêter : 16 bits (I;16*) divisé par 256, entier 32 bits
    ou flottant (I, F) remis à l'échelle 0-255 s'il en sort, CMYK en RGB par
    son profil ICC (cmyk_to_rgb), YCbCr/LAB/HSV en RGB. Retourne une nouvelle
    image, ou img elle-même si rien à faire."""
    mode = img.mode
    if mode.startswith("I;16"):
        return img.convert("I").point(lambda v: v / 256).convert("L")
    if mode in ("I", "F"):
        lo, hi = img.getextrema()
        if lo < 0:
            # Valeurs négatives : étirement de [lo, hi] sur 0-255
            scale = 255.0 / (hi - lo) if hi > lo else 1.0
            img = img.point(lambda v: (v - lo) * scale)
        elif hi > 255:
            # Valeurs positives : mise à l'échelle depuis 0, qui garde les
            # niveaux relatifs (une page uniforme reste claire, pas noire)
            scale = 255.0 / hi
            img = img.point(lambda v: v * scale)
        return img.convert("L")
    if mode == "CMYK":
        return cmyk_to_rgb(img)
    if mode in ("YCbCr", "LAB", "HSV"):
        return img.convert("RGB")
    return img


def cmyk_to_rgb(img):
    """Image CMYK en RGB, en passant par son profil ICC intégré s'il en a un
    (conversion vers sRGB par LittleCMS) : sans lui, la conversion naïve de
    Pillow donne des couleurs fausses (noirs délavés, teintes décalées) sur les
    scans d'impression. Repli sur la conversion naïve sans profil ou si le
    profil est inutilisable."""
    icc = img.info.get("icc_profile")
    if icc:
        try:
            from PIL import ImageCms
            src_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst_profile = ImageCms.createProfile("sRGB")
            return ImageCms.profileToProfile(img, src_profile, dst_profile, outputMode="RGB")
        except Exception:
            pass  # repli : conversion sans profil ci-dessous
    return img.convert("RGB")


def _animation_loop_for(entry, src_loop, target_format):
    """Nombre de répétitions à écrire pour une animation convertie, ou None
    pour ne rien écrire. Les formats ne comptent pas de la même façon : dans
    un GIF, l'absence de valeur veut dire « jouée une fois » et loop=N
    « répétée N fois » ; en WebP et en PNG animé, loop est le nombre de
    lectures (1 = une fois) et, absent, Pillow écrit 0 (en boucle). Un GIF
    joué une fois devenait donc une boucle infinie. 0 vaut « en boucle »
    partout. L'AVIF n'enregistre pas cette valeur."""
    if target_format not in ("WEBP", "PNG"):
        return src_loop
    if entry.get("is_animated_gif"):
        if src_loop is None:
            return 1
        return 0 if src_loop == 0 else src_loop + 1
    return src_loop


def convert_image_data(entry, target_format, quality, lossless=False):
    """Convertit une entrée image vers un nouveau format.
    Retourne (new_entry_dict, None) en cas de succès, (None, failure) en cas
    d'échec. new_entry_dict contient les clés data (pas de tk_img).

    lossless : WebP sans perte (image identique pixel pour pixel ; `quality`
    n'y règle plus que l'effort de compression). Ignoré pour les autres
    formats : PNG, TIFF, BMP et GIF sont toujours sans perte, JPEG et AVIF
    (avec la version de Pillow embarquée) ne savent pas l'être.

    failure : dict décrivant la cause, pour un message compréhensible par
    l'utilisateur — "kind" vaut "too_large" (dimensions au-delà de
    FORMAT_MAX_SIZE : "width", "height", "max"), "unreadable" (image illisible
    ou endommagée), "out_of_memory" ("width", "height") ou "unexpected"
    ("error" : message technique) ; "name" = nom de la page."""
    name = entry.get("orig_name", "")
    width = height = None
    try:
        img = ensure_image_loaded(entry)
        if img is None:
            return None, {"kind": "unreadable", "name": name}
        width, height = img.size

        max_size = FORMAT_MAX_SIZE.get(target_format)
        if max_size is not None and (width > max_size or height > max_size):
            free_image_memory(entry)
            return None, {"kind": "too_large", "name": name,
                          "width": width, "height": height, "max": max_size}

        # Arrondi, jamais tronqué : un PNG à 300 dpi est relu à 299,9994
        _raw_dpi = page_dpi(entry, img)
        source_dpi = (round(_raw_dpi[0]), round(_raw_dpi[1])) if _raw_dpi else None

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
            loop = _animation_loop_for(entry, loop, target_format)
            if loop is not None:
                save_kwargs["loop"] = loop
            if target_format in ("WEBP", "AVIF"):
                save_kwargs["quality"] = quality
            if target_format == "WEBP" and lossless:
                save_kwargs["lossless"] = True
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

        # Modes 16/32 bits, flottant, CMYK/YCbCr/LAB/HSV : ramenés à un mode
        # que tous les formats cibles savent écrire — sauf le gris 16 bits
        # (I;16*) vers PNG ou TIFF, qui le stockent tel quel : le réduire à
        # 256 nuances serait une perte inutile.
        if converted_img.mode.startswith("I;16") and target_format in ("PNG", "TIFF"):
            writable = converted_img
        else:
            writable = _to_writable_mode(converted_img)
        if writable is not converted_img:
            converted_img.close()
            converted_img = writable

        # LA/PA : ni BMP ni GIF ne savent les écrire — RGBA garde l'alpha.
        if converted_img.mode in ("LA", "PA"):
            old_img = converted_img
            converted_img = converted_img.convert("RGBA")
            old_img.close()

        # Conversion en bytes selon le format cible. Profil ICC (s'il
        # correspond au mode écrit) et métadonnées de la page d'origine
        # reportés dans tous les formats qui savent les stocker ; l'image
        # convertie vient d'ensure_image_loaded, déjà dans le sens
        # d'affichage : l'EXIF est réécrit sans l'orientation.
        img_bytes = io.BytesIO()
        meta_kwargs = metadata_save_kwargs(target_format, entry.get("bytes"))

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
            jpeg_kwargs = {"quality": quality, "optimize": True, **meta_kwargs}
            if source_dpi:
                jpeg_kwargs["dpi"] = source_dpi
            icc = page_icc_profile(converted_img, entry.get("bytes"))
            if icc:
                jpeg_kwargs["icc_profile"] = icc
            converted_img.save(img_bytes, format=target_format, **jpeg_kwargs)
        elif target_format == "WEBP":
            # WebP ne stocke que RGB/RGBA : seul un profil RGB peut y aller
            webp_kwargs = {"quality": quality, **meta_kwargs}
            if lossless:
                webp_kwargs["lossless"] = True
            icc = page_icc_profile(converted_img, entry.get("bytes"), "RGB")
            if icc:
                webp_kwargs["icc_profile"] = icc
            converted_img.save(img_bytes, format=target_format, **webp_kwargs)
        elif target_format == "AVIF":
            # icc_profile toujours passé, même None : sinon Pillow reprend
            # celui de img.info, même s'il ne correspond plus au mode
            converted_img.save(img_bytes, format=target_format, quality=quality,
                               icc_profile=page_icc_profile(converted_img, entry.get("bytes")),
                               **meta_kwargs)
        elif target_format == "GIF":
            # RGBA enregistré tel quel : Pillow le quantifie en palette en
            # gardant la transparence, que convert("P", ADAPTIVE) perdrait.
            # 1 bit : la quantification ADAPTIVE le refuse, il passe en L.
            if converted_img.mode == "1":
                old_img = converted_img
                converted_img = converted_img.convert("L")
                old_img.close()
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
            # Pillow n'écrit pas la transparence d'une palette en TIFF : RGBA
            # la garde (GIF ou PNG à palette transparent)
            if converted_img.mode == "P" and "transparency" in converted_img.info:
                old_img = converted_img
                converted_img = converted_img.convert("RGBA")
                old_img.close()
            tiff_kwargs = {"icc_profile": page_icc_profile(converted_img, entry.get("bytes")),
                           **meta_kwargs}
            if source_dpi:
                tiff_kwargs["dpi"] = source_dpi
            # Compression sans perte : sans elle, le TIFF produit était
            # écrit sans compression, bien plus lourd que l'image d'origine
            tiff_kwargs["compression"] = tiff_save_compression(
                converted_img, entry.get("bytes"), default="tiff_lzw")
            converted_img.save(img_bytes, format=target_format, **tiff_kwargs)
        else:
            png_kwargs = {"icc_profile": page_icc_profile(converted_img, entry.get("bytes")),
                          **meta_kwargs}
            if source_dpi:
                png_kwargs["dpi"] = source_dpi
            converted_img.save(img_bytes, format=target_format, **png_kwargs)

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

    except MemoryError:
        free_image_memory(entry)
        return None, {"kind": "out_of_memory", "name": name,
                      "width": width, "height": height}
    except Exception as e:
        free_image_memory(entry)
        return None, {"kind": "unexpected", "name": name,
                      "error": str(e) or type(e).__name__}


def _has_alpha(img):
    """True si img a un canal alpha ou une couleur de palette transparente."""
    return img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)


def _merge_source(img, keep_alpha):
    """img prête à être collée dans une planche de jointure de mode
    "RGBA" (keep_alpha) ou "RGB". Collée sans masque, une image transparente
    sur une planche RGB laisserait voir les couleurs cachées sous ses zones
    transparentes : sans keep_alpha, elle est aplatie sur blanc. Nuances 16
    bits ramenées à 256 (un convert direct rendrait la page blanche)."""
    img = to_display_mode(img)
    if keep_alpha:
        return img.convert("RGBA")
    if _has_alpha(img):
        return _flatten_on_white(img)
    return img.convert("RGB")


def _merge_canvas(size, keep_alpha):
    """Planche de jointure : fond blanc opaque (zones couvertes par aucune
    image), RGBA si la transparence des images est gardée. Les images y sont
    collées sans masque : leurs pixels, alpha compris, remplacent le fond."""
    return Image.new("RGBA" if keep_alpha else "RGB", size, "white")


def merge_images_vertically(images_list, adjustment_mode='keep_original', keep_alpha=False):
    """
    Fusionne plusieurs images verticalement sans perte de qualité.

    Args:
        images_list: Liste des images PIL à fusionner
        adjustment_mode: 'keep_original', 'enlarge_small', ou 'reduce_large'
        keep_alpha: garder la transparence des images (planche RGBA), sinon
                    images aplaties sur blanc
    """
    if not images_list:
        return None
    images_list = [_merge_source(img, keep_alpha) for img in images_list]

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
    merged_img = _merge_canvas((target_width, total_height), keep_alpha)

    y_offset = 0
    for img in adjusted_images:
        x_offset = (target_width - img.width) // 2
        merged_img.paste(img, (x_offset, y_offset))
        y_offset += img.height

    return merged_img


def merge_images_horizontally(images_list, adjustment_mode='keep_original', keep_alpha=False):
    """
    Fusionne plusieurs images horizontalement sans perte de qualité.

    Args:
        images_list: Liste des images PIL à fusionner
        adjustment_mode: 'keep_original', 'enlarge_small', ou 'reduce_large'
        keep_alpha: voir merge_images_vertically
    """
    if not images_list:
        return None
    images_list = [_merge_source(img, keep_alpha) for img in images_list]

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
    merged_img = _merge_canvas((total_width, target_height), keep_alpha)

    x_offset = 0
    for img in adjusted_images:
        y_offset = (target_height - img.height) // 2
        merged_img.paste(img, (x_offset, y_offset))
        x_offset += img.width

    return merged_img


# Écart toléré entre les durées de deux frames : le GIF stocke ses durées par
# pas de 10 ms, le WebP/PNG/AVIF à la milliseconde, une même animation lue
# dans deux formats peut donc différer de quelques ms.
_JOIN_DURATION_TOLERANCE_MS = 10


def is_animated_entry(entry):
    """Vrai pour une image à plusieurs frames, quel que soit son format
    (GIF animé ou WebP/PNG/AVIF animé)."""
    return bool(entry.get("is_animated_gif") or entry.get("is_animated_image"))


def animations_joinable(entries):
    """Indique si des images peuvent être jointes du point de vue de
    l'animation : aucune animée, ou toutes animées avec le même nombre de
    frames et les mêmes durées frame par frame (à la tolérance près).
    Une image animée ne se joint jamais à une image fixe. Un TIFF de plusieurs
    pages ne se joint jamais : la jointure n'en garderait que la première page.

    Lit uniquement gif_frame_count/gif_durations posés au chargement, sans
    relire les bytes : appelée à chaque construction de menu et rafraîchissement
    de la colonne d'icônes."""
    if any(e.get("is_multipage_tiff") for e in entries):
        return False
    animated = [is_animated_entry(e) for e in entries]
    if not any(animated):
        return True
    if not all(animated):
        return False
    ref = entries[0]
    ref_count = ref.get("gif_frame_count", 0)
    ref_durations = ref.get("gif_durations") or []
    if len(ref_durations) != ref_count:
        return False
    for e in entries[1:]:
        durations = e.get("gif_durations") or []
        if e.get("gif_frame_count", 0) != ref_count or len(durations) != ref_count:
            return False
        if any(abs((d or 0) - (r or 0)) > _JOIN_DURATION_TOLERANCE_MS
               for d, r in zip(durations, ref_durations)):
            return False
    return True


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


def merge_images_2d(positions_data, ask_adjustment_func=None, keep_alpha=False):
    """
    Fusionne plusieurs images selon leur disposition 2D exacte.
    Détecte automatiquement les différences de dimensions et propose un dialogue si nécessaire.

    Args:
        positions_data: Données de position des images (list de dicts avec 'entry', 'x', 'y')
        ask_adjustment_func: Callback(dimension_type, dimensions_list) → mode ou None si annulé.
                             Si None, utilise 'keep_original'.
        keep_alpha: garder la transparence des images (planche RGBA, à
                    enregistrer dans un format qui la stocke) ; sinon images
                    aplaties sur blanc. Dans les deux cas, les zones de la
                    planche couvertes par aucune image sont blanches.

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
            row_img = _merge_source(row[0]["img"], keep_alpha)
        else:
            row_img = merge_images_horizontally([item["img"] for item in row], adjustment_mode,
                                                keep_alpha)
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
        merged_img = _merge_canvas((int(max_width), int(total_height)), keep_alpha)
        y_offset = 0
        for rd in row_data:
            x_offset = rd["start_x_real"] - min_x_real
            merged_img.paste(rd["img"], (x_offset, y_offset))
            y_offset += rd["img"].height
        return merged_img

    min_x_real = min(rd["start_x_real"] for rd in row_data)
    max_width = max(rd["start_x_real"] - min_x_real + rd["img"].width for rd in row_data)
    total_height = sum(rd["img"].height for rd in row_data)

    merged_img = _merge_canvas((int(max_width), int(total_height)), keep_alpha)

    y_offset = 0
    for rd in row_data:
        x_offset = rd["start_x_real"] - min_x_real
        merged_img.paste(rd["img"], (x_offset, y_offset))
        y_offset += rd["img"].height

    return merged_img


def _flatten_on_white(img):
    """Frame composée sur fond blanc opaque, comme le fond de l'assemblage :
    collée telle quelle, une frame RGBA laisserait voir les couleurs cachées
    sous ses zones transparentes."""
    rgba = img.convert("RGBA")
    flat = Image.new("RGB", rgba.size, (255, 255, 255))
    flat.paste(rgba, mask=rgba.getchannel("A"))
    rgba.close()
    return flat


def merge_animated_2d(sources, durations, adjustment_mode, save_format,
                      progress_cb=None, is_cancelled=None):
    """Joint des images animées frame par frame selon leur disposition 2D.

    Les sources doivent avoir été validées par animations_joinable : la frame
    n de chaque source est assemblée avec la frame n des autres, par le même
    algorithme que la jointure fixe (merge_images_2d, mode d'ajustement déjà
    choisi).

    Args:
        sources: list de dicts {"bytes", "x", "y"} (positions du canvas de
                 disposition). Les bytes sont une copie prise sur le thread UI :
                 la fonction tourne dans un worker.
        durations: durées des frames (ms), celles de la première source.
        adjustment_mode: 'keep_original' | 'enlarge_small' | 'reduce_large'.
        save_format: "GIF" | "WEBP" | "PNG" | "AVIF".
        progress_cb: callable(done, total) appelé après chaque frame.
        is_cancelled: callable() -> bool, vérifié avant chaque frame.

    Returns:
        bytes de l'image animée, ou None si annulé."""
    opened = [Image.open(io.BytesIO(s["bytes"])) for s in sources]
    frames = []
    try:
        loop = opened[0].info.get("loop", 0)
        frame_count = len(durations)
        for i in range(frame_count):
            if is_cancelled and is_cancelled():
                return None
            frame_positions = []
            for src, s in zip(opened, sources):
                src.seek(min(i, getattr(src, "n_frames", 1) - 1))
                frame_positions.append({"entry": {"img": _flatten_on_white(src)},
                                        "x": s["x"], "y": s["y"]})
            frames.append(merge_images_2d(frame_positions,
                                          ask_adjustment_func=lambda dt, dl: adjustment_mode))
            for fp in frame_positions:
                fp["entry"]["img"].close()
            if progress_cb:
                progress_cb(i + 1, frame_count)
        if is_cancelled and is_cancelled():
            return None

        save_kwargs = {"save_all": True, "append_images": frames[1:],
                       "duration": list(durations), "loop": loop}
        if save_format in ("WEBP", "AVIF"):
            save_kwargs["quality"] = 95
        out = io.BytesIO()
        frames[0].save(out, format=save_format, **save_kwargs)
        return out.getvalue()
    finally:
        for src in opened:
            src.close()
        for f in frames:
            f.close()
