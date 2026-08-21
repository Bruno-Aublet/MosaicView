"""
modules/qt/text_tool_qt.py — Outil d'insertion de texte riche de la barre
d'outils flottante de la visionneuse principale (image_viewer_qt.py).

Fusion progressive des visionneuses : ce module
contient toute la logique propre à l'outil "text" — état/interactions du
canvas (mixin TextCanvasMixin, hérité par _ViewerCanvas), commit des blocs
dans l'historique du panneau (mixin TextViewerMixin, hérité par ImageViewer),
le panneau flottant de formatage rich text (_TextOptionsPanel), et les
overlays de texte eux-mêmes (_RichTextOverlay, _TextBlock). image_viewer_qt.py
ne fait qu'hériter de ces deux mixins et brancher l'icône de la barre
d'outils — voir CLAUDE.md règle "ne jamais migrer le code d'un outil dans
image_viewer_qt.py".

Contrairement au crop/straighten (une seule géométrie par page) ou au clone
(pas de persistance), l'outil texte gère N blocs simultanés par page — chacun
un _RichTextOverlay (QTextEdit transparent) hébergé comme enfant du canvas.

Décisions retenues :
  * Désélection de l'outil (retour à "aucun outil") : les blocs existants ne
    sont PAS effacés (même principe que le rectangle de crop) — ils sont
    figés (plus de focus clavier possible, plus de déplacement à la souris)
    et grisés visuellement (bordure #888888 au lieu de bleu actif), jusqu'à
    resélection de l'outil.
  * Undo/redo unifié : un seul point d'historique global créé à la
    validation ("Appliquer le texte"), comme crop/straighten/clone — PAS à
    chaque frappe. L'undo de frappe Qt natif (document().undo()/redo()) reste
    local à chaque _RichTextOverlay tant qu'il a le focus (comportement Qt
    standard, pas un système ajouté) : Ctrl+Z/Y du panneau n'agit que quand
    aucun bloc n'a le focus, et ne concerne que des validations déjà
    commises. Pas d'historique interne par page à la ImageViewer : la
    persistance du "travail non validé" se fait entièrement via
    _text_blocks_by_page (contenu HTML + position), pas via un second niveau
    d'undo.
  * Barre d'options rich text (police/taille/gras/italique/souligné/couleur) :
    panneau flottant sous la barre d'outils (_TextOptionsPanel, même
    mécanisme que _StraightenAnglePanel/_CloneOptionsPanel), visible
    UNIQUEMENT quand un bloc est actif (pas juste quand l'outil "text" est
    sélectionné) — un outil sélectionné sans bloc actif n'a rien à formater.
"""

from PIL import Image

from PySide6.QtWidgets import (
    QWidget, QLabel, QHBoxLayout, QVBoxLayout, QFrame, QPushButton,
    QSpinBox, QFontComboBox, QTextEdit, QDialog, QSlider, QLineEdit,
)
from PySide6.QtCore import Qt, QPoint, QRect, QRectF, Signal, QTimer
from PySide6.QtGui import (
    QImage, QPainter, QColor, QFont, QTextCharFormat, QTextOption, QCursor,
)

from modules.qt import state as _state_module
from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font


# ─────────────────────────────────────────────────────────────────────────────
# Curseur I-beam
# ─────────────────────────────────────────────────────────────────────────────

def _make_text_cursor() -> QCursor:
    from PySide6.QtGui import QPixmap, QPen
    size = 32
    cx = cy = size // 2
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen_white = QPen(QColor(255, 255, 255, 220), 2.0)
    pen_black = QPen(QColor(0, 0, 0, 240), 1.2)
    for pen in (pen_white, pen_black):
        painter.setPen(pen)
        painter.drawLine(cx, cy - 10, cx, cy + 10)
        painter.drawLine(cx - 4, cy - 10, cx + 4, cy - 10)
        painter.drawLine(cx - 4, cy + 10, cx + 4, cy + 10)
    painter.end()
    return QCursor(pm, cx, cy)

_TEXT_CURSOR = None

def _get_text_cursor() -> QCursor:
    global _TEXT_CURSOR
    if _TEXT_CURSOR is None:
        _TEXT_CURSOR = _make_text_cursor()
    return _TEXT_CURSOR


# ─────────────────────────────────────────────────────────────────────────────
# Sélecteur de couleur maison (fenêtre custom, pas QColorDialog)
# ─────────────────────────────────────────────────────────────────────────────
# QColorDialog a été abandonné : ses widgets internes (grille de couleurs
# personnalisées, aperçu, sélecteurs RVB) sont soit peints par des fonctions
# natives OS soit sensibles au moteur QStyleSheetStyle déclenché par le
# stylesheet global de l'appli, ce qui les rend illisibles en mode sombre
# quel que soit le nombre de couches de palette/stylesheet locales tentées —
# et surtout, bricoler un thème clair fixe en dur dessus viole la règle
# centrale du projet (get_current_theme()/pattern _apply_theme(), CLAUDE.md) :
# tout widget doit suivre le thème réel de l'appli, pas un thème hors-piste
# codé à la main. Remplacé par une fenêtre 100% maison, qui suit le thème
# comme n'importe quelle autre fenêtre du projet.

_BASIC_COLORS = [
    "#000000", "#7f0000", "#007f00", "#7f7f00", "#00007f", "#7f007f", "#007f7f", "#7f7f7f",
    "#bfbfbf", "#ff0000", "#00ff00", "#ffff00", "#0000ff", "#ff00ff", "#00ffff", "#ffffff",
]


class _ColorSwatch(QLabel):
    """Case de couleur cliquable de la grille de couleurs prédéfinies."""

    def __init__(self, color: QColor, on_click):
        super().__init__()
        self._color = QColor(color)
        self._on_click = on_click
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(
            f"background: {self._color.name()}; border: 1px solid #808080;"
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click(QColor(self._color))
        event.accept()


class _HueSatSquare(QWidget):
    """Nuancier 2D cliquable/glissable : teinte en abscisse (0-359°),
    saturation en ordonnée (0=haut, 255=bas — même convention visuelle que
    QColorDialog : blanc en haut, couleur saturée en bas). La valeur (V du
    HSV) est pilotée séparément par _ValueSlider, appliquée ici uniquement
    pour l'aperçu du curseur, pas pour le dégradé peint (qui reste toujours
    à pleine luminosité — comportement standard d'un sélecteur teinte/saturation)."""

    changed = Signal(int, int)  # hue (0-359), sat (0-255)

    def __init__(self):
        super().__init__()
        self.setFixedSize(200, 160)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._hue = 0
        self._sat = 0
        self._gradient_cache = None

    def set_hue_sat(self, hue: int, sat: int):
        self._hue = max(0, min(359, hue))
        self._sat = max(0, min(255, sat))
        self.update()

    def _rebuild_gradient(self):
        img = QImage(self.width(), self.height(), QImage.Format.Format_RGB32)
        for y in range(self.height()):
            sat = 255 - int(255 * y / max(1, self.height() - 1))
            for x in range(self.width()):
                hue = int(359 * x / max(1, self.width() - 1))
                c = QColor.fromHsv(hue, sat, 255)
                img.setPixelColor(x, y, c)
        self._gradient_cache = img

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._gradient_cache = None

    def paintEvent(self, event):
        if self._gradient_cache is None or self._gradient_cache.size() != self.size():
            self._rebuild_gradient()
        painter = QPainter(self)
        painter.drawImage(0, 0, self._gradient_cache)
        # Curseur : petit cercle blanc cerclé de noir à la position teinte/saturation.
        x = int(self._hue / 359 * (self.width() - 1)) if self.width() > 1 else 0
        y = int((255 - self._sat) / 255 * (self.height() - 1)) if self.height() > 1 else 0
        from PySide6.QtGui import QPen
        painter.setPen(QPen(QColor("#000000"), 1.5))
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QPoint(x, y), 5, 5)
        painter.end()

    def _pick_at(self, pos):
        x = max(0, min(self.width() - 1, pos.x()))
        y = max(0, min(self.height() - 1, pos.y()))
        hue = int(359 * x / max(1, self.width() - 1))
        sat = 255 - int(255 * y / max(1, self.height() - 1))
        self._hue, self._sat = hue, sat
        self.update()
        self.changed.emit(hue, sat)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pick_at(event.position().toPoint())
        event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pick_at(event.position().toPoint())
        event.accept()


class _ValueSlider(QWidget):
    """Barre verticale de luminosité (V du HSV, 0-255) — dégradé du noir (bas)
    à la teinte pleine (haut) pour la teinte/saturation courantes."""

    changed = Signal(int)

    def __init__(self):
        super().__init__()
        self.setFixedSize(24, 160)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue = 0
        self._sat = 0
        self._val = 255

    def set_hsv(self, hue: int, sat: int, val: int):
        self._hue = hue
        self._sat = sat
        self._val = max(0, min(255, val))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        for y in range(self.height()):
            val = int(255 * (1 - y / max(1, self.height() - 1)))
            c = QColor.fromHsv(self._hue, self._sat, val)
            painter.setPen(c)
            painter.drawLine(0, y, self.width(), y)
        from PySide6.QtGui import QPen
        y_cursor = int((255 - self._val) / 255 * (self.height() - 1)) if self.height() > 1 else 0
        painter.setPen(QPen(QColor("#000000"), 1.5))
        painter.drawLine(0, y_cursor, self.width(), y_cursor)
        painter.end()

    def _pick_at(self, pos):
        y = max(0, min(self.height() - 1, pos.y()))
        val = 255 - int(255 * y / max(1, self.height() - 1))
        self._val = val
        self.update()
        self.changed.emit(val)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pick_at(event.position().toPoint())
        event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pick_at(event.position().toPoint())
        event.accept()


