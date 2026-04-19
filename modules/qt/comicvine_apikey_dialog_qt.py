# comicvine_apikey_dialog_qt.py — Fenêtre de saisie de la clé API ComicVine

import webbrowser

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton
)
from PySide6.QtCore import Qt, QTimer

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font

_CV_URL     = "https://comicvine.gamespot.com/"
_CV_API_URL = "https://comicvine.gamespot.com/api/"


def show_apikey_dialog(parent, config_manager):
    """
    Ouvre la fenêtre de saisie de la clé API ComicVine.
    Retourne la clé saisie (str) si l'utilisateur valide, ou None s'il annule.
    """
    dlg = _ApiKeyDialog(parent, config_manager)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


class _ApiKeyDialog(QDialog):

    def __init__(self, parent, config_manager):
        super().__init__(parent)
        self._config = config_manager
        self.result_key = None

        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.resize(500, 230)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(14)

        # Explication
        self._explanation = QLabel()
        self._explanation.setWordWrap(True)
        self._explanation.setAlignment(Qt.AlignLeft)
        layout.addWidget(self._explanation)

        # Instructions avec liens cliquables
        self._instructions = QLabel()
        self._instructions.setWordWrap(True)
        self._instructions.setOpenExternalLinks(True)
        self._instructions.setAlignment(Qt.AlignLeft)
        layout.addWidget(self._instructions)

        # Champ de saisie
        field_row = QHBoxLayout()
        field_row.setContentsMargins(0, 4, 0, 0)
        self._field_label = QLabel()
        field_row.addWidget(self._field_label)
        self._key_input = QLineEdit()
        self._key_input.setContextMenuPolicy(Qt.DefaultContextMenu)
        field_row.addWidget(self._key_input)
        layout.addLayout(field_row)

        # Message d'erreur (caché par défaut)
        self._error_label = QLabel()
        self._error_label.setAlignment(Qt.AlignLeft)
        self._error_label.hide()
        layout.addWidget(self._error_label)

        layout.addStretch()

        # Boutons
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.addStretch()
        self._btn_clear = QPushButton()
        self._btn_clear.clicked.connect(self._on_clear)
        btn_row.addWidget(self._btn_clear)
        self._btn_cancel = QPushButton()
        self._btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_cancel)
        self._btn_validate = QPushButton()
        self._btn_validate.setDefault(True)
        self._btn_validate.clicked.connect(self._on_validate)
        btn_row.addWidget(self._btn_validate)
        layout.addLayout(btn_row)

        existing_key = config_manager.get('comicvine_api_key', '').strip()
        if existing_key:
            self._key_input.setText(existing_key)
        self._key_input.textChanged.connect(self._update_clear_btn)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
        self._center_parent = parent

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _retranslate(self):
        theme = get_current_theme()
        font  = _get_current_font(10)
        font_small = _get_current_font(9)

        self.setWindowTitle(_wt("comicvine.api_key_dialog.title"))
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )

        label_style  = f"color: {theme['text']}; background: transparent;"
        link_style   = (
            f"color: {theme['text']}; background: transparent; "
            f"qproperty-openExternalLinks: true;"
        )
        error_style  = "color: #cc3333; background: transparent;"
        input_style  = (
            f"QLineEdit {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid {theme.get('separator', '#aaaaaa')}; padding: 4px 8px; }}"
        )
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid {theme.get('separator', '#aaaaaa')}; padding: 5px 18px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )

        self._explanation.setText(_("comicvine.api_key_dialog.explanation"))
        self._explanation.setFont(font)
        self._explanation.setStyleSheet(label_style)

        cv_link     = f'<a href="{_CV_URL}" style="color:{theme["text"]};">ComicVine</a>'
        api_link    = f'<a href="{_CV_API_URL}" style="color:{theme["text"]};">{_("comicvine.api_key_dialog.api_page_label")}</a>'
        instructions_tpl = _("comicvine.api_key_dialog.instructions")
        self._instructions.setText(instructions_tpl.format(comicvine=cv_link, api_page=api_link))
        self._instructions.setFont(font)
        self._instructions.setStyleSheet(link_style)

        self._field_label.setText(_("comicvine.api_key_dialog.field_label"))
        self._field_label.setFont(font)
        self._field_label.setStyleSheet(label_style)

        self._key_input.setPlaceholderText(_("comicvine.api_key_dialog.placeholder"))
        self._key_input.setFont(font)
        self._key_input.setStyleSheet(input_style)

        self._error_label.setFont(font_small)
        self._error_label.setStyleSheet(error_style)

        self._btn_clear.setText(_("comicvine.api_key_dialog.clear"))
        self._btn_clear.setFont(font)
        self._btn_clear.setStyleSheet(btn_style)
        self._update_clear_btn()

        self._btn_cancel.setText(_("buttons.cancel"))
        self._btn_cancel.setFont(font)
        self._btn_cancel.setStyleSheet(btn_style)

        self._btn_validate.setText(_("comicvine.api_key_dialog.validate"))
        self._btn_validate.setFont(font)
        self._btn_validate.setStyleSheet(btn_style)

    def _update_clear_btn(self):
        self._btn_clear.setEnabled(bool(self._key_input.text().strip()))

    def _on_clear(self):
        self._key_input.clear()
        self._config.set('comicvine_api_key', '')

    def _on_validate(self):
        key = self._key_input.text().strip()
        if not key:
            self._error_label.setText(_("comicvine.api_key_dialog.error_empty"))
            self._error_label.show()
            return
        self._config.set('comicvine_api_key', key)
        self.result_key = key
        self.accept()

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass
