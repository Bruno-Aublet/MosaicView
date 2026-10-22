"""
modules/qt/archive_loader.py
Chargement d'archives CBZ/CBR/EPUB.

Comportement :
  - Texte rouge centré sur le canvas pendant le chargement (pas QProgressDialog)
  - Texte mis à jour avec le pourcentage en temps réel
  - testzip (intégrité ZIP)
  - comic_metadata (ComicInfo.xml)
  - pages illisibles signalées par leur vignette (cadre rouge), sans fenêtre
  - Dialogue correction d'extension (CBZ↔CBR)
  - load_archive (1 fichier) et load_multiple_archives (plusieurs fichiers)
  - Tri naturel, préfixe "NEW-" pour les archives suivantes
"""

import os
import re
import sys
import time
import tarfile
import zipfile
import rarfile
import subprocess
import threading

from PySide6.QtCore import QThread, Signal, QObject, Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)
from PySide6.QtGui import QFont, QColor

from modules.qt.state import get_current_theme
from modules.qt.font_manager_qt import get_current_font as _get_current_font

def _get_7z_exe():
    """Retourne le chemin vers 7z.exe embarqué (compatible PyInstaller)."""
    try:
        base = sys._MEIPASS
    except AttributeError:
        base = os.path.abspath(".")
    return os.path.join(base, "7zip", "7z.exe")


def _to_short_path(path):
    """Convertit un chemin Unicode en chemin court 8.3 pour 7z.exe (Windows)."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024)
        return buf.value or path
    except Exception:
        return path


def _is_7z_file_block(path, attributes):
    """Un bloc de `7z l -slt` décrit un fichier (et non un dossier) : tout
    membre dont les attributs Windows ne contiennent pas D. Jamais une égalité
    avec "A" : un fichier en lecture seule (RA), caché (HA), sans bit archive
    (N) ou venant d'Unix (A_ -rw-r--r--) serait ignoré sans erreur, et une
    conversion supprimerait ensuite l'archive d'origine sans ces pages."""
    if path is None or path.endswith(("/", "\\")):
        return False
    if not attributes:
        return True
    return "D" not in attributes.split(" ")[0]


def _list_7z_files(archive_path):
    """Retourne la liste des noms de fichiers dans une archive 7z (fichiers uniquement, pas les dossiers).
    Lève une exception si 7z.exe signale une erreur fatale (returncode >= 2)."""
    exe = _get_7z_exe()
    short_path = _to_short_path(archive_path)
    result = subprocess.run(
        # -sccUTF-8 : sans lui, 7z écrit dans la page de code console (OEM) et les noms
        # non-ASCII décodés en UTF-8 deviennent des U+FFFD, introuvables ensuite à l'extraction
        [exe, "l", "-ba", "-slt", "-sccUTF-8", short_path],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
    )
    if result.returncode >= 2:
        raise ValueError(f"7z returncode {result.returncode}: not a valid 7z archive")
    names = []
    current_path = None
    current_attr = None
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            # Fin d'un bloc : enregistrer si c'est un fichier
            if _is_7z_file_block(current_path, current_attr):
                names.append(current_path.replace('\\', '/'))
            current_path = None
            current_attr = None
        elif line.startswith("Path = "):
            current_path = line[7:]
        elif line.startswith("Attributes = "):
            current_attr = line[13:]
    # Dernier bloc sans ligne vide finale
    if _is_7z_file_block(current_path, current_attr):
        names.append(current_path.replace('\\', '/'))
    return names


class AlteredMemberError(Exception):
    """Membre d'archive dont la somme de contrôle (CRC) ne correspond plus à
    ses octets : ils ont changé depuis la création de l'archive (disque
    défaillant, copie abîmée). Ses octets ont quand même été récupérés
    (`data`) ; le message est celui du lecteur.

    Hérite d'Exception : un appelant qui ne la traite pas (conversions par
    lot, Bibliothèque, import de métadonnées en lot) refuse le membre comme
    toute erreur de lecture. Seules l'ouverture et l'ajout d'une archive
    affichent la page si ses octets se décodent (create_page_entry)."""

    def __init__(self, message, data):
        super().__init__(message)
        self.data = data


def _run_and_read(cmd):
    """Lance `cmd` et rend (code de sortie, sortie standard, sortie d'erreur
    en texte).

    La sortie est lue par blocs et le programme arrêté dès qu'elle dépasse
    MAX_MEMBER_SIZE (_check_member_size lève ValueError) : capturée d'un
    coup, une page de quelques Ko qui se décompresse en plusieurs Go
    épuiserait la mémoire. La sortie d'erreur est lue par un thread : un
    tube plein bloquerait le programme, et donc la lecture de la sortie."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    stderr_chunks = []
    stderr_reader = threading.Thread(
        target=lambda: stderr_chunks.append(proc.stderr.read()), daemon=True)
    stderr_reader.start()
    chunks = []
    total = 0
    try:
        while True:
            chunk = proc.stdout.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            _check_member_size(total)
            chunks.append(chunk)
    except BaseException:
        proc.kill()
        raise
    finally:
        proc.wait()
        stderr_reader.join()
        proc.stdout.close()
        proc.stderr.close()
    stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
    return proc.returncode, b"".join(chunks), stderr


def _read_7z_file(archive_path, member_name):
    """Extrait un fichier d'une archive 7z et retourne ses bytes via stdout
    (_run_and_read : taille limitée à MAX_MEMBER_SIZE).

    « CRC Failed » : 7z a envoyé les octets du membre, puis constaté qu'ils ne
    correspondent plus à sa somme de contrôle (AlteredMemberError, octets
    joints). Toute autre erreur, dont « Data Error » (données compressées
    détruites, sortie incomplète), lève RuntimeError."""
    exe = _get_7z_exe()
    short_path = _to_short_path(archive_path)
    # Chemin complet, sans -r : avec un nom seul + -r, 7z extrait TOUS les membres
    # de même nom (A/p.jpg et B/p.jpg) concaténés sur stdout, et PIL décode alors
    # silencieusement le premier pour chacun. Sans -r, un nom seul ne matche que la racine.
    # -spd : un nom contenant * ou ? (archive créée hors de Windows, ou nom abîmé
    # par une erreur de codage) serait sinon un joker, et la page recevrait les
    # contenus de tous les membres qu'il désigne, mis bout à bout.
    member_path = member_name.replace('\\', '/')
    returncode, data, stderr = _run_and_read(
        [exe, "e", "-so", "-spd", short_path, "--", member_path])
    if returncode != 0:
        if "CRC Failed" in stderr and data:
            raise AlteredMemberError(stderr, data)
        raise RuntimeError(stderr)
    return data


from modules.qt.entries import create_entry
from modules.qt.localization import _, _wt
from modules.qt.canvas_overlay_qt import show_canvas_text as _show_canvas_text, hide_canvas_text as _hide_canvas_text
from modules.qt.mosaic_canvas import build_qimage_for_entry
from modules.qt.archive_type_detector import detect_archive_type
from modules.qt.read_errors import TranslatedLine, files_ignored_line
from modules.qt.archive_names import (tar_entry_name as _tar_entry_name,
                                      without_epub_rank as _without_epub_rank,
                                      epub_page_name, epub_reading_order, open_zip,
                                      IMAGE_EXTS, MAX_MEMBER_SIZE)


_TYPE_TO_EXT = {
    "CBZ":  ".cbz",
    "EPUB": ".epub",
    "CBR":  ".cbr",
    "CB7":  ".cb7",
    "CBT":  ".cbt",
}


def _natural_sort_key(text):
    name = os.path.splitext(text)[0]
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', name)]


def _expand_multipage_tiffs_with_thumbnails(entries):
    """entries.expand_multipage_tiffs, plus le cache vignette des pages créées
    (build_qimage_for_entry, comme pour chaque entrée lue). Appelée par
    LoadWorker.run et ImportMergeWorker : toute archive ouverte ou ajoutée."""
    from modules.qt.entries import expand_multipage_tiffs
    before = {id(e) for e in entries}
    expanded = expand_multipage_tiffs(entries, IMAGE_EXTS)
    for entry in expanded:
        if id(entry) not in before:
            build_qimage_for_entry(entry)
    return expanded


def _archive_read_error(filepath, exc):
    """Ligne d'erreur d'une archive devenue illisible pendant la lecture de
    ses pages (ouverture groupée) : son nom et la raison brute, traduite à
    l'affichage (read_errors.TranslatedLine)."""
    return TranslatedLine("messages.read_errors.line", name=os.path.basename(filepath),
                          raw_reason=str(exc)[:100] or type(exc).__name__)


def _attach_read_errors(images_data, errors):
    """Rattache chaque erreur de lecture « nom: message » à l'image illisible
    correspondante (marquée corrompue, sans données) comme corruption_reason,
    et renvoie les erreurs restantes. Une page illisible n'est ainsi signalée
    que par sa vignette (cadre rouge, raison dans son info-bulle), sans
    apparaître aussi dans « Fichiers ignorés ». Restent
    dans les erreurs : les fichiers non-image illisibles et les erreurs
    d'archive, qui n'ont pas de vignette.

    Le nom d'une erreur est le chemin du membre dans l'archive ; celui de
    l'entrée peut porter le préfixe NEW- (archives 2..N), être le nom de base
    précédé de son rang de lecture (EPUB, archive_names.epub_page_name) ou un
    chemin TAR normalisé (_tar_entry_name)."""
    unread = {}
    for entry in images_data:
        if entry.get("is_corrupted") and entry.get("bytes") is None and not entry.get("is_dir"):
            name = entry.get("orig_name", "")
            unread.setdefault(name, []).append(entry)
            # Page d'EPUB : aussi retrouvée par son nom de base, sans le rang
            without_rank = _without_epub_rank(name)
            if without_rank != name:
                unread.setdefault(without_rank, []).append(entry)
    remaining = []
    attached = set()
    for err in errors:
        if not isinstance(err, str):
            # Erreur d'archive (TranslatedLine) : jamais celle d'une page
            remaining.append(err)
            continue
        name, sep, message = err.partition(": ")
        target = None
        if sep:
            for key in (name, "NEW-" + name, os.path.basename(name), _tar_entry_name(name)):
                candidates = [e for e in unread.get(key, []) if id(e) not in attached]
                if candidates:
                    target = candidates[0]
                    break
        if target is not None:
            attached.add(id(target))
            target["corruption_reason"] = message
        else:
            remaining.append(err)
    return remaining


def _zip_member_reader(archive):
    """read(nom) pour un ZipFile ouvert, qui rend chaque homonyme à son tour.

    archive.read(nom) renvoie toujours le contenu du DERNIER membre de ce nom :
    dans un ZIP contenant deux fois le même nom, le premier serait lu avec les
    données du second, puis perdu au prochain enregistrement. Appelé une fois
    par membre de namelist(), dans l'ordre (un tri stable garde l'ordre des
    homonymes), il rend les membres un par un.

    read.last_info : ZipInfo du membre de la dernière lecture, même si elle a
    échoué (_remember_unread_zip_member).

    Somme de contrôle fausse : le membre est relu sans ce contrôle et ses
    octets sont joints à AlteredMemberError."""
    pending = {}
    for info in archive.infolist():
        pending.setdefault(info.filename, []).append(info)

    def read(name):
        read.last_info = None
        infos = pending.get(name)
        if not infos:
            info = archive.getinfo(name)
        else:
            info = infos.pop(0) if len(infos) > 1 else infos[0]
        read.last_info = info
        _check_member_size(info.file_size)
        try:
            return archive.read(info)
        except zipfile.BadZipFile as e:
            # zipfile : « Bad CRC-32 for file … », levée une fois tous les
            # octets décompressés
            if "CRC" not in str(e):
                raise
            try:
                with archive.open(info) as src:
                    src._expected_crc = None
                    data = src.read()
            except Exception:
                raise e
            raise AlteredMemberError(str(e), data) from e
    read.last_info = None
    return read


def _remember_unread_zip_member(entry, archive_path, read_member):
    """Page d'un ZIP restée sans données (lecture du membre en échec) : garde
    l'emplacement exact de son membre (position de son en-tête, homonymes
    compris) et l'état du fichier (utils.file_signature). Copier ou couper
    cette page recopie alors ses octets réels depuis l'archive
    (clipboard_qt._copy_unread_member), à condition que le fichier n'ait pas
    changé depuis. read_member : lecteur de _zip_member_reader qui vient
    d'échouer."""
    info = getattr(read_member, "last_info", None)
    if info is None or entry.get("bytes") is not None or entry.get("is_dir"):
        return
    from modules.qt.utils import file_signature
    entry["unread_member"] = {"path": os.path.abspath(archive_path),
                              "offset": info.header_offset,
                              "signature": file_signature(archive_path)}


# Limite de taille d'un membre lu en mémoire : MAX_MEMBER_SIZE (archive_names,
# source unique). La taille reçue pendant l'extraction d'un CB7 est contrôlée
# par _read_7z_file. Un membre trop grand devient une page illisible (cadre
# rouge), que l'enregistrement refuse d'omettre.
def _check_member_size(size):
    """Lève ValueError si `size` (octets annoncés par l'archive) dépasse
    MAX_MEMBER_SIZE. Message sans mots (« 4.20 Go > 500.0 Mo ») : il devient
    la raison affichée de la page illisible."""
    if size is not None and size > MAX_MEMBER_SIZE:
        from modules.qt.utils import format_file_size
        raise ValueError(f"{format_file_size(size)} > {format_file_size(MAX_MEMBER_SIZE)}")


