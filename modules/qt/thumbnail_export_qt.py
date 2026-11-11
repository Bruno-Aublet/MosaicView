"""
modules/qt/thumbnail_export_qt.py — Export des vignettes (raccourci caché Ctrl+Alt+T,
MainWindow._export_thumbnails).

Une vignette à la taille courante de la mosaïque pour chaque élément du document
du panneau actif, toutes à plat dans un dossier choisi. La liste est préparée sur
le fil de l'interface (noms, icônes), puis les pages sont décodées et écrites dans
un thread : l'application reste utilisable, avec le texte rouge de progression et
Annuler sur la mosaïque du panneau. Skill save-export, « Export des vignettes ».
"""

import io
import os
import threading

from PIL import Image
from PySide6.QtCore import QThread, Signal

from modules.qt.entries import oriented

# Extensions écrites telles quelles ; une autre page devient un PNG (Pillow
# choisit le format d'après l'extension)
_THUMB_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")


def thumbnail_items(images_data):
    """[(élément, octets de la page ou None pour une icône, nom du fichier)] :
    une icône par dossier de l'archive (sous-dossiers intermédiaires compris,
    une fois chacun, sans tenir compte de la casse), l'icône de remontée en
    tête si la BD a des dossiers, une vignette par page lisible, l'icône de la
    mosaïque pour un fichier non image ou une page illisible. Vignettes à plat :
    seul le dernier segment du nom est gardé, rendu valide pour Windows."""
    from modules.qt.utils import windows_safe_relpath
    items = []
    seen_dirs = set()
    for i, entry in enumerate(images_data):
        parts = windows_safe_relpath(entry.get("orig_name") or f"thumb_{i}").split("/")
        # Dossiers de l'archive, qui n'ont pas d'entrée propre dans la
        # mosaïque : une icône de dossier pour chacun, à sa première rencontre.
        dirs = parts if entry.get("is_dir") else parts[:-1]
        for depth in range(1, len(dirs) + 1):
            key = "/".join(dirs[:depth]).lower()
            if key not in seen_dirs:
                seen_dirs.add(key)
                items.append(({"extension": "", "is_dir": True}, None, dirs[depth - 1] + ".png"))
        if entry.get("is_dir"):
            continue
        name = parts[-1]
        raw = entry.get("bytes")
        if entry.get("is_image") and not entry.get("is_corrupted"):
            if not raw:
                continue
            stem, ext = os.path.splitext(name)
            if ext.lower() not in _THUMB_EXTS:
                name = (stem or name) + ".png"
            items.append((entry, raw, name))
        else:
            # Fichier non image ou page illisible : l'icône de la mosaïque
            items.append((entry, None, name + ".png"))
    if seen_dirs:
        # Icône de remontée, montrée par la mosaïque dans tout sous-dossier
        items.insert(0, ({"extension": "", "is_parent_dir": True}, None, "parent_dir.png"))
    return items


def write_thumbnail(source, dest, size):
    """Écrit la vignette de `source` (octets d'une page, ou image PIL d'une
    icône) dans `dest`, réduite à `size` au plus.

    Un JPEG est décodé directement à une taille proche (draft), plusieurs fois
    plus vite qu'en entier. Orientation EXIF appliquée, comme dans la mosaïque.
    Écrite par un temporaire : un échec en cours d'écriture ne laisse pas de
    vignette tronquée ; format d'après l'extension du nom final, celle du
    temporaire n'en désignant aucun."""
    from modules.qt.utils import replace_file_atomically
    if isinstance(source, (bytes, bytearray)):
        img = Image.open(io.BytesIO(source))
        if img.format == "JPEG":
            # Côté le plus grand demandé dans les deux sens : l'orientation EXIF
            # peut encore échanger largeur et hauteur
            side = max(size)
            img.draft(img.mode, (side, side))
        img = oriented(img)
    else:
        img = source.copy()
    img.thumbnail(size, Image.Resampling.LANCZOS)
    fmt = Image.registered_extensions().get(os.path.splitext(dest)[1].lower(), "PNG")
    replace_file_atomically(dest, lambda tmp: img.save(tmp, format=fmt))


class _ThumbnailWorker(QThread):
    """Écrit les vignettes préparées. Ne lit que sa propre liste (octets copiés
    au lancement) : jamais le document, que l'utilisateur peut modifier
    pendant ce temps."""

    progress = Signal(int, int)    # vignettes traitées, total
    done = Signal(int, int, str)   # vignettes écrites, vignettes en échec, raison du premier échec

    def __init__(self, jobs, size):
        super().__init__()
        self.setObjectName("ThumbnailExportWorker")
        self._jobs = jobs
        self._size = size
        self._written = 0
        self._failed = 0
        self._first_error = ""
        self._cancelled = threading.Event()

    def run(self):
        # done est toujours émis, même sur une erreur imprévue (relancée pour
        # le journal des erreurs) : sinon le texte de progression resterait
        # affiché sur la mosaïque
        try:
            self._run()
        finally:
            self.done.emit(self._written, self._failed, self._first_error)

    def _run(self):
        from modules.qt.error_log import log_if_unexpected
        from modules.qt.temp_files import exception_text
        total = len(self._jobs)
        for number, (source, dest) in enumerate(self._jobs, 1):
            if self._cancelled.is_set():
                return
            try:
                write_thumbnail(source, dest, self._size)
                self._written += 1
            except Exception as e:
                # Vignette non écrite (dossier protégé, disque plein…) : le
                # résumé dit combien, avec la raison du premier échec
                log_if_unexpected(e)
                self._failed += 1
                if not self._first_error:
                    self._first_error = exception_text(e)
            self.progress.emit(number, total)


