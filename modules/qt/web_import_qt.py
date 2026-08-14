"""
modules/qt/web_import_qt.py — Import d'images depuis le web (version PySide6).
Reproduit à l'identique Modules_OLD/web_import_dialog.py et web_import_helpers.py.
Règles UI Qt : thème, langue à la volée, police courante.
"""

import io
import os
from urllib.parse import quote, urljoin, urlparse

from PIL import Image

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
)

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.dialogs_qt import ErrorDialog
from modules.qt.canvas_overlay_qt import show_canvas_text as _show_canvas_text, hide_canvas_text as _hide_canvas_text
from modules.qt.archive_loader import _natural_sort_key
from modules.qt.entries import create_entry

import modules.qt.state as _state_module


IMAGE_EXTS = (
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
    '.tiff', '.tif', '.ico', '.jfif', '.pjpeg', '.pjp', '.avif',
)

_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)

# En-têtes imitant un vrai navigateur — un User-Agent seul suffit à faire
# reconnaître la requête comme un script par certains serveurs (ex. Cloudflare)
# et à déclencher un 403 Forbidden, alors que la même URL s'ouvre normalement
# dans un navigateur.
_BROWSER_HEADERS = {
    'User-Agent':      _USER_AGENT,
    'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7',
}

# Variante utilisée pour les requêtes dont on sait déjà qu'elles ciblent une
# image (pas une page HTML) : n'annonce pas le support AVIF/WebP dans Accept,
# pour que les CDN à négociation de format (ex. Optimole) renvoient le format
# d'origine (ex. JPEG) plutôt qu'une reconversion.
_IMAGE_HEADERS = {
    'User-Agent':      _USER_AGENT,
    'Accept':          'image/png,image/jpeg,image/gif,image/bmp,image/tiff,*/*;q=0.8',
    'Accept-Language': 'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7',
}


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
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# Extraction des images générées par JS via boucle for + concaténation de
# chaînes (ex. site officiel de MosaicView : `BASE + pad(i) + '.jpg'` pour
# i = 1..GRID_SIZE). Analyse purement textuelle du <script> reçu, sans exécuter
# le JS ni ouvrir de navigateur headless — les chemins générés par ce genre de
# pattern sont des littéraux déjà présents dans le code source de la page.
# ═══════════════════════════════════════════════════════════════════════════════

import re as _re

_JS_CONST_NUM_RE = _re.compile(r'\b(?:const|let|var)\s+(\w+)\s*=\s*(\d+)\s*;')
_JS_CONST_STR_RE = _re.compile(r'''\b(?:const|let|var)\s+(\w+)\s*=\s*(?:'([^']*)'|"([^"]*)")\s*;''')
_JS_PAD_FN_RE = _re.compile(
    r'function\s+(\w+)\s*\(\s*\w+\s*\)\s*\{\s*return\s+String\([^)]*\)\.padStart\(\s*(\d+)\s*,\s*[\'"]([^\'"]*)[\'"]\s*\)'
)
_JS_FOR_LOOP_RE = _re.compile(
    r'for\s*\(\s*(?:let|var)\s+(\w+)\s*=\s*(\w+)\s*;\s*\w+\s*(<=?)\s*(\w+)\s*;\s*\w+\+\+\s*\)\s*\{'
)
# Boucle simulée par récursion + setTimeout (ex. remplissage échelonné d'une
# animation) : `let VAR = START; ... function NAME() { if (VAR >= END) {...
# return;} ...corps...; VAR++; ...setTimeout(NAME, ...); }`
_JS_RECURSIVE_LOOP_RE = _re.compile(
    r'(?:let|var)\s+(\w+)\s*=\s*(\w+)\s*;\s*'
    r'function\s+\w+\s*\(\s*\)\s*\{\s*'
    r'if\s*\(\s*\1\s*>=\s*(\w+)\s*\)\s*\{[^}]*\}\s*'
)
# Segment de concaténation : suite de (littéral '...'/"...", identifiant nu, ou
# appel de fonction à un argument) reliés par des '+', typiquement affecté à
# .src — ex. BASE + pad(i) + '.jpg'  ou  BASE + 'Fantastic 09 ' + pad(i) + '.jpg'
_JS_CONCAT_TERM_RE = _re.compile(
    r'''(?:'([^']*)'|"([^"]*)"|(\w+)\(\s*([\w+\-\s]*?)\s*\)|(\w+))\s*(?:\+|$)'''
)


