"""La suite de tests n'écrit jamais dans le vrai %APPDATA%\\MosaicView
(tests/conftest.py) : configuration et journal des erreurs, y compris ceux
des process PDF démarrés par les tests, vont dans un dossier temporaire."""
import multiprocessing
import os

import conftest
from modules.qt import error_log
from modules.qt.config_manager import default_config_dir


def _child_dirs(queue):
    from modules.qt import error_log as log
    from modules.qt.config_manager import default_config_dir as cfg_dir
    queue.put((cfg_dir(), log.log_dir()))


def _inside(path, root):
    return os.path.normcase(os.path.abspath(path)).startswith(os.path.normcase(os.path.abspath(root)))


def test_config_and_error_log_go_to_the_test_folder():
    assert _inside(default_config_dir(), conftest.TEST_APPDATA)
    assert _inside(error_log.log_dir(), conftest.TEST_APPDATA)
    if conftest.REAL_APPDATA:
        assert not _inside(error_log.log_dir(), conftest.REAL_APPDATA)


def test_processes_started_by_tests_use_the_test_folder_too():
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_child_dirs, args=(queue,))
    proc.start()
    try:
        cfg_dir, log_dir = queue.get(timeout=60)
    finally:
        proc.join(30)
    assert _inside(cfg_dir, conftest.TEST_APPDATA)
    assert _inside(log_dir, conftest.TEST_APPDATA)
