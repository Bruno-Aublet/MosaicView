"""
modules/qt/language_signal.py
Signal Qt global émis à chaque changement de langue.

Usage :
    from modules.qt.language_signal import language_signal
    language_signal.changed.connect(my_slot)   # dans une fenêtre
    language_signal.changed.disconnect(my_slot) # à la fermeture
    disconnect_on_destroy(window, my_slot)     # et à sa destruction
    language_signal.emit("fr")                 # dans _on_language_change
"""

import warnings

from PySide6.QtCore import QObject, Signal


class _LanguageSignal(QObject):
    changed = Signal(str)  # lang_code

    def emit(self, lang_code: str):
        self.changed.emit(lang_code)


language_signal = _LanguageSignal()


def disconnect_on_destroy(widget, handler):
    """Débranche `handler` de language_signal.changed à la destruction de
    `widget`, en plus de sa déconnexion à la fermeture. Une fenêtre détruite
    avec sa fenêtre parente ne passe ni par finished ni par sa méthode de
    fermeture : sa traduction resterait branchée sur un objet détruit
    (RuntimeError au journal des erreurs à chaque changement de langue, objet
    Python retenu par le signal global)."""
    widget.destroyed.connect(lambda *_args: _disconnect_quietly(handler))


def _disconnect_quietly(handler):
    # Déjà déconnecté par la fermeture : PySide6 avertit en console au lieu
    # de lever une exception
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        try:
            language_signal.changed.disconnect(handler)
        except (RuntimeError, TypeError):
            pass  # sans conséquence : déjà déconnecté
