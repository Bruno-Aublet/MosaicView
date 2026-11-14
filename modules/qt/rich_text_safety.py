"""Texte mis en forme venu de l'extérieur (collage, dépôt, macro) ramené à sa
seule mise en forme, sans aucune ressource externe.

Qt (QTextDocument) lit lui-même les fichiers que désigne un HTML : image
(<img src>), feuille de style (<link>, @import, lue dès l'analyse du HTML) ou
fond (attribut background, url(...) d'un style). Une adresse
file://serveur/partage/x.png devient le chemin réseau \\\\serveur\\partage\\x.png,
et Windows envoie à ce serveur l'empreinte (hash NTLM) du mot de passe du
compte, sans aucun clic : il suffit de coller dans l'outil texte un passage
d'une page web piégée, ou de rejouer une macro piégée.

safe_rich_text ne laisse passer qu'une liste blanche de balises et
d'attributs de mise en forme, réécrits à partir de l'analyse faite par
Python : tout ce qu'elle ne reconnaît pas comme une balise permise devient du
texte échappé, jamais du HTML confié à Qt. Les styles qui pourraient désigner
une ressource sont retirés. Module sans Qt, testé par
tests/test_rich_text_safety.py.
"""
import html
import re
from html.parser import HTMLParser

# Balises de mise en forme du texte riche de Qt
_ALLOWED_TAGS = frozenset({
    "html", "head", "body", "meta", "style",
    "p", "br", "span", "div", "font", "b", "strong", "i", "em", "u", "s",
    "strike", "del", "ins", "sub", "sup", "small", "big", "center", "nobr",
    "h1", "h2", "h3", "h4", "h5", "h6", "pre", "code", "tt", "kbd", "samp",
    "var", "dfn", "cite", "q", "address", "blockquote", "hr",
    "ul", "ol", "li", "dl", "dt", "dd",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption",
})
_VOID_TAGS = frozenset({"br", "hr", "meta"})
# Contenu jamais affiché comme texte : retiré avec sa balise
_DROPPED_CONTENT_TAGS = frozenset({
    "script", "title", "noscript", "template", "textarea", "xmp", "iframe",
    "object", "applet", "noframes", "svg", "math",
})
# Attributs de mise en forme ; aucun ne désigne une ressource
_ALLOWED_ATTRS = frozenset({
    "style", "align", "valign", "color", "face", "size", "dir", "width",
    "height", "colspan", "rowspan", "cellpadding", "cellspacing", "border",
    "bgcolor", "class", "type", "start", "value",
})

_CSS_COMMENT = re.compile(r"/\*.*?(?:\*/|$)", re.S)
_CSS_ESCAPE = re.compile(r"\\([0-9a-fA-F]{1,6})\s?|\\(.)", re.S)
# Ce qui peut faire lire une ressource à Qt depuis une feuille de style
_CSS_RESOURCE = re.compile(r"url\(|@import|expression\(|image")


def _css_plain(text):
    """Texte CSS tel que Qt le comprendrait au plus large : commentaires
    retirés, échappements (\\75 pour « u ») décodés, sans espaces, en
    minuscules — un « u\\rl( » ou un « u/**/rl( » y redevient « url( »."""
    text = _CSS_COMMENT.sub("", text)

    def _unescape(match):
        if match.group(1):
            try:
                return chr(int(match.group(1), 16))
            except (ValueError, OverflowError):
                return ""
        return match.group(2)

    text = _CSS_ESCAPE.sub(_unescape, text)
    return re.sub(r"\s+", "", text).lower()


def _safe_style_attr(value):
    """Déclarations d'un attribut style, sans celles qui désignent une
    ressource (background-image, list-style-image, url(...)…)."""
    kept = [declaration for declaration in value.split(";")
            if declaration.strip() and not _CSS_RESOURCE.search(_css_plain(declaration))]
    return ";".join(kept)


def _safe_style_block(content):
    """Contenu d'une balise <style> : gardé seulement s'il ne désigne aucune
    ressource. Un « < » est refusé aussi : l'analyse de Qt pourrait y voir
    une balise que celle de Python a lue comme du texte de style."""
    if "<" in content or _CSS_RESOURCE.search(_css_plain(content)):
        return ""
    return content


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self._dropped_depth = 0
        self._in_style = False

    def _start(self, tag, attrs, self_closing):
        if tag in _DROPPED_CONTENT_TAGS:
            if not self_closing:
                self._dropped_depth += 1
            return
        if self._dropped_depth or tag not in _ALLOWED_TAGS:
            return
        if tag == "meta":
            # Seule la marque de texte riche de Qt est gardée
            if dict(attrs).get("name", "").lower() == "qrichtext":
                self.out.append('<meta name="qrichtext" content="1" />')
            return
        parts = [tag]
        for name, value in attrs:
            if name not in _ALLOWED_ATTRS or value is None:
                continue
            if name == "style":
                value = _safe_style_attr(value)
            elif _CSS_RESOURCE.search(_css_plain(value)):
                continue
            parts.append(f'{name}="{html.escape(value, quote=True)}"')
        closing = " /" if self_closing or tag in _VOID_TAGS else ""
        self.out.append(f"<{' '.join(parts)}{closing}>")
        if tag == "style" and not self_closing:
            self._in_style = True

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, True)

    def handle_endtag(self, tag):
        if tag in _DROPPED_CONTENT_TAGS:
            if self._dropped_depth:
                self._dropped_depth -= 1
            return
        if tag == "style":
            if not self._in_style:
                return
            self._in_style = False
        if self._dropped_depth or tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self._dropped_depth:
            return
        if self._in_style:
            self.out.append(_safe_style_block(data))
            return
        self.out.append(html.escape(data, quote=False))

    # Commentaires, <!DOCTYPE>, instructions de traitement, CDATA : retirés


def safe_rich_text(text):
    """HTML ramené à la mise en forme permise (voir en-tête du module), prêt
    à être donné à un QTextEdit ou un QTextDocument."""
    sanitizer = _Sanitizer()
    sanitizer.feed(text or "")
    sanitizer.close()
    return "".join(sanitizer.out)
