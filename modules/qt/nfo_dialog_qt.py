"""
modules/qt/nfo_dialog_qt.py — Fenêtre de création / édition de fichier NFO.

Un fichier NFO est un fichier texte avec l'extension .nfo.
La fenêtre est non-modale.

Mode création : inject_fn(filename, content) ajoute l'entrée dans la mosaïque.
Mode édition  : edit_fn(new_filename, new_content) met à jour l'entrée dans la mosaïque.

Points d'entrée publics :
    show_nfo_dialog(parent, inject_fn, state)
    show_nfo_edit_dialog(parent, entry, edit_fn, state)
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QTextEdit,
)
from PySide6.QtCore import Qt, QEvent

from modules.qt.app_window_qt import AppWindowMixin
from modules.qt.archive_names import windows_text
from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.utils import (setup_lineedit_context_menu, setup_textedit_context_menu,
                              windows_safe_component)


# ── Points d'entrée publics ───────────────────────────────────────────────────

def show_nfo_dialog(parent, inject_fn, state) -> None:
    """Ouvre la fenêtre en mode création (non-modale).

    inject_fn(filename: str, content: str) — appelé à la validation.
    state — AppState du panneau, pour vérifier les doublons.
    """
    _NfoDialog(parent, inject_fn=inject_fn, state=state).show_nonmodal()


def show_nfo_edit_dialog(parent, entry: dict, edit_fn, state) -> None:
    """Ouvre la fenêtre en mode édition (non-modale).

    entry    — entrée existante dans images_data (orig_name + bytes).
    edit_fn(new_filename: str, new_content: str) — appelé à la validation.
    state    — AppState du panneau, pour vérifier les doublons.
    """
    _NfoDialog(parent, entry=entry, edit_fn=edit_fn, state=state).show_nonmodal()


# ── Helpers styles ────────────────────────────────────────────────────────────

def _annex_text(raw):
    """Texte d'un fichier annexe : UTF-8, sinon codage Windows. La validation le
    réécrit en UTF-8 : un caractère mal lu ici serait perdu dans le fichier."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return windows_text(raw)


def _name_error_key(filename):
    """Clé du message qui refuse `filename` comme nom de fichier, None s'il
    est valable. Le nom devient celui d'un fichier écrit sur le disque
    (export, enregistrement) : « / » ou « \\ » en ferait un dossier, et
    Windows refuse ou détourne les caractères interdits et les noms réservés
    (même règle que utils.windows_safe_component, qui corrige au lieu de
    refuser)."""
    if "/" in filename or "\\" in filename:
        return "dialogs.macro_name_error.invalid_chars"
    safe = windows_safe_component(filename)
    if safe == filename:
        return None
    if safe == "_" + filename:
        return "dialogs.macro_name_error.reserved_name"
    return "dialogs.macro_name_error.invalid_chars"


def _btn_style(theme):
    return (
        f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
        f"border: 1px solid #aaaaaa; padding: 4px 10px; }} "
        f"QPushButton:hover {{ background: {theme['separator']}; }} "
        f"QPushButton:disabled {{ color: #888888; }}"
    )


def _input_style(theme):
    return (
        f"QLineEdit {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
        f"border: 1px solid #aaaaaa; padding: 2px 6px; }}"
    )


def _textedit_style(theme):
    return (
        f"QTextEdit {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
        f"border: 1px solid #aaaaaa; padding: 4px; }}"
    )


def _label_style(theme):
    return f"color: {theme['text']};"


# ── Fenêtre principale ────────────────────────────────────────────────────────

class _NfoDialog(AppWindowMixin, QDialog):

    def __init__(self, parent, inject_fn=None, state=None, entry=None, edit_fn=None):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("nfo.window_title_edit" if entry is not None else "nfo.window_title")
        # Mode création : inject_fn + state
        # Mode édition  : entry + edit_fn
        self._inject_fn     = inject_fn
        self._state         = state
        self._entry         = entry       # None en mode création
        self._edit_fn       = edit_fn     # None en mode création
        self._edit_mode     = entry is not None

        self.setWindowFlags(self.windowFlags() | Qt.Window)
        self.resize(520, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # ── Ligne : nom du fichier ─────────────────────────────────────────────
        filename_row = QHBoxLayout()
        filename_row.setSpacing(6)

        self._lbl_filename = QLabel()
        filename_row.addWidget(self._lbl_filename)

        self._edit_filename = QLineEdit()
        self._edit_filename.setMinimumWidth(200)
        setup_lineedit_context_menu(self._edit_filename)
        filename_row.addWidget(self._edit_filename, stretch=1)

        self._lbl_ext = QLabel(".nfo")
        filename_row.addWidget(self._lbl_ext)

        layout.addLayout(filename_row)

        # ── Pré-remplissage en mode édition ────────────────────────────────────
        if self._edit_mode:
            orig = self._entry.get("orig_name", "")
            import os as _os
            base = _os.path.splitext(_os.path.basename(orig))[0]
            self._edit_filename.setText(base)

        # ── Label contenu ──────────────────────────────────────────────────────
        self._lbl_content = QLabel()
        layout.addWidget(self._lbl_content)

        # ── Zone de texte ──────────────────────────────────────────────────────
        self._text_edit = QTextEdit()
        self._text_edit.setAcceptRichText(False)
        setup_textedit_context_menu(self._text_edit)
        self._text_edit.installEventFilter(self)
        layout.addWidget(self._text_edit, stretch=1)

        # ── Pré-remplissage du contenu en mode édition ─────────────────────────
        if self._edit_mode:
            self._text_edit.setPlainText(_annex_text(self._entry.get("bytes", b"")))
        # Contenu de départ, tel que relu par les champs : « Enregistrer »
        # reste grisé tant qu'il n'a pas changé (un enregistrement sans
        # modification marquerait le document modifié et réécrirait le
        # fichier en UTF-8)
        self._initial = (self._edit_filename.text().strip(), self._text_edit.toPlainText())

        # ── Boutons ────────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._btn_create = QPushButton()
        self._btn_create.setDefault(True)
        self._btn_create.clicked.connect(self._on_create)

        self._btn_clear = QPushButton()
        self._btn_clear.clicked.connect(self._on_clear)

        btn_row.addStretch()
        btn_row.addWidget(self._btn_create)
        btn_row.addWidget(self._btn_clear)
        btn_row.addStretch()

        layout.addLayout(btn_row)

        if self._edit_mode:
            self._edit_filename.textChanged.connect(self._update_save_button)
            self._text_edit.textChanged.connect(self._update_save_button)
            self._update_save_button()

        # ── Langue + thème ─────────────────────────────────────────────────────
        self._connect_language()

    def _update_save_button(self):
        current = (self._edit_filename.text().strip(), self._text_edit.toPlainText())
        self._btn_create.setEnabled(current != self._initial)

    def eventFilter(self, obj, event):
        # Home/End dans la zone de contenu : aller au tout début/toute fin du
        # texte (comme Ctrl+Home/Ctrl+End) plutôt que début/fin de ligne.
        if obj is self._text_edit and event.type() == QEvent.KeyPress \
                and event.key() in (Qt.Key_Home, Qt.Key_End):
            from PySide6.QtGui import QTextCursor
            cursor = self._text_edit.textCursor()
            moveMode = QTextCursor.KeepAnchor if event.modifiers() & Qt.ShiftModifier else QTextCursor.MoveAnchor
            position = QTextCursor.Start if event.key() == Qt.Key_Home else QTextCursor.End
            cursor.movePosition(position, moveMode)
            self._text_edit.setTextCursor(cursor)
            return True
        return super().eventFilter(obj, event)

    # ── Traduction + thème ─────────────────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(10)

        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )

        self._lbl_filename.setText(_("nfo.filename_label"))
        self._lbl_filename.setFont(font)
        self._lbl_filename.setStyleSheet(_label_style(theme))

        self._edit_filename.setFont(font)
        self._edit_filename.setStyleSheet(_input_style(theme))

        self._lbl_ext.setText(".nfo")
        self._lbl_ext.setFont(font)
        self._lbl_ext.setStyleSheet(_label_style(theme))

        self._lbl_content.setText(_("nfo.content_label"))
        self._lbl_content.setFont(font)
        self._lbl_content.setStyleSheet(_label_style(theme))

        self._text_edit.setFont(font)
        self._text_edit.setStyleSheet(_textedit_style(theme))

        btn_style = _btn_style(theme)
        create_key = "nfo.btn_save" if self._edit_mode else "nfo.btn_create"
        self._btn_create.setText(_(create_key))
        self._btn_create.setFont(font)
        self._btn_create.setStyleSheet(btn_style)

        self._btn_clear.setText(_("nfo.btn_clear"))
        self._btn_clear.setFont(font)
        self._btn_clear.setStyleSheet(btn_style)

    # ── Actions ────────────────────────────────────────────────────────────────

    def _on_clear(self):
        self._edit_filename.clear()
        self._text_edit.clear()
        self._edit_filename.setFocus()

    def _name_taken(self, filename):
        """Un fichier du document porte déjà ce nom (sans tenir compte de la
        casse). En édition, l'entrée éditée est exclue, reconnue par son jeton
        (undo_redo.entry_key) et non par identité : recréée par un Annuler
        pendant que la fenêtre était ouverte, c'est un autre objet, qui serait
        pris pour un doublon."""
        from modules.qt.undo_redo import entry_key
        edited_key = entry_key(self._entry) if self._edit_mode else None
        lowered = filename.lower()
        return any(e.get("orig_name", "").lower() == lowered
                   and not (self._edit_mode and entry_key(e) == edited_key)
                   for e in self._state.images_data)

    def _checked_filename(self):
        """Nom saisi, complété de « .nfo », s'il peut devenir celui d'un
        fichier du document ; sinon None, après avoir affiché la raison."""
        filename = self._edit_filename.text().strip()
        if not filename:
            message = lambda: _("nfo.error_empty_name")
        else:
            if not filename.lower().endswith(".nfo"):
                filename += ".nfo"
            key = _name_error_key(filename)
            if key is not None:
                message = lambda k=key: _(k)
            elif self._name_taken(filename):
                message = lambda fn=filename: _("nfo.error_duplicate").format(filename=fn)
            else:
                return filename
        from modules.qt.dialogs_qt import ErrorDialog
        ErrorDialog(self, "nfo.error_title", message).show_nonmodal()
        self._edit_filename.setFocus()
        return None

    def _on_create(self):
        filename = self._checked_filename()
        if filename is None:
            return
        content = self._text_edit.toPlainText()
        if self._edit_mode:
            self._edit_fn(filename, content)
        else:
            self._inject_fn(filename, content)
        self.close()
