"""
modules/qt/status_bar_qt.py
Barre de statut Qt pour MosaicView.

Placée dans le panneau central uniquement (pas sous la colonne gauche),
contrairement à QMainWindow.setStatusBar() qui s'étend sur toute la largeur.
"""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PySide6.QtCore import Qt

from modules.qt.localization import _
from modules.qt.utils import format_file_size
from modules.qt.font_manager_qt import get_current_font


class StatusBar(QWidget):
    """Barre de statut à placer en bas du panneau central."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(0)

        self._label = QLabel("")
        self._label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self._label, 1)

    def refresh(self, state):
        """Met à jour le texte selon l'état courant (reproduit canvas_rendering.update_status_bar)."""
        self._label.setFont(get_current_font(9))

        if state is None:
            self._label.setText("")
            return

        dirs_count  = len({e["orig_name"].split("/")[0] for e in state.images_data if "/" in e.get("orig_name", "") and not e.get("is_dir")})
        files_count = sum(1 for e in state.images_data if not e.get("is_dir"))
        selected_count = len(state.selected_indices)

        total_size = sum(
            len(e.get("bytes", b"")) for e in state.images_data if e.get("bytes")
        )
        selected_size = sum(
            len(state.images_data[i].get("bytes", b""))
            for i in state.selected_indices
            if i < len(state.images_data) and state.images_data[i].get("bytes")
        )

        text = _("labels.status_bar",
                 dirs=dirs_count,
                 files=files_count,
                 selected=selected_count,
                 total_size=format_file_size(total_size),
                 selected_size=format_file_size(selected_size))
        self._label.setText(text)
