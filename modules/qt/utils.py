import os

from PySide6.QtWidgets import QSlider, QMenu
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtCore import Qt


class FocusSlider(QSlider):
    """QSlider avec bordure de focus visible."""

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.hasFocus():
            painter = QPainter(self)
            painter.setPen(QPen(QColor("#888888"), 2))
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))


def _themed_menu_stylesheet(font):
    """Stylesheet QMenu commune : police + couleurs explicites du thème courant.

    Fixer explicitement background-color/color est nécessaire : sans ça, un QMenu
    enfant d'un widget en lecture seule ou en mode "lien" peut hériter d'une
    palette grisée (menu illisible bien que fonctionnel).
    """
    from modules.qt.state import get_current_theme
    theme = get_current_theme()
    return (
        f'QMenu {{ font-family: "{font.family()}"; font-size: {font.pointSize()}pt; '
        f'background-color: {theme["toolbar_bg"]}; color: {theme["text"]}; '
        f'border: 1px solid {theme["separator"]}; }} '
        f'QMenu::item:selected {{ background-color: {theme["separator"]}; }} '
        f'QMenu::item:disabled {{ color: {theme["disabled"]}; }}'
    )


def setup_text_browser_context_menu(browser):
    """
    Remplace le menu contextuel natif (anglais) d'un QTextBrowser
    par un menu traduit avec Copier / Tout sélectionner.
    """
    browser.setContextMenuPolicy(Qt.CustomContextMenu)

    def _show_menu(pos):
        from modules.qt.localization import _
        from modules.qt.font_manager_qt import get_current_font
        font = get_current_font(9)
        menu = QMenu(browser)
        menu.setFont(font)
        menu.setStyleSheet(_themed_menu_stylesheet(font))
        act_copy = menu.addAction(_("buttons.copy"))
        act_copy.setEnabled(browser.textCursor().hasSelection())
        act_copy.triggered.connect(browser.copy)
        menu.addSeparator()
        act_select_all = menu.addAction(_("menu.select_all"))
        act_select_all.triggered.connect(browser.selectAll)
        menu.exec(browser.mapToGlobal(pos))

    browser.customContextMenuRequested.connect(_show_menu)


def setup_lineedit_context_menu(edit, allow_copy_cut=True):
    """
    Remplace le menu contextuel natif (anglais) d'un QLineEdit
    par un menu traduit : Annuler / Refaire / Couper / Copier / Coller / Tout sélectionner.

    allow_copy_cut=False : masque Couper/Copier (champ sensible, ex. mot de passe/clé API),
    ne laisse que Annuler / Refaire / Coller / Tout sélectionner.
    """
    edit.setContextMenuPolicy(Qt.CustomContextMenu)

    def _show_menu(pos):
        from modules.qt.localization import _
        from modules.qt.font_manager_qt import get_current_font
        font = get_current_font(9)
        menu = QMenu(edit)
        menu.setFont(font)
        menu.setStyleSheet(_themed_menu_stylesheet(font))
        has_sel = edit.hasSelectedText()

        act_undo = menu.addAction(_("buttons.undo"))
        act_undo.setEnabled(edit.isUndoAvailable())
        act_undo.triggered.connect(edit.undo)

        act_redo = menu.addAction(_("buttons.redo"))
        act_redo.setEnabled(edit.isRedoAvailable())
        act_redo.triggered.connect(edit.redo)

        menu.addSeparator()

        if allow_copy_cut:
            act_cut = menu.addAction(_("buttons.cut"))
            act_cut.setEnabled(has_sel and not edit.isReadOnly())
            act_cut.triggered.connect(edit.cut)

            act_copy = menu.addAction(_("buttons.copy"))
            act_copy.setEnabled(has_sel)
            act_copy.triggered.connect(edit.copy)

        act_paste = menu.addAction(_("buttons.paste"))
        act_paste.setEnabled(not edit.isReadOnly())
        act_paste.triggered.connect(edit.paste)

        menu.addSeparator()

        act_select_all = menu.addAction(_("menu.select_all"))
        act_select_all.setEnabled(bool(edit.text()))
        act_select_all.triggered.connect(edit.selectAll)

        menu.exec(edit.mapToGlobal(pos))

    edit.customContextMenuRequested.connect(_show_menu)


