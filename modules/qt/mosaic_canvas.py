"""
MosaicCanvas — QGraphicsView remplaçant le tk.Canvas de canvas_rendering.py

Responsabilités :
  - Afficher la grille de vignettes (ThumbnailItem)
  - Gérer la sélection (clic, Ctrl+clic, Shift+clic, rubber band)
  - Drag & drop sortant vers l'explorateur (QDrag + CF_HDROP)
  - Drag & drop interne (réordonnancement)
  - Indicateur de drop (ligne rouge entre vignettes)
  - Navigation clavier (flèches, Entrée, Suppr…)
  - Émet status_changed(int, int) quand la sélection change
"""

import os
import io
import tempfile
import shutil
import uuid

from modules.qt.temp_files import get_mosaicview_temp_dir

from PIL import Image

from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsItem, QGraphicsTextItem,
    QGraphicsProxyWidget, QTextEdit, QApplication, QRubberBand,
)
from PySide6.QtCore import Qt, QRectF, QPointF, QMimeData, QUrl, QTimer, Signal, QObject, QRect, QSize
from PySide6.QtGui import (
    QPainter, QPen, QColor, QBrush, QFont, QFontMetrics,
    QPixmap, QDrag, QImage, QPolygonF, QTextOption,
    QTextBlockFormat, QTextCursor,
)

from modules.qt import state as _state_module
from modules.qt.entries import get_icon_pil_for_entry, create_centered_thumbnail, THUMB_SIZES
from modules.qt.state import get_current_theme
from modules.qt.localization import _
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.tooltips_qt import get_tooltip_text, get_directory_tooltip_text, get_folder_up_tooltip_text
from modules.qt.overlay_tooltip_qt import OverlayTooltip

# ── Constantes (identiques à canvas_rendering.py) ─────────────────────────────
PAD_X    = 10
PAD_Y    = 10
LABEL_H  = 30


def _apply_name_item_font(name_item: "QGraphicsTextItem", font: "QFont", name_w: int, elided: str):
    """Configure un QGraphicsTextItem nom : police, texte tronqué, centrage, NO wrap."""
    name_item.setFont(font)
    name_item.setTextWidth(name_w)
    # Désactive le word-wrap via QTextOption sur le document
    opt = QTextOption(Qt.AlignHCenter)
    opt.setWrapMode(QTextOption.NoWrap)
    name_item.document().setDefaultTextOption(opt)
    name_item.setPlainText(elided)
    # Réapplique l'alignement centré (setPlainText réinitialise le format de bloc)
    cursor = QTextCursor(name_item.document())
    cursor.select(QTextCursor.Document)
    fmt = QTextBlockFormat()
    fmt.setAlignment(Qt.AlignHCenter)
    cursor.mergeBlockFormat(fmt)


# ═══════════════════════════════════════════════════════════════════════════════
# get_visible_entries_qt — clone Qt de canvas_rendering.get_visible_entries()
# ═══════════════════════════════════════════════════════════════════════════════
def get_visible_entries_qt(state) -> list[dict]:
    """
    Retourne la liste des entrées visibles dans le répertoire actuel.
    Les entrées de dossiers virtuels sont créées à la volée (pas de ImageTk).
    Identique à canvas_rendering.get_visible_entries() mais sans dépendance tkinter.
    """
    if state is None or not state.images_data:
        return []

    visible = []
    current_dir = getattr(state, "current_directory", "")

    # Icône de remontée (..) si on n'est pas à la racine
    if current_dir:
        parent_entry = {
            "orig_name": "..",
            "bytes": None,
            "extension": "",
            "is_image": False,
            "is_dir": False,
            "is_parent_dir": True,
            "is_corrupted": False,
        }
        visible.append(parent_entry)

    subdirs_seen: set[str] = set()

    for entry in state.images_data:
        if entry.get("is_dir"):
            continue  # marqueurs de dossiers de l'archive — ignorés

        name = entry.get("orig_name", "")

        if not current_dir:
            # Racine : fichiers sans / + un item par sous-dossier de premier niveau
            if "/" not in name:
                visible.append(entry)
            else:
                first_dir = name.split("/")[0] + "/"
                if first_dir not in subdirs_seen:
                    subdirs_seen.add(first_dir)
                    visible.append({
                        "orig_name": first_dir,
                        "bytes": None,
                        "extension": "",
                        "is_image": False,
                        "is_dir": True,
                        "is_parent_dir": False,
                        "is_corrupted": False,
                    })
        else:
            if name.startswith(current_dir):
                remaining = name[len(current_dir):]
                if "/" not in remaining:
                    visible.append(entry)
                else:
                    subdir_name = remaining.split("/")[0] + "/"
                    full_subdir = current_dir + subdir_name
                    if full_subdir not in subdirs_seen:
                        subdirs_seen.add(full_subdir)
                        visible.append({
                            "orig_name": full_subdir,
                            "bytes": None,
                            "extension": "",
                            "is_image": False,
                            "is_dir": True,
                            "is_parent_dir": False,
                            "is_corrupted": False,
                        })

    return visible

# Accesseurs dynamiques — lisent state.thumb_w/h si disponible, sinon valeurs par défaut
def _tw() -> int:
    st = _state_module.state
    return st.thumb_w if st and hasattr(st, 'thumb_w') else 150

def _th() -> int:
    st = _state_module.state
    return st.thumb_h if st and hasattr(st, 'thumb_h') else 200

def _cw() -> int:
    return _tw() + PAD_X * 2

def _ch() -> int:
    return _th() + PAD_Y * 2 + LABEL_H

SEL_OUTLINE = QColor(0, 0, 255)       # bleu pur — identique à outline="blue" tkinter
FOCUS_COLOR = QColor(128, 128, 128)   # gris — identique à outline="gray" tkinter
DROP_COLOR  = QColor(220, 30, 30)


# ── Conversion PIL → QImage (thread-safe) et PIL → QPixmap (thread UI) ────────
def _pil_to_qimage(pil_img) -> QImage:
    """Convertit un PIL Image en QImage. Peut être appelé depuis n'importe quel thread."""
    img = pil_img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(data, img.width, img.height, img.width * 4, QImage.Format_RGBA8888)
    return qimg.copy()  # .copy() détache les données du buffer Python


def _pil_to_qpixmap(pil_img) -> QPixmap:
    return QPixmap.fromImage(_pil_to_qimage(pil_img))


def build_qimage_for_entry(entry: dict):
    """Crée entry['qt_qimage_large'] depuis bytes. Appelé dans le thread background.
    QImage est thread-safe, contrairement à QPixmap."""
    from PIL import Image as _Image
    import io as _io
    tw, th = THUMB_SIZES[2]
    try:
        if entry.get("is_image") and not entry.get("is_corrupted"):
            raw = entry.get("bytes")
            if raw:
                img = _Image.open(_io.BytesIO(raw))
                has_alpha = (img.mode in ('RGBA', 'LA') or
                             (img.mode == 'P' and 'transparency' in img.info))
                thumb = create_centered_thumbnail(img, tw, th, checkerboard=has_alpha)
                entry["qt_qimage_large"] = _pil_to_qimage(thumb)
                return
    except Exception:
        pass
    # Fallback : icône
    icon_pil = get_icon_pil_for_entry(entry)
    if icon_pil is not None:
        try:
            thumb = create_centered_thumbnail(icon_pil, tw, th)
            entry["qt_qimage_large"] = _pil_to_qimage(thumb)
            return
        except Exception:
            pass
    entry["qt_qimage_large"] = None


def _get_pixmap_for_size(entry: dict, tw: int, th: int) -> QPixmap:
    """Retourne un QPixmap à la taille (tw, th).
    Convertit qt_qimage_large → qt_pixmap_large (instantané, thread UI), puis scale Qt.
    """
    # 1. S'assurer qu'on a un qt_pixmap_large
    if not entry.get("qt_pixmap_large"):
        # Cas normal : qt_qimage_large précalculé dans le thread background
        qimg = entry.get("qt_qimage_large")
        if qimg is not None:
            entry["qt_pixmap_large"] = QPixmap.fromImage(qimg)
            entry["qt_qimage_large"] = None  # libère la QImage, QPixmap suffit
        else:
            # Fallback : pas de QImage précalculée (entrée ajoutée par drop, etc.)
            if entry.get("is_image") and not entry.get("is_corrupted"):
                raw = entry.get("bytes")
                if raw:
                    try:
                        from PIL import Image as _Image
                        import io as _io
                        img = _Image.open(_io.BytesIO(raw))
                        has_alpha = (img.mode in ('RGBA', 'LA') or
                                     (img.mode == 'P' and 'transparency' in img.info))
                        thumb = create_centered_thumbnail(img, tw, th, checkerboard=has_alpha)
                        entry["qt_pixmap_large"] = _pil_to_qpixmap(thumb)
                    except Exception:
                        pass
            if not entry.get("qt_pixmap_large"):
                icon_pil = get_icon_pil_for_entry(entry)
                if icon_pil is not None:
                    try:
                        thumb = create_centered_thumbnail(icon_pil, tw, th)
                        entry["qt_pixmap_large"] = _pil_to_qpixmap(thumb)
                    except Exception:
                        pass
        if not entry.get("qt_pixmap_large"):
            pm = QPixmap(tw, th)
            pm.fill(QColor(200, 200, 200))
            return pm

    # 2. Scale Qt — opération rapide en mémoire, pas de PIL
    large_pm = entry["qt_pixmap_large"]
    if large_pm.width() == tw and large_pm.height() == th:
        return large_pm
    return large_pm.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)

