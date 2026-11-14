"""
modules/qt/import_merge_qt.py
Fusion d'une archive CBZ/CBR dans le comics courant.
"""

import zipfile
import rarfile
import os

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import QDialog

from modules.qt.localization import _
from modules.qt.canvas_overlay_qt import show_canvas_text as _show_canvas_text, hide_canvas_text as _hide_canvas_text
from modules.qt.entries import is_unreadable_file
from modules.qt.archive_loader import (ExtensionCorrectionDialog, _natural_sort_key,
                                       _remember_unread_zip_member, create_page_entry,
                                       folder_entry, _expand_multipage_tiffs_with_thumbnails)
from modules.qt.archive_members import open_archive
from modules.qt.archive_names import epub_page_name
from modules.qt.comic_info import is_comic_info_name, name_incoming_comic_infos
from modules.qt.pdf_loading_qt import _MsgDialog


# ═══════════════════════════════════════════════════════════════════════════════
# Worker thread — reproduit import_worker() de import_and_merge_archive
# ═══════════════════════════════════════════════════════════════════════════════
class ImportMergeWorker(QThread):
    progress     = Signal(int)    # percent
    finished     = Signal(list, list)   # new_entries, errors (« nom: message »)
    error        = Signal(str)
    cancelled    = Signal()
    bad_zip_file = Signal()       # CBZ qui est en fait un RAR
    bad_rar_file = Signal()       # CBR qui est en fait un ZIP

    def __init__(self, filepath: str, merge_prefix: str):
        import threading
        super().__init__()
        self._filepath     = filepath
        self._merge_prefix = merge_prefix
        self._cancelled    = threading.Event()
        # Pages déjà lues, comptées par l'indicateur de mémoire du panneau
        # pendant l'ajout (memory_usage.reading_started)
        self.pages_read    = []

    def run(self):
        filepath     = self._filepath
        merge_prefix = self._merge_prefix
        ext          = os.path.splitext(filepath)[1].lower()
        new_entries  = self.pages_read
        errors       = []

        # Un membre illisible (CRC faux, compression non prise en charge,
        # membre chiffré, taille au-delà de MAX_MEMBER_SIZE) n'arrête jamais
        # l'ajout, comme à l'ouverture d'une archive (LoadWorker) : une page
        # ou un autre fichier reste dans le document sans données (cadre
        # rouge), avec sa raison (entries.is_unreadable_file pour un fichier
        # qui n'est pas une image). Sans cela, une erreur
        # de lecture d'un membre ZIP ou RAR (BadZipFile, BadRarFile) passerait
        # pour une archive mal nommée et ferait proposer de la renommer.
        # Une image au CRC faux qui se décode quand même est ajoutée, marquée
        # altérée (cadre orange), comme à l'ouverture (create_page_entry).
        def _note_read_error(entry, file, message):
            if message is None:
                return
            if entry.get("is_corrupted") or is_unreadable_file(entry):
                entry["corruption_reason"] = message
            else:
                errors.append(f"{file}: {message}")

        def _add_prefix(entry):
            orig_name = entry["orig_name"]
            entry["source_archive"] = os.path.basename(filepath)
            if is_comic_info_name(orig_name):
                # Gardé tel quel : à l'ajout, il devient les métadonnées du
                # comic s'il n'en a pas, sinon il est renommé ComicInfo_0002.xml…
                # (comic_info.name_incoming_comic_infos, dans _append_merged)
                return
            if '/' in orig_name:
                parts     = orig_name.rsplit('/', 1)
                path_part = parts[0]
                filename  = parts[1] if len(parts) > 1 else ""
                if filename:
                    entry["orig_name"] = merge_prefix + path_part + '/' + merge_prefix + filename
                else:
                    entry["orig_name"] = merge_prefix + orig_name
            else:
                entry["orig_name"] = merge_prefix + orig_name

        def _load(kind):
            """Fichiers et dossiers (vides compris) de l'archive, comme à son
            ouverture (lecteur commun archive_members.open_archive, même
            ordre, mêmes protections) ; un EPUB : ses images seules, dans
            l'ordre de lecture du livre, nommées par leur rang
            (archive_names.epub_page_name). Rend False si l'ajout est annulé."""
            with open_archive(filepath, kind) as archive:
                is_epub = kind == "EPUB"
                members = archive.epub_pages() if is_epub else archive.members
                total = len([m for m in members if not m.is_dir])
                count = 0
                for position, member in enumerate(members, start=1):
                    if self._cancelled.is_set():
                        return False
                    if member.is_dir:
                        # Dossier, vide ou non : ajouté avec ses pages, jamais
                        # lu ; aucun pour un nom qui ne désigne aucun vrai
                        # dossier (archive_loader.folder_entry)
                        entry = folder_entry(member.name)
                        if entry is not None:
                            _add_prefix(entry)
                            new_entries.append(entry)
                        continue
                    file_name = epub_page_name(position, member.name) if is_epub else member.name
                    entry, read_error = create_page_entry(file_name, lambda m=member: archive.read(m))
                    if read_error is not None:
                        read_error = read_error[:200]
                        if kind in ("CBZ", "EPUB"):
                            _remember_unread_zip_member(entry, filepath, member.ref)
                    _note_read_error(entry, member.name, read_error)
                    _add_prefix(entry)
                    new_entries.append(entry)
                    count += 1
                    self.progress.emit(int(count / total * 100) if total else 0)
            return True

        try:
            ok = True
            # Format d'après l'extension : un CBZ qui n'est pas un ZIP (ou un
            # CBR qui n'est pas un RAR) est signalé mal nommé, et l'ajout
            # reprend avec le bon format après la question de renommage
            kind = {".cbz": "CBZ", ".epub": "EPUB", ".cbr": "CBR", ".cb7": "CB7",
                    ".cbt": "CBT"}.get(ext)
            if kind in ("CBZ", "EPUB"):
                try:
                    ok = _load(kind)
                except zipfile.BadZipFile:
                    self.bad_zip_file.emit()
                    return
            elif kind == "CBR":
                try:
                    ok = _load(kind)
                except (rarfile.NotRarFile, rarfile.BadRarFile):
                    self.bad_rar_file.emit()
                    return
            elif kind is not None:
                ok = _load(kind)

            if not ok:
                self.cancelled.emit()
                return
            # Un TIFF de plusieurs pages devient une page du comic par page
            # (décision de conception, voir entries.expand_multipage_tiffs).
            self.finished.emit(_expand_multipage_tiffs_with_thumbnails(new_entries), errors)

        except Exception as e:
            from modules.qt.error_log import log_if_unexpected
            log_if_unexpected(e)
            self.error.emit(str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# import_and_merge_archive — reproduit import_and_merge_archive de import_merge.py
# ═══════════════════════════════════════════════════════════════════════════════
def import_and_merge_archive(filepath: str, win, canvas, state):
    """Importe une archive CBZ/CBR et fusionne ses pages dans le comics actuel."""

    state.merge_counter += 1
    merge_prefix = f"NEW{state.merge_counter:02d}-"
    doc_generation = state.doc_generation

    item_holder        = []
    cancel_item_holder = [None]
    _worker_ref        = [None]
    # Annulée (lien Annuler, fermeture du fichier) : _cancel débranche les
    # slots, mais un résultat déjà mis en file d'attente avant le débranchement
    # est quand même livré ; les slots de fin le vérifient.
    cancelled_flag     = [False]
    # Jeton de cette fusion pour MosaicCanvas.hold_empty_hint : pas de texte
    # « BD vide » sous le texte de progression d'une fusion dans une BD vide.
    empty_hint_token   = object()

    def _update_label(percent):
        if _worker_ref[0] is None:
            return
        canvas.hold_empty_hint(empty_hint_token)
        _show_canvas_text(canvas, _("labels.import_progress", percent=percent), item_holder)
        from modules.qt.web_import_qt import _show_cancel_item
        _show_cancel_item(canvas, f"[ {_('buttons.cancel')} ]", cancel_item_holder, _cancel,
                          anchor_lbl=item_holder[0])

    def _remove_label():
        _hide_canvas_text(canvas, item_holder)
        _hide_canvas_text(canvas, cancel_item_holder)
        canvas.release_empty_hint(empty_hint_token)

    def _cancel():
        worker = _worker_ref[0]
        if worker is None:
            return
        cancelled_flag[0] = True
        worker._cancelled.set()
        try:
            worker.progress.disconnect(_update_label)
            worker.finished.disconnect(on_finished)
            worker.cancelled.disconnect(on_cancelled)
            worker.error.disconnect(on_error)
            worker.bad_zip_file.disconnect(on_bad_zip)
            worker.bad_rar_file.disconnect(on_bad_rar)
        except RuntimeError:
            pass
        _worker_ref[0] = None
        _remove_label()
        canvas.render_mosaic()

    _update_label(0)

    def _start_worker(actual_filepath):
        from modules.qt.utils import (dispose_qthread, register_cancel_on_close,
                                      unregister_cancel_on_close)
        worker = ImportMergeWorker(actual_filepath, merge_prefix)
        _worker_ref[0] = worker
        # Archive lue : ni renommée ni mise à la Corbeille par la fenêtre de
        # choix de fichier tant que le worker tourne (retirée par _cleanup)
        from modules.qt import files_in_use
        reading = files_in_use.hold(actual_filepath)
        # Fermeture du fichier pendant la fusion : même effet qu'Annuler
        register_cancel_on_close(canvas, _cancel)
        # Pages lues comptées dans la mémoire du panneau avant d'arriver dans
        # le document ; retirées par _cleanup, appelée par chaque signal de fin
        from modules.qt.memory_usage import reading_finished, reading_started
        reading_started(state, worker.pages_read)

        def _cleanup():
            _worker_ref[0] = None
            files_in_use.release(reading)
            unregister_cancel_on_close(canvas, _cancel)
            reading_finished(state, worker.pages_read)
            dispose_qthread(worker)
            # Programmes annexes de l'ajout (UnRAR, 7-Zip) terminés : retirés
            # tout de suite de l'indicateur de mémoire
            from modules.qt.memory_usage import helper_programs_finished
            helper_programs_finished()

        worker.progress.connect(_update_label)
        worker.finished.connect(lambda *_: _cleanup())
        worker.cancelled.connect(lambda: _cleanup())
        worker.error.connect(lambda *_: _cleanup())
        worker.bad_zip_file.connect(lambda: _cleanup())
        worker.bad_rar_file.connect(lambda: _cleanup())
        worker.finished.connect(on_finished)
        worker.cancelled.connect(on_cancelled)
        worker.error.connect(on_error)
        worker.bad_zip_file.connect(on_bad_zip)
        worker.bad_rar_file.connect(on_bad_rar)
        worker.start()

    def on_cancelled():
        _remove_label()
        canvas.render_mosaic()

    def on_finished(new_entries, errors):
        if cancelled_flag[0]:
            return
        _remove_label()
        if state.doc_generation != doc_generation:
            # Document fermé ou remplacé pendant la fusion : ces pages visaient
            # l'ancien document (voir AppState.doc_generation).
            canvas.render_mosaic()
            return
        if errors:
            # Erreurs qui ne correspondent à aucune entrée (les pages et
            # fichiers illisibles portent leur raison et sont signalés par
            # leur cadre rouge) : même fenêtre qu'à l'ouverture d'une archive
            from modules.qt.archive_loader import _MessageDialog
            from modules.qt.read_errors import files_ignored_line
            shown = list(errors[:20])
            _MessageDialog(win, "messages.warnings.files_ignored.title",
                           lambda: "\n".join(files_ignored_line(e) for e in shown)
                           ).show_nonmodal()
        if not new_entries:
            canvas.render_mosaic()
            return
        # Opération en cours sur le document (rotation…) : les pages sont
        # ajoutées à sa fin, sinon son annulation les effacerait.
        state.run_when_idle(lambda: _append_merged(new_entries))

    def _append_merged(new_entries):
        save_state = getattr(canvas, "_save_state_callback", None) or (lambda: None)
        save_state()
        name_incoming_comic_infos(new_entries, state.images_data)
        # À la fin, rangées entre elles par nom : l'ordre du document, qui peut
        # différer de celui des noms, ne change pas.
        state.images_data.extend(sorted(new_entries, key=lambda e: _natural_sort_key(e["orig_name"])))
        state.modified = True
        state.selected_indices.clear()
        from modules.qt.comic_info import sync_pages_in_xml_data
        sync_pages_in_xml_data(state)
        # Point d'annulation APRÈS la fusion : sans lui, Annuler la défairait
        # avec l'action précédente, et Refaire ne la rétablirait jamais.
        save_state()
        canvas.render_mosaic()

    def on_error(msg):
        if cancelled_flag[0]:
            return
        _remove_label()
        canvas.render_mosaic()
        _MsgDialog(win,
            "messages.errors.import_failed.title",
            "messages.errors.import_failed.message",
            {"error": msg},
        ).show_nonmodal()

    def _handle_ext_choice(choice, filepath, new_ext):
        """Suite commune après choix de correction d'extension (NON modal)."""
        if choice is None:
            return
        actual = filepath
        if choice == 'rename':
            new_path = os.path.splitext(filepath)[0] + new_ext
            try:
                os.rename(filepath, new_path)
                actual = new_path
            except Exception as e:
                _MsgDialog(win,
                    "messages.errors.rename_failed.title",
                    "messages.errors.rename_failed.message",
                    {"error": str(e)[:100]},
                ).show_nonmodal()
                return
        elif choice != 'keep':
            return
        _start_worker(actual)

    def on_bad_zip():
        """CBZ qui est en fait un RAR — dialogue de correction d'extension."""
        if cancelled_flag[0]:
            return
        _remove_label()
        dlg = ExtensionCorrectionDialog(win, filepath, "CBR", "CBZ")
        dlg.ask_async(lambda choice: _handle_ext_choice(choice, filepath, '.cbr'))

    def on_bad_rar():
        """CBR qui est en fait un ZIP — dialogue de correction d'extension."""
        if cancelled_flag[0]:
            return
        _remove_label()
        dlg = ExtensionCorrectionDialog(win, filepath, "CBZ", "CBR")
        dlg.ask_async(lambda choice: _handle_ext_choice(choice, filepath, '.cbz'))

    _start_worker(filepath)
