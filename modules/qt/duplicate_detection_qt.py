# -------------------------
# Détection des pages en double au sein d'une même archive (hash MD5 exact)
# -------------------------
import hashlib
from collections import defaultdict

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QScrollArea, QWidget, QFrame,
)
from PySide6.QtCore import Qt, QTimer

from modules.qt.app_window_qt import AppWindowMixin
from modules.qt.localization import _
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font


def recompute_duplicate_groups(state):
    """Recalcule les groupes de doublons sur state.images_data.

    Rehash paresseusement les entrées dont le cache _hash est None (nouvelles
    ou modifiées depuis le dernier calcul), puis regroupe par hash identique.
    Assigne entry["_is_duplicate"]/entry["_duplicate_group"] (id = le hash lui-même,
    stable entre deux recalculs tant que le contenu du groupe ne change pas).
    """
    if state is None:
        return

    groups = defaultdict(list)
    for entry in state.images_data:
        if not entry.get("is_image") or entry.get("is_corrupted"):
            entry["_hash"] = None
            entry["_is_duplicate"] = False
            entry["_duplicate_group"] = None
            continue

        if entry.get("_hash") is None:
            data = entry.get("bytes")
            entry["_hash"] = hashlib.md5(data).hexdigest() if data else None

        if entry["_hash"] is not None:
            groups[entry["_hash"]].append(entry)

    for file_hash, members in groups.items():
        is_dup = len(members) >= 2
        for entry in members:
            entry["_is_duplicate"] = is_dup
            entry["_duplicate_group"] = file_hash if is_dup else None


def _has_subdirectory_structure(state) -> bool:
    return any(
        '/' in e.get("orig_name", "") and not e.get("is_dir")
        for e in state.images_data
    )


def get_duplicate_groups(state) -> list:
    """Retourne les groupes de doublons sous forme de liste triée pour affichage.

    Chaque groupe est une liste de (real_idx, entry) triée par real_idx croissant.
    Les groupes eux-mêmes sont triés par real_idx de leur première page.
    """
    recompute_duplicate_groups(state)
    groups_by_hash = defaultdict(list)
    for i, entry in enumerate(state.images_data):
        if entry.get("_is_duplicate"):
            groups_by_hash[entry["_duplicate_group"]].append((i, entry))
    groups = list(groups_by_hash.values())
    groups.sort(key=lambda g: g[0][0])
    return groups


def has_any_duplicate(state) -> bool:
    """True si au moins une page en double existe actuellement dans l'archive."""
    recompute_duplicate_groups(state)
    return any(e.get("_is_duplicate") for e in state.images_data)


def delete_pages(state, pages, save_state, render_mosaic, refresh_tabs=None):
    """Supprime ces pages (entrées), retrouvées par identité au moment de l'appel :
    la fenêtre et sa confirmation sont non modales, et une position retenue à
    l'affichage désignerait une autre page si le document a changé depuis. Une
    page déjà retirée (seconde confirmation du même clic) est ignorée."""
    wanted = {id(page) for page in pages}
    delete_entries_by_index(
        state, [i for i, entry in enumerate(state.images_data) if id(entry) in wanted],
        save_state, render_mosaic, refresh_tabs)


def _groups_key(groups) -> tuple:
    """Ce que la liste de la fenêtre affiche de ces groupes (get_duplicate_groups) :
    à comparer pour ne la reconstruire que si cela a changé."""
    return tuple(tuple((real_idx, id(entry), entry.get("orig_name"), entry.get("_hash"))
                       for real_idx, entry in group)
                 for group in groups)


def _checkbox_text(name: str) -> str:
    """Texte d'une case à cocher affichant ce nom : Qt prend un & pour la marque
    d'un raccourci clavier, le masque et souligne la lettre qui suit."""
    return name.replace("&", "&&")


