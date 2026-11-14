"""Lecture des membres d'archives à l'ouverture (archive_members.py et
archive_loader.py).

- lecteur commun (archive_members.open_archive) : deux membres ZIP ou TAR de
  même nom sont lus chacun avec son propre contenu (archive.read(nom) rend le
  dernier pour les deux, et le premier serait perdu au prochain
  enregistrement) ; un lien TAR homonyme n'est jamais lu à la place de la
  page ; un membre trop grand est refusé avant lecture ;
- tar_entry_name (archive_names.py) : une page de CBT garde son chemin
  complet (sans "./"), jamais le seul nom de base ;
- create_page_entry : une page refusée pour sa taille garde sa raison en
  octets, écrite dans la langue du moment à l'affichage seulement.

archive_loader.py importe PySide6 au niveau module : ses fonctions sont
extraites par AST et exécutées seules, sans aucun import Qt.
"""
import io
import os
import warnings
import zipfile

import pytest

import ast_loader
from modules.qt import archive_members
from modules.qt.archive_members import open_archive
from modules.qt.archive_names import IMAGE_EXTS, MAX_MEMBER_SIZE, tar_entry_name, without_epub_rank
from modules.qt.entries import create_entry, is_unreadable_file

_READERS = ("_check_member_size", "AlteredMemberError")
_LOADER_FUNCS = ("_attach_read_errors", "create_page_entry")


def _load():
    # MAX_MEMBER_SIZE : la vraie limite (archive_names)
    return ast_loader.load_from({"archive_members.py": set(_READERS),
                                 "archive_loader.py": set(_LOADER_FUNCS)},
                           os=os, _tar_entry_name=tar_entry_name,
                           _without_epub_rank=without_epub_rank, MAX_MEMBER_SIZE=MAX_MEMBER_SIZE,
                           is_unreadable_file=is_unreadable_file, create_entry=create_entry,
                           IMAGE_EXTS=IMAGE_EXTS)


_NS = _load()
attach_read_errors = _NS["_attach_read_errors"]


def make_zip(path, members):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # "Duplicate name"
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in members:
                zf.writestr(name, data)
    return str(path)


def test_homonyms_are_read_with_their_own_content(tmp_path):
    cbz = make_zip(tmp_path / "a.cbz", [("p.jpg", b"first"), ("q.jpg", b"other"),
                                        ("p.jpg", b"second")])
    with open_archive(cbz, "CBZ") as archive:
        assert [archive.read(m) for m in archive.files()] == [b"first", b"second", b"other"]


def test_single_member_can_be_read_again(tmp_path):
    cbz = make_zip(tmp_path / "a.cbz", [("p.jpg", b"only")])
    with open_archive(cbz, "CBZ") as archive:
        [member] = archive.files()
        assert archive.read(member) == b"only"
        assert archive.read(member) == b"only"


def test_tar_names_keep_their_folder():
    assert tar_entry_name("Ch1/001.jpg") == "Ch1/001.jpg"
    assert tar_entry_name("Ch2/001.jpg") != tar_entry_name("Ch1/001.jpg")


def unread(name):
    return {"orig_name": name, "bytes": None, "is_image": True, "is_corrupted": True,
            "corruption_reason": None}


def test_read_error_becomes_corruption_reason_and_leaves_the_list():
    """Une page illisible n'est signalée qu'une fois (vignette à cadre rouge,
    raison dans l'info-bulle), pas aussi dans « Fichiers ignorés »."""
    page = unread("03.jpg")
    ok = {"orig_name": "01.jpg", "bytes": b"x", "is_image": True, "is_corrupted": False}
    remaining = attach_read_errors([ok, page], ["03.jpg: Bad CRC-32 for file '03.jpg'"])
    assert remaining == []
    assert page["corruption_reason"] == "Bad CRC-32 for file '03.jpg'"


def test_unreadable_other_file_gets_its_reason_and_leaves_the_list():
    """Un fichier qui n'est pas une image reste dans la mosaïque (cadre
    rouge) : « Fichiers ignorés » le dirait absent. Seules les erreurs sans
    entrée restent dans la liste."""
    xml = {"orig_name": "ComicInfo.xml", "bytes": None, "is_image": False, "is_corrupted": False}
    nfo = {"orig_name": "Aaaa.nfo", "bytes": b"x", "is_image": False, "is_corrupted": False}
    errors = ["ComicInfo.xml: Bad CRC-32", "Aaaa.nfo: autre", "archive endommagée"]
    assert attach_read_errors([xml, nfo], errors) == ["Aaaa.nfo: autre", "archive endommagée"]
    assert xml["corruption_reason"] == "Bad CRC-32" and nfo.get("corruption_reason") is None


def test_unreadable_file_is_a_non_image_without_data():
    """Ni une image (page illisible : is_corrupted), ni un dossier, ni l'icône
    « .. » de la mosaïque, qui n'ont pas de données non plus."""
    assert is_unreadable_file({"orig_name": "Aaaa.nfo", "bytes": None, "is_image": False})
    assert not is_unreadable_file({"orig_name": "Aaaa.nfo", "bytes": b"", "is_image": False})
    assert not is_unreadable_file({"orig_name": "a.jpg", "bytes": None, "is_image": True})
    assert not is_unreadable_file({"orig_name": "Aaaa/", "bytes": None, "is_dir": True})
    assert not is_unreadable_file({"orig_name": "..", "bytes": None, "is_parent_dir": True})


