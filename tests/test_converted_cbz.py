"""Conversion en CBZ qui supprime sa source (modules/qt/converted_cbz.py) :
le CBZ est écrit dans un temporaire, relu, puis prend son nom ; la source
n'est supprimée qu'ensuite. Sur de vrais fichiers ; la Corbeille est
remplacée par un dossier du test (jamais la vraie Corbeille).
"""
import os
import stat
import zipfile

import pytest

import modules.qt.utils as utils
from modules.qt import converted_cbz
from modules.qt.batch_claims import BatchClaims, free_name_candidates
from modules.qt.converted_cbz import delete_source, reread_then_delete_source, write_cbz
from modules.qt.utils import ReplacementPendingError, WrittenFileCheckError


class _Stop(BaseException):
    """Arrêt levé par before_write, comme l'annulation d'un lot."""


@pytest.fixture
def trash(tmp_path, monkeypatch):
    """Corbeille du test : liste des chemins reçus, fichiers déplacés dans un
    dossier à part."""
    folder = tmp_path / "corbeille"
    folder.mkdir()
    received = []

    def fake_send_to_trash(path):
        received.append(path)
        os.replace(path, folder / os.path.basename(path))

    monkeypatch.setattr(utils, "send_to_trash", fake_send_to_trash)
    return received


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "Aaaa #001.cbr"
    path.write_bytes(b"source")
    return str(path)


def _temporaries(folder):
    return [n for n in os.listdir(folder) if n.startswith(utils.TEMP_FILE_PREFIX)]


def _names(path):
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def _write_two_pages(cbz):
    cbz.writestr("001.jpg", b"page 1")
    cbz.writestr("002.jpg", b"page 2")
    cbz.expected_count = 2


# ---------------------------------------------------------------------------
# write_cbz + delete_source
# ---------------------------------------------------------------------------

def test_converted_cbz_gets_its_name_then_the_source_goes_to_the_trash(tmp_path, source, trash):
    cbz_path = str(tmp_path / "Aaaa #001.cbz")
    with write_cbz(cbz_path) as cbz:
        _write_two_pages(cbz)
    delete_source(cbz, source)
    with zipfile.ZipFile(cbz_path) as zf:
        assert [zf.read(i) for i in zf.infolist()] == [b"page 1", b"page 2"]
    assert trash == [source] and not os.path.exists(source)
    assert _temporaries(tmp_path) == []


def test_permanent_deletion_removes_the_source_without_the_trash(tmp_path, source, trash):
    cbz_path = str(tmp_path / "Aaaa #001.cbz")
    with write_cbz(cbz_path) as cbz:
        _write_two_pages(cbz)
    delete_source(cbz, source, permanent=True)
    assert not os.path.exists(source) and trash == []
    assert not os.listdir(tmp_path / "corbeille")


def test_the_temporary_is_reread_before_the_cbz_gets_its_name(tmp_path, monkeypatch):
    cbz_path = str(tmp_path / "Aaaa.cbz")
    seen = []
    real = utils.verify_written_cbz

    def spy(path, expected_count=None, shown_path=None):
        seen.append((path, expected_count, shown_path, os.path.exists(cbz_path)))
        return real(path, expected_count, shown_path)

    monkeypatch.setattr(utils, "verify_written_cbz", spy)
    with write_cbz(cbz_path) as cbz:
        _write_two_pages(cbz)
    [(reread, count, shown, named_already)] = seen
    assert reread != cbz_path and os.path.dirname(reread) == str(tmp_path)
    assert count == 2 and shown == cbz_path and not named_already


def test_cbz_that_does_not_reread_is_never_created_and_the_source_kept(tmp_path, source, trash):
    cbz_path = str(tmp_path / "Aaaa #001.cbz")
    with pytest.raises(WrittenFileCheckError) as info:
        with write_cbz(cbz_path) as cbz:
            cbz.writestr("001.jpg", b"page 1")
            cbz.expected_count = 3          # la source annonçait trois pages
    assert info.value.path == cbz_path       # message nommant le CBZ, pas le temporaire
    assert not os.path.exists(cbz_path) and _temporaries(tmp_path) == []
    with pytest.raises(RuntimeError):
        delete_source(cbz, source)
    assert os.path.exists(source) and trash == []


