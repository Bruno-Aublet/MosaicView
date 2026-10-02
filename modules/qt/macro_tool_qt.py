"""
modules/qt/macro_tool_qt.py — Outil "macros" de la barre d'outils flottante
de la visionneuse principale : enregistrer/rejouer une séquence d'actions
faites sur une page.

Deux icônes séparées (pas de bi-mode) : Enregistrer/Lire, actions instantanées
sans geste souris ni overlay canvas (comme rotation_tool_qt.py). Chaque
perform_xxx capturable appelle self._macro_record_step(...) en fin de commit
— no-op si aucun enregistrement n'est en cours. Chaque étape est capturée en
pixels/valeurs absolues fixes, jamais recalculées à l'échelle de la page
cible à la lecture (sauf le redressement automatique, dont l'angle dépend du
contenu de chaque page — voir straighten_tool_qt.py::perform_auto_straighten).
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton,
    QLabel, QLineEdit, QWidget, QScrollArea, QFrame,
)
from PySide6.QtCore import Qt, QTimer

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.dialogs_qt import position_dialog_on_parent, _center_on_widget


def _viewer_state(viewer):
    from modules.qt import state as _state_module
    return viewer.callbacks.get('state') or _state_module.state


def _macro_busy_on_state(state) -> bool:
    """True si une visionneuse ouverte sur `state` (le document d'un panneau)
    lit ou enregistre une macro. Import différé : image_viewer_qt importe ce
    module au niveau module."""
    from modules.qt.image_viewer_qt import image_viewer_refs
    for viewer in list(image_viewer_refs):
        try:
            if _viewer_state(viewer) is state and \
                    (viewer._macro_reading or viewer._macro_recording):
                return True
        except RuntimeError:
            continue
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Verrou d'interaction — visionneuse bridée pendant une lecture de macro
# ─────────────────────────────────────────────────────────────────────────────

class _MacroReadLockOverlay(QWidget):
    """Widget transparent posé en enfant du canvas, redimensionné pour le
    recouvrir entièrement, actif uniquement pendant une lecture de macro —
    avale tous les événements souris avant qu'ils n'atteignent le canvas,
    sans toucher au code existant de chaque outil. La visionneuse reste
    visible (l'utilisateur voit les pages/changements défiler), seule
    l'interaction est bloquée."""

    def __init__(self, canvas):
        super().__init__(canvas)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setCursor(Qt.ArrowCursor)
        self.hide()

    def mousePressEvent(self, event):
        event.accept()

    def mouseMoveEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def mouseDoubleClickEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        event.accept()


# ─────────────────────────────────────────────────────────────────────────────
# Fenêtre "Enregistrer" — non modale, liste live des étapes capturées
# ─────────────────────────────────────────────────────────────────────────────

class _MacroRecordDialog(QDialog):
    """Fenêtre non modale affichée pendant l'enregistrement : liste en direct
    des étapes capturées, bouton Stop (grisé tant qu'aucune étape n'existe) et
    bouton Annuler. Pattern non-modal standard du projet (dialogs_qt.py)."""

    def __init__(self, parent, viewer):
        super().__init__(parent)
        # WindowStaysOnTopHint : le passage en plein écran de la visionneuse
        # (Qt.Window, non lié hiérarchiquement à ce dialogue) la placerait
        # sinon au-dessus de tout le bureau, rendant cette fenêtre inaccessible.
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self._center_parent = parent
        self._viewer = viewer

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        self._list = QListWidget()
        self._list.setMinimumSize(360, 180)
        layout.addWidget(self._list)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_cancel = QPushButton()
        self._btn_cancel.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self._btn_cancel)
        self._btn_stop = QPushButton()
        self._btn_stop.clicked.connect(self._on_stop_clicked)
        btn_row.addWidget(self._btn_stop)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        self._apply_font()
        self.refresh_steps()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _l: (self._retranslate(), self._apply_font())
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    def show_nonmodal(self):
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass
        # Fermeture par la croix ou Échap (finished émis par reject) : même
        # effet que le bouton Annuler. Sans ça, l'enregistrement resterait
        # actif sans fenêtre pour le terminer — navigation bloquée et bouton
        # Lire grisé jusqu'à la fermeture de la visionneuse. "Terminer" et
        # "Annuler" ont déjà détaché ce dialogue du viewer avant de le fermer.
        viewer = self._viewer
        if getattr(viewer, '_macro_recording', False) and \
                getattr(viewer, '_macro_record_dialog', None) is self:
            try:
                viewer._macro_cancel_recording()
            except RuntimeError:
                pass

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }} "
            f"QListWidget {{ background: {theme['bg']}; color: {theme['text']}; "
            f"border: 1px solid {theme['separator']}; }} "
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 14px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }} "
            f"QPushButton:disabled {{ color: {theme['disabled']}; }}"
        )
        self.setWindowTitle(_wt("dialogs.macro_record.title"))
        self._btn_cancel.setText(_("dialogs.macro_record.btn_cancel"))
        self._btn_stop.setText(_("dialogs.macro_record.btn_stop"))
        self.refresh_steps()

    def _apply_font(self):
        try:
            from modules.qt.font_manager_qt import get_current_font
            font = get_current_font()
            self._list.setFont(font)
            self._btn_cancel.setFont(font)
            self._btn_stop.setFont(font)
        except Exception:
            pass

    def refresh_steps(self):
        """Reconstruit la liste depuis self._viewer._macro_steps ; les labels
        sont résolus dynamiquement à chaque affichage, jamais figés en texte
        (label_key/label_args plutôt qu'une phrase déjà résolue), pour rester
        dans la langue active même après un changement de langue."""
        self._list.clear()
        for step in self._viewer._macro_steps:
            label = _(step["label_key"], **step.get("label_args", {}))
            self._list.addItem(QListWidgetItem(label))
        has_steps = len(self._viewer._macro_steps) > 0
        self._btn_stop.setEnabled(has_steps)

    def _on_cancel_clicked(self):
        self._viewer._macro_cancel_recording()
        self.close()

    def _on_stop_clicked(self):
        if not self._viewer._macro_steps:
            return
        self._viewer._macro_stop_recording(self)

