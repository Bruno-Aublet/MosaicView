"""
modules/qt/nfo_dialog_qt.py — Fenêtre de création / édition de fichier NFO.

Un fichier NFO est un fichier texte avec l'extension .nfo.
La fenêtre est non-modale.

Mode création : inject_fn(filename, content) ajoute l'entrée dans la mosaïque.
Mode édition  : edit_fn(new_content) met à jour entry["bytes"] dans la mosaïque.

Points d'entrée publics :
    show_nfo_dialog(parent, inject_fn, state)
    show_nfo_edit_dialog(parent, entry, edit_fn)
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QTextEdit, QMenu,
)
from PySide6.QtCore import Qt, QTimer

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font


# ── Points d'entrée publics ───────────────────────────────────────────────────

def show_nfo_dialog(parent, inject_fn, state) -> None:
    """Ouvre la fenêtre en mode création (non-modale).

    inject_fn(filename: str, content: str) — appelé à la validation.
    state — AppState du panneau, pour vérifier les doublons.
    """
    dlg = _NfoDialog(parent, inject_fn=inject_fn, state=state)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


def show_nfo_edit_dialog(parent, entry: dict, edit_fn) -> None:
    """Ouvre la fenêtre en mode édition (non-modale).

    entry — entrée existante dans images_data (orig_name + bytes).
    edit_fn(new_content: str) — appelé à la validation avec le nouveau contenu.
    """
    dlg = _NfoDialog(parent, entry=entry, edit_fn=edit_fn)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


# ── Helpers styles ────────────────────────────────────────────────────────────

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

class _NfoDialog(QDialog):

    def __init__(self, parent, inject_fn=None, state=None, entry=None, edit_fn=None):
        super().__init__(parent)
        # Mode création : inject_fn + state
        # Mode édition  : entry + edit_fn
        self._inject_fn     = inject_fn
        self._state         = state
        self._entry         = entry       # None en mode création
        self._edit_fn       = edit_fn     # None en mode création
        self._edit_mode     = entry is not None
        self._center_parent = parent

        self.setModal(False)
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
        filename_row.addWidget(self._edit_filename, stretch=1)

        self._lbl_ext = QLabel(".nfo")
        filename_row.addWidget(self._lbl_ext)

        layout.addLayout(filename_row)

        # ── Pré-remplissage en mode édition ────────────────────────────────────
        if self._edit_mode:
            orig = self._entry.get("orig_name", "")
            # Affiche le nom sans l'extension dans le champ (l'ext est à droite)
            import os as _os
            base = _os.path.splitext(_os.path.basename(orig))[0]
            self._edit_filename.setText(base)
            self._edit_filename.setReadOnly(True)

        # ── Label contenu ──────────────────────────────────────────────────────
        self._lbl_content = QLabel()
        layout.addWidget(self._lbl_content)

        # ── Zone de texte ──────────────────────────────────────────────────────
        self._text_edit = QTextEdit()
        self._text_edit.setAcceptRichText(False)
        self._setup_text_context_menu()
        layout.addWidget(self._text_edit, stretch=1)

        # ── Pré-remplissage du contenu en mode édition ─────────────────────────
        if self._edit_mode:
            raw = self._entry.get("bytes", b"")
            try:
                self._text_edit.setPlainText(raw.decode("utf-8"))
            except Exception:
                self._text_edit.setPlainText(raw.decode("latin-1", errors="replace"))

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

        # ── Langue + thème ─────────────────────────────────────────────────────
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

    # ── Traduction + thème ─────────────────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(10)

        title_key = "nfo.window_title_edit" if self._edit_mode else "nfo.window_title"
        self.setWindowTitle(_wt(title_key))
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )

        self._lbl_filename.setText(_("nfo.filename_label"))
        self._lbl_filename.setFont(font)
        self._lbl_filename.setStyleSheet(_label_style(theme))

        self._edit_filename.setFont(font)
        if self._edit_mode:
            # Champ non éditable : fond légèrement différent pour l'indiquer visuellement
            self._edit_filename.setStyleSheet(
                f"QLineEdit {{ background: {theme['bg']}; color: {theme['text']}; "
                f"border: 1px solid #aaaaaa; padding: 2px 6px; }}"
            )
        else:
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

    # ── Menu contextuel clic droit sur QTextEdit ──────────────────────────────

    def _setup_text_context_menu(self):
        self._text_edit.setContextMenuPolicy(Qt.CustomContextMenu)
        self._text_edit.customContextMenuRequested.connect(self._show_text_menu)

    def _show_text_menu(self, pos):
        font = _get_current_font(9)
        menu = QMenu(self._text_edit)
        menu.setFont(font)
        menu.setStyleSheet(
            f'QMenu {{ font-family: "{font.family()}"; font-size: {font.pointSize()}pt; }}'
        )

        cursor = self._text_edit.textCursor()
        has_sel = cursor.hasSelection()

        act_copy = menu.addAction(_("buttons.copy"))
        act_copy.setEnabled(has_sel)
        act_copy.triggered.connect(self._text_edit.copy)

        act_cut = menu.addAction(_("buttons.cut"))
        act_cut.setEnabled(has_sel)
        act_cut.triggered.connect(self._text_edit.cut)

        act_paste = menu.addAction(_("buttons.paste"))
        act_paste.setEnabled(self._text_edit.canPaste())
        act_paste.triggered.connect(self._text_edit.paste)

        menu.addSeparator()

        act_select_all = menu.addAction(_("menu.select_all"))
        act_select_all.setEnabled(bool(self._text_edit.toPlainText()))
        act_select_all.triggered.connect(self._text_edit.selectAll)

        menu.exec(self._text_edit.mapToGlobal(pos))

    # ── Actions ────────────────────────────────────────────────────────────────

    def _on_clear(self):
        self._edit_filename.clear()
        self._text_edit.clear()
        self._edit_filename.setFocus()

    def _on_create(self):
        content = self._text_edit.toPlainText()

        if self._edit_mode:
            # Mode édition : met à jour le contenu de l'entrée existante
            self._edit_fn(content)
            self.close()
            return

        # Mode création
        filename = self._edit_filename.text().strip()
        if not filename:
            from modules.qt.dialogs_qt import ErrorDialog
            ErrorDialog(
                self,
                _("nfo.error_title"),
                _("nfo.error_empty_name"),
            ).exec()
            self._edit_filename.setFocus()
            return

        # Assure l'extension .nfo
        if not filename.lower().endswith(".nfo"):
            filename += ".nfo"

        # Vérifie les doublons dans la mosaïque
        if any(e.get("orig_name", "").lower() == filename.lower()
               for e in self._state.images_data):
            from modules.qt.dialogs_qt import ErrorDialog
            ErrorDialog(
                self,
                _("nfo.error_title"),
                _("nfo.error_duplicate").format(filename=filename),
            ).exec()
            self._edit_filename.setFocus()
            return

        self._inject_fn(filename, content)
        self.close()

    # ── Nettoyage ─────────────────────────────────────────────────────────────

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass
