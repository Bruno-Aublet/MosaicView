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


def _rename_misnamed(path, real_type, claims, conversion_errors):
    """Fichier d'un lot dont l'extension ne correspond pas au format réel
    `real_type` (ZIP nommé .cbr…) : renommé par la règle commune
    (misnamed_archives.rename_to_real_format : extension du format réel, nom
    libre « X (1).cbz »… réservé contre les autres lots par `claims`, jamais
    un fichier écrasé). Rend le nouveau chemin, ou None si le renommage
    échoue, raison ajoutée à `conversion_errors` (texte du thread du lot,
    en latin). Comme avant, les fichiers ouverts dans les panneaux ne sont
    pas consultés : leurs listes appartiennent au thread de l'interface."""
    from modules.qt.misnamed_archives import RenameRefusedError, rename_to_real_format
    basename = os.path.basename(path)
    try:
        return rename_to_real_format(path, real_type, claims=claims)
    except RenameRefusedError as e:
        conversion_errors.append(f"{basename}: " + _(e.key, name=e.name))
    except Exception as e:
        conversion_errors.append(f"{basename}: {_failure_text(e)}")
    return None


def count_ignored_files(directories, files):
    """Fichiers des dossiers `directories` (sous-dossiers compris, liens et
    jonctions jamais suivis : walk_without_links, comme le parcours du lot)
    que le lot ne traite pas : tous les fichiers trouvés, moins `files`.
    Ligne « Nombre de fichiers ignorés » du journal d'un lot lancé sur un
    dossier (menu, dépôt d'un dossier) ; sans objet pour une liste de
    fichiers donnée par la Bibliothèque."""
    total = 0
    for directory in directories:
        for _dirpath, _subdirs, filenames in walk_without_links(directory):
            total += len(filenames)
    return max(0, total - len(files))


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


# Second argument de on_finished(None, …) quand un lot est refusé : un autre
# lot traite déjà l'un de ses fichiers. Les chaînes de lots (Bibliothèque,
# conversion complète) continuent alors avec le lot suivant et le signalent à
# la fin, alors qu'une annulation par l'utilisateur (on_finished(None, None))
# arrête la chaîne.
BATCH_REFUSED = "refused"


def _finish_batch(on_finished, new_files, summary=None):
    """Prévient l'appelant d'un lot lancé sur une liste de fichiers
    (Bibliothèque, conversion complète) : on_finished(liste des CBZ créés et
    des fichiers renommés, (type du lot, données de son résumé)) à la fin du
    lot ; si le lot n'a pas eu lieu, on_finished(None, BATCH_REFUSED) quand
    il est refusé (un autre lot traite l'un des fichiers, _refused_batch),
    on_finished(None, None) quand l'utilisateur l'a annulé à la
    confirmation ou au choix du mode."""
    if on_finished is not None:
        on_finished(new_files, summary)


def _refused_batch(on_finished):
    """Lot refusé avant de commencer (_refuse_if_batch_running,
    _start_batch_claims) : voir BATCH_REFUSED."""
    return _finish_batch(on_finished, None, BATCH_REFUSED)


def _end_batch(parent, kind, show_summary, summary_data, callbacks, on_finished, new_files):
    """Fin d'un lot : son résumé, ou, pour un lot d'une chaîne de la
    Bibliothèque (on_finished), ses données transmises à la chaîne, qui
    montre un seul résumé pour tous ses lots (show_chain_summary)."""
    if on_finished is None:
        show_summary(parent, summary_data, callbacks)
    else:
        _finish_batch(on_finished, new_files, (kind, summary_data))


def _chained_lot_data(prog, handled, errors, renamed=(), renamed_paths=()):
    """Données ajoutées au résumé d'un lot pour la conversion complète en CBZ
    (_run_convert_all) : lot annulé (la suite est alors abandonnée),
    fichiers réellement faits (les `handled[0]` premiers de sa liste),
    lignes de son journal (erreurs, fichiers renommés), écrites dans le
    journal unique de la conversion complète, et chemins des fichiers
    seulement renommés (renamed_paths) : les autres fichiers rendus par le
    lot sont des CBZ qu'il a écrits, que la recompression laisse de côté."""
    return {"cancelled":     prog.cancel_event.is_set(),
            "handled":       handled[0],
            "error_lines":   list(errors),
            "renamed_lines": list(renamed),
            "renamed_paths": list(renamed_paths)}


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
                 total_size, checkbox_key, tooltip_key, start_key, callbacks, files=(),
                 details_fn=None):
        """files : fichiers d'origine que le lot supprimera ; ceux qui ne
        peuvent pas aller à la Corbeille (recycle_bin.trash_problem_count) sont
        signalés tant que la case de suppression définitive n'est pas cochée.
        details_fn : fonction () -> [lignes traduites], affichées sous le
        message et retraduites avec lui (conversion complète en CBZ : une
        ligne par étape)."""
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
        self._tooltip_key  = tooltip_key
        self._start_key    = start_key
        self._callbacks    = callbacks
        self._details_fn   = details_fn
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

        self._details_lbl = None
        if details_fn is not None:
            self._details_lbl = QLabel()
            self._details_lbl.setTextFormat(Qt.PlainText)
            self._details_lbl.setWordWrap(True)
            self._details_lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(self._details_lbl)

        self._size_lbl = QLabel()
        self._size_lbl.setTextFormat(Qt.PlainText)
        self._size_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._size_lbl)

        layout.addSpacing(6)

        self._chk = QCheckBox()
        layout.addWidget(self._chk, alignment=Qt.AlignCenter)
        # Avertissement de la suppression définitive, au survol de la case
        self._overlay_tip = None
        if tooltip_key:
            from modules.qt.overlay_tooltip_qt import OverlayTooltip
            self._overlay_tip = OverlayTooltip(self)
            self._overlay_tip.track(self._chk)

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
        if self._details_lbl is not None:
            self._details_lbl.setText("\n".join(self._details_fn()))
            self._details_lbl.setFont(font)
            self._details_lbl.setMinimumHeight(self._details_lbl.heightForWidth(self.width() - 40))
        self._size_lbl.setText(f"({format_file_size(self._total_size)})")
        self._size_lbl.setFont(font)
        self._chk.setText(_(self._checkbox_key))
        self._chk.setFont(font)
        if self._overlay_tip is not None:
            tip = html.escape(_(self._tooltip_key)).replace("\n", "<br>")
            self._overlay_tip.set_tracked_html(
                f'<table style="max-width:360px;white-space:normal;">'
                f'<tr><td>{tip}</td></tr></table>', self._chk)
            self._overlay_tip.force_refresh_visible(self._chk)
            self._overlay_tip.apply_theme()
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

    def __init__(self, parent, title_key, has_page_bar=True, has_thumbnail=True):
        """has_thumbnail=False : lot qui ne lit aucune page (vérification des
        extensions), fenêtre sans la place de la vignette."""
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
        height = 440 if has_page_bar else 390
        self.setFixedHeight(height if has_thumbnail else height - 210)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(8)

        # Vignette (sans elle : masquée, hors du layout)
        self._thumb_lbl = QLabel(self)
        self._thumb_lbl.setFixedSize(150, 210)
        self._thumb_lbl.setAlignment(Qt.AlignCenter)
        self._thumb_lbl.setVisible(has_thumbnail)
        if has_thumbnail:
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
        self._renamed_epub_lbl = None
        for key in ("renamed_cbz", "renamed_cb7", "renamed_cbt", "renamed_epub"):
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
        has_renamed = any(data.get(k, 0) > 0
                          for k in ("renamed_cbz", "renamed_cb7", "renamed_cbt", "renamed_epub"))
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
            ("renamed_epub", "dialogs.batch_cbr.renamed_epub_count"),
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

    batch_convert_cbr_to_cbz_confirm(parent, cbr_files, directory, callbacks,
                                     ignored_count=count_ignored_files([directory], cbr_files))


def batch_convert_cbr_to_cbz_confirm(parent, cbr_files, directory, callbacks, directories=None,
                                     on_finished=None, ignored_count=None):
    """Fenêtre de confirmation + progression + résumé CBR→CBZ.
    on_finished : voir _finish_batch."""
    cbr_files  = [os.path.normpath(f) for f in cbr_files]
    if _refuse_if_batch_running(parent, cbr_files):
        return _refused_batch(on_finished)
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
                            on_finished, ignored_count=ignored_count)

    dlg.finished.connect(lambda _: _on_confirm_done())
    dlg.show_nonmodal()


def _run_cbr_conversion(parent, cbr_files, directory, directories, callbacks, is_permanent,
                        on_finished=None, ignored_count=None, own_log=True):
    """own_log : faux pour un lot de la conversion complète en CBZ
    (_run_convert_all), qui écrit un seul journal pour tous ses lots avec
    les lignes rendues dans le résumé (error_lines, renamed_lines)."""
    claims = _start_batch_claims(parent, cbr_files)
    if claims is None:
        return _refused_batch(on_finished)
    new_files = []    # CBZ créés et fichiers renommés, rendus par on_finished
    renamed_paths = []    # fichiers seulement renommés (_chained_lot_data)
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
    renamed_epub     = [0]
    handled          = [0]    # fichiers réellement faits (_handled_counter)

    signals.update_filename.connect(prog.set_filename)
    signals.update_progress.connect(prog.set_progress)
    signals.update_page_bar.connect(lambda v, t: prog.set_page_progress(v, t))
    signals.update_thumb.connect(prog.set_thumbnail)

    def do_conversion():
        total = len(cbr_files)
        for idx, cbr_path in enumerate(cbr_files):
            handled[0] = idx
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

            # Fichier mal nommé : ZIP, 7z, TAR ou EPUB → simple renommage
            if real_type in ("CBZ", "CB7", "CBT", "EPUB"):
                label_map   = {"CBZ": "CBR → CBZ", "CB7": "CBR → CB7", "CBT": "CBR → CBT",
                               "EPUB": "CBR → EPUB"}
                counter_map = {"CBZ": renamed_cbz, "CB7": renamed_cb7, "CBT": renamed_cbt,
                               "EPUB": renamed_epub}
                new_path = _rename_misnamed(cbr_path, real_type, claims, conversion_errors)
                if new_path:
                    signals.update_page_bar.emit(100.0, label_map[real_type])
                    counter_map[real_type][0] += 1
                    last_done[0] = basename
                    renamed_entries.append(f"{basename} → {os.path.basename(new_path)}")
                    new_files.append(new_path)
                    renamed_paths.append(new_path)
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
        else:
            handled[0] = total    # boucle allée au bout (ni disque plein, ni annulation)

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        log_error = None
        if (conversion_errors or renamed_entries) and own_log:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "cbrtocbz", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write(log_title("title_cbr"))
                    lf.write(log_line("date", date=now.strftime('%Y-%m-%d %H:%M')))
                    lf.write(log_line("directory", path=directory))
                    # Fichiers .cbr pris par le lot ; autres fichiers du
                    # dossier, s'il a été parcouru (menu, dépôt d'un dossier)
                    lf.write(log_line("files_handled", count=handled[0]))
                    if ignored_count is not None:
                        lf.write(log_line("files_ignored", count=ignored_count))
                    lf.write(log_line("converted", count=converted_count[0]))
                    # Ancienne extension → nouvelle (celle du format réel),
                    # seulement pour les renommages qui ont eu lieu
                    for kind, count in (("CBR→CBZ", renamed_cbz[0]), ("CBR→CB7", renamed_cb7[0]),
                                        ("CBR→CBT", renamed_cbt[0]), ("CBR→EPUB", renamed_epub[0])):
                        if count:
                            lf.write(log_line("renamed_kind", kind=kind, count=count))
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
            "renamed_epub":    renamed_epub[0],
            "errors_count":    len(conversion_errors),
            "has_errors":      bool(conversion_errors),
            "disk_full":       disk_full[0] if disk_full else None,
            "directory":       directory,
            "directories":     directories,
            "log_path":        log_path,
            "log_error":       log_error,
            **_chained_lot_data(prog, handled, conversion_errors, renamed_entries,
                                renamed_paths),
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
        self._renamed_epub_lbl = None
        for key in ("renamed_cbz", "renamed_cbr", "renamed_cbt", "renamed_epub"):
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
        has_renamed = any(data.get(k, 0) > 0
                          for k in ("renamed_cbz", "renamed_cbr", "renamed_cbt", "renamed_epub"))
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
            ("renamed_epub", "dialogs.batch_cb7.renamed_epub_count"),
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

    batch_convert_cb7_to_cbz_confirm(parent, cb7_files, directory, callbacks,
                                     ignored_count=count_ignored_files([directory], cb7_files))


def batch_convert_cb7_to_cbz_confirm(parent, cb7_files, directory, callbacks, directories=None,
                                     on_finished=None, ignored_count=None):
    """Fenêtre de confirmation + progression + résumé CB7→CBZ.
    on_finished : voir _finish_batch."""
    cb7_files = [os.path.normpath(f) for f in cb7_files]
    if _refuse_if_batch_running(parent, cb7_files):
        return _refused_batch(on_finished)
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
                            on_finished, ignored_count=ignored_count)

    dlg.finished.connect(lambda _: _on_confirm_done_cb7())
    dlg.show_nonmodal()


def _run_cb7_conversion(parent, cb7_files, directory, directories, callbacks, is_permanent,
                        on_finished=None, ignored_count=None, own_log=True):
    """own_log : voir _run_cbr_conversion."""
    claims = _start_batch_claims(parent, cb7_files)
    if claims is None:
        return _refused_batch(on_finished)
    new_files = []    # CBZ créés et fichiers renommés, rendus par on_finished
    renamed_paths = []    # fichiers seulement renommés (_chained_lot_data)
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
    renamed_epub      = [0]
    handled           = [0]    # fichiers réellement faits (voir le lot CBR)

    signals.update_filename.connect(prog.set_filename)
    signals.update_progress.connect(prog.set_progress)
    signals.update_page_bar.connect(lambda v, t: prog.set_page_progress(v, t))
    signals.update_thumb.connect(prog.set_thumbnail)

    def do_conversion():
        total = len(cb7_files)
        for idx, cb7_path in enumerate(cb7_files):
            handled[0] = idx
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

            # Fichier mal nommé : ZIP, RAR, TAR ou EPUB → simple renommage
            if real_type in ("CBZ", "CBR", "CBT", "EPUB"):
                label_map   = {"CBZ": "CB7 → CBZ", "CBR": "CB7 → CBR", "CBT": "CB7 → CBT",
                               "EPUB": "CB7 → EPUB"}
                counter_map = {"CBZ": renamed_cbz, "CBR": renamed_cbr, "CBT": renamed_cbt,
                               "EPUB": renamed_epub}
                new_path = _rename_misnamed(cb7_path, real_type, claims, conversion_errors)
                if new_path:
                    signals.update_page_bar.emit(100.0, label_map[real_type])
                    counter_map[real_type][0] += 1
                    last_done[0] = basename
                    renamed_entries.append(f"{basename} → {os.path.basename(new_path)}")
                    new_files.append(new_path)
                    renamed_paths.append(new_path)
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
        else:
            handled[0] = total    # boucle allée au bout (ni disque plein, ni annulation)

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        log_error = None
        if (conversion_errors or renamed_entries) and own_log:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "cb7tocbz", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write(log_title("title_cb7"))
                    lf.write(log_line("date", date=now.strftime('%Y-%m-%d %H:%M')))
                    lf.write(log_line("directory", path=directory))
                    # Fichiers .cb7 pris par le lot ; autres fichiers du
                    # dossier, s'il a été parcouru (menu, dépôt d'un dossier)
                    lf.write(log_line("files_handled", count=handled[0]))
                    if ignored_count is not None:
                        lf.write(log_line("files_ignored", count=ignored_count))
                    lf.write(log_line("converted", count=converted_count[0]))
                    # Ancienne extension → nouvelle (celle du format réel),
                    # seulement pour les renommages qui ont eu lieu
                    for kind, count in (("CB7→CBZ", renamed_cbz[0]), ("CB7→CBR", renamed_cbr[0]),
                                        ("CB7→CBT", renamed_cbt[0]), ("CB7→EPUB", renamed_epub[0])):
                        if count:
                            lf.write(log_line("renamed_kind", kind=kind, count=count))
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
            "renamed_epub":    renamed_epub[0],
            "errors_count":    len(conversion_errors),
            "has_errors":      bool(conversion_errors),
            "disk_full":       disk_full[0] if disk_full else None,
            "directory":       directory,
            "directories":     directories,
            "log_path":        log_path,
            "log_error":       log_error,
            **_chained_lot_data(prog, handled, conversion_errors, renamed_entries,
                                renamed_paths),
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
        self._renamed_epub_lbl = None
        for key in ("renamed_cbz", "renamed_cbr", "renamed_cb7", "renamed_epub"):
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
        has_renamed = any(data.get(k, 0) > 0
                          for k in ("renamed_cbz", "renamed_cbr", "renamed_cb7", "renamed_epub"))
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
            ("renamed_epub", "dialogs.batch_cbt.renamed_epub_count"),
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

    batch_convert_cbt_to_cbz_confirm(parent, cbt_files, directory, callbacks,
                                     ignored_count=count_ignored_files([directory], cbt_files))


def batch_convert_cbt_to_cbz_confirm(parent, cbt_files, directory, callbacks, directories=None,
                                     on_finished=None, ignored_count=None):
    """Fenêtre de confirmation + progression + résumé CBT→CBZ.
    on_finished : voir _finish_batch."""
    cbt_files = [os.path.normpath(f) for f in cbt_files]
    if _refuse_if_batch_running(parent, cbt_files):
        return _refused_batch(on_finished)
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
                            on_finished, ignored_count=ignored_count)

    dlg.finished.connect(lambda _: _on_confirm_done_cbt())
    dlg.show_nonmodal()


def _run_cbt_conversion(parent, cbt_files, directory, directories, callbacks, is_permanent,
                        on_finished=None, ignored_count=None, own_log=True):
    """own_log : voir _run_cbr_conversion."""
    claims = _start_batch_claims(parent, cbt_files)
    if claims is None:
        return _refused_batch(on_finished)
    new_files = []    # CBZ créés et fichiers renommés, rendus par on_finished
    renamed_paths = []    # fichiers seulement renommés (_chained_lot_data)
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
    renamed_epub      = [0]
    handled           = [0]    # fichiers réellement faits (voir le lot CBR)

    signals.update_filename.connect(prog.set_filename)
    signals.update_progress.connect(prog.set_progress)
    signals.update_page_bar.connect(lambda v, t: prog.set_page_progress(v, t))
    signals.update_thumb.connect(prog.set_thumbnail)

    def do_conversion():
        total = len(cbt_files)
        for idx, cbt_path in enumerate(cbt_files):
            handled[0] = idx
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

            # Fichier mal nommé : ZIP, RAR, 7z ou EPUB → simple renommage
            if real_type in ("CBZ", "CBR", "CB7", "EPUB"):
                label_map   = {"CBZ": "CBT → CBZ", "CBR": "CBT → CBR", "CB7": "CBT → CB7",
                               "EPUB": "CBT → EPUB"}
                counter_map = {"CBZ": renamed_cbz, "CBR": renamed_cbr, "CB7": renamed_cb7,
                               "EPUB": renamed_epub}
                new_path = _rename_misnamed(cbt_path, real_type, claims, conversion_errors)
                if new_path:
                    signals.update_page_bar.emit(100.0, label_map[real_type])
                    counter_map[real_type][0] += 1
                    last_done[0] = basename
                    renamed_entries.append(f"{basename} → {os.path.basename(new_path)}")
                    new_files.append(new_path)
                    renamed_paths.append(new_path)
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
        else:
            handled[0] = total    # boucle allée au bout (ni disque plein, ni annulation)

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        log_error = None
        if (conversion_errors or renamed_entries) and own_log:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "cbttocbz", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write(log_title("title_cbt"))
                    lf.write(log_line("date", date=now.strftime('%Y-%m-%d %H:%M')))
                    lf.write(log_line("directory", path=directory))
                    # Fichiers .cbt pris par le lot ; autres fichiers du
                    # dossier, s'il a été parcouru (menu, dépôt d'un dossier)
                    lf.write(log_line("files_handled", count=handled[0]))
                    if ignored_count is not None:
                        lf.write(log_line("files_ignored", count=ignored_count))
                    lf.write(log_line("converted", count=converted_count[0]))
                    # Ancienne extension → nouvelle (celle du format réel),
                    # seulement pour les renommages qui ont eu lieu
                    for kind, count in (("CBT→CBZ", renamed_cbz[0]), ("CBT→CBR", renamed_cbr[0]),
                                        ("CBT→CB7", renamed_cb7[0]), ("CBT→EPUB", renamed_epub[0])):
                        if count:
                            lf.write(log_line("renamed_kind", kind=kind, count=count))
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
            "renamed_epub":    renamed_epub[0],
            "errors_count":    len(conversion_errors),
            "has_errors":      bool(conversion_errors),
            "disk_full":       disk_full[0] if disk_full else None,
            "directory":       directory,
            "directories":     directories,
            "log_path":        log_path,
            "log_error":       log_error,
            **_chained_lot_data(prog, handled, conversion_errors, renamed_entries,
                                renamed_paths),
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

    batch_convert_pdf_to_cbz_confirm(parent, pdf_files, directory, callbacks,
                                     ignored_count=count_ignored_files([directory], pdf_files))


def batch_convert_pdf_to_cbz_confirm(parent, pdf_files, directory, callbacks, directories=None,
                                     on_finished=None, ignored_count=None):
    """Fenêtre de confirmation + progression + résumé PDF→CBZ.
    on_finished : voir _finish_batch."""
    pdf_files  = [os.path.normpath(f) for f in pdf_files]
    if _refuse_if_batch_running(parent, pdf_files):
        return _refused_batch(on_finished)
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
                            on_finished, ignored_count=ignored_count)

    dlg.finished.connect(lambda _: _on_confirm_done_pdf())
    dlg.show_nonmodal()


def _run_pdf_conversion(parent, pdf_files, directory, directories, callbacks, is_permanent, state,
                        on_finished=None, kept=None, ignored_count=None, own_log=True):
    """ignored_count : autres fichiers du dossier (count_ignored_files), None
    pour une liste de fichiers donnée (Bibliothèque, fichier seul).
    own_log : voir _run_cbr_conversion.
    kept : dict ou None. Donné, les PDF ne sont pas supprimés : chaque CBZ
    créé dont la source peut l'être y est noté, {chemin du CBZ: (nombre de
    fichiers, utils.file_signature du CBZ juste après l'écriture,
    utils.file_signature de la source avant la lecture de ses pages)}, pour la question
    « Supprimer l'original ? » de l'appelant (convert_single_file_to_cbz)."""
    claims = _start_batch_claims(parent, pdf_files)
    if claims is None:
        return _refused_batch(on_finished)
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
    handled           = [0]    # fichiers réellement faits (voir le lot CBR)

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
            handled[0] = idx
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
                    kept[cbz_path] = (cbz.count_to_check(), file_signature(cbz_path),
                                      source_signature)
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
        else:
            handled[0] = total    # boucle allée au bout (ni disque plein, ni annulation)

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        log_error = None
        if conversion_errors and own_log:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "pdftocbz", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write(log_title("title_pdf"))
                    lf.write(log_line("date", date=now.strftime('%Y-%m-%d %H:%M')))
                    lf.write(log_line("directory", path=directory))
                    lf.write(log_line("files_handled", count=handled[0]))
                    if ignored_count is not None:
                        lf.write(log_line("files_ignored", count=ignored_count))
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
            **_chained_lot_data(prog, handled, conversion_errors),
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

        ignored = count_ignored_files([actual_directory], img_files)
        if chosen_mode == _ImgModeDialog.MODE_ONE_PER_IMAGE:
            batch_convert_img_to_cbz_confirm(parent, img_files, actual_directory, callbacks,
                                             ignored_count=ignored)
        else:
            batch_convert_imgs_to_single_cbz(parent, img_files, actual_directory, callbacks,
                                             ignored_count=ignored)

    mode_dlg.finished.connect(lambda _: _on_mode_done())
    mode_dlg.show_nonmodal()


class _ChainSummaryDialog(AppWindowMixin, QDialog):
    """Résumé unique des lots enchaînés par la Bibliothèque (« Convertir en
    CBZ » sur plusieurs fichiers) : une section par lot, avec ce que montre
    son résumé habituel (nombre de fichiers convertis, dossiers, renommages,
    détail par extension des images, erreurs et lien vers son journal), et
    les mêmes textes. summaries : [(type, données du résumé)], type parmi
    cbr, cb7, cbt, pdf, img, et extensions et recompress (conversion
    complète en CBZ).

    overall : ce qui concerne toute la chaîne, None si rien à en dire.
    Conversion complète en CBZ (_finish_convert_all) : {"log_path",
    "log_error"} de son journal unique (lien sous les sections, ses lots
    n'ont pas de journal propre), "directories" (liens si aucun lot n'a eu
    lieu). Les deux chaînes, annoncés en tête : "stopped" (l'utilisateur a
    annulé, les lots suivants n'ont pas eu lieu), "unavailable" (fichiers
    d'un lot qui n'a pas pu tourner), "busy" (fichiers laissés de côté, pris
    par un autre lot). Les erreurs et le disque plein d'un lot sont dans sa
    section. title_key : titre de la fenêtre."""

    def __init__(self, parent, summaries, overall=None,
                 title_key="dialogs.batch_cbr.complete_title"):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title(title_key)
        self._sections = []
        self._overall = overall
        self.setFixedWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)
        # Conversion complète : interruption et lots impossibles, en tête
        self._overall_lines_lbl = None
        if overall and (overall.get("stopped") or overall.get("unavailable")
                        or overall.get("busy")):
            self._overall_lines_lbl = QLabel()
            self._overall_lines_lbl.setTextFormat(Qt.PlainText)
            self._overall_lines_lbl.setWordWrap(True)
            self._overall_lines_lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(self._overall_lines_lbl)
            if summaries:
                sep = QFrame()
                sep.setFrameShape(QFrame.HLine)
                layout.addWidget(sep)
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
                # Lien vers le journal et lignes du disque plein
                # (_log_label_text, qui échappe ce qu'il insère), comme dans
                # le résumé de chaque lot
                lbl.setTextFormat(Qt.RichText)
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
        directories = self._directories(summaries) or (overall or {}).get("directories") or []
        self._dirs_lbl = None
        if directories:
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            layout.addWidget(sep)
            # Titre des liens : les sections ne disent plus « dans le
            # répertoire : », leurs dossiers étant listés ici une seule fois
            self._dirs_lbl = QLabel()
            self._dirs_lbl.setTextFormat(Qt.PlainText)
            self._dirs_lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(self._dirs_lbl)
            _add_dir_links(layout, {"directories": directories})

        # Journal unique de la conversion complète
        self._overall_log_lbl = None
        if overall and (overall.get("log_path") or overall.get("log_error")):
            lbl = QLabel()
            # Lien vers le journal (_log_label_text, qui échappe ce qu'il insère)
            lbl.setTextFormat(Qt.RichText)
            lbl.setWordWrap(True)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("color: #4A9EFF;")
            log_path = overall.get("log_path")
            if log_path:
                lbl.setCursor(QCursor(Qt.PointingHandCursor))
                lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
                lbl.linkActivated.connect(lambda _l, p=log_path: _open_path(p))
                from modules.qt.utils import setup_path_label_context_menu
                setup_path_label_context_menu(lbl, lambda p=log_path: p,
                                              lambda p=log_path: _open_path(p))
            layout.addWidget(lbl)
            self._overall_log_lbl = lbl

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
    def _section_message(kind, data):
        """Première ligne d'une section, traduite à l'affichage : le lot et
        son nombre de fichiers faits (« sur {total} » s'il en manque, ou si
        des fichiers ont été seulement renommés). Jamais « dans le
        répertoire : » des résumés de chaque lot : les dossiers sont listés
        une seule fois, sous toutes les sections."""
        if kind == "extensions":
            return _("dialogs.batch_all.section_extensions", count=data["checked"])
        if kind == "recompress":
            return _("dialogs.batch_all.section_recompress", count=data["total"])
        renamed = any(data.get(k, 0) > 0
                      for k in ("renamed_cbz", "renamed_cbr", "renamed_cb7", "renamed_cbt",
                                "renamed_epub"))
        suffix = "_errors" if data.get("has_errors") or renamed else ""
        if kind == "img":
            return _(f"dialogs.batch_all.section_images{suffix}",
                     count=data["converted_count"], total=data["total"])
        return _(f"dialogs.batch_all.section_kind{suffix}", kind=kind.upper(),
                 count=data["converted_count"], total=data["total"])

    @staticmethod
    def _extra_lines(kind, data):
        """Lignes sous le message d'un lot, traduites à l'affichage :
        renommages (dialogs.batch_<type>.renamed_<format>_count), détail par
        extension des images (dialogs.batch_img.converted_by_ext), compteurs
        de la vérification des extensions (seulement ceux qui ne sont pas
        nuls)."""
        if kind == "extensions":
            if not (data["renamed_count"] or data["kept_count"] or data["unknown_count"]
                    or data["errors_count"]):
                return [_("dialogs.batch_extensions.all_correct")]
            lines = [_("dialogs.batch_extensions.well_named_count", count=data["well_named"])]
            for key, count in (("dialogs.batch_extensions.renamed_count", data["renamed_count"]),
                               ("dialogs.batch_extensions.not_renamed_count", data["kept_count"]),
                               ("dialogs.batch_extensions.unknown_count", data["unknown_count"])):
                if count:
                    lines.append(_(key, count=count))
            return lines
        if kind == "recompress":
            lines = [_("dialogs.batch_all.recompressed_count", count=data["recompressed_count"]),
                     _("dialogs.batch_all.already_optimal_count",
                       count=data["already_optimal_count"])]
            if data["renamed_count"]:
                lines.append(_("dialogs.batch_recompress.renamed_line").format(
                    renamed=data["renamed_count"]))
            return lines
        if kind == "img":
            by_ext = data.get("converted_by_ext") or {}
            return [_("dialogs.batch_img.converted_by_ext").format(
                        ext=ext.lstrip('.').upper(), count=by_ext[ext])
                    for ext in sorted(by_ext)]
        return [_(f"dialogs.batch_{kind}.{key}_count").format(count=data[key])
                for key in ("renamed_cbz", "renamed_cbr", "renamed_cb7", "renamed_cbt",
                            "renamed_epub")
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
        overall = self._overall
        if self._overall_lines_lbl is not None:
            lines = []
            if overall.get("stopped"):
                lines.append(_("dialogs.batch_all.stopped"))
            if overall.get("unavailable"):
                lines.append(_("dialogs.batch_all.unavailable", count=overall["unavailable"]))
            if overall.get("busy"):
                lines.append(_("dialogs.batch_all.busy", count=overall["busy"]))
            self._overall_lines_lbl.setText("\n".join(lines))
            self._overall_lines_lbl.setFont(font)
            self._overall_lines_lbl.setMinimumHeight(
                self._overall_lines_lbl.heightForWidth(self.width() - 40))
        if self._overall_log_lbl is not None:
            self._overall_log_lbl.setText(_log_label_text(overall, see_log))
            self._overall_log_lbl.setFont(font)
        if self._dirs_lbl is not None:
            self._dirs_lbl.setText(_("dialogs.batch_all.directories"))
            self._dirs_lbl.setFont(font)
        for section in self._sections:
            kind, data = section["kind"], section["data"]
            section["msg"].setText(self._section_message(kind, data))
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


def show_chain_summary(parent, summaries, overall=None,
                       title_key="dialogs.batch_cbr.complete_title"):
    """Résumé unique d'une chaîne de lots (_ChainSummaryDialog) ; overall :
    voir _ChainSummaryDialog (montré même sans lot fait : un journal, une
    interruption ou des fichiers laissés de côté sont à connaître)."""
    if summaries or overall:
        _ChainSummaryDialog(parent, summaries, overall, title_key).show_nonmodal()


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
        return _refused_batch(on_finished)
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
                                     on_finished=None, ignored_count=None):
    """Fenêtre de confirmation + progression + résumé IMG→CBZ.
    on_finished : voir _finish_batch."""
    img_files  = [os.path.normpath(f) for f in img_files]
    if _refuse_if_batch_running(parent, img_files):
        return _refused_batch(on_finished)
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
                            on_finished, ignored_count=ignored_count)

    dlg.finished.connect(lambda _: _on_confirm_done_img())
    dlg.show_nonmodal()


def _run_img_conversion(parent, img_files, directory, directories, callbacks, is_permanent,
                        on_finished=None, kept=None, ignored_count=None, own_log=True):
    """kept, ignored_count : voir _run_pdf_conversion ; own_log : voir
    _run_cbr_conversion."""
    claims = _start_batch_claims(parent, img_files)
    if claims is None:
        return _refused_batch(on_finished)
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

    handled = [0]    # fichiers réellement faits (voir le lot CBR)

    def do_conversion():
        total = len(img_files)
        for idx, img_path in enumerate(img_files):
            handled[0] = idx
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
                    kept[cbz_path] = (cbz.count_to_check(), file_signature(cbz_path),
                                      source_signature)
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
        else:
            handled[0] = total    # boucle allée au bout (ni disque plein, ni annulation)

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)

        log_path = None
        log_error = None
        if conversion_errors and own_log:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "imgtocbz", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write(log_title("title_img"))
                    lf.write(log_line("date", date=now.strftime('%Y-%m-%d %H:%M')))
                    lf.write(log_line("directory", path=directory))
                    lf.write(log_line("files_handled", count=handled[0]))
                    if ignored_count is not None:
                        lf.write(log_line("files_ignored", count=ignored_count))
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
            **_chained_lot_data(prog, handled, conversion_errors),
        }
        _end_batch(parent, "img", show_batch_img_summary, summary_data, callbacks,
                   on_finished, new_files)

    signals.conversion_done.connect(on_done)
    _start_batch_thread(do_conversion, parent, claims, prog, signals)


def show_batch_img_summary(parent, data, callbacks):
    dlg = _ImgSummaryDialog(parent, data)
    dlg.show_nonmodal()


def batch_convert_imgs_to_single_cbz(parent, img_files, directory, callbacks, on_finished=None,
                                     ignored_count=None):
    """Regroupe toutes les images du répertoire en un seul fichier CBZ
    (« dossier/nom du dossier.cbz »). on_finished : voir _finish_batch ;
    ignored_count : voir _run_pdf_conversion."""
    img_files  = [os.path.normpath(f) for f in img_files]
    if _refuse_if_batch_running(parent, img_files):
        return _refused_batch(on_finished)
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
        _run_imgs_to_single_cbz(parent, img_files, directory, callbacks, is_permanent, on_finished,
                                ignored_count=ignored_count)

    dlg.finished.connect(lambda _: _on_confirm_done_single())
    dlg.show_nonmodal()


def _run_imgs_to_single_cbz(parent, img_files, directory, callbacks, is_permanent,
                            on_finished=None, ignored_count=None, own_log=True):
    """own_log : voir _run_cbr_conversion."""
    claims = _start_batch_claims(parent, img_files)
    if claims is None:
        return _refused_batch(on_finished)
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

    handled = [0]    # images réellement faites (voir le lot CBR)

    def do_conversion():
        # Images réservées par un autre lot encore en cours : laissées de côté
        # (journal), elles pourraient être supprimées par ce dernier.
        free_files = [p for p in img_files if _claim_file(claims, p, conversion_errors)]
        refused = len(img_files) - len(free_files)    # consignées en erreur, donc faites
        handled[0] = refused
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
                    handled[0] = refused + idx
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
                # Toutes les images lues (une annulation ou un disque plein
                # sortent de la boucle par une exception)
                handled[0] = refused + total

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
        if conversion_errors and own_log:
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
                    lf.write(log_line("files_handled", count=handled[0]))
                    if ignored_count is not None:
                        lf.write(log_line("files_ignored", count=ignored_count))
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
            **_chained_lot_data(prog, handled, conversion_errors),
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


def recompress_files(directories, natural_sort_key):
    """Fichiers du lot de recompression lancé seul, dans les dossiers
    `directories` (sous-dossiers compris, liens et jonctions jamais suivis) :
    CBZ, CBR, CB7 et CBT, triés par nom. Le lot lit le format réel de chacun :
    un CBZ mal nommé est renommé puis recompressé, une vraie archive d'un
    autre format ignorée."""
    found = []
    for directory in directories:
        for dirpath, _subdirs, filenames in walk_without_links(directory):
            found.extend(os.path.join(dirpath, name) for name in filenames
                         if name.lower().endswith(('.cbz', '.cbr', '.cb7', '.cbt')))
    found.sort(key=lambda f: natural_sort_key(os.path.basename(f).lower()))
    return found


def batch_recompress_cbz(parent, callbacks, directory=None):
    """Menu Fichier > Batch : recompression des CBZ d'un dossier."""
    if directory is None:
        _ask_batch_directory(parent, "dialogs.batch_recompress.select_directory_title",
                             lambda d: batch_recompress_cbz(parent, callbacks, d))
        return
    files = recompress_files([directory], callbacks['natural_sort_key'])
    if not files:
        ErrorDialog(parent, "dialogs.batch_recompress.no_files_title",
                    lambda d=directory: _("dialogs.batch_recompress.no_files_message").format(
                        directory=d)).show_nonmodal()
        return
    batch_recompress_cbz_confirm(parent, files, directory, callbacks,
                                 ignored_count=count_ignored_files([directory], files))


def batch_recompress_cbz_confirm(parent, archive_files, directory, callbacks, directories=None,
                                 ignored_count=None):
    """Fenêtre de confirmation + progression + résumé pour la recompression batch des CBZ.
    ignored_count : voir _run_pdf_conversion."""
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
        _run_recompress(parent, archive_files, directory, directories, callbacks, level,
                        ignored_count=ignored_count)

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


def _run_recompress(parent, archive_files, directory, directories, callbacks, level,
                    on_finished=None, ignored_count=None, own_log=True):
    """ignored_count : autres fichiers du dossier (count_ignored_files), None
    pour une liste de fichiers donnée. Les archives prises par le lot qui ne
    sont pas des CBZ (vrais CBR, CB7, CBT) s'y ajoutent : une seule notion de
    fichier ignoré, dans le journal comme dans le résumé.
    on_finished, own_log : étape de la conversion complète (voir
    _run_cbr_conversion)."""
    from modules.qt.utils import zip_compression_kwargs

    claims = _start_batch_claims(parent, archive_files)
    if claims is None:
        return _refused_batch(on_finished)
    new_files = []    # CBZ recompressés et fichiers renommés, rendus par on_finished
    renamed_paths = []    # fichiers renommés (_chained_lot_data)
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
    renamed_kinds       = {}    # « CBR→CBZ » : nombre de fichiers renommés ainsi
    handled             = [0]    # fichiers réellement faits (voir le lot CBR)

    signals.update_filename.connect(prog.set_filename)
    signals.update_progress.connect(prog.set_progress)
    signals.update_thumb.connect(prog.set_thumbnail)

    def do_recompress():
        total = len(archive_files)
        zip_kwargs = zip_compression_kwargs(level)
        for idx, path in enumerate(archive_files):
            handled[0] = idx
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
                # Vraie archive d'un autre format : ignorée, comptée avec les
                # autres fichiers ignorés
                signals.update_thumb.emit(None)
                ignored_entries.append(basename)
                continue

            # Vignette : première page du CBZ, celle que montre son ouverture
            _emit_first_page_thumbnail(path, signals, callbacks)

            # Fichier mal nommé (extension ≠ .cbz mais contenu réellement ZIP) : renommer en .cbz
            target_path = path
            if os.path.splitext(path)[1].lower() != ".cbz":
                new_path = _rename_misnamed(path, "CBZ", claims, conversion_errors)
                if not new_path:
                    continue
                renamed_entries.append(f"{basename} → {os.path.basename(new_path)}")
                kind = os.path.splitext(path)[1].lstrip(".").upper() + "→CBZ"
                renamed_kinds[kind] = renamed_kinds.get(kind, 0) + 1
                new_files.append(new_path)
                renamed_paths.append(new_path)
                target_path = new_path

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
        else:
            handled[0] = total    # boucle allée au bout (ni disque plein, ni annulation)

        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        prog.close()
        _unregister_batch(prog)
        if recompressed_paths:
            _update_open_comics_compression(parent, recompressed_paths, level)

        # Fichiers passés par la recompression (moins que le lot s'il a été
        # annulé) ; ignorés : vraies archives d'un autre format et autres
        # fichiers du dossier, une seule notion, la même dans le journal et
        # dans le résumé
        handled_count = len(recompressed_paths) + already_optimal_count[0] + len(conversion_errors)
        ignored_total = len(ignored_entries) + (ignored_count or 0)
        log_path = None
        log_error = None
        if (conversion_errors or renamed_entries) and own_log:
            try:
                from datetime import datetime
                now = datetime.now()
                mosaicview_temp = callbacks['get_mosaicview_temp_dir']()
                log_path = unique_log_path(mosaicview_temp, "recompress", now)
                with open(log_path, 'w', encoding='utf-8') as lf:
                    lf.write(log_title("title_recompress"))
                    lf.write(log_line("date", date=now.strftime('%Y-%m-%d %H:%M')))
                    lf.write(log_line("directory", path=directory))
                    lf.write(log_line("files_handled", count=handled_count))
                    if ignored_count is not None or ignored_entries:
                        lf.write(log_line("files_ignored", count=ignored_total))
                    lf.write(log_line("recompressed", count=len(recompressed_paths)))
                    lf.write(log_line("already_optimal", count=already_optimal_count[0]))
                    # Ancienne extension → nouvelle, seulement pour les
                    # renommages qui ont eu lieu
                    for kind in sorted(renamed_kinds):
                        lf.write(log_line("renamed_kind", kind=kind, count=renamed_kinds[kind]))
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
            "total":               handled_count,
            "recompressed_count":  len(recompressed_paths),
            "already_optimal_count": already_optimal_count[0],
            "ignored_count":       ignored_total,
            "renamed_count":       len(renamed_entries),
            "errors_count":        len(conversion_errors),
            "has_errors":          bool(conversion_errors),
            "disk_full":           disk_full[0] if disk_full else None,
            "directory":           directory,
            "directories":         directories,
            "log_path":            log_path,
            "log_error":           log_error,
            "renamed_kinds":       dict(renamed_kinds),
            **_chained_lot_data(prog, handled, conversion_errors, renamed_entries,
                                renamed_paths),
        }
        new_files.extend(p for p in recompressed_paths if p not in renamed_paths)
        _end_batch(parent, "recompress",
                   lambda p, data, _callbacks: show_batch_recompress_summary(p, data),
                   summary_data, callbacks, on_finished, new_files)

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
        )
        if data["renamed_count"]:
            message += "\n" + _("dialogs.batch_recompress.renamed_line").format(
                renamed=data["renamed_count"])
        # Fichiers ignorés à part, après une ligne vide : ils ne font pas
        # partie des fichiers traités énumérés au-dessus
        if data["ignored_count"]:
            message += "\n\n" + _("dialogs.batch_recompress.ignored_line").format(
                ignored=data["ignored_count"])
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


