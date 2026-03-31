"""
modules/qt/conversion_dialogs_qt.py — Conversion de format d'images (version PySide6)

Reproduit à l'identique le comportement de modules/conversion_dialogs.py (tkinter).
Toutes les fenêtres supportent :
  - le thème courant (clair/sombre)
  - le changement de langue à la volée via language_signal
  - la police courante via get_current_font

Fonctions publiques :
  convert_selected_images(parent, callbacks)
  show_quality_dialog(parent, target_format, selected_entries, callbacks)
  show_conversion_complete_dialog(parent, converted, target_format,
                                  selected_entries, converted_entries, callbacks)
"""

import threading

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QButtonGroup, QRadioButton,
)
from PySide6.QtCore import Qt, QThread, Signal

from modules.qt import state as _state_module
from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.canvas_overlay_qt import show_canvas_text as _show_canvas_text, hide_canvas_text as _hide_canvas_text
from modules.qt.utils import format_file_size, FocusSlider
from modules.qt.dialogs_qt import MsgDialog
from modules.qt.image_ops import convert_image_data
from modules.qt.entries import free_image_memory
from modules.qt.font_manager_qt import get_current_font as _get_current_font


# ─────────────────────────────────────────────────────────────────────────────
# Helpers langue
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Helpers style
# ─────────────────────────────────────────────────────────────────────────────

def _btn_style(theme):
    return (
        f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
        f"border: 1px solid #aaaaaa; padding: 4px 12px; }} "
        f"QPushButton:hover {{ background: {theme['separator']}; }}"
    )


def _radio_style(theme):
    return f"QRadioButton {{ background: {theme['bg']}; color: {theme['text']}; }}"


