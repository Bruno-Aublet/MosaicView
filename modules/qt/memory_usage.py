"""Mémoire utilisée par MosaicView, pour l'indicateur de la barre de statut
(module sans Qt).

- measure() : mémoire réellement occupée en RAM (plage de travail) par
  MosaicView et par ses programmes annexes (process PDF, 7-Zip, UnRAR), et
  mémoire physique du PC. Les autres programmes lancés par MosaicView
  (Bloc-notes d'un journal, application par défaut d'un fichier ouvert,
  Explorateur) ne sont pas les siens et ne sont pas comptés. Dans la version
  « un seul fichier » de l'exe, le programme de démarrage de PyInstaller reste ouvert toute la session (il
  attend la fin de MosaicView pour supprimer le dossier extrait) : il est
  compté avec MosaicView.
- panel_memory_estimate(state) : estimation de ce que contient un panneau
  (pages, historique, images décodées, vignettes, pages déjà lues par un
  chargement en cours : reading_started / reading_finished). Windows ne
  mesure que le process entier : la somme des panneaux reste inférieure au
  total, le reste étant l'application elle-même (Qt, Python, polices,
  Bibliothèque…).
- memory_level(share) : couleur de l'indicateur d'après le pourcentage
  affiché de la mémoire du PC.
- hold_helper_programs(measure, now) : mesure dont les programmes annexes
  restent comptés quelques secondes après leur dernière apparition
  (HelperHold) ; forget_helper_programs() les oublie à la fin d'un
  chargement ou d'un lot.
"""
import os
import sys
import threading
from collections import namedtuple

from PIL import Image

# Seuils de couleur (pourcentage entier affiché de la mémoire physique du
# PC) : vert en dessous d'ORANGE_FROM, orange jusqu'à RED_FROM exclu, rouge
# au-delà. Un tiers de l'échelle par couleur, comme les zones verte, jaune et
# rouge du cadran de la jauge (memory_gauge/, tools/make_memory_gauges.py),
# dont l'aiguille est à 33 % encore dans le vert et à 34 % dans le jaune, à
# 66 % encore dans le jaune et à 67 % dans le rouge. Seuils comparés au
# pourcentage arrondi, pas à la part exacte : « 33% » ne s'affiche jamais en
# orange avec une aiguille dans le vert.
ORANGE_FROM = 34
RED_FROM = 67

# app : MosaicView (et son programme de démarrage) ; helpers : programmes
# annexes en cours ; ram : mémoire physique du PC, en octets. helper_names :
# noms des programmes annexes en cours, dans l'ordre de HELPER_NAMES ;
# helper_sizes : (nom, octets) de chacun d'eux, dans le même ordre.
MemoryMeasure = namedtuple("MemoryMeasure", "app helpers ram helper_names helper_sizes",
                           defaults=((), ()))

# Programmes annexes, dans leur ordre d'affichage. Noms de programmes, jamais
# traduits.
HELPER_NAMES = ("PDF", "7-Zip", "UnRAR")


def helper_kind(image_path, own_executables):
    """Nom du programme annexe (HELPER_NAMES) dont l'exe est image_path, None
    pour un programme qui n'en est pas un. own_executables : exes de
    MosaicView, qui se relance lui-même pour lire les PDF (process séparé)."""
    if not image_path:
        return None
    name = os.path.basename(image_path).lower()
    if name == "7z.exe":
        return "7-Zip"
    if name == "unrar.exe":
        return "UnRAR"
    normalized = os.path.normcase(os.path.abspath(image_path))
    if any(exe and normalized == os.path.normcase(os.path.abspath(exe))
           for exe in own_executables):
        return "PDF"
    return None


def memory_level(share):
    """« green », « orange » ou « red » pour un pourcentage entier de la
    mémoire du PC (percent : 0 à 100, ou plus)."""
    if share >= RED_FROM:
        return "red"
    if share >= ORANGE_FROM:
        return "orange"
    return "green"


def percent(size, ram):
    """Part de `ram` occupée par `size`, en pourcentage entier arrondi."""
    return round(size * 100 / ram) if ram else 0


# ---------------------------------------------------------------------------
# Estimation d'un panneau
# ---------------------------------------------------------------------------

def _pil_size(img):
    """Mémoire des pixels décodés d'une image Pillow : un octet par pixel en
    1 bit, gris ou palette, deux en gris 16 bits, quatre en entier ou flottant
    32 bits et pour toute image de plusieurs canaux (stockés sur 4 octets)."""
    width, height = img.size
    mode = img.mode
    if mode in ("1", "L", "P"):
        per_pixel = 1
    elif mode.startswith("I;16"):
        per_pixel = 2
    else:
        per_pixel = 4
    return width * height * per_pixel


def _qt_size(obj):
    """Mémoire d'une QImage ou d'une QPixmap, reconnues sans importer Qt."""
    size_in_bytes = getattr(obj, "sizeInBytes", None)
    if callable(size_in_bytes):
        return size_in_bytes()
    return obj.width() * obj.height() * obj.depth() // 8


def _value_size(value):
    """Taille d'une valeur d'une entrée : octets d'un fichier, image Pillow,
    image Qt ; 0 pour le reste (noms, nombres, widgets…)."""
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, Image.Image):
        return _pil_size(value)
    if callable(getattr(value, "depth", None)) and callable(getattr(value, "width", None)):
        return _qt_size(value)
    return 0


def reading_started(state, pages):
    """Inscrit `pages`, liste que remplit le worker d'un chargement de ce
    panneau (ouverture ou ajout d'une archive, d'un PDF, d'images) : ses pages
    sont comptées par panel_memory_estimate avant d'arriver dans le document,
    où le worker ne les remet qu'à la fin. Appelée sur le thread Qt au
    démarrage du worker, et retirée par reading_finished sur tous ses chemins
    de fin (pages remises au document ou abandonnées). Une inscription faite
    pour un document fermé ou remplacé depuis (doc_generation) n'est plus
    comptée, et disparaît à l'inscription suivante."""
    state.pages_being_read = [(generation, listed)
                              for generation, listed in state.pages_being_read
                              if generation == state.doc_generation]
    state.pages_being_read.append((state.doc_generation, pages))


def reading_finished(state, pages):
    """Retire `pages` (reading_started). Sans effet si elle n'est pas
    inscrite."""
    state.pages_being_read = [(generation, listed)
                              for generation, listed in state.pages_being_read
                              if listed is not pages]


def _pages_being_read(state):
    """Pages déjà lues par les chargements en cours de ce panneau, lues sur le
    thread Qt pendant que leurs workers en ajoutent : la liste est copiée
    (list() la parcourt d'un seul tenant), et chaque page n'est ajoutée par
    son worker qu'une fois complète. Une page qui changerait pendant sa
    lecture n'est pas comptée cette fois-ci (repli : estimation un peu basse
    jusqu'à la mesure suivante)."""
    values = []
    for generation, pages in state.pages_being_read:
        if generation != state.doc_generation:
            continue
        for entry in list(pages):
            try:
                entry_values = list(entry.values())
            except RuntimeError:
                continue  # page modifiée pendant sa lecture : comptée à la mesure suivante
            values.extend(entry_values)
    return values


def panel_memory_estimate(state):
    """Mémoire estimée d'un panneau, en octets : pages du document et de son
    historique d'annulations, images décodées et vignettes, et pages déjà
    lues par un chargement en cours (reading_started). Chaque objet n'est
    compté qu'une fois : l'historique partage les octets des pages qui n'ont
    pas changé avec le document, et les pages d'un chargement qui vient de se
    terminer sont à la fois dans le document et dans sa liste jusqu'à son
    retrait."""
    seen = set()
    total = 0

    def add(value):
        nonlocal total
        if id(value) in seen:
            return
        size = _value_size(value)
        if size:
            seen.add(id(value))
            total += size

    for entry in state.images_data:
        for value in entry.values():
            add(value)
    for saved in state.history:
        for entry_copy in saved["entries"]:
            for value in entry_copy.values():
                if isinstance(value, dict):      # propriétés de la page (« meta »)
                    for meta_value in value.values():
                        add(meta_value)
                else:
                    add(value)
    for value in _pages_being_read(state):
        add(value)
    return total


# ---------------------------------------------------------------------------
# Process de MosaicView
# ---------------------------------------------------------------------------

def _descendants(root_pid, root_created, parents, created_of, kind_of):
    """Programmes annexes lancés par root_pid, directement ou non : liste de
    (pid, nom du programme annexe).
    parents : {pid: pid du parent} de tous les process de Windows.
    created_of(pid) : date de création du process, None s'il a disparu.
    kind_of(pid) : nom du programme annexe (helper_kind), None pour un autre
    programme, qui n'est pas compté, ni ce qu'il a lancé à son tour.
    Windows réattribue le numéro d'un process terminé : un process dont le
    parent annoncé a été créé après lui n'est pas son enfant (son vrai parent
    s'est terminé, et un autre process a reçu son numéro)."""
    children_of = {}
    for pid, parent in parents.items():
        if pid != parent:
            children_of.setdefault(parent, []).append(pid)
    found = []
    pending = [(root_pid, root_created)]
    visited = {root_pid}
    while pending:
        parent, parent_created = pending.pop()
        for pid in children_of.get(parent, ()):
            if pid in visited:
                continue
            created = created_of(pid)
            if created is None or created < parent_created:
                continue
            visited.add(pid)
            kind = kind_of(pid)
            if kind is None:
                continue
            found.append((pid, kind))
            pending.append((pid, created))
    return found


def _is_onefile(frozen, executable, bundle_dir):
    """Version « un seul fichier » de l'exe : PyInstaller extrait
    l'application dans un dossier temporaire (sys._MEIPASS), hors du dossier
    de l'exe ; dans la version dossier, sys._MEIPASS est le dossier de l'exe
    ou son sous-dossier _internal."""
    if not frozen or not bundle_dir:
        return False
    exe_dir = os.path.normcase(os.path.abspath(os.path.dirname(executable)))
    bundle = os.path.normcase(os.path.abspath(bundle_dir))
    try:
        return os.path.commonpath([exe_dir, bundle]) != exe_dir
    except ValueError:
        return True       # lecteurs différents : forcément hors du dossier de l'exe


def _is_launcher(parent_path, parent_created, executable, my_created):
    """Le parent de MosaicView est son programme de démarrage : même exe, et
    créé avant lui (sinon son numéro a été réattribué à un autre process)."""
    if not parent_path or parent_created is None or parent_created > my_created:
        return False
    return (os.path.normcase(os.path.abspath(parent_path))
            == os.path.normcase(os.path.abspath(executable)))


def _windows_api():
    """Fonctions de Windows employées par measure(), déclarées une fois."""
    import ctypes
    from ctypes import wintypes

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.K32GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
    k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k32.GetProcessTimes.restype = wintypes.BOOL
    k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    k32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
    k32.GlobalMemoryStatusEx.restype = wintypes.BOOL
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32FirstW.restype = wintypes.BOOL
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.restype = wintypes.BOOL
    return ctypes, wintypes, k32, PROCESS_MEMORY_COUNTERS, MEMORYSTATUSEX, PROCESSENTRY32W


_API = None

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TH32CS_SNAPPROCESS = 0x00000002


def _api():
    global _API
    if _API is None:
        _API = _windows_api()
    return _API


def _working_set(handle):
    ctypes, _wt, k32, PMC, _ms, _pe = _api()
    counters = PMC()
    counters.cb = ctypes.sizeof(PMC)
    if not k32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        return None
    return counters.WorkingSetSize