# ═══════════════════════════════════════════════════════════════════════════════
# Vérification des extensions
# ═══════════════════════════════════════════════════════════════════════════════
# Lit le format réel de chaque comic, livre numérique et archive du dossier
# (misnamed_archives.check_extension), puis propose de renommer ceux dont
# l'extension ne correspond pas : rien n'est renommé sans validation. La
# vérification tourne dans le thread du lot ; la question, les renommages,
# le suivi des fichiers renommés (panneaux, récents, marque-pages), le
# journal et le résumé se font sur le thread de l'interface.

# Progression envoyée au plus tous les dixièmes de seconde : des dizaines de
# milliers de petites lectures inonderaient sinon le thread de l'interface
_EXTENSION_PROGRESS_INTERVAL = 0.1


def extension_check_files(directories):
    """Fichiers des dossiers `directories` (sous-dossiers compris, liens et
    jonctions jamais suivis) dont l'extension est vérifiée par le lot
    (misnamed_archives.checked_extension), triés par nom."""
    from modules.qt.archive_names import natural_key
    from modules.qt.misnamed_archives import checked_extension
    files = []
    for directory in directories:
        for root, _subdirs, filenames in walk_without_links(directory):
            files += [os.path.join(root, name) for name in filenames
                      if checked_extension(name) is not None]
    files.sort(key=lambda f: natural_key(os.path.basename(f).lower()))
    return files


def batch_check_extensions(parent, callbacks, directory=None):
    """Menu Fichier > Batch : vérification des extensions d'un dossier."""
    if directory is None:
        _ask_batch_directory(parent, "dialogs.batch_extensions.select_directory_title",
                             lambda d: batch_check_extensions(parent, callbacks, d))
        return
    files = extension_check_files([directory])
    if not files:
        InfoDialog(parent, "dialogs.batch_extensions.no_files_title",
                   lambda d=directory: _("dialogs.batch_extensions.no_files_message",
                                         directory=d)).show_nonmodal()
        return
    run_extension_check(parent, files, directory, callbacks,
                        ignored_count=count_ignored_files([directory], files))


def _check_extensions_of(files, found, unknown, well_named, handled, signals):
    """Corps du thread du lot : vérifie chaque fichier de `files`.
    found : ExtensionCheck des fichiers mal nommés ; unknown : chemins des
    fichiers dont le format n'est pas reconnu (ou illisibles) ;
    well_named[0] : nombre de fichiers bien nommés ; handled[0] : fichiers
    vérifiés (moins que le lot s'il est annulé, _raise_if_batch_stopped au
    début de chaque fichier)."""
    import time
    from modules.qt.misnamed_archives import check_extension
    total = len(files)
    last_emit = None
    for idx, path in enumerate(files):
        handled[0] = idx
        _raise_if_batch_stopped()
        now = time.monotonic()
        if last_emit is None or now - last_emit >= _EXTENSION_PROGRESS_INTERVAL \
                or idx == total - 1:
            last_emit = now
            signals.update_filename.emit(os.path.basename(path))
            signals.update_progress.emit(
                lambda c=idx + 1, t=total: _("dialogs.batch_extensions.checking_progress",
                                             current=c, total=t))
        check = check_extension(path)
        if check is None or check.real_kind is None:
            unknown.append(path)
        elif check.new_extension:
            found.append(check)
        else:
            well_named[0] += 1
    handled[0] = total


def run_extension_check(parent, files, directory, callbacks, directories=None,
                        ignored_count=None, on_finished=None):
    """Vérifie les extensions de `files` en arrière-plan (fenêtre de
    progression, Annuler), puis pose la question des renommages
    (_ask_extension_renames) s'il y a des fichiers mal nommés, sinon montre
    le résumé. Annulé : même suite, avec les fichiers déjà vérifiés.
    directories : dossiers déposés (liens du résumé) ; ignored_count : autres
    fichiers des dossiers (count_ignored_files).
    on_finished : étape de la conversion complète en CBZ (_run_convert_all) :
    ni question ni journal ni résumé, voir _rename_all_misnamed."""
    files = [os.path.normpath(f) for f in files]
    directory = os.path.normpath(directory) if directory else directory
    if directories:
        directories = [os.path.normpath(d) for d in directories]
    claims = _start_batch_claims(parent, files)
    if claims is None:
        return _refused_batch(on_finished)
    prog = _ProgressDialog(parent, "dialogs.batch_extensions.checking_title",
                           has_page_bar=False, has_thumbnail=False)
    prog.show_nonmodal()
    QApplication.processEvents()

    signals = _ThreadSignals()
    _register_batch(prog, signals)
    found, unknown = [], []
    well_named = [0]
    handled = [0]

    signals.update_filename.connect(prog.set_filename)
    signals.update_progress.connect(prog.set_progress)

    def do_check():
        _check_extensions_of(files, found, unknown, well_named, handled, signals)
        signals.conversion_done.emit()

    def on_done():
        prog.mark_done()
        cancelled = prog.cancel_event.is_set()
        prog.close()
        _unregister_batch(prog)
        result = {
            "found":         list(found),
            "unknown":       list(unknown),
            "well_named":    well_named[0],
            "checked":       handled[0],
            "cancelled":     cancelled,
            "directory":     directory,
            "directories":   directories,
            "ignored_count": ignored_count,
        }
        if on_finished is not None:
            _rename_all_misnamed(result, on_finished)
        elif result["found"]:
            _ask_extension_renames(parent, result, callbacks)
        else:
            _finish_extension_check(parent, result, [], [], [], callbacks)

    signals.conversion_done.connect(on_done)
    _start_batch_thread(do_check, parent, claims, prog, signals)


def _app_panels():
    """Panneaux de la fenêtre principale (MainWindow._all_panels), quelle que
    soit la fenêtre qui a lancé le lot."""
    panels = []
    for window in QApplication.topLevelWidgets():
        all_panels = getattr(window, "_all_panels", None)
        if callable(all_panels):
            panels += all_panels()
    return panels


def _ask_extension_renames(parent, result, callbacks):
    """Liste des fichiers mal nommés (fenêtre commune MisnamedArchiveDialog,
    forme liste à cases à cocher, toutes cochées au départ, extension propre
    à chacun) ; les fichiers ouverts dans un panneau y sont marqués et
    expliqués : renommés quand même, le panneau suit leur nouveau nom
    (_follow_renamed_files). Rien n'est renommé sans un clic sur « Renommer
    la sélection », et seuls les fichiers cochés le sont."""
    from modules.qt.misnamed_archive_dialog_qt import MisnamedArchiveDialog
    open_paths = [panel._state.current_file for panel in _app_panels()
                  if panel._state.current_file]
    message_key = ("dialogs.batch_extensions.question_cancelled_message" if result["cancelled"]
                   else "dialogs.batch_extensions.question_message")
    dlg = MisnamedArchiveDialog(
        parent, [(c.path, c.real_kind, c.new_extension) for c in result["found"]],
        message_key, "dialogs.misnamed_archive.not_now_button", as_list=True,
        marked_paths=open_paths, warning_key="dialogs.batch_extensions.open_warning",
        checkable=True)
    # chosen_files : attribut Python posé avant la réponse, lisible après la
    # fermeture de la fenêtre
    dlg.ask_async(lambda answer: _on_extension_renames_answer(
        parent, result, callbacks, answer[0], {entry[0] for entry in dlg.chosen_files}))


def _on_extension_renames_answer(parent, result, callbacks, choice, chosen_paths=None):
    """Réponse à la question : renommage des fichiers cochés (choix
    « rename », `chosen_paths` : leurs chemins ; None = tous), les autres
    laissés tels quels ; puis journal et résumé. Les panneaux, fichiers
    utilisés et fichiers ouverts sont relus ici : la question est non
    modale."""
    errors = []
    renamed = []
    if choice == "rename":
        to_rename, kept = _split_chosen(result["found"], chosen_paths)
        panels = _app_panels()
        renamed = _rename_checked_files(to_rename,
                                        _files_in_use_except_documents(panels), errors)
        _follow_renamed_files(panels, renamed)
    else:
        kept = list(result["found"])
    _finish_extension_check(parent, result, renamed, kept, errors, callbacks)


def _rename_all_misnamed(result, on_finished):
    """Étape « extensions » de la conversion complète en CBZ
    (_run_convert_all) : chaque fichier mal nommé est renommé sans question,
    avec les mêmes règles que les fichiers cochés (_rename_checked_files :
    revérification, premier nom libre, jamais un fichier utilisé ni pris par
    un lot ; panneaux, fichiers récents et marque-pages suivis). Vérification
    annulée : rien n'est renommé, et la conversion complète s'arrête
    (`cancelled`). Rend à la chaîne on_finished(nouveaux chemins,
    ("extensions", données)) ; aucun journal ni résumé propre."""
    errors, renamed = [], []
    found = list(result["found"])
    if result["cancelled"]:
        kept = found
    else:
        kept = []
        panels = _app_panels()
        renamed = _rename_checked_files(found, _files_in_use_except_documents(panels), errors)
        _follow_renamed_files(panels, renamed)
    data = {
        "checked":       result["checked"],
        "well_named":    result["well_named"],
        "renamed_count": len(renamed),
        "kept_count":    len(kept),
        "unknown_count": len(result["unknown"]),
        "errors_count":  len(errors),
        "has_errors":    bool(errors),
        "disk_full":     None,
        "directory":     result["directory"],
        "directories":   result["directories"],
        "log_path":      None,
        "log_error":     None,
        "cancelled":     result["cancelled"],
        "handled":       result["checked"],
        "renamed":       renamed,
        "kept":          kept,
        "unknown":       list(result["unknown"]),
        "error_lines":   errors,
    }
    on_finished([new_path for _check, new_path in renamed], ("extensions", data))


