"""Réservations des traitements par lot (modules/qt/batch_claims.py).

Deux lots lancés sur le même dossier ne doivent jamais traiter le même
fichier ni choisir le même nom de CBZ à créer : sinon le lot en retard relit
une source que l'autre vient de supprimer, puis remplace et supprime le CBZ
complet que l'autre a produit.
"""
import threading

from modules.qt.batch_claims import (BatchClaims, files_in_running_batch,
                                     free_name_candidates, is_claimed)


def test_second_batch_on_the_same_files_is_refused(tmp_path):
    """Même dossier lancé dans les deux panneaux : le second lot ne démarre pas."""
    a, b = str(tmp_path / "a.cbr"), str(tmp_path / "b.cbr")
    first = BatchClaims.start([a, b])
    try:
        assert first is not None
        assert files_in_running_batch([b])
        assert BatchClaims.start([b, str(tmp_path / "c.cbr")]) is None
    finally:
        first.finish()


def test_batch_on_other_files_starts(tmp_path):
    first = BatchClaims.start([str(tmp_path / "a.cbr")])
    try:
        second = BatchClaims.start([str(tmp_path / "c.cbr")])
        assert second is not None
        second.finish()
    finally:
        first.finish()


def test_finished_batch_no_longer_blocks(tmp_path):
    a = str(tmp_path / "a.cbr")
    first = BatchClaims.start([a])
    first.claim(a)
    first.finish()
    assert not files_in_running_batch([a])
    assert not is_claimed(a)
    second = BatchClaims.start([a])
    assert second is not None
    second.finish()


def test_two_batches_started_at_the_same_instant(tmp_path):
    """Deux lancements simultanés (barrière) sur les mêmes fichiers : un seul
    démarre."""
    files = [str(tmp_path / "a.cbr"), str(tmp_path / "b.cbr")]
    barrier = threading.Barrier(2)
    started = []

    def run():
        barrier.wait()
        started.append(BatchClaims.start(files))

    threads = [threading.Thread(target=run) for _i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert sum(c is not None for c in started) == 1
    finally:
        for c in started:
            if c is not None:
                c.finish()


def test_a_file_claimed_by_a_batch_is_refused_to_another(tmp_path):
    src = str(tmp_path / "comic.cbr")
    first, second = BatchClaims(), BatchClaims()
    try:
        assert first.claim(src)
        assert not second.claim(src)
        assert first.claim(src)          # le même lot peut le redemander
    finally:
        first.release_all()
        second.release_all()


def test_released_file_can_be_claimed_again(tmp_path):
    src = str(tmp_path / "comic.cbr")
    first, second = BatchClaims(), BatchClaims()
    first.claim(src)
    first.release_all()
    assert not is_claimed(src)
    assert second.claim(src)
    second.release_all()


def test_path_spelling_does_not_matter(tmp_path):
    (tmp_path / "Dir").mkdir()
    first, second = BatchClaims(), BatchClaims()
    try:
        assert first.claim(str(tmp_path / "Dir" / "a.cbr"))
        assert not second.claim(str(tmp_path / "Dir" / "." / "A.CBR"))
    finally:
        first.release_all()


def test_existing_file_is_skipped(tmp_path):
    (tmp_path / "comic.cbz").write_bytes(b"PK")
    claims = BatchClaims()
    try:
        path = claims.reserve(free_name_candidates(str(tmp_path / "comic"), ".cbz"))
        assert path == str(tmp_path / "comic (1).cbz")
    finally:
        claims.release_all()


def test_name_reserved_by_another_batch_is_not_given_twice(tmp_path):
    """Le CBZ du premier lot n'existe pas encore sur le disque (écrit dans un
    temporaire) : le second lot doit quand même choisir un autre nom."""
    base = str(tmp_path / "comic")
    first, second = BatchClaims(), BatchClaims()
    try:
        assert first.reserve(free_name_candidates(base, ".cbz")) == base + ".cbz"
        assert second.reserve(free_name_candidates(base, ".cbz")) == base + " (1).cbz"
    finally:
        first.release_all()
        second.release_all()


def test_numbered_pattern(tmp_path):
    (tmp_path / "img.cbz").write_bytes(b"PK")
    claims = BatchClaims()
    try:
        path = claims.reserve(free_name_candidates(str(tmp_path / "img"), ".cbz",
                                                   "{base}_{n:02d}{ext}"))
        assert path == str(tmp_path / "img_01.cbz")
    finally:
        claims.release_all()


def test_release_frees_only_the_batch_own_claims(tmp_path):
    a, b = str(tmp_path / "a.cbr"), str(tmp_path / "b.cbr")
    first, second = BatchClaims(), BatchClaims()
    first.claim(a)
    second.claim(b)
    first.release_all()
    try:
        assert not is_claimed(a)
        assert is_claimed(b)
    finally:
        second.release_all()


def test_two_threads_never_get_the_same_file_or_name(tmp_path):
    """Deux lots démarrés au même instant (barrière) sur le même fichier :
    un seul l'obtient, et les noms de CBZ réservés sont distincts."""
    src = str(tmp_path / "comic.cbr")
    base = str(tmp_path / "comic")
    barrier = threading.Barrier(2)
    results = []
    batches = [BatchClaims(), BatchClaims()]

    def run(claims):
        barrier.wait()
        got_file = claims.claim(src)
        name = claims.reserve(free_name_candidates(base, ".cbz"))
        results.append((got_file, name))

    threads = [threading.Thread(target=run, args=(c,)) for c in batches]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(got for got, _name in results) == [False, True]
        assert len({name for _got, name in results}) == 2
    finally:
        for c in batches:
            c.release_all()
