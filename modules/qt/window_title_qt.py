"""
window_title_qt.py — Gestion du titre de la fenêtre principale (version Qt).
"""
import os

from modules.qt.localization import _, _wt


def _title_for_state(state, tr=_wt) -> str:
    """Retourne la partie fichier du titre pour un état donné."""
    if state is not None and getattr(state, "current_file", None):
        return os.path.basename(state.current_file)
    baseline = tr("app_baseline")
    return baseline if (baseline and baseline != "app_baseline") else ""


def _app_title(tr) -> str:
    try:
        import MosaicView as _main
        v = getattr(_main, "__version__", None)
        return f"MosaicView {v}" if v else tr("app_title")
    except Exception:
        return tr("app_title")


def _window_title(window, state, tr) -> str:
    """
    Titre de la fenêtre principale pour la barre des tâches et Alt+Tab,
    textes traduits par `tr`.

    - Mode split actif : "MosaicView - file1.cbz  |||  file2.cbz"
    - Archive ouverte  : "MosaicView - <nom du fichier>"
    - Aucune archive   : "MosaicView - <baseline>"  (ou juste "MosaicView")
    """
    app_title = _app_title(tr)

    # Mode split : construit un titre combiné
    split_active = getattr(window, "_split_active", False)
    panel2 = getattr(window, "_panel2", None)
    if split_active and panel2 is not None:
        state1 = getattr(window, "_panel", None) and window._panel._state
        state2 = panel2._state
        file1 = os.path.basename(state1.current_file) if (state1 and getattr(state1, "current_file", None)) else None
        file2 = os.path.basename(state2.current_file) if (state2 and getattr(state2, "current_file", None)) else None
        baseline = tr("app_baseline") or ""
        if file1 and file2:
            return f"{app_title} - {file1}  |||  {file2}"
        if file1:
            return f"{app_title} - {file1}  |||  {baseline}" if baseline else f"{app_title} - {file1}"
        if file2:
            return f"{app_title} - {baseline}  |||  {file2}" if baseline else f"{app_title} - {file2}"
        return f"{app_title} - {baseline}" if baseline else app_title

    # Mode non splitté
    part = _title_for_state(state, tr)
    return f"{app_title} - {part}" if part else app_title


def _comic_name(state):
    """Nom affiché au-dessus d'un panneau qui montre un comic : son fichier,
    extension comprise, ou « BD sans titre » pour des images sans fichier
    (même texte que l'onglet, TabBar.update) ; None pour un panneau vide."""
    if state is None:
        return None
    if getattr(state, "current_file", None):
        return os.path.basename(state.current_file)
    if getattr(state, "images_data", None):
        return _("tabs.untitled_comic")
    return None


def _title_bar_texts(window):
    """Titres de la barre dessinée par Qt (`_`, police de l'application) :
    le texte de l'appli, et le nom du comic de chaque panneau affiché, de
    gauche à droite (None pour un panneau vide)."""
    baseline = _("app_baseline")
    app_title = _app_title(_)
    app_text = f"{app_title} - {baseline}" if baseline and baseline != "app_baseline" else app_title
    panel1 = getattr(window, "_panel", None)
    panels = [panel1]
    panel2 = getattr(window, "_panel2", None)
    if getattr(window, "_split_active", False) and panel2 is not None:
        panels.append(panel2)
    return app_text, [_comic_name(getattr(p, "_state", None)) for p in panels]


def update_window_title(window, state=None):
    """
    Met à jour le titre de la fenêtre principale : celui que Windows affiche
    dans la barre des tâches et Alt+Tab (`_wt`, police du système), et ceux
    de la barre de titre dessinée par Qt, un par panneau (title_bar_qt).
    """
    window.setWindowTitle(_window_title(window, state, _wt))
    title_bar = getattr(window, "_title_bar", None)
    if title_bar is not None:
        app_text, names = _title_bar_texts(window)
        title_bar.set_titles(app_text, names)
