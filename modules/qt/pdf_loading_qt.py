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
    QRadioButton, QButtonGroup, QCheckBox,
)

from modules.qt.app_window_qt import AppWindowMixin, QuestionWindowMixin
from modules.qt.localization import _
from modules.qt.entries import create_entry, mark_altered
from modules.qt.pdf_page_images import image_damage, page_resolution, raw_page_image, original_images
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
from modules.qt.error_log import logged_child_process


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
class DpiDialog(QuestionWindowMixin, QDialog):
    """Dialogue de sélection du DPI avant chargement PDF.

    offer_original_images : ajoute, au-dessus des résolutions, deux cases
    décochées à chaque ouverture :
    - « Extraire les images brutes du PDF » (mode pdf_page_images.original_images).
      Cochée, elle impose « Pas de modification des images » et grise les
      autres résolutions ;
    - en dessous, en retrait, « Recoller les images coupées entre deux
      pages », grisée et décochée tant que la première ne l'est pas.
    Leur état est lu dans `images_only` et `join_cut_images` par le slot de
    result_signal (émis avant la destruction de la fenêtre). Jamais proposées
    au lot PDF → CBZ, qui supprime le PDF ensuite : le texte posé sur les
    images serait perdu sans retour.

    file_name : nom du PDF concerné, affiché sous la question (plusieurs PDF
    ajoutés ensemble ont chacun leur question)."""

    result_signal = Signal(object)   # dpi (int) si OK, None si annulé

    _NAME_HEIGHT = 22

    def __init__(self, parent, title_key, label_key, offer_original_images=False, file_name=None):
        super().__init__(parent)
        self._setup_window(close_answer=None)
        self.setWindowFlags(Qt.Window)
        self.set_window_title(title_key)
        self.selected_dpi = None
        self.images_only = False
        self.join_cut_images = False
        self._label_key = label_key
        self._file_name = file_name
        extra = self._NAME_HEIGHT if file_name else 0
        if offer_original_images:
            self.setFixedSize(540, 330 + extra)
        else:
            self.setFixedSize(450, 260 + extra)

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

        # Nom de fichier venu du disque : jamais interprété comme du HTML
        self._name_lbl = None
        if file_name:
            self._name_lbl = QLabel()
            self._name_lbl.setTextFormat(Qt.PlainText)
            self._name_lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(self._name_lbl)

        layout.addSpacing(10)

        # Cases « images brutes » et « recoller », au-dessus des résolutions
        self._chk_images_only = None
        self._chk_join_cut = None
        self._dpi_before_images_only = 0
        if offer_original_images:
            self._chk_images_only = QCheckBox()
            self._chk_images_only.toggled.connect(self._on_images_only_toggled)
            layout.addWidget(self._chk_images_only)
            # En retrait sous la première : elle n'agit qu'avec elle
            join_row = QHBoxLayout()
            join_row.addSpacing(22)
            self._chk_join_cut = QCheckBox()
            self._chk_join_cut.setEnabled(False)
            join_row.addWidget(self._chk_join_cut)
            join_row.addStretch(1)
            layout.addLayout(join_row)
            layout.addSpacing(6)

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
        self._btn_cancel.clicked.connect(lambda: self._answer(None))
        btn_layout.addWidget(self._btn_ok)
        btn_layout.addWidget(self._btn_cancel)
        layout.addLayout(btn_layout)

        self._connect_language()

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

    def _update_images_only_tooltip(self):
        import html as _html
        escaped = _html.escape(_("dialogs.pdf.original_images_only_tooltip"))
        self._overlay_tip.track(
            self._chk_images_only,
            f'<table style="max-width:360px;white-space:normal;"><tr><td>{escaped}</td></tr></table>')

    def _update_join_cut_tooltip(self):
        import html as _html
        escaped = _html.escape(_("dialogs.pdf.join_cut_images_tooltip"))
        self._overlay_tip.track(
            self._chk_join_cut,
            f'<table style="max-width:360px;white-space:normal;"><tr><td>{escaped}</td></tr></table>')

    def _on_images_only_toggled(self, checked):
        """Cochée : « Pas de modification des images » imposé, autres
        résolutions grisées (les images sont reprises telles quelles), case
        « Recoller » disponible. Décochée : la résolution choisie avant est
        rétablie, case « Recoller » décochée et grisée."""
        self._chk_join_cut.setEnabled(checked)
        if not checked:
            self._chk_join_cut.setChecked(False)
        if checked:
            for dpi, rb in self._radios:
                if rb.isChecked():
                    self._dpi_before_images_only = dpi
            self._rb_original.setChecked(True)
        for dpi, rb in self._radios:
            if dpi != 0:
                rb.setEnabled(not checked)
        if not checked:
            for dpi, rb in self._radios:
                if dpi == self._dpi_before_images_only:
                    rb.setChecked(True)

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self._lbl.setText(_(self._label_key))
        self._lbl.setFont(font)
        if self._name_lbl is not None:
            from PySide6.QtGui import QFontMetrics
            self._name_lbl.setFont(font)
            width = self.width() - 40
            self._name_lbl.setText(QFontMetrics(font).elidedText(self._file_name, Qt.ElideMiddle, width))
        # La feuille de style de la fenêtre impose la couleur du texte : sans
        # :disabled, une résolution ou une case grisée garderait l'aspect
        # d'un choix disponible.
        radio_style = (f"QRadioButton {{ color: {theme['text']}; }} "
                       f"QRadioButton:disabled {{ color: {theme['disabled']}; }}")
        for dpi, key in self._dpi_options:
            for d, rb in self._radios:
                if d == dpi:
                    rb.setText(_(key))
                    rb.setFont(font)
                    rb.setStyleSheet(radio_style)
        self._btn_ok.setText(_("buttons.ok"))
        self._btn_ok.setFont(font)
        self._btn_ok.setStyleSheet(btn_style)
        self._btn_cancel.setText(_("buttons.cancel"))
        self._btn_cancel.setFont(font)
        self._btn_cancel.setStyleSheet(btn_style)
        if self._chk_images_only is not None:
            check_style = (f"QCheckBox {{ background: {theme['bg']}; color: {theme['text']}; }} "
                           f"QCheckBox:disabled {{ color: {theme['disabled']}; }}")
            for chk, key in ((self._chk_images_only, "dialogs.pdf.original_images_only"),
                             (self._chk_join_cut, "dialogs.pdf.join_cut_images")):
                chk.setText(_(key))
                chk.setFont(font)
                chk.setStyleSheet(check_style)
        self._overlay_tip.apply_theme()
        if hasattr(self, '_rb_original'):
            self._update_original_tooltip()
        if self._chk_images_only is not None:
            self._update_images_only_tooltip()
            self._update_join_cut_tooltip()

    def _on_ok(self):
        dpi = None
        for d, rb in self._radios:
            if rb.isChecked():
                dpi = d
                break
        self.images_only = self._chk_images_only is not None and self._chk_images_only.isChecked()
        self.join_cut_images = self.images_only and self._chk_join_cut.isChecked()
        if self.images_only:
            dpi = 0
        self.selected_dpi = dpi
        self._answer(dpi)


# ═══════════════════════════════════════════════════════════════════════════════
# Dialogue de message générique — supporte le changement de langue
# ═══════════════════════════════════════════════════════════════════════════════
class _MsgDialog(AppWindowMixin, QDialog):
    """Remplace QMessageBox pour supporter le changement de langue à la volée."""

    def __init__(self, parent, title_key, message_key, message_kwargs=None):
        super().__init__(parent)
        self._setup_window()
        self.setWindowFlags(Qt.Window)
        self.set_window_title(title_key)
        self._message_key  = message_key
        self._message_kwargs = message_kwargs or {}

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

        self._connect_language()

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self._lbl.setText(_(self._message_key, **self._message_kwargs))
        self._lbl.setFont(font)
        self._btn_ok.setText(_("buttons.ok"))
        self._btn_ok.setFont(font)
        self._btn_ok.setStyleSheet(btn_style)


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
    conversion par lot supprimerait le PDF d'origine). La pile d'une erreur
    inattendue, qui ne passe pas par le canal, va au journal des erreurs."""
    from modules.qt.error_log import log_if_unexpected
    log_if_unexpected(error, "error_log.entry.pdf_error")
    out_queue.send(('page_error', page_num, (str(error) or type(error).__name__)[:200]))


def _send_request_error(out_queue, error):
    """Dans le process PDF : une requête a échoué. Le message part à
    l'application ; la pile d'une erreur inattendue, qui ne passe pas par le
    canal, est écrite dans le journal des erreurs depuis ce process."""
    from modules.qt.error_log import log_if_unexpected
    log_if_unexpected(error, "error_log.entry.pdf_error")
    out_queue.send(('error', (str(error) or type(error).__name__)[:200]))


# Dans le message d'une page trop grande pour le JPEG : remplacé à l'affichage
# par la limite, traduite (pdf_page_error_text).
_JPEG_TOO_LARGE_MARK = "\x00jpeg_too_large"


def pdf_page_error_text(msg):
    """Texte traduit d'un message ('page_error', page_num, message) pour un
    journal ou la liste « fichiers ignorés » : numéro de page (base 1) et
    erreur (texte brut de PyMuPDF, non traduisible)."""
    _kind, page_num, message = msg
    if _JPEG_TOO_LARGE_MARK in message:
        from modules.qt.image_ops import FORMAT_MAX_SIZE
        message = message.replace(_JPEG_TOO_LARGE_MARK, _("messages.errors.pdf_failure.jpeg_too_large",
                                                          max=FORMAT_MAX_SIZE["JPEG"]))
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


class OpeningText:
    """« Ouverture du fichier PDF... » sur la mosaïque, pendant que le process
    PDF démarre et ouvre le fichier, avant la question de qualité.
    show() est rappelée à chaque vérification : le texte suit un changement
    de langue ou de police. Le texte d'accueil d'un panneau vide, qu'il
    recouvrirait, est retiré pendant ce temps et remis par hide()."""

    def __init__(self, canvas):
        self._canvas = canvas
        self._holder = [None]
        self._shown = False
        self._welcome_removed = False

    def show(self):
        canvas = self._canvas
        if not self._shown:
            self._shown = True
            canvas.hold_empty_hint(self)
            scene = canvas.scene()
            from shiboken6 import isValid
            for item in canvas._empty_items:
                if isValid(item) and item.scene() is scene:
                    scene.removeItem(item)
                    self._welcome_removed = True
            canvas._empty_items.clear()
        _show_canvas_text(canvas, _("labels.pdf_opening"), self._holder)

    def hide(self):
        if not self._shown:
            return
        self._shown = False
        _hide_canvas_text(self._canvas, self._holder)
        self._canvas.release_empty_hint(self)
        if self._welcome_removed:
            self._welcome_removed = False
            self._canvas.render_mosaic()


# Canal du process PDF fermé par l'application : elle a demandé l'arrêt
# (annulation, fermeture du fichier) et tuera le process sous peu, sans lire
# la suite. Le process s'arrête alors en silence ; sans cela, l'envoi suivant
# (progression, puis message d'erreur) échouait et le process mourait en
# affichant deux traces d'erreur dans la console.
_CHANNEL_CLOSED = (BrokenPipeError, EOFError)


@logged_child_process
def _pdf_persistent_process(in_queue, out_queue):
    """
    Process préchauffé qui tourne en idle dès le démarrage de l'app.
    Messages acceptés :
      ('preopen', filepath)   — ouvre le PDF pendant que l'utilisateur choisit le DPI
                                répond ('preopen_ok', total_pages, is_owner) ou erreur
      ('run_opened', dpi)     — lance la conversion du doc déjà ouvert
      ('run', filepath, dpi)  — ouvre + convertit en une seule fois (fallback)
      ('run_opened_images', join), ('run_images', filepath, join),
      ('run_merge_images', filepath, merge_prefix, join)
                              — mode « images brutes » (_convert_images), join :
                                recoller les images coupées ; mêmes réponses que
                                run_opened / run / run_merge, plus ('no_images',)
                                si le PDF ne contient aucune image, et
                                ('scan_progress', percent, done, total) pendant
                                le relevé préalable des pages
      ('discard',)            — annule un preopen sans conversion (ex: dialogue annulé)
      ('quit',)               — arrête le process
    Une page dont une image est abîmée mais rendue quand même est précédée de
    ('page_damaged', page_num, raison) à l'ouverture et à la fusion ; le lot
    reçoit ('page_error', page_num, raison) à la place de la page.
    """
    from modules.qt.error_log import start_child_crash_log
    start_child_crash_log()  # pile d'un plantage de MuPDF
    import pymupdf as fitz
    import io
    import gc
    from PIL import Image as PILImage
    from modules.qt.image_ops import FORMAT_MAX_SIZE

    doc = None  # document pré-ouvert, gardé entre preopen et run_opened

    def _save_page_jpeg(img, buf):
        # Testé avant l'écriture : libjpeg écrit sinon sa limite dans la
        # console, hors de portée de l'exception de Pillow, dont le message est
        # repris ici et complété à l'affichage par cette limite.
        if max(img.size) > FORMAT_MAX_SIZE["JPEG"]:
            raise OSError(f"broken data stream when writing image file - {_JPEG_TOO_LARGE_MARK}")
        img.save(buf, format='JPEG', quality=100, optimize=True)

    def _page_damage(page):
        """Raison de l'image abîmée de `page` : à appeler après l'avoir lue,
        les avertissements de MuPDF ayant été vidés avant (_forget_warnings)."""
        return image_damage(doc, page, fitz.TOOLS.mupdf_warnings(reset=True))

    def _forget_warnings():
        fitz.TOOLS.mupdf_warnings(reset=True)

    def _report_damage(page_num, page):
        damage = _page_damage(page)
        if damage:
            out_queue.send(('page_damaged', page_num, damage))

    def _open_doc(filepath):
        nonlocal doc
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass  # sans conséquence : la référence est remplacée juste après
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
                _forget_warnings()
                page = doc[page_num]
                detected_dpi = None
                raw_image_bytes = None

                if dpi > 0:
                    zoom = dpi / 72.0
                    mat = fitz.Matrix(zoom, zoom)
                    pix = page.get_pixmap(matrix=mat, alpha=False)
                else:
                    image_list = page.get_images(full=True)

                    if image_list:
                        detected_dpi, real_dpi, best_xref = page_resolution(doc, page, image_list)

                        # Image reprise telle quelle seulement si elle est
                        # tout le contenu de la page (raw_page_image)
                        try:
                            raw_image_bytes = raw_page_image(doc, page, image_list, best_xref)
                        except Exception:
                            raw_image_bytes = None
                        if raw_image_bytes is not None:
                            detected_dpi = int(real_dpi)
                    else:
                        detected_dpi = 300

                    if raw_image_bytes is not None:
                        _report_damage(page_num, page)
                        percent = int((page_num + 1) / total_pages * 100)
                        out_queue.send(('page', page_num, raw_image_bytes, detected_dpi))
                        out_queue.send(('progress', percent, page_num + 1, total_pages))
                        continue

                    mat = fitz.Matrix(detected_dpi / 72.0, detected_dpi / 72.0)
                    pix = page.get_pixmap(matrix=mat, alpha=False)

                img = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
                pix = None
                buf = io.BytesIO()
                _save_page_jpeg(img, buf)
                img_data = buf.getvalue()
                img = None
                buf = None

                used_dpi = detected_dpi if (dpi == 0 and detected_dpi is not None) else dpi
                percent = int((page_num + 1) / total_pages * 100)
                _report_damage(page_num, page)
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
                _forget_warnings()
                page = doc[page_num]
                detected_dpi = None
                raw_image_bytes = None

                if dpi > 0:
                    zoom = dpi / 72.0
                    mat = fitz.Matrix(zoom, zoom)
                    pix = page.get_pixmap(matrix=mat, alpha=False)
                else:
                    image_list = page.get_images(full=True)

                    if image_list:
                        detected_dpi, real_dpi, best_xref = page_resolution(doc, page, image_list)

                        # Image reprise telle quelle seulement si elle est
                        # tout le contenu de la page (raw_page_image)
                        try:
                            raw_image_bytes = raw_page_image(doc, page, image_list, best_xref)
                        except Exception:
                            raw_image_bytes = None
                        if raw_image_bytes is not None:
                            detected_dpi = int(real_dpi)
                    else:
                        detected_dpi = 300

                    if raw_image_bytes is not None:
                        _report_damage(page_num, page)
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
                _save_page_jpeg(img, buf)
                img_data = buf.getvalue()
                img = None
                buf = None

                used_dpi = detected_dpi if (dpi == 0 and detected_dpi is not None) else dpi
                filename  = f"{merge_prefix}page_{page_num + 1:04d}.jpg"
                percent   = int((page_num + 1) / total_pages * 100)
                _report_damage(page_num, page)
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

    def _xref_png(xref):
        """PNG de l'image `xref` décodée par PyMuPDF (/Decode appliqué),
        convertie en RVB si elle est en CMJN (PNG ne le prend pas)."""
        pix = fitz.Pixmap(doc, xref)
        if pix.n - pix.alpha >= 4:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        return pix.tobytes("png")

    def _convert_images(merge_prefix=None, join=False):
        """Mode « images brutes » : seules les images du PDF, et, si `join`,
        les images coupées entre deux pages recollées quand la découpe est
        certaine (pdf_page_images.original_images).
        Envoie ('page', index, …) à l'ouverture, ('merge_page', nom, …) à la
        fusion (merge_prefix), numérotées dans l'ordre des images ; une image
        illisible est signalée par _send_page_error. ('no_images',) si le PDF
        n'a vraiment aucune image ; avec seulement des images illisibles,
        ('done',), pour que l'utilisateur voie leurs erreurs."""
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

        # Le relevé préalable décode les images : MuPDF ne dira plus rien
        # d'elles ensuite. Ses avertissements sont lus à la fin de chaque page.
        damaged_pages = {}
        _forget_warnings()

        def _on_scan(done, total):
            if done:
                damage = _page_damage(doc[done - 1])
                if damage:
                    damaged_pages[done - 1] = damage
            out_queue.send(('scan_progress', int(done / total * 100), done, total))

        found = unreadable = 0
        for item in original_images(doc, _xref_png, join_cut_images=join, on_scan=_on_scan):
            page_num = item["page"]
            if "error" in item:
                unreadable += 1
                _send_page_error(out_queue, page_num, item["error"])
            else:
                found += 1
                data = item["data"]
                # Une image recollée s'étend sur plusieurs pages
                for index in range(page_num, page_num + item["pieces"]):
                    if index in damaged_pages:
                        out_queue.send(('page_damaged', page_num, damaged_pages[index]))
                        break
                if merge_prefix is None:
                    out_queue.send(('page', found - 1, data, item["dpi"]))
                else:
                    filename = f"{merge_prefix}page_{found:04d}{pdf_image_ext(data)}"
                    out_queue.send(('merge_page', filename, data, item["dpi"]))
                if found % 10 == 0:
                    gc.collect()
            percent = int((page_num + 1) / total_pages * 100)
            out_queue.send(('progress', percent, page_num + 1, total_pages))

        doc.close()
        doc = None
        out_queue.send(('done',) if found or unreadable else ('no_images',))

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
                    pass  # sans conséquence : le process se termine
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
            except _CHANNEL_CLOSED:
                return  # arrêt demandé par l'application, voir _CHANNEL_CLOSED
            except Exception as e:
                _send_request_error(out_queue, e)
                doc = None

        elif kind == 'run_opened':
            _, dpi = msg
            if doc is None:
                out_queue.send(('error', 'No document pre-opened'))
                continue
            try:
                _convert(dpi)
            except _CHANNEL_CLOSED:
                return  # arrêt demandé par l'application, voir _CHANNEL_CLOSED
            except Exception as e:
                _send_request_error(out_queue, e)
            doc = None

        elif kind == 'run':
            _, filepath, dpi = msg
            try:
                _open_doc(filepath)
                _convert(dpi)
            except _CHANNEL_CLOSED:
                return  # arrêt demandé par l'application, voir _CHANNEL_CLOSED
            except Exception as e:
                _send_request_error(out_queue, e)
            doc = None

        elif kind == 'run_merge':
            _, filepath, dpi, merge_prefix = msg
            try:
                _open_doc(filepath)
                _convert_merge(dpi, merge_prefix)
            except _CHANNEL_CLOSED:
                return  # arrêt demandé par l'application, voir _CHANNEL_CLOSED
            except Exception as e:
                _send_request_error(out_queue, e)
            doc = None

        elif kind == 'run_opened_images':
            _, join = msg
            if doc is None:
                out_queue.send(('error', 'No document pre-opened'))
                continue
            try:
                _convert_images(join=join)
            except _CHANNEL_CLOSED:
                return  # arrêt demandé par l'application, voir _CHANNEL_CLOSED
            except Exception as e:
                _send_request_error(out_queue, e)
            doc = None

        elif kind == 'run_images':
            _, filepath, join = msg
            try:
                _open_doc(filepath)
                _convert_images(join=join)
            except _CHANNEL_CLOSED:
                return  # arrêt demandé par l'application, voir _CHANNEL_CLOSED
            except Exception as e:
                _send_request_error(out_queue, e)
            doc = None

        elif kind == 'run_merge_images':
            _, filepath, merge_prefix, join = msg
            try:
                _open_doc(filepath)
                _convert_images(merge_prefix, join=join)
            except _CHANNEL_CLOSED:
                return  # arrêt demandé par l'application, voir _CHANNEL_CLOSED
            except Exception as e:
                _send_request_error(out_queue, e)
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
                        pass  # repli : fenêtre de qualité sans aperçu de la première page
                    # Image gardée en mémoire par MuPDF, qui ne dirait plus rien
                    # d'une image abîmée lors de la conversion (image_damage)
                    fitz.TOOLS.store_shrink(100)
                    out_queue.send(('batch_ready', total, ratios, thumb_bytes, is_owner))
            except _CHANNEL_CLOSED:
                return  # arrêt demandé par l'application, voir _CHANNEL_CLOSED
            except Exception as e:
                _send_request_error(out_queue, e)
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
                        _forget_warnings()
                        page            = doc[page_num]
                        image_list      = page.get_images(full=True)
                        raw_image_bytes = None

                        if image_list:
                            detected_dpi, _real_dpi, best_xref = page_resolution(doc, page, image_list)

                            # Image reprise telle quelle seulement si elle est
                            # tout le contenu de la page (raw_page_image)
                            try:
                                raw_image_bytes = raw_page_image(doc, page, image_list, best_xref)
                            except Exception:
                                raw_image_bytes = None
                        else:
                            detected_dpi = 300

                        filename = filenames_list[page_num] if page_num < len(filenames_list) else f"{page_num + 1:04d}.jpg"
                        pct = (page_num + 1) / total_pages * 100

                        if raw_image_bytes is not None:
                            damage = _page_damage(page)
                            if damage:
                                out_queue.send(('page_error', page_num, damage[:200]))
                                continue
                            out_queue.send(('batch_page', filename, raw_image_bytes, pct, page_num + 1, total_pages))
                            continue

                        mat = fitz.Matrix(detected_dpi / 72.0, detected_dpi / 72.0)
                        pix = page.get_pixmap(matrix=mat, alpha=False)
                        img = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
                        pix = None
                        buf = io.BytesIO()
                        _save_page_jpeg(img, buf)
                        img = None
                        # Le PDF serait supprimé après la conversion : sa page
                        # abîmée compte comme une erreur, qui le fait conserver.
                        damage = _page_damage(page)
                        if damage:
                            out_queue.send(('page_error', page_num, damage[:200]))
                            continue
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
            except _CHANNEL_CLOSED:
                return  # arrêt demandé par l'application, voir _CHANNEL_CLOSED
            except Exception as e:
                _send_request_error(out_queue, e)
            doc = None

        elif kind == 'discard':
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass  # sans conséquence : document abandonné, la référence est retirée
                doc = None


