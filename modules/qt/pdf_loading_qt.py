"""
modules/qt/pdf_loading_qt.py
Chargement de fichiers PDF.
"""

import io
import os
import gc

from PySide6.QtCore import QThread, Signal, Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QButtonGroup,
)

from modules.qt.localization import _, _wt
from modules.qt.entries import create_entry
from modules.qt.pdf_page_images import best_page_image, raw_page_image
from modules.qt import state as _state_module
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.canvas_overlay_qt import show_canvas_text as _show_canvas_text, hide_canvas_text as _hide_canvas_text

try:
    import pymupdf as fitz
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

from modules.qt.archive_names import IMAGE_EXTS


def pdf_image_ext(data: bytes) -> str:
    """Extension du vrai format d'une page issue d'un PDF : les images
    natives extraites telles quelles peuvent être en JPEG, PNG ou WebP (les
    pages rendues sont toujours en JPEG). Sans ça, une image PNG/WebP serait
    nommée .jpg et convertie en JPEG à la première édition ou sauvegarde."""
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return '.png'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return '.webp'
    return '.jpg'


# ═══════════════════════════════════════════════════════════════════════════════
# Dialogue de sélection DPI — reproduit show_dpi_dialog_for_merge / load_pdf
# ═══════════════════════════════════════════════════════════════════════════════
class DpiDialog(QDialog):
    """Dialogue de sélection du DPI avant chargement PDF."""

    result_signal = Signal(object)   # dpi (int) si OK, None si annulé

    def __init__(self, parent, title_key, label_key):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.selected_dpi = None
        self._emitted = False
        self._title_key = title_key
        self._label_key = label_key
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setFixedSize(450, 260)

        from modules.qt.overlay_tooltip_qt import OverlayTooltip
        self._overlay_tip = OverlayTooltip(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignTop)

        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        font = _get_current_font(10)
        font.setBold(True)
        self._lbl.setFont(font)
        layout.addWidget(self._lbl)

        layout.addSpacing(10)

        self._group = QButtonGroup(self)
        self._radios = []
        self._dpi_options = [
            (0,   "dialogs.pdf.dpi_original"),
            (90,  "dialogs.pdf.dpi_90"),
            (150, "dialogs.pdf.dpi_150"),
            (200, "dialogs.pdf.dpi_200"),
            (300, "dialogs.pdf.dpi_300"),
            (600, "dialogs.pdf.dpi_600"),
        ]
        for dpi, key in self._dpi_options:
            rb = QRadioButton()
            rb.setProperty("dpi_value", dpi)
            self._group.addButton(rb)
            self._radios.append((dpi, rb))
            layout.addWidget(rb)

        # DPI 0 sélectionné par défaut (comme l'original)
        self._radios[0][1].setChecked(True)

        # Tooltip sur le radio "pas de modification" (DPI 0)
        self._rb_original = self._radios[0][1]
        self._update_original_tooltip()

        layout.addStretch(1)

        btn_layout = QHBoxLayout()
        self._btn_ok     = QPushButton()
        self._btn_cancel = QPushButton()
        self._btn_ok.clicked.connect(self._on_ok)
        self._btn_cancel.clicked.connect(lambda: self._finish(None))
        btn_layout.addWidget(self._btn_ok)
        btn_layout.addWidget(self._btn_cancel)
        layout.addLayout(btn_layout)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
        self._center_parent = parent

    def showEvent(self, event):
        # Centrage fait dans ask_async() avant show() (pas de flash de recentrage).
        super().showEvent(event)

    def _update_original_tooltip(self):
        import html as _html
        text = _("dialogs.pdf.dpi_original_tooltip")
        escaped = _html.escape(text).replace("\n", "<br>")
        tip_html = (
            f'<table style="max-width:360px;white-space:normal;">'
            f'<tr><td>{escaped}</td></tr>'
            f'</table>'
        )
        self._overlay_tip.track(self._rb_original, tip_html)

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self.setWindowTitle(_wt(self._title_key))
        self._lbl.setText(_(self._label_key))
        self._lbl.setFont(font)
        for dpi, key in self._dpi_options:
            for d, rb in self._radios:
                if d == dpi:
                    rb.setText(_(key))
                    rb.setFont(font)
        self._btn_ok.setText(_("buttons.ok"))
        self._btn_ok.setFont(font)
        self._btn_ok.setStyleSheet(btn_style)
        self._btn_cancel.setText(_("buttons.cancel"))
        self._btn_cancel.setFont(font)
        self._btn_cancel.setStyleSheet(btn_style)
        self._overlay_tip.apply_theme()
        if hasattr(self, '_rb_original'):
            self._update_original_tooltip()

    def _on_ok(self):
        dpi = None
        for d, rb in self._radios:
            if rb.isChecked():
                dpi = d
                break
        self.selected_dpi = dpi
        self._finish(dpi)

    def _finish(self, dpi):
        if self._emitted:
            return
        self._emitted = True
        self.selected_dpi = dpi
        self._on_close()
        self.result_signal.emit(dpi)
        self.hide()
        self.deleteLater()

    def closeEvent(self, event):
        if not self._emitted:
            self._emitted = True
            self.selected_dpi = None
            self._on_close()
            self.result_signal.emit(None)
        event.accept()

    def ask_async(self, on_result):
        """Affiche (NON modal) et appelle on_result(dpi|None) à la réponse."""
        from modules.qt.dialogs_qt import position_dialog_on_parent
        self.result_signal.connect(on_result)
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Dialogue de message générique — supporte le changement de langue
# ═══════════════════════════════════════════════════════════════════════════════
class _MsgDialog(QDialog):
    """Remplace QMessageBox pour supporter le changement de langue à la volée."""

    def __init__(self, parent, title_key, message_key, message_kwargs=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self._title_key    = title_key
        self._message_key  = message_key
        self._message_kwargs = message_kwargs or {}
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(12)

        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        layout.addWidget(self._lbl)

        self._btn_ok = QPushButton()
        self._btn_ok.clicked.connect(self.close)
        layout.addWidget(self._btn_ok, alignment=Qt.AlignCenter)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
        self._center_parent = parent

    def show_nonmodal(self):
        from modules.qt.dialogs_qt import position_dialog_on_parent
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self.setWindowTitle(_wt(self._title_key))
        self._lbl.setText(_(self._message_key, **self._message_kwargs))
        self._lbl.setFont(font)
        self._btn_ok.setText(_("buttons.ok"))
        self._btn_ok.setFont(font)
        self._btn_ok.setStyleSheet(btn_style)

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Fenêtre de succès — supporte le changement de langue
# ═══════════════════════════════════════════════════════════════════════════════
class PdfSuccessDialog(QDialog):
    """Dialogue de succès après chargement PDF — supporte le changement de langue."""

    def __init__(self, parent, count: int, dpi: int):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self._count = count
        self._dpi   = dpi
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)

        layout = QVBoxLayout(self)
        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        layout.addWidget(self._lbl)

        self._btn_ok = QPushButton()
        self._btn_ok.clicked.connect(self.close)
        layout.addWidget(self._btn_ok, alignment=Qt.AlignCenter)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
        self._center_parent = parent

    def show_nonmodal(self):
        from modules.qt.dialogs_qt import position_dialog_on_parent
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self.setWindowTitle(_wt("messages.info.pdf_convert_success.title"))
        self._lbl.setText(_("messages.info.pdf_convert_success.message", count=self._count, dpi=self._dpi))
        self._lbl.setFont(font)
        self._btn_ok.setText(_("buttons.ok"))
        self._btn_ok.setFont(font)
        self._btn_ok.setStyleSheet(btn_style)

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass



# ═══════════════════════════════════════════════════════════════════════════════
# Worker thread — reproduit load_worker() de start_pdf_loading
# ═══════════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════════
# Process persistant — attend des tâches via in_queue, renvoie via out_queue
# ═══════════════════════════════════════════════════════════════════════════════
def _send_page_error(out_queue, page_num, error):
    """Signale une page que le process n'a pas pu rendre : ('page_error',
    page_num, message). Chaque consommateur doit la traiter, sans quoi le
    document produit a une page de moins sans que personne le sache (et une
    conversion par lot supprimerait le PDF d'origine)."""
    out_queue.send(('page_error', page_num, (str(error) or type(error).__name__)[:200]))


def pdf_page_error_text(msg):
    """Texte traduit d'un message ('page_error', page_num, message) pour un
    journal ou la liste « fichiers ignorés » : numéro de page (base 1) et
    erreur (texte brut de PyMuPDF, non traduisible)."""
    _kind, page_num, message = msg
    return _("messages.errors.pdf_failure.page_error", page=page_num + 1, error=message)


def _warn_pdf_pages_ignored(parent, page_errors):
    """Avertit (non modal) que des pages du PDF n'ont pas pu être chargées :
    même fenêtre « Fichiers ignorés » que le chargement d'archives.
    page_errors : messages ('page_error', …) bruts, mis en texte à chaque
    affichage pour suivre un changement de langue."""
    if not page_errors:
        return
    from modules.qt.archive_loader import _MessageDialog
    errors = list(page_errors[:20])
    _MessageDialog(parent, "messages.warnings.files_ignored.title",
                   lambda: "\n".join(pdf_page_error_text(m) for m in errors)).show_nonmodal()


