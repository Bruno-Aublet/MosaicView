"""
modules/qt/library_dialogs.py — Dialogues auxiliaires de la bibliothèque
"""

import os

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QFrame,
)
from PySide6.QtCore import Qt, QTimer

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_font
from modules.qt.language_signal import language_signal


def _btn_ss(theme):
    alt      = theme.get("toolbar_bg", theme["bg"])
    sep      = theme.get("separator", "#aaaaaa")
    fg       = theme["text"]
    disabled = theme.get("disabled", "#aaaaaa")
    return (
        f"QPushButton {{ background: {alt}; color: {fg}; "
        f"border: 1px solid {sep}; padding: 5px 14px; border-radius: 3px; }} "
        f"QPushButton:hover {{ background: {sep}; }} "
        f"QPushButton:disabled {{ color: {disabled}; }}"
    )


def _input_ss(theme):
    bg  = theme.get("entry_bg", theme["bg"])
    fg  = theme["text"]
    sep = theme.get("separator", "#aaaaaa")
    return f"QLineEdit {{ background: {bg}; color: {fg}; border: 1px solid {sep}; padding: 4px 6px; }}"


def _apply_dialog_theme(dlg, theme):
    bg  = theme["bg"]
    fg  = theme["text"]
    dlg.setStyleSheet(
        f"QDialog {{ background: {bg}; color: {fg}; }} "
        f"QLabel  {{ color: {fg}; }}"
    )


def _center_on(dlg, parent):
    if parent is None:
        return
    from modules.qt.dialogs_qt import _center_on_widget
    _center_on_widget(dlg, parent)


# ── Dialogue Nouvelle base de données ─────────────────────────────────────────

class NewDbDialog(QDialog):
    def __init__(self, parent=None, preset_dir: str = ''):
        super().__init__(parent)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.result_name     = ''
        self.result_dir      = ''
        self.result_save_dir = ''
        self._preset_dir     = preset_dir
        self._first_show = True

        self._build_ui()
        if self._preset_dir:
            self._dir_edit.setText(self._preset_dir)
        self._retranslate()
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(lambda: language_signal.changed.disconnect(self._lang_handler))

    def showEvent(self, event):
        super().showEvent(event)
        if self._first_show:
            self._first_show = False
            QTimer.singleShot(0, lambda: _center_on(self, self.parent()))

    def _build_ui(self):
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        self._name_lbl  = QLabel()
        self._name_edit = QLineEdit()
        layout.addWidget(self._name_lbl)
        layout.addWidget(self._name_edit)

        self._dir_lbl  = QLabel()
        layout.addWidget(self._dir_lbl)
        dir_row = QHBoxLayout()
        self._dir_edit   = QLineEdit()
        self._dir_browse = QPushButton()
        self._dir_browse.setFixedWidth(100)
        dir_row.addWidget(self._dir_edit, 1)
        dir_row.addWidget(self._dir_browse)
        layout.addLayout(dir_row)

        self._save_lbl  = QLabel()
        layout.addWidget(self._save_lbl)
        save_row = QHBoxLayout()
        self._save_edit   = QLineEdit()
        self._save_browse = QPushButton()
        self._save_browse.setFixedWidth(100)
        save_row.addWidget(self._save_edit, 1)
        save_row.addWidget(self._save_browse)
        layout.addLayout(save_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok_btn     = QPushButton()
        self._cancel_btn = QPushButton()
        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._ok_btn)
        layout.addLayout(btn_row)

        self._dir_browse.clicked.connect(self._browse_master)
        self._save_browse.clicked.connect(self._browse_save)
        self._ok_btn.clicked.connect(self._on_ok)
        self._cancel_btn.clicked.connect(self.reject)
        self._name_edit.textChanged.connect(self._update_ok_state)
        self._dir_edit.textChanged.connect(self._update_ok_state)
        self._save_edit.textChanged.connect(self._update_ok_state)
        self._update_ok_state()

    def _update_ok_state(self):
        ok = (bool(self._name_edit.text().strip())
              and bool(self._dir_edit.text().strip())
              and bool(self._save_edit.text().strip()))
        self._ok_btn.setEnabled(ok)

    def _browse_master(self):
        folder = QFileDialog.getExistingDirectory(
            self, _('library.db_new_dir_title')
        )
        if folder:
            self._dir_edit.setText(folder)

    def _browse_save(self):
        folder = QFileDialog.getExistingDirectory(
            self, _('library.db_new_save_title')
        )
        if folder:
            self._save_edit.setText(folder)

    def _on_ok(self):
        name      = self._name_edit.text().strip()
        master    = self._dir_edit.text().strip()
        save_dir  = self._save_edit.text().strip()
        theme     = get_current_theme()
        if not name:
            self._name_edit.setStyleSheet(_input_ss(theme) + " border: 1px solid #cc0000;")
            return
        if not master or not os.path.isdir(master):
            self._dir_edit.setStyleSheet(_input_ss(theme) + " border: 1px solid #cc0000;")
            return
        if not save_dir or not os.path.isdir(save_dir):
            self._save_edit.setStyleSheet(_input_ss(theme) + " border: 1px solid #cc0000;")
            return
        self.result_name     = name
        self.result_dir      = master
        self.result_save_dir = save_dir
        self.accept()

    def _retranslate(self):
        theme = get_current_theme()
        font  = _get_font(9)
        _apply_dialog_theme(self, theme)
        self.setWindowTitle(_wt('library.db_new_title'))
        self._name_lbl.setText(_('library.db_new_name_label'))
        self._dir_lbl.setText(_('library.db_new_dir_label'))
        self._dir_browse.setText(_('library.db_new_dir_browse'))
        self._save_lbl.setText(_('library.db_new_save_label'))
        self._save_browse.setText(_('library.db_new_dir_browse'))
        self._ok_btn.setText('OK')
        self._cancel_btn.setText(_('buttons.cancel'))
        btn_s = _btn_ss(theme)
        inp_s = _input_ss(theme)
        for w in (self._ok_btn, self._cancel_btn, self._dir_browse, self._save_browse):
            w.setStyleSheet(btn_s)
            w.setFont(font)
        for w in (self._name_edit, self._dir_edit, self._save_edit):
            w.setStyleSheet(inp_s)
            w.setFont(font)
        for lbl in (self._name_lbl, self._dir_lbl, self._save_lbl):
            lbl.setFont(font)


