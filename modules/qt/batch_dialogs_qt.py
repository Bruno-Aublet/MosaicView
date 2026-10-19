"""
batch_dialogs_qt.py — Conversion par lot (CBR→CBZ, PDF→CBZ, IMG→CBZ).
Toutes les fenêtres supportent le changement de langue à la volée via language_signal.
"""

import os
import io
import gc
import contextlib
import threading
import zipfile

from PIL import Image

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QProgressBar, QFrame, QSizePolicy, QFileDialog,
    QApplication, QRadioButton, QButtonGroup,
)
from PySide6.QtCore import Qt, QMetaObject, Q_ARG, QObject, Signal
from PySide6.QtGui import QPixmap, QImage, QDesktopServices, QCursor
from PySide6.QtCore import QUrl

from modules.qt.localization import _, _wt
from modules.qt.utils import format_file_size, walk_without_links
from modules.qt.temp_files import unique_log_path, log_text
from modules.qt.state import get_current_theme
from modules.qt.config_manager import get_config_manager
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.dialogs_qt import ErrorDialog, InfoDialog
from modules.qt.archive_type_detector import detect_archive_type
from modules.qt.archive_loader import (_get_7z_exe, _to_short_path, _list_7z_files, _read_7z_file,
                                       _rar_read, _tar_info_read)
from modules.qt.archive_names import tar_entry_name as _tar_entry_name, open_zip, IMAGE_EXTS
from modules.qt.batch_claims import BatchClaims, free_name_candidates, files_in_running_batch

# Import conditionnel rarfile
try:
    import rarfile
except ImportError:
    rarfile = None

# Import conditionnel PyMuPDF
try:
    import pymupdf as fitz
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════════
# Registre des batchs actifs — empêche le GC de détruire prog/signals pendant
# qu'un thread de conversion tourne en arrière-plan.
# ═══════════════════════════════════════════════════════════════════════════════

_active_batches = []   # liste de dicts {'prog': ..., 'signals': ...}


def _register_batch(prog, signals):
    _active_batches.append({'prog': prog, 'signals': signals})


def _unregister_batch(prog):
    global _active_batches
    _active_batches = [b for b in _active_batches if b['prog'] is not prog]


# ═══════════════════════════════════════════════════════════════════════════════
# Arrêt des traitements par lot à la fermeture de l'application
# ═══════════════════════════════════════════════════════════════════════════════
# Les threads de lot sont des démons : sans arrêt coopératif, l'interpréteur les
# tue en pleine écriture à la sortie et laisse un CBZ tronqué (illisible) à côté
# de la source. stop_batches_on_exit() lève _batch_shutdown ; la prochaine
# écriture d'un _interruptible_zip_writer lève alors _BatchInterrupted, qui
# supprime le fichier de sortie incomplet et termine le thread.

_batch_shutdown = threading.Event()
_batch_threads = []
_batch_thread_parents = {}   # thread → panneau qui a lancé le lot


class _BatchInterrupted(BaseException):
    """Interruption d'un lot à la fermeture de l'application. Hérite de
    BaseException pour traverser les `except Exception` des boucles de
    conversion (qui passent sinon à la page ou au fichier suivant)."""


class _BatchCancelled(_BatchInterrupted):
    """Interruption d'un lot par son bouton Annuler (_ProgressDialog). Même
    chemin que _BatchInterrupted (CBZ en cours jeté, source gardée), mais
    _start_batch_thread émet ensuite la fin du lot : le résumé des fichiers
    déjà traités s'affiche."""


# Annulation propre à chaque lot : l'Event du _ProgressDialog du lot, rangé
# par _start_batch_thread dans le thread qui l'exécute.
_batch_local = threading.local()


def _raise_if_batch_stopped():
    """Appelée avant chaque écriture dans le CBZ (_InterruptibleZip) et au
    début de chaque fichier (_claim_file), jamais entre l'écriture d'un CBZ
    et la suppression de sa source."""
    if _batch_shutdown.is_set():
        raise _BatchInterrupted()
    cancel = getattr(_batch_local, "cancel", None)
    if cancel is not None and cancel.is_set():
        raise _BatchCancelled()


class _InterruptibleZip:
    """Enveloppe d'un ZipFile en écriture : chaque écriture vérifie d'abord
    que l'application n'est pas en train de se fermer et que le lot n'a pas
    été annulé."""

    def __init__(self, zf):
        self._zf = zf

    def writestr(self, *args, **kwargs):
        _raise_if_batch_stopped()
        return self._zf.writestr(*args, **kwargs)

    def write(self, *args, **kwargs):
        _raise_if_batch_stopped()
        return self._zf.write(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._zf, name)

    @property
    def comment(self):
        return self._zf.comment

    @comment.setter
    def comment(self, value):
        # Sans ce setter, l'affectation créerait un attribut de l'enveloppe et
        # le commentaire d'archive ne serait jamais écrit dans le ZIP.
        self._zf.comment = value


@contextlib.contextmanager
def _interruptible_zip_writer(path, **zip_kwargs):
    """À utiliser à la place de `zipfile.ZipFile(path, 'w', ...)` dans les
    threads de lot. Le ZIP est écrit dans un fichier temporaire du dossier de
    `path` (même volume), qui ne prend le nom `path` qu'une fois l'écriture et
    la fermeture du ZIP réussies (os.replace). En cas d'échec — exception,
    disque plein à la fermeture, interruption à la fermeture de l'application
    — le temporaire est supprimé et `path` n'est jamais créé : aucun CBZ
    tronqué ne porte le nom d'un CBZ valide. Un crash du processus peut au
    pire laisser un `.~mosaicview_*.tmp`, jamais un faux CBZ ; il est
    supprimé à la prochaine écriture dans ce dossier (make_temp_path)."""
    from modules.qt.utils import (fsync_file, make_temp_path, release_temp_path,
                                  replace_file_retrying)
    tmp_path = make_temp_path(os.path.dirname(os.path.abspath(path)))
    try:
        with zipfile.ZipFile(tmp_path, 'w', **zip_kwargs) as zf:
            yield _InterruptibleZip(zf)
        fsync_file(tmp_path)
        replace_file_retrying(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    finally:
        release_temp_path(tmp_path)


def _start_batch_thread(target, parent, claims, prog, signals):
    """Démarre le thread d'un traitement par lot, enregistré pour
    stop_batches_on_exit() et, avec son panneau `parent`, pour
    running_batch_panel(). Le lot (`claims`, batch_claims.BatchClaims.start)
    est terminé à la fin du thread, quelle qu'elle soit : ses fichiers
    redeviennent disponibles pour un autre lot. Annulé par le bouton de
    `prog`, le lot émet `signals.conversion_done` comme à sa fin normale."""
    def _run():
        _batch_local.cancel = prog.cancel_event
        try:
            target()
        except _BatchCancelled:
            signals.conversion_done.emit()
        except _BatchInterrupted:
            pass  # application en cours de fermeture : fin silencieuse
        finally:
            claims.finish()
    thread = threading.Thread(target=_run, daemon=True)
    _batch_threads[:] = [t for t in _batch_threads if t.is_alive()]
    for t in list(_batch_thread_parents):
        if not t.is_alive():
            del _batch_thread_parents[t]
    _batch_threads.append(thread)
    _batch_thread_parents[thread] = parent
    thread.start()
    return thread


def running_batch_panel():
    """Panneau ayant lancé un lot encore en cours (le premier trouvé s'il y en
    a plusieurs), ou None si aucun lot ne tourne. Sert à MainWindow.closeEvent
    pour demander confirmation avant d'interrompre le lot, fenêtre centrée sur
    ce panneau."""
    for t in _batch_threads:
        if t.is_alive():
            return _batch_thread_parents.get(t)
    return None


def stop_batches_on_exit(timeout_s: float = 5.0):
    """À la fermeture de l'application : interrompt les lots en cours et
    attend (délai global borné) qu'ils aient supprimé leur fichier incomplet."""
    import time
    alive = [t for t in _batch_threads if t.is_alive()]
    _batch_shutdown.set()
    deadline = time.monotonic() + timeout_s
    for t in alive:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        t.join(remaining)


# ═══════════════════════════════════════════════════════════════════════════════
# Signal thread → UI (pour mise à jour thread-safe)
# ═══════════════════════════════════════════════════════════════════════════════

class _ThreadSignals(QObject):
    update_filename   = Signal(str)
    update_progress   = Signal(str)
    update_page_bar   = Signal(float, str)   # valeur 0-100, texte
    update_thumb      = Signal(object)       # QImage ou None
    conversion_done   = Signal()

    # IMG n'a pas de barre de pages
    update_progress_img = Signal(str)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _open_path(path):
    try:
        if os.name == "nt":
            os.startfile(path)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    except Exception:
        pass  # sans conséquence : simple ouverture dans l'application associée, rien n'est modifié


def _unique_flat_name(member_path, written_names):
    """Nom d'entrée du CBZ aplati pour un membre d'archive source.

    Deux membres de même nom dans des dossiers différents (A/p.jpg, B/p.jpg)
    produiraient deux entrées ZIP homonymes dont une seule serait lisible, alors que
    l'archive source est supprimée après conversion. Le doublon reçoit un suffixe
    _2, _3... qui le trie juste après l'original. written_names (noms en minuscules)
    est mis à jour."""
    out_name = os.path.basename(member_path.replace('\\', '/'))
    if out_name.lower() in written_names:
        stem, ext = os.path.splitext(out_name)
        n = 2
        while f"{stem}_{n}{ext}".lower() in written_names:
            n += 1
        out_name = f"{stem}_{n}{ext}"
    written_names.add(out_name.lower())
    return out_name


def _flat_names(member_paths):
    """{chemin du membre: nom à plat} pour un CBZ aplati, qui garde l'ordre de
    lecture : le dossier du membre précède son nom (`Chapitre 1/001.jpg` →
    `Chapitre 1_001.jpg`). Sans lui, les pages de chapitres différents de même
    nom (Ch1/001, Ch2/001…) se suivraient dans l'ordre alphabétique des noms
    et les chapitres seraient entrelacés. Le dossier commun à tous les membres
    (archive dont tout le contenu est rangé dans « MonComic/ ») est retiré :
    il ne distingue aucune page. Caractères de contrôle remplacés par « _ »
    (archive_names.without_control_chars)."""
    from modules.qt.archive_names import without_control_chars
    parts = {p: [without_control_chars(x) for x in p.replace('\\', '/').split('/') if x]
             for p in member_paths}
    dirs = [segments[:-1] for segments in parts.values()]
    common = 0
    while dirs and all(len(d) > common for d in dirs) and len({d[common] for d in dirs}) == 1:
        common += 1
    return {p: "_".join(segments[common:]) for p, segments in parts.items()}


_PAGE_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP",
                 ".bmp": "BMP", ".tiff": "TIFF", ".tif": "TIFF", ".gif": "GIF", ".avif": "AVIF"}


def _normalized_page_bytes(raw, img, ext_lower):
    """Bytes d'une page à écrire dans le CBZ d'un lot. Les modes que les
    lecteurs de BD affichent mal ou pas (CMYK, YCbCr, entier 32 bits I,
    flottant F) sont ramenés à un mode courant par image_ops._to_writable_mode
    (CMYK par son profil ICC, I/F remis à l'échelle 0-255 au lieu d'être
    écrêtés — sinon une page en entiers 32 bits sort toute blanche) et
    réencodés dans le format de la page (JPEG si inconnu), DPI conservé.
    Toute autre page est écrite telle quelle (`raw`), de même qu'un fichier à
    plusieurs images (TIFF multipage) : le réencoder n'en garderait que la
    première, et l'archive source est supprimée après conversion."""
    if img.mode not in ("CMYK", "YCbCr", "I", "F"):
        return raw
    if img.format != "MPO" and getattr(img, "n_frames", 1) > 1:
        return raw
    from modules.qt.image_ops import _to_writable_mode
    from modules.qt.entries import image_dpi, metadata_save_kwargs, page_icc_profile
    out = _to_writable_mode(img)
    out_fmt = _PAGE_FORMATS.get(ext_lower, "JPEG")
    kwargs = {"quality": 100, "optimize": True} if out_fmt == "JPEG" else {}
    # Métadonnées de la page gardées, orientation comprise : les pixels ne
    # sont pas tournés ici. Profil ICC : celui de la conversion (sRGB après
    # un CMJN converti par son profil), s'il correspond au mode écrit.
    kwargs.update(metadata_save_kwargs(out_fmt, raw, keep_orientation=True))
    # Toujours passé, même None : sinon PNG, TIFF et AVIF reprennent le
    # profil CMJN laissé dans img.info par une conversion sans profil.
    if out_fmt in ("JPEG", "PNG", "TIFF", "WEBP", "AVIF"):
        kwargs["icc_profile"] = page_icc_profile(out, None)
    dpi = image_dpi(img)
    if dpi and out_fmt in ("JPEG", "PNG", "TIFF"):
        kwargs["dpi"] = dpi
    buf = io.BytesIO()
    out.save(buf, format=out_fmt, **kwargs)
    return buf.getvalue()


def _copy_pages_to_cbz(cbz, pages, read_page, name_of, on_page=None):
    """Copie les pages d'une archive source (CBR/CB7/CBT) dans le CBZ ouvert `cbz`.

    read_page(page) retourne les bytes d'une page, name_of(page) son chemin dans
    l'archive source. Les pages CMYK/YCbCr/I/F sont ramenées à un mode courant
    (_normalized_page_bytes).
    on_page(courant, total) est appelé après chaque page écrite.

    Une page illisible ou non écrite n'arrête pas la copie des suivantes, mais
    elle est retournée dans la liste des échecs [(chemin, message)] : le CBZ
    produit est alors incomplet et l'archive source ne doit pas être supprimée."""
    failures = []
    written_names = set()
    total = len(pages)
    # Pages triées par chemin complet par l'appelant ; le nom à plat garde le
    # dossier pour que l'ordre alphabétique reste l'ordre de lecture.
    flat = _flat_names([name_of(page) for page in pages])
    from modules.qt.archive_names import name_for_real_format
    for page_num, page in enumerate(pages):
        name = name_of(page)
        out_name = flat[name]
        try:
            raw = read_page(page)
            try:
                tmp = Image.open(io.BytesIO(raw))
                # Extension corrigée si elle annonce un autre format que le
                # vrai (PNG nommé .jpg…), comme à l'ouverture d'un comic
                out_name = name_for_real_format(out_name, tmp.format)
                raw = _normalized_page_bytes(raw, tmp, os.path.splitext(out_name)[1].lower())
                tmp = None
            except Exception:
                pass  # repli : page indécodable écrite telle quelle, sans perte
            cbz.writestr(_unique_flat_name(out_name, written_names), raw)
            raw = None
        except Exception as e:
            failures.append((name, str(e) or type(e).__name__))
            continue
        if (page_num + 1) % 20 == 0:
            gc.collect()
        if on_page is not None:
            on_page(page_num + 1, total)
    return failures


