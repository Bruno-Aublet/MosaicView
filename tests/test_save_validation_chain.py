"""Chaîne de validation et fin différée d'un enregistrement (file_operations_qt).

- _run_validation_chain enchaîne des validations non modales : elle s'arrête
  à la première refusée, et en silence si le document a été fermé ou remplacé
  entre deux étapes (sinon la suite enregistrerait l'état vidé du document
  fermé, ou le document suivant sous le nom du précédent).
- _finish_apply_new_names ne marque le document « enregistré » que s'il a
  encore le contenu écrit et qu'il n'a pas été fermé ou remplacé entre
  l'écriture et la fermeture de la confirmation.
- _mark_save_unverified repasse le document à « modifié » quand le CBZ écrit
  ne se relit pas (l'original est gardé) : sa fermeture doit redemander.
- _delete_original_after_save, suite de « Supprimer l'original ? » répondue
  Oui après « Enregistrer sous » ou une conversion d'« Appliquer » : original
  à la Corbeille si le CBZ écrit est inchangé et se relit ; sinon original
  gardé, avec le bon message, et document repassé à « modifié » si le CBZ ne
  se relit pas.

file_operations_qt.py importe PySide6 : les fonctions sont extraites par AST
(tests/ast_loader.py).
"""
import os
import types
import zipfile

import pytest

import modules.qt.utils as utils
from modules.qt.converted_cbz import ConvertedCbzChangedError, reread_then_delete_source
from modules.qt.state import AppState
from modules.qt.undo_redo import (content_snapshot, content_unchanged, mark_history_saved,
                                  mark_history_unsaved, save_state_data)
from modules.qt.utils import WrittenFileCheckError

import ast_loader
from fakes import tr as _tr

SHOWN = []   # (message, cri de Wilhelm) des fenêtres d'erreur


def _error_dialog(parent, title_fn, message_fn, play_sound=False):
    SHOWN.append((message_fn(), play_sound))
    return types.SimpleNamespace(show_nonmodal=lambda: None)


NS = ast_loader.load(
    "file_operations_qt.py",
    {"_run_validation_chain", "_finish_apply_new_names", "_mark_save_unverified",
     "_delete_original_after_save"},
    content_unchanged=content_unchanged, mark_history_saved=mark_history_saved,
    mark_history_unsaved=mark_history_unsaved, reread_then_delete_source=reread_then_delete_source,
    WrittenFileCheckError=WrittenFileCheckError, ConvertedCbzChangedError=ConvertedCbzChangedError,
    ErrorDialog=_error_dialog, _=_tr, _wt=_tr)
run_validation_chain = NS["_run_validation_chain"]
finish_apply_new_names = NS["_finish_apply_new_names"]
mark_save_unverified = NS["_mark_save_unverified"]
delete_original_after_save = NS["_delete_original_after_save"]


def page(name, data):
    return {"orig_name": name, "bytes": data, "extension": ".jpg", "is_image": True}


def modified_document():
    st = AppState()
    st.current_file = "Aaaa.cbz"
    st.images_data = [page("p01.jpg", b"A")]
    save_state_data(st, force=True)
    st.images_data.append(page("p02.jpg", b"B"))
    save_state_data(st, force=True)
    st.modified = True
    return st


# ---------------------------------------------------------------------------
# _run_validation_chain
# ---------------------------------------------------------------------------

def recording_steps(log, answers):
    """Étapes qui notent leur passage et répondent tout de suite."""
    def step(i, ok):
        def run(done):
            log.append(i)
            done(ok)
        return run
    return [step(i, ok) for i, ok in enumerate(answers)]


def test_every_step_passed_runs_the_save_once():
    log, saved = [], []
    run_validation_chain(recording_steps(log, [True, True, True]), lambda: saved.append(1),
                         state=AppState())
    assert log == [0, 1, 2]
    assert saved == [1]


