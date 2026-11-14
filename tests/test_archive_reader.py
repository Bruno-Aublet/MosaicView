"""Lecteur d'archive commun (archive_members.open_archive / ArchiveReader) :
liste et lecture des membres d'un CBZ, EPUB, CBR, CB7 ou CBT avec les règles
de tous les lecteurs (ouverture et ajout d'une BD, lots, import de
métadonnées, Bibliothèque, ComicInfo.xml).

- ordre de l'ouverture (archive_names.natural_key), dossier compris ; deux
  membres de même nom dans l'ordre de l'archive, chacun lu avec ses octets ;
- dossiers, vides compris, terminés par « / », jamais lus ;
- TAR : noms sans « ./ » (rangés sans lui), liens écartés, racine exclue ;
- taille annoncée contrôlée avant lecture ; somme de contrôle fausse :
  AlteredMemberError, octets joints ;
- archive refermée même si sa liste ne peut pas être lue.

Vraies archives construites à la volée (7z.exe embarqué et WinRAR pour CB7
et CBR, comme les autres tests de lecteurs).
"""
import io
import os
import subprocess
import sys
import tarfile
import warnings
import zipfile
from pathlib import Path

import pytest

from modules.qt import archive_members
from modules.qt.archive_members import (AlteredMemberError, ArchiveReader, kind_from_extension,
                                        open_archive)
from modules.qt.archive_names import ArchiveRefusedError, MemberTooLargeError

ROOT = Path(__file__).resolve().parent.parent
SEVEN_ZIP = ROOT / "7zip" / "7z.exe"
WINRAR = Path(r"C:\Program Files\WinRAR\Rar.exe")


def make_zip(path, members):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")              # « Duplicate name » : voulu
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in members:
                zf.writestr(name, data)
    return str(path)


def names(reader):
    return [m.name for m in reader.members]


# --- ZIP -----------------------------------------------------------------------

def test_zip_order_folders_and_homonyms(tmp_path):
    cbz = make_zip(tmp_path / "a.cbz", [("10.jpg", b"ten"), ("Vide/", b""), ("2.jpg", b"two"),
                                        ("Ch1/01.jpg", b"c1"), ("2.jpg", b"two-bis")])
    with open_archive(cbz, "CBZ") as arc:
        assert names(arc) == ["2.jpg", "2.jpg", "10.jpg", "Ch1/01.jpg", "Vide/"]
        assert [m.name for m in arc.dirs()] == ["Vide/"]
        assert [arc.read(m) for m in arc.files()] == [b"two", b"two-bis", b"ten", b"c1"]
        assert [m.name for m in arc.images()] == ["2.jpg", "2.jpg", "10.jpg", "Ch1/01.jpg"]
        with pytest.raises(IsADirectoryError):
            arc.read(arc.dirs()[0])


def test_size_is_checked_before_reading(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_members, "MAX_MEMBER_SIZE", 3)
    cbz = make_zip(tmp_path / "a.cbz", [("01.jpg", b"1234")])
    with open_archive(cbz, "CBZ") as arc:
        with pytest.raises(MemberTooLargeError):
            arc.read(arc.files()[0])


def test_wrong_checksum_gives_the_bytes_with_the_error(tmp_path):
    cbz = tmp_path / "a.cbz"
    make_zip(cbz, [("01.jpg", b"AAAAAAAA")])
    data = cbz.read_bytes()
    cbz.write_bytes(data.replace(b"AAAAAAAA", b"AAAAAAAB", 1))   # stocké : octets en clair
    with open_archive(str(cbz), "CBZ") as arc:
        with pytest.raises(AlteredMemberError) as caught:
            arc.read(arc.files()[0])
    assert caught.value.data == b"AAAAAAAB"


def test_archive_closed_when_its_list_cannot_be_read(tmp_path, monkeypatch):
    cbz = make_zip(tmp_path / "a.cbz", [("01.jpg", b"x")])
    closed = []
    from modules.qt import archive_names
    real_open_zip = archive_names.open_zip

    class Zip:
        def __init__(self, path):
            self._zf = real_open_zip(path)

        def infolist(self):
            raise OSError("support retiré")

        def close(self):
            closed.append(True)
            self._zf.close()
    monkeypatch.setattr(archive_names, "open_zip", Zip)
    with pytest.raises(OSError):
        open_archive(cbz, "CBZ")
    assert closed == [True]


def test_not_a_zip_raises_at_opening(tmp_path):
    bad = tmp_path / "a.cbz"
    bad.write_bytes(b"pas une archive")
    with pytest.raises(zipfile.BadZipFile):
        open_archive(str(bad), "CBZ")
    with pytest.raises(ArchiveRefusedError):
        ArchiveReader(str(bad), "PDF")


def test_kind_from_extension():
    assert [kind_from_extension(p) for p in ("a.CBZ", "a.zip", "a.epub", "a.cbr", "a.cb7",
                                             "a.cbt", "a.pdf")] == [
        "CBZ", "CBZ", "EPUB", "CBR", "CB7", "CBT", None]


# --- EPUB ----------------------------------------------------------------------

def test_epub_pages_follow_the_reading_order(tmp_path):
    epub = make_zip(tmp_path / "a.epub", [
        ("mimetype", b"application/epub+zip"),
        ("META-INF/container.xml", b'<container><rootfiles><rootfile full-path="c.opf"/>'
                                   b'</rootfiles></container>'),
        ("c.opf", b'<package><manifest><item id="p" href="p.xhtml"/></manifest>'
                  b'<spine><itemref idref="p"/></spine></package>'),
        ("p.xhtml", b'<html><body><img src="zz.png"/></body></html>'),
        ("aa.png", b"a"), ("zz.png", b"z")])
    with open_archive(epub, "EPUB") as arc:
        assert [m.name for m in arc.epub_pages()] == ["zz.png", "aa.png"]


# --- TAR -----------------------------------------------------------------------

def test_tar_names_links_folders_and_order(tmp_path):
    cbt = tmp_path / "a.cbt"
    with tarfile.open(cbt, "w") as tf:
        for name in (".", "./Vide"):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            tf.addfile(info)
        link = tarfile.TarInfo("./01.jpg")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tf.addfile(link)
        for name, data in (("./b.jpg", b"b"), ("a.jpg", b"a"), ("./a.jpg", b"a-bis")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    with open_archive(str(cbt), "CBT") as arc:
        # « ./ » retiré avant de ranger : « a » avant « b » ; homonymes dans
        # l'ordre de l'archive ; lien et racine écartés
        assert names(arc) == ["a.jpg", "a.jpg", "b.jpg", "Vide/"]
        assert [arc.read(m) for m in arc.files()] == [b"a", b"a-bis", b"b"]


# --- CB7 et CBR ------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32" or not SEVEN_ZIP.exists(),
                    reason="7z.exe embarqué requis (Windows)")
