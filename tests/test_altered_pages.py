"""Pages altérées : somme de contrôle fausse, image encore lisible.

Une page dont les octets ont changé depuis la création de son archive (CRC
faux) est relue quand même ; si elle se décode, elle est affichée, marquée
altérée (cadre orange), et une question est posée avant tout enregistrement
qui l'écrirait (sa nouvelle somme de contrôle effacerait la trace). Une page
dont les octets ne se décodent plus reste illisible (cadre rouge).

- Vraies archives : ZIP et 7z stockés (7z.exe embarqué), RAR stocké (créé par
  WinRAR s'il est installé, sinon test ignoré ; relu par l'UnRAR embarqué).
- La marque suit les octets : une retouche (nouveaux octets) l'efface, les
  octets d'origine rendus par Annuler la ramènent.
- Les quatre enregistrements du document et « Sélection en CBZ » posent la
  question ; l'écriture du document entier retire les marques.

archive_loader et file_operations_qt chargent Qt : leurs fonctions sont
extraites par AST.
"""
import ast
import io
import os
import random
import subprocess
import sys
import threading
import types
import zipfile
from pathlib import Path

import pytest
import rarfile
from PIL import Image

from modules.qt import archive_names
from modules.qt.entries import (create_entry, expand_multipage_tiffs, forget_altered_pages,
                                is_altered, mark_altered)

ROOT = Path(__file__).resolve().parent.parent
SEVEN_ZIP = ROOT / "7zip" / "7z.exe"
UNRAR = ROOT / "unrar" / "UnRAR.exe"
WINRAR = Path(r"C:\Program Files\WinRAR\Rar.exe")

_LOADER_NAMES = {"AlteredMemberError", "_run_and_read", "_read_7z_file", "_to_short_path",
                 "_check_member_size", "_zip_member_reader", "_rar_read",
                 "_rar_bytes_without_crc", "create_page_entry"}


def _nodes(module, names):
    tree = ast.parse((ROOT / "modules" / "qt" / module).read_text(encoding="utf-8"))
    found = [n for n in tree.body
             if (isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names)
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) in names for t in n.targets))]
    return found


def _loader():
    ns = {"os": os, "sys": sys, "subprocess": subprocess, "threading": threading,
          "zipfile": zipfile, "rarfile": rarfile, "create_entry": create_entry,
          "IMAGE_EXTS": archive_names.IMAGE_EXTS, "MAX_MEMBER_SIZE": archive_names.MAX_MEMBER_SIZE,
          "_get_7z_exe": lambda: str(SEVEN_ZIP)}
    nodes = _nodes("archive_loader.py", _LOADER_NAMES)
    assert {n.name for n in nodes} == _LOADER_NAMES
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "archive_loader.py", "exec"), ns)
    return ns


L = _loader()


def _jpeg():
    random.seed(7)
    img = Image.new("RGB", (320, 240))
    img.putdata([(random.randrange(256), (x * 3) % 256, 90) for x in range(320 * 240)])
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()


