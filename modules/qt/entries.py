# -------------------------
# Fonctions de gestion des entrées (images/fichiers)
# -------------------------
import os
import io
import hashlib
from PIL import Image

from modules.qt import state as _state_module
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
        pass

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


def create_centered_thumbnail(img, thumb_w, thumb_h, background_color=None, checkerboard=False):
    """Crée une miniature centrée sur un fond transparent (ou damier si checkerboard=True)."""
    img_thumb = img.copy()
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
            img.load()  # Force le décodage — déclenche DecompressionBombError si trop grande

            if img.width <= 0 or img.height <= 0:
                raise ValueError(f"Dimensions invalides: {img.width}x{img.height}")

            # Stocke les dimensions pour éviter de rouvrir l'image (ex. renumérotation)
            entry["img_width"]  = img.width
            entry["img_height"] = img.height

            img_dpi = img.info.get('dpi')
            if img_dpi:
                entry["dpi"] = img_dpi[0] if isinstance(img_dpi, tuple) else img_dpi
            else:
                entry["dpi"] = None

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

                entry["gif_loop"] = img.info.get('loop', 0)
                entry["gif_disposal"] = img.info.get('disposal', 2)
                entry["gif_comment"] = img.info.get('comment', b'').decode('utf-8', errors='ignore') if img.info.get('comment') else ""
                entry["gif_optimize"] = True

                img.seek(0)  # repositionne sur la première frame pour l'affichage
                entry["img"] = None  # lazy loading : pas de chargement complet ici
            else:
                entry["is_animated_gif"] = False
                entry["img"] = None

            entry["large_thumb_pil"] = None
        except Image.DecompressionBombError:
            # Relit les dimensions sans MAX_IMAGE_PIXELS pour ne pas redéclencher l'erreur
            _saved = Image.MAX_IMAGE_PIXELS
            Image.MAX_IMAGE_PIXELS = None
            try:
                _tmp = Image.open(io.BytesIO(data))
                w, h = _tmp.width, _tmp.height
            except Exception:
                w, h = 0, 0
            finally:
                Image.MAX_IMAGE_PIXELS = _saved
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

    entry["_hash"] = (
        hashlib.md5(data).hexdigest()
        if is_image and not entry["is_corrupted"] and data
        else None
    )
    entry["_is_duplicate"] = False
    entry["_duplicate_group"] = None

    return entry


def create_entry_from_file(filepath, image_exts):
    """Crée une entrée à partir d'un fichier sur le disque"""
    try:
        if not os.path.exists(filepath):
            return None

        if not os.path.isfile(filepath):
            return None

        max_size = 500 * 1024 * 1024  # 500 Mo, pour éviter les problèmes de mémoire
        file_size = os.path.getsize(filepath)
        if file_size > max_size:
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


