"""
modules/qt/image_processing_qt.py — Logique PIL pour les ajustements d'images.

Indépendant de l'UI (pas de Qt). Moteur de calcul partagé par les outils de la
barre d'outils flottante de la visionneuse principale (viewers) —
sharpness_tool_qt.py, brightness_tool_qt.py, saturation_tool_qt.py,
remove_colors_tool_qt.py, compression_tool_qt.py, levels_tool_qt.py,
color_depth_tool_qt.py, effects_tool_qt.py, image_mode_tool_qt.py.

Fonctions publiques :
  detect_jpeg_quality(image_bytes) -> int | None
  apply_adjustments(img, settings)  -> PIL.Image
  apply_image_adjustments(selected_entries, settings, callbacks=None)
"""

import io
import os

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from modules.qt import state as _state_module


# ─────────────────────────────────────────────────────────────────────────────
# Détection qualité JPEG
# ─────────────────────────────────────────────────────────────────────────────

def detect_jpeg_quality(image_bytes):
    """Détecte la qualité JPEG approximative d'une image. Retourne None si pas JPEG."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.format not in ('JPEG', 'JPG'):
            return None
        if hasattr(img, 'quantization'):
            qtables = img.quantization
            if qtables and len(qtables) > 0:
                avg_q = sum(qtables[0]) / len(qtables[0])
                if avg_q < 15:   return 95
                elif avg_q < 25: return 85
                elif avg_q < 40: return 75
                elif avg_q < 60: return 60
                else:            return 50
        return 85
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Logique d'ajustement PIL (prévisualisation ET application réelle)
# ─────────────────────────────────────────────────────────────────────────────

# Formats compressés dans leur propre encodeur par la simulation de
# compression (les autres compressibles passent par une simulation JPEG).
_NATIVE_COMPRESSION_FORMATS = {'.webp': 'WEBP', '.avif': 'AVIF'}

# Clé de img.info portant les bytes déjà encodés par apply_adjustments, à
# reprendre tels quels plutôt que de ré-encoder l'image.
ENCODED_BYTES_KEY = '_mosaicview_encoded_bytes'


def _to_filterable(img):
    """Convertit img dans un mode accepté par ImageEnhance/ImageFilter.

    P, 1 et les modes 16/32 bits lèvent "image has wrong mode" ou "cannot
    filter palette images". CMYK est accepté mais donne un résultat inversé
    (ImageEnhance.Brightness mélange vers l'image "sans encre", donc blanche) :
    il passe aussi en RGB."""
    mode = img.mode
    if mode in ('RGB', 'RGBA', 'L', 'LA'):
        return img
    if mode in ('P', 'PA'):
        has_alpha = mode == 'PA' or 'transparency' in img.info
        return img.convert('RGBA' if has_alpha else 'RGB')
    if mode == '1':
        return img.convert('L')
    if mode.startswith('I'):
        # Valeurs 16 bits : un convert('L') direct écrête tout au-dessus de
        # 255 (image quasi blanche) — on garde les 8 bits de poids fort.
        arr = np.asarray(img, dtype=np.int64)
        if arr.max() > 255:
            arr = arr >> 8
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'L')
    return img.convert('RGB')


def _extract_alpha(img):
    """Canal alpha de img (ou de sa transparence de palette), sinon None."""
    if img.mode in ('RGBA', 'LA', 'PA'):
        return img.getchannel('A')
    if img.mode == 'P' and 'transparency' in img.info:
        return img.convert('RGBA').getchannel('A')
    return None



def apply_adjustments(img, settings, for_preview=False):
    """Applique les réglages à une image PIL et retourne l'image résultante.

    settings : dict avec les clés optionnelles :
      brightness, contrast, sharpness, saturation  (int, -100..+100)
      effect        ('none' | 'grayscale' | 'sepia' | 'invert')
      remove_colors_intensity  (int, 0..100)
      threshold     (int, 0..255, défaut 128 = inactif)
      black_point   (int, 0..255)
      white_point   (int, 0..255)
      gamma         (float, 0.1..3.0)
      image_mode    ('unchanged' | 'RGB' | 'RGBA' | 'L' | 'LA' | 'CMYK' | 'BW1' | 'P')
      color_depth   ('unchanged' | '32' | '24' | '8' | '1')
      compression_quality  (int, 1..100)
      unsharp_radius    (float, 0.5..5.0)
      unsharp_percent   (int, 0..200)
      unsharp_threshold (int, 0..30)
    for_preview : si True, reconvertit toujours en RGB/RGBA pour l'affichage Qt
    """
    brightness       = settings.get('brightness',  0)
    contrast         = settings.get('contrast',    0)
    sharpness        = settings.get('sharpness',   0)
    saturation       = settings.get('saturation',  0)
    effect           = settings.get('effect',     'none')
    remove_int       = settings.get('remove_colors_intensity', 0)
    threshold        = settings.get('threshold',  128)
    black_pt         = settings.get('black_point',  0)
    white_pt         = settings.get('white_point', 255)
    gamma            = settings.get('gamma',       1.0)
    image_mode       = settings.get('image_mode', 'unchanged')
    color_depth      = settings.get('color_depth', 'unchanged')
    comp_q           = settings.get('compression_quality', 100)
    unsharp_radius   = settings.get('unsharp_radius',    2.0)
    unsharp_percent  = settings.get('unsharp_percent',   0)
    unsharp_thresh   = settings.get('unsharp_threshold', 3)

    # Seuil, niveaux et suppression des couleurs repassent l'image en RGB :
    # l'alpha d'origine est mémorisé pour être remis après ces blocs.
    tone_ops = (remove_int > 0 or threshold != 128
                or black_pt != 0 or white_pt != 255 or gamma != 1.0)
    orig_alpha = _extract_alpha(img) if tone_ops else None

    if brightness != 0 or contrast != 0 or sharpness != 0 or saturation != 0 or unsharp_percent > 0:
        img = _to_filterable(img)

    # ── Luminosité / contraste / netteté / saturation ─────────────────────────
    if brightness != 0:
        img = ImageEnhance.Brightness(img).enhance(1.0 + brightness / 100.0)
    if contrast != 0:
        img = ImageEnhance.Contrast(img).enhance(1.0 + contrast / 100.0)
    if sharpness != 0:
        if sharpness > 0:
            img = ImageEnhance.Sharpness(img).enhance(1.0 + sharpness / 100.0)
        else:
            img = img.filter(ImageFilter.GaussianBlur(abs(sharpness) / 20.0))
    if saturation != 0:
        img = ImageEnhance.Color(img).enhance(max(0.0, 1.0 + saturation / 100.0))

    # ── Netteté adaptative (Unsharp Mask) ─────────────────────────────────────
    if unsharp_percent > 0:
        img = img.filter(ImageFilter.UnsharpMask(
            radius=unsharp_radius,
            percent=unsharp_percent,
            threshold=unsharp_thresh,
        ))

    # ── Effets ────────────────────────────────────────────────────────────────
    if effect == 'grayscale':
        img = ImageOps.grayscale(img).convert('RGB')
    elif effect == 'sepia':
        img = ImageOps.grayscale(img).convert('RGB')
        arr = np.array(img, dtype=np.float32)
        r = np.clip(arr[:,:,0]*0.393 + arr[:,:,1]*0.769 + arr[:,:,2]*0.189, 0, 255)
        g = np.clip(arr[:,:,0]*0.349 + arr[:,:,1]*0.686 + arr[:,:,2]*0.168, 0, 255)
        b = np.clip(arr[:,:,0]*0.272 + arr[:,:,1]*0.534 + arr[:,:,2]*0.131, 0, 255)
        img = Image.fromarray(np.stack([r, g, b], axis=2).astype(np.uint8))
    elif effect == 'invert':
        if img.mode == 'RGBA':
            r, g, b, a = img.split()
            img = Image.merge('RGBA', (ImageOps.invert(r), ImageOps.invert(g),
                                       ImageOps.invert(b), a))
        else:
            img = ImageOps.invert(img.convert('RGB'))

    # ── Suppression des couleurs ──────────────────────────────────────────────
    if remove_int > 0:
        intensity = remove_int
        gm   = 0.5  - (intensity / 100.0) * 0.25
        ct   = 1.5  + (intensity / 100.0) * 1.5
        thr  = int(150 - (intensity / 100.0) * 50)
        mult = 2.0  + (intensity / 100.0) * 2.5
        bst  = 1.2  + (intensity / 100.0) * 0.4
        img  = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=1)
        arr  = np.power(np.array(img, dtype=np.float32) / 255.0, gm) * 255
        img  = ImageEnhance.Contrast(Image.fromarray(arr.astype(np.uint8))).enhance(ct)
        arr  = np.array(img, dtype=np.float32)
        arr  = np.where(arr > thr, thr + (arr - thr) * mult, arr)
        arr  = np.where((arr > 80) & (arr < 200), arr * bst, arr)
        img  = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert('RGB')

    # ── Seuil ─────────────────────────────────────────────────────────────────
    if threshold != 128:
        img = img.convert('L').point(lambda p: 255 if p > threshold else 0).convert('RGB')

    # ── Niveaux avancés (point noir / gamma / point blanc) ────────────────────
    if black_pt != 0 or white_pt != 255 or gamma != 1.0:
        img = img.convert('RGB')
        lut = []
        for i in range(256):
            n = (i - black_pt) / (white_pt - black_pt) if white_pt != black_pt else 0
            lut.append(int(pow(max(0, min(1, n)), 1.0 / gamma) * 255))
        r, g, b = img.split()
        img = Image.merge('RGB', (r.point(lut), g.point(lut), b.point(lut)))

    # Remise de l'alpha perdu par seuil/niveaux/suppression des couleurs.
    # Pas après niveaux de gris/sépia : leur perte d'alpha est le
    # comportement documenté de l'outil Effets (skill adjust-effects).
    if orig_alpha is not None and img.mode == 'RGB' and effect in ('none', 'invert') \
            and img.size == orig_alpha.size:
        img = img.convert('RGBA')
        img.putalpha(orig_alpha)

    # ── Mode d'image ──────────────────────────────────────────────────────────
    if image_mode != 'unchanged':
        if image_mode == 'BW1':
            img = img.convert('L').point(lambda p: 255 if p > 128 else 0, '1')
        else:
            try:
                img = img.convert(image_mode)
            except Exception:
                pass
        # Reconversion pour affichage Qt (preview uniquement)
        if for_preview:
            if img.mode == '1':
                buf = io.BytesIO()
                img.save(buf, format='PNG')
                buf.seek(0)
                img = Image.open(buf).convert('RGB')
            elif img.mode not in ('RGB', 'RGBA', 'L'):
                try:
                    img = img.convert('RGB')
                except Exception:
                    pass

    # ── Profondeur de couleur ─────────────────────────────────────────────────
    original_ext = settings.get('original_ext', '')
    if color_depth == '32':
        img = img.convert('RGBA') if img.mode != 'RGBA' else img
    elif color_depth == '24':
        img = img.convert('RGB')  if img.mode != 'RGB'  else img
    elif color_depth == '8':
        if original_ext in ('.jpg', '.jpeg', '.jfif', '.pjpeg', '.pjp'):
            img = img.convert('L')
        else:
            # Jamais de reconversion RGB silencieuse après coup — le choix
            # "8 bits" doit rester réellement en mode P (palette).
            img = img.convert('P', palette=Image.ADAPTIVE, colors=256)
    elif color_depth == '1':
        img = img.convert('L').point(lambda p: 255 if p > 128 else 0, '1')
        if for_preview:
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            img = Image.open(buf).convert('RGB')

    # ── Simulation compression JPEG ───────────────────────────────────────────
    native_fmt = _NATIVE_COMPRESSION_FORMATS.get(original_ext)
    if comp_q < 100 and native_fmt:
        # WebP/AVIF : encodage direct dans le format de la page, à la qualité
        # demandée. Conserve l'alpha (une simulation JPEG l'aplatirait sur
        # fond blanc), et les bytes produits sont repris tels quels par
        # apply_image_adjustments (ENCODED_BYTES_KEY) au lieu d'être
        # ré-encodés une seconde fois à qualité 95.
        if img.mode not in ('RGB', 'RGBA', 'L'):
            img = img.convert('RGBA' if _extract_alpha(img) is not None else 'RGB')
        buf = io.BytesIO()
        img.save(buf, format=native_fmt, quality=comp_q)
        data = buf.getvalue()
        img = Image.open(io.BytesIO(data))
        img.load()
        if not for_preview:
            img.info[ENCODED_BYTES_KEY] = data
    elif comp_q < 100 and color_depth not in ('1', '32') and image_mode not in ('BW1', 'RGBA', 'LA', 'CMYK', 'P'):
        if img.mode == 'RGBA':
            bg = Image.new('RGB', img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=comp_q, optimize=True)
        buf.seek(0)
        img = Image.open(buf)

    return img


# ─────────────────────────────────────────────────────────────────────────────
# Calcul automatique des niveaux noir/blanc
# ─────────────────────────────────────────────────────────────────────────────

def compute_auto_levels(image_bytes):
    """Calcule les points noir/blanc automatiques (percentiles 1%/99%) pour une image.

    Retourne (black_val, white_val) en entiers [0..255], ou (0, 255) si échec.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        arr = np.array(img).reshape(-1, 3)
        cutoff_count = int(len(arr) * 0.01)
        merged = arr.mean(axis=1)
        sorted_vals = sorted(merged)
        if len(sorted_vals) > 2 * cutoff_count:
            black_val = int(sorted_vals[cutoff_count])
            white_val = int(sorted_vals[-(cutoff_count + 1)])
        else:
            black_val = int(sorted_vals[0])
            white_val = int(sorted_vals[-1])
        black_val = max(0, min(254, black_val))
        white_val = max(black_val + 1, min(255, white_val))
        return black_val, white_val
    except Exception:
        return 0, 255


# ─────────────────────────────────────────────────────────────────────────────
# Application réelle aux images de la mosaïque
# ─────────────────────────────────────────────────────────────────────────────

def apply_image_adjustments(selected_entries, settings, callbacks=None, skip_history=False,
                            raise_errors=False):
    """Applique les ajustements aux images sélectionnées et met à jour state.

    callbacks : dict avec les clés :
      save_state      : callable
      render_mosaic   : callable

    skip_history=True : saute les 2 save_state ci-dessous, laissés à la charge
    de l'appelant (lecture headless d'une macro, macro_engine.py — un seul
    save_state global pour toute la lecture plutôt qu'un par outil). Le reste
    du traitement (formule PIL, invalidation de caches) est inchangé.

    raise_errors=True : une image sans bytes ou en erreur lève une exception
    au lieu d'être ignorée. Utilisé par les perform_* de la visionneuse, qui
    traitent une seule page et doivent retourner False sur un échec réel —
    sans quoi une lecture de macro compterait la page comme réussie.
    """
    from modules.qt.entries import save_image_to_bytes

    callbacks  = callbacks or {}
    state      = callbacks.get('state') or _state_module.state
    save_state = callbacks.get('save_state')
    render     = callbacks.get('render_mosaic')

    if not selected_entries or not state.images_data:
        return

    # Sauvegarde l'état AVANT modification (pour le undo) — sans force, évite le doublon
    if save_state and not skip_history:
        save_state()

    for entry in selected_entries:
        try:
            if not entry.get('bytes'):
                if raise_errors:
                    raise ValueError(entry.get('orig_name', ''))
                continue
            settings_for_entry = dict(settings)
            original_ext = entry.get('extension', '').lower()
            settings_for_entry['original_ext'] = original_ext

            img = Image.open(io.BytesIO(entry['bytes']))
            img = apply_adjustments(img, settings_for_entry)

            # JPEG (et ses synonymes JFIF/PJPEG/PJP) ne supporte pas RGBA, LA,
            # P ni le mode 1 bit → force PNG
            save_ext = original_ext
            if img.mode in ('RGBA', 'LA', 'P', '1') and original_ext in ('.jpg', '.jpeg', '.jfif', '.pjpeg', '.pjp'):
                save_ext = '.png'

            encoded = img.info.pop(ENCODED_BYTES_KEY, None)
            entry['img'] = img
            entry['extension'] = save_ext
            if encoded is not None and save_ext == original_ext:
                entry['bytes'] = encoded
                entry['img_width'], entry['img_height'] = img.size
                entry['is_animated_image'] = False
            else:
                entry['bytes'] = save_image_to_bytes(entry)
            entry['img'] = None
            if save_ext != original_ext:
                base_name = os.path.splitext(entry.get('orig_name', ''))[0]
                entry['orig_name'] = base_name + save_ext
            entry['_thumbnail'] = None
            entry['large_thumb_pil'] = None
            entry['qt_pixmap_large'] = None
            entry['qt_qimage_large'] = None
            entry['_hash'] = None
        except Exception:
            if raise_errors:
                raise

    state.modified = True

    # Met à jour les métadonnées <Page> pour les entrées modifiées
    from modules.qt.comic_info import get_page_image_index, update_page_entries_in_xml_data
    pairs = [(get_page_image_index(state, e), e) for e in selected_entries if e.get('bytes')]
    pairs = [(i, e) for i, e in pairs if i is not None]
    if pairs:
        update_page_entries_in_xml_data(state, pairs)

    # Sauvegarde l'état APRÈS modification (pour le redo)
    if save_state and not skip_history:
        save_state(force=True)

    if render:
        render()
