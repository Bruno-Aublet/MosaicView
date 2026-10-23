"""Journal des erreurs : erreurs rattrapées loin de la fenêtre qui les affiche.

- log_exception / log_if_unexpected : la pile est écrite depuis le thread où
  l'exception existe encore ; une erreur attendue (fichier, archive abîmée,
  réseau, exception du projet marquée expected_error) ne l'est pas, un défaut
  du programme l'est.
- Secrets masqués : la clé ComicVine d'une cause chaînée n'est jamais écrite.
- sys.unraisablehook : une exception ignorée par Python est écrite ; une
  écriture demandée pendant une écriture du même thread est abandonnée, sans
  blocage.
- Process enfant (moteur PDF) : titres reçus de l'application ; une erreur
  rattrapée et l'exception qui arrête le process sont écrites (vrai process).
- Garde-fous : un except large qui envoie l'exception en texte (signal d'un
  worker, canal d'un process) écrit aussi sa pile ; plus de
  traceback.print_exc dans modules/qt (sortie d'erreur inexistante dans
  l'exe).
"""
import ast
import collections
import json
import multiprocessing
import os
import threading
import types
import zipfile
from pathlib import Path

import pytest

from modules.qt import error_log

ROOT = Path(__file__).resolve().parent.parent
QT_DIR = ROOT / "modules" / "qt"
LOG_CALLS = {"log_exception", "log_if_unexpected", "log_handled_error"}


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """Journal de ce process dans tmp_path, en-têtes rendus par leur clé,
    compteur des erreurs répétées remis à zéro."""
    monkeypatch.setattr(error_log, "_journal", error_log._Journal(str(tmp_path), "9.9.9"))
    monkeypatch.setattr(error_log, "_text", lambda key, **kw: key)
    monkeypatch.setattr(error_log, "_occurrences", {})
    monkeypatch.setattr(error_log, "_trail", collections.deque(maxlen=error_log.TRAIL_SIZE))
    return tmp_path


def _text(folder):
    return error_log.read_log_text(str(folder))[0]


def _raised(exc):
    try:
        raise exc
    except BaseException as e:
        return e


def test_log_exception_from_a_thread_writes_the_traceback(journal):
    def worker():
        try:
            {}["missing"]
        except KeyError as e:
            error_log.log_exception(e)
    t = threading.Thread(target=worker)
    t.start()
    t.join(10)
    text = _text(journal)
    assert "error_log.entry.handled" in text
    assert "KeyError" in text and "in worker" in text


class _ExpectedProjectError(Exception):
    expected_error = True


@pytest.mark.parametrize("exc", [FileNotFoundError("x"), PermissionError("x"), EOFError(),
                                 MemoryError(), zipfile.BadZipFile("x"),
                                 _ExpectedProjectError("x")])
def test_expected_errors_are_not_written(journal, exc):
    assert error_log.is_expected_error(exc)
    error_log.log_if_unexpected(_raised(exc))
    assert error_log.existing_log_path(str(journal)) is None


@pytest.mark.parametrize("exc", [KeyError("x"), TypeError("x"), AttributeError("x"),
                                 ValueError("x"), RuntimeError("x")])
def test_program_errors_are_written(journal, exc):
    assert not error_log.is_expected_error(exc)
    error_log.log_if_unexpected(_raised(exc), "error_log.entry.pdf_error")
    text = _text(journal)
    assert "error_log.entry.pdf_error" in text and type(exc).__name__ in text


def test_comicvine_network_error_is_expected():
    source = (QT_DIR / "comicvine_scraper.py").read_text(encoding="utf-8")
    cls = next(n for n in ast.walk(ast.parse(source))
               if isinstance(n, ast.ClassDef) and n.name == "ComicVineNetworkError")
    assert any(isinstance(s, ast.Assign) and ast.unparse(s) == "expected_error = True"
               for s in cls.body)


def test_api_key_of_a_chained_cause_is_masked(journal):
    try:
        try:
            raise ConnectionError("Max retries exceeded with url: "
                                  "/api/issue/?api_key=SECRET123&format=json")
        except ConnectionError as low:
            raise RuntimeError("API error") from low
    except RuntimeError as e:
        error_log.log_exception(e)
    text = _text(journal)
    assert "ConnectionError" in text
    assert "SECRET123" not in text and "api_key=***&format=json" in text


