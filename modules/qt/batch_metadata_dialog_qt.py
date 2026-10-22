"""
batch_metadata_dialog_qt.py — Confirmation + orchestration du batch d'import de métadonnées.
"""

import os
import threading

# Référence module-level pour empêcher le GC de détruire l'orchestrateur en cours de batch
_active_orchestrators = []
# Listes de fichiers dont la confirmation d'import est affichée (import pas
# encore démarré), voir _files_in_running_batch.
_pending_confirmations = []

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QCheckBox,
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal as _Signal

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.archive_loader import _natural_sort_key
from modules.qt.archive_names import open_zip
from modules.qt.temp_files import exception_text


def _is_comicinfo_name(member_name):
    """Membre d'archive qui serait le ComicInfo.xml du CBZ produit : à la racine
    une fois son nom ramené dans le dossier d'extraction (« ./ComicInfo.xml »
    d'un CBT compris), comic_info.is_comic_info_name. Un ComicInfo.xml rangé
    dans un dossier n'est qu'un fichier annexe, recopié comme les autres."""
    from modules.qt.archive_names import contained_member_name
    from modules.qt.comic_info import is_comic_info_name
    return is_comic_info_name(contained_member_name(member_name))


def _connect_lang(dialog, handler):
    from modules.qt.language_signal import language_signal
    dialog._lang_handler = handler
    dialog._lang_connected = True
    language_signal.changed.connect(dialog._lang_handler)
    dialog.finished.connect(lambda: _disconnect_lang(dialog))


def _disconnect_lang(dialog):
    if not getattr(dialog, '_lang_connected', False):
        return
    dialog._lang_connected = False
    from modules.qt.language_signal import language_signal
    try:
        language_signal.changed.disconnect(dialog._lang_handler)
    except RuntimeError:
        pass


class _MetadataConfirmDialog(QDialog):

    def __init__(self, parent, files: list, dirs: list):
        super().__init__(parent)
        self._files = files
        self._dirs  = dirs
        self.confirmed        = False
        self.permanent_delete = False
        self.skip_existing    = False

        # Y a-t-il des fichiers non-CBZ ?
        self._has_non_cbz = any(
            not f.lower().endswith('.cbz') for f in files
        )

        self.setModal(False)
        self.setFixedWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        # Message nombre de fichiers
        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg_lbl)

        layout.addSpacing(4)

        # Avertissement CBZ (visible seulement si fichiers non-CBZ)
        self._warn_lbl = QLabel()
        self._warn_lbl.setWordWrap(True)
        self._warn_lbl.setAlignment(Qt.AlignCenter)
        self._warn_lbl.setVisible(self._has_non_cbz)
        layout.addWidget(self._warn_lbl)

        layout.addSpacing(4)

        # Checkbox suppression permanente (visible seulement si fichiers non-CBZ)
        self._chk = QCheckBox()
        self._chk.setVisible(self._has_non_cbz)
        layout.addWidget(self._chk, alignment=Qt.AlignCenter)

        # Checkbox ignorer les fichiers ayant déjà des métadonnées
        self._chk_skip = QCheckBox()
        layout.addWidget(self._chk_skip, alignment=Qt.AlignCenter)

        layout.addSpacing(8)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep)

        layout.addSpacing(4)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._start_btn = QPushButton()
        self._start_btn.setFixedWidth(110)
        self._start_btn.setDefault(True)
        self._start_btn.clicked.connect(self._on_start)
        self._cancel_btn = QPushButton()
        self._cancel_btn.setFixedWidth(110)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._start_btn)
        btn_row.addSpacing(16)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.rejected.connect(self._on_cancel)
        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._start_btn.setFocus()
        self._center_parent = parent

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
            f"QFrame[frameShape='4'] {{ color: {theme['separator']}; }}"
        )
        font  = _get_current_font(10)
        font9 = _get_current_font(9)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )

        display_dir = self._dirs[0] if len(self._dirs) == 1 else ", ".join(self._dirs)
        if len(display_dir) > 60:
            display_dir = "..." + display_dir[-57:]

        self.setWindowTitle(_wt("dialogs.batch_metadata.confirm_title"))
        self._msg_lbl.setText(
            _("dialogs.batch_metadata.confirm_message").format(
                count=len(self._files),
                directory=display_dir,
            )
        )
        self._msg_lbl.setFont(font)

        self._warn_lbl.setText(_("dialogs.batch_metadata.cbz_warning"))
        self._warn_lbl.setFont(font9)
        self._warn_lbl.setStyleSheet("color: rgb(220,0,0); font-style: italic;")

        self._chk.setText(_("dialogs.batch_metadata.checkbox_permanent_delete"))
        self._chk.setFont(font9)

        self._chk_skip.setText(_("dialogs.batch_metadata.checkbox_skip_existing"))
        self._chk_skip.setFont(font9)

        self._start_btn.setText(_("dialogs.batch_metadata.start_button"))
        self._start_btn.setFont(font)
        self._start_btn.setStyleSheet(btn_style)
        self._cancel_btn.setText(_("buttons.cancel"))
        self._cancel_btn.setFont(font)
        self._cancel_btn.setStyleSheet(btn_style)

    def _on_start(self):
        self.confirmed        = True
        self.permanent_delete = self._chk.isChecked()
        self.skip_existing    = self._chk_skip.isChecked()
        self.accept()

    def _on_cancel(self):
        self.confirmed = False
        self.accept()


# ═══════════════════════════════════════════════════════════════════════════════
# Worker de chargement/extraction (thread séparé pour ne pas bloquer l'UI)
# ═══════════════════════════════════════════════════════════════════════════════

class _LoadWorker(QThread):
    done  = _Signal(str, object)   # (cbz_filepath, state)
    error = _Signal(str)

    def __init__(self, orchestrator, orig_filepath):
        super().__init__()
        self._orc  = orchestrator
        self._path = orig_filepath

    def run(self):
        # Message d'erreur destiné au journal de l'import (lu dans le Bloc-notes) :
        # en latin pour une langue pIqaD ou tengwar
        from modules.qt.localization import latin_texts
        with latin_texts():
            try:
                cbz_path, state = self._orc._load_state_for_file(self._path)
                self.done.emit(cbz_path, state)
            except Exception as e:
                self.error.emit(exception_text(e))


class _SaveCancelled(Exception):
    """Écriture d'un fichier du lot abandonnée (annulation du lot, ou
    fermeture de l'application via stop_running_qthreads)."""


class _CancellableZip:
    """ZipFile en écriture dont chaque writestr appelle d'abord check()
    (qui lève _SaveCancelled si l'écriture doit s'arrêter). written_files :
    nombre de fichiers écrits (dossiers exclus), attendu par le contrôle du
    CBZ produit."""

    def __init__(self, zf, check):
        self._zf = zf
        self._check = check
        self.written_files = 0

    def writestr(self, name, *args, **kwargs):
        self._check()
        result = self._zf.writestr(name, *args, **kwargs)
        filename = getattr(name, "filename", name)  # nom ou ZipInfo
        if not filename.endswith("/"):
            self.written_files += 1
        return result

    def __getattr__(self, name):
        return getattr(self._zf, name)


