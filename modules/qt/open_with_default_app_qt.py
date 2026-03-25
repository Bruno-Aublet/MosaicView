"""
open_with_default_app_qt.py — Ouvre un fichier non-image avec l'application
Windows associée à son extension.

Logique :
  - Extrait entry["bytes"] dans MosaicViewTemp/<orig_name>
  - Appelle os.startfile() → Windows dispatche vers l'app déclarée pour l'extension
"""

import os

from modules.qt.temp_files import get_mosaicview_temp_dir


def open_file_with_default_app(entry: dict) -> None:
    """
    Extrait le fichier de l'archive vers le dossier temporaire MosaicView,
    puis l'ouvre avec l'application Windows par défaut pour son extension.

    :param entry: dict depuis state.images_data (doit contenir "bytes" et "orig_name")
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
