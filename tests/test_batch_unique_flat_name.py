"""Nom d'entrée du CBZ aplati lors des conversions en lot CBR/CB7/CBT -> CBZ.

batch_dialogs_qt.py importe PySide6 au niveau module : la fonction est extraite
par AST et exécutée seule, sans aucun import Qt.
"""
import os
import re

import pytest

import ast_loader


def _load():
    ns = ast_loader.load("batch_dialogs_qt.py", {"_unique_flat_name", "_flat_names"}, os=os)
    return ns["_unique_flat_name"], ns["_flat_names"]


_unique_flat_name, _flat_names = _load()


def _flatten(paths):
    written = set()
    return [_unique_flat_name(p, written) for p in paths]


def _natural_sort_key(text):
    name = os.path.splitext(text)[0]
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', name)]


def test_unique_names_unchanged():
    paths = ["page01.jpg", "Chap1/page02.jpg", "a b/c d/page 03.png", "Été/café.png"]
    assert _flatten(paths) == ["page01.jpg", "page02.jpg", "page 03.png", "café.png"]


def test_same_basename_in_two_dirs_gets_suffix():
    assert _flatten(["A/page01.jpg", "B/page01.jpg"]) == ["page01.jpg", "page01_2.jpg"]


def test_three_homonyms_and_root():
    paths = ["page01.jpg", "A/page01.jpg", "X/Y/Z/page01.jpg"]
    assert _flatten(paths) == ["page01.jpg", "page01_2.jpg", "page01_3.jpg"]


def test_backslash_separator():
    assert _flatten(["A\\page01.jpg", "B\\page01.jpg"]) == ["page01.jpg", "page01_2.jpg"]


def test_collision_is_case_insensitive():
    assert _flatten(["A/Page01.JPG", "B/page01.jpg"]) == ["Page01.JPG", "page01_2.jpg"]


def test_suffix_skips_already_used_name():
    paths = ["page01_2.jpg", "A/page01.jpg", "B/page01.jpg"]
    assert _flatten(paths) == ["page01_2.jpg", "page01.jpg", "page01_3.jpg"]


def test_output_names_all_distinct():
    paths = ["A/p.jpg", "B/p.jpg", "C/p.jpg", "p.jpg", "D/p_2.jpg"]
    out = _flatten(paths)
    assert len({n.lower() for n in out}) == len(out)


def test_suffixed_name_sorts_right_after_original():
    out = _flatten(["A/page01.jpg", "B/page01.jpg", "page02.jpg"])
    assert sorted(out, key=_natural_sort_key) == ["page01.jpg", "page01_2.jpg", "page02.jpg"]


# ---------------------------------------------------------------------------
# _flat_names : l'ordre alphabétique des noms à plat reste l'ordre de lecture
# ---------------------------------------------------------------------------

def test_folder_precedes_page_name():
    names = _flat_names(["Chapitre 1/001.jpg", "Chapitre 2/001.jpg", "cover.jpg"])
    assert names == {"Chapitre 1/001.jpg": "Chapitre 1_001.jpg",
                     "Chapitre 2/001.jpg": "Chapitre 2_001.jpg",
                     "cover.jpg": "cover.jpg"}


def test_chapters_are_not_interleaved():
    """Trié sur le seul nom de fichier, Ch1/001, Ch2/001, Ch1/002, Ch2/002
    se suivaient : les chapitres étaient entrelacés."""
    paths = ["Chapitre 2/002.jpg", "Chapitre 1/002.jpg", "Chapitre 2/001.jpg", "Chapitre 1/001.jpg"]
    paths.sort(key=lambda p: _natural_sort_key(p.lower()))
    flat = _flat_names(paths)
    out = _flatten([flat[p] for p in paths])
    assert sorted(out, key=_natural_sort_key) == [
        "Chapitre 1_001.jpg", "Chapitre 1_002.jpg", "Chapitre 2_001.jpg", "Chapitre 2_002.jpg"]


