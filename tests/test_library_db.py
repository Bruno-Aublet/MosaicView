import os
import zipfile

import pytest

from modules.qt.library_db import LibraryDB


def make_cbz(path, with_comicinfo=False, series=None, number=None, n_images=2):
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(n_images):
            zf.writestr(f"page{i:02d}.jpg", b"fake image bytes")
        if with_comicinfo:
            series_tag = f"<Series>{series}</Series>" if series else ""
            number_tag = f"<Number>{number}</Number>" if number else ""
            xml = (
                '<?xml version="1.0"?>\r\n<ComicInfo>\r\n'
                f"  {series_tag}\r\n  {number_tag}\r\n"
                "</ComicInfo>"
            )
            zf.writestr("ComicInfo.xml", xml)
    return path


# ---------------------------------------------------------------------------
# Cycle de vie
# ---------------------------------------------------------------------------

def test_create_creates_db_file_and_master_dir(tmp_path):
    db_path = str(tmp_path / "lib.mvdb")
    master = str(tmp_path / "comics")
    os.makedirs(master)

    db = LibraryDB.create(db_path, master)
    try:
        assert os.path.exists(db_path)
        assert db.get_master_dir() == os.path.normpath(master)
    finally:
        db.close()


def test_create_backs_up_existing_db(tmp_path):
    db_path = str(tmp_path / "lib.mvdb")
    master = str(tmp_path / "comics")
    os.makedirs(master)

    db1 = LibraryDB.create(db_path, master)
    db1.close()
    db2 = LibraryDB.create(db_path, master)
    db2.close()

    assert os.path.exists(db_path + ".old")


def test_open_existing_db_reads_master_dir(tmp_path):
    db_path = str(tmp_path / "lib.mvdb")
    master = str(tmp_path / "comics")
    os.makedirs(master)

    db1 = LibraryDB.create(db_path, master)
    db1.close()

    db2 = LibraryDB.open(db_path)
    try:
        assert db2.get_master_dir() == os.path.normpath(master)
    finally:
        db2.close()


def test_name_property_strips_extension_and_dir():
    db = LibraryDB("/some/dir/marvel.mvdb")
    assert db.name == "marvel"


def test_rename_changes_db_path_and_file(tmp_path):
    db_path = str(tmp_path / "old_name.mvdb")
    master = str(tmp_path / "comics")
    os.makedirs(master)
    db = LibraryDB.create(db_path, master)
    try:
        new_path = db.rename("new_name")
        assert new_path.endswith("new_name.mvdb")
        assert os.path.exists(new_path)
        assert not os.path.exists(db_path)
        assert db.db_path == new_path
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Répertoires
# ---------------------------------------------------------------------------

def test_add_directory_and_get_additional_dirs(tmp_path):
    db_path = str(tmp_path / "lib.mvdb")
    master = str(tmp_path / "comics")
    extra = str(tmp_path / "extra")
    os.makedirs(master)
    os.makedirs(extra)

    db = LibraryDB.create(db_path, master)
    try:
        db.add_directory(extra)
        assert db.get_additional_dirs() == [os.path.normpath(extra)]
        all_dirs = db.get_all_dirs()
        assert os.path.normpath(master) in all_dirs
        assert os.path.normpath(extra) in all_dirs
    finally:
        db.close()


def test_set_master_dir_updates_path(tmp_path):
    db_path = str(tmp_path / "lib.mvdb")
    master = str(tmp_path / "comics")
    new_master = str(tmp_path / "comics2")
    os.makedirs(master)
    os.makedirs(new_master)

    db = LibraryDB.create(db_path, master)
    try:
        db.set_master_dir(new_master)
        assert db.get_master_dir() == os.path.normpath(new_master)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# scan — nouveaux / modifiés / supprimés
# ---------------------------------------------------------------------------

