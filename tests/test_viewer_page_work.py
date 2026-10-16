"""Travail non validé de la visionneuse rangé par page (_PageWorkStore).

La visionneuse est non modale : la mosaïque reste modifiable pendant qu'elle
est ouverte. Un rectangle de recadrage, des formes ou un texte en attente sur
une page doivent rester attachés à cette page quand des pages sont
supprimées, déplacées ou insérées avant elle — jamais passer à la page qui
prend sa position.

image_viewer_qt.py importe PySide6 au niveau module : la classe est extraite
par AST et exécutée seule, sans aucun import Qt.
"""
import ast
from pathlib import Path

from modules.qt.state import AppState

ROOT = Path(__file__).resolve().parent.parent


def _load():
    source = (ROOT / "modules" / "qt" / "image_viewer_qt.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.ClassDef) and n.name == "_PageWorkStore")
    ns = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "image_viewer_qt.py", "exec"), ns)
    return ns["_PageWorkStore"]


PageWorkStore = _load()


def make_state(*names):
    st = AppState()
    st.images_data = [{"orig_name": n, "bytes": n.encode(), "is_image": True} for n in names]
    return st


def test_work_follows_its_page_after_a_deletion_before_it():
    st = make_state("p01.jpg", "p02.jpg", "p03.jpg")
    store = PageWorkStore(st)
    store[2] = "crop of p03"
    del st.images_data[0]
    assert store.get(1) == "crop of p03"
    assert 2 not in store
    assert store.get(0) is None


def test_work_follows_its_page_after_a_move():
    st = make_state("p01.jpg", "p02.jpg", "p03.jpg")
    store = PageWorkStore(st)
    store[0] = "shapes of p01"
    st.images_data.append(st.images_data.pop(0))
    assert store.get(2) == "shapes of p01"
    assert 0 not in store


def test_inserted_page_gets_no_work():
    st = make_state("p01.jpg", "p02.jpg")
    store = PageWorkStore(st)
    store[0] = "text of p01"
    st.images_data.insert(0, {"orig_name": "new.jpg", "bytes": b"n", "is_image": True})
    assert 0 not in store
    assert store[1] == "text of p01"


def test_work_of_a_removed_page_is_forgotten():
    """Plus de travail en attente : fermer la visionneuse ne demande pas de
    confirmation pour une page qui n'existe plus."""
    st = make_state("p01.jpg", "p02.jpg")
    store = PageWorkStore(st)
    store[1] = "crop of p02"
    assert store
    del st.images_data[1]
    assert not store


def test_pop_and_out_of_range():
    st = make_state("p01.jpg")
    store = PageWorkStore(st)
    store[0] = "x"
    assert store.pop(0) == "x"
    assert store.pop(0) is None
    assert store.get(5) is None
    store[5] = "ignored"
    assert not store
