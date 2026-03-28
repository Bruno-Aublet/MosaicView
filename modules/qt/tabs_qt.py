"""
modules/qt/tabs_qt.py
Barre d'onglets Qt pour MosaicView (remplace modules/tabs.py tkinter).

Deux onglets :
  - Onglet mosaïque : affiche le nom du fichier ouvert + bouton X fermeture
  - Onglet Métadonnées : affiché si state.comic_metadata est non vide

Utilise QWidget + QHBoxLayout (pas QTabWidget, pour correspondre au style tkinter).
"""

import os

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QScrollArea, QLabel,
    QFrame, QVBoxLayout, QSizePolicy, QStyle, QStyleOptionButton, QMenu,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPainter, QColor, QPen, QGuiApplication

from modules.qt import state as _state_module
from modules.qt.state import get_current_theme
from modules.qt.localization import _
from modules.qt.font_manager_qt import get_current_font as _get_current_font


class _TabButton(QPushButton):
    """QPushButton plat avec indicateur visuel de focus clavier (bordure)."""

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.hasFocus():
            painter = QPainter(self)
            painter.setPen(QPen(QColor("#888888"), 2))
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Left, Qt.Key_Right):
            tab_bar = self.parent()
            if isinstance(tab_bar, TabBar):
                tab_bar._navigate_horiz(self, -1 if key == Qt.Key_Left else 1)
            return
        if key in (Qt.Key_Up, Qt.Key_Down):
            return
        super().keyPressEvent(event)


class _CloseButton(QPushButton):
    """Bouton X avec navigation ←/→ dans la barre d'onglets."""

    def __init__(self):
        super().__init__("\u2715")
        self.setFlat(True)
        self.setStyleSheet(
            "QPushButton { background: #cc3333; color: white; border-radius: 3px; font-weight: bold; }"
            "QPushButton:hover { background: #ff4444; }"
        )

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.hasFocus():
            painter = QPainter(self)
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Left, Qt.Key_Right):
            tab_bar = self.parent()
            if isinstance(tab_bar, TabBar):
                tab_bar._navigate_horiz(self, -1 if key == Qt.Key_Left else 1)
            return
        if key in (Qt.Key_Up, Qt.Key_Down):
            return
        super().keyPressEvent(event)


