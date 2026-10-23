"""
test_check_no_deferred_singleton.py — Garde-fou anti-régression : une opération
ne doit jamais viser le mauvais panneau en split-view.

Le singleton modules.qt.state.state désigne le panneau actif. Une suite
différée (réponse à un dialogue non modal, fin de worker, chaîne de
validation) peut s'exécuter après un changement de panneau : lire le
singleton à ce moment ferait agir l'opération sur l'autre panneau
(sauvegarde du fichier de l'autre panneau, pages ajoutées au mauvais
document). Règle CLAUDE.md : le state du panneau est toujours transmis
explicitement.

Ce test scanne tout modules/qt/, quel que soit le nom sous lequel le module
`modules.qt.state` est importé (`_state_module`, `_st`…), et signale aussi
tout `from modules.qt.state import state` (singleton importé directement).
Il n'autorise une lecture de `_state_module.state` que sous l'une de ces
formes :
  - `quelque_chose or _state_module.state`   (ex. callbacks.get("state") or ...)
  - dans le corps d'un `if state is None:`   (paramètre state=None explicite)
  - `x if <condition> else _state_module.state` (state explicite prioritaire)
  - `_state_module.state.dark_mode` : réglage de thème de toute l'application ;
ou dans une fonction de ALLOWED, qui dit pourquoi elle peut le lire.

Fait partie de la suite pytest normale :  python -m pytest tests/
"""
import ast
import os

import ast_loader

QT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "modules", "qt")

_COMMAND = ("commande lancée par le menu, la colonne d'icônes ou un raccourci du panneau "
            "actif, exécutée tout de suite (singleton = panneau actif) ; le state lu est "
            "gardé dans une variable locale pour la suite")
_SWAP = "sauvegarde du singleton pour le rendre à la fin de l'appel (finally)"

# (fichier, fonction la plus proche) -> raison
ALLOWED = {
    ("clipboard_qt.py", "copy_archive_to_clipboard"): _COMMAND,
    ("clipboard_qt.py", "copy_to_system_clipboard"): _COMMAND,
    ("clipboard_qt.py", "cut_selected"): _COMMAND,
    ("clipboard_qt.py", "paste_from_system_clipboard"): _COMMAND,
    ("flatten_directories_qt.py", "flatten_directories_qt"): _COMMAND,
    ("toggle_theme_qt.py", "toggle_theme"): "bascule du thème, réglage de toute l'application",
    ("file_close_qt.py", "on_window_close"):
        "fermeture de la fenêtre principale, synchrone (closeEvent)",
    ("mosaic_canvas.py", "_resolve_state"):
        "repli quand la scène n'a aucune vue rattachée à un panneau",
    ("panel_widget.py", "_flatten_directories"): _SWAP,
    ("panel_widget.py", "_render_mosaic"): _SWAP,
    ("panel_widget.py", "_apply_thumb_size"): _SWAP,
    ("panel_widget.py", "_wrapped"): _SWAP,
    ("virtual_library_panel.py", "activate"): _SWAP,
    ("library_window.py", "_retranslate"): "couleur du thème (dark_mode), réglage de toute l'application",
    ("panel_widget.py", "apply_separator_theme"):
        "couleur du thème (dark_mode), réglage de toute l'application",
    ("viewer_toolbar_qt.py", "_load_icon"):
        "couleur des icônes selon le thème (dark_mode), réglage de toute l'application",
}

STATE_MODULE = "modules.qt.state"


def _module_aliases(tree):
    """Noms sous lesquels le module modules.qt.state est importé."""
    aliases = {"_state_module"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases |= {a.asname for a in node.names if a.name == STATE_MODULE and a.asname}
        elif isinstance(node, ast.ImportFrom) and node.module == "modules.qt":
            aliases |= {a.asname or a.name for a in node.names if a.name == "state"}
    return aliases


def _is_singleton_read(node, aliases=("_state_module",)):
    return (isinstance(node, ast.Attribute)
            and node.attr == "state"
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
            and isinstance(node.ctx, ast.Load))


def _is_direct_singleton_import(node):
    return (isinstance(node, ast.ImportFrom) and node.module == STATE_MODULE
            and any(a.name == "state" for a in node.names))


def _is_state_is_none_test(test):
    return (isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name) and test.left.id == "state"
            and len(test.ops) == 1 and isinstance(test.ops[0], ast.Is)
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value is None)


