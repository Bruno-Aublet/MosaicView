"""
icon_toolbar_qt.py — Colonne d'icônes PNG (version PySide6)
Reproduit fidèlement modules/icon_toolbar.py (tkinter).

Pattern : IconToolbarQt(parent_widget, callbacks, state_getters, config, icons_dir)
"""

import os
import webbrowser

from PIL import Image, ImageEnhance

from PySide6.QtWidgets import (
    QWidget, QScrollArea, QFrame, QGridLayout, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QDialog, QDialogButtonBox, QMenu, QApplication, QSlider, QComboBox,
)
from PySide6.QtCore import Qt, QSize, QMimeData, QPoint, QByteArray, QTimer, QEvent
from PySide6.QtGui import QPixmap, QImage, QDrag, QPainter, QColor, QPen, QIcon

from modules.qt.localization import _, _wt
from modules.qt.font_loader import resource_path
from modules.qt.undo_redo import can_undo, can_redo
from modules.qt.overlay_tooltip_qt import OverlayTooltip
from modules.qt.printing_qt import (
    PRINT_AVAILABLE,
    print_selection as _print_selection,
    print_all as _print_all,
)

# ── Constantes identiques à icon_toolbar.py ───────────────────────────────────

ICON_DEFINITIONS = [
    # --- FICHIER ---
    {"id": "open_file",           "tooltip_key": None,                            "png": "BTN_OPEN.png"},
    {"id": "close_file",          "tooltip_key": None,                            "png": "BTN_Close.png"},
    {"id": "apply_save",          "tooltip_key": None,                            "png": "BTN_Save_to_CBZ.png"},
    {"id": "batch_cbr_cbz",       "tooltip_key": "tooltip.batch_cbr_to_cbz",     "png": "BTN_Batch_CBR-CBZ.png"},
    {"id": "batch_cb7_cbz",       "tooltip_key": "tooltip.batch_cb7_to_cbz",     "png": "BTN_Batch_CB7-CBZ.png"},
    {"id": "batch_cbt_cbz",       "tooltip_key": "tooltip.batch_cbt_to_cbz",     "png": "BTN_Batch_CBT-CBZ.png"},
    {"id": "batch_pdf_cbz",       "tooltip_key": "tooltip.batch_pdf_to_cbz",     "png": "BTN_Batch-PDF-CBZ.png"},
    {"id": "batch_img_cbz",       "tooltip_key": "tooltip.batch_img_to_cbz",     "png": "BTN_Batch_IMAGES-CBZ.png"},
    # --- ÉDITION ---
    {"id": "undo",                "tooltip_key": None,                            "png": "BTN_Batch_Undo.png"},
    {"id": "redo",                "tooltip_key": None,                            "png": "BTN_Batch_Redo.png"},
    {"id": "flatten_directories", "tooltip_key": None,                            "png": "BTN_Flatten_Directory.png"},
    {"id": "web_import",          "tooltip_key": "web.import_web_tooltip",        "png": "BTN_Web_Import.png"},
    {"id": "delete_selected",     "tooltip_key": None,                            "png": "BTN_Delete.png"},
    # --- PRESSE-PAPIERS ---
    {"id": "copy_selected",       "tooltip_key": "context_menu.image.copy",      "png": "BTN_Copy.png"},
    {"id": "cut_selected",        "tooltip_key": "context_menu.image.cut",       "png": "BTN_Cut.png"},
    {"id": "paste",               "tooltip_key": "context_menu.image.paste",     "png": "BTN_Paste.png"},
    # --- ROTATION / MIROIR ---
    {"id": "rotate_left",         "tooltip_key": "tooltip.rotate_left",          "png": "BTN_Rotate_Left.png"},
    {"id": "rotate_right",        "tooltip_key": "tooltip.rotate_right",         "png": "BTN_Rotate_Right.png"},
    {"id": "flip_horizontal",     "tooltip_key": "tooltip.mirror_horizontal",    "png": "BTN_Mirror_Horizontal.png"},
    {"id": "flip_vertical",       "tooltip_key": "tooltip.mirror_vertical",      "png": "BTN_Mirror_Vertical.png"},
    # --- CONVERSION / REDIMENSIONNEMENT / AJUSTEMENTS ---
    {"id": "convert",             "tooltip_key": None,                            "png": "BTN_Convert.png"},
    {"id": "resize",              "tooltip_key": None,                            "png": "BTN_Resize.png"},
    {"id": "adjustments",         "tooltip_key": None,                            "png": "BTN_Adjustments.png"},
    {"id": "crop",                "tooltip_key": None,                            "png": "BTN_Crop.png"},
    {"id": "straighten",          "tooltip_key": "tooltip.straighten",            "png": "BTN_Straighten.png"},
    {"id": "clone_zone",          "tooltip_key": "tooltip.clone_zone",            "png": "BTN_Clone_Zone.png"},
    {"id": "text",                "tooltip_key": "tooltip.text",                  "png": "BTN_Text.png"},
    {"id": "create_ico",          "tooltip_key": None,                            "png": "BTN_ICO.png"},
    # --- ASSEMBLAGE ---
    {"id": "join_pages",          "tooltip_key": None,                            "png": "BTN_Join.png"},
    {"id": "split_page",          "tooltip_key": None,                            "png": "BTN_Split.png"},
    # --- IMPRESSION ---
    {"id": "print_selection",     "tooltip_key": "buttons.print_selection",       "png": "BTN_Print.png"},
    {"id": "print_all",           "tooltip_key": "buttons.print_all",             "png": "BTN_Print_All.png"},
    # --- ORGANISATION ---
    {"id": "renumber",            "tooltip_key": None,                            "png": "BTN_Renumber.png"},
    {"id": "sort",                "tooltip_key": "menu.sort",                     "png": "BTN_Sort.png"},
    # --- CONTACT / DON --- (hors layout par défaut)
    {"id": "open_mail",           "tooltip_key": "mail.tooltip",                  "png": None,  "img_path": "icons/mail.png"},
    {"id": "donation",            "tooltip_key": "donation.menu_label",           "png": None,  "img_path": "paypal/paypal.png"},
    # --- THÈME ---
    {"id": "toggle_theme",        "tooltip_key": "tooltip.theme_button",          "png": "BTN_Theme.png"},
    # --- AIDE ---
    {"id": "show_user_guide",     "tooltip_key": "tooltip.help_button",           "png": "BTN_Help.png"},
    # --- PLEIN ÉCRAN ---
    {"id": "full_screen",         "tooltip_key": "tooltip.fullscreen",            "png": "BTN_Full_Screen.png"},
    # --- TAILLE DE POLICE ---
    {"id": "decrease_font_size",  "tooltip_key": "tooltip.font_decrease_button",  "png": "BTN_Font_Size_T-.png"},
    {"id": "increase_font_size",  "tooltip_key": "tooltip.font_increase_button",  "png": "BTN_Font_Size_T+.png"},
    # --- RÉINITIALISATION ---
    {"id": "reset_to_defaults",   "tooltip_key": "context_menu.canvas.reset",     "png": "BTN_Reset.png"},
    # --- INTERFACE ---
    {"id": "split_ui",            "tooltip_key": "buttons.split_ui",              "png": "BTN_Split_UI.png",
                                                                                   "png_alt": "BTN_UnSplit-UI.png",
                                                                                   "tooltip_key_alt": "buttons.unsplit_ui"},
]

DEFAULT_LAYOUT = [
    "open_file", "close_file", "apply_save", "delete_selected",
    "undo", "redo", "copy_selected", "cut_selected", "paste",
    "flatten_directories", "convert", "resize", "adjustments", "renumber",
]

# Paliers (px, nb_cols) — identiques à icon_toolbar.py
ICON_SIZE_LEVELS = [
    (96, 3),
    (64, 4),
    (48, 5),
]
ICON_PAD = 6

_ACTIVATION_RULES = {
    "open_file":           None,
    "batch_cbr_cbz":       lambda sg: not sg["has_file"]() and not sg["has_images"](),
    "batch_cb7_cbz":       lambda sg: not sg["has_file"]() and not sg["has_images"](),
    "batch_cbt_cbz":       lambda sg: not sg["has_file"]() and not sg["has_images"](),
    "batch_pdf_cbz":       lambda sg: not sg["has_file"]() and not sg["has_images"](),
    "batch_img_cbz":       lambda sg: not sg["has_file"]() and not sg["has_images"](),
    "close_file":          lambda sg: sg["has_file"]() or sg["has_images"](),
    "apply_save":          lambda sg: sg["has_images"](),
    "undo":                lambda sg: sg["has_undo"](),
    "redo":                lambda sg: sg["has_redo"](),
    "flatten_directories": lambda sg: sg["has_subdirs"](),
    "delete_selected":     lambda sg: sg["has_selection"](),
    "copy_selected":       lambda sg: sg["has_selection"](),
    "cut_selected":        lambda sg: sg["has_selection"](),
    "paste":               None,
    "rotate_left":         lambda sg: sg["has_selected_images"](),
    "rotate_right":        lambda sg: sg["has_selected_images"](),
    "flip_horizontal":     lambda sg: sg["has_selected_images"](),
    "flip_vertical":       lambda sg: sg["has_selected_images"](),
    "convert":             lambda sg: sg["has_selected_images"](),
    "resize":              lambda sg: sg["has_selected_images"](),
    "adjustments":         lambda sg: sg["has_selected_images"](),
    "crop":                lambda sg: sg["single_image_selected"](),
    "straighten":          lambda sg: sg["has_images"](),
    "clone_zone":          lambda sg: sg["has_images"](),
    "text":                lambda sg: sg["has_images"](),
    "create_ico":          lambda sg: sg["single_image_selected"](),
    "join_pages":          lambda sg: sg["has_selected_images"]() and sg["selection_count"]() >= 2,
    "split_page":          lambda sg: sg["has_selected_images"]() and sg["selection_count"]() == 1,
    "print_selection":     lambda sg: sg["print_available"]() and sg["has_selection"](),
    "print_all":           lambda sg: sg["print_available"]() and sg["has_images"](),
    "renumber":            lambda sg: sg["needs_renumbering"]() and not sg["has_subdirs"](),
    "sort":                lambda sg: sg["has_images"](),
    "web_import":          None,
    "open_mail":           None,
    "donation":            None,
    "toggle_theme":        None,
    "show_user_guide":     None,
    "full_screen":         None,
    "decrease_font_size":  None,
    "increase_font_size":  None,
    "reset_to_defaults":   None,
    "split_ui":            None,
}


def _pil_to_qpixmap(pil_img: Image.Image) -> QPixmap:
    import io as _io
    buf = _io.BytesIO()
    pil_img.save(buf, format="PNG")
    buf.seek(0)
    return QPixmap.fromImage(QImage.fromData(buf.read()))


def _to_grayscale(pil_img: Image.Image) -> Image.Image:
    r, g, b, a = pil_img.split()
    gray = pil_img.convert("LA").split()[0]
    result = Image.merge("RGBA", (gray, gray, gray, a))
    return ImageEnhance.Brightness(result).enhance(1.5)


# ── Widget icône ──────────────────────────────────────────────────────────────

class IconLabel(QLabel):
    """
    QLabel affichant une icône PNG.
    Survol : fond légèrement coloré. Focus clavier : bordure.
    Clic gauche : déclenche l'action. Clic droit (renumber) : bascule de mode.
    Drag & drop interne : réorganisation de l'ordre.
    """
    MIME_TYPE = "application/x-mosaicview-icon-id"

    def __init__(self, icon_id: str, pixmap_normal: QPixmap, pixmap_gray: QPixmap,
                 toolbar: "IconToolbarQt"):
        super().__init__()
        self.icon_id        = icon_id
        self._pm_normal     = pixmap_normal
        self._pm_gray       = pixmap_gray
        self._toolbar       = toolbar
        self._active          = True
        self._drag_start      = None
        self._pending_tooltip = None

        self.setPixmap(pixmap_normal)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setScaledContents(False)
        self.setMouseTracking(True)

    def set_active(self, active: bool):
        if self._active != active:
            self._active = active
            self.setPixmap(self._pm_normal if active else self._pm_gray)
            self.setCursor(Qt.PointingHandCursor if active else Qt.ArrowCursor)

    # ── Survol ────────────────────────────────────────────────────────────────
    @staticmethod
    def _format_tooltip(text: str) -> str:
        """Wrap long tooltip text in HTML so QToolTip word-wraps it."""
        if not text:
            return text
        import html as _html
        escaped = _html.escape(text).replace("\n", "<br>")
        return f'<p style="white-space: normal; max-width: 320px;">{escaped}</p>'

    def enterEvent(self, event):
        if self._active:
            c = IconToolbarQt._hover_color
            self.setStyleSheet(f"background: {c}; border-radius: 4px;")
            tb = self._toolbar
            if self.icon_id == "renumber":
                mode = tb._state_getters.get("renumber_mode", lambda: 1)()
                tb.show_tooltip(self._format_tooltip(_(f"tooltip.renumber_btn_{mode}")))
            elif self.icon_id == "split_ui":
                defn = tb._defs.get("split_ui", {})
                split_active = tb._state_getters.get("split_active", lambda: False)()
                key = defn.get("tooltip_key_alt") if split_active else defn.get("tooltip_key")
                if key:
                    tb.show_tooltip(self._format_tooltip(_(key)))
            else:
                defn = tb._defs.get(self.icon_id, {})
                key = defn.get("tooltip_key")
                if key:
                    tb.show_tooltip(self._format_tooltip(_(key)))

    def leaveEvent(self, event):
        self.setStyleSheet("")
        if self._pending_tooltip is None:
            self._toolbar.hide_tooltip()

    # ── Focus clavier ─────────────────────────────────────────────────────────
    def focusInEvent(self, event):
        super().focusInEvent(event)
        c = IconToolbarQt._hover_color
        self.setStyleSheet(f"background: {c}; border: 1px solid #888; border-radius: 4px;")
        # Scroll automatique pour rendre l'icône visible
        scroll = self._toolbar._scroll_area if self._toolbar else None
        if scroll:
            scroll.ensureWidgetVisible(self)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.setStyleSheet("")

    # ── Clic ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start = event.position().toPoint()
        elif event.button() == Qt.RightButton:
            if self.icon_id == "renumber":
                cb = self._toolbar._callbacks.get("toggle_renumber_mode")
                if cb:
                    cb()
                    self._pending_tooltip = True  # bloque hideText dans leaveEvent
            elif self.icon_id == "open_mail":
                self._toolbar._show_mail_context_menu(event.globalPosition().toPoint())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            if self.icon_id == "renumber" and self._pending_tooltip:
                self._pending_tooltip = None
                mode = self._toolbar._state_getters.get("renumber_mode", lambda: 1)()
                self._toolbar.show_tooltip(self._format_tooltip(_(f"tooltip.renumber_btn_{mode}")))
        if event.button() == Qt.LeftButton:
            if self._drag_start is not None:
                delta = (event.position().toPoint() - self._drag_start).manhattanLength()
                if delta < 15 and self._active:
                    cb = self._toolbar._callbacks.get(self.icon_id)
                    if cb:
                        if self.icon_id == "sort":
                            cb(event)
                        else:
                            cb()
        self._drag_start = None

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Space):
            if self._active:
                cb = self._toolbar._callbacks.get(self.icon_id)
                if cb:
                    cb()
        elif key == Qt.Key_Up:
            self._toolbar._navigate(self, "up")
        elif key == Qt.Key_Down:
            self._toolbar._navigate(self, "down")
        elif key == Qt.Key_Left:
            self._toolbar._navigate(self, "left")
        elif key == Qt.Key_Right:
            self._toolbar._navigate(self, "right")
        else:
            super().keyPressEvent(event)

    # ── Drag & drop interne ───────────────────────────────────────────────────
    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton):
            # Suivi souris sans bouton : repositionner le tooltip
            tip = self._toolbar._overlay_tip
            if tip._label.isVisible():
                tip._reposition()
            return
        if self._drag_start is None:
            return
        delta = (event.position().toPoint() - self._drag_start).manhattanLength()
        if delta < 8:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(self.MIME_TYPE, QByteArray(self.icon_id.encode()))
        drag.setMimeData(mime)
        drag.setPixmap(self._pm_normal.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        drag.setHotSpot(QPoint(24, 24))
        drag.exec(Qt.MoveAction)
        self._drag_start = None


# ── Widgets footer ────────────────────────────────────────────────────────────

class _FooterLabel(QLabel):
    """Label texte cliquable pour le copyright."""

    def __init__(self, text: str, callback):
        super().__init__(text)
        self._callback = callback
        self._toolbar: "IconToolbarQt | None" = None
        from modules.qt.font_manager_qt import get_current_font
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setFont(get_current_font(8))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._callback:
            self._callback()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Space):
            if self._callback:
                self._callback()
        elif key == Qt.Key_Up and self._toolbar:
            self._toolbar._navigate_footer(self, "up")
        elif key == Qt.Key_Down and self._toolbar:
            self._toolbar._navigate_footer(self, "down")
        else:
            super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.setStyleSheet("border: 1px solid #888; border-radius: 2px;")

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.setStyleSheet("")

    def update_text(self):
        from modules.qt.localization import _
        from modules.qt.font_manager_qt import get_current_font
        self.setText(_("labels.copyright"))
        self.setFont(get_current_font(8))


class _FooterBtn(QPushButton):
    """Bouton footer (−, ⚙, +) avec navigation ↑↓ vers les autres zones footer."""

    def __init__(self):
        super().__init__()
        self._toolbar: "IconToolbarQt | None" = None

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.setStyleSheet("QPushButton { border: 1px solid #888; border-radius: 2px; }")

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.setStyleSheet("")

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Up and self._toolbar:
            self._toolbar._navigate_footer(self, "up")
        elif key == Qt.Key_Down and self._toolbar:
            self._toolbar._navigate_footer(self, "down")
        elif key == Qt.Key_Left and self._toolbar:
            self._toolbar._navigate_footer_horiz(self, -1)
        elif key == Qt.Key_Right and self._toolbar:
            self._toolbar._navigate_footer_horiz(self, +1)
        else:
            super().keyPressEvent(event)

# ── Widget grille ─────────────────────────────────────────────────────────────

_ARROW_SIZE = 9   # demi-largeur des triangles (identique à drag_drop.py tkinter)


