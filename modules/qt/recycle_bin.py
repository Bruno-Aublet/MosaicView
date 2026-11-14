"""Un fichier peut-il aller à la Corbeille de Windows ?

La mise à la Corbeille (utils.send_to_trash) demande à Windows de recycler le
fichier sans confirmation : quand c'est impossible, Windows le supprime
définitivement, sans rien dire. Les fenêtres qui proposent la Corbeille le
signalent d'avance (warning_text), dans trois cas :
- « no_bin » : lecteur sans Corbeille (clé USB, carte mémoire, CD, partage
  réseau) ;
- « disabled » : Corbeille désactivée sur ce disque (« Ne pas déplacer les
  fichiers vers la Corbeille ») ou par une stratégie de groupe ;
- « too_large » : fichier plus gros que la taille maximale de la Corbeille
  de ce disque.

Sans Qt. Une détection qui échoue ne signale rien (repli : la fenêtre reste
telle qu'elle était).
"""
import os

DRIVE_REMOVABLE = 2
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
_NO_BIN_DRIVE_TYPES = (DRIVE_REMOVABLE, DRIVE_REMOTE, DRIVE_CDROM)

_BITBUCKET = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket"
_POLICIES = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"


# ── Accès à Windows (remplacés par les tests) ────────────────────────────────

def _volume_of(path):
    """Racine du volume de `path` (« C:\\ », « \\\\serveur\\partage\\ », point de
    montage)."""
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    if not ctypes.windll.kernel32.GetVolumePathNameW(os.path.abspath(path), buf, 1024):
        raise OSError("GetVolumePathNameW", path)
    return buf.value


def _drive_type(volume):
    import ctypes
    return ctypes.windll.kernel32.GetDriveTypeW(volume)


def _has_recycle_bin(volume):
    """SHQueryRecycleBinW échoue sur un volume sans Corbeille."""
    import ctypes
    from ctypes import wintypes

    class _Info(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong),
                    ("i64NumItems", ctypes.c_longlong)]

    info = _Info()
    info.cbSize = ctypes.sizeof(_Info)
    return ctypes.windll.shell32.SHQueryRecycleBinW(volume, ctypes.byref(info)) == 0


def _registry_value(root, key, name):
    import winreg
    try:
        with winreg.OpenKey(root, key) as handle:
            return winreg.QueryValueEx(handle, name)[0]
    except OSError:
        return None


def _volume_settings(volume):
    """(NukeOnDelete, MaxCapacity en Mo) de la Corbeille de ce volume, réglés
    par l'utilisateur dans ses propriétés ; None pour une valeur absente."""
    import ctypes
    import re
    import winreg
    buf = ctypes.create_unicode_buffer(64)
    if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(volume, buf, 64):
        return None, None
    match = re.search(r"\{[0-9A-Fa-f-]+\}", buf.value)
    if not match:
        return None, None
    key = _BITBUCKET + "\\Volume\\" + match.group(0)
    nuke = _registry_value(winreg.HKEY_CURRENT_USER, key, "NukeOnDelete")
    if nuke is None:
        nuke = _registry_value(winreg.HKEY_CURRENT_USER, _BITBUCKET, "NukeOnDelete")
    return nuke, _registry_value(winreg.HKEY_CURRENT_USER, key, "MaxCapacity")


def _policy_disabled():
    """Stratégie « Ne pas déplacer les fichiers supprimés vers la Corbeille »."""
    import winreg
    return any(_registry_value(root, _POLICIES, "NoRecycleFiles") == 1
               for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE))


# ── Détection ────────────────────────────────────────────────────────────────

def _volume_problem(volume):
    """("no_bin",), ("disabled",) ou (None, MaxCapacity en Mo) pour un volume."""
    if volume.startswith("\\\\") or _drive_type(volume) in _NO_BIN_DRIVE_TYPES \
            or not _has_recycle_bin(volume):
        return ("no_bin",)
    if _policy_disabled():
        return ("disabled",)
    nuke, max_mb = _volume_settings(volume)
    if nuke == 1:
        return ("disabled",)
    return (None, max_mb)


def trash_problem(path, _cache=None):
    """Raison pour laquelle `path` ne pourra pas aller à la Corbeille : ("no_bin",),
    ("disabled",), ("too_large", taille, maximum) en octets, ou None s'il le
    peut (ou si la détection échoue). `_cache` : {volume: résultat} partagé
    entre plusieurs appels (trash_problem_count)."""
    try:
        volume = _volume_of(path)
        if _cache is not None and volume in _cache:
            found = _cache[volume]
        else:
            found = _volume_problem(volume)
            if _cache is not None:
                _cache[volume] = found
        if found[0] is not None:
            return found
        max_mb = found[1]
        if max_mb is not None and os.path.isfile(path):
            size = os.path.getsize(path)
            maximum = int(max_mb) * 1024 * 1024
            if size > maximum:
                return ("too_large", size, maximum)
        return None
    except Exception:
        return None  # repli : détection impossible, aucun avertissement


def trash_problem_count(paths):
    """Nombre de fichiers de `paths` qui ne pourront pas aller à la Corbeille."""
    cache = {}
    return sum(1 for p in paths if trash_problem(p, cache) is not None)


def warning_text(problem):
    """Avertissement traduit pour un résultat de trash_problem ("" pour None).
    Appelée à l'affichage (retraduction) : la taille est écrite dans la langue
    du moment."""
    if not problem:
        return ""
    from modules.qt.localization import _
    if problem[0] == "no_bin":
        return _("messages.trash.permanent_no_bin")
    if problem[0] == "disabled":
        return _("messages.trash.permanent_disabled")
    from modules.qt.utils import format_file_size
    return _("messages.trash.permanent_too_large",
             size=format_file_size(problem[1]), max_size=format_file_size(problem[2]))