def invalidate_pixmap_cache():
    """Efface qt_pixmap_large et qt_qimage_large des entries (appeler lors d'un nouveau chargement)."""
    st = _state_module.state
    if st and hasattr(st, "images_data"):
        for entry in st.images_data:
            entry.pop("qt_pixmap_large", None)
            entry.pop("qt_qimage_large", None)


# ═══════════════════════════════════════════════════════════════════════════════
# DirItem — vignette pour un dossier virtuel ou l'icône ".." (remonter)
# ═══════════════════════════════════════════════════════════════════════════════
class DirItem(QGraphicsItem):
    """
    Représente un dossier virtuel ou l'entrée ".." dans la grille.
    Reproduit le comportement de render_mosaic() tkinter pour les entrées is_dir / is_parent_dir.
    - Icône centrée (directory.png ou folder_up.png) dans _tw() × _th()
    - Nom non-éditable centré sous l'icône (canvas text équivalent)
    - Cadre bleu de sélection (set_selected)
    - Cadre orange pointillé de focus (set_focused)
    """
    def __init__(self, entry: dict, visual_idx: int):
        super().__init__()
        self.entry      = entry
        self.visual_idx = visual_idx
        self._selected  = False
        self._focused   = False

        self._pixmap    = self._build_pixmap()
        self._label     = self._build_label()

        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlag(QGraphicsItem.ItemIsSelectable, False)

    def _build_pixmap(self) -> QPixmap:
        tw, th = _tw(), _th()
        icon_pil = get_icon_pil_for_entry(self.entry)
        if icon_pil is not None:
            try:
                thumb = create_centered_thumbnail(icon_pil, tw, th)
                return _pil_to_qpixmap(thumb)
            except Exception:
                pass
        pm = QPixmap(tw, th)
        pm.fill(QColor(200, 200, 200))
        return pm

    def _build_label(self) -> str:
        name = self.entry.get("orig_name", "")
        if self.entry.get("is_parent_dir"):
            return ".."
        return name.rstrip("/").split("/")[-1]  # dernier segment du chemin

    def boundingRect(self) -> QRectF:
        return QRectF(-4, -4, _tw() + 8, _th() + LABEL_H + 8)

    def paint(self, painter: QPainter, option, widget=None):
        tw, th = _tw(), _th()

        painter.drawPixmap(0, 0, self._pixmap)

        # Nom centré sous l'icône
        theme = get_current_theme()
        font  = _get_current_font(8)
        painter.setFont(font)
        painter.setPen(QColor(theme["text"]))
        painter.drawText(QRectF(0, th + 2, tw, LABEL_H - 2), Qt.AlignHCenter | Qt.AlignTop, self._label)

        # Cadre sélection (bleu)
        if self._selected:
            painter.setPen(QPen(SEL_OUTLINE, 3))
            painter.drawRect(-2, -2, tw + 4, th + 4)

        # Cadre focus (gris pointillé)
        if self._focused:
            pen = QPen(FOCUS_COLOR, 2, Qt.DashLine)
            pen.setDashPattern([4, 4])
            painter.setPen(pen)
            painter.drawRect(-3, -3, tw + 6, th + LABEL_H + 6)

    def set_selected(self, v: bool):
        if self._selected != v:
            self._selected = v
            self.update()

    def set_focused(self, v: bool):
        if self._focused != v:
            self._focused = v
            self.update()

    @staticmethod
    def _format_tooltip(text: str) -> str:
        import html as _html
        escaped = _html.escape(text).replace("\n", "<br>")
        return f'<p style="white-space: normal; max-width: 320px;">{escaped}</p>'

    def _get_tooltip_text(self):
        from modules.qt.tooltips_qt import get_directory_tooltip_text, get_folder_up_tooltip_text
        if self.entry.get("is_parent_dir"):
            return get_folder_up_tooltip_text()
        return get_directory_tooltip_text(_state_module.state, self.entry.get("orig_name", ""))

    def _canvas(self):
        views = self.scene().views() if self.scene() else []
        return views[0] if views else None

    def hoverMoveEvent(self, event):
        c = self._canvas()
        if c:
            text = self._get_tooltip_text()
            c.show_item_tooltip(self._format_tooltip(text) if text else "")
        super().hoverMoveEvent(event)

    def hoverEnterEvent(self, event):
        c = self._canvas()
        if c:
            text = self._get_tooltip_text()
            c.show_item_tooltip(self._format_tooltip(text) if text else "")
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        c = self._canvas()
        if c:
            c.hide_item_tooltip()
        super().hoverLeaveEvent(event)


# ═══════════════════════════════════════════════════════════════════════════════
# Widget d'édition du nom (reproduit make_name_text_widget de canvas_rendering.py)
# ═══════════════════════════════════════════════════════════════════════════════
import math as _math

CHARS_PER_LINE = 12   # identique à l'original

class NameEdit(QTextEdit):
    """
    QTextEdit mono-ligne au repos, multi-ligne au focus.
    Reproduit le comportement du tk.Text de make_name_text_widget :
      - FocusIn  : s'agrandit selon ceil(len / CHARS_PER_LINE)
      - FocusOut : revient à 1 ligne + commit ; si vide → restaure _original_name
      - Return   : commit + repasse à 1 ligne (pas de saut de ligne)
      - 1ère frappe : sauvegarde état undo (save_state_func)
      - Texte centré (setAlignment natif de QTextEdit)
    """
    def __init__(self, entry: dict, save_state_func=None, update_func=None, commit_func=None):
        super().__init__()
        self._entry         = entry
        self._save_state    = save_state_func
        self._update        = update_func
        self._commit        = commit_func
        self._name_changed  = False
        self._original_name = os.path.splitext(entry.get("orig_name", ""))[0]
        self._scene         = None   # injecté par attach_proxy

        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(0)
        self.setViewportMargins(0, 0, 0, 0)
        self.setContentsMargins(0, 0, 0, 0)

        font = _get_current_font(8)
        self.setFont(font)
        self._line_h = QFontMetrics(font).lineSpacing() + 4

        self._apply_theme()

        # Hauteur initiale : 1 ligne
        self._set_height(1)

        base_name = os.path.splitext(entry.get("orig_name", ""))[0]
        self.setPlainText(base_name)
        self.setAlignment(Qt.AlignCenter)

        self.textChanged.connect(self._on_text_changed)

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"QTextEdit {{ border: 1px solid #aaaaaa; background: {theme['entry_bg']}; "
            f"color: {theme['text']}; padding: 0px; margin: 0px; }}"
        )

    def _set_height(self, lines: int):
        self.setFixedHeight(lines * self._line_h)

    def _on_text_changed(self):
        if not self._name_changed:
            self._name_changed = True
            if self._save_state:
                self._save_state()
        content = self.toPlainText()
        if not content.strip():
            return  # vide → on ne touche pas orig_name, focusOutEvent restaurera
        ext = self._entry.get("extension", "")
        if content.lower().endswith(ext.lower()):
            self._entry["orig_name"] = content
        else:
            self._entry["orig_name"] = content + ext
        if _state_module.state:
            _state_module.state.modified = True
        if self._update:
            self._update()
        # setAlignment pendant textChanged crashe Qt — différer hors du signal
        # Vérifie que l'objet C++ existe encore avant d'appeler setAlignment
        import shiboken6
        QTimer.singleShot(0, lambda: shiboken6.isValid(self) and self.setAlignment(Qt.AlignCenter))

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._original_name = os.path.splitext(self._entry.get("orig_name", ""))[0]
        self._name_changed  = False
        content = self.toPlainText()
        needed  = max(1, _math.ceil(max(len(content), 1) / CHARS_PER_LINE))
        self._set_height(needed)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._set_height(1)
        content = self.toPlainText().strip()
        if not content:
            if self._original_name:
                self.blockSignals(True)
                self.setPlainText(self._original_name)
                self.setAlignment(Qt.AlignCenter)
                self.blockSignals(False)
                self._entry["orig_name"] = self._original_name + self._entry.get("extension", "")
                self._name_changed = False
                if self._update:
                    self._update()
                return
        if self._name_changed:
            self._name_changed = False
            if self._update:
                self._update()
        if self._commit and self._entry.get("_real_idx") is not None:
            self._commit(self._entry["_real_idx"])

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            event.accept()
            self._set_height(1)
            if self._name_changed:
                self._name_changed = False
                if self._update:
                    self._update()
            if self._commit and self._entry.get("_real_idx") is not None:
                self._commit(self._entry["_real_idx"])
            proxy = self.graphicsProxyWidget()
            if proxy:
                proxy.clearFocus()
        else:
            super().keyPressEvent(event)

    def sync(self):
        """Synchronise le texte si orig_name a changé externement."""
        base = os.path.splitext(self._entry.get("orig_name", ""))[0]
        if self.toPlainText() != base:
            self.blockSignals(True)
            self.setPlainText(base)
            self.setAlignment(Qt.AlignCenter)
            self.blockSignals(False)


