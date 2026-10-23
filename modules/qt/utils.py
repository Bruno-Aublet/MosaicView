import os
import threading

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
        menu.deleteLater()

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
        menu.deleteLater()

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
        menu.deleteLater()

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
        menu.deleteLater()

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
        menu.deleteLater()

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
        menu.deleteLater()

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
        menu.deleteLater()

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
            pass  # repli : l'opération non annulée abandonne son résultat (contrôle de doc_generation)


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

    for w in running:
        try:
            if w.isRunning():
                _qthreads_left_running.append(w)
        except RuntimeError:
            continue


# Workers encore en cours après le délai de stop_running_qthreads (appel réseau,
# numérisation WIA, aperçu PyMuPDF : non interruptibles). Gardés référencés
# jusqu'à la fin du process : voir running_qthread_names.
_qthreads_left_running = []


def running_qthread_names():
    """Classes (module.Classe) des workers encore en cours après
    stop_running_qthreads. Liste non vide : la destruction des objets restants
    par l'interpréteur détruirait un QThread actif, ce qui fait planter
    l'application à la sortie ; main() écrit ces noms au journal des erreurs
    (error_log.log_forced_exit) puis termine le process sans cette
    destruction (os._exit)."""
    names = []
    for w in _qthreads_left_running:
        try:
            if w.isRunning():
                names.append(f"{type(w).__module__}.{type(w).__qualname__}")
        except RuntimeError:
            continue
    return names


class FileInUseError(OSError):
    """Le fichier `path` n'a pas pu être remplacé parce qu'un autre programme
    l'a ouvert (lecteur de BD, antivirus, scan ou aperçu de la Bibliothèque).
    Son texte est traduit à chaque str(), pas à la création : un dialogue qui
    l'affiche via `lambda err=e: _(..., error=err)` suit donc un changement
    de langue."""

    def __init__(self, path):
        super().__init__(path)
        self.path = path

    def __str__(self):
        from modules.qt.localization import _
        return _("messages.errors.file_in_use", name=os.path.basename(self.path))


class ReadOnlyFileError(PermissionError):
    """Le fichier `path` est en lecture seule et ne peut pas être remplacé.
    Remplace le message système brut (« [WinError 5] Accès refusé », dans la
    langue de Windows et non celle de l'application). Texte traduit à chaque
    str(), comme FileInUseError."""

    def __init__(self, path):
        super().__init__(path)
        self.path = path

    def __str__(self):
        from modules.qt.localization import _
        return _("messages.errors.file_read_only", name=os.path.basename(self.path))


_REPLACEFILE_IGNORE_MERGE_ERRORS = 0x2
_ERROR_UNABLE_TO_MOVE_REPLACEMENT = 1176
_ERROR_UNABLE_TO_MOVE_REPLACEMENT_2 = 1177

# Temporaires devenus la seule copie complète d'un fichier (ReplacementPendingError) :
# jamais supprimés, ni par l'écriture qui échoue ni par remove_stale_temp_files.
_kept_temp_files = set()


class ReplacementPendingError(OSError):
    """ReplaceFileW a retiré `dest` de son nom (erreurs 1176/1177) et le
    temporaire `src` n'a pas pu prendre sa place : il est désormais la seule
    copie complète du fichier. Il est gardé (is_kept_temp) et le message dit
    où le trouver. Texte traduit à chaque str(), comme FileInUseError."""

    def __init__(self, dest, src):
        super().__init__(dest)
        self.dest = dest
        self.src = src
        _kept_temp_files.add(os.path.normcase(os.path.abspath(src)))

    def __str__(self):
        from modules.qt.localization import _
        return _("messages.errors.replace_pending",
                 name=os.path.basename(self.dest), temp=self.src)


def is_kept_temp(tmp_path):
    """Vrai si `tmp_path` est la seule copie restante d'un fichier
    (ReplacementPendingError) : l'appelant ne doit pas le supprimer."""
    return os.path.normcase(os.path.abspath(tmp_path)) in _kept_temp_files


