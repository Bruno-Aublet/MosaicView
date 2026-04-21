# comicvine_dialog_qt.py — Fenêtre de scraping ComicVine (wizard 2 étapes)
# Inspiré de comic-vine-scraper par Cory Banack (Apache 2.0)

import os
import urllib.request

from PySide6.QtWidgets import (
    QDialog, QStackedWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView,
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QPixmap, QImage

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font

_DATA_ROLE = Qt.UserRole   # stocke l'index dans _series_data / _issues_data


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bytes_to_pixmap(data: bytes) -> QPixmap:
    # Passer par PIL pour normaliser les données avant de les confier à Qt.
    # Qt peut spawner des threads internes instables sur des JPEG corrompus.
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        data = buf.getvalue()
    except Exception:
        pass
    return QPixmap.fromImage(QImage.fromData(data))


def _cover_pixmap_from_state(state) -> QPixmap | None:
    if not state or not state.images_data:
        return None
    for entry in state.images_data:
        if entry.get("is_image") and not entry.get("is_dir"):
            raw = entry.get("bytes")
            if raw:
                try:
                    return _bytes_to_pixmap(raw)
                except Exception:
                    pass
    return None


def _btn_style(theme):
    alt = theme.get("toolbar_bg", theme["bg"])
    sep = theme.get("separator", "#aaaaaa")
    fg  = theme["text"]
    return (
        f"QPushButton {{ background: {alt}; color: {fg}; "
        f"border: 1px solid {sep}; padding: 5px 14px; }} "
        f"QPushButton:hover {{ background: {sep}; }} "
        f"QPushButton:disabled {{ color: #888888; }}"
    )


def _tbl_style(theme):
    bg  = theme["bg"]
    fg  = theme["text"]
    sep = theme.get("separator", "#aaaaaa")
    alt = theme.get("toolbar_bg", bg)
    sel = theme.get("selected", "#3399ff")
    return (
        f"QTableWidget {{ background: {bg}; color: {fg}; "
        f"border: 1px solid {sep}; gridline-color: {sep}; "
        f"alternate-background-color: {alt}; }} "
        f"QTableWidget::item:selected {{ background: {sel}; color: {fg}; }} "
        f"QHeaderView::section {{ background: {alt}; color: {fg}; "
        f"border: 1px solid {sep}; padding: 3px; }}"
    )


# ── Workers ───────────────────────────────────────────────────────────────────

class _SearchWorker(QThread):
    finished = Signal(list, int)
    error    = Signal(str)

    def __init__(self, api_key, search_terms, page=1):
        super().__init__()
        self._api_key = api_key
        self._terms   = search_terms
        self._page    = page

    def run(self):
        try:
            from modules.qt.comicvine_scraper import search_series
            results, total = search_series(self._api_key, self._terms, self._page)
            self.finished.emit(results, total)
        except Exception as e:
            self.error.emit(str(e))


class _IssuesWorker(QThread):
    """Charge TOUTES les pages d'issues d'une série, une par une."""
    finished = Signal(list)   # liste complète
    progress = Signal(int, int)  # (chargés, total)
    error    = Signal(str)

    def __init__(self, api_key, series_id):
        super().__init__()
        self._api_key   = api_key
        self._series_id = series_id

    def run(self):
        from modules.qt.comicvine_scraper import get_series_issues
        all_results = []
        page = 1
        try:
            while True:
                results, total = get_series_issues(self._api_key, self._series_id, page)
                all_results.extend(results)
                self.progress.emit(len(all_results), total)
                if len(all_results) >= total or not results:
                    break
                page += 1
            self.finished.emit(all_results)
        except Exception as e:
            # Retourner les résultats partiels déjà chargés, puis signaler l'erreur
            if all_results:
                self.finished.emit(all_results)
            self.error.emit(str(e))


class _ImageWorker(QThread):
    finished = Signal(bytes)
    error    = Signal(str)

    def __init__(self, url):
        super().__init__()
        self._url = url

    def run(self):
        try:
            req = urllib.request.Request(self._url,
                                         headers={"User-Agent": "MosaicView/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                self.finished.emit(r.read())
        except Exception as e:
            self.error.emit(str(e))


class _MetadataWorker(QThread):
    finished = Signal(dict)
    error    = Signal(str)

    def __init__(self, api_key, issue_id):
        super().__init__()
        self._api_key  = api_key
        self._issue_id = issue_id

    def run(self):
        try:
            from modules.qt.comicvine_scraper import get_issue_details
            meta = get_issue_details(self._api_key, self._issue_id)
            if meta:
                self.finished.emit(meta)
            else:
                self.error.emit("No data returned")
        except Exception as e:
            self.error.emit(str(e))


# ── Point d'entrée public ─────────────────────────────────────────────────────

def show_comicvine_dialog(parent, state, api_key, batch=False, on_done=None,
                          batch_index=None, batch_total=None,
                          shared_search_cache=None, shared_issues_cache=None,
                          on_next=None, cbz_filepath=None):
    """Ouvre la fenêtre de scraping ComicVine (non-modale)."""
    dlg = _ComicVineDialog(parent, state, api_key, batch=batch, on_done=on_done,
                           batch_index=batch_index, batch_total=batch_total,
                           shared_search_cache=shared_search_cache,
                           shared_issues_cache=shared_issues_cache,
                           on_next=on_next, cbz_filepath=cbz_filepath)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


# ── Fenêtre principale ────────────────────────────────────────────────────────

class _ComicVineDialog(QDialog):

    _dying_workers = []   # référence globale pour éviter la destruction par GC

    def __init__(self, parent, state, api_key, batch=False, on_done=None,
                 batch_index=None, batch_total=None,
                 shared_search_cache=None, shared_issues_cache=None,
                 on_next=None, cbz_filepath=None):
        super().__init__(parent)
        self._state        = state
        self._api_key      = api_key
        self._batch        = batch
        self._on_done      = on_done
        self._on_next      = on_next        # callable() → passe au fichier suivant
        self._batch_index  = batch_index    # int 1-based, ou None
        self._batch_total  = batch_total    # int, ou None
        self._worker  = None
        self._image_worker = None
        self._selected_series = None
        # Caches : partagés si fournis, sinon locaux
        self._search_cache = shared_search_cache if shared_search_cache is not None else {}
        self._issues_cache = shared_issues_cache if shared_issues_cache is not None else {}
        self._issues_had_done = False

        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.resize(1100, 580)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._stack = QStackedWidget()
        root.addWidget(self._stack)

        self._page1 = _Page1Series(self, batch=batch)
        self._page2 = _Page2Issues(self, batch=batch)
        self._stack.addWidget(self._page1)
        self._stack.addWidget(self._page2)

        # Connexions inter-pages
        self._page1.search_requested.connect(lambda t, p: self._do_search(t, p))
        self._page1.series_confirmed.connect(self._go_to_issues)
        self._page1.skip_requested.connect(self._on_skip)
        self._page1.cancel_requested.connect(self.close)

        self._page2.back_requested.connect(self._go_to_series)
        self._page2.issue_confirmed.connect(self._apply_metadata)
        self._page2.skip_requested.connect(self._on_skip)
        self._page2.cancel_requested.connect(self.close)
        self._page2.issue_image_requested.connect(self._load_issue_image)

        cover_pix = _cover_pixmap_from_state(state)
        self._page1.set_cover(cover_pix)
        self._page2.set_cover(cover_pix)

        # Nom affiché = fichier SOURCE (original), pas la destination cbz
        orig_path = (getattr(state, '_original_filepath', None)
                     or (state.current_file if state else None))
        filename = os.path.basename(orig_path) if orig_path else ""
        self._page1.set_filename(filename)
        self._page2.set_filename(filename)

        raw = os.path.splitext(os.path.basename(orig_path))[0] if orig_path else ""
        initial = ""
        if state and state.comic_metadata:
            initial = state.comic_metadata.get("series", "").strip()
        if not initial:
            initial = self._clean_filename_for_search(raw)
        self._page1.set_search_term(initial)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
        self._center_parent = parent

        if initial:
            QTimer.singleShot(100, lambda: self._page1._on_search_clicked() if self.isVisible() else None)

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            def _show():
                _center_on_widget(self, p)
                self.resize(1100, 580)
            QTimer.singleShot(0, _show)

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )
        title = _wt("comicvine.menu_label")
        if self._batch_index is not None and self._batch_total is not None:
            title = f"{title}  [{self._batch_index}/{self._batch_total}]"
        self.setWindowTitle(title)
        self._page1.retranslate(batch_counter=self._batch_counter_text())
        self._page2.retranslate(batch_counter=self._batch_counter_text())

    def _batch_counter_text(self):
        if self._batch_index is not None and self._batch_total is not None:
            return f"{self._batch_index} / {self._batch_total}"
        return None

    def _on_skip(self):
        """Ignorer ce fichier : passe au suivant en batch, ferme sinon."""
        if self._on_next:
            self._on_next()
        else:
            self.close()

    # ── Actions ───────────────────────────────────────────────────────────────

    def _do_search(self, search_terms, page=1):
        cache_key = (self._cache_key_for_terms(search_terms), page)
        if cache_key in self._search_cache:
            results, total = self._search_cache[cache_key]
            self._page1.populate_series(results, total, search_terms, page)
            return

        self._page1.set_loading(True)
        self._worker = _SearchWorker(self._api_key, search_terms, page)
        self._worker.finished.connect(lambda r, t: self._on_search_done(r, t, search_terms, page))
        self._worker.error.connect(self._on_search_error)
        self._worker.start()

    def _on_search_done(self, results, total, terms, page):
        self._search_cache[(self._cache_key_for_terms(terms), page)] = (results, total)
        self._page1.set_loading(False)
        self._page1.populate_series(results, total, terms, page)

    def _on_search_error(self, msg):
        self._page1.set_loading(False)
        self._page1.show_error(msg)

    def _go_to_issues(self, series):
        self._selected_series = series
        self._issues_had_done = False
        self._page2.clear_issue_cover()
        self._stack.setCurrentIndex(1)

        series_id = series["id"]
        if series_id in self._issues_cache:
            self._page2.populate_issues(self._issues_cache[series_id], series, self._guess_issue_number())
            return

        self._page2.show_loading_overlay(True, 0, 0)
        self._worker = _IssuesWorker(self._api_key, series_id)
        self._worker.progress.connect(self._on_issues_progress)
        self._worker.finished.connect(self._on_issues_done)
        self._worker.error.connect(self._on_issues_error)
        self._worker.start()

    def _on_issues_progress(self, loaded, total):
        self._page2.show_loading_overlay(True, loaded, total)

    def _on_issues_done(self, results):
        self._page2.show_loading_overlay(False, 0, 0)
        self._page2.populate_issues(results, self._selected_series, self._guess_issue_number())
        self._issues_cache[self._selected_series["id"]] = results
        self._issues_had_done = True   # finished a été émis (peut être suivi d'un error partiel)

    def _on_issues_error(self, msg):
        self._page2.show_loading_overlay(False, 0, 0)
        if getattr(self, '_issues_had_done', False):
            # Résultats partiels déjà affichés — afficher un avertissement non bloquant
            partial_count = len(self._page2._issues_data) if hasattr(self._page2, '_issues_data') else 0
            status = _("comicvine.issues_partial_load").format(count=partial_count, error=msg)
        else:
            status = msg
        self._page2.show_error(status)
        self._issues_had_done = False

    def _go_to_series(self):
        self._stack.setCurrentIndex(0)

    @staticmethod
    def _clean_filename_for_search(fname):
        """Retire le préfixe numérique de nommage (ex. '05909 ') et les dates en tête."""
        import re
        # Supprimer les dates en début de chaîne : YYYY-MM-DD, YYYY MM DD, YYYYMMDD
        fname = re.sub(r'^\d{4}[-\s]?\d{2}[-\s]?\d{2}\s*[-_]?\s*', '', fname)
        # Supprimer le préfixe numérique de nommage (ex. "05909 " ou "05909_")
        fname = re.sub(r'^\d+[\s_\-]+', '', fname)
        return fname.strip()

    @staticmethod
    def _cache_key_for_terms(terms):
        """Normalise un terme de recherche pour la clé de cache série.
        Retire préfixe numérique, dates, numéro d'issue et suffixes parasites."""
        import re
        s = terms.strip()
        # Dates en début
        s = re.sub(r'^\d{4}[-\s]?\d{2}[-\s]?\d{2}\s*[-_]?\s*', '', s)
        # Préfixe numérique de nommage
        s = re.sub(r'^\d+[\s_\-]+', '', s)
        # Numéro d'issue isolé en fin et tout ce qui suit (ex. " 116 - Copie", " 125")
        s = re.sub(r'\s+\d+\b.*$', '', s)
        return s.strip()

    def _guess_issue_number(self):
        """Devine le numéro d'issue depuis les métadonnées ou le texte de recherche saisi."""
        import re
        st = self._state
        if st and st.comic_metadata:
            num = str(st.comic_metadata.get("number", "")).strip()
            if num:
                return num

        # Utiliser le texte saisi dans la zone de recherche si disponible
        search_text = self._page1._search_input.text().strip()
        src = getattr(st, '_original_filepath', None) or (st.current_file if st else None)
        fname = search_text or (os.path.splitext(os.path.basename(src))[0] if src else "")
        if not fname:
            return None

        # Supprimer les dates en début de chaîne : YYYY-MM-DD, YYYY MM DD, YYYYMMDD
        fname_stripped = re.sub(r'^\d{4}[-\s]?\d{2}[-\s]?\d{2}\s*[-_]?\s*', '', fname)

        # Cherche un nombre précédé d'un espace, '#', ou en début (après strip dates)
        # mais PAS collé à des lettres (exclut KODT320a, v2, etc.)
        # et PAS en première position absolue (les préfixes numériques de nommage)
        for m in re.finditer(r'(?:^|[\s#])(\d+)(?=$|[\s\.\-_\[\(])', fname_stripped):
            num = m.group(1)
            # Ignorer si c'est le premier token ET que fname_stripped commence par ce nombre
            if m.start() == 0 and fname_stripped.startswith(num):
                continue
            return num
        return None

    def _load_issue_image(self, url):
        if not url:
            return
        self._image_worker = _ImageWorker(url)
        self._image_worker.finished.connect(self._on_issue_image_done)
        self._image_worker.error.connect(lambda _: None)
        self._image_worker.start()

    def _on_issue_image_done(self, data):
        self._page2.set_issue_cover(_bytes_to_pixmap(data))

    def _apply_metadata(self, issue):
        self._page2._btn_ok.setEnabled(False)
        worker = _MetadataWorker(self._api_key, issue["id"])
        worker.finished.connect(self._on_metadata_done)
        worker.error.connect(self._on_metadata_error)
        worker.start()
        self._worker = worker

    def _on_metadata_done(self, meta):
        self._page2._btn_ok.setEnabled(True)
        self._write_metadata(meta)
        if self._on_done:
            self._on_done()
            if not self._batch:
                self.close()
        elif self._on_next:
            self._on_next()
        else:
            self.close()

    def _on_metadata_error(self, msg):
        self._page2._btn_ok.setEnabled(True)
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(self, _wt("comicvine.menu_label"), msg)

    def _write_metadata(self, meta):
        import xml.etree.ElementTree as ET
        from modules.qt.comic_info import parse_comic_info_xml, _serialize_comic_xml
        from modules.qt.metadata_signal import metadata_signal

        st = self._state
        if not st:
            return

        FIELD_MAP = {
            "title": "Title", "series": "Series", "number": "Number",
            "volume": "Volume", "summary": "Summary", "writer": "Writer",
            "penciller": "Penciller", "inker": "Inker", "colorist": "Colorist",
            "letterer": "Letterer", "cover_artist": "CoverArtist",
            "editor": "Editor", "publisher": "Publisher", "imprint": "Imprint",
            "genre": "Genre", "web": "Web", "year": "Year", "month": "Month",
            "day": "Day", "characters": "Characters", "teams": "Teams",
            "locations": "Locations", "story_arc": "StoryArc",
        }

        xml_entry = None
        for e in st.images_data:
            if e.get("orig_name", "").lower().endswith("comicinfo.xml"):
                xml_entry = e
                break

        if xml_entry and xml_entry.get("bytes"):
            try:
                raw = xml_entry["bytes"]
                if isinstance(raw, str):
                    raw = raw.encode("utf-8")
                # Tenter UTF-8 puis latin-1 en fallback
                try:
                    root = ET.fromstring(raw)
                except ET.ParseError:
                    root = ET.fromstring(raw.decode("latin-1").encode("utf-8"))
                original_bytes = raw
            except Exception:
                root = ET.Element("ComicInfo")
                original_bytes = None
        else:
            root = ET.Element("ComicInfo")
            original_bytes = None

        for field, tag in FIELD_MAP.items():
            value = meta.get(field, "").strip()
            if not value:
                continue
            elem = root.find(tag)
            if elem is None:
                elem = ET.SubElement(root, tag)
            elem.text = value

        new_bytes = _serialize_comic_xml(root, original_bytes)

        if xml_entry:
            xml_entry["bytes"] = new_bytes
        else:
            st.images_data.append({
                "orig_name": "ComicInfo.xml", "name": "ComicInfo.xml",
                "bytes": new_bytes, "is_image": False,
                "is_dir": False, "extension": ".xml",
            })

        st.comic_metadata = parse_comic_info_xml(new_bytes)
        st.modified = True

        # Si le XML n'avait pas de <Pages>, les construire depuis images_data
        if st.comic_metadata and 'pages' not in st.comic_metadata:
            # Injecter <Pages> vide dans le XML pour que sync_pages_in_xml_data puisse le remplir
            xml_entry_now = next(
                (e for e in st.images_data if e.get('orig_name', '').lower().endswith('comicinfo.xml')),
                None
            )
            if xml_entry_now and xml_entry_now.get('bytes'):
                try:
                    root_now = ET.fromstring(xml_entry_now['bytes'])
                    ET.SubElement(root_now, 'Pages')
                    from modules.qt.comic_info import _serialize_comic_xml
                    xml_entry_now['bytes'] = _serialize_comic_xml(root_now, xml_entry_now['bytes'])
                    st.comic_metadata['pages'] = []
                    from modules.qt.comic_info import sync_pages_in_xml_data
                    sync_pages_in_xml_data(st, emit_signal=False)
                except Exception:
                    pass

        metadata_signal.emit()

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass
        for w in (self._worker, self._image_worker):
            if w is None:
                continue
            for sig in ('finished', 'error', 'done'):
                try:
                    getattr(w, sig).disconnect()
                except (RuntimeError, AttributeError):
                    pass
            try:
                w.setParent(None)
            except RuntimeError:
                pass
            if w.isRunning():
                _ComicVineDialog._dying_workers.append(w)
                w.finished.connect(lambda ww=w: _ComicVineDialog._dying_workers.remove(ww)
                                   if ww in _ComicVineDialog._dying_workers else None)


# ── Page 1 : Choix de la série ────────────────────────────────────────────────

class _Page1Series(QWidget):

    search_requested = Signal(str, int)   # (terms, page)
    series_confirmed = Signal(dict)
    skip_requested   = Signal()
    cancel_requested = Signal()

    def __init__(self, parent=None, batch=False):
        super().__init__(parent)
        self._series_data  = []
        self._current_page = 1
        self._total        = 0
        self._current_terms = ""
        self._batch        = batch

        main = QHBoxLayout(self)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(12)

        # Couverture gauche + nom de fichier
        left = QVBoxLayout()
        left.setSpacing(4)
        self._cover_lbl = QLabel()
        self._cover_lbl.setFixedSize(200, 280)
        self._cover_lbl.setAlignment(Qt.AlignCenter)
        left.addWidget(self._cover_lbl)
        self._filename_lbl = QLabel()
        self._filename_lbl.setWordWrap(True)
        self._filename_lbl.setAlignment(Qt.AlignCenter)
        self._filename_lbl.setFixedWidth(200)
        left.addWidget(self._filename_lbl)
        left.addStretch()
        main.addLayout(left, 0)

        # Partie droite
        right = QVBoxLayout()
        right.setSpacing(6)

        # Texte info
        self._info_lbl = QLabel()
        self._info_lbl.setWordWrap(True)
        right.addWidget(self._info_lbl)

        # Barre de recherche
        search_row = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.returnPressed.connect(self._on_search_clicked)
        search_row.addWidget(self._search_input)
        self._search_btn = QPushButton()
        self._search_btn.setMinimumWidth(100)
        self._search_btn.clicked.connect(self._on_search_clicked)
        search_row.addWidget(self._search_btn)
        right.addLayout(search_row)

        # Status + pagination sur la même ligne
        status_row = QHBoxLayout()
        self._status_lbl = QLabel(" ")
        status_row.addWidget(self._status_lbl, 1)
        self._btn_prev = QPushButton("◀")
        self._btn_prev.setFixedWidth(32)
        self._btn_prev.clicked.connect(self._on_prev_page)
        self._page_lbl = QLabel()
        self._btn_next = QPushButton("▶")
        self._btn_next.setFixedWidth(32)
        self._btn_next.clicked.connect(self._on_next_page)
        for w in (self._btn_prev, self._page_lbl, self._btn_next):
            status_row.addWidget(w)
        right.addLayout(status_row)

        # Tableau + overlay de chargement
        from PySide6.QtWidgets import QStackedLayout
        tbl_container = QWidget()
        tbl_stack = QStackedLayout(tbl_container)
        tbl_stack.setStackingMode(QStackedLayout.StackAll)

        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)
        self._table.doubleClicked.connect(self._on_ok_clicked)
        tbl_stack.addWidget(self._table)

        self._loading_overlay = QLabel()
        self._loading_overlay.setAlignment(Qt.AlignCenter)
        self._loading_overlay.setWordWrap(True)
        self._loading_overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._loading_overlay.setStyleSheet(
            "color: rgb(220,0,0); background: transparent; font-weight: bold;")
        self._loading_overlay.hide()
        tbl_stack.addWidget(self._loading_overlay)

        right.addWidget(tbl_container)

        # Boutons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_skip   = QPushButton()
        self._btn_skip.setEnabled(batch)
        self._btn_skip.clicked.connect(self.skip_requested)
        self._btn_cancel = QPushButton()
        self._btn_cancel.clicked.connect(self.cancel_requested)
        self._btn_ok     = QPushButton()
        self._btn_ok.setDefault(True)
        self._btn_ok.clicked.connect(self._on_ok_clicked)
        for w in (self._btn_skip, self._btn_cancel, self._btn_ok):
            btn_row.addWidget(w)
        right.addLayout(btn_row)

        self._credit_lbl = QLabel()
        self._credit_lbl.setAlignment(Qt.AlignCenter)
        self._credit_lbl.setOpenExternalLinks(True)
        right.addWidget(self._credit_lbl)

        main.addLayout(right, 1)

    def retranslate(self, batch_counter=None):
        theme = get_current_theme()
        font  = _get_current_font(10)
        font8 = _get_current_font(8)
        font9 = _get_current_font(9)
        bg    = theme["bg"]
        fg    = theme["text"]
        sep   = theme.get("separator", "#aaaaaa")
        alt   = theme.get("toolbar_bg", bg)

        self.setStyleSheet(f"QWidget {{ background: {bg}; color: {fg}; }}")
        self._cover_lbl.setStyleSheet(f"background: {alt}; border: 1px solid {sep};")
        self._filename_lbl.setFont(font9)
        self._filename_lbl.setStyleSheet(f"color: {fg}; background: transparent;")

        if batch_counter:
            info = f"{_('comicvine.info_single')}  —  {batch_counter}"
        else:
            info = _("comicvine.info_single")
        self._info_lbl.setText(info)
        self._info_lbl.setFont(font9)
        self._info_lbl.setStyleSheet(f"color: {fg}; background: transparent;")

        self._search_input.setFont(font)
        self._search_input.setStyleSheet(
            f"QLineEdit {{ background: {alt}; color: {fg}; "
            f"border: 1px solid {sep}; padding: 4px 8px; }}"
        )
        self._search_input.setPlaceholderText(_("comicvine.search_placeholder"))

        bs = _btn_style(theme)
        self._search_btn.setFont(font)
        self._search_btn.setStyleSheet(bs)
        self._search_btn.setText(_("comicvine.search_button"))

        self._status_lbl.setFont(font9)
        self._status_lbl.setStyleSheet(f"color: {fg}; background: transparent;")

        nav_style = (
            f"QPushButton {{ background: {alt}; color: {fg}; "
            f"border: 1px solid {sep}; padding: 2px 4px; }} "
            f"QPushButton:hover {{ background: {sep}; }} "
            f"QPushButton:disabled {{ color: #888888; }}"
        )
        for btn in (self._btn_prev, self._btn_next):
            btn.setFont(font9)
            btn.setStyleSheet(nav_style)
        self._page_lbl.setFont(font9)
        self._page_lbl.setStyleSheet(f"color: {fg}; background: transparent;")
        self._update_pagination_ui()

        self._table.setStyleSheet(_tbl_style(theme))
        self._table.setFont(font8)
        col1_labels = [
            _("comicvine.col_series"),
            _("comicvine.col_year"),
            _("comicvine.col_issues"),
            _("comicvine.col_publisher"),
        ]
        for col, label in enumerate(col1_labels):
            item = QTableWidgetItem(label)
            item.setFont(font8)
            self._table.setHorizontalHeaderItem(col, item)
        self._table.horizontalHeader().setFont(font8)
        self._table.setColumnWidth(0, 380)
        self._table.setColumnWidth(1, 60)
        self._table.setColumnWidth(2, 60)

        # Mettre à jour le status_lbl si une recherche est déjà affichée
        if self._total > 0:
            self._status_lbl.setText(
                _("comicvine.results_count").format(count=len(self._series_data), total=self._total)
            )

        for btn, key in [
            (self._btn_skip,   "comicvine.btn_skip"),
            (self._btn_cancel, "buttons.cancel"),
            (self._btn_ok,     "buttons.ok"),
        ]:
            btn.setFont(font)
            btn.setStyleSheet(bs)
            btn.setText(_(key))

        link_color = theme.get("link", "#4A9EFF")
        self._credit_lbl.setFont(_get_current_font(8))
        self._credit_lbl.setStyleSheet(f"color: {theme['disabled']}; background: transparent;")
        cv_link     = f'<a href="https://comicvine.gamespot.com/" style="color:{link_color};">ComicVine</a>'
        scraper_link = f'<a href="https://github.com/cbanack/comic-vine-scraper" style="color:{link_color};">cbanack/comic-vine-scraper</a>'
        self._credit_lbl.setText(
            _("comicvine.credit").format(comicvine=cv_link, scraper=scraper_link)
        )

    def set_cover(self, pix):
        if pix:
            self._cover_lbl.setPixmap(
                pix.scaled(200, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self._cover_lbl.setText("—")

    def set_filename(self, filename: str):
        self._filename_lbl.setText(filename)

    def set_search_term(self, term):
        self._search_input.setText(term)

    def set_loading(self, loading):
        self._search_btn.setEnabled(not loading)
        self._btn_prev.setEnabled(False)
        self._btn_next.setEnabled(False)
        self._status_lbl.setText(" ")
        if loading:
            self._table.setRowCount(0)
            self._loading_overlay.setFont(_get_current_font(18, bold=True))
            self._loading_overlay.setText(_("comicvine.searching"))
            self._loading_overlay.show()
            self._loading_overlay.raise_()
            self._table.setEnabled(False)
        else:
            self._loading_overlay.hide()
            self._table.setEnabled(True)

    def show_error(self, msg):
        self._status_lbl.setText(msg)

    def populate_series(self, series_list, total, terms, page):
        self._series_data   = series_list
        self._total         = total
        self._current_page  = page
        self._current_terms = terms

        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        for idx, s in enumerate(series_list):
            row = self._table.rowCount()
            self._table.insertRow(row)
            name_item = QTableWidgetItem(s.get("name", ""))
            name_item.setData(_DATA_ROLE, idx)          # ← index original
            self._table.setItem(row, 0, name_item)
            year_item = QTableWidgetItem(str(s.get("start_year", "")))
            year_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, 1, year_item)
            count_item = QTableWidgetItem(str(s.get("issue_count", "")))
            count_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, 2, count_item)
            self._table.setItem(row, 3, QTableWidgetItem(s.get("publisher", "")))
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(1, Qt.AscendingOrder)
        if series_list:
            best_row = 0
            if terms:
                import difflib, re
                query = terms.lower()

                # Extraire une année (1800-2100) depuis le terme de recherche
                year_match = re.search(r'\b(1[89]\d{2}|20\d{2}|21\d{2})\b', terms)
                target_year = int(year_match.group(1)) if year_match else None

                best_score = -1.0
                best_year_dist = float('inf')
                for row in range(self._table.rowCount()):
                    item = self._table.item(row, 0)
                    if not item:
                        continue
                    score = difflib.SequenceMatcher(None, query, item.text().lower()).ratio()
                    if score > best_score:
                        best_score = score
                        best_row = row
                        year_item = self._table.item(row, 1)
                        try:
                            best_year_dist = abs(int(year_item.text()) - target_year) if (target_year and year_item and year_item.text()) else float('inf')
                        except ValueError:
                            best_year_dist = float('inf')
                    elif score == best_score and target_year:
                        year_item = self._table.item(row, 1)
                        try:
                            dist = abs(int(year_item.text()) - target_year) if (year_item and year_item.text()) else float('inf')
                        except ValueError:
                            dist = float('inf')
                        if dist < best_year_dist:
                            best_year_dist = dist
                            best_row = row
            self._table.selectRow(best_row)
            self._table.scrollTo(self._table.model().index(best_row, 0))

        label = _("comicvine.results_count").format(count=len(series_list), total=total)
        self._status_lbl.setText(label)
        self._update_pagination_ui()

    def _update_pagination_ui(self):
        per_page = 100
        max_page = max(1, (self._total + per_page - 1) // per_page)
        self._btn_prev.setEnabled(self._current_page > 1)
        self._btn_next.setEnabled(self._current_page < max_page and self._total > 0)
        if self._total > 0:
            self._page_lbl.setText(
                _("comicvine.page_indicator").format(page=self._current_page))
        else:
            self._page_lbl.setText("")

    def _on_prev_page(self):
        if self._current_page > 1:
            self.search_requested.emit(self._current_terms, self._current_page - 1)

    def _on_next_page(self):
        self.search_requested.emit(self._current_terms, self._current_page + 1)

    def _on_search_clicked(self):
        term = self._search_input.text().strip()
        if term:
            self._total = 0
            self.search_requested.emit(term, 1)

    def _on_ok_clicked(self):
        row = self._table.currentRow()
        if row < 0:
            return
        item = self._table.item(row, 0)
        if item is None:
            return
        idx = item.data(_DATA_ROLE)
        if idx is None or idx >= len(self._series_data):
            return
        self.series_confirmed.emit(self._series_data[idx])


# ── Page 2 : Choix de l'issue ─────────────────────────────────────────────────

class _Page2Issues(QWidget):

    back_requested        = Signal()
    issue_confirmed       = Signal(dict)
    skip_requested        = Signal()
    cancel_requested      = Signal()
    issue_image_requested = Signal(str)

    def __init__(self, parent=None, batch=False):
        super().__init__(parent)
        self._issues_data    = []
        self._batch          = batch
        self._batch_counter  = None

        main = QHBoxLayout(self)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(12)

        # Colonne gauche
        left = QVBoxLayout()
        left.setSpacing(8)

        self._cover_lbl = QLabel()
        self._cover_lbl.setFixedSize(200, 280)
        self._cover_lbl.setAlignment(Qt.AlignCenter)
        left.addWidget(self._cover_lbl)

        self._filename_lbl = QLabel()
        self._filename_lbl.setWordWrap(True)
        self._filename_lbl.setAlignment(Qt.AlignCenter)
        self._filename_lbl.setFixedWidth(200)
        left.addWidget(self._filename_lbl)

        self._issue_cover_lbl = QLabel()
        self._issue_cover_lbl.setFixedSize(120, 168)
        self._issue_cover_lbl.setAlignment(Qt.AlignCenter)
        left.addWidget(self._issue_cover_lbl, 0, Qt.AlignHCenter)

        left.addStretch()
        main.addLayout(left, 0)

        # Partie droite
        right = QVBoxLayout()
        right.setSpacing(6)

        self._status_lbl = QLabel(" ")
        self._status_lbl.setFixedHeight(18)
        right.addWidget(self._status_lbl)

        # Conteneur pour tableau + overlay
        from PySide6.QtWidgets import QStackedLayout
        tbl_container = QWidget()
        tbl_stack = QStackedLayout(tbl_container)
        tbl_stack.setStackingMode(QStackedLayout.StackAll)

        self._table = QTableWidget()
        self._table.setColumnCount(2)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)
        self._table.currentItemChanged.connect(
            lambda cur, _: self._on_row_changed(self._table.currentRow()))
        self._table.doubleClicked.connect(self._on_ok_clicked)
        tbl_stack.addWidget(self._table)

        self._loading_overlay = QLabel()
        self._loading_overlay.setAlignment(Qt.AlignCenter)
        self._loading_overlay.setWordWrap(True)
        self._loading_overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._loading_overlay.setStyleSheet(
            "color: rgb(220,0,0); background: transparent; font-weight: bold;")
        self._loading_overlay.hide()
        tbl_stack.addWidget(self._loading_overlay)

        right.addWidget(tbl_container)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_back   = QPushButton()
        self._btn_back.clicked.connect(self.back_requested)
        self._btn_skip   = QPushButton()
        self._btn_skip.setEnabled(batch)
        self._btn_skip.clicked.connect(self.skip_requested)
        self._btn_cancel = QPushButton()
        self._btn_cancel.clicked.connect(self.cancel_requested)
        self._btn_ok     = QPushButton()
        self._btn_ok.setDefault(True)
        self._btn_ok.clicked.connect(self._on_ok_clicked)
        for w in (self._btn_back, self._btn_skip, self._btn_cancel, self._btn_ok):
            btn_row.addWidget(w)
        right.addLayout(btn_row)

        self._credit_lbl = QLabel()
        self._credit_lbl.setAlignment(Qt.AlignCenter)
        self._credit_lbl.setOpenExternalLinks(True)
        right.addWidget(self._credit_lbl)

        main.addLayout(right, 1)

    def retranslate(self, batch_counter=None):
        self._batch_counter = batch_counter
        theme = get_current_theme()
        font  = _get_current_font(10)
        font8 = _get_current_font(8)
        font9 = _get_current_font(9)
        bg    = theme["bg"]
        fg    = theme["text"]
        sep   = theme.get("separator", "#aaaaaa")
        alt   = theme.get("toolbar_bg", bg)

        self.setStyleSheet(f"QWidget {{ background: {bg}; color: {fg}; }}")
        cover_style = f"background: {alt}; border: 1px solid {sep};"
        self._cover_lbl.setStyleSheet(cover_style)
        self._issue_cover_lbl.setStyleSheet(cover_style)
        self._filename_lbl.setFont(font9)
        self._filename_lbl.setStyleSheet(f"color: {fg}; background: transparent;")

        self._status_lbl.setFont(font9)
        self._status_lbl.setStyleSheet(f"color: {fg}; background: transparent;")

        self._table.setStyleSheet(_tbl_style(theme))
        self._table.setFont(font8)
        col2_labels = [
            _("comicvine.col_issue_num"),
            _("comicvine.col_issue_title"),
        ]
        for col, label in enumerate(col2_labels):
            item = QTableWidgetItem(label)
            item.setFont(font8)
            self._table.setHorizontalHeaderItem(col, item)
        self._table.horizontalHeader().setFont(font8)
        self._table.setColumnWidth(0, 55)

        bs = _btn_style(theme)
        for btn, key in [
            (self._btn_back,   "comicvine.btn_back"),
            (self._btn_skip,   "comicvine.btn_skip"),
            (self._btn_cancel, "buttons.cancel"),
            (self._btn_ok,     "buttons.ok"),
        ]:
            btn.setFont(font)
            btn.setStyleSheet(bs)
            btn.setText(_(key))

        link_color = theme.get("link", "#4A9EFF")
        self._credit_lbl.setFont(_get_current_font(8))
        self._credit_lbl.setStyleSheet(f"color: {theme['disabled']}; background: transparent;")
        cv_link     = f'<a href="https://comicvine.gamespot.com/" style="color:{link_color};">ComicVine</a>'
        scraper_link = f'<a href="https://github.com/cbanack/comic-vine-scraper" style="color:{link_color};">cbanack/comic-vine-scraper</a>'
        self._credit_lbl.setText(
            _("comicvine.credit").format(comicvine=cv_link, scraper=scraper_link)
        )

    def set_cover(self, pix):
        if pix:
            self._cover_lbl.setPixmap(
                pix.scaled(200, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self._cover_lbl.setText("—")

    def set_filename(self, filename: str):
        self._filename_lbl.setText(filename)

    def set_issue_cover(self, pix):
        if pix:
            self._issue_cover_lbl.setPixmap(
                pix.scaled(120, 168, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self._issue_cover_lbl.setText("—")

    def clear_issue_cover(self):
        self._issue_cover_lbl.clear()
        self._issue_cover_lbl.setText("—")

    def show_loading_overlay(self, visible, loaded, total):
        if visible:
            self._table.setRowCount(0)
            self._status_lbl.setText(" ")
            font = _get_current_font(18, bold=True)
            self._loading_overlay.setFont(font)
            if total > 0:
                txt = _("comicvine.loading_issues_progress").format(
                    loaded=loaded, total=total)
            else:
                txt = _("comicvine.loading_issues")
            self._loading_overlay.setText(txt)
            self._loading_overlay.show()
            self._loading_overlay.raise_()
            self._table.setEnabled(False)
        else:
            self._loading_overlay.hide()
            self._table.setEnabled(True)

    def set_loading(self, loading):
        self._status_lbl.setText(_("comicvine.loading_issues") if loading else " ")

    def show_error(self, msg):
        self._status_lbl.setText(msg)

    def populate_issues(self, issues, series, target_number=None):
        self._issues_data = issues
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        for idx, iss in enumerate(issues):
            row = self._table.rowCount()
            self._table.insertRow(row)
            num_item = QTableWidgetItem(str(iss.get("issue_number", "")))
            num_item.setTextAlignment(Qt.AlignCenter)
            num_item.setData(_DATA_ROLE, idx)
            self._table.setItem(row, 0, num_item)
            self._table.setItem(row, 1, QTableWidgetItem(iss.get("name", "")))
        self._table.setSortingEnabled(True)
        series_name = series.get("name", "") if series else ""
        status = _("comicvine.issues_count").format(series=series_name, count=len(issues))
        if self._batch_counter:
            status = f"{status}  —  {self._batch_counter}"
        self._status_lbl.setText(status)

        if not issues:
            return

        # Présélectionner l'issue correspondant au numéro cible
        select_row = 0
        if target_number:
            target = str(target_number).lstrip("0")
            for row in range(self._table.rowCount()):
                item = self._table.item(row, 0)
                if item and item.text().lstrip("0") == target:
                    select_row = row
                    break
        self._table.selectRow(select_row)
        self._table.scrollTo(self._table.model().index(select_row, 0))

    def _get_issue_at_current_row(self):
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 0)
        if item is None:
            return None
        idx = item.data(_DATA_ROLE)
        if idx is None or idx >= len(self._issues_data):
            return None
        return self._issues_data[idx]

    def _on_row_changed(self, _row):
        iss = self._get_issue_at_current_row()
        if iss:
            url = iss.get("image_url")
            if url:
                self.issue_image_requested.emit(url)

    def _on_ok_clicked(self):
        iss = self._get_issue_at_current_row()
        if iss:
            self.issue_confirmed.emit(iss)
