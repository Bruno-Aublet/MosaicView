"""
modules/qt/status_bar_qt.py
Barre de statut Qt pour MosaicView.

Placée dans le panneau central uniquement (pas sous la colonne gauche),
contrairement à QMainWindow.setStatusBar() qui s'étend sur toute la largeur.
"""

import os

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QSizePolicy
from PySide6.QtCore import Qt, QEvent, QUrl
from PySide6.QtGui import QPixmap, QImage, qGray, qAlpha

from modules.qt.localization import _
from modules.qt.utils import format_file_size
from modules.qt.font_manager_qt import get_current_font
from modules.qt.state import get_current_theme
from modules.qt.overlay_tooltip_qt import OverlayTooltip
from modules.qt.memory_usage import memory_level, percent, panel_memory_estimate


# Dernière mesure de la mémoire de MosaicView (memory_usage.MemoryMeasure, ou
# None si Windows ne l'a pas donnée). Une seule mesure pour toute
# l'application, faite par la fenêtre principale toutes les secondes
# (MainWindow._refresh_memory_indicators) et montrée par chaque barre de
# statut ; une barre créée entre deux mesures (ouverture du double panneau)
# affiche ainsi la dernière tout de suite.
_memory_measure = None


def set_memory_measure(measure):
    global _memory_measure
    _memory_measure = measure


# Jauge de l'info-bulle de l'indicateur de mémoire, une image par couleur
# (memory_usage.memory_level), affichée à _GAUGE_TOOLTIP_SIZE px. Le texte
# riche de Qt réduit une image sans lissage : les icônes sont livrées déjà
# réduites, à 48 px et à 96 px (« @2x ») pour un écran à haute densité.
_GAUGE_ICONS = {
    "green": "Memory_Gauge_01_Green_48",
    "orange": "Memory_Gauge_02_Orange_48",
    "red": "Memory_Gauge_03_Red_48",
}
_GAUGE_TOOLTIP_SIZE = 48


def _gauge_icon_src(level, ratio):
    """Adresse (file:///…) de la jauge `level` pour l'info-bulle : version 96 px
    dès que l'écran compte plus d'un pixel réel par pixel (ratio), réduite
    par Qt à 48 px affichés ; None si le fichier manque (info-bulle sans
    image)."""
    name = _GAUGE_ICONS[level] + ("@2x" if ratio > 1 else "") + ".png"
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "icons", name)
    if not os.path.isfile(path):
        return None
    return QUrl.fromLocalFile(path).toString()


def _memory_tooltip_html(measure, state, ratio):
    """Info-bulle de l'indicateur de mémoire : jauge de la couleur du seuil,
    puis MosaicView (programmes annexes compris), les programmes annexes
    quand il y en a (nommés : seulement ceux qui tournent), et l'estimation
    du panneau (state), chacun en taille et en part de la mémoire du PC."""
    import html as _html
    if measure is None or not measure.ram:
        lines = [_("tooltip.memory_indicator_unknown")]
        src = None
    else:
        ram = measure.ram
        total = measure.app + measure.helpers
        lines = [_("tooltip.memory_indicator_app",
                   size=format_file_size(total), percent=percent(total, ram))]
        if measure.helpers:
            lines.append(_("tooltip.memory_indicator_helpers",
                           names=", ".join(measure.helper_names),
                           size=format_file_size(measure.helpers),
                           percent=percent(measure.helpers, ram)))
        if state is not None:
            panel = panel_memory_estimate(state)
            lines.append(_("tooltip.memory_indicator_panel",
                           size=format_file_size(panel), percent=percent(panel, ram)))
        src = _gauge_icon_src(memory_level(total / ram), ratio)
    text = "<br>".join(_html.escape(line) for line in lines)
    image = ""
    if src:
        image = (f'<td valign="middle"><img src="{_html.escape(src, quote=True)}" '
                 f'width="{_GAUGE_TOOLTIP_SIZE}" height="{_GAUGE_TOOLTIP_SIZE}"></td>'
                 f'<td width="8"></td>')
    return (f'<table style="max-width:360px;white-space:normal;"><tr>'
            f'{image}<td valign="middle">{text}</td></tr></table>')


