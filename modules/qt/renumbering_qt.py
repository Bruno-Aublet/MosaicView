"""
renumbering_qt.py — Renumérotation des pages (version PySide6)

Reproduit fidèlement modules/renumbering.py (tkinter).

Deux modes :
  - renumber_pages_auto_qt : auto-détection pages multiples via ratio w/h
  - renumber_pages_qt      : renumérotation simple séquentielle

La logique métier (compute_auto_multipliers, generate_auto_filenames,
renumber_pages_auto, renumber_pages) reste dans modules/renumbering.py.
Ce module ne fait que fournir :
  - show_first_page_dialog_qt : dialogue Qt de choix pour la 1ère page multiple
  - Les deux fonctions d'entrée Qt qui construisent les callbacks attendus
"""

import io
import os

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton,
    QPushButton, QButtonGroup, QWidget, QFrame,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QImage, QIcon

from modules.qt.localization import _, _wt
from modules.qt.font_loader import resource_path
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt import renumbering as _renumbering_module


# ═══════════════════════════════════════════════════════════════════════════════
# Dialogue "première page multiple"
# ═══════════════════════════════════════════════════════════════════════════════

class _FirstPageDialog(QDialog):
    """Dialogue "première page multiple" — supporte le changement de langue à la volée."""

    def __init__(self, parent, first_entry: dict, first_mult: int, total_logical_pages: int):
        super().__init__(parent)
        self.setModal(True)
        self._result = None

        ico_path = resource_path("icons/MosaicView.ico")
        if os.path.exists(ico_path):
            self.setWindowIcon(QIcon(ico_path))

        # Calcul des labels de fichier (fixes, ne dépendent pas de la langue)
        digits = max(2, len(str(total_logical_pages)))
        if first_mult <= 4:
            parts_auto = [str(j + 1).zfill(digits) for j in range(first_mult)]
            self._label_auto = "-".join(parts_auto)
        else:
            self._label_auto = str(1).zfill(digits) + "---" + str(first_mult).zfill(digits)
        self._label_auto += first_entry.get("extension", "")
        self._label_joint = (str(1).zfill(digits) + "-" + str(total_logical_pages).zfill(digits)
                             + first_entry.get("extension", ""))

        # ── Layout principal ───────────────────────────────────────────────────
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 14, 18, 12)
        main_layout.setSpacing(8)

        self._title_lbl = QLabel()
        title_font = _get_current_font(11)
        title_font.setBold(True)
        self._title_lbl.setFont(title_font)
        self._title_lbl.setWordWrap(True)
        main_layout.addWidget(self._title_lbl)

        # ── Zone centrale ──────────────────────────────────────────────────────
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 4, 0, 4)
        content_layout.setSpacing(12)

        left = QWidget()
        self._left_layout = QVBoxLayout(left)
        self._left_layout.setContentsMargins(0, 0, 0, 0)
        self._left_layout.setSpacing(2)
        self._left_layout.setAlignment(Qt.AlignTop)

        font_normal = _get_current_font(10)
        font_small_italic = _get_current_font(9)
        font_small_italic.setItalic(True)

        self._btn_group = QButtonGroup(self)

        # Radio "auto"
        self._rb_auto = QRadioButton(self._label_auto)
        self._rb_auto.setFont(font_normal)
        self._rb_auto.setProperty("option_value", "auto")
        self._rb_auto.setChecked(True)
        self._btn_group.addButton(self._rb_auto)
        self._left_layout.addWidget(self._rb_auto)
        self._desc_auto = QLabel()
        self._desc_auto.setFont(font_small_italic)
        self._desc_auto.setWordWrap(True)
        self._desc_auto.setContentsMargins(22, 0, 0, 8)
        self._left_layout.addWidget(self._desc_auto)

        # Radio "joint"
        self._rb_joint = QRadioButton(self._label_joint)
        self._rb_joint.setFont(font_normal)
        self._rb_joint.setProperty("option_value", "joint")
        self._btn_group.addButton(self._rb_joint)
        self._left_layout.addWidget(self._rb_joint)
        self._desc_joint = QLabel()
        self._desc_joint.setFont(font_small_italic)
        self._desc_joint.setWordWrap(True)
        self._desc_joint.setContentsMargins(22, 0, 0, 8)
        self._left_layout.addWidget(self._desc_joint)

        # Radio "exclude"
        self._rb_exclude = QRadioButton()
        self._rb_exclude.setFont(font_normal)
        self._rb_exclude.setProperty("option_value", "exclude")
        self._btn_group.addButton(self._rb_exclude)
        self._left_layout.addWidget(self._rb_exclude)
        self._desc_exclude = QLabel()
        self._desc_exclude.setFont(font_small_italic)
        self._desc_exclude.setWordWrap(True)
        self._desc_exclude.setContentsMargins(22, 0, 0, 8)
        self._left_layout.addWidget(self._desc_exclude)

        self._left_layout.addStretch()
        content_layout.addWidget(left, stretch=1)

        # Vignette droite
        right = QWidget()
        right.setFixedWidth(130)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 10, 0, 0)
        right_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        try:
            from PIL import Image
            thumb_pil = Image.open(io.BytesIO(first_entry["bytes"]))
            thumb_pil.thumbnail((120, 170), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            thumb_pil.save(buf, format="PNG")
            buf.seek(0)
            qpix = QPixmap.fromImage(QImage.fromData(buf.read()))
            thumb_lbl = QLabel()
            thumb_lbl.setPixmap(qpix)
            thumb_lbl.setAlignment(Qt.AlignCenter)
            right_layout.addWidget(thumb_lbl)
        except Exception:
            pass
        content_layout.addWidget(right, stretch=0)
        main_layout.addWidget(content, stretch=1)

        # ── Séparateur + boutons ───────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #cccccc;")
        main_layout.addWidget(sep)

        btn_row = QWidget()
        btn_row_layout = QHBoxLayout(btn_row)
        btn_row_layout.setContentsMargins(0, 0, 0, 0)
        btn_row_layout.setSpacing(8)
        btn_row_layout.setAlignment(Qt.AlignHCenter)
        self._btn_ok     = QPushButton()
        self._btn_cancel = QPushButton()
        self._btn_ok.setFixedWidth(100)
        self._btn_cancel.setFixedWidth(100)
        self._btn_ok.setCursor(Qt.PointingHandCursor)
        self._btn_cancel.setCursor(Qt.PointingHandCursor)
        self._btn_ok.setDefault(True)
        btn_row_layout.addWidget(self._btn_ok)
        btn_row_layout.addWidget(self._btn_cancel)
        main_layout.addWidget(btn_row)

        self._btn_ok.clicked.connect(self._on_ok)
        self._btn_cancel.clicked.connect(self._on_cancel)

        # ── Langue à la volée ──────────────────────────────────────────────────
        self._retranslate()
        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

        # Centrage sur la fenêtre principale
        self.adjustSize()
        main_win = parent.window() if parent else None
        if main_win:
            pg = main_win.geometry()
            self.move(
                pg.x() + (pg.width()  - self.width())  // 2,
                pg.y() + (pg.height() - self.height()) // 2,
            )

    def _retranslate(self):
        theme = get_current_theme()
        self.setWindowTitle(_wt("dialogs.first_page_multi.title"))
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        self._title_lbl.setText(_("dialogs.first_page_multi.message"))
        self._title_lbl.setStyleSheet(f"color: {theme['text']};")
        self._desc_auto.setText(_("dialogs.first_page_multi.option_auto_desc"))
        self._desc_joint.setText(_("dialogs.first_page_multi.option_joint_desc"))
        self._rb_exclude.setText(_("dialogs.first_page_multi.option_exclude_label"))
        self._desc_exclude.setText(_("dialogs.first_page_multi.option_exclude_desc"))
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 0; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self._btn_ok.setText(_("buttons.ok"))
        self._btn_ok.setFont(_get_current_font(10))
        self._btn_ok.setStyleSheet(btn_style)
        self._btn_cancel.setText(_("buttons.cancel"))
        self._btn_cancel.setFont(_get_current_font(10))
        self._btn_cancel.setStyleSheet(btn_style)

    def _on_ok(self):
        for btn in self._btn_group.buttons():
            if btn.isChecked():
                self._result = btn.property("option_value")
                break
        self.accept()

    def _on_cancel(self):
        self._result = None
        self.reject()

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass


def show_first_page_dialog_qt(parent, first_entry: dict, first_mult: int,
                               total_logical_pages: int) -> str | None:
    """
    Dialogue Qt demandant comment traiter la première page si elle est multiple.
    Reproduit show_first_page_dialog de modules/renumbering.py.

    Retourne : 'auto', 'joint', 'exclude', ou None si annulé.
    """
    dlg = _FirstPageDialog(parent, first_entry, first_mult, total_logical_pages)
    dlg.exec()
    return dlg._result


# ═══════════════════════════════════════════════════════════════════════════════
# Points d'entrée Qt
# ═══════════════════════════════════════════════════════════════════════════════

def renumber_pages_auto_qt(parent_widget, canvas_render_func, save_state_func=None):
    """
    Renumérotation auto Qt — reproduit renumber_pages_auto de MosaicView.py.

    Args:
        parent_widget   : QWidget parent pour le dialogue
        canvas_render_func : callable → re-render la mosaïque après renumérotation
        save_state_func : callable → sauvegarde undo (None = noop)
    """
    noop = lambda: None
    save = save_state_func or noop

    def _show_dialog(first_entry, first_mult, total_logical_pages, callbacks):
        return show_first_page_dialog_qt(parent_widget, first_entry, first_mult,
                                         total_logical_pages)

    # Monkey-patch temporaire de show_first_page_dialog pour injecter notre version Qt
    original = _renumbering_module.show_first_page_dialog
    _renumbering_module.show_first_page_dialog = _show_dialog
    try:
        _renumbering_module.renumber_pages_auto({
            "save_state":         save,
            "render_mosaic":      canvas_render_func,
            "update_button_text": noop,
            "root":               parent_widget,  # non utilisé avec le patch
        })
    finally:
        _renumbering_module.show_first_page_dialog = original


def renumber_pages_qt(canvas_render_func, save_state_func=None):
    """
    Renumérotation simple Qt — reproduit renumber_pages de MosaicView.py.

    Args:
        canvas_render_func : callable → re-render la mosaïque après renumérotation
        save_state_func    : callable → sauvegarde undo (None = noop)
    """
    noop = lambda: None
    save = save_state_func or noop

    _renumbering_module.renumber_pages({
        "save_state":         save,
        "render_mosaic":      canvas_render_func,
        "update_button_text": noop,
    })
