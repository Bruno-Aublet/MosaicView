"""
modules/qt/split_dialog_qt.py — Scinder une page.
"""

import io
import os

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QButtonGroup, QRadioButton,
)
from PySide6.QtCore import Qt, Signal

from modules.qt import state as _state_module
from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.entries import ensure_image_loaded, free_image_memory, create_entry, save_image_to_bytes
from modules.qt.archive_loader import IMAGE_EXTS
from modules.qt.image_ops import transform_animated_gif
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
    """Fenêtre de découpe d'image en N parties égales (horizontale ou verticale). NON modale."""

    result_signal = Signal(bool)   # True = OK (num_pages/direction valides), False = annulation

    def __init__(self, parent, callbacks):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self._callbacks = callbacks
        self._emitted = False
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
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
            dlg.show_nonmodal()
            return
        self._num_pages = num_pages
        self._direction = "horizontal" if self._radio_h.isChecked() else "vertical"
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

    def ask_async(self, on_result):
        """Affiche (NON modal) et appelle on_result(bool) à la réponse."""
        from modules.qt.dialogs_qt import position_dialog_on_parent
        self.result_signal.connect(on_result)
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

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
        dlg.show_nonmodal()
        return

    if len(state.selected_indices) > 1:
        dlg = MsgDialog(
            parent,
            "messages.warnings.multi_selection_split.title",
            "messages.warnings.multi_selection_split.message",
        )
        dlg.show_nonmodal()
        return

    idx = list(state.selected_indices)[0]
    entry = state.images_data[idx]

    if not entry["is_image"]:
        dlg = MsgDialog(
            parent,
            "messages.warnings.invalid_selection_split.title",
            "messages.warnings.invalid_selection_split.message",
        )
        dlg.show_nonmodal()
        return

    # Lazy loading
    img = ensure_image_loaded(entry)
    if not img:
        dlg = MsgDialog(
            parent,
            "messages.warnings.invalid_selection_split.title",
            "messages.warnings.invalid_selection_split.message",
        )
        dlg.show_nonmodal()
        return

    # Ouvre le dialogue de paramètres (NON modal)
    dialog = SplitDialog(parent, callbacks)

    def _on_confirmed(ok):
        if not ok:
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

        ext_lower = ext.lower()

        def _encode_part(part):
            """Bytes d'un morceau, au format de la page d'origine. Le format
            ne se déduit jamais du nom de l'extension (".tif" -> "TIF",
            ".jfif" -> "JFIF" ne sont pas des formats Pillow)."""
            if ext_lower in (".jpg", ".jpeg", ".jfif", ".pjpeg", ".pjp"):
                if part.mode not in ('RGB', 'L', 'CMYK'):
                    part = part.convert('RGB')
                buf = io.BytesIO()
                part.save(buf, format="JPEG", quality=100, subsampling=0)
                return buf.getvalue()
            if ext_lower == ".webp":
                buf = io.BytesIO()
                part.save(buf, format="WEBP", quality=100)
                return buf.getvalue()
            return save_image_to_bytes({"img": part, "extension": ext,
                                        "bytes": entry.get("bytes"), "dpi": entry.get("dpi")})

        def _part_bytes(box):
            # GIF animé : chaque morceau garde toutes les frames, sinon il ne
            # contiendrait que la première image.
            if entry.get("is_animated_gif"):
                return transform_animated_gif({"bytes": entry["bytes"]}, lambda f: f.crop(box))
            return _encode_part(img.crop(box))

        new_entries = []

        if direction == "horizontal":
            split_height = height / num_pages
            for i in range(num_pages):
                top    = int(i * split_height)
                bottom = int((i + 1) * split_height)
                new_name = f"{base_name}_part{i+1:02d}{ext}"
                new_entries.append(create_entry(new_name, _part_bytes((0, top, width, bottom)), IMAGE_EXTS))

        else:  # vertical
            split_width = width / num_pages
            for i in range(num_pages):
                left  = int(i * split_width)
                right = int((i + 1) * split_width)
                new_name = f"{base_name}_part{i+1:02d}{ext}"
                new_entries.append(create_entry(new_name, _part_bytes((left, 0, right, height)), IMAGE_EXTS))

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

    dialog.ask_async(_on_confirmed)