def _slider_style(theme):
    return (
        f"QSlider::groove:horizontal {{ background: {theme['toolbar_bg']}; height: 6px; border-radius: 3px; }} "
        f"QSlider::handle:horizontal {{ background: {theme['text']}; width: 14px; height: 14px; "
        f"margin: -4px 0; border-radius: 7px; }} "
        f"QSlider::sub-page:horizontal {{ background: {theme['separator']}; border-radius: 3px; }}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# Dialogue de fin de conversion
# ═════════════════════════════════════════════════════════════════════════════

class _ConversionCompleteDialog(QDialog):
    """
    Affiché après la conversion : 3 boutons (supprimer originaux /
    annuler conversion / garder tout).
    """

    def __init__(self, parent, converted, target_format,
                 selected_entries, converted_entries):
        super().__init__(parent)
        self._converted = converted
        self._target_format = target_format
        self._selected_entries = selected_entries
        self._converted_entries = converted_entries

        # Calcul des poids
        self._orig_size  = sum(len(e.get("bytes", b"")) for e in selected_entries)
        self._conv_size  = sum(len(e.get("bytes", b"")) for e in converted_entries)
        self._total_size = self._orig_size + self._conv_size
        self._orig_count  = len(selected_entries)
        self._conv_count  = len(converted_entries)
        self._total_count = self._orig_count + self._conv_count

        self.action = None  # None / "delete_orig" / "delete_conv"

        self.setModal(True)
        self.setFixedWidth(620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 12)
        layout.setSpacing(12)

        # Message
        self._msg_lbl = QLabel()
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        self._msg_lbl.setWordWrap(True)
        layout.addWidget(self._msg_lbl)

        # Boutons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._delete_btn      = QPushButton()
        self._cancel_conv_btn = QPushButton()
        self._keep_btn        = QPushButton()

        self._delete_btn.clicked.connect(self._on_delete)
        self._cancel_conv_btn.clicked.connect(self._on_cancel_conversion)
        self._keep_btn.clicked.connect(self._on_keep)

        btn_row.addWidget(self._delete_btn)
        btn_row.addWidget(self._cancel_conv_btn)
        btn_row.addWidget(self._keep_btn)
        layout.addLayout(btn_row)

        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._delete_btn.setFocus()

    # ── actions ──────────────────────────────────────────────────────────────

    def _on_delete(self):
        self.action = "delete_orig"
        self.accept()

    def _on_cancel_conversion(self):
        self.action = "delete_conv"
        self.accept()

    def _on_keep(self):
        self.action = None
        self.accept()

    # ── retranslate / restyle ─────────────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )
        font = _get_current_font(10)
        style = _btn_style(theme)

        self.setWindowTitle(_wt("messages.questions.conversion_complete.title"))

        self._msg_lbl.setText(
            _("messages.questions.conversion_complete.message",
              count=self._converted, format=self._target_format)
        )
        self._msg_lbl.setFont(font)

        self._delete_btn.setText(
            _("messages.questions.conversion_complete.delete_originals",
              count=self._orig_count, size=format_file_size(self._orig_size))
        )
        self._delete_btn.setFont(font)
        self._delete_btn.setStyleSheet(style)

        self._cancel_conv_btn.setText(
            _("messages.questions.conversion_complete.cancel_conversion",
              count=self._conv_count, size=format_file_size(self._conv_size))
        )
        self._cancel_conv_btn.setFont(font)
        self._cancel_conv_btn.setStyleSheet(style)

        self._keep_btn.setText(
            _("messages.questions.conversion_complete.keep_all",
              count=self._total_count, size=format_file_size(self._total_size))
        )
        self._keep_btn.setFont(font)
        self._keep_btn.setStyleSheet(style)


def show_conversion_complete_dialog(parent, converted, target_format,
                                    selected_entries, converted_entries, callbacks):
    """Affiche le dialogue de fin de conversion avec option de supprimer les originaux."""
    state = callbacks.get('state') or _state_module.state

    dialog = _ConversionCompleteDialog(
        parent, converted, target_format, selected_entries, converted_entries
    )
    dialog.exec()

    if dialog.action == "delete_orig":
        for idx in sorted(state.selected_indices, reverse=True):
            if idx < len(state.images_data):
                state.images_data.pop(idx)
        state.selected_indices.clear()
        from modules.qt.comic_info import sync_pages_in_xml_data
        sync_pages_in_xml_data(state)
        callbacks['render_mosaic']()
        callbacks['update_button_text']()
        callbacks['save_state']()

    elif dialog.action == "delete_conv":
        conv_ids = {id(e) for e in converted_entries}
        state.images_data[:] = [e for e in state.images_data if id(e) not in conv_ids]
        from modules.qt.comic_info import sync_pages_in_xml_data
        sync_pages_in_xml_data(state)
        callbacks['render_mosaic']()
        callbacks['update_button_text']()
        callbacks['save_state']()


# ═════════════════════════════════════════════════════════════════════════════
# Dialogue de qualité (JPEG / WEBP)
# ═════════════════════════════════════════════════════════════════════════════

class _QualityDialog(QDialog):
    """Fenêtre de choix de qualité de compression pour JPEG et WEBP."""

    PRESETS = [95, 85, 75, 60]

    def __init__(self, parent, target_format, selected_entries, callbacks):
        super().__init__(parent)
        self._target_format    = target_format
        self._selected_entries = selected_entries
        self._callbacks        = callbacks

        self._quality_value = 95    # valeur courante
        self._use_custom    = False

        self.setModal(True)
        self.setFixedSize(620, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(6)

        # Titre
        self._title_lbl = QLabel()
        self._title_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._title_lbl)

        layout.addSpacing(4)

        # Radios presets
        self._btn_group = QButtonGroup(self)
        self._radios    = []
        preset_labels   = [
            "dialogs.convert.quality_maximum",
            "dialogs.convert.quality_high",
            "dialogs.convert.quality_medium",
            "dialogs.convert.quality_low",
        ]
        for i, (key, val) in enumerate(zip(preset_labels, self.PRESETS)):
            rb = QRadioButton()
            rb.setChecked(i == 0)
            rb._quality_val = val
            rb._key         = key
            self._btn_group.addButton(rb, i)
            self._radios.append(rb)
            layout.addWidget(rb)
            rb.toggled.connect(self._on_preset_toggled)

        layout.addSpacing(4)

        # Label slider
        self._slider_lbl = QLabel()
        self._slider_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._slider_lbl)

        # Slider
        self._slider = FocusSlider(Qt.Horizontal)
        self._slider.setRange(1, 100)
        self._slider.setValue(95)
        self._slider.setFixedWidth(400)
        layout.addWidget(self._slider, alignment=Qt.AlignCenter)
        self._slider.valueChanged.connect(self._on_slider_changed)

        # Note qualité
        self._note_lbl = QLabel()
        self._note_lbl.setAlignment(Qt.AlignCenter)
        self._note_lbl.setWordWrap(True)
        layout.addWidget(self._note_lbl)

        layout.addStretch()

        # Boutons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._convert_btn = QPushButton()
        self._convert_btn.setFixedWidth(120)
        self._convert_btn.setDefault(True)
        self._convert_btn.clicked.connect(self._on_convert)
        btn_row.addWidget(self._convert_btn)
        self._cancel_btn = QPushButton()
        self._cancel_btn.setFixedWidth(120)
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._convert_btn.setFocus()

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_preset_toggled(self, checked):
        if not checked:
            return
        for rb in self._radios:
            if rb.isChecked():
                self._quality_value = rb._quality_val
                self._use_custom    = False
                # Déplace le slider sans déclencher _on_slider_changed en boucle
                self._slider.blockSignals(True)
                self._slider.setValue(rb._quality_val)
                self._slider.blockSignals(False)
                self._slider_lbl.setText(
                    _("dialogs.convert.quality_custom", value=rb._quality_val)
                )
                break

    def _on_slider_changed(self, val):
        self._slider_lbl.setText(_("dialogs.convert.quality_custom", value=val))
        if val in self.PRESETS:
            idx = self.PRESETS.index(val)
            self._radios[idx].blockSignals(True)
            self._radios[idx].setChecked(True)
            self._radios[idx].blockSignals(False)
            self._quality_value = val
            self._use_custom    = False
        else:
            # Désélectionne tous les presets
            for rb in self._radios:
                rb.blockSignals(True)
                rb.setChecked(False)
                rb.blockSignals(False)
            self._btn_group.setExclusive(False)
            for rb in self._radios:
                rb.setChecked(False)
            self._btn_group.setExclusive(True)
            self._quality_value = val
            self._use_custom    = True

    def _on_convert(self):
        quality = self._quality_value
        self.accept()
        self._callbacks['perform_conversion'](
            self._target_format, quality, self._selected_entries
        )

    # ── retranslate / restyle ─────────────────────────────────────────────────

    def _retranslate(self):
        theme    = get_current_theme()
        state    = self._callbacks.get('state') or _state_module.state
        font_lrg = _get_current_font(14, bold=True)
        font     = _get_current_font(10)
        font_sm  = _get_current_font(9)
        note_color = "#666666" if not state.dark_mode else "#999999"

        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )
        self.setWindowTitle(_wt("dialogs.convert.quality_window_title"))

        self._title_lbl.setText(_("dialogs.convert.quality_label"))
        self._title_lbl.setFont(font_lrg)

        for rb in self._radios:
            rb.setText(_(rb._key))
            rb.setFont(font)
            rb.setStyleSheet(_radio_style(theme))

        self._slider_lbl.setText(
            _("dialogs.convert.quality_custom", value=self._slider.value())
        )
        self._slider_lbl.setFont(font)
        self._slider.setStyleSheet(_slider_style(theme))

        self._note_lbl.setText(_("dialogs.convert.quality_note"))
        self._note_lbl.setFont(font_sm)
        self._note_lbl.setStyleSheet(f"color: {note_color};")

        style = _btn_style(theme)
        self._convert_btn.setText(_("buttons.convert_button"))
        self._convert_btn.setFont(font)
        self._convert_btn.setStyleSheet(style)

        self._cancel_btn.setText(_("buttons.cancel"))
        self._cancel_btn.setFont(font)
        self._cancel_btn.setStyleSheet(style)


