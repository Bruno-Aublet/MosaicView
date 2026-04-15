"""
batch_drop_dialog_qt.py — Fenêtre de sélection du traitement batch pour dossiers droppés.
Reproduit exactement modules/batch_drop_dialog.py (tkinter) pour PySide6.
Règles UI Qt : thème, langue à la volée, police courante.
"""

import os

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QButtonGroup, QRadioButton, QFrame,
)
from PySide6.QtCore import Qt

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font


def _radio_label(key):
    return _("buttons." + key).replace("\n", " ").replace("  ", " ").strip()


def _connect_lang(dialog, handler):
    from modules.qt.language_signal import language_signal
    dialog._lang_handler = handler
    dialog._lang_connected = True
    language_signal.changed.connect(dialog._lang_handler)
    dialog.finished.connect(lambda: _disconnect_lang(dialog))


def _disconnect_lang(dialog):
    if not getattr(dialog, '_lang_connected', False):
        return
    dialog._lang_connected = False
    from modules.qt.language_signal import language_signal
    try:
        language_signal.changed.disconnect(dialog._lang_handler)
    except RuntimeError:
        pass


class BatchDropDialog(QDialog):
    """
    Fenêtre de sélection du traitement batch pour des dossiers droppés.
    Identique à modules/batch_drop_dialog.py (tkinter).
    Supporte : thème courant, changement de langue à la volée, police courante.
    """

    def __init__(self, parent, dirs: list[str]):
        super().__init__(parent)
        self._dirs  = dirs
        self._count = len(dirs)
        self.chosen = None   # 'cbr' | 'cb7' | 'cbt' | 'pdf' | 'img' | None (annuler)

        self.setModal(False)
        self.setFixedSize(480, 280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(4)

        # Label intro
        self._lbl_intro = QLabel()
        self._lbl_intro.setWordWrap(True)
        self._lbl_intro.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_intro)

        layout.addSpacing(4)

        # Radio buttons
        self._btn_group = QButtonGroup(self)

        self._radio_cbr = QRadioButton()
        self._radio_cb7 = QRadioButton()
        self._radio_cbt = QRadioButton()
        self._radio_pdf = QRadioButton()
        self._radio_img = QRadioButton()

        self._btn_group.addButton(self._radio_cbr, 0)
        self._btn_group.addButton(self._radio_cb7, 1)
        self._btn_group.addButton(self._radio_cbt, 2)
        self._btn_group.addButton(self._radio_pdf, 3)
        self._btn_group.addButton(self._radio_img, 4)
        self._radio_cbr.setChecked(True)

        for rb in (self._radio_cbr, self._radio_cb7, self._radio_cbt, self._radio_pdf, self._radio_img):
            layout.addWidget(rb)

        layout.addSpacing(4)

        # Note récursivité
        self._lbl_note = QLabel()
        self._lbl_note.setWordWrap(True)
        self._lbl_note.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_note)

        layout.addSpacing(4)

        # Séparateur
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep)

        layout.addSpacing(4)

        # Boutons OK / Annuler
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_ok     = QPushButton()
        self._btn_ok.setFixedWidth(110)
        self._btn_ok.setDefault(True)
        self._btn_ok.clicked.connect(self._on_ok)
        self._btn_cancel = QPushButton()
        self._btn_cancel.setFixedWidth(110)
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._btn_ok)
        btn_row.addSpacing(16)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.rejected.connect(self._on_cancel)
        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._center_parent = parent

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    # ── Traduction / thème / police ───────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        bg    = theme["bg"]
        fg    = theme["text"]
        tb_bg = theme["toolbar_bg"]
        sep   = theme["separator"]

        self.setStyleSheet(
            f"QDialog {{ background: {bg}; color: {fg}; }}"
            f"QRadioButton {{ background: {bg}; color: {fg}; }}"
            f"QFrame[frameShape='4'] {{ color: {sep}; }}"  # HLine = 4
        )

        font10 = _get_current_font(10)
        font11 = _get_current_font(11)
        font9  = _get_current_font(9)
        btn_style = (
            f"QPushButton {{ background: {tb_bg}; color: {fg}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {sep}; }}"
        )

        self.setWindowTitle(_wt("dialogs.batch_drop.window_title"))

        if self._count == 1:
            intro = _("dialogs.batch_drop.intro_single")
        else:
            intro = _("dialogs.batch_drop.intro_multiple").format(count=self._count)
        self._lbl_intro.setText(intro)
        self._lbl_intro.setFont(font11)

        self._radio_cbr.setText(_radio_label("batch_cbr_to_cbz"))
        self._radio_cbr.setFont(font10)
        self._radio_cb7.setText(_radio_label("batch_cb7_to_cbz"))
        self._radio_cb7.setFont(font10)
        self._radio_cbt.setText(_radio_label("batch_cbt_to_cbz"))
        self._radio_cbt.setFont(font10)
        self._radio_pdf.setText(_radio_label("batch_pdf_to_cbz"))
        self._radio_pdf.setFont(font10)
        self._radio_img.setText(_radio_label("batch_img_to_cbz"))
        self._radio_img.setFont(font10)

        self._lbl_note.setText(_("dialogs.batch_drop.recursive_note"))
        self._lbl_note.setFont(font9)

        self._btn_ok.setText(_("buttons.ok"))
        self._btn_ok.setFont(font10)
        self._btn_ok.setStyleSheet(btn_style)
        self._btn_cancel.setText(_("buttons.cancel"))
        self._btn_cancel.setFont(font10)
        self._btn_cancel.setStyleSheet(btn_style)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _on_ok(self):
        checked = self._btn_group.checkedId()
        self.chosen = ("cbr", "cb7", "cbt", "pdf", "img")[checked]
        self.accept()

    def _on_cancel(self):
        self.chosen = None
        self.accept()


# ═══════════════════════════════════════════════════════════════════════════════
# Fonction publique
# ═══════════════════════════════════════════════════════════════════════════════

def show_batch_drop_dialog(parent, dirs: list[str], callbacks: dict):
    """
    Affiche la fenêtre de sélection batch pour des dossiers droppés.

    dirs      : liste de chemins de dossiers
    callbacks : dict avec 'batch_cbr', 'batch_pdf', 'batch_img' (callables sans argument)
    """
    dlg = BatchDropDialog(parent, dirs)

    def _on_done():
        if dlg.chosen:
            fn = callbacks.get("batch_" + dlg.chosen)
            if fn:
                fn()

    dlg.finished.connect(lambda _: _on_done())
    dlg.show()
