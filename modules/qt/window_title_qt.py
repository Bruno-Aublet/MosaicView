"""
window_title_qt.py — Gestion du titre de la fenêtre principale (version Qt).
"""
import os

from modules.qt.localization import _, _wt


def _title_for_state(state) -> str:
    """Retourne la partie fichier du titre pour un état donné."""
    if state is not None and getattr(state, "current_file", None):
        return os.path.basename(state.current_file)
    baseline = _wt("app_baseline")
    return baseline if (baseline and baseline != "app_baseline") else ""


def update_window_title(window, state=None):
    """
    Met à jour le titre de la fenêtre principale Qt.

    - Mode split actif : "MosaicView - file1.cbz  |  file2.cbz"
    - Archive ouverte  : "MosaicView - <nom du fichier>"
    - Aucune archive   : "MosaicView - <baseline>"  (ou juste "MosaicView")
    """
    try:
        import MosaicView as _main
        v = getattr(_main, "__version__", None)
        app_title = f"MosaicView {v}" if v else _wt("app_title")
    except Exception:
        app_title = _wt("app_title")

    # Mode split : construit un titre combiné
    split_active = getattr(window, "_split_active", False)
    panel2 = getattr(window, "_panel2", None)
    if split_active and panel2 is not None:
        t1 = _title_for_state(getattr(window, "_panel", None) and window._panel._state)
        t2 = _title_for_state(panel2._state)
        parts = [p for p in (t1, t2) if p]
        if parts:
            window.setWindowTitle(f"{app_title} - {'  |  '.join(parts)}")
        else:
            window.setWindowTitle(app_title)
        return

    # Mode non splitté
    part = _title_for_state(state)
    if part:
        window.setWindowTitle(f"{app_title} - {part}")
    else:
        window.setWindowTitle(app_title)