def _rar_read(archive, name):
    """archive.read(name) pour un RarFile, après contrôle de la taille annoncée.

    Somme de contrôle fausse : rarfile ne rend aucun octet (même avec
    crc_check=False) ; le membre est relu par UnRAR, qui les garde avec -kb
    (_rar_bytes_without_crc), et ses octets sont joints à AlteredMemberError."""
    _check_member_size(archive.getinfo(name).file_size)
    try:
        return archive.read(name)
    except (rarfile.BadRarFile, rarfile.RarCRCError) as e:
        if not isinstance(e, rarfile.RarCRCError) and "CRC" not in str(e):
            raise
        try:
            data = _rar_bytes_without_crc(archive.filename, name)
        except Exception:
            raise e
        raise AlteredMemberError(str(e), data) from e


def _rar_bytes_without_crc(archive_path, name):
    """Octets du membre `name` d'un RAR, extraits par UnRAR sans tenir compte
    de sa somme de contrôle (-kb : garder les fichiers abîmés), taille
    limitée (_run_and_read). Code de sortie 3 : somme de contrôle fausse.
    Un nom contenant * ou ? n'est pas relu : UnRAR le prendrait pour un
    joker et rendrait les contenus de plusieurs membres mis bout à bout."""
    if any(c in name for c in "*?") or not archive_path:
        raise ValueError(name)
    returncode, data, stderr = _run_and_read(
        [rarfile.UNRAR_TOOL, "p", "-inul", "-kb", "--", archive_path, name])
    if returncode not in (0, 3) or not data:
        raise RuntimeError(stderr or f"UnRAR {returncode}")
    return data


def create_page_entry(file_name, read):
    """Lit un membre (read() rend ses octets) et crée son entrée
    (entries.create_entry). Rend (entrée, raison brute de l'échec de lecture,
    ou None).

    Image au CRC faux (AlteredMemberError) : ses octets récupérés sont décodés
    comme ceux de toute page. S'ils se décodent, la page est affichée et
    marquée altérée (entries.mark_altered : cadre orange) ; sinon elle reste
    illisible (cadre rouge, sans données), avec la raison du lecteur. Un
    fichier qui n'est pas une image n'est jamais repris ainsi (ComicInfo.xml
    abîmé : signalé dans « Fichiers ignorés », comme toute erreur de lecture).
    Appelée par LoadWorker et ImportMergeWorker, jamais par les conversions
    par lot : chaque page altérée doit être vue par l'utilisateur."""
    from modules.qt.entries import mark_altered
    try:
        data = read()
    except AlteredMemberError as e:
        entry = create_entry(file_name, e.data, IMAGE_EXTS)
        if entry.get("is_image") and not entry.get("is_corrupted"):
            mark_altered(entry)
            return entry, None
        return create_entry(file_name, None, IMAGE_EXTS), str(e)
    except Exception as e:
        return create_entry(file_name, None, IMAGE_EXTS), str(e) or type(e).__name__
    return create_entry(file_name, data, IMAGE_EXTS), None


def _tar_info_read(archive, member):
    """Octets du membre TAR `member` (TarInfo), après contrôle de sa taille."""
    _check_member_size(member.size)
    return archive.extractfile(member).read()


def _tar_member_reader(archive):
    """read(nom) pour un TarFile ouvert, qui rend chaque homonyme à son tour,
    après contrôle de la taille annoncée.

    archive.getmember(nom) renvoie toujours le DERNIER membre de ce nom : deux
    membres homonymes donneraient deux pages identiques, et le premier ne
    serait jamais montré. Seuls les fichiers ordinaires sont considérés (ni
    lien symbolique ni lien physique, qui désignent un autre membre ou un
    chemin hors de l'archive). Appelé une fois par nom de la liste des
    fichiers, dans l'ordre, comme _zip_member_reader."""
    pending = {}
    for member in archive.getmembers():
        if member.isfile():
            pending.setdefault(member.name, []).append(member)

    def read(name):
        members = pending.get(name)
        if not members:
            raise KeyError(name)
        member = members.pop(0) if len(members) > 1 else members[0]
        return _tar_info_read(archive, member)
    return read


def _detect_zip_compression_state(filepath):
    """
    Détecte si un CBZ est stocké sans compression ou compressé, en lisant le
    compress_type de la première entrée fichier (hors dossiers) de l'archive.
    Retourne 'stored', 'deflated', ou None (non-CBZ, ou détection impossible).
    """
    if not filepath or os.path.splitext(filepath)[1].lower() != '.cbz':
        return None
    try:
        with open_zip(filepath) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                return 'stored' if info.compress_type == zipfile.ZIP_STORED else 'deflated'
    except Exception:
        pass  # repli : compression inconnue (None), comme pour une archive sans fichier
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Dialogue d'erreur/avertissement générique
# ═══════════════════════════════════════════════════════════════════════════════
class _MessageDialog(QDialog):
    """Remplace QMessageBox.critical/warning — respecte thème, police, langue.
    msg : texte, ou callable () -> str rappelé à chaque changement de langue."""
    def __init__(self, parent, title_key: str, msg):
        super().__init__(parent)
        self._title_key = title_key
        self._msg       = msg
        self._center_parent = parent
        self.setWindowFlags(Qt.Window)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        self._lbl = QLabel()
        # Texte brut : les lignes commencent souvent par un nom de membre
        # d'archive, qu'un début de balise ferait rendre en HTML
        self._lbl.setTextFormat(Qt.PlainText)
        self._lbl.setWordWrap(True)
        self._lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self._btn_ok = QPushButton()
        self._btn_ok.clicked.connect(self.close)
        btn_layout.addWidget(self._btn_ok)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

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
        font = _get_current_font(10)
        self.setWindowTitle(_wt(self._title_key))
        self._lbl.setText(self._msg() if callable(self._msg) else self._msg)
        self._lbl.setFont(font)
        self._lbl.setStyleSheet(f"color: {theme['text']};")
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self._btn_ok.setText(_("buttons.ok"))
        self._btn_ok.setFont(font)
        self._btn_ok.setStyleSheet(btn_style)

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def show_nonmodal(self):
        from modules.qt.dialogs_qt import position_dialog_on_parent
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()


