"""
dialogs_qt.py — Boîtes de dialogue Qt respectant thème et police.
"""

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt

from modules.qt import state as _state_module
from modules.qt.localization import _, _wt


class MsgDialog(QDialog):
    """Boîte de dialogue modale respectant thème et police courante.

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
        self._title_key = title_key
        self._message_key = message_key
        self._message_kwargs = message_kwargs or {}
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(12)

        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        self._lbl.setMinimumWidth(380)
        layout.addWidget(self._lbl)

        self._btn_ok = QPushButton()
        self._btn_ok.clicked.connect(self.accept)
        layout.addWidget(self._btn_ok, alignment=Qt.AlignCenter)

        self._retranslate()
        self._apply_font()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: (self._retranslate(), self._apply_font())
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def exec(self):
        from modules.qt import user_guide_qt as _guide_mod
        title_key = self._title_key
        message_key = self._message_key
        message_kwargs = self._message_kwargs

        def _reopen():
            w = MsgDialog(_guide_mod._help_window_ref, title_key, message_key, message_kwargs)
            w.exec()

        _guide_mod.register_child_reopen(_reopen)
        try:
            return super().exec()
        finally:
            _guide_mod.unregister_child_reopen()

    def _retranslate(self):
        from modules.qt.state import get_current_theme
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }} "
            f"QLabel  {{ color: {theme['text']}; }} "
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 12px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self.setWindowTitle(_wt(self._title_key))
        self._lbl.setText(_(self._message_key, **self._message_kwargs))
        self._btn_ok.setText(_("buttons.ok"))

    def _apply_font(self):
        try:
            from modules.qt.font_manager_qt import get_current_font
            font = get_current_font()
            self._lbl.setFont(font)
            self._btn_ok.setFont(font)
        except Exception:
            pass


class ConfirmDialog(QDialog):
    """Boîte de dialogue modale OK / Annuler respectant thème et police courante.

    Retourne True si l'utilisateur clique OK, False sinon.
    Utilisation : ConfirmDialog(parent, title_key, message_key).ask()
    """

    def __init__(self, parent, title_key: str, message_key: str,
                 message_kwargs: dict | None = None):
        super().__init__(parent)
        self._title_key = title_key
        self._message_key = message_key
        self._message_kwargs = message_kwargs or {}
        self.setModal(True)

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
        self._btn_ok = QPushButton()
        self._btn_ok.setDefault(True)
        self._btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(self._btn_ok)
        self._btn_cancel = QPushButton()
        self._btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        self._apply_font()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: (self._retranslate(), self._apply_font())
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def ask(self) -> bool:
        return self.exec() == QDialog.Accepted

    def _retranslate(self):
        from modules.qt.state import get_current_theme
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }} "
            f"QLabel  {{ color: {theme['text']}; }} "
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 12px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self.setWindowTitle(_wt(self._title_key))
        self._lbl.setText(_(self._message_key, **self._message_kwargs))
        self._btn_ok.setText(_("buttons.ok"))
        self._btn_cancel.setText(_("buttons.cancel"))

    def _apply_font(self):
        try:
            from modules.qt.font_manager_qt import get_current_font
            font = get_current_font()
            self._lbl.setFont(font)
            self._btn_ok.setFont(font)
            self._btn_cancel.setFont(font)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# ErrorDialog — remplace QMessageBox.critical / .warning
# ─────────────────────────────────────────────────────────────────────────────

class ErrorDialog(QDialog):
    """Boîte de dialogue d'erreur/avertissement respectant thème et police.

    title et message peuvent être une str (figée) ou un callable () → str
    (callable utilisé pour reconstruire le texte à chaque changement de langue).

    Usage :
        ErrorDialog(parent, title_text, message_text).exec()
        ErrorDialog(parent, lambda: _("key.title"), lambda: _("key.msg")).exec()
    """

    def __init__(self, parent, title, message):
        super().__init__(parent)
        self._title_fn   = title   if callable(title)   else (lambda t=title:   t)
        self._message_fn = message if callable(message) else (lambda m=message: m)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(12)

        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        self._lbl.setMinimumWidth(380)
        layout.addWidget(self._lbl)

        self._btn_ok = QPushButton()
        self._btn_ok.clicked.connect(self.accept)
        layout.addWidget(self._btn_ok, alignment=Qt.AlignCenter)

        self._apply_theme()
        self._apply_font()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: (self._apply_theme(), self._apply_font())
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

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
        self.setWindowTitle(self._title_fn())
        self._lbl.setText(self._message_fn())
        self._btn_ok.setText(_("buttons.ok"))

    def _apply_font(self):
        try:
            from modules.qt.font_manager_qt import get_current_font
            font = get_current_font()
            self._lbl.setFont(font)
            self._btn_ok.setFont(font)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# InfoDialog — remplace QMessageBox.information
# ─────────────────────────────────────────────────────────────────────────────

class InfoDialog(QDialog):
    """Boîte de dialogue d'information respectant thème et police.

    title et message peuvent être une str (figée) ou un callable () → str.

    Usage :
        InfoDialog(parent, title_text, message_text).exec()
        InfoDialog(parent, lambda: _("key.title"), lambda: _("key.msg")).exec()
    """

    def __init__(self, parent, title, message):
        super().__init__(parent)
        self._title_fn   = title   if callable(title)   else (lambda t=title:   t)
        self._message_fn = message if callable(message) else (lambda m=message: m)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(12)

        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        self._lbl.setMinimumWidth(380)
        layout.addWidget(self._lbl)

        self._btn_ok = QPushButton()
        self._btn_ok.clicked.connect(self.accept)
        layout.addWidget(self._btn_ok, alignment=Qt.AlignCenter)

        self._apply_theme()
        self._apply_font()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: (self._apply_theme(), self._apply_font())
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

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
        self.setWindowTitle(self._title_fn())
        self._lbl.setText(self._message_fn())
        self._btn_ok.setText(_("buttons.ok"))

    def _apply_font(self):
        try:
            from modules.qt.font_manager_qt import get_current_font
            font = get_current_font()
            self._lbl.setFont(font)
            self._btn_ok.setFont(font)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# QuestionYNCDialog — remplace QMessageBox.question (Yes / No / Cancel)
# ─────────────────────────────────────────────────────────────────────────────

class QuestionYNCDialog(QDialog):
    """Boîte de dialogue Oui / Non / Annuler respectant thème et police.

    title et message peuvent être une str (figée) ou un callable () → str.
    Retourne :
        "yes"    si l'utilisateur clique Oui
        "no"     si l'utilisateur clique Non
        "cancel" si l'utilisateur clique Annuler ou ferme la fenêtre

    Usage :
        result = QuestionYNCDialog(parent, title_text, message_text).ask()
        result = QuestionYNCDialog(parent, lambda: _("key"), lambda: build_msg()).ask()
    """

    def __init__(self, parent, title, message):
        super().__init__(parent)
        self._title_fn   = title   if callable(title)   else (lambda t=title:   t)
        self._message_fn = message if callable(message) else (lambda m=message: m)
        self._result = "cancel"
        self.setModal(True)

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
        self._btn_yes.clicked.connect(self._on_yes)
        btn_row.addWidget(self._btn_yes)
        self._btn_no = QPushButton()
        self._btn_no.clicked.connect(self._on_no)
        btn_row.addWidget(self._btn_no)
        self._btn_cancel = QPushButton()
        self._btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._apply_theme()
        self._apply_font()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: (self._apply_theme(), self._apply_font())
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    def _on_yes(self):
        self._result = "yes"
        self.accept()

    def _on_no(self):
        self._result = "no"
        self.accept()

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def ask(self) -> str:
        self.exec()
        return self._result

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
        self.setWindowTitle(self._title_fn())
        self._lbl.setText(self._message_fn())
        self._btn_yes.setText(_("buttons.yes"))
        self._btn_no.setText(_("buttons.no"))
        self._btn_cancel.setText(_("buttons.cancel"))

    def _apply_font(self):
        try:
            from modules.qt.font_manager_qt import get_current_font
            font = get_current_font()
            self._lbl.setFont(font)
            self._btn_yes.setFont(font)
            self._btn_no.setFont(font)
            self._btn_cancel.setFont(font)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Helpers utilitaires
# ─────────────────────────────────────────────────────────────────────────────

def detect_duplicate_filenames_for_save(entries_to_check=None):
    """Vérifie s'il y a des doublons de noms de fichiers dans les images."""
    state = _state_module.state
    if entries_to_check is None:
        entries_to_check = [e for e in state.images_data if e["is_image"] and not e.get("is_dir")]

    filename_counts = {}
    for entry in entries_to_check:
        filename = entry["orig_name"]
        filename_counts[filename] = filename_counts.get(filename, 0) + 1

    duplicate_names = [name for name, count in filename_counts.items() if count > 1]
    return len(duplicate_names) > 0, duplicate_names
