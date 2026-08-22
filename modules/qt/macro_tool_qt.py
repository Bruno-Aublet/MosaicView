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
    est appelé après une sauvegarde réussie, avant la fermeture."""

    def __init__(self, parent, steps: list, on_saved, existing_name: str | None = None,
                 existing_description: str = ""):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self._center_parent = parent
        self._steps = steps
        self._on_saved = on_saved
        self._existing_name = existing_name
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
        macro_engine.save_macro(macro)
        if self._on_saved:
            self._on_saved(macro)
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
    d'une lecture effective, jamais pour le seul choix d'une macro)."""

    def __init__(self, parent, viewer=None, entries=None, viewer_factory=None, mosaic_canvas=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self._center_parent = parent
        self._viewer = viewer
        self._viewer_factory = viewer_factory
        self._mosaic_canvas = mosaic_canvas
        self._entries = entries
        self._macros = []
        self._selected_name = None

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
        )
        dlg.show_nonmodal()

    def _on_delete_clicked(self):
        from modules.qt import macro_engine
        macro = self._current_macro()
        if macro is None:
            return
        macro_engine.delete_macro(macro["name"])
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
            self._viewer._macro_read_transient_viewer = True
            if self._mosaic_canvas is not None:
                hide_canvas_text(self._mosaic_canvas, item_holder)
        return self._viewer

    def _on_complete_clicked(self):
        macro = self._current_macro()
        if macro is None:
            return
        self.close()
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        viewer = self._ensure_viewer()
        viewer._macro_complete_existing(macro)

    def _on_play_clicked(self):
        macro = self._current_macro()
        if macro is None:
            return
        self.close()
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        viewer = self._ensure_viewer()
        viewer._macro_run(macro, self._entries)


# ─────────────────────────────────────────────────────────────────────────────
# Fenêtre de rapport final — détail des pages en échec/partiel + raison
# ─────────────────────────────────────────────────────────────────────────────

class _MacroReportDialog(QDialog):
    """Résumé final : comptes + liste des pages en échec/partiel avec l'étape fautive."""

    def __init__(self, parent, n_ok: int, failed: list, partial: list):
        super().__init__(parent)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self._center_parent = parent
        self._n_ok = n_ok
        self._failed = failed
        self._partial = partial
        self.resize(520, 320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg_lbl)

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
        if self.page_mode != "single":
            self.page_mode = "single"
            self.display_image()
        self._macro_recording = True
        self._macro_steps = []
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

        state = self.callbacks.get('state') or _state_module.state
        save_state = self.callbacks.get("save_state") or (lambda force=False: None)

        if entries is None:
            entry = state.images_data[self.current_idx]
            entry["_real_idx"] = self.current_idx
            entries = [entry]

        report = macro_engine.run_macro_on_entries(macro, entries, self, save_state)

        if report["interrupted"]:
            return

        self._toolbar.refresh_undo_redo_state()

        transient = getattr(self, "_macro_read_transient_viewer", False)
        report_parent = self._center_parent
        if transient:
            self.close()

        dlg = _MacroReportDialog(
            report_parent, len(report["ok"]), report["failed"], report["partial"])
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
        self._macro_steps.append({
            "tool": tool_id,
            "params": params,
            "label_key": label_key,
            "label_args": label_args or {},
        })
        self._macro_redo_stack = []
        if self._macro_record_dialog is not None:
            self._macro_record_dialog.refresh_steps()

    def _macro_pop_last_step(self):
        if self._macro_steps:
            self._macro_redo_stack.append(self._macro_steps.pop())
        if self._macro_record_dialog is not None:
            self._macro_record_dialog.refresh_steps()

    def _macro_redo_last_step(self):
        if self._macro_redo_stack:
            self._macro_steps.append(self._macro_redo_stack.pop())
        if self._macro_record_dialog is not None:
            self._macro_record_dialog.refresh_steps()

    def _macro_cancel_recording(self):
        """N'annule pas les opérations déjà appliquées à l'image — seule la
        liste de capture est jetée."""
        self._macro_recording = False
        self._macro_steps = []
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
        self._macro_record_dialog = None
        self._macro_complete_name = None
        self._macro_complete_description = ""
        self._toolbar.refresh_macro_buttons_state()
        dialog.close()

        name_dialog = _MacroNameDialog(
            self._center_parent, steps, on_saved=None,
            existing_name=existing_name, existing_description=existing_description,
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
    first_idx = None
    for idx in sorted(state.selected_indices):
        if idx < len(state.images_data) and state.images_data[idx].get("is_image") \
                and not state.images_data[idx].get("is_corrupted"):
            entry = state.images_data[idx]
            entry["_real_idx"] = idx
            entries.append(entry)
            if first_idx is None:
                first_idx = idx
    if not entries:
        return

    def _make_viewer():
        viewer = ImageViewer(parent, first_idx, callbacks=callbacks)
        viewer.show()
        return viewer

    dlg = _MacroReadDialog(parent, entries=entries, viewer_factory=_make_viewer,
                           mosaic_canvas=callbacks.get('canvas'))
    dlg.show_nonmodal()