def setup_textedit_context_menu(edit):
    """
    Remplace le menu contextuel natif (anglais) d'un QTextEdit
    par un menu traduit : Couper / Copier / Coller / Tout sélectionner.
    """
    edit.setContextMenuPolicy(Qt.CustomContextMenu)

    def _show_menu(pos):
        from modules.qt.localization import _
        from modules.qt.font_manager_qt import get_current_font
        font = get_current_font(9)
        menu = QMenu(edit)
        menu.setFont(font)
        menu.setStyleSheet(_themed_menu_stylesheet(font))

        cursor = edit.textCursor()
        has_sel = cursor.hasSelection()

        act_cut = menu.addAction(_("buttons.cut"))
        act_cut.setEnabled(has_sel and not edit.isReadOnly())
        act_cut.triggered.connect(edit.cut)

        act_copy = menu.addAction(_("buttons.copy"))
        act_copy.setEnabled(has_sel)
        act_copy.triggered.connect(edit.copy)

        act_paste = menu.addAction(_("buttons.paste"))
        act_paste.setEnabled(edit.canPaste() and not edit.isReadOnly())
        act_paste.triggered.connect(edit.paste)

        menu.addSeparator()

        act_select_all = menu.addAction(_("menu.select_all"))
        act_select_all.setEnabled(bool(edit.toPlainText()))
        act_select_all.triggered.connect(edit.selectAll)

        menu.exec(edit.mapToGlobal(pos))

    edit.customContextMenuRequested.connect(_show_menu)


def setup_link_label_context_menu(label, get_url):
    """
    Remplace le menu contextuel natif (anglais) d'un QLabel affichant un ou
    plusieurs liens cliquables (Qt.TextBrowserInteraction) par un menu traduit :
    Ouvrir le lien / Copier le lien (un couple d'actions par lien si plusieurs).

    get_url : callable () -> str | list[tuple[str, str]].
      - str : URL unique courante affichée par le label.
      - list[(label, url)] : plusieurs liens (ex. plusieurs URLs de crédits) ;
        chaque lien reçoit son propre "Ouvrir"/"Copier", préfixé par son label.
    """
    label.setContextMenuPolicy(Qt.CustomContextMenu)

    def _show_menu(pos):
        from modules.qt.localization import _
        from modules.qt.font_manager_qt import get_current_font
        font = get_current_font(9)
        menu = QMenu(label)
        menu.setFont(font)
        menu.setStyleSheet(_themed_menu_stylesheet(font))
        result = get_url() or ""

        links = result if isinstance(result, list) else [(None, result)]
        links = [(name, url) for name, url in links if url]

        for i, (name, url) in enumerate(links):
            open_text = _("dialogs.link.open") if name is None else f'{_("dialogs.link.open")} ({name})'
            copy_text = _("dialogs.link.copy") if name is None else f'{_("dialogs.link.copy")} ({name})'

            act_open = menu.addAction(open_text)
            act_open.triggered.connect(lambda _c=False, u=url: open_url(u))

            act_copy = menu.addAction(copy_text)
            act_copy.triggered.connect(lambda _c=False, u=url: _copy_to_clipboard(u))

            if i < len(links) - 1:
                menu.addSeparator()

        if not links:
            act_open = menu.addAction(_("dialogs.link.open"))
            act_open.setEnabled(False)
            act_copy = menu.addAction(_("dialogs.link.copy"))
            act_copy.setEnabled(False)

        menu.exec(label.mapToGlobal(pos))

    label.customContextMenuRequested.connect(_show_menu)


def setup_path_label_context_menu(label, get_path, open_fn):
    """
    Remplace le menu contextuel natif (anglais) d'un QLabel affichant un chemin
    de fichier/dossier cliquable (lien interne ouvrant l'Explorateur Windows,
    pas une vraie URL web) par un menu traduit : Ouvrir l'emplacement / Copier le chemin.

    get_path : callable () -> str, le chemin courant affiché.
    open_fn  : callable (), ouvre le chemin dans l'Explorateur (déjà défini par l'appelant).
    """
    label.setContextMenuPolicy(Qt.CustomContextMenu)

    def _show_menu(pos):
        from modules.qt.localization import _
        from modules.qt.font_manager_qt import get_current_font
        font = get_current_font(9)
        menu = QMenu(label)
        menu.setFont(font)
        menu.setStyleSheet(_themed_menu_stylesheet(font))
        path = get_path() or ""

        act_open = menu.addAction(_("dialogs.link.open_location"))
        act_open.setEnabled(bool(path))
        act_open.triggered.connect(open_fn)

        act_copy = menu.addAction(_("dialogs.link.copy_path"))
        act_copy.setEnabled(bool(path))
        act_copy.triggered.connect(lambda: _copy_to_clipboard(path))

        menu.exec(label.mapToGlobal(pos))

    label.customContextMenuRequested.connect(_show_menu)


