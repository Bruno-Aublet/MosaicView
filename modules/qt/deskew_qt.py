"""
modules/qt/deskew_qt.py — Redressement automatique (deskew) des images sélectionnées

Détecte et corrige l'inclinaison de chaque image sélectionnée via
modules.qt.deskew (Hough), sans fenêtre de prévisualisation. Complémentaire du
redressement manuel (straighten_viewer_qt.py) — aucun des deux ne remplace l'autre.

Point d'entrée public :
  deskew_selected_qt(callbacks)
"""

import threading

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QWidget, QFrame,
)
from PySide6.QtCore import Qt, QThread, Signal

from modules.qt import state as _state_module
from modules.qt.deskew import deskew_entry_data
from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.canvas_overlay_qt import show_canvas_text as _show_canvas_text, hide_canvas_text as _hide_canvas_text
from modules.qt.dialogs_qt import position_dialog_on_parent
from modules.qt.utils import dispose_qthread, register_cancel_on_close, unregister_cancel_on_close


def _connect_lang(dialog, handler):
    from modules.qt.language_signal import language_signal
    dialog._lang_handler = handler
    language_signal.changed.connect(dialog._lang_handler)
    dialog.finished.connect(lambda: _disconnect_lang(dialog))


def _disconnect_lang(dialog):
    from modules.qt.language_signal import language_signal
    try:
        language_signal.changed.disconnect(dialog._lang_handler)
    except RuntimeError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Fenêtre de résumé
# ─────────────────────────────────────────────────────────────────────────────

class _DeskewSummaryDialog(QDialog):
    """Résumé final : nombre de succès/échecs, liste des images en échec."""

    def __init__(self, parent, success_count, failed_names):
        super().__init__(parent)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self._success_count = success_count
        self._failed_names  = failed_names
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg_lbl)

        self._list_scroll = None
        self._list_labels  = []
        if failed_names:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.StyledPanel)
            scroll.setFixedHeight(min(220, 30 + 20 * len(failed_names)))
            content = QWidget()
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(8, 6, 8, 6)
            content_layout.setSpacing(2)
            for name in failed_names:
                lbl = QLabel(f"• {name}")
                lbl.setWordWrap(True)
                lbl.setAlignment(Qt.AlignCenter)
                content_layout.addWidget(lbl)
                self._list_labels.append(lbl)
            content_layout.addStretch()
            scroll.setWidget(content)
            layout.addWidget(scroll)
            self._list_scroll = scroll

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok_btn = QPushButton()
        self._ok_btn.setFixedWidth(100)
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._ok_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        _connect_lang(self, lambda _l: self._retranslate())
        self._ok_btn.setFocus()
        self._center_parent = parent

    def show_nonmodal(self):
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )

        self.setWindowTitle(_wt("dialogs.deskew_summary.title"))
        if self._failed_names:
            self._msg_lbl.setText(_("dialogs.deskew_summary.message_errors").format(
                success=self._success_count, failed=len(self._failed_names)))
        else:
            self._msg_lbl.setText(_("dialogs.deskew_summary.message").format(
                success=self._success_count))
        self._msg_lbl.setFont(font)
        # setFixedWidth(480) + WordWrap : sizeHint sous-estime la hauteur réelle
        # sur plusieurs lignes (calcul comme une seule ligne large) — impose la
        # hauteur exacte plutôt que de compter sur adjustSize()/sizeHint seuls.
        self._msg_lbl.setMinimumHeight(self._msg_lbl.heightForWidth(480 - 40))

        for lbl in self._list_labels:
            lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)


# ─────────────────────────────────────────────────────────────────────────────
# Worker thread
# ─────────────────────────────────────────────────────────────────────────────

_active_workers: list = []  # anti-GC : maintient les workers en vie jusqu'à leur destruction Qt


class _DeskewWorker(QThread):
    progress  = Signal(int)
    done      = Signal()
    cancelled = Signal()

    def __init__(self, entries, state):
        super().__init__()
        self.setObjectName("DeskewWorker")
        self._entries   = entries
        self._state     = state
        self._cancelled = threading.Event()
        self.failed_names: list = []
        self.success_count = 0

    def run(self):
        total = len(self._entries)
        for idx, entry in enumerate(self._entries):
            if self._cancelled.is_set():
                self.cancelled.emit()
                return
            try:
                ok = deskew_entry_data(entry, self._state)
            except Exception:
                ok = False
            if ok:
                self.success_count += 1
            else:
                self.failed_names.append(entry.get("orig_name", "?"))
            if self._cancelled.is_set():
                self.cancelled.emit()
                return
            entry["qt_pixmap_large"] = None
            entry["qt_qimage_large"] = None
            self.progress.emit(int((idx + 1) / total * 100))
        self.done.emit()


# ─────────────────────────────────────────────────────────────────────────────
# Lancement du worker avec overlay
# ─────────────────────────────────────────────────────────────────────────────

def _run_deskew(entries, callbacks):
    from modules.qt.web_import_qt import _show_cancel_item

    state              = callbacks.get('state') or _state_module.state
    save_state_fn      = callbacks.get('save_state',         lambda: None)
    update_button_text = callbacks.get('update_button_text', lambda: None)
    refresh_status_fn  = callbacks.get('refresh_status',     lambda: None)
    canvas             = callbacks.get('canvas')
    parent             = callbacks.get('parent')

    item_holder   = [None]
    cancel_holder = [None]
    worker_ref    = [None]

    def _show(pct):
        if worker_ref[0] is None:
            return
        _show_canvas_text(canvas, _("labels.deskewing", percent=pct), item_holder)
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
        _DeskewSummaryDialog(parent, worker.success_count, worker.failed_names).show_nonmodal()
        _release_worker()

    def on_cancelled():
        _finish_cancel()

    worker = _DeskewWorker(entries, state)
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
# Point d'entrée public
# ─────────────────────────────────────────────────────────────────────────────

def deskew_selected_qt(callbacks):
    """Redresse automatiquement les images sélectionnées (une par une, un seul
    point d'undo/redo pour tout le lot). Grisé côté UI si aucune sélection."""
    state = callbacks.get('state') or _state_module.state
    if not state.selected_indices:
        return

    entries = []
    for idx in sorted(state.selected_indices):
        if idx < len(state.images_data) and state.images_data[idx].get("is_image") \
                and not state.images_data[idx].get("is_corrupted"):
            entry = state.images_data[idx]
            entry["_real_idx"] = idx
            entries.append(entry)
    if not entries:
        return

    callbacks['save_state']()
    _run_deskew(entries, callbacks)