def test_without_journal_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.setattr(error_log, "_journal", None)
    error_log.log_exception(_raised(KeyError("x")))
    error_log.log_if_unexpected(_raised(KeyError("x")))
    assert error_log.existing_log_path(str(tmp_path)) is None


def test_unraisable_exception_is_written_then_passed_on(journal):
    passed = []
    hook = error_log.make_unraisablehook(error_log._journal, passed.append)
    unraisable = types.SimpleNamespace(
        exc_type=ValueError, exc_value=_raised(ValueError("dans __del__")),
        exc_traceback=None, err_msg="Exception ignored in", object="<objet>")
    unraisable.exc_traceback = unraisable.exc_value.__traceback__
    hook(unraisable)
    text = _text(journal)
    assert "error_log.entry.unraisable" in text
    assert "Exception ignored in: '<objet>'" in text and "dans __del__" in text
    assert passed == [unraisable]


def test_start_installs_the_unraisable_hook():
    source = (QT_DIR / "error_log.py").read_text(encoding="utf-8")
    start = next(n for n in ast.walk(ast.parse(source))
                 if isinstance(n, ast.FunctionDef) and n.name == "start")
    assert "sys.unraisablehook = make_unraisablehook(" in ast.unparse(start)


def test_a_write_requested_during_a_write_is_dropped_without_blocking(journal, monkeypatch):
    """Un __del__ appelé par le ramasse-miettes pendant une écriture : le
    verrou des threads n'est pas réentrant, l'écriture imbriquée attendrait
    indéfiniment."""
    real_redact = error_log._redact

    def redact_with_nested_write(text):
        error_log.append_entry("imbriquée", "corps", folder=str(journal))
        return real_redact(text)
    monkeypatch.setattr(error_log, "_redact", redact_with_nested_write)
    t = threading.Thread(target=error_log.append_entry, args=("externe", "corps"),
                         kwargs={"folder": str(journal)}, daemon=True)
    t.start()
    t.join(15)
    assert not t.is_alive()
    entries = error_log.split_entries(Path(error_log.log_path(str(journal))).read_text(encoding="utf-8"))
    assert len(entries) == 1 and "externe" in entries[0][1]
    assert not getattr(error_log._writing, "on", False)


class _FakeLocalization:
    current_language = "fr"

    def get(self, key, **kwargs):
        return f"T:{key}"


def test_titles_are_passed_to_child_processes(monkeypatch):
    from modules.qt import localization
    monkeypatch.delenv(error_log._TITLES_ENV, raising=False)
    monkeypatch.setattr(localization, "_localization_manager", _FakeLocalization())
    error_log.pass_titles_to_children()
    titles = json.loads(os.environ[error_log._TITLES_ENV])
    assert set(titles) == set(error_log._CHILD_TITLE_KEYS)
    monkeypatch.setattr(localization, "_localization_manager", None)
    assert error_log._text("error_log.entry.pdf_error") == "T:error_log.entry.pdf_error"
    assert error_log._text("error_log.entry.crash_pdf") == "error_log.entry.crash_pdf"


@error_log.logged_child_process
def _child_target(folder):
    error_log.start_child_crash_log(folder)
    try:
        {}["missing"]
    except KeyError as e:
        error_log.log_if_unexpected(e, "error_log.entry.pdf_error")
    raise TypeError("arret du process")


def test_child_process_writes_its_caught_and_fatal_errors(tmp_path, monkeypatch):
    monkeypatch.setenv(error_log._VERSION_ENV, "9.9.9")
    monkeypatch.setenv(error_log._TITLES_ENV, json.dumps({
        "error_log.entry.process": "proc {pid}",
        "error_log.entry.pdf_error": "ERREUR MOTEUR",
        "error_log.entry.pdf_uncaught": "ARRET MOTEUR"}))
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(target=_child_target, args=(str(tmp_path),))
    proc.start()
    proc.join(60)
    assert proc.exitcode == 1
    entries = error_log.split_entries(Path(error_log.log_path(str(tmp_path))).read_text(encoding="utf-8"))
    assert len(entries) == 2
    caught, fatal = (chunk for _when, chunk in entries)
    assert f"proc {proc.pid} — ERREUR MOTEUR" in caught and "KeyError" in caught
    assert "MosaicView 9.9.9" in caught
    assert f"proc {proc.pid} — ARRET MOTEUR" in fatal and "arret du process" in fatal


