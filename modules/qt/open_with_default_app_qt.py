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


def _md5(data: bytes) -> bytes:
    return hashlib.md5(data, usedforsecurity=False).digest()


def open_file_with_default_app(
    entry: dict,
    state=None,
    on_modified_callback=None,
) -> None:
    """
    Extrait le fichier de l'archive vers le dossier temporaire MosaicView,
    puis l'ouvre avec l'application Windows par défaut pour son extension.

    Si state et on_modified_callback sont fournis, surveille le fichier temporaire :
    toute modification du contenu est répercutée dans entry["bytes"] et state.modified
    est mis à True, puis on_modified_callback() est appelé dans le thread Qt principal.

    :param entry: dict depuis state.images_data (doit contenir "bytes" et "orig_name")
    :param state: AppState courant (optionnel)
    :param on_modified_callback: callable() appelé après mise à jour de entry["bytes"]
    """
    raw: bytes | None = entry.get("bytes")
    if not raw:
        return

    orig_name: str = entry.get("orig_name", "file")
    # orig_name peut contenir des sous-dossiers (ex. "sub/image.txt") — on garde
    # la structure pour éviter les collisions de noms.
    mosaicview_temp = get_mosaicview_temp_dir()
    tmp_path = os.path.join(mosaicview_temp, orig_name)

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