class _ColorPickerDialog(QDialog):
    """Fenêtre de choix de couleur (texte riche) — maison, suit le thème de
    l'appli. Non-modale (CLAUDE.md règle n°4) : le résultat est retourné via
    le signal color_picked, jamais par valeur de retour synchrone.

    Grille de couleurs prédéfinies + curseurs RVB + champ hexadécimal +
    canal alpha, aperçu de la couleur courante. Portée volontairement plus
    restreinte que QColorDialog (pas de pipette écran, pas de couleurs
    personnalisées mémorisées) — suffisant pour le cas d'usage (choisir une
    couleur de texte), pas une reproduction complète du composant Qt."""

    color_picked = Signal(QColor)

    def __init__(self, parent, initial_color: QColor):
        super().__init__(parent)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setWindowFlags(self.windowFlags() | Qt.Window)
        self._color = QColor(initial_color)
        self._ignore_signals = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # ── Nuancier teinte/saturation + luminosité + aperçu + raccourcis ────
        picker_row = QHBoxLayout()
        picker_row.setSpacing(10)

        self._hue_sat = _HueSatSquare()
        self._hue_sat.changed.connect(self._on_hue_sat_changed)
        picker_row.addWidget(self._hue_sat)

        self._value_slider = _ValueSlider()
        self._value_slider.changed.connect(self._on_value_changed)
        picker_row.addWidget(self._value_slider)

        right_col = QVBoxLayout()
        right_col.setSpacing(6)
        self._preview = QLabel()
        self._preview.setFixedSize(48, 48)
        # Sans cet attribut, un QLabel dont le stylesheet ne pose que
        # "background" (pas de propriété de layout) peut voir sa taille
        # fixe ignorée en présence du stylesheet de la fenêtre parente.
        self._preview.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        right_col.addWidget(self._preview)

        # Grille réduite de couleurs de base (raccourcis rapides), 4 par
        # ligne pour tenir dans la largeur de right_col.
        self._lbl_basic = QLabel()
        right_col.addWidget(self._lbl_basic)
        self._swatches = []
        for row_idx in range(4):
            grid_row = QHBoxLayout()
            grid_row.setSpacing(3)
            for col_idx in range(4):
                i = row_idx * 4 + col_idx
                sw = _ColorSwatch(QColor(_BASIC_COLORS[i]), self._on_swatch_clicked)
                self._swatches.append(sw)
                grid_row.addWidget(sw)
            right_col.addLayout(grid_row)

        right_col.addStretch()
        picker_row.addLayout(right_col)
        picker_row.addStretch()
        root.addLayout(picker_row)

        # ── Champ hexadécimal ──────────────────────────────────────────────────
        hex_row = QHBoxLayout()
        hex_row.setSpacing(8)
        self._lbl_hex = QLabel()
        hex_row.addWidget(self._lbl_hex)
        self._hex_edit = QLineEdit()
        # setFixedHeight obligatoire : sans hauteur explicite, un QLineEdit
        # dans un layout peut s'étirer bien au-delà d'une ligne de texte si un
        # addStretch() voisin absorbe l'espace dans le mauvais widget — texte
        # écrasé/illisible sinon.
        self._hex_edit.setFixedHeight(26)
        self._hex_edit.setMaximumWidth(120)
        self._hex_edit.editingFinished.connect(self._on_hex_edited)
        hex_row.addWidget(self._hex_edit)
        hex_row.addStretch()
        root.addLayout(hex_row)

        # ── Curseurs RVB + alpha ──────────────────────────────────────────────
        self._sliders = {}
        self._spins = {}
        for key, label_attr in (("red", "_lbl_red"), ("green", "_lbl_green"),
                                 ("blue", "_lbl_blue"), ("alpha", "_lbl_alpha")):
            row = QHBoxLayout()
            row.setSpacing(6)
            lbl = QLabel()
            setattr(self, label_attr, lbl)
            lbl.setFixedWidth(70)
            row.addWidget(lbl)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 255)
            slider.valueChanged.connect(lambda v, k=key: self._on_channel_changed(k, v))
            row.addWidget(slider, stretch=1)
            spin = QSpinBox()
            spin.setRange(0, 255)
            spin.setFixedWidth(56)
            spin.valueChanged.connect(lambda v, k=key: self._on_channel_changed(k, v))
            row.addWidget(spin)
            self._sliders[key] = slider
            self._spins[key] = spin
            root.addLayout(row)

        # ── Boutons OK/Annuler ────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_ok = QPushButton()
        self._btn_ok.setDefault(True)
        self._btn_ok.clicked.connect(self._on_ok)
        self._btn_cancel = QPushButton()
        self._btn_cancel.clicked.connect(self.close)
        btn_row.addWidget(self._btn_ok)
        btn_row.addWidget(self._btn_cancel)
        root.addLayout(btn_row)

        self._sync_controls_from_color()
        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_finished)

    def _on_finished(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    # ── Synchronisation contrôles ↔ couleur ──────────────────────────────────

    def _sync_controls_from_color(self):
        self._ignore_signals = True
        try:
            self._hex_edit.setText(self._color.name())
            for key, val in (("red", self._color.red()), ("green", self._color.green()),
                              ("blue", self._color.blue()), ("alpha", self._color.alpha())):
                self._sliders[key].setValue(val)
                self._spins[key].setValue(val)
            hue = max(0, self._color.hue())  # hue() vaut -1 pour le gris/noir/blanc pur
            sat = self._color.saturation()
            val = self._color.value()
            self._hue_sat.set_hue_sat(hue, sat)
            self._value_slider.set_hsv(hue, sat, val)
            self._update_preview()
        finally:
            self._ignore_signals = False

    def _update_preview(self):
        self._preview.setStyleSheet(
            f"background: {self._color.name()}; border: 1px solid #808080;"
        )

    def _on_swatch_clicked(self, color: QColor):
        color.setAlpha(self._color.alpha())
        self._color = color
        self._sync_controls_from_color()

    def _on_hex_edited(self):
        text = self._hex_edit.text().strip()
        color = QColor(text)
        if color.isValid():
            color.setAlpha(self._color.alpha())
            self._color = color
            self._sync_controls_from_color()
        else:
            self._hex_edit.setText(self._color.name())

    def _on_channel_changed(self, key: str, value: int):
        if self._ignore_signals:
            return
        self._ignore_signals = True
        try:
            self._sliders[key].setValue(value)
            self._spins[key].setValue(value)
        finally:
            self._ignore_signals = False
        r = self._sliders["red"].value()
        g = self._sliders["green"].value()
        b = self._sliders["blue"].value()
        a = self._sliders["alpha"].value()
        self._color = QColor(r, g, b, a)
        self._hex_edit.setText(self._color.name())
        hue = max(0, self._color.hue())
        sat = self._color.saturation()
        val = self._color.value()
        self._hue_sat.set_hue_sat(hue, sat)
        self._value_slider.set_hsv(hue, sat, val)
        self._update_preview()

    def _on_hue_sat_changed(self, hue: int, sat: int):
        # CAUSE CONFIRMÉE PAR PRINTS DEBUG : la couleur initiale (#000000,
        # texte noir par défaut) a value()=0 en HSV — _value_slider._val
        # héritait donc de 0 via _sync_controls_from_color(), et cliquer sur
        # le nuancier gardait ce val=0 tel quel (QColor.fromHsv(h, s, 0)
        # = toujours noir, quels que soient h/s). Un clic sur le nuancier
        # doit toujours produire une couleur visible : forcer val=255 s'il
        # est actuellement à 0 (comportement standard d'un sélecteur de
        # couleur — cliquer sur une teinte ne doit jamais rester bloqué noir).
        val = self._value_slider._val
        if val <= 0:
            val = 255
        alpha = self._color.alpha()
        self._color = QColor.fromHsv(hue, sat, val, alpha)
        self._value_slider.set_hsv(hue, sat, val)
        self._sync_rgb_controls_only()

    def _on_value_changed(self, val: int):
        if self._ignore_signals:
            return
        hue = self._hue_sat._hue
        sat = self._hue_sat._sat
        alpha = self._color.alpha()
        self._color = QColor.fromHsv(hue, sat, val, alpha)
        self._sync_rgb_controls_only()

    def _sync_rgb_controls_only(self):
        """Même chose que _sync_controls_from_color, mais SANS resynchroniser
        le nuancier HSV lui-même (qui vient de générer ce changement — se
        resynchroniser depuis QColor.hue()/saturation() ferait perdre la
        teinte choisie par l'utilisateur quand sat=0 ou val=0, cas où hue()
        n'est pas définissable de façon stable par Qt)."""
        self._ignore_signals = True
        try:
            self._hex_edit.setText(self._color.name())
            for key, v in (("red", self._color.red()), ("green", self._color.green()),
                            ("blue", self._color.blue()), ("alpha", self._color.alpha())):
                self._sliders[key].setValue(v)
                self._spins[key].setValue(v)
            self._update_preview()
        finally:
            self._ignore_signals = False

    def _on_ok(self):
        self.color_picked.emit(QColor(self._color))
        self.close()

    # ── Thème / traduction ────────────────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(10)

        self.setWindowTitle(_wt("dialogs.text_viewer.pick_color_title"))
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")

        # background: transparent explicite obligatoire sur chaque QLabel :
        # dès qu'un stylesheet est actif quelque part dans la hiérarchie
        # (ici sur self, le QDialog), un style partiel ne posant QUE "color"
        # fait retomber QStyleSheetStyle sur un fond par défaut opaque sombre
        # au lieu de laisser le label transparent (comportement Qt connu) —
        # bandes noires sur tous les labels malgré une palette/theme corrects.
        self._lbl_basic.setText(_("dialogs.color_picker.basic_colors_label"))
        self._lbl_basic.setFont(font)
        self._lbl_basic.setStyleSheet(f"color: {theme['text']}; background: transparent;")

        self._lbl_hex.setText(_("dialogs.color_picker.hex_label"))
        self._lbl_hex.setFont(font)
        self._lbl_hex.setStyleSheet(f"color: {theme['text']}; background: transparent;")

        input_style = (
            f"QLineEdit {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 2px 6px; }}"
        )
        self._hex_edit.setFont(font)
        self._hex_edit.setStyleSheet(input_style)

        # Style DISTINCT pour les QSpinBox : "QLineEdit { ... }" ne cible que
        # les vrais QLineEdit (comme _hex_edit) — un QSpinBox est une classe
        # différente, même s'il héberge un QLineEdit interne, donc ce
        # sélecteur ne matchait jamais l'objet sur lequel il était posé.
        # Sans ce style dédié : les 4 spinbox R/V/B/Alpha resteraient noires
        # malgré setStyleSheet(input_style), alors que _hex_edit, un vrai
        # QLineEdit, est déjà correct.
        spinbox_style = (
            f"QSpinBox {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 2px 6px; }} "
            f"QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}"
        )

        # sub-page/add-page DOIVENT être stylés explicitement dès que
        # groove/handle le sont — sinon Qt applique un rendu par défaut
        # incohérent (piège déjà documenté dans clone_tool_qt.py::
        # _CloneOptionsPanel pour la même raison).
        accent = "#4a90d9"
        slider_style = (
            f"QSlider {{ background: transparent; }} "
            f"QSlider::groove:horizontal {{ height: 4px; background: {theme['separator']}; "
            f"border-radius: 2px; }} "
            f"QSlider::sub-page:horizontal {{ height: 4px; background: {accent}; "
            f"border-radius: 2px; }} "
            f"QSlider::add-page:horizontal {{ height: 4px; background: {theme['separator']}; "
            f"border-radius: 2px; }} "
            f"QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0; "
            f"background: {accent}; border: 1px solid {theme['text']}; border-radius: 7px; }}"
        )
        for key, label_key in (("red", "dialogs.color_picker.red_label"),
                                ("green", "dialogs.color_picker.green_label"),
                                ("blue", "dialogs.color_picker.blue_label"),
                                ("alpha", "dialogs.color_picker.alpha_label")):
            lbl = getattr(self, f"_lbl_{key}")
            lbl.setText(_(label_key))
            lbl.setFont(font)
            lbl.setStyleSheet(f"color: {theme['text']}; background: transparent;")
            self._sliders[key].setStyleSheet(slider_style)
            self._spins[key].setFont(font)
            self._spins[key].setStyleSheet(spinbox_style)

        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 12px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self._btn_ok.setText(_("buttons.ok"))
        self._btn_ok.setFont(font)
        self._btn_ok.setStyleSheet(btn_style)
        self._btn_cancel.setText(_("buttons.cancel"))
        self._btn_cancel.setFont(font)
        self._btn_cancel.setStyleSheet(btn_style)


# ─────────────────────────────────────────────────────────────────────────────
# Overlay rich text (QTextEdit transparent) — repris de l'ancienne
# text_viewer_qt.py::_RichTextOverlay, comportement inchangé.
# ─────────────────────────────────────────────────────────────────────────────

class _RichTextOverlay(QTextEdit):
    """QTextEdit transparent positionné en overlay sur _ViewerCanvas.

    Signaux :
      content_changed()   — texte ou format modifié
      block_move(dx, dy)  — déplacement Ctrl+flèche (pixels image)
      activated()         — l'overlay a reçu le focus (clic dessus)
    """

    content_changed = Signal()
    block_move      = Signal(int, int)
    activated       = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.Shape.NoFrame)
        self.setAcceptRichText(True)
        # WrapAnywhere (pas WordWrap, pas NoWrap) : le texte doit revenir à la
        # ligne dès qu'il ATTEINT le bord droit de l'image, y compris en plein
        # milieu d'un mot — WordWrap seul ne coupe QU'ENTRE les mots, donc un
        # mot unique très long (ex. du texte tapé sans espaces) continuerait
        # de s'étendre indéfiniment sans jamais provoquer de saut de ligne
        # (sz.width() croît sans limite au-delà de textWidth pendant que
        # sz.height() reste figée à 1 ligne, le texte défile et le début
        # devient invisible).
        self.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.set_active_style(True)
        self._max_width = -1  # -1 = illimité tant qu'aucune limite n'est encore connue
        self.document().contentsChanged.connect(self._on_contents_changed)

        from modules.qt.utils import setup_textedit_context_menu
        setup_textedit_context_menu(self)

        self.hide()

    def set_max_width(self, max_width: int):
        """Largeur maximale disponible en pixels écran (distance entre la
        position du bloc et le bord droit de l'image, voir TextCanvasMixin.
        _text_reposition_block) — appelée à chaque repositionnement/zoom."""
        self._max_width = max(20, int(max_width)) if max_width > 0 else -1
        self._adjust_size()

    def _on_contents_changed(self):
        self._adjust_size()
        self.content_changed.emit()

    def _adjust_size(self):
        doc = self.document()
        doc.setTextWidth(self._max_width if self._max_width > 0 else -1)
        # documentLayout().documentSize() plutôt que doc.size() : après un
        # setTextWidth() qui vient de changer le wrap, doc.size() peut encore
        # renvoyer une taille transitoire pas encore recalculée pour le
        # nouveau textWidth — le widget resterait alors plus petit que son
        # contenu réellement wrappé, provoquant un défilement horizontal
        # interne masqué (barre cachée par ScrollBarAlwaysOff, mais le texte
        # défilerait quand même et le début tapé deviendrait invisible).
        sz = doc.documentLayout().documentSize()
        # sz.width() vaut TOUJOURS textWidth une fois celui-ci fixé (largeur
        # du layout, pas du contenu) — dès qu'un max_width est posé, le
        # widget prendrait immédiatement toute cette largeur au lieu de
        # rester à la taille de son texte réel, ce qui ferait revenir le
        # texte à la ligne bien avant d'avoir réellement atteint le bord de
        # l'image. idealWidth() donne la largeur minimale réellement
        # nécessaire au contenu, indépendamment de textWidth.
        w_content = int(doc.idealWidth()) + 20
        w = min(w_content, self._max_width) if self._max_width > 0 else w_content
        w = max(w, 60)
        h = max(int(sz.height()) + 8, 24)
        self.resize(w, h)

    def apply_char_format(self, fmt: QTextCharFormat):
        cursor = self.textCursor()
        cursor.mergeCharFormat(fmt)
        self.setTextCursor(cursor)
        self.content_changed.emit()

    def keyPressEvent(self, event):
        key  = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)

        if ctrl and key in (Qt.Key.Key_Left, Qt.Key.Key_Right,
                             Qt.Key.Key_Up, Qt.Key.Key_Down):
            dx, dy = 0, 0
            if key == Qt.Key.Key_Left:    dx = -1
            elif key == Qt.Key.Key_Right: dx =  1
            elif key == Qt.Key.Key_Up:    dy = -1
            elif key == Qt.Key.Key_Down:  dy =  1
            self.block_move.emit(dx, dy)
            event.accept()
            return

        # Undo/redo de frappe natif Qt : reste local tant que ce bloc a le
        # focus, jamais promu en point d'historique global (décision
        # explicite — pas un second système ajouté, comportement
        # standard de QTextEdit détourné vers document().undo()/redo() comme
        # dans n'importe quel champ de texte).
        if ctrl and key == Qt.Key.Key_Z:
            self.document().undo()
            event.accept()
            return
        if ctrl and key == Qt.Key.Key_Y:
            self.document().redo()
            event.accept()
            return

        super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.activated.emit()

    def mouseDoubleClickEvent(self, event):
        # Bloc figé (outil désélectionné) : un QTextEdit capte
        # toujours ses événements souris tant qu'il est visible, même en lecture
        # seule — sans ce court-circuit, double-cliquer sur un bloc gris
        # n'atteindrait jamais _ViewerCanvas.mouseDoubleClickEvent et la
        # bascule plein écran par défaut serait impossible à cet endroit.
        if self.isReadOnly():
            event.ignore()
            return
        super().mouseDoubleClickEvent(event)

    def set_active_style(self, active: bool):
        """Bordure bleue (actif, éditable) ou grise (figé, outil désélectionné
        — même principe que le rectangle de crop conservé en gris)."""
        if active:
            self.setStyleSheet(
                "QTextEdit { background: rgba(0,0,0,0); border: 1px dashed rgba(0,120,255,180); }"
                "QTextEdit QAbstractScrollArea { background: rgba(0,0,0,0); }"
            )
        else:
            self.setStyleSheet(
                "QTextEdit { background: rgba(0,0,0,0); border: 1px dashed rgba(136,136,136,180); }"
                "QTextEdit QAbstractScrollArea { background: rgba(0,0,0,0); }"
            )

    def set_frozen(self, frozen: bool):
        """Outil désélectionné : plus de focus/édition possible, juste affiché
        (figé), jusqu'à resélection de l'outil "text"."""
        self.setReadOnly(frozen)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus if frozen else Qt.FocusPolicy.StrongFocus)
        self.set_active_style(not frozen)
        if frozen:
            if self.hasFocus():
                self.clearFocus()
            cursor = self.textCursor()
            cursor.clearSelection()
            self.setTextCursor(cursor)


