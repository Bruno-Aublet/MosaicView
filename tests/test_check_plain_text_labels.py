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
  commencent par un nom de membre d'archive. Fenêtre d'erreur commune
  (dialogs_qt.ErrorDialog) : ses messages citent des noms et des exceptions.
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


def _escaped_names(method):
    """Noms affectés par html.escape(..., quote=True) dans `method`."""
    names = set()
    for node in ast.walk(method):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "escape"
                and any(k.arg == "quote" and isinstance(k.value, ast.Constant) and k.value.value
                        for k in node.value.keywords)):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def _href_values(method):
    """Valeurs insérées juste après 'href="' dans les f-strings de `method`."""
    values = []
    for node in ast.walk(method):
        if isinstance(node, ast.JoinedStr):
            parts = node.values
            for before, value in zip(parts, parts[1:]):
                if (isinstance(before, ast.Constant) and str(before.value).endswith('href="')
                        and isinstance(value, ast.FormattedValue)):
                    values.append(value.value)
    return values


def test_metadata_web_link_is_escaped_with_quotes():
    """Le lien Web est écrit par refresh, puis réécrit par _restyle (fin de
    refresh, thème, langue) : l'adresse est échappée dans les deux, attribut
    href compris."""
    tree = _tree("tabs_qt.py")
    for name in ("refresh", "_restyle"):
        method = _method(tree, "MetadataTab", name)
        escaped = _escaped_names(method)
        hrefs = _href_values(method)
        assert escaped, f"MetadataTab.{name} : lien Web sans html.escape(..., quote=True)"
        assert hrefs, f"MetadataTab.{name} : lien Web introuvable"
        unescaped = [ast.unparse(v) for v in hrefs
                     if not (isinstance(v, ast.Name) and v.id in escaped)]
        assert not unescaped, f"MetadataTab.{name} : href non échappé : {unescaped}"


def test_archive_member_names_are_plain_text():
    tree = _tree("archive_loader.py")
    _assert_plain_text_before_set_text(_method(tree, "_MessageDialog", "__init__"),
                                       "archive_loader._MessageDialog.__init__")


def test_error_dialog_is_plain_text():
    """La fenêtre d'erreur commune cite des noms de fichiers ou de pages et
    des messages d'exception : jamais interprétés comme du HTML."""
    _assert_plain_text_before_set_text(_method(_tree("dialogs_qt.py"), "ErrorDialog", "__init__"),
                                       "dialogs_qt.ErrorDialog.__init__")


def test_message_windows_are_plain_text():
    """Fenêtres de message communes : leurs messages citent des noms de
    fichiers ou de pages et des textes d'exception. Format toujours imposé,
    jamais détecté : sans lui, la protection ne tiendrait qu'à l'ordre des
    lignes du message (Qt ne cherche une balise que dans la première)."""
    tree = _tree("dialogs_qt.py")
    for name in ("MsgDialog", "ConfirmDialog", "QuestionYNCDialog", "ConfirmYNDialog"):
        _assert_plain_text_before_set_text(_method(tree, name, "__init__"),
                                           f"dialogs_qt.{name}.__init__")


# (fichier, fonction qualifiée) -> raison : InfoDialog(..., rich_text=True)
ALLOWED_RICH_INFO_DIALOGS = {
    ("library_window.py", "LibraryWindow"):
        "export Excel : lien vers le fichier écrit, chemin échappé (_export_msg)",
}


def test_info_dialog_is_plain_text_unless_asked():
    """InfoDialog : texte brut par défaut ; HTML seulement pour les appels
    déclarés, qui échappent ce qu'ils insèrent."""
    init = _method(_tree("dialogs_qt.py"), "InfoDialog", "__init__")
    defaults = dict(zip([a.arg for a in init.args.args][-len(init.args.defaults):],
                        init.args.defaults))
    assert isinstance(defaults.get("rich_text"), ast.Constant) and defaults["rich_text"].value is False
    [fmt] = _calls(init, "setTextFormat")
    assert ast.unparse(fmt.args[0]) == "Qt.RichText if rich_text else Qt.PlainText"
    found = set()
    for path in sorted(QT_DIR.glob("*.py")):
        tree = ast_loader.module_tree(path)
        for top in tree.body:
            for node in ast.walk(top):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "InfoDialog"
                        and any(k.arg == "rich_text" for k in node.keywords)):
                    found.add((path.name, getattr(top, "name", "<module>")))
    assert found == set(ALLOWED_RICH_INFO_DIALOGS), (
        "InfoDialog(..., rich_text=True) non déclaré, ou exception sans appel : "
        f"{sorted(found ^ set(ALLOWED_RICH_INFO_DIALOGS))}")


# (fichier, fonction qualifiée) -> raison : QLabel construit par une f-string
ALLOWED_FSTRING_LABELS = {
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
        # Fichier de macro (peut venir d'ailleurs) et noms de pages du rapport
        ("macro_tool_qt.py", "_MacroReadDialog", "__init__", "self._lbl_name"),
        ("macro_tool_qt.py", "_MacroReadDialog", "__init__", "self._lbl_desc"),
        ("macro_tool_qt.py", "_MacroReportDialog", "__init__", "lbl"),
    ]
    bad = [f"{f} {c}.{m} ({t})" for f, c, m, t in cases
           if not _label_set_plain_before_text(_method(_tree(f), c, m), t)]
    assert not bad, "Nom de page affiché sans Qt.PlainText avant setText : " + ", ".join(bad)


