"""
modules/qt/pdf_page_images.py — Images intégrées d'un PDF.

Mode « original » (résolution 0) : résolution de la meilleure image d'une page,
et décision d'extraire cette image telle quelle au lieu de rendre la page.

Mode « images brutes » (original_images) : seules les images du PDF sont
reprises, telles qu'enregistrées, sans la mise en page ; sur demande, une
image coupée entre deux pages est recollée quand la découpe est certaine.

Sans Qt, appelé dans le process PDF dédié (pdf_loading_qt._pdf_persistent_process)
par l'ouverture, la fusion et le lot PDF → CBZ. Les objets `doc` et `page` sont
ceux de PyMuPDF.
"""

import io
import re
from collections import Counter

from PIL import Image, ImageChops, ImageStat

# Écart toléré entre le rectangle de l'image et celui de la page (points PDF) :
# arrondis des transformations écrites par les logiciels de création de PDF.
_PAGE_COVER_TOLERANCE = 2.0

# Formats d'image natifs que la mosaïque sait afficher tels quels.
_RAW_EXTS = ("jpeg", "jpg", "png", "webp")


def best_page_image(page, image_list):
    """(résolution en dpi, xref) de l'image de plus haute résolution parmi
    `image_list` (page.get_images(full=True)), (0, None) s'il n'y en a pas.

    La résolution se calcule sur la page non tournée (page.cropbox) : les
    pixels de l'image suivent le contenu de la page, pas son affichage. Avec
    page.rect, une page tournée de 90° comparait la largeur de l'image à la
    hauteur de la page."""
    box = page.cropbox
    width_in = box.width / 72.0
    height_in = box.height / 72.0
    max_dpi, best_xref = 0, None
    if width_in <= 0 or height_in <= 0:
        return max_dpi, best_xref
    for img_info in image_list:
        try:
            # Dimensions sans décompression : img_info[2]=largeur, img_info[3]=hauteur
            img_width, img_height = img_info[2], img_info[3]
            if img_width > 0 and img_height > 0:
                img_dpi = max(img_width / width_in, img_height / height_in)
                if img_dpi > max_dpi:
                    max_dpi, best_xref = img_dpi, img_info[0]
        except Exception:
            continue
    return max_dpi, best_xref


def _has_key(doc, xref, key):
    """True si le dictionnaire de l'objet xref porte la clé `key`."""
    return doc.xref_get_key(xref, key)[0] != "null"


def raw_page_image(doc, page, image_list, xref):
    """Bytes de l'image `xref` tels qu'enregistrés dans le PDF, quand ils
    sont exactement ce que la page affiche ; None sinon (la page est alors
    rendue).

    L'image intégrée ne contient qu'elle-même : tout ce que la page dessine
    par-dessus ou autour serait perdu. Elle n'est donc reprise que si elle
    est le seul contenu de la page :
    - une seule image, dessinée une seule fois, droite (ni tournée ni
      retournée) et couvrant toute la page ;
    - aucun texte, visible ou non (lettrage des bulles d'une BD numérique,
      couche OCR d'un scan), aucun tracé vectoriel (cases, bordures),
      aucune annotation ni champ de formulaire ;
    - page sans rotation (/Rotate) ;
    - image sans masque de transparence (SMask, Mask, ImageMask) ni tableau
      /Decode (couleurs inversées) : les bytes extraits ne les appliquent pas ;
    - format JPEG, PNG ou WebP.
    Un profil ICC rangé à part dans le PDF (/ICCBased) est ajouté aux bytes
    extraits (_with_icc_profile), sauf un profil sRGB (_is_srgb)."""
    if xref is None or len(image_list) != 1 or page.rotation:
        return None
    if page.get_text().strip() or page.get_drawings():
        return None
    if page.first_annot is not None or page.first_widget is not None:
        return None
    placements = page.get_image_info(xrefs=True)
    if len(placements) != 1 or placements[0].get("xref") != xref:
        return None
    a, b, c, d, _e, _f = placements[0]["transform"]
    if a <= 0 or d <= 0 or abs(b) > 1e-6 or abs(c) > 1e-6:
        return None
    x0, y0, x1, y1 = placements[0]["bbox"]
    rect = page.rect
    if (abs(x0 - rect.x0) > _PAGE_COVER_TOLERANCE or abs(y0 - rect.y0) > _PAGE_COVER_TOLERANCE
            or abs(x1 - rect.x1) > _PAGE_COVER_TOLERANCE or abs(y1 - rect.y1) > _PAGE_COVER_TOLERANCE):
        return None
    if any(_has_key(doc, xref, key) for key in ("SMask", "Mask", "ImageMask", "Decode")):
        return None
    extracted = doc.extract_image(xref)
    if not extracted or extracted.get("smask"):
        return None
    ext = extracted.get("ext", "").lower()
    if ext not in _RAW_EXTS:
        return None
    icc = _image_icc_profile(doc, xref, extracted.get("colorspace"))
    if icc:
        return _with_icc_profile(extracted["image"], ext, icc)
    return extracted["image"]


