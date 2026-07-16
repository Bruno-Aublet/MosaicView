# ═══════════════════════════════════════════════════════════════════════════════
# Déclaration silencieuse de l'application dans le registre (HKCU)
#
# Objectif unique : que MosaicView apparaisse dans la liste « Ouvrir avec »
# de l'Explorateur Windows. On écrit exactement la clé que Windows crée
# lui-même quand l'utilisateur choisit un exe via « Ouvrir avec » :
#
#   HKCU\Software\Classes\Applications\MosaicView.exe\shell\open\command
#     (default) = "<chemin de l'exe>" "%1"
#
# Plus deux compléments sur la même branche : FriendlyAppName (affiche
# « MosaicView » au lieu de « MosaicView.exe » dans la liste) et
# SupportedTypes (liste des extensions pour lesquelles MosaicView est mis
# en avant dans « Ouvrir avec » — voir l'avertissement sur _SUPPORTED_EXTS).
#
# Rien d'autre : aucune association d'extension, aucun choix par défaut
# (UserChoice est verrouillé par Windows et n'est jamais touché ici).
# ═══════════════════════════════════════════════════════════════════════════════
import os
import sys

_APP_ROOT_KEY = r"Software\Classes\Applications\MosaicView.exe"
_APP_KEY = _APP_ROOT_KEY + r"\shell\open\command"
_SUPPORTED_KEY = _APP_ROOT_KEY + r"\SupportedTypes"
_FRIENDLY_NAME = "MosaicView"  # nom affiché dans « Ouvrir avec » (sans .exe)
# Types déclarés « pertinents » (mise en avant dans « Ouvrir avec »).
# ATTENTION : cette liste est exhaustive aux yeux de Windows — un type absent
# fait DISPARAÎTRE MosaicView du menu immédiat « Ouvrir avec » de ce type.
_SUPPORTED_EXTS = (
    ".mvdb",
    ".cbz", ".cbr", ".cb7", ".cbt", ".epub", ".pdf",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
    ".tiff", ".tif", ".ico", ".avif",
)


def ensure_app_registered():
    """Déclare l'exe courant dans le registre si nécessaire (idempotent).

    Ne fait rien hors mode compilé (PyInstaller). Silencieux en cas
    d'échec : ne doit jamais perturber le lancement de l'application."""
    try:
        if not getattr(sys, "frozen", False):
            return  # mode développement (script) : pas de déclaration

        exe = os.path.abspath(sys.executable)
        expected = f'"{exe}" "%1"'

        import winreg

        # Lecture de l'état actuel — si tout est déjà en place, rien à faire
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _APP_KEY) as key:
                current, _type = winreg.QueryValueEx(key, None)
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _APP_ROOT_KEY) as key:
                friendly, _type = winreg.QueryValueEx(key, "FriendlyAppName")
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _SUPPORTED_KEY) as key:
                for ext in _SUPPORTED_EXTS:
                    winreg.QueryValueEx(key, ext)  # présence seule requise
            if current == expected and friendly == _FRIENDLY_NAME:
                return
        except OSError:
            pass  # clé/valeur absente : on écrit ci-dessous

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _APP_KEY) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, expected)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _APP_ROOT_KEY) as key:
            winreg.SetValueEx(key, "FriendlyAppName", 0, winreg.REG_SZ, _FRIENDLY_NAME)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _SUPPORTED_KEY) as key:
            for ext in _SUPPORTED_EXTS:
                winreg.SetValueEx(key, ext, 0, winreg.REG_SZ, "")

        # Notifie l'Explorateur du changement (rafraîchit « Ouvrir avec »)
        try:
            import ctypes
            SHCNE_ASSOCCHANGED = 0x08000000
            SHCNF_IDLIST = 0x0000
            ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
        except Exception:
            pass
    except Exception:
        pass  # jamais bloquant : la déclaration est une commodité, pas un prérequis
