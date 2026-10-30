# donation_dialog_qt.py — Fenêtre de donation PayPal pour MosaicView

import os
import webbrowser

from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QCursor

from modules.qt.app_window_qt import AppWindowMixin
from modules.qt.localization import _
from modules.qt.font_loader import resource_path
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font

_PAYPAL_URL = "https://www.paypal.com/donate/?hosted_button_id=SRSSMSSTEVTJY"


def show_donation_dialog_qt(parent):
    """Ouvre la fenêtre de donation (équivalent Qt de show_donation_dialog)."""
    dlg = _DonationDialog(parent)
    dlg.show_nonmodal()
    return dlg


class _DonationDialog(AppWindowMixin, QDialog):

    def __init__(self, parent):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("donation.title")
        self.resize(550, 300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        # Texte du message
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setWordWrap(True)
        layout.addWidget(self._label)

        # Bouton PayPal (icône ou fallback texte)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)

        self._paypal_btn = QPushButton()
        self._paypal_btn.setCursor(Qt.PointingHandCursor)
        self._paypal_btn.setFlat(True)
        self._paypal_btn.clicked.connect(lambda: webbrowser.open(_PAYPAL_URL))

        paypal_icon_path = resource_path(os.path.join("paypal", "paypal.png"))
        if os.path.exists(paypal_icon_path):
            pix = QPixmap(paypal_icon_path).scaled(
                64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self._paypal_btn.setIcon(pix)
            from PySide6.QtCore import QSize
            self._paypal_btn.setIconSize(QSize(64, 64))
            self._paypal_btn.setFixedSize(74, 74)
            self._has_icon = True
        else:
            self._paypal_btn.setText("PayPal")
            self._has_icon = False

        btn_row.addStretch()
        btn_row.addWidget(self._paypal_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._connect_language()

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(11)

        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )
        self._label.setText(_("donation.message"))
        self._label.setFont(font)
        self._label.setStyleSheet(f"color: {theme['text']}; background: transparent;")

        if not self._has_icon:
            self._paypal_btn.setFont(font)
            self._paypal_btn.setStyleSheet(
                f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
                f"border: 1px solid #aaaaaa; padding: 6px 16px; }} "
                f"QPushButton:hover {{ background: {theme['separator']}; }}"
            )
        else:
            self._paypal_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; }} "
                f"QPushButton:hover {{ background: {theme['separator']}; border-radius: 6px; }}"
            )
