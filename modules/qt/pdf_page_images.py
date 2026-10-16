"""
modules/qt/pdf_page_images.py — Image intégrée d'une page PDF en mode
« original » (résolution 0) : résolution de la meilleure image de la page, et
décision d'extraire cette image telle quelle au lieu de rendre la page.

Sans Qt, appelé dans le process PDF dédié (pdf_loading_qt._pdf_persistent_process)
par l'ouverture, la fusion et le lot PDF → CBZ. Les objets `doc` et `page` sont
ceux de PyMuPDF.
"""

import io
import re

from PIL import Image

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