# ═══════════════════════════════════════════════════════════════════════════════
# Un process dédié par chargement (pas de singleton partagé entre panneaux) —
# chaque PdfLoader/PdfMergeWorker crée et détruit son propre process.
# Un registre faible garde trace des process actifs pour le shutdown global de l'app.
# ═══════════════════════════════════════════════════════════════════════════════
_active_processes: set = set()

# Erreur émise par PdfLoadWorker / PdfMergeWorker quand leur process PDF est
# mort (plantage natif de MuPDF sur un PDF malformé) : marqueur, traduit à
# l'affichage par pdf_error_text pour suivre un changement de langue.
PDF_PROCESS_STOPPED = "\x00pdf_process_stopped"


def pdf_error_text(msg):
    """Texte affiché d'une erreur émise par un worker PDF (langue courante) :
    le marqueur PDF_PROCESS_STOPPED est traduit, tout autre message (erreur
    rendue par PyMuPDF) est gardé tel quel."""
    if msg == PDF_PROCESS_STOPPED:
        return _("messages.errors.pdf_failure.process_stopped")
    return msg


# Message rendu par read_pdf_message quand le process PDF est mort ; jamais
# envoyé par le process lui-même.
PDF_STOPPED_MESSAGE = ('process_stopped',)


def read_pdf_message(conn, process, timeout):
    """Lecture commune du canal d'un process PDF (chargement, fusion, lots,
    import des métadonnées) : le message suivant, None si aucun n'est arrivé
    dans le délai (process vivant), ou PDF_STOPPED_MESSAGE si le process est
    mort. Un process mort (plantage natif de MuPDF) ferme le canal : poll()
    peut alors lever BrokenPipeError et recv() EOFError ; non rattrapées, elles
    arrêtaient le lecteur sans signal de fin. Toute autre erreur de lecture
    (message illisible) rend aussi le canal inutilisable : même issue. Les
    messages envoyés avant la mort sont d'abord tous lus."""
    try:
        if conn.poll(timeout):
            return conn.recv()
        if process is None or not process.is_alive():
            # Dernier message arrivé entre le délai et la fin du process
            if conn.poll(0):
                return conn.recv()
            return _stopped()
    except Exception:
        return _stopped()
    return None