def _resolve_js_number(token: str, consts: dict, loop_var: str | None, loop_val: int | None):
    """Résout un token numérique JS simple : littéral, constante connue, variable
    de boucle courante (éventuellement 'i + 1' / 'i - 1'), sinon None."""
    token = token.strip()
    if token.lstrip('-').isdigit():
        return int(token)
    # La variable de boucle courante prime sur une éventuelle "constante" globale
    # de même nom (ex. `for (let i = 0; ...)` matche aussi le pattern générique
    # de déclaration `let NAME = NUM;`, ce qui pollue `consts['i']`).
    if loop_var is not None and loop_val is not None:
        if token == loop_var:
            return loop_val
        m = _re.fullmatch(rf'{_re.escape(loop_var)}\s*([+\-])\s*(\d+)', token)
        if m:
            sign, amount = m.group(1), int(m.group(2))
            return loop_val + int(amount) if sign == '+' else loop_val - int(amount)
    if token in consts:
        return consts[token]
    return None


def _find_matching_brace(text: str, open_brace_pos: int) -> int:
    """Retourne l'index juste après l'accolade fermante correspondant à
    l'accolade ouvrante située juste avant open_brace_pos (comptage simple,
    suffisant pour du JS non minifié)."""
    depth = 1
    pos = open_brace_pos
    while pos < len(text) and depth > 0:
        ch = text[pos]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
        pos += 1
    return pos


def extract_images_from_js_loops(url: str, html_content: str) -> list[str]:
    """Reconstruit les URLs d'images générées par un pattern JS courant :
    une boucle (vrai `for`, ou boucle simulée par récursion + setTimeout) qui
    affecte `elt.src = A + B + ... + pad(i) + '.ext'` (ou variantes sans pad),
    en résolvant chaque itération littéralement, sans jamais exécuter le script."""
    try:
        consts = {}
        for m in _JS_CONST_NUM_RE.finditer(html_content):
            consts[m.group(1)] = int(m.group(2))

        str_consts = {}
        for m in _JS_CONST_STR_RE.finditer(html_content):
            str_consts[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)

        pad_fns = {}
        for m in _JS_PAD_FN_RE.finditer(html_content):
            fn_name, width, fill = m.group(1), int(m.group(2)), m.group(3)
            pad_fns[fn_name] = (width, fill)

        found_urls = []
        seen = set()

        def process_body(loop_var: str, start: int, end: int, body: str):
            # Toute expression affectée à .src à l'intérieur du corps
            for src_m in _re.finditer(r'\.src\s*=\s*([^;]+);', body):
                expr = src_m.group(1).strip()

                # L'expression doit être intégralement couverte par la regex de
                # termes de concaténation (pas de segment ignoré/tronqué, ex.
                # `pad(order[i])` — accès de tableau non résoluble statiquement)
                # sinon on ne devine pas un résultat partiel : on ignore toute
                # l'expression plutôt que de produire une URL fausse/tronquée.
                covered = ''.join(m.group(0) for m in _JS_CONCAT_TERM_RE.finditer(expr))
                if _re.sub(r'\s+', '', covered) != _re.sub(r'\s+', '', expr):
                    continue

                for i in range(start, end + 1):
                    parts = []
                    ok = True
                    for term_m in _JS_CONCAT_TERM_RE.finditer(expr):
                        lit_s, lit_d, fn_name, fn_arg, ident = term_m.groups()
                        if lit_s is not None:
                            parts.append(lit_s)
                        elif lit_d is not None:
                            parts.append(lit_d)
                        elif fn_name is not None:
                            if fn_name not in pad_fns:
                                ok = False
                                break
                            n = _resolve_js_number(fn_arg, consts, loop_var, i)
                            if n is None:
                                ok = False
                                break
                            width, fill = pad_fns[fn_name]
                            parts.append(str(n).rjust(width, fill or '0'))
                        elif ident is not None:
                            if ident == loop_var:
                                parts.append(str(i))
                            elif ident in str_consts:
                                parts.append(str_consts[ident])
                            elif ident in consts:
                                parts.append(str(consts[ident]))
                            else:
                                ok = False
                                break
                    if not ok or not parts:
                        continue
                    path = ''.join(parts)
                    abs_url = urljoin(url, path)
                    if abs_url.startswith(('http://', 'https://')) and abs_url not in seen:
                        seen.add(abs_url)
                        found_urls.append(abs_url)

        # Vrais `for (let i = A; i <[=] B; i++) { ... }`
        for loop_m in _JS_FOR_LOOP_RE.finditer(html_content):
            loop_var, start_tok, cmp_op, end_tok = loop_m.groups()
            start = _resolve_js_number(start_tok, consts, None, None)
            end = _resolve_js_number(end_tok, consts, None, None)
            if start is None or end is None:
                continue
            if cmp_op == '<':
                end -= 1
            body_end = _find_matching_brace(html_content, loop_m.end())
            process_body(loop_var, start, end, html_content[loop_m.end():body_end])

        # Boucles simulées par récursion + setTimeout (ex. remplissage échelonné)
        for loop_m in _JS_RECURSIVE_LOOP_RE.finditer(html_content):
            loop_var, start_tok, end_tok = loop_m.groups()
            start = _resolve_js_number(start_tok, consts, None, None)
            end = _resolve_js_number(end_tok, consts, None, None)
            if start is None or end is None:
                continue
            end -= 1  # condition d'arrêt observée : `if (VAR >= END)`, donc VAR va de start à END-1
            body_end = _find_matching_brace(html_content, loop_m.end())
            process_body(loop_var, start, end, html_content[loop_m.end():body_end])

        return found_urls
    except Exception:
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

