"""
modules/qt/tabs_qt.py
Barre d'onglets de MosaicView.

Deux onglets :
  - Onglet mosaïque : affiche le nom du fichier ouvert (« BD sans titre » pour
    des images sans fichier ouvert) + bouton X fermeture
  - Onglet Métadonnées : affiché si state.comic_metadata est non vide

Utilise QWidget + QHBoxLayout (pas QTabWidget), pour un style d'onglet spécifique.
"""

import os

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QScrollArea, QLabel,
    QFrame, QVBoxLayout, QSizePolicy, QMenu,
    QTableView, QAbstractItemView, QComboBox, QStyledItemDelegate,
)
from PySide6.QtCore import (Qt, Signal, QThread, QAbstractTableModel, QModelIndex,
                            QItemSelectionModel, QTimer)
from PySide6.QtGui import QPainter, QColor, QPen, QGuiApplication, QFont

from modules.qt import state as _state_module
from modules.qt.state import get_current_theme
from modules.qt.localization import _
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.tooltips_qt import page_type_label
from modules.qt.utils import format_file_size


class _TabButton(QPushButton):
    """QPushButton plat avec indicateur visuel de focus clavier (bordure)."""

    _full_text = None  # nom complet pour elision dynamique

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        return hint.__class__(0, hint.height())

    def sizeHint(self):
        hint = super().sizeHint()
        if self._full_text is not None:
            w = self.fontMetrics().horizontalAdvance(self._full_text) + 16
            return hint.__class__(w, hint.height())
        return hint

    def _update_elision(self):
        if self._full_text is not None:
            elided = self.fontMetrics().elidedText(
                self._full_text, Qt.ElideRight, max(0, self.width() - 16)
            )
            self.setText(elided)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_elision()

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
    """QLabel sélectionnable avec menu contextuel traduit (Copier / Tout sélectionner).

    Texte brut imposé avant setText : il affiche des métadonnées venues d'un
    fichier externe. En détection automatique, un texte commençant par une
    balise serait rendu en HTML, et un <img src="\\\\serveur\\..."> ferait
    contacter ce serveur par Windows (fuite du hash NTLM) au simple affichage."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.PlainText)
        self.setText(text)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        has_selection = bool(self.selectedText())
        act_copy = menu.addAction(_("buttons.copy"))
        act_copy.setEnabled(has_selection)
        act_select_all = menu.addAction(_("menu.select_all"))
        chosen = menu.exec(event.globalPos())
        menu.deleteLater()
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

    def __init__(self, parent=None, tooltip_parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self.setMinimumWidth(0)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 0, 4, 0)
        self._layout.setSpacing(0)

        self._btn_mosaic   = None
        self._btn_close    = None
        self._btn_metadata = None
        self._current_tab  = "mosaic"

        self._close_callback = None
        self._state = None  # state du panneau propriétaire (évite d'utiliser le singleton)

        from modules.qt.overlay_tooltip_qt import OverlayTooltip
        # tooltip_parent doit être un widget plus grand que TabBar (22px) pour que l'overlay soit visible
        self._overlay_tip = OverlayTooltip(tooltip_parent if tooltip_parent is not None else self)

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
        if self._btn_mosaic is not None:
            self._overlay_tip.untrack(self._btn_mosaic)
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

        # Onglet Métadonnées affiché alors que ses métadonnées ont disparu
        # (ComicInfo.xml supprimé, déplacé, Annuler…) : retour à la mosaïque,
        # sinon le contenu resterait sur un onglet dont le bouton n'existe plus.
        from modules.qt.comic_info import shows_metadata_tab
        back_to_mosaic = self._current_tab == "info" and not (st and shows_metadata_tab(st))
        if back_to_mosaic:
            self._current_tab = "mosaic"

        if not st or (not st.current_file and not st.images_data):
            self._layout.addStretch(1)
            if back_to_mosaic:
                self.tab_changed.emit("mosaic")
            return

        # ── Onglet mosaïque ──
        # Présent dès qu'il y a un document : sans fichier ouvert (images
        # isolées), « BD sans titre », pour pouvoir revenir à la mosaïque depuis
        # l'onglet Métadonnées et fermer le document par sa croix.
        if st.current_file:
            display_name = os.path.basename(st.current_file)
        else:
            display_name = _("tabs.untitled_comic")
        if display_name:
            self._btn_mosaic = _TabButton(display_name)
            if not st.current_file:
                # Texte traduit (pas un nom de fichier) : police de la langue courante
                self._btn_mosaic.setFont(_get_current_font(10))
            self._btn_mosaic.setFlat(True)
            self._btn_mosaic.setCursor(Qt.PointingHandCursor)
            self._btn_mosaic.setCheckable(True)
            self._btn_mosaic.setChecked(self._current_tab == "mosaic")
            self._btn_mosaic.clicked.connect(lambda: self._switch("mosaic"))
            style = self._tab_style(self._current_tab == "mosaic")
            self._btn_mosaic._base_style = style
            self._btn_mosaic.setStyleSheet(style)
            self._btn_mosaic.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._btn_mosaic.setMinimumWidth(0)
            self._btn_mosaic._full_text = display_name
            self._btn_mosaic.setMouseTracking(True)
            self._overlay_tip.track(self._btn_mosaic, display_name)
            self._layout.addWidget(self._btn_mosaic)

            # Bouton fermeture
            self._btn_close = _CloseButton()
            self._btn_close.setCursor(Qt.PointingHandCursor)
            self._btn_close.setFixedSize(20, 20)
            if self._close_callback:
                self._btn_close.clicked.connect(self._close_callback)
            self._layout.addWidget(self._btn_close)

        # ── Onglet Métadonnées ── (aussi pour un ComicInfo.xml illisible, qu'il explique)
        if shows_metadata_tab(st):
            self._btn_metadata = _TabButton(_("tabs.metadata"))
            self._btn_metadata.setFont(_get_current_font(10))
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
        if back_to_mosaic:
            self.tab_changed.emit("mosaic")

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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._btn_mosaic is not None:
            self._btn_mosaic._update_elision()

    def apply_theme(self):
        """Met à jour les couleurs des onglets selon le thème courant."""
        self._overlay_tip._apply_style()
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
            st = self._state if self._state is not None else _state_module.state
            hover = "#d0d0d0" if not st.dark_mode else "#3a3a3a"
            return (f"QPushButton {{ background: {toolbar}; color: {text}; "
                    f"font-weight: normal; border: none; padding: 2px 8px; }}"
                    f"QPushButton:hover {{ background: {hover}; }}")


# ═══════════════════════════════════════════════════════════════════════════════
# Modèle table pur Python pour l'onglet Pages
# ═══════════════════════════════════════════════════════════════════════════════
class _PagesTableModel(QAbstractTableModel):
    """Modèle du tableau Pages — données pures, UN SEUL wrapper Qt.

    NE PAS revenir à QStandardItemModel ici : des centaines de QStandardItem
    wrappés Python, créés puis détruits à chaque rafraîchissement de l'onglet
    (chaque suppression/undo/redo), corrompent la table de bindings de shiboken
    quand ça s'entrelace avec le churn d'items de la mosaïque → access violation
    différée dans BindingManager::releaseWrapper (shiboken 6.10/6.11).
    rows : [[(valeur, clé_de_tri|None) × 6], ...] (préparées par _PagesModelBuilder,
    colonnes _PAGES_COLUMNS). La colonne Type garde la valeur brute de la norme
    (FrontCover…), traduite à l'affichage : elle suit un changement de langue.
    La cellule Type est modifiable (liste déroulante, _PageTypeDelegate) : le
    choix n'est pas écrit dans les lignes, il est demandé par type_chosen et
    revient avec la reconstruction du tableau.
    """

    type_chosen = Signal(int, str)  # (position <Page Image="N">, type ; "" = aucun)

    def __init__(self, rows, headers, parent=None):
        super().__init__(parent)
        self._rows = rows
        self._headers = list(headers)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._headers)

    def image_index(self, row):
        """Position <Page Image="N"> de la ligne (le tableau peut être trié)."""
        return self._rows[row][_COL_IMAGE][1]

    def _display(self, row, column):
        value = self._rows[row][column][0]
        if column == _COL_TYPE:
            return page_type_label(value)
        if column == _COL_DOUBLE:
            return "✓" if value else ""
        if column == _COL_SIZE and isinstance(value, int):
            return format_file_size(value)
        return str(value)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.DisplayRole:
            return self._display(index.row(), index.column())
        if role == Qt.EditRole:
            return self._rows[index.row()][index.column()][0]
        if role == Qt.UserRole:
            return self._rows[index.row()][index.column()][1]
        if role == Qt.TextAlignmentRole and index.column() == _COL_DOUBLE:
            return int(Qt.AlignCenter)
        if role == Qt.FontRole:
            # Évalué à chaque paint : suit la police courante (langues CSUR
            # klingon/tengwar incluses) sans dépendre d'un setFont statique.
            return _get_current_font(9)
        return None

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and index.column() == _COL_TYPE:
            flags |= Qt.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid() or index.column() != _COL_TYPE:
            return False
        # Émis même si la ligne a déjà ce type : les autres lignes
        # sélectionnées le reçoivent aussi (set_page_marks ne change que
        # les pages qui ne l'ont pas)
        self.type_chosen.emit(self.image_index(index.row()), value or "")
        return True

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole and 0 <= section < len(self._headers):
                return self._headers[section]
            if role == Qt.FontRole:
                return _get_current_font(9)
        return None

    def set_headers(self, headers):
        """Retraduction des en-têtes à la volée (appelé par _restyle)."""
        self._headers = list(headers)
        if self._headers:
            self.headerDataChanged.emit(Qt.Horizontal, 0, len(self._headers) - 1)

    def sort(self, column, order=Qt.AscendingOrder):
        self.layoutAboutToBeChanged.emit()

        def _key(row):
            sort_val = row[column][1]
            if sort_val is not None:
                return sort_val
            if column == _COL_TYPE:
                return page_type_label(row[column][0])
            return str(row[column][0])

        self._rows.sort(key=_key, reverse=(order == Qt.DescendingOrder))
        self.layoutChanged.emit()


# Colonnes du tableau Pages : clés de traduction des en-têtes et largeurs
_PAGES_COLUMNS = (
    ("metadata.pages_col_image",  55),
    ("metadata.pages_col_type",   120),
    ("metadata.pages_col_double", 90),
    ("metadata.pages_col_width",  70),
    ("metadata.pages_col_height", 70),
    ("metadata.pages_col_size",   90),
)
_COL_IMAGE, _COL_TYPE, _COL_DOUBLE, _COL_SIZE = 0, 1, 2, 5


def _pages_headers():
    return [_(key) for key, _width in _PAGES_COLUMNS]


class _PageTypeCombo(QComboBox):
    """Liste déroulante de la cellule Type : signale la fermeture de sa liste
    (choix, Échap ou clic ailleurs), pour que la cellule ne reste pas en
    édition sans liste ouverte."""

    popup_closed = Signal()

    def hidePopup(self):
        super().hidePopup()
        self.popup_closed.emit()


class _PageTypeDelegate(QStyledItemDelegate):
    """Éditeur de la cellule Type : liste des types de la norme, ouverte dès
    le clic, choix appliqué aussitôt (setData → type_chosen)."""

    def createEditor(self, parent, option, index):
        from modules.qt.comic_info import PAGE_TYPES
        theme = get_current_theme()
        combo = _PageTypeCombo(parent)
        combo.setFont(_get_current_font(9))
        combo.setStyleSheet(
            f"QComboBox {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 0px 4px; }} "
            f"QComboBox QAbstractItemView {{ background: {theme['toolbar_bg']}; "
            f"color: {theme['text']}; selection-background-color: {theme['icon_hover']}; "
            f"selection-color: {theme['text']}; }}")
        combo.addItem(_("comicinfo.combo_empty"), "")
        for page_type in PAGE_TYPES:
            combo.addItem(_(f"metadata.page_types.{page_type}"), page_type)
        current = index.data(Qt.EditRole) or ""
        if current and combo.findData(current) < 0:
            # Valeur hors de la norme : proposée telle quelle, gardée si rechoisie
            combo.insertItem(1, page_type_label(current), current)
        combo.setMaxVisibleItems(combo.count())
        combo._mv_done = False

        def _choose(_index):
            if combo._mv_done:
                return
            combo._mv_done = True
            self.commitData.emit(combo)
            self.closeEditor.emit(combo, QStyledItemDelegate.NoHint)

        def _popup_closed():
            # Différé : à un choix, la liste se ferme avant l'émission
            # d'activated, qui doit encore trouver l'éditeur ouvert
            def _close():
                if not combo._mv_done:
                    combo._mv_done = True
                    self.closeEditor.emit(combo, QStyledItemDelegate.NoHint)
            QTimer.singleShot(0, combo, _close)

        combo.activated.connect(_choose)
        combo.popup_closed.connect(_popup_closed)
        QTimer.singleShot(0, combo, combo.showPopup)
        return combo

    def setEditorData(self, editor, index):
        position = editor.findData(index.data(Qt.EditRole) or "")
        editor.setCurrentIndex(max(position, 0))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentData() or "", Qt.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class _PagesTableView(QTableView):
    """Tableau Pages : sélection de plusieurs lignes (clic, Ctrl+clic,
    Maj+clic, Ctrl+A). Un clic simple sur la cellule Type ou Double page
    d'une ligne déjà sélectionnée garde la sélection : le changement vaut
    pour toutes ses lignes. Sur une autre ligne, il la sélectionne seule.
    can_edit() dit si les pages peuvent changer (opération en cours sur le
    document : non)."""

    double_page_clicked = Signal(int)          # ligne cliquée
    menu_requested = Signal(object)            # position globale (QPoint)

    def __init__(self, can_edit, parent=None):
        super().__init__(parent)
        self._can_edit = can_edit

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        plain_click = (event.button() == Qt.LeftButton
                       and event.modifiers() == Qt.NoModifier
                       and index.isValid()
                       and index.column() in (_COL_TYPE, _COL_DOUBLE)
                       and self._can_edit())
        if not plain_click:
            super().mousePressEvent(event)
            return
        if not self.selectionModel().isRowSelected(index.row(), QModelIndex()):
            super().mousePressEvent(event)
        else:
            # Sans l'appui transmis à Qt, son relâchement ne doit pas l'être
            # non plus : il réduirait la sélection à cette ligne
            self.selectionModel().setCurrentIndex(index, QItemSelectionModel.NoUpdate)
            self._swallow_release = True
            event.accept()
        if index.column() == _COL_TYPE:
            self.edit(index)
        else:
            self.double_page_clicked.emit(index.row())

    def mouseReleaseEvent(self, event):
        if getattr(self, "_swallow_release", False):
            self._swallow_release = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        index = self.indexAt(event.pos())
        if not index.isValid():
            return
        if not self.selectionModel().isRowSelected(index.row(), QModelIndex()):
            self.selectRow(index.row())
        self.menu_requested.emit(event.globalPos())
        event.accept()


# ═══════════════════════════════════════════════════════════════════════════════
# Thread de construction du modèle Pages
# ═══════════════════════════════════════════════════════════════════════════════
def _page_attr_int(value):
    """Valeur de tri entière d'un attribut <Page> (Image, ImageWidth…), 0 si
    elle n'est pas un entier. Le ComicInfo.xml vient du fichier ouvert : une
    valeur « abc » ou « 1.5 » lèverait une exception dans le thread, qui
    s'arrêterait sans jamais remplir le tableau."""
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return 0