def test_cb7_files_and_empty_folders(tmp_path):
    src = tmp_path / "src"
    (src / "Vide").mkdir(parents=True)
    (src / "Ch1").mkdir()
    (src / "10.jpg").write_bytes(b"ten")
    (src / "Ch1" / "2.jpg").write_bytes(b"two")
    cb7 = tmp_path / "a.cb7"
    subprocess.run([str(SEVEN_ZIP), "a", "-t7z", str(cb7), "*"], cwd=src, check=True,
                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    with open_archive(str(cb7), "CB7") as arc:
        assert names(arc) == ["10.jpg", "Ch1/", "Ch1/2.jpg", "Vide/"]
        assert [arc.read(m) for m in arc.files()] == [b"ten", b"two"]


def rar4_stored(files):
    """Archive RAR 4 dont chaque fichier est « stocké » (sans compression),
    lisible par rarfile sans programme externe ; deux fichiers peuvent y
    porter le même nom, ce que WinRAR ne fait pas."""
    import struct
    import zlib

    def block(htype, flags, body, add=b""):
        head = struct.pack("<BHH", htype, flags, 7 + len(body)) + body
        return struct.pack("<H", zlib.crc32(head) & 0xFFFF) + head + add

    out = b"Rar!\x1a\x07\x00" + block(0x73, 0, b"\x00" * 6)
    for name, data in files:
        raw = name.replace("/", "\\").encode("ascii")
        body = struct.pack("<IIBIIBBHI", len(data), len(data), 2, zlib.crc32(data), 0x5A000000,
                           20, 0x30, len(raw), 0x20) + raw
        out += block(0x74, 0x8000, body, data)
    return out + block(0x7B, 0x4000, b"")


UNRAR = ROOT / "unrar" / "UnRAR.exe"
needs_unrar = pytest.mark.skipif(sys.platform != "win32" or not UNRAR.exists(),
                                 reason="UnRAR.exe embarqué requis (Windows)")


@pytest.fixture
def unrar_tool(monkeypatch):
    import rarfile
    monkeypatch.setattr(rarfile, "UNRAR_TOOL", str(UNRAR))


def test_rar_homonym_is_never_read_by_its_name(tmp_path):
    """rarfile retrouve un membre par son nom, même par son RarInfo : lu par
    son nom, chacun recevrait le contenu du dernier, et une conversion
    supprimerait ensuite le CBR. _rar_read refuse donc un nom porté par
    plusieurs fichiers (le lecteur commun les lit autrement)."""
    import rarfile
    from modules.qt.archive_members import _rar_read
    cbr = tmp_path / "a.cbr"
    cbr.write_bytes(rar4_stored([("p.png", b"PREMIER"), ("p.png", b"SECOND"), ("q.png", b"Q")]))
    with rarfile.RarFile(str(cbr)) as rf:
        with pytest.raises(ArchiveRefusedError):
            _rar_read(rf, "p.png")
        assert _rar_read(rf, "q.png") == b"Q"


@needs_unrar
def test_rar_homonyms_are_each_read_with_their_own_content(tmp_path, unrar_tool):
    """Un nom porté par plusieurs fichiers : l'archive est extraite par UnRAR
    (qui renomme les doublons selon les collisions : un vrai « p(1).jpg »
    devient « p(1)(1).jpg »), chaque fichier y est retrouvé par son contenu.
    Les autres fichiers sont lus normalement ; le dossier temporaire est
    supprimé à la fermeture."""
    cbr = tmp_path / "a.cbr"
    cbr.write_bytes(rar4_stored([("Ch1/p.jpg", b"UN"), ("p.jpg", b"RACINE"),
                                 ("Ch1/p.jpg", b"DEUX"), ("Ch1/p.jpg", b"TROIS"),
                                 ("Ch1/p(1).jpg", b"VRAI"), ("noext", b"A"), ("noext", b"B")]))
    with open_archive(str(cbr), "CBR") as arc:
        assert [(m.name, arc.read(m)) for m in arc.files()] == [
            ("Ch1/p.jpg", b"UN"), ("Ch1/p.jpg", b"DEUX"), ("Ch1/p.jpg", b"TROIS"),
            ("Ch1/p(1).jpg", b"VRAI"), ("noext", b"A"), ("noext", b"B"), ("p.jpg", b"RACINE")]
        folder = arc._rar_extraction.folder
        assert os.path.isdir(folder)
    assert not os.path.exists(folder)


@needs_unrar
def test_rar_without_homonyms_is_never_extracted(tmp_path, unrar_tool):
    cbr = tmp_path / "a.cbr"
    cbr.write_bytes(rar4_stored([("p.png", b"P"), ("q.png", b"Q")]))
    with open_archive(str(cbr), "CBR") as arc:
        assert [arc.read(m) for m in arc.files()] == [b"P", b"Q"]
        assert arc._rar_extraction is None


@needs_unrar
def test_rar_homonyms_are_refused_when_a_name_would_leave_the_folder(tmp_path, unrar_tool,
                                                                     monkeypatch):
    """Avant toute extraction : un nom qui sortirait du dossier temporaire, ou
    un fichier au-delà de la limite de taille, rend les homonymes illisibles
    (rien n'est écrit) ; les autres fichiers restent lisibles."""
    cbr = tmp_path / "a.cbr"
    cbr.write_bytes(rar4_stored([("p.png", b"UN"), ("p.png", b"DEUX"), ("../x.png", b"X"),
                                 ("q.png", b"Q")]))
    with open_archive(str(cbr), "CBR") as arc:
        homonyms = [m for m in arc.files() if m.name == "p.png"]
        for member in homonyms:
            with pytest.raises(ArchiveRefusedError):
                arc.read(member)
        assert arc.read(next(m for m in arc.files() if m.name == "q.png")) == b"Q"
    monkeypatch.setattr(archive_members, "MAX_MEMBER_SIZE", 3)
    cbr.write_bytes(rar4_stored([("p.png", b"UN"), ("p.png", b"DEUX"), ("big.png", b"1234")]))
    with open_archive(str(cbr), "CBR") as arc:
        with pytest.raises(MemberTooLargeError):
            arc.read(next(m for m in arc.files() if m.name == "p.png"))


@pytest.mark.skipif(not WINRAR.exists(), reason="WinRAR requis pour créer un RAR")
def test_cbr_files_and_empty_folders(tmp_path):
    import rarfile
    rarfile.UNRAR_TOOL = str(ROOT / "unrar" / "UnRAR.exe")
    src = tmp_path / "src"
    (src / "Vide").mkdir(parents=True)
    (src / "10.jpg").write_bytes(b"ten")
    (src / "2.jpg").write_bytes(b"two")
    cbr = tmp_path / "a.cbr"
    subprocess.run([str(WINRAR), "a", "-r", "-idq", str(cbr), "*"], cwd=src, check=True,
                   capture_output=True)
    with open_archive(str(cbr), "CBR") as arc:
        assert names(arc) == ["2.jpg", "10.jpg", "Vide/"]
        assert [arc.read(m) for m in arc.files()] == [b"two", b"ten"]


# ---------------------------------------------------------------------------
# Commentaire d'archive (métadonnées ComicBookInfo)
# ---------------------------------------------------------------------------

CBI = b'{"ComicBookInfo/1.0": {"series": "Aaaa", "issue": "1"}}'


def test_zip_archive_comment_is_read(tmp_path):
    cbz = tmp_path / "a.cbz"
    with zipfile.ZipFile(cbz, "w") as zf:
        zf.writestr("01.jpg", b"img")
        zf.comment = CBI
    assert archive_members.archive_comment(str(cbz)) == CBI
    with open_archive(str(cbz), "CBZ") as arc:
        assert arc.comment() == CBI


@pytest.mark.skipif(not WINRAR.exists(), reason="WinRAR requis pour créer un RAR")
def test_rar_archive_comment_is_read(tmp_path):
    """Un CBR peut porter un commentaire (ComicTagger y range du
    ComicBookInfo) : lu, pour être repris quand il est converti en CBZ."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "01.jpg").write_bytes(b"img")
    comment = tmp_path / "comment.txt"
    comment.write_bytes(CBI)
    cbr = tmp_path / "a.cbr"
    subprocess.run([str(WINRAR), "a", "-idq", f"-z{comment}", str(cbr), "01.jpg"], cwd=src,
                   check=True, capture_output=True)
    assert archive_members.archive_comment(str(cbr)) == CBI
    with open_archive(str(cbr), "CBR") as arc:
        assert arc.comment() == CBI


def test_archives_without_comment_give_nothing(tmp_path):
    cbz = tmp_path / "a.cbz"
    with zipfile.ZipFile(cbz, "w") as zf:
        zf.writestr("01.jpg", b"img")
    cbt = tmp_path / "a.cbt"
    with tarfile.open(cbt, "w") as tf:
        info = tarfile.TarInfo("01.jpg")
        info.size = 3
        tf.addfile(info, io.BytesIO(b"img"))
    broken = tmp_path / "b.cbz"
    broken.write_bytes(b"PK\x03\x04 incomplet")
    for path in (cbz, cbt, broken, tmp_path / "absent.cbz"):
        assert archive_members.archive_comment(str(path)) == b""
