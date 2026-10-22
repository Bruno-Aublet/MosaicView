"""
modules/qt/error_log.py — Journal des erreurs de MosaicView.

Un seul fichier, %APPDATA%\\MosaicView\\errors\\errors.log, où l'on n'écrit
que lorsqu'une erreur se produit : exception Python non interceptée (y compris
dans un slot Qt ou un thread), erreur rattrapée et affichée par ErrorDialog
(log_handled_error), plantage natif (Qt, Pillow, 7-Zip, MuPDF). Actif dans
l'exécutable comme en mode développement : l'exe n'a pas de console
(sys.stderr vaut None), une telle erreur n'y laisserait aucune trace ; depuis
les sources, la trace du terminal est noyée parmi les prints et perdue à la
fermeture.

- Chaque entrée commence par une ligne d'en-tête (_HEADER_RE) : date, version,
  process, nature de l'erreur. Un démarrage sans erreur n'écrit rien.
- Textes du journal dans la langue de l'application, jamais en alphabet pIqaD
  ou tengwar : le journal est lu dans le Bloc-notes ou dans un mail, où ces
  caractères s'affichent en carrés. En-têtes par _wt() (_text) ; trace mise en
  forme sous localization.latin_texts(), qui fait de même pour un message
  d'exception traduit (FileInUseError…). Les traces Python restent en anglais.
  Avant le chargement des traductions (tout début du démarrage), un en-tête
  porte le nom de sa clé.
- Limites : MAX_AGE_DAYS jours, MAX_BYTES octets. Au démarrage et quand une
  écriture dépasse MAX_BYTES, les entrées trop vieilles sont retirées, puis
  les plus anciennes jusqu'à PRUNE_TARGET_BYTES (marge qui évite un ménage à
  chaque écriture suivante).
- Plusieurs process écrivent dans le même fichier (deux MosaicView ouverts,
  process du moteur PDF) : toute lecture ou écriture se fait sous le verrou de
  _LOCK_NAME (msvcrt.locking), et seul le détenteur du verrou ouvre le
  journal, ce qui permet au ménage de le remplacer d'un coup (temporaire puis
  os.replace) sans jamais le tronquer.
- Plantage natif : faulthandler exige un fichier ouvert à l'avance, où il
  écrit sans en-tête possible. Chaque process ouvre donc un fichier de secours
  caché (_CRASH_PREFIX…, nature, pid et version dans le nom), supprimé à sa
  fermeture normale. collect_crash_buffers verse dans le journal la trace d'un
  fichier de secours resté, avec son en-tête : au démarrage (appel de
  MosaicView.main une fois les traductions chargées), quand un process PDF est
  vu mort, et à la fermeture. Un fichier de secours encore ouvert par
  son process (vivant) ne peut pas être supprimé sous Windows : il est laissé
  tel quel.
- Chaque écriture est forcée sur le disque aussitôt : la fin du process peut
  être brutale (os._exit quand un worker ne s'arrête pas, plantage natif).
- Les hooks appellent ensuite le comportement précédent : en mode
  développement, la trace s'affiche toujours dans le terminal. Seule la pile
  d'un plantage natif va dans le fichier de secours au lieu du terminal
  (faulthandler n'écrit que vers une seule destination).
- Le journal ne fait jamais échouer l'application : start() rend None en cas
  de problème, et une écriture impossible est abandonnée sans bruit.

Sans Qt : testable seul (tests/test_error_log.py).
"""
import atexit
import datetime
import faulthandler
import os
import re
import sys
import threading
import time
import traceback

MAX_AGE_DAYS = 15
MAX_BYTES = 5 * 1024 * 1024
PRUNE_TARGET_BYTES = MAX_BYTES * 8 // 10
LOCK_TIMEOUT = 10.0  # secondes d'attente du verrou avant d'abandonner l'écriture

LOG_NAME = "errors.log"
_LOCK_NAME = "errors.lock"
_CRASH_PREFIX = ".crash_"
_CRASH_SUFFIX = ".tmp"
_VERSION_ENV = "MOSAICVIEW_VERSION"  # version transmise aux process enfants (moteur PDF)
_STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_HEADER_RE = re.compile(r"^===== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) — ", re.MULTILINE)

_journal = None      # _Journal de ce process, posé par start()
_crash_file = None   # fichier de secours ouvert pour faulthandler (ce process)
_lock = threading.Lock()  # écritures des threads d'un même process


def log_dir():
    """Dossier du journal : %APPDATA%\\MosaicView\\errors."""
    from modules.qt.config_manager import default_config_dir
    return os.path.join(default_config_dir(), "errors")


