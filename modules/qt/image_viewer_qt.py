"""
modules/qt/image_viewer_qt.py — Visionneuse d'images (version PySide6)

Reproduit à l'identique le comportement de modules/image_viewer.py (tkinter).
"""

import io

from PIL import Image

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QMenu, QFrame, QDoubleSpinBox,
)
from PySide6.QtCore import Qt, QTimer, QPoint, QElapsedTimer
from PySide6.QtGui import QPixmap, QImage, QKeySequence, QShortcut

from modules.qt import state as _state_module
from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.entries import (
    ensure_image_loaded, free_image_memory, get_gif_frame,
)
from modules.qt.dialogs_qt import MsgDialog, ConfirmYNDialog
from modules.qt.page_detection import compute_reference_ratio
# Outils de la barre d'outils flottante — code dans leur propre module,
# jamais dans ce fichier (CLAUDE.md, fusion des visionneuses idees.txt #3).
from modules.qt.crop_tool_qt import CropCanvasMixin, CropViewerMixin
from modules.qt.straighten_tool_qt import StraightenCanvasMixin, StraightenViewerMixin
from modules.qt.clone_tool_qt import CloneCanvasMixin, CloneViewerMixin
from modules.qt.text_tool_qt import TextCanvasMixin, TextViewerMixin
# La barre d'outils elle-même (composant transversal, pas un outil) — même
# principe de séparation, voir viewer_toolbar_qt.py.
from modules.qt.viewer_toolbar_qt import _ViewerToolbar

# Liste globale des visionneuses ouvertes (pour mise à jour de langue)
image_viewer_refs = []


# ─────────────────────────────────────────────────────────────────────────────
# Helpers PIL → QPixmap
# ─────────────────────────────────────────────────────────────────────────────

def _pil_to_qpixmap(img: Image.Image) -> QPixmap:
    """Convertit une PIL Image en QPixmap (sans perte)."""
    img_rgba = img.convert("RGBA")
    data = img_rgba.tobytes("raw", "RGBA")
    qimg = QImage(data, img_rgba.width, img_rgba.height, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qimg)


def _compose_on_checkerboard(img: Image.Image, tile: int = 16) -> Image.Image:
    """Compose une image RGBA sur un fond damier (gris clair/foncé)."""
    from modules.qt.entries import _make_checkerboard_pil
    bg = _make_checkerboard_pil(img.width, img.height, tile=tile)
    img_rgba = img.convert("RGBA")
    bg.paste(img_rgba, (0, 0), img_rgba)
    return bg


# ─────────────────────────────────────────────────────────────────────────────
# Canvas de visionneuse (zone noire avec image centrée + rubber-band crop)
# ─────────────────────────────────────────────────────────────────────────────

class _ViewerCanvas(CropCanvasMixin, StraightenCanvasMixin, CloneCanvasMixin, TextCanvasMixin, QLabel):
    """
    QLabel utilisé comme zone d'affichage de l'image.
    Gère :
      - affichage d'un QPixmap centré sur fond noir
      - rubber-band (rectangle rouge) pour le recadrage
      - pan clic-droit
      - double-clic plein écran / validation crop
    """

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(0, 0)

    def minimumSizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(0, 0)

    def __init__(self, viewer: "ImageViewer"):
        super().__init__()
        self._viewer = viewer
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background: black;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(1, 1)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)

        # Infos image affichée (pour conversion coordonnées → image originale)
        self.display_offset_x = 0
        self.display_offset_y = 0
        self.display_width    = 0
        self.display_height   = 0

        # Pan clic-droit
        self._pan_start: QPoint | None = None
        self._is_panning = False

        # Décalage de pan persistant (relatif au centrage), conservé entre les zooms
        self.pan_offset_x = 0
        self.pan_offset_y = 0

        # Ignoré transitoirement après un double-clic (validation crop OU
        # bascule plein écran général — voir mouseDoubleClickEvent) pour
        # éviter qu'un clic résiduel ne déclenche un nouveau geste.
        self._ignore_crop_events = False

        # État de l'outil "crop" (voir crop_tool_qt.py::CropCanvasMixin,
        # hérité par cette classe — CLAUDE.md : ne jamais migrer le code d'un
        # outil dans image_viewer_qt.py).
        self._init_crop_state()

        # Bouton Valider (flottant, partagé crop + straighten — texte selon l'outil actif)
        self._validate_btn: QPushButton | None = None
        self._validate_btn_visible = False
        self._validate_btn_connected_tool: str | None = None

        # État de l'outil "straighten" manuel (voir straighten_tool_qt.py::
        # StraightenCanvasMixin, hérité par cette classe — CLAUDE.md : ne
        # jamais migrer le code d'un outil dans image_viewer_qt.py).
        self._init_straighten_state()

        # État de l'outil "clone" (voir clone_tool_qt.py::CloneCanvasMixin,
        # hérité par cette classe — CLAUDE.md : ne jamais migrer le code d'un
        # outil dans image_viewer_qt.py).
        self._init_clone_state()

        # État de l'outil "text" (voir text_tool_qt.py::TextCanvasMixin,
        # hérité par cette classe — CLAUDE.md : ne jamais migrer le code d'un
        # outil dans image_viewer_qt.py).
        self._init_text_state()

    # Méthodes de l'outil "crop" (has_crop, clear_crop, _get_resize_mode...)
    # fournies par CropCanvasMixin (crop_tool_qt.py), de l'outil "straighten"
    # manuel (has_line, clear_line, set_line_end_from_angle...) fournies par
    # StraightenCanvasMixin (straighten_tool_qt.py), et de l'outil "clone"
    # (clear_clone_source, set_clone_mode, set_clone_brush_radius...) fournies
    # par CloneCanvasMixin (clone_tool_qt.py), héritées par cette classe.

    # ── Bouton Valider (partagé crop + straighten) ───────────────────────────
    # Le texte et l'action dépendent de l'outil actif au moment de l'affichage
    # (self._viewer._toolbar.active_tool) — un seul bouton flottant réutilisé
    # par les deux outils plutôt qu'un par outil.

    _VALIDATE_KEYS = {
        "crop": "buttons.validate_crop",
        "straighten": "buttons.validate_straighten",
        "text": "buttons.validate_text",
    }

    def _ensure_validate_btn(self):
        if self._validate_btn is None:
            theme = get_current_theme()
            font = _get_current_font(12, bold=True)
            self._validate_btn = QPushButton(self)
            self._validate_btn.setFont(font)
            self._validate_btn.setStyleSheet(
                f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
                f"border: 1px solid #aaaaaa; padding: 6px 12px; }}"
                f"QPushButton:hover {{ background: {theme['separator']}; }}"
            )
            self._validate_btn.setFixedWidth(200)

    def _show_validate_btn(self):
        self._ensure_validate_btn()
        w = self._validate_btn
        tool = self._viewer._toolbar.active_tool
        key = self._VALIDATE_KEYS.get(tool)
        if key is None:
            return
        if self._validate_btn_connected_tool != tool:
            if self._validate_btn_connected_tool is not None:
                w.clicked.disconnect()
            if tool == "crop":
                w.clicked.connect(self._viewer.validate_crop)
            elif tool == "text":
                w.clicked.connect(self._viewer.validate_text)
            else:
                w.clicked.connect(self._viewer.validate_straighten)
            self._validate_btn_connected_tool = tool
        w.setText(_(key))
        # Positionné en bas au centre
        bw = w.sizeHint().width()
        bh = w.sizeHint().height()
        x = (self.width() - bw) // 2
        y = int(self.height() * 0.92) - bh // 2
        w.setGeometry(x, y, bw, bh)
        w.show()
        w.raise_()
        self._validate_btn_visible = True

    def _hide_validate_btn(self):
        if self._validate_btn is not None:
            self._validate_btn.hide()
        self._validate_btn_visible = False

    def retranslate_validate_btn(self):
        if self._validate_btn is not None and self._validate_btn_visible:
            tool = self._viewer._toolbar.active_tool
            key = self._VALIDATE_KEYS.get(tool)
            if key is not None:
                self._validate_btn.setText(_(key))
            font = _get_current_font(12, bold=True)
            self._validate_btn.setFont(font)

    # ── Affichage du pixmap ──────────────────────────────────────────────────

    def set_pixmap_and_geometry(self, pixmap: QPixmap,
                                offset_x: int, offset_y: int,
                                disp_w: int, disp_h: int):
        # Stocke sans appeler setPixmap() pour ne pas déclencher updateGeometry()
        self._current_pixmap  = pixmap
        self.display_offset_x = offset_x
        self.display_offset_y = offset_y
        self.display_width    = disp_w
        self.display_height   = disp_h
        self.update()

    # ── Dessin (image + rubber-band) ─────────────────────────────────────────

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QPen, QColor
        from PySide6.QtCore import QRectF
        painter = QPainter(self)

        # Fond noir
        painter.fillRect(self.rect(), QColor("black"))

        # Image centrée : le pixmap est stocké à sa résolution source (jamais
        # redimensionné en PIL pour le zoom) — c'est Qt qui l'étire à l'affichage
        # via drawPixmap(rect cible), beaucoup plus rapide qu'un resize() PIL
        # répété à chaque cran de molette.
        pm = getattr(self, '_current_pixmap', None)
        if pm and not pm.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            target = QRectF(self.display_offset_x, self.display_offset_y,
                             self.display_width, self.display_height)
            painter.drawPixmap(target, pm, QRectF(pm.rect()))

        # Rubber-band de recadrage (outil "crop", voir crop_tool_qt.py::
        # CropCanvasMixin.paint_crop_rect).
        self.paint_crop_rect(painter)

        # Trait de redressage manuel (outil "straighten", voir
        # straighten_tool_qt.py::StraightenCanvasMixin.paint_straighten_line).
        self.paint_straighten_line(painter)

        # Marqueur de source du tampon de clonage (outil "clone", voir
        # clone_tool_qt.py::CloneCanvasMixin.paint_clone_marker).
        self.paint_clone_marker(painter)

        # Barre de progression verticale (mode Webtoon uniquement) : indique la
        # position de scroll dans une page qui dépasse de la fenêtre en hauteur.
        if self._viewer.page_mode == "webtoon" and self.display_height > self.height():
            track_w = 4
            track_x = self.width() - track_w - 4
            track_h = self.height()
            painter.fillRect(track_x, 0, track_w, track_h, QColor(255, 255, 255, 40))
            visible_ratio = min(1.0, self.height() / self.display_height)
            thumb_h = max(20, int(track_h * visible_ratio))
            scroll_range = self.display_height - self.height()
            scroll_pos = -self.display_offset_y
            progress = 0.0 if scroll_range <= 0 else max(0.0, min(1.0, scroll_pos / scroll_range))
            thumb_y = int((track_h - thumb_h) * progress)
            painter.fillRect(track_x, thumb_y, track_w, thumb_h, QColor(255, 255, 255, 160))

        painter.end()

    # ── Gestion resize mode ──────────────────────────────────────────────────

    def _get_resize_mode(self, pos: QPoint) -> str | None:
        if not self.has_crop:
            return None
        tolerance = 10
        x1 = min(self._crop_start.x(), self._crop_end.x())
        x2 = max(self._crop_start.x(), self._crop_end.x())
        y1 = min(self._crop_start.y(), self._crop_end.y())
        y2 = max(self._crop_start.y(), self._crop_end.y())
        x, y = pos.x(), pos.y()
        if abs(x - x1) <= tolerance and abs(y - y1) <= tolerance: return 'tl'
        if abs(x - x2) <= tolerance and abs(y - y1) <= tolerance: return 'tr'
        if abs(x - x1) <= tolerance and abs(y - y2) <= tolerance: return 'bl'
        if abs(x - x2) <= tolerance and abs(y - y2) <= tolerance: return 'br'
        if abs(x - x1) <= tolerance and y1 <= y <= y2: return 'left'
        if abs(x - x2) <= tolerance and y1 <= y <= y2: return 'right'
        if abs(y - y1) <= tolerance and x1 <= x <= x2: return 'top'
        if abs(y - y2) <= tolerance and x1 <= x <= x2: return 'bottom'
        if x1 < x < x2 and y1 < y < y2: return 'move'
        return None

    # ── Événements souris ────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self._pan_start = event.position().toPoint()
            self._is_panning = False
            return

        if event.button() != Qt.LeftButton:
            return

        if self._ignore_crop_events:
            return

        active_tool = self._viewer._toolbar.active_tool
        if active_tool == "straighten":
            self.straighten_mouse_press(event)
            return

        if active_tool == "clone":
            self.clone_mouse_press(event)
            return

        if active_tool == "text":
            self.text_mouse_press(event)
            return

        if active_tool != "crop":
            return

        self.crop_mouse_press(event)

    def mouseMoveEvent(self, event):
        self._viewer._toolbar.on_canvas_mouse_move(int(event.position().y()), self.height())

        # Pan clic-droit
        if event.buttons() & Qt.RightButton and self._pan_start is not None:
            delta = event.position().toPoint() - self._pan_start
            if abs(delta.x()) > 5 or abs(delta.y()) > 5:
                self._is_panning = True
                self.setCursor(Qt.SizeAllCursor)
                self.pan_offset_x += delta.x()
                self.pan_offset_y += delta.y()
                self._pan_start = event.position().toPoint()
                if self.has_crop or self.has_text_blocks:
                    self._viewer.display_image(keep_crop_rect=True)
                else:
                    self.display_offset_x += delta.x()
                    self.display_offset_y += delta.y()
                    self.update()
            return

        active_tool = self._viewer._toolbar.active_tool

        if not (event.buttons() & Qt.LeftButton):
            # Mise à jour du curseur selon la position sur le cadre (uniquement si
            # l'outil crop est actif : un rectangle conservé mais désélectionné
            # n'est ni redimensionnable ni déplaçable, voir set_active_tool)
            if self.has_crop and active_tool == "crop":
                self.crop_update_cursor(event)
            elif self.has_line and active_tool == "straighten":
                self.straighten_update_cursor(event)
            elif active_tool == "clone":
                self.clone_update_cursor(event)
            elif active_tool == "text":
                self.text_update_cursor(event)
            else:
                self.setCursor(Qt.ArrowCursor)
            return

        if active_tool == "straighten":
            if self._ignore_crop_events:
                return
            self.straighten_mouse_move(event)
            return

        if active_tool == "clone":
            if self._ignore_crop_events:
                return
            self.clone_mouse_move(event)
            return

        if active_tool == "text":
            if self._ignore_crop_events:
                return
            self.text_mouse_move(event)
            return

        if self._ignore_crop_events:
            return
        if active_tool != "crop":
            return

        self.crop_mouse_move(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self.setCursor(Qt.ArrowCursor)
            if not self._is_panning:
                self._viewer._show_context_menu(event.globalPosition().toPoint())
            self._pan_start  = None
            self._is_panning = False
            return

        if event.button() != Qt.LeftButton:
            return
        if self._ignore_crop_events:
            return

        if self._viewer._toolbar.active_tool == "straighten":
            self.straighten_mouse_release(event)
            return

        if self._viewer._toolbar.active_tool == "clone":
            self.clone_mouse_release(event)
            return

        if self._viewer._toolbar.active_tool == "text":
            self.text_mouse_release(event)
            return

        self.crop_mouse_release(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self._ignore_crop_events = True
        self._waiting_for_double_click = False

        pos = event.position().toPoint()

        if self.has_crop and self._viewer._toolbar.active_tool == "crop":
            x1 = min(self._crop_start.x(), self._crop_end.x())
            y1 = min(self._crop_start.y(), self._crop_end.y())
            x2 = max(self._crop_start.x(), self._crop_end.x())
            y2 = max(self._crop_start.y(), self._crop_end.y())
            if x1 <= pos.x() <= x2 and y1 <= pos.y() <= y2:
                self._viewer._validating_crop = True
                self._viewer.validate_crop()
                self._viewer._validating_crop = False
                QTimer.singleShot(100, lambda: setattr(self, '_ignore_crop_events', False))
                return

        if not self._viewer._validating_crop:
            if self._crop_start is not None and self._crop_end is None:
                self._crop_start = None
                self.update()
            self._viewer.toggle_fullscreen()

        QTimer.singleShot(1000, lambda: setattr(self, '_ignore_crop_events', False))

    def wheelEvent(self, event):
        self._viewer._on_wheel(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._validate_btn_visible and self._validate_btn is not None:
            self._show_validate_btn()
        if self._viewer._toolbar.isVisible():
            self._viewer._toolbar.reposition()
        angle_panel = self._viewer._toolbar._angle_panel
        if angle_panel.isVisible():
            angle_panel.reposition()
        clone_panel = self._viewer._toolbar._clone_panel
        if clone_panel.isVisible():
            clone_panel.reposition()
        text_panel = self._viewer._toolbar._text_panel
        if text_panel.isVisible():
            text_panel.reposition()
        if self.has_text_blocks:
            self.reposition_text_blocks()


# ─────────────────────────────────────────────────────────────────────────────
# Barre d'outils flottante (fusion progressive des visionneuses — idees.txt #3)
# ─────────────────────────────────────────────────────────────────────────────

def _floating_options_panel_style(theme, class_name: str) -> str:
    """Style de fond commun aux panneaux flottants d'options (angle de
    redressage, réglages du tampon de clonage) : contrairement à
    _ViewerToolbar (dont les icônes se détachent d'elles-mêmes par leur
    contraste propre), ces panneaux n'ont que du texte/contrôles fins — sans
    bordure marquée, ils se fondent visuellement dans une image de fond claire
    ou blanche (signalé par l'utilisateur en conditions réelles). Fond franc
    dédié (pas toolbar_bg, trop proche d'un fond de page clair/blanc typique
    d'une BD) + bordure nette dans la couleur de texte du thème."""
    panel_bg = "#3a3a3a" if _state_module.state.dark_mode else "#f0f0f0"
    return (
        f"{class_name} {{ background: {panel_bg}; border: 1px solid {theme['text']}; "
        f"border-radius: 6px; }}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Visionneuse principale
# ─────────────────────────────────────────────────────────────────────────────

class ImageViewer(CropViewerMixin, StraightenViewerMixin, CloneViewerMixin, TextViewerMixin, QDialog):
    """
    Visionneuse d'images Qt.
    Reproduit à l'identique ImageViewer (tkinter) :
      - Navigation ← → / molette
      - Zoom Ctrl+Molette / Ctrl+Plus / Ctrl+Moins / Ctrl+0
      - Mode lecture : simple / double / continu (touche D)
      - Plein écran F11 / double-clic
      - Recadrage rubber-band + validation
      - GIF animé (bouton Play/Pause)
      - Pan clic-droit
      - Menu contextuel clic-droit
      - Undo/Redo Ctrl+Z / Ctrl+Y
    """

    def __init__(self, parent, start_idx: int, callbacks: dict | None = None,
                 initial_tool: str | None = None):
        super().__init__(parent)
        self.callbacks = callbacks or {}
        state = self.callbacks.get('state') or _state_module.state
        self._initial_tool = initial_tool

        self.setWindowTitle(_wt("viewer.title"))
        self.resize(800, 600)
        self.setWindowFlags(Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)

        # ── Appliquer l'icône de la fenêtre ──
        from modules.qt.font_loader import resource_path
        import os
        ico_path = resource_path('icons/MosaicView.ico')
        if os.path.exists(ico_path):
            from PySide6.QtGui import QIcon
            self.setWindowIcon(QIcon(ico_path))

        self.current_idx   = start_idx
        state.active_viewers += 1
        image_viewer_refs.append(self)

        # Rectangles de crop en cours, conservés par page (idees.txt #3, partie B :
        # un travail d'outil non validé survit à un changement de page). Clé = index
        # de page, valeur = (crop_rel_x1, crop_rel_y1, crop_rel_x2, crop_rel_y2).
        # Volatile : jamais persisté sur disque, perdu à la fermeture de la visionneuse.
        self._crop_by_page: dict[int, tuple[float, float, float, float]] = {}

        # Traits de redressage en cours, conservés par page — même principe que
        # _crop_by_page. Clé = index de page, valeur = ((ix1, iy1), (ix2, iy2))
        # en coordonnées image (stables, indépendantes du zoom/pan).
        self._straighten_by_page: dict[int, tuple[tuple[float, float], tuple[float, float]]] = {}

        # État de l'outil "clone" — PAS de dict par page comme crop/straighten :
        # contrairement à eux, un coup de tampon est déjà appliqué et commité
        # (bytes + save_state) dès son relâchement, il n'y a donc rien "en
        # attente de validation" à faire survivre à un changement de page (voir
        # idees.txt #3, discussion du 3e outil migré). Seule la position de la
        # source Ctrl+cliquée est un état d'outil volatile, réinitialisée à
        # chaque changement de page (voir navigate()). Voir clone_tool_qt.py::
        # CloneViewerMixin, hérité par cette classe.
        self._init_clone_viewer_state()

        # Blocs de texte en cours, conservés par page — même principe que
        # _crop_by_page/_straighten_by_page, mais valeur = liste de blocs
        # (ix, iy, html) au lieu d'une seule géométrie. Volatile : jamais
        # persisté sur disque, perdu à la fermeture de la visionneuse.
        self._text_blocks_by_page: dict[int, list[tuple[int, int, str]]] = {}

        # ── État ──────────────────────────────────────────────────────────────
        # zoom_level : échelle réelle par rapport à la taille native de l'image
        # (1.0 = 100% = 1 pixel image = 1 pixel écran). Initialisé au premier
        # affichage pour correspondre à l'ajustement automatique à la fenêtre.
        self.zoom_level      = 1.0
        self._zoom_initialized = False
        self._manual_zoom = False
        self._webtoon_bound_scrolls = 0
        self.page_mode       = "double"
        self.is_fullscreen   = False
        self._validating_crop = False

        self.displayed_left_idx  = None
        self.displayed_right_idx = None

        # GIF
        self.is_animated_gif   = False
        self.gif_is_playing    = False
        self.gif_current_frame = 0
        self._gif_timer        = QTimer(self)
        self._gif_timer.timeout.connect(self._animate_gif_frame)
        self.gif_durations     = []

        # Resize debounce
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._on_resize_debounced)
        self._last_w = 0
        self._last_h = 0

        # Nom masquage plein écran
        self._name_hide_timer = QTimer(self)
        self._name_hide_timer.setSingleShot(True)
        self._name_hide_timer.timeout.connect(self._hide_name_label)

        # ── Layout ───────────────────────────────────────────────────────────
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Canvas de visionneuse
        self._canvas = _ViewerCanvas(self)
        root_layout.addWidget(self._canvas, stretch=1)

        # Barre d'outils flottante (fusion progressive des visionneuses, idees.txt #3)
        self._toolbar = _ViewerToolbar(self)
        if self._initial_tool in ("crop", "straighten", "clone", "text"):
            self._toolbar.set_active_tool(self._initial_tool)

        # Label du nom en bas
        self._name_label = QLabel()
        self._name_label.setAlignment(Qt.AlignCenter)
        self._name_label.setStyleSheet("background: transparent;")
        root_layout.addWidget(self._name_label)

        # Label du zoom (superposé en haut à droite)
        self._zoom_label = QLabel("100%", self)
        self._zoom_label.setStyleSheet("color: #666666; background: transparent;")
        self._zoom_label.adjustSize()

        # Bouton Play/Pause GIF (superposé en haut à gauche)
        self._play_pause_btn = QPushButton("▶", self)
        self._play_pause_btn.setFixedSize(40, 40)
        self._play_pause_btn.clicked.connect(self.toggle_gif_playback)
        self._play_pause_btn.hide()

        # Label du mode de lecture (superposé en haut à gauche)
        self._mode_label = QLabel("", self)
        self._mode_label.setStyleSheet("color: #666666; background: transparent;")
        self._mode_label.adjustSize()

        # ── Raccourcis clavier ────────────────────────────────────────────────
        # Left/Right naviguent de page — mais un bloc de texte en édition (outil
        # "text", idees.txt #3) utilise aussi les flèches simples pour déplacer
        # le curseur dans le texte : sans ce garde, ces deux QShortcut (contexte
        # par défaut Qt.WindowShortcut, actif même si un QTextEdit enfant a le
        # focus) intercepteraient la flèche avant qu'elle n'atteigne l'overlay.
        QShortcut(QKeySequence(Qt.Key_Left),       self).activated.connect(
            lambda: None if self._text_block_has_focus() else self.navigate(-1))
        QShortcut(QKeySequence(Qt.Key_Right),      self).activated.connect(
            lambda: None if self._text_block_has_focus() else self.navigate(1))
        QShortcut(QKeySequence(Qt.Key_Escape),     self).activated.connect(self._on_escape)
        QShortcut(QKeySequence(Qt.Key_F11),        self).activated.connect(self.toggle_fullscreen)

        QShortcut(QKeySequence("Ctrl+Z"),          self).activated.connect(self._undo_and_refresh)
        QShortcut(QKeySequence("Ctrl+Shift+Z"),    self).activated.connect(self._redo_and_refresh)
        QShortcut(QKeySequence("Ctrl+Y"),          self).activated.connect(self._redo_and_refresh)
        QShortcut(QKeySequence("Ctrl++"),          self).activated.connect(lambda: self.adjust_zoom(0.1))
        QShortcut(QKeySequence("Ctrl+-"),          self).activated.connect(lambda: self.adjust_zoom(-0.1))
        QShortcut(QKeySequence("Ctrl+0"),          self).activated.connect(self.fit_zoom_to_window)
        QShortcut(QKeySequence("Ctrl+1"),          self).activated.connect(self.reset_zoom)

        # ── Signal langue ─────────────────────────────────────────────────────
        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self._closed = False
        self._close_confirmed = False

        self._center_parent = parent
        self._retranslate()
        self.display_image()
        # Le canvas n'a pas encore sa taille réelle à la construction (valeur par
        # défaut Qt) : le fit calculé ci-dessus est donc approximatif. On force un
        # recalcul unique une fois la fenêtre affichée à sa taille définitive.
        from PySide6.QtCore import QTimer as _QTimer
        _QTimer.singleShot(0, self._recompute_initial_fit)

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    # ── Traduction à la volée ─────────────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(12)
        font_zoom = _get_current_font(10)

        self.setWindowTitle(_wt("viewer.title"))
        self.setStyleSheet(f"QDialog {{ background: black; }}")

        self._name_label.setFont(font)
        self._name_label.setStyleSheet(
            f"color: {theme['text']}; background: {theme['bg']};"
        )

        warn_color = "#666666" if not (self.callbacks.get('state') or _state_module.state).dark_mode else "#999999"
        self._zoom_label.setFont(font_zoom)
        self._zoom_label.setStyleSheet(f"color: {warn_color}; background: transparent;")
        self._zoom_label.adjustSize()
        self._zoom_label.move(self.width() - self._zoom_label.width() - 10, 10)

        self._mode_label.setFont(font_zoom)
        self._mode_label.setStyleSheet(f"color: {warn_color}; background: transparent;")
        self._update_mode_label()

        self._play_pause_btn.setFont(_get_current_font(16))
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: none; }}"
        )
        self._play_pause_btn.setStyleSheet(btn_style)

        self._canvas.retranslate_validate_btn()

        self._toolbar._apply_theme()
        self._toolbar.retranslate()

    # ── Redisplay avec pan ────────────────────────────────────────────────────

    def _redisplay_with_pan(self):
        """Redessine l'image en tenant compte du décalage de pan."""
        self.display_image(keep_crop_rect=True)

    def _recompute_initial_fit(self):
        """Le zoom initial calculé dans __init__ utilise une taille de canvas
        provisoire (la fenêtre n'a pas encore sa taille réelle à la construction).
        Recalcule une seule fois le fit avec la taille définitive, sauf si
        l'utilisateur a déjà zoomé manuellement entre-temps."""
        if self._manual_zoom:
            return
        self._zoom_initialized = False
        self.display_image(keep_crop_rect=True)
        self._zoom_label.setText(f"{int(self.zoom_level * 100)}%")
        self._zoom_label.adjustSize()
        self._zoom_label.move(self.width() - self._zoom_label.width() - 10, 10)

    # ── Resize de la fenêtre ─────────────────────────────────────────────────

    def _on_resize_debounced(self):
        """Tant qu'aucun zoom manuel n'a été fait, le fit-to-window doit rester
        valable quelle que soit la taille de la fenêtre (plein écran, maximisée,
        etc.) : on force le recalcul du zoom avant de redessiner."""
        if not self._manual_zoom:
            self._zoom_initialized = False
        self.display_image(keep_crop_rect=True)
        self._zoom_label.setText(f"{int(self.zoom_level * 100)}%")
        self._zoom_label.adjustSize()
        self._zoom_label.move(self.width() - self._zoom_label.width() - 10, 10)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        if w != self._last_w or h != self._last_h:
            self._last_w, self._last_h = w, h
            self._resize_timer.start(150)
        # Repositionne le zoom label
        self._zoom_label.adjustSize()
        self._zoom_label.move(w - self._zoom_label.width() - 10, 10)

    # ── Fermeture ─────────────────────────────────────────────────────────────

    def _has_unvalidated_work(self) -> bool:
        """Travail d'outil non validé sur au moins une page (idees.txt #3, partie B) :
        le rectangle/trait/blocs de la page actuellement affichée ne sont pas
        encore dans _crop_by_page/_straighten_by_page/_text_blocks_by_page (ils
        n'y entrent qu'au moment de changer de page), donc toutes les sources
        doivent être vérifiées."""
        if self._canvas.crop_rel_x1 is not None:
            return True
        if self._crop_by_page:
            return True
        if self._canvas._line_img_start is not None:
            return True
        if self._straighten_by_page:
            return True
        if self._canvas.has_text_blocks:
            return True
        return bool(self._text_blocks_by_page)

    def closeEvent(self, event):
        if self._closed:
            super().closeEvent(event)
            return

        if self._has_unvalidated_work() and not self._close_confirmed:
            event.ignore()
            dlg = ConfirmYNDialog(
                self,
                lambda: _wt("viewer.unvalidated_work_title"),
                lambda: _("viewer.unvalidated_work_message"),
            )
            dlg.result_signal.connect(self._on_close_confirmed)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            return

        self._closed = True

        state = self.callbacks.get('state') or _state_module.state
        self._gif_timer.stop()
        self._name_hide_timer.stop()
        self._resize_timer.stop()

        self._save_bookmark(state)

        for entry in state.images_data:
            if entry.get("is_image"):
                free_image_memory(entry)

        state.active_viewers -= 1
        if self in image_viewer_refs:
            image_viewer_refs.remove(self)

        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except Exception:
            pass

        super().closeEvent(event)

    def _on_close_confirmed(self, confirmed: bool):
        if confirmed:
            self._close_confirmed = True
            self.close()

    def _save_bookmark(self, state):
        """Sauvegarde la page courante comme marque-page (sauf page 0 et dernière page)."""
        filepath = getattr(state, 'current_file', None)
        if not filepath:
            return
        img_indices = self.get_image_indices()
        if not img_indices:
            return
        try:
            current_pos = img_indices.index(self.current_idx)
        except ValueError:
            return
        last_pos = len(img_indices) - 1
        if current_pos == 0 or current_pos >= last_pos:
            return
        from modules.qt.config_manager import get_config_manager
        cfg = get_config_manager()
        if cfg:
            cfg.set_bookmark(filepath, current_pos)
            on_bm = self.callbacks.get("on_bookmark_changed")
            if on_bm:
                on_bm(img_indices[current_pos])

    # ── Navigation ────────────────────────────────────────────────────────────

    def get_image_indices(self) -> list[int]:
        state = self.callbacks.get('state') or _state_module.state
        return [i for i, e in enumerate(state.images_data) if e["is_image"]]

    def is_wide_image(self, idx: int) -> bool:
        state = self.callbacks.get('state') or _state_module.state
        if idx >= len(state.images_data):
            return False
        entry = state.images_data[idx]
        if not entry["is_image"]:
            return False
        img = ensure_image_loaded(entry)
        if img is None:
            return False
        w, h = img.size
        return (w / h if h > 0 else 0) > 1.5

    def _get_all_ratios(self):
        """Retourne les ratios largeur/hauteur de toutes les images (pour la détection de pages multiples)."""
        state = self.callbacks.get('state') or _state_module.state
        ratios = []
        for entry in state.images_data:
            if not entry["is_image"]:
                continue
            w = entry.get("img_width")
            h = entry.get("img_height")
            if w and h and h > 0:
                ratios.append(w / h)
            else:
                img = ensure_image_loaded(entry)
                if img is not None:
                    ratios.append(img.width / img.height if img.height > 0 else 0)
                else:
                    ratios.append(0)
        return ratios

    def is_multiple_page(self, idx: int) -> bool:
        """Détecte si une page est une page multiple (double, triple…) selon la logique de renumérotation.
        Utilise le ratio relatif à la médiane des pages portrait : mult >= 2 → page multiple."""
        state = self.callbacks.get('state') or _state_module.state
        if idx >= len(state.images_data):
            return False
        entry = state.images_data[idx]
        if not entry["is_image"]:
            return False
        w = entry.get("img_width")
        h = entry.get("img_height")
        if w and h and h > 0:
            ratio = w / h
        else:
            img = ensure_image_loaded(entry)
            if img is None:
                return False
            ratio = img.width / img.height if img.height > 0 else 0
        if ratio <= 0:
            return False
        reference_ratio = compute_reference_ratio(self._get_all_ratios())
        if reference_ratio <= 0:
            return False
        mult = max(1, round(ratio / reference_ratio))
        return mult >= 2

    def _double_page_pair_start(self, img_indices, pos):
        """Retourne True si pos est le début d'une paire en mode page double.
        La parité est recalculée dynamiquement en tenant compte des pages multiples précédentes :
        on simule le défilement depuis le début pour savoir si pos est début ou fin de paire."""
        if pos == 0:
            return False  # pos 0 est toujours affiché seul
        p = 1  # on commence à simuler depuis pos 1
        while p < pos:
            if self.is_multiple_page(img_indices[p]):
                p += 1  # page multiple : seule, avance de 1
            else:
                if p == pos:
                    break
                # paire normale : deux pages
                p += 2
        return p == pos

    def navigate(self, delta: int):
        img_indices = self.get_image_indices()
        if not img_indices:
            return
        try:
            current_pos = img_indices.index(self.current_idx)
            if self.page_mode == "double":
                is_pair_start = self._double_page_pair_start(img_indices, current_pos)
                if delta > 0:
                    if current_pos == 0:
                        new_pos = 1
                    elif self.is_multiple_page(img_indices[current_pos]):
                        new_pos = current_pos + 1
                    elif is_pair_start:
                        if current_pos + 1 < len(img_indices) and self.is_multiple_page(img_indices[current_pos + 1]):
                            new_pos = current_pos + 1
                        else:
                            new_pos = current_pos + 2
                    else:
                        new_pos = current_pos + 1
                else:
                    if current_pos <= 1:
                        new_pos = 0
                    elif self.is_multiple_page(img_indices[current_pos]):
                        new_pos = current_pos - 1
                    elif is_pair_start:
                        new_pos = max(1, current_pos - 2)
                    else:
                        new_pos = current_pos - 1
            else:
                new_pos = current_pos + delta
            new_pos = max(0, min(new_pos, len(img_indices) - 1))
            self._save_crop_for_current_page()
            self._save_straighten_for_current_page()
            self._save_text_for_current_page()
            self.current_idx = img_indices[new_pos]
            if self.page_mode == "webtoon":
                self._canvas.pan_offset_y = 0
            self._check_clear_bookmark_on_last_page(img_indices, new_pos)
            self._restore_crop_for_page(self.current_idx)
            self._restore_straighten_for_page(self.current_idx)
            self._restore_text_for_page(self.current_idx)
            # Pas de persistance par page pour le clonage (voir idees.txt #3,
            # discussion du 3e outil migré) : la source Ctrl+cliquée est
            # simplement effacée, aucun travail en attente à restaurer.
            self._canvas.clear_clone_source()
            self.display_image(keep_crop_rect=True)
        except ValueError:
            self._save_crop_for_current_page()
            self._save_straighten_for_current_page()
            self._save_text_for_current_page()
            self.current_idx = img_indices[0]
            self._restore_crop_for_page(self.current_idx)
            self._restore_straighten_for_page(self.current_idx)
            self._restore_text_for_page(self.current_idx)
            self._canvas.clear_clone_source()
            self.display_image(keep_crop_rect=True)

    def _check_clear_bookmark_on_last_page(self, img_indices, current_pos):
        """Efface le marque-page si la dernière page est maintenant visible."""
        last_pos = len(img_indices) - 1
        is_last = False
        if self.page_mode == "double":
            is_last = (current_pos >= last_pos or current_pos + 1 >= last_pos)
        else:
            is_last = (current_pos >= last_pos)
        if not is_last:
            return
        state = self.callbacks.get('state') or _state_module.state
        filepath = getattr(state, 'current_file', None)
        if not filepath:
            return
        from modules.qt.config_manager import get_config_manager
        cfg = get_config_manager()
        if cfg and cfg.get_bookmark(filepath) is not None:
            cfg.remove_bookmark(filepath)
            on_bm = self.callbacks.get("on_bookmark_changed")
            if on_bm:
                on_bm(None)

    WEBTOON_PAGE_TURN_THRESHOLD = 360  # ~3 crans de molette standard (120 par cran)

    def _on_wheel(self, event):
        mods = event.modifiers()
        delta = event.angleDelta().y()
        if mods & Qt.ControlModifier:
            self.adjust_zoom(0.1 if delta > 0 else -0.1)
        elif self.page_mode == "webtoon":
            self._on_webtoon_wheel(delta)
        else:
            step = 2 if self.page_mode == "double" else 1
            self.navigate(-step if delta > 0 else step)

    def _on_webtoon_wheel(self, delta: int):
        """Molette en mode webtoon : scroll vertical dans la page ; une fois en
        butée (haut ou bas), accumule l'intensité des crans de molette
        supplémentaires dans le même sens jusqu'à un seuil, puis change de page
        (évite un changement de page accidentel). L'accumulation se fait sur la
        magnitude du delta (pas le nombre d'événements) car une molette à
        défilement fin envoie beaucoup plus d'événements qu'une molette
        standard pour un même geste physique.

        Un scroll rapide peut envoyer un seul événement avec un delta énorme
        (plusieurs dizaines de crans agrégés) qui dépasse d'un coup la distance
        restante jusqu'à la butée : la partie du delta qui "déborde" au-delà de
        la butée est reportée dans l'accumulation dès ce même événement, sinon
        ce geste ne compte pour rien et l'utilisateur doit recommencer 3 crans
        supplémentaires une fois déjà en butée (ressenti de blocage)."""
        ch = self._canvas.height()
        min_offset = min(0, ch - self._canvas.display_height)
        max_offset = 0
        pan_y = self._canvas.pan_offset_y
        new_offset = pan_y + delta

        if delta > 0:
            overflow = max(0, new_offset - max_offset)
        else:
            overflow = max(0, min_offset - new_offset)

        if overflow > 0:
            self._webtoon_bound_scrolls += overflow
            if self._webtoon_bound_scrolls >= self.WEBTOON_PAGE_TURN_THRESHOLD:
                self._webtoon_bound_scrolls = 0
                self.navigate(-1 if delta > 0 else 1)
                return
        else:
            self._webtoon_bound_scrolls = 0

        clamped = max(min_offset, min(max_offset, new_offset))
        moved = clamped - pan_y
        self._canvas.pan_offset_y = clamped
        self._canvas.display_offset_y += moved
        self._canvas.update()

    # ── Zoom ──────────────────────────────────────────────────────────────────

    def adjust_zoom(self, delta: float):
        self._manual_zoom = True
        self.zoom_level = max(0.1, min(10.0, self.zoom_level + delta))
        self._zoom_label.setText(f"{int(self.zoom_level * 100)}%")
        self._zoom_label.adjustSize()
        self._zoom_label.move(self.width() - self._zoom_label.width() - 10, 10)
        self.display_image(keep_crop_rect=True)

    def reset_zoom(self):
        """Ctrl+1 : zoom à 100% (taille réelle des pixels de l'image)."""
        self._manual_zoom = True
        self.zoom_level = 1.0
        self._zoom_label.setText("100%")
        self._zoom_label.adjustSize()
        self._zoom_label.move(self.width() - self._zoom_label.width() - 10, 10)
        self.display_image(keep_crop_rect=True)

    def fit_zoom_to_window(self):
        """Ctrl+0 : recalcule le zoom pour ajuster la page actuelle à la fenêtre.
        Repasse en mode auto-fit (contrairement aux vrais zooms manuels) : un
        redimensionnement ultérieur de la fenêtre continuera à réajuster le zoom."""
        self._manual_zoom = False
        self._zoom_initialized = False
        self.display_image(keep_crop_rect=True)
        self._zoom_label.setText(f"{int(self.zoom_level * 100)}%")
        self._zoom_label.adjustSize()
        self._zoom_label.move(self.width() - self._zoom_label.width() - 10, 10)

    # ── Plein écran ───────────────────────────────────────────────────────────

    def toggle_fullscreen(self):
        if self._validating_crop:
            return
        if self.is_fullscreen:
            self.showNormal()
            self.is_fullscreen = False
            self._name_label.show()
            self._name_label.setStyleSheet(
                f"color: {get_current_theme()['text']}; background: {get_current_theme()['bg']};"
            )
            self._name_hide_timer.stop()
        else:
            self.showFullScreen()
            self.is_fullscreen = True
            self._name_label.setStyleSheet("color: white; background: black;")
        if not self._manual_zoom:
            self._zoom_initialized = False
        self.display_image(keep_crop_rect=True)

    # ── Mode de lecture ───────────────────────────────────────────────────────

    def _update_mode_label(self):
        if self.is_animated_gif:
            self._mode_label.hide()
            return
        if self.page_mode == "single":
            text = _("viewer.mode_single")
        elif self.page_mode == "double":
            text = _("viewer.mode_double")
        elif self.page_mode == "continuous":
            text = _("viewer.mode_continuous")
        else:
            text = _("viewer.mode_webtoon")
        self._mode_label.setText(text)
        self._mode_label.adjustSize()
        self._mode_label.move(10, 10)
        self._mode_label.show()

    def toggle_double_page(self):
        if self.page_mode == "single":
            self.page_mode = "double"
        elif self.page_mode == "double":
            self.page_mode = "continuous"
        elif self.page_mode == "continuous":
            self.page_mode = "webtoon"
        else:
            self.page_mode = "single"
        if self.page_mode == "webtoon":
            self._canvas.pan_offset_y = 0
            self._webtoon_bound_scrolls = 0
            if not self._manual_zoom:
                self._zoom_initialized = False
        self._update_mode_label()
        self.display_image(keep_crop_rect=True)

    # ── Undo/Redo ─────────────────────────────────────────────────────────────

    def _undo_and_refresh(self):
        fn = self.callbacks.get("undo_action")
        if fn:
            fn()
        self._refresh_after_undo_redo()

    def _redo_and_refresh(self):
        fn = self.callbacks.get("redo_action")
        if fn:
            fn()
        self._refresh_after_undo_redo()

    def _refresh_after_undo_redo(self):
        """Rafraîchit la visionneuse après undo/redo en invalidant le cache image."""
        state = self.callbacks.get('state') or _state_module.state
        # Invalide le cache PIL de l'image courante pour forcer le rechargement depuis bytes
        if 0 <= self.current_idx < len(state.images_data):
            entry = state.images_data[self.current_idx]
            entry["img"] = None
        self.display_image()
        self._toolbar.refresh_undo_redo_state()

    # ── Touches clavier ───────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_D:
            self.toggle_double_page()
        else:
            super().keyPressEvent(event)

    # ── Outil texte : focus clavier ──────────────────────────────────────────

    def _text_block_has_focus(self) -> bool:
        """True si un bloc de texte a actuellement le focus clavier — les
        raccourcis Left/Right de navigation doivent alors céder la priorité au
        déplacement du curseur natif de QTextEdit (voir __init__)."""
        block = self._canvas._text_active_block()
        return block is not None and block.overlay.isVisible() and block.overlay.hasFocus()

    # ── Échap ─────────────────────────────────────────────────────────────────

    def _on_escape(self):
        if self._canvas.has_crop or self._canvas._crop_start is not None:
            self._canvas.clear_crop()
            self._crop_by_page.pop(self.current_idx, None)
        elif self._canvas.has_line or self._canvas._line_start is not None:
            self._canvas.clear_line()
            self._toolbar._angle_panel.reset()
            self._straighten_by_page.pop(self.current_idx, None)
        elif self._canvas._clone_source_img is not None:
            # Efface la source Ctrl+cliquée — rien d'autre à annuler pour cet
            # outil (voir idees.txt #3, le clonage n'a pas de "travail en
            # attente de validation" : chaque coup de tampon est déjà commité).
            self._canvas.clear_clone_source()
        elif self._canvas.has_text_blocks:
            self._canvas.clear_text_blocks()
            self._toolbar._text_panel.set_visible_for_tool(self._toolbar.active_tool)
            self._canvas._hide_validate_btn()
            self._text_blocks_by_page.pop(self.current_idx, None)
        elif self.is_fullscreen:
            self.toggle_fullscreen()
        else:
            self.close()

    # ── Marque-page ───────────────────────────────────────────────────────────

    def _delete_current_bookmark(self):
        state = self.callbacks.get('state') or _state_module.state
        filepath = getattr(state, 'current_file', None)
        if not filepath:
            return
        from modules.qt.config_manager import get_config_manager
        cfg = get_config_manager()
        if cfg:
            cfg.remove_bookmark(filepath)
            on_bm = self.callbacks.get("on_bookmark_changed")
            if on_bm:
                on_bm(None)

    # ── Menu contextuel ───────────────────────────────────────────────────────

    def _show_context_menu(self, global_pos: QPoint):
        menu = QMenu(self)
        font = _get_current_font(9)
        menu.setFont(font)

        theme = get_current_theme()
        menu.setStyleSheet(
            f"QMenu {{ background: {theme['toolbar_bg']}; color: {theme['text']}; }}"
            f"QMenu::item:selected {{ background: {theme['separator']}; }}"
        )

        menu.addAction(_("context_menu.viewer.prev_page"),  lambda: self.navigate(-1))
        menu.addAction(_("context_menu.viewer.next_page"),  lambda: self.navigate(1))
        menu.addSeparator()
        menu.addAction(_("context_menu.viewer.zoom_in"),    lambda: self.adjust_zoom(0.1))
        menu.addAction(_("context_menu.viewer.zoom_out"),   lambda: self.adjust_zoom(-0.1))
        menu.addAction(_("context_menu.viewer.zoom_reset"), self.fit_zoom_to_window)
        menu.addAction(_("context_menu.viewer.zoom_100"),   self.reset_zoom)
        menu.addSeparator()

        if self.page_mode == "single":
            mode_label = _("context_menu.viewer.reading_mode_double")
        elif self.page_mode == "double":
            mode_label = _("context_menu.viewer.reading_mode_continuous")
        elif self.page_mode == "continuous":
            mode_label = _("context_menu.viewer.reading_mode_webtoon")
        else:
            mode_label = _("context_menu.viewer.reading_mode_single")
        menu.addAction(mode_label, self.toggle_double_page)
        menu.addSeparator()

        fs_label = (_("context_menu.viewer.fullscreen_exit") if self.is_fullscreen
                    else _("context_menu.viewer.fullscreen"))
        menu.addAction(fs_label, self.toggle_fullscreen)
        menu.addSeparator()
        menu.addAction(_("context_menu.viewer.close_viewer"), self.close)
        menu.addSeparator()

        state = self.callbacks.get('state') or _state_module.state
        filepath = getattr(state, 'current_file', None)
        from modules.qt.config_manager import get_config_manager
        cfg = get_config_manager()
        has_bookmark = bool(filepath and cfg and cfg.get_bookmark(filepath) is not None)
        act_del_bm = menu.addAction(_("context_menu.viewer.delete_bookmark"), self._delete_current_bookmark)
        act_del_bm.setEnabled(has_bookmark)

        menu.exec(global_pos)

    # ── GIF animé ─────────────────────────────────────────────────────────────

    def toggle_gif_playback(self):
        if not self.is_animated_gif:
            return
        if self.gif_is_playing:
            self._gif_timer.stop()
            self.gif_is_playing = False
            self._play_pause_btn.setText("▶")
        else:
            self.gif_is_playing = True
            self._play_pause_btn.setText("⏸")
            self._schedule_gif_frame()

    def _schedule_gif_frame(self):
        if not self.gif_is_playing or not self.is_animated_gif:
            return
        duration = self.gif_durations[self.gif_current_frame] if self.gif_current_frame < len(self.gif_durations) else 100
        self._gif_timer.start(duration)

    def _animate_gif_frame(self):
        self._gif_timer.stop()
        state = self.callbacks.get('state') or _state_module.state
        if not self.gif_is_playing or not self.is_animated_gif:
            return
        entry = state.images_data[self.current_idx]
        frame_count = entry.get("gif_frame_count", 0)
        if frame_count == 0:
            return
        frame = get_gif_frame(entry, self.gif_current_frame)
        if frame is None:
            return
        # Redimensionne la frame
        cw = self._canvas.width()
        ch = self._canvas.height() - 40
        if cw <= 1: cw = 800
        if ch <= 1: ch = 540
        fw, fh = frame.size
        final_w = max(1, int(fw * self.zoom_level))
        final_h = max(1, int(fh * self.zoom_level))
        # Pixmap stocké à la résolution native de la frame, étiré par Qt à l'affichage.
        frame_has_alpha = (frame.mode in ('RGBA', 'LA') or
                           (frame.mode == 'P' and 'transparency' in frame.info))
        if frame_has_alpha:
            frame = _compose_on_checkerboard(frame)
        pixmap = _pil_to_qpixmap(frame)
        offset_x = (cw - final_w) // 2 + self._canvas.pan_offset_x
        if self.page_mode == "webtoon":
            offset_y = self._canvas.pan_offset_y
        else:
            offset_y = (ch - final_h) // 2 + self._canvas.pan_offset_y
        self._canvas.set_pixmap_and_geometry(pixmap, offset_x, offset_y, final_w, final_h)
        self.gif_current_frame = (self.gif_current_frame + 1) % frame_count
        self._schedule_gif_frame()

    def _stop_gif(self):
        self._gif_timer.stop()
        self.gif_is_playing = False

    # ── Affichage ─────────────────────────────────────────────────────────────

    def display_image(self, keep_crop_rect: bool = False):
        state = self.callbacks.get('state') or _state_module.state
        if self.current_idx >= len(state.images_data):
            return

        if not keep_crop_rect:
            self._canvas.clear_crop()
            self._canvas.clear_line()
            self._toolbar._angle_panel.reset()

        img_indices = self.get_image_indices()
        if not img_indices:
            return
        try:
            current_pos = img_indices.index(self.current_idx)
        except ValueError:
            return

        cw = self._canvas.width()
        ch = self._canvas.height()
        if cw <= 1: cw = 780
        if ch <= 1: ch = 540
        viewer_w = cw - 20
        viewer_h = ch - 20

        if self.page_mode == "double":
            if self.is_multiple_page(self.current_idx):
                self._display_single_page(self.current_idx, viewer_w, viewer_h)
            elif current_pos == 0:
                self._display_single_page(img_indices[0], viewer_w, viewer_h)
            else:
                is_pair_start = self._double_page_pair_start(img_indices, current_pos)
                if is_pair_start:
                    left_idx  = img_indices[current_pos]
                    right_idx = img_indices[current_pos + 1] if current_pos + 1 < len(img_indices) else None
                    if self.is_multiple_page(left_idx) or (right_idx and self.is_multiple_page(right_idx)):
                        self._display_single_page(left_idx, viewer_w, viewer_h)
                    else:
                        self._display_double_page(left_idx, right_idx, viewer_w, viewer_h)
                else:
                    left_idx  = img_indices[current_pos - 1] if current_pos > 0 else None
                    right_idx = img_indices[current_pos]
                    if self.is_multiple_page(right_idx):
                        self._display_single_page(right_idx, viewer_w, viewer_h)
                    else:
                        self._display_double_page(left_idx, right_idx, viewer_w, viewer_h)

        elif self.page_mode == "continuous":
            if self.is_wide_image(self.current_idx):
                self._display_single_page(self.current_idx, viewer_w, viewer_h)
            elif current_pos == 0:
                self._display_single_page(img_indices[0], viewer_w, viewer_h)
            else:
                left_idx  = img_indices[current_pos]
                right_idx = img_indices[current_pos + 1] if current_pos + 1 < len(img_indices) else None
                if self.is_wide_image(left_idx) or (right_idx and self.is_wide_image(right_idx)):
                    self._display_single_page(left_idx, viewer_w, viewer_h)
                else:
                    self._display_double_page(left_idx, right_idx, viewer_w, viewer_h)
        else:
            self._display_single_page(self.current_idx, viewer_w, viewer_h)

    WEBTOON_MAX_WIDTH_PX = 900

    def _display_single_page(self, idx: int, viewer_w: int, viewer_h: int):
        state = self.callbacks.get('state') or _state_module.state
        entry = state.images_data[idx]
        if not entry["is_image"]:
            return

        img = ensure_image_loaded(entry)
        if img is None:
            return

        self.displayed_left_idx  = None
        self.displayed_right_idx = None
        self._stop_gif()

        self.is_animated_gif = entry.get("is_animated_gif", False)
        if self.is_animated_gif:
            self.gif_durations = entry.get("gif_durations", [])
            self.gif_current_frame = 0
            self._play_pause_btn.setText("▶")
            self._play_pause_btn.move(10, 10)
            self._play_pause_btn.show()
            self._play_pause_btn.raise_()
        else:
            self._play_pause_btn.hide()
            self.gif_durations = []
        self._update_mode_label()

        img = img.copy()
        has_alpha = (img.mode in ('RGBA', 'LA') or
                     (img.mode == 'P' and 'transparency' in img.info))
        img_w, img_h = img.size
        if not self._zoom_initialized:
            if self.page_mode == "webtoon":
                self.zoom_level = min(self.WEBTOON_MAX_WIDTH_PX, viewer_w) / img_w
            else:
                self.zoom_level = min(viewer_w / img_w, viewer_h / img_h)
            self._zoom_initialized = True
        final_w = max(1, int(img_w * self.zoom_level))
        final_h = max(1, int(img_h * self.zoom_level))
        # Le pixmap est stocké à la résolution source : c'est Qt qui l'étire à
        # l'affichage (voir _ViewerCanvas.paintEvent), pas un resize() PIL ici.
        if has_alpha:
            img = _compose_on_checkerboard(img)
        pixmap = _pil_to_qpixmap(img)

        cw = self._canvas.width()
        ch = self._canvas.height()
        if cw <= 1: cw = viewer_w
        if ch <= 1: ch = viewer_h
        offset_x = (cw - final_w) // 2 + self._canvas.pan_offset_x
        if self.page_mode == "webtoon":
            offset_y = self._canvas.pan_offset_y
        else:
            offset_y = (ch - final_h) // 2 + self._canvas.pan_offset_y

        self._canvas.set_pixmap_and_geometry(pixmap, offset_x, offset_y, final_w, final_h)

        # Redessine le rubber-band si coordonnées relatives disponibles
        if self._canvas.crop_rel_x1 is not None:
            x1 = int(offset_x + self._canvas.crop_rel_x1 * final_w)
            y1 = int(offset_y + self._canvas.crop_rel_y1 * final_h)
            x2 = int(offset_x + self._canvas.crop_rel_x2 * final_w)
            y2 = int(offset_y + self._canvas.crop_rel_y2 * final_h)
            self._canvas._crop_start = QPoint(x1, y1)
            self._canvas._crop_end   = QPoint(x2, y2)
            self._canvas.update()
            # Bouton Valider affiché seulement si l'outil crop est réellement actif
            # (un crop restauré sur une page pendant que l'outil est désélectionné
            # reste visible en gris mais non actionnable, voir _ViewerToolbar).
            if self._toolbar.active_tool == "crop":
                self._canvas._show_validate_btn()
            else:
                self._canvas._hide_validate_btn()

        # Redessine le trait de redressage si un trait est mémorisé pour cette page
        if self._canvas.has_line:
            self._canvas._sync_line_from_image()
            self._canvas.update()
            if self._toolbar.active_tool == "straighten":
                self._canvas._show_validate_btn()
                self._toolbar._angle_panel.set_visible_for_tool("straighten")
            else:
                self._canvas._hide_validate_btn()
                self._toolbar._angle_panel.hide()

        # Repositionne les blocs de texte de cette page (widgets Qt enfants du
        # canvas, pas un simple dessin — doivent suivre offset_x/y/zoom comme
        # le fait paint_crop_rect/paint_straighten_line pour leurs overlays).
        if self._canvas.has_text_blocks:
            self._canvas.reposition_text_blocks()
            if self._toolbar.active_tool == "text":
                self._canvas._show_validate_btn()
            else:
                self._canvas._hide_validate_btn()

        # Nom de fichier
        img_indices = self.get_image_indices()
        pos = img_indices.index(idx) + 1
        total = len(img_indices)
        self._name_label.setText(f"{entry['orig_name']} ({pos}/{total})")

        self._schedule_name_hide()

    def _display_double_page(self, left_idx, right_idx, viewer_w: int, viewer_h: int):
        state = self.callbacks.get('state') or _state_module.state
        self._stop_gif()
        self._play_pause_btn.hide()
        self.is_animated_gif = False

        self.displayed_left_idx  = left_idx
        self.displayed_right_idx = right_idx
        self._update_mode_label()

        left_img = right_img = None
        if left_idx is not None and left_idx < len(state.images_data):
            e = state.images_data[left_idx]
            if e["is_image"]:
                loaded = ensure_image_loaded(e)
                if loaded:
                    left_img = loaded.copy()
        if right_idx is not None and right_idx < len(state.images_data):
            e = state.images_data[right_idx]
            if e["is_image"]:
                loaded = ensure_image_loaded(e)
                if loaded:
                    right_img = loaded.copy()

        if not left_img and not right_img:
            return
        if not right_img:
            self._display_single_page(left_idx, viewer_w, viewer_h)
            return

        # Normalise les hauteurs
        lw, lh = left_img.size
        rw, rh = right_img.size
        max_h = max(lh, rh)
        if lh != max_h:
            left_img = left_img.resize((int(lw * max_h / lh), max_h), Image.Resampling.LANCZOS)
            lw = left_img.size[0]
        if rh != max_h:
            right_img = right_img.resize((int(rw * max_h / rh), max_h), Image.Resampling.LANCZOS)
            rw = right_img.size[0]

        combined_w = lw + rw
        left_has_alpha  = left_img.mode  in ('RGBA', 'LA') or (left_img.mode  == 'P' and 'transparency' in left_img.info)
        right_has_alpha = right_img.mode in ('RGBA', 'LA') or (right_img.mode == 'P' and 'transparency' in right_img.info)
        has_alpha = left_has_alpha or right_has_alpha
        if has_alpha:
            combined = Image.new('RGBA', (combined_w, max_h), (0, 0, 0, 0))
            left_rgba  = left_img.convert('RGBA')
            right_rgba = right_img.convert('RGBA')
            combined.paste(left_rgba,  (0,  0), left_rgba)
            combined.paste(right_rgba, (lw, 0), right_rgba)
        else:
            combined = Image.new('RGB', (combined_w, max_h), 'black')
            combined.paste(left_img,  (0,  0))
            combined.paste(right_img, (lw, 0))

        if not self._zoom_initialized:
            self.zoom_level = min(viewer_w / combined_w, viewer_h / max_h)
            self._zoom_initialized = True
        final_w = max(1, int(combined_w * self.zoom_level))
        final_h = max(1, int(max_h     * self.zoom_level))

        # Pixmap stocké à la résolution combinée native, étiré par Qt à l'affichage.
        if has_alpha:
            combined = _compose_on_checkerboard(combined)
        pixmap = _pil_to_qpixmap(combined)

        cw = self._canvas.width()
        ch = self._canvas.height()
        if cw <= 1: cw = viewer_w
        if ch <= 1: ch = viewer_h
        offset_x = (cw - final_w) // 2 + self._canvas.pan_offset_x
        offset_y = (ch - final_h) // 2 + self._canvas.pan_offset_y

        self._canvas.set_pixmap_and_geometry(pixmap, offset_x, offset_y, final_w, final_h)

        # Nom
        img_indices = self.get_image_indices()
        left_entry  = state.images_data[left_idx]
        right_entry = state.images_data[right_idx] if right_idx else None
        lpos = img_indices.index(left_idx) + 1
        rpos = img_indices.index(right_idx) + 1 if right_idx and right_idx in img_indices else None
        total = len(img_indices)
        if rpos:
            self._name_label.setText(
                f"{left_entry['orig_name']} | {right_entry['orig_name']} ({lpos}-{rpos}/{total})"
            )
        else:
            self._name_label.setText(f"{left_entry['orig_name']} ({lpos}/{total})")

        self._schedule_name_hide()

    # ── Nom en plein écran ────────────────────────────────────────────────────

    def _schedule_name_hide(self):
        self._name_hide_timer.stop()
        if self.is_fullscreen:
            self._name_label.show()
            self._name_hide_timer.start(2000)

    def _hide_name_label(self):
        if self.is_fullscreen:
            self._name_label.hide()


