"""
modules/qt/error_log_dialog_qt.py — Fenêtre du journal des erreurs.

Affiche tout le journal (error_log.read_log_text), de l'erreur la plus récente
à la plus ancienne, avec le chemin cliquable de son dossier (Explorateur,
journal sélectionné) et deux boutons : copier ce contenu dans le
presse-papiers, l'envoyer par mail. Ouverte par le menu « À propos » (barre de
menus et menu contextuel du canvas) et par la section Configuration du mode
d'emploi.

Envoi par mail : comme le journal des numérisations
(scan_dialog_qt._send_scan_log_by_mail), le contenu est copié dans le
presse-papiers et le client mail s'ouvre avec une consigne de collage — un
lien mailto: ne peut pas joindre de fichier, et c'est la seule méthode qui
marche aussi avec un webmail. Seules les erreurs des MAIL_DAYS derniers jours
sont copiées, dans la limite de MAIL_MAX_BYTES (le journal peut atteindre
5 Mo, trop lourd à coller dans un mail) ; une ligne dit alors où se trouve le
journal complet.

Le contenu du journal s'affiche en police à chasse fixe (Consolas), pas dans
la police de la langue : c'est une trace technique en anglais, alignée en
colonnes, que la police d'une langue tengwar ou pIqaD rendrait illisible.
Titre, chemin, boutons et messages suivent la police, la langue et le thème
courants.
"""

import os
import urllib.parse
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font

_dialog = None  # fenêtre ouverte (une seule à la fois)

MAIL_DAYS = 7
MAIL_MAX_BYTES = 500 * 1024


def _show_no_errors(parent):
    from modules.qt.dialogs_qt import InfoDialog
    InfoDialog(parent, lambda: _wt("error_log.window_title"),
               lambda: _("error_log.no_errors")).show_nonmodal()


def mail_content(folder=None, now=None):
    """Texte copié pour l'envoi par mail : erreurs des MAIL_DAYS derniers jours,
    au plus MAIL_MAX_BYTES, précédées d'une ligne qui donne le chemin du
    journal complet si des erreurs plus anciennes ont été laissées. "" s'il
    n'y a aucune erreur."""
    import datetime
    from modules.qt.error_log import log_path, read_log_text
    now = now or datetime.datetime.now()
    content, omitted = read_log_text(folder, since=now - datetime.timedelta(days=MAIL_DAYS),
                                     max_bytes=MAIL_MAX_BYTES)
    if omitted:
        note = _wt("error_log.mail_partial", days=MAIL_DAYS,
                   path=os.path.realpath(log_path(folder)))
        content = f"{note}\n\n{content}" if content else note
    return content


def send_error_log_by_mail(parent):
    """Copie les erreurs récentes du journal (mail_content) dans le
    presse-papiers et ouvre le client mail vers l'adresse de MosaicView, avec
    dans le corps une consigne de collage. Textes par _wt() : ils partent vers
    un programme externe, illisibles en caractères CSUR (règle UI n° 7 de
    CLAUDE.md)."""
    from modules.qt.error_log import read_log_text
    from modules.qt.utils import _copy_to_clipboard, get_support_email
    if not read_log_text()[0]:
        _show_no_errors(parent)
        return
    _copy_to_clipboard(mail_content())
    subject = urllib.parse.quote("MosaicView - Error log")
    body = urllib.parse.quote(_wt("error_log.mail_body_hint") + "\n\n")
    webbrowser.open(f"mailto:{get_support_email()}?subject={subject}&body={body}")


def show_error_log(parent):
    """Ouvre la fenêtre du journal (ou la ramène au premier plan, relue), ou
    dit qu'aucune erreur n'a été enregistrée."""
    global _dialog
    from modules.qt.error_log import read_log_text
    content = read_log_text()[0]
    if not content:
        _show_no_errors(parent)
        return
    # Fenêtre détruite avec son parent (mode d'emploi fermé) sans passer par _on_close
    from shiboken6 import isValid
    if _dialog is not None and not isValid(_dialog):
        _dialog = None
    if _dialog is not None:
        _dialog.load(content)
        _dialog.raise_()
        _dialog.activateWindow()
        return
    _dialog = _ErrorLogDialog(parent, content)
    _dialog.show_nonmodal()


