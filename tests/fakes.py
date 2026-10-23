"""Faux objets communs aux tests, sans Qt.

- Signal : remplace un signal Qt (`connect`, `disconnect`, `emit`). Les slots
  sont appelés à l'émission, dans l'ordre de leur branchement, sur le thread
  qui émet (comme une connexion directe) ; chaque émission est notée dans
  `emitted`, pour les tests qui vérifient ce qu'un worker a envoyé.
- tr : remplace localization._ / _wt : la clé suivie de ses paramètres triés,
  pour retrouver dans un texte la clé et les valeurs qui y sont insérées,
  quelle que soit la langue.

Fichier sans test (ne commence pas par test_) : importé par les tests
(`from fakes import Signal, tr`).
"""


class Signal:
    def __init__(self):
        self._slots = []
        self.emitted = []

    def connect(self, slot):
        self._slots.append(slot)

    def disconnect(self, slot=None):
        if slot is None:
            self._slots = []
        elif slot in self._slots:
            self._slots.remove(slot)

    def emit(self, *args):
        self.emitted.append(args)
        for slot in list(self._slots):
            slot(*args)


def tr(key, **kw):
    return key + (" " + repr(sorted(kw.items())) if kw else "")
