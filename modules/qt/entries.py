# -------------------------
# Fonctions de gestion des entrées (images/fichiers)
# -------------------------
import os
import io
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

    # Si l'entrée est marquée comme corrompue, utilise l'icône spéciale
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

    # Ne fonctionne que pour JPG, JPEG et WEBP
    if ext not in [".jpg", ".jpeg", ".webp"]:
        return None

    try:
        # Récupère les métadonnées sans charger l'image complète
        metadata = get_image_metadata(entry)
        if metadata is None:
            return None

        img_bytes = entry.get("bytes")
        if not img_bytes:
            return None

        # Taille du fichier compressé
        compressed_size = len(img_bytes)

        # Taille théorique non compressée (largeur × hauteur × 3 bytes pour RGB)
        width, height = metadata["size"]
        uncompressed_size = width * height * 3

        # Calcule le taux de compression (pourcentage de réduction)
        if uncompressed_size > 0:
            compression_rate = (1 - (compressed_size / uncompressed_size)) * 100
            # Limite entre 0 et 100%
            compression_rate = max(0, min(100, compression_rate))
            return round(compression_rate, 1)

    except Exception:
        pass

    return None


def _make_checkerboard_pil(w: int, h: int, tile: int = 8) -> Image.Image:
    """Génère une image PIL damier RGBA (gris clair / gris foncé)."""
    light = (200, 200, 200, 255)
    dark  = (160, 160, 160, 255)
    bg = Image.new('RGBA', (w, h))
    pixels = bg.load()
    for y in range(h):
        for x in range(w):
            pixels[x, y] = light if ((x // tile) + (y // tile)) % 2 == 0 else dark
    return bg


def create_centered_thumbnail(img, thumb_w, thumb_h, background_color=None, checkerboard=False):
    """Crée une miniature centrée sur un fond transparent (ou damier si checkerboard=True)."""
    # Redimensionne l'image en conservant le ratio
    img_thumb = img.copy()
    img_thumb.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)

    # Calcule la position pour centrer l'image
    x_offset = (thumb_w - img_thumb.width) // 2
    y_offset = (thumb_h - img_thumb.height) // 2

    # Convertit l'image en RGBA si nécessaire pour gérer la transparence
    if img_thumb.mode != 'RGBA':
        img_thumb = img_thumb.convert('RGBA')

    if checkerboard:
        background = _make_checkerboard_pil(thumb_w, thumb_h)
    else:
        # Fond transparent — le bg du canvas sera visible derrière
        background = Image.new('RGBA', (thumb_w, thumb_h), (0, 0, 0, 0))

    # Colle l'image redimensionnée au centre du fond
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
            # Vérifie que les données ne sont pas vides
            if len(data) == 0:
                raise ValueError("Fichier image vide")

            img = Image.open(io.BytesIO(data))
            img.verify()
            img = Image.open(io.BytesIO(data))
            img.load()  # Force le décodage — déclenche DecompressionBombError si trop grande

            # Vérifie que l'image a des dimensions valides
            if img.width <= 0 or img.height <= 0:
                raise ValueError(f"Dimensions invalides: {img.width}x{img.height}")

            # Stocke les dimensions pour éviter de rouvrir l'image (ex. renumérotation)
            entry["img_width"]  = img.width
            entry["img_height"] = img.height

            # Extrait et stocke le DPI de l'image
            img_dpi = img.info.get('dpi')
            if img_dpi:
                entry["dpi"] = img_dpi[0] if isinstance(img_dpi, tuple) else img_dpi
            else:
                entry["dpi"] = None

            # Détecte si c'est un GIF animé AVANT de copier l'image
            if entry_ext.lower() == '.gif' and hasattr(img, 'n_frames') and img.n_frames > 1:
                entry["is_animated_gif"] = True
                entry["gif_frame_count"] = img.n_frames  # LAZY LOADING : Stocke seulement le nombre de frames
                entry["gif_durations"] = []

                # Extrait seulement les durées (léger en mémoire)
                for frame_idx in range(img.n_frames):
                    img.seek(frame_idx)
                    # Durée en millisecondes (défaut 100ms si non spécifié)
                    duration = img.info.get('duration', 100)
                    entry["gif_durations"].append(duration)

                # Stocke TOUTES les métadonnées du GIF
                entry["gif_loop"] = img.info.get('loop', 0)
                entry["gif_disposal"] = img.info.get('disposal', 2)
                entry["gif_comment"] = img.info.get('comment', b'').decode('utf-8', errors='ignore') if img.info.get('comment') else ""
                entry["gif_optimize"] = True  # On suppose que le GIF était optimisé

                # Repositionne sur la première frame pour l'affichage
                img.seek(0)
                # LAZY LOADING : On ne stocke PAS l'image complète au chargement
                entry["img"] = None
            else:
                entry["is_animated_gif"] = False
                # LAZY LOADING : On ne stocke PAS l'image complète au chargement
                entry["img"] = None

            entry["large_thumb_pil"] = None
        except Image.DecompressionBombError:
            # Lire les dimensions sans déclencher à nouveau l'erreur
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
            # Garde is_image = True pour que le fichier soit reconnu comme image corrompue
            entry["is_animated_gif"] = False
    else:
        entry["img"] = None
    return entry


def create_entry_from_file(filepath, image_exts):
    """Crée une entrée à partir d'un fichier sur le disque"""
    try:
        # Vérifie que le fichier existe et est accessible
        if not os.path.exists(filepath):
            print(f"Fichier introuvable : {filepath}")
            return None

        if not os.path.isfile(filepath):
            print(f"Chemin n'est pas un fichier : {filepath}")
            return None

        # Limite la taille des fichiers à 500 Mo pour éviter les problèmes de mémoire
        max_size = 500 * 1024 * 1024
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
        print(f"Permission refusée pour {filepath}")
        return None
    except Exception as e:
        print(f"Erreur lors du chargement de {filepath} : {e}")
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

                # Essaye d'extraire les SubIFDs s'ils existent
                all_pages = []
                for main_page in tif.pages:
                    all_pages.append(main_page)
                    if hasattr(main_page, 'pages') and main_page.pages is not None and len(main_page.pages) > 0:
                        all_pages.extend(main_page.pages)

                # Itère sur toutes les pages (incluant SubIFDs)
                for page_num, page in enumerate(all_pages):
                    try:

                        # Lit les données de la page
                        img_array = page.asarray()

                        # Convertit en PIL Image
                        img = Image.fromarray(img_array)

                        # Convertit en bytes JPEG
                        img_bytes = io.BytesIO()
                        if img.mode not in ('RGB', 'L'):
                            img = img.convert('RGB')
                        img.save(img_bytes, format='JPEG', quality=100)
                        img_bytes.seek(0)

                        # Crée le nom de fichier
                        filename = f"{base_filename}_page_{page_num + 1:04d}.jpg"

                        # Ajoute le préfixe NEW- si demandé
                        if add_prefix:
                            filename = "NEW-" + filename

                        # Utilise create_entry
                        entry = create_entry(filename, img_bytes.getvalue(), image_exts)
                        entry["source"] = "tiff"
                        entry["tiff_page"] = page_num

                        entries.append(entry)

                    except Exception:
                        continue

                # Si une seule page, renomme
                if len(entries) == 1 and not add_prefix:
                    entries[0]["orig_name"] = f"{base_filename}.jpg"

                if entries:
                    return entries

        except Exception:
            pass

    # Fallback vers PIL si tifffile n'est pas disponible ou a échoué
    try:
        # Vérifie que le fichier existe et est accessible
        if not os.path.exists(filepath):
            return entries

        if not os.path.isfile(filepath):
            return entries

        # Limite la taille des fichiers à 500 Mo pour éviter les problèmes de mémoire
        max_size = 500 * 1024 * 1024
        file_size = os.path.getsize(filepath)
        if file_size > max_size:
            raise FileTooLargeError(filepath, file_size)

        base_filename = os.path.splitext(os.path.basename(filepath))[0]

        # Méthode 1 : Essaye avec TiffImagePlugin pour accéder aux IFD
        from PIL import TiffImagePlugin

        try:
            tiff = Image.open(filepath)

            if tiff.format != 'TIFF':
                raise Exception(f"Format non-TIFF : {tiff.format}")

            page_num = 0
            while True:
                try:
                    tiff.seek(page_num)

                    # Convertit la frame en bytes
                    img_bytes = io.BytesIO()
                    # Sauvegarde en JPEG pour cohérence avec les PDF
                    frame_copy = tiff.copy()
                    if frame_copy.mode not in ('RGB', 'L'):
                        frame_copy = frame_copy.convert('RGB')
                    frame_copy.save(img_bytes, format='JPEG', quality=100)
                    img_bytes.seek(0)

                    # Crée le nom de fichier
                    filename = f"{base_filename}_page_{page_num + 1:04d}.jpg"

                    # Ajoute le préfixe NEW- si demandé (pour la fusion)
                    if add_prefix:
                        filename = "NEW-" + filename

                    # Utilise create_entry pour créer l'entrée correctement
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
            # Méthode 2 : Fallback avec Image.open standard
            img = Image.open(filepath)

            from PIL import ImageSequence

            page_num = 0
            for frame in ImageSequence.Iterator(img):
                try:
                    # Convertit la frame en bytes
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

        # Si une seule page a été extraite, on renomme sans le suffixe _page_0001
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
    # Si l'image est déjà chargée, on la retourne directement
    if entry.get("img") is not None:
        return entry["img"]

    # Image déjà marquée corrompue : pas la peine de retenter
    if entry.get("is_corrupted"):
        return None

    # Si on n'a pas de bytes ou que ce n'est pas une image, on ne peut rien faire
    if not entry.get("is_image") or entry.get("bytes") is None:
        return None

    # Charge l'image depuis les bytes
    try:
        # Gestion spéciale pour les GIF animés
        if entry.get("is_animated_gif"):
            img = Image.open(io.BytesIO(entry["bytes"]))
            # Les frames sont déjà stockées, on recharge juste l'image de base
            img.seek(0)
            entry["img"] = img.copy()
            img.close()
        else:
            # Image normale
            img = Image.open(io.BytesIO(entry["bytes"]))
            entry["img"] = img.copy()
            img.close()

        return entry["img"]
    except Exception as e:
        print(f"Erreur lors du chargement lazy de l'image {entry.get('orig_name', 'inconnue')} : {e}")
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
    # Vérifie que c'est bien un GIF animé
    if not entry.get("is_animated_gif"):
        return None

    # Vérifie que l'index est valide
    frame_count = entry.get("gif_frame_count", 0)
    if frame_idx < 0 or frame_idx >= frame_count:
        return None

    # Vérifie qu'on a les bytes
    if entry.get("bytes") is None:
        return None

    try:
        # Charge le GIF depuis les bytes
        img = Image.open(io.BytesIO(entry["bytes"]))

        # Se positionne sur la frame demandée
        img.seek(frame_idx)

        # Copie et convertit la frame en RGBA
        frame = img.copy().convert("RGBA")

        # Ferme l'image source pour libérer les ressources
        img.close()

        return frame
    except Exception as e:
        print(f"Erreur lors du chargement de la frame {frame_idx} du GIF {entry.get('orig_name', 'inconnu')} : {e}")
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
        # Sinon, utilise le DPI des métadonnées de l'image
        dpi_value = entry.get("dpi") or img.info.get("dpi")

        metadata = {
            "size": img.size,  # (width, height)
            "mode": img.mode,
            "dpi": dpi_value,
            "format": img.format
        }
        # Ferme l'image immédiatement pour libérer les ressources
        img.close()
        return metadata
    except Exception as e:
        print(f"Erreur lecture métadonnées {entry.get('orig_name', 'inconnue')} : {e}")
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

    # Récupère le DPI depuis entry (s'il existe)
    dpi_value = entry.get("dpi")
    # Si dpi_value est un tuple, prend la première valeur
    if isinstance(dpi_value, tuple):
        dpi_value = dpi_value[0]
    # Si pas de DPI dans entry, essaie de le récupérer depuis l'image PIL
    if not dpi_value:
        img_info_dpi = entry["img"].info.get("dpi")
        if img_info_dpi:
            dpi_value = img_info_dpi[0] if isinstance(img_info_dpi, tuple) else img_info_dpi

    if ext in [".jpg", ".jpeg"]:
        # JPEG : détecte la qualité originale et sauvegarde avec cette qualité
        # Détecte la qualité depuis les bytes originaux
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

        # Sauvegarde avec DPI si disponible
        if dpi_value:
            img_to_save.save(img_bytes, format='JPEG', quality=original_quality, optimize=True, dpi=(dpi_value, dpi_value))
        else:
            img_to_save.save(img_bytes, format='JPEG', quality=original_quality, optimize=True)
    elif ext == ".png":
        # Sauvegarde PNG avec DPI si disponible
        if dpi_value:
            entry["img"].save(img_bytes, format='PNG', optimize=True, dpi=(dpi_value, dpi_value))
        else:
            entry["img"].save(img_bytes, format='PNG', optimize=True)
    elif ext == ".webp":
        # WEBP : détecte la qualité originale
        original_quality = 95
        if entry.get("bytes"):
            original_quality = detect_jpeg_quality(entry["bytes"])

        # Sauvegarde WEBP avec DPI si disponible
        if dpi_value:
            entry["img"].save(img_bytes, format='WEBP', quality=original_quality, dpi=(dpi_value, dpi_value))
        else:
            entry["img"].save(img_bytes, format='WEBP', quality=original_quality)
    elif ext == ".gif":
        entry["img"].save(img_bytes, format='GIF')
    else:
        # Format par défaut : PNG pour les autres formats
        if dpi_value:
            entry["img"].save(img_bytes, format='PNG', dpi=(dpi_value, dpi_value))
        else:
            entry["img"].save(img_bytes, format='PNG')

    return img_bytes.getvalue()
