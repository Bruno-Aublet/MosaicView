"""
modules/qt/image_transforms_qt.py — Rotation et miroir (version PySide6)

Reproduit à l'identique le comportement de modules/image_transforms.py
(fonctions rotate_selected et flip_selected) pour la version Qt.

Différences avec la version tkinter :
  - update_button_text → refresh_toolbar_states (callback Qt)
  - opérations exécutées dans un QThread avec overlay de progression + bouton Annuler
"""

import threading

from PySide6.QtCore import QThread, Signal

from modules.qt import state as _state_module
from modules.qt.image_ops import rotate_entry_data, flip_entry_data
from modules.qt.localization import _
from modules.qt.canvas_overlay_qt import show_canvas_text as _show_canvas_text, hide_canvas_text as _hide_canvas_text


def _regenerate_thumbnail_qt(entry: dict):
    """Invalide qt_pixmap_large et qt_qimage_large pour forcer la reconstruction au prochain paint()."""
    entry["qt_pixmap_large"] = None
    entry["qt_qimage_large"] = None


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
                if self._operation(entry, self._state):
                    _regenerate_thumbnail_qt(entry)
            except Exception as e:
                import traceback
                traceback.print_exc()
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

    def on_progress(pct):
        _show(pct)

    def on_finished():
        if worker_ref[0] is None:
            return
        worker_ref[0] = None
        _hide()
        state.modified = True
        for entry in entries:
            real_idx = entry.get("_real_idx")
            if real_idx is not None:
                canvas.refresh_thumbnail(real_idx)
        update_button_text()
        refresh_status_fn()
        save_state_fn()
        if worker in _active_workers:
            _active_workers.remove(worker)
        worker.deleteLater()

    def on_cancelled():
        if worker in _active_workers:
            _active_workers.remove(worker)
        worker.deleteLater()

    worker = _TransformWorker(entries, operation, state)
    worker_ref[0] = worker
    _active_workers.append(worker)
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