def _is_broad(handler):
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(n, ast.Name) and n.id in ("Exception", "BaseException") for n in names)


def _called_names(nodes):
    for node in nodes:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                f = sub.func
                yield f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)


def _sends_the_exception(handler, methods=("emit", "send", "append")):
    for node in handler.body:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                    and sub.func.attr in methods \
                    and any(isinstance(n, ast.Name) and n.id == handler.name
                            for a in sub.args for n in ast.walk(a)):
                return sub.lineno
    return None


def _logging_names(tree):
    """LOG_CALLS, plus les fonctions du module qui en appellent une (aides
    comme batch_dialogs_qt._failure_text ou archive_loader._archive_read_error)."""
    names = set(LOG_CALLS)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and LOG_CALLS & set(_called_names(node.body)):
            names.add(node.name)
    return names


# Exceptions envoyées en texte sans pile, voulues : (fichier, fonction englobante)
_NOT_LOGGED = {
    # Relevé de diagnostic des propriétés WIA, écrit dans le journal de la
    # numérisation : une propriété illisible y est attendue
    ("scan_wia.py", "_log_item_properties"),
}


def _handlers(tree):
    """(fonction englobante, except) de tout le module."""
    def visit(node, func):
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            if isinstance(child, ast.ExceptHandler):
                yield inner, child
            yield from visit(child, inner)
    yield from visit(tree, None)


