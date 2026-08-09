"""
modules/qt/scan_dialog_qt.py — Import d'images depuis un scanner (WIA), version PySide6.
Reproduit le pattern de web_import_qt.py (worker QThread, overlay de progression,
dialogues d'erreur non-modales). Logique WIA pure dans modules/qt/scan_wia.py.
Règles UI Qt : thème, langue à la volée, police courante, non-modale, _wt() pour le titre.
"""

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QRadioButton,
    QButtonGroup, QWidget, QMenu,
)

from modules.qt.localization import _, _wt
from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.dialogs_qt import (
    ErrorDialog, InfoDialog, position_dialog_on_parent, _center_on_widget,
)
from modules.qt.canvas_overlay_qt import show_canvas_text as _show_canvas_text, hide_canvas_text as _hide_canvas_text
from modules.qt.archive_loader import _natural_sort_key
from modules.qt.entries import create_entry
from modules.qt.scan_wia import (
    list_scanner_devices, get_device_capabilities, scan_image, find_escl_fallback,
    get_scan_log_path, scan_log_exists, ScanError, _log_scan_event,
)

import modules.qt.state as _state_module


IMAGE_EXTS = (
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
    '.tiff', '.tif', '.ico', '.jfif', '.pjpeg', '.pjp', '.avif',
)


def _set_scan_active(dialog_parent, panel, active: bool) -> None:
    """Pose/lève le flag "un scan est en cours" sur MainWindow (voir
    MainWindow._set_scan_active) — un seul scan à la fois entre les deux
    panneaux. dialog_parent est toujours le PanelWidget ayant ouvert la
    fenêtre de scan (self.parent() de ScanDialog, primaire ou secondaire) ;
    dialog_parent._main_window donne accès à MainWindow, qui porte l'état
    partagé entre panneaux."""
    dialog_parent._main_window._set_scan_active(panel, active)


# ═══════════════════════════════════════════════════════════════════════════════
# Journal de scan — ouverture directe / dans l'Explorateur
# ═══════════════════════════════════════════════════════════════════════════════

def open_scan_log() -> None:
    """Ouvre Log_scan.txt avec l'application par défaut (Bloc-notes ou
    équivalent configuré par l'utilisateur) — même pattern que
    batch_dialogs_qt.py::_open_path. Silencieux si le fichier n'existe pas
    encore (aucune numérisation faite) ou si l'ouverture échoue."""
    import os
    path = get_scan_log_path()
    if not os.path.exists(path):
        return
    try:
        os.startfile(path)
    except Exception:
        pass


def reveal_scan_log() -> None:
    """Ouvre l'Explorateur Windows avec le focus sur Log_scan.txt — voir
    skill explorer-select, toujours _explorer_select(), jamais un appel
    explorer /select ad hoc. Silencieux si le fichier n'existe pas encore."""
    import os
    path = get_scan_log_path()
    if not os.path.exists(path):
        return
    from modules.qt.library_window import _explorer_select
    _explorer_select(path.replace('/', '\\'))


def show_scan_log_context_menu(parent, global_pos) -> None:
    """Menu contextuel à 2 entrées (ouvrir le log / l'afficher dans
    l'Explorateur) — même thème/police que les autres menus contextuels du
    projet (_make_menu, context_menus_qt.py), réutilisé plutôt que dupliqué.
    Utilisé par le clic droit sur l'icône scan, et pourrait l'être ailleurs
    si un futur point d'entrée en a besoin.

    Les deux entrées sont grisées (jamais juste no-op au clic) tant que
    Log_scan.txt n'existe pas — jamais eu de scan, ou fichier temp purgé/
    supprimé manuellement (voir skill temp-files)."""
    from modules.qt.context_menus_qt import _make_menu, _add_disabled
    log_exists = scan_log_exists()
    menu = _make_menu(parent)
    if log_exists:
        act_open   = menu.addAction(_("scan.open_log"))
        act_reveal = menu.addAction(_("scan.reveal_log"))
    else:
        act_open = _add_disabled(menu, _("scan.open_log"))
        act_reveal = _add_disabled(menu, _("scan.reveal_log"))
    chosen = menu.exec(global_pos)
    if not log_exists:
        return
    if chosen == act_open:
        open_scan_log()
    elif chosen == act_reveal:
        reveal_scan_log()


# ═══════════════════════════════════════════════════════════════════════════════
# Workers
# ═══════════════════════════════════════════════════════════════════════════════

class _ScanListWorker(QThread):
    """Énumère les scanners WIA disponibles dans un thread séparé."""

    finished_ok = Signal(list)    # list[dict] {"id", "name"}
    failed      = Signal(str, str)  # (message_key, detail)

    def run(self):
        import pythoncom
        pythoncom.CoInitialize()
        try:
            devices = list_scanner_devices()
            self.finished_ok.emit(devices)
        except ScanError as e:
            self.failed.emit(e.message_key, e.detail)
        except Exception as e:
            self.failed.emit("scan.error_wia_unavailable", str(e))
        finally:
            pythoncom.CoUninitialize()


class _CapsWorker(QThread):
    """Interroge les capacités (résolutions/modes couleur supportés) d'un
    device dans un thread séparé. Déclenché une seule fois par changement réel
    de sélection dans le combo (voir ScanDialog._on_device_changed) — jamais en
    parallèle d'un scan, pour éviter le conflit de connexion WIA documenté dans
    le skill scan (device signalé "occupé" par une version antérieure qui
    laissait cette connexion ouverte pendant le scan réel)."""

    finished_ok = Signal(dict)
    failed      = Signal(str, str)

    def __init__(self, device_id: str):
        super().__init__()
        self._device_id = device_id

    def run(self):
        import pythoncom
        pythoncom.CoInitialize()
        try:
            caps = get_device_capabilities(self._device_id)
            self.finished_ok.emit(caps)
        except ScanError as e:
            self.failed.emit(e.message_key, e.detail)
        except Exception as e:
            self.failed.emit("scan.error_device_unreachable", str(e))
        finally:
            pythoncom.CoUninitialize()


