"""
file_picker_qt.py — Fenêtre de choix d'un fichier ou d'un dossier, NON MODALE.

Remplace les boîtes de Windows (QFileDialog.getOpenFileName(s),
getSaveFileName, getExistingDirectory) : celles-ci lancent leur propre boucle
d'événements et gèlent toute l'application, l'autre panneau compris (règle UI
n° 4 de CLAUDE.md), et ne suivent ni la langue (pIqaD, tengwar), ni le thème,
ni la police de l'application.

Quatre modes, une fonction chacun ; le résultat arrive une seule fois, par
callback, jamais comme valeur de retour :

    ask_open_file(parent, "clé.titre", départ, filtres, on_result)   → chemin, "" si annulé
    ask_open_files(parent, "clé.titre", départ, filtres, on_result)  → liste, [] si annulé
    ask_save_file(parent, "clé.titre", départ, filtres, on_result)   → chemin, "" si annulé
    ask_directory(parent, "clé.titre", départ, on_result)            → chemin, "" si annulé

- `départ` : dossier, fichier (nom proposé à l'enregistrement) ou "" ;
- `filtres` : liste de file_filter(clé, motifs…) / file_filter_text(clé,
  motifs…), libellés retraduits à chaque changement de langue ;
- `center_on` (option) : widget de centrage autre que `parent` (parent
  masqué : Bibliothèque).

La suite (`on_result`) est appelée après la fermeture de la fenêtre, de
façon différée : elle revérifie ce qui a pu changer pendant le choix
(document fermé, opération démarrée, panneau changé) comme toute réponse
d'une fenêtre non modale.

Clic droit sur la liste : Nouveau dossier, Renommer (aussi F2), Supprimer
(aussi Suppr, mise à la Corbeille après confirmation). Un fichier ou un
dossier utilisé par un traitement par lot en cours n'est ni renommé ni
supprimé (batch_claims.batch_file_under).
Les règles sans Qt (chemins, filtres, historique) sont dans file_picker.py.
"""

import os
from collections import namedtuple

from PySide6.QtCore import (
    QDir, QFileSystemWatcher, QSize, QSortFilterProxyModel, QStandardPaths, Qt, QTimer, Signal,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileIconProvider, QFileSystemModel,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QPushButton,
    QSizePolicy, QSplitter, QStyledItemDelegate, QTreeView, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout,
)

from modules.qt.app_window_qt import QuestionWindowMixin
from modules.qt.batch_claims import batch_file_under
from modules.qt.file_picker import (
    COMPUTER, NavigationHistory, drive_display, drive_label, drive_roots, existing_ancestor,
    format_date, join_names, open_file_under, parent_folder, path_parts, resolve, split_names,
    start_location, subfolders, unique_folder_name, with_extension,
)
from modules.qt.font_loader import resource_path
from modules.qt.font_manager_qt import get_current_font
from modules.qt.localization import _, _wt
from modules.qt.overlay_tooltip_qt import OverlayTooltip
from modules.qt.state import get_current_theme
from modules.qt.title_bar_qt import _tip_html
from modules.qt.utils import (
    _themed_menu_stylesheet, format_file_size, send_to_trash, setup_lineedit_context_menu,
    windows_safe_component,
)

OPEN_FILE, OPEN_FILES, SAVE_FILE, DIRECTORY = range(4)

# Filtre de types : libellé (callable, retraduit) et motifs (« *.cbz »)
FileFilter = namedtuple("FileFilter", "label patterns")

# Icônes de navigation : flèches de la colonne d'icônes, dossier parent de la
# mosaïque
_ICON_BACK = "BTN_Arrow_LEFT.png"
_ICON_FORWARD = "BTN_Arrow_RIGHT.png"
_ICON_UP = "Folder-up.png"

# Colonnes de QFileSystemModel ; le type (2) est masqué
_COL_NAME, _COL_SIZE, _COL_TYPE, _COL_DATE = range(4)
_COLUMN_KEYS = {
    _COL_NAME: "dialogs.file_picker.column_name",
    _COL_SIZE: "dialogs.file_picker.column_size",
    _COL_DATE: "dialogs.file_picker.column_modified",
}

# Emplacements du volet de gauche : clé du libellé, dossier de Windows
_PLACES = (
    ("dialogs.file_picker.desktop", QStandardPaths.DesktopLocation),
    ("dialogs.file_picker.documents", QStandardPaths.DocumentsLocation),
    ("dialogs.file_picker.downloads", QStandardPaths.DownloadLocation),
    ("dialogs.file_picker.pictures", QStandardPaths.PicturesLocation),
)

_PATH_ROLE = Qt.UserRole
_KEY_ROLE = Qt.UserRole + 1
_LOADED_ROLE = Qt.UserRole + 2   # sous-dossiers d'un nœud de l'arborescence déjà lus


def file_filter(key, *patterns, **params):
    """Filtre dont le libellé traduit est suivi de ses motifs :
    file_filter("dialogs.file_filters.ext_files", "*.cbz", ext="CBZ")
    → « Fichiers CBZ (*.cbz) »."""
    return FileFilter(lambda: f"{_(key, **params)} ({' '.join(patterns)})", tuple(patterns))


def file_filter_text(key, *patterns):
    """Filtre dont la traduction contient déjà ses motifs
    (« Archives (*.cbz *.cbr …) ») : `patterns` sert au tri des fichiers."""
    return FileFilter(lambda: _(key), tuple(patterns))


