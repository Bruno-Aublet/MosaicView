# -------------------------
# Fonctions de gestion des entrées (images/fichiers)
# -------------------------
import os
import io
import hashlib
from PIL import Image, ImageSequence

from modules.qt import state as _state_module
from modules.qt.archive_names import name_for_real_format, JPEG_EXTS, MAX_MEMBER_SIZE
from modules.qt.state import get_current_theme
from modules.qt.font_loader import resource_path
from modules.qt.utils import format_file_size


class FileTooLargeError(Exception):
    """Levée quand un fichier dépasse la taille maximale autorisée."""
    def __init__(self, filepath, file_size):
        self.filepath = filepath
        self.file_size = file_size
        self.filename = os.path.basename(filepath)
        self.size_str = format_file_size(file_size)
        super().__init__(f"{self.filename} ({self.size_str})")

# Constantes liées aux entrées
ICON_MAP = {
    ".nfo": resource_path("icons/nfo.png"),
    ".txt": resource_path("icons/txt.png"),
    ".xml": resource_path("icons/xml.png"),
    "dir": resource_path("icons/directory.png"),
    "parent_dir": resource_path("icons/Folder-up.png"),
    "corrupted": resource_path("icons/fichier-corrompu.png")
}
DEFAULT_ICON = resource_path("icons/other.png")

THUMB_SIZES = {
    0: (100, 133),   # Petite
    1: (150, 200),   # Moyenne (par défaut)
    2: (200, 267)    # Grande
}


def get_icon_pil_for_entry(entry, state=None):
    """Retourne l'image PIL brute de l'icône (sans conversion PhotoImage)"""
    ext = entry["extension"].lower()

    if entry.get("is_corrupted"):
        icon_path = ICON_MAP.get("corrupted", DEFAULT_ICON)
    elif entry.get("is_parent_dir"):
        icon_path = ICON_MAP.get("parent_dir", DEFAULT_ICON)
    elif entry.get("is_dir"):
        icon_path = ICON_MAP.get("dir", DEFAULT_ICON)
    else:
        icon_path = ICON_MAP.get(ext, DEFAULT_ICON)

    try:
        img = Image.open(icon_path)
    except Exception:
        try:
            img = Image.open(DEFAULT_ICON)
        except Exception:
            # Si même l'icône par défaut n'existe pas, crée une image vide
            tw, th = (state.thumb_w, state.thumb_h) if state else (150, 200)
            img = Image.new('RGB', (tw, th), color='gray')
    return img



def estimate_compression_rate(entry):
    """Estime le taux de compression pour les images JPG/JPEG/WEBP"""
    ext = entry.get("extension", "").lower()

    if ext not in [".jpg", ".jpeg", ".webp", ".avif"]:
        return None

    try:
        metadata = get_image_metadata(entry)
        if metadata is None:
            return None

        img_bytes = entry.get("bytes")
        if not img_bytes:
            return None

        compressed_size = len(img_bytes)

        # Taille théorique non compressée (largeur × hauteur × 3 bytes pour RGB)
        width, height = metadata["size"]
        uncompressed_size = width * height * 3

        if uncompressed_size > 0:
            compression_rate = (1 - (compressed_size / uncompressed_size)) * 100
            compression_rate = max(0, min(100, compression_rate))
            return round(compression_rate, 1)

    except Exception:
        pass  # repli : taux inconnu (None)

    return None


def _make_checkerboard_pil(w: int, h: int, tile: int = 8) -> Image.Image:
    """Génère une image PIL damier RGBA (gris clair / gris foncé).

    Construit un motif de base (2x2 cases) puis le répète par collage de blocs
    entiers plutôt que pixel par pixel : une boucle Python pixel par pixel sur
    une grande image (ex. une page verticale de webtoon) prend plusieurs
    secondes, contre quelques dizaines de ms avec ce tuilage.
    """
    light = (200, 200, 200, 255)
    dark  = (160, 160, 160, 255)
    pattern = Image.new('RGBA', (tile * 2, tile * 2), light)
    pattern.paste(Image.new('RGBA', (tile, tile), dark), (tile, 0))
    pattern.paste(Image.new('RGBA', (tile, tile), dark), (0, tile))
    bg = Image.new('RGBA', (w, h))
    pw, ph = pattern.size
    for y in range(0, h, ph):
        for x in range(0, w, pw):
            bg.paste(pattern, (x, y))
    return bg


# Balise EXIF d'orientation : un appareil photo enregistre souvent l'image
# dans le sens de son capteur et indique par cette balise comment la tourner
# pour l'afficher.
_EXIF_ORIENTATION = 0x0112
# Orientations qui échangent largeur et hauteur (quart de tour).
_SIZE_SWAPPING_ORIENTATIONS = (5, 6, 7, 8)
# Orientation écrite dans le XMP (relue par Pillow dans getexif())
_XMP_ORIENTATION_PATTERNS = (rb'tiff:Orientation="[0-9]"',
                             rb'<tiff:Orientation>[0-9]</tiff:Orientation>')


def _has_several_images(img):
    """True pour une animation ou un TIFF de plusieurs pages. Un JPEG MPO
    (photo de téléphone avec une image annexe) n'en est pas un : sa première
    image est la photo."""
    return img.format != "MPO" and getattr(img, "n_frames", 1) > 1


def exif_orientation(img):
    """Orientation EXIF de img (1 à 8), 1 si absente ou illisible."""
    try:
        value = int(img.getexif().get(_EXIF_ORIENTATION, 1) or 1)
    except Exception:
        return 1
    return value if 1 <= value <= 8 else 1


def oriented(img):
    """img tournée selon sa balise EXIF d'orientation, telle que l'affichent
    les autres lecteurs, balise retirée de son EXIF et de son XMP ; img
    elle-même si elle est déjà droite (ou à plusieurs images).

    Toute page affichée ou modifiée est décodée par là (open_page_image,
    ensure_image_loaded) : save_image_to_bytes réécrit ensuite l'EXIF sans
    l'orientation (page_metadata), les pixels étant déjà tournés. Une page
    décodée sans passer par là, puis réenregistrée, serait tournée d'un quart
    de tour dans les lecteurs qui respectent l'EXIF."""
    if _has_several_images(img) or exif_orientation(img) == 1:
        return img
    try:
        from PIL import ImageOps
        return ImageOps.exif_transpose(img)
    except Exception:
        return img


def open_page_image(data):
    """Image PIL des bytes d'une page, dans le sens d'affichage (oriented)."""
    return oriented(Image.open(io.BytesIO(data)))


def oriented_size(img):
    """(largeur, hauteur) de img dans le sens d'affichage, sans la décoder."""
    w, h = img.size
    if not _has_several_images(img) and exif_orientation(img) in _SIZE_SWAPPING_ORIENTATIONS:
        return h, w
    return w, h


