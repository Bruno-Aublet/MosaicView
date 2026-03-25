"""
modules/qt/language_signal.py
Signal Qt global émis à chaque changement de langue.

Usage :
    from modules.qt.language_signal import language_signal
    language_signal.changed.connect(my_slot)   # dans une fenêtre
    language_signal.changed.disconnect(my_slot) # à la fermeture
    language_signal.emit("fr")                 # dans _on_language_change
"""

from PySide6.QtCore import QObject, Signal


class _LanguageSignal(QObject):
    changed = Signal(str)  # lang_code

    def emit(self, lang_code: str):
        self.changed.emit(lang_code)


language_signal = _LanguageSignal()
