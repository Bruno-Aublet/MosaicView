"""
test_check_internal_imports.py — Garde-fou : chaque nom importé d'un module du
projet doit y exister.

Un import placé dans une fonction (`from modules.qt.x import nom`) n'est exécuté
qu'à l'appel de cette fonction : un nom supprimé ou renommé dans le module cible
ne casse rien au démarrage. Appelée depuis un gestionnaire d'événement ou un
slot Qt, l'`ImportError` est avalée (trace console seulement) et la commande ne
fait plus rien, sans message — ex. le drop de dossier, quand `drop_handler_qt`
importait encore un alias retiré de `batch_dialogs_qt`.

Ce test scanne `MosaicView.py` et `modules/`, imports locaux compris, et vérifie
pour chaque `from modules.… import nom` que `nom` est défini au niveau module du
fichier cible (fonction, classe, affectation, import, y compris dans un bloc
if/try de niveau module, ou déclaré `global` dans une fonction) ou qu'il désigne
un sous-module ; et pour chaque `import modules.…` que le module existe.

Fait partie de la suite pytest normale :  python -m pytest tests/
"""
import ast
import os

import ast_loader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _source_files():
    yield os.path.join(ROOT, "MosaicView.py")
    for dirpath, _dirs, filenames in os.walk(os.path.join(ROOT, "modules")):
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def _module_path(module):
    """Fichier d'un module `modules.…` (module simple ou paquet), ou None."""
    base = os.path.join(ROOT, *module.split("."))
    if os.path.isfile(base + ".py"):
        return base + ".py"
    if os.path.isfile(os.path.join(base, "__init__.py")):
        return os.path.join(base, "__init__.py")
    if os.path.isdir(base):
        return base  # paquet sans __init__.py (namespace)
    return None


def _bind_targets(target, names):
    for node in ast.walk(target):
        if isinstance(node, ast.Name):
            names.add(node.id)


def _collect_module_level(stmts, names):
    """Noms liés au niveau module, en descendant dans les blocs de contrôle
    (if/try/with/for/while) mais pas dans les fonctions ni les classes."""
    for node in stmts:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                _bind_targets(t, names)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            _bind_targets(node.target, names)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            _bind_targets(node.target, names)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    _bind_targets(item.optional_vars, names)
        for field in ("body", "orelse", "finalbody"):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                _collect_module_level(getattr(node, field, []) or [], names)
        if isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
            for handler in node.handlers:
                _collect_module_level(handler.body, names)


_defined_cache = {}


def _defined_names(path):
    if path not in _defined_cache:
        tree = ast_loader.module_tree(path)
        names = set()
        _collect_module_level(tree.body, names)
        for node in ast.walk(tree):
            if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node, ast.Global):
                names.update(node.names)
        _defined_cache[path] = names
    return _defined_cache[path]


def _find_broken_imports():
    broken = []
    for path in _source_files():
        rel = os.path.relpath(path, ROOT)
        tree = ast_loader.module_tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("modules.") and _module_path(alias.name) is None:
                        broken.append(f"{rel}:{node.lineno}: module {alias.name} introuvable")
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module \
                    and (node.module == "modules" or node.module.startswith("modules.")):
                target = _module_path(node.module)
                if target is None:
                    broken.append(f"{rel}:{node.lineno}: module {node.module} introuvable")
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    if _module_path(f"{node.module}.{alias.name}") is not None:
                        continue  # sous-module
                    if os.path.isdir(target) or alias.name not in _defined_names(target):
                        broken.append(f"{rel}:{node.lineno}: {node.module}.{alias.name} introuvable")
    return broken


def test_every_internal_import_exists():
    broken = _find_broken_imports()
    assert not broken, (
        "Noms importés absents de leur module (un import local cassé ne se voit "
        "qu'à l'exécution, et Qt avale l'erreur) :\n" + "\n".join(broken))


def test_scan_detects_a_broken_import(tmp_path, monkeypatch):
    """Le scan doit voir un nom absent (y compris dans une fonction), et
    accepter un nom présent ou un sous-module : sinon le test ci-dessus
    passerait sans rien vérifier."""
    import sys
    src = tmp_path / "fautif.py"
    src.write_text(
        "from modules.qt.utils import format_file_size\n"
        "from modules.qt import utils\n"
        "def f():\n"
        "    from modules.qt.utils import nom_absent\n"
        "import modules.qt.module_absent\n",
        encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_source_files", lambda: [str(src)])
    broken = _find_broken_imports()
    assert len(broken) == 2
    assert any(b.endswith("modules.qt.utils.nom_absent introuvable") for b in broken)
    assert any(b.endswith("module modules.qt.module_absent introuvable") for b in broken)


def test_scan_covers_the_whole_package():
    assert len(list(_source_files())) > 100
