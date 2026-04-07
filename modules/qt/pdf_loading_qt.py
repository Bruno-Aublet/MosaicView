"""
modules/qt/pdf_loading_qt.py
Chargement de fichiers PDF pour la version PySide6.
Reproduit à l'identique pdf_loading.py (load_pdf + start_pdf_loading).
"""

import io
import os
import gc

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QButtonGroup,
)

from modules.qt.localization import _, _wt
from modules.qt.entries import create_entry
from modules.qt import state as _state_module
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.canvas_overlay_qt import show_canvas_text as _show_canvas_text, hide_canvas_text as _hide_canvas_text

_orphan_workers: list = []

try:
    import fitz
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

IMAGE_EXTS = (
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
    '.tiff', '.tif', '.ico', '.jfif', '.pjpeg', '.pjp',
)


# ═══════════════════════════════════════════════════════════════════════════════
# Dialogue de sélection DPI — reproduit show_dpi_dialog_for_merge / load_pdf
# ═══════════════════════════════════════════════════════════════════════════════
class DpiDialog(QDialog):
    """Dialogue de sélection du DPI avant chargement PDF."""

    def __init__(self, parent, title_key, label_key):
        super().__init__(parent)
        self.selected_dpi = None
        self._title_key = title_key
        self._label_key = label_key
        self.setModal(True)
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
        self._btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self._btn_ok)
        btn_layout.addWidget(self._btn_cancel)
        layout.addLayout(btn_layout)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

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
        if hasattr(self, '_rb_original'):
            self._update_original_tooltip()

    def _on_ok(self):
        for dpi, rb in self._radios:
            if rb.isChecked():
                self.selected_dpi = dpi
                break
        self.accept()

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
        self._title_key    = title_key
        self._message_key  = message_key
        self._message_kwargs = message_kwargs or {}
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(12)

        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        layout.addWidget(self._lbl)

        self._btn_ok = QPushButton()
        self._btn_ok.clicked.connect(self.accept)
        layout.addWidget(self._btn_ok, alignment=Qt.AlignCenter)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

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
        self._count = count
        self._dpi   = dpi
        self.setModal(True)

        layout = QVBoxLayout(self)
        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        layout.addWidget(self._lbl)

        self._btn_ok = QPushButton()
        self._btn_ok.clicked.connect(self.accept)
        layout.addWidget(self._btn_ok, alignment=Qt.AlignCenter)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

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
    import fitz
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
        with open(filepath, 'rb') as f:
            pdf_bytes = f.read()
        doc = fitz.open(stream=pdf_bytes, filetype='pdf')

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
                        max_dpi = 0
                        best_xref = None
                        page_rect = page.rect
                        page_width_inches  = page_rect.width  / 72.0
                        page_height_inches = page_rect.height / 72.0
                        for img_info in image_list:
                            xref = img_info[0]
                            # Dimensions sans décompression : img_info[2]=width, img_info[3]=height
                            try:
                                img_width  = img_info[2]
                                img_height = img_info[3]
                                if img_width > 0 and img_height > 0 and page_width_inches > 0 and page_height_inches > 0:
                                    img_dpi_x = img_width  / page_width_inches
                                    img_dpi_y = img_height / page_height_inches
                                    img_dpi   = max(img_dpi_x, img_dpi_y)
                                    if img_dpi > max_dpi:
                                        max_dpi = img_dpi
                                        best_xref = xref
                            except Exception:
                                continue

                        if has_text:
                            detected_dpi = max_dpi if max_dpi >= 300 else 300
                        else:
                            detected_dpi = max_dpi if max_dpi > 0 else 72
                        detected_dpi = min(detected_dpi, 2400)

                        # Extraction brute seulement si une seule image et pas de texte (ou haute résolution)
                        use_raw = (
                            best_xref is not None
                            and len(image_list) == 1
                            and (not has_text or max_dpi >= 300)
                        )
                        if use_raw:
                            try:
                                best_base_image = doc.extract_image(best_xref)
                                ext = best_base_image.get("ext", "").lower()
                                if ext in ("jpeg", "jpg", "png", "webp"):
                                    raw_image_bytes = best_base_image["image"]
                                    detected_dpi = int(max_dpi) if max_dpi > 0 else int(detected_dpi)
                            except Exception:
                                pass
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

            except Exception:
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
                        max_dpi = 0
                        best_xref = None
                        page_rect = page.rect
                        page_width_inches  = page_rect.width  / 72.0
                        page_height_inches = page_rect.height / 72.0
                        for img_info in image_list:
                            try:
                                img_width  = img_info[2]
                                img_height = img_info[3]
                                if img_width > 0 and img_height > 0 and page_width_inches > 0 and page_height_inches > 0:
                                    img_dpi_x = img_width  / page_width_inches
                                    img_dpi_y = img_height / page_height_inches
                                    img_dpi   = max(img_dpi_x, img_dpi_y)
                                    if img_dpi > max_dpi:
                                        max_dpi = img_dpi
                                        best_xref = img_info[0]
                            except Exception:
                                continue

                        if has_text:
                            detected_dpi = max_dpi if max_dpi >= 300 else 300
                        else:
                            detected_dpi = max_dpi if max_dpi > 0 else 72
                        detected_dpi = min(detected_dpi, 2400)

                        use_raw = (
                            best_xref is not None
                            and len(image_list) == 1
                            and (not has_text or max_dpi >= 300)
                        )
                        if use_raw:
                            try:
                                best_base_image = doc.extract_image(best_xref)
                                ext = best_base_image.get("ext", "").lower()
                                if ext in ("jpeg", "jpg", "png", "webp"):
                                    raw_image_bytes = best_base_image["image"]
                                    detected_dpi = int(max_dpi) if max_dpi > 0 else int(detected_dpi)
                            except Exception:
                                pass
                    else:
                        detected_dpi = 300

                    if raw_image_bytes is not None:
                        filename = f"{merge_prefix}page_{page_num + 1:04d}.jpg"
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

            except Exception:
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
                            max_dpi  = 0
                            best_xref = None
                            rect = page.rect
                            pw = rect.width / 72.0
                            ph = rect.height / 72.0
                            for img_info in image_list:
                                try:
                                    iw = img_info[2]
                                    ih = img_info[3]
                                    if iw > 0 and ih > 0 and pw > 0 and ph > 0:
                                        img_dpi = max(iw / pw, ih / ph)
                                        if img_dpi > max_dpi:
                                            max_dpi = img_dpi
                                            best_xref = img_info[0]
                                except Exception:
                                    continue
                            detected_dpi = (max(max_dpi, 300) if has_text else (max_dpi if max_dpi > 0 else 72))
                            detected_dpi = min(detected_dpi, 2400)

                            use_raw = (
                                best_xref is not None
                                and len(image_list) == 1
                                and (not has_text or max_dpi >= 300)
                            )
                            if use_raw:
                                try:
                                    best_base_image = doc.extract_image(best_xref)
                                    ext = best_base_image.get("ext", "").lower()
                                    if ext in ("jpeg", "jpg", "png", "webp"):
                                        raw_image_bytes = best_base_image["image"]
                                        detected_dpi = int(max_dpi) if max_dpi > 0 else int(detected_dpi)
                                except Exception:
                                    pass
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
                    except Exception:
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
# Singleton — process préchauffé créé une fois au démarrage
# ═══════════════════════════════════════════════════════════════════════════════
_warm_process  = None
_warm_in_q     = None
_warm_out_conn = None  # côté réception (Pipe) — lu par le QThread
_warm_out_q    = None  # gardé pour compatibilité avec le code existant (alias)