def _replace_keeping_identity(src, dest):
    """Remplace `dest` par `src` en gardant l'identité du fichier remplacé.

    os.replace fait de `src` le nouveau fichier : `dest` perd sa date de
    création, ses attributs (lecture seule exceptée, refusée plus haut ;
    caché, archive), ses droits d'accès et ses flux annexes. ReplaceFileW
    (Windows) transfère ces propriétés de `dest` au contenu de `src`.

    Un verrou (erreurs 5/32/33) remonte en PermissionError pour que
    replace_file_retrying réessaie. Toute autre erreur de ReplaceFileW
    (volume qui ne la prend pas en charge, comme certains partages réseau)
    se rabat sur os.replace : sans fichier de sauvegarde, les erreurs
    1176/1177 peuvent avoir déjà retiré `dest` de son nom, et `src`, intact,
    doit alors impérativement prendre sa place — os.replace y est réessayé
    tant que `dest` est absent ; s'il échoue encore, `src` est le seul
    contenu complet : ReplacementPendingError le fait garder par l'appelant."""
    if os.name != "nt" or not os.path.exists(dest):
        os.replace(src, dest)
        return
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    replace_file = kernel32.ReplaceFileW
    replace_file.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                             wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID]
    replace_file.restype = wintypes.BOOL
    if replace_file(os.path.abspath(dest), os.path.abspath(src), None,
                    _REPLACEFILE_IGNORE_MERGE_ERRORS, None, None):
        return
    code = ctypes.get_last_error()
    if code in (5, 32, 33):
        raise ctypes.WinError(code)
    if code in (_ERROR_UNABLE_TO_MOVE_REPLACEMENT, _ERROR_UNABLE_TO_MOVE_REPLACEMENT_2) \
            and not os.path.exists(dest):
        import time
        for attempt in range(50):
            try:
                os.replace(src, dest)
                return
            except OSError as e:
                if attempt == 49:
                    if os.path.exists(dest):
                        raise
                    raise ReplacementPendingError(dest, src) from e
                time.sleep(0.1)
    os.replace(src, dest)


def replace_file_retrying(src, dest, attempts=5, delay=0.2):
    """os.replace(src, dest) qui supporte un verrou passager sur `dest`.

    Sous Windows, remplacer un fichier ouvert par un autre programme échoue
    (PermissionError, erreurs 5/32/33) : antivirus ou indexeur qui le lisent
    un instant, scan ou aperçu de la Bibliothèque. Le remplacement est retenté
    quelques fois (attempts × delay secondes au total) ; si le fichier reste
    bloqué, FileInUseError donne la raison à l'utilisateur au lieu du message
    système brut. Un fichier en lecture seule donne ReadOnlyFileError, un
    autre refus est relancé tel quel. Le fichier remplacé garde sa date de
    création, ses attributs et ses droits (_replace_keeping_identity)."""
    import time
    for attempt in range(attempts):
        try:
            _replace_keeping_identity(src, dest)
            return
        except PermissionError as e:
            if os.path.exists(dest) and not os.access(dest, os.W_OK):
                raise ReadOnlyFileError(dest) from e
            if getattr(e, "winerror", None) not in (5, 32, 33):
                raise
            if attempt == attempts - 1:
                raise FileInUseError(dest) from e
            time.sleep(delay)


def fsync_file(path):
    """Force l'écriture sur disque du contenu de `path`, à appeler avant de le
    substituer à un fichier existant (os.replace) : sans elle, le renommage
    peut atteindre le disque avant les données, et une coupure de courant
    laisse un fichier vide ou tronqué sous le nom final, l'ancien étant déjà
    perdu."""
    with open(path, "rb+") as f:
        os.fsync(f.fileno())


TEMP_FILE_PREFIX = ".~mosaicview_"
TEMP_FILE_SUFFIX = ".tmp"
# Âge au-delà duquel un temporaire qu'aucune écriture en cours de ce processus
# ne détient est considéré comme abandonné. Une écriture en cours met à jour
# la date de modification de son temporaire à chaque bloc écrit.
STALE_TEMP_AGE = 600

_active_temp_files = set()
_active_temp_lock = threading.Lock()


def remove_stale_temp_files(directory):
    """Supprime de `directory` les temporaires d'écriture laissés par un
    arrêt brutal (plantage, coupure de courant) : sans ce ménage, ils
    s'accumulent dans les dossiers de l'utilisateur, à côté de ses comics.
    Seuls les fichiers au nom de temporaire MosaicView, absents des écritures
    en cours et non modifiés depuis STALE_TEMP_AGE secondes sont visés.
    Ménage facultatif : un fichier qu'on ne peut ni lister ni supprimer est
    laissé tel quel sans gêner l'écriture qui suit."""
    import time
    now = time.time()
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return
    with _active_temp_lock:
        active = set(_active_temp_files) | _kept_temp_files
    for entry in entries:
        name = entry.name
        if not (name.startswith(TEMP_FILE_PREFIX) and name.endswith(TEMP_FILE_SUFFIX)):
            continue
        path = os.path.normcase(os.path.abspath(entry.path))
        if path in active:
            continue
        try:
            if not entry.is_file() or now - entry.stat().st_mtime < STALE_TEMP_AGE:
                continue
            os.remove(entry.path)
        except OSError:
            continue