def setup_html_label_context_menu(label):
    """
    Remplace le menu contextuel natif (anglais) d'un QLabel affichant du texte
    HTML avec un ou plusieurs liens <a href="..."> dont la cible n'est pas connue
    à l'avance (URL web classique OU pseudo-lien interne type href="file" géré
    par linkActivated, cf. certains dialogues comme InfoDialog).

    Extrait les liens directement du HTML courant du label (via label.text()) à
    chaque ouverture du menu, propose Ouvrir/Copier pour chacun. "Ouvrir" émet
    label.linkActivated(url), respectant ainsi le comportement déjà branché par
    l'appelant (setOpenExternalLinks ou connexion manuelle du signal) — pas de
    QDesktopServices direct.
    """
    import re
    label.setContextMenuPolicy(Qt.CustomContextMenu)
    _href_re = re.compile(r'href="([^"]*)"')

    def _show_menu(pos):
        from modules.qt.localization import _
        from modules.qt.font_manager_qt import get_current_font
        font = get_current_font(9)
        menu = QMenu(label)
        menu.setFont(font)
        menu.setStyleSheet(_themed_menu_stylesheet(font))

        hrefs = _href_re.findall(label.text())

        if not hrefs:
            act_copy = menu.addAction(_("buttons.copy"))
            act_copy.setEnabled(bool(label.selectedText()))
            act_copy.triggered.connect(lambda: _copy_to_clipboard(label.selectedText()))
        else:
            for i, href in enumerate(hrefs):
                suffix = f' ({i + 1}/{len(hrefs)})' if len(hrefs) > 1 else ''
                act_open = menu.addAction(f'{_("dialogs.link.open")}{suffix}')
                act_open.triggered.connect(lambda _c=False, h=href: label.linkActivated.emit(h))

                act_copy = menu.addAction(f'{_("dialogs.link.copy")}{suffix}')
                act_copy.triggered.connect(lambda _c=False, h=href: _copy_to_clipboard(h))

                if i < len(hrefs) - 1:
                    menu.addSeparator()

        menu.exec(label.mapToGlobal(pos))

    label.customContextMenuRequested.connect(_show_menu)


def setup_selectable_label_context_menu(label):
    """
    Remplace le menu contextuel natif (anglais) d'un QLabel sélectionnable
    (Qt.TextSelectableByMouse, sans lien) par un menu traduit :
    Copier / Tout sélectionner.
    """
    label.setContextMenuPolicy(Qt.CustomContextMenu)

    def _show_menu(pos):
        from modules.qt.localization import _
        from modules.qt.font_manager_qt import get_current_font
        font = get_current_font(9)
        menu = QMenu(label)
        menu.setFont(font)
        menu.setStyleSheet(_themed_menu_stylesheet(font))

        has_sel = bool(label.selectedText())

        act_copy = menu.addAction(_("buttons.copy"))
        act_copy.setEnabled(has_sel)
        act_copy.triggered.connect(lambda: _copy_to_clipboard(label.selectedText()))

        act_select_all = menu.addAction(_("menu.select_all"))
        act_select_all.setEnabled(bool(label.text()))
        act_select_all.triggered.connect(label.selectAll)

        menu.exec(label.mapToGlobal(pos))

    label.customContextMenuRequested.connect(_show_menu)


def open_url(url):
    # N'autoriser que de vraies adresses web : une URL issue de métadonnées
    # externes (ComicInfo.xml d'un CBZ téléchargé) pourrait sinon pointer vers
    # un chemin UNC (\\serveur\partage, fuite de hash NTLM), un file://, ou un
    # protocole personnalisé installé par un autre logiciel sur la machine.
    from urllib.parse import urlsplit
    if urlsplit(url).scheme.lower() not in ("http", "https"):
        return
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtCore import QUrl
    QDesktopServices.openUrl(QUrl(url))


def _copy_to_clipboard(text):
    from PySide6.QtWidgets import QApplication
    QApplication.clipboard().setText(text)


def get_support_email() -> str:
    """Adresse mail de contact MosaicView, reconstruite à partir de morceaux
    séparés pour ne pas apparaître en clair dans le code source (repo public,
    cible facile pour les robots de scraping d'adresses mail)."""
    user = "mosaicview" + "1969"
    domain = "gmail" + "." + "com"
    return f"{user}@{domain}"


