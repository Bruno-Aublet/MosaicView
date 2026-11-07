"""
dialogs_qt.py — Boîtes de dialogue Qt respectant thème et police.
"""

import sys

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt, Signal

from modules.qt import state as _state_module
from modules.qt.app_window_qt import AppWindowMixin, QuestionWindowMixin
from modules.qt.localization import _


def position_dialog_on_parent(dialog, parent):
    """Centre `dialog` sur `parent` AVANT le premier affichage, sans flash.

    À appeler juste avant show()/show_nonmodal() (PAS dans showEvent). Force le
    calcul du layout via ensurePolished() puis positionne la fenêtre, de sorte que
    le premier rendu se fasse déjà au bon endroit (pas de saut visible).
    """
    if parent is None:
        return
    # ensurePolished() force le calcul du layout SANS modifier la taille demandée
    # (resize()/setFixedSize()). NE PAS utiliser adjustSize() : il écraserait la
    # taille explicite de la fenêtre par la taille minimale du contenu.
    dialog.ensurePolished()
    _center_on_widget(dialog, parent)


def _center_on_widget(dialog, parent):
    """Centre `dialog` sur `parent` en respectant les limites de l'écran."""
    if parent is None:
        return
    # Appelée aussi en différé (QTimer.singleShot depuis showEvent) : le
    # dialogue, détruit à sa fermeture (WA_DeleteOnClose), a pu disparaître.
    from shiboken6 import isValid
    if not isValid(dialog) or not isValid(parent):
        return
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QPoint
    top_left = parent.mapToGlobal(QPoint(0, 0))
    pw = parent.width()
    ph = parent.height()
    x = top_left.x() + (pw - dialog.width())  // 2
    y = top_left.y() + (ph - dialog.height()) // 2
    screen = QApplication.screenAt(top_left) or QApplication.primaryScreen()
    if screen:
        sa = screen.availableGeometry()
        fw = dialog.frameGeometry().width()
        fh = dialog.frameGeometry().height()
        x = max(sa.x(), min(x, sa.x() + sa.width()  - fw))
        y = max(sa.y(), min(y, sa.y() + sa.height() - fh))
    dialog.move(x, y)


class MsgDialog(AppWindowMixin, QDialog):
    """Boîte de dialogue non-modale (simple bouton OK) respectant thème et
    police courante, avec sa barre de titre dessinée par Qt (app_window_qt).
    Affichage via show_nonmodal().

    Paramètres
    ----------
    parent        : QWidget parent
    title_key     : clé de traduction pour le titre
    message_key   : clé de traduction pour le message
    message_kwargs: dict de kwargs passés à la clé de traduction du message (optionnel)
    """

    def __init__(self, parent, title_key: str, message_key: str,
                 message_kwargs: dict | None = None):
        super().__init__(parent)
        self._setup_window()
        self.setWindowFlags(Qt.Window)
        self.set_window_title(title_key)
        self._message_key = message_key
        self._message_kwargs = message_kwargs or {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(12)

        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        self._lbl.setMinimumWidth(380)
        layout.addWidget(self._lbl)

        self._btn_ok = QPushButton()
        self._btn_ok.clicked.connect(self.close)
        layout.addWidget(self._btn_ok, alignment=Qt.AlignCenter)

        self._connect_language()

    def _apply_theme(self):
        from modules.qt.state import get_current_theme
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }} "
            f"QLabel  {{ color: {theme['text']}; }} "
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 12px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )

    def _retranslate(self):
        self._lbl.setText(_(self._message_key, **self._message_kwargs))
        self._btn_ok.setText(_("buttons.ok"))
        try:
            from modules.qt.font_manager_qt import get_current_font
            font = get_current_font()
            self._lbl.setFont(font)
            self._btn_ok.setFont(font)
        except Exception:
            pass  # repli : police précédente gardée