# ── Dialogue Renommer la base de données ──────────────────────────────────────

class RenameDbDialog(QDialog):
    def __init__(self, current_name: str, parent=None):
        super().__init__(parent)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.result_name = ''
        self._current = current_name
        self._first_show = True

        self._build_ui()
        self._name_edit.setText(current_name)
        self._retranslate()
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(lambda: language_signal.changed.disconnect(self._lang_handler))

    def showEvent(self, event):
        super().showEvent(event)
        if self._first_show:
            self._first_show = False
            QTimer.singleShot(0, lambda: _center_on(self, self.parent()))

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        self._lbl      = QLabel()
        self._name_edit = QLineEdit()
        layout.addWidget(self._lbl)
        layout.addWidget(self._name_edit)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok_btn     = QPushButton()
        self._cancel_btn = QPushButton()
        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._ok_btn)
        layout.addLayout(btn_row)

        self._ok_btn.clicked.connect(self._on_ok)
        self._cancel_btn.clicked.connect(self.reject)

    def _on_ok(self):
        name = self._name_edit.text().strip()
        if not name:
            theme = get_current_theme()
            self._name_edit.setStyleSheet(_input_ss(theme) + " border: 1px solid #cc0000;")
            return
        self.result_name = name
        self.accept()

    def _retranslate(self):
        theme = get_current_theme()
        font  = _get_font(9)
        _apply_dialog_theme(self, theme)
        self.setWindowTitle(_wt('library.db_rename_title'))
        self._lbl.setText(_('library.db_rename_label'))
        self._ok_btn.setText('OK')
        self._cancel_btn.setText(_('buttons.cancel'))
        btn_s = _btn_ss(theme)
        inp_s = _input_ss(theme)
        for w in (self._ok_btn, self._cancel_btn):
            w.setStyleSheet(btn_s)
            w.setFont(font)
        self._name_edit.setStyleSheet(inp_s)
        self._name_edit.setFont(font)
        self._lbl.setFont(font)


# ── Dialogue Confirmer suppression ────────────────────────────────────────────

class ConfirmDeleteDialog(QDialog):
    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self._db_path = db_path
        self._first_show = True

        self._build_ui()
        self._retranslate()
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(lambda: language_signal.changed.disconnect(self._lang_handler))

    def showEvent(self, event):
        super().showEvent(event)
        if self._first_show:
            self._first_show = False
            QTimer.singleShot(0, lambda: _center_on(self, self.parent()))

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(20, 20, 20, 20)

        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg_lbl)

        self._path_lbl = QLabel()
        self._path_lbl.setWordWrap(True)
        self._path_lbl.setAlignment(Qt.AlignCenter)
        self._path_lbl.setOpenExternalLinks(False)
        self._path_lbl.linkActivated.connect(self._open_in_explorer)
        layout.addWidget(self._path_lbl)

        self._irrev_lbl = QLabel()
        self._irrev_lbl.setAlignment(Qt.AlignCenter)
        layout.addSpacing(4)
        layout.addWidget(self._irrev_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._cancel_btn  = QPushButton()
        self._confirm_btn = QPushButton()
        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._confirm_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._confirm_btn.clicked.connect(self.accept)
        self._cancel_btn.clicked.connect(self.reject)

    def _open_in_explorer(self, _url):
        import subprocess
        from modules.qt.library_window import _explorer_select
        path = self._db_path.replace('/', '\\')
        _explorer_select(path)

    def _retranslate(self):
        theme = get_current_theme()
        font  = _get_font(9)
        _apply_dialog_theme(self, theme)
        self.setWindowTitle(_wt('library.db_delete_title'))
        self._msg_lbl.setText(_('library.db_delete_message'))
        link_color = theme.get('link', '#4a9eff')
        path_escaped = self._db_path.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        self._path_lbl.setText(f'<a href="file" style="color:{link_color};">{path_escaped}</a>')
        self._irrev_lbl.setText(_('library.db_delete_irreversible'))
        self._confirm_btn.setText(_('library.db_delete_confirm'))
        self._cancel_btn.setText(_('buttons.cancel'))

        btn_s = _btn_ss(theme)
        self._cancel_btn.setStyleSheet(btn_s)
        self._cancel_btn.setFont(font)
        self._confirm_btn.setStyleSheet(
            f"QPushButton {{ background: #cc2200; color: #ffffff; "
            f"border: 1px solid #991100; padding: 5px 14px; border-radius: 3px; }} "
            f"QPushButton:hover {{ background: #991100; }}"
        )
        self._confirm_btn.setFont(font)
        self._msg_lbl.setFont(font)
        self._path_lbl.setFont(font)
        self._irrev_lbl.setFont(font)