def show_quality_dialog(parent, target_format, selected_entries, callbacks):
    """Affiche la fenêtre de choix de qualité pour JPEG et WEBP, puis lance la conversion."""
    dialog = _QualityDialog(parent, target_format, selected_entries, callbacks)
    dialog.exec()


# ═════════════════════════════════════════════════════════════════════════════
# Dialogue de sélection de format
# ═════════════════════════════════════════════════════════════════════════════

class _ConvertFormatDialog(QDialog):
    """Fenêtre de choix du format cible pour la conversion d'images."""

    _FORMATS = [
        ("dialogs.convert.format_png",          "PNG"),
        ("dialogs.convert.format_jpg",          "JPEG"),
        ("dialogs.convert.format_webp",         "WEBP"),
        ("dialogs.convert.format_bmp",          "BMP"),
        ("dialogs.convert.format_tiff",         "TIFF"),
        ("dialogs.convert.format_gif_static",   "GIF_STATIC"),
        ("dialogs.convert.format_gif_animated", "GIF_ANIMATED"),
    ]

    def __init__(self, parent, selected_entries, callbacks):
        super().__init__(parent)
        self._selected_entries = selected_entries
        self._callbacks        = callbacks
        self._chosen_format    = "PNG"

        self.setModal(True)
        self.setFixedSize(620, 400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(6)

        # Titre
        self._title_lbl = QLabel()
        self._title_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._title_lbl)

        # Info
        self._info_lbl = QLabel()
        self._info_lbl.setWordWrap(True)
        self._info_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._info_lbl)

        layout.addSpacing(4)

        # Radios formats — centrés dans un widget dédié
        from PySide6.QtWidgets import QWidget as _QWidget
        radios_widget  = _QWidget()
        radios_layout  = QVBoxLayout(radios_widget)
        radios_layout.setContentsMargins(0, 0, 0, 0)
        radios_layout.setSpacing(4)

        self._btn_group = QButtonGroup(self)
        self._radios    = []
        single_sel = (len(selected_entries) == 1)
        for i, (key, val) in enumerate(self._FORMATS):
            rb = QRadioButton()
            rb.setChecked(i == 0)
            rb._format_val = val
            rb._key        = key
            # GIF animé inactif si une seule image sélectionnée
            if val == "GIF_ANIMATED" and single_sel:
                rb.setEnabled(False)
            self._btn_group.addButton(rb, i)
            self._radios.append(rb)
            radios_layout.addWidget(rb)

        layout.addWidget(radios_widget, alignment=Qt.AlignHCenter)
        layout.addStretch()

        # Boutons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._convert_btn = QPushButton()
        self._convert_btn.setFixedWidth(120)
        self._convert_btn.setDefault(True)
        self._convert_btn.clicked.connect(self._on_convert)
        btn_row.addWidget(self._convert_btn)
        self._cancel_btn = QPushButton()
        self._cancel_btn.setFixedWidth(120)
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._convert_btn.setFocus()

    # ── slot ──────────────────────────────────────────────────────────────────

    def _on_convert(self):
        for rb in self._radios:
            if rb.isChecked():
                self._chosen_format = rb._format_val
                break
        self.accept()

    # ── retranslate / restyle ─────────────────────────────────────────────────

    def _retranslate(self):
        theme    = get_current_theme()
        font_lrg = _get_current_font(14, bold=True)
        font     = _get_current_font(11)
        font_sm  = _get_current_font(9)

        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )
        self.setWindowTitle(_wt("dialogs.convert.window_title"))

        nb_files  = len(self._selected_entries)
        file_word = (
            _("dialogs.convert.word_image")
            if nb_files == 1
            else _("dialogs.convert.word_images")
        )
        self._title_lbl.setText(
            _("dialogs.convert.title", count=nb_files, word=file_word)
        )
        self._title_lbl.setFont(font_lrg)

        self._info_lbl.setText(_("dialogs.convert.info"))
        self._info_lbl.setFont(font_sm)

        disabled_color = theme.get("disabled", "#aaaaaa")
        for rb in self._radios:
            rb.setText(_(rb._key))
            rb.setFont(font)
            if rb.isEnabled():
                rb.setStyleSheet(_radio_style(theme))
            else:
                rb.setStyleSheet(
                    f"QRadioButton {{ background: {theme['bg']}; color: {disabled_color}; }}"
                )

        style = _btn_style(theme)
        self._convert_btn.setText(_("buttons.convert_button"))
        self._convert_btn.setFont(font)
        self._convert_btn.setStyleSheet(style)

        self._cancel_btn.setText(_("buttons.cancel"))
        self._cancel_btn.setFont(font)
        self._cancel_btn.setStyleSheet(style)


