"""Extraction de fonctions d'un module de modules/qt/ qui importe Qt, pour les
exécuter seules, sans Qt.

load(module, names, **deps) compile les définitions de premier niveau
`names` (fonctions, classes, affectations) dans un espace de noms formé de
`deps`. Avant toute exécution, chaque nom global lu par ces définitions doit
exister dans `deps`, parmi les définitions extraites ou les fonctions
intégrées de Python : sinon le test échoue en nommant ce qui manque. Sans ce
contrôle, une dépendance ajoutée plus tard au module lèverait NameError à
l'exécution, et dans le bloc `try` d'une fonction qui se replie sur une
valeur par défaut (`except Exception: return data`), le test vérifierait le
repli sans le savoir.
"""
import ast
import builtins
import functools
from pathlib import Path

QT_DIR = Path(__file__).resolve().parent.parent / "modules" / "qt"


@functools.lru_cache(maxsize=None)
def _parsed(path, mtime_ns, size):
    return ast.parse(Path(path).read_text(encoding="utf-8"), filename=path)


def module_tree(path):
    """Arbre AST du fichier `path`, lu une seule fois pour toute la suite tant
    qu'il ne change pas (date et taille) : chaque garde-fou relit tout
    modules/qt/, et chaque lecture coûte environ une seconde. L'arbre est
    partagé entre les tests : le lire seulement, jamais le modifier (un test
    qui retire ou change un nœud doit faire son propre ast.parse)."""
    path = Path(path)
    stat = path.stat()
    return _parsed(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def _bound_names(nodes):
    """Noms liés quelque part dans `nodes` : paramètres, affectations,
    imports locaux, variables de boucle, d'exception et de compréhension,
    fonctions et classes définies."""
    bound = set()
    for top in nodes:
        for node in ast.walk(top):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
            elif isinstance(node, ast.arg):
                bound.add(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                bound.update(node.names)
    return bound


def _read_names(nodes):
    return {node.id for top in nodes for node in ast.walk(top)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}


def nodes(module, names):
    """Définitions de premier niveau de `module` nommées dans `names`."""
    tree = ast.parse((QT_DIR / module).read_text(encoding="utf-8"))
    found = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            found.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in names for t in node.targets):
            found.append(node)
        elif (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
              and node.target.id in names and node.value is not None):
            found.append(node)
    defined = {n.name for n in found if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    defined |= {t.id for n in found if isinstance(n, ast.Assign) for t in n.targets
                if isinstance(t, ast.Name)}
    defined |= {n.target.id for n in found if isinstance(n, ast.AnnAssign)}
    absent = set(names) - defined
    assert not absent, f"{module} : définitions introuvables {sorted(absent)}"
    return found


def _check_and_run(module, found, deps):
    missing = (_read_names(found) - _bound_names(found) - set(deps)
               - set(dir(builtins)))
    assert not missing, (f"{module} : dépendances absentes de l'espace de noms du test "
                         f"{sorted(missing)}")
    ns = dict(deps)
    exec(compile(ast.Module(body=found, type_ignores=[]), module, "exec"), ns)
    return ns


def load(module, names, **deps):
    """Espace de noms où les définitions `names` de `module` sont exécutées."""
    return _check_and_run(module, nodes(module, names), deps)


def load_methods(module, class_name, methods, **deps):
    """Classe `class_name` réduite aux méthodes `methods`, sans ses classes de
    base (mixin de fenêtre Qt) : le test l'hérite pour fournir le reste de
    l'objet (attributs, méthodes de l'interface)."""
    tree = ast.parse((QT_DIR / module).read_text(encoding="utf-8"))
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name),
               None)
    assert cls is not None, f"{module} : classe {class_name} introuvable"
    body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in methods]
    absent = set(methods) - {n.name for n in body}
    assert not absent, f"{module} : méthodes introuvables {sorted(absent)}"
    fields = {"type_params": []} if "type_params" in ast.ClassDef._fields else {}
    reduced = ast.copy_location(ast.ClassDef(name=class_name, bases=[], keywords=[], body=body,
                                             decorator_list=[], **fields), cls)
    return _check_and_run(module, [reduced], deps)[class_name]
