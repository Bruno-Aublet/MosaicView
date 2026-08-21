"""
open_with_default_app_qt.py — Ouvre un fichier non-image avec l'application
Windows associée à son extension.

Logique :
  - Extrait entry["bytes"] dans MosaicViewTemp/<orig_name>
  - Appelle os.startfile() → Windows dispatche vers l'app déclarée pour l'extension
  - Lance un thread de surveillance : si le contenu du fichier temp change par rapport
    aux bytes originaux, met à jour entry["bytes"], state.modified = True, et appelle
    on_modified_callback() pour rafraîchir l'UI.
    La comparaison se fait sur le contenu (MD5) et non sur le mtime, car certaines
    apps touchent le mtime à l'ouverture sans modifier le contenu.
"""

import hashlib
import os
import threading
import time

from modules.qt.temp_files import get_mosaicview_temp_dir

# Intervalle de polling (secondes)
_POLL_INTERVAL = 1.0
# Durée max de surveillance (secondes) — arrêt automatique après 1 heure
_WATCH_TIMEOUT = 3600

# Extensions pour lesquelles os.startfile() EXÉCUTE le fichier au lieu de
# l'afficher (scripts, binaires, raccourcis…). Un fichier piégé dans une
# archive téléchargée serait lancé tel quel — on refuse de l'ouvrir.
_EXECUTABLE_EXTS = frozenset({
    ".exe", ".com", ".scr", ".pif", ".bat", ".cmd", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".ws", ".msi", ".msp", ".mst",
    ".hta", ".cpl", ".jar", ".lnk", ".url", ".scf", ".reg", ".inf",
    ".msc", ".vb", ".sct", ".gadget", ".application", ".appref-ms",
    ".ps1", ".psm1", ".psd1", ".psc1", ".diagcab",
    # Aide compilée (peut exécuter du code) et scripts Python (exécutés si
    # Python est installé sur la machine)
    ".chm", ".py", ".pyw", ".pyz", ".pyzw",
    # Images disque montées automatiquement par Windows au double-clic
    ".iso", ".img", ".vhd", ".vhdx",
    # Raccourci spécial Windows (même famille que .lnk/.scf)
    ".desklink",
    # Add-ins Excel (code natif ou VBA exécuté au chargement)
    ".xll", ".xlam", ".xla",
    # Fichiers de recherche/paramètres/bibliothèque Windows (lancement de
    # commandes via leur contenu XML, ou fuite de hash NTLM via chemin UNC)
    ".settingcontent-ms", ".search-ms", ".library-ms", ".searchconnector-ms",
    # Scripts Monad/MSH (ancêtre de PowerShell, encore associés sur
    # certaines configurations) + compléments de la famille PowerShell
    ".msh", ".msh1", ".msh2", ".mshxml", ".msh1xml", ".msh2xml",
    ".ps1xml", ".ps2", ".ps2xml", ".psc2", ".cdxml",
    # Raccourcis Internet (même famille que .url)
    ".website",
    # Composant Windows Script (jumeau de .sct)
    ".wsc",
    # Lanceur Java Web Start (même famille que .jar)
    ".jnlp",
    # Paquets d'installation d'applications (App Installer)
    ".appx", ".msix", ".appxbundle", ".msixbundle", ".appinstaller",
    # Configuration Windows Sandbox (exécute sa LogonCommand au double-clic)
    ".wsb",
    # Connexion Bureau à distance automatique (redirection de disques possible)
    ".rdp",
    # Requêtes web Excel (téléchargement et exécution de contenu distant)
    ".iqy", ".dqy", ".oqy", ".rqy",
    # Scripts de configuration Internet (blocklist Outlook historique)
    ".ins", ".isp",
    # Thèmes Windows (peuvent désigner un .scr arbitraire ou une ressource UNC)
    ".theme", ".themepack", ".deskthemepack",
    # Vecteurs anciens : WinHelp (famille .chm), scrap objects, XAML Browser App
    ".hlp", ".shs", ".shb", ".xbap",
    # Scripts d'interpréteurs tiers associés à l'exécution par leurs installeurs
    # (AutoHotkey, AutoIt, Perl, Ruby, Tcl) — même logique que .py
    ".ahk", ".au3", ".pl", ".rb", ".tcl",
    # Documents Office à macros / formats exécutant du code à l'ouverture
    ".docm", ".dotm", ".xlsm", ".xltm", ".pptm", ".ppam", ".ppa",
    ".ppsm", ".potm", ".sldm", ".slk",
    # Anciens formats Office binaires (macros possibles sans que l'extension
    # le signale)
    ".doc", ".dot", ".xls", ".xlt", ".ppt", ".pot", ".pps",
    # Formats Office modernes sans macros — bloqués par principe : un document
    # bureautique n'a rien à faire dans une archive de BD, et un .docx peut
    # servir de vecteur d'exploit contre Office lui-même (ex. Follina, 2022)
    ".docx", ".dotx", ".xlsx", ".xltx", ".pptx", ".potx", ".ppsx", ".sldx",
    # Bases et raccourcis Access (macro AutoExec exécutée à l'ouverture)
    ".mdb", ".accdb", ".mde", ".accde", ".ade", ".adp",
    ".mam", ".maq", ".mar", ".mat", ".maf",
})


