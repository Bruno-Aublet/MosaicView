"""Contrôle lexical quenya d'un bloc de traductions (ou d'un fichier de locale entier).

Usage : python check_qya.py <eldamo_qya_index.tsv> <vocab_qya.tsv> <bloc.json> <rapport.txt>
- <bloc.json> : {"chemin.de.clé": "valeur"} ou un JSON imbriqué (locale complète).
- Affiche le nombre d'anomalies (0 = bloc accepté) ; le détail est écrit en UTF-8 dans <rapport.txt>.

Un mot est accepté s'il est :
1. dans `ALLOWED` (noms propres, sigles, formats, touches) ;
2. une forme déclarée dans `vocab_qya.tsv` (composés, dérivés construits), dont chaque élément
   doit lui-même exister dans l'index Eldamo ;
3. un mot de l'index Eldamo (quenya, néo-quenya ou middle quenya non déconseillé), ou une forme
   attestée de ce mot, au besoin après avoir retiré jusqu'à trois terminaisons quenya empilées
   (`SUFFIXES` : pluriels, cas, possessifs, pronoms sujets, temps et participes, dérivations).
   Le quenya n'a pas de mutation initiale : seule la fin du mot varie. Un nom en `-ë` fait son
   pluriel en `-i` (`essë` → `essi`) ; un verbe en `-a` perd son `a` devant `-uva`, `-ië`, `-ina`
   (`latya-` → `latyuva`) : la racine retrouvée est donc aussi testée avec `-a` et `-ë` ajoutés.
Un mot de `FORBIDDEN` (faux sens vérifiés, voir lexique section 1) est toujours refusé, même fléchi.
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
    "gamma", "Gamma", "Hexadecimal", "Alpha", "bit", "bitmap", "pixel", "pixels", "px", "Unsharp", "Mask", "NFO", "nfo", "ISO",
    "GTIN", "Wilhelm", "Klingon", "Sindarin", "Quenya", "Tolkien", "Okrand", "Mozilla", "Firefox",
    "Chrome", "Edge", "Google", "Gmail", "Hough", "Pillow", "Qt", "PySide", "Python", "Node",
    "Apache", "GPL", "GNU", "LGPL", "Igor", "Pavlov", "Alexander", "Roshal", "RARLAB", "Bruno",
    "Aublet", "config", "json", "txt", "xml", "ext", "epub", "jpeg", "jpg", "png", "webp", "avif",
    "gif", "bmp", "tif", "tiff", "ico", "cbz", "cbr", "cb7", "cbt", "pdf", "zip", "rar", "jfif",
    "Log", "scan", "pdftocbz", "Releases", "Assets", "Issue", "Issues", "fitz", "Calibri",
    "Arial", "Segoe", "UI", "DPI", "dpi", "RGB", "RGBA", "CMYK", "LA", "P", "L", "Mo", "Ko",
    "Go", "To", "o", "kB", "MB", "GB", "KB", "x", "X", "XXXXX", "Webp", "WebP", "Jpeg", "Png", "Manga",
    "bits", "RARlab", "qolqoS", "qaSpu", "Alcarin", "z", "v", "Copyright", "rarfile", "s", "fps",
    "cb", "pjpeg", "pjp", "win", "print", "pip", "install", "pywin", "clipboard", "Vibe", "Coding",
    "Anthropic", "xxx", "webcomics", "cbrtocbz", "imgtocbz", "Wikipedia", "manga", "Icon", "icon",
    "extension", "Extension", "Eldar", "Valinor",
}
# Noms des langues de l'aide (endonymes, traités comme noms propres) et nom de l'écriture latine.
ALLOWED |= {
    "Deutsch", "English", "al", "Arabiyya", "Hayeren", "Balgarski", "Zhongwen", "jiǎntǐ", "fántǐ",
    "Hangugeo", "Hrvatski", "Dansk", "Español", "Eesti", "Suomi", "Français", "Ellinika", "Hindi",
    "Magyar", "Bahasa", "Indonesia", "Gaeilge", "Íslenska", "Italiano", "Nihongo", "Latviešu",
    "Lietuvių", "Melayu", "Malti", "Nederlands", "Norsk", "Polski", "Português", "Română",
    "Slovenčina", "Slovenščina", "Svenska", "Tamil", "Čeština", "Thai", "Türkçe", "Ukrainska",
    "Tiếng", "Việt", "latin", "comicvine", "gamespot", "com", "xlsx", "Cymraeg", "tlhIngan", "Hol",
}

# Faux sens vérifiés de l'ancien lexique (section 1 du lexique), et mots d'autres langues.
FORBIDDEN = {
    "alda", "aldasse", "fana", "fanar", "nai", "russe", "nurta", "pata", "samna", "hyarya",
    "renta", "nutta", "nuttale", "oia", "lindele", "windo", "thala", "tereva", "mosse", "centuva",
    "cenil", "cenila", "avahaila", "antanor", "parme", "metanote", "parmavorn", "egor",
    "taur", "menu", "cilmer", "lambetengwe", "lambetengwi", "quettar",
}

# Mots anglais et français courants qui coïncident avec une forme quenya (`the`, `and` = forme
# d'`anda`) ou qui s'y ramènent en retirant une terminaison : toujours signalés.
FOREIGN = {
    "the", "and", "with", "from", "open", "save", "error", "series", "viewer", "start", "mouse",
    "image", "images", "page", "pages", "file", "files", "data", "load", "stop", "close", "cancel",
    "delete", "settings", "window", "library", "folder", "button", "click", "select", "copy",
    "paste", "undo", "redo", "help", "yes", "new", "edit", "view", "menu", "tool", "list", "name",
    "size", "text", "color", "light", "dark", "mode", "line", "left", "right", "all", "none",
    "less", "next", "previous", "first", "last", "apply", "reset", "rotate", "mirror",
    "split", "merge", "sort", "search", "find", "link", "address", "done", "warning", "not",
    "les", "des", "une", "pour", "avec", "dans", "sur", "par", "est", "sont", "fichier",
    "fichiers", "dossier", "erreur", "annuler", "ouvrir", "fermer", "enregistrer", "série",
    "serie", "lot", "lote", "mosaique", "mosaic", "qualite", "petition", "automata", "copia",
    "modulo", "panel",
}

SUFFIXES = sorted({
    # pluriels et cas nominaux
    "r", "i", "li", "o", "on", "ion", "ron", "va", "n", "in", "nna", "nnar", "llo", "llor",
    "sse", "ssen", "nen", "inen", "s", "t", "u", "nta", "lta",
    # possessifs et pronoms sujets
    "nya", "lya", "rya", "lma", "lva", "mma", "lda", "ntya", "lye", "lme", "lve", "nte", "lde",
    "nye", "mme", "s", "ryo", "lyo", "nyo",
    # verbes : aoriste, passé, futur, parfait, participes, gérondif, nom verbal
    "a", "e", "ne", "nne", "uva", "ie", "ina", "na", "la", "ila", "le", "ea", "ta", "ya", "ima",
    "ier",
    # dérivations
    "ince", "ndo", "ro", "mo", "nde", "ve", "wa", "ma", "ea", "ite",
}, key=len, reverse=True)


def fold(s):
    # Les états anciens (middle quenya) notent `k` et `q(e)` là où le quenya tardif écrit
    # `c` et `qu(e)` : `tek-` = `tec-`, `qenta` = `quenta`.
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower())
                if unicodedata.category(c) != "Mn")
    return s.replace("k", "c").replace("qu", "q")


FOREIGN = {fold(w) for w in FOREIGN}
FORBIDDEN = {fold(w) for w in FORBIDDEN}


index_path, vocab_path, block_path, report_path = sys.argv[1:5]

lines = open(index_path, encoding="utf-8").read().splitlines()
cols = lines[0].split("\t")
words, prefixes = set(), set()
for line in lines[1:]:
    r = dict(zip(cols, line.split("\t")))
    if r["lang"] not in ("q", "nq", "mq") or "déconseillé" in r["status"]:
        continue
    if r["speech"] in ("suf", "phoneme", "phrase", "text") or r["word"].startswith("-"):
        continue
    # Un préfixe (`at(a)-`, `en-`, `yu-`) n'est pas un mot autonome : il n'est accepté que comme
    # élément d'une forme déclarée dans vocab_qya.tsv.
    target = prefixes if r["speech"].split()[0] == "pref" else words
    for variant in [r["word"]] + [f.strip() for f in r["forms"].split(";")]:
        raw = fold(variant).strip("-’'")
        # Partie optionnelle entre parenthèses (`hísë(n)`, `parmalas(së)`) : les deux formes.
        for v in {re.sub(r"\(([^)]*)\)", r"\1", raw), re.sub(r"\([^)]*\)", "", raw)}:
            if v and " " not in v and "{" not in v:
                target.add(v)
prefixes -= words


def known(w, depth=3):
    if w in words:
        return True
    if depth == 0:
        return False
    for suf in SUFFIXES:
        # Racine d'au moins 3 lettres : les racines de 2 lettres (`er`, `op`, `an`) feraient
        # passer presque n'importe quel mot étranger.
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            base = w[: -len(suf)]
            for cand in (base, base + "a", base + "e"):
                if cand in words or known(cand, depth - 1):
                    return True
    return False


# vocab_qya.tsv : « forme <TAB> éléments Eldamo <TAB> note ». Une forme déclarée accepte ensuite
# les mêmes terminaisons qu'un mot de l'index.
problems = []
for n, line in enumerate(open(vocab_path, encoding="utf-8").read().splitlines(), 1):
    if not line.strip() or line.startswith("#"):
        continue
    parts = line.split("\t")
    form, lemmas = parts[0], parts[1].split()
    for lem in lemmas:
        f = fold(lem).strip("-")
        if not (f in words or f in prefixes or known(f)):
            problems.append(f"vocab ligne {n} : élément « {lem} » de « {form} » absent d'Eldamo")
    words.add(fold(form).strip("-"))


def flat(d, p=""):
    for k, v in d.items():
        q = f"{p}.{k}" if p else k
        if isinstance(v, dict):
            yield from flat(v, q)
        else:
            yield q, v


def forbidden(f):
    if f in FORBIDDEN:
        return True
    # Un mot attesté tel quel n'est pas une forme fléchie d'un mot interdit (`oialë` ≠ `oia` + `-lë`).
    if f in words:
        return False
    return any(f.endswith(suf) and f[: -len(suf)] in FORBIDDEN for suf in SUFFIXES)


data = json.load(open(block_path, encoding="utf-8"))
items = list(flat(data)) if any(isinstance(v, dict) for v in data.values()) else list(data.items())

STRIP = re.compile(r"\{[^}]*\}|<[^>]+>|&[a-z]+;|https?://\S+|\b[\w.-]+@[\w.-]+\b|\S+\.(?:json|txt|xml|exe|mvdb|cbz|ttf)\b")
TOKEN = re.compile(r"[^\W\d_]+")
for key, value in items:
    if key in ("app_title",):
        continue
    for tok in TOKEN.findall(STRIP.sub(" ", value)):
        if tok in ALLOWED or tok.isupper():
            continue
        f = fold(tok)
        if f in FOREIGN:
            problems.append(f"{key} : « {tok} » anglais ou français")
        elif forbidden(f):
            problems.append(f"{key} : « {tok} » interdit (faux sens)")
        elif not known(f):
            problems.append(f"{key} : « {tok} » inconnu")

with open(report_path, "w", encoding="utf-8") as out:
    out.write("\n".join(problems) + ("\n" if problems else ""))
print(len(problems))
