"""
modules/qt/helper_processes.py — Programmes annexes (7z, UnRAR) jamais laissés
en vie après MosaicView, sans Qt.

Sous Windows, un programme lancé ne meurt pas avec celui qui l'a lancé. Si
MosaicView se termine pendant qu'un 7z ou un UnRAR travaille (fermeture qui
finit par os._exit, plantage, fin forcée par le Gestionnaire des tâches), ce
programme continuerait seul, sans plus personne pour surveiller ce qu'il
écrit : une extraction de CBR piégé (archive_members._RarExtraction)
remplirait le disque.

Chaque programme annexe est donc rattaché, juste après son lancement, à un
objet job de Windows réglé pour tuer tous ses programmes à sa fermeture
(JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE). Seul MosaicView tient ce job ouvert,
jusqu'à sa fin, quelle qu'elle soit : Windows le ferme alors, et ses
programmes meurent avec lui. MosaicView lui-même n'y est jamais rattaché : ce
qu'il ouvre exprès pour l'utilisateur (application par défaut, Explorateur,
Bloc-notes) doit lui survivre.

start_helper(cmd, **popen_kwargs) remplace subprocess.Popen pour un programme
annexe ; keep_rarfile_helpers_with_app() fait de même pour les UnRAR que
rarfile lance lui-même (rarfile.custom_popen). Un rattachement impossible
(autre système que Windows, Windows trop ancien pour les jobs imbriqués)
laisse le programme tel qu'il serait sans ce module.
"""
import subprocess
import sys
import threading

# JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, classe JobObjectExtendedLimitInformation
_KILL_ON_JOB_CLOSE = 0x2000
_EXTENDED_LIMIT_INFORMATION = 9
# Droits demandés sur le programme lancé : rattachement à un job
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001

_lock = threading.Lock()
# Job unique de la session, créé au premier programme annexe : (kernel32,
# poignée), ou False si sa création a échoué. Sa poignée n'est jamais
# refermée : c'est sa fermeture par Windows, à la fin de MosaicView, qui tue
# les programmes rattachés.
_job = None


def _kernel32_and_job():
    """(kernel32, poignée du job), créés une seule fois ; None hors de Windows
    ou si le job ne peut pas être créé."""
    global _job
    if sys.platform != "win32":
        return None
    with _lock:
        if _job is None:
            _job = _create_job() or False
        return _job or None


def _create_job():
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Io(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            wintypes.DWORD)
    k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    # Poignée non héritable (attributs par défaut) : un programme lancé ne la
    # recevra jamais, sans quoi il garderait le job ouvert après MosaicView
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = Extended()
    info.Basic.LimitFlags = _KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
                                       ctypes.sizeof(info)):
        k32.CloseHandle(job)
        return None
    return k32, job


def keep_with_app(pid):
    """Rattache le programme `pid` au job de la session : il mourra au plus
    tard avec MosaicView. Rend False si c'est impossible (le programme reste
    alors tel quel)."""
    found = _kernel32_and_job()
    if found is None:
        return False
    k32, job = found
    process = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
    if not process:
        return False                 # programme déjà terminé
    try:
        return bool(k32.AssignProcessToJobObject(job, process))
    finally:
        k32.CloseHandle(process)


def start_helper(cmd, **popen_kwargs):
    """subprocess.Popen(cmd, **popen_kwargs) pour un programme annexe (7z,
    UnRAR), rattaché aussitôt au job de la session (keep_with_app). 7z et
    UnRAR ne lancent eux-mêmes aucun programme : l'instant entre le lancement
    et le rattachement est sans conséquence. Le numéro du programme désigne
    bien lui : le Popen garde une poignée ouverte sur lui, et Windows ne
    réattribue jamais le numéro d'un processus tant qu'une poignée existe."""
    proc = subprocess.Popen(cmd, **popen_kwargs)
    keep_with_app(proc.pid)
    return proc


def keep_rarfile_helpers_with_app():
    """Fait passer par keep_with_app les UnRAR que rarfile lance lui-même
    (lecture d'un membre, test de l'outil) : rarfile les crée tous par sa
    fonction de module custom_popen, appelée par son nom à chaque fois.
    Sans effet si elle est déjà remplacée."""
    import rarfile
    original = rarfile.custom_popen
    if getattr(original, "_keeps_helpers_with_app", False):
        return

    def custom_popen(cmd):
        proc = original(cmd)
        keep_with_app(proc.pid)
        return proc

    custom_popen._keeps_helpers_with_app = True
    rarfile.custom_popen = custom_popen