# ═══════════════════════════════════════════════════════════════════════════════
# Dialogue correction d'extension
# ═══════════════════════════════════════════════════════════════════════════════
class ExtensionCorrectionDialog(QDialog):
    """
    Affiché quand l'extension d'un fichier ne correspond pas à son format réel.
    Résultat : 'rename', 'keep', ou None (annuler).
    """
    result_signal = Signal(object)   # 'rename' | 'keep' | None

    def __init__(self, parent, filepath: str, detected: str, declared: str):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.result_choice = None
        self._emitted = False
        self._filepath = filepath
        self._detected = detected
        self._declared = declared
        self._rename_ext = _TYPE_TO_EXT.get(detected.upper(), "." + detected.lower())
        self._center_parent = parent
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)

        layout = QVBoxLayout(self)
        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setWordWrap(True)
        layout.addWidget(self._lbl)

        btn_layout = QHBoxLayout()
        self._btn_rename = QPushButton()
        self._btn_keep   = QPushButton()
        self._btn_cancel = QPushButton()

        self._btn_rename.clicked.connect(self._rename)
        self._btn_keep.clicked.connect(self._keep)
        self._btn_cancel.clicked.connect(self._cancel)

        btn_layout.addWidget(self._btn_rename)
        btn_layout.addWidget(self._btn_keep)
        btn_layout.addWidget(self._btn_cancel)
        layout.addLayout(btn_layout)

        self._retranslate()

        from modules.qt.language_signal import language_signal
        self._lang_handler = lambda _: self._retranslate()
        language_signal.changed.connect(self._lang_handler)
        self.finished.connect(self._on_close)

    def showEvent(self, event):
        # Centrage fait dans ask_async() avant show() (pas de flash de recentrage).
        super().showEvent(event)

    def _retranslate(self):
        theme = get_current_theme()
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; color: {theme['text']}; }}"
        )
        font = _get_current_font(10)
        btn_style = (
            f"QPushButton {{ background: {theme['toolbar_bg']}; color: {theme['text']}; "
            f"border: 1px solid #aaaaaa; padding: 4px 8px; }} "
            f"QPushButton:hover {{ background: {theme['separator']}; }}"
        )
        self.setWindowTitle(_wt("messages.extension_mismatch_generic.title"))
        filename = os.path.basename(self._filepath)
        self._lbl.setText(_(
            "messages.extension_mismatch_generic.message",
            filename=filename,
            real=self._detected.upper(),
            declared=self._declared.upper(),
        ))
        self._lbl.setFont(font)
        self._lbl.setStyleSheet(f"color: {theme['text']};")
        for btn in (self._btn_rename, self._btn_keep, self._btn_cancel):
            btn.setFont(font)
            btn.setStyleSheet(btn_style)
        self._btn_rename.setText(_("buttons.rename_to_ext", ext=self._rename_ext))
        self._btn_keep.setText(_("buttons.open_without_rename"))
        self._btn_cancel.setText(_("buttons.cancel"))

    def _on_close(self):
        from modules.qt.language_signal import language_signal
        try:
            language_signal.changed.disconnect(self._lang_handler)
        except RuntimeError:
            pass

    def _rename(self):
        self._finish('rename')

    def _keep(self):
        self._finish('keep')

    def _cancel(self):
        self._finish(None)

    def _finish(self, result):
        if self._emitted:
            return
        self._emitted = True
        self.result_choice = result
        self._on_close()
        self.result_signal.emit(result)
        self.hide()
        self.deleteLater()

    def closeEvent(self, event):
        if not self._emitted:
            self._emitted = True
            self.result_choice = None
            self._on_close()
            self.result_signal.emit(None)
        event.accept()

    def ask_async(self, on_result):
        """Affiche (NON modal) et appelle on_result('rename'|'keep'|None)."""
        from modules.qt.dialogs_qt import position_dialog_on_parent
        self.result_signal.connect(on_result)
        self.adjustSize()
        position_dialog_on_parent(self, self._center_parent)
        self.show()
        self.raise_()
        self.activateWindow()