# ─────────────────────────────────────────────────────────────────────────────
# Fenêtre de saisie nom + description — affichée après le clic sur "Stop"
# ─────────────────────────────────────────────────────────────────────────────

class _MacroNameDialog(QDialog):
    """Demande le nom obligatoire (voir macro_engine.validate_macro_name) et
    une description optionnelle, avant de sauvegarder la macro. on_saved(macro)
    est appelé après une sauvegarde réussie, avant la fermeture.

    rename=True (bouton Renommer) : l'ancien fichier existing_name est retiré
    après écriture sous le nouveau nom (macro_engine.rename_macro). Sinon
    ("Compléter"), changer le nom crée une nouvelle macro et garde l'ancienne.

    confirm_discard=True (fin d'un enregistrement) : fermer sans enregistrer
    (Annuler, croix, Échap) demande confirmation, les étapes enregistrées
    n'existant nulle part ailleurs."""

    def __init__(self, parent, steps: list, on_saved, existing_name: str | None = None,
                 existing_description: str = "", rename: bool = False,
                 confirm_discard: bool = False):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self._center_parent = parent
        self._steps = steps
        self._on_saved = on_saved
        self._existing_name = existing_name
        self._rename = rename
        self._confirm_discard = confirm_discard
        self._saved = False
        self._discard_confirmed = False
        self._discard_dialog = None
        self._status_text_fn = lambda: ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        self._lbl_name = QLabel()
        self._lbl_name.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_name)
        self._edit_name = QLineEdit()
        self._edit_name.setMinimumWidth(320)
        if existing_name:
            self._edit_name.setText(existing_name)
        layout.addWidget(self._edit_name)

        self._lbl_desc = QLabel()
        self._lbl_desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_desc)
        self._edit_desc = QLineEdit()
        self._edit_desc.setMinimumWidth(320)
        self._edit_desc.setText(existing_description)
        layout.addWidget(self._edit_desc)

        self._lbl_status = QLabel(" ")
        self._lbl_status.setWordWrap(True)
        self._lbl_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_status)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_cancel = QPushButton()
        self._btn_cancel.clicked.connect(self.close)
        btn_row.addWidget(self._btn_cancel)
        self._btn_save = QPushButton()
        self._btn_save.setDefault(True)
        self._btn_save.clicked.connect(self._on_save_clicked)
        btn_row.addWidget(self._btn_save)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        self._apply_font()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _l: (self._retranslate(), self._apply_font())
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    def show_nonmodal(self):
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }} "
            f"QLabel  {{ color: {theme['text']}; }} "
            f"QLineEdit {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid {theme['separator']}; padding: 4px 6px; }} "
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 14px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self.setWindowTitle(_wt("dialogs.macro_record.name_dialog_title"))
        self._lbl_name.setText(_("dialogs.macro_record.name_label"))
        self._edit_name.setPlaceholderText(_("dialogs.macro_record.name_placeholder"))
        self._lbl_desc.setText(_("dialogs.macro_record.description_label"))
        self._edit_desc.setPlaceholderText(_("dialogs.macro_record.description_placeholder"))
        self._btn_cancel.setText(_("dialogs.macro_record.btn_cancel"))
        self._btn_save.setText(_("dialogs.macro_record.btn_save"))
        self._lbl_status.setText(self._status_text_fn())
        self._lbl_status.setStyleSheet(f"color: {theme.get('error', '#cc0000')};")

    def _apply_font(self):
        try:
            from modules.qt.font_manager_qt import get_current_font
            font = get_current_font()
            for w in (self._lbl_name, self._edit_name, self._lbl_desc,
                      self._edit_desc, self._lbl_status, self._btn_cancel, self._btn_save):
                w.setFont(font)
        except Exception:
            pass

    def _set_status(self, status_fn):
        self._status_text_fn = status_fn
        self._lbl_status.setText(status_fn())

    def _on_save_clicked(self):
        from modules.qt import macro_engine

        name = self._edit_name.text().strip()
        description = self._edit_desc.text().strip()

        existing_names = macro_engine.list_macro_names()
        if self._existing_name:
            existing_names.discard(self._existing_name)
        ok, error_key = macro_engine.validate_macro_name(name, existing_names=existing_names)
        if not ok:
            self._set_status(lambda k=error_key: _(k))
            return

        macro = {"name": name, "description": description, "steps": self._steps}
        if self._rename and self._existing_name:
            macro_engine.rename_macro(self._existing_name, macro)
        else:
            macro_engine.save_macro(macro)
        self._saved = True
        if self._on_saved:
            self._on_saved(macro)
        self.close()

    def reject(self):
        """Point de passage commun du bouton Annuler (close), de la croix
        (QDialog.closeEvent appelle reject) et d'Échap : une macro tout juste
        enregistrée et jamais sauvegardée n'est abandonnée qu'après
        confirmation."""
        if self._confirm_discard and not self._saved and not self._discard_confirmed:
            self._ask_discard()
            return
        super().reject()

    def _ask_discard(self):
        from modules.qt.dialogs_qt import ConfirmYNDialog
        if self._discard_dialog is not None:
            try:
                self._discard_dialog.raise_()
                self._discard_dialog.activateWindow()
                return
            except RuntimeError:
                self._discard_dialog = None
        dlg = ConfirmYNDialog(
            self,
            lambda: _wt("dialogs.macro_record.discard_title"),
            lambda: _("dialogs.macro_record.discard_message"),
        )
        dlg.result_signal.connect(self._on_discard_answer)
        self._discard_dialog = dlg
        # Positionné avant show() : voir _MacroReadDialog._on_delete_clicked.
        position_dialog_on_parent(dlg, self)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_discard_answer(self, confirmed: bool):
        self._discard_dialog = None
        if confirmed:
            self._discard_confirmed = True
            self.close()