def make_temp_path(directory):
    """Crée un fichier temporaire vide dans `directory` (même volume que la
    destination, pour os.replace) et le déclare en cours d'utilisation, après
    avoir supprimé les temporaires abandonnés de ce dossier. À libérer par
    release_temp_path une fois remplacé ou supprimé."""
    import tempfile
    remove_stale_temp_files(directory)
    fd, tmp_path = tempfile.mkstemp(prefix=TEMP_FILE_PREFIX, suffix=TEMP_FILE_SUFFIX,
                                    dir=directory)
    os.close(fd)
    with _active_temp_lock:
        _active_temp_files.add(os.path.normcase(os.path.abspath(tmp_path)))
    return tmp_path


def release_temp_path(tmp_path):
    """Retire `tmp_path` des temporaires en cours d'utilisation."""
    with _active_temp_lock:
        _active_temp_files.discard(os.path.normcase(os.path.abspath(tmp_path)))


def replace_file_atomically(dest, write_to):
    """Écrit `dest` sans jamais laisser de fichier partiel ni abîmer l'existant.

    write_to(tmp_path) écrit le contenu complet dans un fichier temporaire créé
    dans le dossier de `dest` (même volume), qui remplace ensuite `dest` d'un
    seul coup par os.replace. En cas d'échec, y compris une interruption, le
    temporaire est supprimé et `dest` reste tel qu'il était. Un temporaire
    laissé par un arrêt brutal est supprimé à la prochaine écriture dans ce
    dossier (make_temp_path).

    shutil.move ne convient pas : sous Windows, os.rename refuse une destination
    existante et shutil.move bascule alors sur une copie qui tronque `dest`
    avant de la réécrire.

    Un `dest` qui est un lien symbolique n'est pas remplacé lui-même : c'est
    le fichier qu'il désigne qui reçoit le nouveau contenu, sinon le lien
    deviendrait un fichier ordinaire et sa cible garderait l'ancien contenu.
    """
    if os.path.islink(dest):
        dest = os.path.realpath(dest)
    tmp_path = make_temp_path(os.path.dirname(os.path.abspath(dest)))
    try:
        write_to(tmp_path)
        fsync_file(tmp_path)
        replace_file_retrying(tmp_path, dest)
    except BaseException:
        if not is_kept_temp(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise
    finally:
        release_temp_path(tmp_path)


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


class MissingPageDataError(ValueError):
    """Des fichiers du document n'ont pas de données : illisibles à l'ouverture
    de l'archive (CRC faux, compression non prise en charge, membre chiffré).
    Leur contenu n'existe que dans le fichier d'origine : écrire le document
    sans eux les ferait disparaître sans avertissement, et une conversion
    proposerait ensuite de supprimer l'original. Texte traduit à chaque str(),
    comme FileInUseError."""

    expected_error = True  # document refusé : jamais écrit dans le journal des erreurs

    def __init__(self, names):
        super().__init__(", ".join(names))
        self.names = list(names)

    def __str__(self):
        from modules.qt.localization import _
        lines = [f"• {n}" for n in self.names[:10]]
        if len(self.names) > 10:
            lines.append("…")
        return _("messages.errors.pages_without_data", names="\n".join(lines))


def send_to_trash(path):
    """Met `path` à la Corbeille Windows. Seule implémentation de l'application
    pour « mettre à la Corbeille » : l'utilisateur qui l'a choisie doit
    pouvoir récupérer le fichier, quel que soit le chemin (conversion,
    enregistrement, Bibliothèque, lots).

    send2trash, embarqué, fait le travail ; s'il manque (installation
    incomplète), repli sur SHFileOperationW avec FOF_ALLOWUNDO — jamais une
    suppression définitive à la place. Lève OSError si le fichier n'a pas
    été déplacé (la source reste alors en place)."""
    try:
        from send2trash import send2trash
    except ImportError:
        send2trash = None
    if send2trash is not None:
        send2trash(path)
        return

    import ctypes
    from ctypes import wintypes

    class _SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 3
    # FOF_ALLOWUNDO (Corbeille) | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    flags = 0x0040 | 0x0010 | 0x0004 | 0x0400
    op = _SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = os.path.abspath(path) + "\0\0"
    op.fFlags = flags
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0 or op.fAnyOperationsAborted or os.path.exists(path):
        raise OSError(result or 1, "SHFileOperationW", path)


class WrittenFileCheckError(OSError):
    """Le CBZ qui vient d'être écrit ne se relit pas correctement : sa source
    (archive convertie, fichier d'origine) ne doit pas être supprimée. Texte
    traduit à chaque str(), comme FileInUseError."""

    def __init__(self, path, detail=None, written=None, expected=None):
        super().__init__(path)
        self.path = path
        self.detail = detail
        self.written = written
        self.expected = expected

    def __str__(self):
        from modules.qt.localization import _
        if self.detail is None:
            detail = _("messages.errors.written_file_count",
                       written=self.written, expected=self.expected)
        else:
            detail = self.detail
        return _("messages.errors.written_file_check_failed",
                 name=os.path.basename(self.path), detail=detail)


def verify_written_cbz(path, expected_count=None, shown_path=None):
    """Relit entièrement le CBZ `path` qui vient d'être écrit, avant de
    supprimer la source dont il prend la place : chaque fichier est
    décompressé et son CRC contrôlé (ZipFile.testzip), et, si expected_count
    est donné, le nombre de fichiers (dossiers exclus) doit lui être égal.
    Un CBZ abîmé à l'écriture (disque défaillant, support amovible retiré,
    pilote réseau) passerait sinon pour réussi, et l'original, seule copie
    intacte, partirait à la corbeille. shown_path : chemin nommé dans le
    message (le fichier final quand `path` est son temporaire). Lève
    WrittenFileCheckError."""
    import zipfile
    shown = shown_path or path
    try:
        with zipfile.ZipFile(path) as zf:
            bad_member = zf.testzip()
            count = sum(1 for info in zf.infolist() if not info.is_dir())
    except Exception as e:
        raise WrittenFileCheckError(shown, detail=str(e) or type(e).__name__) from e
    if bad_member is not None:
        raise WrittenFileCheckError(shown, detail=bad_member)
    if expected_count is not None and count != expected_count:
        raise WrittenFileCheckError(shown, written=count, expected=expected_count)


def check_written_cbz_structure(path, expected_count, shown_path=None):
    """Contrôle rapide de la structure du CBZ `path` qui vient d'être écrit,
    sans décompresser aucune page : répertoire central lisible (absent d'un
    fichier tronqué), nombre de fichiers (dossiers exclus) égal à
    expected_count, en-tête local de chaque membre présent et portant le nom
    du répertoire, données de chaque membre ne débordant pas sur le suivant.
    Durée indépendante de la taille des pages, d'où son usage à chaque
    enregistrement ; verify_written_cbz, qui contrôle aussi les CRC, reste
    obligatoire avant de supprimer une source. shown_path : chemin nommé dans
    le message (le fichier final quand `path` est son temporaire). Lève
    WrittenFileCheckError."""
    import zipfile
    shown = shown_path or path
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            # zf.open lit et contrôle l'en-tête local (signature, nom) ; aucune
            # donnée n'est lue tant que le membre ouvert n'est pas lu.
            for info in infos:
                with zf.open(info):
                    pass
            end_of_data = zf.start_dir
    except Exception as e:
        raise WrittenFileCheckError(shown, detail=str(e) or type(e).__name__) from e
    ordered = sorted(infos, key=lambda i: i.header_offset)
    for info, following in zip(ordered, ordered[1:] + [None]):
        limit = following.header_offset if following is not None else end_of_data
        if info.header_offset + info.compress_size > limit:
            raise WrittenFileCheckError(shown, detail=info.filename)
    count = sum(1 for info in infos if not info.is_dir())
    if count != expected_count:
        raise WrittenFileCheckError(shown, written=count, expected=expected_count)


def cbz_members(entries):
    """[(nom dans le ZIP, entrée)] des entrées à écrire dans un CBZ (dossiers
    exclus), dans leur ordre.

    Lève MissingPageDataError si une entrée n'a pas de données. Deux entrées
    de même nom reçoivent des noms distincts (suffixe _2, _3… sur la seconde,
    jamais un nom déjà porté par une autre entrée) : dans un ZIP, la lecture
    par nom renvoie le contenu du dernier homonyme pour tous, et le premier
    serait perdu à la réouverture puis au prochain enregistrement."""
    files = [e for e in entries if not e.get("is_dir")]
    missing = [e.get("orig_name", "") for e in files if e.get("bytes") is None]
    if missing:
        raise MissingPageDataError(missing)
    taken = {e["orig_name"] for e in files}
    seen = set()
    members = []
    for entry in files:
        name = entry["orig_name"]
        if name in seen:
            stem, ext = os.path.splitext(name)
            n = 2
            while f"{stem}_{n}{ext}" in taken or f"{stem}_{n}{ext}" in seen:
                n += 1
            name = f"{stem}_{n}{ext}"
        seen.add(name)
        members.append((name, entry))
    return members


def file_signature(path):
    """État d'un fichier sur le disque : (date de modification en ns, taille),
    ou None s'il est absent ou inaccessible. Deux signatures différentes
    signifient que le fichier a été réécrit entre-temps."""
    try:
        st = os.stat(path)
    except (OSError, TypeError, ValueError):
        return None
    return (st.st_mtime_ns, st.st_size)


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


_WINDOWS_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)] + [f"LPT{i}" for i in range(1, 10)])
_WINDOWS_FORBIDDEN_CHARS = '<>:"|?*'