def _split_chosen(found, chosen_paths):
    """(fichiers à renommer, fichiers laissés tels quels) parmi `found`
    (ExtensionCheck), d'après les chemins cochés `chosen_paths` (None :
    tous à renommer)."""
    if chosen_paths is None:
        return list(found), []
    to_rename = [check for check in found if check.path in chosen_paths]
    kept = [check for check in found if check.path not in chosen_paths]
    return to_rename, kept


def _files_in_use_except_documents(panels):
    """Fichiers que l'application lit ou va lire (chargement en cours,
    fichiers en attente, archive d'une page illisible, archive ou PDF en
    cours d'ajout), sans le comic ouvert d'un panneau au repos : ce lot le
    renomme, et le panneau suit son nouveau nom. Le comic d'un panneau en
    pleine opération (operation_running) reste refusé."""
    from modules.qt import files_in_use
    from modules.qt.utils import is_same_path
    in_use = list(files_in_use.held_paths())
    for panel in panels:
        paths = list(panel._files_in_use())
        current = panel._state.current_file
        if current and not panel._state.operation_running():
            # Une seule fois : présent aussi parmi les fichiers d'un
            # chargement en cours, il reste refusé
            for i, path in enumerate(paths):
                if is_same_path(path, current):
                    del paths[i]
                    break
        in_use += paths
    return in_use


def _rename_checked_files(found, files_in_use, errors):
    """Renomme chaque fichier mal nommé de `found` (ExtensionCheck) avec sa
    nouvelle extension (misnamed_archives.rename_with_extension : premier nom
    libre, jamais d'écrasement, jamais un fichier utilisé ni pris par un
    lot). Chaque fichier est revérifié juste avant : modifié, déplacé ou
    supprimé depuis la vérification, il est laissé tel quel. Rend
    [(ExtensionCheck, nouveau chemin)] ; chaque refus ou échec ajoute une
    ligne à `errors`, en latin (_wt : thread de l'interface, journal lu dans
    le Bloc-notes)."""
    from modules.qt.misnamed_archives import (RenameRefusedError, check_extension,
                                              rename_with_extension)
    renamed = []
    for check in found:
        basename = os.path.basename(check.path)
        now = check_extension(check.path)
        if now is None or now.new_extension != check.new_extension:
            errors.append(f"{basename}: " + _wt("messages.errors.batch_extension_changed"))
            continue
        try:
            new_path = rename_with_extension(check.path, check.extension, check.new_extension,
                                             files_in_use=files_in_use)
        except RenameRefusedError as e:
            errors.append(f"{basename}: " + _wt(e.key, name=e.name))
            continue
        except Exception as e:
            errors.append(f"{basename}: {_failure_text(e)}")
            continue
        renamed.append((check, new_path))
    return renamed


def _follow_renamed_files(panels, renamed):
    """Fichiers renommés ([(ExtensionCheck, nouveau chemin)]) : les panneaux
    qui les ont ouverts prennent leur nouveau nom (titre, barre de statut ;
    un enregistrement écrit sous ce nom), et les fichiers récents et
    marque-pages les suivent."""
    from modules.qt import recent_files
    from modules.qt.utils import is_same_path
    cfg = get_config_manager()
    for check, new_path in renamed:
        recent_files.rename_in_recent_files(check.path, new_path)
        page = cfg.get_bookmark(check.path)
        if page is not None:
            cfg.remove_bookmark(check.path, save=False)
            cfg.set_bookmark(new_path, page)
        for panel in panels:
            state = panel._state
            if state.current_file and is_same_path(state.current_file, check.path):
                state.current_file = new_path
                panel._refresh_title()
                panel._update_status_bar()
    if renamed and panels:
        main_window = getattr(panels[0], "_main_window", None)
        if hasattr(main_window, "_sync_recent_menus"):
            main_window._sync_recent_menus()


def _write_extensions_log(callbacks, data, renamed, kept, unknown, errors):
    """Journal du lot (Log_extensions_*.txt) : compteurs, puis fichiers
    renommés, fichiers mal nommés laissés tels quels, fichiers au format non
    reconnu et erreurs, chacun par son chemin complet (il faut pouvoir les
    retrouver dans les sous-dossiers). Rend son chemin."""
    from datetime import datetime
    from modules.qt.misnamed_archives import rename_label
    now = datetime.now()
    log_path = unique_log_path(callbacks['get_mosaicview_temp_dir'](), "extensions", now)
    kinds = {}
    for check, _new_path in renamed:
        label = rename_label(check.extension, check.new_extension)
        kinds[label] = kinds.get(label, 0) + 1
    sections = (
        ("renamed_files", [f"{check.path} → {os.path.basename(new_path)}"
                           for check, new_path in renamed]),
        ("not_renamed_files", [f"{check.path} → {check.new_extension}" for check in kept]),
        ("unknown_format_files", list(unknown)),
        ("error_details", list(errors)),
    )
    with open(log_path, 'w', encoding='utf-8') as lf:
        lf.write(log_title("title_extensions"))
        lf.write(log_line("date", date=now.strftime('%Y-%m-%d %H:%M')))
        lf.write(log_line("directory", path=data["directory"]))
        lf.write(log_line("files_handled", count=data["checked"]))
        if data["ignored_count"] is not None:
            lf.write(log_line("files_ignored", count=data["ignored_count"]))
        lf.write(log_line("well_named", count=data["well_named"]))
        # Ancienne extension → nouvelle, seulement pour les renommages qui
        # ont eu lieu
        for kind in sorted(kinds):
            lf.write(log_line("renamed_kind", kind=kind, count=kinds[kind]))
        if kept:
            lf.write(log_line("not_renamed", count=len(kept)))
        if unknown:
            lf.write(log_line("unknown_format", count=len(unknown)))
        lf.write(log_line("errors", count=len(errors)))
        for header, lines in sections:
            if not lines:
                continue
            lf.write(f"\n{'='*60}\n")
            lf.write(log_line(header))
            lf.write(f"{'='*60}\n\n")
            for line in lines:
                lf.write(f"  - {log_text(line)}\n")
    return log_path


def _finish_extension_check(parent, result, renamed, kept, errors, callbacks):
    """Fin du lot : journal (dès qu'il y a autre chose que des fichiers bien
    nommés) et résumé."""
    log_path = None
    log_error = None
    if renamed or kept or result["unknown"] or errors:
        try:
            log_path = _write_extensions_log(callbacks, result, renamed, kept,
                                             result["unknown"], errors)
        except Exception as e:
            # Journal impossible à écrire (dossier temporaire plein ou
            # protégé) : la raison est affichée dans le résumé.
            log_path = None
            log_error = exception_text(e)
    summary_data = {
        "checked":         result["checked"],
        "well_named":      result["well_named"],
        "renamed_count":   len(renamed),
        "kept_count":      len(kept),
        "unknown_count":   len(result["unknown"]),
        "errors_count":    len(errors),
        "has_errors":      bool(errors),
        "directory":       result["directory"],
        "directories":     result["directories"],
        "log_path":        log_path,
        "log_error":       log_error,
    }
    _ExtensionsSummaryDialog(parent, summary_data).show_nonmodal()


class _ExtensionsSummaryDialog(AppWindowMixin, QDialog):
    """Résumé de la vérification des extensions : fichiers vérifiés, bien
    nommés, renommés, laissés tels quels, au format non reconnu, erreurs et
    lien vers le journal."""

    def __init__(self, parent, data):
        super().__init__(parent)
        self._setup_window()
        self.set_window_title("dialogs.batch_extensions.complete_title")
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

        self._counts_lbl = QLabel()
        self._counts_lbl.setTextFormat(Qt.PlainText)
        self._counts_lbl.setWordWrap(True)
        self._counts_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._counts_lbl)

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
                self._error_lbl.linkActivated.connect(lambda _l, p=log_path: _open_path(p))
                from modules.qt.utils import setup_path_label_context_menu
                setup_path_label_context_menu(self._error_lbl, lambda p=log_path: p,
                                              lambda p=log_path: _open_path(p))
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

    def _count_lines(self):
        """Lignes des compteurs, seulement ceux qui ne sont pas nuls."""
        data = self._data
        if not (data["renamed_count"] or data["kept_count"] or data["unknown_count"]
                or data["errors_count"]):
            return [_("dialogs.batch_extensions.all_correct")]
        lines = [_("dialogs.batch_extensions.well_named_count", count=data["well_named"])]
        for key, count in (("dialogs.batch_extensions.renamed_count", data["renamed_count"]),
                           ("dialogs.batch_extensions.not_renamed_count", data["kept_count"]),
                           ("dialogs.batch_extensions.unknown_count", data["unknown_count"])):
            if count:
                lines.append(_(key, count=count))
        return lines

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
        self._msg_lbl.setText(_("dialogs.batch_extensions.complete_message", count=data["checked"]))
        self._msg_lbl.setFont(font)
        self._msg_lbl.setMinimumHeight(self._msg_lbl.heightForWidth(440))
        self._counts_lbl.setText("\n".join(self._count_lines()))
        self._counts_lbl.setFont(font)
        self._counts_lbl.setMinimumHeight(self._counts_lbl.heightForWidth(440))

        if self._error_lbl is not None:
            see_log = _("dialogs.see_log")
            if data.get("has_errors"):
                error_text = _("dialogs.batch_extensions.errors_count", count=data["errors_count"])
                self._error_lbl.setText(_log_label_text(data, f"{error_text} — {see_log}",
                                                        error_text))
            else:
                self._error_lbl.setText(_log_label_text(data, see_log))
            self._error_lbl.setFont(font)

        self._ok_btn.setText(_("buttons.ok"))
        self._ok_btn.setFont(font)
        self._ok_btn.setStyleSheet(btn_style)


# ═══════════════════════════════════════════════════════════════════════════════
# Conversion complète en CBZ (« Conversions et compression des BD ») :
# vérification des extensions, puis lots CBR, CB7, CBT, PDF et images
# isolées → CBZ, puis recompression des CBZ, enchaînés
# ═══════════════════════════════════════════════════════════════════════════════

# Extension → lot de la conversion complète (images : IMAGE_EXTS)
_CONVERT_ALL_EXTENSIONS = {".cbr": "cbr", ".cb7": "cb7", ".cbt": "cbt", ".pdf": "pdf"}
CONVERT_ALL_KINDS = ("cbr", "cb7", "cbt", "pdf", "img")
# Conversions dans le journal : libellé fixé par le code, comme les renommages
_CONVERT_ALL_LABELS = {"cbr": "CBR→CBZ", "cb7": "CB7→CBZ", "cbt": "CBT→CBZ", "pdf": "PDF→CBZ"}
# Renommages faits par un lot de conversion (fichier mal nommé) : clé du
# compteur de son résumé → extension donnée
_LOT_RENAMES = (("renamed_cbz", "CBZ"), ("renamed_cbr", "CBR"), ("renamed_cb7", "CB7"),
                ("renamed_cbt", "CBT"), ("renamed_epub", "EPUB"))


