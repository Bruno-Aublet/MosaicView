"""
modules/qt/file_close_qt.py
Fermeture de fichier/application — version PySide6.
Reproduit à l'identique le comportement de modules/file_close.py + on_window_close().

Comportement de la croix de fermeture :
  - Canvas vide → ferme l'application
  - Images présentes ou modifié → ferme le comics (reste dans l'appli)
    - Archive modifiée → dialog 3 boutons (Fermer sans sauver / Sauver+Fermer / Annuler)
    - Archive non modifiée → force_close_file directement
    - Pas d'archive + modifié → dialog Oui/Non/Annuler (créer CBZ ?)
    - Pas d'archive + non modifié → force_close_file directement
"""

import gc
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)

from modules.qt.localization import _, _wt
from modules.qt import state as _state_module
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.undo_redo import reset_history


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog "archive modifiée" — 3 boutons (Fermer sans sauver / Sauver+Fermer / Annuler)
# Reproduit close_warning_dialog.py
# ═══════════════════════════════════════════════════════════════════════════════

class CloseWarningDialog(QDialog):
    """Dialog affiché quand une archive a des modifications non sauvegardées."""

    _BTN_STYLE = (
        "QPushButton {{ background-color: {bg}; color: #000000; font-size: 13pt;"
        " border: 2px groove #888888; }}"
        "QPushButton:hover {{ background-color: {bg_hover}; }}"
    )

    def __init__(self, parent, current_file: str, force_close_cb, apply_and_close_cb):
        super().__init__(parent)
        self._force_close_cb = force_close_cb
        self._apply_and_close_cb = apply_and_close_cb
        ext = os.path.splitext(current_file)[1].lower() if current_file else ""
        self._is_cbz = (ext == ".cbz")
        self.setModal(True)
        self.setFixedSize(550, 450)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 20)
        layout.setSpacing(15)

        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        self._lbl.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(self._lbl)

        self._btn_lose = QPushButton()
        self._btn_lose.setStyleSheet(self._BTN_STYLE.format(bg="#ff9999", bg_hover="#ff7777"))
        self._btn_lose.setFixedHeight(80)
        self._btn_lose.clicked.connect(self._on_lose)
        layout.addWidget(self._btn_lose)

        self._btn_save = QPushButton()
        self._btn_save.setStyleSheet(self._BTN_STYLE.format(bg="#99ff99", bg_hover="#77ff77"))
        self._btn_save.setFixedHeight(80)
        self._btn_save.clicked.connect(self._on_save)
        layout.addWidget(self._btn_save)

        self._btn_cancel = QPushButton()
        self._btn_cancel.setStyleSheet(self._BTN_STYLE.format(bg="#cccccc", bg_hover="#aaaaaa"))
        self._btn_cancel.setFixedHeight(80)
        self._btn_cancel.clicked.connect(self.reject)
        layout.addWidget(self._btn_cancel)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
        self._center_parent = parent

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        self.setWindowTitle(_wt("dialogs.close_warning.title"))
        self._lbl.setText(_("dialogs.close_warning.message"))
        self._lbl.setFont(_get_current_font(13, bold=True))
        self._btn_lose.setText(_("dialogs.close_warning.btn_close_lose"))
        self._btn_lose.setFont(font)
        if self._is_cbz:
            self._btn_save.setText(_("dialogs.close_warning.btn_save_cbz"))
        else:
            self._btn_save.setText(_("dialogs.close_warning.btn_create_cbz"))
        self._btn_save.setFont(font)
        self._btn_cancel.setText(_("dialogs.close_warning.btn_cancel"))
        self._btn_cancel.setFont(font)

    def _on_lose(self):
        self.accept()
        self._force_close_cb()

    def _on_save(self):
        self.accept()
        self._apply_and_close_cb()

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog "fermer sans sauvegarder ?" — Oui/Non/Annuler
# Reproduit close_without_save_dialog.py
# ═══════════════════════════════════════════════════════════════════════════════

