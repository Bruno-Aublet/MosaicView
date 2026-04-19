# update_checker_qt.py — Vérification des mises à jour depuis GitHub Releases

import webbrowser
from urllib.request import urlopen
from urllib.error import URLError
import json
import threading
from packaging.version import Version

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
)
from PySide6.QtCore import Qt, QObject, Signal

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font

_RELEASES_API  = "https://api.github.com/repos/Bruno-Aublet/MosaicView/releases/latest"
_RELEASES_PAGE = "https://github.com/Bruno-Aublet/MosaicView/releases/latest"
_TIMEOUT       = 5  # secondes


def _fetch_latest_version() -> str:
    """Retourne le tag de la dernière release (ex. 'v1.0.1'), ou lève une exception."""
    with urlopen(_RELEASES_API, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read().decode())
    return data["tag_name"]


def _normalize(tag: str) -> str:
    """'v1.0.1' → '1.0.1'"""
    return tag.lstrip("v")


def _is_newer(latest: str, current: str) -> bool:
    """Retourne True si latest est strictement supérieur à current."""
    try:
        return Version(latest) > Version(current)
    except Exception:
        return False


def check_for_updates_qt(parent):
    """Point d'entrée : ouvre la fenêtre de vérification (callback menubar)."""
    dlg = _UpdateDialog(parent)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


# ── Signal interne pour renvoyer le résultat du thread vers le QDialog ────────

class _ResultSignal(QObject):
    ready = Signal(str, str)   # (status, latest_tag)  status = "ok" | "update" | "error"


# ── Dialog ────────────────────────────────────────────────────────────────────

