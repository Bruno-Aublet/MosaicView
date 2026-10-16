"""Applique un bloc de traductions {"chemin.de.clé": valeur} à locales/tlh.json.

Usage : python apply_block.py <tlh.json> <bloc.json|--dry-run>
Conserve l'ordre des clés, l'indentation (2) et les fins de ligne CRLF.
Refuse toute clé absente du fichier (pas de création silencieuse).
"""
import json
import sys

path, block_path = sys.argv[1], sys.argv[2]
raw = open(path, "rb").read()
data = json.loads(raw.decode("utf-8"))


def dump(d):
    return (json.dumps(d, ensure_ascii=False, indent=2).replace("\n", "\r\n") + "\r\n").encode("utf-8")


if block_path == "--dry-run":
    print("identique" if dump(data) == raw else "DIFFÉRENT")
    sys.exit(0)

block = json.load(open(block_path, encoding="utf-8"))
changed = 0
for dotted, value in block.items():
    node = data
    parts = dotted.split(".")
    for p in parts[:-1]:
        node = node[p]
    if parts[-1] not in node:
        sys.exit(f"Clé inconnue : {dotted}")
    if node[parts[-1]] != value:
        node[parts[-1]] = value
        changed += 1
open(path, "wb").write(dump(data))
json.loads(open(path, encoding="utf-8").read())
print(f"{changed} valeur(s) modifiée(s) sur {len(block)}")