def convert_selected_images(parent, callbacks):
    """Ouvre la fenêtre de sélection de format puis lance la conversion."""
    state = callbacks.get('state') or _state_module.state

    if not state.selected_indices:
        dlg = MsgDialog(
            parent,
            "messages.warnings.no_selection_convert.title",
            "messages.warnings.no_selection_convert.message",
        )
        dlg.exec()
        return

    selected_entries = [
        state.images_data[idx]
        for idx in sorted(state.selected_indices)
        if idx < len(state.images_data)
    ]
    if not all(e["is_image"] for e in selected_entries):
        dlg = MsgDialog(
            parent,
            "messages.warnings.invalid_selection_convert.title",
            "messages.warnings.invalid_selection_convert.message",
        )
        dlg.exec()
        return

    dialog = _ConvertFormatDialog(parent, selected_entries, callbacks)
    if dialog.exec() != QDialog.Accepted:
        return

    target_format = dialog._chosen_format

    if target_format in ("JPEG", "WEBP"):
        show_quality_dialog(parent, target_format, selected_entries, callbacks)
    elif target_format == "GIF_ANIMATED":
        callbacks['show_animated_gif_dialog'](selected_entries)
    else:
        # PNG, BMP, TIFF, GIF_STATIC : conversion directe
        fmt = "GIF" if target_format == "GIF_STATIC" else target_format
        callbacks['perform_conversion'](fmt, 95, selected_entries)


# ─────────────────────────────────────────────────────────────────────────────
# Worker thread de conversion
# ─────────────────────────────────────────────────────────────────────────────