def _open_documents():
    """Fichiers ouverts dans l'application : comics des panneaux de la fenêtre
    principale (MainWindow._all_panels), quelle que soit la fenêtre qui a
    ouvert la fenêtre de choix (panneau, mode d'emploi, Bibliothèque sans
    parent), et base de données ouverte dans la Bibliothèque (fenêtre unique,
    jamais détruite : library_window._library_window)."""
    from PySide6.QtWidgets import QApplication
    files = []
    for window in QApplication.topLevelWidgets():
        all_panels = getattr(window, "_all_panels", None)
        if callable(all_panels):
            files += [panel._state.current_file for panel in all_panels()
                      if panel._state.current_file]
    # Import différé : library_window importe ce module
    from modules.qt import library_window
    db = getattr(library_window._library_window, "_db", None)
    if db is not None:
        files.append(db.db_path)
    return files


def _files_in_use():
    """Fichiers que l'application lit ou va lire, hors des fichiers ouverts
    (_open_documents) : archives d'un chargement en cours, fichiers en
    attente d'ajout, archive d'où une page illisible serait relue
    (PanelWidget._files_in_use), archive ou PDF en cours d'ajout
    (files_in_use). Les renommer ou les mettre à la Corbeille ferait échouer
    l'opération en route."""
    from PySide6.QtWidgets import QApplication
    from modules.qt import files_in_use
    files = []
    for window in QApplication.topLevelWidgets():
        all_panels = getattr(window, "_all_panels", None)
        if callable(all_panels):
            for panel in all_panels():
                files += panel._files_in_use()
    return files + files_in_use.held_paths()


def _norm(path):
    return os.path.normcase(os.path.normpath(path)) if path else ""


def _valid_name(name):
    """Nom de fichier ou de dossier que Windows écrit tel quel : ni caractère
    interdit, ni point ou espace final (retiré en silence), ni nom réservé."""
    return bool(name) and "/" not in name and "\\" not in name \
        and windows_safe_component(name) == name