def windows_safe_component(part):
    """Un segment de chemin (nom de fichier ou de dossier) valide sous Windows.

    Un nom de page vient souvent d'une archive créée ailleurs. Écrit tel quel
    sur le disque, Windows le transforme sans le dire : « a:b.jpg » devient un
    flux caché du fichier « a » (vide à l'écran), un point ou une espace final
    est retiré (« x.exe. » est écrit « x.exe »), « CON.jpg » désigne la
    console. Les caractères interdits et de contrôle deviennent « _ », les
    points et espaces finaux sont retirés, un nom réservé est préfixé de « _ »."""
    part = "".join("_" if (c in _WINDOWS_FORBIDDEN_CHARS or ord(c) < 32) else c
                   for c in part).rstrip(". ")
    if not part:
        return "_"
    if part.split(".", 1)[0].rstrip(" ").upper() in _WINDOWS_RESERVED_NAMES:
        part = "_" + part
    return part


def windows_safe_relpath(name):
    """Chemin relatif (séparé par « / ») tiré d'un nom de page : chaque segment
    passe par windows_safe_component ; les segments vides, « . » et « .. »
    sont retirés, le résultat ne désigne donc jamais un dossier parent ni un
    chemin absolu."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    return "/".join(windows_safe_component(p) for p in parts) or "_"


def unique_names(names):
    """`names` rendus distincts sans tenir compte de la casse (Windows ne la
    distingue pas) : un nom déjà pris reçoit « (2) », « (3) »… avant son
    extension. Deux pages homonymes écrites dans un même dossier ne
    s'écrasent ainsi jamais l'une l'autre."""
    seen = set()
    result = []
    for name in names:
        stem, ext = os.path.splitext(name)
        candidate = name
        n = 2
        while candidate.lower() in seen:
            candidate = f"{stem} ({n}){ext}"
            n += 1
        seen.add(candidate.lower())
        result.append(candidate)
    return result


def _is_link_or_junction(path):
    """True si `path` est un lien symbolique ou une jonction NTFS. Les autres
    points d'analyse (dossiers OneDrive à la demande, déduplication) sont de
    vrais dossiers et ne sont pas concernés."""
    import stat
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    tag = getattr(st, "st_reparse_tag", 0)
    return tag in (getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1),
                   getattr(stat, "IO_REPARSE_TAG_SYMLINK", -1))


def walk_without_links(top):
    """os.walk qui n'entre ni dans les liens symboliques ni dans les jonctions
    NTFS. os.walk (Python 3.11) suit les jonctions : un traitement par lot
    lancé sur un dossier traiterait — et supprimerait après conversion — des
    fichiers situés ailleurs, et une jonction vers un dossier parent les
    ferait traiter plusieurs fois."""
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = [d for d in dirnames
                       if not _is_link_or_junction(os.path.join(dirpath, d))]
        yield dirpath, dirnames, filenames