def _ensure_warm_process():
    """Démarre le process préchauffé s'il n'existe pas ou est mort."""
    global _warm_process, _warm_in_q, _warm_out_conn, _warm_out_q
    if _warm_process is not None and _warm_process.is_alive():
        return
    import multiprocessing
    ctx = multiprocessing.get_context('spawn')
    _warm_in_q = ctx.Queue()
    # Pipe duplex=False : out_send dans le process, out_recv dans le QThread
    out_recv, out_send = ctx.Pipe(duplex=False)
    _warm_out_conn = out_recv
    _warm_out_q    = out_recv  # alias pour le code existant
    _warm_process = ctx.Process(
        target=_pdf_persistent_process,
        args=(_warm_in_q, out_send),
        daemon=True,
    )
    _warm_process.start()

def warmup_pdf_process():
    """Appeler au démarrage de l'app pour préchauffer le process fitz."""
    _ensure_warm_process()

def shutdown_pdf_process():
    """Appeler à la fermeture de l'app."""
    global _warm_process, _warm_in_q, _warm_out_conn, _warm_out_q
    global _merge_process, _merge_in_q, _merge_out_conn
    for in_q, out_conn, proc in [
        (_warm_in_q,  _warm_out_conn,  _warm_process),
        (_merge_in_q, _merge_out_conn, _merge_process),
    ]:
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
    _warm_process  = None
    _warm_in_q     = None
    _warm_out_conn = None
    _warm_out_q    = None
    _merge_process  = None
    _merge_in_q     = None
    _merge_out_conn = None


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton — process dédié au merge PDF
# ═══════════════════════════════════════════════════════════════════════════════
_merge_process  = None
_merge_in_q     = None
_merge_out_conn = None

