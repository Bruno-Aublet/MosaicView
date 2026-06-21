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
        state1 = getattr(window, "_panel", None) and window._panel._state
        state2 = panel2._state
        file1 = os.path.basename(state1.current_file) if (state1 and getattr(state1, "current_file", None)) else None
        file2 = os.path.basename(state2.current_file) if (state2 and getattr(state2, "current_file", None)) else None
        baseline = _wt("app_baseline") or ""
        if file1 and file2:
            window.setWindowTitle(f"{app_title} - {file1}  |||  {file2}")
        elif file1:
            window.setWindowTitle(f"{app_title} - {file1}  |||  {baseline}" if baseline else f"{app_title} - {file1}")
        elif file2:
            window.setWindowTitle(f"{app_title} - {baseline}  |||  {file2}" if baseline else f"{app_title} - {file2}")
        else:
            window.setWindowTitle(f"{app_title} - {baseline}" if baseline else app_title)
        return

    # Mode non splitté
    part = _title_for_state(state)
    if part:
        window.setWindowTitle(f"{app_title} - {part}")
    else:
        window.setWindowTitle(app_title)
