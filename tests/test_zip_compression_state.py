"""État de compression d'un CBZ (archive_loader._detect_zip_compression_state), lu par
l'indicateur ZIP de la barre de statut et son clic. Fonction extraite par AST,
archive_loader.py important Qt."""
import os
import zipfile

import pytest

import ast_loader
from modules.qt.archive_names import open_zip

detect = ast_loader.load("archive_loader.py", {"_detect_zip_compression_state"},
                         os=os, zipfile=zipfile, open_zip=open_zip)["_detect_zip_compression_state"]

STORED, DEFLATED = zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED


def make_cbz(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, method in members:
            zf.writestr(name, b"" if name.endswith("/") else b"contenu " * 100, compress_type=method)
    return str(path)


@pytest.mark.parametrize("members, expected", [
    ([("p01.jpg", STORED), ("p02.jpg", STORED)], "stored"),
    ([("p01.jpg", DEFLATED), ("p02.jpg", DEFLATED)], "deflated"),
    # Le premier fichier ne dit rien des autres
    ([("ComicInfo.xml", STORED), ("p01.jpg", DEFLATED)], "deflated"),
    ([("p01.jpg", DEFLATED), ("p02.jpg", STORED)], "deflated"),
    # Un dossier n'est pas un fichier compressé
    ([("Aaaa/", DEFLATED), ("Aaaa/p01.jpg", STORED)], "stored"),
    ([("Aaaa/", STORED)], None),
])
def test_state_comes_from_every_file(tmp_path, members, expected):
    assert detect(make_cbz(tmp_path / "Aaaa.cbz", members)) == expected


def test_no_state_without_a_readable_cbz(tmp_path):
    damaged = tmp_path / "Aaaa.cbz"
    damaged.write_bytes(b"pas un ZIP")
    assert detect(str(damaged)) is None
    assert detect(make_cbz(tmp_path / "Aaaa.zip", [("p01.jpg", STORED)])) is None
    assert detect(None) is None
