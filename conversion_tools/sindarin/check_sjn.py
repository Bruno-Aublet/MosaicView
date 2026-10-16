"""Contrôle lexical sindarin d'un bloc de traductions (ou d'un fichier de locale entier).

Usage : python check_sjn.py <eldamo_sjn_index.tsv> <vocab_sjn.tsv> <bloc.json> <rapport.txt>
- <bloc.json> : {"chemin.de.clé": "valeur"} ou un JSON imbriqué (locale complète).
- Affiche le nombre d'anomalies (0 = bloc accepté) ; le détail est écrit en UTF-8 dans <rapport.txt>.

Un mot est accepté s'il est :
1. dans `ALLOWED` (noms propres, sigles, formats, touches) ;
2. une forme déclarée dans `vocab_sjn.tsv` (pluriels, composés, formes fléchies), dont chaque
   élément doit lui-même exister dans l'index Eldamo ;
3. un mot de l'index Eldamo (sindarin, néo-sindarin ou noldorin non déconseillé), au besoin
   après avoir défait une mutation initiale (douce, nasale, mixte : `i balt` → `palt`) et/ou
   retiré une terminaison verbale (`edro` → `edra-`).
Un mot de `FORBIDDEN` (faux sens vérifiés, voir lexique section 1) est toujours refusé.
"""
import json
import re
import sys
import unicodedata

ALLOWED = {
    "MosaicView", "ComicVine", "ComicInfo", "Windows", "Explorer", "GitHub", "PayPal", "Ctrl",
    "Shift", "Alt", "Suppr", "Del", "Esc", "Echap", "Échap", "Enter", "Entrée", "Tab", "TrueType",
    "pIqaD", "Tengwar", "Tengwa", "Webtoon", "webtoon", "PyMuPDF", "YACReader", "CDisplayEx",
    "Excel", "Distillat", "Gemini", "Flash", "Claude", "Code", "UnRAR", "Zip", "WIA", "eSCL",
    "Glaemscribe", "Paul", "Strack", "Eldamo", "boQwI", "DPAPI", "API", "URL", "JSONP", "mvdb",
    "gamma", "bit", "bitmap", "pixel", "pixels", "px", "Unsharp", "Mask", "NFO", "nfo", "ISO",
    "GTIN", "Wilhelm", "Klingon", "Sindarin", "Quenya", "Tolkien", "Okrand", "Mozilla", "Firefox",
    "Chrome", "Edge", "Google", "Gmail", "Hough", "Pillow", "Qt", "PySide", "Python", "Node",
    "Apache", "GPL", "GNU", "LGPL", "Igor", "Pavlov", "Alexander", "Roshal", "RARLAB", "Bruno",
    "Aublet", "config", "json", "txt", "xml", "ext", "epub", "jpeg", "jpg", "png", "webp", "avif",
    "gif", "bmp", "tif", "tiff", "ico", "cbz", "cbr", "cb7", "cbt", "pdf", "zip", "rar", "jfif",
    "Log", "scan", "pdftocbz", "Releases", "Assets", "Issue", "Issues", "fitz", "Calibri",
    "Arial", "Segoe", "UI", "DPI", "dpi", "RGB", "RGBA", "CMYK", "LA", "P", "L", "Mo", "Ko",
    "Go", "To", "o", "kB", "MB", "GB", "KB", "x", "X", "XXXXX", "Webp", "Jpeg", "Png", "Manga", "bits", "RARlab", "qolqoS", "Alcarin", "z", "v", "Copyright", "rarfile", "s", "fps", "cb", "pjpeg", "pjp", "win", "print", "pip", "install", "pywin", "clipboard", "Vibe", "Coding", "Anthropic", "xxx", "webcomics", "cbrtocbz", "imgtocbz", "Wikipedia",
    "manga",
}
# Noms des langues de l'aide (endonymes, traités comme noms propres) et nom de l'écriture latine.
ALLOWED |= {
    "Deutsch", "English", "al", "Arabiyya", "Hayeren", "Balgarski", "Zhongwen", "jiǎntǐ", "fántǐ",
    "Hangugeo", "Hrvatski", "Dansk", "Español", "Eesti", "Suomi", "Français", "Ellinika", "Hindi",
    "Magyar", "Bahasa", "Indonesia", "Gaeilge", "Íslenska", "Italiano", "Nihongo", "Latviešu",
    "Lietuvių", "Melayu", "Malti", "Nederlands", "Norsk", "Polski", "Português", "Română",
    "Slovenčina", "Slovenščina", "Svenska", "Tamil", "Čeština", "Thai", "Türkçe", "Ukrainska",
    "Tiếng", "Việt", "latin", "comicvine", "gamespot", "com", "xlsx",
}