def test_plain_text_check_detects_a_missing_format():
    func = ast.parse("def f():\n    lbl = QLabel()\n    lbl.setText(name)\n"
                     "    lbl.setTextFormat(Qt.PlainText)\n").body[0]
    assert not _label_set_plain_before_text(func, "lbl")
    assert _fstring_labels(ast.parse("def g(n):\n    QLabel(f'• {n}')\n")) == [("g", 2)]


# --- Règle générale : tout label à texte variable déclare son format ------------

def _label_classes():
    """QLabel et ses sous-classes du projet : {nom: format posé par leur __init__}."""
    classes = {"QLabel": False}
    for path in sorted(QT_DIR.glob("*.py")):
        for node in ast.walk(ast_loader.module_tree(path)):
            if isinstance(node, ast.ClassDef) and any(
                    isinstance(b, ast.Name) and b.id == "QLabel" for b in node.bases):
                init = next((i for i in node.body
                             if isinstance(i, ast.FunctionDef) and i.name == "__init__"), None)
                classes[node.name] = init is not None and bool(_calls(init, "setTextFormat"))
    return classes


def _fixed_text(expr):
    """Texte qui ne peut contenir que des traductions de l'application :
    constante, _("clé") / _wt("clé") sans paramètre, ou assemblage de ceux-ci.
    Un paramètre de traduction, un nom ou une valeur calculée peut porter un
    texte venu d'ailleurs."""
    if isinstance(expr, ast.Constant):
        return True
    if isinstance(expr, ast.Call):
        func = expr.func
        if isinstance(func, ast.Name) and func.id in ("_", "_wt"):
            return not expr.keywords
        if isinstance(func, ast.Attribute) and func.attr in ("upper", "lower", "strip"):
            return not expr.args and _fixed_text(func.value)
        return False
    if isinstance(expr, ast.JoinedStr):
        return all(_fixed_text(v.value) for v in expr.values if isinstance(v, ast.FormattedValue))
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        return _fixed_text(expr.left) and _fixed_text(expr.right)
    return False


def _labels_without_format(tree, classes):
    """[(propriétaire, label, ligne)] : labels qui reçoivent un texte variable
    (setText, ou texte passé au constructeur) sans setTextFormat. Un label est
    désigné par son expression (self._x, lbl) dans la classe ou la fonction de
    premier niveau qui le crée."""
    bad = []
    for top in tree.body:
        if not isinstance(top, (ast.ClassDef, ast.FunctionDef)):
            continue
        created, formats = {}, {}
        for node in ast.walk(top):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name) and node.value.func.id in classes):
                for target in node.targets:
                    created.setdefault(ast.unparse(target), node.value)
        for call in _calls(top, "setTextFormat"):
            formats.setdefault(ast.unparse(call.func.value), call)
        for name, ctor in created.items():
            if classes[ctor.func.id] or name in formats:
                continue
            # Texte au constructeur (un nom seul y est le parent) : jamais
            # précédé d'un format
            if (ctor.func.id == "QLabel" and ctor.args and not isinstance(ctor.args[0], ast.Name)
                    and not _fixed_text(ctor.args[0])):
                bad.append((top.name, name, ctor.lineno))
                continue
            texts = [c for c in _calls(top, "setText")
                     if ast.unparse(c.func.value) == name and c.args and not _fixed_text(c.args[0])]
            if texts:
                bad.append((top.name, name, texts[0].lineno))
    return bad


def test_every_label_with_variable_text_declares_its_format():
    """Un label qui affiche un texte variable (nom, chemin, message construit,
    paramètre de traduction) déclare son format : Qt.PlainText, ou
    Qt.RichText pour un lien voulu dont les valeurs sont échappées. En
    détection automatique, une balise venue d'un nom de page ou d'un fichier
    serait rendue en HTML (CLAUDE.md, Sécurité n° 3)."""
    classes = _label_classes()
    bad = [f"{path.name}:{line} {owner} {name}"
           for path in sorted(QT_DIR.glob("*.py"))
           for owner, name, line in _labels_without_format(ast_loader.module_tree(path), classes)]
    assert not bad, ("Label à texte variable sans setTextFormat (Qt.PlainText, ou Qt.RichText "
                     "pour un lien aux valeurs échappées) : " + ", ".join(bad))


def test_variable_text_check_detects_a_missing_format():
    tree = ast.parse(
        "class A:\n"
        "    def f(self, name):\n"
        "        self._a = QLabel()\n"
        "        self._a.setText(_('k', name=name))\n"
        "        self._b = QLabel()\n"
        "        self._b.setText(_('k'))\n"
        "        self._c = QLabel()\n"
        "        self._c.setTextFormat(Qt.PlainText)\n"
        "        self._c.setText(name)\n"
        "        d = QLabel(f'{name}')\n")
    found = [(name, line) for _o, name, line in _labels_without_format(tree, {"QLabel": False})]
    assert found == [("self._a", 4), ("d", 10)]


def test_library_preview_metadata_is_plain_text():
    tree = _tree("library_window.py")
    builders = [f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
                and any(isinstance(n, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "_META_FIELDS" for t in n.targets)
                    for n in ast.walk(f))]
    assert builders, "library_window : fonction qui affiche _META_FIELDS introuvable"
    for builder in builders:
        _assert_plain_text_before_set_text(builder, f"library_window.{builder.name}")