def test_file_already_under_the_name_is_untouched_when_the_reread_fails(tmp_path, monkeypatch):
    cbz_path = tmp_path / "Aaaa.cbz"
    cbz_path.write_bytes(b"fichier d'un autre programme")

    def fail(path, expected_count=None, shown_path=None):
        raise WrittenFileCheckError(shown_path or path, detail="001.jpg")

    monkeypatch.setattr(utils, "verify_written_cbz", fail)
    with pytest.raises(WrittenFileCheckError):
        with write_cbz(str(cbz_path)) as cbz:
            _write_two_pages(cbz)
    assert cbz_path.read_bytes() == b"fichier d'un autre programme"
    assert _temporaries(tmp_path) == []


def test_without_expected_count_the_written_files_are_checked(tmp_path, monkeypatch):
    seen = []
    real = utils.verify_written_cbz
    monkeypatch.setattr(utils, "verify_written_cbz",
                        lambda p, n=None, shown_path=None: (seen.append(n), real(p, n, shown_path)))
    with write_cbz(str(tmp_path / "Aaaa.cbz")) as cbz:
        cbz.writestr("Chapitre/", b"")          # dossier : non compté
        cbz.writestr("Chapitre/001.jpg", b"page 1")
        cbz.writestr(zipfile.ZipInfo("ComicInfo.xml"), b"<ComicInfo/>")
    assert cbz.written_files == 2 and seen == [2]


def test_archive_comment_is_written(tmp_path):
    cbz_path = str(tmp_path / "Aaaa.cbz")
    with write_cbz(cbz_path) as cbz:
        _write_two_pages(cbz)
        cbz.comment = b"ComicBookInfo"
    with zipfile.ZipFile(cbz_path) as zf:
        assert zf.comment == b"ComicBookInfo"


def test_stop_during_writing_leaves_nothing_and_keeps_the_source(tmp_path, source, trash):
    cbz_path = str(tmp_path / "Aaaa #001.cbz")
    calls = []

    def before_write():
        calls.append(1)
        if len(calls) == 2:
            raise _Stop()

    with pytest.raises(_Stop):
        with write_cbz(cbz_path, before_write=before_write) as cbz:
            _write_two_pages(cbz)
    assert cbz.written_files == 1            # arrêté avant la deuxième page
    assert not os.path.exists(cbz_path) and _temporaries(tmp_path) == []
    with pytest.raises(RuntimeError):
        delete_source(cbz, source)
    assert os.path.exists(source) and trash == []


def test_error_in_the_block_leaves_no_file_and_no_temporary(tmp_path):
    cbz_path = str(tmp_path / "Aaaa.cbz")
    with pytest.raises(ValueError):
        with write_cbz(cbz_path) as cbz:
            cbz.writestr("001.jpg", b"page 1")
            raise ValueError("pages en échec")
    assert not os.path.exists(cbz_path) and _temporaries(tmp_path) == []


def test_cbz_named_after_a_reserved_name_leaves_the_existing_file(tmp_path, source, trash):
    taken = tmp_path / "Aaaa #001.cbz"
    taken.write_bytes(b"CBZ existant")
    claims = BatchClaims.start([source])
    try:
        cbz_path = claims.reserve(free_name_candidates(os.path.splitext(source)[0], ".cbz"))
        with write_cbz(cbz_path) as cbz:
            _write_two_pages(cbz)
        delete_source(cbz, source)
    finally:
        claims.finish()
    assert os.path.basename(cbz_path) == "Aaaa #001 (1).cbz"
    assert taken.read_bytes() == b"CBZ existant"
    assert _names(cbz_path) == ["001.jpg", "002.jpg"]


def test_source_that_cannot_go_to_the_trash_raises_and_both_files_stay(tmp_path, source, monkeypatch):
    def refuse(path):
        raise OSError(5, "refus", path)

    monkeypatch.setattr(utils, "send_to_trash", refuse)
    cbz_path = str(tmp_path / "Aaaa #001.cbz")
    with write_cbz(cbz_path) as cbz:
        _write_two_pages(cbz)
    with pytest.raises(OSError):
        delete_source(cbz, source)
    assert os.path.exists(source) and os.path.exists(cbz_path)


