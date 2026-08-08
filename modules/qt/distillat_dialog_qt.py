# distillat_dialog_qt.py — Fenêtre de présentation de Distillat pour MosaicView Qt

import os

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from modules.qt.localization import _, _wt
from modules.qt.font_loader import resource_path
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.dialogs_qt import position_dialog_on_parent, _center_on_widget
from modules.qt.utils import open_url, setup_link_label_context_menu

_DISTILLAT_URL = "https://github.com/Bruno-Aublet/Distillat/releases/latest"
_DISTILLAT_LANDING_URL = "https://bruno-aublet.github.io/Distillat/"


def show_distillat_dialog_qt(parent):
    """Ouvre la fenêtre de présentation de Distillat ("Publicité éhontée")."""
    dlg = _DistillatDialog(parent)
    dlg.show_nonmodal()
    return dlg


class _DistillatDialog(QDialog):

    def __init__(self, parent):
        super().__init__(parent)
        self.resize(480, 470)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self._center_parent = parent

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 20)
        layout.setSpacing(16)

        icon_path = resource_path(os.path.join("icons", "Distillat.png"))
        if os.path.exists(icon_path):
            self._icon_lbl = QLabel()
            self._icon_lbl.setAlignment(Qt.AlignCenter)
            pix = QPixmap(icon_path).scaled(
                96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self._icon_lbl.setPixmap(pix)
            layout.addWidget(self._icon_lbl)
        else:
            self._icon_lbl = None

        self._title_lbl = QLabel()
        self._title_lbl.setAlignment(Qt.AlignCenter)
        self._title_lbl.setWordWrap(True)
        layout.addWidget(self._title_lbl)

        self._body_lbl = QLabel()
        self._body_lbl.setAlignment(Qt.AlignCenter)
        self._body_lbl.setWordWrap(True)
        self._body_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        from modules.qt.utils import setup_selectable_label_context_menu
        setup_selectable_label_context_menu(self._body_lbl)
        layout.addWidget(self._body_lbl)

        self._landing_lbl = QLabel()
        self._landing_lbl.setAlignment(Qt.AlignCenter)
        self._landing_lbl.setWordWrap(True)
        layout.addWidget(self._landing_lbl)

        self._landing_link_lbl = QLabel()
        self._landing_link_lbl.setAlignment(Qt.AlignCenter)
        self._landing_link_lbl.setOpenExternalLinks(False)
        self._landing_link_lbl.linkActivated.connect(open_url)
        setup_link_label_context_menu(self._landing_link_lbl, lambda: _DISTILLAT_LANDING_URL)
        layout.addWidget(self._landing_link_lbl)

        self._download_lbl = QLabel()
        self._download_lbl.setAlignment(Qt.AlignCenter)
        self._download_lbl.setWordWrap(True)
        layout.addWidget(self._download_lbl)

        self._link_lbl = QLabel()
        self._link_lbl.setAlignment(Qt.AlignCenter)
        self._link_lbl.setOpenExternalLinks(False)
        self._link_lbl.linkActivated.connect(open_url)
        setup_link_label_context_menu(self._link_lbl, lambda: _DISTILLAT_URL)
        layout.addWidget(self._link_lbl)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        self._btn_close = QPushButton()
        self._btn_close.setCursor(Qt.PointingHandCursor)
        self._btn_close.clicked.connect(self.accept)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_close)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _l: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def show_nonmodal(self):
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def _retranslate(self):
        theme = get_current_theme()
        font_title = _get_current_font(15, bold=True)
        font_body = _get_current_font(10)
        font_link = _get_current_font(10, bold=True)
        link_color = theme.get("link", "#0066cc")
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 6px 16px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )

        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        self.setWindowTitle(_wt("dialogs.distillat.window_title"))

        self._title_lbl.setText(_("dialogs.distillat.title"))
        self._title_lbl.setFont(font_title)
        self._title_lbl.setStyleSheet(f"color: {theme['text']}; background: transparent;")

        self._body_lbl.setText(_("dialogs.distillat.body"))
        self._body_lbl.setFont(font_body)
        self._body_lbl.setStyleSheet(f"color: {theme['text']}; background: transparent;")

        self._landing_lbl.setText(_("dialogs.distillat.landing_intro"))
        self._landing_lbl.setFont(font_body)
        self._landing_lbl.setStyleSheet(f"color: {theme['text']}; background: transparent;")

        self._landing_link_lbl.setText(
            f'<a href="{_DISTILLAT_LANDING_URL}" style="color:{link_color};">{_DISTILLAT_LANDING_URL}</a>'
        )
        self._landing_link_lbl.setFont(font_link)
        self._landing_link_lbl.setStyleSheet("background: transparent;")

        self._download_lbl.setText(_("dialogs.distillat.download_intro"))
        self._download_lbl.setFont(font_body)
        self._download_lbl.setStyleSheet(f"color: {theme['text']}; background: transparent;")

        self._link_lbl.setText(f'<a href="{_DISTILLAT_URL}" style="color:{link_color};">{_DISTILLAT_URL}</a>')
        self._link_lbl.setFont(font_link)
        self._link_lbl.setStyleSheet("background: transparent;")

        self._btn_close.setText(_("buttons.close"))
        self._btn_close.setFont(font_body)
        self._btn_close.setStyleSheet(btn_style)

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass
