"""
modules/qt/batch_claims.py — Fichiers réservés par les traitements par lot en cours.

Plusieurs lots peuvent tourner en même temps (conversions CBR/CB7/CBT/PDF/IMG,
recompression, import des métadonnées en lot), chacun dans son propre thread.
Sans coordination, deux lots lancés sur le même dossier traitent le même
fichier : le second relit une source que le premier vient de supprimer, ou
écrit sous le nom de CBZ que le premier est en train de créer (un nom choisi
parce qu'il « n'existe pas encore » sur le disque).

Un lot démarre par BatchClaims.start(fichiers), qui refuse (None) si l'un de
ses fichiers appartient à un lot encore en cours : le second lot n'est pas
lancé du tout. Ensuite, le lot réserve le fichier qu'il traite (claim) et le
nom du fichier qu'il va créer (reserve), puis les libère (release_all) ; un
fichier réservé par un autre lot (lots de types différents qui se recoupent
après leur démarrage) est laissé de côté. finish() termine le lot. Sans Qt :
utilisable depuis les threads des lots et testable seul.
"""

import os
import threading

_lock = threading.Lock()
_owners = {}    # chemin normalisé → BatchClaims qui le réserve
_running = []   # BatchClaims des lots démarrés et pas encore terminés


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


class BatchClaims:
    """Réservations d'un traitement par lot (thread-safe)."""

    def __init__(self, files=()):
        self._keys = set()
        self._files = {_norm(f) for f in files}

    @classmethod
    def start(cls, files):
        """Démarre un lot sur `files` : None si l'un d'eux appartient à un lot
        encore en cours, sinon le BatchClaims du lot, à terminer par finish().
        Test et inscription sous le même verrou : deux lots lancés au même
        instant sur les mêmes fichiers ne peuvent pas démarrer tous les deux."""
        claims = cls(files)
        with _lock:
            if any(claims._files & other._files for other in _running):
                return None
            _running.append(claims)
        return claims

    def finish(self):
        """Fin du lot : toutes ses réservations sont libérées."""
        self.release_all()
        with _lock:
            _running[:] = [c for c in _running if c is not self]

    def claim(self, path) -> bool:
        """Réserve `path` pour ce lot. False s'il est réservé par un autre lot."""
        key = _norm(path)
        with _lock:
            owner = _owners.get(key)
            if owner is not None and owner is not self:
                return False
            _owners[key] = self
            self._keys.add(key)
            return True

    def reserve(self, candidates):
        """Premier chemin de `candidates` qui n'existe pas sur le disque et
        qu'aucun lot n'a réservé : réservé pour ce lot et retourné. Le test
        d'existence et la réservation se font sous le même verrou, aucun autre
        lot ne peut donc choisir le même nom entre les deux."""
        with _lock:
            for path in candidates:
                key = _norm(path)
                if key in _owners or os.path.exists(path):
                    continue
                _owners[key] = self
                self._keys.add(key)
                return path
        return None

    def release_all(self):
        with _lock:
            for key in self._keys:
                if _owners.get(key) is self:
                    del _owners[key]
            self._keys.clear()


def is_claimed(path) -> bool:
    with _lock:
        return _norm(path) in _owners


def files_in_running_batch(files) -> bool:
    """True si l'un de ces fichiers appartient à un lot encore en cours."""
    wanted = {_norm(f) for f in files}
    with _lock:
        return any(wanted & other._files for other in _running)


def free_name_candidates(base_path, ext, numbered="{base} ({n}){ext}"):
    """Noms proposés pour un fichier à créer : `base_path + ext`, puis
    `numbered` avec n = 1, 2, 3… (sans fin, à consommer par reserve)."""
    yield base_path + ext
    n = 1
    while True:
        yield numbered.format(base=base_path, n=n, ext=ext)
        n += 1