class _IncompleteCbz(Exception):
    """Levée dans le bloc d'un _interruptible_zip_writer quand des pages n'ont
    pas pu être écrites : le temporaire est alors supprimé sans jamais prendre
    le nom du CBZ. Renommé puis supprimé, ce CBZ incomplet remplacerait d'abord
    un fichier de même nom créé entre-temps par un autre traitement, puis le
    supprimerait avec lui."""

    def __init__(self, failures=()):
        super().__init__()
        self.failures = list(failures)


def _log_incomplete_cbz(basename, failures, conversion_errors):
    """Conversion incomplète (pages en échec, voir _IncompleteCbz) : l'archive
    source est gardée et l'erreur consignée dans le journal du lot."""
    first_name, first_err = failures[0]
    conversion_errors.append(
        f"{basename}: " + _("messages.errors.batch_incomplete_kept",
                            count=len(failures), detail=f"{first_name}: {first_err}"))


def _start_batch_claims(parent, files):
    """Démarre le lot sur `files` (batch_claims.BatchClaims.start) : None,
    avec un message, si un autre traitement par lot encore en cours (même
    dossier lancé depuis l'autre panneau, import des métadonnées…) traite
    l'un de ces fichiers. Les deux se disputeraient les mêmes fichiers."""
    claims = BatchClaims.start(files)
    if claims is None:
        _show_batch_running(parent)
    return claims


def _refuse_if_batch_running(parent, files):
    """Avant d'afficher la confirmation d'un lot : True, avec un message, si
    l'un de ces fichiers est déjà traité par un lot en cours. Le contrôle est
    refait au démarrage (_start_batch_claims), la confirmation étant non modale."""
    if files_in_running_batch(files):
        _show_batch_running(parent)
        return True
    return False


def _show_batch_running(parent):
    from modules.qt.dialogs_qt import MsgDialog
    MsgDialog(parent, "dialogs.batch_running.title",
              "dialogs.batch_running.message").show_nonmodal()


def _claim_file(claims, path, conversion_errors):
    """Réserve `path` pour ce lot (batch_claims). Réservé par un autre lot
    encore en cours (même dossier lancé deux fois, import des métadonnées…) :
    laissé de côté et consigné dans le journal, retourne False. Appelée au
    début de chaque fichier : un lot annulé s'arrête là (_BatchCancelled)."""
    _raise_if_batch_stopped()
    if claims.claim(path):
        return True
    conversion_errors.append(
        f"{os.path.basename(path)}: " + _("messages.errors.batch_file_in_other_batch"))
    return False


def _cbz_verified(cbz_path, basename, expected_count, conversion_errors):
    """Relit le CBZ produit avant que sa source soit supprimée
    (utils.verify_written_cbz). S'il ne se relit pas correctement, il est
    supprimé, l'erreur est consignée dans le journal du lot et False est
    retourné : la source, seule copie intacte, doit être gardée."""
    from modules.qt.utils import WrittenFileCheckError, verify_written_cbz
    try:
        verify_written_cbz(cbz_path, expected_count)
        return True
    except WrittenFileCheckError as e:
        try:
            os.remove(cbz_path)
        except OSError:
            pass
        conversion_errors.append(f"{basename}: {e}")
        return False


def _pil_to_qimage(img, w, h, callbacks):
    """Vignette QImage d'une PIL Image, créée par le thread du lot. Un QPixmap
    ne se crée que sur le thread de l'interface : la conversion est faite par
    _ProgressDialog.set_thumbnail. copy() détache l'image du tampon `data`,
    libéré à la sortie de la fonction."""
    try:
        thumb = callbacks["create_centered_thumbnail"](img, w, h)
        thumb = thumb.convert("RGBA")
        data  = thumb.tobytes("raw", "RGBA")
        return QImage(data, thumb.width, thumb.height, QImage.Format_RGBA8888).copy()
    except Exception:
        return None


def _add_dir_links(layout, data):
    """Ajoute des liens cliquables vers les dossiers dans le layout."""
    from modules.qt.utils import setup_path_label_context_menu
    dirs = data.get("directories") or [data.get("directory", "")]
    for d in dirs:
        display = d if len(d) <= 60 else "..." + d[-57:]
        lbl = QLabel(f'<a href="file:///{d}">{display}</a>')
        lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        lbl.setCursor(QCursor(Qt.PointingHandCursor))
        lbl.setStyleSheet("color: #4A9EFF;")
        lbl.setWordWrap(True)
        lbl.linkActivated.connect(lambda _, p=d: _open_path(p))
        lbl.setAlignment(Qt.AlignCenter)
        setup_path_label_context_menu(lbl, lambda p=d: p, lambda p=d: _open_path(p))
        layout.addWidget(lbl)


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


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de confirmation (commun aux 3 flux)
# ═══════════════════════════════════════════════════════════════════════════════

class _ConfirmDialog(QDialog):
    """
    Fenêtre de confirmation avant le lancement d'une conversion batch.
    Affiche : message (count/directory), taille totale, checkbox suppression permanente,
    boutons Démarrer / Annuler.
    """

    def __init__(self, parent, title_key, msg_key, count, directory,
                 total_size, checkbox_key, tooltip_key, start_key, callbacks):
        super().__init__(parent)
        self._title_key    = title_key
        self._msg_key      = msg_key
        self._count        = count
        self._directory    = directory
        self._total_size   = total_size
        self._checkbox_key = checkbox_key
        self._start_key    = start_key
        self._callbacks    = callbacks
        self.confirmed     = False

        self.setModal(False)
        self.setFixedWidth(500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        self._msg_lbl  = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg_lbl)

        self._size_lbl = QLabel()
        self._size_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._size_lbl)

        layout.addSpacing(6)

        self._chk = QCheckBox()
        layout.addWidget(self._chk, alignment=Qt.AlignCenter)

        layout.addSpacing(6)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._start_btn  = QPushButton()
        self._start_btn.setFixedWidth(110)
        self._start_btn.clicked.connect(self._on_start)
        self._start_btn.setDefault(True)
        btn_row.addWidget(self._start_btn)
        self._cancel_btn = QPushButton()
        self._cancel_btn.setFixedWidth(110)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.rejected.connect(self._on_cancel)
        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._start_btn.setFocus()
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
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        display_dir = self._directory if len(self._directory) <= 60 else "..." + self._directory[-57:]
        self.setWindowTitle(_wt(self._title_key))
        self._msg_lbl.setText(_(self._msg_key).format(count=self._count, directory=display_dir))
        self._msg_lbl.setFont(font)
        self._size_lbl.setText(f"({format_file_size(self._total_size)})")
        self._size_lbl.setFont(font)
        self._chk.setText(_(self._checkbox_key))
        self._chk.setFont(font)
        self._start_btn.setText(_(self._start_key))
        self._start_btn.setFont(font)
        self._start_btn.setStyleSheet(btn_style)
        self._cancel_btn.setText(_("buttons.cancel"))
        self._cancel_btn.setFont(font)
        self._cancel_btn.setStyleSheet(btn_style)

    @property
    def permanent_delete(self):
        return self._chk.isChecked()

    def _on_start(self):
        self.confirmed = True
        self.accept()

    def _on_cancel(self):
        self.confirmed = False
        self.accept()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de progression (commun aux 3 flux)
# ═══════════════════════════════════════════════════════════════════════════════