# ─────────────────────────────────────────────────────────────────────────────
# Fenêtre "Lire" — disposition côte à côte (liste + détail)
# ─────────────────────────────────────────────────────────────────────────────

class _MacroReadDialog(QDialog):
    """Liste des macros à gauche, détail (titre/description/étapes) à droite,
    boutons Renommer/Supprimer/Compléter/Lire agissant sur la macro
    sélectionnée. Un fichier illisible dans le dossier macros est signalé par
    un avertissement, pas ignoré silencieusement (voir macro_engine.list_macros).

    entries=None : lit sur la page active de `viewer`. entries=liste : mode
    mosaïque, lit sur chaque entrée de la liste (voir panel_widget.py).

    viewer : une ImageViewer déjà ouverte et visible (bouton "Lire" de la
    barre d'outils). viewer_factory : callable créant et affichant l'
    ImageViewer à la demande, invoquée seulement au clic sur Lire/Compléter
    (point d'entrée mosaïque — la visionneuse ne doit s'ouvrir qu'au début
    d'une lecture effective, jamais pour le seul choix d'une macro).

    state : AppState du panneau visé (mosaïque) ; déduit de viewer sinon."""

    def __init__(self, parent, viewer=None, entries=None, viewer_factory=None, mosaic_canvas=None,
                 state=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self._center_parent = parent
        self._viewer = viewer
        self._viewer_factory = viewer_factory
        self._mosaic_canvas = mosaic_canvas
        self._entries = entries
        self._state = state if state is not None else _viewer_state(viewer)
        self._macros = []
        self._selected_name = None
        # Visionneuse fermée pendant que cette fenêtre reste ouverte : Lire/
        # Compléter s'appliqueraient sinon à un objet Qt déjà détruit.
        if viewer is not None:
            viewer.destroyed.connect(self.close)
        # Fichier du panneau fermé pendant que cette fenêtre reste ouverte :
        # elle se ferme avec lui (force_close_file → cancel_operations_on_
        # close), ses pages ne désignant plus rien. Référence conservée pour
        # se désinscrire avec le même objet dans _on_close.
        self._file_close_canvas = (mosaic_canvas if mosaic_canvas is not None
                                   else viewer.callbacks.get('canvas') if viewer is not None
                                   else None)
        self._close_on_file_close = self.close
        if self._file_close_canvas is not None:
            from modules.qt.utils import register_cancel_on_close
            register_cancel_on_close(self._file_close_canvas, self._close_on_file_close)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        self._lbl_hint = QLabel()
        self._lbl_hint.setWordWrap(True)
        layout.addWidget(self._lbl_hint)

        row = QHBoxLayout()
        row.setSpacing(10)

        list_col = QVBoxLayout()
        list_col.setSpacing(6)
        self._list = QListWidget()
        self._list.setFixedWidth(220)
        self._list.currentRowChanged.connect(self._on_selection_changed)
        list_col.addWidget(self._list)
        self._btn_pick_file = QPushButton()
        self._btn_pick_file.clicked.connect(self._on_pick_file_clicked)
        list_col.addWidget(self._btn_pick_file)
        row.addLayout(list_col)

        detail_col = QVBoxLayout()
        detail_col.setSpacing(6)
        self._lbl_name = QLabel()
        self._lbl_name.setWordWrap(True)
        detail_col.addWidget(self._lbl_name)
        self._lbl_desc = QLabel()
        self._lbl_desc.setWordWrap(True)
        detail_col.addWidget(self._lbl_desc)
        self._steps_list = QListWidget()
        detail_col.addWidget(self._steps_list, stretch=1)

        btn_row = QHBoxLayout()
        self._btn_rename = QPushButton()
        self._btn_rename.clicked.connect(self._on_rename_clicked)
        btn_row.addWidget(self._btn_rename)
        self._btn_delete = QPushButton()
        self._btn_delete.setObjectName("macroDeleteBtn")
        self._btn_delete.clicked.connect(self._on_delete_clicked)
        btn_row.addWidget(self._btn_delete)
        self._btn_complete = QPushButton()
        self._btn_complete.clicked.connect(self._on_complete_clicked)
        btn_row.addWidget(self._btn_complete)
        self._btn_play = QPushButton()
        self._btn_play.setObjectName("macroPlayBtn")
        self._btn_play.setDefault(True)
        self._btn_play.clicked.connect(self._on_play_clicked)
        btn_row.addWidget(self._btn_play)
        detail_col.addLayout(btn_row)

        row.addLayout(detail_col, stretch=1)
        layout.addLayout(row)

        self.setMinimumSize(640, 320)
        self._reload_macros()
        self._retranslate()
        self._apply_font()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _l: (self._retranslate(), self._apply_font())
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    def show_nonmodal(self):
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass
        if self._file_close_canvas is not None:
            from modules.qt.utils import unregister_cancel_on_close
            unregister_cancel_on_close(self._file_close_canvas, self._close_on_file_close)

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }} "
            f"QLabel  {{ color: {theme['text']}; }} "
            f"QListWidget {{ background: {theme['bg']}; color: {theme['text']}; "
            f"border: 1px solid {theme['separator']}; }} "
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 12px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }} "
            f"QPushButton:disabled {{ color: {theme['disabled']}; }} "
            f"QPushButton#macroPlayBtn {{ background: #99ff99; color: #000000; }} "
            f"QPushButton#macroPlayBtn:hover {{ background: #77ff77; }} "
            f"QPushButton#macroDeleteBtn {{ background: #ff9999; color: #000000; }} "
            f"QPushButton#macroDeleteBtn:hover {{ background: #ff7777; }}"
        )
        self.setWindowTitle(_wt("dialogs.macro_read.title"))
        self._lbl_hint.setText(_("dialogs.macro_read.hint_record_from_viewer"))
        self._btn_rename.setText(_("dialogs.macro_read.btn_rename"))
        self._btn_delete.setText(_("dialogs.macro_read.btn_delete"))
        self._btn_complete.setText(_("dialogs.macro_read.btn_complete"))
        self._btn_play.setText(_("dialogs.macro_read.btn_play"))
        self._btn_pick_file.setText(_("dialogs.macro_read.btn_pick_file"))
        self._refresh_detail()

    def _apply_font(self):
        try:
            from modules.qt.font_manager_qt import get_current_font
            font = get_current_font()
            for w in (self._lbl_hint, self._list, self._lbl_name, self._lbl_desc, self._steps_list,
                      self._btn_rename, self._btn_delete, self._btn_complete, self._btn_play,
                      self._btn_pick_file):
                w.setFont(font)
        except Exception:
            pass

    def _reload_macros(self):
        from modules.qt import macro_engine
        from modules.qt.dialogs_qt import MsgDialog

        self._macros, errors = macro_engine.list_macros()
        self._list.clear()
        for macro in self._macros:
            self._list.addItem(QListWidgetItem(macro["name"]))
        if self._macros:
            self._list.setCurrentRow(0)
        if errors:
            dlg = MsgDialog(self._center_parent, "dialogs.macro_read.corrupt_title",
                            "dialogs.macro_read.corrupt_message",
                            message_kwargs={"files": ", ".join(errors)})
            dlg.show_nonmodal()

    def _current_macro(self):
        row = self._list.currentRow()
        if 0 <= row < len(self._macros):
            return self._macros[row]
        return None

    def _on_selection_changed(self, row):
        self._refresh_detail()

    def _refresh_detail(self):
        macro = self._current_macro()
        has_macro = macro is not None
        self._btn_rename.setEnabled(has_macro)
        self._btn_delete.setEnabled(has_macro)
        self._btn_complete.setEnabled(has_macro)
        self._btn_play.setEnabled(has_macro)
        self._steps_list.clear()
        if not has_macro:
            self._lbl_desc.setText("")
            if not self._macros:
                self._lbl_name.setAlignment(Qt.AlignCenter)
                self._lbl_name.setText(_("dialogs.macro_read.no_macros"))
            else:
                self._lbl_name.setText("")
            return
        self._lbl_name.setAlignment(Qt.AlignLeft)
        self._lbl_name.setText(macro["name"])
        self._lbl_desc.setText(macro.get("description", ""))
        for step in macro["steps"]:
            label = _(step["label_key"], **step.get("label_args", {}))
            self._steps_list.addItem(QListWidgetItem(f"• {label}"))

    def _on_pick_file_clicked(self):
        import os
        from PySide6.QtWidgets import QFileDialog
        from modules.qt import macro_engine
        from modules.qt.localization import _wt

        macros_dir = macro_engine.get_macros_dir()
        filepath, _filter = QFileDialog.getOpenFileName(
            self, _wt("dialogs.macro_read.pick_file_title"),
            macros_dir, "*.json",
        )
        if not filepath:
            return

        name = os.path.splitext(os.path.basename(filepath))[0]
        for row, macro in enumerate(self._macros):
            if macro["name"] == name:
                self._list.setCurrentRow(row)
                return

    def _on_rename_clicked(self):
        macro = self._current_macro()
        if macro is None:
            return
        dlg = _MacroNameDialog(
            self._center_parent, macro["steps"], on_saved=lambda _m: self._reload_macros(),
            existing_name=macro["name"], existing_description=macro.get("description", ""),
            rename=True,
        )
        dlg.show_nonmodal()

    def _on_delete_clicked(self):
        """Suppression définitive du fichier de la macro : confirmation
        Oui/Non non modale, la suppression n'a lieu qu'à la réponse Oui."""
        from modules.qt.dialogs_qt import ConfirmYNDialog
        macro = self._current_macro()
        if macro is None:
            return
        name = macro["name"]
        dlg = ConfirmYNDialog(
            self,
            lambda: _wt("dialogs.macro_read.delete_confirm_title"),
            lambda n=name: _("dialogs.macro_read.delete_confirm_message", name=n),
        )
        dlg.result_signal.connect(lambda confirmed, n=name: self._delete_macro(n) if confirmed else None)
        # Positionné avant show() : le centrage différé propre à
        # ConfirmYNDialog (showEvent) laisserait sinon un flash à la
        # position par défaut.
        position_dialog_on_parent(dlg, self)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _delete_macro(self, name: str):
        from modules.qt import macro_engine
        macro_engine.delete_macro(name)
        self._reload_macros()

    def _ensure_viewer(self):
        if self._viewer is None and self._viewer_factory is not None:
            # Créer/afficher l'ImageViewer peut être lent (chargement de page
            # depuis l'archive) — sans retour visuel immédiat, l'utilisateur
            # croit son clic ignoré. item_holder local : ce label n'a besoin
            # de vivre que le temps de cet appel.
            item_holder = [None]
            if self._mosaic_canvas is not None:
                from modules.qt.canvas_overlay_qt import show_canvas_text, hide_canvas_text
                show_canvas_text(self._mosaic_canvas, _("labels.macro_preparing"), item_holder)
                from PySide6.QtWidgets import QApplication
                QApplication.processEvents()
            self._viewer = self._viewer_factory()
            if self._viewer is not None:
                self._viewer._macro_read_transient_viewer = True
            if self._mosaic_canvas is not None:
                hide_canvas_text(self._mosaic_canvas, item_holder)
        return self._viewer

    def _refuse_if_busy(self) -> bool:
        """True (et message affiché, cette fenêtre restant ouverte) si une
        lecture ou un enregistrement est déjà en cours sur ce document, dans
        n'importe quelle visionneuse : une seconde lecture imbriquée
        écraserait les données de restauration de la première, et des pages
        modifiées pendant un enregistrement désaligneraient ses étapes de
        l'historique."""
        if not _macro_busy_on_state(self._state):
            return False
        from modules.qt.dialogs_qt import MsgDialog
        MsgDialog(self._center_parent, "dialogs.macro_read.title",
                  "dialogs.macro_read.busy_message").show_nonmodal()
        return True

    def _on_complete_clicked(self):
        macro = self._current_macro()
        if macro is None:
            return
        if self._refuse_if_busy():
            return
        self.close()
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        viewer = self._ensure_viewer()
        if viewer is None:
            return
        viewer._macro_complete_existing(macro)

    def _on_play_clicked(self):
        macro = self._current_macro()
        if macro is None:
            return
        if self._refuse_if_busy():
            return
        # Travail non validé dans la visionneuse (texte, formes, image
        # collée, transparence...) : les étapes de la macro l'effaceraient
        # ou le valideraient avec elles. Une visionneuse créée pour cette
        # lecture (mosaïque) n'en a jamais.
        if self._viewer is not None and self._viewer._has_unvalidated_work():
            from modules.qt.dialogs_qt import MsgDialog
            MsgDialog(self._center_parent, "viewer.unvalidated_work_title",
                      "viewer.macro_read_unvalidated_work_message").show_nonmodal()
            return
        self.close()
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        viewer = self._ensure_viewer()
        if viewer is None:
            return
        viewer._macro_run(macro, self._entries)


