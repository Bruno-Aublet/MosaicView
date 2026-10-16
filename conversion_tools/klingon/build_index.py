"""Construit un index TSV du dictionnaire boQwI' (lecture seule des XML)."""
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

data_dir, out_path = sys.argv[1], sys.argv[2]
cols = ("entry_name", "part_of_speech", "definition", "definition_fr", "search_tags", "notes", "source")
rows = []
for path in sorted(glob.glob(os.path.join(data_dir, "mem-*.xml"))):
    text = open(path, encoding="utf-8").read()
    # Fragments sans racine : on les enveloppe pour le parseur.
    root = ET.fromstring("<root>" + text + "</root>")
    for table in root.iter("table"):
        d = {c.get("name"): (c.text or "") for c in table.findall("column")}
        rows.append([re.sub(r"\s+", " ", d.get(c, "")).strip() for c in cols])
with open(out_path, "w", encoding="utf-8") as f:
    f.write("\t".join(cols) + "\n")
    for r in rows:
        f.write("\t".join(r) + "\n")
print(len(rows))
