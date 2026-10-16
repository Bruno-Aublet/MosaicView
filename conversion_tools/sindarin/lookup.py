"""Recherche dans l'index sindarin Eldamo.

Usage : python lookup.py <index.tsv> <sortie.txt> terme [terme...]
- terme anglais : cherché comme mot entier dans les gloses (`page`, `folder`) ;
- `s:mot` : forme sindarine exacte (accents ignorés), dans `word` et dans `forms`
  (formes fléchies ou mutées attestées).
Sortie UTF-8 dans un fichier (le terminal cp1252 n'affiche pas tout).
Les entrées « néo » et « attesté » non déconseillées sont listées en premier.
"""
import re
import sys
import unicodedata


def fold(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(c) != "Mn")


index_path, out_path, *terms = sys.argv[1:]
lines = open(index_path, encoding="utf-8").read().splitlines()
cols = lines[0].split("\t")
rows = [dict(zip(cols, l.split("\t"))) for l in lines[1:]]


def rank(r):
    return ("déconseillé" in r["status"], {"attesté": 0, "néo": 1}.get(r["status"].split()[0], 2))


with open(out_path, "w", encoding="utf-8") as out:
    for term in terms:
        out.write(f"### {term}\n")
        if term.startswith("s:"):
            key = fold(term[2:])
            hits = [r for r in rows if fold(r["word"]) == key
                    or key in (fold(f.strip()) for f in r["forms"].split(";"))]
        else:
            rx = re.compile(r"\b" + re.escape(term) + r"\b", re.I)
            hits = [r for r in rows if rx.search(r["gloss"])]
        for r in sorted(hits, key=rank)[:15]:
            extra = []
            if r["use_instead"]:
                extra.append(f"-> {r['use_instead']}")
            if r["created"]:
                extra.append(f"créé par {r['created']}")
            if r["forms"]:
                extra.append(f"formes: {r['forms'][:120]}")
            out.write(f"  {r['word']}{r['homonym']} [{r['lang']} {r['speech']}] "
                      f"{r['gloss']} | {r['status']} {r['mark']} | {' | '.join(extra)}\n")
print("ok")