def test_every_broad_except_that_passes_the_error_on_as_text_logs_it():
    """Une exception envoyée en texte (signal d'un worker, canal d'un process,
    liste d'erreurs d'un lot ou d'un chargement) perd sa pile : la fenêtre ou
    le journal du lot qui l'affiche n'en a plus que le message. Le except doit
    l'écrire lui-même (log_if_unexpected, le plus souvent), directement ou par
    une aide du module."""
    offenders = []
    for path in sorted(QT_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        logging = _logging_names(tree)
        for func, node in _handlers(tree):
            if node.name and _is_broad(node) and (path.name, func) not in _NOT_LOGGED:
                line = _sends_the_exception(node)
                if line and not logging & set(_called_names(node.body)):
                    offenders.append(f"{path.name}:{line}")
    assert not offenders, "exception transmise en texte sans sa pile au journal : " + ", ".join(offenders)


def test_the_check_sees_the_known_senders():
    """Le garde-fou doit réellement trouver les sites existants."""
    found = {"emit/send": 0, "append": 0}
    for path in sorted(QT_DIR.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ExceptHandler) and node.name and _is_broad(node):
                found["emit/send"] += bool(_sends_the_exception(node, ("emit", "send")))
                found["append"] += bool(_sends_the_exception(node, ("append",)))
    assert found["emit/send"] >= 15 and found["append"] >= 30, found


def test_pdf_process_request_errors_are_logged():
    source = (QT_DIR / "pdf_loading_qt.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    helper = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_send_request_error")
    assert "log_if_unexpected" in set(_called_names(helper.body))
    for name in ("_pdf_persistent_process",):
        func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
        assert any(ast.unparse(d) == "logged_child_process" for d in func.decorator_list)
    probe = ast.parse((QT_DIR / "pdf_probe.py").read_text(encoding="utf-8"))
    func = next(n for n in ast.walk(probe) if isinstance(n, ast.FunctionDef) and n.name == "_probe_process")
    assert any(ast.unparse(d) == "logged_child_process" for d in func.decorator_list)


def test_no_traceback_printing_outside_the_error_log():
    """traceback.print_exc écrit sur sys.stderr, qui vaut None dans l'exe :
    la pile disparaît. Passer par error_log.log_exception."""
    offenders = []
    for path in sorted(QT_DIR.glob("*.py")):
        if path.name == "error_log.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in ("print_exc", "print_exception"):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, "pile écrite sur la sortie d'erreur : " + ", ".join(offenders)


def _entries(folder):
    return error_log.split_entries(Path(error_log.log_path(str(folder))).read_text(encoding="utf-8"))


def _raise_key_error():
    {}["missing"]


def test_a_repeated_error_is_written_three_times_then_counted(journal):
    """Une erreur levée à chaque rafraîchissement ne remplit pas le journal :
    trois entrées complètes, puis une ligne courte à chaque palier."""
    for _i in range(12):
        try:
            _raise_key_error()
        except KeyError as e:
            error_log.log_exception(e)
    entries = [chunk for _when, chunk in _entries(journal)]
    assert len(entries) == 4
    assert sum("Traceback" in chunk for chunk in entries) == error_log.MAX_REPEATS
    assert "error_log.entry.repeated" in entries[-1]
    assert "KeyError" in entries[-1] and "Traceback" not in entries[-1]


def test_different_errors_are_counted_separately(journal):
    for _i in range(5):
        for exc in (KeyError("a"), TypeError("b")):
            try:
                raise exc
            except Exception as e:
                error_log.log_exception(e)
    entries = [chunk for _when, chunk in _entries(journal)]
    assert sum("KeyError" in c for c in entries) == 3 and sum("TypeError" in c for c in entries) == 3


def test_handled_error_is_written_with_the_message_of_its_window(journal):
    try:
        raise ValueError("boum")
    except ValueError as e:
        error_log.log_handled_error(e, lambda: "Titre de la fenêtre", "Impossible d'écrire Aaaa.cbz")
    text = _entries(journal)[0][1]
    assert text.index("Titre de la fenêtre") < text.index("Impossible d'écrire Aaaa.cbz") \
        < text.index("Traceback")


def test_a_huge_entry_is_shortened_and_keeps_the_previous_ones(journal):
    error_log.append_entry("avant", "corps", folder=str(journal))
    error_log.append_entry("énorme", "début" + "x" * (error_log.MAX_ENTRY_CHARS * 30) + "fin",
                           folder=str(journal))
    entries = [chunk for _when, chunk in _entries(journal)]
    assert len(entries) == 2 and "avant" in entries[0]
    assert entries[1].count("x") < error_log.MAX_ENTRY_CHARS
    assert "début" in entries[1] and "fin" in entries[1] and "[… " in entries[1]


def test_profile_and_app_folders_are_not_written(journal, monkeypatch):
    """Le nom du compte Windows (souvent le nom réel) ne part pas dans le mail."""
    home = r"C:\Users\Aaaa Bbbb"
    monkeypatch.setattr(error_log, "_path_names", None)
    monkeypatch.setattr(error_log, "_short_path", lambda path: None)
    monkeypatch.setattr(error_log.os.path, "expanduser", lambda p: home if p == "~" else p)
    monkeypatch.setattr(error_log.sys, "_MEIPASS", home + r"\AppData\Local\Temp\_MEI123", raising=False)
    body = (r'File "C:\Users\Aaaa Bbbb\AppData\Local\Temp\_MEI123\modules\qt\x.py"' + "\n"
            + "c:/users/aaaa bbbb/Documents/Cccc.cbz")
    error_log.append_entry("titre", body, folder=str(journal))
    text = _entries(journal)[0][1]
    assert "Aaaa" not in text and "aaaa" not in text
    assert r"<MosaicView>\modules\qt\x.py" in text and "%USERPROFILE%/Documents/Cccc.cbz" in text


def test_a_line_that_looks_like_a_header_does_not_create_an_entry(journal):
    error_log.append_entry("titre", "Aaaa.jpg\n===== 2020-01-01 00:00:00 — faux =====\nsuite",
                           folder=str(journal))
    entries = _entries(journal)
    assert len(entries) == 1 and " ===== 2020-01-01" in entries[0][1]


_EXPECTED_CLASSES = {
    "archive_loader.py": "AlteredMemberError", "archive_names.py": "ArchiveRefusedError",
    "entries.py": "FileTooLargeError", "scan_wia.py": "ScanError",
    "web_import_qt.py": "DownloadTooLargeError", "utils.py": "MissingPageDataError",
    "batch_dialogs_qt.py": "_IncompleteCbz", "comicvine_scraper.py": "ComicVineNetworkError",
    "pdf_unlock_qt.py": "_ProbeFailed",
}


def test_project_exceptions_for_normal_failures_are_expected():
    """Fichier refusé ou abîmé, scanner absent, réponse trop grosse : la pile
    de ces exceptions n'apprend rien, elles ne vont pas au journal."""
    for module, name in _EXPECTED_CLASSES.items():
        tree = ast.parse((QT_DIR / module).read_text(encoding="utf-8"))
        cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == name)
        assert any(isinstance(s, ast.Assign) and ast.unparse(s) == "expected_error = True"
                   for s in cls.body), f"{module}: {name}"
    from modules.qt.archive_names import ArchiveRefusedError, ArchiveToolError
    assert error_log.is_expected_error(ArchiveRefusedError("x"))
    assert error_log.is_expected_error(ArchiveToolError("x"))
    assert isinstance(ArchiveRefusedError("x"), ValueError)
    assert isinstance(ArchiveToolError("x"), RuntimeError)