_ICC_BASED = re.compile(r"/ICCBased\s+(\d+)\s+0\s+R")


def _image_icc_profile(doc, xref, components):
    """Profil ICC de l'espace de couleur de l'image `xref` (/ICCBased), None
    s'il n'en a pas ou si son nombre de composantes (/N) n'est pas celui de
    l'image. Un PDF range souvent le profil à part de l'image : les bytes
    extraits n'en portent pas, et les couleurs d'une image Adobe RGB ou
    Display P3 seraient lues comme du sRGB."""
    kind, value = doc.xref_get_key(xref, "ColorSpace")
    if kind == "xref":
        try:
            value = doc.xref_object(int(value.split()[0]), compressed=True)
        except (ValueError, IndexError):
            return None
    match = _ICC_BASED.search(value or "")
    if not match:
        return None
    profile_xref = int(match.group(1))
    n_kind, n_value = doc.xref_get_key(profile_xref, "N")
    if components and n_kind == "int" and int(n_value) != components:
        return None
    try:
        icc = doc.xref_stream(profile_xref)
    except Exception:
        return None
    if not icc or _is_srgb(icc):
        return None
    return icc


def _is_srgb(icc):
    """True si le profil `icc` est un profil sRGB. Inutile de l'ajouter :
    une image sans profil est lue en sRGB. Beaucoup de logiciels (PyMuPDF
    compris) déclarent un profil sRGB pour toute image d'un PDF : l'ajouter
    changerait les bytes d'une image extraite telle quelle, pour rien."""
    try:
        from PIL import ImageCms
        description = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc)))
    except Exception:
        return False
    return "srgb" in (description or "").lower()


def _with_icc_profile(data, ext, icc):
    """Bytes de l'image `data` (format `ext`) portant le profil `icc`, sans
    réencodage avec perte : segments APP2 ICC_PROFILE insérés dans un JPEG,
    PNG réécrit sans perte. Inchangés s'ils portent déjà un profil, ou pour
    un autre format."""
    if ext in ("jpeg", "jpg"):
        return _jpeg_with_icc(data, icc)
    if ext == "png":
        try:
            with Image.open(io.BytesIO(data)) as img:
                if img.info.get("icc_profile"):
                    return data
                img.load()
                out = io.BytesIO()
                img.save(out, format="PNG", icc_profile=icc)
            return out.getvalue()
        except Exception:
            return data
    return data


# Charge utile d'un segment APP2 ICC_PROFILE : 65 535 octets, moins la
# longueur (2), l'identifiant « ICC_PROFILE\0 » (12), le numéro et le nombre
# de segments (2).
_ICC_CHUNK = 65519


def _jpeg_with_icc(data, icc):
    """JPEG `data` avec le profil `icc` en segments APP2, placés après les
    segments APP0 (JFIF doit rester le premier) ; inchangé s'il porte déjà un
    profil ou n'est pas un JPEG lisible."""
    if data[:2] != b"\xff\xd8":
        return data
    pos = 2
    while pos + 4 <= len(data) and data[pos] == 0xFF:
        marker = data[pos + 1]
        length = int.from_bytes(data[pos + 2:pos + 4], "big")
        if marker == 0xE2 and data[pos + 4:pos + 16] == b"ICC_PROFILE\x00":
            return data
        if marker != 0xE0:
            break
        pos += 2 + length
    chunks = [icc[i:i + _ICC_CHUNK] for i in range(0, len(icc), _ICC_CHUNK)]
    if len(chunks) > 255:
        return data
    segments = b"".join(
        b"\xff\xe2" + (len(chunk) + 16).to_bytes(2, "big") + b"ICC_PROFILE\x00"
        + bytes([index + 1, len(chunks)]) + chunk
        for index, chunk in enumerate(chunks))
    return data[:pos] + segments + data[pos:]


# ---------------------------------------------------------------------------
# Mode « images brutes » : les images du PDF sans la mise en page
# ---------------------------------------------------------------------------
# Certains PDF (conversions automatiques d'un EPUB, par exemple) entourent
# chaque image de marges et la coupent en deux quand elle dépasse le bas de la
# page : le haut sur une page, le reste en haut de la suivante. Les morceaux
# sont alors deux images distinctes du PDF, que ce mode recolle.

# Écart toléré entre deux positions comparées (points PDF).
_STITCH_POS_TOLERANCE = 1.0
# Écart relatif toléré entre deux échelles (pixels par point).
_STITCH_SCALE_TOLERANCE = 0.01
# Écart moyen maximal (sur 255) entre la dernière ligne de pixels d'un morceau
# et la première du suivant. Le raccord d'une vraie découpe reste sous 31 sur
# les PDF éprouvés ; au-delà de ce seuil, les bords sont franchement
# incompatibles (bande noire sous une page blanche).
_SEAM_MAX_DIFF = 80


def image_placements(doc, on_page=None):
    """Images dessinées par les pages de `doc`, dans l'ordre de lecture :
    page par page, puis de haut en bas et de gauche à droite. Une image
    dessinée plusieurs fois donne une entrée par dessin.

    Chaque entrée : page (index), xref (0 pour une image intégrée au contenu
    de la page), width / height (pixels), bbox (points), transform, first /
    last (première / dernière image de sa page).

    on_page(done, total) est appelée avant chaque page lue (`done` pages déjà
    lues) : ce relevé lit le contenu de toutes les pages avant la première
    image rendue (une vingtaine de secondes pour 800 pages), sa progression
    doit se voir."""
    placements = []
    for index in range(doc.page_count):
        if on_page is not None:
            on_page(index, doc.page_count)
        infos = [info for info in doc[index].get_image_info(xrefs=True)
                 if info["width"] > 0 and info["height"] > 0]
        infos.sort(key=lambda info: (round(info["bbox"][1], 1), round(info["bbox"][0], 1)))
        for position, info in enumerate(infos):
            placements.append({
                "page": index, "xref": info.get("xref") or 0,
                "width": info["width"], "height": info["height"],
                "bbox": tuple(info["bbox"]), "transform": tuple(info["transform"]),
                "first": position == 0, "last": position == len(infos) - 1,
            })
    return placements


def content_area(placements):
    """(haut, bas) de la zone où les pages dessinent leurs images : positions
    les plus fréquentes du haut et du bas des images, en points. None sans
    image. Les marges ajoutées par la conversion sont les mêmes sur toutes
    les pages : une image coupée touche le bas de cette zone, et son reste en
    occupe le haut."""
    if not placements:
        return None
    top = Counter(round(p["bbox"][1]) for p in placements).most_common(1)[0][0]
    bottom = Counter(round(p["bbox"][3]) for p in placements).most_common(1)[0][0]
    return top, bottom