class _SelectableLabel(QLabel):
    """QLabel sélectionnable avec menu contextuel traduit (Copier / Tout sélectionner)."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        has_selection = bool(self.selectedText())
        act_copy = menu.addAction(_("buttons.copy"))
        act_copy.setEnabled(has_selection)
        act_select_all = menu.addAction(_("menu.select_all"))
        chosen = menu.exec(event.globalPos())
        if chosen == act_copy:
            QGuiApplication.clipboard().setText(self.selectedText())
        elif chosen == act_select_all:
            self.setSelection(0, len(self.text()))


# ═══════════════════════════════════════════════════════════════════════════════
# Barre d'onglets
# ═══════════════════════════════════════════════════════════════════════════════
class TabBar(QWidget):
    """
    Barre fine au-dessus du canvas avec :
      - bouton nom de fichier (onglet mosaïque)
      - bouton X (fermeture)
      - bouton Métadonnées (si disponibles)
    Émet tab_changed("mosaic" | "info").
    """
    tab_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 0, 4, 0)
        self._layout.setSpacing(0)

        self._btn_mosaic   = None
        self._btn_close    = None
        self._btn_metadata = None
        self._current_tab  = "mosaic"

        self._close_callback = None
        self._state = None  # state du panneau propriétaire (évite d'utiliser le singleton)

        # Stretcher pour pousser les boutons à gauche
        self._layout.addStretch(1)

        from modules.qt.language_signal import language_signal
        self._on_language_changed_tab = lambda _: self._rebuild()
        language_signal.changed.connect(self._on_language_changed_tab)

    def cleanup(self):
        """Déconnecte les signaux globaux — à appeler avant destruction."""
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._on_language_changed_tab)
        except RuntimeError:
            pass

    # ── API publique ───────────────────────────────────────────────────────────
    def update(self, close_callback=None, state=None):
        """
        Reconstruit les onglets selon l'état courant.
        close_callback : appelé quand l'utilisateur clique sur X.
        state : AppState du panneau propriétaire (mémorisé pour _rebuild).
        """
        self._close_callback = close_callback or self._close_callback
        if state is not None:
            self._state = state
        self._rebuild()

    def current_tab(self) -> str:
        return self._current_tab

    def set_current_tab(self, tab: str):
        self._current_tab = tab
        self._rebuild()

    # ── Construction interne ──────────────────────────────────────────────────
    def _rebuild(self):
        # Supprime les anciens boutons
        for btn in [self._btn_mosaic, self._btn_close, self._btn_metadata]:
            if btn is not None:
                self._layout.removeWidget(btn)
                btn.deleteLater()
        self._btn_mosaic   = None
        self._btn_close    = None
        self._btn_metadata = None

        # Enlève le stretcher (sera remis à la fin)
        while self._layout.count():
            self._layout.takeAt(0)

        st = self._state if self._state is not None else _state_module.state
        if not st or (not st.current_file and not st.images_data):
            self._layout.addStretch(1)
            return

        # ── Onglet mosaïque ──
        display_name = os.path.basename(st.current_file) if st.current_file else ""
        if display_name:
            self._btn_mosaic = _TabButton(display_name)
            self._btn_mosaic.setFlat(True)
            self._btn_mosaic.setCursor(Qt.PointingHandCursor)
            self._btn_mosaic.setCheckable(True)
            self._btn_mosaic.setChecked(self._current_tab == "mosaic")
            self._btn_mosaic.clicked.connect(lambda: self._switch("mosaic"))
            style = self._tab_style(self._current_tab == "mosaic")
            self._btn_mosaic._base_style = style
            self._btn_mosaic.setStyleSheet(style)
            self._btn_mosaic.setMaximumWidth(200)
            elided = self._btn_mosaic.fontMetrics().elidedText(
                display_name, Qt.ElideRight,
                self._btn_mosaic.maximumWidth() - 16,  # 16 = padding gauche+droite
            )
            if elided != display_name:
                self._btn_mosaic.setText(elided)
                self._btn_mosaic.setToolTip(display_name)
            self._layout.addWidget(self._btn_mosaic)

            # Bouton fermeture
            self._btn_close = _CloseButton()
            self._btn_close.setCursor(Qt.PointingHandCursor)
            self._btn_close.setFixedSize(20, 20)
            if self._close_callback:
                self._btn_close.clicked.connect(self._close_callback)
            self._layout.addWidget(self._btn_close)

        # ── Onglet Métadonnées ──
        if st.comic_metadata:
            self._btn_metadata = _TabButton(_("tabs.metadata"))
            self._btn_metadata.setFlat(True)
            self._btn_metadata.setCursor(Qt.PointingHandCursor)
            self._btn_metadata.setCheckable(True)
            self._btn_metadata.setChecked(self._current_tab == "info")
            self._btn_metadata.clicked.connect(lambda: self._switch("info"))
            style = self._tab_style(self._current_tab == "info")
            self._btn_metadata._base_style = style
            self._btn_metadata.setStyleSheet(style)
            self._layout.addWidget(self._btn_metadata)

        self._layout.addStretch(1)

    def _navigate_horiz(self, current_btn, step: int):
        """Cycle ←/→ entre les boutons visibles de la barre d'onglets."""
        btns = [b for b in [self._btn_mosaic, self._btn_close, self._btn_metadata] if b is not None]
        if not btns:
            return
        try:
            idx = btns.index(current_btn)
        except ValueError:
            return
        btns[(idx + step) % len(btns)].setFocus()

    def _switch(self, tab: str):
        self._current_tab = tab
        self._rebuild()
        self.tab_changed.emit(tab)

    def apply_theme(self):
        """Met à jour les couleurs des onglets selon le thème courant."""
        self._rebuild()

    def _tab_style(self, active: bool) -> str:
        theme = get_current_theme()
        bg      = theme["bg"]
        toolbar = theme["toolbar_bg"]
        text    = theme["text"]
        # Onglet actif : fond clair/foncé selon thème, onglet inactif : toolbar_bg
        if active:
            return (f"QPushButton {{ background: {bg}; color: {text}; "
                    f"font-weight: bold; border: none; padding: 2px 8px; }}")
        else:
            hover = "#d0d0d0" if not _state_module.state.dark_mode else "#3a3a3a"
            return (f"QPushButton {{ background: {toolbar}; color: {text}; "
                    f"font-weight: normal; border: none; padding: 2px 8px; }}"
                    f"QPushButton:hover {{ background: {hover}; }}")