class CloseWithoutSaveDialog(QDialog):
    """Dialog affiché quand des images non-archive ont des modifications non sauvegardées."""

    # Résultat : True=Oui(créer CBZ), False=Non(fermer sans sauver), None=Annuler
    result_value = None

    def __init__(self, parent):
        super().__init__(parent)
        self.result_value = None
        self.setModal(True)
        self.setFixedSize(500, 400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        self._title_lbl = QLabel()
        self._title_lbl.setAlignment(Qt.AlignCenter)
        self._title_lbl.setStyleSheet("color: #d9534f; font-size: 20px; font-weight: bold;")
        self._title_lbl.setWordWrap(True)
        layout.addWidget(self._title_lbl)

        self._msg_lbl = QLabel()
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        self._msg_lbl.setWordWrap(True)
        layout.addWidget(self._msg_lbl)

        self._btn_yes = QPushButton()
        self._btn_yes.setFixedHeight(80)
        self._btn_yes.clicked.connect(self._on_yes)
        layout.addWidget(self._btn_yes)

        self._btn_no = QPushButton()
        self._btn_no.setFixedHeight(80)
        self._btn_no.clicked.connect(self._on_no)
        layout.addWidget(self._btn_no)

        self._btn_cancel = QPushButton()
        self._btn_cancel.setFixedHeight(80)
        self._btn_cancel.clicked.connect(self._on_cancel)
        layout.addWidget(self._btn_cancel)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)
        self._center_parent = parent

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    _BTN_STYLE = (
        "QPushButton {{ background-color: {bg}; color: #000000; font-size: 13pt;"
        " border: 2px groove #888888; }}"
        "QPushButton:hover {{ background-color: {bg_hover}; }}"
    )

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        self.setWindowTitle(_wt("messages.questions.close_without_save.title"))
        self._title_lbl.setText(_("messages.questions.close_without_save.title"))
        self._title_lbl.setFont(_get_current_font(13, bold=True))
        self._msg_lbl.setText(_("messages.questions.close_without_save.message"))
        self._msg_lbl.setFont(_get_current_font(13))
        self._btn_yes.setText(_("buttons.yes"))
        self._btn_yes.setFont(font)
        self._btn_yes.setStyleSheet(self._BTN_STYLE.format(bg="#99ff99", bg_hover="#77ff77"))
        self._btn_no.setText(_("buttons.no"))
        self._btn_no.setFont(font)
        self._btn_no.setStyleSheet(self._BTN_STYLE.format(bg="#ff9999", bg_hover="#ff7777"))
        self._btn_cancel.setText(_("buttons.cancel"))
        self._btn_cancel.setFont(font)
        self._btn_cancel.setStyleSheet(self._BTN_STYLE.format(bg="#cccccc", bg_hover="#aaaaaa"))

    def _on_yes(self):
        self.result_value = True
        self.accept()

    def _on_no(self):
        self.result_value = False
        self.accept()

    def _on_cancel(self):
        self.result_value = None
        self.reject()

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._on_cancel()
        else:
            super().keyPressEvent(event)


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog "confirmer la suppression"
# ═══════════════════════════════════════════════════════════════════════════════

class DeleteConfirmDialog(QDialog):
    """Dialogue de confirmation de suppression — thème + langue à la volée."""

    def __init__(self, parent, count: int, size_str: str):
        super().__init__(parent)
        self._count    = count
        self._size_str = size_str
        self.setModal(True)
        self.setFixedSize(500, 170)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(16)

        self._msg = QLabel()
        self._msg.setWordWrap(True)
        self._msg.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg)

        btn_row = QHBoxLayout()
        self._btn_yes = QPushButton()
        self._btn_yes.clicked.connect(self.accept)
        self._btn_no  = QPushButton()
        self._btn_no.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_yes)
        btn_row.addWidget(self._btn_no)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

        self._btn_yes.setFocus()
        self._center_parent = parent

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            from PySide6.QtCore import QTimer
            from modules.qt.dialogs_qt import _center_on_widget
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 8px 24px; min-width: 100px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self.setWindowTitle(_wt("messages.questions.confirm_delete.title"))
        self._msg.setText(_("messages.questions.confirm_delete.message",
                            count=self._count, size=self._size_str))
        self._msg.setFont(font)
        self._btn_yes.setText(_("buttons.yes"))
        self._btn_yes.setFont(font)
        self._btn_yes.setStyleSheet(btn_style + " QPushButton { background: #ff9999; } QPushButton:hover { background: #ff7777; }")
        self._btn_no.setText(_("buttons.no"))
        self._btn_no.setFont(font)
        self._btn_no.setStyleSheet(btn_style)

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)


# ═══════════════════════════════════════════════════════════════════════════════
# force_close_file — libère la mémoire et nettoie le canvas
# Reproduit force_close_file() de modules/file_close.py
# ═══════════════════════════════════════════════════════════════════════════════

def force_close_file(canvas, refresh_title, refresh_toolbar, refresh_tabs,
                     refresh_status, refresh_menubar):
    """
    Fermeture effective : libère la mémoire, nettoie le canvas.

    canvas          : MosaicCanvas
    refresh_title   : callable()
    refresh_toolbar : callable()
    refresh_tabs    : callable()
    refresh_status  : callable()
    refresh_menubar : callable()
    """
    from modules.qt.mosaic_canvas import invalidate_pixmap_cache
    state = _state_module.state

    invalidate_pixmap_cache()

    # Libère les images PIL en mémoire (images_data + all_entries sans doublon)
    all_entries_to_clean = list(state.images_data)
    if state.all_entries and state.all_entries is not state.images_data:
        seen = {id(e) for e in all_entries_to_clean}
        for e in state.all_entries:
            if id(e) not in seen:
                all_entries_to_clean.append(e)

    for entry in all_entries_to_clean:
        for key in ("img", "large_thumb_pil"):
            if key in entry and entry[key] is not None:
                try:
                    entry[key].close()
                except Exception:
                    pass
                entry[key] = None
        for key in ("bytes", "img_id", "qt_pixmap_large", "qt_qimage_large"):
            if key in entry:
                entry[key] = None

    state.current_file = None
    state.images_data = []
    state.all_entries = []
    state.comic_metadata = None
    state._page_attrs_by_entry_id = {}
    state.modified = False
    state.needs_renumbering = False
    state.merge_counter = 0
    state.current_directory = ""
    reset_history(state)

    canvas.render_mosaic()
    refresh_title()
    refresh_toolbar()
    refresh_tabs()
    refresh_status()
    refresh_menubar()

    gc.collect()
    gc.collect()  # 2e passe pour les cycles Python/Qt

    from modules.qt.pdf_loading_qt import shutdown_pdf_process
    shutdown_pdf_process()



