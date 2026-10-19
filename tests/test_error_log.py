"""Journal des erreurs inattendues (modules/qt/error_log.py).

- Une erreur est écrite avec un en-tête de session, puis le hook précédent est
  appelé (la trace reste affichée dans le terminal en mode développement).
- Une session sans erreur ne laisse rien ; les journaux trop vieux ou trop
  nombreux sont supprimés ; une session trop bavarde est tronquée.

start() n'est pas appelée ici : elle installerait les hooks et faulthandler
dans le process des tests. Ses éléments sont testés un par un.
"""
import os
import threading
import time
import types

from modules.qt import error_log


def _error():
    try:
        raise ValueError("boum")
    except ValueError as e:
        return type(e), e, e.__traceback__


def session(tmp_path):
    path = tmp_path / "errors_test.log"
    return error_log._Session(str(path), open(path, "a", encoding="utf-8"), "9.9.9"), path


def test_error_is_written_with_a_session_header(tmp_path):
    s, path = session(tmp_path)
    s.write_error("exception non rattrapée", *_error())
    s.file.close()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("MosaicView 9.9.9 — session du ")
    assert "ValueError: boum" in text and "exception non rattrapée" in text


def test_excepthook_writes_then_calls_the_previous_hook(tmp_path):
    s, path = session(tmp_path)
    seen = []
    hook = error_log.make_excepthook(s, lambda *args: seen.append(args[0]))
    hook(*_error())
    hook(KeyboardInterrupt, KeyboardInterrupt(), None)
    s.file.close()
    assert seen == [ValueError, KeyboardInterrupt]
    text = path.read_text(encoding="utf-8")
    assert "ValueError: boum" in text and "KeyboardInterrupt" not in text


def test_thread_excepthook_writes_then_calls_the_previous_hook(tmp_path):
    s, path = session(tmp_path)
    seen = []
    hook = error_log.make_thread_excepthook(s, seen.append)
    exc_type, exc, tb = _error()
    args = types.SimpleNamespace(exc_type=exc_type, exc_value=exc, exc_traceback=tb,
                                 thread=threading.Thread(name="lot"))
    hook(args)
    s.file.close()
    assert seen == [args]
    assert "dans le thread lot" in path.read_text(encoding="utf-8")


def test_session_is_truncated_beyond_its_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(error_log, "MAX_SESSION_BYTES", 2000)
    s, path = session(tmp_path)
    for _i in range(50):
        s.write_error("exception non rattrapée", *_error())
    s.file.close()
    text = path.read_text(encoding="utf-8")
    assert "[journal tronqué" in text
    assert len(text.encode("utf-8")) < 4000


def test_prune_removes_empty_old_and_extra_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(error_log, "MAX_FILES", 3)
    now = time.time()

    def make(name, age_days, content="x"):
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        os.utime(p, (now - age_days * 86400, now - age_days * 86400))
        return p
    make("errors_empty.log", 1, content="")
    make("errors_old.log", error_log.MAX_AGE_DAYS + 1)
    for i in range(4):
        make(f"errors_recent{i}.log", i)
    current = make("errors_current.log", 0, content="")
    other = make("notes.txt", 30)
    error_log.prune(str(tmp_path), keep=str(current), now=now)
    remaining = sorted(p.name for p in tmp_path.iterdir())
    # Vide et trop vieux supprimés ; 3 fichiers au plus, session en cours comprise
    assert remaining == ["errors_current.log", "errors_recent0.log", "errors_recent1.log", "notes.txt"]
    assert other.exists()


def test_latest_error_log_skips_empty_logs_and_other_files(tmp_path):
    now = time.time()

    def make(name, age_days, content="x"):
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        os.utime(p, (now - age_days * 86400, now - age_days * 86400))
        return p
    assert error_log.latest_error_log(str(tmp_path)) is None
    make("errors_older.log", 3)
    newest = make("errors_newer.log", 1)
    make("errors_current.log", 0, content="")   # session en cours, sans erreur
    make("notes.log", 0)                        # pas un journal de MosaicView
    assert error_log.latest_error_log(str(tmp_path)) == str(newest)
    assert error_log.latest_error_log(str(tmp_path / "absent")) is None


def test_all_error_logs_text_joins_logs_newest_first(tmp_path):
    now = time.time()

    def make(name, age_days, content):
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        os.utime(p, (now - age_days * 86400, now - age_days * 86400))
        return p
    assert error_log.all_error_logs_text(str(tmp_path)) == ""
    make("errors_a.log", 3, "session ancienne\n")
    make("errors_b.log", 1, "session récente\n")
    make("errors_c.log", 0, "")                 # session en cours, sans erreur
    text = error_log.all_error_logs_text(str(tmp_path))
    assert text.index("session récente") < text.index("session ancienne")
    assert error_log.error_logs(str(tmp_path)) == [str(tmp_path / "errors_b.log"),
                                                    str(tmp_path / "errors_a.log")]
