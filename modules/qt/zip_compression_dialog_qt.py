"""
modules/qt/zip_compression_dialog_qt.py — Réglage du niveau de compression ZIP par défaut.

Fenêtre non modale, pattern identique à split_dialog_qt.py.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox,
)
from PySide6.QtCore import Qt, Signal

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font


def _connect_lang(dialog, handler):
    from modules.qt.language_signal import language_signal
    dialog._lang_handler = handler
    language_signal.changed.connect(dialog._lang_handler)
    dialog.finished.connect(lambda: _disconnect_lang(dialog))


def _disconnect_lang(dialog):
    from modules.qt.language_signal import language_signal
    try:
        language_signal.changed.disconnect(dialog._lang_handler)
    except RuntimeError:
        pass


class ZipCompressionDialog(QDialog):
    """Fenêtre de réglage du niveau de compression ZIP par défaut (0-9). NON modale."""

    result_signal = Signal(bool)   # True = OK, False = annulation

    def __init__(self, parent, config):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self._config = config
        self._emitted = False
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setFixedWidth(440)
        self.setSizeGripEnabled(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(6)
        layout.setSizeConstraint(QVBoxLayout.SetFixedSize)

        self._title_lbl = QLabel()
        self._title_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._title_lbl)

        layout.addSpacing(4)

        self._explain_lbl = QLabel()
        self._explain_lbl.setWordWrap(True)
        self._explain_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._explain_lbl)

        layout.addSpacing(4)

        level_row = QHBoxLayout()
        level_row.setSpacing(8)
        level_row.addStretch()
        self._level_lbl = QLabel()
        level_row.addWidget(self._level_lbl)
        self._spinbox = QSpinBox()
        self._spinbox.setRange(0, 9)
        self._spinbox.setValue(self._config.get_zip_compression_level())
        self._spinbox.setFixedWidth(70)
        self._spinbox.setAlignment(Qt.AlignCenter)
        level_row.addWidget(self._spinbox)
        level_row.addStretch()
        layout.addLayout(level_row)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok_btn = QPushButton()
        self._ok_btn.setFixedWidth(100)
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(self._ok_btn)
        self._cancel_btn = QPushButton()
        self._cancel_btn.setFixedWidth(100)
        self._cancel_btn.clicked.connect(lambda: self._finish(False))
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._ok_btn.setFocus()
        self._center_parent = parent

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )
        font = _get_current_font(11)

        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        spin_style = (
            f"QSpinBox {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 2px 4px; }} "
            f"QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}"
        )

        self.setWindowTitle(_wt("dialogs.zip_compression.window_title"))

        self._title_lbl.setText(_("dialogs.zip_compression.title"))
        self._title_lbl.setFont(_get_current_font(12, bold=True))

        self._explain_lbl.setText(_("dialogs.zip_compression.explanation"))
        self._explain_lbl.setFont(font)

        self._level_lbl.setText(_("dialogs.zip_compression.level_label"))
        self._level_lbl.setFont(font)

        self._spinbox.setFont(font)
        self._spinbox.setStyleSheet(spin_style)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)

        self._cancel_btn.setText(_("buttons.cancel"))
        self._cancel_btn.setFont(font)
        self._cancel_btn.setStyleSheet(btn_style)

    def _on_ok(self):
        self._config.set_zip_compression_level(self._spinbox.value())
        self._finish(True)

    def _finish(self, result: bool):
        if self._emitted:
            return
        self._emitted = True
        _disconnect_lang(self)
        self.result_signal.emit(result)
        self.hide()
        self.deleteLater()

    def closeEvent(self, event):
        if not self._emitted:
            self._emitted = True
            _disconnect_lang(self)
            self.result_signal.emit(False)
        event.accept()

    def ask_async(self, on_result=None):
        """Affiche (NON modal) et appelle on_result(bool) à la réponse, si fourni."""
        from modules.qt.dialogs_qt import position_dialog_on_parent
        if on_result:
            self.result_signal.connect(on_result)
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()


def show_zip_compression_dialog(parent, config, on_result=None):
    """Ouvre la fenêtre de réglage du niveau de compression ZIP par défaut."""
    dialog = ZipCompressionDialog(parent, config)
    dialog.ask_async(on_result)
    return dialog