class _EsclFallbackWorker(QThread):
    """Cherche un device ESCL de repli dans un thread séparé (voir
    scan_wia.find_escl_fallback). Comme les autres workers COM de ce fichier,
    a besoin de son propre pythoncom.CoInitialize()/CoUninitialize() — appeler
    find_escl_fallback() directement depuis un slot Qt (thread UI, jamais
    initialisé pour COM) est un bug réel constaté : sans ce worker dédié, le
    device natif disparaissait de WIA.DeviceManager.DeviceInfos à l'ouverture
    suivante de ScanDialog après chaque déclenchement du repli ESCL."""

    finished_ok = Signal(object)  # dict | None

    def __init__(self, failed_device_id: str):
        super().__init__()
        self._failed_device_id = failed_device_id

    def run(self):
        import pythoncom
        pythoncom.CoInitialize()
        try:
            candidate = find_escl_fallback(self._failed_device_id)
            self.finished_ok.emit(candidate)
        finally:
            pythoncom.CoUninitialize()


class _ScanWorker(QThread):
    """Exécute le scan dans un thread séparé."""

    finished_ok = Signal(bytes)
    failed      = Signal(str, str)

    def __init__(self, device_id: str, settings: dict):
        super().__init__()
        self._device_id = device_id
        self._settings  = settings

    def run(self):
        import pythoncom
        pythoncom.CoInitialize()
        try:
            img_bytes = scan_image(self._device_id, self._settings)
            self.finished_ok.emit(img_bytes)
        except ScanError as e:
            self.failed.emit(e.message_key, e.detail)
        except Exception as e:
            self.failed.emit("scan.error_transfer_failed", str(e))
        finally:
            pythoncom.CoUninitialize()


# ═══════════════════════════════════════════════════════════════════════════════
# Contrôleur de scan (overlay progression, identique au pattern web_import)
# ═══════════════════════════════════════════════════════════════════════════════

class ScanController:
    """Lance le scan et gère l'overlay rouge sur le canvas pendant l'acquisition."""

    def __init__(self, canvas, device_id: str, settings: dict, callbacks: dict, dialog_parent):
        self._canvas        = canvas
        self._callbacks     = callbacks
        self._dialog_parent = dialog_parent
        self._item_holder   = [None]
        self._device_id     = device_id
        self._settings      = settings

        # Masque le texte "canvas vide" pendant le scan — sinon il reste visible
        # sous l'overlay rouge (couches différentes : scène vs widget enfant du
        # viewport) quand aucun fichier n'est encore ouvert.
        self._canvas._loading = True
        from shiboken6 import isValid
        for it in list(self._canvas._empty_items):
            if isValid(it) and it.scene() is self._canvas.scene():
                self._canvas.scene().removeItem(it)
        self._canvas._empty_items.clear()

        _show_canvas_text(self._canvas, _("scan.progress_scanning"), self._item_holder)

        self._worker = _ScanWorker(device_id, settings)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._cleanup)
        self._worker.start()

    def _cleanup(self):
        # Détache la référence forte gardée sur le canvas une fois le thread
        # terminé (voir ScanDialog._start_scan) — sinon on accumule des
        # ScanController en mémoire à chaque scan.
        controllers = getattr(self._canvas, '_scan_controllers', None)
        if controllers is not None and self in controllers:
            controllers.remove(self)

    def _hide_overlay(self):
        _hide_canvas_text(self._canvas, self._item_holder)
        self._canvas._loading = False

    def _on_finished(self, img_bytes: bytes):
        # Scan réussi = fin du cycle, rien ne pourra plus le relancer (pas de
        # repli ESCL en jeu ici) — lève le flag "scan en cours" (voir skill
        # scan, section "Un seul scan à la fois entre les deux panneaux").
        _set_scan_active(self._dialog_parent, self._dialog_parent, False)
        self._hide_overlay()
        state = self._callbacks.get('state') or _state_module.state

        # Numéroté (pas horodaté, pas de préfixe "NEW-") pour que le tri naturel
        # (_natural_sort_key, comparaison lexicographique des segments texte)
        # place l'image scannée après toutes les pages numérotées existantes.
        # Un préfixe alphabétique ("NEW-scan_0002" < "scan_0001") casserait ce
        # tri numérique — contrairement à web_import, on n'a pas besoin d'éviter
        # une collision avec des noms d'archive existants ici : "scan_NNNN" est
        # déjà garanti unique dans la mosaïque par construction (NNNN = position).
        page_number = sum(1 for e in state.images_data if e.get("is_image")) + 1
        filename = f"scan_{page_number:04d}.jpg"

        entry = create_entry(filename, img_bytes, IMAGE_EXTS)
        entry["source_archive"] = "scan"
        _add_entry_to_mosaic(entry, self._callbacks)

    def _on_failed(self, message_key: str, detail: str):
        self._hide_overlay()
        self._canvas.render_mosaic()
        play_sound = message_key in ("scan.error_wia_unavailable", "scan.error_transfer_failed")

        # Recherche du candidat ESCL dans un QThread dédié (avec son propre
        # pythoncom.CoInitialize()/CoUninitialize()), jamais en appel direct
        # depuis ce slot Qt — voir _EsclFallbackWorker : un appel COM synchrone
        # sur le thread UI (jamais initialisé pour COM) corrompait l'état de
        # WIA.DeviceManager, faisant disparaître le device natif de
        # DeviceInfos à l'ouverture suivante de ScanDialog (bug réel constaté,
        # pas un défaut du driver HP comme supposé auparavant).
        self._escl_lookup_worker = _EsclFallbackWorker(self._device_id)
        self._escl_lookup_worker.finished_ok.connect(
            lambda candidate: self._on_escl_lookup_done(candidate, message_key, play_sound)
        )
        self._escl_lookup_worker.start()

    def _on_escl_lookup_done(self, escl_candidate, message_key: str, play_sound: bool):
        if escl_candidate is None:
            # Aucun repli possible, fin du cycle — lève le flag "scan en
            # cours" tout de suite (voir skill scan, section "Un seul scan à
            # la fois entre les deux panneaux").
            _set_scan_active(self._dialog_parent, self._dialog_parent, False)

        def _retry_via_escl():
            _log_scan_event(
                f"  ESCL fallback: user chose to retry scan via id={escl_candidate['id']}\n"
            )
            # Attend que le thread du scan natif qui vient d'échouer soit
            # complètement terminé (CoUninitialize() dans son "finally", pas
            # encore forcément exécuté au moment du signal failed/clic
            # utilisateur) avant de relancer une connexion COM sur le même
            # scanner physique via ESCL — sinon "Le périphérique WIA est
            # occupé" côté driver (constaté en test). Quasi instantané ici
            # (run() est déjà en toute fin d'exécution), pas un vrai freeze UI.
            self._worker.wait()
            new_controllers = getattr(self._canvas, '_scan_controllers', None)
            controller = ScanController(
                self._canvas, escl_candidate["id"], self._settings, self._callbacks, self._dialog_parent
            )
            if new_controllers is not None:
                new_controllers.append(controller)

        # Si escl_candidate existe et que l'utilisateur ferme cette erreur
        # SANS cliquer sur "Essayer via ESCL" (Fermer, croix), le flag "scan
        # en cours" doit être levé aussi — sinon plus rien ne le lèverait,
        # bloquant l'autre panneau indéfiniment. _show_scan_error_with_log_link
        # gère ce cas via son propre paramètre on_dialog_closed_without_retry.
        def _on_closed_without_retry():
            _set_scan_active(self._dialog_parent, self._dialog_parent, False)

        _show_scan_error_with_log_link(
            self._dialog_parent, message_key, play_sound=play_sound,
            escl_candidate=escl_candidate, on_retry_escl=_retry_via_escl,
            on_dialog_closed_without_retry=_on_closed_without_retry if escl_candidate else None,
        )