def test_refused_step_stops_the_chain():
    log, saved = [], []
    run_validation_chain(recording_steps(log, [True, False, True]), lambda: saved.append(1),
                         state=AppState())
    assert log == [0, 1]
    assert saved == []


def test_document_closed_during_a_step_is_never_saved():
    """La fermeture du document ferme la fenêtre de l'étape, et une fenêtre
    d'information fermée vaut « continuer »."""
    st = AppState()
    log, saved = [], []

    def closing_step(done):
        log.append("question")
        st.doc_generation += 1     # force_close_file pendant la question
        done(True)

    def next_step(done):
        log.append("suivante")
        done(True)

    run_validation_chain([closing_step, next_step], lambda: saved.append(1), state=st)
    assert log == ["question"]
    assert saved == []


def test_document_replaced_during_the_last_step_is_never_saved():
    st = AppState()
    saved = []

    def replacing_step(done):
        st.doc_generation += 1     # autre fichier ouvert dans le panneau
        done(True)

    run_validation_chain([lambda done: done(True), replacing_step], lambda: saved.append(1),
                         state=st)
    assert saved == []


def test_answer_given_later_continues_the_chain():
    """Validation non modale : la réponse arrive après le retour de l'étape."""
    st = AppState()
    pending, saved = [], []
    run_validation_chain([lambda done: pending.append(done)], lambda: saved.append(1), state=st)
    assert saved == []
    pending[0](True)
    assert saved == [1]


def test_late_answer_after_closing_is_ignored():
    st = AppState()
    pending, saved = [], []
    run_validation_chain([lambda done: pending.append(done), lambda done: done(True)],
                         lambda: saved.append(1), state=st)
    st.doc_generation += 1
    pending[0](True)
    assert saved == []


# ---------------------------------------------------------------------------
# _finish_apply_new_names
# ---------------------------------------------------------------------------

def finish_callbacks(log):
    return {name: (lambda *a, n=name: log.append((n,) + a))
            for name in ("render_mosaic", "update_button_text", "update_tabs", "on_file_saved")}


def test_unchanged_document_is_marked_saved():
    st = modified_document()
    written = (content_snapshot(st), st.doc_generation)
    log, done = [], []
    finish_apply_new_names(st, finish_callbacks(log), done.append, written)
    assert done == [True]
    assert st.modified is False
    assert st.history[st.history_index]["modified"] is False
    assert ("on_file_saved", "Aaaa.cbz") in log


def test_document_changed_after_the_write_stays_modified():
    """Une page ajoutée pendant la confirmation non modale n'est pas dans le
    fichier : « Enregistrer et fermer » ne doit pas fermer sans demander."""
    st = modified_document()
    written = (content_snapshot(st), st.doc_generation)
    st.images_data.append(page("p03.jpg", b"C"))
    done = []
    finish_apply_new_names(st, finish_callbacks([]), done.append, written)
    assert done == [False]
    assert st.modified is True


def test_page_bytes_replaced_after_the_write_stay_modified():
    st = modified_document()
    written = (content_snapshot(st), st.doc_generation)
    st.images_data[0]["bytes"] = b"Z"
    done = []
    finish_apply_new_names(st, finish_callbacks([]), done.append, written)
    assert done == [False]
    assert st.modified is True


def test_document_closed_before_the_confirmation_is_left_alone():
    st = modified_document()
    written = (content_snapshot(st), st.doc_generation)
    st.doc_generation += 1
    log, done = [], []
    finish_apply_new_names(st, finish_callbacks(log), done.append, written)
    assert done == [False]
    assert log == []
    assert st.modified is True


# ---------------------------------------------------------------------------
# _mark_save_unverified
# ---------------------------------------------------------------------------

class Panel:
    def __init__(self):
        self.status_updates = 0

    def _update_status_bar(self):
        self.status_updates += 1


