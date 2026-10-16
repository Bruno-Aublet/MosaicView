"""Contrôle structurel d'un bloc : clés attendues, placeholders, retours à la ligne,
guillemets droits (retirés en pIqaD), points de suspension finaux.

Usage : python check_struct.py <bloc.json> <prefixe> [prefixe...]
Code de sortie 1 si une anomalie bloquante est trouvée.
"""
import json
import re
import sys

block_path, *prefixes = sys.argv[1:]


def flat(d, p=""):
    for k, v in d.items():
        q = f"{p}.{k}" if p else k
        if isinstance(v, dict):
            yield from flat(v, q)
        else:
            yield q, v


fr = dict(flat(json.load(open("locales/fr.json", encoding="utf-8"))))
b = json.load(open(block_path, encoding="utf-8"))
want = {k for k in fr if any(k == p or k.startswith(p + ".") for p in prefixes)}
ph = lambda s: sorted(re.findall(r"\{[^}]+\}", s))
errors = []
if want - set(b):
    errors.append(f"manquantes : {sorted(want - set(b))}")
if set(b) - want:
    errors.append(f"en trop : {sorted(set(b) - want)}")
for k in set(b) & want:
    if ph(fr[k]) != ph(b[k]):
        errors.append(f"placeholders {k} : {ph(fr[k])} != {ph(b[k])}")
    if fr[k].count("\n") != b[k].count("\n"):
        errors.append(f"retours a la ligne {k}")
    if '"' in b[k]:
        errors.append(f'guillemet droit " dans {k}')
    if "<" in fr[k] and fr[k].count("<") != b[k].count("<"):
        errors.append(f"balises HTML {k}")
for e in errors:
    print("  ERREUR", e)
print(f"{len(b)} cles, {len(errors)} erreur(s)")
sys.exit(1 if errors else 0)
