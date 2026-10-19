"""Aucune erreur avalée sans justification (scan statique de modules/qt/ et
MosaicView.py).

Un `except` large (`except Exception:`, `except BaseException:`, `except:` nu,
ou un tuple qui contient l'un d'eux) dont le seul contenu est `pass` fait
disparaître l'erreur sans trace. Ce n'est admis que si l'échec est sans effet
pour l'utilisateur, et la ligne du `pass` doit alors dire pourquoi :

    pass  # sans conséquence : <raison>   (objet détruit juste après, signal
                                           déjà déconnecté, temporaire…)
    pass  # repli : <raison>              (une valeur de repli prend le relais
                                           sans perte : icône par défaut…)

Une erreur que l'utilisateur doit connaître (page, fichier ou donnée qui
manque, original non supprimé, succès annoncé alors qu'une partie a échoué)
n'est jamais avalée : elle remonte par le mécanisme existant (fenêtre
« Fichiers ignorés », liste d'erreurs d'un lot, message). Règle : CLAUDE.md,
« Style et contenu du code écrit ». Dans une boucle, voir aussi
test_check_no_swallowed_loop_errors.py.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = sorted((ROOT / "modules" / "qt").glob("*.py")) + [ROOT / "MosaicView.py"]

MARKER = re.compile(r"#\s*(sans conséquence|repli)\s*:\s*\S")


def _is_broad(handler):
    names = []
    if handler.type is None:
        return True
    if isinstance(handler.type, ast.Tuple):
        names = handler.type.elts
    else:
        names = [handler.type]
    return any(isinstance(n, ast.Name) and n.id in ("Exception", "BaseException") for n in names)


def unjustified_passes(source):
    """Numéros de ligne des `pass` seuls d'un `except` large dont la ligne ne
    porte aucun des deux marqueurs."""
    lines = source.splitlines()
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if not _is_broad(handler):
                continue
            if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
                lineno = handler.body[0].lineno
                if not MARKER.search(lines[lineno - 1]):
                    found.append(lineno)
    return found


def test_every_silent_broad_except_is_justified():
    offenders = []
    for path in FILES:
        for lineno in unjustified_passes(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert not offenders, (
        "except large réduit à `pass` sans « # sans conséquence : <raison> » ni "
        "« # repli : <raison> » sur la ligne du pass — justifier, ou faire "
        "remonter l'erreur :\n  " + "\n  ".join(offenders))


def test_detector_recognises_marked_and_unmarked_cases():
    source = (
        "try:\n    a()\nexcept Exception:\n    pass\n"                                # 4 : sans marqueur
        "try:\n    b()\nexcept:\n    pass  # repli : valeur par défaut\n"             # 8 : marqué
        "try:\n    c()\nexcept (OSError, BaseException):\n    pass  # à voir\n"       # 12 : commentaire libre
        "try:\n    d()\nexcept ValueError:\n    pass\n"                               # 16 : pas large
        "try:\n    e()\nexcept Exception:\n    pass  # sans conséquence : fermé\n"    # 20 : marqué
        "try:\n    f()\nexcept Exception:\n    x = 1\n"                               # 24 : pas un pass seul
        "try:\n    g()\nexcept Exception:\n    pass  # repli :\n"                     # 28 : raison absente
    )
    assert unjustified_passes(source) == [4, 12, 28]
