"""Copie des pages d'une archive CBR/CB7/CBT vers un CBZ lors des conversions en lot.

Une page illisible ne doit jamais passer inaperçue : l'archive source est
supprimée après conversion, uniquement si aucune page n'a échoué.

batch_dialogs_qt.py et archive_loader.py importent PySide6 au niveau module :
les fonctions sont extraites par AST et exécutées seules, sans aucun import Qt.
"""
import ast
import gc
import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SEVEN_ZIP = ROOT / "7zip" / "7z.exe"


def _load(module, names, ns):
    source = (ROOT / "modules" / "qt" / module).read_text(encoding="utf-8")
    nodes = [n for n in ast.parse(source).body
             if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), module, "exec"), ns)
    return ns


_ns = _load("batch_dialogs_qt.py",
            ("_unique_flat_name", "_copy_pages_to_cbz", "_discard_incomplete_cbz"),
            {"os": os, "io": io, "gc": gc, "Image": Image})
_copy_pages_to_cbz = _ns["_copy_pages_to_cbz"]
_discard_incomplete_cbz = _ns["_discard_incomplete_cbz"]


def jpg_bytes(color):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="JPEG")
    return buf.getvalue()


PAGES = {"p01.jpg": jpg_bytes((255, 0, 0)),
         "p02.jpg": jpg_bytes((0, 255, 0)),
         "p03.jpg": jpg_bytes((0, 0, 255))}


def copy(tmp_path, read_page, pages=tuple(PAGES)):
    out = tmp_path / "out.cbz"
    progress = []
    with zipfile.ZipFile(out, "w") as cbz:
        failures = _copy_pages_to_cbz(cbz, list(pages), read_page, lambda p: p,
                                      lambda cur, tot: progress.append((cur, tot)))
    with zipfile.ZipFile(out) as z:
        written = z.namelist()
    return failures, written, progress


def test_all_pages_copied(tmp_path):
    failures, written, progress = copy(tmp_path, PAGES.__getitem__)
    assert failures == []
    assert written == list(PAGES)
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_unreadable_page_is_reported_and_others_copied(tmp_path):
    def read_page(name):
        if name == "p02.jpg":
            raise RuntimeError("CRC error")
        return PAGES[name]

    failures, written, _progress = copy(tmp_path, read_page)
    assert failures == [("p02.jpg", "CRC error")]
    assert written == ["p01.jpg", "p03.jpg"]


def test_every_page_unreadable(tmp_path):
    def read_page(name):
        raise RuntimeError("Wrong password")

    failures, written, progress = copy(tmp_path, read_page)
    assert [name for name, _err in failures] == list(PAGES)
    assert written == []
    assert progress == []


def test_failed_write_is_reported(tmp_path):
    class FullDisk:
        def writestr(self, *args, **kwargs):
            raise OSError(28, "No space left on device")

    failures = _copy_pages_to_cbz(FullDisk(), list(PAGES), PAGES.__getitem__, lambda p: p)
    assert len(failures) == 3


def test_exception_without_message_is_named(tmp_path):
    def read_page(name):
        raise ValueError()

    failures, _written, _progress = copy(tmp_path, read_page, pages=("p01.jpg",))
    assert failures == [("p01.jpg", "ValueError")]


def test_application_exit_is_not_swallowed(tmp_path):
    class Interrupted(BaseException):
        pass

    def read_page(name):
        raise Interrupted()

    with pytest.raises(Interrupted):
        copy(tmp_path, read_page)


def test_discard_removes_incomplete_cbz_and_logs(tmp_path):
    out = tmp_path / "out.cbz"
    out.write_bytes(b"PK")
    errors = []
    _discard_incomplete_cbz(str(out), "comic.cb7", [("p02.jpg", "CRC error")], errors)
    assert not out.exists()
    assert len(errors) == 1
    assert "comic.cb7" in errors[0] and "p02.jpg" in errors[0] and "CRC error" in errors[0]


def test_discard_tolerates_missing_cbz(tmp_path):
    errors = []
    _discard_incomplete_cbz(str(tmp_path / "absent.cbz"), "c.cbr", [("p.jpg", "x")], errors)
    assert len(errors) == 1


@pytest.mark.skipif(sys.platform != "win32" or not SEVEN_ZIP.exists(),
                    reason="7z.exe embarqué requis (Windows)")
def test_password_protected_cb7_reports_every_page(tmp_path):
    """CB7 chiffré sans chiffrement des noms : la liste des pages est lisible,
    mais aucune page ne s'extrait. Avant correction, un CBZ vide était écrit et
    le CB7 d'origine supprimé."""
    class _NoConsoleSubprocess:
        """subprocess dont run() n'hérite pas de la console : sans cela, 7z
        attendrait la saisie du mot de passe si pytest tourne avec -s."""
        def __getattr__(self, name):
            return getattr(subprocess, name)

        @staticmethod
        def run(*args, **kwargs):
            kwargs.setdefault("stdin", subprocess.DEVNULL)
            return subprocess.run(*args, **kwargs)

    ns = _load("archive_loader.py", ("_to_short_path", "_list_7z_files", "_read_7z_file"),
               {"os": os, "sys": sys, "subprocess": _NoConsoleSubprocess(),
                "_get_7z_exe": lambda: str(SEVEN_ZIP)})
    src = tmp_path / "src"
    src.mkdir()
    for name, data in PAGES.items():
        (src / name).write_bytes(data)
    cb7 = tmp_path / "locked.cb7"
    subprocess.run([str(SEVEN_ZIP), "a", "-psecret", str(cb7), *PAGES],
                   cwd=src, check=True, capture_output=True, stdin=subprocess.DEVNULL)

    names = ns["_list_7z_files"](str(cb7))
    assert sorted(names) == sorted(PAGES)

    failures, written, _progress = copy(
        tmp_path, lambda f: ns["_read_7z_file"](str(cb7), f), pages=sorted(names))
    assert len(failures) == len(PAGES)
    assert written == []