def _stopped():
    """Process PDF mort : la trace de son plantage natif (fichier de secours)
    passe aussitôt dans le journal des erreurs, sans attendre la fermeture."""
    from modules.qt.error_log import collect_crash_buffers
    collect_crash_buffers()
    return PDF_STOPPED_MESSAGE


def _spawn_pdf_process():
    """Crée un process PDF dédié. Retourne (process, in_q, out_conn)."""
    import multiprocessing
    from modules.qt.error_log import pass_titles_to_children
    pass_titles_to_children()  # titres traduits de ses entrées du journal
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
            pass  # sans conséquence : le process est terminé de force ci-dessous s'il reste actif
    if out_conn is not None:
        try:
            out_conn.close()
        except Exception:
            pass  # sans conséquence : canal déjà fermé
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
            pass  # sans conséquence : fermeture de l'application, la fin du process parent l'arrête
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
    no_images       = Signal()                 # mode « images brutes », PDF sans image
    scanning        = Signal(int, int, int)    # mode « images brutes » : relevé des pages (percent, page, total)
    cancelled       = Signal()

    def __init__(self, filepath: str, dpi: int, process, in_q, out_conn, images_only: bool = False,
                 join_cut_images: bool = False):
        import threading
        super().__init__()
        self._filepath  = filepath
        self._dpi       = dpi
        self._images_only = images_only
        self._join_cut_images = join_cut_images
        self._cancelled = threading.Event()
        self._process   = process
        self._in_q      = in_q
        self._out_conn  = out_conn
        # Pages que le process n'a pas pu rendre : messages ('page_error', …)
        # bruts, lus par PdfLoader._on_finished pour avertir l'utilisateur.
        self.page_errors = []
        # Pages déjà lues, comptées par l'indicateur de mémoire du panneau
        # pendant le chargement (memory_usage.reading_started)
        self.pages_read = []

    def _kill_own_process(self):
        """Tue le process dédié à ce chargement."""
        _kill_pdf_process(self._process, self._in_q, self._out_conn)
        self._process = None
        self._in_q    = None
        self._out_conn = None

    def run(self):
        self._in_q.put(('run_opened_images', self._join_cut_images) if self._images_only
                       else ('run_opened', self._dpi))
        self._preopen_fallback_sent = False

        temp_entries = self.pages_read
        total_pages  = 0
        is_owner     = False
        next_page_damaged = False

        while True:
            if self._cancelled.is_set():
                self._kill_own_process()
                self.cancelled.emit()
                return

            msg = read_pdf_message(self._out_conn, self._process, 0.05)
            if msg is None:
                continue
            if msg is PDF_STOPPED_MESSAGE:
                self._kill_own_process()
                self.error.emit(PDF_PROCESS_STOPPED)
                return

            kind = msg[0]

            if kind == '_debug':
                continue

            elif kind == 'preopen_ok':
                # Déjà lu par PdfLoader.load avant la question de qualité ;
                # ignoré s'il arrivait quand même ici
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
                if next_page_damaged and not entry.get("is_corrupted"):
                    mark_altered(entry)
                next_page_damaged = False
                from modules.qt.mosaic_canvas import build_qimage_for_entry
                build_qimage_for_entry(entry)
                temp_entries.append(entry)

            elif kind == 'page_damaged':
                next_page_damaged = True

            elif kind == 'page_error':
                self.page_errors.append(msg)

            elif kind == 'progress':
                _, percent, cur, tot = msg
                self.progress.emit(percent, cur, tot)

            elif kind == 'scan_progress':
                _, percent, cur, tot = msg
                self.scanning.emit(percent, cur, tot)

            elif kind == 'done':
                break

            elif kind == 'error':
                # Cas particulier : le process n'avait pas de doc pré-ouvert
                # (process redémarré entre preopen et run_opened) → fallback run complet
                if msg[1] == 'No document pre-opened' and not self._preopen_fallback_sent:
                    self._preopen_fallback_sent = True
                    self._in_q.put(('run_images', self._filepath, self._join_cut_images)
                                   if self._images_only else ('run', self._filepath, self._dpi))
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

            elif kind == 'no_images':
                self._kill_own_process()
                self.no_images.emit()
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
        from modules.qt.memory_usage import reading_finished
        reading_finished(self._state, worker.pages_read)
        import warnings
        for sig in ('progress', 'finished', 'error', 'cancelled', 'password_error', 'empty_pdf',
                    'no_images', 'scanning'):
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
        # Le PDF y est ouvert d'abord, et la question de qualité n'est posée
        # que s'il s'ouvre : un PDF protégé par mot de passe, abîmé ou sans
        # page n'affiche que son message d'erreur, jamais une question sans objet.
        self._process, self._in_q, self._out_conn = _spawn_pdf_process()
        self._in_q.put(('preopen', filepath))
        process, out_conn = self._process, self._out_conn
        generation = self._state.doc_generation
        # « Ouverture du fichier PDF... » pendant l'attente (le process met
        # plus d'une seconde à démarrer), retiré avant la question ou l'erreur
        opening = OpeningText(self._canvas)

        def _abandoned():
            # Process arrêté entre-temps (fichier fermé : shutdown_own_process),
            # ou autre document ouvert dans le panneau. Le process n'est arrêté
            # que s'il est encore celui de ce chargement.
            if self._out_conn is not out_conn:
                return True
            if self._state.doc_generation != generation:
                self.shutdown_own_process()
                return True
            return False

        def _wait_preopen():
            # Lu sur le thread de l'interface, seul lecteur du canal jusqu'à la
            # remise du process au worker.
            if _abandoned():
                opening.hide()
                return
            msg = read_pdf_message(out_conn, process, 0)
            if msg is None:
                opening.show()
                QTimer.singleShot(50, _wait_preopen)
                return
            if msg is PDF_STOPPED_MESSAGE:
                msg = ('error', PDF_PROCESS_STOPPED)
            if msg[0] == 'preopen_ok':
                if msg[1] > 0:
                    opening.hide()
                    _ask()
                    return
                msg = ('empty_pdf',)
            elif msg[0] not in ('error', 'password_error'):
                QTimer.singleShot(50, _wait_preopen)
                return
            opening.hide()
            self._on_preopen_failed(msg)

        def _ask():
            dlg = DpiDialog(
                self._win,
                "dialogs.pdf.export_quality_title",
                "dialogs.pdf.quality_export",
                offer_original_images=True,
                file_name=os.path.basename(filepath),
            )

            def _on_dpi_chosen(selected_dpi):
                if _abandoned():
                    return
                # Lus pendant l'émission de result_signal, avant la destruction de la fenêtre
                image_options = (dlg.images_only, dlg.join_cut_images)
                if selected_dpi is None:
                    # Annulation : dit au process d'abandonner le doc pré-ouvert, puis le tue
                    try:
                        self._in_q.put(('discard',))
                    except Exception:
                        pass  # sans conséquence : le process est arrêté juste après
                    self.shutdown_own_process()
                    self._state.current_file = None
                    self._after_failed_load()
                    return
                self._start_pdf_loading(filepath, selected_dpi, *image_options)

            dlg.ask_async(_on_dpi_chosen)

        _wait_preopen()

    def _on_preopen_failed(self, result):
        """Le PDF ne s'ouvre pas : `result` est le message du process
        (password_error, empty_pdf ou error). Aucun worker n'a été lancé."""
        self.shutdown_own_process()
        self._state.current_file = None
        self._after_failed_load()
        if result[0] == 'password_error':
            _MsgDialog(self._win,
                "messages.errors.pdf_password_required.title",
                "messages.errors.pdf_password_required.message",
            ).show_nonmodal()
        elif result[0] == 'empty_pdf':
            _MsgDialog(self._win,
                "messages.warnings.empty_pdf.title",
                "messages.warnings.empty_pdf.message",
            ).show_nonmodal()
        else:
            from modules.qt.dialogs_qt import ErrorDialog
            # Textes recalculés à l'affichage (changement de langue)
            ErrorDialog(self._win,
                        "messages.errors.pdf_load_failed.title",
                        lambda m=result[1]: _("messages.errors.pdf_load_failed.message",
                                              error=pdf_error_text(m)),
                        ).show_nonmodal()

    def _show_overlay(self, percent: int, current_page: int, total_pages: int, dpi: int,
                      analyzing: bool = False):
        """analyzing : relevé préalable des pages du mode « images brutes »."""
        if self._worker is None:
            return
        if analyzing:
            line1 = _("labels.pdf_analyzing", total=total_pages)
        elif dpi == 0:
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

    def _start_pdf_loading(self, filepath: str, dpi: int, images_only: bool = False,
                           join_cut_images: bool = False):
        """Reproduit start_pdf_loading. images_only : mode « images brutes »
        (seules les images du PDF) ; join_cut_images : recoller les images
        coupées entre deux pages quand la découpe est certaine."""
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
        self._worker = PdfLoadWorker(filepath, dpi, self._process, self._in_q, self._out_conn,
                                     images_only=images_only, join_cut_images=join_cut_images)
        self._process, self._in_q, self._out_conn = None, None, None
        # Pages lues comptées dans la mémoire du panneau avant d'arriver dans
        # le document ; retirées par _detach_worker ou _cleanup_worker
        from modules.qt.memory_usage import reading_started
        reading_started(st, self._worker.pages_read)

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
        worker.no_images.connect(lambda: self._on_no_images() if _current() else None)
        worker.scanning.connect(
            lambda pct, cur, tot: self._show_overlay(pct, cur, tot, dpi, analyzing=True) if _current() else None
        )

        worker.start()

    def _cleanup_worker(self):
        """Libère le worker depuis le slot de son signal de fin (voir
        dispose_qthread). Ses pages ne sont plus comptées comme pages en cours
        de lecture : remises au document dans le même slot (_on_finished), ou
        abandonnées."""
        if self._worker is not None:
            from modules.qt.memory_usage import reading_finished
            from modules.qt.utils import dispose_qthread
            reading_finished(self._state, self._worker.pages_read)
            dispose_qthread(self._worker)
            self._worker = None

    def _on_finished(self, entries: list, is_owner_protected: bool, filepath: str, dpi: int):
        page_errors = list(getattr(self._worker, 'page_errors', None) or [])
        self._hide_overlay()
        self._cleanup_worker()

        if not entries:
            # Aucune page n'a pu être produite : aucun document ouvert (même
            # raison que _on_empty_pdf), seules les erreurs sont montrées.
            self._state.current_file = None
            self._canvas.render_mosaic()
            _warn_pdf_pages_ignored(self._win, page_errors)
            self._after_failed_load()
            return

        st = self._state
        # Pages ajoutées pendant le chargement (import web, numérisation…)
        # conservées, historique remis à zéro (archive_loader._install_loaded_pages).
        from modules.qt.archive_loader import _install_loaded_pages
        _install_loaded_pages(st, entries)
        st.current_directory = ""
        st.focused_index     = None
        st.modified          = True
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
                pass  # repli : PDF ouvert sans proposer de copie déverrouillée

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
        attente sans jamais être ouverts ni signalés.
        L'onglet mosaïque et le titre de la fenêtre, qui affichaient déjà le
        nom du PDF, sont remis à jour : current_file vient d'être remis à None
        (_refresh_tabs rafraîchit les deux)."""
        refresh_tabs = getattr(self._win, '_refresh_tabs', None)
        if refresh_tabs is not None:
            refresh_tabs()
        run_pending = getattr(self._win, '_run_pending_loads', None)
        if run_pending is not None:
            run_pending()

    def _on_error(self, msg: str):
        self._hide_overlay()
        self._cleanup_worker()
        self._state.current_file = None
        self._canvas.render_mosaic()
        from modules.qt.dialogs_qt import ErrorDialog
        # Textes recalculés à l'affichage (changement de langue)
        ErrorDialog(self._win,
                    "messages.errors.pdf_load_failed.title",
                    lambda m=msg: _("messages.errors.pdf_load_failed.message",
                                    error=pdf_error_text(m)),
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

    def _on_no_images(self):
        """Mode « images brutes » sur un PDF sans image : aucun document
        ouvert (même raison que _on_empty_pdf), et le message invite à
        l'ouvrir page par page."""
        self._hide_overlay()
        self._cleanup_worker()
        self._state.current_file = None
        self._canvas.render_mosaic()
        _MsgDialog(self._win,
            "messages.warnings.pdf_no_images.title",
            "messages.warnings.pdf_no_images.message",
        ).show_nonmodal()
        self._after_failed_load()


# ═══════════════════════════════════════════════════════════════════════════════
# Worker thread — merge PDF via process séparé (sans fitz dans le process Qt)
# ═══════════════════════════════════════════════════════════════════════════════
class PdfMergeWorker(QThread):
    progress       = Signal(int, int, int)  # percent, current_page, total_pages
    finished       = Signal(list, bool)     # new_entries, is_owner_protected
    error          = Signal(str)
    password_error = Signal()
    empty_pdf      = Signal()
    no_images      = Signal()               # mode « images brutes », PDF sans image
    scanning       = Signal(int, int, int)  # mode « images brutes » : relevé des pages (percent, page, total)
    cancelled      = Signal()

    def __init__(self, filepath: str, dpi: int, merge_prefix: str, images_only: bool = False,
                 join_cut_images: bool = False, pdf_process=None):
        """pdf_process : (process, in_q, out_conn) qui a déjà ouvert le PDF
        avant la question de qualité (ask_pdf_merge_quality) ; le worker en
        devient seul responsable. Sans lui, run() en lance un."""
        import threading
        super().__init__()
        self._filepath     = filepath
        self._dpi          = dpi
        self._merge_prefix = merge_prefix
        self._images_only  = images_only
        self._join_cut_images = join_cut_images
        self._cancelled    = threading.Event()
        self._process, self._in_q, self._out_conn = pdf_process or (None, None, None)
        # Pages que le process n'a pas pu rendre : messages ('page_error', …)
        # bruts, mis en texte par _warn_pdf_pages_ignored.
        self.page_errors   = []
        # Pages déjà lues, comptées par l'indicateur de mémoire du panneau
        # pendant l'ajout (memory_usage.reading_started)
        self.pages_read    = []

    def _kill_own_process(self):
        """Tue le process dédié à ce merge."""
        _kill_pdf_process(self._process, self._in_q, self._out_conn)
        self._process  = None
        self._in_q     = None
        self._out_conn = None

    def run(self):
        if self._process is None:
            self._process, self._in_q, self._out_conn = _spawn_pdf_process()
        # Le PDF est rouvert par la commande : le document ouvert avant la
        # question de qualité est refermé par le process avant.
        if self._images_only:
            self._in_q.put(('run_merge_images', self._filepath, self._merge_prefix,
                            self._join_cut_images))
        else:
            self._in_q.put(('run_merge', self._filepath, self._dpi, self._merge_prefix))

        new_entries = self.pages_read
        is_owner    = False
        next_page_damaged = False

        while True:
            if self._cancelled.is_set():
                self._kill_own_process()
                self.cancelled.emit()
                return

            msg = read_pdf_message(self._out_conn, self._process, 0.05)
            if msg is None:
                continue
            if msg is PDF_STOPPED_MESSAGE:
                self._kill_own_process()
                self.error.emit(PDF_PROCESS_STOPPED)
                return

            kind = msg[0]

            if kind == '_debug':
                continue

            elif kind == 'total':
                _, total_pages, is_owner = msg
                self.progress.emit(0, 0, total_pages)

            elif kind == 'merge_page':
                _, filename, img_data, used_dpi = msg
                entry = create_entry(filename, img_data, IMAGE_EXTS)
                entry["source"] = "pdf"
                entry["dpi"]    = used_dpi
                if next_page_damaged and not entry.get("is_corrupted"):
                    mark_altered(entry)
                next_page_damaged = False
                from modules.qt.mosaic_canvas import build_qimage_for_entry
                build_qimage_for_entry(entry)
                new_entries.append(entry)

            elif kind == 'page_damaged':
                next_page_damaged = True

            elif kind == 'page_error':
                self.page_errors.append(msg)

            elif kind == 'progress':
                _, percent, cur, tot = msg
                self.progress.emit(percent, cur, tot)

            elif kind == 'scan_progress':
                _, percent, cur, tot = msg
                self.scanning.emit(percent, cur, tot)

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

            elif kind == 'no_images':
                self._kill_own_process()
                self.no_images.emit()
                return

        self._kill_own_process()
        import gc
        gc.collect()
        self.finished.emit(new_entries, is_owner)


