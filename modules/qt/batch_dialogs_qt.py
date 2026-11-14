"""
batch_dialogs_qt.py — Conversion par lot (CBR→CBZ, PDF→CBZ, IMG→CBZ).
Toutes les fenêtres supportent le changement de langue à la volée via language_signal.
"""

import os
import io
import gc
import html
import contextlib
import threading

from PIL import Image

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QProgressBar, QFrame, QSizePolicy,
    QApplication, QRadioButton, QButtonGroup,
)
from PySide6.QtCore import Qt, QMetaObject, Q_ARG, QObject, Signal
from PySide6.QtGui import QPixmap, QImage, QDesktopServices, QCursor
from PySide6.QtCore import QUrl

from modules.qt.file_picker_qt import ask_directory
from modules.qt.localization import _, _wt
from modules.qt.utils import (estimate_archive_copy_size, format_file_size, walk_without_links,
                              WrittenFileCheckError)
from modules.qt.temp_files import unique_log_path, log_text, exception_text
from modules.qt.state import get_current_theme
from modules.qt.config_manager import get_config_manager
from modules.qt.font_manager_qt import get_current_font as _get_current_font
from modules.qt.dialogs_qt import ErrorDialog, InfoDialog
from modules.qt.app_window_qt import AppWindowMixin
from modules.qt.archive_type_detector import detect_archive_type
from modules.qt.archive_members import _check_member_size, open_archive
from modules.qt.archive_names import open_zip, IMAGE_EXTS
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


def _failure_text(error):
    """Texte d'un échec pour le journal du lot (exception_text). La pile d'une
    erreur inattendue (défaut du programme, et non fichier abîmé ou refusé)
    va en plus au journal des erreurs : le journal du lot n'en garde que le
    message."""
    from modules.qt.error_log import log_if_unexpected
    log_if_unexpected(error)
    return exception_text(error)


def _is_disk_full(error):
    """Place manquante pour écrire un CBZ : refusée avant d'écrire
    (utils.DiskSpaceError, levée par converted_cbz.write_cbz) ou disque
    rempli pendant l'écriture (ENOSPC, la taille prévue n'étant qu'une
    estimation). Le lot s'arrête alors : les fichiers suivants ne
    trouveraient pas la place non plus."""
    import errno
    from modules.qt.utils import DiskSpaceError
    return isinstance(error, DiskSpaceError) or (
        isinstance(error, OSError) and error.errno == errno.ENOSPC)


def _stop_for_disk_full(error, basename, not_processed, conversion_errors, disk_full,
                        last_done=None):
    """Arrêt du lot faute de place (_is_disk_full) : ligne du journal pour le
    fichier en cours (raison, arrêt, nombre de fichiers non traités, lui
    compris, et nom du dernier fichier traité), et ces deux informations
    rangées dans `disk_full` pour le résumé (data["disk_full"] :
    {"count", "last_done"}, _disk_full_lines). L'appelant sort ensuite de sa
    boucle.

    last_done : nom du dernier fichier fini sans erreur (converti, même si
    son original n'a pas pu être supprimé ; renommé ; recompressé ou déjà au
    bon taux), None si aucun."""
    info = {"count": not_processed, "last_done": last_done}
    disk_full[:] = [info]
    conversion_errors.append(f"{basename}: {_failure_text(error)} — "
                             + " ".join(_disk_full_lines(info)))


def _disk_full_lines(info):
    """Lignes traduites qui annoncent l'arrêt d'un lot faute de place
    (_stop_for_disk_full) : l'arrêt et le nombre de fichiers non traités,
    puis le dernier fichier traité s'il y en a un."""
    lines = [_("messages.errors.batch_stopped_disk_full", count=info["count"])]
    if info.get("last_done"):
        lines.append(_("messages.errors.batch_last_done", name=info["last_done"]))
    return lines




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
    """Appelée avant chaque écriture dans le CBZ (_interruptible_zip_writer) et au
    début de chaque fichier (_claim_file), jamais entre l'écriture d'un CBZ
    et la suppression de sa source."""
    if _batch_shutdown.is_set():
        raise _BatchInterrupted()
    cancel = getattr(_batch_local, "cancel", None)
    if cancel is not None and cancel.is_set():
        raise _BatchCancelled()


@contextlib.contextmanager
def _interruptible_zip_writer(path, verify=True, check_structure=False, needed=None,
                              **zip_kwargs):
    """À utiliser à la place de `zipfile.ZipFile(path, 'w', ...)` dans les
    threads de lot : l'écrivain commun converted_cbz.write_cbz (temporaire du
    dossier de `path`, qui ne prend ce nom qu'une fois fermé et relu ; rien
    sous ce nom en cas d'échec), dont chaque écriture vérifie d'abord que
    l'application n'est pas en train de se fermer et que le lot n'a pas été
    annulé (_raise_if_batch_stopped).

    verify : relecture complète du temporaire avant qu'il prenne son nom, que
    converted_cbz.delete_source exige pour supprimer ensuite la source.
    check_structure : contrôle rapide de la structure avant de prendre le nom,
    pour un CBZ qui remplace l'original sans qu'aucune source soit supprimée
    (recompression : verify=False, check_structure=True).
    needed : taille prévue du CBZ ; place libre vérifiée avant d'écrire
    (DiskSpaceError, qui arrête le lot : _is_disk_full)."""
    from modules.qt.converted_cbz import write_cbz
    with write_cbz(path, reread=verify, check_structure=check_structure,
                   before_write=_raise_if_batch_stopped, needed=needed, **zip_kwargs) as writer:
        yield writer


def _start_batch_thread(target, parent, claims, prog, signals):
    """Démarre le thread d'un traitement par lot, enregistré pour
    stop_batches_on_exit() et, avec son panneau `parent`, pour
    running_batch_panel(). Le lot (`claims`, batch_claims.BatchClaims.start)
    est terminé à la fin du thread, quelle qu'elle soit : ses fichiers
    redeviennent disponibles pour un autre lot. Annulé par le bouton de
    `prog`, le lot émet `signals.conversion_done` comme à sa fin normale.

    Le lot travaille sous localization.latin_texts() : tout texte traduit
    produit dans son thread (lignes d'erreur du journal, messages
    d'exception) l'est en latin pour une langue pIqaD ou tengwar, le journal
    étant lu dans le Bloc-notes. Les textes destinés à la fenêtre de
    progression lui sont passés en fonctions, résolues par elle (_text_fn)."""
    def _run():
        from modules.qt.localization import latin_texts
        _batch_local.cancel = prog.cancel_event
        try:
            with latin_texts():
                target()
        except _BatchCancelled:
            signals.conversion_done.emit()
        except _BatchInterrupted:
            pass  # application en cours de fermeture : fin silencieuse
        except Exception:
            # Erreur imprévue : la fin du lot est quand même émise (résumé des
            # fichiers déjà traités, fenêtre de progression fermée, qui sinon
            # refuserait de se fermer), puis l'erreur est relancée pour le
            # journal des erreurs.
            signals.conversion_done.emit()
            raise
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
    # Textes traduits : fonctions () -> str, résolues par la fenêtre de
    # progression (police et langue courantes, rejouées au changement de
    # langue), jamais dans le thread du lot, qui travaille sous latin_texts()
    update_progress   = Signal(object)
    update_page_bar   = Signal(float, object)   # valeur 0-100, texte
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


def log_line(key, **kwargs):
    """Ligne d'en-tête d'un journal de lot (`dialogs.batch_log.<key>`), en
    latin pour une langue en pIqaD ou tengwar : le journal est lu dans le
    Bloc-notes, qui ne sait pas afficher ces alphabets."""
    return _wt(f"dialogs.batch_log.{key}", **kwargs) + "\n"


def log_title(key):
    return f"MosaicView - {_wt(f'dialogs.batch_log.{key}')}\n"


def _log_label_text(data, link_text, error_text=None):
    """Texte du libellé du journal d'un résumé de lot : `link_text` en lien
    quand le journal a été écrit ; sinon `error_text` (nombre d'erreurs, None
    si le lot n'en a pas), suivi de la raison pour laquelle le journal n'a pas
    pu être écrit (data["log_error"]). Le lien ouvre le journal par
    linkActivated, jamais par son adresse."""
    # Lot arrêté faute de place (_stop_for_disk_full) : annoncé en tête, avec
    # le dernier fichier traité (nom venu du disque : échappé)
    lines = [html.escape(line) for line in _disk_full_lines(data["disk_full"])] \
        if data.get("disk_full") else []
    if data.get("log_path"):
        link = f'<a href="#">{link_text}</a>'
        if not lines:
            return link
        lines.append(link)
    else:
        if not lines and not data.get("log_error"):
            return error_text or ""
        if error_text:
            lines.append(error_text)
        if data.get("log_error"):
            lines.append(html.escape(_("dialogs.batch_log_failed", error=data["log_error"])))
    # Balise englobante : le libellé est alors toujours lu en HTML, et le
    # message échappé (chemin contenant « & ») s'affiche tel quel.
    return "<div>" + "<br>".join(lines) + "</div>"


