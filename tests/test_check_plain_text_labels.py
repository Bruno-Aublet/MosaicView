"""Garde-fou (scan AST statique, sans Qt) : un texte venu d'un fichier externe
(métadonnées d'un CBZ, description ComicVine, nom de membre d'archive) ne doit
jamais être affiché par un QLabel en détection automatique du format.

Un texte qui commence par une balise y est rendu en HTML, et une
<img src="\\\\serveur\\..."> fait contacter ce serveur par Windows (envoi du hash
NTLM du mot de passe de session) au simple affichage. Règle : CLAUDE.md,
section Sécurité, n° 3.

- Règle générale : jamais QLabel(str(...)) — une valeur brute convertie en
  texte, affichée sans format imposé.
- Affichages connus de texte externe : Qt.PlainText imposé AVANT le premier
  setText (sinon le HTML serait analysé, et l'image chargée, une première fois),
  et valeur échappée (quote=True) dans le lien Web de l'onglet Métadonnées.
  Fenêtre « Fichiers ignorés » (archive_loader._MessageDialog) : ses lignes
  commencent par un nom de membre d'archive.
- Jamais QLabel(f"…{valeur}…") hors des cas listés dans ALLOWED_FSTRING_LABELS,
  chacun justifié : un préfixe (« • ») ne protège pas, Qt reconnaît une
  balise dans toute la première ligne du texte (Qt::mightBeRichText), pas
  seulement à son début.
- Noms de pages affichés (visionneuse, résumé du redressement, pages hors
  norme du redimensionnement) : Qt.PlainText posé sur le label avant son
  premier setText.
"""
import ast
from pathlib import Path

import ast_loader

ROOT = Path(__file__).resolve().parent.parent
QT_DIR = ROOT / "modules" / "qt"


def _tree(name):
    return ast_loader.module_tree(QT_DIR / name)


def _method(tree, class_name, method_name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item
    raise AssertionError(f"{class_name}.{method_name} introuvable")


def _calls(node, attr):
    return sorted((c for c in ast.walk(node)
                   if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                   and c.func.attr == attr),
                  key=lambda c: (c.lineno, c.col_offset))


def _is_plain_text(call):
    return any(isinstance(a, ast.Attribute) and a.attr == "PlainText" for a in call.args)


def _assert_plain_text_before_set_text(node, where):
    formats = [c for c in _calls(node, "setTextFormat") if _is_plain_text(c)]
    assert formats, f"{where} : setTextFormat(Qt.PlainText) absent"
    texts = _calls(node, "setText")
    if texts:
        first_format = (formats[0].lineno, formats[0].col_offset)
        first_text = (texts[0].lineno, texts[0].col_offset)
        assert first_format < first_text, f"{where} : PlainText posé après le premier setText"


def test_no_qlabel_built_from_a_raw_value():
    offenders = []
    for path in sorted(QT_DIR.glob("*.py")):
        for node in ast.walk(ast_loader.module_tree(path)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "QLabel" and node.args
                    and isinstance(node.args[0], ast.Call)
                    and isinstance(node.args[0].func, ast.Name)
                    and node.args[0].func.id == "str"):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "QLabel(str(...)) affiche une valeur brute en détection automatique du "
        "format : créer le label, setTextFormat(Qt.PlainText), puis setText — "
        + ", ".join(offenders))


def test_metadata_values_are_plain_text():
    _assert_plain_text_before_set_text(
        _method(_tree("tabs_qt.py"), "_SelectableLabel", "__init__"),
        "tabs_qt._SelectableLabel.__init__")


def test_metadata_web_link_is_escaped_with_quotes():
    refresh = _method(_tree("tabs_qt.py"), "MetadataTab", "refresh")
    escapes = [c for c in _calls(refresh, "escape")
               if any(k.arg == "quote" and isinstance(k.value, ast.Constant) and k.value.value
                      for k in c.keywords)]
    assert escapes, "MetadataTab.refresh : lien Web sans html.escape(..., quote=True)"


def test_archive_member_names_are_plain_text():
    tree = _tree("archive_loader.py")
    _assert_plain_text_before_set_text(_method(tree, "_MessageDialog", "__init__"),
                                       "archive_loader._MessageDialog.__init__")