# ─────────────────────────────────────────────────────────────────────────────
# Bloc texte (overlay + position image)
# ─────────────────────────────────────────────────────────────────────────────

class _TextBlock:
    """Regroupe un _RichTextOverlay et sa position en coordonnées image
    (stable, indépendante du zoom/pan — même principe que le rectangle de
    crop/le trait de redressage).

    display_scale : niveau de zoom du canvas au moment où le format du bloc
    a été posé/modifié pour la dernière fois — nécessaire pour "dézoomer" les
    tailles de police au rendu final (voir _text_render_all_blocks). Les
    tailles de police posées dans le document Qt sont volontairement
    multipliées par le zoom courant à l'écran (pour un rendu visuel cohérent
    pendant l'édition — un bloc édité à 68% de zoom doit rester lisible à sa
    taille apparente), donc le rendu final doit diviser par ce même facteur
    pour retrouver la taille réellement voulue par l'utilisateur en pixels
    image. Sans cette compensation, un texte tapé à un zoom < 100% apparaîtrait
    beaucoup plus petit qu'attendu une fois appliqué sur l'image à pleine
    résolution."""

    def __init__(self, overlay: _RichTextOverlay, img_x: int, img_y: int, display_scale: float = 1.0):
        self.overlay = overlay
        self.img_pos = QPoint(img_x, img_y)
        self.display_scale = display_scale or 1.0
        # Décalage vertical FIGÉ une seule fois, au tout premier placement
        # (voir TextCanvasMixin.add_text_block/_text_reposition_block) — le
        # point cliqué (img_pos) doit rester le centre vertical du bloc
        # TEL QU'IL ÉTAIT à ce moment précis (un bloc quasi vide, quelques
        # pixels de haut), pas recalculé plus tard avec la hauteur finale
        # après plusieurs lignes de texte : recalculer ce décalage à chaque
        # fois (écran comme rendu final) ferait dériver le bloc de plus en
        # plus loin du point cliqué à mesure que le texte s'allonge. En
        # pixels IMAGE (indépendant du zoom), pour être réutilisable tel quel par
        # le rendu final comme par l'affichage écran (une fois reconverti
        # au zoom courant pour l'écran).
        self.top_y_offset_img: int | None = None

    def html(self) -> str:
        return self.overlay.toHtml()

    def plain_text(self) -> str:
        return self.overlay.toPlainText()

    def is_empty(self) -> bool:
        return not self.plain_text().strip()


