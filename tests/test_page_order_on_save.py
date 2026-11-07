"""Ordre affiché et ordre des noms à l'enregistrement, et place de la page jointe.

Vérifié :
- question seulement si la réouverture, qui trie par nom, changerait l'ordre
  affiché (la vraie clé de tri est employée) ;
- ses réponses : enregistrer quand même, annuler, renuméroter, avec une nouvelle
  vérification après la renumérotation ;
- la page jointe prend la place de la première page jointe, quel que soit le
  mode de renumérotation ;
- sélection enregistrée dans un nouveau fichier : même question, avec une
  renumérotation limitée à ce fichier, les pages du document restant inchangées.

file_operations_qt.py et merge_dialog_qt.py importent PySide6 : les fonctions
sont extraites par AST (tests/ast_loader.py), les fenêtres sont remplacées.
"""
import ast
import io
import os
import re
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

from modules.qt.comic_info import build_page_attrs_map, parse_comic_info_xml
from modules.qt.state import AppState
from modules.qt.utils import cbz_members

import ast_loader
from fakes import Signal

NATURAL_KEY = ast_loader.load("archive_loader.py", {"_natural_sort_key"},
                              os=os, re=re)["_natural_sort_key"]


class Question:
    shown = []

    def __init__(self, parent):
        Question.shown.append(self)

    def ask_async(self, on_result):
        self.answer = on_result


def _detect_duplicates(entries, state):
    names = [e["orig_name"] for e in (state.images_data if entries is None else entries)]
    repeated = sorted({name for name in names if names.count(name) > 1})
    return bool(repeated), repeated


NS = ast_loader.load("file_operations_qt.py",
                     {"_page_order_lost", "_confirm_page_order_qt", "_document_still_writable",
                      "_answer_renumber_question", "_handle_duplicate_filenames_qt",
                      "_confirm_selection_order_qt", "_selection_renumbered"},
                     cbz_members=cbz_members, PageOrderDialog=Question,
                     SelectionOrderDialog=Question,
                     DuplicateFilenameDialog=lambda parent, names: Question(parent),
                     detect_duplicate_filenames_for_save=_detect_duplicates)
order_lost = NS["_page_order_lost"]
confirm = NS["_confirm_page_order_qt"]
duplicates = NS["_handle_duplicate_filenames_qt"]


@pytest.fixture(autouse=True)
def reopening_key(monkeypatch):
    Question.shown = []
    monkeypatch.setitem(sys.modules, "modules.qt.archive_loader",
                        types.SimpleNamespace(_natural_sort_key=NATURAL_KEY))


def page(name):
    return {"orig_name": name, "bytes": b"x", "is_image": True, "is_dir": False}


def document(*names):
    state = AppState()
    state.images_data = [page(name) for name in names]
    return state


# --- Sélection enregistrée dans un nouveau fichier ----------------------------

confirm_selection = NS["_confirm_selection_order_qt"]
selection_renumbered = NS["_selection_renumbered"]


def named_page(name, extension=".jpg", size=(100, 150)):
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, "PNG")
    return {"orig_name": name, "bytes": buf.getvalue(), "extension": extension,
            "is_image": True, "is_dir": False, "img_width": size[0], "img_height": size[1]}


def ask_selection(state, entries):
    results, renumbered = [], []
    confirm_selection(None, results.append, entries, lambda: renumbered.append(True), state)
    return results, renumbered


def test_selection_in_name_order_asks_nothing():
    state = document("p01.jpg", "p02.jpg", "p03.jpg")
    results, renumbered = ask_selection(state, state.images_data[:2])
    assert results == [True] and renumbered == [] and Question.shown == []


def test_order_of_the_rest_of_the_document_does_not_matter():
    state = document("p03.jpg", "p01.jpg", "p02.jpg")
    results, _renumbered = ask_selection(state, state.images_data[1:])
    assert results == [True] and Question.shown == []


