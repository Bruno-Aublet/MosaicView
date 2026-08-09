# -------------------------
# Redressement automatique (deskew) — détection d'angle + application
# -------------------------
import io
import math
import numpy as np
import cv2
from PIL import Image
from modules.qt.entries import ensure_image_loaded, save_image_to_bytes, free_image_memory

# Nombre minimum de segments bruts détectés par HoughLinesP pour considérer
# qu'il y a assez de signal statistique pour tenter un consensus.
_MIN_SEGMENTS = 5

# Nombre minimum de segments retenus après élimination des valeurs aberrantes
# (voir plus bas) pour faire confiance à leur médiane. Plus bas que
# _MIN_SEGMENTS : un fort consensus (écart-type quasi nul) reste fiable même
# avec peu de segments une fois les faux-positifs écartés.
_MIN_INLIERS = 3

# Écart-type maximal (en degrés) toléré entre les angles des segments retenus
# après élimination des valeurs aberrantes. Au-delà, même le groupe filtré ne
# s'accorde pas sur une inclinaison commune et l'angle n'est pas retenu.
_MAX_ANGLE_STD_DEG = 2.0


def detect_skew_angle(entry):
    """Détecte l'angle d'inclinaison d'une entrée image via la transformée de Hough
    (Canny + HoughLinesP, angle médian des segments dominants détectés).
    Retourne l'angle de correction en degrés (convention identique à
    straighten_viewer_qt.py : positif/négatif directement utilisable par
    PIL.Image.rotate()), ou None si aucun angle fiable n'a pu être déterminé."""
    if not entry.get("is_image"):
        return None

    img = ensure_image_loaded(entry)
    if img is None:
        return None

    gray = np.array(img.convert("L"))
    h, w = gray.shape[:2]

    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    min_line_length = min(w, h) / 2
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100,
                             minLineLength=min_line_length, maxLineGap=20)

    if lines is None or len(lines) < _MIN_SEGMENTS:
        return None

    angles = []
    for line in lines:
        # cv2.HoughLinesP renvoie (N, 4) sur OpenCV 5.x (chaque `line` est déjà
        # [x1, y1, x2, y2]), mais (N, 1, 4) sur d'anciennes versions (4.x) où
        # `line` est [[x1, y1, x2, y2]] — reshape(-1) aplatit les deux formes.
        x1, y1, x2, y2 = np.asarray(line).reshape(-1)
        dx = x2 - x1
        dy = y2 - y1
        if dx == 0 and dy == 0:
            continue
        angle_deg = math.degrees(math.atan2(dy, dx))

        # Normalise dans [-90, 90]
        if angle_deg > 90:
            angle_deg -= 180
        elif angle_deg < -90:
            angle_deg += 180

        # Ramène à l'écart par rapport à l'axe le plus proche (horizontal ou
        # vertical) — même convention que straighten_viewer_qt.py::_on_line_drawn.
        abs_angle = abs(angle_deg)
        if abs_angle <= 45:
            correction = angle_deg
        else:
            correction = angle_deg - 90 if angle_deg >= 0 else angle_deg + 90
        angles.append(correction)

    if len(angles) < _MIN_SEGMENTS:
        return None

    angles = np.array(angles)
    raw_median = float(np.median(angles))

    # Élimine les valeurs aberrantes (un segment isolé qui ne suit pas le
    # consensus, ex. un bord quasi horizontal alors que la page est inclinée)
    # AVANT de mesurer la dispersion — sinon un seul outlier parmi un fort
    # consensus fait échouer le filtre d'écart-type alors que la médiane brute
    # était déjà fiable.
    inliers = angles[np.abs(angles - raw_median) <= _MAX_ANGLE_STD_DEG]

    if len(inliers) < _MIN_INLIERS:
        return None

    std = float(np.std(inliers))
    median = float(np.median(inliers))
    if std > _MAX_ANGLE_STD_DEG:
        return None

    return median


def deskew_entry_data(entry, state=None):
    """Détecte et corrige automatiquement l'inclinaison d'une entrée image.
    Retourne True si un angle fiable a été détecté et appliqué, False sinon
    (entrée non modifiée dans ce cas)."""
    angle = detect_skew_angle(entry)
    if angle is None or abs(angle) < 0.001:
        return False

    img = ensure_image_loaded(entry)
    if img is None:
        return False

    rotated_img = img.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC,
                              fillcolor="white")
    img.close()
    entry["img"] = rotated_img
    entry["bytes"] = save_image_to_bytes(entry)
    entry["img"] = None
    entry["_thumbnail"] = None
    entry["large_thumb_pil"] = None
    entry["qt_pixmap_large"] = None
    entry["qt_qimage_large"] = None
    entry["_hash"] = None

    if state is not None:
        from modules.qt.comic_info import get_page_image_index, update_page_entries_in_xml_data
        idx = get_page_image_index(state, entry)
        if idx is not None:
            update_page_entries_in_xml_data(state, [(idx, entry)])

    free_image_memory(entry)
    return True