def _pdf_persistent_process(in_queue, out_queue):
    """
    Process préchauffé qui tourne en idle dès le démarrage de l'app.
    Messages acceptés :
      ('preopen', filepath)   — ouvre le PDF pendant que l'utilisateur choisit le DPI
                                répond ('preopen_ok', total_pages, is_owner) ou erreur
      ('run_opened', dpi)     — lance la conversion du doc déjà ouvert
      ('run', filepath, dpi)  — ouvre + convertit en une seule fois (fallback)
      ('discard',)            — annule un preopen sans conversion (ex: dialogue annulé)
      ('quit',)               — arrête le process
    """
    import pymupdf as fitz
    import io
    import gc
    from PIL import Image as PILImage

    doc = None  # document pré-ouvert, gardé entre preopen et run_opened

    def _open_doc(filepath):
        nonlocal doc
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
            doc = None
        doc = fitz.open(filepath)

    def _convert(dpi):
        nonlocal doc
        if doc.needs_pass:
            doc.close()
            doc = None
            out_queue.send(('password_error',))
            return

        is_owner_protected = (doc.authenticate("") == 2)
        total_pages = len(doc)

        if total_pages == 0:
            doc.close()
            doc = None
            out_queue.send(('empty_pdf',))
            return

        out_queue.send(('total', total_pages, is_owner_protected))

        for page_num in range(total_pages):
            try:
                page = doc[page_num]
                detected_dpi = None
                raw_image_bytes = None

                if dpi > 0:
                    zoom = dpi / 72.0
                    mat = fitz.Matrix(zoom, zoom)
                    pix = page.get_pixmap(matrix=mat, alpha=False)
                else:
                    image_list = page.get_images(full=True)
                    has_text = bool(page.get_text().strip())

                    if image_list:
                        max_dpi, best_xref = best_page_image(page, image_list)

                        if has_text:
                            detected_dpi = max_dpi if max_dpi >= 300 else 300
                        else:
                            detected_dpi = max_dpi if max_dpi > 0 else 72
                        detected_dpi = min(detected_dpi, 2400)

                        # Image reprise telle quelle seulement si elle est
                        # tout le contenu de la page (raw_page_image)
                        try:
                            raw_image_bytes = raw_page_image(doc, page, image_list, best_xref)
                        except Exception:
                            raw_image_bytes = None
                        if raw_image_bytes is not None:
                            detected_dpi = int(max_dpi) if max_dpi > 0 else int(detected_dpi)
                    else:
                        detected_dpi = 300

                    if raw_image_bytes is not None:
                        percent = int((page_num + 1) / total_pages * 100)
                        out_queue.send(('page', page_num, raw_image_bytes, detected_dpi))
                        out_queue.send(('progress', percent, page_num + 1, total_pages))
                        continue

                    mat = fitz.Matrix(detected_dpi / 72.0, detected_dpi / 72.0)
                    pix = page.get_pixmap(matrix=mat, alpha=False)

                img = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
                pix = None
                buf = io.BytesIO()
                img.save(buf, format='JPEG', quality=100, optimize=True)
                img_data = buf.getvalue()
                img = None
                buf = None

                used_dpi = detected_dpi if (dpi == 0 and detected_dpi is not None) else dpi
                percent = int((page_num + 1) / total_pages * 100)
                out_queue.send(('page', page_num, img_data, used_dpi))
                out_queue.send(('progress', percent, page_num + 1, total_pages))

                if (page_num + 1) % 10 == 0:
                    gc.collect()

            except Exception as e:
                # Jamais ignorée en silence : le document produit serait
                # incomplet sans que personne le sache (voir _send_page_error).
                _send_page_error(out_queue, page_num, e)
                continue

        doc.close()
        doc = None
        out_queue.send(('done',))

    def _convert_merge(dpi, merge_prefix):
        nonlocal doc
        if doc.needs_pass:
            doc.close()
            doc = None
            out_queue.send(('password_error',))
            return

        is_owner_protected = (doc.authenticate("") == 2)
        total_pages = len(doc)

        if total_pages == 0:
            doc.close()
            doc = None
            out_queue.send(('empty_pdf',))
            return

        out_queue.send(('total', total_pages, is_owner_protected))

        for page_num in range(total_pages):
            try:
                page = doc[page_num]
                detected_dpi = None
                raw_image_bytes = None

                if dpi > 0:
                    zoom = dpi / 72.0
                    mat = fitz.Matrix(zoom, zoom)
                    pix = page.get_pixmap(matrix=mat, alpha=False)
                else:
                    image_list = page.get_images(full=True)
                    has_text = bool(page.get_text().strip())

                    if image_list:
                        max_dpi, best_xref = best_page_image(page, image_list)

                        if has_text:
                            detected_dpi = max_dpi if max_dpi >= 300 else 300
                        else:
                            detected_dpi = max_dpi if max_dpi > 0 else 72
                        detected_dpi = min(detected_dpi, 2400)

                        # Image reprise telle quelle seulement si elle est
                        # tout le contenu de la page (raw_page_image)
                        try:
                            raw_image_bytes = raw_page_image(doc, page, image_list, best_xref)
                        except Exception:
                            raw_image_bytes = None
                        if raw_image_bytes is not None:
                            detected_dpi = int(max_dpi) if max_dpi > 0 else int(detected_dpi)
                    else:
                        detected_dpi = 300

                    if raw_image_bytes is not None:
                        filename = f"{merge_prefix}page_{page_num + 1:04d}{pdf_image_ext(raw_image_bytes)}"
                        percent  = int((page_num + 1) / total_pages * 100)
                        out_queue.send(('merge_page', filename, raw_image_bytes, detected_dpi))
                        out_queue.send(('progress', percent, page_num + 1, total_pages))
                        continue

                    mat = fitz.Matrix(detected_dpi / 72.0, detected_dpi / 72.0)
                    pix = page.get_pixmap(matrix=mat, alpha=False)

                img = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
                pix = None
                buf = io.BytesIO()
                img.save(buf, format='JPEG', quality=100, optimize=True)
                img_data = buf.getvalue()
                img = None
                buf = None

                used_dpi = detected_dpi if (dpi == 0 and detected_dpi is not None) else dpi
                filename  = f"{merge_prefix}page_{page_num + 1:04d}.jpg"
                percent   = int((page_num + 1) / total_pages * 100)
                out_queue.send(('merge_page', filename, img_data, used_dpi))
                out_queue.send(('progress', percent, page_num + 1, total_pages))

                if (page_num + 1) % 10 == 0:
                    gc.collect()

            except Exception as e:
                _send_page_error(out_queue, page_num, e)
                continue

        doc.close()
        doc = None
        out_queue.send(('done',))

    # ── boucle principale du process ──────────────────────────────────────────
    while True:
        try:
            msg = in_queue.get()
        except Exception:
            return

        kind = msg[0]

        if kind == 'quit':
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
            return

        elif kind == 'preopen':
            _, filepath = msg
            try:
                _open_doc(filepath)
                if doc.needs_pass:
                    out_queue.send(('password_error',))
                    doc.close()
                    doc = None
                else:
                    is_owner = (doc.authenticate("") == 2)
                    total    = len(doc)
                    out_queue.send(('preopen_ok', total, is_owner))
            except Exception as e:
                out_queue.send(('error', str(e)[:200]))
                doc = None

        elif kind == 'run_opened':
            _, dpi = msg
            if doc is None:
                out_queue.send(('error', 'No document pre-opened'))
                continue
            try:
                _convert(dpi)
            except Exception as e:
                out_queue.send(('error', str(e)[:200]))
            doc = None

        elif kind == 'run':
            _, filepath, dpi = msg
            try:
                _open_doc(filepath)
                _convert(dpi)
            except Exception as e:
                out_queue.send(('error', str(e)[:200]))
            doc = None

        elif kind == 'run_merge':
            _, filepath, dpi, merge_prefix = msg
            try:
                _open_doc(filepath)
                _convert_merge(dpi, merge_prefix)
            except Exception as e:
                out_queue.send(('error', str(e)[:200]))
            doc = None

        elif kind == 'batch_open':
            _, filepath = msg
            try:
                _open_doc(filepath)
                if doc.needs_pass:
                    out_queue.send(('batch_ready', None))  # None = needs_pass
                    doc.close()
                    doc = None
                else:
                    is_owner  = (doc.authenticate("") == 2)
                    total     = len(doc)
                    # Ratios largeur/hauteur pour renumber_mode
                    ratios = []
                    for pn in range(total):
                        try:
                            r = doc[pn].rect
                            ratios.append(r.width / r.height)
                        except Exception:
                            ratios.append(0)
                    # Vignette page 0 (basse résolution)
                    thumb_bytes = None
                    try:
                        pix = doc[0].get_pixmap(matrix=fitz.Matrix(0.5, 0.5), alpha=False)
                        buf = io.BytesIO()
                        PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples).save(buf, format='JPEG', quality=80)
                        thumb_bytes = buf.getvalue()
                        pix = None
                        buf = None
                    except Exception:
                        pass
                    out_queue.send(('batch_ready', total, ratios, thumb_bytes, is_owner))
            except Exception as e:
                out_queue.send(('error', str(e)[:200]))
                doc = None

        elif kind == 'batch_convert':
            _, filenames_list = msg
            if doc is None:
                out_queue.send(('error', 'No document open for batch'))
                continue
            try:
                total_pages = len(doc)
                for page_num in range(total_pages):
                    try:
                        page            = doc[page_num]
                        image_list      = page.get_images(full=True)
                        has_text        = bool(page.get_text().strip())
                        raw_image_bytes = None

                        if image_list:
                            max_dpi, best_xref = best_page_image(page, image_list)
                            detected_dpi = (max(max_dpi, 300) if has_text else (max_dpi if max_dpi > 0 else 72))
                            detected_dpi = min(detected_dpi, 2400)

                            # Image reprise telle quelle seulement si elle est
                            # tout le contenu de la page (raw_page_image)
                            try:
                                raw_image_bytes = raw_page_image(doc, page, image_list, best_xref)
                            except Exception:
                                raw_image_bytes = None
                            if raw_image_bytes is not None:
                                detected_dpi = int(max_dpi) if max_dpi > 0 else int(detected_dpi)
                        else:
                            detected_dpi = 300

                        filename = filenames_list[page_num] if page_num < len(filenames_list) else f"{page_num + 1:04d}.jpg"
                        pct = (page_num + 1) / total_pages * 100

                        if raw_image_bytes is not None:
                            out_queue.send(('batch_page', filename, raw_image_bytes, pct, page_num + 1, total_pages))
                            continue

                        mat = fitz.Matrix(detected_dpi / 72.0, detected_dpi / 72.0)
                        pix = page.get_pixmap(matrix=mat, alpha=False)
                        img = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
                        pix = None
                        buf = io.BytesIO()
                        img.save(buf, format='JPEG', quality=100, optimize=True)
                        img = None
                        out_queue.send(('batch_page', filename, buf.getvalue(), pct, page_num + 1, total_pages))
                        buf = None
                        if (page_num + 1) % 10 == 0:
                            gc.collect()
                    except Exception as e:
                        _send_page_error(out_queue, page_num, e)
                        continue
                doc.close()
                doc = None
                out_queue.send(('done',))
            except Exception as e:
                out_queue.send(('error', str(e)[:200]))
            doc = None

        elif kind == 'discard':
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
                doc = None