class _ProgressDialog(QDialog):
    """
    Fenêtre de progression pendant la conversion.
    Affiche vignette, nom du fichier, barre de pages (optionnelle), progression globale.
    """

    def __init__(self, parent, title_key, has_page_bar=True):
        super().__init__(parent)
        self._title_key   = title_key
        self._has_page_bar = has_page_bar
        self._running     = True
        # Lu par le thread du lot (_raise_if_batch_stopped) : le lot s'arrête
        # à sa prochaine écriture ou au fichier suivant.
        self.cancel_event = threading.Event()

        self.setModal(False)
        self.setFixedWidth(450)
        self.setFixedHeight(440 if has_page_bar else 390)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(8)

        # Vignette
        self._thumb_lbl = QLabel()
        self._thumb_lbl.setFixedSize(150, 210)
        self._thumb_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._thumb_lbl, alignment=Qt.AlignCenter)

        # Nom du fichier
        self._filename_lbl = QLabel("")
        self._filename_lbl.setWordWrap(True)
        self._filename_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._filename_lbl)

        # Barre progression pages (CBR + PDF seulement)
        if has_page_bar:
            self._page_bar = QProgressBar()
            self._page_bar.setRange(0, 100)
            self._page_bar.setValue(0)
            self._page_bar.setFixedWidth(400)
            layout.addWidget(self._page_bar, alignment=Qt.AlignCenter)

            self._page_lbl = QLabel("")
            self._page_lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(self._page_lbl)

        # Progression globale
        self._progress_lbl = QLabel("")
        self._progress_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._progress_lbl)

        self._btn_cancel = QPushButton()
        self._btn_cancel.clicked.connect(self.request_cancel)
        layout.addWidget(self._btn_cancel, alignment=Qt.AlignCenter)

        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
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
        self._filename_lbl.setFont(font)
        self._progress_lbl.setFont(font)
        self._btn_cancel.setText(_("buttons.cancel"))
        self._btn_cancel.setFont(font)
        self._btn_cancel.setStyleSheet(
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 16px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }} "
            f"QPushButton:disabled {{ color: {theme['separator']}; }}")
        self.setWindowTitle(_wt(self._title_key))

    def request_cancel(self):
        """Annuler, ou fermeture de la fenêtre pendant le lot : le fichier en
        cours est abandonné (CBZ jeté, source gardée), puis le résumé des
        fichiers déjà traités s'affiche (_start_batch_thread)."""
        if self._running and not self.cancel_event.is_set():
            self.cancel_event.set()
            self._btn_cancel.setEnabled(False)

    def set_filename(self, name):
        self._filename_lbl.setText(name)

    def set_progress(self, text):
        self._progress_lbl.setText(text)

    def set_page_progress(self, value, text):
        if self._has_page_bar:
            self._page_bar.setValue(int(value))
            self._page_lbl.setText(text)

    def set_thumbnail(self, image):
        """`image` : QImage créée par le thread du lot (_pil_to_qimage), ou None."""
        if image is not None and not image.isNull():
            pixmap = QPixmap.fromImage(image)
            self._thumb_lbl.setPixmap(pixmap.scaled(150, 210, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self._thumb_lbl.clear()

    def mark_done(self):
        self._running = False

    def closeEvent(self, event):
        if self._running:
            # La fenêtre reste ouverte jusqu'à la fin réelle du lot.
            self.request_cancel()
            event.ignore()
        else:
            event.accept()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de résumé CBR
# ═══════════════════════════════════════════════════════════════════════════════

class _CbrSummaryDialog(QDialog):
    def __init__(self, parent, data):
        super().__init__(parent)
        self._data = data
        self.setModal(False)
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        top = QVBoxLayout()
        top.setSpacing(0)
        top.setContentsMargins(0, 0, 0, 0)
        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        top.addWidget(self._msg_lbl)
        _add_dir_links(top, data)
        layout.addLayout(top)

        self._renamed_cbz_lbl = None
        self._renamed_cb7_lbl = None
        self._renamed_cbt_lbl = None
        for key in ("renamed_cbz", "renamed_cb7", "renamed_cbt"):
            if data.get(key, 0) > 0:
                lbl = QLabel()
                lbl.setWordWrap(True)
                lbl.setAlignment(Qt.AlignCenter)
                layout.addWidget(lbl)
                setattr(self, f"_{key}_lbl", lbl)

        # Lien log (erreurs ou renommages)
        self._error_lbl = None
        if data.get("has_errors") or data.get("log_path"):
            log_path = data.get("log_path")
            if log_path:
                self._error_lbl = QLabel()
                self._error_lbl.setWordWrap(True)
                self._error_lbl.setAlignment(Qt.AlignCenter)
                self._error_lbl.setStyleSheet("color: #4A9EFF;")
                self._error_lbl.setCursor(QCursor(Qt.PointingHandCursor))
                self._error_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
                self._error_lbl.linkActivated.connect(lambda _, p=log_path: _open_path(p))
                from modules.qt.utils import setup_path_label_context_menu
                setup_path_label_context_menu(self._error_lbl, lambda p=log_path: p, lambda p=log_path: _open_path(p))
                layout.addWidget(self._error_lbl)
            else:
                self._error_lbl = QLabel()
                self._error_lbl.setWordWrap(True)
                self._error_lbl.setAlignment(Qt.AlignCenter)
                self._error_lbl.setStyleSheet("color: #4A9EFF;")
                layout.addWidget(self._error_lbl)

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
        _connect_lang(self, lambda _: self._retranslate())
        self._ok_btn.setFocus()
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
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        data = self._data
        self.setWindowTitle(_wt("dialogs.batch_cbr.complete_title"))
        has_renamed = any(data.get(k, 0) > 0 for k in ("renamed_cbz", "renamed_cb7", "renamed_cbt"))
        if data.get("has_errors") or has_renamed:
            self._msg_lbl.setText(_("dialogs.batch_cbr.complete_message_errors").format(
                count=data["converted_count"], total=data["total"]))
        else:
            self._msg_lbl.setText(_("dialogs.batch_cbr.complete_message").format(
                count=data["converted_count"]))
        self._msg_lbl.setFont(font)

        for key, trad_key in (
            ("renamed_cbz", "dialogs.batch_cbr.renamed_cbz_count"),
            ("renamed_cb7", "dialogs.batch_cbr.renamed_cb7_count"),
            ("renamed_cbt", "dialogs.batch_cbr.renamed_cbt_count"),
        ):
            lbl = getattr(self, f"_{key}_lbl")
            if lbl is not None:
                lbl.setText(_(trad_key).format(count=data[key]))
                lbl.setFont(font)

        if self._error_lbl is not None:
            log_path = data.get("log_path")
            see_log = _("dialogs.see_log")
            if data.get("has_errors"):
                error_text = _("dialogs.batch_cbr.errors_count").format(count=data["errors_count"])
                if log_path:
                    self._error_lbl.setText(f'<a href="file:///{log_path}">{error_text} — {see_log}</a>')
                else:
                    self._error_lbl.setText(error_text)
            else:
                # Renommages uniquement — juste le lien log
                self._error_lbl.setText(f'<a href="file:///{log_path}">{see_log}</a>')
            self._error_lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de résumé PDF
# ═══════════════════════════════════════════════════════════════════════════════

class _PdfSummaryDialog(QDialog):
    def __init__(self, parent, data, callbacks):
        super().__init__(parent)
        self._data      = data
        self._callbacks = callbacks
        self.setModal(False)
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        top = QVBoxLayout()
        top.setSpacing(0)
        top.setContentsMargins(0, 0, 0, 0)
        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        top.addWidget(self._msg_lbl)
        _add_dir_links(top, data)
        layout.addLayout(top)

        self._error_lbl = None
        if data.get("has_errors"):
            log_path = data.get("log_path")
            self._error_lbl = QLabel()
            self._error_lbl.setWordWrap(True)
            self._error_lbl.setAlignment(Qt.AlignCenter)
            self._error_lbl.setStyleSheet("color: #4A9EFF;")
            if log_path:
                self._error_lbl.setCursor(QCursor(Qt.PointingHandCursor))
                self._error_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
                self._error_lbl.linkActivated.connect(lambda _, p=log_path: _open_path(p))
                from modules.qt.utils import setup_path_label_context_menu
                setup_path_label_context_menu(self._error_lbl, lambda p=log_path: p, lambda p=log_path: _open_path(p))
            layout.addWidget(self._error_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok_btn = QPushButton()
        self._ok_btn.setFixedWidth(100)
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(self._ok_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._ok_btn.setFocus()
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
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        data = self._data
        self.setWindowTitle(_wt("dialogs.batch_pdf.complete_title"))
        if data.get("has_errors"):
            self._msg_lbl.setText(_("dialogs.batch_pdf.complete_message_errors").format(
                count=data["converted_count"], total=data["total"]))
        else:
            self._msg_lbl.setText(_("dialogs.batch_pdf.complete_message").format(
                count=data["converted_count"]))
        self._msg_lbl.setFont(font)

        if self._error_lbl is not None:
            log_path = data.get("log_path")
            error_text = _("dialogs.batch_pdf.errors_count").format(count=data["errors_count"])
            if log_path:
                see_log = _("dialogs.see_log")
                self._error_lbl.setText(f'<a href="file:///{log_path}">{error_text} — {see_log}</a>')
            else:
                self._error_lbl.setText(error_text)
            self._error_lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)

    def _on_ok(self):
        self.accept()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de résumé IMG
# ═══════════════════════════════════════════════════════════════════════════════

class _ImgSummaryDialog(QDialog):
    def __init__(self, parent, data):
        super().__init__(parent)
        self._data = data
        self.setModal(False)
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        top = QVBoxLayout()
        top.setSpacing(0)
        top.setContentsMargins(0, 0, 0, 0)
        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        top.addWidget(self._msg_lbl)
        _add_dir_links(top, data)
        layout.addLayout(top)

        # Labels par extension (statiques — on recrée si langue change)
        self._ext_frame = QVBoxLayout()
        layout.addLayout(self._ext_frame)
        self._ext_labels = []

        self._error_lbl = None
        if data.get("has_errors"):
            log_path = data.get("log_path")
            self._error_lbl = QLabel()
            self._error_lbl.setWordWrap(True)
            self._error_lbl.setAlignment(Qt.AlignCenter)
            self._error_lbl.setStyleSheet("color: #4A9EFF;")
            if log_path:
                self._error_lbl.setCursor(QCursor(Qt.PointingHandCursor))
                self._error_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
                self._error_lbl.linkActivated.connect(lambda _, p=log_path: _open_path(p))
                from modules.qt.utils import setup_path_label_context_menu
                setup_path_label_context_menu(self._error_lbl, lambda p=log_path: p, lambda p=log_path: _open_path(p))
            layout.addWidget(self._error_lbl)

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
        _connect_lang(self, lambda _: self._retranslate())
        self._ok_btn.setFocus()
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
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        data = self._data
        self.setWindowTitle(_wt("dialogs.batch_img.complete_title"))
        if data.get("has_errors"):
            self._msg_lbl.setText(_("dialogs.batch_img.complete_message_errors").format(
                count=data["converted_count"], total=data["total"]))
        else:
            self._msg_lbl.setText(_("dialogs.batch_img.complete_message").format(
                count=data["converted_count"]))
        self._msg_lbl.setFont(font)

        # Mise à jour labels par extension
        converted_by_ext = data.get("converted_by_ext", {})
        for lbl in self._ext_labels:
            lbl.deleteLater()
        self._ext_labels.clear()
        for ext_key in sorted(converted_by_ext.keys()):
            ext_display = ext_key.lstrip('.').upper()
            line = _("dialogs.batch_img.converted_by_ext").format(ext=ext_display, count=converted_by_ext[ext_key])
            lbl = QLabel(line)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setFont(font)
            self._ext_frame.addWidget(lbl, alignment=Qt.AlignCenter)
            self._ext_labels.append(lbl)

        if self._error_lbl is not None:
            log_path = data.get("log_path")
            error_text = _("dialogs.batch_img.errors_count").format(count=data["errors_count"])
            if log_path:
                see_log = _("dialogs.see_log")
                self._error_lbl.setText(f'<a href="file:///{log_path}">{error_text} — {see_log}</a>')
            else:
                self._error_lbl.setText(error_text)
            self._error_lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)


# ═══════════════════════════════════════════════════════════════════════════════
# Fonctions publiques — CBR→CBZ
# ═══════════════════════════════════════════════════════════════════════════════

def batch_convert_cbr_to_cbz(parent, callbacks, directory=None):
    """Lance la conversion batch CBR→CBZ. Équivalent de batch_dialogs.batch_convert_cbr_to_cbz."""
    if rarfile is None:
        ErrorDialog(parent, lambda: _wt("dialogs.batch_cbr.no_cbr_title"),
                    lambda: _("dialogs.batch_cbr.rarfile_unavailable")).show()
        return

    if directory is None:
        cfg = get_config_manager()
        directory = QFileDialog.getExistingDirectory(
            parent, _wt("dialogs.batch_cbr.select_directory_title"),
            cfg.get('last_open_dir', ""))
        if directory:
            cfg.set('last_open_dir', directory)
    if not directory:
        return

    cbr_files = []
    for dirpath, _subdirs, filenames in walk_without_links(directory):
        for fn in filenames:
            if fn.lower().endswith('.cbr'):
                cbr_files.append(os.path.join(dirpath, fn))
    cbr_files.sort(key=lambda f: callbacks['natural_sort_key'](os.path.basename(f).lower()))

    if not cbr_files:
        InfoDialog(parent, lambda: _wt("dialogs.batch_cbr.no_cbr_title"),
                   lambda d=directory: _("dialogs.batch_cbr.no_cbr_message").format(directory=d)).show()
        return

    batch_convert_cbr_to_cbz_confirm(parent, cbr_files, directory, callbacks)


def batch_convert_cbr_to_cbz_confirm(parent, cbr_files, directory, callbacks, directories=None):
    """Fenêtre de confirmation + progression + résumé CBR→CBZ."""
    cbr_files  = [os.path.normpath(f) for f in cbr_files]
    if _refuse_if_batch_running(parent, cbr_files):
        return
    directory  = os.path.normpath(directory) if directory else directory
    if directories:
        directories = [os.path.normpath(d) for d in directories]
    total_size = sum(os.path.getsize(f) for f in cbr_files)

    dlg = _ConfirmDialog(
        parent,
        title_key    = "dialogs.batch_cbr.confirm_title",
        msg_key      = "dialogs.batch_cbr.confirm_message",
        count        = len(cbr_files),
        directory    = directory,
        total_size   = total_size,
        checkbox_key = "dialogs.batch_cbr.checkbox_permanent_delete",
        tooltip_key  = "tooltip.batch_cbr_permanent_delete",
        start_key    = "dialogs.batch_cbr.start_button",
        callbacks    = callbacks,
    )

    def _on_confirm_done():
        if not dlg.confirmed:
            return
        is_permanent = dlg.permanent_delete
        _run_cbr_conversion(parent, cbr_files, directory, directories, callbacks, is_permanent)

    dlg.finished.connect(lambda _: _on_confirm_done())
    dlg.show()


def _run_cbr_conversion(parent, cbr_files, directory, directories, callbacks, is_permanent):
    claims = _start_batch_claims(parent, cbr_files)
    if claims is None:
        return
    prog = _ProgressDialog(parent, "dialogs.batch_cbr.converting_title", has_page_bar=True)
    prog.show()
    QApplication.processEvents()

    signals          = _ThreadSignals()
    _register_batch(prog, signals)
    conversion_errors = []
    renamed_entries   = []
    converted_count  = [0]
    renamed_cbz      = [0]
    renamed_cb7      = [0]
    renamed_cbt      = [0]

    signals.update_filename.connect(prog.set_filename)
    signals.update_progress.connect(prog.set_progress)
    signals.update_page_bar.connect(lambda v, t: prog.set_page_progress(v, t))
    signals.update_thumb.connect(prog.set_thumbnail)

    def do_conversion():
        total = len(cbr_files)
        for idx, cbr_path in enumerate(cbr_files):
            basename = os.path.basename(cbr_path)
            signals.update_filename.emit(basename)
            signals.update_progress.emit(
                _("dialogs.batch_cbr.converting_progress").format(current=idx + 1, total=total))
            signals.update_page_bar.emit(0.0, "")

            # Fichier et noms à créer réservés pour ce fichier seulement
            claims.release_all()
            if not _claim_file(claims, cbr_path, conversion_errors):
                continue

            # Détection du format réel par magic bytes
            real_type = detect_archive_type(cbr_path)

            # Fichier mal nommé : ZIP, 7z ou TAR → simple renommage
            if real_type in ("CBZ", "CB7", "CBT"):
                ext_map     = {"CBZ": ".cbz", "CB7": ".cb7", "CBT": ".cbt"}
                label_map   = {"CBZ": "ZIP → CBZ", "CB7": "7z → CB7", "CBT": "TAR → CBT"}
                counter_map = {"CBZ": renamed_cbz, "CB7": renamed_cb7, "CBT": renamed_cbt}
                new_ext = ext_map[real_type]
                base_path, _ext = os.path.splitext(cbr_path)
                new_path = claims.reserve(free_name_candidates(base_path, new_ext))
                try:
                    os.rename(cbr_path, new_path)
                    signals.update_page_bar.emit(100.0, label_map[real_type])
                    counter_map[real_type][0] += 1
                    renamed_entries.append(f"{basename} → {os.path.basename(new_path)}")
                except Exception as e:
                    conversion_errors.append(f"{basename}: {e}")
                continue

            # Format inconnu
            if real_type is None:
                conversion_errors.append(f"{basename}: format inconnu")
                continue

            # Vignette (seulement si vrai RAR)
            try:
                with rarfile.RarFile(cbr_path, 'r') as arc:
                    names = sorted(
                        [f for f in arc.namelist() if not f.endswith('/') and f.lower().endswith(IMAGE_EXTS)],
                        key=lambda f: callbacks['natural_sort_key'](f.lower())
                    )
                    if names:
                        data = arc.read(names[0])
                        img  = Image.open(io.BytesIO(data))
                        px   = _pil_to_qimage(img, 150, 210, callbacks)
                        signals.update_thumb.emit(px)
                        img = None
            except Exception:
                signals.update_thumb.emit(None)

            try:
                with rarfile.RarFile(cbr_path, 'r') as archive:
                    all_files   = sorted(
                        [f for f in archive.namelist() if not f.endswith('/')],
                        # Chemin complet (dossier puis nom) : trier sur le seul
                        # nom de fichier entrelacerait les chapitres de
                        # sous-dossiers différents (Ch1/001, Ch2/001, Ch1/002…).
                        key=lambda f: callbacks['natural_sort_key'](f.lower())
                    )
                    total_pages = len(all_files)
                    if total_pages == 0:
                        conversion_errors.append(f"{basename}: archive vide")
                        continue

                    base_path, _ext = os.path.splitext(cbr_path)
                    cbz_path = claims.reserve(free_name_candidates(base_path, ".cbz"))

                    from modules.qt.utils import zip_compression_kwargs
                    from modules.qt.config_manager import get_config_manager as _gcm
                    def _on_page(current, total):
                        signals.update_page_bar.emit(
                            current / total * 100,
                            _("dialogs.batch_cbr.page_progress").format(
                                current=current, total=total))

                    with _interruptible_zip_writer(cbz_path, **zip_compression_kwargs(_gcm().get_zip_compression_level())) as cbz:
                        failed_pages = _copy_pages_to_cbz(
                            cbz, all_files, lambda f: _rar_read(archive, f), lambda f: f, _on_page)
                        if failed_pages:
                            raise _IncompleteCbz(failed_pages)

                gc.collect()
                if not _cbz_verified(cbz_path, basename, total_pages, conversion_errors):
                    continue
                try:
                    if is_permanent:
                        os.remove(cbr_path)
                    else:
                        callbacks['safe_delete_file'](cbr_path)
                except Exception as del_err:
                    conversion_errors.append(f"{basename} (suppression): {del_err}")
                converted_count[0] += 1

            except _IncompleteCbz as inc:
                _log_incomplete_cbz(basename, inc.failures, conversion_errors)
            except Exception as e:
                conversion_errors.append(f"{basename}: {e}")

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        if conversion_errors or renamed_entries:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "cbrtocbz", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write("MosaicView - CBR to CBZ Batch Conversion Log\n")
                    lf.write(f"Date: {now.strftime('%Y-%m-%d %H:%M')}\n")
                    lf.write(f"Directory: {directory}\n")
                    lf.write(f"Total files: {len(cbr_files)}\n")
                    lf.write(f"Converted: {converted_count[0]}\n")
                    lf.write(f"Renamed (ZIP→CBZ): {renamed_cbz[0]}\n")
                    lf.write(f"Renamed (7z→CB7): {renamed_cb7[0]}\n")
                    lf.write(f"Renamed (TAR→CBT): {renamed_cbt[0]}\n")
                    lf.write(f"Errors: {len(conversion_errors)}\n")
                    if renamed_entries:
                        lf.write(f"\n{'='*60}\n")
                        lf.write("Renamed files:\n")
                        lf.write(f"{'='*60}\n\n")
                        for entry in renamed_entries:
                            lf.write(f"  - {log_text(entry)}\n")
                    if conversion_errors:
                        lf.write(f"\n{'='*60}\n")
                        lf.write("Error details:\n")
                        lf.write(f"{'='*60}\n\n")
                        for err in conversion_errors:
                            lf.write(f"  - {log_text(err)}\n")
            except Exception:
                pass
                log_path = None

        summary_data = {
            "converted_count": converted_count[0],
            "total":           len(cbr_files),
            "renamed_cbz":     renamed_cbz[0],
            "renamed_cb7":     renamed_cb7[0],
            "renamed_cbt":     renamed_cbt[0],
            "errors_count":    len(conversion_errors),
            "has_errors":      bool(conversion_errors),
            "directory":       directory,
            "directories":     directories,
            "log_path":        log_path,
        }
        show_batch_cbr_summary(parent, summary_data, callbacks)

    signals.conversion_done.connect(on_done)
    _start_batch_thread(do_conversion, parent, claims, prog, signals)


def show_batch_cbr_summary(parent, data, callbacks):
    dlg = _CbrSummaryDialog(parent, data)
    dlg.show()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de résumé CB7
# ═══════════════════════════════════════════════════════════════════════════════

class _Cb7SummaryDialog(QDialog):
    def __init__(self, parent, data):
        super().__init__(parent)
        self._data = data
        self.setModal(False)
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        top = QVBoxLayout()
        top.setSpacing(0)
        top.setContentsMargins(0, 0, 0, 0)
        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        top.addWidget(self._msg_lbl)
        _add_dir_links(top, data)
        layout.addLayout(top)

        self._renamed_cbz_lbl = None
        self._renamed_cbr_lbl = None
        self._renamed_cbt_lbl = None
        for key in ("renamed_cbz", "renamed_cbr", "renamed_cbt"):
            if data.get(key, 0) > 0:
                lbl = QLabel()
                lbl.setWordWrap(True)
                lbl.setAlignment(Qt.AlignCenter)
                layout.addWidget(lbl)
                setattr(self, f"_{key}_lbl", lbl)

        # Lien log (erreurs ou renommages)
        self._error_lbl = None
        log_path = data.get("log_path")
        if data.get("has_errors") or log_path:
            self._error_lbl = QLabel()
            self._error_lbl.setWordWrap(True)
            self._error_lbl.setAlignment(Qt.AlignCenter)
            self._error_lbl.setStyleSheet("color: #4A9EFF;")
            self._error_lbl.setCursor(QCursor(Qt.PointingHandCursor))
            self._error_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
            if log_path:
                self._error_lbl.linkActivated.connect(lambda _, p=log_path: _open_path(p))
                from modules.qt.utils import setup_path_label_context_menu
                setup_path_label_context_menu(self._error_lbl, lambda p=log_path: p, lambda p=log_path: _open_path(p))
            layout.addWidget(self._error_lbl)

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
        _connect_lang(self, lambda _: self._retranslate())
        self._ok_btn.setFocus()
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
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        data = self._data
        self.setWindowTitle(_wt("dialogs.batch_cb7.complete_title"))
        has_renamed = any(data.get(k, 0) > 0 for k in ("renamed_cbz", "renamed_cbr", "renamed_cbt"))
        if data.get("has_errors") or has_renamed:
            self._msg_lbl.setText(_("dialogs.batch_cb7.complete_message_errors").format(
                count=data["converted_count"], total=data["total"]))
        else:
            self._msg_lbl.setText(_("dialogs.batch_cb7.complete_message").format(
                count=data["converted_count"]))
        self._msg_lbl.setFont(font)

        for key, trad_key in (
            ("renamed_cbz", "dialogs.batch_cb7.renamed_cbz_count"),
            ("renamed_cbr", "dialogs.batch_cb7.renamed_cbr_count"),
            ("renamed_cbt", "dialogs.batch_cb7.renamed_cbt_count"),
        ):
            lbl = getattr(self, f"_{key}_lbl")
            if lbl is not None:
                lbl.setText(_(trad_key).format(count=data[key]))
                lbl.setFont(font)

        if self._error_lbl is not None:
            log_path = data.get("log_path")
            see_log = _("dialogs.see_log")
            if data.get("has_errors"):
                error_text = _("dialogs.batch_cb7.errors_count").format(count=data["errors_count"])
                if log_path:
                    self._error_lbl.setText(f'<a href="file:///{log_path}">{error_text} — {see_log}</a>')
                else:
                    self._error_lbl.setText(error_text)
            else:
                self._error_lbl.setText(f'<a href="file:///{log_path}">{see_log}</a>')
            self._error_lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)


# ═══════════════════════════════════════════════════════════════════════════════
# Fonctions publiques — CB7→CBZ
# ═══════════════════════════════════════════════════════════════════════════════

def batch_convert_cb7_to_cbz(parent, callbacks, directory=None):
    """Lance la conversion batch CB7→CBZ."""
    if directory is None:
        cfg = get_config_manager()
        directory = QFileDialog.getExistingDirectory(
            parent, _wt("dialogs.batch_cb7.select_directory_title"),
            cfg.get('last_open_dir', ""))
        if directory:
            cfg.set('last_open_dir', directory)

    if not directory:
        return

    cb7_files = []
    for root, _subdirs, files in walk_without_links(directory):
        for fname in files:
            if fname.lower().endswith('.cb7'):
                cb7_files.append(os.path.join(root, fname))
    cb7_files.sort(key=lambda f: callbacks['natural_sort_key'](os.path.basename(f).lower()))

    if not cb7_files:
        InfoDialog(parent, lambda: _wt("dialogs.batch_cb7.no_cb7_title"),
                   lambda d=directory: _("dialogs.batch_cb7.no_cb7_message").format(directory=d)).show()
        return

    batch_convert_cb7_to_cbz_confirm(parent, cb7_files, directory, callbacks)


def batch_convert_cb7_to_cbz_confirm(parent, cb7_files, directory, callbacks, directories=None):
    """Fenêtre de confirmation + progression + résumé CB7→CBZ."""
    cb7_files = [os.path.normpath(f) for f in cb7_files]
    if _refuse_if_batch_running(parent, cb7_files):
        return
    directory = os.path.normpath(directory) if directory else directory
    if directories:
        directories = [os.path.normpath(d) for d in directories]

    total_size = sum(os.path.getsize(f) for f in cb7_files if os.path.isfile(f))

    dlg = _ConfirmDialog(
        parent,
        title_key    = "dialogs.batch_cb7.confirm_title",
        msg_key      = "dialogs.batch_cb7.confirm_message",
        count        = len(cb7_files),
        directory    = directory,
        total_size   = total_size,
        checkbox_key = "dialogs.batch_cb7.checkbox_permanent_delete",
        tooltip_key  = "tooltip.batch_cb7_permanent_delete",
        start_key    = "dialogs.batch_cb7.start_button",
        callbacks    = callbacks,
    )
    def _on_confirm_done_cb7():
        if not dlg.confirmed:
            return
        is_permanent = dlg.permanent_delete
        _run_cb7_conversion(parent, cb7_files, directory, directories, callbacks, is_permanent)

    dlg.finished.connect(lambda _: _on_confirm_done_cb7())
    dlg.show()


def _run_cb7_conversion(parent, cb7_files, directory, directories, callbacks, is_permanent):
    claims = _start_batch_claims(parent, cb7_files)
    if claims is None:
        return
    prog = _ProgressDialog(parent, "dialogs.batch_cb7.converting_title", has_page_bar=True)
    prog.show()
    QApplication.processEvents()

    signals           = _ThreadSignals()
    _register_batch(prog, signals)
    conversion_errors = []
    renamed_entries   = []
    converted_count   = [0]
    renamed_cbz       = [0]
    renamed_cbr       = [0]
    renamed_cbt       = [0]

    signals.update_filename.connect(prog.set_filename)
    signals.update_progress.connect(prog.set_progress)
    signals.update_page_bar.connect(lambda v, t: prog.set_page_progress(v, t))
    signals.update_thumb.connect(prog.set_thumbnail)

    def do_conversion():
        total = len(cb7_files)
        for idx, cb7_path in enumerate(cb7_files):
            basename = os.path.basename(cb7_path)
            signals.update_filename.emit(basename)
            signals.update_progress.emit(
                _("dialogs.batch_cb7.converting_progress").format(current=idx + 1, total=total))
            signals.update_page_bar.emit(0.0, "")

            # Fichier et noms à créer réservés pour ce fichier seulement
            claims.release_all()
            if not _claim_file(claims, cb7_path, conversion_errors):
                continue

            # Détection du format réel par magic bytes
            real_type = detect_archive_type(cb7_path)

            # Fichier mal nommé : ZIP, RAR ou TAR → simple renommage
            if real_type in ("CBZ", "CBR", "CBT"):
                ext_map     = {"CBZ": ".cbz", "CBR": ".cbr", "CBT": ".cbt"}
                label_map   = {"CBZ": "ZIP → CBZ", "CBR": "RAR → CBR", "CBT": "TAR → CBT"}
                counter_map = {"CBZ": renamed_cbz, "CBR": renamed_cbr, "CBT": renamed_cbt}
                new_ext = ext_map[real_type]
                base_path, _ext = os.path.splitext(cb7_path)
                new_path = claims.reserve(free_name_candidates(base_path, new_ext))
                try:
                    os.rename(cb7_path, new_path)
                    signals.update_page_bar.emit(100.0, label_map[real_type])
                    counter_map[real_type][0] += 1
                    renamed_entries.append(f"{basename} → {os.path.basename(new_path)}")
                except Exception as e:
                    conversion_errors.append(f"{basename}: {e}")
                continue

            # Format inconnu
            if real_type is None:
                conversion_errors.append(f"{basename}: format inconnu")
                continue

            # Vignette
            try:
                all_names = _list_7z_files(cb7_path)
                img_names = sorted(
                    [f for f in all_names if f.lower().endswith(IMAGE_EXTS)],
                    key=lambda f: callbacks['natural_sort_key'](f.lower())
                )
                if img_names:
                    data = _read_7z_file(cb7_path, img_names[0])
                    img  = Image.open(io.BytesIO(data))
                    px   = _pil_to_qimage(img, 150, 210, callbacks)
                    signals.update_thumb.emit(px)
                    img = None
            except Exception:
                signals.update_thumb.emit(None)

            try:
                all_files = sorted(
                    _list_7z_files(cb7_path),
                    # Chemin complet (dossier puis nom), voir le lot CBR.
                    key=lambda f: callbacks['natural_sort_key'](f.lower())
                )
                total_pages = len(all_files)
                if total_pages == 0:
                    conversion_errors.append(f"{basename}: archive vide")
                    continue

                base_path, _ext = os.path.splitext(cb7_path)
                cbz_path = claims.reserve(free_name_candidates(base_path, ".cbz"))

                from modules.qt.utils import zip_compression_kwargs
                from modules.qt.config_manager import get_config_manager as _gcm
                def _on_page(current, total):
                    signals.update_page_bar.emit(
                        current / total * 100,
                        _("dialogs.batch_cb7.page_progress").format(
                            current=current, total=total))

                with _interruptible_zip_writer(cbz_path, **zip_compression_kwargs(_gcm().get_zip_compression_level())) as cbz:
                    failed_pages = _copy_pages_to_cbz(
                        cbz, all_files, lambda f: _read_7z_file(cb7_path, f), lambda f: f, _on_page)
                    if failed_pages:
                        raise _IncompleteCbz(failed_pages)

                gc.collect()
                if not _cbz_verified(cbz_path, basename, total_pages, conversion_errors):
                    continue
                try:
                    if is_permanent:
                        os.remove(cb7_path)
                    else:
                        callbacks['safe_delete_file'](cb7_path)
                except Exception as del_err:
                    conversion_errors.append(f"{basename} (suppression): {del_err}")
                converted_count[0] += 1

            except _IncompleteCbz as inc:
                _log_incomplete_cbz(basename, inc.failures, conversion_errors)
            except Exception as e:
                conversion_errors.append(f"{basename}: {e}")

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        if conversion_errors or renamed_entries:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "cb7tocbz", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write("MosaicView - CB7 to CBZ Batch Conversion Log\n")
                    lf.write(f"Date: {now.strftime('%Y-%m-%d %H:%M')}\n")
                    lf.write(f"Directory: {directory}\n")
                    lf.write(f"Total files: {len(cb7_files)}\n")
                    lf.write(f"Converted: {converted_count[0]}\n")
                    lf.write(f"Renamed (ZIP→CBZ): {renamed_cbz[0]}\n")
                    lf.write(f"Renamed (RAR→CBR): {renamed_cbr[0]}\n")
                    lf.write(f"Renamed (TAR→CBT): {renamed_cbt[0]}\n")
                    lf.write(f"Errors: {len(conversion_errors)}\n")
                    if renamed_entries:
                        lf.write(f"\n{'='*60}\n")
                        lf.write("Renamed files:\n")
                        lf.write(f"{'='*60}\n\n")
                        for entry in renamed_entries:
                            lf.write(f"  - {log_text(entry)}\n")
                    if conversion_errors:
                        lf.write(f"\n{'='*60}\n")
                        lf.write("Error details:\n")
                        lf.write(f"{'='*60}\n\n")
                        for err in conversion_errors:
                            lf.write(f"  - {log_text(err)}\n")
            except Exception:
                pass
                log_path = None

        summary_data = {
            "converted_count": converted_count[0],
            "total":           len(cb7_files),
            "renamed_cbz":     renamed_cbz[0],
            "renamed_cbr":     renamed_cbr[0],
            "renamed_cbt":     renamed_cbt[0],
            "errors_count":    len(conversion_errors),
            "has_errors":      bool(conversion_errors),
            "directory":       directory,
            "directories":     directories,
            "log_path":        log_path,
        }
        show_batch_cb7_summary(parent, summary_data, callbacks)

    signals.conversion_done.connect(on_done)
    _start_batch_thread(do_conversion, parent, claims, prog, signals)


def show_batch_cb7_summary(parent, data, callbacks):
    dlg = _Cb7SummaryDialog(parent, data)
    dlg.show()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de résumé CBT
# ═══════════════════════════════════════════════════════════════════════════════

class _CbtSummaryDialog(QDialog):
    def __init__(self, parent, data):
        super().__init__(parent)
        self._data = data
        self.setModal(False)
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        top = QVBoxLayout()
        top.setSpacing(0)
        top.setContentsMargins(0, 0, 0, 0)
        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        top.addWidget(self._msg_lbl)
        _add_dir_links(top, data)
        layout.addLayout(top)

        self._renamed_cbz_lbl = None
        self._renamed_cbr_lbl = None
        self._renamed_cb7_lbl = None
        for key in ("renamed_cbz", "renamed_cbr", "renamed_cb7"):
            if data.get(key, 0) > 0:
                lbl = QLabel()
                lbl.setWordWrap(True)
                lbl.setAlignment(Qt.AlignCenter)
                layout.addWidget(lbl)
                setattr(self, f"_{key}_lbl", lbl)

        # Lien log (erreurs ou renommages)
        self._error_lbl = None
        log_path = data.get("log_path")
        if data.get("has_errors") or log_path:
            self._error_lbl = QLabel()
            self._error_lbl.setWordWrap(True)
            self._error_lbl.setAlignment(Qt.AlignCenter)
            self._error_lbl.setStyleSheet("color: #4A9EFF;")
            self._error_lbl.setCursor(QCursor(Qt.PointingHandCursor))
            self._error_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
            if log_path:
                self._error_lbl.linkActivated.connect(lambda _, p=log_path: _open_path(p))
                from modules.qt.utils import setup_path_label_context_menu
                setup_path_label_context_menu(self._error_lbl, lambda p=log_path: p, lambda p=log_path: _open_path(p))
            layout.addWidget(self._error_lbl)

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
        _connect_lang(self, lambda _: self._retranslate())
        self._ok_btn.setFocus()
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
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        data = self._data
        self.setWindowTitle(_wt("dialogs.batch_cbt.complete_title"))
        has_renamed = any(data.get(k, 0) > 0 for k in ("renamed_cbz", "renamed_cbr", "renamed_cb7"))
        if data.get("has_errors") or has_renamed:
            self._msg_lbl.setText(_("dialogs.batch_cbt.complete_message_errors").format(
                count=data["converted_count"], total=data["total"]))
        else:
            self._msg_lbl.setText(_("dialogs.batch_cbt.complete_message").format(
                count=data["converted_count"]))
        self._msg_lbl.setFont(font)

        for key, trad_key in (
            ("renamed_cbz", "dialogs.batch_cbt.renamed_cbz_count"),
            ("renamed_cbr", "dialogs.batch_cbt.renamed_cbr_count"),
            ("renamed_cb7", "dialogs.batch_cbt.renamed_cb7_count"),
        ):
            lbl = getattr(self, f"_{key}_lbl")
            if lbl is not None:
                lbl.setText(_(trad_key).format(count=data[key]))
                lbl.setFont(font)

        if self._error_lbl is not None:
            log_path = data.get("log_path")
            see_log = _("dialogs.see_log")
            if data.get("has_errors"):
                error_text = _("dialogs.batch_cbt.errors_count").format(count=data["errors_count"])
                if log_path:
                    self._error_lbl.setText(f'<a href="file:///{log_path}">{error_text} — {see_log}</a>')
                else:
                    self._error_lbl.setText(error_text)
            else:
                self._error_lbl.setText(f'<a href="file:///{log_path}">{see_log}</a>')
            self._error_lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)


# ═══════════════════════════════════════════════════════════════════════════════
# Fonctions publiques — CBT→CBZ
# ═══════════════════════════════════════════════════════════════════════════════

def batch_convert_cbt_to_cbz(parent, callbacks, directory=None):
    """Lance la conversion batch CBT→CBZ."""
    if directory is None:
        cfg = get_config_manager()
        directory = QFileDialog.getExistingDirectory(
            parent, _wt("dialogs.batch_cbt.select_directory_title"),
            cfg.get('last_open_dir', ""))
        if directory:
            cfg.set('last_open_dir', directory)

    if not directory:
        return

    cbt_files = []
    for root, _subdirs, files in walk_without_links(directory):
        for fname in files:
            if fname.lower().endswith('.cbt'):
                cbt_files.append(os.path.join(root, fname))
    cbt_files.sort(key=lambda f: callbacks['natural_sort_key'](os.path.basename(f).lower()))

    if not cbt_files:
        InfoDialog(parent, lambda: _wt("dialogs.batch_cbt.no_cbt_title"),
                   lambda d=directory: _("dialogs.batch_cbt.no_cbt_message").format(directory=d)).show()
        return

    batch_convert_cbt_to_cbz_confirm(parent, cbt_files, directory, callbacks)


def batch_convert_cbt_to_cbz_confirm(parent, cbt_files, directory, callbacks, directories=None):
    """Fenêtre de confirmation + progression + résumé CBT→CBZ."""
    cbt_files = [os.path.normpath(f) for f in cbt_files]
    if _refuse_if_batch_running(parent, cbt_files):
        return
    directory = os.path.normpath(directory) if directory else directory
    if directories:
        directories = [os.path.normpath(d) for d in directories]

    total_size = sum(os.path.getsize(f) for f in cbt_files if os.path.isfile(f))

    dlg = _ConfirmDialog(
        parent,
        title_key    = "dialogs.batch_cbt.confirm_title",
        msg_key      = "dialogs.batch_cbt.confirm_message",
        count        = len(cbt_files),
        directory    = directory,
        total_size   = total_size,
        checkbox_key = "dialogs.batch_cbt.checkbox_permanent_delete",
        tooltip_key  = "tooltip.batch_cbt_permanent_delete",
        start_key    = "dialogs.batch_cbt.start_button",
        callbacks    = callbacks,
    )
    def _on_confirm_done_cbt():
        if not dlg.confirmed:
            return
        is_permanent = dlg.permanent_delete
        _run_cbt_conversion(parent, cbt_files, directory, directories, callbacks, is_permanent)

    dlg.finished.connect(lambda _: _on_confirm_done_cbt())
    dlg.show()


