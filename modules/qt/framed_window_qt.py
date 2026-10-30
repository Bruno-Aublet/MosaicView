"""
framed_window_qt.py — Barre de titre dessinée par Qt pour les fenêtres autres
que la fenêtre principale (dialogues, fenêtres secondaires).

Windows dessine sa barre de titre avec la police du système, qui ne sait pas
afficher le pIqaD ni le tengwar, et sans le thème de l'application. Ici, la
fenêtre est sans cadre pour Qt et garde le cadre de Windows (window_frame_qt :
ombre, déplacement, ancrage) ; la barre est un widget Qt : icône, titre en
`_()` avec `get_current_font()`, boutons, menu de la fenêtre traduit.

Emploi (skill `window-title`) :

    class MaFenetre(FramedWindowMixin, QDialog):
        def __init__(self, parent):
            super().__init__(parent)
            self._setup_title_bar()            # tout de suite après super().__init__
            self.set_window_title("clé.du.titre")

- `set_window_title(clé, **params)` est l'unique façon de poser le titre :
  `_wt()` pour Windows (barre des tâches, Alt+Tab), `_()` pour la barre ; les
  deux sont retraduits au changement de langue par la barre elle-même.
- La barre occupe le haut de la fenêtre, dont le contenu est décalé d'autant
  par une marge de la fenêtre (comptée par son layout : taille minimale,
  `adjustSize`). `resize`, `setFixedSize`, `setFixedHeight`, `setMinimum*`
  et `setMaximum*` ajoutent la hauteur de la barre : la taille demandée reste
  celle du contenu, comme sous la barre de Windows.
- Boutons d'après le type de la fenêtre (window_frame_qt.caption_buttons) ;
  bords de redimensionnement absents sur un axe de taille fixe.
- Plein écran : barre masquée, marge retirée.
- Premier affichage : fenêtre transparente jusqu'à son premier dessin
  (_CaptionBar.hide_until_painted), sans flash clair en thème sombre.
"""

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor, QFontMetrics
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QWidget

from modules.qt import window_frame_qt
from modules.qt.config_manager import get_config_manager
from modules.qt.font_manager_qt import get_current_font
from modules.qt.language_signal import disconnect_on_destroy, language_signal
from modules.qt.localization import _, _wt
from modules.qt.overlay_tooltip_qt import OverlayTooltip
from modules.qt.title_bar_qt import (
    TITLE_BAR_HEIGHT, _CaptionButton, _GLYPH_CLOSE, _GLYPH_MAXIMIZE,
    _GLYPH_MINIMIZE, _GLYPH_RESTORE, _tip_html,
)
from modules.qt.utils import _themed_menu_stylesheet

# Taille maximale d'un widget (QWIDGETSIZE_MAX) : une hauteur maximale
# illimitée le reste, sans la hauteur de la barre ajoutée.
_SIZE_MAX = 16777215
_ICON_WIDTH = TITLE_BAR_HEIGHT + 4
# Couleurs de la barre de titre de Windows 10, que la barre remplace ; titre
# grisé quand la fenêtre n'est pas au premier plan
_BAR_COLORS = {
    "light": {"background": "#ffffff", "text": "#000000", "inactive": "#999999"},
    "dark": {"background": "#202020", "text": "#ffffff", "inactive": "#aaaaaa"},
}
# Marge ajoutée à la largeur mesurée du titre (rendu du QLabel)
_TEXT_PADDING = 8
# Premier affichage : délai au bout duquel la fenêtre redevient opaque même
# si aucun dessin n'a été signalé (hide_until_painted)
_REVEAL_FALLBACK_MS = 300