GOOD = _jpeg()
# Abîmée au milieu des données d'image : défauts visibles, mais se décode
ALTERED = GOOD[:len(GOOD) // 2] + bytes(32) + GOOD[len(GOOD) // 2 + 32:]
# Début du fichier détruit : ne se décode plus
DESTROYED = bytes(2048) + GOOD[2048:]


def _decodes(data):
    try:
        Image.open(io.BytesIO(data)).load()
        return True
    except Exception:
        return False


def test_sample_pages_behave_as_expected():
    assert _decodes(GOOD) and _decodes(ALTERED) and not _decodes(DESTROYED)


def _damage(archive, damaged):
    """Remplace dans le fichier `archive` (membres stockés, octets en clair)
    les octets de la page saine par `damaged`, sans toucher à sa somme de
    contrôle : comme un disque qui abîme le fichier après sa création."""
    blob = open(archive, "rb").read()
    assert blob.count(GOOD) == 1
    open(archive, "wb").write(blob.replace(GOOD, damaged))


def _zip(tmp_path, damaged):
    path = tmp_path / "comic.cbz"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        z.writestr("page_01.jpg", GOOD)
    _damage(path, damaged)
    return path


def _read_zip(path):
    with archive_names.open_zip(path) as archive:
        read = L["_zip_member_reader"](archive)
        return L["create_page_entry"]("page_01.jpg", lambda: read("page_01.jpg"))


def test_zip_altered_page_is_shown_and_marked(tmp_path):
    entry, message = _read_zip(_zip(tmp_path, ALTERED))
    assert message is None
    assert entry["bytes"] == ALTERED and not entry["is_corrupted"]
    assert is_altered(entry)


def test_zip_destroyed_page_stays_unreadable(tmp_path):
    entry, message = _read_zip(_zip(tmp_path, DESTROYED))
    assert entry["is_corrupted"] and entry["bytes"] is None
    assert "CRC" in message and not is_altered(entry)


def test_sound_zip_page_is_not_marked(tmp_path):
    path = tmp_path / "comic.cbz"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        z.writestr("page_01.jpg", GOOD)
    entry, message = _read_zip(path)
    assert message is None and not is_altered(entry)


def test_altered_member_is_still_an_error_for_other_readers(tmp_path):
    """Conversions par lot, Bibliothèque : AlteredMemberError reste une
    erreur de lecture ordinaire pour qui ne la traite pas."""
    with archive_names.open_zip(_zip(tmp_path, ALTERED)) as archive:
        with pytest.raises(Exception) as caught:
            L["_zip_member_reader"](archive)("page_01.jpg")
    assert isinstance(caught.value, L["AlteredMemberError"])


_CASES = pytest.mark.parametrize("damaged, shown", [(ALTERED, True), (DESTROYED, False)],
                                 ids=["altered", "destroyed"])


@pytest.mark.skipif(sys.platform != "win32" or not SEVEN_ZIP.exists(), reason="7z.exe requis")
@_CASES
def test_7z_altered_page(tmp_path, damaged, shown):
    (tmp_path / "page_01.jpg").write_bytes(GOOD)
    archive = tmp_path / "comic.cb7"
    subprocess.run([str(SEVEN_ZIP), "a", "-t7z", "-mx0", str(archive), str(tmp_path / "page_01.jpg")],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)
    _damage(archive, damaged)
    entry, message = L["create_page_entry"](
        "page_01.jpg", lambda: L["_read_7z_file"](str(archive), "page_01.jpg"))
    assert is_altered(entry) is shown
    assert (message is None) is shown
    assert entry["is_corrupted"] is not shown


@pytest.mark.skipif(sys.platform != "win32" or not WINRAR.exists() or not UNRAR.exists(),
                    reason="WinRAR (création du RAR) et UnRAR embarqué requis")
@_CASES
def test_rar_altered_page(tmp_path, monkeypatch, damaged, shown):
    monkeypatch.setattr(rarfile, "UNRAR_TOOL", str(UNRAR))
    rarfile.tool_setup(force=True)
    (tmp_path / "page_01.jpg").write_bytes(GOOD)
    archive = tmp_path / "comic.cbr"
    subprocess.run([str(WINRAR), "a", "-m0", "-ep", "-idq", str(archive), str(tmp_path / "page_01.jpg")],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)
    _damage(archive, damaged)
    with rarfile.RarFile(str(archive)) as rf:
        entry, message = L["create_page_entry"]("page_01.jpg", lambda: L["_rar_read"](rf, "page_01.jpg"))
    assert is_altered(entry) is shown
    assert (message is None) is shown


def test_mark_follows_the_page_bytes():
    entry = create_entry("page.jpg", ALTERED, archive_names.IMAGE_EXTS)
    mark_altered(entry)
    assert is_altered(entry)
    original = entry["bytes"]
    entry["bytes"] = GOOD                    # retouche : nouveaux octets
    assert not is_altered(entry)
    entry["bytes"] = original                # Annuler : octets lus rendus
    assert is_altered(entry)
    forget_altered_pages([entry])            # enregistré dans le fichier du document
    assert not is_altered(entry)


def _inter_panel_copy(byte_copy):
    """_copy_entry de PanelWidget._on_inter_panel_drop (copie d'une page
    déplacée vers l'autre panneau), extraite du vrai code ; `byte_copy`
    remplace bytes() pour simuler une autre façon de copier les octets."""
    tree = ast.parse((ROOT / "modules" / "qt" / "panel_widget.py").read_text(encoding="utf-8"))
    method = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_on_inter_panel_drop")
    nodes = [n for n in method.body
             if (isinstance(n, ast.FunctionDef) and n.name == "_copy_entry")
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "_UI_KEYS" for t in n.targets))]
    assert len(nodes) == 2
    ns = {"is_altered": is_altered, "mark_altered": mark_altered, "bytes": byte_copy}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "panel_widget.py", "exec"), ns)
    return ns["_copy_entry"]