# ═══════════════════════════════════════════════════════════════════════════════
# Un process dédié par chargement (pas de singleton partagé entre panneaux) —
# chaque PdfLoader/PdfMergeWorker crée et détruit son propre process.
# Un registre faible garde trace des process actifs pour le shutdown global de l'app.
# ═══════════════════════════════════════════════════════════════════════════════
_active_processes: set = set()

def _spawn_pdf_process():
    """Crée un process PDF dédié. Retourne (process, in_q, out_conn)."""
    import multiprocessing
    ctx = multiprocessing.get_context('spawn')
    in_q = ctx.Queue()
    # Pipe duplex=False : out_send dans le process, out_recv dans le QThread
    out_recv, out_send = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_pdf_persistent_process,
        args=(in_q, out_send),
        daemon=True,
    )
    proc.start()
    _active_processes.add(proc)
    return proc, in_q, out_recv

def _kill_pdf_process(proc, in_q, out_conn):
    """Arrête proprement un process PDF dédié."""
    if in_q is not None:
        try:
            in_q.put(('quit',))
        except Exception:
            pass
    if out_conn is not None:
        try:
            out_conn.close()
        except Exception:
            pass
    if proc is not None:
        proc.join(timeout=2)
        if proc.is_alive():
            proc.terminate()
        _active_processes.discard(proc)

def shutdown_pdf_process():
    """Appeler à la fermeture de l'app — tue tous les process PDF encore actifs."""
    for proc in list(_active_processes):
        try:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=2)
        except Exception:
            pass
        _active_processes.discard(proc)


# ═══════════════════════════════════════════════════════════════════════════════
# QThread qui pilote le sous-processus et traduit les messages en signaux Qt
# ═══════════════════════════════════════════════════════════════════════════════
class PdfLoadWorker(QThread):
    progress        = Signal(int, int, int)   # percent, current_page, total_pages
    finished        = Signal(list, bool)       # entries, is_owner_protected
    error           = Signal(str)
    password_error  = Signal()
    empty_pdf       = Signal()
    cancelled       = Signal()

    def __init__(self, filepath: str, dpi: int, process, in_q, out_conn):
        import threading
        super().__init__()
        self._filepath  = filepath
        self._dpi       = dpi
        self._cancelled = threading.Event()
        self._process   = process
        self._in_q      = in_q
        self._out_conn  = out_conn
        # Pages que le process n'a pas pu rendre : messages ('page_error', …)
        # bruts, lus par PdfLoader._on_finished pour avertir l'utilisateur.
        self.page_errors = []

    def _kill_own_process(self):
        """Tue le process dédié à ce chargement."""
        _kill_pdf_process(self._process, self._in_q, self._out_conn)
        self._process = None
        self._in_q    = None
        self._out_conn = None

    def run(self):
        self._in_q.put(('run_opened', self._dpi))
        self._preopen_fallback_sent = False

        temp_entries = []
        total_pages  = 0
        is_owner     = False

        while True:
            if self._cancelled.is_set():
                self._kill_own_process()
                self.cancelled.emit()
                return

            if not self._out_conn.poll(0.05):
                if self._process is None or not self._process.is_alive():
                    self._kill_own_process()
                    self.error.emit("PDF process terminated unexpectedly")
                    return
                continue

            try:
                msg = self._out_conn.recv()
            except Exception:
                self._kill_own_process()
                self.error.emit("PDF pipe broken")
                return

            kind = msg[0]

            if kind == '_debug':
                continue

            elif kind == 'preopen_ok':
                # Normalement déjà consommé par _drain_preopen dans load()
                # mais on l'ignore au cas où il arriverait quand même ici
                continue

            elif kind == 'total':
                _, total_pages, is_owner = msg
                self.progress.emit(0, 0, total_pages)

            elif kind == 'page':
                _, page_num, img_data, used_dpi = msg
                filename = f"page_{page_num + 1:04d}{pdf_image_ext(img_data)}"
                entry = create_entry(filename, img_data, IMAGE_EXTS)
                entry["source"] = "pdf"
                entry["dpi"]    = used_dpi
                from modules.qt.mosaic_canvas import build_qimage_for_entry
                build_qimage_for_entry(entry)
                temp_entries.append(entry)

            elif kind == 'page_error':
                self.page_errors.append(msg)

            elif kind == 'progress':
                _, percent, cur, tot = msg
                self.progress.emit(percent, cur, tot)

            elif kind == 'done':
                break

            elif kind == 'error':
                # Cas particulier : le process n'avait pas de doc pré-ouvert
                # (process redémarré entre preopen et run_opened) → fallback run complet
                if msg[1] == 'No document pre-opened' and not self._preopen_fallback_sent:
                    self._preopen_fallback_sent = True
                    self._in_q.put(('run', self._filepath, self._dpi))
                    continue
                self._kill_own_process()
                self.error.emit(msg[1])
                return

            elif kind == 'password_error':
                self._kill_own_process()
                self.password_error.emit()
                return

            elif kind == 'empty_pdf':
                self._kill_own_process()
                self.empty_pdf.emit()
                return

        self._kill_own_process()
        import gc
        gc.collect()
        self.finished.emit(temp_entries, is_owner)

    def stop(self):
        self._cancelled.set()