def _add_entry_to_mosaic(entry: dict, callbacks: dict) -> None:
    state = callbacks.get('state') or _state_module.state

    save_state             = callbacks['save_state']
    render_mosaic          = callbacks['render_mosaic']
    update_button_text     = callbacks.get('update_button_text', lambda: None)
    update_create_cbz_btn  = callbacks.get('update_create_cbz_button', lambda: None)
    clear_selection        = callbacks.get('clear_selection', lambda: None)

    if not state.images_data:
        save_state()

    state.images_data.append(entry)
    state.images_data.sort(key=lambda e: _natural_sort_key(e["orig_name"]))
    state.modified = True

    if any(e.get("is_image", False) for e in state.images_data):
        state.needs_renumbering = True

    clear_selection()
    render_mosaic()
    update_button_text()
    update_create_cbz_btn()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialogue de paramètres de scan
# ═══════════════════════════════════════════════════════════════════════════════

_RESOLUTION_CHOICES = [75, 150, 200, 300, 600, 1200]
_DEFAULT_DPI = 300


def _connect_lang(dialog, handler):
    from modules.qt.language_signal import language_signal
    dialog._lang_handler = handler
    language_signal.changed.connect(dialog._lang_handler)
    dialog.finished.connect(lambda: _disconnect_lang(dialog))


def _disconnect_lang(dialog):
    from modules.qt.language_signal import language_signal
    try:
        language_signal.changed.disconnect(dialog._lang_handler)
    except RuntimeError:
        pass


def _send_scan_log_by_mail(parent) -> None:
    """Copie le contenu complet de Log_scan.txt dans le presse-papiers (taille
    illimitée, fiable à 100% — contrairement au paramètre body: d'un lien
    mailto:, dont la limite de longueur varie et n'est pas garantie selon le
    client mail, voir historique de discussion dans le skill scan) et ouvre le
    client mail par défaut vers l'adresse MosaicView, avec dans le corps une
    instruction de collage (Ctrl+V) au lieu du contenu lui-même.

    Le corps du mail utilise _wt() et non _() : un texte transmis tel quel à un
    client mail externe doit rester lisible même si la langue active est une
    variante CSUR (klingon pIqaD, sindarin/quenya Tengwar) — mêmes contraintes
    que les titres de fenêtre, voir règle UI n°7 du CLAUDE.md."""
    import os
    import urllib.parse
    import webbrowser
    from modules.qt.temp_files import get_mosaicview_temp_dir
    from modules.qt.utils import _copy_to_clipboard

    log_path = os.path.join(get_mosaicview_temp_dir(), "Log_scan.txt")
    try:
        with open(log_path, encoding="utf-8") as f:
            content = f.read()
    except Exception:
        content = ""

    if not content.strip():
        InfoDialog(
            parent,
            lambda: _wt("scan.dialog_title"),
            lambda: _("scan.no_log_yet"),
        ).show_nonmodal()
        return

    _copy_to_clipboard(content)
    body = urllib.parse.quote(_wt("scan.mail_body_hint") + "\n\n")
    webbrowser.open(f"mailto:mosaicview1969@gmail.com?subject=MosaicView%20-%20Scan%20log&body={body}")


def _show_scan_error_with_log_link(
    parent, message_key: str, play_sound: bool = False,
    escl_candidate: dict | None = None, on_retry_escl=None,
    on_dialog_closed_without_retry=None,
) -> None:
    """ErrorDialog générique du projet (dialogs_qt.py) + lien discret d'envoi du
    journal de scan, ajouté par-dessus sans modifier la classe partagée (voir
    scan skill, section pièges — ErrorDialog n'a pas de point d'extension prévu
    pour un widget additionnel, réservé à ce dialogue plutôt que généralisé).

    Si escl_candidate n'est pas None (un device WIA non-natif a été trouvé pour
    la même machine, voir scan_wia.find_escl_fallback), ajoute aussi un texte
    d'accompagnement + un bouton proposant d'essayer ce device en repli. Le
    choix reste entièrement manuel — jamais de bascule automatique et
    silencieuse (voir skill scan, section "Repli ESCL")."""
    has_escl_proposal = escl_candidate is not None and on_retry_escl is not None
    dlg = ErrorDialog(
        parent,
        lambda: _wt("scan.dialog_title"),
        lambda k=message_key: _(k),
        play_sound=play_sound,
        # Avec la proposition ESCL, "OK" seul seul est ambigu (il y a désormais
        # un vrai autre choix d'action à côté) : "Fermer" clarifie qu'il ferme
        # la fenêtre sans rien tenter d'autre. Sans proposition ESCL,
        # comportement inchangé (texte "OK" par défaut).
        ok_text_key="buttons.close" if has_escl_proposal else "buttons.ok",
    )

    # Le bouton OK/Fermer d'ErrorDialog est déjà dans le layout à ce stade
    # (2e widget, juste après le message) — tout widget ajouté par simple
    # addWidget() atterrirait donc APRÈS lui. Les widgets ESCL/lien mail
    # doivent au contraire apparaître AVANT le bouton de fermeture (qui reste
    # la dernière action, en bas), d'où insertWidget() à un index croissant
    # explicite plutôt que addWidget().
    layout = dlg.layout()
    insert_at = layout.indexOf(dlg._btn_ok)

    from modules.qt.language_signal import language_signal
    retranslate_fns = []

    if has_escl_proposal:
        lbl_hint = QLabel(dlg)
        lbl_hint.setAlignment(Qt.AlignCenter)
        lbl_hint.setWordWrap(True)

        btn_escl = QPushButton(dlg)
        retry_clicked = [False]

        def _on_retry_clicked():
            retry_clicked[0] = True
            dlg.accept()
            on_retry_escl()

        btn_escl.clicked.connect(_on_retry_clicked)

        def _on_dlg_finished():
            # Fermeture sans avoir cliqué sur "Essayer via ESCL" (Fermer,
            # croix) — prévient l'appelant, qui doit lever son propre flag
            # "scan en cours" (voir ScanController._on_escl_lookup_done).
            if not retry_clicked[0] and on_dialog_closed_without_retry:
                on_dialog_closed_without_retry()

        dlg.finished.connect(_on_dlg_finished)

        def _retranslate_escl(*_a):
            theme = get_current_theme()
            lbl_hint.setText(_("scan.escl_fallback_hint"))
            lbl_hint.setFont(_get_current_font(9))
            lbl_hint.setStyleSheet(f"color: {theme['text']}; background: transparent; font-style: italic;")
            btn_escl.setText(_("scan.escl_fallback_button"))
            btn_escl.setFont(_get_current_font(10))

        retranslate_fns.append(_retranslate_escl)
        _retranslate_escl()
        layout.insertWidget(insert_at, lbl_hint)
        insert_at += 1
        layout.insertWidget(insert_at, btn_escl, alignment=Qt.AlignCenter)
        insert_at += 1

    lbl = QLabel(dlg)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setCursor(Qt.PointingHandCursor)
    lbl.mousePressEvent = lambda e: _send_scan_log_by_mail(dlg) if e.button() == Qt.LeftButton else None

    def _retranslate_link(*_a):
        theme = get_current_theme()
        lbl.setText(_("scan.send_log_link"))
        lbl.setFont(_get_current_font(8))
        lbl.setStyleSheet(f"color: {theme['disabled']}; text-decoration: underline; background: transparent;")

    retranslate_fns.append(_retranslate_link)
    _retranslate_link()

    def _retranslate_all(*_a):
        for fn in retranslate_fns:
            fn()

    def _disconnect_link():
        try:
            language_signal.changed.disconnect(_retranslate_all)
        except RuntimeError:
            pass

    language_signal.changed.connect(_retranslate_all)
    dlg.finished.connect(_disconnect_link)

    layout.insertWidget(insert_at, lbl, alignment=Qt.AlignCenter)
    dlg.show_nonmodal()


