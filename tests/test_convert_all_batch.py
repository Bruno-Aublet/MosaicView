"""Conversion complète en CBZ (batch_dialogs_qt, « Conversions et
compression des BD ») : vérification des extensions, puis lots CBR, CB7,
CBT, PDF et images isolées → CBZ, puis recompression des CBZ, enchaînés,
avec une seule confirmation, un seul journal et un seul résumé.

- fichiers de chaque étape, relus sur le disque avant de la lancer (un
  fichier renommé par l'étape « extensions » passe par le bon lot) ;
- ordre des étapes, étape sans fichier sautée, mode « un seul CBZ » des
  images : un CBZ par dossier déposé ;
- seule l'annulation par l'utilisateur arrête la suite ; un lot refusé,
  arrêté faute de place ou qui ne peut pas tourner (module absent) laisse
  les étapes suivantes se faire, et le problème est signalé à la fin ;
  les fichiers déjà pris par un autre lot sont écartés et signalés ;
- étape « extensions » : renommage sans question, rien de renommé si elle
  est annulée ;
- journal unique : compteurs de l'ensemble, fichier renommé puis converti
  compté une fois ;
- fenêtre de dépôt d'un dossier : chaque choix mène à un traitement.

Les lots eux-mêmes (own_log=False) sont testés par test_batch_loops.py.
batch_dialogs_qt.py importe PySide6 : ses fonctions sont extraites par
tests/ast_loader.py, les lots remplacés par des faux.
"""
import ast
import os
import types
import zipfile
from pathlib import Path

import pytest

import ast_loader
from fakes import tr as _tr
from modules.qt.archive_names import IMAGE_EXTS, natural_key
from modules.qt.batch_claims import BatchClaims, files_in_running_batch
from modules.qt.misnamed_archives import check_extension
from modules.qt.utils import walk_without_links

MODULE = "batch_dialogs_qt.py"
ROOT = Path(__file__).resolve().parent.parent
FAKE_IMG_MODE = types.SimpleNamespace(MODE_ONE_PER_IMAGE="one_per_image",
                                      MODE_ALL_IN_ONE="all_in_one")
CALLBACKS = {"natural_sort_key": natural_key, "state": object()}


def touch(folder, *names):
    for name in names:
        path = Path(folder) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