class _DropOverlay(QWidget):
    """Overlay transparent au-dessus de tous les enfants, peint uniquement l'indicateur de drop."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._indicator: tuple | None = None
        self.hide()

    def set_indicator(self, info: tuple | None):
        self._indicator = info
        if info is None:
            self.hide()
        else:
            self.raise_()
            self.show()
        self.update()

    def paintEvent(self, event):
        if self._indicator is None:
            return
        _, x, y_top, y_bot = self._indicator
        A = _ARROW_SIZE
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("red"), 3)
        painter.setPen(pen)
        painter.setBrush(QColor("red"))
        # Trait vertical
        painter.drawLine(x, y_top + A, x, y_bot - A)
        # Triangle haut (pointe vers le bas)
        from PySide6.QtGui import QPolygon
        top_tri = QPolygon([
            QPoint(x - A, y_top),
            QPoint(x + A, y_top),
            QPoint(x,     y_top + A),
        ])
        painter.drawPolygon(top_tri)
        # Triangle bas (pointe vers le haut)
        bot_tri = QPolygon([
            QPoint(x - A, y_bot),
            QPoint(x + A, y_bot),
            QPoint(x,     y_bot - A),
        ])
        painter.drawPolygon(bot_tri)
        painter.end()


class IconGrid(QWidget):
    """Grille de labels icônes avec accept drop pour le réordonnancement."""

    _SCROLL_ZONE  = 40   # px depuis le bord pour déclencher l'auto-scroll
    _SCROLL_STEP  = 12   # px scrollés par tick
    _SCROLL_INTERVAL = 30  # ms entre chaque tick

    def __init__(self, toolbar: "IconToolbarQt"):
        super().__init__()
        self._toolbar = toolbar
        self.setAcceptDrops(True)
        self._layout_grid = QGridLayout(self)
        self._layout_grid.setSpacing(0)
        self._layout_grid.setContentsMargins(ICON_PAD, ICON_PAD, ICON_PAD, ICON_PAD)
        # indicateur de drop : (insert_idx, x, y_top, y_bottom) ou None
        self._drop_indicator: tuple | None = None
        # overlay au-dessus des icônes pour le trait de drop
        self._drop_overlay = _DropOverlay(self)
        # auto-scroll pendant le drag
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(self._SCROLL_INTERVAL)
        self._scroll_timer.timeout.connect(self._do_auto_scroll)
        self._scroll_direction = 0  # -1 = haut, +1 = bas, 0 = arrêté

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._drop_overlay.setGeometry(0, 0, self.width(), self.height())

    # ── Calcul de l'index d'insertion et de la position du trait ──────────────

    def _calc_insert(self, pos: QPoint) -> tuple:
        """
        Retourne (insert_idx, line_x, line_y_top, line_y_bottom).
        insert_idx ∈ [0, len(layout)] : position d'insertion dans self._toolbar._layout.
        line_x / line_y : coordonnées du trait vertical rouge à dessiner.

        Algorithme : trouver la cellule la plus proche en distance Manhattan,
        puis décider si on insère avant ou après selon la demi-cellule (X et Y).
        """
        tb = self._toolbar
        layout = tb._layout
        n      = len(layout)

        if n == 0:
            return (0, ICON_PAD, ICON_PAD, self.height() - ICON_PAD)

        # 1. Trouver la cellule la plus proche (centre de la cellule)
        best_i    = 0
        best_dist = float("inf")
        for i, icon_id in enumerate(layout):
            lbl = tb._icon_widgets.get(icon_id)
            if lbl is None:
                continue
            geo    = lbl.geometry()
            cx     = geo.left() + geo.width()  // 2
            cy     = geo.top()  + geo.height() // 2
            dist   = abs(pos.x() - cx) + abs(pos.y() - cy)
            if dist < best_dist:
                best_dist = dist
                best_i    = i

        lbl = tb._icon_widgets.get(layout[best_i])
        geo = lbl.geometry()
        mid_x = geo.left() + geo.width()  // 2
        mid_y = geo.top()  + geo.height() // 2

        # 2. Décider avant ou après selon la position relative dans la cellule
        #    On compare X et Y simultanément : si le curseur est dans la moitié
        #    gauche/haute → insérer avant, droite/basse → insérer après.
        #    On utilise la composante dominante (écart le plus grand).
        dx = pos.x() - mid_x
        dy = pos.y() - mid_y

        if abs(dy) >= abs(dx):
            insert_before = dy < 0
        else:
            insert_before = dx < 0

        if insert_before:
            insert_idx = best_i
            line_x     = geo.left()
        else:
            insert_idx = best_i + 1
            line_x     = geo.right()

        return (insert_idx, line_x, geo.top(), geo.bottom())

    # ── Auto-scroll ───────────────────────────────────────────────────────────

    def _do_auto_scroll(self):
        sa = self._toolbar._scroll_area
        if sa is None:
            return
        vbar = sa.verticalScrollBar()
        vbar.setValue(vbar.value() + self._scroll_direction * self._SCROLL_STEP)

    def _update_auto_scroll(self, pos: QPoint):
        """Démarre/arrête l'auto-scroll selon la position du curseur dans le scroll_area."""
        sa = self._toolbar._scroll_area
        if sa is None:
            return
        # Convertir pos (coordonnées IconGrid) en coordonnées viewport du scroll_area
        viewport_pos = self.mapTo(sa.viewport(), pos)
        vh = sa.viewport().height()
        zone = self._SCROLL_ZONE

        if viewport_pos.y() < zone:
            direction = -1
        elif viewport_pos.y() > vh - zone:
            direction = 1
        else:
            direction = 0

        if direction != self._scroll_direction:
            self._scroll_direction = direction
            if direction != 0:
                self._scroll_timer.start()
            else:
                self._scroll_timer.stop()

    def _stop_auto_scroll(self):
        self._scroll_timer.stop()
        self._scroll_direction = 0

    # ── Événements drag ───────────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(IconLabel.MIME_TYPE):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if not event.mimeData().hasFormat(IconLabel.MIME_TYPE):
            return
        event.acceptProposedAction()
        pos = event.position().toPoint()
        info = self._calc_insert(pos)
        self._drop_indicator = info
        self._drop_overlay.set_indicator(info)
        self._update_auto_scroll(pos)

    def dragLeaveEvent(self, event):
        self._drop_indicator = None
        self._drop_overlay.set_indicator(None)
        self._stop_auto_scroll()

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(IconLabel.MIME_TYPE):
            return
        src_id = bytes(event.mimeData().data(IconLabel.MIME_TYPE)).decode()
        insert_idx = self._drop_indicator[0] if self._drop_indicator else None
        self._drop_indicator = None
        self._drop_overlay.set_indicator(None)
        self._stop_auto_scroll()
        self._toolbar._reorder_by_drop(src_id, event.position().toPoint(), insert_idx)
        event.acceptProposedAction()


# ── Réglette taille des vignettes ────────────────────────────────────────────

class ThumbSizeSlider(QWidget):
    """
    Réglette horizontale 0-2 pour la taille des vignettes.
    Reproduit fidèlement le tk.Scale de MosaicView.py :
      - 3 positions : small (0), normal (1), large (2)
      - Espace : cycle 0→1→2→0
      - Flèches : redirigées (ne changent pas la valeur)
      - Label traduit au-dessus
    """

    def __init__(self, initial_index: int = 1, on_change=None, theme: str = "light"):
        super().__init__()
        self._on_change = on_change
        self._visible = True  # réservé pour l'option future de masquage
        self._theme = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        # Label "Taille vignettes" — centré sur 90px
        self._label = QLabel(_("labels.thumb_size"))
        from modules.qt.font_manager_qt import get_current_font
        self._label.setFont(get_current_font(7))
        self._label.setFixedWidth(90)
        self._label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._label, alignment=Qt.AlignLeft)

        # Slider — longueur fixe 90px, pas de valeur affichée, pas de ticks
        self._slider = _ThumbSlider(Qt.Horizontal, self)
        self._slider.setMinimum(0)
        self._slider.setMaximum(2)
        self._slider.setSingleStep(1)
        self._slider.setPageStep(1)
        self._slider.setTickPosition(QSlider.NoTicks)
        self._slider.setFixedWidth(90)
        self._slider.setFixedHeight(20)
        self._slider.valueChanged.connect(self._emit_change)
        layout.addWidget(self._slider, alignment=Qt.AlignLeft)

        self._slider.setValue(initial_index)
        self._apply_theme(theme)

    def _apply_theme(self, theme: str):
        self._theme = theme
        is_dark = (theme == "dark")
        groove_bg  = "#555555" if is_dark else "#c0c0c0"
        groove_bd  = "#777777" if is_dark else "#999999"
        handle_bg  = "#888888" if is_dark else "#e8e8e8"
        handle_hov = "#aaaaaa" if is_dark else "#d0d0d0"
        text_color = "#dddddd" if is_dark else "#000000"
        self._label.setStyleSheet(f"color: {text_color};")
        self._slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 20px;
                margin: 0px;
                background: transparent;
                border: none;
            }}
            QSlider::sub-page:horizontal {{
                height: 4px;
                margin: 8px 0px;
                background: {groove_bg};
                border: 1px solid {groove_bd};
                border-radius: 1px;
            }}
            QSlider::add-page:horizontal {{
                height: 4px;
                margin: 8px 0px;
                background: {groove_bg};
                border: 1px solid {groove_bd};
                border-radius: 1px;
            }}
            QSlider::handle:horizontal {{
                width: 12px;
                height: 20px;
                margin: 0px;
                background: {handle_bg};
                border: 1px solid {groove_bd};
                border-radius: 1px;
            }}
            QSlider::handle:horizontal:hover {{
                background: {handle_hov};
            }}
            QSlider:focus {{
                outline: none;
                border: 1px solid #888;
                border-radius: 2px;
            }}
        """)

    def set_theme(self, theme: str):
        self._apply_theme(theme)

    def _emit_change(self, value: int):
        if self._on_change:
            self._on_change(value)

    def set_value(self, index: int):
        self._slider.blockSignals(True)
        self._slider.setValue(index)
        self._slider.blockSignals(False)

    def get_value(self) -> int:
        return self._slider.value()

    def update_language(self):
        from modules.qt.font_manager_qt import get_current_font
        self._label.setText(_("labels.thumb_size"))
        self._label.setFont(get_current_font(7))


class _ThumbSlider(QSlider):
    """QSlider spécialisé : Espace=cycle, flèches ↑↓=navigation footer."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._toolbar: "IconToolbarQt | None" = None

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            cur = self.value()
            self.setValue((cur + 1) % (self.maximum() + 1))
            event.accept()
        elif key == Qt.Key_Up and self._toolbar:
            self._toolbar._navigate_footer(self, "up")
        elif key == Qt.Key_Down and self._toolbar:
            self._toolbar._navigate_footer(self, "down")
        elif key == Qt.Key_Left and self._toolbar:
            self._toolbar._navigate_footer_horiz(self, -1)
        elif key == Qt.Key_Right and self._toolbar:
            self._toolbar._navigate_footer_horiz(self, +1)
        else:
            super().keyPressEvent(event)