class _SaveWorker(QThread):
    done     = _Signal(str)   # raison de l'échec de suppression de l'original, "" sinon
    error    = _Signal(str)
    progress = _Signal(int)   # pourcentage conversion PDF

    def __init__(self, orchestrator, filepath, state):
        super().__init__()
        self._orc      = orchestrator
        self._filepath = filepath
        self._state    = state
        # Annulation coopérative, testée entre deux fichiers écrits : posée par
        # l'annulation du lot et par stop_running_qthreads (utils.py) à la
        # fermeture de l'application. Le temporaire est alors supprimé et
        # l'original reste intact, au lieu d'un thread détruit en pleine écriture.
        self._cancelled = threading.Event()

    def run(self):
        # Messages d'erreur destinés au journal de l'import (lu dans le
        # Bloc-notes) : en latin pour une langue pIqaD ou tengwar
        from modules.qt.localization import latin_texts
        with latin_texts():
            try:
                source_error = self._orc._save_state_for_file(
                    self._filepath, self._state,
                    progress_cb=lambda pct: self.progress.emit(pct),
                    cancel_event=self._cancelled)
                self.done.emit(source_error or "")
            except _SaveCancelled:
                pass
            except Exception as e:
                self.error.emit(exception_text(e))


# ═══════════════════════════════════════════════════════════════════════════════
# Orchestrateur batch
# ═══════════════════════════════════════════════════════════════════════════════