class ConfirmDialog(QuestionWindowMixin, QDialog):
    """Boîte de dialogue non-modale OK / Annuler respectant thème et police
    courante, avec sa barre de titre dessinée par Qt (app_window_qt).

    result_signal émet True si l'utilisateur clique OK, False sinon (Annuler
    ou fermeture). Utilisation : ConfirmDialog(parent, title_key, message_key).ask_async(on_result)
    """

    result_signal = Signal(bool)   # True = OK, False = Annuler/fermeture

    def __init__(self, parent, title_key: str, message_key: str,
                 message_kwargs: dict | None = None, ok_key: str = "buttons.ok",
                 plain_text: bool = False):
        """ok_key : texte du bouton de validation. plain_text : message en
        texte brut, obligatoire quand message_kwargs contient un texte venu
        d'un fichier (nom de page : règle de sécurité n° 3 de CLAUDE.md)."""
        super().__init__(parent)
        self._setup_window(close_answer=False)
        self.setWindowFlags(Qt.Window)
        self.set_window_title(title_key)
        self._message_key = message_key
        self._message_kwargs = message_kwargs or {}
        self._ok_key = ok_key

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(12)

        self._lbl = QLabel()
        if plain_text:
            self._lbl.setTextFormat(Qt.PlainText)
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        self._lbl.setMinimumWidth(380)
        layout.addWidget(self._lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_ok = QPushButton()
        self._btn_ok.setDefault(True)
        self._btn_ok.clicked.connect(lambda: self._answer(True))
        btn_row.addWidget(self._btn_ok)
        self._btn_cancel = QPushButton()
        self._btn_cancel.clicked.connect(lambda: self._answer(False))
        btn_row.addWidget(self._btn_cancel)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._connect_language()

    def _apply_theme(self):
        from modules.qt.state import get_current_theme
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }} "
            f"QLabel  {{ color: {theme['text']}; }} "
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 12px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )

    def _retranslate(self):
        self._lbl.setText(_(self._message_key, **self._message_kwargs))
        self._btn_ok.setText(_(self._ok_key))
        self._btn_cancel.setText(_("buttons.cancel"))
        try:
            from modules.qt.font_manager_qt import get_current_font
            font = get_current_font()
            self._lbl.setFont(font)
            self._btn_ok.setFont(font)
            self._btn_cancel.setFont(font)
        except Exception:
            pass  # repli : police précédente gardée


# ─────────────────────────────────────────────────────────────────────────────
# ErrorDialog — remplace QMessageBox.critical / .warning
# ─────────────────────────────────────────────────────────────────────────────

def page_operation_failed(parent, title_key, message_key, entry=None):
    """Fenêtre d'échec d'une opération sur une page, `message_key` attendant
    {error}. Appelée dans un bloc except : le message cite l'exception, et sa
    pile va au journal des erreurs (ErrorDialog). Appelée hors exception, avec
    `entry` : la page est illisible, le message la nomme."""
    from modules.qt.temp_files import exception_text
    error = sys.exc_info()[1]
    if error is not None:
        ErrorDialog(parent, title_key,
                    lambda d=exception_text(error): _(message_key, error=d)).show_nonmodal()
    else:
        ErrorDialog(parent, title_key,
                    lambda n=(entry or {}).get("orig_name", ""):
                    _(message_key, error=_("messages.errors.batch_image_unreadable", error=n))
                    ).show_nonmodal()


class ErrorDialog(AppWindowMixin, QDialog):
    """Boîte de dialogue d'erreur/avertissement respectant thème et police,
    avec sa barre de titre dessinée par Qt (app_window_qt).

    title_key : clé de traduction du titre (`_wt()` pour Windows, `_()` pour
    la barre de titre). message : str (figée) ou callable () → str (callable
    utilisé pour reconstruire le texte à chaque changement de langue).

    play_sound : easter egg (cri de Wilhelm) joué à l'ouverture — réservé aux
    erreurs système rares et graves (échec d'écriture disque, erreur réseau
    imprévue...), jamais aux simples validations utilisateur. False par défaut.

    Usage :
        ErrorDialog(parent, "key.title", lambda: _("key.msg")).show_nonmodal()
    """

    def __init__(self, parent, title_key, message, play_sound=False, ok_text_key: str = "buttons.ok"):
        super().__init__(parent)
        # Ouverte depuis un bloc except : la pile de l'erreur va au journal des
        # erreurs, précédée du titre et du message de la fenêtre (le journal
        # les traduit en lettres latines, d'où un titre à traduire, pas sa clé).
        from modules.qt.error_log import log_handled_error
        title = lambda: _(title_key)
        log_handled_error(sys.exc_info()[1], title, message)
        self._setup_window()
        self.set_window_title(title_key)
        self._message_fn = message if callable(message) else (lambda m=message: m)
        self._ok_text_key = ok_text_key

        if play_sound:
            from modules.qt.easter_eggs_qt import play_wilhelm_scream
            play_wilhelm_scream()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(12)

        self._lbl = QLabel()
        # Texte brut : un message d'erreur cite souvent un nom de fichier ou
        # de page, ou le texte d'une exception ; en détection automatique, une
        # balise <img src="\\serveur\…"> en première ligne ferait contacter ce
        # serveur par Windows dès l'affichage
        self._lbl.setTextFormat(Qt.PlainText)
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        self._lbl.setMinimumWidth(380)
        layout.addWidget(self._lbl)

        self._btn_ok = QPushButton()
        self._btn_ok.clicked.connect(self.accept)
        layout.addWidget(self._btn_ok, alignment=Qt.AlignCenter)

        self._connect_language()

    def _apply_theme(self):
        from modules.qt.state import get_current_theme
        theme = get_current_theme()
        bg   = theme['bg']
        fg   = theme['text']
        alt  = theme.get('toolbar_bg', bg)
        sep  = theme.get('separator', '#aaaaaa')
        self.setStyleSheet(f"QDialog {{ background: {bg}; }}")
        self._lbl.setStyleSheet(f"color: {fg}; background: {bg};")
        self._btn_ok.setStyleSheet(
            f"QPushButton {{ background: {alt}; color: {fg}; "
            f"border: 1px solid {sep}; padding: 4px 12px; border-radius: 3px; }} "
            f"QPushButton:hover {{ background: {sep}; }}"
        )

    def _retranslate(self):
        self._lbl.setText(self._message_fn())
        self._btn_ok.setText(_(self._ok_text_key))
        try:
            from modules.qt.font_manager_qt import get_current_font
            font = get_current_font()
            self._lbl.setFont(font)
            self._btn_ok.setFont(font)
        except Exception:
            pass  # repli : police précédente gardée