def page_size(data):
    """(largeur, hauteur) d'affichage d'une page d'après l'en-tête de ses
    bytes (oriented_size)."""
    with Image.open(io.BytesIO(data)) as img:
        return oriented_size(img)


def page_metadata(data, keep_orientation=False):
    """(exif, xmp, comment) des bytes d'origine d'une page, à réécrire quand
    elle est réencodée — None pour ce qui manque. EXIF et XMP sans
    l'orientation, sauf keep_orientation=True (pixels réencodés sans avoir
    été tournés, comme la normalisation des pages des lots) : réécrite sur
    une image déjà tournée (oriented), elle la ferait tourner une seconde
    fois. Pillow relit aussi l'orientation dans le XMP : elle est retirée
    des deux."""
    exif = xmp = comment = None
    if not data:
        return exif, xmp, comment
    try:
        with Image.open(io.BytesIO(data)) as orig:
            # TIFF : getexif() rend les balises de structure du fichier
            # lui-même (largeur, hauteur, emplacement des données…), pas un
            # bloc EXIF — réécrites ailleurs, elles abîmeraient l'image.
            if orig.format != "TIFF":
                tags = orig.getexif()
                if not keep_orientation and _EXIF_ORIENTATION in tags:
                    del tags[_EXIF_ORIENTATION]
                if len(tags):
                    exif = tags.tobytes()
            xmp = orig.info.get("xmp") or orig.info.get("XML:com.adobe.xmp")
            if isinstance(xmp, str):
                xmp = xmp.encode("utf-8")
            if xmp and not keep_orientation:
                import re
                for pattern in _XMP_ORIENTATION_PATTERNS:
                    xmp = re.sub(pattern, b"", xmp)
            comment = orig.info.get("comment") or None
    except Exception:
        pass  # repli : aucune métadonnée reprise des données d'origine
    return exif, xmp, comment


def metadata_save_kwargs(fmt, data, keep_orientation=False):
    """Arguments de save() qui réécrivent les métadonnées de la page d'origine
    (page_metadata) dans le format `fmt` : EXIF pour JPEG, WebP, AVIF et
    PNG ; XMP pour JPEG, WebP et AVIF ; commentaire pour JPEG. Jamais d'EXIF
    écrit dans un TIFF : Pillow fusionne ses balises avec celles qui
    décrivent l'image (dimensions, résolution), qu'elles pourraient
    contredire."""
    exif, xmp, comment = page_metadata(data, keep_orientation)
    kwargs = {}
    if exif and fmt in ("JPEG", "WEBP", "AVIF", "PNG"):
        kwargs["exif"] = exif
    if xmp and fmt in ("JPEG", "WEBP", "AVIF"):
        kwargs["xmp"] = xmp
    if comment and fmt == "JPEG":
        kwargs["comment"] = comment
    return kwargs


def to_display_mode(img):
    """img dans un mode que l'affichage sait convertir en RGBA sans écrêter :
    les niveaux de gris 16 bits (I;16*), les entiers 32 bits (I) et les
    flottants (F) ramenés à 256 nuances (image_ops._to_writable_mode) — un
    convert('RGBA') direct rend blanc tout ce qui dépasse 255. Les autres
    modes sont rendus tels quels."""
    if img.mode.startswith("I") or img.mode == "F":
        from modules.qt.image_ops import _to_writable_mode
        return _to_writable_mode(img)
    return img


def to_screen_mode(img):
    """img prête à être affichée ou imprimée (vignettes, visionneuse,
    aperçus, impression) : to_display_mode, et une page CMJN convertie en RVB
    par son profil ICC (image_ops.cmyk_to_rgb). Sans profil, un convert('RGBA')
    direct donne des couleurs fausses à l'écran (trop vives ou délavées),
    alors que la page est juste dans les logiciels qui gèrent la couleur.
    Réservée à l'affichage : jamais pour une image enregistrée dans le comic
    (outils de dessin, jointure, GIF animé gardent to_display_mode)."""
    if img.mode == "CMYK":
        from modules.qt.image_ops import cmyk_to_rgb
        return cmyk_to_rgb(img)
    return to_display_mode(img)


def rotate_page_image(img, angle, fillcolor=None):
    """img tournée de `angle` degrés avec interpolation bicubique, agrandie
    pour contenir toute la page (redressement manuel et automatique) ;
    `fillcolor` remplit les coins découverts ("white", ou None : noir).

    Une page en niveaux de gris 16 bits est tournée en entiers 32 bits puis
    ramenée en 16 bits (valeurs bornées à 0-65535) : Pillow ne sait pas
    interpoler une image 16 bits, et l'écrasait — page presque noire,
    valeurs plafonnées à 255."""
    if img.mode.startswith("I;16"):
        fill = 65535 if fillcolor == "white" else 0
        wide = img.convert("I").rotate(angle, expand=True, resample=Image.Resampling.BICUBIC,
                                       fillcolor=fill)
        return wide.convert("I;16")
    return img.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC, fillcolor=fillcolor)


def drawing_base_image(data):
    """Image RGBA de travail des outils de dessin de la visionneuse (texte,
    formes, collage d'image, clonage, flou) pour la page dont `data` sont les
    bytes : dans le sens d'affichage, 16 bits ramenés à 256 nuances
    (to_display_mode — un convert('RGBA') direct rend la page blanche)."""
    return to_display_mode(open_page_image(data)).convert("RGBA")


def drawn_page_image(data, drawn):
    """Image à enregistrer après un outil de dessin, `drawn` étant le résultat
    du dessin sur drawing_base_image(data) : la page retrouve son mode
    d'origine. Un dessin en couleur devient gris (ou noir et blanc) sur une
    page qui n'est pas en couleur — passer la page en couleur est le rôle de
    l'outil Mode d'image. Sans ce retour, toute page passait en RGBA : un
    JPEG en niveaux de gris devenait un JPEG couleur, un PNG 1 bit une image
    32 bits, bien plus lourds.

    - gris 16 bits : seuls les pixels changés par le dessin prennent sa
      valeur, en gris (256 nuances, Qt ne dessine pas au-delà) ; tout le
      reste garde sa précision d'origine ;
    - niveaux de gris (L) : dessin aplati sur blanc puis en gris ;
    - 1 bit : idem, puis seuil à mi-gris (comme le 1 bit de l'outil Mode
      d'image) ;
    - gris avec transparence (LA) : en LA ;
    - palette (P) hors GIF : palette de 256 couleurs recalculée, transparence
      comprise (un GIF garde l'image RGBA : son enregistrement la quantifie
      lui-même et garde mieux sa transparence) ;
    - toute autre page (RGB, RGBA, CMJN…) : `drawn` tel quel."""
    try:
        orig = open_page_image(data)
    except Exception:
        return drawn
    mode = orig.mode
    if mode in ("L", "1"):
        rgba = drawn.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        gray = flat.convert("L")
        if mode == "L":
            return gray
        return gray.point(lambda p: 255 if p > 128 else 0, "1")
    if mode == "LA":
        return drawn.convert("LA")
    if mode in ("P", "PA") and orig.format != "GIF":
        return drawn.convert("RGBA").quantize(colors=256)
    if not mode.startswith("I;16"):
        return drawn
    import numpy as np
    base8 = np.asarray(to_display_mode(orig).convert("L"))
    flat = Image.new("RGB", drawn.size, (255, 255, 255))
    rgba = drawn.convert("RGBA")
    flat.paste(rgba, mask=rgba.getchannel("A"))
    drawn8 = np.asarray(flat.convert("L"))
    changed = drawn8 != base8
    values = np.asarray(orig.convert("I"), dtype=np.int64).copy()
    values[changed] = drawn8[changed].astype(np.int64) * 257
    return Image.fromarray(values.astype(np.uint16))


