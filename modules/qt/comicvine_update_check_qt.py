# comicvine_update_check_qt.py — Vérification de mise à jour des métadonnées
# depuis l'URL ComicVine source déjà enregistrée dans le ComicInfo.xml.
#
# Retélécharge les métadonnées de l'issue, les compare aux métadonnées locales
# et propose une mise à jour globale si des différences sont détectées.

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QWidget,
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font


# ── Helpers styles (repris du style comicvine_url_dialog_qt) ──────────────────

def _btn_style(theme):
    alt = theme.get("toolbar_bg", theme["bg"])
    sep = theme.get("separator", "#aaaaaa")
    fg  = theme["text"]
    return (
        f"QPushButton {{ background: {alt}; color: {fg}; "
        f"border: 1px solid {sep}; padding: 5px 14px; }} "
        f"QPushButton:hover {{ background: {sep}; }} "
        f"QPushButton:disabled {{ color: #888888; }}"
    )


def _label_style(theme):
    return f"color: {theme['text']};"


# Référence globale pour éviter que le GC ne détruise un QThread encore actif
# (aucune fenêtre "en attente" ne garde le worker vivant tant que la requête tourne).
_running_workers = []


# ── Worker réseau ─────────────────────────────────────────────────────────────

class _CheckUpdateWorker(QThread):
    finished = Signal(dict)
    error    = Signal(str)

    def __init__(self, api_key, issue_id):
        super().__init__()
        self._api_key  = api_key
        self._issue_id = issue_id

    def run(self):
        try:
            from modules.qt.comicvine_scraper import get_issue_details
            meta = get_issue_details(self._api_key, self._issue_id)
            if meta:
                self.finished.emit(meta)
            else:
                self.error.emit(_("comicvine_update.error_not_found"))
        except Exception as e:
            from modules.qt.comicvine_scraper import error_to_signal_payload
            self.error.emit(error_to_signal_payload(e))


# ── Point d'entrée public ─────────────────────────────────────────────────────

def show_comicvine_update_check(parent, state, api_key, issue_id, on_done=None, busy_widget=None):
    """
    Lance la vérification de mise à jour ComicVine pour `state` (non-bloquant).

    - Aucune différence trouvée -> InfoDialog "déjà à jour".
    - Différences trouvées      -> _UpdateDiffDialog listant les champs changés,
      avec un bouton pour tout mettre à jour (appelle on_done() après écriture).
    - Erreur réseau/API         -> ErrorDialog avec le message remonté.

    busy_widget : bouton déclencheur (QPushButton), désactivé et affichant
    "Vérification en cours..." pendant l'exécution, restauré ensuite.
    Marqué via l'attribut `is_checking_updates` (property Qt) pendant toute
    la durée de la requête, pour que le _retranslate() de la fenêtre parente
    puisse réafficher le bon texte ("checking" au lieu du libellé normal) si
    l'utilisateur change de langue pendant l'attente.

    Retourne le worker (pour que l'appelant puisse, si besoin, garder une
    référence le temps de l'exécution et éviter le ramassage par le GC).
    """
    worker = _CheckUpdateWorker(api_key, issue_id)

    if busy_widget is not None:
        busy_widget.setProperty("is_checking_updates", True)
        busy_widget.setEnabled(False)
        busy_widget.setText(_("comicvine_update.checking"))

    def _restore_busy_widget():
        if busy_widget is not None:
            busy_widget.setProperty("is_checking_updates", False)
            busy_widget.setEnabled(True)
            busy_widget.setText(_("comicvine_update.btn_check"))

    def _on_finished(remote_meta):
        _restore_busy_widget()
        from modules.qt.comic_info import diff_comic_metadata
        local_meta = state.comic_metadata if state else {}
        diffs = diff_comic_metadata(local_meta, remote_meta)
        if not diffs:
            from modules.qt.dialogs_qt import InfoDialog
            dlg = InfoDialog(parent,
                             lambda: _wt("comicvine_update.up_to_date_title"),
                             lambda: _("comicvine_update.up_to_date_message"))
            dlg.show_nonmodal()
            return
        dlg = _UpdateDiffDialog(parent, state, diffs, remote_meta, on_done=on_done)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_error(msg):
        _restore_busy_widget()
        from modules.qt.dialogs_qt import ErrorDialog
        from modules.qt.comicvine_scraper import error_message_fn
        ErrorDialog(parent, lambda: _wt("comicvine_update.error_title"),
                   error_message_fn(msg), play_sound=True).show_nonmodal()

    def _cleanup():
        if worker in _running_workers:
            _running_workers.remove(worker)

    worker.finished.connect(_on_finished)
    worker.error.connect(_on_error)
    worker.finished.connect(_cleanup)
    worker.error.connect(_cleanup)
    _running_workers.append(worker)
    worker.start()
    return worker


