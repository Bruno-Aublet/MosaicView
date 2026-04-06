"""
modules/qt/web_import_qt.py — Import d'images depuis le web (version PySide6).
Reproduit à l'identique Modules_OLD/web_import_dialog.py et web_import_helpers.py.
Règles UI Qt : thème, langue à la volée, police courante.
"""

import io
import os
import time
from urllib.parse import urljoin, urlparse

from PIL import Image

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
)

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.dialogs_qt import ErrorDialog, InfoDialog
from modules.qt.canvas_overlay_qt import show_canvas_text as _show_canvas_text, hide_canvas_text as _hide_canvas_text
from modules.qt.archive_loader import _natural_sort_key
from modules.qt.entries import create_entry

import modules.qt.state as _state_module


IMAGE_EXTS = (
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
    '.tiff', '.tif', '.ico', '.jfif', '.pjpeg', '.pjp',
)

_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/91.0.4472.124 Safari/537.36'
)


# ═══════════════════════════════════════════════════════════════════════════════
# Utilitaires HTML
# ═══════════════════════════════════════════════════════════════════════════════

def extract_images_from_html(url: str, html_content: str) -> list[str]:
    """Extrait toutes les URLs d'images d'une page HTML."""
    try:
        try:
            from lxml import html as lxml_html
            tree = lxml_html.fromstring(html_content)
            image_urls = tree.xpath('//img/@src')
        except ImportError:
            from html.parser import HTMLParser

            class _ImageExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.image_urls = []

                def handle_starttag(self, tag, attrs):
                    if tag == 'img':
                        for attr, value in attrs:
                            if attr == 'src':
                                self.image_urls.append(value)

            parser = _ImageExtractor()
            parser.feed(html_content)
            image_urls = parser.image_urls

        absolute_urls = []
        for img_url in image_urls:
            absolute_url = urljoin(url, img_url)
            if absolute_url.startswith(('http://', 'https://')):
                absolute_urls.append(absolute_url)

        return absolute_urls
    except Exception as e:
        print(f"Erreur lors de l'extraction des images HTML : {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# Item cliquable pour le bouton annuler (sous-classe QGraphicsTextItem)
# ═══════════════════════════════════════════════════════════════════════════════

def _show_cancel_item(canvas, text: str, item_holder: list, on_click, anchor_lbl=None) -> None:
    """Crée ou met à jour le label Annuler cliquable sur le viewport, placé sous anchor_lbl + 8px."""
    lbl = item_holder[0] if item_holder else None
    if lbl is None or not isinstance(lbl, QLabel):
        lbl = QLabel(canvas)
        lbl.setStyleSheet(
            "color: rgb(255, 102, 102); background: transparent;"
            "text-decoration: underline;"
        )
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setCursor(Qt.PointingHandCursor)
        lbl.mousePressEvent = lambda e: on_click() if e.button() == Qt.LeftButton else None
        lbl.raise_()
        if item_holder:
            item_holder[0] = lbl
        else:
            item_holder.append(lbl)

    lbl.setFont(_get_current_font(16, bold=True))
    lbl.setText(text)

    vr = canvas.rect()
    lbl.setFixedWidth(vr.width())
    lbl.adjustSize()

    if anchor_lbl is not None and isinstance(anchor_lbl, QLabel):
        y = anchor_lbl.y() + anchor_lbl.height()
    else:
        y = (vr.height() - lbl.height()) // 2 + 40
    lbl.move(0, y)
    lbl.show()


# ═══════════════════════════════════════════════════════════════════════════════
# Worker thread de téléchargement
# ═══════════════════════════════════════════════════════════════════════════════

class _DownloadWorker(QThread):
    """Télécharge les images dans un thread séparé."""

    progress  = Signal(int, int)   # (downloaded, total)
    finished  = Signal(list)       # new_entries
    no_images = Signal()

    def __init__(self, image_urls: list[str], page_title: str, cancel_flag: list):
        super().__init__()
        self._image_urls  = image_urls
        self._page_title  = page_title
        self._cancel_flag = cancel_flag  # [False] — modifiable depuis le thread principal

    def run(self):
        import urllib.request

        state           = _state_module.state
        has_comics_open = state.current_file is not None
        new_entries     = []
        downloaded      = 0
        headers         = {'User-Agent': _USER_AGENT}

        for idx, img_url in enumerate(self._image_urls):
            if self._cancel_flag[0]:
                break

            self.progress.emit(downloaded, len(self._image_urls))

            try:
                req = urllib.request.Request(img_url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as response:
                    image_data = response.read()

                try:
                    img = Image.open(io.BytesIO(image_data))
                    real_fmt = img.format
                    img.verify()

                    url_path     = img_url.split('?')[0]
                    url_filename = os.path.basename(url_path)

                    if not url_filename or '.' not in url_filename:
                        url_filename = f"{self._page_title}_{idx + 1:03d}.jpg"

                    # Corriger l'extension si le format réel diffère (ex. WebP servi en .jpg par Chrome)
                    if real_fmt:
                        ext_map = {'JPEG': 'jpg'}
                        real_ext = ext_map.get(real_fmt, real_fmt.lower())
                        declared_ext = os.path.splitext(url_filename)[1].lstrip('.').lower()
                        if declared_ext != real_ext:
                            base = os.path.splitext(url_filename)[0]
                            url_filename = f"{base}.{real_ext}"

                    needs_prefix   = has_comics_open or state.images_data or new_entries
                    final_filename = ("NEW-" + url_filename) if needs_prefix else url_filename

                    entry = create_entry(final_filename, image_data, IMAGE_EXTS)
                    entry["source_archive"] = "web"
                    new_entries.append(entry)
                    downloaded += 1

                except Exception:
                    pass  # pas une image valide

            except Exception:
                pass

        self.finished.emit(new_entries)
        if downloaded == 0:
            self.no_images.emit()


# ═══════════════════════════════════════════════════════════════════════════════
# Contrôleur de téléchargement (overlay progression + bouton annuler cliquable)
# ═══════════════════════════════════════════════════════════════════════════════

class WebDownloadController:
    """
    Lance le téléchargement des images web et gère l'overlay rouge sur le canvas.
    Texte de progression via canvas_overlay_qt.show_canvas_text.
    Bouton annuler via _CancelTextItem (sous-classe cliquable).
    """

    def __init__(self, canvas, image_urls: list[str], page_title: str, callbacks: dict):
        self._canvas      = canvas
        self._image_urls  = image_urls
        self._page_title  = page_title
        self._callbacks   = callbacks
        self._cancel_flag = [False]
        self._item_holder        = [None]  # texte de progression (canvas_overlay_qt)
        self._cancel_item_holder = [None]  # bouton annuler (_CancelTextItem)

        self._update_overlay(0, len(image_urls))

        self._worker = _DownloadWorker(image_urls, page_title, self._cancel_flag)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.no_images.connect(self._on_no_images)
        self._worker.start()

    # ── Overlay ───────────────────────────────────────────────────────────────

    def _update_overlay(self, downloaded: int, total: int):
        text = _("web.web_download_progress", downloaded=downloaded, total=total)
        _show_canvas_text(self._canvas, text, self._item_holder)

        cancel_text = f"[ {_('web.web_download_cancel')} ]"
        _show_cancel_item(self._canvas, cancel_text, self._cancel_item_holder,
                          self._on_cancel, anchor_lbl=self._item_holder[0])

    def _hide_overlay(self):
        _hide_canvas_text(self._canvas, self._item_holder)
        _hide_canvas_text(self._canvas, self._cancel_item_holder)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_progress(self, downloaded: int, total: int):
        self._update_overlay(downloaded, total)

    def _on_cancel(self):
        self._cancel_flag[0] = True
        _show_canvas_text(
            self._canvas,
            _("web.web_download_cancel") + "...",
            self._item_holder,
        )

    def _on_finished(self, new_entries: list):
        self._hide_overlay()
        if new_entries:
            _add_entries_to_mosaic(new_entries, self._callbacks)

    def _on_no_images(self):
        if not self._cancel_flag[0]:
            ErrorDialog(
                self._canvas.window(),
                _("web.web_no_images"),
                _("web.web_no_images_found"),
            ).exec()


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers partagés
# ═══════════════════════════════════════════════════════════════════════════════

def _add_entries_to_mosaic(entries: list, callbacks: dict) -> None:
    state = callbacks.get('state') or _state_module.state

    save_state            = callbacks['save_state']
    render_mosaic         = callbacks['render_mosaic']
    update_button_text    = callbacks.get('update_button_text', lambda: None)
    update_create_cbz_btn = callbacks.get('update_create_cbz_button', lambda: None)
    clear_selection       = callbacks.get('clear_selection', lambda: None)

    if not state.images_data:
        save_state()

    state.images_data.extend(entries)
    state.images_data.sort(key=lambda e: _natural_sort_key(e["orig_name"]))
    state.modified = True

    if any(e.get("is_image", False) for e in state.images_data):
        state.needs_renumbering = True

    clear_selection()
    render_mosaic()
    update_button_text()
    update_create_cbz_btn()


def _extract_single_img_src(html_fragment: str, base_url: str) -> str | None:
    """Extrait le src du premier <img> d'un fragment HTML (drop navigateur).

    Retourne l'URL absolue si c'est une image, None sinon.
    """
    try:
        try:
            from lxml import html as lxml_html
            tree = lxml_html.fromstring(html_fragment)
            srcs = tree.xpath('//img/@src')
        except ImportError:
            from html.parser import HTMLParser

            class _FirstImg(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.src = None

                def handle_starttag(self, tag, attrs):
                    if tag == 'img' and self.src is None:
                        for attr, value in attrs:
                            if attr == 'src':
                                self.src = value

            p = _FirstImg()
            p.feed(html_fragment)
            srcs = [p.src] if p.src else []

        if not srcs:
            return None

        abs_url = urljoin(base_url, srcs[0])
        if abs_url.startswith(('http://', 'https://')):
            return abs_url
    except Exception:
        pass
    return None


_IMAGE_URL_EXTS = (
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
    '.tiff', '.tif', '.ico', '.jfif', '.pjpeg', '.pjp', '.svg',
)


def _url_looks_like_image(url: str) -> bool:
    """Retourne True si l'URL pointe directement vers un fichier image (selon son extension)."""
    path = url.split('?')[0].split('#')[0]
    return path.lower().endswith(_IMAGE_URL_EXTS)


class _ResolveWorker(QThread):
    """Résout une URL (HEAD ou GET) dans un thread, puis lance le téléchargement."""

    resolved = Signal(list, str)   # (image_urls, page_title)

    def __init__(self, url: str):
        super().__init__()
        self._url = url

    def run(self):
        import urllib.request

        url        = self._url
        parsed_url = urlparse(url)
        page_title = parsed_url.netloc.replace('www.', '')

        try:
            headers = {'User-Agent': _USER_AGENT}
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                content      = response.read()
                content_type = response.headers.get('Content-Type', '').lower()

            if 'image' in content_type:
                self.resolved.emit([url], page_title)
            else:
                try:
                    html_content = content.decode('utf-8', errors='ignore')
                except Exception:
                    html_content = content.decode('latin-1', errors='ignore')
                image_urls = extract_images_from_html(url, html_content)
                self.resolved.emit(image_urls, page_title)

        except Exception as e:
            print(f"[web_import] _ResolveWorker error: {e}")
            self.resolved.emit([], page_title)


def _resolve_and_download(canvas, url: str, callbacks: dict) -> None:
    """Résout une URL droppée sans bloquer l'UI, puis lance le téléchargement."""
    parsed_url = urlparse(url)
    page_title = parsed_url.netloc.replace('www.', '')

    if _url_looks_like_image(url):
        # Extension image connue → téléchargement direct, pas besoin de résolution
        download_and_add_web_images(canvas, [url], page_title, callbacks)
        return

    # Résolution asynchrone (GET dans un thread)
    worker = _ResolveWorker(url)

    def _on_resolved(image_urls, pt):
        if image_urls:
            download_and_add_web_images(canvas, image_urls, pt, callbacks)

    worker.resolved.connect(_on_resolved)
    # Garde une référence pour éviter le GC avant la fin du thread
    canvas._resolve_workers = getattr(canvas, '_resolve_workers', [])
    canvas._resolve_workers.append(worker)
    worker.finished.connect(lambda: canvas._resolve_workers.remove(worker)
                            if worker in canvas._resolve_workers else None)
    worker.start()


def download_and_add_web_images(canvas, image_urls: list[str], page_title: str,
                                callbacks: dict) -> None:
    """Lance le téléchargement des images et les ajoute à la mosaïque."""
    if not image_urls:
        return
    WebDownloadController(canvas, image_urls, page_title, callbacks)


def process_web_image(image_data: bytes, suggested_filename: str | None,
                      callbacks: dict, parent_widget=None) -> None:
    """Traite une image droppée depuis le web (données binaires) et l'ajoute à la mosaïque."""
    state = _state_module.state

    try:
        img = Image.open(io.BytesIO(image_data))

        if suggested_filename:
            base_name  = os.path.splitext(suggested_filename)[0]
            img_format = img.format.lower() if img.format else 'png'
            filename   = f"{base_name}.{img_format}"
        else:
            timestamp  = time.strftime("%Y%m%d_%H%M%S")
            img_format = img.format.lower() if img.format else 'png'
            filename   = f"web_image_{timestamp}.{img_format}"

        has_comics_open = state.current_file is not None
        entry = {
            "orig_name": ("NEW-" + filename) if (has_comics_open or state.images_data) else filename,
            "data":      image_data,
            "is_image":  True,
        }

        _add_entries_to_mosaic([entry], callbacks)

    except Exception as e:
        ErrorDialog(
            parent_widget,
            _("errors.title"),
            f"{_('web.import_web_invalid_url')}\n\n{e}",
        ).exec()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialogue de saisie d'URL
# ═══════════════════════════════════════════════════════════════════════════════

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


class WebImportDialog(QDialog):
    """
    Fenêtre de dialogue pour saisir une URL et importer les images d'une page web.
    Identique à Modules_OLD/web_import_dialog.py (tkinter).
    Supporte : thème courant, changement de langue à la volée, police courante.
    """

    def __init__(self, parent, canvas, callbacks: dict):
        super().__init__(parent)
        self._canvas    = canvas
        self._callbacks = callbacks

        self.setModal(True)
        self.setFixedSize(500, 180)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(6)

        self._lbl_title = QLabel()
        self._lbl_title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_title)

        self._lbl_url = QLabel()
        self._lbl_url.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_url)

        self._entry_url = QLineEdit()
        self._entry_url.setMinimumWidth(400)
        self._entry_url.returnPressed.connect(self._process_url)
        layout.addWidget(self._entry_url, alignment=Qt.AlignCenter)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_ok = QPushButton()
        self._btn_ok.setFixedWidth(110)
        self._btn_ok.setDefault(True)
        self._btn_ok.clicked.connect(self._process_url)
        self._btn_cancel = QPushButton()
        self._btn_cancel.setFixedWidth(110)
        self._btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_ok)
        btn_row.addSpacing(16)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())

        self._entry_url.setFocus()

    # ── Traduction / thème / police ───────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        bg    = theme["bg"]
        fg    = theme["text"]
        tb_bg = theme["toolbar_bg"]
        sep   = theme["separator"]
        e_bg  = theme.get("entry_bg", bg)

        self.setStyleSheet(
            f"QDialog   {{ background: {bg}; color: {fg}; }}"
            f"QLabel    {{ background: {bg}; color: {fg}; }}"
            f"QLineEdit {{ background: {e_bg}; color: {fg}; "
            f"border: 1px solid {sep}; padding: 2px 4px; }}"
        )
        btn_style = (
            f"QPushButton {{ background: {tb_bg}; color: {fg}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {sep}; }}"
        )

        font10 = _get_current_font(10)
        font12 = _get_current_font(12, bold=True)

        self.setWindowTitle(_wt("web.import_web_dialog_title"))

        self._lbl_title.setText(_("web.import_web_dialog_title"))
        self._lbl_title.setFont(font12)

        self._lbl_url.setText(_("web.import_web_url_label"))
        self._lbl_url.setFont(font10)

        self._entry_url.setFont(_get_current_font(10))

        self._btn_ok.setText(_("web.import_web_ok_button"))
        self._btn_ok.setFont(font10)
        self._btn_ok.setStyleSheet(btn_style)

        self._btn_cancel.setText(_("web.import_web_cancel_button"))
        self._btn_cancel.setFont(font10)
        self._btn_cancel.setStyleSheet(btn_style)

    # ── Traitement de l'URL ───────────────────────────────────────────────────

    def _process_url(self):
        import urllib.request

        url = self._entry_url.text().strip()
        if not url:
            return

        if not url.startswith(('http://', 'https://', 'ftp://')):
            if '.' in url and ' ' not in url:
                url = 'https://' + url

        if not url.startswith(('http://', 'https://')):
            ErrorDialog(
                self,
                _("web.import_web_dialog_title"),
                _("web.import_web_invalid_url"),
            ).exec()
            return

        self.accept()  # ferme la fenêtre avant de lancer le téléchargement

        try:
            headers = {'User-Agent': _USER_AGENT}
            req = urllib.request.Request(url, headers=headers)

            with urllib.request.urlopen(req, timeout=10) as response:
                content      = response.read()
                content_type = response.headers.get('Content-Type', '').lower()

            parsed_url = urlparse(url)
            page_title = parsed_url.netloc.replace('www.', '')

            if 'image' in content_type:
                try:
                    img = Image.open(io.BytesIO(content))
                    img.verify()
                    download_and_add_web_images(self._canvas, [url], page_title,
                                               self._callbacks)
                except Exception:
                    InfoDialog(
                        self.parent(),
                        _("web.web_drag_drop_title"),
                        _("web.web_copy_paste_message"),
                    ).exec()
            else:
                try:
                    html_content = content.decode('utf-8', errors='ignore')
                except Exception:
                    html_content = content.decode('latin-1', errors='ignore')

                image_urls = extract_images_from_html(url, html_content)

                if image_urls:
                    download_and_add_web_images(self._canvas, image_urls, page_title,
                                               self._callbacks)
                else:
                    InfoDialog(
                        self.parent(),
                        _("web.web_drag_drop_title"),
                        _("web.web_copy_paste_message"),
                    ).exec()

        except Exception as e:
            ErrorDialog(
                self.parent(),
                _("web.import_web_dialog_title"),
                f"{_('web.import_web_invalid_url')}\n\n{e}",
            ).exec()


def show_web_import_dialog(parent, canvas, callbacks: dict) -> None:
    """Ouvre la fenêtre d'import web (point d'entrée public)."""
    dlg = WebImportDialog(parent, canvas, callbacks)
    dlg.exec()