# ─────────────────────────────────────────────────────────────────────────────
# Panneau flottant de formatage rich text
# ─────────────────────────────────────────────────────────────────────────────

class _TextOptionsPanel(QWidget):
    """Panneau flottant avec les contrôles de formatage (police, taille, gras,
    italique, souligné, couleur), affiché sous la barre d'outils UNIQUEMENT
    quand l'outil "text" est actif ET qu'un bloc est actif (décision
    explicite) — même principe de positionnement que
    _StraightenAnglePanel/_CloneOptionsPanel."""

    def __init__(self, viewer: "ImageViewer"):
        super().__init__(viewer._canvas)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._viewer = viewer
        self._ignore_format_signals = False
        self._text_color = QColor(0, 0, 0, 255)

        # Timer unique et réutilisable pour différer sync_from_block, au lieu
        # d'un nouveau QTimer.singleShot() à chaque frappe/activation : sans
        # coalescence, taper rapidement empilait plusieurs callbacks différés
        # dans la queue Qt, dont certains s'exécutaient sur un
        # QTextCharFormat déjà périmé (le document ayant changé entre-temps)
        # — provoquerait un access violation natif dans fmt.fontFamily() au
        # 3e/4e appel rapproché. setSingleShot + start() redémarre le délai à chaque
        # appel : seul le DERNIER état demandé est effectivement synchronisé.
        self._sync_timer = None  # QTimer créé après construction complète du widget
        self._pending_sync_block = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        self._font_combo = QFontComboBox()
        self._font_combo.setEditable(False)
        self._font_combo.setMinimumWidth(150)
        self._font_combo.setMaximumWidth(200)
        self._font_combo.currentFontChanged.connect(self._on_font_family_changed)
        layout.addWidget(self._font_combo)

        self._lbl_size = QLabel()
        layout.addWidget(self._lbl_size)

        self._size_spin = QSpinBox()
        self._size_spin.setMinimum(6)
        self._size_spin.setMaximum(500)
        self._size_spin.setValue(24)
        self._size_spin.setFixedWidth(58)
        self._size_spin.valueChanged.connect(self._on_font_size_changed)
        layout.addWidget(self._size_spin)

        self._sep1 = QFrame()
        self._sep1.setFrameShape(QFrame.Shape.VLine)
        layout.addWidget(self._sep1)

        self._bold_btn = QPushButton()
        self._bold_btn.setFixedSize(28, 28)
        self._bold_btn.setCheckable(True)
        self._bold_btn.clicked.connect(self._on_bold_clicked)
        layout.addWidget(self._bold_btn)

        self._italic_btn = QPushButton()
        self._italic_btn.setFixedSize(28, 28)
        self._italic_btn.setCheckable(True)
        self._italic_btn.clicked.connect(self._on_italic_clicked)
        layout.addWidget(self._italic_btn)

        self._underline_btn = QPushButton()
        self._underline_btn.setFixedSize(28, 28)
        self._underline_btn.setCheckable(True)
        self._underline_btn.clicked.connect(self._on_underline_clicked)
        layout.addWidget(self._underline_btn)

        self._sep2 = QFrame()
        self._sep2.setFrameShape(QFrame.Shape.VLine)
        layout.addWidget(self._sep2)

        self._lbl_color = QLabel()
        layout.addWidget(self._lbl_color)

        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(28, 28)
        self._color_btn.clicked.connect(self._pick_color)
        layout.addWidget(self._color_btn)

        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.timeout.connect(self._run_pending_sync)

        self.hide()

    def request_sync(self, block: "_TextBlock"):
        """Point d'entrée unique pour différer sync_from_block — remplace
        les appels directs à QTimer.singleShot() (voir commentaire de
        __init__) : redémarre le délai à chaque appel au lieu d'empiler des
        callbacks, ne synchronise jamais que le DERNIER bloc/état demandé."""
        self._pending_sync_block = block
        self._sync_timer.start(0)

    def _run_pending_sync(self):
        block = self._pending_sync_block
        self._pending_sync_block = None
        if block is None:
            return
        try:
            if not block.overlay.isVisible():
                return
        except RuntimeError:
            return
        if self._viewer._canvas._text_active_block() is not block:
            return
        self.sync_from_block(block)

    def _apply_theme(self):
        from modules.qt.clone_tool_qt import floating_options_panel_style
        theme = get_current_theme()
        self.setStyleSheet(floating_options_panel_style(theme, "_TextOptionsPanel"))
        self._lbl_size.setStyleSheet(f"color: {theme['text']}; background: transparent;")
        self._lbl_color.setStyleSheet(f"color: {theme['text']}; background: transparent;")
        self._sep1.setStyleSheet(f"color: {theme['separator']};")
        self._sep2.setStyleSheet(f"color: {theme['separator']};")
        self._font_combo.setStyleSheet(
            f"QComboBox {{ background: {theme['bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 2px 6px; }} "
            f"QComboBox QAbstractItemView {{ background: {theme['bg']}; color: {theme['text']}; "
            f"selection-background-color: {theme['separator']}; }}"
        )
        self._size_spin.setStyleSheet(
            f"QSpinBox {{ background: {theme['bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 2px 4px; }} "
            f"QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}"
        )
        self._apply_toggle_style(self._bold_btn, "font-weight: bold;")
        self._apply_toggle_style(self._italic_btn, "font-style: italic;")
        self._apply_toggle_style(self._underline_btn, "text-decoration: underline;")
        self._apply_color_btn_style(theme)

    def _apply_color_btn_style(self, theme):
        # Bordure dans la couleur de texte du thème (pas un gris fixe #aaaaaa,
        # quasi invisible sur fond noir en mode sombre) : sans elle, un texte
        # par défaut noir (_text_color initial) rendrait le bouton
        # indiscernable du fond du panneau lui-même. Même principe que
        # floating_options_panel_style.
        self._color_btn.setStyleSheet(
            f"QPushButton {{ background: {self._text_color.name()}; "
            f"border: 2px solid {theme['text']}; border-radius: 3px; }}"
        )

    def _apply_toggle_style(self, btn: QPushButton, extra: str):
        theme = get_current_theme()
        checked = btn.isChecked()
        bg = theme['separator'] if checked else theme['bg']
        self.setProperty("_last_theme", theme)
        btn.setStyleSheet(
            f"QPushButton {{ background: {bg}; color: {theme['text']}; "
            f"border: 1px solid {'#4488cc' if checked else '#aaaaaa'}; {extra} }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )

    def retranslate(self):
        font = _get_current_font(11)
        self._lbl_size.setText(_("dialogs.text_viewer.size_label"))
        self._lbl_size.setFont(font)
        self._lbl_color.setText(_("dialogs.text_viewer.color_label"))
        self._lbl_color.setFont(font)
        self._bold_btn.setText(_("dialogs.text_viewer.bold_btn"))
        self._bold_btn.setFont(font)
        self._italic_btn.setText(_("dialogs.text_viewer.italic_btn"))
        self._italic_btn.setFont(font)
        self._underline_btn.setText(_("dialogs.text_viewer.underline_btn"))
        self._underline_btn.setFont(font)
        self._font_combo.setFont(font)
        self._size_spin.setFont(font)

    # ── Visibilité ────────────────────────────────────────────────────────────

    def set_visible_for_tool(self, tool_id: str | None):
        canvas = self._viewer._canvas
        active_block = canvas._text_active_block()
        if tool_id == "text" and active_block is not None:
            self.show()
            self.reposition()
            self.raise_()
            # Différé via request_sync (timer unique coalescé, voir __init__)
            # : set_visible_for_tool est aussi appelé depuis
            # _on_text_block_activated, elle-même appelée en cascade depuis
            # _RichTextOverlay.focusInEvent (ou content_changed pendant la
            # frappe) — donc en pleine réentrance dans un événement Qt natif
            # en cours de traitement sur ce même widget. sync_from_block()
            # relit currentCharFormat() et manipule d'autres widgets : appelé
            # de façon synchrone à cet instant précis, ça provoquait un
            # access violation natif. Laisser Qt terminer l'événement en
            # cours avant de resynchroniser.
            self.request_sync(active_block)
        else:
            self.hide()

    def reposition(self):
        self.adjustSize()
        canvas = self._viewer._canvas
        x = (canvas.width() - self.width()) // 2
        y = 8 + self._viewer._toolbar.height() + 6
        self.move(max(0, x), y)

    def mousePressEvent(self, event):
        # Sans ce blindage, un clic sur une zone vide du panneau "fuit" vers
        # _ViewerCanvas en dessous — même piège déjà documenté pour
        # _ToolButton/_ActionButton/_ViewerToolbar (skill viewers), appliqué
        # par cohérence à tous les panneaux flottants.
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def enterEvent(self, event):
        # Suspend le timer d'auto-masquage de la barre (dont ce panneau suit
        # désormais la visibilité) tant que la souris reste sur ce panneau —
        # pas seulement redémarré à chaque mouvement, complètement arrêté
        # (voir _ViewerToolbar.pause_hide).
        self._viewer._toolbar.pause_hide()
        # Le curseur posé par text_update_cursor (ex. SizeAllCursor en survol
        # d'un bloc) est celui du CANVAS, pas celui de ce panneau — sans ce
        # reset, il resterait affiché par-dessus les contrôles du panneau,
        # même piège que _TransparencyOptionsPanel/_LevelsOptionsPanel.
        self.setCursor(Qt.ArrowCursor)

    def leaveEvent(self, event):
        # Revérification différée à 0ms : Qt peut envoyer un Leave en
        # transitant entre deux widgets enfants même quand la souris reste
        # visuellement sur le panneau (même piège que _LevelsOptionsPanel).
        QTimer.singleShot(0, self._check_really_left)

    def _check_really_left(self):
        really_left = not self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        if really_left:
            self._viewer._toolbar.resume_hide()
            # setCursor(ArrowCursor) posé dans enterEvent ne s'applique qu'à
            # ce panneau et ses enfants — aucun reset à faire ici pour le
            # canvas : Qt réaffiche de lui-même le curseur déjà posé dessus
            # dès que la souris repasse physiquement au-dessus.
            self.unsetCursor()

    # ── Synchronisation depuis le bloc actif ─────────────────────────────────

    def sync_from_block(self, block: "_TextBlock"):
        if self._ignore_format_signals:
            return
        # Un QTextEdit pas encore affiché (isVisible() False) peut ne pas avoir
        # de format courant utilisable côté C++ Qt — currentCharFormat() dans
        # cet état a provoqué un access violation natif (pas une exception
        # Python attrapable) lors du tout premier appel depuis add_text_block.
        # Garde défensif en plus de l'ordre d'appel corrigé côté appelant.
        try:
            visible = block.overlay.isVisible()
        except RuntimeError:
            return
        if not visible:
            return
        try:
            fmt = block.overlay.currentCharFormat()
        except Exception:
            return
        self._ignore_format_signals = True
        try:
            self._bold_btn.blockSignals(True)
            self._bold_btn.setChecked(fmt.fontWeight() >= QFont.Weight.Bold)
            self._bold_btn.blockSignals(False)
            self._italic_btn.blockSignals(True)
            self._italic_btn.setChecked(fmt.fontItalic())
            self._italic_btn.blockSignals(False)
            self._underline_btn.blockSignals(True)
            self._underline_btn.setChecked(fmt.fontUnderline())
            self._underline_btn.blockSignals(False)
            self._apply_toggle_style(self._bold_btn, "font-weight: bold;")
            self._apply_toggle_style(self._italic_btn, "font-style: italic;")
            self._apply_toggle_style(self._underline_btn, "text-decoration: underline;")
            # Revalidation juste avant fmt.fontFamily() : point de crash
            # identifié à répétition (access violation natif) quand
            # l'utilisateur change rapidement de bloc de texte pendant que ce
            # callback différé était en vol — le focus a pu changer entre le
            # currentCharFormat() ci-dessus et cet appel précis (fontWeight/
            # fontItalic/fontUnderline juste au-dessus ne crashent jamais,
            # seul fontFamily() est en cause : le curseur/document sous-
            # jacent au format peut devenir invalide au moment exact où un
            # autre overlay reçoit le focus). Ne PAS traiter la police si le
            # focus n'est plus sur ce bloc à cet instant précis.
            if not block.overlay.hasFocus():
                return
            family = fmt.fontFamily()
            if family:
                self._font_combo.blockSignals(True)
                idx = self._font_combo.findText(family)
                if idx >= 0:
                    self._font_combo.setCurrentIndex(idx)
                self._font_combo.blockSignals(False)
            pt = fmt.fontPointSize()
            if pt > 0:
                # fmt.fontPointSize() est la taille AFFICHÉE (déjà multipliée
                # par block.display_scale, voir apply_default_format_to_block)
                # — diviser pour remonter la spinbox à la taille logique
                # voulue par l'utilisateur, pas la taille zoomée à l'écran.
                logical_pt = pt / (block.display_scale or 1.0)
                self._size_spin.blockSignals(True)
                self._size_spin.setValue(max(1, int(round(logical_pt))))
                self._size_spin.blockSignals(False)
            fg = fmt.foreground().color()
            if fg.isValid() and fg != QColor(0, 0, 0, 0):
                self._text_color = fg
                self._apply_color_btn_style(get_current_theme())
        finally:
            self._ignore_format_signals = False

    def apply_default_format_to_block(self, block: "_TextBlock"):
        """Applique le format courant des contrôles à un bloc fraîchement créé
        — setCurrentCharFormat seul ne suffit pas sur un document vide, il
        faut aussi poser la police par défaut du document (piège documenté
        dans le skill add-text-to-image).

        La taille choisie dans la spinbox est une taille LOGIQUE (voulue en
        pixels sur l'image finale) — le document affiché à l'écran utilise
        cette taille multipliée par block.display_scale (zoom du canvas au
        moment de la frappe) pour rester visuellement cohérent pendant
        l'édition ; _text_render_all_blocks divise par ce même facteur au
        rendu final pour retrouver la taille logique voulue."""
        family = self._font_combo.currentFont().family()
        display_size = max(1, int(self._size_spin.value() * block.display_scale))
        fmt = QTextCharFormat()
        fmt.setFontFamily(family)
        fmt.setFontPointSize(display_size)
        fmt.setForeground(self._text_color)
        ov = block.overlay
        ov.document().setDefaultFont(QFont(family, display_size))
        ov.setCurrentCharFormat(fmt)

    # ── Contrôles ─────────────────────────────────────────────────────────────

    def _active_overlay(self) -> "_RichTextOverlay | None":
        block = self._viewer._canvas._text_active_block()
        return block.overlay if block else None

    def _on_font_family_changed(self, font):
        if self._ignore_format_signals:
            return
        ov = self._active_overlay()
        if ov is None:
            return
        fmt = QTextCharFormat()
        fmt.setFontFamily(font.family())
        ov.apply_char_format(fmt)
        ov.setFocus()

    def _on_font_size_changed(self, value):
        if self._ignore_format_signals:
            return
        block = self._viewer._canvas._text_active_block()
        if block is None:
            return
        # value = taille logique choisie par l'utilisateur (voulue en pixels
        # sur l'image finale) — le zoom courant peut avoir changé depuis la
        # création du bloc (l'utilisateur peut zoomer/dézoomer en cours
        # d'édition), donc display_scale est reposé ici au zoom ACTUEL avant
        # de calculer la taille affichée (voir apply_default_format_to_block).
        block.display_scale = self._viewer.zoom_level or 1.0
        display_size = max(1, int(value * block.display_scale))
        fmt = QTextCharFormat()
        fmt.setFontPointSize(display_size)
        block.overlay.apply_char_format(fmt)
        block.overlay.setFocus()

    def _on_bold_clicked(self, checked):
        if self._ignore_format_signals:
            return
        ov = self._active_overlay()
        if ov is None:
            return
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Bold if checked else QFont.Weight.Normal)
        ov.apply_char_format(fmt)
        self._apply_toggle_style(self._bold_btn, "font-weight: bold;")
        ov.setFocus()

    def _on_italic_clicked(self, checked):
        if self._ignore_format_signals:
            return
        ov = self._active_overlay()
        if ov is None:
            return
        fmt = QTextCharFormat()
        fmt.setFontItalic(checked)
        ov.apply_char_format(fmt)
        self._apply_toggle_style(self._italic_btn, "font-style: italic;")
        ov.setFocus()

    def _on_underline_clicked(self, checked):
        if self._ignore_format_signals:
            return
        ov = self._active_overlay()
        if ov is None:
            return
        fmt = QTextCharFormat()
        fmt.setFontUnderline(checked)
        ov.apply_char_format(fmt)
        self._apply_toggle_style(self._underline_btn, "text-decoration: underline;")
        ov.setFocus()

    def _pick_color(self):
        # Fenêtre maison (_ColorPickerDialog), non-modale, résultat via signal
        # color_picked — QColorDialog abandonné (voir docstring de
        # _ColorPickerDialog) : ses widgets internes restaient illisibles en
        # mode sombre malgré plusieurs tentatives de palette/stylesheet, et
        # les bricoler avec un thème clair fixe en dur violait de toute façon
        # la règle centrale du projet (suivre get_current_theme()).
        # Centrée sur self._viewer (la visionneuse principale, ImageViewer) —
        # pas sur self (_TextOptionsPanel, un petit panneau flottant en haut
        # de l'écran) qui donnerait une position peu pertinente.
        dlg = _ColorPickerDialog(self._viewer, self._text_color)
        dlg.color_picked.connect(self._on_color_picked)
        # adjustSize() AVANT position_dialog_on_parent : ce dialogue n'a pas
        # de taille explicite imposée (contrairement à l'avertissement de
        # position_dialog_on_parent sur adjustSize(), qui vise les fenêtres à
        # taille FIXE explicite — non applicable ici). ensurePolished() seul
        # ne suffirait pas à faire calculer la vraie taille du layout à ce
        # stade : dialog.height() vaudrait encore une hauteur par défaut
        # minime au moment du centrage, donc le calcul (ph - dialog.height())
        # // 2 placerait la fenêtre trop bas — elle grandirait ensuite vers
        # le bas une fois affichée.
        dlg.adjustSize()
        from modules.qt.dialogs_qt import position_dialog_on_parent
        position_dialog_on_parent(dlg, self._viewer)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_color_picked(self, color: QColor):
        if not color.isValid():
            return
        self._text_color = color
        self._apply_color_btn_style(get_current_theme())
        ov = self._active_overlay()
        if ov is not None:
            fmt = QTextCharFormat()
            fmt.setForeground(color)
            ov.apply_char_format(fmt)
            ov.setFocus()


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état et interactions souris de l'outil (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class TextCanvasMixin:
    """Hérité par _ViewerCanvas (image_viewer_qt.py) en plus de QLabel : ajoute
    l'état et les méthodes de l'outil "text" au canvas de la visionneuse, sans
    que leur code vive dans image_viewer_qt.py. Suppose que l'hôte a déjà
    self._viewer (ImageViewer) et les attributs habituels de _ViewerCanvas
    (display_offset_x/y).

    Les blocs (_RichTextOverlay) sont des enfants Qt directs du canvas —
    repositionnés depuis leurs coordonnées image stables à chaque pan/zoom/
    resize, comme le rectangle de crop et le trait de redressage."""

    def _init_text_state(self):
        self._text_blocks: list[_TextBlock] = []
        self._text_active_block_ref: _TextBlock | None = None
        self._text_drag_block: _TextBlock | None = None
        self._text_drag_pending  = False
        self._text_dragging      = False
        self._text_drag_start_widget  = None
        self._text_drag_start_img_pos = None

    def _text_active_block(self) -> "_TextBlock | None":
        return self._text_active_block_ref

    @property
    def has_text_blocks(self) -> bool:
        return bool(self._text_blocks)

    def _text_widget_to_image(self, pt: QPoint) -> tuple:
        zoom = self._viewer.zoom_level or 1.0
        ix = (pt.x() - self.display_offset_x) / zoom
        iy = (pt.y() - self.display_offset_y) / zoom
        return int(ix), int(iy)

    def _text_image_to_widget(self, ix: float, iy: float) -> QPoint:
        zoom = self._viewer.zoom_level or 1.0
        return QPoint(int(self.display_offset_x + ix * zoom),
                       int(self.display_offset_y + iy * zoom))

    def _text_reposition_block(self, block: "_TextBlock"):
        # block.img_pos est le point cliqué par l'utilisateur — c'est le
        # DÉBUT horizontal du texte (bord gauche du widget), comme dans
        # n'importe quel éditeur de texte : on clique où on veut commencer à
        # écrire, pas où on veut que le texte soit centré horizontalement (un
        # centrage horizontal introduirait d'autres problèmes : largeur de
        # wrap mal calculée, dérive pendant la frappe). Le centrage VERTICAL,
        # lui, reste voulu : le point cliqué doit rester au milieu de la
        # hauteur du bloc — mais TEL QU'ELLE ÉTAIT au moment du placement
        # initial (voir _TextBlock.top_y_offset_img), jamais recalculé avec
        # la hauteur courante : sinon le décalage grandirait avec le texte
        # tapé, et le rendu final (qui doit reproduire EXACTEMENT ce même
        # décalage figé) recalculerait alors une valeur différente à partir
        # de la hauteur finale, aggravant l'écart à chaque ligne
        # supplémentaire.
        if block.top_y_offset_img is None:
            zoom_at_creation = block.display_scale or 1.0
            block.top_y_offset_img = int(round(
                (block.overlay.height() / 2) / zoom_at_creation))
        wpt = self._text_image_to_widget(block.img_pos.x(), block.img_pos.y())
        zoom = self._viewer.zoom_level or 1.0
        offset_screen = int(round(block.top_y_offset_img * zoom))
        target = QPoint(wpt.x(), wpt.y() - offset_screen)
        block.overlay.move(target)
        # Largeur maximale disponible jusqu'au bord droit RÉEL de l'image
        # (pas du canvas) — le texte doit revenir à la ligne automatiquement
        # en l'ATTEIGNANT, pas déborder hors du cadre affiché (voir
        # _RichTextOverlay.set_max_width).
        right_edge = self.display_offset_x + self.display_width
        max_w = right_edge - wpt.x()
        block.overlay.set_max_width(max_w)

    def reposition_text_blocks(self):
        """À appeler après tout changement de zoom/pan/taille du canvas — même
        piège documenté dans le skill add-text-to-image (sans ça, les overlays
        restent à leurs anciennes coordonnées widget alors que l'image a bougé)."""
        for b in self._text_blocks:
            self._text_reposition_block(b)

    def _text_block_at(self, pos: QPoint) -> "_TextBlock | None":
        for b in reversed(self._text_blocks):
            if not b.overlay.isVisible():
                continue
            r = QRect(b.overlay.pos(), b.overlay.size())
            if r.adjusted(-4, -4, 4, 4).contains(pos):
                return b
        return None

    def _text_activate_block(self, block: "_TextBlock"):
        if self._text_active_block_ref is block:
            return
        if self._text_active_block_ref is not None:
            prev = self._text_active_block_ref.overlay
            cursor = prev.textCursor()
            cursor.clearSelection()
            prev.setTextCursor(cursor)
        self._text_active_block_ref = block
        self._viewer._on_text_block_activated(block)

    def add_text_block(self, ix: int, iy: int) -> "_TextBlock":
        overlay = _RichTextOverlay(self)
        overlay.content_changed.connect(self._viewer._on_text_content_changed)
        overlay.block_move.connect(
            lambda dx, dy: self._on_text_block_move_signal(dx, dy))
        zoom = self._viewer.zoom_level or 1.0
        block = _TextBlock(overlay, ix, iy, display_scale=zoom)
        overlay.activated.connect(lambda bl=block: self._text_activate_block(bl))
        self._text_blocks.append(block)
        self._text_reposition_block(block)
        overlay.show()
        overlay.raise_()
        # Format par défaut posé et overlay affiché AVANT _text_activate_block :
        # celle-ci déclenche _TextOptionsPanel.sync_from_block(), qui appelle
        # overlay.currentCharFormat() côté C++ Qt — sur un QTextEdit encore
        # caché et sans format défini, cet appel provoquait un access violation
        # natif (crash immédiat, aucune exception Python à attraper) au premier
        # clic de placement d'un bloc de texte.
        self._viewer._toolbar._text_panel.apply_default_format_to_block(block)
        self._text_activate_block(block)
        overlay.setFocus()
        return block

    def _on_text_block_move_signal(self, dx: int, dy: int):
        block = self._text_active_block_ref
        if block is None:
            return
        block.img_pos = QPoint(block.img_pos.x() + dx, block.img_pos.y() + dy)
        self._text_reposition_block(block)
        self._viewer._on_text_content_changed()

    def clear_text_blocks(self):
        for b in self._text_blocks:
            b.overlay.hide()
            b.overlay.deleteLater()
        self._text_blocks.clear()
        self._text_active_block_ref = None

    def _remove_block(self, block: "_TextBlock"):
        """Retire un bloc précis (overlay masqué + deleteLater), sans toucher
        aux autres. Utilisé pour supprimer un bloc resté vide plutôt que de
        le laisser s'accumuler indéfiniment — voir text_mouse_press."""
        if block in self._text_blocks:
            self._text_blocks.remove(block)
        block.overlay.hide()
        block.overlay.deleteLater()
        if self._text_active_block_ref is block:
            self._text_active_block_ref = None

    def _text_set_frozen(self, frozen: bool):
        """Applique l'état figé/actif à tous les blocs — appelé à la
        (dé)sélection de l'outil "text" (décision explicite :
        les blocs restent affichés, gris, non éditables tant que l'outil
        n'est pas resélectionné)."""
        for b in self._text_blocks:
            b.overlay.set_frozen(frozen)

    # ── Événements souris (appelés depuis _ViewerCanvas.mousePress/Move/ReleaseEvent) ──

    def text_mouse_press(self, event) -> bool:
        pos = event.position().toPoint()
        hit = self._text_block_at(pos)
        # Un clic (sur un bloc existant OU une zone vide) pendant qu'un bloc
        # encore vide (jamais tapé) est actif ne doit pas le laisser traîner
        # indéfiniment — le retirer plutôt que d'en accumuler. Cliquer
        # rapidement sur plusieurs zones sans taper ferait exploser le nombre
        # de _RichTextOverlay vivants simultanément (chacun avec son
        # focus/timer de sync en vol), terrain instable pour des access
        # violations natifs dans sync_from_block.
        active = self._text_active_block_ref
        if active is not None and active is not hit and active.is_empty():
            self._remove_block(active)
        if hit is not None:
            self._text_activate_block(hit)
            self._text_drag_block   = hit
            self._text_drag_pending = True
            self._text_drag_start_widget  = pos
            self._text_drag_start_img_pos = QPoint(*self._text_widget_to_image(pos))
        else:
            ix, iy = self._text_widget_to_image(pos)
            # Clic hors de l'image (marges autour d'une image plus petite que
            # le canvas) créerait un bloc à des coordonnées négatives/hors
            # limites — sans clamp, contrairement au rectangle de crop (voir
            # crop_tool_qt.py, même principe). Un bloc placé à une position
            # aberrante reste un widget Qt valide mais positionné très loin
            # de sa zone normale, terrain supplémentaire d'instabilité pour Qt.
            ix, iy = self._clamp_to_image(ix, iy)
            self.add_text_block(ix, iy)
        return True

    def _clamp_to_image(self, ix: int, iy: int) -> tuple:
        zoom = self._viewer.zoom_level or 1.0
        iw = int(self.display_width / zoom) if self.display_width else 0
        ih = int(self.display_height / zoom) if self.display_height else 0
        if iw > 0:
            ix = max(0, min(ix, iw - 1))
        if ih > 0:
            iy = max(0, min(iy, ih - 1))
        return ix, iy

    def text_mouse_move(self, event) -> bool:
        pos = event.position().toPoint()
        if self._text_drag_pending and self._text_drag_start_widget is not None:
            diff = pos - self._text_drag_start_widget
            if diff.x() ** 2 + diff.y() ** 2 >= 16:
                self._text_drag_pending = False
                self._text_dragging     = True
                self.setCursor(Qt.SizeAllCursor)
            return True
        if self._text_dragging and self._text_drag_block is not None:
            cur_img = QPoint(*self._text_widget_to_image(pos))
            dx = cur_img.x() - self._text_drag_start_img_pos.x()
            dy = cur_img.y() - self._text_drag_start_img_pos.y()
            block = self._text_drag_block
            block.img_pos = QPoint(block.img_pos.x() + dx, block.img_pos.y() + dy)
            self._text_drag_start_img_pos = cur_img
            self._text_reposition_block(block)
            return True
        return False

    def text_update_cursor(self, event):
        if self._text_block_at(event.position().toPoint()) is not None:
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.setCursor(_get_text_cursor())

    def text_mouse_release(self, event) -> bool:
        handled = False
        if self._text_dragging:
            self._text_dragging = False
            handled = True
            self._viewer._on_text_content_changed()
        elif self._text_drag_pending:
            self._text_drag_pending = False
            if self._text_drag_block is not None:
                self._text_drag_block.overlay.setFocus()
            handled = True
        self._text_drag_block = None
        self._text_drag_start_widget  = None
        self._text_drag_start_img_pos = None
        return handled


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — rendu / commit / persistance par page (hérité par ImageViewer)
# ─────────────────────────────────────────────────────────────────────────────