@pytest.mark.skipif(os.name != "nt", reason="lecture seule qui empêche la suppression : Windows")
def test_read_only_source_is_not_deleted_and_the_cbz_stays(tmp_path, source):
    os.chmod(source, stat.S_IREAD)
    try:
        cbz_path = str(tmp_path / "Aaaa #001.cbz")
        with write_cbz(cbz_path) as cbz:
            _write_two_pages(cbz)
        with pytest.raises(PermissionError):
            delete_source(cbz, source, permanent=True)
        assert os.path.exists(source) and os.path.exists(cbz_path)
    finally:
        os.chmod(source, stat.S_IREAD | stat.S_IWRITE)


def test_temporary_that_became_the_only_copy_is_kept(tmp_path, monkeypatch):
    cbz_path = str(tmp_path / "Aaaa.cbz")

    def replacement_pending(src, dest):
        raise ReplacementPendingError(dest, src)

    monkeypatch.setattr(utils, "replace_file_retrying", replacement_pending)
    with pytest.raises(ReplacementPendingError) as info:
        with write_cbz(cbz_path) as cbz:
            _write_two_pages(cbz)
    kept = info.value.src
    assert os.path.exists(kept) and utils.is_kept_temp(kept)
    assert _names(kept) == ["001.jpg", "002.jpg"]


def test_rewrite_in_place_checks_the_structure_without_rereading(tmp_path, source, monkeypatch):
    calls = []
    real_check = utils.check_written_cbz_structure
    monkeypatch.setattr(utils, "check_written_cbz_structure",
                        lambda p, n, shown_path=None: (calls.append(("structure", n)),
                                                       real_check(p, n, shown_path)))
    monkeypatch.setattr(utils, "verify_written_cbz",
                        lambda *a, **k: calls.append(("relecture",)))
    cbz_path = tmp_path / "Aaaa.cbz"
    cbz_path.write_bytes(b"ancien contenu")
    with write_cbz(str(cbz_path), reread=False, check_structure=True) as cbz:
        _write_two_pages(cbz)
    assert calls == [("structure", 2)]
    assert _names(cbz_path) == ["001.jpg", "002.jpg"]
    with pytest.raises(RuntimeError):        # aucune relecture : rien à supprimer
        delete_source(cbz, source)
    assert os.path.exists(source)


def test_rewrite_that_fails_its_structure_check_keeps_the_original(tmp_path, monkeypatch):
    cbz_path = tmp_path / "Aaaa.cbz"
    cbz_path.write_bytes(b"ancien contenu")

    def fail(path, expected_count, shown_path=None):
        raise WrittenFileCheckError(shown_path or path, detail="001.jpg")

    monkeypatch.setattr(utils, "check_written_cbz_structure", fail)
    with pytest.raises(WrittenFileCheckError):
        with write_cbz(str(cbz_path), reread=False, check_structure=True) as cbz:
            _write_two_pages(cbz)
    assert cbz_path.read_bytes() == b"ancien contenu" and _temporaries(tmp_path) == []


def test_delete_source_refuses_anything_but_a_reread_writer(source, trash):
    with pytest.raises(RuntimeError):
        delete_source(object(), source)
    assert os.path.exists(source) and trash == []


# ---------------------------------------------------------------------------
# Source réécrite par un autre programme pendant sa conversion
# ---------------------------------------------------------------------------