_DOWNLOAD_MAX_RETRIES   = 2    # tentatives supplémentaires après l'échec initial
_DOWNLOAD_RETRY_DELAY_S = 1.0  # délai entre deux tentatives

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
        import time
        import urllib.request

        state           = _state_module.state
        has_comics_open = state.current_file is not None
        new_entries     = []
        downloaded      = 0
        headers         = dict(_IMAGE_HEADERS)

        for idx, img_url in enumerate(self._image_urls):
            if self._cancel_flag[0]:
                break

            self.progress.emit(downloaded, len(self._image_urls))

            try:
                # Encoder le path (espaces, accents...) sans re-encoder un %XX déjà présent
                parsed_img_url = urlparse(img_url)
                request_url = parsed_img_url._replace(path=quote(parsed_img_url.path)).geturl()
                req = urllib.request.Request(request_url, headers=headers)

                # Retry sur échec réseau transitoire (ex. 503 ponctuel côté
                # serveur) — pas de retry sur une image invalide (PIL), qui ne
                # se corrigera pas en réessayant le même contenu.
                image_data = None
                last_network_err = None
                for attempt in range(_DOWNLOAD_MAX_RETRIES + 1):
                    if self._cancel_flag[0]:
                        break
                    try:
                        with urllib.request.urlopen(req, timeout=10) as response:
                            image_data = response.read()
                        break
                    except Exception as e:
                        last_network_err = e
                        if attempt < _DOWNLOAD_MAX_RETRIES:
                            time.sleep(_DOWNLOAD_RETRY_DELAY_S)

                if self._cancel_flag[0]:
                    break
                if image_data is None:
                    raise last_network_err

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

        _suppress_empty_hint(canvas)
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
            _add_entries_to_mosaic(self._canvas, new_entries, self._callbacks)
        else:
            _restore_empty_hint(self._canvas, self._callbacks)

    def _on_no_images(self):
        if not self._cancel_flag[0]:
            ErrorDialog(
                self._canvas.window(),
                lambda: _wt("web.web_no_images"),
                lambda: _("web.web_no_images_found"),
            ).show_nonmodal()


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers partagés
# ═══════════════════════════════════════════════════════════════════════════════

