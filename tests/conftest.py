"""Configuration commune de la suite de tests.

Tout ce que la suite écrit sur le disque va dans un seul dossier temporaire,
supprimé à la fin de la suite : rien n'y reste d'un lancement à l'autre.

- APPDATA y est redirigé (sous-dossier `appdata`) avant tout test : la
  configuration (config_manager.default_config_dir) et le journal des erreurs
  (error_log.log_dir, fichiers de secours des process PDF) y sont alors créés,
  jamais dans le vrai %APPDATA%\\MosaicView de l'utilisateur. La variable est
  posée dans l'environnement du process des tests, donc héritée par les
  process qu'ils démarrent (PdfProbe, process PDF) : eux non plus n'écrivent
  plus ailleurs.
- Les dossiers `tmp_path` de pytest y sont créés (sous-dossier `tmp`, par
  PYTEST_DEBUG_TEMPROOT, lue par pytest au premier `tmp_path`) au lieu de
  %TEMP%\\pytest-of-<compte>, où pytest garde les trois derniers lancements.

Une suite tuée (bouton Stop, process arrêté) ne passe pas par sa fin et laisse
son dossier : celui-ci est supprimé au lancement suivant s'il a plus de deux
heures, jamais avant, pour ne pas retirer le dossier d'une suite en cours.

Un process démarré par un test (méthode spawn) réimporte ce fichier : il
reprend alors le dossier de la suite (_MARKER), au lieu d'en créer un second
qui ne serait jamais supprimé.
"""
import os
import shutil
import tempfile
import time

_MARKER = "MOSAICVIEW_TESTS_APPDATA"
_SESSION_PREFIX = "mosaicview_tests_"
_KILLED_SUITE_AGE = 2 * 3600


def _remove_killed_suites_dirs():
    """Supprime les dossiers de plus de deux heures laissés par une suite tuée."""
    root = tempfile.gettempdir()
    limit = time.time() - _KILLED_SUITE_AGE
    for name in os.listdir(root):
        if not name.startswith(_SESSION_PREFIX):
            continue
        path = os.path.join(root, name)
        try:
            if os.path.isdir(path) and not os.path.islink(path) and os.path.getmtime(path) < limit:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue  # dossier disparu ou illisible : repris au lancement suivant


REAL_APPDATA = os.environ.get("MOSAICVIEW_TESTS_REAL_APPDATA", os.environ.get("APPDATA"))
TEST_APPDATA = os.environ.get(_MARKER)
_OWNER = TEST_APPDATA is None  # process de pytest, qui crée et supprime le dossier
if _OWNER:
    _remove_killed_suites_dirs()
    _session_dir = tempfile.mkdtemp(prefix=_SESSION_PREFIX)
    TEST_APPDATA = os.path.join(_session_dir, "appdata")
    os.mkdir(TEST_APPDATA)
    _pytest_temproot = os.path.join(_session_dir, "tmp")
    os.mkdir(_pytest_temproot)
    os.environ["PYTEST_DEBUG_TEMPROOT"] = _pytest_temproot
    os.environ[_MARKER] = TEST_APPDATA
    os.environ["MOSAICVIEW_TESTS_REAL_APPDATA"] = REAL_APPDATA or ""
os.environ["APPDATA"] = TEST_APPDATA
# Version posée par error_log.start() pour les process enfants : jamais
# héritée d'une application lancée depuis le même terminal
os.environ.pop("MOSAICVIEW_VERSION", None)


import pytest


@pytest.fixture(autouse=True)
def _no_batch_left_running():
    """Après chaque test : aucun lot laissé « en cours » dans le registre
    global de batch_claims (un test qui démarre un lot sans le terminer
    réserverait ses fichiers pour les tests suivants)."""
    yield
    from modules.qt import batch_claims
    for claims in list(batch_claims._running):
        claims.finish()


# Posée par run_tests.py (suite lancée avant une publication) : un test sauté
# (WinRAR, 7-Zip, PyMuPDF ou tifffile absent) fait échouer la suite. Un test
# sauté n'échoue pas, et « tous PASSED » passerait sinon pour une couverture
# complète alors qu'une partie n'a pas été exécutée.
NO_SKIP = "MOSAICVIEW_TESTS_NO_SKIP"


def pytest_sessionfinish(session, exitstatus):
    if _OWNER and os.environ.get(NO_SKIP):
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        skipped = reporter.stats.get("skipped", []) if reporter is not None else []
        if skipped and session.exitstatus == 0:
            session.exitstatus = 1
            reporter.write_sep("=", f"{len(skipped)} test(s) sauté(s) : la suite n'est pas "
                                    "complète (voir la liste « SKIPPED » ci-dessus)", red=True)


def pytest_unconfigure(config):
    """Dossier de la suite supprimé en tout dernier : après la fin de session
    de pytest, qui travaille encore dans ses dossiers `tmp_path`."""
    if _OWNER:
        shutil.rmtree(os.path.dirname(TEST_APPDATA), ignore_errors=True)