# (fichier, fonction qualifiée) -> raison : QLabel construit par une f-string
ALLOWED_FSTRING_LABELS = {
    ("animated_gif_dialog_qt.py", "_ThumbWidget.__init__"): "numéro d'image calculé",
    ("batch_dialogs_qt.py", "_add_dir_links"):
        "chemin de dossier du disque (Windows interdit < et > dans les noms)",
    ("file_operations_qt.py", "ThumbnailSavedDialog.__init__"):
        "chemin de dossier du disque (Windows interdit < et > dans les noms)",
    ("library_window.py", "LibraryWindow._populate_meta"): "libellé traduit",
    ("tabs_qt.py", "MetadataTab.refresh"): "valeur échappée par html.escape(quote=True)",
}


def _fstring_labels(tree):
    out = []

    def walk(node, qual):
        for child in ast.iter_child_nodes(node):
            q = qual + [child.name] if isinstance(child, (ast.FunctionDef, ast.ClassDef)) else qual
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                    and child.func.id == "QLabel" and child.args
                    and isinstance(child.args[0], ast.JoinedStr)
                    and any(isinstance(v, ast.FormattedValue) for v in child.args[0].values)):
                out.append((".".join(q), child.lineno))
            walk(child, q)
    walk(tree, [])
    return out


def test_no_qlabel_built_from_an_fstring_with_a_value():
    found = {(path.name, qual): line
             for path in sorted(QT_DIR.glob("*.py"))
             for qual, line in _fstring_labels(ast_loader.module_tree(path))}
    bad = [f"{f}:{line} ({q})" for (f, q), line in found.items() if (f, q) not in ALLOWED_FSTRING_LABELS]
    assert not bad, (
        "QLabel(f\"…{valeur}…\") affiche la valeur en détection automatique du format "
        "(un nom de membre d'archive serait lu en HTML) : créer le label, "
        "setTextFormat(Qt.PlainText), puis setText — " + ", ".join(bad))
    stale = [k for k in ALLOWED_FSTRING_LABELS if k not in found]
    assert not stale, f"Exceptions sans QLabel correspondant : {stale}"


def _label_set_plain_before_text(func, target):
    """`target` (nom ou attribut, ex. self._name_label) reçoit
    setTextFormat(Qt.PlainText) dans `func`, avant tout setText sur lui."""
    def on_target(calls):
        return [c for c in calls if ast.unparse(c.func.value) == target]
    formats = [c for c in on_target(_calls(func, "setTextFormat")) if _is_plain_text(c)]
    texts = on_target(_calls(func, "setText"))
    if not formats:
        return False
    return not texts or (formats[0].lineno, formats[0].col_offset) < (texts[0].lineno, texts[0].col_offset)


def test_page_names_are_plain_text():
    cases = [
        ("image_viewer_qt.py", "ImageViewer", "__init__", "self._name_label"),
        ("deskew_qt.py", "_DeskewSummaryDialog", "__init__", "lbl"),
        ("resize_dialog_qt.py", "OutlierDialog", "_build_page_widgets", "name_lbl"),
    ]
    bad = [f"{f} {c}.{m} ({t})" for f, c, m, t in cases
           if not _label_set_plain_before_text(_method(_tree(f), c, m), t)]
    assert not bad, "Nom de page affiché sans Qt.PlainText avant setText : " + ", ".join(bad)


def test_plain_text_check_detects_a_missing_format():
    func = ast.parse("def f():\n    lbl = QLabel()\n    lbl.setText(name)\n"
                     "    lbl.setTextFormat(Qt.PlainText)\n").body[0]
    assert not _label_set_plain_before_text(func, "lbl")
    assert _fstring_labels(ast.parse("def g(n):\n    QLabel(f'• {n}')\n")) == [("g", 2)]


def test_library_preview_metadata_is_plain_text():
    tree = _tree("library_window.py")
    builders = [f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
                and any(isinstance(n, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "_META_FIELDS" for t in n.targets)
                    for n in ast.walk(f))]
    assert builders, "library_window : fonction qui affiche _META_FIELDS introuvable"
    for builder in builders:
        _assert_plain_text_before_set_text(builder, f"library_window.{builder.name}")