def convert_all_files(directories, natural_sort_key):
    """Fichiers de chaque lot de conversion des dossiers `directories`
    (sous-dossiers compris, liens et jonctions jamais suivis) :
    {type (CONVERT_ALL_KINDS): [chemins]}, chaque liste triée par nom comme
    celle du lot lancé seul."""
    found = {kind: [] for kind in CONVERT_ALL_KINDS}
    for directory in directories:
        for dirpath, _subdirs, filenames in walk_without_links(directory):
            for name in filenames:
                lower = name.lower()
                kind = _CONVERT_ALL_EXTENSIONS.get(os.path.splitext(lower)[1])
                if kind is None and lower.endswith(IMAGE_EXTS):
                    kind = "img"
                if kind is not None:
                    found[kind].append(os.path.normpath(os.path.join(dirpath, name)))
    for files in found.values():
        files.sort(key=lambda f: natural_sort_key(os.path.basename(f).lower()))
    return found


def recompress_step_files(directories, natural_sort_key):
    """CBZ des dossiers `directories` (sous-dossiers compris, liens et
    jonctions jamais suivis), triés par nom : fichiers de l'étape de
    recompression de la conversion complète. Les CBR, CB7 et CBT n'y sont
    pas : ceux qui restent après les conversions sont de vraies archives de
    leur format, que la recompression ignorerait."""
    found = []
    for directory in directories:
        for dirpath, _subdirs, filenames in walk_without_links(directory):
            found.extend(os.path.normpath(os.path.join(dirpath, name))
                         for name in filenames if name.lower().endswith(".cbz"))
    found.sort(key=lambda f: natural_sort_key(os.path.basename(f).lower()))
    return found


def batch_convert_all_to_cbz(parent, callbacks, directory=None):
    """Menu Fichier > Batch > « Conversions et compression des BD » : demande
    le dossier, puis ouvre la fenêtre de choix des traitements, la même
    qu'au dépôt d'un dossier (drop_handler_qt.show_batch_choice)."""
    if directory is None:
        _ask_batch_directory(parent, "dialogs.batch_all.select_directory_title",
                             lambda d: batch_convert_all_to_cbz(parent, callbacks, d))
        return
    from modules.qt.drop_handler_qt import show_batch_choice
    show_batch_choice(parent, [directory], callbacks, from_menu=True)


def convert_all_to_cbz(parent, dirs, callbacks, directories=None, steps=None):
    """Conversion complète en CBZ des dossiers `dirs` (`directories` : liens
    du résumé) : question du mode des images s'il y en a (_ImgModeDialog),
    une seule confirmation, puis les lots enchaînés (_run_convert_all).
    steps : traitements cochés dans la fenêtre de choix (types de
    batch_drop_dialog_qt.STEP_KINDS), None pour tous ; les autres ne
    comptent ni dans la confirmation ni dans la chaîne."""
    dirs = [os.path.normpath(d) for d in dirs]
    if directories:
        directories = [os.path.normpath(d) for d in directories]
    steps = frozenset(steps) if steps is not None else None

    def chosen(kind):
        return steps is None or kind in steps

    check_files = extension_check_files(dirs) if chosen("extensions") else []
    found = convert_all_files(dirs, callbacks['natural_sort_key'])
    for kind in CONVERT_ALL_KINDS:
        if not chosen(kind):
            found[kind] = []
    # CBZ déjà présents, annoncés pour la recompression (ceux que les
    # conversions écriront le sont déjà au taux par défaut)
    cbz_files = (recompress_step_files(dirs, callbacks['natural_sort_key'])
                 if chosen("recompress") else [])
    all_files = sorted({os.path.normpath(f) for f in check_files}
                       | {f for kind in CONVERT_ALL_KINDS for f in found[kind]}
                       | set(cbz_files))
    if not all_files:
        InfoDialog(parent, "dialogs.batch_all.no_files_title",
                   lambda d=", ".join(dirs): _("dialogs.batch_all.no_files_message",
                                               directory=d)).show_nonmodal()
        return
    # Pas de refus ici si un autre lot traite certains fichiers : chaque
    # étape les laisse de côté et la fin les signale (_run_convert_all)
    ignored = count_ignored_files(dirs, all_files)

    def _confirm(img_mode):
        _confirm_convert_all(parent, dirs, directories, callbacks, len(check_files), found,
                             all_files, img_mode, ignored, len(cbz_files), steps)

    if not found["img"]:
        _confirm(None)
        return
    mode_dlg = _ImgModeDialog(parent)

    def _on_mode_done():
        if mode_dlg.chosen_mode is not None:
            _confirm(mode_dlg.chosen_mode)

    mode_dlg.finished.connect(lambda _result: _on_mode_done())
    mode_dlg.show_nonmodal()


def _convert_all_details(check_count, counts, img_mode, cbz_count=0, level=None):
    """Lignes de la confirmation de la conversion complète, une par étape
    qui a des fichiers, traduites à l'affichage. Recompression : CBZ déjà
    présents (cbz_count), au taux par défaut `level`, la question que pose
    son lot lancé seul (_RecompressConfirmDialog)."""
    lines = []
    if check_count:
        lines.append(_("dialogs.batch_all.detail_extensions", count=check_count))
    for kind in ("cbr", "cb7", "cbt", "pdf"):
        if counts[kind]:
            lines.append(_("dialogs.batch_all.detail_kind", kind=kind.upper(), count=counts[kind]))
    if counts["img"]:
        key = ("dialogs.batch_all.detail_images_single"
               if img_mode == _ImgModeDialog.MODE_ALL_IN_ONE
               else "dialogs.batch_all.detail_images")
        lines.append(_(key, count=counts["img"]))
    if cbz_count:
        lines.append(_("dialogs.batch_all.detail_recompress", count=cbz_count, level=level))
    return lines


def _confirm_convert_all(parent, dirs, directories, callbacks, check_count, found, all_files,
                         img_mode, ignored_count, cbz_count=0, steps=None):
    """Confirmation unique de la conversion complète : une ligne par étape,
    une seule case de suppression définitive pour tous les lots (la
    recompression ne supprime rien). Le taux de la recompression est celui
    qu'annonce la confirmation, gardé pour son étape."""
    level = get_config_manager().get_zip_compression_level()
    counts = {kind: len(found[kind]) for kind in CONVERT_ALL_KINDS}
    # Fichiers que les conversions supprimeront (Corbeille ou définitivement)
    convertible = [f for kind in CONVERT_ALL_KINDS for f in found[kind]]
    total_size = 0
    for path in all_files:
        try:
            total_size += os.path.getsize(path)
        except OSError:
            continue    # fichier disparu depuis le parcours : sans taille, l'étape le verra
    dlg = _ConfirmDialog(
        parent,
        title_key    = "dialogs.batch_all.confirm_title",
        msg_key      = "dialogs.batch_all.confirm_message",
        count        = len(all_files),
        directory    = ", ".join(dirs),
        total_size   = total_size,
        checkbox_key = "dialogs.batch_all.checkbox_permanent_delete",
        tooltip_key  = "tooltip.batch_all_permanent_delete",
        start_key    = "dialogs.batch_all.start_button",
        callbacks    = callbacks,
        files        = convertible,
        details_fn   = lambda: _convert_all_details(check_count, counts, img_mode,
                                                    cbz_count, level),
    )

    def _on_confirm_done():
        if dlg.confirmed:
            _run_convert_all(parent, dirs, directories, callbacks, img_mode,
                             dlg.permanent_delete, ignored_count, level, steps)

    dlg.finished.connect(lambda _result: _on_confirm_done())
    dlg.show_nonmodal()


def _convert_all_steps(dirs, img_mode, chosen=None):
    """Étapes de la conversion complète, dans l'ordre : [(type, dossier)].
    Extensions d'abord : un fichier mal nommé prend son extension réelle et
    passe ensuite par le bon lot (un PDF nommé « .cbr » par le lot PDF).
    Images toutes dans un seul CBZ : un CBZ par dossier déposé, écrit dans
    ce dossier sous son nom ; dossier None : tous les dossiers.
    Recompression en dernier : elle traite aussi les fichiers que les étapes
    précédentes ont renommés en .cbz.
    chosen : traitements cochés (types de STEP_KINDS, images : « img »),
    None pour tous ; les autres étapes sont retirées."""
    steps = [("extensions", None)] + [(kind, None) for kind in ("cbr", "cb7", "cbt", "pdf")]
    if img_mode == _ImgModeDialog.MODE_ALL_IN_ONE:
        steps += [("img_single", folder) for folder in dirs]
    else:
        steps.append(("img", None))
    steps.append(("recompress", None))
    if chosen is not None:
        steps = [(kind, folder) for kind, folder in steps
                 if ("img" if kind == "img_single" else kind) in chosen]
    return steps


def _convert_all_step_files(kind, folder, dirs, callbacks):
    """Fichiers d'une étape, relus sur le disque au moment de la lancer :
    les étapes précédentes en ont renommé ou supprimé."""
    if kind == "extensions":
        return extension_check_files(dirs)
    if kind == "recompress":
        return recompress_step_files(dirs, callbacks['natural_sort_key'])
    found = convert_all_files([folder] if folder else dirs, callbacks['natural_sort_key'])
    return found["img" if kind in ("img", "img_single") else kind]


def _start_convert_all_step(parent, kind, folder, files, dirs, directories, callbacks,
                            is_permanent, state, on_finished, problems, level=None):
    """Lance une étape de la conversion complète, sans confirmation ni
    journal propre (own_log=False) ; sa fin appelle on_finished. Rend False
    si son lot ne peut pas tourner (rarfile ou PyMuPDF absent) : ses
    fichiers sont alors notés dans problems["unavailable"] (lignes du
    journal). level : taux de la recompression (None : celui des réglages)."""
    directory = dirs[0]
    unavailable = problems["unavailable"]
    if kind == "extensions":
        run_extension_check(parent, files, directory, callbacks, directories,
                            on_finished=on_finished)
    elif kind == "recompress":
        if level is None:
            level = get_config_manager().get_zip_compression_level()
        _run_recompress(parent, files, directory, directories, callbacks, level,
                        on_finished=on_finished, own_log=False)
    elif kind == "cbr":
        if rarfile is None:
            unavailable.extend(f"{os.path.basename(f)}: "
                               + _wt("dialogs.batch_cbr.rarfile_unavailable") for f in files)
            return False
        _run_cbr_conversion(parent, files, directory, directories, callbacks, is_permanent,
                            on_finished, own_log=False)
    elif kind == "cb7":
        _run_cb7_conversion(parent, files, directory, directories, callbacks, is_permanent,
                            on_finished, own_log=False)
    elif kind == "cbt":
        _run_cbt_conversion(parent, files, directory, directories, callbacks, is_permanent,
                            on_finished, own_log=False)
    elif kind == "pdf":
        if not PDF_AVAILABLE:
            unavailable.extend(f"{os.path.basename(f)}: "
                               + _wt("dialogs.batch_pdf.pymupdf_required_message") for f in files)
            return False
        _run_pdf_conversion(parent, files, directory, directories, callbacks, is_permanent,
                            state, on_finished, own_log=False)
    elif kind == "img":
        _run_img_conversion(parent, files, directory, directories, callbacks, is_permanent,
                            on_finished, own_log=False)
    else:
        _run_imgs_to_single_cbz(parent, files, folder, callbacks, is_permanent, on_finished,
                                own_log=False)
    return True


