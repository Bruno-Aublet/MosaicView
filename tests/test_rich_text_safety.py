"""Texte mis en forme venu de l'extérieur : jamais de ressource externe lue
par Qt (modules/qt/rich_text_safety.py, outil texte de la visionneuse).

Qt lit lui-même les fichiers que désigne un HTML (image, feuille de style,
fond) : une adresse file://serveur/partage/x.png devient un chemin réseau,
et Windows envoie au serveur le hash NTLM du compte, sans clic. Il suffisait
de coller dans l'outil texte un passage d'une page web piégée, ou de rejouer
une macro piégée.

- safe_rich_text ne garde que la mise en forme (liste blanche de balises et
  d'attributs), et rend en texte échappé tout ce qu'elle ne reconnaît pas ;
- le collage et le dépôt dans un bloc de l'outil texte passent par elle
  (_RichTextOverlay.insertFromMimeData, extraite par tests/ast_loader.py) ;
- garde-fou statique sur modules/qt : tout setHtml/insertHtml passe par
  safe_rich_text (sauf les documents de l'application elle-même), et tout
  champ QTextEdit n'accepte que le texte brut, ou filtre ce qu'on y colle.
"""
import ast
import os
import re

import pytest

import ast_loader
from modules.qt.rich_text_safety import safe_rich_text

QT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "modules", "qt")

# HTML tel que l'outil texte l'écrit (QTextEdit.toHtml) : rendu identique
QT_HTML = (
    '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN" "http://www.w3.org/TR/REC-html40/strict.dtd">\n'
    '<html><head><meta name="qrichtext" content="1" /><meta charset="utf-8" />'
    '<style type="text/css">\np, li { white-space: pre-wrap; }\n'
    'li.unchecked::marker { content: "\\2610"; }\n</style></head>'
    '<body style=" font-family:\'Segoe UI\'; font-size:9pt; font-weight:400;">\n'
    '<p style=" margin-top:0px; -qt-block-indent:0; text-indent:0px;">'
    '<span style=" font-family:\'Arial\'; font-size:36pt; font-weight:700; color:#ff0000;">'
    'Aaaa  bb &lt; cc &amp; dd</span></p></body></html>'
)

# Chaque forme désigne une ressource que Qt irait lire
TRAPS = [
    '<p>Aaaa <img src="file://serveur/partage/x.png" width="200"></p>',
    '<IMG SRC=file://serveur/partage/x.png>',
    '<img/src="file://serveur/partage/x.png">',
    '<img src="\\\\serveur\\partage\\x.png">',
    '<img src="file://serveur/partage/x.png"',
    '<<img src="file://serveur/partage/x.png">',
    '<link rel="stylesheet" href="file://serveur/partage/a.css">',
    '<style>@import "file://serveur/partage/a.css";</style>',
    '<style>p { background: url(file://serveur/partage/x.png) }</style>',
    '<style>p { background: u\\72l(file://serveur/partage/x.png) }</style>',
    '<style>p { background: \\75\\72\\6c(file://serveur/partage/x.png) }</style>',
    '<p style="color:red; background-image: url(file://serveur/partage/x.png)">t</p>',
    '<p style="background: u/**/rl(file://serveur/partage/x.png)">t</p>',
    '<p style="background: &#117;rl(file://serveur/partage/x.png)">t</p>',
    '<li style="list-style-image: url(file://serveur/partage/x.png)">t</li>',
    '<table background="file://serveur/partage/x.png"><tr><td>t</td></tr></table>',
    '<body background="file://serveur/partage/x.png">t</body>',
    '<td style="background-image:url(\'file://serveur/partage/x.png\')">t</td>',
    '<svg><image href="file://serveur/partage/x.png"/></svg>',
    '<object data="file://serveur/partage/x.png">t</object>',
    '<a href="file://serveur/partage/x.png">t</a>',
    '<base href="file://serveur/partage/"><img src="x.png">',
    '<style>p {}</style><p>t</p></style><img src="file://serveur/partage/x.png">',
    '<style>p { color: red } <img src="file://serveur/partage/x.png"> </style>',
]


@pytest.mark.parametrize("trap", TRAPS)
def test_no_resource_is_left_for_qt(trap):
    """Tout « < » de la sortie ouvre une vraie balise : le texte est échappé,
    et un contenu de <style> qui en contient un est retiré."""
    out = safe_rich_text(trap)
    tags = re.findall(r"<[^>]*>", out)
    for tag in tags:
        assert re.match(r"</?(p|li|table|tr|td|body|style)\b", tag), (trap, out)
    tags_text = " ".join(tags).lower()
    for word in ("src=", "href=", "background", "data="):
        assert word not in tags_text, (trap, out)
    for word in ("url(", "@import"):
        assert word not in out.lower(), (trap, out)


def test_unrecognised_tag_becomes_escaped_text():
    out = safe_rich_text('<<img src="file://serveur/partage/x.png">')
    assert "<img" not in out.lower()


def test_unfinished_tag_becomes_escaped_text():
    out = safe_rich_text('Aaaa <img src="file://serveur/partage/x.png"')
    assert out.startswith("Aaaa ") and "<img" not in out.lower()