# ═══════════════════════════════════════════════════════════════════════════════
# Worker thread de chargement
# ═══════════════════════════════════════════════════════════════════════════════
class LoadWorker(QThread):
    """
    Charge une ou plusieurs archives en arrière-plan.
    Émet :
      progress(int)                    — pourcentage 0-100
      finished(list, list, str|None)   — (images_data, errors, first_filepath)
      error(str)                       — message d'erreur fatal
      need_ext_dialog(str, str, str)   — (filepath, detected, declared)
      cancelled()                      — utilisateur a annulé
      file_renamed(str)                — fichier renommé (extension corrigée) pendant le chargement
    """
    progress        = Signal(int)
    load_finished   = Signal(list, list, str)   # images_data, errors, first_filepath
    error           = Signal(str)
    need_ext_dialog = Signal(str, str, str)
    cancelled       = Signal()
    file_renamed    = Signal(str)

    def __init__(self, filepaths: list, multi: bool = False):
        super().__init__()
        self._filepaths  = filepaths
        self._multi      = multi
        self._ext_result = None
        self._ext_event  = threading.Event()
        self._cancelled  = threading.Event()
        # Texte de la dernière erreur émise, () -> str rappelé à l'affichage
        # (_emit_error) : le message suit un changement de langue.
        self.error_text_fn = None

    def _emit_error(self, text_fn):
        """Émet `error` (texte dans la langue courante) et garde text_fn,
        () -> str, que les slots rappellent à l'affichage (error_text_fn)."""
        self.error_text_fn = text_fn
        self.error.emit(text_fn())

    def _emit_read_error(self, filepath, exc):
        """Erreur de lecture d'une archive entière : nom du fichier et raison
        traduite (read_errors.read_error_text), au lieu du message brut de
        l'exception, le plus souvent en anglais."""
        from modules.qt.read_errors import read_error_text
        raw = str(exc)[:200] or type(exc).__name__
        self._emit_error(lambda f=os.path.basename(filepath), r=raw:
                         _("messages.read_errors.line", name=f, reason=read_error_text(r)))

    def set_ext_result(self, result):
        self._ext_result = result
        self._ext_event.set()

    def _ask_ext(self, filepath, detected, declared):
        """Émet le signal, bloque jusqu'à réponse."""
        self._ext_event.clear()
        self._ext_result = None
        # Testé APRÈS clear() : ArchiveLoader._detach_worker pose _cancelled puis
        # set_ext_result(None). Si l'annulation arrive après ce test, son set()
        # suit forcément le clear() et débloque le wait() ci-dessous ; sinon on
        # sort ici. Dans les deux cas le thread ne reste jamais bloqué.
        if self._cancelled.is_set():
            return None
        self.need_ext_dialog.emit(filepath, detected, declared)
        self._ext_event.wait()
        return self._ext_result

    # ── helpers détection / namelist ───────────────────────────────────────────

    _EXT_TO_TYPE = {
        ".cbz":  "CBZ",
        ".epub": "EPUB",
        ".cbr":  "CBR",
        ".cb7":  "CB7",
        ".cbt":  "CBT",
    }
    _ERROR_KEYS = {
        "CBZ":  "messages.errors.zip_invalid.message",
        "EPUB": "messages.errors.zip_invalid.message",
        "CBR":  "messages.errors.rar_invalid.message",
        "CB7":  "messages.errors.7z_invalid.message",
        "CBT":  "messages.errors.cbt_invalid.message",
    }

    def _get_namelist(self, filepath, real_type):
        """Retourne (files_list, error_str). error_str est None si succès."""
        try:
            if real_type in ("CBZ", "EPUB"):
                with open_zip(filepath) as archive:
                    nl = sorted(archive.namelist(), key=_natural_sort_key)
                    if real_type == "EPUB":
                        # Images seules, dans l'ordre de lecture du livre ;
                        # les pages sont nommées par leur rang (_page_name)
                        nl = epub_reading_order(
                            archive,
                            [f for f in nl if any(f.lower().endswith(e) for e in IMAGE_EXTS)])
                return nl, None
            elif real_type == "CBR":
                with rarfile.RarFile(filepath, 'r') as archive:
                    return sorted(archive.namelist(), key=_natural_sort_key), None
            elif real_type == "CB7":
                all_names = _list_7z_files(filepath)
                return sorted([f for f in all_names if not f.endswith('/')], key=_natural_sort_key), None
            elif real_type == "CBT":
                # Tous les fichiers, comme pour CBZ/CBR/CB7 (ComicInfo.xml,
                # .nfo…) : une conversion en CBZ propose ensuite de supprimer
                # le CBT, qui ne doit rien emporter avec lui.
                with tarfile.open(filepath, 'r:*') as archive:
                    return sorted(
                        [m.name for m in archive.getmembers() if m.isfile()],
                        key=_natural_sort_key
                    ), None
        except Exception as e:
            return [], str(e)[:200]
        return [], "unknown format"

    def _resolve_filepath(self, filepath, declared_ext, real_type, actual_first_fp, is_multi_error_list=None):
        """
        Si real_type != declared type, demande renommage et renomme si besoin.
        Retourne le filepath final, ou None si annulé.
        is_multi_error_list : si non-None, les erreurs de renommage sont ajoutées à cette liste
                              plutôt qu'émises via self.error.
        """
        declared_type = self._EXT_TO_TYPE.get("." + declared_ext.lstrip("."))
        if real_type == declared_type:
            return filepath  # rien à faire

        choice = self._ask_ext(filepath, real_type, declared_ext.lstrip('.'))
        if choice is None:
            return None

        if choice == 'rename':
            new_ext = _TYPE_TO_EXT.get(real_type, declared_ext)
            new_filepath = os.path.splitext(filepath)[0] + new_ext
            try:
                os.rename(filepath, new_filepath)
                filepath = new_filepath
                if actual_first_fp is not None:
                    actual_first_fp[0] = filepath
                self.file_renamed.emit(filepath)
            except Exception as e:
                if is_multi_error_list is not None:
                    is_multi_error_list.append(
                        TranslatedLine("messages.errors.rename_failed.message", error=str(e)))
                else:
                    self._emit_error(lambda err=str(e): _("messages.errors.rename_failed.message",
                                                          error=err))
                return None

        return filepath

    # ── load_archive (single fichier) ──────────────────────────────────────────
    def load_archive(self, filepath, all_data, errors, actual_first_fp=None):
        """Charge un seul fichier CBZ/CBR/EPUB/CB7. Retourne False si annulé/erreur."""
        ext = os.path.splitext(filepath)[1].lower()

        real_type = detect_archive_type(filepath)
        if real_type is None:
            error_key = self._ERROR_KEYS.get(self._EXT_TO_TYPE.get(ext, "CBZ"), "messages.errors.zip_invalid.message")
            self._emit_error(lambda k=error_key, f=os.path.basename(filepath): _(k, file=f))
            return False

        filepath = self._resolve_filepath(filepath, ext, real_type, actual_first_fp)
        if filepath is None:
            self.cancelled.emit()
            return False

        files_list, err = self._get_namelist(filepath, real_type)
        if err:
            error_key = self._ERROR_KEYS.get(real_type, "messages.errors.zip_invalid.message")
            self._emit_error(lambda k=error_key, f=os.path.basename(filepath): _(k, file=f))
            return False
        if not files_list:
            return True

        total = len(files_list)
        entries = []
        if real_type in ("CBZ", "EPUB"):
            try:
                with open_zip(filepath) as archive:
                    read_member = _zip_member_reader(archive)
                    for idx, file in enumerate(files_list, start=1):
                        if self._cancelled.is_set():
                            return False
                        if file.endswith('/'):
                            continue
                        # Page d'un EPUB : files_list ne contient que des images,
                        # idx est leur rang dans l'ordre de lecture
                        file_name = epub_page_name(idx, file) if real_type == "EPUB" else file
                        entry, message = create_page_entry(file_name, lambda f=file: read_member(f))
                        if message is not None:
                            errors.append(f"{file}: {message[:50]}")
                            _remember_unread_zip_member(entry, filepath, read_member)
                        build_qimage_for_entry(entry)
                        all_data.append(entry)
                        self.progress.emit(int(idx / total * 100))
            except Exception as e:
                self._emit_read_error(filepath, e)
                return False
        elif real_type == "CBR":
            try:
                with rarfile.RarFile(filepath, 'r') as archive:
                    for idx, file in enumerate(files_list, start=1):
                        if self._cancelled.is_set():
                            return False
                        if file.endswith('/'):
                            continue
                        entry, message = create_page_entry(file, lambda f=file: _rar_read(archive, f))
                        if message is not None:
                            errors.append(f"{file}: {message[:50]}")
                        build_qimage_for_entry(entry)
                        all_data.append(entry)
                        self.progress.emit(int(idx / total * 100))
            except Exception as e:
                self._emit_read_error(filepath, e)
                return False
        elif real_type == "CB7":
            for idx, file in enumerate(files_list, start=1):
                if self._cancelled.is_set():
                    return False
                entry, message = create_page_entry(file, lambda f=file: _read_7z_file(filepath, f))
                if message is not None:
                    errors.append(f"{file}: {message[:50]}")
                build_qimage_for_entry(entry)
                all_data.append(entry)
                self.progress.emit(int(idx / total * 100))
        elif real_type == "CBT":
            try:
                with tarfile.open(filepath, 'r:*') as archive:
                    read_member = _tar_member_reader(archive)
                    for idx, file in enumerate(files_list, start=1):
                        if self._cancelled.is_set():
                            return False
                        try:
                            data = read_member(file)
                        except Exception as e:
                            data = None
                            errors.append(f"{file}: {str(e)[:50]}")
                        entry = create_entry(_tar_entry_name(file), data, IMAGE_EXTS)
                        build_qimage_for_entry(entry)
                        all_data.append(entry)
                        self.progress.emit(int(idx / total * 100))
            except Exception as e:
                self._emit_read_error(filepath, e)
                return False

        return True

    # ── load_multiple_archives ─────────────────────────────────────────────────
    def load_multiple_archives(self, filepaths, all_data, errors, actual_first_fp=None):
        """Charge plusieurs fichiers. Retourne False si annulé/erreur fatale.

        L'annulation (Annuler, fermeture du fichier) est testée avant chaque
        archive et chaque page, comme dans load_archive : sans ça, toutes les
        archives seraient lues jusqu'au bout pour rien, et gardées ouvertes."""
        _TYPE_TO_FORMAT = {"CBZ": "zip", "EPUB": "zip", "CBR": "rar", "CB7": "7z", "CBT": "tar"}

        # Phase 1 : détection + dialogues extension + namelists
        archive_namelists = []
        for orig_filepath in filepaths:
            if self._cancelled.is_set():
                return False
            ext = os.path.splitext(orig_filepath)[1].lower()
            current_filepath = orig_filepath

            real_type = detect_archive_type(current_filepath)
            if real_type is None:
                error_key = self._ERROR_KEYS.get(self._EXT_TO_TYPE.get(ext, "CBZ"), "messages.errors.zip_invalid.message")
                errors.append(TranslatedLine(error_key, file=os.path.basename(current_filepath)))
                continue

            current_filepath = self._resolve_filepath(
                current_filepath, ext, real_type, actual_first_fp,
                is_multi_error_list=errors
            )
            if self._cancelled.is_set():
                return False
            if current_filepath is None:
                continue

            files_list, err = self._get_namelist(current_filepath, real_type)
            if err or not files_list:
                if err:
                    error_key = self._ERROR_KEYS.get(real_type, "messages.errors.zip_invalid.message")
                    errors.append(TranslatedLine(error_key, file=os.path.basename(current_filepath)))
                continue

            archive_namelists.append((current_filepath, _TYPE_TO_FORMAT[real_type], real_type, files_list))

        if not archive_namelists:
            # Aucune archive lisible : leurs erreurs (archive invalide,
            # renommage impossible) sont affichées, traduites à chaque
            # affichage (files_ignored_line). Sans erreur, chaque question de
            # correction d'extension a été refusée : annulation.
            if errors:
                self._emit_error(lambda lines=list(errors[:20]):
                                 "\n".join(files_ignored_line(e) for e in lines))
            else:
                self.cancelled.emit()
            return False

        # Phase 2 : vraie lecture avec progression
        total_files = sum(
            len([f for f in files_list if not f.endswith("/")])
            for _, _, _, files_list in archive_namelists
        )
        processed_files = 0
        # Archives lues entièrement. Une archive qui échoue pendant la lecture
        # de ses pages (fichier devenu illisible depuis la liste de ses noms :
        # partage réseau coupé, support retiré) ne laisse aucune page partielle
        # et ne donne pas son nom au document : enregistré, il la remplacerait
        # par les pages des autres archives.
        read_archives = []

        for filepath, actual_format, real_type, files_list in archive_namelists:
            # Préfixe NEW- : pages ajoutées à la première archive lue
            add_prefix = bool(read_archives)
            first_entry = len(all_data)
            failed = False

            if actual_format == "zip":
                try:
                    with open_zip(filepath) as archive:
                        read_member = _zip_member_reader(archive)
                        for position, file in enumerate(files_list, start=1):
                            if self._cancelled.is_set():
                                return False
                            if file.endswith('/'):
                                continue
                            file_name = epub_page_name(position, file) if real_type == "EPUB" else file
                            entry, message = create_page_entry(file_name, lambda f=file: read_member(f))
                            if message is not None:
                                errors.append(f"{file}: {message[:50]}")
                                _remember_unread_zip_member(entry, filepath, read_member)
                            if add_prefix:
                                entry["orig_name"] = "NEW-" + entry["orig_name"]
                                entry["source_archive"] = os.path.basename(filepath)
                            build_qimage_for_entry(entry)
                            all_data.append(entry)
                            processed_files += 1
                            self.progress.emit(int(processed_files / total_files * 100) if total_files > 0 else 0)
                except Exception as e:
                    del all_data[first_entry:]
                    errors.append(_archive_read_error(filepath, e))
                    failed = True

            elif actual_format == "7z":
                for file in files_list:
                    if self._cancelled.is_set():
                        return False
                    entry, message = create_page_entry(file, lambda f=file: _read_7z_file(filepath, f))
                    if message is not None:
                        errors.append(f"{file}: {message[:50]}")
                    if add_prefix:
                        entry["orig_name"] = "NEW-" + entry["orig_name"]
                        entry["source_archive"] = os.path.basename(filepath)
                    build_qimage_for_entry(entry)
                    all_data.append(entry)
                    processed_files += 1
                    self.progress.emit(int(processed_files / total_files * 100) if total_files > 0 else 0)

            elif actual_format == "rar":
                try:
                    with rarfile.RarFile(filepath, 'r') as archive:
                        for file in files_list:
                            if self._cancelled.is_set():
                                return False
                            if file.endswith('/'):
                                continue
                            entry, message = create_page_entry(file, lambda f=file: _rar_read(archive, f))
                            if message is not None:
                                errors.append(f"{file}: {message[:50]}")
                            if add_prefix:
                                entry["orig_name"] = "NEW-" + entry["orig_name"]
                                entry["source_archive"] = os.path.basename(filepath)
                            build_qimage_for_entry(entry)
                            all_data.append(entry)
                            processed_files += 1
                            self.progress.emit(int(processed_files / total_files * 100) if total_files > 0 else 0)
                except Exception as e:
                    del all_data[first_entry:]
                    errors.append(_archive_read_error(filepath, e))
                    failed = True

            elif actual_format == "tar":
                try:
                    with tarfile.open(filepath, 'r:*') as archive:
                        read_member = _tar_member_reader(archive)
                        for file in files_list:
                            if self._cancelled.is_set():
                                return False
                            try:
                                data = read_member(file)
                            except Exception as e:
                                errors.append(f"{file}: {str(e)[:50]}")
                                data = None
                            entry = create_entry(_tar_entry_name(file), data, IMAGE_EXTS)
                            if add_prefix:
                                entry["orig_name"] = "NEW-" + entry["orig_name"]
                                entry["source_archive"] = os.path.basename(filepath)
                            build_qimage_for_entry(entry)
                            all_data.append(entry)
                            processed_files += 1
                            self.progress.emit(int(processed_files / total_files * 100) if total_files > 0 else 0)
                except Exception as e:
                    del all_data[first_entry:]
                    errors.append(_archive_read_error(filepath, e))
                    failed = True

            if not failed:
                read_archives.append(filepath)

        if not read_archives:
            # Erreurs d'archive et de pages des archives abandonnées : raison
            # traduite à chaque affichage (files_ignored_line)
            self._emit_error(lambda lines=list(errors[:20]):
                             "\n".join(files_ignored_line(e) for e in lines))
            return False
        if actual_first_fp is not None:
            actual_first_fp[0] = read_archives[0]
        return True

    # ── run ────────────────────────────────────────────────────────────────────
    def run(self):
        errors   = []
        all_data = []
        actual_first_fp = [self._filepaths[0] if self._filepaths else ""]

        if self._multi:
            ok = self.load_multiple_archives(self._filepaths, all_data, errors, actual_first_fp)
        else:
            ok = self.load_archive(self._filepaths[0], all_data, errors, actual_first_fp)

        if not ok:
            return

        if self._multi:
            all_data.sort(key=lambda e: _natural_sort_key(e["orig_name"]))

        # Un TIFF de plusieurs pages devient une page du comic par page
        # (décision de conception, voir entries.expand_multipage_tiffs).
        all_data = _expand_multipage_tiffs_with_thumbnails(all_data)

        self.load_finished.emit(all_data, errors, actual_first_fp[0])


# Référence globale aux workers orphelins (annulés mais encore en cours)
# pour éviter que Qt les détruise pendant que le thread tourne.
_orphan_workers: list = []


# ═══════════════════════════════════════════════════════════════════════════════
# Gestionnaire de chargement (interface avec MainWindow)
# ═══════════════════════════════════════════════════════════════════════════════
def _install_loaded_pages(state, loaded_pages):
    """Fin d'un chargement qui remplace le document (archive, PDF) : les pages
    lues deviennent le document.

    Pendant le chargement, le document est vide mais d'autres sources peuvent
    y ajouter des pages (import web, numérisation, collage d'une image) :
    remplacées par la liste lue, elles disparaîtraient sans avertissement.
    Elles sont gardées à la suite des pages lues, et le document est alors
    modifié. L'historique repart de zéro : un état poussé pendant le
    chargement ne décrit qu'une partie du document, et un Annuler qui le
    restaurerait remplacerait le fichier ouvert par ces seules pages."""
    from modules.qt.undo_redo import reset_history
    from modules.qt.comic_info import name_incoming_comic_infos
    added = list(state.images_data)
    state.images_data = list(loaded_pages) + added
    # Un seul ComicInfo.xml : le premier lu ; les autres (homonymes de
    # l'archive, fichier déposé pendant le chargement) sont renommés
    name_incoming_comic_infos(state.images_data, [])
    # Une page dont l'extension a été corrigée à la lecture (vrai format,
    # entries.create_entry) n'est corrigée dans le fichier qu'à
    # l'enregistrement : le document est modifié
    if added or any(p.get("extension_corrected_from") for p in loaded_pages):
        state.modified = True
    reset_history(state)


