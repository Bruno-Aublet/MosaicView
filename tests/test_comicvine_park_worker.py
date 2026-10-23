"""ComicVine : mise à l'écart d'un worker remplacé ou d'une fenêtre fermée
(comicvine_dialog_qt._ComicVineDialog._park_running_worker).

_park_running_worker confie un worker encore actif à park_qthread (utils.py),
qui le détruit à sa fin. Un attribut de la fenêtre peut encore le désigner
(le parcage ne remet pas toujours l'attribut à None) : le parquer de nouveau —
à la fermeture de la fenêtre (_on_close), qui parque ses quatre workers — ne
doit jamais toucher un objet déjà détruit (isRunning() lèverait RuntimeError,
et les workers suivants de la liste ne seraient pas parqués).

Vérifié par le comportement : worker détruit, actif, terminé, sans signal
`progress`, absent. comicvine_dialog_qt.py importe PySide6 : la méthode est
extraite par AST (tests/ast_loader.load_methods) ; shiboken6.isValid et
park_qthread sont remplacés.
"""
import sys
import types
import warnings

import pytest

import ast_loader
from fakes import Signal

Base = ast_loader.load_methods("comicvine_dialog_qt.py", "_ComicVineDialog",
                               {"_park_running_worker"}, warnings=warnings)


class Worker:
    def __init__(self, running=True, alive=True, with_progress=True):
        self.running, self.alive = running, alive
        self.finished, self.error = Signal(), Signal()
        if with_progress:
            self.progress = Signal()
        for signal in self.signals():
            signal.connect(lambda *a: None)

    def signals(self):
        return [getattr(self, n) for n in ("finished", "error", "progress") if hasattr(self, n)]

    def isRunning(self):
        if not self.alive:
            raise RuntimeError("Internal C++ object already deleted")
        return self.running


@pytest.fixture
def parked(monkeypatch):
    shiboken = types.ModuleType("shiboken6")
    shiboken.isValid = lambda obj: obj.alive
    monkeypatch.setitem(sys.modules, "shiboken6", shiboken)
    from modules.qt import utils
    calls = []
    monkeypatch.setattr(utils, "park_qthread", calls.append)
    return calls


def test_destroyed_worker_is_left_alone(parked):
    worker = Worker(alive=False)
    Base()._park_running_worker(worker)                 # ne lève pas
    assert parked == []
    assert all(s._slots for s in worker.signals())      # jamais touché


def test_running_worker_is_disconnected_then_parked(parked):
    worker = Worker(running=True)
    Base()._park_running_worker(worker)
    assert parked == [worker]
    assert not any(s._slots for s in worker.signals())  # donnée périmée : slots débranchés


def test_finished_worker_is_disconnected_but_not_parked(parked):
    worker = Worker(running=False)
    Base()._park_running_worker(worker)
    assert parked == []
    assert not any(s._slots for s in worker.signals())


def test_worker_without_progress_signal_is_parked(parked):
    worker = Worker(with_progress=False)
    Base()._park_running_worker(worker)
    assert parked == [worker]


def test_no_worker_does_nothing(parked):
    Base()._park_running_worker(None)
    assert parked == []