class _BatchMetadataOrchestrator:
    """
    Gère la séquence batch : ouvre la fenêtre ComicVine pour chaque fichier,
    avec caches partagés entre fichiers.
    """

    def __init__(self, parent, files: list, batch_callbacks: dict, claims,
                 permanent_delete: bool = False, skip_existing: bool = False):
        self._parent          = parent
        self._files           = files
        self._callbacks       = batch_callbacks
        self._permanent_delete = permanent_delete
        self._skip_existing   = skip_existing
        self._index           = 0
        self._current_dlg     = None
        self._worker          = None
        self._orphan_workers  = []   # workers abandonnés mais encore en cours
        # Process de lecture des couvertures PDF (pdf_probe), lancé au premier
        # PDF du lot, arrêté à la fin du lot
        self._pdf_probe       = None
        self._pending_timer   = None
        self._seq             = 0
        # Lot terminé (résumé affiché) : la fermeture de la fenêtre ComicVine
        # par _show_summary ne doit pas relancer une fin de lot
        self._ended           = False
        # Annulation demandée pendant l'écriture d'un fichier : le résumé
        # attend la fin réelle de cette écriture (cancel, _finish_cancel)
        self._cancelling      = False
        # Lot inscrit auprès des autres traitements par lot (batch_claims,
        # BatchClaims.start) : fichier en cours et CBZ qu'il va produire
        # réservés jusqu'au fichier suivant, lot terminé par _show_summary
        self._claims          = claims

        # Caches partagés entre tous les fichiers du batch
        self._search_cache = {}

        self._issues_cache = {}

        # Statistiques pour le résumé final
        self._done_count    = 0   # fichiers avec métadonnées écrites
        self._skipped_count = 0   # fichiers ignorés
        self._errors        = []  # [(filename, message)]

    def start(self):
        self._load_and_open(first=True)

    def _load_and_open(self, first=False):
        """Lance le chargement du fichier courant dans un thread, affiche l'overlay."""
        self._claims.release_all()
        if self._index >= len(self._files):
            if self._current_dlg:
                self._current_dlg.close()
            else:
                # Aucun fichier n'a ouvert la fenêtre ComicVine (tous ignorés
                # ou réservés par un autre lot) : sans elle, rien d'autre ne
                # terminerait le lot ni n'afficherait son résumé.
                self._show_summary()
            return

        orig_filepath = self._files[self._index]

        # Fichier réservé par un autre traitement par lot encore en cours
        # (conversion du même dossier…) : laissé de côté, compté en erreur.
        if not self._claims.claim(orig_filepath):
            # Ligne du journal (Bloc-notes) : _wt, jamais en pIqaD ou tengwar
            self._errors.append((os.path.basename(orig_filepath),
                                 _wt("messages.errors.batch_file_in_other_batch")))
            self._index += 1
            self._load_and_open(first=first)
            return

        # Skip si l'option est cochée et que le fichier CBZ contient déjà un ComicInfo.xml
        if self._skip_existing and orig_filepath.lower().endswith('.cbz'):
            try:
                import zipfile as _zf
                with _zf.ZipFile(orig_filepath, 'r') as _z:
                    from modules.qt.comic_info import is_comic_info_name
                    if any(is_comic_info_name(n) for n in _z.namelist()):
                        self._skipped_count += 1
                        self._index += 1
                        self._load_and_open(first=first)
                        return
            except Exception:
                pass  # repli : le fichier est traité comme s'il n'avait pas de ComicInfo.xml

        # Annuler un timer en attente et ignorer le worker précédent s'il tourne encore
        if self._pending_timer is not None:
            self._pending_timer.stop()
            self._pending_timer = None
        self._retire_current_worker()

        self._seq += 1
        my_seq = self._seq

        if first:
            self._worker = _LoadWorker(self, orig_filepath)
            self._worker.done.connect(
                lambda cbz, st, s=my_seq: self._on_load_done_first(cbz, st) if s == self._seq else None)
            self._worker.error.connect(
                lambda msg, s=my_seq: self._on_load_error(msg) if s == self._seq else None)
            self._worker.start()
        else:
            ext = os.path.splitext(orig_filepath)[1].lower()
            self._worker = _LoadWorker(self, orig_filepath)
            self._worker.done.connect(
                lambda cbz, st, s=my_seq: self._on_load_done_next(cbz, st) if s == self._seq else None)
            self._worker.error.connect(
                lambda msg, s=my_seq: self._on_load_error(msg) if s == self._seq else None)
            self._worker.start()
            self._pending_timer = None

    def _show_converting_overlay(self, visible, text=None):
        """Affiche/cache l'overlay 'Conversion en cours' sur la page courante."""
        if not self._current_dlg:
            return
        dlg = self._current_dlg
        stack_idx = dlg._stack.currentIndex()
        txt = text if text is not None else _("dialogs.batch_cbr.converting_title")
        if stack_idx == 0:
            if visible:
                dlg._page1.set_loading(True)
                dlg._page1._loading_overlay.setText(txt)
                dlg._page1._loading_overlay.show()
                dlg._page1._loading_overlay.raise_()
            else:
                dlg._page1.set_loading(False)
        else:
            if visible:
                dlg._page2.show_loading_overlay(True, 0, 0)
                dlg._page2._loading_overlay.setText(txt)
                dlg._page2._loading_overlay.show()
                dlg._page2._loading_overlay.raise_()
            else:
                dlg._page2.show_loading_overlay(False, 0, 0)

    def _on_save_progress(self, pct):
        """Met à jour le texte de l'overlay avec le pourcentage de conversion PDF."""
        txt = _("dialogs.batch_cbr.converting_title") + f"\n{pct}%"
        self._show_converting_overlay(True, text=txt)

    def _on_load_done_first(self, cbz_filepath, state):
        self._current_cbz_filepath = cbz_filepath
        from modules.qt.config_manager import get_config_manager
        api_key = get_config_manager().get_comicvine_api_key()
        from modules.qt.comicvine_dialog_qt import show_comicvine_dialog
        self._current_dlg = show_comicvine_dialog(
            self._parent, state, api_key,
            batch=True,
            cbz_filepath=cbz_filepath,
            on_done=lambda: self._on_file_done(cbz_filepath, state),
            batch_index=self._index + 1,
            batch_total=len(self._files),
            shared_search_cache=self._search_cache,
            shared_issues_cache=self._issues_cache,
            on_next=self._on_skip,
            on_cancel_batch=self.cancel,
        )
        self._current_dlg.finished.connect(self._on_dialog_closed)

    def _on_dialog_closed(self, _result=None):
        """Fenêtre ComicVine fermée hors fin de lot (croix, Échap) : arrête le
        lot comme le bouton Annuler confirmé, résumé compris. Sinon le lot
        resterait bloqué sans fenêtre, sans résumé, et un enregistrement en
        cours enchaînerait le fichier suivant dans une fenêtre invisible."""
        if self._ended:
            return
        self.cancel()

    def _on_load_done_next(self, cbz_filepath, state):
        self._current_cbz_filepath = cbz_filepath
        if self._current_dlg:
            self._rescue_dlg_workers(self._current_dlg)
        self._update_dialog(state, cbz_filepath)
        self._lock_nav_buttons(False)

    def _on_load_error(self, msg):
        fname = os.path.basename(self._files[self._index]) if self._index < len(self._files) else "?"
        self._errors.append((fname, msg))
        if self._current_dlg:
            self._current_dlg._page1.set_loading(False)
            self._current_dlg._page2.show_loading_overlay(False, 0, 0)
        self._index += 1
        self._lock_nav_buttons(False)
        self._load_and_open(first=self._current_dlg is None)

    def _on_skip(self):
        self._skipped_count += 1
        self._on_next()

    def _on_next(self):
        if self._cancelling:
            return  # lot arrêté : la fin de l'écriture en cours affiche le résumé
        self._index += 1
        if self._index >= len(self._files):
            self._retire_current_worker()
            self._show_summary()
            return
        self._lock_nav_buttons(True)
        self._reset_dialog()
        self._load_and_open(first=False)

    def cancel(self):
        """Arrêt anticipé du batch (bouton Annuler confirmé) : le fichier
        courant et les suivants ne recevront pas de métadonnées, mais ceux
        déjà traités restent acquis. Parque les workers en cours puis affiche
        le résumé, comme une fin de batch normale.

        Une écriture en cours n'est arrêtée qu'avant l'écriture d'un fichier
        du CBZ : passé le dernier, elle va jusqu'au bout (remplacement,
        relecture, suppression de l'original). Le résumé attend donc sa fin
        réelle, et ses fichiers restent réservés jusque-là (_show_summary
        termine le lot) : sinon le fichier serait annoncé non traité alors
        que son original a été supprimé, et un autre lot pourrait le prendre
        pendant cette fin d'écriture."""
        if self._ended or self._cancelling:
            return
        if self._pending_timer is not None:
            self._pending_timer.stop()
            self._pending_timer = None
        self._retire_current_worker()
        save_worker = getattr(self, '_save_worker', None)
        if save_worker is not None and save_worker.isRunning():
            self._cancelling = True
            save_worker._cancelled.set()
            # finished natif (_SaveWorker ne le masque pas), émis après son
            # done ou son error : le fichier est compté avant le résumé.
            save_worker.finished.connect(self._finish_cancel)
            if not save_worker.isRunning():
                # Fini juste avant le branchement : son finished est perdu
                QTimer.singleShot(0, self._finish_cancel)
            return
        self._retire_save_worker()
        self._show_summary()

    def _finish_cancel(self):
        """Fin réelle de l'écriture en cours au moment de l'annulation."""
        self._retire_save_worker()
        self._show_summary()

    def _rescue_dlg_workers(self, dlg):
        """Détache les workers internes du dialog (y compris ceux des couvertures,
        dont le résultat afficherait sinon une couverture périmée sur le fichier
        suivant) et les garde vivants jusqu'à la fin réelle de leur thread.

        Parqués via park_qthread, pas via leur signal finished : les workers
        ComicVine déclarent leur propre `finished`, qui masque QThread.finished
        natif et n'est pas émis sur tous les chemins."""
        import warnings
        from modules.qt.utils import park_qthread
        from modules.qt.comicvine_dialog_qt import _ComicVineDialog
        for attr in ('_worker', '_image_worker', '_first_issue_worker', '_first_issue_image_worker'):
            w = getattr(dlg, attr, None)
            if w is None:
                continue
            setattr(dlg, attr, None)
            # Déjà parqué par la fenêtre elle-même (_park_running_worker ne remet
            # pas toujours l'attribut à None) : un second parking le libérerait
            # alors qu'il est encore dans _dying_workers, dont la purge
            # (isRunning()) toucherait un objet détruit.
            if any(x is w for x in _ComicVineDialog._dying_workers):
                continue
            for sig in ('finished', 'error', 'progress'):
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore', RuntimeWarning)
                        getattr(w, sig).disconnect()
                except (RuntimeError, TypeError, AttributeError):
                    pass
            try:
                w.setParent(None)
            except RuntimeError:
                pass
            park_qthread(w)

    def _retire_save_worker(self):
        """Met le save_worker en orphelin s'il tourne encore (évite destruction par GC)."""
        w = getattr(self, '_save_worker', None)
        if w is None or not w.isRunning():
            return
        try:
            w.done.disconnect()
        except RuntimeError:
            pass
        try:
            w.error.disconnect()
        except RuntimeError:
            pass
        self._orphan_workers.append(w)
        w.finished.connect(
            lambda ww=w: self._orphan_workers.remove(ww) if ww in self._orphan_workers else None)
        self._save_worker = None

    def _retire_current_worker(self):
        """Déconnecte et met en orphelin le worker courant s'il tourne encore."""
        if self._worker is None or not self._worker.isRunning():
            return
        try:
            self._worker.done.disconnect()
        except RuntimeError:
            pass
        try:
            self._worker.error.disconnect()
        except RuntimeError:
            pass
        orphan = self._worker
        # Couverture d'un PDF encore en lecture : son process est arrêté (la
        # lecture échoue aussitôt, résultat ignoré) et le fichier suivant en
        # aura un neuf, au lieu d'attendre la fin d'une lecture devenue inutile
        if orphan._path.lower().endswith('.pdf'):
            self._stop_pdf_probe()
        self._orphan_workers.append(orphan)
        orphan.finished.connect(
            lambda w=orphan: self._orphan_workers.remove(w) if w in self._orphan_workers else None)
        self._worker = None

    def _stop_pdf_probe(self):
        if self._pdf_probe is not None:
            self._pdf_probe.shutdown()
            self._pdf_probe = None

    def _lock_nav_buttons(self, locked):
        """Désactive/réactive les boutons Skip et Cancel pendant le chargement."""
        dlg = self._current_dlg
        if not dlg:
            return
        for page in (dlg._page1, dlg._page2):
            page._btn_skip.setEnabled(not locked)
            page._btn_cancel.setEnabled(not locked)

    def _reset_dialog(self):
        """Vide immédiatement le dialog en attendant le chargement du fichier suivant."""
        dlg = self._current_dlg
        if not dlg:
            return
        dlg._stack.setCurrentIndex(0)
        dlg._page1.set_cover(None)
        dlg._page1.clear_first_issue_cover()
        dlg._page1.set_filename("")
        dlg._page1.set_search_term("")
        dlg._page1._table.setRowCount(0)
        dlg._page1.set_loading(False)
        dlg._page2.set_cover(None)
        dlg._page2.set_filename("")
        dlg._page2.clear_issue_cover()
        dlg._page2._table.setRowCount(0)

    def _update_dialog(self, state, cbz_filepath):
        """Met à jour la fenêtre ComicVine existante pour le nouveau fichier."""
        dlg = self._current_dlg
        dlg._state           = state
        dlg._batch_index     = self._index + 1
        dlg._selected_series = None
        dlg._issues_had_done = False

        # Retour à la page 1
        dlg._stack.setCurrentIndex(0)

        # Nouvelle couverture + nom de fichier
        from modules.qt.comicvine_dialog_qt import _cover_pixmap_from_state
        cover_pix = _cover_pixmap_from_state(state)
        dlg._page1.set_cover(cover_pix)
        dlg._page2.set_cover(cover_pix)
        dlg._page2.clear_issue_cover()

        # Nom affiché = fichier SOURCE (original), pas la destination cbz
        orig_path = getattr(state, '_original_filepath', None) or cbz_filepath
        filename = os.path.basename(orig_path)
        dlg._page1.set_filename(filename)
        dlg._page2.set_filename(filename)

        # Nouveau terme de recherche
        initial = ""
        if state and state.comic_metadata:
            initial = state.comic_metadata.get("series", "").strip()
        if not initial and orig_path:
            initial = os.path.splitext(os.path.basename(orig_path))[0]
        dlg._page1.set_search_term(initial)

        # Mettre à jour le callback on_done pour ce nouveau fichier
        dlg._on_done = lambda: self._on_file_done(cbz_filepath, state)

        # Retranslate (met à jour le compteur dans le titre et les pages)
        dlg._retranslate()

        # Lancer la recherche automatiquement (guard : dialog peut être fermé avant le délai)
        if initial:
            QTimer.singleShot(100, lambda: dlg._page1._on_search_clicked() if dlg.isVisible() else None)

    def _on_file_done(self, filepath, state):
        """Lance la sauvegarde en thread avec overlay, puis passe au suivant."""
        orig = getattr(state, '_original_filepath', None)
        is_non_cbz = orig and orig != filepath
        if is_non_cbz:
            self._show_converting_overlay(True)
        self._save_worker = _SaveWorker(self, filepath, state)
        self._save_worker.done.connect(
            lambda source_error, o=orig: self._on_save_done(source_error, o))
        self._save_worker.progress.connect(self._on_save_progress)
        def _on_save_error(msg):
            fname = os.path.basename(filepath)
            self._errors.append((fname, msg))
            self._retire_save_worker()
            self._show_converting_overlay(False)
            self._on_next()
        self._save_worker.error.connect(_on_save_error)
        self._save_worker.start()

    def _on_save_done(self, source_error="", orig_filepath=None):
        self._show_converting_overlay(False)
        self._done_count += 1
        # CBZ écrit et vérifié, mais original resté en place : compté comme
        # traité, et signalé dans le résumé et le journal du lot
        if source_error and orig_filepath:
            # Ligne du journal (Bloc-notes) : _wt, jamais en pIqaD ou tengwar
            self._errors.append((os.path.basename(orig_filepath),
                                 _wt("messages.errors.batch_source_not_deleted",
                                     error=source_error)))
        self._retire_save_worker()
        self._on_next()

    def _show_summary(self):
        """Ferme le dialog ComicVine et affiche la fenêtre de résumé."""
        if self._ended:
            return
        self._ended = True
        self._claims.finish()
        self._stop_pdf_probe()
        if self._current_dlg:
            self._rescue_dlg_workers(self._current_dlg)
            self._current_dlg.close()

        # Écrire le log si erreurs
        log_path = None
        log_error = None
        if self._errors:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = self._callbacks['get_mosaicview_temp_dir']()
                from modules.qt.temp_files import unique_log_path, log_text
                from modules.qt.batch_dialogs_qt import log_line, log_title
                log_path = unique_log_path(mosaicview_temp, "metadata", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write(log_title("title_metadata"))
                    lf.write(log_line("date", date=now.strftime('%Y-%m-%d %H:%M')))
                    lf.write(log_line("total_files", count=len(self._files)))
                    lf.write(log_line("processed", count=self._done_count))
                    lf.write(log_line("skipped", count=self._skipped_count))
                    lf.write(log_line("errors", count=len(self._errors)))
                    lf.write(f"\n{'='*60}\n")
                    lf.write(log_line("error_details"))
                    lf.write(f"{'='*60}\n\n")
                    for fname, msg in self._errors:
                        lf.write(f"  - {log_text(fname)}: {log_text(msg)}\n")
            except Exception as e:
                # Journal impossible à écrire : la raison est affichée dans le résumé.
                log_path = None
                log_error = exception_text(e)

        summary_data = {
            "total":   len(self._files),
            "done":    self._done_count,
            "skipped": self._skipped_count,
            "errors":  len(self._errors),
            "log_path": log_path,
            "log_error": log_error,
        }
        _show_metadata_summary(self._parent, summary_data)
        if hasattr(self, '_on_batch_complete'):
            self._on_batch_complete()

    def _load_state_for_file(self, orig_filepath):
        """
        Charge un state pour le fichier donné.
        Pour les non-CBZ : extrait les images via le loader approprié et
        prépare un chemin CBZ de destination.
        Retourne (cbz_filepath, state).
        """
        import zipfile
        from modules.qt import state as _state_mod
        from modules.qt.comic_info import parse_comic_info_xml
        from modules.qt.archive_names import IMAGE_EXTS

        new_state = _state_mod.AppState()
        new_state.current_file    = orig_filepath
        new_state.images_data     = []
        new_state.comic_metadata  = {}
        new_state.modified        = False
        new_state._original_filepath = orig_filepath

        ext = os.path.splitext(orig_filepath)[1].lower()

        if ext == '.cbz':
            cbz_filepath = orig_filepath
            # Noms réels (open_zip), les mêmes qu'à la recopie de l'archive
            # dans _save_state_for_file : la couverture y est reconnue par son nom
            from modules.qt.archive_loader import _check_member_size
            with open_zip(orig_filepath) as zf:
                all_names = zf.namelist()

                def _read_first(name):
                    # Par ZipInfo, premier membre de ce nom : read(nom) rendrait
                    # le dernier homonyme. Taille annoncée contrôlée avant lecture.
                    info = next(i for i in zf.infolist() if i.filename == name)
                    _check_member_size(info.file_size)
                    return zf.read(info)

                cover_name = next(
                    (n for n in sorted(all_names, key=_natural_sort_key)
                     if os.path.splitext(n)[1].lower() in IMAGE_EXTS),
                    None
                )
                # Couverture uniquement. Premier membre de ce nom : c'est lui que
                # _save_state_for_file remplace, un homonyme étant recopié.
                if cover_name:
                    data = _read_first(cover_name)
                    new_state.images_data.append({
                        "orig_name": cover_name, "name": cover_name,
                        "bytes": data, "is_image": True,
                        "is_dir": False,
                        "extension": os.path.splitext(cover_name)[1].lower(),
                    })
                # ComicInfo.xml de la B.D. : le fichier nommé exactement ainsi,
                # à la racine (règle commune, comme l'option « ignorer les
                # fichiers ayant déjà un ComicInfo.xml »). ComicInfo_0002.xml,
                # dossier/ComicInfo.xml ou une copie de sauvegarde ne sont que
                # des fichiers annexes : pris ici, ils pré-rempliraient le
                # formulaire avec d'autres métadonnées.
                from modules.qt.comic_info import is_comic_info_name
                xml_name = next((n for n in all_names if is_comic_info_name(n)), None)
                if xml_name:
                    xml_data = _read_first(xml_name)
                    new_state.images_data.append({
                        "orig_name": xml_name, "name": xml_name,
                        "bytes": xml_data, "is_image": False,
                        "is_dir": False,
                        "extension": ".xml",
                    })
                    try:
                        new_state.comic_metadata = parse_comic_info_xml(xml_data) or {}
                    except Exception:
                        pass  # repli : XML recopié tel quel, aucune métadonnée pré-remplie
                # Manifest pour que _save_state_for_file réécrive le CBZ complet
                new_state.images_data.append({
                    "_cbz_path": orig_filepath,
                    "is_image": False, "is_dir": False,
                    "orig_name": "__cbz_manifest__", "name": "__cbz_manifest__",
                    "bytes": None, "extension": "",
                })

        else:
            # Fichier non-CBZ : extraire les images via les loaders existants.
            # CBZ de destination à un nom libre, comme les conversions par lot :
            # un X.cbz déjà présent à côté de X.cbr ne doit jamais être écrasé.
            # Nom réservé (batch_claims) jusqu'au fichier suivant : l'écriture
            # n'a lieu qu'après le choix ComicVine, un lot de conversion lancé
            # entre-temps aurait sinon pu créer ce même nom.
            from modules.qt.batch_claims import free_name_candidates
            base_path = os.path.splitext(orig_filepath)[0]
            cbz_filepath = self._claims.reserve(free_name_candidates(base_path, '.cbz'))
            new_state.current_file = cbz_filepath
            # Jamais d'erreur avalée ici : sans manifeste, l'écriture produirait
            # un CBZ ne contenant que ComicInfo.xml, puis supprimerait
            # l'original. L'exception passe par _LoadWorker.error (fichier
            # compté en erreur, lot poursuivi au fichier suivant).
            new_state.images_data = self._extract_non_cbz(orig_filepath, ext)

        return cbz_filepath, new_state

    def _extract_non_cbz(self, filepath, ext):
        """Extrait les images d'un fichier non-CBZ en liste d'entries."""
        images_data = []

        from modules.qt.archive_names import IMAGE_EXTS

        def _is_image(name):
            return os.path.splitext(name)[1].lower() in IMAGE_EXTS

        if ext == '.cbr':
            import rarfile
            from modules.qt.archive_loader import _rar_read
            with rarfile.RarFile(filepath) as rf:
                all_names = sorted(n for n in rf.namelist() if _is_image(n))
                if all_names:
                    data = _rar_read(rf, all_names[0])
            if all_names:
                images_data.append({
                    "orig_name": all_names[0], "name": all_names[0],
                    "bytes": data, "is_image": True,
                    "is_dir": False,
                    "extension": os.path.splitext(all_names[0])[1].lower(),
                })
            images_data.append({
                "_cbr_path": filepath,
                "is_image": False, "is_dir": False,
                "orig_name": "__cbr_manifest__", "name": "__cbr_manifest__",
                "bytes": None, "extension": "",
            })

        elif ext == '.cbt':
            import tarfile
            from modules.qt.archive_loader import _tar_info_read
            with tarfile.open(filepath, 'r:*') as tf:
                img_members = sorted(
                    (m for m in tf.getmembers() if m.isfile() and _is_image(m.name)),
                    key=lambda m: m.name
                )
                if img_members:
                    data = _tar_info_read(tf, img_members[0])
                    if data:
                        images_data.append({
                            "orig_name": img_members[0].name,
                            "name": os.path.basename(img_members[0].name),
                            "bytes": data, "is_image": True,
                            "is_dir": False,
                            "extension": os.path.splitext(img_members[0].name)[1].lower(),
                        })
            images_data.append({
                "_cbt_path": filepath,
                "is_image": False, "is_dir": False,
                "orig_name": "__cbt_manifest__", "name": "__cbt_manifest__",
                "bytes": None, "extension": "",
            })

        elif ext == '.cb7':
            from modules.qt.archive_loader import _list_7z_files, _read_7z_file
            # Tous les fichiers de l'archive (recopiés par _save_state_for_file,
            # avant suppression de l'original) ; la couverture parmi les images.
            all_names = sorted(_list_7z_files(filepath))
            image_names = [n for n in all_names if _is_image(n)]
            # On ne charge que la couverture (1ère image) — la conversion complète
            # se fera dans _save_state_for_file via re-extraction
            if image_names:
                first = image_names[0]
                data = _read_7z_file(filepath, first)
                if data:
                    images_data.append({
                        "orig_name": first, "name": first,
                        "bytes": data, "is_image": True,
                        "is_dir": False,
                        "extension": os.path.splitext(first)[1].lower(),
                    })
            images_data.append({"_cb7_all_names": all_names, "_cb7_path": filepath,
                                 "is_image": False, "is_dir": False,
                                 "orig_name": "__cb7_manifest__", "name": "__cb7_manifest__",
                                 "bytes": None, "extension": ""})

        elif ext == '.pdf':
            # On ne rasterise que la première page (couverture) pour l'aperçu,
            # dans un process séparé (pdf_probe) : un PDF qui fait planter
            # MuPDF ne ferme pas l'application. La conversion complète se fera
            # dans _save_state_for_file.
            from modules.qt import pdf_probe
            if self._pdf_probe is None:
                self._pdf_probe = pdf_probe.PdfProbe()
            status, data = self._pdf_probe.call(('cover_jpeg', filepath, 96))
            if status == pdf_probe.ERROR:
                raise ValueError(data)
            if status == pdf_probe.TIMEOUT:
                raise ValueError(_("messages.errors.pdf_failure.no_response"))
            if status != pdf_probe.OK:
                raise ValueError(_("messages.errors.pdf_failure.process_stopped"))
            if not data:
                raise ValueError(_("messages.errors.pdf_failure.empty"))
            images_data.append({
                "orig_name": "page_0001.jpg", "name": "page_0001.jpg",
                "bytes": data, "is_image": True,
                "is_dir": False, "extension": ".jpg",
            })
            # Manifest pour que _save_state_for_file sache ré-extraire toutes les pages
            images_data.append({
                "_pdf_path": filepath,
                "is_image": False, "is_dir": False,
                "orig_name": "__pdf_manifest__", "name": "__pdf_manifest__",
                "bytes": None, "extension": "",
            })

        return images_data

    def _save_state_for_file(self, filepath, state, progress_cb=None, cancel_event=None):
        """Sauvegarde le CBZ modifié sur disque. Supprime l'original si non-CBZ et permanent_delete.

        Un échec d'écriture lève une exception : _SaveWorker la transmet par son
        signal error, le fichier est compté en erreur dans le résumé du lot et
        l'original n'est pas supprimé.

        Rend la raison (message brut) pour laquelle l'original d'une conversion
        n'a pas pu être supprimé, None sinon : le CBZ est écrit et vérifié, le
        fichier est compté comme traité, et _on_save_done ajoute cette raison
        aux erreurs du lot (sur le thread de l'interface, jamais depuis le
        worker) — sinon l'utilisateur garderait l'original à côté du CBZ sans
        le savoir.

        cancel_event (threading.Event) : testé avant chaque fichier écrit dans
        le ZIP et pendant l'attente du process PDF ; posé, il lève
        _SaveCancelled — temporaire supprimé, original intact."""
        def _check_cancel():
            if cancel_event is not None and cancel_event.is_set():
                raise _SaveCancelled()
        if not state.modified:
            return
        if not filepath.lower().endswith('.cbz'):
            return
        import zipfile

        MANIFEST_NAMES = {
            "__cbz_manifest__", "__cbr_manifest__", "__cbt_manifest__",
            "__cb7_manifest__", "__pdf_manifest__",
        }

        def _get_manifest(tag):
            return next((e for e in state.images_data if e.get("orig_name") == tag), None)

        cbz_manifest = _get_manifest("__cbz_manifest__")
        cbr_manifest = _get_manifest("__cbr_manifest__")
        cbt_manifest = _get_manifest("__cbt_manifest__")
        cb7_manifest = _get_manifest("__cb7_manifest__")
        pdf_manifest = _get_manifest("__pdf_manifest__")

        # Conversion d'un CBR/CBT/CB7/PDF sans manifeste : ses pages n'ont pas
        # été lues, le CBZ ne contiendrait que ComicInfo.xml et l'original
        # serait supprimé ensuite. Rien n'est écrit.
        orig_path = getattr(state, '_original_filepath', None)
        if orig_path and orig_path != filepath \
                and not (cbr_manifest or cbt_manifest or cb7_manifest or pdf_manifest):
            raise ValueError(_("messages.errors.batch_no_source_pages"))

        def _write_non_manifest(zf):
            for entry in state.images_data:
                if entry.get("orig_name") in MANIFEST_NAMES:
                    continue
                if entry.get("bytes") is None or entry.get("is_dir"):
                    continue
                zf.writestr(entry["orig_name"], entry["bytes"])

        # Noms déjà écrits dans le CBZ d'une conversion (casse ignorée)
        written_names = set()

        def _write_xml_only(zf):
            for entry in state.images_data:
                if entry.get("orig_name") in MANIFEST_NAMES:
                    continue
                if entry.get("bytes") is None or entry.get("is_dir"):
                    continue
                if entry.get("is_image"):
                    continue
                zf.writestr(entry["orig_name"], entry["bytes"])
                written_names.add(entry["orig_name"].lower())

        def _converted_name(member_name):
            """Nom d'un membre de la source (CBR/CBT/CB7) dans le CBZ produit :
            rien qui sorte du dossier d'extraction d'un autre lecteur
            (contained_member_name), et jamais un nom déjà écrit — deux
            membres homonymes donneraient deux entrées ZIP de même nom, dont
            une seule lisible, alors que la source est supprimée ensuite."""
            from modules.qt.archive_names import contained_member_name
            name = contained_member_name(member_name) or "fichier"
            stem, ext = os.path.splitext(name)
            # Homonyme du ComicInfo.xml : nommé comme à son arrivée dans un
            # document (comic_info.name_incoming_comic_infos), ComicInfo_0002.xml…
            pattern = "{}_{:04d}{}" if _is_comicinfo_name(name) else "{}_{}{}"
            candidate, n = name, 2
            while candidate.lower() in written_names:
                candidate = pattern.format(stem, n, ext)
                n += 1
            written_names.add(candidate.lower())
            return candidate

        old_comicinfo_skipped = []

        def _is_old_comicinfo(member_name):
            """Ancien ComicInfo.xml de la source, remplacé par celui que
            _write_xml_only vient d'écrire : le premier seulement. Un second
            ComicInfo.xml à la racine est un fichier annexe, recopié sous le
            nom ComicInfo_0002.xml (_converted_name) — l'original est supprimé
            ensuite, il serait perdu."""
            if old_comicinfo_skipped or not _is_comicinfo_name(member_name):
                return False
            old_comicinfo_skipped.append(member_name)
            return True

        from modules.qt.utils import make_temp_path, release_temp_path
        tmp_path = make_temp_path(os.path.dirname(os.path.abspath(filepath)))
        try:
            from modules.qt.utils import zip_compression_kwargs
            from modules.qt.config_manager import get_config_manager as _gcm
            with zipfile.ZipFile(tmp_path, 'w', **zip_compression_kwargs(_gcm().get_zip_compression_level())) as raw_zf:
                zf = _CancellableZip(raw_zf, _check_cancel)
                if cbz_manifest:
                    _write_non_manifest(zf)
                    # Recopier toutes les entrées de l'original sauf couverture et XML déjà écrits
                    # (seul le premier membre de ce nom est remplacé : un homonyme est recopié).
                    written = {e["orig_name"] for e in state.images_data
                               if e.get("orig_name") not in MANIFEST_NAMES and e.get("bytes") is not None}
                    from modules.qt.archive_loader import _check_member_size
                    with open_zip(cbz_manifest["_cbz_path"]) as src:
                        # Commentaire d'archive (métadonnées ComicBookInfo) conservé
                        raw_zf.comment = src.comment
                        for info in src.infolist():
                            if info.filename in written:
                                written.discard(info.filename)
                                continue
                            # Par ZipInfo : read(nom) rendrait le dernier homonyme.
                            # Taille contrôlée avant lecture : un membre trop
                            # grand fait échouer l'écriture, original intact.
                            _check_member_size(info.file_size)
                            zf.writestr(info.filename, src.read(info))

                # CBR/CBT/CB7 : l'original est supprimé ensuite, tous ses
                # fichiers sont donc recopiés (pages de tout format, .nfo,
                # .txt, ComicInfo.xml d'un dossier...), sauf l'ancien
                # ComicInfo.xml remplacé par celui que _write_xml_only vient
                # d'écrire (_is_old_comicinfo).
                elif cbr_manifest:
                    _write_xml_only(zf)
                    import rarfile
                    from modules.qt.archive_loader import _check_member_size
                    with rarfile.RarFile(cbr_manifest["_cbr_path"]) as rf:
                        for info in sorted(rf.infolist(), key=lambda i: i.filename):
                            if info.is_dir() or _is_old_comicinfo(info.filename):
                                continue
                            # Taille annoncée contrôlée avant lecture (MAX_MEMBER_SIZE)
                            _check_member_size(info.file_size)
                            zf.writestr(_converted_name(info.filename), rf.read(info))

                elif cbt_manifest:
                    _write_xml_only(zf)
                    import tarfile
                    from modules.qt.archive_loader import _tar_info_read
                    with tarfile.open(cbt_manifest["_cbt_path"], 'r:*') as tf:
                        for m in sorted(tf.getmembers(), key=lambda x: x.name):
                            if not m.isfile() or _is_old_comicinfo(m.name):
                                continue
                            # Nom sans le préfixe "./" (sinon faux sous-dossier
                            # « . » dans le CBZ) ; taille annoncée contrôlée
                            # avant lecture (_tar_info_read, MAX_MEMBER_SIZE).
                            zf.writestr(_converted_name(m.name), _tar_info_read(tf, m))

                elif cb7_manifest:
                    _write_xml_only(zf)
                    from modules.qt.archive_loader import _read_7z_file
                    for name in cb7_manifest["_cb7_all_names"]:
                        if _is_old_comicinfo(name):
                            continue
                        zf.writestr(_converted_name(name), _read_7z_file(cb7_manifest["_cb7_path"], name))

                elif pdf_manifest:
                    _write_xml_only(zf)
                    import modules.qt.pdf_loading_qt as _pdfmod

                    pdf_path = pdf_manifest["_pdf_path"]
                    # Process dédié à cette écriture (pas partagé avec les panneaux
                    # ni un autre traitement), détruit systématiquement à la fin.
                    pdf_process, pdf_in_q, pdf_out_conn = _pdfmod._spawn_pdf_process()
                    try:
                        pdf_in_q.put(('run_merge', pdf_path, 0, ""))

                        while True:
                            # Lecture commune : un process mort (canal fermé)
                            # donne le message traduit, jamais « EOFError »
                            msg = _pdfmod.read_pdf_message(pdf_out_conn, pdf_process, 0.1)
                            if msg is None:
                                _check_cancel()
                                continue
                            kind = msg[0]
                            if kind == 'process_stopped':
                                raise ValueError(_("messages.errors.pdf_failure.process_stopped"))
                            if kind == '_debug':
                                continue
                            elif kind == 'total':
                                pass
                            elif kind == 'merge_page':
                                _kind, filename, img_data, _used_dpi = msg
                                zf.writestr(filename, img_data)
                            elif kind == 'page_error':
                                # CBZ incomplet : abandon, le PDF d'origine
                                # (qui serait supprimé ensuite) est conservé.
                                raise ValueError(_pdfmod.pdf_page_error_text(msg))
                            elif kind == 'progress':
                                _kind, percent, _cur, _tot = msg
                                if progress_cb:
                                    progress_cb(percent)
                            elif kind == 'done':
                                break
                            elif kind == 'error':
                                raise ValueError(msg[1])
                            elif kind == 'password_error':
                                raise ValueError(_("messages.errors.pdf_failure.password"))
                            elif kind == 'empty_pdf':
                                raise ValueError(_("messages.errors.pdf_failure.empty"))
                    finally:
                        _pdfmod._kill_pdf_process(pdf_process, pdf_in_q, pdf_out_conn)

                else:
                    _write_non_manifest(zf)

            # Écriture réussie : remplacer l'original par le tmp, une fois son
            # contenu sur le disque (fsync_file) et sa structure contrôlée,
            # comme pour un enregistrement depuis un panneau
            from modules.qt.utils import (check_written_cbz_structure, fsync_file,
                                          replace_file_retrying)
            fsync_file(tmp_path)
            check_written_cbz_structure(tmp_path, zf.written_files, shown_path=filepath)
            replace_file_retrying(tmp_path, filepath)

        except BaseException:
            # Nettoyage du fichier tmp en cas d'erreur ou d'interruption, sauf
            # s'il est devenu la seule copie du fichier (is_kept_temp)
            from modules.qt.utils import is_kept_temp
            try:
                if os.path.exists(tmp_path) and not is_kept_temp(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise
        finally:
            release_temp_path(tmp_path)

        # Supprimer l'original non-CBZ, une fois le CBZ écrit relu sans erreur.
        # Sinon ce nouveau CBZ (nom libre, créé par cette écriture) est retiré
        # et WrittenFileCheckError remonte comme un échec d'écriture : le
        # fichier n'est pas compté comme traité et l'original reste en place.
        orig = getattr(state, '_original_filepath', None)
        if orig and orig != filepath:
            from modules.qt.utils import WrittenFileCheckError, verify_written_cbz
            try:
                verify_written_cbz(filepath)
            except WrittenFileCheckError:
                try:
                    os.remove(filepath)
                except OSError:
                    pass
                raise
            try:
                if self._permanent_delete:
                    os.remove(orig)
                else:
                    from modules.qt.utils import send_to_trash
                    send_to_trash(orig)
            except Exception as e:
                return str(e)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Fenêtre de résumé
# ═══════════════════════════════════════════════════════════════════════════════

class _MetadataSummaryDialog(QDialog):

    def __init__(self, parent, data: dict):
        super().__init__(parent)
        self._data = data
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        self._lbl_done    = QLabel()
        self._lbl_skipped = QLabel()
        self._lbl_errors  = QLabel()
        for lbl in (self._lbl_done, self._lbl_skipped, self._lbl_errors):
            lbl.setWordWrap(True)
            lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(lbl)

        self._log_lbl = None
        if data.get("log_error"):
            self._log_lbl = QLabel()
            self._log_lbl.setTextFormat(Qt.PlainText)
            self._log_lbl.setWordWrap(True)
            self._log_lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(self._log_lbl)
        elif data.get("log_path"):
            self._log_lbl = QLabel()
            self._log_lbl.setWordWrap(True)
            self._log_lbl.setAlignment(Qt.AlignCenter)
            self._log_lbl.setStyleSheet("color: #4A9EFF;")
            from PySide6.QtCore import Qt as _Qt
            self._log_lbl.setCursor(_Qt.PointingHandCursor)
            self._log_lbl.setTextInteractionFlags(_Qt.TextBrowserInteraction)
            lp = data["log_path"]
            self._log_lbl.linkActivated.connect(lambda _, p=lp: _open_log(p))
            from modules.qt.utils import setup_path_label_context_menu
            setup_path_label_context_menu(self._log_lbl, lambda p=lp: p, lambda p=lp: _open_log(p))
            layout.addWidget(self._log_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep)

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
        _connect_lang(self, lambda _: self._retranslate())
        self._ok_btn.setFocus()
        self._center_parent = parent

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
            f"QFrame[frameShape='4'] {{ color: {theme['separator']}; }}"
        )
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        d = self._data
        self.setWindowTitle(_wt("dialogs.batch_metadata.complete_title"))
        self._lbl_done.setText(
            _("dialogs.batch_metadata.done_count").format(done=d["done"], total=d["total"]))
        self._lbl_done.setFont(font)
        self._lbl_skipped.setText(
            _("dialogs.batch_metadata.skipped_count").format(skipped=d["skipped"]))
        self._lbl_skipped.setFont(font)
        if d.get("errors", 0) > 0:
            self._lbl_errors.setText(
                _("dialogs.batch_metadata.errors_count").format(count=d["errors"]))
            self._lbl_errors.setStyleSheet(f"color: {theme['text']};")
        else:
            self._lbl_errors.setText("")
        self._lbl_errors.setFont(font)

        if self._log_lbl is not None and d.get("log_error"):
            self._log_lbl.setText(_("dialogs.batch_log_failed", error=d["log_error"]))
            self._log_lbl.setStyleSheet(f"color: {theme['text']};")
            self._log_lbl.setFont(font)
        elif self._log_lbl is not None:
            see_log = _("dialogs.see_log")
            err_txt = _("dialogs.batch_metadata.errors_count").format(count=d["errors"])
            lp = d["log_path"]
            self._log_lbl.setText(f'<a href="file:///{lp}">{err_txt} — {see_log}</a>')
            self._log_lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)


def _open_log(path):
    import subprocess
    try:
        subprocess.Popen(["notepad.exe", path])
    except Exception:
        import os as _os
        _os.startfile(path)


def _show_metadata_summary(parent, data: dict):
    dlg = _MetadataSummaryDialog(parent, data)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


# ═══════════════════════════════════════════════════════════════════════════════
# Confirmation d'arrêt du batch (bouton Annuler cliqué pendant le traitement)
# ═══════════════════════════════════════════════════════════════════════════════

class _CancelConfirmDialog(QDialog):

    def __init__(self, parent):
        super().__init__(parent)
        self.setModal(False)
        self.setFixedWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg_lbl)

        layout.addSpacing(8)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep)

        layout.addSpacing(4)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._continue_btn = QPushButton()
        self._continue_btn.setFixedWidth(140)
        self._continue_btn.setDefault(True)
        self._continue_btn.clicked.connect(self._on_continue)
        self._stop_btn = QPushButton()
        self._stop_btn.setFixedWidth(140)
        self._stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(self._continue_btn)
        btn_row.addSpacing(16)
        btn_row.addWidget(self._stop_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.stopped = False
        self.rejected.connect(self._on_continue)
        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._continue_btn.setFocus()

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
            f"QFrame[frameShape='4'] {{ color: {theme['separator']}; }}"
        )
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )

        self.setWindowTitle(_wt("dialogs.batch_metadata.cancel_confirm_title"))
        self._msg_lbl.setText(_("dialogs.batch_metadata.cancel_confirm_message"))
        self._msg_lbl.setFont(font)

        self._continue_btn.setText(_("buttons.continue"))
        self._continue_btn.setFont(font)
        self._continue_btn.setStyleSheet(btn_style)
        self._stop_btn.setText(_("dialogs.batch_metadata.cancel_confirm_stop_button"))
        self._stop_btn.setFont(font)
        self._stop_btn.setStyleSheet(btn_style)

    def _on_continue(self):
        self.stopped = False
        self.accept()

    def _on_stop(self):
        self.stopped = True
        self.accept()