class ArchiveLoader(QObject):
    """
    Orchestre le chargement d'une ou plusieurs archives.
    Pendant le chargement, affiche un texte rouge centré sur le canvas.

    Usage :
        loader = ArchiveLoader(parent_window, canvas, state)
        loader.load([filepath])
        loader.load([fp1, fp2, ...])

    Signal :
        loading_finished  — émis quand le rendu est terminé
    """
    loading_started  = Signal()
    loading_finished = Signal()

    def __init__(self, parent_window, canvas, state):
        super().__init__(parent_window)
        self._win    = parent_window
        self._canvas = canvas
        self._state  = state
        self._worker = None
        # ExtensionCorrectionDialog en attente de réponse, fermé si le
        # chargement qui l'a ouvert est détaché (voir _detach_worker)
        self._ext_dialog = None
        # QGraphicsTextItem rouge centré (affiché pendant le chargement)
        self._loading_item = None
        self._loading_item_holder = [None]
        # Texte cliquable Annuler (comme web_import_qt)
        self._cancel_item_holder = [None]

    # ──────────────────────────────────────────────────────────────────────────
    # Démarrage du chargement
    # ──────────────────────────────────────────────────────────────────────────
    def load(self, filepaths: list):
        if not filepaths:
            return

        multi = len(filepaths) > 1

        # Réinitialise l'état : nouveau document (voir AppState.doc_generation)
        st = self._state
        st.doc_generation   += 1
        st.images_data       = []
        st.modified          = False
        st.selected_indices.clear()
        st.current_file      = filepaths[0]  # premier fichier (comme l'original)
        st.disk_signature    = None
        st.comic_metadata    = None
        st.original_page_count = None
        st.needs_renumbering = True
        st.merge_counter     = 0
        st.first_image_dir   = None
        st.current_directory = ""
        st.focused_index     = None

        # Vide la scène (supprime les 3 lignes du canvas vide) avant d'afficher le texte rouge
        self._canvas._empty_items.clear()
        self._canvas._items.clear()
        self._canvas._drop_indicator_items.clear()
        self._canvas.scene().clear()

        # Affiche le texte rouge centré sur le canvas (comme l'original)
        self._show_loading_text(0)

        # Ancien worker éventuellement encore en cours (chargements consécutifs) :
        # jamais détruit directement, voir _detach_worker.
        self._detach_worker()

        self._worker = LoadWorker(filepaths, multi=multi)
        self._worker.progress.connect(self._on_progress)
        self._worker.load_finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.need_ext_dialog.connect(self._on_need_ext_dialog)
        self._worker.file_renamed.connect(self._on_file_renamed)

        self.loading_started.emit()
        self._worker.start()

    # ──────────────────────────────────────────────────────────────────────────
    # Texte rouge centré sur le canvas
    # ──────────────────────────────────────────────────────────────────────────
    def _show_loading_text(self, percent: int):
        if self._worker is None:
            return
        _show_canvas_text(self._canvas, _("labels.loading", percent=percent), self._loading_item_holder)
        self._loading_item = self._loading_item_holder[0]
        self._show_cancel_button()

    def _hide_loading_text(self):
        _hide_canvas_text(self._canvas, self._loading_item_holder)
        self._loading_item = None
        self._hide_cancel_button()

    # ──────────────────────────────────────────────────────────────────────────
    # Texte cliquable Annuler (comme web_import_qt._CancelTextItem)
    # ──────────────────────────────────────────────────────────────────────────
    def _show_cancel_button(self):
        from modules.qt.web_import_qt import _show_cancel_item
        cancel_text = f"[ {_('buttons.cancel')} ]"
        _show_cancel_item(self._canvas, cancel_text, self._cancel_item_holder, self.cancel,
                          anchor_lbl=self._loading_item_holder[0])

    def _hide_cancel_button(self):
        _hide_canvas_text(self._canvas, self._cancel_item_holder)

    # ──────────────────────────────────────────────────────────────────────────
    # Slots signaux worker
    # ──────────────────────────────────────────────────────────────────────────
    def _detach_worker(self):
        """Débranche le worker courant de ce chargeur et le garde vivant dans
        _orphan_workers jusqu'à la fin réelle de son thread : un QThread détruit
        pendant qu'il tourne fait planter l'application (« QThread: Destroyed
        while thread is still running »)."""
        worker = self._worker
        if worker is None:
            return
        self._worker = None
        worker._cancelled.set()
        # Débranché AVANT de débloquer le worker (set_ext_result ci-dessous) :
        # sinon il a le temps d'émettre son signal cancelled vers ce chargeur,
        # dont le slot (_on_cancelled) s'exécute alors après coup et remet
        # current_file à None — sur le document suivant si un autre fichier est
        # chargé entre-temps.
        try:
            worker.progress.disconnect(self._on_progress)
            worker.load_finished.disconnect(self._on_finished)
            worker.error.disconnect(self._on_error)
            worker.cancelled.disconnect(self._on_cancelled)
            worker.need_ext_dialog.disconnect(self._on_need_ext_dialog)
            worker.file_renamed.disconnect(self._on_file_renamed)
        except RuntimeError:
            pass
        # Débloque un worker en attente de la réponse au dialogue de correction
        # d'extension (voir LoadWorker._ask_ext), sinon son thread ne finit jamais.
        worker.set_ext_result(None)
        # Ce dialogue ne concerne plus aucun chargement : le laisser ouvert
        # proposerait un choix sans effet.
        ext_dlg = self._ext_dialog
        self._ext_dialog = None
        if ext_dlg is not None:
            try:
                ext_dlg.close()
            except RuntimeError:
                pass
        worker.setParent(None)
        _orphan_workers.append(worker)

        def _on_thread_done(w=worker):
            try:
                _orphan_workers.remove(w)
            except ValueError:
                return  # déjà libéré (appel direct ci-dessous + signal)
            from modules.qt.utils import dispose_qthread
            dispose_qthread(w)
        # finished natif de QThread (LoadWorker ne le masque pas : son signal de
        # fin s'appelle load_finished), émis une fois run() réellement terminé.
        worker.finished.connect(_on_thread_done)
        if worker.isFinished():
            _on_thread_done()

    def cancel(self):
        """Annule le chargement en cours et déconnecte les signaux du worker."""
        if self._worker is not None:
            self._detach_worker()
            self._hide_loading_text()
            self._state.current_file = None
            self._canvas.render_mosaic()
            self.loading_finished.emit()

    def shutdown(self):
        """Arrête le chargement en cours sans toucher à l'état du panneau :
        appelé par force_close_file, qui réinitialise lui-même l'état et
        l'affichage."""
        if self._worker is None:
            return
        self._detach_worker()
        self._hide_loading_text()

    def _cleanup_worker(self):
        """Libère le worker Qt après la fin du thread (appelé depuis le slot de
        son signal de fin, voir dispose_qthread)."""
        if self._worker is not None:
            from modules.qt.utils import dispose_qthread
            dispose_qthread(self._worker)
            self._worker = None

    def _from_current_worker(self) -> bool:
        """Le signal en cours de traitement vient-il du chargement courant ?
        _detach_worker débranche les slots d'un chargement annulé ou remplacé,
        mais un signal déjà mis en file d'attente avant le débranchement est
        quand même livré (Qt ne vérifie la connexion qu'à l'émission) : sans ce
        contrôle, le fichier annulé serait installé, éventuellement dans le
        document suivant."""
        sender = self.sender()
        return sender is None or sender is self._worker

    def _on_progress(self, pct: int):
        if not self._from_current_worker():
            return
        self._show_loading_text(pct)

    def _on_finished(self, images_data: list, errors: list, first_filepath: str):
        if not self._from_current_worker():
            return
        self._hide_loading_text()
        self._cleanup_worker()

        st = self._state
        # Pages illisibles : la raison de chacune va dans son info-bulle et
        # sort de la liste « Fichiers ignorés ».
        errors = _attach_read_errors(images_data, errors)
        _install_loaded_pages(st, images_data)
        st.current_directory = ""
        st.needs_renumbering = True
        st.focused_index     = None
        st.zip_compression_state = _detect_zip_compression_state(first_filepath) if first_filepath else None
        if first_filepath:
            st.current_file = first_filepath
        # Référence pour détecter, à l'enregistrement, une modification du
        # fichier faite ailleurs depuis son ouverture.
        from modules.qt.utils import file_signature
        st.disk_signature = file_signature(st.current_file)

        # Métadonnées : celles du ComicInfo.xml du document (comic_info.
        # is_comic_info_entry), le fichier que les lecteurs de BD liront
        if first_filepath:
            st.comic_metadata = None
            st.original_page_count = None
            try:
                from modules.qt.comic_info import sync_comic_metadata
                sync_comic_metadata(st, force=True)
            except Exception:
                pass  # repli : métadonnées absentes, l'onglet Métadonnées signale le ComicInfo.xml illisible
            if st.comic_metadata and hasattr(self._win, '_metadata_tab'):
                from PySide6.QtCore import QTimer as _QTimer
                _QTimer.singleShot(0, self._win._metadata_tab.refresh)

        self._canvas.render_mosaic()
        self.loading_finished.emit()

        # Pages illisibles : signalées par leur cadre rouge et leur icône, la
        # raison dans l'info-bulle — pas de fenêtre. Seuls les autres fichiers
        # illisibles et les erreurs d'archive vont dans « Fichiers ignorés ».
        if errors:
            from modules.qt.read_errors import files_ignored_line, unreadable_member_names
            names = unreadable_member_names(images_data)
            shown = list(errors[:20])
            dlg = _MessageDialog(
                self._win, "messages.warnings.files_ignored.title",
                lambda: "\n".join(files_ignored_line(e, names) for e in shown))
            dlg.show_nonmodal()

    def _on_error(self, msg: str):
        if not self._from_current_worker():
            return
        # Texte recalculé à l'affichage (changement de langue), lu avant que
        # _cleanup_worker libère le worker
        text = getattr(self._worker, "error_text_fn", None) or msg
        self._hide_loading_text()
        self._cleanup_worker()
        self._state.current_file = None
        self._canvas.render_mosaic()
        self.loading_finished.emit()
        dlg = _MessageDialog(self._win, "messages.warnings.cannot_open_file.title", text)
        dlg.show_nonmodal()

    def _on_cancelled(self):
        if not self._from_current_worker():
            return
        self._hide_loading_text()
        self._cleanup_worker()
        self._state.current_file = None
        self._canvas.render_mosaic()
        self.loading_finished.emit()

    def _on_file_renamed(self, new_filepath: str):
        """Fichier renommé (extension corrigée) pendant le chargement : met à jour
        current_file et rafraîchit le titre de fenêtre immédiatement, sans attendre
        la fin du chargement (sinon la barre de titre garde l'ancien nom pendant
        toute la durée du chargement)."""
        if not self._from_current_worker():
            return
        self._state.current_file = new_filepath
        self._win._refresh_title()

    def _on_need_ext_dialog(self, filepath: str, detected: str, declared: str):
        """Affiché dans le thread principal (NON modal) ; la réponse est transmise
        au worker via son callback. Le worker (dans son propre thread) reste bloqué
        sur son threading.Event jusqu'à set_ext_result — le thread UI, lui, n'est
        PAS bloqué : l'utilisateur peut agir sur l'autre panneau."""
        if not self._from_current_worker():
            return  # chargement détaché : _detach_worker l'a déjà débloqué
        dlg = ExtensionCorrectionDialog(self._win, filepath, detected, declared)
        # La réponse va au worker qui a posé la question, pas à self._worker : il
        # a pu être détaché entre-temps (annulation, fermeture du fichier, qui
        # ferme aussi ce dialogue) ou remplacé par le chargement d'un autre fichier.
        asking_worker = self._worker
        self._ext_dialog = dlg

        def _on_choice(choice):
            if self._ext_dialog is dlg:
                self._ext_dialog = None
            if asking_worker is None:
                return
            if self._worker is asking_worker:
                # Réaffiche le texte rouge après fermeture du dialogue
                self._show_loading_text(0)
            asking_worker.set_ext_result(choice)

        dlg.ask_async(_on_choice)