def format_file_size(size_bytes):
    """Convertit une taille en octets en format lisible (ex: "1.5 Mo")."""
    if size_bytes < 1024:
        return f"{size_bytes} o"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} Ko"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} Mo"
    elif size_bytes < 1024 * 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} Go"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024 * 1024):.2f} To"


def zip_compression_kwargs(level: int) -> dict:
    """
    Convertit un niveau de compression 0-9 (réglage utilisateur) en kwargs
    pour zipfile.ZipFile(..., **kwargs).
    0 → ZIP_STORED (pas de compression). 1-9 → ZIP_DEFLATED avec compresslevel.
    """
    import zipfile
    if level <= 0:
        return {"compression": zipfile.ZIP_STORED}
    return {"compression": zipfile.ZIP_DEFLATED, "compresslevel": level}


# ─────────────────────────────────────────────────────────────────────────────
# Cycle de vie des QThread (règle générale : CLAUDE.md, section Architecture)
# ─────────────────────────────────────────────────────────────────────────────

def dispose_qthread(worker):
    """Libère un QThread depuis le slot de son signal de fin.

    Ce signal est émis à la dernière ligne de run() : le thread tourne encore
    quelques µs quand le slot s'exécute. Un deleteLater() sans wait() préalable
    peut détruire le QThread pendant qu'il tourne → corruption mémoire qui fait
    planter l'application bien plus tard, n'importe où. wait() est ici quasi
    instantané. Réservé aux signaux suivis d'un return dans run() : ailleurs,
    wait() bloquerait le thread UI le temps du traitement restant."""
    if worker is None:
        return
    try:
        worker.wait()
        worker.deleteLater()
    except RuntimeError:
        pass  # objet C++ déjà détruit


_parked_qthreads = []
_park_timer = None


def park_qthread(worker):
    """Garde vivant un QThread détaché (slots déjà débranchés par l'appelant)
    jusqu'à la fin réelle de son run(), puis le libère.

    La fin est détectée par isRunning(), pas par un signal : plusieurs workers
    déclarent leur propre `finished`, qui masque QThread.finished natif et
    n'est pas émis sur tous les chemins (annulation notamment)."""
    global _park_timer
    if worker is None or any(w is worker for w in _parked_qthreads):
        return
    _parked_qthreads.append(worker)
    if _park_timer is None:
        from PySide6.QtCore import QTimer
        _park_timer = QTimer()
        _park_timer.setInterval(200)
        _park_timer.timeout.connect(_release_finished_parked_qthreads)
    if not _park_timer.isActive():
        _park_timer.start()


def _release_finished_parked_qthreads():
    for w in list(_parked_qthreads):
        try:
            running = w.isRunning()
        except RuntimeError:
            _parked_qthreads.remove(w)
            continue
        if not running:
            _parked_qthreads.remove(w)
            dispose_qthread(w)
    if not _parked_qthreads and _park_timer is not None:
        _park_timer.stop()


def register_cancel_on_close(canvas, cancel_fn):
    """Inscrit la fonction d'annulation d'une opération asynchrone propre au
    panneau de `canvas` : force_close_file l'appelle à la fermeture du fichier,
    pour arrêter le traitement et masquer son texte rouge au lieu de le laisser
    finir pour rien. À retirer (unregister_cancel_on_close) quand l'opération
    se termine. L'opération doit rester protégée par AppState.doc_generation :
    une annulation trop tardive ne doit pas suffire à la rendre sûre."""
    callbacks = getattr(canvas, '_cancel_on_close', None)
    if callbacks is None:
        callbacks = []
        canvas._cancel_on_close = callbacks
    callbacks.append(cancel_fn)


def unregister_cancel_on_close(canvas, cancel_fn):
    callbacks = getattr(canvas, '_cancel_on_close', None)
    if callbacks and cancel_fn in callbacks:
        callbacks.remove(cancel_fn)


def cancel_operations_on_close(canvas):
    """Appelé par force_close_file : annule toutes les opérations inscrites
    par register_cancel_on_close pour ce canvas. Une annulation qui échoue ne
    doit jamais empêcher la fermeture du fichier."""
    callbacks = getattr(canvas, '_cancel_on_close', None)
    if not callbacks:
        return
    pending = list(callbacks)
    callbacks.clear()
    for cancel_fn in pending:
        try:
            cancel_fn()
        except Exception:
            pass