@pytest.mark.parametrize("byte_copy", [bytes, lambda b: bytes(bytearray(b))],
                         ids=["same_object", "real_copy"])
def test_page_moved_to_the_other_panel_stays_marked(byte_copy):
    entry = create_entry("page.jpg", ALTERED, archive_names.IMAGE_EXTS)
    mark_altered(entry)
    moved = _inter_panel_copy(byte_copy)(entry)
    assert moved["bytes"] == ALTERED and is_altered(moved)
    sound = create_entry("page.jpg", GOOD, archive_names.IMAGE_EXTS)
    assert not is_altered(_inter_panel_copy(byte_copy)(sound))


def test_pages_of_an_altered_multipage_tiff_are_all_marked():
    frames = [Image.new("RGB", (40, 30), c) for c in ("red", "blue")]
    out = io.BytesIO()
    frames[0].save(out, "TIFF", save_all=True, append_images=frames[1:])
    entry = create_entry("02.tif", out.getvalue(), archive_names.IMAGE_EXTS)
    mark_altered(entry)
    pages = expand_multipage_tiffs([entry], archive_names.IMAGE_EXTS)
    assert len(pages) == 2 and all(is_altered(p) for p in pages)


# ---------------------------------------------------------------------------
# Enregistrement
# ---------------------------------------------------------------------------

def _function(tree, name):
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _calls(node, name):
    return any(isinstance(n, ast.Call) and getattr(n.func, "id", None) == name for n in ast.walk(node))


def test_every_cbz_save_of_the_document_asks_first():
    tree = ast.parse((ROOT / "modules" / "qt" / "file_operations_qt.py").read_text(encoding="utf-8"))
    for name in ("save_as_cbz", "apply_new_names", "create_cbz_from_images", "save_selection_as_cbz"):
        assert _calls(_function(tree, name), "_confirm_altered_pages_qt"), name
    write = _function(tree, "_write_document_unlocked")
    assert _calls(write, "forget_altered_pages")


class _FakeConfirm:
    shown = []

    def __init__(self, parent, title_key, message_key, message_kwargs=None, ok_key=None,
                 plain_text=False):
        self.args = (message_key, message_kwargs, ok_key, plain_text)

    def ask_async(self, on_result):
        _FakeConfirm.shown.append(self.args)
        on_result(True)


@pytest.fixture
def confirm(monkeypatch):
    fake = types.ModuleType("modules.qt.dialogs_qt")
    fake.ConfirmDialog = _FakeConfirm
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", fake)
    _FakeConfirm.shown = []
    ns = {}
    exec(compile(ast.Module(body=_nodes("file_operations_qt.py",
                                        {"_confirm_altered_pages_qt", "_ALTERED_PAGES_LISTED"}),
                            type_ignores=[]), "file_operations_qt.py", "exec"), ns)
    return ns["_confirm_altered_pages_qt"]


def _page(name, altered):
    entry = create_entry(name, ALTERED, archive_names.IMAGE_EXTS)
    if altered:
        mark_altered(entry)
    return entry


def test_no_question_without_altered_page(confirm):
    answers = []
    confirm(None, answers.append, [_page("a.jpg", False)])
    assert answers == [True] and _FakeConfirm.shown == []


def test_question_names_the_altered_pages_in_plain_text(confirm):
    answers = []
    confirm(None, answers.append, [_page("a.jpg", False), _page("<b>x</b>.jpg", True)])
    [(key, kwargs, ok_key, plain_text)] = _FakeConfirm.shown
    assert key.endswith(".message_one") and kwargs == {"page": "<b>x</b>.jpg"}
    assert ok_key.endswith(".save_anyway") and plain_text and answers == [True]


def test_long_list_of_altered_pages_is_shortened(confirm):
    pages = [_page(f"p{i:02d}.jpg", True) for i in range(13)]
    confirm(None, lambda ok: None, pages)
    [(key, kwargs, _ok, _plain)] = _FakeConfirm.shown
    assert key.endswith(".message_many_more")
    assert kwargs["count"] == 13 and kwargs["more"] == 3
    assert kwargs["pages"].splitlines() == [f"p{i:02d}.jpg" for i in range(10)]