def log_path(folder=None):
    """Chemin du journal (qu'il existe ou non)."""
    return os.path.join(folder or log_dir(), LOG_NAME)


def existing_log_path(folder=None):
    """Chemin du journal s'il contient au moins une erreur, None sinon."""
    path = log_path(folder)
    try:
        return path if os.path.getsize(path) > 0 else None
    except OSError:
        return None


# ── Verrou entre process ──────────────────────────────────────────────────────

class _FileLock:
    """Verrou exclusif sur _LOCK_NAME, partagé par tous les process qui écrivent
    ou lisent le journal. Sans msvcrt (hors Windows), seul le verrou des threads
    s'applique."""

    def __init__(self, folder, timeout=LOCK_TIMEOUT):
        self._path = os.path.join(folder, _LOCK_NAME)
        self._timeout = timeout
        self._file = None

    def __enter__(self):
        _lock.acquire()
        try:
            self._file = open(self._path, "a+b")
            try:
                import msvcrt
            except ImportError:
                return self
            deadline = time.monotonic() + self._timeout
            while True:
                try:
                    self._file.seek(0)
                    msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
                    return self
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
        except BaseException:
            self._release()
            raise

    def __exit__(self, *exc):
        self._release()
        return False

    def _release(self):
        try:
            if self._file is not None:
                try:
                    import msvcrt
                    self._file.seek(0)
                    msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
                except (ImportError, OSError):
                    pass  # sans conséquence : le verrou tombe à la fermeture du fichier
                self._file.close()
        finally:
            self._file = None
            _lock.release()


# ── Entrées du journal ────────────────────────────────────────────────────────

def _text(key, **kwargs):
    """Texte d'en-tête du journal : par _wt(), jamais en alphabet pIqaD ou
    tengwar (voir docstring du module)."""
    try:
        from modules.qt.localization import _wt
        return _wt(key, **kwargs)
    except Exception:
        return key  # repli : nom de la clé, traductions indisponibles


def _header(stamp, version, pid, title):
    process = _text("error_log.entry.process", pid=pid)
    return f"===== {stamp} — MosaicView {version} — {process} — {title} =====\n"


def split_entries(text):
    """Entrées du journal dans l'ordre du fichier : [(date, texte complet de
    l'entrée)]. Un texte placé avant la première en-tête (fichier abîmé) est
    ignoré."""
    starts = [m.start() for m in _HEADER_RE.finditer(text)]
    entries = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        chunk = text[start:end].rstrip("\n") + "\n"
        stamp = _HEADER_RE.match(text, start).group(1)
        try:
            when = datetime.datetime.strptime(stamp, _STAMP_FORMAT)
        except ValueError:
            continue
        entries.append((when, chunk))
    return entries


def _pruned(entries, now, max_age_days, target_bytes):
    """Entrées gardées : celles de moins de max_age_days jours, puis les plus
    récentes tenant dans target_bytes."""
    limit = now - datetime.timedelta(days=max_age_days)
    kept = [(when, chunk) for when, chunk in entries if when >= limit]
    total = sum(len(chunk.encode("utf-8")) for _when, chunk in kept)
    while kept and total > target_bytes:
        total -= len(kept[0][1].encode("utf-8"))
        kept.pop(0)
    return kept


def _read_unlocked(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _rewrite_unlocked(path, entries):
    """Remplace le journal par `entries` d'un seul coup (temporaire du même
    dossier puis os.replace) : un échec laisse l'ancien journal intact."""
    if not entries:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(chunk for _when, chunk in entries))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def prune(folder=None, now=None):
    """Retire du journal les entrées de plus de MAX_AGE_DAYS jours, puis les
    plus anciennes au-delà de MAX_BYTES (jusqu'à PRUNE_TARGET_BYTES)."""
    folder = folder or log_dir()
    now = now or datetime.datetime.now()
    path = log_path(folder)
    with _FileLock(folder):
        _prune_unlocked(path, now, force=True)


def _prune_unlocked(path, now, force=False):
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if not force and size <= MAX_BYTES:
        return
    entries = split_entries(_read_unlocked(path))
    target = PRUNE_TARGET_BYTES if size > MAX_BYTES else MAX_BYTES
    kept = _pruned(entries, now, MAX_AGE_DAYS, target)
    if len(kept) != len(entries):
        _rewrite_unlocked(path, kept)