def delete_entries_by_index(state, indices, save_state, render_mosaic, refresh_tabs=None):
    """Supprime les entrées aux indices donnés (liste explicite, indépendante de
    state.selected_indices), en suivant le pattern d'opération sur images_data :
    save_state avant/après, sync du ComicInfo.xml, render_mosaic."""
    if not indices:
        return
    # Fenêtre des doublons non modale : une opération (rotation…) a pu être
    # lancée sur le document depuis son ouverture (panel_widget.BUSY_BLOCKED_COMMANDS).
    if state.operation_running():
        return

    from modules.qt.comic_info import has_comic_info_entry, sync_pages_in_xml_data

    # État d'avant. Sans force : déjà au sommet de l'historique, une copie
    # donnerait un Annuler sans effet.
    save_state()
    for idx in sorted(set(indices), reverse=True):
        if idx < len(state.images_data):
            state.images_data.pop(idx)
    state.modified = True
    sync_pages_in_xml_data(state)

    image_count = sum(1 for e in state.images_data if e.get("is_image", False))
    if image_count == 0:
        state.needs_renumbering = False

    has_xml = has_comic_info_entry(state)
    if not has_xml and state.comic_metadata:
        state.comic_metadata = None
        if refresh_tabs:
            refresh_tabs()

    save_state(force=True)
    render_mosaic()


# ─────────────────────────────────────────────────────────────────────────────
# Fenêtre de liste des doublons
# ─────────────────────────────────────────────────────────────────────────────

def show_duplicates_window(parent, state, save_state, render_mosaic, refresh_tabs=None):
    """Ouvre la fenêtre non-modale listant les groupes de doublons."""
    dlg = _DuplicatesWindow(parent, state, save_state, render_mosaic, refresh_tabs)
    dlg.show_nonmodal()
    return dlg