# ── Dropdown de langue ────────────────────────────────────────────────────────

from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QStyleOptionComboBox, QStyle
from PySide6.QtCore import QModelIndex

_LANG_IS_CURRENT_ROLE = Qt.UserRole + 1


class _LangComboDelegate(QStyledItemDelegate):
    """Dessine les items de la liste déroulante avec leur Qt.FontRole.
    Met en gras l'item dont _LANG_IS_CURRENT_ROLE est True."""
    def paint(self, painter, option, index):
        from PySide6.QtGui import QFont as _QFont
        font = index.data(Qt.FontRole)
        is_current = index.data(_LANG_IS_CURRENT_ROLE)
        if font or is_current:
            option = QStyleOptionViewItem(option)
            font = _QFont(font) if font else _QFont(option.font)
            if is_current:
                font.setBold(True)
            option.font = font
        super().paint(painter, option, index)

    def sizeHint(self, option, index):
        from PySide6.QtGui import QFont as _QFont
        font = index.data(Qt.FontRole)
        is_current = index.data(_LANG_IS_CURRENT_ROLE)
        if font or is_current:
            option = QStyleOptionViewItem(option)
            font = _QFont(font) if font else _QFont(option.font)
            if is_current:
                font.setBold(True)
            option.font = font
        return super().sizeHint(option, index)


_wheel_hook_singleton = None  # WheelHook global, créé une seule fois


class _LangCombo(QComboBox):
    """QComboBox qui dessine le texte replié avec la police de l'item courant."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._from_hook = False
        self._toolbar: "IconToolbarQt | None" = None
        global _wheel_hook_singleton
        from modules.qt.wheel_hook import WheelHook
        if _wheel_hook_singleton is None:
            _wheel_hook_singleton = WheelHook(self)
            _wheel_hook_singleton.install()
        else:
            _wheel_hook_singleton._target = self

    def showPopup(self):
        super().showPopup()
        self.view().installEventFilter(self)

    def hidePopup(self):
        self.view().removeEventFilter(self)
        super().hidePopup()

    def eventFilter(self, obj, event):
        if obj is self.view() and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Space:
                idx = self.view().currentIndex()
                if idx.isValid():
                    self.setCurrentIndex(idx.row())
                super(_LangCombo, self).hidePopup()
                return True
        return False

    def keyPressEvent(self, event):
        key = event.key()
        if not self.view().isVisible():
            # Liste fermée
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self.showPopup()
                return
            elif key == Qt.Key_Up and self._toolbar:
                self._toolbar._navigate_footer(self, "up")
                return
            elif key == Qt.Key_Down and self._toolbar:
                self._toolbar._navigate_footer(self, "down")
                return
            elif key == Qt.Key_Left and self._toolbar:
                self._toolbar._navigate_footer_horiz(self, -1)
                return
            elif key == Qt.Key_Right and self._toolbar:
                self._toolbar._navigate_footer_horiz(self, +1)
                return
        super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def wheelEvent(self, event):
        # Si l'événement vient du hook, on l'a déjà traité dans wheel_from_hook → ignorer
        if self._from_hook:
            event.accept()
            return
        # Windows "scroll inactive windows" : Qt peut livrer un wheel natif même si la fenêtre
        # n'est pas active. Dans ce cas le hook va aussi envoyer → on laisse le hook gérer seul.
        from PySide6.QtWidgets import QApplication
        top = self.window()
        if top and top != QApplication.activeWindow():
            event.accept()
            return
        super().wheelEvent(event)
        event.accept()

    def wheel_from_hook(self, event):
        """Appelé par WheelHook à la place de sendEvent direct.
        Si la fenêtre parente est la fenêtre Qt active, Qt livrera l'événement natif → ne rien faire."""
        from PySide6.QtWidgets import QApplication
        top = self.window()
        if top and top is QApplication.activeWindow():
            return
        self._from_hook = True
        try:
            super().wheelEvent(event)
        finally:
            self._from_hook = False

    def paintEvent(self, event):
        from PySide6.QtWidgets import QStylePainter, QStyleOptionComboBox
        painter = QStylePainter(self)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        idx = self.currentIndex()
        if idx >= 0:
            font = self.itemData(idx, Qt.FontRole)
            if font:
                opt.font = font
                painter.setFont(font)
        painter.drawComplexControl(QStyle.CC_ComboBox, opt)
        painter.drawControl(QStyle.CE_ComboBoxLabel, opt)
        if self.hasFocus():
            painter.setPen(QPen(QColor("#888888"), 2))
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))


