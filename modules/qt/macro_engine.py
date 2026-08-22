"""
Moteur de stockage/validation des macros de la visionneuse principale.

Une macro est une séquence d'étapes (une par action enregistrée dans la visionneuse),
stockée en pixels absolus fixes (jamais en coordonnées relatives à la page). Chaque macro
vit dans son propre fichier JSON, nommé d'après le nom de la macro, dans
%APPDATA%\\MosaicView\\macros\\.

Module pur (aucune dépendance Qt) : lecture/écriture disque et validation de nom
uniquement. La logique d'enregistrement live et de lecture (dispatch par outil,
run_macro_on_entries) est ajoutée dans une étape ultérieure du chantier.
"""

import json
import os
import re


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


def load_macro(path):
    """Charge une macro depuis son fichier JSON. Retourne None si le fichier est
    illisible ou mal formé (JSON invalide, ou absence des clés minimales attendues)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    if not isinstance(data, dict) or "name" not in data or "steps" not in data:
        return None
    if not isinstance(data["steps"], list):
        return None

    data.setdefault("description", "")
    return data


def save_macro(macro):
    """Écrit une macro dans son fichier JSON (macro["name"] doit déjà avoir été
    validé via validate_macro_name). macro : {"name": str, "description": str,
    "steps": [...]}. Écrase le fichier existant si la macro porte déjà ce nom
    (cas du renommage géré séparément par rename_macro, pas ici)."""
    path = _macro_file_path(macro["name"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(macro, f, indent=2, ensure_ascii=False)
    return path


def rename_macro(old_name, new_name):
    """Renomme une macro : renomme son fichier ET met à jour le champ 'name' interne.
    L'appelant doit avoir déjà validé new_name via validate_macro_name (en excluant
    old_name de existing_names, pour ne pas se rejeter lui-même comme doublon)."""
    old_path = _macro_file_path(old_name)
    macro = load_macro(old_path)
    if macro is None:
        return False

    macro["name"] = new_name
    new_path = _macro_file_path(new_name)
    with open(new_path, "w", encoding="utf-8") as f:
        json.dump(macro, f, indent=2, ensure_ascii=False)

    if os.path.exists(old_path) and old_path != new_path:
        os.remove(old_path)
    return True


def delete_macro(name):
    """Supprime le fichier d'une macro. No-op silencieux si déjà absent."""
    path = _macro_file_path(name)
    if os.path.exists(path):
        os.remove(path)


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

    if tool == "crop":
        return viewer.perform_crop(skip_history=True, override_px=tuple(p["px"]))
    if tool == "straighten":
        return viewer.perform_straighten(skip_history=True, override_angle=p["angle"])
    if tool == "straighten_auto":
        return viewer.perform_auto_straighten(skip_history=True)
    if tool == "clone":
        return viewer.perform_clone_step(p)
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
        return viewer.perform_color_depth(p["key"], skip_history=True)
    if tool == "effect":
        return viewer.perform_effect(p["key"], skip_history=True)
    if tool == "image_mode":
        from modules.qt.image_mode_tool_qt import _BLOCKED_MODE_KEYS_BY_EXT
        from modules.qt import state as _state_module
        state = viewer.callbacks.get('state') or _state_module.state
        ext = state.images_data[viewer.current_idx].get('extension', '').lower()
        if p["key"] in _BLOCKED_MODE_KEYS_BY_EXT.get(ext, set()):
            return False
        return viewer.perform_image_mode(p["key"], skip_history=True)
    if tool in ("restore_color_depth", "restore_effect", "restore_image_mode"):
        return _apply_restore_step(viewer, tool)

    return False