# ─────────────────────────────────────────────────────────────────────────────
# Fenêtre de rapport final — détail des pages en échec/partiel + raison
# ─────────────────────────────────────────────────────────────────────────────

class _MacroReportDialog(QDialog):
    """Résumé final : comptes + liste des pages en échec/partiel avec l'étape fautive.
    note_key : clé d'une remarque affichée sous les comptes (lecture arrêtée
    avant la fin), None sinon."""

    def __init__(self, parent, n_ok: int, failed: list, partial: list, note_key: str | None = None):
        super().__init__(parent)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self._center_parent = parent
        self._n_ok = n_ok
        self._failed = failed
        self._partial = partial
        self._note_key = note_key
        self.resize(520, 320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg_lbl)

        self._note_lbl = QLabel()
        self._note_lbl.setWordWrap(True)
        self._note_lbl.setAlignment(Qt.AlignCenter)
        self._note_lbl.setVisible(note_key is not None)
        layout.addWidget(self._note_lbl)

        self._list_scroll = None
        self._list_labels = []
        rows = list(failed) + list(partial)
        if rows:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.StyledPanel)
            scroll.setMinimumHeight(min(240, 30 + 20 * len(rows)))
            content = QWidget()
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(8, 6, 8, 6)
            content_layout.setSpacing(2)
            for row in rows:
                lbl = QLabel()
                lbl.setWordWrap(True)
                lbl.setAlignment(Qt.AlignCenter)
                content_layout.addWidget(lbl)
                self._list_labels.append((lbl, row))
            content_layout.addStretch()
            scroll.setWidget(content)
            layout.addWidget(scroll, stretch=1)
            self._list_scroll = scroll

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok_btn = QPushButton()
        self._ok_btn.setFixedWidth(100)
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._ok_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _l: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
        self._ok_btn.setFocus()

    def show_nonmodal(self):
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def _step_label(self, step):
        if step is None:
            return ""
        return _(step["label_key"], **step.get("label_args", {}))

    def _retranslate(self):
        from modules.qt.font_manager_qt import get_current_font
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )

        self.setWindowTitle(_wt("dialogs.macro_read.report_title"))
        self._msg_lbl.setText(_("dialogs.macro_read.report_message").format(
            ok=self._n_ok, partial=len(self._partial), failed=len(self._failed)))
        self._msg_lbl.setFont(font)
        self._msg_lbl.setMinimumHeight(self._msg_lbl.heightForWidth(self.width() - 40))

        # Avertissement non bloquant : couleur de texte du thème en gras
        # italique, jamais de couleur vive (CLAUDE.md, détails de style).
        if self._note_key is not None:
            note_font = get_current_font(10, bold=True)
            note_font.setItalic(True)
            self._note_lbl.setFont(note_font)
            self._note_lbl.setStyleSheet(f"color: {theme['text']};")
            self._note_lbl.setText(_(self._note_key))
            self._note_lbl.setMinimumHeight(self._note_lbl.heightForWidth(self.width() - 40))

        for lbl, row in self._list_labels:
            if len(row) == 2:
                page_name, failed_step = row
                lbl.setText(_("dialogs.macro_read.report_failed_line").format(
                    page=page_name, step=self._step_label(failed_step)))
            else:
                page_name, applied, failed_step = row
                lbl.setText(_("dialogs.macro_read.report_partial_line").format(
                    page=page_name, applied=applied, step=self._step_label(failed_step)))
            lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)