def test_lock_wait_is_shorter_on_the_main_thread(tmp_path):
    """Depuis l'interface, une écriture bloquée par un autre process ne fige
    les panneaux que MAIN_THREAD_LOCK_TIMEOUT secondes."""
    assert error_log._FileLock(str(tmp_path))._timeout == error_log.MAIN_THREAD_LOCK_TIMEOUT
    seen = []
    t = threading.Thread(target=lambda: seen.append(error_log._FileLock(str(tmp_path))._timeout))
    t.start()
    t.join(10)
    assert seen == [error_log.LOCK_TIMEOUT]
    assert error_log.MAIN_THREAD_LOCK_TIMEOUT < error_log.LOCK_TIMEOUT


def test_serious_qt_messages_are_written_and_repeats_limited(journal):
    for _i in range(12):
        error_log.log_qt_message("critical", "QThread: Destroyed while thread is still running",
                                 "default")
    entries = [chunk for _when, chunk in _entries(journal)]
    assert len(entries) == error_log.MAX_REPEATS + 1
    assert "error_log.entry.qt_message" in entries[0]
    assert "Qt critical: QThread: Destroyed while thread is still running" in entries[0]
    assert "error_log.entry.repeated" in entries[-1]


def test_forced_exit_names_the_workers_still_running(journal):
    error_log.log_forced_exit([])
    assert error_log.existing_log_path(str(journal)) is None
    error_log.log_forced_exit(["modules.qt.aaaa.BbbbWorker"])
    text = _entries(journal)[0][1]
    assert "error_log.entry.forced_exit" in text and "modules.qt.aaaa.BbbbWorker" in text


def test_every_entry_gives_the_configuration_on_its_second_line(journal, monkeypatch):
    """Windows, Python, PySide6, Pillow, exe ou sources : dans chaque entrée,
    donc aussi dans le texte copié pour le mail."""
    import platform
    monkeypatch.setattr(error_log, "_text",
                        lambda key, **kw: key + " " + " ".join(f"{k}={v}" for k, v in sorted(kw.items())))
    error_log.append_entry("titre", "corps", folder=str(journal))
    lines = _entries(journal)[0][1].splitlines()
    assert lines[1].startswith("error_log.entry.environment")
    assert f"python={platform.python_version()}" in lines[1] and "program=MosaicView.py" in lines[1]
    assert f"system={platform.platform()}" in lines[1]
    assert lines[2] == "corps"


