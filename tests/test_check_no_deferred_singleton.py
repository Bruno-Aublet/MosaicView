"""
test_check_no_deferred_singleton.py — Garde-fou anti-régression : les opérations
différées ne doivent jamais viser le mauvais panneau en split-view.

save_as_cbz / create_cbz_from_images / apply_new_names & co sont appelées en
différé (réponse à un dialogue de fermeture non modal, chaîne de validation non
modale), et le téléchargement de l'import web tourne dans un thread. Le singleton
modules.qt.state.state peut alors pointer sur l'autre panneau : le lire sans
repli explicite fait agir l'opération sur le mauvais panneau (sauvegarde du
fichier de l'autre panneau, nommage des images importées d'après l'autre panneau).

Ce test scanne les fichiers de SCANNED_FILES (dans modules/qt/) et n'autorise une lecture de
`_state_module.state` que sous l'une de ces formes :
  - `quelque_chose or _state_module.state`   (ex. callbacks.get("state") or ...)
  - dans le corps d'un `if state is None:`   (paramètre state=None explicite)

Fait partie de la suite pytest normale :  python -m pytest tests/
"""
import ast
import os

QT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "modules", "qt")
SCANNED_FILES = ("file_operations_qt.py", "web_import_qt.py")


def _is_singleton_read(node):
    return (isinstance(node, ast.Attribute)
            and node.attr == "state"
            and isinstance(node.value, ast.Name)
            and node.value.id == "_state_module"
            and isinstance(node.ctx, ast.Load))


def _is_state_is_none_test(test):
    return (isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name) and test.left.id == "state"
            and len(test.ops) == 1 and isinstance(test.ops[0], ast.Is)
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value is None)


def _find_unguarded_reads(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    bad = []
    for node in ast.walk(tree):
        if not _is_singleton_read(node):
            continue
        parent = parents.get(node)
        if (isinstance(parent, ast.BoolOp) and isinstance(parent.op, ast.Or)
                and parent.values[-1] is node and len(parent.values) > 1):
            continue
        guarded = False
        child, anc = node, parent
        while anc is not None:
            if isinstance(anc, ast.If) and _is_state_is_none_test(anc.test) and child in anc.body:
                guarded = True
                break
            child, anc = anc, parents.get(anc)
        if not guarded:
            bad.append(node.lineno)
    return bad


def test_no_unguarded_singleton_read_in_save_operations():
    findings = []
    for name in SCANNED_FILES:
        path = os.path.join(QT_DIR, name)
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        findings += [f"{name}:{line}" for line in _find_unguarded_reads(tree)]
    assert not findings, (
        "Lecture du singleton _state_module.state sans state explicite prioritaire "
        "(risque d'agir sur le mauvais panneau en split-view) :\n  "
        + "\n  ".join(findings)
    )


def test_checker_detects_bare_read():
    tree = ast.parse("def f():\n    state = _state_module.state\n")
    assert _find_unguarded_reads(tree) == [2]


def test_checker_accepts_guarded_reads():
    src = (
        "def f(callbacks, state=None):\n"
        "    a = callbacks.get('state') or _state_module.state\n"
        "    if state is None:\n"
        "        state = _state_module.state\n"
    )
    assert _find_unguarded_reads(ast.parse(src)) == []