def _straight(placement):
    a, b, c, d = placement["transform"][:4]
    return a > 0 and d > 0 and abs(b) < 1e-6 and abs(c) < 1e-6


def _same_scale(x, y):
    return abs(x - y) <= _STITCH_SCALE_TOLERANCE * max(abs(x), abs(y))


def continues(upper, lower, area):
    """True si l'image `lower` peut être la suite de `upper`, coupée en bas
    de sa page : `upper` est la dernière image de sa page et descend jusqu'au
    bas de la zone, `lower` est la première de la page suivante et commence
    en haut de la zone ; mêmes largeur en pixels et position horizontale,
    images droites, même échelle dans les deux sens (une image entière
    écrasée pour tenir dans la page n'est pas un morceau)."""
    if area is None or not upper["xref"] or not lower["xref"]:
        return False
    top, bottom = area
    if lower["page"] != upper["page"] + 1 or not upper["last"] or not lower["first"]:
        return False
    if abs(upper["bbox"][3] - bottom) > _STITCH_POS_TOLERANCE:
        return False
    if abs(lower["bbox"][1] - top) > _STITCH_POS_TOLERANCE:
        return False
    if upper["width"] != lower["width"]:
        return False
    if (abs(upper["bbox"][0] - lower["bbox"][0]) > _STITCH_POS_TOLERANCE
            or abs(upper["bbox"][2] - lower["bbox"][2]) > _STITCH_POS_TOLERANCE):
        return False
    if not (_straight(upper) and _straight(lower)):
        return False
    scales = [scale for p in (upper, lower)
              for scale in (p["width"] / p["transform"][0], p["height"] / p["transform"][3])]
    return all(_same_scale(scales[0], scale) for scale in scales[1:])


def _ends_before_bottom(placement, area):
    """True si l'image s'arrête avant le bas de la zone : c'est le cas du
    dernier morceau d'une image coupée. Une image qui descend jusqu'en bas
    est une image entière (ou un morceau dont la suite manque) : sans cette
    règle, les pages d'un PDF dont chaque image remplit exactement la zone
    seraient toutes recollées en une seule."""
    return placement["bbox"][3] < area[1] - _STITCH_POS_TOLERANCE


def seams_match(upper_data, lower_data):
    """True si la dernière ligne de pixels de l'image `upper_data` et la
    première de `lower_data` (bytes, même largeur) se raccordent : écart
    moyen sous _SEAM_MAX_DIFF."""
    with Image.open(io.BytesIO(upper_data)) as upper, Image.open(io.BytesIO(lower_data)) as lower:
        if upper.width != lower.width:
            return False
        upper_row = upper.convert("RGB").crop((0, upper.height - 1, upper.width, upper.height))
        lower_row = lower.convert("RGB").crop((0, 0, lower.width, 1))
    diff = ImageStat.Stat(ImageChops.difference(upper_row, lower_row)).mean
    return sum(diff) / len(diff) <= _SEAM_MAX_DIFF


def placement_dpi(placement):
    """Résolution de l'image telle que la page la dessine (pixels par pouce
    en largeur), au moins 1."""
    width_pt = placement["bbox"][2] - placement["bbox"][0]
    if width_pt <= 0:
        return 72
    return max(1, round(placement["width"] * 72.0 / width_pt))


