"""Erreurs rendues visibles et copies protégées (Audit 7, P3).

- temp_files.exception_text : une exception sans message (EOFError,
  MemoryError) donne le nom de son type, jamais une ligne vide.
- clipboard_qt.paste_from_system_clipboard : le presse-papiers est refermé
  même si sa lecture échoue (resté ouvert, il bloquerait tout Windows).
- config_manager : un échec d'écriture de la configuration est transmis à
  save_failed_handler ; une clé API impossible à chiffrer n'est pas enregistrée
  vide. Fenêtre de la clé API : l'échec reste affiché, la fenêtre ouverte, et
  le message est rejoué au changement de langue. Fenêtre principale : un seul
  avertissement par session.
- batch_dialogs_qt._log_label_text : un journal de lot impossible à écrire est
  signalé dans le résumé, avec la raison échappée.
- batch_metadata_dialog_qt._CancellableZip : nombre de fichiers écrits, attendu
  par le contrôle du CBZ avant remplacement.
- utils : ReplaceFileW qui retire la destination sans que le temporaire puisse
  la remplacer → ReplacementPendingError, temporaire gardé (seule copie).
- error_log : trace d'une erreur signalée à l'utilisateur ; appels présents
  dans ErrorDialog et les deux process PDF (le journal lui-même :
  test_error_log.py).

Les modules Qt sont lus par AST, leurs fonctions exécutées seules, sans Qt.
"""
import ast
import ctypes
import html
import io
import os
import sys
import time
import types
import zipfile
from pathlib import Path

import pytest

from modules.qt import config_manager, error_log, utils
from modules.qt.archive_names import IMAGE_EXTS
from modules.qt.temp_files import exception_text

from fakes import tr as _tr

ROOT = Path(__file__).resolve().parent.parent


def _functions(module, names, namespace, class_name=None):
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
    body = tree.body
    if class_name:
        body = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name).body
    nodes = [n for n in body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    if class_name:
        nodes = [ast.ClassDef(name=class_name, bases=[], keywords=[], decorator_list=[], body=nodes)]
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), module, "exec"),
         namespace)
    return namespace


# ---------------------------------------------------------------------------
# Messages d'erreur jamais vides


def test_exception_text_names_the_type_of_an_empty_exception():
    assert exception_text(EOFError()) == "EOFError"
    assert exception_text(MemoryError()) == "MemoryError"
    assert exception_text(ValueError("page abîmée")) == "page abîmée"


# ---------------------------------------------------------------------------
# Collage : presse-papiers toujours refermé


class _FakeMsgDialog:
    shown = []

    def __init__(self, parent, title_key, message_key, params=None):
        self.args = (title_key, message_key, params)

    def show_nonmodal(self):
        _FakeMsgDialog.shown.append(self.args)


def _paste_namespace():
    return _functions("modules/qt/clipboard_qt.py", {"paste_from_system_clipboard"}, {
        "io": io, "IMAGE_EXTS": IMAGE_EXTS, "MsgDialog": _FakeMsgDialog,
        "_state_module": types.SimpleNamespace(state=types.SimpleNamespace(images_data=[])),
        "_": _tr, "_wt": _tr})


@pytest.fixture
def win32(monkeypatch):
    calls = []
    w32 = types.ModuleType("win32clipboard")
    w32.OpenClipboard = lambda *a: calls.append("open")
    w32.CloseClipboard = lambda: calls.append("close")
    w32con = types.ModuleType("win32con")
    w32con.CF_HDROP, w32con.CF_DIB = 15, 8
    monkeypatch.setitem(sys.modules, "win32clipboard", w32)
    monkeypatch.setitem(sys.modules, "win32con", w32con)
    _FakeMsgDialog.shown = []
    return w32, calls


def test_paste_closes_the_clipboard_when_reading_it_fails(win32):
    w32, calls = win32

    def broken(_fmt):
        raise RuntimeError()
    w32.IsClipboardFormatAvailable = broken
    _paste_namespace()["paste_from_system_clipboard"](
        None, lambda files: None, lambda: None, lambda: None, lambda: None, str)
    assert calls == ["open", "close"]
    assert _FakeMsgDialog.shown == [("messages.errors.paste_failed.title",
                                     "messages.errors.paste_failed.message",
                                     {"error": "RuntimeError"})]


def test_paste_of_files_closes_the_clipboard_before_loading_them(win32):
    w32, calls = win32
    w32.IsClipboardFormatAvailable = lambda fmt: fmt == 15
    w32.GetClipboardData = lambda fmt: ("C:/a.jpg",)
    loaded = []

    def load(files):
        loaded.append((list(files), list(calls)))
    _paste_namespace()["paste_from_system_clipboard"](
        None, load, lambda: None, lambda: None, lambda: None, str)
    assert loaded == [(["C:/a.jpg"], ["open", "close"])]


# ---------------------------------------------------------------------------
# Configuration impossible à écrire


