"""
modules/qt/metadata_signal.py
Signal Qt global émis quand les métadonnées comic (state.comic_metadata) ont changé.

Le signal transporte l'AppState concerné : en split-view, chaque onglet Infos
n'écoute que les émissions portant sur le state de son propre panneau (sinon
une modification dans un panneau reconstruirait aussi l'onglet de l'autre).
state=None = émission sans panneau identifiable, reçue par tous les onglets.

Usage :
    from modules.qt.metadata_signal import metadata_signal, metadata_pages_signal
    metadata_signal.changed.connect(my_slot)       # my_slot(state)
    metadata_signal.emit(state)

    metadata_pages_signal.changed.connect(my_slot)
    metadata_pages_signal.emit(state)   # mise à jour légère des valeurs Pages uniquement
"""

from PySide6.QtCore import QObject, Signal


class _MetadataSignal(QObject):
    changed = Signal(object)

    def emit(self, state=None):
        self.changed.emit(state)


metadata_signal       = _MetadataSignal()
metadata_pages_signal = _MetadataSignal()