class _FileProxy(QSortFilterProxyModel):
    """Vue de QFileSystemModel : titres de colonnes traduits, taille et date
    au format de l'application, dossiers toujours en tête, tri naturel des
    noms, et seul le dossier tout juste créé renommable."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        from modules.qt.archive_loader import _natural_sort_key
        self._sort_key = _natural_sort_key

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole and section in _COLUMN_KEYS:
            return _(_COLUMN_KEYS[section])
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.DisplayRole):
        column = index.column()
        if column in (_COL_SIZE, _COL_DATE) and role in (Qt.DisplayRole, Qt.TextAlignmentRole):
            if role == Qt.TextAlignmentRole:
                if column == _COL_SIZE:
                    return int(Qt.AlignRight | Qt.AlignVCenter)
                return int(Qt.AlignLeft | Qt.AlignVCenter)
            info = self.sourceModel().fileInfo(self.mapToSource(index))
            if info.isRoot():
                return ""
            if column == _COL_SIZE:
                return "" if info.isDir() else format_file_size(info.size())
            return format_date(info.lastModified().toSecsSinceEpoch())
        return super().data(index, role)

    def flags(self, index):
        flags = super().flags(index) & ~(Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
        if not self._window.is_renamable(self.sourceModel().filePath(self.mapToSource(index))):
            flags &= ~Qt.ItemIsEditable
        return flags

    def _name(self, index):
        model = self.sourceModel()
        return model.fileName(index) or model.filePath(index)

    def lessThan(self, left, right):
        model = self.sourceModel()
        a, b = model.fileInfo(left), model.fileInfo(right)
        if a.isDir() != b.isDir():
            # Dossiers en tête dans les deux sens du tri
            return a.isDir() if self.sortOrder() == Qt.AscendingOrder else b.isDir()
        column = left.column()
        if column == _COL_SIZE and not a.isDir() and a.size() != b.size():
            return a.size() < b.size()
        if column == _COL_DATE:
            ta, tb = a.lastModified().toSecsSinceEpoch(), b.lastModified().toSecsSinceEpoch()
            if ta != tb:
                return ta < tb
        return self._sort_key(self._name(left).lower()) < self._sort_key(self._name(right).lower())


class _RenameDelegate(QStyledItemDelegate):
    """Renommage d'un élément (Renommer, F2, Nouveau dossier) : zone de
    saisie au menu traduit, nom contrôlé par la fenêtre avant d'être
    appliqué."""

    def __init__(self, window, view):
        super().__init__(view)
        self._window = window

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            setup_lineedit_context_menu(editor)
            editor.setFont(get_current_font())
        return editor

    def setModelData(self, editor, model, index):
        self._window.rename_item(editor.text(), model, index)


class FilePickerWindow(QuestionWindowMixin, QDialog):
    """Fenêtre de choix ; à ouvrir par ask_open_file, ask_open_files,
    ask_save_file ou ask_directory."""

    result_signal = Signal(object)

    def __init__(self, parent, mode, title_key, start, filters=(), center_on=None):
        super().__init__(parent)
        self._setup_window(close_answer=[] if mode == OPEN_FILES else "", center_on=center_on)
        self.setWindowFlags(Qt.Window)
        self.set_window_title(title_key)
        self._mode = mode
        self._filters = [] if mode == DIRECTORY else (
            list(filters) or [file_filter("dialogs.file_filters.all_files", "*")])
        self._current = COMPUTER
        self._history = NavigationHistory()
        self._renaming = ""            # élément en cours de renommage, seul modifiable
        self._status_fn = None
        self._overwrite_token = None   # question « Remplacer ? » en cours

        self._model = QFileSystemModel(self)
        # Écriture permise pour renommer le dossier créé ; le proxy retire ce
        # droit à tous les autres éléments
        self._model.setReadOnly(False)
        entries = QDir.AllDirs | QDir.NoDotAndDotDot | QDir.Drives
        if mode != DIRECTORY:
            entries |= QDir.Files
        self._model.setFilter(entries)
        self._model.setNameFilterDisables(False)
        self._model.setRootPath(COMPUTER)
        self._proxy = _FileProxy(self)
        self._proxy.setSourceModel(self._model)

        self._build()
        self.resize(900, 560)

        folder, name = start_location(start, os.path.expanduser("~"),
                                      directory_only=(mode == DIRECTORY))
        self._connect_language()
        self._apply_filter()
        self._go_to(folder)
        if name:
            self._name_edit.setText(name)
            stem = os.path.splitext(name)[0] if mode == SAVE_FILE else name
            self._name_edit.setSelection(0, len(stem))
        (self._view if mode == DIRECTORY else self._name_edit).setFocus()

    # ── Construction ──────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(8)

        nav = QHBoxLayout()
        self._btn_back = self._icon_button(_ICON_BACK, self._go_back)
        self._btn_forward = self._icon_button(_ICON_FORWARD, self._go_forward)
        self._btn_up = self._icon_button(_ICON_UP, self._go_up)
        for button in (self._btn_back, self._btn_forward, self._btn_up):
            nav.addWidget(button)
        self._path_edit = QLineEdit()
        setup_lineedit_context_menu(self._path_edit)
        self._path_edit.returnPressed.connect(self._on_path_entered)
        # Frappe de l'utilisateur seulement (textEdited) : le texte posé par le
        # code (navigation) n'efface pas le message
        self._path_edit.textEdited.connect(self._clear_status)
        nav.addWidget(self._path_edit, 1)
        self._btn_new_folder = QPushButton()
        self._btn_new_folder.setAutoDefault(False)
        self._btn_new_folder.clicked.connect(self._create_folder)
        nav.addWidget(self._btn_new_folder)
        root.addLayout(nav)

        # Volet de gauche et liste séparés par une poignée : le volet
        # s'élargit pour un chemin profond
        body = QSplitter(Qt.Horizontal)
        body.setChildrenCollapsible(False)
        self._places = QTreeWidget()
        self._places.setHeaderHidden(True)
        self._places.setMinimumWidth(140)
        # Colonne à la largeur des noms : un nom long fait défiler le volet
        # horizontalement au lieu d'être coupé
        self._places.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._places.header().setStretchLastSection(False)
        self._places.itemClicked.connect(self._on_place_clicked)
        self._places.itemActivated.connect(self._on_place_clicked)
        self._places.itemExpanded.connect(self._fill_children)
        self._icons = QFileIconProvider()
        # Dossiers dont l'arbre a lu les sous-dossiers : surveillés par
        # Windows, l'arbre suit les créations, suppressions et renommages
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_folder_changed)
        self._watched = {}   # chemin (_norm) -> nœud de l'arbre
        self._fill_places()
        # Lecteurs branchés ou retirés (clé USB, lecteur réseau) : masque des
        # lecteurs relu chaque seconde, sans accès aux disques
        self._drives_timer = QTimer(self)
        self._drives_timer.setInterval(1000)
        self._drives_timer.timeout.connect(self._check_drives)
        self._drives_timer.start()
        body.addWidget(self._places)

        self._view = QTreeView()
        self._view.setModel(self._proxy)
        self._view.setRootIsDecorated(False)
        self._view.setItemsExpandable(False)
        self._view.setExpandsOnDoubleClick(False)
        self._view.setUniformRowHeights(True)
        self._view.setSelectionBehavior(QAbstractItemView.SelectRows)
        # Plusieurs éléments sélectionnables dans tous les modes (Supprimer) ;
        # seul le mode « plusieurs fichiers » en garde plusieurs pour le choix
        self._view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # Renommage seulement par Renommer (menu, F2) ou Nouveau dossier
        self._view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._show_list_menu)
        self._view.setItemDelegate(_RenameDelegate(self, self._view))
        self._view.setColumnHidden(_COL_TYPE, True)
        self._view.setSortingEnabled(True)
        self._view.sortByColumn(_COL_NAME, Qt.AscendingOrder)
        header = self._view.header()
        header.setStretchLastSection(True)
        header.resizeSection(_COL_NAME, 380)
        header.resizeSection(_COL_SIZE, 110)
        self._view.activated.connect(self._on_activated)
        self._view.selectionModel().selectionChanged.connect(self._on_selection_changed)
        body.addWidget(self._view)
        body.setStretchFactor(1, 1)
        body.setSizes([230, 650])
        root.addWidget(body, 1)

        form = QGridLayout()
        form.setHorizontalSpacing(8)
        self._name_label = QLabel()
        self._name_edit = QLineEdit()
        setup_lineedit_context_menu(self._name_edit)
        self._name_edit.returnPressed.connect(self._accept)
        self._name_edit.textChanged.connect(self._update_buttons)
        self._name_edit.textEdited.connect(self._clear_status)
        form.addWidget(self._name_label, 0, 0)
        form.addWidget(self._name_edit, 0, 1)
        self._type_label = QLabel()
        self._filter_combo = QComboBox()
        self._filter_combo.currentIndexChanged.connect(self._apply_filter)
        if self._filters:
            form.addWidget(self._type_label, 1, 0)
            form.addWidget(self._filter_combo, 1, 1)
        else:
            self._type_label.hide()
            self._filter_combo.hide()
        form.setColumnStretch(1, 1)
        root.addLayout(form)

        # Message d'erreur à gauche des boutons, à place fixe (hauteur de deux
        # lignes posée par _retranslate, largeur prise sur l'espace libre) :
        # son apparition ne déplace rien dans la fenêtre
        self._status = QLabel()
        self._status.setTextFormat(Qt.PlainText)  # cite des noms de fichiers
        self._status.setWordWrap(True)
        self._status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

        buttons = QHBoxLayout()
        buttons.addWidget(self._status, 1)
        self._btn_ok = QPushButton()
        self._btn_cancel = QPushButton()
        for button in (self._btn_ok, self._btn_cancel):
            button.setAutoDefault(False)
            button.setMinimumWidth(110)
            buttons.addWidget(button)
        self._btn_ok.clicked.connect(self._accept)
        self._btn_cancel.clicked.connect(self.close)
        root.addLayout(buttons)

        self._overlay_tip = OverlayTooltip(self)
        for button in (self._btn_back, self._btn_forward, self._btn_up):
            self._overlay_tip.track(button)

    def _icon_button(self, icon_name, slot):
        """Bouton de navigation à icône (grisée par Qt quand il est désactivé)."""
        button = QPushButton()
        button.setObjectName("navButton")
        button.setIcon(QIcon(resource_path(os.path.join("icons", icon_name))))
        button.setIconSize(QSize(22, 22))
        button.setFixedSize(36, 30)
        button.setAutoDefault(False)
        button.clicked.connect(slot)
        return button

    def _fill_places(self):
        """Volet de gauche : Ce PC et les raccourcis (Bureau, Documents…), qui
        ne se déplient pas, puis les lecteurs, dépliables dossier par dossier
        (_fill_children). Le dossier affiché est toujours déplié sous son
        lecteur (_reveal_in_places), même ouvert par un raccourci."""
        item = QTreeWidgetItem([""])
        item.setIcon(0, self._icons.icon(QFileIconProvider.Computer))
        item.setData(0, _PATH_ROLE, COMPUTER)
        item.setData(0, _KEY_ROLE, "dialogs.file_picker.this_pc")
        self._places.addTopLevelItem(item)
        self._computer_item = item
        for key, location in _PLACES:
            path = QStandardPaths.writableLocation(location)
            if path and os.path.isdir(path):
                item = QTreeWidgetItem([""])
                item.setIcon(0, self._icons.icon(QFileIconProvider.Folder))
                item.setData(0, _PATH_ROLE, os.path.normpath(path))
                item.setData(0, _KEY_ROLE, key)
                self._places.addTopLevelItem(item)
        self._drive_items = {}   # racine (_norm) -> nœud du lecteur
        self._drives = []
        self._sync_drives()

    def _new_folder_item(self, name, path, icon_type=QFileIconProvider.Folder):
        item = QTreeWidgetItem([name])
        item.setIcon(0, self._icons.icon(icon_type))
        item.setData(0, _PATH_ROLE, path)
        # Flèche affichée avant toute lecture : les sous-dossiers ne sont lus
        # qu'au dépliage (un lecteur réseau ou un CD peut être lent)
        item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        return item

    def _sync_drives(self):
        """Lecteurs du volet alignés sur ceux de Windows (file_picker.
        drive_roots) : un lecteur apparu est ajouté à sa place (ordre des
        lettres, après les raccourcis), un lecteur retiré est enlevé avec ses
        dossiers surveillés. Rend True si la liste a changé."""
        roots = drive_roots()
        if roots == self._drives:
            return False
        self._drives = roots
        present = {_norm(root) for root in roots}
        for key, item in list(self._drive_items.items()):
            if key not in present:
                self._forget(item)
                self._places.takeTopLevelItem(self._places.indexOfTopLevelItem(item))
                del self._drive_items[key]
        first_drive = self._places.topLevelItemCount() - len(self._drive_items)
        for position, root in enumerate(roots):
            if _norm(root) in self._drive_items:
                continue
            item = self._new_folder_item(drive_display(root, drive_label(root)), root,
                                         QFileIconProvider.Drive)
            self._places.insertTopLevelItem(first_drive + position, item)
            self._drive_items[_norm(root)] = item
        return True

    def _check_drives(self):
        """Minuterie : un lecteur branché ou retiré met le volet à jour ; si le
        dossier affiché était sur un lecteur retiré, retour à Ce PC."""
        if not self._sync_drives():
            return
        if self._current and not os.path.isdir(self._current):
            self._go_to(existing_ancestor(self._current) or COMPUTER)
        else:
            self._reveal_in_places(self._current)

    def _fill_children(self, item):
        """Lit les sous-dossiers d'un lecteur ou d'un dossier du volet au
        premier dépliage, puis le fait surveiller (_on_folder_changed)."""
        if item.data(0, _LOADED_ROLE):
            return
        self._sync_children(item)
        item.setData(0, _LOADED_ROLE, True)
        path = item.data(0, _PATH_ROLE)
        if self._watcher.addPath(path):
            self._watched[_norm(path)] = item

    def _sync_children(self, item):
        """Sous-dossiers du nœud alignés sur le disque (file_picker.subfolders),
        sur place : un dossier disparu est retiré (avec ses dossiers
        surveillés), un nouveau inséré à sa place dans l'ordre ; ceux qui
        restent gardent leurs nœuds, donc leur état déplié."""
        wanted = subfolders(item.data(0, _PATH_ROLE))
        wanted_keys = {os.path.normcase(name) for name, _path in wanted}
        row = 0
        for name, path in wanted:
            key = os.path.normcase(name)
            while row < item.childCount():
                child = item.child(row)
                child_key = os.path.normcase(child.text(0))
                if child_key in wanted_keys:
                    break
                self._forget(child)
                item.removeChild(child)
            if row < item.childCount() and os.path.normcase(item.child(row).text(0)) == key:
                row += 1
                continue
            # Nouveau dossier, ou nœud existant plus loin (ordre changé) : le
            # nœud existant est déplacé, sinon un nœud neuf est créé
            existing = next((item.child(i) for i in range(row + 1, item.childCount())
                             if os.path.normcase(item.child(i).text(0)) == key), None)
            if existing is not None:
                item.removeChild(existing)
                item.insertChild(row, existing)
            else:
                item.insertChild(row, self._new_folder_item(name, path))
            row += 1
        while item.childCount() > row:
            child = item.child(row)
            self._forget(child)
            item.removeChild(child)
        item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator if item.childCount()
                                     else QTreeWidgetItem.DontShowIndicatorWhenChildless)

    def _forget(self, item):
        """Nœud retiré de l'arbre : ses dossiers et ceux de ses descendants ne
        sont plus surveillés."""
        stack = [item]
        while stack:
            node = stack.pop()
            path = node.data(0, _PATH_ROLE)
            if path and self._watched.get(_norm(path)) is node:
                del self._watched[_norm(path)]
                self._watcher.removePath(path)
            stack.extend(node.child(i) for i in range(node.childCount()))

    def _on_folder_changed(self, path):
        """Un dossier surveillé a changé (sous-dossier créé, supprimé,
        renommé) : ses sous-dossiers sont mis à jour sur place. Si le dossier
        affiché a disparu, la fenêtre remonte à son plus proche parent."""
        item = self._watched.get(_norm(path))
        if item is not None and os.path.isdir(path):
            self._sync_children(item)
        if self._current and not os.path.isdir(self._current):
            self._go_to(existing_ancestor(self._current) or COMPUTER)

    def _reveal_in_places(self, path):
        """Déplie le volet jusqu'au dossier affiché, sous son lecteur, et le
        met en évidence, centré verticalement ; tout le reste se replie. Un
        chemin réseau sans lecteur (\\\\serveur\\partage) ne met rien en
        évidence."""
        tree = self._places
        tree.collapseAll()
        target = None
        if not path:
            target = self._computer_item
        else:
            root, parts = path_parts(path)
            item = self._drive_items.get(_norm(root)) if root else None
            target = item
            for part in parts:
                if item is None:
                    break
                self._fill_children(item)
                key = os.path.normcase(part)
                child = next((item.child(i) for i in range(item.childCount())
                              if os.path.normcase(item.child(i).text(0)) == key), None)
                if child is None:
                    break  # dossier caché ou disparu : le plus proche parent reste en évidence
                tree.expandItem(item)
                item = target = child
        if target is None:
            tree.setCurrentItem(None)
            tree.clearSelection()
        else:
            tree.setCurrentItem(target)
            self._center_place_vertically(target)
            # Les nœuds tout juste dépliés ne sont placés qu'au prochain
            # calcul de la vue : centrage refait à ce moment-là
            QTimer.singleShot(0, self, self._center_current_place)

    def _center_current_place(self):
        item = self._places.currentItem()
        if item is not None:
            self._center_place_vertically(item)

    def _center_place_vertically(self, item):
        """Centre le nœud verticalement dans le volet, défilement horizontal
        ramené à gauche : PositionAtCenter centre aussi horizontalement, sur
        le milieu de la colonne, qui a la largeur du nom le plus long."""
        self._places.scrollToItem(item, QAbstractItemView.PositionAtCenter)
        self._places.horizontalScrollBar().setValue(0)

    # ── Langue, thème, police ─────────────────────────────────────────────

    def _apply_theme(self):
        theme = get_current_theme()
        bg, fg, sep = theme["bg"], theme["text"], theme["separator"]
        entry_bg, alt = theme["entry_bg"], theme["toolbar_bg"]
        self.setStyleSheet(
            f"QDialog {{ background: {bg}; color: {fg}; }} "
            f"QLabel {{ color: {fg}; }} "
            f"QLineEdit {{ background: {entry_bg}; color: {fg}; border: 1px solid {sep}; "
            f"padding: 3px; }} "
            f"QTreeView {{ background: {entry_bg}; color: {fg}; "
            f"border: 1px solid {sep}; outline: 0; }} "
            f"QTreeView::item:selected {{ background: #3399ff; color: #ffffff; }} "
            f"QTreeView::item:hover:!selected {{ background: {theme['icon_hover']}; }} "
            f"QSplitter::handle {{ background: {bg}; width: 6px; }} "
            f"QHeaderView::section {{ background: {alt}; color: {fg}; "
            f"border: 1px solid {sep}; padding: 3px 4px; }} "
            f"QComboBox {{ background: {entry_bg}; color: {fg}; border: 1px solid {sep}; "
            f"padding: 2px 6px; }} "
            f"QComboBox QAbstractItemView {{ background: {entry_bg}; color: {fg}; "
            f"selection-background-color: #3399ff; selection-color: #ffffff; }} "
            f"QPushButton {{ background: {alt}; color: {fg}; border: 1px solid {sep}; "
            f"padding: 4px 12px; border-radius: 3px; }} "
            f"QPushButton:hover {{ background: {theme['icon_hover']}; }} "
            f"QPushButton:disabled {{ color: {theme['disabled']}; }} "
            f"QPushButton#navButton {{ padding: 2px; }}"
        )
        self._status.setStyleSheet(f"color: {theme['memory_red']};")
        self._overlay_tip.apply_theme()

    def _retranslate(self):
        font = get_current_font()
        ok_key = {SAVE_FILE: "dialogs.file_picker.save",
                  DIRECTORY: "dialogs.file_picker.select_folder"}.get(self._mode,
                                                                        "dialogs.file_picker.open")
        self._btn_ok.setText(_(ok_key))
        self._btn_cancel.setText(_("buttons.cancel"))
        self._btn_new_folder.setText(_("dialogs.file_picker.new_folder"))
        self._name_label.setText(_("dialogs.file_picker.folder_label" if self._mode == DIRECTORY
                                   else "dialogs.file_picker.name_label"))
        self._type_label.setText(_("dialogs.file_picker.type_label"))
        self._path_edit.setPlaceholderText(_("dialogs.file_picker.this_pc"))
        if self._filters:
            index = max(self._filter_combo.currentIndex(), 0)
            self._filter_combo.blockSignals(True)
            self._filter_combo.clear()
            self._filter_combo.addItems([f.label() for f in self._filters])
            self._filter_combo.setCurrentIndex(index)
            self._filter_combo.blockSignals(False)
        for row in range(self._places.topLevelItemCount()):
            item = self._places.topLevelItem(row)
            key = item.data(0, _KEY_ROLE)
            if key:
                item.setText(0, _(key))
        for button, key in ((self._btn_back, "dialogs.file_picker.back"),
                            (self._btn_forward, "dialogs.file_picker.forward"),
                            (self._btn_up, "dialogs.file_picker.up")):
            self._overlay_tip.set_tracked_html(_tip_html(_(key)), button)
        self._proxy.headerDataChanged.emit(Qt.Horizontal, 0, _COL_DATE)
        for widget in (self._btn_ok, self._btn_cancel, self._btn_new_folder, self._name_label,
                       self._type_label, self._path_edit, self._name_edit, self._filter_combo,
                       self._places, self._view, self._view.header(), self._status):
            widget.setFont(font)
        # Deux lignes réservées en permanence, d'après la police courante
        self._status.setFixedHeight(self._status.fontMetrics().lineSpacing() * 2 + 4)
        self._refresh_status()

    # ── Navigation ────────────────────────────────────────────────────────

    def _go_to(self, path, record=True):
        """Affiche le dossier `path` (COMPUTER : les lecteurs). False s'il
        n'existe pas (message sous la liste)."""
        path = os.path.normpath(path) if path else COMPUTER
        if path and not os.path.isdir(path):
            self._set_status(lambda p=path: _("dialogs.file_picker.not_found", name=p))
            return False
        self._current = path
        if record:
            self._history.visit(path)
        self._model.setRootPath(path)
        self._view.setRootIndex(self._proxy.mapFromSource(self._model.index(path)))
        self._view.selectionModel().clear()
        self._path_edit.setText(path)
        self._reveal_in_places(path)
        if self._mode == DIRECTORY:
            self._name_edit.clear()
        self._set_status(None)
        self._update_buttons()
        return True

    def _go_back(self):
        path = self._history.back()
        if path is not None and not self._go_to(path, record=False):
            self._history.forward()
        self._update_buttons()

    def _go_forward(self):
        path = self._history.forward()
        if path is not None and not self._go_to(path, record=False):
            self._history.back()
        self._update_buttons()

    def _go_up(self):
        parent = parent_folder(self._current)
        if parent is not None:
            self._go_to(parent)

    def _on_place_clicked(self, item, _column=0):
        """Clic ou Entrée sur le volet : ouvre l'emplacement au tour suivant
        de la boucle d'événements. _go_to relit les sous-dossiers du chemin
        affiché, ce qui détruit les nœuds de l'arborescence, dont peut-être
        celui qui a émis le signal : jamais pendant son émission."""
        path = item.data(0, _PATH_ROLE)
        QTimer.singleShot(0, self, lambda p=path: self._go_to(p))

    def _on_path_entered(self):
        path = resolve(self._current, self._path_edit.text())
        if not path:
            self._go_to(COMPUTER)
        elif os.path.isdir(path):
            self._go_to(path)
        elif os.path.isfile(path) and self._mode in (OPEN_FILE, OPEN_FILES):
            self._answer([path] if self._mode == OPEN_FILES else path)
        else:
            self._set_status(lambda p=path: _("dialogs.file_picker.not_found", name=p))

    def _on_activated(self, index):
        """Double-clic ou Entrée sur la liste : un dossier s'ouvre, un fichier
        est choisi."""
        path = os.path.normpath(self._model.filePath(self._proxy.mapToSource(index)))
        if os.path.isdir(path):
            self._go_to(path)
        elif self._mode in (OPEN_FILE, OPEN_FILES, SAVE_FILE):
            self._name_edit.setText(os.path.basename(path))
            self._accept()

    def _on_selection_changed(self, *_args):
        """Nom du champ d'après la sélection : fichiers (dossiers pour le
        choix d'un dossier). Un dossier sélectionné à l'enregistrement ne
        remplace pas le nom tapé."""
        self._set_status(None)
        names = []
        for index in self._view.selectionModel().selectedRows(_COL_NAME):
            info = self._model.fileInfo(self._proxy.mapToSource(index))
            if info.isDir() == (self._mode == DIRECTORY):
                names.append(info.fileName())
        if names:
            self._name_edit.setText(join_names(names) if self._mode == OPEN_FILES else names[0])
        elif self._mode == DIRECTORY:
            self._name_edit.clear()

    def _apply_filter(self, *_args):
        self._set_status(None)
        if self._filters:
            index = max(self._filter_combo.currentIndex(), 0)
            self._model.setNameFilters(list(self._filters[index].patterns))

    def _update_buttons(self, *_args):
        self._btn_back.setEnabled(self._history.can_go_back())
        self._btn_forward.setEnabled(self._history.can_go_forward())
        self._btn_up.setEnabled(bool(self._current))
        self._btn_new_folder.setEnabled(bool(self._current))
        has_name = bool(self._name_edit.text().strip())
        self._btn_ok.setEnabled(has_name or (self._mode == DIRECTORY and bool(self._current)))

    def keyPressEvent(self, event):
        key, modifiers = event.key(), event.modifiers()
        in_list = self.focusWidget() is self._view
        if in_list and key == Qt.Key_F2:
            self._rename_selected()
        elif in_list and key == Qt.Key_Delete:
            self._delete_selected()
        elif key == Qt.Key_Backspace and not isinstance(self.focusWidget(), QLineEdit):
            self._go_up()
        elif modifiers & Qt.AltModifier and key == Qt.Key_Left:
            self._go_back()
        elif modifiers & Qt.AltModifier and key == Qt.Key_Right:
            self._go_forward()
        elif modifiers & Qt.AltModifier and key == Qt.Key_Up:
            self._go_up()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    # ── Nouveau dossier, renommer, supprimer ──────────────────────────────

    def is_renamable(self, path):
        """Seul l'élément dont le renommage a été demandé (Renommer, F2,
        Nouveau dossier) est modifiable."""
        return bool(self._renaming) and _norm(path) == self._renaming

    def _selected_paths(self):
        """Chemins des éléments sélectionnés, hors lecteurs (liste « Ce PC »)."""
        if not self._current:
            return []
        return [os.path.normpath(self._model.filePath(self._proxy.mapToSource(index)))
                for index in self._view.selectionModel().selectedRows(_COL_NAME)]

    def _show_list_menu(self, pos):
        """Clic droit sur la liste : Nouveau dossier, Renommer, Supprimer.
        Un clic sur un élément non sélectionné le sélectionne seul."""
        index = self._view.indexAt(pos)
        if index.isValid() and not self._view.selectionModel().isRowSelected(index.row(),
                                                                            index.parent()):
            self._view.setCurrentIndex(index)
        elif not index.isValid():
            self._view.selectionModel().clear()
        paths = self._selected_paths()
        menu = QMenu(self._view)
        menu.setStyleSheet(_themed_menu_stylesheet(get_current_font()))
        new_folder = menu.addAction(_("dialogs.file_picker.new_folder"))
        new_folder.setEnabled(bool(self._current))
        menu.addSeparator()
        rename = menu.addAction(_("dialogs.file_picker.rename"))
        rename.setEnabled(len(paths) == 1)
        delete = menu.addAction(_("dialogs.file_picker.delete"))
        delete.setEnabled(bool(paths))
        chosen = menu.exec(self._view.viewport().mapToGlobal(pos))
        menu.deleteLater()
        if chosen is new_folder:
            self._create_folder()
        elif chosen is rename:
            self._rename_selected()
        elif chosen is delete:
            self._delete_selected()

    def _refuse_if_in_use(self, paths):
        """True (et message) si l'un des éléments est, ou contient, un fichier
        ouvert dans l'application — comic d'un panneau, base de la
        Bibliothèque : ils resteraient sur un nom qui n'existe plus — ou un
        fichier d'un traitement par lot en cours (le lot le perdrait en
        route), ou un fichier que l'application lit ou va lire (ajout en
        cours ou en attente, archive d'une page illisible : _files_in_use).
        Relu à chaque demande : un fichier a pu être ouvert ou fermé depuis
        l'ouverture de la fenêtre."""
        opened = _open_documents()
        in_use = _files_in_use()
        for path in paths:
            in_panel = open_file_under(path, opened)
            if in_panel:
                self._set_status(lambda n=os.path.basename(in_panel):
                                 _("dialogs.file_picker.file_open", name=n))
                return True
            read = open_file_under(path, in_use)
            if read:
                self._set_status(lambda n=os.path.basename(read):
                                 _("dialogs.file_picker.file_in_use", name=n))
                return True
            if batch_file_under(path):
                self._set_status(lambda n=os.path.basename(path):
                                 _("messages.errors.file_in_batch", name=n))
                return True
        return False

    def _create_folder(self):
        """Crée un dossier dans le dossier affiché, nom libre (« Nouveau
        dossier (2) »…) dans la langue de l'application en alphabet latin
        (il est écrit sur le disque), puis ouvre son nom à la saisie."""
        self._set_status(None)
        if not self._current:
            return
        try:
            existing = os.listdir(self._current)
        except OSError:
            existing = []
        name = unique_folder_name(existing, _wt("dialogs.file_picker.new_folder"))
        index = self._model.mkdir(self._model.index(self._current), name)
        if not index.isValid():
            self._set_status(lambda f=self._current: _("dialogs.file_picker.create_failed", folder=f))
            return
        self._set_status(None)
        self._edit_name(index)

    def _rename_selected(self):
        self._set_status(None)
        paths = self._selected_paths()
        if len(paths) != 1 or self._refuse_if_in_use(paths):
            return
        self._edit_name(self._model.index(paths[0]))

    def _edit_name(self, source_index):
        """Ouvre le nom de l'élément à la saisie (_RenameDelegate)."""
        self._renaming = _norm(self._model.filePath(source_index))
        view_index = self._proxy.mapFromSource(source_index)
        self._view.setCurrentIndex(view_index)
        self._view.scrollTo(view_index)
        self._view.edit(view_index)

    def rename_item(self, text, model, index):
        """Nom saisi pour l'élément en cours de renommage (_RenameDelegate).
        Lot revérifié ici : il a pu démarrer pendant la saisie."""
        name = text.strip()
        old = model.data(index, Qt.EditRole)
        if not name or name == old:
            return
        if not _valid_name(name):
            self._set_status(lambda n=name: _("dialogs.file_picker.invalid_name", name=n))
            return
        old_path = os.path.normpath(self._model.filePath(self._proxy.mapToSource(index)))
        if self._refuse_if_in_use([old_path]):
            return
        if not model.setData(index, name, Qt.EditRole):
            self._set_status(lambda n=name: _("dialogs.file_picker.rename_failed", name=n))
            return
        self._renaming = _norm(os.path.join(os.path.dirname(old_path), name))
        self._set_status(None)

    def _delete_selected(self):
        """Met les éléments sélectionnés à la Corbeille (utils.send_to_trash,
        jamais de suppression définitive), après confirmation."""
        self._set_status(None)
        paths = self._selected_paths()
        if not paths or self._refuse_if_in_use(paths):
            return
        from modules.qt.dialogs_qt import ConfirmYNDialog
        from modules.qt.recycle_bin import trash_problem, trash_problem_count, warning_text
        if len(paths) == 1:
            message = lambda n=os.path.basename(paths[0]): _("dialogs.file_picker.delete_one", name=n)
            # Pas de Corbeille pour cet élément : suppression définitive
            warning = lambda p=trash_problem(paths[0]): warning_text(p)
        else:
            message = lambda c=len(paths): _("dialogs.file_picker.delete_many", count=c)
            no_trash = trash_problem_count(paths)
            warning = lambda c=no_trash: (_("messages.trash.many_permanent", count=c)
                                          if c else "")

        def _on_reply(confirmed):
            # Réponse différée : un lot a pu démarrer, un élément disparaître
            if not confirmed or self._answered:
                return
            targets = [p for p in paths if os.path.exists(p)]
            if self._refuse_if_in_use(targets):
                return
            self._set_status(None)
            for path in targets:
                try:
                    send_to_trash(path)
                except OSError as e:
                    from modules.qt.temp_files import exception_text
                    self._set_status(lambda n=os.path.basename(path), err=exception_text(e):
                                     _("dialogs.file_picker.delete_failed", name=n, error=err))
                    return

        ConfirmYNDialog(self, "dialogs.file_picker.delete_title", message,
                        warning=warning).ask_async(_on_reply)

    # ── Validation ────────────────────────────────────────────────────────

    def _accept(self):
        self._set_status(None)
        text = self._name_edit.text()
        if self._mode == DIRECTORY:
            self._accept_directory(text)
        elif self._mode == SAVE_FILE:
            self._accept_save(text)
        else:
            self._accept_open(text)

    def _accept_directory(self, text):
        path = resolve(self._current, text) if text.strip() else self._current
        if not path:
            return
        if not os.path.isdir(path):
            self._set_status(lambda n=text.strip(): _("dialogs.file_picker.not_found", name=n))
            return
        self._answer(path)

    def _accept_open(self, text):
        names = split_names(text) if self._mode == OPEN_FILES else [text.strip()] * bool(text.strip())
        if not names:
            return
        paths = [resolve(self._current, n) for n in names]
        if len(paths) == 1 and os.path.isdir(paths[0]):
            self._go_to(paths[0])
            self._name_edit.clear()
            return
        for name, path in zip(names, paths):
            if not os.path.isfile(path):
                self._set_status(lambda n=name: _("dialogs.file_picker.not_found", name=n))
                return
        self._answer(paths if self._mode == OPEN_FILES else paths[0])

    def _accept_save(self, text):
        path = resolve(self._current, text)
        if not path:
            return
        if os.path.isdir(path):
            self._go_to(path)
            self._name_edit.clear()
            return
        folder, name = os.path.split(path)
        if not _valid_name(name):
            self._set_status(lambda n=name: _("dialogs.file_picker.invalid_name", name=n))
            return
        patterns = self._filters[max(self._filter_combo.currentIndex(), 0)].patterns
        name = with_extension(name, patterns)
        path = os.path.join(folder, name)
        if not os.path.isdir(folder):
            self._set_status(lambda f=folder: _("dialogs.file_picker.not_found", name=f))
            return
        if os.path.isdir(path):
            self._set_status(lambda n=name: _("dialogs.file_picker.invalid_name", name=n))
            return
        if not os.path.exists(path):
            self._answer(path)
            return
        # Fichier existant : remplacé seulement après confirmation. Une
        # nouvelle validation pendant la question la rend caduque.
        from modules.qt.dialogs_qt import ConfirmYNDialog
        token = self._overwrite_token = object()

        def _on_reply(replace):
            if replace and token is self._overwrite_token and not self._answered:
                self._answer(path)

        ConfirmYNDialog(self, "dialogs.file_picker.replace_title",
                        lambda n=name: _("dialogs.file_picker.replace_message", name=n)
                        ).ask_async(_on_reply)

    def _answer(self, result):
        """Fenêtre masquée tout de suite, réponse au tour suivant de la boucle
        d'événements : la suite (un enregistrement, avec son texte rouge de
        progression sur la mosaïque) commence fenêtre disparue. Sans cela, la
        fenêtre resterait affichée par-dessus la progression jusqu'à la fin de
        l'écriture. Rattaché à la fenêtre : détruite entre-temps avec son
        parent, elle ne répond plus."""
        if self._answered:
            return
        self._drives_timer.stop()
        self.hide()
        QTimer.singleShot(0, self, lambda r=result: QuestionWindowMixin._answer(self, r))

    # ── Message sous la liste ─────────────────────────────────────────────

    def _clear_status(self, *_args):
        self._set_status(None)

    def _set_status(self, text_fn):
        """Message d'erreur (callable, retraduit), None pour l'effacer.
        Effacé dès que l'utilisateur agit (sélection, frappe dans un champ,
        filtre, dossier, nouvelle action), jamais par une minuterie : un
        message qui disparaît seul peut ne pas avoir été lu."""
        self._status_fn = text_fn
        self._refresh_status()

    def _refresh_status(self):
        # Jamais masqué : vidé, il garde sa place
        self._status.setText("" if self._status_fn is None else self._status_fn())


def _ask(parent, mode, title_key, start, filters, on_result, center_on):
    window = FilePickerWindow(parent, mode, title_key, start, filters, center_on=center_on)
    window.ask_async(on_result)
    return window


def ask_open_file(parent, title_key, start, filters, on_result, center_on=None):
    """Un fichier existant ; on_result(chemin), "" si annulé."""
    return _ask(parent, OPEN_FILE, title_key, start, filters, on_result, center_on)


def ask_open_files(parent, title_key, start, filters, on_result, center_on=None):
    """Un ou plusieurs fichiers existants ; on_result(liste), [] si annulé."""
    return _ask(parent, OPEN_FILES, title_key, start, filters, on_result, center_on)


def ask_save_file(parent, title_key, start, filters, on_result, center_on=None):
    """Nom d'un fichier à écrire (remplacement d'un fichier existant
    confirmé, extension du filtre ajoutée) ; on_result(chemin), "" si annulé."""
    return _ask(parent, SAVE_FILE, title_key, start, filters, on_result, center_on)


def ask_directory(parent, title_key, start, on_result, center_on=None):
    """Un dossier existant ; on_result(chemin), "" si annulé."""
    return _ask(parent, DIRECTORY, title_key, start, (), on_result, center_on)