def _run_cbt_conversion(parent, cbt_files, directory, directories, callbacks, is_permanent):
    import tarfile as _tarfile
    claims = _start_batch_claims(parent, cbt_files)
    if claims is None:
        return
    prog = _ProgressDialog(parent, "dialogs.batch_cbt.converting_title", has_page_bar=True)
    prog.show()
    QApplication.processEvents()

    signals           = _ThreadSignals()
    _register_batch(prog, signals)
    conversion_errors = []
    renamed_entries   = []
    converted_count   = [0]
    renamed_cbz       = [0]
    renamed_cbr       = [0]
    renamed_cb7       = [0]

    signals.update_filename.connect(prog.set_filename)
    signals.update_progress.connect(prog.set_progress)
    signals.update_page_bar.connect(lambda v, t: prog.set_page_progress(v, t))
    signals.update_thumb.connect(prog.set_thumbnail)

    def do_conversion():
        total = len(cbt_files)
        for idx, cbt_path in enumerate(cbt_files):
            basename = os.path.basename(cbt_path)
            signals.update_filename.emit(basename)
            signals.update_progress.emit(
                _("dialogs.batch_cbt.converting_progress").format(current=idx + 1, total=total))
            signals.update_page_bar.emit(0.0, "")

            # Fichier et noms à créer réservés pour ce fichier seulement
            claims.release_all()
            if not _claim_file(claims, cbt_path, conversion_errors):
                continue

            # Détection du format réel par magic bytes
            real_type = detect_archive_type(cbt_path)

            # Fichier mal nommé : ZIP, RAR ou 7z → simple renommage
            if real_type in ("CBZ", "CBR", "CB7"):
                ext_map     = {"CBZ": ".cbz", "CBR": ".cbr", "CB7": ".cb7"}
                label_map   = {"CBZ": "ZIP → CBZ", "CBR": "RAR → CBR", "CB7": "7z → CB7"}
                counter_map = {"CBZ": renamed_cbz, "CBR": renamed_cbr, "CB7": renamed_cb7}
                new_ext = ext_map[real_type]
                base_path, _ext = os.path.splitext(cbt_path)
                new_path = claims.reserve(free_name_candidates(base_path, new_ext))
                try:
                    os.rename(cbt_path, new_path)
                    signals.update_page_bar.emit(100.0, label_map[real_type])
                    counter_map[real_type][0] += 1
                    renamed_entries.append(f"{basename} → {os.path.basename(new_path)}")
                except Exception as e:
                    conversion_errors.append(f"{basename}: {e}")
                continue

            # Format inconnu
            if real_type is None:
                conversion_errors.append(f"{basename}: format inconnu")
                continue

            # Vignette
            try:
                with _tarfile.open(cbt_path, 'r:*') as arc:
                    img_members = sorted(
                        [m for m in arc.getmembers()
                         if m.isfile() and m.name.lower().endswith(IMAGE_EXTS)],
                        key=lambda m: callbacks['natural_sort_key'](m.name.lower())
                    )
                    if img_members:
                        data = arc.extractfile(img_members[0]).read()
                        img  = Image.open(io.BytesIO(data))
                        px   = _pil_to_qimage(img, 150, 210, callbacks)
                        signals.update_thumb.emit(px)
                        img = None
            except Exception:
                signals.update_thumb.emit(None)

            try:
                with _tarfile.open(cbt_path, 'r:*') as archive:
                    all_members = sorted(
                        [m for m in archive.getmembers() if m.isfile()],
                        # Chemin complet (dossier puis nom), voir le lot CBR.
                        key=lambda m: callbacks['natural_sort_key'](m.name.lower())
                    )
                    total_pages = len(all_members)
                    if total_pages == 0:
                        conversion_errors.append(f"{basename}: archive vide")
                        continue

                    base_path, _ext = os.path.splitext(cbt_path)
                    cbz_path = claims.reserve(free_name_candidates(base_path, ".cbz"))

                    from modules.qt.utils import zip_compression_kwargs
                    from modules.qt.config_manager import get_config_manager as _gcm
                    def _on_page(current, total):
                        signals.update_page_bar.emit(
                            current / total * 100,
                            _("dialogs.batch_cbt.page_progress").format(
                                current=current, total=total))

                    with _interruptible_zip_writer(cbz_path, **zip_compression_kwargs(_gcm().get_zip_compression_level())) as cbz:
                        # Noms normalisés comme à l'ouverture d'un CBT
                        # (_tar_entry_name, sans préfixe "./").
                        failed_pages = _copy_pages_to_cbz(
                            cbz, all_members, lambda m: _tar_info_read(archive, m),
                            lambda m: _tar_entry_name(m.name), _on_page)
                        if failed_pages:
                            raise _IncompleteCbz(failed_pages)

                gc.collect()
                if not _cbz_verified(cbz_path, basename, total_pages, conversion_errors):
                    continue
                try:
                    if is_permanent:
                        os.remove(cbt_path)
                    else:
                        callbacks['safe_delete_file'](cbt_path)
                except Exception as del_err:
                    conversion_errors.append(f"{basename} (suppression): {del_err}")
                converted_count[0] += 1

            except _IncompleteCbz as inc:
                _log_incomplete_cbz(basename, inc.failures, conversion_errors)
            except Exception as e:
                conversion_errors.append(f"{basename}: {e}")

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        if conversion_errors or renamed_entries:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "cbttocbz", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write("MosaicView - CBT to CBZ Batch Conversion Log\n")
                    lf.write(f"Date: {now.strftime('%Y-%m-%d %H:%M')}\n")
                    lf.write(f"Directory: {directory}\n")
                    lf.write(f"Total files: {len(cbt_files)}\n")
                    lf.write(f"Converted: {converted_count[0]}\n")
                    lf.write(f"Renamed (ZIP→CBZ): {renamed_cbz[0]}\n")
                    lf.write(f"Renamed (RAR→CBR): {renamed_cbr[0]}\n")
                    lf.write(f"Renamed (7z→CB7): {renamed_cb7[0]}\n")
                    lf.write(f"Errors: {len(conversion_errors)}\n")
                    if renamed_entries:
                        lf.write(f"\n{'='*60}\n")
                        lf.write("Renamed files:\n")
                        lf.write(f"{'='*60}\n\n")
                        for entry in renamed_entries:
                            lf.write(f"  - {log_text(entry)}\n")
                    if conversion_errors:
                        lf.write(f"\n{'='*60}\n")
                        lf.write("Error details:\n")
                        lf.write(f"{'='*60}\n\n")
                        for err in conversion_errors:
                            lf.write(f"  - {log_text(err)}\n")
            except Exception:
                pass
                log_path = None

        summary_data = {
            "converted_count": converted_count[0],
            "total":           len(cbt_files),
            "renamed_cbz":     renamed_cbz[0],
            "renamed_cbr":     renamed_cbr[0],
            "renamed_cb7":     renamed_cb7[0],
            "errors_count":    len(conversion_errors),
            "has_errors":      bool(conversion_errors),
            "directory":       directory,
            "directories":     directories,
            "log_path":        log_path,
        }
        show_batch_cbt_summary(parent, summary_data, callbacks)

    signals.conversion_done.connect(on_done)
    _start_batch_thread(do_conversion, parent, claims, prog, signals)


def show_batch_cbt_summary(parent, data, callbacks):
    dlg = _CbtSummaryDialog(parent, data)
    dlg.show()


# ═══════════════════════════════════════════════════════════════════════════════
# Fonctions publiques — PDF→CBZ
# ═══════════════════════════════════════════════════════════════════════════════

def batch_convert_pdf_to_cbz(parent, callbacks, directory=None):
    """Lance la conversion batch PDF→CBZ."""
    if not PDF_AVAILABLE:
        ErrorDialog(parent,
                    lambda: _wt("dialogs.batch_pdf.pymupdf_required_title"),
                    lambda: _("dialogs.batch_pdf.pymupdf_required_message")).show()
        return

    if directory is None:
        cfg = get_config_manager()
        directory = QFileDialog.getExistingDirectory(
            parent, _wt("dialogs.batch_pdf.select_directory_title"),
            cfg.get('last_open_dir', ""))
        if directory:
            cfg.set('last_open_dir', directory)
    if not directory:
        return

    pdf_files = []
    for dirpath, _subdirs, filenames in walk_without_links(directory):
        for fn in filenames:
            if fn.lower().endswith('.pdf'):
                pdf_files.append(os.path.join(dirpath, fn))
    pdf_files.sort(key=lambda f: callbacks['natural_sort_key'](os.path.basename(f).lower()))

    if not pdf_files:
        InfoDialog(parent, lambda: _wt("dialogs.batch_pdf.no_pdf_title"),
                   lambda d=directory: _("dialogs.batch_pdf.no_pdf_message").format(directory=d)).show()
        return

    batch_convert_pdf_to_cbz_confirm(parent, pdf_files, directory, callbacks)


def batch_convert_pdf_to_cbz_confirm(parent, pdf_files, directory, callbacks, directories=None):
    """Fenêtre de confirmation + progression + résumé PDF→CBZ."""
    pdf_files  = [os.path.normpath(f) for f in pdf_files]
    if _refuse_if_batch_running(parent, pdf_files):
        return
    directory  = os.path.normpath(directory) if directory else directory
    if directories:
        directories = [os.path.normpath(d) for d in directories]
    from modules.qt import state as _state_module
    state = callbacks.get('state') or _state_module.state

    total_size = sum(os.path.getsize(f) for f in pdf_files)
    dlg = _ConfirmDialog(
        parent,
        title_key    = "dialogs.batch_pdf.confirm_title",
        msg_key      = "dialogs.batch_pdf.confirm_message",
        count        = len(pdf_files),
        directory    = directory,
        total_size   = total_size,
        checkbox_key = "dialogs.batch_pdf.checkbox_permanent_delete",
        tooltip_key  = "tooltip.batch_pdf_permanent_delete",
        start_key    = "dialogs.batch_pdf.start_button",
        callbacks    = callbacks,
    )
    def _on_confirm_done_pdf():
        if not dlg.confirmed:
            return
        is_permanent = dlg.permanent_delete
        _run_pdf_conversion(parent, pdf_files, directory, directories, callbacks, is_permanent, state)

    dlg.finished.connect(lambda _: _on_confirm_done_pdf())
    dlg.show()


def _run_pdf_conversion(parent, pdf_files, directory, directories, callbacks, is_permanent, state):
    claims = _start_batch_claims(parent, pdf_files)
    if claims is None:
        return
    prog = _ProgressDialog(parent, "dialogs.batch_pdf.converting_title", has_page_bar=True)
    prog.show()
    QApplication.processEvents()

    signals           = _ThreadSignals()
    _register_batch(prog, signals)
    conversion_errors = []
    converted_count   = [0]
    owner_protected   = []

    signals.update_filename.connect(prog.set_filename)
    signals.update_progress.connect(prog.set_progress)
    signals.update_page_bar.connect(lambda v, t: prog.set_page_progress(v, t))
    signals.update_thumb.connect(prog.set_thumbnail)

    def do_conversion():
        import modules.qt.pdf_loading_qt as _pdfmod

        # Process dédié à ce batch (pas partagé avec les panneaux ni un autre batch) —
        # créé une fois pour toute la durée du lot, détruit à la fin.
        proc = list(_pdfmod._spawn_pdf_process())  # [process, in_q, out_conn]

        def _send(msg):
            proc[1].put(msg)

        def _recv(timeout=60):
            """Attend un message du process, retourne None si timeout."""
            if proc[2].poll(timeout):
                return proc[2].recv()
            return None

        def _restart_process():
            """Après un timeout ou une écriture interrompue, le process peut
            encore travailler sur le document abandonné : ses messages
            tardifs (batch_ready, pages, done) seraient pris pour ceux du
            PDF suivant. Il est remplacé par un process neuf."""
            _pdfmod._kill_pdf_process(*proc)
            proc[:] = _pdfmod._spawn_pdf_process()

        try:
            _convert_all(_send, _recv, _restart_process)
        finally:
            _pdfmod._kill_pdf_process(*proc)
        signals.conversion_done.emit()

    def _convert_all(_send, _recv, _restart_process):
        total = len(pdf_files)
        for idx, pdf_path in enumerate(pdf_files):
            basename = os.path.basename(pdf_path)
            signals.update_filename.emit(basename)
            signals.update_progress.emit(
                _("dialogs.batch_pdf.converting_progress").format(current=idx + 1, total=total))
            signals.update_page_bar.emit(0.0, "")

            # Fichier et nom du CBZ réservés pour ce fichier seulement
            claims.release_all()
            if not _claim_file(claims, pdf_path, conversion_errors):
                continue

            _send(('batch_open', pdf_path))

            # Attend batch_ready
            batch_ready = None
            while True:
                msg = _recv(timeout=30)
                if msg is None:
                    break
                if msg[0] == '_debug':
                    continue
                if msg[0] in ('batch_ready', 'error'):
                    batch_ready = msg
                    break

            if batch_ready is None or batch_ready[0] == 'error':
                err = batch_ready[1] if batch_ready else _("messages.errors.pdf_failure.no_response")
                conversion_errors.append(f"{basename}: {err}")
                if batch_ready is None:
                    _restart_process()
                continue

            if batch_ready[1] is None:
                # needs_pass
                conversion_errors.append(
                    _("messages.errors.pdf_encrypted_skipped").format(filename=basename))
                continue

            _kind, total_pages, ratios, thumb_bytes, is_owner = batch_ready

            if is_owner:
                owner_protected.append(pdf_path)

            # Vignette
            if thumb_bytes:
                try:
                    img = Image.open(io.BytesIO(thumb_bytes))
                    px  = _pil_to_qimage(img, 150, 210, callbacks)
                    signals.update_thumb.emit(px)
                except Exception:
                    signals.update_thumb.emit(None)
            else:
                signals.update_thumb.emit(None)

            # Noms de fichiers
            ext_out = ".jpg"
            if state.renumber_mode == 1:
                multipliers    = callbacks['compute_auto_multipliers'](ratios)
                filenames_list = callbacks['generate_auto_filenames'](multipliers, ext_out)
            else:
                digits = max(2, len(str(total_pages)))
                filenames_list = [str(i + 1).zfill(digits) + ext_out for i in range(total_pages)]

            # CBZ path
            base_path, _ext = os.path.splitext(pdf_path)
            cbz_path = claims.reserve(free_name_candidates(base_path, ".cbz"))

            # Lance la conversion
            _send(('batch_convert', filenames_list))

            # Reçoit les pages et les écrit dans le CBZ
            from modules.qt.utils import zip_compression_kwargs
            from modules.qt.config_manager import get_config_manager as _gcm
            # Un timeout, une erreur du process ou une page non rendue laisse un
            # CBZ incomplet : il est supprimé et le PDF d'origine conservé.
            pdf_incomplete = False
            pages_written = 0
            page_failures = []
            try:
                with _interruptible_zip_writer(cbz_path, **zip_compression_kwargs(_gcm().get_zip_compression_level())) as cbz:
                    while True:
                        msg = _recv(timeout=120)
                        if msg is None:
                            conversion_errors.append(
                                f"{basename}: " + _("messages.errors.pdf_failure.no_response"))
                            pdf_incomplete = True
                            break
                        if msg[0] == '_debug':
                            continue
                        if msg[0] == 'batch_page':
                            _kind2, filename, img_bytes, pct, cur, tot = msg
                            from modules.qt.pdf_loading_qt import pdf_image_ext
                            filename = os.path.splitext(filename)[0] + pdf_image_ext(img_bytes)
                            cbz.writestr(filename, img_bytes)
                            pages_written += 1
                            signals.update_page_bar.emit(
                                pct,
                                _("dialogs.batch_pdf.page_progress").format(
                                    current=cur, total=tot))
                        elif msg[0] == 'page_error':
                            # La conversion continue jusqu'à 'done' (le process
                            # doit finir ce document avant le suivant).
                            from modules.qt.pdf_loading_qt import pdf_page_error_text
                            page_failures.append(pdf_page_error_text(msg))
                        elif msg[0] == 'done':
                            break
                        elif msg[0] == 'error':
                            conversion_errors.append(f"{basename}: {msg[1]}")
                            pdf_incomplete = True
                            break
                    # Process à remplacer seulement après un timeout ou une
                    # erreur du process, pas après une page non rendue.
                    process_failed = pdf_incomplete
                    if not pdf_incomplete and (page_failures or pages_written != total_pages):
                        pdf_incomplete = True
                        detail = page_failures[0] if page_failures else f"{pages_written}/{total_pages}"
                        conversion_errors.append(
                            f"{basename}: " + _("messages.errors.batch_incomplete_kept",
                                                count=total_pages - pages_written, detail=detail))
                    if pdf_incomplete:
                        raise _IncompleteCbz()
            except _IncompleteCbz:
                if process_failed:
                    _restart_process()
                continue
            except Exception as e:
                # Le CBZ en cours d'écriture n'a jamais pris son nom final
                # (_interruptible_zip_writer) : rien à supprimer.
                conversion_errors.append(f"{basename}: {e}")
                _restart_process()
                continue

            gc.collect()

            if not _cbz_verified(cbz_path, basename, pages_written, conversion_errors):
                continue

            if pdf_path not in owner_protected:
                try:
                    if is_permanent:
                        os.remove(pdf_path)
                    else:
                        callbacks['safe_delete_file'](pdf_path)
                except Exception as del_err:
                    conversion_errors.append(f"{basename} (suppression): {del_err}")

            converted_count[0] += 1

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        if conversion_errors:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "pdftocbz", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write("MosaicView - PDF to CBZ Batch Conversion Log\n")
                    lf.write(f"Date: {now.strftime('%Y-%m-%d %H:%M')}\n")
                    lf.write(f"Directory: {directory}\n")
                    lf.write(f"Total files: {len(pdf_files)}\n")
                    lf.write(f"Converted: {converted_count[0]}\n")
                    lf.write(f"Errors: {len(conversion_errors)}\n")
                    lf.write(f"\n{'='*60}\n")
                    lf.write("Error details:\n")
                    lf.write(f"{'='*60}\n\n")
                    for err in conversion_errors:
                        lf.write(f"  - {log_text(err)}\n")
            except Exception:
                pass
                log_path = None

        summary_data = {
            "converted_count":     converted_count[0],
            "total":               len(pdf_files),
            "errors_count":        len(conversion_errors),
            "has_errors":          bool(conversion_errors),
            "directory":           directory,
            "directories":         directories,
            "log_path":            log_path,
        }
        show_batch_pdf_summary(parent, summary_data, callbacks)

    signals.conversion_done.connect(on_done)
    _start_batch_thread(do_conversion, parent, claims, prog, signals)


def show_batch_pdf_summary(parent, data, callbacks):
    dlg = _PdfSummaryDialog(parent, data, callbacks)
    dlg.show()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog choix de mode IMG→CBZ
# ═══════════════════════════════════════════════════════════════════════════════

class _ImgModeDialog(QDialog):
    """Demande à l'utilisateur le mode de regroupement : une image/CBZ ou tout en un."""

    MODE_ONE_PER_IMAGE = "one_per_image"
    MODE_ALL_IN_ONE    = "all_in_one"

    def __init__(self, parent):
        super().__init__(parent)
        self.setModal(False)
        self.setMinimumWidth(480)
        self.chosen_mode = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self._title_lbl = QLabel()
        self._title_lbl.setAlignment(Qt.AlignCenter)
        self._title_lbl.setWordWrap(True)
        layout.addWidget(self._title_lbl)

        self._intro_lbl = QLabel()
        self._intro_lbl.setAlignment(Qt.AlignCenter)
        self._intro_lbl.setWordWrap(True)
        layout.addWidget(self._intro_lbl)

        layout.addSpacing(6)

        self._group = QButtonGroup(self)

        # Option 1 — une image par CBZ
        opt1_layout = QVBoxLayout()
        opt1_layout.setSpacing(0)
        opt1_layout.setContentsMargins(0, 0, 0, 0)
        self._radio1 = QRadioButton()
        self._radio1.setChecked(True)
        self._group.addButton(self._radio1)
        opt1_layout.addWidget(self._radio1, alignment=Qt.AlignCenter)
        self._sub1 = QLabel()
        self._sub1.setAlignment(Qt.AlignCenter)
        opt1_layout.addWidget(self._sub1, alignment=Qt.AlignCenter)
        layout.addLayout(opt1_layout)

        layout.addSpacing(8)

        # Option 2 — toutes les images en un seul CBZ
        opt2_layout = QVBoxLayout()
        opt2_layout.setSpacing(0)
        opt2_layout.setContentsMargins(0, 0, 0, 0)
        self._radio2 = QRadioButton()
        self._group.addButton(self._radio2)
        opt2_layout.addWidget(self._radio2, alignment=Qt.AlignCenter)
        self._sub2 = QLabel()
        self._sub2.setAlignment(Qt.AlignCenter)
        self._sub2.setWordWrap(True)
        opt2_layout.addWidget(self._sub2)
        layout.addLayout(opt2_layout)

        layout.addSpacing(12)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok_btn = QPushButton()
        self._ok_btn.setFixedWidth(110)
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(self._ok_btn)
        self._cancel_btn = QPushButton()
        self._cancel_btn.setFixedWidth(110)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.rejected.connect(self._on_cancel)
        self._retranslate()
        _connect_lang(self, lambda _lang: self._retranslate())
        self._ok_btn.setFocus()
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
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )
        font      = _get_current_font(10)
        font_bold = _get_current_font(11)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        radio_style = f"QRadioButton {{ color: {theme['text']}; }}"

        self.setWindowTitle(_wt("dialogs.batch_img.mode_title"))
        self._title_lbl.setText(_("dialogs.batch_img.mode_title"))
        self._title_lbl.setFont(font_bold)
        self._intro_lbl.setText(_("dialogs.batch_img.mode_intro"))
        self._intro_lbl.setFont(font)

        self._radio1.setText(_("dialogs.batch_img.mode_one_per_image"))
        self._radio1.setFont(font)
        self._radio1.setStyleSheet(radio_style)
        self._sub1.setText(_("dialogs.batch_img.mode_one_per_image_sub"))
        self._sub1.setFont(_get_current_font(9))
        self._sub1.setStyleSheet(f"color: {theme.get('disabled', '#888888')};")

        self._radio2.setText(_("dialogs.batch_img.mode_all_in_one"))
        self._radio2.setFont(font)
        self._radio2.setStyleSheet(radio_style)
        self._sub2.setText(_("dialogs.batch_img.mode_all_in_one_sub"))
        self._sub2.setFont(_get_current_font(9))
        self._sub2.setStyleSheet(f"color: {theme.get('disabled', '#888888')};")

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)
        self._cancel_btn.setText(_("buttons.cancel"))
        self._cancel_btn.setFont(font)
        self._cancel_btn.setStyleSheet(btn_style)

    def _on_ok(self):
        self.chosen_mode = (
            self.MODE_ONE_PER_IMAGE if self._radio1.isChecked()
            else self.MODE_ALL_IN_ONE
        )
        self.accept()

    def _on_cancel(self):
        self.chosen_mode = None
        self.accept()