def _rewrite_later(path):
    """Réécriture par un autre programme : date de modification changée
    (os.utime : sans écrire, Windows refuserait d'écrire un fichier ouvert)."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


def test_source_unchanged_since_its_reading_goes_to_the_trash(tmp_path, source, trash):
    signature = utils.file_signature(source)
    cbz_path = str(tmp_path / "Aaaa #001.cbz")
    with write_cbz(cbz_path) as cbz:
        _write_two_pages(cbz)
        converted_cbz.check_source_unchanged(source, signature)
    delete_source(cbz, source, source_signature=signature)
    assert trash == [source]


def test_source_rewritten_during_the_reading_never_gives_a_cbz(tmp_path, source, trash):
    """Pages lues pendant qu'un autre programme réécrit la source (RAR, 7z
    rouverts à chaque page) : le CBZ pourrait mêler deux versions — il ne
    prend jamais son nom, et la source reste."""
    signature = utils.file_signature(source)
    cbz_path = str(tmp_path / "Aaaa #001.cbz")
    with pytest.raises(converted_cbz.SourceChangedError):
        with write_cbz(cbz_path) as cbz:
            _write_two_pages(cbz)
            _rewrite_later(source)
            converted_cbz.check_source_unchanged(source, signature)
    assert not os.path.exists(cbz_path) and _temporaries(tmp_path) == []
    assert os.path.exists(source) and trash == []


@pytest.mark.parametrize("change", [_rewrite_later, os.remove])
def test_source_changed_after_the_reading_is_not_deleted(tmp_path, source, trash, change):
    """Réécrite (ou supprimée) pendant la relecture du CBZ : rien n'est
    supprimé, le CBZ complet reste."""
    signature = utils.file_signature(source)
    cbz_path = str(tmp_path / "Aaaa #001.cbz")
    with write_cbz(cbz_path) as cbz:
        _write_two_pages(cbz)
    change(source)
    with pytest.raises(converted_cbz.SourceChangedError):
        delete_source(cbz, source, source_signature=signature)
    assert trash == [] and os.path.exists(cbz_path)


def test_changed_source_is_an_expected_case_for_the_error_journal():
    from modules.qt.error_log import is_expected_error
    assert is_expected_error(converted_cbz.SourceChangedError("x.cbr"))


def _delete_source_calls_without_signature(tree, name):
    import ast
    return [f"{name}:{node.lineno}" for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", None)) == "delete_source"
            and not any(k.arg == "source_signature" for k in node.keywords)]


def test_scan_detects_a_delete_source_without_signature():
    import ast
    tree = ast.parse("delete_source(w, p)\n"
                     "delete_source(w, p, source_signature=s)\n"
                     "converted_cbz.delete_source(w, p, permanent=True)\n")
    assert _delete_source_calls_without_signature(tree, "x.py") == ["x.py:1", "x.py:3"]


def test_every_conversion_compares_its_source_before_deleting_it():
    """Toute conversion qui supprime sa source passe à delete_source l'état
    de la source relevé avant sa lecture : une source réécrite par un autre
    programme entre-temps n'est jamais supprimée."""
    import ast_loader
    qt_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "modules", "qt")
    found, offenders = 0, []
    for name in sorted(os.listdir(qt_dir)):
        if name.endswith(".py") and name != "converted_cbz.py":
            tree = ast_loader.module_tree(os.path.join(qt_dir, name))
            text = open(os.path.join(qt_dir, name), encoding="utf-8").read()
            found += text.count("delete_source(")
            offenders += _delete_source_calls_without_signature(tree, name)
    assert found >= 7, "le scan ne voit plus les conversions des lots"
    assert not offenders, "delete_source sans source_signature :\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# reread_then_delete_source (CBZ déjà nommé : panneau, Bibliothèque)
# ---------------------------------------------------------------------------

def _named_cbz(tmp_path, pages=2):
    path = str(tmp_path / "Aaaa #001.cbz")
    with zipfile.ZipFile(path, "w") as zf:
        for n in range(pages):
            zf.writestr(f"{n + 1:03d}.jpg", b"page")
    return path


def test_named_cbz_reread_then_the_source_goes_to_the_trash(tmp_path, source, trash):
    assert reread_then_delete_source(_named_cbz(tmp_path), 2, source) is True
    assert trash == [source] and not os.path.exists(source)


def test_named_cbz_with_permanent_deletion(tmp_path, source, trash):
    assert reread_then_delete_source(_named_cbz(tmp_path), 2, source, permanent=True) is True
    assert not os.path.exists(source) and trash == []


def test_named_cbz_that_does_not_reread_deletes_nothing(tmp_path, source, trash):
    cbz_path = _named_cbz(tmp_path, pages=1)
    with pytest.raises(WrittenFileCheckError):
        reread_then_delete_source(cbz_path, 2, source)
    assert os.path.exists(source) and os.path.exists(cbz_path) and trash == []


