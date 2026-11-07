"""Aplatissement des répertoires (flatten_directories_qt.flatten_entries).

- Les fichiers des sous-dossiers sont ramenés à la racine, dossiers retirés,
  collisions résolues par _1, _2…
- Un ComicInfo.xml sorti d'un dossier ne prend jamais la place du ComicInfo.xml
  de la BD, quel que soit l'ordre de la liste : il est nommé comme toute
  arrivée (ComicInfo_0002.xml…), ou devient le ComicInfo.xml de la BD si elle
  n'en a pas.
- L'historique reçoit un seul état nouveau : pas d'Annuler sans effet.
"""
import sys
import types

import pytest

from modules.qt import flatten_directories_qt
from modules.qt.flatten_directories_qt import flatten_entries
from modules.qt.state import AppState
from modules.qt.undo_redo import save_state_data


def entry(name, is_dir=False):
    return {"orig_name": name, "is_dir": is_dir, "bytes": None if is_dir else b"x"}


def names(entries):
    return [e["orig_name"] for e in entries]


def test_files_come_to_root_and_dirs_are_removed():
    data = [entry("ch1", is_dir=True), entry("ch1/p01.jpg"), entry("ch2/p01.jpg"), entry("p02.jpg")]
    assert names(flatten_entries(data)) == ["p01.jpg", "p01_1.jpg", "p02.jpg"]


def test_new_prefix_of_first_folder_is_kept():
    assert names(flatten_entries([entry("NEW-ch1/p01.jpg")])) == ["NEW-p01.jpg"]


def test_folder_comic_info_after_the_real_one_is_renamed():
    data = [entry("ComicInfo.xml"), entry("Bonus/ComicInfo.xml")]
    assert names(flatten_entries(data)) == ["ComicInfo.xml", "ComicInfo_0002.xml"]


def test_folder_comic_info_before_the_real_one_never_takes_its_place():
    real = entry("ComicInfo.xml")
    bonus = entry("Bonus/ComicInfo.xml")
    flatten_entries([bonus, real])
    assert real["orig_name"] == "ComicInfo.xml"
    assert bonus["orig_name"] == "ComicInfo_0002.xml"


def test_case_is_ignored_for_comic_info():
    data = [entry("ComicInfo.xml"), entry("bonus/comicinfo.xml")]
    assert names(flatten_entries(data)) == ["ComicInfo.xml", "comicinfo_0002.xml"]


def test_folder_comic_info_becomes_the_comic_info_when_there_is_none():
    data = [entry("p01.jpg"), entry("Bonus/ComicInfo.xml"), entry("Extra/ComicInfo.xml")]
    assert names(flatten_entries(data)) == ["p01.jpg", "ComicInfo.xml", "ComicInfo_0002.xml"]


def test_renamed_comic_info_skips_names_already_taken():
    data = [entry("ComicInfo.xml"), entry("ComicInfo_0002.xml"), entry("Bonus/ComicInfo.xml")]
    assert names(flatten_entries(data))[2] == "ComicInfo_0003.xml"


def page_entry(name):
    return {"orig_name": name, "bytes": name.encode(), "extension": ".jpg", "is_image": True}


@pytest.fixture
def flatten(monkeypatch):
    """Aplatit le document de `state`, sans Qt (minuterie et signal remplacés)."""
    timer = types.SimpleNamespace(singleShot=lambda delay, fn: None)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", types.SimpleNamespace(QTimer=timer))
    monkeypatch.setitem(sys.modules, "modules.qt.metadata_signal",
                        types.SimpleNamespace(metadata_signal=None))

    def run(state):
        monkeypatch.setattr(flatten_directories_qt._state_module, "state", state)
        flatten_directories_qt.flatten_directories_qt(
            None, lambda: None, lambda: None, types.SimpleNamespace(emit=lambda: None),
            save_state_func=lambda: save_state_data(state))
    return run


def test_undo_after_opening_has_a_single_step(flatten):
    """État de l'ouverture, puis état aplati : une copie du premier donnerait un
    second Annuler sans effet."""
    state = AppState()
    state.images_data = [page_entry("ch1/p01.jpg"), page_entry("ch1/p02.jpg")]
    save_state_data(state, force=True)
    flatten(state)
    assert names(state.images_data) == ["p01.jpg", "p02.jpg"]
    assert [names(s["entries"]) for s in state.history] == [
        ["ch1/p01.jpg", "ch1/p02.jpg"], ["p01.jpg", "p02.jpg"]]
    assert state.history_index == 1


def test_state_before_flattening_is_saved_when_missing(flatten):
    """Le document a changé depuis le dernier état : l'état d'avant est posé, Annuler
    revient bien à lui."""
    state = AppState()
    state.images_data = [page_entry("ch1/p01.jpg")]
    save_state_data(state, force=True)
    state.images_data.append(page_entry("ch1/p02.jpg"))
    flatten(state)
    assert [names(s["entries"]) for s in state.history] == [
        ["ch1/p01.jpg"], ["ch1/p01.jpg", "ch1/p02.jpg"], ["p01.jpg", "p02.jpg"]]