def _path_key(path):
    """Chemin comparable d'un fichier (casse et séparateurs de Windows ignorés)."""
    return os.path.normcase(os.path.normpath(path))


def _note_busy(problems, files):
    """Fichiers laissés de côté par la conversion complète, pris par un autre
    lot : chacun noté une seule fois, même s'il revient à l'étape suivante
    (vérifié par l'étape « extensions », puis converti par son lot)."""
    busy = problems["busy"]
    busy.extend(f for f in files if f not in busy)


def _run_convert_all(parent, dirs, directories, callbacks, img_mode, is_permanent,
                     ignored_count, level=None, chosen=None):
    """Enchaîne les étapes de la conversion complète (_convert_all_steps) ;
    une étape sans fichier est sautée. Seule l'annulation par l'utilisateur
    (bouton Annuler d'une étape) arrête la suite. Tout autre problème laisse
    les étapes suivantes se faire et est signalé à la fin : erreurs et
    disque plein d'une étape (son résumé), fichiers pris par un autre lot
    (problems["busy"]), lot qui ne peut pas tourner (problems["unavailable"]).
    La recompression laisse de côté les CBZ écrits par les conversions de la
    chaîne, déjà au taux par défaut (level : celui de la confirmation).
    chosen : traitements cochés, None pour tous (_convert_all_steps).
    À la fin : un seul journal et un seul résumé (_finish_convert_all)."""
    state = callbacks.get('state')
    if state is None:
        from modules.qt import state as _state_module
        state = _state_module.state
    steps = _convert_all_steps(dirs, img_mode, chosen)
    results = []        # [(type, données du résumé, fichiers du lot)]
    problems = {"unavailable": [], "busy": []}
    stopped = [False]
    written = set()     # CBZ écrits par les conversions (_path_key)

    def _on_step_done(kind, files):
        def _done(new_files, summary):
            if new_files is None:
                # Lot refusé à son démarrage (BATCH_REFUSED) : un autre lot a
                # pris l'un de ses fichiers entre-temps. Les lots de la chaîne
                # n'ont pas de confirmation : rien d'autre ne rend None.
                _note_busy(problems, files)
            else:
                data = summary[1]
                results.append(("img" if kind == "img_single" else kind, data, files))
                if kind not in ("extensions", "recompress"):
                    renamed = {_path_key(f) for f in data.get("renamed_paths", ())}
                    written.update(_path_key(f) for f in new_files
                                   if _path_key(f) not in renamed)
                if data.get("cancelled"):
                    stopped[0] = True
            _next()
        return _done

    def _next():
        while steps and not stopped[0]:
            kind, folder = steps.pop(0)
            files = _convert_all_step_files(kind, folder, dirs, callbacks)
            if kind == "recompress":
                files = [f for f in files if _path_key(f) not in written]
            # Fichiers déjà traités par un autre lot (l'autre panneau, la
            # Bibliothèque…) : laissés de côté et signalés à la fin, l'étape
            # traite les autres
            taken = [f for f in files if files_in_running_batch([f])]
            if taken:
                _note_busy(problems, taken)
                files = [f for f in files if f not in taken]
            if files and _start_convert_all_step(parent, kind, folder, files, dirs, directories,
                                                 callbacks, is_permanent, state,
                                                 _on_step_done(kind, files), problems, level):
                return
        _finish_convert_all(parent, dirs, callbacks, results, problems, stopped[0],
                            ignored_count)

    _next()


def _convert_all_counts(results, problems):
    """Compteurs et lignes du journal de la conversion complète, d'après
    les résultats de ses étapes [(type, données, fichiers du lot)] et les
    problèmes hors des lots (problems : voir _run_convert_all).
    « Traités » : fichiers distincts réellement faits, un fichier renommé par
    l'étape « extensions » puis converti n'étant compté qu'une fois."""
    from modules.qt.misnamed_archives import rename_label

    def key(path):
        return os.path.normcase(os.path.normpath(path))

    old_names = {}      # nouveau chemin (étape « extensions ») → chemin d'origine
    handled = set()
    unavailable = problems["unavailable"]
    counts = {"checked": 0, "well_named": 0, "renamed": {}, "kept": 0, "unknown": 0,
              "converted": {}, "images": None, "recompressed": None, "already_optimal": 0,
              "errors": len(unavailable), "busy": len(problems["busy"])}
    lines = {"renamed_files": [], "not_renamed_files": [], "unknown_format_files": [],
             "busy_files": list(problems["busy"]), "error_details": []}
    for kind, data, files in results:
        for path in files[:data["handled"]]:
            handled.add(old_names.get(key(path), key(path)))
        counts["errors"] += data["errors_count"]
        lines["error_details"] += data["error_lines"]
        if kind == "extensions":
            counts["checked"] += data["checked"]
            counts["well_named"] += data["well_named"]
            counts["kept"] += data["kept_count"]
            counts["unknown"] += data["unknown_count"]
            for check, new_path in data["renamed"]:
                old_names[key(new_path)] = key(check.path)
                label = rename_label(check.extension, check.new_extension)
                counts["renamed"][label] = counts["renamed"].get(label, 0) + 1
                lines["renamed_files"].append(f"{check.path} → {os.path.basename(new_path)}")
            lines["not_renamed_files"] += [f"{check.path} → {check.new_extension}"
                                           for check in data["kept"]]
            lines["unknown_format_files"] += data["unknown"]
            continue
        lines["renamed_files"] += data["renamed_lines"]
        if kind == "recompress":
            counts["recompressed"] = data["recompressed_count"]
            counts["already_optimal"] = data["already_optimal_count"]
            for label, count in data["renamed_kinds"].items():
                counts["renamed"][label] = counts["renamed"].get(label, 0) + count
            continue
        for counter, extension in _LOT_RENAMES:
            if data.get(counter):
                label = f"{kind.upper()}→{extension}"
                counts["renamed"][label] = counts["renamed"].get(label, 0) + data[counter]
        if kind == "img":
            counts["images"] = (counts["images"] or 0) + data["converted_count"]
        else:
            counts["converted"][_CONVERT_ALL_LABELS[kind]] = data["converted_count"]
    lines["error_details"] += unavailable
    counts["handled"] = len(handled)
    return counts, lines


def _write_convert_all_log(callbacks, dirs, results, problems, stopped, ignored_count):
    """Journal unique de la conversion complète (Log_convertall_*.txt) :
    compteurs de l'ensemble, puis fichiers renommés, laissés tels quels, au
    format non reconnu, pris par un autre lot, et erreurs de toutes les
    étapes. Rend son chemin."""
    from datetime import datetime
    counts, lines = _convert_all_counts(results, problems)
    now = datetime.now()
    log_path = unique_log_path(callbacks['get_mosaicview_temp_dir'](), "convertall", now)
    with open(log_path, 'w', encoding='utf-8') as lf:
        lf.write(log_title("title_all"))
        lf.write(log_line("date", date=now.strftime('%Y-%m-%d %H:%M')))
        for directory in dirs:
            lf.write(log_line("directory", path=directory))
        lf.write(log_line("files_handled", count=counts["handled"]))
        if ignored_count is not None:
            lf.write(log_line("files_ignored", count=ignored_count))
        if counts["checked"]:
            lf.write(log_line("well_named", count=counts["well_named"]))
        # Ancienne extension → nouvelle, seulement pour les renommages qui
        # ont eu lieu
        for label in sorted(counts["renamed"]):
            lf.write(log_line("renamed_kind", kind=label, count=counts["renamed"][label]))
        if counts["kept"]:
            lf.write(log_line("not_renamed", count=counts["kept"]))
        if counts["unknown"]:
            lf.write(log_line("unknown_format", count=counts["unknown"]))
        # Conversions des lots qui ont eu lieu, dans l'ordre des étapes
        for kind in ("cbr", "cb7", "cbt", "pdf"):
            label = _CONVERT_ALL_LABELS[kind]
            if label in counts["converted"]:
                lf.write(log_line("converted_kind", kind=label, count=counts["converted"][label]))
        if counts["images"] is not None:
            lf.write(log_line("converted_images", count=counts["images"]))
        if counts["recompressed"] is not None:
            lf.write(log_line("recompressed", count=counts["recompressed"]))
            lf.write(log_line("already_optimal", count=counts["already_optimal"]))
        if counts["busy"]:
            lf.write(log_line("busy", count=counts["busy"]))
        lf.write(log_line("errors", count=counts["errors"]))
        if stopped:
            lf.write(log_line("stopped"))
        for header in ("renamed_files", "not_renamed_files", "unknown_format_files",
                       "busy_files", "error_details"):
            if not lines[header]:
                continue
            lf.write(f"\n{'='*60}\n")
            lf.write(log_line(header))
            lf.write(f"{'='*60}\n\n")
            for line in lines[header]:
                lf.write(f"  - {log_text(line)}\n")
    return log_path


def _finish_convert_all(parent, dirs, callbacks, results, problems, stopped, ignored_count):
    """Fin de la conversion complète : son journal unique, puis un seul
    résumé pour toutes ses étapes (show_chain_summary), qui annonce en tête
    l'annulation et les problèmes hors des lots."""
    log_path = None
    log_error = None
    try:
        log_path = _write_convert_all_log(callbacks, dirs, results, problems, stopped,
                                          ignored_count)
    except Exception as e:
        # Journal impossible à écrire (dossier temporaire plein ou protégé) :
        # la raison est affichée dans le résumé.
        log_path = None
        log_error = exception_text(e)
    show_chain_summary(parent, [(kind, data) for kind, data, _files in results],
                       overall={"log_path": log_path, "log_error": log_error,
                                "stopped": stopped,
                                "unavailable": len(problems["unavailable"]),
                                "busy": len(problems["busy"]),
                                "directories": dirs},
                       title_key="dialogs.batch_all.complete_title")