def _creation_time(handle):
    ctypes, wintypes, k32, _pmc, _ms, _pe = _api()
    times = [wintypes.FILETIME() for _i in range(4)]
    if not k32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
        return None
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def _image_path(handle):
    ctypes, wintypes, k32, _pmc, _ms, _pe = _api()
    size = wintypes.DWORD(32768)
    buf = ctypes.create_unicode_buffer(size.value)
    if not k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
        return None
    return buf.value


class _OpenedProcesses:
    """Handles des process consultés pendant une mesure, fermés à la fin."""

    def __init__(self):
        self._handles = {}

    def get(self, pid):
        if pid not in self._handles:
            _c, _w, k32, _pmc, _ms, _pe = _api()
            self._handles[pid] = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION,
                                                 False, pid)
        return self._handles[pid]

    def close(self):
        _c, _w, k32, _pmc, _ms, _pe = _api()
        for handle in self._handles.values():
            if handle:
                k32.CloseHandle(handle)
        self._handles.clear()


def _process_parents():
    """{pid: pid du parent} de tous les process de Windows."""
    ctypes, _w, k32, _pmc, _ms, PE = _api()
    snapshot = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot is None or snapshot == ctypes.c_void_p(-1).value:
        raise OSError("CreateToolhelp32Snapshot")
    try:
        parents = {}
        entry = PE()
        entry.dwSize = ctypes.sizeof(PE)
        ok = k32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            ok = k32.Process32NextW(snapshot, ctypes.byref(entry))
        return parents
    finally:
        k32.CloseHandle(snapshot)


def measure():
    """Mesure actuelle (MemoryMeasure), ou None si Windows ne la donne pas
    (l'indicateur affiche alors un tiret, sans message)."""
    try:
        return _measure()
    except (OSError, AttributeError, ValueError):
        # repli : indicateur sans valeur ; mesure retentée au tour suivant
        return None


def _measure():
    ctypes, _w, k32, _pmc, MS, _pe = _api()
    status = MS()
    status.dwLength = ctypes.sizeof(MS)
    if not k32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("GlobalMemoryStatusEx")

    me = k32.GetCurrentProcess()
    app = _working_set(me)
    my_created = _creation_time(me)
    if app is None or my_created is None:
        raise OSError("GetProcessMemoryInfo")

    my_pid = os.getpid()
    parents = _process_parents()
    opened = _OpenedProcesses()
    try:
        def created_of(pid):
            handle = opened.get(pid)
            return _creation_time(handle) if handle else None

        # Dans un environnement virtuel, le python.exe de l'environnement
        # relance celui de l'installation de Python (_base_executable).
        own_executables = (sys.executable, getattr(sys, "_base_executable", None))

        def kind_of(pid):
            handle = opened.get(pid)
            return helper_kind(_image_path(handle) if handle else None, own_executables)

        by_kind = {}
        for pid, kind in _descendants(my_pid, my_created, parents, created_of, kind_of):
            size = _working_set(opened.get(pid))
            if size:                  # process terminé entre-temps : rien à compter
                by_kind[kind] = by_kind.get(kind, 0) + size

        if _is_onefile(getattr(sys, "frozen", False), sys.executable,
                       getattr(sys, "_MEIPASS", None)):
            launcher = parents.get(my_pid)
            handle = opened.get(launcher) if launcher else None
            if handle and _is_launcher(_image_path(handle), _creation_time(handle),
                                       sys.executable, my_created):
                app += _working_set(handle) or 0
    finally:
        opened.close()
    return _with_helpers(MemoryMeasure(app, 0, status.ullTotalPhys), by_kind)


def _with_helpers(measure, by_kind):
    """`measure` avec les programmes annexes de `by_kind` ({nom: octets}) :
    total, noms et tailles dans l'ordre de HELPER_NAMES."""
    names = tuple(name for name in HELPER_NAMES if name in by_kind)
    return measure._replace(helpers=sum(by_kind[name] for name in names),
                            helper_names=names,
                            helper_sizes=tuple((name, by_kind[name]) for name in names))