# ═══════════════════════════════════════════════════════════════════════════════
# Fonctions publiques — IMG→CBZ
# ═══════════════════════════════════════════════════════════════════════════════

def batch_convert_img_to_cbz(parent, callbacks, directory=None):
    """Lance la conversion batch images→CBZ."""
    # Choix du mode avant la sélection du répertoire
    mode_dlg = _ImgModeDialog(parent)

    def _on_mode_done():
        if mode_dlg.chosen_mode is None:
            return
        chosen_mode = mode_dlg.chosen_mode

        actual_directory = directory
        if actual_directory is None:
            cfg = get_config_manager()
            actual_directory = QFileDialog.getExistingDirectory(
                parent, _wt("dialogs.batch_img.select_directory_title"),
                cfg.get('last_open_dir', ""))
            if actual_directory:
                cfg.set('last_open_dir', actual_directory)
        if not actual_directory:
            return

        img_files = []
        for dirpath, _subdirs, filenames in walk_without_links(actual_directory):
            for fn in filenames:
                if fn.lower().endswith(IMAGE_EXTS):
                    img_files.append(os.path.join(dirpath, fn))
        img_files.sort(key=lambda f: callbacks['natural_sort_key'](os.path.basename(f).lower()))

        if not img_files:
            InfoDialog(parent, lambda: _wt("dialogs.batch_img.no_img_title"),
                       lambda d=actual_directory: _("dialogs.batch_img.no_img_message").format(directory=d)).show()
            return

        if chosen_mode == _ImgModeDialog.MODE_ONE_PER_IMAGE:
            batch_convert_img_to_cbz_confirm(parent, img_files, actual_directory, callbacks)
        else:
            batch_convert_imgs_to_single_cbz(parent, img_files, actual_directory, callbacks)

    mode_dlg.finished.connect(lambda _: _on_mode_done())
    mode_dlg.show()


def batch_convert_img_to_cbz_confirm(parent, img_files, directory, callbacks, directories=None):
    """Fenêtre de confirmation + progression + résumé IMG→CBZ."""
    img_files  = [os.path.normpath(f) for f in img_files]
    if _refuse_if_batch_running(parent, img_files):
        return
    directory  = os.path.normpath(directory) if directory else directory
    if directories:
        directories = [os.path.normpath(d) for d in directories]
    total_size = sum(os.path.getsize(f) for f in img_files)

    dlg = _ConfirmDialog(
        parent,
        title_key    = "dialogs.batch_img.confirm_title",
        msg_key      = "dialogs.batch_img.confirm_message",
        count        = len(img_files),
        directory    = directory,
        total_size   = total_size,
        checkbox_key = "dialogs.batch_img.checkbox_permanent_delete",
        tooltip_key  = "tooltip.batch_img_permanent_delete",
        start_key    = "dialogs.batch_img.start_button",
        callbacks    = callbacks,
    )
    def _on_confirm_done_img():
        if not dlg.confirmed:
            return
        is_permanent = dlg.permanent_delete
        _run_img_conversion(parent, img_files, directory, directories, callbacks, is_permanent)

    dlg.finished.connect(lambda _: _on_confirm_done_img())
    dlg.show()


def _run_img_conversion(parent, img_files, directory, directories, callbacks, is_permanent):
    claims = _start_batch_claims(parent, img_files)
    if claims is None:
        return
    prog = _ProgressDialog(parent, "dialogs.batch_img.converting_title", has_page_bar=False)
    prog.show()
    QApplication.processEvents()

    signals           = _ThreadSignals()
    _register_batch(prog, signals)
    conversion_errors = []
    converted_count   = [0]
    converted_by_ext  = {}

    signals.update_filename.connect(prog.set_filename)
    signals.update_progress.connect(prog.set_progress)
    signals.update_thumb.connect(prog.set_thumbnail)

    def do_conversion():
        total = len(img_files)
        for idx, img_path in enumerate(img_files):
            basename  = os.path.basename(img_path)
            base_path, ext = os.path.splitext(img_path)
            ext_lower = ext.lower()

            signals.update_filename.emit(basename)
            signals.update_progress.emit(
                _("dialogs.batch_img.converting_progress").format(current=idx + 1, total=total))

            # Fichier et nom du CBZ réservés pour ce fichier seulement
            claims.release_all()
            if not _claim_file(claims, img_path, conversion_errors):
                continue

            # Vignette
            try:
                img = Image.open(img_path)
                px  = _pil_to_qimage(img, 150, 210, callbacks)
                signals.update_thumb.emit(px)
                img = None
            except Exception:
                signals.update_thumb.emit(None)

            try:
                with open(img_path, 'rb') as f:
                    img_data = f.read()

                try:
                    tmp = Image.open(io.BytesIO(img_data))
                    tmp.load()
                except Exception as val_err:
                    raise Exception(f"invalid or corrupted image: {val_err}")

                if tmp.format == 'ICO' or ext_lower == '.ico':
                    raise Exception("unsupported format: ICO files cannot be converted")

                # Un JPEG MPO (photo de téléphone avec une image annexe :
                # profondeur, aperçu) a plusieurs images pour Pillow, mais
                # c'est une photo ordinaire, que les lecteurs affichent
                n_frames = 1 if tmp.format == 'MPO' else getattr(tmp, 'n_frames', 1)
                if n_frames > 1:
                    fmt = tmp.format or ext_lower.lstrip('.').upper()
                    raise Exception(f"unsupported multi-frame image ({fmt}, {n_frames} frames)")

                # Nom de la page dans le CBZ avec l'extension du vrai format
                from modules.qt.archive_names import name_for_real_format
                page_name = name_for_real_format(basename, tmp.format)
                img_data = _normalized_page_bytes(img_data, tmp,
                                                  os.path.splitext(page_name)[1].lower())
                tmp = None

                cbz_path = claims.reserve(
                    free_name_candidates(base_path, ".cbz", "{base}_{n:02d}{ext}"))

                from modules.qt.utils import zip_compression_kwargs
                from modules.qt.config_manager import get_config_manager as _gcm
                with _interruptible_zip_writer(
                        cbz_path, **zip_compression_kwargs(_gcm().get_zip_compression_level())) as cbz:
                    cbz.writestr(page_name, img_data)

                img_data = None
                gc.collect()

                if not _cbz_verified(cbz_path, basename, 1, conversion_errors):
                    continue

                try:
                    if is_permanent:
                        os.remove(img_path)
                    else:
                        callbacks['safe_delete_file'](img_path)
                except Exception as del_err:
                    conversion_errors.append(f"{basename} (suppression): {del_err}")

                converted_count[0] += 1
                converted_by_ext[ext_lower] = converted_by_ext.get(ext_lower, 0) + 1

            except Exception as e:
                conversion_errors.append(f"{basename}: {e}")

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        if conversion_errors:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "imgtocbz", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write("MosaicView - Image to CBZ Batch Conversion Log\n")
                    lf.write(f"Date: {now.strftime('%Y-%m-%d %H:%M')}\n")
                    lf.write(f"Directory: {directory}\n")
                    lf.write(f"Total files: {len(img_files)}\n")
                    lf.write(f"Converted: {converted_count[0]}\n")
                    lf.write(f"Errors: {len(conversion_errors)}\n")
                    lf.write(f"\n{'='*60}\n")
                    lf.write("Error details:\n")
                    lf.write(f"{'='*60}\n\n")
                    for err in conversion_errors:
                        lf.write(f"  - {log_text(err)}\n")
            except Exception:
                pass
                log_path = None

        summary_data = {
            "converted_count":  converted_count[0],
            "total":            len(img_files),
            "errors_count":     len(conversion_errors),
            "has_errors":       bool(conversion_errors),
            "directory":        directory,
            "directories":      directories,
            "log_path":         log_path,
            "converted_by_ext": dict(converted_by_ext),
        }
        show_batch_img_summary(parent, summary_data, callbacks)

    signals.conversion_done.connect(on_done)
    _start_batch_thread(do_conversion, parent, claims, prog, signals)


def show_batch_img_summary(parent, data, callbacks):
    dlg = _ImgSummaryDialog(parent, data)
    dlg.show()


def batch_convert_imgs_to_single_cbz(parent, img_files, directory, callbacks):
    """Regroupe toutes les images du répertoire en un seul fichier CBZ."""
    img_files  = [os.path.normpath(f) for f in img_files]
    if _refuse_if_batch_running(parent, img_files):
        return
    directory  = os.path.normpath(directory)
    total_size = sum(os.path.getsize(f) for f in img_files)

    dlg = _ConfirmDialog(
        parent,
        title_key    = "dialogs.batch_img.confirm_title",
        msg_key      = "dialogs.batch_img.confirm_message",
        count        = len(img_files),
        directory    = directory,
        total_size   = total_size,
        checkbox_key = "dialogs.batch_img.checkbox_permanent_delete",
        tooltip_key  = "tooltip.batch_img_permanent_delete",
        start_key    = "dialogs.batch_img.start_button",
        callbacks    = callbacks,
    )
    def _on_confirm_done_single():
        if not dlg.confirmed:
            return
        is_permanent = dlg.permanent_delete
        _run_imgs_to_single_cbz(parent, img_files, directory, callbacks, is_permanent)

    dlg.finished.connect(lambda _: _on_confirm_done_single())
    dlg.show()


def _run_imgs_to_single_cbz(parent, img_files, directory, callbacks, is_permanent):
    claims = _start_batch_claims(parent, img_files)
    if claims is None:
        return
    prog = _ProgressDialog(parent, "dialogs.batch_img.converting_title", has_page_bar=False)
    prog.show()
    QApplication.processEvents()

    signals           = _ThreadSignals()
    _register_batch(prog, signals)
    conversion_errors = []
    converted_count   = [0]
    converted_by_ext  = {}

    signals.update_filename.connect(prog.set_filename)
    signals.update_progress.connect(prog.set_progress)
    signals.update_thumb.connect(prog.set_thumbnail)

    # Nom libre, réservé pour toute la durée du lot (batch_claims)
    cbz_path_out = claims.reserve(free_name_candidates(
        os.path.join(directory, os.path.basename(directory)), ".cbz", "{base}_{n:02d}{ext}"))

    def do_conversion():
        # Images réservées par un autre lot encore en cours : laissées de côté
        # (journal), elles pourraient être supprimées par ce dernier.
        free_files = [p for p in img_files if _claim_file(claims, p, conversion_errors)]
        if not free_files:
            signals.conversion_done.emit()
            return
        total = len(free_files)
        # Images écrites dans le CBZ, supprimées seulement une fois le CBZ fermé :
        # tant que son répertoire central n'est pas écrit, il est illisible, et
        # une interruption à la fermeture de l'application le supprime.
        written_images = []
        # Images de sous-dossiers différents (os.walk) de même nom : noms
        # d'entrée distincts, sinon une seule serait lisible dans le CBZ alors
        # que toutes les images sources sont supprimées.
        written_names = set()
        # Ordre de lecture : par chemin relatif complet (dossier puis nom), et
        # nom à plat qui garde le dossier (_flat_names) — trié sur le seul nom
        # de fichier, les images de sous-dossiers différents s'entrelaçaient.
        try:
            base_dir = os.path.commonpath([os.path.dirname(p) for p in free_files])
            relative = {p: os.path.relpath(p, base_dir) for p in free_files}
        except ValueError:
            # Images sur des lecteurs différents : pas de dossier commun, le
            # chemin complet de chaque image tient lieu de chemin relatif.
            relative = {p: os.path.splitdrive(p)[1] for p in free_files}
        free_files.sort(key=lambda p: callbacks['natural_sort_key'](relative[p].lower()))
        flat = _flat_names(list(relative.values()))
        from modules.qt.archive_names import name_for_real_format
        from modules.qt.utils import zip_compression_kwargs
        from modules.qt.config_manager import get_config_manager as _gcm
        try:
            with _interruptible_zip_writer(
                    cbz_path_out, **zip_compression_kwargs(_gcm().get_zip_compression_level())) as cbz:
                for idx, img_path in enumerate(free_files):
                    basename  = os.path.basename(img_path)
                    ext_lower = os.path.splitext(img_path)[1].lower()

                    signals.update_filename.emit(basename)
                    signals.update_progress.emit(
                        _("dialogs.batch_img.converting_progress").format(current=idx + 1, total=total))

                    # Vignette
                    try:
                        img = Image.open(img_path)
                        px  = _pil_to_qimage(img, 150, 210, callbacks)
                        signals.update_thumb.emit(px)
                        img = None
                    except Exception:
                        signals.update_thumb.emit(None)

                    try:
                        with open(img_path, 'rb') as f:
                            img_data = f.read()

                        try:
                            tmp = Image.open(io.BytesIO(img_data))
                            tmp.load()
                        except Exception as val_err:
                            raise Exception(f"invalid or corrupted image: {val_err}")

                        if tmp.format == 'ICO' or ext_lower == '.ico':
                            raise Exception("unsupported format: ICO files cannot be converted")

                        # JPEG MPO : une photo ordinaire (voir _run_img_conversion)
                        n_frames = 1 if tmp.format == 'MPO' else getattr(tmp, 'n_frames', 1)
                        if n_frames > 1:
                            fmt = tmp.format or ext_lower.lstrip('.').upper()
                            raise Exception(f"unsupported multi-frame image ({fmt}, {n_frames} frames)")

                        # Nom de la page avec l'extension du vrai format
                        page_name = name_for_real_format(flat[relative[img_path]], tmp.format)
                        img_data = _normalized_page_bytes(img_data, tmp,
                                                          os.path.splitext(page_name)[1].lower())
                        tmp = None

                        cbz.writestr(_unique_flat_name(page_name, written_names), img_data)
                        img_data = None
                        gc.collect()
                        written_images.append((img_path, basename, ext_lower))

                    except Exception as e:
                        conversion_errors.append(f"{basename}: {e}")

            if written_images and not _cbz_verified(
                    cbz_path_out, os.path.basename(cbz_path_out), len(written_images),
                    conversion_errors):
                written_images = []

            for img_path, basename, ext_lower in written_images:
                try:
                    if is_permanent:
                        os.remove(img_path)
                    else:
                        callbacks['safe_delete_file'](img_path)
                except Exception as del_err:
                    conversion_errors.append(f"{basename} (suppression): {del_err}")

                converted_count[0] += 1
                converted_by_ext[ext_lower] = converted_by_ext.get(ext_lower, 0) + 1

        except Exception as e:
            conversion_errors.append(f"CBZ creation failed: {e}")

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        if conversion_errors:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "imgtocbz", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write("MosaicView - Images to single CBZ Batch Conversion Log\n")
                    lf.write(f"Date: {now.strftime('%Y-%m-%d %H:%M')}\n")
                    lf.write(f"Directory: {directory}\n")
                    lf.write(f"Output: {cbz_path_out}\n")
                    lf.write(f"Total files: {len(img_files)}\n")
                    lf.write(f"Converted: {converted_count[0]}\n")
                    lf.write(f"Errors: {len(conversion_errors)}\n")
                    lf.write(f"\n{'='*60}\n")
                    lf.write("Error details:\n")
                    lf.write(f"{'='*60}\n\n")
                    for err in conversion_errors:
                        lf.write(f"  - {log_text(err)}\n")
            except Exception:
                pass
                log_path = None

        summary_data = {
            "converted_count":  converted_count[0],
            "total":            len(img_files),
            "errors_count":     len(conversion_errors),
            "has_errors":       bool(conversion_errors),
            "directory":        directory,
            "directories":      None,
            "log_path":         log_path,
            "converted_by_ext": dict(converted_by_ext),
        }
        show_batch_img_summary(parent, summary_data, callbacks)

    signals.conversion_done.connect(on_done)
    _start_batch_thread(do_conversion, parent, claims, prog, signals)


