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


def format_file_size(size_bytes):
    """
    Convertit une taille en octets en format lisible (o, Ko, Mo, Go, To).

    Args:
        size_bytes: Taille en octets (int)

    Returns:
        str: Taille formatée (ex: "1.5 Mo")
    """
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


def safe_join(base, name):
    """
    Joint `name` à `base` en garantissant que le résultat reste à l'intérieur
    de `base`. Préserve les sous-dossiers légitimes (ex. "chapitre1/page01.jpg").

    Protège contre la traversée de répertoire (Zip Slip) : un nom contenant
    "../", un chemin absolu ou une autre lettre de lecteur produit un chemin
    hors de `base`.

    Returns:
        str: le chemin absolu sûr si `name` reste sous `base`.
        None: si `name` tente de sortir de `base` (l'appelant doit ignorer l'entrée).
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