# ═══════════════════════════════════════════════════════════════════════════════
# Gestionnaire principal — reproduit load_pdf + start_pdf_loading
# ═══════════════════════════════════════════════════════════════════════════════
class PdfLoader:
    """
    Orchestre le chargement d'un PDF.
    Usage : PdfLoader(parent_window, canvas, state).load(filepath)
    """

    def __init__(self, parent_window, canvas, state):
        self._win                = parent_window
        self._canvas             = canvas
        self._state              = state
        self._worker             = None
        self._overlay_holder     = [None]
        self._cancel_item_holder = [None]
        # Process dédié à ce panneau, créé dans load(), gardé jusqu'à ce que
        # le PdfLoadWorker s'en empare (start_pdf_loading) ou qu'il soit tué
        # (annulation, erreur de preopen).
        self._process  = None
        self._in_q     = None
        self._out_conn = None

    def shutdown_own_process(self):
        """Tue le process PDF dédié à ce panneau, s'il y en a un actif, et
        détache le chargement en cours (appelé notamment à la fermeture du
        fichier : son résultat ne doit plus toucher à l'état du panneau)."""
        if self._process is not None:
            _kill_pdf_process(self._process, self._in_q, self._out_conn)
            self._process = None
            self._in_q    = None
            self._out_conn = None
        if self._worker is not None:
            self._detach_worker()
            self._hide_overlay()

    def _detach_worker(self):
        """Débranche le worker courant et le parque jusqu'à la fin réelle de
        son thread (le worker tue lui-même son process en voyant _cancelled).
        Parqué via park_qthread, pas via son signal finished : PdfLoadWorker
        déclare son propre `finished`, qui masque QThread.finished natif et
        n'est jamais émis après une annulation."""
        worker = self._worker
        if worker is None:
            return
        self._worker = None
        worker._cancelled.set()
        import warnings
        for sig in ('progress', 'finished', 'error', 'cancelled', 'password_error', 'empty_pdf'):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', RuntimeWarning)
                    getattr(worker, sig).disconnect()
            except (RuntimeError, TypeError):
                pass
        from modules.qt.utils import park_qthread
        park_qthread(worker)

    def load(self, filepath: str):
        """Reproduit load_pdf : dialogue DPI puis start_pdf_loading."""
        if not PDF_AVAILABLE:
            _MsgDialog(self._win,
                "messages.errors.pymupdf_not_installed.title",
                "messages.errors.pymupdf_not_installed.message",
            ).show_nonmodal()
            return

        # Nouveau document (voir AppState.doc_generation) : incrémenté dès
        # maintenant, pas au démarrage effectif après le dialogue DPI, pour que
        # les fichiers mis en attente pendant ce dialogue (PanelWidget._load_files)
        # visent bien ce document-ci.
        self._state.doc_generation += 1

        # Process dédié à ce chargement (pas de singleton partagé entre panneaux).
        # Lance le preopen AVANT d'afficher le dialogue DPI — fitz.open() se
        # fait pendant que l'utilisateur choisit le DPI, pas après son clic OK
        self._process, self._in_q, self._out_conn = _spawn_pdf_process()
        self._in_q.put(('preopen', filepath))

        # Vide la out_queue dans un thread pendant que le dialogue est affiché.
        # Ça "chauffe" le canal IPC — sans ça, le premier get() dans le QThread
        # peut prendre 1-2s sur Windows (établissement du pipe multiprocessing).
        import threading
        _preopen_result = [None]
        out_conn = self._out_conn
        def _drain_preopen():
            try:
                while True:
                    if out_conn.poll(5):
                        msg = out_conn.recv()
                        if msg[0] == 'preopen_ok':
                            _preopen_result[0] = msg
                            break
                        elif msg[0] in ('error', 'password_error', 'empty_pdf'):
                            _preopen_result[0] = msg
                            break
                        # _debug ou autre : ignore
                    else:
                        break  # timeout
            except Exception:
                pass
        _drain_thread = threading.Thread(target=_drain_preopen, daemon=True)
        _drain_thread.start()

        dlg = DpiDialog(
            self._win,
            "dialogs.pdf.export_quality_title",
            "dialogs.pdf.quality_export",
        )

        generation = self._state.doc_generation

        def _on_dpi_chosen(selected_dpi):
            if selected_dpi is None:
                # Annulation : dit au process d'abandonner le doc pré-ouvert, puis le tue
                # (pas de réutilisation possible, plus de singleton préchauffé)
                try:
                    self._in_q.put(('discard',))
                except Exception:
                    pass
                self.shutdown_own_process()
                self._state.current_file = None
                self._after_failed_load()
                return
            _when_preopen_done(selected_dpi, 0)

        def _when_preopen_done(selected_dpi, waited_ms):
            # Le preopen est normalement terminé quand l'utilisateur valide le
            # DPI. Sinon (gros PDF), sa fin est attendue par un QTimer et non
            # par join() : l'interface, et donc l'autre panneau, resterait
            # figée jusqu'à 10 s.
            if self._state.doc_generation != generation:
                # Fichier fermé ou autre document ouvert pendant l'attente.
                # Le process n'est arrêté que s'il est encore celui de ce
                # chargement, pas celui d'un chargement plus récent.
                if self._out_conn is out_conn:
                    self.shutdown_own_process()
                return
            if _drain_thread.is_alive() and waited_ms < 10000:
                QTimer.singleShot(50, lambda: _when_preopen_done(selected_dpi, waited_ms + 50))
                return

            # Si le preopen a échoué, on le gère ici avant de démarrer le worker
            result = _preopen_result[0]
            if result is not None and result[0] == 'password_error':
                self.shutdown_own_process()
                self._state.current_file = None
                self._after_failed_load()
                _MsgDialog(self._win,
                    "messages.errors.pdf_password_required.title",
                    "messages.errors.pdf_password_required.message",
                ).show_nonmodal()
                return
            if result is not None and result[0] == 'empty_pdf':
                self.shutdown_own_process()
                self._state.current_file = None
                self._after_failed_load()
                _MsgDialog(self._win,
                    "messages.warnings.empty_pdf.title",
                    "messages.warnings.empty_pdf.message",
                ).show_nonmodal()
                return
            if result is not None and result[0] == 'error':
                self.shutdown_own_process()
                self._state.current_file = None
                self._after_failed_load()
                _MsgDialog(self._win,
                    "messages.errors.pdf_load_failed.title",
                    "messages.errors.pdf_load_failed.message",
                ).show_nonmodal()
                return

            self._start_pdf_loading(filepath, selected_dpi)

        dlg.ask_async(_on_dpi_chosen)

    def _show_overlay(self, percent: int, current_page: int, total_pages: int, dpi: int):
        if self._worker is None:
            return
        if dpi == 0:
            line1 = _("labels.pdf_converting_no_change", total=total_pages)
        else:
            line1 = _("labels.pdf_converting", total=total_pages, dpi=dpi)
        line2 = f"{percent}%"
        line3 = _("labels.pdf_page", current=current_page, total=total_pages)
        _show_canvas_text(self._canvas, f"{line1}\n{line2}\n{line3}", self._overlay_holder)
        self._canvas.viewport().update()
        from modules.qt.web_import_qt import _show_cancel_item
        _show_cancel_item(self._canvas, f"[ {_('buttons.cancel')} ]", self._cancel_item_holder, self.cancel,
                          anchor_lbl=self._overlay_holder[0])

    def _hide_overlay(self):
        _hide_canvas_text(self._canvas, self._overlay_holder)
        _hide_canvas_text(self._canvas, self._cancel_item_holder)

    def cancel(self):
        """Annule le chargement PDF en cours."""
        if self._worker is None:
            return
        self._detach_worker()
        self._hide_overlay()
        self._state.current_file = None
        self._canvas.render_mosaic()
        if hasattr(self._win, '_on_loading_finished'):
            self._win._on_loading_finished()

    def _start_pdf_loading(self, filepath: str, dpi: int):
        """Reproduit start_pdf_loading."""
        st = self._state
        # images_data n'est pas vidé : le panneau était vide à l'ouverture du
        # PDF (PanelWidget._load_files), il ne peut contenir que des pages
        # ajoutées pendant le choix de la résolution (import web,
        # numérisation…), gardées à la fin (archive_loader._install_loaded_pages).
        st.selected_indices.clear()
        st.merge_counter = 0
        st.needs_renumbering = False

        # Vide le canvas
        self._canvas._empty_items.clear()
        self._canvas._items.clear()
        self._canvas._drop_indicator_items.clear()
        self._canvas.scene().clear()

        # Ancien worker éventuellement encore actif (chargements consécutifs) :
        # jamais détruit directement, voir _detach_worker.
        self._detach_worker()

        # Transfère le process pré-ouvert (créé dans load()) au worker, qui en devient
        # seul responsable (kill sur annulation/erreur/fin).
        self._worker = PdfLoadWorker(filepath, dpi, self._process, self._in_q, self._out_conn)
        self._process, self._in_q, self._out_conn = None, None, None

        # Affiche l'overlay après création du worker (sinon _show_overlay retourne immédiatement)
        self._show_overlay(0, 0, 0, dpi)

        # Force le repaint AVANT de démarrer le worker
        from PySide6.QtWidgets import QApplication
        worker = self._worker
        QApplication.processEvents()
        if self._worker is not worker:
            # Annulé (lien Annuler, fermeture du fichier) pendant ce traitement
            # des événements : le worker n'a pas démarré et ne tuera donc
            # jamais lui-même le process qui lui a été confié.
            worker._kill_own_process()
            return

        # Chaque slot vérifie que le worker émetteur est encore le chargement
        # courant : _detach_worker débranche les slots, mais un signal déjà mis
        # en file d'attente avant le débranchement est quand même livré.
        def _current():
            return self._worker is worker

        worker.progress.connect(
            lambda pct, cur, tot: self._show_overlay(pct, cur, tot, dpi) if _current() else None
        )
        worker.finished.connect(
            lambda entries, owner: self._on_finished(entries, owner, filepath, dpi)
            if _current() else None
        )
        worker.cancelled.connect(lambda: self._on_cancelled() if _current() else None)
        worker.error.connect(lambda msg: self._on_error(msg) if _current() else None)
        worker.password_error.connect(lambda: self._on_password_error() if _current() else None)
        worker.empty_pdf.connect(lambda: self._on_empty_pdf() if _current() else None)

        worker.start()

    def _cleanup_worker(self):
        """Libère le worker depuis le slot de son signal de fin (voir dispose_qthread)."""
        if self._worker is not None:
            from modules.qt.utils import dispose_qthread
            dispose_qthread(self._worker)
            self._worker = None

    def _on_finished(self, entries: list, is_owner_protected: bool, filepath: str, dpi: int):
        page_errors = list(getattr(self._worker, 'page_errors', None) or [])
        self._hide_overlay()
        self._cleanup_worker()

        st = self._state
        # Pages ajoutées pendant le chargement (import web, numérisation…)
        # conservées, historique remis à zéro (archive_loader._install_loaded_pages).
        from modules.qt.archive_loader import _install_loaded_pages
        _install_loaded_pages(st, entries)
        st.current_directory = ""
        st.focused_index     = None
        st.modified          = True

        if entries:
            st.needs_renumbering = True
            if hasattr(self._win, '_renumber_btn_action'):
                self._win._renumber_btn_action()

        self._canvas.render_mosaic()

        if hasattr(self._win, '_on_loading_finished'):
            self._win._on_loading_finished()

        _warn_pdf_pages_ignored(self._win, page_errors)

        if is_owner_protected:
            try:
                from modules.qt.pdf_unlock_qt import show_pdf_unlock_dialog
                show_pdf_unlock_dialog(filepath, self._win)
            except Exception:
                pass

    def _on_cancelled(self):
        self._hide_overlay()
        self._cleanup_worker()
        self._state.current_file = None
        self._canvas.render_mosaic()
        if hasattr(self._win, '_on_loading_finished'):
            self._win._on_loading_finished()

    def _after_failed_load(self):
        """Le PDF ne s'ouvre pas (erreur, mot de passe, PDF vide, choix de la
        résolution annulé) : les fichiers déposés pendant son chargement sont
        traités maintenant par le panneau (PanelWidget._run_pending_loads,
        ouverts dans le panneau resté vide). Sans cet appel, ils restaient en
        attente sans jamais être ouverts ni signalés."""
        run_pending = getattr(self._win, '_run_pending_loads', None)
        if run_pending is not None:
            run_pending()

    def _on_error(self, msg: str):
        self._hide_overlay()
        self._cleanup_worker()
        self._state.current_file = None
        self._canvas.render_mosaic()
        _MsgDialog(self._win,
            "messages.errors.pdf_load_failed.title",
            "messages.errors.pdf_load_failed.message",
            {"error": msg},
        ).show_nonmodal()
        self._after_failed_load()

    def _on_password_error(self):
        self._hide_overlay()
        self._cleanup_worker()
        self._state.current_file = None
        self._canvas.render_mosaic()
        _MsgDialog(self._win,
            "messages.errors.pdf_password_required.title",
            "messages.errors.pdf_password_required.message",
        ).show_nonmodal()
        self._after_failed_load()

    def _on_empty_pdf(self):
        self._hide_overlay()
        self._cleanup_worker()
        # Aucun document ouvert : laissé sur le PDF, le panneau passerait pour
        # occupé par un document sans page, et le fichier ouvert ensuite y
        # serait fusionné au lieu d'être ouvert (PanelWidget._load_files).
        self._state.current_file = None
        self._canvas.render_mosaic()
        _MsgDialog(self._win,
            "messages.warnings.empty_pdf.title",
            "messages.warnings.empty_pdf.message",
        ).show_nonmodal()
        self._after_failed_load()


