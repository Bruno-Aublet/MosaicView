"""
modules/qt/bit_depth_warning_qt.py — Avertissement de la visionneuse
principale : la page affichée, à 16 bits par couleur, passera à 8 bits si
l'outil actif la modifie.

Pillow, qui décode et réencode les pages, n'a aucun mode couleur à 16 bits
par canal (un PNG ou un TIFF « 48 bits » est lu directement en 8 bits), et
ses filtres (luminosité, netteté, niveaux…) ne travaillent pas en 16 bits,
même en niveaux de gris. Une page jamais modifiée garde ses octets d'origine,
donc ses 16 bits ; seules les modifications les réduisent. L'avertissement
ne bloque rien : l'utilisateur décide en connaissance de cause.

Un seul bandeau partagé par tous les outils (créé par _ViewerToolbar, comme
ses panneaux), placé en bas du canvas : sous la barre, il chevaucherait les
panneaux d'options et les boutons Valider/Annuler. Recalculé à chaque
sélection d'outil (_ViewerToolbar.set_active_tool) et à chaque affichage de
page (ImageViewer.display_image) — une modification commitée réaffiche la
page, et le bandeau disparaît quand elle est passée à 8 bits.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from modules.qt import state as _state_module
from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.clone_tool_qt import floating_options_panel_style
from modules.qt.entries import page_high_depth


# Outils qui réduisent à 8 bits une page en niveaux de gris 16 bits : ceux
# qui passent par les réglages (image_processing_qt.apply_adjustments) et la
# transparence. Les outils de dessin, le recadrage et la rotation la gardent
# en 16 bits (entries.drawn_page_image, opérations sans interpolation).
_GRAY16_REDUCING_TOOLS = {
    "brightness", "saturation", "sharpness", "remove_colors", "compression",
    "levels", "transparency", "color_depth", "effects", "image_mode",
}


class _BitDepthWarning(QLabel):
    """Bandeau d'avertissement « 16 bits → 8 bits » (voir docstring de module)."""

    def __init__(self, viewer: "ImageViewer", toolbar: "_ViewerToolbar"):
        super().__init__(viewer._canvas)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAlignment(Qt.AlignCenter)
        # Curseur standard au survol, même pendant un outil à curseur dessiné
        # sur le canvas (règle des panneaux flottants de la barre)
        self.setCursor(Qt.ArrowCursor)
        self._viewer = viewer
        # Reçue directement : la barre appelle refresh() pendant sa propre
        # construction, avant que viewer._toolbar existe
        self._toolbar = toolbar
        # Profondeur mémorisée pour les bytes de la page déjà examinée : le
        # bandeau est recalculé à chaque affichage, l'en-tête n'est relu
        # que si la page a changé
        self._checked_bytes = None
        self._depth = None
        self.hide()

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(
            floating_options_panel_style(theme, "_BitDepthWarning")
            + f" _BitDepthWarning {{ color: {theme['text']}; padding: 4px 10px; }}"
        )

    def retranslate(self):
        font = _get_current_font(11, bold=True)
        font.setItalic(True)
        self.setFont(font)
        self.setText(_("viewer.bit_depth_warning"))
        if self.isVisible():
            self.reposition()

    def _page_depth(self):
        state = self._viewer.callbacks.get('state') or _state_module.state
        idx = self._viewer.current_idx
        if not (0 <= idx < len(state.images_data)):
            return None
        data = state.images_data[idx].get("bytes")
        if data is not self._checked_bytes:
            self._checked_bytes = data
            self._depth = page_high_depth(data)
        return self._depth

    def refresh(self):
        tool_id = self._toolbar.active_tool
        depth = self._page_depth() if tool_id else None
        show = (depth == "color16") or (depth == "gray16" and tool_id in _GRAY16_REDUCING_TOOLS)
        if show:
            self.show()
            self.reposition()
            self.raise_()
        else:
            self.hide()

    def reposition(self):
        self.adjustSize()
        canvas = self._viewer._canvas
        x = (canvas.width() - self.width()) // 2
        y = canvas.height() - self.height() - 12
        self.move(max(0, x), max(0, y))

    def mousePressEvent(self, event):
        # Sans ce blindage, un clic sur le bandeau fuit vers _ViewerCanvas en
        # dessous (widget enfant du canvas) et déclenche le geste de l'outil
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()