# ─────────────────────────────────────────────────────────────────────────────
# InfoDialog — remplace QMessageBox.information
# ─────────────────────────────────────────────────────────────────────────────

class InfoDialog(AppWindowMixin, QDialog):
    """Boîte de dialogue d'information respectant thème et police, avec sa
    barre de titre dessinée par Qt (app_window_qt).

    title_key : clé de traduction du titre (`_wt()` pour Windows, `_()` pour
    la barre de titre). message : str (figée) ou callable () → str.
    Le message peut contenir du HTML avec des liens <a href="...">.
    Connecter linkActivated pour gérer les clics sur les liens.

    Usage :
        InfoDialog(parent, "key.title", lambda: _("key.msg")).show_nonmodal()
    """

    def __init__(self, parent, title_key, message):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title(title_key)
        self._message_fn = message if callable(message) else (lambda m=message: m)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(12)

        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        self._lbl.setMinimumWidth(380)
        self._lbl.setOpenExternalLinks(False)
        self._lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        from modules.qt.utils import setup_html_label_context_menu
        setup_html_label_context_menu(self._lbl)
        layout.addWidget(self._lbl)

        self._btn_ok = QPushButton()
        self._btn_ok.clicked.connect(self.accept)
        layout.addWidget(self._btn_ok, alignment=Qt.AlignCenter)

        self._connect_language()

    def _apply_theme(self):
        from modules.qt.state import get_current_theme
        theme = get_current_theme()
        bg   = theme['bg']
        fg   = theme['text']
        alt  = theme.get('toolbar_bg', bg)
        sep  = theme.get('separator', '#aaaaaa')
        self.setStyleSheet(f"QDialog {{ background: {bg}; }}")
        self._lbl.setStyleSheet(f"color: {fg}; background: {bg};")
        self._btn_ok.setStyleSheet(
            f"QPushButton {{ background: {alt}; color: {fg}; "
            f"border: 1px solid {sep}; padding: 4px 12px; border-radius: 3px; }} "
            f"QPushButton:hover {{ background: {sep}; }}"
        )

    def _retranslate(self):
        self._lbl.setText(self._message_fn())
        self._btn_ok.setText(_("buttons.ok"))
        try:
            from modules.qt.font_manager_qt import get_current_font
            font = get_current_font()
            self._lbl.setFont(font)
            self._btn_ok.setFont(font)
        except Exception:
            pass  # repli : police précédente gardée


# ─────────────────────────────────────────────────────────────────────────────
# QuestionYNCDialog — remplace QMessageBox.question (Yes / No / Cancel)
# ─────────────────────────────────────────────────────────────────────────────

