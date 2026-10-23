"""Configuration commune de la suite de tests.

APPDATA est redirigé vers un dossier temporaire avant tout test : la
configuration (config_manager.default_config_dir) et le journal des erreurs
(error_log.log_dir, fichiers de secours des process PDF) y sont alors créés,
jamais dans le vrai %APPDATA%\\MosaicView de l'utilisateur. La variable est
posée dans l'environnement du process des tests, donc héritée par les process
qu'ils démarrent (PdfProbe, process PDF) : eux non plus n'écrivent plus
ailleurs. Le dossier est supprimé à la fin de la suite.

Un process démarré par un test (méthode spawn) réimporte ce fichier : il
reprend alors le dossier de la suite (_MARKER), au lieu d'en créer un second
qui ne serait jamais supprimé.
"""
import os
import shutil
import tempfile

_MARKER = "MOSAICVIEW_TESTS_APPDATA"

REAL_APPDATA = os.environ.get("MOSAICVIEW_TESTS_REAL_APPDATA", os.environ.get("APPDATA"))
TEST_APPDATA = os.environ.get(_MARKER)
_OWNER = TEST_APPDATA is None  # process de pytest, qui crée et supprime le dossier
if _OWNER:
    TEST_APPDATA = tempfile.mkdtemp(prefix="mosaicview_tests_appdata_")
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
    if _OWNER:
        shutil.rmtree(TEST_APPDATA, ignore_errors=True)
