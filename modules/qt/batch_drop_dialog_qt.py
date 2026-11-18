"""
batch_drop_dialog_qt.py — Fenêtre de choix du traitement par lot d'un ou
plusieurs dossiers (dépôt d'un dossier, ou menu Fichier > Batch >
« Conversions et compression des BD »).
Règles UI Qt : thème, langue à la volée, police courante.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QButtonGroup, QRadioButton, QCheckBox, QFrame, QWidget,
    QStyle, QStyleOptionButton,
)
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPalette

from modules.qt.app_window_qt import AppWindowMixin
from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font


# Traitements du groupe « Conversions et compression des BD », dans l'ordre
# affiché : type → clé de son libellé (buttons.*)
STEPS = (("cbr", "batch_cbr_to_cbz"), ("cb7", "batch_cb7_to_cbz"),
         ("cbt", "batch_cbt_to_cbz"), ("pdf", "batch_pdf_to_cbz"),
         ("img", "batch_img_to_cbz"), ("extensions", "batch_check_extensions"),
         ("recompress", "batch_recompress"))
STEP_KINDS = tuple(kind for kind, _key in STEPS)

# Traitements cochés, retenus jusqu'à la fermeture de l'application (jamais
# enregistrés) : tout est coché au lancement
_session_checked = set(STEP_KINDS)


def _radio_label(key):
    return _("buttons." + key).replace("\n", " ").replace("  ", " ").strip()


class _PartialRadio(QRadioButton):
    """Bouton radio qui montre une sélection partielle, comme une case à
    cocher à trois états : choisi alors que seule une partie de ses
    traitements est cochée, son rond porte un tiret au lieu du point.
    Qt n'a pas d'état partiel pour un bouton radio : le point dessiné par le
    style est recouvert de la couleur de fond du rond, puis le tiret tracé."""

    def __init__(self):
        super().__init__()
        self._partial = False

    def set_partial(self, partial):
        if partial != self._partial:
            self._partial = partial
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not (self._partial and self.isChecked()):
            return
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        rect = QRectF(self.style().subElementRect(QStyle.SE_RadioButtonIndicator, opt, self))
        inner = rect.adjusted(rect.width() * 0.22, rect.height() * 0.22,
                              -rect.width() * 0.22, -rect.height() * 0.22)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.palette().color(QPalette.Base))
        painter.drawEllipse(inner)
        dash_h = max(2.0, rect.height() * 0.16)
        dash = QRectF(rect.center().x() - rect.width() * 0.24,
                      rect.center().y() - dash_h / 2,
                      rect.width() * 0.48, dash_h)
        painter.setBrush(self.palette().color(QPalette.WindowText))
        painter.drawRoundedRect(dash, dash_h / 2, dash_h / 2)
        painter.end()


class BatchDropDialog(AppWindowMixin, QDialog):
    """
    Fenêtre de choix du traitement par lot des dossiers `dirs`.
    Supporte : thème courant, changement de langue à la volée, police courante.

    Trois choix exclusifs (boutons radio) : en tête « Conversions et
    compression des BD » (choisi par défaut), puis l'import de métadonnées et
    la Bibliothèque. La flèche de la ligne de tête déplie ou replie les
    traitements du groupe, des cases à cocher (STEPS) : une seule cochée
    lance ce traitement seul, plusieurs les enchaînent (conversion complète).
    Cases retenues jusqu'à la fermeture de l'application (_session_checked),
    toutes cochées au lancement ; groupe replié à chaque ouverture ; la
    hauteur de la fenêtre suit le dépliage (_fit_height).

    Résultat : chosen ('all', 'metadata', 'library', None si annulé) et,
    pour 'all', steps (types cochés, dans l'ordre de STEP_KINDS).
    from_menu : ouverte depuis le menu (dossier choisi, pas déposé).
    """

    _WIDTH = 480

    def __init__(self, parent, dirs: list[str], from_menu=False):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("dialogs.batch_drop.window_title")
        self._dirs  = dirs
        self._count = len(dirs)
        self._from_menu = from_menu
        self._expanded = False
        self.chosen = None
        self.steps = ()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(4)

        # Label intro
        self._lbl_intro = QLabel()
        self._lbl_intro.setTextFormat(Qt.PlainText)
        self._lbl_intro.setWordWrap(True)
        self._lbl_intro.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_intro)

        # Note récursivité, sous l'intro : elle vaut pour tous les choix
        self._lbl_note = QLabel()
        self._lbl_note.setTextFormat(Qt.PlainText)
        self._lbl_note.setWordWrap(True)
        self._lbl_note.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_note)

        layout.addSpacing(8)

        # Choix exclusifs
        self._btn_group = QButtonGroup(self)
        self._radio_all      = _PartialRadio()
        self._radio_metadata = QRadioButton()
        self._radio_library  = QRadioButton()
        self._choices = {self._radio_all: "all", self._radio_metadata: "metadata",
                         self._radio_library: "library"}
        for rb in self._choices:
            self._btn_group.addButton(rb)
        self._radio_all.setChecked(True)
        self._btn_group.buttonToggled.connect(lambda _b, _c: self._update_ok())
        # Reclic sur le groupe déjà choisi : tous ses traitements recochés.
        # L'état d'avant le clic est relevé à l'appui (pressed), clicked
        # arrivant après le changement d'état
        self._all_was_checked = False
        self._radio_all.pressed.connect(
            lambda: setattr(self, "_all_was_checked", self._radio_all.isChecked()))
        self._radio_all.clicked.connect(lambda _on: self._on_group_clicked())

        # « Conversions et compression des BD » et sa flèche de dépliage
        all_row = QHBoxLayout()
        all_row.setContentsMargins(0, 0, 0, 0)
        all_row.setSpacing(4)
        self._btn_expand = QPushButton()
        self._btn_expand.setFixedSize(22, 22)
        self._btn_expand.setFlat(True)
        self._btn_expand.setCursor(Qt.PointingHandCursor)
        self._btn_expand.clicked.connect(self._toggle_expanded)
        all_row.addWidget(self._btn_expand)
        all_row.addWidget(self._radio_all)
        all_row.addStretch()
        layout.addLayout(all_row)

        # Traitements du groupe, en retrait
        self._sub_box = QWidget()
        sub_layout = QVBoxLayout(self._sub_box)
        # Retrait au-delà de la flèche (22 + 4) et du rond de la ligne de
        # tête : les traitements se lisent comme contenus dans ce choix
        sub_layout.setContentsMargins(54, 0, 0, 0)
        sub_layout.setSpacing(4)
        self._step_checks = {}
        for kind, _key in STEPS:
            check = QCheckBox()
            check.setChecked(kind in _session_checked)
            check.toggled.connect(lambda on, k=kind: self._on_step_toggled(k, on))
            sub_layout.addWidget(check)
            self._step_checks[kind] = check
        self._sub_box.setVisible(False)
        layout.addWidget(self._sub_box)

        layout.addSpacing(4)
        sep_radios = QFrame()
        sep_radios.setFrameShape(QFrame.HLine)
        sep_radios.setFrameShadow(QFrame.Sunken)
        self._sep_radios = sep_radios
        layout.addWidget(sep_radios)
        layout.addSpacing(4)

        for rb in (self._radio_metadata, self._radio_library):
            layout.addWidget(rb)

        layout.addSpacing(4)

        # Séparateur
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep)

        layout.addSpacing(4)

        # Boutons OK / Annuler
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_ok     = QPushButton()
        self._btn_ok.setFixedWidth(110)
        self._btn_ok.setDefault(True)
        self._btn_ok.clicked.connect(self._on_ok)
        self._btn_cancel = QPushButton()
        self._btn_cancel.setFixedWidth(110)
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._btn_ok)
        btn_row.addSpacing(16)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.rejected.connect(self._on_cancel)
        self._update_ok()
        self._connect_language()

    # ── Traduction / thème / police ───────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        bg    = theme["bg"]
        fg    = theme["text"]
        tb_bg = theme["toolbar_bg"]
        sep   = theme["separator"]

        self.setStyleSheet(
            f"QDialog {{ background: {bg}; color: {fg}; }}"
            f"QRadioButton {{ background: {bg}; color: {fg}; }}"
            f"QCheckBox {{ background: {bg}; color: {fg}; }}"
            f"QFrame[frameShape='4'] {{ color: {sep}; }}"  # HLine = 4
        )
        self._sep_radios.setStyleSheet(f"color: {sep};")

        font10 = _get_current_font(10)
        font11 = _get_current_font(11)
        font9  = _get_current_font(9)
        btn_style = (
            f"QPushButton {{ background: {tb_bg}; color: {fg}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {sep}; }} "
            f"QPushButton:disabled {{ color: {sep}; }}"
        )

        if self._from_menu:
            intro = _("dialogs.batch_drop.intro_chosen")
        elif self._count == 1:
            intro = _("dialogs.batch_drop.intro_single")
        else:
            intro = _("dialogs.batch_drop.intro_multiple").format(count=self._count)
        self._lbl_intro.setText(intro)
        self._lbl_intro.setFont(font11)

        self._radio_all.setText(_("dialogs.batch_drop.all_to_cbz"))
        self._radio_all.setFont(font10)
        # ► (U+25BA) et non ▶ (U+25B6) : Windows dessine ▶ comme un emoji,
        # dans une autre police que ▼
        self._btn_expand.setText("▼" if self._expanded else "►")
        self._btn_expand.setFont(font9)
        self._btn_expand.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {fg}; border: none; }} "
            f"QPushButton:hover {{ background: {sep}; }}")
        for kind, key in STEPS:
            self._step_checks[kind].setText(_radio_label(key))
            self._step_checks[kind].setFont(font10)
        self._radio_metadata.setText(_radio_label("batch_metadata"))
        self._radio_metadata.setFont(font10)
        self._radio_library.setText(_radio_label("batch_library"))
        self._radio_library.setFont(font10)

        self._lbl_note.setText(_("dialogs.batch_drop.recursive_note"))
        self._lbl_note.setFont(font9)

        self._btn_ok.setText(_("buttons.ok"))
        self._btn_ok.setFont(font10)
        self._btn_ok.setStyleSheet(btn_style)
        self._btn_cancel.setText(_("buttons.cancel"))
        self._btn_cancel.setFont(font10)
        self._btn_cancel.setStyleSheet(btn_style)
        # Textes et police changés : hauteur recalculée
        self._fit_height()

    def _fit_height(self):
        """Hauteur fixe de la fenêtre, celle de son contenu à la largeur
        _WIDTH : elle suit le dépliage, la langue et la taille de police.
        totalHeightForWidth compte les marges de la fenêtre (barre de titre
        comprise), que setFixedSize ajoute lui-même : elles sont retirées."""
        layout = self.layout()
        layout.activate()
        margins = self.contentsMargins()
        width = self._WIDTH + margins.left() + margins.right()
        if layout.hasHeightForWidth():
            height = layout.totalHeightForWidth(width)
        else:
            height = layout.totalSizeHint().height()
        self.setFixedSize(self._WIDTH, height - margins.top() - margins.bottom())

    # ── Actions ───────────────────────────────────────────────────────────────

    def _toggle_expanded(self):
        """Déplie ou replie les traitements du groupe ; les cases gardent leur
        état (replié, le groupe lance toujours les traitements cochés)."""
        self._expanded = not self._expanded
        self._sub_box.setVisible(self._expanded)
        self._btn_expand.setText("▼" if self._expanded else "►")
        self._fit_height()

    def _checked_steps(self):
        return tuple(kind for kind in STEP_KINDS if self._step_checks[kind].isChecked())

    def _on_step_toggled(self, kind, on):
        """Case d'un traitement, cochée ou décochée : retenue pour la suite de
        la session, et le groupe devient le choix de la fenêtre."""
        if on:
            _session_checked.add(kind)
        else:
            _session_checked.discard(kind)
        self._radio_all.setChecked(True)
        self._update_ok()

    def _on_group_clicked(self):
        """Clic sur le groupe alors qu'il était déjà choisi : tous ses
        traitements sont recochés (premier clic depuis un autre choix : le
        groupe est seulement choisi, cases inchangées)."""
        if not self._all_was_checked:
            return
        for check in self._step_checks.values():
            check.setChecked(True)

    def _update_ok(self):
        """OK grisé quand le groupe est choisi sans aucune case cochée ;
        rond du groupe en sélection partielle quand une partie seulement de
        ses traitements est cochée."""
        checked = self._checked_steps()
        self._radio_all.set_partial(0 < len(checked) < len(STEP_KINDS))
        self._btn_ok.setEnabled(not (self._radio_all.isChecked() and not checked))

    def _on_ok(self):
        self.chosen = self._choices[self._btn_group.checkedButton()]
        self.steps = self._checked_steps() if self.chosen == "all" else ()
        if self.chosen == "all" and not self.steps:
            return
        self.accept()

    def _on_cancel(self):
        self.chosen = None
        self.accept()


# ═══════════════════════════════════════════════════════════════════════════════
# Fonction publique
# ═══════════════════════════════════════════════════════════════════════════════

def show_batch_drop_dialog(parent, dirs: list[str], callbacks: dict, from_menu=False):
    """
    Affiche la fenêtre de choix du traitement par lot des dossiers `dirs`.

    callbacks : 'batch_all' (appelé avec les types cochés), 'batch_metadata',
    'batch_library' (sans argument).
    """
    dlg = BatchDropDialog(parent, dirs, from_menu=from_menu)

    def _on_done():
        if dlg.chosen == "all":
            callbacks["batch_all"](dlg.steps)
        elif dlg.chosen:
            callbacks["batch_" + dlg.chosen]()

    dlg.finished.connect(lambda _: _on_done())
    dlg.show_nonmodal()