class _ErrorLogDialog(QDialog):
    """Fenêtre non modale du journal : dossier du journal cliquable, contenu
    en lecture seule, boutons Copier / Envoyer / Fermer."""

    def __init__(self, parent, content):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._center_parent = parent
        from modules.qt.error_log import log_dir
        self._folder = os.path.realpath(log_dir())
        self._status_text_fn = None
        self.resize(820, 520)

        from modules.qt.font_loader import resource_path
        from PySide6.QtGui import QIcon
        ico_path = resource_path("icons/MosaicView.ico")
        if os.path.exists(ico_path):
            self.setWindowIcon(QIcon(ico_path))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # Dossier du journal : clic → Explorateur, journal sélectionné
        self._path_lbl = QLabel()
        self._path_lbl.setTextFormat(Qt.PlainText)
        self._path_lbl.setAlignment(Qt.AlignCenter)
        self._path_lbl.setCursor(Qt.PointingHandCursor)
        self._path_lbl.setText(self._folder)
        self._path_lbl.mousePressEvent = (
            lambda e: self._open_explorer() if e.button() == Qt.LeftButton else None)
        from modules.qt.utils import setup_path_label_context_menu
        setup_path_label_context_menu(self._path_lbl, lambda: self._folder, self._open_explorer)
        layout.addWidget(self._path_lbl)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.NoWrap)
        from modules.qt.utils import setup_textedit_context_menu
        setup_textedit_context_menu(self._text)
        layout.addWidget(self._text, stretch=1)

        self._status_lbl = QLabel()
        self._status_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._status_lbl)

        row = QHBoxLayout()
        row.addStretch(1)
        self._btn_copy = QPushButton()
        self._btn_copy.clicked.connect(self._copy)
        self._btn_send = QPushButton()
        self._btn_send.clicked.connect(lambda: send_error_log_by_mail(self))
        self._btn_close = QPushButton()
        self._btn_close.clicked.connect(self.close)
        for btn in (self._btn_copy, self._btn_send, self._btn_close):
            btn.setCursor(Qt.PointingHandCursor)
            row.addWidget(btn)
        row.addStretch(1)
        layout.addLayout(row)

        self.load(content)
        self._retranslate()
        from modules.qt.language_signal import language_signal
        # Débranché une seule fois (closeEvent, finished et destroyed y mènent tous) :
        # un second disconnect fait afficher un RuntimeWarning par PySide.
        self._lang_link = {"handler": lambda _lang: self._retranslate()}
        language_signal.changed.connect(self._lang_link["handler"])
        self.finished.connect(self._on_close)
        # Détruite avec son parent (mode d'emploi fermé), la fenêtre ne passe ni par
        # closeEvent ni par finished : le signal de langue est débranché ici aussi.
        self.destroyed.connect(lambda *_args, link=self._lang_link: _disconnect_language(link))

    def show_nonmodal(self):
        from modules.qt.dialogs_qt import position_dialog_on_parent
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def load(self, content):
        """Affiche `content`, le journal relu à chaque ouverture de la fenêtre."""
        self._text.setPlainText(content)
        self._set_status(None)

    def _open_explorer(self):
        from modules.qt.error_log import existing_log_path
        latest = existing_log_path()
        if latest:
            from modules.qt.library_window import _explorer_select
            _explorer_select(os.path.realpath(latest))
        else:
            import subprocess
            try:
                subprocess.Popen(["explorer", self._folder])
            except OSError:
                pass  # sans conséquence : simple ouverture de l'Explorateur, rien n'est modifié

    def _copy(self):
        from modules.qt.utils import _copy_to_clipboard
        _copy_to_clipboard(self._text.toPlainText())
        self._set_status(lambda: _("error_log.copied"))

    def _set_status(self, status_fn):
        self._status_text_fn = status_fn
        self._status_lbl.setText(status_fn() if status_fn else "")

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(10)
        self.setWindowTitle(_wt("error_log.window_title"))
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")

        link_color = theme.get("link", "#4A9EFF")
        self._path_lbl.setFont(font)
        self._path_lbl.setStyleSheet(f"color: {link_color}; text-decoration: underline;")

        # Contenu : chasse fixe, à la taille de la police courante (voir docstring du module)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSizeF(_get_current_font(9).pointSizeF())
        self._text.setFont(mono)
        self._text.setStyleSheet(
            f"QPlainTextEdit {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid {theme['separator']}; }}")

        self._status_lbl.setFont(font)
        self._status_lbl.setStyleSheet(f"color: {theme['text']};")
        if self._status_text_fn:
            self._status_lbl.setText(self._status_text_fn())

        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 12px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}")
        for btn, key in ((self._btn_copy, "error_log.btn_copy"),
                         (self._btn_send, "error_log.btn_send"),
                         (self._btn_close, "buttons.close")):
            btn.setText(_(key))
            btn.setFont(font)
            btn.setStyleSheet(btn_style)

    def closeEvent(self, event):
        self._on_close()
        super().closeEvent(event)

    def _on_close(self):
        global _dialog
        if _dialog is self:
            _dialog = None
        _disconnect_language(self._lang_link)


def _disconnect_language(link):
    """Débranche le gestionnaire de langue de `link` s'il ne l'est pas déjà."""
    handler = link.pop("handler", None)
    if handler is None:
        return
    from modules.qt.language_signal import language_signal
    try:
        language_signal.changed.disconnect(handler)
    except (RuntimeError, TypeError):
        pass  # sans conséquence : signal déjà détruit à la fermeture de l'application