class _PagesModelBuilder(QThread):
    done = Signal(object)  # émet une liste de lignes [(valeur, sort_val|None) × 6]

    # NE JAMAIS créer de QStandardItemModel/QStandardItem dans ce thread : les
    # wrappers Qt créés hors du thread principal, puis détruits sur le thread
    # principal pendant que celui-ci crée/détruit massivement d'autres objets Qt
    # (render_mosaic), corrompent la table de bindings de shiboken → access
    # violation différée dans BindingManager::releaseWrapper (crash constaté sur
    # suppressions de doublons + undo/redo, shiboken 6.10/6.11, juillet 2026).
    # Ce thread ne prépare que des données pures (str/int) ; les objets Qt sont
    # construits dans _on_pages_model_ready, sur le thread principal.

    def __init__(self, pages):
        super().__init__()
        self._pages     = pages
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        self.wait()

    def run(self):
        from modules.qt.comic_info import is_double_page
        rows = []
        for page in self._pages:
            if self._cancelled:
                return
            # Poids gardé en octets, écrit à l'affichage (_PagesTableModel._display) :
            # il suit un changement de langue. Valeur non entière : montrée telle quelle.
            size_raw = page.get('ImageSize', '')
            try:
                size_int = int(size_raw) if size_raw else None
            except (ValueError, TypeError):
                size_int = None
            size_value = size_int if size_int is not None else size_raw

            double = is_double_page(page)
            rows.append([
                (page.get('Image', ''),       _page_attr_int(page.get('Image'))),
                (page.get('Type', ''),        None),
                (double,                      int(double)),
                (page.get('ImageWidth', ''),  _page_attr_int(page.get('ImageWidth'))),
                (page.get('ImageHeight', ''), _page_attr_int(page.get('ImageHeight'))),
                (size_value,                  size_int if size_int is not None else 0),
            ])
        if not self._cancelled:
            self.done.emit(rows)


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

        self._state = None  # state du panneau propriétaire

        # Références conservées pour _restyle() — liste de (key, lbl_widget, txt_widget)
        self._field_widgets  = []   # [(key, QLabel_titre, _SelectableLabel_valeur), ...]
        self._toggle_btn     = None
        self._edit_btn       = None
        self._check_updates_btn = None
        self._unreadable_lbl = None  # message « ComicInfo.xml illisible » ou « réparé »
        self._unreadable_key = "tabs.comicinfo_unreadable"
        self._pages_count    = 0
        self._pages_table    = None  # _PagesTableView
        self._pages_builder  = None  # _PagesModelBuilder en cours
        self._pages_sort     = None  # (colonne, ordre) choisis dans l'en-tête du tableau

        from modules.qt.metadata_signal import metadata_signal, metadata_pages_signal
        metadata_signal.changed.connect(self._on_metadata_changed)
        metadata_pages_signal.changed.connect(self._on_metadata_pages_changed)

        from modules.qt.language_signal import language_signal
        self._on_language_changed_meta = lambda _: self._restyle()
        language_signal.changed.connect(self._on_language_changed_meta)

    def _concerns_this_panel(self, st) -> bool:
        """Les signaux de métadonnées sont globaux : une émission portant sur
        le state de l'autre panneau (split-view) ne doit pas reconstruire cet
        onglet. st=None = émetteur sans panneau identifiable → accepté."""
        return st is None or self._state is None or st is self._state

    def _on_metadata_changed(self, st=None):
        if self._concerns_this_panel(st):
            self.refresh()

    def _on_metadata_pages_changed(self, st=None):
        if self._concerns_this_panel(st):
            self.refresh_pages_only()

    def cleanup(self):
        """Déconnecte les signaux globaux — à appeler avant destruction."""
        from modules.qt.metadata_signal import metadata_signal, metadata_pages_signal
        from modules.qt.language_signal import language_signal
        try:
            metadata_signal.changed.disconnect(self._on_metadata_changed)
        except RuntimeError:
            pass
        try:
            metadata_pages_signal.changed.disconnect(self._on_metadata_pages_changed)
        except RuntimeError:
            pass
        try:
            language_signal.changed.disconnect(self._on_language_changed_meta)
        except RuntimeError:
            pass

    def keyPressEvent(self, event):
        key = event.key()
        vbar = self.verticalScrollBar()
        if key == Qt.Key_Home:
            vbar.setValue(vbar.minimum())
        elif key == Qt.Key_End:
            vbar.setValue(vbar.maximum())
        else:
            super().keyPressEvent(event)

    def apply_theme(self):
        self._restyle()

    # ── Reconstruction complète (données changées) ────────────────────────────
    def refresh(self):
        # Vide tout
        self._field_widgets = []
        self._toggle_btn    = None
        self._edit_btn      = None
        self._check_updates_btn = None
        self._unreadable_lbl = None
        self._pages_count   = 0
        self._pages_table   = None
        self._pages_sort    = None
        self._drop_pages_builder()

        while self._vlay.count():
            item = self._vlay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        st = self._state or _state_module.state
        if not st or not st.comic_metadata:
            # ComicInfo.xml présent mais illisible : l'onglet l'explique et
            # garde le bouton Modifier (formulaire avec son avertissement)
            from modules.qt.comic_info import comic_info_unreadable
            if st and comic_info_unreadable(st):
                self._add_edit_button_row(_get_current_font(10))
                lbl = QLabel()
                lbl.setTextFormat(Qt.PlainText)
                lbl.setWordWrap(True)
                lbl.setAlignment(Qt.AlignCenter)
                self._vlay.addWidget(lbl)
                self._unreadable_lbl = lbl
                self._unreadable_key = "tabs.comicinfo_unreadable"
                self._vlay.addStretch(1)
            self._restyle()
            return

        normal_font = _get_current_font(10)
        bold_font   = _get_current_font(10)
        bold_font.setBold(True)

        btn_lay = self._add_edit_button_row(normal_font)

        # ComicInfo.xml abîmé, lu dans sa version réparée : même bandeau que
        # pour un fichier illisible, avec son propre texte
        from modules.qt.comic_info import comic_info_repaired
        if comic_info_repaired(st):
            lbl = QLabel()
            lbl.setTextFormat(Qt.PlainText)
            lbl.setWordWrap(True)
            lbl.setAlignment(Qt.AlignCenter)
            self._vlay.addWidget(lbl)
            self._unreadable_lbl = lbl
            self._unreadable_key = "tabs.comicinfo_repaired"

        # Bouton "Vérifier les mises à jour" — visible seulement si une URL
        # ComicVine d'issue est détectée dans les métadonnées locales.
        from modules.qt.comic_info import get_source_comicvine_issue_id
        self._check_updates_btn = None
        if get_source_comicvine_issue_id(st.comic_metadata):
            check_btn = QPushButton(_("comicvine_update.btn_check"))
            check_btn.setFont(normal_font)
            def _on_check_click():
                p = self.parent()
                while p is not None:
                    if hasattr(p, '_check_comicvine_updates'):
                        p._check_comicvine_updates(busy_widget=check_btn)
                        return
                    p = p.parent()
            check_btn.clicked.connect(_on_check_click)
            btn_lay.insertWidget(btn_lay.count() - 1, check_btn)
            self._check_updates_btn = check_btn

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

            if key == 'web':
                # Lien cliquable (URL web réelle) plutôt qu'un simple texte
                # sélectionnable, cf. feedback_securite_urls_externes.md :
                # setOpenExternalLinks(False) + open_url filtre le schéma.
                link_color = get_current_theme().get("link", "#4A9EFF")
                # Valeur échappée aussi dans l'attribut href : un guillemet
                # fermerait l'attribut et la suite serait du HTML interprété
                # (balise <img> chargée à l'affichage, voir _SelectableLabel).
                import html as _html
                escaped_value = _html.escape(str(value), quote=True)
                txt = QLabel(f'<a href="{escaped_value}" style="color:{link_color};">{escaped_value}</a>')
                txt.setOpenExternalLinks(False)
                from modules.qt.utils import open_url as _open_url_fn
                txt.linkActivated.connect(_open_url_fn)
                txt.setTextInteractionFlags(Qt.TextBrowserInteraction)
                from modules.qt.utils import setup_link_label_context_menu
                setup_link_label_context_menu(txt, lambda v=value: v)
            else:
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

        pages = st.comic_metadata.get('pages')
        if pages:
            self._pages_count = len(pages)
            self._build_pages_section(pages, bold_font)
        else:
            self._vlay.addStretch(1)
            self._restyle()

    def _add_edit_button_row(self, font):
        """Ligne de boutons en haut de l'onglet, avec « Modifier le fichier
        ComicInfo.xml » (_edit_comicinfo du panneau) ; rend son layout, dont
        le dernier élément est le ressort de droite (insérer avant)."""
        edit_btn = QPushButton(_("comicvine.edit_comicinfo"))
        edit_btn.setFont(font)
        def _on_edit_click():
            p = self.parent()
            while p is not None:
                if hasattr(p, '_edit_comicinfo'):
                    p._edit_comicinfo()
                    return
                p = p.parent()
        edit_btn.clicked.connect(_on_edit_click)
        btn_row = QWidget()
        btn_lay = QHBoxLayout(btn_row)
        btn_lay.setContentsMargins(0, 4, 0, 8)
        btn_lay.addStretch()
        btn_lay.addWidget(edit_btn)
        btn_lay.addStretch()
        self._edit_btn = edit_btn
        self._vlay.addWidget(btn_row)
        return btn_lay

    def refresh_pages_only(self):
        """Mise à jour légère : remet à jour uniquement le tableau Pages sans reconstruire tout le panneau."""
        st = self._state or _state_module.state
        if not st or not st.comic_metadata:
            return
        pages = st.comic_metadata.get('pages')
        if not pages:
            return
        if self._toggle_btn is None:
            self.refresh()
            return
        self.update_pages(pages)

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

        if self._edit_btn is not None:
            self._edit_btn.setText(_("comicvine.edit_comicinfo"))
            self._edit_btn.setFont(normal_font)
            self._edit_btn.setStyleSheet(
                f"QPushButton {{ background: {theme['toolbar_bg']}; color: {text}; "
                f"border: 1px solid #aaaaaa; padding: 4px 10px; }} "
                f"QPushButton:hover {{ background: {theme['separator']}; }}"
            )

        if self._unreadable_lbl is not None:
            # Avertissement non bloquant : couleur du texte, gras italique
            warn_font = _get_current_font(10)
            warn_font.setBold(True)
            warn_font.setItalic(True)
            self._unreadable_lbl.setText(_(self._unreadable_key))
            self._unreadable_lbl.setFont(warn_font)
            self._unreadable_lbl.setStyleSheet(f"color: {text}; padding: 12px;")

        if self._check_updates_btn is not None:
            checking = self._check_updates_btn.property("is_checking_updates")
            self._check_updates_btn.setText(
                _("comicvine_update.checking") if checking else _("comicvine_update.btn_check"))
            self._check_updates_btn.setFont(normal_font)
            self._check_updates_btn.setStyleSheet(
                f"QPushButton {{ background: {theme['toolbar_bg']}; color: {text}; "
                f"border: 1px solid #aaaaaa; padding: 4px 10px; }} "
                f"QPushButton:hover {{ background: {theme['separator']}; }}"
            )

        _st = self._state or _state_module.state
        for key, lbl, txt in self._field_widgets:
            lbl.setText(f"{_(f'metadata.{key}')} :")
            lbl.setFont(bold_font)
            txt.setFont(normal_font)
            if key == 'web':
                web_url = _st.comic_metadata.get('web', '') if _st and _st.comic_metadata else ''
                if web_url:
                    link_color = theme.get("link", "#4A9EFF")
                    # Échappée dans le texte ET dans href, comme dans refresh :
                    # un guillemet fermerait l'attribut, et la suite serait du
                    # HTML interprété (balise <img> chargée à l'affichage)
                    import html as _html
                    escaped_url = _html.escape(str(web_url), quote=True)
                    txt.setText(f'<a href="{escaped_url}" style="color:{link_color};">{escaped_url}</a>')

        if self._toggle_btn is not None:
            arrow = "▼" if self._toggle_btn.isChecked() else "▶"
            self._toggle_btn.setText(f"{arrow}  {_('metadata.pages')}  ({self._pages_count})")
            self._toggle_btn.setFont(bold_font)
            self._toggle_btn.setStyleSheet(
                f"QPushButton {{ text-align: left; padding: 6px 4px; "
                f"color: {text}; background: transparent; border: none; }}"
            )

        if self._pages_table is not None:
            self._pages_table.setStyleSheet(
                f"QTableView {{ background: {bg}; color: {text}; "
                f"gridline-color: {theme.get('separator', '#cccccc')}; "
                f"border: none; border-left: 1px solid {theme.get('separator', '#cccccc')}; }}"
                f"QHeaderView::section {{ background: {theme.get('toolbar_bg', bg)}; "
                f"color: {text}; border: none; "
                f"border-top: 1px solid {theme.get('separator', '#cccccc')}; "
                f"border-right: 1px solid {theme.get('separator', '#cccccc')}; "
                f"border-bottom: 1px solid {theme.get('separator', '#cccccc')}; padding: 2px; }}"
                f"QHeaderView::section:first {{ "
                f"border-left: 1px solid {theme.get('separator', '#cccccc')}; }}"
                f"QTableView::item:selected {{ background: {theme['icon_hover']}; "
                f"color: {text}; }}"
            )
            # Police du tableau et des en-têtes réappliquée systématiquement
            # (règle projet : jamais un setFont unique à la création pour un
            # widget dont le texte change de langue — polices CSUR illisibles sinon)
            self._pages_table.setFont(_get_current_font(9))
            self._pages_table.horizontalHeader().setFont(_get_current_font(9))
            model = self._pages_table.model()
            if isinstance(model, _PagesTableModel):
                model.set_headers(_pages_headers())
            # Types de page traduits à l'affichage (_PagesTableModel._display)
            self._pages_table.viewport().update()

    # ── Construction de la section Pages avec QTableView ─────────────────────
    def _build_pages_section(self, pages, bold_font):
        self._toggle_btn = QPushButton()
        self._toggle_btn.setFlat(True)
        self._toggle_btn.setFont(bold_font)
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setChecked(False)
        self._toggle_btn.setCursor(Qt.PointingHandCursor)

        table = _PagesTableView(self._page_types_editable)
        table.setVisible(False)
        # Cellule Type ouverte par _PagesTableView (clic simple), jamais par Qt
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setItemDelegateForColumn(_COL_TYPE, _PageTypeDelegate(table))
        table.double_page_clicked.connect(self._on_double_page_clicked)
        table.menu_requested.connect(self._show_page_type_menu)
        table.horizontalHeader().sortIndicatorChanged.connect(self._remember_pages_sort)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.setShowGrid(True)
        table.setFont(_get_current_font(9))
        table.horizontalHeader().setFont(_get_current_font(9))
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        table.setSortingEnabled(True)
        for i, (_key, width) in enumerate(_PAGES_COLUMNS):
            table.setColumnWidth(i, width)
        self._pages_table = table

        toggle_btn = self._toggle_btn

        def _toggle(checked):
            table.setVisible(checked)
            arrow = "▼" if checked else "▶"
            toggle_btn.setText(f"{arrow}  {_('metadata.pages')}  ({len(pages)})")

        self._toggle_btn.toggled.connect(_toggle)

        self._vlay.addWidget(self._toggle_btn)
        self._vlay.addWidget(table)

        sep_end = QFrame()
        sep_end.setFrameShape(QFrame.HLine)
        self._vlay.addWidget(sep_end)
        # Lignes tassées vers le haut : sans ce ressort, Qt répartit la hauteur
        # libre entre les lignes de champs, tableau replié et champs peu nombreux.
        self._vlay.addStretch(1)

        # Construction du modèle dans un thread séparé
        self._pages_builder = _PagesModelBuilder(pages)
        self._pages_builder.done.connect(self._on_pages_model_ready)
        self._pages_builder.start()

    def _on_pages_model_ready(self, rows):
        """Reçu depuis le thread builder — construit le modèle depuis les données
        pures et l'assigne au tableau (voir commentaire de _PagesTableModel :
        aucun QStandardItem, un seul wrapper Qt pour tout le tableau)."""
        # Résultat d'un builder remplacé (update_pages, refresh) : émis juste
        # avant son annulation, il est livré quand même. Il afficherait des
        # pages périmées, et le wait()/deleteLater ci-dessous viserait le
        # builder courant, encore en cours. Le builder remplacé a déjà été
        # libéré par _drop_pages_builder.
        sender = self.sender()
        if sender is None or sender is not self._pages_builder:
            return
        if self._pages_table is not None:
            table = self._pages_table
            old_model = table.model()
            # Largeurs (réglées par l'utilisateur ou initiales) et lignes
            # sélectionnées, repérées par leur page : setModel les efface
            if old_model is not None:
                widths = [table.columnWidth(i) for i in range(len(_PAGES_COLUMNS))]
                selected = {old_model.image_index(i.row())
                            for i in table.selectionModel().selectedRows()}
            else:
                widths = [width for _key, width in _PAGES_COLUMNS]
                selected = set()
            model = _PagesTableModel(rows, _pages_headers(), parent=table)
            model.type_chosen.connect(self._on_page_type_chosen, Qt.QueuedConnection)
            old_selection = table.selectionModel()
            table.setModel(model)
            if old_selection is not None:
                # Créé par la vue pour l'ancien modèle, il ne lui sert plus
                old_selection.deleteLater()
            if old_model is not None:
                # Libération synchrone de l'ancien modèle (un seul wrapper Qt,
                # destruction sans danger) — évite l'accumulation d'enfants sur la table.
                old_model.setParent(None)
            for i, width in enumerate(widths):
                table.setColumnWidth(i, width)
            if self._pages_sort is not None:
                model.sort(*self._pages_sort)
            for row in range(model.rowCount()):
                if model.image_index(row) in selected:
                    table.selectionModel().select(
                        model.index(row, 0),
                        QItemSelectionModel.Select | QItemSelectionModel.Rows)
            # Ajuste la hauteur du tableau pour afficher toutes les lignes sans scrollbar
            header_h = self._pages_table.horizontalHeader().height()
            row_h    = self._pages_table.verticalHeader().defaultSectionSize()
            self._pages_table.setFixedHeight(header_h + row_h * model.rowCount() + 2)
        if self._pages_builder is not None:
            # wait() OBLIGATOIRE avant deleteLater : done est émis à la dernière
            # ligne de run(), donc le thread tourne encore pendant quelques µs
            # quand ce slot s'exécute. deleteLater sans wait() peut détruire le
            # QThread pendant qu'il tourne → corruption mémoire silencieuse qui
            # détone plus tard n'importe où (même famille de crash que le
            # téléchargement de couvertures ComicVine, v1.5.6). Quasi instantané
            # ici puisque run() se termine juste après l'émission.
            self._pages_builder.wait()
            self._pages_builder.deleteLater()
        self._pages_builder = None
        self._restyle()

    def _drop_pages_builder(self):
        """Arrête et libère le builder en cours (cancel() attend sa fin : la
        préparation de données pures est brève). Son résultat éventuellement
        déjà en file d'attente est écarté par _on_pages_model_ready."""
        builder, self._pages_builder = self._pages_builder, None
        if builder is None:
            return
        try:
            builder.done.disconnect(self._on_pages_model_ready)
        except (RuntimeError, TypeError):
            pass
        builder.cancel()
        builder.deleteLater()

    # ── Type de page et double page depuis le tableau ────────────────────────
    def _remember_pages_sort(self, column, order):
        """Tri choisi dans l'en-tête : réappliqué au modèle reconstruit après
        un changement (_on_pages_model_ready), sans quoi les lignes
        reviendraient dans l'ordre des pages à chaque modification."""
        self._pages_sort = (column, order)

    def _page_types_editable(self):
        """Les pages peuvent changer : ComicInfo.xml lu, et aucune opération
        en arrière-plan sur le document (panel_widget.BUSY_BLOCKED_COMMANDS)."""
        st = self._state
        if st is None or st.operation_running():
            return False
        from modules.qt.comic_info import page_types_editable
        return page_types_editable(st)

    def _selected_page_positions(self, row=None):
        """Positions <Page Image="N"> des lignes sélectionnées ; `row` (ligne
        modifiée) hors de la sélection : elle seule."""
        table = self._pages_table
        model = table.model() if table is not None else None
        if not isinstance(model, _PagesTableModel):
            return []
        rows = {i.row() for i in table.selectionModel().selectedRows()}
        if row is not None and row not in rows:
            rows = {row}
        return [model.image_index(r) for r in rows]

    def _apply_page_marks(self, positions, **marks):
        """Applique le type ou la double page aux pages `positions` par la
        commande du panneau (PanelWidget._set_page_marks : historique,
        document modifié, tableau reconstruit)."""
        if not positions or not self._page_types_editable():
            return
        from modules.qt.comic_info import page_entries_at
        entries = page_entries_at(self._state, positions)
        if not entries:
            return
        p = self.parent()
        while p is not None:
            if hasattr(p, '_set_page_marks'):
                p._set_page_marks(entries, **marks)
                return
            p = p.parent()

    def _on_page_type_chosen(self, position, page_type):
        """Choix dans la liste déroulante de la cellule Type (connexion
        différée : le modèle peut être remplacé sans que l'éditeur de la
        cellule soit encore en cours de fermeture)."""
        model = self._pages_table.model() if self._pages_table is not None else None
        if not isinstance(model, _PagesTableModel):
            return
        row = next((r for r in range(model.rowCount()) if model.image_index(r) == position), None)
        if row is None:
            return
        self._apply_page_marks(self._selected_page_positions(row), page_type=page_type)

    def _on_double_page_clicked(self, row):
        """Case Double page : cochée sur toutes les lignes concernées si une
        seule ne l'est pas, sinon décochée sur toutes."""
        positions = self._selected_page_positions(row)
        if not positions:
            return
        from modules.qt.comic_info import page_entries_at, page_marks_summary
        entries = page_entries_at(self._state, positions)
        _types, doubles = page_marks_summary(self._state, entries)
        self._apply_page_marks(positions, double_page=(doubles != len(entries)))

    def _show_page_type_menu(self, global_pos):
        """Clic droit sur les lignes : le menu « Type de page » du menu
        Images, appliqué aux lignes sélectionnées."""
        positions = self._selected_page_positions()
        if not positions or self._state is None:
            return
        from modules.qt.comic_info import page_entries_at
        from modules.qt.context_menus_qt import _make_menu
        from modules.qt.menubar_qt import populate_page_type_menu
        entries = page_entries_at(self._state, positions)
        menu = _make_menu(self)
        populate_page_type_menu(
            menu, self._state, entries,
            lambda **marks: self._apply_page_marks(positions, **marks),
            enabled=self._page_types_editable())
        menu.exec(global_pos)
        menu.deleteLater()

    def update_pages(self, pages):
        """Mise à jour du tableau Pages — recharge le modèle via thread."""
        if self._toggle_btn is None:
            self.refresh()
            return
        self._pages_count = len(pages)
        arrow = "▼" if self._toggle_btn.isChecked() else "▶"
        self._toggle_btn.setText(f"{arrow}  {_('metadata.pages')}  ({self._pages_count})")
        self._drop_pages_builder()
        self._pages_builder = _PagesModelBuilder(pages)
        self._pages_builder.done.connect(self._on_pages_model_ready)
        self._pages_builder.start()