def _unique_flat_name(member_path, written_names):
    """Nom d'entrée du CBZ aplati pour un membre d'archive source.

    Deux membres de même nom dans des dossiers différents (A/p.jpg, B/p.jpg)
    produiraient deux entrées ZIP homonymes dont une seule serait lisible, alors que
    l'archive source est supprimée après conversion. Le doublon reçoit un suffixe
    _2, _3... qui le trie juste après l'original. written_names (noms en minuscules)
    est mis à jour."""
    # basename lit « C: » comme un lecteur (« C: » → « », « C:.. » → « .. ») :
    # « _ » à la place, jamais un membre sans nom, « . » ou « .. »
    out_name = os.path.basename(member_path.replace('\\', '/'))
    if out_name in ("", ".", ".."):
        out_name = "_"
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
    (archive_names.without_control_chars). Segments « . » et « .. » retirés :
    un membre « x/.. » deviendrait sinon une entrée nommée « .. », et « _ »
    remplace un nom dont il ne reste rien.

    « / » et « _ » ne se trient pas au même rang : `Aaaa/001` précède `Aaaa[2]/001`,
    mais `Aaaa[2]_001` précède `Aaaa_001`. Si la mise à plat changerait ainsi l'ordre
    des images à la réouverture, chaque image reçoit son rang devant son nom. Un
    fichier qui n'est pas une image garde le sien : numéroté, un ComicInfo.xml ne
    serait plus reconnu."""
    # Clé de tri de la réouverture : celle de l'ouverture d'une archive
    from modules.qt.archive_names import IMAGE_EXTS, without_control_chars
    from modules.qt.archive_names import natural_key as _reopening_key

    parts = {p: [without_control_chars(x) for x in p.replace('\\', '/').split('/')
                 if x not in ("", ".", "..")]
             for p in member_paths}
    dirs = [segments[:-1] for segments in parts.values()]
    common = 0
    while dirs and all(len(d) > common for d in dirs) and len({d[common] for d in dirs}) == 1:
        common += 1
    flat = {p: "_".join(segments[common:]) or "_" for p, segments in parts.items()}

    images = [p for p in flat if os.path.splitext(flat[p])[1].lower() in IMAGE_EXTS]
    in_folders = sorted(images, key=lambda p: _reopening_key("/".join(parts[p][common:]).lower()))
    if in_folders != sorted(images, key=lambda p: _reopening_key(flat[p])):
        digits = max(4, len(str(len(images))))
        for rank, p in enumerate(in_folders, 1):
            flat[p] = f"{rank:0{digits}d}_{flat[p]}"
    return flat


# Lot PDF → CBZ : nombre de PDF traités par un même moteur PDF avant qu'il
# soit remplacé (voir _run_pdf_conversion)
_PDF_FILES_PER_ENGINE = 200


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
    from modules.qt.entries import encode_page, image_dpi, page_format
    # Mode courant d'abord (CMJN par son profil, 32 bits et flottant remis à
    # l'échelle), même vers JPEG ou TIFF qui sauraient garder le CMJN : c'est
    # le but de la normalisation, les lecteurs de BD affichant mal ces modes.
    out = _to_writable_mode(img)
    # Encodeur commun (entries.encode_page) : profil ICC s'il correspond au
    # mode écrit (celui d'une page CMJN, converti, ne l'est plus), résolution
    # de la page, compression TIFF d'origine sinon LZW. Métadonnées gardées
    # orientation comprise : les pixels ne sont pas tournés ici. JPEG en
    # qualité maximale.
    return encode_page(out, page_format(ext_lower) or "JPEG", raw, quality=100,
                       dpi=image_dpi(img), keep_orientation=True)


def _emit_first_page_thumbnail(path, signals, callbacks):
    """Vignette de la fenêtre de progression d'un lot : la première page de
    l'archive `path`, celle que montre son ouverture (archive_members.
    first_page : même ordre, mêmes protections). Vignette vide si l'archive
    n'a pas d'image ou si elle ne peut pas être lue (la conversion, elle,
    signale l'erreur)."""
    try:
        from modules.qt.archive_members import first_page
        from modules.qt.entries import open_checked_image
        page = first_page(path)
        if page:
            # Taille annoncée impossible refusée avant décodage
            img = open_checked_image(page[1])
            signals.update_thumb.emit(_pil_to_qimage(img, 150, 210, callbacks))
        else:
            signals.update_thumb.emit(None)
    except Exception:
        signals.update_thumb.emit(None)


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
                # Taille annoncée impossible : refusée avant décodage, la
                # page est alors écrite telle quelle (except plus bas)
                from modules.qt.entries import open_checked_image
                tmp = open_checked_image(raw)
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
            failures.append((name, _failure_text(e)))
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

    expected_error = True  # pages en échec déjà consignées : jamais écrite dans le journal des erreurs

    def __init__(self, failures=()):
        super().__init__()
        self.failures = list(failures)


class _UnusableImage(Exception):
    """Image refusée par un lot images → CBZ : illisible, icône, ou plusieurs
    images dans un fichier. Cas normal, consigné dans le journal du lot."""

    expected_error = True  # jamais écrite dans le journal des erreurs


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


def _finish_batch(on_finished, new_files, summary=None):
    """Prévient l'appelant d'un lot lancé sur une liste de fichiers
    (Bibliothèque) : on_finished(liste des CBZ créés et des fichiers
    renommés, (type du lot, données de son résumé)) à la fin du lot ;
    on_finished(None, None) si le lot n'a pas eu lieu (refusé : un autre lot
    traite l'un des fichiers ; annulé à la confirmation ou au choix du
    mode)."""
    if on_finished is not None:
        on_finished(new_files, summary)


def _end_batch(parent, kind, show_summary, summary_data, callbacks, on_finished, new_files):
    """Fin d'un lot : son résumé, ou, pour un lot d'une chaîne de la
    Bibliothèque (on_finished), ses données transmises à la chaîne, qui
    montre un seul résumé pour tous ses lots (show_chain_summary)."""
    if on_finished is None:
        show_summary(parent, summary_data, callbacks)
    else:
        _finish_batch(on_finished, new_files, (kind, summary_data))


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
        # Lien : chemin échappé (un nom de dossier peut contenir « & »)
        lbl = QLabel()
        lbl.setTextFormat(Qt.RichText)
        lbl.setText(f'<a href="file:///{html.escape(d, quote=True)}">{html.escape(display)}</a>')
        lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        lbl.setCursor(QCursor(Qt.PointingHandCursor))
        lbl.setStyleSheet("color: #4A9EFF;")
        lbl.setWordWrap(True)
        lbl.linkActivated.connect(lambda _, p=d: _open_path(p))
        lbl.setAlignment(Qt.AlignCenter)
        setup_path_label_context_menu(lbl, lambda p=d: p, lambda p=d: _open_path(p))
        layout.addWidget(lbl)


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de confirmation (commun aux 3 flux)
# ═══════════════════════════════════════════════════════════════════════════════

class _ConfirmDialog(AppWindowMixin, QDialog):
    """
    Fenêtre de confirmation avant le lancement d'une conversion batch.
    Affiche : message (count/directory), taille totale, checkbox suppression permanente,
    boutons Démarrer / Annuler.
    """

    def __init__(self, parent, title_key, msg_key, count, directory,
                 total_size, checkbox_key, tooltip_key, start_key, callbacks, files=()):
        """files : fichiers d'origine que le lot supprimera ; ceux qui ne
        peuvent pas aller à la Corbeille (recycle_bin.trash_problem_count) sont
        signalés tant que la case de suppression définitive n'est pas cochée."""
        super().__init__(parent)
        self._setup_window()
        self.set_window_title(title_key)
        from modules.qt.recycle_bin import trash_problem_count
        self._no_trash_count = trash_problem_count(files)
        self._msg_key      = msg_key
        self._count        = count
        self._directory    = directory
        self._total_size   = total_size
        self._checkbox_key = checkbox_key
        self._start_key    = start_key
        self._callbacks    = callbacks
        self.confirmed     = False

        self.setFixedWidth(500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        self._msg_lbl  = QLabel()
        self._msg_lbl.setTextFormat(Qt.PlainText)
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg_lbl)

        self._size_lbl = QLabel()
        self._size_lbl.setTextFormat(Qt.PlainText)
        self._size_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._size_lbl)

        layout.addSpacing(6)

        self._chk = QCheckBox()
        layout.addWidget(self._chk, alignment=Qt.AlignCenter)

        # Fichiers dont la suppression sera définitive même sans la case
        self._trash_lbl = QLabel()
        self._trash_lbl.setTextFormat(Qt.PlainText)
        self._trash_lbl.setWordWrap(True)
        self._trash_lbl.setAlignment(Qt.AlignCenter)
        self._trash_lbl.setVisible(bool(self._no_trash_count))
        layout.addWidget(self._trash_lbl)
        self._chk.toggled.connect(self._update_trash_warning)

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
        self._connect_language()
        self._start_btn.setFocus()

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
        if self._no_trash_count:
            # Avertissement non bloquant : couleur du texte, gras italique
            warn_font = _get_current_font(10)
            warn_font.setBold(True)
            warn_font.setItalic(True)
            self._trash_lbl.setFont(warn_font)
            self._trash_lbl.setStyleSheet(f"color: {theme['text']};")
            self._trash_lbl.setText(_("messages.trash.batch_permanent",
                                      count=self._no_trash_count, total=self._count))
            self._trash_lbl.setMinimumHeight(self._trash_lbl.heightForWidth(self.width() - 40))

    def _update_trash_warning(self, checked):
        """Case « sans passer par la corbeille » cochée : l'avertissement ne
        dit plus rien de nouveau."""
        if self._no_trash_count:
            self._trash_lbl.setVisible(not checked)
            self.adjustSize()

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

class _ProgressDialog(AppWindowMixin, QDialog):
    """
    Fenêtre de progression pendant la conversion.
    Affiche vignette, nom du fichier, barre de pages (optionnelle), progression globale.
    """

    def __init__(self, parent, title_key, has_page_bar=True):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title(title_key)
        self._has_page_bar = has_page_bar
        self._running     = True
        # Textes affichés : fonctions () -> str, rejouées par _retranslate
        self._progress_fn = None
        self._page_fn     = None
        # Lu par le thread du lot (_raise_if_batch_stopped) : le lot s'arrête
        # à sa prochaine écriture ou au fichier suivant.
        self.cancel_event = threading.Event()

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
        self._filename_lbl.setTextFormat(Qt.PlainText)
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
            self._page_lbl.setTextFormat(Qt.PlainText)
            self._page_lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(self._page_lbl)

        # Progression globale
        self._progress_lbl = QLabel("")
        self._progress_lbl.setTextFormat(Qt.PlainText)
        self._progress_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._progress_lbl)

        self._btn_cancel = QPushButton()
        self._btn_cancel.clicked.connect(self.request_cancel)
        layout.addWidget(self._btn_cancel, alignment=Qt.AlignCenter)

        self._connect_language()

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}")
        font = _get_current_font(10)
        self._filename_lbl.setFont(font)
        self._progress_lbl.setFont(font)
        if self._progress_fn is not None:
            self._progress_lbl.setText(self._progress_fn())
        if self._has_page_bar:
            self._page_lbl.setFont(font)
            if self._page_fn is not None:
                self._page_lbl.setText(self._page_fn())
        self._btn_cancel.setText(_("buttons.cancel"))
        self._btn_cancel.setFont(font)
        self._btn_cancel.setStyleSheet(
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 16px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }} "
            f"QPushButton:disabled {{ color: {theme['separator']}; }}")

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
        """`text` : fonction () -> str (texte traduit, résolu ici dans la
        langue courante et rejoué à chaque changement de langue), ou texte
        fixe."""
        self._progress_fn = text if callable(text) else (lambda t=text: t)
        self._progress_lbl.setText(self._progress_fn())

    def set_page_progress(self, value, text):
        """Comme set_progress, pour la barre de pages."""
        if self._has_page_bar:
            self._page_bar.setValue(int(value))
            self._page_fn = text if callable(text) else (lambda t=text: t)
            self._page_lbl.setText(self._page_fn())

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
        # Croix et Échap : la fenêtre reste ouverte jusqu'à la fin réelle du
        # lot, que son thread met encore à jour.
        if self._running:
            self.request_cancel()
            event.ignore()
        else:
            event.accept()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de résumé CBR
# ═══════════════════════════════════════════════════════════════════════════════

class _CbrSummaryDialog(AppWindowMixin, QDialog):
    def __init__(self, parent, data):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("dialogs.batch_cbr.complete_title")
        self._data = data
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        top = QVBoxLayout()
        top.setSpacing(0)
        top.setContentsMargins(0, 0, 0, 0)
        self._msg_lbl = QLabel()
        self._msg_lbl.setTextFormat(Qt.PlainText)
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
                lbl.setTextFormat(Qt.PlainText)
                lbl.setWordWrap(True)
                lbl.setAlignment(Qt.AlignCenter)
                layout.addWidget(lbl)
                setattr(self, f"_{key}_lbl", lbl)

        # Lien log (erreurs ou renommages)
        self._error_lbl = None
        if data.get("has_errors") or data.get("log_path") or data.get("log_error"):
            log_path = data.get("log_path")
            if log_path:
                self._error_lbl = QLabel()
                # Texte de _log_label_text (HTML, valeurs échappées)
                self._error_lbl.setTextFormat(Qt.RichText)
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
                # Texte de _log_label_text (HTML, valeurs échappées)
                self._error_lbl.setTextFormat(Qt.RichText)
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

        self._connect_language()
        self._ok_btn.setFocus()

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
            see_log = _("dialogs.see_log")
            if data.get("has_errors"):
                error_text = _("dialogs.batch_cbr.errors_count").format(count=data["errors_count"])
                self._error_lbl.setText(_log_label_text(data, f"{error_text} — {see_log}", error_text))
            else:
                # Renommages uniquement — juste le lien log
                self._error_lbl.setText(_log_label_text(data, see_log))
            self._error_lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de résumé PDF
# ═══════════════════════════════════════════════════════════════════════════════

class _PdfSummaryDialog(AppWindowMixin, QDialog):
    def __init__(self, parent, data, callbacks):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("dialogs.batch_pdf.complete_title")
        self._data      = data
        self._callbacks = callbacks
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        top = QVBoxLayout()
        top.setSpacing(0)
        top.setContentsMargins(0, 0, 0, 0)
        self._msg_lbl = QLabel()
        self._msg_lbl.setTextFormat(Qt.PlainText)
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        top.addWidget(self._msg_lbl)
        _add_dir_links(top, data)
        layout.addLayout(top)

        self._error_lbl = None
        if data.get("has_errors"):
            log_path = data.get("log_path")
            self._error_lbl = QLabel()
            # Lien vers le journal (_log_label_text, qui échappe ce qu'il insère)
            self._error_lbl.setTextFormat(Qt.RichText)
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

        self._connect_language()
        self._ok_btn.setFocus()

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
        if data.get("has_errors"):
            self._msg_lbl.setText(_("dialogs.batch_pdf.complete_message_errors").format(
                count=data["converted_count"], total=data["total"]))
        else:
            self._msg_lbl.setText(_("dialogs.batch_pdf.complete_message").format(
                count=data["converted_count"]))
        self._msg_lbl.setFont(font)

        if self._error_lbl is not None:
            error_text = _("dialogs.batch_pdf.errors_count").format(count=data["errors_count"])
            see_log = _("dialogs.see_log")
            self._error_lbl.setText(_log_label_text(data, f"{error_text} — {see_log}", error_text))
            self._error_lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)

    def _on_ok(self):
        self.accept()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de résumé IMG
# ═══════════════════════════════════════════════════════════════════════════════

