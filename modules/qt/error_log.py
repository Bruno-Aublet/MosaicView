"""
modules/qt/error_log.py — Journal des erreurs inattendues de MosaicView.

Garde la trace des erreurs que rien ne rattrape : exception Python non
interceptée (y compris dans un slot Qt ou un thread) et plantage natif (Qt,
Pillow, 7-Zip, pile écrite par faulthandler). Actif dans l'exécutable comme en
mode développement : l'exe n'a pas de console (sys.stderr vaut None), une
telle erreur n'y laisserait aucune trace ; depuis les sources, la trace du
terminal est noyée parmi les prints et perdue à la fermeture.

- Un fichier par session dans %APPDATA%\\MosaicView\\errors (errors_<date>_<pid>.log),
  ouvert au démarrage par start() : faulthandler exige un fichier ouvert à
  l'avance. Rien n'y est écrit tant qu'aucune erreur ne se produit, et un
  fichier resté vide est supprimé au démarrage suivant (prune).
- Limites : MAX_AGE_DAYS jours, MAX_FILES fichiers, MAX_SESSION_BYTES par
  session (au-delà, une ligne de troncature puis plus rien).
- Chaque trace est écrite et forcée sur le disque aussitôt : la fin du process
  peut être brutale (os._exit quand un worker ne s'arrête pas, plantage natif).
- Les hooks appellent ensuite le comportement précédent : en mode
  développement, la trace s'affiche toujours dans le terminal. Seule la pile
  d'un plantage natif va dans le fichier au lieu du terminal (faulthandler
  n'écrit que vers une seule destination).
- Le journal ne fait jamais échouer l'application : start() rend None en cas
  de problème, et une écriture impossible est abandonnée sans bruit.

Sans Qt : testable seul (tests/test_error_log.py).
"""
import datetime
import faulthandler
import os
import sys
import threading
import time
import traceback

MAX_AGE_DAYS = 10
MAX_FILES = 20
MAX_SESSION_BYTES = 1024 * 1024

_PREFIX = "errors_"
_SUFFIX = ".log"

_session = None  # _Session de ce process, posée par start()


def log_dir():
    """Dossier des journaux : %APPDATA%\\MosaicView\\errors."""
    from modules.qt.config_manager import default_config_dir
    return os.path.join(default_config_dir(), "errors")


def current_log_path():
    """Fichier du journal de la session en cours, None s'il n'a pas pu être ouvert."""
    return _session.path if _session is not None else None


