"""
app_window_qt.py — Ce que fait toute fenêtre de l'application, écrit une fois.

AppWindowMixin, bâti sur FramedWindowMixin (barre de titre dessinée par Qt),
applique les règles UI de CLAUDE.md communes à toutes les fenêtres :

- non modale (règle n° 4 : une modale gèlerait aussi l'autre panneau) ;
- détruite à sa fermeture (WA_DeleteOnClose), sauf `delete_on_close=False`
  (Bibliothèque : fenêtre unique, masquée puis réaffichée) ;
- langue, thème et taille de police émettent tous language_signal, qui
  rappelle `_apply_theme()` puis `_retranslate()` ; le gestionnaire est
  débranché à la fermeture (finished) et à la destruction ;
- placée sur le panneau source avant d'être affichée (show_nonmodal) ;
- Échap suit le chemin de la croix (close → closeEvent).

QuestionWindowMixin y ajoute, pour une fenêtre qui pose une question, la
réponse envoyée une seule fois (boutons, croix, Échap).

Emploi :

    class MaFenetre(AppWindowMixin, QDialog):
        def __init__(self, parent):
            super().__init__(parent)
            self._setup_window()               # tout de suite après super().__init__
            self.set_window_title("clé.du.titre")
            ...                                # widgets
            self._connect_language()           # en dernier, widgets créés

        def _apply_theme(self): ...            # couleurs
        def _retranslate(self): ...            # textes ET police (get_current_font)
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QDialog

from modules.qt.framed_window_qt import FramedWindowMixin
from modules.qt.language_signal import disconnect_on_destroy, language_signal


class AppWindowMixin(FramedWindowMixin):
    """Fenêtre de premier niveau de l'application : `class X(AppWindowMixin,
    QDialog)`, `self._setup_window()` juste après `super().__init__`, puis
    `self._connect_language()` à la fin de `__init__`."""

    def _setup_window(self, delete_on_close: bool = True, center_on=None) -> None:
        """Barre de titre, non-modalité, destruction à la fermeture.
        center_on : widget sur lequel show_nonmodal centre la fenêtre, par
        défaut son parent (le panneau qui l'a ouverte)."""
        self._setup_title_bar()
        self._lang_handler = None
        self._center_parent = center_on if center_on is not None else self.parentWidget()
        if isinstance(self, QDialog):
            self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        if delete_on_close:
            self.setAttribute(Qt.WA_DeleteOnClose)

    def _connect_language(self, apply_now: bool = True) -> None:
        """Thème et textes appliqués une première fois, puis rejoués à chaque
        language_signal. En dernier dans __init__ : ils visent les widgets.
        apply_now=False : la fenêtre a déjà posé thème et textes en se
        construisant (rejouer toute sa retraduction serait un travail inutile)."""
        if apply_now:
            self._apply_theme()
            self._retranslate()
        # Une lambda, pas une méthode liée : la déconnexion à la destruction
        # relirait sinon l'objet C++ déjà détruit
        self._lang_handler = lambda _lang_code: self._on_language_signal()
        language_signal.changed.connect(self._lang_handler)
        # Détruite avec sa fenêtre parente, la fenêtre ne passe pas par finished
        disconnect_on_destroy(self, self._lang_handler)
        if isinstance(self, QDialog):
            self.finished.connect(self._disconnect_language)

    def _on_language_signal(self) -> None:
        """Rejoué à chaque language_signal (langue, thème, taille de police)."""
        self._apply_theme()
        self._retranslate()

    def _disconnect_language(self, *_args) -> None:
        """Débranche la traduction, une seule fois : PySide avertit en console
        d'une déconnexion d'un gestionnaire qui ne l'est plus."""
        handler, self._lang_handler = getattr(self, "_lang_handler", None), None
        if handler is None:
            return
        try:
            language_signal.changed.disconnect(handler)
        except (RuntimeError, TypeError):
            pass  # sans conséquence : déjà déconnecté

    def _apply_theme(self) -> None:
        """Couleurs de la fenêtre (get_current_theme), à redéfinir."""

    def _retranslate(self) -> None:
        """Textes de la fenêtre, à redéfinir : chaque widget dont le texte
        change reçoit aussi setFont(get_current_font(…)), sans quoi le pIqaD
        et le tengwar s'affichent dans l'ancienne police."""

    def set_center_widget(self, widget) -> None:
        """Widget sur lequel show_nonmodal centrera la fenêtre, à la place de
        celui donné à sa construction (parent masqué, par exemple)."""
        self._center_parent = widget

    def show_nonmodal(self) -> None:
        """Affiche la fenêtre déjà centrée sur son panneau, sans saut.

        Une fenêtre à qui le code n'a donné aucune taille ne prend celle de
        son contenu qu'au premier show() : centrée avant, elle le serait
        d'après sa taille par défaut. adjustSize() fait ici ce que show()
        ferait, et seulement dans ce cas : une taille posée par resize() ou
        setFixedSize() (WA_Resized) n'est jamais écrasée."""
        self.ensurePolished()
        if not self.testAttribute(Qt.WA_Resized):
            self.adjustSize()
        # Import différé : dialogs_qt importe ce module
        from modules.qt.dialogs_qt import position_dialog_on_parent
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event):
        # Échap : QDialog.reject masquerait la fenêtre sans passer par
        # closeEvent, dont le travail (réponse à l'appelant, libération) serait
        # sauté ; close() suit le chemin de la croix, QDialog.closeEvent
        # appelant ensuite reject.
        if isinstance(self, QDialog) and event.matches(QKeySequence.Cancel):
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)


class QuestionWindowMixin(AppWindowMixin):
    """Fenêtre qui pose une question et renvoie la réponse par son signal
    `result_signal`, déclaré par la fenêtre (Signal(bool), Signal(str)…) :

        self._setup_window(close_answer=False)   # réponse de la croix et d'Échap
        bouton.clicked.connect(lambda: self._answer(True))
        Fenetre(...).ask_async(on_result)

    La réponse n'est envoyée qu'une fois, quel que soit l'ordre des clics et
    des fermetures."""

    def _setup_window(self, close_answer=None, **options) -> None:
        super()._setup_window(**options)
        self._close_answer = close_answer
        self._answered = False

    def _answer(self, result) -> None:
        """Réponse d'un bouton, puis fenêtre fermée et détruite. Fermée par
        hide() + deleteLater(), sans passer par closeEvent (qui répondrait une
        seconde fois) ni par finished : la traduction est débranchée ici."""
        if self._answered:
            return
        self._answered = True
        self._disconnect_language()
        self.result_signal.emit(result)
        self.hide()
        self.deleteLater()

    def closeEvent(self, event):
        # Croix et Échap : réponse par défaut. QDialog.closeEvent n'est pas
        # appelé : il passerait par reject, qui rappelle close.
        if not self._answered:
            self._answered = True
            self._disconnect_language()
            self.result_signal.emit(self._close_answer)
        event.accept()

    def reject(self):
        # reject appelé directement : même chemin que la croix
        self.close()

    def ask_async(self, on_result) -> None:
        """Affiche la fenêtre (non modale) ; on_result(réponse) à la réponse."""
        self.result_signal.connect(on_result)
        self.show_nonmodal()
