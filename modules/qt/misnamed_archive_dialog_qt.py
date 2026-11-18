"""
modules/qt/misnamed_archive_dialog_qt.py — Question posée pour des archives
mal nommées (extension qui ne correspond pas au format réel) : les renommer,
ou non. Fenêtre commune, sous deux formes :
- un fichier (import de métadonnées en lot, qui ne peut pas le traiter sans
  corriger son extension), avec la case « faire de même pour les suivants » ;
- une liste (fin d'un scan de la Bibliothèque).

Règle de renommage et détection : misnamed_archives.py (sans Qt).
"""

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QLabel,
                               QListWidget, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from modules.qt.app_window_qt import QuestionWindowMixin
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.localization import _
from modules.qt.misnamed_archives import EXTENSION_OF_KIND
from modules.qt.state import get_current_theme

_WIDTH = 500
_LIST_HEIGHT = 180


def _new_extension(entry):
    """Extension donnée au fichier d'une entrée de `files` : la troisième
    valeur si elle est donnée, sinon celle du format réel."""
    return entry[2] if len(entry) > 2 else EXTENSION_OF_KIND[entry[1]]


class MisnamedArchiveDialog(QuestionWindowMixin, QDialog):
    """files : [(chemin, format réel « CBR »…)] des archives mal nommées, ou
    [(chemin, format réel, nouvelle extension)] quand l'extension donnée
    n'est pas celle d'EXTENSION_OF_KIND (lot « Vérifier les extensions » :
    « .zip », « .pdf »…).

    as_list=False (un seul fichier) : message_key avec {name}, {real},
    {declared} et {ext}, bouton « Renommer en {ext} ».
    as_list=True : message_key avec {count}, liste des fichiers (« nom → .ext »),
    bouton « Renommer ». marked_paths : fichiers de la liste précédés de
    « ⚠ », expliqués sous la liste par warning_key (avec {count}).
    checkable=True (avec as_list) : une case par fichier, toutes cochées au
    départ, boutons « Tout sélectionner » / « Tout désélectionner », bouton
    « Renommer la sélection », grisé si aucune case n'est cochée (modèle :
    fenêtre des doublons, duplicate_detection_qt).
    skip_key : texte du bouton qui ne renomme pas ; following_key : texte de
    la case « faire de même pour les suivants » (None : pas de case).

    Réponse (ask_async) : (choix, pour_les_suivants), choix « rename » ou
    « skip ». La croix et Échap répondent (« skip », False) : rien n'est
    renommé sans un clic sur Renommer. chosen_files : entrées de `files` à
    renommer, posées avant la réponse (attribut Python, lisible après la
    fermeture de la fenêtre) — les cases cochées, ou toute la liste sans
    checkable."""

    result_signal = Signal(object)

    def __init__(self, parent, files, message_key, skip_key, following_key=None, as_list=False,
                 marked_paths=(), warning_key=None, checkable=False):
        super().__init__(parent)
        self._setup_window(close_answer=("skip", False))
        self.set_window_title("dialogs.misnamed_archive.title")
        self._files = list(files)
        self._as_list = as_list
        self._message_key = message_key
        self._skip_key = skip_key
        self._following_key = following_key
        marked = {os.path.normcase(os.path.abspath(p)) for p in marked_paths}
        self._marked = [entry[0] for entry in self._files
                        if os.path.normcase(os.path.abspath(entry[0])) in marked]
        self._warning_key = warning_key
        self._checkable = bool(as_list and checkable)
        self.chosen_files = list(self._files)
        self.setFixedWidth(_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(12)

        # Nom du fichier venu du disque : texte brut
        self._lbl = QLabel()
        self._lbl.setTextFormat(Qt.PlainText)
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        layout.addWidget(self._lbl)

        self._list = None
        self._scroll = None
        self._checkboxes = []    # [(QCheckBox, entrée de files)]
        self._btn_select_all = self._btn_deselect_all = None
        if self._checkable:
            select_row = QHBoxLayout()
            select_row.setSpacing(8)
            self._btn_select_all = QPushButton()
            self._btn_select_all.clicked.connect(lambda: self._set_all_checked(True))
            self._btn_deselect_all = QPushButton()
            self._btn_deselect_all.clicked.connect(lambda: self._set_all_checked(False))
            select_row.addWidget(self._btn_select_all)
            select_row.addWidget(self._btn_deselect_all)
            select_row.addStretch(1)
            layout.addLayout(select_row)

            # Une case par fichier, dans une zone qui défile ; noms de
            # fichiers et extensions : rien à traduire
            self._scroll = QScrollArea()
            self._scroll.setWidgetResizable(True)
            self._scroll.setFixedHeight(_LIST_HEIGHT)
            self._scroll_content = QWidget()
            rows = QVBoxLayout(self._scroll_content)
            rows.setContentsMargins(6, 4, 6, 4)
            rows.setSpacing(2)
            for entry in self._files:
                mark = "⚠ " if entry[0] in self._marked else ""
                text = f"{mark}{os.path.basename(entry[0])}  →  {_new_extension(entry)}"
                # Qt lit un « & » comme la marque d'un raccourci clavier
                chk = QCheckBox(text.replace("&", "&&"))
                chk.setChecked(True)
                chk.toggled.connect(self._update_rename_state)
                rows.addWidget(chk)
                self._checkboxes.append((chk, entry))
            rows.addStretch(1)
            self._scroll.setWidget(self._scroll_content)
            layout.addWidget(self._scroll)
        elif as_list:
            # Noms de fichiers et extensions : rien à traduire
            self._list = QListWidget()
            self._list.setSelectionMode(QAbstractItemView.NoSelection)
            self._list.setFocusPolicy(Qt.NoFocus)
            self._list.setFixedHeight(_LIST_HEIGHT)
            for entry in self._files:
                mark = "⚠ " if entry[0] in self._marked else ""
                self._list.addItem(
                    f"{mark}{os.path.basename(entry[0])}  →  {_new_extension(entry)}")
            layout.addWidget(self._list)

        # Avertissement non bloquant (fichiers marqués) : couleur du texte,
        # gras italique
        self._warning_lbl = None
        if self._marked and warning_key:
            self._warning_lbl = QLabel()
            self._warning_lbl.setTextFormat(Qt.PlainText)
            self._warning_lbl.setAlignment(Qt.AlignCenter)
            self._warning_lbl.setWordWrap(True)
            layout.addWidget(self._warning_lbl)

        self._chk_following = None
        if following_key:
            self._chk_following = QCheckBox()
            layout.addWidget(self._chk_following, alignment=Qt.AlignCenter)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_rename = QPushButton()
        self._btn_skip = QPushButton()
        self._btn_rename.clicked.connect(self._on_rename)
        self._btn_skip.clicked.connect(lambda: self._answer(("skip", self._following())))
        btn_row.addWidget(self._btn_rename)
        btn_row.addSpacing(16)
        btn_row.addWidget(self._btn_skip)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._connect_language()
        self._btn_rename.setFocus()

    def _following(self):
        return bool(self._chk_following is not None and self._chk_following.isChecked())

    def _on_rename(self):
        """Renommer : les fichiers à renommer (chosen_files) sont posés avant
        la réponse, que l'appelant lit dans sa suite."""
        if self._checkable:
            self.chosen_files = [entry for chk, entry in self._checkboxes if chk.isChecked()]
            if not self.chosen_files:
                return
        self._answer(("rename", self._following()))

    def _set_all_checked(self, checked):
        for chk, _entry in self._checkboxes:
            chk.setChecked(checked)
        self._update_rename_state()

    def _update_rename_state(self, _checked=None):
        """Renommer la sélection : grisé tant qu'aucune case n'est cochée."""
        if self._checkable:
            self._btn_rename.setEnabled(any(chk.isChecked() for chk, _entry in self._checkboxes))

    def _apply_theme(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
            f"QLabel {{ color: {theme['text']}; }}"
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 12px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
            f"QPushButton:disabled {{ color: {theme['separator']}; }}"
        )
        if self._scroll is not None:
            self._scroll.setStyleSheet(
                f"QScrollArea {{ background: {theme['bg']}; "
                f"border: 1px solid {theme['separator']}; }}")
            self._scroll_content.setStyleSheet(f"background: {theme['bg']};")
            for chk, _entry in self._checkboxes:
                chk.setStyleSheet(f"QCheckBox {{ color: {theme['text']}; }}")
        if self._list is not None:
            self._list.setStyleSheet(
                f"QListWidget {{ background: {theme['bg']}; color: {theme['text']}; "
                f"border: 1px solid {theme['separator']}; }}")
        # Case stylée sur elle-même, jamais par la feuille de style de la
        # fenêtre (une info-bulle en hériterait une ligne vide)
        if self._chk_following is not None:
            self._chk_following.setStyleSheet(f"QCheckBox {{ color: {theme['text']}; }}")

    def _retranslate(self):
        font = _get_current_font(10)
        if self._checkable:
            self._lbl.setText(_(self._message_key, count=len(self._files)))
            self._btn_rename.setText(_("dialogs.misnamed_archive.rename_selection_button"))
            self._btn_select_all.setText(_("menu.select_all"))
            self._btn_deselect_all.setText(_("dialogs.misnamed_archive.deselect_all_button"))
            for btn in (self._btn_select_all, self._btn_deselect_all):
                btn.setFont(_get_current_font(9))
            for chk, _entry in self._checkboxes:
                chk.setFont(_get_current_font(9))
        elif self._as_list:
            self._lbl.setText(_(self._message_key, count=len(self._files)))
            self._btn_rename.setText(_("dialogs.misnamed_archive.rename_all_button"))
            self._list.setFont(_get_current_font(9))
        else:
            path, real_kind = self._files[0][:2]
            ext = _new_extension(self._files[0])
            self._lbl.setText(_(self._message_key, name=os.path.basename(path), real=real_kind,
                                declared=os.path.splitext(path)[1].lstrip(".").upper(), ext=ext))
            self._btn_rename.setText(_("dialogs.misnamed_archive.rename_button", ext=ext))
        self._lbl.setFont(font)
        # QLabel à retour à la ligne dans une fenêtre de largeur fixe : sa
        # hauteur est imposée, sinon adjustSize couperait les dernières lignes
        margins = self.layout().contentsMargins()
        width = _WIDTH - margins.left() - margins.right()
        self._lbl.setMinimumHeight(self._lbl.heightForWidth(width))
        if self._warning_lbl is not None:
            warn_font = _get_current_font(10)
            warn_font.setBold(True)
            warn_font.setItalic(True)
            self._warning_lbl.setFont(warn_font)
            self._warning_lbl.setStyleSheet(f"color: {get_current_theme()['text']};")
            # Même marque que les fichiers de la liste, posée par le code
            self._warning_lbl.setText("⚠ " + _(self._warning_key, count=len(self._marked)))
            self._warning_lbl.setMinimumHeight(self._warning_lbl.heightForWidth(width))
        if self._chk_following is not None:
            self._chk_following.setText(_(self._following_key))
            self._chk_following.setFont(_get_current_font(9))
        self._btn_skip.setText(_(self._skip_key))
        for btn in (self._btn_rename, self._btn_skip):
            btn.setFont(font)
        self.adjustSize()
