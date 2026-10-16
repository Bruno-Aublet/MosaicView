"""Recherche dans l'index boQwI' : par sens anglais (défaut) ou par mot klingon (préfixe 'k:')."""
import re
import sys

index_path, out_path, *terms = sys.argv[1:]
rows = [l.rstrip("\n").split("\t") for l in open(index_path, encoding="utf-8")][1:]
with open(out_path, "w", encoding="utf-8") as out:
    for term in terms:
        out.write(f"### {term}\n")
        if term.startswith("k:"):
            word = term[2:]
            hits = [r for r in rows if r[0] == word]
        else:
            rx = re.compile(r"\b" + re.escape(term) + r"\b", re.I)
            hits = [r for r in rows if rx.search(r[2]) or rx.search(r[4])]
        for r in [h for h in hits if not h[1].startswith("sen")][:8]:
            name, pos, d_en, d_fr, tags, notes, src = (r + [""] * 7)[:7]
            out.write(f"  {name} [{pos}] {d_en} | fr: {d_fr} | src: {src[:60]}\n")
print("ok")