def create_centered_thumbnail(img, thumb_w, thumb_h, background_color=None, checkerboard=False):
    """Crée une miniature centrée sur un fond transparent (ou damier si checkerboard=True)."""
    img_thumb = to_screen_mode(oriented(img)).copy()
    img_thumb.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)

    x_offset = (thumb_w - img_thumb.width) // 2
    y_offset = (thumb_h - img_thumb.height) // 2

    if img_thumb.mode != 'RGBA':
        img_thumb = img_thumb.convert('RGBA')

    # Fond transparent — le bg du canvas sera visible derrière
    background = Image.new('RGBA', (thumb_w, thumb_h), (0, 0, 0, 0))

    if checkerboard:
        # Le damier ne doit couvrir que le rectangle réel occupé par l'image
        # redimensionnée, pas tout le cadre thumb_w x thumb_h — sinon la
        # transparence "déborde" hors du contour de l'image (ex. icône carrée
        # dans un cadre de vignette rectangulaire). On peint donc le damier en
        # plein sur ce seul rectangle (collage sans masque), puis l'image vient
        # se poser par-dessus avec son propre alpha comme masque : le damier
        # reste visible uniquement là où l'image est réellement transparente.
        checker = _make_checkerboard_pil(img_thumb.width, img_thumb.height)
        background.paste(checker, (x_offset, y_offset))

    background.paste(img_thumb, (x_offset, y_offset), img_thumb)

    return background


# Formats autres que GIF pouvant contenir une animation (WebP animé, PNG
# animé/APNG, AVIF animé).
_ANIMATABLE_EXTS = ('.webp', '.png', '.avif')


def set_animated_image_info(entry, img=None):
    """Détecte un WebP/PNG/AVIF à plusieurs frames et pose
    entry["is_animated_image"], plus gif_frame_count/gif_durations (mêmes
    clés que les GIF animés, lues par la lecture animée de la visionneuse).

    Ce drapeau a les mêmes effets que is_animated_gif (traitement frame par
    frame des opérations, grisage des outils de la visionneuse, lecture
    animée, jointure, info-bulle), voir image_ops.is_animated_entry ; seule la
    fenêtre des GIF animés reste réservée aux GIF. img : image déjà ouverte sur ces
    bytes (évite de la rouvrir), sinon ouverte ici.

    Pose aussi entry["is_multipage_tiff"] (TIFF de plusieurs pages, sans
    animation) : voir image_ops.has_several_frames."""
    entry["is_animated_image"] = False
    entry["is_multipage_tiff"] = False
    ext = entry.get("extension", "").lower()
    if ext in (".tif", ".tiff") and entry.get("bytes"):
        try:
            im = img if img is not None else Image.open(io.BytesIO(entry["bytes"]))
            entry["is_multipage_tiff"] = getattr(im, "n_frames", 1) > 1
            im.seek(0)
        except Exception:
            pass  # repli : TIFF considéré d'une seule page
        return
    if ext not in _ANIMATABLE_EXTS or not entry.get("bytes"):
        return
    try:
        im = img if img is not None else Image.open(io.BytesIO(entry["bytes"]))
        frame_count = getattr(im, "n_frames", 1)
        if frame_count > 1:
            durations = []
            for frame_idx in range(frame_count):
                im.seek(frame_idx)
                # WebP/AVIF ne renseignent la durée qu'une fois la frame décodée.
                im.load()
                # int : le PNG animé renvoie des flottants, que le minuteur Qt
                # de la lecture animée refuserait.
                durations.append(int(im.info.get("duration") or 100))
            im.seek(0)
            entry["is_animated_image"] = True
            entry["gif_frame_count"] = frame_count
            entry["gif_durations"] = durations
    except Exception:
        pass  # repli : image considérée fixe, ses données restent intactes


def animation_play_count(entry):
    """Nombre de lectures d'une animation (GIF, WebP, PNG animé) d'après son
    fichier, None si elle se joue en boucle. Les formats ne comptent pas de
    la même façon (voir image_ops._animation_loop_for) : dans un GIF, sans
    valeur loop l'animation est jouée une fois et loop=N la répète N fois ;
    en WebP et en PNG animé, loop est le nombre de lectures. 0 vaut « en
    boucle » partout ; l'AVIF n'enregistre pas cette valeur (en boucle).
    Lit seulement l'en-tête, sans décoder d'image."""
    if not entry.get("bytes"):
        return None
    try:
        with Image.open(io.BytesIO(entry["bytes"])) as im:
            fmt = im.format
            loop = im.info.get("loop")
    except Exception:
        return None
    if fmt == "GIF":
        if loop is None:
            return 1
        return None if loop == 0 else loop + 1
    if fmt in ("WEBP", "PNG") and loop:
        return loop
    return None


def image_dpi(img):
    """Résolution (x, y) de l'image PIL `img`, None si elle n'en a pas.

    Un TIFF sans balise de résolution est annoncé par Pillow à (1, 1), et
    cette valeur suit l'image dans ses copies et transformations (img.info).
    Prise pour une vraie résolution, elle était affichée et écrite dans les
    fichiers produits. Une résolution d'1 dpi ou moins n'a de sens pour
    aucune image : elle vaut absence de résolution."""
    dpi = img.info.get("dpi")
    if isinstance(dpi, (int, float)):
        dpi = (dpi, dpi)
    if not isinstance(dpi, (tuple, list)) or len(dpi) < 2:
        return None
    try:
        if max(float(dpi[0]), float(dpi[1])) <= 1:
            return None
    except (TypeError, ValueError):
        return None
    return tuple(dpi[:2])


def page_dpi(entry, img=None):
    """Résolution (x, y) à écrire pour la page `entry` (`img` : son image
    décodée, si elle l'est déjà), None si elle n'en a pas. entry["dpi"] ne
    garde que la valeur horizontale (affichée, et changée par le
    redimensionnement) : la résolution de l'image est reprise telle quelle
    tant que sa valeur horizontale est celle-là, sinon la nouvelle valeur
    vaut dans les deux sens. Sans cela, une page à 300 × 600 dpi était
    réécrite à 300 × 300."""
    value = entry.get("dpi")
    if isinstance(value, (tuple, list)):
        value = value[0] if value else None
    source = image_dpi(img) if img is not None else None
    if source is None and entry.get("bytes"):
        try:
            with Image.open(io.BytesIO(entry["bytes"])) as orig:
                source = image_dpi(orig)
        except Exception:
            source = None
    if not value:
        return source
    if source and round(float(source[0])) == round(float(value)):
        return source
    return (value, value)