@pytest.mark.parametrize("answer, expected, renumber", [
    ("renumber", [True], [True]),
    ("ignore", [True], []),
    (None, [False], []),
])
def test_answers_to_the_selection_question(answer, expected, renumber):
    state = document("p02.jpg", "p01.jpg")
    results, renumbered = ask_selection(state, state.images_data)
    [question] = Question.shown
    assert results == []
    question.answer(answer)
    assert results == expected and renumbered == renumber


def test_renumbering_a_selection_leaves_the_document_unchanged():
    second, first = named_page("p02.jpg"), named_page("p01.png", ".png")
    copies = selection_renumbered([second, first], renumber_mode=2)
    assert [c["orig_name"] for c in copies] == ["01.jpg", "02.png"]
    assert (second["orig_name"], first["orig_name"]) == ("p02.jpg", "p01.png")
    assert copies[0]["bytes"] is second["bytes"]
    assert copies[0]["_selection_source_id"] == id(second)


def test_renumbered_selection_keeps_its_order_when_reopened():
    pages = [named_page("p10.jpg"), named_page("p02.jpg"), named_page("p07.jpg")]
    copies = selection_renumbered(pages, renumber_mode=2)
    state = AppState()
    assert order_lost(state, pages) is True and order_lost(state, copies) is False


def test_automatic_mode_names_a_double_page():
    pages = [named_page("c.jpg"), named_page("a.jpg", size=(200, 150)), named_page("b.jpg")]
    copies = selection_renumbered(pages, renumber_mode=1)
    assert [c["orig_name"] for c in copies] == ["01.jpg", "02-03.jpg", "04.jpg"]


def test_mode_off_still_numbers_simply_when_asked():
    pages = [named_page("c.jpg"), named_page("a.jpg", size=(200, 150))]
    copies = selection_renumbered(pages, renumber_mode=0)
    assert [c["orig_name"] for c in copies] == ["01.jpg", "02.jpg"]


def test_file_that_is_not_an_image_keeps_its_name():
    info = {"orig_name": "ComicInfo.xml", "bytes": b"<a/>", "extension": ".xml", "is_image": False}
    copies = selection_renumbered([info, named_page("b.jpg")], renumber_mode=2)
    assert copies[0] is info and copies[1]["orig_name"] == "01.jpg"


