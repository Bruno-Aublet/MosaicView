"""
modules/qt/status_bar_qt.py
Barre de statut Qt pour MosaicView.

Placée dans le panneau central uniquement (pas sous la colonne gauche),
contrairement à QMainWindow.setStatusBar() qui s'étend sur toute la largeur.
"""

import os

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QImage, qGray, qAlpha

from modules.qt.localization import _
from modules.qt.utils import format_file_size
from modules.qt.font_manager_qt import get_current_font
from modules.qt.state import get_current_theme
from modules.qt.overlay_tooltip_qt import OverlayTooltip


_DUPLICATE_INDICATOR_PIXMAP: QPixmap | None = None
_DUPLICATE_INDICATOR_PIXMAP_GRAY: QPixmap | None = None


def _get_duplicate_indicator_pixmap(grayed: bool) -> QPixmap:
    """Version réduite du badge orange de doublon (mosaic_canvas._get_duplicate_pixmap),
    grisée quand aucun doublon n'est présent."""
    global _DUPLICATE_INDICATOR_PIXMAP, _DUPLICATE_INDICATOR_PIXMAP_GRAY
    if grayed:
        if _DUPLICATE_INDICATOR_PIXMAP_GRAY is None:
            from modules.qt.mosaic_canvas import _get_duplicate_pixmap
            src = _get_duplicate_pixmap()
            img = src.toImage().convertToFormat(QImage.Format_ARGB32)
            for y in range(img.height()):
                for x in range(img.width()):
                    pixel = img.pixel(x, y)
                    gray = qGray(pixel)
                    img.setPixel(x, y, (qAlpha(pixel) << 24) | (gray << 16) | (gray << 8) | gray)
            _DUPLICATE_INDICATOR_PIXMAP_GRAY = QPixmap.fromImage(img)
        return _DUPLICATE_INDICATOR_PIXMAP_GRAY
    if _DUPLICATE_INDICATOR_PIXMAP is None:
        from modules.qt.mosaic_canvas import _get_duplicate_pixmap
        _DUPLICATE_INDICATOR_PIXMAP = _get_duplicate_pixmap()
    return _DUPLICATE_INDICATOR_PIXMAP


def _format_tooltip(text: str) -> str:
    """Wrap long tooltip text in HTML so the overlay tooltip word-wraps it."""
    if not text:
        return text
    import html as _html
    escaped = _html.escape(text).replace("\n", "<br>")
    return f'<p style="white-space: normal; max-width: 320px;">{escaped}</p>'


