"""LibraryDB.reindex_files n'indexe que les fichiers des répertoires de la base.

Un fichier enregistré ailleurs (« Enregistrer sous » hors de la bibliothèque,
autre base ouverte entre-temps) ne doit jamais y être ajouté : il y apparaîtrait
avec un chemin relatif du type ../../…
"""
import os
import zipfile

from modules.qt.library_db import LibraryDB


def make_cbz(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("page01.jpg", b"fake image bytes")
    return path


def relative_paths(db):
    return {row["relative_path"] for row in
            db._conn.execute("SELECT relative_path FROM comics").fetchall()}


def make_db(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    return LibraryDB.create(str(tmp_path / "lib.mvdb"), master), master


def test_file_in_master_dir_is_indexed(tmp_path):
    db, master = make_db(tmp_path)
    try:
        db.reindex_files([make_cbz(os.path.join(master, "a.cbz"))])
        assert relative_paths(db) == {"a.cbz"}
    finally:
        db.close()


def test_file_in_subfolder_of_master_is_indexed(tmp_path):
    db, master = make_db(tmp_path)
    try:
        db.reindex_files([make_cbz(os.path.join(master, "serie", "b.cbz"))])
        assert relative_paths(db) == {"serie/b.cbz"}
    finally:
        db.close()


def test_file_outside_library_is_ignored(tmp_path):
    db, _master = make_db(tmp_path)
    try:
        db.reindex_files([make_cbz(str(tmp_path / "elsewhere" / "c.cbz"))])
        assert relative_paths(db) == set()
    finally:
        db.close()


def test_sibling_folder_with_common_prefix_is_ignored(tmp_path):
    """« comics2 » commence comme « comics » sans en être un sous-dossier."""
    db, _master = make_db(tmp_path)
    try:
        db.reindex_files([make_cbz(str(tmp_path / "comics2" / "d.cbz"))])
        assert relative_paths(db) == set()
    finally:
        db.close()


def test_file_in_additional_dir_is_indexed(tmp_path):
    db, _master = make_db(tmp_path)
    extra = str(tmp_path / "extra")
    os.makedirs(extra)
    try:
        db.add_directory(extra)
        db.reindex_files([make_cbz(os.path.join(extra, "e.cbz"))])
        assert len(relative_paths(db)) == 1
    finally:
        db.close()


def test_unindexed_extension_in_library_is_ignored(tmp_path):
    """Extension que scan() n'indexe pas (_ALL_EXTS) : ignorée de même."""
    db, master = make_db(tmp_path)
    try:
        path = os.path.join(master, "setup.exe")
        with open(path, "w") as f:
            f.write("x")
        db.reindex_files([path])
        assert relative_paths(db) == set()
    finally:
        db.close()