def test_selection_save_asks_about_the_order_and_writes_renumbered_copies():
    tree = ast.parse((Path(__file__).resolve().parent.parent / "modules" / "qt"
                      / "file_operations_qt.py").read_text(encoding="utf-8"))
    function = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "save_selection_as_cbz")
    called = {n.func.id for n in ast.walk(function)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert {"_confirm_selection_order_qt", "_selection_renumbered"} <= called


# --- L'ordre serait-il perdu ? -------------------------------------------------

@pytest.mark.parametrize("names", [
    ["01.jpg", "02.jpg", "10.jpg"],
    ["2.jpg", "10.jpg"],                       # ordre naturel, pas alphabétique
    ["a.JPG", "B.jpg"],                        # sans tenir compte de la casse
    ["02.png", "02.jpg"],                      # noms égaux sans l'extension : ordre écrit gardé
    ["a/01.jpg", "a/02.jpg", "b/01.jpg"],
    ["02.jpg", "02.jpg"],                      # le second devient 02_2.jpg à l'écriture
    [],
])
def test_order_of_the_names_needs_no_question(names):
    assert not order_lost(document(*names))


@pytest.mark.parametrize("names", [["02.jpg", "01.jpg"], ["10.jpg", "2.jpg"], ["b/01.jpg", "a/01.jpg"]])
def test_other_order_is_lost(names):
    assert order_lost(document(*names))


def test_files_that_are_not_pages_do_not_count():
    state = document("01.jpg", "02.jpg")
    state.images_data.insert(1, {"orig_name": "zz.nfo", "bytes": b"x", "is_image": False,
                                 "is_dir": False})
    state.images_data.insert(0, {"orig_name": "zz/", "bytes": None, "is_image": False,
                                 "is_dir": True})
    assert not order_lost(state)


# --- La question ---------------------------------------------------------------

def run(state, renumber=None):
    answers = []
    confirm(None, renumber, answers.append, state)
    return answers


def test_no_question_when_the_order_is_kept():
    assert run(document("01.jpg", "02.jpg")) == [True] and Question.shown == []


@pytest.mark.parametrize("answer, expected", [("ignore", True), (None, False)])
def test_save_anyway_or_cancel(answer, expected):
    answers = run(document("02.jpg", "01.jpg"))
    assert answers == []                       # en attente de la réponse
    Question.shown[0].answer(answer)
    assert answers == [expected]


def renumber_ok(state):
    def renumber(after):
        for number, entry in enumerate(state.images_data, 1):
            entry["orig_name"] = f"{number:02d}.jpg"
        after(True)
    return renumber


def test_renumbering_then_saving():
    state = document("02.jpg", "01.jpg")
    answers = run(state, renumber_ok(state))
    Question.shown[0].answer("renumber")
    assert answers == [True] and len(Question.shown) == 1


def test_order_still_lost_after_the_renumbering_asks_again():
    """Question de la première page annulée, ou première page exclue."""
    state = document("02.jpg", "01.jpg")
    answers = run(state, lambda after: after(True))
    Question.shown[0].answer("renumber")
    assert answers == [] and len(Question.shown) == 2
    Question.shown[1].answer("ignore")
    assert answers == [True]


def test_impossible_renumbering_stops_the_save():
    state = document("02.jpg", "01.jpg")
    answers = run(state, lambda after: after(False))
    Question.shown[0].answer("renumber")
    assert answers == [False]


def test_operation_started_during_the_question_stops_the_save():
    state = document("02.jpg", "01.jpg")
    called = []
    answers = run(state, called.append)
    state.begin_operation(object())
    Question.shown[0].answer("renumber")
    assert called == [] and answers == [False]


# --- Noms en double : l'enregistrement attend la renumérotation ----------------

def test_no_question_without_duplicates():
    answers = []
    duplicates(None, None, answers.append, state=document("01.jpg", "02.jpg"))
    assert answers == [True] and Question.shown == []


@pytest.mark.parametrize("answer, expected", [("ignore", True), (None, False)])
def test_duplicates_ignored_or_cancelled(answer, expected):
    answers = []
    duplicates(None, None, answers.append, state=document("01.jpg", "01.jpg"))
    Question.shown[0].answer(answer)
    assert answers == [expected]


def test_save_waits_for_the_answer_about_the_first_page():
    state = document("01.jpg", "01.jpg")
    finish = []
    answers = []
    duplicates(None, finish.append, answers.append, state=state)
    Question.shown[0].answer("renumber")
    assert answers == []                       # question de la première page affichée
    state.images_data[1]["orig_name"] = "02.jpg"
    finish[0](True)
    assert answers == [True] and len(Question.shown) == 1


def test_first_page_question_cancelled_asks_about_the_duplicates_again():
    state = document("01.jpg", "01.jpg")
    answers = []
    duplicates(None, lambda after: after(True), answers.append, state=state)
    Question.shown[0].answer("renumber")
    assert answers == [] and len(Question.shown) == 2


def test_impossible_renumbering_stops_the_save_of_duplicates():
    answers = []
    duplicates(None, lambda after: after(False), answers.append,
               state=document("01.jpg", "01.jpg"))
    Question.shown[0].answer("renumber")
    assert answers == [False]


def test_selection_is_checked_again_alone():
    state = document("01.jpg", "01.jpg", "02.jpg")
    selection = state.images_data[1:]
    answers = []
    duplicates(None, None, answers.append, entries_to_check=selection, state=state)
    assert answers == [True] and Question.shown == []


# --- Pages ajoutées par la numérisation et l'import web ------------------------

def document_with_pages_list(monkeypatch):
    monkeypatch.setitem(sys.modules, "modules.qt.metadata_signal",
                        types.SimpleNamespace(metadata_signal=Signal()))
    xml = b'<ComicInfo><Pages><Page Image="0" Type="FrontCover"/></Pages></ComicInfo>'
    state = document("01.jpg")
    info = {"orig_name": "ComicInfo.xml", "bytes": xml, "extension": ".xml",
            "is_image": False, "is_dir": False}
    state.images_data.append(info)
    state.comic_metadata = parse_comic_info_xml(xml)
    build_page_attrs_map(state)
    return state, info


def assert_new_page_is_listed_before_the_history_state(state, info, xml_at_save):
    pages = parse_comic_info_xml(info["bytes"])["pages"]
    assert [p["Image"] for p in pages] == ["0", "1"] and pages[0]["Type"] == "FrontCover"
    assert xml_at_save[-1] == info["bytes"]


def test_scanned_page_enters_the_pages_list(monkeypatch):
    state, info = document_with_pages_list(monkeypatch)
    saved = []
    add = ast_loader.load("scan_dialog_qt.py", {"_add_entry_to_mosaic"},
                          _state_module=types.SimpleNamespace(state=None))["_add_entry_to_mosaic"]
    add(page("scan.jpg"), {"state": state, "save_state": lambda: saved.append(info["bytes"]),
                           "render_mosaic": lambda: None})
    assert_new_page_is_listed_before_the_history_state(state, info, saved)


def test_page_from_the_web_enters_the_pages_list(monkeypatch):
    state, info = document_with_pages_list(monkeypatch)
    saved = []
    add = ast_loader.load("web_import_qt.py", {"_append_entries"})["_append_entries"]
    add(state, [page("web.jpg")], {"save_state": lambda: saved.append(info["bytes"]),
                                   "render_mosaic": lambda: None})
    assert_new_page_is_listed_before_the_history_state(state, info, saved)


# --- Page jointe ---------------------------------------------------------------

class YesNoCancel:
    YES, NO, CANCEL = "yes", "no", "cancel"
    shown = []

    def __init__(self, parent, title, message):
        pass

    def ask_async(self, on_result):
        YesNoCancel.shown.append(on_result)


class Merge(ast_loader.load_methods(
        "merge_dialog_qt.py", "MergeDialog", {"_ask_delete_sources"},
        YesNoCancelDialog=YesNoCancel, _save_state_data=lambda state, force=False: None)):

    def __init__(self, state, sources):
        self._state, self._source_entries, self._doc_generation = state, sources, state.doc_generation
        self.renumbered = []
        self._callbacks = {"render_mosaic": lambda: None, "update_button_text": lambda: None,
                           "clear_selection": lambda: None,
                           "renumber_no_save": lambda on_done: (self.renumbered.append(1), on_done())}


def joined(mode, answer):
    state = document("01.jpg", "02.jpg", "03.jpg", "04.jpg")
    state.renumber_mode = mode
    pages = state.images_data
    merge = Merge(state, [pages[1], pages[2]])
    merge._ask_delete_sources(None, page("Collage_001.jpg"))
    YesNoCancel.shown[-1](answer)
    return [e["orig_name"] for e in state.images_data], merge.renumbered


@pytest.mark.parametrize("mode, renumbered", [(0, []), (1, [1]), (2, [1])])
def test_joined_page_replaces_its_sources_at_the_first_one(mode, renumbered):
    assert joined(mode, "yes") == (["01.jpg", "Collage_001.jpg", "04.jpg"], renumbered)


@pytest.mark.parametrize("mode, renumbered", [(0, []), (1, [1]), (2, [1])])
def test_joined_page_comes_before_its_kept_sources(mode, renumbered):
    assert joined(mode, "no") == (["01.jpg", "Collage_001.jpg", "02.jpg", "03.jpg", "04.jpg"],
                                  renumbered)
