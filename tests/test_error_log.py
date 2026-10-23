"""Journal des erreurs (modules/qt/error_log.py) : un seul fichier, errors.log.

- Une entrée n'est écrite que pour une erreur, avec son en-tête (date, version,
  pid, nature) ; les hooks appellent ensuite le comportement précédent (trace
  toujours affichée dans le terminal en mode développement).
- Ménage : entrées de plus de MAX_AGE_DAYS jours retirées, puis les plus
  anciennes au-delà de MAX_BYTES.
- Plusieurs process écrivent dans le même fichier sous un verrou : aucune
  entrée perdue ni mélangée (vrais process).
- Plantages natifs : un fichier de secours resté est versé dans le journal puis
  supprimé ; vide, il est seulement supprimé ; encore ouvert par son process,
  il n'est pas touché.
- Envoi par mail : erreurs récentes seulement, avec la ligne qui donne le
  chemin du journal complet.

start() n'est pas appelée ici : elle installerait les hooks et faulthandler
dans le process des tests. Ses éléments sont testés un par un.
"""
import ast
import collections
import datetime
import multiprocessing
import os
import threading
import types
from pathlib import Path

import pytest

from modules.qt import error_log

ROOT = Path(__file__).resolve().parent.parent
_REAL_TEXT = error_log._text  # avant que la fixture ne le remplace


def _error():
    try:
        raise ValueError("boum")
    except ValueError as e:
        return type(e), e, e.__traceback__


@pytest.fixture(autouse=True)
def header_texts_as_keys(monkeypatch):
    """En-têtes rendus par leur clé et leurs paramètres : les tests ne
    dépendent pas de la langue chargée. Compteur des erreurs répétées remis à
    zéro : _error() lève la même erreur dans plusieurs tests."""
    monkeypatch.setattr(error_log, "_text",
                        lambda key, **kw: key + (repr(sorted(kw.items())) if kw else ""))
    monkeypatch.setattr(error_log, "_occurrences", {})
    monkeypatch.setattr(error_log, "_trail", collections.deque(maxlen=error_log.TRAIL_SIZE))


def _entries(folder):
    return error_log.split_entries(Path(error_log.log_path(str(folder))).read_text(encoding="utf-8"))


def test_entry_has_a_header_with_date_version_pid_and_title(tmp_path):
    journal = error_log._Journal(str(tmp_path), "9.9.9")
    journal.write_error("titre traduit", *_error())
    text = Path(error_log.log_path(str(tmp_path))).read_text(encoding="utf-8")
    assert text.startswith("===== ")
    first_line = text.splitlines()[0]
    assert "MosaicView 9.9.9" in first_line
    assert f"error_log.entry.process[('pid', {os.getpid()})]" in first_line
    assert "titre traduit" in first_line and "ValueError: boum" in text


def test_no_error_writes_nothing(tmp_path):
    error_log._Journal(str(tmp_path), "9.9.9")
    assert error_log.existing_log_path(str(tmp_path)) is None
    assert error_log.read_log_text(str(tmp_path)) == ("", 0)


def test_excepthook_writes_then_calls_the_previous_hook(tmp_path):
    journal = error_log._Journal(str(tmp_path), "9.9.9")
    seen = []
    hook = error_log.make_excepthook(journal, lambda *args: seen.append(args[0]))
    hook(*_error())
    hook(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert seen == [ValueError, KeyboardInterrupt]
    text = error_log.read_log_text(str(tmp_path))[0]
    assert "error_log.entry.uncaught" in text
    assert "ValueError: boum" in text and "KeyboardInterrupt" not in text


def test_thread_excepthook_writes_then_calls_the_previous_hook(tmp_path):
    journal = error_log._Journal(str(tmp_path), "9.9.9")
    seen = []
    hook = error_log.make_thread_excepthook(journal, seen.append)
    exc_type, exc, tb = _error()
    args = types.SimpleNamespace(exc_type=exc_type, exc_value=exc, exc_traceback=tb,
                                 thread=threading.Thread(name="lot"))
    hook(args)
    assert seen == [args]
    assert "error_log.entry.uncaught_thread[('thread', 'lot')]" in error_log.read_log_text(str(tmp_path))[0]


def test_read_log_text_newest_first_and_filters(tmp_path):
    now = datetime.datetime(2026, 10, 20, 12, 0, 0)
    for days in (9, 3, 1):
        error_log.append_entry(f"erreur il y a {days} jours", "trace", folder=str(tmp_path),
                               version="9.9.9", when=now - datetime.timedelta(days=days))
    text, omitted = error_log.read_log_text(str(tmp_path))
    assert omitted == 0
    assert text.index("il y a 1 jours") < text.index("il y a 3 jours") < text.index("il y a 9 jours")
    recent, omitted = error_log.read_log_text(str(tmp_path), since=now - datetime.timedelta(days=7))
    assert omitted == 1 and "il y a 9 jours" not in recent
    one, omitted = error_log.read_log_text(str(tmp_path), max_bytes=10)
    assert omitted == 2 and "il y a 1 jours" in one


def test_prune_removes_old_entries_then_the_oldest_beyond_the_size(tmp_path, monkeypatch):
    now = datetime.datetime(2026, 10, 20, 12, 0, 0)
    error_log.append_entry("trop vieille", "x", folder=str(tmp_path), version="1",
                           when=now - datetime.timedelta(days=error_log.MAX_AGE_DAYS + 1))
    for i in range(5):
        error_log.append_entry(f"récente {i}", "y" * 200, folder=str(tmp_path), version="1",
                               when=now - datetime.timedelta(hours=5 - i))
    monkeypatch.setattr(error_log, "MAX_BYTES", 600)
    monkeypatch.setattr(error_log, "PRUNE_TARGET_BYTES", 600)
    error_log.prune(str(tmp_path), now=now)
    titles = [chunk.splitlines()[0] for _w, chunk in _entries(tmp_path)]
    assert not any("trop vieille" in t for t in titles)
    assert titles and all("récente" in t for t in titles)
    assert "récente 4" in titles[-1] and not any("récente 0" in t for t in titles)
    assert os.path.getsize(error_log.log_path(str(tmp_path))) <= 600


def test_writing_beyond_the_size_limit_prunes_the_oldest(tmp_path, monkeypatch):
    monkeypatch.setattr(error_log, "MAX_BYTES", 1500)
    monkeypatch.setattr(error_log, "PRUNE_TARGET_BYTES", 1200)
    for i in range(20):
        error_log.append_entry(f"erreur {i}", "z" * 100, folder=str(tmp_path), version="1")
    assert os.path.getsize(error_log.log_path(str(tmp_path))) <= 1500
    titles = [chunk.splitlines()[0] for _w, chunk in _entries(tmp_path)]
    assert "erreur 19" in titles[-1]


def test_threads_writing_together_lose_nothing(tmp_path):
    def write(n):
        for i in range(25):
            error_log.append_entry(f"thread {n} entrée {i}", "trace\n" * 3,
                                   folder=str(tmp_path), version="1")
    threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(_entries(tmp_path)) == 100


def _write_from_process(folder, n):
    from modules.qt import error_log as log
    for i in range(30):
        log.append_entry(f"process {n} entrée {i}", "ligne\n" * 20, folder=folder, version="1")


@pytest.mark.skipif(os.name != "nt", reason="verrou msvcrt propre à Windows")
def test_two_processes_writing_together_lose_nothing(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_write_from_process, args=(str(tmp_path), n)) for n in range(2)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    entries = _entries(tmp_path)
    assert len(entries) == 60
    for _when, chunk in entries:
        assert chunk.count("=====") == 2 and chunk.count("ligne") == 20


def test_crash_buffer_is_collected_with_its_header_then_removed(tmp_path):
    crashed = tmp_path / error_log._crash_name("pdf", 4242, "1.8.12")
    crashed.write_text("Fatal Python error: Access violation\n", encoding="utf-8")
    empty = tmp_path / error_log._crash_name("app", 4343, "1.8.12")
    empty.write_text("", encoding="utf-8")
    error_log.collect_crash_buffers(str(tmp_path))
    assert not crashed.exists() and not empty.exists()
    entries = _entries(tmp_path)
    assert len(entries) == 1
    header = entries[0][1].splitlines()[0]
    assert "error_log.entry.crash_pdf" in header and "MosaicView 1.8.12" in header
    assert "('pid', 4242)" in header
    assert "Access violation" in entries[0][1]


@pytest.mark.skipif(os.name != "nt", reason="fichier ouvert non supprimable : propre à Windows")
def test_crash_buffer_of_a_living_process_is_left_alone(tmp_path):
    path = tmp_path / error_log._crash_name("app", 4444, "1.8.12")
    with open(path, "a", encoding="utf-8") as f:
        f.write("trace en cours")
        f.flush()
        error_log.collect_crash_buffers(str(tmp_path))
        assert path.exists()
    assert error_log.existing_log_path(str(tmp_path)) is None


def test_child_crash_buffer_is_hidden_and_named_after_the_process(tmp_path, monkeypatch):
    enabled = []
    monkeypatch.setattr(error_log.faulthandler, "enable", lambda file, all_threads: enabled.append(file))
    monkeypatch.setattr(error_log.atexit, "register", lambda *a: None)
    monkeypatch.setenv(error_log._VERSION_ENV, "1.8.12")
    monkeypatch.setattr(error_log, "_crash_file", None)
    monkeypatch.setattr(error_log, "_journal", None)  # posé par start_child_crash_log
    error_log.start_child_crash_log(str(tmp_path))
    try:
        name = error_log._crash_name("pdf", os.getpid(), "1.8.12")
        assert [p.name for p in tmp_path.iterdir()] == [name]
        assert enabled == [error_log._crash_file]
        if os.name == "nt":
            import ctypes
            assert ctypes.windll.kernel32.GetFileAttributesW(str(tmp_path / name)) & 0x2
    finally:
        error_log._crash_file.close()


class _FakeLocalization:
    """Langue pIqaD : _() rend l'alphabet klingon, _wt() la version latine."""
    current_language = "tlh-piqad"
    _cache_lock = threading.Lock()
    _translations_cache = {"tlh": {"t": {"message": "latin {name}"}}}

    def get(self, key, **kwargs):
        return " {name}".format(**kwargs)

    def _load_one_translation(self, code):
        pass


class _TranslatedError(Exception):
    """Comme FileInUseError : message traduit à chaque str()."""

    def __str__(self):
        from modules.qt.localization import _
        return _("t.message", name="comic.cbz")


def test_traces_and_headers_never_use_a_fictional_alphabet(tmp_path, monkeypatch):
    from modules.qt import localization
    monkeypatch.setattr(localization, "_localization_manager", _FakeLocalization())
    monkeypatch.setattr(error_log, "_text", _REAL_TEXT)
    assert localization._("t.message", name="x") == " x"
    with localization.latin_texts():
        assert localization._("t.message", name="x") == "latin x"
    assert localization._("t.message", name="x") == " x"
    journal = error_log._Journal(str(tmp_path), "9.9.9")
    try:
        raise _TranslatedError()
    except _TranslatedError as e:
        journal.write_error(error_log._text("t.message", name="titre"), type(e), e, e.__traceback__)
    text = error_log.read_log_text(str(tmp_path))[0]
    assert "latin comic.cbz" in text and "latin titre" in text
    assert not any(0xE000 <= ord(ch) <= 0xF8FF for ch in text)


def test_session_logs_of_the_previous_format_are_removed(tmp_path):
    for name in ("errors_2026-10-01_10-00-00_123.log", "errors.log", "notes.txt"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    error_log._remove_session_logs(str(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["errors.log", "notes.txt"]


def test_mail_content_keeps_recent_errors_and_says_where_the_rest_is(tmp_path):
    source = (ROOT / "modules" / "qt" / "error_log_dialog_qt.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == "mail_content")
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) in ("MAIL_DAYS", "MAIL_MAX_BYTES")
                                                   for t in n.targets))]
    ns = {"os": os, "_wt": lambda key, **kw: f"{key} {sorted(kw.items())}"}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "error_log_dialog_qt.py", "exec"), ns)
    now = datetime.datetime(2026, 10, 20, 12, 0, 0)
    error_log.append_entry("ancienne", "a", folder=str(tmp_path), version="1",
                           when=now - datetime.timedelta(days=10))
    error_log.append_entry("récente", "b", folder=str(tmp_path), version="1",
                           when=now - datetime.timedelta(days=1))
    content = ns["mail_content"](str(tmp_path), now=now)
    assert content.startswith("error_log.mail_partial") and "('days', 7)" in content
    assert "récente" in content and "ancienne" not in content
    only_recent = tmp_path / "seul"
    error_log.append_entry("récente", "b", folder=str(only_recent), version="1",
                           when=now - datetime.timedelta(days=1))
    assert not ns["mail_content"](str(only_recent), now=now).startswith("error_log.mail_partial")