# ═══════════════════════════════════════════════════════════════════════════════
# Ajout d'un PDF au comic ouvert : question de qualité, puis import_and_merge_pdf
# ═══════════════════════════════════════════════════════════════════════════════
def _show_pdf_merge_failure(win, msg):
    """Message de l'échec d'un ajout de PDF. msg : message du process
    ('error', texte), ('password_error',), ('empty_pdf',) ou ('no_images',)."""
    kind = msg[0]
    if kind == 'password_error':
        _MsgDialog(win,
            "messages.errors.pdf_password_required.title",
            "messages.errors.pdf_password_required.message",
        ).show_nonmodal()
    elif kind == 'empty_pdf':
        _MsgDialog(win,
            "messages.warnings.empty_pdf.title",
            "messages.warnings.empty_pdf.message",
        ).show_nonmodal()
    elif kind == 'no_images':
        _MsgDialog(win,
            "messages.warnings.pdf_no_images.title",
            "messages.warnings.pdf_no_images.message",
        ).show_nonmodal()
    else:
        from modules.qt.dialogs_qt import ErrorDialog
        # Textes recalculés à l'affichage (changement de langue)
        ErrorDialog(win,
                    "messages.errors.pdf_import_failed.title",
                    lambda m=msg[1]: _("messages.errors.pdf_import_failed.message",
                                       error=pdf_error_text(m)),
                    ).show_nonmodal()


