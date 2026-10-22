"""Contrôle lexical d'un texte klingon : chaque mot doit se décomposer en
préfixe? + racine(s) canonique(s) + suffixes canoniques (dictionnaire boQwI').

Usage : python -I check_tlh.py <index.tsv> <values.json> <out.txt>
  values.json : {"clé": "valeur klingon", ...}
"""
import json
import re
import sys
from functools import lru_cache

index_path, values_path, out_path = sys.argv[1:4]

rows = [l.rstrip("\n").split("\t") for l in open(index_path, encoding="utf-8")][1:]
roots, suffixes, prefixes = set(), set(), set()
for r in rows:
    name, pos = r[0], r[1]
    if pos.startswith("sen"):
        continue
    if name.startswith("-"):
        suffixes.add(name[1:])
    elif name.endswith("-"):
        prefixes.add(name[:-1])
    else:
        for w in name.split():
            roots.add(w)
prefixes.discard("0")

# Mots admis hors dictionnaire : noms propres, formats, sigles, emprunts.
ALLOWED = set("""
comicvine gamespot com Code nIpon neDerlan norvegh tamIl clipboard XXXXX Log pdftocbz cbrtocbz imgtocbz scan txt Wilhelm_scream en wikipedia org https wiki xxx euro 403 DPAPI MA15 R18 X18 j t b TrueType jfif pjpeg pjp epub bitmap RAM HDD Copyright v3 Zip MosaicView ComicVine ComicInfo CBZ CBR CB7 CBT PDF EPUB ZIP RAR TAR 7Z 7z JPEG JPG PNG GIF WEBP WebP AVIF BMP TIFF TIF ICO
API URL HTML XML JSON NFO SQLite Ctrl Alt Shift Tab Enter Esc ESC Del F5 F11 PayPal Windows Explorer UnRAR Pillow PyMuPDF APPDATA
Qt pIqaD Tengwar tengwar CSUR WIA DPI RGB RGBA CMYK OK Unicode mvdb GitHub Okrand KLI GNU GPL LGPL Igor Pavlov RARlab
Bruno Aublet Claude Anthropic AI Distillat Gemini Flash Wilhelm Wikipedia WAV Excel XLSX ISO GTIN Manga Webtoon
qolqoS Alcarin Sindarin Quenya Vibe Coding CDisplayEx YACReader MB Mo Ko KB px fps MP bit bits rarfile fitz pip install
ico cbz cbr cb7 cbt pdf nfo xml json txt mvdb pywin32 win32print PIL gamma Alpha Plus Minus D L LA P V C X Y Z A I T B G M MA PG R
Assets Releases v
""".split())


@lru_cache(maxsize=None)
def split_suffixes(s):
    if s == "":
        return True
    for i in range(1, len(s) + 1):
        if s[:i] in suffixes and split_suffixes(s[i:]):
            return True
    return False


@lru_cache(maxsize=None)
def stem_ok(s, depth=0):
    """s = racine (éventuellement composée de 2 racines) + suffixes."""
    for i in range(1, len(s) + 1):
        head = s[:i]
        if head in roots:
            rest = s[i:]
            if split_suffixes(rest):
                return True
            if False and stem_ok(rest, 1):
                return True
    return False


def word_core_ok(w):
    if w in ALLOWED or w in roots:
        return True
    if stem_ok(w):
        return True
    for p in prefixes:
        if w.startswith(p) and stem_ok(w[len(p):]):
            return True
    # Nombre suivi de suffixes klingons (256Daq, 16vo')
    m = re.match(r"^\d+(.+)$", w)
    if m and split_suffixes(m.group(1)):
        return True
    # Nom propre / sigle suivi de suffixes klingons (GitHubDaq, CBZmey)
    for a in ALLOWED:
        if w.startswith(a) and len(w) > len(a) and split_suffixes(w[len(a):]):
            return True
    return False


def word_ok(w):
    # L'apostrophe est une lettre klingonne ; on ne la retire que si le mot
    # nu échoue (guillemet simple de citation 'mot').
    if word_core_ok(w):
        return True
    for cand in (w.strip("'"), w.lstrip("'"), w.rstrip("'")):
        if cand and cand != w and word_core_ok(cand):
            return True
    return False


def words(text):
    text = re.sub(r"\{[^}]+\}|https?://\S+|<[^>]+>|\*\.\w+|[\w.-]+\.(?:py|json|txt|exe|ttf|ico|xml|mvdb|htm|cbz|nfo)\b", " ", text)
    for w in re.findall(r"[A-Za-z0-9']+", text):
        if w.strip("'") and not w.strip("'").isdigit():
            yield w


values = json.load(open(values_path, encoding="utf-8"))
bad = {}
for key, val in values.items():
    for w in words(val):
        if not word_ok(w):
            bad.setdefault(w, []).append(key)
with open(out_path, "w", encoding="utf-8") as out:
    out.write(f"{len(bad)} mot(s) non reconnu(s)\n")
    for w, ks in sorted(bad.items()):
        out.write(f"  {w} : {', '.join(ks[:6])}\n")
print(len(bad))
