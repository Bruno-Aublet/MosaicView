"""Extraction de code par AST : seulement par tests/ast_loader.py.

Une extraction écrite à la main (ast.Module compilé puis exécuté) ne contrôle
pas les dépendances de ce qu'elle extrait : une dépendance ajoutée plus tard
au module lève NameError à l'exécution, qu'un `except` de repli peut avaler,
et le test vérifie alors le repli sans le savoir. ast_loader.load /
load_inner / load_methods font échouer le test en nommant ce qui manque.
"""
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
HAND_EXTRACTION = re.compile(r"ast\.Module\(|exec\(compile")


def _hand_extractors():
    own = Path(__file__).name
    return {p.name for p in TESTS.glob("*.py")
            if p.name not in ("ast_loader.py", own)
            and HAND_EXTRACTION.search(p.read_text(encoding="utf-8"))}


def test_tests_extract_with_ast_loader():
    found = sorted(_hand_extractors())
    assert not found, ("extraire par ast_loader.load / load_inner / load_methods, "
                       "pas à la main :\n" + "\n".join(found))


def test_load_methods_takes_the_class_constants_with_the_methods():
    """Constantes de classe reprises telles qu'écrites dans le module, pas
    recopiées dans le test."""
    import ast_loader
    worker = ast_loader.load_methods("archive_loader.py", "LoadWorker", {"_emit_error"},
                                     class_attrs=("_EXT_TO_TYPE",))
    assert worker._EXT_TO_TYPE[".cbz"] == "CBZ"
    assert not hasattr(worker, "_ERROR_KEYS")             # seulement celles demandées


def test_load_inner_takes_definitions_from_inside_a_method():
    import ast_loader
    ns = ast_loader.load_inner("panel_widget.py", ("PanelWidget", "_on_inter_panel_drop"),
                               {"_copy_entry", "_UI_KEYS"},
                               is_altered=lambda e: False, mark_altered=lambda e: None)
    copy = ns["_copy_entry"]({"bytes": b"x", "img": object()})
    assert copy["bytes"] == b"x" and copy["img"] is None
    assert all(copy[k] is None for k in ns["_UI_KEYS"])


def test_load_inner_names_a_missing_dependency():
    import pytest
    import ast_loader
    with pytest.raises(AssertionError, match="mark_altered"):
        ast_loader.load_inner("panel_widget.py", ("PanelWidget", "_on_inter_panel_drop"),
                              {"_copy_entry", "_UI_KEYS"}, is_altered=lambda e: False)


def test_load_methods_names_a_missing_class_constant():
    import pytest
    import ast_loader
    with pytest.raises(AssertionError, match="_NOT_A_CONSTANT"):
        ast_loader.load_methods("archive_loader.py", "LoadWorker", {"_emit_error"},
                                class_attrs=("_NOT_A_CONSTANT",))