# ═══════════════════════════════════════════════════════════════════════════════
# Item vignette
# ═══════════════════════════════════════════════════════════════════════════════
class ThumbnailItem(QGraphicsItem):
    """
    Représente une vignette dans la grille.
    Correspond à une entrée dans state.images_data (real_idx).

    Rendu fidèle à canvas_rendering.py :
      - Image centrée dans _tw() × _th()
      - Cadre rouge si is_corrupted
      - Cadre bleu de sélection
      - Cadre orange pointillé de focus
      - Nom affiché par QGraphicsTextItem au repos (léger)
      - Double-clic sur le nom → NameEdit créé à la volée, détruit au FocusOut
      - Extension dans un QGraphicsTextItem séparé à droite du nom
    """
    EXT_W  = 40

    def __init__(self, entry: dict, real_idx: int, visual_idx: int, scene: 'QGraphicsScene',
                 save_state_func=None):
        super().__init__()
        self.entry           = entry
        self.real_idx        = real_idx
        self.visual_idx      = visual_idx
        self._selected       = False
        self._focused        = False
        self._scene          = scene
        self._save_state_func = save_state_func

        entry["_real_idx"] = real_idx

        # ── Pixmap ────────────────────────────────────────────────────────────
        self._pixmap = self._build_pixmap(entry)

        # ── Nom + extension : QGraphicsTextItem légers (pas de QTextEdit) ─────
        self._name_text_item = None   # créé dans attach_proxy()
        self._ext_item       = None   # créé dans attach_proxy()
        self._name_w         = 84     # calculé dans attach_proxy()

        # ── NameEdit créé à la demande (clic simple sur zone nom) ─────────────
        self._name_edit  = None
        self._proxy_name = None

        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlag(QGraphicsItem.ItemIsSelectable, False)

    # ── Construction pixmap ────────────────────────────────────────────────────
    def _build_pixmap(self, entry: dict) -> QPixmap:
        return _get_pixmap_for_size(entry, _tw(), _th())

    def attach_proxy(self, scene):
        """Crée les QGraphicsTextItem nom + extension (légers, pas de QTextEdit)."""
        tw, th = _tw(), _th()
        font8  = _get_current_font(8)
        theme  = get_current_theme()

        # Nom sans extension
        base_name = os.path.splitext(self.entry.get("orig_name", ""))[0]
        ext       = self.entry.get("extension", "")

        # Largeur fixe centrée sous la vignette
        name_w = min(84, tw)
        self._name_w = name_w

        name_x = (tw - name_w) / 2

        fm     = QFontMetrics(font8)
        line_h = fm.lineSpacing() + 4

        # Nom tronqué sur UNE seule ligne avec "…", centré dans la box, sans wrap
        elided = fm.elidedText(base_name, Qt.ElideRight, name_w - 4)
        name_item = QGraphicsTextItem(self)
        name_item.setDefaultTextColor(QColor(theme["text"]))
        _apply_name_item_font(name_item, font8, name_w, elided)
        name_item.setPos(name_x, th + 4)
        self._name_text_item = name_item
        self._name_line_h    = line_h
        self._name_x         = name_x

        # Extension — centrée verticalement sur la hauteur de la box
        _CDISPLAY_FORMATS = {".jpg", ".jpeg", ".png", ".gif", ".bmp"}
        is_image = self.entry.get("is_image", True)
        ext_color = QColor(theme["text"]) if (not is_image or ext.lower() in _CDISPLAY_FORMATS) else QColor("#cc0000")
        ext_item = QGraphicsTextItem(self)
        ext_item.setPlainText(ext)
        ext_item.setFont(font8)
        ext_item.setDefaultTextColor(ext_color)
        ext_h    = ext_item.boundingRect().height()
        offset_y = (line_h - ext_h) / 2
        ext_item.setPos(name_x + name_w + 2, th + 4 + offset_y)
        self._ext_item = ext_item

    def _activate_name_edit(self):
        """Remplace le QGraphicsTextItem par un NameEdit éditable."""
        if self._name_edit is not None:
            return  # déjà actif
        tw, th = _tw(), _th()

        self._name_text_item.setVisible(False)

        name_edit = NameEdit(
            self.entry,
            save_state_func=self._save_state_func,
            commit_func=self._on_name_commit,
        )
        name_edit.setFixedWidth(self._name_w)
        name_edit._scene = self._scene

        proxy = QGraphicsProxyWidget(self)
        proxy.setWidget(name_edit)
        proxy.setPos(self._name_x, th + 4)
        self._name_edit  = name_edit
        self._proxy_name = proxy

        # Donne le focus immédiatement
        name_edit.setFocus()

    def _deactivate_name_edit(self):
        """Détruit le NameEdit, remet le QGraphicsTextItem avec le nom à jour."""
        if self._name_edit is None or self._proxy_name is None:
            return
        # Met à jour le texte affiché — 1 ligne tronquée
        base  = os.path.splitext(self.entry.get("orig_name", ""))[0]
        font8 = _get_current_font(8)
        fm    = QFontMetrics(font8)
        elided = fm.elidedText(base, Qt.ElideRight, self._name_w - 4)
        _apply_name_item_font(self._name_text_item, font8, self._name_w, elided)
        self._name_text_item.setVisible(True)
        # Repositionne l'extension (hauteur fixe inchangée)
        if self._ext_item is not None:
            ext_h    = self._ext_item.boundingRect().height()
            offset_y = (self._name_line_h - ext_h) / 2
            self._ext_item.setPos(self._name_x + self._name_w + 2, _th() + 2 + offset_y)

        # Détruit proxy + NameEdit
        if self._proxy_name is not None:
            self._proxy_name.setWidget(None)
            self._scene.removeItem(self._proxy_name)
            self._proxy_name = None
        self._name_edit.deleteLater()
        self._name_edit = None
        self.update()

    def _on_name_commit(self, real_idx):
        """Appelé par NameEdit au Return ou FocusOut."""
        self._deactivate_name_edit()

    # ── Géométrie ──────────────────────────────────────────────────────────────
    def boundingRect(self) -> QRectF:
        return QRectF(-4, -4, _tw() + 8, _th() + LABEL_H + 8)

    # ── Rendu ──────────────────────────────────────────────────────────────────
    def paint(self, painter: QPainter, option, widget=None):
        tw, th = _tw(), _th()
        is_corrupted = self.entry.get("is_corrupted", False)

        painter.drawPixmap(0, 0, self._pixmap)

        if is_corrupted:
            painter.setPen(QPen(QColor(200, 0, 0), 3))
            painter.drawRect(-1, -1, tw + 2, th + 2)

        if self._selected:
            painter.setPen(QPen(SEL_OUTLINE, 3))
            painter.drawRect(-2, -2, tw + 4, th + 4)

        if self._focused:
            pen = QPen(FOCUS_COLOR, 2, Qt.DashLine)
            pen.setDashPattern([4, 4])
            painter.setPen(pen)
            painter.drawRect(-5, -5, tw + 10, th + LABEL_H + 6)

        # Bordure de la zone nom (reproduit le relief="solid" du tk.Text)
        if self._name_edit is None and self._name_text_item is not None:
            name_x = self._name_x
            line_h = self._name_line_h
            painter.setPen(QPen(QColor("#aaaaaa"), 1))
            painter.drawRect(int(name_x), th + 4, self._name_w, line_h)

    # ── Double-clic → édition (fallback si déjà sélectionné) ─────────────────
    def mouseDoubleClickEvent(self, event):
        pos = event.pos()
        if self._is_over_name_box(pos):
            self._activate_name_edit()
        else:
            super().mouseDoubleClickEvent(event)

    # ── Sélection / focus ──────────────────────────────────────────────────────
    def set_selected(self, v: bool):
        if self._selected != v:
            self._selected = v
            self.update()

    def set_focused(self, v: bool):
        if self._focused != v:
            self._focused = v
            self.update()

    # ── Tooltip au survol ──────────────────────────────────────────────────────
    @staticmethod
    def _format_tooltip(text: str) -> str:
        import html as _html
        escaped = _html.escape(text).replace("\n", "<br>")
        return f'<p style="white-space: normal; max-width: 320px;">{escaped}</p>'

    def _is_over_name_box(self, pos) -> bool:
        """Retourne True si la position (coords locales) est dans la zone du nom."""
        if not hasattr(self, '_name_x'):
            return False
        th = _th()
        line_h = self._name_line_h if hasattr(self, '_name_line_h') else 20
        return (self._name_x <= pos.x() <= self._name_x + self._name_w
                and th + 4 <= pos.y() <= th + 4 + line_h)

    def _get_tooltip_text(self):
        entry = self.entry
        c = self._canvas()
        st = c._state if c and hasattr(c, '_state') else _state_module.state
        if entry.get("is_parent_dir"):
            return get_folder_up_tooltip_text()
        elif entry.get("is_dir"):
            return get_directory_tooltip_text(st, entry["orig_name"])
        else:
            return get_tooltip_text(st, self.real_idx)

    def _canvas(self):
        views = self.scene().views() if self.scene() else []
        return views[0] if views else None

    def hoverMoveEvent(self, event):
        c = self._canvas()
        if c:
            if self._is_over_name_box(event.pos()):
                c.hide_item_tooltip()
            else:
                text = self._get_tooltip_text()
                c.show_item_tooltip(self._format_tooltip(text) if text else "")
        super().hoverMoveEvent(event)

    def hoverEnterEvent(self, event):
        c = self._canvas()
        if c:
            if self._is_over_name_box(event.pos()):
                c.hide_item_tooltip()
            else:
                text = self._get_tooltip_text()
                c.show_item_tooltip(self._format_tooltip(text) if text else "")
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        c = self._canvas()
        if c:
            c.hide_item_tooltip()
        super().hoverLeaveEvent(event)

    def sync_name(self):
        """Synchronise le nom affiché si orig_name a changé (ex: renumérotation)."""
        if self._name_edit is not None:
            self._name_edit.sync()
        elif self._name_text_item is not None:
            base  = os.path.splitext(self.entry.get("orig_name", ""))[0]
            font8 = _get_current_font(8)
            fm    = QFontMetrics(font8)
            elided = fm.elidedText(base, Qt.ElideRight, self._name_w - 4)
            _apply_name_item_font(self._name_text_item, font8, self._name_w, elided)

    def reload_pixmap(self):
        """Recharge la pixmap depuis entry (après rotation, undo, etc.)."""
        self._pixmap = self._build_pixmap(self.entry)
        self.update()