def _enclosing_function(node, parents):
    anc = parents.get(node)
    while anc is not None:
        if isinstance(anc, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return anc.name
        anc = parents.get(anc)
    return None


def _find_unguarded_reads(tree):
    """[(ligne, fonction la plus proche ou None)] des lectures non reconnues."""
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    aliases = _module_aliases(tree)
    bad = []
    for node in ast.walk(tree):
        if _is_direct_singleton_import(node):
            bad.append((node.lineno, _enclosing_function(node, parents)))
            continue
        if not _is_singleton_read(node, aliases):
            continue
        parent = parents.get(node)
        if (isinstance(parent, ast.BoolOp) and isinstance(parent.op, ast.Or)
                and parent.values[-1] is node and len(parent.values) > 1):
            continue
        if isinstance(parent, ast.IfExp) and parent.orelse is node:
            continue
        if isinstance(parent, ast.Attribute) and parent.attr == "dark_mode":
            continue
        guarded = False
        func = None
        child, anc = node, parent
        while anc is not None:
            if isinstance(anc, ast.If) and _is_state_is_none_test(anc.test) and child in anc.body:
                guarded = True
            if func is None and isinstance(anc, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func = anc.name
            child, anc = anc, parents.get(anc)
        if not guarded:
            bad.append((node.lineno, func))
    return bad


def _scan():
    found = {}
    for name in sorted(os.listdir(QT_DIR)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(QT_DIR, name)
        tree = ast_loader.module_tree(path)
        found[name] = _find_unguarded_reads(tree)
    return found


FOUND = _scan()


def test_no_unguarded_singleton_read_in_modules_qt():
    findings = [f"{name}:{line} ({func})" for name, reads in FOUND.items()
                for line, func in reads if (name, func) not in ALLOWED]
    assert not findings, (
        "Lecture du singleton _state_module.state sans state explicite prioritaire "
        "(risque d'agir sur le mauvais panneau en split-view). Transmettre le state "
        "du panneau, ou, pour une commande exécutée tout de suite, l'ajouter à ALLOWED "
        "avec sa raison :\n  " + "\n  ".join(findings)
    )


def test_allowed_list_is_not_stale():
    used = {(name, func) for name, reads in FOUND.items() for _line, func in reads}
    stale = sorted(k for k in ALLOWED if k not in used)
    assert not stale, f"Exceptions sans lecture correspondante : {stale}"


def test_scan_covers_the_whole_package():
    assert len(FOUND) > 100
    assert "file_operations_qt.py" in FOUND and "panel_widget.py" in FOUND


def test_checker_detects_bare_read():
    tree = ast.parse("def f():\n    state = _state_module.state\n")
    assert _find_unguarded_reads(tree) == [(2, "f")]


def test_checker_detects_a_read_in_a_deferred_callback():
    tree = ast.parse("def f(dlg):\n    def _on_answer(ok):\n"
                     "        _state_module.state.modified = True\n"
                     "    dlg.done.connect(_on_answer)\n")
    assert _find_unguarded_reads(tree) == [(3, "_on_answer")]


def test_checker_sees_other_aliases_and_direct_imports():
    tree = ast.parse("def f():\n    from modules.qt import state as _st\n    s = _st.state\n"
                     "def g():\n    from modules.qt.state import state\n")
    assert sorted(_find_unguarded_reads(tree)) == [(3, "f"), (5, "g")]


def test_checker_accepts_guarded_reads():
    src = (
        "def f(callbacks, state=None):\n"
        "    a = callbacks.get('state') or _state_module.state\n"
        "    if state is None:\n"
        "        state = _state_module.state\n"
        "    b = state if state is not None else _state_module.state\n"
        "    dark = _state_module.state.dark_mode\n"
    )
    assert _find_unguarded_reads(ast.parse(src)) == []
