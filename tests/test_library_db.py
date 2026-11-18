import os
import sqlite3
import zipfile

import pytest

from modules.qt import library_db
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
    db = LibraryDB("/some/dir/aaaa.mvdb")
    assert db.name == "aaaa"


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
    master = str(tmp_path / "comics")
    os.makedirs(master)
    comic_path = os.path.join(master, "comic1.cbz")
    make_cbz(comic_path, n_images=1)

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        row = db.search([])[0]
        assert row["page_count"] == 1

        # Modifie le fichier : sa date de modification (à la seconde) doit
        # avancer pour être détectée ; posée à la main plutôt qu'attendue
        first = os.stat(comic_path).st_mtime
        make_cbz(comic_path, n_images=3)
        os.utime(comic_path, (first + 5, first + 5))

        stats = db.scan()
        assert stats["updated"] == 1
        row = db.search([])[0]
        assert row["page_count"] == 3
    finally:
        db.close()


def test_scan_extracts_comicinfo_metadata(tmp_path):
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "comic1.cbz"), with_comicinfo=True, series="Aaaa Bbbb", number="1")

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        row = db.search([])[0]
        assert row["series"] == "Aaaa Bbbb"
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
    make_cbz(os.path.join(master, "a.cbz"), with_comicinfo=True, series="Aaaa Bbbb")
    make_cbz(os.path.join(master, "b.cbz"), with_comicinfo=True, series="Cccc")

    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        results = db.search([{"field": "series", "op": "contains", "value": "Bbbb"}])
        assert len(results) == 1
        assert results[0]["series"] == "Aaaa Bbbb"
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


def test_get_absolute_path_of_additional_directory_file(tmp_path):
    """Un fichier d'un répertoire additionnel a un chemin relatif au maître en
    ../… : il reste accessible."""
    master = str(tmp_path / "comics")
    extra = str(tmp_path / "other")
    os.makedirs(master)
    os.makedirs(extra)
    make_cbz(os.path.join(extra, "comic2.cbz"))
    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.add_directory(extra)
        db.scan()
        row = db.search([])[0]
        assert row["relative_path"].startswith("../")
        abs_path = db.get_absolute_path(row["id"])
        assert os.path.normpath(abs_path) == os.path.normpath(os.path.join(extra, "comic2.cbz"))
    finally:
        db.close()


def test_get_absolute_path_outside_library_dirs_returns_none(tmp_path):
    """Une ligne désignant un fichier hors de tous les répertoires de la base
    (base abîmée ou reçue d'un tiers) ne donne aucun chemin : sinon la
    conversion (qui supprime la source) ou l'ouverture s'appliqueraient à un
    fichier quelconque du disque."""
    master = str(tmp_path / "comics")
    os.makedirs(master)
    make_cbz(os.path.join(master, "comic1.cbz"))
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF")
    db = LibraryDB.create(str(tmp_path / "lib.mvdb"), master)
    try:
        db.scan()
        row = db.search([])[0]
        for rel in ("../secret.pdf", str(outside)):
            db._conn.execute("UPDATE comics SET relative_path=? WHERE id=?", (rel, row["id"]))
            assert db.get_absolute_path(row["id"]) is None
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


def test_window_can_write_while_a_scan_is_running(tmp_path, monkeypatch):
    """Le scan (thread, connexion à part) valide ses écritures au fil de l'eau :
    pendant ce temps, une écriture de la fenêtre (marquer comme lu,
    réindexation après un enregistrement) ne doit pas attendre la fin du scan.
    Délai de validation mis à 0 pour rendre le test déterministe ; l'écriture
    concurrente se fait dans le rappel de progression, donc pendant le scan."""
    monkeypatch.setattr(library_db, "_SCAN_COMMIT_INTERVAL", 0)
    master = str(tmp_path / "comics")
    os.makedirs(master)
    for name in ("a.cbz", "b.cbz", "c.cbz"):
        make_cbz(os.path.join(master, name))
    db_path = str(tmp_path / "lib.mvdb")

    db = LibraryDB.create(db_path, master)
    writes = []

    def on_progress(event):
        # Deuxième fichier : le premier vient d'être indexé par le scan.
        if len(writes) == 0 and event[0] == "new" and db.count() >= 1:
            other = sqlite3.connect(db_path, timeout=0)
            try:
                other.execute("UPDATE comics SET is_read = 1")
                other.commit()
                writes.append(True)
            finally:
                other.close()

    try:
        db.scan(progress_callback=on_progress)
        assert writes == [True]
        assert db.count() == 3
    finally:
        db.close()