# Workers en cours : sans référence Python, un QThread actif serait détruit
_active_workers = []


def export_thumbnails(panel):
    """Choix du dossier (fenêtre de choix non modale), puis _export_to."""
    from modules.qt.dialogs_qt import ErrorDialog
    from modules.qt.file_picker_qt import ask_directory
    from modules.qt.localization import _

    state = panel._state
    if not state.images_data:
        ErrorDialog(
            panel,
            "messages.warnings.no_thumbnail_export.title",
            lambda: _("messages.warnings.no_thumbnail_export.message"),
        ).show_nonmodal()
        return

    # Dernier dossier de l'export des vignettes s'il existe encore (réglage
    # propre à cet export), sinon dossier de la BD
    from modules.qt.config_manager import get_config_manager
    config = get_config_manager()
    initial_dir = config.get("last_thumbnail_export_dir", "")
    if not (initial_dir and os.path.isdir(initial_dir)):
        initial_dir = ""
        if state.current_file:
            initial_dir = os.path.dirname(os.path.abspath(state.current_file))
    generation = state.doc_generation

    def _on_chosen(folder):
        # Réponse différée : abandonnée si le document a été fermé ou
        # remplacé pendant le choix
        if folder and state.doc_generation == generation and state.images_data:
            _export_to(panel, folder)

    ask_directory(panel, "messages.info.thumbnails_saved.title", initial_dir, _on_chosen)


def _export_to(panel, folder):
    """Préparation de la liste des vignettes, puis écriture dans un thread."""
    from modules.qt.canvas_overlay_qt import hide_canvas_text, show_canvas_text
    from modules.qt.config_manager import get_config_manager
    from modules.qt.entries import get_icon_pil_for_entry
    from modules.qt.file_operations_qt import ThumbnailSavedDialog
    from modules.qt.localization import _
    from modules.qt.utils import (dispose_qthread, register_cancel_on_close, safe_join,
                                  unique_names, unregister_cancel_on_close)
    from modules.qt.web_import_qt import _show_cancel_item

    state = panel._state
    config = get_config_manager()
    config.set("last_thumbnail_export_dir", folder)

    items = thumbnail_items(state.images_data)
    # Les fichiers déjà présents dans le dossier gardent leur nom : une
    # vignette homonyme reçoit « (2) », « (3) »… au lieu de les remplacer.
    try:
        existing = os.listdir(folder)
    except OSError:
        existing = []
    names = unique_names(existing + [name for _entry, _raw, name in items])[len(existing):]
    jobs = []
    for (entry, raw, _name), name in zip(items, names):
        dest = safe_join(folder, name)
        if dest is None:
            continue
        # Icônes préparées ici : le thread ne lit jamais le document
        source = raw if raw is not None else get_icon_pil_for_entry(entry, state).convert("RGBA")
        jobs.append((source, dest))

    canvas = panel._canvas
    generation = state.doc_generation
    worker = _ThumbnailWorker(jobs, (state.thumb_w, state.thumb_h))
    item_holder, cancel_holder = [None], [None]
    cancelled = [False]

    def _show(number, total):
        if cancelled[0]:
            return
        show_canvas_text(canvas, _("labels.exporting_thumbnails", done=number, total=total),
                         item_holder)
        _show_cancel_item(canvas, f"[ {_('buttons.cancel')} ]", cancel_holder, _cancel,
                          anchor_lbl=item_holder[0])

    def _hide():
        hide_canvas_text(canvas, item_holder)
        hide_canvas_text(canvas, cancel_holder)

    def _cancel():
        # Annuler, ou fermeture du fichier : les vignettes déjà écrites restent
        cancelled[0] = True
        worker._cancelled.set()
        _hide()

    def on_done(count, failed, first_error):
        _hide()
        unregister_cancel_on_close(canvas, _cancel)
        if worker in _active_workers:
            _active_workers.remove(worker)
        dispose_qthread(worker)
        if cancelled[0] or state.doc_generation != generation:
            return
        ThumbnailSavedDialog(panel, count, folder, failed, first_error)

    _active_workers.append(worker)
    # Fermeture du fichier pendant l'export : même effet qu'Annuler
    register_cancel_on_close(canvas, _cancel)
    worker.progress.connect(_show)
    worker.done.connect(on_done)
    _show(0, len(jobs))
    worker.start()
