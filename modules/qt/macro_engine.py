"""
Moteur de stockage/validation des macros de la visionneuse principale.

Une macro est une séquence d'étapes (une par action enregistrée dans la visionneuse),
stockée en pixels absolus fixes (jamais en coordonnées relatives à la page). Chaque macro
vit dans son propre fichier JSON, nommé d'après le nom de la macro, dans
%APPDATA%\\MosaicView\\macros\\.

Contient la lecture/écriture disque, la validation de nom, le contrôle des bornes
d'une étape (step_fits_page), le dispatcher par outil (apply_step_to_entry), la
boucle de lecture (run_macro_on_entries) et l'annulation d'une lecture
interrompue (rollback_macro_reading). Aucun import Qt au niveau du module : seule
run_macro_on_entries importe PySide6, à l'appel.
"""

import json
import os
import re
import traceback

from modules.qt.undo_redo import history_top


# ─────────────────────────────────────────────────────────────────────────────
# Emplacement de stockage
# ─────────────────────────────────────────────────────────────────────────────

MACROS_SUBDIR = "macros"

# Longueur maximale d'un nom de macro (donc du nom de fichier, hors extension).
MAX_MACRO_NAME_LENGTH = 30

# Caractères interdits dans un nom de fichier Windows.
_INVALID_NAME_CHARS_RE = re.compile(r'[\\/:*?"<>|]')

# Caractères de contrôle (0x00-0x1F).
_CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f]')

# Noms de fichiers réservés par Windows (insensible à la casse, avec ou sans extension).
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def get_macros_dir():
    """Retourne %APPDATA%\\MosaicView\\macros\\, en le créant si nécessaire.
    Ne jamais reconstruire ce chemin à la main ailleurs dans le projet — toujours
    passer par cette fonction (même principe que get_config_manager().config_dir,
    voir skill config-storage)."""
    from modules.qt.config_manager import get_config_manager

    macros_dir = os.path.join(get_config_manager().config_dir, MACROS_SUBDIR)
    os.makedirs(macros_dir, exist_ok=True)
    return macros_dir


# ─────────────────────────────────────────────────────────────────────────────
# Validation du nom d'une macro
# ─────────────────────────────────────────────────────────────────────────────

def validate_macro_name(name, existing_names=None):
    """Retourne (True, None) si le nom est valide, (False, error_key) sinon —
    error_key est une clé de traduction (dialogs.macro_name_error.*), jamais
    un message déjà résolu. Rejet strict, jamais de filtrage/correction
    silencieuse : un nom invalide doit être corrigé par l'utilisateur.
    existing_names : comparaison insensible à la casse."""
    if not name or not name.strip():
        return False, "dialogs.macro_name_error.empty"

    name = name.strip()

    if len(name) > MAX_MACRO_NAME_LENGTH:
        return False, "dialogs.macro_name_error.too_long"

    if _INVALID_NAME_CHARS_RE.search(name) or _CONTROL_CHARS_RE.search(name):
        return False, "dialogs.macro_name_error.invalid_chars"

    # Un nom réservé reste interdit même suivi d'une extension (ex. "CON.json").
    stem = name.split(".", 1)[0].upper()
    if stem in _RESERVED_NAMES:
        return False, "dialogs.macro_name_error.reserved_name"

    # Windows n'autorise pas un nom se terminant par un point ou un espace.
    if name.endswith(".") or name.endswith(" "):
        return False, "dialogs.macro_name_error.invalid_chars"

    if existing_names is not None:
        lowered = name.lower()
        if any(lowered == existing.lower() for existing in existing_names):
            return False, "dialogs.macro_name_error.duplicate"

    return True, None


def _macro_file_path(name):
    """Chemin du fichier JSON pour une macro déjà validée (validate_macro_name)."""
    return os.path.join(get_macros_dir(), name + ".json")


# ─────────────────────────────────────────────────────────────────────────────
# Persistance — un fichier JSON par macro
# ─────────────────────────────────────────────────────────────────────────────

def list_macro_names():
    """Retourne l'ensemble des noms de macros déjà enregistrées (pour le contrôle
    d'unicité), en lisant uniquement les noms de fichiers — pas leur contenu."""
    macros_dir = get_macros_dir()
    names = set()
    for filename in os.listdir(macros_dir):
        if filename.lower().endswith(".json"):
            names.add(filename[:-len(".json")])
    return names


def list_macros():
    """Retourne (macros, errors) : macros valides triées par nom, et noms de
    fichiers illisibles/corrompus à afficher explicitement à l'utilisateur
    (jamais ignoré silencieusement)."""
    macros_dir = get_macros_dir()
    macros = []
    errors = []
    for filename in sorted(os.listdir(macros_dir)):
        if not filename.lower().endswith(".json"):
            continue
        path = os.path.join(macros_dir, filename)
        macro = load_macro(path)
        if macro is None:
            errors.append(filename)
        else:
            macros.append(macro)
    macros.sort(key=lambda m: m.get("name", "").lower())
    return macros, errors


def _is_valid_step(step) -> bool:
    """Structure minimale d'une étape, lue sans garde-fou par l'affichage
    (_MacroReadDialog._refresh_detail) et par apply_step_to_entry."""
    return (isinstance(step, dict)
            and isinstance(step.get("tool"), str)
            and isinstance(step.get("params"), dict)
            and isinstance(step.get("label_key"), str)
            and isinstance(step.get("label_args", {}), dict))