def test_page_count_uses_the_same_image_list_as_the_mosaic(tmp_path):
    """Pages .jfif (autre nom du JPEG) et .ico comptées comme dans la
    mosaïque (archive_names.IMAGE_EXTS), les fichiers texte non."""
    cbz = tmp_path / "comic.cbz"
    with zipfile.ZipFile(cbz, "w") as zf:
        for name in ("01.jpg", "02.jfif", "03.pjpeg", "04.ico", "info.nfo", "ComicInfo.xml"):
            zf.writestr(name, b"x")
    assert library_db._count_pages(str(cbz)) == 4


def test_comicinfo_beyond_the_size_limit_is_not_read(tmp_path, monkeypatch):
    """Un ComicInfo.xml plus grand que MAX_MEMBER_SIZE n'est pas lu (archive
    piégée) : le comic est catalogué sans métadonnées, mais reste repéré
    comme ayant un ComicInfo.xml."""
    cbz = make_cbz(tmp_path / "comic.cbz", with_comicinfo=True, series="S")
    assert library_db._read_comicinfo_from_archive(str(cbz)) is not None
    monkeypatch.setattr(library_db, "MAX_MEMBER_SIZE", 4)
    assert library_db._read_comicinfo_from_archive(str(cbz)) is None
    assert library_db._has_comicinfo_in_archive(str(cbz)) is True


# ---------------------------------------------------------------------------
# Archives mal nommées (extension qui ne correspond pas au format réel)
# ---------------------------------------------------------------------------

def test_misnamed_archive_is_read_in_its_real_format(tmp_path):
    """ZIP nommé .cbr : lu comme un ZIP, sinon il serait catalogué sans
    titre, sans série et sans nombre de pages."""
    cbr = make_cbz(tmp_path / "comic.cbr", with_comicinfo=True, series="Aaaa", n_images=3)
    assert library_db._count_pages(str(cbr)) == 3
    assert library_db._has_comicinfo_in_archive(str(cbr)) is True


def scanned_library(tmp_path, names):
    master = tmp_path / "comics"
    master.mkdir()
    for name in names:
        make_cbz(master / name, with_comicinfo=True, series="Aaaa")
    return master, LibraryDB.create(str(tmp_path / "lib.mvdb"), str(master))


def test_scan_reports_misnamed_archives_it_indexes(tmp_path):
    master, db = scanned_library(tmp_path, ["a.cbr", "b.cbz", "c.zip", "d.cb7"])
    try:
        stats = db.scan()
        assert sorted((os.path.basename(p), k) for p, k in stats["misnamed"]) == [
            ("a.cbr", "CBZ"), ("d.cb7", "CBZ")]
        row = db.get_by_filepath(str(master / "a.cbr"))
        assert row["series"] == "Aaaa" and row["page_count"] == 2
        # Rescan sans modification : fichiers déjà indexés, pas proposés à nouveau
        assert db.scan()["misnamed"] == []
    finally:
        db.close()


def test_renamed_file_keeps_its_line_and_read_mark(tmp_path):
    master, db = scanned_library(tmp_path, ["a.cbr"])
    try:
        db.scan()
        old = db.get_by_filepath(str(master / "a.cbr"))
        db.set_read([old["id"]], True)
        os.rename(master / "a.cbr", master / "a.cbz")
        assert db.rename_file(str(master / "a.cbr"), str(master / "a.cbz"))
        row = db.get_by_filepath(str(master / "a.cbz"))
        assert (row["id"], row["is_read"], row["series"]) == (old["id"], 1, "Aaaa")
        assert (row["filename"], row["file_extension"]) == ("a.cbz", ".cbz")
        assert db.get_by_filepath(str(master / "a.cbr")) is None
        # Rescan : la ligne n'est ni supprimée ni recréée
        stats = db.scan()
        assert (stats["new"], stats["deleted"]) == (0, 0) and db.count() == 1
    finally:
        db.close()


def test_rename_replaces_a_stale_line_left_under_the_new_name(tmp_path):
    """Une ligne restée sous le nouveau nom (fichier disparu, jamais rescanné)
    est retirée : le nom était libre sur le disque."""
    master, db = scanned_library(tmp_path, ["a.cbr", "a.cbz"])
    try:
        db.scan()
        os.remove(master / "a.cbz")
        os.rename(master / "a.cbr", master / "a.cbz")
        assert db.rename_file(str(master / "a.cbr"), str(master / "a.cbz"))
        assert db.count() == 1
    finally:
        db.close()