class _UpdateDialog(QDialog):

    def __init__(self, parent):
        super().__init__(parent)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.resize(360, 160)

        self._latest_tag  = ""
        self._status      = "checking"   # "checking" | "ok" | "update" | "error"

        # Signal pour recevoir le résultat du thread réseau
        self._signal = _ResultSignal()
        self._signal.ready.connect(self._on_result)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(16)

        self._message = QLabel()
        self._message.setAlignment(Qt.AlignCenter)
        self._message.setWordWrap(True)
        layout.addWidget(self._message)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.addStretch()

        self._download_btn = QPushButton()
        self._download_btn.setCursor(Qt.PointingHandCursor)
        self._download_btn.clicked.connect(self._on_download)
        self._download_btn.hide()
        btn_row.addWidget(self._download_btn)

        self._retry_btn = QPushButton()
        self._retry_btn.clicked.connect(self._on_retry)
        self._retry_btn.hide()
        btn_row.addWidget(self._retry_btn)

        self._ok_btn = QPushButton()
        self._ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._ok_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Langue à la volée
        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
        self._center_parent = parent

        self._retranslate()

        # Lancer la vérification réseau en thread
        t = threading.Thread(target=self._fetch, daemon=True)
        t.start()

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            def _do_center():
                from PySide6.QtWidgets import QApplication
                from PySide6.QtCore import QPoint
                top_left = p.mapToGlobal(QPoint(0, 0))
                x = top_left.x() + (p.width()  - self.width())  // 2
                y = top_left.y() + (p.height() - self.height()) // 2
                screen = QApplication.screenAt(top_left) or QApplication.primaryScreen()
                if screen:
                    sa = screen.availableGeometry()
                    x = max(sa.left(), min(x, sa.right()  - self.width()))
                    y = max(sa.top(),  min(y, sa.bottom() - self.height()))
                self.move(x, y)
            QTimer.singleShot(0, _do_center)

    def _fetch(self):
        """Exécuté dans un thread — ne touche pas aux widgets Qt directement."""
        try:
            tag = _fetch_latest_version()
            import MosaicView as _main
            current = getattr(_main, "__version__", "0.0.0")
            if _is_newer(_normalize(tag), _normalize(current)):
                self._signal.ready.emit("update", tag)
            else:
                self._signal.ready.emit("ok", tag)
        except Exception:
            self._signal.ready.emit("error", "")

    def _on_result(self, status: str, latest_tag: str):
        self._status     = status
        self._latest_tag = latest_tag
        self._retranslate()

    def _on_download(self):
        webbrowser.open(_RELEASES_PAGE)

    def _on_retry(self):
        self._status = "checking"
        self._retranslate()
        t = threading.Thread(target=self._fetch, daemon=True)
        t.start()

    def _retranslate(self):
        theme = get_current_theme()
        font  = _get_current_font(11)
        font_btn = _get_current_font(10)

        self.setWindowTitle(_wt("updates.dialog_title"))
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )

        self._message.setFont(font)
        self._message.setStyleSheet(f"color: {theme['text']}; background: transparent;")

        import MosaicView as _main
        current = getattr(_main, "__version__", "?")

        if self._status == "checking":
            self._message.setText(_("updates.checking"))
            self._download_btn.hide()
            self._retry_btn.hide()

        elif self._status == "ok":
            self._message.setText(
                _("updates.up_to_date").replace("{version}", _normalize(self._latest_tag))
            )
            self._download_btn.hide()
            self._retry_btn.hide()

        elif self._status == "update":
            self._message.setText(
                _("updates.update_available")
                .replace("{latest}",  _normalize(self._latest_tag))
                .replace("{current}", current)
            )
            self._download_btn.show()
            self._download_btn.setText(_("updates.download"))
            self._download_btn.setFont(font_btn)
            self._download_btn.setStyleSheet(
                f"QPushButton {{ background: #2a7a2a; color: #ffffff; "
                f"border: none; padding: 6px 16px; border-radius: 4px; }} "
                f"QPushButton:hover {{ background: #3a9a3a; }}"
            )
            self._retry_btn.hide()

        elif self._status == "error":
            self._message.setText(_("updates.error"))
            self._download_btn.hide()
            self._retry_btn.setText(_("updates.retry"))
            self._retry_btn.setFont(font_btn)
            self._retry_btn.setStyleSheet(
                f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
                f"border: 1px solid #aaaaaa; padding: 6px 16px; border-radius: 4px; }} "
                f"QPushButton:hover {{ background: {theme['separator']}; }}"
            )
            self._retry_btn.show()

        self._ok_btn.setText(_("buttons.close"))
        self._ok_btn.setFont(font_btn)
        self._ok_btn.setStyleSheet(
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 6px 16px; border-radius: 4px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass


# ── Vérification automatique au démarrage ─────────────────────────────────────

class _StartupSignal(QObject):
    update_found = Signal(str)   # latest_tag


_startup_sig = None  # référence module-level pour éviter le GC


def check_for_updates_on_startup(main_window) -> None:
    """Lance la vérification en arrière-plan au démarrage.
    Si une nouvelle version est détectée, notifie main_window via
    show_update_banner(latest) et set_update_available_in_menu(latest).
    Silencieux si à jour ou erreur réseau.
    """
    global _startup_sig
    sig = _StartupSignal()
    sig.update_found.connect(lambda tag: _on_startup_update_found(main_window, tag))
    _startup_sig = sig

    def _fetch():
        try:
            tag = _fetch_latest_version()
            import MosaicView as _main
            current = getattr(_main, "__version__", "0.0.0")
            if _is_newer(_normalize(tag), _normalize(current)):
                sig.update_found.emit(tag)
        except Exception:
            pass  # silencieux au démarrage

    t = threading.Thread(target=_fetch, daemon=True)
    t.start()


def _on_startup_update_found(main_window, latest_tag: str) -> None:
    latest = _normalize(latest_tag)
    if hasattr(main_window, "show_update_banner"):
        main_window.show_update_banner(latest)
    if hasattr(main_window, "set_update_available_in_menu"):
        main_window.set_update_available_in_menu(latest)