def test_damaged_named_cbz_deletes_nothing(tmp_path, source, trash):
    cbz_path = _named_cbz(tmp_path)
    with open(cbz_path, "rb") as f:
        data = bytearray(f.read())
    # En-tête local de 30 octets + nom « 001.jpg » (7) : données de la
    # première page (stockée) à partir de l'octet 37 ; CRC alors faux
    data[38] ^= 0xFF
    with open(cbz_path, "wb") as f:
        f.write(bytes(data))
    with pytest.raises(WrittenFileCheckError):
        reread_then_delete_source(cbz_path, 2, source)
    assert os.path.exists(source) and trash == []


def test_source_already_gone_is_left_alone(tmp_path, trash, monkeypatch):
    reread = []
    monkeypatch.setattr(utils, "verify_written_cbz", lambda *a, **k: reread.append(a))
    gone = str(tmp_path / "absent.cbr")
    assert reread_then_delete_source(_named_cbz(tmp_path), 2, gone) is False
    assert reread == [] and trash == []


def test_named_cbz_source_that_cannot_go_to_the_trash_raises(tmp_path, source, monkeypatch):
    def refuse(path):
        raise OSError(5, "refus", path)

    monkeypatch.setattr(utils, "send_to_trash", refuse)
    with pytest.raises(OSError):
        reread_then_delete_source(_named_cbz(tmp_path), 2, source)
    assert os.path.exists(source)


def test_named_cbz_unchanged_since_its_writing_lets_the_source_go(tmp_path, source, trash):
    cbz_path = _named_cbz(tmp_path)
    signature = utils.file_signature(cbz_path)
    assert reread_then_delete_source(cbz_path, 2, source, written_signature=signature) is True
    assert trash == [source]


def test_named_cbz_rewritten_since_its_writing_keeps_the_source(tmp_path, source, trash,
                                                                 monkeypatch):
    """Réponse « Supprimer l'original ? » tardive : le CBZ a été réécrit
    ailleurs entre-temps (autre programme, autre panneau, Bibliothèque), même
    avec le bon nombre de pages. Ni relu ni rien de supprimé."""
    cbz_path = _named_cbz(tmp_path)
    signature = utils.file_signature(cbz_path)
    with zipfile.ZipFile(cbz_path, "w") as zf:      # autre contenu, autre taille
        zf.writestr("001.jpg", b"autre page plus longue")
        zf.writestr("002.jpg", b"page")
    reread = []
    monkeypatch.setattr(utils, "verify_written_cbz", lambda *a, **k: reread.append(a))
    with pytest.raises(converted_cbz.ConvertedCbzChangedError) as info:
        reread_then_delete_source(cbz_path, 2, source, written_signature=signature)
    assert (info.value.path, info.value.source) == (cbz_path, source)
    assert reread == [] and trash == [] and os.path.exists(source)


def test_named_cbz_that_disappeared_since_its_writing_keeps_the_source(tmp_path, source, trash):
    cbz_path = _named_cbz(tmp_path)
    signature = utils.file_signature(cbz_path)
    os.remove(cbz_path)
    with pytest.raises(converted_cbz.ConvertedCbzChangedError):
        reread_then_delete_source(cbz_path, 2, source, written_signature=signature)
    assert os.path.exists(source) and trash == []


def test_rewritten_cbz_is_an_expected_case_for_the_error_journal():
    from modules.qt import error_log
    error = converted_cbz.ConvertedCbzChangedError("C:/a/Aaaa.cbz", "C:/a/Aaaa.cbr")
    assert error_log.is_expected_error(error)


def test_module_does_not_import_qt():
    """Testé sans Qt : les fonctions de utils.py sont importées à l'appel."""
    import ast
    tree = ast.parse(open(converted_cbz.__file__, encoding="utf-8").read())
    top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = [a.name for n in top_level for a in n.names] + \
            [n.module for n in top_level if isinstance(n, ast.ImportFrom)]
    assert not [n for n in names if n and ("PySide6" in n or n.startswith("modules"))]
