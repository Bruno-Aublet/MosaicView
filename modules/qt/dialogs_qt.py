"""
dialogs_qt.py — Boîtes de dialogue Qt respectant thème et police.
"""

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt, QTimer, Signal

from modules.qt import state as _state_module
from modules.qt.localization import _, _wt


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
        self.setWindowFlags(Qt.Window)
        self._title_key = title_key
        self._message_key = message_key
        self._message_kwargs = message_kwargs or {}
        self._center_parent = parent
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)

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

        self._retranslate()
        self._apply_font()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: (self._retranslate(), self._apply_font())
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def show_nonmodal(self):
        # Positionne sur le panneau AVANT le premier affichage (pas de flash de
        # recentrage). Le showEvent ajuste ensuite si la taille a changé.
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

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

    result_signal = Signal(bool)   # True = OK, False = Annuler/fermeture

    def __init__(self, parent, title_key: str, message_key: str,
                 message_kwargs: dict | None = None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self._title_key = title_key
        self._message_key = message_key
        self._message_kwargs = message_kwargs or {}
        self._emitted = False
        self._center_parent = parent
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)

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
        self._btn_ok.clicked.connect(self._on_ok)
        btn_row.addWidget(self._btn_ok)
        self._btn_cancel = QPushButton()
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        self._apply_font()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: (self._retranslate(), self._apply_font())
        language_signal.changed.connect(self._lang_handler)

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _disconnect_lang(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def _finish(self, result: bool):
        if self._emitted:
            return
        self._emitted = True
        self._disconnect_lang()
        self.result_signal.emit(result)
        self.hide()
        self.deleteLater()

    def _on_ok(self):
        self._finish(True)

    def _on_cancel(self):
        self._finish(False)

    def closeEvent(self, event):
        if not self._emitted:
            self._emitted = True
            self._disconnect_lang()
            self.result_signal.emit(False)
        event.accept()

    def ask_async(self, on_result):
        """Affiche le dialogue (NON modal) et appelle on_result(bool) à la réponse."""
        self.result_signal.connect(on_result)
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

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

    play_sound : easter egg (cri de Wilhelm) joué à l'ouverture — réservé aux
    erreurs système rares et graves (échec d'écriture disque, erreur réseau
    imprévue...), jamais aux simples validations utilisateur. False par défaut.

    Usage :
        ErrorDialog(parent, title_text, message_text).exec()
        ErrorDialog(parent, lambda: _wt("key.title"), lambda: _("key.msg")).exec()
    """

    def __init__(self, parent, title, message, play_sound=False, ok_text_key: str = "buttons.ok"):
        super().__init__(parent)
        self._title_fn   = title   if callable(title)   else (lambda t=title:   t)
        self._message_fn = message if callable(message) else (lambda m=message: m)
        self._ok_text_key = ok_text_key
        self._center_parent = parent
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)

        if play_sound:
            from modules.qt.easter_eggs_qt import play_wilhelm_scream
            play_wilhelm_scream()

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

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def show_nonmodal(self):
        # Positionne sur le panneau AVANT le premier affichage (pas de flash de
        # recentrage). Le showEvent ajuste ensuite si la taille a changé.
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

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
        self.setWindowTitle(self._title_fn())
        self._lbl.setText(self._message_fn())
        self._btn_ok.setText(_(self._ok_text_key))

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
    Le message peut contenir du HTML avec des liens <a href="...">.
    Connecter linkActivated pour gérer les clics sur les liens.

    Usage :
        InfoDialog(parent, title_text, message_text).exec()
        InfoDialog(parent, lambda: _wt("key.title"), lambda: _("key.msg")).exec()
    """

    def __init__(self, parent, title, message):
        super().__init__(parent)
        self._title_fn   = title   if callable(title)   else (lambda t=title:   t)
        self._message_fn = message if callable(message) else (lambda m=message: m)
        self._center_parent = parent
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)

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

        self._apply_theme()
        self._apply_font()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: (self._apply_theme(), self._apply_font())
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def show_nonmodal(self):
        # Positionne sur le panneau AVANT le premier affichage (pas de flash de
        # recentrage). Le showEvent ajuste ensuite si la taille a changé.
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

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
        result = QuestionYNCDialog(parent, lambda: _wt("key"), lambda: build_msg()).ask()
    """

    result_signal = Signal(str)   # "yes" | "no" | "cancel"

    def __init__(self, parent, title, message):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self._title_fn   = title   if callable(title)   else (lambda t=title:   t)
        self._message_fn = message if callable(message) else (lambda m=message: m)
        self._result = "cancel"
        self._emitted = False
        self._center_parent = parent
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)

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
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._apply_theme()
        self._apply_font()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: (self._apply_theme(), self._apply_font())
        language_signal.changed.connect(self._lang_handler)

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _disconnect_lang(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def _finish(self, result):
        if self._emitted:
            return
        self._emitted = True
        self._result = result
        self._disconnect_lang()
        self.result_signal.emit(result)
        self.hide()
        self.deleteLater()

    def _on_yes(self):
        self._finish("yes")

    def _on_no(self):
        self._finish("no")

    def _on_cancel(self):
        self._finish("cancel")

    def closeEvent(self, event):
        if not self._emitted:
            self._emitted = True
            self._disconnect_lang()
            self.result_signal.emit("cancel")
        event.accept()

    def ask_async(self, on_result):
        """Affiche le dialogue (NON modal) et appelle on_result(str) à la réponse.
        result ∈ {'yes','no','cancel'}. Remplace l'ancien ask() modal."""
        self.result_signal.connect(on_result)
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

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
# ConfirmYNDialog — Oui / Non non-modal avec signal
# ─────────────────────────────────────────────────────────────────────────────

class ConfirmYNDialog(QDialog):
    """Boîte de dialogue non-modale Oui / Non.

    title et message sont des callables () → str pour le support multilingue.
    Émet result_signal(True) si Oui, result_signal(False) si Non ou fermeture.
    """

    result_signal = Signal(bool)

    def __init__(self, parent, title, message):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self._title_fn   = title   if callable(title)   else (lambda t=title:   t)
        self._message_fn = message if callable(message) else (lambda m=message: m)
        self._center_parent = parent

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
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._apply_theme()
        self._apply_font()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: (self._apply_theme(), self._apply_font())
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _disconnect_lang(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def _on_yes(self):
        self._disconnect_lang()
        self.result_signal.emit(True)
        self.hide()
        self.deleteLater()

    def _on_no(self):
        self._disconnect_lang()
        self.result_signal.emit(False)
        self.hide()
        self.deleteLater()

    def closeEvent(self, event):
        self._disconnect_lang()
        self.result_signal.emit(False)
        event.accept()

    def _on_close(self):
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
        self._btn_yes.setText(_("buttons.yes"))
        self._btn_no.setText(_("buttons.no"))

    def _apply_font(self):
        try:
            from modules.qt.font_manager_qt import get_current_font
            font = get_current_font()
            self._lbl.setFont(font)
            self._btn_yes.setFont(font)
            self._btn_no.setFont(font)
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
