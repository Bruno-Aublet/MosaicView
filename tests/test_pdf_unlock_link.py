"""Lien de la fenêtre de succès du déverrouillage d'un PDF : l'Explorateur
s'ouvre sur le fichier écrit, sélectionné, comme partout ailleurs (skill
explorer-select), et non sur son dossier seul.
"""
import os
import sys
import types

import ast_loader


def test_link_selects_the_unlocked_copy_in_the_explorer(monkeypatch):
    selected = []
    monkeypatch.setitem(sys.modules, "modules.qt.library_window",
                        types.SimpleNamespace(_explorer_select=selected.append))
    methods = ast_loader.load_methods("pdf_unlock_qt.py", "_PdfUnlockedSuccessDialog",
                                      {"_open_file"}, os=os)

    class Window(methods):
        _dest_path = "C:/Aaaa/Bbbb_unlocked.pdf"

    Window()._open_file()
    assert selected == ["C:\\Aaaa\\Bbbb_unlocked.pdf"]
