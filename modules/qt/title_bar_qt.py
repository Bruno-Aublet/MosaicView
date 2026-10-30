"""
title_bar_qt.py — Barre de titre de la fenêtre principale, dessinée par Qt.

Remplace la barre de Windows (retirée par window_frame_qt.py) : icône, titre,
boutons réduire / agrandir-restaurer / fermer. Le reste de la barre déplace
la fenêtre : `is_drag_area` le dit à `MainWindow.nativeEvent`, qui le
déclare barre de titre à Windows (déplacement, double-clic, ancrage natifs).
Texte posé par `window_title_qt.update_window_title` ; skill `window-title`.
"""

import html as _html

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QMouseEvent
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QWidget,
)

from modules.qt import window_frame_qt
from modules.qt.font_manager_qt import get_current_font
from modules.qt.language_signal import disconnect_on_destroy, language_signal
from modules.qt.localization import _
from modules.qt.config_manager import get_config_manager
from modules.qt.overlay_tooltip_qt import OverlayTooltip
from modules.qt.state import THEMES
from modules.qt.title_bar_layout import APP, COMIC, layout_titles, panel_kinds
from modules.qt.utils import _themed_menu_stylesheet

TITLE_BAR_HEIGHT = 32
_BUTTON_WIDTH = 46
# Marge ajoutée à la largeur mesurée d'un texte (rendu du QLabel)
_TEXT_PADDING = 4

# Pictogrammes des boutons de Windows (police Segoe MDL2 Assets, présente
# depuis Windows 10) : ce ne sont pas des textes, la police de la langue ne
# s'y applique pas.
_GLYPH_FONT = "Segoe MDL2 Assets"
_GLYPH_MINIMIZE = ""
_GLYPH_MAXIMIZE = ""
_GLYPH_RESTORE = ""
_GLYPH_CLOSE = ""

# Couleurs de la poignée de la séparation des panneaux, communes à la poignée
# (_EqualSplitterHandle.paintEvent, MosaicView.py) et à son prolongement dans
# la barre (_SeparationGrip) : fixes, elles ne suivent pas le thème.
SEPARATION_COLORS = {"normal": "#999999", "hover": "#3a7bd5",
                     "pressed": "#2a5bb5", "border": "#666666"}

_HOVER = {"light": "#d0d0d0", "dark": "#3a3a3a"}
_PRESSED = {"light": "#b8b8b8", "dark": "#505050"}
_CLOSE_HOVER = "#e81123"
_CLOSE_PRESSED = "#f1707a"


class _CaptionButton(QPushButton):
    def __init__(self, glyph: str, is_close: bool = False, parent=None):
        super().__init__(glyph, parent)
        self._is_close = is_close
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(_BUTTON_WIDTH, TITLE_BAR_HEIGHT)
        font = QFont(_GLYPH_FONT)
        font.setPixelSize(10)
        self.setFont(font)

    def apply_theme(self, theme_name: str, text: str) -> None:
        hover = _CLOSE_HOVER if self._is_close else _HOVER[theme_name]
        pressed = _CLOSE_PRESSED if self._is_close else _PRESSED[theme_name]
        hover_text = "#ffffff" if self._is_close else text
        self.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {text}; border: none; }}
            QPushButton:hover {{ background: {hover}; color: {hover_text}; }}
            QPushButton:pressed {{ background: {pressed}; color: {hover_text}; }}
        """)


class _SeparationGrip(QWidget):
    """Prolongement de la poignée de la séparation des panneaux jusqu'en haut
    de la fenêtre. Mêmes largeur et couleurs qu'elle (SEPARATION_COLORS),
    pour ne faire qu'un avec elle : chacune prend la couleur de survol quand
    l'une des deux est survolée. Ses clics, glissers et double-clics sont
    transmis à la poignée elle-même (QSplitterHandle), qui déplace la
    séparation ou la remet au milieu exactement comme si on l'avait saisie."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("titleSeparator")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.SplitHCursor)
        c = SEPARATION_COLORS
        self.setStyleSheet(
            f"#titleSeparator {{ background: {c['normal']};"
            f" border-left: 1px solid {c['border']}; border-right: 1px solid {c['border']}; }}"
            f' #titleSeparator:hover, #titleSeparator[linkedHover="true"] {{ background: {c["hover"]}; }}'
            f' #titleSeparator[pressed="true"] {{ background: {c["pressed"]}; }}')
        self._handle = None

    def enterEvent(self, event):
        if self._handle is not None:
            self._handle.set_linked_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._handle is not None:
            self._handle.set_linked_hover(False)
        super().leaveEvent(event)

    def set_handle(self, handle) -> None:
        self._handle = handle

    def set_state(self, name: str, value: bool) -> None:
        """Survol ou appui de la poignée, repris par le prolongement."""
        if self.property(name) == value:
            return
        self.setProperty(name, value)
        self.style().unpolish(self)
        self.style().polish(self)

    def _forward(self, event) -> None:
        # QSplitterHandle calcule le déplacement d'après la position globale
        # et le point saisi dans la poignée : position locale convertie
        if self._handle is not None:
            global_pos = event.globalPosition()
            local = QPointF(self._handle.mapFromGlobal(global_pos.toPoint()))
            QApplication.sendEvent(self._handle, QMouseEvent(
                event.type(), local, global_pos,
                event.button(), event.buttons(), event.modifiers()))
        event.accept()

    def mousePressEvent(self, event):
        self._forward(event)

    def mouseMoveEvent(self, event):
        self._forward(event)

    def mouseReleaseEvent(self, event):
        self._forward(event)

    def mouseDoubleClickEvent(self, event):
        self._forward(event)


class TitleBar(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._active = True
        self.setObjectName("titleBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(TITLE_BAR_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._icon = QLabel(self)
        self._icon.setFixedSize(TITLE_BAR_HEIGHT + 4, TITLE_BAR_HEIGHT)
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setPixmap(window.windowIcon().pixmap(16, 16))
        layout.addWidget(self._icon)

        # Zone des titres, entre l'icône et les boutons : les titres y sont
        # placés à la main (title_bar_layout), au-dessus de leur panneau.
        self._text_area = QWidget(self)
        self._text_area.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self._text_area, 1)
        # Nom de fichier venu de l'utilisateur ou d'une archive : texte brut
        self._labels = []
        for _index in range(2):
            label = QLabel(self)
            label.setTextFormat(Qt.PlainText)
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            label.hide()
            self._labels.append(label)
        self._separator = _SeparationGrip(self)
        self._separator.hide()
        self._app_text = ""
        self._names = [None]
        self._handle = None
        # Replacement différé au tour suivant de la boucle d'événements : le
        # QSplitter redimensionne les panneaux avant de déplacer sa poignée
        # (lue trop tôt, elle est à son ancienne place), et un déplacement
        # fait pendant que Qt redessine n'apparaît pas à l'écran.
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(0)
        self._relayout_timer.timeout.connect(self._relayout)

        self._min_button = _CaptionButton(_GLYPH_MINIMIZE, parent=self)
        self._max_button = _CaptionButton(_GLYPH_MAXIMIZE, parent=self)
        self._close_button = _CaptionButton(_GLYPH_CLOSE, is_close=True, parent=self)
        self._min_button.clicked.connect(lambda: window_frame_qt.minimize(self._window))
        self._max_button.clicked.connect(lambda: window_frame_qt.toggle_maximized(self._window))
        self._close_button.clicked.connect(self._window.close)
        for button in (self._min_button, self._max_button, self._close_button):
            layout.addWidget(button)

        # Infobulles affichées dans la fenêtre entière : la barre, haute de
        # TITLE_BAR_HEIGHT, couperait l'infobulle placée sous le curseur.
        self._overlay_tip = OverlayTooltip(window)
        for button in (self._min_button, self._max_button, self._close_button):
            self._overlay_tip.track(button)

        # Thème et taille de police changés émettent aussi language_signal
        self._lang_handler = lambda _lang_code: (self.apply_theme(), self._retranslate())
        language_signal.changed.connect(self._lang_handler)
        disconnect_on_destroy(self, self._lang_handler)
        self.apply_theme()
        self._retranslate()

    def set_titles(self, app_text: str, names: list) -> None:
        """Texte de l'appli et nom du comic de chaque panneau affiché, de
        gauche à droite (None : panneau vide) ; window_title_qt."""
        self._app_text = app_text
        self._names = list(names)
        self._relayout()

    def watch(self, widget) -> None:
        """Replace les titres quand `widget` (cadre d'un panneau) change de
        taille ou de position : séparation déplacée, double panneau ouvert
        ou fermé, fenêtre redimensionnée."""
        widget.installEventFilter(self)

    def watch_separation(self, handle) -> None:
        """Poignée de la séparation des panneaux : le séparateur la suit
        sans délai (_follow_handle), les titres au tour suivant (_relayout)."""
        self._handle = handle
        self._separator.set_handle(handle)
        handle.installEventFilter(self)

    def eventFilter(self, obj, event):
        kind = event.type()
        if obj is self._handle:
            # Survol et appui de la poignée repris par son prolongement
            # (les clics transmis par celui-ci passent aussi par ici)
            if kind == QEvent.Enter:
                self._separator.set_state("linkedHover", True)
            elif kind == QEvent.Leave:
                self._separator.set_state("linkedHover", False)
            elif kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._separator.set_state("pressed", True)
            elif kind == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._separator.set_state("pressed", False)
            elif kind in (QEvent.Resize, QEvent.Move):
                self._follow_handle()
        if kind in (QEvent.Resize, QEvent.Move, QEvent.Show, QEvent.Hide):
            self._relayout_timer.start()
        return False

    def _follow_handle(self) -> None:
        """Place le séparateur sur la poignée, dans le même passage que le
        QSplitter : différé, il la suivrait avec une image de retard."""
        if self._handle is None or not self._separator.isVisible():
            return
        area = self._text_area.geometry()
        width = self._handle.width()
        x = self._bar_x(self._handle.mapTo(self._window, QPoint(0, 0)).x())
        x = max(area.left(), min(x, area.right() + 1 - width))
        self._separator.setGeometry(x, 0, width, self.height())

    def is_drag_area(self, x: int, y: int) -> bool:
        """Vrai si le point (coordonnées de la barre) déplace la fenêtre :
        toute la barre sauf ses boutons et le prolongement de la séparation."""
        if not self.isVisible() or not self.rect().contains(x, y):
            return False
        child = self.childAt(x, y)
        return not isinstance(child, (QPushButton, _SeparationGrip))

    def update_window_state(self) -> None:
        """Pictogramme agrandir/restaurer selon l'état de la fenêtre."""
        maximized = self._window.isMaximized()
        self._max_button.setText(_GLYPH_RESTORE if maximized else _GLYPH_MAXIMIZE)
        self._retranslate()

    def set_active(self, active: bool) -> None:
        """Titre grisé quand la fenêtre n'est pas au premier plan, comme
        dans la barre de Windows."""
        if active != self._active:
            self._active = active
            self.apply_theme()

    def show_window_menu(self, global_pos) -> None:
        """Menu de la fenêtre (clic droit sur la barre, Alt+Espace), à la
        place du menu système de Windows : traduit et aux couleurs du thème."""
        window = self._window
        maximized = window.isMaximized()
        menu = QMenu(self)
        menu.setStyleSheet(_themed_menu_stylesheet(get_current_font(9)))
        restore = menu.addAction(_("title_bar.restore"))
        restore.setEnabled(maximized)
        minimize = menu.addAction(_("title_bar.minimize"))
        maximize = menu.addAction(_("title_bar.maximize"))
        maximize.setEnabled(not maximized)
        menu.addSeparator()
        close = menu.addAction(_("title_bar.close"))
        close.setShortcut("Alt+F4")
        close.setShortcutContext(Qt.WidgetShortcut)  # affichage seul
        chosen = menu.exec(global_pos)
        menu.deleteLater()
        if chosen is restore or chosen is maximize:
            window_frame_qt.toggle_maximized(window)
        elif chosen is minimize:
            window_frame_qt.minimize(window)
        elif chosen is close:
            window.close()

    def _retranslate(self) -> None:
        maximize_key = "title_bar.restore" if self._window.isMaximized() else "title_bar.maximize"
        for button, key in ((self._min_button, "title_bar.minimize"),
                            (self._max_button, maximize_key),
                            (self._close_button, "title_bar.close")):
            self._overlay_tip.set_tracked_html(_tip_html(_(key)), button)
            # Le pictogramme change sous le curseur (clic sur agrandir)
            self._overlay_tip.force_refresh_visible(button)

    def apply_theme(self) -> None:
        # Thème lu dans la configuration : la barre existe avant le premier
        # panneau, donc avant tout état de panneau ; la bascule de thème
        # l'enregistre avant d'émettre language_signal.
        theme_name = "dark" if get_config_manager().get_dark_mode() else "light"
        theme = THEMES[theme_name]
        text = theme["text"] if self._active else theme["disabled"]
        self.setStyleSheet(f"#titleBar {{ background: {theme['toolbar_bg']}; }}")
        for label in self._labels:
            label.setStyleSheet(f"color: {text}; background: transparent;")
            label.setFont(get_current_font(10))
        for button in (self._min_button, self._max_button, self._close_button):
            button.apply_theme(theme_name, text)
        self._overlay_tip.apply_theme()
        self._relayout()

    def _relayout(self) -> None:
        """Place chaque titre au-dessus de son panneau (title_bar_layout)."""
        geometry = self._window._title_panels_geometry()
        if geometry is None:
            return
        spans, handle = geometry
        spans = spans[:len(self._names)]
        texts = self._texts()
        metrics = QFontMetrics(self._labels[0].font())
        panels = []
        for (x0, x1), (kind, text) in zip(spans, texts):
            width = metrics.horizontalAdvance(text) + _TEXT_PADDING if kind else 0
            panels.append((self._bar_x(x0), self._bar_x(x1), kind, width))
        separator = None
        if handle is not None and len(panels) == 2:
            center, width = handle
            # Coordonnées de la fenêtre et de la barre ne diffèrent que d'un
            # décalage entier : le centre garde sa demi-unité
            separator = (center + self._bar_x(0), width)
        area = self._text_area.geometry()
        placements, separator_x = layout_titles(area.left(), area.right() + 1, panels, separator)

        for index, label in enumerate(self._labels):
            placement = placements[index] if index < len(placements) else None
            if placement is None or placement[1] <= 0:
                label.hide()
                continue
            x, width = placement
            text = texts[index][1]
            label.setText(metrics.elidedText(text, Qt.ElideRight, width))
            label.setGeometry(x, 0, width, self.height())
            label.show()
        # Barre redessinée en entier : l'ancienne place du séparateur et des
        # titres doit disparaître de l'écran
        self.update()
        if separator_x is None:
            self._separator.hide()
        else:
            self._separator.setGeometry(separator_x, 0, separator[1], self.height())
            self._separator.show()

    def _texts(self) -> list:
        """(contenu, texte) de chaque panneau affiché."""
        kinds = panel_kinds(self._names, len(self._names) > 1)
        return [(kind, name if kind == COMIC else self._app_text if kind == APP else "")
                for kind, name in zip(kinds, self._names)]

    def _bar_x(self, window_x: int) -> int:
        return self.mapFrom(self._window, QPoint(window_x, 0)).x()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_timer.start()


def _tip_html(text: str) -> str:
    return (f'<table style="max-width:360px;white-space:normal;">'
            f'<tr><td>{_html.escape(text)}</td></tr></table>')