def test_failed_config_save_is_reported_to_the_handler(tmp_path, monkeypatch):
    cfg = config_manager.ConfigManager(str(tmp_path))
    received = []
    monkeypatch.setattr(cfg, "save_failed_handler", received.append)

    def disk_full(_path, _data):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(config_manager, "write_json_atomically", disk_full)
    assert cfg.set_bookmark(str(tmp_path / "a.cbz"), 3) is False
    assert len(received) == 1 and received[0].errno == 28


def test_api_key_that_cannot_be_encrypted_is_not_saved_empty(tmp_path, monkeypatch):
    cfg = config_manager.ConfigManager(str(tmp_path))
    cfg.config["comicvine_api_key"] = "ancienne"
    monkeypatch.setattr(config_manager, "_dpapi_encrypt", lambda _key: "")
    assert cfg.set_comicvine_api_key("nouvelle") is False
    assert cfg.config["comicvine_api_key"] == "ancienne"


class _Label:
    def __init__(self):
        self.text, self.visible = "", False

    def setText(self, text):
        self.text = text

    def clear(self):
        self.text = ""

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


def _api_key_dialog(key, saved):
    ns = _functions("modules/qt/comicvine_apikey_dialog_qt.py",
                    {"_set_error", "_on_validate", "_on_clear"}, {"_": _tr}, "_ApiKeyDialog")
    dlg = ns["_ApiKeyDialog"]()
    dlg._error_label, dlg._error_text_fn, dlg.result_key = _Label(), None, None
    dlg._key_input = types.SimpleNamespace(text=lambda: key, clear=lambda: None)
    dlg._config = types.SimpleNamespace(set_comicvine_api_key=lambda k: saved)
    dlg.accepted = []
    dlg.accept = lambda: dlg.accepted.append(True)
    return dlg


def test_api_key_dialog_stays_open_when_the_key_cannot_be_saved():
    dlg = _api_key_dialog("abc", saved=False)
    dlg._on_validate()
    assert dlg.accepted == [] and dlg.result_key is None
    assert dlg._error_label.visible
    assert dlg._error_label.text == "comicvine.api_key_dialog.error_save"
    assert dlg._error_text_fn() == "comicvine.api_key_dialog.error_save"


def test_api_key_dialog_empty_key_message_is_kept_for_retranslation():
    dlg = _api_key_dialog("  ", saved=True)
    dlg._on_validate()
    assert dlg.accepted == []
    assert dlg._error_text_fn() == "comicvine.api_key_dialog.error_empty"
    tree = ast.parse((ROOT / "modules/qt/comicvine_apikey_dialog_qt.py").read_text(encoding="utf-8"))
    retranslate = next(n for n in ast.walk(tree)
                       if isinstance(n, ast.FunctionDef) and n.name == "_retranslate")
    assert "self._error_text_fn()" in ast.unparse(retranslate)


def test_api_key_dialog_accepts_a_saved_key_and_clears_the_error():
    dlg = _api_key_dialog("abc", saved=True)
    dlg._on_clear()
    assert dlg._error_text_fn is None and not dlg._error_label.visible
    dlg._on_validate()
    assert dlg.accepted == [True] and dlg.result_key == "abc"


def test_config_save_failure_is_shown_once_per_session(monkeypatch):
    shown = []

    class FakeErrorDialog:
        def __init__(self, parent, title, message, **kw):
            self.message = message

        def show_nonmodal(self):
            shown.append(self.message())
    fake = types.ModuleType("modules.qt.dialogs_qt")
    fake.ErrorDialog = FakeErrorDialog
    monkeypatch.setitem(sys.modules, "modules.qt.dialogs_qt", fake)
    ns = _functions("MosaicView.py", {"_on_config_save_failed"}, {"_": _tr}, "MainWindow")
    win = ns["MainWindow"]()
    win._config_save_warned, win._active_panel = False, None
    win._on_config_save_failed("disque plein")
    win._on_config_save_failed("disque plein")
    assert shown == ["messages.errors.config_save_failed.message [('error', 'disque plein')]"]


# ---------------------------------------------------------------------------
# Journal de lot impossible à écrire


def _log_label_text():
    return _functions("modules/qt/batch_dialogs_qt.py", {"_log_label_text"},
                      {"_": _tr, "html": html})["_log_label_text"]


def test_log_label_is_a_link_when_the_log_was_written():
    text = _log_label_text()({"log_path": "C:/t/log.txt"}, "2 erreurs — Voir le journal", "2 erreurs")
    assert text == '<a href="#">2 erreurs — Voir le journal</a>'


def test_log_label_gives_the_reason_when_the_log_could_not_be_written():
    text = _log_label_text()({"log_path": None, "log_error": "C:/A&B plein"}, "lien", "2 erreurs")
    assert text.startswith("<div>2 erreurs<br>") and text.endswith("</div>")
    assert "C:/A&amp;B plein" in text and "file:///" not in text