FORBIDDEN = {
    "aew", "randir", "rendir", "taur", "haedh", "deleb", "harthad", "tell", "telleir", "aen",
    "thamb", "lath", "thala", "hannas", "host", "tach", "ambar", "embar", "thur",
    "nothrannath", "tirhor", "avedui", "adrevedui", "pado", "honom", "orthertha", "nedhoro",
    "govaded", "ceno", "cenor", "revail", "nudh", "nuidad", "bethleg", "bethlegin", "golover",
    "pedid", "thennad", "rlinn", "formen", "tengwa", "tenn", "tenna",
}

MUTATIONS = [
    ("", ""), ("ph", "p"), ("th", "t"), ("ch", "c"), ("mb", "b"), ("nd", "d"), ("ng", "g"),
    ("dh", "d"), ("lh", "l"), ("rh", "r"), ("b", "p"), ("d", "t"), ("g", "c"), ("v", "b"),
    ("v", "m"), ("m", "b"), ("n", "d"), ("h", "s"), ("h", "c"), ("l", "gl"), ("r", "gr"),
    ("w", "gw"), ("f", "p"), ("", "g"), ("ch", "h"),
]
VERB_ENDINGS = ["o", "a", "ad", "ed", "en", "nen", "nnen", "ol", "iel", "ar", "ir", "in", "im",
                "ant", "er", "ach", "ich", "ath", "atha", "ithon", "athon", "on", "i", "as", "ui", "n"]


def fold(s):
    s = s.lower().replace("ŷ", "y").replace("ý", "y")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


index_path, vocab_path, block_path, report_path = sys.argv[1:5]

lines = open(index_path, encoding="utf-8").read().splitlines()
cols = lines[0].split("\t")
words, stems = set(), set()
for line in lines[1:]:
    r = dict(zip(cols, line.split("\t")))
    if r["lang"] not in ("s", "ns", "n") or "déconseillé" in r["status"]:
        continue
    w = r["word"]
    for variant in [w] + [f.strip() for f in r["forms"].split(";")]:
        v = fold(variant).strip("-").replace("(", "").replace(")", "")
        if v and " " not in v:
            words.add(v)
    if w.endswith("-"):
        raw = fold(w).rstrip("-")
        # Partie optionnelle entre parenthèses (`tírad(a)-`) : les deux formes sont valides.
        for stem in {re.sub(r"\(([^)]*)\)", r"\1", raw), re.sub(r"\([^)]*\)", "", raw)}:
            stems.add(stem)
            if stem.endswith("ia"):
                stems.add(stem[:-2])
            if stem.endswith("a"):
                stems.add(stem[:-1])


def known(w):
    for mut, orig in MUTATIONS:
        if not w.startswith(mut):
            continue
        base = orig + w[len(mut):]
        if base in words or base in stems:
            return True
        for e in VERB_ENDINGS:
            if base.endswith(e) and base[: -len(e)] in stems:
                return True
    return False


# vocab_sjn.tsv : « forme <TAB> éléments Eldamo <TAB> note ». Une forme terminée par `-` est un
# verbe : elle accepte ensuite les terminaisons verbales. Les formes déclarées acceptent aussi
# les mutations initiales.
problems = []
declared_words, declared_stems = set(), set()
for n, line in enumerate(open(vocab_path, encoding="utf-8").read().splitlines(), 1):
    if not line.strip() or line.startswith("#"):
        continue
    parts = line.split("\t")
    form, lemmas = parts[0], parts[1].split()
    for lem in lemmas:
        if not known(fold(lem).strip("-")):
            problems.append(f"vocab ligne {n} : élément « {lem} » de « {form} » absent d'Eldamo")
    f = fold(form)
    if f.endswith("-"):
        declared_stems.add(f.rstrip("-"))
        if f.rstrip("-").endswith("a"):
            declared_stems.add(f.rstrip("-")[:-1])
    else:
        declared_words.add(f)
words |= declared_words
stems |= declared_stems
vocab = set()


def flat(d, p=""):
    for k, v in d.items():
        q = f"{p}.{k}" if p else k
        if isinstance(v, dict):
            yield from flat(v, q)
        else:
            yield q, v


data = json.load(open(block_path, encoding="utf-8"))
items = list(flat(data)) if any(isinstance(v, dict) for v in data.values()) else list(data.items())

STRIP = re.compile(r"\{[^}]*\}|<[^>]+>|&[a-z]+;|https?://\S+|\b[\w.-]+@[\w.-]+\b|\S+\.(?:json|txt|xml|exe|mvdb|cbz|ttf)\b")
TOKEN = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)*")
for key, value in items:
    if key in ("app_title",):
        continue
    for tok in TOKEN.findall(STRIP.sub(" ", value)):
        if tok in ALLOWED or tok.isupper():
            continue
        f = fold(tok)
        if f in FORBIDDEN:
            problems.append(f"{key} : « {tok} » interdit (faux sens)")
        elif f not in vocab and not known(f):
            problems.append(f"{key} : « {tok} » inconnu")

with open(report_path, "w", encoding="utf-8") as out:
    out.write("\n".join(problems) + ("\n" if problems else ""))
print(len(problems))