def stop_running_qthreads(timeout_ms: int = 3000):
    """À la fermeture de l'application : demande l'arrêt de tous les QThread
    encore actifs, puis attend leur fin dans un délai global borné.

    Sans ça, un QThread encore en cours au moment où l'interpréteur détruit
    les objets restants est détruit pendant qu'il tourne. L'arrêt passe par
    les conventions d'annulation des workers du projet : Event `_cancelled` ou
    `_stop`, booléen `_cancelled`/`cancelled`, liste `_cancel_flag`, et
    set_ext_result(None) pour un LoadWorker en attente de réponse. Un worker
    sans mécanisme d'annulation (appel réseau, COM) est simplement attendu."""
    import gc
    import threading
    import time
    from PySide6.QtCore import QThread

    running = []
    for obj in gc.get_objects():
        try:
            if isinstance(obj, QThread) and obj.isRunning():
                running.append(obj)
        except RuntimeError:
            continue

    for w in running:
        for attr in ('_cancelled', '_stop'):
            flag = getattr(w, attr, None)
            if isinstance(flag, threading.Event):
                flag.set()
            elif flag is False:
                setattr(w, attr, True)
        if getattr(w, 'cancelled', None) is False:
            w.cancelled = True
        cancel_flag = getattr(w, '_cancel_flag', None)
        if isinstance(cancel_flag, list) and cancel_flag:
            cancel_flag[0] = True
        if hasattr(w, 'set_ext_result'):
            w.set_ext_result(None)

    deadline = time.monotonic() + timeout_ms / 1000
    for w in running:
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            break
        try:
            w.wait(remaining)
        except RuntimeError:
            pass


def replace_file_atomically(dest, write_to):
    """Écrit `dest` sans jamais laisser de fichier partiel ni abîmer l'existant.

    write_to(tmp_path) écrit le contenu complet dans un fichier temporaire créé
    dans le dossier de `dest` (même volume), qui remplace ensuite `dest` d'un
    seul coup par os.replace. En cas d'échec, y compris une interruption, le
    temporaire est supprimé et `dest` reste tel qu'il était.

    shutil.move ne convient pas : sous Windows, os.rename refuse une destination
    existante et shutil.move bascule alors sur une copie qui tronque `dest`
    avant de la réécrire.
    """
    import tempfile
    dest_dir = os.path.dirname(os.path.abspath(dest))
    fd, tmp_path = tempfile.mkstemp(prefix=".~mosaicview_", suffix=".tmp", dir=dest_dir)
    os.close(fd)
    try:
        write_to(tmp_path)
        os.replace(tmp_path, dest)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def estimate_cbz_size(entries):
    """Taille maximale, en octets, du CBZ écrit à partir de ces entrées : données
    plus en-têtes ZIP (en-tête local et entrée du répertoire central, chacun
    portant le nom). Majorant : la compression ne peut que réduire les données."""
    total = 22  # fin du répertoire central
    for entry in entries:
        data = entry.get("bytes")
        if data is None or entry.get("is_dir"):
            continue
        name_len = len(entry.get("orig_name", "").encode("utf-8"))
        total += len(data) + 100 + 2 * name_len
    return total


def is_same_path(a, b):
    """True si `a` et `b` désignent le même fichier.

    Sous Windows, deux chaînes différentes (casse, séparateurs, composants
    relatifs ou "..", noms courts 8.3) peuvent désigner le même fichier : une
    comparaison de chaînes ne suffit pas. samefile est utilisé quand les deux
    existent, sinon une comparaison des chemins normalisés.
    """
    if not a or not b:
        return False
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def safe_join(base, name):
    """
    Joint `name` à `base` en garantissant que le résultat reste à l'intérieur
    de `base`. Préserve les sous-dossiers légitimes (ex. "chapitre1/page01.jpg").

    Protège contre la traversée de répertoire (Zip Slip) : un nom contenant
    "../", un chemin absolu ou une autre lettre de lecteur produit un chemin
    hors de `base`. Retourne None dans ce cas (l'appelant doit ignorer l'entrée).
    """
    base_real = os.path.realpath(base)
    dest = os.path.realpath(os.path.join(base_real, name))
    try:
        if dest != base_real and os.path.commonpath([base_real, dest]) != base_real:
            return None
    except ValueError:
        # commonpath lève ValueError si les chemins sont sur des lecteurs
        # différents ou mélangent UNC et lettre de lecteur (ex. nom d'entrée
        # "D:\evil" ou "\\serveur\part") — c'est une évasion, on refuse.
        return None
    return dest