def test_scan_detects_new_files(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "comic1.cbz"))
    make_cbz(os.path.join(master, "comic2.cbz"))

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        stats = db.scan()
        assert stats["new"] == 2
        assert stats["updated"] == 0
        assert stats["deleted"] == 0
        assert db.count() == 2
    finally:
        db.close()


def test_scan_ignores_unsupported_extensions(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "comic1.cbz"))
    with open(os.path.join(master, "readme.md"), "w") as f:
        f.write("not a comic")

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        stats = db.scan()
        assert stats["new"] == 1
    finally:
        db.close()


def test_scan_detects_deleted_files(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    comic_path = os.path.join(master, "comic1.cbz")
    make_cbz(comic_path)

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        assert db.count() == 1

        os.remove(comic_path)
        stats = db.scan()
        assert stats["deleted"] == 1
        assert db.count() == 0
    finally:
        db.close()


def test_scan_detects_updated_files(tmp_path):
    import time

    master = str(tmp_path / "comics")
    os.makedirs(master)
    comic_path = os.path.join(master, "comic1.cbz")
    make_cbz(comic_path, n_images=1)

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        row = db.search([])[0]
        assert row["page_count"] == 1

        # Modifie le fichier : mtime doit avancer pour être détecté comme update
        time.sleep(1.1)
        make_cbz(comic_path, n_images=3)

        stats = db.scan()
        assert stats["updated"] == 1
        row = db.search([])[0]
        assert row["page_count"] == 3
    finally:
        db.close()


def test_scan_extracts_comicinfo_metadata(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "comic1.cbz"), with_comicinfo=True, series="Amazing Spider-Man", number="1")

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        row = db.search([])[0]
        assert row["series"] == "Amazing Spider-Man"
        assert row["number"] == "1"
        assert row["has_comicinfo"] == 1
    finally:
        db.close()


def test_scan_preserves_is_read_across_rescans(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "comic1.cbz"))

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        row = db.search([])[0]
        db.set_read([row["id"]], True)

        db.scan()  # rescan sans changement disque -> pas d'update, is_read préservé
        row = db.search([])[0]
        assert row["is_read"] == 1
    finally:
        db.close()


# ---------------------------------------------------------------------------
# set_read
# ---------------------------------------------------------------------------

def test_set_read_marks_multiple_ids(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "comic1.cbz"))
    make_cbz(os.path.join(master, "comic2.cbz"))

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        ids = [r["id"] for r in db.search([])]
        db.set_read(ids, True)
        for row in db.search([]):
            assert row["is_read"] == 1

        db.set_read(ids, False)
        for row in db.search([]):
            assert row["is_read"] == 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# search — whitelist, opérateurs, champs virtuels
# ---------------------------------------------------------------------------

def test_search_no_criteria_returns_all(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "a.cbz"))
    make_cbz(os.path.join(master, "b.cbz"))

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        assert len(db.search([])) == 2
    finally:
        db.close()


def test_search_contains_operator(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "a.cbz"), with_comicinfo=True, series="Amazing Spider-Man")
    make_cbz(os.path.join(master, "b.cbz"), with_comicinfo=True, series="Batman")

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        results = db.search([{"field": "series", "op": "contains", "value": "Spider"}])
        assert len(results) == 1
        assert results[0]["series"] == "Amazing Spider-Man"
    finally:
        db.close()


def test_search_unknown_field_ignored_returns_all(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "a.cbz"))

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        # Champ hors whitelist ignoré silencieusement (anti-injection)
        results = db.search([{"field": "sql_injection_attempt; DROP TABLE comics;--", "op": "contains", "value": "x"}])
        assert len(results) == 1
    finally:
        db.close()


def test_search_unknown_op_ignored_returns_all(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "a.cbz"))

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        results = db.search([{"field": "series", "op": "bogus_op", "value": "x"}])
        assert len(results) == 1
    finally:
        db.close()