def error_logs(folder=None):
    """Journaux qui contiennent des erreurs (session en cours et précédentes),
    du plus récent au plus ancien d'après leur dernière écriture. Un journal
    vide (session sans erreur) ne compte pas."""
    folder = folder or log_dir()
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    logs = []
    for name in names:
        if not (name.startswith(_PREFIX) and name.endswith(_SUFFIX)):
            continue
        path = os.path.join(folder, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        if stat.st_size > 0:
            logs.append((stat.st_mtime, path))
    logs.sort(reverse=True)
    return [path for _mtime, path in logs]


def latest_error_log(folder=None):
    """Journal le plus récent qui contient des erreurs, None s'il n'y en a aucun."""
    logs = error_logs(folder)
    return logs[0] if logs else None


def all_error_logs_text(folder=None):
    """Contenu de tous les journaux d'erreurs, du plus récent au plus ancien,
    chacun commençant par son en-tête (version, date de la session) : c'est ce
    que la fenêtre du journal affiche, copie et envoie. "" s'il n'y en a aucun
    de lisible."""
    parts = []
    for path in error_logs(folder):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read().strip()
        except OSError:
            continue
        if text:
            parts.append(text)
    return "\n\n\n".join(parts)


def prune(folder, keep=None, now=None):
    """Supprime dans `folder` les journaux vides, ceux de plus de MAX_AGE_DAYS
    jours, puis les plus anciens au-delà de MAX_FILES. `keep` (le journal de la
    session en cours) n'est jamais supprimé."""
    now = time.time() if now is None else now
    logs = []
    for name in os.listdir(folder):
        if not (name.startswith(_PREFIX) and name.endswith(_SUFFIX)):
            continue
        path = os.path.join(folder, name)
        if keep is not None and os.path.normcase(path) == os.path.normcase(keep):
            continue
        try:
            stat = os.stat(path)
        except OSError:
            continue
        if stat.st_size == 0 or now - stat.st_mtime > MAX_AGE_DAYS * 86400:
            _remove(path)
        else:
            logs.append((stat.st_mtime, path))
    logs.sort(reverse=True)
    allowed = MAX_FILES - (1 if keep is not None else 0)
    for _mtime, path in logs[max(allowed, 0):]:
        _remove(path)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass  # sans conséquence : journal encore ouvert ailleurs, retenté au démarrage suivant


class _Session:
    """Fichier journal d'une session : en-tête écrit avant la première trace,
    écritures sérialisées (plusieurs threads) et limitées à MAX_SESSION_BYTES."""

    def __init__(self, path, file, version):
        self.path = path
        self.file = file
        self.version = version
        self.started = datetime.datetime.now()
        self.written = 0
        self.truncated = False
        self._lock = threading.Lock()

    def write_error(self, title, exc_type, exc, tb):
        """Ajoute la trace d'une erreur. Ne lève jamais : le journal ne doit
        pas créer d'erreur de plus."""
        try:
            text = "".join(traceback.format_exception(exc_type, exc, tb))
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            record = f"\n===== {stamp} — {title} =====\n{text}"
            with self._lock:
                if self.truncated:
                    return
                if self.written == 0:
                    record = (f"MosaicView {self.version} — session du "
                              f"{self.started:%Y-%m-%d %H:%M:%S} (pid {os.getpid()})\n") + record
                size = len(record.encode("utf-8", errors="replace"))
                if self.written + size > MAX_SESSION_BYTES:
                    record = (f"\n[journal tronqué : plus de {MAX_SESSION_BYTES // 1024} Ko "
                              f"d'erreurs dans cette session]\n")
                    self.truncated = True
                self.file.write(record)
                self.file.flush()
                os.fsync(self.file.fileno())
                self.written += size
        except Exception:
            pass  # sans conséquence : l'erreur d'origine est quand même traitée par le hook précédent


def make_excepthook(session, previous):
    """sys.excepthook qui écrit l'erreur dans le journal, puis appelle le hook
    `previous` (affichage dans le terminal en mode développement)."""
    def hook(exc_type, exc, tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            session.write_error("exception non rattrapée", exc_type, exc, tb)
        previous(exc_type, exc, tb)
    return hook


def make_thread_excepthook(session, previous):
    """threading.excepthook équivalent, pour une exception dans un thread Python."""
    def hook(args):
        if not issubclass(args.exc_type, SystemExit):
            name = args.thread.name if args.thread is not None else "?"
            session.write_error(f"exception non rattrapée dans le thread {name}",
                                args.exc_type, args.exc_value, args.exc_traceback)
        previous(args)
    return hook


def start(version, folder=None):
    """Ouvre le journal de la session et installe les hooks. Rend le chemin du
    fichier, ou None si le journal n'a pas pu être mis en place (l'application
    démarre alors normalement, sans journal). Appelée une seule fois, par
    MosaicView.main, après le relais vers une instance déjà ouverte : une
    seconde instance, qui se ferme aussitôt, ne crée pas de journal."""
    global _session
    if _session is not None:
        return _session.path
    try:
        folder = folder or log_dir()
        os.makedirs(folder, exist_ok=True)
        name = f"{_PREFIX}{datetime.datetime.now():%Y-%m-%d_%H-%M-%S}_{os.getpid()}{_SUFFIX}"
        path = os.path.join(folder, name)
        file = open(path, "a", encoding="utf-8", errors="replace")
        prune(folder, keep=path)
        session = _Session(path, file, version)
        faulthandler.enable(file=file, all_threads=True)
        sys.excepthook = make_excepthook(session, sys.excepthook)
        threading.excepthook = make_thread_excepthook(session, threading.excepthook)
        _session = session
        return path
    except Exception:
        return None  # repli : application sans journal des erreurs