class _CaptionBar(QWidget):
    """Barre de titre d'une fenêtre : icône, titre, boutons. Toute la barre
    sauf ses boutons déplace la fenêtre (is_drag_area, HTCAPTION)."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._active = True
        self._title_key = None
        self._title_kwargs = {}
        self._title_suffix = ""
        self._title_fixed = None
        self._title_text = ""
        self._can_minimize = False
        self._can_maximize = False
        self._reveal_on_paint = False
        self.setObjectName("captionBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(TITLE_BAR_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._icon = QLabel(self)
        self._icon.setFixedSize(_ICON_WIDTH, TITLE_BAR_HEIGHT)
        self._icon.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._icon)

        # Un titre peut citer un nom de fichier ou de base : texte brut
        self._title = QLabel(self)
        self._title.setTextFormat(Qt.PlainText)
        self._title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self._title, 1)

        self._min_button = _CaptionButton(_GLYPH_MINIMIZE, parent=self)
        self._max_button = _CaptionButton(_GLYPH_MAXIMIZE, parent=self)
        self._close_button = _CaptionButton(_GLYPH_CLOSE, is_close=True, parent=self)
        self._min_button.clicked.connect(lambda: window_frame_qt.minimize(self._window))
        self._max_button.clicked.connect(lambda: window_frame_qt.toggle_maximized(self._window))
        self._close_button.clicked.connect(self._window.close)
        for button in (self._min_button, self._max_button, self._close_button):
            layout.addWidget(button)
        self._min_button.hide()
        self._max_button.hide()

        # Infobulles dans la fenêtre entière : la barre, haute de
        # TITLE_BAR_HEIGHT, couperait l'infobulle placée sous le curseur.
        self._overlay_tip = OverlayTooltip(window)
        for button in (self._min_button, self._max_button, self._close_button):
            self._overlay_tip.track(button)

        # Taille, état (plein écran, agrandie), activation et icône suivis
        # sur la fenêtre, sans redéfinir ses gestionnaires d'événements
        window.installEventFilter(self)

        # Thème et taille de police changés émettent aussi language_signal ;
        # une lambda, pas une méthode liée (déconnexion après destruction)
        self._lang_handler = lambda _lang_code: (self.apply_theme(), self.retranslate())
        language_signal.changed.connect(self._lang_handler)
        disconnect_on_destroy(self, self._lang_handler)
        self.apply_theme()
        self.retranslate()

    def set_title(self, key, kwargs, fixed_text=None, suffix="") -> None:
        self._title_key = key
        self._title_kwargs = dict(kwargs)
        self._title_fixed = fixed_text
        self._title_suffix = suffix
        self.retranslate()

    def set_buttons(self, minimize: bool, maximize: bool) -> None:
        self._can_minimize = minimize
        self._can_maximize = maximize
        self._min_button.setVisible(minimize)
        self._max_button.setVisible(maximize)
        self._elide()

    def retranslate(self) -> None:
        key, kwargs = self._title_key, self._title_kwargs
        if self._title_fixed is not None:
            self._window.setWindowTitle(self._title_fixed)
            self._title_text = self._title_fixed
        else:
            # Windows (barre des tâches, Alt+Tab) ne sait pas afficher pIqaD
            # et tengwar : _wt() ; la barre, dessinée par Qt : _()
            suffix = self._title_suffix
            self._window.setWindowTitle((_wt(key, **kwargs) if key else "") + suffix)
            self._title_text = (_(key, **kwargs) if key else "") + suffix
        self._elide()
        maximize_key = "title_bar.restore" if self._window.isMaximized() else "title_bar.maximize"
        for button, tip_key in ((self._min_button, "title_bar.minimize"),
                                (self._max_button, maximize_key),
                                (self._close_button, "title_bar.close")):
            self._overlay_tip.set_tracked_html(_tip_html(_(tip_key)), button)
            # Le pictogramme change sous le curseur (clic sur agrandir)
            self._overlay_tip.force_refresh_visible(button)

    def apply_theme(self) -> None:
        # Thème lu dans la configuration, comme la barre de la fenêtre
        # principale : la bascule l'enregistre avant d'émettre language_signal.
        theme_name = "dark" if get_config_manager().get_dark_mode() else "light"
        colors = _BAR_COLORS[theme_name]
        text = colors["text"] if self._active else colors["inactive"]
        font = get_current_font(10)
        self.setStyleSheet(f"#captionBar {{ background: {colors['background']}; }}")
        self._icon.setStyleSheet("background: transparent;")
        # Police dans la feuille de style : une règle QLabel de la fenêtre
        # (couleur, taille) l'emporterait sinon sur setFont.
        self._title.setStyleSheet(
            f'QLabel {{ color: {text}; background: transparent; padding: 0; margin: 0;'
            f' font-family: "{font.family()}"; font-size: {font.pointSize()}pt; }}')
        self._title.setFont(font)
        for button in (self._min_button, self._max_button, self._close_button):
            button.apply_theme(theme_name, text)
        self._overlay_tip.apply_theme()
        self._update_icon()
        self._elide()

    def hide_until_painted(self) -> None:
        """Fenêtre transparente jusqu'à son premier dessin par Qt, puis opaque
        au tour suivant de la boucle d'événements (image envoyée à l'écran).
        Sans cela, Windows montre un instant la fenêtre encore vide, claire :
        un flash à l'ouverture d'une fenêtre en thème sombre."""
        self._reveal_on_paint = True
        self._window.setWindowOpacity(0.0)
        # Filet : opaque de toute façon, même si aucun dessin n'est signalé
        QTimer.singleShot(_REVEAL_FALLBACK_MS, self._reveal)

    def _reveal(self) -> None:
        from shiboken6 import isValid
        # Fenêtre fermée et détruite entre-temps : sans conséquence
        if not isValid(self) or not isValid(self._window):
            return
        self._reveal_on_paint = False
        if self._window.windowOpacity() < 1.0:
            self._window.setWindowOpacity(1.0)

    def set_active(self, active: bool) -> None:
        """Titre grisé quand la fenêtre n'est pas au premier plan."""
        if active != self._active:
            self._active = active
            self.apply_theme()

    def update_window_state(self) -> None:
        window = self._window
        full_screen = window.isFullScreen()
        self.setVisible(not full_screen)
        window.setContentsMargins(0, 0 if full_screen else TITLE_BAR_HEIGHT, 0, 0)
        self._max_button.setText(_GLYPH_RESTORE if window.isMaximized() else _GLYPH_MAXIMIZE)
        self.retranslate()

    def is_drag_area(self, x: int, y: int) -> bool:
        """Vrai si le point (coordonnées de la barre) déplace la fenêtre :
        toute la barre sauf ses boutons."""
        if not self.isVisible() or not self.rect().contains(x, y):
            return False
        return not isinstance(self.childAt(x, y), QPushButton)

    def show_window_menu(self, global_pos) -> None:
        """Menu de la fenêtre (clic droit sur la barre, Alt+Espace), à la
        place du menu système de Windows : traduit, aux couleurs du thème,
        limité aux boutons de la barre."""
        window = self._window
        maximized = window.isMaximized()
        menu = QMenu(self)
        menu.setStyleSheet(_themed_menu_stylesheet(get_current_font(9)))
        restore = maximize = minimize = None
        if self._can_maximize:
            restore = menu.addAction(_("title_bar.restore"))
            restore.setEnabled(maximized)
        if self._can_minimize:
            minimize = menu.addAction(_("title_bar.minimize"))
        if self._can_maximize:
            maximize = menu.addAction(_("title_bar.maximize"))
            maximize.setEnabled(not maximized)
        if self._can_minimize or self._can_maximize:
            menu.addSeparator()
        close = menu.addAction(_("title_bar.close"))
        close.setShortcut("Alt+F4")
        close.setShortcutContext(Qt.WidgetShortcut)  # affichage seul
        chosen = menu.exec(global_pos)
        menu.deleteLater()
        if chosen is None:
            return
        if chosen is restore or chosen is maximize:
            window_frame_qt.toggle_maximized(window)
        elif chosen is minimize:
            window_frame_qt.minimize(window)
        elif chosen is close:
            window.close()

    def eventFilter(self, obj, event):
        if obj is self._window:
            kind = event.type()
            if kind == QEvent.Resize:
                self.setGeometry(0, 0, self._window.width(), TITLE_BAR_HEIGHT)
            elif kind == QEvent.WindowStateChange:
                self.update_window_state()
            elif kind == QEvent.ActivationChange:
                self.set_active(self._window.isActiveWindow())
            elif kind == QEvent.WindowIconChange:
                self._update_icon()
            elif kind == QEvent.Paint and self._reveal_on_paint:
                self._reveal_on_paint = False
                QTimer.singleShot(0, self._reveal)
        return False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _update_icon(self) -> None:
        # Icône de la fenêtre, à défaut celle de sa fenêtre parente, puis
        # celle de l'application (QApplication.setWindowIcon, MosaicView.main)
        self._icon.setPixmap(self._window.windowIcon().pixmap(16, 16))

    def _elide(self) -> None:
        """Titre raccourci (…) à la place laissée par l'icône et les boutons."""
        buttons = sum(b.width() for b in (self._min_button, self._max_button, self._close_button)
                      if not b.isHidden())
        available = self.width() - _ICON_WIDTH - buttons - _TEXT_PADDING
        metrics = QFontMetrics(get_current_font(10))
        self._title.setText(metrics.elidedText(self._title_text, Qt.ElideRight, max(0, available)))


def _size_args(args):
    """(largeur, hauteur) d'arguments QSize ou (largeur, hauteur)."""
    if len(args) == 1:
        return args[0].width(), args[0].height()
    return args


class FramedWindowMixin:
    """Barre de titre dessinée par Qt, pour une fenêtre de premier niveau
    (QDialog, QWidget avec Qt.Window) : `class X(FramedWindowMixin, QDialog)`,
    puis `self._setup_title_bar()` juste après `super().__init__`."""

    def _setup_title_bar(self) -> None:
        self._frame_installed = False
        self._native_frame = False
        # Sans cadre pour Qt avant la création de la fenêtre Windows : Qt
        # considère alors toute la fenêtre comme zone cliente (positions
        # justes) ; window_frame_qt.install rend le cadre à Windows.
        self.setWindowFlags(self.windowFlags())
        self._caption_bar = _CaptionBar(self)
        self._caption_bar.setGeometry(0, 0, self.width(), TITLE_BAR_HEIGHT)
        self.setContentsMargins(0, TITLE_BAR_HEIGHT, 0, 0)

    def set_window_title(self, key, suffix="", **kwargs) -> None:
        """Titre de la fenêtre, par sa clé de traduction : `_wt()` pour
        Windows, `_()` pour la barre, retraduits au changement de langue.
        Peut être rappelée à tout moment (titre qui dépend de l'état de la
        fenêtre). `suffix` : texte non traduit ajouté à la fin (compteur
        « [2/5] »…)."""
        self._caption_bar.set_title(key, kwargs, suffix=suffix)

    def set_window_title_text(self, text) -> None:
        """Titre non traduit, identique pour Windows et pour la barre : nom
        propre seulement (nom d'une licence tierce…), jamais un texte
        d'interface, qui passe par set_window_title et sa clé."""
        self._caption_bar.set_title(None, {}, text)

    # ── Drapeaux et premier affichage ─────────────────────────────────────

    def setWindowFlags(self, flags):
        # Une fenêtre qui pose ses drapeaux (setWindowFlags(Qt.Window)) reste
        # sans cadre pour Qt
        super().setWindowFlags(flags | Qt.FramelessWindowHint)

    def setVisible(self, visible):
        if visible and not getattr(self, "_frame_installed", True):
            self._install_frame()
            self._caption_bar.hide_until_painted()
        super().setVisible(visible)

    def _install_frame(self) -> None:
        """Boutons et styles du cadre, au premier affichage : la taille
        (fixe ou non) et le type de la fenêtre sont alors connus."""
        layout = self.layout()
        if layout is not None:
            layout.activate()  # contrainte de taille du layout (SetFixedSize)
        fixed_width = self.minimumWidth() == self.maximumWidth()
        fixed_height = self.minimumHeight() == self.maximumHeight()
        flags = self.windowFlags()
        minimize, maximize = window_frame_qt.caption_buttons(
            self.windowType() == Qt.Window,
            bool(flags & Qt.WindowMinimizeButtonHint),
            bool(flags & Qt.WindowMaximizeButtonHint),
            self.parentWidget() is not None,
            fixed_width, fixed_height)
        self._caption_bar.set_buttons(minimize, maximize)
        # Faux hors de Windows : la fenêtre reste alors sans cadre, et
        # nativeEvent ne traite aucun message
        self._native_frame = window_frame_qt.install(
            self, minimize, maximize, not (fixed_width and fixed_height))
        self._frame_installed = True

    # ── Messages de Windows ───────────────────────────────────────────────

    def nativeEvent(self, event_type, message):
        # Appelée pour chaque message de Windows : rien d'autre que le test
        if getattr(self, "_native_frame", False):
            handled = window_frame_qt.handle_native_message(
                self, message, self._caption_bar.is_drag_area, self._show_caption_menu)
            if handled is not None:
                return handled
        return super().nativeEvent(event_type, message)

    def _show_caption_menu(self, at_title_bar: bool = False) -> None:
        """Menu de la fenêtre, sous le curseur (clic droit sur la barre) ou
        sous l'icône (Alt+Espace). Ouvert au tour suivant de la boucle
        d'événements : nativeEvent traite encore le message de Windows."""
        bar = self._caption_bar
        if at_title_bar:
            pos = bar.mapToGlobal(QPoint(0, bar.height()))
        else:
            pos = QCursor.pos()
        QTimer.singleShot(0, lambda: bar.show_window_menu(pos))

    # ── Tailles : celle du contenu, barre en plus ─────────────────────────

    def resize(self, *args):
        width, height = _size_args(args)
        super().resize(width, height + TITLE_BAR_HEIGHT)

    def setFixedSize(self, *args):
        width, height = _size_args(args)
        super().setFixedSize(width, height + TITLE_BAR_HEIGHT)

    def setFixedHeight(self, height):
        super().setFixedHeight(height + TITLE_BAR_HEIGHT)

    def setMinimumSize(self, *args):
        width, height = _size_args(args)
        super().setMinimumSize(width, height + TITLE_BAR_HEIGHT)

    def setMinimumHeight(self, height):
        super().setMinimumHeight(height + TITLE_BAR_HEIGHT)

    def setMaximumSize(self, *args):
        width, height = _size_args(args)
        super().setMaximumSize(width, _with_bar(height))

    def setMaximumHeight(self, height):
        super().setMaximumHeight(_with_bar(height))


def _with_bar(height):
    return height if height >= _SIZE_MAX - TITLE_BAR_HEIGHT else height + TITLE_BAR_HEIGHT