class _ImgSummaryDialog(AppWindowMixin, QDialog):
    def __init__(self, parent, data):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("dialogs.batch_img.complete_title")
        self._data = data
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        top = QVBoxLayout()
        top.setSpacing(0)
        top.setContentsMargins(0, 0, 0, 0)
        self._msg_lbl = QLabel()
        self._msg_lbl.setTextFormat(Qt.PlainText)
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
            # Lien vers le journal (_log_label_text, qui échappe ce qu'il insère)
            self._error_lbl.setTextFormat(Qt.RichText)
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

        self._connect_language()
        self._ok_btn.setFocus()

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
            lbl = QLabel()
            lbl.setTextFormat(Qt.PlainText)
            lbl.setText(line)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setFont(font)
            self._ext_frame.addWidget(lbl, alignment=Qt.AlignCenter)
            self._ext_labels.append(lbl)

        if self._error_lbl is not None:
            error_text = _("dialogs.batch_img.errors_count").format(count=data["errors_count"])
            see_log = _("dialogs.see_log")
            self._error_lbl.setText(_log_label_text(data, f"{error_text} — {see_log}", error_text))
            self._error_lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)


# ═══════════════════════════════════════════════════════════════════════════════
# Fonctions publiques — CBR→CBZ
# ═══════════════════════════════════════════════════════════════════════════════

def _ask_batch_directory(parent, title_key, on_directory):
    """Dossier d'un traitement par lot, demandé par la fenêtre de choix de
    l'application (non modale) et mémorisé comme dernier dossier ouvert ;
    on_directory(dossier) seulement si un dossier a été choisi."""
    cfg = get_config_manager()

    def _on_result(directory):
        if directory:
            cfg.set('last_open_dir', directory)
            on_directory(directory)

    ask_directory(parent, title_key, cfg.get('last_open_dir', ""), _on_result)


def batch_convert_cbr_to_cbz(parent, callbacks, directory=None):
    """Lance la conversion batch CBR→CBZ. Équivalent de batch_dialogs.batch_convert_cbr_to_cbz."""
    if rarfile is None:
        ErrorDialog(parent, "dialogs.batch_cbr.no_cbr_title",
                    lambda: _("dialogs.batch_cbr.rarfile_unavailable")).show_nonmodal()
        return

    if directory is None:
        _ask_batch_directory(parent, "dialogs.batch_cbr.select_directory_title",
                             lambda d: batch_convert_cbr_to_cbz(parent, callbacks, d))
        return

    cbr_files = []
    for dirpath, _subdirs, filenames in walk_without_links(directory):
        for fn in filenames:
            if fn.lower().endswith('.cbr'):
                cbr_files.append(os.path.join(dirpath, fn))
    cbr_files.sort(key=lambda f: callbacks['natural_sort_key'](os.path.basename(f).lower()))

    if not cbr_files:
        InfoDialog(parent, "dialogs.batch_cbr.no_cbr_title",
                   lambda d=directory: _("dialogs.batch_cbr.no_cbr_message").format(directory=d)).show_nonmodal()
        return

    batch_convert_cbr_to_cbz_confirm(parent, cbr_files, directory, callbacks)


def batch_convert_cbr_to_cbz_confirm(parent, cbr_files, directory, callbacks, directories=None,
                                     on_finished=None):
    """Fenêtre de confirmation + progression + résumé CBR→CBZ.
    on_finished : voir _finish_batch."""
    cbr_files  = [os.path.normpath(f) for f in cbr_files]
    if _refuse_if_batch_running(parent, cbr_files):
        return _finish_batch(on_finished, None)
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
        files        = cbr_files,
    )

    def _on_confirm_done():
        if not dlg.confirmed:
            return _finish_batch(on_finished, None)
        is_permanent = dlg.permanent_delete
        _run_cbr_conversion(parent, cbr_files, directory, directories, callbacks, is_permanent,
                            on_finished)

    dlg.finished.connect(lambda _: _on_confirm_done())
    dlg.show_nonmodal()


def _run_cbr_conversion(parent, cbr_files, directory, directories, callbacks, is_permanent,
                        on_finished=None):
    claims = _start_batch_claims(parent, cbr_files)
    if claims is None:
        return _finish_batch(on_finished, None)
    new_files = []    # CBZ créés et fichiers renommés, rendus par on_finished
    prog = _ProgressDialog(parent, "dialogs.batch_cbr.converting_title", has_page_bar=True)
    prog.show_nonmodal()
    QApplication.processEvents()

    signals          = _ThreadSignals()
    _register_batch(prog, signals)
    conversion_errors = []
    disk_full         = []    # lot arrêté faute de place (_stop_for_disk_full)
    last_done         = [None]    # dernier fichier fini sans erreur
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
                lambda c=idx + 1, t=total: _("dialogs.batch_cbr.converting_progress").format(current=c, total=t))
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
                    last_done[0] = basename
                    renamed_entries.append(f"{basename} → {os.path.basename(new_path)}")
                    new_files.append(new_path)
                except Exception as e:
                    conversion_errors.append(f"{basename}: {_failure_text(e)}")
                continue

            # Format inconnu
            if real_type is None:
                conversion_errors.append(f"{basename}: " + _("messages.errors.batch_unknown_format"))
                continue

            # Vignette (seulement si vrai RAR) : première page de l'ouverture
            _emit_first_page_thumbnail(cbr_path, signals, callbacks)

            try:
                # Archive rouverte à chaque page lue : son état est relevé
                # avant la lecture, et la conversion abandonnée si un autre
                # programme la réécrit pendant ce temps (check_source_unchanged)
                from modules.qt.utils import file_signature
                source_signature = file_signature(cbr_path)
                # Lecteur commun (archive_members.open_archive) : fichiers dans
                # l'ordre de l'ouverture, chemin complet (trier sur le seul nom
                # de fichier entrelacerait les chapitres, Ch1/001, Ch2/001…),
                # mêmes protections (taille, noms portés par plusieurs fichiers)
                with open_archive(cbr_path, "CBR") as archive:
                    all_files   = archive.files()
                    total_pages = len(all_files)
                    if total_pages == 0:
                        conversion_errors.append(f"{basename}: " + _("messages.errors.batch_empty_archive"))
                        continue

                    base_path, _ext = os.path.splitext(cbr_path)
                    cbz_path = claims.reserve(free_name_candidates(base_path, ".cbz"))

                    from modules.qt.utils import zip_compression_kwargs
                    from modules.qt.config_manager import get_config_manager as _gcm
                    from modules.qt.converted_cbz import check_source_unchanged, delete_source
                    def _on_page(current, total):
                        signals.update_page_bar.emit(
                            current / total * 100,
                            lambda c=current, t=total: _("dialogs.batch_cbr.page_progress").format(
                                current=c, total=t))

                    with _interruptible_zip_writer(
                            cbz_path, needed=estimate_archive_copy_size(all_files, cbr_path),
                            **zip_compression_kwargs(_gcm().get_zip_compression_level())) as cbz:
                        failed_pages = _copy_pages_to_cbz(
                            cbz, all_files, archive.read, lambda m: m.name, _on_page)
                        if failed_pages:
                            raise _IncompleteCbz(failed_pages)
                        check_source_unchanged(cbr_path, source_signature)
                        cbz.expected_count = total_pages
                        # Commentaire d'archive de la source (métadonnées
                        # ComicBookInfo d'un CBR) repris : la source est
                        # supprimée ensuite
                        cbz.comment = archive.comment()

                # Archive RAR fermée (sortie du `with`) : sinon Windows en
                # refuserait la suppression
                gc.collect()
                try:
                    delete_source(cbz, cbr_path, permanent=is_permanent,
                                  source_signature=source_signature)
                except Exception as del_err:
                    conversion_errors.append(
                        f"{basename}: " + _("messages.errors.batch_source_delete_failed", error=_failure_text(del_err)))
                converted_count[0] += 1
                last_done[0] = basename
                new_files.append(cbz_path)

            except _IncompleteCbz as inc:
                _log_incomplete_cbz(basename, inc.failures, conversion_errors)
            except Exception as e:
                if _is_disk_full(e):
                    _stop_for_disk_full(e, basename, total - idx, conversion_errors, disk_full, last_done[0])
                    break
                conversion_errors.append(f"{basename}: {_failure_text(e)}")

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        log_error = None
        if conversion_errors or renamed_entries:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "cbrtocbz", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write(log_title("title_cbr"))
                    lf.write(log_line("date", date=now.strftime('%Y-%m-%d %H:%M')))
                    lf.write(log_line("directory", path=directory))
                    lf.write(log_line("total_files", count=len(cbr_files)))
                    lf.write(log_line("converted", count=converted_count[0]))
                    lf.write(log_line("renamed_kind", kind="ZIP→CBZ", count=renamed_cbz[0]))
                    lf.write(log_line("renamed_kind", kind="7z→CB7", count=renamed_cb7[0]))
                    lf.write(log_line("renamed_kind", kind="TAR→CBT", count=renamed_cbt[0]))
                    lf.write(log_line("errors", count=len(conversion_errors)))
                    if renamed_entries:
                        lf.write(f"\n{'='*60}\n")
                        lf.write(log_line("renamed_files"))
                        lf.write(f"{'='*60}\n\n")
                        for entry in renamed_entries:
                            lf.write(f"  - {log_text(entry)}\n")
                    if conversion_errors:
                        lf.write(f"\n{'='*60}\n")
                        lf.write(log_line("error_details"))
                        lf.write(f"{'='*60}\n\n")
                        for err in conversion_errors:
                            lf.write(f"  - {log_text(err)}\n")
            except Exception as e:
                # Journal impossible à écrire (dossier temporaire plein ou
                # protégé) : la raison est affichée dans le résumé.
                log_path = None
                log_error = exception_text(e)

        summary_data = {
            "converted_count": converted_count[0],
            "total":           len(cbr_files),
            "renamed_cbz":     renamed_cbz[0],
            "renamed_cb7":     renamed_cb7[0],
            "renamed_cbt":     renamed_cbt[0],
            "errors_count":    len(conversion_errors),
            "has_errors":      bool(conversion_errors),
            "disk_full":       disk_full[0] if disk_full else None,
            "directory":       directory,
            "directories":     directories,
            "log_path":        log_path,
            "log_error":       log_error,
        }
        _end_batch(parent, "cbr", show_batch_cbr_summary, summary_data, callbacks,
                   on_finished, new_files)

    signals.conversion_done.connect(on_done)
    _start_batch_thread(do_conversion, parent, claims, prog, signals)


def show_batch_cbr_summary(parent, data, callbacks):
    dlg = _CbrSummaryDialog(parent, data)
    dlg.show_nonmodal()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de résumé CB7
# ═══════════════════════════════════════════════════════════════════════════════

class _Cb7SummaryDialog(AppWindowMixin, QDialog):
    def __init__(self, parent, data):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("dialogs.batch_cb7.complete_title")
        self._data = data
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        top = QVBoxLayout()
        top.setSpacing(0)
        top.setContentsMargins(0, 0, 0, 0)
        self._msg_lbl = QLabel()
        self._msg_lbl.setTextFormat(Qt.PlainText)
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
                lbl.setTextFormat(Qt.PlainText)
                lbl.setWordWrap(True)
                lbl.setAlignment(Qt.AlignCenter)
                layout.addWidget(lbl)
                setattr(self, f"_{key}_lbl", lbl)

        # Lien log (erreurs ou renommages)
        self._error_lbl = None
        log_path = data.get("log_path")
        if data.get("has_errors") or log_path or data.get("log_error"):
            self._error_lbl = QLabel()
            # Lien vers le journal (_log_label_text, qui échappe ce qu'il insère)
            self._error_lbl.setTextFormat(Qt.RichText)
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

        self._connect_language()
        self._ok_btn.setFocus()

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
            see_log = _("dialogs.see_log")
            if data.get("has_errors"):
                error_text = _("dialogs.batch_cb7.errors_count").format(count=data["errors_count"])
                self._error_lbl.setText(_log_label_text(data, f"{error_text} — {see_log}", error_text))
            else:
                self._error_lbl.setText(_log_label_text(data, see_log))
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
        _ask_batch_directory(parent, "dialogs.batch_cb7.select_directory_title",
                             lambda d: batch_convert_cb7_to_cbz(parent, callbacks, d))
        return

    cb7_files = []
    for root, _subdirs, files in walk_without_links(directory):
        for fname in files:
            if fname.lower().endswith('.cb7'):
                cb7_files.append(os.path.join(root, fname))
    cb7_files.sort(key=lambda f: callbacks['natural_sort_key'](os.path.basename(f).lower()))

    if not cb7_files:
        InfoDialog(parent, "dialogs.batch_cb7.no_cb7_title",
                   lambda d=directory: _("dialogs.batch_cb7.no_cb7_message").format(directory=d)).show_nonmodal()
        return

    batch_convert_cb7_to_cbz_confirm(parent, cb7_files, directory, callbacks)