def _ensure_merge_process():
    """Démarre le process merge s'il n'existe pas ou est mort."""
    global _merge_process, _merge_in_q, _merge_out_conn
    if _merge_process is not None and _merge_process.is_alive():
        return
    import multiprocessing
    ctx = multiprocessing.get_context('spawn')
    _merge_in_q = ctx.Queue()
    out_recv, out_send = ctx.Pipe(duplex=False)
    _merge_out_conn = out_recv
    _merge_process = ctx.Process(
        target=_pdf_persistent_process,
        args=(_merge_in_q, out_send),
        daemon=True,
    )
    _merge_process.start()


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

    def __init__(self, filepath: str, dpi: int):
        import threading
        super().__init__()
        self._filepath  = filepath
        self._dpi       = dpi
        self._cancelled = threading.Event()
        self._process   = None

    @staticmethod
    def _kill_warm_process():
        """Tue le process préchauffé et remet les globaux à None."""
        global _warm_process, _warm_in_q, _warm_out_q
        if _warm_process and _warm_process.is_alive():
            _warm_process.terminate()
            _warm_process.join(timeout=2)
        _warm_process = None
        _warm_in_q    = None
        _warm_out_q   = None

    def run(self):
        _warm_in_q.put(('run_opened', self._dpi))
        self._preopen_fallback_sent = False

        temp_entries = []
        total_pages  = 0
        is_owner     = False

        while True:
            if self._cancelled.is_set():
                self._kill_warm_process()
                self.cancelled.emit()
                return

            if not _warm_out_conn.poll(0.05):
                if _warm_process is None or not _warm_process.is_alive():
                    self.error.emit("PDF process terminated unexpectedly")
                    return
                continue

            try:
                msg = _warm_out_conn.recv()
            except Exception:
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
                filename = f"page_{page_num + 1:04d}.jpg"
                entry = create_entry(filename, img_data, IMAGE_EXTS)
                entry["source"] = "pdf"
                entry["dpi"]    = used_dpi
                from modules.qt.mosaic_canvas import build_qimage_for_entry
                build_qimage_for_entry(entry)
                temp_entries.append(entry)

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
                    _warm_in_q.put(('run', self._filepath, self._dpi))
                    continue
                self.error.emit(msg[1])
                return

            elif kind == 'password_error':
                self.password_error.emit()
                return

            elif kind == 'empty_pdf':
                self.empty_pdf.emit()
                return

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

    def load(self, filepath: str):
        """Reproduit load_pdf : dialogue DPI puis start_pdf_loading."""
        if not PDF_AVAILABLE:
            _MsgDialog(self._win,
                "messages.errors.pymupdf_not_installed.title",
                "messages.errors.pymupdf_not_installed.message",
            ).exec()
            return

        # Lance le preopen AVANT d'afficher le dialogue DPI — fitz.open() se
        # fait pendant que l'utilisateur choisit le DPI, pas après son clic OK
        _ensure_warm_process()
        _warm_in_q.put(('preopen', filepath))

        # Vide la out_queue dans un thread pendant que le dialogue est affiché.
        # Ça "chauffe" le canal IPC — sans ça, le premier get() dans le QThread
        # peut prendre 1-2s sur Windows (établissement du pipe multiprocessing).
        import threading
        _preopen_result = [None]
        def _drain_preopen():
            try:
                while True:
                    if _warm_out_conn.poll(5):
                        msg = _warm_out_conn.recv()
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
        if dlg.exec() != QDialog.Accepted or dlg.selected_dpi is None:
            # Annulation : dit au process d'abandonner le doc pré-ouvert
            _warm_in_q.put(('discard',))
            self._state.current_file = None
            return

        # Attend que le preopen soit terminé (normalement déjà fait)
        _drain_thread.join(timeout=10)

        # Si le preopen a échoué, on le gère ici avant de démarrer le worker
        result = _preopen_result[0]
        if result is not None and result[0] == 'password_error':
            self._state.current_file = None
            _MsgDialog(self._win,
                "messages.errors.pdf_password_required.title",
                "messages.errors.pdf_password_required.message",
            ).exec()
            return
        if result is not None and result[0] == 'empty_pdf':
            self._state.current_file = None
            _MsgDialog(self._win,
                "messages.warnings.empty_pdf.title",
                "messages.warnings.empty_pdf.message",
            ).exec()
            return
        if result is not None and result[0] == 'error':
            self._state.current_file = None
            _MsgDialog(self._win,
                "messages.errors.pdf_load_failed.title",
                "messages.errors.pdf_load_failed.message",
            ).exec()
            return

        self._start_pdf_loading(filepath, dlg.selected_dpi)

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
        self._worker._cancelled.set()
        try:
            self._worker.progress.disconnect()
            self._worker.finished.disconnect()
            self._worker.error.disconnect()
            self._worker.cancelled.disconnect()
            self._worker.password_error.disconnect()
            self._worker.empty_pdf.disconnect()
        except RuntimeError:
            pass
        worker = self._worker
        self._worker = None
        worker.setParent(None)
        _orphan_workers.append(worker)
        def _on_done(w=worker):
            try:
                _orphan_workers.remove(w)
            except ValueError:
                pass
            w.deleteLater()
        worker.finished.connect(_on_done)
        self._hide_overlay()
        self._state.current_file = None
        self._canvas.render_mosaic()
        if hasattr(self._win, '_on_loading_finished'):
            self._win._on_loading_finished()

    def _start_pdf_loading(self, filepath: str, dpi: int):
        """Reproduit start_pdf_loading."""
        st = self._state
        st.images_data = []
        st.modified    = False
        st.selected_indices.clear()
        st.merge_counter = 0
        st.needs_renumbering = False

        # Vide le canvas
        self._canvas._empty_items.clear()
        self._canvas._items.clear()
        self._canvas._drop_indicator_items.clear()
        self._canvas.scene().clear()

        # Libère l'ancien worker si présent (chargements consécutifs sans attendre la fin)
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

        self._worker = PdfLoadWorker(filepath, dpi)

        # Affiche l'overlay après création du worker (sinon _show_overlay retourne immédiatement)
        self._show_overlay(0, 0, 0, dpi)

        # Force le repaint AVANT de démarrer le worker
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        self._worker.progress.connect(
            lambda pct, cur, tot: self._show_overlay(pct, cur, tot, dpi)
        )
        self._worker.finished.connect(
            lambda entries, owner: self._on_finished(entries, owner, filepath, dpi)
        )
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.error.connect(self._on_error)
        self._worker.password_error.connect(self._on_password_error)
        self._worker.empty_pdf.connect(self._on_empty_pdf)

        self._worker.start()

    def _cleanup_worker(self):
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _on_finished(self, entries: list, is_owner_protected: bool, filepath: str, dpi: int):
        self._hide_overlay()
        self._cleanup_worker()

        st = self._state
        st.images_data       = entries
        st.all_entries       = list(entries)
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

    def _on_error(self, msg: str):
        self._hide_overlay()
        self._cleanup_worker()
        self._state.current_file = None
        self._canvas.render_mosaic()
        _MsgDialog(self._win,
            "messages.errors.pdf_load_failed.title",
            "messages.errors.pdf_load_failed.message",
            {"error": msg},
        ).exec()

    def _on_password_error(self):
        self._hide_overlay()
        self._cleanup_worker()
        self._state.current_file = None
        self._canvas.render_mosaic()
        _MsgDialog(self._win,
            "messages.errors.pdf_password_required.title",
            "messages.errors.pdf_password_required.message",
        ).exec()

    def _on_empty_pdf(self):
        self._hide_overlay()
        self._cleanup_worker()
        _MsgDialog(self._win,
            "messages.warnings.empty_pdf.title",
            "messages.warnings.empty_pdf.message",
        ).exec()


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

    @staticmethod
    def _kill_merge_process():
        global _merge_process, _merge_in_q, _merge_out_conn
        if _merge_process and _merge_process.is_alive():
            _merge_process.terminate()
            _merge_process.join(timeout=2)
        _merge_process  = None
        _merge_in_q     = None
        _merge_out_conn = None

    def run(self):
        _ensure_merge_process()
        _merge_in_q.put(('run_merge', self._filepath, self._dpi, self._merge_prefix))

        new_entries = []
        is_owner    = False

        while True:
            if self._cancelled.is_set():
                self._kill_merge_process()
                self.cancelled.emit()
                return

            if not _merge_out_conn.poll(0.05):
                if _merge_process is None or not _merge_process.is_alive():
                    self.error.emit("PDF merge process terminated unexpectedly")
                    return
                continue

            try:
                msg = _merge_out_conn.recv()
            except Exception:
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

            elif kind == 'progress':
                _, percent, _cur, _tot = msg
                self.progress.emit(percent)

            elif kind == 'done':
                break

            elif kind == 'error':
                self.error.emit(msg[1])
                return

            elif kind == 'password_error':
                self.password_error.emit()
                return

            elif kind == 'empty_pdf':
                self.empty_pdf.emit()
                return

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
        ).exec()
        return

    state.merge_counter += 1
    merge_prefix = f"NEW{state.merge_counter:02d}-"

    overlay_holder      = [None]
    cancel_item_holder  = [None]
    worker_ref          = [None]

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
        _hide()

        if new_entries:
            src_name = os.path.basename(filepath)
            for entry in new_entries:
                entry["source_archive"] = src_name
            from modules.qt.archive_loader import _natural_sort_key as _nsk
            state.images_data.extend(new_entries)
            state.images_data.sort(key=lambda e: _nsk(e["orig_name"]))
            state.all_entries = list(state.images_data)
            state.modified = True
            state.selected_indices.clear()

        canvas.render_mosaic()

        if is_owner_protected:
            try:
                from modules.qt.pdf_unlock_qt import show_pdf_unlock_dialog
                show_pdf_unlock_dialog(filepath, win)
            except Exception:
                pass

    def on_error(msg):
        _hide()
        _MsgDialog(win,
            "messages.errors.pdf_import_failed.title",
            "messages.errors.pdf_import_failed.message",
            {"error": msg},
        ).exec()

    def on_password_error():
        _hide()
        _MsgDialog(win,
            "messages.errors.pdf_password_required.title",
            "messages.errors.pdf_password_required.message",
        ).exec()

    def on_empty_pdf():
        _hide()
        _MsgDialog(win,
            "messages.warnings.empty_pdf.title",
            "messages.warnings.empty_pdf.message",
        ).exec()

    worker = PdfMergeWorker(filepath, dpi, merge_prefix)
    worker_ref[0] = worker

    def _cleanup():
        worker_ref[0] = None
        worker.deleteLater()

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
