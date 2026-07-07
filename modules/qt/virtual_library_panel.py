"""
Panneau virtuel de la bibliothèque : état AppState interne, jamais affiché,
jamais ajouté à aucun splitter. Permet à library_window.py de réutiliser un
vrai AppState (images_data, comic_metadata...) au lieu d'un state factice
(type('_FakeState', ...)), sans construire de canvas/menubar/toolbar et sans
jamais interférer avec panel1/panel2.

Usage :
    panel = VirtualLibraryPanel()
    if panel.open_from_file(abs_path):
        with panel.activate():
            ...  # code qui lit/écrit modules.qt.state.state
"""

import os
import zipfile
from contextlib import contextmanager

from modules.qt.state import AppState
from modules.qt import state as _state_module
from modules.qt.archive_loader import IMAGE_EXTS
from modules.qt.comic_info import read_comic_info


class VirtualLibraryPanel:
    """État interne pour la bibliothèque : pas de QWidget, pas de canvas."""

    def __init__(self):
        self._state = AppState()

    @property
    def state(self):
        return self._state

    def open_from_file(self, abs_path: str) -> bool:
        """Peuple le state depuis une archive sur disque (images_data +
        comic_metadata). Retourne False si le fichier est illisible."""
        ext = os.path.splitext(abs_path)[1].lower()
        if ext != '.cbz':
            return False

        st = self._state
        st.images_data = []
        st.selected_indices = set()
        st.current_file = abs_path
        st.modified = False

        try:
            with zipfile.ZipFile(abs_path, 'r') as zf:
                for name in zf.namelist():
                    if name.endswith('/'):
                        continue
                    ext_entry = os.path.splitext(name)[1].lower()
                    st.images_data.append({
                        'orig_name': name,
                        'is_image': ext_entry in IMAGE_EXTS,
                        'is_dir': False,
                    })
        except Exception:
            return False

        st.comic_metadata = read_comic_info(abs_path)
        return True

    @contextmanager
    def activate(self):
        """Redirige le singleton modules.qt.state.state vers ce panneau
        virtuel le temps du bloc, puis restaure l'état précédent — même
        pattern que PanelWidget._build_menubar_callbacks."""
        prev = _state_module.state
        _state_module.state = self._state
        try:
            yield self._state
        finally:
            _state_module.state = prev