class ScanDialog(QDialog):
    """
    Fenêtre de paramètres de scan : sélection du device, résolution, mode couleur.
    Non-modale. Supporte thème, langue à la volée, police courante.
    """

    def __init__(self, parent, canvas, callbacks: dict):
        super().__init__(parent)
        self._canvas    = canvas
        self._callbacks = callbacks
        self._devices   = []       # [{"id", "name"}, ...]
        self._center_parent = parent

        # Dernier device/dpi/mode couleur choisis (session précédente), voir
        # skill scan. Lu une seule fois ici ; réappliqué au device (dans
        # _on_devices_listed) et au mode couleur (juste ci-dessous — les
        # radios sont construits immédiatement et self._radio_color est
        # coché par défaut, donc la préférence doit être appliquée dès la
        # construction, pas seulement quand les capacités arrivent).
        from modules.qt.config_manager import get_config_manager
        self._last_settings = get_config_manager().get_scan_last_settings()

        # Un seul scan à la fois entre les deux panneaux (voir skill scan) :
        # posé dès l'ouverture de cette fenêtre de réglages, levé soit ici en
        # cas d'annulation (_on_close_no_scan, jamais lancé de scan), soit par
        # ScanController à la toute fin du cycle de scan (voir _start_scan et
        # _set_scan_active dans ScanController — pas ici, un scan peut encore
        # tourner après la fermeture de CETTE fenêtre).
        self._scan_launched = False
        _set_scan_active(parent, parent, True)
        self.finished.connect(self._on_close_no_scan)

        self.setWindowFlags(Qt.Window)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setFixedSize(420, 326)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(10)

        self._lbl_title = QLabel()
        self._lbl_title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_title)

        # ── Sélecteur de device ──────────────────────────────────────────────
        self._lbl_device = QLabel()
        self._lbl_device.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_device)

        self._combo_device = QComboBox()
        layout.addWidget(self._combo_device)

        # ── Résolution ────────────────────────────────────────────────────────
        self._lbl_resolution = QLabel()
        self._lbl_resolution.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_resolution)

        self._combo_resolution = QComboBox()
        # Volontairement VIDE au départ, pas pré-rempli avec _RESOLUTION_CHOICES :
        # le contenu réel dépend de _CapsWorker (voir _on_device_changed), qui
        # met 1 à 2s à répondre. Pré-remplir avec une liste optimiste laissait
        # l'utilisateur cliquer sur une résolution non confirmée par le device
        # (ex. 1200 DPI affiché puis retiré une fois la vraie réponse connue) —
        # bug réel constaté en test. La liste ne s'affiche qu'une fois confirmée
        # (par le driver ou par le repli en cas de timeout, voir _on_caps_timeout).
        layout.addWidget(self._combo_resolution)

        # ── Mode couleur ──────────────────────────────────────────────────────
        self._lbl_color = QLabel()
        self._lbl_color.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_color)

        color_row = QHBoxLayout()
        color_row.addStretch()
        self._radio_color     = QRadioButton()
        self._radio_grayscale = QRadioButton()
        self._radio_bw        = QRadioButton()
        self._radio_color.setChecked(True)
        self._color_group = QButtonGroup(self)
        for rb, mode in ((self._radio_color, "color"), (self._radio_grayscale, "grayscale"), (self._radio_bw, "bw")):
            self._color_group.addButton(rb)
            rb.setProperty("_scan_color_mode", mode)
            color_row.addWidget(rb)
        # Réapplique le dernier mode couleur choisi (session précédente) —
        # sera de nouveau ajusté dans _on_caps_ready si ce mode n'est pas
        # supporté par le device réellement sélectionné.
        last_color_mode = self._last_settings.get("color_mode")
        if last_color_mode:
            for rb in (self._radio_color, self._radio_grayscale, self._radio_bw):
                if rb.property("_scan_color_mode") == last_color_mode:
                    rb.setChecked(True)
                    break
        color_row.addStretch()
        layout.addLayout(color_row)

        # ── Boutons ───────────────────────────────────────────────────────────
        layout.addStretch()
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_scan = QPushButton()
        self._btn_scan.setFixedWidth(120)
        self._btn_scan.setDefault(True)
        self._btn_scan.clicked.connect(self._start_scan)
        self._btn_cancel = QPushButton()
        self._btn_cancel.setFixedWidth(120)
        self._btn_cancel.clicked.connect(self.close)
        btn_row.addWidget(self._btn_scan)
        btn_row.addSpacing(16)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── Lien discret : envoyer le journal de scan par mail (support) ────────
        self._lbl_send_log = QLabel()
        self._lbl_send_log.setAlignment(Qt.AlignCenter)
        self._lbl_send_log.setCursor(Qt.PointingHandCursor)
        self._lbl_send_log.mousePressEvent = (
            lambda e: _send_scan_log_by_mail(self) if e.button() == Qt.LeftButton else None
        )
        layout.addWidget(self._lbl_send_log)

        self._set_controls_enabled(False)
        self._retranslate()
        _connect_lang(self, lambda _l: self._retranslate())

        self._list_worker = _ScanListWorker()
        self._list_worker.finished_ok.connect(self._on_devices_listed)
        self._list_worker.failed.connect(self._on_list_failed)
        self._list_worker.start()

    def showEvent(self, event):
        super().showEvent(event)
        if self._center_parent and not event.spontaneous():
            p = self._center_parent
            QTimer.singleShot(0, lambda: _center_on_widget(self, p))

    def _set_controls_enabled(self, enabled: bool):
        self._combo_device.setEnabled(enabled)
        self._combo_resolution.setEnabled(enabled)
        self._radio_color.setEnabled(enabled)
        self._radio_grayscale.setEnabled(enabled)
        self._radio_bw.setEnabled(enabled)
        self._btn_scan.setEnabled(enabled)

    # ── Traduction / thème / police ───────────────────────────────────────────

    def _retranslate(self):
        theme = get_current_theme()
        bg    = theme["bg"]
        fg    = theme["text"]
        tb_bg = theme["toolbar_bg"]
        sep   = theme["separator"]

        self.setStyleSheet(
            f"QDialog     {{ background: {bg}; color: {fg}; }}"
            f"QLabel      {{ background: {bg}; color: {fg}; }}"
            f"QComboBox   {{ background: {tb_bg}; color: {fg}; border: 1px solid {sep}; padding: 2px 4px; }}"
            f"QRadioButton{{ background: {bg}; color: {fg}; }}"
        )
        btn_style = (
            f"QPushButton {{ background: {tb_bg}; color: {fg}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {sep}; }}"
        )

        font10 = _get_current_font(10)
        font12 = _get_current_font(12, bold=True)

        self.setWindowTitle(_wt("scan.dialog_title"))

        self._lbl_title.setText(_("scan.dialog_title"))
        self._lbl_title.setFont(font12)

        self._lbl_device.setText(_("scan.device_label"))
        self._lbl_device.setFont(font10)
        self._combo_device.setFont(font10)
        if self._combo_device.count() == 0:
            self._combo_device.addItem(_("scan.searching_devices"))

        self._lbl_resolution.setText(_("scan.resolution_label"))
        self._lbl_resolution.setFont(font10)
        self._combo_resolution.setFont(font10)
        if self._combo_resolution.count() == 0:
            self._combo_resolution.addItem(_("scan.checking_capabilities"))

        self._lbl_color.setText(_("scan.color_mode_label"))
        self._lbl_color.setFont(font10)
        self._radio_color.setText(_("scan.color_mode_color"))
        self._radio_color.setFont(font10)
        self._radio_grayscale.setText(_("scan.color_mode_grayscale"))
        self._radio_grayscale.setFont(font10)
        self._radio_bw.setText(_("scan.color_mode_bw"))
        self._radio_bw.setFont(font10)

        self._btn_scan.setText(_("scan.scan_button"))
        self._btn_scan.setFont(font10)
        self._btn_scan.setStyleSheet(btn_style)

        self._btn_cancel.setText(_("web.import_web_cancel_button"))
        self._btn_cancel.setFont(font10)
        self._btn_cancel.setStyleSheet(btn_style)

        self._lbl_send_log.setText(_("scan.send_log_link"))
        self._lbl_send_log.setFont(_get_current_font(8))
        self._lbl_send_log.setStyleSheet(
            f"color: {theme['disabled']}; text-decoration: underline;"
        )

    # ── Chargement des devices / capacités ──────────────────────────────────────

    def _on_devices_listed(self, devices: list):
        self._devices = devices
        self._combo_device.clear()
        if not devices:
            InfoDialog(
                self.parent(),
                lambda: _wt("scan.dialog_title"),
                lambda: _("scan.no_devices_found"),
            ).show_nonmodal()
            self.close()
            return
        for d in devices:
            self._combo_device.addItem(d["name"], d["id"])
        self._set_controls_enabled(True)

        # Représélectionne le dernier device utilisé (session précédente) s'il
        # est toujours présent dans la liste actuelle — sinon garde l'index 0
        # par défaut (device débranché/renommé entre deux sessions).
        last_device_id = self._last_settings.get("device_id")
        if last_device_id:
            for i, d in enumerate(devices):
                if d["id"] == last_device_id:
                    self._combo_device.setCurrentIndex(i)
                    break

        # Connecté seulement maintenant (pas dans __init__) : les addItem()
        # ci-dessus déclencheraient sinon currentIndexChanged pour chaque
        # device ajouté, avant même que la liste ne soit complète.
        self._combo_device.currentIndexChanged.connect(self._on_device_changed)
        self._on_device_changed(self._combo_device.currentIndex())

    def _on_list_failed(self, message_key: str, detail: str):
        _show_scan_error_with_log_link(self.parent(), message_key)
        self.close()

    # Délai maximum d'attente de _CapsWorker avant repli sur la liste fixe —
    # généreux (l'interrogation la plus lente observée en test tournait autour
    # de 2-3s), pour ne jamais couper une réponse légitime juste un peu lente.
    _CAPS_TIMEOUT_MS = 8000

    def _on_device_changed(self, index: int):
        if index < 0 or index >= len(self._devices):
            return
        device_id = self._devices[index]["id"]
        # Combo résolution vide + désactivé tant que les capacités réelles ne
        # sont pas connues — jamais de liste pré-remplie optimiste affichée
        # avant confirmation par le device (voir commentaire à la construction
        # du widget) : cliquer sur une résolution non confirmée par le driver
        # est le bug réel qu'on corrige ici, pas seulement un souci d'affichage.
        self._combo_resolution.clear()
        self._combo_resolution.addItem(_("scan.checking_capabilities"))
        self._combo_resolution.setEnabled(False)
        # Bloque aussi le combo device (pas seulement le bouton Scanner) : un
        # second changement de device pendant que _CapsWorker tourne encore
        # créerait deux workers concurrents, chacun avec sa propre connexion
        # WIA — même risque de conflit que celui déjà rencontré et corrigé
        # entre _CapsWorker et le scan réel (voir skill scan).
        self._combo_device.setEnabled(False)
        self._btn_scan.setEnabled(False)

        # Cache persistant (%APPDATA%\MosaicView, voir ConfigManager.get_scan_
        # capabilities) : un device déjà interrogé avec succès lors d'une
        # session précédente affiche instantanément sa liste connue au lieu de
        # rester sur "Vérification..." 1-2s à chaque réouverture du dialogue —
        # le scanner ne change pas de capacités matérielles d'une session à
        # l'autre.
        from modules.qt.config_manager import get_config_manager
        cfg = get_config_manager()
        _log_scan_event(f"  Config read: scan_capabilities[{device_id}] from {cfg.get_config_file_path()}\n")
        cached_caps = cfg.get_scan_capabilities(device_id)
        if cached_caps:
            # Cache HIT : PAS de relance de _CapsWorker derrière (contrairement
            # à une version précédente qui rafraîchissait silencieusement le
            # cache à chaque ouverture). Preuve en log (session du 2026-08-08) :
            # une seule connexion _CapsWorker au device natif — isolée, sans
            # conflit avec quoi que ce soit d'autre, terminée normalement —
            # suffit à le faire disparaître de WIA.DeviceManager.DeviceInfos à
            # l'ouverture suivante de ScanDialog. Le driver HP ne semble tout
            # simplement pas supporter des connexions répétées rapprochées.
            # Le cache ne devient donc la seule source tant qu'il est valide ;
            # se re-synchronise naturellement à la prochaine fois où il sera
            # MISS (device jamais vu, ou cache vidé/changé de machine).
            _log_scan_event(f"  Config read: cache HIT — {cached_caps} (native device NOT re-queried, see skill scan)\n")
            self._on_caps_ready(cached_caps, worker=None, from_cache=True)
            self._caps_worker = None
            return

        _log_scan_event("  Config read: cache MISS (device never queried successfully before)\n")
        worker = _CapsWorker(device_id)
        self._caps_worker = worker
        _log_scan_event(f"  Resolution dropdown: querying capabilities for device_id={device_id}\n")
        # Les callbacks vérifient explicitement que le signal vient bien du
        # worker actuellement référencé (self._caps_worker is worker) — sans
        # ça, un ancien worker (connecté sur self, pas détruit par le simple
        # fait de réassigner self._caps_worker) peut émettre son résultat
        # APRÈS un worker plus récent et écraser le combo avec des données
        # périmées.
        worker.finished_ok.connect(lambda caps, w=worker: self._on_caps_ready(caps, w, device_id=device_id))
        worker.failed.connect(lambda mk, d, w=worker: self._on_caps_failed(mk, d, w))
        worker.start()

        # Repli si le device ne répond pas dans un délai raisonnable (scanner
        # réseau/WiFi lent, driver capricieux...) — sans ça, un device muet
        # laisserait le combo bloqué sur "Vérification..." indéfiniment,
        # empêchant tout scan. Le worker continue de tourner en arrière-plan
        # (on ne peut pas interrompre un appel COM en cours, voir skill scan) ;
        # s'il finit par répondre après coup, le garde-fou worker is self._caps_worker
        # dans _on_caps_ready/_on_caps_failed ignore ce résultat tardif puisque
        # _fallback_caps aura déjà pris sa place de "réponse retenue".
        QTimer.singleShot(self._CAPS_TIMEOUT_MS, lambda w=worker: self._on_caps_timeout(w))

    def _on_caps_timeout(self, worker) -> None:
        if worker is not self._caps_worker or self._combo_resolution.isEnabled():
            return  # déjà résolu (succès ou échec) avant l'expiration du délai
        _log_scan_event(
            f"  Resolution dropdown: TIMEOUT after {self._CAPS_TIMEOUT_MS}ms, "
            f"device still not answered — falling back to fixed list\n"
        )
        self._apply_caps_fallback("timeout")

    def _apply_caps_fallback(self, reason: str = "failed") -> None:
        """Repeuple le combo résolution avec la liste fixe _RESOLUTION_CHOICES
        et réactive les contrôles — utilisé aussi bien en cas d'échec explicite
        de _CapsWorker (reason="failed") qu'en cas de timeout (reason="timeout",
        device muet)."""
        _log_scan_event(f"  Resolution dropdown: using fallback list {_RESOLUTION_CHOICES} (reason={reason})\n")
        self._combo_device.setEnabled(True)
        self._btn_scan.setEnabled(True)
        self._combo_resolution.clear()
        for dpi in _RESOLUTION_CHOICES:
            self._combo_resolution.addItem(f"{dpi} DPI", dpi)
        # Reprend le dernier DPI choisi (session précédente) s'il est proposé
        # par cette liste de repli, sinon _DEFAULT_DPI comme avant.
        last_dpi = self._last_settings.get("dpi")
        default_dpi = last_dpi if last_dpi in _RESOLUTION_CHOICES else _DEFAULT_DPI
        self._combo_resolution.setCurrentIndex(_RESOLUTION_CHOICES.index(default_dpi))
        self._combo_resolution.setEnabled(True)

    def _on_caps_ready(self, caps: dict, worker=None, from_cache: bool = False, device_id: str | None = None):
        if worker is not None and worker is not self._caps_worker:
            _log_scan_event("  Resolution dropdown: ignoring stale _CapsWorker result (a newer query is active)\n")
            return
        _log_scan_event(f"  Resolution dropdown: applying {'cached' if from_cache else 'confirmed'} capabilities {caps}\n")
        # Persiste uniquement un résultat frais du device (pas un simple replay
        # du cache lui-même) — voir ConfigManager.set_scan_capabilities, skill
        # scan. Le cache sert à afficher instantanément à la prochaine ouverture
        # de ScanDialog pour ce même device, sans réinterroger le scanner.
        if not from_cache and device_id:
            from modules.qt.config_manager import get_config_manager
            cfg = get_config_manager()
            ok = cfg.set_scan_capabilities(device_id, caps)
            status = "OK" if ok else "FAILED (save_config returned False)"
            _log_scan_event(f"  Config write: scan_capabilities[{device_id}] = {caps} → {cfg.get_config_file_path()} — {status}\n")
        self._combo_device.setEnabled(True)
        self._btn_scan.setEnabled(True)

        current_dpi = self._combo_resolution.currentData()
        self._combo_resolution.blockSignals(True)
        self._combo_resolution.clear()
        resolutions = caps.get("resolutions") or _RESOLUTION_CHOICES
        for dpi in resolutions:
            self._combo_resolution.addItem(f"{dpi} DPI", dpi)
        # Reproduit le même choix qu'avant si toujours proposé, sinon retombe
        # sur le dernier DPI choisi en session précédente (si connu), sinon la
        # valeur la plus proche — pas de retour silencieux au premier de la liste.
        if current_dpi in resolutions:
            self._combo_resolution.setCurrentIndex(resolutions.index(current_dpi))
        else:
            target_dpi = current_dpi or self._last_settings.get("dpi") or _DEFAULT_DPI
            closest = min(resolutions, key=lambda v: abs(v - target_dpi))
            self._combo_resolution.setCurrentIndex(resolutions.index(closest))
        self._combo_resolution.blockSignals(False)
        self._combo_resolution.setEnabled(True)

        color_modes = caps.get("color_modes") or ["color", "grayscale", "bw"]
        self._radio_color.setEnabled("color" in color_modes)
        self._radio_grayscale.setEnabled("grayscale" in color_modes)
        self._radio_bw.setEnabled("bw" in color_modes)
        # Si le mode actuellement coché n'est plus proposé, retombe sur le
        # premier mode réellement supporté plutôt que de laisser un radio
        # coché mais désactivé (état incohérent pour l'utilisateur).
        checked_mode = None
        for rb in (self._radio_color, self._radio_grayscale, self._radio_bw):
            if rb.isChecked():
                checked_mode = rb.property("_scan_color_mode")
        if checked_mode not in color_modes:
            for rb in (self._radio_color, self._radio_grayscale, self._radio_bw):
                if rb.property("_scan_color_mode") in color_modes:
                    rb.setChecked(True)
                    break

    def _on_caps_failed(self, message_key: str, detail: str, worker=None):
        if worker is not None and worker is not self._caps_worker:
            _log_scan_event("  Resolution dropdown: ignoring stale _CapsWorker failure (a newer query is active)\n")
            return
        _log_scan_event(f"  Resolution dropdown: capabilities query FAILED — {message_key} ({detail})\n")
        # Échec de l'interrogation des capacités seul (device débranché entre
        # l'énumération et la sélection, par ex.) — pas fatal pour le dialogue :
        # on retombe sur la liste de résolutions fixe existante plutôt que de
        # bloquer l'utilisateur. L'échec réel, s'il y en a un, réapparaîtra de
        # toute façon au moment du scan lui-même.
        self._apply_caps_fallback("failed")

    # ── Déclenchement du scan ───────────────────────────────────────────────────

    def _start_scan(self):
        index = self._combo_device.currentIndex()
        if index < 0 or index >= len(self._devices):
            return
        device_id = self._devices[index]["id"]

        dpi = self._combo_resolution.currentData()
        color_mode = "color"
        for rb in (self._radio_color, self._radio_grayscale, self._radio_bw):
            if rb.isChecked():
                color_mode = rb.property("_scan_color_mode")
                break

        settings = {
            "dpi": dpi,
            "color_mode": color_mode,
            "x_pos": None, "y_pos": None, "width": None, "height": None,
        }

        # Mémorise ce choix pour la prochaine ouverture de ScanDialog (voir
        # skill scan) — device, dpi et mode couleur, pas seulement les
        # capacités du device comme le cache existant.
        from modules.qt.config_manager import get_config_manager
        get_config_manager().set_scan_last_settings(device_id, dpi, color_mode)

        dialog_parent = self.parent()
        self._scan_launched = True
        self.close()

        # Si le cache de capacités était MISS à l'ouverture (voir
        # _on_device_changed), _CapsWorker peut encore tourner en arrière-plan
        # à ce stade — il se connecte réellement au device. Sans attendre sa
        # fin ici, un clic rapide sur Scanner lancerait une deuxième connexion
        # COM concurrente au même driver pendant que la première tourne
        # encore. self._caps_worker est None si le cache était HIT (plus de
        # worker du tout dans ce cas, voir _on_device_changed).
        caps_worker = getattr(self, "_caps_worker", None)
        if caps_worker is not None and caps_worker.isRunning():
            _log_scan_event("  Scan start: waiting for background _CapsWorker refresh to finish first...\n")
            caps_worker.wait()

        controller = ScanController(self._canvas, device_id, settings, self._callbacks, dialog_parent)
        # Garde une référence forte tant que le worker COM tourne — sans ça, le
        # garbage collector peut détruire ScanController (et son QThread) alors
        # que Transfer() est encore en cours, d'où le crash natif observé
        # ("QThread: Destroyed while thread is still running").
        self._canvas._scan_controllers = getattr(self._canvas, '_scan_controllers', [])
        self._canvas._scan_controllers.append(controller)

    def _on_close_no_scan(self):
        # Fermeture de la fenêtre de réglages SANS avoir lancé de scan
        # (Annuler, croix, Échap...) — lève le flag posé dans __init__. Si un
        # scan a été lancé (_scan_launched), ne rien faire ici : c'est
        # ScanController qui lèvera le flag à la toute fin du cycle complet
        # (scan + éventuel repli ESCL), potentiellement bien après la
        # fermeture de cette fenêtre.
        if not self._scan_launched:
            _set_scan_active(self.parent(), self.parent(), False)


def show_scan_dialog(parent, canvas, callbacks: dict) -> None:
    """Ouvre la fenêtre de paramètres de scan (point d'entrée public)."""
    dlg = ScanDialog(parent, canvas, callbacks)
    position_dialog_on_parent(dlg, parent)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
