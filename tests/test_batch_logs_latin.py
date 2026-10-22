"""Textes des traitements par lot : journaux lisibles, progression traduite par
la fenêtre.

- Les journaux de lot sont lus dans le Bloc-notes, qui ne sait pas afficher le
  pIqaD ni les tengwar : leurs en-têtes passent par log_line / log_title
  (`_wt`, repli latin), et aucun texte anglais ou traduit par `_` n'y est
  écrit directement.
- Les threads de lot travaillent sous latin_texts() : un message d'erreur
  traduit qu'ils construisent (et qui finit dans le journal) est en latin.
- Les textes de progression sont envoyés à la fenêtre sous forme de fonctions,
  résolues par elle dans sa langue et sa police (et rejouées au changement de
  langue) : jamais un `_()` résolu dans le thread du lot.

batch_dialogs_qt et batch_metadata_dialog_qt chargent Qt : ils sont lus par
AST, sans être importés.
"""
import ast
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BATCH = ROOT / "modules" / "qt" / "batch_dialogs_qt.py"
METADATA = ROOT / "modules" / "qt" / "batch_metadata_dialog_qt.py"

# Fonctions dont les arguments ne sont pas du texte écrit tel quel (clé de
# traduction, nom de fichier échappé, format de date).
_TEXT_FUNCS = {"log_line", "log_title", "log_text", "exception_text"}
# Caractères de mise en forme admis en dur dans un journal.
_LAYOUT = set(" =-:\n")


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _func_name(call):
    f = call.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None


def _constants_outside(node, skip):
    """Chaînes constantes de `node`, hors des sous-arbres pour lesquels
    skip(sous_arbre) est vrai."""
    if skip(node):
        return
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node
    for child in ast.iter_child_nodes(node):
        yield from _constants_outside(child, skip)


def _log_writes(tree):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "write" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "lf"):
            yield node


def test_batch_log_headers_are_translated_in_latin():
    found = 0
    offenders = []
    for path in (BATCH, METADATA):
        for call in _log_writes(_tree(path)):
            found += 1
            for arg in call.args:
                skip = lambda n: isinstance(n, ast.Call) and _func_name(n) in _TEXT_FUNCS
                for const in _constants_outside(arg, skip):
                    if set(const.value) - _LAYOUT:
                        offenders.append(f"{path.name}:{const.lineno} {const.value!r}")
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Call) and _func_name(sub) == "_":
                        offenders.append(f"{path.name}:{sub.lineno} _() dans un journal")
    assert found >= 8, "écritures de journal de lot introuvables"
    assert not offenders, "texte d'un journal de lot hors de log_line/log_title :\n" + "\n".join(offenders)


def _function(tree, name, cls=None):
    nodes = tree.body
    if cls is not None:
        nodes = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls).body
    return next(n for n in nodes if isinstance(n, ast.FunctionDef) and n.name == name)


class _FakeLocalization:
    """Langue pIqaD : _() rend l'alphabet klingon, _wt() la version latine."""
    current_language = "tlh-piqad"
    _cache_lock = threading.Lock()
    _translations_cache = {"tlh": {"dialogs": {"batch_log": {
        "date": "jaj: {date}", "title_cbr": "QonoS"}}}}

    def get(self, key, **kwargs):
        return " {date}".format(date=kwargs.get("date", ""))

    def _load_one_translation(self, code):
        pass


def test_log_line_and_title_never_use_a_fictional_alphabet(monkeypatch):
    from modules.qt import localization
    monkeypatch.setattr(localization, "_localization_manager", _FakeLocalization())
    tree = _tree(BATCH)
    module = ast.Module(body=[_function(tree, "log_line"), _function(tree, "log_title")],
                        type_ignores=[])
    namespace = {"_wt": localization._wt}
    exec(compile(module, str(BATCH), "exec"), namespace)
    assert namespace["log_line"]("date", date="2026-10-20 12:00") == "jaj: 2026-10-20 12:00\n"
    assert namespace["log_title"]("title_cbr") == "MosaicView - QonoS\n"


def _uses_latin_texts(func):
    return any(isinstance(n, ast.With) and any(
        isinstance(item.context_expr, ast.Call) and _func_name(item.context_expr) == "latin_texts"
        for item in n.items) for n in ast.walk(func))


def test_batch_threads_work_under_latin_texts():
    batch = _tree(BATCH)
    assert _uses_latin_texts(_function(batch, "_start_batch_thread"))
    metadata = _tree(METADATA)
    for cls in ("_LoadWorker", "_SaveWorker"):
        assert _uses_latin_texts(_function(metadata, "run", cls)), cls


def test_progress_texts_are_resolved_by_the_progress_window():
    emits = 0
    offenders = []
    for node in ast.walk(_tree(BATCH)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "emit" and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr in ("update_progress", "update_page_bar")):
            continue
        emits += 1
        text = node.args[-1]
        for sub in _walk_outside_lambdas(text):
            if isinstance(sub, ast.Call) and _func_name(sub) in ("_", "_wt"):
                offenders.append(f"ligne {node.lineno}")
    assert emits >= 11, "envois de progression introuvables"
    assert not offenders, "texte de progression traduit dans le thread du lot : " + ", ".join(offenders)


def _walk_outside_lambdas(node):
    if isinstance(node, ast.Lambda):
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _walk_outside_lambdas(child)