def create_entries_from_tiff(filepath, image_exts, add_prefix=False):
    """Crée des entrées pour chaque page d'un fichier TIFF multi-pages"""
    entries = []

    # Essaye d'abord avec tifffile (plus robuste pour les TIFF complexes)
    try:
        import tifffile
        TIFFFILE_AVAILABLE = True
    except ImportError:
        TIFFFILE_AVAILABLE = False

    if TIFFFILE_AVAILABLE:
        try:
            with tifffile.TiffFile(filepath) as tif:
                base_filename = os.path.splitext(os.path.basename(filepath))[0]

                # Essaye d'extraire les SubIFDs s'ils existent, en plus des pages principales
                all_pages = []
                for main_page in tif.pages:
                    all_pages.append(main_page)
                    if hasattr(main_page, 'pages') and main_page.pages is not None and len(main_page.pages) > 0:
                        all_pages.extend(main_page.pages)

                for page_num, page in enumerate(all_pages):
                    try:
                        img_array = page.asarray()
                        img = Image.fromarray(img_array)

                        img_bytes = io.BytesIO()
                        if img.mode not in ('RGB', 'L'):
                            img = img.convert('RGB')
                        img.save(img_bytes, format='JPEG', quality=100)
                        img_bytes.seek(0)

                        filename = f"{base_filename}_page_{page_num + 1:04d}.jpg"
                        if add_prefix:
                            filename = "NEW-" + filename

                        entry = create_entry(filename, img_bytes.getvalue(), image_exts)
                        entry["source"] = "tiff"
                        entry["tiff_page"] = page_num

                        entries.append(entry)

                    except Exception:
                        continue

                if len(entries) == 1 and not add_prefix:
                    entries[0]["orig_name"] = f"{base_filename}.jpg"

                if entries:
                    return entries

        except Exception:
            pass

    # Fallback vers PIL si tifffile n'est pas disponible ou a échoué
    try:
        if not os.path.exists(filepath):
            return entries

        if not os.path.isfile(filepath):
            return entries

        max_size = 500 * 1024 * 1024  # 500 Mo, pour éviter les problèmes de mémoire
        file_size = os.path.getsize(filepath)
        if file_size > max_size:
            raise FileTooLargeError(filepath, file_size)

        base_filename = os.path.splitext(os.path.basename(filepath))[0]

        # Méthode 1 : TiffImagePlugin pour accéder aux IFD
        from PIL import TiffImagePlugin

        try:
            tiff = Image.open(filepath)

            if tiff.format != 'TIFF':
                raise Exception(f"Format non-TIFF : {tiff.format}")

            page_num = 0
            while True:
                try:
                    tiff.seek(page_num)

                    img_bytes = io.BytesIO()
                    frame_copy = tiff.copy()
                    if frame_copy.mode not in ('RGB', 'L'):
                        frame_copy = frame_copy.convert('RGB')
                    frame_copy.save(img_bytes, format='JPEG', quality=100)  # JPEG pour cohérence avec les PDF
                    img_bytes.seek(0)

                    filename = f"{base_filename}_page_{page_num + 1:04d}.jpg"
                    if add_prefix:
                        filename = "NEW-" + filename

                    entry = create_entry(filename, img_bytes.getvalue(), image_exts)
                    entry["source"] = "tiff"
                    entry["tiff_page"] = page_num

                    entries.append(entry)
                    page_num += 1

                except EOFError:
                    break
                except Exception:
                    if page_num == 0:
                        raise
                    break

            tiff.close()

        except FileTooLargeError:
            raise
        except Exception:
            # Méthode 2 : fallback avec Image.open standard
            img = Image.open(filepath)

            from PIL import ImageSequence

            page_num = 0
            for frame in ImageSequence.Iterator(img):
                try:
                    img_bytes = io.BytesIO()
                    frame_copy = frame.copy()
                    if frame_copy.mode not in ('RGB', 'L'):
                        frame_copy = frame_copy.convert('RGB')
                    frame_copy.save(img_bytes, format='JPEG', quality=100)
                    img_bytes.seek(0)

                    filename = f"{base_filename}_page_{page_num + 1:04d}.jpg"

                    if add_prefix:
                        filename = "NEW-" + filename

                    entry = create_entry(filename, img_bytes.getvalue(), image_exts)
                    entry["source"] = "tiff"
                    entry["tiff_page"] = page_num

                    entries.append(entry)
                    page_num += 1

                except Exception:
                    break

            img.close()

        if len(entries) == 1 and not add_prefix:
            entries[0]["orig_name"] = f"{base_filename}.jpg"

    except FileTooLargeError:
        raise
    except Exception:
        pass

    return entries


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
            img = Image.open(io.BytesIO(entry["bytes"]))
            entry["img"] = img.copy()
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
    if not entry.get("is_animated_gif"):
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
        dpi_value = entry.get("dpi") or img.info.get("dpi")

        metadata = {
            "size": img.size,  # (width, height)
            "mode": img.mode,
            "dpi": dpi_value,
            "format": img.format
        }
        img.close()
        return metadata
    except Exception:
        return None


def detect_jpeg_quality(img_bytes):
    """
    Détecte la qualité JPEG originale d'une image en utilisant la méthode des moindres carrés.

    Args:
        img_bytes: bytes de l'image JPEG

    Returns:
        int: Qualité estimée (50-100), ou 95 par défaut
    """
    try:
        img = Image.open(io.BytesIO(img_bytes))
        if img.format == 'JPEG' and hasattr(img, 'quantization'):
            qtables = img.quantization
            if qtables and len(qtables) > 0:
                best_quality = 95
                best_match = float('inf')

                # Test chaque niveau de qualité de 100 à 50
                for quality in range(100, 49, -1):
                    try:
                        # Crée une image temporaire pour obtenir la table standard
                        temp_img = Image.new('RGB', (8, 8))
                        temp_buffer = io.BytesIO()
                        temp_img.save(temp_buffer, format='JPEG', quality=quality)
                        temp_buffer.seek(0)
                        temp_jpeg = Image.open(temp_buffer)
                        std_qtables = temp_jpeg.quantization

                        if std_qtables and len(std_qtables) > 0:
                            # Somme des carrés des différences (moindres carrés)
                            diff = sum((a - b) ** 2 for a, b in zip(qtables[0], std_qtables[0]))
                            if diff < best_match:
                                best_match = diff
                                best_quality = quality

                        temp_jpeg.close()
                        temp_buffer.close()
                    except:
                        pass

                img.close()
                return best_quality
        img.close()
    except:
        pass
    return 95


def save_image_to_bytes(entry):
    """
    Sauvegarde entry["img"] en bytes en conservant le format original et les métadonnées DPI.

    Args:
        entry: Dictionnaire représentant une image

    Returns:
        bytes: Les données de l'image sauvegardée avec métadonnées DPI si disponibles
    """
    if entry.get("img") is None:
        return entry.get("bytes")

    img_bytes = io.BytesIO()
    ext = entry.get("extension", ".jpg").lower()

    dpi_value = entry.get("dpi")
    if isinstance(dpi_value, tuple):
        dpi_value = dpi_value[0]
    if not dpi_value:
        # Pas de DPI dans entry : essaie de le récupérer depuis l'image PIL
        img_info_dpi = entry["img"].info.get("dpi")
        if img_info_dpi:
            dpi_value = img_info_dpi[0] if isinstance(img_info_dpi, tuple) else img_info_dpi

    if ext in (".jpg", ".jpeg", ".jfif", ".pjpeg", ".pjp"):
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

        if dpi_value:
            img_to_save.save(img_bytes, format='JPEG', quality=original_quality, optimize=True, dpi=(dpi_value, dpi_value))
        else:
            img_to_save.save(img_bytes, format='JPEG', quality=original_quality, optimize=True)
    elif ext == ".png":
        if dpi_value:
            entry["img"].save(img_bytes, format='PNG', optimize=True, dpi=(dpi_value, dpi_value))
        else:
            entry["img"].save(img_bytes, format='PNG', optimize=True)
    elif ext == ".webp":
        original_quality = 95
        if entry.get("bytes"):
            original_quality = detect_jpeg_quality(entry["bytes"])

        if dpi_value:
            entry["img"].save(img_bytes, format='WEBP', quality=original_quality, dpi=(dpi_value, dpi_value))
        else:
            entry["img"].save(img_bytes, format='WEBP', quality=original_quality)
    elif ext == ".gif":
        entry["img"].save(img_bytes, format='GIF')
    elif ext == ".avif":
        original_quality = 95
        if entry.get("bytes"):
            original_quality = detect_jpeg_quality(entry["bytes"])

        if dpi_value:
            entry["img"].save(img_bytes, format='AVIF', quality=original_quality, dpi=(dpi_value, dpi_value))
        else:
            entry["img"].save(img_bytes, format='AVIF', quality=original_quality)
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
                pass

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
        # (1/L/LA/P/RGB/RGBA/CMYK) — aucune conversion nécessaire.
        if dpi_value:
            entry["img"].save(img_bytes, format='TIFF', dpi=(dpi_value, dpi_value))
        else:
            entry["img"].save(img_bytes, format='TIFF')
    else:  # format par défaut : PNG pour les autres formats
        if dpi_value:
            entry["img"].save(img_bytes, format='PNG', dpi=(dpi_value, dpi_value))
        else:
            entry["img"].save(img_bytes, format='PNG')

    return img_bytes.getvalue()