def _md5(data: bytes) -> bytes:
    return hashlib.md5(data, usedforsecurity=False).digest()


def open_file_with_default_app(
    entry: dict,
    state=None,
    on_modified_callback=None,
    parent=None,
) -> None:
    """
    Extrait le fichier de l'archive vers le dossier temporaire MosaicView,
    puis l'ouvre avec l'application Windows par défaut pour son extension.

    Si state et on_modified_callback sont fournis (tous deux optionnels),
    surveille le fichier temporaire : toute modification du contenu est
    répercutée dans entry["bytes"] et state.modified est mis à True, puis
    on_modified_callback() est appelé dans le thread Qt principal.
    """
    raw: bytes | None = entry.get("bytes")
    if not raw:
        return

    orig_name: str = entry.get("orig_name", "file")

    # Sécurité : refuse les types de fichiers que Windows exécuterait via
    # os.startfile() (l'utilisateur s'attend à "voir" le fichier, pas à le lancer).
    ext = os.path.splitext(orig_name)[1].lower()
    if ext in _EXECUTABLE_EXTS:
        _warn_executable_file(parent, ext)
        return

    # orig_name peut contenir des sous-dossiers (ex. "sub/image.txt") — on garde
    # la structure pour éviter les collisions de noms.
    mosaicview_temp = get_mosaicview_temp_dir()

    # Sécurité : empêche toute écriture hors du dossier temp (nom d'entrée piégé
    # "../" — Zip Slip). Le fichier est ensuite ouvert via os.startfile() ; un
    # chemin évadé pourrait déposer/lancer un fichier ailleurs. On refuse.
    from modules.qt.utils import safe_join
    tmp_path = safe_join(mosaicview_temp, orig_name)
    if tmp_path is None:
        _warn_unsafe_path(parent)
        return

    parent_dir = os.path.dirname(tmp_path)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    with open(tmp_path, "wb") as f:
        f.write(raw)

    os.startfile(tmp_path)

    if state is not None and on_modified_callback is not None:
        _start_watch_thread(tmp_path, raw, entry, state, on_modified_callback)


def _start_watch_thread(
    tmp_path: str,
    original_bytes: bytes,
    entry: dict,
    state,
    on_modified_callback,
) -> None:
    """Lance un thread daemon qui surveille tmp_path par comparaison de contenu."""

    # Utilise une liste pour muter le hash de référence depuis la closure
    # (permet de détecter plusieurs sauvegardes successives dans la même session)
    current_hash = [_md5(original_bytes)]

    def _watch():
        try:
            last_mtime = os.path.getmtime(tmp_path)
        except OSError:
            return

        deadline = time.monotonic() + _WATCH_TIMEOUT

        while time.monotonic() < deadline:
            time.sleep(_POLL_INTERVAL)
            try:
                current_mtime = os.path.getmtime(tmp_path)
            except OSError:
                # Fichier supprimé — arrêt de la surveillance
                break

            if current_mtime == last_mtime:
                continue

            last_mtime = current_mtime
            # Attendre brièvement que l'app ait fini d'écrire
            time.sleep(0.3)
            try:
                with open(tmp_path, "rb") as f:
                    new_bytes = f.read()
            except OSError:
                continue

            # Comparer le contenu, pas juste le mtime : certaines apps touchent
            # le mtime à l'ouverture sans modifier le contenu.
            new_hash = _md5(new_bytes)
            if new_hash == current_hash[0]:
                continue

            current_hash[0] = new_hash

            # Émet le signal Qt — traversée thread-safe vers le thread principal
            on_modified_callback(new_bytes)

    t = threading.Thread(target=_watch, daemon=True, name="NonImageFileWatcher")
    t.start()


def _warn_executable_file(parent, ext):
    """Avertit (fenêtre non-modale) que le fichier n'a pas été ouvert car son
    extension correspond à un type exécutable par Windows."""
    from modules.qt.localization import _, _wt
    from modules.qt.dialogs_qt import InfoDialog
    dlg = InfoDialog(
        parent,
        lambda: _wt("messages.warnings.executable_file_open.title"),
        lambda: _("messages.warnings.executable_file_open.message").replace("{ext}", ext),
    )
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


def _warn_unsafe_path(parent):
    """Avertit (fenêtre non-modale) que le fichier n'a pas pu être ouvert car
    son nom tentait d'écrire hors du dossier temporaire (chemin non valide)."""
    from modules.qt.localization import _, _wt
    from modules.qt.dialogs_qt import InfoDialog
    dlg = InfoDialog(
        parent,
        lambda: _wt("messages.warnings.unsafe_path_open.title"),
        lambda: _("messages.warnings.unsafe_path_open.message"),
    )
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