class _ClickableLabel(QLabel):
    """QLabel cliquable utilisé pour les indicateurs de la statusbar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._on_click = None
        self._on_right_click = None
        self._click_enabled = True
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._on_click and self._click_enabled:
            self._on_click()
        elif event.button() == Qt.RightButton and self._on_right_click and self._click_enabled:
            self._on_right_click()
        super().mousePressEvent(event)


class StatusBar(QWidget):
    """Barre de statut à placer en bas du panneau central."""

    def __init__(self, parent=None, tooltip_parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self._zip_level_getter = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(10)

        self._label = QLabel("")
        self._label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self._label, 1)

        self._renumber_indicator = _ClickableLabel()
        self._renumber_indicator.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._renumber_indicator, 0)

        self._indicator_sep = QLabel("|")
        self._indicator_sep.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._indicator_sep, 0)

        self._zip_indicator = _ClickableLabel()
        self._zip_indicator.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._zip_indicator, 0)

        self._duplicate_sep = QLabel("|")
        self._duplicate_sep.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._duplicate_sep, 0)

        self._duplicate_indicator = _ClickableLabel()
        self._duplicate_indicator.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._duplicate_indicator, 0)

        # Tooltip overlay : QLabel enfant du panneau central (pas de la statusbar
        # elle-même, trop étroite en hauteur) pour ne pas être borné — même pattern
        # que TabBar(tooltip_parent=panel)
        self._overlay_tip = OverlayTooltip(tooltip_parent or self)
        self._overlay_tip.track(self._renumber_indicator, "")
        self._overlay_tip.track(self._zip_indicator, "")
        self._overlay_tip.track(self._duplicate_indicator, "")

    def set_renumber_click_callback(self, callback):
        """callback : appelé au clic gauche sur l'indicateur de mode de renumérotation."""
        self._renumber_indicator._on_click = callback

    def set_zip_click_callback(self, callback):
        """callback : appelé au clic gauche sur l'indicateur de compression ZIP."""
        self._zip_indicator._on_click = callback

    def set_zip_right_click_callback(self, callback):
        """callback : appelé au clic droit sur l'indicateur de compression ZIP
        (ouvre la fenêtre de réglage du taux par défaut)."""
        self._zip_indicator._on_right_click = callback

    def set_zip_level_getter(self, getter):
        """getter : callable() → int, retourne le niveau de compression ZIP par défaut courant."""
        self._zip_level_getter = getter

    def set_duplicate_click_callback(self, callback):
        """callback : appelé au clic gauche sur l'indicateur de doublons (ouvre la fenêtre
        de gestion des doublons). Le clic n'a d'effet que si des doublons sont présents."""
        self._duplicate_indicator._on_click = callback

    def refresh(self, state):
        """Met à jour le texte selon l'état courant (reproduit canvas_rendering.update_status_bar)."""
        font9 = get_current_font(9)
        self._label.setFont(font9)
        self._renumber_indicator.setFont(font9)
        self._indicator_sep.setFont(font9)
        self._zip_indicator.setFont(font9)
        self._duplicate_sep.setFont(font9)

        theme = get_current_theme()
        self._renumber_indicator.setStyleSheet(f"color: {theme.get('text', '#000000')};")
        self._indicator_sep.setStyleSheet(f"color: {theme.get('separator', '#aaaaaa')};")
        self._zip_indicator.setStyleSheet(f"color: {theme.get('text', '#000000')};")
        self._duplicate_sep.setStyleSheet(f"color: {theme.get('separator', '#aaaaaa')};")

        if state is None:
            self._label.setText("")
            self._renumber_indicator.setText("")
            self._zip_indicator.setText("")
            self._set_duplicate_indicator_state(False)
            return

        dirs_count  = len({e["orig_name"].split("/")[0] for e in state.images_data if "/" in e.get("orig_name", "") and not e.get("is_dir")})
        files_count = sum(1 for e in state.images_data if not e.get("is_dir"))
        selected_count = len(state.selected_indices)

        total_size = sum(
            len(e.get("bytes", b"")) for e in state.images_data if e.get("bytes")
        )
        selected_size = sum(
            len(state.images_data[i].get("bytes", b""))
            for i in state.selected_indices
            if i < len(state.images_data) and state.images_data[i].get("bytes")
        )

        text = _("labels.status_bar",
                 dirs=dirs_count,
                 files=files_count,
                 selected=selected_count,
                 total_size=format_file_size(total_size),
                 selected_size=format_file_size(selected_size))
        self._label.setText(text)

        mode = getattr(state, "renumber_mode", 1)
        mode_key = {0: "labels.renumber_indicator_off",
                    1: "labels.renumber_indicator_auto",
                    2: "labels.renumber_indicator_simple"}.get(mode, "labels.renumber_indicator_auto")
        self._renumber_indicator.setText(_(mode_key))
        tip_html = _format_tooltip(_(f"tooltip.renumber_indicator_{mode}"))
        self._overlay_tip.set_tracked_html(tip_html, self._renumber_indicator)
        if self._overlay_tip._label.isVisible():
            # Le tooltip est déjà affiché (ex. juste après un clic) : on force
            # son contenu à jour immédiatement plutôt que d'attendre le prochain MouseMove
            self._overlay_tip.show_tooltip(tip_html)

        zip_state = getattr(state, "zip_compression_state", None)
        has_file = bool(state.images_data)
        default_level = self._zip_level_getter() if self._zip_level_getter else 0
        zip_text_key = {
            "stored": "labels.zip_indicator_stored",
            "deflated": "labels.zip_indicator_deflated",
        }.get(zip_state, "labels.zip_indicator_na")
        self._zip_indicator.setText(_(zip_text_key))
        if has_file:
            self._zip_indicator.setStyleSheet(f"color: {theme.get('text', '#000000')};")
            self._zip_indicator.setCursor(Qt.PointingHandCursor)
        else:
            self._zip_indicator.setStyleSheet(f"color: {theme.get('disabled', '#999999')};")
            self._zip_indicator.setCursor(Qt.ArrowCursor)
        if zip_state == "stored" and default_level <= 0:
            zip_tip_key = "tooltip.zip_indicator_stored_noop"
        elif zip_state == "stored":
            zip_tip_key = "tooltip.zip_indicator_stored_action"
        elif zip_state == "deflated":
            zip_tip_key = "tooltip.zip_indicator_deflated"
        else:
            zip_tip_key = "tooltip.zip_indicator_na"
        if zip_tip_key == "tooltip.zip_indicator_na" and has_file:
            ext = os.path.splitext(state.current_file or "")[1].lower() if state.current_file else ""
            zip_tip_html = _format_tooltip(_(zip_tip_key, level=default_level, ext=ext or _("tooltip.zip_indicator_ext_image")))
        else:
            zip_tip_html = _format_tooltip(_(zip_tip_key, level=default_level)) if has_file else ""
        self._overlay_tip.set_tracked_html(zip_tip_html, self._zip_indicator)
        if self._overlay_tip._label.isVisible():
            self._overlay_tip.show_tooltip(zip_tip_html)

        from modules.qt.duplicate_detection_qt import has_any_duplicate
        self._set_duplicate_indicator_state(has_file and has_any_duplicate(state), has_file=has_file)

    def _set_duplicate_indicator_state(self, active: bool, has_file: bool = False):
        """Met à jour l'icône/tooltip/curseur de l'indicateur de doublons selon
        la présence ou non de doublons dans le fichier actuellement ouvert.
        Sans fichier ouvert, aucun tooltip ne doit être affiché."""
        pixmap = _get_duplicate_indicator_pixmap(grayed=not active)
        target_size = max(14, get_current_font(9).pointSize() + 6)
        scaled = pixmap.scaled(target_size, target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._duplicate_indicator.setPixmap(scaled)
        self._duplicate_indicator.setCursor(Qt.PointingHandCursor if active else Qt.ArrowCursor)
        self._duplicate_indicator._click_enabled = active

        if has_file:
            dup_tip_key = "tooltip.duplicate_indicator" if active else "tooltip.duplicate_indicator_none"
            dup_tip_html = _format_tooltip(_(dup_tip_key))
        else:
            dup_tip_html = ""
        self._overlay_tip.set_tracked_html(dup_tip_html, self._duplicate_indicator)
        if self._overlay_tip._label.isVisible():
            self._overlay_tip.show_tooltip(dup_tip_html)
