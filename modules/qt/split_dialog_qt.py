"""
modules/qt/split_dialog_qt.py — Scinder une page (version PySide6)

Reproduit à l'identique le comportement de modules/split_dialog.py (tkinter).
"""

import io
import os

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QButtonGroup, QRadioButton,
)
from PySide6.QtCore import Qt

from modules.qt import state as _state_module
from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.entries import ensure_image_loaded, free_image_memory, create_entry
from modules.qt.dialogs_qt import MsgDialog


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


class SplitDialog(QDialog):
    """Fenêtre de découpe d'image en N parties égales (horizontale ou verticale)."""

    def __init__(self, parent, callbacks):
        super().__init__(parent)
        self._callbacks = callbacks
        self.setModal(True)
        self.setFixedSize(420, 280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(6)

        # Titre
        self._title_lbl = QLabel()
        self._title_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._title_lbl)

        layout.addSpacing(4)

        # Ligne : nombre de pages
        num_row = QHBoxLayout()
        num_row.setSpacing(8)
        self._num_lbl = QLabel()
        num_row.addWidget(self._num_lbl)
        self._spinbox = QSpinBox()
        self._spinbox.setRange(2, 10)
        self._spinbox.setValue(2)
        self._spinbox.setFixedWidth(70)
        num_row.addWidget(self._spinbox)
        num_row.addStretch()
        layout.addLayout(num_row)

        layout.addSpacing(4)

        # Direction
        self._dir_lbl = QLabel()
        layout.addWidget(self._dir_lbl)

        self._btn_group = QButtonGroup(self)
        self._radio_h = QRadioButton()
        self._radio_v = QRadioButton()
        self._radio_v.setChecked(True)
        self._btn_group.addButton(self._radio_h)
        self._btn_group.addButton(self._radio_v)
        layout.addWidget(self._radio_h)
        layout.addWidget(self._radio_v)

        layout.addSpacing(4)

        # Avertissement
        self._warn_lbl = QLabel()
        self._warn_lbl.setAlignment(Qt.AlignCenter)
        self._warn_lbl.setWordWrap(True)
        layout.addWidget(self._warn_lbl)

        layout.addStretch()

        # Boutons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok_btn = QPushButton()
        self._ok_btn.setFixedWidth(100)
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(self._ok_btn)
        self._cancel_btn = QPushButton()
        self._cancel_btn.setFixedWidth(100)
        self._cancel_btn.clicked.connect(self.reject)
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
            f"border: 1px solid #aaaaaa; }}"
        )
        radio_style = (
            f"QRadioButton {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )
        warn_color = "#666666" if not (self._callbacks.get('state') or _state_module.state).dark_mode else "#999999"

        self.setWindowTitle(_wt("dialogs.split.window_title"))

        self._title_lbl.setText(_("dialogs.split.title"))
        self._title_lbl.setFont(_get_current_font(14, bold=True))

        self._num_lbl.setText(_("dialogs.split.pages_label"))
        self._num_lbl.setFont(font)

        self._spinbox.setFont(font)
        self._spinbox.setStyleSheet(spin_style)

        self._dir_lbl.setText(_("dialogs.split.direction_label"))
        self._dir_lbl.setFont(font)

        self._radio_h.setText(_("dialogs.split.horizontal"))
        self._radio_h.setFont(font)
        self._radio_h.setStyleSheet(radio_style)

        self._radio_v.setText(_("dialogs.split.vertical"))
        self._radio_v.setFont(font)
        self._radio_v.setStyleSheet(radio_style)

        self._warn_lbl.setText(_("dialogs.split.warning"))
        warn_font = _get_current_font(9)
        warn_font.setItalic(True)
        self._warn_lbl.setFont(warn_font)
        self._warn_lbl.setStyleSheet(f"color: {warn_color};")

        self._ok_btn.setText(_("dialogs.split.button_split"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)

        self._cancel_btn.setText(_("dialogs.split.button_cancel"))
        self._cancel_btn.setFont(font)
        self._cancel_btn.setStyleSheet(btn_style)

    def _on_ok(self):
        num_pages = self._spinbox.value()
        if num_pages < 2 or num_pages > 10:
            dlg = MsgDialog(
                self,
                "messages.warnings.invalid_number_split.title",
                "messages.warnings.invalid_number_split.message",
            )
            dlg.exec()
            return
        self._num_pages = num_pages
        self._direction = "horizontal" if self._radio_h.isChecked() else "vertical"
        self.accept()

    @property
    def num_pages(self):
        return getattr(self, "_num_pages", 2)

    @property
    def direction(self):
        return getattr(self, "_direction", "vertical")


def split_page(parent, callbacks):
    """Ouvre la fenêtre de découpe puis exécute la découpe si confirmée.

    Args:
        parent   : QWidget parent (fenêtre principale)
        callbacks: dict avec 'render_mosaic', 'update_button_text', 'save_state'
    """
    state = callbacks.get('state') or _state_module.state

    # Vérification : exactement une image sélectionnée
    if not state.selected_indices:
        dlg = MsgDialog(
            parent,
            "messages.warnings.no_selection_split.title",
            "messages.warnings.no_selection_split.message",
        )
        dlg.exec()
        return

    if len(state.selected_indices) > 1:
        dlg = MsgDialog(
            parent,
            "messages.warnings.multi_selection_split.title",
            "messages.warnings.multi_selection_split.message",
        )
        dlg.exec()
        return

    idx = list(state.selected_indices)[0]
    entry = state.images_data[idx]

    if not entry["is_image"]:
        dlg = MsgDialog(
            parent,
            "messages.warnings.invalid_selection_split.title",
            "messages.warnings.invalid_selection_split.message",
        )
        dlg.exec()
        return

    # Lazy loading
    img = ensure_image_loaded(entry)
    if not img:
        dlg = MsgDialog(
            parent,
            "messages.warnings.invalid_selection_split.title",
            "messages.warnings.invalid_selection_split.message",
        )
        dlg.exec()
        return

    # Ouvre le dialogue de paramètres
    dialog = SplitDialog(parent, callbacks)
    if dialog.exec() != QDialog.Accepted:
        return

    num_pages = dialog.num_pages
    direction = dialog.direction

    # Sauvegarde l'état pour undo
    callbacks["save_state"]()

    # Dimensions de l'image
    width, height = img.size

    # Nom de base + extension
    orig_name = entry["orig_name"]
    base_name, ext = os.path.splitext(orig_name)

    output_format = ext.upper()[1:]  # Retire le point
    if output_format == "JPG":
        output_format = "JPEG"

    image_exts = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff", ".tif"}
    new_entries = []

    if direction == "horizontal":
        split_height = height / num_pages
        for i in range(num_pages):
            top    = int(i * split_height)
            bottom = int((i + 1) * split_height)
            cropped_img = img.crop((0, top, width, bottom))
            new_name = f"{base_name}_part{i+1:02d}{ext}"
            img_bytes = io.BytesIO()
            if output_format in ["JPEG", "WEBP"]:
                if cropped_img.mode in ('RGBA', 'LA', 'P'):
                    cropped_img = cropped_img.convert('RGB')
                cropped_img.save(img_bytes, format=output_format, quality=100, subsampling=0)
            else:
                cropped_img.save(img_bytes, format=output_format)
            new_entries.append(create_entry(new_name, img_bytes.getvalue(), image_exts))

    else:  # vertical
        split_width = width / num_pages
        for i in range(num_pages):
            left  = int(i * split_width)
            right = int((i + 1) * split_width)
            cropped_img = img.crop((left, 0, right, height))
            new_name = f"{base_name}_part{i+1:02d}{ext}"
            img_bytes = io.BytesIO()
            if output_format in ["JPEG", "WEBP"]:
                if cropped_img.mode in ('RGBA', 'LA', 'P'):
                    cropped_img = cropped_img.convert('RGB')
                cropped_img.save(img_bytes, format=output_format, quality=100, subsampling=0)
            else:
                cropped_img.save(img_bytes, format=output_format)
            new_entries.append(create_entry(new_name, img_bytes.getvalue(), image_exts))

    # Insère les nouvelles entrées juste après l'image d'origine
    for i, new_entry in enumerate(new_entries):
        state.images_data.insert(idx + 1 + i, new_entry)

    # Libère la mémoire
    free_image_memory(entry)

    # Archive modifiée
    state.modified = True
    from modules.qt.comic_info import sync_pages_in_xml_data
    sync_pages_in_xml_data(state)

    # Rafraîchit l'affichage
    callbacks["render_mosaic"]()
    callbacks["update_button_text"]()