def test_prefixed_epub_and_tar_names_are_matched():
    pages = [unread("NEW-a.jpg"), unread("0002_b.jpg"), unread("sub/c.jpg"),
             unread("NEW-0001_d.jpg")]
    remaining = attach_read_errors(pages, ["a.jpg: e1", "OEBPS/img/b.jpg: e2", "./sub/c.jpg: e3",
                                           "OEBPS/d.jpg: e4"])
    assert remaining == []
    assert [p["corruption_reason"] for p in pages] == ["e1", "e2", "e3", "e4"]


def test_homonyms_each_get_their_own_reason():
    first, second = unread("p.jpg"), unread("p.jpg")
    attach_read_errors([first, second], ["p.jpg: e1", "p.jpg: e2"])
    assert (first["corruption_reason"], second["corruption_reason"]) == ("e1", "e2")


def test_tar_names_lose_leading_dot_and_slash():
    assert tar_entry_name("./001.jpg") == "001.jpg"
    assert tar_entry_name("././sub/002.jpg") == "sub/002.jpg"
    assert tar_entry_name("/abs/003.jpg") == "abs/003.jpg"
    assert tar_entry_name("sub\\004.jpg") == "sub/004.jpg"
    # Préfixes mêlés, dans n'importe quel ordre
    assert tar_entry_name("/./005.jpg") == "005.jpg"
    assert tar_entry_name("\\.\\.//006.jpg") == "006.jpg"


def test_tar_names_not_in_utf8_are_read_in_their_encoding():
    """Nom latin-1 lu par tarfile avec des caractères de substitution : relu
    en cp1252, sinon latin-1 (octet non défini en cp1252) ; un nom UTF-8
    reste inchangé."""
    import tarfile
    for written, expected in [("Été/café.jpg", "Été/café.jpg"), ("x\x81.jpg", "x\x81.jpg")]:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT, encoding="latin-1") as tf:
            info = tarfile.TarInfo(written)
            tf.addfile(info, io.BytesIO(b""))
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r") as tf:
            read_name = tf.getmembers()[0].name
        assert read_name != expected           # bien lu avec des substitutions
        assert tar_entry_name(read_name) == expected
        zipfile.ZipFile(io.BytesIO(), "w").writestr(tar_entry_name(read_name), b"")
    assert tar_entry_name("Été/café.jpg") == "Été/café.jpg"


# ── TAR : homonymes, liens, taille annoncée ─────────────────────────────────

def make_tar(path, members):
    """members : [(nom, octets)] ou [(nom, TarInfo préparé, octets|None)]."""
    import tarfile
    with tarfile.open(path, mode="w") as tf:
        for item in members:
            if len(item) == 2:
                name, data = item
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            else:
                _name, info, data = item
                tf.addfile(info, io.BytesIO(data) if data is not None else None)
    return str(path)


def test_tar_homonyms_are_read_with_their_own_content(tmp_path):
    cbt = make_tar(tmp_path / "a.cbt", [("p.jpg", b"first"), ("q.jpg", b"other"),
                                        ("p.jpg", b"second")])
    with open_archive(cbt, "CBT") as archive:
        assert [archive.read(m) for m in archive.files()] == [b"first", b"second", b"other"]


def test_tar_link_homonym_is_never_read_instead_of_the_file(tmp_path):
    """Un lien symbolique placé après une page du même nom : getmember(nom)
    le rendrait à la place de la page. Le lien n'est pas un membre."""
    import tarfile
    link = tarfile.TarInfo("p.jpg")
    link.type = tarfile.SYMTYPE
    link.linkname = "../../outside.jpg"
    cbt = make_tar(tmp_path / "a.cbt", [("p.jpg", b"page"), ("p.jpg", link, None)])
    with open_archive(cbt, "CBT") as archive:
        [member] = archive.files()
        assert archive.read(member) == b"page"


def test_page_refused_for_its_size_keeps_the_size_in_bytes():
    """Raison gardée en octets (MemberTooLargeError.raw_reason) : écrite avec
    son unité, elle resterait dans la langue du moment de la lecture."""
    too_big = MAX_MEMBER_SIZE + 1
    entry, message = _NS["create_page_entry"]("big.jpg", lambda: _NS["_check_member_size"](too_big))
    assert message == f"{too_big} > {MAX_MEMBER_SIZE}"
    assert entry["is_corrupted"] and entry["bytes"] is None


def test_member_larger_than_the_limit_is_refused_before_reading(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_members, "MAX_MEMBER_SIZE", 4)
    members = [("big.jpg", b"12345"), ("ok.jpg", b"1234")]
    for path, kind in ((make_tar(tmp_path / "a.cbt", members), "CBT"),
                       (make_zip(tmp_path / "a.cbz", members), "CBZ")):
        with open_archive(path, kind) as archive:
            big, ok = archive.files()
            assert archive.read(ok) == b"1234"
            with pytest.raises(ValueError, match=">"):
                archive.read(big)
