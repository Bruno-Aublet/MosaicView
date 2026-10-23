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

- Erreur rattrapée loin de la fenêtre qui l'affiche (worker qui l'envoie en
  texte par un signal, process PDF qui l'envoie par son canal) : sa pile est
  écrite là où l'exception existe encore, par log_exception ou
  log_if_unexpected. Une erreur attendue (is_expected_error : fichier
  absent ou illisible, archive abîmée, réseau…) n'a pas de pile utile et
  n'est pas écrite ; un défaut du programme (TypeError, KeyError…) l'est.
- Un process enfant (moteur PDF) écrit lui-même ses entrées
  (start_child_crash_log ; exception qui arrête le process :
  logged_child_process) : sans traductions chargées, il reçoit leurs
  titres déjà traduits de l'application (pass_titles_to_children, au
  lancement du process).
- Secrets masqués dans tout ce qui est écrit (_redact : api_key=…) : le
  journal est envoyé par mail, et une exception réseau chaînée garde l'URL
  appelée, clé ComicVine comprise.
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
import collections
import datetime
import faulthandler
import functools
import json
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
# Même attente depuis le thread principal (interface) : plus courte, une
# écriture bloquée par un autre process figerait sinon les deux panneaux
MAIN_THREAD_LOCK_TIMEOUT = 2.0
# Corps d'une entrée, en caractères : au-delà, le milieu est omis. Une entrée
# plus grosse que PRUNE_TARGET_BYTES serait retirée par le ménage aussitôt
# écrite, et tout le journal avec elle.
MAX_ENTRY_CHARS = 256 * 1024
# Une même erreur (même type, même pile) n'est écrite en entier que
# MAX_REPEATS fois par session ; ensuite une ligne courte à chaque palier de
# REPEAT_MARKS. Une erreur levée à chaque rafraîchissement (peinture, minuteur)
# figerait sinon l'interface et chasserait du journal la première erreur,
# souvent la cause.
MAX_REPEATS = 3
REPEAT_MARKS = (10, 100, 1000, 10000, 100000)

LOG_NAME = "errors.log"
_LOCK_NAME = "errors.lock"
_CRASH_PREFIX = ".crash_"
_CRASH_SUFFIX = ".tmp"
_VERSION_ENV = "MOSAICVIEW_VERSION"  # version transmise aux process enfants (moteur PDF)
_TITLES_ENV = "MOSAICVIEW_LOG_TITLES"  # titres traduits transmis aux process enfants
_CHILD_TITLE_KEYS = ("error_log.entry.process", "error_log.entry.pdf_error",
                     "error_log.entry.pdf_uncaught", "error_log.entry.repeated",
                     "error_log.entry.environment")
_STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
# Heure d'une entrée à la milliseconde, comparable à celle des dernières
# actions ; une entrée écrite sans millisecondes reste reconnue
_HEADER_RE = re.compile(r"^===== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:\.\d{3})? — ", re.MULTILINE)
_SECRET_RE = re.compile(r"((?:api_?key|access_token|token|password)=)[^&\s'\"<>]+",
                        re.IGNORECASE)

_journal = None      # _Journal de ce process, posé par start() ou start_child_crash_log()
_crash_file = None   # fichier de secours ouvert pour faulthandler (ce process)
_lock = threading.Lock()  # écritures des threads d'un même process
# Écriture en cours dans ce thread : une erreur levée pendant l'écriture (un
# __del__ appelé par le ramasse-miettes, vu par sys.unraisablehook) ne relance
# pas une écriture, qui attendrait _lock, non réentrant, indéfiniment
_writing = threading.local()


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
    s'applique. Attente : MAIN_THREAD_LOCK_TIMEOUT depuis le thread principal,
    LOCK_TIMEOUT ailleurs."""

    def __init__(self, folder, timeout=None):
        self._path = os.path.join(folder, _LOCK_NAME)
        if timeout is None:
            timeout = (MAIN_THREAD_LOCK_TIMEOUT
                       if threading.current_thread() is threading.main_thread() else LOCK_TIMEOUT)
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
    tengwar (voir docstring du module). Sans traductions chargées (process
    enfant, tout début du démarrage) : titre transmis par l'application
    (pass_titles_to_children), sinon nom de la clé."""
    try:
        from modules.qt.localization import _wt, get_localization
        if get_localization() is not None:
            return _wt(key, **kwargs)
    except Exception:
        pass  # repli : titre transmis par l'application, ou nom de la clé
    template = _child_titles().get(key, key)
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template


def _child_titles():
    try:
        titles = json.loads(os.environ.get(_TITLES_ENV, "{}"))
        return titles if isinstance(titles, dict) else {}
    except ValueError:
        return {}


def pass_titles_to_children():
    """À appeler juste avant de lancer un process enfant qui écrit dans le
    journal (moteur PDF) : ses titres d'entrées, traduits dans la langue du
    moment, lui sont transmis par une variable d'environnement, héritée au
    lancement. Ne lève jamais."""
    try:
        from modules.qt.localization import _wt, get_localization
        if get_localization() is not None:
            os.environ[_TITLES_ENV] = json.dumps({k: _wt(k) for k in _CHILD_TITLE_KEYS})
    except Exception:
        pass  # repli : entrées du process enfant titrées par le nom de leur clé


def _redact(text):
    """Masque la valeur d'un secret passé dans une URL (api_key=…, token=…)."""
    return _SECRET_RE.sub(r"\1***", text)


_FAKE_HEADER_RE = re.compile(r"^=====", re.MULTILINE)
_path_names = None  # [(motif du chemin, nom qui le remplace)], voir _shorten_paths


def _short_path(path):
    """Forme courte 8.3 d'un chemin (C:\\Users\\NOMLON~1), employée par
    certaines variables d'environnement (TEMP). None si indisponible."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf)):
            return buf.value
    except Exception:
        pass  # repli : seule la forme longue du chemin est remplacée
    return None


def _shorten_paths(text):
    """Remplace le dossier de l'exe (_MEIPASS) par <MosaicView> et le dossier
    du profil par %USERPROFILE% : le nom du compte Windows, souvent le nom réel
    de l'utilisateur, ne part pas dans le journal envoyé par mail, et les
    piles restent lisibles. Formes longue, courte (8.3) et à barres obliques."""
    global _path_names
    if _path_names is None:
        names = []
        for path, name in ((getattr(sys, "_MEIPASS", None), "<MosaicView>"),
                           (os.path.expanduser("~"), "%USERPROFILE%")):
            if not path or path == "~":
                continue
            variants = {path.rstrip("\\/"), (_short_path(path) or path).rstrip("\\/")}
            variants |= {v.replace("\\", "/") for v in variants}
            for variant in sorted(variants, key=len, reverse=True):
                names.append((re.compile(re.escape(variant), re.IGNORECASE), name))
        _path_names = names
    for pattern, name in _path_names:
        text = pattern.sub(lambda _m, n=name: n, text)
    return text


def _clean_body(body):
    """Corps d'une entrée tel qu'il est écrit : chemins du profil remplacés,
    secrets masqués, milieu omis au-delà de MAX_ENTRY_CHARS, et ligne qui
    commence par ===== décalée d'une espace (un nom de fichier ou un message
    extérieur créerait sinon une fausse entrée, à la date choisie)."""
    body = _redact(_shorten_paths(body.rstrip("\n")))
    if len(body) > MAX_ENTRY_CHARS:
        head = MAX_ENTRY_CHARS // 4
        tail = MAX_ENTRY_CHARS - head
        body = f"{body[:head]}\n[… {len(body) - head - tail} …]\n{body[-tail:]}"
    return _FAKE_HEADER_RE.sub(" =====", body)


# ── Dernières actions ─────────────────────────────────────────────────────────

TRAIL_SIZE = 20
# Dernières actions importantes de ce process : (heure, panneau ou None, clé
# error_log.trail.<clé>, paramètres). Gardées en mémoire seulement, jointes aux
# entrées d'erreur : ce qui se passait juste avant (fichier, panneau, opération
# en cours ou terminée) est souvent ce qui manque pour reproduire une erreur.
_trail = collections.deque(maxlen=TRAIL_SIZE)
_trail_lock = threading.Lock()


def record_action(key, panel=None, **params):
    """Mémorise une action importante (texte error_log.trail.<key>, traduit à
    l'écriture d'une entrée). panel : numéro du panneau, ou None. Rien n'est
    écrit sur le disque. Ne lève jamais."""
    try:
        with _trail_lock:
            _trail.append((datetime.datetime.now(), panel, key, params))
    except Exception:
        pass  # sans conséquence : action seulement absente du fil des dernières actions


def _milliseconds(when, fmt):
    """`when` au format `fmt`, suivi des millisecondes (« 08:14:47.312 »)."""
    return f"{when.strftime(fmt)}.{when.microsecond // 1000:03d}"


def describe_paths(paths):
    """Nom du premier fichier, suivi du nombre des autres : « a.cbz (+3) »."""
    names = [os.path.basename(p) for p in paths if p]
    if not names:
        return ""
    return names[0] if len(names) == 1 else f"{names[0]} (+{len(names) - 1})"


def _visible(value):
    """Valeur d'un paramètre, caractères de contrôle échappés : un nom de
    fichier piégé ne crée pas de fausse ligne dans l'entrée."""
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in str(value))


def _trail_text():
    """Bloc « Dernières actions » joint à une entrée d'erreur, "" s'il n'y en a
    aucune (process enfant, tout début du démarrage)."""
    with _trail_lock:
        items = list(_trail)
    if not items:
        return ""
    lines = [_text("error_log.trail.title")]
    for when, panel, key, params in items:
        text = _text(f"error_log.trail.{key}", **{k: _visible(v) for k, v in params.items()})
        if panel is not None:
            text = f"{_text('error_log.trail.panel', number=panel)} — {text}"
        lines.append(f"  {_milliseconds(when, '%H:%M:%S')}  {text}")
    return "\n".join(lines)


_environment = None  # valeurs de _environment_line, calculées une fois par process


def _environment_values():
    global _environment
    if _environment is None:
        import platform

        def version(module):
            try:
                return __import__(module).__version__
            except Exception:
                return "?"  # repli : version inconnue, l'entrée est quand même écrite

        _environment = {
            "system": platform.platform(), "python": platform.python_version(),
            "qt": version("PySide6"), "pillow": version("PIL"),
            "program": "MosaicView.exe" if getattr(sys, "frozen", False) else "MosaicView.py",
        }
    return _environment


def _environment_line():
    """Deuxième ligne de chaque entrée : configuration de l'utilisateur
    (Windows, Python, PySide6, Pillow, exe ou sources), indispensable pour
    reproduire une erreur signalée par mail ou lue dans le journal."""
    try:
        return _text("error_log.entry.environment", **_environment_values())
    except Exception:
        return ""  # repli : entrée écrite sans la ligne de configuration


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
    ne doit pas créer d'erreur de plus. Une entrée demandée pendant une
    écriture du même thread est abandonnée (voir _writing)."""
    if getattr(_writing, "on", False):
        return
    _writing.on = True
    try:
        folder = folder or log_dir()
        os.makedirs(folder, exist_ok=True)
        when = when or datetime.datetime.now()
        record = _header(_milliseconds(when, _STAMP_FORMAT), version or _current_version(),
                         os.getpid() if pid is None else pid, title)
        record += _environment_line() + "\n" + _clean_body(body) + "\n\n"
        path = log_path(folder)
        with _FileLock(folder):
            with open(path, "a", encoding="utf-8", errors="replace", newline="\n") as f:
                f.write(record)
                f.flush()
                os.fsync(f.fileno())
            _prune_unlocked(path, datetime.datetime.now())
    except Exception:
        pass  # sans conséquence : l'erreur d'origine est quand même traitée par le hook précédent
    finally:
        _writing.on = False


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

    def write_error(self, title, exc_type, exc, tb, context=""):
        """Ajoute la trace d'une erreur (title : texte déjà traduit par
        _text ; context : lignes écrites avant la pile). Au-delà de
        MAX_REPEATS occurrences de la même erreur, rien, sauf une ligne courte
        à chaque palier de REPEAT_MARKS. Ne lève jamais."""
        count = _occurrence(exc_type, tb)
        if count > MAX_REPEATS and count not in REPEAT_MARKS:
            return
        try:
            from modules.qt.localization import latin_texts
            with latin_texts():
                if count > MAX_REPEATS:
                    text = "".join(traceback.format_exception_only(exc_type, exc))
                else:
                    text = "".join(traceback.format_exception(exc_type, exc, tb))
        except Exception:
            text = repr(exc)
        if count > MAX_REPEATS:
            title = _text("error_log.entry.repeated", count=count)
        else:
            parts = [context.rstrip("\n"), _trail_text(), text]
            text = "\n\n".join(part for part in parts if part)
        append_entry(title, text, folder=self.folder, version=self.version)


_occurrences = {}  # empreinte d'une erreur → nombre d'occurrences dans ce process
_occurrences_lock = threading.Lock()


def _occurrence(exc_type, tb):
    """Numéro de cette occurrence de l'erreur dans la session : même type et
    mêmes (fichier, fonction, ligne) dans la pile = même erreur."""
    try:
        frames = tuple((f.filename, f.name, f.lineno) for f in traceback.extract_tb(tb))
    except Exception:
        frames = ()  # repli : erreur reconnue par son seul type
    key = (getattr(exc_type, "__qualname__", repr(exc_type)), frames)
    with _occurrences_lock:
        count = _occurrences.get(key, 0) + 1
        _occurrences[key] = count
    return count


def _dialog_text(title, message):
    """Titre et message d'une fenêtre d'erreur (texte ou fonction () -> str),
    en lettres latines (journal lu dans le Bloc-notes)."""
    lines = []
    try:
        from modules.qt.localization import latin_texts
        with latin_texts():
            for value in (title, message):
                value = value() if callable(value) else value
                if value:
                    lines.append(str(value))
    except Exception:
        pass  # repli : entrée écrite avec la seule pile
    return "\n".join(lines)


def log_handled_error(exc, title=None, message=None):
    """Ajoute au journal la trace d'une erreur rattrapée et affichée à
    l'utilisateur (ErrorDialog), précédée du titre et du message de sa fenêtre
    (texte ou fonction () -> str) : c'est ce que l'utilisateur décrira, et
    souvent le seul endroit qui nomme le fichier en cause. Sans journal en
    place (start() en échec), ne fait rien."""
    journal = _journal
    if journal is not None and exc is not None:
        journal.write_error(_text("error_log.entry.handled"), type(exc), exc, exc.__traceback__,
                            context=_dialog_text(title, message))


# Erreurs attendues venues d'une bibliothèque (archive abîmée, PDF illisible,
# réseau) : reconnues par le module de leur classe, sans l'importer (PyMuPDF ne
# se charge jamais dans le process de l'application)
_EXPECTED_MODULES = frozenset({"zipfile", "tarfile", "PIL", "rarfile", "py7zr",
                               "fitz", "pymupdf", "requests", "urllib3"})


def is_expected_error(exc):
    """Erreur attendue, dont la pile n'apprend rien : fichier absent, protégé
    ou illisible, disque plein, réseau (OSError), fin de fichier, mémoire
    insuffisante, erreur d'une bibliothèque de lecture (_EXPECTED_MODULES),
    exception du projet qui déclare expected_error = True (réponse refusée par
    ComicVine…). Tout le reste est un défaut possible du programme."""
    if isinstance(exc, (OSError, EOFError, MemoryError)):
        return True
    if getattr(exc, "expected_error", False):
        return True
    return (type(exc).__module__ or "").split(".")[0] in _EXPECTED_MODULES


def log_exception(exc, title_key="error_log.entry.handled"):
    """Ajoute au journal la pile d'une erreur rattrapée, depuis n'importe quel
    thread ou process enfant (moteur PDF) : à appeler là où l'exception
    existe encore, avant de l'envoyer en texte (signal d'un worker, canal d'un
    process). La pile s'affiche aussi dans le terminal en mode développement,
    même sans journal en place. Ne lève jamais."""
    if exc is None:
        return
    journal = _journal
    if journal is not None:
        journal.write_error(_text(title_key), type(exc), exc, exc.__traceback__)
    if sys.stderr is not None:
        try:
            traceback.print_exception(type(exc), exc, exc.__traceback__)
        except Exception:
            pass  # sans conséquence : la pile est déjà dans le journal


def log_if_unexpected(exc, title_key="error_log.entry.handled"):
    """log_exception, sauf pour une erreur attendue (is_expected_error)."""
    if exc is not None and not is_expected_error(exc):
        log_exception(exc, title_key)


def make_excepthook(journal, previous):
    """sys.excepthook qui écrit l'erreur dans le journal, puis appelle le hook
    `previous` (affichage dans le terminal en mode développement)."""
    def hook(exc_type, exc, tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            journal.write_error(_text("error_log.entry.uncaught"), exc_type, exc, tb)
        previous(exc_type, exc, tb)
    return hook


def make_unraisablehook(journal, previous):
    """sys.unraisablehook : exception que Python ne peut pas lever (dans un
    __del__, une callback de weakref, la fermeture d'un générateur) ; il ne
    fait sinon que l'écrire sur sys.stderr, qui vaut None dans l'exe."""
    def hook(unraisable):
        if unraisable.exc_type is not None and not issubclass(unraisable.exc_type, KeyboardInterrupt):
            try:
                where = f"{unraisable.err_msg or 'Exception ignored in'}: {unraisable.object!r}"
            except Exception:
                where = unraisable.err_msg or "Exception ignored"  # repli : repr de l'objet impossible
            journal.write_error(_text("error_log.entry.unraisable"), unraisable.exc_type,
                                unraisable.exc_value, unraisable.exc_traceback, context=where)
        previous(unraisable)
    return hook


def log_qt_message(level, message, category=""):
    """Message grave de Qt (level : "critical" ou "fatal"), reçu par le
    gestionnaire de messages posé par MosaicView.main. Qt l'écrit sinon sur sa
    sortie d'erreur, absente dans l'exe ; pour un qFatal (« QThread: Destroyed
    while thread is still running »), faulthandler ne garde que la pile Python,
    et ce texte est la seule indication de la cause. Le même message n'est
    écrit que MAX_REPEATS fois, puis une ligne à chaque palier de
    REPEAT_MARKS. Écrit avant le retour : Qt arrête le process juste après un
    message fatal. Ne lève jamais."""
    journal = _journal
    if journal is None:
        return
    key = ("qt", level, message)
    with _occurrences_lock:
        count = _occurrences.get(key, 0) + 1
        _occurrences[key] = count
    if count > MAX_REPEATS and count not in REPEAT_MARKS:
        return
    title = (_text("error_log.entry.repeated", count=count) if count > MAX_REPEATS
             else _text("error_log.entry.qt_message"))
    body = f"Qt {level}: {message}" + (f"\n[{category}]" if category else "")
    if count <= MAX_REPEATS:
        trail = _trail_text()
        body += f"\n\n{trail}" if trail else ""
    append_entry(title, body, folder=journal.folder, version=journal.version)


def log_forced_exit(worker_names):
    """Fermeture forcée de l'application (os._exit en fin de MosaicView.main) :
    des traitements en arrière-plan ne se sont pas arrêtés à temps
    (worker_names : leurs classes). Sans cette entrée, rien ne dirait lequel
    rend la fermeture lente ou bloque. Rien si la liste est vide."""
    journal = _journal
    if journal is None or not worker_names:
        return
    body = "\n".join(worker_names)
    trail = _trail_text()
    body += f"\n\n{trail}" if trail else ""
    append_entry(_text("error_log.entry.forced_exit"), body,
                 folder=journal.folder, version=journal.version)


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
    l'application (collect_crash_buffers) ; les erreurs rattrapées
    (log_exception) et celle qui arrête le process (logged_child_process) sont
    écrites directement dans le journal. Sans lui, seul « process arrêté »
    remonte. Ne fait jamais échouer le process."""
    global _journal
    try:
        folder = folder or log_dir()
        version = os.environ.get(_VERSION_ENV, "?")
        _open_crash_buffer(folder, "pdf", version)
        _journal = _Journal(folder, version)
    except Exception:
        pass  # repli : process enfant sans journal de plantage


def logged_child_process(target):
    """Décorateur de la fonction d'un process enfant (moteur PDF) : une
    exception qui en sort est écrite dans le journal, puis relancée.
    multiprocessing la rattrape lui-même et ne fait que l'écrire sur la sortie
    d'erreur du process (sys.excepthook n'est pas appelé), inexistante dans
    l'exe : l'application ne verrait que « process arrêté ». La fonction
    décorée garde son nom (functools.wraps) : multiprocessing la retrouve par
    lui pour la lancer."""
    @functools.wraps(target)
    def run(*args, **kwargs):
        try:
            return target(*args, **kwargs)
        except Exception as e:
            journal = _journal  # posé par start_child_crash_log, au début de target
            if journal is not None:
                journal.write_error(_text("error_log.entry.pdf_uncaught"),
                                    type(e), e, e.__traceback__)
            raise
    return run


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
        sys.unraisablehook = make_unraisablehook(journal, sys.unraisablehook)
        _journal = journal
        # À la fermeture : traces des process PDF plantés pendant la session
        atexit.register(collect_crash_buffers, folder)
        return log_path(folder)
    except Exception:
        return None  # repli : application sans journal des erreurs