# Compressions TIFF que Pillow écrit pour tous les modes de l'application.
_TIFF_ANY_MODE_COMPRESSIONS = ("tiff_lzw", "tiff_adobe_deflate", "tiff_deflate", "packbits")
_TIFF_BILEVEL_COMPRESSIONS = ("group3", "group4", "tiff_ccitt")
_TIFF_JPEG_COMPRESSIONS = ("jpeg", "tiff_jpeg")


def tiff_save_compression(img, data, default="raw"):
    """Compression à passer à Pillow pour écrire `img` en TIFF — toujours,
    "raw" compris : sans valeur passée, Pillow reprend celle de img.info,
    même incompatible avec le mode de l'image modifiée. Celle de l'image,
    sinon celle des bytes d'origine `data` si c'est un TIFF, sinon `default`.
    Sans elle, une page TIFF compressée devenait un TIFF non compressé, bien
    plus lourd, après tout outil qui recompose l'image (img.info perdu).

    Une compression incompatible avec le mode écrit est remplacée par LZW,
    sans perte et lue par tous les logiciels : Pillow ne refuse pas toujours
    une telle combinaison, il peut faire planter le programme (CCITT Group 3/4
    hors image 1 bit, JPEG sur une image 1 bit, à palette ou 16 bits). Un
    TIFF JPEG passe aussi en LZW : Pillow le réencoderait à qualité 75, avec
    une nouvelle perte à chaque retouche. Toute autre compression (LZMA,
    Zstd, WebP…) passe en LZW, faute de garantie que Pillow sache l'écrire."""
    comp = img.info.get("compression")
    if not comp and data:
        try:
            with Image.open(io.BytesIO(data)) as orig:
                if orig.format == "TIFF":
                    comp = orig.info.get("compression")
        except Exception:
            comp = None
    if not comp:
        return default
    if comp == "raw":
        return "raw"
    if comp in _TIFF_ANY_MODE_COMPRESSIONS:
        return comp
    if comp in _TIFF_BILEVEL_COMPRESSIONS and img.mode == "1":
        return comp
    return "tiff_lzw"


def page_high_depth(data):
    """Profondeur au-delà de 8 bits par couleur de la page dont `data` sont
    les bytes : "gray16" pour une image en niveaux de gris 16 bits (ou
    entiers 32 bits, flottants) que Pillow lit telle quelle — gardée par la
    conversion et les outils de dessin, réduite à 8 bits par les réglages ;
    "color16" pour un PNG ou un TIFF à 16 bits par couleur que Pillow lit
    déjà en 8 bits (aucun mode couleur 16 bits) — réduite par toute
    modification ; None sinon. Lit seulement l'en-tête."""
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as im:
            mode, fmt = im.mode, im.format
            bits = 8
            if fmt == "PNG" and len(data) > 25:
                bits = data[24]
            elif fmt == "TIFF":
                sample = im.tag_v2.get(258)
                if isinstance(sample, (tuple, list)):
                    bits = max(sample) if sample else 8
                elif isinstance(sample, int):
                    bits = sample
    except Exception:
        return None
    if mode.startswith("I;16") or mode in ("I", "F"):
        return "gray16"
    return "color16" if bits > 8 else None


def _header_size(data):
    """(largeur, hauteur) lues dans l'en-tête d'une image que Pillow refuse
    d'ouvrir parce qu'elle dépasse le double d'Image.MAX_IMAGE_PIXELS ; (0, 0) si
    illisible. Seul le lecteur du format est appelé (Image.OPEN, comme le fait
    Image.open, restreint aux formats gérés par Image.ID) : il lit l'en-tête
    sans décoder l'image ni appliquer le contrôle de taille. Lever ce contrôle
    en mettant Image.MAX_IMAGE_PIXELS à None le lèverait pour toute
    l'application, le temps de la lecture : une image ouverte au même moment
    par un autre thread y échapperait."""
    prefix = data[:16]
    for fmt in list(Image.ID):
        factory, accept = Image.OPEN.get(fmt, (None, None))
        if factory is None:
            continue
        try:
            accepted = accept(prefix) if accept else True
            if not accepted or isinstance(accepted, str):
                continue
            im = factory(io.BytesIO(data), "")
            try:
                return im.size
            finally:
                im.close()
        except Exception:
            continue
    return 0, 0