# ═══════════════════════════════════════════════════════════════════════════════
# Onglet Métadonnées
# ═══════════════════════════════════════════════════════════════════════════════
class MetadataTab(QScrollArea):
    """
    Affiche les métadonnées de state.comic_metadata dans une zone scrollable.

    Deux niveaux de mise à jour :
      - refresh()      : reconstruit tous les widgets (appelé uniquement quand les données changent)
      - _restyle()     : met à jour couleurs + polices + textes traduits sans recréer les widgets
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self._content = QWidget()
        self.setWidget(self._content)
        self._vlay = QVBoxLayout(self._content)
        self._vlay.setContentsMargins(20, 10, 20, 10)
        self._vlay.setSpacing(0)

        # Références conservées pour _restyle() — liste de (key, lbl_widget, txt_widget)
        self._field_widgets     = []   # [(key, QLabel_titre, _SelectableLabel_valeur), ...]
        self._toggle_btn        = None
        self._header_labels     = []   # [QLabel, ...] en-têtes colonnes Pages
        self._pages_count       = 0
        self._page_rows         = []   # [QWidget, ...] une ligne par page
        self._pages_content_lay = None # QVBoxLayout du contenu pages (pour ajout/suppression)

        from modules.qt.metadata_signal import metadata_signal
        metadata_signal.changed.connect(self.refresh)

        from modules.qt.language_signal import language_signal
        self._on_language_changed_meta = lambda _: self._restyle()
        language_signal.changed.connect(self._on_language_changed_meta)

    def cleanup(self):
        """Déconnecte les signaux globaux — à appeler avant destruction."""
        from modules.qt.metadata_signal import metadata_signal
        from modules.qt.language_signal import language_signal
        try:
            metadata_signal.changed.disconnect(self.refresh)
        except RuntimeError:
            pass
        try:
            language_signal.changed.disconnect(self._on_language_changed_meta)
        except RuntimeError:
            pass

        # Si des métadonnées sont déjà chargées (ex: fichier ouvert avant création du widget)
        st = _state_module.state
        if st and st.comic_metadata:
            self.refresh()

    def apply_theme(self):
        self._restyle()

    # ── Reconstruction complète (données changées) ────────────────────────────
    def refresh(self):
        # Vide tout
        self._field_widgets = []
        self._toggle_btn    = None
        self._header_labels = []
        self._pages_count   = 0

        while self._vlay.count():
            item = self._vlay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        st = _state_module.state
        if not st or not st.comic_metadata:
            self._restyle()
            return

        normal_font = _get_current_font(10)
        bold_font   = _get_current_font(10)
        bold_font.setBold(True)

        for key, value in st.comic_metadata.items():
            if key == 'pages':
                continue
            if not value or not str(value).strip():
                continue

            row = QWidget()
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(0, 6, 0, 6)
            row_lay.setSpacing(10)

            lbl = QLabel()
            lbl.setFont(bold_font)
            lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            lbl.setFixedWidth(160)
            row_lay.addWidget(lbl)

            txt = _SelectableLabel(str(value))
            txt.setFont(normal_font)
            txt.setWordWrap(True)
            txt.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            txt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            row_lay.addWidget(txt, 1)

            self._vlay.addWidget(row)
            self._field_widgets.append((key, lbl, txt))

            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            self._vlay.addWidget(sep)

        # Section <Page> collapsable
        pages = st.comic_metadata.get('pages')
        if pages:
            self._pages_count = len(pages)
            self._build_pages_section(pages, bold_font)

        self._vlay.addStretch(1)
        self._restyle()

    # ── Mise à jour légère (thème / langue / fonte) ───────────────────────────
    def _restyle(self):
        theme       = get_current_theme()
        bg          = theme["bg"]
        text        = theme["text"]
        normal_font = _get_current_font(10)
        bold_font   = _get_current_font(10)
        bold_font.setBold(True)

        self.setStyleSheet(f"QScrollArea {{ background: {bg}; border: none; }}")
        self._content.setStyleSheet(f"background: {bg}; color: {text};")

        for key, lbl, txt in self._field_widgets:
            lbl.setText(f"{_(f'metadata.{key}')} :")
            lbl.setFont(bold_font)
            txt.setFont(normal_font)

        if self._toggle_btn is not None:
            arrow = "▼" if self._toggle_btn.isChecked() else "▶"
            self._toggle_btn.setText(f"{arrow}  {_('metadata.pages')}  ({self._pages_count})")
            self._toggle_btn.setFont(bold_font)
            self._toggle_btn.setStyleSheet(
                f"QPushButton {{ text-align: left; padding: 6px 4px; "
                f"color: {text}; background: transparent; border: none; }}"
            )

        font_small = _get_current_font(9)
        font_small.setBold(True)
        col_keys = [
            "metadata.pages_col_image",
            "metadata.pages_col_type",
            "metadata.pages_col_width",
            "metadata.pages_col_height",
            "metadata.pages_col_size",
        ]
        for i, h in enumerate(self._header_labels):
            h.setText(_(col_keys[i]))
            h.setFont(font_small)

    # ── Construction de la section Pages (appelée une seule fois) ────────────
    def _build_pages_section(self, pages, bold_font):
        theme = get_current_theme()

        self._toggle_btn = QPushButton()
        self._toggle_btn.setFlat(True)
        self._toggle_btn.setFont(bold_font)
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setChecked(False)
        self._toggle_btn.setCursor(Qt.PointingHandCursor)

        content_widget = QWidget()
        content_lay    = QVBoxLayout(content_widget)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)
        content_widget.setVisible(False)
        self._pages_content_lay = content_lay
        self._page_rows = []

        # En-têtes
        header_row = QWidget()
        header_lay = QHBoxLayout(header_row)
        header_lay.setContentsMargins(0, 2, 0, 2)
        header_lay.setSpacing(4)
        font_small = _get_current_font(9)
        font_small.setBold(True)
        self._header_labels = []
        for col_w in [55, 120, 70, 70, 90]:
            h = QLabel()
            h.setFont(font_small)
            h.setFixedWidth(col_w)
            header_lay.addWidget(h)
            self._header_labels.append(h)
        header_lay.addStretch(1)
        content_lay.addWidget(header_row)

        sep0 = QFrame()
        sep0.setFrameShape(QFrame.HLine)
        content_lay.addWidget(sep0)

        font_row = _get_current_font(9)
        for page in pages:
            p_row = self._build_page_row(page, font_row)
            content_lay.addWidget(p_row)
            self._page_rows.append(p_row)

        toggle_btn = self._toggle_btn

        def _toggle(checked):
            content_widget.setVisible(checked)
            arrow = "▼" if checked else "▶"
            toggle_btn.setText(f"{arrow}  {_('metadata.pages')}  ({len(pages)})")

        self._toggle_btn.toggled.connect(_toggle)

        self._vlay.addWidget(self._toggle_btn)
        self._vlay.addWidget(content_widget)

        sep_end = QFrame()
        sep_end.setFrameShape(QFrame.HLine)
        self._vlay.addWidget(sep_end)

    def _build_page_row(self, page, font_row):
        """Construit et retourne un QWidget représentant une ligne de page."""
        p_row = QWidget()
        p_lay = QHBoxLayout(p_row)
        p_lay.setContentsMargins(0, 1, 0, 1)
        p_lay.setSpacing(4)
        for val, col_w in [
            (page.get('Image', ''), 55),
            (page.get('Type', ''), 120),
            (page.get('ImageWidth', ''), 70),
            (page.get('ImageHeight', ''), 70),
            (page.get('ImageSize', ''), 90),
        ]:
            lbl = QLabel(str(val))
            lbl.setFont(font_row)
            lbl.setFixedWidth(col_w)
            p_lay.addWidget(lbl)
        p_lay.addStretch(1)
        return p_row

    def update_pages(self, pages):
        """Mise à jour incrémentale de la section Pages — ajoute/supprime des lignes
        sans reconstruire l'ensemble. Appelé lors d'un D&D inter-panneaux."""
        if self._pages_content_lay is None or self._toggle_btn is None:
            # Section pas encore construite → refresh complet
            self.refresh()
            return

        font_row = _get_current_font(9)
        new_count = len(pages)
        old_count = len(self._page_rows)

        if new_count > old_count:
            # Ajouter les lignes manquantes à la fin
            for i in range(old_count, new_count):
                p_row = self._build_page_row(pages[i], font_row)
                self._pages_content_lay.addWidget(p_row)
                self._page_rows.append(p_row)
        elif new_count < old_count:
            # Supprimer les lignes en trop à la fin
            for _i in range(old_count - new_count):
                row = self._page_rows.pop()
                self._pages_content_lay.removeWidget(row)
                row.deleteLater()

        # Mettre à jour les numéros Image dans les lignes existantes
        for i, page in enumerate(pages):
            row = self._page_rows[i]
            labels = [row.layout().itemAt(j).widget() for j in range(5)]
            for lbl, val in zip(labels, [
                page.get('Image', ''), page.get('Type', ''),
                page.get('ImageWidth', ''), page.get('ImageHeight', ''),
                page.get('ImageSize', ''),
            ]):
                if lbl and isinstance(lbl, QLabel):
                    lbl.setText(str(val))

        # Mettre à jour le compteur dans le bouton toggle
        self._pages_count = new_count
        arrow = "▼" if self._toggle_btn.isChecked() else "▶"
        self._toggle_btn.setText(f"{arrow}  {_('metadata.pages')}  ({new_count})")

        # Mettre à jour le champ page_count dans _field_widgets
        for key, lbl, txt in self._field_widgets:
            if key == 'page_count':
                txt.setText(str(new_count))
                break