def _apply_restore_step(viewer, tool: str) -> bool:
    """"Restaurer l'original" en lecture : restaure la page courante à son
    état d'avant la PREMIÈRE étape de CETTE lecture qui l'a modifiée — jamais
    le snapshot capturé à l'enregistrement (valable seulement sur la page
    d'origine). run_macro_on_entries pose viewer._macro_read_page_start_bytes
    avant la première étape de chaque page traitée."""
    from modules.qt import state as _state_module

    state = viewer.callbacks.get('state') or _state_module.state
    entry = state.images_data[viewer.current_idx]
    original_bytes = viewer._macro_read_page_start_bytes.get(viewer.current_idx)
    if original_bytes is None:
        return False

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
    "failed": [...], "interrupted": bool}, partial = liste de
    (entry, step_index) pour les pages où la macro s'est arrêtée avant la
    fin. interrupted=True : l'utilisateur a fermé la fenêtre en cours de
    route, closeEvent a déjà fait un rollback complet (rollback_macro_reading)
    — le reste du rapport est alors vide et ne doit pas être interprété
    comme un résultat partiel valide, tout a été annulé.

    viewer doit déjà être positionnable sur chaque page (viewer.current_idx
    modifiable + viewer.display_image()). save_state_fn : callable, appelé
    une fois avant et une fois (force=True) après toute la lecture — sauté
    si interrompue."""
    from PySide6.QtWidgets import QApplication

    steps = macro["steps"]
    report = {"ok": [], "partial": [], "failed": [], "interrupted": False}

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

    save_state_fn()
    hide_canvas_text(viewer._canvas, prep_item_holder)

    viewer._macro_read_page_start_bytes = {}

    for entry in entries:
        if not viewer._macro_reading:
            report["ok"] = []
            report["partial"] = []
            report["failed"] = []
            report["interrupted"] = True
            return report

        real_idx = entry.get("_real_idx")
        if real_idx is None:
            report["failed"].append(entry)
            continue
        viewer.current_idx = real_idx
        viewer._macro_read_page_start_bytes[real_idx] = entry.get('bytes')
        viewer.display_image()
        QApplication.processEvents()

        applied = 0
        failed_step = None
        for step in steps:
            if not viewer._macro_reading:
                report["ok"] = []
                report["partial"] = []
                report["failed"] = []
                report["interrupted"] = True
                return report
            if not apply_step_to_entry(viewer, step):
                failed_step = step
                break
            applied += 1
            QApplication.processEvents()

        page_name = entry.get("orig_name", "?")
        if applied == len(steps):
            report["ok"].append(entry)
        elif applied == 0:
            report["failed"].append((page_name, failed_step))
        else:
            report["partial"].append((page_name, applied, failed_step))

    save_state_fn(force=True)
    viewer._macro_reading = False
    viewer._macro_set_locked_for_reading(False)
    viewer._toolbar.refresh_macro_buttons_state()
    return report


def rollback_macro_reading(viewer):
    """Annule tout ce qu'une lecture en cours a déjà appliqué : restaure
    chaque page touchée à son état d'avant la première étape de CETTE
    lecture (viewer._macro_read_page_start_bytes), sans jamais committer
    dans state.history — la lecture n'aura jamais eu lieu du point de vue
    de l'undo/redo (voir run_macro_on_entries, qui n'a fait qu'un
    save_state() "avant", jamais le save_state(force=True) "après")."""
    from modules.qt import state as _state_module

    state = viewer.callbacks.get('state') or _state_module.state
    canvas = viewer.callbacks.get("canvas")
    for real_idx, original_bytes in getattr(viewer, '_macro_read_page_start_bytes', {}).items():
        if 0 <= real_idx < len(state.images_data):
            entry = state.images_data[real_idx]
            entry['bytes'] = original_bytes
            entry['img'] = None
            entry['qt_pixmap_large'] = None
            entry['qt_qimage_large'] = None
            entry['_thumbnail'] = None
            if canvas is not None:
                canvas.refresh_thumbnail(real_idx)

    from modules.qt.undo_redo import pop_last_state
    pop_last_state(state)

    render_mosaic = viewer.callbacks.get("render_mosaic")
    if render_mosaic:
        render_mosaic()
