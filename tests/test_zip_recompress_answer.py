"""Question « Recompresser au niveau par défaut ? » / « Convertir en CBZ ? »
(clic sur l'indicateur ZIP, commande recompress_to_default :
PanelWidget._zip_indicator_clicked).

La question est non modale : pendant qu'elle reste affichée, le document du
panneau peut être fermé ou remplacé, ou une opération en arrière-plan
démarrer. La réponse « Oui » ne vaut que pour le document à qui elle a été
posée : sinon un autre document serait réenregistré (ou converti) sans que
l'utilisateur l'ait demandé.

La méthode est extraite par tests/ast_loader.py ; la fenêtre de question est
remplacée (aucun Qt), et le test donne lui-même la réponse.
"""
import os
import sys
import types

import pytest

import ast_loader
from modules.qt.state import AppState


class FakeConfirmDialog:
    """ConfirmDialog sans fenêtre : garde la suite à appeler avec la réponse."""
    last = None

    def __init__(self, parent, title_key, message_key, message_kwargs=None):
        self.on_result = None
        FakeConfirmDialog.last = self

    def ask_async(self, on_result):
        self.on_result = on_result


_Methods = ast_loader.load_methods("panel_widget.py", "PanelWidget",
                                   {"_zip_indicator_clicked"}, os=os)


class Panel(_Methods):
    def __init__(self, current_file="Aaaa.cbz"):
        self._state = AppState()
        self._state.current_file = current_file
        self._state.images_data = [{"orig_name": "01.jpg", "bytes": b"x", "is_image": True}]
        self._state.zip_compression_state = "deflated"
        self.saved = 0
        self.created = 0

    def _zip_compression_config(self):
        return types.SimpleNamespace(get_zip_compression_level=lambda: 0)

    def _apply_new_names(self):
        self.saved += 1

    def _create_cbz_from_images(self):
        self.created += 1


@pytest.fixture
def panel(monkeypatch):
    fake = types.ModuleType("modules.qt.dialogs_qt")
    fake.ConfirmDialog = FakeConfirmDialog
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", fake)
    FakeConfirmDialog.last = None
    return Panel()


def test_yes_saves_the_document_it_was_asked_for(panel):
    panel._zip_indicator_clicked()
    FakeConfirmDialog.last.on_result(True)
    assert panel.saved == 1


def test_no_saves_nothing(panel):
    panel._zip_indicator_clicked()
    FakeConfirmDialog.last.on_result(False)
    assert panel.saved == 0


def test_yes_after_the_document_was_replaced_saves_nothing(panel):
    panel._zip_indicator_clicked()
    # Document fermé puis autre fichier ouvert pendant la question
    panel._state.doc_generation += 1
    panel._state.current_file = "Bbbb.cbr"
    FakeConfirmDialog.last.on_result(True)
    assert panel.saved == 0 and panel.created == 0


def test_yes_during_an_operation_saves_nothing(panel):
    panel._zip_indicator_clicked()
    token = object()
    panel._state.begin_operation(token)
    FakeConfirmDialog.last.on_result(True)
    assert panel.saved == 0
    panel._state.end_operation(token)


def test_images_without_file_become_a_cbz_on_yes(panel):
    panel._state.current_file = None
    panel._zip_indicator_clicked()
    FakeConfirmDialog.last.on_result(True)
    assert panel.created == 1 and panel.saved == 0