def append_entry(title, body, folder=None, version=None, pid=None, when=None):
    """Ajoute une entrée au journal, sous le verrou. Ne lève jamais : le journal
    ne doit pas créer d'erreur de plus."""
    try:
        folder = folder or log_dir()
        os.makedirs(folder, exist_ok=True)
        when = when or datetime.datetime.now()
        record = _header(when.strftime(_STAMP_FORMAT), version or _current_version(),
                         os.getpid() if pid is None else pid, title)
        record += body.rstrip("\n") + "\n\n"
        path = log_path(folder)
        with _FileLock(folder):
            with open(path, "a", encoding="utf-8", errors="replace", newline="\n") as f:
                f.write(record)
                f.flush()
                os.fsync(f.fileno())
            _prune_unlocked(path, datetime.datetime.now())
    except Exception:
        pass  # sans conséquence : l'erreur d'origine est quand même traitée par le hook précédent


def _current_version():
    if _journal is not None:
        return _journal.version
    return os.environ.get(_VERSION_ENV, "?")


def read_log_text(folder=None, newest_first=True, since=None, max_bytes=None):
    """Texte du journal, entrées de la plus récente à la plus ancienne (ce que
    la fenêtre affiche). since : seulement les entrées postérieures à cette
    date ; max_bytes : seulement les plus récentes tenant dans cette taille.
    Rend (texte, nombre d'entrées omises). ("", 0) s'il n'y a rien de
    lisible."""
    folder = folder or log_dir()
    try:
        with _FileLock(folder):
            text = _read_unlocked(log_path(folder))
    except OSError:
        return "", 0
    entries = split_entries(text)
    total = len(entries)
    if since is not None:
        entries = [(w, c) for w, c in entries if w >= since]
    if max_bytes is not None:
        kept, size = [], 0
        for when, chunk in reversed(entries):
            size += len(chunk.encode("utf-8"))
            if size > max_bytes and kept:
                break
            kept.append((when, chunk))
        entries = list(reversed(kept))
    if newest_first:
        entries = list(reversed(entries))
    return "\n".join(chunk for _w, chunk in entries).strip(), total - len(entries)


# ── Erreurs Python ────────────────────────────────────────────────────────────

class _Journal:
    """Écrit les erreurs de ce process dans le journal du dossier `folder`."""

    def __init__(self, folder, version):
        self.folder = folder
        self.version = version

    def write_error(self, title, exc_type, exc, tb):
        """Ajoute la trace d'une erreur (title : texte déjà traduit par
        _text). Ne lève jamais."""
        try:
            from modules.qt.localization import latin_texts
            with latin_texts():
                text = "".join(traceback.format_exception(exc_type, exc, tb))
        except Exception:
            text = repr(exc)
        append_entry(title, text, folder=self.folder, version=self.version)


def log_handled_error(exc):
    """Ajoute au journal la trace d'une erreur rattrapée et affichée à
    l'utilisateur (ErrorDialog) : sa fenêtre ne donne que le message, la pile
    resterait sinon introuvable. Sans journal en place (process enfant, start()
    en échec), ne fait rien."""
    journal = _journal
    if journal is not None and exc is not None:
        journal.write_error(_text("error_log.entry.handled"), type(exc), exc, exc.__traceback__)