def create_entry(file, data, image_exts):
    """
    Crée une entrée pour un fichier.

    Args:
        file: nom du fichier dans l'archive
        data: bytes du fichier
        image_exts: extensions d'images supportées
    """
    state = _state_module.state
    entry_ext = os.path.splitext(file)[1]
    is_image = entry_ext.lower() in image_exts
    is_dir = file.endswith("/")
    entry = {
        "orig_name": file,
        "bytes": data,
        "extension": entry_ext,
        "name_entry": None,
        "ext_label": None,
        "img_id": None,
        "text_id": None,
        "is_image": is_image,
        "is_dir": is_dir,
        "is_corrupted": False,
        "is_too_large": False,
        "corruption_reason": None
    }
    if is_image and data is not None:
        try:
            if len(data) == 0:
                raise ValueError("Fichier image vide")

            img = Image.open(io.BytesIO(data))
            img.verify()
            img = Image.open(io.BytesIO(data))

            # Extension qui ne correspond pas au vrai format (PNG nommé .jpg…) :
            # corrigée dès l'ouverture (archive_names.name_for_real_format),
            # avant toute décision prise d'après l'extension (GIF animé,
            # format de réenregistrement). L'ancienne extension est gardée
            # pour l'info-bulle ; le chargeur marque le document modifié.
            corrected = name_for_real_format(file, img.format)
            if corrected != file:
                entry["extension_corrected_from"] = entry_ext
                entry["orig_name"] = corrected
                entry_ext = os.path.splitext(corrected)[1]
                entry["extension"] = entry_ext

            # Force le décodage. Une image trop grande a déjà été refusée par
            # Image.open (DecompressionBombError) : Pillow ne lève cette erreur
            # qu'au-delà du double d'Image.MAX_IMAGE_PIXELS (réglée dans
            # MosaicView.py), et se contente d'un avertissement en deçà.
            img.load()

            if img.width <= 0 or img.height <= 0:
                raise ValueError(f"Dimensions invalides: {img.width}x{img.height}")

            # Stocke les dimensions pour éviter de rouvrir l'image (ex. renumérotation),
            # dans le sens d'affichage
            entry["img_width"], entry["img_height"] = oriented_size(img)

            img_dpi = image_dpi(img)
            entry["dpi"] = img_dpi[0] if img_dpi else None

            # Détecte si c'est un GIF animé AVANT de copier l'image
            if entry_ext.lower() == '.gif' and hasattr(img, 'n_frames') and img.n_frames > 1:
                entry["is_animated_gif"] = True
                # Lazy loading : ne stocke que le nombre de frames, pas les frames elles-mêmes
                entry["gif_frame_count"] = img.n_frames
                entry["gif_durations"] = []

                for frame_idx in range(img.n_frames):
                    img.seek(frame_idx)
                    duration = img.info.get('duration', 100)
                    entry["gif_durations"].append(duration)

                # None : pas de bloc de répétition, l'animation n'est jouée
                # qu'une fois (0 = en boucle)
                entry["gif_loop"] = img.info.get('loop')
                entry["gif_disposal"] = img.info.get('disposal', 2)
                entry["gif_comment"] = img.info.get('comment', b'').decode('utf-8', errors='ignore') if img.info.get('comment') else ""
                entry["gif_optimize"] = True

                img.seek(0)  # repositionne sur la première frame pour l'affichage
                entry["img"] = None  # lazy loading : pas de chargement complet ici
            else:
                entry["is_animated_gif"] = False
                entry["img"] = None
                set_animated_image_info(entry, img)

            entry["large_thumb_pil"] = None
        except Image.DecompressionBombError:
            w, h = _header_size(data)
            entry["img"] = None
            entry["is_corrupted"] = True
            entry["is_too_large"] = True
            entry["corruption_reason"] = f"{w}x{h} ({w * h:,} pixels)" if w else ""
            entry["is_animated_gif"] = False
        except Exception as e:
            entry["img"] = None
            entry["is_corrupted"] = True
            entry["is_too_large"] = False
            entry["corruption_reason"] = str(e)
            # is_image reste True pour que le fichier soit reconnu comme image corrompue
            entry["is_animated_gif"] = False
    else:
        entry["img"] = None
        if is_image and data is None:
            # Page de l'archive illisible (CRC faux, compression non prise en
            # charge, membre chiffré) : signalée comme corrompue (cadre rouge,
            # remplacer/supprimer). Ses données n'existent que dans le fichier
            # d'origine : les écritures refusent de l'omettre
            # (file_operations_qt.MissingPageDataError).
            entry["is_corrupted"] = True
            entry["is_animated_gif"] = False

    entry["_hash"] = (
        hashlib.md5(data).hexdigest()
        if is_image and not entry["is_corrupted"] and data
        else None
    )
    entry["_is_duplicate"] = False
    entry["_duplicate_group"] = None

    return entry


def _single_page_tiff_bytes(frame, compression=None, dpi=None, icc=None):
    """Bytes d'un TIFF d'une seule page contenant `frame`, mode inchangé,
    avec la compression, le DPI et le profil ICC donnés."""
    save_kwargs = {}
    if compression and compression != "raw":
        save_kwargs["compression"] = compression
    if dpi:
        save_kwargs["dpi"] = dpi
    if icc:
        save_kwargs["icc_profile"] = icc
    out = io.BytesIO()
    try:
        frame.save(out, format="TIFF", **save_kwargs)
    except Exception:
        # Compression d'origine incompatible avec le mode de la page :
        # TIFF sans compression imposée
        save_kwargs.pop("compression", None)
        out = io.BytesIO()
        frame.save(out, format="TIFF", **save_kwargs)
    return out.getvalue()


def split_multipage_tiff_pages(data):
    """Bytes de chaque page d'un TIFF de plusieurs pages, chacune enregistrée
    comme un TIFF d'une seule page : compression, DPI et profil ICC d'origine
    conservés, mode de couleur inchangé (jamais de conversion en JPEG)."""
    src = Image.open(io.BytesIO(data))
    compression = src.info.get("compression")
    dpi = image_dpi(src)
    pages = []
    for page in ImageSequence.Iterator(src):
        frame = page.copy()
        pages.append(_single_page_tiff_bytes(frame, compression, dpi,
                                             page.info.get("icc_profile")))
        frame.close()
    src.close()
    return pages


def expand_multipage_tiffs(entries, image_exts):
    """Remplace chaque TIFF de plusieurs pages d'une liste d'entrées par une
    entrée par page, à sa place, dans l'ordre : `02.tif` (3 pages) devient
    `02_page_0001.tif`, `02_page_0002.tif`, `02_page_0003.tif` (même dossier,
    même préfixe, même archive d'origine). Chaque page reste un TIFF.

    DÉCISION DE CONCEPTION, à ne pas remettre en cause : un TIFF de plusieurs
    pages n'a pas sa place dans un comic. Un CBZ est une suite d'images, une
    image par page ; les lecteurs de BD n'en affichent que la première page,
    et dans la mosaïque une seule vignette cachait les autres pages, qu'on ne
    pouvait ni voir, ni copier, ni déplacer, ni supprimer une à une. Toutes
    ses pages sont donc affichées comme des pages du comic dès l'ouverture
    de l'archive ; l'enregistrement écrit ensuite une image par page.

    Un TIFF qui ne peut pas être découpé reste tel quel, en une seule entrée
    (aucune page perdue : les opérations le traitent alors page par page,
    image_ops.has_several_frames)."""
    result = []
    for entry in entries:
        if not entry.get("is_multipage_tiff") or entry.get("bytes") is None:
            result.append(entry)
            continue
        try:
            pages = split_multipage_tiff_pages(entry["bytes"])
        except Exception:
            result.append(entry)
            continue
        stem, ext = os.path.splitext(entry["orig_name"])
        for number, page_bytes in enumerate(pages, start=1):
            page_entry = create_entry(f"{stem}_page_{number:04d}{ext}", page_bytes, image_exts)
            if "source_archive" in entry:
                page_entry["source_archive"] = entry["source_archive"]
            result.append(page_entry)
    return result


def create_entry_from_file(filepath, image_exts):
    """Crée une entrée à partir d'un fichier sur le disque"""
    try:
        if not os.path.exists(filepath):
            return None

        if not os.path.isfile(filepath):
            return None

        # Même limite que pour un membre d'archive (archive_names.MAX_MEMBER_SIZE)
        file_size = os.path.getsize(filepath)
        if file_size > MAX_MEMBER_SIZE:
            raise FileTooLargeError(filepath, file_size)

        with open(filepath, 'rb') as f:
            data = f.read()
        filename = os.path.basename(filepath)
        return create_entry(filename, data, image_exts)
    except FileTooLargeError:
        raise
    except PermissionError:
        return None
    except Exception:
        return None