# ─────────────────────────────────────────────────────────────────────────────
# Mixin canvas — état de l'outil (hérité par _ViewerCanvas)
# ─────────────────────────────────────────────────────────────────────────────

class MacroCanvasMixin:
    """Hérité par _ViewerCanvas. Volontairement vide : cet outil n'a aucun
    overlay ni geste souris sur le canvas (comme rotation/color_depth)."""

    def _init_macro_state(self):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Mixin viewer — enregistrement/lecture (hérité par ImageViewer)
# ─────────────────────────────────────────────────────────────────────────────

class MacroViewerMixin:
    """Hérité par ImageViewer. self._macro_recording/_macro_reading pilotent
    le grisage réciproque des 2 boutons (voir
    _ViewerToolbar.refresh_macro_buttons_state)."""

    def _macro_set_locked_for_reading(self, locked: bool):
        """Bride/débride la visionneuse pendant une lecture de macro : barre
        d'outils masquée, verrou souris posé sur le canvas, raccourcis
        clavier désactivés — seule la croix de fermeture de la fenêtre reste
        utilisable."""
        if locked:
            self._toolbar.hide()
            self._macro_lock_overlay.resize(self._canvas.size())
            self._macro_lock_overlay.show()
            self._macro_lock_overlay.raise_()
        else:
            self._macro_lock_overlay.hide()
        for sc in self._macro_lockable_shortcuts:
            sc.setEnabled(not locked)

    def open_macro_record_dialog(self):
        if self._macro_recording or self._macro_reading:
            return
        # Une autre visionneuse lit ou enregistre déjà une macro sur ce
        # document (lecture depuis la mosaïque, par exemple) : ses commits
        # désaligneraient les étapes de cet enregistrement de l'historique.
        if _macro_busy_on_state(_viewer_state(self)):
            from modules.qt.dialogs_qt import MsgDialog
            MsgDialog(self._center_parent, "dialogs.macro_record.title",
                      "dialogs.macro_read.busy_message").show_nonmodal()
            return
        if self.page_mode != "single":
            self.page_mode = "single"
            self.display_image()
        self._macro_recording = True
        self._macro_steps = []
        self._macro_step_snapshots = []
        self._macro_redo_stack = []
        self._macro_page_idx = self.current_idx
        self._toolbar.refresh_macro_buttons_state()
        self._macro_record_dialog = _MacroRecordDialog(self._center_parent, self)
        self._macro_record_dialog.show_nonmodal()

    def open_macro_read_dialog(self, entries=None):
        """entries=None : lecture sur la page active (bouton "Lire" de la
        barre). entries=liste : lecture en lot depuis la mosaïque (voir
        panel_widget.py)."""
        if self._macro_recording or self._macro_reading:
            return
        dlg = _MacroReadDialog(self._center_parent, self, entries=entries)
        dlg.show_nonmodal()

    def _macro_run(self, macro: dict, entries=None):
        """Lance la lecture. entries=None : page active uniquement."""
        from modules.qt import state as _state_module
        from modules.qt import macro_engine

        # Garde de dernier recours : _MacroReadDialog refuse déjà une lecture
        # pendant un enregistrement ou une autre lecture sur ce document.
        if self._macro_recording or self._macro_reading:
            return

        state = self.callbacks.get('state') or _state_module.state
        save_state = self.callbacks.get("save_state") or (lambda force=False: None)

        if entries is None:
            # Fichier fermé depuis l'ouverture de cette visionneuse : plus
            # de page à lire.
            if not (0 <= self.current_idx < len(state.images_data)):
                return
            entry = state.images_data[self.current_idx]
            entry["_real_idx"] = self.current_idx
            entries = [entry]

        report = macro_engine.run_macro_on_entries(macro, entries, self, save_state)

        if report["interrupted"]:
            return

        transient = getattr(self, "_macro_read_transient_viewer", False)
        if report["stopped"] == "closed":
            # Document fermé ou remplacé pendant la lecture : aucun rapport
            # sur un document qui n'est plus là. La visionneuse créée pour
            # cette lecture se ferme sans confirmation (un travail laissé en
            # attente concerne une page qui n'existe plus).
            if transient:
                self._close_confirmed = True
                self.close()
            return

        self._toolbar.refresh_undo_redo_state()

        report_parent = self._center_parent
        if transient:
            self.close()

        note_key = ("dialogs.macro_read.report_stopped_modified"
                    if report["stopped"] == "modified" else None)
        dlg = _MacroReportDialog(
            report_parent, len(report["ok"]), report["failed"], report["partial"],
            note_key=note_key)
        dlg.show_nonmodal()

    def _macro_complete_existing(self, macro: dict):
        """Bouton "Compléter" : reprend un enregistrement sur la macro
        existante, les nouvelles étapes s'ajoutent à la suite des
        existantes (référentiel de page potentiellement différent d'un
        bloc d'étapes à l'autre, assumé)."""
        if self._macro_recording or self._macro_reading:
            return
        self._macro_recording = True
        self._macro_steps = list(macro["steps"])
        # Étapes reprises du fichier : aucun état d'historique ne leur
        # correspond, un undo ne doit jamais les retirer.
        self._macro_step_snapshots = [None] * len(self._macro_steps)
        self._macro_redo_stack = []
        self._macro_page_idx = self.current_idx
        self._macro_complete_name = macro["name"]
        self._macro_complete_description = macro.get("description", "")
        self._toolbar.refresh_macro_buttons_state()
        self._macro_record_dialog = _MacroRecordDialog(self._center_parent, self)
        self._macro_record_dialog.show_nonmodal()

    def _macro_record_step(self, tool_id: str, params: dict, label_key: str, label_args: dict | None = None):
        """Appelée en fin de chaque perform_xxx capturable — no-op si aucun
        enregistrement n'est en cours. label_key/label_args résolus
        dynamiquement à l'affichage, jamais stockés comme texte figé."""
        if not self._macro_recording:
            return
        from modules.qt import macro_engine
        self._macro_steps.append({
            "tool": tool_id,
            "params": params,
            "label_key": label_key,
            "label_args": label_args or {},
        })
        # Sommet de l'historique juste après le commit de cette étape —
        # permet à _macro_sync_steps_with_history de savoir si un undo l'a
        # annulée (voir macro_engine.sync_recorded_steps).
        self._macro_step_snapshots.append(macro_engine.history_top(_viewer_state(self)))
        self._macro_redo_stack = []
        if self._macro_record_dialog is not None:
            self._macro_record_dialog.refresh_steps()

    def _macro_sync_steps_with_history(self):
        """Appelée après tout undo/redo (ImageViewer._refresh_after_undo_redo,
        qu'il vienne de la visionneuse ou de la mosaïque) : la liste live de
        l'enregistrement suit l'état réel de l'historique."""
        if not self._macro_recording:
            return
        from modules.qt import macro_engine
        state = _viewer_state(self)
        macro_engine.sync_recorded_steps(
            self._macro_steps, self._macro_step_snapshots, self._macro_redo_stack,
            state.history, state.history_index)
        if self._macro_record_dialog is not None:
            self._macro_record_dialog.refresh_steps()

    def _macro_discard_pending_work(self):
        """Jette le travail à valider laissé sur la page courante par une
        étape de lecture en échec (texte, formes, image collée,
        transparence) — même geste que le bouton "Annuler" de chaque outil."""
        for tool in ("text", "shapes", "transparency", "paste_image"):
            self._cancel_tool_work(tool)

    def _macro_cancel_recording(self):
        """N'annule pas les opérations déjà appliquées à l'image — seule la
        liste de capture est jetée."""
        self._macro_recording = False
        self._macro_steps = []
        self._macro_step_snapshots = []
        self._macro_record_dialog = None
        self._macro_complete_name = None
        self._macro_complete_description = ""
        self._toolbar.refresh_macro_buttons_state()

    def _macro_stop_recording(self, dialog):
        steps = self._macro_steps
        existing_name = getattr(self, '_macro_complete_name', None)
        existing_description = getattr(self, '_macro_complete_description', "")
        self._macro_recording = False
        self._macro_steps = []
        self._macro_step_snapshots = []
        self._macro_record_dialog = None
        self._macro_complete_name = None
        self._macro_complete_description = ""
        self._toolbar.refresh_macro_buttons_state()
        dialog.close()

        name_dialog = _MacroNameDialog(
            self._center_parent, steps, on_saved=None,
            existing_name=existing_name, existing_description=existing_description,
            confirm_discard=True,
        )
        name_dialog.show_nonmodal()


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée public — lecture depuis la mosaïque (sélection multiple)
# ─────────────────────────────────────────────────────────────────────────────