def batch_convert_cb7_to_cbz_confirm(parent, cb7_files, directory, callbacks, directories=None,
                                     on_finished=None):
    """Fenêtre de confirmation + progression + résumé CB7→CBZ.
    on_finished : voir _finish_batch."""
    cb7_files = [os.path.normpath(f) for f in cb7_files]
    if _refuse_if_batch_running(parent, cb7_files):
        return _finish_batch(on_finished, None)
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
        files        = cb7_files,
    )
    def _on_confirm_done_cb7():
        if not dlg.confirmed:
            return _finish_batch(on_finished, None)
        is_permanent = dlg.permanent_delete
        _run_cb7_conversion(parent, cb7_files, directory, directories, callbacks, is_permanent,
                            on_finished)

    dlg.finished.connect(lambda _: _on_confirm_done_cb7())
    dlg.show_nonmodal()


def _run_cb7_conversion(parent, cb7_files, directory, directories, callbacks, is_permanent,
                        on_finished=None):
    claims = _start_batch_claims(parent, cb7_files)
    if claims is None:
        return _finish_batch(on_finished, None)
    new_files = []    # CBZ créés et fichiers renommés, rendus par on_finished
    prog = _ProgressDialog(parent, "dialogs.batch_cb7.converting_title", has_page_bar=True)
    prog.show_nonmodal()
    QApplication.processEvents()

    signals           = _ThreadSignals()
    _register_batch(prog, signals)
    conversion_errors = []
    disk_full         = []    # lot arrêté faute de place (_stop_for_disk_full)
    last_done         = [None]    # dernier fichier fini sans erreur
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
                lambda c=idx + 1, t=total: _("dialogs.batch_cb7.converting_progress").format(current=c, total=t))
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
                    last_done[0] = basename
                    renamed_entries.append(f"{basename} → {os.path.basename(new_path)}")
                    new_files.append(new_path)
                except Exception as e:
                    conversion_errors.append(f"{basename}: {_failure_text(e)}")
                continue

            # Format inconnu
            if real_type is None:
                conversion_errors.append(f"{basename}: " + _("messages.errors.batch_unknown_format"))
                continue

            # Vignette : première page de l'ouverture
            _emit_first_page_thumbnail(cb7_path, signals, callbacks)

            try:
                # Archive rouverte à chaque page lue : voir le lot CBR
                from modules.qt.utils import file_signature
                source_signature = file_signature(cb7_path)
                # Lecteur commun : fichiers dans l'ordre de l'ouverture, chemin
                # complet (voir le lot CBR), mêmes protections
                with open_archive(cb7_path, "CB7") as archive:
                    all_files = archive.files()
                    total_pages = len(all_files)
                    if total_pages == 0:
                        conversion_errors.append(f"{basename}: " + _("messages.errors.batch_empty_archive"))
                        continue

                    base_path, _ext = os.path.splitext(cb7_path)
                    cbz_path = claims.reserve(free_name_candidates(base_path, ".cbz"))

                    from modules.qt.utils import zip_compression_kwargs
                    from modules.qt.config_manager import get_config_manager as _gcm
                    from modules.qt.converted_cbz import check_source_unchanged, delete_source
                    def _on_page(current, total):
                        signals.update_page_bar.emit(
                            current / total * 100,
                            lambda c=current, t=total: _("dialogs.batch_cb7.page_progress").format(
                                current=c, total=t))

                    with _interruptible_zip_writer(
                            cbz_path, needed=estimate_archive_copy_size(all_files, cb7_path),
                            **zip_compression_kwargs(_gcm().get_zip_compression_level())) as cbz:
                        failed_pages = _copy_pages_to_cbz(
                            cbz, all_files, archive.read, lambda m: m.name, _on_page)
                        if failed_pages:
                            raise _IncompleteCbz(failed_pages)
                        check_source_unchanged(cb7_path, source_signature)
                        cbz.expected_count = total_pages
                        # Commentaire d'archive de la source (métadonnées
                        # ComicBookInfo d'un CBR) repris : la source est
                        # supprimée ensuite
                        cbz.comment = archive.comment()

                gc.collect()
                try:
                    delete_source(cbz, cb7_path, permanent=is_permanent,
                                  source_signature=source_signature)
                except Exception as del_err:
                    conversion_errors.append(
                        f"{basename}: " + _("messages.errors.batch_source_delete_failed", error=_failure_text(del_err)))
                converted_count[0] += 1
                last_done[0] = basename
                new_files.append(cbz_path)

            except _IncompleteCbz as inc:
                _log_incomplete_cbz(basename, inc.failures, conversion_errors)
            except Exception as e:
                if _is_disk_full(e):
                    _stop_for_disk_full(e, basename, total - idx, conversion_errors, disk_full, last_done[0])
                    break
                conversion_errors.append(f"{basename}: {_failure_text(e)}")

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        log_error = None
        if conversion_errors or renamed_entries:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "cb7tocbz", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write(log_title("title_cb7"))
                    lf.write(log_line("date", date=now.strftime('%Y-%m-%d %H:%M')))
                    lf.write(log_line("directory", path=directory))
                    lf.write(log_line("total_files", count=len(cb7_files)))
                    lf.write(log_line("converted", count=converted_count[0]))
                    lf.write(log_line("renamed_kind", kind="ZIP→CBZ", count=renamed_cbz[0]))
                    lf.write(log_line("renamed_kind", kind="RAR→CBR", count=renamed_cbr[0]))
                    lf.write(log_line("renamed_kind", kind="TAR→CBT", count=renamed_cbt[0]))
                    lf.write(log_line("errors", count=len(conversion_errors)))
                    if renamed_entries:
                        lf.write(f"\n{'='*60}\n")
                        lf.write(log_line("renamed_files"))
                        lf.write(f"{'='*60}\n\n")
                        for entry in renamed_entries:
                            lf.write(f"  - {log_text(entry)}\n")
                    if conversion_errors:
                        lf.write(f"\n{'='*60}\n")
                        lf.write(log_line("error_details"))
                        lf.write(f"{'='*60}\n\n")
                        for err in conversion_errors:
                            lf.write(f"  - {log_text(err)}\n")
            except Exception as e:
                # Journal impossible à écrire (dossier temporaire plein ou
                # protégé) : la raison est affichée dans le résumé.
                log_path = None
                log_error = exception_text(e)

        summary_data = {
            "converted_count": converted_count[0],
            "total":           len(cb7_files),
            "renamed_cbz":     renamed_cbz[0],
            "renamed_cbr":     renamed_cbr[0],
            "renamed_cbt":     renamed_cbt[0],
            "errors_count":    len(conversion_errors),
            "has_errors":      bool(conversion_errors),
            "disk_full":       disk_full[0] if disk_full else None,
            "directory":       directory,
            "directories":     directories,
            "log_path":        log_path,
            "log_error":       log_error,
        }
        _end_batch(parent, "cb7", show_batch_cb7_summary, summary_data, callbacks,
                   on_finished, new_files)

    signals.conversion_done.connect(on_done)
    _start_batch_thread(do_conversion, parent, claims, prog, signals)


def show_batch_cb7_summary(parent, data, callbacks):
    dlg = _Cb7SummaryDialog(parent, data)
    dlg.show_nonmodal()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog de résumé CBT
# ═══════════════════════════════════════════════════════════════════════════════

class _CbtSummaryDialog(AppWindowMixin, QDialog):
    def __init__(self, parent, data):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("dialogs.batch_cbt.complete_title")
        self._data = data
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        top = QVBoxLayout()
        top.setSpacing(0)
        top.setContentsMargins(0, 0, 0, 0)
        self._msg_lbl = QLabel()
        self._msg_lbl.setTextFormat(Qt.PlainText)
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
                lbl.setTextFormat(Qt.PlainText)
                lbl.setWordWrap(True)
                lbl.setAlignment(Qt.AlignCenter)
                layout.addWidget(lbl)
                setattr(self, f"_{key}_lbl", lbl)

        # Lien log (erreurs ou renommages)
        self._error_lbl = None
        log_path = data.get("log_path")
        if data.get("has_errors") or log_path or data.get("log_error"):
            self._error_lbl = QLabel()
            # Lien vers le journal (_log_label_text, qui échappe ce qu'il insère)
            self._error_lbl.setTextFormat(Qt.RichText)
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

        self._connect_language()
        self._ok_btn.setFocus()

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
            see_log = _("dialogs.see_log")
            if data.get("has_errors"):
                error_text = _("dialogs.batch_cbt.errors_count").format(count=data["errors_count"])
                self._error_lbl.setText(_log_label_text(data, f"{error_text} — {see_log}", error_text))
            else:
                self._error_lbl.setText(_log_label_text(data, see_log))
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
        _ask_batch_directory(parent, "dialogs.batch_cbt.select_directory_title",
                             lambda d: batch_convert_cbt_to_cbz(parent, callbacks, d))
        return

    cbt_files = []
    for root, _subdirs, files in walk_without_links(directory):
        for fname in files:
            if fname.lower().endswith('.cbt'):
                cbt_files.append(os.path.join(root, fname))
    cbt_files.sort(key=lambda f: callbacks['natural_sort_key'](os.path.basename(f).lower()))

    if not cbt_files:
        InfoDialog(parent, "dialogs.batch_cbt.no_cbt_title",
                   lambda d=directory: _("dialogs.batch_cbt.no_cbt_message").format(directory=d)).show_nonmodal()
        return

    batch_convert_cbt_to_cbz_confirm(parent, cbt_files, directory, callbacks)


def batch_convert_cbt_to_cbz_confirm(parent, cbt_files, directory, callbacks, directories=None,
                                     on_finished=None):
    """Fenêtre de confirmation + progression + résumé CBT→CBZ.
    on_finished : voir _finish_batch."""
    cbt_files = [os.path.normpath(f) for f in cbt_files]
    if _refuse_if_batch_running(parent, cbt_files):
        return _finish_batch(on_finished, None)
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
        files        = cbt_files,
    )
    def _on_confirm_done_cbt():
        if not dlg.confirmed:
            return _finish_batch(on_finished, None)
        is_permanent = dlg.permanent_delete
        _run_cbt_conversion(parent, cbt_files, directory, directories, callbacks, is_permanent,
                            on_finished)

    dlg.finished.connect(lambda _: _on_confirm_done_cbt())
    dlg.show_nonmodal()