class LanguageComboWidget(QWidget):
    """
    QComboBox simple pour changer de langue.
    languages : liste de (code, nom, police) — même format que MosaicView.py
    on_change  : callable(code)
    """

    def __init__(self, languages: list, current_code: str, on_change=None, theme: str = "light"):
        super().__init__()
        self._languages    = languages   # [(code, nom, police), ...]
        self._on_change    = on_change
        self._theme        = theme
        self._updating     = False
        self._current_code = current_code

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)

        self._combo = _LangCombo()
        self._combo.setMinimumWidth(90)
        self._combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._combo.setFocusPolicy(Qt.WheelFocus)
        from modules.qt.font_manager_qt import get_font_manager
        _fm = get_font_manager()
        _special = {getattr(_fm, 'piqad_font_name', None), getattr(_fm, 'tengwar_font_name', None)} - {None}
        for code, name, font_name in languages:
            self._combo.addItem(name)
            idx = self._combo.count() - 1
            self._combo.setItemData(idx, code, Qt.UserRole)
            if font_name in _special:
                from PySide6.QtGui import QFont as _QFont
                self._combo.setItemData(idx, _QFont(font_name, 9), Qt.FontRole)

        self._combo.setItemDelegate(_LangComboDelegate(self._combo))
        self._set_current(current_code)
        self._update_current_role(current_code)
        self._combo.currentIndexChanged.connect(self._on_index_changed)
        layout.addWidget(self._combo)

        self._apply_theme(theme)

    def _set_current(self, code: str):
        for i in range(self._combo.count()):
            if self._combo.itemData(i, Qt.UserRole) == code:
                self._combo.setCurrentIndex(i)
                return

    def _update_current_role(self, code: str):
        from PySide6.QtGui import QColor as _QColor
        from modules.qt.state import get_current_theme
        accent = get_current_theme()["link"]
        theme_text = get_current_theme()["text"]
        for i in range(self._combo.count()):
            is_cur = self._combo.itemData(i, Qt.UserRole) == code
            self._combo.setItemData(i, is_cur, _LANG_IS_CURRENT_ROLE)
            # Texte : nom brut + ✓ en fin si actif
            raw_name = self._combo.itemData(i, Qt.UserRole + 2)
            if raw_name is None:
                raw_name = self._combo.itemText(i).rstrip(" \u2713").rstrip()
                self._combo.setItemData(i, raw_name, Qt.UserRole + 2)
            label = raw_name + "  \u2713" if is_cur else raw_name
            self._combo.setItemData(i, label, Qt.DisplayRole)
            # Couleur
            self._combo.setItemData(i, _QColor(accent if is_cur else theme_text), Qt.ForegroundRole)

    def _on_index_changed(self, index: int):
        if self._updating:
            return
        code = self._combo.itemData(index, Qt.UserRole)
        if code is None or code == "__header__":
            # Item non sélectionnable — revenir à la langue courante
            self._updating = True
            self._set_current(self._current_code)
            self._updating = False
            return
        self._current_code = code
        if self._on_change:
            self._on_change(code)

    def set_current_language(self, code: str):
        self._current_code = code
        self._updating = True
        self._set_current(code)
        self._updating = False
        self._update_current_role(code)
        self._combo.update()  # force le repaint pour appliquer la bonne police

    def _apply_theme(self, theme: str):
        self._theme = theme
        is_dark = (theme == "dark")
        bg   = "#3a3a3a" if is_dark else "#ffffff"
        text = "#dddddd" if is_dark else "#000000"
        border = "#555555" if is_dark else "#aaaaaa"
        self._combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {bg};
                color: {text};
                border: 1px solid {border};
                padding: 1px 4px;
            }}
            QComboBox:focus {{
                border: 1px solid #888;
            }}
            QComboBox QAbstractItemView {{
                background-color: {bg};
                color: {text};
                selection-background-color: #3a7bd5;
                selection-color: #ffffff;
            }}
        """)

    def set_theme(self, theme: str):
        self._apply_theme(theme)
        self._update_current_role(self._current_code)


# ── Barre d'icônes principale ─────────────────────────────────────────────────

class IconToolbarQt(QWidget):
    """
    Colonne d'icônes PNG pour MosaicView Qt.
    Reproduit fidèlement IconToolbar (tkinter).
    """

    _hover_color: str = "#cccccc"  # mis à jour par set_hover_color() au changement de thème

    def __init__(self, parent, callbacks: dict, state_getters: dict, config, icons_dir: str):
        super().__init__(parent)
        self._callbacks     = callbacks
        self._state_getters = state_getters
        self._config        = config
        self._icons_dir     = icons_dir

        self._defs = {d["id"]: d for d in ICON_DEFINITIONS}

        # Layout chargé depuis config ou défaut
        saved = config.get_icon_toolbar_layout() if hasattr(config, "get_icon_toolbar_layout") else None
        if saved:
            self._layout = [i for i in saved if i in self._defs]
        else:
            self._layout = list(DEFAULT_LAYOUT)

        # Taille d'icône
        raw_idx = config.get_icon_size_index() if hasattr(config, "get_icon_size_index") else 0
        self._size_index = max(0, min(len(ICON_SIZE_LEVELS) - 1, raw_idx))
        self._icon_size  = ICON_SIZE_LEVELS[self._size_index][0]
        self._cols       = ICON_SIZE_LEVELS[self._size_index][1]

        # Taille des vignettes (0=small, 1=normal, 2=large)
        self._thumb_size_index = 1  # sera mis à jour par set_thumb_size_index()

        # Visibilité réglette et combo langue
        self._show_thumb_slider = config.get_show_thumb_slider() if hasattr(config, "get_show_thumb_slider") else True
        self._show_lang_combo   = config.get_show_lang_combo()   if hasattr(config, "get_show_lang_combo")   else True

        # Caches pixmap
        self._pm_cache      = {}  # id → QPixmap normal
        self._pm_cache_gray = {}  # id → QPixmap grisé

        # Widgets icônes
        self._icon_widgets: dict[str, IconLabel] = {}

        # Layout principal créé une seule fois
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        # Tooltip overlay (QLabel enfant de la toolbar, positionné près du curseur)
        self._overlay_tip = OverlayTooltip(self)

        self._build()

    # ── Méthodes tooltip overlay ───────────────────────────────────────────────

    def show_tooltip(self, html: str):
        self._overlay_tip.show_tooltip(html)

    def hide_tooltip(self):
        self._overlay_tip.hide_tooltip()

    # ── Construction ──────────────────────────────────────────────────────────

    def _build(self):
        # Nettoyage : vide le layout principal sans le recréer
        while self._main_layout.count():
            item = self._main_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        main_layout = self._main_layout

        # Zone scrollable
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setContentsMargins(0, 0, 0, 0)
        self._grid_widget = IconGrid(self)
        self._grid_layout = self._grid_widget._layout_grid
        scroll_area.setWidget(self._grid_widget)
        self._scroll_area = scroll_area
        main_layout.addWidget(scroll_area, stretch=1)

        # Séparateur
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #cccccc;")
        main_layout.addWidget(sep)

        # Ligne [-] [⚙] [+]
        size_row = QWidget()
        size_row_layout = QHBoxLayout(size_row)
        size_row_layout.setContentsMargins(6, 2, 6, 4)
        size_row_layout.setSpacing(0)

        self._btn_minus = _FooterBtn()
        self._btn_minus._toolbar = self
        self._btn_minus.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._btn_minus.setFlat(True)
        self._btn_minus.setCursor(Qt.PointingHandCursor)
        self._btn_minus.setMouseTracking(True)
        self._btn_minus.clicked.connect(self._decrease_icon_size)
        self._set_btn_icon(self._btn_minus, "BTN_-.png", 20)
        size_row_layout.addWidget(self._btn_minus)

        self._btn_cfg = _FooterBtn()
        self._btn_cfg._toolbar = self
        self._btn_cfg.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._btn_cfg.setFlat(True)
        self._btn_cfg.setCursor(Qt.PointingHandCursor)
        self._btn_cfg.setMouseTracking(True)
        self._btn_cfg.clicked.connect(self._open_config_window)
        self._set_btn_icon(self._btn_cfg, "BTN_Control.png", 20)
        size_row_layout.addWidget(self._btn_cfg)

        self._btn_plus = _FooterBtn()
        self._btn_plus._toolbar = self
        self._btn_plus.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._btn_plus.setFlat(True)
        self._btn_plus.setCursor(Qt.PointingHandCursor)
        self._btn_plus.setMouseTracking(True)
        self._btn_plus.clicked.connect(self._increase_icon_size)
        self._set_btn_icon(self._btn_plus, "BTN_+.png", 20)
        size_row_layout.addWidget(self._btn_plus)

        self._overlay_tip.track(self._btn_minus, _("tooltip.icon_toolbar_size_decrease"))
        self._overlay_tip.track(self._btn_cfg,   _("tooltip.icon_toolbar_config_button"))
        self._overlay_tip.track(self._btn_plus,  _("tooltip.icon_toolbar_size_increase"))

        main_layout.addWidget(size_row)

        # Réglette taille des vignettes + dropdown de langue (sur la même ligne)
        slider_lang_row = QWidget()
        slider_lang_layout = QHBoxLayout(slider_lang_row)
        slider_lang_layout.setContentsMargins(0, 0, 0, 0)
        slider_lang_layout.setSpacing(0)

        self._thumb_size_slider = ThumbSizeSlider(
            initial_index=self._thumb_size_index,
            on_change=self._on_thumb_size_change,
        )
        self._thumb_size_slider._slider._toolbar = self
        slider_lang_layout.addWidget(self._thumb_size_slider)

        languages = self._callbacks.get("get_languages", lambda: [])()
        current_lang = self._callbacks.get("get_current_language", lambda: "fr")()
        self._lang_combo = LanguageComboWidget(
            languages=languages,
            current_code=current_lang,
            on_change=self._callbacks.get("change_language"),
        )
        self._lang_combo._combo._toolbar = self
        slider_lang_layout.addWidget(self._lang_combo)

        self._slider_lang_row = slider_lang_row
        self._thumb_size_slider.setVisible(self._show_thumb_slider)
        self._lang_combo.setVisible(self._show_lang_combo)
        slider_lang_row.setVisible(self._show_thumb_slider or self._show_lang_combo)

        main_layout.addWidget(slider_lang_row)

        # Séparateur avant footer
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color: #cccccc;")
        main_layout.addWidget(sep2)

        # Footer : copyright
        self._footer_copyright = _FooterLabel(
            text=_("labels.copyright"),
            callback=self._callbacks.get("show_license_dialog"),
        )
        self._footer_copyright._toolbar = self
        self._footer_copyright.setContentsMargins(4, 6, 4, 6)
        main_layout.addWidget(self._footer_copyright)

        self._populate_grid()
        self._update_size_buttons()

    def _populate_grid(self):
        self._icon_widgets.clear()
        # Vider la grille
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        last_row = 0
        for pos, icon_id in enumerate(self._layout):
            pm_n = self._get_pixmap(icon_id, gray=False)
            pm_g = self._get_pixmap(icon_id, gray=True)
            lbl = IconLabel(icon_id, pm_n, pm_g, self)
            lbl.setFixedSize(self._icon_size + ICON_PAD, self._icon_size + 20)  # 20 = pady=10 * 2
            row = pos // self._cols
            col = pos % self._cols
            self._grid_layout.addWidget(lbl, row, col)
            self._icon_widgets[icon_id] = lbl
            last_row = row

        # Ligne et colonne extensibles pour absorber l'espace restant
        # sans étirer les cellules d'icônes
        self._grid_layout.setRowStretch(last_row + 1, 1)
        self._grid_layout.setColumnStretch(self._cols, 1)

        self.refresh_states()

    # ── Pixmaps ───────────────────────────────────────────────────────────────

    def _get_pixmap(self, icon_id: str, gray: bool) -> QPixmap:
        cache = self._pm_cache_gray if gray else self._pm_cache
        if icon_id in cache:
            return cache[icon_id]
        defn = self._defs.get(icon_id)
        if defn:
            if defn.get("png"):
                img_path = os.path.join(self._icons_dir, defn["png"])
            elif defn.get("img_path"):
                img_path = resource_path(defn["img_path"])
            else:
                img_path = None
            if img_path and os.path.exists(img_path):
                try:
                    img = Image.open(img_path).convert("RGBA").resize(
                        (self._icon_size, self._icon_size), Image.LANCZOS
                    )
                    if gray:
                        img = _to_grayscale(img)
                    pm = _pil_to_qpixmap(img)
                    cache[icon_id] = pm
                    return pm
                except Exception:
                    pass
        # Placeholder
        pm = QPixmap(self._icon_size, self._icon_size)
        pm.fill(QColor(180, 180, 180) if gray else QColor(200, 200, 220))
        cache[icon_id] = pm
        return pm

    # ── États actif/grisé ─────────────────────────────────────────────────────

    def _is_active(self, icon_id: str) -> bool:
        rule = _ACTIVATION_RULES.get(icon_id)
        if rule is None:
            return True
        try:
            return bool(rule(self._state_getters))
        except Exception:
            return False

    def refresh_states(self):
        for icon_id, lbl in self._icon_widgets.items():
            lbl.set_active(self._is_active(icon_id))
        # Met à jour le pixmap de split_ui selon l'état courant
        split_lbl = self._icon_widgets.get("split_ui")
        if split_lbl:
            split_active = self._state_getters.get("split_active", lambda: False)()
            defn = self._defs.get("split_ui", {})
            png_key = "png_alt" if split_active else "png"
            png_file = defn.get(png_key)
            if png_file:
                cache_key = f"split_ui_{png_key}"
                if cache_key not in self._pm_cache:
                    img_path = os.path.join(self._icons_dir, png_file)
                    if os.path.exists(img_path):
                        try:
                            img = Image.open(img_path).convert("RGBA").resize(
                                (self._icon_size, self._icon_size), Image.LANCZOS
                            )
                            self._pm_cache[cache_key] = _pil_to_qpixmap(img)
                            self._pm_cache_gray[cache_key] = _pil_to_qpixmap(_to_grayscale(img))
                        except Exception:
                            pass
                pm_n = self._pm_cache.get(cache_key)
                pm_g = self._pm_cache_gray.get(cache_key)
                if pm_n:
                    split_lbl._pm_normal = pm_n
                if pm_g:
                    split_lbl._pm_gray = pm_g
                split_lbl.setPixmap(pm_n if split_lbl._active else pm_g)

    def set_hover_color(self, color: str):
        IconToolbarQt._hover_color = color
        if hasattr(self, "_overlay_tip"):
            self._overlay_tip._apply_style()

    def set_slider_theme(self, theme: str):
        if hasattr(self, "_thumb_size_slider"):
            self._thumb_size_slider.set_theme(theme)
        if hasattr(self, "_lang_combo"):
            self._lang_combo.set_theme(theme)

    def update_language_combo(self):
        """Met à jour la langue sélectionnée dans le combo (après changement de langue)."""
        if hasattr(self, "_lang_combo"):
            current = self._callbacks.get("get_current_language", lambda: "fr")()
            self._lang_combo.set_current_language(current)

    # ── Navigation clavier ────────────────────────────────────────────────────

    def _footer_rows(self) -> list:
        """Retourne les lignes du footer : chaque ligne est une liste de widgets focusables."""
        rows = []
        # Ligne 1 : [−] [⚙] [+]
        rows.append([self._btn_minus, self._btn_cfg, self._btn_plus])
        # Ligne 2 : slider et/ou combo (seulement si visibles)
        row2 = []
        if self._thumb_size_slider.isVisible():
            row2.append(self._thumb_size_slider._slider)
        if self._lang_combo.isVisible():
            row2.append(self._lang_combo._combo)
        if row2:
            rows.append(row2)
        # Ligne 3 : copyright
        rows.append([self._footer_copyright])
        return rows

    def _navigate_footer_horiz(self, widget, step: int):
        """Navigation ←→ dans la ligne courante du footer."""
        for row in self._footer_rows():
            if widget in row:
                new = row.index(widget) + step
                if 0 <= new < len(row):
                    row[new].setFocus()
                return

    def _navigate_footer(self, widget, direction: str):
        """Navigation ↑↓ par ligne dans le footer. ↑ depuis la 1re ligne → dernière icône."""
        rows = self._footer_rows()
        # Trouver la ligne courante
        cur_row = None
        for i, row in enumerate(rows):
            if widget in row:
                cur_row = i
                break
        if cur_row is None:
            return
        if direction == "up":
            if cur_row == 0:
                icons = list(self._icon_widgets.values())
                if icons:
                    icons[-1].setFocus()
            else:
                for w in rows[cur_row - 1]:
                    if w.isEnabled():
                        w.setFocus()
                        return
        elif direction == "down":
            if cur_row < len(rows) - 1:
                for w in rows[cur_row + 1]:
                    if w.isEnabled():
                        w.setFocus()
                        return

    def _navigate(self, widget: IconLabel, direction: str):
        ids    = list(self._icon_widgets.keys())
        widgs  = list(self._icon_widgets.values())
        try:
            cur = widgs.index(widget)
        except ValueError:
            return
        total = len(ids)
        if direction == "up":
            new = cur - self._cols
        elif direction == "down":
            new = cur + self._cols
        elif direction == "left":
            new = cur - 1
        elif direction == "right":
            new = cur + 1
        else:
            return
        if direction == "down":
            # Trouver la ligne courante et la dernière ligne via le grid layout
            item = self._grid_layout.itemAtPosition
            cur_row = None
            last_row = 0
            for i, w in enumerate(widgs):
                idx = self._grid_layout.indexOf(w)
                if idx < 0:
                    continue
                r, _, _, _ = self._grid_layout.getItemPosition(idx)
                if i == cur:
                    cur_row = r
                if r > last_row:
                    last_row = r
            if cur_row is not None and cur_row >= last_row:
                rows = self._footer_rows()
                for row in rows:
                    for w in row:
                        if w.isEnabled():
                            w.setFocus()
                            return
                return
        if 0 <= new < total:
            widgs[new].setFocus()

    def get_first_icon(self) -> QWidget | None:
        if self._icon_widgets:
            return next(iter(self._icon_widgets.values()))
        return None

    def get_icon_widgets(self) -> list[QWidget]:
        return list(self._icon_widgets.values())

    # ── Taille des icônes ─────────────────────────────────────────────────────

    def _set_btn_icon(self, btn: QPushButton, filename: str, size: int):
        img_path = os.path.join(self._icons_dir, filename)
        if os.path.exists(img_path):
            try:
                img = Image.open(img_path).convert("RGBA").resize((size, size), Image.LANCZOS)
                pm = _pil_to_qpixmap(img)
                btn.setIcon(QIcon(pm))
                btn.setIconSize(QSize(size, size))
                return
            except Exception:
                pass
        btn.setText({"BTN_-.png": "−", "BTN_+.png": "+", "BTN_Control.png": "⚙"}.get(filename, "?"))

    def _decrease_icon_size(self):
        if self._size_index < len(ICON_SIZE_LEVELS) - 1:
            self._size_index += 1
            self._apply_size_change()

    def _increase_icon_size(self):
        if self._size_index > 0:
            self._size_index -= 1
            self._apply_size_change()

    def _apply_size_change(self):
        self._icon_size = ICON_SIZE_LEVELS[self._size_index][0]
        self._cols      = ICON_SIZE_LEVELS[self._size_index][1]
        self._pm_cache.clear()
        self._pm_cache_gray.clear()
        if hasattr(self._config, "set_icon_size_index"):
            self._config.set_icon_size_index(self._size_index)
        # Recalcule le nombre de colonnes selon la largeur courante, puis reconstruit
        cur_w = self._scroll_area.viewport().width() if hasattr(self, "_scroll_area") else 0
        if cur_w > 0:
            cell_w = self._icon_size + ICON_PAD
            max_cols = ICON_SIZE_LEVELS[self._size_index][1]
            new_cols = max(1, min(max_cols, (cur_w - 2 * ICON_PAD) // cell_w))
            self._cols = new_cols
        fw = QApplication.focusWidget()
        focus_was_in_toolbar = False
        focused_btn_minus = fw is self._btn_minus
        focused_btn_plus  = fw is self._btn_plus
        w = fw
        while w is not None:
            if w is self:
                focus_was_in_toolbar = True
                break
            w = w.parent()
        self._populate_grid()
        self._update_size_buttons()
        if focus_was_in_toolbar:
            if focused_btn_minus and not self._btn_minus.isEnabled():
                QTimer.singleShot(0, self._btn_cfg.setFocus)
            elif focused_btn_plus and not self._btn_plus.isEnabled():
                QTimer.singleShot(0, self._btn_cfg.setFocus)
            else:
                QTimer.singleShot(0, fw.setFocus)
        cb = self._callbacks.get("on_icon_size_changed")
        if cb:
            cb(self._size_index)

    def _update_size_buttons(self):
        self._btn_minus.setEnabled(self._size_index < len(ICON_SIZE_LEVELS) - 1)
        self._btn_plus.setEnabled(self._size_index > 0)

    def adapt_cols_to_width(self, available_width: int):
        """Recalcule _cols selon la largeur disponible et re-peuple si nécessaire.
        Le nombre de colonnes est borné entre 1 et le nb_cols du niveau courant."""
        cell_w = self._icon_size + ICON_PAD
        max_cols = ICON_SIZE_LEVELS[self._size_index][1]
        new_cols = max(1, (available_width - 2 * ICON_PAD) // cell_w)
        new_cols = min(new_cols, max_cols)
        if new_cols != self._cols:
            self._cols = new_cols
            self._populate_grid()

    # ── Taille des vignettes ───────────────────────────────────────────────────

    def _on_thumb_size_change(self, index: int):
        """Appelé par ThumbSizeSlider quand l'utilisateur déplace la réglette."""
        self._thumb_size_index = index
        cb = self._callbacks.get("on_thumb_size_change")
        if cb:
            cb(index)

    def set_size_index(self, index: int):
        """Applique une taille d'icônes sans sauvegarder en config (usage inter-panneaux)."""
        self._size_index = max(0, min(len(ICON_SIZE_LEVELS) - 1, index))
        self._apply_size_change()

    def apply_layout(self, layout: list, show_thumb_slider: bool, show_lang_combo: bool):
        """Applique une disposition (layout + visibilité slider/combo) sans sauvegarder en config."""
        self._layout = [i for i in layout if i in self._defs]
        self._show_thumb_slider = show_thumb_slider
        self._show_lang_combo   = show_lang_combo
        self._thumb_size_slider.setVisible(show_thumb_slider)
        self._lang_combo.setVisible(show_lang_combo)
        self._slider_lang_row.setVisible(show_thumb_slider or show_lang_combo)
        self._populate_grid()

    def set_thumb_size_index(self, index: int):
        """Appelé depuis l'extérieur pour synchroniser la réglette sans déclencher le callback."""
        self._thumb_size_index = index
        if hasattr(self, "_thumb_size_slider"):
            self._thumb_size_slider.set_value(index)

    def update_language(self):
        """Met à jour les textes traduits de la toolbar (label réglette, tooltips)."""
        if hasattr(self, "_thumb_size_slider"):
            self._thumb_size_slider.update_language()
        if hasattr(self, "_btn_minus"):
            self._overlay_tip.set_tracked_html(_("tooltip.icon_toolbar_size_decrease"), self._btn_minus)
        if hasattr(self, "_btn_cfg"):
            self._overlay_tip.set_tracked_html(_("tooltip.icon_toolbar_config_button"), self._btn_cfg)
        if hasattr(self, "_btn_plus"):
            self._overlay_tip.set_tracked_html(_("tooltip.icon_toolbar_size_increase"), self._btn_plus)
        if hasattr(self, "_footer_copyright"):
            self._footer_copyright.update_text()

    # ── Menu contextuel mail ───────────────────────────────────────────────────

    def _show_mail_context_menu(self, global_pos):
        menu = QMenu(self)
        action = menu.addAction(_("mail.copy_address"))
        chosen = menu.exec(global_pos)
        if chosen == action:
            QApplication.clipboard().setText("mosaicview1969@gmail.com")

    # ── Noms d'affichage ──────────────────────────────────────────────────────

    _LABEL_KEYS = {
        "open_file":           "buttons.open_file",
        "close_file":          "buttons.close_file",
        "apply_save":          "buttons.save",
        "batch_cbr_cbz":       "buttons.batch_cbr_to_cbz",
        "batch_cb7_cbz":       "buttons.batch_cb7_to_cbz",
        "batch_cbt_cbz":       "buttons.batch_cbt_to_cbz",
        "batch_pdf_cbz":       "buttons.batch_pdf_to_cbz",
        "batch_img_cbz":       "buttons.batch_img_to_cbz",
        "undo":                "buttons.undo",
        "redo":                "buttons.redo",
        "flatten_directories": "buttons.flatten_dirs",
        "web_import":          "web.import_web_button",
        "delete_selected":     "buttons.delete_selected",
        "copy_selected":       "buttons.copy",
        "cut_selected":        "buttons.cut",
        "paste":               "buttons.paste",
        "rotate_left":         "tooltip.rotate_left",
        "rotate_right":        "tooltip.rotate_right",
        "flip_horizontal":     "tooltip.mirror_horizontal",
        "flip_vertical":       "tooltip.mirror_vertical",
        "convert":             "buttons.convert",
        "resize":              "buttons.reduce_size",
        "adjustments":         "buttons.adjustments",
        "crop":                "context_menu.image.crop",
        "create_ico":          "context_menu.image.create_ico",
        "join_pages":          "buttons.join_pages",
        "split_page":          "buttons.split_page",
        "print_selection":     "buttons.print_selection",
        "print_all":           "buttons.print_all",
        "renumber":            "buttons.renumber",
        "sort":                "menu.sort",
        "open_mail":           "mail.icon_label",
        "donation":            "donation.menu_label",
        "toggle_theme":        "tooltip.theme_button",
        "show_user_guide":     "tooltip.help_button",
        "full_screen":         "tooltip.fullscreen",
        "decrease_font_size":  "tooltip.font_decrease_button",
        "increase_font_size":  "tooltip.font_increase_button",
        "reset_to_defaults":   "context_menu.canvas.reset_label",
        "straighten":          "tooltip.straighten",
        "clone_zone":          "tooltip.clone_zone",
        "text":                "tooltip.text",
    }

    def _get_icon_label(self, icon_id: str) -> str:
        import re, unicodedata
        key = self._LABEL_KEYS.get(icon_id)
        if not key:
            return icon_id
        raw = _(key)
        raw = re.sub(r"[\n\r]", " ", raw)
        raw = "".join(c for c in raw if unicodedata.category(c) not in ("So", "Sm"))
        return raw.strip()

    # ── Fenêtre de configuration ───────────────────────────────────────────────

    def _open_config_window(self):
        all_known  = [d["id"] for d in ICON_DEFINITIONS]
        active_ids = [i for i in self._layout if i in self._defs]
        hidden_ids = [i for i in all_known if i not in active_ids]

        sel_active: set = set()
        sel_hidden: set = set()

        THUMB = 56
        COLS  = 4
        photo_cache: dict = {}

        def get_thumb(icon_id: str) -> QPixmap:
            if icon_id in photo_cache:
                return photo_cache[icon_id]
            defn = self._defs.get(icon_id)
            if defn:
                if defn.get("png"):
                    img_path = os.path.join(self._icons_dir, defn["png"])
                elif defn.get("img_path"):
                    img_path = resource_path(defn["img_path"])
                else:
                    img_path = None
                if img_path and os.path.exists(img_path):
                    try:
                        img = Image.open(img_path).convert("RGBA").resize(
                            (THUMB, THUMB), Image.LANCZOS
                        )
                        pm = _pil_to_qpixmap(img)
                        photo_cache[icon_id] = pm
                        return pm
                    except Exception:
                        pass
            pm = QPixmap(THUMB, THUMB)
            pm.fill(QColor(200, 200, 220))
            photo_cache[icon_id] = pm
            return pm

        dlg = QDialog(self)
        dlg.setWindowTitle(_wt("labels.icon_toolbar_config_title"))
        dlg.setModal(True)
        dlg.resize(1100, 580)

        root_layout = QVBoxLayout(dlg)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(6)

        from modules.qt.font_manager_qt import get_current_font as _gcf
        title_lbl = QLabel(_("labels.icon_toolbar_config_title"))
        title_lbl.setAlignment(Qt.AlignCenter)
        title_lbl.setFont(_gcf(11))
        root_layout.addWidget(title_lbl)

        main_widget = QWidget()
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(6)
        root_layout.addWidget(main_widget, stretch=1)

        def make_panel(header_key: str):
            frame = QWidget()
            vlay  = QVBoxLayout(frame)
            vlay.setContentsMargins(0, 0, 0, 0)
            vlay.setSpacing(4)
            hdr = QLabel(_(header_key))
            hdr.setAlignment(Qt.AlignCenter)
            hdr.setFont(_gcf(9))
            hdr._key = header_key
            vlay.addWidget(hdr)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setFrameShape(QFrame.StyledPanel)
            scroll.setMinimumWidth(500)
            scroll.setMinimumHeight(460)
            vlay.addWidget(scroll, stretch=1)
            grid_widget = QWidget()
            grid_layout = QGridLayout(grid_widget)
            grid_layout.setSpacing(4)
            grid_layout.setContentsMargins(6, 6, 6, 6)
            scroll.setWidget(grid_widget)
            frame._grid_layout = grid_layout
            frame._hdr = hdr
            return frame

        left_panel  = make_panel("labels.icon_toolbar_active")
        right_panel = make_panel("labels.icon_toolbar_hidden")

        arrow_widget = QWidget()
        arrow_layout = QVBoxLayout(arrow_widget)
        arrow_layout.setContentsMargins(4, 0, 4, 0)
        arrow_layout.setSpacing(24)
        arrow_layout.setAlignment(Qt.AlignVCenter)
        btn_to_hidden = QPushButton()
        btn_to_hidden.setFixedSize(40, 40)
        btn_to_hidden.setCursor(Qt.PointingHandCursor)
        self._set_btn_icon(btn_to_hidden, "BTN_Arrow_RIGHT.png", 32)
        btn_to_active = QPushButton()
        btn_to_active.setFixedSize(40, 40)
        btn_to_active.setCursor(Qt.PointingHandCursor)
        self._set_btn_icon(btn_to_active, "BTN_Arrow_LEFT.png", 32)
        arrow_layout.addWidget(btn_to_hidden)
        arrow_layout.addWidget(btn_to_active)

        main_layout.addWidget(left_panel,   stretch=1)
        main_layout.addWidget(arrow_widget, stretch=0)
        main_layout.addWidget(right_panel,  stretch=1)

        from PySide6.QtWidgets import QCheckBox

        chk_thumb = QCheckBox(_("labels.icon_toolbar_show_thumb_slider"))
        chk_thumb.setFont(_gcf(9))
        chk_thumb.setChecked(self._show_thumb_slider)

        chk_lang  = QCheckBox(_("labels.icon_toolbar_show_lang_combo"))
        chk_lang.setFont(_gcf(9))
        chk_lang.setChecked(self._show_lang_combo)

        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(8)
        btn_reset  = QPushButton(_("buttons.icon_toolbar_reset"))
        btn_reset.setCursor(Qt.PointingHandCursor)
        btn_reset.setFont(_gcf(9))
        btn_ok     = QPushButton(_("buttons.ok"))
        btn_ok.setCursor(Qt.PointingHandCursor)
        btn_ok.setFont(_gcf(9))
        btn_cancel = QPushButton(_("buttons.cancel"))
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.setFont(_gcf(9))
        bottom_layout.addWidget(btn_reset)
        bottom_layout.addStretch()
        bottom_layout.addWidget(chk_thumb)
        bottom_layout.addSpacing(16)
        bottom_layout.addWidget(chk_lang)
        bottom_layout.addStretch()
        bottom_layout.addWidget(btn_ok)
        bottom_layout.addWidget(btn_cancel)
        root_layout.addWidget(bottom)

        def render_panel(panel, id_list: list, sel_set: set):
            gl = panel._grid_layout
            while gl.count():
                item = gl.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            for pos, icon_id in enumerate(id_list):
                row = pos // COLS
                col = pos % COLS
                is_sel = icon_id in sel_set

                CELL_W = 110
                CELL_H = THUMB + 72

                cell = QWidget()
                cell.setFixedSize(CELL_W, CELL_H)
                cell_layout = QVBoxLayout(cell)
                cell_layout.setContentsMargins(3, 3, 3, 3)
                cell_layout.setSpacing(2)
                cell_layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
                if is_sel:
                    cell.setStyleSheet("background: #3a7bd5; border-radius: 4px;")

                img_lbl = QLabel()
                img_lbl.setPixmap(get_thumb(icon_id))
                img_lbl.setAlignment(Qt.AlignCenter)
                cell_layout.addWidget(img_lbl)

                txt_lbl = QLabel(self._get_icon_label(icon_id))
                txt_lbl.setAlignment(Qt.AlignCenter)
                txt_lbl.setWordWrap(True)
                txt_lbl.setFixedWidth(CELL_W - 6)
                txt_lbl.setFont(_gcf(8))
                cell_layout.addWidget(txt_lbl)

                def _on_click(checked=False, iid=icon_id, p=panel, sel=sel_set):
                    if iid in sel:
                        sel.discard(iid)
                    else:
                        sel.add(iid)
                    if p is left_panel:
                        sel_hidden.clear()
                        render_panel(right_panel, hidden_ids, sel_hidden)
                    else:
                        sel_active.clear()
                        render_panel(left_panel, active_ids, sel_active)
                    render_panel(p, id_list, sel)

                for w in (cell, img_lbl, txt_lbl):
                    w.mousePressEvent = lambda e, fn=_on_click: fn()

                gl.addWidget(cell, row, col)

            last_row = max(len(id_list) - 1, 0) // COLS
            gl.setRowStretch(last_row + 1, 1)

        def refresh_all():
            render_panel(left_panel,  active_ids, sel_active)
            render_panel(right_panel, hidden_ids, sel_hidden)

        def _retranslate(_lang=None):
            dlg.setWindowTitle(_wt("labels.icon_toolbar_config_title"))
            title_lbl.setText(_("labels.icon_toolbar_config_title"))
            title_lbl.setFont(_gcf(11))
            left_panel._hdr.setText(_("labels.icon_toolbar_active"))
            left_panel._hdr.setFont(_gcf(9))
            right_panel._hdr.setText(_("labels.icon_toolbar_hidden"))
            right_panel._hdr.setFont(_gcf(9))
            chk_thumb.setText(_("labels.icon_toolbar_show_thumb_slider"))
            chk_thumb.setFont(_gcf(9))
            chk_lang.setText(_("labels.icon_toolbar_show_lang_combo"))
            chk_lang.setFont(_gcf(9))
            btn_reset.setText(_("buttons.icon_toolbar_reset"))
            btn_reset.setFont(_gcf(9))
            btn_ok.setText(_("buttons.ok"))
            btn_ok.setFont(_gcf(9))
            btn_cancel.setText(_("buttons.cancel"))
            btn_cancel.setFont(_gcf(9))
            refresh_all()

        from modules.qt.language_signal import language_signal
        language_signal.changed.connect(_retranslate)
        dlg.finished.connect(lambda: language_signal.changed.disconnect(_retranslate))

        def move_to_hidden():
            if not sel_active:
                return
            for iid in list(sel_active):
                if iid in active_ids:
                    active_ids.remove(iid)
                    hidden_ids.append(iid)
            sel_active.clear()
            refresh_all()

        def move_to_active():
            if not sel_hidden:
                return
            for iid in list(sel_hidden):
                if iid in hidden_ids:
                    hidden_ids.remove(iid)
                    active_ids.append(iid)
            sel_hidden.clear()
            refresh_all()

        def do_reset():
            active_ids.clear()
            hidden_ids.clear()
            active_ids.extend(all_known)
            sel_active.clear()
            sel_hidden.clear()
            chk_thumb.setChecked(True)
            chk_lang.setChecked(True)
            refresh_all()

        def do_ok():
            self._layout = list(active_ids)
            if hasattr(self._config, "set_icon_toolbar_layout"):
                self._config.set_icon_toolbar_layout(self._layout)
            self._show_thumb_slider = chk_thumb.isChecked()
            self._show_lang_combo   = chk_lang.isChecked()
            if hasattr(self._config, "set_show_thumb_slider"):
                self._config.set_show_thumb_slider(self._show_thumb_slider)
            if hasattr(self._config, "set_show_lang_combo"):
                self._config.set_show_lang_combo(self._show_lang_combo)
            self._thumb_size_slider.setVisible(self._show_thumb_slider)
            self._lang_combo.setVisible(self._show_lang_combo)
            self._slider_lang_row.setVisible(self._show_thumb_slider or self._show_lang_combo)
            self._populate_grid()
            dlg.accept()

        btn_to_hidden.clicked.connect(move_to_hidden)
        btn_to_active.clicked.connect(move_to_active)
        btn_reset.clicked.connect(do_reset)
        btn_ok.clicked.connect(do_ok)
        btn_cancel.clicked.connect(dlg.reject)

        refresh_all()
        dlg.exec()

    # ── Réordonnancement par drag & drop ──────────────────────────────────────

    def _reorder_by_drop(self, src_id: str, drop_pos: QPoint, insert_idx: int | None = None):
        if src_id not in self._layout:
            return
        src_pos = self._layout.index(src_id)

        if insert_idx is None:
            # Fallback : calcul depuis drop_pos (ne devrait pas arriver en pratique)
            insert_idx = len(self._layout)
            for icon_id, lbl in self._icon_widgets.items():
                if lbl.geometry().contains(drop_pos):
                    insert_idx = self._layout.index(icon_id)
                    break

        # Éviter les no-ops : insérer juste avant ou juste après src ne change rien
        if insert_idx == src_pos or insert_idx == src_pos + 1:
            return

        self._layout.remove(src_id)
        # Après suppression, les indices > src_pos se décalent de -1
        adjusted = insert_idx if insert_idx <= src_pos else insert_idx - 1
        self._layout.insert(adjusted, src_id)
        if hasattr(self._config, "set_icon_toolbar_layout"):
            self._config.set_icon_toolbar_layout(self._layout)
        self._populate_grid()