_DUPLICATE_INDICATOR_PIXMAP: QPixmap | None = None
_DUPLICATE_INDICATOR_PIXMAP_GRAY: QPixmap | None = None


def _get_duplicate_indicator_pixmap(grayed: bool) -> QPixmap:
    """Version réduite du badge orange de doublon (mosaic_canvas._get_duplicate_pixmap_wide_margin),
    grisée quand aucun doublon n'est présent."""
    global _DUPLICATE_INDICATOR_PIXMAP, _DUPLICATE_INDICATOR_PIXMAP_GRAY
    if grayed:
        if _DUPLICATE_INDICATOR_PIXMAP_GRAY is None:
            from modules.qt.mosaic_canvas import _get_duplicate_pixmap_wide_margin
            src = _get_duplicate_pixmap_wide_margin()
            img = src.toImage().convertToFormat(QImage.Format_ARGB32)
            for y in range(img.height()):
                for x in range(img.width()):
                    pixel = img.pixel(x, y)
                    gray = qGray(pixel)
                    img.setPixel(x, y, (qAlpha(pixel) << 24) | (gray << 16) | (gray << 8) | gray)
            _DUPLICATE_INDICATOR_PIXMAP_GRAY = QPixmap.fromImage(img)
        return _DUPLICATE_INDICATOR_PIXMAP_GRAY
    if _DUPLICATE_INDICATOR_PIXMAP is None:
        from modules.qt.mosaic_canvas import _get_duplicate_pixmap_wide_margin
        _DUPLICATE_INDICATOR_PIXMAP = _get_duplicate_pixmap_wide_margin()
    return _DUPLICATE_INDICATOR_PIXMAP


_BOOKMARK_INDICATOR_PIXMAP_GRAY: QPixmap | None = None