def _suppress_empty_hint(canvas) -> None:
    """Empêche/efface le message d'accueil ('Déposez ici...') pendant qu'un
    overlay rouge de progression est actif sur un canvas potentiellement vide —
    sinon les deux textes centrés se chevauchent visuellement. Même pattern que
    panel_widget.py pour le chargement d'archive (canvas._loading)."""
    canvas._loading = True
    empty_items = getattr(canvas, '_empty_items', None)
    if not empty_items:
        return
    from shiboken6 import isValid
    for it in empty_items:
        if isValid(it) and it.scene() is canvas.scene():
            canvas.scene().removeItem(it)
    empty_items.clear()


def _restore_empty_hint(canvas, callbacks: dict) -> None:
    """Réactive l'affichage normal du message d'accueil et le réaffiche
    immédiatement si le canvas est resté vide (échec total de l'import)."""
    canvas._loading = False
    render_mosaic = callbacks.get('render_mosaic')
    if render_mosaic is not None:
        render_mosaic()


def _add_entries_to_mosaic(canvas, entries: list, callbacks: dict) -> None:
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

    canvas._loading = False

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
    '.tiff', '.tif', '.ico', '.jfif', '.pjpeg', '.pjp', '.svg', '.avif',
)


def _url_looks_like_image(url: str) -> bool:
    """Retourne True si l'URL pointe directement vers un fichier image (selon son extension)."""
    path = url.split('?')[0].split('#')[0]
    return path.lower().endswith(_IMAGE_URL_EXTS)


class _ResolveWorker(QThread):
    """Résout une URL (GET) dans un thread : détecte si c'est une image directe,
    sinon extrait les URLs d'images de la page (HTML statique + boucles JS de
    génération de chemins, voir extract_images_from_js_loops)."""

    resolved_image = Signal(str, str)    # (url, page_title) — Content-Type image
    resolved_html  = Signal(list, str)   # (image_urls, page_title)
    error_occurred = Signal(str, str)    # (kind, detail)  kind: "forbidden" | "network"

    def __init__(self, url: str):
        super().__init__()
        self._url = url

    def run(self):
        import urllib.request
        import urllib.error

        url        = self._url
        parsed_url = urlparse(url)
        page_title = parsed_url.netloc.replace('www.', '')

        try:
            req = urllib.request.Request(url, headers=dict(_BROWSER_HEADERS))
            with urllib.request.urlopen(req, timeout=10) as response:
                content      = response.read()
                content_type = response.headers.get('Content-Type', '').lower()

            if 'image' in content_type:
                self.resolved_image.emit(url, page_title)
            else:
                try:
                    html_content = content.decode('utf-8', errors='ignore')
                except Exception:
                    html_content = content.decode('latin-1', errors='ignore')

                image_urls = extract_images_from_html(url, html_content)
                for u in extract_images_from_js_loops(url, html_content):
                    if u not in image_urls:
                        image_urls.append(u)

                self.resolved_html.emit(image_urls, page_title)

        except urllib.error.HTTPError as e:
            kind = "forbidden" if e.code == 403 else "network"
            self.error_occurred.emit(kind, str(e))
        except Exception as e:
            self.error_occurred.emit("network", str(e))