def test_search_int_cast_field_orders_numerically_not_lexically(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "a.cbz"), with_comicinfo=True, series="S", number="9")
    make_cbz(os.path.join(master, "b.cbz"), with_comicinfo=True, series="S", number="10")

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        results = db.search([{"field": "number", "op": "gt", "value": "5"}], order_by="number")
        numbers = [r["number"] for r in results]
        assert numbers == ["9", "10"]  # tri numérique via CAST, pas lexicographique ("10" < "9" en texte)
    finally:
        db.close()


def test_search_cursor_int_cast_field_orders_numerically_not_lexically(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "a.cbz"), with_comicinfo=True, series="S", number="9")
    make_cbz(os.path.join(master, "b.cbz"), with_comicinfo=True, series="S", number="10")

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        total, cursor = db.search_cursor([{"field": "number", "op": "gt", "value": "5"}], order_by="number")
        rows = cursor.fetchall()
        numbers = [r["number"] for r in rows]
        assert total == 2
        assert numbers == ["9", "10"]
    finally:
        db.close()


def test_search_media_type_virtual_field(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "comic.cbz"))
    with open(os.path.join(master, "book.epub"), "wb") as f:
        f.write(b"PK\x03\x04fake epub")

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        comics = db.search([{"field": "media_type", "op": "mt_comics", "value": ""}])
        assert len(comics) == 1
        assert comics[0]["file_extension"] == ".cbz"
    finally:
        db.close()


def test_search_yesno_text_field_true_false(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    path = os.path.join(master, "a.cbz")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("page00.jpg", b"data")
        zf.writestr("ComicInfo.xml", '<?xml version="1.0"?>\r\n<ComicInfo>\r\n  <BlackAndWhite>Yes</BlackAndWhite>\r\n</ComicInfo>')

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        results = db.search([{"field": "black_and_white", "op": "true", "value": ""}])
        assert len(results) == 1
    finally:
        db.close()


def test_search_empty_and_not_empty_operators(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "a.cbz"), with_comicinfo=True, series="Has Series")
    make_cbz(os.path.join(master, "b.cbz"), with_comicinfo=False)

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        with_series = db.search([{"field": "series", "op": "not_empty", "value": ""}])
        without_series = db.search([{"field": "series", "op": "empty", "value": ""}])
        assert len(with_series) == 1
        assert len(without_series) == 1
    finally:
        db.close()


def test_search_order_by_invalid_field_falls_back_to_series(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "a.cbz"))

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        # order_by hors whitelist -> ne doit pas lever d'exception, retombe sur 'series'
        results = db.search([], order_by="'; DROP TABLE comics;--")
        assert len(results) == 1
    finally:
        db.close()


# ---------------------------------------------------------------------------
# get_absolute_path / get_by_filepath
# ---------------------------------------------------------------------------

def test_get_absolute_path_reconstructs_from_relative(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "comic1.cbz"))

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        row = db.search([])[0]
        abs_path = db.get_absolute_path(row["id"])
        assert os.path.normpath(abs_path) == os.path.normpath(os.path.join(master, "comic1.cbz"))
    finally:
        db.close()


def test_get_absolute_path_unknown_id_returns_none(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        assert db.get_absolute_path(999) is None
    finally:
        db.close()


def test_get_by_filepath_finds_matching_row(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    comic_path = os.path.join(master, "comic1.cbz")
    make_cbz(comic_path)

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        row = db.get_by_filepath(comic_path)
        assert row is not None
        assert row["filename"] == "comic1.cbz"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Colonnes / count
# ---------------------------------------------------------------------------

def test_columns_config_roundtrip(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        assert db.get_columns_config() is None
        db.set_columns_config(["series", "number", "writer"])
        assert db.get_columns_config() == ["series", "number", "writer"]
    finally:
        db.close()


def test_count_reflects_scanned_files(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "a.cbz"))
    make_cbz(os.path.join(master, "b.cbz"))
    make_cbz(os.path.join(master, "c.cbz"))

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        assert db.count() == 0
        db.scan()
        assert db.count() == 3
    finally:
        db.close()