def _get_bookmark_indicator_pixmap(grayed: bool) -> QPixmap | None:
    """Ruban de marque-page de la mosaïque (mosaic_canvas._get_bookmark_pixmap),
    grisé en mode marque-pages off."""
    global _BOOKMARK_INDICATOR_PIXMAP_GRAY
    from modules.qt.mosaic_canvas import _get_bookmark_pixmap
    src = _get_bookmark_pixmap()
    if src is None or not grayed:
        return src
    if _BOOKMARK_INDICATOR_PIXMAP_GRAY is None:
        img = src.toImage().convertToFormat(QImage.Format_ARGB32)
        for y in range(img.height()):
            for x in range(img.width()):
                pixel = img.pixel(x, y)
                gray = qGray(pixel)
                img.setPixel(x, y, (qAlpha(pixel) << 24) | (gray << 16) | (gray << 8) | gray)
        _BOOKMARK_INDICATOR_PIXMAP_GRAY = QPixmap.fromImage(img)
    return _BOOKMARK_INDICATOR_PIXMAP_GRAY


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
        self._bookmarks_enabled_getter = None
        self._memory_state_getter = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(10)

        self._label = QLabel("")
        self._label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        # Sans ceci, un QLabel réclame comme largeur minimale celle de son
        # texte complet (setMinimumWidth(0) seul ne suffit pas : la sizePolicy
        # par défaut fait que Qt utilise quand même le sizeHint basé sur le
        # texte dans le calcul du minimum du layout), empêchant tout le
        # panneau (donc la colonne d'icônes à côté) de rétrécir en dessous de
        # cette largeur — alors que ce label a justement le stretch du layout
        # pour absorber l'espace disponible et peut légitimement être
        # compressé/tronqué quand la place manque.
        self._label.setMinimumWidth(0)
        self._label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self._label, 1)

        self._renumber_indicator = _ClickableLabel()
        self._renumber_indicator.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._renumber_indicator, 0)

        self._indicator_sep = QLabel(" ")
        self._indicator_sep.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._indicator_sep, 0)

        self._zip_indicator = _ClickableLabel()
        self._zip_indicator.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._zip_indicator, 0)

        # Les deux indicateurs sans texte (doublons, marque-pages) sont serrés
        # dans leur propre layout : l'espacement du layout principal, prévu
        # entre des textes, les écarterait inutilement. Le séparateur qui les
        # précède y est aussi, pour réduire l'écart avec l'indicateur ZIP.
        icons_layout = QHBoxLayout()
        icons_layout.setContentsMargins(0, 0, 0, 0)
        icons_layout.setSpacing(2)

        self._duplicate_sep = QLabel(" ")
        self._duplicate_sep.setAlignment(Qt.AlignCenter)
        icons_layout.addWidget(self._duplicate_sep, 0)

        self._duplicate_indicator = _ClickableLabel()
        self._duplicate_indicator.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        icons_layout.addWidget(self._duplicate_indicator, 0)

        self._bookmark_indicator = _ClickableLabel()
        self._bookmark_indicator.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        icons_layout.addWidget(self._bookmark_indicator, 0)

        # Mémoire de MosaicView, en dernière position : pourcentage de la
        # mémoire du PC, coloré selon le seuil. Sans effet au clic. Dans le
        # groupe des icônes, à 3 px de plus que l'écart entre elles.
        self._memory_indicator = QLabel("")
        self._memory_indicator.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._memory_indicator.setMouseTracking(True)
        # Marge du label, pas addSpacing() : un espace ajouté au layout
        # recevrait aussi l'écart de 2 px de chaque côté.
        self._memory_indicator.setContentsMargins(3, 0, 0, 0)
        icons_layout.addWidget(self._memory_indicator, 0)

        layout.addLayout(icons_layout, 0)

        # Tooltip overlay : QLabel enfant du panneau central (pas de la statusbar
        # elle-même, trop étroite en hauteur) pour ne pas être borné — même pattern
        # que TabBar(tooltip_parent=panel)
        self._overlay_tip = OverlayTooltip(tooltip_parent or self)
        self._overlay_tip.track(self._renumber_indicator, "")
        self._overlay_tip.track(self._zip_indicator, "")
        self._overlay_tip.track(self._duplicate_indicator, "")
        self._overlay_tip.track(self._bookmark_indicator, "")
        self._overlay_tip.track(self._memory_indicator, "")
        # Installé après track() pour passer avant le suivi du tooltip : le
        # contenu (estimation du panneau comprise) est calculé à l'entrée de
        # la souris, juste avant son affichage, et non à chaque mesure.
        self._memory_indicator.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self._memory_indicator and event.type() == QEvent.Enter:
            self._update_memory_tooltip()
        return super().eventFilter(obj, event)

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

    def set_bookmarks_enabled_getter(self, getter):
        """getter : callable() → bool, retourne le mode marque-pages du panneau."""
        self._bookmarks_enabled_getter = getter

    def set_memory_state_getter(self, getter):
        """getter : callable() → AppState du panneau, pour l'estimation de sa
        mémoire dans l'info-bulle de l'indicateur de mémoire."""
        self._memory_state_getter = getter

    def set_bookmark_click_callback(self, callback):
        """callback : appelé au clic gauche sur l'indicateur de marque-pages
        (bascule mode on/off)."""
        self._bookmark_indicator._on_click = callback

    def set_bookmark_right_click_callback(self, callback):
        """callback : appelé au clic droit sur l'indicateur de marque-pages
        (menu d'effacement des marque-pages)."""
        self._bookmark_indicator._on_right_click = callback

    def refresh(self, state):
        """Met à jour le texte et les indicateurs selon l'état courant."""
        font9 = get_current_font(9)
        self._label.setFont(font9)
        self._renumber_indicator.setFont(font9)
        self._indicator_sep.setFont(font9)
        self._zip_indicator.setFont(font9)
        self._duplicate_sep.setFont(font9)

        theme = get_current_theme()
        self._label.setStyleSheet(f"color: {theme.get('text', '#000000')};")
        self._renumber_indicator.setStyleSheet(f"color: {theme.get('text', '#000000')};")
        self._indicator_sep.setStyleSheet(f"color: {theme.get('separator', '#aaaaaa')};")
        self._zip_indicator.setStyleSheet(f"color: {theme.get('text', '#000000')};")
        self._duplicate_sep.setStyleSheet(f"color: {theme.get('separator', '#aaaaaa')};")

        # Le mode marque-pages est un réglage du panneau, pas du fichier :
        # indicateur actif même sans fichier ouvert, donc mis à jour avant le
        # court-circuit state is None. De même pour la mémoire, propre à
        # toute l'application.
        self._refresh_bookmark_indicator()
        self.refresh_memory()

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
        self._overlay_tip.force_refresh_visible(self._renumber_indicator)

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
        self._overlay_tip.force_refresh_visible(self._zip_indicator)

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
        self._overlay_tip.force_refresh_visible(self._duplicate_indicator)

    def refresh_memory(self):
        """Montre la dernière mesure de la mémoire (set_memory_measure) :
        pourcentage de la mémoire du PC occupé par MosaicView et ses
        programmes annexes, en vert, orange ou rouge (memory_usage.memory_level) ;
        tiret grisé si la mesure manque. Appelée par refresh() et après
        chaque mesure, sans le reste de la barre."""
        measure = _memory_measure
        theme = get_current_theme()
        self._memory_indicator.setFont(get_current_font(9))
        if measure is None or not measure.ram:
            self._memory_indicator.setText(_("labels.memory_indicator_unknown"))
            color = theme.get('disabled', '#999999')
        else:
            total = measure.app + measure.helpers
            self._memory_indicator.setText(
                _("labels.memory_indicator", percent=percent(total, measure.ram)))
            color = theme[f"memory_{memory_level(total / measure.ram)}"]
        self._memory_indicator.setStyleSheet(f"color: {color};")
        if self._overlay_tip.is_visible_for(self._memory_indicator):
            self._update_memory_tooltip()

    def _update_memory_tooltip(self):
        """Contenu de l'info-bulle de l'indicateur de mémoire, recalculé à
        l'entrée de la souris et, tant qu'elle est affichée, à chaque mesure
        et à chaque rafraîchissement de la barre (langue, thème, police)."""
        state = self._memory_state_getter() if self._memory_state_getter else None
        html = _memory_tooltip_html(_memory_measure, state, self.devicePixelRatioF())
        self._overlay_tip.set_tracked_html(html, self._memory_indicator)
        self._overlay_tip.force_refresh_visible(self._memory_indicator)

    def _refresh_bookmark_indicator(self):
        """Met à jour l'icône/tooltip de l'indicateur de marque-pages selon le
        mode du panneau (set_bookmarks_enabled_getter) : ruban coloré = on,
        grisé = off."""
        enabled = bool(self._bookmarks_enabled_getter and self._bookmarks_enabled_getter())
        pixmap = _get_bookmark_indicator_pixmap(grayed=not enabled)
        if pixmap is not None:
            target_size = max(14, get_current_font(9).pointSize() + 6)
            self._bookmark_indicator.setPixmap(
                pixmap.scaled(target_size, target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        tip_key = "tooltip.bookmark_indicator_on" if enabled else "tooltip.bookmark_indicator_off"
        self._overlay_tip.set_tracked_html(_format_tooltip(_(tip_key)), self._bookmark_indicator)
        self._overlay_tip.force_refresh_visible(self._bookmark_indicator)
