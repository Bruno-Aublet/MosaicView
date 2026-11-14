"""
modules/qt/process_owner.py — Compte Windows propriétaire d'un process.

Sert à l'instance unique (single_instance_qt) : le canal nommé par lequel un
second lancement transmet son fichier à la fenêtre déjà ouverte est visible
de tous les comptes du PC. Un autre compte connecté au même moment pourrait le
créer le premier et recevoir les chemins des fichiers ouverts. Avant d'envoyer
quoi que ce soit, le lancement vérifie que le process qui tient le canal
appartient à son propre compte.

Module sans Qt, en ctypes. Les fonctions Windows sont prises dans des
bibliothèques chargées ici (ctypes.WinDLL) : déclarer leurs types sur
ctypes.windll, partagé par tout le programme, changerait ceux des autres
appels de ces mêmes fonctions.
"""
import ctypes
from ctypes import wintypes

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1          # TOKEN_INFORMATION_CLASS.TokenUser

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.GetCurrentProcess.argtypes = []
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_advapi32.OpenProcessToken.restype = wintypes.BOOL
_advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.HANDLE)]
_advapi32.GetTokenInformation.restype = wintypes.BOOL
_advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                          wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_advapi32.GetLengthSid.restype = wintypes.DWORD
_advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]


def _token_user_sid(process_handle):
    """SID (octets) du compte propriétaire du process `process_handle`, None
    si son jeton ne peut pas être lu."""
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(process_handle, _TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        size = wintypes.DWORD()
        _advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
        if not size.value:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if not _advapi32.GetTokenInformation(token, _TOKEN_USER, buffer, size,
                                             ctypes.byref(size)):
            return None
        # TOKEN_USER commence par SID_AND_ATTRIBUTES : un pointeur vers le SID
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        return ctypes.string_at(sid, _advapi32.GetLengthSid(sid))
    finally:
        _kernel32.CloseHandle(token)


def process_belongs_to_current_user(pid):
    """True si le process `pid` appartient au compte Windows du process
    courant. False pour un process d'un autre compte, introuvable, ou qu'on
    n'a pas le droit d'interroger (un process d'un autre compte ne l'est
    normalement pas) : dans le doute, la réponse est « non »."""
    if not pid:
        return False
    try:
        current = _token_user_sid(_kernel32.GetCurrentProcess())
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            other = _token_user_sid(handle)
        finally:
            _kernel32.CloseHandle(handle)
        return current is not None and other == current
    except OSError:
        return False   # repli : propriétaire inconnu, traité comme un autre compte