def make_excepthook(journal, previous):
    """sys.excepthook qui écrit l'erreur dans le journal, puis appelle le hook
    `previous` (affichage dans le terminal en mode développement)."""
    def hook(exc_type, exc, tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            journal.write_error(_text("error_log.entry.uncaught"), exc_type, exc, tb)
        previous(exc_type, exc, tb)
    return hook


def make_thread_excepthook(journal, previous):
    """threading.excepthook équivalent, pour une exception dans un thread Python."""
    def hook(args):
        if not issubclass(args.exc_type, SystemExit):
            name = args.thread.name if args.thread is not None else "?"
            journal.write_error(_text("error_log.entry.uncaught_thread", thread=name),
                                args.exc_type, args.exc_value, args.exc_traceback)
        previous(args)
    return hook


# ── Plantages natifs : fichiers de secours ────────────────────────────────────

_CRASH_TITLE_KEYS = {"app": "error_log.entry.crash_app",
                     "pdf": "error_log.entry.crash_pdf"}


def _crash_name(kind, pid, version):
    safe_version = re.sub(r"[^0-9A-Za-z.]", "_", version or "?")
    return f"{_CRASH_PREFIX}{kind}_{pid}_{safe_version}{_CRASH_SUFFIX}"


def _parse_crash_name(name):
    m = re.fullmatch(re.escape(_CRASH_PREFIX) + r"([a-z]+)_(\d+)_(.+)" + re.escape(_CRASH_SUFFIX), name)
    return (m.group(1), int(m.group(2)), m.group(3)) if m else None


def _hide(path):
    """Fichier de secours caché dans l'Explorateur : ce n'est pas un journal."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)  # FILE_ATTRIBUTE_HIDDEN
    except Exception:
        pass  # sans conséquence : fichier seulement visible dans l'Explorateur


def _open_crash_buffer(folder, kind, version):
    """Ouvre le fichier de secours de ce process et y dirige faulthandler."""
    global _crash_file
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, _crash_name(kind, os.getpid(), version))
    # "a" et non "w" : Windows refuse de recréer un fichier caché déjà présent
    # (fichier d'un process précédent de même pid, pas encore ramassé)
    _crash_file = open(path, "a", encoding="utf-8", errors="replace")
    _hide(path)
    faulthandler.enable(file=_crash_file, all_threads=True)
    atexit.register(_close_crash_buffer, path)
    return path


def _close_crash_buffer(path):
    """Fermeture normale du process : plus de plantage à enregistrer, le
    fichier de secours (vide) est supprimé."""
    global _crash_file
    try:
        faulthandler.disable()
        if _crash_file is not None:
            _crash_file.close()
        _crash_file = None
        os.remove(path)
    except Exception:
        pass  # sans conséquence : fichier vide, supprimé par le prochain ramassage


def collect_crash_buffers(folder=None):
    """Verse dans le journal la trace des fichiers de secours laissés par un
    process qui a planté, puis les supprime. Un fichier encore ouvert par un
    process vivant (dont le nôtre) ne peut pas être supprimé : il est laissé.
    Un fichier vide (process arrêté sans plantage) est seulement supprimé.
    Ne lève jamais."""
    try:
        folder = folder or log_dir()
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        parsed = _parse_crash_name(name)
        if parsed is None:
            continue
        kind, pid, version = parsed
        path = os.path.join(folder, name)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            when = datetime.datetime.fromtimestamp(os.path.getmtime(path))
            os.remove(path)
        except OSError:
            continue  # process encore vivant (fichier ouvert), ou déjà ramassé par un autre
        if text.strip():
            title = _text(_CRASH_TITLE_KEYS.get(kind, "error_log.entry.crash_other"))
            append_entry(title, text, folder=folder, version=version, pid=pid, when=when)


def start_child_crash_log(folder=None):
    """Dans un process enfant (moteur PDF) : la pile d'un plantage natif
    (MuPDF) va dans son fichier de secours, versé ensuite dans le journal par
    l'application (collect_crash_buffers). Sans lui, seul « process arrêté »
    remonte. Ne fait jamais échouer le process."""
    try:
        _open_crash_buffer(folder or log_dir(), "pdf", os.environ.get(_VERSION_ENV, "?"))
    except Exception:
        pass  # repli : process enfant sans journal de plantage


# ── Mise en place ─────────────────────────────────────────────────────────────

def _remove_session_logs(folder):
    """Les fichiers errors_<date>_<pid>.log éventuellement présents (un journal
    par session) ne sont plus lus : supprimés."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if name.startswith("errors_") and name.endswith(".log"):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass  # sans conséquence : fichier ignoré, retenté au démarrage suivant


def start(version, folder=None):
    """Met en place le journal et installe les hooks. Rend le chemin du
    journal, ou None s'il n'a pas pu être mis en place (l'application démarre
    alors normalement, sans journal). Appelée une seule fois, par
    MosaicView.main, après le relais vers une instance déjà ouverte : une
    seconde instance, qui se ferme aussitôt, n'y touche pas."""
    global _journal
    if _journal is not None:
        return log_path(_journal.folder)
    try:
        folder = folder or log_dir()
        os.makedirs(folder, exist_ok=True)
        os.environ[_VERSION_ENV] = version
        _remove_session_logs(folder)
        # Traces d'un plantage précédent : ramassées par MosaicView.main une
        # fois les traductions chargées (leur en-tête est traduit)
        prune(folder)
        journal = _Journal(folder, version)
        _open_crash_buffer(folder, "app", version)
        sys.excepthook = make_excepthook(journal, sys.excepthook)
        threading.excepthook = make_thread_excepthook(journal, threading.excepthook)
        _journal = journal
        # À la fermeture : traces des process PDF plantés pendant la session
        atexit.register(collect_crash_buffers, folder)
        return log_path(folder)
    except Exception:
        return None  # repli : application sans journal des erreurs
