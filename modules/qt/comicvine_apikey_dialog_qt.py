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
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(14)

        self._explanation = QLabel()
        self._explanation.setWordWrap(True)
        self._explanation.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._explanation)

        self._instructions = QLabel()
        self._instructions.setWordWrap(True)
        self._instructions.setOpenExternalLinks(True)
        self._instructions.setAlignment(Qt.AlignCenter)
        from modules.qt.utils import setup_link_label_context_menu
        setup_link_label_context_menu(self._instructions, lambda: [
            ("ComicVine", _CV_URL),
            (_("comicvine.api_key_dialog.api_page_label"), _CV_API_URL),
        ])
        layout.addWidget(self._instructions)

        field_row = QHBoxLayout()
        field_row.setContentsMargins(0, 4, 0, 0)
        self._field_label = QLabel()
        field_row.addWidget(self._field_label)
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.Password)
        from modules.qt.utils import setup_lineedit_context_menu
        setup_lineedit_context_menu(self._key_input, allow_copy_cut=False)
        field_row.addWidget(self._key_input)
        self._btn_toggle_visibility = QPushButton()
        self._btn_toggle_visibility.clicked.connect(self._on_toggle_visibility)
        field_row.addWidget(self._btn_toggle_visibility)
        layout.addLayout(field_row)

        self._encryption_notice = QLabel()
        self._encryption_notice.setWordWrap(True)
        self._encryption_notice.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._encryption_notice)

        self._error_label = QLabel()
        self._error_label.setAlignment(Qt.AlignCenter)
        self._error_label.hide()
        layout.addWidget(self._error_label)

        layout.addStretch()

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

        existing_key = config_manager.get_comicvine_api_key()
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
        if not event.spontaneous():
            self.adjustSize()
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

        link_color  = theme.get("link", "#4A9EFF")
        cv_link     = f'<a href="{_CV_URL}" style="color:{link_color};">ComicVine</a>'
        api_link    = f'<a href="{_CV_API_URL}" style="color:{link_color};">{_("comicvine.api_key_dialog.api_page_label")}</a>'
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

        is_hidden = self._key_input.echoMode() == QLineEdit.Password
        toggle_key = "comicvine.api_key_dialog.show_key" if is_hidden else "comicvine.api_key_dialog.hide_key"
        self._btn_toggle_visibility.setText(_(toggle_key))
        self._btn_toggle_visibility.setFont(font)
        self._btn_toggle_visibility.setStyleSheet(btn_style)

        self._encryption_notice.setText(_("comicvine.api_key_dialog.encryption_notice"))
        self._encryption_notice.setFont(font_small)
        self._encryption_notice.setStyleSheet(f"color: {theme.get('disabled', '#aaaaaa')}; background: transparent;")

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

    def _on_toggle_visibility(self):
        if self._key_input.echoMode() == QLineEdit.Password:
            self._key_input.setEchoMode(QLineEdit.Normal)
        else:
            self._key_input.setEchoMode(QLineEdit.Password)
        self._retranslate()

    def _update_clear_btn(self):
        self._btn_clear.setEnabled(bool(self._key_input.text().strip()))

    def _on_clear(self):
        self._key_input.clear()
        self._config.set_comicvine_api_key('')

    def _on_validate(self):
        key = self._key_input.text().strip()
        if not key:
            self._error_label.setText(_("comicvine.api_key_dialog.error_empty"))
            self._error_label.show()
            return
        self._config.set_comicvine_api_key(key)
        self.result_key = key
        self.accept()

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass
