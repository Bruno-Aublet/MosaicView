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

from modules.qt.app_window_qt import AppWindowMixin
from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.archive_members import open_archive
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


class _MetadataConfirmDialog(AppWindowMixin, QDialog):

    def __init__(self, parent, files: list, dirs: list):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("dialogs.batch_metadata.confirm_title")
        self._files = files
        self._dirs  = dirs
        self.confirmed        = False
        self.permanent_delete = False
        self.skip_existing    = False

        # Y a-t-il des fichiers non-CBZ ?
        self._has_non_cbz = any(
            not f.lower().endswith('.cbz') for f in files
        )

        self.setFixedWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        # Message nombre de fichiers
        self._msg_lbl = QLabel()
        self._msg_lbl.setTextFormat(Qt.PlainText)
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

        # Originaux convertis (non-CBZ) qui ne peuvent pas aller à la Corbeille :
        # leur suppression sera définitive même sans la case
        from modules.qt.recycle_bin import trash_problem_count
        self._converted = [f for f in files if not f.lower().endswith('.cbz')]
        self._no_trash_count = trash_problem_count(self._converted)
        self._trash_lbl = QLabel()
        self._trash_lbl.setTextFormat(Qt.PlainText)
        self._trash_lbl.setWordWrap(True)
        self._trash_lbl.setAlignment(Qt.AlignCenter)
        self._trash_lbl.setVisible(bool(self._no_trash_count))
        layout.addWidget(self._trash_lbl)
        self._chk.toggled.connect(self._update_trash_warning)

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
        self._connect_language()
        self._start_btn.setFocus()

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

        if self._no_trash_count:
            # Avertissement non bloquant : couleur du texte, gras italique
            warn_font = _get_current_font(9)
            warn_font.setBold(True)
            warn_font.setItalic(True)
            self._trash_lbl.setFont(warn_font)
            self._trash_lbl.setStyleSheet(f"color: {theme['text']};")
            self._trash_lbl.setText(_("messages.trash.batch_permanent",
                                      count=self._no_trash_count, total=len(self._converted)))
            self._trash_lbl.setMinimumHeight(self._trash_lbl.heightForWidth(self.width() - 40))

        self._chk_skip.setText(_("dialogs.batch_metadata.checkbox_skip_existing"))
        self._chk_skip.setFont(font9)

        self._start_btn.setText(_("dialogs.batch_metadata.start_button"))
        self._start_btn.setFont(font)
        self._start_btn.setStyleSheet(btn_style)
        self._cancel_btn.setText(_("buttons.cancel"))
        self._cancel_btn.setFont(font)
        self._cancel_btn.setStyleSheet(btn_style)

    def _update_trash_warning(self, checked):
        """Case « sans passer par la corbeille » cochée : l'avertissement ne
        dit plus rien de nouveau."""
        if self._no_trash_count:
            self._trash_lbl.setVisible(not checked)
            self.adjustSize()

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
    done     = _Signal(str, object)   # (cbz_filepath, state)
    error    = _Signal(str)
    misnamed = _Signal(str)           # format réel d'une archive mal nommée (« CBR »…)

    def __init__(self, orchestrator, orig_filepath):
        super().__init__()
        self._orc  = orchestrator
        self._path = orig_filepath

    def run(self):
        # Message d'erreur destiné au journal de l'import (lu dans le Bloc-notes) :
        # en latin pour une langue pIqaD ou tengwar
        from modules.qt.localization import latin_texts
        from modules.qt.misnamed_archives import MisnamedArchiveError
        with latin_texts():
            try:
                cbz_path, state = self._orc._load_state_for_file(self._path)
                self.done.emit(cbz_path, state)
            except MisnamedArchiveError as e:
                # Question de renommage posée par l'orchestrateur (_on_misnamed)
                self.misnamed.emit(e.real_kind)
            except Exception as e:
                from modules.qt.error_log import log_if_unexpected
                log_if_unexpected(e)
                self.error.emit(exception_text(e))


class _SaveCancelled(Exception):
    """Écriture d'un fichier du lot abandonnée (annulation du lot, ou
    fermeture de l'application via stop_running_qthreads)."""


class _SaveWorker(QThread):
    done     = _Signal(str)   # raison de l'échec de suppression de l'original, "" sinon
    error    = _Signal(str)
    disk_full = _Signal(str)  # plus assez de place pour le CBZ : l'import s'arrête
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
                from modules.qt.batch_dialogs_qt import _is_disk_full
                from modules.qt.error_log import log_if_unexpected
                log_if_unexpected(e)
                if _is_disk_full(e):
                    self.disk_full.emit(exception_text(e))
                else:
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
                 permanent_delete: bool = False, skip_existing: bool = False,
                 ignored_count=None):
        self._parent          = parent
        self._files           = files
        # Autres fichiers des dossiers du lot (batch_dialogs_qt.count_ignored_files),
        # None pour une liste de fichiers donnée (Bibliothèque) : ligne
        # « Nombre de fichiers ignorés » du journal
        self._ignored_count   = ignored_count
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
        # Fichiers dont le ComicInfo.xml abîmé a été réparé, original gardé
        # en annexe (comic_info.replace_comic_info_bytes) : [(filename, annexe)]
        self._repaired      = []
        # Fichiers non traités quand l'import s'est arrêté faute de place, None sinon
        self._disk_full     = None
        # Dernier fichier dont les métadonnées ont été écrites (un fichier
        # ignoré ne compte pas), annoncé avec l'arrêt faute de place
        self._last_done     = None
        # Archives mal nommées : fichiers renommés (« X.cbr → X.cbz »), réponse
        # donnée pour les suivants (« rename », « skip », None : demander), et
        # question affichée (fermée à la fin du lot)
        self._renamed         = []
        # Nombre de renommages par ancienne et nouvelle extension (« CBR→CBZ »)
        self._renamed_kinds   = {}
        self._misnamed_choice = None
        self._misnamed_dlg    = None

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
                    from modules.qt.comic_info import comic_info_member_index
                    if comic_info_member_index(
                            [i.filename for i in _z.infolist() if not i.is_dir()]) is not None:
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
            self._worker.misnamed.connect(
                lambda kind, s=my_seq: self._on_misnamed(kind) if s == self._seq else None)
            self._worker.start()
        else:
            ext = os.path.splitext(orig_filepath)[1].lower()
            self._worker = _LoadWorker(self, orig_filepath)
            self._worker.done.connect(
                lambda cbz, st, s=my_seq: self._on_load_done_next(cbz, st) if s == self._seq else None)
            self._worker.error.connect(
                lambda msg, s=my_seq: self._on_load_error(msg) if s == self._seq else None)
            self._worker.misnamed.connect(
                lambda kind, s=my_seq: self._on_misnamed(kind) if s == self._seq else None)
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
        # Fenêtre détruite à sa fermeture : sans cette remise à None, une
        # progression d'écriture arrivée pendant l'annulation (_cancelling)
        # viserait un objet détruit (_show_converting_overlay)
        dlg = self._current_dlg
        dlg.destroyed.connect(
            lambda *_args: setattr(self, "_current_dlg", None) if self._current_dlg is dlg else None)

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

    def _on_misnamed(self, real_kind):
        """Fichier courant mal nommé (extension qui ne correspond pas à son
        format réel `real_kind`) : sans correction de l'extension, il ne peut
        pas être traité. La question est posée au fil du lot, qui attend la
        réponse, sauf si une réponse a déjà été donnée pour les fichiers
        suivants (_misnamed_choice)."""
        path = self._files[self._index]
        if self._misnamed_choice is not None:
            self._apply_misnamed_choice(self._misnamed_choice, path, real_kind)
            return
        from modules.qt.misnamed_archive_dialog_qt import MisnamedArchiveDialog
        dlg = MisnamedArchiveDialog(self._current_dlg or self._parent, [(path, real_kind)],
                                    "dialogs.misnamed_archive.metadata_message",
                                    "dialogs.misnamed_archive.skip_button",
                                    "dialogs.misnamed_archive.following_checkbox")
        self._misnamed_dlg = dlg

        def _on_answer(answer, seq=self._seq):
            if self._misnamed_dlg is dlg:
                self._misnamed_dlg = None
            # Réponse non modale : lot arrêté, ou passé à un autre fichier,
            # pendant la question
            if self._ended or self._cancelling or seq != self._seq:
                return
            choice, following = answer
            if following:
                self._misnamed_choice = choice
            self._apply_misnamed_choice(choice, path, real_kind)
        dlg.ask_async(_on_answer)

    def _apply_misnamed_choice(self, choice, path, real_kind):
        """Renomme le fichier mal nommé `path` selon son format réel puis le
        traite (rename_to_real_format : jamais d'écrasement, nom libre
        « X (1).cbz »… si le nom est pris), ou le laisse de côté. Refusé,
        impossible ou laissé de côté : compté en erreur, avec sa raison, et le
        lot passe au suivant. Lignes du journal (Bloc-notes) : _wt."""
        from modules.qt.misnamed_archives import EXTENSION_OF_KIND
        if choice != "rename":
            self._on_load_error(_wt("messages.errors.batch_misnamed_not_renamed",
                                    real=real_kind, ext=EXTENSION_OF_KIND[real_kind]))
            return
        from modules.qt.file_picker_qt import _files_in_use, _open_documents
        from modules.qt.misnamed_archives import RenameRefusedError, rename_to_real_format
        try:
            new_path = rename_to_real_format(path, real_kind, claims=self._claims,
                                             open_files=_open_documents(),
                                             files_in_use=_files_in_use())
        except RenameRefusedError as e:
            self._on_load_error(_wt(e.key, name=e.name))
            return
        except OSError as e:
            self._on_load_error(_wt("messages.errors.rename_failed.message",
                                    error=exception_text(e)))
            return
        self._renamed.append(f"{os.path.basename(path)} → {os.path.basename(new_path)}")
        kind = (os.path.splitext(path)[1].lstrip(".").upper() + "→"
                + os.path.splitext(new_path)[1].lstrip(".").upper())
        self._renamed_kinds[kind] = self._renamed_kinds.get(kind, 0) + 1
        # Même fichier, sous son nouveau nom : rechargé à la même place du lot
        self._files[self._index] = new_path
        self._load_and_open(first=self._current_dlg is None)

    def _on_skip(self):
        self._skipped_count += 1
        self._on_next()

    def _on_next(self):
        if self._cancelling:
            # Lot arrêté : la fin de l'écriture en cours affiche le résumé.
            # Le fichier écrit compte parmi les fichiers faits (_index : ligne
            # « Nombre de fichiers traités » du journal)
            self._index += 1
            return
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
        for attr in ('_worker', '_image_worker', '_first_issue_worker', '_first_issue_image_worker'):
            w = getattr(dlg, attr, None)
            if w is None:
                continue
            setattr(dlg, attr, None)
            # Un worker déjà parqué par la fenêtre (_park_running_worker ne
            # remet pas toujours l'attribut à None) l'est sans effet une seconde
            # fois : park_qthread n'en garde qu'une référence
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
        try:
            w.disk_full.disconnect()
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
            # Même nettoyage que la fenêtre ComicVine ouverte seule
            # (_ComicVineDialog.__init__) : dates et préfixe de numérotation
            # en tête retirés
            from modules.qt.comicvine_dialog_qt import _ComicVineDialog
            initial = _ComicVineDialog._clean_filename_for_search(
                os.path.splitext(os.path.basename(orig_path))[0])
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
        backup_name = getattr(state, "comic_info_backup_name", None)
        self._save_worker.done.connect(
            lambda source_error, o=orig or filepath, b=backup_name:
            self._on_save_done(source_error, o, b))
        self._save_worker.progress.connect(self._on_save_progress)
        def _on_save_error(msg):
            fname = os.path.basename(filepath)
            self._errors.append((fname, msg))
            self._retire_save_worker()
            self._show_converting_overlay(False)
            self._on_next()
        self._save_worker.error.connect(_on_save_error)

        def _on_save_disk_full(msg):
            # Plus assez de place pour écrire le CBZ : l'import s'arrête,
            # les fichiers suivants ne la trouveraient pas non plus.
            # Original intact, aucun CBZ créé. Ligne du journal (Bloc-notes) :
            # _wt, jamais en pIqaD ou tengwar.
            not_processed = len(self._files) - self._index
            self._disk_full = not_processed
            stop = _wt("messages.errors.batch_stopped_disk_full", count=not_processed)
            if self._last_done:
                stop += " " + _wt("messages.errors.batch_last_done", name=self._last_done)
            self._errors.append((os.path.basename(filepath), msg + " — " + stop))
            self._retire_save_worker()
            self._show_converting_overlay(False)
            self._retire_current_worker()
            self._show_summary()
        self._save_worker.disk_full.connect(_on_save_disk_full)
        self._save_worker.start()

    def _on_save_done(self, source_error="", orig_filepath=None, backup_name=None):
        self._show_converting_overlay(False)
        self._done_count += 1
        if orig_filepath:
            self._last_done = os.path.basename(orig_filepath)
        if backup_name and orig_filepath:
            self._repaired.append((os.path.basename(orig_filepath), backup_name))
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
        # Question de renommage encore affichée : fermée avant la fenêtre
        # ComicVine, qui peut être sa fenêtre parente (sa réponse, envoyée par
        # la fermeture, est ignorée : lot terminé)
        misnamed_dlg, self._misnamed_dlg = self._misnamed_dlg, None
        if misnamed_dlg is not None:
            try:
                misnamed_dlg.close()
            except RuntimeError:
                pass  # sans conséquence : fenêtre déjà détruite avec sa parente
        if self._current_dlg:
            self._rescue_dlg_workers(self._current_dlg)
            self._current_dlg.close()

        # Écrire le log si erreurs ou ComicInfo.xml réparés
        log_path = None
        log_error = None
        if self._errors or self._repaired or self._renamed:
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
                    # Fichiers réellement faits : moins que le lot après une
                    # annulation ou un disque plein (fichier en cours exclu)
                    lf.write(log_line("files_handled", count=min(self._index, len(self._files))))
                    if self._ignored_count is not None:
                        lf.write(log_line("files_ignored", count=self._ignored_count))
                    lf.write(log_line("metadata_saved", count=self._done_count))
                    lf.write(log_line("passed_unchanged", count=self._skipped_count))
                    # Ancienne extension → nouvelle, seulement pour les
                    # renommages qui ont eu lieu
                    for kind in sorted(self._renamed_kinds):
                        lf.write(log_line("renamed_kind", kind=kind, count=self._renamed_kinds[kind]))
                    lf.write(log_line("errors", count=len(self._errors)))
                    if self._renamed:
                        lf.write(f"\n{'='*60}\n")
                        lf.write(log_line("renamed_files"))
                        lf.write(f"{'='*60}\n\n")
                        for entry in self._renamed:
                            lf.write(f"  - {log_text(entry)}\n")
                    if self._errors:
                        lf.write(f"\n{'='*60}\n")
                        lf.write(log_line("error_details"))
                        lf.write(f"{'='*60}\n\n")
                        for fname, msg in self._errors:
                            lf.write(f"  - {log_text(fname)}: {log_text(msg)}\n")
                    if self._repaired:
                        lf.write(f"\n{'='*60}\n")
                        lf.write(log_line("comicinfo_repaired_details"))
                        lf.write(f"{'='*60}\n\n")
                        for fname, backup in self._repaired:
                            lf.write(f"  - {log_text(fname)}: "
                                     f"{log_text(_wt('dialogs.batch_log.comicinfo_repaired_line', name=backup))}\n")
            except Exception as e:
                # Journal impossible à écrire : la raison est affichée dans le résumé.
                log_path = None
                log_error = exception_text(e)

        summary_data = {
            "total":   len(self._files),
            "done":    self._done_count,
            "skipped": self._skipped_count,
            "errors":  len(self._errors),
            "repaired": len(self._repaired),
            "renamed": len(self._renamed),
            "disk_full": self._disk_full,
            "last_done": self._last_done,
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

        # Extension qui ne correspond pas au format réel (quelques octets lus) :
        # rien n'est chargé, l'orchestrateur demande s'il faut la corriger
        # (_on_misnamed). Sans correction, le fichier ne peut pas être traité.
        from modules.qt.misnamed_archives import MisnamedArchiveError, misnamed_real_kind
        real_kind = misnamed_real_kind(orig_filepath)
        if real_kind is not None:
            raise MisnamedArchiveError(orig_filepath, real_kind)

        new_state = _state_mod.AppState()
        new_state.current_file    = orig_filepath
        new_state.images_data     = []
        new_state.comic_metadata  = {}
        new_state.modified        = False
        new_state._original_filepath = orig_filepath

        ext = os.path.splitext(orig_filepath)[1].lower()

        if ext == '.cbz':
            cbz_filepath = orig_filepath
            # Lecteur commun (archive_members.open_archive) : noms réels
            # (open_zip), les mêmes qu'à la recopie de l'archive dans
            # _save_state_for_file ; membres lus par leur ZipInfo (premier d'un
            # nom porté par plusieurs : c'est lui que _save_state_for_file
            # remplace, un homonyme étant recopié), taille contrôlée avant
            # lecture
            with open_archive(orig_filepath, "CBZ") as archive:
                files = archive.files()
                # Noms de l'archive, recopiée entière à l'enregistrement : la
                # copie d'un ComicInfo.xml abîmé (comic_info.replace_comic_info_bytes)
                # ne doit prendre aucun d'eux
                new_state._archive_member_names = [m.name for m in archive.members]

                # Couverture uniquement : la première page de l'ouverture
                images = archive.images()
                if images:
                    cover = images[0]
                    new_state.images_data.append({
                        "orig_name": cover.name, "name": cover.name,
                        "bytes": archive.read(cover), "is_image": True,
                        "is_dir": False,
                        "extension": os.path.splitext(cover.name)[1].lower(),
                    })
                # ComicInfo.xml de la B.D. : celui de la racine, ou celui du
                # dossier qui contient toutes les pages (règle commune,
                # comic_info.comic_info_member_index, comme l'option « ignorer
                # les fichiers ayant déjà un ComicInfo.xml »), réécrit à sa
                # place. ComicInfo_0002.xml, le ComicInfo.xml d'un autre
                # dossier ou une copie de sauvegarde ne sont que des fichiers
                # annexes : pris ici, ils pré-rempliraient le formulaire avec
                # d'autres métadonnées.
                from modules.qt.comic_info import comic_info_member_index
                xml_index = comic_info_member_index([m.name for m in files])
                if xml_index is not None:
                    xml_name = files[xml_index].name
                    xml_data = archive.read(files[xml_index])
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

        def _real_kind():
            """Format réel de l'archive, lu dans son contenu, jamais d'après
            l'extension : 7-Zip ouvre tous les formats qu'il connaît, et un TAR
            compressé nommé .cb7 y apparaît comme un seul fichier (le TAR), que
            la conversion recopierait à la place des pages avant de supprimer
            l'original. Le manifeste garde ce format (« _kind »), relu par
            _save_state_for_file. Format inconnu : erreur (fichier compté en
            erreur, original gardé), attendue (ArchiveRefusedError) : jamais
            écrite dans le journal des erreurs."""
            from modules.qt.archive_names import ArchiveRefusedError
            from modules.qt.archive_type_detector import detect_archive_type
            kind = detect_archive_type(filepath)
            if kind is None:
                raise ArchiveRefusedError(_("messages.errors.batch_unknown_format"))
            return kind

        def _append_cover():
            # Couverture affichée dans la fenêtre ComicVine : la première page,
            # celle que montre l'ouverture de la BD (archive_members.first_page,
            # ordre de l'ouverture, lecteurs communs). Affichage seulement : la
            # conversion recopie tous les fichiers dans _save_state_for_file.
            # Une erreur de lecture remonte (fichier compté en erreur).
            from modules.qt.archive_members import first_page
            page = first_page(filepath)
            if page and page[1]:
                name, data = page
                images_data.append({
                    "orig_name": name, "name": name,
                    "bytes": data, "is_image": True,
                    "is_dir": False,
                    "extension": os.path.splitext(name)[1].lower(),
                })

        if ext == '.cbr':
            kind = _real_kind()
            _append_cover()
            images_data.append({
                "_cbr_path": filepath, "_kind": kind,
                "is_image": False, "is_dir": False,
                "orig_name": "__cbr_manifest__", "name": "__cbr_manifest__",
                "bytes": None, "extension": "",
            })

        elif ext == '.cbt':
            kind = _real_kind()
            _append_cover()
            images_data.append({
                "_cbt_path": filepath, "_kind": kind,
                "is_image": False, "is_dir": False,
                "orig_name": "__cbt_manifest__", "name": "__cbt_manifest__",
                "bytes": None, "extension": "",
            })

        elif ext == '.cb7':
            # Fichiers et dossiers de l'archive relus au moment de la recopie
            # (_save_state_for_file, lecteur commun), comme pour CBR et CBT
            kind = _real_kind()
            _append_cover()
            images_data.append({"_cb7_path": filepath, "_kind": kind,
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
        orig = getattr(state, '_original_filepath', None)
        is_conversion = bool(orig) and orig != filepath
        if is_conversion and not (cbr_manifest or cbt_manifest or cb7_manifest or pdf_manifest):
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

        written_folders = set()

        def _write_folder(zf, member_name):
            """Dossier de la source (CBR/CBT/CB7), vide ou non : recopié, une
            fois, sous un nom contenu (contained_member_name, comme les
            fichiers) — un dossier présent dans l'archive y reste jusqu'à ce
            que l'utilisateur l'aplatisse, et l'original est supprimé ensuite.
            Jamais compté parmi les fichiers relus (CbzWriter.written_files)."""
            from modules.qt.archive_names import contained_member_name
            folder = contained_member_name(member_name)
            if folder and folder.lower() not in written_folders:
                written_folders.add(folder.lower())
                zf.writestr(folder + "/", b"")

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

        def _source_archive():
            """(chemin, type) de l'archive CBR/CBT/CB7 convertie : son format
            réel, relevé au chargement (_extract_non_cbz, « _kind »), à défaut
            celui de son extension."""
            manifest, path_key, kind = ((cbr_manifest, "_cbr_path", "CBR") if cbr_manifest
                                        else (cbt_manifest, "_cbt_path", "CBT") if cbt_manifest
                                        else (cb7_manifest, "_cb7_path", "CB7"))
            return manifest[path_key], manifest.get("_kind") or kind

        def _needed():
            """Taille prévue du CBZ, en-têtes compris (place vérifiée par
            write_cbz avant d'écrire) : fichiers du document (ComicInfo.xml…),
            plus les membres recopiés du CBZ ou de l'archive source. PDF :
            pages rendues de taille inconnue d'avance, pas de vérification
            (seul un disque plein pendant l'écriture arrête l'import)."""
            from modules.qt.utils import estimate_archive_copy_size, estimate_cbz_size
            own = estimate_cbz_size([e for e in state.images_data
                                     if e.get("orig_name") not in MANIFEST_NAMES])
            if cbz_manifest:
                with open_zip(cbz_manifest["_cbz_path"]) as src:
                    return own + len(src.comment) + sum(
                        info.file_size + 100 + 2 * len(info.filename.encode("utf-8"))
                        for info in src.infolist())
            if cbr_manifest or cbt_manifest or cb7_manifest:
                source, kind = _source_archive()
                with open_archive(source, kind) as archive:
                    copied = estimate_archive_copy_size(archive.members, source)
                return None if copied is None else own + copied
            if pdf_manifest:
                return None
            return own

        from modules.qt.utils import zip_compression_kwargs
        from modules.qt.config_manager import get_config_manager as _gcm
        from modules.qt.converted_cbz import check_source_unchanged, delete_source, write_cbz
        # Conversion : source relue membre par membre pendant l'écriture (RAR
        # et 7z rouverts à chaque membre) ; abandonnée si un autre programme
        # la réécrit pendant ce temps, avant que le CBZ prenne son nom
        source_signature = None
        if is_conversion:
            from modules.qt.utils import file_signature
            source_signature = file_signature(orig)
        # Temporaire à côté du fichier, structure contrôlée (comme un
        # enregistrement depuis un panneau), puis nom donné. Conversion :
        # l'original sera supprimé ensuite, le temporaire est donc aussi relu
        # entièrement AVANT de prendre son nom. Tout échec ou annulation
        # (_check_cancel avant chaque fichier écrit) remonte : temporaire
        # supprimé, original intact, aucun CBZ créé.
        with write_cbz(filepath, reread=is_conversion, check_structure=True,
                       before_write=_check_cancel, needed=_needed(),
                       **zip_compression_kwargs(_gcm().get_zip_compression_level())) as zf:
            if cbz_manifest:
                # Seul ComicInfo.xml vient du state. Les pages, couverture
                # comprise (lue au chargement pour l'affichage seulement), sont
                # relues dans le CBZ au moment d'enregistrer : un autre
                # programme a pu le réécrire pendant le choix ComicVine.
                _write_xml_only(zf)
                # Recopier toutes les entrées de l'original sauf le XML déjà écrit
                # (seul le premier membre de ce nom est remplacé : un homonyme est recopié).
                written = {e["orig_name"] for e in state.images_data
                           if e.get("orig_name") not in MANIFEST_NAMES and e.get("bytes") is not None
                           and not e.get("is_image") and not e.get("is_dir")}
                from modules.qt.archive_loader import _check_member_size
                with open_zip(cbz_manifest["_cbz_path"]) as src:
                    # Commentaire d'archive (métadonnées ComicBookInfo) conservé
                    zf.comment = src.comment
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
            elif cbr_manifest or cbt_manifest or cb7_manifest:
                _write_xml_only(zf)
                source, kind = _source_archive()
                # Lecteur commun (archive_members.open_archive), mêmes règles
                # qu'à l'ouverture : fichiers et dossiers (vides compris) dans
                # l'ordre de l'ouverture, nom TAR sans « ./ » (sinon faux
                # sous-dossier « . » dans le CBZ) et relu dans son codage
                # (zipfile refuse le nom de substitution de tarfile), liens
                # TAR écartés, taille contrôlée (MAX_MEMBER_SIZE), fichiers RAR
                # de même nom chacun lu avec son contenu. Toute page illisible
                # fait échouer l'écriture : l'original est gardé.
                with open_archive(source, kind) as archive:
                    # Commentaire d'archive (métadonnées ComicBookInfo d'un
                    # CBR) repris : la source est supprimée ensuite
                    zf.comment = archive.comment()
                    for member in archive.members:
                        if member.is_dir:
                            _write_folder(zf, member.name)
                            continue
                        if _is_old_comicinfo(member.name):
                            continue
                        zf.writestr(_converted_name(member.name), archive.read(member))

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
                        elif kind in ('page_error', 'page_damaged'):
                            # CBZ incomplet ou page abîmée : abandon, le PDF
                            # d'origine (qui serait supprimé ensuite) est conservé.
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

            if is_conversion:
                check_source_unchanged(orig, source_signature)

        # Supprimer l'original non-CBZ : le CBZ qui porte maintenant son nom a
        # été relu sans erreur avant le renommage (delete_source le refuse
        # sinon). Archive source fermée à la sortie de son `with` : Windows
        # en refuserait sinon la suppression.
        if is_conversion:
            try:
                delete_source(zf, orig, permanent=self._permanent_delete,
                              source_signature=source_signature)
            except Exception as e:
                # Texte de l'exception (nom de son type si elle n'en a pas),
                # et pile d'une erreur inattendue au journal des erreurs
                from modules.qt.error_log import log_if_unexpected
                log_if_unexpected(e)
                return exception_text(e)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Fenêtre de résumé
# ═══════════════════════════════════════════════════════════════════════════════

class _MetadataSummaryDialog(AppWindowMixin, QDialog):

    def __init__(self, parent, data: dict):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("dialogs.batch_metadata.complete_title")
        self._data = data
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        # Import arrêté faute de place sur le disque : annoncé en tête
        self._lbl_disk_full = QLabel()
        self._lbl_disk_full.setTextFormat(Qt.PlainText)
        self._lbl_disk_full.setVisible(bool(data.get("disk_full")))
        self._lbl_done    = QLabel()
        self._lbl_done.setTextFormat(Qt.PlainText)
        self._lbl_skipped = QLabel()
        self._lbl_skipped.setTextFormat(Qt.PlainText)
        self._lbl_errors  = QLabel()
        self._lbl_errors.setTextFormat(Qt.PlainText)
        self._lbl_repaired = QLabel()
        self._lbl_repaired.setTextFormat(Qt.PlainText)
        self._lbl_renamed = QLabel()
        self._lbl_renamed.setTextFormat(Qt.PlainText)
        for lbl in (self._lbl_disk_full, self._lbl_done, self._lbl_skipped, self._lbl_errors,
                    self._lbl_repaired, self._lbl_renamed):
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
            # Lien vers le journal, chemin échappé
            self._log_lbl.setTextFormat(Qt.RichText)
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

        self._connect_language()
        self._ok_btn.setFocus()

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
        if d.get("disk_full"):
            # Texte brut (Qt.PlainText) : le nom du fichier vient du disque
            lines = [_("messages.errors.batch_stopped_disk_full", count=d["disk_full"])]
            if d.get("last_done"):
                lines.append(_("messages.errors.batch_last_done", name=d["last_done"]))
            self._lbl_disk_full.setText("\n".join(lines))
            self._lbl_disk_full.setStyleSheet("color: #d9534f; font-weight: bold;")
            self._lbl_disk_full.setFont(font)
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
        # ComicInfo.xml abîmés : contenu récupéré, original gardé en annexe.
        # Information, pas une erreur : couleur du texte
        repaired = d.get("repaired", 0)
        self._lbl_repaired.setText(
            _("dialogs.batch_metadata.comicinfo_repaired_count", count=repaired) if repaired else "")
        self._lbl_repaired.setStyleSheet(f"color: {theme['text']};")
        self._lbl_repaired.setFont(font)
        self._lbl_repaired.setVisible(bool(repaired))
        # Archives mal nommées dont l'extension a été corrigée : information
        renamed = d.get("renamed", 0)
        self._lbl_renamed.setText(
            _("dialogs.batch_metadata.renamed_count", count=renamed) if renamed else "")
        self._lbl_renamed.setStyleSheet(f"color: {theme['text']};")
        self._lbl_renamed.setFont(font)
        self._lbl_renamed.setVisible(bool(renamed))

        if self._log_lbl is not None and d.get("log_error"):
            self._log_lbl.setText(_("dialogs.batch_log_failed", error=d["log_error"]))
            self._log_lbl.setStyleSheet(f"color: {theme['text']};")
            self._log_lbl.setFont(font)
        elif self._log_lbl is not None:
            see_log = _("dialogs.see_log")
            # Journal écrit pour les seuls ComicInfo.xml réparés ou fichiers
            # renommés : lien nommé d'après eux, « 0 erreur(s) » serait trompeur
            if d["errors"] or not (repaired or renamed):
                err_txt = _("dialogs.batch_metadata.errors_count").format(count=d["errors"])
            elif repaired:
                err_txt = _("dialogs.batch_metadata.comicinfo_repaired_count", count=repaired)
            else:
                err_txt = _("dialogs.batch_metadata.renamed_count", count=renamed)
            lp = d["log_path"]
            import html
            self._log_lbl.setText(f'<a href="file:///{html.escape(lp, quote=True)}">'
                                  f'{err_txt} — {see_log}</a>')
            self._log_lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)


def _open_log(path):
    import subprocess
    from modules.qt.utils import windows_program
    try:
        subprocess.Popen([windows_program("notepad.exe"), path])
    except Exception:
        import os as _os
        _os.startfile(path)


def _show_metadata_summary(parent, data: dict):
    _MetadataSummaryDialog(parent, data).show_nonmodal()


# ═══════════════════════════════════════════════════════════════════════════════
# Confirmation d'arrêt du batch (bouton Annuler cliqué pendant le traitement)
# ═══════════════════════════════════════════════════════════════════════════════

class _CancelConfirmDialog(AppWindowMixin, QDialog):

    def __init__(self, parent):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("dialogs.batch_metadata.cancel_confirm_title")
        self.setFixedWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        self._msg_lbl = QLabel()
        self._msg_lbl.setTextFormat(Qt.PlainText)
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
        self._connect_language()
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
    dlg = _CancelConfirmDialog(parent)
    dlg.finished.connect(lambda _: on_result(dlg.stopped))
    dlg.show_nonmodal()
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


def show_batch_metadata_dialog(parent, files: list, dirs: list, batch_callbacks: dict,
                               ignored_count=None):
    """
    Affiche la fenêtre de confirmation pour l'import de métadonnées en lot.
    Si confirmé, vérifie la clé API et lance l'orchestrateur.
    ignored_count : autres fichiers des dossiers du lot
    (batch_dialogs_qt.count_ignored_files), None pour une liste de fichiers
    donnée (Bibliothèque).
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
            ignored_count=ignored_count,
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
    dlg.show_nonmodal()