def test_check_finds_misnamed_files_already_in_the_library(tmp_path, monkeypatch):
    """« Vérifier les extensions » : un fichier indexé avant la détection
    (ici : comme l'ancienne Bibliothèque, qui le lisait d'après son
    extension) est trouvé, puis relu dans son format réel."""
    master, db = scanned_library(tmp_path, ["a.cbr", "b.cbz", "c.zip"])
    try:
        real_detection = library_db._real_archive_kind
        monkeypatch.setattr(library_db, "_real_archive_kind", lambda path: None)
        db.scan()
        assert db.get_by_filepath(str(master / "a.cbr"))["series"] is None
        monkeypatch.setattr(library_db, "_real_archive_kind", real_detection)
        progress = []
        found = db.find_misnamed(progress_callback=progress.append)
        assert [(os.path.basename(p), k) for p, k in found] == [("a.cbr", "CBZ")]
        assert progress[-1] == (3, 3)
        row = db.get_by_filepath(str(master / "a.cbr"))
        assert (row["series"], row["page_count"]) == ("Aaaa", 2)
    finally:
        db.close()


def test_check_skips_files_missing_from_the_disk_and_stops_on_request(tmp_path):
    import threading
    master, db = scanned_library(tmp_path, ["a.cbr", "b.cbr"])
    try:
        db.scan()
        os.remove(master / "a.cbr")
        assert [os.path.basename(p) for p, _k in db.find_misnamed()] == ["b.cbr"]
        stop = threading.Event()
        stop.set()
        assert db.find_misnamed(stop_event=stop) == []
    finally:
        db.close()


def _library_with_unread_misnamed(tmp_path, monkeypatch, names):
    """Base dont les fichiers mal nommés ont été indexés sans être lus
    (comme par l'ancienne Bibliothèque) : série vide."""
    master, db = scanned_library(tmp_path, names)
    real_detection = library_db._real_archive_kind
    monkeypatch.setattr(library_db, "_real_archive_kind", lambda path: None)
    db.scan()
    monkeypatch.setattr(library_db, "_real_archive_kind", real_detection)
    return master, db


def test_check_cancelled_rereads_the_files_already_found(tmp_path, monkeypatch):
    """Bouton Annuler : la recherche s'arrête, les fichiers déjà trouvés sont
    relus (ils seront proposés au renommage)."""
    import threading
    master, db = _library_with_unread_misnamed(tmp_path, monkeypatch, ["a.cbr", "b.cbr"])
    try:
        cancel = threading.Event()
        found = db.find_misnamed(progress_callback=lambda _p: cancel.set(), cancel_event=cancel)
        assert len(found) == 1
        assert db.get_by_filepath(found[0][0])["series"] == "Aaaa"
    finally:
        db.close()


def test_check_stopped_does_not_reread_anything(tmp_path, monkeypatch):
    """Base ou application fermée : arrêt rapide, aucune relecture (le
    prochain rescan ou la prochaine vérification s'en chargera)."""
    import threading
    master, db = _library_with_unread_misnamed(tmp_path, monkeypatch, ["a.cbr", "b.cbr"])
    try:
        stop = threading.Event()
        found = db.find_misnamed(progress_callback=lambda _p: stop.set(), stop_event=stop)
        assert len(found) == 1
        assert db.get_by_filepath(found[0][0])["series"] is None
    finally:
        db.close()


def test_reindex_stops_on_request(tmp_path, monkeypatch):
    import threading
    master, db = _library_with_unread_misnamed(tmp_path, monkeypatch, ["a.cbr"])
    try:
        stop = threading.Event()
        stop.set()
        db.reindex_files([str(master / "a.cbr")], stop_event=stop)
        assert db.get_by_filepath(str(master / "a.cbr"))["series"] is None
    finally:
        db.close()


def test_rename_of_a_file_unknown_to_the_library_changes_nothing(tmp_path):
    master, db = scanned_library(tmp_path, ["a.cbz"])
    try:
        db.scan()
        assert db.rename_file(str(master / "x.cbr"), str(master / "x.cbz")) is False
        assert db.count() == 1
    finally:
        db.close()
