"""
batch_metadata_dialog_qt.py — Confirmation + orchestration du batch d'import de métadonnées.
"""

import os

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QCheckBox,
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal as _Signal

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font


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
        self.confirmed       = False
        self.permanent_delete = False

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

        self._start_btn.setText(_("dialogs.batch_metadata.start_button"))
        self._start_btn.setFont(font)
        self._start_btn.setStyleSheet(btn_style)
        self._cancel_btn.setText(_("buttons.cancel"))
        self._cancel_btn.setFont(font)
        self._cancel_btn.setStyleSheet(btn_style)

    def _on_start(self):
        self.confirmed        = True
        self.permanent_delete = self._chk.isChecked()
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
    done  = _Signal()
    error = _Signal(str)

    def __init__(self, orchestrator, filepath, state):
        super().__init__()
        self._orc      = orchestrator
        self._filepath = filepath
        self._state    = state

    def run(self):
        try:
            self._orc._save_state_for_file(self._filepath, self._state)
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

    def __init__(self, parent, files: list, batch_callbacks: dict, permanent_delete: bool = False):
        self._parent          = parent
        self._files           = files
        self._callbacks       = batch_callbacks
        self._permanent_delete = permanent_delete
        self._index           = 0
        self._current_dlg     = None
        self._worker          = None
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

        # Annuler un timer en attente et ignorer le worker précédent s'il tourne encore
        if self._pending_timer is not None:
            self._pending_timer.stop()
            self._pending_timer = None
        if self._worker is not None and self._worker.isRunning():
            try:
                self._worker.done.disconnect()
            except RuntimeError:
                pass
            try:
                self._worker.error.disconnect()
            except RuntimeError:
                pass
            self._worker.finished.connect(self._worker.deleteLater)
            self._worker.quit()
            self._worker = None

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

    def _show_converting_overlay(self, visible):
        """Affiche/cache l'overlay 'Conversion en cours' sur la page courante."""
        if not self._current_dlg:
            return
        dlg = self._current_dlg
        stack_idx = dlg._stack.currentIndex()
        txt = _("dialogs.batch_cbr.converting_title")
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
        self._update_dialog(state, cbz_filepath)
        # L'overlay est caché automatiquement par set_loading(False) lors du retour à la page 1

    def _on_load_error(self, msg):
        fname = os.path.basename(self._files[self._index]) if self._index < len(self._files) else "?"
        self._errors.append((fname, msg))
        if self._current_dlg:
            self._current_dlg._page1.set_loading(False)
            self._current_dlg._page2.show_loading_overlay(False, 0, 0)
        self._index += 1
        self._load_and_open(first=self._current_dlg is None)

    def _on_skip(self):
        """Appelé sur Ignorer — compte le skip et passe au suivant."""
        self._skipped_count += 1
        self._on_next()

    def _on_next(self):
        """Passe au fichier suivant ou affiche le résumé si batch terminé."""
        self._index += 1
        if self._index >= len(self._files):
            self._show_summary()
            return
        self._reset_dialog()
        self._load_and_open(first=False)

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

        # Lancer la recherche automatiquement
        if initial:
            QTimer.singleShot(100, dlg._page1._on_search_clicked)

    def _on_file_done(self, filepath, state):
        """Lance la sauvegarde en thread avec overlay, puis passe au suivant."""
        orig = getattr(state, '_original_filepath', None)
        is_non_cbz = orig and orig != filepath
        if is_non_cbz:
            self._show_converting_overlay(True)
        self._save_worker = _SaveWorker(self, filepath, state)
        self._save_worker.done.connect(self._on_save_done)
        def _on_save_error(msg):
            fname = os.path.basename(filepath)
            self._errors.append((fname, msg))
            self._on_save_done()
        self._save_worker.error.connect(_on_save_error)
        self._save_worker.start()

    def _on_save_done(self):
        self._show_converting_overlay(False)
        self._done_count += 1
        self._on_next()

    def _show_summary(self):
        """Ferme le dialog ComicVine et affiche la fenêtre de résumé."""
        if self._current_dlg:
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
            try:
                with zipfile.ZipFile(orig_filepath, 'r') as zf:
                    for name in zf.namelist():
                        data     = zf.read(name)
                        is_image = name.lower().endswith(
                            ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif'))
                        is_xml   = name.lower().endswith('.xml')
                        new_state.images_data.append({
                            "orig_name": name, "name": name,
                            "bytes": data, "is_image": is_image,
                            "is_dir": False,
                            "extension": os.path.splitext(name)[1].lower(),
                        })
                        if is_xml and 'comicinfo' in name.lower():
                            try:
                                new_state.comic_metadata = parse_comic_info_xml(data) or {}
                            except Exception:
                                pass
            except Exception as e:
                print(f"Erreur chargement CBZ {orig_filepath}: {e}")

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
            try:
                import rarfile
                with rarfile.RarFile(filepath) as rf:
                    for name in sorted(rf.namelist()):
                        if name.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')):
                            images_data.append({
                                "orig_name": name, "name": name,
                                "bytes": rf.read(name), "is_image": True,
                                "is_dir": False,
                                "extension": os.path.splitext(name)[1].lower(),
                            })
            except Exception as e:
                print(f"Erreur CBR {filepath}: {e}")

        elif ext == '.cbt':
            try:
                import tarfile
                with tarfile.open(filepath, 'r:*') as tf:
                    img_members = sorted(
                        (m for m in tf.getmembers()
                         if m.isfile() and m.name.lower().endswith(
                             ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'))),
                        key=lambda m: m.name
                    )
                    for member in img_members:
                        try:
                            data = tf.extractfile(member).read()
                        except Exception:
                            continue
                        if data:
                            images_data.append({
                                "orig_name": member.name,
                                "name": os.path.basename(member.name),
                                "bytes": data, "is_image": True,
                                "is_dir": False,
                                "extension": os.path.splitext(member.name)[1].lower(),
                            })
            except Exception as e:
                print(f"Erreur CBT {filepath}: {e}")

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
            try:
                import fitz
                doc = fitz.open(filepath)
                if doc.is_closed or len(doc) == 0:
                    doc.close()
                    raise ValueError("PDF vide ou illisible")
                for i, page in enumerate(doc):
                    pix  = page.get_pixmap(dpi=150)
                    data = pix.tobytes("jpeg")
                    name = f"page_{i+1:04d}.jpg"
                    images_data.append({
                        "orig_name": name, "name": name,
                        "bytes": data, "is_image": True,
                        "is_dir": False, "extension": ".jpg",
                    })
                doc.close()
            except Exception as e:
                print(f"Erreur PDF {filepath}: {e}")

        return images_data

    def _save_state_for_file(self, filepath, state):
        """Sauvegarde le CBZ modifié sur disque. Supprime l'original si non-CBZ et permanent_delete."""
        if not state.modified:
            return
        if not filepath.lower().endswith('.cbz'):
            return
        try:
            import zipfile

            # Récupérer le manifest CB7 si présent (on n'a chargé que la couverture)
            manifest = next(
                (e for e in state.images_data if e.get("orig_name") == "__cb7_manifest__"),
                None
            )

            with zipfile.ZipFile(filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
                if manifest:
                    # Écrire d'abord les entrées non-manifest (couverture + XML métadonnées)
                    for entry in state.images_data:
                        if entry.get("orig_name") == "__cb7_manifest__":
                            continue
                        if entry.get("bytes") is None or entry.get("is_dir"):
                            continue
                        zf.writestr(entry["orig_name"], entry["bytes"])
                    # Extraire et écrire toutes les autres images du CB7
                    from modules.qt.archive_loader import _list_7z_files, _read_7z_file
                    cover_name = next(
                        (e["orig_name"] for e in state.images_data
                         if e.get("is_image") and e.get("orig_name") != "__cb7_manifest__"),
                        None
                    )
                    for name in manifest["_cb7_all_names"]:
                        if name == cover_name:
                            continue  # déjà écrite
                        data = _read_7z_file(manifest["_cb7_path"], name)
                        if data:
                            zf.writestr(name, data)
                else:
                    for entry in state.images_data:
                        if entry.get("bytes") is None or entry.get("is_dir"):
                            continue
                        zf.writestr(entry["orig_name"], entry["bytes"])
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
        )
        orchestrator.start()

    dlg.finished.connect(lambda _: _on_confirm_done())
    dlg.show()
