"""Lecture des membres d'archives à l'ouverture (archive_loader.py).

- _zip_member_reader : deux membres ZIP de même nom sont lus chacun avec son
  propre contenu (archive.read(nom) rend le dernier pour les deux, et le premier
  serait perdu au prochain enregistrement).
- tar_entry_name (archive_names.py) : une page de CBT garde son chemin
  complet (sans "./"), jamais le seul nom de base.

archive_loader.py importe PySide6 au niveau module : ses fonctions sont
extraites par AST et exécutées seules, sans aucun import Qt.
"""
import ast
import io
import os
import warnings
import zipfile
from pathlib import Path

from modules.qt.archive_names import tar_entry_name, without_epub_rank

ROOT = Path(__file__).resolve().parent.parent
_FUNCS = ("_zip_member_reader", "_attach_read_errors", "_check_member_size",
          "_tar_member_reader", "_tar_info_read")
_CONSTANTS = ("MAX_MEMBER_SIZE",)


def _is_constant(node):
    return (isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id in _CONSTANTS)


def _load():
    source = (ROOT / "modules" / "qt" / "archive_loader.py").read_text(encoding="utf-8")
    nodes = [n for n in ast.parse(source).body
             if (isinstance(n, ast.FunctionDef) and n.name in _FUNCS) or _is_constant(n)]
    assert {n.name for n in nodes if isinstance(n, ast.FunctionDef)} == set(_FUNCS)
    ns = {"os": os, "_tar_entry_name": tar_entry_name,
          "_without_epub_rank": without_epub_rank}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "archive_loader.py", "exec"), ns)
    return ns


_NS = _load()
zip_member_reader = _NS["_zip_member_reader"]
attach_read_errors = _NS["_attach_read_errors"]


def make_zip(members):
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # "Duplicate name"
        with zipfile.ZipFile(buf, "w") as zf:
            for name, data in members:
                zf.writestr(name, data)
    return zipfile.ZipFile(io.BytesIO(buf.getvalue()))


def test_homonyms_are_read_with_their_own_content():
    archive = make_zip([("p.jpg", b"first"), ("q.jpg", b"other"), ("p.jpg", b"second")])
    read = zip_member_reader(archive)
    assert [read(n) for n in archive.namelist()] == [b"first", b"other", b"second"]


def test_single_member_can_be_read_again():
    archive = make_zip([("p.jpg", b"only")])
    read = zip_member_reader(archive)
    assert read("p.jpg") == b"only"
    assert read("p.jpg") == b"only"


def test_unknown_name_falls_back_to_archive_read():
    archive = make_zip([("p.jpg", b"only")])
    read = zip_member_reader(archive)
    try:
        read("missing.jpg")
    except KeyError:
        pass
    else:
        raise AssertionError("un membre absent doit lever KeyError, comme archive.read")


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


def test_errors_without_corrupted_page_are_kept():
    xml = {"orig_name": "ComicInfo.xml", "bytes": None, "is_image": False, "is_corrupted": False}
    errors = ["ComicInfo.xml: Bad CRC-32", "archive endommagée"]
    assert attach_read_errors([xml], errors) == errors


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


# ── TAR : homonymes, liens, taille annoncée ─────────────────────────────────

tar_member_reader = _NS["_tar_member_reader"]


def make_tar(members):
    """members : [(nom, octets)] ou [(nom, TarInfo préparé, octets|None)]."""
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for item in members:
            if len(item) == 2:
                name, data = item
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            else:
                _name, info, data = item
                tf.addfile(info, io.BytesIO(data) if data is not None else None)
    buf.seek(0)
    return tarfile.open(fileobj=buf, mode="r")


def test_tar_homonyms_are_read_with_their_own_content():
    archive = make_tar([("p.jpg", b"first"), ("q.jpg", b"other"), ("p.jpg", b"second")])
    read = tar_member_reader(archive)
    names = [m.name for m in archive.getmembers() if m.isfile()]
    assert [read(n) for n in names] == [b"first", b"other", b"second"]


def test_tar_link_homonym_is_never_read_instead_of_the_file():
    """Un lien symbolique placé après une page du même nom : getmember(nom)
    le rendrait à la place de la page."""
    import tarfile
    link = tarfile.TarInfo("p.jpg")
    link.type = tarfile.SYMTYPE
    link.linkname = "../../outside.jpg"
    archive = make_tar([("p.jpg", b"page"), ("p.jpg", link, None)])
    assert tar_member_reader(archive)("p.jpg") == b"page"


def test_member_larger_than_the_limit_is_refused_before_reading():
    old = _NS["MAX_MEMBER_SIZE"]
    _NS["MAX_MEMBER_SIZE"] = 4
    try:
        tar_read = tar_member_reader(make_tar([("big.jpg", b"12345"), ("ok.jpg", b"1234")]))
        zip_read = zip_member_reader(make_zip([("big.jpg", b"12345"), ("ok.jpg", b"1234")]))
        for read in (tar_read, zip_read):
            assert read("ok.jpg") == b"1234"
            try:
                read("big.jpg")
            except ValueError as e:
                assert ">" in str(e)
            else:
                raise AssertionError("un membre trop grand doit être refusé")
    finally:
        _NS["MAX_MEMBER_SIZE"] = old