def make_zip(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("p01.jpg", b"A")
    return str(path)


# ---------------------------------------------------------------------------
# Fichiers de chaque étape
# ---------------------------------------------------------------------------

def load_files():
    return ast_loader.load(MODULE, {"convert_all_files", "_CONVERT_ALL_EXTENSIONS",
                                    "CONVERT_ALL_KINDS"},
                           os=os, walk_without_links=walk_without_links,
                           IMAGE_EXTS=IMAGE_EXTS)["convert_all_files"]


def test_each_lot_gets_its_files_in_subfolders(tmp_path):
    touch(tmp_path, "b.cbr", "a.CBR", "sous/c.cb7", "d.cbt", "e.PDF", "p01.jpg",
          "sous/p02.png", "f.cbz", "g.zip", "h.epub", "notes.txt")
    found = load_files()([str(tmp_path)], natural_key)
    names = {kind: [os.path.basename(f) for f in files] for kind, files in found.items()}
    assert names == {"cbr": ["a.CBR", "b.cbr"], "cb7": ["c.cb7"], "cbt": ["d.cbt"],
                     "pdf": ["e.PDF"], "img": ["p01.jpg", "p02.png"]}


# ---------------------------------------------------------------------------
# Étapes et enchaînement
# ---------------------------------------------------------------------------

def test_steps_check_extensions_first_then_each_lot():
    steps = ast_loader.load(MODULE, {"_convert_all_steps"},
                            _ImgModeDialog=FAKE_IMG_MODE)["_convert_all_steps"]
    assert steps(["A", "B"], FAKE_IMG_MODE.MODE_ONE_PER_IMAGE) == [
        ("extensions", None), ("cbr", None), ("cb7", None), ("cbt", None), ("pdf", None),
        ("img", None), ("recompress", None)]
    assert steps(["A"], None)[-2:] == [("img", None), ("recompress", None)]
    # Toutes les images dans un seul CBZ : un CBZ par dossier déposé
    assert steps(["A", "B"], FAKE_IMG_MODE.MODE_ALL_IN_ONE)[-3:] == [
        ("img_single", "A"), ("img_single", "B"), ("recompress", None)]


class Chain:
    """Conversion complète exécutée avec de faux lots : chaque étape lancée
    est notée avec ses fichiers, et sa fin rendue tout de suite par
    `outcome(type, fichiers)` → (nouveaux fichiers, données du résumé) ou
    (None, None) pour un lot refusé, ou False si son lot ne peut pas
    tourner."""

    def __init__(self, folder, outcome=None, img_mode=FAKE_IMG_MODE.MODE_ONE_PER_IMAGE):
        self.folder = str(folder)
        self.started, self.finished, self.levels = [], [], []
        self.outcome = outcome or (lambda kind, files: ([], lot_data(len(files))))
        self.ns = ast_loader.load(
            MODULE, {"_run_convert_all", "_convert_all_steps", "_convert_all_step_files",
                     "convert_all_files", "_CONVERT_ALL_EXTENSIONS", "CONVERT_ALL_KINDS",
                     "_note_busy", "_path_key", "recompress_step_files"},
            os=os, walk_without_links=walk_without_links, IMAGE_EXTS=IMAGE_EXTS,
            _ImgModeDialog=FAKE_IMG_MODE, extension_check_files=self.extension_files,
            files_in_running_batch=files_in_running_batch,
            _start_convert_all_step=self.start, _finish_convert_all=self.finish)
        self.img_mode = img_mode

    @staticmethod
    def extension_files(dirs):
        return sorted(os.path.join(root, name) for d in dirs
                      for root, _s, names in walk_without_links(d) for name in names
                      if name.lower().endswith((".cbr", ".cbz", ".pdf", ".zip")))

    def start(self, parent, kind, folder, files, dirs, directories, callbacks, is_permanent,
              state, on_finished, problems, level=None):
        self.started.append((kind, folder, [os.path.basename(f) for f in files]))
        self.levels.append(level)
        outcome = self.outcome(kind, files)
        if outcome is False:
            problems["unavailable"].append(f"{kind}: indisponible")
            return False
        on_finished(*outcome)
        return True

    def finish(self, parent, dirs, callbacks, results, problems, stopped, ignored_count):
        self.finished.append({"results": results, "unavailable": problems["unavailable"],
                              "busy": [os.path.basename(f) for f in problems["busy"]],
                              "stopped": stopped, "ignored": ignored_count})

    def run(self, dirs=None, level=None, chosen=None):
        self.ns["_run_convert_all"](None, dirs or [self.folder], None, CALLBACKS, self.img_mode,
                                    False, 3, level, chosen)
        [result] = self.finished
        return result


def lot_data(handled, **extra):
    return ("lot", dict({"cancelled": False, "disk_full": None, "handled": handled}, **extra))


def test_every_lot_runs_in_order_and_empty_steps_are_skipped(tmp_path):
    touch(tmp_path, "a.cbr", "b.pdf", "p01.jpg")
    chain = Chain(tmp_path)
    result = chain.run()
    assert [step[0] for step in chain.started] == ["extensions", "cbr", "pdf", "img"]
    assert [kind for kind, _data, _files in result["results"]] == ["extensions", "cbr", "pdf",
                                                                   "img"]
    assert result["stopped"] is False and result["ignored"] == 3


def test_files_renamed_by_the_extension_step_go_to_the_right_lot(tmp_path):
    """Un PDF nommé .cbr, renommé .pdf par l'étape « extensions », est
    converti par le lot PDF : chaque étape relit le dossier."""
    touch(tmp_path, "a.cbr")

    def outcome(kind, files):
        if kind == "extensions":
            os.rename(tmp_path / "a.cbr", tmp_path / "a.pdf")
        return [], lot_data(len(files))

    chain = Chain(tmp_path, outcome)
    chain.run()
    assert chain.started == [("extensions", None, ["a.cbr"]), ("pdf", None, ["a.pdf"])]


def test_only_a_cancelled_lot_stops_the_rest(tmp_path):
    touch(tmp_path, "a.cbr", "b.cb7", "c.pdf")

    def outcome(kind, files):
        return [], lot_data(len(files), cancelled=(kind == "cb7"))

    chain = Chain(tmp_path, outcome)
    result = chain.run()
    assert [step[0] for step in chain.started] == ["extensions", "cbr", "cb7"]
    assert result["stopped"] is True
    assert [kind for kind, _data, _files in result["results"]] == ["extensions", "cbr", "cb7"]


@pytest.mark.parametrize("problem", ["disk_full", "refused"])
def test_a_refused_or_full_disk_lot_lets_the_rest_run(tmp_path, problem):
    """Disque plein : signalé dans la section du lot. Lot refusé à son
    démarrage (un autre lot a pris l'un de ses fichiers entre-temps) : ses
    fichiers sont signalés comme pris par un autre lot."""
    touch(tmp_path, "a.cbr", "b.cb7", "c.pdf")

    def outcome(kind, files):
        if kind != "cb7":
            return [], lot_data(len(files))
        if problem == "refused":
            return None, None
        return [], lot_data(1, disk_full={"count": 1, "last_done": None})

    chain = Chain(tmp_path, outcome)
    result = chain.run()
    assert [step[0] for step in chain.started] == ["extensions", "cbr", "cb7", "pdf"]
    assert result["stopped"] is False
    kinds = [kind for kind, _data, _files in result["results"]]
    if problem == "refused":
        assert kinds == ["extensions", "cbr", "pdf"] and result["busy"] == ["b.cb7"]
    else:
        assert kinds == ["extensions", "cbr", "cb7", "pdf"] and result["busy"] == []


def test_files_taken_by_another_batch_are_left_aside_and_reported(tmp_path):
    touch(tmp_path, "a.cbr", "b.cbr", "c.cb7")
    claims = BatchClaims.start([str(tmp_path / "a.cbr"), str(tmp_path / "c.cb7")])
    try:
        chain = Chain(tmp_path)
        result = chain.run()
    finally:
        claims.finish()
    # L'étape CBR traite b.cbr seul ; l'étape CB7 n'a plus de fichier
    assert chain.started == [("extensions", None, ["b.cbr"]), ("cbr", None, ["b.cbr"])]
    # Chacun signalé une fois, même vu par deux étapes
    assert sorted(result["busy"]) == ["a.cbr", "c.cb7"]
    assert result["stopped"] is False


def test_a_lot_that_cannot_run_is_an_error_and_the_rest_goes_on(tmp_path):
    touch(tmp_path, "a.cbr", "b.pdf")
    chain = Chain(tmp_path, lambda kind, files: False if kind == "cbr"
                  else ([], lot_data(len(files))))
    result = chain.run()
    assert [step[0] for step in chain.started] == ["extensions", "cbr", "pdf"]
    assert result["unavailable"] == ["cbr: indisponible"] and result["stopped"] is False


def test_recompression_comes_last_and_skips_the_cbz_written_by_the_chain(tmp_path):
    """Recompression en dernier, au taux de la confirmation : CBZ déjà
    présents et fichiers seulement renommés en .cbz, jamais les CBZ que les
    conversions viennent d'écrire (déjà au taux par défaut)."""
    touch(tmp_path, "old.cbz", "a.cbr", "b.cbr")

    def outcome(kind, files):
        if kind != "cbr":
            return [], lot_data(len(files))
        written, renamed = str(tmp_path / "a.cbz"), str(tmp_path / "b.cbz")
        os.rename(tmp_path / "a.cbr", written)
        os.rename(tmp_path / "b.cbr", renamed)
        return [written, renamed], lot_data(len(files), renamed_paths=[renamed])

    chain = Chain(tmp_path, outcome)
    result = chain.run(level=6)
    assert chain.started[-1] == ("recompress", None, ["b.cbz", "old.cbz"])
    assert chain.levels[-1] == 6
    assert [kind for kind, _data, _files in result["results"]][-1] == "recompress"


def test_recompression_only_cbz_files(tmp_path):
    touch(tmp_path, "a.cbz", "sous/b.CBZ", "c.cbr", "d.zip")
    found = ast_loader.load(MODULE, {"recompress_step_files"}, os=os,
                            walk_without_links=walk_without_links)["recompress_step_files"]
    assert [os.path.basename(f) for f in found([str(tmp_path)], natural_key)] == ["a.cbz",
                                                                                  "b.CBZ"]


def test_images_in_one_cbz_get_one_lot_per_dropped_folder(tmp_path):
    touch(tmp_path, "A/p01.jpg", "A/sous/p02.jpg", "B/p01.png")
    chain = Chain(tmp_path, img_mode=FAKE_IMG_MODE.MODE_ALL_IN_ONE)
    result = chain.run([str(tmp_path / "A"), str(tmp_path / "B")])
    assert chain.started == [("img_single", str(tmp_path / "A"), ["p01.jpg", "p02.jpg"]),
                             ("img_single", str(tmp_path / "B"), ["p01.png"])]
    # Une section « img » par CBZ dans le résumé
    assert [kind for kind, _data, _files in result["results"]] == ["img", "img"]


# ---------------------------------------------------------------------------
# Étape « extensions » : renommage sans question
# ---------------------------------------------------------------------------

def load_rename_all(renamed_calls, followed):
    def rename_checked(found, in_use, errors):
        renamed_calls.append(list(found))
        return [(check, check.path[:-4] + check.new_extension) for check in found]
    return ast_loader.load(
        MODULE, {"_rename_all_misnamed"},
        _app_panels=lambda: ["panneau"],
        _files_in_use_except_documents=lambda panels: [],
        _rename_checked_files=rename_checked,
        _follow_renamed_files=lambda panels, renamed: followed.append(renamed),
    )["_rename_all_misnamed"]


def extension_result(tmp_path, cancelled):
    check = check_extension(make_zip(tmp_path / "a.cbr"))
    return {"found": [check], "unknown": [str(tmp_path / "x.cbz")], "well_named": 2,
            "checked": 4, "cancelled": cancelled, "directory": str(tmp_path),
            "directories": None, "ignored_count": None}, check


def test_extension_step_renames_every_misnamed_file_without_asking(tmp_path):
    calls, followed, got = [], [], []
    result, check = extension_result(tmp_path, cancelled=False)
    load_rename_all(calls, followed)(result, lambda new, summary: got.append((new, summary)))
    assert calls == [[check]] and len(followed) == 1
    [(new_files, (kind, data))] = got
    assert kind == "extensions" and new_files == [check.path[:-4] + ".cbz"]
    assert data["renamed_count"] == 1 and data["kept_count"] == 0
    assert data["unknown_count"] == 1 and data["handled"] == 4 and data["cancelled"] is False


def test_cancelled_extension_step_renames_nothing(tmp_path):
    calls, followed, got = [], [], []
    result, check = extension_result(tmp_path, cancelled=True)
    load_rename_all(calls, followed)(result, lambda new, summary: got.append((new, summary)))
    assert calls == [] and followed == []
    [(new_files, (_kind, data))] = got
    assert new_files == [] and data["kept"] == [check] and data["cancelled"] is True


# ---------------------------------------------------------------------------
# Journal unique
# ---------------------------------------------------------------------------

def load_log():
    return ast_loader.load(
        MODULE, {"_write_convert_all_log", "_convert_all_counts", "log_line", "log_title",
                 "_CONVERT_ALL_LABELS", "_LOT_RENAMES"},
        os=os, _wt=_tr, log_text=lambda t: t,
        unique_log_path=lambda d, kind, now: os.path.join(d, f"Log_{kind}.txt"))


def test_log_counts_the_whole_conversion(tmp_path):
    folder = tmp_path / "comics"
    folder.mkdir()
    misnamed = check_extension(make_zip(folder / "a.cb7"))     # ZIP nommé .cb7
    pdf_named_cbr = str(folder / "b.cbr")
    renamed_pdf = str(folder / "b.pdf")
    renamed_cbz = str(folder / "a.cbz")
    extensions = {"checked": 3, "well_named": 1, "kept_count": 0, "unknown_count": 1,
                  "errors_count": 0, "handled": 3,
                  "renamed": [(misnamed, renamed_cbz),
                              (types.SimpleNamespace(path=pdf_named_cbr, extension=".cbr",
                                                     new_extension=".pdf"), renamed_pdf)],
                  "kept": [], "unknown": [str(folder / "c.cbz")], "error_lines": []}
    cbr = {"handled": 1, "converted_count": 0, "errors_count": 1, "renamed_cbz": 1,
           "error_lines": ["d.cbr: erreur"], "renamed_lines": ["e.cbr → e.cbz"]}
    pdf = {"handled": 1, "converted_count": 1, "errors_count": 0, "error_lines": [],
           "renamed_lines": []}
    img = {"handled": 2, "converted_count": 2, "errors_count": 0, "error_lines": [],
           "renamed_lines": []}
    results = [("extensions", extensions, [misnamed.path, pdf_named_cbr, str(folder / "c.cbz")]),
               ("cbr", cbr, [str(folder / "d.cbr")]),
               ("pdf", pdf, [renamed_pdf]),
               ("img", img, [str(folder / "p01.jpg"), str(folder / "p02.jpg")])]
    logs = tmp_path / "logs"
    logs.mkdir()
    path = load_log()["_write_convert_all_log"](
        {"get_mosaicview_temp_dir": lambda: str(logs)}, [str(folder)], results,
        {"unavailable": ["f.cb7: indisponible"], "busy": [str(folder / "g.cbt")]}, True, 4)
    assert os.path.basename(path) == "Log_convertall.txt"
    text = open(path, encoding="utf-8").read()
    for expected in ("title_all", f"directory [('path', {str(folder)!r})]",
                     # 3 vérifiés + d.cbr + 2 images ; b.pdf déjà compté sous son ancien nom
                     "files_handled [('count', 6)]", "files_ignored [('count', 4)]",
                     "well_named [('count', 1)]",
                     "renamed_kind [('count', 1), ('kind', 'CB7→CBZ')]",
                     "renamed_kind [('count', 1), ('kind', 'CBR→PDF')]",
                     # Renommage fait par le lot CBR lui-même
                     "renamed_kind [('count', 1), ('kind', 'CBR→CBZ')]",
                     "unknown_format [('count', 1)]",
                     "converted_kind [('count', 0), ('kind', 'CBR→CBZ')]",
                     "converted_kind [('count', 1), ('kind', 'PDF→CBZ')]",
                     "converted_images [('count', 2)]",
                     "errors [('count', 2)]", "stopped",
                     # Fichier pris par un autre lot : compteur et section
                     "busy [('count', 1)]", "busy_files", f"  - {folder / 'g.cbt'}",
                     f"  - {misnamed.path} → a.cbz", "  - e.cbr → e.cbz",
                     f"  - {folder / 'c.cbz'}", "  - d.cbr: erreur", "  - f.cb7: indisponible"):
        assert expected in text, expected
    # Lots qui n'ont pas eu lieu (CB7, CBT) : aucune ligne de conversion
    assert "('kind', 'CB7→CBZ')]" not in text.split("unknown_format")[1]
    assert "CBT→CBZ" not in text and "not_renamed" not in text


def test_log_counts_the_recompression(tmp_path):
    folder = str(tmp_path)
    recompress = {"handled": 3, "recompressed_count": 1, "already_optimal_count": 1,
                  "errors_count": 1, "error_lines": ["c.cbz: erreur"],
                  "renamed_lines": ["b.cbr → b.cbz"], "renamed_kinds": {"CBR→CBZ": 1}}
    results = [("recompress", recompress,
                [os.path.join(folder, n) for n in ("a.cbz", "b.cbr", "c.cbz")])]
    path = load_log()["_write_convert_all_log"](
        {"get_mosaicview_temp_dir": lambda: folder}, [folder], results,
        {"unavailable": [], "busy": []}, False, None)
    text = open(path, encoding="utf-8").read()
    for expected in ("files_handled [('count', 3)]", "recompressed [('count', 1)]",
                     "already_optimal [('count', 1)]",
                     "renamed_kind [('count', 1), ('kind', 'CBR→CBZ')]",
                     "errors [('count', 1)]", "  - b.cbr → b.cbz", "  - c.cbz: erreur"):
        assert expected in text, expected


def test_log_without_recompression_has_no_recompression_line(tmp_path):
    path = load_log()["_write_convert_all_log"](
        {"get_mosaicview_temp_dir": lambda: str(tmp_path)}, [str(tmp_path)], [],
        {"unavailable": [], "busy": []}, False, None)
    text = open(path, encoding="utf-8").read()
    assert "recompressed" not in text and "already_optimal" not in text


def test_log_without_interruption_has_no_stopped_line(tmp_path):
    path = load_log()["_write_convert_all_log"](
        {"get_mosaicview_temp_dir": lambda: str(tmp_path)}, [str(tmp_path)], [],
        {"unavailable": [], "busy": []}, False, None)
    text = open(path, encoding="utf-8").read()
    assert "stopped" not in text and "files_ignored" not in text and "busy" not in text
    assert "files_handled [('count', 0)]" in text


# ---------------------------------------------------------------------------
# Résumé unique : section « extensions »
# ---------------------------------------------------------------------------

def test_summary_lines_of_the_extension_step():
    chain = ast_loader.load_methods(MODULE, "_ChainSummaryDialog", {"_extra_lines"}, _=_tr)
    data = {"renamed_count": 2, "kept_count": 0, "unknown_count": 1, "errors_count": 0,
            "well_named": 5}
    assert chain._extra_lines("extensions", data) == [
        "dialogs.batch_extensions.well_named_count [('count', 5)]",
        "dialogs.batch_extensions.renamed_count [('count', 2)]",
        "dialogs.batch_extensions.unknown_count [('count', 1)]"]
    clean = dict(data, renamed_count=0, unknown_count=0)
    assert chain._extra_lines("extensions", clean) == ["dialogs.batch_extensions.all_correct"]


def test_summary_lines_of_the_recompression_step():
    chain = ast_loader.load_methods(MODULE, "_ChainSummaryDialog",
                                    {"_extra_lines", "_section_message"}, _=_tr)
    data = {"total": 4, "recompressed_count": 2, "already_optimal_count": 1,
            "renamed_count": 0}
    assert chain._section_message("recompress", data) == (
        "dialogs.batch_all.section_recompress [('count', 4)]")
    assert chain._extra_lines("recompress", data) == [
        "dialogs.batch_all.recompressed_count [('count', 2)]",
        "dialogs.batch_all.already_optimal_count [('count', 1)]"]
    assert len(chain._extra_lines("recompress", dict(data, renamed_count=1))) == 3


def test_confirmation_announces_the_recompression_and_its_level():
    details = ast_loader.load(MODULE, {"_convert_all_details"}, _=_tr,
                              _ImgModeDialog=FAKE_IMG_MODE)["_convert_all_details"]
    counts = {"cbr": 1, "cb7": 0, "cbt": 0, "pdf": 0, "img": 0}
    lines = details(2, counts, None, 3, 6)
    assert lines[-1] == "dialogs.batch_all.detail_recompress [('count', 3), ('level', 6)]"
    assert not any("recompress" in line for line in details(2, counts, None, 0, 6))


def test_section_messages_never_end_with_the_directory():
    """Résumé unique : chaque section dit son lot et son compte, jamais
    « dans le répertoire : » (les dossiers sont listés une fois, en bas)."""
    chain = ast_loader.load_methods(MODULE, "_ChainSummaryDialog", {"_section_message"}, _=_tr)
    lot = {"converted_count": 2, "total": 3, "has_errors": True}
    assert chain._section_message("cbr", lot) == (
        "dialogs.batch_all.section_kind_errors [('count', 2), ('kind', 'CBR'), ('total', 3)]")
    assert chain._section_message("cb7", dict(lot, has_errors=False)) == (
        "dialogs.batch_all.section_kind [('count', 2), ('kind', 'CB7'), ('total', 3)]")
    # Un fichier seulement renommé n'est pas converti : « sur {total} »
    assert chain._section_message("cbt", dict(lot, has_errors=False, renamed_cbz=1)).startswith(
        "dialogs.batch_all.section_kind_errors")
    assert chain._section_message("img", dict(lot, has_errors=False)).startswith(
        "dialogs.batch_all.section_images ")
    assert chain._section_message("extensions", {"checked": 6}) == (
        "dialogs.batch_all.section_extensions [('count', 6)]")
    # Dans les textes eux-mêmes, aucune section ne finit par deux-points
    import json
    batch_all = json.loads((ROOT / "locales" / "fr.json").read_text("utf-8"))["dialogs"]["batch_all"]
    for key in ("section_kind", "section_kind_errors", "section_images", "section_images_errors",
                "section_extensions", "section_recompress"):
        assert not batch_all[key].rstrip().endswith(":"), key


# ---------------------------------------------------------------------------
# Fenêtre de choix (dépôt d'un dossier, menu) : chaque choix mène à un traitement
# ---------------------------------------------------------------------------

DROP_DIALOG = ROOT / "modules" / "qt" / "batch_drop_dialog_qt.py"
DROP_HANDLER = ROOT / "modules" / "qt" / "drop_handler_qt.py"


def _module_constant(path, name):
    tree = ast.parse(path.read_text("utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} introuvable dans {path.name}")


def _dict_keys(path, name):
    """Clés du dict littéral affecté à `name` dans le module."""
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                and getattr(node.targets[0], "id", None) == name):
            return [key.value for key in node.value.keys]
    raise AssertionError(f"dict {name} introuvable dans {path.name}")


STEPS = _module_constant(DROP_DIALOG, "STEPS")
STEP_KINDS = tuple(kind for kind, _key in STEPS)


def test_every_choice_of_the_window_leads_to_a_treatment():
    """Choix exclusifs : le groupe (« all », avec ses traitements cochés),
    l'import de métadonnées et la Bibliothèque ; chaque traitement du groupe
    se lance aussi seul (dict `single` de show_batch_choice)."""
    assert set(_dict_keys(DROP_HANDLER, "callbacks")) == {"batch_all", "batch_metadata",
                                                          "batch_library"}
    assert set(_dict_keys(DROP_HANDLER, "single")) == set(STEP_KINDS)
    # Les traitements du groupe sont ceux de la conversion complète
    steps = ast_loader.load(MODULE, {"_convert_all_steps"},
                            _ImgModeDialog=FAKE_IMG_MODE)["_convert_all_steps"]
    chain = {"img" if kind == "img_single" else kind
             for kind, _folder in steps(["A"], FAKE_IMG_MODE.MODE_ONE_PER_IMAGE)}
    assert chain == set(STEP_KINDS)


def test_group_is_first_and_chosen_by_default_recompression_last():
    source = DROP_DIALOG.read_text("utf-8")
    assert "self._radio_all.setChecked(True)" in source
    assert STEP_KINDS[-1] == "recompress"
    init = source.split("def __init__", 1)[1].split("def _retranslate", 1)[0]
    assert init.index("all_row.addWidget(self._radio_all)") < init.index("sub_layout.addWidget")
    # Aucun trait dans le groupe : après le groupe, et au-dessus des boutons
    assert init.count("QFrame.HLine") == 2
    # Note des sous-dossiers sous l'intro, au-dessus de tous les choix
    assert (init.index("layout.addWidget(self._lbl_intro)")
            < init.index("layout.addWidget(self._lbl_note)")
            < init.index("all_row.addWidget(self._radio_all)"))
    # Tout coché au lancement de l'application
    assert "_session_checked = set(STEP_KINDS)" in source


class _Check:
    def __init__(self, on):
        self.on = on

    def isChecked(self):
        return self.on


class _Button(_Check):
    def setChecked(self, on):
        self.on = on

    def setEnabled(self, on):
        self.on = on

    def set_partial(self, partial):
        self.partial = partial


def load_dialog(session):
    base = ast_loader.load_methods(
        "batch_drop_dialog_qt.py", "BatchDropDialog",
        {"_checked_steps", "_on_step_toggled", "_update_ok", "_on_group_clicked"},
        STEP_KINDS=STEP_KINDS, _session_checked=session)

    class Dialog(base):
        def __init__(self, checked):
            self._step_checks = {kind: _Check(kind in checked) for kind in STEP_KINDS}
            self._radio_all = _Button(False)
            self._btn_ok = _Button(True)
    return Dialog


def test_a_checked_step_is_remembered_for_the_session_and_chooses_the_group():
    session = set(STEP_KINDS)
    dialog = load_dialog(session)(set(STEP_KINDS) - {"pdf"})
    dialog._step_checks["pdf"].on = False
    dialog._on_step_toggled("pdf", False)
    assert "pdf" not in session
    assert dialog._checked_steps() == tuple(k for k in STEP_KINDS if k != "pdf")
    # Décocher choisit aussi le groupe
    assert dialog._radio_all.on is True
    dialog._radio_all.on = False
    dialog._step_checks["pdf"].on = True
    dialog._on_step_toggled("pdf", True)
    assert "pdf" in session and dialog._radio_all.on is True


def test_ok_is_greyed_when_the_group_has_no_checked_step():
    dialog = load_dialog(set())(set())
    dialog._radio_all.on = True
    dialog._update_ok()
    assert dialog._btn_ok.on is False
    dialog._step_checks["cb7"].on = True
    dialog._update_ok()
    assert dialog._btn_ok.on is True


def test_clicking_the_chosen_group_again_checks_all_its_steps():
    dialog = load_dialog(set())({"cb7"})
    for check in dialog._step_checks.values():
        check.setChecked = lambda on, c=check: setattr(c, "on", on)
    dialog._all_was_checked = False
    dialog._on_group_clicked()
    assert dialog._checked_steps() == ("cb7",)
    dialog._all_was_checked = True
    dialog._on_group_clicked()
    assert dialog._checked_steps() == STEP_KINDS


def test_group_shows_a_partial_selection():
    dialog = load_dialog(set())(set(STEP_KINDS))
    dialog._update_ok()
    assert dialog._radio_all.partial is False
    dialog._step_checks["pdf"].on = False
    dialog._update_ok()
    assert dialog._radio_all.partial is True
    for check in dialog._step_checks.values():
        check.on = False
    dialog._update_ok()
    assert dialog._radio_all.partial is False


def test_only_the_checked_steps_are_chained():
    steps = ast_loader.load(MODULE, {"_convert_all_steps"},
                            _ImgModeDialog=FAKE_IMG_MODE)["_convert_all_steps"]
    assert steps(["A"], None, {"cb7", "cbt"}) == [("cb7", None), ("cbt", None)]
    assert steps(["A", "B"], FAKE_IMG_MODE.MODE_ALL_IN_ONE, {"img", "recompress"}) == [
        ("img_single", "A"), ("img_single", "B"), ("recompress", None)]


def test_unchecked_steps_never_run(tmp_path):
    touch(tmp_path, "a.cbr", "b.cb7", "c.cbt", "d.pdf", "old.cbz")
    chain = Chain(tmp_path)
    chain.run(chosen={"cb7", "cbt"})
    assert [step[0] for step in chain.started] == ["cb7", "cbt"]