def _run_cbt_conversion(parent, cbt_files, directory, directories, callbacks, is_permanent,
                        on_finished=None):
    claims = _start_batch_claims(parent, cbt_files)
    if claims is None:
        return _finish_batch(on_finished, None)
    new_files = []    # CBZ créés et fichiers renommés, rendus par on_finished
    prog = _ProgressDialog(parent, "dialogs.batch_cbt.converting_title", has_page_bar=True)
    prog.show_nonmodal()
    QApplication.processEvents()

    signals           = _ThreadSignals()
    _register_batch(prog, signals)
    conversion_errors = []
    disk_full         = []    # lot arrêté faute de place (_stop_for_disk_full)
    last_done         = [None]    # dernier fichier fini sans erreur
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
                lambda c=idx + 1, t=total: _("dialogs.batch_cbt.converting_progress").format(current=c, total=t))
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
                    last_done[0] = basename
                    renamed_entries.append(f"{basename} → {os.path.basename(new_path)}")
                    new_files.append(new_path)
                except Exception as e:
                    conversion_errors.append(f"{basename}: {_failure_text(e)}")
                continue

            # Format inconnu
            if real_type is None:
                conversion_errors.append(f"{basename}: " + _("messages.errors.batch_unknown_format"))
                continue

            # Vignette : première page de l'ouverture
            _emit_first_page_thumbnail(cbt_path, signals, callbacks)

            try:
                # Archive ouverte en lecture, mais qu'un autre programme peut
                # réécrire sur place : son état est relevé avant la lecture
                # (voir le lot CBR, check_source_unchanged)
                from modules.qt.utils import file_signature
                source_signature = file_signature(cbt_path)
                # Lecteur commun : fichiers seuls (ni lien symbolique ni lien
                # physique), noms normalisés comme à l'ouverture d'un CBT (sans
                # « ./ », relus dans leur codage), dans l'ordre de l'ouverture
                with open_archive(cbt_path, "CBT") as archive:
                    all_members = archive.files()
                    total_pages = len(all_members)
                    if total_pages == 0:
                        conversion_errors.append(f"{basename}: " + _("messages.errors.batch_empty_archive"))
                        continue

                    base_path, _ext = os.path.splitext(cbt_path)
                    cbz_path = claims.reserve(free_name_candidates(base_path, ".cbz"))

                    from modules.qt.utils import zip_compression_kwargs
                    from modules.qt.config_manager import get_config_manager as _gcm
                    from modules.qt.converted_cbz import check_source_unchanged, delete_source
                    def _on_page(current, total):
                        signals.update_page_bar.emit(
                            current / total * 100,
                            lambda c=current, t=total: _("dialogs.batch_cbt.page_progress").format(
                                current=c, total=t))

                    with _interruptible_zip_writer(
                            cbz_path, needed=estimate_archive_copy_size(all_members, cbt_path),
                            **zip_compression_kwargs(_gcm().get_zip_compression_level())) as cbz:
                        failed_pages = _copy_pages_to_cbz(
                            cbz, all_members, archive.read, lambda m: m.name, _on_page)
                        if failed_pages:
                            raise _IncompleteCbz(failed_pages)
                        check_source_unchanged(cbt_path, source_signature)
                        cbz.expected_count = total_pages
                        # Commentaire d'archive de la source (métadonnées
                        # ComicBookInfo d'un CBR) repris : la source est
                        # supprimée ensuite
                        cbz.comment = archive.comment()

                # Archive TAR fermée (sortie du `with`) : sinon Windows en
                # refuserait la suppression
                gc.collect()
                try:
                    delete_source(cbz, cbt_path, permanent=is_permanent,
                                  source_signature=source_signature)
                except Exception as del_err:
                    conversion_errors.append(
                        f"{basename}: " + _("messages.errors.batch_source_delete_failed", error=_failure_text(del_err)))
                converted_count[0] += 1
                last_done[0] = basename
                new_files.append(cbz_path)

            except _IncompleteCbz as inc:
                _log_incomplete_cbz(basename, inc.failures, conversion_errors)
            except Exception as e:
                if _is_disk_full(e):
                    _stop_for_disk_full(e, basename, total - idx, conversion_errors, disk_full, last_done[0])
                    break
                conversion_errors.append(f"{basename}: {_failure_text(e)}")

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        log_error = None
        if conversion_errors or renamed_entries:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "cbttocbz", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write(log_title("title_cbt"))
                    lf.write(log_line("date", date=now.strftime('%Y-%m-%d %H:%M')))
                    lf.write(log_line("directory", path=directory))
                    lf.write(log_line("total_files", count=len(cbt_files)))
                    lf.write(log_line("converted", count=converted_count[0]))
                    lf.write(log_line("renamed_kind", kind="ZIP→CBZ", count=renamed_cbz[0]))
                    lf.write(log_line("renamed_kind", kind="RAR→CBR", count=renamed_cbr[0]))
                    lf.write(log_line("renamed_kind", kind="7z→CB7", count=renamed_cb7[0]))
                    lf.write(log_line("errors", count=len(conversion_errors)))
                    if renamed_entries:
                        lf.write(f"\n{'='*60}\n")
                        lf.write(log_line("renamed_files"))
                        lf.write(f"{'='*60}\n\n")
                        for entry in renamed_entries:
                            lf.write(f"  - {log_text(entry)}\n")
                    if conversion_errors:
                        lf.write(f"\n{'='*60}\n")
                        lf.write(log_line("error_details"))
                        lf.write(f"{'='*60}\n\n")
                        for err in conversion_errors:
                            lf.write(f"  - {log_text(err)}\n")
            except Exception as e:
                # Journal impossible à écrire (dossier temporaire plein ou
                # protégé) : la raison est affichée dans le résumé.
                log_path = None
                log_error = exception_text(e)

        summary_data = {
            "converted_count": converted_count[0],
            "total":           len(cbt_files),
            "renamed_cbz":     renamed_cbz[0],
            "renamed_cbr":     renamed_cbr[0],
            "renamed_cb7":     renamed_cb7[0],
            "errors_count":    len(conversion_errors),
            "has_errors":      bool(conversion_errors),
            "disk_full":       disk_full[0] if disk_full else None,
            "directory":       directory,
            "directories":     directories,
            "log_path":        log_path,
            "log_error":       log_error,
        }
        _end_batch(parent, "cbt", show_batch_cbt_summary, summary_data, callbacks,
                   on_finished, new_files)

    signals.conversion_done.connect(on_done)
    _start_batch_thread(do_conversion, parent, claims, prog, signals)


def show_batch_cbt_summary(parent, data, callbacks):
    dlg = _CbtSummaryDialog(parent, data)
    dlg.show_nonmodal()


# ═══════════════════════════════════════════════════════════════════════════════
# Fonctions publiques — PDF→CBZ
# ═══════════════════════════════════════════════════════════════════════════════

def batch_convert_pdf_to_cbz(parent, callbacks, directory=None):
    """Lance la conversion batch PDF→CBZ."""
    if not PDF_AVAILABLE:
        ErrorDialog(parent,
                    "dialogs.batch_pdf.pymupdf_required_title",
                    lambda: _("dialogs.batch_pdf.pymupdf_required_message")).show_nonmodal()
        return

    if directory is None:
        _ask_batch_directory(parent, "dialogs.batch_pdf.select_directory_title",
                             lambda d: batch_convert_pdf_to_cbz(parent, callbacks, d))
        return

    pdf_files = []
    for dirpath, _subdirs, filenames in walk_without_links(directory):
        for fn in filenames:
            if fn.lower().endswith('.pdf'):
                pdf_files.append(os.path.join(dirpath, fn))
    pdf_files.sort(key=lambda f: callbacks['natural_sort_key'](os.path.basename(f).lower()))

    if not pdf_files:
        InfoDialog(parent, "dialogs.batch_pdf.no_pdf_title",
                   lambda d=directory: _("dialogs.batch_pdf.no_pdf_message").format(directory=d)).show_nonmodal()
        return

    batch_convert_pdf_to_cbz_confirm(parent, pdf_files, directory, callbacks)


def batch_convert_pdf_to_cbz_confirm(parent, pdf_files, directory, callbacks, directories=None,
                                     on_finished=None):
    """Fenêtre de confirmation + progression + résumé PDF→CBZ.
    on_finished : voir _finish_batch."""
    pdf_files  = [os.path.normpath(f) for f in pdf_files]
    if _refuse_if_batch_running(parent, pdf_files):
        return _finish_batch(on_finished, None)
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
        files        = pdf_files,
    )
    def _on_confirm_done_pdf():
        if not dlg.confirmed:
            return _finish_batch(on_finished, None)
        is_permanent = dlg.permanent_delete
        _run_pdf_conversion(parent, pdf_files, directory, directories, callbacks, is_permanent, state,
                            on_finished)

    dlg.finished.connect(lambda _: _on_confirm_done_pdf())
    dlg.show_nonmodal()


def _run_pdf_conversion(parent, pdf_files, directory, directories, callbacks, is_permanent, state,
                        on_finished=None, kept=None):
    """kept : dict ou None. Donné, les PDF ne sont pas supprimés : chaque CBZ
    créé dont la source peut l'être y est noté, {chemin du CBZ: (nombre de
    fichiers, utils.file_signature juste après l'écriture)}, pour la question
    « Supprimer l'original ? » de l'appelant (convert_single_file_to_cbz)."""
    claims = _start_batch_claims(parent, pdf_files)
    if claims is None:
        return _finish_batch(on_finished, None)
    new_files = []    # CBZ créés, rendus par on_finished
    prog = _ProgressDialog(parent, "dialogs.batch_pdf.converting_title", has_page_bar=True)
    prog.show_nonmodal()
    QApplication.processEvents()

    signals           = _ThreadSignals()
    _register_batch(prog, signals)
    conversion_errors = []
    disk_full         = []    # lot arrêté faute de place (_stop_for_disk_full)
    last_done         = [None]    # dernier fichier fini sans erreur
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
            """Attend un message du process : None si le délai est dépassé,
            ('process_stopped',) si le process est mort (plantage natif de
            MuPDF sur un PDF malformé), par la lecture commune
            (pdf_loading_qt.read_pdf_message) : non rattrapée, l'erreur du
            canal fermé arrêterait le thread du lot sans résumé ni fermeture
            de sa fenêtre."""
            return _pdfmod.read_pdf_message(proc[2], proc[0], timeout)

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
            # Moteur remplacé entre deux fichiers, tous les
            # _PDF_FILES_PER_ENGINE PDF : gardé pour tout le lot, sa mémoire
            # augmente un peu à chaque PDF (environ 0,1 Mo), sans limite sur un
            # lot de milliers de fichiers
            if idx and idx % _PDF_FILES_PER_ENGINE == 0:
                _restart_process()
            basename = os.path.basename(pdf_path)
            signals.update_filename.emit(basename)
            signals.update_progress.emit(
                lambda c=idx + 1, t=total: _("dialogs.batch_pdf.converting_progress").format(current=c, total=t))
            signals.update_page_bar.emit(0.0, "")

            # Fichier et nom du CBZ réservés pour ce fichier seulement
            claims.release_all()
            if not _claim_file(claims, pdf_path, conversion_errors):
                continue

            # Le PDF, ouvert par le process, est lu page par page : son état
            # est relevé avant (voir le lot CBR, check_source_unchanged)
            from modules.qt.utils import file_signature
            source_signature = file_signature(pdf_path)
            _send(('batch_open', pdf_path))

            # Attend batch_ready
            batch_ready = None
            while True:
                msg = _recv(timeout=30)
                if msg is None:
                    break
                if msg[0] == '_debug':
                    continue
                if msg[0] in ('batch_ready', 'error', 'process_stopped'):
                    batch_ready = msg
                    break

            if batch_ready is None or batch_ready[0] in ('error', 'process_stopped'):
                if batch_ready is None:
                    err = _("messages.errors.pdf_failure.no_response")
                elif batch_ready[0] == 'process_stopped':
                    err = _("messages.errors.pdf_failure.process_stopped")
                else:
                    err = batch_ready[1]
                conversion_errors.append(f"{basename}: {err}")
                # Process sans réponse ou mort : remplacé pour le PDF suivant
                if batch_ready is None or batch_ready[0] == 'process_stopped':
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
            from modules.qt.converted_cbz import (SourceChangedError, check_source_unchanged,
                                                  delete_source)
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
                                lambda c=cur, t=tot: _("dialogs.batch_pdf.page_progress").format(
                                    current=c, total=t))
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
                        elif msg[0] == 'process_stopped':
                            conversion_errors.append(
                                f"{basename}: " + _("messages.errors.pdf_failure.process_stopped"))
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
                    check_source_unchanged(pdf_path, source_signature)
                    cbz.expected_count = pages_written
            except _IncompleteCbz:
                if process_failed:
                    _restart_process()
                continue
            except (WrittenFileCheckError, SourceChangedError) as e:
                # CBZ qui ne se relit pas, ou PDF réécrit pendant sa lecture :
                # jamais créé sous son nom, PDF gardé ; le process PDF, lui, a
                # fini son document normalement.
                conversion_errors.append(f"{basename}: {_failure_text(e)}")
                continue
            except Exception as e:
                # Le CBZ en cours d'écriture n'a jamais pris son nom final
                # (_interruptible_zip_writer) : rien à supprimer.
                if _is_disk_full(e):
                    # Taille des pages rendues inconnue d'avance : pas de
                    # vérification avant d'écrire, seulement le disque plein
                    # pendant l'écriture. Process arrêté par do_conversion.
                    _stop_for_disk_full(e, basename, total - idx, conversion_errors, disk_full, last_done[0])
                    break
                conversion_errors.append(f"{basename}: {_failure_text(e)}")
                _restart_process()
                continue

            gc.collect()

            if kept is not None:
                # Source gardée (PDF seul de la Bibliothèque) : « Supprimer
                # l'original ? » posée ensuite par l'appelant. Un PDF protégé
                # par un mot de passe propriétaire n'est jamais supprimé, comme
                # dans le lot : absent de `kept`, aucune question.
                if pdf_path not in owner_protected:
                    kept[cbz_path] = (cbz.count_to_check(), file_signature(cbz_path))
            elif pdf_path not in owner_protected:
                try:
                    delete_source(cbz, pdf_path, permanent=is_permanent,
                                  source_signature=source_signature)
                except Exception as del_err:
                    conversion_errors.append(
                        f"{basename}: " + _("messages.errors.batch_source_delete_failed", error=_failure_text(del_err)))

            converted_count[0] += 1
            last_done[0] = basename
            new_files.append(cbz_path)

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        log_error = None
        if conversion_errors:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "pdftocbz", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write(log_title("title_pdf"))
                    lf.write(log_line("date", date=now.strftime('%Y-%m-%d %H:%M')))
                    lf.write(log_line("directory", path=directory))
                    lf.write(log_line("total_files", count=len(pdf_files)))
                    lf.write(log_line("converted", count=converted_count[0]))
                    lf.write(log_line("errors", count=len(conversion_errors)))
                    lf.write(f"\n{'='*60}\n")
                    lf.write(log_line("error_details"))
                    lf.write(f"{'='*60}\n\n")
                    for err in conversion_errors:
                        lf.write(f"  - {log_text(err)}\n")
            except Exception as e:
                # Journal impossible à écrire (dossier temporaire plein ou
                # protégé) : la raison est affichée dans le résumé.
                log_path = None
                log_error = exception_text(e)

        summary_data = {
            "converted_count":     converted_count[0],
            "total":               len(pdf_files),
            "errors_count":        len(conversion_errors),
            "has_errors":          bool(conversion_errors),
            "disk_full":           disk_full[0] if disk_full else None,
            "directory":           directory,
            "directories":         directories,
            "log_path":            log_path,
            "log_error":           log_error,
        }
        _end_batch(parent, "pdf", show_batch_pdf_summary, summary_data, callbacks,
                   on_finished, new_files)

    signals.conversion_done.connect(on_done)
    _start_batch_thread(do_conversion, parent, claims, prog, signals)


def show_batch_pdf_summary(parent, data, callbacks):
    dlg = _PdfSummaryDialog(parent, data, callbacks)
    dlg.show_nonmodal()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialog choix de mode IMG→CBZ
# ═══════════════════════════════════════════════════════════════════════════════

class _ImgModeDialog(AppWindowMixin, QDialog):
    """Demande à l'utilisateur le mode de regroupement : une image/CBZ ou tout en un."""

    MODE_ONE_PER_IMAGE = "one_per_image"
    MODE_ALL_IN_ONE    = "all_in_one"

    def __init__(self, parent):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("dialogs.batch_img.mode_title")
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
        self._connect_language()
        self._ok_btn.setFocus()

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
        if directory is None:
            _ask_batch_directory(parent, "dialogs.batch_img.select_directory_title",
                                 lambda d: _convert_images_in(d, chosen_mode))
        else:
            _convert_images_in(directory, chosen_mode)

    def _convert_images_in(actual_directory, chosen_mode):
        img_files = []
        for dirpath, _subdirs, filenames in walk_without_links(actual_directory):
            for fn in filenames:
                if fn.lower().endswith(IMAGE_EXTS):
                    img_files.append(os.path.join(dirpath, fn))
        img_files.sort(key=lambda f: callbacks['natural_sort_key'](os.path.basename(f).lower()))

        if not img_files:
            InfoDialog(parent, "dialogs.batch_img.no_img_title",
                       lambda d=actual_directory: _("dialogs.batch_img.no_img_message").format(directory=d)).show_nonmodal()
            return

        if chosen_mode == _ImgModeDialog.MODE_ONE_PER_IMAGE:
            batch_convert_img_to_cbz_confirm(parent, img_files, actual_directory, callbacks)
        else:
            batch_convert_imgs_to_single_cbz(parent, img_files, actual_directory, callbacks)

    mode_dlg.finished.connect(lambda _: _on_mode_done())
    mode_dlg.show_nonmodal()


class _ChainSummaryDialog(AppWindowMixin, QDialog):
    """Résumé unique des lots enchaînés par la Bibliothèque (« Convertir en
    CBZ » sur plusieurs fichiers) : une section par lot, avec ce que montre
    son résumé habituel (nombre de fichiers convertis, dossiers, renommages,
    détail par extension des images, erreurs et lien vers son journal), et
    les mêmes textes. summaries : [(type, données du résumé)], type parmi
    cbr, cb7, cbt, pdf, img."""

    def __init__(self, parent, summaries):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("dialogs.batch_cbr.complete_title")
        self._sections = []
        self.setFixedWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)
        for number, (kind, data) in enumerate(summaries):
            if number:
                sep = QFrame()
                sep.setFrameShape(QFrame.HLine)
                layout.addWidget(sep)
            section = {"kind": kind, "data": data, "extra": [], "error": None}
            msg = QLabel()
            msg.setTextFormat(Qt.PlainText)
            msg.setWordWrap(True)
            msg.setAlignment(Qt.AlignCenter)
            layout.addWidget(msg)
            section["msg"] = msg
            # Renommages (lots d'archives) et détail par extension (images)
            for _line in self._extra_lines(kind, data):
                lbl = QLabel()
                lbl.setTextFormat(Qt.PlainText)
                lbl.setWordWrap(True)
                lbl.setAlignment(Qt.AlignCenter)
                layout.addWidget(lbl)
                section["extra"].append(lbl)
            if data.get("has_errors") or data.get("log_path") or data.get("log_error"):
                lbl = QLabel()
                lbl.setTextFormat(Qt.PlainText)
                lbl.setWordWrap(True)
                lbl.setAlignment(Qt.AlignCenter)
                lbl.setStyleSheet("color: #4A9EFF;")
                log_path = data.get("log_path")
                if log_path:
                    lbl.setCursor(QCursor(Qt.PointingHandCursor))
                    lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
                    lbl.linkActivated.connect(lambda _, p=log_path: _open_path(p))
                    from modules.qt.utils import setup_path_label_context_menu
                    setup_path_label_context_menu(lbl, lambda p=log_path: p,
                                                  lambda p=log_path: _open_path(p))
                layout.addWidget(lbl)
                section["error"] = lbl
            self._sections.append(section)

        # Dossiers des lots, une seule fois chacun sous toutes les sections :
        # les lots d'une même sélection travaillent souvent dans le même dossier
        directories = self._directories(summaries)
        if directories:
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            layout.addWidget(sep)
            _add_dir_links(layout, {"directories": directories})

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ok_btn = QPushButton()
        self._ok_btn.setFixedWidth(100)
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._ok_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._connect_language()
        self._ok_btn.setFocus()

    @staticmethod
    def _directories(summaries):
        """Dossiers de tous les lots, sans doublon (casse et séparateurs de
        Windows ignorés), dans l'ordre des lots."""
        seen, result = set(), []
        for _kind, data in summaries:
            for directory in data.get("directories") or [data.get("directory")]:
                if not directory:
                    continue
                key = os.path.normcase(os.path.normpath(directory))
                if key not in seen:
                    seen.add(key)
                    result.append(directory)
        return result

    @staticmethod
    def _extra_lines(kind, data):
        """Lignes sous le message d'un lot, traduites à l'affichage :
        renommages (dialogs.batch_<type>.renamed_<format>_count), détail par
        extension des images (dialogs.batch_img.converted_by_ext)."""
        if kind == "img":
            by_ext = data.get("converted_by_ext") or {}
            return [_("dialogs.batch_img.converted_by_ext").format(
                        ext=ext.lstrip('.').upper(), count=by_ext[ext])
                    for ext in sorted(by_ext)]
        return [_(f"dialogs.batch_{kind}.{key}_count").format(count=data[key])
                for key in ("renamed_cbz", "renamed_cbr", "renamed_cb7", "renamed_cbt")
                if data.get(key, 0) > 0]

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
                           f"QFrame[frameShape='4'] {{ color: {theme['separator']}; }}")
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        see_log = _("dialogs.see_log")
        for section in self._sections:
            kind, data = section["kind"], section["data"]
            renamed = any(data.get(k, 0) > 0
                          for k in ("renamed_cbz", "renamed_cbr", "renamed_cb7", "renamed_cbt"))
            if data.get("has_errors") or renamed:
                text = _(f"dialogs.batch_{kind}.complete_message_errors").format(
                    count=data["converted_count"], total=data["total"])
            else:
                text = _(f"dialogs.batch_{kind}.complete_message").format(
                    count=data["converted_count"])
            section["msg"].setText(text)
            section["msg"].setFont(font)
            for lbl, line in zip(section["extra"], self._extra_lines(kind, data)):
                lbl.setText(line)
                lbl.setFont(font)
            if section["error"] is not None:
                if data.get("has_errors"):
                    error_text = _(f"dialogs.batch_{kind}.errors_count").format(
                        count=data["errors_count"])
                    section["error"].setText(
                        _log_label_text(data, f"{error_text} — {see_log}", error_text))
                else:
                    section["error"].setText(_log_label_text(data, see_log))
                section["error"].setFont(font)
        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)


def show_chain_summary(parent, summaries):
    """Résumé unique d'une chaîne de lots (_ChainSummaryDialog)."""
    if summaries:
        _ChainSummaryDialog(parent, summaries).show_nonmodal()


def batch_convert_image_list(parent, img_files, directory, callbacks, on_finished=None):
    """Images choisies une à une (Bibliothèque) : même question de mode que
    le lot (_ImgModeDialog : un CBZ par image, ou toutes dans un seul CBZ
    écrit dans `directory`), puis le lot correspondant. on_finished : voir
    _finish_batch (None si la question est annulée)."""
    mode_dlg = _ImgModeDialog(parent)

    def _on_mode_done():
        if mode_dlg.chosen_mode is None:
            return _finish_batch(on_finished, None)
        if mode_dlg.chosen_mode == _ImgModeDialog.MODE_ONE_PER_IMAGE:
            batch_convert_img_to_cbz_confirm(parent, img_files, directory, callbacks,
                                             on_finished=on_finished)
        else:
            batch_convert_imgs_to_single_cbz(parent, img_files, directory, callbacks,
                                             on_finished=on_finished)

    mode_dlg.finished.connect(lambda _: _on_mode_done())
    mode_dlg.show_nonmodal()


def convert_single_file_to_cbz(parent, path, callbacks, kept, on_finished=None):
    """Un PDF ou une image seul (Bibliothèque) : converti en CBZ par le lot de
    son type (même lecture, pages non modifiées, progression, journal), sans
    fenêtre de confirmation et sans supprimer la source. Les CBZ dont la
    source peut être supprimée sont notés dans `kept` (voir
    _run_pdf_conversion) : l'appelant pose ensuite la question « Supprimer
    l'original ? ». on_finished : voir _finish_batch ; le résumé n'est pas
    affiché, l'appelant le montre en cas d'échec."""
    path = os.path.normpath(path)
    if _refuse_if_batch_running(parent, [path]):
        return _finish_batch(on_finished, None)
    directory = os.path.dirname(path)
    if os.path.splitext(path)[1].lower() == '.pdf':
        from modules.qt import state as _state_module
        state = callbacks.get('state') or _state_module.state
        _run_pdf_conversion(parent, [path], directory, None, callbacks, False, state,
                            on_finished, kept=kept)
    else:
        _run_img_conversion(parent, [path], directory, None, callbacks, False,
                            on_finished, kept=kept)


def batch_convert_img_to_cbz_confirm(parent, img_files, directory, callbacks, directories=None,
                                     on_finished=None):
    """Fenêtre de confirmation + progression + résumé IMG→CBZ.
    on_finished : voir _finish_batch."""
    img_files  = [os.path.normpath(f) for f in img_files]
    if _refuse_if_batch_running(parent, img_files):
        return _finish_batch(on_finished, None)
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
        files        = img_files,
    )
    def _on_confirm_done_img():
        if not dlg.confirmed:
            return _finish_batch(on_finished, None)
        is_permanent = dlg.permanent_delete
        _run_img_conversion(parent, img_files, directory, directories, callbacks, is_permanent,
                            on_finished)

    dlg.finished.connect(lambda _: _on_confirm_done_img())
    dlg.show_nonmodal()


def _run_img_conversion(parent, img_files, directory, directories, callbacks, is_permanent,
                        on_finished=None, kept=None):
    """kept : voir _run_pdf_conversion."""
    claims = _start_batch_claims(parent, img_files)
    if claims is None:
        return _finish_batch(on_finished, None)
    new_files = []    # CBZ créés, rendus par on_finished
    prog = _ProgressDialog(parent, "dialogs.batch_img.converting_title", has_page_bar=False)
    prog.show_nonmodal()
    QApplication.processEvents()

    signals           = _ThreadSignals()
    _register_batch(prog, signals)
    conversion_errors = []
    disk_full         = []    # lot arrêté faute de place (_stop_for_disk_full)
    last_done         = [None]    # dernier fichier fini sans erreur
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
                lambda c=idx + 1, t=total: _("dialogs.batch_img.converting_progress").format(current=c, total=t))

            # Fichier et nom du CBZ réservés pour ce fichier seulement
            claims.release_all()
            if not _claim_file(claims, img_path, conversion_errors):
                continue

            # Vignette (taille annoncée impossible refusée avant décodage)
            try:
                from modules.qt.entries import check_announced_size
                img = Image.open(img_path)
                check_announced_size(img, os.path.getsize(img_path))
                px  = _pil_to_qimage(img, 150, 210, callbacks)
                signals.update_thumb.emit(px)
                img = None
            except Exception:
                signals.update_thumb.emit(None)

            try:
                # Image réécrite par un autre programme après sa lecture :
                # jamais supprimée (delete_source compare son état à celui-ci)
                from modules.qt.utils import file_signature
                source_signature = file_signature(img_path)
                with open(img_path, 'rb') as f:
                    img_data = f.read()

                try:
                    # Taille annoncée impossible refusée avant décodage
                    from modules.qt.entries import open_checked_image
                    tmp = open_checked_image(img_data)
                    tmp.load()
                except Exception as val_err:
                    raise _UnusableImage(_("messages.errors.batch_image_unreadable",
                                           error=exception_text(val_err)))

                if tmp.format == 'ICO' or ext_lower == '.ico':
                    raise _UnusableImage(_("messages.errors.batch_image_ico"))

                # Un JPEG MPO (photo de téléphone avec une image annexe :
                # profondeur, aperçu) a plusieurs images pour Pillow, mais
                # c'est une photo ordinaire, que les lecteurs affichent
                n_frames = 1 if tmp.format == 'MPO' else getattr(tmp, 'n_frames', 1)
                if n_frames > 1:
                    fmt = tmp.format or ext_lower.lstrip('.').upper()
                    raise _UnusableImage(_("messages.errors.batch_image_multiframe",
                                           format=fmt, count=n_frames))

                # Nom de la page dans le CBZ avec l'extension du vrai format
                from modules.qt.archive_names import name_for_real_format
                page_name = name_for_real_format(basename, tmp.format)
                img_data = _normalized_page_bytes(img_data, tmp,
                                                  os.path.splitext(page_name)[1].lower())
                tmp = None

                cbz_path = claims.reserve(
                    free_name_candidates(base_path, ".cbz", "{base}_{n:02d}{ext}"))

                from modules.qt.utils import estimate_cbz_size, zip_compression_kwargs
                from modules.qt.config_manager import get_config_manager as _gcm
                from modules.qt.converted_cbz import delete_source
                with _interruptible_zip_writer(
                        cbz_path,
                        needed=estimate_cbz_size([{"orig_name": page_name, "bytes": img_data}]),
                        **zip_compression_kwargs(_gcm().get_zip_compression_level())) as cbz:
                    cbz.writestr(page_name, img_data)
                    cbz.expected_count = 1

                img_data = None
                gc.collect()

                if kept is not None:
                    # Source gardée (image seule de la Bibliothèque) :
                    # « Supprimer l'original ? » posée ensuite par l'appelant
                    kept[cbz_path] = (cbz.count_to_check(), file_signature(cbz_path))
                else:
                    try:
                        delete_source(cbz, img_path, permanent=is_permanent,
                                      source_signature=source_signature)
                    except Exception as del_err:
                        conversion_errors.append(
                            f"{basename}: " + _("messages.errors.batch_source_delete_failed", error=_failure_text(del_err)))

                converted_count[0] += 1
                last_done[0] = basename
                converted_by_ext[ext_lower] = converted_by_ext.get(ext_lower, 0) + 1
                new_files.append(cbz_path)

            except Exception as e:
                if _is_disk_full(e):
                    _stop_for_disk_full(e, basename, total - idx, conversion_errors, disk_full, last_done[0])
                    break
                conversion_errors.append(f"{basename}: {_failure_text(e)}")

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        log_error = None
        if conversion_errors:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "imgtocbz", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write(log_title("title_img"))
                    lf.write(log_line("date", date=now.strftime('%Y-%m-%d %H:%M')))
                    lf.write(log_line("directory", path=directory))
                    lf.write(log_line("total_files", count=len(img_files)))
                    lf.write(log_line("converted", count=converted_count[0]))
                    lf.write(log_line("errors", count=len(conversion_errors)))
                    lf.write(f"\n{'='*60}\n")
                    lf.write(log_line("error_details"))
                    lf.write(f"{'='*60}\n\n")
                    for err in conversion_errors:
                        lf.write(f"  - {log_text(err)}\n")
            except Exception as e:
                # Journal impossible à écrire (dossier temporaire plein ou
                # protégé) : la raison est affichée dans le résumé.
                log_path = None
                log_error = exception_text(e)

        summary_data = {
            "converted_count":  converted_count[0],
            "total":            len(img_files),
            "errors_count":     len(conversion_errors),
            "has_errors":       bool(conversion_errors),
            "disk_full":        disk_full[0] if disk_full else None,
            "directory":        directory,
            "directories":      directories,
            "log_path":         log_path,
            "log_error":        log_error,
            "converted_by_ext": dict(converted_by_ext),
        }
        _end_batch(parent, "img", show_batch_img_summary, summary_data, callbacks,
                   on_finished, new_files)

    signals.conversion_done.connect(on_done)
    _start_batch_thread(do_conversion, parent, claims, prog, signals)


def show_batch_img_summary(parent, data, callbacks):
    dlg = _ImgSummaryDialog(parent, data)
    dlg.show_nonmodal()


def batch_convert_imgs_to_single_cbz(parent, img_files, directory, callbacks, on_finished=None):
    """Regroupe toutes les images du répertoire en un seul fichier CBZ
    (« dossier/nom du dossier.cbz »). on_finished : voir _finish_batch."""
    img_files  = [os.path.normpath(f) for f in img_files]
    if _refuse_if_batch_running(parent, img_files):
        return _finish_batch(on_finished, None)
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
        files        = img_files,
    )
    def _on_confirm_done_single():
        if not dlg.confirmed:
            return _finish_batch(on_finished, None)
        is_permanent = dlg.permanent_delete
        _run_imgs_to_single_cbz(parent, img_files, directory, callbacks, is_permanent, on_finished)

    dlg.finished.connect(lambda _: _on_confirm_done_single())
    dlg.show_nonmodal()


def _run_imgs_to_single_cbz(parent, img_files, directory, callbacks, is_permanent,
                            on_finished=None):
    claims = _start_batch_claims(parent, img_files)
    if claims is None:
        return _finish_batch(on_finished, None)
    new_files = []    # CBZ créé, rendu par on_finished
    prog = _ProgressDialog(parent, "dialogs.batch_img.converting_title", has_page_bar=False)
    prog.show_nonmodal()
    QApplication.processEvents()

    signals           = _ThreadSignals()
    _register_batch(prog, signals)
    conversion_errors = []
    disk_full         = []    # lot arrêté faute de place (_stop_for_disk_full)
    last_done         = [None]    # dernier fichier fini sans erreur
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
        from modules.qt.converted_cbz import delete_source
        from modules.qt.utils import file_signature
        # Taille prévue : celle des images, en-têtes ZIP compris (image dont
        # la taille ne se lit pas : comptée pour rien, elle échouera à la lecture)
        needed = 22
        for p in free_files:
            try:
                needed += os.path.getsize(p) + 100 + 2 * (len(flat[relative[p]].encode("utf-8")) + 8)
            except OSError:
                pass  # repli : image illisible, consignée au journal quand sa lecture échouera
        try:
            with _interruptible_zip_writer(
                    cbz_path_out, needed=needed,
                    **zip_compression_kwargs(_gcm().get_zip_compression_level())) as cbz:
                for idx, img_path in enumerate(free_files):
                    basename  = os.path.basename(img_path)
                    ext_lower = os.path.splitext(img_path)[1].lower()

                    signals.update_filename.emit(basename)
                    signals.update_progress.emit(
                        lambda c=idx + 1, t=total: _("dialogs.batch_img.converting_progress").format(current=c, total=t))

                    # Vignette (taille annoncée impossible refusée avant décodage)
                    try:
                        from modules.qt.entries import check_announced_size
                        img = Image.open(img_path)
                        check_announced_size(img, os.path.getsize(img_path))
                        px  = _pil_to_qimage(img, 150, 210, callbacks)
                        signals.update_thumb.emit(px)
                        img = None
                    except Exception:
                        signals.update_thumb.emit(None)

                    try:
                        # Comparé à la suppression (delete_source) : une image
                        # réécrite après sa lecture n'est jamais supprimée
                        source_signature = file_signature(img_path)
                        with open(img_path, 'rb') as f:
                            img_data = f.read()

                        try:
                            # Taille annoncée impossible refusée avant décodage
                            from modules.qt.entries import open_checked_image
                            tmp = open_checked_image(img_data)
                            tmp.load()
                        except Exception as val_err:
                            raise _UnusableImage(_("messages.errors.batch_image_unreadable",
                                                   error=exception_text(val_err)))

                        if tmp.format == 'ICO' or ext_lower == '.ico':
                            raise _UnusableImage(_("messages.errors.batch_image_ico"))

                        # JPEG MPO : une photo ordinaire (voir _run_img_conversion)
                        n_frames = 1 if tmp.format == 'MPO' else getattr(tmp, 'n_frames', 1)
                        if n_frames > 1:
                            fmt = tmp.format or ext_lower.lstrip('.').upper()
                            raise _UnusableImage(_("messages.errors.batch_image_multiframe",
                                                   format=fmt, count=n_frames))

                        # Nom de la page avec l'extension du vrai format
                        page_name = name_for_real_format(flat[relative[img_path]], tmp.format)
                        img_data = _normalized_page_bytes(img_data, tmp,
                                                          os.path.splitext(page_name)[1].lower())
                        tmp = None

                        cbz.writestr(_unique_flat_name(page_name, written_names), img_data)
                        img_data = None
                        gc.collect()
                        written_images.append((img_path, basename, ext_lower, source_signature))

                    except Exception as e:
                        if _is_disk_full(e):
                            raise          # tout le CBZ est abandonné (except plus bas)
                        conversion_errors.append(f"{basename}: {_failure_text(e)}")

                if not written_images:
                    # Aucune image écrite : le temporaire est jeté, aucun CBZ
                    # vide n'est créé.
                    raise _IncompleteCbz()
                cbz.expected_count = len(written_images)

            # Ici, le CBZ relu porte son nom : un CBZ qui ne se relit pas a levé
            # WrittenFileCheckError (except ci-dessous), aucune image supprimée.
            new_files.append(cbz_path_out)
            for img_path, basename, ext_lower, source_signature in written_images:
                try:
                    delete_source(cbz, img_path, permanent=is_permanent,
                                  source_signature=source_signature)
                except Exception as del_err:
                    conversion_errors.append(
                        f"{basename}: " + _("messages.errors.batch_source_delete_failed", error=_failure_text(del_err)))

                converted_count[0] += 1
                last_done[0] = basename
                converted_by_ext[ext_lower] = converted_by_ext.get(ext_lower, 0) + 1

        except _IncompleteCbz:
            pass  # chaque image en échec est déjà consignée au journal du lot
        except Exception as e:
            if _is_disk_full(e):
                # CBZ jamais créé : aucune image supprimée, toutes non traitées
                _stop_for_disk_full(e, os.path.basename(cbz_path_out), total,
                                    conversion_errors, disk_full, last_done[0])
                signals.conversion_done.emit()
                return
            conversion_errors.append(
                f"{os.path.basename(cbz_path_out)}: "
                + _("messages.errors.batch_cbz_creation_failed", error=_failure_text(e)))

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        log_error = None
        if conversion_errors:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "imgtocbz", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write(log_title("title_img_single"))
                    lf.write(log_line("date", date=now.strftime('%Y-%m-%d %H:%M')))
                    lf.write(log_line("directory", path=directory))
                    lf.write(log_line("output", path=cbz_path_out))
                    lf.write(log_line("total_files", count=len(img_files)))
                    lf.write(log_line("converted", count=converted_count[0]))
                    lf.write(log_line("errors", count=len(conversion_errors)))
                    lf.write(f"\n{'='*60}\n")
                    lf.write(log_line("error_details"))
                    lf.write(f"{'='*60}\n\n")
                    for err in conversion_errors:
                        lf.write(f"  - {log_text(err)}\n")
            except Exception as e:
                # Journal impossible à écrire (dossier temporaire plein ou
                # protégé) : la raison est affichée dans le résumé.
                log_path = None
                log_error = exception_text(e)

        summary_data = {
            "converted_count":  converted_count[0],
            "total":            len(img_files),
            "errors_count":     len(conversion_errors),
            "has_errors":       bool(conversion_errors),
            "disk_full":        disk_full[0] if disk_full else None,
            "directory":        directory,
            "directories":      None,
            "log_path":         log_path,
            "log_error":        log_error,
            "converted_by_ext": dict(converted_by_ext),
        }
        _end_batch(parent, "img", show_batch_img_summary, summary_data, callbacks,
                   on_finished, new_files)

    signals.conversion_done.connect(on_done)
    _start_batch_thread(do_conversion, parent, claims, prog, signals)


