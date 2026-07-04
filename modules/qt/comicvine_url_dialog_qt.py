# comicvine_url_dialog_qt.py — Fenêtre de chargement des métadonnées depuis une URL ComicVine directe
#
# Intercalée avant la fenêtre de recherche par nom de série (comicvine_dialog_qt.py).
# Permet à l'utilisateur qui connaît déjà l'adresse exacte d'une page ComicVine
# (issue ou série) de charger les métadonnées sans passer par la recherche.

import re

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font

_ISSUE_RE  = re.compile(r'comicvine\.gamespot\.com/.*?/4000-(\d+)', re.IGNORECASE)
_SERIES_RE = re.compile(r'comicvine\.gamespot\.com/.*?/4050-(\d+)', re.IGNORECASE)

# Ancien domaine de page web ComicVine (avant l'unification sur
# comicvine.gamespot.com), avec ou sans www. Le domaine à lui seul
# caractérise l'"ancien format" — le préfixe numérique après le tiret peut
# être "37-" (ancien identifiant d'issue) ou déjà "4000-"/"4050-" (certaines
# pages comicvine.com utilisent le nouvel identifiant sans avoir basculé de
# domaine). Dans tous les cas le numéro après le tiret est l'ID exploitable.
_ISSUE_RE_OLD  = re.compile(r'comicvine\.com/.*?/(?:37|4000)-(\d+)', re.IGNORECASE)
_SERIES_RE_OLD = re.compile(r'comicvine\.com/.*?/4050-(\d+)', re.IGNORECASE)


def _parse_comicvine_url(url):
    """Extrait le type ('issue' | 'series') et l'ID numérique d'une URL ComicVine.

    Reconnaît à la fois le format actuel (comicvine.gamespot.com, préfixes
    4000-/4050-) et l'ancien domaine de page web (comicvine.com, préfixes
    37-/4000- pour une issue, 4050- pour une série), antérieur à
    l'unification des domaines ComicVine.

    Retourne (kind, id) ou None si l'URL ne correspond à aucun format connu.
    """
    url = (url or "").strip()
    if not url:
        return None
    m = _ISSUE_RE.search(url)
    if m:
        return ("issue", m.group(1))
    m = _SERIES_RE.search(url)
    if m:
        return ("series", m.group(1))
    m = _ISSUE_RE_OLD.search(url)
    if m:
        return ("issue", m.group(1))
    m = _SERIES_RE_OLD.search(url)
    if m:
        return ("series", m.group(1))
    return None


# ── Helpers styles (repris du style comicvine_dialog_qt / nfo_dialog_qt) ──────

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


def _input_style(theme):
    sep = theme.get("separator", "#aaaaaa")
    return (
        f"QLineEdit {{ background: {theme.get('toolbar_bg', theme['bg'])}; "
        f"color: {theme['text']}; border: 1px solid {sep}; padding: 4px 6px; }}"
    )


def _label_style(theme):
    return f"color: {theme['text']};"


# ── Worker réseau ─────────────────────────────────────────────────────────────

class _UrlFetchWorker(QThread):
    issue_done  = Signal(dict)
    series_done = Signal(dict)
    error       = Signal(str)

    def __init__(self, api_key, kind, ident):
        super().__init__()
        self._api_key = api_key
        self._kind    = kind
        self._ident   = ident

    def run(self):
        try:
            from modules.qt.comicvine_scraper import get_issue_details, get_series_summary
            if self._kind == "issue":
                meta = get_issue_details(self._api_key, self._ident)
                if meta:
                    self.issue_done.emit(meta)
                else:
                    self.error.emit(_("comicvine_url.error_not_found"))
            else:
                series = get_series_summary(self._api_key, self._ident)
                if series:
                    self.series_done.emit(series)
                else:
                    self.error.emit(_("comicvine_url.error_not_found"))
        except Exception as e:
            from modules.qt.comicvine_scraper import error_to_signal_payload
            self.error.emit(error_to_signal_payload(e))