def load_macro(path):
    """Charge une macro depuis son fichier JSON. Retourne None si le fichier est
    illisible ou mal formé (JSON invalide, clés minimales absentes, ou étape
    mal structurée) — list_macros le signale alors comme fichier illisible."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
        return None
    if not isinstance(data.get("steps"), list):
        return None
    if not all(_is_valid_step(step) for step in data["steps"]):
        return None

    data.setdefault("description", "")
    return data


def save_macro(macro):
    """Écrit une macro dans son fichier JSON (macro["name"] doit déjà avoir été
    validé via validate_macro_name). macro : {"name": str, "description": str,
    "steps": [...]}. Écrase le fichier existant si la macro porte déjà ce nom
    (le renommage, qui retire aussi l'ancien fichier, passe par rename_macro)."""
    path = _macro_file_path(macro["name"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(macro, f, indent=2, ensure_ascii=False)
    return path


def rename_macro(old_name, macro):
    """Enregistre `macro` sous son nom (macro["name"], déjà validé via
    validate_macro_name en excluant old_name des doublons) puis retire le
    fichier de old_name. Windows ne distingue pas la casse des noms de
    fichiers : un renommage qui ne change que la casse désigne le même
    fichier, qui est alors renommé en place — le supprimer après écriture
    effacerait la macro elle-même."""
    old_path = _macro_file_path(old_name)
    new_path = _macro_file_path(macro["name"])
    same_file = (os.path.normcase(os.path.abspath(old_path))
                 == os.path.normcase(os.path.abspath(new_path)))
    if same_file:
        if old_path != new_path and os.path.exists(old_path):
            os.rename(old_path, new_path)
        return save_macro(macro)
    path = save_macro(macro)
    if os.path.exists(old_path):
        os.remove(old_path)
    return path


def delete_macro(name):
    """Supprime le fichier d'une macro. No-op silencieux si déjà absent."""
    path = _macro_file_path(name)
    if os.path.exists(path):
        os.remove(path)


# ─────────────────────────────────────────────────────────────────────────────
# Historique undo/redo — enregistrement et lecture
# ─────────────────────────────────────────────────────────────────────────────

def _viewer_state(viewer):
    state = viewer.callbacks.get('state')
    if state is None:
        from modules.qt import state as _state_module
        state = _state_module.state
    return state


def sync_recorded_steps(steps, snapshots, redo_stack, history, history_index):
    """Aligne la liste des étapes d'un enregistrement en cours sur l'état
    réel de l'historique après un undo/redo (déclenché dans la visionneuse
    ou depuis la mosaïque). snapshots[i] est le sommet de l'historique pris
    juste après le commit de steps[i] (None pour une étape reprise d'une
    macro existante par "Compléter", jamais annulable). redo_stack contient
    des paires (étape, snapshot), modifiées en place comme steps/snapshots.

    Une étape est annulée quand son snapshot se trouve après history_index,
    rétablie quand il redevient actif. Comparer des snapshots plutôt que de
    retirer une étape par Ctrl+Z reste juste quand une action pousse deux
    états (rotation/miroir), quand l'undo annule une action antérieure à
    l'enregistrement ou non enregistrée, et quand l'historique plein a
    retiré ses plus anciens états."""
    undone = history[history_index + 1:]
    active = history[:history_index + 1]
    while (steps and snapshots[-1] is not None
           and any(h is snapshots[-1] for h in undone)):
        redo_stack.append((steps.pop(), snapshots.pop()))
    while redo_stack and any(h is redo_stack[-1][1] for h in active):
        step, snapshot = redo_stack.pop()
        steps.append(step)
        snapshots.append(snapshot)


# ─────────────────────────────────────────────────────────────────────────────
# Contrôle des bornes au rejeu — clonage, flou, texte, formes, coller-image
# ─────────────────────────────────────────────────────────────────────────────

# Outils dont le dessin est rogné (ou, pour le texte, recalé au bord) par leur
# propre rendu quand il déborde de l'image : sans contrôle explicite ici, un
# élément enregistré dans la page de référence mais tombant hors d'une page
# cible plus petite serait perdu ou déplacé sans que la page soit signalée.
_BOUNDED_TOOLS = ("clone", "blur", "text", "shapes", "paste_image")


def _point_inside(x, y, w, h) -> bool:
    return 0 <= x < w and 0 <= y < h


def _points_lost(points, ref_w, ref_h, target_w, target_h) -> bool:
    """True si un point qui était dans la page de référence tombe hors de la
    page cible. Un point déjà hors de la page de référence à l'enregistrement
    (coup de pinceau qui déborde du bord) reste toléré : c'est le geste réel
    de l'utilisateur. Sans taille de référence (macro enregistrée avant son
    ajout aux paramètres), seul un ensemble de points entièrement hors de la
    page cible compte comme perdu."""
    if ref_w is None:
        return not any(_point_inside(x, y, target_w, target_h) for x, y in points)
    return any(
        _point_inside(x, y, ref_w, ref_h) and not _point_inside(x, y, target_w, target_h)
        for x, y in points
    )


def _box_lost(box, ref_w, ref_h, target_w, target_h) -> bool:
    """Même règle que _points_lost pour un cadre (x1, y1, x2, y2), coins dans
    un ordre quelconque : la partie du cadre comprise dans la page de
    référence doit tenir entièrement dans la page cible. Les deux pages
    partagent l'origine (0, 0), seuls les bords droit et bas peuvent donc
    faire perdre une partie du cadre. Sans taille de référence : perdu
    seulement si le cadre ne touche plus du tout la page cible."""
    x1, x2 = sorted((box[0], box[2]))
    y1, y2 = sorted((box[1], box[3]))
    if ref_w is None:
        return x2 <= 0 or y2 <= 0 or x1 >= target_w or y1 >= target_h
    cx1, cy1 = max(x1, 0), max(y1, 0)
    cx2, cy2 = min(x2, ref_w), min(y2, ref_h)
    if cx1 >= cx2 or cy1 >= cy2:
        return False
    return cx2 > target_w or cy2 > target_h


def step_fits_page(step: dict, target_w: int, target_h: int) -> bool:
    """False si un élément de l'étape (point peint, source de clonage, ancre
    de bloc texte, cadre de forme ou d'image collée) ne peut pas être
    reproduit sur une page cible de target_w × target_h pixels — l'étape
    échoue alors pour cette page, jamais de rognage/recalage silencieux.
    Les formes et images collées pivotées sont contrôlées sur leur cadre
    non pivoté. True pour tout outil hors _BOUNDED_TOOLS."""
    tool = step["tool"]
    p = step["params"]
    ref_w, ref_h = p.get("ref_w"), p.get("ref_h")
    if ref_w is None or ref_h is None:
        ref_w = ref_h = None

    if tool in ("clone", "blur"):
        points = [(pt[0], pt[1]) for pt in p.get("points_px") or []]
        if _points_lost(points, ref_w, ref_h, target_w, target_h):
            return False
        if tool == "clone" and points and p.get("source_px"):
            # Source effective de chaque point : décalage constant entre la
            # source et le premier point du stroke (même calcul que
            # CloneCanvasMixin._get_effective_clone_source).
            sx, sy = p["source_px"]
            fx, fy = points[0]
            sources = [(sx + x - fx, sy + y - fy) for x, y in points]
            if _points_lost(sources, ref_w, ref_h, target_w, target_h):
                return False
        return True

    if tool == "text":
        # Ancre du bloc, là où TextViewerMixin._text_render_all_blocks colle
        # le texte (recalée au bord de l'image si elle en sort) — la taille
        # du texte rendu n'est connue qu'au rendu Qt.
        for b in p.get("blocks") or []:
            anchor = (b["img_x"], b["img_y"] - (b.get("top_y_offset_img") or 0))
            if _points_lost([anchor], ref_w, ref_h, target_w, target_h):
                return False
        return True

    if tool in ("shapes", "paste_image"):
        items = p.get("shapes") if tool == "shapes" else p.get("images")
        for item in items or []:
            box = (item["ix1"], item["iy1"], item["ix2"], item["iy2"])
            if _box_lost(box, ref_w, ref_h, target_w, target_h):
                return False
        return True

    return True


def _current_page_size(viewer):
    """(largeur, hauteur) de la page affichée par viewer, lue depuis l'en-tête
    de entry['bytes'] sans décoder l'image. None si illisible."""
    import io
    from PIL import Image

    state = viewer.callbacks.get('state')
    if state is None:
        from modules.qt import state as _state_module
        state = _state_module.state
    data = state.images_data[viewer.current_idx].get('bytes')
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.size
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Lecture headless — rejoue les étapes sur une vraie ImageViewer bridée
# ─────────────────────────────────────────────────────────────────────────────

def apply_step_to_entry(viewer, step: dict) -> bool:
    """Rejoue une étape de macro sur la page actuellement affichée par
    `viewer` (déjà positionnée par l'appelant, current_idx correct). Chaque
    perform_* est appelé avec skip_history=True (le save_state global est
    géré une seule fois par run_macro_on_entries). Retourne True/False selon
    le succès réel du commit."""
    tool = step["tool"]
    p = step["params"]

    # Sur un GIF animé, seuls rotation et miroir savent traiter toutes les
    # frames : tout autre outil aplatirait l'animation en sa première image.
    if tool not in ("rotate", "flip"):
        from modules.qt import state as _state_module
        state = viewer.callbacks.get('state') or _state_module.state
        if 0 <= viewer.current_idx < len(state.images_data) \
                and state.images_data[viewer.current_idx].get("is_animated_gif"):
            return False

    if tool in _BOUNDED_TOOLS:
        size = _current_page_size(viewer)
        if size is None or not step_fits_page(step, size[0], size[1]):
            return False

    if tool == "crop":
        return viewer.perform_crop(skip_history=True, override_px=tuple(p["px"]))
    if tool == "straighten":
        return viewer.perform_straighten(skip_history=True, override_angle=p["angle"])
    if tool == "straighten_auto":
        # Page déjà droite : rien à redresser, l'étape réussit sans effet et
        # les étapes suivantes s'appliquent quand même.
        return viewer.perform_auto_straighten(skip_history=True, no_skew_ok=True)
    if tool == "clone":
        return viewer.perform_clone_step(p)
    if tool == "blur":
        return viewer.perform_blur_step(p)
    if tool == "text":
        return viewer.perform_text_step(p)
    if tool == "shapes":
        return viewer.perform_shapes_step(p)
    if tool == "transparency":
        return viewer.perform_transparency_step(p)
    if tool == "paste_image":
        return viewer.perform_paste_image_step(p)
    if tool == "rotate":
        return viewer.perform_rotate(p["angle"], skip_history=True)
    if tool == "flip":
        return viewer.perform_flip(p["direction"], skip_history=True)

    if tool == "levels":
        panel = viewer._toolbar._levels_panel
        panel.set_values_silent(p["threshold"], p["black_point"], p["gamma"], p["white_point"])
        return viewer.perform_levels(skip_history=True)
    if tool == "brightness":
        panel = viewer._toolbar._brightness_panel
        panel.set_values_silent(p["brightness"], p["contrast"])
        return viewer.perform_brightness(skip_history=True)
    if tool == "saturation":
        viewer._toolbar._saturation_panel.set_value_silent(p["saturation"])
        return viewer.perform_saturation(skip_history=True)
    if tool == "remove_colors":
        viewer._toolbar._remove_colors_panel.set_value_silent(p["intensity"])
        return viewer.perform_remove_colors(skip_history=True)
    if tool == "compression":
        viewer._toolbar._compression_panel.set_value_silent(p["quality"])
        return viewer.perform_compression(skip_history=True)
    if tool == "sharpness":
        viewer._toolbar._sharpness_panel.set_value_silent(p["value"])
        return viewer.perform_sharpness(skip_history=True)
    if tool == "unsharp":
        viewer._toolbar._unsharp_panel.set_values_silent(p["radius"], p["percent"], p["threshold"])
        return viewer.perform_unsharp(skip_history=True)

    if tool == "color_depth":
        from modules.qt.color_depth_tool_qt import _BLOCKED_DEPTH_KEYS_BY_EXT
        from modules.qt import state as _state_module
        state = viewer.callbacks.get('state') or _state_module.state
        ext = state.images_data[viewer.current_idx].get('extension', '').lower()
        if p["key"] in _BLOCKED_DEPTH_KEYS_BY_EXT.get(ext, set()):
            return False
        _remember_tool_start(viewer, "color_depth")
        return viewer.perform_color_depth(p["key"], skip_history=True)
    if tool == "effect":
        _remember_tool_start(viewer, "effect")
        return viewer.perform_effect(p["key"], skip_history=True)
    if tool == "image_mode":
        from modules.qt.image_mode_tool_qt import _BLOCKED_MODE_KEYS_BY_EXT
        from modules.qt import state as _state_module
        state = viewer.callbacks.get('state') or _state_module.state
        ext = state.images_data[viewer.current_idx].get('extension', '').lower()
        if p["key"] in _BLOCKED_MODE_KEYS_BY_EXT.get(ext, set()):
            return False
        _remember_tool_start(viewer, "image_mode")
        return viewer.perform_image_mode(p["key"], skip_history=True)
    if tool in ("restore_color_depth", "restore_effect", "restore_image_mode"):
        return _apply_restore_step(viewer, tool)

    return False


_RESTORE_STEP_FAMILY = {
    "restore_color_depth": "color_depth",
    "restore_effect": "effect",
    "restore_image_mode": "image_mode",
}


def _remember_tool_start(viewer, family: str):
    """Mémorise, pour la page courante, les bytes d'avant la première étape
    de `family` (color_depth/effect/image_mode) de la lecture en cours — ce
    que "Restaurer l'original" de cet outil restaurera sur cette page."""
    snapshots = getattr(viewer, '_macro_read_tool_start_bytes', None)
    if snapshots is None:
        snapshots = viewer._macro_read_tool_start_bytes = {}
    key = (viewer.current_idx, family)
    if key not in snapshots:
        snapshots[key] = _viewer_state(viewer).images_data[viewer.current_idx].get('bytes')


def _apply_restore_step(viewer, tool: str) -> bool:
    """"Restaurer l'original" en lecture : restaure la page courante à son
    état d'avant la première étape de CE même outil dans la lecture en cours
    (_remember_tool_start) — les étapes d'autres outils appliquées avant
    (recadrage, etc.) sont conservées, comme à l'enregistrement. Jamais le
    snapshot capturé à l'enregistrement, valable seulement sur la page
    d'origine. Aucune étape de cet outil sur la page : rien à restaurer,
    l'étape réussit sans effet. Le snapshot est consommé : une étape
    suivante du même outil repart de l'état restauré."""
    state = _viewer_state(viewer)
    entry = state.images_data[viewer.current_idx]
    snapshots = getattr(viewer, '_macro_read_tool_start_bytes', {})
    original_bytes = snapshots.pop((viewer.current_idx, _RESTORE_STEP_FAMILY[tool]), None)
    if original_bytes is None:
        return True

    entry['bytes'] = original_bytes
    entry['img'] = None
    entry['qt_pixmap_large'] = None
    entry['qt_qimage_large'] = None
    state.modified = True

    real_idx = entry.get("_real_idx")
    canvas = viewer.callbacks.get("canvas")
    if canvas is not None and real_idx is not None:
        from modules.qt.mosaic_canvas import build_qimage_for_entry
        build_qimage_for_entry(entry)
        canvas.refresh_thumbnail(real_idx)
        canvas.refresh_duplicate_overlay()
    viewer.display_image(keep_crop_rect=True)
    return True


def run_macro_on_entries(macro: dict, entries: list, viewer, save_state_fn) -> dict:
    """Lit une macro sur une liste d'entrées (une visionneuse pour une page,
    plusieurs pour un lot mosaïque) — un seul save_state global pour toute
    la lecture. Retourne un rapport {"ok": [...], "partial": [...],
    "failed": [...], "interrupted": bool, "stopped": None|"closed"|"modified"},
    partial = liste de (page, étapes appliquées, étape fautive) pour les pages
    où la macro s'est arrêtée avant la fin. interrupted=True : l'utilisateur
    a fermé la visionneuse en cours de route, closeEvent a déjà fait un
    rollback complet (rollback_macro_reading) — le reste du rapport est alors
    vide et ne doit pas être interprété comme un résultat partiel valide,
    tout a été annulé. stopped : lecture arrêtée parce que le document a été
    fermé/remplacé ("closed", rien n'est plus écrit dans le panneau) ou
    modifié depuis la mosaïque ("modified", les pages déjà traitées gardent
    leurs modifications) — voir reading_context_changed.

    viewer doit déjà être positionnable sur chaque page (viewer.current_idx
    modifiable + viewer.display_image()). save_state_fn : callable, appelé
    une fois avant et une fois (force=True) après toute la lecture — sauté
    si interrompue."""
    from PySide6.QtWidgets import QApplication

    steps = macro["steps"]
    report = {"ok": [], "partial": [], "failed": [], "interrupted": False, "stopped": None}

    if viewer.page_mode != "single":
        viewer.page_mode = "single"
        viewer.display_image()

    viewer._macro_reading = True
    viewer._macro_set_locked_for_reading(True)
    viewer._toolbar.refresh_macro_buttons_state()

    # Retour visuel immédiat avant save_state_fn() (peut être lent sur un
    # gros fichier) — sans ça, rien ne change à l'écran entre le clic sur
    # "Lire" et la première page traitée, l'utilisateur croit son clic ignoré.
    from modules.qt.localization import _
    from modules.qt.canvas_overlay_qt import show_canvas_text, hide_canvas_text
    prep_item_holder = [None]
    show_canvas_text(viewer._canvas, _("labels.macro_preparing"), prep_item_holder)
    QApplication.processEvents()

    # save_state() "avant" ne pousse rien quand le sommet de l'historique
    # décrit déjà l'état courant (cas normal : chaque action sauve après
    # elle-même) — rollback_macro_reading ne doit alors rien dépiler, sous
    # peine de retirer le dernier état légitime de l'utilisateur.
    state = _viewer_state(viewer)
    top_before = history_top(state)
    save_state_fn()
    viewer._macro_read_pushed_before = history_top(state) is not top_before
    hide_canvas_text(viewer._canvas, prep_item_holder)

    # Protections du document (skill file-close) : la boucle rend la main à
    # Qt entre deux étapes (processEvents), la mosaïque reste utilisable
    # pendant la lecture. Fermeture du fichier → cancel_operations_on_close
    # arrête la lecture ; numéro de document et sommet de l'historique
    # mémorisés ici détectent un fichier remplacé ou une modification faite
    # depuis la mosaïque (voir reading_context_changed).
    viewer._macro_read_doc_generation = getattr(state, 'doc_generation', 0)
    viewer._macro_read_expected_top = history_top(state)
    viewer._macro_read_stop_reason = None
    viewer._macro_read_page_start = {}
    viewer._macro_read_tool_start_bytes = {}

    from modules.qt.utils import register_cancel_on_close, unregister_cancel_on_close
    canvas = viewer.callbacks.get("canvas")

    def _on_file_close():
        viewer._macro_read_stop_reason = "closed"

    if canvas is not None:
        register_cancel_on_close(canvas, _on_file_close)
    try:
        stopped = _read_pages(steps, entries, viewer, state, report)
    finally:
        if canvas is not None:
            unregister_cancel_on_close(canvas, _on_file_close)

    if report["interrupted"]:
        return report
    report["stopped"] = stopped
    # Arrêt sur document modifié : l'action de la mosaïque qui l'a détecté a
    # déjà sauvé un état incluant les pages traitées jusque-là ; un état
    # forcé ici écraserait une éventuelle branche de redo. Document fermé :
    # son historique n'existe plus.
    if stopped is None:
        save_state_fn(force=True)
    viewer._macro_reading = False
    viewer._macro_set_locked_for_reading(False)
    viewer._toolbar.refresh_macro_buttons_state()
    return report


def _index_of(state, entry):
    """Index actuel de `entry` dans state.images_data, par identité — une page
    a pu être déplacée ou supprimée depuis la constitution de la liste."""
    for i, e in enumerate(state.images_data):
        if e is entry:
            return i
    return None


def reading_context_changed(viewer, state):
    """État du document d'une lecture : None s'il est intact, "closed" s'il
    a été fermé ou remplacé (cancel_operations_on_close a été appelé, ou
    doc_generation a changé — ArchiveLoader/PdfLoader le changent aussi),
    "modified" si l'historique a bougé alors que la lecture n'y écrit rien
    entre son save_state "avant" et sa fin : toute action de la mosaïque qui
    modifie le document (suppression, réordonnancement, undo/redo...)
    sauve ou déplace un état. Attributs absents (pas de lecture démarrée) :
    considéré intact."""
    if getattr(viewer, '_macro_read_stop_reason', None) == "closed":
        return "closed"
    generation = getattr(viewer, '_macro_read_doc_generation', None)
    if generation is not None and getattr(state, 'doc_generation', 0) != generation:
        return "closed"
    if hasattr(viewer, '_macro_read_expected_top') and \
            history_top(state) is not viewer._macro_read_expected_top:
        return "modified"
    return None


def _read_pages(steps, entries, viewer, state, report):
    """Boucle page par page de run_macro_on_entries. Retourne None (toutes
    les pages lues), "closed" ou "modified" (lecture arrêtée, voir
    reading_context_changed). Visionneuse fermée en cours de route :
    report["interrupted"] posé (rollback déjà fait par closeEvent), retour
    None."""
    from PySide6.QtWidgets import QApplication

    def _interrupted():
        report.update(ok=[], partial=[], failed=[], interrupted=True)

    for entry in entries:
        if not viewer._macro_reading:
            _interrupted()
            return None
        stopped = reading_context_changed(viewer, state)
        if stopped:
            return stopped
        real_idx = _index_of(state, entry)
        if real_idx is None:
            continue
        # Index lu par les perform_* pour rafraîchir la bonne vignette.
        entry["_real_idx"] = real_idx
        viewer.current_idx = real_idx
        viewer._macro_read_page_start[id(entry)] = (entry, entry.get('bytes'))
        viewer.display_image()
        QApplication.processEvents()

        applied = 0
        failed_step = None
        for step in steps:
            if not viewer._macro_reading:
                _interrupted()
                return None
            stopped = reading_context_changed(viewer, state)
            if stopped is None and _index_of(state, entry) != viewer.current_idx:
                stopped = "modified"
            if stopped:
                failed_step = step
                break
            # Une exception (étape d'un fichier JSON incohérent, erreur
            # imprévue d'un outil) compte comme l'échec de cette étape :
            # sans ce garde, elle sortirait de la boucle en laissant la
            # visionneuse verrouillée et la lecture jamais terminée.
            try:
                ok = apply_step_to_entry(viewer, step)
            except Exception:
                traceback.print_exc()
                ok = False
            if not ok:
                failed_step = step
                break
            applied += 1
            QApplication.processEvents()

        if stopped == "closed":
            return stopped

        if failed_step is not None:
            # Une étape à validation (texte, formes, image collée,
            # transparence) qui échoue laisse son travail en attente sur la
            # page — il bloquerait l'undo/redo et la fermeture de la
            # visionneuse. Aucun travail de l'utilisateur ne peut s'y
            # trouver : la lecture refuse de démarrer tant qu'il en reste.
            viewer._macro_discard_pending_work()

        page_name = entry.get("orig_name", "?")
        if stopped:
            if applied:
                report["partial"].append((page_name, applied, failed_step))
            return stopped
        if applied == len(steps):
            report["ok"].append(entry)
        elif applied == 0:
            report["failed"].append((page_name, failed_step))
        else:
            report["partial"].append((page_name, applied, failed_step))
    return None


def rollback_macro_reading(viewer) -> bool:
    """Annule tout ce qu'une lecture en cours a déjà appliqué : restaure
    chaque page touchée (retrouvée par identité, elle a pu changer de place)
    à son état d'avant la première étape de CETTE lecture
    (viewer._macro_read_page_start), sans jamais committer dans
    state.history — la lecture n'aura jamais eu lieu du point de vue de
    l'undo/redo (voir run_macro_on_entries, qui n'a fait qu'un save_state()
    "avant", jamais le save_state(force=True) "après"). Cet état "avant"
    n'est dépilé que s'il a réellement été poussé
    (viewer._macro_read_pushed_before).

    Document fermé, remplacé ou modifié depuis la mosaïque pendant la
    lecture (reading_context_changed) : rien n'est restauré ni dépilé, ses
    pages et son historique ne sont plus ceux de la lecture. Retourne True
    si le rollback a eu lieu."""
    state = _viewer_state(viewer)
    if reading_context_changed(viewer, state):
        return False
    canvas = viewer.callbacks.get("canvas")
    for entry, original_bytes in getattr(viewer, '_macro_read_page_start', {}).values():
        entry['bytes'] = original_bytes
        entry['img'] = None
        entry['qt_pixmap_large'] = None
        entry['qt_qimage_large'] = None
        entry['_thumbnail'] = None
        real_idx = _index_of(state, entry)
        if canvas is not None and real_idx is not None:
            canvas.refresh_thumbnail(real_idx)

    if getattr(viewer, '_macro_read_pushed_before', False):
        from modules.qt.undo_redo import pop_last_state
        pop_last_state(state)
        viewer._macro_read_pushed_before = False

    render_mosaic = viewer.callbacks.get("render_mosaic")
    if render_mosaic:
        render_mosaic()
    return True