def create_entries_from_tiff(filepath, image_exts, add_prefix=False, failures=None):
    """Crée les entrées d'un fichier TIFF ouvert seul (hors archive).

    Même règle que pour un TIFF contenu dans une archive
    (split_multipage_tiff_pages) : chaque page reste un TIFF, dans son mode
    d'origine (palette, 1 bit, 16 bits, CMJN, alpha…), avec sa compression,
    son DPI et son profil ICC — jamais de conversion en JPEG. Un TIFF d'une
    seule page est repris tel quel, sous son nom. Les pages d'un TIFF de
    plusieurs pages sont nommées `<nom>_page_0001.tif`… (même extension).

    failures : liste facultative qui reçoit (page, message) pour chaque page
    illisible (page numérotée à partir de 1). Une page illisible n'est jamais
    ignorée en silence : l'appelant doit la signaler, sinon le document
    produit a une page de moins sans que personne le sache. Les pages
    suivantes sont lues quand même."""
    if not os.path.isfile(filepath):
        return []

    # Même limite que pour un membre d'archive (archive_names.MAX_MEMBER_SIZE)
    file_size = os.path.getsize(filepath)
    if file_size > MAX_MEMBER_SIZE:
        raise FileTooLargeError(filepath, file_size)

    with open(filepath, 'rb') as f:
        data = f.read()

    filename = os.path.basename(filepath)
    prefix = "NEW-" if add_prefix else ""
    try:
        src = Image.open(io.BytesIO(data))
        page_count = getattr(src, "n_frames", 1)
    except Exception:
        src, page_count = None, 1

    if page_count <= 1:
        # Une seule page (ou fichier que PIL n'ouvre pas) : les bytes
        # d'origine, comme toute autre image isolée — create_entry signale
        # une image illisible comme corrompue.
        if src is not None:
            src.close()
        return [create_entry(prefix + filename, data, image_exts)]

    stem, ext = os.path.splitext(filename)
    entries = []
    for page_num in range(page_count):
        dpi = None
        try:
            try:
                src.seek(page_num)
                # Après seek, src.info décrit la page courante
                dpi = image_dpi(src)
                frame = src.copy()
                frame.load()
                page_bytes = _single_page_tiff_bytes(
                    frame, src.info.get("compression"), dpi, src.info.get("icc_profile"))
                frame.close()
            except Exception as pil_error:
                page_bytes = _tifffile_page_bytes(filepath, page_num, dpi)
                if page_bytes is None:
                    raise pil_error
        except Exception as e:
            if failures is not None:
                failures.append((page_num + 1, str(e) or type(e).__name__))
            continue
        entry = create_entry(f"{prefix}{stem}_page_{page_num + 1:04d}{ext}",
                             page_bytes, image_exts)
        entry["source"] = "tiff"
        entry["tiff_page"] = page_num
        entries.append(entry)
    src.close()
    return entries


def _tifffile_page_bytes(filepath, page_num, dpi):
    """Repli pour une page que PIL ne sait pas décoder (compression absente
    de sa libtiff) : la page lue par tifffile, réécrite en TIFF sans perte.
    Seulement pour les données dont le sens est sans ambiguïté — niveaux de
    gris 8/16 bits, RGB et RGBA 8 bits ; None pour tout le reste (palette,
    CMJN, noir = blanc…), que l'appelant signale comme page illisible
    plutôt que de produire de fausses couleurs."""
    try:
        import numpy as np
        import tifffile
        with tifffile.TiffFile(filepath) as tif:
            page = tif.pages[page_num]
            photometric = int(page.photometric)
            arr = page.asarray()
    except Exception:
        return None
    if photometric == 1 and arr.ndim == 2 and arr.dtype in (np.uint8, np.uint16):
        img = Image.fromarray(arr)
    elif photometric == 2 and arr.ndim == 3 and arr.dtype == np.uint8 and arr.shape[2] in (3, 4):
        img = Image.fromarray(arr)
    else:
        return None
    return _single_page_tiff_bytes(img, "tiff_deflate", dpi)


def ensure_image_loaded(entry):
    """
    Charge entry["img"] depuis entry["bytes"] si elle n'est pas déjà en mémoire.
    Cette fonction implémente le lazy loading des images complètes.

    Args:
        entry: Dictionnaire représentant une image

    Returns:
        L'objet PIL Image ou None en cas d'erreur
    """
    if entry.get("img") is not None:
        return entry["img"]

    if entry.get("is_corrupted"):
        return None

    if not entry.get("is_image") or entry.get("bytes") is None:
        return None

    try:
        if entry.get("is_animated_gif"):
            # Les durées/métadonnées GIF sont déjà stockées, on ne recharge que l'image de base
            img = Image.open(io.BytesIO(entry["bytes"]))
            img.seek(0)
            entry["img"] = img.copy()
            img.close()
        else:
            # Dans le sens d'affichage (oriented) : toute opération qui
            # réenregistre la page part de cette image
            img = Image.open(io.BytesIO(entry["bytes"]))
            entry["img"] = oriented(img).copy()
            img.close()

        return entry["img"]
    except Exception:
        entry["img"] = None
        entry["is_corrupted"] = True
        return None


def free_image_memory(entry):
    """
    Libère la mémoire occupée par entry["img"] tout en gardant entry["bytes"].
    Utilisé après les opérations pour économiser la RAM.

    Args:
        entry: Dictionnaire représentant une image
    """
    if entry.get("img") is not None:
        entry["img"].close()
        entry["img"] = None


def get_gif_frame(entry, frame_idx):
    """
    Charge une frame spécifique d'un GIF animé à la demande (lazy loading).
    Cette fonction permet d'éviter de stocker toutes les frames en mémoire.

    Args:
        entry: Dictionnaire représentant un GIF animé
        frame_idx: Index de la frame à charger (0-based)

    Returns:
        PIL.Image: Frame convertie en RGBA, ou None en cas d'erreur
    """
    if not (entry.get("is_animated_gif") or entry.get("is_animated_image")):
        return None

    frame_count = entry.get("gif_frame_count", 0)
    if frame_idx < 0 or frame_idx >= frame_count:
        return None

    if entry.get("bytes") is None:
        return None

    try:
        img = Image.open(io.BytesIO(entry["bytes"]))
        img.seek(frame_idx)
        frame = img.copy().convert("RGBA")
        img.close()

        return frame
    except Exception:
        return None


def get_image_metadata(entry):
    """
    Récupère les métadonnées d'une image (dimensions, DPI, etc.) sans charger l'image complète.
    Utilise PIL pour lire seulement le header du fichier.

    Args:
        entry: Dictionnaire représentant une image

    Returns:
        dict: Dictionnaire avec les métadonnées (size, dpi, mode) ou None
    """
    if not entry.get("is_image") or entry.get("bytes") is None:
        return None

    try:
        # Ouvre l'image SANS la copier en mémoire (juste lecture du header)
        img = Image.open(io.BytesIO(entry["bytes"]))

        # Priorité au DPI stocké dans entry (notamment pour les PDF importés)
        dpi_value = entry.get("dpi") or image_dpi(img)

        metadata = {
            "size": oriented_size(img),  # (width, height) dans le sens d'affichage
            "mode": img.mode,
            "dpi": dpi_value,
            "format": img.format
        }
        img.close()
        return metadata
    except Exception:
        return None