# ── Fenêtre de résultat (liste des différences) ───────────────────────────────

class _UpdateDiffDialog(QDialog):

    def __init__(self, parent, state, diffs, remote_meta, on_done=None):
        super().__init__(parent)
        self._state         = state
        self._diffs         = diffs          # list[(field_key, status)]
        self._remote_meta   = remote_meta
        self._on_done       = on_done
        self._center_parent = parent

        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setMinimumWidth(460)
        self.resize(460, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        self._lbl_intro = QLabel()
        self._lbl_intro.setWordWrap(True)
        self._lbl_intro.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_intro)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll_content = QWidget()
        self._scroll.setWidget(self._scroll_content)
        self._list_layout = QVBoxLayout(self._scroll_content)
        self._list_layout.setContentsMargins(4, 4, 4, 4)
        self._list_layout.setSpacing(4)
        self._diff_labels = []
        for field_key, status in self._diffs:
            lbl = QLabel()
            lbl.setWordWrap(True)
            self._list_layout.addWidget(lbl)
            self._diff_labels.append((lbl, field_key, status))
        self._list_layout.addStretch(1)
        layout.addWidget(self._scroll, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._btn_update = QPushButton()
        self._btn_update.setDefault(True)
        self._btn_update.clicked.connect(self._on_update_clicked)
        self._btn_cancel = QPushButton()
        self._btn_cancel.clicked.connect(self.close)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_update)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    # ── Centrage à l'affichage ─────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            p = self._center_parent
            QTimer.singleShot(0, lambda: self._center_on(p))

    def _center_on(self, parent):
        from modules.qt.dialogs_qt import _center_on_widget
        _center_on_widget(self, parent)

    # ── Traduction + thème ─────────────────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(10)

        self.setWindowTitle(_wt("comicvine_update.diff_window_title"))
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }} "
            f"QScrollArea {{ background: {theme['bg']}; border: none; }}"
        )
        self._scroll_content.setStyleSheet(f"background: {theme['bg']};")

        self._lbl_intro.setText(_("comicvine_update.diff_intro"))
        self._lbl_intro.setFont(font)
        self._lbl_intro.setStyleSheet(_label_style(theme))

        status_key = {"modified": "comicvine_update.status_modified",
                     "added": "comicvine_update.status_added"}
        for lbl, field_key, status in self._diff_labels:
            field_label = _(f"metadata.{field_key}")
            status_label = _(status_key[status])
            lbl.setText(f"• {field_label} — {status_label}")
            lbl.setFont(font)
            lbl.setStyleSheet(_label_style(theme))

        btn_style = _btn_style(theme)
        self._btn_update.setText(_("comicvine_update.btn_update_all"))
        self._btn_update.setFont(font)
        self._btn_update.setStyleSheet(btn_style)

        self._btn_cancel.setText(_("comicvine_update.btn_cancel"))
        self._btn_cancel.setFont(font)
        self._btn_cancel.setStyleSheet(btn_style)

    # ── Actions ────────────────────────────────────────────────────────────────

    def _on_update_clicked(self):
        from modules.qt.comic_info import write_comic_metadata_from_scraper
        write_comic_metadata_from_scraper(self._state, self._remote_meta)
        parent = self._center_parent
        self.close()
        if self._on_done:
            self._on_done()
        from modules.qt.dialogs_qt import InfoDialog
        dlg = InfoDialog(parent,
                         lambda: _wt("comicvine_update.update_done_title"),
                         lambda: _("comicvine_update.update_done_message"))
        dlg.show_nonmodal()

    # ── Nettoyage ─────────────────────────────────────────────────────────────

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass
