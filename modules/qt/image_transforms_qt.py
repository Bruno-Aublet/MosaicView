"""
modules/qt/image_transforms_qt.py — Rotation et miroir (rotate_selected, flip_selected).

Opérations exécutées dans un QThread avec overlay de progression + bouton Annuler.
"""

import threading

from PySide6.QtCore import QThread, Signal

from modules.qt import state as _state_module
from modules.qt.image_ops import rotate_entry_data, flip_entry_data
from modules.qt.localization import _
from modules.qt.canvas_overlay_qt import show_canvas_text as _show_canvas_text, hide_canvas_text as _hide_canvas_text
from modules.qt.utils import dispose_qthread, register_cancel_on_close, unregister_cancel_on_close


def _regenerate_thumbnail_qt(entry: dict):
    """Invalide qt_pixmap_large et qt_qimage_large pour forcer la reconstruction au prochain paint()."""
    entry["qt_pixmap_large"] = None
    entry["qt_qimage_large"] = None
    entry["_hash"] = None


_active_workers: list = []  # anti-GC : maintient les workers en vie jusqu'à leur destruction Qt


# ─────────────────────────────────────────────────────────────────────────────
# Worker thread
# ─────────────────────────────────────────────────────────────────────────────

class _TransformWorker(QThread):
    progress  = Signal(int)
    done      = Signal()
    cancelled = Signal()

    def __init__(self, entries, operation, state):
        """
        entries   : liste des entrées à traiter
        operation : callable(entry, state) → bool
        state     : AppState
        """
        super().__init__()
        self.setObjectName("TransformWorker")
        self._entries   = entries
        self._operation = operation
        self._state     = state
        self._cancelled = threading.Event()

    def run(self):
        total = len(self._entries)
        for idx, entry in enumerate(self._entries):
            if self._cancelled.is_set():
                self.cancelled.emit()
                return
            try:
                self._operation(entry, self._state)
            except Exception:
                pass
            # Vérifier le flag après l'opération (qui peut être longue) :
            # si _cancel() a été appelé pendant l'opération, on arrête sans invalider le thumbnail
            # (la restauration d'état via undo se charge de remettre les bytes d'origine).
            if self._cancelled.is_set():
                self.cancelled.emit()
                return
            _regenerate_thumbnail_qt(entry)
            self.progress.emit(int((idx + 1) / total * 100))
        self.done.emit()


# ─────────────────────────────────────────────────────────────────────────────
# Lancement du worker avec overlay
# ─────────────────────────────────────────────────────────────────────────────

def _run_transform(entries, operation, label_key, callbacks):
    from modules.qt.web_import_qt import _show_cancel_item

    state              = callbacks.get('state') or _state_module.state
    save_state_fn      = callbacks.get('save_state',         lambda: None)
    update_button_text = callbacks.get('update_button_text', lambda: None)
    refresh_status_fn  = callbacks.get('refresh_status',     lambda: None)
    canvas             = callbacks.get('canvas')

    item_holder   = [None]
    cancel_holder = [None]
    worker_ref    = [None]

    def _show(pct):
        if worker_ref[0] is None:
            return
        _show_canvas_text(canvas, _(label_key, percent=pct), item_holder)
        _show_cancel_item(canvas, f"[ {_('buttons.cancel')} ]", cancel_holder, _cancel,
                          anchor_lbl=item_holder[0])

    def _hide():
        _hide_canvas_text(canvas, item_holder)
        _hide_canvas_text(canvas, cancel_holder)

    def _cancel():
        w = worker_ref[0]
        if w is None:
            return
        w._cancelled.set()
        worker_ref[0] = None
        _hide()
        # Pas de rollback ici : le worker peut être en train d'écrire les bytes
        # d'une page (restore_state_qt réutilise les mêmes dicts d'entrées), et
        # cette écriture tardive survivrait au rollback. Il est fait dans
        # _finish_cancel, une fois le worker sorti de sa boucle.

    def _release_worker():
        unregister_cancel_on_close(canvas, _cancel)
        if worker in _active_workers:
            _active_workers.remove(worker)
        dispose_qthread(worker)

    def _document_changed():
        # Document fermé ou remplacé pendant le traitement : l'état et
        # l'historique du panneau sont ceux d'un autre document, à ne pas
        # toucher (voir AppState.doc_generation).
        if state.doc_generation != doc_generation:
            return True
        return False

    def _finish_cancel():
        # Restaure l'état au sommet de l'historique (= avant la transformation)
        # sans décrémenter history_index — save_state() n'a rien ajouté car
        # l'état était identique au précédent au moment du lancement.
        if not _document_changed():
            rollback = callbacks.get('rollback')
            if rollback:
                rollback()
        _release_worker()

    def on_progress(pct):
        _show(pct)

    def on_finished():
        if worker_ref[0] is None:
            # Annulé après le traitement de la dernière page : done est émis
            # au lieu de cancelled, le rollback reste à faire.
            _finish_cancel()
            return
        worker_ref[0] = None
        _hide()
        if _document_changed():
            _release_worker()
            return
        state.modified = True
        for entry in entries:
            real_idx = entry.get("_real_idx")
            if real_idx is not None:
                canvas.refresh_thumbnail(real_idx)
        canvas.refresh_duplicate_overlay()
        update_button_text()
        refresh_status_fn()
        save_state_fn()
        _release_worker()

    def on_cancelled():
        _finish_cancel()

    worker = _TransformWorker(entries, operation, state)
    worker_ref[0] = worker
    doc_generation = state.doc_generation
    _active_workers.append(worker)
    # Fermeture du fichier pendant le traitement : même effet qu'Annuler (le
    # rollback de _finish_cancel est alors sauté, le document ayant changé)
    register_cancel_on_close(canvas, _cancel)
    worker.progress.connect(on_progress)
    worker.done.connect(on_finished)
    worker.cancelled.connect(on_cancelled)
    _show(0)
    worker.start()


# ─────────────────────────────────────────────────────────────────────────────
# Points d'entrée publics
# ─────────────────────────────────────────────────────────────────────────────

def rotate_selected_qt(angle, callbacks):
    """Fait pivoter les images sélectionnées de 90°.
    angle: -90 pour rotation droite (horaire), 90 pour rotation gauche (anti-horaire)."""
    state = callbacks.get('state') or _state_module.state
    if not state.selected_indices:
        return

    entries = [
        state.images_data[idx]
        for idx in sorted(state.selected_indices)
        if idx < len(state.images_data) and state.images_data[idx].get("is_image")
    ]
    if not entries:
        return

    for i, entry in enumerate(entries):
        entry["_real_idx"] = sorted(state.selected_indices)[i]

    callbacks['save_state']()

    def _op(entry, st):
        return rotate_entry_data(entry, angle, st)

    _run_transform(entries, _op, "labels.rotating", callbacks)


def flip_selected_qt(direction, callbacks):
    """Retourne les images sélectionnées.
    direction: 'horizontal' ou 'vertical'."""
    state = callbacks.get('state') or _state_module.state
    if not state.selected_indices:
        return

    entries = [
        state.images_data[idx]
        for idx in sorted(state.selected_indices)
        if idx < len(state.images_data) and state.images_data[idx].get("is_image")
    ]
    if not entries:
        return

    for i, entry in enumerate(entries):
        entry["_real_idx"] = sorted(state.selected_indices)[i]

    callbacks['save_state']()

    def _op(entry, st):
        return flip_entry_data(entry, direction, st)

    _run_transform(entries, _op, "labels.flipping", callbacks)