_standard_jpeg_tables = None


def _jpeg_quality_tables():
    """[(qualité, table de luminance standard)] de 100 à 50, calculée une
    seule fois (un encodage JPEG par qualité) : recalculée à chaque appel,
    elle coûtait 51 encodages à chaque enregistrement d'une page JPEG
    modifiée. Deux threads qui la calculent en même temps produisent la même
    liste."""
    global _standard_jpeg_tables
    if _standard_jpeg_tables is None:
        tables = []
        temp_img = Image.new('RGB', (8, 8))
        for quality in range(100, 49, -1):
            try:
                temp_buffer = io.BytesIO()
                temp_img.save(temp_buffer, format='JPEG', quality=quality)
                with Image.open(temp_buffer) as temp_jpeg:
                    std_qtables = temp_jpeg.quantization
                if std_qtables:
                    tables.append((quality, list(std_qtables[0])))
            except Exception:
                pass  # repli : cette qualité manque à la table, l'estimation prend la plus proche
        _standard_jpeg_tables = tables
    return _standard_jpeg_tables


def detect_jpeg_quality(img_bytes):
    """
    Détecte la qualité JPEG originale d'une image en utilisant la méthode des moindres carrés.

    Args:
        img_bytes: bytes de l'image JPEG

    Returns:
        int: Qualité estimée (50-100), ou 95 par défaut
    """
    try:
        with Image.open(io.BytesIO(img_bytes)) as img:
            if img.format != 'JPEG' or not hasattr(img, 'quantization'):
                return 95
            qtables = img.quantization
        if not qtables:
            return 95
        best_quality = 95
        best_match = float('inf')
        # Somme des carrés des différences (moindres carrés)
        for quality, std_table in _jpeg_quality_tables():
            diff = sum((a - b) ** 2 for a, b in zip(qtables[0], std_table))
            if diff < best_match:
                best_match = diff
                best_quality = quality
        return best_quality
    except Exception:
        return 95


# Espace couleur attendu dans l'en-tête d'un profil ICC (octets 16 à 20),
# selon le mode PIL de l'image enregistrée.
_ICC_SPACE_BY_MODE = {
    "RGB": b"RGB ", "RGBA": b"RGB ", "P": b"RGB ", "PA": b"RGB ",
    "L": b"GRAY", "LA": b"GRAY", "1": b"GRAY",
    "I;16": b"GRAY", "I;16B": b"GRAY", "I;16L": b"GRAY", "I": b"GRAY",
    "CMYK": b"CMYK",
}


def _icc_for_mode(icc, mode):
    """Profil ICC à réécrire, ou None s'il ne correspond pas au mode de
    l'image. Pillow conserve img.info["icc_profile"] à travers convert() :
    après un passage CMYK -> RGB ou RGB -> L, réécrire l'ancien profil
    fausserait les couleurs dans les logiciels qui le respectent."""
    if not icc or len(icc) < 20:
        return None
    return icc if icc[16:20] == _ICC_SPACE_BY_MODE.get(mode) else None


def _webp_is_lossless(data):
    """True si ces bytes WebP sont encodés sans perte (bloc VP8L). Parcourt
    les blocs RIFF : un WebP sans perte avec profil ICC ou alpha utilise le
    conteneur étendu (VP8X) où VP8L n'est pas le premier bloc. Un WebP animé
    porte l'image de chaque frame dans un bloc ANMF (16 octets d'en-tête de
    frame, puis ses propres sous-blocs) : c'est la première frame qui décide."""
    if not data or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return False

    def _scan(pos, end):
        while pos + 8 <= end:
            fourcc = data[pos:pos + 4]
            size = int.from_bytes(data[pos + 4:pos + 8], "little")
            if fourcc == b"VP8L":
                return True
            if fourcc == b"VP8 ":
                return False
            if fourcc == b"ANMF":
                return _scan(pos + 8 + 16, min(end, pos + 8 + size))
            pos += 8 + size + (size & 1)
        return False

    return _scan(12, len(data))


def _source_encoding_info(entry):
    """Profil ICC et sous-échantillonnage JPEG des bytes d'origine de
    l'entrée, à reprendre au réenregistrement. Les outils qui recomposent
    l'image (Image.new, collage) perdent img.info : l'ICC est alors repris
    des bytes d'origine. Sous-échantillonnage : -1 si inconnu ou non JPEG."""
    icc, sampling = None, -1
    data = entry.get("bytes")
    if data:
        try:
            from PIL import JpegImagePlugin
            orig = Image.open(io.BytesIO(data))
            icc = orig.info.get("icc_profile")
            if orig.format == "JPEG":
                sampling = JpegImagePlugin.get_sampling(orig)
            orig.close()
        except Exception:
            pass  # repli : ICC absent et sous-échantillonnage inconnu (-1)
    return icc, sampling


def page_icc_profile(img, data, mode=None):
    """Profil ICC à écrire pour l'image img issue de la page dont `data`
    sont les bytes d'origine : celui de img.info, sinon celui des bytes
    d'origine (perdu par les outils qui recomposent l'image : Image.new,
    ImageEnhance…), retenu seulement s'il correspond au mode écrit (`mode`,
    sinon celui de img, voir _icc_for_mode). Règle commune à tous les formats
    et à tous les encodeurs : sans elle, le profil était perdu par une
    luminosité en PNG, TIFF ou AVIF, ou un profil RGB était écrit dans une
    image en niveaux de gris."""
    icc = img.info.get("icc_profile")
    if not icc and data:
        try:
            with Image.open(io.BytesIO(data)) as orig:
                icc = orig.info.get("icc_profile")
        except Exception:
            icc = None
    return _icc_for_mode(icc, mode or img.mode)