def test_text_tool_html_keeps_its_formatting():
    out = safe_rich_text(QT_HTML)
    assert '<meta name="qrichtext" content="1" />' in out
    assert "white-space: pre-wrap;" in out and '"\\2610"' in out
    assert "font-size:36pt" in out and "font-weight:700" in out and "color:#ff0000" in out
    assert "Arial" in out and "Segoe UI" in out
    assert "Aaaa  bb &lt; cc &amp; dd" in out


def test_formatting_is_kept_without_the_resource():
    out = safe_rich_text('<p style="color:red; background-image: url(file://serveur/x.png); '
                         'font-size:12pt">t</p>')
    assert out == '<p style="color:red; font-size:12pt">t</p>'


def test_hidden_content_is_removed():
    assert safe_rich_text("<script>Bbbb</script><title>Cccc</title>Aaaa") == "Aaaa"


def test_comments_and_doctype_are_removed():
    assert safe_rich_text("<!DOCTYPE html><!-- Bbbb -->Aaaa") == "Aaaa"


def test_empty_input():
    assert safe_rich_text("") == "" and safe_rich_text(None) == ""


# --- Collage et dépôt dans un bloc de l'outil texte -------------------------------

Overlay = ast_loader.load_methods("text_tool_qt.py", "_RichTextOverlay",
                                  {"insertFromMimeData"}, safe_rich_text=safe_rich_text)


class FakeOverlay(Overlay):
    def __init__(self):
        self.inserted = []

    def insertHtml(self, text):
        self.inserted.append(("html", text))

    def insertPlainText(self, text):
        self.inserted.append(("text", text))

    def ensureCursorVisible(self):
        pass


class Mime:
    def __init__(self, html=None, text=None):
        self._html, self._text = html, text

    def hasHtml(self):
        return self._html is not None

    def html(self):
        return self._html

    def hasText(self):
        return self._text is not None

    def text(self):
        return self._text


def test_pasted_html_is_filtered():
    overlay = FakeOverlay()
    overlay.insertFromMimeData(Mime(html=TRAPS[0], text="Aaaa"))
    assert overlay.inserted == [("html", "<p>Aaaa </p>")]


def test_pasted_plain_text_is_inserted_as_text():
    overlay = FakeOverlay()
    overlay.insertFromMimeData(Mime(text='<img src="file://serveur/x.png">'))
    assert overlay.inserted == [("text", '<img src="file://serveur/x.png">')]


def test_paste_without_text_inserts_nothing():
    overlay = FakeOverlay()
    overlay.insertFromMimeData(Mime())
    assert overlay.inserted == []


# --- Garde-fou statique ------------------------------------------------------------

# Documents de l'application elle-même, affichés en lecture seule (QTextBrowser)
ALLOWED_FIXED_HTML = {
    "changelog_dialog_qt.py": "CHANGELOG.md livré avec l'application",
    "license_dialog_qt.py": "textes de licence livrés avec l'application",
    "user_guide_qt.py": "mode d'emploi, textes des fichiers de traduction",
}


def _modules():
    for name in sorted(os.listdir(QT_DIR)):
        if name.endswith(".py"):
            path = os.path.join(QT_DIR, name)
            with open(path, encoding="utf-8") as f:
                source = f.read()
            yield name, source, ast.parse(source)


def _is_call_to(node, name):
    func = getattr(node, "func", None)
    return isinstance(node, ast.Call) and (
        (isinstance(func, ast.Name) and func.id == name)
        or (isinstance(func, ast.Attribute) and func.attr == name))


def test_html_given_to_qt_goes_through_safe_rich_text():
    offenders = []
    for name, _source, tree in _modules():
        if name in ALLOWED_FIXED_HTML:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("setHtml", "insertHtml")):
                if not (node.args and _is_call_to(node.args[0], "safe_rich_text")):
                    offenders.append(f"{name}:{node.lineno}")
    assert offenders == [], f"HTML donné à Qt sans safe_rich_text : {offenders}"


def _defines(class_node, method):
    return any(isinstance(item, ast.FunctionDef) and item.name == method for item in class_node.body)


def test_every_text_field_refuses_or_filters_pasted_html():
    """Un QTextEdit accepte par défaut le HTML collé : il n'accepte que le
    texte brut (setAcceptRichText(False)), ou filtre lui-même ce qu'on y
    colle (insertFromMimeData et loadResource)."""
    offenders = []
    for name, source, tree in _modules():
        functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                    isinstance(base, ast.Name) and base.id == "QTextEdit" for base in node.bases):
                segment = ast.get_source_segment(source, node)
                if "setAcceptRichText(False)" not in segment and not (
                        _defines(node, "insertFromMimeData") and _defines(node, "loadResource")):
                    offenders.append(f"{name}:{node.name}")
            elif _is_call_to(node, "QTextEdit") and isinstance(node.func, ast.Name):
                owner = [f for f in functions if f.lineno <= node.lineno <= f.end_lineno]
                segment = ast.get_source_segment(source, owner[-1]) if owner else source
                if "setAcceptRichText(False)" not in segment:
                    offenders.append(f"{name}:{node.lineno}")
    assert offenders == [], f"champ de texte qui accepte le HTML collé : {offenders}"
