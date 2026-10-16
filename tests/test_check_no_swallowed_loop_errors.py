"""Aucune erreur avalée en silence dans une boucle (scan AST de modules/qt/ et
MosaicView.py).

Un `except Exception: continue` (ou `except:` / `except BaseException:`) dans
une boucle fait disparaître sans trace l'élément en cours : une page qui ne se
rend pas, ne se convertit pas ou ne s'écrit pas manque alors au résultat sans
que personne le sache. C'est la cause commune de plusieurs pertes de pages
(PDF, conversion de format, import de métadonnées en lot).

Une erreur dans une boucle doit être remontée (liste d'échecs, message,
exception). Un cas réellement sans conséquence se déclare dans ALLOWED, avec
la raison : (fichier, fonction englobante) → nombre d'occurrences admises.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = sorted((ROOT / "modules" / "qt").glob("*.py")) + [ROOT / "MosaicView.py"]

ALLOWED = {
    # Détection de la résolution d'une page PDF : une image dont les
    # dimensions sont illisibles ne compte simplement pas dans le calcul.
    ("pdf_page_images.py", "best_page_image"): 1,
    # Métadonnées <Pages> : une image illisible garde ses anciennes dimensions.
    ("comic_info.py", "update_page_entries_in_xml_data"): 1,
    # Recherche du plateau du scanner parmi les éléments WIA du périphérique.
    ("scan_wia.py", "_find_scan_item"): 1,
    # Fenêtre des dimensions aberrantes : une page illisible n'y est pas montrée
    # (elle ne peut pas être redimensionnée).
    ("resize_dialog_qt.py", "OutlierDialog._build_page_widgets"): 1,
    # Export caché des vignettes (Ctrl+Alt+T) : le résumé compte les fichiers
    # réellement écrits.
    ("MosaicView.py", "MainWindow._export_thumbnails"): 1,
}


def _is_broad(handler):
    if handler.type is None:
        return True
    return isinstance(handler.type, ast.Name) and handler.type.id in ("Exception", "BaseException")


def swallowed_in_loops():
    """{(fichier, fonction englobante): nombre} des `except` larges dont le seul
    contenu est `continue`, situés dans une boucle."""
    found = {}
    for path in FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def visit(node, scope, in_loop):
            for child in ast.iter_child_nodes(node):
                child_scope, child_loop = scope, in_loop
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    child_scope = f"{scope}.{child.name}" if scope else child.name
                    child_loop = False
                elif isinstance(child, (ast.For, ast.AsyncFor, ast.While)):
                    child_loop = True
                if (isinstance(child, ast.ExceptHandler) and in_loop and _is_broad(child)
                        and len(child.body) == 1 and isinstance(child.body[0], ast.Continue)):
                    key = (path.name, scope)
                    found[key] = found.get(key, 0) + 1
                visit(child, child_scope, child_loop)

        visit(tree, "", False)
    return found


def test_no_new_swallowed_error_in_loops():
    found = swallowed_in_loops()
    unexpected = {key: count for key, count in found.items()
                  if count > ALLOWED.get(key, 0)}
    assert not unexpected, (
        "Erreur avalée dans une boucle (`except Exception: continue`) : remonter "
        "l'échec (liste d'échecs, message) au lieu de l'ignorer, ou, si c'est "
        f"réellement sans conséquence, le déclarer dans ALLOWED avec la raison : {unexpected}")


def test_allowed_list_is_up_to_date():
    """Une exception corrigée ou déplacée doit être retirée d'ALLOWED, pour
    que la liste ne couvre jamais un nouveau cas par erreur."""
    found = swallowed_in_loops()
    stale = {key: count for key, count in ALLOWED.items() if found.get(key, 0) < count}
    assert not stale, f"Entrées d'ALLOWED qui ne correspondent plus au code : {stale}"