def save_image_to_bytes(entry):
    """
    Sauvegarde entry["img"] en bytes en conservant le format original et les métadonnées DPI.

    Args:
        entry: Dictionnaire représentant une image

    Met aussi à jour entry["img_width"]/entry["img_height"] (dimensions
    mémorisées lues par la renumérotation et la détection des pages
    multiples) — sans quoi elles resteraient celles d'avant une rotation, un
    crop ou un redimensionnement.

    Returns:
        bytes: Les données de l'image sauvegardée avec métadonnées DPI si disponibles
    """
    if entry.get("img") is None:
        return entry.get("bytes")

    entry["img_width"], entry["img_height"] = entry["img"].size
    # Une seule image est écrite ici : une animation WebP/PNG/AVIF ou les
    # autres pages d'un TIFF n'existent plus dans les bytes produits.
    entry["is_animated_image"] = False
    entry["is_multipage_tiff"] = False

    img_bytes = io.BytesIO()
    ext = entry.get("extension", ".jpg").lower()

    dpi = page_dpi(entry, entry["img"])

    if ext in JPEG_EXTS:
        # JFIF/PJPEG/PJP sont des variantes/synonymes historiques du format
        # JPEG (JFIF = JPEG File Interchange Format, PJPEG/PJP = JPEG
        # progressif) — mêmes contraintes d'encodage que .jpg/.jpeg.
        original_quality = 95
        if entry.get("bytes"):
            original_quality = detect_jpeg_quality(entry["bytes"])

        img_to_save = entry["img"]
        if img_to_save.mode in ("RGBA", "LA", "P"):
            rgb_img = Image.new("RGB", img_to_save.size, (255, 255, 255))
            if img_to_save.mode == "P":
                img_to_save = img_to_save.convert("RGBA")
            rgb_img.paste(img_to_save, mask=img_to_save.split()[-1] if img_to_save.mode in ("RGBA", "LA") else None)
            img_to_save = rgb_img

        src_icc, sampling = _source_encoding_info(entry)
        jpeg_kwargs = {"quality": original_quality, "optimize": True}
        if dpi:
            jpeg_kwargs["dpi"] = dpi
        # Sous-échantillonnage de l'original repris : sans lui, Pillow
        # applique 4:2:0 et une page en 4:4:4 perd de la couleur à chaque
        # édition.
        if sampling >= 0:
            jpeg_kwargs["subsampling"] = sampling
        icc = _icc_for_mode(entry["img"].info.get("icc_profile") or src_icc, img_to_save.mode)
        if icc:
            jpeg_kwargs["icc_profile"] = icc
        jpeg_kwargs.update(metadata_save_kwargs("JPEG", entry.get("bytes")))
        img_to_save.save(img_bytes, format='JPEG', **jpeg_kwargs)
    elif ext == ".png":
        # icc_profile toujours passé, même None : sinon Pillow reprend celui
        # de img.info, même s'il ne correspond plus au mode de l'image
        png_kwargs = {"optimize": True,
                      "icc_profile": page_icc_profile(entry["img"], entry.get("bytes"))}
        if dpi:
            png_kwargs["dpi"] = dpi
        png_kwargs.update(metadata_save_kwargs("PNG", entry.get("bytes")))
        entry["img"].save(img_bytes, format='PNG', **png_kwargs)
    elif ext == ".webp":
        original_quality = 95
        if entry.get("bytes"):
            original_quality = detect_jpeg_quality(entry["bytes"])

        webp_kwargs = {"quality": original_quality}
        if dpi:
            webp_kwargs["dpi"] = dpi
        # Un WebP sans perte reste sans perte : sinon il deviendrait avec
        # perte (qualité 95) dès la première édition.
        if _webp_is_lossless(entry.get("bytes")):
            webp_kwargs["lossless"] = True
        # WebP ne stocke que RGB/RGBA (Pillow convertit les autres modes) :
        # seul un profil RGB peut être réécrit.
        icc = page_icc_profile(entry["img"], entry.get("bytes"), "RGB")
        if icc:
            webp_kwargs["icc_profile"] = icc
        webp_kwargs.update(metadata_save_kwargs("WEBP", entry.get("bytes")))
        entry["img"].save(img_bytes, format='WEBP', **webp_kwargs)
    elif ext == ".gif":
        entry["img"].save(img_bytes, format='GIF')
    elif ext == ".avif":
        original_quality = 95
        if entry.get("bytes"):
            original_quality = detect_jpeg_quality(entry["bytes"])

        avif_kwargs = {"quality": original_quality,
                       "icc_profile": page_icc_profile(entry["img"], entry.get("bytes"))}
        if dpi:
            avif_kwargs["dpi"] = dpi
        avif_kwargs.update(metadata_save_kwargs("AVIF", entry.get("bytes")))
        entry["img"].save(img_bytes, format='AVIF', **avif_kwargs)
    elif ext == ".ico":
        # ICO : reproduit exactement le jeu de résolutions du fichier
        # d'origine (16/32/48/64/128/256... selon ce qui était réellement
        # présent), pas un nombre de tailles arbitraire — PIL redimensionne
        # lui-même l'image de travail vers chaque taille au moment du save().
        original_sizes = None
        if entry.get("bytes"):
            try:
                orig_img = Image.open(io.BytesIO(entry["bytes"]))
                original_sizes = orig_img.info.get("sizes")
            except Exception:
                pass  # repli : tailles d'icône par défaut de Pillow

        # Pillow écrit nativement l'ICO dans les modes 1/L/P/RGB/RGBA — respecter
        # le mode choisi par l'utilisateur (outils mode d'image/profondeur de
        # couleur de la visionneuse) au lieu de le forcer en RGBA comme le fait
        # ico_creator_qt.py (contexte différent : création d'une icône neuve,
        # transparence voulue par défaut). Seuls LA et CMYK n'ont pas d'écriture
        # ICO directe dans Pillow — convertis vers l'équivalent avec/sans alpha.
        img_to_save = entry["img"]
        if img_to_save.mode == "LA":
            img_to_save = img_to_save.convert("RGBA")
        elif img_to_save.mode == "CMYK":
            img_to_save = img_to_save.convert("RGB")

        if original_sizes:
            img_to_save.save(img_bytes, format='ICO', sizes=list(original_sizes))
        else:
            img_to_save.save(img_bytes, format='ICO')
    elif ext == ".bmp":
        # BMP écrit nativement 1/L/P/RGB/RGBA (RGBA -> BGRA 32 bits) — seuls
        # LA et CMYK n'ont pas d'écriture BMP directe dans Pillow.
        img_to_save = entry["img"]
        if img_to_save.mode == "LA":
            img_to_save = img_to_save.convert("RGBA")
        elif img_to_save.mode == "CMYK":
            img_to_save = img_to_save.convert("RGB")
        img_to_save.save(img_bytes, format='BMP')
    elif ext in (".tiff", ".tif"):
        # TIFF accepte nativement tous les modes PIL rencontrés dans l'appli
        # (1/L/LA/P/RGB/RGBA/CMYK). Seule exception : la transparence d'une
        # palette, que Pillow n'écrit pas en TIFF — RGBA la garde.
        img_to_save = entry["img"]
        if img_to_save.mode == "P" and "transparency" in img_to_save.info:
            img_to_save = img_to_save.convert("RGBA")
        tiff_kwargs = {"icc_profile": page_icc_profile(img_to_save, entry.get("bytes"))}
        if dpi:
            tiff_kwargs["dpi"] = dpi
        tiff_kwargs["compression"] = tiff_save_compression(img_to_save, entry.get("bytes"))
        tiff_kwargs.update(metadata_save_kwargs("TIFF", entry.get("bytes")))
        img_to_save.save(img_bytes, format='TIFF', **tiff_kwargs)
    else:  # format par défaut : PNG pour les autres formats
        if dpi:
            entry["img"].save(img_bytes, format='PNG', dpi=dpi)
        else:
            entry["img"].save(img_bytes, format='PNG')

    return img_bytes.getvalue()