# ═══════════════════════════════════════════════════════════════════════════════
# Batch : recompression ZIP des CBZ au taux par défaut
# ═══════════════════════════════════════════════════════════════════════════════

class _RecompressConfirmDialog(AppWindowMixin, QDialog):
    """Fenêtre de confirmation avant le lancement de la recompression batch.
    Pas de checkbox (pas de suppression de fichiers dans ce batch)."""

    def __init__(self, parent, count, directory, total_size, level):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("dialogs.batch_recompress.window_title")
        self._count      = count
        self._directory  = directory
        self._total_size = total_size
        self._level      = level
        self.confirmed   = False

        self.setFixedWidth(500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        self._msg_lbl = QLabel()
        self._msg_lbl.setTextFormat(Qt.PlainText)
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg_lbl)

        self._size_lbl = QLabel()
        self._size_lbl.setTextFormat(Qt.PlainText)
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
        self._connect_language()
        self._start_btn.setFocus()

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
        self._msg_lbl.setText(_("dialogs.batch_recompress.confirm_message").format(
            count=self._count, directory=display_dir, level=self._level))
        self._msg_lbl.setFont(font)
        self._msg_lbl.setMinimumHeight(self._msg_lbl.heightForWidth(460))
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
    dlg.show_nonmodal()


class _AlreadyAtLevel(Exception):
    """Levée dans le bloc d'écriture de la recompression : le résultat est le même
    que l'original. Son temporaire est jeté, et l'original garde sa date."""


def _members_compression(zf):
    """Méthode et taille compressée des fichiers d'un ZIP, dossiers exclus. Un ZIP
    n'indique pas le taux employé : à données égales, seul le résultat le dit."""
    return [(i.compress_type, i.compress_size) for i in zf.infolist() if not i.is_dir()]


def _all_members_stored(path):
    """True si aucun fichier du ZIP n'est compressé. Ne décompresse rien : la méthode
    de chaque fichier est dans le répertoire de l'archive, lu à son ouverture. Archive
    illisible : False, c'est sa recompression qui signalera l'erreur."""
    try:
        with open_zip(path) as zf:
            return all(method == 0 for method, _size in _members_compression(zf))
    except Exception:
        return False  # repli : l'erreur est consignée par la recompression qui suit


def _update_open_comics_compression(parent, rewritten, level):
    """Les comics ouverts dans un panneau dont le fichier vient d'être recompressé
    prennent leur nouvel état : il n'est sinon relu qu'à la réouverture, et
    la barre de statut garderait l'ancien."""
    from modules.qt.utils import is_same_path
    for panel in parent.window()._all_panels():
        state = panel._state
        if state.current_file and any(is_same_path(state.current_file, p) for p in rewritten):
            state.zip_compression_state = "stored" if level <= 0 else "deflated"
            panel._update_status_bar()


def _run_recompress(parent, archive_files, directory, directories, callbacks, level):
    from modules.qt.utils import zip_compression_kwargs

    claims = _start_batch_claims(parent, archive_files)
    if claims is None:
        return
    prog = _ProgressDialog(parent, "dialogs.batch_recompress.running_title", has_page_bar=False)
    prog.show_nonmodal()
    QApplication.processEvents()

    signals = _ThreadSignals()
    _register_batch(prog, signals)

    conversion_errors = []
    disk_full          = []    # lot arrêté faute de place (_stop_for_disk_full)
    last_done          = [None]    # dernier fichier fini sans erreur
    renamed_entries    = []
    recompressed_paths = []
    already_optimal_count = [0]
    ignored_entries     = []

    signals.update_filename.connect(prog.set_filename)
    signals.update_progress.connect(prog.set_progress)
    signals.update_thumb.connect(prog.set_thumbnail)

    def do_recompress():
        total = len(archive_files)
        zip_kwargs = zip_compression_kwargs(level)
        for idx, path in enumerate(archive_files):
            basename = os.path.basename(path)
            signals.update_filename.emit(basename)
            signals.update_progress.emit(
                lambda c=idx + 1, t=total: _("dialogs.batch_recompress.progress").format(current=c, total=t))

            # Fichier et nom de renommage réservés pour ce fichier seulement
            claims.release_all()
            if not _claim_file(claims, path, conversion_errors):
                continue

            real_type = detect_archive_type(path)

            if real_type is None:
                # Format inconnu, fichier disparu ou illisible : rien ne dit que
                # ce n'est pas un CBZ
                signals.update_thumb.emit(None)
                conversion_errors.append(f"{basename}: " + _("messages.errors.batch_unknown_format"))
                continue

            if real_type != "CBZ":
                # Vraie archive d'un autre format : ignorée, nommée dans le journal
                signals.update_thumb.emit(None)
                ignored_entries.append(basename)
                continue

            # Vignette : première page du CBZ, celle que montre son ouverture
            _emit_first_page_thumbnail(path, signals, callbacks)

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
                    conversion_errors.append(f"{basename}: {_failure_text(e)}")
                    continue

            # Sans compression demandée, et aucun fichier compressé : rien à calculer
            if level <= 0 and _all_members_stored(target_path):
                already_optimal_count[0] += 1
                last_done[0] = basename
                continue

            # Recompresse dans le temporaire de l'écrivain, qui ne remplace
            # l'original qu'à la sortie du bloc : le CBZ d'origine, lu pendant
            # l'écriture, est refermé avant (Windows refuse de remplacer un
            # fichier ouvert). Aucune source supprimée : pas de relecture
            # complète, mais contrôle de la structure avant le remplacement,
            # comme un enregistrement depuis un panneau. Tout échec laisse
            # l'original intact et supprime le temporaire.
            try:
                # Taille prévue : chaque membre non compressé, en-têtes compris
                with open_zip(target_path) as zin:
                    needed = 22 + len(zin.comment) + sum(
                        info.file_size + 100 + 2 * len(info.filename.encode("utf-8"))
                        for info in zin.infolist())
                with _interruptible_zip_writer(target_path, verify=False, check_structure=True,
                                               needed=needed, **zip_compression_kwargs(level)) as zout:
                    # Noms réels des membres (open_zip) : réécrits ici avec le
                    # drapeau UTF-8, un nom mal lu le resterait définitivement
                    with open_zip(target_path) as zin:
                        # Lecture par ZipInfo, jamais par nom : read(nom) rend le
                        # contenu du dernier homonyme pour chacun d'eux, et le
                        # premier serait remplacé par une copie du second. Taille
                        # annoncée contrôlée avant lecture (limite par fichier,
                        # MAX_MEMBER_SIZE) : un membre géant serait sinon chargé
                        # entier en mémoire ; refusé, l'original reste intact.
                        # Écrit par son ZipInfo, lu juste avant : seule la
                        # compression change, le membre garde sa date, son
                        # commentaire, ses données supplémentaires et ses attributs.
                        # État d'origine relevé avant : l'écriture met à jour ce ZipInfo.
                        before = _members_compression(zin)
                        for info in zin.infolist():
                            _check_member_size(info.file_size)
                            zout.writestr(info, zin.read(info),
                                          compress_type=zip_kwargs["compression"],
                                          compresslevel=zip_kwargs.get("compresslevel"))
                        if _members_compression(zout) == before:
                            raise _AlreadyAtLevel()
                        # Commentaire d'archive (métadonnées ComicBookInfo) conservé
                        zout.comment = zin.comment
                        zout.expected_count = sum(1 for info in zin.infolist() if not info.is_dir())
                recompressed_paths.append(target_path)
                last_done[0] = basename
            except _AlreadyAtLevel:
                already_optimal_count[0] += 1
                last_done[0] = basename
            except Exception as e:
                if _is_disk_full(e):
                    _stop_for_disk_full(e, os.path.basename(target_path), total - idx,
                                        conversion_errors, disk_full, last_done[0])
                    break
                conversion_errors.append(f"{os.path.basename(target_path)}: {_failure_text(e)}")

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)
        if recompressed_paths:
            _update_open_comics_compression(parent, recompressed_paths, level)

        log_path = None
        log_error = None
        if conversion_errors or renamed_entries or ignored_entries:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "recompress", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write(log_title("title_recompress"))
                    lf.write(log_line("date", date=now.strftime('%Y-%m-%d %H:%M')))
                    lf.write(log_line("directory", path=directory))
                    lf.write(log_line("total_files", count=len(archive_files)))
                    lf.write(log_line("recompressed", count=len(recompressed_paths)))
                    lf.write(log_line("already_optimal", count=already_optimal_count[0]))
                    lf.write(log_line("ignored_not_cbz", count=len(ignored_entries)))
                    lf.write(log_line("renamed_misnamed", count=len(renamed_entries)))
                    lf.write(log_line("errors", count=len(conversion_errors)))
                    if renamed_entries:
                        lf.write(f"\n{'='*60}\n")
                        lf.write(log_line("renamed_files"))
                        lf.write(f"{'='*60}\n\n")
                        for entry in renamed_entries:
                            lf.write(f"  - {log_text(entry)}\n")
                    if ignored_entries:
                        lf.write(f"\n{'='*60}\n")
                        lf.write(log_line("ignored_not_cbz", count=len(ignored_entries)))
                        lf.write(f"{'='*60}\n\n")
                        for entry in ignored_entries:
                            lf.write(f"  - {log_text(entry)}\n")
                    if conversion_errors:
                        lf.write(f"\n{'='*60}\n")
                        lf.write(log_line("error_details"))
                        lf.write(f"{'='*60}\n\n")
                        for err in conversion_errors:
                            lf.write(f"  - {log_text(err)}\n")
            except Exception as e:
                # Journal impossible à écrire (dossier temporaire plein ou
                # protégé) : la raison est affichée dans le résumé.
                log_path = None
                log_error = exception_text(e)

        summary_data = {
            # Fichiers réellement traités : moins que le lot s'il a été annulé
            "total":               (len(recompressed_paths) + already_optimal_count[0]
                                    + len(ignored_entries) + len(conversion_errors)),
            "recompressed_count":  len(recompressed_paths),
            "already_optimal_count": already_optimal_count[0],
            "ignored_count":       len(ignored_entries),
            "renamed_count":       len(renamed_entries),
            "errors_count":        len(conversion_errors),
            "has_errors":          bool(conversion_errors),
            "disk_full":           disk_full[0] if disk_full else None,
            "directory":           directory,
            "directories":         directories,
            "log_path":            log_path,
            "log_error":           log_error,
        }
        show_batch_recompress_summary(parent, summary_data)

    signals.conversion_done.connect(on_done)
    _start_batch_thread(do_recompress, parent, claims, prog, signals)


class _RecompressSummaryDialog(AppWindowMixin, QDialog):
    def __init__(self, parent, data):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("dialogs.batch_recompress.complete_title")
        self._data = data
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        top = QVBoxLayout()
        top.setSpacing(0)
        top.setContentsMargins(0, 0, 0, 0)
        self._msg_lbl = QLabel()
        self._msg_lbl.setTextFormat(Qt.PlainText)
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignCenter)
        top.addWidget(self._msg_lbl)
        _add_dir_links(top, data)
        layout.addLayout(top)

        self._error_lbl = None
        log_path = data.get("log_path")
        if data.get("has_errors") or log_path or data.get("log_error"):
            self._error_lbl = QLabel()
            # Lien vers le journal (_log_label_text, qui échappe ce qu'il insère)
            self._error_lbl.setTextFormat(Qt.RichText)
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

        self._connect_language()
        self._ok_btn.setFocus()

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
        message = _("dialogs.batch_recompress.summary_message").format(
            total=data["total"],
            recompressed=data["recompressed_count"],
            already_optimal=data["already_optimal_count"],
            ignored=data["ignored_count"],
        )
        if data["renamed_count"]:
            message += "\n" + _("dialogs.batch_recompress.renamed_line").format(
                renamed=data["renamed_count"])
        self._msg_lbl.setText(message)
        self._msg_lbl.setFont(font)
        self._msg_lbl.setMinimumHeight(self._msg_lbl.heightForWidth(440))

        if self._error_lbl is not None:
            if data.get("has_errors"):
                count = data["errors_count"]
                self._error_lbl.setText(_log_label_text(
                    data, _("dialogs.batch_recompress.errors_with_log").format(count=count),
                    _("dialogs.batch_recompress.errors_count", count=count)))
            else:
                self._error_lbl.setText(_log_label_text(
                    data, _("dialogs.batch_recompress.view_log")))
            self._error_lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)


def show_batch_recompress_summary(parent, data):
    dlg = _RecompressSummaryDialog(parent, data)
    dlg.show_nonmodal()