def ask_pdf_merge_quality(win, canvas, filepath: str, state, on_choice):
    """Question de qualité avant l'ajout d'un PDF au comic ouvert.

    Le PDF est d'abord ouvert dans son process, et la question n'est posée
    que s'il s'ouvre : un PDF protégé par mot de passe, abîmé ou sans page
    n'affiche que son message d'erreur, jamais une question sans objet.

    on_choice(choix) est appelée une seule fois : None si l'ajout n'a pas
    lieu (PDF illisible : erreur déjà affichée ; question refusée ou fermée ;
    document fermé ou remplacé pendant l'ouverture du PDF), sinon un objet
    avec dpi, images_only, join_cut_images, total_pages et pdf_process, le
    process qui a ouvert le PDF, à confier à import_and_merge_pdf."""
    if not PDF_AVAILABLE:
        _MsgDialog(win,
            "messages.errors.pymupdf_not_installed.title",
            "messages.errors.pymupdf_not_installed.message",
        ).show_nonmodal()
        on_choice(None)
        return

    process, in_q, out_conn = _spawn_pdf_process()
    in_q.put(('preopen', filepath))
    doc_generation = state.doc_generation
    # « Ouverture du fichier PDF... » pendant l'attente (le process met plus
    # d'une seconde à démarrer), retiré avant la question ou l'erreur
    opening = OpeningText(canvas)

    def _wait_preopen():
        # Lu sur le thread de l'interface, seul lecteur du canal jusqu'à la
        # remise du process à l'ajout.
        if state.doc_generation != doc_generation:
            # Document fermé ou remplacé pendant l'ouverture du PDF
            opening.hide()
            _kill_pdf_process(process, in_q, out_conn)
            on_choice(None)
            return
        msg = read_pdf_message(out_conn, process, 0)
        if msg is None:
            opening.show()
            QTimer.singleShot(50, _wait_preopen)
            return
        if msg is PDF_STOPPED_MESSAGE:
            msg = ('error', PDF_PROCESS_STOPPED)
        if msg[0] == 'preopen_ok':
            if msg[1] > 0:
                opening.hide()
                _ask(msg[1])
                return
            msg = ('empty_pdf',)
        elif msg[0] not in ('error', 'password_error'):
            QTimer.singleShot(50, _wait_preopen)
            return
        opening.hide()
        _kill_pdf_process(process, in_q, out_conn)
        _show_pdf_merge_failure(win, msg)
        on_choice(None)

    def _ask(total_pages):
        dlg = DpiDialog(win, "dialogs.pdf.merge_quality_title", "dialogs.pdf.quality_merge",
                        offer_original_images=True, file_name=os.path.basename(filepath))

        def _on_dpi(selected_dpi):
            if selected_dpi is None:
                _kill_pdf_process(process, in_q, out_conn)
                on_choice(None)
                return
            from types import SimpleNamespace
            # Lus pendant l'émission de result_signal, avant la destruction de la fenêtre
            on_choice(SimpleNamespace(dpi=selected_dpi, images_only=dlg.images_only,
                                      join_cut_images=dlg.join_cut_images,
                                      total_pages=total_pages,
                                      pdf_process=(process, in_q, out_conn)))

        dlg.ask_async(_on_dpi)

    _wait_preopen()