# ═══════════════════════════════════════════════════════════════════════════════
# Factory — construction et câblage de la toolbar depuis MainWindow
# ═══════════════════════════════════════════════════════════════════════════════
def build_icon_toolbar(mw, *, is_primary=True) -> "IconToolbarQt":
    """Instancie IconToolbarQt avec les state_getters et callbacks issus de mw.

    ``mw`` est l'instance MainWindow ; aucune importation circulaire car on
    reçoit l'objet par paramètre.
    Retourne l'instance créée (à assigner à mw._icon_toolbar).
    """
    from modules.qt.config_manager import get_config_manager
    from modules.qt.menubar_callbacks_qt import build_menubar_callbacks

    st = mw._state
    if is_primary:
        cb = build_menubar_callbacks(mw)
    else:
        cb = mw._build_menubar_callbacks()

    state_getters = {
        "has_file":              lambda: st.current_file is not None,
        "has_images":            lambda: bool(st.images_data),
        "is_modified":           lambda: bool(st.modified),
        "has_selection":         lambda: len(st.selected_indices) > 0,
        "selection_count":       lambda: len(st.selected_indices),
        "has_selected_images":   lambda: bool(st.selected_indices) and any(
            st.images_data[i].get("is_image", False)
            for i in st.selected_indices if i < len(st.images_data)
        ),
        "has_undo":              lambda: can_undo(st),
        "has_redo":              lambda: can_redo(st),
        "has_subdirs":           lambda: any(
            e.get("is_dir") or ('/' in e.get("orig_name", "") and not e.get("is_dir"))
            for e in st.images_data
        ),
        "needs_renumbering":     lambda: bool(getattr(st, "needs_renumbering", False)),
        "renumber_mode":         lambda: getattr(st, "renumber_mode", 1),
        "print_available":       lambda: PRINT_AVAILABLE,
        "single_image_selected": lambda: (
            len(st.selected_indices) == 1 and bool(st.selected_indices) and
            (lambda idx: idx < len(st.images_data)
                and st.images_data[idx].get("is_image", False)
                and not st.images_data[idx].get("is_corrupted", False)
            )(next(iter(st.selected_indices)))
            if st.selected_indices else False
        ),
        "split_active":          lambda: mw._split_active,
    }

    toolbar_callbacks = {
        "open_file":             cb["open_file"],
        "close_file":            cb["close_file"],
        "apply_save":            lambda: (
            cb["create_cbz_from_images"]() if not st.current_file and st.images_data else
            cb["apply_new_names"]()        if st.current_file and st.modified else
            cb["save_as_cbz"]()            if st.current_file and not st.modified else
            None
        ),
        "batch_cbr_cbz":         cb["batch_convert_cbr_to_cbz"],
        "batch_cb7_cbz":         cb["batch_convert_cb7_to_cbz"],
        "batch_cbt_cbz":         cb["batch_convert_cbt_to_cbz"],
        "batch_pdf_cbz":         cb["batch_convert_pdf_to_cbz"],
        "batch_img_cbz":         cb["batch_convert_img_to_cbz"],
        "undo":                  cb["undo_action"],
        "redo":                  cb["redo_action"],
        "flatten_directories":   cb["flatten_directories"],
        "web_import":            cb["show_web_import_dialog"],
        "delete_selected":       cb["delete_selected"],
        "copy_selected":         cb["copy_selected"],
        "cut_selected":          cb["cut_selected"],
        "paste":                 cb["paste_ctrl_v"],
        "renumber":              mw._renumber_btn_action,
        "toggle_renumber_mode":  mw._toggle_renumber_mode,
        "rotate_left":           cb["rotate_selected_left"],
        "rotate_right":          cb["rotate_selected_right"],
        "flip_horizontal":       cb["flip_selected_horizontal"],
        "flip_vertical":         cb["flip_selected_vertical"],
        "convert":               cb["convert_selected_images"],
        "resize":                cb["reduce_selected_images_size"],
        "adjustments":           cb["show_image_adjustments_dialog"],
        "straighten":            cb["show_straighten_viewer"],
        "clone_zone":            cb["show_clone_zone_viewer"],
        "text":                  cb["show_text_viewer"],
        "crop":                  cb["crop_selected_image"],
        "create_ico":            cb["create_ico_from_selected"],
        "join_pages":            cb["open_merge_window"],
        "split_page":            cb["split_page"],
        "print_selection":       lambda: _print_selection(mw, mw._canvas, st),
        "print_all":             lambda: _print_all(mw, mw._canvas, st),
        "sort":                  mw._show_sort_menu,
        "open_mail":             lambda: webbrowser.open("mailto:mosaicview1969@gmail.com?subject=MosaicView"),
        "donation":              mw._show_donation_dialog,
        "show_license_dialog":   mw._show_license_dialog,
        "show_full_gpl_license": mw._show_full_gpl_license,
        "show_full_unrar_license": mw._show_full_unrar_license,
        "show_full_7zip_license": mw._show_full_7zip_license,
        "show_full_piqad_license": mw._show_full_piqad_license,
        "toggle_theme":          mw._toggle_theme,
        "show_user_guide":       mw._show_user_guide,
        "full_screen":           mw._toggle_fullscreen,
        "decrease_font_size":    mw._decrease_font_size,
        "increase_font_size":    mw._increase_font_size,
        "reset_to_defaults":     mw._reset_to_defaults,
        "split_ui":              mw._toggle_split_ui,
        "on_thumb_size_change":  mw._on_thumb_size_change,
        "on_icon_size_changed":  mw._update_splitter_constraints,
        "get_languages":         lambda: mw._language_list,
        "get_current_language":  lambda: mw._loc.get_current_language(),
        "change_language":       mw._on_language_change,
    }

    icons_dir = resource_path("icons")
    if is_primary:
        config = get_config_manager()
    else:
        from modules.qt.config_manager import Panel2Config
        config = Panel2Config(get_config_manager())
    return IconToolbarQt(
        mw._left_panel, toolbar_callbacks, state_getters,
        config, icons_dir,
    )