# ═══════════════════════════════════════════════════════════════════════════════
# Batch : recompression ZIP des CBZ au taux par défaut
# ═══════════════════════════════════════════════════════════════════════════════

class _RecompressConfirmDialog(QDialog):
    """Fenêtre de confirmation avant le lancement de la recompression batch.
    Pas de checkbox (pas de suppression de fichiers dans ce batch)."""

    def __init__(self, parent, count, directory, total_size, level):
        super().__init__(parent)
        self._count      = count
        self._directory  = directory
        self._total_size = total_size
        self._level      = level
        self.confirmed   = False

        self.setModal(False)
        self.setFixedWidth(500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg_lbl)

        self._size_lbl = QLabel()
        self._size_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._size_lbl)

        layout.addSpacing(6)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._start_btn = QPushButton()
        self._start_btn.setFixedWidth(110)
        self._start_btn.clicked.connect(self._on_start)
        self._start_btn.setDefault(True)
        btn_row.addWidget(self._start_btn)
        self._cancel_btn = QPushButton()
        self._cancel_btn.setFixedWidth(110)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.rejected.connect(self._on_cancel)
        self._retranslate()
        _connect_lang(self, lambda _: self._retranslate())
        self._start_btn.setFocus()
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
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        display_dir = self._directory if len(self._directory) <= 60 else "..." + self._directory[-57:]
        self.setWindowTitle(_wt("dialogs.batch_recompress.window_title"))
        self._msg_lbl.setText(_("dialogs.batch_recompress.confirm_message").format(
            count=self._count, directory=display_dir, level=self._level))
        self._msg_lbl.setFont(font)
        self._size_lbl.setText(f"({format_file_size(self._total_size)})")
        self._size_lbl.setFont(font)
        self._start_btn.setText(_("dialogs.batch_recompress.start_button"))
        self._start_btn.setFont(font)
        self._start_btn.setStyleSheet(btn_style)
        self._cancel_btn.setText(_("buttons.cancel"))
        self._cancel_btn.setFont(font)
        self._cancel_btn.setStyleSheet(btn_style)

    def _on_start(self):
        self.confirmed = True
        self.accept()

    def _on_cancel(self):
        self.confirmed = False
        self.accept()


def batch_recompress_cbz_confirm(parent, archive_files, directory, callbacks, directories=None):
    """Fenêtre de confirmation + progression + résumé pour la recompression batch des CBZ."""
    archive_files = [os.path.normpath(f) for f in archive_files]
    if _refuse_if_batch_running(parent, archive_files):
        return
    directory     = os.path.normpath(directory) if directory else directory
    if directories:
        directories = [os.path.normpath(d) for d in directories]
    total_size = sum(os.path.getsize(f) for f in archive_files)
    level = get_config_manager().get_zip_compression_level()

    dlg = _RecompressConfirmDialog(parent, len(archive_files), directory, total_size, level)

    def _on_confirm_done():
        if not dlg.confirmed:
            return
        _run_recompress(parent, archive_files, directory, directories, callbacks, level)

    dlg.finished.connect(lambda _: _on_confirm_done())
    dlg.show()


def _run_recompress(parent, archive_files, directory, directories, callbacks, level):
    from modules.qt.utils import zip_compression_kwargs
    from modules.qt.archive_loader import _detect_zip_compression_state

    claims = _start_batch_claims(parent, archive_files)
    if claims is None:
        return
    prog = _ProgressDialog(parent, "dialogs.batch_recompress.running_title", has_page_bar=False)
    prog.show()
    QApplication.processEvents()

    signals = _ThreadSignals()
    _register_batch(prog, signals)

    conversion_errors = []
    renamed_entries    = []
    recompressed_count = [0]
    already_optimal_count = [0]
    ignored_count       = [0]

    signals.update_filename.connect(prog.set_filename)
    signals.update_progress.connect(prog.set_progress)
    signals.update_thumb.connect(prog.set_thumbnail)

    def do_recompress():
        total = len(archive_files)
        for idx, path in enumerate(archive_files):
            basename = os.path.basename(path)
            signals.update_filename.emit(basename)
            signals.update_progress.emit(
                _("dialogs.batch_recompress.progress").format(current=idx + 1, total=total))

            # Fichier et nom de renommage réservés pour ce fichier seulement
            claims.release_all()
            if not _claim_file(claims, path, conversion_errors):
                continue

            real_type = detect_archive_type(path)

            if real_type != "CBZ":
                # Vraie archive non-CBZ (CBR/CB7/CBT réel, ou format inconnu) : ignorée
                signals.update_thumb.emit(None)
                ignored_count[0] += 1
                continue

            # Vignette : première image du CBZ
            try:
                with open_zip(path) as arc:
                    names = sorted(
                        [f for f in arc.namelist() if not f.endswith('/') and f.lower().endswith(IMAGE_EXTS)],
                        key=lambda f: callbacks['natural_sort_key'](os.path.basename(f).lower())
                    )
                    if names:
                        data = arc.read(names[0])
                        img  = Image.open(io.BytesIO(data))
                        px   = _pil_to_qimage(img, 150, 210, callbacks)
                        signals.update_thumb.emit(px)
                        img = None
                    else:
                        signals.update_thumb.emit(None)
            except Exception:
                signals.update_thumb.emit(None)

            # Fichier mal nommé (extension ≠ .cbz mais contenu réellement ZIP) : renommer en .cbz
            target_path = path
            if os.path.splitext(path)[1].lower() != ".cbz":
                base_path, _ext = os.path.splitext(path)
                new_path = claims.reserve(free_name_candidates(base_path, ".cbz"))
                try:
                    os.rename(path, new_path)
                    renamed_entries.append(f"{basename} → {os.path.basename(new_path)}")
                    target_path = new_path
                except Exception as e:
                    conversion_errors.append(f"{basename}: {e}")
                    continue

            # Déjà au taux par défaut : ne pas retraiter
            state = _detect_zip_compression_state(target_path)
            if level <= 0 and state == "stored":
                already_optimal_count[0] += 1
                continue

            # Recompresse via fichier temporaire. Le CBZ d'origine reste ouvert en
            # lecture jusqu'à la fin de l'écriture : le nouveau contenu ne peut
            # le remplacer qu'après, d'où ce temporaire intermédiaire.
            from modules.qt.utils import make_temp_path, release_temp_path
            tmp_path = None
            try:
                tmp_path = make_temp_path(os.path.dirname(os.path.abspath(target_path)))
                # Noms réels des membres (open_zip) : réécrits ici avec le
                # drapeau UTF-8, un nom mal lu le resterait définitivement
                with open_zip(target_path) as zin, \
                     _interruptible_zip_writer(tmp_path, **zip_compression_kwargs(level)) as zout:
                    # Lecture par ZipInfo, jamais par nom : read(nom) rend le
                    # contenu du dernier homonyme pour chacun d'eux, et le
                    # premier serait remplacé par une copie du second.
                    for info in zin.infolist():
                        zout.writestr(info.filename, zin.read(info))
                    # Commentaire d'archive (métadonnées ComicBookInfo) conservé
                    zout.comment = zin.comment
                # os.replace et non shutil.move : sous Windows, ce dernier
                # tronque le CBZ d'origine puis le recopie quand il existe déjà.
                # replace_file_retrying : CBZ d'origine ouvert ailleurs.
                from modules.qt.utils import replace_file_retrying
                replace_file_retrying(tmp_path, target_path)
                recompressed_count[0] += 1
            except Exception as e:
                conversion_errors.append(f"{os.path.basename(target_path)}: {e}")
            finally:
                # Après un remplacement réussi le temporaire n'existe plus ;
                # sinon (erreur, interruption à la fermeture) il est supprimé.
                if tmp_path:
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except OSError:
                        pass
                    release_temp_path(tmp_path)

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        if conversion_errors or renamed_entries:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "recompress", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write("MosaicView - CBZ Recompression Batch Log\n")
                    lf.write(f"Date: {now.strftime('%Y-%m-%d %H:%M')}\n")
                    lf.write(f"Directory: {directory}\n")
                    lf.write(f"Total files: {len(archive_files)}\n")
                    lf.write(f"Recompressed: {recompressed_count[0]}\n")
                    lf.write(f"Already optimal: {already_optimal_count[0]}\n")
                    lf.write(f"Ignored (not CBZ): {ignored_count[0]}\n")
                    lf.write(f"Renamed (mis-named → .cbz): {len(renamed_entries)}\n")
                    lf.write(f"Errors: {len(conversion_errors)}\n")
                    if renamed_entries:
                        lf.write(f"\n{'='*60}\n")
                        lf.write("Renamed files:\n")
                        lf.write(f"{'='*60}\n\n")
                        for entry in renamed_entries:
                            lf.write(f"  - {log_text(entry)}\n")
                    if conversion_errors:
                        lf.write(f"\n{'='*60}\n")
                        lf.write("Error details:\n")
                        lf.write(f"{'='*60}\n\n")
                        for err in conversion_errors:
                            lf.write(f"  - {log_text(err)}\n")
            except Exception:
                pass
                log_path = None

        summary_data = {
            "total":               len(archive_files),
            "recompressed_count":  recompressed_count[0],
            "already_optimal_count": already_optimal_count[0],
            "ignored_count":       ignored_count[0],
            "renamed_count":       len(renamed_entries),
            "errors_count":        len(conversion_errors),
            "has_errors":          bool(conversion_errors),
            "directory":           directory,
            "directories":         directories,
            "log_path":            log_path,
        }
        show_batch_recompress_summary(parent, summary_data)

    signals.conversion_done.connect(on_done)
    _start_batch_thread(do_recompress, parent, claims, prog, signals)


class _RecompressSummaryDialog(QDialog):
    def __init__(self, parent, data):
        super().__init__(parent)
        self._data = data
        self.setModal(False)
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        top = QVBoxLayout()
        top.setSpacing(0)
        top.setContentsMargins(0, 0, 0, 0)
        self._msg_lbl = QLabel()
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        top.addWidget(self._msg_lbl)
        _add_dir_links(top, data)
        layout.addLayout(top)

        self._error_lbl = None
        log_path = data.get("log_path")
        if data.get("has_errors") or log_path:
            self._error_lbl = QLabel()
            self._error_lbl.setWordWrap(True)
            self._error_lbl.setAlignment(Qt.AlignCenter)
            self._error_lbl.setStyleSheet("color: #4A9EFF;")
            self._error_lbl.setCursor(QCursor(Qt.PointingHandCursor))
            self._error_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
            if log_path:
                self._error_lbl.linkActivated.connect(lambda _, p=log_path: _open_path(p))
                from modules.qt.utils import setup_path_label_context_menu
                setup_path_label_context_menu(self._error_lbl, lambda p=log_path: p, lambda p=log_path: _open_path(p))
            layout.addWidget(self._error_lbl)

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
        _connect_lang(self, lambda _: self._retranslate())
        self._ok_btn.setFocus()
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
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        data = self._data
        self.setWindowTitle(_wt("dialogs.batch_recompress.complete_title"))
        self._msg_lbl.setText(_("dialogs.batch_recompress.summary_message").format(
            total=data["total"],
            recompressed=data["recompressed_count"],
            already_optimal=data["already_optimal_count"],
            ignored=data["ignored_count"],
        ))
        self._msg_lbl.setFont(font)

        if self._error_lbl is not None:
            log_path = data.get("log_path")
            if data.get("has_errors") and log_path:
                text = _("dialogs.batch_recompress.errors_with_log").format(
                    count=data["errors_count"])
                self._error_lbl.setText(f'<a href="#">{text}</a>')
            elif log_path:
                self._error_lbl.setText(f'<a href="#">{_("dialogs.batch_recompress.view_log")}</a>')
            self._error_lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)


def show_batch_recompress_summary(parent, data):
    dlg = _RecompressSummaryDialog(parent, data)
    dlg.show()