@pytest.mark.parametrize("char", ["[", "]", "^", "@", "=", ";"])
def test_order_is_kept_when_flattening_would_invert_two_folders(char):
    """`/` et `_` ne se trient pas au même rang : sans numéro, `Aaaa[2]_001`
    passerait devant `Aaaa_001`."""
    paths = ["Aaaa/001.jpg", "Aaaa/002.jpg", f"Aaaa{char}2/001.jpg", f"Aaaa{char}2/002.jpg"]
    flat = _flat_names(paths)
    assert sorted(paths, key=lambda p: _natural_sort_key(flat[p])) == paths
    assert flat["Aaaa/001.jpg"] == "0001_Aaaa_001.jpg"
    assert flat[f"Aaaa{char}2/002.jpg"] == f"0004_Aaaa{char}2_002.jpg"


def test_rank_does_not_depend_on_the_order_given():
    paths = ["Aaaa[2]/001.jpg", "Aaaa/002.jpg", "Aaaa/001.jpg"]
    flat = _flat_names(paths)
    assert flat["Aaaa/001.jpg"].startswith("0001_") and flat["Aaaa[2]/001.jpg"].startswith("0003_")


def test_file_that_is_not_an_image_is_never_numbered():
    flat = _flat_names(["Aaaa/001.jpg", "Aaaa[2]/001.jpg", "ComicInfo.xml", "Aaaa/notes.txt"])
    assert flat["ComicInfo.xml"] == "ComicInfo.xml" and flat["Aaaa/notes.txt"] == "Aaaa_notes.txt"
    assert flat["Aaaa/001.jpg"] == "0001_Aaaa_001.jpg"


def test_folders_in_a_safe_order_keep_their_names():
    names = _flat_names(["Aaaa-b/001.jpg", "Aaaa/001.jpg", "Aaaa_b/001.jpg"])
    assert names == {"Aaaa-b/001.jpg": "Aaaa-b_001.jpg", "Aaaa/001.jpg": "Aaaa_001.jpg",
                     "Aaaa_b/001.jpg": "Aaaa_b_001.jpg"}


def test_folder_common_to_all_pages_is_dropped():
    names = _flat_names(["MonComic/001.jpg", "MonComic/002.jpg", "MonComic\\Bonus/001.jpg"])
    assert names == {"MonComic/001.jpg": "001.jpg", "MonComic/002.jpg": "002.jpg",
                     "MonComic\\Bonus/001.jpg": "Bonus_001.jpg"}


def test_flat_archive_is_unchanged():
    assert _flat_names(["01.jpg", "02.jpg"]) == {"01.jpg": "01.jpg", "02.jpg": "02.jpg"}


def test_no_entry_named_dot_dot_or_without_name():
    """Membre « x/.. », « . » ou « C: » d'une archive piégée : jamais une
    entrée ZIP nommée « .. » ou « . » (sortie du dossier d'extraction d'un
    autre lecteur), ni sans nom (zipfile ne peut pas l'écrire)."""
    assert _flat_names(["y/x/.."]) == {"y/x/..": "x"}      # donnait « .. »
    flat = _flat_names(["..", ".", "x/./p.jpg", "x/../q.jpg"])
    assert flat == {"..": "_", ".": "_", "x/./p.jpg": "x_p.jpg", "x/../q.jpg": "x_q.jpg"}
    assert _flatten(["C:", "c:", "C:.", "C:.."]) == ["_", "__2", "__3", "__4"]


def test_control_characters_are_replaced():
    """Un retour à la ligne dans un nom de membre ne passe pas dans le CBZ."""
    assert _flat_names(["Ch\n1/p\r01.jpg", "Ch2/p01.jpg"]) == {
        "Ch\n1/p\r01.jpg": "Ch_1_p_01.jpg", "Ch2/p01.jpg": "Ch2_p01.jpg"}