def _resolve_and_download(canvas, url: str, callbacks: dict, parent=None) -> None:
    """Résout une URL droppée sans bloquer l'UI, puis lance le téléchargement.

    parent : panneau (PanelWidget) sur lequel centrer les dialogues d'erreur.
    À défaut de parent explicite, on replie sur canvas.window() (fenêtre entière —
    centrage incorrect en mode split, mais fonctionnel)."""
    dialog_parent = parent if parent is not None else canvas.window()
    parsed_url = urlparse(url)
    page_title = parsed_url.netloc.replace('www.', '')

    if _url_looks_like_image(url):
        # Extension image connue → téléchargement direct, pas besoin de résolution
        download_and_add_web_images(canvas, [url], page_title, callbacks)
        return

    # Overlay affiché dès le début : la résolution (GET + extraction) laisserait
    # sinon le canvas visuellement vide et donnerait l'impression d'un freeze.
    # Le message d'accueil ('Déposez ici...') est masqué pendant ce temps pour
    # ne pas se chevaucher visuellement avec le texte rouge de progression
    # (download_and_add_web_images/WebDownloadController le masque aussi pour
    # le cas image directe, qui saute cette fonction).
    _suppress_empty_hint(canvas)
    analyzing_item_holder = [None]
    _show_canvas_text(canvas, _("web.web_analyzing_page"), analyzing_item_holder)

    def _hide_analyzing_overlay():
        _hide_canvas_text(canvas, analyzing_item_holder)

    # Résolution asynchrone (GET dans un thread)
    worker = _ResolveWorker(url)

    def _on_resolved_image(u, pt):
        _hide_analyzing_overlay()
        download_and_add_web_images(canvas, [u], pt, callbacks)

    def _on_resolved_html(image_urls, pt):
        _hide_analyzing_overlay()
        if image_urls:
            download_and_add_web_images(canvas, image_urls, pt, callbacks)
        else:
            _restore_empty_hint(canvas, callbacks)
            ErrorDialog(
                dialog_parent,
                lambda: _wt("web.web_drop_no_images_title"),
                lambda: _("web.web_drop_no_images_message"),
            ).show_nonmodal()

    def _on_error(kind, detail):
        _hide_analyzing_overlay()
        _restore_empty_hint(canvas, callbacks)
        title_key   = "web.web_drop_forbidden_title" if kind == "forbidden" else "web.web_drop_error_title"
        message_key = "web.web_drop_forbidden_message" if kind == "forbidden" else "web.web_drop_error_message"
        ErrorDialog(
            dialog_parent,
            lambda: _wt(title_key),
            lambda: _(message_key),
        ).show_nonmodal()

    worker.resolved_image.connect(_on_resolved_image)
    worker.resolved_html.connect(_on_resolved_html)
    worker.error_occurred.connect(_on_error)
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

        self.setWindowFlags(Qt.Window)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
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
        from modules.qt.utils import setup_lineedit_context_menu
        setup_lineedit_context_menu(self._entry_url)
        layout.addWidget(self._entry_url, alignment=Qt.AlignCenter)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_ok = QPushButton()
        self._btn_ok.setFixedWidth(110)
        self._btn_ok.setDefault(True)
        self._btn_ok.clicked.connect(self._process_url)
        self._btn_cancel = QPushButton()
        self._btn_cancel.setFixedWidth(110)
        self._btn_cancel.clicked.connect(self.close)
        btn_row.addWidget(self._btn_ok)
        btn_row.addSpacing(16)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._center_parent = parent

        self._entry_url.setFocus()

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

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
        url = self._entry_url.text().strip()
        if not url:
            return

        if not url.startswith(('http://', 'https://', 'ftp://')):
            if '.' in url and ' ' not in url:
                url = 'https://' + url

        if not url.startswith(('http://', 'https://')):
            ErrorDialog(
                self,
                lambda: _wt("web.import_web_dialog_title"),
                lambda: _("web.import_web_invalid_url"),
            ).show_nonmodal()
            return

        canvas    = self._canvas
        callbacks = self._callbacks
        parent    = self.parent()
        self.close()  # ferme la fenêtre avant de lancer la résolution/téléchargement

        # Résolution asynchrone (GET + extraction JS statique si HTML) — même
        # chemin que le drop de lien, pour que les pages générant leurs images
        # en JavaScript (ex. le site officiel de MosaicView) soient gérées.
        _resolve_and_download(canvas, url, callbacks, parent=parent)


def show_web_import_dialog(parent, canvas, callbacks: dict) -> None:
    """Ouvre la fenêtre d'import web (point d'entrée public)."""
    dlg = WebImportDialog(parent, canvas, callbacks)
    from modules.qt.dialogs_qt import position_dialog_on_parent
    position_dialog_on_parent(dlg, parent)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
