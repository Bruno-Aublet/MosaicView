"""
batch_metadata_dialog_qt.py — Confirmation + orchestration du batch d'import de métadonnées.
"""

import os

# Référence module-level pour empêcher le GC de détruire l'orchestrateur en cours de batch
_active_orchestrators = []

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QCheckBox,
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal as _Signal

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.archive_loader import _natural_sort_key


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
        try:
            cbz_path, state = self._orc._load_state_for_file(self._path)
            self.done.emit(cbz_path, state)
        except Exception as e:
            self.error.emit(str(e))


class _SaveWorker(QThread):
    done     = _Signal()
    error    = _Signal(str)
    progress = _Signal(int)   # pourcentage conversion PDF

    def __init__(self, orchestrator, filepath, state):
        super().__init__()
        self._orc      = orchestrator
        self._filepath = filepath
        self._state    = state

    def run(self):
        try:
            self._orc._save_state_for_file(self._filepath, self._state,
                                           progress_cb=lambda pct: self.progress.emit(pct))
            self.done.emit()
        except Exception as e:
            self.error.emit(str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# Orchestrateur batch
# ═══════════════════════════════════════════════════════════════════════════════

class _BatchMetadataOrchestrator:
    """
    Gère la séquence batch : ouvre la fenêtre ComicVine pour chaque fichier,
    avec caches partagés entre fichiers.
    """

    def __init__(self, parent, files: list, batch_callbacks: dict, permanent_delete: bool = False, skip_existing: bool = False):
        self._parent          = parent
        self._files           = files
        self._callbacks       = batch_callbacks
        self._permanent_delete = permanent_delete
        self._skip_existing   = skip_existing
        self._index           = 0
        self._current_dlg     = None
        self._worker          = None
        self._orphan_workers  = []   # workers abandonnés mais encore en cours
        self._pending_timer   = None
        self._seq             = 0

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
        if self._index >= len(self._files):
            if self._current_dlg:
                self._current_dlg.close()
            return

        orig_filepath = self._files[self._index]

        # Skip si l'option est cochée et que le fichier CBZ contient déjà un ComicInfo.xml
        if self._skip_existing and orig_filepath.lower().endswith('.cbz'):
            try:
                import zipfile as _zf
                with _zf.ZipFile(orig_filepath, 'r') as _z:
                    if any(n.lower().endswith('comicinfo.xml') for n in _z.namelist()):
                        self._skipped_count += 1
                        self._index += 1
                        self._load_and_open(first=first)
                        return
            except Exception:
                pass

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
        api_key = get_config_manager().get('comicvine_api_key', '').strip()
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
        )

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
        self._index += 1
        if self._index >= len(self._files):
            self._retire_current_worker()
            self._show_summary()
            return
        self._lock_nav_buttons(True)
        self._reset_dialog()
        self._load_and_open(first=False)

    def _rescue_dlg_workers(self, dlg):
        """Détache les workers internes du dialog et garde les références vivantes."""
        from modules.qt.comicvine_dialog_qt import _ComicVineDialog
        for attr in ('_worker', '_image_worker'):
            w = getattr(dlg, attr, None)
            if w is None:
                continue
            for sig in ('finished', 'error', 'done'):
                try:
                    getattr(w, sig).disconnect()
                except (RuntimeError, AttributeError):
                    pass
            # Détacher du parent Qt AVANT de stocker la référence Python
            try:
                w.setParent(None)
            except RuntimeError:
                pass
            # Garder la référence dans la liste de classe du dialog jusqu'à fin naturelle
            _ComicVineDialog._dying_workers.append(w)
            w.finished.connect(
                lambda ww=w: _ComicVineDialog._dying_workers.remove(ww)
                if ww in _ComicVineDialog._dying_workers else None)
            setattr(dlg, attr, None)

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
        self._orphan_workers.append(orphan)
        orphan.finished.connect(
            lambda w=orphan: self._orphan_workers.remove(w) if w in self._orphan_workers else None)
        self._worker = None

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
        self._save_worker.done.connect(self._on_save_done)
        self._save_worker.progress.connect(self._on_save_progress)
        def _on_save_error(msg):
            fname = os.path.basename(filepath)
            self._errors.append((fname, msg))
            self._retire_save_worker()
            self._show_converting_overlay(False)
            self._done_count += 1
            self._on_next()
        self._save_worker.error.connect(_on_save_error)
        self._save_worker.start()

    def _on_save_done(self):
        self._show_converting_overlay(False)
        self._done_count += 1
        self._retire_save_worker()
        self._on_next()

    def _show_summary(self):
        """Ferme le dialog ComicVine et affiche la fenêtre de résumé."""
        if self._current_dlg:
            self._rescue_dlg_workers(self._current_dlg)
            self._current_dlg.close()

        # Écrire le log si erreurs
        log_path = None
        if self._errors:
            try:
                from datetime import datetime
                now = datetime.now()
                log_filename = f"Log_metadata_{now.strftime('%Y_%m_%d_%H_%M')}.txt"
                mosaicview_temp = self._callbacks['get_mosaicview_temp_dir']()
                log_path = os.path.join(mosaicview_temp, log_filename)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write("MosaicView - Batch Metadata Import Log\n")
                    lf.write(f"Date: {now.strftime('%Y-%m-%d %H:%M')}\n")
                    lf.write(f"Total files: {len(self._files)}\n")
                    lf.write(f"Processed: {self._done_count}\n")
                    lf.write(f"Skipped: {self._skipped_count}\n")
                    lf.write(f"Errors: {len(self._errors)}\n")
                    lf.write(f"\n{'='*60}\nError details:\n{'='*60}\n\n")
                    for fname, msg in self._errors:
                        lf.write(f"  - {fname}: {msg}\n")
            except Exception as e:
                print(f"[batch] erreur log: {e}")
                log_path = None

        summary_data = {
            "total":   len(self._files),
            "done":    self._done_count,
            "skipped": self._skipped_count,
            "errors":  len(self._errors),
            "log_path": log_path,
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

        new_state = _state_mod.AppState()
        new_state.current_file    = orig_filepath
        new_state.images_data     = []
        new_state.comic_metadata  = {}
        new_state.modified        = False
        new_state._original_filepath = orig_filepath

        ext = os.path.splitext(orig_filepath)[1].lower()

        if ext == '.cbz':
            cbz_filepath = orig_filepath
            with zipfile.ZipFile(orig_filepath, 'r') as zf:
                all_names = zf.namelist()
                cover_name = next(
                    (n for n in sorted(all_names, key=_natural_sort_key)
                     if n.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif'))),
                    None
                )
                # Couverture uniquement
                if cover_name:
                    data = zf.read(cover_name)
                    new_state.images_data.append({
                        "orig_name": cover_name, "name": cover_name,
                        "bytes": data, "is_image": True,
                        "is_dir": False,
                        "extension": os.path.splitext(cover_name)[1].lower(),
                    })
                # ComicInfo.xml uniquement
                xml_name = next(
                    (n for n in all_names if n.lower().endswith('.xml') and 'comicinfo' in n.lower()),
                    None
                )
                if xml_name:
                    xml_data = zf.read(xml_name)
                    new_state.images_data.append({
                        "orig_name": xml_name, "name": xml_name,
                        "bytes": xml_data, "is_image": False,
                        "is_dir": False,
                        "extension": ".xml",
                    })
                    try:
                        new_state.comic_metadata = parse_comic_info_xml(xml_data) or {}
                    except Exception:
                        pass
                # Manifest pour que _save_state_for_file réécrive le CBZ complet
                new_state.images_data.append({
                    "_cbz_path": orig_filepath,
                    "is_image": False, "is_dir": False,
                    "orig_name": "__cbz_manifest__", "name": "__cbz_manifest__",
                    "bytes": None, "extension": "",
                })

        else:
            # Fichier non-CBZ : extraire les images via les loaders existants
            cbz_filepath = os.path.splitext(orig_filepath)[0] + '.cbz'
            new_state.current_file = cbz_filepath
            try:
                images_data = self._extract_non_cbz(orig_filepath, ext)
                new_state.images_data = images_data
            except Exception as e:
                print(f"Erreur extraction {orig_filepath}: {e}")

        return cbz_filepath, new_state

    def _extract_non_cbz(self, filepath, ext):
        """Extrait les images d'un fichier non-CBZ en liste d'entries."""
        images_data = []

        if ext == '.cbr':
            import rarfile
            with rarfile.RarFile(filepath) as rf:
                all_names = sorted(
                    n for n in rf.namelist()
                    if n.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'))
                )
            if all_names:
                with rarfile.RarFile(filepath) as rf:
                    data = rf.read(all_names[0])
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
            with tarfile.open(filepath, 'r:*') as tf:
                img_members = sorted(
                    (m for m in tf.getmembers()
                     if m.isfile() and m.name.lower().endswith(
                         ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'))),
                    key=lambda m: m.name
                )
                if img_members:
                    data = tf.extractfile(img_members[0]).read()
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
            all_names = sorted(n for n in _list_7z_files(filepath)
                               if n.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')))
            # On ne charge que la couverture (1ère image) — la conversion complète
            # se fera dans _save_state_for_file via re-extraction
            if all_names:
                first = all_names[0]
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
            import fitz
            doc = fitz.open(filepath)
            if doc.is_closed or len(doc) == 0:
                doc.close()
                raise ValueError("PDF vide ou illisible")
            # On ne rasterise que la première page (couverture) pour l'aperçu.
            # La conversion complète se fera dans _save_state_for_file.
            page = doc[0]
            pix  = page.get_pixmap(dpi=96)
            data = pix.tobytes("jpeg")
            doc.close()
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

    def _save_state_for_file(self, filepath, state, progress_cb=None):
        """Sauvegarde le CBZ modifié sur disque. Supprime l'original si non-CBZ et permanent_delete."""
        if not state.modified:
            return
        if not filepath.lower().endswith('.cbz'):
            return
        try:
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

            def _write_non_manifest(zf):
                """Écrit les entrées avec bytes (couverture + XML metadata)."""
                for entry in state.images_data:
                    if entry.get("orig_name") in MANIFEST_NAMES:
                        continue
                    if entry.get("bytes") is None or entry.get("is_dir"):
                        continue
                    zf.writestr(entry["orig_name"], entry["bytes"])

            def _write_xml_only(zf):
                """Écrit uniquement les entrées non-image (ComicInfo.xml)."""
                for entry in state.images_data:
                    if entry.get("orig_name") in MANIFEST_NAMES:
                        continue
                    if entry.get("bytes") is None or entry.get("is_dir"):
                        continue
                    if entry.get("is_image"):
                        continue
                    zf.writestr(entry["orig_name"], entry["bytes"])

            def _cover_name():
                return next(
                    (e["orig_name"] for e in state.images_data if e.get("is_image")),
                    None
                )

            with zipfile.ZipFile(filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
                if cbz_manifest:
                    _write_non_manifest(zf)
                    # Recopier toutes les entrées de l'original sauf couverture et XML déjà écrits
                    written = {e["orig_name"] for e in state.images_data
                               if e.get("orig_name") not in MANIFEST_NAMES and e.get("bytes") is not None}
                    with zipfile.ZipFile(cbz_manifest["_cbz_path"], 'r') as src:
                        for name in src.namelist():
                            if name not in written:
                                zf.writestr(name, src.read(name))

                elif cbr_manifest:
                    _write_xml_only(zf)
                    import rarfile
                    with rarfile.RarFile(cbr_manifest["_cbr_path"]) as rf:
                        for name in sorted(rf.namelist()):
                            if name.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')):
                                zf.writestr(name, rf.read(name))

                elif cbt_manifest:
                    _write_xml_only(zf)
                    import tarfile
                    with tarfile.open(cbt_manifest["_cbt_path"], 'r:*') as tf:
                        for m in sorted(tf.getmembers(), key=lambda x: x.name):
                            if not m.isfile():
                                continue
                            if not m.name.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')):
                                continue
                            data = tf.extractfile(m).read()
                            if data:
                                zf.writestr(m.name, data)

                elif cb7_manifest:
                    _write_xml_only(zf)
                    from modules.qt.archive_loader import _read_7z_file
                    for name in cb7_manifest["_cb7_all_names"]:
                        data = _read_7z_file(cb7_manifest["_cb7_path"], name)
                        if data:
                            zf.writestr(name, data)

                elif pdf_manifest:
                    _write_xml_only(zf)
                    import modules.qt.pdf_loading_qt as _pdfmod
                    from modules.qt.archive_loader import _natural_sort_key as _nsk

                    pdf_path = pdf_manifest["_pdf_path"]
                    _pdfmod._ensure_merge_process()
                    _pdfmod._merge_in_q.put(('run_merge', pdf_path, 0, ""))

                    while True:
                        if not _pdfmod._merge_out_conn.poll(0.1):
                            if _pdfmod._merge_process is None or not _pdfmod._merge_process.is_alive():
                                raise ValueError("PDF merge process terminated unexpectedly")
                            continue
                        msg = _pdfmod._merge_out_conn.recv()
                        kind = msg[0]
                        if kind == '_debug':
                            continue
                        elif kind == 'total':
                            pass
                        elif kind == 'merge_page':
                            _, filename, img_data, _used_dpi = msg
                            zf.writestr(filename, img_data)
                        elif kind == 'progress':
                            _, percent, _cur, _tot = msg
                            if progress_cb:
                                progress_cb(percent)
                        elif kind == 'done':
                            break
                        elif kind == 'error':
                            raise ValueError(msg[1])
                        elif kind == 'password_error':
                            raise ValueError("PDF protégé par mot de passe")
                        elif kind == 'empty_pdf':
                            raise ValueError("PDF vide")

                else:
                    _write_non_manifest(zf)
        except Exception as e:
            print(f"Erreur sauvegarde {filepath}: {e}")
            return

        # Supprimer l'original non-CBZ
        orig = getattr(state, '_original_filepath', None)
        if orig and orig != filepath:
            try:
                if self._permanent_delete:
                    os.remove(orig)
                else:
                    try:
                        from send2trash import send2trash
                        send2trash(orig)
                    except ImportError:
                        import ctypes
                        from ctypes import wintypes
                        class _SHFILEOPSTRUCT(ctypes.Structure):
                            _fields_ = [
                                ("hwnd",   wintypes.HWND),
                                ("wFunc",  wintypes.UINT),
                                ("pFrom",  wintypes.LPCWSTR),
                                ("pTo",    wintypes.LPCWSTR),
                                ("fFlags", wintypes.WORD),
                                ("fAnyOperationsAborted", wintypes.BOOL),
                                ("hNameMappings", ctypes.c_void_p),
                                ("lpszProgressTitle", wintypes.LPCWSTR),
                            ]
                        shell32 = ctypes.windll.shell32
                        op = _SHFILEOPSTRUCT()
                        op.hwnd   = 0
                        op.wFunc  = 3        # FO_DELETE
                        op.pFrom  = orig + "\0\0"
                        op.fFlags = 0x0040   # FOF_ALLOWUNDO → corbeille
                        shell32.SHFileOperationW(ctypes.byref(op))
            except Exception as e:
                print(f"Erreur suppression {orig}: {e}")


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
        if data.get("log_path"):
            self._log_lbl = QLabel()
            self._log_lbl.setWordWrap(True)
            self._log_lbl.setAlignment(Qt.AlignCenter)
            self._log_lbl.setStyleSheet("color: #4A9EFF;")
            from PySide6.QtCore import Qt as _Qt
            self._log_lbl.setCursor(_Qt.PointingHandCursor)
            self._log_lbl.setTextInteractionFlags(_Qt.TextBrowserInteraction)
            lp = data["log_path"]
            self._log_lbl.linkActivated.connect(lambda _, p=lp: _open_log(p))
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

        if self._log_lbl is not None:
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
# Fonction publique
# ═══════════════════════════════════════════════════════════════════════════════

def show_batch_metadata_dialog(parent, files: list, dirs: list, batch_callbacks: dict):
    """
    Affiche la fenêtre de confirmation pour l'import de métadonnées en lot.
    Si confirmé, vérifie la clé API et lance l'orchestrateur.
    """
    dlg = _MetadataConfirmDialog(parent, files, dirs)

    def _on_confirm_done():
        if not dlg.confirmed:
            return

        from modules.qt.config_manager import get_config_manager
        api_key = get_config_manager().get('comicvine_api_key', '').strip()

        if not api_key:
            # Pas de clé API → ouvrir le dialog de saisie d'abord
            from modules.qt.comicvine_apikey_dialog_qt import show_apikey_dialog
            key_dlg = show_apikey_dialog(parent, get_config_manager())

            def _on_key_done():
                new_key = get_config_manager().get('comicvine_api_key', '').strip()
                if new_key:
                    _start_batch(new_key)

            key_dlg.finished.connect(lambda _: _on_key_done())
        else:
            _start_batch(api_key)

    def _start_batch(api_key):
        orchestrator = _BatchMetadataOrchestrator(
            parent, files, batch_callbacks,
            permanent_delete=dlg.permanent_delete,
            skip_existing=dlg.skip_existing,
        )
        # Garder une référence module-level pour éviter la destruction par le GC
        _active_orchestrators.append(orchestrator)
        orchestrator._on_batch_complete = lambda: _active_orchestrators.remove(orchestrator) if orchestrator in _active_orchestrators else None
        orchestrator.start()

    dlg.finished.connect(lambda _: _on_confirm_done())
    dlg.show()