# ---------------------------------------------------------------------------
# Programmes annexes maintenus entre deux mesures
# ---------------------------------------------------------------------------

# Durée, en secondes, pendant laquelle un programme annexe reste compté après
# la dernière mesure qui l'a vu tourner.
HELPER_HOLD_SECONDS = 3.0


class HelperHold:
    """Dernière mémoire de chaque programme annexe, gardée HELPER_HOLD_SECONDS.

    Un programme annexe ne vit souvent que le temps d'un fichier : rarfile
    lance un UnRAR par page lue, _read_7z_file un 7-Zip par membre. Une mesure
    (toutes les 250 ms, MainWindow._sample_memory) tombe tantôt pendant l'un
    d'eux, tantôt entre deux, et l'indicateur montrerait puis retirerait leur
    mémoire sans cesse.
    Une mesure qui voit un programme remplace sa valeur et relance son délai ;
    une mesure qui ne le voit pas garde sa dernière valeur jusqu'à
    l'expiration du délai. Chaque programme a son propre délai. forget() les
    oublie tous : à la fin d'un chargement ou d'un lot, qui ne lancera plus
    rien, la mesure suivante montre seulement ce qui tourne encore. Délai
    compté en temps réel (`now` : time.monotonic()), pas en nombre de
    mesures : suspendues tant que la fenêtre est réduite, elles ne font pas
    revenir une valeur périmée à son retour. Verrou : forget() est aussi
    appelée depuis le thread d'un lot."""

    def __init__(self, seconds=HELPER_HOLD_SECONDS):
        self._seconds = seconds
        self._lock = threading.Lock()
        self._last = {}           # nom du programme annexe → (octets, instant vu)

    def forget(self):
        with self._lock:
            self._last.clear()

    def apply(self, measure, now):
        """`measure` (MemoryMeasure, ou None) avec les programmes annexes
        encore dans leur délai."""
        if measure is None:
            return None
        with self._lock:
            for name, size in measure.helper_sizes:
                self._last[name] = (size, now)
            for name, (_size, seen) in list(self._last.items()):
                if now - seen > self._seconds:
                    del self._last[name]
            by_kind = {name: size for name, (size, _seen) in self._last.items()}
        return _with_helpers(measure, by_kind)


# Une seule mesure pour toute l'application (MainWindow._sample_memory et
# _refresh_memory_indicators), donc un seul maintien.
_helper_hold = HelperHold()


def hold_helper_programs(measure, now):
    """Mesure montrée par l'indicateur : programmes annexes maintenus
    (HelperHold.apply)."""
    return _helper_hold.apply(measure, now)


def forget_helper_programs():
    """Fin d'un lot : les programmes annexes ne sont plus maintenus, la
    mesure suivante ne compte que ceux qui tournent encore. Utilisable depuis
    n'importe quel thread."""
    _helper_hold.forget()


# Mesure immédiate de l'indicateur (MainWindow._refresh_memory_indicators),
# posée par la fenêtre principale au démarrage.
_measure_now = None


def set_measure_now(refresh):
    global _measure_now
    _measure_now = refresh


def helper_programs_finished():
    """Fin d'un chargement ou d'un ajout dans un panneau (thread Qt seulement) :
    ses programmes annexes (UnRAR, 7-Zip, process PDF) sont terminés. Ils ne
    sont plus maintenus et la mémoire est mesurée tout de suite, sans
    attendre la mesure suivante. Un programme annexe d'une autre opération
    encore en cours (autre panneau, lot) revient dès qu'une mesure le voit."""
    _helper_hold.forget()
    if _measure_now is not None:
        try:
            _measure_now()
        except RuntimeError:
            pass  # sans conséquence : fenêtre principale détruite, application en cours de fermeture