def show_cancel_confirm_dialog(parent, on_result):
    """Affiche la confirmation d'arrêt du batch.

    on_result : callable(stop: bool) appelé une fois l'utilisateur répondu
    (stop=True si le batch doit être arrêté, False pour continuer).
    """
    from modules.qt.dialogs_qt import position_dialog_on_parent
    dlg = _CancelConfirmDialog(parent)
    dlg.finished.connect(lambda _: on_result(dlg.stopped))
    position_dialog_on_parent(dlg, parent)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


# ═══════════════════════════════════════════════════════════════════════════════
# Fonction publique
# ═══════════════════════════════════════════════════════════════════════════════

def _files_in_running_batch(files) -> bool:
    """True si un import en lot non terminé traite déjà l'un de ces fichiers.
    Deux imports sur le même fichier le convertiraient deux fois (« X.cbz »
    puis « X (1).cbz ») et tenteraient deux fois de supprimer l'original."""
    def _norm(path):
        return os.path.normcase(os.path.abspath(path))
    wanted = {_norm(f) for f in files}
    for orchestrator in _active_orchestrators:
        if getattr(orchestrator, '_ended', False):
            continue
        if wanted & {_norm(f) for f in orchestrator._files}:
            return True
    # Confirmation encore affichée pour ces fichiers : l'import n'a pas
    # démarré, mais il peut l'être à tout moment.
    for pending in _pending_confirmations:
        if wanted & {_norm(f) for f in pending}:
            return True
    return False