def import_and_merge_pdf(filepath: str, dpi: int, win, canvas, state, images_only: bool = False,
                         join_cut_images: bool = False, pdf_process=None, total_pages: int = 0,
                         on_done=None):
    """Importe un PDF et fusionne ses pages dans le comics actuel avec préfixe NEW-.
    images_only : mode « images brutes » (seules les images du PDF) ;
    join_cut_images : recoller les images coupées entre deux pages quand la
    découpe est certaine ; pdf_process, total_pages : process qui a déjà
    ouvert le PDF et son nombre de pages (ask_pdf_merge_quality).
    on_done() est appelée une fois l'ajout terminé, quelle qu'en soit l'issue
    (pages ajoutées, erreur, annulation, fichier fermé) : PanelWidget._merge_pdfs
    n'ajoute le PDF suivant qu'à ce moment, pour que deux conversions ne
    tournent jamais en même temps."""
    ended = [False]

    def _end():
        if ended[0]:
            return
        ended[0] = True
        if on_done is not None:
            on_done()

    if not PDF_AVAILABLE:
        _MsgDialog(win,
            "messages.errors.pymupdf_not_installed.title",
            "messages.errors.pymupdf_not_installed.message",
        ).show_nonmodal()
        if pdf_process is not None:
            _kill_pdf_process(*pdf_process)
        _end()
        return

    state.merge_counter += 1
    merge_prefix = f"NEW{state.merge_counter:02d}-"
    # Document visé, relevé avant le traitement des événements ci-dessous :
    # une fermeture du fichier traitée à ce moment change doc_generation, et
    # la fusion ne doit alors viser aucun document (voir AppState.doc_generation)
    doc_generation = state.doc_generation

    overlay_holder      = [None]
    cancel_item_holder  = [None]
    worker_ref          = [None]
    # Annulée (lien Annuler, fermeture du fichier) : _cancel débranche les
    # slots, mais un résultat déjà mis en file d'attente avant le débranchement
    # est quand même livré ; les slots de fin le vérifient.
    cancelled_flag      = [False]
    # Jeton de cette fusion pour MosaicCanvas.hold_empty_hint : pas de texte
    # « BD vide » sous le texte de progression d'une fusion dans une BD vide.
    empty_hint_token    = object()

    def _render(percent, current_page, total, analyzing=False):
        """Même texte que l'ouverture d'un PDF (PdfLoader._show_overlay).
        analyzing : relevé préalable des pages du mode « images brutes »."""
        if analyzing:
            line1 = _("labels.pdf_analyzing", total=total)
        elif dpi == 0:
            line1 = _("labels.pdf_converting_no_change", total=total)
        else:
            line1 = _("labels.pdf_converting", total=total, dpi=dpi)
        line3 = _("labels.pdf_page", current=current_page, total=total)
        canvas.hold_empty_hint(empty_hint_token)
        _show_canvas_text(canvas, f"{line1}\n{percent}%\n{line3}", overlay_holder)
        from modules.qt.web_import_qt import _show_cancel_item
        _show_cancel_item(canvas, f"[ {_('buttons.cancel')} ]", cancel_item_holder, _cancel,
                          anchor_lbl=overlay_holder[0])

    def _show(percent, current_page, total, analyzing=False):
        if worker_ref[0] is None:
            return
        _render(percent, current_page, total, analyzing)

    def _hide():
        _hide_canvas_text(canvas, overlay_holder)
        _hide_canvas_text(canvas, cancel_item_holder)
        canvas.release_empty_hint(empty_hint_token)

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
            w.no_images.disconnect(on_no_images)
            w.scanning.disconnect(on_scanning)
        except RuntimeError:
            pass
        worker_ref[0] = None
        _hide()
        canvas.render_mosaic()

    # Affiche le label immédiatement, avant de créer le worker
    _render(0, 0, total_pages)
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    if state.doc_generation != doc_generation:
        # Fichier fermé ou remplacé pendant ce traitement des événements, avant
        # l'inscription de l'annulation à la fermeture : la fusion n'est pas lancée
        _hide()
        if pdf_process is not None:
            _kill_pdf_process(*pdf_process)
        _end()
        return

    def on_scanning(percent, current_page, total):
        _show(percent, current_page, total, analyzing=True)

    def on_progress(percent, current_page, total):
        _show(percent, current_page, total)

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
                pass  # repli : PDF ajouté sans proposer de copie déverrouillée

    def _append_merged(new_entries):
        from modules.qt.archive_loader import _natural_sort_key as _nsk
        save_state = getattr(canvas, "_save_state_callback", None) or (lambda: None)
        save_state()
        # À la fin, rangées entre elles par nom : l'ordre du document, qui peut
        # différer de celui des noms, ne change pas.
        state.images_data.extend(sorted(new_entries, key=lambda e: _nsk(e["orig_name"])))
        state.modified = True
        state.selected_indices.clear()
        from modules.qt.comic_info import sync_pages_in_xml_data
        sync_pages_in_xml_data(state)
        # Point d'annulation APRÈS la fusion : sans lui, Annuler la défairait
        # avec l'action précédente, et Refaire ne la rétablirait jamais.
        save_state()
        canvas.render_mosaic()

    def _failed(msg):
        if cancelled_flag[0]:
            return
        _hide()
        _show_pdf_merge_failure(win, msg)

    def on_error(msg):
        _failed(('error', msg))

    def on_password_error():
        _failed(('password_error',))

    def on_empty_pdf():
        _failed(('empty_pdf',))

    def on_no_images():
        _failed(('no_images',))

    from modules.qt.utils import (dispose_qthread, register_cancel_on_close,
                                  unregister_cancel_on_close)
    worker = PdfMergeWorker(filepath, dpi, merge_prefix, images_only=images_only,
                            join_cut_images=join_cut_images, pdf_process=pdf_process)
    worker_ref[0] = worker
    # Fermeture du fichier pendant la fusion : même effet qu'Annuler
    register_cancel_on_close(canvas, _cancel)
    # Pages lues comptées dans la mémoire du panneau avant d'arriver dans le
    # document ; retirées par _cleanup, appelée par chaque signal de fin
    from modules.qt.memory_usage import reading_finished, reading_started
    reading_started(state, worker.pages_read)

    def _cleanup():
        worker_ref[0] = None
        unregister_cancel_on_close(canvas, _cancel)
        reading_finished(state, worker.pages_read)
        dispose_qthread(worker)
        # Process PDF de l'ajout terminé : retiré tout de suite de
        # l'indicateur de mémoire
        from modules.qt.memory_usage import helper_programs_finished
        helper_programs_finished()

    worker.progress.connect(on_progress)
    worker.finished.connect(lambda *_: _cleanup())
    worker.cancelled.connect(lambda: _cleanup())
    worker.error.connect(lambda *_: _cleanup())
    worker.password_error.connect(lambda: _cleanup())
    worker.empty_pdf.connect(lambda: _cleanup())
    worker.no_images.connect(lambda: _cleanup())
    worker.finished.connect(on_finished)
    worker.cancelled.connect(on_cancelled)
    worker.error.connect(on_error)
    worker.password_error.connect(on_password_error)
    worker.empty_pdf.connect(on_empty_pdf)
    worker.no_images.connect(on_no_images)
    worker.scanning.connect(on_scanning)
    # Branchée en dernier, jamais débranchée (même après Annuler) : le PDF
    # suivant n'est ajouté qu'après le traitement complet de celui-ci
    for signal in (worker.finished, worker.cancelled, worker.error, worker.password_error,
                   worker.empty_pdf, worker.no_images):
        signal.connect(lambda *_args: _end())

    worker.start()
