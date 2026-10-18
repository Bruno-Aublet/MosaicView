"""
open_with_default_app_qt.py — Ouvre un fichier non-image avec l'application
Windows associée à son extension.

Logique :
  - Refuse tout type absent de _OPENABLE_EXTS (liste blanche)
  - Extrait entry["bytes"] dans MosaicViewTemp/<orig_name rendu valide sous Windows>
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

# Seules extensions ouvertes avec l'application Windows par défaut : texte,
# PDF, sons, vidéos et images que MosaicView n'affiche pas. Une liste blanche
# et non une liste de types dangereux : celle-ci est toujours incomplète (un
# .pyc est exécuté si Python est installé, un .rtf a servi à attaquer Office,
# un .svg ou un .html exécute un script dans le navigateur), et ce qui
# s'exécute dépend des logiciels installés sur chaque machine. Tout autre
# type est refusé.
_OPENABLE_EXTS = frozenset({
    # Texte
    ".txt", ".diz", ".md", ".xml", ".json", ".csv", ".log", ".ini", ".cfg",
    ".sfv", ".md5", ".sha1", ".sha256", ".srt", ".ass", ".ssa", ".vtt",
    # Documents
    ".pdf", ".epub",
    # Sons et vidéos
    ".mp3", ".ogg", ".oga", ".flac", ".wav", ".m4a", ".opus",
    ".mp4", ".m4v", ".mkv", ".avi", ".webm", ".mov",
    # Images que MosaicView n'affiche pas
    ".jxl", ".heic", ".heif", ".psd", ".tga", ".jp2", ".dds", ".exr", ".hdr",
})


def openable_temp_name(orig_name):
    """Chemin relatif sous lequel une entrée non-image est écrite pour être
    ouverte avec l'application par défaut, ou None si son type est refusé.

    Le type est jugé sur le nom réellement écrit (utils.windows_safe_relpath) :
    Windows retire le point ou l'espace final d'un nom, et « evil.exe. »,
    dont l'extension apparente est « . », serait écrit puis lancé comme
    « evil.exe »."""
    from modules.qt.utils import windows_safe_relpath
    rel = windows_safe_relpath(orig_name)
    if os.path.splitext(rel)[1].lower() not in _OPENABLE_EXTS:
        return None
    return rel


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

    # Sécurité : seuls les types de _OPENABLE_EXTS sont ouverts
    # (l'utilisateur s'attend à "voir" le fichier, pas à le lancer).
    rel_name = openable_temp_name(orig_name)
    if rel_name is None:
        _warn_blocked_file(parent)
        return

    # orig_name peut contenir des sous-dossiers (ex. "sub/image.txt") — on garde
    # la structure pour éviter les collisions de noms.
    mosaicview_temp = get_mosaicview_temp_dir()

    # Défense en profondeur : windows_safe_relpath a déjà retiré les « .. ».
    from modules.qt.utils import safe_join
    tmp_path = safe_join(mosaicview_temp, rel_name)
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


# Arrêt de la surveillance en cours de chaque fichier temporaire : un même
# fichier rouvert ne garde qu'un thread, celui de sa dernière ouverture.
_watch_stops = {}
_watch_lock = threading.Lock()


def _start_watch_thread(
    tmp_path: str,
    original_bytes: bytes,
    entry: dict,
    state,
    on_modified_callback,
) -> None:
    """Lance un thread daemon qui surveille tmp_path par comparaison de contenu.

    La surveillance s'arrête à la fermeture ou au remplacement du document
    (state.doc_generation) : la page surveillée n'existe plus. Elle s'arrête
    aussi quand le même fichier est rouvert, la nouvelle surveillance prenant
    le relais (deux threads signaleraient chaque modification deux fois)."""

    # Utilise une liste pour muter le hash de référence depuis la closure
    # (permet de détecter plusieurs sauvegardes successives dans la même session)
    current_hash = [_md5(original_bytes)]
    generation = getattr(state, "doc_generation", None)
    stop = threading.Event()
    with _watch_lock:
        previous = _watch_stops.get(tmp_path)
        if previous is not None:
            previous.set()
        _watch_stops[tmp_path] = stop

    def _watch():
        try:
            _watch_until_stopped()
        finally:
            with _watch_lock:
                if _watch_stops.get(tmp_path) is stop:
                    del _watch_stops[tmp_path]

    def _watch_until_stopped():
        try:
            last_mtime = os.path.getmtime(tmp_path)
        except OSError:
            return

        deadline = time.monotonic() + _WATCH_TIMEOUT

        while time.monotonic() < deadline:
            if stop.wait(_POLL_INTERVAL):
                break
            if getattr(state, "doc_generation", None) != generation:
                break
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


def _warn_blocked_file(parent):
    """Avertit (fenêtre non-modale) que le fichier n'a pas été ouvert car son
    type n'est pas dans la liste blanche _OPENABLE_EXTS."""
    from modules.qt.localization import _, _wt
    from modules.qt.dialogs_qt import InfoDialog
    dlg = InfoDialog(
        parent,
        lambda: _wt("messages.warnings.file_open_blocked.title"),
        lambda: _("messages.warnings.file_open_blocked.message"),
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