class QuestionYNCDialog(QuestionWindowMixin, QDialog):
    """Boîte de dialogue Oui / Non / Annuler respectant thème et police, avec
    sa barre de titre dessinée par Qt (app_window_qt).

    title_key : clé de traduction du titre. message : str (figée) ou
    callable () → str.
    result_signal émet :
        "yes"    si l'utilisateur clique Oui
        "no"     si l'utilisateur clique Non
        "cancel" si l'utilisateur clique Annuler ou ferme la fenêtre

    Usage :
        QuestionYNCDialog(parent, "key", lambda: build_msg()).ask_async(on_result)
    """

    result_signal = Signal(str)   # "yes" | "no" | "cancel"

    def __init__(self, parent, title_key, message):
        super().__init__(parent)
        self._setup_window(close_answer="cancel")
        self.setWindowFlags(Qt.Window)
        self.set_window_title(title_key)
        self._message_fn = message if callable(message) else (lambda m=message: m)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(12)

        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        self._lbl.setMinimumWidth(380)
        layout.addWidget(self._lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_yes = QPushButton()
        self._btn_yes.setDefault(True)
        self._btn_yes.clicked.connect(lambda: self._answer("yes"))
        btn_row.addWidget(self._btn_yes)
        self._btn_no = QPushButton()
        self._btn_no.clicked.connect(lambda: self._answer("no"))
        btn_row.addWidget(self._btn_no)
        self._btn_cancel = QPushButton()
        self._btn_cancel.clicked.connect(lambda: self._answer("cancel"))
        btn_row.addWidget(self._btn_cancel)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._connect_language()

    def _apply_theme(self):
        from modules.qt.state import get_current_theme
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }} "
            f"QLabel  {{ color: {theme['text']}; }} "
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 12px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )

    def _retranslate(self):
        self._lbl.setText(self._message_fn())
        self._btn_yes.setText(_("buttons.yes"))
        self._btn_no.setText(_("buttons.no"))
        self._btn_cancel.setText(_("buttons.cancel"))
        try:
            from modules.qt.font_manager_qt import get_current_font
            font = get_current_font()
            self._lbl.setFont(font)
            self._btn_yes.setFont(font)
            self._btn_no.setFont(font)
            self._btn_cancel.setFont(font)
        except Exception:
            pass  # repli : police précédente gardée


# ─────────────────────────────────────────────────────────────────────────────
# ConfirmYNDialog — Oui / Non non-modal avec signal
# ─────────────────────────────────────────────────────────────────────────────

class ConfirmYNDialog(QuestionWindowMixin, QDialog):
    """Boîte de dialogue non-modale Oui / Non, avec sa barre de titre
    dessinée par Qt (app_window_qt).

    title_key : clé de traduction du titre ; message : callable () → str pour
    le support multilingue.
    Émet result_signal(True) si Oui, result_signal(False) si Non ou fermeture.
    """

    result_signal = Signal(bool)

    def __init__(self, parent, title_key, message):
        super().__init__(parent)
        self._setup_window(close_answer=False)
        self.setWindowFlags(Qt.Window)
        self.set_window_title(title_key)
        self._message_fn = message if callable(message) else (lambda m=message: m)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(12)

        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        self._lbl.setMinimumWidth(380)
        layout.addWidget(self._lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_yes = QPushButton()
        self._btn_yes.setDefault(True)
        self._btn_yes.clicked.connect(lambda: self._answer(True))
        btn_row.addWidget(self._btn_yes)
        self._btn_no = QPushButton()
        self._btn_no.clicked.connect(lambda: self._answer(False))
        btn_row.addWidget(self._btn_no)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._connect_language()

    def _apply_theme(self):
        from modules.qt.state import get_current_theme
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }} "
            f"QLabel  {{ color: {theme['text']}; }} "
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 12px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )

    def _retranslate(self):
        self._lbl.setText(self._message_fn())
        self._btn_yes.setText(_("buttons.yes"))
        self._btn_no.setText(_("buttons.no"))
        try:
            from modules.qt.font_manager_qt import get_current_font
            font = get_current_font()
            self._lbl.setFont(font)
            self._btn_yes.setFont(font)
            self._btn_no.setFont(font)
        except Exception:
            pass  # repli : police précédente gardée


# ─────────────────────────────────────────────────────────────────────────────
# Helpers utilitaires
# ─────────────────────────────────────────────────────────────────────────────

def detect_duplicate_filenames_for_save(entries_to_check=None, state=None):
    if state is None:
        state = _state_module.state
    if entries_to_check is None:
        entries_to_check = [e for e in state.images_data if e["is_image"] and not e.get("is_dir")]

    filename_counts = {}
    for entry in entries_to_check:
        filename = entry["orig_name"]
        filename_counts[filename] = filename_counts.get(filename, 0) + 1

    duplicate_names = [name for name, count in filename_counts.items() if count > 1]
    return len(duplicate_names) > 0, duplicate_names