def placement_image(doc, placement, xref_png):
    """Bytes de l'image d'un placement.

    - Image enregistrée en JPEG, PNG ou WebP, sans masque ni tableau
      /Decode : ses bytes tels quels, avec le profil ICC rangé à part dans le
      PDF (comme raw_page_image).
    - Masque de transparence (SMask) : PNG avec la transparence.
    - Autre format (JPEG 2000, JBIG2, fax…), /Mask, /ImageMask ou /Decode :
      PNG, sans perte, rendu par `xref_png(xref)` (bytes PNG de l'image
      décodée par PyMuPDF, qui applique /Decode ; fourni par le process PDF,
      seul autorisé à utiliser PyMuPDF directement).
    - Image intégrée au contenu de la page (xref 0, sans objet propre) : la
      zone de la page qu'elle occupe, rendue à sa résolution."""
    xref = placement["xref"]
    if not xref:
        page = doc[placement["page"]]
        pix = page.get_pixmap(dpi=placement_dpi(placement), clip=placement["bbox"])
        return pix.tobytes("png")
    extracted = doc.extract_image(xref) or {}
    smask = extracted.get("smask") or 0
    ext = extracted.get("ext", "").lower()
    icc = _image_icc_profile(doc, xref, extracted.get("colorspace"))
    special = any(_has_key(doc, xref, key) for key in ("Mask", "ImageMask", "Decode"))
    if extracted.get("image") and not smask and not special and ext in _RAW_EXTS:
        data = extracted["image"]
        return _with_icc_profile(data, ext, icc) if icc else data
    with Image.open(io.BytesIO(xref_png(xref))) as decoded:
        image = decoded.copy()
    if smask:
        with Image.open(io.BytesIO(xref_png(smask))) as decoded_mask:
            mask = decoded_mask.convert("L")
        if mask.size != image.size:
            mask = mask.resize(image.size)
        image = image.convert("RGBA")
        image.putalpha(mask)
    out = io.BytesIO()
    image.save(out, format="PNG", **({"icc_profile": icc} if icc else {}))
    return out.getvalue()


def stitch_vertically(pieces):
    """PNG des images `pieces` (bytes, même largeur) placées l'une sous
    l'autre, sans perte. Profil ICC de la première conservé. Mode commun :
    celui des morceaux s'ils l'ont tous (L, RGB, RGBA), sinon RGBA si l'un
    est transparent, sinon RGB."""
    images = []
    for data in pieces:
        with Image.open(io.BytesIO(data)) as decoded:
            images.append(decoded.copy())
    modes = {image.mode for image in images}
    if len(modes) == 1 and modes <= {"L", "RGB", "RGBA"}:
        mode = images[0].mode
    elif any(image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info for image in images):
        mode = "RGBA"
    else:
        mode = "RGB"
    icc = images[0].info.get("icc_profile")
    result = Image.new(mode, (images[0].width, sum(image.height for image in images)))
    y = 0
    for image in images:
        result.paste(image.convert(mode), (0, y))
        y += image.height
    out = io.BytesIO()
    result.save(out, format="PNG", **({"icc_profile": icc} if icc and mode != "L" else {}))
    return out.getvalue()


def _error_text(exc):
    return str(exc) or exc.__class__.__name__


# Nombre de recollages candidats de même taille qui suffit à faire de cette
# taille une référence, quand le PDF n'a aucune image entière de cette taille.
_REFERENCE_MIN_GROUPS = 3


def _trimmed(chain, area):
    """`chain` sans les images finales qui descendent jusqu'au bas de la
    zone : le dernier morceau d'une image coupée s'arrête avant
    (_ends_before_bottom)."""
    chain = list(chain)
    while len(chain) > 1 and not _ends_before_bottom(chain[-1], area):
        chain.pop()
    return chain


def reference_sizes(placements, area):
    """Tailles (largeur, hauteur en pixels) qu'une image recollée doit
    atteindre pour être recollée.

    La géométrie seule ne distingue pas une image coupée en deux d'une page
    entière suivie d'une image plus courte sans rapport, dont les bords
    blancs se ressemblent. Mais une conversion coupe des images qui avaient
    presque toujours la même taille : les vraies découpes retombent toutes
    sur la taille des images restées entières. Une taille est une référence
    si une image du PDF qui n'entre dans aucun recollage possible (continues)
    l'a, ou si au moins _REFERENCE_MIN_GROUPS recollages possibles
    l'atteignent (PDF dont toutes les images sont coupées). Un faux
    recollage tomberait sur une taille quelconque."""
    chains, current = [], []
    for index, placement in enumerate(placements):
        if current and continues(placements[current[-1]], placement, area):
            current.append(index)
            continue
        if current:
            chains.append(current)
        current = [index]
    if current:
        chains.append(current)
    involved, totals = set(), Counter()
    for chain in chains:
        if len(chain) < 2:
            continue
        involved.update(chain)
        kept = _trimmed([placements[i] for i in chain], area)
        if len(kept) > 1:
            totals[(kept[0]["width"], sum(p["height"] for p in kept))] += 1
    sizes = {(p["width"], p["height"]) for index, p in enumerate(placements)
             if index not in involved and p["xref"]}
    sizes.update(size for size, count in totals.items() if count >= _REFERENCE_MIN_GROUPS)
    return sizes


