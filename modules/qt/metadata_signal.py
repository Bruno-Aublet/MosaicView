"""
modules/qt/metadata_signal.py
Signal Qt global émis quand les métadonnées comic (state.comic_metadata) ont changé.

Usage :
    from modules.qt.metadata_signal import metadata_signal
    metadata_signal.changed.connect(my_slot)
    metadata_signal.emit()
"""

from PySide6.QtCore import QObject, Signal


class _MetadataSignal(QObject):
    changed = Signal()

    def emit(self):
        self.changed.emit()


metadata_signal = _MetadataSignal()