def test_log_label_without_log_shows_only_the_error_count():
    assert _log_label_text()({"log_path": None}, "lien", "2 erreurs") == "2 erreurs"
    assert _log_label_text()({"log_path": None}, "lien") == ""


def test_metadata_summary_reports_an_unwritten_log():
    source = (ROOT / "modules/qt/batch_metadata_dialog_qt.py").read_text(encoding="utf-8")
    assert '"log_error": log_error' in source
    assert 'dialogs.batch_log_failed' in source


# ---------------------------------------------------------------------------
# Contrôle du CBZ avant remplacement (import des métadonnées)


def test_cancellable_zip_counts_written_files_but_not_folders(tmp_path):
    ns = _functions("modules/qt/batch_metadata_dialog_qt.py", {"_CancellableZip"}, {})
    path = tmp_path / "out.cbz"
    with zipfile.ZipFile(path, "w") as raw:
        zf = ns["_CancellableZip"](raw, lambda: None)
        zf.writestr("dossier/", b"")
        zf.writestr("dossier/p1.jpg", b"1")
        zf.writestr(zipfile.ZipInfo("p2.jpg"), b"2")
    assert zf.written_files == 2
    utils.check_written_cbz_structure(str(path), zf.written_files)


# ---------------------------------------------------------------------------
# ReplaceFileW qui retire la destination : temporaire gardé


@pytest.mark.skipif(os.name != "nt", reason="ReplaceFileW propre à Windows")
def test_temp_is_kept_when_it_became_the_only_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "_kept_temp_files", set())
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    dest = tmp_path / "comic.cbz"
    dest.write_bytes(b"OLD")

    def replace_file_w(dest_path, src_path, *_args):
        os.remove(dest_path)  # destination retirée, remplacement inachevé
        return 0
    kernel32 = types.SimpleNamespace(ReplaceFileW=replace_file_w)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: kernel32)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 1176)
    real_replace = os.replace

    def locked(src, dst):
        if str(dst) == str(dest):
            raise PermissionError(32, "fichier verrouillé")
        real_replace(src, dst)
    monkeypatch.setattr(os, "replace", locked)

    with pytest.raises(utils.ReplacementPendingError) as info:
        utils.replace_file_atomically(str(dest), lambda tmp: Path(tmp).write_bytes(b"NEW"))
    kept = info.value.src
    assert not dest.exists()
    assert Path(kept).read_bytes() == b"NEW"
    assert utils.is_kept_temp(kept)
    assert "comic.cbz" in str(info.value) or "replace_pending" in str(info.value)

    old = time.time() - 2 * utils.STALE_TEMP_AGE
    os.utime(kept, (old, old))
    utils.remove_stale_temp_files(str(tmp_path))
    assert Path(kept).exists()


def test_ordinary_temp_is_still_removed_after_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "_kept_temp_files", set())
    seen = []

    def failing_write(tmp):
        seen.append(tmp)
        raise OSError("disque plein")
    with pytest.raises(OSError):
        utils.replace_file_atomically(str(tmp_path / "comic.cbz"), failing_write)
    assert not os.path.exists(seen[0])


# ---------------------------------------------------------------------------
# Journal des erreurs : erreurs signalées et process enfant


def test_handled_error_is_written_with_its_traceback(tmp_path, monkeypatch):
    monkeypatch.setattr(error_log, "_journal", error_log._Journal(str(tmp_path), "9.9.9"))
    monkeypatch.setattr(error_log, "_text", lambda key, **kw: key)
    try:
        raise ValueError("écriture impossible")
    except ValueError as e:
        error_log.log_handled_error(e)
    text = error_log.read_log_text(str(tmp_path))[0]
    assert "error_log.entry.handled" in text
    assert "ValueError: écriture impossible" in text and "Traceback" in text


def test_handled_error_without_journal_or_exception_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(error_log, "_journal", None)
    error_log.log_handled_error(ValueError("x"))
    monkeypatch.setattr(error_log, "_journal", error_log._Journal(str(tmp_path), "9.9.9"))
    error_log.log_handled_error(None)
    assert error_log.existing_log_path(str(tmp_path)) is None


def test_error_dialog_logs_the_exception_being_handled():
    source = (ROOT / "modules/qt/dialogs_qt.py").read_text(encoding="utf-8")
    cls = next(n for n in ast.parse(source).body
               if isinstance(n, ast.ClassDef) and n.name == "ErrorDialog")
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    assert "log_handled_error(sys.exc_info()[1], title, message)" in ast.unparse(init)


def test_pdf_child_processes_open_their_crash_log():
    for module, func in (("modules/qt/pdf_loading_qt.py", "_pdf_persistent_process"),
                         ("modules/qt/pdf_probe.py", "_probe_process")):
        tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == func)
        assert "start_child_crash_log()" in ast.unparse(node), func