def _show_already_running(parent):
    from modules.qt.dialogs_qt import MsgDialog
    MsgDialog(parent,
              "dialogs.batch_metadata.already_running_title",
              "dialogs.batch_metadata.already_running_message").show_nonmodal()


def _show_other_batch_running(parent):
    from modules.qt.dialogs_qt import MsgDialog
    MsgDialog(parent,
              "dialogs.batch_running.title",
              "dialogs.batch_running.message").show_nonmodal()


def show_batch_metadata_dialog(parent, files: list, dirs: list, batch_callbacks: dict):
    """
    Affiche la fenêtre de confirmation pour l'import de métadonnées en lot.
    Si confirmé, vérifie la clé API et lance l'orchestrateur.
    """
    if _files_in_running_batch(files):
        _show_already_running(parent)
        return
    from modules.qt.batch_claims import BatchClaims, files_in_running_batch
    # Lot de conversion ou de recompression en cours sur ces fichiers
    if files_in_running_batch(files):
        _show_other_batch_running(parent)
        return
    dlg = _MetadataConfirmDialog(parent, files, dirs)
    # Fichiers réservés tant que la confirmation est affichée ; libérés à sa
    # fermeture, juste avant _on_confirm_done (connecté après) qui démarre
    # l'import et refait le contrôle.
    _pending_confirmations.append(files)

    def _release_confirmation():
        _pending_confirmations[:] = [p for p in _pending_confirmations if p is not files]

    dlg.finished.connect(lambda _r: _release_confirmation())

    def _on_confirm_done():
        if not dlg.confirmed:
            return

        from modules.qt.config_manager import get_config_manager
        api_key = get_config_manager().get_comicvine_api_key()

        if not api_key:
            # Pas de clé API → ouvrir le dialog de saisie d'abord
            from modules.qt.comicvine_apikey_dialog_qt import show_apikey_dialog
            key_dlg = show_apikey_dialog(parent, get_config_manager())

            def _on_key_done():
                new_key = get_config_manager().get_comicvine_api_key()
                if new_key:
                    _start_batch(new_key)

            key_dlg.finished.connect(lambda _: _on_key_done())
        else:
            _start_batch(api_key)

    def _start_batch(api_key):
        # Confirmation non modale : un autre import a pu démarrer sur ces
        # fichiers pendant qu'elle était affichée.
        if _files_in_running_batch(files):
            _show_already_running(parent)
            return
        claims = BatchClaims.start(files)
        if claims is None:
            _show_other_batch_running(parent)
            return
        orchestrator = _BatchMetadataOrchestrator(
            parent, files, batch_callbacks, claims,
            permanent_delete=dlg.permanent_delete,
            skip_existing=dlg.skip_existing,
        )
        # Garder une référence module-level pour éviter la destruction par le GC
        _active_orchestrators.append(orchestrator)
        _extra_cb = batch_callbacks.get('on_batch_complete')
        def _on_complete():
            if orchestrator in _active_orchestrators:
                _active_orchestrators.remove(orchestrator)
            if _extra_cb:
                _extra_cb()
        orchestrator._on_batch_complete = _on_complete
        orchestrator.start()

    dlg.finished.connect(lambda _: _on_confirm_done())
    dlg.show()