class TextViewerMixin:
    """Hérité par ImageViewer (image_viewer_qt.py) en plus de QDialog : ajoute
    la logique de rendu/validation/persistance de l'outil "text" au viewer,
    sans que son code vive dans image_viewer_qt.py. Suppose que l'hôte a déjà
    self._canvas (_ViewerCanvas, avec TextCanvasMixin), self.callbacks,
    self.current_idx, self._toolbar (avec _text_panel), et
    self._text_blocks_by_page (persistance par page, initialisée dans
    ImageViewer.__init__ comme _crop_by_page/_straighten_by_page)."""

    def _on_text_block_activated(self, block):
        self._toolbar._text_panel.set_visible_for_tool(self._toolbar.active_tool)

    def _on_text_content_changed(self):
        if self._toolbar.active_tool == "text" and self._canvas.has_text_blocks:
            self._canvas._update_validate_btn_state()
            self._canvas._update_cancel_btn_state()
        # Différé via request_sync (timer unique coalescé côté
        # _TextOptionsPanel, voir son __init__) : content_changed est émis
        # SYNCHRONEMENT depuis _RichTextOverlay.keyPressEvent (via
        # _on_contents_changed), donc en pleine réentrance dans le
        # traitement Qt natif de la frappe. Resynchroniser la barre
        # d'options immédiatement à cet instant provoquerait un access
        # violation natif — et sans coalescence, taper rapidement empilerait
        # plusieurs callbacks différés s'exécutant sur un QTextCharFormat
        # déjà périmé (access violation dans fmt.fontFamily() au 3e/4e appel
        # rapproché).
        panel = self._toolbar._text_panel
        active = self._canvas._text_active_block()
        if active is not None and panel.isVisible():
            panel.request_sync(active)

    # ── Rendu final — tous les blocs → PIL ────────────────────────────────────

    def _text_render_all_blocks(self, base_img: Image.Image) -> Image.Image:
        img = base_img.copy()
        iw, ih = img.size
        for block in self._canvas._text_blocks:
            if block.is_empty():
                continue
            doc = block.overlay.document().clone()
            # Même largeur de wrap que celle vue à l'écran pendant l'édition
            # (_RichTextOverlay._max_width, voir TextCanvasMixin.
            # _text_reposition_block) — sinon le rendu final ignorerait le
            # retour à la ligne automatique posé au bord de l'image et
            # produirait à nouveau un texte débordant sur une seule ligne,
            # incohérent avec ce que l'utilisateur a validé visuellement.
            overlay_max_w = block.overlay._max_width
            doc.setTextWidth(overlay_max_w if overlay_max_w > 0 else -1)
            sz = doc.size()
            # Les tailles de police du document sont celles AFFICHÉES à
            # l'écran (voir apply_default_format_to_block/_on_font_size_changed :
            # taille logique × block.display_scale, zoom du canvas au moment
            # de l'édition) — les dézoomer ici (facteur inverse) pour que le
            # rendu final corresponde à la taille voulue par l'utilisateur en
            # pixels sur l'image native, indépendamment du zoom courant.
            # Sans cette compensation, un bloc édité à un zoom < 100% (ex.
            # 68%) apparaîtrait beaucoup plus petit qu'attendu une fois
            # appliqué.
            scale = 1.0 / (block.display_scale or 1.0)
            tw = max(int(sz.width() * scale), 1)
            th = max(int(sz.height() * scale), 1)

            text_img = QImage(tw, th, QImage.Format.Format_ARGB32)
            text_img.fill(Qt.GlobalColor.transparent)
            painter = QPainter(text_img)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            painter.scale(scale, scale)
            doc.drawContents(painter, QRectF(0, 0, sz.width(), sz.height()))
            painter.end()

            ptr = text_img.constBits()
            arr = bytes(ptr)
            # Ordre de canaux BGRA (pas RGBA) : format mémoire natif de
            # QImage.Format_ARGB32, voir skill add-text-to-image.
            text_pil = Image.frombytes('RGBA', (tw, th), arr, 'raw', 'BGRA')

            # block.img_pos est le point cliqué ; block.top_y_offset_img est
            # le décalage vertical FIGÉ une seule fois au placement initial
            # (voir _TextBlock.__init__/TextCanvasMixin._text_reposition_block)
            # — le reproduire ici TEL QUEL, jamais le recalculer à partir de
            # la hauteur courante du widget (qui a grandi avec le texte tapé
            # et ne correspond plus à la position réellement affichée à
            # l'écran). Recalculer ce décalage ici avec la hauteur finale au
            # lieu de réutiliser la valeur figée aggraverait l'écart à chaque
            # ligne supplémentaire.
            x = block.img_pos.x()
            y = block.img_pos.y() - (block.top_y_offset_img or 0)
            px = max(0, min(x, iw - 1))
            py = max(0, min(y, ih - 1))
            img.paste(text_pil, (px, py), text_pil)
        return img

    def validate_text(self):
        from modules.qt.dialogs_qt import MsgDialog
        blocks = self._canvas._text_blocks
        if not any(not b.is_empty() for b in blocks):
            dlg = MsgDialog(
                self,
                "messages.warnings.no_text_block.title",
                "messages.warnings.no_text_block.message",
            )
            dlg.show_nonmodal()
            return
        self.perform_text()

    def perform_text(self):
        import io as _io
        from modules.qt import state as _state_module
        from modules.qt.entries import save_image_to_bytes
        from modules.qt.dialogs_qt import MsgDialog

        state = self.callbacks.get('state') or _state_module.state
        save_state    = self.callbacks.get("save_state")
        render_mosaic = self.callbacks.get("render_mosaic")
        update_btn    = self.callbacks.get("update_button_text")
        canvas_cb     = self.callbacks.get("canvas")

        try:
            entry = state.images_data[self.current_idx]
            if not entry.get('bytes'):
                dlg = MsgDialog(self._center_parent, "messages.errors.text_failed.title",
                                "messages.errors.text_failed.title")
                dlg.show_nonmodal()
                return

            base_img_raw = Image.open(_io.BytesIO(entry['bytes']))
            # Mode d'origine (pour la reconversion en sortie plus bas) — posé
            # une seule fois, paresseusement à la première application de texte
            # sur cette page, même principe que clone_tool_qt.py::
            # CloneViewerMixin._on_clone_paint_stroke (pas à l'ouverture de la
            # visionneuse : ImageViewer sert à bien d'autres usages).
            if '_orig_mode' not in entry:
                entry['_orig_mode'] = base_img_raw.mode
            base_img = base_img_raw.convert('RGBA')

            if save_state:
                save_state()

            composed = self._text_render_all_blocks(base_img)

            orig_mode = entry.get('_orig_mode', 'RGBA')
            out_img = composed
            # .bmp exclu : Pillow écrit bien un canal alpha 32-bit, mais ne le
            # redétecte pas à la relecture (header BMP classique ambigu sur la
            # présence d'alpha) — transparence non fiable, voir color_depth_tool_qt.py.
            if orig_mode not in ('RGBA', 'LA', 'P') and \
                    entry.get('extension', '').lower() not in (
                        '.png', '.webp', '.avif', '.tiff', '.tif', '.ico'
                    ):
                bg = Image.new('RGB', out_img.size, (255, 255, 255))
                bg.paste(out_img, mask=out_img.split()[3])
                out_img = bg

            entry["img"]   = out_img.copy()
            entry["bytes"] = save_image_to_bytes(entry)
            entry["img"] = None
            entry["_thumbnail"] = None
            entry["large_thumb_pil"] = None
            entry["qt_pixmap_large"] = None
            entry["qt_qimage_large"] = None
            entry["_hash"] = None
            state.modified = True

            from modules.qt.comic_info import get_page_image_index, update_page_entries_in_xml_data
            _pidx = get_page_image_index(state, entry)
            if _pidx is not None:
                update_page_entries_in_xml_data(state, [(_pidx, entry)])
            if save_state:
                save_state(force=True)

            real_idx = entry.get("_real_idx")
            if canvas_cb is not None and real_idx is not None:
                from modules.qt.mosaic_canvas import build_qimage_for_entry
                build_qimage_for_entry(entry)
                canvas_cb.refresh_thumbnail(real_idx)
                canvas_cb.refresh_duplicate_overlay()
            elif render_mosaic:
                render_mosaic()
            if update_btn:
                update_btn()

            self._canvas.clear_text_blocks()
            self._toolbar._text_panel.set_visible_for_tool(self._toolbar.active_tool)
            # Recalcule seulement l'ÉTAT du bouton (repasse en gris,
            # has_text_blocks vient de redevenir faux) — ne touche JAMAIS à sa
            # visibilité, pilotée uniquement par _ViewerToolbar.
            # show_and_schedule_hide/_on_hide_timeout (mécanisme unique, voir
            # image_viewer_qt.py::_update_validate_btn_state). Bouton
            # "Annuler" jumeau rafraîchi juste à côté.
            self._canvas._update_validate_btn_state()
            self._canvas._update_cancel_btn_state()
            self._text_blocks_by_page.pop(self.current_idx, None)
            self.display_image()
            self._toolbar.refresh_undo_redo_state()

        except Exception:
            dlg = MsgDialog(self._center_parent, "messages.errors.text_failed.title",
                            "messages.errors.text_failed.title")
            dlg.show_nonmodal()

    # ── Persistance par page ──────────────────────────────────────────────────

    def _save_text_for_current_page(self):
        """Mémorise les blocs de la page qu'on s'apprête à quitter (contenu
        HTML + position image + display_scale) — même principe que
        _crop_by_page/_straighten_by_page, mais liste de blocs au lieu d'une
        seule géométrie. display_scale doit être conservé : le HTML sérialisé
        contient des tailles de police déjà mises à l'échelle de ce facteur
        (voir _TextBlock.display_scale), le perdre désynchroniserait le
        rendu final au moment de l'application."""
        blocks = self._canvas._text_blocks
        if blocks:
            self._text_blocks_by_page[self.current_idx] = [
                (b.img_pos.x(), b.img_pos.y(), b.html(), b.display_scale, b.top_y_offset_img)
                for b in blocks
            ]
        else:
            self._text_blocks_by_page.pop(self.current_idx, None)

    def _restore_text_for_page(self, idx: int):
        """Recrée les blocs mémorisés pour la page idx, figés ou actifs selon
        l'outil actuellement sélectionné dans la barre."""
        self._canvas.clear_text_blocks()
        saved = self._text_blocks_by_page.get(idx)
        if not saved:
            self._toolbar._text_panel.set_visible_for_tool(self._toolbar.active_tool)
            return
        for ix, iy, html, display_scale, top_y_offset_img in saved:
            block = self._canvas.add_text_block(ix, iy)
            # display_scale et top_y_offset_img d'origine restaurés APRÈS
            # add_text_block : celui-ci pose display_scale = zoom_level
            # courant et fige top_y_offset_img avec la hauteur du widget
            # encore vide à cet instant — les deux doivent être écrasés par
            # les valeurs sauvegardées (celles réellement utilisées à
            # l'écran/au rendu quand le bloc a été tapé), sinon changer de
            # page puis y revenir recalculait un décalage vertical différent
            # de celui figé initialement.
            block.display_scale = display_scale
            block.top_y_offset_img = top_y_offset_img
            block.overlay.setHtml(html)
            # add_text_block a déjà positionné l'overlay une première fois
            # avec top_y_offset_img=None (donc recalculé à partir du widget
            # encore vide) — repositionner maintenant que la vraie valeur
            # sauvegardée a été restaurée ci-dessus.
            self._canvas._text_reposition_block(block)
        frozen = self._toolbar.active_tool != "text"
        self._canvas._text_set_frozen(frozen)
        if frozen:
            self._canvas._text_active_block_ref = None
        self._toolbar._text_panel.set_visible_for_tool(self._toolbar.active_tool)