def read_macro_on_selection_qt(parent, callbacks):
    """Lecture d'une macro sur la sélection courante de la mosaïque — les 3
    points d'entrée obligatoires (barre de menus, menu contextuel, colonne
    d'icônes) pointent tous vers cette même fonction, sur le modèle de
    deskew_selected_qt (deskew_qt.py). La vraie ImageViewer (visible, bridée
    pendant la lecture — voir MacroViewerMixin._macro_set_locked_for_reading)
    n'est créée qu'au moment où l'utilisateur clique Lire/Compléter dans la
    fenêtre "Lire" — jamais pour le seul choix d'une macro dans la liste."""
    from modules.qt import state as _state_module
    from modules.qt.image_viewer_qt import ImageViewer

    state = callbacks.get('state') or _state_module.state
    if not state.selected_indices:
        return

    entries = []
    for idx in sorted(state.selected_indices):
        if idx < len(state.images_data) and state.images_data[idx].get("is_image") \
                and not state.images_data[idx].get("is_corrupted"):
            entry = state.images_data[idx]
            entry["_real_idx"] = idx
            entries.append(entry)
    if not entries:
        return

    def _make_viewer():
        # Page retrouvée par identité au moment du clic : la fenêtre "Lire"
        # étant non modale, des pages ont pu être déplacées ou supprimées
        # depuis la constitution de la sélection.
        first_idx = next((i for i, e in enumerate(state.images_data)
                          if any(e is x for x in entries)), None)
        if first_idx is None:
            return None
        viewer = ImageViewer(parent, first_idx, callbacks=callbacks)
        viewer.show()
        return viewer

    dlg = _MacroReadDialog(parent, entries=entries, viewer_factory=_make_viewer,
                           mosaic_canvas=callbacks.get('canvas'), state=state)
    dlg.show_nonmodal()
