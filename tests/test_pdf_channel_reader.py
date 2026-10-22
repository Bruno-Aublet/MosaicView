"""Lecture commune du canal d'un process PDF (Audit 7, P4-2).

pdf_loading_qt.read_pdf_message est le seul lecteur du canal pour le
chargement, la fusion, le lot PDF → CBZ et l'import des métadonnées : message
suivant, None (rien dans le délai, process vivant) ou PDF_STOPPED_MESSAGE
(process mort : canal fermé, poll() ou recv() qui lèvent). Les messages
envoyés avant la mort sont lus d'abord.

pdf_loading_qt importe Qt : la fonction est extraite par AST. Un vrai canal
multiprocessing est aussi éprouvé.
"""
import ast
import multiprocessing
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
QT = ROOT / "modules" / "qt"


def _load():
    tree = ast.parse((QT / "pdf_loading_qt.py").read_text(encoding="utf-8"))
    nodes = [n for n in tree.body
             if (isinstance(n, ast.FunctionDef) and n.name in ("read_pdf_message", "_stopped"))
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "PDF_STOPPED_MESSAGE"
                                                   for t in n.targets))]
    assert len(nodes) == 3
    ns = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "pdf_loading_qt.py", "exec"), ns)
    return ns["read_pdf_message"], ns["PDF_STOPPED_MESSAGE"]


read_pdf_message, STOPPED = _load()
ALIVE = types.SimpleNamespace(is_alive=lambda: True)
COLLECTED = []


@pytest.fixture(autouse=True)
def no_real_error_log(monkeypatch):
    """Un process vu mort fait ramasser ses traces de plantage : ici sur un
    faux, jamais dans le vrai dossier des journaux."""
    from modules.qt import error_log
    COLLECTED.clear()
    monkeypatch.setattr(error_log, "collect_crash_buffers", lambda *a: COLLECTED.append(True))
DEAD = types.SimpleNamespace(is_alive=lambda: False)


class _Conn:
    def __init__(self, messages=(), poll_error=None, recv_error=None):
        self.messages = list(messages)
        self.poll_error, self.recv_error = poll_error, recv_error

    def poll(self, timeout=0):
        if self.poll_error:
            raise self.poll_error
        return bool(self.messages) or self.recv_error is not None

    def recv(self):
        if self.messages:
            return self.messages.pop(0)
        raise self.recv_error


def test_message_is_returned():
    assert read_pdf_message(_Conn([("total", 3, False)]), ALIVE, 0.01) == ("total", 3, False)


def test_nothing_yet_from_a_living_process_gives_none():
    assert read_pdf_message(_Conn(), ALIVE, 0.01) is None


@pytest.mark.parametrize("conn", [
    _Conn(recv_error=EOFError()),                 # canal fermé : poll() vrai, recv() lève
    _Conn(poll_error=BrokenPipeError()),          # canal déjà rompu : poll() lève
    _Conn(recv_error=ValueError("illisible")),    # message impossible à relire
])
def test_closed_or_broken_channel_means_stopped(conn):
    assert read_pdf_message(conn, ALIVE, 0.01) is STOPPED


def test_dead_process_without_message_means_stopped():
    assert read_pdf_message(_Conn(), DEAD, 0.01) is STOPPED
    assert read_pdf_message(_Conn(), None, 0.01) is STOPPED


def test_dead_process_crash_trace_is_collected_at_once():
    read_pdf_message(_Conn(recv_error=EOFError()), ALIVE, 0.01)
    assert COLLECTED == [True]
    COLLECTED.clear()
    assert read_pdf_message(_Conn([("done",)]), ALIVE, 0.01) == ("done",)
    assert COLLECTED == []


def test_messages_sent_before_death_are_read_first():
    conn = _Conn([("merge_page", "p1.jpg", b"x", 150), ("done",)], recv_error=EOFError())
    got = [read_pdf_message(conn, DEAD, 0.01) for _i in range(3)]
    assert got == [("merge_page", "p1.jpg", b"x", 150), ("done",), STOPPED]


def _send_then_exit(conn):
    conn.send(("done",))
    conn.close()


def test_real_pipe_of_a_process_that_exits():
    ctx = multiprocessing.get_context("spawn")
    recv_end, send_end = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_send_then_exit, args=(send_end,))
    proc.start()
    send_end.close()
    try:
        assert read_pdf_message(recv_end, proc, 30) == ("done",)
        proc.join(30)
        assert read_pdf_message(recv_end, proc, 0.5) is STOPPED
    finally:
        proc.join(5)
        recv_end.close()


def test_every_pdf_channel_reader_uses_the_common_function():
    """Aucun poll()/recv() direct sur le canal d'un process PDF hors de
    read_pdf_message (PdfProbe, modèle à statuts explicites, et la lecture
    préalable de l'ouverture, _drain_preopen, sont à part)."""
    allowed = {("pdf_loading_qt.py", "read_pdf_message"), ("pdf_loading_qt.py", "_drain_preopen"),
               ("pdf_probe.py", "call")}
    bad = []
    for path in QT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for node in ast.walk(func):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("poll", "recv")
                        and (path.name, func.name) not in allowed
                        and not any(isinstance(inner, ast.FunctionDef) and inner is not func
                                    and node in ast.walk(inner) for inner in ast.walk(func))):
                    bad.append(f"{path.name}: {func.name} → .{node.func.attr}()")
    assert not bad, "\n".join(sorted(set(bad)))