def test_unreadable_written_cbz_puts_the_document_back_to_modified():
    st = modified_document()
    mark_history_saved(st)
    st.modified = False
    panel, log = Panel(), []
    callbacks = {name: (lambda n=name: log.append(n))
                 for name in ("update_button_text", "update_tabs", "update_window_title")}
    mark_save_unverified(panel, callbacks, st, st.doc_generation)
    assert st.modified is True
    assert all(snap["modified"] for snap in st.history)   # Annuler ne rend jamais « enregistré »
    assert panel.status_updates == 1
    assert sorted(log) == ["update_button_text", "update_tabs", "update_window_title"]


def test_unverified_save_of_a_closed_document_changes_nothing():
    st = modified_document()
    mark_history_saved(st)
    st.modified = False
    generation = st.doc_generation
    st.doc_generation += 1
    mark_save_unverified(Panel(), {}, st, generation)
    assert st.modified is False
    assert st.history[st.history_index]["modified"] is False


# ---------------------------------------------------------------------------
# _delete_original_after_save
# ---------------------------------------------------------------------------

@pytest.fixture
def saved(tmp_path, monkeypatch):
    """Document enregistré dans un CBZ de deux pages, original encore là,
    Corbeille du test (jamais la vraie)."""
    SHOWN.clear()
    trash = []

    def fake_send_to_trash(path):
        trash.append(os.path.basename(path))
        os.remove(path)
    monkeypatch.setattr(utils, "send_to_trash", fake_send_to_trash)
    cbz = tmp_path / "Aaaa.cbz"
    with zipfile.ZipFile(cbz, "w") as zf:
        zf.writestr("p01.jpg", b"A")
        zf.writestr("p02.jpg", b"B")
    original = tmp_path / "Aaaa.cbr"
    original.write_bytes(b"Rar! source")
    st = modified_document()
    mark_history_saved(st)
    st.modified = False
    return types.SimpleNamespace(cbz=str(cbz), original=str(original), state=st, trash=trash,
                                 signature=utils.file_signature(str(cbz)))


def answer_yes(s, written_count=2, original=None):
    return delete_original_after_save(Panel(), {}, s.state, s.state.doc_generation, s.cbz,
                                      written_count, s.original if original is None else original,
                                      s.signature)


def test_yes_sends_the_original_to_the_trash(saved):
    assert answer_yes(saved) is True
    assert saved.trash == ["Aaaa.cbr"] and SHOWN == []
    assert saved.state.modified is False


def test_cbz_that_does_not_reread_keeps_the_original_and_the_document_modified(saved):
    assert answer_yes(saved, written_count=3) is False    # une page manque à la relecture
    assert os.path.exists(saved.original) and saved.trash == []
    assert saved.state.modified is True                   # la fermeture redemandera
    [(_message, wilhelm)] = SHOWN
    assert wilhelm is True


def test_cbz_rewritten_before_the_answer_keeps_the_original_without_alarm(saved):
    with zipfile.ZipFile(saved.cbz, "w") as zf:           # autre programme, autre panneau
        zf.writestr("p01.jpg", b"autre page, autre taille")
        zf.writestr("p02.jpg", b"B")
    assert answer_yes(saved) is True
    assert os.path.exists(saved.original) and saved.trash == []
    assert saved.state.modified is False                  # le document reste enregistré
    [(_message, wilhelm)] = SHOWN
    assert wilhelm is False


def test_original_that_cannot_go_to_the_trash_is_reported(saved, monkeypatch):
    def refuse(path):
        raise OSError(5, "refus", path)
    monkeypatch.setattr(utils, "send_to_trash", refuse)
    assert answer_yes(saved) is True
    assert os.path.exists(saved.original)
    [(message, wilhelm)] = SHOWN
    assert message.startswith("messages.errors.delete_error") and wilhelm is True


def test_without_an_original_nothing_is_deleted_or_reread(saved, monkeypatch):
    reread = []
    monkeypatch.setattr(utils, "verify_written_cbz", lambda *a, **k: reread.append(a))
    assert answer_yes(saved, original="") is True
    assert reread == [] and saved.trash == [] and SHOWN == []
