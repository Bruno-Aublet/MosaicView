"""Première page d'une archive (archive_members.first_page) : couverture de la
Bibliothèque, de l'import de métadonnées, vignette de la recompression. C'est
la page que montre l'ouverture de la BD dans un panneau, lue avec les mêmes
protections que l'ouverture.

- ordre de l'ouverture (archive_names.natural_key) : « 2.jpg » avant
  « 10.jpg », dossier compris (un tri sur le seul nom mélangeait les
  chapitres), jamais un tri simple ;
- deux membres de même nom : le premier, celui que l'ouverture montre en
  premier (jamais une lecture par nom, qui rend le dernier) ;
- taille annoncée contrôlée avant lecture (MAX_MEMBER_SIZE) ;
- CBT : un lien n'est pas une page ; EPUB : ordre de lecture du livre ;
- format d'après le contenu, pas d'après l'extension.

batch_metadata_dialog_qt.py importe Qt : sa méthode est extraite par AST.
"""
import io
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
from PIL import Image

import ast_loader
from modules.qt import archive_members
from modules.qt.archive_members import first_page
from modules.qt.archive_names import MemberTooLargeError

SEVEN_ZIP = Path(__file__).resolve().parent.parent / "7zip" / "7z.exe"


def png(color):
    buf = io.BytesIO()
    Image.new("RGB", (6, 4), color).save(buf, "PNG")
    return buf.getvalue()


def make_zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
    return str(path)


def test_numbers_by_value_not_simple_sort(tmp_path):
    cbz = make_zip(tmp_path / "a.cbz", [("10.png", png("red")), ("2.png", png("blue"))])
    assert first_page(cbz) == ("2.png", png("blue"))


def test_the_folder_is_part_of_the_order(tmp_path):
    cbz = make_zip(tmp_path / "a.cbz", [("Ch2/001.png", png("red")), ("Ch1/002.png", png("blue")),
                                        ("ComicInfo.xml", b"<x/>"), ("Vide/", b"")])
    assert first_page(cbz) == ("Ch1/002.png", png("blue"))


def test_homonyms_give_the_first_member(tmp_path):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")              # « Duplicate name » : voulu
        cbz = make_zip(tmp_path / "a.cbz", [("01.png", png("red")), ("01.png", png("blue"))])
    assert first_page(cbz) == ("01.png", png("red"))


def test_without_any_image_gives_nothing(tmp_path):
    assert first_page(make_zip(tmp_path / "a.cbz", [("info.nfo", b"x")])) is None
    (tmp_path / "x.cbz").write_bytes(b"pas une archive")
    assert first_page(str(tmp_path / "x.cbz")) is None


def test_size_is_checked_before_reading(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_members, "MAX_MEMBER_SIZE", 10)
    cbz = make_zip(tmp_path / "a.cbz", [("01.png", png("red"))])
    with pytest.raises(MemberTooLargeError):
        first_page(cbz)


def test_format_from_the_content_not_the_extension(tmp_path):
    misnamed = make_zip(tmp_path / "a.cbr", [("01.png", png("red"))])
    assert first_page(misnamed) == ("01.png", png("red"))


def test_cbt_link_is_not_a_page(tmp_path):
    cbt = tmp_path / "a.cbt"
    with tarfile.open(cbt, "w") as tf:
        link = tarfile.TarInfo("./01.png")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tf.addfile(link)
        for name, data in (("./10.png", png("red")), ("./2.png", png("blue"))):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    assert first_page(str(cbt)) == ("2.png", png("blue"))


def test_epub_follows_the_reading_order(tmp_path):
    epub = make_zip(tmp_path / "a.epub", [
        ("mimetype", b"application/epub+zip"),
        ("META-INF/container.xml", b'<container><rootfiles><rootfile full-path="OEBPS/c.opf"/>'
                                   b'</rootfiles></container>'),
        ("OEBPS/c.opf", b'<package><manifest><item id="p" href="p.xhtml"/></manifest>'
                        b'<spine><itemref idref="p"/></spine></package>'),
        ("OEBPS/p.xhtml", b'<html><body><img src="img/zz.png"/></body></html>'),
        ("OEBPS/img/aa.png", png("red")), ("OEBPS/img/zz.png", png("blue"))])
    assert first_page(epub) == ("OEBPS/img/zz.png", png("blue"))


@pytest.mark.skipif(sys.platform != "win32" or not SEVEN_ZIP.exists(),
                    reason="7z.exe embarqué requis (Windows)")
def test_cb7(tmp_path):
    src = tmp_path / "src"
    (src / "Ch1").mkdir(parents=True)
    (src / "10.png").write_bytes(png("red"))
    (src / "Ch1" / "1.png").write_bytes(png("blue"))
    cb7 = tmp_path / "a.cb7"
    subprocess.run([str(SEVEN_ZIP), "a", "-t7z", str(cb7), "*"], cwd=src, check=True,
                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    assert first_page(str(cb7)) == ("10.png", png("red"))       # « 10 » avant « Ch1 »


def test_metadata_import_shows_the_first_page_as_cover(tmp_path):
    """Couverture d'un CBT dans la fenêtre ComicVine : la première page de
    l'ouverture, plus la première dans l'ordre simple (« 10 » avant « 2 »)."""
    extract = ast_loader.load_methods("batch_metadata_dialog_qt.py", "_BatchMetadataOrchestrator",
                                      {"_extract_non_cbz"}, os=os, _=lambda key, **kw: key)
    cbt = tmp_path / "a.cbt"
    with tarfile.open(cbt, "w") as tf:
        for name, data in (("./10.png", png("red")), ("./2.png", png("blue"))):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    cover, manifest = extract()._extract_non_cbz(str(cbt), ".cbt")
    assert (cover["orig_name"], cover["bytes"]) == ("2.png", png("blue"))
    assert manifest["_cbt_path"] == str(cbt)