# ── Point d'entrée public ─────────────────────────────────────────────────────

def show_comicvine_url_dialog(parent, state, api_key, on_done=None):
    """Ouvre la fenêtre de chargement des métadonnées depuis une URL ComicVine (non-modale)."""
    dlg = _ComicVineUrlDialog(parent, state, api_key, on_done=on_done)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


# ── Fenêtre ────────────────────────────────────────────────────────────────────

class _ComicVineUrlDialog(QDialog):

    def __init__(self, parent, state, api_key, on_done=None):
        super().__init__(parent)
        self._state         = state
        self._api_key       = api_key
        self._on_done        = on_done
        self._worker         = None
        self._center_parent  = parent
        # Fonction () -> str retraduisant le statut/erreur actuellement affiché
        # dans self._lbl_status, réappelée depuis _retranslate() au changement
        # de langue (sinon le label reste figé dans l'ancienne langue).
        self._status_text_fn = lambda: ""

        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        self._lbl_intro = QLabel()
        self._lbl_intro.setWordWrap(True)
        self._lbl_intro.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_intro)

        self._lbl_field = QLabel()
        self._lbl_field.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_field)

        field_row = QHBoxLayout()
        field_row.setSpacing(6)
        field_row.addStretch()
        self._edit_url = QLineEdit()
        self._edit_url.setMinimumWidth(400)
        self._edit_url.returnPressed.connect(self._on_download_clicked)
        from modules.qt.utils import setup_lineedit_context_menu
        setup_lineedit_context_menu(self._edit_url)
        field_row.addWidget(self._edit_url)
        field_row.addStretch()
        layout.addLayout(field_row)

        self._lbl_hint = QLabel()
        self._lbl_hint.setWordWrap(True)
        self._lbl_hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_hint)

        self._lbl_warning = QLabel()
        self._lbl_warning.setWordWrap(True)
        self._lbl_warning.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_warning)

        self._lbl_status = QLabel(" ")
        self._lbl_status.setWordWrap(True)
        self._lbl_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_status)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._btn_download = QPushButton()
        self._btn_download.setDefault(True)
        self._btn_download.clicked.connect(self._on_download_clicked)
        self._btn_search = QPushButton()
        self._btn_search.clicked.connect(self._on_search_clicked)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_download)
        btn_row.addWidget(self._btn_search)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._edit_url.textChanged.connect(self._update_download_btn_enabled)
        existing_url = self._get_existing_comicvine_url()
        if existing_url:
            self._edit_url.setText(existing_url)
        self._update_download_btn_enabled()
        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    # ── Centrage à l'affichage ─────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            p = self._center_parent
            QTimer.singleShot(0, lambda: self._center_on(p))

    def _center_on(self, parent):
        from modules.qt.dialogs_qt import _center_on_widget
        _center_on_widget(self, parent)

    def _get_existing_comicvine_url(self):
        """Retourne l'URL ComicVine déjà présente dans les métadonnées locales
        (champ standard Web), si le fichier a déjà été scrapé (par MosaicView
        ou un autre outil). L'URL peut être dans un ancien format ComicVine
        (comicvine.com/.../37-XXXXX/) : elle reste utilisable telle quelle par
        _parse_comicvine_url."""
        meta = self._state.comic_metadata if self._state else None
        if not meta:
            return None
        return (meta.get("web") or "").strip() or None

    # ── Traduction + thème ─────────────────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(10)

        self.setWindowTitle(_wt("comicvine_url.window_title"))
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")

        self._lbl_intro.setText(_("comicvine_url.intro"))
        self._lbl_intro.setFont(font)
        self._lbl_intro.setStyleSheet(_label_style(theme))

        self._lbl_field.setText(_("comicvine_url.field_label"))
        self._lbl_field.setFont(font)
        self._lbl_field.setStyleSheet(_label_style(theme))

        self._edit_url.setPlaceholderText(_("comicvine_url.placeholder"))
        self._edit_url.setFont(font)
        self._edit_url.setStyleSheet(_input_style(theme))

        self._lbl_hint.setText(_("comicvine_url.hint_series"))
        self._lbl_hint.setFont(font)
        self._lbl_hint.setStyleSheet(_label_style(theme))

        self._lbl_warning.setText(_("comicvine_url.warning"))
        self._lbl_warning.setFont(font)
        self._lbl_warning.setStyleSheet(f"color: {theme['text']}; font-style: italic; font-weight: bold;")

        self._lbl_status.setText(self._status_text_fn())
        self._lbl_status.setFont(font)
        self._lbl_status.setStyleSheet(f"color: {theme.get('error', '#cc0000')};")

        btn_style = _btn_style(theme)
        self._btn_download.setText(_("comicvine_url.btn_download"))
        self._btn_download.setFont(font)
        self._btn_download.setStyleSheet(btn_style)

        self._btn_search.setText(_("comicvine_url.btn_search"))
        self._btn_search.setFont(font)
        self._btn_search.setStyleSheet(btn_style)

    # ── Actions ────────────────────────────────────────────────────────────────

    def _set_status(self, status_fn):
        """status_fn : callable () -> str, réappelé depuis _retranslate()."""
        self._status_text_fn = status_fn
        self._lbl_status.setText(status_fn())

    def _set_busy(self, busy, status_fn=None):
        self._btn_download.setEnabled(not busy and bool(self._edit_url.text().strip()))
        self._btn_search.setEnabled(not busy)
        self._edit_url.setEnabled(not busy)
        self._set_status(status_fn if status_fn is not None else (lambda: ""))

    def _update_download_btn_enabled(self):
        self._btn_download.setEnabled(bool(self._edit_url.text().strip()))

    def _on_download_clicked(self):
        url = self._edit_url.text().strip()
        if not url:
            self._set_status(lambda: _("comicvine_url.error_empty"))
            return

        parsed = _parse_comicvine_url(url)
        if not parsed:
            self._set_status(lambda: _("comicvine_url.error_invalid_url"))
            return

        kind, ident = parsed
        self._set_busy(True, lambda: _("comicvine_url.downloading"))
        self._worker = _UrlFetchWorker(self._api_key, kind, ident)
        self._worker.issue_done.connect(self._on_issue_done)
        self._worker.series_done.connect(self._on_series_done)
        self._worker.error.connect(self._on_fetch_error)
        self._worker.start()

    def _on_issue_done(self, meta):
        from modules.qt.comic_info import write_comic_metadata_from_scraper
        write_comic_metadata_from_scraper(self._state, meta)
        self._set_busy(False)
        parent = self._center_parent
        if self._on_done:
            self._on_done()
        self.close()
        from modules.qt.dialogs_qt import InfoDialog
        dlg = InfoDialog(parent,
                         lambda: _wt("comicvine_url.download_done_title"),
                         lambda: _("comicvine_url.download_done_message"))
        dlg.show_nonmodal()

    def _on_series_done(self, series):
        self._set_busy(False)
        from modules.qt.comicvine_dialog_qt import show_comicvine_dialog
        show_comicvine_dialog(self._center_parent, self._state, self._api_key,
                              on_done=self._on_done, preselected_series=series)
        self.close()

    def _on_fetch_error(self, msg):
        from modules.qt.comicvine_scraper import error_message_fn
        self._set_busy(False, error_message_fn(msg))

    def _on_search_clicked(self):
        from modules.qt.comicvine_dialog_qt import show_comicvine_dialog
        show_comicvine_dialog(self._center_parent, self._state, self._api_key,
                              on_done=self._on_done)
        self.close()

    # ── Nettoyage ─────────────────────────────────────────────────────────────

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass
        if self._worker is not None:
            for sig in ('issue_done', 'series_done', 'error'):
                try:
                    getattr(self._worker, sig).disconnect()
                except (RuntimeError, AttributeError):
                    pass
