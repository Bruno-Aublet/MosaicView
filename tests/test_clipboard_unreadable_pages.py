"""Copier / Couper une page illisible à l'ouverture (cadre rouge, sans données
en mémoire) : la page copiée est toujours la page réelle.

- archive_names.copy_zip_member_as_stored : octets d'un membre ZIP tels qu'ils
  sont dans l'archive (CRC faux compris), membre désigné par son en-tête
  (homonymes distincts) ; exception si le membre ne peut pas être décompressé.
- archive_loader : une page ZIP illisible garde la référence de son membre
  (_zip_member_reader.last_info, _remember_unread_zip_member).
- clipboard_qt : la page est copiée avec ses octets réels, et Couper retire
  toute la sélection ; si une page ne peut pas être relue (archive modifiée
  depuis l'ouverture, page sans référence de membre), rien n'est copié ni
  retiré, et l'avertissement nomme la page.

archive_loader.py et clipboard_qt.py importent PySide6 au niveau module : leurs
fonctions sont extraites par AST et exécutées seules. Le presse-papiers Windows
et la fenêtre d'avertissement sont remplacés par des faux.
"""
import ast
import io
import os
import struct
import sys
import time
import types
import warnings
import zipfile
from pathlib import Path

import pytest

from modules.qt.archive_names import IMAGE_EXTS, MAX_MEMBER_SIZE, copy_zip_member_as_stored, open_zip
from modules.qt.entries import create_entry
from modules.qt.state import AppState

from fakes import tr as _tr   # la clé et ses paramètres, pour les vérifier

ROOT = Path(__file__).resolve().parent.parent


def _extract(module, names, namespace):
    source = (ROOT / "modules" / "qt" / module).read_text(encoding="utf-8")
    nodes = [n for n in ast.parse(source).body
             if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), module, "exec"), namespace)
    return namespace


_LOADER = _extract("archive_loader.py",
                   {"_zip_member_reader", "_check_member_size", "_remember_unread_zip_member"},
                   {"os": os, "MAX_MEMBER_SIZE": MAX_MEMBER_SIZE})


STATE = AppState()
_CLIP = _extract("clipboard_qt.py",
                 {"_copy_entries_to_system_clipboard", "copy_to_system_clipboard", "cut_selected",
                  "_warn_unsafe_paths_skipped", "_copy_unread_member", "_warn_unreadable_not_copied"},
                 {"os": os, "io": io, "struct": struct, "time": time,
                  "_state_module": types.SimpleNamespace(state=STATE), "_": _tr, "_wt": _tr})
PAGE2 = bytes(range(256)) * 300


@pytest.fixture
def clipboard(monkeypatch):
    """Faux presse-papiers Windows (win32clipboard) et fausse fenêtre
    « Fichiers ignorés » (archive_loader._MessageDialog, qui importe Qt)."""
    clip = {"data": None, "messages": []}
    w32 = types.ModuleType("win32clipboard")
    w32.OpenClipboard = lambda *a: None
    w32.CloseClipboard = lambda: None
    w32.EmptyClipboard = lambda: clip.update(data=None)
    w32.SetClipboardData = lambda fmt, data: clip.update(data=data)
    w32con = types.ModuleType("win32con")
    w32con.CF_HDROP = 15
    monkeypatch.setitem(sys.modules, "win32clipboard", w32)
    monkeypatch.setitem(sys.modules, "win32con", w32con)

    class _MessageDialog:
        def __init__(self, parent, title_key, msg):
            self._msg = msg

        def show_nonmodal(self):
            clip["messages"].append(self._msg())
    fake_loader = types.ModuleType("modules.qt.archive_loader")
    fake_loader._MessageDialog = _MessageDialog
    monkeypatch.setitem(sys.modules, "modules.qt.archive_loader", fake_loader)
    return clip


def clipboard_files(clip):
    if clip["data"] is None:
        return []
    return [f for f in clip["data"][20:].decode("utf-16-le").split("\0") if f]


def _set_member_field(raw, member_index, local_offset, central_offset, value, size):
    """Remplace un champ de l'en-tête local et du répertoire central du
    membre `member_index` (données laissées intactes)."""
    for sig, offset in ((b"PK\x03\x04", local_offset), (b"PK\x01\x02", central_offset)):
        pos = -1
        for _i in range(member_index + 1):
            pos = raw.find(sig, pos + 1)
        raw[pos + offset:pos + offset + size] = value