class _ConversionWorker(QThread):
    progress  = Signal(int)          # percent
    finished  = Signal(list, int)    # (converted_entries, converted_count)
    cancelled = Signal()

    def __init__(self, target_format, quality, selected_entries,
                 state, insert_after_idx, inserted_entries):
        super().__init__()
        self._target_format    = target_format
        self._quality          = quality
        self._selected_entries = selected_entries
        self._state            = state
        self._insert_after_idx = insert_after_idx
        self._inserted_entries = inserted_entries  # liste partagée avec perform_conversion
        self._cancelled        = threading.Event()

    def run(self):
        converted         = 0
        converted_entries = []
        total             = len(self._selected_entries)
        insert_idx        = self._insert_after_idx

        for i, entry in enumerate(self._selected_entries):
            if self._cancelled.is_set():
                self.cancelled.emit()
                return
            new_entry, _err = convert_image_data(entry, self._target_format, self._quality)
            if new_entry:
                new_entry["qt_pixmap_large"] = None
                free_image_memory(new_entry)
                insert_idx += 1
                self._state.images_data.insert(insert_idx, new_entry)
                self._inserted_entries.append(new_entry)
                converted_entries.append(new_entry)
                converted += 1
            self.progress.emit(int((i + 1) / total * 100))

        self.finished.emit(converted_entries, converted)


# ─────────────────────────────────────────────────────────────────────────────
# Logique de conversion
# ─────────────────────────────────────────────────────────────────────────────

def perform_conversion(parent, target_format, quality, selected_entries, callbacks):
    """Lance la conversion PIL dans un thread et affiche la barre de progression."""
    from modules.qt.web_import_qt import _show_cancel_item
    state = callbacks.get('state') or _state_module.state
    state.converting = True
    insert_after_idx = max(state.selected_indices) if state.selected_indices else len(state.images_data)
    callbacks['save_state']()

    canvas           = parent._canvas
    item_holder      = [None]
    cancel_holder    = [None]
    worker_ref       = [None]
    inserted_entries = []   # entrées déjà insérées dans state.images_data par le worker

    def _show(pct):
        if worker_ref[0] is None:
            return
        _show_canvas_text(canvas, _("labels.converting", percent=pct), item_holder)
        _show_cancel_item(canvas, f"[ {_('buttons.cancel')} ]", cancel_holder, _cancel)

    def _hide():
        _hide_canvas_text(canvas, item_holder)
        _hide_canvas_text(canvas, cancel_holder)

    def _cancel():
        w = worker_ref[0]
        if w is None:
            return
        w._cancelled.set()
        worker_ref[0] = None
        _hide()
        # Retire TOUTES les entrées déjà insérées par le worker
        inserted_ids = {id(e) for e in inserted_entries}
        state.images_data[:] = [e for e in state.images_data if id(e) not in inserted_ids]
        inserted_entries.clear()
        state.converting = False
        from modules.qt.comic_info import sync_pages_in_xml_data
        sync_pages_in_xml_data(state)
        callbacks['render_mosaic']()
        callbacks['update_button_text']()

    def on_progress(pct):
        _show(pct)

    def on_finished(converted_entries_signal, converted):
        worker_ref[0] = None
        _hide()
        state.converting = False
        state.converting_percent = 0
        state.modified = True
        from modules.qt.comic_info import sync_pages_in_xml_data
        sync_pages_in_xml_data(state)
        callbacks['save_state']()
        callbacks['render_mosaic']()
        callbacks['update_button_text']()
        if converted > 0:
            # Utiliser inserted_entries (liste partagée directement, sans passer par le signal Qt)
            # pour que les id() correspondent bien aux objets dans state.images_data
            show_conversion_complete_dialog(
                parent, converted, target_format, selected_entries, inserted_entries, {
                    "render_mosaic":      callbacks['render_mosaic'],
                    "update_button_text": callbacks['update_button_text'],
                    "save_state":         callbacks['save_state'],
                    "state":              callbacks.get('state'),
                }
            )

    def on_cancelled():
        # Le nettoyage est déjà fait dans _cancel (appelé depuis le thread UI)
        pass

    def _cleanup():
        worker.deleteLater()

    worker = _ConversionWorker(target_format, quality, selected_entries,
                               state, insert_after_idx, inserted_entries)
    worker_ref[0] = worker
    worker.progress.connect(on_progress)
    worker.finished.connect(on_finished)
    worker.cancelled.connect(on_cancelled)
    worker.finished.connect(lambda *_: _cleanup())
    worker.cancelled.connect(_cleanup)
    _show(0)
    worker.start()
