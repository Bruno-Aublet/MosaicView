"""Export de la sélection vers un dossier (file_operations_qt.save_selection_to_folder) :
une page sélectionnée sans données (illisible dans le fichier d'origine) ne peut
pas être exportée, et le résumé (FileSavedDialog) dit combien manquent, au lieu
de ne compter que les fichiers écrits.

file_operations_qt.py importe PySide6 : les méthodes sont extraites par AST.
"""
import ast
import types

import ast_loader

MODULE = "file_operations_qt.py"

Summary = ast_loader.load_methods(
    MODULE, "FileSavedDialog", {"_retranslate"}, class_attrs=("_WIDTH",),
    _=lambda key, **kw: key + str(sorted(kw.items())),
    _apply_confirmation_font=lambda widgets: None)


class FakeSummary(Summary):
    def __init__(self, missing):
        self._count, self._folder, self._skipped, self._missing = 3, "C:/tmp", 0, missing
        self.text = None
        self._msg = types.SimpleNamespace(setText=self._set, heightForWidth=lambda w: 40,
                                          setMinimumHeight=lambda h: None)
        self._close_btn = types.SimpleNamespace(setText=lambda t: None)

    def _set(self, text):
        self.text = text


def test_summary_says_how_many_selected_pages_are_missing():
    summary = FakeSummary(missing=1)
    summary._retranslate()
    assert "messages.info.files_saved.missing_pages" in summary.text
    assert "('missing', 1)" in summary.text


def test_summary_says_nothing_more_when_every_page_was_exported():
    summary = FakeSummary(missing=0)
    summary._retranslate()
    assert "missing_pages" not in summary.text


def test_export_counts_pages_without_data_and_passes_them_to_the_summary():
    tree = ast.parse((ast_loader.QT_DIR / MODULE).read_text(encoding="utf-8"))
    func = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "save_selection_to_folder")
    source = ast.unparse(func)
    assert "to_export = [e for e in chosen if e['bytes'] is not None]" in source
    assert "missing_count = len(chosen) - len(to_export)" in source
    assert "FileSavedDialog(parent, saved, folder, skipped_count, missing_count)" in source