def make_cbz(path, members, bad_crc=(), method=zipfile.ZIP_STORED):
    """CBZ dont les membres d'index `bad_crc` ont un CRC faux."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # "Duplicate name"
        with zipfile.ZipFile(path, "w", method) as zf:
            for name, data in members:
                zf.writestr(name, data)
    raw = bytearray(path.read_bytes())
    for index in bad_crc:
        _set_member_field(raw, index, 14, 16, b"\x00\x00\x00\x00", 4)
    path.write_bytes(bytes(raw))


def open_like_mosaicview(cbz):
    """Boucle de lecture de LoadWorker.load_archive (branche CBZ), avec les
    fonctions réelles du chargeur."""
    entries = []
    with open_zip(str(cbz)) as archive:
        read_member = _LOADER["_zip_member_reader"](archive)
        for name in archive.namelist():
            try:
                data = read_member(name)
                reason = None
            except Exception as e:
                data, reason = None, str(e)
            entry = create_entry(name, data, IMAGE_EXTS)
            if data is None:
                entry["corruption_reason"] = reason
                _LOADER["_remember_unread_zip_member"](entry, str(cbz), read_member)
            entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# archive_names.copy_zip_member_as_stored
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_member_with_bad_crc_is_copied_as_stored(tmp_path, method):
    cbz = tmp_path / "bd.cbz"
    make_cbz(cbz, [("p1.jpg", b"one"), ("p2.jpg", PAGE2)], bad_crc=[1], method=method)
    with zipfile.ZipFile(cbz) as zf, pytest.raises(zipfile.BadZipFile):
        zf.read("p2.jpg")
    with zipfile.ZipFile(cbz) as zf:
        offset = zf.infolist()[1].header_offset
    out = tmp_path / "p2.jpg"
    copy_zip_member_as_stored(str(cbz), offset, str(out))
    assert out.read_bytes() == PAGE2


def test_homonym_is_copied_from_its_own_member(tmp_path):
    cbz = tmp_path / "bd.cbz"
    make_cbz(cbz, [("p.jpg", b"first"), ("p.jpg", b"second")])
    with zipfile.ZipFile(cbz) as zf:
        offsets = [i.header_offset for i in zf.infolist()]
    for offset, expected in zip(offsets, (b"first", b"second")):
        out = tmp_path / "out.jpg"
        copy_zip_member_as_stored(str(cbz), offset, str(out))
        assert out.read_bytes() == expected


def test_member_with_unsupported_compression_cannot_be_copied(tmp_path):
    cbz = tmp_path / "bd.cbz"
    make_cbz(cbz, [("p.jpg", PAGE2)], method=zipfile.ZIP_DEFLATED)
    raw = bytearray(cbz.read_bytes())
    _set_member_field(raw, 0, 8, 10, struct.pack("<H", 9), 2)   # Deflate64
    cbz.write_bytes(bytes(raw))
    with pytest.raises(NotImplementedError):
        copy_zip_member_as_stored(str(cbz), 0, str(tmp_path / "out.jpg"))


# ---------------------------------------------------------------------------
# archive_loader : référence du membre d'une page illisible
# ---------------------------------------------------------------------------

def test_unreadable_page_remembers_its_member(tmp_path):
    cbz = tmp_path / "bd.cbz"
    make_cbz(cbz, [("p1.jpg", b"one"), ("p2.jpg", PAGE2)], bad_crc=[1])
    entries = open_like_mosaicview(cbz)
    assert "unread_member" not in entries[0]
    ref = entries[1]["unread_member"]
    with zipfile.ZipFile(cbz) as zf:
        assert ref["offset"] == zf.infolist()[1].header_offset
    assert ref["path"] == os.path.abspath(cbz)


def test_failed_lookup_leaves_no_stale_member(tmp_path):
    """Nom absent de l'archive : la lecture échoue avant de désigner un
    membre, la page ne reçoit pas la référence du membre lu précédemment."""
    cbz = tmp_path / "bd.cbz"
    make_cbz(cbz, [("p1.jpg", b"one")])
    with open_zip(str(cbz)) as archive:
        read = _LOADER["_zip_member_reader"](archive)
        read("p1.jpg")
        with pytest.raises(KeyError):
            read("absent.jpg")
        entry = create_entry("absent.jpg", None, IMAGE_EXTS)
        _LOADER["_remember_unread_zip_member"](entry, str(cbz), read)
    assert "unread_member" not in entry


# ---------------------------------------------------------------------------
# clipboard_qt : Copier / Couper
# ---------------------------------------------------------------------------

def _cut(tmp_path):
    _CLIP["cut_selected"](lambda: str(tmp_path / "temp"), lambda: None,
                          lambda *a, **k: None, parent=None)


def test_cut_copies_the_real_unreadable_page_and_removes_the_selection(tmp_path, clipboard):
    cbz = tmp_path / "bd.cbz"
    make_cbz(cbz, [("p1.jpg", b"one"), ("p2.jpg", PAGE2), ("p3.jpg", b"three")], bad_crc=[1])
    STATE.images_data = open_like_mosaicview(cbz)
    assert STATE.images_data[1]["bytes"] is None
    STATE.selected_indices = {0, 1, 2}
    _cut(tmp_path)
    files = clipboard_files(clipboard)
    assert [os.path.basename(f) for f in files] == ["p1.jpg", "p2.jpg", "p3.jpg"]
    assert Path(files[1]).read_bytes() == PAGE2
    assert STATE.images_data == []
    assert clipboard["messages"] == []


def test_changed_archive_refuses_cut_and_keeps_every_page(tmp_path, clipboard):
    cbz = tmp_path / "bd.cbz"
    make_cbz(cbz, [("p1.jpg", b"one"), ("p2.jpg", PAGE2)], bad_crc=[1])
    STATE.images_data = open_like_mosaicview(cbz)
    STATE.selected_indices = {0, 1}
    st = os.stat(cbz)
    os.utime(cbz, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
    _cut(tmp_path)
    assert clipboard["data"] is None
    assert [e["orig_name"] for e in STATE.images_data] == ["p1.jpg", "p2.jpg"]
    [message] = clipboard["messages"]
    assert message.startswith("messages.warnings.clipboard_unreadable.cut")
    assert "p2.jpg" in message
    assert "messages.warnings.clipboard_unreadable.source_changed" in message


def test_clipboard_in_use_is_reported_and_nothing_is_cut(tmp_path, clipboard, monkeypatch):
    """Presse-papiers tenu par un autre programme (OpenClipboard refusé) :
    message, rien de retiré, aucun dossier temporaire laissé."""
    def refuse(*args):
        raise OSError(5, "Accès refusé")
    monkeypatch.setattr(sys.modules["win32clipboard"], "OpenClipboard", refuse)
    shown = []

    class ErrorDialog:
        def __init__(self, parent, title, message, play_sound=False):
            self._message = message

        def show_nonmodal(self):
            shown.append(self._message())
    fake_dialogs = types.ModuleType("modules.qt.dialogs_qt")
    fake_dialogs.ErrorDialog = ErrorDialog
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", fake_dialogs)
    STATE.images_data = [create_entry("a.jpg", b"one", IMAGE_EXTS), create_entry("b.jpg", b"two", IMAGE_EXTS)]
    STATE.selected_indices = {0, 1}
    _cut(tmp_path)
    assert clipboard["data"] is None
    assert [e["orig_name"] for e in STATE.images_data] == ["a.jpg", "b.jpg"]
    assert shown == ["messages.errors.clipboard_failed.message [('error', 'Accès refusé')]"]
    assert os.listdir(tmp_path / "temp") == []


def test_page_without_member_reference_refuses_copy(tmp_path, clipboard):
    """Page illisible d'un CBR, CB7 ou CBT : aucune référence de membre, sa
    page réelle ne peut pas être relue."""
    unread = create_entry("x.jpg", None, IMAGE_EXTS)
    unread["corruption_reason"] = "x.jpg: Bad CRC-32"
    STATE.images_data = [create_entry("a.jpg", b"one", IMAGE_EXTS), unread]
    STATE.selected_indices = {0, 1}
    assert _CLIP["copy_to_system_clipboard"](lambda: str(tmp_path / "temp"), parent=None) is False
    assert clipboard["data"] is None
    [message] = clipboard["messages"]
    assert message.startswith("messages.warnings.clipboard_unreadable.copy")
    assert "x.jpg" in message