# ═══════════════════════════════════════════════════════════════════════════════
# close_file — logique principale avec dialogs de confirmation
# Reproduit close_file() de modules/file_close.py
# ═══════════════════════════════════════════════════════════════════════════════

def close_file(parent, canvas, create_cbz_cb, apply_new_names_cb,
               refresh_title, refresh_toolbar, refresh_tabs,
               refresh_status, refresh_menubar):
    """
    Ferme le fichier courant avec confirmation si modifié.
    Retourne True si le fichier a été fermé (ou qu'il n'y avait rien),
    False si l'utilisateur a annulé.

    parent            : QWidget parent pour les dialogs
    canvas            : MosaicCanvas
    create_cbz_cb     : callable() → créer CBZ depuis images libres
    apply_new_names_cb: callable() → appliquer noms + sauvegarder
    refresh_*         : callables de rafraîchissement UI
    """
    state = _state_module.state

    def _force():
        force_close_file(canvas, refresh_title, refresh_toolbar,
                         refresh_tabs, refresh_status, refresh_menubar)

    # ── Pas d'archive, images présentes ──────────────────────────────────────
    if not state.current_file and state.images_data:
        if not state.modified:
            _force()
            return True

        dlg = CloseWithoutSaveDialog(parent)
        dlg.exec()
        response = dlg.result_value

        if response is None:    # Annuler
            return False
        elif response:          # Oui → créer CBZ
            create_cbz_cb()
            if state.current_file:  # Archive créée avec succès
                _force()
        else:                   # Non → fermer sans sauver
            _force()
        return True

    # ── Pas d'archive, pas d'images ──────────────────────────────────────────
    if not state.current_file and not state.images_data:
        return True  # Rien à fermer (l'appelant gère la fermeture de l'appli)

    # ── Archive présente ──────────────────────────────────────────────────────
    if not state.modified:
        _force()
        return True
    else:
        def _apply_and_close():
            result = apply_new_names_cb()
            if result is not False:
                _force()

        dlg = CloseWarningDialog(parent, state.current_file, _force, _apply_and_close)
        dlg.exec()
        # Si l'utilisateur a cliqué Annuler (reject), on ne ferme pas
        return dlg.result() == QDialog.Accepted


# ═══════════════════════════════════════════════════════════════════════════════
# on_window_close — point d'entrée depuis closeEvent
# Reproduit on_window_close() de MosaicView.py
# ═══════════════════════════════════════════════════════════════════════════════

def on_window_close(main_window, canvas, create_cbz_cb, apply_new_names_cb,
                    refresh_title, refresh_toolbar, refresh_tabs,
                    refresh_status, refresh_menubar,
                    save_session_cb, cleanup_temp_cb):
    """
    Gère le clic sur la croix de fermeture.
    Retourne True si l'application peut se fermer, False si l'utilisateur a annulé.

    main_window    : QMainWindow (parent des dialogs)
    save_session_cb: callable() → sauvegarde géométrie/état
    cleanup_temp_cb: callable() → nettoyage des fichiers temporaires
    """
    state = _state_module.state

    if state.images_data or state.modified:
        had_archive = bool(state.current_file)
        closed = close_file(
            main_window, canvas, create_cbz_cb, apply_new_names_cb,
            refresh_title, refresh_toolbar, refresh_tabs,
            refresh_status, refresh_menubar,
        )
        if not closed:
            return False  # L'utilisateur a annulé → ne pas fermer l'appli

        # Si on avait une archive et qu'on vient de la fermer → rester sur canvas vide
        if had_archive:
            try:
                cleanup_temp_cb()
            except Exception as e:
                print(f"Erreur nettoyage temp files : {e}")
            return False

        # Pas d'archive (mode sans archive) : la fermeture vide le canvas → quitter l'appli
        save_session_cb()
        try:
            cleanup_temp_cb()
        except Exception as e:
            print(f"Erreur nettoyage temp files : {e}")
        return True

    # Canvas vide → ferme l'application
    save_session_cb()
    try:
        cleanup_temp_cb()
    except Exception as e:
        print(f"Erreur nettoyage temp files : {e}")
    return True
