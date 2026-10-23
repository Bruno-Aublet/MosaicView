"""Écriture réelle du CBZ d'un panneau (Enregistrer, Enregistrer sous,
Enregistrer la sélection, Appliquer) : file_operations_qt.
_write_document_with_progress et _write_document_unlocked, avec les vraies
fonctions d'écriture et de contrôle (replace_file_atomically,
check_written_cbz_structure, cbz_members).

Vérifié sur le fichier produit, relu :
- toutes les pages, dans l'ordre, avec leurs octets ; dossiers ignorés ;
  homonymes gardés sous des noms distincts ;
- commentaire d'archive du fichier ouvert (métadonnées ComicBookInfo)
  recopié, au même endroit comme dans un autre fichier ; jamais pour une
  sélection ;
- document fermé ou remplacé pendant l'écriture : abandon, fichier existant
  intact, aucun temporaire laissé ;
- action enregistrée dans l'historique pendant l'écriture : le document n'est
  pas déclaré enregistré ;
- marque des pages altérées retirée seulement quand le document entier est
  écrit dans son fichier.

file_operations_qt.py importe PySide6 : les fonctions sont extraites par AST
(tests/ast_loader.py), sans Qt.
"""
import io
import os
import zipfile

import pytest
from PIL import Image

from modules.qt.archive_names import JPEG_EXTS
from modules.qt.entries import is_altered, mark_altered, open_page_image, save_image_to_bytes
from modules.qt.image_ops import has_several_frames
from modules.qt.state import AppState
from modules.qt.undo_redo import history_top, save_state_data
from modules.qt.utils import (MissingPageDataError, cbz_members, check_written_cbz_structure,
                              replace_file_atomically)

import ast_loader

NS = ast_loader.load(
    "file_operations_qt.py",
    {"_DocumentChangedDuringSave", "_write_document_with_progress", "_write_document_unlocked",
     "_write_zip_with_progress", "_zip_comment", "_bytes_with_entry_dpi",
     "_DPI_WRITABLE_EXTS", "_JPEG_EXTS", "jpeg_bytes_with_dpi"},
    os=os, io=io, zipfile=zipfile, Image=Image, JPEG_EXTS=JPEG_EXTS,
    history_top=history_top, replace_file_atomically=replace_file_atomically,
    check_written_cbz_structure=check_written_cbz_structure, cbz_members=cbz_members,
    MissingPageDataError=MissingPageDataError, open_page_image=open_page_image,
    save_image_to_bytes=save_image_to_bytes, has_several_frames=has_several_frames)
write_document = NS["_write_document_with_progress"]
DocumentChanged = NS["_DocumentChangedDuringSave"]

COMMENT = b'{"ComicBookInfo/1.0": {"series": "Aaaa"}}'


def png(color, dpi=None):
    buf = io.BytesIO()
    kwargs = {"dpi": (dpi, dpi)} if dpi else {}
    Image.new("RGB", (8, 6), color).save(buf, "PNG", **kwargs)
    return buf.getvalue()


def page(name, data, **extra):
    entry = {"orig_name": name, "bytes": data, "extension": os.path.splitext(name)[1],
             "is_image": True}
    entry.update(extra)
    return entry


def document(*entries, current_file=None):
    st = AppState()
    st.images_data = list(entries)
    st.current_file = current_file
    save_state_data(st, force=True)
    return st


def existing_cbz(path, comment=b""):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("old.png", png((9, 9, 9)))
        zf.comment = comment
    return path.read_bytes()


def members(path):
    with zipfile.ZipFile(path) as zf:
        return [(info.filename, zf.read(info)) for info in zf.infolist()], zf.comment


class Overlay:
    """Progression « Sauvegarde X% » : update() traite les événements dans
    l'application, d'où ce qui peut s'y passer (on_update(n), n-ième page)."""

    def __init__(self, on_update=None):
        self.percents = []
        self._on_update = on_update

    def update(self, percent, process_events=True):
        self.percents.append(percent)
        if self._on_update:
            self._on_update(len(self.percents))


def folder_content(path):
    return sorted(os.listdir(path))


# ---------------------------------------------------------------------------
# Contenu écrit
# ---------------------------------------------------------------------------

def test_saved_cbz_has_every_page_in_order_with_its_bytes(tmp_path):
    a, b, c = png((255, 0, 0)), png((0, 255, 0)), png((0, 0, 255))
    st = document(page("Ch1/", None, is_dir=True, is_image=False), page("Ch1/01.png", a),
                  page("02.png", b), page("02.png", c), page("ComicInfo.xml", b"<ComicInfo/>",
                                                             is_image=False))
    out = tmp_path / "Aaaa.cbz"
    overlay = Overlay()
    assert write_document(str(out), st, overlay, 0) is True
    written, comment = members(out)
    assert written == [("Ch1/01.png", a), ("02.png", b), ("02_2.png", c),
                       ("ComicInfo.xml", b"<ComicInfo/>")]
    assert comment == b""
    assert overlay.percents[-1] == 100
    assert not st.operation_running()


def test_page_resolution_is_written_in_the_file(tmp_path):
    st = document(page("01.png", png((1, 2, 3)), dpi=300))
    out = tmp_path / "Aaaa.cbz"
    write_document(str(out), st, None, 0)
    [(_name, data)], _comment = members(out)
    assert round(Image.open(io.BytesIO(data)).info["dpi"][0]) == 300


def test_save_over_the_open_file_keeps_its_archive_comment(tmp_path):
    path = tmp_path / "Aaaa.cbz"
    existing_cbz(path, COMMENT)
    st = document(page("01.png", png((1, 1, 1))), current_file=str(path))
    write_document(str(path), st, None, 0)
    written, comment = members(path)
    assert comment == COMMENT
    assert [name for name, _data in written] == ["01.png"]


def test_save_as_another_file_keeps_the_archive_comment(tmp_path):
    source = tmp_path / "Aaaa.cbz"
    existing_cbz(source, COMMENT)
    st = document(page("01.png", png((1, 1, 1))), current_file=str(source))
    out = tmp_path / "Bbbb.cbz"
    write_document(str(out), st, None, 0)
    assert members(out)[1] == COMMENT


def test_saved_selection_has_no_archive_comment(tmp_path):
    source = tmp_path / "Aaaa.cbz"
    existing_cbz(source, COMMENT)
    first, second = page("01.png", png((1, 1, 1))), page("02.png", png((2, 2, 2)))
    st = document(first, second, current_file=str(source))
    out = tmp_path / "Bbbb.cbz"
    write_document(str(out), st, None, 0, entries=[second])
    written, comment = members(out)
    assert [name for name, _data in written] == ["02.png"]
    assert comment == b""


# ---------------------------------------------------------------------------
# Document fermé, remplacé ou modifié pendant l'écriture
# ---------------------------------------------------------------------------

def test_document_closed_during_the_write_keeps_the_existing_file(tmp_path):
    path = tmp_path / "Aaaa.cbz"
    before = existing_cbz(path, COMMENT)
    st = document(page("01.png", png((1, 1, 1))), page("02.png", png((2, 2, 2))),
                  page("03.png", png((3, 3, 3))), current_file=str(path))

    def close_document(n):
        if n == 1:                       # force_close_file : pages vidées
            for entry in st.images_data:
                entry["bytes"] = None
            st.doc_generation += 1

    with pytest.raises(DocumentChanged):
        write_document(str(path), st, Overlay(close_document), 0)
    assert path.read_bytes() == before
    assert folder_content(tmp_path) == ["Aaaa.cbz"]
    assert not st.operation_running()


def test_document_replaced_after_the_last_page_keeps_the_existing_file(tmp_path):
    """Pages encore présentes, mais le panneau montre un autre fichier : le
    CBZ écrit ne correspond plus au document du panneau."""
    path = tmp_path / "Aaaa.cbz"
    before = existing_cbz(path)
    st = document(page("01.png", png((1, 1, 1))), page("02.png", png((2, 2, 2))),
                  current_file=str(path))

    def replace_document(n):
        if n == 2:
            st.doc_generation += 1

    with pytest.raises(DocumentChanged):
        write_document(str(path), st, Overlay(replace_document), 0)
    assert path.read_bytes() == before
    assert folder_content(tmp_path) == ["Aaaa.cbz"]


def test_page_without_data_is_refused_and_the_existing_file_kept(tmp_path):
    path = tmp_path / "Aaaa.cbz"
    before = existing_cbz(path)
    st = document(page("01.png", png((1, 1, 1))), page("02.png", None), current_file=str(path))
    with pytest.raises(MissingPageDataError):
        write_document(str(path), st, None, 0)
    assert path.read_bytes() == before
    assert folder_content(tmp_path) == ["Aaaa.cbz"]


def test_change_recorded_during_the_write_is_not_reported_saved(tmp_path):
    st = document(page("01.png", png((1, 1, 1))), page("02.png", png((2, 2, 2))))

    def modify(n):
        if n == 1:
            st.images_data[1]["bytes"] = png((7, 7, 7))
            save_state_data(st, force=True)

    out = tmp_path / "Aaaa.cbz"
    assert write_document(str(out), st, Overlay(modify), 0) is False


# ---------------------------------------------------------------------------
# Pages altérées (somme de contrôle fausse à la lecture)
# ---------------------------------------------------------------------------

def test_saving_the_document_into_its_file_forgets_the_altered_marks(tmp_path):
    altered = page("01.png", png((1, 1, 1)))
    mark_altered(altered)
    path = tmp_path / "Aaaa.cbz"
    existing_cbz(path)
    st = document(altered, current_file=str(path))
    write_document(str(path), st, None, 0)
    assert not is_altered(altered)


def test_saving_a_selection_keeps_the_altered_marks(tmp_path):
    """Le fichier du document, lui, n'a pas changé : ses pages y restent altérées."""
    altered = page("01.png", png((1, 1, 1)))
    mark_altered(altered)
    st = document(altered, page("02.png", png((2, 2, 2))))
    write_document(str(tmp_path / "Bbbb.cbz"), st, None, 0, entries=[altered])
    assert is_altered(altered)