# ═══════════════════════════════════════════════════════════════════════════════
# Worker thread — merge PDF via process séparé (sans fitz dans le process Qt)
# ═══════════════════════════════════════════════════════════════════════════════
class PdfMergeWorker(QThread):
    progress       = Signal(int)        # percent
    finished       = Signal(list, bool) # new_entries, is_owner_protected
    error          = Signal(str)
    password_error = Signal()
    empty_pdf      = Signal()
    cancelled      = Signal()

    def __init__(self, filepath: str, dpi: int, merge_prefix: str):
        import threading
        super().__init__()
        self._filepath     = filepath
        self._dpi          = dpi
        self._merge_prefix = merge_prefix
        self._cancelled    = threading.Event()
        self._process      = None
        self._in_q         = None
        self._out_conn     = None
        # Pages que le process n'a pas pu rendre : messages ('page_error', …)
        # bruts, mis en texte par _warn_pdf_pages_ignored.
        self.page_errors   = []

    def _kill_own_process(self):
        """Tue le process dédié à ce merge."""
        _kill_pdf_process(self._process, self._in_q, self._out_conn)
        self._process  = None
        self._in_q     = None
        self._out_conn = None

    def run(self):
        self._process, self._in_q, self._out_conn = _spawn_pdf_process()
        self._in_q.put(('run_merge', self._filepath, self._dpi, self._merge_prefix))

        new_entries = []
        is_owner    = False

        while True:
            if self._cancelled.is_set():
                self._kill_own_process()
                self.cancelled.emit()
                return

            if not self._out_conn.poll(0.05):
                if self._process is None or not self._process.is_alive():
                    self._kill_own_process()
                    self.error.emit("PDF merge process terminated unexpectedly")
                    return
                continue

            try:
                msg = self._out_conn.recv()
            except Exception:
                self._kill_own_process()
                self.error.emit("PDF merge pipe broken")
                return

            kind = msg[0]

            if kind == '_debug':
                continue

            elif kind == 'total':
                _, _total_pages, is_owner = msg

            elif kind == 'merge_page':
                _, filename, img_data, used_dpi = msg
                entry = create_entry(filename, img_data, IMAGE_EXTS)
                entry["source"] = "pdf"
                entry["dpi"]    = used_dpi
                from modules.qt.mosaic_canvas import build_qimage_for_entry
                build_qimage_for_entry(entry)
                new_entries.append(entry)

            elif kind == 'page_error':
                self.page_errors.append(msg)

            elif kind == 'progress':
                _, percent, _cur, _tot = msg
                self.progress.emit(percent)

            elif kind == 'done':
                break

            elif kind == 'error':
                self._kill_own_process()
                self.error.emit(msg[1])
                return

            elif kind == 'password_error':
                self._kill_own_process()
                self.password_error.emit()
                return

            elif kind == 'empty_pdf':
                self._kill_own_process()
                self.empty_pdf.emit()
                return

        self._kill_own_process()
        import gc
        gc.collect()
        self.finished.emit(new_entries, is_owner)


# ═══════════════════════════════════════════════════════════════════════════════
# import_and_merge_pdf — reproduit import_and_merge_pdf de pdf_loading.py
# ═══════════════════════════════════════════════════════════════════════════════
def import_and_merge_pdf(filepath: str, dpi: int, win, canvas, state):
    """Importe un PDF et fusionne ses pages dans le comics actuel avec préfixe NEW-."""
    if not PDF_AVAILABLE:
        _MsgDialog(win,
            "messages.errors.pymupdf_not_installed.title",
            "messages.errors.pymupdf_not_installed.message",
        ).show_nonmodal()
        return

    state.merge_counter += 1
    merge_prefix = f"NEW{state.merge_counter:02d}-"

    overlay_holder      = [None]
    cancel_item_holder  = [None]
    worker_ref          = [None]
    # Annulée (lien Annuler, fermeture du fichier) : _cancel débranche les
    # slots, mais un résultat déjà mis en file d'attente avant le débranchement
    # est quand même livré ; les slots de fin le vérifient.
    cancelled_flag      = [False]

    def _show(percent):
        if worker_ref[0] is None:
            return
        if dpi == 0:
            line1 = _("labels.pdf_converting_no_change", total=0)
        else:
            line1 = _("labels.pdf_converting", total=0, dpi=dpi)
        _show_canvas_text(canvas, f"{line1}\n{percent}%", overlay_holder)
        from modules.qt.web_import_qt import _show_cancel_item
        _show_cancel_item(canvas, f"[ {_('buttons.cancel')} ]", cancel_item_holder, _cancel,
                          anchor_lbl=overlay_holder[0])

    def _hide():
        _hide_canvas_text(canvas, overlay_holder)
        _hide_canvas_text(canvas, cancel_item_holder)

    def _cancel():
        w = worker_ref[0]
        if w is None:
            return
        cancelled_flag[0] = True
        w._cancelled.set()
        try:
            w.progress.disconnect(on_progress)
            w.finished.disconnect(on_finished)
            w.cancelled.disconnect(on_cancelled)
            w.error.disconnect(on_error)
            w.password_error.disconnect(on_password_error)
            w.empty_pdf.disconnect(on_empty_pdf)
        except RuntimeError:
            pass
        worker_ref[0] = None
        _hide()
        canvas.render_mosaic()

    # Affiche le label immédiatement, avant de créer le worker
    if dpi == 0:
        line1 = _("labels.pdf_converting_no_change", total=0)
    else:
        line1 = _("labels.pdf_converting", total=0, dpi=dpi)
    _show_canvas_text(canvas, f"{line1}\n0%", overlay_holder)
    from modules.qt.web_import_qt import _show_cancel_item
    _show_cancel_item(canvas, f"[ {_('buttons.cancel')} ]", cancel_item_holder, _cancel,
                      anchor_lbl=overlay_holder[0])
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()

    def on_progress(percent):
        _show(percent)

    def on_cancelled():
        _hide()
        canvas.render_mosaic()

    def on_finished(new_entries, is_owner_protected):
        if cancelled_flag[0]:
            return
        _hide()

        if state.doc_generation != doc_generation:
            # Document fermé ou remplacé pendant la fusion : ces pages visaient
            # l'ancien document (voir AppState.doc_generation).
            return

        if new_entries:
            src_name = os.path.basename(filepath)
            for entry in new_entries:
                entry["source_archive"] = src_name
            # Opération en cours sur le document (rotation…) : les pages sont
            # ajoutées à sa fin, sinon son annulation les effacerait.
            state.run_when_idle(lambda: _append_merged(new_entries))
        else:
            canvas.render_mosaic()

        _warn_pdf_pages_ignored(win, worker.page_errors)

        if is_owner_protected:
            try:
                from modules.qt.pdf_unlock_qt import show_pdf_unlock_dialog
                show_pdf_unlock_dialog(filepath, win)
            except Exception:
                pass

    def _append_merged(new_entries):
        from modules.qt.archive_loader import _natural_sort_key as _nsk
        save_state = getattr(canvas, "_save_state_callback", None) or (lambda: None)
        save_state()
        state.images_data.extend(new_entries)
        state.images_data.sort(key=lambda e: _nsk(e["orig_name"]))
        state.modified = True
        state.selected_indices.clear()
        # Point d'annulation APRÈS la fusion : sans lui, Annuler la défairait
        # avec l'action précédente, et Refaire ne la rétablirait jamais.
        save_state()
        canvas.render_mosaic()

    def on_error(msg):
        if cancelled_flag[0]:
            return
        _hide()
        _MsgDialog(win,
            "messages.errors.pdf_import_failed.title",
            "messages.errors.pdf_import_failed.message",
            {"error": msg},
        ).show_nonmodal()

    def on_password_error():
        if cancelled_flag[0]:
            return
        _hide()
        _MsgDialog(win,
            "messages.errors.pdf_password_required.title",
            "messages.errors.pdf_password_required.message",
        ).show_nonmodal()

    def on_empty_pdf():
        if cancelled_flag[0]:
            return
        _hide()
        _MsgDialog(win,
            "messages.warnings.empty_pdf.title",
            "messages.warnings.empty_pdf.message",
        ).show_nonmodal()

    from modules.qt.utils import (dispose_qthread, register_cancel_on_close,
                                  unregister_cancel_on_close)
    worker = PdfMergeWorker(filepath, dpi, merge_prefix)
    worker_ref[0] = worker
    doc_generation = state.doc_generation
    # Fermeture du fichier pendant la fusion : même effet qu'Annuler
    register_cancel_on_close(canvas, _cancel)

    def _cleanup():
        worker_ref[0] = None
        unregister_cancel_on_close(canvas, _cancel)
        dispose_qthread(worker)

    worker.progress.connect(on_progress)
    worker.finished.connect(lambda *_: _cleanup())
    worker.cancelled.connect(lambda: _cleanup())
    worker.error.connect(lambda *_: _cleanup())
    worker.password_error.connect(lambda: _cleanup())
    worker.empty_pdf.connect(lambda: _cleanup())
    worker.finished.connect(on_finished)
    worker.cancelled.connect(on_cancelled)
    worker.error.connect(on_error)
    worker.password_error.connect(on_password_error)
    worker.empty_pdf.connect(on_empty_pdf)

    worker.start()