def original_images(doc, xref_png, join_cut_images=False, on_scan=None):
    """Images d'origine de `doc`, une par image dessinée, dans l'ordre de
    lecture (image_placements). Une image utilisée sur plusieurs pages est
    rendue à chaque fois.

    join_cut_images : les morceaux d'une image coupée entre deux pages sont
    recollés, seulement quand la découpe est certaine :
    - chaque morceau continue le précédent (continues) et leurs bords se
      raccordent (seams_match) ;
    - le dernier s'arrête avant le bas de la zone (_ends_before_bottom) ;
    - la taille obtenue est une taille de référence du PDF (reference_sizes).
    En cas de doute, les morceaux restent séparés.

    Rend, au fil du document, des dicts {"page": index de la page de la
    première image, "data": bytes, "dpi": résolution, "pieces": nombre de
    morceaux}, ou {"page": index, "error": message} pour une image illisible
    (signalée à l'utilisateur, jamais avalée). `xref_png` : voir
    placement_image. on_scan(done, total) : progression du relevé préalable
    de toutes les pages (image_placements)."""
    placements = image_placements(doc, on_scan)
    area = content_area(placements)
    references = reference_sizes(placements, area) if join_cut_images else set()
    group, datas = [], []
    for placement in placements:
        try:
            data = placement_image(doc, placement, xref_png)
        except Exception as exc:
            yield from _emit_group(group, datas, area, references)
            group, datas = [], []
            yield {"page": placement["page"], "error": _error_text(exc)}
            continue
        if (join_cut_images and group and continues(group[-1], placement, area)
                and _seams_match_safe(datas[-1], data)):
            group.append(placement)
            datas.append(data)
            continue
        yield from _emit_group(group, datas, area, references)
        group, datas = [placement], [data]
    yield from _emit_group(group, datas, area, references)


def _seams_match_safe(upper_data, lower_data):
    """seams_match ; False si l'une des images ne se décode pas (les deux
    restent alors des images séparées, sans perte)."""
    try:
        return seams_match(upper_data, lower_data)
    except Exception:
        return False


def _emit_group(group, datas, area, references):
    """Rend les images d'un groupe : une seule image recollée si la découpe
    est certaine, sinon chaque image telle quelle. Tant que la dernière image
    du groupe descend jusqu'au bas de la zone, elle n'est pas un reste de
    découpe : elle est détachée et rendue seule après les autres. Le groupe
    restant n'est recollé que si sa taille est une référence
    (reference_sizes)."""
    group, datas = list(group), list(datas)
    trailing = []
    while len(group) > 1 and not _ends_before_bottom(group[-1], area):
        trailing.insert(0, (group.pop(), datas.pop()))
    size = (group[0]["width"], sum(p["height"] for p in group)) if group else None
    if len(group) > 1 and size in references:
        try:
            stitched = stitch_vertically(datas)
        except Exception:
            stitched = None  # repli : les morceaux sont rendus séparément, sans perte
        if stitched is not None:
            yield {"page": group[0]["page"], "data": stitched,
                   "dpi": placement_dpi(group[0]), "pieces": len(group)}
            group, datas = [], []
    for placement, data in list(zip(group, datas)) + trailing:
        yield {"page": placement["page"], "data": data,
               "dpi": placement_dpi(placement), "pieces": 1}