def test_main_installs_the_qt_handler_and_logs_a_forced_exit():
    tree = ast.parse((ROOT / "MosaicView.py").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    code = ast.unparse(main)
    assert code.index("_start_error_log(") < code.index("_install_qt_message_handler()")
    assert code.index("log_forced_exit(") < code.index("os._exit(")


def test_an_error_entry_gives_the_last_actions(journal, monkeypatch):
    """Ce qui se passait juste avant l'erreur : heure, panneau, action, nom du
    fichier (caractères de contrôle échappés), seulement les TRAIL_SIZE
    dernières."""
    monkeypatch.setattr(error_log, "_text",
                        lambda key, **kw: key + "".join(f" {k}={v}" for k, v in sorted(kw.items())))
    error_log.record_action("batch", count=3)
    for i in range(error_log.TRAIL_SIZE):
        error_log.record_action("open", 2, name=f"Aaaa{i}.cbz")
    error_log.record_action("save", 1, name="Bbbb\n===== faux.cbz")
    try:
        raise KeyError("x")
    except KeyError as e:
        error_log.log_exception(e)
    text = _entries(journal)[0][1]
    assert text.index("error_log.trail.title") < text.index("Traceback")
    assert "error_log.trail.batch" not in text and "Aaaa0.cbz" not in text
    assert "error_log.trail.panel number=2 — error_log.trail.open name=Aaaa19.cbz" in text
    assert r"name=Bbbb\n===== faux.cbz" in text
    assert len(_entries(journal)) == 1


def test_no_trail_without_actions_and_paths_are_described(journal):
    try:
        raise KeyError("x")
    except KeyError as e:
        error_log.log_exception(e)
    assert "error_log.trail.title" not in _entries(journal)[0][1]
    assert error_log.describe_paths([r"C:\a\Aaaa.cbz"]) == "Aaaa.cbz"
    assert error_log.describe_paths([r"C:\a\Aaaa.cbz", "b.cbr", "c.pdf"]) == "Aaaa.cbz (+2)"
    assert error_log.describe_paths([None]) == ""


def test_operations_are_recorded_with_their_panel(journal):
    from modules.qt.state import AppState
    st = AppState()
    st.panel_number = 2

    class AaaaWorker:
        pass
    token = AaaaWorker()
    st.begin_operation(token)
    st.end_operation(token)
    st.end_operation(token)  # deuxième fin : rien de plus
    actions = [(panel, key, params) for _when, panel, key, params in error_log._trail]
    assert actions == [(2, "operation_start", {"name": "AaaaWorker"}),
                       (2, "operation_end", {"name": "AaaaWorker"})]


def test_actions_are_recorded_where_they_happen():
    """Ouverture et ajout, fermeture, enregistrement, export, lot."""
    expected = {
        ("panel_widget.py", "_load_files"): "record_action(",
        ("file_close_qt.py", "force_close_file"): "record_action('close'",
        ("file_operations_qt.py", "_write_cbz_with_overlay"): "record_action('save'",
        ("file_operations_qt.py", "save_selection_to_folder"): "record_action('export'",
        ("batch_claims.py", "start"): "record_action('batch'",
    }
    for (module, func), call in expected.items():
        tree = ast.parse((QT_DIR / module).read_text(encoding="utf-8"))
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func)
        assert call in ast.unparse(node), f"{module}: {func}"


def test_times_are_given_to_the_millisecond_and_old_entries_still_read(journal):
    """En-tête et dernières actions à la milliseconde, pour comparer l'heure
    de l'erreur à celle des actions ; une entrée écrite avant, sans
    millisecondes, reste reconnue (ménage, tri, mail)."""
    import datetime as dt
    import re
    when = dt.datetime(2026, 10, 23, 8, 14, 47, 312000)
    error_log.append_entry("titre", "corps", folder=str(journal), when=when)
    error_log.record_action("batch", count=1)
    try:
        raise KeyError("x")
    except KeyError as e:
        error_log.log_exception(e)
    entries = _entries(journal)
    assert entries[0][1].startswith("===== 2026-10-23 08:14:47.312 — ")
    assert entries[0][0] == dt.datetime(2026, 10, 23, 8, 14, 47)
    assert re.search(r"^  \d\d:\d\d:\d\d\.\d{3}  error_log\.trail\.batch", entries[1][1], re.MULTILINE)
    old = "===== 2026-10-20 10:00:00 — MosaicView 1 — p — t =====\nancienne\n"
    assert [w for w, _c in error_log.split_entries(old)] == [dt.datetime(2026, 10, 20, 10, 0, 0)]
