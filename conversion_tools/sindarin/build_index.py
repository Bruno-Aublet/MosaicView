"""Construit un index TSV du sindarin à partir des données Eldamo (lecture seule du XML).

Usage : python build_index.py <eldamo-data.xml> <sortie.tsv>

Source : eldamo-data.xml de Paul Strack (https://github.com/pfstrack/eldamo,
src/data/eldamo-data.xml), licence Creative Commons Attribution 4.0.

Langues retenues : Sindarin (s), Neo-Sindarin (ns, néologismes et adaptations),
Noldorin (n), Early Noldorin (en), Gnomish (g), North Sindarin (norths), Ilkorin (ilk).

Colonnes :
- word : forme (chiffre d'homonymie ¹²³ retiré, conservé dans `homonym`) ;
- lang : code Eldamo ;
- status : `attesté` (s), `néo` (ns), `ancien` (n/en/g/ilk/norths), suivi de
  `déconseillé` quand Eldamo recommande un autre mot (colonne `use_instead`) ;
- mark : marqueurs de fiabilité Eldamo (`!` néologisme, `^` adapté d'un état ancien,
  `#` attesté seulement fléchi ou en composé, `*` reconstruit, `?`/`‽` douteux,
  `†` archaïque/poétique, `-` forme rejetée) ;
- created : auteur d'un néologisme ;
- forms : formes attestées (fléchies, mutées, en composé) relevées dans les références.
"""
import re
import sys
import xml.etree.ElementTree as ET

LANGS = {"s", "ns", "n", "en", "g", "norths", "ilk"}
SUPERSCRIPT = re.compile(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]+$")

xml_path, out_path = sys.argv[1], sys.argv[2]
root = ET.parse(xml_path).getroot()

rows = []
for w in root.iter("word"):
    lang = w.get("l")
    if lang not in LANGS:
        continue
    v = w.get("v") or ""
    hom = SUPERSCRIPT.search(v)
    word = SUPERSCRIPT.sub("", v)
    gloss = w.get("ngloss") or w.get("gloss") or ""
    use_instead = sorted({d.get("v") for d in w.findall("deprecated") if d.get("v")})
    status = {"s": "attesté", "ns": "néo"}.get(lang, "ancien")
    if use_instead:
        status += " déconseillé"
    forms = []
    for ref in w.findall("ref"):
        rv = ref.get("v")
        if rv and rv != word and rv not in forms:
            forms.append(rv)
        for inf in ref.findall("inflect"):
            iv = inf.get("v")
            if iv and iv != word and iv not in forms:
                forms.append(iv)
    sources = sorted({(r.get("source") or "").split("/")[0] for r in w.findall("ref")} - {""})
    rows.append([
        word, hom.group(0) if hom else "", lang, w.get("speech") or "",
        re.sub(r"\s+", " ", gloss).strip(), status, w.get("mark") or "",
        " ; ".join(use_instead), w.get("created") or "", " ; ".join(forms[:30]),
        " ".join(sources),
    ])

cols = ("word", "homonym", "lang", "speech", "gloss", "status", "mark",
        "use_instead", "created", "forms", "sources")
with open(out_path, "w", encoding="utf-8", newline="\n") as f:
    f.write("\t".join(cols) + "\n")
    for r in rows:
        f.write("\t".join(c.replace("\t", " ") for c in r) + "\n")
print(len(rows))