class _DuplicatesWindow(AppWindowMixin, QDialog):

    def __init__(self, parent, state, save_state, render_mosaic, refresh_tabs=None):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("dialogs.duplicates.window_title")
        self._state         = state
        self._save_state    = save_state
        self._render_mosaic = render_mosaic
        self._refresh_tabs  = refresh_tabs

        self.setMinimumWidth(420)
        self.resize(460, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        self._lbl_empty = QLabel()
        self._lbl_empty.setWordWrap(True)
        self._lbl_empty.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_empty)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll_content = QWidget()
        self._scroll.setWidget(self._scroll_content)
        self._list_layout = QVBoxLayout(self._scroll_content)
        self._list_layout.setContentsMargins(4, 4, 4, 4)
        self._list_layout.setSpacing(10)
        layout.addWidget(self._scroll, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._btn_delete = QPushButton()
        self._btn_delete.clicked.connect(self._on_delete_clicked)
        self._btn_close = QPushButton()
        self._btn_close.clicked.connect(self.close)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_delete)
        btn_row.addWidget(self._btn_close)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._checkboxes = []  # list[(QCheckBox, real_idx, entry)]
        self._delete_confirm = None   # confirmation de suppression ouverte
        self._shown_key = None        # _groups_key de la liste affichée
        self._pending_scroll = None   # position de défilement à retrouver
        self._scroll.verticalScrollBar().rangeChanged.connect(self._on_scroll_range_changed)
        # Textes posés par _rebuild_list (_retranslate)
        self._rebuild_list()
        self._connect_language(apply_now=False)

    # ── Construction de la liste ─────────────────────────────────────────────────

    def _rebuild_list(self):
        # Cases cochées mémorisées par identité d'objet entry (jamais par position :
        # les real_idx se décalent après une suppression, une entrée héritant de la
        # position d'une autre serait cochée à tort → risque de suppression non voulue)
        previously_checked = {id(entry) for chk, real_idx, entry in self._checkboxes if chk.isChecked()}
        scroll_value = self._scroll.verticalScrollBar().value()

        # Tous les éléments, espace final compris : gardé, chaque reconstruction en
        # ajouterait un au-dessus des groupes, qui descendraient dans la fenêtre.
        while self._list_layout.count():
            w = self._list_layout.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
        self._checkboxes = []

        groups = get_duplicate_groups(self._state)
        self._shown_key = _groups_key(groups)
        show_path = _has_subdirectory_structure(self._state)

        self._scroll.setVisible(bool(groups))
        self._btn_delete.setVisible(bool(groups))
        self._lbl_empty.setVisible(not groups)

        for group_num, group in enumerate(groups, start=1):
            self._list_layout.addWidget(self._build_group_widget(group_num, group, show_path))

        if previously_checked:
            for chk, real_idx, entry in self._checkboxes:
                if id(entry) in previously_checked:
                    chk.setChecked(True)

        self._list_layout.addStretch(1)
        self._retranslate()
        self._update_delete_button_state()
        # La reconstruction ramène la liste en haut, et la zone ne connaît sa nouvelle
        # hauteur qu'un peu plus tard, en plusieurs étapes : la position se remet à
        # chacune (_on_scroll_range_changed). Oubliée ensuite : un simple
        # redimensionnement de la fenêtre ne doit pas la rétablir.
        if scroll_value and self._pending_scroll is None:
            self._pending_scroll = scroll_value
            QTimer.singleShot(200, self._forget_pending_scroll)

    def _on_scroll_range_changed(self, _minimum: int, maximum: int):
        if self._pending_scroll is not None:
            self._scroll.verticalScrollBar().setValue(min(self._pending_scroll, maximum))

    def _forget_pending_scroll(self):
        self._pending_scroll = None

    def _build_group_widget(self, group_num: int, group: list, show_path: bool) -> QWidget:
        from modules.qt.mosaic_canvas import _get_pixmap_for_size

        container = QFrame()
        container.setFrameShape(QFrame.StyledPanel)
        v = QVBoxLayout(container)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        title = QLabel(_("dialogs.duplicates.group_title", num=group_num, count=len(group)))
        title.setProperty("group_num", group_num)
        title.setProperty("group_count", len(group))
        title.setStyleSheet("font-weight: bold;")
        v.addWidget(title)

        # Boutons Sélectionner/Désélectionner le groupe — méthodes liées + sender()
        # uniquement : pas de lambda capturant des widgets du frame (une lambda
        # retenue par la connexion C++ d'un bouton enfant créerait un cycle Python
        # qui retarde la destruction du frame détaché au rebuild jusqu'au GC).
        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(8)

        btn_select = QPushButton()
        btn_select.setProperty("group_role", "select_group")
        btn_select.clicked.connect(self._on_group_select_clicked)
        buttons_row.addWidget(btn_select)

        btn_deselect = QPushButton()
        btn_deselect.setProperty("group_role", "deselect_group")
        btn_deselect.clicked.connect(self._on_group_deselect_clicked)
        buttons_row.addWidget(btn_deselect)

        buttons_row.addStretch(1)
        v.addLayout(buttons_row)

        for real_idx, entry in group:
            row = QHBoxLayout()
            row.setSpacing(8)

            thumb_lbl = QLabel()
            pm = _get_pixmap_for_size(entry, 45, 60)
            thumb_lbl.setPixmap(pm)
            thumb_lbl.setFixedSize(45, 60)
            row.addWidget(thumb_lbl)

            name = entry.get("orig_name", "")
            if not show_path:
                import os
                name = os.path.basename(name)
            chk = QCheckBox(_checkbox_text(name))
            chk.stateChanged.connect(self._update_delete_button_state)
            row.addWidget(chk, stretch=1)

            self._checkboxes.append((chk, real_idx, entry))
            v.addLayout(row)

        return container

    def _on_group_select_clicked(self):
        self._set_group_checked_from_sender(True)

    def _on_group_deselect_clicked(self):
        self._set_group_checked_from_sender(False)

    def _set_group_checked_from_sender(self, checked: bool):
        """Retrouve le QFrame de groupe du bouton cliqué via sender(), et coche/décoche
        ses checkboxes. Aucune référence capturée à la construction (voir commentaire
        dans _build_group_widget)."""
        btn = self.sender()
        if btn is None:
            return
        container = btn.parent()
        if container is None:
            return
        for chk in container.findChildren(QCheckBox):
            chk.setChecked(checked)
        self._update_delete_button_state()

    def _update_delete_button_state(self, _checkstate=None):
        has_selection = any(chk.isChecked() for chk, _real_idx, _entry in self._checkboxes)
        self._btn_delete.setEnabled(has_selection and self._delete_confirm is None)

    # ── Traduction + thème ─────────────────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        font = _get_current_font(10)

        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }} "
            f"QScrollArea {{ background: {theme['bg']}; border: none; }} "
            f"QFrame {{ color: {theme['text']}; }}"
        )
        self._scroll_content.setStyleSheet(f"background: {theme['bg']};")

        self._lbl_empty.setText(_("dialogs.duplicates.no_duplicates"))
        self._lbl_empty.setFont(font)
        self._lbl_empty.setStyleSheet(f"color: {theme['text']};")

        alt = theme.get("toolbar_bg", theme["bg"])
        sep = theme.get("separator", "#aaaaaa")
        disabled = theme.get("disabled", "#888888")
        btn_style = (
            f"QPushButton {{ background: {alt}; color: {theme['text']}; "
            f"border: 1px solid {sep}; padding: 6px 16px; }} "
            f"QPushButton:hover {{ background: {sep}; }} "
            f"QPushButton:disabled {{ color: {disabled}; }}"
        )
        group_btn_style = (
            f"QPushButton {{ background: {alt}; color: {theme['text']}; "
            f"border: 1px solid {sep}; padding: 3px 8px; }} "
            f"QPushButton:hover {{ background: {sep}; }}"
        )

        for i in range(self._list_layout.count()):
            item = self._list_layout.itemAt(i)
            container = item.widget()
            if not isinstance(container, QFrame):
                continue
            for child in container.findChildren(QLabel):
                child.setFont(font)
                group_num = child.property("group_num")
                if group_num is not None:
                    child.setText(_("dialogs.duplicates.group_title",
                                    num=group_num, count=child.property("group_count")))
                    child.setStyleSheet(f"font-weight: bold; color: {theme['text']};")
                else:
                    child.setStyleSheet(f"color: {theme['text']};")
            for child in container.findChildren(QCheckBox):
                child.setFont(font)
                child.setStyleSheet(f"color: {theme['text']};")
            for child in container.findChildren(QPushButton):
                role = child.property("group_role")
                if role == "select_group":
                    child.setText(_("dialogs.duplicates.select_group"))
                elif role == "deselect_group":
                    child.setText(_("dialogs.duplicates.deselect_group"))
                else:
                    continue
                child.setFont(font)
                child.setStyleSheet(group_btn_style)

        self._btn_delete.setText(_("dialogs.duplicates.delete_selection"))
        self._btn_delete.setFont(font)
        self._btn_delete.setStyleSheet(btn_style)
        self._btn_close.setText(_("buttons.close"))
        self._btn_close.setFont(font)
        self._btn_close.setStyleSheet(btn_style)

    # ── Actions ────────────────────────────────────────────────────────────────

    def _on_delete_clicked(self):
        pages = [entry for chk, _real_idx, entry in self._checkboxes if chk.isChecked()]
        if not pages or self._delete_confirm is not None:
            return

        from modules.qt.utils import format_file_size
        total_size = sum(len(page["bytes"]) for page in pages if page.get("bytes"))
        size_str = format_file_size(total_size) if total_size > 0 else ""

        from modules.qt.file_close_qt import DeleteConfirmDialog
        confirm = DeleteConfirmDialog(self, len(pages), size_str)

        def _do_delete():
            delete_pages(
                self._state, pages, self._save_state, self._render_mosaic, self._refresh_tabs
            )
            # La mosaïque redessinée a déjà mis la liste à jour (status_changed) :
            # refresh() ne la reconstruit pas une seconde fois.
            if get_duplicate_groups(self._state):
                self.refresh()
            else:
                self.close()

        # Bouton grisé tant que la question est ouverte : un second clic en ouvrirait
        # une autre, exactement au même endroit.
        self._delete_confirm = confirm
        self._update_delete_button_state()
        confirm.finished.connect(self._on_delete_confirm_closed)
        confirm.accepted.connect(_do_delete)
        confirm.show_nonmodal()

    def _on_delete_confirm_closed(self, _result=None):
        self._delete_confirm = None
        self._update_delete_button_state()

    # ── Mise à jour temps réel ─────────────────────────────────────────────────

    def refresh(self):
        """Appelée sur status_changed, donc à chaque clic dans la mosaïque : la liste
        n'est reconstruite que si ses doublons ont changé."""
        if _groups_key(get_duplicate_groups(self._state)) != self._shown_key:
            self._rebuild_list()