# ═══════════════════════════════════════════════════════════════════════════════
# Canvas mosaïque
# ═══════════════════════════════════════════════════════════════════════════════
class MosaicCanvas(QGraphicsView):
    """
    Remplaçant du tk.Canvas + render_mosaic().
    Reçoit l'état via _state_module.state (même pattern que tkinter).
    """
    status_changed = Signal()

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self._state = state
        self._items: list[ThumbnailItem | DirItem] = []   # visual_idx → item (ThumbnailItem ou DirItem)
        self._focused_idx: int | None = None          # visual_idx
        self._drag_start_pos: QPointF | None = None
        self._drag_candidate_visual: int | None = None
        self._drag_deferred_select: "ThumbnailItem | None" = None  # sélection différée si clic sur item déjà sélectionné
        self._drop_indicator_items: list = []
        self._load_callback = None                    # défini par MainWindow après création
        self._canvas_context_menu_callback = None     # défini par MainWindow après création
        self._context_menu_callback = None            # défini par MainWindow après création
        self._dir_context_menu_callback = None        # défini par MainWindow après création
        self._open_image_viewer_callback = None       # défini par MainWindow après création
        self._open_non_image_callback = None          # défini par MainWindow après création
        self._context_menu_open = False               # guard anti-réentrance clic droit
        self._has_subdirectory_structure_callback = None  # () → bool
        self._warn_flatten_callback = None            # () → None  (renumérotation)
        self._warn_flatten_dnd_callback = None        # () → None  (D&D inter-panneaux)
        self._inter_panel_warn_shown = False          # True si la cible a déjà affiché le warning
        self._drop_was_internal      = False          # True si le drop a atterri dans MosaicView
        self._save_state_callback = None              # défini par MainWindow après création
        self._renumber_after_drop_callback = None     # () → None, défini par MainWindow après création
        self._delete_selected_callback = None         # () → None, défini par MainWindow après création
        self._web_import_callback = None              # (urls: list[str]) → None, défini par MainWindow après création
        self._inter_panel_drop_callback = None        # (entries, insert_real, source_canvas) → None

        # Rubber band (sélection par cadre)
        self._rubber_band: QRubberBand | None = None
        self._rubber_start_viewport: QPointF | None = None  # coords viewport

        # Auto-scroll pendant le drag (haut/bas de la vue)
        self._drag_scroll_timer = QTimer(self)
        self._drag_scroll_timer.setInterval(20)  # ms entre chaque tick
        self._drag_scroll_timer.timeout.connect(self._auto_scroll_tick)
        self._drag_scroll_dy: int = 0  # vitesse courante (px/tick, signée)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)

        # ── Tooltip overlay (créé AVANT _apply_theme_bg() qui lui applique son style) ──
        self._overlay_tip = OverlayTooltip(self.viewport())

        self._apply_theme_bg()

        # 3 lignes du canvas vide (recréées dans _show_empty_message)
        self._empty_items: list = []

        # Quand True, render_mosaic n'affiche pas le texte d'accueil (chargement en cours)
        self._loading: bool = False

    def update_name_fonts(self):
        """Met à jour la police des labels de noms après un changement de langue/police."""
        font8 = _get_current_font(8)
        fm = QFontMetrics(font8)
        line_h = fm.lineSpacing() + 4
        th = _th()
        for item in self._items:
            if isinstance(item, ThumbnailItem):
                if item._name_text_item is not None:
                    elided = fm.elidedText(
                        os.path.splitext(item.entry.get("orig_name", ""))[0],
                        Qt.ElideRight,
                        item._name_w - 4,
                    )
                    _apply_name_item_font(item._name_text_item, font8, item._name_w, elided)
                item._name_line_h = line_h
                if item._ext_item is not None:
                    _CDISPLAY_FORMATS = {".jpg", ".jpeg", ".png", ".gif", ".bmp"}
                    ext = item.entry.get("extension", "")
                    is_image = item.entry.get("is_image", True)
                    theme = get_current_theme()
                    ext_color = QColor(theme["text"]) if (not is_image or ext.lower() in _CDISPLAY_FORMATS) else QColor("#cc0000")
                    item._ext_item.setDefaultTextColor(ext_color)
                    item._ext_item.setFont(font8)
                    ext_h = item._ext_item.boundingRect().height()
                    offset_y = (line_h - ext_h) / 2
                    item._ext_item.setPos(item._name_x + item._name_w + 2, th + 4 + offset_y)
            elif isinstance(item, DirItem):
                item.update()  # DirItem appelle _get_current_font() dans paint()

    # ──────────────────────────────────────────────────────────────────────────
    # Mise à jour ciblée d'une vignette (sans reconstruire la scène)
    # ──────────────────────────────────────────────────────────────────────────
    def refresh_thumbnail(self, real_idx: int):
        """Met à jour uniquement le pixmap du ThumbnailItem correspondant à real_idx.
        N'appelle pas render_mosaic — la scène reste intacte."""
        for item in self._items:
            if isinstance(item, ThumbnailItem) and item.real_idx == real_idx:
                item._pixmap = item._build_pixmap(item.entry)
                item.update()
                return

    # ──────────────────────────────────────────────────────────────────────────
    # Rendu de la mosaïque (équivalent render_mosaic)
    # ──────────────────────────────────────────────────────────────────────────
    def render_mosaic(self):
        """Reconstruit la scène à partir de state.images_data (via get_visible_entries_qt)."""
        # Vider les listes AVANT scene.clear() pour éviter les dangling C++ pointers
        self._empty_items.clear()
        self._items.clear()
        self._drop_indicator_items.clear()
        self._scene.clear()

        st = self._state
        if st is None or not st.images_data:
            if not self._loading:
                self._show_empty_message()
            self._scene.setSceneRect(self.viewport().rect().toRectF())
            return

        visible = get_visible_entries_qt(st)
        if not visible:
            if not self._loading:
                self._show_empty_message()
            self._scene.setSceneRect(self.viewport().rect().toRectF())
            return

        cols = self._cols()

        # Réinitialise les maps real ↔ visual
        st.real_to_visual = {}
        st.visual_to_real = {}

        for visual_idx, entry in enumerate(visible):
            col = visual_idx % cols
            row = visual_idx // cols
            x   = col * _cw() + PAD_X
            y   = row * _ch() + PAD_Y

            if entry.get("is_dir") or entry.get("is_parent_dir"):
                # Dossier virtuel ou icône ".."
                item = DirItem(entry, visual_idx)
                item.setPos(x, y)
                self._scene.addItem(item)
                self._items.append(item)
                st.visual_to_real[visual_idx] = None   # pas de real_idx
            else:
                # Image normale — retrouve son real_idx dans images_data
                real_idx = next(
                    (i for i, e in enumerate(st.images_data) if e is entry),
                    None
                )
                if real_idx is None:
                    continue
                item = ThumbnailItem(entry, real_idx, visual_idx, self._scene,
                                    save_state_func=self._save_state_callback)
                item.setPos(x, y)
                self._scene.addItem(item)
                item.attach_proxy(self._scene)
                self._items.append(item)

                st.real_to_visual[real_idx]    = visual_idx
                st.visual_to_real[visual_idx]  = real_idx

                if real_idx in st.selected_indices:
                    item.set_selected(True)

        self._scene.setSceneRect(self._scene.itemsBoundingRect().adjusted(-5, -5, PAD_X, PAD_Y))
        self.status_changed.emit()

    def _apply_theme_bg(self):
        """Applique la couleur de fond du canvas selon le thème (identique à l'original)."""
        st = self._state
        dark = st.dark_mode if st and hasattr(st, 'dark_mode') else False
        bg = "#2b2b2b" if dark else "#f5f5f5"
        self._scene.setBackgroundBrush(QBrush(QColor(bg)))
        sep     = "#555555" if dark else "#cccccc"
        handle  = "#888888" if dark else "#aaaaaa"
        self.setStyleSheet(
            f"QGraphicsView {{ background: {bg}; border: none; }}"
            f"QScrollBar:vertical {{ background: {bg}; width: 14px; margin: 0px; }}"
            f"QScrollBar::handle:vertical {{ background: {sep}; min-height: 20px; border-radius: 3px; }}"
            f"QScrollBar::handle:vertical:hover {{ background: {handle}; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}"
            f"QScrollBar:horizontal {{ background: {bg}; height: 14px; margin: 0px; }}"
            f"QScrollBar::handle:horizontal {{ background: {sep}; min-width: 20px; border-radius: 3px; }}"
            f"QScrollBar::handle:horizontal:hover {{ background: {handle}; }}"
            f"QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}"
        )
        self._overlay_tip._apply_style()

    def show_item_tooltip(self, html: str):
        """Affiche le tooltip overlay avec le contenu HTML donné."""
        self._overlay_tip.show_tooltip(html)

    def hide_item_tooltip(self):
        """Cache le tooltip overlay."""
        self._overlay_tip.hide_tooltip()

    def _show_empty_message(self):
        """3 lignes centrées identiques à l'original (labels.empty_canvas_line1/2/3)."""
        # Vide d'abord les anciens items (scene.clear() les a déjà supprimés)
        self._empty_items.clear()

        line1 = _("labels.empty_canvas_line1") or "Déposez ici un ou plusieurs fichiers CBZ/CBR/PDF ou vos images"
        line2 = _("labels.empty_canvas_line2") or "Une fois un fichier ouvert, vous pouvez continuer à en déposer d'autres"
        line3 = _("labels.empty_canvas_line3") or "Déposez un ou plusieurs dossiers pour lancer un traitement par lot"

        st = self._state
        dark = st.dark_mode if st and hasattr(st, 'dark_mode') else False
        color = QColor("#c0c0c0" if dark else "#a0a0a0")
        font = _get_current_font(18)

        for text in (line1, line2, line3):
            it = self._scene.addText(text)
            it.setDefaultTextColor(color)
            it.setFont(font)
            self._empty_items.append(it)

        # Centrage différé : le viewport peut être 0×0 à la construction
        QTimer.singleShot(0, self._center_empty_items)

    def _center_empty_items(self):
        """Centre les 3 lignes verticalement dans la vue (en coordonnées de scène)."""
        if not self._empty_items:
            return
        vp_w = self.viewport().width()
        margin = 40
        text_w = max(100, vp_w - margin * 2)
        gap = 4
        center_scene = self.mapToScene(self.viewport().rect().center())
        # Applique la largeur de wrap et le centrage avant de mesurer la hauteur
        for it in self._empty_items:
            it.setTextWidth(text_w)
            opt = QTextOption(Qt.AlignHCenter)
            opt.setWrapMode(QTextOption.WordWrap)
            it.document().setDefaultTextOption(opt)
        total_h = sum(it.boundingRect().height() + gap for it in self._empty_items) - gap
        y = center_scene.y() - total_h / 2
        for it in self._empty_items:
            bw = it.boundingRect().width()
            it.setPos(center_scene.x() - bw / 2, y)
            y += it.boundingRect().height() + gap
        # Restreint le sceneRect au viewport pour désactiver les scrollbars
        self._scene.setSceneRect(self.viewport().rect().toRectF())

    def _cols(self) -> int:
        w = self.viewport().width()
        return max(1, w // _cw())

    def _reorder_items_after_drop(self):
        """
        Repositionne les ThumbnailItem existants après un D&D interne,
        sans recréer les items (évite le freeze de render_mosaic).
        Suppose qu'il n'y a pas de dossiers virtuels (has_subdirs=False côté drag).
        """
        st = self._state
        if st is None:
            return
        cols = self._cols()

        # Construit un dict entry_id → ThumbnailItem existant
        item_by_entry_id = {id(item.entry): item for item in self._items
                            if isinstance(item, ThumbnailItem)}

        # Nouvel ordre visuel = ordre de images_data
        new_items: list = []
        st.real_to_visual = {}
        st.visual_to_real = {}

        for real_idx, entry in enumerate(st.images_data):
            item = item_by_entry_id.get(id(entry))
            if item is None:
                continue  # entrée inconnue — ne devrait pas arriver
            # Met à jour les indices
            item.real_idx   = real_idx
            item.visual_idx = real_idx
            entry["_real_idx"] = real_idx
            # Repositionne
            col = real_idx % cols
            row = real_idx // cols
            item.setPos(col * _cw() + PAD_X, row * _ch() + PAD_Y)
            # Sélection
            item.set_selected(real_idx in st.selected_indices)
            new_items.append(item)
            st.real_to_visual[real_idx]  = real_idx
            st.visual_to_real[real_idx]  = real_idx

        self._items = new_items
        self._focused_idx = None  # réinitialise le focus visuel
        # Synchronise les noms (renumérotation, etc.)
        for item in new_items:
            item.sync_name()
        self._scene.setSceneRect(self._scene.itemsBoundingRect().adjusted(-5, -5, PAD_X, PAD_Y))
        self.status_changed.emit()

    # ──────────────────────────────────────────────────────────────────────────
    # Resize → relayout
    # ──────────────────────────────────────────────────────────────────────────
    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._relayout)

    def _relayout(self):
        if self._empty_items:
            self._center_empty_items()
            return
        cols = self._cols()
        for item in self._items:
            col = item.visual_idx % cols
            row = item.visual_idx // cols
            item.setPos(col * _cw() + PAD_X, row * _ch() + PAD_Y)
        if self._items:
            self._scene.setSceneRect(self._scene.itemsBoundingRect().adjusted(-5, -5, PAD_X, PAD_Y))

    # ──────────────────────────────────────────────────────────────────────────
    # Navigation dans les sous-dossiers (identique à MosaicView.py tkinter)
    # ──────────────────────────────────────────────────────────────────────────
    def navigate_to_directory(self, directory_name: str):
        """Entre dans un sous-dossier."""
        if self._state:
            self._state.current_directory = directory_name
            self._state.selected_indices.clear()
            self._focused_idx = None
        self.render_mosaic()

    def navigate_up(self):
        """Remonte d'un niveau dans l'arborescence."""
        if not self._state:
            return
        cur = getattr(self._state, "current_directory", "")
        if not cur:
            return
        stripped = cur.rstrip("/")
        if "/" in stripped:
            parent = "/".join(stripped.split("/")[:-1]) + "/"
        else:
            parent = ""
        self._state.current_directory = parent
        self._state.selected_indices.clear()
        self._focused_idx = None
        self.render_mosaic()

    # ──────────────────────────────────────────────────────────────────────────
    # Utilitaires sélection
    # ──────────────────────────────────────────────────────────────────────────
    def _item_at(self, scene_pos: QPointF) -> 'ThumbnailItem | DirItem | None':
        for it in self._scene.items(scene_pos):
            if isinstance(it, (ThumbnailItem, DirItem)):
                return it
        return None

    def _set_focus(self, visual_idx: int | None):
        if self._focused_idx is not None:
            item = self._item_by_visual(self._focused_idx)
            if item:
                item.set_focused(False)
        self._focused_idx = visual_idx
        if visual_idx is not None:
            item = self._item_by_visual(visual_idx)
            if item:
                item.set_focused(True)
        if self._state:
            self._state.focused_index = visual_idx

    def _item_by_visual(self, visual_idx: int) -> 'ThumbnailItem | DirItem | None':
        for it in self._items:
            if it.visual_idx == visual_idx:
                return it
        return None

    def _clear_selection(self):
        for it in self._items:
            it.set_selected(False)
        if self._state:
            self._state.selected_indices.clear()

    def _clear_selection_and_emit(self):
        self._clear_selection()
        self.status_changed.emit()

    def _select_all(self):
        for it in self._items:
            if isinstance(it, ThumbnailItem):
                it.set_selected(True)
                if self._state:
                    self._state.selected_indices.add(it.real_idx)
        self.status_changed.emit()

    def _invert_selection(self):
        if not self._state or not self._state.images_data:
            return
        all_indices = set(range(len(self._state.images_data)))
        new_selection = all_indices - self._state.selected_indices
        self._state.selected_indices = new_selection
        for it in self._items:
            if isinstance(it, ThumbnailItem):
                it.set_selected(it.real_idx in new_selection)
        self.status_changed.emit()

    def _select_item(self, item, add=False):
        if not add:
            self._clear_selection()
        if isinstance(item, ThumbnailItem):
            item.set_selected(True)
            if self._state:
                self._state.selected_indices.add(item.real_idx)
        elif isinstance(item, DirItem):
            item.set_selected(True)
        self.status_changed.emit()

    def _toggle_item(self, item):
        if isinstance(item, DirItem):
            return  # les dossiers ne sont pas sélectionnables au sens multi-sélection
        st = self._state
        if item.real_idx in (st.selected_indices if st else set()):
            item.set_selected(False)
            if st:
                st.selected_indices.discard(item.real_idx)
        else:
            item.set_selected(True)
            if st:
                st.selected_indices.add(item.real_idx)
        self.status_changed.emit()

    # ──────────────────────────────────────────────────────────────────────────
    # Événements souris
    # ──────────────────────────────────────────────────────────────────────────
    def mousePressEvent(self, event):
        scene_pos = self.mapToScene(event.position().toPoint())
        item = self._item_at(scene_pos)

        if event.button() == Qt.LeftButton:
            self.setFocus()
            if item is None:
                # Démarre le rubber band sur zone vide
                ctrl  = bool(event.modifiers() & Qt.ControlModifier)
                shift = bool(event.modifiers() & Qt.ShiftModifier)
                if not ctrl and not shift:
                    self._clear_selection()
                self._set_focus(None)
                self.status_changed.emit()
                self._rubber_start_viewport = event.position()
                if self._rubber_band is None:
                    self._rubber_band = QRubberBand(QRubberBand.Rectangle, self)
                vp = event.position().toPoint()
                self._rubber_band.setGeometry(QRect(vp, QSize()))
                self._rubber_band.show()
                super().mousePressEvent(event)
                return
            elif isinstance(item, DirItem):
                # Clic sur un dossier : sélectionne uniquement le dossier, efface les images
                self._clear_selection()
                item.set_selected(True)
                self._set_focus(item.visual_idx)
                # Pas de drag possible sur les dossiers
            else:
                ctrl  = bool(event.modifiers() & Qt.ControlModifier)
                shift = bool(event.modifiers() & Qt.ShiftModifier)
                if ctrl:
                    self._toggle_item(item)
                elif shift and self._focused_idx is not None:
                    self._select_range(self._focused_idx, item.visual_idx)
                else:
                    item_pos = item.mapFromScene(scene_pos)
                    # Clic sur la zone nom → édition immédiate (1 seul clic)
                    if (isinstance(item, ThumbnailItem)
                            and item._is_over_name_box(item_pos)):
                        self._select_item(item)
                        self._set_focus(item.visual_idx)
                        item._activate_name_edit()
                        super().mousePressEvent(event)
                        return
                    # Si l'item est déjà sélectionné, reporter la sélection exclusive
                    # au mouseRelease (pour ne pas casser le drag multi-sélection)
                    st = self._state
                    already_selected = (
                        isinstance(item, ThumbnailItem)
                        and st is not None
                        and item.real_idx in st.selected_indices
                        and len(st.selected_indices) > 1
                    )
                    if already_selected:
                        self._drag_deferred_select = item
                    else:
                        self._select_item(item)
                self._set_focus(item.visual_idx)
                self._drag_start_pos       = event.position()
                self._drag_candidate_visual = item.visual_idx

        elif event.button() == Qt.RightButton:
            if self._context_menu_open:
                return
            self._context_menu_open = True
            try:
                if item is not None and isinstance(item, ThumbnailItem):
                    if self._context_menu_callback:
                        self._context_menu_callback(event.globalPosition().toPoint(), item.real_idx)
                elif item is not None and isinstance(item, DirItem):
                    if self._dir_context_menu_callback:
                        self._dir_context_menu_callback(event.globalPosition().toPoint())
                elif item is None:
                    if self._canvas_context_menu_callback:
                        self._canvas_context_menu_callback(event.globalPosition().toPoint())
            finally:
                self._context_menu_open = False
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # Rubber band actif
        if self._rubber_band is not None and self._rubber_start_viewport is not None:
            start = self._rubber_start_viewport.toPoint()
            cur   = event.position().toPoint()
            self._rubber_band.setGeometry(QRect(start, cur).normalized())
            super().mouseMoveEvent(event)
            return
        if (event.buttons() & Qt.LeftButton
                and self._drag_start_pos is not None
                and self._drag_candidate_visual is not None):
            delta = (event.position() - self._drag_start_pos).manhattanLength()
            if delta > QApplication.startDragDistance():
                self._start_drag()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        # Fin du rubber band
        if self._rubber_band is not None and self._rubber_start_viewport is not None:
            self._rubber_band.hide()
            start_vp = self._rubber_start_viewport.toPoint()
            end_vp   = event.position().toPoint()
            rect_vp  = QRect(start_vp, end_vp).normalized()

            # Sélectionne les items dont le centre est dans le rectangle (coords scène)
            if rect_vp.width() > 4 or rect_vp.height() > 4:
                scene_rect = QRectF(
                    self.mapToScene(rect_vp.topLeft()),
                    self.mapToScene(rect_vp.bottomRight())
                )
                ctrl  = bool(event.modifiers() & Qt.ControlModifier)
                shift = bool(event.modifiers() & Qt.ShiftModifier)
                for it in self._items:
                    if not isinstance(it, ThumbnailItem):
                        continue
                    item_center = it.mapToScene(it.boundingRect().center())
                    if scene_rect.contains(item_center):
                        if ctrl and it.real_idx in self._state.selected_indices:
                            it.set_selected(False)
                            self._state.selected_indices.discard(it.real_idx)
                        else:
                            it.set_selected(True)
                            self._state.selected_indices.add(it.real_idx)
                self.status_changed.emit()

            self._rubber_start_viewport = None
            super().mouseReleaseEvent(event)
            return

        # Sélection différée : clic simple sur item déjà sélectionné sans drag → sélection exclusive
        if self._drag_deferred_select is not None:
            self._select_item(self._drag_deferred_select)
            self._drag_deferred_select = None
        self._drag_start_pos        = None
        self._drag_candidate_visual = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        scene_pos = self.mapToScene(event.position().toPoint())
        # Si le clic tombe sur un QGraphicsProxyWidget (NameEdit), ignorer
        for scene_item in self._scene.items(scene_pos):
            if isinstance(scene_item, QGraphicsProxyWidget):
                super().mouseDoubleClickEvent(event)
                return
        item = self._item_at(scene_pos)
        if item is None:
            super().mouseDoubleClickEvent(event)
            return
        if isinstance(item, DirItem):
            if item.entry.get("is_parent_dir"):
                self.navigate_up()
            else:
                self.navigate_to_directory(item.entry.get("orig_name", ""))
        else:
            entry = self._state.images_data[item.real_idx]
            if entry.get("is_image"):
                if self._open_image_viewer_callback:
                    self._open_image_viewer_callback(item.real_idx)
            else:
                if self._open_non_image_callback:
                    self._open_non_image_callback(entry)
        super().mouseDoubleClickEvent(event)

    def _select_range(self, from_visual: int, to_visual: int):
        """Sélection Shift+clic : plage de visual_idx (images seulement)."""
        lo, hi = sorted((from_visual, to_visual))
        self._clear_selection()
        for it in self._items:
            if lo <= it.visual_idx <= hi and isinstance(it, ThumbnailItem):
                it.set_selected(True)
                if self._state:
                    self._state.selected_indices.add(it.real_idx)
        self.status_changed.emit()

    # ──────────────────────────────────────────────────────────────────────────
    # Drag interne (réordonnancement) + drag sortant (CF_HDROP)
    # ──────────────────────────────────────────────────────────────────────────
    def _start_drag(self):
        # Annule la sélection différée — le drag commence, on garde la multi-sélection telle quelle
        self._drag_deferred_select = None
        st = self._state
        selected_reals = sorted(st.selected_indices) if st and st.selected_indices else []
        if not selected_reals and self._drag_candidate_visual is not None:
            real = st.visual_to_real.get(self._drag_candidate_visual) if st else None
            if real is not None:
                selected_reals = [real]
        if not selected_reals:
            return

        mime = QMimeData()

        # ── Mime type interne pour le réordonnancement ────────────────────────
        # Bloqué si : sous-dossiers, ou sélection contient au moins un non-image
        # (drag-out CF_HDROP reste toujours autorisé)
        has_subdirs = (
            self._has_subdirectory_structure_callback is not None
            and self._has_subdirectory_structure_callback()
        )
        st_data = st.images_data if st else []
        has_non_image = any(
            not st_data[i].get("is_image")
            for i in selected_reals
            if i < len(st_data)
        )
        # Identifiant du canvas source — toujours présent pour permettre la détection
        # inter-panneaux même en cas de sous-dossiers (le warning y est affiché côté cible)
        mime.setData("application/x-mosaicview-panel", str(id(self)).encode())
        if has_subdirs:
            # Pas de mime indices → pas d'indicateur rouge, pas de réordonnancement
            # Le warning est affiché à la fin si drop interne
            pass
        else:
            indices_bytes = ",".join(str(i) for i in selected_reals).encode()
            mime.setData("application/x-mosaicview-indices", indices_bytes)

        # ── CF_HDROP pour le drag-out vers l'explorateur ──────────────────────
        urls = []
        tmp_dir = None
        entries_to_export = [st.images_data[i] for i in selected_reals if i < len(st.images_data)]
        if entries_to_export:
            try:
                tmp_dir = os.path.join(get_mosaicview_temp_dir(), f"drag_{uuid.uuid4().hex[:8]}")
                os.makedirs(tmp_dir, exist_ok=True)
                # Corrige le chemin court Windows (PROPRI~1 → PROPRIETAIRE)
                try:
                    import ctypes
                    buf = ctypes.create_unicode_buffer(32768)
                    ctypes.windll.kernel32.GetLongPathNameW(tmp_dir, buf, 32768)
                    tmp_dir = buf.value or tmp_dir
                except Exception:
                    pass
                for entry in entries_to_export:
                    data = entry.get("bytes") or entry.get("data")
                    if not data:
                        continue
                    fname = os.path.basename(entry.get("orig_name", "image.png"))
                    fpath = os.path.join(tmp_dir, fname)
                    with open(fpath, "wb") as f:
                        f.write(data)
                    urls.append(QUrl.fromLocalFile(fpath))
            except Exception as e:
                print(f"[drag-out] erreur export temp : {e}")
        if urls:
            mime.setUrls(urls)

        first_item = self._item_by_visual(
            st.real_to_visual.get(selected_reals[0], 0) if st else 0
        )
        pixmap = first_item._pixmap.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation) if first_item else QPixmap()

        # Griser les vignettes sélectionnées pendant le drag (comme l'original tkinter)
        grayed_items: list[tuple] = []  # [(item, original_pixmap), ...]
        for real_idx in selected_reals:
            visual = st.real_to_visual.get(real_idx) if st else None
            if visual is None:
                continue
            item = self._item_by_visual(visual)
            if item is None or not isinstance(item, ThumbnailItem):
                continue
            orig_pm = item._pixmap
            gray_img = orig_pm.toImage().convertToFormat(QImage.Format_Grayscale8)
            item._pixmap = QPixmap.fromImage(gray_img.convertToFormat(QImage.Format_RGB32))
            item.update()
            grayed_items.append((item, orig_pm))

        drag = QDrag(self)
        drag.setMimeData(mime)
        if not pixmap.isNull():
            drag.setPixmap(pixmap)
            drag.setHotSpot(pixmap.rect().center())

        result_action = drag.exec(Qt.CopyAction | Qt.MoveAction)

        # Restaurer les pixmaps originaux après le drag
        # (les items peuvent être déjà détruits si render_mosaic a été appelé
        # pendant le drag — cas inter-panneaux)
        for item, orig_pm in grayed_items:
            try:
                item._pixmap = orig_pm
                item.update()
            except RuntimeError:
                pass

        # Si sous-dossiers et drop interne tenté → avertir (le réordonnancement n'a pas eu lieu)
        # Qt retourne IgnoreAction (pas MoveAction) quand il n'y a pas de mime interne
        # Ne pas afficher si la cible inter-panneaux a déjà affiché le warning
        if has_subdirs and result_action != Qt.CopyAction and self._warn_flatten_dnd_callback:
            if not self._inter_panel_warn_shown:
                self._warn_flatten_dnd_callback()
        self._inter_panel_warn_shown = False

        # Drop interne (réordonnancement) → les fichiers temp ne sont pas utilisés, on nettoie.
        # Drop externe (explorateur, navigateur…) → on laisse intact ; la cible lit le fichier
        # après drag.exec(), et le dossier sera nettoyé au prochain démarrage.
        if self._drop_was_internal and tmp_dir and os.path.isdir(tmp_dir):
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
        self._drop_was_internal = False

        self._drag_start_pos        = None
        self._drag_candidate_visual = None
        self._clear_drop_indicator()

    def _calc_insert_visual(self, scene_pos: QPointF) -> int:
        """Calcule la position d'insertion (visual_idx) selon la position de la souris."""
        cols = self._cols()
        cw, ch = _cw(), _ch()
        x = scene_pos.x() - PAD_X
        y = scene_pos.y() - PAD_Y
        col = int(x // cw)
        row = int(y // ch)
        # Dans la cellule, si on est dans la moitié droite → insertion après
        cell_x = x - col * cw
        if cell_x > cw / 2:
            col += 1
        col = max(0, min(col, cols))
        insert = row * cols + col
        return max(0, min(insert, len(self._items)))

    def _auto_scroll_tick(self):
        """Scrolle verticalement d'un pas à chaque tick pendant un drag."""
        if self._drag_scroll_dy == 0:
            self._drag_scroll_timer.stop()
            return
        sb = self.verticalScrollBar()
        sb.setValue(sb.value() + self._drag_scroll_dy)

    def _draw_drop_indicator(self, scene_pos: QPointF):
        """Dessine la ligne rouge d'indication de drop."""
        self._clear_drop_indicator()
        insert = self._calc_insert_visual(scene_pos)
        cols = self._cols()
        if insert < len(self._items):
            item = self._item_by_visual(insert)
            if item:
                p = item.pos()
                x, y = p.x(), p.y()
            else:
                return
        else:
            # Après le dernier
            if self._items:
                last = self._items[-1]
                p = last.pos()
                x = p.x() + _tw()
                y = p.y()
            else:
                x, y = PAD_X, PAD_Y

        s = 9  # demi-largeur des triangles (identique à _ARROW_SIZE = 9 de drag_drop.py)
        y1 = y
        y2 = y + _th()
        pen  = QPen(DROP_COLOR, 1)
        brush = QBrush(DROP_COLOR)

        # Triangle haut (▼ — pointe vers le bas)
        top = QPolygonF([QPointF(x - s, y1), QPointF(x + s, y1), QPointF(x, y1 + s)])
        t1 = self._scene.addPolygon(top, pen, brush)
        self._drop_indicator_items.append(t1)

        # Ligne verticale centrale
        line = self._scene.addLine(x, y1 + s, x, y2 - s, QPen(DROP_COLOR, 4))
        self._drop_indicator_items.append(line)

        # Triangle bas (▲ — pointe vers le haut)
        bot = QPolygonF([QPointF(x - s, y2), QPointF(x + s, y2), QPointF(x, y2 - s)])
        t2 = self._scene.addPolygon(bot, pen, brush)
        self._drop_indicator_items.append(t2)

    def _clear_drop_indicator(self):
        for item in self._drop_indicator_items:
            self._scene.removeItem(item)
        self._drop_indicator_items.clear()

    # ──────────────────────────────────────────────────────────────────────────
    # Drag entrant (interne + fichiers externes)
    # ──────────────────────────────────────────────────────────────────────────
    def dragEnterEvent(self, event):
        if (event.mimeData().hasFormat("application/x-mosaicview-indices")
                or event.mimeData().hasUrls()):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        # Auto-scroll : zone d'activation = 60 px en haut et en bas du viewport
        _SCROLL_ZONE = 60
        _SCROLL_MAX  = 50  # px/tick max
        vp_y  = event.position().y()
        vp_h  = self.viewport().height()
        if vp_y < _SCROLL_ZONE:
            ratio = 1.0 - vp_y / _SCROLL_ZONE
            self._drag_scroll_dy = -max(2, int(ratio * _SCROLL_MAX))
            if not self._drag_scroll_timer.isActive():
                self._drag_scroll_timer.start()
        elif vp_y > vp_h - _SCROLL_ZONE:
            ratio = 1.0 - (vp_h - vp_y) / _SCROLL_ZONE
            self._drag_scroll_dy = max(2, int(ratio * _SCROLL_MAX))
            if not self._drag_scroll_timer.isActive():
                self._drag_scroll_timer.start()
        else:
            self._drag_scroll_dy = 0
            self._drag_scroll_timer.stop()

        if event.mimeData().hasFormat("application/x-mosaicview-indices"):
            scene_pos = self.mapToScene(event.position().toPoint())
            self._draw_drop_indicator(scene_pos)
            event.acceptProposedAction()
        elif event.mimeData().hasUrls():
            event.acceptProposedAction()

    def leaveEvent(self, event):
        self.hide_item_tooltip()
        super().leaveEvent(event)

    def dragLeaveEvent(self, event):
        self._drag_scroll_timer.stop()
        self._drag_scroll_dy = 0
        self._clear_drop_indicator()

    def dropEvent(self, event):
        self._drag_scroll_timer.stop()
        self._drag_scroll_dy = 0
        self._clear_drop_indicator()
        mime = event.mimeData()

        # Détecte si le drag vient d'un autre panneau (présent même sans indices, cas sous-dossiers)
        source_id = None
        if mime.hasFormat("application/x-mosaicview-panel"):
            source_id = bytes(mime.data("application/x-mosaicview-panel")).decode()
        is_inter_panel = (source_id is not None and source_id != str(id(self))
                          and self._inter_panel_drop_callback is not None)

        # Drop inter-panneaux sans indices = source a des sous-dossiers → warning + rejet
        if is_inter_panel and not mime.hasFormat("application/x-mosaicview-indices"):
            if self._warn_flatten_dnd_callback:
                self._warn_flatten_dnd_callback()
            # Signale à la source de ne pas afficher son propre warning
            src_canvas = event.source()
            if isinstance(src_canvas, MosaicCanvas):
                src_canvas._inter_panel_warn_shown = True
            event.ignore()
            return

        # Drop inter-panneaux : destination a des sous-dossiers → warning + rejet
        if is_inter_panel and (
            self._has_subdirectory_structure_callback is not None
            and self._has_subdirectory_structure_callback()
        ):
            if self._warn_flatten_dnd_callback:
                self._warn_flatten_dnd_callback()
            # Signale à la source de ne pas afficher son propre warning
            src_canvas = event.source()
            if isinstance(src_canvas, MosaicCanvas):
                src_canvas._inter_panel_warn_shown = True
            event.ignore()
            return

        if mime.hasFormat("application/x-mosaicview-indices"):
            raw = bytes(mime.data("application/x-mosaicview-indices")).decode()
            dragged_reals = [int(i) for i in raw.split(",") if i]
            if not dragged_reals:
                return
            scene_pos = self.mapToScene(event.position().toPoint())
            insert_visual = self._calc_insert_visual(scene_pos)
            st = self._state

            if is_inter_panel:
                # ── Déplacement inter-panneaux ────────────────────────────────
                insert_real = st.visual_to_real.get(insert_visual, len(st.images_data))
                if insert_real is None:
                    insert_real = len(st.images_data)
                self._inter_panel_drop_callback(
                    dragged_reals, insert_real, source_id
                )
                src_canvas = event.source()
                if isinstance(src_canvas, MosaicCanvas):
                    src_canvas._drop_was_internal = True
                event.acceptProposedAction()
            else:
                # ── Réordonnancement interne ──────────────────────────────────
                # Les non-images ne participent pas au réordonnancement intra-panneau
                dragged_reals = [i for i in dragged_reals
                                 if i < len(st.images_data) and st.images_data[i].get("is_image")]
                if not dragged_reals:
                    event.ignore()
                    return
                # Convertit insert_visual en real_idx cible
                insert_real = st.visual_to_real.get(insert_visual, len(st.images_data))
                if insert_real is None:
                    insert_real = len(st.images_data)
                # Retire les entrées draguées de images_data
                dragged_entries = [st.images_data[i] for i in dragged_reals if i < len(st.images_data)]
                remaining = [e for i, e in enumerate(st.images_data) if i not in set(dragged_reals)]
                # Recalcule insert_real dans la liste restante
                n_before = sum(1 for i in dragged_reals if i < insert_real)
                insert_real = max(0, insert_real - n_before)
                insert_real = min(insert_real, len(remaining))
                # Réinsère
                for offset, entry in enumerate(dragged_entries):
                    remaining.insert(insert_real + offset, entry)
                st.images_data = remaining
                st.modified = True
                from modules.qt.comic_info import sync_pages_in_xml_data
                sync_pages_in_xml_data(st)
                # Met à jour selected_indices
                new_reals = set()
                for entry in dragged_entries:
                    idx = remaining.index(entry)
                    new_reals.add(idx)
                st.selected_indices = new_reals
                if self._renumber_after_drop_callback:
                    self._renumber_after_drop_callback()
                if self._save_state_callback:
                    self._save_state_callback()
                self._reorder_items_after_drop()
                self._drop_was_internal = True
                event.acceptProposedAction()

        elif mime.hasUrls() and event.source() is not self:
            # ── Drop de fichiers externes ────────────────────────────────────
            # Ignoré si le drag vient de MosaicView lui-même (non-image drag-out)
            local_paths = []
            web_urls    = []
            for u in mime.urls():
                local = u.toLocalFile()
                if local:
                    local_paths.append(local)
                else:
                    url_str = u.toString()
                    if url_str.startswith(('http://', 'https://')):
                        web_urls.append(url_str)

            if local_paths and self._load_callback:
                self._load_callback(local_paths, from_drop=True)
            elif web_urls and self._web_import_callback:
                # Tente d'extraire l'URL d'image depuis le fragment HTML du MIME
                # (quand on droppe une image depuis un navigateur, text/html contient
                # le <img src="..."> de l'image droppée, pas de la page entière)
                image_url = None
                if mime.hasHtml():
                    from modules.qt.web_import_qt import _extract_single_img_src
                    image_url = _extract_single_img_src(mime.html(), web_urls[0])
                if image_url:
                    self._web_import_callback([image_url])
                else:
                    self._web_import_callback(web_urls)
            event.acceptProposedAction()

    # ──────────────────────────────────────────────────────────────────────────
    # Navigation clavier
    # ──────────────────────────────────────────────────────────────────────────
    def keyPressEvent(self, event):
        # Si un NameEdit est actif (proxy focusé dans la scène), lui déléguer l'événement
        focused_item = self._scene.focusItem() if self._scene else None
        if isinstance(focused_item, QGraphicsProxyWidget):
            w = focused_item.widget()
            if isinstance(w, NameEdit):
                w.keyPressEvent(event)
                return
        # Escape : toujours traité, même canvas vide
        if event.key() == Qt.Key_Escape:
            self._clear_selection()
            self.status_changed.emit()
            return
        if not self._items:
            super().keyPressEvent(event)
            return

        cols = self._cols()
        n    = len(self._items)
        cur  = self._focused_idx if self._focused_idx is not None else -1
        key  = event.key()
        new_visual = None

        if key == Qt.Key_Right:
            new_visual = min(cur + 1, n - 1)
        elif key == Qt.Key_Left:
            new_visual = max(cur - 1, 0)
        elif key == Qt.Key_Down:
            new_visual = min(cur + cols, n - 1)
        elif key == Qt.Key_Up:
            new_visual = max(cur - cols, 0)
        elif key == Qt.Key_Home:
            new_visual = 0
        elif key == Qt.Key_End:
            new_visual = n - 1
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            if cur >= 0:
                focused_item = self._item_by_visual(cur)
                if isinstance(focused_item, DirItem):
                    if focused_item.entry.get("is_parent_dir"):
                        self.navigate_up()
                    else:
                        self.navigate_to_directory(focused_item.entry.get("orig_name", ""))
                elif isinstance(focused_item, ThumbnailItem):
                    entry = self._state.images_data[focused_item.real_idx]
                    if entry.get("is_image"):
                        if self._open_image_viewer_callback:
                            self._open_image_viewer_callback(focused_item.real_idx)
                    else:
                        if self._open_non_image_callback:
                            self._open_non_image_callback(entry)
            return
        elif key == Qt.Key_A and event.modifiers() & Qt.ControlModifier:
            self._select_all()
            return
        elif key == Qt.Key_I and event.modifiers() & Qt.ControlModifier:
            self._invert_selection()
            return
        elif key == Qt.Key_Space:
            # Espace : toggle sélection sur la vignette focusée (images seulement)
            if cur >= 0:
                item = self._item_by_visual(cur)
                if item and isinstance(item, ThumbnailItem):
                    self._toggle_item(item)
            return
        elif key == Qt.Key_Escape:
            self._clear_selection()
            self.status_changed.emit()
            return
        elif key in (Qt.Key_Delete, Qt.Key_Backspace):
            if self._delete_selected_callback:
                self._delete_selected_callback()
            return

        if new_visual is not None and new_visual != cur:
            item = self._item_by_visual(new_visual)
            if item:
                # Les flèches déplacent uniquement le focus (comme dans l'original tkinter).
                # La sélection n'est PAS modifiée par la navigation clavier seule.
                # Shift+flèche : étend la sélection. Sans modificateur : focus seul.
                if event.modifiers() & Qt.ShiftModifier:
                    self._select_range(cur, new_visual)
                self._set_focus(new_visual)
                self._scroll_to(item)
                self.status_changed.emit()

        super().keyPressEvent(event)

    def _scroll_to(self, item: ThumbnailItem):
        rect = item.mapToScene(item.boundingRect()).boundingRect()
        self.ensureVisible(rect.x(), rect.y(), rect.width(), rect.height(), 20, 20)

    def ensureVisible(self, x, y, w, h, xmargin=0, ymargin=0):
        self.scene()  # force scene refresh
        from PySide6.QtCore import QRectF
        self.centerOn(x + w/2, y + h/2)