# ─────────────────────────────────────────────────────────────────────────────
# Fonction publique d'ouverture
# ─────────────────────────────────────────────────────────────────────────────

def open_image_viewer(parent, idx: int, callbacks: dict, initial_tool: str | None = None):
    """Ouvre la visionneuse sur l'image d'index idx.

    initial_tool : "crop" ou "straighten" pour présélectionner un outil de la
    barre d'outils flottante à l'ouverture (ex. commande "Recadrer" du menu
    contextuel/barre de menus/colonne d'icônes) ; None (défaut) = aucun outil
    présélectionné."""
    state = (callbacks or {}).get('state') or _state_module.state
    if not state.images_data[idx]["is_image"] or state.images_data[idx].get("is_corrupted"):
        return
    viewer = ImageViewer(parent, idx, callbacks=callbacks, initial_tool=initial_tool)
    viewer.show()


def update_image_viewer_if_open():
    """Met à jour le titre des visionneuses ouvertes si elles existent."""
    for viewer in image_viewer_refs[:]:
        try:
            if viewer and viewer.isVisible():
                viewer.setWindowTitle(_wt("viewer.title"))
                viewer._retranslate()
        except Exception:
            if viewer in image_viewer_refs:
                image_viewer_refs.remove(viewer)


def refresh_image_viewers_after_external_undo_redo(state):
    """Rafraîchit l'affichage de toute visionneuse ouverte sur `state` après un
    undo/redo déclenché HORS de la visionneuse (icône Undo/Redo de la colonne
    verticale, barre de menus, Ctrl+Z/Ctrl+Y du panneau) — un seul historique
    partagé (state.history, undo/redo unifié, voir idees.txt #3), mais sans cet
    appel la visionneuse continue d'afficher l'ancienne image : son propre
    ImageViewer._undo_and_refresh()/_refresh_after_undo_redo() n'est jamais
    déclenché puisque le clic n'a pas eu lieu sur SA propre icône Undo/Redo."""
    for viewer in image_viewer_refs[:]:
        try:
            if viewer and viewer.isVisible() and viewer.callbacks.get('state') is state:
                viewer._refresh_after_undo_redo()
        except Exception:
            if viewer in image_viewer_refs:
                image_viewer_refs.remove(viewer)
